/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * LAZ v2 compressed item writers -- ported from LASzip's
 * laswriteitemcompressed_v2.cpp, and the exact inverse of the readers in
 * laz_readitem_v2.c. This is the encoding almost every LAZ file in the wild
 * uses for point formats 0-5.
 */
#include "laz_writeitem.h"

/* ======================================================== POINT10 v2 ===== */

typedef struct {
    LazWriteItem base;
    LazSymbolModel m_changed_values;
    LazIntCompressor ic_intensity;
    LazSymbolModel m_scan_angle_rank[2];
    LazIntCompressor ic_point_source_ID;
    LazSymbolModel m_bit_byte[256], m_classification[256], m_user_data[256];
    U8 created_bit_byte[256], created_classification[256], created_user_data[256];
    LazIntCompressor ic_dx;
    LazIntCompressor ic_dy;
    LazIntCompressor ic_z;
    LazStreamingMedian5 last_x_diff_median5[16];
    LazStreamingMedian5 last_y_diff_median5[16];
    I32 last_intensity[16];
    I32 last_height[8];
    U8 last_item[20];
} Point10v2;

static BOOL p10v2_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Point10v2 *w = (Point10v2 *)self;
    U32 i;
    (void)context;

    for (i = 0; i < 16; i++) {
        laz_median5_init(&w->last_x_diff_median5[i]);
        laz_median5_init(&w->last_y_diff_median5[i]);
        w->last_intensity[i] = 0;
        w->last_height[i / 2] = 0;
    }

    laz_symbol_model_init(&w->m_changed_values, NULL);
    laz_ic_init_compressor(&w->ic_intensity);
    laz_symbol_model_init(&w->m_scan_angle_rank[0], NULL);
    laz_symbol_model_init(&w->m_scan_angle_rank[1], NULL);
    laz_ic_init_compressor(&w->ic_point_source_ID);
    laz_bank_reinit(w->m_bit_byte, w->created_bit_byte, 256);
    laz_bank_reinit(w->m_classification, w->created_classification, 256);
    laz_bank_reinit(w->m_user_data, w->created_user_data, 256);
    laz_ic_init_compressor(&w->ic_dx);
    laz_ic_init_compressor(&w->ic_dy);
    laz_ic_init_compressor(&w->ic_z);

    memcpy(w->last_item, item, 20);
    return LAZ_TRUE;
}

static BOOL p10v2_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Point10v2 *w = (Point10v2 *)self;
    U8 *li = w->last_item;
    U32 n, m, l, k_bits;
    I32 median, diff, changed_values;

    (void)context;
    /* the return-number context comes from the point being written, which is
     * what the reader recovers before it needs it */
    n = P10_NUMBER_OF_RETURNS(item);
    m = laz_number_return_map[n][P10_RETURN_NUMBER(item)];
    l = laz_number_return_level[n][P10_RETURN_NUMBER(item)];

    changed_values = ((li[14] != item[14]) << 5) |     /* bit_byte */
                     ((w->last_intensity[m] != (I32)P10_INTENSITY_IN(item)) << 4) |
                     ((li[15] != item[15]) << 3) |     /* classification */
                     ((li[16] != item[16]) << 2) |     /* scan_angle_rank */
                     ((li[17] != item[17]) << 1) |     /* user_data */
                     (P10_POINT_SOURCE_ID(li) != P10_POINT_SOURCE_ID_IN(item));

    laz_encode_symbol(self->enc, &w->m_changed_values, (U32)changed_values);

    if (changed_values & 32) {
        laz_encode_symbol(self->enc,
                          laz_bank_get(w->m_bit_byte, w->created_bit_byte, li[14]),
                          item[14]);
    }

    if (changed_values & 16) {
        laz_ic_compress(&w->ic_intensity, w->last_intensity[m],
                        P10_INTENSITY_IN(item), (m < 3 ? m : 3));
        w->last_intensity[m] = P10_INTENSITY_IN(item);
    }

    if (changed_values & 8) {
        laz_encode_symbol(self->enc,
                          laz_bank_get(w->m_classification, w->created_classification, li[15]),
                          item[15]);
    }

    if (changed_values & 4) {
        laz_encode_symbol(self->enc, &w->m_scan_angle_rank[P10_SCAN_DIR_FLAG(item)],
                          (U32)(U8)U8_FOLD((I32)item[16] - (I32)li[16]));
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

    /* x */
    median = laz_median5_get(&w->last_x_diff_median5[m]);
    diff = P10_X_IN(item) - P10_X(li);
    laz_ic_compress(&w->ic_dx, median, diff, (n == 1));
    laz_median5_add(&w->last_x_diff_median5[m], diff);

    /* y -- the context deliberately uses ic_dx's k, matching LASzip */
    k_bits = w->ic_dx.k;
    median = laz_median5_get(&w->last_y_diff_median5[m]);
    diff = P10_Y_IN(item) - P10_Y(li);
    laz_ic_compress(&w->ic_dy, median, diff,
                    (n == 1) + (k_bits < 20 ? U32_ZERO_BIT_0(k_bits) : 20));
    laz_median5_add(&w->last_y_diff_median5[m], diff);

    /* z */
    k_bits = (w->ic_dx.k + w->ic_dy.k) / 2;
    laz_ic_compress(&w->ic_z, w->last_height[l], P10_Z_IN(item),
                    (n == 1) + (k_bits < 18 ? U32_ZERO_BIT_0(k_bits) : 18));
    w->last_height[l] = P10_Z_IN(item);

    memcpy(li, item, 20);
    return LAZ_TRUE;
}

