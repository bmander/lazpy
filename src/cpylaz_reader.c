/* PointReader: the decode side of the container, plus the region and column
 * machinery its bulk-read entry points share. */
#include "cpylaz.h"

/* =========================================================== PointReader == */

typedef struct {
    PyObject_HEAD
    LazReadPoint rp;
    LazStream *stream;
    PyObject *fp;
    LazPoint point;
    U8 *extra_bytes;
    /* what a caller sees, which in compatibility mode is less than the item
     * layout decodes -- that is self->rp.num_extra_bytes */
    U32 num_extra_bytes;
    /* LAS 1.4 compatibility mode: where in the extra bytes the packed-away
     * 1.4 fields live, in the order CompatibilityLayout names them, with -1
     * for COMPAT_NIR when there is no NIR band. compat is false and the starts
     * are meaningless for an ordinary file. */
    BOOL compat;
    I32 compat_starts[COMPAT_ATTRIBUTES];
    /* the quantized scan angle rank for every rank there is; see
     * reader_recode_compat, which would otherwise divide once per point */
    const I16 *scan_angle_of_rank;
    PyObject *point_view;
    BOOL ready;
    U64 index;              /* number of points read so far */
} ReaderObject;

/* The widths of the hidden attributes, in the order cpylaz.h names them. */
const U32 compat_widths[COMPAT_ATTRIBUTES] = {2, 1, 1, 1, 2};

/*
 * The scan angle every rank stands for, built once and shared by both
 * directions: a division per point is what tabulating it avoids, and the
 * writer needs the same 256 values to work out what a rank leaves over.
 *
 * Filled on first use rather than at module init because almost no file is a
 * compatibility-mode one. Two threads racing here write the same values.
 */
const I16 *compat_scan_angle_of_rank(void)
{
    static I16 table[256];
    static BOOL filled = LAZ_FALSE;
    int i;

    if (!filled) {
        for (i = 0; i < 256; i++)
            table[i] = COMPAT_SCAN_ANGLE_OF_RANK((I8)i);
        filled = LAZ_TRUE;
    }
    return table;
}

/*
 * Reconstitute a LAS 1.4 point that was written as a legacy one.
 *
 * A LAS 1.4 compatibility-mode file stores a format 6-10 point as a format
 * 1/3/4/5 point plus five (or seven, with a NIR band) extra bytes holding what
 * the legacy record cannot express. Rebuilding the real point means adding
 * those back to what the legacy fields already carry: the scan angle is a
 * remainder on top of the quantized rank, the return numbers and the
 * classification are increments on top of the narrow legacy values, and the
 * classification flags are the legacy three plus the overlap bit.
 *
 * This is laszip_read_point()'s recoding step in laszip_dll.cpp, and the
 * inverse of what laszip_write_point() does on the way in. The truncating
 * assignments into the 4-bit return-number fields and the wrapping one into
 * extended_classification are laszip's too, not an oversight here.
 */
static void reader_recode_compat(ReaderObject *self)
{
    LazPoint *p = &self->point;
    const U8 *extra = self->extra_bytes;
    const I32 *at = self->compat_starts;
    I16 scan_angle_remainder;
    U8 extended_returns = extra[at[COMPAT_EXTENDED_RETURNS]];
    U8 classification = extra[at[COMPAT_CLASSIFICATION]];
    U8 flags_and_channel = extra[at[COMPAT_FLAGS_AND_CHANNEL]];

    /* the extra bytes are on-disk data, so little-endian whatever the host */
    scan_angle_remainder = (I16)laz_le_get16(extra + at[COMPAT_SCAN_ANGLE]);
    if (at[COMPAT_NIR] >= 0)
        p->rgb[3] = laz_le_get16(extra + at[COMPAT_NIR]);

    p->extended_scan_angle = (I16)(scan_angle_remainder +
        self->scan_angle_of_rank[(U8)p->scan_angle_rank]);
    laz_point_set_extended_return_number(
        p, (U8)(((extended_returns >> 4) & 0x0F) + laz_point_return_number(p)));
    laz_point_set_extended_number_of_returns(
        p, (U8)((extended_returns & 0x0F) + laz_point_number_of_returns(p)));
    p->extended_classification =
        (U8)(classification + laz_point_classification(p));
    laz_point_set_extended_scanner_channel(p, (flags_and_channel >> 1) & 0x03);
    laz_point_set_extended_classification_flags(
        p, (U8)(((flags_and_channel & 0x01) << 3)
                | (laz_point_withheld_flag(p) << 2)
                | (laz_point_keypoint_flag(p) << 1)
                | laz_point_synthetic_flag(p)));
    laz_point_set_extended_point_type(p, 1);
}

