import models
import cpylaz
import encoder
import pytest
import io


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

    def test_start(self):
        fp = io.BytesIO(file_contents)
        decoder = encoder.ArithmeticDecoder(fp)
        decoder.start()

        assert decoder.fp == fp
        assert decoder.length == 4294967295
        assert decoder.value == 2908556787


class TestCArithmeticDeoder:

    def test_create(self):
        fp = io.BytesIO()
        decoder = cpylaz.ArithmeticDecoder(fp)
        assert decoder.length == 0
        assert decoder.value == 0

    def test_start(self):
        fp = io.BytesIO(file_contents)
        decoder = cpylaz.ArithmeticDecoder(fp)
        decoder.start()
        assert decoder.length == 4294967295
        assert decoder.value == 2908556787