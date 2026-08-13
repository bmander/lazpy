import collections
import io

import pytest

import lazpy
from lazpy import _cpylaz as cpylaz
from lazpy import (Compressor, LASZIP_VLR_RECORD_ID, Reader, ItemType,
                   LazError, UnsupportedFileError, Writer)
from helpers import (FIXTURES, REFERENCE_HASH, assert_is_the_file_laszip_wrote,
                     fixture, header_field, load)
from lazpy.compat import (_compatibility_layout,
                          _extra_bytes_attributes)
from lazpy.formats import _POINT_FORMATS
from lazpy.headers import _header_size, _find_laz_header, _read_las_header


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

# Every compatibility-mode fixture: the legacy point format and LAS version it
# wears, the format it stands in for, how many bytes it hides per point --
# five, or seven where there is a NIR band to hide too -- and how many extra
# bytes it has of its own.
CompatFixture = collections.namedtuple(
    "CompatFixture", "stem legacy minor upgraded hidden extra")
COMPAT_BY_NAME = {
    f"{f.stem}_v{v}.{ext}": f
    for f in [
        CompatFixture("pt6_compat", 1, 2, 6, 5, 6),
        CompatFixture("pt7_compat", 3, 2, 7, 5, 6),
        CompatFixture("pt8_compat", 3, 2, 8, 7, 6),
        CompatFixture("pt9_compat", 4, 3, 9, 5, 6),
        CompatFixture("pt10_compat", 5, 3, 10, 7, 6),
        # no extra bytes of its own, which is the ordinary shape of one of
        # these: nothing is left for the "extra bytes" record to describe
        CompatFixture("pt8_compat_noextra", 3, 2, 8, 7, 0),
    ]
    for v, ext in ((0, "las"), (2, "laz"))
}
COMPAT_NAMES = list(COMPAT_BY_NAME)


@pytest.mark.parametrize("name", COMPAT_NAMES)
def test_a_compatibility_file_reads_as_the_las_14_file_it_stands_in(name):
    """The version, point format and record length laszip would report."""
    f = COMPAT_BY_NAME[name]
    data = load(name).data

    # what the file says about itself before lazpy has had its say
    assert header_field(data, "version_minor") == f.minor
    assert header_field(data, "point_data_format_id") & 0x7F == f.legacy

    with Reader(fixture(name)) as reader:
        header = reader.header
        assert header["version_minor"] == 4
        assert reader.point_format == f.upgraded
        assert reader.num_extra_bytes == f.extra
        # the hidden bytes are gone and the wider LAS 1.4 fields are there
        assert header_field(data, "point_data_record_length") == (
            _POINT_FORMATS[f.legacy].size + f.extra + f.hidden)
        assert header["point_data_record_length"] == (
            _POINT_FORMATS[f.upgraded].size + f.extra)
        # the header grows by exactly the tables LAS 1.4 has and 1.2/1.3 do not
        grew = _header_size(4) - _header_size(f.minor)
        assert header["header_size"] == _header_size(4)
        assert header["header_size"] == (
            header_field(data, "header_size") + grew)
        assert header["offset_to_point_data"] == (
            header_field(data, "offset_to_point_data") + grew)


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

    assert lazpy.LASCOMPATIBLE_VLR_KEY not in vlrs
    assert len(vlrs) == declared
    if not extra:
        assert lazpy.EXTRA_BYTES_VLR_KEY not in vlrs
        return
    attributes = vlrs[lazpy.EXTRA_BYTES_VLR_KEY]
    attribute_names = [a.name for a
                       in _extra_bytes_attributes(attributes["data"])]
    assert not any(n.startswith(b"LAS 1.4 ") for n in attribute_names)
    assert len(attribute_names) == extra
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
        header = _read_las_header(fh)

    ids = [record_id for _, record_id in header["variable_length_records"]]
    assert ids.count(LASZIP_VLR_RECORD_ID) == 2
    assert _find_laz_header(header) is not None
    assert lazpy.LASCOMPATIBLE_VLR_KEY in header["variable_length_records"]


def without_compatibility_record(name):
    """A fixture with its compatibility record renamed, so nothing marks it.

    The record is left in place rather than cut out, so the file is otherwise
    byte for byte what it was: only the user id that identifies it changes.
    """
    user_id = lazpy.LASCOMPATIBLE_VLR_KEY[0]
    data = bytearray(load(name).data)
    data[data.index(user_id)] = ord("x")
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
        assert lazpy.LASCOMPATIBLE_VLR_KEY not in vlrs
        assert _compatibility_layout(reader.header) is None


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
        assert list(_extra_bytes_attributes(data)) == [
            (b"a", 0, 0, 2), (b"b", 192, 2, 1), (b"c", 384, 3, 8)]

    def test_an_undocumented_attribute_is_as_wide_as_it_says(self):
        """Data type 0 means bytes nobody described, however many of them."""
        data = self.descriptor(b"raw", 0, options=13)
        assert list(_extra_bytes_attributes(data)) == [
            (b"raw", 0, 0, 13)]

    def test_a_trailing_partial_descriptor_is_ignored(self):
        data = self.descriptor(b"a", 1) + b"\0" * 40
        assert [name for name, _, _, _
                in _extra_bytes_attributes(data)] == [b"a"]

    def test_an_unknown_data_type_is_refused(self):
        """Sizing it wrong would put every attribute after it in the wrong
        place, which is worse than saying so."""
        data = self.descriptor(b"a", 99)
        with pytest.raises(UnsupportedFileError, match="data type 99"):
            list(_extra_bytes_attributes(data))


