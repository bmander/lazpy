/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * LAZ v3 and v4 layered item readers for the LAS 1.4 point types.
 *
 * Ported from LASzip's lasreaditemcompressed_v3.cpp and _v4.cpp.
 *
 * These differ from v1/v2 in two structural ways:
 *
 *  1. Layering. A chunk is not one arithmetic stream but several: Z,
 *     classification, flags, intensity, scan angle, user data, point source
 *     and GPS time each get their own byte range with its own decoder, so a
 *     reader can skip whole attributes it was not asked for. That is why every
 *     layer owns an in-memory stream, and why chunk_sizes() must run for all
 *     items before any of them is init'd.
 *
 *  2. Scanner-channel contexts. Up to four independent model sets exist per
 *     item; the POINT14 reader decodes which one is active and passes it to
 *     the other items through the shared `context` parameter.
 *
 * v4 is v3 with four fixes, applied here behind the `v4` flag rather than by
 * duplicating the file (LASzip ships them as near-identical copies):
 *   - POINT14 publishes `context` on every point, not only when the scanner
 *     channel changed;
 *   - RGB14/RGBNIR14/WAVEPACKET14/BYTE14 refresh their `last_item` pointer on
 *     any context switch, not only when switching to a never-used context
 *     (in v3 an already-used context kept reading the previous context's
 *     last item);
 *   - RGBNIR14's NIR branch reads last_item through the context rather than
 *     through that possibly-stale pointer.
 */
#include "laz_readitem.h"

/* The LASpoint14 field accessors this file decodes into live in laz_item.h,
 * shared with the writers so one definition of the layout serves both. */

#define LASZIP_GPSTIME_MULTI            500
#define LASZIP_GPSTIME_MULTI_MINUS      (-10)
#define LASZIP_GPSTIME_MULTI_CODE_FULL  (LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS + 1)
#define LASZIP_GPSTIME_MULTI_TOTAL      (LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS + 5)

const U8 laz_number_return_map_6ctx[16][16] = {
    {  0,  1,  2,  3,  4,  5,  3,  4,  4,  5,  5,  5,  5,  5,  5,  5 },
    {  1,  0,  1,  3,  3,  3,  3,  3,  3,  3,  3,  3,  3,  3,  3,  3 },
    {  2,  1,  2,  4,  4,  4,  4,  4,  4,  4,  4,  3,  3,  3,  3,  3 },
    {  3,  3,  4,  5,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4 },
    {  4,  3,  4,  4,  5,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4 },
    {  5,  3,  4,  4,  4,  5,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4 },
    {  3,  3,  4,  4,  4,  4,  5,  4,  4,  4,  4,  4,  4,  4,  4,  4 },
    {  4,  3,  4,  4,  4,  4,  4,  5,  4,  4,  4,  4,  4,  4,  4,  4 },
    {  4,  3,  4,  4,  4,  4,  4,  4,  5,  4,  4,  4,  4,  4,  4,  4 },
    {  5,  3,  4,  4,  4,  4,  4,  4,  4,  5,  4,  4,  4,  4,  4,  4 },
    {  5,  3,  4,  4,  4,  4,  4,  4,  4,  4,  5,  4,  4,  4,  4,  4 },
    {  5,  3,  3,  4,  4,  4,  4,  4,  4,  4,  4,  5,  5,  4,  4,  4 },
    {  5,  3,  3,  4,  4,  4,  4,  4,  4,  4,  4,  5,  5,  5,  4,  4 },
    {  5,  3,  3,  4,  4,  4,  4,  4,  4,  4,  4,  4,  5,  5,  5,  4 },
    {  5,  3,  3,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  5,  5,  5 },
    {  5,  3,  3,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  4,  5,  5 }
};

const U8 laz_number_return_level_8ctx[16][16] = {
    {  0,  1,  2,  3,  4,  5,  6,  7,  7,  7,  7,  7,  7,  7,  7,  7 },
    {  1,  0,  1,  2,  3,  4,  5,  6,  7,  7,  7,  7,  7,  7,  7,  7 },
    {  2,  1,  0,  1,  2,  3,  4,  5,  6,  7,  7,  7,  7,  7,  7,  7 },
    {  3,  2,  1,  0,  1,  2,  3,  4,  5,  6,  7,  7,  7,  7,  7,  7 },
    {  4,  3,  2,  1,  0,  1,  2,  3,  4,  5,  6,  7,  7,  7,  7,  7 },
    {  5,  4,  3,  2,  1,  0,  1,  2,  3,  4,  5,  6,  7,  7,  7,  7 },
    {  6,  5,  4,  3,  2,  1,  0,  1,  2,  3,  4,  5,  6,  7,  7,  7 },
    {  7,  6,  5,  4,  3,  2,  1,  0,  1,  2,  3,  4,  5,  6,  7,  7 },
    {  7,  7,  6,  5,  4,  3,  2,  1,  0,  1,  2,  3,  4,  5,  6,  7 },
    {  7,  7,  7,  6,  5,  4,  3,  2,  1,  0,  1,  2,  3,  4,  5,  6 },
    {  7,  7,  7,  7,  6,  5,  4,  3,  2,  1,  0,  1,  2,  3,  4,  5 },
    {  7,  7,  7,  7,  7,  6,  5,  4,  3,  2,  1,  0,  1,  2,  3,  4 },
    {  7,  7,  7,  7,  7,  7,  6,  5,  4,  3,  2,  1,  0,  1,  2,  3 },
    {  7,  7,  7,  7,  7,  7,  7,  6,  5,  4,  3,  2,  1,  0,  1,  2 },
    {  7,  7,  7,  7,  7,  7,  7,  7,  6,  5,  4,  3,  2,  1,  0,  1 },
    {  7,  7,  7,  7,  7,  7,  7,  7,  7,  6,  5,  4,  3,  2,  1,  0 }
};

/* ---------------------------------------------------------------- layers - */

/*
 * One decodable byte layer of a chunk. `num_bytes` comes from chunk_sizes();
 * `requested` is fixed at construction from the selective-decompression mask;
 * `changed` says whether this layer actually carries data in this chunk, which
 * is what the read path tests.
 */
typedef struct {
    LazStream *stream;
    LazDecoder dec;
    U32 num_bytes;
    BOOL changed;
    BOOL requested;
} Layer;

static BOOL layer_create(Layer *l)
{
    if (l->stream) return LAZ_TRUE;
    l->stream = laz_stream_new_array(NULL, 0);
    if (!l->stream) return LAZ_FALSE;
    laz_decoder_setup(&l->dec, l->stream);
    return LAZ_TRUE;
}

static void layer_free(Layer *l)
{
    if (l->stream) laz_stream_destroy(l->stream);
    l->stream = NULL;
}

static void stream_skip(LazStream *in, U32 n)
{
    laz_stream_seek(in, laz_stream_tell(in) + (I64)n);
}

/* Copies this layer's bytes out of `in` into `buf` at `*offset` and points the
 * layer's decoder at them; skips them in the stream if not requested. */
static void layer_load(Layer *l, LazStream *in, U8 *buf, U64 *offset)
{
    if (l->requested) {
        if (l->num_bytes) {
            laz_stream_get_bytes(in, buf + *offset, l->num_bytes);
            laz_stream_array_reset(l->stream, buf + *offset, l->num_bytes);
            laz_decoder_init(&l->dec, l->stream, LAZ_TRUE);
            *offset += l->num_bytes;
            l->changed = LAZ_TRUE;
        } else {
            laz_stream_array_reset(l->stream, NULL, 0);
            l->changed = LAZ_FALSE;
        }
    } else {
        if (l->num_bytes) stream_skip(in, l->num_bytes);
        l->changed = LAZ_FALSE;
    }
}

/* TRUE once this layer's decoder has read past the bytes it was given. */
static BOOL layer_overran(const Layer *l)
{
    return l->stream && l->stream->eof;
}

