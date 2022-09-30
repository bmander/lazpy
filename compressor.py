import encoder
import models


class IntegerCompressor:
    def __init__(self, dec_or_enc, bits=16, contexts=1, bits_high=8, range=0):
        if type(dec_or_enc) == encoder.ArithmeticDecoder:
            self.dec = dec_or_enc
            self.enc = None
        elif type(dec_or_enc) == encoder.ArithmeticEncoder:
            self.dec = None
            self.enc = dec_or_enc

        self.bits = bits
        self.contexts = contexts
        self.bits_high = bits_high
        self.range = range

        if range != 0:
            self.corr_bits = 0
            self.corr_range = range
            while range != 0:
                range >>= 1
                self.corr_bits += 1
            if self.corr_range == (1 << (self.corr_bits - 1)):
                self.corr_bits -= 1
            self.corr_min = -self.corr_range // 2
            self.corr_max = self.corr_min+self.corr_range-1
        elif bits > 0 and bits < 32:
            self.corr_bits = bits
            self.corr_range = 1 << bits
            self.corr_min = -self.corr_range // 2
            self.corr_max = self.corr_min+self.corr_range-1
        else:
            self.corr_bits = 32
            self.corr_range = 0
            self.corr_min = -0x7FFFFFFF
            self.corr_max = 0x7FFFFFFF

        self.m_bits = None
        self.m_corrector = None

        self.k = 0

    def init_decompressor(self):
        assert self.dec

        if self.m_bits is None:
            self.m_bits = []
            for i in range(self.contexts):
                model = self.dec.create_symbol_model(self.corr_bits+1)
                self.m_bits.append(model)

            self.m_corrector = [models.ArithmeticBitModel()]
            for i in range(1, self.corr_bits):
                if i <= self.bits_high:
                    self.m_corrector.append(
                        self.dec.create_symbol_model(1 << i))
                else:
                    self.m_corrector.append(
                        self.dec.create_symbol_model(1 << self.bits_high))

        for i in range(self.contexts):
            self.m_bits[i].init()

        self.m_corrector[0].init()

        for i in range(1, self.corr_bits):
            self.m_corrector[i].init()

    def _read_corrector(self, model):
        self.k = self.dec.decode_symbol(model)

        if self.k != 0:
            if self.k < 32:
                if self.k <= self.bits_high:
                    c = self.dec.decode_symbol(self.m_corrector[self.k])
                else:
                    k1 = self.k-self.bits_high
                    c = self.dec.decode_symbol(self.m_corrector[self.k])
                    c1 = self.dec.read_bits(k1)
                    c = (c << k1) | c1

                # translate c back into its correct interval
                if c >= (1 << (self.k-1)):
                    c += 1
                else:
                    c -= (1 << self.k)-1

            else:
                c = self.corr_min
        else:
            c = self.dec.decode_bit(self.m_corrector[0])

        return c

    def decompress(self, pred, context=0):
        assert self.dec

        real = pred + self._read_corrector(self.m_bits[context])

        if real < 0:
            real += self.corr_range
        elif real >= self.corr_range:
            real -= self.corr_range

        return real
