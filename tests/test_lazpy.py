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

# The fields lazpy does not hand back, and that a rebuilt header has to be
# patched at directly. LAS 1.4 zeroes the legacy point count for the extended
# point types and keeps the real one further along.
LEGACY_POINT_COUNT_OFFSET = 107
EXTENDED_POINT_COUNT_OFFSET = 247
# The chunk size sits 12 bytes into the LASzip VLR's payload, which begins 54
# bytes into the record -- whose 16-byte user id starts at offset 2.
LASZIP_VLR_USER_ID = b"laszip encoded"
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
        offset = header.index(LASZIP_VLR_USER_ID) + LASZIP_VLR_CHUNK_SIZE_OFFSET
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
    items = [(t, size, 0) for t, size, _ in load(f"pt{point_format}_v2.laz").items]
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
                                      rand.randrange(2**40), rand.randrange(2**20),
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
    def test_round_trips_at_any_chunk_size(self, records, expected, chunk_size):
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
        """So a caller may end every chunk itself without having to special-case
        the first one, or two boundaries that fall together."""
        assert (self.written(records, -1, breaks=(0, 1, 1, 200)) ==
                self.written(records, -1, breaks=(1, 200)))

    def test_a_fixed_size_chunk_cannot_be_closed_early(self, records):
        with pytest.raises(LazError):
            self.written(records, 137, breaks=(50,))

    def test_a_non_seekable_output_appends_the_chunk_table_offset(self, records):
        """With nowhere to patch, the offset in front of the first chunk is -1
        and the real one goes at the very end -- which is what the reader
        already knows to look for."""
        written = self.written(records, 137, seekable=False)
        seekable = self.written(records, 137)
        start = load(self.NAME).header["offset_to_point_data"]

        assert struct.unpack_from("<q", written.data, start)[0] == -1
        table_start = struct.unpack_from("<q", written.data, len(written.data) - 8)[0]
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
