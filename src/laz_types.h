/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_types.h -- fixed-width types, bit-twiddling macros and the canonical
 * decoded-point layout shared by every part of the lazpy C core.
 *
 * Ported from LASzip's mydefs.hpp / laszip_api.h. Names are kept close to the
 * originals so the ported readers can be diffed against the reference source.
 */
#ifndef LAZ_TYPES_H
#define LAZ_TYPES_H

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t  U8;
typedef uint16_t U16;
typedef uint32_t U32;
typedef uint64_t U64;
typedef int8_t   I8;
typedef int16_t  I16;
typedef int32_t  I32;
typedef int64_t  I64;
typedef float    F32;
typedef double   F64;
typedef int      BOOL;

#define LAZ_TRUE  1
#define LAZ_FALSE 0

#define U8_MIN  ((U8)0x0)
#define U8_MAX  ((U8)0xFF)
#define U8_MAX_PLUS_ONE 0x0100
#define U32_MAX ((U32)0xFFFFFFFF)
#define I8_MIN  ((I8)0x80)
#define I8_MAX  ((I8)0x7F)
#define I32_MIN ((I32)0x80000000)
#define I32_MAX ((I32)0x7FFFFFFF)

#define U8_FOLD(n) (((n) < U8_MIN) ? ((n) + U8_MAX_PLUS_ONE) \
                                   : (((n) > U8_MAX) ? ((n) - U8_MAX_PLUS_ONE) : (n)))
#define U8_CLAMP(n) (((n) <= 0) ? U8_MIN : (((n) >= 255) ? U8_MAX : ((U8)(n))))
#define I8_CLAMP(n) (((n) <= I8_MIN) ? I8_MIN : (((n) >= I8_MAX) ? I8_MAX : ((I8)(n))))
#define I16_QUANTIZE(n) (((n) >= 0) ? (I16)((n) + 0.5) : (I16)((n) - 0.5))
#define I32_QUANTIZE(n) (((n) >= 0) ? (I32)((n) + 0.5f) : (I32)((n) - 0.5f))
#define U32_ZERO_BIT_0(n) ((n) & (U32)0xFFFFFFFE)

/* LASzip item types, as stored in the LASzip VLR. */
typedef enum {
    LAZ_ITEM_BYTE = 0,
    LAZ_ITEM_SHORT = 1,
    LAZ_ITEM_INT = 2,
    LAZ_ITEM_LONG = 3,
    LAZ_ITEM_FLOAT = 4,
    LAZ_ITEM_DOUBLE = 5,
    LAZ_ITEM_POINT10 = 6,
    LAZ_ITEM_GPSTIME11 = 7,
    LAZ_ITEM_RGB12 = 8,
    LAZ_ITEM_WAVEPACKET13 = 9,
    LAZ_ITEM_POINT14 = 10,
    LAZ_ITEM_RGB14 = 11,
    LAZ_ITEM_RGBNIR14 = 12,
    LAZ_ITEM_WAVEPACKET14 = 13,
    LAZ_ITEM_BYTE14 = 14
} LazItemType;

typedef struct {
    U16 type;
    U16 size;
    U16 version;
} LazItem;

#define LAZ_COMPRESSOR_NONE             0
#define LAZ_COMPRESSOR_POINTWISE        1
#define LAZ_COMPRESSOR_POINTWISE_CHUNKED 2
#define LAZ_COMPRESSOR_LAYERED_CHUNKED  3

#define LAZ_CODER_ARITHMETIC 0

/*
 * Host byte order.
 *
 * LAS and LAZ are little-endian on disk; the decoded point below holds host
 * order. On a little-endian host the two coincide and every conversion here
 * folds away, which is why the raw item coders used to be plain byte copies.
 * On a big-endian host they do not, and the conversions are what stands
 * between the disk and the point.
 */
#if defined(__BYTE_ORDER__) && defined(__ORDER_BIG_ENDIAN__)
#define LAZ_BIG_ENDIAN (__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)
#else
/* MSVC defines no such macro and every target it compiles for is
 * little-endian, so assume that rather than refuse to build. It is an
 * assumption and not a fact, which is why laz_host_order_ok() below exists for
 * a caller to check once at start-up. */
