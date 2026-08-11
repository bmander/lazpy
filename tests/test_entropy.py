import io
import random

import pytest

import compressor
import encoder
import models
from lazpy import LazError
from lazpy import _cpylaz as cpylaz


class TestArithmeticModel:

    def test_create(self):
        model = models.ArithmeticModel(256, False)
        assert model.num_symbols == 256
        assert model.compress is False

        with pytest.raises(Exception):
            model.decoder_table_lookup(0)

        with pytest.raises(Exception):
            model.distribution_lookup(0)

        with pytest.raises(Exception):
            model.symbol_count_lookup(0)

        assert model.has_decoder_table() is False

    def test_init_4(self):
        model = models.ArithmeticModel(4, False)
        model.init()

        assert model.num_symbols == 4
        assert model.compress is False

        with pytest.raises(Exception):
            model.decoder_table_lookup(0)

        assert model.symbol_count_lookup(0) == 1
        assert model.symbol_count_lookup(2) == 1
        assert model.symbol_count_lookup(3) == 1
        with pytest.raises(Exception):
            model.symbol_count_lookup(4)

        assert model.distribution_lookup(0) == 0
        assert model.distribution_lookup(2) == 16384
        assert model.distribution_lookup(3) == 24576
        with pytest.raises(Exception):
            model.distribution_lookup(4)

        assert model.has_decoder_table() is False

    def test_init_256(self):
        model = models.ArithmeticModel(256, False)
        model.init()

        assert model.num_symbols == 256
        assert model.compress is False

        assert model.decoder_table_lookup(0) == 0
        assert model.decoder_table_lookup(32) == 127
        assert model.decoder_table_lookup(65) == 255
        with pytest.raises(Exception):
            model.decoder_table_lookup(66)

        assert model.symbol_count_lookup(0) == 1
        assert model.symbol_count_lookup(32) == 1
        assert model.symbol_count_lookup(255) == 1
        with pytest.raises(Exception):
            model.symbol_count_lookup(256)

        assert model.distribution_lookup(0) == 0
        assert model.distribution_lookup(32) == 4096
        assert model.distribution_lookup(255) == 32640
        with pytest.raises(Exception):
            model.distribution_lookup(256)

        assert model.has_decoder_table() is True

    def test_table_init(self):
        model = models.ArithmeticModel(8, False)
        with pytest.raises(ValueError):
            model.init([1, 1, 2, 3, 5, 8, 13, 21, 34])

        model.init([1, 1, 2, 3, 5, 8, 13, 21])

        assert model.symbol_count_lookup(0) == 1
        assert model.symbol_count_lookup(7) == 21

        assert model.has_decoder_table() is False

        assert model.distribution_lookup(0) == 0
        assert model.distribution_lookup(4) == 28672
        assert model.distribution_lookup(5) == 49152
        assert model.distribution_lookup(6) == 16384
        assert model.distribution_lookup(7) == 4096


