/*
 * Derived from LASzip (https://github.com/LASzip/LASzip), the ByteStreamIn
 * hierarchy.
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/* Required before Python.h on CPython below 3.13 for the "y#" format used to
 * hand a block to the file object; a no-op from 3.13 on, where the Py_ssize_t
 * lengths it selects became the only behaviour. */
#define PY_SSIZE_T_CLEAN
#include "Python.h"
#include "laz_stream.h"

/* ------------------------------------------------------------------ array */

typedef struct {
    const U8 *data;
    I64 size;
    I64 pos;
} ArrayImpl;

static U32 array_get_byte(LazStream *s)
{
    ArrayImpl *a = (ArrayImpl *)s->impl;
    if (a->pos >= a->size) { s->eof = LAZ_TRUE; return 0; }
    return a->data[a->pos++];
}

static void array_get_bytes(LazStream *s, U8 *bytes, I64 num_bytes)
{
    ArrayImpl *a = (ArrayImpl *)s->impl;
    if (num_bytes < 0) return;
    if (a->pos + num_bytes > a->size) {
        I64 avail = a->size - a->pos;
        if (avail < 0) avail = 0;
        if (avail) memcpy(bytes, a->data + a->pos, (size_t)avail);
        memset(bytes + avail, 0, (size_t)(num_bytes - avail));
        a->pos = a->size;
        s->eof = LAZ_TRUE;
        return;
    }
    memcpy(bytes, a->data + a->pos, (size_t)num_bytes);
    a->pos += num_bytes;
}

static I64 array_tell(LazStream *s) { return ((ArrayImpl *)s->impl)->pos; }

static BOOL array_seek(LazStream *s, I64 position)
{
    ArrayImpl *a = (ArrayImpl *)s->impl;
    if (position < 0 || position > a->size) return LAZ_FALSE;
    a->pos = position;
    return LAZ_TRUE;
}

static BOOL array_seek_end(LazStream *s, I64 distance)
{
    ArrayImpl *a = (ArrayImpl *)s->impl;
    if (distance < 0 || distance > a->size) return LAZ_FALSE;
    a->pos = a->size - distance;
    return LAZ_TRUE;
}

static void array_destroy(LazStream *s) { free(s->impl); }

LazStream *laz_stream_new_array(const U8 *data, I64 size)
{
    LazStream *s = (LazStream *)calloc(1, sizeof(LazStream));
    ArrayImpl *a;
    if (!s) return NULL;
    a = (ArrayImpl *)calloc(1, sizeof(ArrayImpl));
    if (!a) { free(s); return NULL; }
    a->data = data;
    a->size = size;
    a->pos = 0;
    s->impl = a;
    s->get_byte = array_get_byte;
    s->get_bytes = array_get_bytes;
    s->tell = array_tell;
    s->seek = array_seek;
    s->seek_end = array_seek_end;
    s->destroy = array_destroy;
    s->seekable = LAZ_TRUE;
    s->eof = LAZ_FALSE;
    return s;
}

void laz_stream_array_reset(LazStream *s, const U8 *data, I64 size)
{
    ArrayImpl *a = (ArrayImpl *)s->impl;
    a->data = data;
    a->size = size;
    a->pos = 0;
    s->eof = LAZ_FALSE;
}

/* ------------------------------------------------------------------- file */

/*
 * Buffered reader over a Python file-like object.
 *
 * `base` is the file offset of buf[0], so tell() is base + pos without
 * calling back into Python. Any seek drops the buffer.
 *
 * That accounting means the object's own position is only ever moved by this
 * code, and Python holds it too: lazpy.ExtendedVariableLengthRecord reads an
 * extended record's payload by seeking the same object and putting it back
 * exactly where it found it. Anything here that leaves the object somewhere
 * other than base + fill -- read-ahead, say -- has to be reflected there.
 */
#define FILE_BUF_SIZE 65536

typedef struct {
    PyObject *fp;
    U8 *buf;
    I64 base;       /* file offset corresponding to buf[0] */
    I64 fill;       /* valid bytes in buf */
    I64 pos;        /* read cursor within buf */
    I64 length;     /* whole file, found on demand; -2 until then, -1 if it
                     * cannot be found (see laz_stream_remaining) */
    /* A writable view of buf, for readinto: made once and reused, since
     * making one per refill would cost more than the copy it saves. NULL
     * where the file object has no readinto, which read() is the fallback
     * for -- BytesIO and open() both have one, a socket makefile may not. */
    PyObject *view;
} FileImpl;

