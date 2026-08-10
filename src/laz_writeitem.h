/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_writeitem.h -- common interface for the classes that encode the items
 * making up a point. The mirror image of laz_readitem.h, ported from LASzip's
 * laswriteitem.hpp, and deliberately the same shape: a small vtable at the
 * head of each concrete writer, so a concrete writer can always be cast to
 * LazWriteItem*.
 *
 * One asymmetry: write() reports a failed point through its return value,
 * where read() returns void and the reader raises a flag the container picks
 * up afterwards. That is the read side's established convention -- see
 * overran() and alloc_failed in laz_readitem.h -- and it exists because a
 * writer is driven a point at a time by a caller who can stop, while a reader
 * is on the decode path LASzip threw exceptions from.
 *
 * `item` points at this item's bytes inside the caller's point -- for the
 * compressed writers that is the same host-order layout the matching reader
 * produces, so writer(reader(x)) is the identity on the byte stream.
 *
 * Every writer pairs with the reader of the same name: laz_writeitem_v2_rgb12
 * is the exact inverse of laz_readitem_v2_rgb12, model update for model
 * update. The predictors they share live in laz_item.h.
 */
#ifndef LAZ_WRITEITEM_H
#define LAZ_WRITEITEM_H

#include "laz_types.h"
#include "laz_stream.h"
#include "laz_item.h"

typedef struct LazWriteItem LazWriteItem;

struct LazWriteItem {
    BOOL (*write)(LazWriteItem *self, const U8 *item, U32 *context);
    /* compressed writers only: seed state from the chunk's first (raw) point */
    BOOL (*init)(LazWriteItem *self, const U8 *item, U32 *context);
    /*
     * Layered (v3/v4) writers only, and always as a pair: a layered writer
     * buffers each layer separately, so closing a chunk takes two passes over
     * the writers -- every writer emits its per-layer byte counts, then every
     * writer emits the layers themselves. That is the order laz_readpoint.c
     * reads them back in, and it is why one hook cannot do the job.
     */
    BOOL (*chunk_sizes)(LazWriteItem *self);
    BOOL (*chunk_bytes)(LazWriteItem *self);
    void (*destroy)(LazWriteItem *self);

    LazOutStream *outstream;  /* raw writers: borrowed */
    LazEncoder *enc;          /* compressed writers: borrowed */
};

static inline void laz_writeitem_destroy(LazWriteItem *w)
{
    if (!w) return;
    if (w->destroy) w->destroy(w);
    free(w);
}

/* --- raw writers (laswriteitemraw.hpp): the host/on-disk byte-order boundary --- */
LazWriteItem *laz_writeitem_raw_point10(LazOutStream *out);
LazWriteItem *laz_writeitem_raw_gpstime11(LazOutStream *out);
LazWriteItem *laz_writeitem_raw_rgb12(LazOutStream *out);
LazWriteItem *laz_writeitem_raw_rgbnir14(LazOutStream *out);
LazWriteItem *laz_writeitem_raw_wavepacket13(LazOutStream *out);
LazWriteItem *laz_writeitem_raw_byte(LazOutStream *out, U32 number);
/* gathers the LAS 1.4 fields of a LazPoint back into the 30-byte record, the
 * inverse of laz_readitem_raw_point14 */
LazWriteItem *laz_writeitem_raw_point14(LazOutStream *out);

/* --- v1 compressed writers (laswriteitemcompressed_v1.cpp) --- */
LazWriteItem *laz_writeitem_v1_point10(LazEncoder *enc);
LazWriteItem *laz_writeitem_v1_gpstime11(LazEncoder *enc);
LazWriteItem *laz_writeitem_v1_rgb12(LazEncoder *enc);
LazWriteItem *laz_writeitem_v1_byte(LazEncoder *enc, U32 number);
LazWriteItem *laz_writeitem_v1_wavepacket13(LazEncoder *enc);

/* --- v2 compressed writers (laswriteitemcompressed_v2.cpp) --- */
LazWriteItem *laz_writeitem_v2_point10(LazEncoder *enc);
LazWriteItem *laz_writeitem_v2_gpstime11(LazEncoder *enc);
LazWriteItem *laz_writeitem_v2_rgb12(LazEncoder *enc);
LazWriteItem *laz_writeitem_v2_byte(LazEncoder *enc, U32 number);

/*
 * --- v3/v4 layered writers (laswriteitemcompressed_v3.cpp / _v4.cpp) ---
 *
 * `enc` is not encoded through: these writers hold their own encoder per
 * layer, and take the shared one only to reach the chunk's output stream,
 * exactly as the layered readers take the shared decoder only for its input
 * stream.
 */
LazWriteItem *laz_writeitem_v3_point14(LazEncoder *enc);
LazWriteItem *laz_writeitem_v3_rgb14(LazEncoder *enc);
LazWriteItem *laz_writeitem_v3_rgbnir14(LazEncoder *enc);
LazWriteItem *laz_writeitem_v3_wavepacket14(LazEncoder *enc);
LazWriteItem *laz_writeitem_v3_byte14(LazEncoder *enc, U32 number);

LazWriteItem *laz_writeitem_v4_point14(LazEncoder *enc);
LazWriteItem *laz_writeitem_v4_rgb14(LazEncoder *enc);
LazWriteItem *laz_writeitem_v4_rgbnir14(LazEncoder *enc);
LazWriteItem *laz_writeitem_v4_wavepacket14(LazEncoder *enc);
LazWriteItem *laz_writeitem_v4_byte14(LazEncoder *enc, U32 number);

/*
 * Picks the writer for one item of a LASzip VLR, mirroring laz_readitem_new_raw
 * and make_compressed_reader on the read side. Returns NULL for a type or
 * version with no writer, which the caller reports.
 *
 * item->version selects the encoding, exactly as the VLR declares it: 0 means
 * the item is stored raw, and WAVEPACKET13 stays at 1 even inside a v2 file.
 */
LazWriteItem *laz_writeitem_new_raw(const LazItem *item, LazOutStream *out);
LazWriteItem *laz_writeitem_new_compressed(const LazItem *item, LazEncoder *enc);

#endif /* LAZ_WRITEITEM_H */
