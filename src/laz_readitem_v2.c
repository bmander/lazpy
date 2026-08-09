/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * LAZ v2 compressed item readers -- ported from LASzip's
 * lasreaditemcompressed_v2.cpp. This is the format the overwhelming majority
 * of LAZ files in the wild use for point formats 0-5.
 */
#include "laz_readitem.h"

const U8 laz_number_return_map[8][8] = {
    { 15, 14, 13, 12, 11, 10,  9,  8 },
    { 14,  0,  1,  3,  6, 10, 10,  9 },
    { 13,  1,  2,  4,  7, 11, 11, 10 },
    { 12,  3,  4,  5,  8, 12, 12, 11 },
    { 11,  6,  7,  8,  9, 13, 13, 12 },
    { 10, 10, 11, 12, 13, 14, 14, 13 },
    {  9, 10, 11, 12, 13, 14, 15, 14 },
    {  8,  9, 10, 11, 12, 13, 14, 15 }
};

const U8 laz_number_return_level[8][8] = {
    {  0,  1,  2,  3,  4,  5,  6,  7 },
    {  1,  0,  1,  2,  3,  4,  5,  6 },
    {  2,  1,  0,  1,  2,  3,  4,  5 },
    {  3,  2,  1,  0,  1,  2,  3,  4 },
    {  4,  3,  2,  1,  0,  1,  2,  3 },
    {  5,  4,  3,  2,  1,  0,  1,  2 },
    {  6,  5,  4,  3,  2,  1,  0,  1 },
    {  7,  6,  5,  4,  3,  2,  1,  0 }
};

/* ======================================================== POINT10 v2 ===== */

/* Field accessors for the 20-byte legacy point record held in last_item. */
#define P10_RETURN_NUMBER(li)    ((li)[14] & 0x07)
#define P10_NUMBER_OF_RETURNS(li) (((li)[14] >> 3) & 0x07)
#define P10_SCAN_DIR_FLAG(li)    (((li)[14] >> 6) & 0x01)

typedef struct {
    LazReadItem base;
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

static BOOL p10v2_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Point10v2 *r = (Point10v2 *)self;
    U32 i;
    (void)context;

    for (i = 0; i < 16; i++) {
        laz_median5_init(&r->last_x_diff_median5[i]);
        laz_median5_init(&r->last_y_diff_median5[i]);
        r->last_intensity[i] = 0;
        r->last_height[i / 2] = 0;
    }

    laz_symbol_model_init(&r->m_changed_values, NULL);
    laz_ic_init_decompressor(&r->ic_intensity);
    laz_symbol_model_init(&r->m_scan_angle_rank[0], NULL);
    laz_symbol_model_init(&r->m_scan_angle_rank[1], NULL);
    laz_ic_init_decompressor(&r->ic_point_source_ID);
    laz_bank_reinit(r->m_bit_byte, r->created_bit_byte, 256);
    laz_bank_reinit(r->m_classification, r->created_classification, 256);
    laz_bank_reinit(r->m_user_data, r->created_user_data, 256);
    laz_ic_init_decompressor(&r->ic_dx);
    laz_ic_init_decompressor(&r->ic_dy);
    laz_ic_init_decompressor(&r->ic_z);

    memcpy(r->last_item, item, 20);
    /* the first point of a chunk seeds everything except intensity */
    r->last_item[12] = 0;
    r->last_item[13] = 0;
    return LAZ_TRUE;
}

