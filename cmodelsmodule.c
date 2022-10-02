
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

static PyObject *ErrorObject;

typedef struct {
    PyObject_HEAD
    PyObject            *x_attr;        /* Attributes dictionary */
    uint32_t bit_0_prob;
    uint32_t bit_0_count;
    uint32_t bit_count;
    uint32_t update_cycle;
    uint32_t bits_until_update;
} ArithmeticBitModelObject;

static PyTypeObject ArithmeticBitModel_Type;

#define ArithmeticBitModelObject_Check(v)      (Py_TYPE(v) == &ArithmeticBitModel_Type)

static ArithmeticBitModelObject *
newArithmeticBitModelObject(PyObject *arg)
{
    ArithmeticBitModelObject *self;
    self = PyObject_New(ArithmeticBitModelObject, &ArithmeticBitModel_Type);
    if (self == NULL)
        return NULL;
    self->x_attr = NULL;
    return self;
}

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
    Py_XDECREF(self->x_attr);
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
ArithmeticBitModel_demo(ArithmeticBitModelObject *self, PyObject *args)
{
    if (!PyArg_ParseTuple(args, ":demo"))
        return NULL;
    Py_INCREF(Py_None);
    return Py_None;
}

static PyMethodDef ArithmeticBitModel_methods[] = {
    {"demo",            (PyCFunction)ArithmeticBitModel_demo,  METH_VARARGS,
        PyDoc_STR("demo() -> None")},
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
    {NULL}
};

static PyObject *
ArithmeticBitModel_getattro(ArithmeticBitModelObject *self, PyObject *name)
{
    if (self->x_attr != NULL) {
        PyObject *v = PyDict_GetItemWithError(self->x_attr, name);
        if (v != NULL) {
            Py_INCREF(v);
            return v;
        }
        else if (PyErr_Occurred()) {
            return NULL;
        }
    }
    return PyObject_GenericGetAttr((PyObject *)self, name);
}

static int
ArithmeticBitModel_setattr(ArithmeticBitModelObject *self, const char *name, PyObject *v)
{
    if (self->x_attr == NULL) {
        self->x_attr = PyDict_New();
        if (self->x_attr == NULL)
            return -1;
    }
    if (v == NULL) {
        int rv = PyDict_DelItemString(self->x_attr, name);
        if (rv < 0 && PyErr_ExceptionMatches(PyExc_KeyError))
            PyErr_SetString(PyExc_AttributeError,
                "delete non-existing ArithmeticBitModel attribute");
        return rv;
    }
    else
        return PyDict_SetItemString(self->x_attr, name, v);
}

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
    (setattrfunc)ArithmeticBitModel_setattr,   /*tp_setattr*/
    0,                          /*tp_as_async*/
    0,                          /*tp_repr*/
    0,                          /*tp_as_number*/
    0,                          /*tp_as_sequence*/
    0,                          /*tp_as_mapping*/
    0,                          /*tp_hash*/
    0,                          /*tp_call*/
    0,                          /*tp_str*/
    (getattrofunc)ArithmeticBitModel_getattro, /*tp_getattro*/
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
/* --------------------------------------------------------------------- */

/* Function of two integers returning integer */

PyDoc_STRVAR(cmodels_foo_doc,
"foo(i,j)\n\
\n\
Return the sum of i and j.");

static PyObject *
cmodels_foo(PyObject *self, PyObject *args)
{
    long i, j;
    long res;
    if (!PyArg_ParseTuple(args, "ll:foo", &i, &j))
        return NULL;
    res = i+j; /* CMODELSX Do something here */
    return PyLong_FromLong(res);
}


/* Function of no arguments returning new ArithmeticBitModel object */

static PyObject *
cmodels_new(PyObject *self, PyObject *args)
{
    ArithmeticBitModelObject *rv;

    if (!PyArg_ParseTuple(args, ":new"))
        return NULL;
    rv = newArithmeticBitModelObject(args);
    if (rv == NULL)
        return NULL;
    return (PyObject *)rv;
}

/* Example with subtle bug from extensions manual ("Thin Ice"). */

static PyObject *
cmodels_bug(PyObject *self, PyObject *args)
{
    PyObject *list, *item;

    if (!PyArg_ParseTuple(args, "O:bug", &list))
        return NULL;

    item = PyList_GetItem(list, 0);
    /* Py_INCREF(item); */
    PyList_SetItem(list, 1, PyLong_FromLong(0L));
    PyObject_Print(item, stdout, 0);
    printf("\n");
    /* Py_DECREF(item); */

    Py_INCREF(Py_None);
    return Py_None;
}

/* Test bad format character */

static PyObject *
cmodels_roj(PyObject *self, PyObject *args)
{
    PyObject *a;
    long b;
    if (!PyArg_ParseTuple(args, "O#:roj", &a, &b))
        return NULL;
    Py_INCREF(Py_None);
    return Py_None;
}


/* ---------- */

