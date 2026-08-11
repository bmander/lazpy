/*
 * cpylazmodule.c -- Python bindings for the lazpy C core.
 *
 * Two layers are exposed:
 *
 *   - PointReader / PointWriter / Point: the actual reading and writing API.
 *     Header and VLR parsing stay in Python (lazpy/__init__.py); everything
 *     from the first point onward is C.
 *
 *   - ArithmeticBitModel / ArithmeticModel / ArithmeticDecoder /
 *     ArithmeticEncoder / IntegerCompressor: thin wrappers over the entropy
 *     coder. These are not needed to read a file, but they let the test suite
 *     pin the coder against known bit-exact vectors and against the pure-Python
 *     reference in tests/models.py and tests/encoder.py, which is what catches
 *     a desync at its source rather than 3000 points into a chunk.
 */
/* see laz_stream.c: required before Python.h for "#" formats below CPython 3.13 */
#define PY_SSIZE_T_CLEAN
#include "Python.h"
#include "structmember.h"

#include "laz_types.h"
#include "laz_stream.h"
#include "laz_arithmetic.h"
#include "laz_intcompressor.h"
#include "laz_readpoint.h"
#include "laz_writepoint.h"
#include "laz_index.h"

/*
 * The exception every decode failure raises. lazpy/__init__.py re-exports this
 * as LazError, so "this file failed to decode" is one catchable category
 * rather than a mix of RuntimeError, ValueError and OSError.
 */
static PyObject *LazErrorType = NULL;

/* ==================================================== ArithmeticBitModel == */

typedef struct {
    PyObject_HEAD
    LazBitModel m;
} BitModelObject;

static PyTypeObject BitModel_Type;

static int BitModel_tp_init(BitModelObject *self, PyObject *args, PyObject *kwds)
{
    (void)args; (void)kwds;
    laz_bit_model_init(&self->m);
    return 0;
}

static PyObject *BitModel_init(BitModelObject *self, PyObject *Py_UNUSED(ignored))
{
    laz_bit_model_init(&self->m);
    Py_RETURN_NONE;
}

static PyObject *BitModel_update(BitModelObject *self, PyObject *Py_UNUSED(ignored))
{
    laz_bit_model_update(&self->m);
    Py_RETURN_NONE;
}

#define BITMODEL_GETSET(field)                                                 \
    static PyObject *BitModel_get_##field(BitModelObject *self, void *c)       \
    { (void)c; return PyLong_FromUnsignedLong(self->m.field); }                \
    static int BitModel_set_##field(BitModelObject *self, PyObject *v, void *c)\
    {                                                                          \
        unsigned long x;                                                       \
        (void)c;                                                               \
        if (v == NULL) { PyErr_SetString(PyExc_AttributeError,                 \
            "cannot delete " #field); return -1; }                             \
        x = PyLong_AsUnsignedLong(v);                                          \
        if (PyErr_Occurred()) return -1;                                       \
        self->m.field = (U32)x;                                                \
        return 0;                                                              \
    }

BITMODEL_GETSET(bit_0_prob)
BITMODEL_GETSET(bit_0_count)
BITMODEL_GETSET(bit_count)
BITMODEL_GETSET(bits_until_update)
BITMODEL_GETSET(update_cycle)

static PyGetSetDef BitModel_getset[] = {
    {"bit_0_prob", (getter)BitModel_get_bit_0_prob, (setter)BitModel_set_bit_0_prob, NULL, NULL},
    {"bit_0_count", (getter)BitModel_get_bit_0_count, (setter)BitModel_set_bit_0_count, NULL, NULL},
    {"bit_count", (getter)BitModel_get_bit_count, (setter)BitModel_set_bit_count, NULL, NULL},
    {"bits_until_update", (getter)BitModel_get_bits_until_update, (setter)BitModel_set_bits_until_update, NULL, NULL},
    {"update_cycle", (getter)BitModel_get_update_cycle, (setter)BitModel_set_update_cycle, NULL, NULL},
    {NULL}
};

static PyMethodDef BitModel_methods[] = {
    {"init", (PyCFunction)BitModel_init, METH_NOARGS, "init() -> None"},
    {"update", (PyCFunction)BitModel_update, METH_NOARGS, "update() -> None"},
    {NULL}
};

static PyObject *BitModel_repr(BitModelObject *self)
{
    return PyUnicode_FromFormat(
        "ArithmeticBitModel(update_cycle=%u, bits_until_update=%u, "
        "bit_0_prob=%u, bit_0_count=%u, bit_count=%u)",
        self->m.update_cycle, self->m.bits_until_update,
        self->m.bit_0_prob, self->m.bit_0_count, self->m.bit_count);
}

static PyTypeObject BitModel_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.ArithmeticBitModel",
    .tp_basicsize = sizeof(BitModelObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)BitModel_tp_init,
    .tp_methods = BitModel_methods,
    .tp_getset = BitModel_getset,
    .tp_repr = (reprfunc)BitModel_repr,
    .tp_dealloc = (destructor)PyObject_Del,
};

/* ======================================================= ArithmeticModel == */

/*
 * Either owns its model (constructed from Python) or borrows one belonging to
 * an IntegerCompressor, in which case `owner` keeps that object alive.
 */
typedef struct {
    PyObject_HEAD
    LazSymbolModel *m;
    LazSymbolModel storage;
    PyObject *owner;
} SymbolModelObject;

static PyTypeObject SymbolModel_Type;

static int SymbolModel_tp_init(SymbolModelObject *self, PyObject *args, PyObject *kwds)
{
    unsigned int num_symbols;
    PyObject *compress_obj = NULL;
    static char *kwlist[] = {"num_symbols", "compress", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "I|O", kwlist, &num_symbols, &compress_obj))
        return -1;

    self->m = &self->storage;
    self->owner = NULL;
    laz_symbol_model_setup(self->m, num_symbols,
                           (compress_obj && PyObject_IsTrue(compress_obj)) ? LAZ_TRUE : LAZ_FALSE);
    return 0;
}

