import encoder
from lazpy import _cpylaz as cpylaz


def i32(x):
    """Wrap to a signed 32-bit integer.

    Predictor and corrector arithmetic is I32 in the C version, and for
    32-bit fields it genuinely relies on the wraparound: the corrector
    between two values at opposite ends of the range is only small once it
    has wrapped."""
    return ((x + 0x80000000) & 0xFFFFFFFF) - 0x80000000


class IntegerCompressor:
    def __init__(self, dec_or_enc, bits=16, contexts=1, bits_high=8, range=0):
        if type(dec_or_enc) in {cpylaz.ArithmeticDecoder, 
                                encoder.ArithmeticDecoder}:
            self.dec = dec_or_enc
            self.enc = None
        elif type(dec_or_enc) in {cpylaz.ArithmeticEncoder,
                                  encoder.ArithmeticEncoder}:
            self.dec = None
            self.enc = dec_or_enc
        else:
            raise ValueError("First argument must be an encoder or decoder")

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
            self.corr_min = -0x80000000
            self.corr_max = 0x7FFFFFFF

        self.m_bits = None
        self.m_corrector = None

        self.k = 0

    def _init_models(self, coder):
        # the models differ between the two directions only in whether they
        # build a decoder table, which create_symbol_model already knows
        if self.m_bits is None:
            self.m_bits = []
            for i in range(self.contexts):
                model = coder.create_symbol_model(self.corr_bits+1)
                self.m_bits.append(model)

            self.m_corrector = [cpylaz.ArithmeticBitModel()]
            for i in range(1, self.corr_bits+1):
                if i <= self.bits_high:
                    self.m_corrector.append(
                        coder.create_symbol_model(1 << i))
                else:
                    self.m_corrector.append(
                        coder.create_symbol_model(1 << self.bits_high))

        for model in self.m_bits:
            model.init()

        # entry 0 is the bit model, entries 1..corr_bits the symbol models
        for model in self.m_corrector:
            model.init()

    def init_decompressor(self):
        assert self.dec
        self._init_models(self.dec)

    def init_compressor(self):
        assert self.enc
        self._init_models(self.enc)

    def _read_corrector(self, model):
        self.k = self.dec.decode_symbol(model)

        if self.k != 0:
            if self.k < 32:
                c = self.dec.decode_symbol(self.m_corrector[self.k])
                if self.k > self.bits_high:
                    k1 = self.k-self.bits_high
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

        real = i32(pred + self._read_corrector(self.m_bits[context]))

        if real < 0:
            real += self.corr_range
        elif real >= self.corr_range:
            real -= self.corr_range

        return real

    def _write_corrector(self, c, model):
        # find the tightest interval [-(2**k - 1), 2**k] that contains c
        self.k = 0
        c1 = -c if c <= 0 else c-1
        while c1:
            c1 >>= 1
            self.k += 1

        self.enc.encode_symbol(model, self.k)

        if self.k != 0:
            # k == 32 means c == corr_min, which the decoder recovers from k
            if self.k < 32:
                # translate c into the k-bit interval [0, 2**k - 1]
                if c < 0:
                    c += (1 << self.k)-1
                else:
                    c -= 1

                if self.k <= self.bits_high:
                    self.enc.encode_symbol(self.m_corrector[self.k], c)
                else:
                    k1 = self.k-self.bits_high
                    c1 = c & ((1 << k1)-1)
                    self.enc.encode_symbol(self.m_corrector[self.k], c >> k1)
                    self.enc.write_bits(k1, c1)
        else:
            self.enc.encode_bit(self.m_corrector[0], c)

    def compress(self, pred, real, context=0):
        assert self.enc

        # fold the corrector into [corr_min, corr_max] so that k stays within
        # corr_bits
        corr = i32(real - pred)
        if corr < self.corr_min:
            corr += self.corr_range
        elif corr > self.corr_max:
            corr -= self.corr_range

        self._write_corrector(corr, self.m_bits[context])

    def get_m_bits(self, i):
        # matches C API
        return self.m_bits[i]

    def get_corrector(self, i):
        # matches C API
        return self.m_corrector[i]
