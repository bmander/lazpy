/*
 * Derived from LASzip (https://github.com/LASzip/LASzip), lasreadpoint.cpp.
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

#include <stdio.h>
#include <stdarg.h>
#include "laz_readpoint.h"

static void set_error(LazReadPoint *rp, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(rp->last_error, sizeof(rp->last_error), fmt, ap);
    va_end(ap);
    rp->has_error = LAZ_TRUE;
}

static void set_warning(LazReadPoint *rp, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(rp->last_warning, sizeof(rp->last_warning), fmt, ap);
    va_end(ap);
    rp->has_warning = LAZ_TRUE;
}

void laz_readpoint_init_struct(LazReadPoint *rp, U32 decompress_selective)
{
    memset(rp, 0, sizeof(*rp));
    rp->chunk_size = U32_MAX;
    rp->decompress_selective = decompress_selective;
}

static LazReadItem *make_compressed_reader(const LazItem *item, LazDecoder *dec,
                                           U32 selective)
{
    U32 v = item->version;
    switch (item->type) {
    case LAZ_ITEM_POINT10:
        if (v == 1) return laz_readitem_v1_point10(dec);
        if (v == 2) return laz_readitem_v2_point10(dec);
        return NULL;
    case LAZ_ITEM_GPSTIME11:
        if (v == 1) return laz_readitem_v1_gpstime11(dec);
        if (v == 2) return laz_readitem_v2_gpstime11(dec);
        return NULL;
    case LAZ_ITEM_RGB12:
        if (v == 1) return laz_readitem_v1_rgb12(dec);
        if (v == 2) return laz_readitem_v2_rgb12(dec);
        return NULL;
    case LAZ_ITEM_BYTE:
        if (v == 1) return laz_readitem_v1_byte(dec, item->size);
        if (v == 2) return laz_readitem_v2_byte(dec, item->size);
        return NULL;
    /* version 2 is accepted for the 1.4 items because lasproto wrote it */
    case LAZ_ITEM_POINT14:
        if (v == 2 || v == 3) return laz_readitem_v3_point14(dec, selective);
        if (v == 4) return laz_readitem_v4_point14(dec, selective);
        return NULL;
    case LAZ_ITEM_RGB14:
        if (v == 2 || v == 3) return laz_readitem_v3_rgb14(dec, selective);
        if (v == 4) return laz_readitem_v4_rgb14(dec, selective);
        return NULL;
    case LAZ_ITEM_RGBNIR14:
        if (v == 2 || v == 3) return laz_readitem_v3_rgbnir14(dec, selective);
        if (v == 4) return laz_readitem_v4_rgbnir14(dec, selective);
        return NULL;
    case LAZ_ITEM_BYTE14:
        if (v == 2 || v == 3) return laz_readitem_v3_byte14(dec, item->size, selective);
        if (v == 4) return laz_readitem_v4_byte14(dec, item->size, selective);
        return NULL;
    case LAZ_ITEM_WAVEPACKET13:
        if (v == 1) return laz_readitem_v1_wavepacket13(dec);
        return NULL;
    case LAZ_ITEM_WAVEPACKET14:
        if (v == 3) return laz_readitem_v3_wavepacket14(dec, selective);
        if (v == 4) return laz_readitem_v4_wavepacket14(dec, selective);
        return NULL;
    default:
        return NULL;
    }
}

