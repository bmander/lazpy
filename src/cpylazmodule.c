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
#include "cpylaz.h"

PyObject *LazErrorType = NULL;

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
