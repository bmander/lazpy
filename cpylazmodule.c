
#include "Python.h"


#define BM_LENGTH_SHIFT 13
#define BM_MAX_COUNT (1 << BM_LENGTH_SHIFT)
#define MIN(a,b) ((a) < (b) ? (a) : (b))

#define AC_MAX_LENGTH 0xFFFFFFFF
#define AC_MIN_LENGTH 0x01000000

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

static void
_ArithmeticBitModel_init(ArithmeticBitModelObject *self)
{
    // initialize equiprobable model
    self->bit_0_count = 1;
    self->bit_count = 2;
    self->bit_0_prob = 1 << (BM_LENGTH_SHIFT - 1);

    // start with frequent updates
    self->update_cycle = self->bits_until_update = 4;
}

static PyObject *
ArithmeticBitModel_init(ArithmeticBitModelObject *self, PyObject *args)
{
    _ArithmeticBitModel_init(self);

    return Py_None;
}

static int
ArithmeticBitModel__init__(ArithmeticBitModelObject *self, PyObject *args, PyObject *kwargs)
{   
    _ArithmeticBitModel_init(self);

    return 0;
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
    "cpylaz.ArithmeticBitModel",             /*tp_name*/
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
    (initproc)ArithmeticBitModel__init__,                          /*tp_init*/
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

int
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
_ArithmeticModel_init(ArithmeticModelObject *self, PyObject *table)
{   

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
    if(table != NULL){
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

    if (ArithmeticModel__update(self) == 1) {
        return NULL;
    }
    self->symbols_until_update = (self->num_symbols+6) >> 1;
    self->update_cycle = self->symbols_until_update;

    Py_RETURN_NONE;
}

static PyObject *
ArithmeticModel_init(ArithmeticModelObject *self, PyObject *args, PyObject *kwargs)
{   

    PyObject * table = NULL;
    if (!PyArg_ParseTuple(args, "|O", &table)) {
        return NULL;
    }

    if (table != NULL && !PyList_Check(table)) {
        PyErr_SetString(PyExc_TypeError, "The table argument must be a list");
        return NULL;
    }

    if (table != NULL && PyList_Size(table) != self->num_symbols) {
        PyErr_SetString(PyExc_ValueError, "The table argument must be the same length as num_symbols");
        return NULL;
    }

    return _ArithmeticModel_init(self, table);

}

int
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

    // TODO use of 64 bits is a hack to get the C impl to behave like the 
    // python impl. The weird thing is, the python impl must have been
    // behaving differently than the original 32 bit implementation, and yet
    // the end result worked just fine. After all the unit tests pass, change
    // this back to 32 bits to see if it still works.
    uint32_t sum = 0;
    uint32_t s = 0;
    uint32_t scale = 0x80000000u / self->total_count;


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

void
_ArithmeticModel_increment_symbol_count(ArithmeticModelObject *self, uint32_t symbol)
{
    self->symbol_count[symbol]++;
    self->symbols_until_update--;

    if (self->symbols_until_update == 0) {
        ArithmeticModel__update(self);
    }
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

    _ArithmeticModel_increment_symbol_count(self, symbol);

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

static PyObject *
ArithmeticModel_get_table_shift(ArithmeticModelObject *self, PyObject *args)
{
    return PyLong_FromUnsignedLong(self->table_shift);
}

static PyObject *
ArithmeticModel_get_last_symbol(ArithmeticModelObject *self, PyObject *args)
{
    return PyLong_FromUnsignedLong(self->last_symbol);
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
    {"table_shift", (getter)ArithmeticModel_get_table_shift, NULL, "table_shift", NULL},
    {"last_symbol", (getter)ArithmeticModel_get_last_symbol, NULL, "last_symbol", NULL},
    {NULL}
};

static PyTypeObject ArithmeticModel_Type = {
    /* The ob_type field must be initialized in the module init function
     * to be portable to Windows without using C++. */
    PyVarObject_HEAD_INIT(NULL, 0)
    "cpylaz.ArithmeticModel",             /*tp_name*/
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
    "cpylaz.ArithmeticEncoder", /*tp_name*/
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
    uint8_t *bytes = (uint8_t*)PyBytes_AsString(read_result);

    // read big endian uint32_t
    self->value = bytes[0] << 24 | bytes[1] << 16 | bytes[2] << 8 | bytes[3];
    self->length = AC_MAX_LENGTH;

    Py_DECREF(read_result);

    Py_RETURN_NONE;
}

void
ArithmeticDecoder__renorm_dec_interval(ArithmeticDecoderObject *self) {
    while (self->length < AC_MIN_LENGTH) {
        PyObject *read_result = getBytesFromPythonFileLikeObject(self->fp, 1);
        void *bytes = PyBytes_AsString(read_result);

        self->value = (self->value << 8) | *((uint8_t *)bytes);
        self->length <<= 8;

        Py_DECREF(read_result);
    }
}

static uint32_t
_ArithmeticDecoder_decode_bit(ArithmeticDecoderObject *self, ArithmeticBitModelObject *m)
{

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

    return sym;
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

    uint32_t sym = _ArithmeticDecoder_decode_bit(self, m);

    return PyLong_FromUnsignedLong(sym);
    
}

static uint32_t
_ArithmeticDecoder_decode_symbol(ArithmeticDecoderObject *self, ArithmeticModelObject *m) {
    uint32_t y = self->length;
    uint32_t x;
    uint32_t sym;
    uint32_t n;
    uint32_t k;

    // use table lookup for faster decoding
    if(m->table_size > 0){

        self->length >>= DM_LENGTH_SHIFT;
        uint32_t dv = self->value / self->length;
        uint32_t t = dv >> m->table_shift;

        // use table to get first symbol
        sym = m->decoder_table[t];
        n = m->decoder_table[t+1] + 1;

        // finish with bisection search
        while(n > sym+1) {
            uint32_t k = (sym + n) >> 1;
            if(m->distribution[k] > dv) {
                n = k;
            } else {
                sym = k;
            }
        }

        // compute products
        x = m->distribution[sym] * self->length;

        if(sym != m->last_symbol) {
            y = m->distribution[sym+1] * self->length;
        }
    } else {
        // decode using only multiplications
        x = sym = 0;
        self->length >>= DM_LENGTH_SHIFT;
        n = m->num_symbols;
        k = n >> 1;

        // decode via bisection search
        while(k != sym){
            uint32_t z = self->length * m->distribution[k];
            if(z > self->value){
                n = k;
                y = z;  // value is smaller
            } else {
                sym = k;
                x = z;  // value is larger or equal
            }

            k = (sym + n) >> 1;
        }
    }


    // update interval
    self->value -= x;
    self->length = y - x;

    if(self->length < AC_MIN_LENGTH){
        ArithmeticDecoder__renorm_dec_interval(self);
    }

    _ArithmeticModel_increment_symbol_count(m, sym);

    return sym;
}

static PyObject *
ArithmeticDecoder_decode_symbol(ArithmeticDecoderObject *self, PyObject *args) {
    // get ArithmeticModel from args
    PyObject *argm;
    if (!PyArg_ParseTuple(args, "O!", &ArithmeticModel_Type, &argm)) {
        return NULL;
    }
    ArithmeticModelObject *m = (ArithmeticModelObject *)argm;

    uint32_t sym = _ArithmeticDecoder_decode_symbol(self, m);

    return PyLong_FromUnsignedLong(sym);
}

uint32_t
_ArithmeticDecoder_read_bits(ArithmeticDecoderObject *self, uint32_t bits) {

    if(bits > 19) {
        uint32_t lower = _ArithmeticDecoder_read_bits(self, 16);
        uint32_t upper = _ArithmeticDecoder_read_bits(self, bits-16);
        return (upper << 16) | lower;
    }

    self->length >>= bits;
    uint32_t sym = self->value / (self->length);
    self->value = self->value % self->length;
    
    if(self->length < AC_MIN_LENGTH){
       ArithmeticDecoder__renorm_dec_interval(self);
    }

    return sym;
}

static PyObject *
ArithmeticDecoder_read_bits(ArithmeticDecoderObject *self, PyObject *args) {
    uint32_t bits;
    if (!PyArg_ParseTuple(args, "I", &bits)) {
        return NULL;
    }

    if(bits > 32) {
        PyErr_SetString(PyExc_ValueError, "bits must be <= 32");
        return NULL;
    }

    uint32_t sym = _ArithmeticDecoder_read_bits(self, bits);
    return PyLong_FromUnsignedLong(sym);
}

static PyObject *
ArithmeticDecoder_read_int(ArithmeticDecoderObject *self, PyObject *args){
    uint32_t sym = _ArithmeticDecoder_read_bits(self, 32);
    return PyLong_FromUnsignedLong(sym);
}

static PyObject *
_ArithmeticDecoder_create_symbol_model(ArithmeticDecoderObject *self, uint32_t num_symbols) {

    PyObject *newargs = PyTuple_New(2);
    PyTuple_SetItem(newargs, 0, PyLong_FromUnsignedLong(num_symbols));
    PyTuple_SetItem(newargs, 1, PyBool_FromLong(0));
    PyObject *model = PyObject_CallObject((PyObject *)&ArithmeticModel_Type, newargs);

    return model;
}

static PyObject *
ArithmeticDecoder_create_symbol_model(ArithmeticDecoderObject *self, PyObject *args) {
    uint32_t num_symbols;
    if (!PyArg_ParseTuple(args, "I", &num_symbols)) {
        return NULL;
    }

    return _ArithmeticDecoder_create_symbol_model(self, num_symbols);
}

static PyObject *
ArithmeticDecoder__repr__(ArithmeticDecoderObject *self) {
    return PyUnicode_FromFormat("ArithmeticDecoder(value=%d, length=%d)", self->value, self->length);
}

static PyObject *
ArithmeticDecoder_length(ArithmeticDecoderObject *self, PyObject *args)
{
    return PyLong_FromUnsignedLong(self->length);
}

static PyObject *
ArithmeticDecoder_value(ArithmeticDecoderObject *self, PyObject *args)
{
    return PyLong_FromUnsignedLong(self->value);
}

static PyMethodDef ArithmeticDecoder_methods[] = {
    {"start", (PyCFunction)ArithmeticDecoder_start, METH_VARARGS, "Start decoding"},
    {"decode_bit", (PyCFunction)ArithmeticDecoder_decode_bit, METH_VARARGS, "Decode a bit"},
    {"decode_symbol", (PyCFunction)ArithmeticDecoder_decode_symbol, METH_VARARGS, "Decode a symbol"},
    {"read_bits", (PyCFunction)ArithmeticDecoder_read_bits, METH_VARARGS, "Read bits"},
    {"read_int", (PyCFunction)ArithmeticDecoder_read_int, METH_VARARGS, "Read int"},
    {"create_symbol_model", (PyCFunction)ArithmeticDecoder_create_symbol_model, METH_VARARGS, "Create symbol model"},
    {NULL, NULL}  /* Sentinel */
};

PyGetSetDef ArithmeticDecoder_getset[] = {
    {"length", (getter)ArithmeticDecoder_length, NULL, "length", NULL},
    {"value", (getter)ArithmeticDecoder_value, NULL, "value", NULL},
    {NULL}  /* Sentinel */
};

static PyTypeObject ArithmeticDecoder_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    "cpylaz.ArithmeticDecoder", /*tp_name*/
    sizeof(ArithmeticDecoderObject), /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    /* methods */
    (destructor)ArithmeticDecoder_dealloc,    /*tp_dealloc*/
    0,                          /*tp_vectorcall_offset*/
    (getattrfunc)0,             /*tp_getattr*/
    0,   /*tp_setattr*/
    0,                          /*tp_as_async*/
    (reprfunc)ArithmeticDecoder__repr__,                          /*tp_repr*/
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

typedef struct {
    PyObject_HEAD
    PyObject *enc;
    PyObject *dec;
    uint32_t k;
    uint32_t bits;
    uint32_t contexts;
    uint32_t bits_high;
    uint32_t range;
    uint32_t corr_bits;
    uint32_t corr_range;
    int32_t corr_min;
    int32_t corr_max;
    PyObject **m_bits;
    PyObject **m_corrector;
} IntegerCompressorObject;

static PyTypeObject IntegerCompressor_Type;

static int
_IntegerCompressor__init__(IntegerCompressorObject *self, PyObject *enc, 
                           PyObject *dec, uint32_t bits, uint32_t contexts, 
                           uint32_t bits_high, uint32_t range) {

    if(enc) {
        Py_INCREF(enc);
        self->enc = enc;
    } else {
        self->enc = Py_None;
    }

    if(dec) {
        Py_INCREF(dec);
        self->dec = dec;
    } else {
        self->dec = Py_None;
    }

    self->bits = bits;
    self->contexts = contexts;
    self->bits_high = bits_high;
    self->range = range;

    if(range != 0) {
        self->corr_bits = 0;
        self->corr_range = range;
        while(range != 0) {
            range >>= 1;
            self->corr_bits += 1;
        }
        if(self->corr_range == (1u << (self->corr_bits - 1u))) {
            self->corr_bits -= 1;
        }
        self->corr_min = -self->corr_range / 2;
        self->corr_max = self->corr_min+self->corr_range-1;
    } else if( bits > 0 && bits < 32) {
        self->corr_bits = bits;
        self->corr_range = 1 << bits;
        self->corr_min = -self->corr_range / 2;
        self->corr_max = self->corr_min+self->corr_range-1;
    } else {
        self->corr_bits = 32;
        self->corr_range = 0;
        self->corr_min = -0x7FFFFFFF;
        self->corr_max = 0x7FFFFFFF;
    }

    self->m_bits = NULL;
    self->m_corrector = NULL;

    self->k = 0;

    return 0;
}

static int
IntegerCompressor__init__(IntegerCompressorObject *self, PyObject *args, PyObject *kwargs) {

    uint32_t bits=16;
    uint32_t contexts=1;
    uint32_t bits_high=8;
    uint32_t range=0;
    PyObject *enc;
    PyObject *dec;

    PyObject *enc_or_dec;
    if (!PyArg_ParseTuple(args, "O|IIII", &enc_or_dec, &bits, &contexts, &bits_high, &range)) {
        return -1;
    }

    if (PyObject_IsInstance(enc_or_dec, (PyObject *)&ArithmeticEncoder_Type)) {
        enc = enc_or_dec;
        dec = NULL;
    } else if (PyObject_IsInstance(enc_or_dec, (PyObject *)&ArithmeticDecoder_Type)) {
        enc = NULL;
        dec = enc_or_dec;
    } else {
        PyErr_SetString(PyExc_TypeError, "Argument must be an encoder or decoder");
        return -1;
    }

    return _IntegerCompressor__init__(self, enc, dec, bits, contexts, bits_high, range);
}

static PyObject *
_IntegerCompressor_create(ArithmeticDecoderObject *dec, uint32_t bits, uint32_t contexts) {

    PyObject *args = Py_BuildValue("(OII)", dec, bits, contexts);
    PyObject *ic = PyObject_CallObject((PyObject *)&IntegerCompressor_Type, args);

    return ic;
}

static void
IntegerCompressor_dealloc(IntegerCompressorObject *self) {
    Py_XDECREF(self->enc);
    Py_XDECREF(self->dec);
    if(self->m_bits != NULL) {
        for(uint32_t i=0; i<self->contexts; i++) {
            Py_XDECREF(self->m_bits[i]);
        }
        free(self->m_bits);
    }
    if(self->m_corrector != NULL) {
        for(uint32_t i=0; i<self->contexts; i++) {
            Py_XDECREF(self->m_corrector[i]);
        }
        free(self->m_corrector);
    }
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static PyObject *
IntegerCompressor_init_decompressor(IntegerCompressorObject *self, PyObject *args){

    if(self->m_bits == NULL){
        self->m_bits = malloc(self->contexts * sizeof(PyObject *));
        self->m_corrector = malloc(self->corr_bits * sizeof(PyObject *));
        for(uint32_t i=0; i<self->contexts; i++) {
            PyObject *model = _ArithmeticDecoder_create_symbol_model(
                (ArithmeticDecoderObject *)self->dec, self->corr_bits+1);
            Py_INCREF(model);
            self->m_bits[i] = model;
        }

        PyObject *bitmodel = PyObject_CallObject((PyObject *)&ArithmeticBitModel_Type, NULL);
        self->m_corrector[0] = bitmodel;

        for(uint32_t i=1; i<self->corr_bits; i++){
            uint32_t num_symbols;
            if(i <= self->bits_high){
                num_symbols = 1 << i;
            } else {
                num_symbols = 1 << self->bits_high;
            } 
            PyObject *model = _ArithmeticDecoder_create_symbol_model((ArithmeticDecoderObject *)self->dec, num_symbols);
            Py_INCREF(model);
            self->m_corrector[i] = model;
        }
    }

    for(uint32_t i=0; i<self->contexts; i++) {
        _ArithmeticModel_init((ArithmeticModelObject *)self->m_bits[i], NULL);
    }

    _ArithmeticBitModel_init((ArithmeticBitModelObject *)self->m_corrector[0]);

    for(uint32_t i=1; i<self->corr_bits; i++){
        _ArithmeticModel_init((ArithmeticModelObject *)self->m_corrector[i], NULL);
    }

    Py_RETURN_NONE;
}

static int32_t
_IntegerCompressor_read_corrector(IntegerCompressorObject *self, ArithmeticModelObject *model){
    uint32_t k1, c1;
    int32_t c;

    self->k = _ArithmeticDecoder_decode_symbol((ArithmeticDecoderObject *)self->dec, model);


    if(self->k != 0) {
        if(self->k < 32) {
            c = _ArithmeticDecoder_decode_symbol((ArithmeticDecoderObject *)self->dec, (ArithmeticModelObject *)self->m_corrector[self->k]);
            if(self->k > self->bits_high){
                k1 = self->k - self->bits_high;
                c1 = _ArithmeticDecoder_read_bits((ArithmeticDecoderObject *)self->dec, k1);
                c = (c << k1) | c1;
            }

            // translate c back into its correct interval
            if(c >= (1 << (self->k-1))) {
                c += 1;
            } else {
                c -= (1u << self->k)-1u;
            }

        } else { 
            c = self->corr_min;
        }
    } else {
        c = _ArithmeticDecoder_decode_bit((ArithmeticDecoderObject *)self->dec, (ArithmeticBitModelObject *)self->m_corrector[0]);
    }

    return c;
}

static int32_t
_IntegerCompressor_decompress(IntegerCompressorObject *self, int32_t pred, uint32_t context){
    int32_t real;

    real = pred + _IntegerCompressor_read_corrector(self, (ArithmeticModelObject *)self->m_bits[context]);

    if(real < 0) {
        real += self->corr_range;
    } else if(real >= (int32_t)self->corr_range) {
        real -= self->corr_range;
    }

    return real;
}

static PyObject *
IntegerCompressor_decompress(IntegerCompressorObject *self, PyObject *args){
    int32_t pred, real;
    uint32_t context = 0;

    if(!PyArg_ParseTuple(args, "i|I", &pred, &context)) {
        return NULL;
    }

    real = _IntegerCompressor_decompress(self, pred, context);

    return PyLong_FromLong(real);
}


static PyObject *
IntegerCompressor_get_m_bits(IntegerCompressorObject *self, PyObject *args){
    // returns the m_bits at the given index
    uint32_t index;
    if (!PyArg_ParseTuple(args, "I", &index)) {
        return NULL;
    }
    if(index >= self->contexts) {
        PyErr_SetString(PyExc_IndexError, "Index out of range");
        return NULL;
    }
    Py_INCREF(self->m_bits[index]);
    return self->m_bits[index];
}

static PyObject *
IntegerCompressor_get_corrector(IntegerCompressorObject *self, PyObject *args){
    // returns the m_corrector at the given index
    uint32_t index;
    if (!PyArg_ParseTuple(args, "I", &index)) {
        return NULL;
    }
    if(index >= self->corr_bits) {
        PyErr_SetString(PyExc_IndexError, "Index out of range");
        return NULL;
    }
    Py_INCREF(self->m_corrector[index]);
    return self->m_corrector[index];
}

static PyObject *
IntegerCompressor_get_enc(IntegerCompressorObject *self, void *closure) {
    Py_INCREF(self->enc);
    return self->enc;
}

static PyObject *
IntegerCompressor_get_dec(IntegerCompressorObject *self, void *closure) {
    Py_INCREF(self->dec);
    return self->dec;
}

static PyObject *
IntegerCompressor_get_bits(IntegerCompressorObject *self, void *closure) {
    return PyLong_FromUnsignedLong(self->bits);
}

static PyObject *
IntegerCompressor_get_contexts(IntegerCompressorObject *self, void *closure) {
    return PyLong_FromUnsignedLong(self->contexts);
}

static PyObject *
IntegerCompressor_get_bits_high(IntegerCompressorObject *self, void *closure) {
    return PyLong_FromUnsignedLong(self->bits_high);
}

static PyObject *
IntegerCompressor_get_range(IntegerCompressorObject *self, void *closure) {
    return PyLong_FromUnsignedLong(self->range);
}

static PyObject *
IntegerCompressor_get_k(IntegerCompressorObject *self, void *closure) {
    return PyLong_FromUnsignedLong(self->k);
}

static PyMethodDef IntegerCompressor_methods[] = {
    {"init_decompressor", (PyCFunction)IntegerCompressor_init_decompressor, METH_NOARGS, NULL},
    {"get_m_bits", (PyCFunction)IntegerCompressor_get_m_bits, METH_VARARGS, NULL},
    {"get_corrector", (PyCFunction)IntegerCompressor_get_corrector, METH_VARARGS, NULL},
    {"decompress", (PyCFunction)IntegerCompressor_decompress, METH_VARARGS, NULL},
    {NULL, NULL}  /* Sentinel */
};

PyGetSetDef IntegerCompressor_getset[] = {
    {"enc", (getter)IntegerCompressor_get_enc, NULL, "Encoder", NULL},
    {"dec", (getter)IntegerCompressor_get_dec, NULL, "Decoder", NULL},
    {"bits", (getter)IntegerCompressor_get_bits, NULL, "Bits", NULL},
    {"contexts", (getter)IntegerCompressor_get_contexts, NULL, "Contexts", NULL},
    {"bits_high", (getter)IntegerCompressor_get_bits_high, NULL, "Bits high", NULL},
    {"range", (getter)IntegerCompressor_get_range, NULL, "Range", NULL},
    {"k", (getter)IntegerCompressor_get_k, NULL, "K", NULL},
    {NULL}  /* Sentinel */
};

static PyTypeObject IntegerCompressor_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    "cpylaz.IntegerCompressor", /*tp_name*/
    sizeof(IntegerCompressorObject), /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    /* methods */
    (destructor)IntegerCompressor_dealloc,    /*tp_dealloc*/
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
    IntegerCompressor_methods,                /*tp_methods*/
    0,                          /*tp_members*/
    IntegerCompressor_getset,                          /*tp_getset*/
    0,                          /*tp_base*/
    0,                          /*tp_dict*/
    0,                          /*tp_descr_get*/
    0,                          /*tp_descr_set*/
    0,                          /*tp_dictoffset*/
    (initproc)IntegerCompressor__init__,                          /*tp_init*/
    0,                          /*tp_alloc*/
    PyType_GenericNew,                          /*tp_new*/
    0,                          /*tp_free*/
    0,                          /*tp_is_gc*/
};

// StreamingMedian5

typedef struct {
    int32_t values[5];
    int32_t high;
} StreamingMedian5;

inline void StreamingMedian5_init(StreamingMedian5 *self) {
    self->values[0] = 0;
    self->values[1] = 0;
    self->values[2] = 0;
    self->values[3] = 0;
    self->values[4] = 0;
    self->high = 1;
}

inline void add(StreamingMedian5 *self, int32_t v){
    if (self->high) {
        if (v < self->values[2]) {
            self->values[4] = self->values[3];
            self->values[3] = self->values[2];
            if (v < self->values[0]) {
                self->values[2] = self->values[1];
                self->values[1] = self->values[0];
                self->values[0] = v;
            } else if (v < self->values[1]) {
                self->values[2] = self->values[1];
                self->values[1] = v;
            } else {
                self->values[2] = v;
            }
        } else {
            if (v < self->values[3]) {
                self->values[4] = self->values[3];
                self->values[3] = v;
            } else {
                self->values[4] = v;
            }
            self->high = 0;
        }
    } else {
        if (self->values[2] < v) {
            self->values[0] = self->values[1];
            self->values[1] = self->values[2];
            if (self->values[4] < v) {
                self->values[2] = self->values[3];
                self->values[3] = self->values[4];
                self->values[4] = v;
            } else if (self->values[3] < v) {
                self->values[2] = self->values[3];
                self->values[3] = v;
            } else {
                self->values[2] = v;
            }
        } else {
            if (self->values[1] < v) {
                self->values[0] = self->values[1];
                self->values[1] = v;
            } else {
                self->values[0] = v;
            }
            self->high = 1;
        }
    }
}

inline int32_t get(StreamingMedian5* self)
{
    return self->values[2];
}

const uint8_t number_return_map[8][8] = 
{
  { 15, 14, 13, 12, 11, 10,  9,  8 },
  { 14,  0,  1,  3,  6, 10, 10,  9 },
  { 13,  1,  2,  4,  7, 11, 11, 10 },
  { 12,  3,  4,  5,  8, 12, 12, 11 },
  { 11,  6,  7,  8,  9, 13, 13, 12 },
  { 10, 10, 11, 12, 13, 14, 14, 13 },
  {  9, 10, 11, 12, 13, 14, 15, 14 },
  {  8,  9, 10, 11, 12, 13, 14, 15 }
};

const uint8_t number_return_level[8][8] = 
{
  {  0,  1,  2,  3,  4,  5,  6,  7 },
  {  1,  0,  1,  2,  3,  4,  5,  6 },
  {  2,  1,  0,  1,  2,  3,  4,  5 },
  {  3,  2,  1,  0,  1,  2,  3,  4 },
  {  4,  3,  2,  1,  0,  1,  2,  3 },
  {  5,  4,  3,  2,  1,  0,  1,  2 },
  {  6,  5,  4,  3,  2,  1,  0,  1 },
  {  7,  6,  5,  4,  3,  2,  1,  0 }
};

typedef struct {
    PyObject_HEAD
    ArithmeticDecoderObject *dec;
    ArithmeticModelObject *m_changed_values;
    IntegerCompressorObject *ic_intensity;
    ArithmeticModelObject *m_scan_rank[2];
    IntegerCompressorObject *ic_point_source_id;
    ArithmeticModelObject *m_bit_byte[256];
    ArithmeticModelObject *m_classification[256];
    ArithmeticModelObject *m_user_data[256];
    IntegerCompressorObject *ic_dx;
    IntegerCompressorObject *ic_dy;
    IntegerCompressorObject *ic_z;
    StreamingMedian5 last_x_diff_median5[16];
    StreamingMedian5 last_y_diff_median5[16];

    uint16_t last_intensity[16];
    int32_t last_height[8];
    uint8_t last_item[20];
} read_item_compressed_point10_v2Object;

static void
read_item_compressed_point10_v2_dealloc(read_item_compressed_point10_v2Object* self)
{
    Py_XDECREF(self->dec);
    Py_XDECREF(self->m_changed_values);
    Py_XDECREF(self->ic_intensity);
    for(int i = 0; i < 2; i++)
        Py_XDECREF(&self->m_scan_rank[i]);
    Py_XDECREF(self->ic_point_source_id);
    for(int i = 0; i < 256; i++)
        Py_XDECREF(&self->m_bit_byte[i]);
    for(int i = 0; i < 256; i++)
        Py_XDECREF(&self->m_classification[i]);
    for(int i = 0; i < 256; i++)
        Py_XDECREF(&self->m_user_data[i]);
    Py_XDECREF(self->ic_dx);
    Py_XDECREF(self->ic_dy);
    Py_XDECREF(self->ic_z);

    Py_TYPE(self)->tp_free((PyObject *)self);
}

static int
_read_item_compressed_point10_v2__init__(read_item_compressed_point10_v2Object *self, ArithmeticDecoderObject *dec)
{
    self->dec = dec;
    Py_INCREF(dec);

    self->m_changed_values = (ArithmeticModelObject *)_ArithmeticDecoder_create_symbol_model(self->dec, 64);
    self->ic_intensity = (IntegerCompressorObject *)_IntegerCompressor_create(self->dec, 16, 4);
    for(int i = 0; i < 2; i++)
        self->m_scan_rank[i] = (ArithmeticModelObject *)_ArithmeticDecoder_create_symbol_model(self->dec, 256);
    self->ic_point_source_id = (IntegerCompressorObject *)_IntegerCompressor_create(self->dec, 16, 1);
    for(int i = 0; i < 256; i++)
        self->m_bit_byte[i] = NULL;
    for(int i = 0; i < 256; i++)
        self->m_classification[i] = NULL;
    for(int i = 0; i < 256; i++)
        self->m_user_data[i] = NULL;
    self->ic_dx = (IntegerCompressorObject *)_IntegerCompressor_create(self->dec, 32, 2);
    self->ic_dy = (IntegerCompressorObject *)_IntegerCompressor_create(self->dec, 32, 22);
    self->ic_z = (IntegerCompressorObject *)_IntegerCompressor_create(self->dec, 32, 20);

    for(int i = 0; i < 16; i++) {
        StreamingMedian5_init(&self->last_x_diff_median5[i]);
        StreamingMedian5_init(&self->last_y_diff_median5[i]);
    }

    for(int i = 0; i < 16; i++)
        self->last_intensity[i] = 0;

    for(int i = 0; i < 8; i++)
        self->last_height[i] = 0;

    for(int i = 0; i < 20; i++)
        self->last_item[i] = 0;
    
    return 0;

}

static int
read_item_compressed_point10_v2__init__(read_item_compressed_point10_v2Object *self, PyObject *args, PyObject *kwds)
{

    PyObject *argdec;
    if (!PyArg_ParseTuple(args, "O!", &ArithmeticDecoder_Type, &argdec)) {
        return -1;
    }
    ArithmeticDecoderObject *dec = (ArithmeticDecoderObject *)argdec;

    return _read_item_compressed_point10_v2__init__(self, (ArithmeticDecoderObject *)dec);
}

static PyObject *
read_item_compressed_point10_v2_get_dec(read_item_compressed_point10_v2Object *self, void *closure)
{
    Py_INCREF(self->dec);
    return (PyObject *)self->dec;
}

static PyObject *
read_item_compressed_point10_v2_get_m_changed_values(read_item_compressed_point10_v2Object *self, void *closure)
{
    Py_INCREF(self->m_changed_values);
    return (PyObject *)self->m_changed_values;
}

static PyObject *
read_item_compressed_point10_v2_get_ic_intensity(read_item_compressed_point10_v2Object *self, void *closure)
{
    Py_INCREF(self->ic_intensity);
    return (PyObject *)self->ic_intensity;
}

static PyObject *
read_item_compressed_point10_v2_get_m_scan_rank(read_item_compressed_point10_v2Object *self, void *closure)
{
    PyObject *result = PyTuple_New(2);
    for(int i = 0; i < 2; i++) {
        Py_INCREF(self->m_scan_rank[i]);
        PyTuple_SetItem(result, i, (PyObject *)self->m_scan_rank[i]);
    }
    return result;
}


static PyMethodDef read_item_compressed_point10_v2_methods[] = {
    {NULL, NULL}  /* Sentinel */
};

PyGetSetDef read_item_compressed_point10_v2_getset[] = {
    {"dec", (getter)read_item_compressed_point10_v2_get_dec, NULL, "decoder", NULL},
    {"m_changed_values", (getter)read_item_compressed_point10_v2_get_m_changed_values, NULL, "m_changed_values", NULL},
    {"ic_intensity", (getter)read_item_compressed_point10_v2_get_ic_intensity, NULL, "ic_intensity", NULL},
    {"m_scan_rank", (getter)read_item_compressed_point10_v2_get_m_scan_rank, NULL, "m_scan_rank", NULL},
    {NULL}  /* Sentinel */
};


static PyTypeObject read_item_compressed_point10_v2_Type = {
    PyVarObject_HEAD_INIT(NULL, 0)
    "cpylaz.read_item_compressed_point10_v2", /*tp_name*/
    sizeof(read_item_compressed_point10_v2Object), /*tp_basicsize*/
    0,                          /*tp_itemsize*/
    /* methods */
    (destructor)read_item_compressed_point10_v2_dealloc,    /*tp_dealloc*/
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
    read_item_compressed_point10_v2_methods,                /*tp_methods*/
    0,                          /*tp_members*/
    read_item_compressed_point10_v2_getset,                          /*tp_getset*/
    0,                          /*tp_base*/
    0,                          /*tp_dict*/
    0,                          /*tp_descr_get*/
    0,                          /*tp_descr_set*/
    0,                          /*tp_dictoffset*/
    (initproc)read_item_compressed_point10_v2__init__,                          /*tp_init*/
    0,                          /*tp_alloc*/
    PyType_GenericNew,                          /*tp_new*/
    0,                          /*tp_free*/
    0,                          /*tp_is_gc*/
};
/* List of functions defined in the module */

static PyMethodDef cpylaz_methods[] = {
    {NULL,              NULL}           /* sentinel */
};

PyDoc_STRVAR(module_doc,
"C implementation of models.");


static int
cpylaz_exec(PyObject *m)
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

    ArithmeticEncoder_Type.tp_base = &PyBaseObject_Type;
    if (PyType_Ready(&ArithmeticEncoder_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "ArithmeticEncoder", (PyObject *)&ArithmeticEncoder_Type);

    ArithmeticDecoder_Type.tp_base = &PyBaseObject_Type;
    if (PyType_Ready(&ArithmeticDecoder_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "ArithmeticDecoder", (PyObject *)&ArithmeticDecoder_Type);

    IntegerCompressor_Type.tp_base = &PyBaseObject_Type;
    if (PyType_Ready(&IntegerCompressor_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "IntegerCompressor", (PyObject *)&IntegerCompressor_Type);

    read_item_compressed_point10_v2_Type.tp_base = &PyBaseObject_Type;
    if (PyType_Ready(&read_item_compressed_point10_v2_Type) < 0)
        goto fail;
    PyModule_AddObject(m, "read_item_compressed_point10_v2", (PyObject *)&read_item_compressed_point10_v2_Type);

    PyModule_AddIntConstant(m, "DM_LENGTH_SHIFT", DM_LENGTH_SHIFT);

    return 0;
 fail:
    Py_XDECREF(m);
    return -1;
}

static struct PyModuleDef_Slot cpylaz_slots[] = {
    {Py_mod_exec, cpylaz_exec},
    {0, NULL},
};

static struct PyModuleDef cpylazmodule = {
    PyModuleDef_HEAD_INIT,
    "cpylaz",
    module_doc,
    0,
    cpylaz_methods,
    cpylaz_slots,
    NULL,
    NULL,
    NULL
};

/* Export function for the module (*must* be called PyInit_cpylaz) */

PyMODINIT_FUNC
PyInit_cpylaz(void)
{
    return PyModuleDef_Init(&cpylazmodule);
}