/*
 * laz_intcompressor.h -- the entropy-coded integer decompressor.
 *
 * Ported from LASzip's integercompressor.{hpp,cpp}. Every numeric field in a
 * LAZ point ultimately comes through here: a symbol model picks the magnitude
 * bucket k, then a per-k model (plus raw bits for large k) locates the value
 * inside that bucket.
 */
#ifndef LAZ_INTCOMPRESSOR_H
#define LAZ_INTCOMPRESSOR_H

#include "laz_arithmetic.h"

typedef struct {
    LazDecoder *dec;        /* borrowed */
    U32 k;                  /* magnitude bucket of the last corrector read */

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
void laz_ic_setup(LazIntCompressor *ic, LazDecoder *dec, U32 bits, U32 contexts,
                  U32 bits_high, U32 range);
BOOL laz_ic_init_decompressor(LazIntCompressor *ic);
void laz_ic_free(LazIntCompressor *ic);

I32 laz_ic_decompress(LazIntCompressor *ic, I32 pred, U32 context);

#endif /* LAZ_INTCOMPRESSOR_H */
