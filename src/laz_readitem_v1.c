/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * LAZ v1 compressed item readers -- ported from LASzip's
 * lasreaditemcompressed_v1.cpp.
 *
 * This is the original LASzip 1.0 encoding for point formats 0-5. It is
 * superseded by v2 but still appears in older archives, so a reader claiming
 * parity has to handle it. WAVEPACKET13 only ever had a v1 encoding, so that
 * reader is used by point formats 4, 5, 9 and 10 regardless of file vintage.
 */
#include "laz_readitem.h"

/* ======================================================== POINT10 v1 ===== */

typedef struct {
    LazReadItem base;
    LazIntCompressor ic_dx, ic_dy, ic_z;
    LazIntCompressor ic_intensity, ic_scan_angle_rank, ic_point_source_ID;
    LazSymbolModel m_changed_values;
    LazSymbolModel m_bit_byte[256], m_classification[256], m_user_data[256];
    U8 created_bit_byte[256], created_classification[256], created_user_data[256];
    I32 last_x_diff[3];
    I32 last_y_diff[3];
    I32 last_incr;
    U8 last_item[20];
} Point10v1;

static BOOL p10v1_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Point10v1 *r = (Point10v1 *)self;
    (void)context;

    r->last_x_diff[0] = r->last_x_diff[1] = r->last_x_diff[2] = 0;
    r->last_y_diff[0] = r->last_y_diff[1] = r->last_y_diff[2] = 0;
    r->last_incr = 0;

    if (!laz_ic_init_decompressor(&r->ic_dx) ||
        !laz_ic_init_decompressor(&r->ic_dy) ||
        !laz_ic_init_decompressor(&r->ic_z) ||
        !laz_ic_init_decompressor(&r->ic_intensity) ||
        !laz_ic_init_decompressor(&r->ic_scan_angle_rank) ||
        !laz_ic_init_decompressor(&r->ic_point_source_ID) ||
        !laz_symbol_model_init(&r->m_changed_values, NULL) ||
        !laz_bank_reinit(r->m_bit_byte, r->created_bit_byte, 256) ||
        !laz_bank_reinit(r->m_classification, r->created_classification, 256) ||
        !laz_bank_reinit(r->m_user_data, r->created_user_data, 256))
        return LAZ_FALSE;

    memcpy(r->last_item, item, 20);
    return LAZ_TRUE;
}

static void p10v1_read(LazReadItem *self, U8 *item, U32 *context)
{
    Point10v1 *r = (Point10v1 *)self;
    U8 *li = r->last_item;
    I32 median_x, median_y, x_diff, y_diff, changed_values;
    U32 k_bits;
    LazSymbolModel *m;

    (void)context;
    median_x = laz_median3(r->last_x_diff);
    median_y = laz_median3(r->last_y_diff);

    /* x, y, z first in v1 -- the opposite order from v2 */
    x_diff = laz_ic_decompress(&r->ic_dx, median_x, 0);
    P10_X(li) += x_diff;
    k_bits = r->ic_dx.k;                        /* corrector bits switch contexts */
    y_diff = laz_ic_decompress(&r->ic_dy, median_y, (k_bits < 19 ? k_bits : 19));
    P10_Y(li) += y_diff;
    k_bits = (k_bits + r->ic_dy.k) / 2;
    P10_Z(li) = laz_ic_decompress(&r->ic_z, P10_Z(li), (k_bits < 19 ? k_bits : 19));

    changed_values = (I32)laz_decode_symbol(self->dec, &r->m_changed_values);

    if (changed_values) {
        if (changed_values & 32) {
            P10_INTENSITY(li) = (U16)laz_ic_decompress(&r->ic_intensity, P10_INTENSITY(li), 0);
        }
        if (changed_values & 16) {
            m = laz_bank_get(r->m_bit_byte, r->created_bit_byte, li[14]);
            if (!m) { self->alloc_failed = LAZ_TRUE; return; }
            li[14] = (U8)laz_decode_symbol(self->dec, m);
        }
        if (changed_values & 8) {
            m = laz_bank_get(r->m_classification, r->created_classification, li[15]);
            if (!m) { self->alloc_failed = LAZ_TRUE; return; }
            li[15] = (U8)laz_decode_symbol(self->dec, m);
        }
        if (changed_values & 4) {
            li[16] = (U8)laz_ic_decompress(&r->ic_scan_angle_rank, li[16], k_bits < 3);
        }
        if (changed_values & 2) {
            m = laz_bank_get(r->m_user_data, r->created_user_data, li[17]);
            if (!m) { self->alloc_failed = LAZ_TRUE; return; }
            li[17] = (U8)laz_decode_symbol(self->dec, m);
        }
        if (changed_values & 1) {
            P10_POINT_SOURCE_ID(li) =
                (U16)laz_ic_decompress(&r->ic_point_source_ID, P10_POINT_SOURCE_ID(li), 0);
        }
    }

    r->last_x_diff[r->last_incr] = x_diff;
    r->last_y_diff[r->last_incr] = y_diff;
    r->last_incr++;
    if (r->last_incr > 2) r->last_incr = 0;

    memcpy(item, li, 20);
}

