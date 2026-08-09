import io
import os

import pytest

import compressor
import cpylaz
import encoder
import lazpy
import models
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

TESTDATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata")


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
