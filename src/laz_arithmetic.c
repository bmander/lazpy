/*
 * Derived from LASzip (https://github.com/LASzip/LASzip), arithmeticmodel.cpp,
 * arithmeticdecoder.cpp and arithmeticencoder.cpp.
 * Copyright (c) 2007-2022, rapidlasso GmbH -- fast tools to catch reality
 * Licensed under the Apache License, Version 2.0; see LICENSE and NOTICE.
 *
 * Modified: translated from C++ to C and restructured.
 */

#include "laz_arithmetic.h"

/* ------------------------------------------------------- model allocation */

/* Number of allocations still allowed, or -1 for "no limit". See the comment
 * on laz_alloc_fail_after in the header. */
static I64 alloc_countdown = -1;

void laz_alloc_fail_after(I64 n)
{
    alloc_countdown = n;
}

static BOOL alloc_should_fail(void)
{
    if (alloc_countdown < 0) return LAZ_FALSE;
    if (alloc_countdown == 0) return LAZ_TRUE;
    alloc_countdown--;
    return LAZ_FALSE;
}

void *laz_model_alloc(size_t size)
{
    if (alloc_should_fail()) return NULL;
    return malloc(size);
}

void *laz_model_calloc(size_t n, size_t size)
{
    if (alloc_should_fail()) return NULL;
    return calloc(n, size);
}

/* -------------------------------------------------------------- bit model */

void laz_bit_model_init(LazBitModel *m)
{
    /* equiprobable model */
    m->bit_0_count = 1;
    m->bit_count = 2;
    m->bit_0_prob = 1u << (BM_LENGTH_SHIFT - 1);
    /* start with frequent updates */
    m->update_cycle = m->bits_until_update = 4;
}

void laz_bit_model_update(LazBitModel *m)
{
    /* halve counts when a threshold is reached */
    if ((m->bit_count += m->update_cycle) > BM_MAX_COUNT) {
        m->bit_count = (m->bit_count + 1) >> 1;
        m->bit_0_count = (m->bit_0_count + 1) >> 1;
        if (m->bit_0_count == m->bit_count) m->bit_count += 1;
    }

    /* compute scaled bit 0 probability */
    {
        U32 scale = 0x80000000u / m->bit_count;
        m->bit_0_prob = (m->bit_0_count * scale) >> (31 - BM_LENGTH_SHIFT);
    }

    /* update frequency of model updates */
    m->update_cycle = (5 * m->update_cycle) >> 2;
    if (m->update_cycle > 64) m->update_cycle = 64;
    m->bits_until_update = m->update_cycle;
}

/* ----------------------------------------------------------- symbol model */

void laz_symbol_model_update(LazSymbolModel *m)
{
    U32 k, sum = 0, s = 0, scale;

    /* halve counts when a threshold is reached */
    if ((m->total_count += m->update_cycle) > DM_MAX_COUNT) {
        m->total_count = 0;
        for (k = 0; k < m->num_symbols; k++) {
            m->total_count += (m->symbol_count[k] = (m->symbol_count[k] + 1) >> 1);
        }
    }

    /* compute cumulative distribution and, when present, the decoder table */
    scale = 0x80000000u / m->total_count;

    if (m->compress || m->table_size == 0) {
        for (k = 0; k < m->num_symbols; k++) {
            m->distribution[k] = (scale * sum) >> (31 - DM_LENGTH_SHIFT);
            sum += m->symbol_count[k];
        }
    } else {
        for (k = 0; k < m->num_symbols; k++) {
            U32 w;
            m->distribution[k] = (scale * sum) >> (31 - DM_LENGTH_SHIFT);
            sum += m->symbol_count[k];
            w = m->distribution[k] >> m->table_shift;
            while (s < w) m->decoder_table[++s] = k - 1;
        }
        m->decoder_table[0] = 0;
        while (s <= m->table_size) m->decoder_table[++s] = m->num_symbols - 1;
    }

    /* set frequency of model updates */
    m->update_cycle = (5 * m->update_cycle) >> 2;
    {
        U32 max_cycle = (m->num_symbols + 6) << 3;
        if (m->update_cycle > max_cycle) m->update_cycle = max_cycle;
    }
    m->symbols_until_update = m->update_cycle;
}

void laz_symbol_model_setup(LazSymbolModel *m, U32 num_symbols, BOOL compress)
{
    memset(m, 0, sizeof(*m));
    m->num_symbols = num_symbols;
    m->compress = compress;
}

