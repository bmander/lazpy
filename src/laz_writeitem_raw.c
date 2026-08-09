/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * Raw (uncompressed) item writers -- ported from LASzip's laswriteitemraw.hpp.
 *
 * Only the little-endian variants are ported; the module refuses to load on a
 * big-endian host rather than silently mis-encoding.
 *
 * Most items are a straight byte copy because the LazPoint layout matches the
 * on-disk layout at that offset. POINT14 is the exception: the 30-byte LAS 1.4
 * record has to be gathered from both the legacy and extended fields of
 * LazPoint, the exact inverse of raw_read_point14 in laz_readitem_raw.c.
 *
 * These are not only for uncompressed files: every compressed chunk begins
 * with one raw point, which is what seeds the predictors of the compressed
 * writers that follow.
 */
#include "laz_writeitem.h"

typedef struct {
    LazWriteItem base;
    U32 number;
    U8 buffer[30];
} RawWriter;

static BOOL raw_init_noop(LazWriteItem *self, const U8 *item, U32 *context)
{
    (void)self; (void)item; (void)context;
    return LAZ_TRUE;
}

static RawWriter *raw_new(LazOutStream *out,
                          BOOL (*write)(LazWriteItem *, const U8 *, U32 *), U32 number)
{
    RawWriter *w = (RawWriter *)calloc(1, sizeof(RawWriter));
    if (!w) return NULL;
    w->base.write = write;
    w->base.init = raw_init_noop;
    w->base.chunk_sizes = NULL;
    w->base.chunk_bytes = NULL;
    w->base.destroy = NULL;
    w->base.outstream = out;
    w->number = number;
    return w;
}

#define RAW_FIXED_WRITER(name, nbytes)                                        \
    static BOOL name(LazWriteItem *self, const U8 *item, U32 *context)        \
    {                                                                         \
        (void)context;                                                        \
        laz_outstream_put_bytes(self->outstream, item, nbytes);               \
        return LAZ_TRUE;                                                      \
    }

RAW_FIXED_WRITER(raw_write_point10,     20)
RAW_FIXED_WRITER(raw_write_gpstime11,    8)
RAW_FIXED_WRITER(raw_write_rgb12,        6)
RAW_FIXED_WRITER(raw_write_rgbnir14,     8)
RAW_FIXED_WRITER(raw_write_wavepacket13, 29)

static BOOL raw_write_byte(LazWriteItem *self, const U8 *item, U32 *context)
{
    (void)context;
    laz_outstream_put_bytes(self->outstream, item, ((RawWriter *)self)->number);
    return LAZ_TRUE;
}

/*
 * Gathers the LAS 1.4 fields back into the 30-byte record, undoing the scatter
 * raw_read_point14 performs. A point that never came from a 1.4 source has
 * extended_point_type clear, and then the extended fields are derived from the
 * legacy ones instead -- which is lossy in the same places the reader's
 * saturation is, and for the same reason.
 */
static BOOL raw_write_point14(LazWriteItem *self, const U8 *item, U32 *context)
{
    RawWriter *w = (RawWriter *)self;
    U8 *b = w->buffer;
    const LazPoint *p = (const LazPoint *)item;
    U8 classification, class_flags, scanner_channel;
    U8 return_number, number_of_returns;
    I16 scan_angle;

    (void)context;
    memcpy(b + 0, item, 14);            /* X, Y, Z, intensity */

    /* the low three flag bits are the legacy ones either way; only overlap,
     * the fourth, exists solely in the extended field */
    class_flags = (U8)(p->synthetic_flag | (p->keypoint_flag << 1) |
                       (p->withheld_flag << 2));
    classification = p->classification;
    if (p->extended_point_type) {
        class_flags |= (U8)(p->extended_classification_flags & 0x08);
        if (classification == 0) classification = p->extended_classification;
        scanner_channel = p->extended_scanner_channel;
        return_number = p->extended_return_number;
        number_of_returns = p->extended_number_of_returns;
        scan_angle = p->extended_scan_angle;
    } else {
        scanner_channel = 0;
        return_number = p->return_number;
        number_of_returns = p->number_of_returns;
        scan_angle = I16_QUANTIZE(p->scan_angle_rank / 0.006f);
    }

    b[14] = (U8)(return_number | (number_of_returns << 4));
    b[15] = (U8)(class_flags | (scanner_channel << 4) |
                 (p->scan_direction_flag << 6) | (p->edge_of_flight_line << 7));
    b[16] = classification;
    b[17] = p->user_data;
    memcpy(b + 18, &scan_angle, 2);
    memcpy(b + 20, &p->point_source_ID, 2);
    memcpy(b + 22, &p->gps_time, 8);

    laz_outstream_put_bytes(self->outstream, b, 30);
    return LAZ_TRUE;
}

LazWriteItem *laz_writeitem_raw_point10(LazOutStream *out)
{ return (LazWriteItem *)raw_new(out, raw_write_point10, 0); }

LazWriteItem *laz_writeitem_raw_gpstime11(LazOutStream *out)
{ return (LazWriteItem *)raw_new(out, raw_write_gpstime11, 0); }

LazWriteItem *laz_writeitem_raw_rgb12(LazOutStream *out)
{ return (LazWriteItem *)raw_new(out, raw_write_rgb12, 0); }

LazWriteItem *laz_writeitem_raw_rgbnir14(LazOutStream *out)
{ return (LazWriteItem *)raw_new(out, raw_write_rgbnir14, 0); }

LazWriteItem *laz_writeitem_raw_wavepacket13(LazOutStream *out)
{ return (LazWriteItem *)raw_new(out, raw_write_wavepacket13, 0); }

LazWriteItem *laz_writeitem_raw_byte(LazOutStream *out, U32 number)
{ return (LazWriteItem *)raw_new(out, raw_write_byte, number); }

LazWriteItem *laz_writeitem_raw_point14(LazOutStream *out)
{ return (LazWriteItem *)raw_new(out, raw_write_point14, 0); }
