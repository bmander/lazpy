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
from lazpy import (Compressor, LASZIP_VLR_RECORD_ID, LASZIP_VLR_USER_ID,
                   Point, Reader, Selective, ItemType, LazError,
                   UnsupportedFileError, Writer)

# How a variable length record is keyed in header["variable_length_records"].
LASZIP_VLR_KEY = (LASZIP_VLR_USER_ID, LASZIP_VLR_RECORD_ID)


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


# ---------------------------------------------------------------------------
# End-to-end reading.
#
# testdata/ holds a small file for every point data format (0-10) crossed with
# every LASzip item version that applies to it, a "_pointwise" variant of each
# legacy format for the non-chunked container, a "_compat" pair of each 1.4
# format for LAS 1.4 compatibility mode, plus reference_hashes.txt: the
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
REFERENCE_HASH = {name: (digest, count)
                  for name, digest, count in REFERENCE_HASHES}


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
            got = (p.X, p.Y, p.Z, p.gps_time, p.classification)
            assert got == sequential[index], f"seek({index}) in {name}"


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
# Extended variable length records.
#
# No fixture has any -- laszip writes none -- so they are built here and stuck
# on the end of one, which is exactly what a file with them looks like: the
# records sit behind the point data, and two header fields aim at them.
# ---------------------------------------------------------------------------

EVLR_OFFSET_FIELD = 235      # start_of_first_extended_variable_length_record
EVLR_COUNT_FIELD = 243       # number_of_extended_variable_length_records
EVLR_LENGTH_FIELD = 20       # record_length_after_header, within a record

# LAS 1.4, which is the only version with extended records; the same points
# compressed and not, since the two take different routes to the point data
EVLR_NAME = "pt6_v3.laz"
EVLR_NAMES = [EVLR_NAME, "pt6_v0.las"]


def evlr_bytes(user_id, record_id, payload, description=b""):
    """One extended record on disk: 60 bytes of header, then the payload."""
    return struct.pack("<H16sHQ32s", 0, user_id, record_id, len(payload),
                       description) + payload


def with_evlrs(*records, name=EVLR_NAME, declared=None, start=None):
    """A fixture's bytes, with `records` appended and the header aimed at them.

    `declared` and `start` override the two header fields, which is how a file
    that lies about the records it holds gets built.
    """
    data = bytearray(load(name).data)
    offset = len(data)
    for record in records:
        data += record
    struct.pack_into("<Q", data, EVLR_OFFSET_FIELD,
                     offset if start is None else start)
    struct.pack_into("<I", data, EVLR_COUNT_FIELD,
                     len(records) if declared is None else declared)
    return bytes(data)


def evlrs_of(reader):
    return reader.header["extended_variable_length_records"]


def test_extended_records_are_parsed():
    data = with_evlrs(
        evlr_bytes(b"LASF_Projection", 2112, b"WKT" * 100, b"a projection"),
        evlr_bytes(b"lazpy", 1, b"payload"))

    with Reader(io.BytesIO(data)) as reader:
        evlrs = evlrs_of(reader)
        assert set(evlrs) == {(b"LASF_Projection", 2112), (b"lazpy", 1)}

        wkt = evlrs[(b"LASF_Projection", 2112)]
        assert wkt["user_id"] == b"LASF_Projection"
        assert wkt["record_id"] == 2112
        assert wkt["description"] == b"a projection"
        assert wkt["record_length_after_header"] == 300
        assert wkt["data"] == b"WKT" * 100

        assert evlrs[(b"lazpy", 1)]["data"] == b"payload"
        assert reader.warning is None


@pytest.mark.parametrize("name", EVLR_NAMES)
def test_extended_records_leave_the_points_alone(name):
    """Compressed or not, and whatever else the reader does with the file."""
    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"payload"), name=name)
    with Reader(io.BytesIO(data)) as reader:
        assert evlrs_of(reader)[(b"lazpy", 1)]["data"] == b"payload"
        assert reader.checksum() == REFERENCE_HASH[name]


def test_an_extended_record_reads_like_a_dict():
    """The payload arrives late, but it is a key like any other."""
    with Reader(io.BytesIO(with_evlrs(evlr_bytes(b"lazpy", 1, b"load")))) as r:
        record = evlrs_of(r)[(b"lazpy", 1)]
        assert set(record) == {"reserved", "user_id", "record_id",
                               "record_length_after_header", "description",
                               "offset_to_data", "data"}
        assert "data" in record
        assert record.get("data") == b"load"
        assert dict(record)["data"] == b"load"


def test_records_sharing_a_record_id_are_kept_apart():
    """LAS namespaces records by user id -- LASF_Spec reserves ids 0 to 99 for
    waveform packet descriptors -- so the id alone is not a key."""
    data = with_evlrs(evlr_bytes(b"LASF_Spec", 7, b"theirs"),
                      evlr_bytes(b"lazpy", 7, b"ours"))
    with Reader(io.BytesIO(data)) as reader:
        assert evlrs_of(reader)[(b"LASF_Spec", 7)]["data"] == b"theirs"
        assert evlrs_of(reader)[(b"lazpy", 7)]["data"] == b"ours"
        assert reader.warning is None


class RecordingBytesIO(io.BytesIO):
    """A file that remembers which of its bytes were read."""

    def __init__(self, data):
        super().__init__(data)
        self.reads = []

    def read(self, size=-1):
        start = self.tell()
        data = super().read(size)
        self.reads.append((start, len(data)))
        return data

    def touched(self, start, length):
        return any(at < start + length and start < at + n
                   for at, n in self.reads)


def test_a_payload_is_not_read_until_it_is_asked_for():
    """Opening a file must not pull its payloads in: a waveform packet record
    is allowed to be gigabytes, which is what the eight-byte length is for."""
    payload = b"w" * (1 << 20)
    data = with_evlrs(evlr_bytes(b"LASF_Spec", 65535, payload))
    # the far end of the payload, which no buffered read over the point block
    # could reach by accident
    tail = (len(data) - 1024, 1024)

    fp = RecordingBytesIO(data)
    with Reader(fp) as reader:
        record = evlrs_of(reader)[(b"LASF_Spec", 65535)]
        assert record["record_length_after_header"] == len(payload)
        assert not fp.touched(*tail)

        assert reader.checksum() == REFERENCE_HASH[EVLR_NAME]
        assert not fp.touched(*tail), "decoding read the payload"

        assert record["data"] == payload
        assert fp.touched(*tail)


def test_reading_a_payload_does_not_disturb_point_reading():
    """Points and payloads are readable in either order, and interleaved: the
    point reader owns the file by then, and keeps its own buffer over it."""
    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"first" * 500),
                      evlr_bytes(b"lazpy", 2, b"second" * 500))

    def coords(point):
        return (point.X, point.Y, point.Z, point.gps_time)

    with Reader(io.BytesIO(data)) as reader:
        expected = [coords(p) for p in reader]

    with Reader(io.BytesIO(data)) as reader:
        evlrs = evlrs_of(reader)
        first, second = evlrs[(b"lazpy", 1)], evlrs[(b"lazpy", 2)]
        assert first["data"] == b"first" * 500          # before any point

        # read on, without a seek to paper over a disturbed decoder
        got = [coords(reader.read()) for _ in range(10)]
        assert second["data"] == b"second" * 500        # mid-decode
        got += [coords(reader.read())
                for _ in range(reader.num_points - len(got))]
        assert got == expected


@pytest.mark.parametrize("name", FIXTURES)
def test_files_without_extended_records_have_none(name):
    """Every version, including the 1.4 files that could have had them.

    An always-present key, so nothing has to ask whether a file is new enough
    to have had any. Decoding is not re-checked here: these are the fixtures
    exactly as they sit on disk, which test_decodes_identically_to_laszip
    already reads end to end.
    """
    with Reader(fixture(name)) as reader:
        assert evlrs_of(reader) == {}


def test_a_short_block_keeps_what_is_there_and_warns():
    """A malformed record behind the points says nothing about the points, so
    the file still opens."""
    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"here"), declared=3)
    with Reader(io.BytesIO(data)) as reader:
        assert list(evlrs_of(reader)) == [(b"lazpy", 1)]
        assert reader.warning == ("file declares 3 extended variable length "
                                  "records but holds 1")
        assert reader.checksum() == REFERENCE_HASH[EVLR_NAME]


def test_a_wild_record_count_stops_at_the_end_of_the_file():
    """The count is a U32 out of the header and worth no more trust than that:
    what stops the walk is running out of file, not the count."""
    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"here"), declared=0xFFFFFFFF)
    with Reader(io.BytesIO(data)) as reader:
        assert list(evlrs_of(reader)) == [(b"lazpy", 1)]
        assert "holds 1" in reader.warning
        assert reader.checksum() == REFERENCE_HASH[EVLR_NAME]


def test_a_block_past_the_end_of_the_file_warns():
    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"here"), start=1 << 40)
    with Reader(io.BytesIO(data)) as reader:
        assert evlrs_of(reader) == {}
        assert "holds 0" in reader.warning
        assert reader.checksum() == REFERENCE_HASH[EVLR_NAME]


def test_a_payload_running_past_the_end_of_the_file_is_not_handed_over_short():
    record = bytearray(evlr_bytes(b"lazpy", 1, b"here"))
    struct.pack_into("<Q", record, EVLR_LENGTH_FIELD, 1 << 20)
    with Reader(io.BytesIO(with_evlrs(bytes(record)))) as reader:
        assert evlrs_of(reader) == {}
        assert "holds 0" in reader.warning


