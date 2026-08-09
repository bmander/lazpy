/*
 * laz_stream.h -- byte-oriented input streams.
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

#endif /* LAZ_STREAM_H */
