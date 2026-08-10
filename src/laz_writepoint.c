/*
 * Derived from LASzip (https://github.com/LASzip/LASzip), laswritepoint.cpp.
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

#include <stdio.h>
#include <stdarg.h>
#include "laz_writepoint.h"
#include "laz_intcompressor.h"

static void set_error(LazWritePoint *wp, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(wp->last_error, sizeof(wp->last_error), fmt, ap);
    va_end(ap);
    wp->has_error = LAZ_TRUE;
}

void laz_writepoint_init_struct(LazWritePoint *wp)
{
    memset(wp, 0, sizeof(*wp));
    wp->chunk_size = U32_MAX;
}

BOOL laz_writepoint_setup(LazWritePoint *wp, U32 num_items, const LazItem *items,
                          U32 compressor, U32 coder, U32 chunk_size)
{
    U32 i;

    if (num_items == 0 || items == NULL) {
        set_error(wp, "no items to write");
        return LAZ_FALSE;
    }
    if (num_items > LAZ_MAX_ITEMS) {
        set_error(wp, "too many items (%u, maximum %u)", num_items, LAZ_MAX_ITEMS);
        return LAZ_FALSE;
    }

    wp->num_writers = num_items;
    wp->have_enc = LAZ_FALSE;
    wp->layered_las14_compression = LAZ_FALSE;
    wp->chunk_size = U32_MAX;

    if (compressor) {
        if (coder != LAZ_CODER_ARITHMETIC) {
            set_error(wp, "entropy coder %u is not supported", coder);
            return LAZ_FALSE;
        }
        if (!laz_encoder_setup(&wp->enc)) { set_error(wp, "out of memory"); return LAZ_FALSE; }
        wp->have_enc = LAZ_TRUE;
        wp->layered_las14_compression = (compressor == LAZ_COMPRESSOR_LAYERED_CHUNKED);
    }

    /* raw writers always exist: they write the first point of every chunk */
    wp->writers_raw = (LazWriteItem **)calloc(num_items, sizeof(LazWriteItem *));
    if (!wp->writers_raw) { set_error(wp, "out of memory"); return LAZ_FALSE; }
    for (i = 0; i < num_items; i++) {
        wp->item_offsets[i] = laz_item_offset(items[i].type);
        if (wp->item_offsets[i] == -2) {
            set_error(wp, "item type %u is not supported", items[i].type);
            return LAZ_FALSE;
        }
        if (wp->item_offsets[i] == -1) wp->num_extra_bytes += items[i].size;
        wp->writers_raw[i] = laz_writeitem_new_raw(&items[i], NULL);
        if (!wp->writers_raw[i]) {
            set_error(wp, "item type %u is not supported", items[i].type);
            return LAZ_FALSE;
        }
        if (items[i].type == LAZ_ITEM_POINT14) wp->has_point14 = LAZ_TRUE;
        wp->point_size += items[i].size;
    }

    if (wp->have_enc) {
        wp->writers_compressed = (LazWriteItem **)calloc(num_items, sizeof(LazWriteItem *));
        if (!wp->writers_compressed) { set_error(wp, "out of memory"); return LAZ_FALSE; }
        for (i = 0; i < num_items; i++) {
            wp->writers_compressed[i] = laz_writeitem_new_compressed(&items[i], &wp->enc);
            if (!wp->writers_compressed[i]) {
                set_error(wp, "item type %u version %u is not supported",
                          items[i].type, items[i].version);
                return LAZ_FALSE;
            }
        }
        if (compressor != LAZ_COMPRESSOR_POINTWISE) {
            if (chunk_size) wp->chunk_size = chunk_size;
            wp->chunked = LAZ_TRUE;
        }
    }

    return LAZ_TRUE;
}

void laz_writepoint_init_point(const LazWritePoint *wp, LazPoint *point)
{
    point->extended_point_type = wp->has_point14 ? 1 : 0;
}

BOOL laz_writepoint_init(LazWritePoint *wp, LazOutStream *outstream)
{
    U32 i;
    if (!outstream) return LAZ_FALSE;
    wp->outstream = outstream;

    if (wp->chunked) {
        /* the position of the chunk table, once there is one. A stream that
         * cannot seek gets -1 here and the real position after the table. */
        wp->chunk_table_start_position =
            outstream->seekable ? laz_outstream_tell(outstream) : -1;
        laz_outstream_put64(outstream, (U64)wp->chunk_table_start_position);
        wp->chunk_start_position = laz_outstream_tell(outstream);
    }

    for (i = 0; i < wp->num_writers; i++) wp->writers_raw[i]->outstream = outstream;

    /* a compressed stream starts at a chunk head, where writers is NULL */
    wp->writers = wp->have_enc ? NULL : wp->writers_raw;
    wp->chunk_count = 0;
    return LAZ_TRUE;
}