/* Grows `*buf` to at least `need` bytes. */
static BOOL ensure_bytes(U8 **buf, U64 *allocated, U64 need)
{
    U8 *grown;
    if (need <= *allocated) return LAZ_TRUE;
    grown = (U8 *)realloc(*buf, (size_t)need);
    if (!grown) return LAZ_FALSE;
    *buf = grown;
    *allocated = need;
    return LAZ_TRUE;
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
    U64 last_gpstime[4];
    I32 last_gpstime_diff[4];
    I32 multi_extreme_counter[4];
    LazSymbolModel m_gpstime_multi, m_gpstime_0diff;
    LazIntCompressor ic_gpstime;
} Point14Context;

typedef struct {
    LazReadItem base;
    BOOL v4;
    Layer channel_returns_XY, Z, classification, flags, intensity;
    Layer scan_angle, user_data, point_source, gps_time;
    U8 *bytes;
    U64 num_bytes_allocated;
    U32 current_context;
    Point14Context contexts[4];
} Point14v3;

static void p14_read_gps_time(Point14v3 *r);

/* Returns LAZ_FALSE if any of the models this context needs could not be
 * allocated. Callers on the read path record that on the item; see
 * LazReadItem.alloc_failed. */
static BOOL p14_create_and_init(Point14v3 *r, U32 context, const U8 *item)
{
    Point14Context *c = &r->contexts[context];
    U32 i;

    if (!c->created) {
        for (i = 0; i < 8; i++)
            laz_symbol_model_setup(&c->m_changed_values[i], 128, LAZ_FALSE);
        laz_symbol_model_setup(&c->m_scanner_channel, 3, LAZ_FALSE);
        laz_bank_setup(c->m_number_of_returns, c->created_nor, 16, 16, LAZ_FALSE);
        laz_bank_setup(c->m_return_number, c->created_rn, 16, 16, LAZ_FALSE);
        laz_symbol_model_setup(&c->m_return_number_gps_same, 13, LAZ_FALSE);

        laz_ic_setup_dec(&c->ic_dX, &r->channel_returns_XY.dec, 32, 2, 8, 0);
        laz_ic_setup_dec(&c->ic_dY, &r->channel_returns_XY.dec, 32, 22, 8, 0);
        laz_ic_setup_dec(&c->ic_Z, &r->Z.dec, 32, 20, 8, 0);

        laz_bank_setup(c->m_classification, c->created_cls, 64, 256, LAZ_FALSE);
        laz_bank_setup(c->m_flags, c->created_flg, 64, 64, LAZ_FALSE);
        laz_bank_setup(c->m_user_data, c->created_usr, 64, 256, LAZ_FALSE);

        laz_ic_setup_dec(&c->ic_intensity, &r->intensity.dec, 16, 4, 8, 0);
        laz_ic_setup_dec(&c->ic_scan_angle, &r->scan_angle.dec, 16, 2, 8, 0);
        laz_ic_setup_dec(&c->ic_point_source_ID, &r->point_source.dec, 16, 1, 8, 0);

        laz_symbol_model_setup(&c->m_gpstime_multi, LASZIP_GPSTIME_MULTI_TOTAL, LAZ_FALSE);
        laz_symbol_model_setup(&c->m_gpstime_0diff, 5, LAZ_FALSE);
        laz_ic_setup_dec(&c->ic_gpstime, &r->gps_time.dec, 32, 9, 8, 0);

        c->created = LAZ_TRUE;
    }

    /* channel_returns_XY layer */
    for (i = 0; i < 8; i++) {
        if (!laz_symbol_model_init(&c->m_changed_values[i], NULL)) return LAZ_FALSE;
    }
    if (!laz_symbol_model_init(&c->m_scanner_channel, NULL) ||
        !laz_bank_reinit(c->m_number_of_returns, c->created_nor, 16) ||
        !laz_bank_reinit(c->m_return_number, c->created_rn, 16) ||
        !laz_symbol_model_init(&c->m_return_number_gps_same, NULL) ||
        !laz_ic_init_decompressor(&c->ic_dX) ||
        !laz_ic_init_decompressor(&c->ic_dY))
        return LAZ_FALSE;
    for (i = 0; i < 12; i++) {
        laz_median5_init(&c->last_X_diff_median5[i]);
        laz_median5_init(&c->last_Y_diff_median5[i]);
    }

    /* Z layer */
    if (!laz_ic_init_decompressor(&c->ic_Z)) return LAZ_FALSE;
    for (i = 0; i < 8; i++) c->last_Z[i] = P14_Z(item);

    /* classification / flags / user_data layers */
    if (!laz_bank_reinit(c->m_classification, c->created_cls, 64) ||
        !laz_bank_reinit(c->m_flags, c->created_flg, 64) ||
        !laz_bank_reinit(c->m_user_data, c->created_usr, 64))
        return LAZ_FALSE;

    /* intensity layer */
    if (!laz_ic_init_decompressor(&c->ic_intensity)) return LAZ_FALSE;
    for (i = 0; i < 8; i++) c->last_intensity[i] = P14_INTENSITY(item);

    if (!laz_ic_init_decompressor(&c->ic_scan_angle) ||
        !laz_ic_init_decompressor(&c->ic_point_source_ID))
        return LAZ_FALSE;

    /* gps_time layer */
    if (!laz_symbol_model_init(&c->m_gpstime_multi, NULL) ||
        !laz_symbol_model_init(&c->m_gpstime_0diff, NULL) ||
        !laz_ic_init_decompressor(&c->ic_gpstime))
        return LAZ_FALSE;
    c->last = 0;
    c->next = 0;
    memset(c->last_gpstime_diff, 0, sizeof(c->last_gpstime_diff));
    memset(c->multi_extreme_counter, 0, sizeof(c->multi_extreme_counter));
    memcpy(&c->last_gpstime[0], item + 32, 8);
    c->last_gpstime[1] = c->last_gpstime[2] = c->last_gpstime[3] = 0;

    memcpy(c->last_item, item, LASPOINT14_SIZE);
    P14_GPS_TIME_CHANGE(c->last_item) = LAZ_FALSE;

    c->unused = LAZ_FALSE;
    return LAZ_TRUE;
}

static BOOL p14_chunk_sizes(LazReadItem *self)
{
    Point14v3 *r = (Point14v3 *)self;
    LazStream *in = self->dec->stream;
    r->channel_returns_XY.num_bytes = laz_stream_get32(in);
    r->Z.num_bytes = laz_stream_get32(in);
    r->classification.num_bytes = laz_stream_get32(in);
    r->flags.num_bytes = laz_stream_get32(in);
    r->intensity.num_bytes = laz_stream_get32(in);
    r->scan_angle.num_bytes = laz_stream_get32(in);
    r->user_data.num_bytes = laz_stream_get32(in);
    r->point_source.num_bytes = laz_stream_get32(in);
    r->gps_time.num_bytes = laz_stream_get32(in);
    return LAZ_TRUE;
}

