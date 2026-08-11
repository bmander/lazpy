/* Point: one decoded point, and the borrow/detach machinery the reader
 * and writer move points around with. */
#include "cpylaz.h"

/* ================================================================= Point == */

/*
 * A decoded point -- either a view onto one, or one of its own.
 *
 * PointReader.read() hands back the reader's own Point rather than a fresh
 * object, so reading 40M points does not allocate 40M objects; call copy() to
 * keep one past the next read().
 *
 * Ownership runs one way only: the reader holds a reference to its Point, and
 * the Point holds no reference back. A Point that is still alive when its
 * reader is destroyed gets detached first (see Reader_dealloc), copying the
 * last decoded values into its own storage, so a stale Point is frozen rather
 * than dangling.
 *
 * Points are also writable, because a file has to be written from something:
 * Point(X=..., ...) builds one from nothing, and every attribute assigns. What
 * assignment does to a point that is viewing a reader's buffer is spelled out
 * where the setters are.
 */
/* A zeroed point that owns itself. The three ways to get a Point -- built,
 * borrowed, copied -- all start here and then repoint or fill as they need. */
PointObject *point_alloc(PyTypeObject *type)
{
    PointObject *o = PyObject_New(PointObject, type);
    if (!o) return NULL;
    memset(&o->storage, 0, sizeof(o->storage));
    o->p = &o->storage;
    o->extra = NULL;
    o->num_extra = 0;
    o->extra_storage = NULL;
    return o;
}

/* A view onto memory owned by `reader`, valid until the reader detaches it. */
PyObject *Point_borrow(LazPoint *p, U8 *extra, U32 num_extra)
{
    PointObject *o = point_alloc(&Point_Type);
    if (!o) return NULL;
    o->p = p;
    o->extra = extra;
    o->num_extra = num_extra;
    return (PyObject *)o;
}

/* Copies the currently-viewed values into this object so it can outlive the
 * memory it was pointing at. Failure to allocate leaves extra bytes empty
 * rather than dangling. */
void Point_detach(PointObject *self)
{
    if (self->p == &self->storage) return;
    self->storage = *self->p;
    if (self->num_extra) {
        self->extra_storage = (U8 *)malloc(self->num_extra);
        if (self->extra_storage) {
            memcpy(self->extra_storage, self->extra, self->num_extra);
        } else {
            self->num_extra = 0;
        }
    }
    self->p = &self->storage;
    self->extra = self->extra_storage;
}

static void Point_dealloc(PointObject *self)
{
    free(self->extra_storage);
    PyObject_Del(self);
}

static PyObject *Point_copy(PointObject *self, PyObject *Py_UNUSED(i))
{
    PointObject *o = point_alloc(&Point_Type);
    if (!o) return NULL;
    o->storage = *self->p;
    o->num_extra = self->num_extra;
    if (self->num_extra) {
        o->extra_storage = (U8 *)malloc(self->num_extra);
        if (!o->extra_storage) { PyObject_Del(o); return PyErr_NoMemory(); }
        memcpy(o->extra_storage, self->extra, self->num_extra);
        o->extra = o->extra_storage;
    }
    return (PyObject *)o;
}

#define POINT_UGETTER(name, expr)                                              \
    static PyObject *Point_get_##name(PointObject *self, void *c)              \
    { (void)c; return PyLong_FromUnsignedLong((unsigned long)(expr)); }
#define POINT_IGETTER(name, expr)                                              \
    static PyObject *Point_get_##name(PointObject *self, void *c)              \
    { (void)c; return PyLong_FromLong((long)(expr)); }

