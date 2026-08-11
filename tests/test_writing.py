import io
import random
import struct

import pytest

from lazpy import Compressor, Reader, ItemType
from helpers import (LAS14_FORMATS, LEGACY_FORMATS,
                     assert_reproduces_point_block, compress, fixture,
                     las_records, load, pack_point14, packed_records,
                     point14_scanner_channel, rebuilt, written_block)


# ---------------------------------------------------------------------------
# Writing.
#
# testdata/ is an oracle for writing as well as reading: up to point format 5,
# every ptN_v0.las holds the same points as the ptN_v1/v2 .laz beside it,
# uncompressed, so feeding those raw records to the writer should reproduce the
# compressed files laszip produced -- byte for byte, because the encoder is
# deterministic. That is a far stronger claim than a round trip, and it is what
# these tests make. Above format 5 the .las fixture is not a faithful copy of
# the points; see the layered section further down for where they come from
# instead.
#
# cpylaz.PointWriter is the whole container: the chunk boundaries, the raw
# first point of each chunk, the item writers behind it and the chunk table at
# the end. What it emits is a fixture's point block in its entirety, so that is
# what the byte-identity tests compare.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("point_format", LEGACY_FORMATS)
def test_v1_output_is_byte_identical_to_laszip(point_format):
    """The whole point block of a non-chunked v1 file, reproduced exactly."""
    assert_reproduces_point_block(f"pt{point_format}_v1_pointwise.laz",
                                  las_records(f"pt{point_format}_v0.las"))


@pytest.mark.parametrize("point_format", LEGACY_FORMATS)
def test_v2_output_is_byte_identical_to_laszip(point_format):
    """Every chunk of a chunked v2 file, and its chunk table, reproduced."""
    assert_reproduces_point_block(f"pt{point_format}_v2.laz",
                                  las_records(f"pt{point_format}_v0.las"))


@pytest.mark.parametrize("point_format", LEGACY_FORMATS)
def test_raw_output_is_byte_identical(point_format):
    """An uncompressed container writes the records straight through."""
    records = las_records(f"pt{point_format}_v0.las")
    items = [(t, size, 0)
             for t, size, _ in load(f"pt{point_format}_v2.laz").items]
    fp = io.BytesIO()
    compress(fp, records, items, Compressor.NONE)
    assert fp.getvalue() == b"".join(records)

# ---------------------------------------------------------------------------
# The layered (v3/v4) writers, point formats 6-10.
#
# Above point format 5 the ptN_v0.las fixture is not a usable source of points.
# tools/mklaz.cpp sets the four extended classification flags but leaves the
# legacy synthetic/keypoint/withheld bits clear, and LASzip's raw POINT14
# writer rebuilds three of those four flags out of the legacy bits -- so the
# uncompressed fixture lost flags that the compressed one beside it kept. The
# committed reference hashes say as much: pt6_v0.las and pt6_v3.laz do not
# agree. Decoding the .laz and packing its points back into records is what
# feeds the writers the points laszip actually compressed.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("point_format", LAS14_FORMATS)
@pytest.mark.parametrize("version", (3, 4))
def test_layered_output_is_byte_identical_to_laszip(point_format, version):
    """Every chunk of a v3 or v4 file, reproduced exactly."""
    name = f"pt{point_format}_v{version}.laz"
    assert_reproduces_point_block(name, packed_records(name))


def test_the_layered_fixtures_switch_scanner_channel_mid_chunk():
    """What makes the test above cover the four context sets and the handshake.

    A file that stayed on one scanner channel would exercise none of it, so
    this pins the property the byte-identity test depends on rather than
    trusting the generator.
    """
    name = "pt6_v3.laz"
    with Reader(fixture(name)) as reader:
        channels = [point.extended_scanner_channel for point in reader]

    assert set(channels) == {0, 1, 2, 3}
    chunk_size = load(name).chunk_size
    assert any(channels[i] != channels[i + 1] and (i + 1) % chunk_size
               for i in range(len(channels) - 1))


# An Eulerian circuit of the four scanner channels: every ordered pair of
# distinct channels appears exactly once, and it closes back on 0 so repeating
# it keeps that true. Every context switch therefore happens in both
# directions, which is what separates a switch that creates a context from one
# that returns to a used one -- the case v3 and v4 disagree about.
CHANNEL_WALK = (0, 1, 0, 2, 0, 3, 1, 2, 1, 3, 2, 3)


