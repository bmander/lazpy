import functools
import io
import os
import struct

import pytest

from lazpy import _cpylaz as cpylaz
from lazpy import Point, Reader, LazError
from helpers import TESTDATA, fixture
from lazpy.reader import _fields_for_point_format


# ---------------------------------------------------------------------------
# Spatial indexing.
#
# A ".lax" index maps a quadtree cell to the runs of point indices that landed
# in it, so a rectangle query can decode a few chunks instead of a whole file.
# The fixtures are laszip's own work -- tools/mklax.cpp builds them with
# LASquadtree and LASindex -- and reference_inside.txt is what laszip's
# laszip_read_inside_point selects from each: the number of points and a hash
# of their indices, for a handful of rectangles.
#
# Which points a query selects is the whole of what an index decides. That
# every field of every point decodes correctly is what the whole-file hashes
# above already say, so the reference here is the indices, and the points
# themselves are checked against what a filtered full scan of the same file
# finds.
# ---------------------------------------------------------------------------

def load_reference_queries():
    path = os.path.join(TESTDATA, "reference_inside.txt")
    entries = []
    with open(path) as fh:
        for line in fh:
            name, *rect, count, digest = line.split()
            entries.append((name, tuple(float(v) for v in rect),
                            int(count), int(digest)))
    return entries


REFERENCE_QUERIES = load_reference_queries()
# the file and rectangle alone, for the tests that check against a full scan
# of the same file rather than against the reference numbers
QUERY_CASES = [(name, rect) for name, rect, _, _ in REFERENCE_QUERIES]
QUERY_IDS = [f"{name}-{'_'.join(str(v) for v in rect)}"
             for name, rect, _, _ in REFERENCE_QUERIES]
INDEXED_FIXTURES = list(dict.fromkeys(name for name, _, _, _
                                      in REFERENCE_QUERIES))
RECTANGLES = list(dict.fromkeys(rect for _, rect, _, _ in REFERENCE_QUERIES))


def query_indices(reader, rect):
    """The index of every point a rectangle query yields.

    Read off the reader rather than counted here: `index` is where the reader
    will read next, and the generator is suspended on the point it just
    handed over, so one less than it is that point's own index.
    """
    return [reader.index - 1 for _ in reader.points_within(*rect)]


def index_hash(indices):
    """What lazdump --inside hashes: the indices, little-endian, FNV-1a."""
    digest = 14695981039346656037
    for index in indices:
        for byte in struct.pack("<Q", index):
            digest = ((digest ^ byte) * 1099511628211) % 2**64
    return digest


@functools.lru_cache(maxsize=None)
def scaled_xy(name):
    """Every point's georeferenced x and y, in file order.

    Through Reader.scale, which is the coordinate points_within documents its
    rectangle in.
    """
    with Reader(fixture(name)) as reader:
        return [reader.scale(point)[:2] for point in reader]


def inside_by_scan(name, rect):
    """The indices a filtered full scan of the file would select."""
    min_x, min_y, max_x, max_y = rect
    return [i for i, (x, y) in enumerate(scaled_xy(name))
            if min_x <= x < max_x and min_y <= y < max_y]


def without_sidecar(name, tmp_path):
    """A copy of a fixture with its ".lax" left behind."""
    copy = tmp_path / os.path.basename(name)
    copy.write_bytes(open(fixture(name), "rb").read())
    return str(copy)


class TestSpatialIndexAgainstLaszip:

    def test_the_suite_has_indexed_fixtures(self):
        """Otherwise the parametrised tests below pass over an empty list."""
        assert INDEXED_FIXTURES

    @pytest.mark.parametrize("name", INDEXED_FIXTURES)
    def test_every_indexed_fixture_has_its_index(self, name):
        with Reader(fixture(name)) as reader:
            assert reader.has_spatial_index

    @pytest.mark.parametrize("name,rect,count,digest", REFERENCE_QUERIES,
                             ids=QUERY_IDS)
    def test_a_rectangle_query_selects_what_laszip_selects(
            self, name, rect, count, digest):
        with Reader(fixture(name)) as reader:
            indices = query_indices(reader, rect)
        assert len(indices) == count
        assert index_hash(indices) == digest

    @pytest.mark.parametrize("name,rect,count,digest", REFERENCE_QUERIES,
                             ids=QUERY_IDS)
    def test_an_unindexed_file_selects_the_same_points(
            self, name, rect, count, digest, tmp_path):
        """The fallback has to answer the same question, only slower."""
        if name == "pt1_v2_appended.laz":
            pytest.skip("its index is inside the file, not beside it")
        with Reader(without_sidecar(name, tmp_path)) as reader:
            assert not reader.has_spatial_index
            indices = query_indices(reader, rect)
        assert len(indices) == count
        assert index_hash(indices) == digest


