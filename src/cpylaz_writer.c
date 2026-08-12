/* PointWriter: the compress side of the container. */
#include "cpylaz.h"

/* =========================================================== PointWriter == */

/*
 * The compress side of PointReader: points in, a LAZ point block out.
 *
 * Points arrive as records -- the bytes as they sit in an uncompressed file --
 * rather than as Point objects, because a record is what a caller converting a
 * file already has and it is the layout the items describe. Each record is
 * scattered into a LazPoint before any writer sees it, because an item coder
 * is handed a pointer into the point rather than into the record, and for the
 * LAS 1.4 point types the two are not the same: POINT14's 30-byte record
 * splits across both the legacy and the extended fields of a point.
 *
 * That scatter is not written here: taking apart uncompressed records is what
 * a LazReadPoint over an uncompressed stream does, so the writer keeps one,
 * pointed at each record in turn. It also settles the extra-bytes buffer and
 * the extended_point_type stamp, which are its business either way.
 *
 * Writing starts wherever the file object already is, which is what makes the
 * chunk table hold absolute file positions: the caller writes the LAS header
 * first and hands over a file positioned behind it. An object that cannot
 * answer tell() -- a pipe -- has to be told where that is, with start_offset.
 */

typedef struct {
    PyObject_HEAD
    LazWritePoint wp;
    LazOutStream *stream;
    PyObject *fp;
    /* the record being written, and the reader that takes it apart */
    LazStream *record;
    LazReadPoint scatter;
    Py_ssize_t record_size;
    LazPoint point;
    U8 *extra_bytes;
    BOOL ready;             /* clear before init finishes and after done() */
    U64 index;              /* number of points written so far */
    /* what a LAS header cannot be finished without, tallied here because this
     * is what sees every point: bounds are unscaled, and the return counts are
     * indexed by return number, so entry 0 is the invalid one */
    I32 min_xyz[3], max_xyz[3];
    U64 by_return[16];
    /* LAS 1.4 compatibility mode: where the hidden fields go in the extra
     * bytes, with -1 for COMPAT_NIR when there is no NIR band */
    BOOL compat;
    I32 compat_starts[COMPAT_ATTRIBUTES];
    const I16 *scan_angle_of_rank;      /* what a rank stands for, tabulated */
} WriterObject;

/*
 * Write a LAS 1.4 point as a legacy one.
 *
 * The exact inverse of reader_recode_compat: what the legacy record cannot
 * express is put in the extra bytes, and what it can hold is narrowed to fit
 * -- the scan angle to a rank, the return numbers to three bits, the
 * classification to five. Each narrowing leaves a remainder behind, and it is
 * the remainder that goes in the extra bytes, so that adding the two back
 * together is all reading one takes.
 *
 * This is laszip_write_point()'s recoding step in laszip_dll.cpp. The three
 * classification flags are not touched: they are already in the legacy field
 * the record is written from, which is where a Writer takes them from for a
 * native LAS 1.4 file too.
 */
