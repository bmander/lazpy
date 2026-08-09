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

/*
 * Where item `type` lives inside a LazPoint. Extra bytes are the exception:
 * they go to a caller-supplied buffer, signalled by -1, and an item type
 * neither direction can code gives -2.
 *
 * Both directions need this, because an item coder is handed a pointer into
 * the point rather than the point itself.
 */
static inline I32 laz_item_offset(U32 type)
{
    switch (type) {
    case LAZ_ITEM_POINT10:
    case LAZ_ITEM_POINT14:
        return LAZ_POINT_OFFSET_XYZ;
    case LAZ_ITEM_GPSTIME11:
        return LAZ_POINT_OFFSET_GPSTIME;
    case LAZ_ITEM_RGB12:
    case LAZ_ITEM_RGB14:
    case LAZ_ITEM_RGBNIR14:
        return LAZ_POINT_OFFSET_RGB;
    case LAZ_ITEM_WAVEPACKET13:
    case LAZ_ITEM_WAVEPACKET14:
        return LAZ_POINT_OFFSET_WAVEPACKET;
    case LAZ_ITEM_BYTE:
    case LAZ_ITEM_BYTE14:
        return -1;
    default:
        return -2;      /* unsupported */
    }
}

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

/*
 * Fields of LASzip's combined LASpoint14 -- the layout the LAS 1.4 item coders
 * hold in last_item, and the one they are handed a point in. One definition of
 * the offsets and bit positions serves both directions, because two would be
 * free to drift apart silently.
 *
 * The mutable set below is what a reader decodes into; the _IN set that
 * follows is the same fields on a const item, for the writers, exactly as
 * P10_X_IN mirrors P10_X above.
 */
#define LASPOINT14_SIZE LAZ_POINT14_WRITE_EXTENT

#define P14_X(li)  (*(I32 *)((li) + 0))
#define P14_Y(li)  (*(I32 *)((li) + 4))
#define P14_Z(li)  (*(I32 *)((li) + 8))
#define P14_INTENSITY(li) (*(U16 *)((li) + 12))

#define P14_SET_LEGACY_RETURN_NUMBER(li, v)     ((li)[14] = (U8)(((li)[14] & 0xF8) | ((v) & 0x07)))
#define P14_SET_LEGACY_NUMBER_OF_RETURNS(li, v) ((li)[14] = (U8)(((li)[14] & 0xC7) | (((v) & 0x07) << 3)))
#define P14_SCAN_DIRECTION_FLAG(li)             (((li)[14] >> 6) & 0x01)
#define P14_SET_SCAN_DIRECTION_FLAG(li, v)      ((li)[14] = (U8)(((li)[14] & 0xBF) | (((v) & 0x01) << 6)))
#define P14_EDGE_OF_FLIGHT_LINE(li)             (((li)[14] >> 7) & 0x01)
#define P14_SET_EDGE_OF_FLIGHT_LINE(li, v)      ((li)[14] = (U8)(((li)[14] & 0x7F) | (((v) & 0x01) << 7)))

#define P14_SET_LEGACY_CLASSIFICATION(li, v)    ((li)[15] = (U8)(((li)[15] & 0xE0) | ((v) & 0x1F)))
#define P14_SET_LEGACY_FLAGS(li, v)             ((li)[15] = (U8)(((li)[15] & 0x1F) | (((v) & 0x07) << 5)))

#define P14_SET_LEGACY_SCAN_ANGLE_RANK(li, v)   (*(I8 *)((li) + 16) = (I8)(v))
#define P14_USER_DATA(li)                       ((li)[17])
#define P14_POINT_SOURCE_ID(li)                 (*(U16 *)((li) + 18))
#define P14_SCAN_ANGLE(li)                      (*(I16 *)((li) + 20))

