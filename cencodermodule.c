#include "Python.h"
#include "cmodelsmodule.h"

#define AC_MAX_LENGTH 0xFFFFFFFF
#define AC_MIN_LENGTH 0x01000000

typedef struct {
    PyObject_HEAD
    /* Type-specific fields go here. */
} ArithmeticEncoderObject;

static PyObject *
ArithmeticEncoder_new(PyTypeObject *type, PyObject *args, PyObject *kwds)
{
    PyErr_SetString(PyExc_NotImplementedError, "Not implemented");
    return NULL;
}

static void
ArithmeticEncoder_dealloc(ArithmeticEncoderObject *self)
{
    PyObject_Del(self);
}

static PyTypeObject ArithmeticEncoder_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    "cencodermodule.ArithmeticEncoder", /*tp_name*/
    sizeof(ArithmeticEncoderObject), /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    /* methods */
    (destructor)ArithmeticEncoder_dealloc,    /*tp_dealloc*/
    0,                          /*tp_vectorcall_offset*/
    (getattrfunc)0,             /*tp_getattr*/
    0,   /*tp_setattr*/
    0,                          /*tp_as_async*/
    0,                          /*tp_repr*/
    0,                          /*tp_as_number*/
    0,                          /*tp_as_sequence*/
    0,                          /*tp_as_mapping*/
    0,                          /*tp_hash*/
    0,                          /*tp_call*/
    0,                          /*tp_str*/
    0, /*tp_getattro*/
    0,                          /*tp_setattro*/
    0,                          /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT,         /*tp_flags*/
    0,                          /*tp_doc*/
    0,                          /*tp_traverse*/
    0,                          /*tp_clear*/
    0,                          /*tp_richcompare*/
    0,                          /*tp_weaklistoffset*/
    0,                          /*tp_iter*/
    0,                          /*tp_iternext*/
    0,                /*tp_methods*/
    0,                          /*tp_members*/
    0,                          /*tp_getset*/
    0,                          /*tp_base*/
    0,                          /*tp_dict*/
    0,                          /*tp_descr_get*/
    0,                          /*tp_descr_set*/
    0,                          /*tp_dictoffset*/
    0,                          /*tp_init*/
    0,                          /*tp_alloc*/
    ArithmeticEncoder_new,                          /*tp_new*/
    0,                          /*tp_free*/
    0,                          /*tp_is_gc*/
};

typedef struct {
    PyObject_HEAD
    uint32_t length;
    uint32_t value;
    PyObject *fp;
} ArithmeticDecoderObject;

PyObject *
getBytesFromPythonFileLikeObject(PyObject *fp, uint32_t length) {
    PyObject *read = PyObject_GetAttrString(fp, "read");
    PyObject *readArgs = PyTuple_New(1);
    PyTuple_SetItem(readArgs, 0, PyLong_FromLong(length));
    PyObject *read_result = PyObject_CallObject(read, readArgs);
    Py_DECREF(read);
    Py_DECREF(readArgs);

    return read_result;
}

static int
ArithmeticDecoder_init(ArithmeticDecoderObject *self, PyObject *args, PyObject *kwds)
{
    PyObject *fp;
    if (!PyArg_ParseTuple(args, "O", &fp)) {
        return -1;
    }
    Py_INCREF(fp);
    self->fp = fp;
    self->length = 0;
    self->value = 0;
    return 0;
}

static void
ArithmeticDecoder_dealloc(ArithmeticDecoderObject *self)
{
    Py_XDECREF(self->fp);
    PyObject_Del(self);
}

static PyObject *
ArithmeticDecoder_start(ArithmeticDecoderObject *self, PyObject *args)
{
    PyObject *read_result = getBytesFromPythonFileLikeObject(self->fp, 4);
    void *bytes = PyBytes_AsString(read_result);

    self->value = *((uint32_t *)bytes);
    self->length = AC_MAX_LENGTH;

    Py_DECREF(read_result);

    Py_RETURN_NONE;
}

void
ArithmeticDecoder__renorm_dec_interval(ArithmeticDecoderObject *self) {
    if (self->length < AC_MIN_LENGTH) {
        PyObject *read_result = getBytesFromPythonFileLikeObject(self->fp, 1);
        void *bytes = PyBytes_AsString(read_result);

        self->value = (self->value << 8) | *((uint8_t *)bytes);
        self->length <<= 8;

        Py_DECREF(read_result);
    }
}

static PyObject *
ArithmeticDecoder_decode_bit(ArithmeticDecoderObject *self, PyObject *args)
{
    // get ArithmeticBitModel from args
    PyObject *argm;
    if (!PyArg_ParseTuple(args, "O!", &ArithmeticBitModel_Type, &argm)) {
        return NULL;
    }
    ArithmeticBitModelObject *m = (ArithmeticBitModelObject *)argm;

    uint32_t x = m->bit_0_prob * (self->length >> BM_LENGTH_SHIFT);
    uint32_t sym = (self->value >= x);

    if (sym==0){
        self->length = x;
        m->bit_0_count++;
    } else {
        self->value -= x;
        self->length -= x;
    }

    if(self->length < AC_MIN_LENGTH){
        ArithmeticDecoder__renorm_dec_interval(self);
    }

    m->bits_until_update--;
    if (m->bits_until_update == 0) { 
        updateArithmeticBitModel(m);
    }

    return PyLong_FromUnsignedLong(sym);
    
}


