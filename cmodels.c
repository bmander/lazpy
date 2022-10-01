#define PY_SSIZE_T_CLEAN
#include <Python.h>

static PyObject *
cmodels_hello(PyObject *self, PyObject *args)
{
    const char *name;
    int sts;

    if (!PyArg_ParseTuple(args, "s", &name))
        return NULL;
    sts = printf("Hello %s!", name);

    return PyLong_FromLong(sts);
}

static PyMethodDef CmodelsMethods[] = {
    {"hello",  cmodels_hello, METH_VARARGS,
     "Print a friendly greeting."},
    {NULL, NULL, 0, NULL}        /* Sentinel */
};

static struct PyModuleDef cmodelsmodule = {
    PyModuleDef_HEAD_INIT,
    "cmodels",   /* name of module */
    NULL, /* module documentation, may be NULL */
    -1,       /* size of per-interpreter state of the module,
                 or -1 if the module keeps state in global variables. */
    CmodelsMethods
};

PyMODINIT_FUNC
PyInit_cmodels(void)
{
    return PyModule_Create(&cmodelsmodule);
}
