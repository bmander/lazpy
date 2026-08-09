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
 * big-endian host rather than silently mis-encoding. Every item here is a
 * straight byte copy, because the LazPoint layout already matches the on-disk
 * layout at that offset. POINT14, the one item that needs gathering rather
 * than copying, belongs with the LAS 1.4 writers.
 *
 * These are not only for uncompressed files: every compressed chunk begins
 * with one raw point, which is what seeds the predictors of the compressed
 * writers that follow.
 */
#include "laz_writeitem.h"

typedef struct {
    LazWriteItem base;
    U32 number;
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