static BOOL p14_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Point14v3 *r = (Point14v3 *)self;
    LazStream *in = self->dec->stream;
    U64 num_bytes, offset;
    U32 c;

    if (!layer_create(&r->channel_returns_XY) || !layer_create(&r->Z) ||
        !layer_create(&r->classification) || !layer_create(&r->flags) ||
        !layer_create(&r->intensity) || !layer_create(&r->scan_angle) ||
        !layer_create(&r->user_data) || !layer_create(&r->point_source) ||
        !layer_create(&r->gps_time))
        return LAZ_FALSE;

    /* channel_returns_XY is never optional: it carries the context switches */
    num_bytes = r->channel_returns_XY.num_bytes;
    if (r->Z.requested) num_bytes += r->Z.num_bytes;
    if (r->classification.requested) num_bytes += r->classification.num_bytes;
    if (r->flags.requested) num_bytes += r->flags.num_bytes;
    if (r->intensity.requested) num_bytes += r->intensity.num_bytes;
    if (r->scan_angle.requested) num_bytes += r->scan_angle.num_bytes;
    if (r->user_data.requested) num_bytes += r->user_data.num_bytes;
    if (r->point_source.requested) num_bytes += r->point_source.num_bytes;
    if (r->gps_time.requested) num_bytes += r->gps_time.num_bytes;

    if (!ensure_bytes(&r->bytes, &r->num_bytes_allocated, num_bytes)) return LAZ_FALSE;

    offset = 0;
    layer_load(&r->channel_returns_XY, in, r->bytes, &offset);
    layer_load(&r->Z, in, r->bytes, &offset);
    layer_load(&r->classification, in, r->bytes, &offset);
    layer_load(&r->flags, in, r->bytes, &offset);
    layer_load(&r->intensity, in, r->bytes, &offset);
    layer_load(&r->scan_angle, in, r->bytes, &offset);
    layer_load(&r->user_data, in, r->bytes, &offset);
    layer_load(&r->point_source, in, r->bytes, &offset);
    layer_load(&r->gps_time, in, r->bytes, &offset);

    for (c = 0; c < 4; c++) r->contexts[c].unused = LAZ_TRUE;

    r->current_context = P14_SCANNER_CHANNEL(item);
    *context = r->current_context;   /* POINT14 sets the context for all items */

    return p14_create_and_init(r, r->current_context, item);
}

static void p14_read(LazReadItem *self, U8 *item, U32 *context)
{
    Point14v3 *r = (Point14v3 *)self;
    Point14Context *cx = &r->contexts[r->current_context];
    U8 *last_item = cx->last_item;
    I32 lpr, changed_values, cpr;
    BOOL point_source_change, gps_time_change, scan_angle_change;
    U32 last_n, last_r, n, rn, m, l, k_bits;
    I32 median, diff;
    LazSymbolModel *model;

    /* single (3) / first (1) / last (2) / intermediate (0) from the last return,
     * plus whether the GPS time changed on that return */
    lpr = (P14_RETURN_NUMBER(last_item) == 1 ? 1 : 0);
    lpr += (P14_RETURN_NUMBER(last_item) >= P14_NUMBER_OF_RETURNS(last_item) ? 2 : 0);
    lpr += (P14_GPS_TIME_CHANGE(last_item) ? 4 : 0);

    changed_values = (I32)laz_decode_symbol(&r->channel_returns_XY.dec,
                                            &cx->m_changed_values[lpr]);

    if (changed_values & (1 << 6)) {        /* scanner channel changed */
        U32 diff_sym = laz_decode_symbol(&r->channel_returns_XY.dec, &cx->m_scanner_channel);
        U32 scanner_channel = (r->current_context + diff_sym + 1) % 4;
        if (r->contexts[scanner_channel].unused) {
            if (!p14_create_and_init(r, scanner_channel, cx->last_item)) {
                self->alloc_failed = LAZ_TRUE;
                return;
            }
        }
        r->current_context = scanner_channel;
        if (!r->v4) *context = r->current_context;
        cx = &r->contexts[r->current_context];
        last_item = cx->last_item;
        P14_SET_SCANNER_CHANNEL(last_item, scanner_channel);
    }
    /* v4 publishes the context on every point, not only when it changed */
    if (r->v4) *context = r->current_context;

    point_source_change = (changed_values & (1 << 5)) ? LAZ_TRUE : LAZ_FALSE;
    gps_time_change = (changed_values & (1 << 4)) ? LAZ_TRUE : LAZ_FALSE;
    scan_angle_change = (changed_values & (1 << 3)) ? LAZ_TRUE : LAZ_FALSE;

    last_n = P14_NUMBER_OF_RETURNS(last_item);
    last_r = P14_RETURN_NUMBER(last_item);

    if (changed_values & (1 << 2)) {
        model = laz_bank_get(cx->m_number_of_returns, cx->created_nor, last_n);
        if (!model) { self->alloc_failed = LAZ_TRUE; return; }
        n = laz_decode_symbol(&r->channel_returns_XY.dec, model);
        P14_SET_NUMBER_OF_RETURNS(last_item, n);
    } else {
        n = last_n;
    }

    if ((changed_values & 3) == 0) {
        rn = last_r;
    } else if ((changed_values & 3) == 1) {         /* plus 1 mod 16 */
        rn = (last_r + 1) % 16;
        P14_SET_RETURN_NUMBER(last_item, rn);
    } else if ((changed_values & 3) == 2) {         /* minus 1 mod 16 */
        rn = (last_r + 15) % 16;
        P14_SET_RETURN_NUMBER(last_item, rn);
    } else {
        /* difference bigger than +/-1, so decode how it differs */
        if (gps_time_change) {
            model = laz_bank_get(cx->m_return_number, cx->created_rn, last_r);
            if (!model) { self->alloc_failed = LAZ_TRUE; return; }
            rn = laz_decode_symbol(&r->channel_returns_XY.dec, model);
        } else {
            I32 sym = (I32)laz_decode_symbol(&r->channel_returns_XY.dec,
                                             &cx->m_return_number_gps_same);
            rn = (last_r + (sym + 2)) % 16;
        }
        P14_SET_RETURN_NUMBER(last_item, rn);
    }

    /* keep the legacy 3-bit copies in step, saturating rather than wrapping */
    if (n > 7) {
        if (rn > 6) {
            P14_SET_LEGACY_RETURN_NUMBER(last_item, (rn >= n) ? 7 : 6);
        } else {
            P14_SET_LEGACY_RETURN_NUMBER(last_item, rn);
        }
        P14_SET_LEGACY_NUMBER_OF_RETURNS(last_item, 7);
    } else {
        P14_SET_LEGACY_RETURN_NUMBER(last_item, rn);
        P14_SET_LEGACY_NUMBER_OF_RETURNS(last_item, n);
    }

    m = laz_number_return_map_6ctx[n][rn];
    l = laz_number_return_level_8ctx[n][rn];

    cpr = (rn == 1 ? 2 : 0);            /* first? */
    cpr += (rn >= n ? 1 : 0);           /* last? */

    /* X */
    median = laz_median5_get(&cx->last_X_diff_median5[(m << 1) | gps_time_change]);
    diff = laz_ic_decompress(&cx->ic_dX, median, (n == 1));
    P14_X(last_item) += diff;
    laz_median5_add(&cx->last_X_diff_median5[(m << 1) | gps_time_change], diff);

    /* Y */
    median = laz_median5_get(&cx->last_Y_diff_median5[(m << 1) | gps_time_change]);
    k_bits = cx->ic_dX.k;
    diff = laz_ic_decompress(&cx->ic_dY, median,
                             (n == 1) + (k_bits < 20 ? U32_ZERO_BIT_0(k_bits) : 20));
    P14_Y(last_item) += diff;
    laz_median5_add(&cx->last_Y_diff_median5[(m << 1) | gps_time_change], diff);

    if (r->Z.changed) {
        k_bits = (cx->ic_dX.k + cx->ic_dY.k) / 2;
        P14_Z(last_item) = laz_ic_decompress(&cx->ic_Z, cx->last_Z[l],
                                             (n == 1) + (k_bits < 18 ? U32_ZERO_BIT_0(k_bits) : 18));
        cx->last_Z[l] = P14_Z(last_item);
    }

    if (r->classification.changed) {
        U32 last_classification = P14_CLASSIFICATION(last_item);
        I32 ccc = (I32)(((last_classification & 0x1F) << 1) + (cpr == 3 ? 1 : 0));
        U32 cls;
        model = laz_bank_get(cx->m_classification, cx->created_cls, (U32)ccc);
        if (!model) { self->alloc_failed = LAZ_TRUE; return; }
        cls = laz_decode_symbol(&r->classification.dec, model);
        P14_CLASSIFICATION(last_item) = (U8)cls;
        P14_SET_LEGACY_CLASSIFICATION(last_item, (cls < 32) ? cls : 0);
    }

    if (r->flags.changed) {
        U32 last_flags = (U32)((P14_EDGE_OF_FLIGHT_LINE(last_item) << 5) |
                               (P14_SCAN_DIRECTION_FLAG(last_item) << 4) |
                               P14_CLASSIFICATION_FLAGS(last_item));
        U32 fl;
        model = laz_bank_get(cx->m_flags, cx->created_flg, last_flags);
        if (!model) { self->alloc_failed = LAZ_TRUE; return; }
        fl = laz_decode_symbol(&r->flags.dec, model);
        P14_SET_EDGE_OF_FLIGHT_LINE(last_item, !!(fl & (1 << 5)));
        P14_SET_SCAN_DIRECTION_FLAG(last_item, !!(fl & (1 << 4)));
        P14_SET_CLASSIFICATION_FLAGS(last_item, fl & 0x0F);
        P14_SET_LEGACY_FLAGS(last_item, fl & 0x07);
    }

    if (r->intensity.changed) {
        U16 intensity = (U16)laz_ic_decompress(&cx->ic_intensity,
            cx->last_intensity[(cpr << 1) | gps_time_change], (U32)cpr);
        cx->last_intensity[(cpr << 1) | gps_time_change] = intensity;
        P14_INTENSITY(last_item) = intensity;
    }

    if (r->scan_angle.changed && scan_angle_change) {
        P14_SCAN_ANGLE(last_item) = (I16)laz_ic_decompress(
            &cx->ic_scan_angle, P14_SCAN_ANGLE(last_item), (U32)gps_time_change);
        P14_SET_LEGACY_SCAN_ANGLE_RANK(last_item,
            I8_CLAMP(I16_QUANTIZE(0.006f * P14_SCAN_ANGLE(last_item))));
    }

    if (r->user_data.changed) {
        U32 idx = P14_USER_DATA(last_item) / 4;
        model = laz_bank_get(cx->m_user_data, cx->created_usr, idx);
        if (!model) { self->alloc_failed = LAZ_TRUE; return; }
        P14_USER_DATA(last_item) = (U8)laz_decode_symbol(&r->user_data.dec, model);
    }

    if (r->point_source.changed && point_source_change) {
        P14_POINT_SOURCE_ID(last_item) = (U16)laz_ic_decompress(
            &cx->ic_point_source_ID, P14_POINT_SOURCE_ID(last_item), 0);
    }

    if (r->gps_time.changed && gps_time_change) {
        p14_read_gps_time(r);
        memcpy(last_item + 32, &cx->last_gpstime[cx->last], 8);
    }

    memcpy(item, last_item, LASPOINT14_SIZE);
    P14_GPS_TIME_CHANGE(last_item) = gps_time_change;
}