def test_a_payload_that_goes_away_under_the_reader_raises():
    """The length was checked against the file when it was opened, so this can
    only happen to a file that shrank since. It is still not a short read."""
    fp = io.BytesIO(with_evlrs(evlr_bytes(b"lazpy", 1, b"payload")))
    with Reader(fp) as reader:
        record = evlrs_of(reader)[(b"lazpy", 1)]
        fp.truncate(record["offset_to_data"] + 3)
        with pytest.raises(LazError, match="past the end"):
            record["data"]


def test_a_stream_that_cannot_seek_fails_as_a_stream_that_cannot_seek():
    """Extended records are behind the point data, so reading them means
    seeking -- but a file that cannot seek was already unreadable, and that is
    the error worth getting."""
    class Unseekable(io.RawIOBase):
        def __init__(self, data):
            self._buffer = io.BytesIO(data)

        def read(self, size=-1):
            return self._buffer.read(size)

        def readable(self):
            return True

        def seekable(self):
            return False

    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"payload"))
    with pytest.raises(LazError, match="seek to the start of point data"):
        Reader(Unseekable(data))


def test_a_payload_already_read_outlives_the_reader(tmp_path):
    path = tmp_path / "evlrs.laz"
    path.write_bytes(with_evlrs(evlr_bytes(b"lazpy", 1, b"payload")))
    with Reader(str(path)) as reader:
        record = evlrs_of(reader)[(b"lazpy", 1)]
        assert record["data"] == b"payload"
    assert record["data"] == b"payload"


def test_a_payload_cannot_be_read_once_the_file_is_closed(tmp_path):
    path = tmp_path / "evlrs.laz"
    path.write_bytes(with_evlrs(evlr_bytes(b"lazpy", 1, b"payload")))
    with Reader(str(path)) as reader:
        record = evlrs_of(reader)[(b"lazpy", 1)]
    with pytest.raises(LazError, match="closed"):
        record["data"]


# ---------------------------------------------------------------------------
# LAS 1.4 compatibility mode.
#
# The ptN_compat_* fixtures are LAS 1.4 points hidden in a legacy file, written
# by laszip's own compatibility mode. That they decode to the right points is
# already settled by the reference hashes, which come from laszip reading them
# back the same way; what is left to pin here is the view of the file around
# the points -- the version and point format lazpy reports, the header fields
# it fills in from the record that carried them, and the two records that stop
# being true once the points are put back together.
# ---------------------------------------------------------------------------

# Every compatibility-mode fixture: which legacy point format and LAS version
# it wears, how many bytes it hides per point -- five, or seven where there is
# a NIR band to hide too -- and how many extra bytes it has of its own.
CompatFixture = collections.namedtuple(
    "CompatFixture", "stem legacy minor hidden extra")
COMPAT_FIXTURES = [
    CompatFixture("pt6_compat", 1, 2, 5, 6),
    CompatFixture("pt7_compat", 3, 2, 5, 6),
    CompatFixture("pt8_compat", 3, 2, 7, 6),
    CompatFixture("pt9_compat", 4, 3, 5, 6),
    CompatFixture("pt10_compat", 5, 3, 7, 6),
    # no extra bytes of its own, which is the ordinary shape of one of these:
    # nothing is left for the "extra bytes" record to describe
    CompatFixture("pt8_compat_noextra", 3, 2, 7, 0),
]
COMPAT_NAMES = [f"{f.stem}_v{v}.{ext}" for f in COMPAT_FIXTURES
                for v, ext in ((0, "las"), (2, "laz"))]
COMPAT_BY_NAME = {f"{f.stem}_v{v}.{ext}": f for f in COMPAT_FIXTURES
                  for v, ext in ((0, "las"), (2, "laz"))}

LASCOMPATIBLE_KEY = (b"lascompatible", 22204)
EXTRA_BYTES_KEY = (b"LASF_Spec", 4)


@pytest.mark.parametrize("name", COMPAT_NAMES)
def test_a_compatibility_file_reads_as_the_las_14_file_it_stands_in(name):
    """The version, point format and record length laszip would report."""
    f = COMPAT_BY_NAME[name]
    legacy, minor, hidden, extra = f.legacy, f.minor, f.hidden, f.extra
    upgraded = {1: 6, 3: 7 if hidden == 5 else 8, 4: 9, 5: 10}[legacy]
    data = load(name).data

    # what the file says about itself before any of this
    assert data[25] == minor
    assert data[104] & 0x7F == legacy
    on_disk_header_size, on_disk_offset = struct.unpack_from("<HI", data, 94)

    with Reader(fixture(name)) as reader:
        header = reader.header
        assert header["version_minor"] == 4
        assert reader.point_format == upgraded
        assert reader.num_extra_bytes == extra
        # the hidden bytes are gone and the wider LAS 1.4 fields are there
        assert struct.unpack_from("<H", data, 105)[0] == (
            lazpy._POINT_FORMATS[legacy][0] + extra + hidden)
        assert header["point_data_record_length"] == (
            lazpy._POINT_FORMATS[upgraded][0] + extra)
        # a LAS 1.2 header is 148 bytes shorter than a 1.4 one, a 1.3 one 140
        grew = 148 if minor == 2 else 140
        assert header["header_size"] == on_disk_header_size + grew == 375
        assert header["offset_to_point_data"] == on_disk_offset + grew


@pytest.mark.parametrize("name", COMPAT_NAMES)
def test_a_compatibility_file_carries_its_las_14_fields(name):
    """The complaint the feature answers: these are not all zero.

    The fixtures sweep each field across its range, so every one of them takes
    a value the legacy record has no room for -- a classification above 31, a
    return number above 7, a scan angle past what a one-byte rank can say.
    """
    with Reader(fixture(name)) as reader:
        points = [p.copy() for p in reader]

    assert max(p.extended_classification for p in points) > 31
    assert max(p.extended_return_number for p in points) > 7
    assert max(p.extended_number_of_returns for p in points) > 7
    assert max(p.extended_scanner_channel for p in points) == 3
    assert min(p.extended_scan_angle for p in points) < -21167
    assert max(p.extended_scan_angle for p in points) > 21167
    assert all(p.extended_point_type == 1 for p in points)


@pytest.mark.parametrize("name", COMPAT_NAMES)
def test_the_records_describing_the_disguise_are_gone(name):
    """Once the points are whole again the two records describe nothing.

    The compatibility record always goes. The "extra bytes" record loses the
    attributes covering the hidden fields, and goes with them if that is all it
    had -- which it is unless the file had extra bytes of its own.
    """
    extra = COMPAT_BY_NAME[name].extra
    with Reader(fixture(name)) as reader:
        vlrs = reader.header["variable_length_records"]
        declared = reader.header["number_of_variable_length_records"]

    assert LASCOMPATIBLE_KEY not in vlrs
    assert len(vlrs) == declared
    if not extra:
        assert EXTRA_BYTES_KEY not in vlrs
        return
    attributes = vlrs[EXTRA_BYTES_KEY]
    names = [name for name, _, _, _
             in lazpy._extra_bytes_attributes(attributes["data"])]
    assert not any(n.startswith(b"LAS 1.4 ") for n in names)
    assert len(names) == extra
    assert attributes["record_length_after_header"] == len(attributes["data"])


@pytest.mark.parametrize("name", COMPAT_NAMES)
def test_the_point_counts_come_from_the_compatibility_record(name):
    with Reader(fixture(name)) as reader:
        header = reader.header
        assert reader.num_points == 500
        assert header["extended_number_of_point_records"] == 500
        assert sum(header["extended_number_of_points_by_return"]) == 500
        # compatibility mode cannot carry extended records, so laszip reads
        # the two fields that address them as zero whatever they say
        assert header["number_of_extended_variable_length_records"] == 0
        assert header["start_of_first_extended_variable_length_record"] == 0
        assert header["start_of_waveform_data_packet_record"] == 0


def test_the_laszip_record_survives_sharing_an_id_with_the_other_one():
    """Both records claim id 22204, which is why records are keyed by user id
    as well: keyed by id alone one of them would have replaced the other, and
    a compatibility-mode LAZ file would not decode at all."""
    name = "pt6_compat_v2.laz"
    with open(fixture(name), "rb") as fh:
        header = Reader._read_las_header(fh)

    ids = [record_id for _, record_id in header["variable_length_records"]]
    assert ids.count(22204) == 2
    assert Reader._find_laz_header(header) is not None
    assert (b"lascompatible", 22204) in header["variable_length_records"]


def without_compatibility_record(name):
    """A fixture with its compatibility record renamed, so nothing marks it.

    The record is left in place rather than cut out, so the file is otherwise
    byte for byte what it was: only the user id that identifies it changes.
    """
    data = bytearray(load(name).data)
    data[data.index(b"lascompatible")] = ord("x")
    return io.BytesIO(bytes(data))


@pytest.mark.parametrize("name", COMPAT_NAMES)
def test_without_the_record_the_file_is_the_legacy_file_it_says_it_is(name):
    """Nothing is upgraded on the strength of the extra bytes alone."""
    f = COMPAT_BY_NAME[name]
    with Reader(without_compatibility_record(name)) as reader:
        assert reader.header["version_minor"] == f.minor
        assert reader.point_format == f.legacy
        # the LAS 1.4 fields stay where they were, in the extra bytes
        assert reader.num_extra_bytes == f.extra + f.hidden
        assert all(p.extended_classification == 0 for p in reader)


@pytest.mark.parametrize("name", [n for n in FIXTURES
                                  if "_compat_" not in n])
def test_an_ordinary_file_is_left_alone(name):
    """Everything that is not a compatibility-mode file reads as before."""
    with Reader(fixture(name)) as reader:
        vlrs = reader.header["variable_length_records"]
        assert LASCOMPATIBLE_KEY not in vlrs
        assert lazpy._compatibility_layout(reader.header) is None