static void SymbolModel_dealloc(SymbolModelObject *self)
{
    if (self->owner) {
        Py_CLEAR(self->owner);          /* borrowed model, owner frees it */
    } else if (self->m) {
        laz_symbol_model_free(self->m);
    }
    PyObject_Del(self);
}

/* Wraps a model owned by `owner` without copying it. */
static PyObject *SymbolModel_borrow(LazSymbolModel *m, PyObject *owner)
{
    SymbolModelObject *o = PyObject_New(SymbolModelObject, &SymbolModel_Type);
    if (!o) return NULL;
    o->m = m;
    memset(&o->storage, 0, sizeof(o->storage));
    Py_INCREF(owner);
    o->owner = owner;
    return (PyObject *)o;
}

static PyObject *SymbolModel_init(SymbolModelObject *self, PyObject *args)
{
    PyObject *table = NULL;
    U32 *counts = NULL;
    BOOL ok;

    if (!PyArg_ParseTuple(args, "|O", &table)) return NULL;

    if (table != NULL && table != Py_None) {
        Py_ssize_t n, i;
        if (!PyList_Check(table)) {
            PyErr_SetString(PyExc_TypeError, "table must be a list of ints");
            return NULL;
        }
        n = PyList_Size(table);
        if ((U32)n != self->m->num_symbols) {
            PyErr_SetString(PyExc_ValueError,
                            "table must be the same length as num_symbols");
            return NULL;
        }
        counts = (U32 *)PyMem_Malloc((size_t)n * sizeof(U32));
        if (!counts) return PyErr_NoMemory();
        for (i = 0; i < n; i++) {
            PyObject *item = PyList_GetItem(table, i);
            unsigned long v;
            if (!PyLong_Check(item)) {
                PyMem_Free(counts);
                PyErr_SetString(PyExc_TypeError, "table must be a list of ints");
                return NULL;
            }
            v = PyLong_AsUnsignedLong(item);
            if (PyErr_Occurred()) { PyMem_Free(counts); return NULL; }
            counts[i] = (U32)v;
        }
    }

    ok = laz_symbol_model_init(self->m, counts);
    PyMem_Free(counts);
    if (!ok) {
        PyErr_SetString(PyExc_ValueError, "number of symbols must be between 2 and 2048");
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *SymbolModel_increment_symbol_count(SymbolModelObject *self, PyObject *args)
{
    unsigned int sym;
    if (!self->m->distribution) {
        PyErr_SetString(PyExc_ValueError, "model not initialized");
        return NULL;
    }
    if (!PyArg_ParseTuple(args, "I", &sym)) return NULL;
    if (sym >= self->m->num_symbols) {
        PyErr_SetString(PyExc_IndexError, "symbol out of range");
        return NULL;
    }
    ++self->m->symbol_count[sym];
    if (--self->m->symbols_until_update == 0) laz_symbol_model_update(self->m);
    Py_RETURN_NONE;
}

#define MODEL_LOOKUP(name, array, bound)                                       \
    static PyObject *SymbolModel_##name(SymbolModelObject *self, PyObject *args)\
    {                                                                          \
        unsigned int idx;                                                      \
        if (self->m->array == NULL) {                                          \
            PyErr_SetString(PyExc_Exception, "model not initialized");         \
            return NULL;                                                       \
        }                                                                      \
        if (!PyArg_ParseTuple(args, "I", &idx)) return NULL;                   \
        if (idx >= (bound)) {                                                  \
            PyErr_SetString(PyExc_IndexError, "index out of range");           \
            return NULL;                                                       \
        }                                                                      \
        return PyLong_FromUnsignedLong(self->m->array[idx]);                   \
    }

MODEL_LOOKUP(decoder_table_lookup, decoder_table, self->m->table_size + 2)
MODEL_LOOKUP(distribution_lookup, distribution, self->m->num_symbols)
MODEL_LOOKUP(symbol_count_lookup, symbol_count, self->m->num_symbols)

static PyObject *SymbolModel_has_decoder_table(SymbolModelObject *self, PyObject *Py_UNUSED(i))
{
    if (self->m->table_size == 0) Py_RETURN_FALSE;
    Py_RETURN_TRUE;
}

static PyObject *SymbolModel_get_num_symbols(SymbolModelObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->m->num_symbols); }

static PyObject *SymbolModel_get_compress(SymbolModelObject *self, void *c)
{ (void)c; if (self->m->compress) Py_RETURN_TRUE; Py_RETURN_FALSE; }

static PyObject *SymbolModel_get_table_shift(SymbolModelObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->m->table_shift); }

static PyObject *SymbolModel_get_last_symbol(SymbolModelObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->m->last_symbol); }

static PyMethodDef SymbolModel_methods[] = {
    {"init", (PyCFunction)SymbolModel_init, METH_VARARGS, "init(table=None) -> None"},
    {"increment_symbol_count", (PyCFunction)SymbolModel_increment_symbol_count, METH_VARARGS, NULL},
    {"decoder_table_lookup", (PyCFunction)SymbolModel_decoder_table_lookup, METH_VARARGS, NULL},
    {"distribution_lookup", (PyCFunction)SymbolModel_distribution_lookup, METH_VARARGS, NULL},
    {"symbol_count_lookup", (PyCFunction)SymbolModel_symbol_count_lookup, METH_VARARGS, NULL},
    {"has_decoder_table", (PyCFunction)SymbolModel_has_decoder_table, METH_NOARGS, NULL},
    {NULL}
};

static PyGetSetDef SymbolModel_getset[] = {
    {"num_symbols", (getter)SymbolModel_get_num_symbols, NULL, NULL, NULL},
    {"compress", (getter)SymbolModel_get_compress, NULL, NULL, NULL},
    {"table_shift", (getter)SymbolModel_get_table_shift, NULL, NULL, NULL},
    {"last_symbol", (getter)SymbolModel_get_last_symbol, NULL, NULL, NULL},
    {NULL}
};

/* Shared by ArithmeticDecoder.create_symbol_model and the encoder's. */
static PyObject *coder_create_symbol_model(PyObject *args, PyObject *compress)
{
    unsigned int num_symbols;
    if (!PyArg_ParseTuple(args, "I", &num_symbols)) return NULL;
    return PyObject_CallFunction((PyObject *)&SymbolModel_Type, "IO",
                                 num_symbols, compress);
}