BOOL laz_readpoint_setup(LazReadPoint *rp, U32 num_items, const LazItem *items,
                         U32 compressor, U32 coder, U32 chunk_size)
{
    U32 i;

    if (num_items == 0 || items == NULL) {
        set_error(rp, "no items to read");
        return LAZ_FALSE;
    }
    if (num_items > LAZ_MAX_ITEMS) {
        set_error(rp, "too many items (%u, maximum %u)", num_items, LAZ_MAX_ITEMS);
        return LAZ_FALSE;
    }

    rp->items = (LazItem *)malloc(num_items * sizeof(LazItem));
    if (!rp->items) { set_error(rp, "out of memory"); return LAZ_FALSE; }
    memcpy(rp->items, items, num_items * sizeof(LazItem));

    rp->num_readers = num_items;
    rp->have_dec = LAZ_FALSE;
    rp->layered_las14_compression = LAZ_FALSE;
    rp->chunk_size = U32_MAX;

    if (compressor > LAZ_COMPRESSOR_LAYERED_CHUNKED) {
        set_error(rp, "compressor %u is not supported", compressor);
        return LAZ_FALSE;
    }

    if (compressor) {
        if (coder != LAZ_CODER_ARITHMETIC) {
            set_error(rp, "entropy coder %u is not supported", coder);
            return LAZ_FALSE;
        }
        laz_decoder_setup(&rp->dec, NULL);
        rp->have_dec = LAZ_TRUE;
        rp->layered_las14_compression = (compressor == LAZ_COMPRESSOR_LAYERED_CHUNKED);
    }

    /* raw readers always exist: they read the first point of every chunk */
    rp->readers_raw = (LazReadItem **)calloc(num_items, sizeof(LazReadItem *));
    if (!rp->readers_raw) { set_error(rp, "out of memory"); return LAZ_FALSE; }
    for (i = 0; i < num_items; i++) {
        rp->item_offsets[i] = laz_item_offset(items[i].type);
        if (rp->item_offsets[i] == -2) {
            set_error(rp, "item type %u is not supported", items[i].type);
            return LAZ_FALSE;
        }
        if (rp->item_offsets[i] == -1) rp->num_extra_bytes += items[i].size;
        rp->readers_raw[i] = laz_readitem_new_raw(&items[i], NULL);
        if (!rp->readers_raw[i]) {
            set_error(rp, "item type %u is not supported", items[i].type);
            return LAZ_FALSE;
        }
        if (items[i].type == LAZ_ITEM_POINT14) rp->has_point14 = LAZ_TRUE;
        rp->point_size += items[i].size;
    }

    if (rp->have_dec) {
        rp->readers_compressed = (LazReadItem **)calloc(num_items, sizeof(LazReadItem *));
        if (!rp->readers_compressed) { set_error(rp, "out of memory"); return LAZ_FALSE; }
        for (i = 0; i < num_items; i++) {
            rp->readers_compressed[i] =
                make_compressed_reader(&items[i], &rp->dec, rp->decompress_selective);
            if (!rp->readers_compressed[i]) {
                set_error(rp, "item type %u version %u is not supported",
                          items[i].type, items[i].version);
                return LAZ_FALSE;
            }
            /*
             * The container and the item versions have to agree, and a file
             * can declare a pair that does not: the LASzip VLR carries the
             * compressor and the per-item versions as separate fields.
             * Reading either one the other's way is undefined rather than
             * merely wrong -- a flat item in a layered chunk is asked for
             * layer sizes it has no function for, and crashes.
             *
             * Which kind an item is, is not restated here as a version
             * table: a layered reader is exactly one that knows how to read
             * its layer sizes. The mirror of laz_writepoint_setup's check.
             */
            if ((rp->readers_compressed[i]->chunk_sizes != NULL)
                    != rp->layered_las14_compression) {
                set_error(rp, "item type %u version %u cannot be read from "
                          "compressor %u", items[i].type, items[i].version,
                          compressor);
                return LAZ_FALSE;
            }
        }
        if (compressor != LAZ_COMPRESSOR_POINTWISE) {
            if (chunk_size) rp->chunk_size = chunk_size;
            rp->number_chunks = U32_MAX;
        }
        /* a point buffer for skipping over points while seeking */
        rp->seek_extra_bytes = (U8 *)calloc(rp->point_size + 1, 1);
        if (!rp->seek_extra_bytes) { set_error(rp, "out of memory"); return LAZ_FALSE; }
        laz_readpoint_init_point(rp, &rp->seek_point);
    }

    return LAZ_TRUE;
}

void laz_readpoint_init_point(const LazReadPoint *rp, LazPoint *point)
{
    if (rp->has_point14) laz_point_set_extended_point_type(point, 1);
}

BOOL laz_readpoint_init(LazReadPoint *rp, LazStream *instream)
{
    U32 i;
    if (!instream) return LAZ_FALSE;
    rp->instream = instream;

    for (i = 0; i < rp->num_readers; i++) rp->readers_raw[i]->instream = instream;

    if (rp->have_dec) {
        rp->chunk_count = rp->chunk_size;
        rp->point_start = 0;
        rp->readers = NULL;
    } else {
        rp->point_start = laz_stream_tell(instream);
        rp->readers = rp->readers_raw;
    }
    return LAZ_TRUE;
}