/*
 * Gives up the readinto view, with the GIL held.
 *
 * Released and not merely dropped: it is writable and points into a buffer
 * this stream will free, and it has been handed to a file object that may
 * have kept a reference. release() makes any such reference raise rather
 * than write into freed memory. It raises itself if a slice of it is still
 * exported, which is not this code's problem but must not be left pending.
 */
static void file_drop_view(FileImpl *f)
{
    PyObject *type, *value, *traceback, *res;
    if (!f->view) return;

    /* This runs from the destructor too, where the exception that is ending
     * the read is already set -- and calling into Python with one pending is
     * illegal. Put it aside and hand it back. */
    PyErr_Fetch(&type, &value, &traceback);

    res = PyObject_CallMethod(f->view, "release", NULL);
    Py_XDECREF(res);
    if (!res) PyErr_Clear();
    Py_DECREF(f->view);
    f->view = NULL;

    PyErr_Restore(type, value, traceback);
}

/* Refills the buffer from the current logical position. Returns bytes read. */
static I64 file_refill(LazStream *s)
{
    FileImpl *f = (FileImpl *)s->impl;
    PyObject *res;
    Py_ssize_t n;
    char *data;
    PyGILState_STATE gil;

    /* Once failed the stream is inert: an exception is pending, so calling
     * back into Python again would be illegal. Decoding continues on zeros
     * until the caller notices and propagates. */
    if (s->failed) return 0;

    gil = PyGILState_Ensure();

    f->base += f->pos;
    f->pos = 0;
    f->fill = 0;

    /* On failure the Python exception is deliberately left set: the binding
     * propagates it rather than reporting a generic end-of-file, so a
     * PermissionError or a file object returning a non-bytes value is
     * distinguishable from a genuinely truncated file. */
    if (f->view) {
        /* readinto fills the buffer where it lies. read() would allocate a
         * 64 KB bytes object, have it copied here, and free it again. */
        res = PyObject_CallMethod(f->fp, "readinto", "O", f->view);
        if (res == NULL) {
            /* Having a readinto is not the same as having one that works:
             * io.RawIOBase supplies one that raises NotImplementedError, and
             * an attribute can be anything at all. Take the refusal as it
             * comes and use read() from here on -- but only for a refusal;
             * a PermissionError from a real read is the caller's to see. */
            if (PyErr_ExceptionMatches(PyExc_NotImplementedError) ||
                PyErr_ExceptionMatches(PyExc_TypeError) ||
                PyErr_ExceptionMatches(PyExc_AttributeError)) {
                PyErr_Clear();
                file_drop_view(f);
            } else {
                PyGILState_Release(gil);
                s->failed = LAZ_TRUE;
                s->eof = LAZ_TRUE;
                return 0;
            }
        } else {
            n = PyNumber_AsSsize_t(res, PyExc_OverflowError);
            Py_DECREF(res);
            if (n < 0 || n > FILE_BUF_SIZE) {
                if (n >= 0)
                    PyErr_SetString(PyExc_ValueError,
                                    "readinto wrote more than it was given");
                PyGILState_Release(gil);
                s->failed = LAZ_TRUE;
                s->eof = LAZ_TRUE;
                return 0;
            }
            f->fill = (I64)n;
            PyGILState_Release(gil);
            if (n == 0) s->eof = LAZ_TRUE;
            return (I64)n;
        }
    }

    res = PyObject_CallMethod(f->fp, "read", "n", (Py_ssize_t)FILE_BUF_SIZE);
    if (res == NULL) {
        PyGILState_Release(gil);
        s->failed = LAZ_TRUE;
        s->eof = LAZ_TRUE;
        return 0;
    }
    if (PyBytes_AsStringAndSize(res, &data, &n) < 0) {
        Py_DECREF(res);
        PyGILState_Release(gil);
        s->failed = LAZ_TRUE;
        s->eof = LAZ_TRUE;
        return 0;
    }
    if (n > FILE_BUF_SIZE) {
        Py_DECREF(res);
        PyErr_SetString(PyExc_ValueError, "read returned more than it was asked for");
        PyGILState_Release(gil);
        s->failed = LAZ_TRUE;
        s->eof = LAZ_TRUE;
        return 0;
    }
    if (n > 0) {
        memcpy(f->buf, data, (size_t)n);
        f->fill = (I64)n;
    }
    Py_DECREF(res);
    PyGILState_Release(gil);
    if (n == 0) s->eof = LAZ_TRUE;
    return (I64)n;
}

