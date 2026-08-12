
import io

import pytest

import lazpy
from lazpy import (Chunking, Compressor, Point, Reader, Selective, ItemType,
                   LazError, UnsupportedFileError, Writer)
from helpers import FIXTURES, REFERENCE_HASHES, fixture, load


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


class TestReaderLifecycle:
    """
    The three states a Reader can be in, and what each one answers.

    A Reader can be built without a file and opened later, which is what
    makes "not open yet" a state rather than an impossibility, and it can be
    closed. Both used to be reported by whatever an internal None happened to
    do next -- an AttributeError from one, a TypeError from the other -- for
    a condition Writer already answers with a ValueError.
    """

    def test_a_reader_that_was_never_opened_says_so(self):
        reader = Reader()
        for use in (lambda: reader.read(), lambda: reader.num_points,
                    lambda: len(reader), lambda: reader.scales,
                    lambda: reader.index, lambda: list(reader.points())):
            with pytest.raises(ValueError, match="not open"):
                use()

    def test_a_closed_reader_says_so(self):
        reader = Reader(fixture("pt1_v2.laz"))
        reader.close()
        for use in (lambda: reader.read(), lambda: reader.seek(0),
                    lambda: reader.index, lambda: list(reader.points())):
            with pytest.raises(ValueError, match="closed"):
                use()

    def test_a_closed_reader_still_describes_its_file(self):
        """The header was read once and the file it describes has not
        changed, so keeping it is useful rather than untidy -- and it is the
        reason closed and never-opened have to be told apart."""
        reader = Reader(fixture("pt1_v2.laz"))
        count, scales = reader.num_points, reader.scales
        reader.close()
        assert (reader.num_points, reader.scales) == (count, scales)

    def test_opening_again_does_not_strand_the_first_file(self):
        """__init__ takes the filename optionally so that open() can be
        called separately, which invites calling it twice."""
        reader = Reader(fixture("pt1_v2.laz"))
        first = reader.fp
        reader.open(fixture("pt2_v2.laz"))
        assert first.closed
        assert reader.read() is not None      # and the second one works

    def test_a_lent_file_is_still_not_ours_to_close(self):
        with open(fixture("pt1_v2.laz"), "rb") as fp:
            reader = Reader(fp)
            reader.open(fixture("pt2_v2.laz"))
            assert not fp.closed


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
            assert reader.chunking is Chunking.FIXED
            assert reader.chunk_size == 137

    def test_a_pointwise_file_reports_no_chunking(self):
        """However large a chunk size its LASzip VLR carries.

        The POINTWISE container is one stream from the first point to the
        last, so the field is left over rather than descriptive.
        """
        with Reader(fixture("pt1_v1_pointwise.laz")) as reader:
            assert reader.laz_header["chunk_size"] == 50000   # what it says
            assert reader.chunking is Chunking.NONE
            assert reader.chunk_size is None

    def test_an_uncompressed_file_reports_no_chunking(self):
        with Reader(fixture("pt1_v0.las")) as reader:
            assert reader.chunking is Chunking.NONE
            assert reader.chunk_size is None

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
# What a file keeps of its own: the bytes inside the header, and the ones
# between the last record and the first point. laszip carries both as
# user_data_in_header and user_data_after_header; a copy that dropped them
# would not be a copy.
# ---------------------------------------------------------------------------

def test_the_padding_before_the_points_is_kept():
    buf = io.BytesIO()
    with Writer(buf, 1, user_data_after_header=b"mine") as writer:
        writer.write(Point(X=1))

    with Reader(io.BytesIO(buf.getvalue())) as reader:
        assert reader.header["user_data_after_header"] == b"mine"
        assert reader.read().X == 1


@pytest.mark.parametrize("name", FIXTURES)
def test_a_file_with_no_padding_says_so(name):
    """An always-present key, so nothing has to ask whether a file has any."""
    assert load(name).header["user_data_after_header"] == b""


def test_a_clean_file_has_no_warnings():
    with Reader(fixture("pt1_v2.laz")) as reader:
        assert reader.warnings == ()
        assert reader.warning is None