static PyTypeObject SymbolModel_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.ArithmeticModel",
    .tp_basicsize = sizeof(SymbolModelObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)SymbolModel_tp_init,
    .tp_dealloc = (destructor)SymbolModel_dealloc,
    .tp_methods = SymbolModel_methods,
    .tp_getset = SymbolModel_getset,
};

/* ===================================================== ArithmeticEncoder == */

typedef struct {
    PyObject_HEAD
    LazEncoder e;
    LazOutStream *stream;
    PyObject *fp;
} EncoderObject;

static PyTypeObject Encoder_Type;

static int Encoder_tp_init(EncoderObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *fp;
    (void)kwds;
    if (!PyArg_ParseTuple(args, "O", &fp)) return -1;

    if (!laz_encoder_setup(&self->e)) { PyErr_NoMemory(); return -1; }
    self->stream = laz_outstream_new_file(fp);
    if (!self->stream) { laz_encoder_free(&self->e); PyErr_NoMemory(); return -1; }
    Py_INCREF(fp);
    self->fp = fp;
    return 0;
}

static void Encoder_dealloc(EncoderObject *self)
{
    laz_encoder_free(&self->e);
    if (self->stream) laz_outstream_destroy(self->stream);
    Py_XDECREF(self->fp);
    PyObject_Del(self);
}

/*
 * Raises for a failed write, preferring the exception the file object itself
 * raised. Shaped like reader_error: the first call after a failure propagates
 * the original, and later ones -- the caller having caught and cleared it --
 * still raise something rather than returning NULL with nothing set.
 */
static PyObject *encoder_error(void)
{
    if (PyErr_Occurred()) return NULL;            /* propagate the original */
    PyErr_SetString(LazErrorType, "error writing to the underlying file");
    return NULL;
}

/*
 * Every coding call is bracketed by these two.
 *
 * An encoder without a stream has either not been started or has already been
 * flushed by done(), and coding into it would dereference NULL.
 */
static int encoder_ready(EncoderObject *self)
{
    if (self->stream->failed) { encoder_error(); return -1; }
    if (self->e.stream == NULL) {
        PyErr_SetString(PyExc_ValueError, "encoder is not started");
        return -1;
    }
    return 0;
}

/* Returns None, or NULL with an exception set if the stream failed. */
static PyObject *encoder_result(EncoderObject *self)
{
    if (self->stream->failed) return encoder_error();
    Py_RETURN_NONE;
}

static PyObject *Encoder_start(EncoderObject *self, PyObject *Py_UNUSED(i))
{
    laz_encoder_init(&self->e, self->stream);
    Py_RETURN_NONE;
}

static PyObject *Encoder_done(EncoderObject *self, PyObject *Py_UNUSED(i))
{
    if (encoder_ready(self) < 0) return NULL;
    laz_encoder_done(&self->e);
    /* done() means the stream is complete, so the sink's buffer goes out with
     * it -- the caller's next move is to read back what was written */
    laz_outstream_flush(self->stream);
    return encoder_result(self);
}

static PyObject *Encoder_encode_bit(EncoderObject *self, PyObject *args)
{
    PyObject *m;
    unsigned int sym;
    if (!PyArg_ParseTuple(args, "O!I", &BitModel_Type, &m, &sym)) return NULL;
    if (sym > 1) {
        PyErr_SetString(PyExc_ValueError, "bit must be 0 or 1");
        return NULL;
    }
    if (encoder_ready(self) < 0) return NULL;
    laz_encode_bit(&self->e, &((BitModelObject *)m)->m, sym);
    return encoder_result(self);
}

static PyObject *Encoder_encode_symbol(EncoderObject *self, PyObject *args)
{
    PyObject *m;
    unsigned int sym;
    LazSymbolModel *sm;
    if (!PyArg_ParseTuple(args, "O!I", &SymbolModel_Type, &m, &sym)) return NULL;
    sm = ((SymbolModelObject *)m)->m;
    if (!sm->distribution) {
        PyErr_SetString(PyExc_ValueError, "model not initialized");
        return NULL;
    }
    if (sym >= sm->num_symbols) {
        PyErr_SetString(PyExc_IndexError, "symbol out of range");
        return NULL;
    }
    if (encoder_ready(self) < 0) return NULL;
    laz_encode_symbol(&self->e, sm, sym);
    return encoder_result(self);
}

static PyObject *Encoder_write_bits(EncoderObject *self, PyObject *args)
{
    unsigned int bits, sym;
    if (!PyArg_ParseTuple(args, "II", &bits, &sym)) return NULL;
    if (bits == 0 || bits > 32) {
        PyErr_SetString(PyExc_ValueError, "bits must be in 1..32");
        return NULL;
    }
    if (bits < 32 && sym >= (1u << bits)) {
        PyErr_SetString(PyExc_ValueError, "symbol does not fit in that many bits");
        return NULL;
    }
    if (encoder_ready(self) < 0) return NULL;
    laz_write_bits(&self->e, bits, sym);
    return encoder_result(self);
}

static PyObject *Encoder_write_int(EncoderObject *self, PyObject *args)
{
    unsigned int sym;
    if (!PyArg_ParseTuple(args, "I", &sym)) return NULL;
    if (encoder_ready(self) < 0) return NULL;
    laz_write_int(&self->e, sym);
    return encoder_result(self);
}

/* Compression models never build a decoder table; that is the only way in
 * which a model differs between the two directions. */
static PyObject *Encoder_create_symbol_model(EncoderObject *self, PyObject *args)
{
    (void)self;
    return coder_create_symbol_model(args, Py_True);
}

static PyObject *Encoder_repr(EncoderObject *self)
{
    return PyUnicode_FromFormat("ArithmeticEncoder(base=%u, length=%u)",
                                self->e.base, self->e.length);
}

static PyObject *Encoder_get_length(EncoderObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->e.length); }

static PyObject *Encoder_get_base(EncoderObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->e.base); }

