
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

#define DM_LENGTH_SHIFT 15
#define DM_MAX_COUNT (1 << DM_LENGTH_SHIFT)

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

    uint32_t last_symbol;
    uint32_t table_shift;
    uint32_t table_size;
    uint32_t total_count;
    uint32_t update_cycle;
    uint32_t symbols_until_update;

    // tables
    uint32_t *distribution;
    uint32_t *symbol_count;
    uint32_t *decoder_table;
} ArithmeticModelObject;

void
ArithmeticModel__update(ArithmeticModelObject *self);

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

static PyObject *
ArithmeticModel_init(ArithmeticModelObject *self, PyObject *args, PyObject *kwargs)
{   
    PyObject * table = Py_None;
    if (!PyArg_ParseTuple(args, "|O", &table)) {
        return NULL;
    }

    

    if (self->distribution == NULL){
        if (self->num_symbols < 2 || self->num_symbols > 2048) {
            PyErr_SetString(PyExc_ValueError, "The number of symbols must be between 2 and 2048");
            return NULL;
        }

        self->last_symbol = self->num_symbols-1;

        if(self->compress==0 && self->num_symbols > 16) {
            uint32_t table_bits = 3;
            while(self->num_symbols > (1u << (table_bits+2u))){
                table_bits++;
            }

            self->table_shift = DM_LENGTH_SHIFT - table_bits;

            self->table_size = 1 << table_bits;

            uint32_t decoder_table_size = (self->table_size+2)*sizeof(uint32_t);
            self->decoder_table = (uint32_t *)malloc(decoder_table_size);
            memset(self->decoder_table, 0, decoder_table_size);
        } else { // small alphabet; no table needed
            self->table_shift = 0;
            self->table_size = 0;
        }

        self->distribution = (uint32_t *)malloc(self->num_symbols*sizeof(uint32_t));
        self->symbol_count = (uint32_t *)malloc(self->num_symbols*sizeof(uint32_t));

        memset(self->distribution, 0, self->num_symbols*sizeof(uint32_t));
    }

    self->total_count = 0;
    self->update_cycle = self->num_symbols;
    if(table != Py_None){
        // check that table is a list of ints
        if (!PyList_Check(table)) {
            PyErr_SetString(PyExc_TypeError, "table must be a list of ints");
            return NULL;
        }
        // copy table into symbol_count
        for (uint32_t i = 0; i < self->num_symbols; i++) {
            PyObject *item = PyList_GetItem(table, i);
            if (!PyLong_Check(item)) {
                PyErr_SetString(PyExc_TypeError, "table must be a list of ints");
                return NULL;
            }
            self->symbol_count[i] = PyLong_AsUnsignedLong(item);
        }
    } else {
        for(uint32_t i = 0; i < self->num_symbols; i++){
            self->symbol_count[i] = 1;
        }
    }

    ArithmeticModel__update(self);
    self->symbols_until_update = (self->num_symbols+6) >> 1;
    self->update_cycle = self->symbols_until_update;

    Py_RETURN_NONE;
}