static U32 file_get_byte(LazStream *s)
{
    FileImpl *f = (FileImpl *)s->impl;
    if (f->pos >= f->fill) {
        if (file_refill(s) == 0) return 0;
    }
    return f->buf[f->pos++];
}

static void file_get_bytes(LazStream *s, U8 *bytes, I64 num_bytes)
{
    FileImpl *f = (FileImpl *)s->impl;
    while (num_bytes > 0) {
        I64 avail = f->fill - f->pos;
        if (avail <= 0) {
            if (file_refill(s) == 0) { memset(bytes, 0, (size_t)num_bytes); return; }
            continue;
        }
        if (avail > num_bytes) avail = num_bytes;
        memcpy(bytes, f->buf + f->pos, (size_t)avail);
        f->pos += avail;
        bytes += avail;
        num_bytes -= avail;
    }
}

static I64 file_tell(LazStream *s)
{
    FileImpl *f = (FileImpl *)s->impl;
    return f->base + f->pos;
}

/* Seeks the underlying object and invalidates the buffer. */
static BOOL file_seek_raw(LazStream *s, I64 offset, int whence)
{
    FileImpl *f = (FileImpl *)s->impl;
    PyObject *res;
    I64 newpos;
    PyGILState_STATE gil;

    if (s->failed) return LAZ_FALSE;      /* see file_refill */

    gil = PyGILState_Ensure();

    res = PyObject_CallMethod(f->fp, "seek", "Li", (long long)offset, whence);
    if (res == NULL) { s->failed = LAZ_TRUE; PyGILState_Release(gil); return LAZ_FALSE; }
    Py_DECREF(res);

    res = PyObject_CallMethod(f->fp, "tell", NULL);
    if (res == NULL) { s->failed = LAZ_TRUE; PyGILState_Release(gil); return LAZ_FALSE; }
    newpos = (I64)PyLong_AsLongLong(res);
    Py_DECREF(res);
    if (PyErr_Occurred()) { s->failed = LAZ_TRUE; PyGILState_Release(gil); return LAZ_FALSE; }
    PyGILState_Release(gil);

    f->base = newpos;
    f->pos = 0;
    f->fill = 0;
    s->eof = LAZ_FALSE;
    return LAZ_TRUE;
}

static BOOL file_seek(LazStream *s, I64 position)
{
    FileImpl *f = (FileImpl *)s->impl;
    /* stay inside the buffer when possible */
    if (position >= f->base && position < f->base + f->fill) {
        f->pos = position - f->base;
        return LAZ_TRUE;
    }
    return file_seek_raw(s, position, SEEK_SET);
}

static BOOL file_seek_end(LazStream *s, I64 distance)
{
    return file_seek_raw(s, -distance, SEEK_END);
}

static void file_destroy(LazStream *s)
{
    FileImpl *f = (FileImpl *)s->impl;
    PyGILState_STATE gil = PyGILState_Ensure();
    file_drop_view(f);
    Py_XDECREF(f->fp);
    PyGILState_Release(gil);
    free(f->buf);
    free(f);
}

/* The file offset a freshly wrapped object is already at. An object without a
 * usable tell() is fine: positions are then relative to wherever it happened
 * to be, so the exception is cleared deliberately. */
static I64 file_start_position(PyObject *fp)
{
    PyObject *res;
    I64 position;

    res = PyObject_CallMethod(fp, "tell", NULL);
    if (res == NULL) { PyErr_Clear(); return 0; }
    position = (I64)PyLong_AsLongLong(res);
    Py_DECREF(res);
    if (PyErr_Occurred()) { PyErr_Clear(); return 0; }
    return position;
}

/* Whether seeks on `fp` can be expected to work. An object that answers
 * seekable() is taken at its word -- a pipe has a seek method that raises --
 * and anything else is judged by whether it has one at all. Both directions
 * branch on the answer: the reader falls back to walking chunks in order, the
 * writer to the -1 chunk-table offset. */