class TestCArithmeticModel:

    def test_create(self):
        model = cpylaz.ArithmeticModel(256, False)
        assert model.num_symbols == 256
        assert model.compress is False

        with pytest.raises(Exception):
            model.decoder_table_lookup(0)

        with pytest.raises(Exception):
            model.distribution_lookup(0)

        with pytest.raises(Exception):
            model.symbol_count_lookup(0)

        assert model.has_decoder_table() is False

    def test_init_4(self):
        model = cpylaz.ArithmeticModel(4, False)
        model.init()

        assert model.num_symbols == 4
        assert model.compress is False

        with pytest.raises(Exception):
            model.decoder_table_lookup(0)

        assert model.symbol_count_lookup(0) == 1
        assert model.symbol_count_lookup(2) == 1
        assert model.symbol_count_lookup(3) == 1
        with pytest.raises(Exception):
            model.symbol_count_lookup(4)

        assert model.distribution_lookup(0) == 0
        assert model.distribution_lookup(2) == 16384
        assert model.distribution_lookup(3) == 24576
        with pytest.raises(Exception):
            model.distribution_lookup(4)

        assert model.has_decoder_table() is False

    def test_init_256(self):
        model = cpylaz.ArithmeticModel(256, False)
        model.init()

        assert model.num_symbols == 256
        assert model.compress is False

        assert model.decoder_table_lookup(0) == 0
        assert model.decoder_table_lookup(32) == 127
        assert model.decoder_table_lookup(65) == 255
        with pytest.raises(Exception):
            model.decoder_table_lookup(66)

        assert model.symbol_count_lookup(0) == 1
        assert model.symbol_count_lookup(32) == 1
        assert model.symbol_count_lookup(255) == 1
        with pytest.raises(Exception):
            model.symbol_count_lookup(256)

        assert model.distribution_lookup(0) == 0
        assert model.distribution_lookup(32) == 4096
        assert model.distribution_lookup(255) == 32640
        with pytest.raises(Exception):
            model.distribution_lookup(256)

        assert model.has_decoder_table() is True

    def test_table_init(self):
        model = cpylaz.ArithmeticModel(8, False)
        with pytest.raises(ValueError):
            model.init([1, 1, 2, 3, 5, 8, 13, 21, 34])

        model.init([1, 1, 2, 3, 5, 8, 13, 21])

        assert model.symbol_count_lookup(0) == 1
        assert model.symbol_count_lookup(7) == 21

        assert model.has_decoder_table() is False

        assert model.distribution_lookup(0) == 0
        assert model.distribution_lookup(4) == 28672
        assert model.distribution_lookup(5) == 49152
        assert model.distribution_lookup(6) == 16384
        assert model.distribution_lookup(7) == 4096


class TestArithmeticBitModel:
    def test_create(self):
        model = models.ArithmeticBitModel()
        assert model is not None

        assert model.bit_0_count == 1
        assert model.bit_count == 2
        assert model.bit_0_prob == 4096
        assert model.update_cycle == 4
        assert model.bits_until_update == 4


class TestCArithmeticBitModel:
    def test_create(self):
        model = cpylaz.ArithmeticBitModel()
        assert model is not None

        assert model.bit_0_count == 1
        assert model.bit_0_prob == 4096
        assert model.bits_until_update == 4


# string filled with random bits
file_contents = (
    b"\xad]\r\xf3-v*V\xa9\xd3\xf9\xbb\x7f\x9a\x06\xc9^hWv\xe7\xe7"
    b"\rXE\xf0w\x88+\xe0G\x12\xe0\x06?c\xc8\xd7e\xa1\xe0\t\x86\x08\x9a\x11"
    b"\x88\xd4U\xbfb?d`H\xdcgq\x15\xab\tx\xe7\x8bP\\\xf0\x99\xa9\xf1\xf2G-@"
    b"7y\xf9J\x94)\x17\xe6\xa2>\x17\x8d\xdf\x14\xf3\xc9\x85Q\xc5?BTB\xfd"
    b"\x9d\xa8>\xf80\x8a\x19\x01(\xc2N\xe0`\xbc$\x9b\x91\xe0\xed\xe3\x19K"
    b"\xdb\xba\x01\x11\x9a\xf2\x89\x01\xb1\xb5\xb2%\xe7=.ua\xbb\x92(-\xb4"
    b"\xde=*#\xec\x15Hs:\x80\xa7\x0b\xba\xe6\xbcD!'\x1c\x08\t\x1db\xfeT\xa5"
    b"_\x15OeL\x81,Z\xf2\\|\x86i[\xc0\x1fQ\x9e;2]\xef\x92\xbb\x16\xfd\xcb"
    b"\x88\x9f\x13Je\xe8-@\x8a\xbd\xc7)v\xb3K\xcc\x9e\xa4\xaf\xc8\xb5\x05"
    b"\x1c!\x97i\xe4\x8c\x89n\xb5\x9c\xb0\xbc\x00\x85\re\xed0\x8b\xe0\xe4"
    b"\x0c\x1c; \xbf*\x89\xec\xa9\x80\xc2n\xc0R(\x8d|\x1a"
)