POINT_IGETTER(X, self->p->X)
POINT_IGETTER(Y, self->p->Y)
POINT_IGETTER(Z, self->p->Z)
POINT_UGETTER(intensity, self->p->intensity)
POINT_UGETTER(return_number, laz_point_return_number(self->p))
POINT_UGETTER(number_of_returns, laz_point_number_of_returns(self->p))
POINT_UGETTER(scan_direction_flag, laz_point_scan_direction_flag(self->p))
POINT_UGETTER(edge_of_flight_line, laz_point_edge_of_flight_line(self->p))
POINT_UGETTER(classification, laz_point_classification(self->p))
POINT_UGETTER(synthetic_flag, laz_point_synthetic_flag(self->p))
POINT_UGETTER(keypoint_flag, laz_point_keypoint_flag(self->p))
POINT_UGETTER(withheld_flag, laz_point_withheld_flag(self->p))
POINT_IGETTER(scan_angle_rank, self->p->scan_angle_rank)
POINT_UGETTER(user_data, self->p->user_data)
POINT_UGETTER(point_source_ID, self->p->point_source_ID)
POINT_IGETTER(extended_scan_angle, self->p->extended_scan_angle)
POINT_UGETTER(extended_point_type, laz_point_extended_point_type(self->p))
POINT_UGETTER(extended_scanner_channel, laz_point_extended_scanner_channel(self->p))
POINT_UGETTER(extended_classification_flags, laz_point_extended_classification_flags(self->p))
POINT_UGETTER(extended_classification, self->p->extended_classification)
POINT_UGETTER(extended_return_number, laz_point_extended_return_number(self->p))
POINT_UGETTER(extended_number_of_returns, laz_point_extended_number_of_returns(self->p))

static PyObject *Point_get_gps_time(PointObject *self, void *c)
{ (void)c; return PyFloat_FromDouble(self->p->gps_time); }

static PyObject *Point_get_rgb(PointObject *self, void *c)
{
    PyObject *t;
    int i;
    (void)c;
    t = PyTuple_New(4);
    if (!t) return NULL;
    for (i = 0; i < 4; i++) {
        PyObject *v = PyLong_FromUnsignedLong(self->p->rgb[i]);
        if (!v) { Py_DECREF(t); return NULL; }
        PyTuple_SET_ITEM(t, i, v);
    }
    return t;
}

static PyObject *Point_get_wave_packet(PointObject *self, void *c)
{ (void)c; return PyBytes_FromStringAndSize((const char *)self->p->wave_packet, 29); }

static PyObject *Point_get_extra_bytes(PointObject *self, void *c)
{
    (void)c;
    if (!self->num_extra) return PyBytes_FromStringAndSize(NULL, 0);
    return PyBytes_FromStringAndSize((const char *)self->extra, self->num_extra);
}

/*
 * Setters. A Point assigned to writes through to whatever it is looking at,
 * which for the one a reader hands back is that reader's own point -- the same
 * buffer the next read() overwrites. That is the contract read() already has,
 * and the alternative is worse: quietly detaching would leave the reader
 * holding a Point that no longer follows it.
 *
 * Most of these fields are narrower than the C type holding them -- five bits
 * of classification, three of return number -- so each carries the range it
 * accepts and refuses anything outside it rather than truncating silently.
 */
static int point_settable(PyObject *v)
{
    if (v != NULL) return 0;
    PyErr_SetString(PyExc_AttributeError, "cannot delete a point attribute");
    return -1;
}

static int point_uvalue(PyObject *v, unsigned long max, unsigned long *out)
{
    unsigned long value;

    if (point_settable(v) < 0) return -1;
    value = PyLong_AsUnsignedLong(v);
    if (value == (unsigned long)-1 && PyErr_Occurred()) return -1;
    if (value > max) {
        PyErr_Format(PyExc_ValueError, "%lu is out of range (0 to %lu)", value, max);
        return -1;
    }
    *out = value;
    return 0;
}

static int point_ivalue(PyObject *v, long min, long max, long *out)
{
    long value;

    if (point_settable(v) < 0) return -1;
    value = PyLong_AsLong(v);
    if (value == -1 && PyErr_Occurred()) return -1;
    if (value < min || value > max) {
        PyErr_Format(PyExc_ValueError, "%ld is out of range (%ld to %ld)",
                     value, min, max);
        return -1;
    }
    *out = value;
    return 0;
}

#define POINT_USETTER(name, max)                                               \
    static int Point_set_##name(PointObject *self, PyObject *v, void *c)       \
    {                                                                          \
        unsigned long value;                                                   \
        (void)c;                                                               \
        if (point_uvalue(v, (max), &value) < 0) return -1;                     \
        self->p->name = value;                                                 \
        return 0;                                                              \
    }
#define POINT_ISETTER(name, min, max)                                          \
    static int Point_set_##name(PointObject *self, PyObject *v, void *c)       \
    {                                                                          \
        long value;                                                            \
        (void)c;                                                               \
        if (point_ivalue(v, (min), (max), &value) < 0) return -1;              \
        self->p->name = value;                                                 \
        return 0;                                                              \
    }
