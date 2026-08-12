import io
import struct

import pytest

from lazpy import Point, Reader, LazError, UnsupportedFileError, Writer
from helpers import (FIXTURES, REFERENCE_HASH, a_record, field_span, fixture,
                     load)


# ---------------------------------------------------------------------------
# Extended variable length records.
#
# No fixture has any -- laszip writes none -- so they are built here and stuck
# on the end of one, which is exactly what a file with them looks like: the
# records sit behind the point data, and two header fields aim at them.
# ---------------------------------------------------------------------------

# Derived from the header tables rather than written down, which is what
# field_span is for: the same two fields are located that way further down,
# and a header this file disagreed with would be a header these tests were
# quietly testing something else about.
EVLR_OFFSET_FIELD = field_span(
    "start_of_first_extended_variable_length_record", 4)[0]
EVLR_COUNT_FIELD = field_span(
    "number_of_extended_variable_length_records", 4)[0]
EVLR_LENGTH_FIELD = 20       # record_length_after_header, within a record

# LAS 1.4, which is the only version with extended records; the same points
# compressed and not, since the two take different routes to the point data
EVLR_NAME = "pt6_v3.laz"
EVLR_NAMES = [EVLR_NAME, "pt6_v0.las"]


def evlr_bytes(user_id, record_id, payload, description=b""):
    """One extended record on disk: 60 bytes of header, then the payload."""
    return struct.pack("<H16sHQ32s", 0, user_id, record_id, len(payload),
                       description) + payload


def with_evlrs(*records, name=EVLR_NAME, declared=None, start=None):
    """A fixture's bytes, with `records` appended and the header aimed at them.

    `declared` and `start` override the two header fields, which is how a file
    that lies about the records it holds gets built.
    """
    data = bytearray(load(name).data)
    offset = len(data)
    for record in records:
        data += record
    struct.pack_into("<Q", data, EVLR_OFFSET_FIELD,
                     offset if start is None else start)
    struct.pack_into("<I", data, EVLR_COUNT_FIELD,
                     len(records) if declared is None else declared)
    return bytes(data)


def evlrs_of(reader):
    return reader.header["extended_variable_length_records"]


def test_extended_records_are_parsed():
    data = with_evlrs(
        evlr_bytes(b"LASF_Projection", 2112, b"WKT" * 100, b"a projection"),
        evlr_bytes(b"lazpy", 1, b"payload"))

    with Reader(io.BytesIO(data)) as reader:
        evlrs = evlrs_of(reader)
        assert set(evlrs) == {(b"LASF_Projection", 2112), (b"lazpy", 1)}

        wkt = evlrs[(b"LASF_Projection", 2112)]
        assert wkt["user_id"] == b"LASF_Projection"
        assert wkt["record_id"] == 2112
        assert wkt["description"] == b"a projection"
        assert wkt["record_length_after_header"] == 300
        assert wkt["data"] == b"WKT" * 100

        assert evlrs[(b"lazpy", 1)]["data"] == b"payload"
        assert reader.warning is None


@pytest.mark.parametrize("name", EVLR_NAMES)
def test_extended_records_leave_the_points_alone(name):
    """Compressed or not, and whatever else the reader does with the file."""
    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"payload"), name=name)
    with Reader(io.BytesIO(data)) as reader:
        assert evlrs_of(reader)[(b"lazpy", 1)]["data"] == b"payload"
        assert reader.checksum() == REFERENCE_HASH[name]


def test_an_extended_record_reads_like_a_dict():
    """The payload arrives late, but it is a key like any other."""
    with Reader(io.BytesIO(with_evlrs(evlr_bytes(b"lazpy", 1, b"load")))) as r:
        record = evlrs_of(r)[(b"lazpy", 1)]
        assert set(record) == {"reserved", "user_id", "record_id",
                               "record_length_after_header", "description",
                               "offset_to_data", "data"}
        assert "data" in record
        assert record.get("data") == b"load"
        assert dict(record)["data"] == b"load"