static void p10v2_read(LazReadItem *self, U8 *item, U32 *context)
{
    Point10v2 *r = (Point10v2 *)self;
    U8 *li = r->last_item;
    U32 n, m, l, k_bits;
    I32 median, diff;
    I32 changed_values;

    (void)context;
    changed_values = (I32)laz_decode_symbol(self->dec, &r->m_changed_values);

    if (changed_values) {
        if (changed_values & 32) {
            li[14] = (U8)laz_decode_symbol(self->dec, laz_bank_get(r->m_bit_byte, r->created_bit_byte, li[14]));
        }

        n = P10_NUMBER_OF_RETURNS(li);
        m = laz_number_return_map[n][P10_RETURN_NUMBER(li)];
        l = laz_number_return_level[n][P10_RETURN_NUMBER(li)];

        if (changed_values & 16) {
            P10_INTENSITY(li) = (U16)laz_ic_decompress(&r->ic_intensity,
                                                       r->last_intensity[m],
                                                       (m < 3 ? m : 3));
            r->last_intensity[m] = P10_INTENSITY(li);
        } else {
            P10_INTENSITY(li) = (U16)r->last_intensity[m];
        }

        if (changed_values & 8) {
            li[15] = (U8)laz_decode_symbol(self->dec, laz_bank_get(r->m_classification, r->created_classification, li[15]));
        }

        if (changed_values & 4) {
            I32 val = (I32)laz_decode_symbol(self->dec,
                                             &r->m_scan_angle_rank[P10_SCAN_DIR_FLAG(li)]);
            li[16] = (U8)U8_FOLD(val + li[16]);
        }

        if (changed_values & 2) {
            li[17] = (U8)laz_decode_symbol(self->dec, laz_bank_get(r->m_user_data, r->created_user_data, li[17]));
        }

        if (changed_values & 1) {
            P10_POINT_SOURCE_ID(li) =
                (U16)laz_ic_decompress(&r->ic_point_source_ID, P10_POINT_SOURCE_ID(li), 0);
        }
    } else {
        n = P10_NUMBER_OF_RETURNS(li);
        m = laz_number_return_map[n][P10_RETURN_NUMBER(li)];
        l = laz_number_return_level[n][P10_RETURN_NUMBER(li)];
    }

    /* x */
    median = laz_median5_get(&r->last_x_diff_median5[m]);
    diff = laz_ic_decompress(&r->ic_dx, median, (n == 1));
    P10_X(li) += diff;
    laz_median5_add(&r->last_x_diff_median5[m], diff);

    /* y -- the context deliberately uses ic_dx's k, matching LASzip */
    median = laz_median5_get(&r->last_y_diff_median5[m]);
    k_bits = r->ic_dx.k;
    diff = laz_ic_decompress(&r->ic_dy, median,
                             (n == 1) + (k_bits < 20 ? U32_ZERO_BIT_0(k_bits) : 20));
    P10_Y(li) += diff;
    laz_median5_add(&r->last_y_diff_median5[m], diff);

    /* z */
    k_bits = (r->ic_dx.k + r->ic_dy.k) / 2;
    P10_Z(li) = laz_ic_decompress(&r->ic_z, r->last_height[l],
                                  (n == 1) + (k_bits < 18 ? U32_ZERO_BIT_0(k_bits) : 18));
    r->last_height[l] = P10_Z(li);

    memcpy(item, li, 20);
}

static void p10v2_destroy(LazReadItem *self)
{
    Point10v2 *r = (Point10v2 *)self;
    laz_symbol_model_free(&r->m_changed_values);
    laz_ic_free(&r->ic_intensity);
    laz_symbol_model_free(&r->m_scan_angle_rank[0]);
    laz_symbol_model_free(&r->m_scan_angle_rank[1]);
    laz_ic_free(&r->ic_point_source_ID);
    laz_bank_free(r->m_bit_byte, 256);
    laz_bank_free(r->m_classification, 256);
    laz_bank_free(r->m_user_data, 256);
    laz_ic_free(&r->ic_dx);
    laz_ic_free(&r->ic_dy);
    laz_ic_free(&r->ic_z);
}

