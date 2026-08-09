/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * LAZ v3 and v4 layered item writers for the LAS 1.4 point types -- ported from
 * LASzip's laswriteitemcompressed_v3.cpp and _v4.cpp, and the exact inverse of
 * the readers in laz_readitem_v3.c.
 *
 * Layering inverts awkwardly. A reader is handed a chunk whose layer sizes are
 * already known, so it can slice the chunk up and give each layer its own
 * decoder. A writer does not learn a layer's size until the chunk is finished,
 * so each layer gets its own growable buffer and its own encoder, and the sizes
 * are emitted ahead of the concatenated layer bytes only once the chunk closes.
 * That is what the chunk_sizes/chunk_bytes pair in LazWriteItem is for, and why
 * every writer must emit its sizes before any writer emits its bytes.
 *
 * A layer that no point changed is dropped: its size goes out as zero and its
 * bytes do not go out at all, which is exactly what makes the reader's
 * `changed` false and stops it decoding the layer. So the encoder still runs
 * over every point of, say, the classification layer -- the decision to keep
 * the result is taken at the end of the chunk.
 *
 * The scanner-channel contexts work as they do on the read side: up to four
 * independent model sets per item, with POINT14 deciding which is active and
 * publishing it through the shared `context` parameter for every other item to
 * follow.
 *
 * v4 differs from v3 in two places, applied here behind the `v4` flag rather
 * than by duplicating the file:
 *   - POINT14 publishes `context` on every point, not only when the scanner
 *     channel changed;
 *   - the other items refresh their `last_item` pointer on any context switch,
 *     not only when switching to a never-used context.
 * The read side has a third fix, in RGBNIR14's NIR branch, with no counterpart
 * here: it corrects a stale read, and the writer never had the stale read.
 */
#include "laz_writeitem.h"

/* The item buffer is LASzip's combined LASpoint14; laz_types.h asserts the
 * write extent cannot reach the next item's slot. */
#define LASPOINT14_SIZE LAZ_POINT14_WRITE_EXTENT

/*
 * Field accessors. Everything the POINT14 writer looks at is read-only -- the
 * point being written is const, and last_item is refreshed by copying the whole
 * point -- so one const-correct set covers both, with gps_time_change the sole
 * exception because it is state the writer keeps rather than a point field.
 */
#define P14_X(p)  (*(const I32 *)((const U8 *)(p) + 0))
#define P14_Y(p)  (*(const I32 *)((const U8 *)(p) + 4))
#define P14_Z(p)  (*(const I32 *)((const U8 *)(p) + 8))
#define P14_INTENSITY(p) (*(const U16 *)((const U8 *)(p) + 12))

#define P14_SCAN_DIRECTION_FLAG(p) ((((const U8 *)(p))[14] >> 6) & 0x01)
#define P14_EDGE_OF_FLIGHT_LINE(p) ((((const U8 *)(p))[14] >> 7) & 0x01)

#define P14_USER_DATA(p)           (((const U8 *)(p))[17])
#define P14_POINT_SOURCE_ID(p)     (*(const U16 *)((const U8 *)(p) + 18))
#define P14_SCAN_ANGLE(p)          (*(const I16 *)((const U8 *)(p) + 20))

#define P14_SCANNER_CHANNEL(p)      ((((const U8 *)(p))[22] >> 2) & 0x03)
#define P14_CLASSIFICATION_FLAGS(p) ((((const U8 *)(p))[22] >> 4) & 0x0F)
#define P14_CLASSIFICATION(p)       (((const U8 *)(p))[23])
#define P14_RETURN_NUMBER(p)        (((const U8 *)(p))[24] & 0x0F)
#define P14_NUMBER_OF_RETURNS(p)    ((((const U8 *)(p))[24] >> 4) & 0x0F)

#define P14_GPS_TIME_CHANGE(p)     (*(const I32 *)((const U8 *)(p) + 28))
#define P14_SET_GPS_TIME_CHANGE(p, v) (*(I32 *)((U8 *)(p) + 28) = (v))
#define P14_GPS_TIME(p)            (*(const F64 *)((const U8 *)(p) + 32))
#define P14_GPS_TIME_I64(p)        (*(const I64 *)((const U8 *)(p) + 32))

#define LASZIP_GPSTIME_MULTI            500
#define LASZIP_GPSTIME_MULTI_MINUS      (-10)
#define LASZIP_GPSTIME_MULTI_CODE_FULL  (LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS + 1)
#define LASZIP_GPSTIME_MULTI_TOTAL      (LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS + 5)

/* ---------------------------------------------------------------- layers - */

/*
 * One encodable byte layer of a chunk: a buffer that grows as the chunk is
 * written, and an encoder that fills it. Both outlive the chunk and are
 * recycled, which is what LASzip's outstream->seek(0) does.
 *
 * `changed` says whether any point in this chunk needed the layer. It stays
 * false for a layer whose value never moved, and a false layer is dropped.
 */
typedef struct {
    LazOutStream *stream;   /* owned */
    LazEncoder enc;         /* owned */
    BOOL changed;
} WLayer;

static BOOL wlayer_create(WLayer *l)
{
    if (l->stream) return LAZ_TRUE;
    l->stream = laz_outstream_new_array();
    if (!l->stream) return LAZ_FALSE;
    if (!laz_encoder_setup(&l->enc)) {
        laz_outstream_destroy(l->stream);
        l->stream = NULL;
        return LAZ_FALSE;
    }
    return LAZ_TRUE;
}

static void wlayer_init(WLayer *l)
{
    laz_outstream_array_rewind(l->stream);
    laz_encoder_init(&l->enc, l->stream);
    l->changed = LAZ_FALSE;
}

static void wlayer_free(WLayer *l)
{
    laz_encoder_free(&l->enc);
    if (l->stream) laz_outstream_destroy(l->stream);
    l->stream = NULL;
}

static U32 wlayer_num_bytes(WLayer *l)
{
    I64 size;
    (void)laz_outstream_array_data(l->stream, &size);
    return (U32)size;
}

static void wlayer_put_size(WLayer *l, LazOutStream *out)
{
    laz_outstream_put32(out, l->changed ? wlayer_num_bytes(l) : 0);
}

static void wlayer_put_bytes(WLayer *l, LazOutStream *out)
{
    I64 size;
    const U8 *data;
    if (!l->changed) return;
    data = laz_outstream_array_data(l->stream, &size);
    laz_outstream_put_bytes(out, data, size);
}

/* Every layered writer reaches its chunk's output through the shared encoder,
 * which it never encodes with -- the mirror of the layered readers taking the
 * shared decoder only for its input stream. */
static LazOutStream *chunk_stream(LazWriteItem *self)
{
    return self->enc->stream;
}

/* ==================================================== POINT14 raw writer == */

/*
 * Gathers the LAS 1.4 fields of a decoded point back into the 30-byte record,
 * the exact inverse of laz_readitem_raw_point14. Lives here rather than with
 * the other raw writers because it is the one that has to understand the
 * split between the legacy and extended fields of a point.
 *
 * A point that never came from a 1.4 source has extended_point_type clear, and
 * then the extended fields are derived from the legacy ones instead.
 */
typedef struct {
    LazWriteItem base;
    U8 buffer[30];
} RawPoint14;