static BOOL file_is_seekable(PyObject *fp)
{
    PyObject *res;
    int ok;

    if (!PyObject_HasAttrString(fp, "seek")) return LAZ_FALSE;
    if (!PyObject_HasAttrString(fp, "seekable")) return LAZ_TRUE;

    res = PyObject_CallMethod(fp, "seekable", NULL);
    if (res == NULL) { PyErr_Clear(); return LAZ_FALSE; }
    ok = PyObject_IsTrue(res);
    Py_DECREF(res);
    if (ok < 0) { PyErr_Clear(); return LAZ_FALSE; }
    return ok ? LAZ_TRUE : LAZ_FALSE;
}

LazStream *laz_stream_new_file(void *py_fp)
{
    LazStream *s = (LazStream *)calloc(1, sizeof(LazStream));
    FileImpl *f;

    if (!s) return NULL;
    f = (FileImpl *)calloc(1, sizeof(FileImpl));
    if (!f) { free(s); return NULL; }
    f->buf = (U8 *)malloc(FILE_BUF_SIZE);
    if (!f->buf) { free(f); free(s); return NULL; }

    f->fp = (PyObject *)py_fp;
    Py_INCREF(f->fp);

    f->base = file_start_position(f->fp);
    f->pos = 0;
    f->fill = 0;
    f->length = -2;                       /* not looked for yet */

    /* One view over the buffer, for readinto to fill in place. Made here
     * because making it per refill would cost more than the copy it saves,
     * and only where the file object has a readinto to give it to. */
    if (PyObject_HasAttrString(f->fp, "readinto")) {
        f->view = PyMemoryView_FromMemory((char *)f->buf, FILE_BUF_SIZE,
                                          PyBUF_WRITE);
        if (!f->view) PyErr_Clear();          /* fall back to read() */
    }

    s->impl = f;
    s->get_byte = file_get_byte;
    s->get_bytes = file_get_bytes;
    s->tell = file_tell;
    s->seek = file_seek;
    s->seek_end = file_seek_end;
    s->destroy = file_destroy;
    s->seekable = file_is_seekable(f->fp);
    s->eof = LAZ_FALSE;
    return s;
}

/* -------------------------------------------------------------- file out */

/*
 * Buffered, like its input-side twin and for the same reason: the arithmetic
 * encoder hands over AC_BUFFER_SIZE blocks, but the raw item writers -- which
 * write every point of an uncompressed file and the first point of every
 * compressed chunk -- put one item at a time, a dozen bytes at a go. A Python
 * call per item costs more than the encoding does.
 *
 * `pos` counts bytes handed to put_bytes, buffered or not, so tell() never
 * has to flush and never has to ask the file object.
 */
typedef struct {
    PyObject *fp;
    U8 *buf;
    I64 fill;       /* bytes staged in buf */
    I64 pos;        /* logical write position, ahead of the file object by fill */
} FileOutImpl;

/* The one place this stream calls Python. */
static void fileout_emit(LazOutStream *s, const U8 *bytes, I64 num_bytes)
{
    FileOutImpl *f = (FileOutImpl *)s->impl;
    PyObject *res;
    PyGILState_STATE gil;

    /* Once failed the stream is inert: an exception is pending, so calling
     * back into Python again would be illegal. The encoder keeps writing into
     * the void until the caller notices and propagates. */
    if (s->failed || num_bytes <= 0) return;

    gil = PyGILState_Ensure();
    res = PyObject_CallMethod(f->fp, "write", "y#",
                              (const char *)bytes, (Py_ssize_t)num_bytes);
    if (res == NULL) {
        s->failed = LAZ_TRUE;         /* exception left set for the binding */
    } else {
        Py_DECREF(res);
    }
    PyGILState_Release(gil);
}

static void fileout_flush(LazOutStream *s)
{
    FileOutImpl *f = (FileOutImpl *)s->impl;
    if (f->fill == 0) return;
    fileout_emit(s, f->buf, f->fill);
    f->fill = 0;                      /* dropped either way; see fileout_emit */
}

static void fileout_put_bytes(LazOutStream *s, const U8 *bytes, I64 num_bytes)
{
    FileOutImpl *f = (FileOutImpl *)s->impl;

    if (s->failed || num_bytes <= 0) return;

    if (f->fill + num_bytes > FILE_BUF_SIZE) fileout_flush(s);
    if (num_bytes > FILE_BUF_SIZE) {
        fileout_emit(s, bytes, num_bytes);    /* too big to stage; straight out */
    } else {
        memcpy(f->buf + f->fill, bytes, (size_t)num_bytes);
        f->fill += num_bytes;
    }
    f->pos += num_bytes;
}

