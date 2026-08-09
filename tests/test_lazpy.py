import collections
import functools
import io
import os
import random
import struct

import pytest

import compressor
import encoder
import lazpy
import models
from lazpy import _cpylaz as cpylaz
from lazpy import (Compressor, Reader, Selective, ItemType, LazError,
                   UnsupportedFileError)


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
file_contents = b"\xad]\r\xf3-v*V\xa9\xd3\xf9\xbb\x7f\x9a\x06\xc9^hWv\xe7\xe7" \
b"\rXE\xf0w\x88+\xe0G\x12\xe0\x06?c\xc8\xd7e\xa1\xe0\t\x86\x08\x9a\x11\x88\xd4" \
b"U\xbfb?d`H\xdcgq\x15\xab\tx\xe7\x8bP\\\xf0\x99\xa9\xf1\xf2G-@7y\xf9J\x94)" \
b"\x17\xe6\xa2>\x17\x8d\xdf\x14\xf3\xc9\x85Q\xc5?BTB\xfd\x9d\xa8>\xf80\x8a\x19" \
b"\x01(\xc2N\xe0`\xbc$\x9b\x91\xe0\xed\xe3\x19K\xdb\xba\x01\x11\x9a\xf2\x89" \
b"\x01\xb1\xb5\xb2%\xe7=.ua\xbb\x92(-\xb4\xde=*#\xec\x15Hs:\x80\xa7\x0b\xba" \
b"\xe6\xbcD!'\x1c\x08\t\x1db\xfeT\xa5_\x15OeL\x81,Z\xf2\\|\x86i[\xc0\x1fQ\x9e" \
b";2]\xef\x92\xbb\x16\xfd\xcb\x88\x9f\x13Je\xe8-@\x8a\xbd\xc7)v\xb3K\xcc\x9e" \
b"\xa4\xaf\xc8\xb5\x05\x1c!\x97i\xe4\x8c\x89n\xb5\x9c\xb0\xbc\x00\x85\re\xed0" \
b"\x8b\xe0\xe4\x0c\x1c; \xbf*\x89\xec\xa9\x80\xc2n\xc0R(\x8d|\x1a"

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

        bits = [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1,
        0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 1, 0,
        0, 0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0]

        testbits = [int(decoder.decode_bit(m)) for i in range(64)]

        assert bits == testbits

    def test_decode_symbol(self):
        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)
        m = models.ArithmeticModel(8, compress=False)
        m.init()
        decoder.start()

        symbols = [5, 3, 2, 5, 6, 6, 7, 2, 6, 5, 1, 6, 5, 3, 5, 3, 4, 7, 7, 3, 
        6, 6, 5, 1, 6, 7, 3, 5, 6, 7, 7, 4, 6, 6, 5, 6, 7, 6, 1, 5, 7, 6, 5, 
        5, 6, 7, 7, 6, 5, 5, 7, 7, 0, 5, 7, 6, 6, 6, 6, 2, 5, 5, 5, 7]

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


class TestCArithmeticDeoder:

    def test_create(self):
        fp = io.BytesIO()
        decoder = cpylaz.ArithmeticDecoder(fp)
        assert decoder.length == 0
        assert decoder.value == 0

        assert repr(decoder) == "ArithmeticDecoder(value=0, length=0)"

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

        bits = [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 1, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1,
        0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 1, 0,
        0, 0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0]

        testbits = [int(decoder.decode_bit(m)) for i in range(64)]

        assert bits == testbits


    def test_decode_symbol(self):
        fp = io.BytesIO(file_contents)
        decoder = cpylaz.ArithmeticDecoder(fp)
        m = cpylaz.ArithmeticModel(8, False)
        m.init()
        decoder.start()

        symbols = [5, 3, 2, 5, 6, 6, 7, 2, 6, 5, 1, 6, 5, 3, 5, 3, 4, 7, 7, 3, 
        6, 6, 5, 1, 6, 7, 3, 5, 6, 7, 7, 4, 6, 6, 5, 6, 7, 6, 1, 5, 7, 6, 5, 
        5, 6, 7, 7, 6, 5, 5, 7, 7, 0, 5, 7, 6, 6, 6, 6, 2, 5, 5, 5, 7]

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

    assert compress_all(PY_CODER, pairs, 16) == compress_all(C_CODER, pairs, 16)


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


