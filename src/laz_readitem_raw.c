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
 * Only the little-endian variants are ported; the module refuses to load on a
 * big-endian host rather than silently mis-decoding.
 *
 * Most items are a straight byte copy because the LazPoint layout matches the
 * on-disk layout at that offset. POINT14 is the exception: the 30-byte LAS 1.4
 * record has to be scattered into both the legacy and extended fields of
 * LazPoint, exactly as LASreadItemRaw_POINT14_LE does, so that a point read
 * from an uncompressed file is indistinguishable from a decompressed one.
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

#define RAW_FIXED_READER(name, nbytes)                                        \
    static void name(LazReadItem *self, U8 *item, U32 *context)               \
    {                                                                         \
        (void)context;                                                        \
        laz_stream_get_bytes(self->instream, item, nbytes);                   \
    }

RAW_FIXED_READER(raw_read_point10,     20)
RAW_FIXED_READER(raw_read_gpstime11,    8)
RAW_FIXED_READER(raw_read_rgb12,        6)
RAW_FIXED_READER(raw_read_rgbnir14,     8)
RAW_FIXED_READER(raw_read_wavepacket13, 29)

static void raw_read_byte(LazReadItem *self, U8 *item, U32 *context)
{
    (void)context;
    laz_stream_get_bytes(self->instream, item, ((RawReader *)self)->number);
}

/* Field accessors for the packed 30-byte LAS 1.4 point record. */
#define P14_X(b)                 ((I32)((U32)(b)[0] | ((U32)(b)[1] << 8) | ((U32)(b)[2] << 16) | ((U32)(b)[3] << 24)))
#define P14_Y(b)                 ((I32)((U32)(b)[4] | ((U32)(b)[5] << 8) | ((U32)(b)[6] << 16) | ((U32)(b)[7] << 24)))
#define P14_Z(b)                 ((I32)((U32)(b)[8] | ((U32)(b)[9] << 8) | ((U32)(b)[10] << 16) | ((U32)(b)[11] << 24)))
#define P14_INTENSITY(b)         ((U16)((b)[12] | ((b)[13] << 8)))
#define P14_RETURN_NUMBER(b)     ((b)[14] & 0x0F)
#define P14_NUMBER_OF_RETURNS(b) (((b)[14] >> 4) & 0x0F)
#define P14_CLASS_FLAGS(b)       ((b)[15] & 0x0F)
#define P14_SCANNER_CHANNEL(b)   (((b)[15] >> 4) & 0x03)
#define P14_SCAN_DIR_FLAG(b)     (((b)[15] >> 6) & 0x01)
#define P14_EDGE_OF_FLIGHT(b)    (((b)[15] >> 7) & 0x01)
#define P14_CLASSIFICATION(b)    ((b)[16])
#define P14_USER_DATA(b)         ((b)[17])
#define P14_SCAN_ANGLE(b)        ((I16)((U16)((b)[18] | ((b)[19] << 8))))
#define P14_POINT_SOURCE_ID(b)   ((U16)((b)[20] | ((b)[21] << 8)))

static void raw_read_point14(LazReadItem *self, U8 *item, U32 *context)
{
    RawReader *r = (RawReader *)self;
    U8 *b = r->buffer;
    LazPoint *p = (LazPoint *)item;
    U8 return_number, number_of_returns;

    (void)context;
    laz_stream_get_bytes(self->instream, b, 30);

    p->X = P14_X(b);
    p->Y = P14_Y(b);
    p->Z = P14_Z(b);
    p->intensity = P14_INTENSITY(b);

    return_number = P14_RETURN_NUMBER(b);
    number_of_returns = P14_NUMBER_OF_RETURNS(b);

    /* the legacy 3-bit fields saturate rather than wrap */
    if (number_of_returns > 7) {
        if (return_number > 6) {
            p->return_number = (return_number >= number_of_returns) ? 7 : 6;
        } else {
            p->return_number = return_number;
        }
        p->number_of_returns = 7;
    } else {
        p->return_number = return_number;
        p->number_of_returns = number_of_returns;
    }

    p->scan_direction_flag = P14_SCAN_DIR_FLAG(b);
    p->edge_of_flight_line = P14_EDGE_OF_FLIGHT(b);

    /* legacy classification byte: flags in the top 3 bits, class in the low 5 */
    {
        U8 legacy = (U8)((P14_CLASS_FLAGS(b) << 5) & 0xE0);
        U8 cls = P14_CLASSIFICATION(b);
        if (cls < 32) legacy |= cls;
        ((U8 *)item)[15] = legacy;
    }

    p->scan_angle_rank = I8_CLAMP(I16_QUANTIZE(0.006f * P14_SCAN_ANGLE(b)));
    p->user_data = P14_USER_DATA(b);
    p->point_source_ID = P14_POINT_SOURCE_ID(b);

    p->extended_scanner_channel = P14_SCANNER_CHANNEL(b);
    p->extended_classification_flags = P14_CLASS_FLAGS(b);
    p->extended_classification = P14_CLASSIFICATION(b);
    p->extended_return_number = return_number;
    p->extended_number_of_returns = number_of_returns;
    p->extended_scan_angle = P14_SCAN_ANGLE(b);

    memcpy(&p->gps_time, &b[22], 8);
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