# ---------------------------------------------------------------------------
# Writing one.
#
# The other direction, which is laszip_request_compatibility_mode() on the
# writing side: LAS 1.4 points in, a legacy file out. The oracle is the same
# fixtures the reading tests use -- given the points laszip was given, lazpy
# writes the file laszip wrote, which is a stronger claim than a round trip
# and covers every rule the disguise applies at once.
# ---------------------------------------------------------------------------

def disguised(name, **kwargs):
    """A fixture's own points, written back out in compatibility mode."""
    f = COMPAT_BY_NAME[name]
    with Reader(fixture(name)) as reader:
        points = [point.copy() for point in reader]
        header = dict(reader.header)
        layout = dict(num_extra_bytes=reader.num_extra_bytes,
                      scales=reader.scales, offsets=reader.offsets)

    buf = io.BytesIO()
    with Writer(buf, f.upgraded, compatibility=True,
                compressed=name.endswith(".laz"),
                chunk_size=load(name).chunk_size or 50000,
                system_identifier=header["system_identifier"],
                generating_software=header["generating_software"],
                # laszip names itself in every record it writes, having
                # already named itself in the header
                vlr_description=header["generating_software"],
                file_creation=(header["file_creation_day"],
                               header["file_creation_year"]),
                **layout, **kwargs) as writer:
        for point in points:
            writer.write(point)
    return buf.getvalue(), points


@pytest.mark.parametrize("name", COMPAT_NAMES)
def test_a_disguised_file_is_the_file_laszip_wrote(name):
    """Every byte of it: the downgraded header, the two records that describe
    the disguise, and points whose LAS 1.4 fields have been folded into their
    extra bytes exactly as laszip folds them.
    """
    f = COMPAT_BY_NAME[name]

    ours, _ = disguised(name)

    assert_is_the_file_laszip_wrote(ours, load(name).data, f.minor)


@pytest.mark.parametrize("name", COMPAT_NAMES)
def test_a_disguised_file_reads_back_as_the_points_that_went_in(name):
    """Which is what the disguise is for: lazpy's own reader, and laszip
    asked for the same mode, put the LAS 1.4 points back together."""
    data, points = disguised(name)

    with Reader(io.BytesIO(data)) as reader:
        assert reader.point_format == COMPAT_BY_NAME[name].upgraded
        # the checksum is over every field of every point, and pins how many
        # there were as well
        assert reader.checksum() == REFERENCE_HASH[name]
        assert len(points) == reader.num_points


@pytest.mark.parametrize("name", COMPAT_NAMES)
def test_the_header_is_the_legacy_one_the_file_wears(name):
    """Read back, it is the LAS 1.4 file again; on disk it is not."""
    f = COMPAT_BY_NAME[name]
    data, _ = disguised(name)

    assert header_field(data, "version_minor") == f.minor
    assert header_field(data, "point_data_format_id") & 0x7F == f.legacy
    assert (header_field(data, "point_data_record_length", f.minor) ==
            _POINT_FORMATS[f.legacy].size + f.extra + f.hidden)

    with Reader(io.BytesIO(data)) as reader:
        assert reader.point_format == f.upgraded
        assert reader.num_extra_bytes == f.extra


def test_the_counts_go_in_the_record_the_legacy_header_has_no_room_for():
    """They are only known once the last point is written, and the record
    was written before the first one: it is rewritten at close."""
    data, points = disguised("pt6_compat_v0.las")

    with Reader(io.BytesIO(data)) as reader:
        by_return = collections.Counter(p.extended_return_number
                                        for p in points)
        assert (reader.header["extended_number_of_points_by_return"] ==
                [by_return[n] for n in range(1, 16)])
        # the legacy field states the five returns it has room for
        assert header_field(data, "number_of_point_records") == len(points)


def test_the_laszip_record_says_the_file_was_written_this_way():
    """laszip sets the low bit of the options field for a disguised file."""
    data, _ = disguised("pt6_compat_v2.laz")

    with Reader(io.BytesIO(data)) as reader:
        assert reader.laz_header["options"] == 1


@pytest.mark.parametrize("point_format", [0, 1, 5])
def test_a_legacy_format_has_nothing_to_disguise(point_format):
    with pytest.raises(UnsupportedFileError, match="point formats 6 to 10"):
        Writer(io.BytesIO(), point_format, compatibility=True)


def test_las_14_writes_its_points_as_they_are():
    with pytest.raises(UnsupportedFileError, match="predates it"):
        Writer(io.BytesIO(), 6, compatibility=True, version_minor=4)


def test_records_are_not_what_a_disguised_writer_takes():
    """The bytes of a point would already be the legacy record it is about to
    build, and there would be nothing left to fold."""
    with Writer(io.BytesIO(), 6, compatibility=True) as writer:
        with pytest.raises(ValueError, match="takes points, not records"):
            writer.write(b"\0" * 39)


def test_a_disguised_file_carries_the_records_it_was_given():
    """The two the disguise needs go in front; whatever else the caller had
    still travels."""
    wkt = {"user_id": b"LASF_Projection", "record_id": 2112, "data": b"WKT",
           "description": b""}

    buf = io.BytesIO()
    with Writer(buf, 6, compatibility=True, vlrs=[wkt]) as writer:
        writer.write(lazpy.Point(X=1, extended_return_number=1))

    with Reader(io.BytesIO(buf.getvalue())) as reader:
        # the two that made it a disguise are gone from what Reader reports,
        # having been undone; the caller's own is not
        assert list(reader.header["variable_length_records"]) == [
            (b"LASF_Projection", 2112), (b"laszip encoded", 22204)]