# ---------------------------------------------------------------------------
# End-to-end reading.
#
# testdata/ holds a small file for every point data format (0-10) crossed with
# every LASzip item version that applies to it, a "_pointwise" variant of each
# legacy format for the non-chunked container, plus reference_hashes.txt: the
# FNV-1a checksum of every decoded field of every point, produced by laszip
# itself via tools/lazdump.c. Matching those hashes is the real correctness
# claim -- the unit tests above only pin the entropy coder.
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTDATA = os.path.join(REPO_ROOT, "testdata")


def load_reference_hashes():
    path = os.path.join(TESTDATA, "reference_hashes.txt")
    entries = []
    with open(path) as fh:
        for line in fh:
            name, digest, count = line.split()
            entries.append((name, int(digest), int(count)))
    return entries


REFERENCE_HASHES = load_reference_hashes()
FIXTURES = [name for name, _, _ in REFERENCE_HASHES]


def fixture(name):
    return os.path.join(TESTDATA, name)


@pytest.mark.parametrize("name,digest,count", REFERENCE_HASHES,
                         ids=[e[0] for e in REFERENCE_HASHES])
def test_decodes_identically_to_laszip(name, digest, count):
    """Every decoded field of every point matches laszip, bit for bit."""
    with Reader(fixture(name)) as reader:
        assert reader.num_points == count
        assert reader.checksum() == (digest, count)


@pytest.mark.parametrize("name", FIXTURES)
def test_reads_every_point_sequentially(name):
    with Reader(fixture(name)) as reader:
        n = sum(1 for _ in reader)
        assert n == reader.num_points


