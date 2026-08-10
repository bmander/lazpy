"""Read and write LAS and LAZ point cloud files.

Header and variable-length-record parsing and construction happen here;
everything from the first point onward is handled by the ``lazpy._cpylaz`` C
extension, which is a port of LASzip.

    >>> reader = Reader("cloud.laz")
    >>> reader.num_points
    43271750
    >>> for point in reader:
    ...     print(point.X, point.Y, point.Z, point.classification)

    >>> with Writer("out.laz", point_format=1) as writer:
    ...     writer.write(Point(X=1, Y=2, Z=3, gps_time=4.0))

LAS file specification
    1.2: https://www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf
    1.4: https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf
"""

from collections.abc import Mapping
from enum import IntEnum
import io
import sys

from ._cpylaz import (PointReader, PointWriter, Point,  # noqa: F401 (re-exported)
                      LazError)
from ._utils import (unsigned_int, signed_int, u32_array, u64_array, double,
                     cstr, PACKERS)

__all__ = ["Reader", "Writer", "Point", "Compressor", "Coder", "ItemType",
           "Selective", "LazError", "UnsupportedFileError",
           "ExtendedVariableLengthRecord"]

LASZIP_VLR_RECORD_ID = 22204
LASZIP_VLR_USER_ID = b"laszip encoded"


# LazError is defined in the C extension so decode failures raised from C and
# header failures raised from Python are one catchable category.


class UnsupportedFileError(LazError):
    """Raised for a well-formed file whose encoding lazpy cannot handle."""


class Compressor(IntEnum):
    NONE = 0
    POINTWISE = 1
    POINTWISE_CHUNKED = 2
    LAYERED_CHUNKED = 3


class Coder(IntEnum):
    ARITHMETIC = 0


class Selective(IntEnum):
    """Attributes to decode, for the layered LAS 1.4 point formats.

    These map to the byte layers of a v3/v4 chunk. Skipping a layer avoids its
    entropy decoding entirely, so reading only XY out of a format-6 file is
    substantially faster than reading everything.
    """
    CHANNEL_RETURNS_XY = 0x00000000   # always decoded
    Z = 0x00000001
    CLASSIFICATION = 0x00000002
    FLAGS = 0x00000004
    INTENSITY = 0x00000008
    SCAN_ANGLE = 0x00000010
    USER_DATA = 0x00000020
    POINT_SOURCE = 0x00000040
    GPS_TIME = 0x00000080
    RGB = 0x00000100
    NIR = 0x00000200
    WAVEPACKET = 0x00000400
    BYTE0 = 0x00010000
    EXTRA_BYTES = 0xFFFF0000
    ALL = 0xFFFFFFFF


class ItemType(IntEnum):
    BYTE = 0
    SHORT = 1
    INT = 2
    LONG = 3
    FLOAT = 4
    DOUBLE = 5
    POINT10 = 6
    GPSTIME11 = 7
    RGB12 = 8
    WAVEPACKET13 = 9
    POINT14 = 10
    RGB14 = 11
    RGBNIR14 = 12
    WAVEPACKET14 = 13
    BYTE14 = 14


# Point data record sizes for formats 0-10, and which optional items each has.
# Mirrors LASzip::setup(); anything beyond these sizes is extra bytes.
_POINT_FORMATS = {
    #  (base_size, gps_time, rgb,   nir,   wavepacket, point14)
    0:  (20,       False,    False, False, False,      False),
    1:  (28,       True,     False, False, False,      False),
    2:  (26,       False,    True,  False, False,      False),
    3:  (34,       True,     True,  False, False,      False),
    4:  (57,       True,     False, False, True,       False),
    5:  (63,       True,     True,  False, True,       False),
    6:  (30,       False,    False, False, False,      True),
    7:  (36,       False,    True,  False, False,      True),
    8:  (38,       False,    True,  True,  False,      True),
    9:  (59,       False,    False, False, True,       True),
    10: (67,       False,    True,  True,  True,       True),
}