static void writer_recode_compat(WriterObject *self)
{
    LazPoint *p = &self->point;
    U8 *extra = self->extra_bytes;
    const I32 *at = self->compat_starts;
    I8 rank = I8_CLAMP(COMPAT_RANK_OF_SCAN_ANGLE(p->extended_scan_angle));
    U8 extended_returns = laz_point_extended_return_number(p);
    U8 extended_number = laz_point_extended_number_of_returns(p);
    U8 return_number, number_of_returns, classification;

    p->scan_angle_rank = rank;
    /* the extra bytes are on-disk data, so little-endian whatever the host */
    laz_le_put16(extra + at[COMPAT_SCAN_ANGLE],
                 (U16)(I16)(p->extended_scan_angle
                            - self->scan_angle_of_rank[(U8)rank]));

    /*
     * Above seven returns the legacy pair cannot say which return this is, so
     * laszip says the count is seven and keeps the return number as near the
     * end of the sweep as three bits allow: the last four returns keep their
     * distance from the end, and everything before them is a fourth return.
     */
    if (extended_number <= 7) {
        return_number = extended_returns;
        number_of_returns = extended_number;
    } else {
        number_of_returns = 7;
        if (extended_returns <= 4)
            return_number = extended_returns;
        else if (extended_returns >= extended_number - 3)
            return_number = (U8)(extended_returns - (extended_number - 7));
        else
            return_number = 4;
    }
    laz_point_set_return_number(p, return_number);
    laz_point_set_number_of_returns(p, number_of_returns);
    extra[at[COMPAT_EXTENDED_RETURNS]] =
        (U8)(((extended_returns - return_number) << 4)
             | (extended_number - number_of_returns));

    /* five bits hold classifications up to 31; past that the legacy field
     * says nothing and the whole value travels in the extra bytes */
    classification = p->extended_classification;
    laz_point_set_classification(p, classification <= 31 ? classification : 0);
    extra[at[COMPAT_CLASSIFICATION]] =
        (U8)(classification - laz_point_classification(p));

    /* of the four classification flags only overlap is new in LAS 1.4; the
     * other three are already in the legacy field */
    extra[at[COMPAT_FLAGS_AND_CHANNEL]] =
        (U8)(((laz_point_extended_classification_flags(p) >> 3) & 0x01)
             | (laz_point_extended_scanner_channel(p) << 1));

    if (at[COMPAT_NIR] >= 0)
        laz_le_put16(extra + at[COMPAT_NIR], p->rgb[3]);
}

static void Writer_dealloc(WriterObject *self)
{
    laz_readpoint_destroy(&self->scatter);
    laz_writepoint_destroy(&self->wp);
    if (self->record) laz_stream_destroy(self->record);
    if (self->stream) laz_outstream_destroy(self->stream);
    Py_XDECREF(self->fp);
    free(self->extra_bytes);
    PyObject_Del(self);
}

static int Writer_tp_init(WriterObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *fp, *items_obj, *compatibility = NULL;
    unsigned int compressor, coder = 0, chunk_size = 0;
    long long start_offset = -1;
    LazItem *items = NULL;
    U32 num_items = 0;
    int failed, compat;
    static char *kwlist[] = {"fp", "items", "compressor", "coder", "chunk_size",
                             "start_offset", "compatibility", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOI|IILO", kwlist,
                                     &fp, &items_obj, &compressor, &coder,
                                     &chunk_size, &start_offset,
                                     &compatibility))
        return -1;

    if (parse_items(items_obj, &items, &num_items) < 0) return -1;

    laz_writepoint_init_struct(&self->wp);
    laz_readpoint_init_struct(&self->scatter, LAZ_DECOMPRESS_SELECTIVE_ALL);
    /* the same items, read back uncompressed: that is what a record is */
    failed = (!laz_writepoint_setup(&self->wp, num_items, items, compressor,
                                    coder, chunk_size) ||
              !laz_readpoint_setup(&self->scatter, num_items, items, 0, 0, 0));
    PyMem_Free(items);
    if (failed) {
        PyErr_SetString(LazErrorType, self->wp.has_error ? self->wp.last_error
                                                         : self->scatter.last_error);
        return -1;
    }

    compat = parse_compat_starts(compatibility, self->wp.num_extra_bytes,
                                 self->compat_starts);
    if (compat < 0) return -1;
    self->compat = (BOOL)compat;
    if (self->compat) self->scan_angle_of_rank = compat_scan_angle_of_rank();

    self->record_size = (Py_ssize_t)self->wp.point_size;
    if (self->wp.num_extra_bytes) {
        self->extra_bytes = (U8 *)calloc(self->wp.num_extra_bytes, 1);
        if (!self->extra_bytes) { PyErr_NoMemory(); return -1; }
    }

    /* one array stream, repointed at each record as it arrives */
    self->record = laz_stream_new_array(NULL, 0);
    if (!self->record) { PyErr_NoMemory(); return -1; }
    laz_readpoint_init(&self->scatter, self->record);
    laz_readpoint_init_point(&self->scatter, &self->point);

    self->stream = laz_outstream_new_file(fp);
    if (!self->stream) { PyErr_NoMemory(); return -1; }
    Py_INCREF(fp);
    self->fp = fp;
    if (start_offset >= 0)
        laz_outstream_file_set_position(self->stream, (I64)start_offset);

    if (!laz_writepoint_init(&self->wp, self->stream)) {
        PyErr_SetString(LazErrorType, "could not initialise the point writer");
        return -1;
    }
    if (self->stream->failed) {
        /* the chunk-table placeholder could not be written; whatever the file
         * object raised is the better message */
        if (!PyErr_Occurred())
            PyErr_SetString(LazErrorType, "error writing to the underlying file");
        return -1;
    }

    self->ready = LAZ_TRUE;
    self->index = 0;
    return 0;
}