def test_records_sharing_a_record_id_are_kept_apart():
    """LAS namespaces records by user id -- LASF_Spec reserves ids 0 to 99 for
    waveform packet descriptors -- so the id alone is not a key."""
    data = with_evlrs(evlr_bytes(b"LASF_Spec", 7, b"theirs"),
                      evlr_bytes(b"lazpy", 7, b"ours"))
    with Reader(io.BytesIO(data)) as reader:
        assert evlrs_of(reader)[(b"LASF_Spec", 7)]["data"] == b"theirs"
        assert evlrs_of(reader)[(b"lazpy", 7)]["data"] == b"ours"
        assert reader.warning is None


class RecordingBytesIO(io.BytesIO):
    """A file that remembers which of its bytes were read."""

    def __init__(self, data):
        super().__init__(data)
        self.reads = []

    def read(self, size=-1):
        start = self.tell()
        data = super().read(size)
        self.reads.append((start, len(data)))
        return data

    def readinto(self, buffer):
        """Recorded too, since that is the one the decoder really calls.

        Without this the stream's refill goes through BytesIO's own readinto
        and reads nothing this class can see, which leaves every assertion
        below passing whatever the decoder touched.
        """
        start = self.tell()
        n = super().readinto(buffer)
        self.reads.append((start, n))
        return n

    def touched(self, start, length):
        return any(at < start + length and start < at + n
                   for at, n in self.reads)


def test_a_payload_is_not_read_until_it_is_asked_for():
    """Opening a file must not pull its payloads in: a waveform packet record
    is allowed to be gigabytes, which is what the eight-byte length is for."""
    payload = b"w" * (1 << 20)
    data = with_evlrs(evlr_bytes(b"LASF_Spec", 65535, payload))
    # the far end of the payload, which no buffered read over the point block
    # could reach by accident
    tail = (len(data) - 1024, 1024)

    fp = RecordingBytesIO(data)
    with Reader(fp) as reader:
        record = evlrs_of(reader)[(b"LASF_Spec", 65535)]
        assert record["record_length_after_header"] == len(payload)
        assert not fp.touched(*tail)

        assert reader.checksum() == REFERENCE_HASH[EVLR_NAME]
        assert not fp.touched(*tail), "decoding read the payload"

        assert record["data"] == payload
        assert fp.touched(*tail)


def test_reading_a_payload_does_not_disturb_point_reading():
    """Points and payloads are readable in either order, and interleaved: the
    point reader owns the file by then, and keeps its own buffer over it."""
    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"first" * 500),
                      evlr_bytes(b"lazpy", 2, b"second" * 500))

    def coords(point):
        return (point.X, point.Y, point.Z, point.gps_time)

    with Reader(io.BytesIO(data)) as reader:
        expected = [coords(p) for p in reader]

    with Reader(io.BytesIO(data)) as reader:
        evlrs = evlrs_of(reader)
        first, second = evlrs[(b"lazpy", 1)], evlrs[(b"lazpy", 2)]
        assert first["data"] == b"first" * 500          # before any point

        # read on, without a seek to paper over a disturbed decoder
        got = [coords(reader.read()) for _ in range(10)]
        assert second["data"] == b"second" * 500        # mid-decode
        got += [coords(reader.read())
                for _ in range(reader.num_points - len(got))]
        assert got == expected


@pytest.mark.parametrize("name", FIXTURES)
def test_files_without_extended_records_have_none(name):
    """Every version, including the 1.4 files that could have had them.

    An always-present key, so nothing has to ask whether a file is new enough
    to have had any. Decoding is not re-checked here: these are the fixtures
    exactly as they sit on disk, which test_decodes_identically_to_laszip
    already reads end to end.
    """
    with Reader(fixture(name)) as reader:
        assert evlrs_of(reader) == {}


def test_a_short_block_keeps_what_is_there_and_warns():
    """A malformed record behind the points says nothing about the points, so
    the file still opens."""
    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"here"), declared=3)
    with Reader(io.BytesIO(data)) as reader:
        assert list(evlrs_of(reader)) == [(b"lazpy", 1)]
        assert reader.warning == ("file declares 3 extended variable length "
                                  "records but holds 1")
        assert reader.checksum() == REFERENCE_HASH[EVLR_NAME]


