/*
 * Derived from LASzip (https://github.com/LASzip/LASzip).
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

/*
 * laz_intcompressor.h -- the entropy-coded integer decompressor.
 *
 * Ported from LASzip's integercompressor.{hpp,cpp}. Every numeric field in a
 * LAZ point ultimately comes through here: a symbol model picks the magnitude
 * bucket k, then a per-k model (plus raw bits for large k) locates the value
 * inside that bucket.
 *
 * One instance codes in one direction: set it up with a decoder or with an
 * encoder, never both.
 */
#ifndef LAZ_INTCOMPRESSOR_H
#define LAZ_INTCOMPRESSOR_H

#include "laz_arithmetic.h"

typedef struct {
    LazDecoder *dec;        /* borrowed; NULL when compressing */
    LazEncoder *enc;        /* borrowed; NULL when decompressing */
    U32 k;                  /* magnitude bucket of the last corrector coded */

    U32 bits;
    U32 contexts;
    U32 bits_high;
    U32 range;

    U32 corr_bits;
    U32 corr_range;
    I32 corr_min;
    I32 corr_max;

    LazSymbolModel *m_bits;      /* [contexts] */
    LazBitModel m_corrector0;
    LazSymbolModel *m_corrector; /* [corr_bits+1], entry 0 unused */
    BOOL models_created;
} LazIntCompressor;

/* bits_high defaults to 8 and range to 0 in LASzip; pass them explicitly. */
void laz_ic_setup_dec(LazIntCompressor *ic, LazDecoder *dec, U32 bits, U32 contexts,
                      U32 bits_high, U32 range);
void laz_ic_setup_enc(LazIntCompressor *ic, LazEncoder *enc, U32 bits, U32 contexts,
                      U32 bits_high, U32 range);
BOOL laz_ic_init_decompressor(LazIntCompressor *ic);
BOOL laz_ic_init_compressor(LazIntCompressor *ic);
void laz_ic_free(LazIntCompressor *ic);

I32 laz_ic_decompress(LazIntCompressor *ic, I32 pred, U32 context);
void laz_ic_compress(LazIntCompressor *ic, I32 pred, I32 real, U32 context);

#endif /* LAZ_INTCOMPRESSOR_H */