class TestArithmeticDecoder:
    def test_create(self):
        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)
        assert decoder is not None

        assert repr(decoder) == "ArithmeticDecoder(value=0, length=0)"

    def test_start(self):
        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)
        decoder.start()

        assert decoder.fp == fp
        assert decoder.length == 4294967295
        assert decoder.value == 2908556787

    def test_decode_bit(self):
        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)
        m = models.ArithmeticBitModel()
        decoder.start()

        bits = [
            1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 0, 1, 0, 0,
            0, 0, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0,
            0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0, 0,
            1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0]

        testbits = [int(decoder.decode_bit(m)) for i in range(64)]

        assert bits == testbits

    def test_decode_symbol(self):
        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)
        m = models.ArithmeticModel(8, compress=False)
        m.init()
        decoder.start()

        symbols = [
            5, 3, 2, 5, 6, 6, 7, 2, 6, 5, 1, 6, 5, 3, 5, 3,
            4, 7, 7, 3, 6, 6, 5, 1, 6, 7, 3, 5, 6, 7, 7, 4,
            6, 6, 5, 6, 7, 6, 1, 5, 7, 6, 5, 5, 6, 7, 7, 6,
            5, 5, 7, 7, 0, 5, 7, 6, 6, 6, 6, 2, 5, 5, 5, 7]

        test_symbols = [decoder.decode_symbol(m) for i in range(64)]

        assert symbols == test_symbols

    def test_read_bits(self):
        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)
        decoder.start()

        assert decoder.read_bits(32) == 3142626653

        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)
        decoder.start()

        assert decoder.read_bits(1) == 1
        assert decoder.value == 761073140
        assert decoder.length == 2147483647
        assert decoder.read_bits(2) == 1
        assert decoder.value == 224202229
        assert decoder.length == 536870911
        assert decoder.read_bits(3) == 3
        assert decoder.value == 22875640
        assert decoder.length == 67108863
        assert decoder.read_bits(8) == 87
        assert decoder.value == 17714989
        assert decoder.length == 67108608
        assert decoder.read_bits(16) == 17316
        assert decoder.value == 47281706
        assert decoder.length == 67043328
        assert decoder.read_bits(18) == 185418
        assert decoder.value == 1951836627
        assert decoder.length == 4278190080
        assert decoder.read_bits(4) == 7
        assert decoder.read_bits(8) == 76
        assert decoder.read_bits(16) == 46932
        assert decoder.read_bits(32) == 3890320431

    def test_read_int(self):
        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)
        decoder.start()

        assert decoder.read_int() == 3142626653

    def test_create_symbol_model(self):
        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)

        model = decoder.create_symbol_model(8)

        assert model is not None
        assert model.num_symbols == 8