static PyObject *
ArithmeticDecoder_length(ArithmeticDecoderObject *self, PyObject *args)
{
    return PyLong_FromLong(self->length);
}

static PyObject *
ArithmeticDecoder_value(ArithmeticDecoderObject *self, PyObject *args)
{
    return PyLong_FromLong(self->value);
}

static PyMethodDef ArithmeticDecoder_methods[] = {
    {"start", (PyCFunction)ArithmeticDecoder_start, METH_VARARGS, "Start decoding"},
    {"decode_bit", (PyCFunction)ArithmeticDecoder_decode_bit, METH_VARARGS, "Decode a bit"},
    {NULL, NULL}  /* Sentinel */
};

PyGetSetDef ArithmeticDecoder_getset[] = {
    {"length", (getter)ArithmeticDecoder_length, NULL, "length", NULL},
    {"value", (getter)ArithmeticDecoder_value, NULL, "value", NULL},
    {NULL}  /* Sentinel */
};

static PyTypeObject ArithmeticDecoder_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    "cencodermodule.ArithmeticDecoder", /*tp_name*/
    sizeof(ArithmeticDecoderObject), /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    /* methods */
    (destructor)ArithmeticDecoder_dealloc,    /*tp_dealloc*/
    0,                          /*tp_vectorcall_offset*/
    (getattrfunc)0,             /*tp_getattr*/
    0,   /*tp_setattr*/
    0,                          /*tp_as_async*/
    0,                          /*tp_repr*/
    0,                          /*tp_as_number*/
    0,                          /*tp_as_sequence*/
    0,                          /*tp_as_mapping*/
    0,                          /*tp_hash*/
    0,                          /*tp_call*/
    0,                          /*tp_str*/
    0, /*tp_getattro*/
    0,                          /*tp_setattro*/
    0,                          /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT,         /*tp_flags*/
    0,                          /*tp_doc*/
    0,                          /*tp_traverse*/
    0,                          /*tp_clear*/
    0,                          /*tp_richcompare*/
    0,                          /*tp_weaklistoffset*/
    0,                          /*tp_iter*/
    0,                          /*tp_iternext*/
    ArithmeticDecoder_methods,                /*tp_methods*/
    0,                          /*tp_members*/
    ArithmeticDecoder_getset,                          /*tp_getset*/
    0,                          /*tp_base*/
    0,                          /*tp_dict*/
    0,                          /*tp_descr_get*/
    0,                          /*tp_descr_set*/
    0,                          /*tp_dictoffset*/
    (initproc)ArithmeticDecoder_init,                          /*tp_init*/
    0,                          /*tp_alloc*/
    PyType_GenericNew,                          /*tp_new*/
    0,                          /*tp_free*/
    0,                          /*tp_is_gc*/
};


static PyMethodDef cencoder_methods[] = {
    {NULL,              NULL}           /* sentinel */
};


PyDoc_STRVAR(module_doc,
"C implementation of encoder.");

static int
cencoder_exec(PyObject *m)
{
    /* Slot initialization is subject to the rules of initializing globals.
       C99 requires the initializers to be "address constants".  Function
       designators like 'PyType_GenericNew', with implicit conversion to
       a pointer, are valid C99 address constants.

       However, the unary '&' operator applied to a non-static variable
       like 'PyBaseObject_Type' is not required to produce an address
       constant.  Compilers may support this (gcc does), MSVC does not.

       Both compilers are strictly standard conforming in this particular
       behavior.
    */
    ArithmeticEncoder_Type.tp_base = &PyBaseObject_Type;
    if (PyType_Ready(&ArithmeticEncoder_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "ArithmeticEncoder", (PyObject *)&ArithmeticEncoder_Type);

    ArithmeticDecoder_Type.tp_base = &PyBaseObject_Type;
    if (PyType_Ready(&ArithmeticDecoder_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "ArithmeticDecoder", (PyObject *)&ArithmeticDecoder_Type);

    return 0;
 fail:
    Py_XDECREF(m);
    return -1;
}

static struct PyModuleDef_Slot cencoder_slots[] = {
    {Py_mod_exec, cencoder_exec},
    {0, NULL},
};

static struct PyModuleDef cencodermodule = {
    PyModuleDef_HEAD_INIT,
    "cencoder",
    module_doc,
    0,
    cencoder_methods,
    cencoder_slots,
    NULL,
    NULL,
    NULL
};

/* Export function for the module (*must* be called PyInit_cmodels) */

PyMODINIT_FUNC
PyInit_cencoder(void)
{
    return PyModuleDef_Init(&cencodermodule);
}