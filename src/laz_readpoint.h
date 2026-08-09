/*
 * laz_readpoint.h -- the point-stream container.
 *
 * Ported from LASzip's lasreadpoint.{hpp,cpp}. This is the layer that turns a
 * list of LASzip items plus a byte stream into a sequence of decoded points:
 * it owns the item readers, reads the chunk table, restarts the entropy coder
 * at every chunk boundary, and implements random access by seeking.
 *
 * Three container shapes are supported, selected by the LASzip VLR:
 *   - uncompressed        : raw readers only, fixed-size records
 *   - pointwise / chunked : one arithmetic decoder over the whole chunk
 *   - layered chunked     : LAS 1.4, where each item splits the chunk into
 *                           independently-decodable byte layers
 */
#ifndef LAZ_READPOINT_H
#define LAZ_READPOINT_H

#include "laz_types.h"
#include "laz_stream.h"
#include "laz_arithmetic.h"
#include "laz_readitem.h"

typedef struct {
    LazStream *instream;        /* borrowed */
    U32 num_readers;
    LazReadItem **readers;      /* points at readers_raw or readers_compressed */
    LazReadItem **readers_raw;
    LazReadItem **readers_compressed;
    LazDecoder dec;
    BOOL have_dec;
    BOOL layered_las14_compression;

    U32 point_size;
    LazItem *items;             /* owned copy */
    /* True for point formats 6-10. LASzip stamps extended_point_type once when
     * the reader is opened rather than per point, so callers must seed their
     * point buffer with it; nothing in the decode path writes that field. */
    BOOL has_point14;

    /* chunking */
    U32 chunk_size;             /* U32_MAX means adaptive (variable) chunks */
    U32 chunk_count;
    U32 current_chunk;
    U32 number_chunks;
    U32 tabled_chunks;
    U64 *chunk_totals;          /* adaptive chunking only */
    I64 *chunk_starts;

    U32 decompress_selective;

    /* seeking */
    I64 point_start;
    LazPoint seek_point;
    U8 *seek_extra_bytes;

    char last_error[192];
    char last_warning[192];
    BOOL has_error;
    BOOL has_warning;
} LazReadPoint;

void laz_readpoint_init_struct(LazReadPoint *rp, U32 decompress_selective);

/* Builds the raw and (if compressed) compressed item readers. `compressor` and
 * `coder` come from the LASzip VLR; pass compressor 0 for an uncompressed file. */
BOOL laz_readpoint_setup(LazReadPoint *rp, U32 num_items, const LazItem *items,
                         U32 compressor, U32 coder, U32 chunk_size);

BOOL laz_readpoint_init(LazReadPoint *rp, LazStream *instream);

/* Decodes the next point. `extra_bytes` may be NULL when the layout has none. */
BOOL laz_readpoint_read(LazReadPoint *rp, LazPoint *point, U8 *extra_bytes);

/* Moves from point index `current` to `target`, using the chunk table when
 * available and decoding forward otherwise. */
BOOL laz_readpoint_seek(LazReadPoint *rp, U64 current, U64 target);

void laz_readpoint_destroy(LazReadPoint *rp);

#endif /* LAZ_READPOINT_H */