static BOOL read_chunk_table(LazReadPoint *rp)
{
    I64 chunk_table_start_position;
    I64 chunks_start;
    BOOL failed = LAZ_FALSE;
    U32 version, declared, i;

    chunk_table_start_position = (I64)laz_stream_get64(rp->instream);
    chunks_start = laz_stream_tell(rp->instream);

    /* compressor interrupted before it could write the chunk table? */
    if ((chunk_table_start_position + 8) == chunks_start) {
        if (rp->chunk_size == U32_MAX) {
            set_error(rp, "compressor was interrupted before writing an adaptive chunk table");
            return LAZ_FALSE;
        }
        rp->number_chunks = 256;
        rp->chunk_starts = (I64 *)malloc(sizeof(I64) * (rp->number_chunks + 1));
        if (!rp->chunk_starts) { set_error(rp, "out of memory"); return LAZ_FALSE; }
        rp->chunk_starts[0] = chunks_start;
        rp->tabled_chunks = 1;
        set_warning(rp, "compressor was interrupted before writing the chunk table");
        return LAZ_TRUE;
    }

    if (!rp->instream->seekable) {
        if (rp->chunk_size == U32_MAX) {
            set_error(rp, "adaptive chunking needs a seekable stream");
            return LAZ_FALSE;
        }
        rp->number_chunks = 0;
        rp->tabled_chunks = 0;
        return LAZ_TRUE;
    }

    if (chunk_table_start_position == -1) {
        /* written to a non-seekable stream: the position is at the very end */
        if (!laz_stream_seek_end(rp->instream, 8)) return LAZ_FALSE;
        chunk_table_start_position = (I64)laz_stream_get64(rp->instream);
    }

    do {
        if (!laz_stream_seek(rp->instream, chunk_table_start_position)) { failed = LAZ_TRUE; break; }
        if (laz_stream_tell(rp->instream) != chunk_table_start_position) { failed = LAZ_TRUE; break; }

        version = laz_stream_get32(rp->instream);
        if (version != 0) { failed = LAZ_TRUE; break; }

        /*
         * The count is a u32 out of the file and two arrays are sized from
         * it, so a corrupt one is a couple of eight-byte-per-chunk mallocs of
         * whatever it says -- eleven gigabytes each, for one file the fuzzer
         * mutated. A chunk occupies at least a byte of the point data it
         * describes, so there cannot be more chunks than there are bytes
         * between the first one and the table itself.
         *
         * Rejected before it is adopted, not after: number_chunks staying
         * U32_MAX is what tells the recovery below that no usable count was
         * read, and what keeps it from leaving a count without the
         * chunk_starts that every later read indexes.
         */
        declared = laz_stream_get32(rp->instream);
        if ((I64)declared > chunk_table_start_position - chunks_start) {
            failed = LAZ_TRUE;
            break;
        }
        rp->number_chunks = declared;
        free(rp->chunk_totals); rp->chunk_totals = NULL;
        free(rp->chunk_starts); rp->chunk_starts = NULL;

        if (rp->chunk_size == U32_MAX) {
            rp->chunk_totals = (U64 *)malloc(sizeof(U64) * ((U64)rp->number_chunks + 1));
            if (!rp->chunk_totals) { failed = LAZ_TRUE; break; }
            rp->chunk_totals[0] = 0;
        }
        rp->chunk_starts = (I64 *)malloc(sizeof(I64) * ((U64)rp->number_chunks + 1));
        if (!rp->chunk_starts) { failed = LAZ_TRUE; break; }
        rp->chunk_starts[0] = chunks_start;
        rp->tabled_chunks = 1;

        if (rp->number_chunks > 0) {
            LazIntCompressor ic;
            laz_decoder_init(&rp->dec, rp->instream, LAZ_TRUE);
            laz_ic_setup_dec(&ic, &rp->dec, 32, 2, 8, 0);
            if (!laz_ic_init_decompressor(&ic)) { laz_ic_free(&ic); failed = LAZ_TRUE; break; }
            for (i = 1; i <= rp->number_chunks; i++) {
                if (rp->chunk_size == U32_MAX) {
                    rp->chunk_totals[i] = (U64)(U32)laz_ic_decompress(
                        &ic, (i > 1 ? (I32)(U32)rp->chunk_totals[i - 1] : 0), 0);
                }
                rp->chunk_starts[i] = (I64)laz_ic_decompress(
                    &ic, (i > 1 ? (I32)(U32)rp->chunk_starts[i - 1] : 0), 1);
                rp->tabled_chunks++;
            }
            laz_ic_free(&ic);
            laz_decoder_done(&rp->dec);

            /* the table stores deltas; accumulate into absolute values */
            for (i = 1; i <= rp->number_chunks; i++) {
                if (rp->chunk_size == U32_MAX) rp->chunk_totals[i] += rp->chunk_totals[i - 1];
                rp->chunk_starts[i] += rp->chunk_starts[i - 1];
                if (rp->chunk_starts[i] <= rp->chunk_starts[i - 1]) { failed = LAZ_TRUE; break; }
            }
            if (failed) break;
        }
    } while (0);

    if (failed) {
        free(rp->chunk_totals);
        rp->chunk_totals = NULL;
        if (rp->chunk_size == U32_MAX) {
            set_error(rp, "adaptive chunk table is corrupt");
            return LAZ_FALSE;
        }
        if (rp->number_chunks == U32_MAX) {
            /* never even got the chunk count */
            rp->number_chunks = 256;
            rp->chunk_starts = (I64 *)malloc(sizeof(I64) * ((U64)rp->number_chunks + 1));
            if (!rp->chunk_starts) { set_error(rp, "out of memory"); return LAZ_FALSE; }
            rp->chunk_starts[0] = chunks_start;
            rp->tabled_chunks = 1;
        } else if (rp->chunk_starts) {
            /* salvage as many chunk starts as were read */
            for (i = 1; i < rp->tabled_chunks; i++) {
                rp->chunk_starts[i] += rp->chunk_starts[i - 1];
            }
        }
        set_warning(rp, "corrupt or missing chunk table");
    }

    if (!laz_stream_seek(rp->instream, chunks_start)) return LAZ_FALSE;
    return LAZ_TRUE;
}