def test_a_wild_record_count_stops_at_the_end_of_the_file():
    """The count is a U32 out of the header and worth no more trust than that:
    what stops the walk is running out of file, not the count."""
    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"here"), declared=0xFFFFFFFF)
    with Reader(io.BytesIO(data)) as reader:
        assert list(evlrs_of(reader)) == [(b"lazpy", 1)]
        assert "holds 1" in reader.warning
        assert reader.checksum() == REFERENCE_HASH[EVLR_NAME]


def test_a_block_past_the_end_of_the_file_warns():
    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"here"), start=1 << 40)
    with Reader(io.BytesIO(data)) as reader:
        assert evlrs_of(reader) == {}
        assert "holds 0" in reader.warning
        assert reader.checksum() == REFERENCE_HASH[EVLR_NAME]


def test_a_payload_running_past_the_end_of_the_file_is_not_handed_over_short():
    record = bytearray(evlr_bytes(b"lazpy", 1, b"here"))
    struct.pack_into("<Q", record, EVLR_LENGTH_FIELD, 1 << 20)
    with Reader(io.BytesIO(with_evlrs(bytes(record)))) as reader:
        assert evlrs_of(reader) == {}
        assert "holds 0" in reader.warning


def test_a_payload_that_goes_away_under_the_reader_raises():
    """The length was checked against the file when it was opened, so this can
    only happen to a file that shrank since. It is still not a short read."""
    fp = io.BytesIO(with_evlrs(evlr_bytes(b"lazpy", 1, b"payload")))
    with Reader(fp) as reader:
        record = evlrs_of(reader)[(b"lazpy", 1)]
        fp.truncate(record["offset_to_data"] + 3)
        with pytest.raises(LazError, match="past the end"):
            record["data"]


def test_a_stream_that_cannot_seek_fails_as_a_stream_that_cannot_seek():
    """Extended records are behind the point data, so reading them means
    seeking -- but a file that cannot seek was already unreadable, and that is
    the error worth getting."""
    class Unseekable(io.RawIOBase):
        def __init__(self, data):
            self._buffer = io.BytesIO(data)

        def read(self, size=-1):
            return self._buffer.read(size)

        def readable(self):
            return True

        def seekable(self):
            return False

    data = with_evlrs(evlr_bytes(b"lazpy", 1, b"payload"))
    with pytest.raises(LazError, match="seek to the start of point data"):
        Reader(Unseekable(data))


def test_a_payload_already_read_outlives_the_reader(tmp_path):
    path = tmp_path / "evlrs.laz"
    path.write_bytes(with_evlrs(evlr_bytes(b"lazpy", 1, b"payload")))
    with Reader(str(path)) as reader:
        record = evlrs_of(reader)[(b"lazpy", 1)]
        assert record["data"] == b"payload"
    assert record["data"] == b"payload"


def test_a_payload_cannot_be_read_once_the_file_is_closed(tmp_path):
    path = tmp_path / "evlrs.laz"
    path.write_bytes(with_evlrs(evlr_bytes(b"lazpy", 1, b"payload")))
    with Reader(str(path)) as reader:
        record = evlrs_of(reader)[(b"lazpy", 1)]
    with pytest.raises(LazError, match="closed"):
        record["data"]


# ---------------------------------------------------------------------------
# Writing them.
#
# They go behind the point block, so the writer can leave them to close():
# everything in front is already written and none of it moves, and the two
# header fields that address them join the ones close() was going back for
# anyway. That is also why they need not all be known up front, unlike the
# ordinary records, which the header counts before the points begin.
# ---------------------------------------------------------------------------

def written(evlrs=(), point_format=6, points=1, **kwargs):
    """A small LAS 1.4 file carrying `evlrs`, as bytes."""
    buf = io.BytesIO()
    with Writer(buf, point_format, evlrs=evlrs, **kwargs) as writer:
        for index in range(points):
            writer.write(Point(X=index))
    return buf.getvalue()