#define LAZ_BIG_ENDIAN 0
#endif

/*
 * Little-endian scalars in a byte buffer.
 *
 * The rule these serve, which the whole codebase follows:
 *
 *  - A scalar field of the decoded point is in host order. The raw item coders
 *    convert on the way in and out (laz_readitem_raw.c, laz_writeitem_raw.c);
 *    everything between -- the compressed coders, the array API -- just reads
 *    and writes the point and never thinks about byte order at all.
 *  - A blob keeps its on-disk order wherever it goes. There are two, both
 *    opaque to the point: `wave_packet`, unpacked only by laz_wp_get32 in
 *    laz_item.h, and the extra bytes, whose typing lives in a VLR no item coder
 *    can see. Both reach Python as bytes, so leaving them alone is also what
 *    makes those bytes the same on every host.
 *  - Anything emitting a byte stream that must not depend on the host converts
 *    explicitly. That is the on-disk format, and also Reader_checksum, whose
 *    hash is compared against values laszip computed elsewhere.
 *
 * So these are for the third case and for reading into or out of the second.
 * Written as shifts rather than a cast plus a conditional swap so the same code
 * is correct either way, and so neither alignment nor strict aliasing comes
 * into it; a compiler folds them back into a single load or store.
 */
static inline U16 laz_le_get16(const U8 *p)
{
    return (U16)((U16)p[0] | ((U16)p[1] << 8));
}

static inline U32 laz_le_get32(const U8 *p)
{
    return (U32)p[0] | ((U32)p[1] << 8) | ((U32)p[2] << 16) | ((U32)p[3] << 24);
}

static inline U64 laz_le_get64(const U8 *p)
{
    return (U64)laz_le_get32(p) | ((U64)laz_le_get32(p + 4) << 32);
}

static inline void laz_le_put16(U8 *p, U16 v)
{
    p[0] = (U8)(v & 0xFF);
    p[1] = (U8)(v >> 8);
}

static inline void laz_le_put32(U8 *p, U32 v)
{
    p[0] = (U8)(v & 0xFF);
    p[1] = (U8)((v >> 8) & 0xFF);
    p[2] = (U8)((v >> 16) & 0xFF);
    p[3] = (U8)((v >> 24) & 0xFF);
}

static inline void laz_le_put64(U8 *p, U64 v)
{
    laz_le_put32(p, (U32)(v & 0xFFFFFFFFu));
    laz_le_put32(p + 4, (U32)(v >> 32));
}

/* A float travels as its IEEE 754 bit pattern too; the spatial index's
 * bounding box is four of them. */
static inline F32 laz_le_get_f32(const U8 *p)
{
    U32 bits = laz_le_get32(p);
    F32 v;
    memcpy(&v, &bits, 4);
    return v;
}

static inline void laz_le_put_f32(U8 *p, F32 v)
{
    U32 bits;
    memcpy(&bits, &v, 4);
    laz_le_put32(p, bits);
}

/* A double travels as its IEEE 754 bit pattern, which is what the LAS gps_time
 * and the LAZ coders both treat it as. */
static inline F64 laz_le_get_f64(const U8 *p)
{
    U64 bits = laz_le_get64(p);
    F64 v;
    memcpy(&v, &bits, 8);
    return v;
}

static inline void laz_le_put_f64(U8 *p, F64 v)
{
    U64 bits;
    memcpy(&bits, &v, 8);
    laz_le_put64(p, bits);
}

/*
 * Is LAZ_BIG_ENDIAN right about this host?
 *
 * Everything above rests on that macro, and on a compiler that defines no
 * __BYTE_ORDER__ it is a guess rather than a fact. Rather than refuse to build
 * on such a compiler -- which is what the old little-endian-only check did, and
 * which would now reject a host that works -- lay a known value down both ways
 * and see whether they agree. A caller checks once at start-up; getting this
 * wrong mis-decodes every point, so it is worth not assuming.
 */