def awkward_las14_records(count, items):
    """LAS 1.4 points chosen to reach the branches the fixtures never do.

    Return numbers cover the whole 4-bit range, including the counts above
    seven where the legacy 3-bit copies saturate; the scanner channel follows
    CHANNEL_WALK, holding each channel long enough for its contexts to carry
    real state; and the gps times repeat, creep, leap and drop back into an
    earlier sequence.
    """
    rand = random.Random(14)
    records = []
    sequences = [1.0e9, 5.0e9, 9.0e9]
    for i in range(count):
        number_of_returns = 1 + (i % 15)
        return_number = 1 + (i % number_of_returns)
        scanner_channel = CHANNEL_WALK[(i // 7) % len(CHANNEL_WALK)]
        which = (i // 11) % len(sequences)
        sequences[which] += (0.0 if i % 9 == 0 else
                             1.0e6 if i % 53 == 0 else
                             0.0005 * (1 + i % 37))

        record = bytearray()
        for item_type, size, _ in items:
            if item_type == ItemType.POINT14:
                record += pack_point14(
                    rand.randrange(-2**30, 2**30) if i % 5 == 0 else i * 11,
                    i * -13 if i % 4 else rand.randrange(-2**30, 2**30),
                    (i * i) % 70000, rand.randrange(65536),
                    return_number, number_of_returns,
                    i % 16, scanner_channel, i & 1, (i >> 1) & 1,
                    rand.randrange(256), rand.randrange(256),
                    rand.randrange(-32768, 32768), rand.randrange(65536),
                    sequences[which])
            elif item_type in (ItemType.RGB14, ItemType.RGBNIR14):
                channels = size // 2
                record += struct.pack(f"<{channels}H",
                                      *(rand.randrange(65536)
                                        for _ in range(channels)))
            elif item_type == ItemType.WAVEPACKET14:
                record += struct.pack("<BQIifff", rand.randrange(256),
                                      rand.randrange(2**40),
                                      rand.randrange(2**20),
                                      rand.randrange(-2**20, 2**20),
                                      *(rand.uniform(-1, 1) for _ in range(3)))
            elif item_type == ItemType.BYTE14:
                record += bytes(rand.randrange(256) for _ in range(size))
            else:
                raise AssertionError(f"unexpected item type {item_type}")
        records.append(bytes(record))
    return records


class TestLayeredPointsReadBack:
    """
    Byte-identity only covers the points laszip happened to generate. These
    feed the layered writers deliberately awkward points, wrap the result in a
    real container and read it back.

    Point format 10 carries POINT14, RGBNIR14, WAVEPACKET14 and BYTE14 at once;
    format 7 is what covers the three-channel RGB14, which no other format has.
    """

    @pytest.mark.parametrize("point_format", (7, 10))
    @pytest.mark.parametrize("version", (3, 4))
    def test_round_trips_across_chunks(self, point_format, version):
        name = f"pt{point_format}_v{version}.laz"
        records = awkward_las14_records(400, load(name).items)
        assert len(records) > load(name).chunk_size      # more than one chunk

        # every ordered pair of scanner channels, staying put included, and
        # POINT14 is item 0 so its record is at the front
        channels = [point14_scanner_channel(record) for record in records]
        assert len(set(zip(channels, channels[1:]))) == 16

        raw = f"pt{point_format}_v0.las"
        with Reader(rebuilt(raw, b"".join(records), len(records))) as reader:
            expected = reader.checksum()

        block = written_block(name, records)
        with Reader(rebuilt(name, block, len(records))) as reader:
            assert reader.checksum() == expected


class TestWrittenPointsReadBack:
    """
    Byte-identity only covers the points laszip happened to generate. These
    feed the writers deliberately awkward points, wrap the result in a real
    container and read it back, which is the only check available where there
    is no laszip reference to compare against.

    Point format 5 carries every legacy item at once, so one format covers all
    of them.
    """

    def test_v1_round_trips(self, records, expected):
        name = "pt5_v1_pointwise.laz"
        block = written_block(name, records)
        with Reader(rebuilt(name, block, len(records))) as reader:
            assert reader.checksum() == expected

    def test_v2_round_trips_across_chunks(self, records, expected):
        name = "pt5_v2.laz"
        assert len(records) > load(name).chunk_size      # more than one chunk

        block = written_block(name, records)
        with Reader(rebuilt(name, block, len(records))) as reader:
            assert reader.checksum() == expected
