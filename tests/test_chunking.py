import collections
import io
import struct

import pytest

from lazpy import _cpylaz as cpylaz
from lazpy import Compressor, ItemType, Reader, LazError
from helpers import compress, las_records, load, rebuilt_header


# ---------------------------------------------------------------------------
# The container itself: where the chunk boundaries fall, and the table that
# records them.
#
# Byte-identity against the fixtures already pins the fixed-size chunk table
# laszip writes, so what is left is the sizes and shapes testdata/ has no file
# for -- boundaries that fall awkwardly against the point count, chunks the
# caller ends itself, and an output that cannot seek back to patch the offset
# it left in front of the first chunk. Those are checked by reading the result
# back, which is the only oracle there is without a laszip beside us.
# ---------------------------------------------------------------------------

class WriteOnly:
    """A sink that can only be appended to, as a pipe can: no tell, no seek.

    It has to be told where in the file it starts, since it cannot say.
    """

    def __init__(self, buffer):
        self.buffer = buffer

    def write(self, data):
        return self.buffer.write(data)


WrittenFile = collections.namedtuple("WrittenFile", "data number_chunks")


class TestChunking:
    """Point format 5 again, because it carries every legacy item at once."""

    NAME = "pt5_v2.laz"

    def written(self, records, chunk_size, breaks=(), seekable=True):
        """`records` written at `chunk_size`, as a file a Reader can open.

        The header has to declare that chunk size, so it is rebuilt rather than
        taken from the fixture; the writer is told where it ends, since a
        write-only sink cannot say where it is.
        """
        header = rebuilt_header(self.NAME, len(records), chunk_size)
        buffer = io.BytesIO(header)
        buffer.seek(0, io.SEEK_END)
        writer = compress(buffer if seekable else WriteOnly(buffer), records,
                          load(self.NAME).items, Compressor.POINTWISE_CHUNKED,
                          chunk_size, breaks, start_offset=len(header))
        return WrittenFile(buffer.getvalue(), writer.number_chunks)

    @pytest.mark.parametrize("chunk_size", (1, 137, 400, 1000))
    def test_round_trips_at_any_chunk_size(self, records, expected,
                                           chunk_size):
        """One point per chunk, a size that divides the input unevenly, exactly
        the point count, and more than it."""
        written = self.written(records, chunk_size)
        assert written.number_chunks == -(-len(records) // chunk_size)
        with Reader(io.BytesIO(written.data)) as reader:
            assert reader.checksum() == expected

    def test_adaptive_chunks_round_trip(self, records, expected):
        """Chunk boundaries the caller picks, recorded as point counts in the
        table alongside the byte lengths."""
        breaks = (1, 2, 199, 200)
        written = self.written(records, -1, breaks=breaks)
        assert written.number_chunks == len(breaks) + 1
        with Reader(io.BytesIO(written.data)) as reader:
            assert reader.checksum() == expected

    def test_seeking_into_adaptive_chunks(self, records):
        """What the point counts in an adaptive table are for: without them a
        reader cannot tell which chunk holds a given point."""
        written = self.written(records, -1, breaks=(1, 2, 199, 200))
        with Reader(io.BytesIO(written.data)) as reader:
            sequential = [(p.X, p.gps_time) for p in reader]
            for index in (0, 250, 1, 199, len(records) - 1, 200, 2):
                reader.seek(index)
                point = reader.read()
                assert (point.X, point.gps_time) == sequential[index]

    def with_no_chunks_declared(self, written):
        """The same file, with its chunk table saying it holds none."""
        data = bytearray(written.data)
        start = load(self.NAME).header["offset_to_point_data"]
        table_at = struct.unpack_from("<q", data, start)[0]
        struct.pack_into("<I", data, table_at + 4, 0)
        return bytes(data)

    def test_a_lost_fixed_size_table_is_rebuilt_from_the_points(self, records,
                                                                expected):
        """Where every chunk holds the same number of points, the boundaries
        are implied by the size and the table is a convenience -- so a file
        that lost it still reads, whole."""
        data = self.with_no_chunks_declared(self.written(records, 137))
        with Reader(io.BytesIO(data)) as reader:
            assert reader.checksum() == expected

    def test_a_lost_adaptive_table_is_refused_rather_than_guessed(self,
                                                                  records):
        """Where the caller picked the boundaries, the table is the only
        record of them, and nothing else in the file implies where they fall.
        Decoding on regardless never restarts the decoder at the boundaries
        the points really have, so every point past the first one comes back
        wrong -- which is worth an error rather than an answer."""
        data = self.with_no_chunks_declared(
            self.written(records, -1, breaks=(1, 2, 199, 200)))
        # every way in, since a seek opens the first chunk itself and a read
        # that follows one takes a different path through the chunk logic
        for reach in (lambda r: list(r),
                      lambda r: [r.read() for _ in range(len(records))],
                      lambda r: (r.seek(50), r.read())):
            with pytest.raises(LazError):
                with Reader(io.BytesIO(data)) as reader:
                    reach(reader)

    def test_reading_past_the_last_adaptive_chunk_raises(self, records):
        """The running totals end at the last chunk, so the chunk after it has
        no size stated anywhere -- and a read that runs off the end of the
        points is exactly what asks for one."""
        written = self.written(records, -1, breaks=(1, 2, 199, 200))
        with Reader(io.BytesIO(written.data)) as reader:
            for _ in range(len(records)):
                reader.read()
            with pytest.raises(LazError):
                reader.read()

    @pytest.mark.parametrize("chunk_size", (-1, 137))
    def test_an_empty_file_can_be_read_and_seeked(self, chunk_size):
        """A table that declares no chunks states no chunk sizes: there is no
        chunk for a seek to find, and nothing at chunk_totals[1] for the
        decoder to take a first chunk size from. Both are files the writer
        produces itself -- no points, with the boundaries left to the caller
        or fixed."""
        written = self.written([], chunk_size)
        assert written.number_chunks == 0
        with Reader(io.BytesIO(written.data)) as reader:
            assert reader.num_points == 0
            reader.seek(0)
            assert list(reader) == []

    def test_closing_a_chunk_no_point_went_into_does_nothing(self, records):
        """So a caller may end every chunk itself without special-casing
        the first one, or two boundaries that fall together."""
        assert (self.written(records, -1, breaks=(0, 1, 1, 200)) ==
                self.written(records, -1, breaks=(1, 200)))

    def test_a_fixed_size_chunk_cannot_be_closed_early(self, records):
        with pytest.raises(LazError):
            self.written(records, 137, breaks=(50,))

    def test_a_non_seekable_output_appends_the_chunk_table_offset(
            self, records):
        """With nowhere to patch, the offset in front of the first chunk is -1
        and the real one goes at the very end -- which is what the reader
        already knows to look for."""
        written = self.written(records, 137, seekable=False)
        seekable = self.written(records, 137)
        start = load(self.NAME).header["offset_to_point_data"]

        assert struct.unpack_from("<q", written.data, start)[0] == -1
        table_start = struct.unpack_from(
            "<q", written.data, len(written.data) - 8)[0]
        assert table_start == struct.unpack_from("<q", seekable.data, start)[0]

        # the two differ in nothing else: same points, same table, same place
        assert written.data[:start] == seekable.data[:start]
        assert written.data[start + 8:-8] == seekable.data[start + 8:]

    def test_a_non_seekable_output_reads_back(self, records, expected):
        written = self.written(records, 137, seekable=False)
        with Reader(io.BytesIO(written.data)) as reader:
            assert reader.checksum() == expected


class TestPointWriterErrors:

    def items(self):
        return load("pt1_v2.laz").items

    def writer(self, items=None, compressor=Compressor.POINTWISE_CHUNKED):
        return cpylaz.PointWriter(io.BytesIO(),
                                  self.items() if items is None else items,
                                  compressor)

    def test_rejects_a_record_of_the_wrong_size(self):
        with pytest.raises(ValueError):
            self.writer().write(b"\x00" * 3)

    def test_rejects_an_unknown_item_version(self):
        with pytest.raises(LazError):
            self.writer([(t, size, 7) for t, size, _ in self.items()])

    def test_rejects_raw_items_in_a_compressed_container(self):
        """Version 0 means the item is stored uncompressed, which no compressed
        container has a writer for."""
        items = list(self.items())
        items[0] = (items[0][0], items[0][1], 0)
        with pytest.raises(LazError):
            self.writer(items)

    def test_rejects_no_items(self):
        with pytest.raises(ValueError):
            self.writer([])

    def test_writes_nothing_for_no_points(self):
        fp = io.BytesIO()
        cpylaz.PointWriter(fp, self.items(), Compressor.NONE).done()
        assert fp.getvalue() == b""

    def test_an_empty_chunked_file_still_gets_a_chunk_table(self):
        """Eight bytes of offset, then a table of no chunks."""
        fp = io.BytesIO()
        writer = cpylaz.PointWriter(fp, self.items(),
                                    Compressor.POINTWISE_CHUNKED)
        writer.done()
        assert fp.getvalue() == struct.pack("<qII", 8, 0, 0)

    def test_a_finished_writer_takes_nothing_more(self):
        writer = self.writer()
        writer.done()
        for call in (lambda: writer.write(b"\x00" * 28), writer.chunk,
                     writer.done):
            with pytest.raises(ValueError):
                call()

    def test_a_failing_sink_propagates_its_own_exception(self):
        """Neither swallowed nor relabelled a LazError: the file object's own
        error is what the caller has to see.

        The sink buffers, so a file this short only reaches it when the writer
        is closed -- which is exactly why done() has to be able to fail.
        """
        class Broken(io.BytesIO):
            def write(self, data):
                raise OSError("disk is on fire")

        items = [(t, size, 0) for t, size, _ in self.items()]
        writer = cpylaz.PointWriter(Broken(), items, Compressor.NONE)
        writer.write(las_records("pt1_v0.las")[0])
        with pytest.raises(OSError, match="disk is on fire"):
            writer.done()


# ---------------------------------------------------------------------------
# More than one BYTE item.
#
# lazpy's own layouts carry at most one -- items_for_point_format appends a
# single trailing BYTE for whatever a record has past its point format -- but a
# LASzip VLR is free to declare several, and a reader has to take the layout
# the file gives it. Each one needs its own start within the caller's
# extra-bytes buffer; they used to share offset 0 and overwrite each other, in
# both directions, which decoded silently to the wrong bytes.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("compressor,version", [
    (Compressor.NONE, 0),
    (Compressor.POINTWISE, 1),
    (Compressor.POINTWISE_CHUNKED, 2),
])
def test_two_byte_items_keep_their_own_bytes(compressor, version):
    """Two BYTE items of different widths, round-tripped.

    The widths differ so that aliasing shows up as wrong bytes rather than as
    a coincidence: at a shared offset the wider item overwrites the narrower,
    and what comes back is the tail of one padded with zeros.
    """
    items = [(int(ItemType.POINT10), 20, version),
             (int(ItemType.BYTE), 3, version),
             (int(ItemType.BYTE), 5, version)]
    record = bytes(range(20)) + b"ABC" + b"VWXYZ"

    fp = io.BytesIO()
    writer = cpylaz.PointWriter(fp, items, int(compressor))
    writer.write(record)
    writer.write(record)
    writer.done()

    fp.seek(0)
    reader = cpylaz.PointReader(fp, items, int(compressor))
    for _ in range(2):
        assert bytes(reader.read().extra_bytes) == b"ABCVWXYZ"


def test_the_extra_bytes_buffer_is_the_sum_of_the_byte_items():
    """Which is what gives each of them somewhere separate to go."""
    items = [(int(ItemType.POINT10), 20, 0),
             (int(ItemType.BYTE), 3, 0),
             (int(ItemType.BYTE), 5, 0)]
    reader = cpylaz.PointReader(io.BytesIO(), items, int(Compressor.NONE))
    assert reader.num_extra_bytes == 8
