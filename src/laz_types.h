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
 * The canonical decoded point.
 *
 * This deliberately mirrors LASzip's laszip_point_struct byte-for-byte,
 * because the item readers write straight into it at fixed offsets rather
 * than through any accessor: POINT10/POINT14 write at offset 0, GPSTIME11
 * writes at offset 32 (gps_time), RGB12/RGB14/RGBNIR14 at offset 40 (rgb),
 * and WAVEPACKET13/14 at offset 48 (wave_packet). Reordering these fields
 * silently corrupts decoding.
 */
typedef struct {
    I32 X;                              /* offset  0 */
    I32 Y;                              /* offset  4 */
    I32 Z;                              /* offset  8 */
    U16 intensity;                      /* offset 12 */
    U8 return_number : 3;               /* offset 14 (bitfield byte) */
    U8 number_of_returns : 3;
    U8 scan_direction_flag : 1;
    U8 edge_of_flight_line : 1;
    U8 classification : 5;              /* offset 15 */
    U8 synthetic_flag : 1;
    U8 keypoint_flag : 1;
    U8 withheld_flag : 1;
    I8 scan_angle_rank;                 /* offset 16 */
    U8 user_data;                       /* offset 17 */
    U16 point_source_ID;                /* offset 18 */

    I16 extended_scan_angle;            /* offset 20 */
    U8 extended_point_type : 2;         /* offset 22 */
    U8 extended_scanner_channel : 2;
    U8 extended_classification_flags : 4;
    U8 extended_classification;         /* offset 23 */
    U8 extended_return_number : 4;      /* offset 24 */
    U8 extended_number_of_returns : 4;

    U8 dummy[7];                        /* offset 25, aligns gps_time to 32 */

    F64 gps_time;                       /* offset 32 */
    U16 rgb[4];                         /* offset 40 (r, g, b, nir) */
    U8 wave_packet[29];                 /* offset 48 */

    I32 num_extra_bytes;
    U8 *extra_bytes;
} LazPoint;

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
LAZ_ASSERT_OFFSET(scan_angle_rank, 16);
LAZ_ASSERT_OFFSET(user_data, 17);
LAZ_ASSERT_OFFSET(point_source_ID, 18);
LAZ_ASSERT_OFFSET(extended_scan_angle, 20);
LAZ_ASSERT_OFFSET(extended_classification, 23);
LAZ_ASSERT_OFFSET(gps_time, LAZ_POINT_OFFSET_GPSTIME);
LAZ_ASSERT_OFFSET(rgb, LAZ_POINT_OFFSET_RGB);
LAZ_ASSERT_OFFSET(wave_packet, LAZ_POINT_OFFSET_WAVEPACKET);
/* the POINT14 copy must not reach into the wavepacket slot */
_Static_assert(LAZ_POINT14_WRITE_EXTENT <= LAZ_POINT_OFFSET_WAVEPACKET,
               "POINT14 write extent overlaps the wavepacket item");
#undef LAZ_ASSERT_OFFSET
#endif

/* Little-endian hosts only: the readers reinterpret bytes in place. */
#if defined(__BYTE_ORDER__) && defined(__ORDER_BIG_ENDIAN__) && \
    __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
#error "lazpy only supports little-endian hosts"
#endif

#endif /* LAZ_TYPES_H */