/*
 * Decode the next point, whole.
 *
 * Every path that hands a point to a caller goes through here, so that "a
 * decoded point has had its LAS 1.4 fields put back" is one statement rather
 * than one per read loop. Seeking decodes points too (laz_readpoint_seek) and
 * deliberately does not come this way: those points are passed over, not
 * handed out.
 */
/*
 * Whether the file object underneath is still answering.
 *
 * The core reads a stream that cannot fail: past the end it hands back zeros
 * and sets `eof`, so a decode carries on rather than stopping. When the
 * failure is Python's -- a file object whose seek() or read() raised -- that
 * is not something to decode through: the stream records it in `failed` and
 * leaves the exception set for whoever is holding the GIL to find, which is
 * this file. Without this check the exception would dangle until the
 * interpreter noticed it on the way out, and the caller would be handed a
 * point decoded from zeros.
 */
static BOOL reader_stream_ok(ReaderObject *self)
{
    return !self->stream || !self->stream->failed;
}

static BOOL reader_next(ReaderObject *self)
{
    if (!laz_readpoint_read(&self->rp, &self->point, self->extra_bytes))
        return LAZ_FALSE;
    if (!reader_stream_ok(self)) return LAZ_FALSE;
    if (self->compat) reader_recode_compat(self);
    return LAZ_TRUE;
}

static void Reader_dealloc(ReaderObject *self)
{
    /* the view points into memory this object is about to free */
    if (self->point_view) {
        Point_detach((PointObject *)self->point_view);
        Py_CLEAR(self->point_view);
    }
    laz_readpoint_destroy(&self->rp);
    if (self->stream) laz_stream_destroy(self->stream);
    Py_XDECREF(self->fp);
    free(self->extra_bytes);
    PyObject_Del(self);
}

/* items is a sequence of (type, size, version) triples from the LASzip VLR. */
int parse_items(PyObject *seq, LazItem **out, U32 *out_n)
{
    Py_ssize_t n, i;
    LazItem *items;

    seq = PySequence_Fast(seq, "items must be a sequence");
    if (!seq) return -1;
    n = PySequence_Fast_GET_SIZE(seq);
    if (n <= 0) {
        Py_DECREF(seq);
        PyErr_SetString(PyExc_ValueError, "items must not be empty");
        return -1;
    }
    items = (LazItem *)PyMem_Malloc((size_t)n * sizeof(LazItem));
    if (!items) { Py_DECREF(seq); PyErr_NoMemory(); return -1; }

    for (i = 0; i < n; i++) {
        PyObject *t = PySequence_Fast_GET_ITEM(seq, i);
        unsigned int type, size, version;
        if (!PyArg_ParseTuple(t, "III", &type, &size, &version)) {
            PyMem_Free(items);
            Py_DECREF(seq);
            return -1;
        }
        items[i].type = (U16)type;
        items[i].size = (U16)size;
        items[i].version = (U16)version;
    }
    Py_DECREF(seq);
    *out = items;
    *out_n = (U32)n;
    return 0;
}

/*
 * The `compatibility` argument: the five attribute starts, as
 * lazpy/compat.py read them out of the "extra bytes" VLR, or None.
 *
 * Only start_NIR_band may be absent, as -1. The bytes a start addresses have
 * to be inside the extra bytes the layout holds, since the recoding reads
 * them for every point with no further checking. Shared with the writer,
 * which puts there what this takes back out.
 */
int parse_compat_starts(PyObject *obj, U32 num_extra_bytes,
                        I32 starts[COMPAT_ATTRIBUTES])
{
    int i;

    if (obj == NULL || obj == Py_None) return 0;
    if (!PyArg_ParseTuple(obj, "iiiii", &starts[0], &starts[1], &starts[2],
                          &starts[3], &starts[4]))
        return -1;

    for (i = 0; i < COMPAT_ATTRIBUTES; i++) {
        if (i == COMPAT_NIR && starts[i] < 0) continue;     /* no NIR band */
        /* by subtraction, so a start near U32_MAX cannot wrap past the end */
        if (starts[i] < 0 || compat_widths[i] > num_extra_bytes ||
            (U32)starts[i] > num_extra_bytes - compat_widths[i]) {
            PyErr_SetString(LazErrorType, "a LAS 1.4 compatibility attribute "
                            "lies outside the extra bytes");
            return -1;
        }
    }
    return 1;
}

