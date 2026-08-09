/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_stream.h -- byte-oriented input and output streams.
 *
 * Ported from LASzip's ByteStreamIn hierarchy. Two backings are needed:
 *
 *   - "array": a fixed in-memory buffer. The layered LAS 1.4 (v3/v4) readers
 *     slice a chunk into per-layer sub-streams and give each its own decoder,
 *     so an in-memory stream is not an optimisation there but a requirement.
 *
 *   - "file": a Python file-like object. Reads are buffered, because the
 *     arithmetic decoder pulls single bytes during renormalisation and a
 *     Python-level call per byte dominates decode time otherwise.
 *
 * Every getter sets stream->eof on underrun rather than raising; callers check
 * laz_stream_error() at a granularity that makes sense for them.
 */
#ifndef LAZ_STREAM_H
#define LAZ_STREAM_H

#include "laz_types.h"

typedef struct LazStream LazStream;

struct LazStream {
    /* vtable */
    U32  (*get_byte)(LazStream *s);
    void (*get_bytes)(LazStream *s, U8 *bytes, I64 num_bytes);
    I64  (*tell)(LazStream *s);
    BOOL (*seek)(LazStream *s, I64 position);
    BOOL (*seek_end)(LazStream *s, I64 distance);
    void (*destroy)(LazStream *s);

    BOOL seekable;
    BOOL eof;          /* set once a read ran past the end of the stream */
    BOOL failed;       /* set when the underlying stream itself errored, as
                        * opposed to simply running out; the binding then
                        * propagates the original exception */

    void *impl;
};

/* Wraps a Python file-like object (must supply read/seek/tell). Borrows a
 * reference to fp for the lifetime of the stream. */
LazStream *laz_stream_new_file(void *py_fp);

/* Wraps a caller-owned buffer; the buffer must outlive the stream. */
LazStream *laz_stream_new_array(const U8 *data, I64 size);

/* Repoints an existing array stream at a new buffer, keeping the object.
 * Used to recycle per-layer streams across chunks. */
void laz_stream_array_reset(LazStream *s, const U8 *data, I64 size);

void laz_stream_destroy(LazStream *s);

static inline U32 laz_stream_get_byte(LazStream *s) { return s->get_byte(s); }
static inline void laz_stream_get_bytes(LazStream *s, U8 *b, I64 n) { s->get_bytes(s, b, n); }
static inline I64 laz_stream_tell(LazStream *s) { return s->tell(s); }
static inline BOOL laz_stream_seek(LazStream *s, I64 p) { return s->seek(s, p); }
static inline BOOL laz_stream_seek_end(LazStream *s, I64 d) { return s->seek_end(s, d); }
static inline BOOL laz_stream_eof(LazStream *s) { return s->eof; }

U32 laz_stream_get32(LazStream *s);
U64 laz_stream_get64(LazStream *s);

/* ---------------------------------------------------------------- output */

/*
 * The write-side counterpart, ported from ByteStreamOut.
 *
 * The file sink is deliberately unbuffered, unlike its input-side twin: the
 * arithmetic encoder keeps its own ring buffer, because carry propagation has
 * to be able to reach back into already-produced bytes, so it hands over whole
 * AC_BUFFER_SIZE blocks and a second buffer underneath would earn nothing.
 *
 * The vtable mirrors LazStream because the layered v3/v4 writers need an
 * in-memory sink for the same reason the layered readers need an in-memory
 * source, and because the chunked writer patches its chunk-table offset by
 * seeking back to a position it remembered.
 *
 * tell() is a byte count the sink maintains itself rather than a question put
 * to the underlying object: a pipe has a perfectly well-defined write position
 * even though it cannot answer tell(), and the chunk table is a table of
 * positions either way.
 *
 * As on the input side, a failed write sets `failed` and leaves the Python
 * exception pending rather than raising through the C core.
 */
typedef struct LazOutStream LazOutStream;

struct LazOutStream {
    void (*put_bytes)(LazOutStream *s, const U8 *bytes, I64 num_bytes);
    I64  (*tell)(LazOutStream *s);
    BOOL (*seek)(LazOutStream *s, I64 position);
    void (*destroy)(LazOutStream *s);

    BOOL seekable;     /* clear when seek() will always fail, which is what
                        * makes a writer emit the -1 chunk-table offset */
    BOOL failed;

    void *impl;
};

/* Wraps a Python file-like object (must supply write; seeking additionally
 * needs seek, and is offered only when the object says it is seekable).
 * Borrows a reference to fp for the lifetime of the stream.
 *
 * `start_offset` is where in the file the object is already positioned, which
 * matters because a chunk table records absolute positions: pass a negative
 * value to have the object asked, which is right whenever it can answer, and
 * the true offset for an appending sink that cannot -- a pipe knows neither
 * where it is nor that anything came before it. */
LazOutStream *laz_outstream_new_file(void *py_fp, I64 start_offset);

/* Collects everything written into one growable buffer the stream owns.
 * laz_outstream_array_data hands back that buffer, valid until the next write
 * or destroy. Sets `failed` if it cannot grow. */
LazOutStream *laz_outstream_new_array(void);
const U8 *laz_outstream_array_data(LazOutStream *s, I64 *size);

/* Drops everything written so far but keeps the buffer, so the next chunk
 * refills it. The layered v3/v4 writers recycle one array sink per layer
 * across every chunk of a file. */
void laz_outstream_array_rewind(LazOutStream *s);

void laz_outstream_destroy(LazOutStream *s);

static inline void laz_outstream_put_bytes(LazOutStream *s, const U8 *b, I64 n)
{ s->put_bytes(s, b, n); }
static inline I64 laz_outstream_tell(LazOutStream *s) { return s->tell(s); }
static inline BOOL laz_outstream_seek(LazOutStream *s, I64 p) { return s->seek(s, p); }

/* The multi-byte fields a writer emits outside the entropy coder: a layered
 * chunk's per-layer byte counts, and the chunk table's own offset. */
void laz_outstream_put32(LazOutStream *s, U32 value);
void laz_outstream_put64(LazOutStream *s, U64 value);

#endif /* LAZ_STREAM_H */