void
ArithmeticModel__update(ArithmeticModelObject *self)
{
    // halve counts when threshold is reached
    self->total_count += self->update_cycle;
    if(self->total_count > DM_MAX_COUNT) {
        self->total_count = 0;
        for(uint32_t i = 0; i < self->num_symbols; i++) {
            self->symbol_count[i] = (self->symbol_count[i]+1) >> 1;
            self->total_count += self->symbol_count[i];
        }
    }

    // compute distribution
    uint32_t sum = 0;
    uint32_t s = 0;
    uint32_t scale = 0x80000000 / self->total_count;


    if(self->compress != 0 || self->table_size == 0){
        for(uint32_t k = 0; k < self->num_symbols; k++) {
            self->distribution[k] = (scale*sum) >> (31 - DM_LENGTH_SHIFT);
            sum += self->symbol_count[k];
        }
    } else {
        for(uint32_t k = 0; k < self->num_symbols; k++) {
            self->distribution[k] = (scale*sum) >> (31 - DM_LENGTH_SHIFT);
            sum += self->symbol_count[k];
            uint32_t w = self->distribution[k] >> self->table_shift;
            while(s < w){
                s++;
                self->decoder_table[s] = k-1;
            }
        }
        self->decoder_table[0] = 0;
        while( s<=self->table_size){
            s++;
            self->decoder_table[s] = self->num_symbols-1;
        }
    }

    // set frequency of model updates
    self->update_cycle = (5 * self->update_cycle) >> 2;
    uint32_t max_cycle = (self->num_symbols + 6) << 3;
    self->update_cycle = MIN(self->update_cycle, max_cycle);
    self->symbols_until_update = self->update_cycle;
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

static PyObject *
ArithmeticModel_increment_symbol_count(ArithmeticModelObject *self, PyObject *args)
{
    if (self->distribution == NULL) {
        PyErr_SetString(PyExc_ValueError, "Model not initialized");
        return NULL;
    }

    uint32_t symbol;
    if (!PyArg_ParseTuple(args, "I", &symbol)) {
        return NULL;
    }

    self->symbol_count[symbol]++;
    self->symbols_until_update--;

    if (self->symbols_until_update == 0) {
        ArithmeticModel__update(self);
    }

    Py_RETURN_NONE;
}

static PyObject *
ArithmeticModel_decoder_table_lookup(ArithmeticModelObject *self, PyObject *args)
{
    if(self->decoder_table == NULL) {
        PyErr_SetString(PyExc_Exception, "Model not initialized");
        return NULL;
    }

    uint32_t index;
    if (!PyArg_ParseTuple(args, "I", &index)) {
        return NULL;
    }

    if(index >= self->table_size+2) {
        PyErr_SetString(PyExc_IndexError, "index out of range");
        return NULL;
    }

    return PyLong_FromUnsignedLong(self->decoder_table[index]);
}

static PyObject *
ArithmeticModel_distribution_lookup(ArithmeticModelObject *self, PyObject *args)
{
    if(self->distribution == NULL) {
        PyErr_SetString(PyExc_Exception, "Model not initialized");
        return NULL;
    }

    uint32_t index;
    if (!PyArg_ParseTuple(args, "I", &index)) {
        return NULL;
    }

    if(index >= self->num_symbols) {
        PyErr_SetString(PyExc_IndexError, "index out of range");
        return NULL;
    }

    return PyLong_FromUnsignedLong(self->distribution[index]);
}

static PyObject *
ArithmeticModel_symbol_count_lookup(ArithmeticModelObject *self, PyObject *args)
{
    if(self->symbol_count == NULL) {
        PyErr_SetString(PyExc_Exception, "Model not initialized");
        return NULL;
    }

    uint32_t index;
    if (!PyArg_ParseTuple(args, "I", &index)) {
        return NULL;
    }

    if(index >= self->num_symbols) {
        PyErr_SetString(PyExc_IndexError, "index out of range");
        return NULL;
    }

    return PyLong_FromUnsignedLong(self->symbol_count[index]);
}

static PyObject *
ArithmeticModel_has_decoder_table(ArithmeticModelObject *self, PyObject *args)
{
    if(self->table_size == 0) {
        Py_RETURN_FALSE;
    } else {
        Py_RETURN_TRUE;
    }
}

static PyObject *
ArithmeticModel_get_num_symbols(ArithmeticModelObject *self, PyObject *args)
{
    return PyLong_FromUnsignedLong(self->num_symbols);
}

static PyObject *
ArithmeticModel_get_compress(ArithmeticModelObject *self, PyObject *args)
{
    if(self->compress == 1) {
        Py_RETURN_TRUE;
    } else {
        Py_RETURN_FALSE;
    }
}


static PyMethodDef ArithmeticModel_methods[] = {
    {"init",            (PyCFunction)ArithmeticModel_init,  METH_VARARGS,
        PyDoc_STR("init() -> None")},
    {"increment_symbol_count", (PyCFunction)ArithmeticModel_increment_symbol_count, METH_VARARGS,
        PyDoc_STR("increment_symbol_count(symbol) -> None")},
    {"decoder_table_lookup", (PyCFunction)ArithmeticModel_decoder_table_lookup, METH_VARARGS,
        PyDoc_STR("decoder_table_lookup(index) -> symbol")},
    {"distribution_lookup", (PyCFunction)ArithmeticModel_distribution_lookup, METH_VARARGS,
        PyDoc_STR("distribution_lookup(index) -> symbol")},
    {"symbol_count_lookup", (PyCFunction)ArithmeticModel_symbol_count_lookup, METH_VARARGS,
        PyDoc_STR("symbol_count_lookup(index) -> symbol")},
    {"has_decoder_table", (PyCFunction)ArithmeticModel_has_decoder_table, METH_VARARGS,
        PyDoc_STR("has_decoder_table() -> bool")},
    {NULL,              NULL}           /* sentinel */
};

PyGetSetDef ArithmeticModel_getset[] = {
    {"num_symbols", (getter)ArithmeticModel_get_num_symbols, NULL, "number of symbols", NULL},
    {"compress", (getter)ArithmeticModel_get_compress, NULL, "compress", NULL},
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