static BOOL raw_p14_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    RawPoint14 *w = (RawPoint14 *)self;
    U8 *b = w->buffer;
    const U8 *p = item;
    U8 classification, return_number, number_of_returns, scanner_channel;
    U8 classification_flags;
    I16 scan_angle;

    (void)context;
    memcpy(b + 0, p + 0, 14);           /* X, Y, Z, intensity */

    classification = (U8)(p[15] & 0x1F);        /* legacy classification */
    if (p[22] & 0x03) {                         /* extended_point_type */
        classification_flags = (U8)(((p[22] >> 4) & 0x08) | (p[15] >> 5));
        if (classification == 0) classification = p[23];
        scanner_channel = (U8)((p[22] >> 2) & 0x03);
        return_number = (U8)(p[24] & 0x0F);
        number_of_returns = (U8)((p[24] >> 4) & 0x0F);
        scan_angle = *(const I16 *)(p + 20);
    } else {
        classification_flags = (U8)(p[15] >> 5);
        scanner_channel = 0;
        return_number = (U8)(p[14] & 0x07);
        number_of_returns = (U8)((p[14] >> 3) & 0x07);
        scan_angle = I16_QUANTIZE(*(const I8 *)(p + 16) / 0.006f);
    }

    b[14] = (U8)(return_number | (number_of_returns << 4));
    b[15] = (U8)(classification_flags | (scanner_channel << 4) |
                 (P14_SCAN_DIRECTION_FLAG(p) << 6) |
                 (P14_EDGE_OF_FLIGHT_LINE(p) << 7));
    b[16] = classification;
    b[17] = p[17];                      /* user_data */
    memcpy(b + 18, &scan_angle, 2);
    memcpy(b + 20, p + 18, 2);          /* point_source_ID */
    memcpy(b + 22, p + 32, 8);          /* gps_time */

    laz_outstream_put_bytes(self->outstream, b, 30);
    return LAZ_TRUE;
}

static BOOL raw_p14_init_noop(LazWriteItem *self, const U8 *item, U32 *context)
{
    (void)self; (void)item; (void)context;
    return LAZ_TRUE;
}

LazWriteItem *laz_writeitem_raw_point14(LazOutStream *out)
{
    RawPoint14 *w = (RawPoint14 *)calloc(1, sizeof(RawPoint14));
    if (!w) return NULL;
    w->base.write = raw_p14_write;
    w->base.init = raw_p14_init_noop;
    w->base.outstream = out;
    return (LazWriteItem *)w;
}

/* ======================================================== POINT14 v3/v4 == */

typedef struct {
    BOOL unused;
    BOOL created;

    U8 last_item[LASPOINT14_SIZE];
    U16 last_intensity[8];
    LazStreamingMedian5 last_X_diff_median5[12];
    LazStreamingMedian5 last_Y_diff_median5[12];
    I32 last_Z[8];

    LazSymbolModel m_changed_values[8];
    LazSymbolModel m_scanner_channel;
    LazSymbolModel m_number_of_returns[16];
    U8 created_nor[16];
    LazSymbolModel m_return_number_gps_same;
    LazSymbolModel m_return_number[16];
    U8 created_rn[16];
    LazIntCompressor ic_dX, ic_dY, ic_Z;

    LazSymbolModel m_classification[64];
    U8 created_cls[64];
    LazSymbolModel m_flags[64];
    U8 created_flg[64];
    LazSymbolModel m_user_data[64];
    U8 created_usr[64];

    LazIntCompressor ic_intensity, ic_scan_angle, ic_point_source_ID;

    U32 last, next;
    I64 last_gpstime[4];
    I32 last_gpstime_diff[4];
    I32 multi_extreme_counter[4];
    LazSymbolModel m_gpstime_multi, m_gpstime_0diff;
    LazIntCompressor ic_gpstime;
} Point14Context;

typedef struct {
    LazWriteItem base;
    BOOL v4;
    WLayer channel_returns_XY, Z, classification, flags, intensity;
    WLayer scan_angle, user_data, point_source, gps_time;
    U32 current_context;
    Point14Context contexts[4];
} Point14v3;

static void p14_write_gps_time(Point14v3 *w, I64 gps_time);

static void p14_create_and_init(Point14v3 *w, U32 context, const U8 *item)
{
    Point14Context *c = &w->contexts[context];
    U32 i;

    if (!c->created) {
        for (i = 0; i < 8; i++)
            laz_symbol_model_setup(&c->m_changed_values[i], 128, LAZ_TRUE);
        laz_symbol_model_setup(&c->m_scanner_channel, 3, LAZ_TRUE);
        laz_bank_setup(c->m_number_of_returns, c->created_nor, 16, 16, LAZ_TRUE);
        laz_bank_setup(c->m_return_number, c->created_rn, 16, 16, LAZ_TRUE);
        laz_symbol_model_setup(&c->m_return_number_gps_same, 13, LAZ_TRUE);

        laz_ic_setup_enc(&c->ic_dX, &w->channel_returns_XY.enc, 32, 2, 8, 0);
        laz_ic_setup_enc(&c->ic_dY, &w->channel_returns_XY.enc, 32, 22, 8, 0);
        laz_ic_setup_enc(&c->ic_Z, &w->Z.enc, 32, 20, 8, 0);

        laz_bank_setup(c->m_classification, c->created_cls, 64, 256, LAZ_TRUE);
        laz_bank_setup(c->m_flags, c->created_flg, 64, 64, LAZ_TRUE);
        laz_bank_setup(c->m_user_data, c->created_usr, 64, 256, LAZ_TRUE);

        laz_ic_setup_enc(&c->ic_intensity, &w->intensity.enc, 16, 4, 8, 0);
        laz_ic_setup_enc(&c->ic_scan_angle, &w->scan_angle.enc, 16, 2, 8, 0);
        laz_ic_setup_enc(&c->ic_point_source_ID, &w->point_source.enc, 16, 1, 8, 0);

        laz_symbol_model_setup(&c->m_gpstime_multi, LASZIP_GPSTIME_MULTI_TOTAL, LAZ_TRUE);
        laz_symbol_model_setup(&c->m_gpstime_0diff, 5, LAZ_TRUE);
        laz_ic_setup_enc(&c->ic_gpstime, &w->gps_time.enc, 32, 9, 8, 0);

        c->created = LAZ_TRUE;
    }

    /* channel_returns_XY layer */
    for (i = 0; i < 8; i++) laz_symbol_model_init(&c->m_changed_values[i], NULL);
    laz_symbol_model_init(&c->m_scanner_channel, NULL);
    laz_bank_reinit(c->m_number_of_returns, c->created_nor, 16);
    laz_bank_reinit(c->m_return_number, c->created_rn, 16);
    laz_symbol_model_init(&c->m_return_number_gps_same, NULL);
    laz_ic_init_compressor(&c->ic_dX);
    laz_ic_init_compressor(&c->ic_dY);
    for (i = 0; i < 12; i++) {
        laz_median5_init(&c->last_X_diff_median5[i]);
        laz_median5_init(&c->last_Y_diff_median5[i]);
    }

    /* Z layer */
    laz_ic_init_compressor(&c->ic_Z);
    for (i = 0; i < 8; i++) c->last_Z[i] = P14_Z(item);

    /* classification / flags / user_data layers */
    laz_bank_reinit(c->m_classification, c->created_cls, 64);
    laz_bank_reinit(c->m_flags, c->created_flg, 64);
    laz_bank_reinit(c->m_user_data, c->created_usr, 64);

    /* intensity layer */
    laz_ic_init_compressor(&c->ic_intensity);
    for (i = 0; i < 8; i++) c->last_intensity[i] = P14_INTENSITY(item);

    laz_ic_init_compressor(&c->ic_scan_angle);
    laz_ic_init_compressor(&c->ic_point_source_ID);

    /* gps_time layer */
    laz_symbol_model_init(&c->m_gpstime_multi, NULL);
    laz_symbol_model_init(&c->m_gpstime_0diff, NULL);
    laz_ic_init_compressor(&c->ic_gpstime);
    c->last = 0;
    c->next = 0;
    memset(c->last_gpstime_diff, 0, sizeof(c->last_gpstime_diff));
    memset(c->multi_extreme_counter, 0, sizeof(c->multi_extreme_counter));
    c->last_gpstime[0] = P14_GPS_TIME_I64(item);
    c->last_gpstime[1] = c->last_gpstime[2] = c->last_gpstime[3] = 0;

    memcpy(c->last_item, item, LASPOINT14_SIZE);
    P14_SET_GPS_TIME_CHANGE(c->last_item, LAZ_FALSE);

    c->unused = LAZ_FALSE;
}

