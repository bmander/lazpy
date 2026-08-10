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
 * The mirror of laz_readitem_raw.c, and with it the only place the host byte
 * order and the on-disk one meet; see the comment there.
 *
 * Most items are a byte copy because the LazPoint layout matches the on-disk
 * layout at that offset -- on a big-endian host, a copy into a scratch buffer
 * that is then swapped, through the same swaps the reader uses, since a byte
 * swap is its own inverse. POINT14 is the exception: the 30-byte LAS 1.4
 * record has to be gathered from both the legacy and extended fields of
 * LazPoint, the exact inverse of raw_read_point14 in laz_readitem_raw.c, and
 * it writes every field explicitly so it needs no swap.
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

/*
 * The mirror of RAW_FIXED_READER. The item is const and cannot be swapped
 * where it lies, so the big-endian body copies it into the writer's scratch
 * buffer and swaps that; the little-endian body never mentions `swap` and puts
 * the item straight out, as it always did.
 */
#if LAZ_BIG_ENDIAN
#define RAW_FIXED_WRITER(name, nbytes, swap)                                  \
    static BOOL name(LazWriteItem *self, const U8 *item, U32 *context)        \
    {                                                                         \
        U8 *buf = ((RawWriter *)self)->buffer;                                \
        (void)context;                                                        \
        memcpy(buf, item, nbytes);                                            \
        swap(buf);                                                            \
        laz_outstream_put_bytes(self->outstream, buf, nbytes);                \
        return LAZ_TRUE;                                                      \
    }
#else
#define RAW_FIXED_WRITER(name, nbytes, swap)                                  \
    static BOOL name(LazWriteItem *self, const U8 *item, U32 *context)        \
    {                                                                         \
        (void)context;                                                        \
        laz_outstream_put_bytes(self->outstream, item, nbytes);               \
        return LAZ_TRUE;                                                      \
    }
#endif

RAW_FIXED_WRITER(raw_write_point10,     20, laz_swap_point10)
RAW_FIXED_WRITER(raw_write_gpstime11,    8, laz_swap_gpstime11)
RAW_FIXED_WRITER(raw_write_rgb12,        6, laz_swap_rgb12)
RAW_FIXED_WRITER(raw_write_rgbnir14,     8, laz_swap_rgbnir14)
RAW_FIXED_WRITER(raw_write_wavepacket13, 29, laz_swap_wavepacket13)

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
    laz_le_put32(b + 0, (U32)p->X);
    laz_le_put32(b + 4, (U32)p->Y);
    laz_le_put32(b + 8, (U32)p->Z);
    laz_le_put16(b + 12, p->intensity);

    /* the low three flag bits are the legacy ones either way; only overlap,
     * the fourth, exists solely in the extended field */
    class_flags = (U8)(laz_point_synthetic_flag(p) |
                       (laz_point_keypoint_flag(p) << 1) |
                       (laz_point_withheld_flag(p) << 2));
    classification = laz_point_classification(p);
    if (laz_point_extended_point_type(p)) {
        class_flags |= (U8)(laz_point_extended_classification_flags(p) & 0x08);
        if (classification == 0) classification = p->extended_classification;
        scanner_channel = laz_point_extended_scanner_channel(p);
        return_number = laz_point_extended_return_number(p);
        number_of_returns = laz_point_extended_number_of_returns(p);
        scan_angle = p->extended_scan_angle;
    } else {
        scanner_channel = 0;
        return_number = laz_point_return_number(p);
        number_of_returns = laz_point_number_of_returns(p);
        scan_angle = I16_QUANTIZE(p->scan_angle_rank / 0.006f);
    }

    b[14] = (U8)(return_number | (number_of_returns << 4));
    b[15] = (U8)(class_flags | (scanner_channel << 4) |
                 (laz_point_scan_direction_flag(p) << 6) |
                 (laz_point_edge_of_flight_line(p) << 7));
    b[16] = classification;
    b[17] = p->user_data;
    laz_le_put16(b + 18, (U16)scan_angle);
    laz_le_put16(b + 20, p->point_source_ID);
    laz_le_put_f64(b + 22, p->gps_time);

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