static void p10v2_destroy(LazWriteItem *self)
{
    Point10v2 *w = (Point10v2 *)self;
    laz_symbol_model_free(&w->m_changed_values);
    laz_ic_free(&w->ic_intensity);
    laz_symbol_model_free(&w->m_scan_angle_rank[0]);
    laz_symbol_model_free(&w->m_scan_angle_rank[1]);
    laz_ic_free(&w->ic_point_source_ID);
    laz_bank_free(w->m_bit_byte, 256);
    laz_bank_free(w->m_classification, 256);
    laz_bank_free(w->m_user_data, 256);
    laz_ic_free(&w->ic_dx);
    laz_ic_free(&w->ic_dy);
    laz_ic_free(&w->ic_z);
}

LazWriteItem *laz_writeitem_v2_point10(LazEncoder *enc)
{
    Point10v2 *w = (Point10v2 *)calloc(1, sizeof(Point10v2));
    if (!w) return NULL;
    w->base.write = p10v2_write;
    w->base.init = p10v2_init;
    w->base.destroy = p10v2_destroy;
    w->base.enc = enc;

    laz_symbol_model_setup(&w->m_changed_values, 64, LAZ_TRUE);
    laz_ic_setup_enc(&w->ic_intensity, enc, 16, 4, 8, 0);
    laz_symbol_model_setup(&w->m_scan_angle_rank[0], 256, LAZ_TRUE);
    laz_symbol_model_setup(&w->m_scan_angle_rank[1], 256, LAZ_TRUE);
    laz_ic_setup_enc(&w->ic_point_source_ID, enc, 16, 1, 8, 0);
    laz_bank_setup(w->m_bit_byte, w->created_bit_byte, 256, 256, LAZ_TRUE);
    laz_bank_setup(w->m_classification, w->created_classification, 256, 256, LAZ_TRUE);
    laz_bank_setup(w->m_user_data, w->created_user_data, 256, 256, LAZ_TRUE);
    laz_ic_setup_enc(&w->ic_dx, enc, 32, 2, 8, 0);
    laz_ic_setup_enc(&w->ic_dy, enc, 32, 22, 8, 0);
    laz_ic_setup_enc(&w->ic_z, enc, 32, 20, 8, 0);
    return (LazWriteItem *)w;
}

/* ====================================================== GPSTIME11 v2 ===== */

#define LASZIP_GPSTIME_MULTI            500
#define LASZIP_GPSTIME_MULTI_MINUS      (-10)
#define LASZIP_GPSTIME_MULTI_UNCHANGED  (LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS + 1)
#define LASZIP_GPSTIME_MULTI_CODE_FULL  (LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS + 2)
#define LASZIP_GPSTIME_MULTI_TOTAL      (LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS + 6)

typedef struct {
    LazWriteItem base;
    LazSymbolModel m_gpstime_multi;
    LazSymbolModel m_gpstime_0diff;
    LazIntCompressor ic_gpstime;
    U32 last, next;
    I64 last_gpstime[4];
    I32 last_gpstime_diff[4];
    I32 multi_extreme_counter[4];
} Gpstime11v2;

static BOOL gps11v2_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Gpstime11v2 *w = (Gpstime11v2 *)self;
    (void)context;

    w->last = 0;
    w->next = 0;
    memset(w->last_gpstime_diff, 0, sizeof(w->last_gpstime_diff));
    memset(w->multi_extreme_counter, 0, sizeof(w->multi_extreme_counter));

    laz_symbol_model_init(&w->m_gpstime_multi, NULL);
    laz_symbol_model_init(&w->m_gpstime_0diff, NULL);
    laz_ic_init_compressor(&w->ic_gpstime);

    memcpy(&w->last_gpstime[0], item, 8);
    w->last_gpstime[1] = 0;
    w->last_gpstime[2] = 0;
    w->last_gpstime[3] = 0;
    return LAZ_TRUE;
}