BOOL laz_symbol_model_init(LazSymbolModel *m, const U32 *table)
{
    U32 k;

    if (m->distribution == NULL) {
        if (m->num_symbols < 2 || m->num_symbols > (1u << 11)) return LAZ_FALSE;
        m->last_symbol = m->num_symbols - 1;

        if (!m->compress && m->num_symbols > 16) {
            U32 table_bits = 3;
            while (m->num_symbols > (1u << (table_bits + 2))) ++table_bits;
            m->table_size = 1u << table_bits;
            m->table_shift = DM_LENGTH_SHIFT - table_bits;
            /* one block: [distribution | symbol_count | decoder_table] */
            m->distribution = (U32 *)laz_model_alloc(
                (2 * (size_t)m->num_symbols + m->table_size + 2) * sizeof(U32));
            if (m->distribution == NULL) return LAZ_FALSE;
            m->decoder_table = m->distribution + 2 * m->num_symbols;
        } else {
            m->decoder_table = NULL;
            m->table_size = m->table_shift = 0;
            m->distribution = (U32 *)laz_model_alloc(2 * (size_t)m->num_symbols * sizeof(U32));
            if (m->distribution == NULL) return LAZ_FALSE;
        }
        m->symbol_count = m->distribution + m->num_symbols;
    }

    m->total_count = 0;
    m->update_cycle = m->num_symbols;
    if (table) {
        for (k = 0; k < m->num_symbols; k++) m->symbol_count[k] = table[k];
    } else {
        for (k = 0; k < m->num_symbols; k++) m->symbol_count[k] = 1;
    }

    laz_symbol_model_update(m);
    m->symbols_until_update = m->update_cycle = (m->num_symbols + 6) >> 1;
    return LAZ_TRUE;
}

void laz_symbol_model_free(LazSymbolModel *m)
{
    if (m->distribution) free(m->distribution);
    m->distribution = NULL;
    m->symbol_count = NULL;
    m->decoder_table = NULL;
}

LazSymbolModel *laz_symbol_models_new(U32 n, U32 num_symbols, BOOL compress)
{
    U32 i;
    LazSymbolModel *models = (LazSymbolModel *)laz_model_calloc(n, sizeof(LazSymbolModel));
    if (!models) return NULL;
    for (i = 0; i < n; i++) laz_symbol_model_setup(&models[i], num_symbols, compress);
    return models;
}

void laz_symbol_models_free(LazSymbolModel *models, U32 n)
{
    U32 i;
    if (!models) return;
    for (i = 0; i < n; i++) laz_symbol_model_free(&models[i]);
    free(models);
}

/* ---------------------------------------------------------------- decoder */

void laz_decoder_setup(LazDecoder *d, LazStream *stream)
{
    d->stream = stream;
    d->value = 0;
    /*
     * The interval length starts where init would put it, not at zero.
     *
     * A decoder is meant to be init'd before it is read, and every decoder of
     * a well-formed file is. A malformed one can reach a decode first -- a
     * layered chunk that declares no bytes for a layer the reader goes on to
     * read from -- and the first thing laz_decode_symbol does is divide by
     * this, so a zero here is the process dying on SIGFPE rather than the
     * file being reported as corrupt. Decoding from a stream that has nothing
     * in it sets eof, which is what overran() already turns into an error.
     */
    d->length = AC_MAX_LENGTH;
}

void laz_decoder_init(LazDecoder *d, LazStream *stream, BOOL really_init)
{
    d->stream = stream;
    d->length = AC_MAX_LENGTH;
    if (really_init) {
        d->value = (laz_stream_get_byte(stream) << 24);
        d->value |= (laz_stream_get_byte(stream) << 16);
        d->value |= (laz_stream_get_byte(stream) << 8);
        d->value |= (laz_stream_get_byte(stream));
    }
}

void laz_decoder_done(LazDecoder *d)
{
    d->stream = NULL;
}

static inline void renorm_dec_interval(LazDecoder *d)
{
    do {
        d->value = (d->value << 8) | laz_stream_get_byte(d->stream);
    } while ((d->length <<= 8) < AC_MIN_LENGTH);
}

