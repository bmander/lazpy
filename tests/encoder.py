from lazpy._utils import unsigned_int
from lazpy import _cpylaz as cpylaz

BM_LENGTH_SHIFT = 13

U32_MASK = 0xFFFFFFFF

# the interval bounds, shared by the encoder and the decoder: they have to
# stay identical or the two desync
AC_MAX_LENGTH = 0xFFFFFFFF
AC_MIN_LENGTH = 0x01000000

# Bytes accumulate in a ring of two halves of this size. A half is handed to
# the file only once the other half has been filled, which keeps that many
# bytes of slack behind the write cursor for a carry to ripple back into.
AC_BUFFER_SIZE = 1024


class ArithmeticEncoder:
    """An ArithmeticEncoder is the inverse of ArithmeticDecoder: it turns a
    sequence of symbols back into the byte stream the decoder consumes."""

    def __init__(self, fp):
        self.fp = fp
        self.base = 0
        self.length = 0
        self.outbuffer = bytearray(2 * AC_BUFFER_SIZE)
        self.outbyte = 0
        self.endbyte = 2 * AC_BUFFER_SIZE

    def start(self):
        self.base = 0
        self.length = AC_MAX_LENGTH
        self.outbyte = 0
        # nothing has been produced yet, so there is no history to keep and
        # the first half to hand off is the whole ring
        self.endbyte = 2 * AC_BUFFER_SIZE

    def _check_started(self):
        # an interval of zero width means start() has not been called, or
        # done() has already consumed it; encoding into it would spin in
        # renormalization forever
        if self.length == 0:
            raise RuntimeError(
                "Encoder interval is empty - have you called start()?")

    def _propagate_carry(self):
        """Add the carry out of base to the last byte produced, rippling back
        through a run of 0xFF."""
        p = (self.outbyte - 1) % len(self.outbuffer)
        while self.outbuffer[p] == 0xFF:
            self.outbuffer[p] = 0
            p = (p - 1) % len(self.outbuffer)
        self.outbuffer[p] += 1

    def _manage_outbuffer(self):
        """Hand the just-filled half to the file, and fill the other."""
        if self.outbyte == len(self.outbuffer):
            self.outbyte = 0
        self.fp.write(bytes(
            self.outbuffer[self.outbyte:self.outbyte + AC_BUFFER_SIZE]))
        self.endbyte = self.outbyte + AC_BUFFER_SIZE

    def _renorm_enc_interval(self):
        while True:
            # output and discard the top byte
            self.outbuffer[self.outbyte] = (self.base >> 24) & 0xFF
            self.outbyte += 1
            if self.outbyte == self.endbyte:
                self._manage_outbuffer()
            self.base = (self.base << 8) & U32_MASK
            self.length = (self.length << 8) & U32_MASK
            if self.length >= AC_MIN_LENGTH:
                break

    def encode_bit(self, m, sym):
        assert sym in (0, 1)
        self._check_started()

        # m is an ArithmeticBitModel
        x = m.bit_0_prob * (self.length >> BM_LENGTH_SHIFT)

        if sym == 0:
            self.length = x
            m.bit_0_count += 1
        else:
            init_base = self.base
            self.base = (self.base + x) & U32_MASK
            self.length -= x
            if init_base > self.base:  # overflow = carry
                self._propagate_carry()

        if self.length < AC_MIN_LENGTH:
            self._renorm_enc_interval()

        m.bits_until_update -= 1  # TODO get the model to handle this
        if m.bits_until_update == 0:
            m.update()

    def encode_symbol(self, m, sym):
        # m is an ArithmeticModel
        self._check_started()

        init_base = self.base

        if sym == m.last_symbol:
            x = m.distribution_lookup(sym) * \
                (self.length >> cpylaz.DM_LENGTH_SHIFT)
            self.length -= x  # no second product needed
        else:
            self.length >>= cpylaz.DM_LENGTH_SHIFT
            x = m.distribution_lookup(sym) * self.length
            self.length = m.distribution_lookup(sym+1) * self.length - x

        self.base = (self.base + x) & U32_MASK

        if init_base > self.base:
            self._propagate_carry()

        if self.length < AC_MIN_LENGTH:
            self._renorm_enc_interval()

        m.increment_symbol_count(sym)

    def write_bits(self, bits, sym):
        assert bits > 0 and bits <= 32
        assert sym < (1 << bits)
        self._check_started()

        # mirrors read_bits: the low 16 bits go first, then the rest
        if bits > 19:
            self.write_bits(16, sym & 0xFFFF)
            sym >>= 16
            bits -= 16

        init_base = self.base
        self.length >>= bits
        self.base = (self.base + sym * self.length) & U32_MASK

        if init_base > self.base:
            self._propagate_carry()

        if self.length < AC_MIN_LENGTH:
            self._renorm_enc_interval()

    def write_int(self, sym):
        self.write_bits(32, sym)

    def done(self):
        """Flush the interval and the ring buffer, then write the trailing
        zero bytes that the decoder's four-byte initial fill expects."""
        self._check_started()

        init_base = self.base
        another_byte = True

        # pick a final base inside the current interval that ends in zeros
        if self.length > 2 * AC_MIN_LENGTH:
            self.base = (self.base + AC_MIN_LENGTH) & U32_MASK
            self.length = AC_MIN_LENGTH >> 1     # one more byte
        else:
            self.base = (self.base + (AC_MIN_LENGTH >> 1)) & U32_MASK
            self.length = AC_MIN_LENGTH >> 9     # two more bytes
            another_byte = False

        if init_base > self.base:
            self._propagate_carry()

        self._renorm_enc_interval()

        # endbyte still at the end of the ring means nothing has been handed
        # off yet; otherwise the far half holds the older bytes
        if self.endbyte != len(self.outbuffer):
            self.fp.write(bytes(self.outbuffer[AC_BUFFER_SIZE:]))
        if self.outbyte:
            self.fp.write(bytes(self.outbuffer[:self.outbyte]))

        # two or three zero bytes, to stay in sync with the decoder
        self.fp.write(b"\x00\x00\x00" if another_byte else b"\x00\x00")

        # the stream is finished; nothing more may be encoded into it
        self.length = 0

    def create_symbol_model(self, num_symbols):
        return cpylaz.ArithmeticModel(num_symbols, True)

    def __repr__(self):
        return f"ArithmeticEncoder(base={self.base}, length={self.length})"