static I64 fileout_tell(LazOutStream *s) { return ((FileOutImpl *)s->impl)->pos; }

static BOOL fileout_seek(LazOutStream *s, I64 position)
{
    FileOutImpl *f = (FileOutImpl *)s->impl;
    PyObject *res;
    PyGILState_STATE gil;

    if (s->failed || !s->seekable) return LAZ_FALSE;   /* see fileout_emit */
    if (position == f->pos) return LAZ_TRUE;

    /* the buffer belongs where it was written, not where we are going */
    fileout_flush(s);
    if (s->failed) return LAZ_FALSE;

    gil = PyGILState_Ensure();
    res = PyObject_CallMethod(f->fp, "seek", "L", (long long)position);
    if (res == NULL) { s->failed = LAZ_TRUE; PyGILState_Release(gil); return LAZ_FALSE; }
    Py_DECREF(res);
    PyGILState_Release(gil);

    f->pos = position;
    return LAZ_TRUE;
}

static void fileout_destroy(LazOutStream *s)
{
    FileOutImpl *f = (FileOutImpl *)s->impl;
    PyGILState_STATE gil = PyGILState_Ensure();

    /* A last-resort flush, for a stream dropped without being closed. There is
     * nobody left to report a failure to, and an exception left pending here
     * would surface in whatever ran next -- including the failure path that is
     * already unwinding with one of its own -- so it is discarded. */
    if (!s->failed) {
        PyObject *type, *value, *traceback;
        PyErr_Fetch(&type, &value, &traceback);
        fileout_flush(s);
        if (s->failed) PyErr_Clear();
        PyErr_Restore(type, value, traceback);
    }

    Py_XDECREF(f->fp);
    PyGILState_Release(gil);
    free(f->buf);
    free(f);
}

LazOutStream *laz_outstream_new_file(void *py_fp)
{
    LazOutStream *s = (LazOutStream *)calloc(1, sizeof(LazOutStream));
    FileOutImpl *f;

    if (!s) return NULL;
    f = (FileOutImpl *)calloc(1, sizeof(FileOutImpl));
    if (!f) { free(s); return NULL; }
    f->buf = (U8 *)malloc(FILE_BUF_SIZE);
    if (!f->buf) { free(f); free(s); return NULL; }

    f->fp = (PyObject *)py_fp;
    Py_INCREF(f->fp);
    f->pos = file_start_position(f->fp);
    f->fill = 0;

    s->impl = f;
    s->put_bytes = fileout_put_bytes;
    s->flush = fileout_flush;
    s->tell = fileout_tell;
    s->seek = fileout_seek;
    s->destroy = fileout_destroy;
    s->seekable = file_is_seekable(f->fp);
    s->failed = LAZ_FALSE;
    return s;
}

void laz_outstream_file_set_position(LazOutStream *s, I64 position)
{
    ((FileOutImpl *)s->impl)->pos = position;
}

/* ------------------------------------------------------------- array out */

typedef struct {
    U8 *data;
    I64 size;       /* bytes written; the high-water mark of pos */
    I64 pos;        /* write cursor, which a seek can move back into `data` */
    I64 capacity;
} ArrayOutImpl;

static void arrayout_put_bytes(LazOutStream *s, const U8 *bytes, I64 num_bytes)
{
    ArrayOutImpl *a = (ArrayOutImpl *)s->impl;

    if (s->failed || num_bytes <= 0) return;

    if (a->pos + num_bytes > a->capacity) {
        I64 want = a->capacity ? a->capacity : 1024;
        U8 *grown;
        while (want < a->pos + num_bytes) want *= 2;
        grown = (U8 *)realloc(a->data, (size_t)want);
        if (!grown) { s->failed = LAZ_TRUE; return; }
        a->data = grown;
        a->capacity = want;
    }
    memcpy(a->data + a->pos, bytes, (size_t)num_bytes);
    a->pos += num_bytes;
    if (a->pos > a->size) a->size = a->pos;
}