static void p14_read_gps_time(Point14v3 *r)
{
    Point14Context *c = &r->contexts[r->current_context];
    LazDecoder *dec = &r->gps_time.dec;
    I32 multi;

    if (c->last_gpstime_diff[c->last] == 0) {
        multi = (I32)laz_decode_symbol(dec, &c->m_gpstime_0diff);
        if (multi == 0) {                   /* difference fits in 32 bits */
            c->last_gpstime_diff[c->last] = laz_ic_decompress(&c->ic_gpstime, 0, 0);
            c->last_gpstime[c->last] =
                (U64)((I64)c->last_gpstime[c->last] + c->last_gpstime_diff[c->last]);
            c->multi_extreme_counter[c->last] = 0;
        } else if (multi == 1) {            /* difference is huge */
            c->next = (c->next + 1) & 3;
            c->last_gpstime[c->next] = (U64)(U32)laz_ic_decompress(
                &c->ic_gpstime, (I32)(c->last_gpstime[c->last] >> 32), 8);
            c->last_gpstime[c->next] <<= 32;
            c->last_gpstime[c->next] |= laz_read_int(dec);
            c->last = c->next;
            c->last_gpstime_diff[c->last] = 0;
            c->multi_extreme_counter[c->last] = 0;
        } else {                            /* switch to another sequence */
            c->last = (c->last + multi - 1) & 3;
            p14_read_gps_time(r);
        }
    } else {
        multi = (I32)laz_decode_symbol(dec, &c->m_gpstime_multi);
        if (multi == 1) {
            c->last_gpstime[c->last] = (U64)((I64)c->last_gpstime[c->last] +
                laz_ic_decompress(&c->ic_gpstime, c->last_gpstime_diff[c->last], 1));
            c->multi_extreme_counter[c->last] = 0;
        } else if (multi < LASZIP_GPSTIME_MULTI_CODE_FULL) {
            I32 gpstime_diff;
            if (multi == 0) {
                gpstime_diff = laz_ic_decompress(&c->ic_gpstime, 0, 7);
                c->multi_extreme_counter[c->last]++;
                if (c->multi_extreme_counter[c->last] > 3) {
                    c->last_gpstime_diff[c->last] = gpstime_diff;
                    c->multi_extreme_counter[c->last] = 0;
                }
            } else if (multi < LASZIP_GPSTIME_MULTI) {
                gpstime_diff = laz_ic_decompress(&c->ic_gpstime,
                    multi * c->last_gpstime_diff[c->last], (multi < 10) ? 2 : 3);
            } else if (multi == LASZIP_GPSTIME_MULTI) {
                gpstime_diff = laz_ic_decompress(&c->ic_gpstime,
                    LASZIP_GPSTIME_MULTI * c->last_gpstime_diff[c->last], 4);
                c->multi_extreme_counter[c->last]++;
                if (c->multi_extreme_counter[c->last] > 3) {
                    c->last_gpstime_diff[c->last] = gpstime_diff;
                    c->multi_extreme_counter[c->last] = 0;
                }
            } else {
                multi = LASZIP_GPSTIME_MULTI - multi;
                if (multi > LASZIP_GPSTIME_MULTI_MINUS) {
                    gpstime_diff = laz_ic_decompress(&c->ic_gpstime,
                        multi * c->last_gpstime_diff[c->last], 5);
                } else {
                    gpstime_diff = laz_ic_decompress(&c->ic_gpstime,
                        LASZIP_GPSTIME_MULTI_MINUS * c->last_gpstime_diff[c->last], 6);
                    c->multi_extreme_counter[c->last]++;
                    if (c->multi_extreme_counter[c->last] > 3) {
                        c->last_gpstime_diff[c->last] = gpstime_diff;
                        c->multi_extreme_counter[c->last] = 0;
                    }
                }
            }
            c->last_gpstime[c->last] = (U64)((I64)c->last_gpstime[c->last] + gpstime_diff);
        } else if (multi == LASZIP_GPSTIME_MULTI_CODE_FULL) {
            c->next = (c->next + 1) & 3;
            c->last_gpstime[c->next] = (U64)(U32)laz_ic_decompress(
                &c->ic_gpstime, (I32)(c->last_gpstime[c->last] >> 32), 8);
            c->last_gpstime[c->next] <<= 32;
            c->last_gpstime[c->next] |= laz_read_int(dec);
            c->last = c->next;
            c->last_gpstime_diff[c->last] = 0;
            c->multi_extreme_counter[c->last] = 0;
        } else if (multi >= LASZIP_GPSTIME_MULTI_CODE_FULL) {
            c->last = (c->last + multi - LASZIP_GPSTIME_MULTI_CODE_FULL) & 3;
            p14_read_gps_time(r);
        }
    }
}