static BOOL p14_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Point14v3 *w = (Point14v3 *)self;
    U32 c;

    if (!wlayer_create(&w->channel_returns_XY) || !wlayer_create(&w->Z) ||
        !wlayer_create(&w->classification) || !wlayer_create(&w->flags) ||
        !wlayer_create(&w->intensity) || !wlayer_create(&w->scan_angle) ||
        !wlayer_create(&w->user_data) || !wlayer_create(&w->point_source) ||
        !wlayer_create(&w->gps_time))
        return LAZ_FALSE;

    wlayer_init(&w->channel_returns_XY);
    wlayer_init(&w->Z);
    wlayer_init(&w->classification);
    wlayer_init(&w->flags);
    wlayer_init(&w->intensity);
    wlayer_init(&w->scan_angle);
    wlayer_init(&w->user_data);
    wlayer_init(&w->point_source);
    wlayer_init(&w->gps_time);

    /* these two carry the context switches and the coordinates, so they are
     * emitted whether or not anything in them moved */
    w->channel_returns_XY.changed = LAZ_TRUE;
    w->Z.changed = LAZ_TRUE;

    for (c = 0; c < 4; c++) w->contexts[c].unused = LAZ_TRUE;

    w->current_context = P14_SCANNER_CHANNEL(item);
    *context = w->current_context;   /* POINT14 sets the context for all items */

    p14_create_and_init(w, w->current_context, item);
    return LAZ_TRUE;
}

static BOOL p14_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Point14v3 *w = (Point14v3 *)self;
    Point14Context *cx = &w->contexts[w->current_context];
    const U8 *last_item = cx->last_item;
    I32 lpr, changed_values, cpr;
    U32 scanner_channel;
    BOOL point_source_change, gps_time_change, scan_angle_change;
    U32 last_n, last_r, n, rn, m, l, k_bits;
    I32 median, diff;

    /* single (3) / first (1) / last (2) / intermediate (0) from the last return,
     * plus whether the GPS time changed on that return */
    lpr = (P14_RETURN_NUMBER(last_item) == 1 ? 1 : 0);
    lpr += (P14_RETURN_NUMBER(last_item) >= P14_NUMBER_OF_RETURNS(last_item) ? 2 : 0);
    lpr += (P14_GPS_TIME_CHANGE(last_item) ? 4 : 0);

    /* a switch to a context that already exists predicts against that
     * context's last point, so the comparisons below have to see it */
    scanner_channel = P14_SCANNER_CHANNEL(item);
    if (scanner_channel != w->current_context &&
        !w->contexts[scanner_channel].unused) {
        last_item = w->contexts[scanner_channel].last_item;
    }

    point_source_change = (P14_POINT_SOURCE_ID(item) != P14_POINT_SOURCE_ID(last_item));
    gps_time_change = (P14_GPS_TIME(item) != P14_GPS_TIME(last_item));
    scan_angle_change = (P14_SCAN_ANGLE(item) != P14_SCAN_ANGLE(last_item));

    last_n = P14_NUMBER_OF_RETURNS(last_item);
    last_r = P14_RETURN_NUMBER(last_item);
    n = P14_NUMBER_OF_RETURNS(item);
    rn = P14_RETURN_NUMBER(item);

    changed_values = ((scanner_channel != w->current_context) << 6) |
                     (point_source_change << 5) |
                     (gps_time_change << 4) |
                     (scan_angle_change << 3) |
                     ((n != last_n) << 2);

    /* return number: same (0) / plus one mod 16 (1) / minus one mod 16 (2) /
     * some other difference (3) */
    if (rn != last_r) {
        if (rn == ((last_r + 1) % 16)) changed_values |= 1;
        else if (rn == ((last_r + 15) % 16)) changed_values |= 2;
        else changed_values |= 3;
    }

    laz_encode_symbol(&w->channel_returns_XY.enc,
                      &cx->m_changed_values[lpr], (U32)changed_values);

    if (changed_values & (1 << 6)) {        /* scanner channel changed */
        I32 sym = (I32)scanner_channel - (I32)w->current_context;
        laz_encode_symbol(&w->channel_returns_XY.enc, &cx->m_scanner_channel,
                          (U32)((sym > 0 ? sym : sym + 4) - 1));
        if (w->contexts[scanner_channel].unused) {
            p14_create_and_init(w, scanner_channel, cx->last_item);
            last_item = w->contexts[scanner_channel].last_item;
        }
        w->current_context = scanner_channel;
        if (!w->v4) *context = w->current_context;
        cx = &w->contexts[w->current_context];
    }
    /* v4 publishes the context on every point, not only when it changed */
    if (w->v4) *context = w->current_context;

    if (changed_values & (1 << 2)) {
        laz_encode_symbol(&w->channel_returns_XY.enc,
                          laz_bank_get(cx->m_number_of_returns, cx->created_nor, last_n),
                          n);
    }

    if ((changed_values & 3) == 3) {
        if (gps_time_change) {
            laz_encode_symbol(&w->channel_returns_XY.enc,
                              laz_bank_get(cx->m_return_number, cx->created_rn, last_r),
                              rn);
        } else {
            I32 sym = (I32)rn - (I32)last_r;
            laz_encode_symbol(&w->channel_returns_XY.enc,
                              &cx->m_return_number_gps_same,
                              (U32)((sym > 1 ? sym : sym + 16) - 2));
        }
    }

    m = laz_number_return_map_6ctx[n][rn];
    l = laz_number_return_level_8ctx[n][rn];

    cpr = (rn == 1 ? 2 : 0);            /* first? */
    cpr += (rn >= n ? 1 : 0);           /* last? */

    /* X */
    median = laz_median5_get(&cx->last_X_diff_median5[(m << 1) | gps_time_change]);
    diff = P14_X(item) - P14_X(last_item);
    laz_ic_compress(&cx->ic_dX, median, diff, (n == 1));
    laz_median5_add(&cx->last_X_diff_median5[(m << 1) | gps_time_change], diff);

    /* Y */
    k_bits = cx->ic_dX.k;
    median = laz_median5_get(&cx->last_Y_diff_median5[(m << 1) | gps_time_change]);
    diff = P14_Y(item) - P14_Y(last_item);
    laz_ic_compress(&cx->ic_dY, median, diff,
                    (n == 1) + (k_bits < 20 ? U32_ZERO_BIT_0(k_bits) : 20));
    laz_median5_add(&cx->last_Y_diff_median5[(m << 1) | gps_time_change], diff);

    /* Z */
    k_bits = (cx->ic_dX.k + cx->ic_dY.k) / 2;
    laz_ic_compress(&cx->ic_Z, cx->last_Z[l], P14_Z(item),
                    (n == 1) + (k_bits < 18 ? U32_ZERO_BIT_0(k_bits) : 18));
    cx->last_Z[l] = P14_Z(item);

    {
        U32 last_classification = P14_CLASSIFICATION(last_item);
        U32 classification = P14_CLASSIFICATION(item);
        U32 ccc = ((last_classification & 0x1F) << 1) + (cpr == 3 ? 1 : 0);

        if (classification != last_classification) w->classification.changed = LAZ_TRUE;
        laz_encode_symbol(&w->classification.enc,
                          laz_bank_get(cx->m_classification, cx->created_cls, ccc),
                          classification);
    }

    {
        U32 last_flags = (U32)((P14_EDGE_OF_FLIGHT_LINE(last_item) << 5) |
                               (P14_SCAN_DIRECTION_FLAG(last_item) << 4) |
                               P14_CLASSIFICATION_FLAGS(last_item));
        U32 flags = (U32)((P14_EDGE_OF_FLIGHT_LINE(item) << 5) |
                          (P14_SCAN_DIRECTION_FLAG(item) << 4) |
                          P14_CLASSIFICATION_FLAGS(item));

        if (flags != last_flags) w->flags.changed = LAZ_TRUE;
        laz_encode_symbol(&w->flags.enc,
                          laz_bank_get(cx->m_flags, cx->created_flg, last_flags),
                          flags);
    }

    if (P14_INTENSITY(item) != P14_INTENSITY(last_item)) w->intensity.changed = LAZ_TRUE;
    laz_ic_compress(&cx->ic_intensity,
                    cx->last_intensity[(cpr << 1) | gps_time_change],
                    P14_INTENSITY(item), (U32)cpr);
    cx->last_intensity[(cpr << 1) | gps_time_change] = P14_INTENSITY(item);

    if (scan_angle_change) {
        w->scan_angle.changed = LAZ_TRUE;
        laz_ic_compress(&cx->ic_scan_angle, P14_SCAN_ANGLE(last_item),
                        P14_SCAN_ANGLE(item), (U32)gps_time_change);
    }

    {
        U32 idx = P14_USER_DATA(last_item) / 4;
        if (P14_USER_DATA(item) != P14_USER_DATA(last_item))
            w->user_data.changed = LAZ_TRUE;
        laz_encode_symbol(&w->user_data.enc,
                          laz_bank_get(cx->m_user_data, cx->created_usr, idx),
                          P14_USER_DATA(item));
    }

    if (point_source_change) {
        w->point_source.changed = LAZ_TRUE;
        laz_ic_compress(&cx->ic_point_source_ID, P14_POINT_SOURCE_ID(last_item),
                        P14_POINT_SOURCE_ID(item), 0);
    }

    if (gps_time_change) {
        w->gps_time.changed = LAZ_TRUE;
        p14_write_gps_time(w, P14_GPS_TIME_I64(item));
    }

    memcpy(cx->last_item, item, LASPOINT14_SIZE);
    P14_SET_GPS_TIME_CHANGE(cx->last_item, gps_time_change);
    return LAZ_TRUE;
}

