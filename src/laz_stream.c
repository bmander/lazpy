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
 */
#define FILE_BUF_SIZE 65536

typedef struct {
    PyObject *fp;
    U8 *buf;
    I64 base;       /* file offset corresponding to buf[0] */
    I64 fill;       /* valid bytes in buf */
    I64 pos;        /* read cursor within buf */
} FileImpl;

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
    Py_XDECREF(f->fp);
    PyGILState_Release(gil);
    free(f->buf);
    free(f);
}

LazStream *laz_stream_new_file(void *py_fp)
{
    LazStream *s = (LazStream *)calloc(1, sizeof(LazStream));
    FileImpl *f;
    PyObject *res;

    if (!s) return NULL;
    f = (FileImpl *)calloc(1, sizeof(FileImpl));
    if (!f) { free(s); return NULL; }
    f->buf = (U8 *)malloc(FILE_BUF_SIZE);
    if (!f->buf) { free(f); free(s); return NULL; }

    f->fp = (PyObject *)py_fp;
    Py_INCREF(f->fp);

    /* A file object without a usable tell() is fine; positions are then
     * relative to wherever it happened to be. Cleared deliberately. */
    res = PyObject_CallMethod(f->fp, "tell", NULL);
    if (res == NULL) {
        PyErr_Clear();
        f->base = 0;
    } else {
        f->base = (I64)PyLong_AsLongLong(res);
        Py_DECREF(res);
        if (PyErr_Occurred()) { PyErr_Clear(); f->base = 0; }
    }
    f->pos = 0;
    f->fill = 0;

    s->impl = f;
    s->get_byte = file_get_byte;
    s->get_bytes = file_get_bytes;
    s->tell = file_tell;
    s->seek = file_seek;
    s->seek_end = file_seek_end;
    s->destroy = file_destroy;
    s->seekable = PyObject_HasAttrString(f->fp, "seek") ? LAZ_TRUE : LAZ_FALSE;
    s->eof = LAZ_FALSE;
    return s;
}

/* -------------------------------------------------------------- file out */

typedef struct {
    PyObject *fp;
} FileOutImpl;

static void fileout_put_bytes(LazOutStream *s, const U8 *bytes, I64 num_bytes)
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

static void fileout_destroy(LazOutStream *s)
{
    FileOutImpl *f = (FileOutImpl *)s->impl;
    PyGILState_STATE gil = PyGILState_Ensure();
    Py_XDECREF(f->fp);
    PyGILState_Release(gil);
    free(f);
}

LazOutStream *laz_outstream_new_file(void *py_fp)
{
    LazOutStream *s = (LazOutStream *)calloc(1, sizeof(LazOutStream));
    FileOutImpl *f;

    if (!s) return NULL;
    f = (FileOutImpl *)calloc(1, sizeof(FileOutImpl));
    if (!f) { free(s); return NULL; }

    f->fp = (PyObject *)py_fp;
    Py_INCREF(f->fp);

    s->impl = f;
    s->put_bytes = fileout_put_bytes;
    s->destroy = fileout_destroy;
    s->failed = LAZ_FALSE;
    return s;
}

/* ------------------------------------------------------------- array out */

typedef struct {
    U8 *data;
    I64 size;
    I64 capacity;
} ArrayOutImpl;

static void arrayout_put_bytes(LazOutStream *s, const U8 *bytes, I64 num_bytes)
{
    ArrayOutImpl *a = (ArrayOutImpl *)s->impl;

    if (s->failed || num_bytes <= 0) return;

    if (a->size + num_bytes > a->capacity) {
        I64 want = a->capacity ? a->capacity : 1024;
        U8 *grown;
        while (want < a->size + num_bytes) want *= 2;
        grown = (U8 *)realloc(a->data, (size_t)want);
        if (!grown) { s->failed = LAZ_TRUE; return; }
        a->data = grown;
        a->capacity = want;
    }
    memcpy(a->data + a->size, bytes, (size_t)num_bytes);
    a->size += num_bytes;
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
    s->destroy = arrayout_destroy;
    s->failed = LAZ_FALSE;
    return s;
}

const U8 *laz_outstream_array_data(LazOutStream *s, I64 *size)
{
    ArrayOutImpl *a = (ArrayOutImpl *)s->impl;
    *size = a->size;
    return a->data;
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

U32 laz_stream_get32(LazStream *s)
{
    U8 b[4];
    s->get_bytes(s, b, 4);
    return (U32)b[0] | ((U32)b[1] << 8) | ((U32)b[2] << 16) | ((U32)b[3] << 24);
}

U64 laz_stream_get64(LazStream *s)
{
    U64 lo = laz_stream_get32(s);
    U64 hi = laz_stream_get32(s);
    return lo | (hi << 32);
}