static BOOL p14_overran(LazReadItem *self)
{
    Point14v3 *r = (Point14v3 *)self;
    return layer_overran(&r->channel_returns_XY) || layer_overran(&r->Z) ||
           layer_overran(&r->classification) || layer_overran(&r->flags) ||
           layer_overran(&r->intensity) || layer_overran(&r->scan_angle) ||
           layer_overran(&r->user_data) || layer_overran(&r->point_source) ||
           layer_overran(&r->gps_time);
}

static void p14_destroy(LazReadItem *self)
{
    Point14v3 *r = (Point14v3 *)self;
    U32 i, ci;
    for (ci = 0; ci < 4; ci++) {
        Point14Context *c = &r->contexts[ci];
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
    layer_free(&r->channel_returns_XY);
    layer_free(&r->Z);
    layer_free(&r->classification);
    layer_free(&r->flags);
    layer_free(&r->intensity);
    layer_free(&r->scan_angle);
    layer_free(&r->user_data);
    layer_free(&r->point_source);
    layer_free(&r->gps_time);
    free(r->bytes);
}

static LazReadItem *point14_new(LazDecoder *dec, U32 selective, BOOL v4)
{
    Point14v3 *r = (Point14v3 *)calloc(1, sizeof(Point14v3));
    if (!r) return NULL;
    r->base.read = p14_read;
    r->base.init = p14_init;
    r->base.chunk_sizes = p14_chunk_sizes;
    r->base.overran = p14_overran;
    r->base.destroy = p14_destroy;
    r->base.dec = dec;
    r->v4 = v4;

    r->channel_returns_XY.requested = LAZ_TRUE;
    r->Z.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_Z);
    r->classification.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_CLASSIFICATION);
    r->flags.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_FLAGS);
    r->intensity.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_INTENSITY);
    r->scan_angle.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_SCAN_ANGLE);
    r->user_data.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_USER_DATA);
    r->point_source.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_POINT_SOURCE);
    r->gps_time.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_GPS_TIME);
    return (LazReadItem *)r;
}

LazReadItem *laz_readitem_v3_point14(LazDecoder *dec, U32 s)
{ return point14_new(dec, s, LAZ_FALSE); }
LazReadItem *laz_readitem_v4_point14(LazDecoder *dec, U32 s)
{ return point14_new(dec, s, LAZ_TRUE); }

/* ========================================================== RGB14 v3/v4 == */

typedef struct {
    BOOL unused;
    BOOL created;
    U16 last_item[3];
    LazSymbolModel m_byte_used;
    LazSymbolModel m_rgb_diff[6];
} Rgb14Context;

typedef struct {
    LazReadItem base;
    BOOL v4;
    Layer rgb;
    U8 *bytes;
    U64 num_bytes_allocated;
    U32 current_context;
    Rgb14Context contexts[4];
} Rgb14v3;

static BOOL rgb14_create_and_init(Rgb14v3 *r, U32 context, const U8 *item)
{
    Rgb14Context *c = &r->contexts[context];
    U32 i;
    if (!c->created) {
        laz_symbol_model_setup(&c->m_byte_used, 128, LAZ_FALSE);
        for (i = 0; i < 6; i++) laz_symbol_model_setup(&c->m_rgb_diff[i], 256, LAZ_FALSE);
        c->created = LAZ_TRUE;
    }
    if (!laz_symbol_model_init(&c->m_byte_used, NULL)) return LAZ_FALSE;
    for (i = 0; i < 6; i++) {
        if (!laz_symbol_model_init(&c->m_rgb_diff[i], NULL)) return LAZ_FALSE;
    }
    memcpy(c->last_item, item, 6);
    c->unused = LAZ_FALSE;
    return LAZ_TRUE;
}

static BOOL rgb14_chunk_sizes(LazReadItem *self)
{
    Rgb14v3 *r = (Rgb14v3 *)self;
    r->rgb.num_bytes = laz_stream_get32(self->dec->stream);
    return LAZ_TRUE;
}

static BOOL rgb14_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Rgb14v3 *r = (Rgb14v3 *)self;
    U64 offset = 0;
    U32 c;

    if (!layer_create(&r->rgb)) return LAZ_FALSE;
    if (!ensure_bytes(&r->bytes, &r->num_bytes_allocated, r->rgb.num_bytes))
        return LAZ_FALSE;

    layer_load(&r->rgb, self->dec->stream, r->bytes, &offset);

    for (c = 0; c < 4; c++) r->contexts[c].unused = LAZ_TRUE;
    r->current_context = *context;      /* set by the POINT14 reader */
    return rgb14_create_and_init(r, r->current_context, item);
}

/*
 * Shared RGB body: decodes three 16-bit channels into out[0..2] against last[].
 * Used by both RGB14 and the RGB half of RGBNIR14, which are identical apart
 * from which models they draw from.
 */
static void rgb_decode(LazDecoder *dec, LazSymbolModel *m_byte_used,
                       LazSymbolModel *m_rgb_diff, U16 *out, const U16 *last)
{
    U8 corr;
    I32 diff = 0;
    U32 sym = laz_decode_symbol(dec, m_byte_used);

    if (sym & (1 << 0)) {
        corr = (U8)laz_decode_symbol(dec, &m_rgb_diff[0]);
        out[0] = (U16)U8_FOLD(corr + (last[0] & 255));
    } else {
        out[0] = last[0] & 0xFF;
    }
    if (sym & (1 << 1)) {
        corr = (U8)laz_decode_symbol(dec, &m_rgb_diff[1]);
        out[0] |= (U16)(((U16)U8_FOLD(corr + (last[0] >> 8))) << 8);
    } else {
        out[0] |= (last[0] & 0xFF00);
    }

    if (sym & (1 << 6)) {       /* green and blue differ from red */
        diff = (out[0] & 0x00FF) - (last[0] & 0x00FF);
        if (sym & (1 << 2)) {
            corr = (U8)laz_decode_symbol(dec, &m_rgb_diff[2]);
            out[1] = (U16)U8_FOLD(corr + U8_CLAMP(diff + (last[1] & 255)));
        } else {
            out[1] = last[1] & 0xFF;
        }
        if (sym & (1 << 4)) {
            corr = (U8)laz_decode_symbol(dec, &m_rgb_diff[4]);
            diff = (diff + ((out[1] & 0x00FF) - (last[1] & 0x00FF))) / 2;
            out[2] = (U16)U8_FOLD(corr + U8_CLAMP(diff + (last[2] & 255)));
        } else {
            out[2] = last[2] & 0xFF;
        }
        diff = (out[0] >> 8) - (last[0] >> 8);
        if (sym & (1 << 3)) {
            corr = (U8)laz_decode_symbol(dec, &m_rgb_diff[3]);
            out[1] |= (U16)(((U16)U8_FOLD(corr + U8_CLAMP(diff + (last[1] >> 8)))) << 8);
        } else {
            out[1] |= (last[1] & 0xFF00);
        }
        if (sym & (1 << 5)) {
            corr = (U8)laz_decode_symbol(dec, &m_rgb_diff[5]);
            diff = (diff + ((out[1] >> 8) - (last[1] >> 8))) / 2;
            out[2] |= (U16)(((U16)U8_FOLD(corr + U8_CLAMP(diff + (last[2] >> 8)))) << 8);
        } else {
            out[2] |= (last[2] & 0xFF00);
        }
    } else {
        out[1] = out[0];
        out[2] = out[0];
    }
}

static void rgb14_read(LazReadItem *self, U8 *item, U32 *context)
{
    Rgb14v3 *r = (Rgb14v3 *)self;
    U16 *last_item = r->contexts[r->current_context].last_item;

    if (r->current_context != *context) {
        r->current_context = *context;
        if (r->contexts[r->current_context].unused) {
            if (!rgb14_create_and_init(r, r->current_context, (const U8 *)last_item)) {
                self->alloc_failed = LAZ_TRUE;
                return;
            }
            if (!r->v4) last_item = r->contexts[r->current_context].last_item;
        }
        /* v4 refreshes last_item for any switch, v3 only for an unused context */
        if (r->v4) last_item = r->contexts[r->current_context].last_item;
    }

    if (r->rgb.changed) {
        Rgb14Context *c = &r->contexts[r->current_context];
        rgb_decode(&r->rgb.dec, &c->m_byte_used, c->m_rgb_diff, (U16 *)item, last_item);
        memcpy(last_item, item, 6);
    } else {
        memcpy(item, last_item, 6);
    }
}