/* Whether *index* is a chunk this file's table describes, which is what every
 * reader of chunk_totals has to establish first. Neither test it might be
 * mistaken for answers it: the array is allocated for a table of no chunks
 * too, holding only the zero it starts from, and chunk_size == U32_MAX stops
 * meaning "adaptive" the moment init_dec overwrites it below. */
static BOOL chunk_is_tabled(const LazReadPoint *rp, U32 index)
{
    return rp->chunk_totals != NULL && index < rp->number_chunks;
}

static BOOL init_dec(LazReadPoint *rp)
{
    if (rp->number_chunks == U32_MAX) {
        if (!read_chunk_table(rp)) return LAZ_FALSE;
        rp->current_chunk = 0;
        if (chunk_is_tabled(rp, 0)) rp->chunk_size = (U32)rp->chunk_totals[1];
    }
    rp->point_start = laz_stream_tell(rp->instream);
    rp->readers = NULL;
    return LAZ_TRUE;
}

BOOL laz_readpoint_read(LazReadPoint *rp, LazPoint *point, U8 *extra_bytes)
{
    U32 i;
    U32 context = 0;
    U8 *dst[LAZ_MAX_ITEMS];
    U8 *base = (U8 *)point;

    /* offsets were resolved at setup; this is just the per-call rebase */
    for (i = 0; i < rp->num_readers; i++) {
        dst[i] = (rp->item_offsets[i] < 0) ? extra_bytes : (base + rp->item_offsets[i]);
    }

    if (rp->have_dec) {
        if (rp->chunk_count == rp->chunk_size) {
            if (rp->point_start != 0) {
                laz_decoder_done(&rp->dec);
                rp->current_chunk++;
                /* if the table says where this chunk starts, verify we are there */
                if (rp->current_chunk < rp->tabled_chunks) {
                    I64 here = laz_stream_tell(rp->instream);
                    if (rp->chunk_starts[rp->current_chunk] != here) {
                        rp->current_chunk--;
                        set_error(rp, "chunk with index %u is corrupt", rp->current_chunk);
                        return LAZ_FALSE;
                    }
                }
            }
            if (!init_dec(rp)) return LAZ_FALSE;

            if (rp->current_chunk == rp->tabled_chunks) {
                /* no or incomplete chunk table: build it as we go */
                if (rp->current_chunk >= rp->number_chunks) {
                    I64 *grown;
                    rp->number_chunks += 256;
                    grown = (I64 *)realloc(rp->chunk_starts,
                                           sizeof(I64) * ((U64)rp->number_chunks + 1));
                    if (!grown) { set_error(rp, "out of memory"); return LAZ_FALSE; }
                    rp->chunk_starts = grown;
                }
                rp->chunk_starts[rp->tabled_chunks] = rp->point_start;
                rp->tabled_chunks++;
            } else if (chunk_is_tabled(rp, rp->current_chunk)) {
                rp->chunk_size = (U32)(rp->chunk_totals[rp->current_chunk + 1] -
                                       rp->chunk_totals[rp->current_chunk]);
            } else if (rp->chunk_size == U32_MAX) {
                /*
                 * Adaptive chunking with no size for the chunk about to be
                 * decoded, which means either the table lied about how many
                 * there are or the caller has read past the last point.
                 * Carrying on would leave chunk_size at U32_MAX, so no later
                 * read would ever take a chunk transition and the decoder
                 * would never restart at the boundaries the file really has:
                 * every point from the first of them on comes back wrong,
                 * with nothing raised. Where the boundaries are fixed the
                 * branch above rebuilds the table instead, which recovers
                 * such a file completely; adaptive boundaries are not
                 * recoverable, which is why the file was asked to state them.
                 */
                set_error(rp, "chunk table gives no size for chunk %u",
                          rp->current_chunk);
                return LAZ_FALSE;
            }
            rp->chunk_count = 0;
        }
        rp->chunk_count++;

        if (rp->readers) {
            for (i = 0; i < rp->num_readers; i++) {
                LazReadItem *rd = rp->readers[i];
                rd->read(rd, dst[i], &context);
                /* a model this reader had to create partway through the chunk
                 * could not be allocated, so the point is only half decoded */
                if (rd->alloc_failed) {
                    set_error(rp, "out of memory");
                    return LAZ_FALSE;
                }
            }
        } else {
            /* first point of a chunk is stored raw and seeds the predictors */
            for (i = 0; i < rp->num_readers; i++) {
                rp->readers_raw[i]->read(rp->readers_raw[i], dst[i], &context);
            }
            if (rp->layered_las14_compression) {
                /* for layered compression the decoder only carries the stream */
                laz_decoder_init(&rp->dec, rp->instream, LAZ_FALSE);
                /* point count of this chunk, then every layer's byte count */
                (void)laz_stream_get32(rp->instream);
                for (i = 0; i < rp->num_readers; i++) {
                    if (!rp->readers_compressed[i]->chunk_sizes(rp->readers_compressed[i])) {
                        set_error(rp, "reading layer sizes of chunk %u failed", rp->current_chunk);
                        return LAZ_FALSE;
                    }
                }
                for (i = 0; i < rp->num_readers; i++) {
                    if (!rp->readers_compressed[i]->init(rp->readers_compressed[i], dst[i], &context)) {
                        set_error(rp, "initialising chunk %u failed", rp->current_chunk);
                        return LAZ_FALSE;
                    }
                }
            } else {
                for (i = 0; i < rp->num_readers; i++) {
                    if (!rp->readers_compressed[i]->init(rp->readers_compressed[i], dst[i], &context)) {
                        set_error(rp, "initialising chunk %u failed", rp->current_chunk);
                        return LAZ_FALSE;
                    }
                }
                laz_decoder_init(&rp->dec, rp->instream, LAZ_TRUE);
            }
            rp->readers = rp->readers_compressed;
        }
    } else {
        for (i = 0; i < rp->num_readers; i++) {
            rp->readers[i]->read(rp->readers[i], dst[i], &context);
        }
    }

    if (laz_stream_eof(rp->instream)) {
        set_error(rp, "end-of-file during chunk with index %u", rp->current_chunk);
        return LAZ_FALSE;
    }
    /* a layered reader that ran off the end of one of its layers means the
     * chunk is corrupt, not that the point decoded to zeros */
    if (rp->layered_las14_compression && rp->readers == rp->readers_compressed) {
        for (i = 0; i < rp->num_readers; i++) {
            LazReadItem *rd = rp->readers_compressed[i];
            if (rd->overran && rd->overran(rd)) {
                set_error(rp, "chunk with index %u is corrupt (layer truncated)",
                          rp->current_chunk);
                return LAZ_FALSE;
            }
        }
    }
    return LAZ_TRUE;
}

