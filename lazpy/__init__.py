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

from enum import IntEnum
import sys

from ._cpylaz import (PointReader, PointWriter, Point,  # noqa: F401 (re-exported)
                      LazError)
from ._utils import (unsigned_int, signed_int, u32_array, u64_array, double,
                     cstr, pack_unsigned_int, pack_signed_int, pack_cstr,
                     PACKERS)

__all__ = ["Reader", "Writer", "Point", "Compressor", "Coder", "ItemType",
           "Selective", "LazError", "UnsupportedFileError"]

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


def _as_i32(value):
    """A chunk size as the LASzip VLR stores it: signed, so the U32_MAX that
    selects variable-size chunks is written as -1."""
    return value - 0x100000000 if value > 0x7FFFFFFF else value


class Reader:
    """Sequential and random access to the points of a LAS or LAZ file."""

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
        record = {}
        record['reserved'] = unsigned_int(fp.read(2))
        record['user_id'] = cstr(fp.read(16))
        record['record_id'] = unsigned_int(fp.read(2))
        record['record_length_after_header'] = unsigned_int(fp.read(2))
        record['description'] = cstr(fp.read(32))
        record['data'] = fp.read(record['record_length_after_header'])
        return record

    @classmethod
    def _read_las_header(cls, fp):
        def read_into(header, fmt):
            for name, size, func in fmt:
                header[name] = func(fp.read(size))
            return sum(size for _, size, _ in fmt)

        header = {}
        bytes_read = read_into(header, cls.HEADER_FORMAT_12)

        if header['file_signature'] != b'LASF':
            raise LazError("not a LAS file (bad file signature)")

        major, minor = header['version_major'], header['version_minor']
        if major == 1 and minor >= 3:
            bytes_read += read_into(header, cls.HEADER_FORMAT_13)
        if major == 1 and minor >= 4:
            bytes_read += read_into(header, cls.HEADER_FORMAT_14)
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
    def _parse_laszip_record(data):
        fmt = (
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

        record = {}
        offset = 0
        for name, size, func in fmt:
            record[name] = func(data[offset:offset + size])
            offset += size

        record['items'] = []
        for _ in range(record['number_of_items']):
            record['items'].append({
                'type': unsigned_int(data[offset:offset + 2]),
                'size': unsigned_int(data[offset + 2:offset + 4]),
                'version': unsigned_int(data[offset + 4:offset + 6]),
            })
            offset += 6

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
        but costs random access: seeking then has to decode forward instead of
        jumping to a chunk.
        """
        return self._reader.warning

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

    #: LASzip's default item version per point format, from
    #: ``LASzip::get_default_version``.
    DEFAULT_LAZ_VERSION = {False: 2, True: 3}      # keyed by "is a 1.4 format"

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
        if point_format not in _POINT_FORMATS:
            raise UnsupportedFileError(f"unknown point data format {point_format}")

        base_size, _, _, _, wavepacket, point14 = _POINT_FORMATS[point_format]
        if num_extra_bytes < 0:
            raise ValueError("num_extra_bytes cannot be negative")

        if compressed is None:
            compressed = not str(filename).lower().endswith('.las')
        if version_minor is None:
            version_minor = 4 if point14 else (3 if wavepacket else 2)
        self._check_version(point_format, version_minor, point14, wavepacket)

        self.point_format = point_format
        self.num_extra_bytes = num_extra_bytes
        self.compressed = bool(compressed)
        self.items = items_for_point_format(point_format,
                                            base_size + num_extra_bytes)
        if self.compressed:
            if laz_version is None:
                laz_version = self.DEFAULT_LAZ_VERSION[point14]
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

        self._open(filename)
        try:
            self.header = self._build_header(
                base_size + num_extra_bytes, version_minor, scales, offsets,
                system_identifier, generating_software, file_creation)
            self.fp.write(self._pack_header(self.header))
            if self.compressed:
                self.fp.write(self._pack_laszip_vlr())
            self._writer = PointWriter(self.fp, self.items,
                                       int(self.compressor),
                                       chunk_size=chunk_size)
        except Exception:
            self._close_file()
            raise

    # -- construction ----------------------------------------------------

    @staticmethod
    def _check_version(point_format, version_minor, point14, wavepacket):
        if version_minor not in (2, 3, 4):
            raise UnsupportedFileError(
                f"lazpy writes LAS 1.2 to 1.4, not 1.{version_minor}")
        if point14 and version_minor < 4:
            raise UnsupportedFileError(
                f"point data format {point_format} needs LAS 1.4")
        if wavepacket and version_minor < 3:
            raise UnsupportedFileError(
                f"point data format {point_format} needs LAS 1.3")

    @staticmethod
    def _check_laz_version(laz_version, point14):
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
        if not (hasattr(self.fp, 'seek') and
                getattr(self.fp, 'seekable', lambda: True)()):
            self._close_file()
            raise ValueError("writing needs a seekable file, because the point "
                             "count and bounding box are only known at the end")

    def _build_header(self, record_length, version_minor, scales, offsets,
                      system_identifier, generating_software, file_creation):
        header_size = self._header_size(version_minor)
        vlr_size = 54 + self._laszip_vlr_size() if self.compressed else 0
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

    @classmethod
    def _header_formats(cls, version_minor):
        formats = [Reader.HEADER_FORMAT_12]
        if version_minor >= 3:
            formats.append(Reader.HEADER_FORMAT_13)
        if version_minor >= 4:
            formats.append(Reader.HEADER_FORMAT_14)
        return formats

    @classmethod
    def _header_size(cls, version_minor):
        return sum(size
                   for fmt in cls._header_formats(version_minor)
                   for _, size, _ in fmt)

    def _pack_header(self, header):
        """The header bytes, from the same field tables Reader parses with."""
        out = bytearray()
        for fmt in self._header_formats(header['version_minor']):
            for name, size, parse in fmt:
                out += PACKERS[parse](header[name], size)
        return bytes(out)

    def _laszip_vlr_size(self):
        return 34 + 6 * len(self.items)

    def _pack_laszip_vlr(self):
        """The LASzip VLR, the inverse of Reader._parse_laszip_record."""
        major, minor, revision = self.LASZIP_VERSION
        record = bytearray()
        record += pack_unsigned_int(self.compressor, 2)
        record += pack_unsigned_int(Coder.ARITHMETIC, 2)
        record += pack_unsigned_int(major, 1)
        record += pack_unsigned_int(minor, 1)
        record += pack_unsigned_int(revision, 2)
        record += pack_unsigned_int(0, 4)                    # options
        # signed, because -1 there is what selects variable-size chunks
        record += pack_signed_int(_as_i32(self.chunk_size), 4)
        record += pack_signed_int(-1, 8)                     # special EVLRs:
        record += pack_signed_int(-1, 8)                     # none, as laszip
        record += pack_unsigned_int(len(self.items), 2)
        for item_type, size, version in self.items:
            record += pack_unsigned_int(item_type, 2)
            record += pack_unsigned_int(size, 2)
            record += pack_unsigned_int(version, 2)

        header = bytearray()
        header += pack_unsigned_int(0, 2)                    # reserved
        header += pack_cstr(LASZIP_VLR_USER_ID, 16)
        header += pack_unsigned_int(LASZIP_VLR_RECORD_ID, 2)
        header += pack_unsigned_int(len(record), 2)
        header += pack_cstr(b'lazpy', 32)                    # description
        return bytes(header + record)

    # -- writing ---------------------------------------------------------

    def write(self, point):
        """Append one point, as a :class:`Point` or as its record bytes."""
        self._check_open()
        self._writer.write(point)

    def chunk(self):
        """Close the open chunk, for variable-size chunking.

        Only meaningful for a file opened with ``chunk_size=0xFFFFFFFF``, where
        the boundaries are the caller's to choose.
        """
        self._check_open()
        self._writer.chunk()

    def _check_open(self):
        if self._closed or self._writer is None:
            raise ValueError("writer is closed")

    def close(self):
        """Finish the point block and fill in the header fields that needed
        every point to be known. Idempotent."""
        if self._closed or self._writer is None:
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
        return 0 if self._writer is None else self._writer.index

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __len__(self):
        return self.num_points
