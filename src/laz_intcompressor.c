/*
 * Derived from LASzip (https://github.com/LASzip/LASzip), integercompressor.cpp.
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

#include "laz_intcompressor.h"

static void ic_setup(LazIntCompressor *ic, U32 bits, U32 contexts,
                     U32 bits_high, U32 range)
{
    memset(ic, 0, sizeof(*ic));
    ic->bits = bits;
    ic->contexts = contexts;
    ic->bits_high = bits_high;
    ic->range = range;

    if (range) {                 /* the corrector's significant bits and range */
        U32 r = range;
        ic->corr_bits = 0;
        ic->corr_range = range;
        while (r) { r >>= 1; ic->corr_bits++; }
        if (ic->corr_range == (1u << (ic->corr_bits - 1))) ic->corr_bits--;
        ic->corr_min = -((I32)(ic->corr_range / 2));
        ic->corr_max = ic->corr_min + ic->corr_range - 1;
    } else if (bits && bits < 32) {
        ic->corr_bits = bits;
        ic->corr_range = 1u << bits;
        ic->corr_min = -((I32)(ic->corr_range / 2));
        ic->corr_max = ic->corr_min + ic->corr_range - 1;
    } else {
        ic->corr_bits = 32;
        ic->corr_range = 0;
        ic->corr_min = I32_MIN;
        ic->corr_max = I32_MAX;
    }

    ic->k = 0;
    ic->models_created = LAZ_FALSE;
}

void laz_ic_setup_dec(LazIntCompressor *ic, LazDecoder *dec, U32 bits, U32 contexts,
                      U32 bits_high, U32 range)
{
    ic_setup(ic, bits, contexts, bits_high, range);
    ic->dec = dec;
}

void laz_ic_setup_enc(LazIntCompressor *ic, LazEncoder *enc, U32 bits, U32 contexts,
                      U32 bits_high, U32 range)
{
    ic_setup(ic, bits, contexts, bits_high, range);
    ic->enc = enc;
}

/* The models differ between the two directions only in whether they build a
 * decoder table, so the whole of init lives here and takes the direction. */
static BOOL ic_init_models(LazIntCompressor *ic, BOOL compress)
{
    U32 i;

    if (!ic->models_created) {
        /* each half is skipped if a previous, failed call already made it, so
         * that retrying after an allocation failure does not leak the other */
        if (!ic->m_bits) {
            ic->m_bits = laz_symbol_models_new(ic->contexts, ic->corr_bits + 1, compress);
            if (!ic->m_bits) return LAZ_FALSE;
        }

        /* entry 0 is the bit model held separately; 1..corr_bits are symbol models */
        ic->m_corrector = (LazSymbolModel *)laz_model_calloc(ic->corr_bits + 1,
                                                            sizeof(LazSymbolModel));
        if (!ic->m_corrector) return LAZ_FALSE;
        for (i = 1; i <= ic->corr_bits; i++) {
            U32 num_symbols = (i <= ic->bits_high) ? (1u << i) : (1u << ic->bits_high);
            laz_symbol_model_setup(&ic->m_corrector[i], num_symbols, compress);
        }
        ic->models_created = LAZ_TRUE;
    }

    for (i = 0; i < ic->contexts; i++) {
        if (!laz_symbol_model_init(&ic->m_bits[i], NULL)) return LAZ_FALSE;
    }
    laz_bit_model_init(&ic->m_corrector0);
    for (i = 1; i <= ic->corr_bits; i++) {
        if (!laz_symbol_model_init(&ic->m_corrector[i], NULL)) return LAZ_FALSE;
    }
    return LAZ_TRUE;
}

BOOL laz_ic_init_decompressor(LazIntCompressor *ic)
{
    return ic_init_models(ic, LAZ_FALSE);
}

BOOL laz_ic_init_compressor(LazIntCompressor *ic)
{
    return ic_init_models(ic, LAZ_TRUE);
}