static void p10v1_destroy(LazReadItem *self)
{
    Point10v1 *r = (Point10v1 *)self;
    laz_ic_free(&r->ic_dx);
    laz_ic_free(&r->ic_dy);
    laz_ic_free(&r->ic_z);
    laz_ic_free(&r->ic_intensity);
    laz_ic_free(&r->ic_scan_angle_rank);
    laz_ic_free(&r->ic_point_source_ID);
    laz_symbol_model_free(&r->m_changed_values);
    laz_bank_free(r->m_bit_byte, 256);
    laz_bank_free(r->m_classification, 256);
    laz_bank_free(r->m_user_data, 256);
}

LazReadItem *laz_readitem_v1_point10(LazDecoder *dec)
{
    Point10v1 *r = (Point10v1 *)calloc(1, sizeof(Point10v1));
    if (!r) return NULL;
    r->base.read = p10v1_read;
    r->base.init = p10v1_init;
    r->base.destroy = p10v1_destroy;
    r->base.dec = dec;

    laz_ic_setup_dec(&r->ic_dx, dec, 32, 1, 8, 0);
    laz_ic_setup_dec(&r->ic_dy, dec, 32, 20, 8, 0);
    laz_ic_setup_dec(&r->ic_z, dec, 32, 20, 8, 0);
    laz_ic_setup_dec(&r->ic_intensity, dec, 16, 1, 8, 0);
    laz_ic_setup_dec(&r->ic_scan_angle_rank, dec, 8, 2, 8, 0);
    laz_ic_setup_dec(&r->ic_point_source_ID, dec, 16, 1, 8, 0);
    laz_symbol_model_setup(&r->m_changed_values, 64, LAZ_FALSE);
    laz_bank_setup(r->m_bit_byte, r->created_bit_byte, 256, 256, LAZ_FALSE);
    laz_bank_setup(r->m_classification, r->created_classification, 256, 256, LAZ_FALSE);
    laz_bank_setup(r->m_user_data, r->created_user_data, 256, 256, LAZ_FALSE);
    return (LazReadItem *)r;
}

/* ====================================================== GPSTIME11 v1 ===== */

#define LASZIP_GPSTIME_MULTIMAX 512

typedef struct {
    LazReadItem base;
    LazSymbolModel m_gpstime_multi;
    LazSymbolModel m_gpstime_0diff;
    LazIntCompressor ic_gpstime;
    I32 last_gpstime_diff;
    I32 multi_extreme_counter;
    U64 last_gpstime;
} Gpstime11v1;

static BOOL gps11v1_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Gpstime11v1 *r = (Gpstime11v1 *)self;
    (void)context;
    r->last_gpstime_diff = 0;
    r->multi_extreme_counter = 0;
    if (!laz_symbol_model_init(&r->m_gpstime_multi, NULL) ||
        !laz_symbol_model_init(&r->m_gpstime_0diff, NULL) ||
        !laz_ic_init_decompressor(&r->ic_gpstime))
        return LAZ_FALSE;
    memcpy(&r->last_gpstime, item, 8);
    return LAZ_TRUE;
}