/* The bit-packed fields go through laz_types.h's accessors rather than an
 * assignment, so the packing is stated in one place -- including the bound,
 * which is the field's own mask and so comes from the same table rather than
 * being restated per field here. */
#define POINT_PSETTER(name)                                                    \
    static int Point_set_##name(PointObject *self, PyObject *v, void *c)       \
    {                                                                          \
        unsigned long value;                                                   \
        (void)c;                                                               \
        if (point_uvalue(v, LAZ_POINT_MAX_##name, &value) < 0) return -1;      \
        laz_point_set_##name(self->p, (U8)value);                              \
        return 0;                                                              \
    }

POINT_ISETTER(X, I32_MIN, I32_MAX)
POINT_ISETTER(Y, I32_MIN, I32_MAX)
POINT_ISETTER(Z, I32_MIN, I32_MAX)
POINT_USETTER(intensity, 0xFFFF)
POINT_PSETTER(return_number)
POINT_PSETTER(number_of_returns)
POINT_PSETTER(scan_direction_flag)
POINT_PSETTER(edge_of_flight_line)
POINT_PSETTER(classification)
POINT_PSETTER(synthetic_flag)
POINT_PSETTER(keypoint_flag)
POINT_PSETTER(withheld_flag)
POINT_ISETTER(scan_angle_rank, -128, 127)
POINT_USETTER(user_data, 0xFF)
POINT_USETTER(point_source_ID, 0xFFFF)
POINT_ISETTER(extended_scan_angle, -32768, 32767)
/* no setter for extended_point_type: see the getset table */
POINT_PSETTER(extended_scanner_channel)
POINT_PSETTER(extended_classification_flags)
POINT_USETTER(extended_classification, 0xFF)
POINT_PSETTER(extended_return_number)
POINT_PSETTER(extended_number_of_returns)

static int Point_set_gps_time(PointObject *self, PyObject *v, void *c)
{
    double value;
    (void)c;
    if (point_settable(v) < 0) return -1;
    value = PyFloat_AsDouble(v);
    if (value == -1.0 && PyErr_Occurred()) return -1;
    self->p->gps_time = value;
    return 0;
}

/* Three channels or four: NIR is only in point formats 8 and 10, and a
 * three-channel assignment leaves it alone rather than guessing at zero. */
static int Point_set_rgb(PointObject *self, PyObject *v, void *c)
{
    PyObject *seq;
    Py_ssize_t n, i;
    U16 channels[4];

    (void)c;
    if (point_settable(v) < 0) return -1;
    seq = PySequence_Fast(v, "rgb must be a sequence of 3 or 4 channels");
    if (!seq) return -1;
    n = PySequence_Fast_GET_SIZE(seq);
    if (n != 3 && n != 4) {
        PyErr_Format(PyExc_ValueError, "rgb takes 3 or 4 channels, not %zd", n);
        Py_DECREF(seq);
        return -1;
    }
    for (i = 0; i < n; i++) {
        unsigned long value = PyLong_AsUnsignedLong(PySequence_Fast_GET_ITEM(seq, i));
        if (value == (unsigned long)-1 && PyErr_Occurred()) { Py_DECREF(seq); return -1; }
        if (value > 0xFFFF) {
            PyErr_Format(PyExc_ValueError, "channel %zd is out of range (0 to 65535)", i);
            Py_DECREF(seq);
            return -1;
        }
        channels[i] = (U16)value;
    }
    Py_DECREF(seq);

    for (i = 0; i < n; i++) self->p->rgb[i] = channels[i];
    return 0;
}

static int Point_set_wave_packet(PointObject *self, PyObject *v, void *c)
{
    char *data;
    Py_ssize_t len;

    (void)c;
    if (point_settable(v) < 0) return -1;
    if (PyBytes_AsStringAndSize(v, &data, &len) < 0) return -1;
    if (len != 29) {
        PyErr_Format(PyExc_ValueError, "a wave packet is 29 bytes, not %zd", len);
        return -1;
    }
    memcpy(self->p->wave_packet, data, 29);
    return 0;
}

/* The one attribute that can change how much storage a point needs, and so the
 * one a reader's point cannot take freely: that buffer is the reader's, sized
 * by the file's item layout. Same length, and it is written in place like any
 * other field; a different length needs a point of one's own. */
static int Point_set_extra_bytes(PointObject *self, PyObject *v, void *c)
{
    char *data;
    Py_ssize_t len;
    U8 *storage = NULL;

    (void)c;
    if (point_settable(v) < 0) return -1;
    if (PyBytes_AsStringAndSize(v, &data, &len) < 0) return -1;
    if (len > (Py_ssize_t)U32_MAX) {
        PyErr_SetString(PyExc_ValueError, "too many extra bytes");
        return -1;
    }

    if ((U32)len == self->num_extra) {
        if (len) memcpy(self->extra, data, (size_t)len);
        return 0;
    }
    if (self->extra != self->extra_storage) {
        PyErr_Format(PyExc_ValueError,
                     "this point's extra bytes belong to a reader and are %u "
                     "bytes; copy() it to give it %zd", self->num_extra, len);
        return -1;
    }

    if (len) {
        storage = (U8 *)malloc((size_t)len);
        if (!storage) { PyErr_NoMemory(); return -1; }
        memcpy(storage, data, (size_t)len);
    }
    free(self->extra_storage);
    self->extra_storage = storage;
    self->extra = storage;
    self->num_extra = (U32)len;
    return 0;
}

static PyGetSetDef Point_getset[] = {
#define POINT_GETSET(name, doc) \
    {#name, (getter)Point_get_##name, (setter)Point_set_##name, doc, NULL}
    POINT_GETSET(X, "unscaled x: multiply by the header's x scale and add "
                    "its x offset for metres; Reader.scale does this"),
    POINT_GETSET(Y, "unscaled y; see X"),
    POINT_GETSET(Z, "unscaled z; see X"),
    POINT_GETSET(intensity, "return magnitude, 0 to 65535, normalised by the "
                            "system; 0 where it records none"),
    POINT_GETSET(return_number, "which return this pulse is, 1 to 7; formats "
                                "6-10 count to 15 in extended_return_number"),
    POINT_GETSET(number_of_returns, "returns this pulse gave, 1 to 7; formats "
                                    "6-10 count to 15 in "
                                    "extended_number_of_returns"),
    POINT_GETSET(scan_direction_flag, "1 while the mirror was moving from the "
                                      "left side of the scan to the right, "
                                      "0 the other way"),
    POINT_GETSET(edge_of_flight_line, "1 on the last point of a scan line"),
    POINT_GETSET(classification, "ASPRS class, 0 to 31 -- 1 unclassified, 2 "
                                 "ground, 3-5 vegetation, 6 building, 9 "
                                 "water; formats 6-10 carry the full 0-255 "
                                 "range in extended_classification"),
    POINT_GETSET(synthetic_flag, "1 if the point came from somewhere other "
                                 "than this survey"),
    POINT_GETSET(keypoint_flag, "1 if the point is a model keypoint, not to "
                                "be thinned away"),
    POINT_GETSET(withheld_flag, "1 if the point is deleted: present in the "
                                "file, not to be used"),
    POINT_GETSET(scan_angle_rank, "the angle off nadir in whole degrees, "
                                  "negative to the left of the aircraft and "
                                  "positive to the right; -90 to 90 in "
                                  "practice, and a signed byte's -128 to 127 "
                                  "at the limit. extended_scan_angle is the "
                                  "finer form the 1.4 formats carry"),
    POINT_GETSET(user_data, "0 to 255, meaning whatever the producer says"),
    POINT_GETSET(point_source_ID, "which flight line the point came from, or "
                                  "0 for this file itself"),
    POINT_GETSET(extended_scan_angle, "the angle off nadir in steps of 0.006 "
                                      "degrees, so -30000 to 30000 spans -180 "
                                      "to 180; scan_angle_rank holds the same "
                                      "angle rounded to degrees and clamped "
                                      "to what one signed byte holds"),
    /* read-only: a writer stamps this from the layout it is writing, so
     * setting it here would decide nothing */
    {"extended_point_type", (getter)Point_get_extended_point_type, NULL,
     "1 on a point of format 6-10", NULL},
    POINT_GETSET(extended_scanner_channel, "which of a multi-scanner "
                                           "system's channels, 0 to 3"),
    POINT_GETSET(extended_classification_flags,
                 "synthetic|keypoint|withheld|overlap; of these only overlap "
                 "reaches a LAS 1.4 record from here, the other three from "
                 "synthetic_flag, keypoint_flag and withheld_flag"),
    POINT_GETSET(extended_classification,
                 "the LAS 1.4 class; a record takes it only where the legacy "
                 "classification is 0"),
    POINT_GETSET(extended_return_number, "which return this pulse is, 1 to "
                                         "15"),
    POINT_GETSET(extended_number_of_returns, "returns this pulse gave, 1 to "
                                             "15"),
    POINT_GETSET(gps_time, "when the point was collected, in seconds. Which "
                           "epoch is the header's to say: GPS week seconds, "
                           "or -- with the global encoding bit set, as most "
                           "files have it -- standard GPS time less 1e9. 0.0 "
                           "on a format that carries no time"),
    POINT_GETSET(rgb, "(red, green, blue, nir), each 0 to 65535. Channels the "
                      "point format does not carry read as 0, and assigning "
                      "three channels leaves nir alone"),
    POINT_GETSET(wave_packet, "the 29 bytes of full-waveform description, as "
                              "they sit on disk -- little-endian on every "
                              "host, so unpack them with struct's '<': "
                              "descriptor index (1 byte), offset to the "
                              "waveform data (8), its size (4), the return's "
                              "location within it (4), and the parametric "
                              "dx, dy, dz (4 each)"),
    POINT_GETSET(extra_bytes, "the point's extra bytes, as many as "
                              "PointReader.num_extra_bytes says. Assigning a "
                              "different number is what a reader's shared "
                              "point will not take -- that buffer is sized by "
                              "the file -- so copy() first"),
#undef POINT_GETSET
    {NULL}
};

static PyMethodDef Point_methods[] = {
    {"copy", (PyCFunction)Point_copy, METH_NOARGS,
     "copy() -> Point\n\n"
     "A detached copy, safe to keep across reads."},
    {NULL}
};

static PyObject *Point_tp_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    (void)args; (void)kwds;
    return (PyObject *)point_alloc(type);
}

/* Point(X=1, classification=2, ...): every keyword is an attribute, so the
 * accepted names are exactly the settable ones and nothing lists them twice. */
static int Point_tp_init(PointObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *key, *value;
    Py_ssize_t pos = 0;

    if (PyTuple_GET_SIZE(args) != 0) {
        PyErr_SetString(PyExc_TypeError, "Point takes keyword arguments only");
        return -1;
    }
    while (kwds && PyDict_Next(kwds, &pos, &key, &value)) {
        if (PyObject_SetAttr((PyObject *)self, key, value) < 0) return -1;
    }
    return 0;
}

static PyObject *Point_repr(PointObject *self)
{
    return PyUnicode_FromFormat(
        "Point(X=%i, Y=%i, Z=%i, intensity=%u, return_number=%u, "
        "number_of_returns=%u, classification=%u)",
        self->p->X, self->p->Y, self->p->Z, (unsigned)self->p->intensity,
        (unsigned)laz_point_return_number(self->p),
        (unsigned)laz_point_number_of_returns(self->p),
        (unsigned)laz_point_classification(self->p));
}

PyDoc_STRVAR(point_doc,
"Point(**fields)\n"
"\n"
"One decoded point, with every LAS attribute as a settable attribute here.\n"
"\n"
"A point carries the fields of every format, not only its own: reading a\n"
"format that has no colour leaves rgb zero, and one that has no time leaves\n"
"gps_time 0.0. The extended_* attributes are the LAS 1.4 widenings of the\n"
"fields above them, populated by point formats 6-10 alone;\n"
"extended_point_type is 1 on those formats and 0 elsewhere. On formats 6-10\n"
"the legacy fields are populated too, kept consistent with the extended\n"
"ones by saturating rather than wrapping. Reader.arrays() names the\n"
"fields a given file really carries.\n"
"\n"
"The point a reader returns is the reader's own buffer and is overwritten\n"
"by the next read; copy() makes a detached one. Building a point for a\n"
"writer takes keywords, one per attribute:\n"
"\n"
"    Point(X=125000, Y=473000, Z=1200, classification=2)\n");

PyTypeObject Point_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.Point",
    .tp_basicsize = sizeof(PointObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = point_doc,
    .tp_new = Point_tp_new,
    .tp_init = (initproc)Point_tp_init,
    .tp_dealloc = (destructor)Point_dealloc,
    .tp_getset = Point_getset,
    .tp_methods = Point_methods,
    .tp_repr = (reprfunc)Point_repr,
};