def test_extended_records_are_written_and_read_back():
    data = written([a_record(b"lazpy", 1, b"payload", b"a description"),
                    a_record(b"LASF_Spec", 65535, b"more")])

    with Reader(io.BytesIO(data)) as reader:
        header = reader.header
        assert header["number_of_extended_variable_length_records"] == 2
        assert (header["start_of_first_extended_variable_length_record"] >
                header["offset_to_point_data"])
        records = evlrs_of(reader)
        assert list(records) == [(b"lazpy", 1), (b"LASF_Spec", 65535)]
        assert records[(b"lazpy", 1)]["data"] == b"payload"
        assert records[(b"lazpy", 1)]["description"] == b"a description"
        assert records[(b"LASF_Spec", 65535)]["data"] == b"more"
        assert reader.warning is None


def test_a_payload_no_ordinary_record_could_hold():
    """The whole reason the record type exists: a length field of eight bytes
    rather than two."""
    payload = b"WKT" * 40_000

    data = written([a_record(b"LASF_Projection", 2112, payload)])

    with Reader(io.BytesIO(data)) as reader:
        assert evlrs_of(reader)[(b"LASF_Projection", 2112)]["data"] == payload


def test_a_record_added_after_the_writer_was_opened():
    """Which is what writing something computed from the points needs."""
    buf = io.BytesIO()
    with Writer(buf, 6) as writer:
        writer.write(Point(X=1))
        writer.evlrs.append(a_record(b"lazpy", 2, b"afterwards"))

    with Reader(io.BytesIO(buf.getvalue())) as reader:
        assert evlrs_of(reader)[(b"lazpy", 2)]["data"] == b"afterwards"


def test_the_points_are_untouched_by_records_behind_them():
    """The chunk table sits between the points and the records, and nothing
    that follows it moves anything in front of it."""
    plain = written(points=20)
    with_records = written([a_record(b"lazpy", 1, b"x" * 500)], points=20)

    with Reader(io.BytesIO(with_records)) as reader:
        assert [point.X for point in reader] == list(range(20))

    # everything in front of the records is the file that has none, but for
    # the two header fields that say where they are
    front = bytearray(with_records[:len(plain)])
    assert front != plain
    for name in ("start_of_first_extended_variable_length_record",
                 "number_of_extended_variable_length_records"):
        offset, size = field_span(name, 4)
        front[offset:offset + size] = plain[offset:offset + size]
    assert bytes(front) == plain


@pytest.mark.parametrize("version_minor", [2, 3])
def test_below_las_14_there_is_nowhere_to_point_at_them(version_minor):
    with pytest.raises(UnsupportedFileError, match="need LAS 1.4"):
        written([a_record(b"lazpy", 1, b"payload")], point_format=1,
                version_minor=version_minor)


def test_one_added_late_to_a_file_that_cannot_hold_it_is_refused():
    buf = io.BytesIO()
    with pytest.raises(UnsupportedFileError, match="need LAS 1.4"):
        with Writer(buf, 1, version_minor=2) as writer:
            writer.write(Point(X=1))
            writer.evlrs.append(a_record(b"lazpy", 1, b"late"))


def test_two_records_sharing_a_key_are_refused():
    """A reader keys them by user id and record id, so it could only find
    one of them."""
    with pytest.raises(ValueError, match="two records claim"):
        written([a_record(b"lazpy", 1, b"one"), a_record(b"lazpy", 1, b"two")])


def test_a_files_extended_records_survive_a_copy():
    """What a reader hands back is what a writer takes."""
    source = written([a_record(b"lazpy", 1, b"payload", b"described"),
                      a_record(b"LASF_Projection", 2112, b"WKT" * 1000)])

    with Reader(io.BytesIO(source)) as reader:
        copy = written(evlrs_of(reader), points=0)

    with Reader(io.BytesIO(copy)) as reader:
        copied = evlrs_of(reader)
        assert list(copied) == [(b"lazpy", 1), (b"LASF_Projection", 2112)]
        assert copied[(b"lazpy", 1)]["description"] == b"described"
        assert copied[(b"LASF_Projection", 2112)]["data"] == b"WKT" * 1000