/* Which chunk holds point *index*, by bisection over the running totals. The
 * caller guarantees a range with a chunk in it; the loop stops on an empty
 * one anyway, since an equality test there would spin on a range it can never
 * close. */
static U32 search_chunk_table(const LazReadPoint *rp, U64 index, U32 lower, U32 upper)
{
    while (lower + 1 < upper) {
        U32 mid = (lower + upper) / 2;
        if (index >= rp->chunk_totals[mid]) lower = mid; else upper = mid;
    }
    return lower;
}

BOOL laz_readpoint_seek(LazReadPoint *rp, U64 current, U64 target)
{
    U64 delta = 0;

    if (!rp->instream->seekable) return LAZ_FALSE;

    if (!rp->have_dec) {
        if (current != target) {
            return laz_stream_seek(rp->instream,
                                   rp->point_start + (I64)rp->point_size * (I64)target);
        }
        return LAZ_TRUE;
    }

    if (rp->point_start == 0) {
        if (!init_dec(rp)) return LAZ_FALSE;
        rp->chunk_count = 0;
    }

    if (rp->chunk_starts) {
        U32 target_chunk;
        if (chunk_is_tabled(rp, 0)) {
            target_chunk = search_chunk_table(rp, target, 0, rp->number_chunks);
            rp->chunk_size = (U32)(rp->chunk_totals[target_chunk + 1] -
                                   rp->chunk_totals[target_chunk]);
            delta = target - rp->chunk_totals[target_chunk];
        } else {
            target_chunk = (U32)(target / rp->chunk_size);
            delta = target % rp->chunk_size;
        }
        if (target_chunk >= rp->tabled_chunks) {
            /* beyond the table: decode forward from the last known chunk */
            if (rp->current_chunk < (rp->tabled_chunks - 1)) {
                laz_decoder_done(&rp->dec);
                rp->current_chunk = rp->tabled_chunks - 1;
                if (!laz_stream_seek(rp->instream,
                                     rp->chunk_starts[rp->current_chunk]))
                    return LAZ_FALSE;
                if (!init_dec(rp)) return LAZ_FALSE;
                rp->chunk_count = 0;
            }
            delta += (rp->chunk_size * (target_chunk - rp->current_chunk)) - rp->chunk_count;
        } else if (rp->current_chunk != target_chunk || current > target) {
            laz_decoder_done(&rp->dec);
            rp->current_chunk = target_chunk;
            if (!laz_stream_seek(rp->instream,
                                 rp->chunk_starts[rp->current_chunk]))
                return LAZ_FALSE;
            if (!init_dec(rp)) return LAZ_FALSE;
            rp->chunk_count = 0;
        } else {
            delta = target - current;
        }
    } else if (current > target) {
        laz_decoder_done(&rp->dec);
        if (!laz_stream_seek(rp->instream, rp->point_start)) return LAZ_FALSE;
        if (!init_dec(rp)) return LAZ_FALSE;
        delta = target;
    } else if (current < target) {
        delta = target - current;
    }

    while (delta) {
        if (!laz_readpoint_read(rp, &rp->seek_point, rp->seek_extra_bytes)) return LAZ_FALSE;
        delta--;
    }
    return LAZ_TRUE;
}

void laz_readpoint_destroy(LazReadPoint *rp)
{
    U32 i;
    if (rp->readers_raw) {
        for (i = 0; i < rp->num_readers; i++) laz_readitem_destroy(rp->readers_raw[i]);
        free(rp->readers_raw);
        rp->readers_raw = NULL;
    }
    if (rp->readers_compressed) {
        for (i = 0; i < rp->num_readers; i++) laz_readitem_destroy(rp->readers_compressed[i]);
        free(rp->readers_compressed);
        rp->readers_compressed = NULL;
    }
    rp->readers = NULL;
    free(rp->items); rp->items = NULL;
    free(rp->chunk_totals); rp->chunk_totals = NULL;
    free(rp->chunk_starts); rp->chunk_starts = NULL;
    free(rp->seek_extra_bytes); rp->seek_extra_bytes = NULL;
}