/*
 * Looks for a stored sequence whose last time is within 32 bits of this one.
 * Returns the offset from `last` (1..3), or 0 if none qualifies -- as in the
 * GPSTIME11 v2 writer, four interleaved sequences keep a file that alternates
 * between two sensors in the cheap small-difference path.
 */
static U32 p14_find_other_sequence(const Point14Context *c, I64 gps_time)
{
    U32 i;
    for (i = 1; i < 4; i++) {
        I64 diff64 = gps_time - c->last_gpstime[(c->last + i) & 3];
        if (diff64 == (I64)(I32)diff64) return i;
    }
    return 0;
}

/* Starts a fresh sequence, coding the time in full. */
static void p14_start_new_sequence(Point14Context *c, LazEncoder *enc, I64 gps_time)
{
    laz_ic_compress(&c->ic_gpstime, (I32)((U64)c->last_gpstime[c->last] >> 32),
                    (I32)((U64)gps_time >> 32), 8);
    laz_write_int(enc, (U32)(U64)gps_time);
    c->next = (c->next + 1) & 3;
    c->last = c->next;
    c->last_gpstime_diff[c->last] = 0;
    c->multi_extreme_counter[c->last] = 0;
}

static void p14_write_gps_time(Point14v3 *w, I64 gps_time)
{
    Point14Context *c = &w->contexts[w->current_context];
    LazEncoder *enc = &w->gps_time.enc;
    I64 diff64 = gps_time - c->last_gpstime[c->last];
    I32 diff32 = (I32)diff64;
    U32 other;

    if (c->last_gpstime_diff[c->last] == 0) {   /* last integer difference was zero */
        if (diff64 == (I64)diff32) {            /* difference fits in 32 bits */
            laz_encode_symbol(enc, &c->m_gpstime_0diff, 0);
            laz_ic_compress(&c->ic_gpstime, 0, diff32, 0);
            c->last_gpstime_diff[c->last] = diff32;
            c->multi_extreme_counter[c->last] = 0;
        } else {                                /* difference is huge */
            other = p14_find_other_sequence(c, gps_time);
            if (other) {                        /* it belongs to another sequence */
                laz_encode_symbol(enc, &c->m_gpstime_0diff, other + 1);
                c->last = (c->last + other) & 3;
                p14_write_gps_time(w, gps_time);
                return;
            }
            laz_encode_symbol(enc, &c->m_gpstime_0diff, 1);
            p14_start_new_sequence(c, enc, gps_time);
        }
        c->last_gpstime[c->last] = gps_time;
        return;
    }

    /* the last integer difference was *not* zero */
    if (diff64 == (I64)diff32) {
        /* how many times the last difference this one is */
        I32 last_diff = c->last_gpstime_diff[c->last];
        I32 multi = I32_QUANTIZE((F32)diff32 / (F32)last_diff);

        if (multi == 1) {
            /* the case we expect most often, for regularly spaced pulses */
            laz_encode_symbol(enc, &c->m_gpstime_multi, 1);
            laz_ic_compress(&c->ic_gpstime, last_diff, diff32, 1);
            c->multi_extreme_counter[c->last] = 0;
        } else if (multi > 0) {
            if (multi < LASZIP_GPSTIME_MULTI) {     /* coded directly */
                laz_encode_symbol(enc, &c->m_gpstime_multi, (U32)multi);
                laz_ic_compress(&c->ic_gpstime, multi * last_diff, diff32,
                                (multi < 10) ? 2 : 3);
            } else {
                laz_encode_symbol(enc, &c->m_gpstime_multi, LASZIP_GPSTIME_MULTI);
                laz_ic_compress(&c->ic_gpstime, LASZIP_GPSTIME_MULTI * last_diff, diff32, 4);
                c->multi_extreme_counter[c->last]++;
                if (c->multi_extreme_counter[c->last] > 3) {
                    c->last_gpstime_diff[c->last] = diff32;
                    c->multi_extreme_counter[c->last] = 0;
                }
            }
        } else if (multi < 0) {
            if (multi > LASZIP_GPSTIME_MULTI_MINUS) {   /* coded directly */
                laz_encode_symbol(enc, &c->m_gpstime_multi,
                                  (U32)(LASZIP_GPSTIME_MULTI - multi));
                laz_ic_compress(&c->ic_gpstime, multi * last_diff, diff32, 5);
            } else {
                laz_encode_symbol(enc, &c->m_gpstime_multi,
                                  LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS);
                laz_ic_compress(&c->ic_gpstime,
                                LASZIP_GPSTIME_MULTI_MINUS * last_diff, diff32, 6);
                c->multi_extreme_counter[c->last]++;
                if (c->multi_extreme_counter[c->last] > 3) {
                    c->last_gpstime_diff[c->last] = diff32;
                    c->multi_extreme_counter[c->last] = 0;
                }
            }
        } else {
            laz_encode_symbol(enc, &c->m_gpstime_multi, 0);
            laz_ic_compress(&c->ic_gpstime, 0, diff32, 7);
            c->multi_extreme_counter[c->last]++;
            if (c->multi_extreme_counter[c->last] > 3) {
                c->last_gpstime_diff[c->last] = diff32;
                c->multi_extreme_counter[c->last] = 0;
            }
        }
    } else {                                    /* difference is huge */
        other = p14_find_other_sequence(c, gps_time);
        if (other) {                            /* it belongs to another sequence */
            laz_encode_symbol(enc, &c->m_gpstime_multi,
                              (U32)(LASZIP_GPSTIME_MULTI_CODE_FULL + (I32)other));
            c->last = (c->last + other) & 3;
            p14_write_gps_time(w, gps_time);
            return;
        }
        laz_encode_symbol(enc, &c->m_gpstime_multi, LASZIP_GPSTIME_MULTI_CODE_FULL);
        p14_start_new_sequence(c, enc, gps_time);
    }

    c->last_gpstime[c->last] = gps_time;
}