class TestCompatibilityLayoutIsChecked:
    """Where the hidden fields are is checked once, when the reader is built.

    The recoding reads those bytes for every point and does not look again, so
    a layout that points outside the extra bytes has to be refused here or not
    at all.
    """

    ITEMS = [(ItemType.POINT10, 20, 0), (ItemType.GPSTIME11, 8, 0),
             (ItemType.BYTE, 5, 0)]      # five extra bytes, all of them hidden

    def reader(self, compatibility):
        return cpylaz.PointReader(io.BytesIO(b""), self.ITEMS,
                                  Compressor.NONE,
                                  compatibility=compatibility)

    def test_a_layout_inside_the_extra_bytes_is_accepted(self):
        """The five hidden fields, laid out as laszip lays them out: two bytes
        of scan angle remainder then three single bytes, and no NIR band."""
        assert self.reader((0, 2, 3, 4, -1)).num_extra_bytes == 0

    @pytest.mark.parametrize("compatibility", [
        (0, 2, 3, 5, -1),       # the last field starts one past the end
        (4, 0, 1, 2, -1),       # the two-byte one straddles the end
        (0, 2, 3, 4, 4),        # a NIR band there is no room for
        (-1, 2, 3, 4, -1),      # a start before the beginning
    ])
    def test_a_layout_outside_the_extra_bytes_is_refused(self, compatibility):
        with pytest.raises(LazError, match="outside the extra bytes"):
            self.reader(compatibility)


class TestExtraBytesAttributes:
    """The "extra bytes" record is what says where the hidden fields are."""

    def descriptor(self, name, data_type, options=0):
        record = bytearray(lazpy.EXTRA_BYTES_ATTRIBUTE_SIZE)
        record[2] = data_type
        record[3] = options
        record[4:4 + len(name)] = name
        return bytes(record)

    def test_attributes_are_laid_out_end_to_end(self):
        data = (self.descriptor(b"a", 3)        # I16, two bytes
                + self.descriptor(b"b", 1)      # U8, one byte
                + self.descriptor(b"c", 10))    # F64, eight bytes
        assert list(lazpy._extra_bytes_attributes(data)) == [
            (b"a", 0, 0, 2), (b"b", 192, 2, 1), (b"c", 384, 3, 8)]

    def test_an_undocumented_attribute_is_as_wide_as_it_says(self):
        """Data type 0 means bytes nobody described, however many of them."""
        data = self.descriptor(b"raw", 0, options=13)
        assert list(lazpy._extra_bytes_attributes(data)) == [
            (b"raw", 0, 0, 13)]

    def test_a_trailing_partial_descriptor_is_ignored(self):
        data = self.descriptor(b"a", 1) + b"\0" * 40
        assert [name for name, _, _, _
                in lazpy._extra_bytes_attributes(data)] == [b"a"]

    def test_an_unknown_data_type_is_refused(self):
        """Sizing it wrong would put every attribute after it in the wrong
        place, which is worse than saying so."""
        data = self.descriptor(b"a", 99)
        with pytest.raises(UnsupportedFileError, match="data type 99"):
            list(lazpy._extra_bytes_attributes(data))


# ---------------------------------------------------------------------------
# Writing.
#
# testdata/ is an oracle for writing as well as reading: up to point format 5,
# every ptN_v0.las holds the same points as the ptN_v1/v2 .laz beside it,
# uncompressed, so feeding those raw records to the writer should reproduce the
# compressed files laszip produced -- byte for byte, because the encoder is
# deterministic. That is a far stronger claim than a round trip, and it is what
# these tests make. Above format 5 the .las fixture is not a faithful copy of
# the points; see the layered section further down for where they come from
# instead.
#
# cpylaz.PointWriter is the whole container: the chunk boundaries, the raw
# first point of each chunk, the item writers behind it and the chunk table at
# the end. What it emits is a fixture's point block in its entirety, so that is
# what the byte-identity tests compare.
# ---------------------------------------------------------------------------

LEGACY_FORMATS = range(6)
LAS14_FORMATS = range(6, 11)


def field_span(name, version_minor):
    """Where a header field sits, from the same tables that define it."""
    offset = 0
    for fmt in lazpy.header_formats(version_minor):
        for field, size, _ in fmt:
            if field == name:
                return offset, size
            offset += size
    raise KeyError(name)


# The fields lazpy does not hand back, and that a rebuilt header has to be
# patched at directly. LAS 1.4 zeroes the legacy point count for the extended
# point types and keeps the real one further along.
LEGACY_POINT_COUNT_OFFSET = field_span("number_of_point_records", 2)[0]
EXTENDED_POINT_COUNT_OFFSET = field_span(
    "extended_number_of_point_records", 4)[0]
# The chunk size sits 12 bytes into the LASzip VLR's payload, which begins 54
# bytes into the record -- whose 16-byte user id starts at offset 2.
LASZIP_VLR_CHUNK_SIZE_OFFSET = -2 + 54 + 12

FixtureFile = collections.namedtuple(
    "FixtureFile", "data header num_points items compressor chunk_size")


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
        compressor = (Compressor.NONE if reader.laz_header is None
                      else reader.laz_header["compressor"])
        return FixtureFile(data, reader.header, reader.num_points,
                           tuple((int(t), size, version)
                                 for t, size, version in reader.items),
                           int(compressor), reader.chunk_size)


def las_records(name):
    """The uncompressed point records of a .las fixture, as they sit on disk.

    For point formats 0-5 a record is exactly the concatenation of its LAZ
    items, so these go straight into a writer.
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


def rebuilt_header(name, count, chunk_size=None):
    """A fixture's header and VLRs, holding `count` points.

    `chunk_size` overrides what the LASzip VLR declares, which a round trip at
    any size other than the fixture's needs: the VLR is where the reader gets
    it from, and -1 there is what selects adaptive chunking.
    """
    f = load(name)
    header = bytearray(f.data[:f.header["offset_to_point_data"]])
    if struct.unpack_from("<I", header, LEGACY_POINT_COUNT_OFFSET)[0]:
        struct.pack_into("<I", header, LEGACY_POINT_COUNT_OFFSET, count)
    if f.header["version_minor"] >= 4:
        struct.pack_into("<Q", header, EXTENDED_POINT_COUNT_OFFSET, count)
    if chunk_size is not None:
        offset = (header.index(LASZIP_VLR_USER_ID)
                  + LASZIP_VLR_CHUNK_SIZE_OFFSET)
        struct.pack_into("<i", header, offset, chunk_size)
    return bytes(header)


def rebuilt(name, block, count):
    """A fixture's header and VLRs, with our own point block behind them."""
    return io.BytesIO(rebuilt_header(name, count) + block)


def compress(fp, records, items, compressor, chunk_size=0, breaks=(),
             start_offset=-1):
    """Drive a PointWriter over `records`, and hand it back once it is closed.

    `breaks` are the indices to close the open chunk in front of, which only
    adaptive chunking allows; an index appearing twice closes a chunk twice
    over. The chunk size is masked because the VLR declares it signed, and -1
    there -- adaptive -- is U32_MAX to the writer.
    """
    writer = cpylaz.PointWriter(fp, items, compressor,
                                chunk_size=chunk_size & 0xFFFFFFFF,
                                start_offset=start_offset)
    for index, record in enumerate(records):
        for _ in range(breaks.count(index)):
            writer.chunk()
        writer.write(record)
    writer.done()
    return writer


def written_block(name, records):
    """The point block `records` compress to, in the container `name` declares.

    Written behind that fixture's own header, because a chunk table holds
    absolute file positions: a block written anywhere else is not the block the
    fixture holds, however identical the compressed points are.
    """
    f = load(name)
    start = f.header["offset_to_point_data"]
    fp = io.BytesIO(f.data[:start])
    fp.seek(0, io.SEEK_END)
    compress(fp, records, f.items, f.compressor, f.chunk_size)
    return fp.getvalue()[start:]


def assert_reproduces_point_block(name, records):
    """Compressing `records` rebuilds `name`'s point block: every chunk, the
    offset in front of them, and the chunk table behind."""
    assert written_block(name, records) == point_block(name)


@pytest.mark.parametrize("point_format", LEGACY_FORMATS)
def test_v1_output_is_byte_identical_to_laszip(point_format):
    """The whole point block of a non-chunked v1 file, reproduced exactly."""
    assert_reproduces_point_block(f"pt{point_format}_v1_pointwise.laz",
                                  las_records(f"pt{point_format}_v0.las"))


@pytest.mark.parametrize("point_format", LEGACY_FORMATS)
def test_v2_output_is_byte_identical_to_laszip(point_format):
    """Every chunk of a chunked v2 file, and its chunk table, reproduced."""
    assert_reproduces_point_block(f"pt{point_format}_v2.laz",
                                  las_records(f"pt{point_format}_v0.las"))


@pytest.mark.parametrize("point_format", LEGACY_FORMATS)
def test_raw_output_is_byte_identical(point_format):
    """An uncompressed container writes the records straight through."""
    records = las_records(f"pt{point_format}_v0.las")
    items = [(t, size, 0)
             for t, size, _ in load(f"pt{point_format}_v2.laz").items]
    fp = io.BytesIO()
    compress(fp, records, items, Compressor.NONE)
    assert fp.getvalue() == b"".join(records)


