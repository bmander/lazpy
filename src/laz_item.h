/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_item.h -- the state an item coder needs in either direction.
 *
 * Compressing an item and decompressing it are the same model updates driven
 * from opposite ends, so the predictors, model banks and lookup tables here
 * are shared verbatim between laz_readitem_*.c and laz_writeitem_*.c. In
 * LASzip these live in laszip_common_v1/v2.hpp and are duplicated between the
 * reader and writer translation units; keeping one copy is what stops the two
 * directions drifting apart.
 *
 * The two return-number tables are defined in laz_readitem_v2.c, which is
 * simply where they were first needed.
 */
#ifndef LAZ_ITEM_H
#define LAZ_ITEM_H

#include "laz_types.h"
#include "laz_arithmetic.h"
#include "laz_intcompressor.h"

/* Fields of the 20-byte legacy point record, as held in a reader's last_item.
 * The v2 reader adds bitfield accessors of its own on top of these. */
#define P10_X(li)               (*(I32 *)((li) + 0))
#define P10_Y(li)               (*(I32 *)((li) + 4))
#define P10_Z(li)               (*(I32 *)((li) + 8))
#define P10_INTENSITY(li)       (*(U16 *)((li) + 12))
#define P10_POINT_SOURCE_ID(li) (*(U16 *)((li) + 18))

/* The same fields on a const item. A writer is handed the point to encode as
 * const and must not modify it; casting that away to reuse the macros above
 * would work but would only hide the distinction. */
#define P10_X_IN(p)               (*(const I32 *)((const U8 *)(p) + 0))
#define P10_Y_IN(p)               (*(const I32 *)((const U8 *)(p) + 4))
#define P10_Z_IN(p)               (*(const I32 *)((const U8 *)(p) + 8))
#define P10_INTENSITY_IN(p)       (*(const U16 *)((const U8 *)(p) + 12))
#define P10_POINT_SOURCE_ID_IN(p) (*(const U16 *)((const U8 *)(p) + 18))

/* Fields of the 20-byte record that live inside the bit-packed byte 14. */
#define P10_RETURN_NUMBER(li)     ((li)[14] & 0x07)
#define P10_NUMBER_OF_RETURNS(li) (((li)[14] >> 3) & 0x07)
#define P10_SCAN_DIR_FLAG(li)     (((li)[14] >> 6) & 0x01)

/* Median of the three preceding differences, branch-for-branch as LASzip.
 * Predicts x and y in the POINT10 v1 coders. */
static inline I32 laz_median3(const I32 *d)
{
    if (d[0] < d[1]) {
        if (d[1] < d[2]) return d[1];
        else if (d[0] < d[2]) return d[2];
        else return d[0];
    } else {
        if (d[0] < d[2]) return d[0];
        else if (d[1] < d[2]) return d[2];
        else return d[1];
    }
}

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
 * The predicted part of a wavepacket item: the 28 bytes after the packet index,
 * laid out as [offset:8][size:4][return:4][x,y,z:12]. Unpacked field by field
 * rather than through a cast, so the layout does not depend on struct padding.
 */
typedef struct {
    U64 offset;
    U32 packet_size;
    I32 return_point;
    I32 x, y, z;
} LazWavepacket13;

static inline LazWavepacket13 laz_wp_unpack(const U8 *item)
{
    LazWavepacket13 w;
    w.offset = (U64)laz_wp_get32(item) | ((U64)laz_wp_get32(item + 4) << 32);
    w.packet_size = laz_wp_get32(item + 8);
    w.return_point = (I32)laz_wp_get32(item + 12);
    w.x = (I32)laz_wp_get32(item + 16);
    w.y = (I32)laz_wp_get32(item + 20);
    w.z = (I32)laz_wp_get32(item + 24);
    return w;
}

static inline void laz_wp_pack(const LazWavepacket13 *w, U8 *item)
{
    laz_wp_put32((U32)(w->offset & 0xFFFFFFFF), item);
    laz_wp_put32((U32)(w->offset >> 32), item + 4);
    laz_wp_put32(w->packet_size, item + 8);
    laz_wp_put32((U32)w->return_point, item + 12);
    laz_wp_put32((U32)w->x, item + 16);
    laz_wp_put32((U32)w->y, item + 20);
    laz_wp_put32((U32)w->z, item + 24);
}

/*
 * A bank of symbol models created on demand.
 *
 * LASzip allocates these lazily and, on chunk init, re-initialises only the
 * ones that exist. Doing the same matters for more than memory: a model that
 * has never been created must start fresh the first time it is used mid-chunk,
 * which is not the same as having been initialised at chunk start. The writer
 * has to create them on exactly the same schedule as the reader, or the two
 * disagree about a model's age.
 *
 * `created` parallels `m` and records which entries have been through
 * laz_symbol_model_init at least once.
 */
static inline void laz_bank_setup(LazSymbolModel *m, U8 *created, U32 n,
                                  U32 num_symbols, BOOL compress)
{
    U32 i;
    for (i = 0; i < n; i++) {
        laz_symbol_model_setup(&m[i], num_symbols, compress);
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

/* Return-number lookup tables shared by the POINT10 v2 and POINT14 v3 coders. */
extern const U8 laz_number_return_map[8][8];
extern const U8 laz_number_return_level[8][8];
extern const U8 laz_number_return_map_6ctx[16][16];
extern const U8 laz_number_return_level_8ctx[16][16];

#endif /* LAZ_ITEM_H */
