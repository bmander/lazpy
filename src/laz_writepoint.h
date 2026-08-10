/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_writepoint.h -- the point-stream container, compress side.
 *
 * Ported from LASzip's laswritepoint.{hpp,cpp} and the mirror image of
 * laz_readpoint.h: it owns the item writers, cuts the stream into chunks,
 * restarts the entropy coder at every boundary, and writes the chunk table
 * that lets a reader seek.
 *
 * The same three container shapes the reader handles:
 *   - uncompressed        : raw writers only, fixed-size records
 *   - pointwise / chunked : one arithmetic encoder per chunk
 *   - layered chunked     : LAS 1.4, where a chunk closes as a point count,
 *                           every writer's layer sizes, then the layers
 *
 * A chunked stream begins with an 8-byte placeholder for the position of the
 * chunk table, which is only known once the last point is written. On a
 * seekable output the writer goes back and patches it; otherwise it writes -1
 * there and appends the real position after the table, which is the variant
 * read_chunk_table in laz_readpoint.c already recognises.
 */
#ifndef LAZ_WRITEPOINT_H
#define LAZ_WRITEPOINT_H

#include "laz_types.h"
#include "laz_stream.h"
#include "laz_arithmetic.h"
#include "laz_writeitem.h"

typedef struct {
    LazOutStream *outstream;    /* borrowed */
    U32 num_writers;
    LazWriteItem **writers;     /* writers_raw, writers_compressed, or NULL at
                                 * the head of a chunk, where the next point is
                                 * written raw to seed the compressed writers */
    LazWriteItem **writers_raw;
    LazWriteItem **writers_compressed;
    LazEncoder enc;
    BOOL have_enc;
    BOOL layered_las14_compression;

    U32 point_size;
    /* Byte offset within a LazPoint that each item encodes from, or -1 for
     * items taken from the caller's extra-bytes buffer. As on the read side,
     * resolved once at setup rather than per point. */
    I32 item_offsets[LAZ_MAX_ITEMS];
    /* Total size of the BYTE/BYTE14 items, i.e. how large the caller's
     * extra-bytes buffer must be. */
    U32 num_extra_bytes;
    /* True for point formats 6-10; see laz_writepoint_init_point. */
    BOOL has_point14;

    /* chunking */
    BOOL chunked;               /* false for uncompressed and for POINTWISE */
    U32 chunk_size;             /* U32_MAX means adaptive (variable) chunks */
    U32 chunk_count;            /* points written into the open chunk */
    U32 number_chunks;
    U32 alloced_chunks;
    U32 *chunk_sizes;           /* point counts; adaptive chunking only */
    U32 *chunk_bytes;           /* byte length of each closed chunk */
    I64 chunk_start_position;
    I64 chunk_table_start_position;   /* -1 when the output cannot seek */

    char last_error[192];
    BOOL has_error;
} LazWritePoint;

void laz_writepoint_init_struct(LazWritePoint *wp);

/* Builds the raw and (if compressed) compressed item writers. `compressor` and
 * `coder` are the values that go into the LASzip VLR; pass compressor 0 to
 * write uncompressed points. A `chunk_size` of U32_MAX -- or of 0, as in
 * laz_readpoint_setup -- selects adaptive chunking, where the boundaries are
 * laz_writepoint_chunk's to choose. Chunk size is ignored unless the
 * compressor is a chunked one. */
BOOL laz_writepoint_setup(LazWritePoint *wp, U32 num_items, const LazItem *items,
                          U32 compressor, U32 coder, U32 chunk_size);

/* Takes the output stream and, when chunking, writes the chunk-table
 * placeholder. Everything written from here on belongs to the point block. */
BOOL laz_writepoint_init(LazWritePoint *wp, LazOutStream *outstream);

/* Marks a point as belonging to this layout, the mirror of
 * laz_readpoint_init_point. Point formats 6-10 carry extended_point_type on
 * every point, the raw POINT14 writer branches on it, and a point built from
 * scratch would otherwise lose its extended fields silently. Every point
 * passed to laz_writepoint_write must have been through this -- a point that
 * came from a reader of the same layout already has. */
void laz_writepoint_init_point(const LazWritePoint *wp, LazPoint *point);

/* Encodes one point. `extra_bytes` may be NULL when the layout has none. */
BOOL laz_writepoint_write(LazWritePoint *wp, const LazPoint *point,
                          const U8 *extra_bytes);

/* Closes the open chunk early, for adaptive chunking. Fails unless the
 * container is chunked and its chunk size is U32_MAX. Closing a chunk no point
 * has been written to does nothing, so a caller may end every chunk with this
 * without having to special-case the end of the input. */
BOOL laz_writepoint_chunk(LazWritePoint *wp);

/* Closes the last chunk, writes the chunk table and flushes the output. Must
 * be called exactly once, before the output is closed: a writer dropped
 * without it leaves a file whose last bytes never reached the sink and which
 * has no chunk table, so a reader has to reconstruct one. */
BOOL laz_writepoint_done(LazWritePoint *wp);

void laz_writepoint_destroy(LazWritePoint *wp);

#endif /* LAZ_WRITEPOINT_H */