# ---------------------------------------------------------------------------
# The layered (v3/v4) writers, point formats 6-10.
#
# Above point format 5 the ptN_v0.las fixture is not a usable source of points.
# tools/mklaz.cpp sets the four extended classification flags but leaves the
# legacy synthetic/keypoint/withheld bits clear, and LASzip's raw POINT14
# writer rebuilds three of those four flags out of the legacy bits -- so the
# uncompressed fixture lost flags that the compressed one beside it kept. The
# committed reference hashes say as much: pt6_v0.las and pt6_v3.laz do not
# agree. Decoding the .laz and packing its points back into records is what
# feeds the writers the points laszip actually compressed.
# ---------------------------------------------------------------------------

def pack_point14(X, Y, Z, intensity, return_number, number_of_returns,
                 classification_flags, scanner_channel, scan_direction_flag,
                 edge_of_flight_line, classification, user_data, scan_angle,
                 point_source_ID, gps_time):
    """The 30-byte LAS 1.4 point record, from its fields.

    Byte 14 and byte 15 each pack several fields, and both have to agree with
    raw_write_point14 in the extension, so they are composed in one place.
    """
    return struct.pack(
        "<iiiHBBBBhHd", X, Y, Z, intensity,
        return_number | (number_of_returns << 4),
        classification_flags | (scanner_channel << 4) |
        (scan_direction_flag << 6) | (edge_of_flight_line << 7),
        classification, user_data, scan_angle, point_source_ID, gps_time)


def point14_scanner_channel(record):
    """The scanner channel back out of a record pack_point14 built."""
    return (record[15] >> 4) & 3


def pack_las14_record(point, items):
    """A decoded point, back in the layout its items have on disk."""
    out = bytearray()
    for item_type, size, _ in items:
        if item_type == ItemType.POINT14:
            out += pack_point14(
                point.X, point.Y, point.Z, point.intensity,
                point.extended_return_number,
                point.extended_number_of_returns,
                point.extended_classification_flags,
                point.extended_scanner_channel,
                point.scan_direction_flag, point.edge_of_flight_line,
                point.extended_classification, point.user_data,
                point.extended_scan_angle, point.point_source_ID,
                point.gps_time)
        elif item_type == ItemType.RGB14:
            out += struct.pack("<HHH", *point.rgb[:3])
        elif item_type == ItemType.RGBNIR14:
            out += struct.pack("<HHHH", *point.rgb)
        elif item_type == ItemType.WAVEPACKET14:
            out += bytes(point.wave_packet)
        elif item_type == ItemType.BYTE14:
            out += bytes(point.extra_bytes)[:size]
        else:
            raise AssertionError(f"unexpected item type {item_type}")
    return bytes(out)


def packed_records(name):
    """The records of a LAS 1.4 fixture, rebuilt from what it decodes to."""
    items = load(name).items
    with Reader(io.BytesIO(load(name).data)) as reader:
        return [pack_las14_record(point, items) for point in reader]


@pytest.mark.parametrize("point_format", LAS14_FORMATS)
@pytest.mark.parametrize("version", (3, 4))
def test_layered_output_is_byte_identical_to_laszip(point_format, version):
    """Every chunk of a v3 or v4 file, reproduced exactly."""
    name = f"pt{point_format}_v{version}.laz"
    assert_reproduces_point_block(name, packed_records(name))


def test_the_layered_fixtures_switch_scanner_channel_mid_chunk():
    """What makes the test above cover the four context sets and the handshake.

    A file that stayed on one scanner channel would exercise none of it, so
    this pins the property the byte-identity test depends on rather than
    trusting the generator.
    """
    name = "pt6_v3.laz"
    with Reader(fixture(name)) as reader:
        channels = [point.extended_scanner_channel for point in reader]

    assert set(channels) == {0, 1, 2, 3}
    chunk_size = load(name).chunk_size
    assert any(channels[i] != channels[i + 1] and (i + 1) % chunk_size
               for i in range(len(channels) - 1))


# An Eulerian circuit of the four scanner channels: every ordered pair of
# distinct channels appears exactly once, and it closes back on 0 so repeating
# it keeps that true. Every context switch therefore happens in both
# directions, which is what separates a switch that creates a context from one
# that returns to a used one -- the case v3 and v4 disagree about.
CHANNEL_WALK = (0, 1, 0, 2, 0, 3, 1, 2, 1, 3, 2, 3)