static PyObject *Encoder_get_fp(EncoderObject *self, void *c)
{ (void)c; Py_INCREF(self->fp); return self->fp; }

static PyMethodDef Encoder_methods[] = {
    {"start", (PyCFunction)Encoder_start, METH_NOARGS, NULL},
    {"done", (PyCFunction)Encoder_done, METH_NOARGS, NULL},
    {"encode_bit", (PyCFunction)Encoder_encode_bit, METH_VARARGS, NULL},
    {"encode_symbol", (PyCFunction)Encoder_encode_symbol, METH_VARARGS, NULL},
    {"write_bits", (PyCFunction)Encoder_write_bits, METH_VARARGS, NULL},
    {"write_int", (PyCFunction)Encoder_write_int, METH_VARARGS, NULL},
    {"create_symbol_model", (PyCFunction)Encoder_create_symbol_model, METH_VARARGS, NULL},
    {NULL}
};

static PyGetSetDef Encoder_getset[] = {
    {"length", (getter)Encoder_get_length, NULL, NULL, NULL},
    {"base", (getter)Encoder_get_base, NULL, NULL, NULL},
    {"fp", (getter)Encoder_get_fp, NULL, NULL, NULL},
    {NULL}
};

static PyTypeObject Encoder_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.ArithmeticEncoder",
    .tp_basicsize = sizeof(EncoderObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Encoder_tp_init,
    .tp_dealloc = (destructor)Encoder_dealloc,
    .tp_methods = Encoder_methods,
    .tp_getset = Encoder_getset,
    .tp_repr = (reprfunc)Encoder_repr,
};

/* ===================================================== ArithmeticDecoder == */

typedef struct {
    PyObject_HEAD
    LazDecoder d;
    LazStream *stream;
    PyObject *fp;
} DecoderObject;

static PyTypeObject Decoder_Type;

static int Decoder_tp_init(DecoderObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *fp;
    (void)kwds;
    if (!PyArg_ParseTuple(args, "O", &fp)) return -1;

    self->stream = laz_stream_new_file(fp);
    if (!self->stream) { PyErr_NoMemory(); return -1; }
    Py_INCREF(fp);
    self->fp = fp;
    laz_decoder_setup(&self->d, self->stream);
    return 0;
}

static void Decoder_dealloc(DecoderObject *self)
{
    if (self->stream) laz_stream_destroy(self->stream);
    Py_XDECREF(self->fp);
    PyObject_Del(self);
}

static PyObject *Decoder_start(DecoderObject *self, PyObject *Py_UNUSED(i))
{
    laz_decoder_init(&self->d, self->stream, LAZ_TRUE);
    Py_RETURN_NONE;
}

static PyObject *Decoder_decode_bit(DecoderObject *self, PyObject *args)
{
    PyObject *m;
    if (!PyArg_ParseTuple(args, "O!", &BitModel_Type, &m)) return NULL;
    return PyLong_FromUnsignedLong(laz_decode_bit(&self->d, &((BitModelObject *)m)->m));
}

static PyObject *Decoder_decode_symbol(DecoderObject *self, PyObject *args)
{
    PyObject *m;
    LazSymbolModel *sm;
    if (!PyArg_ParseTuple(args, "O!", &SymbolModel_Type, &m)) return NULL;
    sm = ((SymbolModelObject *)m)->m;
    if (!sm->distribution) {
        PyErr_SetString(PyExc_ValueError, "model not initialized");
        return NULL;
    }
    return PyLong_FromUnsignedLong(laz_decode_symbol(&self->d, sm));
}

static PyObject *Decoder_read_bits(DecoderObject *self, PyObject *args)
{
    unsigned int bits;
    if (!PyArg_ParseTuple(args, "I", &bits)) return NULL;
    if (bits == 0 || bits > 32) {
        PyErr_SetString(PyExc_ValueError, "bits must be in 1..32");
        return NULL;
    }
    return PyLong_FromUnsignedLong(laz_read_bits(&self->d, bits));
}

static PyObject *Decoder_read_int(DecoderObject *self, PyObject *Py_UNUSED(i))
{
    return PyLong_FromUnsignedLong(laz_read_int(&self->d));
}

static PyObject *Decoder_create_symbol_model(DecoderObject *self, PyObject *args)
{
    (void)self;
    return coder_create_symbol_model(args, Py_False);
}

static PyObject *Decoder_repr(DecoderObject *self)
{
    return PyUnicode_FromFormat("ArithmeticDecoder(value=%u, length=%u)",
                                self->d.value, self->d.length);
}

static PyObject *Decoder_get_length(DecoderObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->d.length); }

static PyObject *Decoder_get_value(DecoderObject *self, void *c)
{ (void)c; return PyLong_FromUnsignedLong(self->d.value); }

static PyObject *Decoder_get_fp(DecoderObject *self, void *c)
{ (void)c; Py_INCREF(self->fp); return self->fp; }

static PyMethodDef Decoder_methods[] = {
    {"start", (PyCFunction)Decoder_start, METH_NOARGS, NULL},
    {"decode_bit", (PyCFunction)Decoder_decode_bit, METH_VARARGS, NULL},
    {"decode_symbol", (PyCFunction)Decoder_decode_symbol, METH_VARARGS, NULL},
    {"read_bits", (PyCFunction)Decoder_read_bits, METH_VARARGS, NULL},
    {"read_int", (PyCFunction)Decoder_read_int, METH_NOARGS, NULL},
    {"create_symbol_model", (PyCFunction)Decoder_create_symbol_model, METH_VARARGS, NULL},
    {NULL}
};

static PyGetSetDef Decoder_getset[] = {
    {"length", (getter)Decoder_get_length, NULL, NULL, NULL},
    {"value", (getter)Decoder_get_value, NULL, NULL, NULL},
    {"fp", (getter)Decoder_get_fp, NULL, NULL, NULL},
    {NULL}
};

static PyTypeObject Decoder_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.ArithmeticDecoder",
    .tp_basicsize = sizeof(DecoderObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Decoder_tp_init,
    .tp_dealloc = (destructor)Decoder_dealloc,
    .tp_methods = Decoder_methods,
    .tp_getset = Decoder_getset,
    .tp_repr = (reprfunc)Decoder_repr,
};

