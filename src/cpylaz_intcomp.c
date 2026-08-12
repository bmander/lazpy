/* IntegerCompressor: the staged integer-prediction coder over the models. */
#include "cpylaz.h"

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

static int IntComp_tp_init(IntCompObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *dec_or_enc;
    unsigned int bits = 16, contexts = 1, bits_high = 8, range = 0;
    static char *kwlist[] = {"dec", "bits", "contexts", "bits_high", "range", NULL};

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "O|IIII", kwlist,
                                     &dec_or_enc, &bits, &contexts, &bits_high, &range))
        return -1;

    /*
     * The coder has to have a file as well as the right type. Everything
     * below reaches into its struct and codes through it without going near
     * the guards on the coder's own methods, so an unstarted one taken here
     * is a crash later rather than a refusal now: the decoder divides by an
     * interval length of zero, and the encoder writes through the output
     * byte laz_encoder_setup never allocated.
     *
     * Which is the same thing __new__ without __init__ leaves behind. One
     * object on its own is safe now; this is the pair.
     */
    if (PyObject_TypeCheck(dec_or_enc, &Decoder_Type)) {
        if (((DecoderObject *)dec_or_enc)->stream == NULL) {
            PyErr_SetString(PyExc_ValueError, "decoder has no file");
            return -1;
        }
        laz_ic_setup_dec(&self->ic, &((DecoderObject *)dec_or_enc)->d,
                         bits, contexts, bits_high, range);
    } else if (PyObject_TypeCheck(dec_or_enc, &Encoder_Type)) {
        if (((EncoderObject *)dec_or_enc)->stream == NULL) {
            PyErr_SetString(PyExc_ValueError, "encoder has no file");
            return -1;
        }
        laz_ic_setup_enc(&self->ic, &((EncoderObject *)dec_or_enc)->e,
                         bits, contexts, bits_high, range);
    } else {
        PyErr_SetString(PyExc_TypeError,
                        "first argument must be an ArithmeticDecoder or ArithmeticEncoder");
        return -1;
    }
    /* as the coders do: __init__ again lets go of the last call's coder,
     * and laz_ic_setup_* has already zeroed whatever init_*compressor
     * allocated over it */
    Py_INCREF(dec_or_enc);
    Py_XSETREF(self->coder, dec_or_enc);
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

PyTypeObject IntComp_Type = {
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