/* As reader_error: the file object's own exception first, then the core's. */
static PyObject *writer_error(WriterObject *self)
{
    if (PyErr_Occurred()) return NULL;            /* propagate the original */
    if (self->stream && self->stream->failed) {
        PyErr_SetString(LazErrorType, "error writing to the underlying file");
        return NULL;
    }
    PyErr_SetString(LazErrorType,
                    self->wp.has_error ? self->wp.last_error : "write failed");
    return NULL;
}

/* Fills self->point from a record, as the matching reader would. */
static int writer_take_record(WriterObject *self, PyObject *arg)
{
    char *data;
    Py_ssize_t len;

    if (PyBytes_AsStringAndSize(arg, &data, &len) < 0) return -1;
    if (len != self->record_size) {
        PyErr_Format(PyExc_ValueError, "point %llu is %zd bytes, expected %zd",
                     (unsigned long long)self->index, len, self->record_size);
        return -1;
    }

    laz_stream_array_reset(self->record, (const U8 *)data, len);
    if (!laz_readpoint_read(&self->scatter, &self->point, self->extra_bytes)) {
        if (!PyErr_Occurred())
            PyErr_SetString(LazErrorType, self->scatter.last_error);
        return -1;
    }
    return 0;
}

/*
 * Fills self->point from another Point, by copying rather than by writing
 * through the caller's: the LAS 1.4 flag below is stamped on whatever is about
 * to be written, and a Point handed in from a reader is not ours to mark.
 *
 * A point carrying fewer extra bytes than the layout has is padded with zeros,
 * which is what an unset field is everywhere else; more than the layout has is
 * an error, because there is nowhere for them to go.
 */
static int writer_take_point(WriterObject *self, PointObject *point)
{
    U32 want = self->wp.num_extra_bytes;

    if (point->num_extra > want) {
        PyErr_Format(PyExc_ValueError,
                     "point carries %u extra bytes, but the layout has room "
                     "for %u", point->num_extra, want);
        return -1;
    }

    self->point = *point->p;
    /* the copy brought the other point's extra-bytes buffer with it */
    self->point.num_extra_bytes = (I32)want;
    self->point.extra_bytes = self->extra_bytes;
    if (want) {
        memcpy(self->extra_bytes, point->extra, point->num_extra);
        memset(self->extra_bytes + point->num_extra, 0, want - point->num_extra);
    }

    /* point formats 6-10 are marked and everything else is not: a point built
     * by hand has never been through a reader, and one copied from another
     * file may have been through the wrong sort */
    laz_writepoint_init_point(&self->wp, &self->point);
    return 0;
}

/* The running header fields. Bounds start at the first point rather than at
 * the extremes of I32, so a one-point file has bounds equal to that point. */
static void writer_tally(WriterObject *self)
{
    const I32 xyz[3] = {self->point.X, self->point.Y, self->point.Z};
    U32 return_number;
    int i;

    if (self->index == 0) {
        for (i = 0; i < 3; i++) self->min_xyz[i] = self->max_xyz[i] = xyz[i];
    } else {
        for (i = 0; i < 3; i++) {
            if (xyz[i] < self->min_xyz[i]) self->min_xyz[i] = xyz[i];
            if (xyz[i] > self->max_xyz[i]) self->max_xyz[i] = xyz[i];
        }
    }

    /* In compatibility mode the point about to be written is a legacy one,
     * but the counts a LAS 1.4 header states are of LAS 1.4 return numbers --
     * which is why this runs before the recoding narrows them. */
    return_number = (self->compat
                     || laz_point_extended_point_type(&self->point))
                    ? laz_point_extended_return_number(&self->point)
                    : laz_point_return_number(&self->point);
    self->by_return[return_number & 0xF]++;
}