/* Ends the open chunk on the stream, leaving the byte after it as the write
 * position. The two shapes differ entirely: a pointwise chunk is one
 * arithmetic stream and simply finishes, while a layered chunk has been
 * buffered per layer and is only now laid out -- point count, every writer's
 * layer sizes, then every writer's layer bytes, which is the order
 * laz_readpoint_read expects to find them in. */
static void close_chunk(LazWritePoint *wp)
{
    U32 i;
    if (wp->layered_las14_compression) {
        laz_outstream_put32(wp->outstream, wp->chunk_count);
        for (i = 0; i < wp->num_writers; i++)
            wp->writers_compressed[i]->chunk_sizes(wp->writers_compressed[i]);
        for (i = 0; i < wp->num_writers; i++)
            wp->writers_compressed[i]->chunk_bytes(wp->writers_compressed[i]);
    } else {
        laz_encoder_done(&wp->enc);
    }
}

static BOOL add_chunk_to_table(LazWritePoint *wp)
{
    I64 position;

    if (wp->number_chunks == wp->alloced_chunks) {
        U32 want = wp->alloced_chunks ? wp->alloced_chunks * 2 : 1024;
        U32 *grown;
        if (wp->chunk_size == U32_MAX) {
            grown = (U32 *)realloc(wp->chunk_sizes, sizeof(U32) * want);
            if (!grown) { set_error(wp, "out of memory"); return LAZ_FALSE; }
            wp->chunk_sizes = grown;
        }
        grown = (U32 *)realloc(wp->chunk_bytes, sizeof(U32) * want);
        if (!grown) { set_error(wp, "out of memory"); return LAZ_FALSE; }
        wp->chunk_bytes = grown;
        wp->alloced_chunks = want;
    }

    position = laz_outstream_tell(wp->outstream);
    if (wp->chunk_size == U32_MAX) wp->chunk_sizes[wp->number_chunks] = wp->chunk_count;
    wp->chunk_bytes[wp->number_chunks] = (U32)(position - wp->chunk_start_position);
    wp->chunk_start_position = position;
    wp->number_chunks++;
    return LAZ_TRUE;
}

/* Ends the open chunk and records it, leaving the writer at a chunk head:
 * `writers` NULL, so the next point goes out raw and seeds the compressed
 * writers again. */
static BOOL close_and_record(LazWritePoint *wp)
{
    close_chunk(wp);
    if (!add_chunk_to_table(wp)) return LAZ_FALSE;
    wp->writers = NULL;
    wp->chunk_count = 0;
    return LAZ_TRUE;
}

/*
 * The chunk table itself: a version, the chunk count, and then the two columns
 * -- point counts, for adaptive chunking only, and byte lengths -- entropy
 * coded as deltas from the previous row, exactly as read_chunk_table decodes
 * them. It goes after the point data, so its own position has to be recorded
 * in the eight bytes laz_writepoint_init left in front of the first chunk.
 */
static BOOL write_chunk_table(LazWritePoint *wp)
{
    LazOutStream *out = wp->outstream;
    I64 position = laz_outstream_tell(out);
    U32 i;

    if (wp->chunk_table_start_position != -1) {
        if (!laz_outstream_seek(out, wp->chunk_table_start_position)) {
            set_error(wp, "could not seek back to patch the chunk table offset");
            return LAZ_FALSE;
        }
        laz_outstream_put64(out, (U64)position);
        if (!laz_outstream_seek(out, position)) {
            set_error(wp, "could not seek forward again past the point data");
            return LAZ_FALSE;
        }
    }

    laz_outstream_put32(out, 0);                    /* chunk table version */
    laz_outstream_put32(out, wp->number_chunks);

    if (wp->number_chunks > 0) {
        LazIntCompressor ic;
        laz_encoder_init(&wp->enc, out);
        laz_ic_setup_enc(&ic, &wp->enc, 32, 2, 8, 0);
        if (!laz_ic_init_compressor(&ic)) {
            laz_ic_free(&ic);
            set_error(wp, "out of memory");
            return LAZ_FALSE;
        }
        for (i = 0; i < wp->number_chunks; i++) {
            if (wp->chunk_size == U32_MAX) {
                laz_ic_compress(&ic, (I32)(i ? wp->chunk_sizes[i - 1] : 0),
                                (I32)wp->chunk_sizes[i], 0);
            }
            laz_ic_compress(&ic, (I32)(i ? wp->chunk_bytes[i - 1] : 0),
                            (I32)wp->chunk_bytes[i], 1);
        }
        laz_ic_free(&ic);
        laz_encoder_done(&wp->enc);
    }

    /* the output could not seek, so the position nobody could patch in goes
     * at the very end instead; read_chunk_table looks for it there */
    if (wp->chunk_table_start_position == -1) laz_outstream_put64(out, (U64)position);

    return LAZ_TRUE;
}

