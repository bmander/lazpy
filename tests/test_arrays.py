
import pytest

from lazpy import Point, Reader, LazError, Writer
from helpers import FIXTURES, fixture


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
    classification flags are shifts and masks over a byte on both paths --
    _ARRAY_FIELDS on one, LAZ_POINT_PACKED_FIELDS in src/laz_types.h on the
    other -- and the two statements of those shifts have to agree.
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
            with pytest.raises(ValueError, match="shorter than the point"):
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