static PyObject *Writer_write(WriterObject *self, PyObject *arg)
{
    int taken;

    if (!self->ready) {
        PyErr_SetString(PyExc_ValueError, "writer is closed");
        return NULL;
    }

    if (PyObject_TypeCheck(arg, &Point_Type)) {
        taken = writer_take_point(self, (PointObject *)arg);
    } else if (self->compat) {
        PyErr_SetString(PyExc_ValueError,
                        "compatibility mode takes points, not records: a "
                        "record here would already be the legacy one it is "
                        "about to build");
        return NULL;
    } else {
        taken = writer_take_record(self, arg);
    }
    if (taken < 0) return NULL;

    /* before the recoding, which is what makes the tally the LAS 1.4 one */
    writer_tally(self);
    if (self->compat) writer_recode_compat(self);

    if (!laz_writepoint_write(&self->wp, &self->point, self->extra_bytes))
        return writer_error(self);
    self->index++;
    Py_RETURN_NONE;
}

/* ------------------------------------------------------- writing in bulk - */

/*
 * The mirror of PointReader.read_into, and the same triples: a buffer, the
 * offset into the decoded point its bytes belong at, and how many per point.
 * An offset of -1 means the extra bytes instead.
 *
 * What it is for is the same thing in the other direction. read_into exists
 * so a whole file can be decoded without a Python object per point; without
 * this, writing one back put the object -- and a call, and a type check --
 * back in front of every point, so a conversion ran at the speed of the half
 * that had no bulk path.
 */
typedef struct {
    const U8 *src;          /* walks the caller's column */
    U8 *dst;                /* fixed: where in the point it lands */
    Py_ssize_t size;
} Source;

typedef struct {
    PyObject *seq;
    Source *cols;
    Py_buffer *views;
    Py_ssize_t n, held;
} Sources;

static void sources_close(Sources *s)
{
    Py_ssize_t i;
    for (i = 0; i < s->held; i++) PyBuffer_Release(&s->views[i]);
    PyMem_Free(s->views);
    PyMem_Free(s->cols);
    Py_XDECREF(s->seq);
}

static BOOL sources_open(WriterObject *self, PyObject *targets,
                         Py_ssize_t count, Sources *s)
{
    Py_ssize_t i;

    s->seq = NULL; s->cols = NULL; s->views = NULL; s->n = 0; s->held = 0;

    s->seq = PySequence_Fast(targets, "targets must be a sequence");
    if (!s->seq) return LAZ_FALSE;
    s->n = PySequence_Fast_GET_SIZE(s->seq);

    s->cols = (Source *)PyMem_Malloc((size_t)s->n * sizeof(Source));
    s->views = (Py_buffer *)PyMem_Malloc((size_t)s->n * sizeof(Py_buffer));
    if (!s->cols || !s->views) { PyErr_NoMemory(); return LAZ_FALSE; }

    for (i = 0; i < s->n; i++) {
        PyObject *t = PySequence_Fast_GET_ITEM(s->seq, i);
        PyObject *buf;
        Py_ssize_t offset, size;

        if (!PyArg_ParseTuple(t, "Onn", &buf, &offset, &size))
            return LAZ_FALSE;
        if (size <= 0) {
            PyErr_SetString(PyExc_ValueError, "field width must be positive");
            return LAZ_FALSE;
        }
        if (count > PY_SSIZE_T_MAX / size) {
            PyErr_SetString(PyExc_OverflowError, "count is too large");
            return LAZ_FALSE;
        }
        /* bounded by subtraction, as columns_open does it: offset and size
         * are the caller's and adding them could overflow */
        if (offset == -1) {
            if (size > (Py_ssize_t)self->wp.num_extra_bytes) {
                PyErr_SetString(PyExc_ValueError,
                                "field lies outside the extra bytes");
                return LAZ_FALSE;
            }
            s->cols[i].dst = self->extra_bytes;
        } else {
            if (offset < 0 || offset > POINT_FIXED_EXTENT - size) {
                PyErr_SetString(PyExc_ValueError,
                                "field lies outside the decoded point");
                return LAZ_FALSE;
            }
            s->cols[i].dst = (U8 *)&self->point + offset;
        }

        if (PyObject_GetBuffer(buf, &s->views[i], PyBUF_C_CONTIGUOUS) < 0)
            return LAZ_FALSE;
        s->held = i + 1;
        if (s->views[i].len < count * size) {
            PyErr_SetString(PyExc_ValueError,
                            "a column is shorter than the point count");
            return LAZ_FALSE;
        }
        s->cols[i].src = (const U8 *)s->views[i].buf;
        s->cols[i].size = size;
    }
    return LAZ_TRUE;
}