class TestCArithmeticDecoder:

    def test_create(self):
        """A fresh decoder has the interval start() would give it.

        Not zero: decoding divides by the interval length, so a decoder read
        before it is started -- which only a malformed file arranges -- must
        run out of stream rather than divide by zero. See laz_decoder_setup.
        """
        fp = io.BytesIO()
        decoder = cpylaz.ArithmeticDecoder(fp)
        assert decoder.length == 0xFFFFFFFF
        assert decoder.value == 0

        assert repr(decoder) == "ArithmeticDecoder(value=0, length=4294967295)"

    def test_start(self):
        fp = io.BytesIO(file_contents)
        decoder = cpylaz.ArithmeticDecoder(fp)
        decoder.start()
        assert decoder.length == 4294967295
        assert decoder.value == 2908556787

    def test_decode_bit(self):
        fp = io.BytesIO(file_contents)
        decoder = cpylaz.ArithmeticDecoder(fp)
        m = cpylaz.ArithmeticBitModel()
        decoder.start()

        bits = [
            1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 0, 1, 0, 0,
            0, 0, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0,
            0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0, 0,
            1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0]

        testbits = [int(decoder.decode_bit(m)) for i in range(64)]

        assert bits == testbits

    def test_decode_symbol(self):
        fp = io.BytesIO(file_contents)
        decoder = cpylaz.ArithmeticDecoder(fp)
        m = cpylaz.ArithmeticModel(8, False)
        m.init()
        decoder.start()

        symbols = [
            5, 3, 2, 5, 6, 6, 7, 2, 6, 5, 1, 6, 5, 3, 5, 3,
            4, 7, 7, 3, 6, 6, 5, 1, 6, 7, 3, 5, 6, 7, 7, 4,
            6, 6, 5, 6, 7, 6, 1, 5, 7, 6, 5, 5, 6, 7, 7, 6,
            5, 5, 7, 7, 0, 5, 7, 6, 6, 6, 6, 2, 5, 5, 5, 7]

        test_symbols = [decoder.decode_symbol(m) for i in range(64)]

        assert symbols == test_symbols

    def test_read_bits(self):
        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)
        decoder.start()

        assert decoder.read_bits(32) == 3142626653

        fp = io.BytesIO(file_contents)
        decoder = cpylaz.ArithmeticDecoder(fp)
        decoder.start()

        assert decoder.read_bits(1) == 1
        assert decoder.value == 761073140
        assert decoder.length == 2147483647
        assert decoder.read_bits(2) == 1
        assert decoder.value == 224202229
        assert decoder.length == 536870911
        assert decoder.read_bits(3) == 3
        assert decoder.value == 22875640
        assert decoder.length == 67108863
        assert decoder.read_bits(8) == 87
        assert decoder.value == 17714989
        assert decoder.length == 67108608
        assert decoder.read_bits(16) == 17316
        assert decoder.value == 47281706
        assert decoder.length == 67043328
        assert decoder.read_bits(18) == 185418
        assert decoder.value == 1951836627
        assert decoder.length == 4278190080
        assert decoder.read_bits(4) == 7
        assert decoder.read_bits(8) == 76
        assert decoder.read_bits(16) == 46932
        assert decoder.read_bits(32) == 3890320431

    def test_read_int(self):
        fp = io.BytesIO(file_contents)
        decoder = cpylaz.ArithmeticDecoder(fp)
        decoder.start()

        assert decoder.read_int() == 3142626653

    def test_create_symbol_model(self):
        fp = io.BytesIO(file_contents)
        decoder = cpylaz.ArithmeticDecoder(fp)

        model = decoder.create_symbol_model(8)

        assert model is not None
        assert model.num_symbols == 8


class TestIntegerCompressor:
    def test_create(self):
        fp = io.BytesIO()
        dec = cpylaz.ArithmeticDecoder(fp)
        ic = compressor.IntegerCompressor(dec)
        assert ic is not None

        assert ic.dec is dec
        assert ic.enc is None
        assert ic.bits == 16
        assert ic.contexts == 1
        assert ic.bits_high == 8
        assert ic.range == 0

        ic.init_decompressor()

        assert ic.get_m_bits(0).num_symbols == 17
        assert type(ic.get_corrector(0)) == cpylaz.ArithmeticBitModel
        assert ic.get_corrector(1).num_symbols == 2

    def test_decompress(self):
        fp = io.BytesIO(file_contents)
        dec = encoder.ArithmeticDecoder(fp)
        dec.start()
        ic = compressor.IntegerCompressor(dec)

        ic.init_decompressor()

        assert ic.decompress(0) == 1051
        assert ic.k == 11
        assert ic.decompress(1051) == 998
        assert ic.k == 6
        assert ic.decompress(998) == 997
        assert ic.k == 1
        assert ic.decompress(997) == 865
        assert ic.k == 8
        assert ic.decompress(865) == 64006
        assert ic.k == 12
        assert ic.decompress(64006) == 64001
        assert ic.k == 3
        assert ic.decompress(64001) == 64027
        assert ic.k == 5


class TestCIntegerCompressor:
    def test_create(self):
        fp = io.BytesIO()
        dec = cpylaz.ArithmeticDecoder(fp)
        ic = cpylaz.IntegerCompressor(dec)
        assert ic is not None

        assert ic.dec is dec
        assert ic.enc is None
        assert ic.bits == 16
        assert ic.contexts == 1
        assert ic.bits_high == 8
        assert ic.range == 0

        ic.init_decompressor()

        assert ic.get_m_bits(0).num_symbols == 17
        assert type(ic.get_corrector(0)) == cpylaz.ArithmeticBitModel
        assert ic.get_corrector(1).num_symbols == 2

    def test_decompress(self):
        fp = io.BytesIO(file_contents)
        dec = cpylaz.ArithmeticDecoder(fp)
        dec.start()
        ic = cpylaz.IntegerCompressor(dec)

        ic.init_decompressor()

        assert ic.decompress(0) == 1051
        assert ic.k == 11
        assert ic.decompress(1051) == 998
        assert ic.k == 6
        assert ic.decompress(998) == 997
        assert ic.k == 1
        assert ic.decompress(997) == 865
        assert ic.k == 8
        assert ic.decompress(865) == 64006
        assert ic.k == 12
        assert ic.decompress(64006) == 64001
        assert ic.k == 3
        assert ic.decompress(64001) == 64027
        assert ic.k == 5