static BOOL p14_chunk_sizes(LazWriteItem *self)
{
    Point14v3 *w = (Point14v3 *)self;
    LazOutStream *out = chunk_stream(self);

    laz_encoder_done(&w->channel_returns_XY.enc);
    laz_encoder_done(&w->Z.enc);
    if (w->classification.changed) laz_encoder_done(&w->classification.enc);
    if (w->flags.changed) laz_encoder_done(&w->flags.enc);
    if (w->intensity.changed) laz_encoder_done(&w->intensity.enc);
    if (w->scan_angle.changed) laz_encoder_done(&w->scan_angle.enc);
    if (w->user_data.changed) laz_encoder_done(&w->user_data.enc);
    if (w->point_source.changed) laz_encoder_done(&w->point_source.enc);
    if (w->gps_time.changed) laz_encoder_done(&w->gps_time.enc);

    wlayer_put_size(&w->channel_returns_XY, out);
    wlayer_put_size(&w->Z, out);
    wlayer_put_size(&w->classification, out);
    wlayer_put_size(&w->flags, out);
    wlayer_put_size(&w->intensity, out);
    wlayer_put_size(&w->scan_angle, out);
    wlayer_put_size(&w->user_data, out);
    wlayer_put_size(&w->point_source, out);
    wlayer_put_size(&w->gps_time, out);
    return LAZ_TRUE;
}

static BOOL p14_chunk_bytes(LazWriteItem *self)
{
    Point14v3 *w = (Point14v3 *)self;
    LazOutStream *out = chunk_stream(self);

    wlayer_put_bytes(&w->channel_returns_XY, out);
    wlayer_put_bytes(&w->Z, out);
    wlayer_put_bytes(&w->classification, out);
    wlayer_put_bytes(&w->flags, out);
    wlayer_put_bytes(&w->intensity, out);
    wlayer_put_bytes(&w->scan_angle, out);
    wlayer_put_bytes(&w->user_data, out);
    wlayer_put_bytes(&w->point_source, out);
    wlayer_put_bytes(&w->gps_time, out);
    return LAZ_TRUE;
}

static void p14_destroy(LazWriteItem *self)
{
    Point14v3 *w = (Point14v3 *)self;
    U32 i, ci;
    for (ci = 0; ci < 4; ci++) {
        Point14Context *c = &w->contexts[ci];
        if (!c->created) continue;
        for (i = 0; i < 8; i++) laz_symbol_model_free(&c->m_changed_values[i]);
        laz_symbol_model_free(&c->m_scanner_channel);
        laz_bank_free(c->m_number_of_returns, 16);
        laz_bank_free(c->m_return_number, 16);
        laz_symbol_model_free(&c->m_return_number_gps_same);
        laz_ic_free(&c->ic_dX);
        laz_ic_free(&c->ic_dY);
        laz_ic_free(&c->ic_Z);
        laz_bank_free(c->m_classification, 64);
        laz_bank_free(c->m_flags, 64);
        laz_bank_free(c->m_user_data, 64);
        laz_ic_free(&c->ic_intensity);
        laz_ic_free(&c->ic_scan_angle);
        laz_ic_free(&c->ic_point_source_ID);
        laz_symbol_model_free(&c->m_gpstime_multi);
        laz_symbol_model_free(&c->m_gpstime_0diff);
        laz_ic_free(&c->ic_gpstime);
    }
    wlayer_free(&w->channel_returns_XY);
    wlayer_free(&w->Z);
    wlayer_free(&w->classification);
    wlayer_free(&w->flags);
    wlayer_free(&w->intensity);
    wlayer_free(&w->scan_angle);
    wlayer_free(&w->user_data);
    wlayer_free(&w->point_source);
    wlayer_free(&w->gps_time);
}

static LazWriteItem *point14_new(LazEncoder *enc, BOOL v4)
{
    Point14v3 *w = (Point14v3 *)calloc(1, sizeof(Point14v3));
    if (!w) return NULL;
    w->base.write = p14_write;
    w->base.init = p14_init;
    w->base.chunk_sizes = p14_chunk_sizes;
    w->base.chunk_bytes = p14_chunk_bytes;
    w->base.destroy = p14_destroy;
    w->base.enc = enc;
    w->v4 = v4;
    return (LazWriteItem *)w;
}

LazWriteItem *laz_writeitem_v3_point14(LazEncoder *enc) { return point14_new(enc, LAZ_FALSE); }
LazWriteItem *laz_writeitem_v4_point14(LazEncoder *enc) { return point14_new(enc, LAZ_TRUE); }

/* ========================================================== RGB14 v3/v4 == */

/*
 * Shared RGB body: encodes three 16-bit channels against last[], and returns
 * the byte-used symbol so the caller can tell whether anything moved. The
 * inverse of rgb_decode in laz_readitem_v3.c, and used by both RGB14 and the
 * RGB half of RGBNIR14, which differ only in which models they draw from.
 */
static U32 rgb_encode(LazEncoder *enc, LazSymbolModel *m_byte_used,
                      LazSymbolModel *m_rgb_diff, const U16 *cur, const U16 *last)
{
    I32 diff_l = 0, diff_h = 0, corr;
    U32 sym;

    sym  = (U32)((last[0] & 0x00FF) != (cur[0] & 0x00FF)) << 0;
    sym |= (U32)((last[0] & 0xFF00) != (cur[0] & 0xFF00)) << 1;
    sym |= (U32)((last[1] & 0x00FF) != (cur[1] & 0x00FF)) << 2;
    sym |= (U32)((last[1] & 0xFF00) != (cur[1] & 0xFF00)) << 3;
    sym |= (U32)((last[2] & 0x00FF) != (cur[2] & 0x00FF)) << 4;
    sym |= (U32)((last[2] & 0xFF00) != (cur[2] & 0xFF00)) << 5;
    /* bit 6 says whether green and blue differ from red at all */
    sym |= (U32)(((cur[0] & 0x00FF) != (cur[1] & 0x00FF)) ||
                 ((cur[0] & 0x00FF) != (cur[2] & 0x00FF)) ||
                 ((cur[0] & 0xFF00) != (cur[1] & 0xFF00)) ||
                 ((cur[0] & 0xFF00) != (cur[2] & 0xFF00))) << 6;

    laz_encode_symbol(enc, m_byte_used, sym);

    if (sym & (1 << 0)) {
        diff_l = (I32)(cur[0] & 255) - (I32)(last[0] & 255);
        laz_encode_symbol(enc, &m_rgb_diff[0], (U32)(U8)U8_FOLD(diff_l));
    }
    if (sym & (1 << 1)) {
        diff_h = (I32)(cur[0] >> 8) - (I32)(last[0] >> 8);
        laz_encode_symbol(enc, &m_rgb_diff[1], (U32)(U8)U8_FOLD(diff_h));
    }
    if (sym & (1 << 6)) {
        if (sym & (1 << 2)) {
            corr = (I32)(cur[1] & 255) - (I32)U8_CLAMP(diff_l + (I32)(last[1] & 255));
            laz_encode_symbol(enc, &m_rgb_diff[2], (U32)(U8)U8_FOLD(corr));
        }
        if (sym & (1 << 4)) {
            diff_l = (diff_l + (I32)(cur[1] & 255) - (I32)(last[1] & 255)) / 2;
            corr = (I32)(cur[2] & 255) - (I32)U8_CLAMP(diff_l + (I32)(last[2] & 255));
            laz_encode_symbol(enc, &m_rgb_diff[4], (U32)(U8)U8_FOLD(corr));
        }
        if (sym & (1 << 3)) {
            corr = (I32)(cur[1] >> 8) - (I32)U8_CLAMP(diff_h + (I32)(last[1] >> 8));
            laz_encode_symbol(enc, &m_rgb_diff[3], (U32)(U8)U8_FOLD(corr));
        }
        if (sym & (1 << 5)) {
            diff_h = (diff_h + (I32)(cur[1] >> 8) - (I32)(last[1] >> 8)) / 2;
            corr = (I32)(cur[2] >> 8) - (I32)U8_CLAMP(diff_h + (I32)(last[2] >> 8));
            laz_encode_symbol(enc, &m_rgb_diff[5], (U32)(U8)U8_FOLD(corr));
        }
    }
    return sym;
}