static void gps11v1_read(LazReadItem *self, U8 *item, U32 *context)
{
    Gpstime11v1 *r = (Gpstime11v1 *)self;
    I32 multi;

    (void)context;
    if (r->last_gpstime_diff == 0) {
        multi = (I32)laz_decode_symbol(self->dec, &r->m_gpstime_0diff);
        if (multi == 1) {                       /* difference fits in 32 bits */
            r->last_gpstime_diff = laz_ic_decompress(&r->ic_gpstime, 0, 0);
            r->last_gpstime = (U64)((I64)r->last_gpstime + r->last_gpstime_diff);
        } else if (multi == 2) {                /* difference is huge */
            r->last_gpstime = laz_read_int64(self->dec);
        }
    } else {
        multi = (I32)laz_decode_symbol(self->dec, &r->m_gpstime_multi);

        if (multi < LASZIP_GPSTIME_MULTIMAX - 2) {
            I32 gpstime_diff;
            if (multi == 1) {
                gpstime_diff = laz_ic_decompress(&r->ic_gpstime, r->last_gpstime_diff, 1);
                r->last_gpstime_diff = gpstime_diff;
                r->multi_extreme_counter = 0;
            } else if (multi == 0) {
                gpstime_diff = laz_ic_decompress(&r->ic_gpstime, r->last_gpstime_diff / 4, 2);
                r->multi_extreme_counter++;
                if (r->multi_extreme_counter > 3) {
                    r->last_gpstime_diff = gpstime_diff;
                    r->multi_extreme_counter = 0;
                }
            } else if (multi < 10) {
                gpstime_diff = laz_ic_decompress(&r->ic_gpstime, multi * r->last_gpstime_diff, 3);
            } else if (multi < 50) {
                gpstime_diff = laz_ic_decompress(&r->ic_gpstime, multi * r->last_gpstime_diff, 4);
            } else {
                gpstime_diff = laz_ic_decompress(&r->ic_gpstime, multi * r->last_gpstime_diff, 5);
                if (multi == LASZIP_GPSTIME_MULTIMAX - 3) {
                    r->multi_extreme_counter++;
                    if (r->multi_extreme_counter > 3) {
                        r->last_gpstime_diff = gpstime_diff;
                        r->multi_extreme_counter = 0;
                    }
                }
            }
            r->last_gpstime = (U64)((I64)r->last_gpstime + gpstime_diff);
        } else if (multi < LASZIP_GPSTIME_MULTIMAX - 1) {
            r->last_gpstime = laz_read_int64(self->dec);
        }
    }
    memcpy(item, &r->last_gpstime, 8);
}

static void gps11v1_destroy(LazReadItem *self)
{
    Gpstime11v1 *r = (Gpstime11v1 *)self;
    laz_symbol_model_free(&r->m_gpstime_multi);
    laz_symbol_model_free(&r->m_gpstime_0diff);
    laz_ic_free(&r->ic_gpstime);
}

LazReadItem *laz_readitem_v1_gpstime11(LazDecoder *dec)
{
    Gpstime11v1 *r = (Gpstime11v1 *)calloc(1, sizeof(Gpstime11v1));
    if (!r) return NULL;
    r->base.read = gps11v1_read;
    r->base.init = gps11v1_init;
    r->base.destroy = gps11v1_destroy;
    r->base.dec = dec;
    laz_symbol_model_setup(&r->m_gpstime_multi, LASZIP_GPSTIME_MULTIMAX, LAZ_FALSE);
    laz_symbol_model_setup(&r->m_gpstime_0diff, 3, LAZ_FALSE);
    laz_ic_setup_dec(&r->ic_gpstime, dec, 32, 6, 8, 0);
    return (LazReadItem *)r;
}

/* ========================================================== RGB12 v1 ===== */

typedef struct {
    LazReadItem base;
    LazSymbolModel m_byte_used;
    LazIntCompressor ic_rgb;
    U16 last_item[3];
} Rgb12v1;

static BOOL rgb12v1_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Rgb12v1 *r = (Rgb12v1 *)self;
    (void)context;
    if (!laz_symbol_model_init(&r->m_byte_used, NULL) ||
        !laz_ic_init_decompressor(&r->ic_rgb))
        return LAZ_FALSE;
    memcpy(r->last_item, item, 6);
    return LAZ_TRUE;
}

