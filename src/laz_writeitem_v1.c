/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * LAZ v1 compressed item writers -- ported from LASzip's
 * laswriteitemcompressed_v1.cpp, and the exact inverse of the readers in
 * laz_readitem_v1.c.
 *
 * Reading that file alongside this one is the best way to check either: every
 * decode call there has one encode call here, in the same order, against the
 * same model. Where the reader derives a value from the stream, the writer
 * derives the same value from the point it was handed.
 */
#include "laz_writeitem.h"

/* ======================================================== POINT10 v1 ===== */

typedef struct {
    LazWriteItem base;
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

static BOOL p10v1_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Point10v1 *w = (Point10v1 *)self;
    (void)context;

    w->last_x_diff[0] = w->last_x_diff[1] = w->last_x_diff[2] = 0;
    w->last_y_diff[0] = w->last_y_diff[1] = w->last_y_diff[2] = 0;
    w->last_incr = 0;

    laz_ic_init_compressor(&w->ic_dx);
    laz_ic_init_compressor(&w->ic_dy);
    laz_ic_init_compressor(&w->ic_z);
    laz_ic_init_compressor(&w->ic_intensity);
    laz_ic_init_compressor(&w->ic_scan_angle_rank);
    laz_ic_init_compressor(&w->ic_point_source_ID);
    laz_symbol_model_init(&w->m_changed_values, NULL);
    laz_bank_reinit(w->m_bit_byte, w->created_bit_byte, 256);
    laz_bank_reinit(w->m_classification, w->created_classification, 256);
    laz_bank_reinit(w->m_user_data, w->created_user_data, 256);

    memcpy(w->last_item, item, 20);
    return LAZ_TRUE;
}

static BOOL p10v1_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Point10v1 *w = (Point10v1 *)self;
    U8 *li = w->last_item;
    I32 median_x, median_y, x_diff, y_diff, changed_values;
    U32 k_bits;

    (void)context;
    median_x = laz_median3(w->last_x_diff);
    median_y = laz_median3(w->last_y_diff);

    /* x, y, z first in v1 -- the opposite order from v2 */
    x_diff = P10_X_IN(item) - P10_X(li);
    y_diff = P10_Y_IN(item) - P10_Y(li);

    laz_ic_compress(&w->ic_dx, median_x, x_diff, 0);
    k_bits = w->ic_dx.k;                        /* corrector bits switch contexts */
    laz_ic_compress(&w->ic_dy, median_y, y_diff, (k_bits < 19 ? k_bits : 19));
    k_bits = (k_bits + w->ic_dy.k) / 2;
    laz_ic_compress(&w->ic_z, P10_Z(li), P10_Z_IN(item), (k_bits < 19 ? k_bits : 19));

    changed_values = ((P10_INTENSITY(li) != P10_INTENSITY_IN(item)) << 5) |
                     ((li[14] != item[14]) << 4) |     /* bit_byte */
                     ((li[15] != item[15]) << 3) |     /* classification */
                     ((li[16] != item[16]) << 2) |     /* scan_angle_rank */
                     ((li[17] != item[17]) << 1) |     /* user_data */
                     (P10_POINT_SOURCE_ID(li) != P10_POINT_SOURCE_ID_IN(item));

    laz_encode_symbol(self->enc, &w->m_changed_values, (U32)changed_values);

    if (changed_values) {
        if (changed_values & 32) {
            laz_ic_compress(&w->ic_intensity, P10_INTENSITY(li), P10_INTENSITY_IN(item), 0);
        }
        if (changed_values & 16) {
            laz_encode_symbol(self->enc,
                              laz_bank_get(w->m_bit_byte, w->created_bit_byte, li[14]),
                              item[14]);
        }
        if (changed_values & 8) {
            laz_encode_symbol(self->enc,
                              laz_bank_get(w->m_classification, w->created_classification, li[15]),
                              item[15]);
        }
        if (changed_values & 4) {
            laz_ic_compress(&w->ic_scan_angle_rank, li[16], item[16], k_bits < 3);
        }
        if (changed_values & 2) {
            laz_encode_symbol(self->enc,
                              laz_bank_get(w->m_user_data, w->created_user_data, li[17]),
                              item[17]);
        }
        if (changed_values & 1) {
            laz_ic_compress(&w->ic_point_source_ID,
                            P10_POINT_SOURCE_ID(li), P10_POINT_SOURCE_ID_IN(item), 0);
        }
    }

    w->last_x_diff[w->last_incr] = x_diff;
    w->last_y_diff[w->last_incr] = y_diff;
    w->last_incr++;
    if (w->last_incr > 2) w->last_incr = 0;

    memcpy(li, item, 20);
    return LAZ_TRUE;
}