@pytest.mark.parametrize("name", FIXTURES)
def test_seek_matches_sequential_read(name):
    """Random access must land on the same point a sequential read would."""
    with Reader(fixture(name)) as reader:
        sequential = [(p.X, p.Y, p.Z, p.gps_time, p.classification)
                      for p in reader]

        n = len(sequential)
        # forwards, backwards, repeats, and both ends
        for index in [0, 1, n - 1, n // 2, 5, n // 3, 0, n - 2, n // 2, 3]:
            reader.seek(index)
            p = reader.read()
            assert (p.X, p.Y, p.Z, p.gps_time, p.classification) == sequential[index], \
                f"seek({index}) in {name}"


@pytest.mark.parametrize("name", FIXTURES)
def test_reader_reports_its_position(name):
    with Reader(fixture(name)) as reader:
        assert reader.index == 0
        reader.read()
        assert reader.index == 1
        reader.seek(10)
        assert reader.index == 10
        reader.read()
        assert reader.index == 11


# ---------------------------------------------------------------------------
# The item writers.
#
# testdata/ is an oracle for writing as well as reading: every ptN_v0.las holds
# the same points as the ptN_v1/v2 .laz beside it, uncompressed, so feeding
# those raw records to the writers should reproduce the compressed files laszip
# produced -- byte for byte, because the encoder is deterministic. That is a
# far stronger claim than a round trip, and it is what these tests make.
#
# cpylaz.compress_chunk() codes one chunk: the first point raw, then the rest
# through the compressed writers over one arithmetic stream. Chunking and the
# chunk table are the container's job and are still to come, so the tests
# assemble the container themselves.
# ---------------------------------------------------------------------------

LEGACY_FORMATS = range(6)

# The one field lazpy does not hand back: patching a fixture's point count
# means writing it where the LAS header keeps it.
LEGACY_POINT_COUNT_OFFSET = 107

FixtureFile = collections.namedtuple(
    "FixtureFile", "data header num_points items chunk_size")


@functools.lru_cache(maxsize=None)
def load(name):
    """A fixture's bytes, plus what lazpy parses out of its header.

    Going through Reader rather than unpacking header offsets by hand keeps
    these helpers honest about the fields that moved in LAS 1.4 -- num_points
    is the extended count where there is one.
    """
    with open(fixture(name), "rb") as fh:
        data = fh.read()
    with Reader(io.BytesIO(data)) as reader:
        return FixtureFile(data, reader.header, reader.num_points,
                           tuple((int(t), size, version)
                                 for t, size, version in reader.items),
                           reader.chunk_size)


def las_records(name):
    """The uncompressed point records of a .las fixture, as they sit on disk.

    For point formats 0-5 a record is exactly the concatenation of its LAZ
    items, so these go straight into compress_chunk.
    """
    f = load(name)
    start = f.header["offset_to_point_data"]
    length = f.header["point_data_record_length"]
    return [f.data[start + i * length: start + (i + 1) * length]
            for i in range(f.num_points)]


def point_block(name):
    """Everything a fixture holds from the first point onward."""
    f = load(name)
    return f.data[f.header["offset_to_point_data"]:]


def rebuilt(name, block, count):
    """A fixture's header and VLRs, with our own point block behind them."""
    f = load(name)
    header = bytearray(f.data[:f.header["offset_to_point_data"]])
    struct.pack_into("<I", header, LEGACY_POINT_COUNT_OFFSET, count)
    return io.BytesIO(bytes(header) + block)


def compressed_chunks(name, records):
    """Every chunk of `records`, at the chunk size `name` declares."""
    items, chunk_size = load(name).items, load(name).chunk_size
    return [cpylaz.compress_chunk(items, records[start:start + chunk_size])
            for start in range(0, len(records), chunk_size)]


@pytest.mark.parametrize("point_format", LEGACY_FORMATS)
def test_v1_output_is_byte_identical_to_laszip(point_format):
    """The whole point block of a non-chunked v1 file, reproduced exactly."""
    name = f"pt{point_format}_v1_pointwise.laz"
    written = cpylaz.compress_chunk(load(name).items,
                                    las_records(f"pt{point_format}_v0.las"))
    assert written == point_block(name)


@pytest.mark.parametrize("point_format", LEGACY_FORMATS)
def test_v2_output_is_byte_identical_to_laszip(point_format):
    """Every chunk of a chunked v2 file, reproduced exactly.

    Walking the chunks end to end also pins their lengths: the last one has to
    finish exactly where the chunk table begins.
    """
    name = f"pt{point_format}_v2.laz"
    block = point_block(name)
    table_start = struct.unpack_from("<q", block, 0)[0]

    position = 8                                   # past the chunk table offset
    for index, written in enumerate(
            compressed_chunks(name, las_records(f"pt{point_format}_v0.las"))):
        assert block[position:position + len(written)] == written, \
            f"chunk {index} differs"
        position += len(written)

    assert load(name).header["offset_to_point_data"] + position == table_start


@pytest.mark.parametrize("point_format", LEGACY_FORMATS)
def test_raw_output_is_byte_identical(point_format):
    """Version 0 items write the record straight through."""
    records = las_records(f"pt{point_format}_v0.las")
    items = [(t, size, 0) for t, size, _ in load(f"pt{point_format}_v2.laz").items]
    assert cpylaz.compress_chunk(items, records) == b"".join(records)


def awkward_records(count, length):
    """Points chosen to reach the branches the tidy fixtures never do.

    Coordinates that jump the full I32 range, gps times that repeat, creep and
    leap far enough to start a new time sequence, and colours, wavepackets and
    extra bytes with no structure at all.
    """
    rand = random.Random(4)
    records = []
    gps_time = 1.0e9
    for i in range(count):
        r = bytearray(length)
        struct.pack_into("<iii", r, 0,
                         rand.randrange(-2**30, 2**30) if i % 7 == 0 else i * 13,
                         i * -7 if i % 3 else rand.randrange(-2**30, 2**30),
                         i * i % 90000)
        struct.pack_into("<H", r, 12, rand.randrange(65536))
        for offset in (14, 15, 16, 17):
            r[offset] = rand.randrange(256)
        struct.pack_into("<H", r, 18, rand.randrange(65536))
        gps_time += (1e6 if i % 50 == 0 else
                     0.0 if i % 11 == 0 else 0.001 * rand.randrange(1000))
        struct.pack_into("<d", r, 20, gps_time)
        struct.pack_into("<HHH", r, 28, *(rand.randrange(65536) for _ in range(3)))
        r[34] = rand.randrange(256)
        struct.pack_into("<QIifff", r, 35,
                         rand.randrange(2**40), rand.randrange(2**20),
                         rand.randrange(-2**20, 2**20),
                         *(rand.uniform(-1, 1) for _ in range(3)))
        for offset in range(63, length):
            r[offset] = rand.randrange(256)
        records.append(bytes(r))
    return records


@pytest.fixture(scope="module")
def records():
    return awkward_records(400, load("pt5_v0.las").header["point_data_record_length"])


@pytest.fixture(scope="module")
def expected(records):
    """What those records decode to when nothing compresses them."""
    with Reader(rebuilt("pt5_v0.las", b"".join(records), len(records))) as reader:
        return reader.checksum()


class TestWrittenPointsReadBack:
    """
    Byte-identity only covers the points laszip happened to generate. These
    feed the writers deliberately awkward points, wrap the result in a real
    container and read it back, which is the only check available where there
    is no laszip reference to compare against.

    Point format 5 carries every legacy item at once, so one format covers all
    of them.
    """

    def test_v1_round_trips(self, records, expected):
        name = "pt5_v1_pointwise.laz"
        block = cpylaz.compress_chunk(load(name).items, records)
        with Reader(rebuilt(name, block, len(records))) as reader:
            assert reader.checksum() == expected

    def test_v2_round_trips_across_chunks(self, records, expected):
        name = "pt5_v2.laz"
        chunks = compressed_chunks(name, records)
        assert len(chunks) > 1

        # A chunk table offset pointing back at the point block is how laszip
        # leaves a file it was interrupted writing; the reader then walks the
        # chunks in order, which is all this needs.
        block = (struct.pack("<q", load(name).header["offset_to_point_data"])
                 + b"".join(chunks))
        with Reader(rebuilt(name, block, len(records))) as reader:
            assert reader.checksum() == expected


class TestCompressChunkErrors:

    def items(self):
        return load("pt1_v2.laz").items

    def test_rejects_mixed_raw_and_compressed_items(self):
        items = list(self.items())
        items[0] = (items[0][0], items[0][1], 0)
        with pytest.raises(ValueError):
            cpylaz.compress_chunk(items, las_records("pt1_v0.las"))

    def test_rejects_a_record_of_the_wrong_size(self):
        with pytest.raises(ValueError):
            cpylaz.compress_chunk(self.items(), [b"\x00" * 3])

    def test_rejects_an_unknown_item_version(self):
        items = [(t, size, 7) for t, size, _ in self.items()]
        with pytest.raises(ValueError):
            cpylaz.compress_chunk(items, las_records("pt1_v0.las"))

    def test_rejects_no_items(self):
        with pytest.raises(ValueError):
            cpylaz.compress_chunk([], [b""])

    def test_writes_nothing_for_no_points(self):
        assert cpylaz.compress_chunk(self.items(), []) == b""


POINTWISE_FIXTURES = [n for n in FIXTURES if n.endswith("_pointwise.laz")]


class TestPointwiseContainer:
    """
    LASzip's original container compresses the whole file as one stream with no
    chunk table, so chunk_size stays U32_MAX, number_chunks stays 0 and
    read_chunk_table is skipped -- and seeking has nowhere to jump to, so it
    re-reads from point_start instead.

    Decoding and seeking are already covered: the fixtures are in FIXTURES, so
    the parametrised tests above run over them. What those cannot see is which
    container the file actually uses, because a pointwise file read as chunked
    does not raise -- it just decodes to garbage. So that is what this pins.
    """

    @staticmethod
    def compressor_of(name):
        with Reader(fixture(name)) as reader:
            return reader.laz_header["compressor"]

    def test_the_suite_has_pointwise_fixtures(self):
        """Otherwise the parametrised tests below pass over an empty list."""
        assert POINTWISE_FIXTURES

    @pytest.mark.parametrize("name", POINTWISE_FIXTURES)
    def test_fixture_really_is_non_chunked(self, name):
        assert self.compressor_of(name) == Compressor.POINTWISE

    def test_chunked_fixtures_are_still_chunked(self):
        """Guards the contrast: the rest of testdata/ must stay chunked."""
        for name in FIXTURES:
            if name.endswith(".las") or name in POINTWISE_FIXTURES:
                continue
            assert self.compressor_of(name) != Compressor.POINTWISE


class TestPointSemantics:
    def test_read_returns_a_shared_buffer(self):
        """read() reuses one object; that is why copy() exists."""
        with Reader(fixture("pt1_v2.laz")) as reader:
            a = reader.read()
            snapshot = a.copy()
            b = reader.read()
            assert a is b
            assert (snapshot.X, snapshot.Y) != (b.X, b.Y)

    def test_copy_is_independent(self):
        with Reader(fixture("pt3_v2.laz")) as reader:
            first = reader.read().copy()
            x, gps = first.X, first.gps_time
            for _ in range(10):
                reader.read()
            assert (first.X, first.gps_time) == (x, gps)

    def test_point_outlives_its_reader(self):
        """A Point kept past its reader must freeze, not dangle."""
        reader = Reader(fixture("pt10_v4.laz"))
        reader.read()
        point = reader.read()
        before = (point.X, point.Y, point.Z, point.gps_time,
                  point.rgb, point.wave_packet, point.extra_bytes)
        reader.close()
        del reader
        after = (point.X, point.Y, point.Z, point.gps_time,
                 point.rgb, point.wave_packet, point.extra_bytes)
        assert before == after

    def test_scaled_coordinates(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            point = reader.read()
            x, y, z = reader.scale(point)
            sx, sy, sz = reader.scales
            ox, oy, oz = reader.offsets
            assert x == point.X * sx + ox
            assert y == point.Y * sy + oy
            assert z == point.Z * sz + oz

    def test_extra_bytes_are_exposed(self):
        # every fixture carries 6 trailing extra bytes
        with Reader(fixture("pt6_v3.laz")) as reader:
            assert reader.num_extra_bytes == 6
            assert len(reader.read().extra_bytes) == 6

    def test_points_slice(self):
        with Reader(fixture("pt2_v2.laz")) as reader:
            got = [p.X for p in reader.points(start=10, count=5)]
            reader.seek(10)
            want = [reader.read().X for _ in range(5)]
            assert got == want


class TestFileProperties:
    def test_compressed_and_uncompressed_agree(self):
        """The .las and .laz of a legacy format hold the same points."""
        for point_format in range(6):
            raw = Reader(fixture(f"pt{point_format}_v0.las"))
            for version in (1, 2):
                comp = Reader(fixture(f"pt{point_format}_v{version}.laz"))
                assert comp.checksum() == raw.checksum(), \
                    f"format {point_format} v{version} differs from raw"
                raw.seek(0)
                comp.close()
            raw.close()

    def test_flags_compression(self):
        with Reader(fixture("pt1_v2.laz")) as laz:
            assert laz.is_compressed is True
            # the compressed-flag bit is cleared from the reported format
            assert laz.point_format == 1
        with Reader(fixture("pt1_v0.las")) as las:
            assert las.is_compressed is False
            assert las.point_format == 1

    def test_header_fields(self):
        with Reader(fixture("pt6_v3.laz")) as reader:
            assert reader.header["file_signature"] == b"LASF"
            assert reader.header["version_major"] == 1
            assert reader.header["version_minor"] == 4
            assert reader.num_points == 500
            assert len(reader) == 500

    def test_chunk_size_from_laszip_vlr(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            assert reader.chunk_size == 137

    def test_accepts_an_open_file_object(self):
        with open(fixture("pt0_v2.laz"), "rb") as fh:
            reader = Reader(fh)
            assert reader.num_points == 500
            reader.read()
            reader.close()
            assert not fh.closed      # we did not open it, so we do not close it


class TestItemLayout:
    def test_known_formats(self):
        items = lazpy.items_for_point_format(1, 28)
        assert [t for t, _, _ in items] == [ItemType.POINT10, ItemType.GPSTIME11]

        items = lazpy.items_for_point_format(10, 67)
        assert [t for t, _, _ in items] == [
            ItemType.POINT14, ItemType.RGBNIR14, ItemType.WAVEPACKET14]

    def test_trailing_bytes_become_an_extra_item(self):
        items = lazpy.items_for_point_format(0, 20 + 7)
        assert items[-1] == (ItemType.BYTE, 7, 0)

        items = lazpy.items_for_point_format(6, 30 + 7)
        assert items[-1] == (ItemType.BYTE14, 7, 0)

    def test_rejects_unknown_format(self):
        with pytest.raises(UnsupportedFileError):
            lazpy.items_for_point_format(11, 30)

    def test_rejects_undersized_record(self):
        with pytest.raises(LazError):
            lazpy.items_for_point_format(3, 20)


class TestSelectiveDecompression:
    """Only the layered LAS 1.4 formats can skip layers."""

    def test_skipping_layers_keeps_xy_in_sync(self):
        mask = Selective.ALL & ~(Selective.Z | Selective.INTENSITY |
                                 Selective.CLASSIFICATION)
        with Reader(fixture("pt6_v3.laz")) as full:
            want = [(p.X, p.Y) for p in full]
        with Reader(fixture("pt6_v3.laz"), decompress_selective=mask) as partial:
            got = [(p.X, p.Y) for p in partial]
        assert got == want

    def test_skipped_attributes_are_frozen(self):
        mask = Selective.ALL & ~Selective.Z
        with Reader(fixture("pt6_v3.laz"), decompress_selective=mask) as reader:
            zs = {p.Z for p in reader}
        # Z never decodes, so it keeps the first point's value within each chunk
        assert len(zs) < 10

    def test_full_mask_is_the_default(self):
        with Reader(fixture("pt8_v4.laz")) as a:
            default = a.checksum()
        with Reader(fixture("pt8_v4.laz"), decompress_selective=Selective.ALL) as b:
            explicit = b.checksum()
        assert default == explicit


class TestErrors:
    def test_not_a_las_file(self, tmp_path):
        path = tmp_path / "bogus.laz"
        path.write_bytes(b"NOPE" + bytes(400))
        with pytest.raises(LazError):
            Reader(str(path))

    def test_seek_out_of_range(self):
        with Reader(fixture("pt0_v2.laz")) as reader:
            with pytest.raises(IndexError):
                reader.seek(reader.num_points + 1)
            with pytest.raises(IndexError):
                reader.seek(-1)

    def test_reading_past_the_end_raises(self):
        with Reader(fixture("pt0_v2.laz")) as reader:
            for _ in range(reader.num_points):
                reader.read()
            with pytest.raises(LazError):
                for _ in range(200):
                    reader.read()

    def test_truncated_file_is_reported(self, tmp_path):
        whole = open(fixture("pt6_v3.laz"), "rb").read()
        path = tmp_path / "truncated.laz"
        path.write_bytes(whole[:len(whole) // 2])
        with pytest.raises(LazError):
            with Reader(str(path)) as reader:
                for _ in range(reader.num_points):
                    reader.read()

    def test_every_failure_is_one_catchable_category(self):
        """Header, setup and decode failures all raise LazError."""
        assert issubclass(UnsupportedFileError, LazError)
        with Reader(fixture("pt0_v2.laz")) as reader:
            with pytest.raises(LazError):
                for _ in range(reader.num_points + 200):
                    reader.read()

    def test_underlying_file_errors_are_not_swallowed(self):
        """An I/O error from the file object propagates as itself.

        Without this, a genuine read failure is indistinguishable from a
        truncated file, because both would surface as "end-of-file".
        """
        class Exploding:
            """Reads normally until armed, then fails."""

            def __init__(self, path):
                self._fh = open(path, "rb")
                self.armed = False

            def read(self, n=-1):
                if self.armed:
                    raise PermissionError("device on fire")
                return self._fh.read(n)

            def seek(self, *a):
                return self._fh.seek(*a)

            def tell(self):
                return self._fh.tell()

        fh = Exploding(fixture("pt1_v2.laz"))
        reader = Reader(fh)          # header parsing must succeed
        fh.armed = True              # now break the decoder's refill
        with pytest.raises(PermissionError):
            for _ in range(reader.num_points):
                reader.read()