static void rgb12v1_read(LazReadItem *self, U8 *item, U32 *context)
{
    Rgb12v1 *r = (Rgb12v1 *)self;
    U16 *out = (U16 *)item;
    U16 *last = r->last_item;
    U32 sym;

    (void)context;
    sym = laz_decode_symbol(self->dec, &r->m_byte_used);

    /* each of the six bytes of r,g,b is coded independently under its own context */
    if (sym & (1 << 0)) out[0] = (U16)laz_ic_decompress(&r->ic_rgb, last[0] & 255, 0);
    else out[0] = (U16)(last[0] & 0xFF);
    if (sym & (1 << 1)) out[0] |= (U16)(((U16)laz_ic_decompress(&r->ic_rgb, last[0] >> 8, 1)) << 8);
    else out[0] |= (last[0] & 0xFF00);
    if (sym & (1 << 2)) out[1] = (U16)laz_ic_decompress(&r->ic_rgb, last[1] & 255, 2);
    else out[1] = (U16)(last[1] & 0xFF);
    if (sym & (1 << 3)) out[1] |= (U16)(((U16)laz_ic_decompress(&r->ic_rgb, last[1] >> 8, 3)) << 8);
    else out[1] |= (last[1] & 0xFF00);
    if (sym & (1 << 4)) out[2] = (U16)laz_ic_decompress(&r->ic_rgb, last[2] & 255, 4);
    else out[2] = (U16)(last[2] & 0xFF);
    if (sym & (1 << 5)) out[2] |= (U16)(((U16)laz_ic_decompress(&r->ic_rgb, last[2] >> 8, 5)) << 8);
    else out[2] |= (last[2] & 0xFF00);

    memcpy(r->last_item, item, 6);
}

static void rgb12v1_destroy(LazReadItem *self)
{
    Rgb12v1 *r = (Rgb12v1 *)self;
    laz_symbol_model_free(&r->m_byte_used);
    laz_ic_free(&r->ic_rgb);
}

LazReadItem *laz_readitem_v1_rgb12(LazDecoder *dec)
{
    Rgb12v1 *r = (Rgb12v1 *)calloc(1, sizeof(Rgb12v1));
    if (!r) return NULL;
    r->base.read = rgb12v1_read;
    r->base.init = rgb12v1_init;
    r->base.destroy = rgb12v1_destroy;
    r->base.dec = dec;
    laz_symbol_model_setup(&r->m_byte_used, 64, LAZ_FALSE);
    laz_ic_setup_dec(&r->ic_rgb, dec, 8, 6, 8, 0);
    return (LazReadItem *)r;
}

/* =========================================================== BYTE v1 ===== */

typedef struct {
    LazReadItem base;
    U32 number;
    LazIntCompressor ic_byte;
    U8 *last_item;
} Bytev1;

static BOOL bytev1_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Bytev1 *r = (Bytev1 *)self;
    (void)context;
    if (!laz_ic_init_decompressor(&r->ic_byte)) return LAZ_FALSE;
    memcpy(r->last_item, item, r->number);
    return LAZ_TRUE;
}

static void bytev1_read(LazReadItem *self, U8 *item, U32 *context)
{
    Bytev1 *r = (Bytev1 *)self;
    U32 i;
    (void)context;
    for (i = 0; i < r->number; i++) {
        item[i] = (U8)laz_ic_decompress(&r->ic_byte, r->last_item[i], i);
    }
    memcpy(r->last_item, item, r->number);
}

static void bytev1_destroy(LazReadItem *self)
{
    Bytev1 *r = (Bytev1 *)self;
    laz_ic_free(&r->ic_byte);
    free(r->last_item);
}

LazReadItem *laz_readitem_v1_byte(LazDecoder *dec, U32 number)
{
    Bytev1 *r = (Bytev1 *)calloc(1, sizeof(Bytev1));
    if (!r) return NULL;
    r->base.read = bytev1_read;
    r->base.init = bytev1_init;
    r->base.destroy = bytev1_destroy;
    r->base.dec = dec;
    r->number = number;
    laz_ic_setup_dec(&r->ic_byte, dec, 8, number, 8, 0);
    r->last_item = (U8 *)calloc(number ? number : 1, 1);
    if (!r->last_item) { free(r); return NULL; }
    return (LazReadItem *)r;
}

/* =================================================== WAVEPACKET13 v1 ===== */

/*
 * The 29-byte wavepacket item is [index:1][offset:8][size:4][return:4][x,y,z:12].
 * Only the trailing 28 bytes are predicted; the leading index byte gets its own
 * symbol model. See LazWavepacket13 in laz_item.h.
 */