typedef struct {
    BOOL unused;
    BOOL created;
    U16 last_item[3];
    LazSymbolModel m_byte_used;
    LazSymbolModel m_rgb_diff[6];
} Rgb14Context;

typedef struct {
    LazWriteItem base;
    BOOL v4;
    WLayer rgb;
    U32 current_context;
    Rgb14Context contexts[4];
} Rgb14v3;

static void rgb14_create_and_init(Rgb14v3 *w, U32 context, const U8 *item)
{
    Rgb14Context *c = &w->contexts[context];
    U32 i;
    if (!c->created) {
        laz_symbol_model_setup(&c->m_byte_used, 128, LAZ_TRUE);
        for (i = 0; i < 6; i++) laz_symbol_model_setup(&c->m_rgb_diff[i], 256, LAZ_TRUE);
        c->created = LAZ_TRUE;
    }
    laz_symbol_model_init(&c->m_byte_used, NULL);
    for (i = 0; i < 6; i++) laz_symbol_model_init(&c->m_rgb_diff[i], NULL);
    memcpy(c->last_item, item, 6);
    c->unused = LAZ_FALSE;
}

static BOOL rgb14_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Rgb14v3 *w = (Rgb14v3 *)self;
    U32 c;

    if (!wlayer_create(&w->rgb)) return LAZ_FALSE;
    wlayer_init(&w->rgb);

    for (c = 0; c < 4; c++) w->contexts[c].unused = LAZ_TRUE;
    w->current_context = *context;      /* set by the POINT14 writer */
    rgb14_create_and_init(w, w->current_context, item);
    return LAZ_TRUE;
}

static BOOL rgb14_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Rgb14v3 *w = (Rgb14v3 *)self;
    U16 *last_item = w->contexts[w->current_context].last_item;
    Rgb14Context *c;

    if (w->current_context != *context) {
        w->current_context = *context;
        if (w->contexts[w->current_context].unused) {
            rgb14_create_and_init(w, w->current_context, (const U8 *)last_item);
            if (!w->v4) last_item = w->contexts[w->current_context].last_item;
        }
        /* v4 refreshes last_item for any switch, v3 only for an unused context */
        if (w->v4) last_item = w->contexts[w->current_context].last_item;
    }
    c = &w->contexts[w->current_context];

    if (rgb_encode(&w->rgb.enc, &c->m_byte_used, c->m_rgb_diff,
                   (const U16 *)item, last_item))
        w->rgb.changed = LAZ_TRUE;

    memcpy(last_item, item, 6);
    return LAZ_TRUE;
}

static BOOL rgb14_chunk_sizes(LazWriteItem *self)
{
    Rgb14v3 *w = (Rgb14v3 *)self;
    laz_encoder_done(&w->rgb.enc);
    wlayer_put_size(&w->rgb, chunk_stream(self));
    return LAZ_TRUE;
}

static BOOL rgb14_chunk_bytes(LazWriteItem *self)
{
    wlayer_put_bytes(&((Rgb14v3 *)self)->rgb, chunk_stream(self));
    return LAZ_TRUE;
}

static void rgb14_destroy(LazWriteItem *self)
{
    Rgb14v3 *w = (Rgb14v3 *)self;
    U32 ci, i;
    for (ci = 0; ci < 4; ci++) {
        if (!w->contexts[ci].created) continue;
        laz_symbol_model_free(&w->contexts[ci].m_byte_used);
        for (i = 0; i < 6; i++) laz_symbol_model_free(&w->contexts[ci].m_rgb_diff[i]);
    }
    wlayer_free(&w->rgb);
}

static LazWriteItem *rgb14_new(LazEncoder *enc, BOOL v4)
{
    Rgb14v3 *w = (Rgb14v3 *)calloc(1, sizeof(Rgb14v3));
    if (!w) return NULL;
    w->base.write = rgb14_write;
    w->base.init = rgb14_init;
    w->base.chunk_sizes = rgb14_chunk_sizes;
    w->base.chunk_bytes = rgb14_chunk_bytes;
    w->base.destroy = rgb14_destroy;
    w->base.enc = enc;
    w->v4 = v4;
    return (LazWriteItem *)w;
}

LazWriteItem *laz_writeitem_v3_rgb14(LazEncoder *enc) { return rgb14_new(enc, LAZ_FALSE); }
LazWriteItem *laz_writeitem_v4_rgb14(LazEncoder *enc) { return rgb14_new(enc, LAZ_TRUE); }

/* ======================================================= RGBNIR14 v3/v4 == */

typedef struct {
    BOOL unused;
    BOOL created;
    U16 last_item[4];
    LazSymbolModel m_rgb_bytes_used;
    LazSymbolModel m_rgb_diff[6];
    LazSymbolModel m_nir_bytes_used;
    LazSymbolModel m_nir_diff[2];
} RgbNir14Context;

typedef struct {
    LazWriteItem base;
    BOOL v4;
    WLayer rgb, nir;
    U32 current_context;
    RgbNir14Context contexts[4];
} RgbNir14v3;

static void rgbnir14_create_and_init(RgbNir14v3 *w, U32 context, const U8 *item)
{
    RgbNir14Context *c = &w->contexts[context];
    U32 i;

    if (!c->created) {
        laz_symbol_model_setup(&c->m_rgb_bytes_used, 128, LAZ_TRUE);
        for (i = 0; i < 6; i++) laz_symbol_model_setup(&c->m_rgb_diff[i], 256, LAZ_TRUE);
        laz_symbol_model_setup(&c->m_nir_bytes_used, 4, LAZ_TRUE);
        for (i = 0; i < 2; i++) laz_symbol_model_setup(&c->m_nir_diff[i], 256, LAZ_TRUE);
        c->created = LAZ_TRUE;
    }
    laz_symbol_model_init(&c->m_rgb_bytes_used, NULL);
    for (i = 0; i < 6; i++) laz_symbol_model_init(&c->m_rgb_diff[i], NULL);
    laz_symbol_model_init(&c->m_nir_bytes_used, NULL);
    for (i = 0; i < 2; i++) laz_symbol_model_init(&c->m_nir_diff[i], NULL);

    memcpy(c->last_item, item, 8);
    c->unused = LAZ_FALSE;
}

static BOOL rgbnir14_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    RgbNir14v3 *w = (RgbNir14v3 *)self;
    U32 c;

    if (!wlayer_create(&w->rgb) || !wlayer_create(&w->nir)) return LAZ_FALSE;
    wlayer_init(&w->rgb);
    wlayer_init(&w->nir);

    for (c = 0; c < 4; c++) w->contexts[c].unused = LAZ_TRUE;
    w->current_context = *context;
    rgbnir14_create_and_init(w, w->current_context, item);
    return LAZ_TRUE;
}

static BOOL rgbnir14_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    RgbNir14v3 *w = (RgbNir14v3 *)self;
    U16 *last_item = w->contexts[w->current_context].last_item;
    const U16 *cur = (const U16 *)item;
    RgbNir14Context *c;
    U32 sym;

    if (w->current_context != *context) {
        w->current_context = *context;
        if (w->contexts[w->current_context].unused) {
            rgbnir14_create_and_init(w, w->current_context, (const U8 *)last_item);
            if (!w->v4) last_item = w->contexts[w->current_context].last_item;
        }
        if (w->v4) last_item = w->contexts[w->current_context].last_item;
    }
    c = &w->contexts[w->current_context];

    if (rgb_encode(&w->rgb.enc, &c->m_rgb_bytes_used, c->m_rgb_diff, cur, last_item))
        w->rgb.changed = LAZ_TRUE;

    sym  = (U32)((last_item[3] & 0x00FF) != (cur[3] & 0x00FF)) << 0;
    sym |= (U32)((last_item[3] & 0xFF00) != (cur[3] & 0xFF00)) << 1;
    laz_encode_symbol(&w->nir.enc, &c->m_nir_bytes_used, sym);
    if (sym & (1 << 0)) {
        I32 diff_l = (I32)(cur[3] & 255) - (I32)(last_item[3] & 255);
        laz_encode_symbol(&w->nir.enc, &c->m_nir_diff[0], (U32)(U8)U8_FOLD(diff_l));
    }
    if (sym & (1 << 1)) {
        I32 diff_h = (I32)(cur[3] >> 8) - (I32)(last_item[3] >> 8);
        laz_encode_symbol(&w->nir.enc, &c->m_nir_diff[1], (U32)(U8)U8_FOLD(diff_h));
    }
    if (sym) w->nir.changed = LAZ_TRUE;

    memcpy(last_item, item, 8);
    return LAZ_TRUE;
}

