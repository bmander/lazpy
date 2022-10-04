#include "Python.h"

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

static PyTypeObject ArithmeticEncoder_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    "cencodermodule.ArithmeticEncoder", /*tp_name*/
    sizeof(ArithmeticEncoderObject), /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    /* methods */
    (destructor)0,    /*tp_dealloc*/
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