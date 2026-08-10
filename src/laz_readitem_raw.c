/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * Raw (uncompressed) item readers -- ported from LASzip's lasreaditemraw.hpp.
 *
 * This is one of the two places the on-disk byte order and the host's meet;
 * the other is laz_writeitem_raw.c. Everything downstream -- the compressed
 * coders, the point buffer, the array API -- works in host order, so on a
 * big-endian host the conversion is confined to these two files. LASzip draws
 * the same line, with a separate _BE class per item where lazpy has a swap.
 *
 * Most items are a byte copy plus a swap from laz_item.h, because the LazPoint
 * layout matches the on-disk layout at that offset. POINT14 is the exception:
 * the 30-byte LAS 1.4 record has to be scattered into both the legacy and
 * extended fields of LazPoint, exactly as LASreadItemRaw_POINT14_LE does, so
 * that a point read from an uncompressed file is indistinguishable from a
 * decompressed one. It reads every field explicitly and so needs no swap.
 */
#include "laz_readitem.h"

typedef struct {
    LazReadItem base;
    U32 number;
    U8 buffer[30];
} RawReader;

static BOOL raw_init_noop(LazReadItem *self, const U8 *item, U32 *context)
{
    (void)self; (void)item; (void)context;
    return LAZ_TRUE;
}

static RawReader *raw_new(LazStream *in, void (*read)(LazReadItem *, U8 *, U32 *), U32 number)
{
    RawReader *r = (RawReader *)calloc(1, sizeof(RawReader));
    if (!r) return NULL;
    r->base.read = read;
    r->base.init = raw_init_noop;
    r->base.chunk_sizes = NULL;
    r->base.destroy = NULL;
    r->base.instream = in;
    r->number = number;
    return r;
}

/*
 * Read a fixed-size record straight into the point buffer, then put it in host
 * order. The little-endian body simply never mentions `swap`, so that host
 * gets the plain byte copy it always had and needs no no-op stand-ins for the
 * swaps in laz_item.h.
 */
#if LAZ_BIG_ENDIAN
#define RAW_FIXED_READER(name, nbytes, swap)                                  \
    static void name(LazReadItem *self, U8 *item, U32 *context)               \
    {                                                                         \
        (void)context;                                                        \
        laz_stream_get_bytes(self->instream, item, nbytes);                   \
        swap(item);                                                           \
    }
#else
#define RAW_FIXED_READER(name, nbytes, swap)                                  \
    static void name(LazReadItem *self, U8 *item, U32 *context)               \
    {                                                                         \
        (void)context;                                                        \
        laz_stream_get_bytes(self->instream, item, nbytes);                   \
    }
#endif

RAW_FIXED_READER(raw_read_point10,     20, laz_swap_point10)
RAW_FIXED_READER(raw_read_gpstime11,    8, laz_swap_gpstime11)
RAW_FIXED_READER(raw_read_rgb12,        6, laz_swap_rgb12)
RAW_FIXED_READER(raw_read_rgbnir14,     8, laz_swap_rgbnir14)
RAW_FIXED_READER(raw_read_wavepacket13, 29, laz_swap_wavepacket13)

static void raw_read_byte(LazReadItem *self, U8 *item, U32 *context)
{
    (void)context;
    laz_stream_get_bytes(self->instream, item, ((RawReader *)self)->number);
}

/* Field accessors for the packed 30-byte LAS 1.4 point record -- the on-disk
 * layout, which is not the LASpoint14 layout the P14_* macros in laz_item.h
 * describe. This is the one place the two meet. */
#define REC14_X(b)                 ((I32)laz_le_get32((b) + 0))
#define REC14_Y(b)                 ((I32)laz_le_get32((b) + 4))
#define REC14_Z(b)                 ((I32)laz_le_get32((b) + 8))
#define REC14_INTENSITY(b)         (laz_le_get16((b) + 12))
#define REC14_RETURN_NUMBER(b)     ((b)[14] & 0x0F)
#define REC14_NUMBER_OF_RETURNS(b) (((b)[14] >> 4) & 0x0F)
#define REC14_CLASS_FLAGS(b)       ((b)[15] & 0x0F)
#define REC14_SCANNER_CHANNEL(b)   (((b)[15] >> 4) & 0x03)
#define REC14_SCAN_DIR_FLAG(b)     (((b)[15] >> 6) & 0x01)
#define REC14_EDGE_OF_FLIGHT(b)    (((b)[15] >> 7) & 0x01)
#define REC14_CLASSIFICATION(b)    ((b)[16])
#define REC14_USER_DATA(b)         ((b)[17])
#define REC14_SCAN_ANGLE(b)        ((I16)laz_le_get16((b) + 18))
#define REC14_POINT_SOURCE_ID(b)   (laz_le_get16((b) + 20))
#define REC14_GPS_TIME(b)          (laz_le_get_f64((b) + 22))