static BOOL rgb14_overran(LazReadItem *self)
{
    return layer_overran(&((Rgb14v3 *)self)->rgb);
}

static void rgb14_destroy(LazReadItem *self)
{
    Rgb14v3 *r = (Rgb14v3 *)self;
    U32 ci, i;
    for (ci = 0; ci < 4; ci++) {
        if (!r->contexts[ci].created) continue;
        laz_symbol_model_free(&r->contexts[ci].m_byte_used);
        for (i = 0; i < 6; i++) laz_symbol_model_free(&r->contexts[ci].m_rgb_diff[i]);
    }
    layer_free(&r->rgb);
    free(r->bytes);
}

static LazReadItem *rgb14_new(LazDecoder *dec, U32 selective, BOOL v4)
{
    Rgb14v3 *r = (Rgb14v3 *)calloc(1, sizeof(Rgb14v3));
    if (!r) return NULL;
    r->base.read = rgb14_read;
    r->base.init = rgb14_init;
    r->base.chunk_sizes = rgb14_chunk_sizes;
    r->base.overran = rgb14_overran;
    r->base.destroy = rgb14_destroy;
    r->base.dec = dec;
    r->v4 = v4;
    r->rgb.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_RGB);
    return (LazReadItem *)r;
}

LazReadItem *laz_readitem_v3_rgb14(LazDecoder *dec, U32 s) { return rgb14_new(dec, s, LAZ_FALSE); }
LazReadItem *laz_readitem_v4_rgb14(LazDecoder *dec, U32 s) { return rgb14_new(dec, s, LAZ_TRUE); }

/* ======================================================= RGBNIR14 v3/v4 == */

typedef struct {
    BOOL unused;
    BOOL created_rgb;
    BOOL created_nir;
    U16 last_item[4];
    LazSymbolModel m_rgb_bytes_used;
    LazSymbolModel m_rgb_diff[6];
    LazSymbolModel m_nir_bytes_used;
    LazSymbolModel m_nir_diff[2];
} RgbNir14Context;

typedef struct {
    LazReadItem base;
    BOOL v4;
    Layer rgb, nir;
    U8 *bytes;
    U64 num_bytes_allocated;
    U32 current_context;
    RgbNir14Context contexts[4];
} RgbNir14v3;

static BOOL rgbnir14_create_and_init(RgbNir14v3 *r, U32 context, const U8 *item)
{
    RgbNir14Context *c = &r->contexts[context];
    U32 i;

    if (r->rgb.requested) {
        if (!c->created_rgb) {
            laz_symbol_model_setup(&c->m_rgb_bytes_used, 128, LAZ_FALSE);
            for (i = 0; i < 6; i++) laz_symbol_model_setup(&c->m_rgb_diff[i], 256, LAZ_FALSE);
            c->created_rgb = LAZ_TRUE;
        }
        if (!laz_symbol_model_init(&c->m_rgb_bytes_used, NULL)) return LAZ_FALSE;
        for (i = 0; i < 6; i++) {
            if (!laz_symbol_model_init(&c->m_rgb_diff[i], NULL)) return LAZ_FALSE;
        }
    }
    if (r->nir.requested) {
        if (!c->created_nir) {
            laz_symbol_model_setup(&c->m_nir_bytes_used, 4, LAZ_FALSE);
            for (i = 0; i < 2; i++) laz_symbol_model_setup(&c->m_nir_diff[i], 256, LAZ_FALSE);
            c->created_nir = LAZ_TRUE;
        }
        if (!laz_symbol_model_init(&c->m_nir_bytes_used, NULL)) return LAZ_FALSE;
        for (i = 0; i < 2; i++) {
            if (!laz_symbol_model_init(&c->m_nir_diff[i], NULL)) return LAZ_FALSE;
        }
    }

    memcpy(c->last_item, item, 8);
    c->unused = LAZ_FALSE;
    return LAZ_TRUE;
}

static BOOL rgbnir14_chunk_sizes(LazReadItem *self)
{
    RgbNir14v3 *r = (RgbNir14v3 *)self;
    r->rgb.num_bytes = laz_stream_get32(self->dec->stream);
    r->nir.num_bytes = laz_stream_get32(self->dec->stream);
    return LAZ_TRUE;
}

static BOOL rgbnir14_init(LazReadItem *self, const U8 *item, U32 *context)
{
    RgbNir14v3 *r = (RgbNir14v3 *)self;
    U64 num_bytes = 0, offset = 0;
    U32 c;

    if (!layer_create(&r->rgb) || !layer_create(&r->nir)) return LAZ_FALSE;
    if (r->rgb.requested) num_bytes += r->rgb.num_bytes;
    if (r->nir.requested) num_bytes += r->nir.num_bytes;
    if (!ensure_bytes(&r->bytes, &r->num_bytes_allocated, num_bytes)) return LAZ_FALSE;

    layer_load(&r->rgb, self->dec->stream, r->bytes, &offset);
    layer_load(&r->nir, self->dec->stream, r->bytes, &offset);

    for (c = 0; c < 4; c++) r->contexts[c].unused = LAZ_TRUE;
    r->current_context = *context;
    return rgbnir14_create_and_init(r, r->current_context, item);
}

static void rgbnir14_read(LazReadItem *self, U8 *item, U32 *context)
{
    RgbNir14v3 *r = (RgbNir14v3 *)self;
    U16 *last_item = r->contexts[r->current_context].last_item;
    RgbNir14Context *c;

    if (r->current_context != *context) {
        r->current_context = *context;
        if (r->contexts[r->current_context].unused) {
            if (!rgbnir14_create_and_init(r, r->current_context, (const U8 *)last_item)) {
                self->alloc_failed = LAZ_TRUE;
                return;
            }
            if (!r->v4) last_item = r->contexts[r->current_context].last_item;
        }
        if (r->v4) last_item = r->contexts[r->current_context].last_item;
    }
    c = &r->contexts[r->current_context];

    if (r->rgb.changed) {
        rgb_decode(&r->rgb.dec, &c->m_rgb_bytes_used, c->m_rgb_diff,
                   (U16 *)item, last_item);
        memcpy(last_item, item, 6);
    } else {
        memcpy(item, last_item, 6);
    }

    if (r->nir.changed) {
        U8 corr;
        U32 sym = laz_decode_symbol(&r->nir.dec, &c->m_nir_bytes_used);
        if (sym & (1 << 0)) {
            corr = (U8)laz_decode_symbol(&r->nir.dec, &c->m_nir_diff[0]);
            ((U16 *)item)[3] = (U16)U8_FOLD(corr + (last_item[3] & 255));
        } else {
            ((U16 *)item)[3] = last_item[3] & 0xFF;
        }
        if (sym & (1 << 1)) {
            corr = (U8)laz_decode_symbol(&r->nir.dec, &c->m_nir_diff[1]);
            ((U16 *)item)[3] |= (U16)(((U16)U8_FOLD(corr + (last_item[3] >> 8))) << 8);
        } else {
            ((U16 *)item)[3] |= (last_item[3] & 0xFF00);
        }
        /* v4 writes through the context rather than the cached pointer */
        if (r->v4) c->last_item[3] = ((U16 *)item)[3];
        else last_item[3] = ((U16 *)item)[3];
    } else {
        ((U16 *)item)[3] = r->v4 ? c->last_item[3] : last_item[3];
    }
}

