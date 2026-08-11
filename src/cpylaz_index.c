/* SpatialIndex: the parsed LASzip quadtree index. */
#include "cpylaz.h"

/* ========================================================== SpatialIndex == */

/*
 * A parsed ".lax" spatial index, whichever way it reached us.
 *
 * The payload is parsed once into the core's own arrays, so the buffer handed
 * in here is not kept: an index that lives inside the LAZ file and one that
 * lives beside it are the same bytes, and reading them is Python's business.
 */
typedef struct {
    PyObject_HEAD
    LazIndex ix;
    BOOL ready;
} IndexObject;

static int Index_tp_init(IndexObject *self, PyObject *args, PyObject *kwds)
{
    Py_buffer view;
    LazStream *stream;
    BOOL ok;
    static char *kwlist[] = {"data", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "y*", kwlist, &view))
        return -1;

    laz_index_destroy(&self->ix);           /* a second init starts over */
    self->ready = LAZ_FALSE;

    stream = laz_stream_new_array((const U8 *)view.buf, (I64)view.len);
    if (!stream) {
        PyBuffer_Release(&view);
        PyErr_NoMemory();
        return -1;
    }
    ok = laz_index_read(&self->ix, stream);
    laz_stream_destroy(stream);
    PyBuffer_Release(&view);

    if (!ok) {
        PyErr_SetString(LazErrorType, self->ix.has_error
                        ? self->ix.last_error : "could not read the spatial index");
        laz_index_destroy(&self->ix);
        return -1;
    }
    self->ready = LAZ_TRUE;
    return 0;
}

static void Index_dealloc(IndexObject *self)
{
    laz_index_destroy(&self->ix);
    Py_TYPE(self)->tp_free((PyObject *)self);
}

/* What both query methods do once the core has answered: the merged
 * intervals as a list of pairs, or the exception the failure deserves. */
static PyObject *index_result(IndexObject *self, BOOL ok)
{
    PyObject *list;
    U32 i;

    if (!ok) {
        PyErr_SetString(LazErrorType, self->ix.has_error
                        ? self->ix.last_error : "spatial index query failed");
        return NULL;
    }

    list = PyList_New(self->ix.num_merged);
    if (!list) return NULL;
    for (i = 0; i < self->ix.num_merged; i++) {
        PyObject *pair = Py_BuildValue(
            "(KK)", (unsigned long long)self->ix.merged[i].start,
            (unsigned long long)self->ix.merged[i].end);
        if (!pair) { Py_DECREF(list); return NULL; }
        PyList_SET_ITEM(list, i, pair);
    }
    return list;
}

static PyObject *Index_intervals(IndexObject *self, PyObject *args)
{
    double min_x, min_y, max_x, max_y;
    BOOL ok;

    if (!PyArg_ParseTuple(args, "dddd", &min_x, &min_y, &max_x, &max_y))
        return NULL;
    if (!self->ready) {
        PyErr_SetString(PyExc_ValueError, "index is not initialised");
        return NULL;
    }

    /* the descent and the merge touch no Python object, as the seek and the
     * checksum above do not */
    Py_BEGIN_ALLOW_THREADS
    ok = laz_index_intersect_rectangle(&self->ix, min_x, min_y, max_x, max_y);
    Py_END_ALLOW_THREADS
    return index_result(self, ok);
}

static PyObject *Index_intervals_within_circle(IndexObject *self,
                                               PyObject *args)
{
    double center_x, center_y, radius;
    BOOL ok;

    if (!PyArg_ParseTuple(args, "ddd", &center_x, &center_y, &radius))
        return NULL;
    if (!self->ready) {
        PyErr_SetString(PyExc_ValueError, "index is not initialised");
        return NULL;
    }

    Py_BEGIN_ALLOW_THREADS
    ok = laz_index_intersect_circle(&self->ix, center_x, center_y, radius);
    Py_END_ALLOW_THREADS
    return index_result(self, ok);
}

static PyObject *Index_get_bounds(IndexObject *self, void *c)
{
    const LazQuadtree *q = &self->ix.quadtree;
    (void)c;
    return Py_BuildValue("(dddd)", (double)q->min_x, (double)q->min_y,
                         (double)q->max_x, (double)q->max_y);
}

static PyObject *Index_get_levels(IndexObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->ix.quadtree.levels); }

static PyObject *Index_get_num_cells(IndexObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->ix.num_cells); }

static PyObject *Index_get_warning(IndexObject *self, void *c)
{
    (void)c;
    if (!self->ix.has_warning) Py_RETURN_NONE;
    return PyUnicode_FromString(self->ix.last_warning);
}

static PyMethodDef Index_methods[] = {
    {"intervals", (PyCFunction)Index_intervals, METH_VARARGS,
     "intervals(min_x, min_y, max_x, max_y) -> [(start, end), ...]  "
     "(inclusive point index ranges that may hold a point in the rectangle)"},
    {"intervals_within_circle", (PyCFunction)Index_intervals_within_circle,
     METH_VARARGS,
     "intervals_within_circle(center_x, center_y, radius) -> "
     "[(start, end), ...]  (the same for a circle, which reaches fewer cells "
     "than the square around it)"},
    {NULL}
};

static PyGetSetDef Index_getset[] = {
    {"bounds", (getter)Index_get_bounds, NULL,
     "(min_x, min_y, max_x, max_y) of the indexed area", NULL},
    {"levels", (getter)Index_get_levels, NULL,
     "how deep the quadtree goes", NULL},
    {"num_cells", (getter)Index_get_num_cells, NULL,
     "how many cells hold points", NULL},
    {"warning", (getter)Index_get_warning, NULL,
     "a non-fatal problem found while reading the index, or None", NULL},
    {NULL}
};

PyDoc_STRVAR(index_doc,
"SpatialIndex(data)\n"
"\n"
"A parsed LASzip spatial index, from the bytes of one.\n"
"\n"
"The index is a quadtree over the surveyed area whose cells name the runs\n"
"of point indices that landed in them, so a rectangle query can decode a\n"
"few chunks instead of the whole file. intervals() asks it a rectangle and\n"
"gets those runs back; intervals_within_circle() asks it a circle.\n"
"\n"
"`data` is the payload of a \".lax\" file or of the extended record an\n"
"appended index lives in -- the two carry the same bytes. Finding one is\n"
"Reader.spatial_index's job.\n");

PyTypeObject Index_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.SpatialIndex",
    .tp_basicsize = sizeof(IndexObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = index_doc,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Index_tp_init,
    .tp_dealloc = (destructor)Index_dealloc,
    .tp_methods = Index_methods,
    .tp_getset = Index_getset,
};