static int parse_compatibility(ReaderObject *self, PyObject *obj)
{
    I32 *starts = self->compat_starts;
    int i, found = parse_compat_starts(obj, self->rp.num_extra_bytes, starts);

    if (found <= 0) return found;

    for (i = 0; i < COMPAT_ATTRIBUTES; i++) {
        /* everything from the first compatibility attribute on belongs to the
         * reconstituted point rather than to the caller's extra bytes */
        if (starts[i] >= 0 && (U32)starts[i] < self->num_extra_bytes)
            self->num_extra_bytes = (U32)starts[i];
    }

    self->scan_angle_of_rank = compat_scan_angle_of_rank();
    self->compat = LAZ_TRUE;
    return 0;
}

static int Reader_tp_init(ReaderObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *fp, *items_obj, *compatibility = NULL;
    unsigned int compressor, coder = 0, chunk_size = 0;
    unsigned int selective = LAZ_DECOMPRESS_SELECTIVE_ALL;
    long long start_offset = -1;
    LazItem *items = NULL;
    U32 num_items = 0;
    static char *kwlist[] = {"fp", "items", "compressor", "coder", "chunk_size",
                             "start_offset", "decompress_selective",
                             "compatibility", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOI|IILIO", kwlist,
                                     &fp, &items_obj, &compressor, &coder,
                                     &chunk_size, &start_offset, &selective,
                                     &compatibility))
        return -1;

    if (parse_items(items_obj, &items, &num_items) < 0) return -1;

    /* Setup first: it decides how many extra bytes the items imply, so the
     * buffer the readers write into is sized by the core rather than trusted
     * from the caller. */
    laz_readpoint_init_struct(&self->rp, selective);
    if (!laz_readpoint_setup(&self->rp, num_items, items, compressor, coder, chunk_size)) {
        PyMem_Free(items);
        PyErr_SetString(LazErrorType, self->rp.last_error);
        return -1;
    }
    PyMem_Free(items);

    self->num_extra_bytes = self->rp.num_extra_bytes;
    if (self->num_extra_bytes) {
        self->extra_bytes = (U8 *)calloc(self->num_extra_bytes, 1);
        if (!self->extra_bytes) { PyErr_NoMemory(); return -1; }
    }
    /* may trim num_extra_bytes: the compatibility attributes are decoded but
     * are not the caller's extra bytes */
    if (parse_compatibility(self, compatibility) < 0) return -1;
    self->point.num_extra_bytes = (I32)self->num_extra_bytes;
    self->point.extra_bytes = self->extra_bytes;

    self->stream = laz_stream_new_file(fp);
    if (!self->stream) { PyErr_NoMemory(); return -1; }
    Py_INCREF(fp);
    self->fp = fp;

    if (start_offset >= 0 && !laz_stream_seek(self->stream, (I64)start_offset)) {
        PyErr_SetString(LazErrorType, "could not seek to the start of point data");
        return -1;
    }

    if (!laz_readpoint_init(&self->rp, self->stream)) {
        PyErr_SetString(LazErrorType, "could not initialise the point reader");
        return -1;
    }

    laz_readpoint_init_point(&self->rp, &self->point);

    self->point_view = Point_borrow(&self->point, self->extra_bytes,
                                    self->num_extra_bytes);
    if (!self->point_view) return -1;

    self->ready = LAZ_TRUE;
    self->index = 0;
    return 0;
}

/*
 * Raises for a failed core operation, preferring the most specific cause:
 * an exception the underlying file object already raised, then the core's own
 * message, then a generic fallback.
 */
static PyObject *reader_error(ReaderObject *self)
{
    if (PyErr_Occurred()) return NULL;            /* propagate the original */
    if (self->stream && self->stream->failed) {
        PyErr_SetString(LazErrorType, "error reading from the underlying file");
        return NULL;
    }
    PyErr_SetString(LazErrorType,
                    self->rp.has_error ? self->rp.last_error : "read failed");
    return NULL;
}