/* ==================================================== IntegerCompressor == */

/*
 * Codes in one direction only. `coder` keeps the decoder or encoder alive;
 * which one it is, is already recorded by the core, as ic.dec / ic.enc.
 */
typedef struct {
    PyObject_HEAD
    LazIntCompressor ic;
    PyObject *coder;
} IntCompObject;

static PyTypeObject IntComp_Type;

static int IntComp_tp_init(IntCompObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *dec_or_enc;
    unsigned int bits = 16, contexts = 1, bits_high = 8, range = 0;
    static char *kwlist[] = {"dec", "bits", "contexts", "bits_high", "range", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|IIII", kwlist,
                                     &dec_or_enc, &bits, &contexts, &bits_high, &range))
        return -1;

    if (PyObject_TypeCheck(dec_or_enc, &Decoder_Type)) {
        laz_ic_setup_dec(&self->ic, &((DecoderObject *)dec_or_enc)->d,
                         bits, contexts, bits_high, range);
    } else if (PyObject_TypeCheck(dec_or_enc, &Encoder_Type)) {
        laz_ic_setup_enc(&self->ic, &((EncoderObject *)dec_or_enc)->e,
                         bits, contexts, bits_high, range);
    } else {
        PyErr_SetString(PyExc_TypeError,
                        "first argument must be an ArithmeticDecoder or ArithmeticEncoder");
        return -1;
    }
    Py_INCREF(dec_or_enc);
    self->coder = dec_or_enc;
    return 0;
}

static void IntComp_dealloc(IntCompObject *self)
{
    laz_ic_free(&self->ic);
    Py_XDECREF(self->coder);
    PyObject_Del(self);
}

/* The direction is recorded by the core: exactly one of ic.dec/ic.enc is set. */
static BOOL intcomp_codes(IntCompObject *self, BOOL compress)
{
    return compress ? (self->ic.enc != NULL) : (self->ic.dec != NULL);
}

static int intcomp_check_direction(IntCompObject *self, BOOL compress)
{
    if (intcomp_codes(self, compress)) return 0;
    PyErr_SetString(PyExc_ValueError,
                    "this IntegerCompressor codes in the other direction");
    return -1;
}

/* Guards the direction and the argument range shared by (de)compress. */
static int intcomp_ready(IntCompObject *self, BOOL compress, U32 context)
{
    if (intcomp_check_direction(self, compress) < 0) return -1;
    if (!self->ic.models_created) {
        PyErr_Format(PyExc_ValueError, "call init_%scompressor() first",
                     compress ? "" : "de");
        return -1;
    }
    if (context >= self->ic.contexts) {
        PyErr_SetString(PyExc_IndexError, "context out of range");
        return -1;
    }
    return 0;
}

static PyObject *intcomp_init(IntCompObject *self, BOOL compress)
{
    if (intcomp_check_direction(self, compress) < 0) return NULL;
    if (!(compress ? laz_ic_init_compressor(&self->ic)
                   : laz_ic_init_decompressor(&self->ic))) return PyErr_NoMemory();
    Py_RETURN_NONE;
}

static PyObject *IntComp_init_decompressor(IntCompObject *self, PyObject *Py_UNUSED(i))
{
    return intcomp_init(self, LAZ_FALSE);
}

static PyObject *IntComp_init_compressor(IntCompObject *self, PyObject *Py_UNUSED(i))
{
    return intcomp_init(self, LAZ_TRUE);
}

static PyObject *IntComp_decompress(IntCompObject *self, PyObject *args)
{
    int pred;
    unsigned int context = 0;
    if (!PyArg_ParseTuple(args, "i|I", &pred, &context)) return NULL;
    if (intcomp_ready(self, LAZ_FALSE, context) < 0) return NULL;
    return PyLong_FromLong(laz_ic_decompress(&self->ic, pred, context));
}

static PyObject *IntComp_compress(IntCompObject *self, PyObject *args)
{
    int pred, real;
    unsigned int context = 0;
    if (!PyArg_ParseTuple(args, "ii|I", &pred, &real, &context)) return NULL;
    if (intcomp_ready(self, LAZ_TRUE, context) < 0) return NULL;
    laz_ic_compress(&self->ic, pred, real, context);
    return encoder_result((EncoderObject *)self->coder);
}

static PyObject *IntComp_get_m_bits(IntCompObject *self, PyObject *args)
{
    unsigned int idx;
    if (!PyArg_ParseTuple(args, "I", &idx)) return NULL;
    if (!self->ic.models_created || idx >= self->ic.contexts) {
        PyErr_SetString(PyExc_IndexError, "index out of range");
        return NULL;
    }
    return SymbolModel_borrow(&self->ic.m_bits[idx], (PyObject *)self);
}

/* Index 0 is the bit model; 1..corr_bits are symbol models. */
static PyObject *IntComp_get_corrector(IntCompObject *self, PyObject *args)
{
    unsigned int idx;
    if (!PyArg_ParseTuple(args, "I", &idx)) return NULL;
    if (!self->ic.models_created || idx > self->ic.corr_bits) {
        PyErr_SetString(PyExc_IndexError, "index out of range");
        return NULL;
    }
    if (idx == 0) {
        BitModelObject *o = PyObject_New(BitModelObject, &BitModel_Type);
        if (!o) return NULL;
        o->m = self->ic.m_corrector0;      /* snapshot: the bit model is small */
        return (PyObject *)o;
    }
    return SymbolModel_borrow(&self->ic.m_corrector[idx], (PyObject *)self);
}

#define INTCOMP_GETTER(name, expr)                                             \
    static PyObject *IntComp_get_##name(IntCompObject *self, void *c)          \
    { (void)c; return (expr); }

INTCOMP_GETTER(bits, PyLong_FromUnsignedLong(self->ic.bits))
INTCOMP_GETTER(contexts, PyLong_FromUnsignedLong(self->ic.contexts))
INTCOMP_GETTER(bits_high, PyLong_FromUnsignedLong(self->ic.bits_high))
INTCOMP_GETTER(range, PyLong_FromUnsignedLong(self->ic.range))
INTCOMP_GETTER(k, PyLong_FromUnsignedLong(self->ic.k))
INTCOMP_GETTER(corr_bits, PyLong_FromUnsignedLong(self->ic.corr_bits))

