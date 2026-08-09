/*
 * cpylazmodule.c -- Python bindings for the lazpy C core.
 *
 * Two layers are exposed:
 *
 *   - PointReader / Point: the actual reading API. Header and VLR parsing stay
 *     in Python (lazpy/__init__.py); everything from the first point onward is
 *     C.
 *
 *   - ArithmeticBitModel / ArithmeticModel / ArithmeticDecoder /
 *     ArithmeticEncoder / IntegerCompressor: thin wrappers over the entropy
 *     coder. These are not needed to read a file, but they let the test suite
 *     pin the coder against known bit-exact vectors and against the pure-Python
 *     reference in tests/models.py and tests/encoder.py, which is what catches
 *     a desync at its source rather than 3000 points into a chunk. They are
 *     also the only way the encoder is reachable at all until there is a
 *     writer above it.
 */
/* see laz_stream.c: required before Python.h for "#" formats below CPython 3.13 */
#define PY_SSIZE_T_CLEAN
#include "Python.h"
#include "structmember.h"

#include "laz_types.h"
#include "laz_stream.h"
#include "laz_arithmetic.h"
#include "laz_intcompressor.h"
#include "laz_readitem.h"
#include "laz_writeitem.h"
#include "laz_readpoint.h"

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
 * A view onto a decoded point.
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