def items_for_point_format(point_format, point_size):
    """Derive the LASzip item layout of an *uncompressed* point record.

    Compressed files carry this list explicitly in the LASzip VLR; for plain
    LAS it has to be reconstructed from the point format and record length,
    with any surplus bytes becoming a trailing BYTE item.

    Returns a list of ``(type, size, version)`` triples with version 0.
    """
    try:
        base, gps, rgb, nir, wave, point14 = _POINT_FORMATS[point_format]
    except KeyError:
        raise UnsupportedFileError(
            f"unknown point data format {point_format}") from None

    extra = point_size - base
    if extra < 0:
        raise LazError(
            f"point size {point_size} is {-extra} bytes too small "
            f"for point data format {point_format}")

    items = [(ItemType.POINT14 if point14 else ItemType.POINT10,
              30 if point14 else 20, 0)]
    if gps:
        items.append((ItemType.GPSTIME11, 8, 0))
    if rgb:
        if point14:
            items.append((ItemType.RGBNIR14, 8, 0) if nir
                         else (ItemType.RGB14, 6, 0))
        else:
            items.append((ItemType.RGB12, 6, 0))
    if wave:
        items.append((ItemType.WAVEPACKET14 if point14 else ItemType.WAVEPACKET13,
                      29, 0))
    if extra:
        items.append((ItemType.BYTE14 if point14 else ItemType.BYTE, extra, 0))
    return items


def _versioned_items(items, version):
    """The same layout, encoded at a given LASzip item version.

    WAVEPACKET13 is the exception: it never got a version 2, so it stays at 1
    inside a v2 file -- which is how laszip's own VLR declares it.
    """
    return [(t, size, 1 if t == ItemType.WAVEPACKET13 else version)
            for t, size, _ in items]


def _min_version_minor(point_format):
    """The oldest LAS 1.x that can describe *point_format*.

    Wavepackets arrived in LAS 1.3 and the extended point types in 1.4. This is
    both what a writer defaults to and what it checks a caller against.
    """
    _, _, _, _, wavepacket, point14 = _POINT_FORMATS[point_format]
    return 4 if point14 else (3 if wavepacket else 2)


def _as_i32(value):
    """A chunk size as the LASzip VLR declares it: signed, so the U32_MAX that
    selects variable-size chunks is written as -1."""
    return value - 0x100000000 if value > 0x7FFFFFFF else value


# ---------------------------------------------------------------------------
# The on-disk layouts, each a table of (name, size, parser).
#
# One table serves both directions: a reader walks it with the parser named in
# it, a writer with that parser's inverse from _utils.PACKERS. A field's width
# and its place in the record are therefore stated once, for both.
# ---------------------------------------------------------------------------

HEADER_FORMAT_12 = (
    ('file_signature', 4, cstr),
    ('file_source_id', 2, unsigned_int),
    ('global_encoding', 2, unsigned_int),
    ('guid_data_1', 4, unsigned_int),
    ('guid_data_2', 2, unsigned_int),
    ('guid_data_3', 2, unsigned_int),
    ('guid_data_4', 8, cstr),
    ('version_major', 1, unsigned_int),
    ('version_minor', 1, unsigned_int),
    ('system_identifier', 32, cstr),
    ('generating_software', 32, cstr),
    ('file_creation_day', 2, unsigned_int),
    ('file_creation_year', 2, unsigned_int),
    ('header_size', 2, unsigned_int),
    ('offset_to_point_data', 4, unsigned_int),
    ('number_of_variable_length_records', 4, unsigned_int),
    ('point_data_format_id', 1, unsigned_int),
    ('point_data_record_length', 2, unsigned_int),
    ('number_of_point_records', 4, unsigned_int),
    ('number_of_points_by_return', 4 * 5, u32_array),
    ('x_scale_factor', 8, double),
    ('y_scale_factor', 8, double),
    ('z_scale_factor', 8, double),
    ('x_offset', 8, double),
    ('y_offset', 8, double),
    ('z_offset', 8, double),
    ('max_x', 8, double),
    ('min_x', 8, double),
    ('max_y', 8, double),
    ('min_y', 8, double),
    ('max_z', 8, double),
    ('min_z', 8, double),
)

HEADER_FORMAT_13 = (
    ('start_of_waveform_data_packet_record', 8, unsigned_int),
)

HEADER_FORMAT_14 = (
    ('start_of_first_extended_variable_length_record', 8, unsigned_int),
    ('number_of_extended_variable_length_records', 4, unsigned_int),
    ('extended_number_of_point_records', 8, unsigned_int),
    ('extended_number_of_points_by_return', 8 * 15, u64_array),
)


def header_formats(version_minor):
    """The tables a LAS 1.`version_minor` header is made of, in file order."""
    formats = [HEADER_FORMAT_12]
    if version_minor >= 3:
        formats.append(HEADER_FORMAT_13)
    if version_minor >= 4:
        formats.append(HEADER_FORMAT_14)
    return formats


VLR_HEADER_FORMAT = (
    ('reserved', 2, unsigned_int),
    ('user_id', 16, cstr),
    ('record_id', 2, unsigned_int),
    ('record_length_after_header', 2, unsigned_int),
    ('description', 32, cstr),
)

