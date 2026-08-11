import collections
import io
import struct

import pytest

from lazpy import _cpylaz as cpylaz
from lazpy import (Chunking, Compressor, EXTRA_BYTES_VLR_KEY,
                   ExtraBytesAttribute, LASZIP_VLR_KEY, Point, Reader,
                   LazError, UnsupportedFileError, Writer, auto_offsets,
                   extra_bytes_record)
from lazpy.compat import _extra_bytes_attributes
from lazpy.headers import VLR_HEADER_SIZE, _header_size
from helpers import (EXTENDED_POINT_COUNT_OFFSET, LAS14_FORMATS,
                     LEGACY_FORMATS, LEGACY_POINT_COUNT_OFFSET, REFERENCE_HASH,
                     a_record, assert_is_the_file_laszip_wrote, fixture,
                     las_records, load, point_block)


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
            assert reader.chunking is Chunking.ADAPTIVE
            assert reader.chunk_size is None
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

    assert_is_the_file_laszip_wrote(data, f.data, header["version_minor"])


# ---------------------------------------------------------------------------
# Variable length records.
#
# Everything a file says about itself beyond the fixed header is a variable
# length record: where on the earth its coordinates are, what its extra bytes
# mean, whatever the file it was copied from carried. The writer takes them
# alongside the LASzip one it builds itself, and they go in front of it, which
# is where laszip puts its own -- it appends to the records it was given.
# ---------------------------------------------------------------------------


WKT = a_record(b"LASF_Projection", 2112, b"COMPD_CS[" + b"x" * 300 + b"]",
               b"OGC coordinate system WKT")
CLASSES = a_record(b"LASF_Spec", 0, b"ground\0" * 4, b"classification")


def one_point_file(**kwargs):
    """A file holding a single point, as bytes."""
    buf = io.BytesIO()
    with Writer(buf, 1, **kwargs) as writer:
        writer.write(Point(X=1))
    return buf.getvalue()


def records_of(data):
    with Reader(io.BytesIO(data)) as reader:
        return reader.header["variable_length_records"]


def test_records_are_written_and_read_back():
    """What went in comes out, payload and description alike."""
    records = records_of(one_point_file(vlrs=[WKT, CLASSES]))

    assert list(records) == [(b"LASF_Projection", 2112), (b"LASF_Spec", 0),
                             LASZIP_VLR_KEY]
    for given in (WKT, CLASSES):
        got = records[(given["user_id"], given["record_id"])]
        assert got["data"] == given["data"]
        assert got["description"] == given["description"]
        assert got["record_length_after_header"] == len(given["data"])


def test_a_mapping_of_records_is_taken_as_readily_as_a_list():
    """Copying a file's records is handing over what the reader gave."""
    mapping = records_of(one_point_file(vlrs=[WKT, CLASSES]))

    assert records_of(one_point_file(vlrs=mapping)) == mapping


def test_the_laszip_record_of_a_source_file_is_dropped():
    """It describes how the file it came from was compressed, which is not a
    question the new file answers the same way."""
    source = records_of(one_point_file(vlrs=[WKT]))

    data = one_point_file(vlrs=source, compressed=False)

    assert list(records_of(data)) == [(b"LASF_Projection", 2112)]


def test_the_header_counts_the_records_and_the_points_follow_them():
    """The one thing a record can break: where the points begin."""
    points, layout = source_points("pt1_v0.las")
    buf = io.BytesIO()
    with Writer(buf, 1, vlrs=[WKT, CLASSES], **layout) as writer:
        for point in points:
            writer.write(point)

    with Reader(io.BytesIO(buf.getvalue())) as reader:
        header = reader.header
        records = header["variable_length_records"]
        assert header["number_of_variable_length_records"] == 3
        payloads = sum(r["record_length_after_header"]
                       for r in records.values())
        assert (header["offset_to_point_data"] ==
                header["header_size"] + VLR_HEADER_SIZE * 3 + payloads)
        assert reader.checksum() == REFERENCE_HASH["pt1_v0.las"]


def test_a_files_records_survive_a_copy():
    """A compatibility fixture is the only one in testdata/ carrying a record
    that is not the LASzip one: an "extra bytes" descriptor for what is left
    of its extra bytes once the hidden LAS 1.4 fields are taken out. Copying
    the file is handing its points and its records to a writer.

    The points are checked by coordinate rather than by laszip's checksum,
    because a native LAS 1.4 file cannot reproduce one thing this fixture
    holds: its legacy return numbers are the ones laszip's own down-conversion
    computed, and a point format 6-10 record has nowhere to keep a legacy
    field that disagrees with the extended one it is derived from.
    """
    name = "pt8_compat_v0.las"
    with Reader(fixture(name)) as reader:
        points = [point.copy() for point in reader]
        source = dict(reader.header["variable_length_records"])
        point_format = reader.point_format
        layout = dict(scales=reader.scales, offsets=reader.offsets)

    buf = io.BytesIO()
    with Writer(buf, point_format, vlrs=source, **layout) as writer:
        for point in points:
            writer.write(point)

    with Reader(io.BytesIO(buf.getvalue())) as reader:
        copied = reader.header["variable_length_records"]
        assert copied.keys() == source.keys() | {LASZIP_VLR_KEY}
        for key, record in source.items():
            assert copied[key]["data"] == record["data"]
            assert copied[key]["description"] == record["description"]
        # taken from the descriptor that came along, not restated here
        assert reader.num_extra_bytes == 6
        assert [(p.X, p.Y, p.Z, bytes(p.extra_bytes)) for p in reader] == \
            [(p.X, p.Y, p.Z, bytes(p.extra_bytes)) for p in points]


