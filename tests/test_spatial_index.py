import functools
import io
import os
import struct

import pytest

from lazpy import _cpylaz as cpylaz
from lazpy import (Point, Reader, LazError, Writer,
                   append_spatial_index)
from helpers import REFERENCE_HASH, TESTDATA, fixture
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
    with open(fixture(name), "rb") as fh:
        copy.write_bytes(fh.read())
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
        with open(fixture("pt1_v2_appended.laz"), "rb") as fh:
            data = fh.read()
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


# ---------------------------------------------------------------------------
# Circle queries.
#
# The shape anyone selecting around a point actually wants, and one the
# quadtree can answer more cheaply than the square around it: the corners of
# that square are cells a circle never reaches. LASquadtree has it and the
# port left it out; laszip's own DLL exposes only the rectangle.
# ---------------------------------------------------------------------------

def middle_of(reader, fraction=6):
    """A circle over the middle of an indexed file, as (x, y, radius)."""
    min_x, min_y, max_x, max_y = reader.spatial_index.bounds
    radius = (max_x - min_x) / fraction
    center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
    return center + (radius,)


def inside_circle_by_scan(name, circle):
    """Which points a circle really holds, from the cached full scan the
    rectangle tests use -- the same oracle, a different shape."""
    center_x, center_y, radius = circle
    return [index for index, (x, y) in enumerate(scaled_xy(name))
            if (x - center_x) ** 2 + (y - center_y) ** 2 < radius ** 2]


def circle_indices(reader, circle):
    """The index of every point a circle query yields, as query_indices does
    for a rectangle."""
    return [reader.index - 1 for _ in reader.points_within_circle(*circle)]


def points_covered(intervals):
    """How many points a set of intervals reaches."""
    return sum(end - start + 1 for start, end in intervals)


@pytest.mark.parametrize("name", INDEXED_FIXTURES)
def test_a_circle_selects_exactly_the_points_inside_it(name):
    with Reader(fixture(name)) as reader:
        circle = middle_of(reader)

        indices = circle_indices(reader, circle)

    assert indices == inside_circle_by_scan(name, circle)
    assert indices          # the fixtures are dense enough for this to bite


@pytest.mark.parametrize("name", INDEXED_FIXTURES)
def test_a_circle_reads_no_more_than_the_square_around_it(name):
    """The point of asking the index for a circle rather than filtering a
    rectangle: the corners of the square are cells it can skip."""
    with Reader(fixture(name)) as reader:
        center_x, center_y, radius = middle_of(reader)
        index = reader.spatial_index

        circle = index.intervals_within_circle(center_x, center_y, radius)
        square = index.intervals(center_x - radius, center_y - radius,
                                 center_x + radius, center_y + radius)

        assert points_covered(circle) <= points_covered(square)


def test_a_circle_of_no_size_holds_nothing():
    with Reader(fixture(INDEXED_FIXTURES[0])) as reader:
        center_x, center_y, _ = middle_of(reader)
        assert reader.spatial_index.intervals_within_circle(
            center_x, center_y, 0.0) == []
        assert list(reader.points_within_circle(center_x, center_y, 0.0)) == []


def test_a_negative_radius_is_refused():
    with Reader(fixture(INDEXED_FIXTURES[0])) as reader:
        with pytest.raises(ValueError, match="radius cannot be negative"):
            list(reader.points_within_circle(0.0, 0.0, -1.0))


def test_an_unindexed_file_selects_the_same_circle(tmp_path):
    """Without an index it is a filtered full scan, and the answer is the
    same one."""
    name = INDEXED_FIXTURES[0]
    with Reader(fixture(name)) as reader:
        circle = middle_of(reader)
        wanted = circle_indices(reader, circle)

    with Reader(without_sidecar(name, tmp_path)) as bare:
        assert not bare.has_spatial_index
        assert circle_indices(bare, circle) == wanted


def test_the_array_forms_select_what_the_points_do():
    np = pytest.importorskip("numpy")
    with Reader(fixture(INDEXED_FIXTURES[0])) as reader:
        circle = middle_of(reader)
        wanted = [(p.X, p.Y) for p in reader.points_within_circle(*circle)]

        columns = reader.arrays_within("X", "Y", circle=circle)
        xyz = reader.xyz_within(circle=circle)

    assert list(zip(columns["X"].tolist(), columns["Y"].tolist())) == wanted
    assert len(xyz) == len(wanted)
    assert np.allclose(xyz[:, 0], columns["X"] * reader.scales[0]
                       + reader.offsets[0])


def test_a_query_is_over_one_shape_or_the_other():
    with Reader(fixture(INDEXED_FIXTURES[0])) as reader:
        with pytest.raises(TypeError, match="rectangle or a circle"):
            reader.arrays_within("X", rect=(0, 0, 1, 1), circle=(0, 0, 1))
        with pytest.raises(TypeError, match="rectangle or a circle"):
            reader.arrays_within("X")