/* One of the two is always None: an IntegerCompressor codes in one direction. */
static PyObject *intcomp_coder_if(IntCompObject *self, BOOL compress)
{
    if (!intcomp_codes(self, compress)) Py_RETURN_NONE;
    Py_INCREF(self->coder);
    return self->coder;
}

static PyObject *IntComp_get_dec(IntCompObject *self, void *c)
{ (void)c; return intcomp_coder_if(self, LAZ_FALSE); }

static PyObject *IntComp_get_enc(IntCompObject *self, void *c)
{ (void)c; return intcomp_coder_if(self, LAZ_TRUE); }

static PyMethodDef IntComp_methods[] = {
    {"init_decompressor", (PyCFunction)IntComp_init_decompressor, METH_NOARGS, NULL},
    {"init_compressor", (PyCFunction)IntComp_init_compressor, METH_NOARGS, NULL},
    {"decompress", (PyCFunction)IntComp_decompress, METH_VARARGS, NULL},
    {"compress", (PyCFunction)IntComp_compress, METH_VARARGS, NULL},
    {"get_m_bits", (PyCFunction)IntComp_get_m_bits, METH_VARARGS, NULL},
    {"get_corrector", (PyCFunction)IntComp_get_corrector, METH_VARARGS, NULL},
    {NULL}
};

static PyGetSetDef IntComp_getset[] = {
    {"dec", (getter)IntComp_get_dec, NULL, NULL, NULL},
    {"enc", (getter)IntComp_get_enc, NULL, NULL, NULL},
    {"bits", (getter)IntComp_get_bits, NULL, NULL, NULL},
    {"contexts", (getter)IntComp_get_contexts, NULL, NULL, NULL},
    {"bits_high", (getter)IntComp_get_bits_high, NULL, NULL, NULL},
    {"range", (getter)IntComp_get_range, NULL, NULL, NULL},
    {"k", (getter)IntComp_get_k, NULL, NULL, NULL},
    {"corr_bits", (getter)IntComp_get_corr_bits, NULL, NULL, NULL},
    {NULL}
};

static PyTypeObject IntComp_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.IntegerCompressor",
    .tp_basicsize = sizeof(IntCompObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)IntComp_tp_init,
    .tp_dealloc = (destructor)IntComp_dealloc,
    .tp_methods = IntComp_methods,
    .tp_getset = IntComp_getset,
};

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
typedef struct {
    PyObject_HEAD
    LazPoint *p;            /* -> storage once detached or copied */
    LazPoint storage;
    U8 *extra;              /* -> extra_storage once detached or copied */
    U32 num_extra;
    U8 *extra_storage;      /* owned, may be NULL */
} PointObject;

static PyTypeObject Point_Type;

/* A zeroed point that owns itself. The three ways to get a Point -- built,
 * borrowed, copied -- all start here and then repoint or fill as they need. */
static PointObject *point_alloc(PyTypeObject *type)
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
static PyObject *Point_borrow(LazPoint *p, U8 *extra, U32 num_extra)
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
static void Point_detach(PointObject *self)
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
    POINT_GETSET(X, "unscaled x"),
    POINT_GETSET(Y, "unscaled y"),
    POINT_GETSET(Z, "unscaled z"),
    POINT_GETSET(intensity, NULL),
    POINT_GETSET(return_number, NULL),
    POINT_GETSET(number_of_returns, NULL),
    POINT_GETSET(scan_direction_flag, NULL),
    POINT_GETSET(edge_of_flight_line, NULL),
    POINT_GETSET(classification, NULL),
    POINT_GETSET(synthetic_flag, NULL),
    POINT_GETSET(keypoint_flag, NULL),
    POINT_GETSET(withheld_flag, NULL),
    POINT_GETSET(scan_angle_rank, NULL),
    POINT_GETSET(user_data, NULL),
    POINT_GETSET(point_source_ID, NULL),
    POINT_GETSET(extended_scan_angle, NULL),
    /* read-only: a writer stamps this from the layout it is writing, so
     * setting it here would decide nothing */
    {"extended_point_type", (getter)Point_get_extended_point_type, NULL,
     "1 on a point of format 6-10", NULL},
    POINT_GETSET(extended_scanner_channel, NULL),
    POINT_GETSET(extended_classification_flags,
                 "synthetic|keypoint|withheld|overlap; of these only overlap "
                 "reaches a LAS 1.4 record from here, the other three from "
                 "synthetic_flag, keypoint_flag and withheld_flag"),
    POINT_GETSET(extended_classification,
                 "the LAS 1.4 class; a record takes it only where the legacy "
                 "classification is 0"),
    POINT_GETSET(extended_return_number, NULL),
    POINT_GETSET(extended_number_of_returns, NULL),
    POINT_GETSET(gps_time, NULL),
    POINT_GETSET(rgb, "(red, green, blue, nir)"),
    POINT_GETSET(wave_packet, NULL),
    POINT_GETSET(extra_bytes, NULL),
#undef POINT_GETSET
    {NULL}
};

static PyMethodDef Point_methods[] = {
    {"copy", (PyCFunction)Point_copy, METH_NOARGS,
     "copy() -> Point  (detached from the reader's buffer)"},
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

static PyTypeObject Point_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.Point",
    .tp_basicsize = sizeof(PointObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = Point_tp_new,
    .tp_init = (initproc)Point_tp_init,
    .tp_dealloc = (destructor)Point_dealloc,
    .tp_getset = Point_getset,
    .tp_methods = Point_methods,
    .tp_repr = (reprfunc)Point_repr,
};

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
    I32 compat_starts[5];
    /* the quantized scan angle rank for every rank there is; see
     * reader_recode_compat, which would otherwise divide once per point */
    I16 scan_angle_of_rank[256];
    PyObject *point_view;
    BOOL ready;
    U64 index;              /* number of points read so far */
} ReaderObject;