static BOOL rgbnir14_chunk_sizes(LazWriteItem *self)
{
    RgbNir14v3 *w = (RgbNir14v3 *)self;
    LazOutStream *out = chunk_stream(self);
    laz_encoder_done(&w->rgb.enc);
    laz_encoder_done(&w->nir.enc);
    wlayer_put_size(&w->rgb, out);
    wlayer_put_size(&w->nir, out);
    return LAZ_TRUE;
}

static BOOL rgbnir14_chunk_bytes(LazWriteItem *self)
{
    RgbNir14v3 *w = (RgbNir14v3 *)self;
    LazOutStream *out = chunk_stream(self);
    wlayer_put_bytes(&w->rgb, out);
    wlayer_put_bytes(&w->nir, out);
    return LAZ_TRUE;
}

static void rgbnir14_destroy(LazWriteItem *self)
{
    RgbNir14v3 *w = (RgbNir14v3 *)self;
    U32 ci, i;
    for (ci = 0; ci < 4; ci++) {
        RgbNir14Context *c = &w->contexts[ci];
        if (!c->created) continue;
        laz_symbol_model_free(&c->m_rgb_bytes_used);
        for (i = 0; i < 6; i++) laz_symbol_model_free(&c->m_rgb_diff[i]);
        laz_symbol_model_free(&c->m_nir_bytes_used);
        for (i = 0; i < 2; i++) laz_symbol_model_free(&c->m_nir_diff[i]);
    }
    wlayer_free(&w->rgb);
    wlayer_free(&w->nir);
}

static LazWriteItem *rgbnir14_new(LazEncoder *enc, BOOL v4)
{
    RgbNir14v3 *w = (RgbNir14v3 *)calloc(1, sizeof(RgbNir14v3));
    if (!w) return NULL;
    w->base.write = rgbnir14_write;
    w->base.init = rgbnir14_init;
    w->base.chunk_sizes = rgbnir14_chunk_sizes;
    w->base.chunk_bytes = rgbnir14_chunk_bytes;
    w->base.destroy = rgbnir14_destroy;
    w->base.enc = enc;
    w->v4 = v4;
    return (LazWriteItem *)w;
}

LazWriteItem *laz_writeitem_v3_rgbnir14(LazEncoder *enc) { return rgbnir14_new(enc, LAZ_FALSE); }
LazWriteItem *laz_writeitem_v4_rgbnir14(LazEncoder *enc) { return rgbnir14_new(enc, LAZ_TRUE); }

/* =================================================== WAVEPACKET14 v3/v4 == */

typedef struct {
    BOOL unused;
    BOOL created;
    U8 last_item[29];
    I32 last_diff_32;
    U32 sym_last_offset_diff;
    LazSymbolModel m_packet_index;
    LazSymbolModel m_offset_diff[4];
    LazIntCompressor ic_offset_diff, ic_packet_size, ic_return_point, ic_xyz;
} Wave14Context;

typedef struct {
    LazWriteItem base;
    BOOL v4;
    WLayer wavepacket;
    U32 current_context;
    Wave14Context contexts[4];
} Wave14v3;

static void wave14_create_and_init(Wave14v3 *w, U32 context, const U8 *item)
{
    Wave14Context *c = &w->contexts[context];
    U32 i;

    if (!c->created) {
        laz_symbol_model_setup(&c->m_packet_index, 256, LAZ_TRUE);
        for (i = 0; i < 4; i++) laz_symbol_model_setup(&c->m_offset_diff[i], 4, LAZ_TRUE);
        laz_ic_setup_enc(&c->ic_offset_diff, &w->wavepacket.enc, 32, 1, 8, 0);
        laz_ic_setup_enc(&c->ic_packet_size, &w->wavepacket.enc, 32, 1, 8, 0);
        laz_ic_setup_enc(&c->ic_return_point, &w->wavepacket.enc, 32, 1, 8, 0);
        laz_ic_setup_enc(&c->ic_xyz, &w->wavepacket.enc, 32, 3, 8, 0);
        c->created = LAZ_TRUE;
    }
    laz_symbol_model_init(&c->m_packet_index, NULL);
    for (i = 0; i < 4; i++) laz_symbol_model_init(&c->m_offset_diff[i], NULL);
    laz_ic_init_compressor(&c->ic_offset_diff);
    laz_ic_init_compressor(&c->ic_packet_size);
    laz_ic_init_compressor(&c->ic_return_point);
    laz_ic_init_compressor(&c->ic_xyz);

    c->last_diff_32 = 0;
    c->sym_last_offset_diff = 0;
    memcpy(c->last_item, item, 29);
    c->unused = LAZ_FALSE;
}

static BOOL wave14_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Wave14v3 *w = (Wave14v3 *)self;
    U32 c;

    if (!wlayer_create(&w->wavepacket)) return LAZ_FALSE;
    wlayer_init(&w->wavepacket);

    for (c = 0; c < 4; c++) w->contexts[c].unused = LAZ_TRUE;
    w->current_context = *context;
    wave14_create_and_init(w, w->current_context, item);
    return LAZ_TRUE;
}

static BOOL wave14_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Wave14v3 *w = (Wave14v3 *)self;
    U8 *last_item = w->contexts[w->current_context].last_item;
    Wave14Context *c;
    LazWavepacket13 this_wp, last_wp;
    I64 diff64;
    I32 diff32;

    if (w->current_context != *context) {
        w->current_context = *context;
        if (w->contexts[w->current_context].unused) {
            wave14_create_and_init(w, w->current_context, last_item);
            if (!w->v4) last_item = w->contexts[w->current_context].last_item;
        }
        if (w->v4) last_item = w->contexts[w->current_context].last_item;
    }
    c = &w->contexts[w->current_context];

    if (memcmp(item, last_item, 29) != 0) w->wavepacket.changed = LAZ_TRUE;

    laz_encode_symbol(&w->wavepacket.enc, &c->m_packet_index, item[0]);

    this_wp = laz_wp_unpack(item + 1);
    last_wp = laz_wp_unpack(last_item + 1);

    diff64 = (I64)(this_wp.offset - last_wp.offset);
    diff32 = (I32)diff64;

    if (diff64 == (I64)diff32) {
        if (diff32 == 0) {
            laz_encode_symbol(&w->wavepacket.enc,
                              &c->m_offset_diff[c->sym_last_offset_diff], 0);
            c->sym_last_offset_diff = 0;
        } else if (diff32 == (I32)last_wp.packet_size) {
            laz_encode_symbol(&w->wavepacket.enc,
                              &c->m_offset_diff[c->sym_last_offset_diff], 1);
            c->sym_last_offset_diff = 1;
        } else {
            laz_encode_symbol(&w->wavepacket.enc,
                              &c->m_offset_diff[c->sym_last_offset_diff], 2);
            c->sym_last_offset_diff = 2;
            laz_ic_compress(&c->ic_offset_diff, c->last_diff_32, diff32, 0);
            c->last_diff_32 = diff32;
        }
    } else {
        laz_encode_symbol(&w->wavepacket.enc,
                          &c->m_offset_diff[c->sym_last_offset_diff], 3);
        c->sym_last_offset_diff = 3;
        laz_write_int64(&w->wavepacket.enc, this_wp.offset);
    }

    laz_ic_compress(&c->ic_packet_size, (I32)last_wp.packet_size,
                    (I32)this_wp.packet_size, 0);
    laz_ic_compress(&c->ic_return_point, last_wp.return_point, this_wp.return_point, 0);
    laz_ic_compress(&c->ic_xyz, last_wp.x, this_wp.x, 0);
    laz_ic_compress(&c->ic_xyz, last_wp.y, this_wp.y, 1);
    laz_ic_compress(&c->ic_xyz, last_wp.z, this_wp.z, 2);

    memcpy(last_item, item, 29);
    return LAZ_TRUE;
}