LazReadItem *laz_readitem_v2_point10(LazDecoder *dec)
{
    Point10v2 *r = (Point10v2 *)calloc(1, sizeof(Point10v2));
    if (!r) return NULL;
    r->base.read = p10v2_read;
    r->base.init = p10v2_init;
    r->base.destroy = p10v2_destroy;
    r->base.dec = dec;

    laz_symbol_model_setup(&r->m_changed_values, 64, LAZ_FALSE);
    laz_ic_setup(&r->ic_intensity, dec, 16, 4, 8, 0);
    laz_symbol_model_setup(&r->m_scan_angle_rank[0], 256, LAZ_FALSE);
    laz_symbol_model_setup(&r->m_scan_angle_rank[1], 256, LAZ_FALSE);
    laz_ic_setup(&r->ic_point_source_ID, dec, 16, 1, 8, 0);
    laz_bank_setup(r->m_bit_byte, r->created_bit_byte, 256, 256);
    laz_bank_setup(r->m_classification, r->created_classification, 256, 256);
    laz_bank_setup(r->m_user_data, r->created_user_data, 256, 256);
    laz_ic_setup(&r->ic_dx, dec, 32, 2, 8, 0);
    laz_ic_setup(&r->ic_dy, dec, 32, 22, 8, 0);
    laz_ic_setup(&r->ic_z, dec, 32, 20, 8, 0);
    return (LazReadItem *)r;
}

/* ====================================================== GPSTIME11 v2 ===== */

#define LASZIP_GPSTIME_MULTI            500
#define LASZIP_GPSTIME_MULTI_MINUS      (-10)
#define LASZIP_GPSTIME_MULTI_UNCHANGED  (LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS + 1)
#define LASZIP_GPSTIME_MULTI_CODE_FULL  (LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS + 2)
#define LASZIP_GPSTIME_MULTI_TOTAL      (LASZIP_GPSTIME_MULTI - LASZIP_GPSTIME_MULTI_MINUS + 6)

typedef struct {
    LazReadItem base;
    LazSymbolModel m_gpstime_multi;
    LazSymbolModel m_gpstime_0diff;
    LazIntCompressor ic_gpstime;
    U32 last, next;
    U64 last_gpstime[4];
    I32 last_gpstime_diff[4];
    I32 multi_extreme_counter[4];
} Gpstime11v2;

static BOOL gps11v2_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Gpstime11v2 *r = (Gpstime11v2 *)self;
    (void)context;

    r->last = 0;
    r->next = 0;
    memset(r->last_gpstime_diff, 0, sizeof(r->last_gpstime_diff));
    memset(r->multi_extreme_counter, 0, sizeof(r->multi_extreme_counter));

    laz_symbol_model_init(&r->m_gpstime_multi, NULL);
    laz_symbol_model_init(&r->m_gpstime_0diff, NULL);
    laz_ic_init_decompressor(&r->ic_gpstime);

    memcpy(&r->last_gpstime[0], item, 8);
    r->last_gpstime[1] = 0;
    r->last_gpstime[2] = 0;
    r->last_gpstime[3] = 0;
    return LAZ_TRUE;
}