# ---------------------------------------------------------------------------
# Building one.
#
# The other half of the index: lasindex's job, and what testdata/'s .lax files
# were made with -- tools/README.md records the parameters as
# `mklax <file> 1.0 30 -20`, so the same parameters over the same points
# should give the same index.
#
# "The same" up to the order the cells appear in the file, which is the one
# thing a reader cannot see: LASzip writes them in the order its hash of them
# happens to iterate, and this writes them in order of cell index. Everything
# either one answers is compared instead.
# ---------------------------------------------------------------------------

FIXTURE_INDEX = dict(cell_size=1.0, minimum_points=30, maximum_intervals=-20)

# LASX, the quadtree record and its bounding box: everything in front of the
# cells, which is where an order can differ
QUADTREE_BYTES = 56


def built_index(name):
    with Reader(fixture(name)) as reader:
        return reader.build_spatial_index(**FIXTURE_INDEX)


# every indexed fixture but the one whose index is inside it
SIDECAR_FIXTURES = [name for name in INDEXED_FIXTURES
                    if name != "pt1_v2_appended.laz"]


def committed_index(name):
    """The .lax lasindex built beside this fixture."""
    with open(fixture(name.rsplit(".", 1)[0] + ".lax"), "rb") as fh:
        return fh.read()


@pytest.mark.parametrize("name", SIDECAR_FIXTURES)
def test_the_tree_is_the_one_laszip_built(name):
    """Byte for byte, as far as the cells: the same levels over the same
    square, which is what the cell size and the extent of the points decide.
    """
    committed = committed_index(name)

    assert built_index(name)[:QUADTREE_BYTES] == committed[:QUADTREE_BYTES]


@pytest.mark.parametrize("name", SIDECAR_FIXTURES)
def test_a_built_index_answers_what_laszips_own_answers(name):
    """Every cell and every run of points, by what the two select."""
    committed = cpylaz.SpatialIndex(committed_index(name))

    ours = cpylaz.SpatialIndex(built_index(name))

    assert (ours.bounds, ours.levels, ours.num_cells) == \
        (committed.bounds, committed.levels, committed.num_cells)
    for rect in RECTANGLES:
        assert ours.intervals(*rect) == committed.intervals(*rect)
    min_x, min_y, max_x, max_y = ours.bounds
    for radius in (0.5, 2.0, 20.0):
        centre = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        assert ours.intervals_within_circle(*centre, radius) == \
            committed.intervals_within_circle(*centre, radius)


@pytest.mark.parametrize("name", INDEXED_FIXTURES)
def test_an_index_built_here_selects_what_a_scan_selects(name, tmp_path):
    """The index is only worth having if the answer is the same one."""
    copy = without_sidecar(name, tmp_path)

    with Reader(copy) as reader:
        written = reader.write_spatial_index(**FIXTURE_INDEX)
    assert written.endswith(".lax")

    with Reader(copy) as reader:
        assert reader.has_spatial_index
        for rect in RECTANGLES:
            reader.seek(0)
            assert query_indices(reader, rect) == inside_by_scan(name, rect)


def test_an_index_can_go_inside_the_file_it_indexes(tmp_path):
    """Where `lasindex -append` puts one, found through the LASzip record."""
    name = "pt1_v2.laz"
    copy = without_sidecar(name, tmp_path)

    append_spatial_index(copy, built_index(name))

    with Reader(copy) as reader:
        assert reader.has_spatial_index
        assert reader.spatial_index.num_cells == 22
        # the points are where they were: the record went behind them
        assert reader.checksum() == REFERENCE_HASH[name]
    with Reader(copy) as reader:
        rect = RECTANGLES[0]
        assert query_indices(reader, rect) == inside_by_scan(name, rect)


def test_a_plain_las_has_nowhere_to_keep_one_inside(tmp_path):
    copy = without_sidecar("pt0_v0.las", tmp_path)

    with pytest.raises(LazError, match="only a compressed file"):
        append_spatial_index(copy, built_index("pt0_v0.las"))


def test_a_coarser_tree_holds_fewer_cells():
    """The cell size is what decides how much the index knows."""
    fine = cpylaz.SpatialIndex(built_index("pt1_v2.laz"))

    with Reader(fixture("pt1_v2.laz")) as reader:
        coarse = cpylaz.SpatialIndex(reader.build_spatial_index(
            cell_size=4.0, minimum_points=30, maximum_intervals=-20))

    assert coarse.levels < fine.levels
    assert coarse.num_cells < fine.num_cells


def test_a_reader_with_no_name_has_nowhere_to_put_a_sidecar():
    with open(fixture("pt1_v2.laz"), "rb") as fp:
        with Reader(io.BytesIO(fp.read())) as reader:
            with pytest.raises(ValueError, match="no name"):
                reader.write_spatial_index()


def test_an_index_of_no_points_is_refused():
    buf = io.BytesIO()
    with Writer(buf, 1):
        pass

    with Reader(io.BytesIO(buf.getvalue())) as reader:
        with pytest.raises(LazError, match="nothing to index"):
            reader.build_spatial_index()