def test_a_record_no_reader_could_find_is_refused():
    with pytest.raises(ValueError, match="two records claim"):
        one_point_file(vlrs=[WKT, dict(WKT, data=b"else")])


def test_a_payload_too_long_to_declare_is_refused():
    with pytest.raises(ValueError, match="over the 65535"):
        one_point_file(vlrs=[a_record(b"lazpy", 1, b"x" * 65536)])


# ---------------------------------------------------------------------------
# The "extra bytes" record, which is the one record the writer can build: it
# describes the opaque bytes on the end of every point, and a file whose
# descriptor and record length disagree is one nothing can read.
# ---------------------------------------------------------------------------

# Six one-byte attributes, which is what the pt0_v0.las points carry
SIX_BYTES = [ExtraBytesAttribute(f"byte {i}".encode(), 1,
                                 scale=0.5, offset=2.0)
             for i in range(6)]


def test_the_extra_bytes_record_sizes_the_points():
    """A descriptor is enough on its own: num_extra_bytes comes from it."""
    points, layout = source_points("pt0_v0.las")
    layout.pop("num_extra_bytes")
    buf = io.BytesIO()
    with Writer(buf, 0, vlrs=[extra_bytes_record(SIX_BYTES)],
                **layout) as writer:
        for point in points:
            writer.write(point)

    with Reader(io.BytesIO(buf.getvalue())) as reader:
        assert reader.num_extra_bytes == 6
        assert reader.header["point_data_record_length"] == 20 + 6
        assert reader.checksum() == REFERENCE_HASH["pt0_v0.las"]


def test_the_attributes_describe_what_they_were_given():
    data = one_point_file(vlrs=[extra_bytes_record(SIX_BYTES)])

    record = records_of(data)[EXTRA_BYTES_VLR_KEY]
    attributes = list(_extra_bytes_attributes(record["data"]))
    assert [a.name for a in attributes] == [a.name for a in SIX_BYTES]
    assert [a.start for a in attributes] == list(range(6))
    # scale and offset, where the descriptor keeps them -- behind the name,
    # four unused bytes and the three no-data/min/max triples -- and the
    # option bits that say they were set at all. One value per dimension, of
    # which a scalar attribute has one; the other two are what laszip leaves
    # there, which means no scaling.
    first = record["data"]
    assert first[3] == 0x08 | 0x10
    assert struct.unpack_from("<3d", first, 4 + 32 + 4 + 3 * 24) == (0.5, 1, 1)
    assert struct.unpack_from("<3d", first, 4 + 32 + 4 + 4 * 24) == (2.0, 0, 0)


def test_a_descriptor_that_contradicts_the_record_length_is_refused():
    with pytest.raises(ValueError, match="describes 6 bytes per point"):
        one_point_file(vlrs=[extra_bytes_record(SIX_BYTES)],
                       num_extra_bytes=4)


def test_undocumented_bytes_are_not_built_here():
    """Data type 0 keeps its width where scale and offset would go."""
    with pytest.raises(ValueError, match="option byte"):
        extra_bytes_record([ExtraBytesAttribute(b"opaque", 0)])


def test_an_unknown_data_type_is_refused():
    with pytest.raises(Exception, match="data type"):
        extra_bytes_record([ExtraBytesAttribute(b"what", 99)])


# ---------------------------------------------------------------------------
# Coordinates, versions and the space a header keeps for its producer.
#
# The small things laszip's writing API has that this one did not: a way in
# from georeferenced coordinates, offsets that make room for them, the two
# LAS versions below 1.2, and the bytes a header may carry of its own.
# ---------------------------------------------------------------------------

def a_writer(point_format=1, **kwargs):
    return Writer(io.BytesIO(), point_format, **kwargs)


def at(writer, x, y, z):
    """A point standing at a georeferenced coordinate."""
    X, Y, Z = writer.unscale(x, y, z)
    return Point(X=X, Y=Y, Z=Z)


# A survey where the default offsets do not reach: a UTM northing stored to
# the millimetre is six times what a point's integer holds from zero.
MILLIMETRES = (0.001, 0.001, 0.001)
SURVEY_MIN = (515000.0, 6748000.0, 0.0)
SURVEY_MAX = (516000.0, 6749000.0, 400.0)
# scales and offsets that do reach it, for the tests about rounding rather
# than about range
NEAR_SURVEY = (500000.0, 6700000.0, 0.0)
CENTIMETRES = (0.01, 0.01, 0.001)


