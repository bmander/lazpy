#ifndef Py_CMODELSMODULE_H
#define Py_CMODELSMODULE_H
#ifdef __cplusplus
extern "C" {
#endif

/* C API functions */
#define PyCModels_ArithmeicBitModelObject_Check_NUM 0
#define PyCModels_ArithmeicBitModelObject_Check_RETURN void
#define PyCModels_ArithmeicBitModelObject_Check_PROTO (ArithmeticBitModelObject *)

/* Total number of C API pointers */
#define PyCModels_API_pointers 1

#ifdef CMODELS_MODULE
/* This section is used when compiling cmodelsmodule.c */

static PyCModels_ArithmeicBitModelObject_Check_RETURN updateArithmeticBitModel PyCModels_ArithmeicBitModelObject_Check_PROTO;

#else
/* This section is used in modules that use cmodelsmodule's API */

static void **PyCModels_API;

#endif

#define BM_LENGTH_SHIFT 13
#define BM_MAX_COUNT (1 << BM_LENGTH_SHIFT)
#define MIN(a,b) ((a) < (b) ? (a) : (b))

#define DM_LENGTH_SHIFT 15
#define DM_MAX_COUNT (1 << DM_LENGTH_SHIFT)
#define RAISE_ERROR(msg) { PyErr_SetString(PyExc_Exception, msg); return NULL; }

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


#ifdef __cplusplus
}
#endif

#endif /* !Py_CMODELSMODULE_H */