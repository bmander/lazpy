/*
 * laz_readitem.h -- common interface for the classes that decode the items
 * making up a point.
 *
 * Ported from LASzip's lasreaditem.hpp. LASzip uses virtual methods; here the
 * equivalent is a small vtable at the head of each concrete reader, so a
 * concrete reader can always be cast to LazReadItem*.
 *
 * `item` points into the caller's LazPoint at the offset for this item type
 * (see laz_types.h) -- readers write raw little-endian bytes there.
 */
#ifndef LAZ_READITEM_H
#define LAZ_READITEM_H

#include "laz_types.h"
#include "laz_stream.h"
#include "laz_arithmetic.h"
#include "laz_intcompressor.h"

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

/* --- raw readers (lasreaditemraw.hpp), little-endian hosts only --- */
LazReadItem *laz_readitem_raw_point10(LazStream *in);
LazReadItem *laz_readitem_raw_gpstime11(LazStream *in);
LazReadItem *laz_readitem_raw_rgb12(LazStream *in);
LazReadItem *laz_readitem_raw_rgbnir14(LazStream *in);
LazReadItem *laz_readitem_raw_wavepacket13(LazStream *in);
LazReadItem *laz_readitem_raw_byte(LazStream *in, U32 number);
LazReadItem *laz_readitem_raw_point14(LazStream *in);

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

/* --- shared field accessors and helpers ---------------------------------- */

/* Fields of the 20-byte legacy point record, as held in a reader's last_item.
 * The v2 reader adds bitfield accessors of its own on top of these. */
#define P10_X(li)               (*(I32 *)((li) + 0))
#define P10_Y(li)               (*(I32 *)((li) + 4))
#define P10_Z(li)               (*(I32 *)((li) + 8))
#define P10_INTENSITY(li)       (*(U16 *)((li) + 12))
#define P10_POINT_SOURCE_ID(li) (*(U16 *)((li) + 18))

/* Little-endian 32-bit access inside a packed wavepacket record. Shared by the
 * WAVEPACKET13 v1 and WAVEPACKET14 v3/v4 readers, which pack identically. */
static inline U32 laz_wp_get32(const U8 *p)
{
    return (U32)p[0] | ((U32)p[1] << 8) | ((U32)p[2] << 16) | ((U32)p[3] << 24);
}

static inline void laz_wp_put32(U32 v, U8 *p)
{
    p[0] = (U8)(v & 0xFF);
    p[1] = (U8)((v >> 8) & 0xFF);
    p[2] = (U8)((v >> 16) & 0xFF);
    p[3] = (U8)((v >> 24) & 0xFF);
}

/*
 * A bank of symbol models created on demand.
 *
 * LASzip allocates these lazily and, on chunk init, re-initialises only the
 * ones that exist. Doing the same matters for more than memory: a model that
 * has never been created must start fresh the first time it is used mid-chunk,
 * which is not the same as having been initialised at chunk start.
 *
 * `created` parallels `m` and records which entries have been through
 * laz_symbol_model_init at least once.
 */
static inline void laz_bank_setup(LazSymbolModel *m, U8 *created, U32 n, U32 num_symbols)
{
    U32 i;
    for (i = 0; i < n; i++) {
        laz_symbol_model_setup(&m[i], num_symbols, LAZ_FALSE);
        created[i] = 0;
    }
}

static inline void laz_bank_reinit(LazSymbolModel *m, const U8 *created, U32 n)
{
    U32 i;
    for (i = 0; i < n; i++) if (created[i]) laz_symbol_model_init(&m[i], NULL);
}

static inline LazSymbolModel *laz_bank_get(LazSymbolModel *m, U8 *created, U32 idx)
{
    if (!created[idx]) {
        laz_symbol_model_init(&m[idx], NULL);
        created[idx] = 1;
    }
    return &m[idx];
}

static inline void laz_bank_free(LazSymbolModel *m, U32 n)
{
    U32 i;
    for (i = 0; i < n; i++) laz_symbol_model_free(&m[i]);
}

/* Streaming median of the last five values, used to predict dx/dy in the
 * POINT10 v2 and POINT14 v3/v4 readers. Ported from laszip_common_v2.hpp. */
typedef struct {
    I32 values[5];
    BOOL high;
} LazStreamingMedian5;

static inline void laz_median5_init(LazStreamingMedian5 *m)
{
    m->values[0] = m->values[1] = m->values[2] = m->values[3] = m->values[4] = 0;
    m->high = LAZ_TRUE;
}

static inline I32 laz_median5_get(const LazStreamingMedian5 *m) { return m->values[2]; }

static inline void laz_median5_add(LazStreamingMedian5 *m, I32 v)
{
    if (m->high) {
        if (v < m->values[2]) {
            m->values[4] = m->values[3];
            m->values[3] = m->values[2];
            if (v < m->values[0]) {
                m->values[2] = m->values[1];
                m->values[1] = m->values[0];
                m->values[0] = v;
            } else if (v < m->values[1]) {
                m->values[2] = m->values[1];
                m->values[1] = v;
            } else {
                m->values[2] = v;
            }
        } else {
            if (v < m->values[3]) {
                m->values[4] = m->values[3];
                m->values[3] = v;
            } else {
                m->values[4] = v;
            }
            m->high = LAZ_FALSE;
        }
    } else {
        if (m->values[2] < v) {
            m->values[0] = m->values[1];
            m->values[1] = m->values[2];
            if (m->values[4] < v) {
                m->values[2] = m->values[3];
                m->values[3] = m->values[4];
                m->values[4] = v;
            } else if (m->values[3] < v) {
                m->values[2] = m->values[3];
                m->values[3] = v;
            } else {
                m->values[2] = v;
            }
        } else {
            if (m->values[1] < v) {
                m->values[0] = m->values[1];
                m->values[1] = v;
            } else {
                m->values[0] = v;
            }
            m->high = LAZ_TRUE;
        }
    }
}

/* Return-number lookup tables shared by the POINT10 v2 and POINT14 v3 readers. */
extern const U8 laz_number_return_map[8][8];
extern const U8 laz_number_return_level[8][8];
extern const U8 laz_number_return_map_6ctx[16][16];
extern const U8 laz_number_return_level_8ctx[16][16];

#endif /* LAZ_READITEM_H */