def round_trip(coordinates, scales, offsets):
    """What comes back out when these coordinates go in at these scales."""
    buf = io.BytesIO()
    with Writer(buf, 1, scales=scales, offsets=offsets) as writer:
        for xyz in coordinates:
            writer.write(at(writer, *xyz))
    with Reader(io.BytesIO(buf.getvalue())) as reader:
        return [reader.scale(point) for point in reader]


class TestUnscale:
    """The inverse of Reader.scale, which is laszip_set_coordinates."""

    def test_it_is_the_inverse_of_scale(self):
        wanted = [(515000.0, 6748000.0, 33.5), (515000.005, 6747999.995, 0.0),
                  (499999.99, 6699999.99, -12.125)]

        got = round_trip(wanted, CENTIMETRES, NEAR_SURVEY)

        # back within a scale unit, which is as near as a coordinate stored as
        # a multiple of one can be
        for point, want in zip(got, wanted):
            assert point == pytest.approx(want, abs=max(CENTIMETRES))

    def test_a_half_rounds_away_from_zero_as_laszip_rounds_it(self):
        """Python's own round() would send it to the nearer even number, and
        a grid of points lands on halves constantly."""
        with a_writer(scales=(1.0, 1.0, 1.0)) as writer:
            assert writer.unscale(0.5, 1.5, 2.5) == (1, 2, 3)
            assert writer.unscale(-0.5, -1.5, -2.5) == (-1, -2, -3)

    def test_a_coordinate_that_does_not_fit_is_refused(self):
        """Not wrapped into a point somewhere else entirely, which is what
        laszip_check_for_integer_overflow exists to catch."""
        with a_writer(scales=CENTIMETRES, offsets=NEAR_SURVEY) as writer:
            with pytest.raises(ValueError, match="no point can hold"):
                writer.unscale(1e9, 0.0, 0.0)


class TestAutoOffsets:

    def test_offsets_bring_a_survey_within_reach(self):
        offsets = auto_offsets(SURVEY_MIN, SURVEY_MAX, MILLIMETRES)

        got = round_trip([SURVEY_MIN, SURVEY_MAX], MILLIMETRES, offsets)

        assert got == [SURVEY_MIN, SURVEY_MAX]

    def test_the_default_offsets_would_not_have(self):
        """Which is the reason to have this: from an offset of zero, a point
        stored to the millimetre reaches about two million metres, and a
        northing is three times that."""
        with a_writer(scales=MILLIMETRES) as writer:
            with pytest.raises(ValueError, match="no point can hold"):
                writer.unscale(*SURVEY_MIN)

    def test_they_are_round_numbers(self):
        """laszip rounds each down to a multiple of ten million scale units,
        so that neighbouring files are likely to share them."""
        assert auto_offsets(SURVEY_MIN, SURVEY_MAX) == (500000.0, 6700000.0,
                                                        0.0)

    def test_a_scale_that_could_not_work_is_refused(self):
        with pytest.raises(ValueError, match="must be positive"):
            auto_offsets((0.0,) * 3, (1.0,) * 3, scales=(0.0, 1.0, 1.0))


@pytest.mark.parametrize("version_minor", [0, 1])
def test_the_las_versions_below_1_2(version_minor):
    """Which lazpy has always read and would not write, so there were files
    it could copy no further than."""
    points, layout = source_points("pt1_v0.las")

    data = written_file(1, None, points, layout, version_minor=version_minor)

    with Reader(io.BytesIO(data)) as reader:
        assert reader.header["version_minor"] == version_minor
        assert reader.header["header_size"] == _header_size(version_minor)
        assert reader.checksum() == REFERENCE_HASH["pt1_v0.las"]


def test_colour_needs_las_12():
    with pytest.raises(UnsupportedFileError, match="needs LAS 1.2"):
        a_writer(point_format=2, version_minor=1)


def test_the_default_version_is_not_the_oldest_one_that_would_do():
    """Format 1 could be a LAS 1.0 file; a file written today should not
    claim to predate the century."""
    with a_writer() as writer:
        assert writer.header["version_minor"] == 2


def test_a_header_carries_the_user_data_it_was_given():
    """The bytes between the fields LAS defines and the records behind them,
    which the reader has always handed back and nothing could write."""
    user_data = b"something of my own"
    points, layout = source_points("pt1_v0.las")

    data = written_file(1, None, points, layout,
                        user_data_in_header=user_data)

    with Reader(io.BytesIO(data)) as reader:
        header = reader.header
        assert header["user_data"] == user_data
        assert header["header_size"] == _header_size(2, len(user_data))
        assert header["offset_to_point_data"] == header["header_size"]
        assert reader.checksum() == REFERENCE_HASH["pt1_v0.las"]


def test_a_header_whose_length_stops_making_sense_is_refused():
    """The one thing a caller may not do to writer.header before close."""
    writer = a_writer()
    writer.header["header_size"] = 300
    with pytest.raises(LazError, match="but this file declares 300"):
        writer.close()