# ---------------------------------------------------------------------------
# The encoder.
#
# There are no committed byte vectors for the encoder the way there are for
# the decoder, and there is no need for any: the claim worth making is that
# encoding is the exact inverse of decoding, and the decoder is already pinned
# against laszip below. Every test here is therefore a round trip.
#
# Both implementations are run through the same tests, and one test asserts
# that they emit the same bytes, so a desync in either shows up as a
# disagreement rather than as two implementations wrong in the same way.
# ---------------------------------------------------------------------------

# (encoder, decoder, integer compressor) for each implementation
PY_CODER = (encoder.ArithmeticEncoder, encoder.ArithmeticDecoder,
            compressor.IntegerCompressor)
C_CODER = (cpylaz.ArithmeticEncoder, cpylaz.ArithmeticDecoder,
           cpylaz.IntegerCompressor)

coders = pytest.mark.parametrize("coder", [PY_CODER, C_CODER],
                                 ids=["python", "c"])


def encode(coder, encode_into):
    """Run encode_into(enc) against a fresh encoder and return the bytes."""
    fp = io.BytesIO()
    enc = coder[0](fp)
    enc.start()
    encode_into(enc)
    enc.done()
    return fp.getvalue()


def decoder_for(coder, data):
    dec = coder[1](io.BytesIO(data))
    dec.start()
    return dec


def pseudorandom(count, modulus, seed=1):
    """A repeatable spread of values, so a failure is reproducible."""
    rand = random.Random(seed)
    return [rand.randrange(modulus) for _ in range(count)]


def symbols_round_trip(coder, num_symbols, symbols):
    """Encodes symbols through a fresh model and decodes them back through
    another. Returns (decoded, encoded bytes)."""
    def encode_into(enc):
        m = enc.create_symbol_model(num_symbols)
        m.init()
        for s in symbols:
            enc.encode_symbol(m, s)

    data = encode(coder, encode_into)

    dec = decoder_for(coder, data)
    m = dec.create_symbol_model(num_symbols)
    m.init()
    return [dec.decode_symbol(m) for _ in symbols], data


def compress_all(coder, pairs, bits, contexts=1, bits_high=8):
    """Compresses every (pred, real) pair and returns the encoded bytes."""
    enc = coder[0](io.BytesIO())
    ic = coder[2](enc, bits=bits, contexts=contexts, bits_high=bits_high)
    ic.init_compressor()
    enc.start()
    for i, (pred, real) in enumerate(pairs):
        ic.compress(pred, real, i % contexts)
    enc.done()
    return enc.fp.getvalue()