static BOOL rgbnir14_overran(LazReadItem *self)
{
    RgbNir14v3 *r = (RgbNir14v3 *)self;
    return layer_overran(&r->rgb) || layer_overran(&r->nir);
}

static void rgbnir14_destroy(LazReadItem *self)
{
    RgbNir14v3 *r = (RgbNir14v3 *)self;
    U32 ci, i;
    for (ci = 0; ci < 4; ci++) {
        RgbNir14Context *c = &r->contexts[ci];
        if (c->created_rgb) {
            laz_symbol_model_free(&c->m_rgb_bytes_used);
            for (i = 0; i < 6; i++) laz_symbol_model_free(&c->m_rgb_diff[i]);
        }
        if (c->created_nir) {
            laz_symbol_model_free(&c->m_nir_bytes_used);
            for (i = 0; i < 2; i++) laz_symbol_model_free(&c->m_nir_diff[i]);
        }
    }
    layer_free(&r->rgb);
    layer_free(&r->nir);
    free(r->bytes);
}

static LazReadItem *rgbnir14_new(LazDecoder *dec, U32 selective, BOOL v4)
{
    RgbNir14v3 *r = (RgbNir14v3 *)calloc(1, sizeof(RgbNir14v3));
    if (!r) return NULL;
    r->base.read = rgbnir14_read;
    r->base.init = rgbnir14_init;
    r->base.chunk_sizes = rgbnir14_chunk_sizes;
    r->base.overran = rgbnir14_overran;
    r->base.destroy = rgbnir14_destroy;
    r->base.dec = dec;
    r->v4 = v4;
    r->rgb.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_RGB);
    r->nir.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_NIR);
    return (LazReadItem *)r;
}

LazReadItem *laz_readitem_v3_rgbnir14(LazDecoder *dec, U32 s) { return rgbnir14_new(dec, s, LAZ_FALSE); }
LazReadItem *laz_readitem_v4_rgbnir14(LazDecoder *dec, U32 s) { return rgbnir14_new(dec, s, LAZ_TRUE); }

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
    LazReadItem base;
    BOOL v4;
    Layer wavepacket;
    U8 *bytes;
    U64 num_bytes_allocated;
    U32 current_context;
    Wave14Context contexts[4];
} Wave14v3;

static BOOL wave14_create_and_init(Wave14v3 *r, U32 context, const U8 *item)
{
    Wave14Context *c = &r->contexts[context];
    U32 i;

    if (r->wavepacket.requested) {
        if (!c->created) {
            laz_symbol_model_setup(&c->m_packet_index, 256, LAZ_FALSE);
            for (i = 0; i < 4; i++) laz_symbol_model_setup(&c->m_offset_diff[i], 4, LAZ_FALSE);
            laz_ic_setup_dec(&c->ic_offset_diff, &r->wavepacket.dec, 32, 1, 8, 0);
            laz_ic_setup_dec(&c->ic_packet_size, &r->wavepacket.dec, 32, 1, 8, 0);
            laz_ic_setup_dec(&c->ic_return_point, &r->wavepacket.dec, 32, 1, 8, 0);
            laz_ic_setup_dec(&c->ic_xyz, &r->wavepacket.dec, 32, 3, 8, 0);
            c->created = LAZ_TRUE;
        }
        if (!laz_symbol_model_init(&c->m_packet_index, NULL)) return LAZ_FALSE;
        for (i = 0; i < 4; i++) {
            if (!laz_symbol_model_init(&c->m_offset_diff[i], NULL)) return LAZ_FALSE;
        }
        if (!laz_ic_init_decompressor(&c->ic_offset_diff) ||
            !laz_ic_init_decompressor(&c->ic_packet_size) ||
            !laz_ic_init_decompressor(&c->ic_return_point) ||
            !laz_ic_init_decompressor(&c->ic_xyz))
            return LAZ_FALSE;
    }

    c->last_diff_32 = 0;
    c->sym_last_offset_diff = 0;
    memcpy(c->last_item, item, 29);
    c->unused = LAZ_FALSE;
    return LAZ_TRUE;
}

static BOOL wave14_chunk_sizes(LazReadItem *self)
{
    Wave14v3 *r = (Wave14v3 *)self;
    r->wavepacket.num_bytes = laz_stream_get32(self->dec->stream);
    return LAZ_TRUE;
}

static BOOL wave14_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Wave14v3 *r = (Wave14v3 *)self;
    U64 offset = 0;
    U32 c;

    if (!layer_create(&r->wavepacket)) return LAZ_FALSE;
    if (!ensure_bytes(&r->bytes, &r->num_bytes_allocated, r->wavepacket.num_bytes))
        return LAZ_FALSE;

    layer_load(&r->wavepacket, self->dec->stream, r->bytes, &offset);

    for (c = 0; c < 4; c++) r->contexts[c].unused = LAZ_TRUE;
    r->current_context = *context;
    return wave14_create_and_init(r, r->current_context, item);
}

static void wave14_read(LazReadItem *self, U8 *item, U32 *context)
{
    Wave14v3 *r = (Wave14v3 *)self;
    U8 *last_item = r->contexts[r->current_context].last_item;
    Wave14Context *c;
    U64 offset;
    U32 packet_size;
    I32 return_point, x, y, z;

    if (r->current_context != *context) {
        r->current_context = *context;
        if (r->contexts[r->current_context].unused) {
            if (!wave14_create_and_init(r, r->current_context, last_item)) {
                self->alloc_failed = LAZ_TRUE;
                return;
            }
            if (!r->v4) last_item = r->contexts[r->current_context].last_item;
        }
        if (r->v4) last_item = r->contexts[r->current_context].last_item;
    }
    c = &r->contexts[r->current_context];

    if (!r->wavepacket.changed) return;

    item[0] = (U8)laz_decode_symbol(&r->wavepacket.dec, &c->m_packet_index);

    /* the 28 predicted bytes follow the packet index */
    {
        const U8 *lw = last_item + 1;
        U64 last_offset = (U64)laz_wp_get32(lw) | ((U64)laz_wp_get32(lw + 4) << 32);
        U32 last_packet_size = laz_wp_get32(lw + 8);
        I32 last_return_point = (I32)laz_wp_get32(lw + 12);
        I32 last_x = (I32)laz_wp_get32(lw + 16);
        I32 last_y = (I32)laz_wp_get32(lw + 20);
        I32 last_z = (I32)laz_wp_get32(lw + 24);

        c->sym_last_offset_diff = laz_decode_symbol(&r->wavepacket.dec,
            &c->m_offset_diff[c->sym_last_offset_diff]);

        if (c->sym_last_offset_diff == 0) {
            offset = last_offset;
        } else if (c->sym_last_offset_diff == 1) {
            offset = last_offset + last_packet_size;
        } else if (c->sym_last_offset_diff == 2) {
            c->last_diff_32 = laz_ic_decompress(&c->ic_offset_diff, c->last_diff_32, 0);
            offset = last_offset + c->last_diff_32;
        } else {
            offset = laz_read_int64(&r->wavepacket.dec);
        }

        packet_size = (U32)laz_ic_decompress(&c->ic_packet_size, (I32)last_packet_size, 0);
        return_point = laz_ic_decompress(&c->ic_return_point, last_return_point, 0);
        x = laz_ic_decompress(&c->ic_xyz, last_x, 0);
        y = laz_ic_decompress(&c->ic_xyz, last_y, 1);
        z = laz_ic_decompress(&c->ic_xyz, last_z, 2);
    }

    laz_wp_put32((U32)(offset & 0xFFFFFFFF), item + 1);
    laz_wp_put32((U32)(offset >> 32), item + 5);
    laz_wp_put32(packet_size, item + 9);
    laz_wp_put32((U32)return_point, item + 13);
    laz_wp_put32((U32)x, item + 17);
    laz_wp_put32((U32)y, item + 21);
    laz_wp_put32((U32)z, item + 25);

    memcpy(last_item, item, 29);
}

