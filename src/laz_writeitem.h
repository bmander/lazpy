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
 * `item` points at this item's bytes inside the caller's point -- for the
 * compressed writers that is the same little-endian layout the matching reader
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
    /* layered (v3/v4) writers only: emit this item's per-layer byte counts */
    BOOL (*chunk_sizes)(LazWriteItem *self);
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

/* --- raw writers (laswriteitemraw.hpp), little-endian hosts only --- */
LazWriteItem *laz_writeitem_raw_point10(LazOutStream *out);
LazWriteItem *laz_writeitem_raw_gpstime11(LazOutStream *out);
LazWriteItem *laz_writeitem_raw_rgb12(LazOutStream *out);
LazWriteItem *laz_writeitem_raw_wavepacket13(LazOutStream *out);
LazWriteItem *laz_writeitem_raw_byte(LazOutStream *out, U32 number);

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

#endif /* LAZ_WRITEITEM_H */