static PyObject *Reader_read(ReaderObject *self, PyObject *Py_UNUSED(i))
{
    BOOL ok;
    if (!self->ready) {
        PyErr_SetString(PyExc_ValueError, "reader is not initialised");
        return NULL;
    }
    /* The GIL is deliberately held. Decoding one point costs less than a
     * release/reacquire pair, and the only Python re-entry underneath is the
     * stream refill roughly once per 64 KB. */
    ok = reader_next(self);

    if (!ok) return reader_error(self);
    self->index++;
    Py_INCREF(self->point_view);
    return self->point_view;
}

/*
 * Decodes `count` points and returns an FNV-1a hash of every decoded field,
 * without building a Python object per point. This exists so a whole file can
 * be checked against a laszip reference (tools/lazdump.c --hash) -- at tens of
 * millions of points, hashing in Python is the bottleneck, not decoding.
 * The record layout must match lazdump.c and compare_with_laszip.py exactly,
 * which is why it is laid out little-endian rather than copied out of the
 * point: the hashes in testdata/reference_hashes.txt are a property of the
 * file, and must not depend on the host that computed them.
 */
static PyObject *Reader_checksum(ReaderObject *self, PyObject *args)
{
    long long count = -1;
    U64 h = 14695981039346656037ULL;
    U64 done = 0;
    BOOL ok = LAZ_TRUE;

    if (!PyArg_ParseTuple(args, "|L", &count)) return NULL;

    Py_BEGIN_ALLOW_THREADS
    while (count < 0 || done < (U64)count) {
        U8 rec[64];
        LazPoint *p = &self->point;
        int i;

        if (!reader_next(self)) { ok = LAZ_FALSE; break; }

        laz_le_put32(rec + 0, (U32)p->X);
        laz_le_put32(rec + 4, (U32)p->Y);
        laz_le_put32(rec + 8, (U32)p->Z);
        laz_le_put16(rec + 12, p->intensity);
        rec[14] = p->returns_and_flags;
        rec[15] = p->classification_bits;
        rec[16] = (U8)p->scan_angle_rank;
        rec[17] = p->user_data;
        laz_le_put16(rec + 18, p->point_source_ID);
        laz_le_put_f64(rec + 20, p->gps_time);
        for (i = 0; i < 4; i++) laz_le_put16(rec + 28 + 2 * i, p->rgb[i]);
        laz_le_put16(rec + 36, (U16)p->extended_scan_angle);
        rec[38] = p->extended_flags;
        rec[39] = p->extended_classification;
        rec[40] = p->extended_returns;
        memcpy(rec + 41, p->wave_packet, 23);

        for (i = 0; i < 64; i++) { h ^= rec[i]; h *= 1099511628211ULL; }
        for (i = 23; i < 29; i++) { h ^= p->wave_packet[i]; h *= 1099511628211ULL; }
        for (i = 0; i < (int)self->num_extra_bytes; i++) {
            h ^= self->extra_bytes[i];
            h *= 1099511628211ULL;
        }
        done++;
    }
    Py_END_ALLOW_THREADS

    if (!ok) return reader_error(self);
    self->index += done;
    return Py_BuildValue("(KK)", (unsigned long long)h, (unsigned long long)done);
}

/*
 * One column of the array path: where a field sits in the decoded point, and
 * where each point's copy of it goes. Both source pointers are fixed for the
 * life of the reader -- the point and its extra-bytes buffer are reused, not
 * reallocated -- so they are resolved once and the loop below only advances
 * the destination.
 */
typedef struct {
    const U8 *src;
    U8 *dst;
    Py_ssize_t size;
} Column;

/* The part of LazPoint the item readers fill; everything past it is
 * bookkeeping (the extra-bytes count and pointer), not decoded data. */
#define POINT_FIXED_EXTENT (LAZ_POINT_OFFSET_WAVEPACKET + 29)

/* ----------------------------------------------------------- region queries */

/*
 * The area a query is over, and what turns a stored point into the
 * coordinates it is in.
 *
 * A rectangle, or -- where `radius` is above zero -- the circle inside it,
 * which is the shape the index can answer more cheaply than the square
 * around it and the shape anyone selecting around a point wants.
 *
 * The test is done in scaled floats rather than by converting the rectangle
 * to raw integer bounds once. Converting would skip this multiply-add per
 * candidate, but it is not bit-equivalent at the boundaries, and which points
 * a query selects is pinned against laszip's own answer in
 * testdata/reference_inside.txt.
 */
typedef struct {
    double min_x, min_y, max_x, max_y;
    double scale_x, scale_y, offset_x, offset_y;
    double center_x, center_y, radius;
} Region;

