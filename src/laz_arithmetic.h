/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_arithmetic.h -- arithmetic coder models and decoder.
 *
 * Ported from LASzip's arithmeticmodel.{hpp,cpp} and arithmeticdecoder.cpp,
 * which in turn derive from Said & Pearlman's reference implementation. The
 * integer arithmetic here is bit-exact by necessity: any deviation desynchronises
 * the bitstream rather than merely degrading it.
 */
#ifndef LAZ_ARITHMETIC_H
#define LAZ_ARITHMETIC_H

#include "laz_types.h"
#include "laz_stream.h"

#define BM_LENGTH_SHIFT 13
#define BM_MAX_COUNT    (1u << BM_LENGTH_SHIFT)
#define DM_LENGTH_SHIFT 15
#define DM_MAX_COUNT    (1u << DM_LENGTH_SHIFT)

#define AC_MAX_LENGTH 0xFFFFFFFFu
#define AC_MIN_LENGTH 0x01000000u

typedef struct {
    U32 update_cycle;
    U32 bits_until_update;
    U32 bit_0_prob;
    U32 bit_0_count;
    U32 bit_count;
} LazBitModel;

typedef struct {
    U32 *distribution;      /* NULL until first init */
    U32 *symbol_count;
    U32 *decoder_table;     /* NULL for small alphabets */
    U32 total_count;
    U32 update_cycle;
    U32 symbols_until_update;
    U32 num_symbols;
    U32 last_symbol;
    U32 table_size;
    U32 table_shift;
    BOOL compress;
} LazSymbolModel;

typedef struct {
    LazStream *stream;      /* borrowed; not owned */
    U32 value;
    U32 length;
} LazDecoder;

void laz_bit_model_init(LazBitModel *m);
void laz_bit_model_update(LazBitModel *m);

/* Sets up an unused model. Allocation is deferred to the first laz_symbol_model_init. */
void laz_symbol_model_setup(LazSymbolModel *m, U32 num_symbols, BOOL compress);
/* (Re)initialises counts. Allocates on first call. table may be NULL.
 * Returns LAZ_FALSE on allocation failure or an out-of-range symbol count. */
BOOL laz_symbol_model_init(LazSymbolModel *m, const U32 *table);
void laz_symbol_model_free(LazSymbolModel *m);
/* Recomputes the distribution (and decoder table) from the current counts. */
void laz_symbol_model_update(LazSymbolModel *m);

/* Allocates an array of `n` symbol models, each set up for num_symbols. */
LazSymbolModel *laz_symbol_models_new(U32 n, U32 num_symbols, BOOL compress);
void laz_symbol_models_free(LazSymbolModel *models, U32 n);

void laz_decoder_setup(LazDecoder *d, LazStream *stream);
/* really_init false only hands over the stream without consuming the 4 initial
 * bytes; the layered v3/v4 path relies on that. */
void laz_decoder_init(LazDecoder *d, LazStream *stream, BOOL really_init);
void laz_decoder_done(LazDecoder *d);

U32 laz_decode_bit(LazDecoder *d, LazBitModel *m);
U32 laz_decode_symbol(LazDecoder *d, LazSymbolModel *m);
U32 laz_read_bits(LazDecoder *d, U32 bits);
static inline U32 laz_read_int(LazDecoder *d) { return laz_read_bits(d, 32); }
U64 laz_read_int64(LazDecoder *d);

#endif /* LAZ_ARITHMETIC_H */