static inline BOOL laz_host_order_ok(void)
{
    const U32 probe = 0x01020304u;
    U8 first;
    memcpy(&first, &probe, 1);
    /* the most significant byte is stored first only on a big-endian host */
    return (first == 0x01) == (LAZ_BIG_ENDIAN != 0);
}

/*
 * The canonical decoded point.
 *
 * This deliberately mirrors LASzip's laszip_point_struct byte-for-byte,
 * because the item readers write straight into it at fixed offsets rather
 * than through any accessor: POINT10/POINT14 write at offset 0, GPSTIME11
 * writes at offset 32 (gps_time), RGB12/RGB14/RGBNIR14 at offset 40 (rgb),
 * and WAVEPACKET13/14 at offset 48 (wave_packet). Reordering these fields
 * silently corrupts decoding.
 *
 * Scalars are in host order and wave_packet is a blob, both per the rule on
 * laz_le_get16 above.
 *
 * The bytes LAS packs several fields into are plain U8 here rather than C
 * bitfields: the item coders reach those same bytes through fixed shifts
 * (P10_RETURN_NUMBER and friends in laz_item.h), and bitfield allocation runs
 * from the most significant bit on the big-endian ABIs, so the two would agree
 * only by accident. Packing them by hand, through the accessors below, makes
 * one rule hold on every host -- and is why no assertion here has anything to
 * say about bit positions: they are no longer the compiler's to choose.
 */
typedef struct {
    I32 X;                              /* offset  0 */
    I32 Y;                              /* offset  4 */
    I32 Z;                              /* offset  8 */
    U16 intensity;                      /* offset 12 */
    U8 returns_and_flags;               /* offset 14 (packed, see below) */
    U8 classification_bits;             /* offset 15 (packed) */
    I8 scan_angle_rank;                 /* offset 16 */
    U8 user_data;                       /* offset 17 */
    U16 point_source_ID;                /* offset 18 */

    I16 extended_scan_angle;            /* offset 20 */
    U8 extended_flags;                  /* offset 22 (packed) */
    U8 extended_classification;         /* offset 23 */
    U8 extended_returns;                /* offset 24 (packed) */

    U8 dummy[7];                        /* offset 25, aligns gps_time to 32 */

    F64 gps_time;                       /* offset 32 */
    U16 rgb[4];                         /* offset 40 (r, g, b, nir) */
    U8 wave_packet[29];                 /* offset 48 */

    I32 num_extra_bytes;
    U8 *extra_bytes;
} LazPoint;

/*
 * The packed fields, as (name, containing byte, shift, mask).
 *
 * The bit positions are the on-disk ones, so the raw coders can copy those
 * bytes through untouched, and they are the positions the P10_/P14_ macros in
 * laz_item.h already assume. lazpy/__init__.py's _ARRAY_FIELDS restates the
 * same shifts for numpy; test_arrays_match_reading_point_by_point pins the two
 * together.
 */
#define LAZ_POINT_PACKED_FIELDS(_)                                            \
    _(return_number,                 returns_and_flags,    0, 0x07)           \
    _(number_of_returns,             returns_and_flags,    3, 0x07)           \
    _(scan_direction_flag,           returns_and_flags,    6, 0x01)           \
    _(edge_of_flight_line,           returns_and_flags,    7, 0x01)           \
    _(classification,                classification_bits,  0, 0x1F)           \
    _(synthetic_flag,                classification_bits,  5, 0x01)           \
    _(keypoint_flag,                 classification_bits,  6, 0x01)           \
    _(withheld_flag,                 classification_bits,  7, 0x01)           \
    _(extended_point_type,           extended_flags,       0, 0x03)           \
    _(extended_scanner_channel,      extended_flags,       2, 0x03)           \
    _(extended_classification_flags, extended_flags,       4, 0x0F)           \
    _(extended_return_number,        extended_returns,     0, 0x0F)           \
    _(extended_number_of_returns,    extended_returns,     4, 0x0F)

