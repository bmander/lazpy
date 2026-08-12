import io
import os
import struct

import pytest

from lazpy import _cpylaz as cpylaz
from lazpy import Reader, LazError
from helpers import (REFERENCE_HASH, SURVIVABLE, TESTDATA, field_span,
                     load)


# ---------------------------------------------------------------------------
# Malformed input
#
# The reader is validated hard against well-formed files -- every point format
# and item version, byte-identical to laszip. This is the other side: a file
# that is wrong. Such a file may raise; it may not crash the interpreter, hang
# it, or hand back points decoded from bytes that were never there.
#
# tools/fuzz.py mutates the fixtures looking for cases that do. The ones it
# found are pinned below, each as the shape of file that provoked it rather
# than the mutated blob itself -- the blob is 15 KB of which four bytes matter,
# and a test that says which four is a test that says what went wrong.
# ---------------------------------------------------------------------------


def malformed(name):
    """One of the files tools/fuzz.py found, as a stream."""
    with open(os.path.join(TESTDATA, "malformed", name), "rb") as fh:
        return io.BytesIO(fh.read())


class TestMalformedHeaders:

    def doctored(self, name, field, value):
        """A fixture with one header field overwritten.

        The field is named rather than offset, and located through the same
        tables that define the header, so these cannot drift from it.
        """
        offset, size = field_span(field, load(name).header["version_minor"])
        data = bytearray(load(name).data)
        struct.pack_into({1: "<B", 2: "<H", 4: "<I", 8: "<Q"}[size],
                         data, offset, value)
        return io.BytesIO(bytes(data))

    def test_a_vlr_count_larger_than_the_file_is_refused(self):
        """Found by fuzzing: it used to hang.

        unpack_format slices, so reading a record past the end of the file
        produced a record of zeros rather than failing -- and the count is a
        u32, so a corrupt one is four billion of those before the loop ends.
        """
        with pytest.raises(LazError, match="ends inside a variable length"):
            Reader(self.doctored("pt1_v2.laz",
                                 "number_of_variable_length_records",
                                 0xFFFFFFFF))

    def test_a_vlr_longer_than_the_file_is_refused(self):
        f = load("pt1_v2.laz")
        data = bytearray(f.data)
        # the first record sits at header_size, and the length of its payload
        # is 20 bytes into its own header
        struct.pack_into("<H", data, f.header["header_size"] + 20, 0xFFFF)

        with pytest.raises(LazError, match="ends inside a variable length"):
            Reader(io.BytesIO(bytes(data)))

    def test_an_inflated_point_count_stops_at_the_end_of_the_file(self):
        """Rather than reading zeros for the points that are not there."""
        for name in ("pt1_v2.laz", "pt1_v0.las", "pt1_v1_pointwise.laz"):
            with self.doctored(name, "number_of_point_records",
                               100_000) as fp:
                with Reader(fp) as reader:
                    assert reader.num_points == 100_000
                    read = 0
                    with pytest.raises(LazError):
                        for _ in reader:
                            read += 1
                    # 501 rather than 500 for a chunked file: what sits
                    # behind the last chunk is the chunk table, and one
                    # more point decodes out of it before the stream runs
                    # out. What matters is that it stops there rather than
                    # at the 100,000 the header asked for.
                    assert 500 <= read <= 501, name

    def test_a_header_size_below_the_fields_it_declares_is_refused(self):
        with pytest.raises(LazError, match="too small"):
            Reader(self.doctored("pt1_v2.laz", "header_size", 32))


class TestMalformedLaszipVlr:
    """The fields of the LASzip record, which is what sizes every allocation.

    Driven through PointReader rather than by doctoring a file, because the
    record is what a PointReader takes as arguments: the same values, without
    a hundred lines of file surgery in between.
    """

    LEGACY_ITEMS = ((6, 20, 2), (7, 8, 2), (0, 6, 2))     # point10, gps, extra
    LAYERED_ITEMS = ((10, 30, 3), (12, 8, 3), (14, 6, 3))  # point14, rgbnir...

    def reader(self, items, compressor, **kw):
        f = load("pt1_v2.laz")
        reader = cpylaz.PointReader(
            io.BytesIO(f.data), items, compressor,
            start_offset=f.header["offset_to_point_data"], **kw)
        for _ in range(3):
            reader.read()
        return reader

    def test_too_many_items_is_refused_before_it_allocates(self):
        with pytest.raises(LazError, match="too many items"):
            self.reader([(0, 1, 0)] * 4000, 2, chunk_size=137)

    def test_no_items_is_refused(self):
        with pytest.raises(ValueError):
            self.reader([], 2, chunk_size=137)

    def test_an_unknown_item_type_is_refused(self):
        with pytest.raises(LazError, match="item type 99"):
            self.reader([(99, 20, 2)], 2, chunk_size=137)

    def test_an_unknown_item_version_is_refused(self):
        with pytest.raises(LazError, match="version 99"):
            self.reader([(6, 20, 99)], 2, chunk_size=137)

    def test_an_unknown_compressor_is_refused(self):
        with pytest.raises(LazError, match="compressor 99"):
            self.reader(self.LEGACY_ITEMS, 99, chunk_size=137)

    @pytest.mark.parametrize("items,compressor", [
        # a layered item in a flat container: its layer sizes are never read,
        # so its decoders would start on empty streams and divide by zero
        (LAYERED_ITEMS, 1),
        (LAYERED_ITEMS, 2),
        # a flat item in a layered one: it has no chunk_sizes to call
        (LEGACY_ITEMS, 3),
    ])
    def test_a_container_that_disagrees_with_its_items_is_refused(
            self, items, compressor):
        """Found by fuzzing: the first of these crashed with SIGFPE.

        The compressor and the item versions are separate fields of the same
        record, so a file can declare a pair that cannot be read together.
        """
        with pytest.raises(LazError, match="cannot be read from compressor"):
            self.reader(list(items), compressor, chunk_size=137)

    def test_a_zero_chunk_size_does_not_decode_forever(self):
        with pytest.raises(LazError):
            self.reader(list(self.LEGACY_ITEMS), 2, chunk_size=0)