@coders
class TestArithmeticEncoder:

    def test_repr(self, coder):
        enc = coder[0](io.BytesIO())
        enc.start()
        assert repr(enc) == "ArithmeticEncoder(base=0, length=4294967295)"

    def test_bits_round_trip(self, coder):
        bits = pseudorandom(5000, 2)

        def encode_into(enc):
            m = cpylaz.ArithmeticBitModel()
            for b in bits:
                enc.encode_bit(m, b)

        data = encode(coder, encode_into)

        dec = decoder_for(coder, data)
        m = cpylaz.ArithmeticBitModel()
        assert [int(dec.decode_bit(m)) for _ in bits] == bits

    # 16 symbols or fewer needs no decoder table on the decode side; more does,
    # and the two paths through decode_symbol have to agree with the one
    # encode_symbol
    @pytest.mark.parametrize("num_symbols", [2, 8, 16, 17, 256, 2048])
    def test_symbols_round_trip(self, coder, num_symbols):
        symbols = pseudorandom(4000, num_symbols, seed=num_symbols)
        assert symbols_round_trip(coder, num_symbols, symbols)[0] == symbols

    def test_raw_bits_round_trip(self, coder):
        rand = random.Random(7)
        values = [(width, rand.randrange(1 << width))
                  for width in range(1, 33) for _ in range(40)]

        def encode_into(enc):
            for width, value in values:
                enc.write_bits(width, value)

        data = encode(coder, encode_into)

        dec = decoder_for(coder, data)
        assert [dec.read_bits(width) for width, _ in values] == \
               [value for _, value in values]

    def test_write_int_round_trip(self, coder):
        values = [0, 1, 0xFFFF, 0x10000, 0x7FFFFFFF, 0xFFFFFFFF] + \
            pseudorandom(100, 1 << 32, seed=3)

        def encode_into(enc):
            for value in values:
                enc.write_int(value)

        data = encode(coder, encode_into)

        dec = decoder_for(coder, data)
        assert [dec.read_int() for _ in values] == values

    # Long enough to cycle the encoder's ring buffer several times, which is
    # what puts carry propagation across a buffer boundary in play.
    def test_long_stream_round_trip(self, coder):
        symbols = pseudorandom(8000, 256, seed=11)

        decoded, data = symbols_round_trip(coder, 256, symbols)

        assert len(data) > 4 * 1024        # more than two ring buffers
        assert decoded == symbols

    def test_mixed_round_trip(self, coder):
        """Interleaving the three kinds of write is the case that matters in a
        real point stream, and the one where an interval left in the wrong
        state by one of them shows up."""
        symbols = pseudorandom(2000, 59, seed=13)

        def encode_into(enc):
            m = enc.create_symbol_model(59)
            m.init()
            bit_model = cpylaz.ArithmeticBitModel()
            for s in symbols:
                enc.encode_symbol(m, s)
                enc.encode_bit(bit_model, s & 1)
                enc.write_bits(7, s % 128)

        dec = decoder_for(coder, encode(coder, encode_into))
        m = dec.create_symbol_model(59)
        m.init()
        bit_model = cpylaz.ArithmeticBitModel()
        for s in symbols:
            assert dec.decode_symbol(m) == s
            assert dec.decode_bit(bit_model) == s & 1
            assert dec.read_bits(7) == s % 128

    # the two implementations track "started" differently -- the binding on
    # its stream, the reference on its interval -- so they raise different
    # types for it
    NOT_STARTED = (ValueError, RuntimeError)

    def test_encoding_before_start_raises(self, coder):
        enc = coder[0](io.BytesIO())
        with pytest.raises(self.NOT_STARTED):
            enc.write_bits(8, 0)

    def test_encoding_after_done_raises(self, coder):
        enc = coder[0](io.BytesIO())
        enc.start()
        enc.done()
        with pytest.raises(self.NOT_STARTED):
            enc.write_bits(8, 0)


def test_a_failing_write_is_not_swallowed():
    """An I/O error from the file object propagates as itself, rather than
    leaving a silently truncated stream behind."""
    class Exploding:
        def write(self, data):
            raise PermissionError("device on fire")

    enc = cpylaz.ArithmeticEncoder(Exploding())
    enc.start()
    enc.write_bits(16, 4242)
    with pytest.raises(PermissionError):
        enc.done()

    # and once the caller has caught that, the encoder still refuses to be
    # used rather than returning NULL with nothing set
    with pytest.raises(LazError):
        enc.write_bits(8, 0)


def test_the_two_encoders_emit_the_same_bytes():
    symbols = pseudorandom(4000, 256, seed=17)
    bits = pseudorandom(4000, 2, seed=19)

    def encode_into(enc):
        m = enc.create_symbol_model(256)
        m.init()
        bit_model = cpylaz.ArithmeticBitModel()
        for symbol, bit in zip(symbols, bits):
            enc.encode_symbol(m, symbol)
            enc.encode_bit(bit_model, bit)
            enc.write_bits(11, symbol * 8 + bit)

    assert encode(PY_CODER, encode_into) == encode(C_CODER, encode_into)