static void p10v1_destroy(LazWriteItem *self)
{
    Point10v1 *w = (Point10v1 *)self;
    laz_ic_free(&w->ic_dx);
    laz_ic_free(&w->ic_dy);
    laz_ic_free(&w->ic_z);
    laz_ic_free(&w->ic_intensity);
    laz_ic_free(&w->ic_scan_angle_rank);
    laz_ic_free(&w->ic_point_source_ID);
    laz_symbol_model_free(&w->m_changed_values);
    laz_bank_free(w->m_bit_byte, 256);
    laz_bank_free(w->m_classification, 256);
    laz_bank_free(w->m_user_data, 256);
}

LazWriteItem *laz_writeitem_v1_point10(LazEncoder *enc)
{
    Point10v1 *w = (Point10v1 *)calloc(1, sizeof(Point10v1));
    if (!w) return NULL;
    w->base.write = p10v1_write;
    w->base.init = p10v1_init;
    w->base.destroy = p10v1_destroy;
    w->base.enc = enc;

    laz_ic_setup_enc(&w->ic_dx, enc, 32, 1, 8, 0);
    laz_ic_setup_enc(&w->ic_dy, enc, 32, 20, 8, 0);
    laz_ic_setup_enc(&w->ic_z, enc, 32, 20, 8, 0);
    laz_ic_setup_enc(&w->ic_intensity, enc, 16, 1, 8, 0);
    laz_ic_setup_enc(&w->ic_scan_angle_rank, enc, 8, 2, 8, 0);
    laz_ic_setup_enc(&w->ic_point_source_ID, enc, 16, 1, 8, 0);
    laz_symbol_model_setup(&w->m_changed_values, 64, LAZ_TRUE);
    laz_bank_setup(w->m_bit_byte, w->created_bit_byte, 256, 256, LAZ_TRUE);
    laz_bank_setup(w->m_classification, w->created_classification, 256, 256, LAZ_TRUE);
    laz_bank_setup(w->m_user_data, w->created_user_data, 256, 256, LAZ_TRUE);
    return (LazWriteItem *)w;
}

/* ====================================================== GPSTIME11 v1 ===== */

#define LASZIP_GPSTIME_MULTIMAX 512

typedef struct {
    LazWriteItem base;
    LazSymbolModel m_gpstime_multi;
    LazSymbolModel m_gpstime_0diff;
    LazIntCompressor ic_gpstime;
    I32 last_gpstime_diff;
    I32 multi_extreme_counter;
    I64 last_gpstime;
} Gpstime11v1;

static BOOL gps11v1_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Gpstime11v1 *w = (Gpstime11v1 *)self;
    (void)context;
    w->last_gpstime_diff = 0;
    w->multi_extreme_counter = 0;
    laz_symbol_model_init(&w->m_gpstime_multi, NULL);
    laz_symbol_model_init(&w->m_gpstime_0diff, NULL);
    laz_ic_init_compressor(&w->ic_gpstime);
    memcpy(&w->last_gpstime, item, 8);
    return LAZ_TRUE;
}