#define P14_SCANNER_CHANNEL(li)                 (((li)[22] >> 2) & 0x03)
#define P14_SET_SCANNER_CHANNEL(li, v)          ((li)[22] = (U8)(((li)[22] & 0xF3) | (((v) & 0x03) << 2)))
#define P14_CLASSIFICATION_FLAGS(li)            (((li)[22] >> 4) & 0x0F)
#define P14_SET_CLASSIFICATION_FLAGS(li, v)     ((li)[22] = (U8)(((li)[22] & 0x0F) | (((v) & 0x0F) << 4)))
#define P14_CLASSIFICATION(li)                  ((li)[23])
#define P14_RETURN_NUMBER(li)                   ((li)[24] & 0x0F)
#define P14_SET_RETURN_NUMBER(li, v)            ((li)[24] = (U8)(((li)[24] & 0xF0) | ((v) & 0x0F)))
#define P14_NUMBER_OF_RETURNS(li)               (((li)[24] >> 4) & 0x0F)
#define P14_SET_NUMBER_OF_RETURNS(li, v)        ((li)[24] = (U8)(((li)[24] & 0x0F) | (((v) & 0x0F) << 4)))
#define P14_GPS_TIME_CHANGE(li)                 (*(I32 *)((li) + 28))
#define P14_GPS_TIME(li)                        (*(F64 *)((li) + 32))

/* The same fields on a const item. A writer reads both the point it was handed
 * and its own last_item through these; only last_item is ever assigned to, and
 * that goes through the mutable set above. */
#define P14_X_IN(p)  (*(const I32 *)((const U8 *)(p) + 0))
#define P14_Y_IN(p)  (*(const I32 *)((const U8 *)(p) + 4))
#define P14_Z_IN(p)  (*(const I32 *)((const U8 *)(p) + 8))
#define P14_INTENSITY_IN(p) (*(const U16 *)((const U8 *)(p) + 12))

#define P14_SCAN_DIRECTION_FLAG_IN(p) ((((const U8 *)(p))[14] >> 6) & 0x01)
#define P14_EDGE_OF_FLIGHT_LINE_IN(p) ((((const U8 *)(p))[14] >> 7) & 0x01)

#define P14_USER_DATA_IN(p)           (((const U8 *)(p))[17])
#define P14_POINT_SOURCE_ID_IN(p)     (*(const U16 *)((const U8 *)(p) + 18))
#define P14_SCAN_ANGLE_IN(p)          (*(const I16 *)((const U8 *)(p) + 20))

#define P14_SCANNER_CHANNEL_IN(p)      ((((const U8 *)(p))[22] >> 2) & 0x03)
#define P14_CLASSIFICATION_FLAGS_IN(p) ((((const U8 *)(p))[22] >> 4) & 0x0F)
#define P14_CLASSIFICATION_IN(p)       (((const U8 *)(p))[23])
#define P14_RETURN_NUMBER_IN(p)        (((const U8 *)(p))[24] & 0x0F)
#define P14_NUMBER_OF_RETURNS_IN(p)    ((((const U8 *)(p))[24] >> 4) & 0x0F)

#define P14_GPS_TIME_CHANGE_IN(p) (*(const I32 *)((const U8 *)(p) + 28))
#define P14_GPS_TIME_IN(p)        (*(const F64 *)((const U8 *)(p) + 32))
/* the same eight bytes as P14_GPS_TIME_IN, compared and coded as an integer */
#define P14_GPS_TIME_I64_IN(p)    (*(const I64 *)((const U8 *)(p) + 32))

/*
 * Looks for a stored sequence whose last time is within 32 bits of this one.
 * Returns the offset from `last` (1..3), or 0 if none qualifies. Four
 * interleaved sequences let a file that alternates between two sensors stay in
 * the cheap small-difference path.
 *
 * Only the writers search: a decoder is told which sequence to move to.
 */
static inline U32 laz_gps_find_other_sequence(const I64 *last_gpstime, U32 last,
                                              I64 this_gpstime)
{
    U32 i;
    for (i = 1; i < 4; i++) {
        I64 diff64 = this_gpstime - last_gpstime[(last + i) & 3];
        if (diff64 == (I64)(I32)diff64) return i;
    }
    return 0;
}

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