void laz_ic_free(LazIntCompressor *ic)
{
    if (ic->m_bits) {
        laz_symbol_models_free(ic->m_bits, ic->contexts);
        ic->m_bits = NULL;
    }
    if (ic->m_corrector) {
        U32 i;
        for (i = 1; i <= ic->corr_bits; i++) laz_symbol_model_free(&ic->m_corrector[i]);
        free(ic->m_corrector);
        ic->m_corrector = NULL;
    }
    ic->models_created = LAZ_FALSE;
}

/* Decodes which magnitude bucket the corrector falls in, then its exact
 * location within that bucket. */
static I32 read_corrector(LazIntCompressor *ic, LazSymbolModel *m_bits)
{
    I32 c;

    ic->k = laz_decode_symbol(ic->dec, m_bits);

    if (ic->k) {                    /* c is either smaller than 0 or bigger than 1 */
        if (ic->k < 32) {
            if (ic->k <= ic->bits_high) {
                c = (I32)laz_decode_symbol(ic->dec, &ic->m_corrector[ic->k]);
            } else {
                /* high bits through the model, low bits raw */
                U32 k1 = ic->k - ic->bits_high;
                c = (I32)laz_decode_symbol(ic->dec, &ic->m_corrector[ic->k]);
                c = (c << k1) | (I32)laz_read_bits(ic->dec, k1);
            }
            /* translate c back into its correct interval */
            if (c >= (1 << (ic->k - 1))) {
                c += 1;
            } else {
                c -= ((1 << ic->k) - 1);
            }
        } else {
            c = ic->corr_min;
        }
    } else {                        /* c is either 0 or 1 */
        c = (I32)laz_decode_bit(ic->dec, &ic->m_corrector0);
    }

    return c;
}

I32 laz_ic_decompress(LazIntCompressor *ic, I32 pred, U32 context)
{
    I32 real = pred + read_corrector(ic, &ic->m_bits[context]);
    if (real < 0) real += ic->corr_range;
    else if ((U32)real >= ic->corr_range) real -= ic->corr_range;
    return real;
}

/* The exact inverse of read_corrector: k is the tightest bucket holding c,
 * and within it c is coded either whole or, once k passes bits_high, as
 * modelled high bits followed by raw low bits. */
static void write_corrector(LazIntCompressor *ic, I32 c, LazSymbolModel *m_bits)
{
    U32 c1;

    /* find the tightest interval [-(2^k - 1), 2^k] that contains c. The
     * magnitude is taken in U32 so that c == I32_MIN does not overflow. */
    ic->k = 0;
    c1 = (c <= 0) ? (U32)(-(I64)c) : (U32)c - 1;
    while (c1) { c1 >>= 1; ic->k++; }

    laz_encode_symbol(ic->enc, m_bits, ic->k);

    if (ic->k) {                    /* c is either smaller than 0 or bigger than 1 */
        /* k == 32 means c == corr_min, which the decoder recovers from k alone */
        if (ic->k < 32) {
            /* translate c into [0, 2^k - 1]; in U32 because k reaches 31 */
            U32 u = (c < 0) ? (U32)c + ((1u << ic->k) - 1) : (U32)c - 1;

            if (ic->k <= ic->bits_high) {
                laz_encode_symbol(ic->enc, &ic->m_corrector[ic->k], u);
            } else {
                /* high bits through the model, low bits raw */
                U32 k1 = ic->k - ic->bits_high;
                laz_encode_symbol(ic->enc, &ic->m_corrector[ic->k], u >> k1);
                laz_write_bits(ic->enc, k1, u & ((1u << k1) - 1));
            }
        }
    } else {                        /* c is either 0 or 1 */
        laz_encode_bit(ic->enc, &ic->m_corrector0, (U32)c);
    }
}

void laz_ic_compress(LazIntCompressor *ic, I32 pred, I32 real, U32 context)
{
    /* the raw corrector lies in [-(corr_range - 1), corr_range - 1]; fold it
     * into [corr_min, corr_max] so that k stays within corr_bits */
    I32 corr = (I32)((U32)real - (U32)pred);
    if (corr < ic->corr_min) corr = (I32)((U32)corr + ic->corr_range);
    else if (corr > ic->corr_max) corr = (I32)((U32)corr - ic->corr_range);
    write_corrector(ic, corr, &ic->m_bits[context]);
}