static BOOL gps11v1_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Gpstime11v1 *w = (Gpstime11v1 *)self;
    I64 this_gpstime;
    I64 diff64;
    I32 diff32;

    (void)context;
    memcpy(&this_gpstime, item, 8);

    if (w->last_gpstime_diff == 0) {        /* the last integer difference was zero */
        if (this_gpstime == w->last_gpstime) {
            laz_encode_symbol(self->enc, &w->m_gpstime_0diff, 0);
            return LAZ_TRUE;                /* the doubles have not changed */
        }
        diff64 = this_gpstime - w->last_gpstime;
        diff32 = (I32)diff64;
        if (diff64 == (I64)diff32) {        /* difference fits in 32 bits */
            laz_encode_symbol(self->enc, &w->m_gpstime_0diff, 1);
            laz_ic_compress(&w->ic_gpstime, 0, diff32, 0);
            w->last_gpstime_diff = diff32;
        } else {                            /* difference is huge */
            laz_encode_symbol(self->enc, &w->m_gpstime_0diff, 2);
            laz_write_int64(self->enc, (U64)this_gpstime);
        }
        w->last_gpstime = this_gpstime;
        return LAZ_TRUE;
    }

    /* the last integer difference was *not* zero */
    if (this_gpstime == w->last_gpstime) {
        laz_encode_symbol(self->enc, &w->m_gpstime_multi, LASZIP_GPSTIME_MULTIMAX - 1);
        return LAZ_TRUE;
    }

    diff64 = this_gpstime - w->last_gpstime;
    diff32 = (I32)diff64;

    if (diff64 == (I64)diff32) {
        /* how many times the last difference this one is, clamped into the
         * range the symbol alphabet can carry */
        I32 multi = I32_QUANTIZE((F32)diff32 / (F32)w->last_gpstime_diff);
        if (multi >= LASZIP_GPSTIME_MULTIMAX - 3) multi = LASZIP_GPSTIME_MULTIMAX - 3;
        else if (multi <= 0) multi = 0;

        laz_encode_symbol(self->enc, &w->m_gpstime_multi, (U32)multi);

        if (multi == 1) {                   /* the case we expect most often */
            laz_ic_compress(&w->ic_gpstime, w->last_gpstime_diff, diff32, 1);
            w->last_gpstime_diff = diff32;
            w->multi_extreme_counter = 0;
        } else if (multi == 0) {
            laz_ic_compress(&w->ic_gpstime, w->last_gpstime_diff / 4, diff32, 2);
            w->multi_extreme_counter++;
            if (w->multi_extreme_counter > 3) {
                w->last_gpstime_diff = diff32;
                w->multi_extreme_counter = 0;
            }
        } else if (multi < 10) {
            laz_ic_compress(&w->ic_gpstime, multi * w->last_gpstime_diff, diff32, 3);
        } else if (multi < 50) {
            laz_ic_compress(&w->ic_gpstime, multi * w->last_gpstime_diff, diff32, 4);
        } else {
            laz_ic_compress(&w->ic_gpstime, multi * w->last_gpstime_diff, diff32, 5);
            if (multi == LASZIP_GPSTIME_MULTIMAX - 3) {
                w->multi_extreme_counter++;
                if (w->multi_extreme_counter > 3) {
                    w->last_gpstime_diff = diff32;
                    w->multi_extreme_counter = 0;
                }
            }
        }
    } else {                                /* difference is huge: write the double */
        laz_encode_symbol(self->enc, &w->m_gpstime_multi, LASZIP_GPSTIME_MULTIMAX - 2);
        laz_write_int64(self->enc, (U64)this_gpstime);
    }

    w->last_gpstime = this_gpstime;
    return LAZ_TRUE;
}

static void gps11v1_destroy(LazWriteItem *self)
{
    Gpstime11v1 *w = (Gpstime11v1 *)self;
    laz_symbol_model_free(&w->m_gpstime_multi);
    laz_symbol_model_free(&w->m_gpstime_0diff);
    laz_ic_free(&w->ic_gpstime);
}

LazWriteItem *laz_writeitem_v1_gpstime11(LazEncoder *enc)
{
    Gpstime11v1 *w = (Gpstime11v1 *)calloc(1, sizeof(Gpstime11v1));
    if (!w) return NULL;
    w->base.write = gps11v1_write;
    w->base.init = gps11v1_init;
    w->base.destroy = gps11v1_destroy;
    w->base.enc = enc;
    laz_symbol_model_setup(&w->m_gpstime_multi, LASZIP_GPSTIME_MULTIMAX, LAZ_TRUE);
    laz_symbol_model_setup(&w->m_gpstime_0diff, 3, LAZ_TRUE);
    laz_ic_setup_enc(&w->ic_gpstime, enc, 32, 6, 8, 0);
    return (LazWriteItem *)w;
}

/* ========================================================== RGB12 v1 ===== */

typedef struct {
    LazWriteItem base;
    LazSymbolModel m_byte_used;
    LazIntCompressor ic_rgb;
    U16 last_item[3];
} Rgb12v1;

static BOOL rgb12v1_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Rgb12v1 *w = (Rgb12v1 *)self;
    (void)context;
    laz_symbol_model_init(&w->m_byte_used, NULL);
    laz_ic_init_compressor(&w->ic_rgb);
    memcpy(w->last_item, item, 6);
    return LAZ_TRUE;
}