static I64 arrayout_tell(LazOutStream *s) { return ((ArrayOutImpl *)s->impl)->pos; }

/*
 * Only backwards over what is already there: an array sink is a buffer, not a
 * file, so there is no seeking past the end and no hole to fill.
 *
 * Nothing seeks an array sink today -- the layered writers only append and
 * rewind. It exists so that pointing a chunked writer at one works, rather
 * than finding a NULL where the vtable promised a method.
 */
static BOOL arrayout_seek(LazOutStream *s, I64 position)
{
    ArrayOutImpl *a = (ArrayOutImpl *)s->impl;
    if (position < 0 || position > a->size) return LAZ_FALSE;
    a->pos = position;
    return LAZ_TRUE;
}

static void arrayout_destroy(LazOutStream *s)
{
    ArrayOutImpl *a = (ArrayOutImpl *)s->impl;
    free(a->data);
    free(a);
}

LazOutStream *laz_outstream_new_array(void)
{
    LazOutStream *s = (LazOutStream *)calloc(1, sizeof(LazOutStream));
    ArrayOutImpl *a;

    if (!s) return NULL;
    a = (ArrayOutImpl *)calloc(1, sizeof(ArrayOutImpl));
    if (!a) { free(s); return NULL; }

    s->impl = a;
    s->put_bytes = arrayout_put_bytes;
    s->tell = arrayout_tell;
    s->seek = arrayout_seek;
    s->destroy = arrayout_destroy;
    s->seekable = LAZ_TRUE;
    s->failed = LAZ_FALSE;
    return s;
}

const U8 *laz_outstream_array_data(LazOutStream *s, I64 *size)
{
    ArrayOutImpl *a = (ArrayOutImpl *)s->impl;
    *size = a->size;
    return a->data;
}

void laz_outstream_array_rewind(LazOutStream *s)
{
    ArrayOutImpl *a = (ArrayOutImpl *)s->impl;
    a->size = 0;
    a->pos = 0;
}

/* ----------------------------------------------------------------- shared */

void laz_outstream_destroy(LazOutStream *s)
{
    if (!s) return;
    if (s->destroy) s->destroy(s);
    free(s);
}

void laz_stream_destroy(LazStream *s)
{
    if (!s) return;
    if (s->destroy) s->destroy(s);
    free(s);
}

/* The chunk table and the layer byte counts are little-endian on disk like
 * everything else, so these go through the shared accessors in laz_types.h
 * rather than spelling the packing out a second time. */
U32 laz_stream_get32(LazStream *s)
{
    U8 b[4];
    s->get_bytes(s, b, 4);
    return laz_le_get32(b);
}

U64 laz_stream_get64(LazStream *s)
{
    U8 b[8];
    s->get_bytes(s, b, 8);
    return laz_le_get64(b);
}

/* The whole length of a file stream, found once and remembered. Costs a seek
 * to the end and back, so it is worth not doing twice. */
static I64 file_length(LazStream *s)
{
    FileImpl *f = (FileImpl *)s->impl;
    I64 here;

    if (f->length != -2) return f->length;
    f->length = -1;                       /* unless the seeks below work */
    if (!s->seekable || s->failed) return f->length;

    here = file_tell(s);
    if (laz_stream_seek_end(s, 0)) {
        I64 end = file_tell(s);
        if (laz_stream_seek(s, here) && end >= 0) f->length = end;
    }
    return f->length;
}

I64 laz_stream_remaining(LazStream *s)
{
    I64 length, here;

    /* An array stream measures itself for free through the same three vtable
     * calls; only a file stream is worth remembering the answer for, which is
     * what file_length does. */
    if (s->seek != file_seek) {
        here = laz_stream_tell(s);
        if (!laz_stream_seek_end(s, 0)) return -1;
        length = laz_stream_tell(s);
        if (!laz_stream_seek(s, here)) return -1;
    } else {
        length = file_length(s);
        if (length < 0) return -1;
        here = laz_stream_tell(s);
    }
    return here > length ? 0 : length - here;
}

void laz_outstream_put32(LazOutStream *s, U32 value)
{
    U8 b[4];
    laz_le_put32(b, value);
    s->put_bytes(s, b, 4);
}

void laz_outstream_put64(LazOutStream *s, U64 value)
{
    U8 b[8];
    laz_le_put64(b, value);
    s->put_bytes(s, b, 8);
}