/* One point's worth out of every column, into the point. */
static void sources_take(Sources *s)
{
    Py_ssize_t i;
    for (i = 0; i < s->n; i++) {
        memcpy(s->cols[i].dst, s->cols[i].src, (size_t)s->cols[i].size);
        s->cols[i].src += s->cols[i].size;
    }
}

static PyObject *Writer_write_from(WriterObject *self, PyObject *args)
{
    PyObject *targets, *result = NULL;
    Py_ssize_t count, done = 0;
    Sources s;
    BOOL ok = LAZ_TRUE;

    if (!PyArg_ParseTuple(args, "On", &targets, &count)) return NULL;
    if (!self->ready) {
        PyErr_SetString(PyExc_ValueError, "writer is closed");
        return NULL;
    }
    if (count < 0) {
        PyErr_SetString(PyExc_ValueError, "count must not be negative");
        return NULL;
    }
    if (!sources_open(self, targets, count, &s)) { sources_close(&s); return NULL; }

    /* Everything a column does not fill keeps whatever the last point left,
     * so the point is cleared once and the extra bytes with it -- a caller
     * naming three fields means the rest are zero, not the previous point's. */
    memset(&self->point, 0, sizeof(self->point));
    self->point.num_extra_bytes = (I32)self->wp.num_extra_bytes;
    self->point.extra_bytes = self->extra_bytes;
    if (self->wp.num_extra_bytes)
        memset(self->extra_bytes, 0, self->wp.num_extra_bytes);

    for (done = 0; done < count; done++) {
        sources_take(&s);
        laz_writepoint_init_point(&self->wp, &self->point);
        writer_tally(self);
        if (self->compat) writer_recode_compat(self);
        if (!laz_writepoint_write(&self->wp, &self->point, self->extra_bytes)) {
            ok = LAZ_FALSE;
            break;
        }
        /* per point, not once at the end: the tally reads it to know whether
         * this is the first point, which is what starts the bounding box at
         * a real coordinate rather than at the extremes of I32 */
        self->index++;
    }

    result = ok ? (Py_INCREF(Py_None), Py_None) : writer_error(self);

    sources_close(&s);
    return result;
}

static PyObject *Writer_chunk(WriterObject *self, PyObject *Py_UNUSED(i))
{
    if (!self->ready) {
        PyErr_SetString(PyExc_ValueError, "writer is closed");
        return NULL;
    }
    if (!laz_writepoint_chunk(&self->wp)) return writer_error(self);
    Py_RETURN_NONE;
}

static PyObject *Writer_done(WriterObject *self, PyObject *Py_UNUSED(i))
{
    if (!self->ready) {
        PyErr_SetString(PyExc_ValueError, "writer is closed");
        return NULL;
    }
    /* whatever happens the writer is spent: a second chunk table would be
     * appended to a file that already has one */
    self->ready = LAZ_FALSE;
    if (!laz_writepoint_done(&self->wp)) return writer_error(self);
    Py_RETURN_NONE;
}