U32 laz_decode_bit(LazDecoder *d, LazBitModel *m)
{
    U32 x = m->bit_0_prob * (d->length >> BM_LENGTH_SHIFT);   /* product l x p0 */
    U32 sym = (d->value >= x);

    if (sym == 0) {
        d->length = x;
        ++m->bit_0_count;
    } else {
        d->value -= x;
        d->length -= x;
    }

    if (d->length < AC_MIN_LENGTH) renorm_dec_interval(d);
    if (--m->bits_until_update == 0) laz_bit_model_update(m);

    return sym;
}

U32 laz_decode_symbol(LazDecoder *d, LazSymbolModel *m)
{
    U32 n, sym, x, y = d->length;

    if (m->decoder_table) {                 /* table look-up for faster decoding */
        U32 dv = d->value / (d->length >>= DM_LENGTH_SHIFT);
        U32 t = dv >> m->table_shift;

        sym = m->decoder_table[t];
        n = m->decoder_table[t + 1] + 1;

        while (n > sym + 1) {               /* finish with bisection search */
            U32 k = (sym + n) >> 1;
            if (m->distribution[k] > dv) n = k; else sym = k;
        }

        x = m->distribution[sym] * d->length;
        if (sym != m->last_symbol) y = m->distribution[sym + 1] * d->length;
    } else {                                /* decode using only multiplications */
        U32 k;
        x = sym = 0;
        d->length >>= DM_LENGTH_SHIFT;
        k = (n = m->num_symbols) >> 1;

        do {
            U32 z = d->length * m->distribution[k];
            if (z > d->value) {
                n = k;
                y = z;                      /* value is smaller */
            } else {
                sym = k;
                x = z;                      /* value is larger or equal */
            }
        } while ((k = (sym + n) >> 1) != sym);
    }

    d->value -= x;
    d->length = y - x;

    if (d->length < AC_MIN_LENGTH) renorm_dec_interval(d);

    ++m->symbol_count[sym];
    if (--m->symbols_until_update == 0) laz_symbol_model_update(m);

    return sym;
}

U32 laz_read_bits(LazDecoder *d, U32 bits)
{
    U32 sym;

    if (bits > 19) {
        U32 tmp = laz_read_bits(d, 16);
        bits = bits - 16;
        return (laz_read_bits(d, bits) << 16) | tmp;
    }

    sym = d->value / (d->length >>= bits);
    d->value -= d->length * sym;

    if (d->length < AC_MIN_LENGTH) renorm_dec_interval(d);

    return sym;
}

U64 laz_read_int64(LazDecoder *d)
{
    U64 lower = laz_read_int(d);
    U64 upper = laz_read_int(d);
    return (upper << 32) | lower;
}

/* ---------------------------------------------------------------- encoder */

BOOL laz_encoder_setup(LazEncoder *e)
{
    memset(e, 0, sizeof(*e));
    e->outbuffer = (U8 *)malloc(2 * AC_BUFFER_SIZE);
    if (e->outbuffer == NULL) return LAZ_FALSE;
    e->endbuffer = e->outbuffer + 2 * AC_BUFFER_SIZE;
    return LAZ_TRUE;
}

void laz_encoder_init(LazEncoder *e, LazOutStream *stream)
{
    e->stream = stream;
    e->base = 0;
    e->length = AC_MAX_LENGTH;
    e->outbyte = e->outbuffer;
    /* the first half to be handed off is the whole ring: nothing has been
     * written yet, so there is no history to keep behind the cursor */
    e->endbyte = e->endbuffer;
}

void laz_encoder_free(LazEncoder *e)
{
    free(e->outbuffer);
    e->outbuffer = e->endbuffer = e->outbyte = e->endbyte = NULL;
}

/* Hands the just-filled half to the stream and starts filling the other. */
static void manage_outbuffer(LazEncoder *e)
{
    if (e->outbyte == e->endbuffer) e->outbyte = e->outbuffer;
    laz_outstream_put_bytes(e->stream, e->outbyte, AC_BUFFER_SIZE);
    e->endbyte = e->outbyte + AC_BUFFER_SIZE;
}

/*
 * Adds the carry out of `base` to the last byte produced, rippling back
 * through a run of 0xFF. The run cannot be longer than the ring, because a
 * byte only leaves the ring once AC_BUFFER_SIZE further bytes have been
 * produced, and the interval width guarantees a non-0xFF byte long before then.
 */