def awkward_las14_records(count, items):
    """LAS 1.4 points chosen to reach the branches the fixtures never do.

    Return numbers cover the whole 4-bit range, including the counts above
    seven where the legacy 3-bit copies saturate; the scanner channel follows
    CHANNEL_WALK, holding each channel long enough for its contexts to carry
    real state; and the gps times repeat, creep, leap and drop back into an
    earlier sequence.
    """
    rand = random.Random(14)
    records = []
    sequences = [1.0e9, 5.0e9, 9.0e9]
    for i in range(count):
        number_of_returns = 1 + (i % 15)
        return_number = 1 + (i % number_of_returns)
        scanner_channel = CHANNEL_WALK[(i // 7) % len(CHANNEL_WALK)]
        which = (i // 11) % len(sequences)
        sequences[which] += (0.0 if i % 9 == 0 else
                             1.0e6 if i % 53 == 0 else
                             0.0005 * (1 + i % 37))

        record = bytearray()
        for item_type, size, _ in items:
            if item_type == ItemType.POINT14:
                record += pack_point14(
                    rand.randrange(-2**30, 2**30) if i % 5 == 0 else i * 11,
                    i * -13 if i % 4 else rand.randrange(-2**30, 2**30),
                    (i * i) % 70000, rand.randrange(65536),
                    return_number, number_of_returns,
                    i % 16, scanner_channel, i & 1, (i >> 1) & 1,
                    rand.randrange(256), rand.randrange(256),
                    rand.randrange(-32768, 32768), rand.randrange(65536),
                    sequences[which])
            elif item_type in (ItemType.RGB14, ItemType.RGBNIR14):
                channels = size // 2
                record += struct.pack(f"<{channels}H",
                                      *(rand.randrange(65536)
                                        for _ in range(channels)))
            elif item_type == ItemType.WAVEPACKET14:
                record += struct.pack("<BQIifff", rand.randrange(256),
                                      rand.randrange(2**40),
                                      rand.randrange(2**20),
                                      rand.randrange(-2**20, 2**20),
                                      *(rand.uniform(-1, 1) for _ in range(3)))
            elif item_type == ItemType.BYTE14:
                record += bytes(rand.randrange(256) for _ in range(size))
            else:
                raise AssertionError(f"unexpected item type {item_type}")
        records.append(bytes(record))
    return records


class TestLayeredPointsReadBack:
    """
    Byte-identity only covers the points laszip happened to generate. These
    feed the layered writers deliberately awkward points, wrap the result in a
    real container and read it back.

    Point format 10 carries POINT14, RGBNIR14, WAVEPACKET14 and BYTE14 at once;
    format 7 is what covers the three-channel RGB14, which no other format has.
    """

    @pytest.mark.parametrize("point_format", (7, 10))
    @pytest.mark.parametrize("version", (3, 4))
    def test_round_trips_across_chunks(self, point_format, version):
        name = f"pt{point_format}_v{version}.laz"
        records = awkward_las14_records(400, load(name).items)
        assert len(records) > load(name).chunk_size      # more than one chunk

        # every ordered pair of scanner channels, staying put included, and
        # POINT14 is item 0 so its record is at the front
        channels = [point14_scanner_channel(record) for record in records]
        assert len(set(zip(channels, channels[1:]))) == 16

        raw = f"pt{point_format}_v0.las"
        with Reader(rebuilt(raw, b"".join(records), len(records))) as reader:
            expected = reader.checksum()

        block = written_block(name, records)
        with Reader(rebuilt(name, block, len(records))) as reader:
            assert reader.checksum() == expected


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
                         (rand.randrange(-2**30, 2**30) if i % 7 == 0
                          else i * 13),
                         i * -7 if i % 3 else rand.randrange(-2**30, 2**30),
                         i * i % 90000)
        struct.pack_into("<H", r, 12, rand.randrange(65536))
        for offset in (14, 15, 16, 17):
            r[offset] = rand.randrange(256)
        struct.pack_into("<H", r, 18, rand.randrange(65536))
        gps_time += (1e6 if i % 50 == 0 else
                     0.0 if i % 11 == 0 else 0.001 * rand.randrange(1000))
        struct.pack_into("<d", r, 20, gps_time)
        struct.pack_into("<HHH", r, 28,
                         *(rand.randrange(65536) for _ in range(3)))
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
    length = load("pt5_v0.las").header["point_data_record_length"]
    return awkward_records(400, length)


@pytest.fixture(scope="module")
def expected(records):
    """What those records decode to when nothing compresses them."""
    fp = rebuilt("pt5_v0.las", b"".join(records), len(records))
    with Reader(fp) as reader:
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
        block = written_block(name, records)
        with Reader(rebuilt(name, block, len(records))) as reader:
            assert reader.checksum() == expected

    def test_v2_round_trips_across_chunks(self, records, expected):
        name = "pt5_v2.laz"
        assert len(records) > load(name).chunk_size      # more than one chunk

        block = written_block(name, records)
        with Reader(rebuilt(name, block, len(records))) as reader:
            assert reader.checksum() == expected


# ---------------------------------------------------------------------------
# The container itself: where the chunk boundaries fall, and the table that
# records them.
#
# Byte-identity against the fixtures already pins the fixed-size chunk table
# laszip writes, so what is left is the sizes and shapes testdata/ has no file
# for -- boundaries that fall awkwardly against the point count, chunks the
# caller ends itself, and an output that cannot seek back to patch the offset
# it left in front of the first chunk. Those are checked by reading the result
# back, which is the only oracle there is without a laszip beside us.
# ---------------------------------------------------------------------------

class WriteOnly:
    """A sink that can only be appended to, as a pipe can: no tell, no seek.

    It has to be told where in the file it starts, since it cannot say.
    """

    def __init__(self, buffer):
        self.buffer = buffer

    def write(self, data):
        return self.buffer.write(data)


WrittenFile = collections.namedtuple("WrittenFile", "data number_chunks")


class TestChunking:
    """Point format 5 again, because it carries every legacy item at once."""

    NAME = "pt5_v2.laz"

    def written(self, records, chunk_size, breaks=(), seekable=True):
        """`records` written at `chunk_size`, as a file a Reader can open.

        The header has to declare that chunk size, so it is rebuilt rather than
        taken from the fixture; the writer is told where it ends, since a
        write-only sink cannot say where it is.
        """
        header = rebuilt_header(self.NAME, len(records), chunk_size)
        buffer = io.BytesIO(header)
        buffer.seek(0, io.SEEK_END)
        writer = compress(buffer if seekable else WriteOnly(buffer), records,
                          load(self.NAME).items, Compressor.POINTWISE_CHUNKED,
                          chunk_size, breaks, start_offset=len(header))
        return WrittenFile(buffer.getvalue(), writer.number_chunks)

    @pytest.mark.parametrize("chunk_size", (1, 137, 400, 1000))
    def test_round_trips_at_any_chunk_size(self, records, expected,
                                           chunk_size):
        """One point per chunk, a size that divides the input unevenly, exactly
        the point count, and more than it."""
        written = self.written(records, chunk_size)
        assert written.number_chunks == -(-len(records) // chunk_size)
        with Reader(io.BytesIO(written.data)) as reader:
            assert reader.checksum() == expected

    def test_adaptive_chunks_round_trip(self, records, expected):
        """Chunk boundaries the caller picks, recorded as point counts in the
        table alongside the byte lengths."""
        breaks = (1, 2, 199, 200)
        written = self.written(records, -1, breaks=breaks)
        assert written.number_chunks == len(breaks) + 1
        with Reader(io.BytesIO(written.data)) as reader:
            assert reader.checksum() == expected

    def test_seeking_into_adaptive_chunks(self, records):
        """What the point counts in an adaptive table are for: without them a
        reader cannot tell which chunk holds a given point."""
        written = self.written(records, -1, breaks=(1, 2, 199, 200))
        with Reader(io.BytesIO(written.data)) as reader:
            sequential = [(p.X, p.gps_time) for p in reader]
            for index in (0, 250, 1, 199, len(records) - 1, 200, 2):
                reader.seek(index)
                point = reader.read()
                assert (point.X, point.gps_time) == sequential[index]

    def test_closing_a_chunk_no_point_went_into_does_nothing(self, records):
        """So a caller may end every chunk itself without special-casing
        the first one, or two boundaries that fall together."""
        assert (self.written(records, -1, breaks=(0, 1, 1, 200)) ==
                self.written(records, -1, breaks=(1, 200)))

    def test_a_fixed_size_chunk_cannot_be_closed_early(self, records):
        with pytest.raises(LazError):
            self.written(records, 137, breaks=(50,))

    def test_a_non_seekable_output_appends_the_chunk_table_offset(
            self, records):
        """With nowhere to patch, the offset in front of the first chunk is -1
        and the real one goes at the very end -- which is what the reader
        already knows to look for."""
        written = self.written(records, 137, seekable=False)
        seekable = self.written(records, 137)
        start = load(self.NAME).header["offset_to_point_data"]

        assert struct.unpack_from("<q", written.data, start)[0] == -1
        table_start = struct.unpack_from(
            "<q", written.data, len(written.data) - 8)[0]
        assert table_start == struct.unpack_from("<q", seekable.data, start)[0]

        # the two differ in nothing else: same points, same table, same place
        assert written.data[:start] == seekable.data[:start]
        assert written.data[start + 8:-8] == seekable.data[start + 8:]

    def test_a_non_seekable_output_reads_back(self, records, expected):
        written = self.written(records, 137, seekable=False)
        with Reader(io.BytesIO(written.data)) as reader:
            assert reader.checksum() == expected


class TestPointWriterErrors:

    def items(self):
        return load("pt1_v2.laz").items

    def writer(self, items=None, compressor=Compressor.POINTWISE_CHUNKED):
        return cpylaz.PointWriter(io.BytesIO(),
                                  self.items() if items is None else items,
                                  compressor)

    def test_rejects_a_record_of_the_wrong_size(self):
        with pytest.raises(ValueError):
            self.writer().write(b"\x00" * 3)

    def test_rejects_an_unknown_item_version(self):
        with pytest.raises(LazError):
            self.writer([(t, size, 7) for t, size, _ in self.items()])

    def test_rejects_raw_items_in_a_compressed_container(self):
        """Version 0 means the item is stored uncompressed, which no compressed
        container has a writer for."""
        items = list(self.items())
        items[0] = (items[0][0], items[0][1], 0)
        with pytest.raises(LazError):
            self.writer(items)

    def test_rejects_no_items(self):
        with pytest.raises(ValueError):
            self.writer([])

    def test_writes_nothing_for_no_points(self):
        fp = io.BytesIO()
        cpylaz.PointWriter(fp, self.items(), Compressor.NONE).done()
        assert fp.getvalue() == b""

    def test_an_empty_chunked_file_still_gets_a_chunk_table(self):
        """Eight bytes of offset, then a table of no chunks."""
        fp = io.BytesIO()
        writer = cpylaz.PointWriter(fp, self.items(),
                                    Compressor.POINTWISE_CHUNKED)
        writer.done()
        assert fp.getvalue() == struct.pack("<qII", 8, 0, 0)

    def test_a_finished_writer_takes_nothing_more(self):
        writer = self.writer()
        writer.done()
        for call in (lambda: writer.write(b"\x00" * 28), writer.chunk,
                     writer.done):
            with pytest.raises(ValueError):
                call()

    def test_a_failing_sink_propagates_its_own_exception(self):
        """Neither swallowed nor relabelled a LazError: the file object's own
        error is what the caller has to see.

        The sink buffers, so a file this short only reaches it when the writer
        is closed -- which is exactly why done() has to be able to fail.
        """
        class Broken(io.BytesIO):
            def write(self, data):
                raise OSError("disk is on fire")

        items = [(t, size, 0) for t, size, _ in self.items()]
        writer = cpylaz.PointWriter(Broken(), items, Compressor.NONE)
        writer.write(las_records("pt1_v0.las")[0])
        with pytest.raises(OSError, match="disk is on fire"):
            writer.done()


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
            # we did not open it, so we do not close it
            assert not fh.closed


class TestItemLayout:
    def test_known_formats(self):
        items = lazpy.items_for_point_format(1, 28)
        assert [t for t, _, _ in items] == [ItemType.POINT10,
                                            ItemType.GPSTIME11]

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
        with Reader(fixture("pt6_v3.laz"),
                    decompress_selective=mask) as partial:
            got = [(p.X, p.Y) for p in partial]
        assert got == want

    def test_skipped_attributes_are_frozen(self):
        mask = Selective.ALL & ~Selective.Z
        with Reader(fixture("pt6_v3.laz"),
                    decompress_selective=mask) as reader:
            zs = {p.Z for p in reader}
        # Z never decodes, so it keeps the chunk's first point's value
        assert len(zs) < 10

    def test_full_mask_is_the_default(self):
        with Reader(fixture("pt8_v4.laz")) as a:
            default = a.checksum()
        with Reader(fixture("pt8_v4.laz"),
                    decompress_selective=Selective.ALL) as b:
            explicit = b.checksum()
        assert default == explicit


# ---------------------------------------------------------------------------
# The array API.
#
# numpy is optional -- nothing else in lazpy needs it -- so these skip rather
# than fail when it is absent, and CI installs it so they actually run.
# ---------------------------------------------------------------------------

try:
    import numpy as np
except ImportError:                                     # pragma: no cover
    np = None

needs_numpy = pytest.mark.skipif(np is None, reason="needs numpy")

RGB_NAMES = ("red", "green", "blue", "nir")


def point_field(point, name):
    """The value ``arrays()`` puts in a column, read the per-point way.

    Two shapes differ between the APIs on purpose: the blobs come back as
    arrays of bytes rather than bytes, and the colour channels are columns of
    their own rather than one rgb tuple.
    """
    if name in ("wave_packet", "extra_bytes"):
        return np.frombuffer(getattr(point, name), dtype="u1")
    if name in RGB_NAMES:
        return point.rgb[RGB_NAMES.index(name)]
    return getattr(point, name)


@needs_numpy
@pytest.mark.parametrize("name", FIXTURES)
def test_arrays_match_reading_point_by_point(name):
    """Every column, every point, against the API already pinned to laszip.

    This is what makes the bit-unpacking trustworthy: return number and the
    classification flags are shifts and masks over a byte on the array path
    and C bitfields on the point path, and they have to agree.
    """
    with Reader(fixture(name)) as reader:
        columns = reader.arrays()
    with Reader(fixture(name)) as reader:
        for i, point in enumerate(reader):
            for field, column in columns.items():
                assert np.array_equal(column[i], point_field(point, field)), \
                    f"{field} differs at point {i} of {name}"


@needs_numpy
class TestArrays:

    @pytest.mark.parametrize("name, present, absent", [
        ("pt0_v2.laz", {"X", "Y", "Z", "classification"},
         {"gps_time", "red", "wave_packet"}),
        ("pt3_v2.laz", {"gps_time", "red", "green", "blue"}, {"wave_packet"}),
        ("pt4_v2.laz", {"wave_packet", "gps_time"}, {"red", "nir"}),
        ("pt8_v3.laz", {"nir", "extended_classification",
                        "extended_scan_angle"}, {"wave_packet"}),
    ])
    def test_default_columns_follow_the_point_format(self, name, present,
                                                     absent):
        with Reader(fixture(name)) as reader:
            columns = set(reader.arrays())
        assert present <= columns
        assert not (absent & columns)

    def test_naming_fields_reads_only_those(self):
        with Reader(fixture("pt3_v2.laz")) as reader:
            columns = reader.arrays("X", "gps_time")
        assert list(columns) == ["X", "gps_time"]

    def test_columns_are_typed_by_the_field(self):
        with Reader(fixture("pt3_v2.laz")) as reader:
            columns = reader.arrays("X", "intensity", "classification",
                                    "gps_time", "scan_angle_rank")
        assert columns["X"].dtype == np.int32
        assert columns["intensity"].dtype == np.uint16
        assert columns["classification"].dtype == np.uint8
        assert columns["gps_time"].dtype == np.float64
        assert columns["scan_angle_rank"].dtype == np.int8

    def test_blobs_come_back_as_rows_of_bytes(self):
        with Reader(fixture("pt4_v2.laz")) as reader:
            columns = reader.arrays("wave_packet")
            assert columns["wave_packet"].shape == (reader.num_points, 29)

        with Reader(fixture("pt1_v2.laz")) as reader:
            extra = reader.num_extra_bytes
            assert extra                        # the fixture has extra bytes
            columns = reader.arrays("extra_bytes")
            assert columns["extra_bytes"].shape == (reader.num_points, extra)

    def test_reads_the_whole_file_by_default(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            assert len(reader.arrays("X")["X"]) == reader.num_points
            assert reader.index == reader.num_points

    def test_start_seeks(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            whole = reader.arrays("X", start=0)["X"]
            tail = reader.arrays("X", start=400)["X"]
        assert np.array_equal(tail, whole[400:])

    def test_successive_calls_walk_the_file(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            whole = reader.arrays("X", start=0)["X"]
            reader.seek(0)
            blocks = [reader.arrays("X", count=200)["X"] for _ in range(3)]
        assert [len(b) for b in blocks] == [200, 200, 100]
        assert np.array_equal(np.concatenate(blocks), whole)

    def test_a_count_past_the_end_stops_at_the_end(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            columns = reader.arrays("X", count=10 ** 6)
            assert len(columns["X"]) == reader.num_points

    def test_count_zero_reads_nothing(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            assert len(reader.arrays("X", count=0)["X"]) == 0
            assert reader.index == 0

    def test_xyz_is_the_scaled_point(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            xyz = reader.xyz()
            reader.seek(0)
            want = [reader.scale(p) for p in reader]
        assert xyz.shape == (len(want), 3)
        assert xyz.dtype == np.float64
        assert np.array_equal(xyz, np.array(want))

    def test_xyz_takes_a_range(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            whole = reader.xyz()
            part = reader.xyz(start=100, count=50)
        assert np.array_equal(part, whole[100:150])

    def test_rejects_an_unknown_field(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            with pytest.raises(ValueError, match="unknown point field"):
                reader.arrays("elevation")

    def test_rejects_extra_bytes_a_file_does_not_have(self, tmp_path):
        # every fixture carries extra bytes, so write a file that does not
        path = str(tmp_path / "plain.laz")
        with Writer(path, point_format=1) as writer:
            writer.write(Point(X=1, Y=2, Z=3))

        with Reader(path) as reader:
            assert reader.num_extra_bytes == 0
            assert "extra_bytes" not in reader.arrays()
            with pytest.raises(ValueError, match="no extra bytes"):
                reader.arrays("extra_bytes")


@needs_numpy
class TestReadInto:
    """The raw C entry point the array API is built on."""

    def targets(self, count, size, offset=0):
        """A one-column target list, sized to hold *count* of the field."""
        return [(np.empty(count * size, dtype="u1"), offset, size)]

    def test_rejects_a_short_buffer(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            with pytest.raises(ValueError, match="too small"):
                reader._reader.read_into(self.targets(4, 4), 5)

    def test_rejects_a_field_outside_the_point(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            targets = self.targets(1, 8, offset=72)
            with pytest.raises(ValueError, match="outside the decoded point"):
                reader._reader.read_into(targets, 1)

    def test_rejects_an_offset_that_is_neither_a_field_nor_the_blob(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            targets = self.targets(1, 1, offset=-2)
            with pytest.raises(ValueError, match="outside the decoded point"):
                reader._reader.read_into(targets, 1)

    def test_rejects_extra_bytes_wider_than_the_file_has(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            width = reader.num_extra_bytes + 1
            targets = self.targets(1, width, offset=-1)
            with pytest.raises(ValueError, match="outside the extra bytes"):
                reader._reader.read_into(targets, 1)

    def test_rejects_a_read_only_buffer(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            with pytest.raises(Exception):
                reader._reader.read_into([(b"xxxx", 0, 4)], 1)

    def test_a_failed_read_still_counts_what_it_decoded(self):
        """A run that dies part way leaves the index on the points it got.

        Nothing here knows where the file ends -- that is the header's job,
        and Reader.arrays() clamps to it -- so asking for more points than
        there are runs into the end of the stream. Some of those reads decode
        whatever is left in the chunk before one of them fails, so the index
        lands past the last real point but short of what was asked for.
        """
        with Reader(fixture("pt1_v2.laz")) as reader:
            want = reader.num_points + 50
            with pytest.raises(LazError):
                reader._reader.read_into(self.targets(want, 4), want)
            assert reader.num_points <= reader.index < want


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


# ---------------------------------------------------------------------------
# The Writer front end.
#
# These cover the whole path at once: a header and a LASzip VLR built in
# Python, points through the container, and the result read back. The oracle
# is reference_hashes.txt -- laszip's own checksum of every field of every
# point of the fixture the points came from -- so a round trip that reproduces
# it is one laszip agrees with, field for field, extra bytes included.
#
# Where the source points are laszip's own and the container settings match
# its, the check is stronger still: the point block comes out byte-identical,
# which also pins the header size and the VLR that decide where it starts.
# ---------------------------------------------------------------------------

# Every point format against every item version that applies to it, with None
# for "no compression at all".
WRITER_CASES = ([(pf, v) for pf in LEGACY_FORMATS for v in (None, 1, 2)] +
                [(pf, v) for pf in LAS14_FORMATS for v in (None, 3, 4)])
WRITER_IDS = [f"pt{pf}_{'raw' if v is None else 'v%d' % v}"
              for pf, v in WRITER_CASES]


def source_fixture(point_format):
    """The fixture whose points a writer test writes.

    Above point format 5 the ptN_v0.las fixture is not a faithful copy of the
    points -- see the layered section above -- so the compressed file is the
    source there. Its committed checksum covers v3 and v4 alike, since both
    hold the same points.
    """
    return (f"pt{point_format}_v0.las" if point_format < 6
            else f"pt{point_format}_v3.laz")


def source_points(name):
    """Every point of a fixture, detached from the reader's buffer, plus the
    layout facts a writer needs to take them."""
    with Reader(fixture(name)) as reader:
        points = [point.copy() for point in reader]
        return points, dict(num_extra_bytes=reader.num_extra_bytes,
                            scales=reader.scales, offsets=reader.offsets)


def written_file(point_format, laz_version, points, layout, breaks=(),
                 **kwargs):
    """`points` written out whole, as bytes.

    `breaks` are the indices to end a chunk in front of, as in `compress`,
    which only a file with variable-size chunks allows.
    """
    buf = io.BytesIO()
    with Writer(buf, point_format, compressed=laz_version is not None,
                laz_version=laz_version, **layout, **kwargs) as writer:
        for index, point in enumerate(points):
            if index in breaks:
                writer.chunk()
            writer.write(point)
    return buf.getvalue()


@pytest.mark.parametrize("point_format,laz_version", WRITER_CASES,
                         ids=WRITER_IDS)
def test_round_trips_every_format_and_version(point_format, laz_version):
    """Write a fixture's points, read back, and match laszip's checksum."""
    name = source_fixture(point_format)
    points, layout = source_points(name)

    data = written_file(point_format, laz_version, points, layout)

    with Reader(io.BytesIO(data)) as reader:
        assert reader.point_format == point_format
        assert reader.is_compressed == (laz_version is not None)
        assert reader.checksum() == REFERENCE_HASH[name]


@pytest.mark.parametrize("point_format",
                         list(LEGACY_FORMATS) + list(LAS14_FORMATS))
def test_the_written_point_block_is_byte_identical_to_laszip(point_format):
    """Given laszip's own points and its chunk size, at the item version it
    used, every byte behind the header is the byte laszip wrote.

    All but one: the eight bytes in front of the first chunk hold the position
    of the chunk table, and that is the one thing in a point block that depends
    on where the block starts. The generator wrote the wavepacket formats as
    LAS 1.2, which lazpy will not do -- those formats arrived in 1.3 -- so for
    4 and 5 its header is eight bytes longer and everything shifts by eight.
    """
    laz_version = 2 if point_format < 6 else 3
    name = f"pt{point_format}_v{laz_version}.laz"
    points, layout = source_points(source_fixture(point_format))

    data = written_file(point_format, laz_version, points, layout,
                        chunk_size=load(name).chunk_size)

    block = point_block(name)
    with Reader(io.BytesIO(data)) as reader:
        start = reader.header["offset_to_point_data"]
    shift = start - load(name).header["offset_to_point_data"]

    assert data[start + 8:] == block[8:]
    assert (struct.unpack_from("<q", data, start)[0] ==
            struct.unpack_from("<q", block, 0)[0] + shift)


@pytest.mark.parametrize("point_format",
                         list(LEGACY_FORMATS) + list(LAS14_FORMATS))
def test_the_laszip_vlr_matches_the_one_laszip_writes(point_format):
    """Everything the VLR declares about the encoding, byte for byte: the
    compressor, the coder, the chunk size and every item triple.

    Only the description differs, which is free text naming the writer.
    """
    laz_version = 2 if point_format < 6 else 3
    name = f"pt{point_format}_v{laz_version}.laz"
    points, layout = source_points(source_fixture(point_format))

    data = written_file(point_format, laz_version, points, layout,
                        chunk_size=load(name).chunk_size)

    with Reader(io.BytesIO(data)) as reader:
        vlrs = reader.header["variable_length_records"]
        written = vlrs[LASZIP_VLR_KEY]
    expected = load(name).header["variable_length_records"][LASZIP_VLR_KEY]

    assert written["data"] == expected["data"]
    assert written["user_id"] == expected["user_id"]
    assert written["record_id"] == expected["record_id"]


class TestWrittenHeader:
    """The fields a header cannot be finished without."""

    def written(self):
        """Point format 1 written back out, as its header and its points."""
        points, layout = source_points(source_fixture(1))
        data = written_file(1, 2, points, layout)
        with Reader(io.BytesIO(data)) as reader:
            return reader.header, points

    def test_bounds_are_the_extremes_of_the_points_written(self):
        header, points = self.written()
        x_scale, z_scale = header["x_scale_factor"], header["z_scale_factor"]
        assert header["min_x"] == min(p.X for p in points) * x_scale
        assert header["max_x"] == max(p.X for p in points) * x_scale
        assert header["min_z"] == min(p.Z for p in points) * z_scale
        assert header["max_z"] == max(p.Z for p in points) * z_scale

    def test_counts_by_return_number(self):
        header, points = self.written()
        expected = collections.Counter(p.return_number for p in points)
        assert header["number_of_points_by_return"] == \
            [expected[n] for n in range(1, 6)]

    def test_las_14_keeps_the_real_count_out_of_the_legacy_field(self):
        """Which is the rule Reader compensates for on the way back in, so the
        raw bytes are what has to be checked."""
        points, layout = source_points(source_fixture(6))
        data = written_file(6, 3, points, layout)

        legacy = struct.unpack_from("<I", data, LEGACY_POINT_COUNT_OFFSET)
        extended = struct.unpack_from("<Q", data, EXTENDED_POINT_COUNT_OFFSET)
        by_return = struct.unpack_from("<5I", data,
                                       LEGACY_POINT_COUNT_OFFSET + 4)
        assert legacy[0] == 0
        assert extended[0] == len(points)
        assert by_return == (0,) * 5

    def test_a_legacy_format_in_a_las_14_file_fills_in_both(self):
        points, layout = source_points(source_fixture(1))
        data = written_file(1, 2, points, layout, version_minor=4)

        legacy = struct.unpack_from("<I", data, LEGACY_POINT_COUNT_OFFSET)
        extended = struct.unpack_from("<Q", data, EXTENDED_POINT_COUNT_OFFSET)
        assert legacy[0] == len(points)
        assert extended[0] == len(points)

    def test_an_empty_file_is_still_a_file(self):
        buf = io.BytesIO()
        with Writer(buf, 1) as writer:
            assert writer.num_points == 0
        buf.seek(0)
        with Reader(buf) as reader:
            assert reader.num_points == 0
            assert list(reader) == []

    @pytest.mark.parametrize("point_format,expected", [(1, 2), (4, 3), (6, 4)])
    def test_las_version_defaults_to_the_oldest_that_fits_the_format(
            self, point_format, expected):
        """Wavepackets arrived in LAS 1.3, extended point types in 1.4."""
        buf = io.BytesIO()
        Writer(buf, point_format).close()
        buf.seek(0)
        with Reader(buf) as reader:
            assert reader.header["version_minor"] == expected


class TestWriterFiles:

    def test_writes_a_file_by_name(self, tmp_path):
        path = tmp_path / "cloud.laz"
        points, layout = source_points("pt1_v0.las")
        with Writer(str(path), 1, **layout) as writer:
            for point in points:
                writer.write(point)

        with Reader(str(path)) as reader:
            assert reader.is_compressed
            assert reader.checksum() == REFERENCE_HASH["pt1_v0.las"]

    def test_a_las_extension_writes_an_uncompressed_file(self, tmp_path):
        path = tmp_path / "cloud.las"
        with Writer(str(path), 1) as writer:
            writer.write(Point(X=1, Y=2, Z=3))

        with Reader(str(path)) as reader:
            assert not reader.is_compressed

    def test_records_go_in_as_well_as_points(self):
        """What a file being converted already has, and what the item layout
        describes."""
        records = las_records("pt1_v0.las")
        buf = io.BytesIO()
        with Writer(buf, 1, num_extra_bytes=6) as writer:
            for record in records:
                writer.write(record)

        buf.seek(0)
        with Reader(buf) as reader:
            assert reader.checksum() == REFERENCE_HASH["pt1_v0.las"]

    def test_close_is_idempotent_and_final(self):
        buf = io.BytesIO()
        writer = Writer(buf, 1)
        writer.write(Point(X=1))
        writer.close()
        writer.close()
        with pytest.raises(ValueError):
            writer.write(Point(X=2))
        assert writer.num_points == 1     # still answers for what it wrote

    def test_a_lent_file_object_is_left_at_the_end_of_what_was_written(self):
        """Closing goes back to the front to patch the header, and comes back:
        a caller who lent us an open file may have plans for it."""
        buf = io.BytesIO()
        with Writer(buf, 1) as writer:
            writer.write(Point(X=1))
        assert buf.tell() == len(buf.getvalue())
        assert not buf.closed          # not ours to close

    def test_header_fields_set_before_closing_reach_the_file(self):
        buf = io.BytesIO()
        with Writer(buf, 1) as writer:
            writer.header["system_identifier"] = b"a survey rig"
            writer.header["file_source_id"] = 7
            writer.write(Point(X=1))

        buf.seek(0)
        with Reader(buf) as reader:
            assert reader.header["system_identifier"] == b"a survey rig"
            assert reader.header["file_source_id"] == 7

    def test_an_impossible_request_creates_no_file(self, tmp_path):
        """What cannot be written is settled before anything is opened."""
        path = tmp_path / "cloud.laz"
        with pytest.raises(LazError):
            Writer(str(path), 6, version_minor=2)      # 6 needs LAS 1.4
        assert not path.exists()


class TestWriterErrors:

    def test_rejects_an_unknown_point_format(self):
        with pytest.raises(UnsupportedFileError):
            Writer(io.BytesIO(), 11)

    @pytest.mark.parametrize("point_format,laz_version",
                             [(1, 3), (1, 4), (6, 1), (6, 2), (1, 9)])
    def test_rejects_an_item_version_the_format_does_not_have(
            self, point_format, laz_version):
        with pytest.raises(UnsupportedFileError):
            Writer(io.BytesIO(), point_format, laz_version=laz_version)

    def test_rejects_an_item_version_for_an_uncompressed_file(self):
        with pytest.raises(ValueError):
            Writer(io.BytesIO(), 1, compressed=False, laz_version=2)

    @pytest.mark.parametrize("point_format,compressor", [
        (1, Compressor.LAYERED_CHUNKED),    # layers are a LAS 1.4 thing
        (6, Compressor.POINTWISE),          # and predate the 1.4 items
        (6, Compressor.POINTWISE_CHUNKED),
    ])
    def test_rejects_a_container_the_items_cannot_use(self, point_format,
                                                      compressor):
        """The core refuses this too, and has to: a container and its writers
        that disagree about layering call through a hook that is not there."""
        with pytest.raises(UnsupportedFileError):
            Writer(io.BytesIO(), point_format, compressor=compressor)

    def test_the_core_refuses_a_container_the_items_cannot_use(self):
        """Below the Writer, where the crash would be. PointWriter is public
        and the test suite drives it directly."""
        with pytest.raises(LazError):
            cpylaz.PointWriter(io.BytesIO(), load("pt1_v2.laz").items,
                               Compressor.LAYERED_CHUNKED)
        with pytest.raises(LazError):
            cpylaz.PointWriter(io.BytesIO(), load("pt6_v3.laz").items,
                               Compressor.POINTWISE_CHUNKED)

    def test_rejects_a_compressor_for_an_uncompressed_file(self):
        with pytest.raises(ValueError):
            Writer(io.BytesIO(), 1, compressed=False,
                   compressor=Compressor.POINTWISE_CHUNKED)

    def test_ending_a_chunk_needs_a_variable_size_one(self):
        with Writer(io.BytesIO(), 1, chunk_size=137) as writer:
            writer.write(Point(X=1))
            with pytest.raises(LazError):
                writer.chunk()

    def test_rejects_a_point_format_the_las_version_cannot_describe(self):
        with pytest.raises(UnsupportedFileError):
            Writer(io.BytesIO(), 4, version_minor=2)   # wavepackets need 1.3

    def test_rejects_an_output_that_cannot_seek(self):
        """The count and the bounding box are only known at the end, and they
        belong at the front."""
        class WriteOnlyFile:
            def write(self, data):
                return len(data)

        with pytest.raises(ValueError, match="seekable"):
            Writer(WriteOnlyFile(), 1)

    def test_rejects_a_header_edit_that_changes_the_header_length(self):
        """Fields can be set until the file is closed, but not ones that move
        the points: that distance is already written, twice over."""
        buf = io.BytesIO()
        writer = Writer(buf, 1)
        writer.write(Point(X=1))
        writer.header["version_minor"] = 4          # eight bytes longer
        with pytest.raises(LazError, match="header"):
            writer.close()

    def test_rejects_more_extra_bytes_than_the_layout_holds(self):
        buf = io.BytesIO()
        with Writer(buf, 1, num_extra_bytes=2) as writer:
            with pytest.raises(ValueError):
                writer.write(Point(extra_bytes=b"abcd"))

    def test_pads_fewer_extra_bytes_than_the_layout_holds(self):
        """An unset field is zero everywhere else, and so is an unset byte."""
        buf = io.BytesIO()
        with Writer(buf, 1, num_extra_bytes=4) as writer:
            writer.write(Point(extra_bytes=b"ab"))
            writer.write(Point())
        buf.seek(0)
        with Reader(buf) as reader:
            assert [p.extra_bytes for p in reader] == [b"ab\0\0", b"\0" * 4]


class TestWritablePoint:

    def test_builds_a_point_from_keywords(self):
        point = Point(X=-5, Y=6, Z=7, intensity=300, classification=31,
                      gps_time=1.25, rgb=(1, 2, 3, 4), user_data=9,
                      wave_packet=bytes(range(29)), extra_bytes=b"xyz")
        assert (point.X, point.Y, point.Z) == (-5, 6, 7)
        assert point.intensity == 300
        assert point.classification == 31
        assert point.gps_time == 1.25
        assert point.rgb == (1, 2, 3, 4)
        assert point.user_data == 9
        assert point.wave_packet == bytes(range(29))
        assert point.extra_bytes == b"xyz"

    def test_rgb_takes_three_channels_and_leaves_the_fourth(self):
        point = Point(rgb=(1, 2, 3, 4))
        point.rgb = (7, 8, 9)
        assert point.rgb == (7, 8, 9, 4)

    @pytest.mark.parametrize("field,value", [
        ("classification", 32),          # five bits
        ("return_number", 8),            # three
        ("extended_return_number", 16),  # four
        ("intensity", 65536),
        ("scan_angle_rank", 128),
        ("extended_scan_angle", 32768),
        ("X", 2 ** 31),
    ])
    def test_refuses_a_value_the_field_cannot_hold(self, field, value):
        """Rather than truncating it into a neighbouring bitfield."""
        with pytest.raises((ValueError, OverflowError)):
            setattr(Point(), field, value)

    def test_refuses_an_unknown_field(self):
        with pytest.raises(AttributeError):
            Point(elevation=3)

    def test_takes_no_positional_arguments(self):
        with pytest.raises(TypeError):
            Point(1, 2, 3)

    def test_a_readers_point_is_its_buffer_and_stays_that_way(self):
        """Assignment writes through to the reader's own point rather than
        quietly detaching it, which would leave the reader holding a Point
        that no longer followed it."""
        with Reader(fixture("pt1_v2.laz")) as reader:
            point = reader.read()
            point.X = 12345
            assert point.X == 12345

            assert reader.read() is point
            assert point.X != 12345          # the next point, not the edit

    def test_a_copy_can_be_resized_and_the_original_cannot(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            point = reader.read()
            assert len(point.extra_bytes) == 6
            point.extra_bytes = b"abcdef"    # same size: written in place
            assert point.extra_bytes == b"abcdef"

            with pytest.raises(ValueError, match="copy"):
                point.extra_bytes = b"ab"

            detached = point.copy()
            detached.extra_bytes = b"ab"
            assert detached.extra_bytes == b"ab"

    def test_a_point_built_by_hand_round_trips(self):
        """Including the LAS 1.4 fields, which only reach the file because the
        writer marks the point it is given as an extended one."""
        point = Point(X=1, Y=2, Z=3, extended_return_number=9,
                      extended_number_of_returns=11,
                      extended_scanner_channel=2,
                      extended_classification=200, extended_scan_angle=-4000,
                      keypoint_flag=1, extended_classification_flags=0b1000,
                      gps_time=99.5, rgb=(10, 20, 30, 40))
        buf = io.BytesIO()
        with Writer(buf, 8) as writer:
            writer.write(point)

        buf.seek(0)
        with Reader(buf) as reader:
            back = reader.read()
            for field in ("X", "Y", "Z", "extended_return_number",
                          "extended_number_of_returns",
                          "extended_scanner_channel",
                          "extended_classification", "extended_scan_angle",
                          "keypoint_flag", "gps_time", "rgb"):
                assert getattr(back, field) == getattr(point, field), field

    def test_the_legacy_flags_are_what_reaches_a_las_14_record(self):
        """Three of the four classification flags are held twice over in a
        decoded point, and it is the legacy copies a record is built from --
        LASzip's rule, and the reason a hand-built point sets those."""
        point = Point(X=1, synthetic_flag=1, withheld_flag=1,
                      extended_classification_flags=0b1111)
        buf = io.BytesIO()
        with Writer(buf, 6) as writer:
            writer.write(point)

        buf.seek(0)
        with Reader(buf) as reader:
            back = reader.read()
            # synthetic and withheld survived; the keypoint bit set only in
            # extended_classification_flags did not, and overlap did
            assert (back.synthetic_flag, back.keypoint_flag,
                    back.withheld_flag) == (1, 0, 1)
            assert back.extended_classification_flags == 0b1101


class TestWriterContainers:
    """Which container the points go in, and where its chunks end -- the
    writer's most consequential choice for whoever reads the file back, since
    it decides whether they can seek at all.

    What the container does with the points is `TestChunking`'s, a layer down;
    what is left here is that the writer offers the choice and carries it into
    the file.
    """

    @pytest.mark.parametrize("point_format", (0, 5))
    def test_the_non_chunked_container_matches_laszip(self, point_format):
        """LASzip's original container compresses the whole file as one stream
        with no chunk table, which is how laszip wrote the _pointwise
        fixtures -- so the point block is comparable byte for byte.

        Two formats rather than all six: the per-format item output in this
        container is already pinned against every one of those fixtures by
        test_v1_output_is_byte_identical_to_laszip, and what is new here is
        that a Writer can ask for it.
        """
        name = f"pt{point_format}_v1_pointwise.laz"
        points, layout = source_points(source_fixture(point_format))

        data = written_file(point_format, 1, points, layout,
                            compressor=Compressor.POINTWISE)

        with Reader(io.BytesIO(data)) as reader:
            assert reader.laz_header["compressor"] == Compressor.POINTWISE
            start = reader.header["offset_to_point_data"]
            assert reader.checksum() == REFERENCE_HASH[name]
        assert data[start:] == point_block(name)

    def test_variable_size_chunks_through_the_writer(self):
        """A chunk size of U32_MAX leaves the boundaries to the caller, and is
        declared as -1 in the VLR so a reader knows to expect point counts in
        the chunk table -- which is what lets it seek to one.
        """
        points, layout = source_points("pt1_v0.las")

        data = written_file(1, 2, points, layout, chunk_size=0xFFFFFFFF,
                            breaks=(1, 10, 200, 201))

        with Reader(io.BytesIO(data)) as reader:
            assert reader.chunk_size == -1
            for index in (0, 300, 1, 205, len(points) - 1, 10, 200):
                reader.seek(index)
                assert reader.read().X == points[index].X, index
            reader.seek(0)
            assert reader.checksum() == REFERENCE_HASH["pt1_v0.las"]


def test_the_vlr_description_is_the_callers():
    """Free text naming what wrote the file, and the one field that stopped a
    caller from reproducing a foreign file's VLR byte for byte."""
    buf = io.BytesIO()
    with Writer(buf, 1, vlr_description=b"by laszip of LAStools") as writer:
        writer.write(Point(X=1))

    buf.seek(0)
    with Reader(buf) as reader:
        vlr = reader.header["variable_length_records"][LASZIP_VLR_KEY]
    assert vlr["description"] == b"by laszip of LAStools"


# Every format but the wavepacket two, whose fixtures are LAS 1.2 -- a file
# lazpy will not write, since those formats arrived in 1.3, so its header is
# eight bytes longer and nothing lines up.
WHOLE_FILE_FORMATS = [pf for pf in list(LEGACY_FORMATS) + list(LAS14_FORMATS)
                      if pf not in (4, 5)]


@pytest.mark.parametrize("point_format", WHOLE_FILE_FORMATS)
def test_a_written_file_is_the_file_laszip_wrote(point_format):
    """Given the same points, settings and free text, everything lazpy writes
    is what laszip wrote -- header, VLR, points and chunk table alike.

    Everything except two header statistics, which differ because lazpy fills
    them in and the generator did not: it left the counts by return number at
    zero and wrote the nominal coordinate range rather than the extent of the
    points it had. Those are what `close()` computes, so a writer cannot be
    talked into reproducing them, and should not be.
    """
    laz_version = 2 if point_format < 6 else 3
    name = f"pt{point_format}_v{laz_version}.laz"
    f = load(name)
    header = f.header
    points, layout = source_points(name)

    data = written_file(
        point_format, laz_version, points, layout,
        chunk_size=f.chunk_size,
        version_minor=header["version_minor"],
        system_identifier=header["system_identifier"],
        generating_software=header["generating_software"],
        file_creation=(header["file_creation_day"],
                       header["file_creation_year"]),
        vlr_description=header["variable_length_records"][
            LASZIP_VLR_KEY]["description"])

    computed = ["number_of_points_by_return",
                "min_x", "max_x", "min_y", "max_y", "min_z", "max_z"]
    if header["version_minor"] >= 4:
        computed.append("extended_number_of_points_by_return")

    ours, theirs = bytearray(data), bytearray(f.data)
    for field in computed:
        offset, size = field_span(field, header["version_minor"])
        ours[offset:offset + size] = theirs[offset:offset + size]
    assert bytes(ours) == bytes(theirs)