static PyObject *Writer_get_index(WriterObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLongLong(self->index); }

static PyObject *Writer_get_number_chunks(WriterObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->wp.number_chunks); }

static PyObject *Writer_get_bounds(WriterObject *self, void *c)
{
    (void)c;
    if (self->index == 0) Py_RETURN_NONE;         /* nothing to bound */
    return Py_BuildValue("(iiiiii)",
                         self->min_xyz[0], self->min_xyz[1], self->min_xyz[2],
                         self->max_xyz[0], self->max_xyz[1], self->max_xyz[2]);
}

static PyObject *Writer_get_points_by_return(WriterObject *self, void *c)
{
    PyObject *t;
    int i;

    (void)c;
    t = PyTuple_New(16);
    if (!t) return NULL;
    for (i = 0; i < 16; i++) {
        PyObject *v = PyLong_FromUnsignedLongLong(self->by_return[i]);
        if (!v) { Py_DECREF(t); return NULL; }
        PyTuple_SET_ITEM(t, i, v);
    }
    return t;
}

static PyMethodDef Writer_methods[] = {
    {"write", (PyCFunction)Writer_write, METH_O,
     "write(point) -> None\n\n"
     "Append one point: a Point, or the bytes of one on-disk record."},
    {"write_from", (PyCFunction)Writer_write_from, METH_VARARGS,
     "write_from(targets, count) -> None\n\n"
     "Append count points straight out of buffers, one field per target.\n"
     "The mirror of PointReader.read_into, and the same (buffer, offset,\n"
     "size) triples; an offset of -1 means the extra bytes. Fields no\n"
     "target names are written as zero."},
    {"chunk", (PyCFunction)Writer_chunk, METH_NOARGS,
     "chunk() -> None\n\n"
     "Close the open chunk. Only meaningful with variable-size chunking, "
     "where the boundaries are the caller's to choose."},
    {"done", (PyCFunction)Writer_done, METH_NOARGS,
     "done() -> None\n\n"
     "Close the last chunk and write the chunk table. Not optional: "
     "without it the file ends mid-chunk and nothing can seek in it."},
    {NULL}
};

static PyGetSetDef Writer_getset[] = {
    {"index", (getter)Writer_get_index, NULL,
     "number of points written so far", NULL},
    {"number_chunks", (getter)Writer_get_number_chunks, NULL,
     "number of chunks closed so far", NULL},
    {"bounds", (getter)Writer_get_bounds, NULL,
     "(min_x, min_y, min_z, max_x, max_y, max_z), unscaled, or None", NULL},
    {"points_by_return", (getter)Writer_get_points_by_return, NULL,
     "how many points carried each return number, 0 through 15", NULL},
    {NULL}
};

PyDoc_STRVAR(writer_doc,
"PointWriter(fp, items, compressor, coder=0, chunk_size=0, start_offset=-1,\n"
"            compatibility=None)\n"
"\n"
"The compress side of PointReader: points in, a LAZ point block out.\n"
"\n"
"The arguments are PointReader's. As there, this is the container alone --\n"
"it writes no header and no LASzip VLR, only the points and the chunk\n"
"table behind them, so what it produces has to be described by a header\n"
"something else wrote. lazpy.Writer is that something else.\n"
"\n"
"`compatibility` is PointReader's argument put to the opposite use: given\n"
"the five attribute starts, a LAS 1.4 point handed to write() is written as\n"
"the legacy point the items describe, with what that cannot express folded\n"
"into the extra bytes at those starts. Points only there -- a record would\n"
"already be the legacy one this is about to build.\n"
"\n"
"write() takes a Point or the bytes of one on-disk record; call done()\n"
"once every point is written.\n");

PyTypeObject Writer_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.PointWriter",
    .tp_basicsize = sizeof(WriterObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = writer_doc,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Writer_tp_init,
    .tp_dealloc = (destructor)Writer_dealloc,
    .tp_methods = Writer_methods,
    .tp_getset = Writer_getset,
};