static PyTypeObject Str_Type = {
    /* The ob_type field must be initialized in the module init function
     * to be portable to Windows without using C++. */
    PyVarObject_HEAD_INIT(NULL, 0)
    "cmodelsmodule.Str",             /*tp_name*/
    0,                          /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    /* methods */
    0,                          /*tp_dealloc*/
    0,                          /*tp_vectorcall_offset*/
    0,                          /*tp_getattr*/
    0,                          /*tp_setattr*/
    0,                          /*tp_as_async*/
    0,                          /*tp_repr*/
    0,                          /*tp_as_number*/
    0,                          /*tp_as_sequence*/
    0,                          /*tp_as_mapping*/
    0,                          /*tp_hash*/
    0,                          /*tp_call*/
    0,                          /*tp_str*/
    0,                          /*tp_getattro*/
    0,                          /*tp_setattro*/
    0,                          /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    0,                          /*tp_doc*/
    0,                          /*tp_traverse*/
    0,                          /*tp_clear*/
    0,                          /*tp_richcompare*/
    0,                          /*tp_weaklistoffset*/
    0,                          /*tp_iter*/
    0,                          /*tp_iternext*/
    0,                          /*tp_methods*/
    0,                          /*tp_members*/
    0,                          /*tp_getset*/
    0, /* see PyInit_cmodels */      /*tp_base*/
    0,                          /*tp_dict*/
    0,                          /*tp_descr_get*/
    0,                          /*tp_descr_set*/
    0,                          /*tp_dictoffset*/
    0,                          /*tp_init*/
    0,                          /*tp_alloc*/
    0,                          /*tp_new*/
    0,                          /*tp_free*/
    0,                          /*tp_is_gc*/
};

/* ---------- */

static PyObject *
null_richcompare(PyObject *self, PyObject *other, int op)
{
    Py_INCREF(Py_NotImplemented);
    return Py_NotImplemented;
}

static PyTypeObject Null_Type = {
    /* The ob_type field must be initialized in the module init function
     * to be portable to Windows without using C++. */
    PyVarObject_HEAD_INIT(NULL, 0)
    "cmodelsmodule.Null",            /*tp_name*/
    0,                          /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    /* methods */
    0,                          /*tp_dealloc*/
    0,                          /*tp_vectorcall_offset*/
    0,                          /*tp_getattr*/
    0,                          /*tp_setattr*/
    0,                          /*tp_as_async*/
    0,                          /*tp_repr*/
    0,                          /*tp_as_number*/
    0,                          /*tp_as_sequence*/
    0,                          /*tp_as_mapping*/
    0,                          /*tp_hash*/
    0,                          /*tp_call*/
    0,                          /*tp_str*/
    0,                          /*tp_getattro*/
    0,                          /*tp_setattro*/
    0,                          /*tp_as_buffer*/
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE, /*tp_flags*/
    0,                          /*tp_doc*/
    0,                          /*tp_traverse*/
    0,                          /*tp_clear*/
    null_richcompare,           /*tp_richcompare*/
    0,                          /*tp_weaklistoffset*/
    0,                          /*tp_iter*/
    0,                          /*tp_iternext*/
    0,                          /*tp_methods*/
    0,                          /*tp_members*/
    0,                          /*tp_getset*/
    0, /* see PyInit_cmodels */      /*tp_base*/
    0,                          /*tp_dict*/
    0,                          /*tp_descr_get*/
    0,                          /*tp_descr_set*/
    0,                          /*tp_dictoffset*/
    0,                          /*tp_init*/
    0,                          /*tp_alloc*/
    PyType_GenericNew,          /*tp_new*/
    0,                          /*tp_free*/
    0,                          /*tp_is_gc*/
};


/* ---------- */


/* List of functions defined in the module */

static PyMethodDef cmodels_methods[] = {
    {"roj",             cmodels_roj,         METH_VARARGS,
        PyDoc_STR("roj(a,b) -> None")},
    {"foo",             cmodels_foo,         METH_VARARGS,
        cmodels_foo_doc},
    {"new",             cmodels_new,         METH_VARARGS,
        PyDoc_STR("new() -> new Cmodels object")},
    {"bug",             cmodels_bug,         METH_VARARGS,
        PyDoc_STR("bug(o) -> None")},
    {NULL,              NULL}           /* sentinel */
};

PyDoc_STRVAR(module_doc,
"This is a template module just for instruction.");


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
    Null_Type.tp_base = &PyBaseObject_Type;
    Str_Type.tp_base = &PyUnicode_Type;

    /* Finalize the type object including setting type of the new type
     * object; doing it here is required for portability, too. */
    if (PyType_Ready(&ArithmeticBitModel_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "ArithmeticBitModel", (PyObject *)&ArithmeticBitModel_Type);

    /* Add some symbolic constants to the module */
    if (ErrorObject == NULL) {
        ErrorObject = PyErr_NewException("cmodels.error", NULL, NULL);
        if (ErrorObject == NULL)
            goto fail;
    }
    Py_INCREF(ErrorObject);
    PyModule_AddObject(m, "error", ErrorObject);

    /* Add Str */
    if (PyType_Ready(&Str_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "Str", (PyObject *)&Str_Type);

    /* Add Null */
    if (PyType_Ready(&Null_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "Null", (PyObject *)&Null_Type);
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