static BOOL point_inside(const Region *r, const LazPoint *p)
{
    double x = p->X * r->scale_x + r->offset_x;
    double y = p->Y * r->scale_y + r->offset_y;

    if (r->radius > 0) {
        /* strictly inside, as LASquadtree tests a circle; there are no
         * adjoining circles for a half-open edge to divide */
        double dx = x - r->center_x, dy = y - r->center_y;
        return dx * dx + dy * dy < r->radius * r->radius;
    }
    /* half-open, as laszip_read_inside_point is: adjoining rectangles
     * partition the points rather than sharing the ones on the seam */
    return x >= r->min_x && x < r->max_x && y >= r->min_y && y < r->max_y;
}

/*
 * Decodes forward to the next point inside the region, or to `stop`.
 *
 * Returns 1 with the point decoded, 0 having reached `stop`, and -1 on a
 * decode failure -- which sets nothing, since it may run with the GIL
 * released; the caller raises through reader_error.
 *
 * This is the whole reason the region is known down here: a query's
 * candidates outnumber its results -- 49,000 to 400 for a small square of a
 * million-point file -- and every rejected one used to cross into Python and
 * box two integers to be tested there.
 */
static int reader_next_within(ReaderObject *self, U64 stop, const Region *region)
{
    while (self->index < stop) {
        if (!reader_next(self)) return -1;
        self->index++;
        if (point_inside(region, &self->point)) return 1;
    }
    return 0;
}

/* The area and what puts a point in it, as one flat tuple of doubles:
 * lazpy.Reader._region builds it once for a whole query. The last three are
 * the circle, whose radius is zero for a rectangle query. */
static int region_convert(PyObject *obj, void *out)
{
    Region *r = (Region *)out;
    return PyArg_ParseTuple(obj, "ddddddddddd;a region is eleven floats",
                            &r->min_x, &r->min_y, &r->max_x, &r->max_y,
                            &r->scale_x, &r->scale_y,
                            &r->offset_x, &r->offset_y,
                            &r->center_x, &r->center_y, &r->radius);
}

static PyObject *Reader_read_within(ReaderObject *self, PyObject *args)
{
    unsigned long long stop;
    Region region;
    int found;

    if (!PyArg_ParseTuple(args, "KO&", &stop, region_convert, &region))
        return NULL;
    if (!self->ready) {
        PyErr_SetString(PyExc_ValueError, "reader is not initialised");
        return NULL;
    }

    /*
     * The first candidate is decoded with the GIL held, as read() is and for
     * read()'s reason: one point costs less than a release and reacquire. If
     * it is inside, that is the whole call.
     *
     * Only once one has been rejected does this settle in for a scan and let
     * go -- and then it may be decoding thousands, since a query worth making
     * rejects far more than it returns.
     */
    if (self->index < (U64)stop) {
        if (!reader_next(self)) return reader_error(self);
        self->index++;
        if (point_inside(&region, &self->point)) {
            Py_INCREF(self->point_view);
            return self->point_view;
        }
    }

    Py_BEGIN_ALLOW_THREADS
    found = reader_next_within(self, (U64)stop, &region);
    Py_END_ALLOW_THREADS

    if (found < 0) return reader_error(self);
    if (found == 0) Py_RETURN_NONE;
    Py_INCREF(self->point_view);
    return self->point_view;
}

/*
 * Decodes `count` points straight into caller-owned buffers, one per field.
 *
 * `targets` is a sequence of (buffer, offset, size) triples: a writable
 * C-contiguous buffer, the byte offset of the field inside the decoded point,
 * and the field's width. Offset -1 means the extra bytes, which are a blob
 * beside the point rather than a field in it.
 *
 * This is what makes the numpy API in lazpy/__init__.py worth having: whole
 * columns arrive with one Python call and no object per point, where
 * iterating read() costs an attribute lookup and a boxed int per field.
 * Nothing here knows what a field means -- names, types and the unpacking of
 * the sub-byte fields are Python's business.
 */
/*
 * The columns of one read_into call, and the buffers they are writing into.
 *
 * Opened from the caller's `targets` and closed whatever happens after, since
 * every one of them is holding a buffer view.
 */
typedef struct {
    PyObject *seq;
    Column *cols;
    Py_buffer *views;
    Py_ssize_t n;
    Py_ssize_t held;        /* views actually acquired, which is what to release */
} Columns;