static BOOL rgb12v1_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Rgb12v1 *w = (Rgb12v1 *)self;
    const U16 *cur = (const U16 *)item;
    U16 *last = w->last_item;
    U32 sym;

    (void)context;
    /* each of the six bytes of r,g,b is coded independently under its own context */
    sym  = (U32)((last[0] & 0x00FF) != (cur[0] & 0x00FF)) << 0;
    sym |= (U32)((last[0] & 0xFF00) != (cur[0] & 0xFF00)) << 1;
    sym |= (U32)((last[1] & 0x00FF) != (cur[1] & 0x00FF)) << 2;
    sym |= (U32)((last[1] & 0xFF00) != (cur[1] & 0xFF00)) << 3;
    sym |= (U32)((last[2] & 0x00FF) != (cur[2] & 0x00FF)) << 4;
    sym |= (U32)((last[2] & 0xFF00) != (cur[2] & 0xFF00)) << 5;

    laz_encode_symbol(self->enc, &w->m_byte_used, sym);

    if (sym & (1 << 0)) laz_ic_compress(&w->ic_rgb, last[0] & 255, cur[0] & 255, 0);
    if (sym & (1 << 1)) laz_ic_compress(&w->ic_rgb, last[0] >> 8, cur[0] >> 8, 1);
    if (sym & (1 << 2)) laz_ic_compress(&w->ic_rgb, last[1] & 255, cur[1] & 255, 2);
    if (sym & (1 << 3)) laz_ic_compress(&w->ic_rgb, last[1] >> 8, cur[1] >> 8, 3);
    if (sym & (1 << 4)) laz_ic_compress(&w->ic_rgb, last[2] & 255, cur[2] & 255, 4);
    if (sym & (1 << 5)) laz_ic_compress(&w->ic_rgb, last[2] >> 8, cur[2] >> 8, 5);

    memcpy(w->last_item, item, 6);
    return LAZ_TRUE;
}

static void rgb12v1_destroy(LazWriteItem *self)
{
    Rgb12v1 *w = (Rgb12v1 *)self;
    laz_symbol_model_free(&w->m_byte_used);
    laz_ic_free(&w->ic_rgb);
}

LazWriteItem *laz_writeitem_v1_rgb12(LazEncoder *enc)
{
    Rgb12v1 *w = (Rgb12v1 *)calloc(1, sizeof(Rgb12v1));
    if (!w) return NULL;
    w->base.write = rgb12v1_write;
    w->base.init = rgb12v1_init;
    w->base.destroy = rgb12v1_destroy;
    w->base.enc = enc;
    laz_symbol_model_setup(&w->m_byte_used, 64, LAZ_TRUE);
    laz_ic_setup_enc(&w->ic_rgb, enc, 8, 6, 8, 0);
    return (LazWriteItem *)w;
}

/* =========================================================== BYTE v1 ===== */

typedef struct {
    LazWriteItem base;
    U32 number;
    LazIntCompressor ic_byte;
    U8 *last_item;
} Bytev1;

static BOOL bytev1_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Bytev1 *w = (Bytev1 *)self;
    (void)context;
    laz_ic_init_compressor(&w->ic_byte);
    memcpy(w->last_item, item, w->number);
    return LAZ_TRUE;
}

static BOOL bytev1_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Bytev1 *w = (Bytev1 *)self;
    U32 i;
    (void)context;
    for (i = 0; i < w->number; i++) {
        laz_ic_compress(&w->ic_byte, w->last_item[i], item[i], i);
    }
    memcpy(w->last_item, item, w->number);
    return LAZ_TRUE;
}

static void bytev1_destroy(LazWriteItem *self)
{
    Bytev1 *w = (Bytev1 *)self;
    laz_ic_free(&w->ic_byte);
    free(w->last_item);
}

LazWriteItem *laz_writeitem_v1_byte(LazEncoder *enc, U32 number)
{
    Bytev1 *w = (Bytev1 *)calloc(1, sizeof(Bytev1));
    if (!w) return NULL;
    w->base.write = bytev1_write;
    w->base.init = bytev1_init;
    w->base.destroy = bytev1_destroy;
    w->base.enc = enc;
    w->number = number;
    laz_ic_setup_enc(&w->ic_byte, enc, 8, number, 8, 0);
    w->last_item = (U8 *)calloc(number ? number : 1, 1);
    if (!w->last_item) { free(w); return NULL; }
    return (LazWriteItem *)w;
}

/* =================================================== WAVEPACKET13 v1 ===== */

/*
 * The 29-byte wavepacket item is [index:1][offset:8][size:4][return:4][x,y,z:12].
 * Only the trailing 28 bytes are predicted; the leading index byte gets its own
 * symbol model. See LazWavepacket13 in laz_item.h.
 */
typedef struct {
    LazWriteItem base;
    LazSymbolModel m_packet_index;
    LazSymbolModel m_offset_diff[4];
    LazIntCompressor ic_offset_diff, ic_packet_size, ic_return_point, ic_xyz;
    I32 last_diff_32;
    U32 sym_last_offset_diff;
    U8 last_item[28];
} Wavepacket13v1;

