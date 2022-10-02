
/* Use this file as a template to start implementing a module that
   also declares object types. All occurrences of 'ArithmeticBitModel' should be changed
   to something reasonable for your objects. After that, all other
   occurrences of 'cmodels' should be changed to something reasonable for your
   module. If your module is named foo your sourcefile should be named
   foomodule.c.

   You will probably want to delete all references to 'x_attr' and add
   your own types of attributes instead.  Maybe you want to name your
   local variables other than 'self'.  If your object type is needed in
   other files, you'll have to create a file "foobarobject.h"; see
   floatobject.h for an example. */

/* ArithmeticBitModel objects */

#include "Python.h"


#define BM_LENGTH_SHIFT 13
#define BM_MAX_COUNT (1 << BM_LENGTH_SHIFT)
#define MIN(a,b) ((a) < (b) ? (a) : (b))

typedef struct {
    PyObject_HEAD
    uint32_t bit_0_prob;
    uint32_t bit_0_count;
    uint32_t bit_count;
    uint32_t update_cycle;
    uint32_t bits_until_update;
} ArithmeticBitModelObject;

static PyTypeObject ArithmeticBitModel_Type;

#define ArithmeticBitModelObject_Check(v)      (Py_TYPE(v) == &ArithmeticBitModel_Type)

static void
updateArithmeticBitModel(ArithmeticBitModelObject *self) {
    // halve counts when threshold is reached
    self->bit_count += self->update_cycle;
    if(self->bit_count >= BM_MAX_COUNT) {
        self->bit_count = (self->bit_count + 1) >> 1;
        self->bit_0_count = (self->bit_0_count + 1) >> 1;
        if(self->bit_0_count == self->bit_count) {
            self->bit_count += 1;
        }
    }

    // compute scaled bit 0 probability
    uint32_t scale = 0x80000000 / self->bit_count;
    self->bit_0_prob = (self->bit_0_count * scale) >> (31 - BM_LENGTH_SHIFT);

    // update frequency of model updates
    self->update_cycle = (5 * self->update_cycle) >> 2;
    self->update_cycle = MIN(self->update_cycle, 64);
    self->bits_until_update = self->update_cycle;
}

/* ArithmeticBitModel methods */

static void
ArithmeticBitModel_dealloc(ArithmeticBitModelObject *self)
{
    PyObject_Del(self);
}

static PyObject *
ArithmeticBitModel_init(ArithmeticBitModelObject *self, PyObject *args)
{
    // initialize equiprobable model
    self->bit_0_count = 1;
    self->bit_count = 2;
    self->bit_0_prob = 1 << (BM_LENGTH_SHIFT - 1);

    // start with frequent updates
    self->update_cycle = self->bits_until_update = 4;

    Py_INCREF(Py_None);
    return Py_None;
}

static PyObject *
ArithmeticBitModel_update(ArithmeticBitModelObject *self, PyObject *args)
{
    updateArithmeticBitModel(self);

    Py_INCREF(Py_None);
    return Py_None;
}

static PyObject *
ArithmeticBitModel_get_bit_0_prob(ArithmeticBitModelObject *self, PyObject *args)
{
    return PyLong_FromUnsignedLong(self->bit_0_prob);
}

static PyObject *
ArithmeticBitModel_set_bit_0_prob(ArithmeticBitModelObject *self, PyObject *value, void *closure)
{
    if (!PyLong_Check(value)) {
        PyErr_SetString(PyExc_TypeError,
                        "The bit_0_prob attribute value must be an integer");
        return NULL;
    }
    self->bit_0_prob = PyLong_AsUnsignedLong(value);

    return 0;
}

static PyObject *
ArithmeticBitModel_get_bit_0_count(ArithmeticBitModelObject *self, void *closure)
{
    return PyLong_FromUnsignedLong(self->bit_0_count);
}

static PyObject *
ArithmeticBitModel_set_bit_0_count(ArithmeticBitModelObject *self, PyObject *value, void *closure)
{
    if (!PyLong_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "The bit_0_count attribute value must be an integer");
        return NULL;
    }
    self->bit_0_count = PyLong_AsUnsignedLong(value);
    return 0;
}

static PyObject *
ArithmeticBitModel_get_bits_until_update(ArithmeticBitModelObject *self, void *closure)
{
    return PyLong_FromUnsignedLong(self->bits_until_update);
}

static PyObject *
ArithmeticBitModel_set_bits_until_update(ArithmeticBitModelObject *self, PyObject *value, void *closure)
{
    if (!PyLong_Check(value)) {
        PyErr_SetString(PyExc_TypeError, "The bits_until_update attribute value must be an integer");
        return NULL;
    }
    self->bits_until_update = PyLong_AsUnsignedLong(value);
    return 0;
}


static PyMethodDef ArithmeticBitModel_methods[] = {
    {"init",            (PyCFunction)ArithmeticBitModel_init,  METH_NOARGS,
        PyDoc_STR("init() -> None")},
    {"update",          (PyCFunction)ArithmeticBitModel_update,  METH_NOARGS,
        PyDoc_STR("update() -> None")},
    {NULL,              NULL}           /* sentinel */
};