class ArithmeticDecoder:
    """An ArithmeticDecoder decodes a stream of symbols using an arithmetic
    model."""

    def __init__(self, fp):
        self.fp = fp
        self.length = 0
        self.value = 0

    def start(self):
        self.length = AC_MAX_LENGTH

        data = self.fp.read(4)
        self.value = int.from_bytes(data, byteorder='big')

    def _renorm_dec_interval(self):
        """Renormalize the decoder interval."""
        while self.length < AC_MIN_LENGTH:
            data = unsigned_int(self.fp.read(1))
            self.value = (self.value << 8) | data
            self.length <<= 8

    def decode_bit(self, m):
        if self.length == 0:
            raise RuntimeError(
                "Decoder interval is empty - have you called start()?")

        # m is an ArithmeticBitModel
        x = m.bit_0_prob * (self.length >> BM_LENGTH_SHIFT)
        sym = (self.value >= x)

        if sym == 0:
            self.length = x
            m.bit_0_count += 1
        else:
            self.value -= x
            self.length -= x

        if self.length < AC_MIN_LENGTH:
            self._renorm_dec_interval()

        m.bits_until_update -= 1  # TODO get the model to handle this
        if m.bits_until_update == 0:
            m.update()

        return sym

    def decode_symbol(self, m):
        # m is an ArithmeticModel

        if self.length == 0:
            raise RuntimeError(
                "Decoder interval is empty - have you called start()?")

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

        if self.length < AC_MIN_LENGTH:
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

        if self.length < AC_MIN_LENGTH:
            self._renorm_dec_interval()

        return sym

    def read_int(self):
        return self.read_bits(32)

    def create_symbol_model(self, num_symbols):
        return cpylaz.ArithmeticModel(num_symbols, False)

    def __repr__(self):
        return f"ArithmeticDecoder(value={self.value}, length={self.length})"