class TestMalformedChunks:

    def test_absurd_layer_sizes_are_refused_before_they_allocate(self):
        """Found by fuzzing: this file asked for 11 GB, and got it.

        Each layer's byte count is a u32 straight out of the chunk header,
        and they are summed and handed to realloc -- so a corrupt header is
        an allocation of whatever it says, which for this file was eleven
        gigabytes and nineteen seconds before it failed for an unrelated
        reason. The layers of one chunk cannot outweigh what is left in the
        file, and now they are not allowed to claim to.
        """
        with pytest.raises(LazError, match="layer sizes"):
            with Reader(malformed("layer_sizes_11gb.laz")) as reader:
                for _ in range(reader.num_points):
                    reader.read()

    def test_a_chunk_table_claiming_eleven_gigabytes(self):
        """The chunk table's own count, rather than the layer sizes inside a
        chunk: it is a u32 out of the file and two arrays are sized from it,
        so a corrupt one asks for eight bytes per chunk of whatever it says.
        A chunk takes at least a byte of the point data it describes, so
        there cannot be more chunks than there are bytes between the first
        one and the table -- which is what this file is now refused by,
        before anything is allocated for it.
        """
        with pytest.raises(LazError, match="corrupt"):
            with Reader(malformed("chunk_table_11gb.laz")) as reader:
                for _ in range(reader.num_points):
                    reader.read()

    def test_a_layer_of_no_bytes_at_all(self):
        """A layered chunk whose layer is declared empty. Its decoder starts
        with an interval length of zero, and the first decode divides by it
        -- a SIGFPE, which takes the process rather than raising. The decoder
        starts at U32_MAX instead now, so an empty layer runs out of stream
        and is reported as the corrupt chunk it is.
        """
        with pytest.raises(LazError, match="corrupt"):
            with Reader(malformed("empty_layer_sigfpe.laz")) as reader:
                for _ in range(reader.num_points):
                    reader.read()

    def test_a_seek_the_file_object_refuses_is_reported_as_itself(self):
        """Found by fuzzing, as a SystemError from an unrelated place.

        This file's chunk table position is negative, so seeking to it
        raises ValueError. The stream records that and leaves the exception
        set for the binding to find -- the convention the write side already
        followed -- but nothing on the read path looked. The decode carried
        on over zeros and returned a point with the exception still
        pending, which the interpreter reported as "SystemError: read()
        returned a result with an exception set".
        """
        with pytest.raises(ValueError, match="negative seek"):
            with Reader(malformed("seek_past_end.laz")) as reader:
                for _ in range(reader.num_points):
                    reader.read()


class TestMalformedCorpus:
    """Every file tools/fuzz.py has found, kept as a regression case.

    The assertion is only that reading does not take the process down: what
    each one *should* raise is pinned by a named test above, where the fault
    can be stated in one line instead of inferred from 15 KB of noise.
    """

    @pytest.mark.parametrize(
        "name", sorted(os.listdir(os.path.join(TESTDATA, "malformed"))))
    def test_reading_it_raises_rather_than_crashing(self, name):
        try:
            with Reader(malformed(name)) as reader:
                for _ in range(min(reader.num_points, 2000)):
                    reader.read()
                reader.seek(0)
                reader.checksum(min(reader.num_points, 500))
                list(reader.points_within(1490, 1690, 1510, 1710))
        except SURVIVABLE:
            pass


class TestStreamRefill:
    """The two ways a file object can answer a refill, and one way it must not.

    The decoder fills its 64 KB buffer with readinto where there is one and
    read where there is not, and neither may be taken at its word about how
    much it produced -- the buffer is a fixed allocation.
    """

    def read_all(self, fp):
        with Reader(fp) as reader:
            return reader.checksum()

    def test_readinto_and_read_decode_the_same_file(self):
        name = "pt1_v2.laz"
        data = load(name).data

        class NoReadinto(io.BytesIO):
            """A file object of the older kind, with only read()."""
            readinto = None

        assert self.read_all(io.BytesIO(data)) == REFERENCE_HASH[name]
        assert self.read_all(NoReadinto(data)) == REFERENCE_HASH[name]

    def test_a_read_returning_more_than_it_was_asked_for_is_refused(self):
        """It would otherwise be memcpy'd into a fixed 64 KB buffer."""
        class Overfull(io.BytesIO):
            readinto = None

            def read(self, size=-1):
                return b"\x00" * (size + 1024) if size > 0 else b""

        with pytest.raises((LazError, ValueError)):
            self.read_all(Overfull(load("pt1_v2.laz").data))

    def test_a_readinto_claiming_more_than_the_buffer_is_refused(self):
        class Liar(io.BytesIO):
            def readinto(self, buffer):
                super().readinto(buffer)
                return len(buffer) + 1024

        with pytest.raises((LazError, ValueError)):
            self.read_all(Liar(load("pt1_v2.laz").data))