static void columns_close(Columns *c)
{
    Py_ssize_t i;
    for (i = 0; i < c->held; i++) PyBuffer_Release(&c->views[i]);
    PyMem_Free(c->views);
    PyMem_Free(c->cols);
    Py_XDECREF(c->seq);
    c->seq = NULL; c->cols = NULL; c->views = NULL; c->n = 0; c->held = 0;
}

static BOOL columns_open_partial(ReaderObject *self, PyObject *targets,
                                 Py_ssize_t capacity, Columns *c)
{
    Py_ssize_t i;

    c->seq = NULL; c->cols = NULL; c->views = NULL; c->n = 0; c->held = 0;

    /* Every failure below closes what it opened, so a caller that got FALSE
     * has nothing left to release. */
    c->seq = PySequence_Fast(targets, "targets must be a sequence");
    if (!c->seq) return LAZ_FALSE;
    c->n = PySequence_Fast_GET_SIZE(c->seq);

    c->cols = (Column *)PyMem_Malloc((size_t)c->n * sizeof(Column));
    c->views = (Py_buffer *)PyMem_Malloc((size_t)c->n * sizeof(Py_buffer));
    if (!c->cols || !c->views) { PyErr_NoMemory(); return LAZ_FALSE; }

    for (i = 0; i < c->n; i++) {
        PyObject *t = PySequence_Fast_GET_ITEM(c->seq, i);
        PyObject *buf;
        Py_ssize_t offset, size;

        if (!PyArg_ParseTuple(t, "Onn", &buf, &offset, &size)) return LAZ_FALSE;
        if (size <= 0) {
            PyErr_SetString(PyExc_ValueError, "field width must be positive");
            return LAZ_FALSE;
        }
        if (capacity > PY_SSIZE_T_MAX / size) {
            PyErr_SetString(PyExc_OverflowError, "count is too large");
            return LAZ_FALSE;
        }
        /* Both bounds are checked by subtraction rather than by adding
         * offset and size, which are the caller's and could overflow. */
        if (offset == -1) {
            if (size > (Py_ssize_t)self->num_extra_bytes) {
                PyErr_SetString(PyExc_ValueError,
                                "field lies outside the extra bytes");
                return LAZ_FALSE;
            }
            c->cols[i].src = self->extra_bytes;
        } else {
            if (offset < 0 || offset > POINT_FIXED_EXTENT - size) {
                PyErr_SetString(PyExc_ValueError,
                                "field lies outside the decoded point");
                return LAZ_FALSE;
            }
            c->cols[i].src = (const U8 *)&self->point + offset;
        }

        if (PyObject_GetBuffer(buf, &c->views[i],
                               PyBUF_WRITABLE | PyBUF_C_CONTIGUOUS) < 0)
            return LAZ_FALSE;
        c->held = i + 1;
        if (c->views[i].len < capacity * size) {
            PyErr_SetString(PyExc_ValueError, "output buffer is too small");
            return LAZ_FALSE;
        }
        c->cols[i].dst = (U8 *)c->views[i].buf;
        c->cols[i].size = size;
    }
    return LAZ_TRUE;
}

/* Resolves `targets` against this reader's point, checking that every buffer
 * has room for `capacity` points. Returns false with an exception set, having
 * released whatever it had already taken -- so only a successful open needs
 * closing. */
static BOOL columns_open(ReaderObject *self, PyObject *targets,
                         Py_ssize_t capacity, Columns *c)
{
    if (columns_open_partial(self, targets, capacity, c)) return LAZ_TRUE;
    columns_close(c);
    return LAZ_FALSE;
}

/* Copies the point just decoded into the columns, advancing each. */
static void columns_append(Columns *c)
{
    Py_ssize_t i;
    for (i = 0; i < c->n; i++) {
        memcpy(c->cols[i].dst, c->cols[i].src, (size_t)c->cols[i].size);
        c->cols[i].dst += c->cols[i].size;    /* on to this column's next */
    }
}

static PyObject *Reader_read_into(ReaderObject *self, PyObject *args)
{
    PyObject *targets, *result = NULL;
    Py_ssize_t count, done = 0;
    Columns c;
    BOOL ok = LAZ_TRUE;

    if (!PyArg_ParseTuple(args, "On", &targets, &count)) return NULL;
    if (!self->ready) {
        PyErr_SetString(PyExc_ValueError, "reader is not initialised");
        return NULL;
    }
    if (count < 0) {
        PyErr_SetString(PyExc_ValueError, "count must not be negative");
        return NULL;
    }
    if (!columns_open(self, targets, count, &c)) return NULL;

    Py_BEGIN_ALLOW_THREADS
    for (done = 0; done < count; done++) {
        if (!reader_next(self)) {
            ok = LAZ_FALSE;
            break;
        }
        columns_append(&c);
    }
    Py_END_ALLOW_THREADS

    /* Points that did decode stay decoded, so the index has to follow them
     * even when the read that failed leaves the arrays part-filled. */
    self->index += (U64)done;

    if (ok) {
        result = Py_None;
        Py_INCREF(result);
    } else {
        result = reader_error(self);         /* raises; returns NULL */
    }

    columns_close(&c);
    return result;
}