static void gps11v2_read(LazReadItem *self, U8 *item, U32 *context)
{
    Gpstime11v2 *r = (Gpstime11v2 *)self;
    I32 multi;

    if (r->last_gpstime_diff[r->last] == 0) {   /* last integer difference was zero */
        multi = (I32)laz_decode_symbol(self->dec, &r->m_gpstime_0diff);
        if (multi == 1) {                       /* difference fits in 32 bits */
            r->last_gpstime_diff[r->last] = laz_ic_decompress(&r->ic_gpstime, 0, 0);
            r->last_gpstime[r->last] =
                (U64)((I64)r->last_gpstime[r->last] + r->last_gpstime_diff[r->last]);
            r->multi_extreme_counter[r->last] = 0;
        } else if (multi == 2) {                /* difference is huge */
            r->next = (r->next + 1) & 3;
            r->last_gpstime[r->next] = (U64)(U32)laz_ic_decompress(
                &r->ic_gpstime, (I32)(r->last_gpstime[r->last] >> 32), 8);
            r->last_gpstime[r->next] <<= 32;
            r->last_gpstime[r->next] |= laz_read_int(self->dec);
            r->last = r->next;
            r->last_gpstime_diff[r->last] = 0;
            r->multi_extreme_counter[r->last] = 0;
        } else if (multi > 2) {                 /* switch to another sequence */
            r->last = (r->last + multi - 2) & 3;
            gps11v2_read(self, item, context);
        }
    } else {
        multi = (I32)laz_decode_symbol(self->dec, &r->m_gpstime_multi);
        if (multi == 1) {
            r->last_gpstime[r->last] = (U64)((I64)r->last_gpstime[r->last] +
                laz_ic_decompress(&r->ic_gpstime, r->last_gpstime_diff[r->last], 1));
            r->multi_extreme_counter[r->last] = 0;
        } else if (multi < LASZIP_GPSTIME_MULTI_UNCHANGED) {
            I32 gpstime_diff;
            if (multi == 0) {
                gpstime_diff = laz_ic_decompress(&r->ic_gpstime, 0, 7);
                r->multi_extreme_counter[r->last]++;
                if (r->multi_extreme_counter[r->last] > 3) {
                    r->last_gpstime_diff[r->last] = gpstime_diff;
                    r->multi_extreme_counter[r->last] = 0;
                }
            } else if (multi < LASZIP_GPSTIME_MULTI) {
                gpstime_diff = laz_ic_decompress(&r->ic_gpstime,
                                                 multi * r->last_gpstime_diff[r->last],
                                                 (multi < 10) ? 2 : 3);
            } else if (multi == LASZIP_GPSTIME_MULTI) {
                gpstime_diff = laz_ic_decompress(&r->ic_gpstime,
                    LASZIP_GPSTIME_MULTI * r->last_gpstime_diff[r->last], 4);
                r->multi_extreme_counter[r->last]++;
                if (r->multi_extreme_counter[r->last] > 3) {
                    r->last_gpstime_diff[r->last] = gpstime_diff;
                    r->multi_extreme_counter[r->last] = 0;
                }
            } else {
                multi = LASZIP_GPSTIME_MULTI - multi;
                if (multi > LASZIP_GPSTIME_MULTI_MINUS) {
                    gpstime_diff = laz_ic_decompress(&r->ic_gpstime,
                        multi * r->last_gpstime_diff[r->last], 5);
                } else {
                    gpstime_diff = laz_ic_decompress(&r->ic_gpstime,
                        LASZIP_GPSTIME_MULTI_MINUS * r->last_gpstime_diff[r->last], 6);
                    r->multi_extreme_counter[r->last]++;
                    if (r->multi_extreme_counter[r->last] > 3) {
                        r->last_gpstime_diff[r->last] = gpstime_diff;
                        r->multi_extreme_counter[r->last] = 0;
                    }
                }
            }
            r->last_gpstime[r->last] = (U64)((I64)r->last_gpstime[r->last] + gpstime_diff);
        } else if (multi == LASZIP_GPSTIME_MULTI_CODE_FULL) {
            r->next = (r->next + 1) & 3;
            r->last_gpstime[r->next] = (U64)(U32)laz_ic_decompress(
                &r->ic_gpstime, (I32)(r->last_gpstime[r->last] >> 32), 8);
            r->last_gpstime[r->next] <<= 32;
            r->last_gpstime[r->next] |= laz_read_int(self->dec);
            r->last = r->next;
            r->last_gpstime_diff[r->last] = 0;
            r->multi_extreme_counter[r->last] = 0;
        } else if (multi >= LASZIP_GPSTIME_MULTI_CODE_FULL) {
            r->last = (r->last + multi - LASZIP_GPSTIME_MULTI_CODE_FULL) & 3;
            gps11v2_read(self, item, context);
        }
    }
    memcpy(item, &r->last_gpstime[r->last], 8);
}