/* A view onto memory owned by `reader`, valid until the reader detaches it. */
static PyObject *Point_borrow(LazPoint *p, U8 *extra, U32 num_extra)
{
    PointObject *o = PyObject_New(PointObject, &Point_Type);
    if (!o) return NULL;
    o->p = p;
    o->extra = extra;
    o->num_extra = num_extra;
    o->extra_storage = NULL;
    memset(&o->storage, 0, sizeof(o->storage));
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
    PointObject *o = PyObject_New(PointObject, &Point_Type);
    if (!o) return NULL;
    o->storage = *self->p;
    o->p = &o->storage;
    o->num_extra = self->num_extra;
    o->extra_storage = NULL;
    o->extra = NULL;
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
POINT_UGETTER(return_number, self->p->return_number)
POINT_UGETTER(number_of_returns, self->p->number_of_returns)
POINT_UGETTER(scan_direction_flag, self->p->scan_direction_flag)
POINT_UGETTER(edge_of_flight_line, self->p->edge_of_flight_line)
POINT_UGETTER(classification, self->p->classification)
POINT_UGETTER(synthetic_flag, self->p->synthetic_flag)
POINT_UGETTER(keypoint_flag, self->p->keypoint_flag)
POINT_UGETTER(withheld_flag, self->p->withheld_flag)
POINT_IGETTER(scan_angle_rank, self->p->scan_angle_rank)
POINT_UGETTER(user_data, self->p->user_data)
POINT_UGETTER(point_source_ID, self->p->point_source_ID)
POINT_IGETTER(extended_scan_angle, self->p->extended_scan_angle)
POINT_UGETTER(extended_point_type, self->p->extended_point_type)
POINT_UGETTER(extended_scanner_channel, self->p->extended_scanner_channel)
POINT_UGETTER(extended_classification_flags, self->p->extended_classification_flags)
POINT_UGETTER(extended_classification, self->p->extended_classification)
POINT_UGETTER(extended_return_number, self->p->extended_return_number)
POINT_UGETTER(extended_number_of_returns, self->p->extended_number_of_returns)

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

static PyGetSetDef Point_getset[] = {
    {"X", (getter)Point_get_X, NULL, "unscaled x", NULL},
    {"Y", (getter)Point_get_Y, NULL, "unscaled y", NULL},
    {"Z", (getter)Point_get_Z, NULL, "unscaled z", NULL},
    {"intensity", (getter)Point_get_intensity, NULL, NULL, NULL},
    {"return_number", (getter)Point_get_return_number, NULL, NULL, NULL},
    {"number_of_returns", (getter)Point_get_number_of_returns, NULL, NULL, NULL},
    {"scan_direction_flag", (getter)Point_get_scan_direction_flag, NULL, NULL, NULL},
    {"edge_of_flight_line", (getter)Point_get_edge_of_flight_line, NULL, NULL, NULL},
    {"classification", (getter)Point_get_classification, NULL, NULL, NULL},
    {"synthetic_flag", (getter)Point_get_synthetic_flag, NULL, NULL, NULL},
    {"keypoint_flag", (getter)Point_get_keypoint_flag, NULL, NULL, NULL},
    {"withheld_flag", (getter)Point_get_withheld_flag, NULL, NULL, NULL},
    {"scan_angle_rank", (getter)Point_get_scan_angle_rank, NULL, NULL, NULL},
    {"user_data", (getter)Point_get_user_data, NULL, NULL, NULL},
    {"point_source_ID", (getter)Point_get_point_source_ID, NULL, NULL, NULL},
    {"extended_scan_angle", (getter)Point_get_extended_scan_angle, NULL, NULL, NULL},
    {"extended_point_type", (getter)Point_get_extended_point_type, NULL, NULL, NULL},
    {"extended_scanner_channel", (getter)Point_get_extended_scanner_channel, NULL, NULL, NULL},
    {"extended_classification_flags", (getter)Point_get_extended_classification_flags, NULL, NULL, NULL},
    {"extended_classification", (getter)Point_get_extended_classification, NULL, NULL, NULL},
    {"extended_return_number", (getter)Point_get_extended_return_number, NULL, NULL, NULL},
    {"extended_number_of_returns", (getter)Point_get_extended_number_of_returns, NULL, NULL, NULL},
    {"gps_time", (getter)Point_get_gps_time, NULL, NULL, NULL},
    {"rgb", (getter)Point_get_rgb, NULL, "(red, green, blue, nir)", NULL},
    {"wave_packet", (getter)Point_get_wave_packet, NULL, NULL, NULL},
    {"extra_bytes", (getter)Point_get_extra_bytes, NULL, NULL, NULL},
    {NULL}
};

static PyMethodDef Point_methods[] = {
    {"copy", (PyCFunction)Point_copy, METH_NOARGS,
     "copy() -> Point  (detached from the reader's buffer)"},
    {NULL}
};

static PyObject *Point_repr(PointObject *self)
{
    return PyUnicode_FromFormat(
        "Point(X=%i, Y=%i, Z=%i, intensity=%u, return_number=%u, "
        "number_of_returns=%u, classification=%u)",
        self->p->X, self->p->Y, self->p->Z, (unsigned)self->p->intensity,
        (unsigned)self->p->return_number, (unsigned)self->p->number_of_returns,
        (unsigned)self->p->classification);
}

static PyTypeObject Point_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.Point",
    .tp_basicsize = sizeof(PointObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
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
    U32 num_extra_bytes;
    PyObject *point_view;
    BOOL ready;
    U64 index;              /* number of points read so far */
} ReaderObject;

static PyTypeObject Reader_Type;

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

static int Reader_tp_init(ReaderObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *fp, *items_obj;
    unsigned int compressor, coder = 0, chunk_size = 0;
    unsigned int selective = LAZ_DECOMPRESS_SELECTIVE_ALL;
    long long start_offset = -1;
    LazItem *items = NULL;
    U32 num_items = 0;
    static char *kwlist[] = {"fp", "items", "compressor", "coder", "chunk_size",
                             "start_offset", "decompress_selective", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOI|IILI", kwlist,
                                     &fp, &items_obj, &compressor, &coder,
                                     &chunk_size, &start_offset, &selective))
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
    ok = laz_readpoint_read(&self->rp, &self->point, self->extra_bytes);

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
 * The record layout must match lazdump.c and compare_with_laszip.py exactly.
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

        if (!laz_readpoint_read(&self->rp, p, self->extra_bytes)) { ok = LAZ_FALSE; break; }

        memcpy(rec + 0, &p->X, 4);
        memcpy(rec + 4, &p->Y, 4);
        memcpy(rec + 8, &p->Z, 4);
        memcpy(rec + 12, &p->intensity, 2);
        rec[14] = ((U8 *)p)[14];
        rec[15] = ((U8 *)p)[15];
        rec[16] = (U8)p->scan_angle_rank;
        rec[17] = p->user_data;
        memcpy(rec + 18, &p->point_source_ID, 2);
        memcpy(rec + 20, &p->gps_time, 8);
        memcpy(rec + 28, p->rgb, 8);
        memcpy(rec + 36, &p->extended_scan_angle, 2);
        rec[38] = ((U8 *)p)[22];
        rec[39] = p->extended_classification;
        rec[40] = ((U8 *)p)[24];
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

static PyObject *Reader_seek(ReaderObject *self, PyObject *args)
{
    unsigned long long target;
    BOOL ok;
    if (!PyArg_ParseTuple(args, "K", &target)) return NULL;

    Py_BEGIN_ALLOW_THREADS
    ok = laz_readpoint_seek(&self->rp, self->index, (U64)target);
    Py_END_ALLOW_THREADS

    if (!ok) return reader_error(self);
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
     "size of the extra-bytes buffer implied by the item layout", NULL},
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

/* ========================================================= chunk writer == */

/*
 * compress_chunks: run a sequence of point records through the item writers.
 *
 * This is the point-coding half of LASzip's container and nothing else -- each
 * chunk's first point raw, then the rest through the compressed writers -- with
 * the chunk table and the header left out. It exists so the item writers can be
 * pinned against real laszip output before the point writer exists, and the
 * point writer will subsume it. The chunks come back separately because their
 * boundaries are exactly what a caller wants to check.
 *
 * One set of writers spans every chunk, as the container's will: a writer's
 * init() has to put it back into the state a fresh one would be in, and a
 * layered writer that failed to rewind its layer buffers would only show up on
 * the second chunk.
 *
 * Records go in as they sit in the file. Each is scattered into a LazPoint by
 * the raw *readers* before any writer sees it, because an item coder is handed
 * a pointer into the point rather than into the record, and for the LAS 1.4
 * point types the two are not the same: POINT14's 30-byte record splits across
 * both the legacy and the extended fields of a point. For point formats 0-5
 * the scatter is the identity.
 *
 * Layered (v3/v4) chunks close differently from pointwise ones. There is no
 * single arithmetic stream to finish: instead the chunk's point count goes out,
 * then every writer's per-layer byte counts, then every writer's layer bytes.
 * The shared encoder is still init'd, because that is where the layered writers
 * find the output stream, but nothing is ever encoded through it.
 */
/* Writes one point through `w`, item by item, from the offsets it decoded to. */
static void write_record(LazWriteItem **w, U8 *const *src, U32 num_items)
{
    U32 i, context = 0;
    for (i = 0; i < num_items; i++) w[i]->write(w[i], src[i], &context);
}

static PyObject *cpylaz_compress_chunks(PyObject *self, PyObject *args)
{
    PyObject *item_seq, *record_seq, *records = NULL, *result = NULL;
    LazItem *items = NULL;
    LazWriteItem **raw = NULL, **compressed = NULL;
    LazReadItem **scatter = NULL;
    LazStream *in = NULL;
    LazPoint point;
    U8 *extra_bytes = NULL, *at[LAZ_MAX_ITEMS];
    U32 num_extra_bytes = 0;
    U32 num_items = 0, i;
    Py_ssize_t chunk_size = 0;
    Py_ssize_t record_size = 0, num_records, r, chunk_start = 0;
    LazOutStream *out = NULL;
    LazEncoder enc = {0};       /* the cleanup path frees it however we get there */
    BOOL have_enc, layered;

    (void)self;
    memset(&point, 0, sizeof(point));
    if (!PyArg_ParseTuple(args, "OO|n", &item_seq, &record_seq, &chunk_size))
        return NULL;
    if (parse_items(item_seq, &items, &num_items) < 0) return NULL;

    if (num_items > LAZ_MAX_ITEMS) {
        PyErr_Format(PyExc_ValueError, "too many items (%u, maximum %u)",
                     num_items, LAZ_MAX_ITEMS);
        goto done;
    }

    /* Items carry their own versions, exactly as the LASzip VLR declares them
     * -- WAVEPACKET13 stays at v1 inside a v2 file. What a chunk cannot mix is
     * compressed and uncompressed items, because one encoder covers them all,
     * nor layered and pointwise ones, because those close a chunk differently. */
    for (i = 0; i < num_items; i++) {
        if ((items[i].version == 0) != (items[0].version == 0) ||
            (items[i].version >= 3) != (items[0].version >= 3)) {
            PyErr_SetString(PyExc_ValueError,
                            "items must all be raw, all pointwise or all layered");
            goto done;
        }
        record_size += items[i].size;
    }
    have_enc = (items[0].version != 0);
    layered = (items[0].version >= 3);

    records = PySequence_Fast(record_seq, "records must be a sequence");
    if (!records) goto done;
    num_records = PySequence_Fast_GET_SIZE(records);

    raw = (LazWriteItem **)PyMem_Calloc(num_items, sizeof(LazWriteItem *));
    compressed = (LazWriteItem **)PyMem_Calloc(num_items, sizeof(LazWriteItem *));
    scatter = (LazReadItem **)PyMem_Calloc(num_items, sizeof(LazReadItem *));
    out = laz_outstream_new_array();
    in = laz_stream_new_array(NULL, 0);
    if (!raw || !compressed || !scatter || !out || !in ||
        (have_enc && !laz_encoder_setup(&enc))) {
        PyErr_NoMemory();
        goto done;
    }

    /* Built once and reused, as laz_readpoint_setup builds its readers. */
    for (i = 0; i < num_items; i++) {
        I32 offset = laz_item_offset(items[i].type);
        if (offset == -2) {
            PyErr_Format(PyExc_ValueError, "item type %u is not supported",
                         items[i].type);
            goto done;
        }
        if (offset == -1) num_extra_bytes += items[i].size;
        if (items[i].type == LAZ_ITEM_POINT14) point.extended_point_type = 1;

        scatter[i] = laz_readitem_new_raw(&items[i], in);
        raw[i] = laz_writeitem_new_raw(&items[i], out);
        if (!scatter[i] || !raw[i]) {
            PyErr_Format(PyExc_ValueError, "no raw coder for item type %u",
                         items[i].type);
            goto done;
        }
        if (have_enc) {
            compressed[i] = laz_writeitem_new_compressed(&items[i], &enc);
            if (!compressed[i]) {
                PyErr_Format(PyExc_ValueError, "no v%u writer for item type %u",
                             items[i].version, items[i].type);
                goto done;
            }
        }
    }

    extra_bytes = (U8 *)PyMem_Calloc(num_extra_bytes + 1, 1);
    if (!extra_bytes) { PyErr_NoMemory(); goto done; }
    for (i = 0; i < num_items; i++) {
        I32 offset = laz_item_offset(items[i].type);
        at[i] = (offset < 0) ? extra_bytes : (U8 *)&point + offset;
    }

    /* a chunk size of zero means one chunk holding every record, which is what
     * the non-chunked POINTWISE container does */
    if (chunk_size <= 0 || chunk_size > num_records) chunk_size = num_records;

    result = PyList_New(0);
    if (!result) goto done;

    for (chunk_start = 0; chunk_start < num_records; chunk_start += chunk_size) {
        Py_ssize_t chunk_count = num_records - chunk_start;
        PyObject *chunk;
        const U8 *bytes;
        I64 size;

        if (chunk_count > chunk_size) chunk_count = chunk_size;
        laz_outstream_array_rewind(out);

        for (r = 0; r < chunk_count; r++) {
            PyObject *rec = PySequence_Fast_GET_ITEM(records, chunk_start + r);
            char *data;
            Py_ssize_t len;
            U32 context = 0;

            if (PyBytes_AsStringAndSize(rec, &data, &len) < 0) goto fail;
            if (len != record_size) {
                PyErr_Format(PyExc_ValueError, "record %zd is %zd bytes, expected %zd",
                             chunk_start + r, len, record_size);
                goto fail;
            }

            laz_stream_array_reset(in, (const U8 *)data, len);
            for (i = 0; i < num_items; i++)
                scatter[i]->read(scatter[i], at[i], &context);

            context = 0;
            if (r == 0) {
                /* a chunk's first point is stored raw and seeds the predictors */
                write_record(raw, at, num_items);
                if (have_enc) {
                    for (i = 0; i < num_items; i++)
                        compressed[i]->init(compressed[i], at[i], &context);
                    /* pointwise: the stream the encoder emits into; layered:
                     * only how the writers reach the chunk's output */
                    laz_encoder_init(&enc, out);
                }
            } else {
                write_record(have_enc ? compressed : raw, at, num_items);
            }
        }

        if (have_enc) {
            if (layered) {
                laz_outstream_put32(out, (U32)chunk_count);
                for (i = 0; i < num_items; i++)
                    compressed[i]->chunk_sizes(compressed[i]);
                for (i = 0; i < num_items; i++)
                    compressed[i]->chunk_bytes(compressed[i]);
            } else {
                laz_encoder_done(&enc);
            }
        }
        if (out->failed) {
            /* the array sink only fails on allocation; a file sink would have
             * left the file object's own exception pending */
            if (!PyErr_Occurred()) PyErr_NoMemory();
            goto fail;
        }

        bytes = laz_outstream_array_data(out, &size);
        chunk = PyBytes_FromStringAndSize((const char *)bytes, (Py_ssize_t)size);
        if (!chunk) goto fail;
        if (PyList_Append(result, chunk) < 0) { Py_DECREF(chunk); goto fail; }
        Py_DECREF(chunk);
    }
    goto done;

fail:
    Py_CLEAR(result);

done:
    for (i = 0; i < num_items; i++) {
        if (raw) laz_writeitem_destroy(raw[i]);
        if (compressed) laz_writeitem_destroy(compressed[i]);
        if (scatter) laz_readitem_destroy(scatter[i]);
    }
    PyMem_Free(raw);
    PyMem_Free(compressed);
    PyMem_Free(scatter);
    PyMem_Free(extra_bytes);
    laz_encoder_free(&enc);
    laz_outstream_destroy(out);
    laz_stream_destroy(in);
    PyMem_Free(items);
    Py_XDECREF(records);
    return result;
}

/* ================================================================ module == */

static PyMethodDef cpylaz_methods[] = {
    {"compress_chunks", cpylaz_compress_chunks, METH_VARARGS,
     "compress_chunks(items, records, chunk_size=0) -> list[bytes]"},
    {NULL, NULL}
};

PyDoc_STRVAR(module_doc, "C backend for lazpy: LAZ entropy coding and point decoding.");

static int cpylaz_exec(PyObject *m)
{
    /* The point layout and host endianness are enforced at compile time by
     * static assertions in src/laz_types.h, next to the struct they constrain. */

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