static void raw_read_point14(LazReadItem *self, U8 *item, U32 *context)
{
    RawReader *r = (RawReader *)self;
    U8 *b = r->buffer;
    LazPoint *p = (LazPoint *)item;
    U8 return_number, number_of_returns;

    (void)context;
    laz_stream_get_bytes(self->instream, b, 30);

    p->X = REC14_X(b);
    p->Y = REC14_Y(b);
    p->Z = REC14_Z(b);
    p->intensity = REC14_INTENSITY(b);

    return_number = REC14_RETURN_NUMBER(b);
    number_of_returns = REC14_NUMBER_OF_RETURNS(b);

    /* the legacy 3-bit fields saturate rather than wrap */
    if (number_of_returns > 7) {
        if (return_number > 6) {
            laz_point_set_return_number(
                p, (return_number >= number_of_returns) ? 7 : 6);
        } else {
            laz_point_set_return_number(p, return_number);
        }
        laz_point_set_number_of_returns(p, 7);
    } else {
        laz_point_set_return_number(p, return_number);
        laz_point_set_number_of_returns(p, number_of_returns);
    }

    laz_point_set_scan_direction_flag(p, REC14_SCAN_DIR_FLAG(b));
    laz_point_set_edge_of_flight_line(p, REC14_EDGE_OF_FLIGHT(b));

    /* legacy classification byte: flags in the top 3 bits, class in the low 5 */
    {
        U8 legacy = (U8)((REC14_CLASS_FLAGS(b) << 5) & 0xE0);
        U8 cls = REC14_CLASSIFICATION(b);
        if (cls < 32) legacy |= cls;
        p->classification_bits = legacy;
    }

    p->scan_angle_rank = I8_CLAMP(I16_QUANTIZE(0.006f * REC14_SCAN_ANGLE(b)));
    p->user_data = REC14_USER_DATA(b);
    p->point_source_ID = REC14_POINT_SOURCE_ID(b);

    laz_point_set_extended_scanner_channel(p, REC14_SCANNER_CHANNEL(b));
    laz_point_set_extended_classification_flags(p, REC14_CLASS_FLAGS(b));
    p->extended_classification = REC14_CLASSIFICATION(b);
    laz_point_set_extended_return_number(p, return_number);
    laz_point_set_extended_number_of_returns(p, number_of_returns);
    p->extended_scan_angle = REC14_SCAN_ANGLE(b);

    p->gps_time = REC14_GPS_TIME(b);
}

LazReadItem *laz_readitem_raw_point10(LazStream *in)
{ return (LazReadItem *)raw_new(in, raw_read_point10, 0); }

LazReadItem *laz_readitem_raw_gpstime11(LazStream *in)
{ return (LazReadItem *)raw_new(in, raw_read_gpstime11, 0); }

LazReadItem *laz_readitem_raw_rgb12(LazStream *in)
{ return (LazReadItem *)raw_new(in, raw_read_rgb12, 0); }

LazReadItem *laz_readitem_raw_rgbnir14(LazStream *in)
{ return (LazReadItem *)raw_new(in, raw_read_rgbnir14, 0); }

LazReadItem *laz_readitem_raw_wavepacket13(LazStream *in)
{ return (LazReadItem *)raw_new(in, raw_read_wavepacket13, 0); }

LazReadItem *laz_readitem_raw_byte(LazStream *in, U32 number)
{ return (LazReadItem *)raw_new(in, raw_read_byte, number); }

LazReadItem *laz_readitem_raw_point14(LazStream *in)
{ return (LazReadItem *)raw_new(in, raw_read_point14, 0); }

LazReadItem *laz_readitem_new_raw(const LazItem *item, LazStream *in)
{
    switch (item->type) {
    case LAZ_ITEM_POINT10:      return laz_readitem_raw_point10(in);
    case LAZ_ITEM_GPSTIME11:    return laz_readitem_raw_gpstime11(in);
    case LAZ_ITEM_RGB12:
    case LAZ_ITEM_RGB14:        return laz_readitem_raw_rgb12(in);
    case LAZ_ITEM_RGBNIR14:     return laz_readitem_raw_rgbnir14(in);
    case LAZ_ITEM_WAVEPACKET13:
    case LAZ_ITEM_WAVEPACKET14: return laz_readitem_raw_wavepacket13(in);
    case LAZ_ITEM_BYTE:
    case LAZ_ITEM_BYTE14:       return laz_readitem_raw_byte(in, item->size);
    case LAZ_ITEM_POINT14:      return laz_readitem_raw_point14(in);
    default:                    return NULL;
    }
}