static void gps11v2_destroy(LazReadItem *self)
{
    Gpstime11v2 *r = (Gpstime11v2 *)self;
    laz_symbol_model_free(&r->m_gpstime_multi);
    laz_symbol_model_free(&r->m_gpstime_0diff);
    laz_ic_free(&r->ic_gpstime);
}

LazReadItem *laz_readitem_v2_gpstime11(LazDecoder *dec)
{
    Gpstime11v2 *r = (Gpstime11v2 *)calloc(1, sizeof(Gpstime11v2));
    if (!r) return NULL;
    r->base.read = gps11v2_read;
    r->base.init = gps11v2_init;
    r->base.destroy = gps11v2_destroy;
    r->base.dec = dec;

    laz_symbol_model_setup(&r->m_gpstime_multi, LASZIP_GPSTIME_MULTI_TOTAL, LAZ_FALSE);
    laz_symbol_model_setup(&r->m_gpstime_0diff, 6, LAZ_FALSE);
    laz_ic_setup(&r->ic_gpstime, dec, 32, 9, 8, 0);
    return (LazReadItem *)r;
}

/* ========================================================== RGB12 v2 ===== */

typedef struct {
    LazReadItem base;
    LazSymbolModel m_byte_used;
    LazSymbolModel m_rgb_diff[6];
    U16 last_item[3];
} Rgb12v2;

static BOOL rgb12v2_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Rgb12v2 *r = (Rgb12v2 *)self;
    U32 i;
    (void)context;
    laz_symbol_model_init(&r->m_byte_used, NULL);
    for (i = 0; i < 6; i++) laz_symbol_model_init(&r->m_rgb_diff[i], NULL);
    memcpy(r->last_item, item, 6);
    return LAZ_TRUE;
}

static void rgb12v2_read(LazReadItem *self, U8 *item, U32 *context)
{
    Rgb12v2 *r = (Rgb12v2 *)self;
    U16 *out = (U16 *)item;
    U16 *last = r->last_item;
    U8 corr;
    I32 diff = 0;
    U32 sym;

    (void)context;
    sym = laz_decode_symbol(self->dec, &r->m_byte_used);

    /* lower byte of red */
    if (sym & (1 << 0)) {
        corr = (U8)laz_decode_symbol(self->dec, &r->m_rgb_diff[0]);
        out[0] = (U16)U8_FOLD(corr + (last[0] & 255));
    } else {
        out[0] = last[0] & 0xFF;
    }
    /* upper byte of red */
    if (sym & (1 << 1)) {
        corr = (U8)laz_decode_symbol(self->dec, &r->m_rgb_diff[1]);
        out[0] |= (U16)(((U16)U8_FOLD(corr + (last[0] >> 8))) << 8);
    } else {
        out[0] |= (last[0] & 0xFF00);
    }

    /* bit 6 says whether green and blue differ from red at all */
    if (sym & (1 << 6)) {
        diff = (out[0] & 0x00FF) - (last[0] & 0x00FF);
        if (sym & (1 << 2)) {
            corr = (U8)laz_decode_symbol(self->dec, &r->m_rgb_diff[2]);
            out[1] = (U16)U8_FOLD(corr + U8_CLAMP(diff + (last[1] & 255)));
        } else {
            out[1] = last[1] & 0xFF;
        }
        if (sym & (1 << 4)) {
            corr = (U8)laz_decode_symbol(self->dec, &r->m_rgb_diff[4]);
            diff = (diff + ((out[1] & 0x00FF) - (last[1] & 0x00FF))) / 2;
            out[2] = (U16)U8_FOLD(corr + U8_CLAMP(diff + (last[2] & 255)));
        } else {
            out[2] = last[2] & 0xFF;
        }
        diff = (out[0] >> 8) - (last[0] >> 8);
        if (sym & (1 << 3)) {
            corr = (U8)laz_decode_symbol(self->dec, &r->m_rgb_diff[3]);
            out[1] |= (U16)(((U16)U8_FOLD(corr + U8_CLAMP(diff + (last[1] >> 8)))) << 8);
        } else {
            out[1] |= (last[1] & 0xFF00);
        }
        if (sym & (1 << 5)) {
            corr = (U8)laz_decode_symbol(self->dec, &r->m_rgb_diff[5]);
            diff = (diff + ((out[1] >> 8) - (last[1] >> 8))) / 2;
            out[2] |= (U16)(((U16)U8_FOLD(corr + U8_CLAMP(diff + (last[2] >> 8)))) << 8);
        } else {
            out[2] |= (last[2] & 0xFF00);
        }
    } else {
        out[1] = out[0];
        out[2] = out[0];
    }
    memcpy(r->last_item, item, 6);
}

