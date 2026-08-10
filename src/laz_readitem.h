/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_readitem.h -- common interface for the classes that decode the items
 * making up a point.
 *
 * Ported from LASzip's lasreaditem.hpp. LASzip uses virtual methods; here the
 * equivalent is a small vtable at the head of each concrete reader, so a
 * concrete reader can always be cast to LazReadItem*.
 *
 * `item` points into the caller's LazPoint at the offset for this item type
 * (see laz_types.h) -- readers write host-order bytes there, in the layout
 * documented on that struct.
 */
#ifndef LAZ_READITEM_H
#define LAZ_READITEM_H

#include "laz_types.h"
#include "laz_stream.h"
#include "laz_item.h"

typedef struct LazReadItem LazReadItem;

struct LazReadItem {
    void (*read)(LazReadItem *self, U8 *item, U32 *context);
    /* compressed readers only: seed state from the chunk's first (raw) point */
    BOOL (*init)(LazReadItem *self, const U8 *item, U32 *context);
    /* layered (v3/v4) readers only: read this item's per-layer byte counts */
    BOOL (*chunk_sizes)(LazReadItem *self);
    /* layered readers only: TRUE once a layer decoder has read past the end of
     * its byte range. LASzip throws there; lazpy keeps decoding and reports it
     * afterwards, so a corrupt chunk surfaces as an error instead of silently
     * yielding zero-filled points. */
    BOOL (*overran)(LazReadItem *self);
    void (*destroy)(LazReadItem *self);

    LazStream *instream;    /* raw readers: borrowed */
    LazDecoder *dec;        /* compressed readers: borrowed */
};

static inline void laz_readitem_destroy(LazReadItem *r)
{
    if (!r) return;
    if (r->destroy) r->destroy(r);
    free(r);
}

/* --- raw readers (lasreaditemraw.hpp): the on-disk/host byte-order boundary --- */
LazReadItem *laz_readitem_raw_point10(LazStream *in);
LazReadItem *laz_readitem_raw_gpstime11(LazStream *in);
LazReadItem *laz_readitem_raw_rgb12(LazStream *in);
LazReadItem *laz_readitem_raw_rgbnir14(LazStream *in);
LazReadItem *laz_readitem_raw_wavepacket13(LazStream *in);
LazReadItem *laz_readitem_raw_byte(LazStream *in, U32 number);
LazReadItem *laz_readitem_raw_point14(LazStream *in);

/*
 * Picks the raw reader for one item of a LASzip VLR, the mirror of
 * laz_writeitem_new_raw. Returns NULL for a type with no reader.
 *
 * Several 1.4 items share a legacy reader, because uncompressed they are the
 * same bytes: RGB14 is an RGB12, WAVEPACKET14 a WAVEPACKET13, BYTE14 a BYTE.
 */
LazReadItem *laz_readitem_new_raw(const LazItem *item, LazStream *in);

/* --- v1 compressed readers (lasreaditemcompressed_v1.cpp) --- */
LazReadItem *laz_readitem_v1_point10(LazDecoder *dec);
LazReadItem *laz_readitem_v1_gpstime11(LazDecoder *dec);
LazReadItem *laz_readitem_v1_rgb12(LazDecoder *dec);
LazReadItem *laz_readitem_v1_byte(LazDecoder *dec, U32 number);
LazReadItem *laz_readitem_v1_wavepacket13(LazDecoder *dec);

/* --- v2 compressed readers (lasreaditemcompressed_v2.cpp) --- */
LazReadItem *laz_readitem_v2_point10(LazDecoder *dec);
LazReadItem *laz_readitem_v2_gpstime11(LazDecoder *dec);
LazReadItem *laz_readitem_v2_rgb12(LazDecoder *dec);
LazReadItem *laz_readitem_v2_byte(LazDecoder *dec, U32 number);

/* --- v3/v4 layered readers (lasreaditemcompressed_v3.cpp / _v4.cpp) --- */
LazReadItem *laz_readitem_v3_point14(LazDecoder *dec, U32 decompress_selective);
LazReadItem *laz_readitem_v3_rgb14(LazDecoder *dec, U32 decompress_selective);
LazReadItem *laz_readitem_v3_rgbnir14(LazDecoder *dec, U32 decompress_selective);
LazReadItem *laz_readitem_v3_byte14(LazDecoder *dec, U32 number, U32 decompress_selective);
LazReadItem *laz_readitem_v3_wavepacket14(LazDecoder *dec, U32 decompress_selective);

LazReadItem *laz_readitem_v4_point14(LazDecoder *dec, U32 decompress_selective);
LazReadItem *laz_readitem_v4_rgb14(LazDecoder *dec, U32 decompress_selective);
LazReadItem *laz_readitem_v4_rgbnir14(LazDecoder *dec, U32 decompress_selective);
LazReadItem *laz_readitem_v4_byte14(LazDecoder *dec, U32 number, U32 decompress_selective);
LazReadItem *laz_readitem_v4_wavepacket14(LazDecoder *dec, U32 decompress_selective);

/* Selective-decompression flags (laszip_api.h). Only the v3/v4 readers honour
 * them; everything else always decodes in full. */
#define LAZ_DECOMPRESS_SELECTIVE_ALL                0xFFFFFFFFu
#define LAZ_DECOMPRESS_SELECTIVE_CHANNEL_RETURNS_XY 0x00000000u
#define LAZ_DECOMPRESS_SELECTIVE_Z                  0x00000001u
#define LAZ_DECOMPRESS_SELECTIVE_CLASSIFICATION     0x00000002u
#define LAZ_DECOMPRESS_SELECTIVE_FLAGS              0x00000004u
#define LAZ_DECOMPRESS_SELECTIVE_INTENSITY          0x00000008u
#define LAZ_DECOMPRESS_SELECTIVE_SCAN_ANGLE         0x00000010u
#define LAZ_DECOMPRESS_SELECTIVE_USER_DATA          0x00000020u
#define LAZ_DECOMPRESS_SELECTIVE_POINT_SOURCE       0x00000040u
#define LAZ_DECOMPRESS_SELECTIVE_GPS_TIME           0x00000080u
#define LAZ_DECOMPRESS_SELECTIVE_RGB                0x00000100u
#define LAZ_DECOMPRESS_SELECTIVE_NIR                0x00000200u
#define LAZ_DECOMPRESS_SELECTIVE_WAVEPACKET         0x00000400u
#define LAZ_DECOMPRESS_SELECTIVE_BYTE0              0x00010000u
#define LAZ_DECOMPRESS_SELECTIVE_EXTRA_BYTES        0xFFFF0000u

#endif /* LAZ_READITEM_H */