/*
 * read_into for a region: decodes up to `stop` and writes only the points
 * inside, returning how many that was.
 *
 * The result count is not knowable in advance -- that is what the query is
 * for -- so the caller sizes the buffers for the whole candidate span and
 * trims to the return value.
 */
static PyObject *Reader_read_into_within(ReaderObject *self, PyObject *args)
{
    PyObject *targets, *result = NULL;
    unsigned long long stop;
    Py_ssize_t written = 0, room;
    Region region;
    Columns c;
    int found = 0;

    if (!PyArg_ParseTuple(args, "OKO&", &targets, &stop, region_convert, &region))
        return NULL;
    if (!self->ready) {
        PyErr_SetString(PyExc_ValueError, "reader is not initialised");
        return NULL;
    }

    /* Every point between here and `stop` could be inside, so that is what
     * the buffers have to hold. */
    room = (stop > (unsigned long long)self->index)
         ? (Py_ssize_t)(stop - self->index) : 0;
    if (!columns_open(self, targets, room, &c)) return NULL;

    Py_BEGIN_ALLOW_THREADS
    while (written < room) {
        found = reader_next_within(self, (U64)stop, &region);
        if (found != 1) break;
        columns_append(&c);
        written++;
    }
    Py_END_ALLOW_THREADS

    if (found < 0) result = reader_error(self);   /* raises; returns NULL */
    else result = PyLong_FromSsize_t(written);

    columns_close(&c);
    return result;
}

static PyObject *Reader_seek(ReaderObject *self, PyObject *args)
{
    unsigned long long target;
    BOOL ok;
    if (!PyArg_ParseTuple(args, "K", &target)) return NULL;

    Py_BEGIN_ALLOW_THREADS
    ok = laz_readpoint_seek(&self->rp, self->index, (U64)target);
    Py_END_ALLOW_THREADS

    if (!ok || !reader_stream_ok(self)) return reader_error(self);
    self->index = (U64)target;
    Py_RETURN_NONE;
}

static PyObject *Reader_get_point(ReaderObject *self, void *c)
{ (void)c; Py_INCREF(self->point_view); return self->point_view; }

static PyObject *Reader_get_index(ReaderObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLongLong(self->index); }

static PyObject *Reader_get_chunk_starts(ReaderObject *self, void *c)
{
    PyObject *list;
    U32 i;
    (void)c;
    if (!self->rp.chunk_starts) Py_RETURN_NONE;
    list = PyList_New(self->rp.tabled_chunks);
    if (!list) return NULL;
    for (i = 0; i < self->rp.tabled_chunks; i++) {
        PyObject *v = PyLong_FromLongLong((long long)self->rp.chunk_starts[i]);
        if (!v) { Py_DECREF(list); return NULL; }
        PyList_SET_ITEM(list, i, v);
    }
    return list;
}

static PyObject *Reader_get_num_extra_bytes(ReaderObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->num_extra_bytes); }

static PyObject *Reader_get_warning(ReaderObject *self, void *c)
{
    (void)c;
    if (!self->rp.has_warning) Py_RETURN_NONE;
    return PyUnicode_FromString(self->rp.last_warning);
}

