#include "laz_arithmetic.h"

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
            m->distribution = (U32 *)malloc(
                (2 * (size_t)m->num_symbols + m->table_size + 2) * sizeof(U32));
            if (m->distribution == NULL) return LAZ_FALSE;
            m->decoder_table = m->distribution + 2 * m->num_symbols;
        } else {
            m->decoder_table = NULL;
            m->table_size = m->table_shift = 0;
            m->distribution = (U32 *)malloc(2 * (size_t)m->num_symbols * sizeof(U32));
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
    LazSymbolModel *models = (LazSymbolModel *)calloc(n, sizeof(LazSymbolModel));
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
    d->length = 0;
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