static inline void propagate_carry(LazEncoder *e)
{
    U8 *p = (e->outbyte == e->outbuffer) ? e->endbuffer - 1 : e->outbyte - 1;
    while (*p == 0xFFu) {
        *p = 0;
        if (p == e->outbuffer) p = e->endbuffer;
        p--;
    }
    ++*p;
}

static inline void renorm_enc_interval(LazEncoder *e)
{
    do {                                    /* output and discard top byte */
        *e->outbyte++ = (U8)(e->base >> 24);
        if (e->outbyte == e->endbyte) manage_outbuffer(e);
        e->base <<= 8;
    } while ((e->length <<= 8) < AC_MIN_LENGTH);
}

void laz_encode_bit(LazEncoder *e, LazBitModel *m, U32 sym)
{
    U32 x = m->bit_0_prob * (e->length >> BM_LENGTH_SHIFT);   /* product l x p0 */

    if (sym == 0) {
        e->length = x;
        ++m->bit_0_count;
    } else {
        U32 init_base = e->base;
        e->base += x;
        e->length -= x;
        if (init_base > e->base) propagate_carry(e);          /* overflow = carry */
    }

    if (e->length < AC_MIN_LENGTH) renorm_enc_interval(e);
    if (--m->bits_until_update == 0) laz_bit_model_update(m);
}

void laz_encode_symbol(LazEncoder *e, LazSymbolModel *m, U32 sym)
{
    U32 x, init_base = e->base;

    if (sym == m->last_symbol) {
        x = m->distribution[sym] * (e->length >> DM_LENGTH_SHIFT);
        e->base += x;
        e->length -= x;                     /* no second product needed */
    } else {
        x = m->distribution[sym] * (e->length >>= DM_LENGTH_SHIFT);
        e->base += x;
        e->length = m->distribution[sym + 1] * e->length - x;
    }

    if (init_base > e->base) propagate_carry(e);
    if (e->length < AC_MIN_LENGTH) renorm_enc_interval(e);

    ++m->symbol_count[sym];
    if (--m->symbols_until_update == 0) laz_symbol_model_update(m);
}

void laz_write_bits(LazEncoder *e, U32 bits, U32 sym)
{
    U32 init_base;

    /* mirrors laz_read_bits: the low 16 bits go first, then the rest */
    if (bits > 19) {
        laz_write_bits(e, 16, sym & 0xFFFFu);
        sym >>= 16;
        bits -= 16;
    }

    init_base = e->base;
    e->base += sym * (e->length >>= bits);

    if (init_base > e->base) propagate_carry(e);
    if (e->length < AC_MIN_LENGTH) renorm_enc_interval(e);
}

/* mirrors laz_read_int64: the low word first, then the high */
void laz_write_int64(LazEncoder *e, U64 sym)
{
    laz_write_int(e, (U32)(sym & 0xFFFFFFFFu));
    laz_write_int(e, (U32)(sym >> 32));
}

void laz_encoder_done(LazEncoder *e)
{
    U32 init_base = e->base;
    BOOL another_byte = LAZ_TRUE;

    /* pick a final base inside the current interval that ends in zero bytes,
     * so the decoder's four-byte initial fill sees what it expects */
    if (e->length > 2 * AC_MIN_LENGTH) {
        e->base += AC_MIN_LENGTH;
        e->length = AC_MIN_LENGTH >> 1;             /* one more byte */
    } else {
        e->base += AC_MIN_LENGTH >> 1;
        e->length = AC_MIN_LENGTH >> 9;             /* two more bytes */
        another_byte = LAZ_FALSE;
    }

    if (init_base > e->base) propagate_carry(e);
    renorm_enc_interval(e);                 /* flushes the remaining bytes */

    /* endbyte still pointing at the end of the ring means nothing has been
     * handed off yet; otherwise the far half holds the older bytes */
    if (e->endbyte != e->endbuffer) {
        laz_outstream_put_bytes(e->stream, e->outbuffer + AC_BUFFER_SIZE, AC_BUFFER_SIZE);
    }
    if (e->outbyte != e->outbuffer) {
        laz_outstream_put_bytes(e->stream, e->outbuffer, e->outbyte - e->outbuffer);
    }

    /* two or three zero bytes, to stay in sync with the decoder */
    {
        static const U8 zeros[3] = {0, 0, 0};
        laz_outstream_put_bytes(e->stream, zeros, another_byte ? 3 : 2);
    }

    e->stream = NULL;
}