# An extended VLR differs from a VLR in one field: the payload length is eight
# bytes rather than two, which is the whole reason the record type exists. They
# live behind the point data, so a file can grow one without moving its points.
EVLR_HEADER_FORMAT = (
    ('reserved', 2, unsigned_int),
    ('user_id', 16, cstr),
    ('record_id', 2, unsigned_int),
    ('record_length_after_header', 8, unsigned_int),
    ('description', 32, cstr),
)

# The LASzip VLR's payload, followed by one triple per item in the layout.
#
# number_of_special_evlrs and offset_to_special_evlrs are parsed and then left
# alone. They address the extended records laszip writes for LAS 1.4
# compatibility mode, where the 1.4-only point fields are carried beside a 1.2
# point format; nothing can be done with them until that mode is read at all.
LASZIP_RECORD_FORMAT = (
    ('compressor', 2, unsigned_int),
    ('coder', 2, unsigned_int),
    ('version_major', 1, unsigned_int),
    ('version_minor', 1, unsigned_int),
    ('version_revision', 2, unsigned_int),
    ('options', 4, unsigned_int),
    ('chunk_size', 4, signed_int),
    ('number_of_special_evlrs', 8, signed_int),
    ('offset_to_special_evlrs', 8, signed_int),
    ('number_of_items', 2, unsigned_int),
)

LASZIP_ITEM_FORMAT = (
    ('type', 2, unsigned_int),
    ('size', 2, unsigned_int),
    ('version', 2, unsigned_int),
)


def format_size(fmt):
    """How many bytes a table occupies."""
    return sum(size for _, size, _ in fmt)


VLR_HEADER_SIZE = format_size(VLR_HEADER_FORMAT)
EVLR_HEADER_SIZE = format_size(EVLR_HEADER_FORMAT)


def unpack_format(fmt, data, offset=0):
    """Every field of `fmt` out of `data`, and where it ended."""
    record = {}
    for name, size, parse in fmt:
        record[name] = parse(data[offset:offset + size])
        offset += size
    return record, offset


def pack_format(fmt, values):
    """The bytes of `fmt`, from a dict holding its fields."""
    return b''.join(PACKERS[parse](values[name], size)
                    for name, size, parse in fmt)


def _can_seek(fp):
    """Whether seeks on `fp` can be expected to work.

    The same rule the C stream applies in file_is_seekable (src/laz_stream.c):
    an object that answers seekable() is taken at its word, since a pipe has a
    seek method that raises, and one that does not answer at all is believed.
    """
    return hasattr(fp, 'seek') and getattr(fp, 'seekable', lambda: True)()


class ExtendedVariableLengthRecord(Mapping):
    """One EVLR, whose payload is fetched the first time it is asked for.

    Reads like the dict a regular VLR is: every field of the record header is a
    key, plus ``offset_to_data`` -- where the payload begins in the file -- and
    ``data``, the payload itself.

    ``data`` is what is not a dict. An EVLR payload is allowed to be enormous,
    which is what its eight-byte length field is for -- a waveform data packet
    record can run to gigabytes -- so opening a file reads the 60-byte headers
    and no more, and the bytes are read only if something asks for them. That
    means ``data`` needs the reader still open, since the reader is what holds
    the file. Once read, the payload is kept, so it outlives the reader.
    """

    def __init__(self, fields, fp):
        self._fields = fields
        self._fp = fp
        self._data = None

    def __getitem__(self, key):
        if key != 'data':
            return self._fields[key]
        if self._data is None:
            self._data = self._read_data()
        return self._data

    def __iter__(self):
        return iter((*self._fields, 'data'))

    def __len__(self):
        return len(self._fields) + 1

    def __repr__(self):
        return (f"<EVLR {self['user_id']!r}/{self['record_id']} "
                f"{self['record_length_after_header']} bytes>")

    def _read_data(self):
        """The payload, read out from under whoever else is using the file.

        The point reader has owned the file handle since it was constructed and
        keeps its own buffer over it, so the position has to come back exactly
        where it was found; it advances the handle only by reading, so what
        tell() reports is where it believes it is.
        """
        length = self._fields['record_length_after_header']
        try:
            resume = self._fp.tell()
            try:
                self._fp.seek(self._fields['offset_to_data'])
                data = self._fp.read(length)
            finally:
                self._fp.seek(resume)
        except (OSError, ValueError) as exc:
            # a closed file object is a ValueError, a dead one an OSError
            raise LazError(f"cannot read the payload of {self!r}: the file is "
                           f"closed or unreadable") from exc
        if len(data) != length:
            raise LazError(f"{self!r} runs past the end of the file")
        return data


