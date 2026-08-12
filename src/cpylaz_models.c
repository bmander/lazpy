/* The entropy-coder wrappers: ArithmeticBitModel, ArithmeticModel,
 * ArithmeticEncoder and ArithmeticDecoder. Kept in one file because the
 * four are entangled: the coders type-check the models in O! parses and
 * both build models through coder_create_symbol_model. */
#include "cpylaz.h"

/* ==================================================== ArithmeticBitModel == */

/*
 * A bit model is told nothing, so it can be a usable model from the moment it
 * exists rather than from __init__. That matters because __new__ without
 * __init__ is reachable from ordinary Python -- pickle and copy both take
 * that route -- and the zeroed model it used to hand back divided by its own
 * zero total on the first update.
 */
static PyObject *BitModel_tp_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    BitModelObject *self = PyObject_New(BitModelObject, type);
    (void)args; (void)kwds;
    if (self) laz_bit_model_init(&self->m);
    return (PyObject *)self;
}

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

PyTypeObject BitModel_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.ArithmeticBitModel",
    .tp_basicsize = sizeof(BitModelObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = BitModel_tp_new,
    .tp_init = (initproc)BitModel_tp_init,
    .tp_methods = BitModel_methods,
    .tp_getset = BitModel_getset,
    .tp_repr = (reprfunc)BitModel_repr,
    .tp_dealloc = (destructor)PyObject_Del,
};

/* ======================================================= ArithmeticModel == */

/*
 * Points the model at its own storage before anything else can, so that a
 * model made by __new__ alone -- pickle's route, and copy's -- is an empty
 * model rather than a NULL one. Every method here reaches through self->m to
 * decide whether it has been initialised, so a NULL there turned all ten of
 * them, the plain attributes included, into a segfault.
 *
 * The same shape as point_alloc, and for the same reason.
 */
static PyObject *SymbolModel_tp_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    SymbolModelObject *self = PyObject_New(SymbolModelObject, type);
    (void)args; (void)kwds;
    if (!self) return NULL;
    memset(&self->storage, 0, sizeof(self->storage));
    self->m = &self->storage;
    self->owner = NULL;
    return (PyObject *)self;
}

static int SymbolModel_tp_init(SymbolModelObject *self, PyObject *args, PyObject *kwds)
{
    unsigned int num_symbols;
    int compress;
    PyObject *compress_obj = NULL;
    static char *kwlist[] = {"num_symbols", "compress", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "I|O", kwlist, &num_symbols, &compress_obj))
        return -1;

    compress = compress_obj ? PyObject_IsTrue(compress_obj) : 0;
    if (compress < 0) return -1;

    /* __init__ can be called again on a live object, and setup zeroes the
     * struct over whatever the last one allocated. Only our own storage is
     * ours to free -- a borrowed model belongs to the reader that owns it,
     * and that reader is what owner keeps alive, so letting go of the model
     * means letting go of the reference too. */
    if (self->m == &self->storage) laz_symbol_model_free(self->m);
    Py_CLEAR(self->owner);

    self->m = &self->storage;
    laz_symbol_model_setup(self->m, num_symbols,
                           compress ? LAZ_TRUE : LAZ_FALSE);
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
PyObject *SymbolModel_borrow(LazSymbolModel *m, PyObject *owner)
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
            PyErr_SetString(PyExc_ValueError, "model not initialized");        \
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
PyObject *coder_create_symbol_model(PyObject *args, PyObject *compress)
{
    unsigned int num_symbols;
    if (!PyArg_ParseTuple(args, "I", &num_symbols)) return NULL;
    return PyObject_CallFunction((PyObject *)&SymbolModel_Type, "IO",
                                 num_symbols, compress);
}

PyTypeObject SymbolModel_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "lazpy._cpylaz.ArithmeticModel",
    .tp_basicsize = sizeof(SymbolModelObject),
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_new = SymbolModel_tp_new,
    .tp_init = (initproc)SymbolModel_tp_init,
    .tp_dealloc = (destructor)SymbolModel_dealloc,
    .tp_methods = SymbolModel_methods,
    .tp_getset = SymbolModel_getset,
};

/* ===================================================== ArithmeticEncoder == */

static int Encoder_tp_init(EncoderObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *fp;
    (void)kwds;
    if (!PyArg_ParseTuple(args, "O", &fp)) return -1;

    /* __init__ is callable again on a live object, and what the last one
     * built is ours to let go of first: otherwise a second call strands the
     * models, the sink's buffer and a reference to the old file. */
    laz_encoder_free(&self->e);
    if (self->stream) laz_outstream_destroy(self->stream);
    self->stream = NULL;
    Py_CLEAR(self->fp);

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
    /* No stream at all means __init__ never ran, which is what __new__ on its
     * own leaves behind; asking the stream how it is going comes after. */
    if (self->stream == NULL) {
        PyErr_SetString(PyExc_ValueError, "encoder has no file");
        return -1;
    }
    if (self->stream->failed) { encoder_error(); return -1; }
    if (self->e.stream == NULL) {
        PyErr_SetString(PyExc_ValueError, "encoder is not started");
        return -1;
    }
    return 0;
}

