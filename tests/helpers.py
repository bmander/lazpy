"""What more than one test file needs: the fixture inventory, the
reference hashes, and the kit that rebuilds and recompresses a
fixture's point block."""

import collections
import functools
import io
import os
import random
import struct

import lazpy
from lazpy import _cpylaz as cpylaz
from lazpy import Compressor, ItemType, LASZIP_VLR_USER_ID, Reader

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTDATA = os.path.join(REPO_ROOT, "testdata")


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
REFERENCE_HASH = {name: (digest, count)
                  for name, digest, count in REFERENCE_HASHES}


def fixture(name):
    return os.path.join(TESTDATA, name)


def a_record(user_id, record_id, data, description=b""):
    """One variable length record, in the shape a Writer takes -- which is
    the shape a Reader hands back, minus the fields it derives."""
    return {"user_id": user_id, "record_id": record_id, "data": data,
            "description": description}


def field_span(name, version_minor):
    """Where a header field sits, from the same tables that define it."""
    offset = 0
    for fmt in lazpy.header_formats(version_minor):
        for field, size, _ in fmt:
            if field == name:
                return offset, size
            offset += size
    raise KeyError(name)


def header_field(data, name, version_minor=2):
    """The value of an unsigned header field, straight out of a file's bytes.

    For reading what a file says about itself before lazpy has had its say.
    """
    offset, size = field_span(name, version_minor)
    return int.from_bytes(data[offset:offset + size], "little")


LEGACY_FORMATS = range(6)
LAS14_FORMATS = range(6, 11)


# The fields lazpy does not hand back, and that a rebuilt header has to be
# patched at directly. LAS 1.4 zeroes the legacy point count for the extended
# point types and keeps the real one further along.
LEGACY_POINT_COUNT_OFFSET = field_span("number_of_point_records", 2)[0]
EXTENDED_POINT_COUNT_OFFSET = field_span(
    "extended_number_of_point_records", 4)[0]
# The chunk size sits 12 bytes into the LASzip VLR's payload, which begins 54
# bytes into the record -- whose 16-byte user id starts at offset 2.
LASZIP_VLR_CHUNK_SIZE_OFFSET = -2 + 54 + 12

FixtureFile = collections.namedtuple(
    "FixtureFile", "data header num_points items compressor chunk_size")


@functools.lru_cache(maxsize=None)
def load(name):
    """A fixture's bytes, plus what lazpy parses out of its header.

    Going through Reader rather than unpacking header offsets by hand keeps
    these helpers honest about the fields that moved in LAS 1.4 -- num_points
    is the extended count where there is one.
    """
    with open(fixture(name), "rb") as fh:
        data = fh.read()
    with Reader(io.BytesIO(data)) as reader:
        # the VLR's own fields rather than Reader.chunk_size, which is None
        # for the containers that do not chunk: what these helpers want is the
        # field to write back out when re-encoding the fixture
        laz = reader.laz_header
        compressor = Compressor.NONE if laz is None else laz["compressor"]
        chunk_size = 0 if laz is None else laz["chunk_size"]
        return FixtureFile(data, reader.header, reader.num_points,
                           tuple((int(t), size, version)
                                 for t, size, version in reader.items),
                           int(compressor), chunk_size)


def las_records(name):
    """The uncompressed point records of a .las fixture, as they sit on disk.

    For point formats 0-5 a record is exactly the concatenation of its LAZ
    items, so these go straight into a writer.
    """
    f = load(name)
    start = f.header["offset_to_point_data"]
    length = f.header["point_data_record_length"]
    return [f.data[start + i * length: start + (i + 1) * length]
            for i in range(f.num_points)]


def point_block(name):
    """Everything a fixture holds from the first point onward."""
    f = load(name)
    return f.data[f.header["offset_to_point_data"]:]


def rebuilt_header(name, count, chunk_size=None):
    """A fixture's header and VLRs, holding `count` points.

    `chunk_size` overrides what the LASzip VLR declares, which a round trip at
    any size other than the fixture's needs: the VLR is where the reader gets
    it from, and -1 there is what selects adaptive chunking.
    """
    f = load(name)
    header = bytearray(f.data[:f.header["offset_to_point_data"]])
    if struct.unpack_from("<I", header, LEGACY_POINT_COUNT_OFFSET)[0]:
        struct.pack_into("<I", header, LEGACY_POINT_COUNT_OFFSET, count)
    if f.header["version_minor"] >= 4:
        struct.pack_into("<Q", header, EXTENDED_POINT_COUNT_OFFSET, count)
    if chunk_size is not None:
        offset = (header.index(LASZIP_VLR_USER_ID)
                  + LASZIP_VLR_CHUNK_SIZE_OFFSET)
        struct.pack_into("<i", header, offset, chunk_size)
    return bytes(header)


def rebuilt(name, block, count):
    """A fixture's header and VLRs, with our own point block behind them."""
    return io.BytesIO(rebuilt_header(name, count) + block)