class TestRectangleQueries:

    @pytest.mark.parametrize("name,rect", QUERY_CASES, ids=QUERY_IDS)
    def test_the_points_are_the_ones_a_full_scan_finds(self, name, rect):
        """Same points, not merely the same number of them: a seek that
        landed one chunk over would still count right."""
        expected = inside_by_scan(name, rect)
        xy = scaled_xy(name)
        with Reader(fixture(name)) as reader:
            found = [(reader.index - 1, reader.scale(point)[:2])
                     for point in reader.points_within(*rect)]
        assert [i for i, _ in found] == expected
        assert [p for _, p in found] == [xy[i] for i in expected]

    def test_the_rectangle_is_half_open(self):
        """A point on the lower edge is in and one on the upper edge is out,
        so that adjoining rectangles partition the points."""
        name = "pt1_v2.laz"
        x, y = scaled_xy(name)[0]
        with Reader(fixture(name)) as reader:
            on_lower = query_indices(reader, (x, y, x + 1, y + 1))
            on_upper = query_indices(reader, (x - 1, y - 1, x, y))
        assert 0 in on_lower
        assert 0 not in on_upper

    def test_an_inside_out_rectangle_is_refused(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            with pytest.raises(ValueError, match="inside out"):
                next(reader.points_within(1500, 1700, 1499, 1701))

    def test_a_rectangle_beyond_the_file_finds_nothing(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            assert list(reader.points_within(1e6, 1e6, 2e6, 2e6)) == []

    def test_it_yields_the_reader_s_own_buffer(self):
        """As points() does; copy() is what keeps one."""
        with Reader(fixture("pt1_v2.laz")) as reader:
            points = list(reader.points_within(1498, 1699, 1500, 1701))
            assert len({id(point) for point in points}) == 1

    def test_a_query_can_follow_a_query(self):
        """Each one seeks to where its intervals begin, so the reader being
        left somewhere else must not matter."""
        with Reader(fixture("pt1_v2.laz")) as reader:
            first = query_indices(reader, (1498, 1699, 1500, 1701))
            again = query_indices(reader, (1498, 1699, 1500, 1701))
            other = query_indices(reader, (1500, 1698, 1501, 1699))
        assert first == again
        assert other == inside_by_scan("pt1_v2.laz", (1500, 1698, 1501, 1699))

    @pytest.mark.parametrize("rect", [
        (1600, 1800, 1601, 1801),                  # nowhere near the points
        (1500, 1698, 1501, 1699),
        (1500.5, 1700.5, 1501.5, 1701.5),
        (1496.62, 1697.5, 1498, 1699),
    ])
    def test_the_index_decodes_a_fraction_of_the_file(self, rect):
        """The point of an index: a rectangle only part of the file reaches
        must not cost a scan of all of it.

        Measured as points decoded rather than as elapsed time, which on a
        five-hundred-point fixture would say nothing. The saving is the same
        ratio at any size, and it is the ratio that is the feature.
        """
        with Reader(fixture("pt1_v2.laz")) as reader:
            spans = reader.spatial_index.intervals(*rect)
            decoded = sum(end - start + 1 for start, end in spans)
            assert decoded <= reader.num_points // 2


class TestFindingTheIndex:

    def test_an_index_beside_the_file_is_found_by_name(self):
        with Reader(fixture("pt1_v2.laz")) as reader:
            assert reader.has_spatial_index
            assert reader.spatial_index.bounds == (1495.0, 1695.0,
                                                   1503.0, 1703.0)

    def test_an_index_inside_the_file_is_found(self):
        """pt1_v2_appended.laz carries its index as an extended record the LAS
        header does not count; only the LASzip record points at it."""
        name = "pt1_v2_appended.laz"
        assert not os.path.exists(fixture(name).replace(".laz", ".lax"))
        with Reader(fixture(name)) as reader:
            assert reader.has_spatial_index
            assert reader.spatial_index.num_cells == 22

    def test_appending_an_index_leaves_the_points_alone(self):
        """The same file, plus a record behind the points."""
        with Reader(fixture("pt1_v2.laz")) as plain:
            with Reader(fixture("pt1_v2_appended.laz")) as appended:
                assert appended.checksum() == plain.checksum()

    def test_a_file_without_an_index_says_so(self, tmp_path):
        with Reader(without_sidecar("pt1_v2.laz", tmp_path)) as reader:
            assert not reader.has_spatial_index
            assert reader.spatial_index is None

    def test_a_file_object_has_no_index_to_find(self):
        """An index is found by the name of the file it indexes, and a file
        object has none. The query still works, by scanning."""
        with open(fixture("pt1_v2.laz"), "rb") as fp:
            with Reader(io.BytesIO(fp.read())) as reader:
                assert not reader.has_spatial_index
                assert query_indices(reader, (1498, 1699, 1500, 1701)) == \
                    inside_by_scan("pt1_v2.laz", (1498, 1699, 1500, 1701))

    def test_the_index_is_only_looked_for_once(self):
        """Asked for twice, the same object comes back -- a query does not
        re-read the sidecar."""
        with Reader(fixture("pt1_v2.laz")) as reader:
            assert reader.spatial_index is reader.spatial_index

    def test_a_closed_reader_has_no_index(self):
        """Finding one means reading the file, and there is no file left."""
        reader = Reader(fixture("pt1_v2.laz"))
        reader.close()
        assert reader.spatial_index is None

    def test_reopening_looks_again(self):
        reader = Reader(fixture("pt1_v2.laz"))
        reader.close()
        with reader.open(fixture("pt1_v2.laz")) as reopened:
            assert reopened.has_spatial_index

    def test_an_index_the_file_promises_but_cuts_short_is_not_passed_over(
            self, tmp_path):
        """Unlike a record the header merely counted, this one was pointed
        at, so a record that is not all there is worth saying so about."""
        copy = tmp_path / "pt1_v2_appended.laz"
        data = open(fixture("pt1_v2_appended.laz"), "rb").read()
        copy.write_bytes(data[:-16])
        with Reader(str(copy)) as reader:
            with pytest.raises(LazError, match="runs past the end"):
                reader.spatial_index

    def test_an_unreadable_index_is_not_passed_over(self, tmp_path):
        """Falling back to a full scan would answer the same question far
        more slowly and say nothing about why."""
        copy = without_sidecar("pt1_v2.laz", tmp_path)
        (tmp_path / "pt1_v2.lax").write_bytes(b"not an index at all")
        with Reader(copy) as reader:
            with pytest.raises(LazError, match="LASX"):
                reader.spatial_index


class TestSpatialIndexParsing:

    @staticmethod
    def index_of(name):
        with Reader(fixture(name)) as reader:
            return reader.spatial_index

    def test_it_describes_its_quadtree(self):
        index = self.index_of("pt1_v2.laz")
        assert index.levels == 3
        assert index.num_cells == 22
        assert index.warning is None

    def test_the_intervals_are_ascending_and_disjoint(self):
        index = self.index_of("pt1_v2.laz")
        for rect in RECTANGLES:
            spans = index.intervals(*rect)
            assert all(start <= end for start, end in spans)
            assert all(spans[i][1] < spans[i + 1][0]
                       for i in range(len(spans) - 1))

    def test_a_rectangle_over_everything_covers_every_point(self):
        index = self.index_of("pt1_v2.laz")
        spans = index.intervals(*index.bounds[:2], *index.bounds[2:])
        covered = {i for start, end in spans for i in range(start, end + 1)}
        assert covered == set(range(500))

    def test_a_rectangle_that_misses_has_no_intervals(self):
        assert self.index_of("pt1_v2.laz").intervals(1e6, 1e6, 2e6, 2e6) == []

    @pytest.mark.parametrize("data", [
        b"", b"LASX", b"NOPE" + bytes(64),
        b"LASX" + bytes(4) + b"NOPE" + bytes(64),
    ])
    def test_a_malformed_index_raises(self, data):
        with pytest.raises(LazError):
            cpylaz.SpatialIndex(data)

    def test_an_index_cut_short_raises(self):
        with open(fixture("pt1_v2.lax"), "rb") as fp:
            data = fp.read()
        with pytest.raises(LazError, match="ends"):
            cpylaz.SpatialIndex(data[:len(data) // 2])


class TestSpatialIndexFormats:
    """The parts of the format the fixtures cannot reach.

    A file needs more than four billion points before an index states its
    intervals in 64 bits, and needs to be malformed before a cell index
    appears twice, so both are built here by hand rather than by laszip.
    """

    @staticmethod
    def lax(cells, wide=False, levels=1, bounds=(0.0, 2.0, 0.0, 2.0)):
        """A ".lax" payload holding `cells`, as {cell_index: [(start, end)]}.

        `bounds` is (min_x, max_x, min_y, max_y), the order the file states
        them in rather than the order a query takes them.
        """
        out = bytearray(b"LASX" + struct.pack("<I", 0))
        out += b"LASS" + struct.pack("<I", 0)
        # version, levels, then the level index and implicit level count a
        # tiling would use and reading discards
        out += b"LASQ" + struct.pack("<IIII", 0, levels, 0, 0)
        out += struct.pack("<ffff", *bounds)

        out += b"LASV" + struct.pack("<II", 1 if wide else 0, len(cells))
        width = "<Q" if wide else "<I"
        for cell_index, intervals in cells:
            # what the cell says it holds; clamped so a deliberately backwards
            # interval can still be written out
            full = sum(max(0, end - start + 1) for start, end in intervals)
            out += struct.pack("<iI", cell_index, len(intervals))
            out += struct.pack(width, full)
            for start, end in intervals:
                out += struct.pack(width, start) + struct.pack(width, end)
        return bytes(out)

    def test_it_reads_a_hand_built_index(self):
        index = cpylaz.SpatialIndex(self.lax([(1, [(0, 10), (2000, 2010)])]))
        assert index.levels == 1
        assert index.num_cells == 1
        assert index.bounds == (0.0, 0.0, 2.0, 2.0)
        assert index.intervals(0.1, 0.1, 0.2, 0.2) == [(0, 10), (2000, 2010)]
        # the other three quadrants hold nothing
        assert index.intervals(1.1, 1.1, 1.2, 1.2) == []

    def test_intervals_past_four_billion_points(self):
        far = 2**32 + 5
        index = cpylaz.SpatialIndex(
            self.lax([(1, [(far, far + 100)])], wide=True))
        assert index.intervals(0.1, 0.1, 0.2, 0.2) == [(far, far + 100)]

    def test_the_two_interval_widths_agree(self):
        cells = [(1, [(0, 10)]), (2, [(3000, 3010)])]
        narrow = cpylaz.SpatialIndex(self.lax(cells))
        wide = cpylaz.SpatialIndex(self.lax(cells, wide=True))
        rect = (0.1, 0.1, 1.9, 0.2)
        assert narrow.intervals(*rect) == wide.intervals(*rect)

    def test_two_cells_merge_into_one_ascending_list(self):
        """Cells interleave -- each one's intervals run the length of the
        file -- so a query over several has to put them back in order."""
        index = cpylaz.SpatialIndex(self.lax([(1, [(0, 10), (5000, 5010)]),
                                              (2, [(20, 30)])]))
        assert index.intervals(0.1, 0.1, 1.9, 0.2) == [(0, 30), (5000, 5010)]

    def test_a_repeated_cell_keeps_its_first_entry(self):
        index = cpylaz.SpatialIndex(self.lax([(1, [(0, 10)]),
                                              (1, [(5000, 5010)])]))
        assert "more than once" in index.warning
        assert index.intervals(0.1, 0.1, 0.2, 0.2) == [(0, 10)]

    def test_an_interval_that_ends_before_it_starts_is_refused(self):
        with pytest.raises(LazError, match="before it starts"):
            cpylaz.SpatialIndex(self.lax([(1, [(10, 0)])]))

    def test_a_cell_outside_the_tree_is_refused(self):
        with pytest.raises(LazError, match="out of range"):
            cpylaz.SpatialIndex(self.lax([(99, [(0, 10)])]))

    def test_too_many_levels_are_refused(self):
        with pytest.raises(LazError, match="max 15"):
            cpylaz.SpatialIndex(self.lax([(1, [(0, 10)])], levels=16))

    def test_an_index_naming_points_the_file_lacks_is_survivable(self,
                                                                 tmp_path):
        """An index is data out of a file like any other. One that names
        points past the end of this one must not send the reader off after
        them."""
        copy = without_sidecar("pt1_v2.laz", tmp_path)
        (tmp_path / "pt1_v2.lax").write_bytes(
            self.lax([(1, [(0, 10), (10**9, 10**9 + 5)])],
                     bounds=(1400.0, 1600.0, 1600.0, 1800.0)))
        with Reader(copy) as reader:
            indices = query_indices(reader, (1400, 1600, 1500, 1700))
            assert indices == [i for i in inside_by_scan(
                "pt1_v2.laz", (1400, 1600, 1500, 1700)) if i <= 10]


class TestRectangleQueriesAsArrays:
    """arrays_within and xyz_within select what points_within selects.

    Which points a rectangle contains is settled against laszip's own answer
    elsewhere in this file; what these have to show is that the array form
    and the point form agree, for every reference rectangle and every
    container shape. Anything else would mean two answers to one question.
    """

    @pytest.mark.parametrize("name,rect", QUERY_CASES, ids=QUERY_IDS)
    def test_arrays_within_matches_points_within(self, name, rect):
        np = pytest.importorskip("numpy")
        with Reader(fixture(name)) as reader:
            expected = [(p.X, p.Y, p.Z, p.classification)
                        for p in map(Point.copy, reader.points_within(*rect))]
        with Reader(fixture(name)) as reader:
            a = reader.arrays_within("X", "Y", "Z", "classification",
                                     rect=rect)

        assert len(a["X"]) == len(expected)
        for i, name_ in enumerate(("X", "Y", "Z", "classification")):
            assert np.array_equal(a[name_], [row[i] for row in expected])

    def test_arrays_within_defaults_to_every_field(self):
        np = pytest.importorskip("numpy")
        rect = (1498, 1699, 1500, 1701)
        with Reader(fixture("pt1_v2.laz")) as reader:
            a = reader.arrays_within(rect=rect)
            expected = _fields_for_point_format(
                reader.point_format, reader.num_extra_bytes)
        assert sorted(a) == sorted(expected)
        # the sub-byte fields are unpacked from the byte they share, and
        # trimmed to the same length as the rest
        assert len(a["return_number"]) == len(a["X"])
        assert np.all(a["return_number"] <= 7)

    def test_xyz_within_is_the_scaled_form(self):
        np = pytest.importorskip("numpy")
        rect = (1498, 1699, 1500, 1701)
        with Reader(fixture("pt1_v2.laz")) as reader:
            xyz = reader.xyz_within(rect)
        with Reader(fixture("pt1_v2.laz")) as reader:
            expected = [reader.scale(p) for p in reader.points_within(*rect)]

        assert xyz.shape == (len(expected), 3)
        assert np.array_equal(xyz, np.array(expected, dtype=np.float64))
        # and every one of them really is inside
        assert np.all((xyz[:, 0] >= rect[0]) & (xyz[:, 0] < rect[2]))
        assert np.all((xyz[:, 1] >= rect[1]) & (xyz[:, 1] < rect[3]))

    def test_a_rectangle_that_finds_nothing_gives_empty_arrays(self):
        pytest.importorskip("numpy")
        with Reader(fixture("pt1_v2.laz")) as reader:
            a = reader.arrays_within("X", "Y", rect=(1e6, 1e6, 2e6, 2e6))
        assert len(a["X"]) == 0 and len(a["Y"]) == 0

    def test_an_unindexed_file_selects_the_same_points(self, tmp_path):
        """The filtered full scan has to answer the same question."""
        np = pytest.importorskip("numpy")
        rect = (1498, 1699, 1500, 1701)
        with Reader(fixture("pt1_v2.laz")) as reader:
            indexed = reader.arrays_within("X", "Y", rect=rect)
        with Reader(without_sidecar("pt1_v2.laz", tmp_path)) as reader:
            assert not reader.has_spatial_index
            scanned = reader.arrays_within("X", "Y", rect=rect)
        assert np.array_equal(indexed["X"], scanned["X"])
        assert np.array_equal(indexed["Y"], scanned["Y"])
