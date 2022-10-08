from utils import unsigned_int
import models
import cpylaz

BM_LENGTH_SHIFT = 13


class ArithmeticEncoder:
    def __init__(self):
        raise NotImplementedError()


class ArithmeticDecoder:
    """An ArithmeticDecoder decodes a stream of symbols using an arithmetic
    model."""
    AC_MAX_LENGTH = 0xFFFFFFFF
    AC_MIN_LENGTH = 0x01000000

    def __init__(self, fp):
        self.fp = fp
        self.length = 0
        self.value = 0

    def start(self):
        self.length = self.AC_MAX_LENGTH

        data = self.fp.read(4)
        self.value = int.from_bytes(data, byteorder='big')

    def _renorm_dec_interval(self):
        """Renormalize the decoder interval."""
        while self.length < self.AC_MIN_LENGTH:
            data = unsigned_int(self.fp.read(1))
            self.value = (self.value << 8) | data
            self.length <<= 8

    def decode_bit(self, m):
        # m is an ArithmeticBitModel
        x = m.bit_0_prob * (self.length >> BM_LENGTH_SHIFT)
        sym = (self.value >= x)

        if sym == 0:
            self.length = x
            m.bit_0_count += 1
        else:
            self.value -= x
            self.length -= x

        if self.length < self.AC_MIN_LENGTH:
            self._renorm_dec_interval()

        m.bits_until_update -= 1  # TODO get the model to handle this
        if m.bits_until_update == 0:
            m.update()

        return sym

    def decode_symbol(self, m):
        # m is an ArithmeticModel

        y = self.length

        # use table lookup for faster decoding
        if m.has_decoder_table():
            self.length >>= cpylaz.DM_LENGTH_SHIFT
            dv = self.value // self.length
            t = dv >> m.table_shift

            # use table to get first symbol
            sym = m.decoder_table_lookup(t)
            n = m.decoder_table_lookup(t+1) + 1

            # finish with bisection search
            while n > sym+1:
                k = (sym + n) >> 1
                if m.distribution_lookup(k) > dv:
                    n = k
                else:
                    sym = k

            # compute products
            x = m.distribution_lookup(sym) * self.length

            if sym != m.last_symbol:
                y = m.distribution_lookup(sym+1) * self.length

        # decode using only multiplications
        else:
            x = sym = 0
            self.length >>= cpylaz.DM_LENGTH_SHIFT
            n = m.num_symbols
            k = n >> 1

            # decode via bisection search
            while k != sym:
                z = self.length * m.distribution_lookup(k)
                if z > self.value:
                    n = k
                    y = z  # value is smaller
                else:
                    sym = k
                    x = z  # value is larger or equal

                k = (sym + n) >> 1

        # update interval
        self.value -= x
        self.length = y - x

        if self.length < self.AC_MIN_LENGTH:
            self._renorm_dec_interval()

        m.increment_symbol_count(sym)

        return sym

    def read_bits(self, bits):
        assert bits > 0 and bits <= 32

        if bits > 19:
            lower = self.read_bits(16)
            upper = self.read_bits(bits-16)
            return (upper << 16) | lower

        self.length >>= bits
        sym = self.value // (self.length)
        self.value = self.value % self.length

        if self.length < self.AC_MIN_LENGTH:
            self._renorm_dec_interval()

        return sym

    def read_int(self):
        return self.read_bits(32)

    def create_symbol_model(self, num_symbols):
        return cpylaz.ArithmeticModel(num_symbols, False)

    def __repr__(self):
        return f"ArithmeticDecoder(value={self.value}, length={self.length})"