/* Starts a fresh sequence, coding the time in full. */
static void start_new_sequence(Gpstime11v2 *w, LazEncoder *enc, I64 this_gpstime)
{
    laz_ic_compress(&w->ic_gpstime, (I32)((U64)w->last_gpstime[w->last] >> 32),
                    (I32)((U64)this_gpstime >> 32), 8);
    laz_write_int(enc, (U32)(U64)this_gpstime);
    w->next = (w->next + 1) & 3;
    w->last = w->next;
    w->last_gpstime_diff[w->last] = 0;
    w->multi_extreme_counter[w->last] = 0;
}

static BOOL gps11v2_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Gpstime11v2 *w = (Gpstime11v2 *)self;
    I64 this_gpstime;
    I64 diff64;
    I32 diff32;
    U32 other;

    memcpy(&this_gpstime, item, 8);

    if (w->last_gpstime_diff[w->last] == 0) {   /* last integer difference was zero */
        if (this_gpstime == w->last_gpstime[w->last]) {
            laz_encode_symbol(self->enc, &w->m_gpstime_0diff, 0);
            return LAZ_TRUE;                    /* the doubles have not changed */
        }
        diff64 = this_gpstime - w->last_gpstime[w->last];
        diff32 = (I32)diff64;
        if (diff64 == (I64)diff32) {            /* difference fits in 32 bits */
            laz_encode_symbol(self->enc, &w->m_gpstime_0diff, 1);
            laz_ic_compress(&w->ic_gpstime, 0, diff32, 0);
            w->last_gpstime_diff[w->last] = diff32;
            w->multi_extreme_counter[w->last] = 0;
        } else {                                /* difference is huge */
            other = laz_gps_find_other_sequence(w->last_gpstime, w->last, this_gpstime);
            if (other) {                        /* it belongs to another sequence */
                laz_encode_symbol(self->enc, &w->m_gpstime_0diff, other + 2);
                w->last = (w->last + other) & 3;
                return gps11v2_write(self, item, context);
            }
            laz_encode_symbol(self->enc, &w->m_gpstime_0diff, 2);
            start_new_sequence(w, self->enc, this_gpstime);
        }
        w->last_gpstime[w->last] = this_gpstime;
        return LAZ_TRUE;
    }

    /* the last integer difference was *not* zero */
    if (this_gpstime == w->last_gpstime[w->last]) {
        laz_encode_symbol(self->enc, &w->m_gpstime_multi, LASZIP_GPSTIME_MULTI_UNCHANGED);
        return LAZ_TRUE;
    }

    diff64 = this_gpstime - w->last_gpstime[w->last];
    diff32 = (I32)diff64;

    if (diff64 == (I64)diff32) {
        /* how many times the last difference this one is */
        I32 last_diff = w->last_gpstime_diff[w->last];
        I32 multi = I32_QUANTIZE((F32)diff32 / (F32)last_diff);

        if (multi == 1) {
            /* the case we expect most often, for regularly spaced pulses */
            laz_encode_symbol(self->enc, &w->m_gpstime_multi, 1);
            laz_ic_compress(&w->ic_gpstime, last_diff, diff32, 1);
            w->multi_extreme_counter[w->last] = 0;
        } else if (multi > 0) {
            if (multi < LASZIP_GPSTIME_MULTI) {     /* coded directly */
                laz_encode_symbol(self->enc, &w->m_gpstime_multi, (U32)multi);
                laz_ic_compress(&w->ic_gpstime, multi * last_diff, diff32,
                                (multi < 10) ? 2 : 3);
            } else {
                laz_encode_symbol(self->enc, &w->m_gpstime_multi, LASZIP_GPSTIME_MULTI);
                laz_ic_compress(&w->ic_gpstime, LASZIP_GPSTIME_MULTI * last_diff, diff32, 4);
                w->multi_extreme_counter[w->last]++;
                if (w->multi_extreme_counter[w->last] > 3) {
                    w->last_gpstime_diff[w->last] = diff32;
                    w->multi_extreme_counter[w->last] = 0;
                }
            }
        } else if (multi < 0) {
            if (multi > LASZIP_GPSTIME_MULTI_MINUS) {   /* coded directly */
                laz_encode_symbol(self->enc, &w->m_gpstime_multi,
                                  (U32)(LASZIP_GPSTIME_MULTI - multi));
                laz_ic_compress(&w->ic_gpstime, multi * last_diff, diff32, 5);
            } else {
                laz_encode_symbol(self->enc, &w->m_gpstime_multi,
                                  LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS);
                laz_ic_compress(&w->ic_gpstime,
                                LASZIP_GPSTIME_MULTI_MINUS * last_diff, diff32, 6);
                w->multi_extreme_counter[w->last]++;
                if (w->multi_extreme_counter[w->last] > 3) {
                    w->last_gpstime_diff[w->last] = diff32;
                    w->multi_extreme_counter[w->last] = 0;
                }
            }
        } else {
            laz_encode_symbol(self->enc, &w->m_gpstime_multi, 0);
            laz_ic_compress(&w->ic_gpstime, 0, diff32, 7);
            w->multi_extreme_counter[w->last]++;
            if (w->multi_extreme_counter[w->last] > 3) {
                w->last_gpstime_diff[w->last] = diff32;
                w->multi_extreme_counter[w->last] = 0;
            }
        }
    } else {                                    /* difference is huge */
        other = laz_gps_find_other_sequence(w->last_gpstime, w->last, this_gpstime);
        if (other) {                            /* it belongs to another sequence */
            laz_encode_symbol(self->enc, &w->m_gpstime_multi,
                              (U32)(LASZIP_GPSTIME_MULTI_CODE_FULL + (I32)other));
            w->last = (w->last + other) & 3;
            return gps11v2_write(self, item, context);
        }
        laz_encode_symbol(self->enc, &w->m_gpstime_multi, LASZIP_GPSTIME_MULTI_CODE_FULL);
        start_new_sequence(w, self->enc, this_gpstime);
    }

    w->last_gpstime[w->last] = this_gpstime;
    return LAZ_TRUE;
}