class Reader:
    """Sequential and random access to the points of a LAS or LAZ file."""

    def __init__(self, filename=None, decompress_selective=None):
        """Open *filename*, if given.

        ``decompress_selective`` is a bitmask of ``Selective`` flags naming the
        attributes worth decoding. It only has an effect on the layered LAS 1.4
        formats (6-10), where each attribute is a separately skippable byte
        layer; everything else always decodes in full. Attributes that are
        skipped keep the value they had in the first point of the chunk.
        """
        self.fp = None
        self.header = None
        self.laz_header = None
        self._reader = None
        self._owns_fp = False
        self._evlr_warning = None
        self.decompress_selective = (Selective.ALL if decompress_selective is None
                                     else int(decompress_selective))
        if filename is not None:
            self.open(filename)

    # -- construction ----------------------------------------------------

    def open(self, filename):
        """Open a file by path. Also accepts an already-open binary file."""
        if sys.byteorder != 'little':
            raise UnsupportedFileError("only little-endian hosts are supported")

        if hasattr(filename, 'read'):
            self.fp = filename
            self._owns_fp = False
        else:
            self.fp = open(filename, 'rb')
            self._owns_fp = True

        try:
            self._setup()
        except Exception:
            self.close()
            raise
        return self

    def _setup(self):
        self.header = self._read_las_header(self.fp)
        # before the point reader takes the file over, since this seeks past
        # the point data and back
        records, self._evlr_warning = self._read_evlrs(self.fp, self.header)
        self.header['extended_variable_length_records'] = records
        self.laz_header = self._find_laz_header(self.header)

        if self.laz_header is None:
            # plain LAS: reconstruct the item layout from the point format
            compressor = Compressor.NONE
            coder = Coder.ARITHMETIC
            chunk_size = 0
            items = items_for_point_format(
                self.header['point_data_format_id'],
                self.header['point_data_record_length'])
        else:
            compressor = self.laz_header['compressor']
            coder = self.laz_header['coder']
            chunk_size = self.laz_header['chunk_size']
            items = [(it['type'], it['size'], it['version'])
                     for it in self.laz_header['items']]

            if compressor == Compressor.NONE:
                raise LazError("LASzip VLR declares no compression")
            if coder != Coder.ARITHMETIC:
                raise UnsupportedFileError(f"unknown entropy coder {coder}")

            # the high bit of the format id flags compression; clear it
            self.header['point_data_format_id'] &= 0b01111111

            # a negative chunk size means adaptive (variable-size) chunks
            if chunk_size < 0:
                chunk_size = 0xFFFFFFFF

        self.items = items
        self._reader = PointReader(
            self.fp,
            items,
            int(compressor),
            coder=int(coder),
            chunk_size=int(chunk_size),
            start_offset=self.header['offset_to_point_data'],
            decompress_selective=self.decompress_selective,
        )
        # sized by the C core from the item layout, not recomputed here
        self.num_extra_bytes = self._reader.num_extra_bytes
        # cached so scale() does not do six dict lookups per point
        h = self.header
        self._scale_offset = (h['x_scale_factor'], h['y_scale_factor'],
                              h['z_scale_factor'], h['x_offset'],
                              h['y_offset'], h['z_offset'])

    def close(self):
        self._reader = None
        if self.fp is not None and self._owns_fp:
            self.fp.close()
        self.fp = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # -- header parsing --------------------------------------------------

    @staticmethod
    def _read_variable_length_record(fp):
        record, _ = unpack_format(VLR_HEADER_FORMAT, fp.read(VLR_HEADER_SIZE))
        record['data'] = fp.read(record['record_length_after_header'])
        return record

    @classmethod
    def _read_las_header(cls, fp):
        def read_into(header, fmt):
            fields, _ = unpack_format(fmt, fp.read(format_size(fmt)))
            header.update(fields)
            return format_size(fmt)

        header = {}
        bytes_read = read_into(header, HEADER_FORMAT_12)

        if header['file_signature'] != b'LASF':
            raise LazError("not a LAS file (bad file signature)")

        major, minor = header['version_major'], header['version_minor']
        # which further tables the file has, by its own account
        for fmt in (header_formats(minor)[1:] if major == 1 else ()):
            bytes_read += read_into(header, fmt)
        if major == 1 and minor >= 4:
            # LAS 1.4 zeroes the legacy count for the extended point formats
            if header['number_of_point_records'] == 0:
                header['number_of_point_records'] = \
                    header['extended_number_of_point_records']

        # anything between the known fields and header_size is user data
        user_data_size = header['header_size'] - bytes_read
        if user_data_size < 0:
            raise LazError(f"header_size {header['header_size']} is too small")
        header['user_data'] = fp.read(user_data_size)

        header['variable_length_records'] = {}
        for _ in range(header['number_of_variable_length_records']):
            vlr = cls._read_variable_length_record(fp)
            header['variable_length_records'][vlr['record_id']] = vlr

        return header

    @staticmethod
    def _read_evlrs(fp, header):
        """The extended records `header` points at, and a warning, as a pair.

        The extended records live behind the point data rather than in front of
        it, so reading them means going to the end of the file and coming back.
        Only their headers are read; see ExtendedVariableLengthRecord for why
        the payloads are not.

        They are keyed by ``(user_id, record_id)`` rather than by record id
        alone, which is what ``variable_length_records`` uses: LAS namespaces
        records by user id, and a bare id collides -- LASF_Spec reserves ids 0
        to 99 for waveform packet descriptors, so a file with two of them would
        keep one.

        A file that declares more records than it holds keeps the ones it does
        hold, and the shortfall is the warning; a malformed record behind the
        point data says nothing about the points themselves, so it is not worth
        refusing to open the file over.
        """
        records = {}
        # counted rather than len(records), so that two records that really do
        # share a key do not read as a truncated file
        found = 0

        if not (header['version_major'] == 1 and header['version_minor'] >= 4):
            return records, None
        declared = header['number_of_extended_variable_length_records']
        start = header['start_of_first_extended_variable_length_record']
        if not declared or not start:
            return records, None
        # a file that cannot seek cannot be read at all -- the point reader
        # says so, in better words than a failure here would
        if not _can_seek(fp):
            return records, None

        resume = fp.tell()
        try:
            fp.seek(0, io.SEEK_END)
            end_of_file = fp.tell()
            fp.seek(start)
            # `declared` is a U32 out of the header and worth no more trust
            # than that, but it needs no cap of its own: every pass consumes at
            # least these 60 bytes and never seeks backwards, so a count that
            # outruns the file stops at the first short read. The work a header
            # aimed at the wrong place can cause is bounded by what is behind
            # the offset, which is to say by the size of the file.
            for _ in range(declared):
                data = fp.read(EVLR_HEADER_SIZE)
                if len(data) < EVLR_HEADER_SIZE:
                    break
                fields, _ = unpack_format(EVLR_HEADER_FORMAT, data)
                fields['offset_to_data'] = fp.tell()
                # checked here rather than where the payload is read, so that a
                # record that is not all there is left out rather than handed
                # over and found short later
                length = fields['record_length_after_header']
                if fields['offset_to_data'] + length > end_of_file:
                    break
                key = (fields['user_id'], fields['record_id'])
                records[key] = ExtendedVariableLengthRecord(fields, fp)
                found += 1
                fp.seek(length, io.SEEK_CUR)     # past it, not through it
        finally:
            fp.seek(resume)

        if found < declared:
            return records, (f"file declares {declared} extended variable "
                             f"length records but holds {found}")
        return records, None

    @staticmethod
    def _parse_laszip_record(data):
        record, offset = unpack_format(LASZIP_RECORD_FORMAT, data)

        record['items'] = []
        for _ in range(record['number_of_items']):
            item, offset = unpack_format(LASZIP_ITEM_FORMAT, data, offset)
            record['items'].append(item)

        record['user_data'] = data[offset:]
        return record

    @staticmethod
    def _find_laz_header(header):
        """Return the parsed LASzip VLR, or None for an uncompressed file."""
        vlr = header['variable_length_records'].get(LASZIP_VLR_RECORD_ID)
        if vlr is None:
            return None
        return Reader._parse_laszip_record(vlr['data'])

    # -- properties ------------------------------------------------------

    @property
    def num_points(self):
        return self.header['number_of_point_records']

    @property
    def chunk_size(self):
        if self.laz_header is None:
            return 0
        return self.laz_header['chunk_size']

    @property
    def point_format(self):
        return self.header['point_data_format_id']

    @property
    def is_compressed(self):
        return self.laz_header is not None

    @property
    def scales(self):
        h = self.header
        return (h['x_scale_factor'], h['y_scale_factor'], h['z_scale_factor'])

    @property
    def offsets(self):
        return (self.header['x_offset'], self.header['y_offset'],
                self.header['z_offset'])

    @property
    def index(self):
        """Index of the next point to be read."""
        return self._reader.index

    @property
    def warning(self):
        """A non-fatal problem found while reading, or None.

        Set when the chunk table is missing or corrupt, which is recoverable
        but costs random access -- seeking then has to decode forward instead
        of jumping to a chunk -- and when the extended variable length records
        behind the point data are fewer than the header claims.

        The chunk table comes first when both are true: it is the one that
        affects reading points.
        """
        table = self._reader.warning if self._reader is not None else None
        return table or self._evlr_warning

    # -- reading ---------------------------------------------------------

    def read(self):
        """Decode the next point.

        The returned :class:`Point` is the reader's own buffer and is
        overwritten by the next ``read()``; call ``point.copy()`` to keep it.
        """
        return self._reader.read()

    def checksum(self, count=None):
        """Decode *count* points and return ``(fnv1a_hash, points_read)``.

        Defaults to every point remaining in the file. Hashes each decoded
        field entirely in C, which is what makes whole-file verification
        against a laszip reference practical at tens of millions of points.
        Advances the reader.
        """
        if count is None:
            count = self.num_points - self.index
        return self._reader.checksum(max(0, count))

    def seek(self, index):
        """Position the reader so the next ``read()`` returns point *index*."""
        if index < 0 or index > self.num_points:
            raise IndexError(f"point index {index} out of range")
        self._reader.seek(index)

    def scale(self, point):
        """Return the georeferenced (x, y, z) of *point* as floats."""
        sx, sy, sz, ox, oy, oz = self._scale_offset
        return (point.X * sx + ox, point.Y * sy + oy, point.Z * sz + oz)

    def points(self, start=0, count=None):
        """Yield points, one at a time, without copying.

        Each iteration yields the same object with new contents, so store
        ``point.copy()`` if points need to outlive the loop.
        """
        if start:
            self.seek(start)
        remaining = self.num_points - start if count is None else count
        read = self._reader.read      # hoisted: this loop runs per point
        for _ in range(remaining):
            yield read()

    def __iter__(self):
        return self.points(self.index)

    def __len__(self):
        return self.num_points


