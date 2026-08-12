/*
 * cpylaz.h -- what the Python binding's translation units share.
 *
 * The binding is split one file per exposed type (cpylaz_*.c), and this is
 * the only header that knows about Python: the laz_* headers stay
 * Python-free, which is what keeps the core portable. Everything here is
 * cross-file plumbing -- the object structs more than one file dereferences,
 * the type objects, the exception, and the few helpers shared between types.
 */
#ifndef CPYLAZ_H
#define CPYLAZ_H

/* see laz_stream.c: required before Python.h for "#" formats below CPython 3.13 */
#define PY_SSIZE_T_CLEAN
#include "Python.h"
#include "structmember.h"

#include "laz_types.h"
#include "laz_stream.h"
#include "laz_arithmetic.h"
#include "laz_intcompressor.h"
#include "laz_readpoint.h"
#include "laz_writepoint.h"
#include "laz_index.h"
#include "laz_indexbuild.h"

/*
 * The exception every decode failure raises. lazpy/__init__.py re-exports
 * this as LazError, so "this file failed to decode" is one catchable
 * category rather than a mix of RuntimeError, ValueError and OSError.
 * Defined in cpylazmodule.c, created at module init.
 */
extern PyObject *LazErrorType;

typedef struct {
    PyObject_HEAD
    LazBitModel m;
} BitModelObject;

typedef struct {
    PyObject_HEAD
    LazSymbolModel *m;
    LazSymbolModel storage;
    PyObject *owner;
} SymbolModelObject;

typedef struct {
    PyObject_HEAD
    LazEncoder e;
    LazOutStream *stream;
    PyObject *fp;
} EncoderObject;

typedef struct {
    PyObject_HEAD
    LazDecoder d;
    LazStream *stream;
    PyObject *fp;
} DecoderObject;

typedef struct {
    PyObject_HEAD
    LazPoint *p;            /* -> storage once detached or copied */
    LazPoint storage;
    U8 *extra;              /* -> extra_storage once detached or copied */
    U32 num_extra;
    U8 *extra_storage;      /* owned, may be NULL */
} PointObject;

extern PyTypeObject BitModel_Type;
extern PyTypeObject SymbolModel_Type;
extern PyTypeObject Encoder_Type;
extern PyTypeObject Decoder_Type;
extern PyTypeObject IntComp_Type;
extern PyTypeObject Point_Type;
extern PyTypeObject Reader_Type;
extern PyTypeObject Writer_Type;
extern PyTypeObject Index_Type;

/* A model over memory owned by `owner`, kept alive by holding it. */
PyObject *SymbolModel_borrow(LazSymbolModel *m, PyObject *owner);

/* The create_symbol_model both coders offer: args is (num_symbols,). */
PyObject *coder_create_symbol_model(PyObject *args, PyObject *compress);

/* None, or NULL with the exception set, per how the encoder's stream is. */
PyObject *encoder_result(EncoderObject *self);

/* The part of LazPoint the item readers fill; everything past it is
 * bookkeeping (the extra-bytes count and pointer), not decoded data. Bounds
 * a read_into target, and is what POINT_LAYOUT publishes as __extent__. */
#define POINT_FIXED_EXTENT (LAZ_POINT_OFFSET_WAVEPACKET + LAZ_WAVEPACKET_SIZE)

PointObject *point_alloc(PyTypeObject *type);

/* A view onto memory owned by `reader`, valid until the reader detaches it. */
PyObject *Point_borrow(LazPoint *p, U8 *extra, U32 num_extra);

/* Copies the currently-viewed values into this object so it can outlive the
 * memory it was pointing at. */
void Point_detach(PointObject *self);

/* The (type, size, version) triples of a LASzip item list, malloc'd. */
int parse_items(PyObject *seq, LazItem **out, U32 *out_n);

/*
 * LAS 1.4 compatibility mode, which the reader undoes and the writer does.
 *
 * A format 6-10 point is written as a legacy one plus five extra bytes -- or
 * seven, with a near-infrared band -- holding what the legacy record cannot
 * express. These say where those bytes are and how wide each is, in the order
 * lazpy/compat.py's CompatibilityLayout hands them over. One statement of that
 * order for both directions.
 */
enum {
    COMPAT_SCAN_ANGLE, COMPAT_EXTENDED_RETURNS, COMPAT_CLASSIFICATION,
    COMPAT_FLAGS_AND_CHANNEL, COMPAT_NIR, COMPAT_ATTRIBUTES
};
extern const U32 compat_widths[COMPAT_ATTRIBUTES];

/*
 * laszip's own expressions for the two directions of the scan angle, kept as
 * they are written in laszip_dll.cpp so the two can be compared: a LAS 1.4
 * angle is hundredths of a degree over 0.6, a legacy rank is degrees.
 *
 * The reader tabulates the first for all 256 ranks rather than dividing once
 * per point, and the writer, which needs the same 256 values, shares that
 * table -- see compat_scan_angle_of_rank().
 */
#define COMPAT_SCAN_ANGLE_OF_RANK(rank) \
    I16_QUANTIZE(((F32)(rank)) / 0.006f)
#define COMPAT_RANK_OF_SCAN_ANGLE(angle) \
    I32_QUANTIZE(0.006f * (F32)(angle))

/* The 256 values of COMPAT_SCAN_ANGLE_OF_RANK, indexed by rank as a U8. */
const I16 *compat_scan_angle_of_rank(void);

/*
 * The five starts out of the `compatibility` argument, checked against the
 * extra bytes they have to lie inside. 1 when there is a layout, 0 when the
 * argument is absent or None, -1 with the exception set.
 */
int parse_compat_starts(PyObject *obj, U32 num_extra_bytes,
                        I32 starts[COMPAT_ATTRIBUTES]);

#endif