static BOOL wp13v1_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Wavepacket13v1 *w = (Wavepacket13v1 *)self;
    U32 i;
    (void)context;

    w->last_diff_32 = 0;
    w->sym_last_offset_diff = 0;

    laz_symbol_model_init(&w->m_packet_index, NULL);
    for (i = 0; i < 4; i++) laz_symbol_model_init(&w->m_offset_diff[i], NULL);
    laz_ic_init_compressor(&w->ic_offset_diff);
    laz_ic_init_compressor(&w->ic_packet_size);
    laz_ic_init_compressor(&w->ic_return_point);
    laz_ic_init_compressor(&w->ic_xyz);

    memcpy(w->last_item, item + 1, 28);   /* skip the packet index byte */
    return LAZ_TRUE;
}

static BOOL wp13v1_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Wavepacket13v1 *w = (Wavepacket13v1 *)self;
    LazWavepacket13 cur, last;
    I64 diff64;
    I32 diff32;

    (void)context;
    laz_encode_symbol(self->enc, &w->m_packet_index, item[0]);
    item++;

    cur = laz_wp_unpack(item);
    last = laz_wp_unpack(w->last_item);

    diff64 = (I64)(cur.offset - last.offset);
    diff32 = (I32)diff64;

    if (diff64 == (I64)diff32) {
        if (diff32 == 0) {
            laz_encode_symbol(self->enc, &w->m_offset_diff[w->sym_last_offset_diff], 0);
            w->sym_last_offset_diff = 0;
        } else if (diff32 == (I32)last.packet_size) {
            laz_encode_symbol(self->enc, &w->m_offset_diff[w->sym_last_offset_diff], 1);
            w->sym_last_offset_diff = 1;
        } else {
            laz_encode_symbol(self->enc, &w->m_offset_diff[w->sym_last_offset_diff], 2);
            w->sym_last_offset_diff = 2;
            laz_ic_compress(&w->ic_offset_diff, w->last_diff_32, diff32, 0);
            w->last_diff_32 = diff32;
        }
    } else {
        laz_encode_symbol(self->enc, &w->m_offset_diff[w->sym_last_offset_diff], 3);
        w->sym_last_offset_diff = 3;
        laz_write_int64(self->enc, cur.offset);
    }

    laz_ic_compress(&w->ic_packet_size, (I32)last.packet_size, (I32)cur.packet_size, 0);
    laz_ic_compress(&w->ic_return_point, last.return_point, cur.return_point, 0);
    laz_ic_compress(&w->ic_xyz, last.x, cur.x, 0);
    laz_ic_compress(&w->ic_xyz, last.y, cur.y, 1);
    laz_ic_compress(&w->ic_xyz, last.z, cur.z, 2);

    memcpy(w->last_item, item, 28);
    return LAZ_TRUE;
}

static void wp13v1_destroy(LazWriteItem *self)
{
    Wavepacket13v1 *w = (Wavepacket13v1 *)self;
    U32 i;
    laz_symbol_model_free(&w->m_packet_index);
    for (i = 0; i < 4; i++) laz_symbol_model_free(&w->m_offset_diff[i]);
    laz_ic_free(&w->ic_offset_diff);
    laz_ic_free(&w->ic_packet_size);
    laz_ic_free(&w->ic_return_point);
    laz_ic_free(&w->ic_xyz);
}

LazWriteItem *laz_writeitem_v1_wavepacket13(LazEncoder *enc)
{
    Wavepacket13v1 *w = (Wavepacket13v1 *)calloc(1, sizeof(Wavepacket13v1));
    U32 i;
    if (!w) return NULL;
    w->base.write = wp13v1_write;
    w->base.init = wp13v1_init;
    w->base.destroy = wp13v1_destroy;
    w->base.enc = enc;

    laz_symbol_model_setup(&w->m_packet_index, 256, LAZ_TRUE);
    for (i = 0; i < 4; i++) laz_symbol_model_setup(&w->m_offset_diff[i], 4, LAZ_TRUE);
    laz_ic_setup_enc(&w->ic_offset_diff, enc, 32, 1, 8, 0);
    laz_ic_setup_enc(&w->ic_packet_size, enc, 32, 1, 8, 0);
    laz_ic_setup_enc(&w->ic_return_point, enc, 32, 1, 8, 0);
    laz_ic_setup_enc(&w->ic_xyz, enc, 32, 3, 8, 0);
    return (LazWriteItem *)w;
}
