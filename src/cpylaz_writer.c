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
} WriterObject;

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
    PyObject *fp, *items_obj;
    unsigned int compressor, coder = 0, chunk_size = 0;
    long long start_offset = -1;
    LazItem *items = NULL;
    U32 num_items = 0;
    int failed;
    static char *kwlist[] = {"fp", "items", "compressor", "coder", "chunk_size",
                             "start_offset", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOI|IIL", kwlist,
                                     &fp, &items_obj, &compressor, &coder,
                                     &chunk_size, &start_offset))
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

    return_number = laz_point_extended_point_type(&self->point)
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

    taken = PyObject_TypeCheck(arg, &Point_Type)
            ? writer_take_point(self, (PointObject *)arg)
            : writer_take_record(self, arg);
    if (taken < 0) return NULL;

    if (!laz_writepoint_write(&self->wp, &self->point, self->extra_bytes))
        return writer_error(self);
    writer_tally(self);
    self->index++;
    Py_RETURN_NONE;
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
"PointWriter(fp, items, compressor, coder=0, chunk_size=0, start_offset=-1)\n"
"\n"
"The compress side of PointReader: points in, a LAZ point block out.\n"
"\n"
"The arguments are PointReader's. As there, this is the container alone --\n"
"it writes no header and no LASzip VLR, only the points and the chunk\n"
"table behind them, so what it produces has to be described by a header\n"
"something else wrote. lazpy.Writer is that something else.\n"
"\n"
"write() takes a Point or the bytes of one on-disk record; call done()\n"
"when the last point is in.\n");

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