typedef struct {
    LazReadItem base;
    LazSymbolModel m_packet_index;
    LazSymbolModel m_offset_diff[4];
    LazIntCompressor ic_offset_diff, ic_packet_size, ic_return_point, ic_xyz;
    I32 last_diff_32;
    U32 sym_last_offset_diff;
    U8 last_item[28];
} Wavepacket13v1;

static BOOL wp13v1_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Wavepacket13v1 *r = (Wavepacket13v1 *)self;
    U32 i;
    (void)context;

    r->last_diff_32 = 0;
    r->sym_last_offset_diff = 0;

    if (!laz_symbol_model_init(&r->m_packet_index, NULL)) return LAZ_FALSE;
    for (i = 0; i < 4; i++) {
        if (!laz_symbol_model_init(&r->m_offset_diff[i], NULL)) return LAZ_FALSE;
    }
    if (!laz_ic_init_decompressor(&r->ic_offset_diff) ||
        !laz_ic_init_decompressor(&r->ic_packet_size) ||
        !laz_ic_init_decompressor(&r->ic_return_point) ||
        !laz_ic_init_decompressor(&r->ic_xyz))
        return LAZ_FALSE;

    memcpy(r->last_item, item + 1, 28);   /* skip the packet index byte */
    return LAZ_TRUE;
}

static void wp13v1_read(LazReadItem *self, U8 *item, U32 *context)
{
    Wavepacket13v1 *r = (Wavepacket13v1 *)self;
    LazWavepacket13 cur, last;

    (void)context;
    item[0] = (U8)laz_decode_symbol(self->dec, &r->m_packet_index);
    item++;

    last = laz_wp_unpack(r->last_item);

    r->sym_last_offset_diff = laz_decode_symbol(self->dec,
                                                &r->m_offset_diff[r->sym_last_offset_diff]);

    if (r->sym_last_offset_diff == 0) {
        cur.offset = last.offset;
    } else if (r->sym_last_offset_diff == 1) {
        cur.offset = last.offset + last.packet_size;
    } else if (r->sym_last_offset_diff == 2) {
        r->last_diff_32 = laz_ic_decompress(&r->ic_offset_diff, r->last_diff_32, 0);
        cur.offset = last.offset + r->last_diff_32;
    } else {
        cur.offset = laz_read_int64(self->dec);
    }

    cur.packet_size = (U32)laz_ic_decompress(&r->ic_packet_size, (I32)last.packet_size, 0);
    cur.return_point = laz_ic_decompress(&r->ic_return_point, last.return_point, 0);
    cur.x = laz_ic_decompress(&r->ic_xyz, last.x, 0);
    cur.y = laz_ic_decompress(&r->ic_xyz, last.y, 1);
    cur.z = laz_ic_decompress(&r->ic_xyz, last.z, 2);

    laz_wp_pack(&cur, item);
    memcpy(r->last_item, item, 28);
}

static void wp13v1_destroy(LazReadItem *self)
{
    Wavepacket13v1 *r = (Wavepacket13v1 *)self;
    U32 i;
    laz_symbol_model_free(&r->m_packet_index);
    for (i = 0; i < 4; i++) laz_symbol_model_free(&r->m_offset_diff[i]);
    laz_ic_free(&r->ic_offset_diff);
    laz_ic_free(&r->ic_packet_size);
    laz_ic_free(&r->ic_return_point);
    laz_ic_free(&r->ic_xyz);
}

LazReadItem *laz_readitem_v1_wavepacket13(LazDecoder *dec)
{
    Wavepacket13v1 *r = (Wavepacket13v1 *)calloc(1, sizeof(Wavepacket13v1));
    U32 i;
    if (!r) return NULL;
    r->base.read = wp13v1_read;
    r->base.init = wp13v1_init;
    r->base.destroy = wp13v1_destroy;
    r->base.dec = dec;

    laz_symbol_model_setup(&r->m_packet_index, 256, LAZ_FALSE);
    for (i = 0; i < 4; i++) laz_symbol_model_setup(&r->m_offset_diff[i], 4, LAZ_FALSE);
    laz_ic_setup_dec(&r->ic_offset_diff, dec, 32, 1, 8, 0);
    laz_ic_setup_dec(&r->ic_packet_size, dec, 32, 1, 8, 0);
    laz_ic_setup_dec(&r->ic_return_point, dec, 32, 1, 8, 0);
    laz_ic_setup_dec(&r->ic_xyz, dec, 32, 3, 8, 0);
    return (LazReadItem *)r;
}