static void gps11v2_destroy(LazWriteItem *self)
{
    Gpstime11v2 *w = (Gpstime11v2 *)self;
    laz_symbol_model_free(&w->m_gpstime_multi);
    laz_symbol_model_free(&w->m_gpstime_0diff);
    laz_ic_free(&w->ic_gpstime);
}

LazWriteItem *laz_writeitem_v2_gpstime11(LazEncoder *enc)
{
    Gpstime11v2 *w = (Gpstime11v2 *)calloc(1, sizeof(Gpstime11v2));
    if (!w) return NULL;
    w->base.write = gps11v2_write;
    w->base.init = gps11v2_init;
    w->base.destroy = gps11v2_destroy;
    w->base.enc = enc;

    laz_symbol_model_setup(&w->m_gpstime_multi, LASZIP_GPSTIME_MULTI_TOTAL, LAZ_TRUE);
    laz_symbol_model_setup(&w->m_gpstime_0diff, 6, LAZ_TRUE);
    laz_ic_setup_enc(&w->ic_gpstime, enc, 32, 9, 8, 0);
    return (LazWriteItem *)w;
}

/* ========================================================== RGB12 v2 ===== */

typedef struct {
    LazWriteItem base;
    LazSymbolModel m_byte_used;
    LazSymbolModel m_rgb_diff[6];
    U16 last_item[3];
} Rgb12v2;

static BOOL rgb12v2_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Rgb12v2 *w = (Rgb12v2 *)self;
    U32 i;
    (void)context;
    laz_symbol_model_init(&w->m_byte_used, NULL);
    for (i = 0; i < 6; i++) laz_symbol_model_init(&w->m_rgb_diff[i], NULL);
    memcpy(w->last_item, item, 6);
    return LAZ_TRUE;
}