/*
 * Each field gets a getter, a setter, and LAZ_POINT_MAX_<name> -- its mask,
 * which is also the largest value it can hold. Anything that has to range-check
 * a value takes the bound from here rather than restating it; see POINT_PSETTER
 * in cpylazmodule.c.
 */
#define LAZ_DEFINE_PACKED_ACCESSORS(name, byte, shift, mask)                  \
    enum { LAZ_POINT_MAX_##name = (mask) };                                   \
    static inline U8 laz_point_##name(const LazPoint *p)                      \
    { return (U8)((p->byte >> (shift)) & (mask)); }                           \
    static inline void laz_point_set_##name(LazPoint *p, U8 v)                \
    { p->byte = (U8)((p->byte & ~((mask) << (shift)))                         \
                     | (((v) & (mask)) << (shift))); }
LAZ_POINT_PACKED_FIELDS(LAZ_DEFINE_PACKED_ACCESSORS)
#undef LAZ_DEFINE_PACKED_ACCESSORS

/* Offsets the item readers write to. */
#define LAZ_POINT_OFFSET_XYZ         0
#define LAZ_POINT_OFFSET_GPSTIME    32
#define LAZ_POINT_OFFSET_RGB        40
#define LAZ_POINT_OFFSET_WAVEPACKET 48

/*
 * The POINT14 v3/v4 readers do not write only the LAS 1.4 fields: they finish
 * with a 48-byte copy from offset 0, which covers the scratch area at 25-31
 * and the rgb slots at 40-47. That is faithful to LASzip, and safe only
 * because POINT14 is always item 0, so any RGB item decodes afterwards and
 * overwrites 40-47. A VLR listing RGB14 before POINT14 would break this.
 */
#define LAZ_POINT14_WRITE_EXTENT    48

/*
 * The layout is load-bearing, not incidental: item readers write through casts
 * at the offsets above rather than through field accessors, so a compiler that
 * packed this struct differently would corrupt every decoded point. Checked
 * here, where the struct is defined, so any consumer of src/ gets the check
 * and not just the Python extension.
 */
#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
#define LAZ_ASSERT_OFFSET(field, want) \
    _Static_assert(offsetof(LazPoint, field) == (want), \
                   "LazPoint." #field " must live at offset " #want)
LAZ_ASSERT_OFFSET(X, LAZ_POINT_OFFSET_XYZ);
LAZ_ASSERT_OFFSET(Y, 4);
LAZ_ASSERT_OFFSET(Z, 8);
LAZ_ASSERT_OFFSET(intensity, 12);
LAZ_ASSERT_OFFSET(returns_and_flags, 14);
LAZ_ASSERT_OFFSET(classification_bits, 15);
LAZ_ASSERT_OFFSET(scan_angle_rank, 16);
LAZ_ASSERT_OFFSET(user_data, 17);
LAZ_ASSERT_OFFSET(point_source_ID, 18);
LAZ_ASSERT_OFFSET(extended_scan_angle, 20);
LAZ_ASSERT_OFFSET(extended_flags, 22);
LAZ_ASSERT_OFFSET(extended_classification, 23);
LAZ_ASSERT_OFFSET(extended_returns, 24);
LAZ_ASSERT_OFFSET(gps_time, LAZ_POINT_OFFSET_GPSTIME);
LAZ_ASSERT_OFFSET(rgb, LAZ_POINT_OFFSET_RGB);
LAZ_ASSERT_OFFSET(wave_packet, LAZ_POINT_OFFSET_WAVEPACKET);
/* the POINT14 copy must not reach into the wavepacket slot */
_Static_assert(LAZ_POINT14_WRITE_EXTENT <= LAZ_POINT_OFFSET_WAVEPACKET,
               "POINT14 write extent overlaps the wavepacket item");
/* the gps time is coded as the eight bytes of an IEEE 754 double, both on disk
 * and by the coders that treat it as an I64 */
_Static_assert(sizeof(F64) == 8, "gps_time must be an 8-byte double");
#undef LAZ_ASSERT_OFFSET
#endif

#endif /* LAZ_TYPES_H */