BOOL laz_writepoint_write(LazWritePoint *wp, const LazPoint *point,
                          const U8 *extra_bytes)
{
    U32 i;
    U32 context = 0;
    const U8 *src[LAZ_MAX_ITEMS];
    const U8 *base = (const U8 *)point;

    /* offsets were resolved at setup; this is just the per-call rebase */
    for (i = 0; i < wp->num_writers; i++) {
        src[i] = (wp->item_offsets[i] < 0) ? extra_bytes : (base + wp->item_offsets[i]);
    }

    /* Only a compressed stream is chunked, and only it counts points: an
     * uncompressed one has no boundaries to reach and nothing to close. */
    if (wp->have_enc) {
        if (wp->chunk_count == wp->chunk_size && !close_and_record(wp)) return LAZ_FALSE;
        wp->chunk_count++;
    }

    if (wp->writers) {
        for (i = 0; i < wp->num_writers; i++) {
            if (!wp->writers[i]->write(wp->writers[i], src[i], &context)) {
                set_error(wp, "writing item %u of chunk %u failed", i, wp->number_chunks);
                return LAZ_FALSE;
            }
        }
    } else {
        /* first point of a chunk is stored raw and seeds the predictors */
        for (i = 0; i < wp->num_writers; i++) {
            if (!wp->writers_raw[i]->write(wp->writers_raw[i], src[i], &context) ||
                !wp->writers_compressed[i]->init(wp->writers_compressed[i], src[i], &context)) {
                set_error(wp, "starting chunk %u failed", wp->number_chunks);
                return LAZ_FALSE;
            }
        }
        wp->writers = wp->writers_compressed;
        /* pointwise: the stream the encoder emits into; layered: only how the
         * writers reach the chunk's output */
        laz_encoder_init(&wp->enc, wp->outstream);
    }

    if (wp->outstream->failed) {
        set_error(wp, "error writing to the underlying file");
        return LAZ_FALSE;
    }
    return LAZ_TRUE;
}

BOOL laz_writepoint_chunk(LazWritePoint *wp)
{
    if (!wp->chunked || wp->chunk_size != U32_MAX) {
        set_error(wp, "only a variable-size chunk can be closed early");
        return LAZ_FALSE;
    }
    /* nothing written since the last boundary: there is no chunk to close, and
     * an empty one is not something a reader could make sense of */
    if (wp->writers != wp->writers_compressed) return LAZ_TRUE;

    return close_and_record(wp);
}

BOOL laz_writepoint_done(LazWritePoint *wp)
{
    if (!wp->outstream) {
        set_error(wp, "the point writer has no output stream");
        return LAZ_FALSE;
    }

    if (wp->writers == wp->writers_compressed) {
        /* a chunk is open, and it has at least the point that opened it */
        close_chunk(wp);
        if (wp->chunked) {
            if (!add_chunk_to_table(wp)) return LAZ_FALSE;
            if (!write_chunk_table(wp)) return LAZ_FALSE;
        }
    } else if (wp->writers == NULL) {
        /* compressed, with no points written since the last chunk closed */
        if (wp->chunked && !write_chunk_table(wp)) return LAZ_FALSE;
    }

    /* the file is finished, so nothing may sit in the sink's buffer */
    laz_outstream_flush(wp->outstream);

    if (wp->outstream->failed) {
        set_error(wp, "error writing to the underlying file");
        return LAZ_FALSE;
    }
    return LAZ_TRUE;
}

void laz_writepoint_destroy(LazWritePoint *wp)
{
    U32 i;
    if (wp->writers_raw) {
        for (i = 0; i < wp->num_writers; i++) laz_writeitem_destroy(wp->writers_raw[i]);
        free(wp->writers_raw);
        wp->writers_raw = NULL;
    }
    if (wp->writers_compressed) {
        for (i = 0; i < wp->num_writers; i++) laz_writeitem_destroy(wp->writers_compressed[i]);
        free(wp->writers_compressed);
        wp->writers_compressed = NULL;
    }
    wp->writers = NULL;
    laz_encoder_free(&wp->enc);
    free(wp->chunk_sizes); wp->chunk_sizes = NULL;
    free(wp->chunk_bytes); wp->chunk_bytes = NULL;
}