/* Returns None, or NULL with an exception set if the stream failed. */
PyObject *encoder_result(EncoderObject *self)
{
    if (self->stream->failed) return encoder_error();
    Py_RETURN_NONE;
}

static PyObject *Encoder_start(EncoderObject *self, PyObject *Py_UNUSED(i))
{
    /* the same question Decoder_start asks, rather than starting onto a NULL
     * stream and leaving encoder_ready to refuse the next call instead */
    if (self->stream == NULL) {
        PyErr_SetString(PyExc_ValueError, "encoder has no file");
        return NULL;
    }
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

/* None before __init__ has given it one, rather than a borrowed NULL. */
static PyObject *Encoder_get_fp(EncoderObject *self, void *c)
{ (void)c; Py_XINCREF(self->fp); return self->fp ? self->fp : Py_None; }

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

PyTypeObject Encoder_Type = {
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

static int Decoder_tp_init(DecoderObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *fp;
    (void)kwds;
    if (!PyArg_ParseTuple(args, "O", &fp)) return -1;

    /* as in Encoder_tp_init: let go of the last call's stream and file */
    if (self->stream) laz_stream_destroy(self->stream);
    self->stream = NULL;
    Py_CLEAR(self->fp);

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

/*
 * Whether the decoder was given a file, which __new__ without __init__ does
 * not. Decoding from one that was not dereferences NULL.
 *
 * Not the whole of encoder_ready: there is no "not started" case to check,
 * because laz_decoder_setup leaves the interval length at AC_MAX_LENGTH so
 * that decoding before start() runs out of stream rather than dividing by
 * zero. An encoder has no such resting state, which is why its guard asks
 * one more question than this one.
 */
static int decoder_ready(DecoderObject *self)
{
    if (self->stream == NULL) {
        PyErr_SetString(PyExc_ValueError, "decoder has no file");
        return -1;
    }
    return 0;
}

/*
 * Whether the file underneath is still answering, and the mirror of what
 * PointReader does with reader_stream_ok. The other half of the bracket:
 * decoder_ready asks before, this asks after, and a decode call needs both.
 *
 * The core decodes from a stream that cannot fail: past the end it hands back
 * zeros so a decode carries on rather than stopping. A file object whose
 * read() raised is not that -- the stream records it and leaves the exception
 * set for whoever holds the GIL. Without asking, every decode call here would
 * return a number worked out from zeros and leave the exception hanging for
 * some later, unrelated call to trip over.
 *
 * Takes the value the caller decoded so it can be handed straight back, or
 * released and replaced by NULL when the stream is the thing that failed.
 * Safe to dereference the stream, every caller having passed decoder_ready.
 */
static PyObject *decoder_result(DecoderObject *self, PyObject *value)
{
    if (!self->stream->failed) return value;
    Py_XDECREF(value);
    if (PyErr_Occurred()) return NULL;          /* the file object's own */
    PyErr_SetString(LazErrorType, "error reading from the underlying file");
    return NULL;
}

static PyObject *Decoder_start(DecoderObject *self, PyObject *Py_UNUSED(i))
{
    if (decoder_ready(self) < 0) return NULL;
    laz_decoder_init(&self->d, self->stream, LAZ_TRUE);
    return decoder_result(self, Py_NewRef(Py_None));
}

static PyObject *Decoder_decode_bit(DecoderObject *self, PyObject *args)
{
    PyObject *m;
    if (decoder_ready(self) < 0) return NULL;
    if (!PyArg_ParseTuple(args, "O!", &BitModel_Type, &m)) return NULL;
    return decoder_result(self, PyLong_FromUnsignedLong(
        laz_decode_bit(&self->d, &((BitModelObject *)m)->m)));
}

static PyObject *Decoder_decode_symbol(DecoderObject *self, PyObject *args)
{
    PyObject *m;
    LazSymbolModel *sm;
    if (decoder_ready(self) < 0) return NULL;
    if (!PyArg_ParseTuple(args, "O!", &SymbolModel_Type, &m)) return NULL;
    sm = ((SymbolModelObject *)m)->m;
    if (!sm->distribution) {
        PyErr_SetString(PyExc_ValueError, "model not initialized");
        return NULL;
    }
    return decoder_result(self,
                          PyLong_FromUnsignedLong(laz_decode_symbol(&self->d, sm)));
}

static PyObject *Decoder_read_bits(DecoderObject *self, PyObject *args)
{
    unsigned int bits;
    if (decoder_ready(self) < 0) return NULL;
    if (!PyArg_ParseTuple(args, "I", &bits)) return NULL;
    if (bits == 0 || bits > 32) {
        PyErr_SetString(PyExc_ValueError, "bits must be in 1..32");
        return NULL;
    }
    return decoder_result(self,
                          PyLong_FromUnsignedLong(laz_read_bits(&self->d, bits)));
}

static PyObject *Decoder_read_int(DecoderObject *self, PyObject *Py_UNUSED(i))
{
    if (decoder_ready(self) < 0) return NULL;
    return decoder_result(self,
                          PyLong_FromUnsignedLong(laz_read_int(&self->d)));
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
{ (void)c; Py_XINCREF(self->fp); return self->fp ? self->fp : Py_None; }

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

PyTypeObject Decoder_Type = {
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