static PyTypeObject Reader_Type;

/*
 * Where the hidden LAS 1.4 fields sit in compat_starts, in the order
 * lazpy/__init__.py's CompatibilityLayout hands them over, and how wide each
 * one is. One statement of that order, rather than one per use.
 */
enum {
    COMPAT_SCAN_ANGLE, COMPAT_EXTENDED_RETURNS, COMPAT_CLASSIFICATION,
    COMPAT_FLAGS_AND_CHANNEL, COMPAT_NIR, COMPAT_ATTRIBUTES
};
static const U32 compat_widths[COMPAT_ATTRIBUTES] = {2, 1, 1, 1, 2};

/* laszip's own expression for the scan angle a rank stands for, kept as it is
 * written in laszip_dll.cpp so the two can be compared; lifted out because
 * reader_recode_compat wants it tabulated rather than evaluated per point. */
#define COMPAT_SCAN_ANGLE_OF_RANK(rank) \
    I16_QUANTIZE(((F32)(rank)) / 0.006f)

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
static int parse_items(PyObject *seq, LazItem **out, U32 *out_n)
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
 * lazpy/__init__.py read them out of the "extra bytes" VLR, or None.
 *
 * Only start_NIR_band may be absent, as -1. The bytes a start addresses have
 * to be inside the extra bytes the item layout actually decodes, since the
 * recoding reads them for every point with no further checking.
 */
static int parse_compatibility(ReaderObject *self, PyObject *obj)
{
    I32 *starts = self->compat_starts;
    U32 decoded = self->rp.num_extra_bytes;
    int i;

    if (obj == NULL || obj == Py_None) return 0;
    if (!PyArg_ParseTuple(obj, "iiiii", &starts[0], &starts[1], &starts[2],
                          &starts[3], &starts[4]))
        return -1;

    for (i = 0; i < COMPAT_ATTRIBUTES; i++) {
        if (i == COMPAT_NIR && starts[i] < 0) continue;     /* no NIR band */
        /* by subtraction, so a start near U32_MAX cannot wrap past the end */
        if (starts[i] < 0 || compat_widths[i] > decoded ||
            (U32)starts[i] > decoded - compat_widths[i]) {
            PyErr_SetString(LazErrorType, "a LAS 1.4 compatibility attribute "
                            "lies outside the extra bytes");
            return -1;
        }
        /* everything from the first compatibility attribute on belongs to the
         * reconstituted point rather than to the caller's extra bytes */
        if ((U32)starts[i] < self->num_extra_bytes)
            self->num_extra_bytes = (U32)starts[i];
    }

    for (i = 0; i < 256; i++)
        self->scan_angle_of_rank[i] = COMPAT_SCAN_ANGLE_OF_RANK((I8)i);

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
static PyObject *Reader_read_into(ReaderObject *self, PyObject *args)
{
    PyObject *targets, *seq, *result = NULL;
    Py_ssize_t count;
    Py_ssize_t n, i, done = 0, held = 0;
    Column *cols = NULL;
    Py_buffer *views = NULL;
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

    seq = PySequence_Fast(targets, "targets must be a sequence");
    if (!seq) return NULL;
    n = PySequence_Fast_GET_SIZE(seq);

    cols = (Column *)PyMem_Malloc((size_t)n * sizeof(Column));
    views = (Py_buffer *)PyMem_Malloc((size_t)n * sizeof(Py_buffer));
    if (!cols || !views) { PyErr_NoMemory(); goto cleanup; }

    for (i = 0; i < n; i++) {
        PyObject *t = PySequence_Fast_GET_ITEM(seq, i);
        PyObject *buf;
        Py_ssize_t offset, size;

        if (!PyArg_ParseTuple(t, "Onn", &buf, &offset, &size)) goto cleanup;
        if (size <= 0) {
            PyErr_SetString(PyExc_ValueError, "field width must be positive");
            goto cleanup;
        }
        if (count > PY_SSIZE_T_MAX / size) {
            PyErr_SetString(PyExc_OverflowError, "count is too large");
            goto cleanup;
        }
        /* Both bounds are checked by subtraction rather than by adding
         * offset and size, which are the caller's and could overflow. */
        if (offset == -1) {
            if (size > (Py_ssize_t)self->num_extra_bytes) {
                PyErr_SetString(PyExc_ValueError,
                                "field lies outside the extra bytes");
                goto cleanup;
            }
            cols[i].src = self->extra_bytes;
        } else {
            if (offset < 0 || offset > POINT_FIXED_EXTENT - size) {
                PyErr_SetString(PyExc_ValueError,
                                "field lies outside the decoded point");
                goto cleanup;
            }
            cols[i].src = (const U8 *)&self->point + offset;
        }

        if (PyObject_GetBuffer(buf, &views[i],
                               PyBUF_WRITABLE | PyBUF_C_CONTIGUOUS) < 0)
            goto cleanup;
        held = i + 1;
        if (views[i].len < count * size) {
            PyErr_SetString(PyExc_ValueError, "output buffer is too small");
            goto cleanup;
        }
        cols[i].dst = (U8 *)views[i].buf;
        cols[i].size = size;
    }

    Py_BEGIN_ALLOW_THREADS
    for (done = 0; done < count; done++) {
        if (!reader_next(self)) {
            ok = LAZ_FALSE;
            break;
        }
        for (i = 0; i < n; i++) {
            memcpy(cols[i].dst, cols[i].src, (size_t)cols[i].size);
            cols[i].dst += cols[i].size;      /* on to this column's next */
        }
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

cleanup:
    for (i = 0; i < held; i++) PyBuffer_Release(&views[i]);
    PyMem_Free(views);
    PyMem_Free(cols);
    Py_DECREF(seq);
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
     "read() -> Point  (the reader's shared Point; call copy() to keep it)"},
    {"read_into", (PyCFunction)Reader_read_into, METH_VARARGS,
     "read_into(targets, count) -> None  (targets: (buffer, offset, size))"},
    {"seek", (PyCFunction)Reader_seek, METH_VARARGS, "seek(index) -> None"},
    {"checksum", (PyCFunction)Reader_checksum, METH_VARARGS,
     "checksum(count=-1) -> (fnv1a_hash, points_read)"},
    {NULL}
};

static PyGetSetDef Reader_getset[] = {
    {"point", (getter)Reader_get_point, NULL, NULL, NULL},
    {"index", (getter)Reader_get_index, NULL, NULL, NULL},
    {"chunk_starts", (getter)Reader_get_chunk_starts, NULL, NULL, NULL},
    {"num_extra_bytes", (getter)Reader_get_num_extra_bytes, NULL,
     "how many extra bytes a decoded point carries -- the item layout's, less "
     "any the LAS 1.4 compatibility attributes take up", NULL},
    {"warning", (getter)Reader_get_warning, NULL, NULL, NULL},
    {NULL}
};

static PyTypeObject Reader_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.PointReader",
    .tp_basicsize = sizeof(ReaderObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Reader_tp_init,
    .tp_dealloc = (destructor)Reader_dealloc,
    .tp_methods = Reader_methods,
    .tp_getset = Reader_getset,
};

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
     "write(point) -> None  (a Point, or the bytes its items occupy on disk)"},
    {"chunk", (PyCFunction)Writer_chunk, METH_NOARGS,
     "chunk() -> None  (close the open chunk; variable-size chunking only)"},
    {"done", (PyCFunction)Writer_done, METH_NOARGS,
     "done() -> None  (close the last chunk and write the chunk table)"},
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