PyGetSetDef ArithmeticBitModel_getset[] = {
    {"bit_0_prob", /* name */
     (getter)ArithmeticBitModel_get_bit_0_prob, /* getter */
     (setter)ArithmeticBitModel_set_bit_0_prob, /* setter */
     NULL, /* doc */
     NULL}, /* closure */
    {"bit_0_count",
     (getter) ArithmeticBitModel_get_bit_0_count,
     (setter) ArithmeticBitModel_set_bit_0_count,
     NULL,
     NULL},
    {"bits_until_update",
     (getter) ArithmeticBitModel_get_bits_until_update,
     (setter) ArithmeticBitModel_set_bits_until_update,
     NULL,
     NULL},
    {NULL}
};


static PyTypeObject ArithmeticBitModel_Type = {
    /* The ob_type field must be initialized in the module init function
     * to be portable to Windows without using C++. */
    PyVarObject_HEAD_INIT(NULL, 0)
    "cmodelsmodule.ArithmeticBitModel",             /*tp_name*/
    sizeof(ArithmeticBitModelObject),          /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    /* methods */
    (destructor)ArithmeticBitModel_dealloc,    /*tp_dealloc*/
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
    ArithmeticBitModel_methods,                /*tp_methods*/
    0,                          /*tp_members*/
    ArithmeticBitModel_getset,                          /*tp_getset*/
    0,                          /*tp_base*/
    0,                          /*tp_dict*/
    0,                          /*tp_descr_get*/
    0,                          /*tp_descr_set*/
    0,                          /*tp_dictoffset*/
    0,                          /*tp_init*/
    0,                          /*tp_alloc*/
    PyType_GenericNew,                          /*tp_new*/
    0,                          /*tp_free*/
    0,                          /*tp_is_gc*/
};

typedef struct {
    PyObject_HEAD
    uint32_t num_symbols;
    uint32_t compress;

    // tables
    uint32_t *distribution;
    uint32_t *symbol_count;
    uint32_t *decoder_table;
} ArithmeticModelObject;

static int
ArithmeticModel__init__(ArithmeticModelObject *self, PyObject *args, PyObject *kwargs)
{
    // get num_symbols and compress from args
    if (!PyArg_ParseTuple(args, "II", &self->num_symbols, &self->compress)) {
        return -1;
    }

    self->distribution = NULL;
    self->symbol_count = NULL;
    self->decoder_table = NULL;

    return 0;
}

static void
ArithmeticModel_dealloc(ArithmeticModelObject *self)
{
    if (self->distribution != NULL) {
        free(self->distribution);
    }
    if (self->symbol_count != NULL) {
        free(self->symbol_count);
    }
    if (self->decoder_table != NULL) {
        free(self->decoder_table);
    }

    PyObject_Del(self);
}

static PyMethodDef ArithmeticModel_methods[] = {
    {NULL,              NULL}           /* sentinel */
};

PyGetSetDef ArithmeticModel_getset[] = {
    {NULL}
};

static PyTypeObject ArithmeticModel_Type = {
    /* The ob_type field must be initialized in the module init function
     * to be portable to Windows without using C++. */
    PyVarObject_HEAD_INIT(NULL, 0)
    "cmodelsmodule.ArithmeticModel",             /*tp_name*/
    sizeof(ArithmeticModelObject),          /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    /* methods */
    (destructor)ArithmeticModel_dealloc,    /*tp_dealloc*/
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
    ArithmeticModel_methods,                /*tp_methods*/
    0,                          /*tp_members*/
    ArithmeticModel_getset,                          /*tp_getset*/
    0,                          /*tp_base*/
    0,                          /*tp_dict*/
    0,                          /*tp_descr_get*/
    0,                          /*tp_descr_set*/
    0,                          /*tp_dictoffset*/
    (initproc)ArithmeticModel__init__,                          /*tp_init*/
    0,                          /*tp_alloc*/
    PyType_GenericNew,                          /*tp_new*/
    0,                          /*tp_free*/
    0,                          /*tp_is_gc*/
};

/* List of functions defined in the module */

static PyMethodDef cmodels_methods[] = {
    {NULL,              NULL}           /* sentinel */
};

PyDoc_STRVAR(module_doc,
"C implementation of models.");


static int
cmodels_exec(PyObject *m)
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
    ArithmeticBitModel_Type.tp_base = &PyBaseObject_Type;
    if (PyType_Ready(&ArithmeticBitModel_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "ArithmeticBitModel", (PyObject *)&ArithmeticBitModel_Type);

    ArithmeticModel_Type.tp_base = &PyBaseObject_Type;
    if (PyType_Ready(&ArithmeticModel_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "ArithmeticModel", (PyObject *)&ArithmeticModel_Type);

    return 0;
 fail:
    Py_XDECREF(m);
    return -1;
}

static struct PyModuleDef_Slot cmodels_slots[] = {
    {Py_mod_exec, cmodels_exec},
    {0, NULL},
};

static struct PyModuleDef cmodelsmodule = {
    PyModuleDef_HEAD_INIT,
    "cmodels",
    module_doc,
    0,
    cmodels_methods,
    cmodels_slots,
    NULL,
    NULL,
    NULL
};

/* Export function for the module (*must* be called PyInit_cmodels) */

PyMODINIT_FUNC
PyInit_cmodels(void)
{
    return PyModuleDef_Init(&cmodelsmodule);
}