static BOOL rgb12v2_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Rgb12v2 *w = (Rgb12v2 *)self;
    const U16 *cur = (const U16 *)item;
    U16 *last = w->last_item;
    I32 diff_l = 0, diff_h = 0, corr;
    U32 sym;

    (void)context;
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

    laz_encode_symbol(self->enc, &w->m_byte_used, sym);

    if (sym & (1 << 0)) {
        diff_l = (I32)(cur[0] & 255) - (I32)(last[0] & 255);
        laz_encode_symbol(self->enc, &w->m_rgb_diff[0], (U32)(U8)U8_FOLD(diff_l));
    }
    if (sym & (1 << 1)) {
        diff_h = (I32)(cur[0] >> 8) - (I32)(last[0] >> 8);
        laz_encode_symbol(self->enc, &w->m_rgb_diff[1], (U32)(U8)U8_FOLD(diff_h));
    }
    if (sym & (1 << 6)) {
        if (sym & (1 << 2)) {
            corr = (I32)(cur[1] & 255) - (I32)U8_CLAMP(diff_l + (I32)(last[1] & 255));
            laz_encode_symbol(self->enc, &w->m_rgb_diff[2], (U32)(U8)U8_FOLD(corr));
        }
        if (sym & (1 << 4)) {
            diff_l = (diff_l + (I32)(cur[1] & 255) - (I32)(last[1] & 255)) / 2;
            corr = (I32)(cur[2] & 255) - (I32)U8_CLAMP(diff_l + (I32)(last[2] & 255));
            laz_encode_symbol(self->enc, &w->m_rgb_diff[4], (U32)(U8)U8_FOLD(corr));
        }
        if (sym & (1 << 3)) {
            corr = (I32)(cur[1] >> 8) - (I32)U8_CLAMP(diff_h + (I32)(last[1] >> 8));
            laz_encode_symbol(self->enc, &w->m_rgb_diff[3], (U32)(U8)U8_FOLD(corr));
        }
        if (sym & (1 << 5)) {
            diff_h = (diff_h + (I32)(cur[1] >> 8) - (I32)(last[1] >> 8)) / 2;
            corr = (I32)(cur[2] >> 8) - (I32)U8_CLAMP(diff_h + (I32)(last[2] >> 8));
            laz_encode_symbol(self->enc, &w->m_rgb_diff[5], (U32)(U8)U8_FOLD(corr));
        }
    }
    memcpy(w->last_item, item, 6);
    return LAZ_TRUE;
}

static void rgb12v2_destroy(LazWriteItem *self)
{
    Rgb12v2 *w = (Rgb12v2 *)self;
    U32 i;
    laz_symbol_model_free(&w->m_byte_used);
    for (i = 0; i < 6; i++) laz_symbol_model_free(&w->m_rgb_diff[i]);
}

LazWriteItem *laz_writeitem_v2_rgb12(LazEncoder *enc)
{
    Rgb12v2 *w = (Rgb12v2 *)calloc(1, sizeof(Rgb12v2));
    U32 i;
    if (!w) return NULL;
    w->base.write = rgb12v2_write;
    w->base.init = rgb12v2_init;
    w->base.destroy = rgb12v2_destroy;
    w->base.enc = enc;
    laz_symbol_model_setup(&w->m_byte_used, 128, LAZ_TRUE);
    for (i = 0; i < 6; i++) laz_symbol_model_setup(&w->m_rgb_diff[i], 256, LAZ_TRUE);
    return (LazWriteItem *)w;
}

/* =========================================================== BYTE v2 ===== */

typedef struct {
    LazWriteItem base;
    U32 number;
    LazSymbolModel *m_byte;   /* [number] */
    U8 *last_item;            /* [number] */
} Bytev2;

static BOOL bytev2_init(LazWriteItem *self, const U8 *item, U32 *context)
{
    Bytev2 *w = (Bytev2 *)self;
    U32 i;
    (void)context;
    for (i = 0; i < w->number; i++) laz_symbol_model_init(&w->m_byte[i], NULL);
    memcpy(w->last_item, item, w->number);
    return LAZ_TRUE;
}

static BOOL bytev2_write(LazWriteItem *self, const U8 *item, U32 *context)
{
    Bytev2 *w = (Bytev2 *)self;
    U32 i;
    (void)context;
    for (i = 0; i < w->number; i++) {
        I32 diff = (I32)item[i] - (I32)w->last_item[i];
        laz_encode_symbol(self->enc, &w->m_byte[i], (U32)(U8)U8_FOLD(diff));
    }
    memcpy(w->last_item, item, w->number);
    return LAZ_TRUE;
}

static void bytev2_destroy(LazWriteItem *self)
{
    Bytev2 *w = (Bytev2 *)self;
    laz_symbol_models_free(w->m_byte, w->number);
    free(w->last_item);
}

LazWriteItem *laz_writeitem_v2_byte(LazEncoder *enc, U32 number)
{
    Bytev2 *w = (Bytev2 *)calloc(1, sizeof(Bytev2));
    if (!w) return NULL;
    w->base.write = bytev2_write;
    w->base.init = bytev2_init;
    w->base.destroy = bytev2_destroy;
    w->base.enc = enc;
    w->number = number;
    w->m_byte = laz_symbol_models_new(number, 256, LAZ_TRUE);
    w->last_item = (U8 *)calloc(number ? number : 1, 1);
    if (!w->m_byte || !w->last_item) { bytev2_destroy((LazWriteItem *)w); free(w); return NULL; }
    return (LazWriteItem *)w;
}