static PyMethodDef Reader_methods[] = {
    {"read", (PyCFunction)Reader_read, METH_NOARGS,
     "read() -> Point\n\n"
     "Decode the next point. The result is the reader's shared Point, "
     "overwritten by the next read(); call copy() to keep it."},
    {"read_into", (PyCFunction)Reader_read_into, METH_VARARGS,
     "read_into(targets, count) -> None\n\n"
     "Decode count points straight into buffers, one field per target.\n"
     "A target is (buffer, offset, size): where in a decoded point the "
     "field sits and how wide it is. This is what Reader.arrays() is "
     "built on, and it holds no Python object per point."},
    {"read_within", (PyCFunction)Reader_read_within, METH_VARARGS,
     "read_within(stop, region) -> Point | None\n\n"
     "Decode forward to the next point inside the region, or to index "
     "stop, whichever comes first; None means stop was reached with "
     "nothing inside. A region is one flat tuple of eleven floats -- "
     "min_x, min_y, max_x, max_y, then the x and y scales and offsets "
     "that turn a stored coordinate into the georeferenced one the area "
     "is in, then a centre and a radius -- so testing a point costs no "
     "Python call. A radius above zero selects the circle inside that "
     "rectangle rather than the rectangle."},
    {"read_into_within", (PyCFunction)Reader_read_into_within, METH_VARARGS,
     "read_into_within(targets, stop, region) -> int\n\n"
     "read_into and read_within at once: decode to index stop, writing "
     "only the points inside the region, and return how many that was. "
     "How many there will be is what the query is for, so the caller "
     "sizes the targets for the whole span and trims to the result."},
    {"seek", (PyCFunction)Reader_seek, METH_VARARGS,
     "seek(index) -> None\n\n"
     "Make index the next point to be read. Costs a chunk decode where "
     "there is a chunk table to jump by, and a decode from the last "
     "known boundary where there is not."},
    {"checksum", (PyCFunction)Reader_checksum, METH_VARARGS,
     "checksum(count=-1) -> (fnv1a_hash, points_read)\n\n"
     "Decode count points, hashing every field of each, and advance past "
     "them. Runs entirely in C with the GIL released, so verifying a "
     "forty-million-point file against laszip stays quick."},
    {NULL}
};

static PyGetSetDef Reader_getset[] = {
    {"point", (getter)Reader_get_point, NULL,
     "the Point read() returns: one buffer for the life of the reader, "
     "holding the last point decoded",
     NULL},
    {"index", (getter)Reader_get_index, NULL,
     "which point read() will decode next, counting from 0", NULL},
    {"chunk_starts", (getter)Reader_get_chunk_starts, NULL,
     "where each chunk begins, as offsets into the file, or None on a file "
     "with no chunk table. Read from the table at the first point rather "
     "than when the reader is built, so it is None until then; where the "
     "table was missing or corrupt (see warning) it holds only the "
     "boundaries reading has reached so far and grows as it reaches more",
     NULL},
    {"num_extra_bytes", (getter)Reader_get_num_extra_bytes, NULL,
     "how many extra bytes a decoded point carries -- the item layout's, less "
     "any the LAS 1.4 compatibility attributes take up", NULL},
    {"warning", (getter)Reader_get_warning, NULL,
     "a non-fatal problem met while reading, or None. A missing or corrupt "
     "chunk table is the usual one: points still decode, but seeking has to "
     "decode forward to reach them",
     NULL},
    {NULL}
};

PyDoc_STRVAR(reader_doc,
/* One line, however long: this is the signature Sphinx and help() lift off
 * the front of the docstring, and they only recognise it unwrapped. */
"PointReader(fp, items, compressor, coder=0, chunk_size=0, start_offset=-1, decompress_selective=Selective.ALL, compatibility=None)\n"
"\n"
"The point block of a LAS or LAZ file, decoded.\n"
"\n"
"This is the container alone. It is handed an open binary file and the item\n"
"layout the LASzip VLR declares -- `items` is a sequence of (type, size,\n"
"version) triples -- and reads nothing of the LAS header itself, which is\n"
"why it needs telling where the points start and how they are packed.\n"
"lazpy.Reader is the front end that parses a header and builds one of these;\n"
"use this only for a container the header does not describe.\n"
"\n"
"`compressor` and `coder` are the LASzip VLR's own fields (see\n"
"lazpy.Compressor and lazpy.Coder), `chunk_size` its chunk size with\n"
"0xFFFFFFFF meaning adaptive, and `start_offset` where to seek before\n"
"reading, or -1 to read from wherever the file is. `decompress_selective` is\n"
"a lazpy.Selective mask, which the layered LAS 1.4 formats alone honour.\n"
"`compatibility` describes a LAS 1.4 file disguised as a legacy one, and is\n"
"None for every ordinary file.\n");

PyTypeObject Reader_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.PointReader",
    .tp_basicsize = sizeof(ReaderObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = reader_doc,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Reader_tp_init,
    .tp_dealloc = (destructor)Reader_dealloc,
    .tp_methods = Reader_methods,
    .tp_getset = Reader_getset,
};
