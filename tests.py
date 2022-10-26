import models
import cpylaz
import encoder
import pytest
import compressor
import io
import sys


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


def test_encoder_not_implemented():
    with pytest.raises(NotImplementedError):
        cpylaz.ArithmeticEncoder()

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

class TestLASpoint:
    def test_create(self):
        point = cpylaz.LASpoint()

        assert type(point) == cpylaz.LASpoint

class Testread_item_compressed_point10_v2:
    def test_create(self):
        fp = io.BytesIO(file_contents)
        dec = cpylaz.ArithmeticDecoder(fp)
        dec.start()

        ricp = cpylaz.read_item_compressed_point10_v2(dec)

        assert ricp is not None

        assert ricp.dec == dec

        assert type(ricp.m_changed_values) == cpylaz.ArithmeticModel
        assert type(ricp.ic_intensity) == cpylaz.IntegerCompressor

        m_scan_rank = ricp.m_scan_rank
        assert len(m_scan_rank) == 2
        assert type(m_scan_rank[0]) == cpylaz.ArithmeticModel
        assert type(m_scan_rank[1]) == cpylaz.ArithmeticModel