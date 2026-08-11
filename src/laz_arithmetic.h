/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_arithmetic.h -- arithmetic coder models, decoder and encoder.
 *
 * Ported from LASzip's arithmeticmodel.{hpp,cpp}, arithmeticdecoder.cpp and
 * arithmeticencoder.cpp, which in turn derive from Said & Pearlman's reference
 * implementation. The integer arithmetic here is bit-exact by necessity: any
 * deviation desynchronises the bitstream rather than merely degrading it.
 *
 * The models are shared between the two directions. Only the decoder builds a
 * decoder table, which is why laz_symbol_model_setup takes a `compress` flag.
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

/*
 * The encoder cannot emit bytes as it produces them: a later symbol can carry
 * into an already-produced byte, and the carry may have to ripple back through
 * a run of 0xFF. Bytes therefore accumulate in a ring of two AC_BUFFER_SIZE
 * halves, and a half is handed to the stream only once the other half has been
 * filled, which puts AC_BUFFER_SIZE bytes of slack behind the write cursor.
 *
 * [outbuffer, endbuffer) is the ring; outbyte is the write cursor and endbyte
 * the end of the half currently being filled.
 */
#define AC_BUFFER_SIZE 1024

typedef struct {
    LazOutStream *stream;   /* borrowed; not owned. NULL until init/after done */
    U32 base;
    U32 length;
    U8 *outbuffer;          /* owned, 2 * AC_BUFFER_SIZE bytes */
    U8 *endbuffer;
    U8 *outbyte;
    U8 *endbyte;
} LazEncoder;

/*
 * Model memory is allocated through these two rather than malloc/calloc
 * directly, so that a test can make it run out on demand.
 *
 * Every model allocation is lazy -- laz_symbol_model_setup only records the
 * shape, and the buffer appears on the first laz_symbol_model_init, which for
 * the on-demand banks in laz_item.h happens partway through decoding a chunk.
 * The failure paths that hang off those allocations are therefore reachable
 * only under genuine memory exhaustion, and nothing else in the test suite can
 * reach them.
 *
 * laz_alloc_fail_after(n) lets the next n allocations through and fails every
 * one after that; -1, the default, never fails. It is a debugging hook, not
 * part of the reading or writing interface, and it is not thread-safe.
 */
void *laz_model_alloc(size_t size);
void *laz_model_calloc(size_t n, size_t size);
void laz_alloc_fail_after(I64 n);

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

/* Allocates the ring buffer. Returns LAZ_FALSE on allocation failure. */
BOOL laz_encoder_setup(LazEncoder *e);
void laz_encoder_init(LazEncoder *e, LazOutStream *stream);
/* Flushes the interval and the ring, then writes the two or three trailing
 * zero bytes the decoder's initial fill expects, and releases the stream. */
void laz_encoder_done(LazEncoder *e);
void laz_encoder_free(LazEncoder *e);

void laz_encode_bit(LazEncoder *e, LazBitModel *m, U32 sym);
void laz_encode_symbol(LazEncoder *e, LazSymbolModel *m, U32 sym);
void laz_write_bits(LazEncoder *e, U32 bits, U32 sym);
static inline void laz_write_int(LazEncoder *e, U32 sym) { laz_write_bits(e, 32, sym); }
void laz_write_int64(LazEncoder *e, U64 sym);

#endif /* LAZ_ARITHMETIC_H */