@coders
class TestIntegerCompressorRoundTrip:

    def round_trip(self, coder, pairs, bits, contexts=1, bits_high=8):
        """Compresses every (pred, real) pair and decompresses them back."""
        data = compress_all(coder, pairs, bits, contexts, bits_high)

        dec = coder[1](io.BytesIO(data))
        ic = coder[2](dec, bits=bits, contexts=contexts, bits_high=bits_high)
        ic.init_decompressor()
        dec.start()
        return [ic.decompress(pred, i % contexts)
                for i, (pred, _) in enumerate(pairs)]

    # Holding the predictor still and sweeping the value over the whole range
    # sweeps the corrector over the whole range with it, so every k from 0 to
    # bits is reached, along with every corrector inside every k.
    @pytest.mark.parametrize("bits", [8, 16])
    def test_full_corrector_range(self, coder, bits):
        values = list(range(1 << bits))
        pairs = [(0, real) for real in values]
        assert self.round_trip(coder, pairs, bits) == values

    # 2^32 values cannot be swept, so this walks the boundary of every k
    # instead: the corrector is one of +-2^j and its neighbours, which is
    # where the translation into and out of the k-bit interval turns over.
    # -2^31 is the only corrector that reaches k == 32, which the decoder
    # recovers from k alone.
    def test_32_bit_corrector_boundaries(self, coder):
        corrs = [0, 1, -1, -2**31]
        for j in range(31):
            corrs += [2**j - 1, 2**j, 2**j + 1, -2**j - 1, -2**j, -2**j + 1]

        pred = 1234567
        pairs = [(pred, compressor.i32(pred + corr)) for corr in corrs]
        assert self.round_trip(coder, pairs, 32) == [real for _, real in pairs]

    def test_32_bit_values(self, coder):
        rand = random.Random(23)
        values = [rand.randrange(-2**31, 2**31) for _ in range(3000)]
        pairs = list(zip([0] + values, values))
        assert self.round_trip(coder, pairs, 32) == values

    # Each context carries its own k model, so a value coded under one context
    # must not be decoded under another.
    def test_contexts_stay_separate(self, coder):
        values = pseudorandom(3000, 1 << 16, seed=29)
        pairs = [(0, real) for real in values]
        assert self.round_trip(coder, pairs, 16, contexts=4) == values

    # Below bits_high a corrector is coded whole; above it the high bits go
    # through a model and the low bits are written raw. bits_high cannot go
    # past 11: the per-k model would need more than 2048 symbols.
    @pytest.mark.parametrize("bits_high", [4, 11])
    def test_bits_high_split(self, coder, bits_high):
        values = pseudorandom(8000, 1 << 16, seed=37)
        pairs = [(0, real) for real in values]
        assert self.round_trip(coder, pairs, 16, bits_high=bits_high) == values


def test_the_two_integer_compressors_emit_the_same_bytes():
    values = pseudorandom(3000, 1 << 16, seed=31)
    pairs = list(zip([0] + values, values))     # a running predictor

    assert (compress_all(PY_CODER, pairs, 16)
            == compress_all(C_CODER, pairs, 16))


class TestCIntegerCompressorDirection:
    """An IntegerCompressor codes one way only, and says so."""

    def test_compressing_with_a_decoder_raises(self):
        dec = cpylaz.ArithmeticDecoder(io.BytesIO(file_contents))
        ic = cpylaz.IntegerCompressor(dec)
        ic.init_decompressor()
        with pytest.raises(ValueError):
            ic.compress(0, 1)
        with pytest.raises(ValueError):
            ic.init_compressor()

    def test_decompressing_with_an_encoder_raises(self):
        enc = cpylaz.ArithmeticEncoder(io.BytesIO())
        ic = cpylaz.IntegerCompressor(enc)
        ic.init_compressor()
        assert ic.enc is enc
        assert ic.dec is None
        with pytest.raises(ValueError):
            ic.decompress(0)
        with pytest.raises(ValueError):
            ic.init_decompressor()

    def test_rejects_anything_else(self):
        with pytest.raises(TypeError):
            cpylaz.IntegerCompressor(io.BytesIO())

    def test_compressing_before_init_raises(self):
        enc = cpylaz.ArithmeticEncoder(io.BytesIO())
        ic = cpylaz.IntegerCompressor(enc)
        enc.start()
        with pytest.raises(ValueError):
            ic.compress(0, 1)