def compress(fp, records, items, compressor, chunk_size=0, breaks=(),
             start_offset=-1):
    """Drive a PointWriter over `records`, and hand it back once it is closed.

    `breaks` are the indices to close the open chunk in front of, which only
    adaptive chunking allows; an index appearing twice closes a chunk twice
    over. The chunk size is masked because the VLR declares it signed, and -1
    there -- adaptive -- is U32_MAX to the writer.
    """
    writer = cpylaz.PointWriter(fp, items, compressor,
                                chunk_size=chunk_size & 0xFFFFFFFF,
                                start_offset=start_offset)
    for index, record in enumerate(records):
        for _ in range(breaks.count(index)):
            writer.chunk()
        writer.write(record)
    writer.done()
    return writer


def written_block(name, records):
    """The point block `records` compress to, in the container `name` declares.

    Written behind that fixture's own header, because a chunk table holds
    absolute file positions: a block written anywhere else is not the block the
    fixture holds, however identical the compressed points are.
    """
    f = load(name)
    start = f.header["offset_to_point_data"]
    fp = io.BytesIO(f.data[:start])
    fp.seek(0, io.SEEK_END)
    compress(fp, records, f.items, f.compressor, f.chunk_size)
    return fp.getvalue()[start:]


def assert_reproduces_point_block(name, records):
    """Compressing `records` rebuilds `name`'s point block: every chunk, the
    offset in front of them, and the chunk table behind."""
    assert written_block(name, records) == point_block(name)


def pack_point14(X, Y, Z, intensity, return_number, number_of_returns,
                 classification_flags, scanner_channel, scan_direction_flag,
                 edge_of_flight_line, classification, user_data, scan_angle,
                 point_source_ID, gps_time):
    """The 30-byte LAS 1.4 point record, from its fields.

    Byte 14 and byte 15 each pack several fields, and both have to agree with
    raw_write_point14 in the extension, so they are composed in one place.
    """
    return struct.pack(
        "<iiiHBBBBhHd", X, Y, Z, intensity,
        return_number | (number_of_returns << 4),
        classification_flags | (scanner_channel << 4) |
        (scan_direction_flag << 6) | (edge_of_flight_line << 7),
        classification, user_data, scan_angle, point_source_ID, gps_time)


def point14_scanner_channel(record):
    """The scanner channel back out of a record pack_point14 built."""
    return (record[15] >> 4) & 3


def pack_las14_record(point, items):
    """A decoded point, back in the layout its items have on disk."""
    out = bytearray()
    for item_type, size, _ in items:
        if item_type == ItemType.POINT14:
            out += pack_point14(
                point.X, point.Y, point.Z, point.intensity,
                point.extended_return_number,
                point.extended_number_of_returns,
                point.extended_classification_flags,
                point.extended_scanner_channel,
                point.scan_direction_flag, point.edge_of_flight_line,
                point.extended_classification, point.user_data,
                point.extended_scan_angle, point.point_source_ID,
                point.gps_time)
        elif item_type == ItemType.RGB14:
            out += struct.pack("<HHH", *point.rgb[:3])
        elif item_type == ItemType.RGBNIR14:
            out += struct.pack("<HHHH", *point.rgb)
        elif item_type == ItemType.WAVEPACKET14:
            out += bytes(point.wave_packet)
        elif item_type == ItemType.BYTE14:
            out += bytes(point.extra_bytes)[:size]
        else:
            raise AssertionError(f"unexpected item type {item_type}")
    return bytes(out)


def packed_records(name):
    """The records of a LAS 1.4 fixture, rebuilt from what it decodes to."""
    items = load(name).items
    with Reader(io.BytesIO(load(name).data)) as reader:
        return [pack_las14_record(point, items) for point in reader]


def awkward_records(count, length):
    """Points chosen to reach the branches the tidy fixtures never do.

    Coordinates that jump the full I32 range, gps times that repeat, creep and
    leap far enough to start a new time sequence, and colours, wavepackets and
    extra bytes with no structure at all.
    """
    rand = random.Random(4)
    records = []
    gps_time = 1.0e9
    for i in range(count):
        r = bytearray(length)
        struct.pack_into("<iii", r, 0,
                         (rand.randrange(-2**30, 2**30) if i % 7 == 0
                          else i * 13),
                         i * -7 if i % 3 else rand.randrange(-2**30, 2**30),
                         i * i % 90000)
        struct.pack_into("<H", r, 12, rand.randrange(65536))
        for offset in (14, 15, 16, 17):
            r[offset] = rand.randrange(256)
        struct.pack_into("<H", r, 18, rand.randrange(65536))
        gps_time += (1e6 if i % 50 == 0 else
                     0.0 if i % 11 == 0 else 0.001 * rand.randrange(1000))
        struct.pack_into("<d", r, 20, gps_time)
        struct.pack_into("<HHH", r, 28,
                         *(rand.randrange(65536) for _ in range(3)))
        r[34] = rand.randrange(256)
        struct.pack_into("<QIifff", r, 35,
                         rand.randrange(2**40), rand.randrange(2**20),
                         rand.randrange(-2**20, 2**20),
                         *(rand.uniform(-1, 1) for _ in range(3)))
        for offset in range(63, length):
            r[offset] = rand.randrange(256)
        records.append(bytes(r))
    return records