static void rgb12v2_destroy(LazReadItem *self)
{
    Rgb12v2 *r = (Rgb12v2 *)self;
    U32 i;
    laz_symbol_model_free(&r->m_byte_used);
    for (i = 0; i < 6; i++) laz_symbol_model_free(&r->m_rgb_diff[i]);
}

LazReadItem *laz_readitem_v2_rgb12(LazDecoder *dec)
{
    Rgb12v2 *r = (Rgb12v2 *)calloc(1, sizeof(Rgb12v2));
    U32 i;
    if (!r) return NULL;
    r->base.read = rgb12v2_read;
    r->base.init = rgb12v2_init;
    r->base.destroy = rgb12v2_destroy;
    r->base.dec = dec;
    laz_symbol_model_setup(&r->m_byte_used, 128, LAZ_FALSE);
    for (i = 0; i < 6; i++) laz_symbol_model_setup(&r->m_rgb_diff[i], 256, LAZ_FALSE);
    return (LazReadItem *)r;
}

/* =========================================================== BYTE v2 ===== */

typedef struct {
    LazReadItem base;
    U32 number;
    LazSymbolModel *m_byte;   /* [number] */
    U8 *last_item;            /* [number] */
} Bytev2;

static BOOL bytev2_init(LazReadItem *self, const U8 *item, U32 *context)
{
    Bytev2 *r = (Bytev2 *)self;
    U32 i;
    (void)context;
    for (i = 0; i < r->number; i++) laz_symbol_model_init(&r->m_byte[i], NULL);
    memcpy(r->last_item, item, r->number);
    return LAZ_TRUE;
}

static void bytev2_read(LazReadItem *self, U8 *item, U32 *context)
{
    Bytev2 *r = (Bytev2 *)self;
    U32 i;
    (void)context;
    for (i = 0; i < r->number; i++) {
        I32 value = r->last_item[i] + (I32)laz_decode_symbol(self->dec, &r->m_byte[i]);
        item[i] = (U8)U8_FOLD(value);
    }
    memcpy(r->last_item, item, r->number);
}

static void bytev2_destroy(LazReadItem *self)
{
    Bytev2 *r = (Bytev2 *)self;
    laz_symbol_models_free(r->m_byte, r->number);
    free(r->last_item);
}

LazReadItem *laz_readitem_v2_byte(LazDecoder *dec, U32 number)
{
    Bytev2 *r = (Bytev2 *)calloc(1, sizeof(Bytev2));
    if (!r) return NULL;
    r->base.read = bytev2_read;
    r->base.init = bytev2_init;
    r->base.destroy = bytev2_destroy;
    r->base.dec = dec;
    r->number = number;
    r->m_byte = laz_symbol_models_new(number, 256, LAZ_FALSE);
    r->last_item = (U8 *)calloc(number ? number : 1, 1);
    if (!r->m_byte || !r->last_item) { bytev2_destroy((LazReadItem *)r); free(r); return NULL; }
    return (LazReadItem *)r;
}