static BOOL wave14_overran(LazReadItem *self)
{
    return layer_overran(&((Wave14v3 *)self)->wavepacket);
}

static void wave14_destroy(LazReadItem *self)
{
    Wave14v3 *r = (Wave14v3 *)self;
    U32 ci, i;
    for (ci = 0; ci < 4; ci++) {
        Wave14Context *c = &r->contexts[ci];
        if (!c->created) continue;
        laz_symbol_model_free(&c->m_packet_index);
        for (i = 0; i < 4; i++) laz_symbol_model_free(&c->m_offset_diff[i]);
        laz_ic_free(&c->ic_offset_diff);
        laz_ic_free(&c->ic_packet_size);
        laz_ic_free(&c->ic_return_point);
        laz_ic_free(&c->ic_xyz);
    }
    layer_free(&r->wavepacket);
    free(r->bytes);
}

static LazReadItem *wave14_new(LazDecoder *dec, U32 selective, BOOL v4)
{
    Wave14v3 *r = (Wave14v3 *)calloc(1, sizeof(Wave14v3));
    if (!r) return NULL;
    r->base.read = wave14_read;
    r->base.init = wave14_init;
    r->base.chunk_sizes = wave14_chunk_sizes;
    r->base.overran = wave14_overran;
    r->base.destroy = wave14_destroy;
    r->base.dec = dec;
    r->v4 = v4;
    r->wavepacket.requested = !!(selective & LAZ_DECOMPRESS_SELECTIVE_WAVEPACKET);
    return (LazReadItem *)r;
}

LazReadItem *laz_readitem_v3_wavepacket14(LazDecoder *dec, U32 s) { return wave14_new(dec, s, LAZ_FALSE); }
LazReadItem *laz_readitem_v4_wavepacket14(LazDecoder *dec, U32 s) { return wave14_new(dec, s, LAZ_TRUE); }

/* ========================================================= BYTE14 v3/v4 == */

typedef struct {
    BOOL unused;
    BOOL created;
    U8 *last_item;          /* [number] */
    LazSymbolModel *m_bytes; /* [number] */
} Byte14Context;

typedef struct {
    LazReadItem base;
    BOOL v4;
    U32 number;
    Layer *layers;          /* [number], one per extra byte */
    U8 *bytes;
    U64 num_bytes_allocated;
    U32 current_context;
    Byte14Context contexts[4];
} Byte14v3;

static BOOL byte14_create_and_init(Byte14v3 *r, U32 context, const U8 *item)
{
    Byte14Context *c = &r->contexts[context];
    U32 i;

    if (!c->created) {
        /* m_bytes may have survived a call that failed after it, so it is
         * allocated only once however often this is retried */
        if (!c->m_bytes) {
            c->m_bytes = laz_symbol_models_new(r->number, 256, LAZ_FALSE);
            if (!c->m_bytes) return LAZ_FALSE;
        }
        c->last_item = (U8 *)calloc(r->number ? r->number : 1, 1);
        if (!c->last_item) return LAZ_FALSE;
        c->created = LAZ_TRUE;
    }

    for (i = 0; i < r->number; i++) {
        if (!laz_symbol_model_init(&c->m_bytes[i], NULL)) return LAZ_FALSE;
    }
    memcpy(c->last_item, item, r->number);
    c->unused = LAZ_FALSE;
    return LAZ_TRUE;
}

static BOOL byte14_chunk_sizes(LazReadItem *self)
{
    Byte14v3 *r = (Byte14v3 *)self;
    U32 i;
    for (i = 0; i < r->number; i++)
        r->layers[i].num_bytes = laz_stream_get32(self->dec->stream);
    return LAZ_TRUE;
}

static BOOL byte14_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Byte14v3 *r = (Byte14v3 *)self;
    U64 num_bytes = 0, offset = 0;
    U32 i, c;

    for (i = 0; i < r->number; i++) {
        if (!layer_create(&r->layers[i])) return LAZ_FALSE;
        if (r->layers[i].requested) num_bytes += r->layers[i].num_bytes;
    }
    if (!ensure_bytes(&r->bytes, &r->num_bytes_allocated, num_bytes)) return LAZ_FALSE;

    for (i = 0; i < r->number; i++)
        layer_load(&r->layers[i], self->dec->stream, r->bytes, &offset);

    for (c = 0; c < 4; c++) r->contexts[c].unused = LAZ_TRUE;
    r->current_context = *context;
    return byte14_create_and_init(r, r->current_context, item);
}

static void byte14_read(LazReadItem *self, U8 *item, U32 *context)
{
    Byte14v3 *r = (Byte14v3 *)self;
    U8 *last_item = r->contexts[r->current_context].last_item;
    Byte14Context *c;
    U32 i;

    if (r->current_context != *context) {
        r->current_context = *context;
        if (r->contexts[r->current_context].unused) {
            if (!byte14_create_and_init(r, r->current_context, last_item)) {
                self->alloc_failed = LAZ_TRUE;
                return;
            }
            if (!r->v4) last_item = r->contexts[r->current_context].last_item;
        }
        if (r->v4) last_item = r->contexts[r->current_context].last_item;
    }
    c = &r->contexts[r->current_context];

    for (i = 0; i < r->number; i++) {
        if (r->layers[i].changed) {
            I32 value = last_item[i] +
                (I32)laz_decode_symbol(&r->layers[i].dec, &c->m_bytes[i]);
            item[i] = (U8)U8_FOLD(value);
            last_item[i] = item[i];
        } else {
            item[i] = last_item[i];
        }
    }
}

static BOOL byte14_overran(LazReadItem *self)
{
    Byte14v3 *r = (Byte14v3 *)self;
    U32 i;
    for (i = 0; i < r->number; i++) if (layer_overran(&r->layers[i])) return LAZ_TRUE;
    return LAZ_FALSE;
}

static void byte14_destroy(LazReadItem *self)
{
    Byte14v3 *r = (Byte14v3 *)self;
    U32 ci, i;
    /* not gated on c->created: a context whose first allocation succeeded and
     * whose second did not is not created, but still owns memory */
    for (ci = 0; ci < 4; ci++) {
        Byte14Context *c = &r->contexts[ci];
        laz_symbol_models_free(c->m_bytes, r->number);
        free(c->last_item);
    }
    if (r->layers) {
        for (i = 0; i < r->number; i++) layer_free(&r->layers[i]);
        free(r->layers);
    }
    free(r->bytes);
}

static LazReadItem *byte14_new(LazDecoder *dec, U32 number, U32 selective, BOOL v4)
{
    Byte14v3 *r = (Byte14v3 *)calloc(1, sizeof(Byte14v3));
    U32 i;
    if (!r) return NULL;
    r->base.read = byte14_read;
    r->base.init = byte14_init;
    r->base.chunk_sizes = byte14_chunk_sizes;
    r->base.overran = byte14_overran;
    r->base.destroy = byte14_destroy;
    r->base.dec = dec;
    r->v4 = v4;
    r->number = number;
    r->layers = (Layer *)calloc(number ? number : 1, sizeof(Layer));
    if (!r->layers) { free(r); return NULL; }
    for (i = 0; i < number; i++) {
        /* only the first 16 extra bytes have selective-decompression bits */
        r->layers[i].requested = (i > 15) ? LAZ_TRUE
            : !!(selective & (LAZ_DECOMPRESS_SELECTIVE_BYTE0 << i));
    }
    return (LazReadItem *)r;
}

LazReadItem *laz_readitem_v3_byte14(LazDecoder *dec, U32 n, U32 s)
{ return byte14_new(dec, n, s, LAZ_FALSE); }
LazReadItem *laz_readitem_v4_byte14(LazDecoder *dec, U32 n, U32 s)
{ return byte14_new(dec, n, s, LAZ_TRUE); }