static PyTypeObject Writer_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.PointWriter",
    .tp_basicsize = sizeof(WriterObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Writer_tp_init,
    .tp_dealloc = (destructor)Writer_dealloc,
    .tp_methods = Writer_methods,
    .tp_getset = Writer_getset,
};

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

static PyTypeObject Index_Type;

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

static PyObject *Index_intervals(IndexObject *self, PyObject *args)
{
    double min_x, min_y, max_x, max_y;
    PyObject *list;
    BOOL ok;
    U32 i;

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

static PyTypeObject Index_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.SpatialIndex",
    .tp_basicsize = sizeof(IndexObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = PyType_GenericNew,
    .tp_init = (initproc)Index_tp_init,
    .tp_dealloc = (destructor)Index_dealloc,
    .tp_methods = Index_methods,
    .tp_getset = Index_getset,
};

/* ================================================================ module == */

/* Test hook; see laz_alloc_fail_after in laz_arithmetic.h for what it is for. */
static PyObject *cpylaz_alloc_fail_after(PyObject *self, PyObject *arg)
{
    long long n = PyLong_AsLongLong(arg);
    (void)self;
    if (n == -1 && PyErr_Occurred()) return NULL;
    laz_alloc_fail_after((I64)n);
    Py_RETURN_NONE;
}

static PyMethodDef cpylaz_methods[] = {
    {"_alloc_fail_after", cpylaz_alloc_fail_after, METH_O,
     "Test hook: let the next n model allocations succeed and fail every one\n"
     "after that. -1 restores the default of never failing."},
    {NULL, NULL}
};

PyDoc_STRVAR(module_doc, "C backend for lazpy: LAZ entropy coding and point decoding.");

static int cpylaz_exec(PyObject *m)
{
    /* The point layout is enforced at compile time by static assertions in
     * src/laz_types.h, next to the struct they constrain. Byte order cannot be
     * settled there -- either order decodes the same file to the same values,
     * so what matters is not which one this host is but whether the build
     * guessed it right. Wrong, and every point mis-decodes silently. */
    if (!laz_host_order_ok()) {
        PyErr_SetString(PyExc_RuntimeError,
                        "lazpy was built for the wrong host byte order");
        return -1;
    }

#define ADD_TYPE(var, name)                                                    \
    do {                                                                       \
        if (PyType_Ready(&var) < 0) return -1;                                 \
        Py_INCREF(&var);                                                       \
        if (PyModule_AddObject(m, name, (PyObject *)&var) < 0) {               \
            Py_DECREF(&var);                                                   \
            return -1;                                                         \
        }                                                                      \
    } while (0)

    ADD_TYPE(BitModel_Type, "ArithmeticBitModel");
    ADD_TYPE(SymbolModel_Type, "ArithmeticModel");
    ADD_TYPE(Encoder_Type, "ArithmeticEncoder");
    ADD_TYPE(Decoder_Type, "ArithmeticDecoder");
    ADD_TYPE(IntComp_Type, "IntegerCompressor");
    ADD_TYPE(Point_Type, "Point");
    ADD_TYPE(Reader_Type, "PointReader");
    ADD_TYPE(Writer_Type, "PointWriter");
    ADD_TYPE(Index_Type, "SpatialIndex");
#undef ADD_TYPE

    LazErrorType = PyErr_NewExceptionWithDoc(
        "lazpy.LazError", "A LAS/LAZ file could not be read or decoded.",
        NULL, NULL);
    if (LazErrorType == NULL) return -1;
    Py_INCREF(LazErrorType);
    if (PyModule_AddObject(m, "LazError", LazErrorType) < 0) {
        Py_DECREF(LazErrorType);
        return -1;
    }

    PyModule_AddIntConstant(m, "DM_LENGTH_SHIFT", DM_LENGTH_SHIFT);
    PyModule_AddIntConstant(m, "BM_LENGTH_SHIFT", BM_LENGTH_SHIFT);
    PyModule_AddIntConstant(m, "DECOMPRESS_SELECTIVE_ALL", (long)LAZ_DECOMPRESS_SELECTIVE_ALL);
    return 0;
}

static struct PyModuleDef_Slot cpylaz_slots[] = {
    {Py_mod_exec, (void *)cpylaz_exec},
    {0, NULL},
};

static struct PyModuleDef cpylazmodule = {
    PyModuleDef_HEAD_INIT,
    "lazpy._cpylaz",
    module_doc,
    0,
    cpylaz_methods,
    cpylaz_slots,
    NULL, NULL, NULL
};

PyMODINIT_FUNC PyInit__cpylaz(void)
{
    return PyModuleDef_Init(&cpylazmodule);
}