static BOOL wave14_chunk_sizes(LazWriteItem *self)
{
    Wave14v3 *w = (Wave14v3 *)self;
    laz_encoder_done(&w->wavepacket.enc);
    wlayer_put_size(&w->wavepacket, chunk_stream(self));
    return LAZ_TRUE;
}

static BOOL wave14_chunk_bytes(LazWriteItem *self)
{
    wlayer_put_bytes(&((Wave14v3 *)self)->wavepacket, chunk_stream(self));
    return LAZ_TRUE;
}

static void wave14_destroy(LazWriteItem *self)
{
    Wave14v3 *w = (Wave14v3 *)self;
    U32 ci, i;
    for (ci = 0; ci < 4; ci++) {
        Wave14Context *c = &w->contexts[ci];
        if (!c->created) continue;
        laz_symbol_model_free(&c->m_packet_index);
        for (i = 0; i < 4; i++) laz_symbol_model_free(&c->m_offset_diff[i]);
        laz_ic_free(&c->ic_offset_diff);
        laz_ic_free(&c->ic_packet_size);
        laz_ic_free(&c->ic_return_point);
        laz_ic_free(&c->ic_xyz);
    }
    wlayer_free(&w->wavepacket);
}

static LazWriteItem *wave14_new(LazEncoder *enc, BOOL v4)
{
    Wave14v3 *w = (Wave14v3 *)calloc(1, sizeof(Wave14v3));
    if (!w) return NULL;
    w->base.write = wave14_write;
    w->base.init = wave14_init;
    w->base.chunk_sizes = wave14_chunk_sizes;
    w->base.chunk_bytes = wave14_chunk_bytes;
    w->base.destroy = wave14_destroy;
    w->base.enc = enc;
    w->v4 = v4;
    return (LazWriteItem *)w;
}

LazWriteItem *laz_writeitem_v3_wavepacket14(LazEncoder *enc)
{ return wave14_new(enc, LAZ_FALSE); }
LazWriteItem *laz_writeitem_v4_wavepacket14(LazEncoder *enc)
{ return wave14_new(enc, LAZ_TRUE); }

/* ========================================================= BYTE14 v3/v4 == */

typedef struct {
    BOOL unused;
    BOOL created;
    U8 *last_item;              /* [number] */
    LazSymbolModel *m_bytes;    /* [number] */
} Byte14Context;

typedef struct {
    LazWriteItem base;
    BOOL v4;
    U32 number;
    WLayer *layers;             /* [number], one per extra byte */
    U32 current_context;
    Byte14Context contexts[4];
} Byte14v3;

static BOOL byte14_create_and_init(Byte14v3 *w, U32 context, const U8 *item)
{
    Byte14Context *c = &w->contexts[context];
    U32 i;

    if (!c->created) {
        c->m_bytes = laz_symbol_models_new(w->number, 256, LAZ_TRUE);
        c->last_item = (U8 *)calloc(w->number ? w->number : 1, 1);
        if (!c->m_bytes || !c->last_item) return LAZ_FALSE;
        c->created = LAZ_TRUE;
    }

    for (i = 0; i < w->number; i++) laz_symbol_model_init(&c->m_bytes[i], NULL);
    memcpy(c->last_item, item, w->number);
    c->unused = LAZ_FALSE;
    return LAZ_TRUE;
}

static BOOL byte14_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Byte14v3 *w = (Byte14v3 *)self;
    U32 i, c;

    for (i = 0; i < w->number; i++) {
        if (!wlayer_create(&w->layers[i])) return LAZ_FALSE;
        wlayer_init(&w->layers[i]);
    }

    for (c = 0; c < 4; c++) w->contexts[c].unused = LAZ_TRUE;
    w->current_context = *context;
    return byte14_create_and_init(w, w->current_context, item);
}

static BOOL byte14_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Byte14v3 *w = (Byte14v3 *)self;
    U8 *last_item = w->contexts[w->current_context].last_item;
    Byte14Context *c;
    U32 i;

    if (w->current_context != *context) {
        w->current_context = *context;
        if (w->contexts[w->current_context].unused) {
            if (!byte14_create_and_init(w, w->current_context, last_item))
                return LAZ_FALSE;
            if (!w->v4) last_item = w->contexts[w->current_context].last_item;
        }
        if (w->v4) last_item = w->contexts[w->current_context].last_item;
    }
    c = &w->contexts[w->current_context];

    for (i = 0; i < w->number; i++) {
        I32 diff = (I32)item[i] - (I32)last_item[i];
        laz_encode_symbol(&w->layers[i].enc, &c->m_bytes[i], (U32)(U8)U8_FOLD(diff));
        if (diff) {
            w->layers[i].changed = LAZ_TRUE;
            last_item[i] = item[i];
        }
    }
    return LAZ_TRUE;
}

static BOOL byte14_chunk_sizes(LazWriteItem *self)
{
    Byte14v3 *w = (Byte14v3 *)self;
    LazOutStream *out = chunk_stream(self);
    U32 i;
    for (i = 0; i < w->number; i++) {
        laz_encoder_done(&w->layers[i].enc);
        wlayer_put_size(&w->layers[i], out);
    }
    return LAZ_TRUE;
}

static BOOL byte14_chunk_bytes(LazWriteItem *self)
{
    Byte14v3 *w = (Byte14v3 *)self;
    LazOutStream *out = chunk_stream(self);
    U32 i;
    for (i = 0; i < w->number; i++) wlayer_put_bytes(&w->layers[i], out);
    return LAZ_TRUE;
}

static void byte14_destroy(LazWriteItem *self)
{
    Byte14v3 *w = (Byte14v3 *)self;
    U32 ci, i;
    for (ci = 0; ci < 4; ci++) {
        /* not gated on `created`: a context whose models were allocated but
         * whose last_item was not still owns those models */
        Byte14Context *c = &w->contexts[ci];
        if (c->m_bytes) laz_symbol_models_free(c->m_bytes, w->number);
        free(c->last_item);
    }
    if (w->layers) {
        for (i = 0; i < w->number; i++) wlayer_free(&w->layers[i]);
        free(w->layers);
    }
}

static LazWriteItem *byte14_new(LazEncoder *enc, U32 number, BOOL v4)
{
    Byte14v3 *w = (Byte14v3 *)calloc(1, sizeof(Byte14v3));
    if (!w) return NULL;
    w->base.write = byte14_write;
    w->base.init = byte14_init;
    w->base.chunk_sizes = byte14_chunk_sizes;
    w->base.chunk_bytes = byte14_chunk_bytes;
    w->base.destroy = byte14_destroy;
    w->base.enc = enc;
    w->v4 = v4;
    w->number = number;
    w->layers = (WLayer *)calloc(number ? number : 1, sizeof(WLayer));
    if (!w->layers) { free(w); return NULL; }
    return (LazWriteItem *)w;
}

LazWriteItem *laz_writeitem_v3_byte14(LazEncoder *enc, U32 number)
{ return byte14_new(enc, number, LAZ_FALSE); }
LazWriteItem *laz_writeitem_v4_byte14(LazEncoder *enc, U32 number)
{ return byte14_new(enc, number, LAZ_TRUE); }