class Writer:
    """Write points to a LAS or LAZ file.

    The mirror of :class:`Reader`: the header and the LASzip VLR are built
    here, and everything from the first point onward is the C extension's.

        >>> with Writer("out.laz", point_format=6, scales=(0.01, 0.01, 0.01)) as w:
        ...     for point in points:
        ...         w.write(point)

    A point is a :class:`Point` -- one built with ``Point(X=..., ...)``, or one
    that came from a reader -- or the raw bytes of its record, which is what a
    file being converted already has.

    One wrinkle in the LAS 1.4 point formats is worth knowing when building
    points by hand: three of the four classification flags exist twice over. A
    record keeps synthetic, keypoint and withheld in the same four bits as
    overlap, but a decoded point splits them, and what goes back into a record
    is ``synthetic_flag``, ``keypoint_flag`` and ``withheld_flag`` -- only the
    overlap bit is taken from ``extended_classification_flags``. That is
    LASzip's rule, kept because matching it byte for byte is what makes these
    files the files laszip would have written.

    Three header fields are not knowable until the last point has been written:
    the point count, the counts by return number, and the bounding box. They
    are filled in by ``close()``, which is why the output has to be seekable.
    Everything else can be set through ``writer.header`` until then, so long as
    it does not change how long the header is.
    """

    #: LASzip's own version, which is what the LASzip VLR records: the encoding
    #: of the point block, not the software that produced it. lazpy names
    #: itself in the header's ``generating_software`` instead.
    LASZIP_VERSION = (3, 5, 1)

    def __init__(self, filename, point_format, *, scales=(0.01, 0.01, 0.01),
                 offsets=(0.0, 0.0, 0.0), compressed=None, laz_version=None,
                 chunk_size=50000, num_extra_bytes=0, version_minor=None,
                 system_identifier=b'', generating_software=None,
                 file_creation=(0, 0)):
        """Open *filename* for writing points of *point_format*.

        ``compressed`` defaults to LAZ unless the name ends in ``.las``.
        ``laz_version`` picks the LASzip item encoding: 1 or 2 for point
        formats 0-5, 3 or 4 for 6-10, defaulting to what laszip itself would
        choose. ``version_minor`` picks the LAS version, defaulting to the
        oldest one that can describe the point format.

        ``scales`` and ``offsets`` are how the integer coordinates of a point
        become georeferenced ones; they are recorded in the header and applied
        to nothing here, since points are written as they are given.
        """
        self.fp = None
        self.header = None
        self._writer = None
        self._closed = False
        self._owns_fp = False

        if sys.byteorder != 'little':
            raise UnsupportedFileError("only little-endian hosts are supported")
        if num_extra_bytes < 0:
            raise ValueError("num_extra_bytes cannot be negative")

        # raises for a point format there is no layout for, so everything
        # below can look one up
        record_length = _POINT_FORMATS.get(point_format, (0,))[0] + num_extra_bytes
        self.items = items_for_point_format(point_format, record_length)
        point14 = _POINT_FORMATS[point_format][5]

        if compressed is None:
            compressed = not str(filename).lower().endswith('.las')
        if version_minor is None:
            version_minor = _min_version_minor(point_format)
        self._check_version(point_format, version_minor)

        self.point_format = point_format
        self.num_extra_bytes = num_extra_bytes
        self.compressed = bool(compressed)
        if self.compressed:
            if laz_version is None:
                laz_version = 3 if point14 else 2      # laszip's own default
            self._check_laz_version(laz_version, point14)
            self.items = _versioned_items(self.items, laz_version)
            # the layered container exists for the LAS 1.4 items and only them
            self.compressor = (Compressor.LAYERED_CHUNKED if point14
                               else Compressor.POINTWISE_CHUNKED)
        else:
            if laz_version not in (None, 0):
                raise ValueError("an uncompressed file has no item version")
            laz_version = 0
            self.compressor = Compressor.NONE
        self.laz_version = laz_version
        self.chunk_size = chunk_size

        # built before the header, because the header records how far past
        # itself the points begin
        vlr = self._pack_laszip_vlr() if self.compressed else b''

        self._open(filename)
        try:
            self.header = self._build_header(
                record_length, version_minor, len(vlr), scales, offsets,
                system_identifier, generating_software, file_creation)
            self.fp.write(self._pack_header(self.header))
            self.fp.write(vlr)
            self._writer = PointWriter(self.fp, self.items,
                                       int(self.compressor),
                                       chunk_size=chunk_size)
        except Exception:
            self._close_file()
            raise

    # -- construction ----------------------------------------------------

    @staticmethod
    def _check_version(point_format, version_minor):
        if version_minor not in (2, 3, 4):
            raise UnsupportedFileError(
                f"lazpy writes LAS 1.2 to 1.4, not 1.{version_minor}")
        minimum = _min_version_minor(point_format)
        if version_minor < minimum:
            raise UnsupportedFileError(
                f"point data format {point_format} needs LAS 1.{minimum}")

    @staticmethod
    def _check_laz_version(laz_version, point14):
        """A pre-flight check, so an impossible request fails before a file is
        opened rather than in the item factory after the header is written."""
        allowed = (3, 4) if point14 else (1, 2)
        if laz_version not in allowed:
            raise UnsupportedFileError(
                f"LASzip item version {laz_version} is not one of "
                f"{allowed} for this point format")

    def _open(self, filename):
        if hasattr(filename, 'write'):
            self.fp = filename
            self._owns_fp = False
        else:
            self.fp = open(filename, 'wb')
            self._owns_fp = True
        # close() has to go back and fill in the counts and the bounding box
        if not _can_seek(self.fp):
            self._close_file()
            raise ValueError("writing needs a seekable file, because the point "
                             "count and bounding box are only known at the end")

    def _build_header(self, record_length, version_minor, vlr_size, scales,
                      offsets, system_identifier, generating_software,
                      file_creation):
        header_size = self._header_size(version_minor)
        day, year = file_creation

        header = {
            'file_signature': b'LASF',
            'file_source_id': 0,
            'global_encoding': 0,
            'guid_data_1': 0, 'guid_data_2': 0, 'guid_data_3': 0,
            'guid_data_4': b'',
            'version_major': 1,
            'version_minor': version_minor,
            'system_identifier': system_identifier,
            'generating_software': (b'lazpy' if generating_software is None
                                    else generating_software),
            'file_creation_day': day,
            'file_creation_year': year,
            'header_size': header_size,
            'offset_to_point_data': header_size + vlr_size,
            'number_of_variable_length_records': 1 if self.compressed else 0,
            # the high bit is what tells a reader the points are compressed
            'point_data_format_id': self.point_format | (0x80 if self.compressed
                                                         else 0),
            'point_data_record_length': record_length,
            'number_of_point_records': 0,
            'number_of_points_by_return': [0] * 5,
            'max_x': 0.0, 'min_x': 0.0,
            'max_y': 0.0, 'min_y': 0.0,
            'max_z': 0.0, 'min_z': 0.0,
        }
        for axis, scale, offset in zip('xyz', scales, offsets):
            header[f'{axis}_scale_factor'] = scale
            header[f'{axis}_offset'] = offset
        if version_minor >= 3:
            header['start_of_waveform_data_packet_record'] = 0
        if version_minor >= 4:
            header['start_of_first_extended_variable_length_record'] = 0
            header['number_of_extended_variable_length_records'] = 0
            header['extended_number_of_point_records'] = 0
            header['extended_number_of_points_by_return'] = [0] * 15
        return header

    @staticmethod
    def _header_size(version_minor):
        return sum(format_size(fmt) for fmt in header_formats(version_minor))

    @staticmethod
    def _pack_header(header):
        """The header bytes, from the tables Reader parses them with.

        A caller may set header fields until the file is closed, but not ones
        that change how long the header is -- that length is already in the
        file, in header_size and again in offset_to_point_data.
        """
        version_minor = header['version_minor']
        if Writer._header_size(version_minor) != header['header_size']:
            raise LazError(
                f"a LAS 1.{version_minor} header is "
                f"{Writer._header_size(version_minor)} bytes, but this file "
                f"declares {header['header_size']}")
        return b''.join(pack_format(fmt, header)
                        for fmt in header_formats(version_minor))

    def _pack_laszip_vlr(self):
        """The LASzip VLR, the inverse of Reader._parse_laszip_record."""
        major, minor, revision = self.LASZIP_VERSION
        record = pack_format(LASZIP_RECORD_FORMAT, {
            'compressor': self.compressor,
            'coder': Coder.ARITHMETIC,
            'version_major': major,
            'version_minor': minor,
            'version_revision': revision,
            'options': 0,
            'chunk_size': _as_i32(self.chunk_size),
            'number_of_special_evlrs': -1,      # none, as laszip writes it
            'offset_to_special_evlrs': -1,
            'number_of_items': len(self.items),
        })
        for item_type, size, version in self.items:
            record += pack_format(LASZIP_ITEM_FORMAT,
                                  {'type': item_type, 'size': size,
                                   'version': version})

        return pack_format(VLR_HEADER_FORMAT, {
            'reserved': 0,
            'user_id': LASZIP_VLR_USER_ID,
            'record_id': LASZIP_VLR_RECORD_ID,
            'record_length_after_header': len(record),
            'description': b'lazpy',
        }) + record

    # -- writing ---------------------------------------------------------

    def write(self, point):
        """Append one point, as a :class:`Point` or as its record bytes.

        Raises ValueError once the writer is closed -- the check is the point
        writer's, since this runs once per point and it has to make it anyway.
        """
        self._writer.write(point)

    def chunk(self):
        """Close the open chunk, for variable-size chunking.

        Only meaningful for a file opened with ``chunk_size=0xFFFFFFFF``, where
        the boundaries are the caller's to choose.
        """
        self._writer.chunk()

    def close(self):
        """Finish the point block and fill in the header fields that needed
        every point to be known. Idempotent."""
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.done()
            self._patch_header()
        finally:
            self._close_file()

    def _close_file(self):
        if self.fp is not None and self._owns_fp:
            self.fp.close()
        self.fp = None

    def _patch_header(self):
        """Rewrite the header with the counts and bounds the points implied.

        The whole header goes back rather than the individual fields: it is a
        few hundred bytes, and picking them out by offset is what the field
        tables exist to avoid.
        """
        count = self._writer.index
        by_return = self._writer.points_by_return
        header = self.header

        # LAS 1.4 keeps the real count in its own field and zeroes the legacy
        # one for the extended point formats, which is what Reader compensates
        # for when it reads a file back.
        legacy = self.point_format < 6 and count <= 0xFFFFFFFF
        header['number_of_point_records'] = count if legacy else 0
        header['number_of_points_by_return'] = (list(by_return[1:6]) if legacy
                                                else [0] * 5)
        if header['version_minor'] >= 4:
            header['extended_number_of_point_records'] = count
            header['extended_number_of_points_by_return'] = list(by_return[1:16])

        # A file with no points keeps the zero bounds _build_header set, which
        # is what laszip leaves in an empty file too.
        bounds = self._writer.bounds
        if bounds is not None:
            for i, axis in enumerate('xyz'):
                scale = header[f'{axis}_scale_factor']
                offset = header[f'{axis}_offset']
                header[f'min_{axis}'] = bounds[i] * scale + offset
                header[f'max_{axis}'] = bounds[i + 3] * scale + offset

        end = self.fp.tell()
        self.fp.seek(0)
        self.fp.write(self._pack_header(header))
        self.fp.seek(end)          # a file object the caller lent us

    @property
    def num_points(self):
        """How many points have been written."""
        return self._writer.index

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __len__(self):
        return self.num_points
