"""The reading front end: :class:`Reader` and what only it needs."""

from collections import namedtuple
from collections.abc import Mapping
import io
import os

from ._cpylaz import PointReader, SpatialIndex, LazError
from .formats import (LASZIP_VLR_KEY, LASINDEX_EVLR_KEY, Compressor, Coder,
                      Chunking, Selective, UnsupportedFileError,
                      items_for_point_format, _POINT_FORMATS)
from .headers import (HEADER_FORMAT_12, VLR_HEADER_FORMAT, VLR_HEADER_SIZE,
                      EVLR_HEADER_FORMAT, EVLR_HEADER_SIZE,
                      LASZIP_RECORD_FORMAT, LASZIP_ITEM_FORMAT,
                      header_formats, format_size, unpack_format, _can_seek)
from .compat import _compatibility_layout, _upgrade_to_las_14

# ---------------------------------------------------------------------------
# The array API.
#
# Every field of the decoded point, as the C reader has to be told about it:
# the byte offset it lives at, the numpy type it lands in, and how many of
# those per point. LAS packs several fields into part of a byte and numpy has
# no bitfield type, so those fields carry the `shift` and `mask` that unpack
# their containing byte, which is cheaper than teaching the decode loop about
# bit layouts -- and the ones sharing a byte share one read of it.
#
# Offsets are into LazPoint (src/laz_types.h), which mirrors LASzip's own
# point struct; -1 means the extra bytes instead. Types are spelled in native
# order ('='), because read_into copies the decoded point's bytes straight
# through and a decoded point is in host order -- see the byte-order note on
# LazPoint. The sub-byte shifts are host-independent: LazPoint packs those
# bytes by hand rather than leaving them to the compiler, for exactly this
# reason.
#
# This restates a layout that C owns, so it is pinned from the outside:
# test_arrays_match_reading_point_by_point compares every column of every
# fixture against Point's own getters, which are themselves pinned to laszip.
# ---------------------------------------------------------------------------

_Field = namedtuple("_Field", "offset dtype width shift mask",
                    defaults=(1, 0, None))

_ARRAY_FIELDS = {
    'X': _Field(0, '=i4'),
    'Y': _Field(4, '=i4'),
    'Z': _Field(8, '=i4'),
    'intensity': _Field(12, '=u2'),
    'return_number': _Field(14, 'u1', mask=0x07),
    'number_of_returns': _Field(14, 'u1', shift=3, mask=0x07),
    'scan_direction_flag': _Field(14, 'u1', shift=6, mask=0x01),
    'edge_of_flight_line': _Field(14, 'u1', shift=7, mask=0x01),
    'classification': _Field(15, 'u1', mask=0x1F),
    'synthetic_flag': _Field(15, 'u1', shift=5, mask=0x01),
    'keypoint_flag': _Field(15, 'u1', shift=6, mask=0x01),
    'withheld_flag': _Field(15, 'u1', shift=7, mask=0x01),
    'scan_angle_rank': _Field(16, 'i1'),
    'user_data': _Field(17, 'u1'),
    'point_source_ID': _Field(18, '=u2'),
    'extended_scan_angle': _Field(20, '=i2'),
    'extended_point_type': _Field(22, 'u1', mask=0x03),
    'extended_scanner_channel': _Field(22, 'u1', shift=2, mask=0x03),
    'extended_classification_flags': _Field(22, 'u1', shift=4, mask=0x0F),
    'extended_classification': _Field(23, 'u1'),
    'extended_return_number': _Field(24, 'u1', mask=0x0F),
    'extended_number_of_returns': _Field(24, 'u1', shift=4, mask=0x0F),
    'gps_time': _Field(32, '=f8'),
    'red': _Field(40, '=u2'),
    'green': _Field(42, '=u2'),
    'blue': _Field(44, '=u2'),
    'nir': _Field(46, '=u2'),
    # a wavepacket keeps its on-disk order in the point, so it is bytes here
    'wave_packet': _Field(48, 'u1', width=29),
}

# The fields every point format carries, in the order LAS lists them.
_CORE_FIELDS = ('X', 'Y', 'Z', 'intensity', 'return_number',
                'number_of_returns', 'scan_direction_flag',
                'edge_of_flight_line', 'classification', 'synthetic_flag',
                'keypoint_flag', 'withheld_flag', 'scan_angle_rank',
                'user_data', 'point_source_ID')

# What the extended point types add. The legacy fields above stay meaningful
# in formats 6-10: the POINT14 reader keeps them in step, saturating the
# narrower ones rather than wrapping.
_EXTENDED_FIELDS = ('extended_return_number', 'extended_number_of_returns',
                    'extended_classification', 'extended_classification_flags',
                    'extended_scanner_channel', 'extended_scan_angle',
                    'extended_point_type')


def _fields_for_point_format(point_format, num_extra_bytes):
    """The field names a point of this format actually carries."""
    try:
        _, gps, rgb, nir, wave, point14 = _POINT_FORMATS[point_format]
    except KeyError:
        raise UnsupportedFileError(
            f"unknown point data format {point_format}") from None
    names = list(_CORE_FIELDS)
    if point14:
        names += _EXTENDED_FIELDS
    if gps or point14:            # POINT14 carries its gps time inside itself
        names.append('gps_time')
    if rgb:
        names += ['red', 'green', 'blue']
    if nir:
        names.append('nir')
    if wave:
        names.append('wave_packet')
    if num_extra_bytes:
        names.append('extra_bytes')
    return names


def _numpy():
    """numpy, imported on use.

    The array API is the only part of lazpy that wants it and the extension
    has no dependencies of its own, so numpy stays optional rather than
    becoming the price of reading a file.
    """
    try:
        import numpy
    except ImportError:
        raise ImportError(
            "Reader.arrays() and Reader.xyz() need numpy; install it with "
            "`pip install lazpy[numpy]`") from None
    return numpy


class ExtendedVariableLengthRecord(Mapping):
    """One EVLR, whose payload is read the first time it is asked for.

    Reads like the dict a regular VLR is: every field of the record header is a
    key, plus ``offset_to_data`` -- where the payload begins in the file -- and
    ``data``, the payload itself.

    ``data`` is the one lazy key. An EVLR payload can be enormous -- a
    waveform data packet record can run to gigabytes -- so opening a file
    reads only the 60-byte headers, and the payload is read when first
    accessed. That means ``data`` needs the reader still open; once read, it
    is kept, and outlives the reader.
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
    """Read the points of a LAS or LAZ file: in order, by index, or as arrays.

    Open one by path or from an open binary file, then iterate it point by
    point, or reach for :meth:`seek`, :meth:`arrays` and
    :meth:`points_within`.

    ``reader.header`` is a dict of every LAS header field, plus the variable
    length records under ``header["variable_length_records"]``, keyed by
    ``(user_id, record_id)``. For a LAS 1.4 compatibility-mode file the
    header describes the LAS 1.4 file it stands in for, not the file on
    disk: ``header_size`` and ``offset_to_point_data`` in particular locate
    nothing in the physical file.
    """

    def __init__(self, filename=None, decompress_selective=None):
        """Open *filename*, if given.

        ``decompress_selective`` is a bitmask of ``Selective`` flags naming the
        attributes to decode. It only has an effect on the layered LAS 1.4
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
        self._path = None
        self._index = None
        self._index_looked_for = False
        self.decompress_selective = (
            Selective.ALL if decompress_selective is None
            else int(decompress_selective))
        if filename is not None:
            self.open(filename)

    # -- construction ----------------------------------------------------

    def open(self, filename):
        """Open a file by path. Also accepts an already-open binary file."""
        self._path = None
        self._index = None
        self._index_looked_for = False

        if hasattr(filename, 'read'):
            self.fp = filename
            self._owns_fp = False
        else:
            self.fp = open(filename, 'rb')
            self._owns_fp = True
            # remembered for the sidecar ".lax", which is found by name
            self._path = os.fspath(filename)

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

        # where the points really begin, before the upgrade below moves the
        # header's copy on to where a LAS 1.4 header would have ended
        point_data_offset = self.header['offset_to_point_data']
        compatibility = _compatibility_layout(self.header)

        self.items = items
        self._reader = PointReader(
            self.fp,
            items,
            int(compressor),
            coder=int(coder),
            chunk_size=int(chunk_size),
            start_offset=point_data_offset,
            decompress_selective=self.decompress_selective,
            compatibility=compatibility,
        )
        # sized by the C core from the item layout, not recomputed here; in
        # compatibility mode it is what the layout leaves once the hidden LAS
        # 1.4 fields are taken back out
        self.num_extra_bytes = self._reader.num_extra_bytes
        if compatibility is not None:
            _upgrade_to_las_14(self.header, compatibility,
                               self.num_extra_bytes)
        # cached so scale() does not do six dict lookups per point
        h = self.header
        self._scale_offset = (h['x_scale_factor'], h['y_scale_factor'],
                              h['z_scale_factor'], h['x_offset'],
                              h['y_offset'], h['z_offset'])

    def close(self):
        """Release the point reader, and the file if this reader opened it.

        A file object handed in is left open.
        """
        self._reader = None
        # dropped rather than left to be looked for: finding an index means
        # reading the file, and there is no file any more
        self._index = None
        self._index_looked_for = True
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
        # Short reads are checked rather than left to unpack_format, which
        # slices and so would turn the end of the file into a record of
        # zeros. A header that declares 4 billion records -- the count is a
        # u32, and a corrupt one reads as 0xFFFFFFFF -- would otherwise be
        # four billion of those before the loop ended.
        data = fp.read(VLR_HEADER_SIZE)
        if len(data) < VLR_HEADER_SIZE:
            raise LazError("file ends inside a variable length record")
        record, _ = unpack_format(VLR_HEADER_FORMAT, data)
        record['data'] = fp.read(record['record_length_after_header'])
        if len(record['data']) < record['record_length_after_header']:
            raise LazError("file ends inside a variable length record")
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

        # keyed by (user_id, record_id), as the extended records are; see
        # LASZIP_VLR_KEY for why the id alone is not a key
        header['variable_length_records'] = {}
        for _ in range(header['number_of_variable_length_records']):
            vlr = cls._read_variable_length_record(fp)
            header['variable_length_records'][
                (vlr['user_id'], vlr['record_id'])] = vlr

        return header

    @staticmethod
    def _read_evlrs(fp, header):
        """The extended records `header` points at, and a warning, as a pair.

        The extended records live behind the point data rather than in front of
        it, so reading them means going to the end of the file and coming back.
        Only their headers are read; see ExtendedVariableLengthRecord for why
        the payloads are not.

        They are keyed by ``(user_id, record_id)``, as the ordinary records
        are: LAS namespaces records by user id, and a bare id collides --
        LASF_Spec reserves ids 0 to 99 for waveform packet descriptors, so a
        file with two of them would keep one.

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
                record = Reader._evlr_at(fp, end_of_file)
                if record is None:
                    break
                records[(record['user_id'], record['record_id'])] = record
                found += 1
                # past the payload, not through it
                fp.seek(record['record_length_after_header'], io.SEEK_CUR)
        finally:
            fp.seek(resume)

        if found < declared:
            return records, (f"file declares {declared} extended variable "
                             f"length records but holds {found}")
        return records, None

    @staticmethod
    def _evlr_at(fp, end_of_file):
        """One extended record, read from where `fp` is, or None.

        None means there is not a whole record here: either the 60-byte header
        is short or the payload it declares runs past the end of the file. That
        is checked here rather than where the payload is read, so a record that
        is not all there is left out rather than handed over and found short
        later. Leaves `fp` on the payload, which is where the next record's
        header begins once the payload is skipped.
        """
        data = fp.read(EVLR_HEADER_SIZE)
        if len(data) < EVLR_HEADER_SIZE:
            return None
        fields, _ = unpack_format(EVLR_HEADER_FORMAT, data)
        fields['offset_to_data'] = fp.tell()
        if (fields['offset_to_data'] + fields['record_length_after_header']
                > end_of_file):
            return None
        return ExtendedVariableLengthRecord(fields, fp)

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
        vlr = header['variable_length_records'].get(LASZIP_VLR_KEY)
        if vlr is None:
            return None
        return Reader._parse_laszip_record(vlr['data'])

    # -- properties ------------------------------------------------------

    @property
    def num_points(self):
        """How many points the file holds, which is also ``len(reader)``.

        The LAS 1.4 count where the header has one, since the legacy field
        is only 32 bits and saturates.
        """
        return self.header['number_of_point_records']

    @property
    def chunking(self):
        """How this file's points are grouped, as a :class:`Chunking`."""
        if self.laz_header is None:
            return Chunking.NONE
        # The VLR carries a chunk size whether or not anything chunks, so what
        # it means depends on the compressor beside it.
        if self.laz_header['compressor'] == Compressor.POINTWISE:
            return Chunking.NONE
        if self.laz_header['chunk_size'] < 0:
            return Chunking.ADAPTIVE
        return Chunking.FIXED

    @property
    def chunk_size(self):
        """How many points share a chunk, or None where no one number applies.

        None for an unchunked file -- plain LAS, or the POINTWISE container,
        whatever chunk size its LASzip VLR carries -- and None for adaptive
        chunking, where each chunk's size is the writer's choice and lives
        only in the chunk table. :attr:`chunking` tells those two apart;
        ``PointReader.chunk_starts`` has the boundaries themselves once the
        table has been read.
        """
        if self.chunking is not Chunking.FIXED:
            return None
        return self.laz_header['chunk_size']

    @property
    def point_format(self):
        """Which LAS point data format, 0 to 10, the points are in.

        With the high bit that flags compression cleared, and reported as
        the 1.4 format a compatibility-mode file stands in for rather than
        the legacy one it is written as.
        """
        return self.header['point_data_format_id']

    @property
    def is_compressed(self):
        """Whether this is a LAZ file rather than a plain LAS one."""
        return self.laz_header is not None

    @property
    def scales(self):
        """``(x, y, z)`` scale factors: what a stored coordinate means.

        A point's X, Y and Z are integers; multiplying by these and adding
        :attr:`offsets` gives the georeferenced coordinate. :meth:`scale`
        does this.
        """
        h = self.header
        return (h['x_scale_factor'], h['y_scale_factor'], h['z_scale_factor'])

    @property
    def offsets(self):
        """``(x, y, z)`` offsets, added to the scaled coordinates."""
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

        Defaults to every point remaining in the file. Hashes every decoded
        field entirely in C, so a whole file verifies against a laszip
        reference quickly even at tens of millions of points. Advances the
        reader.
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

    # -- region queries --------------------------------------------------

    def _appended_index_data(self):
        """The index the LASzip record says is inside this file, or None.

        Two ways in, both the same bytes. An index appended by ``lasindex
        -append`` is an extended record the LAS header does not count, found
        only by the offset the LASzip record keeps for it; a file that also
        lists it among its extended records is read from there instead, since
        those are parsed already.
        """
        record = self.header['extended_variable_length_records'].get(
            LASINDEX_EVLR_KEY)
        if record is not None:
            return record['data']

        if self.laz_header is None:
            return None
        offset = self.laz_header['offset_to_special_evlrs']
        if self.laz_header['number_of_special_evlrs'] <= 0 or offset < 0:
            return None
        if not _can_seek(self.fp):
            return None

        # read out from under the point reader and put the handle back, as
        # ExtendedVariableLengthRecord does; see its _read_data
        resume = self.fp.tell()
        try:
            self.fp.seek(0, io.SEEK_END)
            end_of_file = self.fp.tell()
            self.fp.seek(offset)
            record = Reader._evlr_at(self.fp, end_of_file)
        finally:
            self.fp.seek(resume)

        if record is None:
            # unlike a record the header merely counted, this one was pointed
            # at: something said an index is here, so a record that is not all
            # there is worth saying so about
            raise LazError("the record the LASzip header points at for the "
                           "spatial index runs past the end of the file")
        # the chain is for special records in general rather than for indexes,
        # so something else sitting there is not an error
        if (record['user_id'], record['record_id']) != LASINDEX_EVLR_KEY:
            return None
        return record['data']

    def _sidecar_index_data(self):
        """The index in the ".lax" beside this file, or None.

        Only a file opened by name has one to look for -- an index is found by
        the name of the file it indexes, and a file object has none.
        """
        if self._path is None:
            return None
        root, _ = os.path.splitext(self._path)
        try:
            with open(root + '.lax', 'rb') as fp:
                return fp.read()
        except OSError:
            return None

    @property
    def spatial_index(self):
        """The file's spatial index, or None if it has none.

        Looked for the first time it is asked about rather than when the file
        is opened, so a reader that only ever walks the points pays nothing for
        it. An index inside the file is preferred to one beside it: it travels
        with the file, so it is the one that cannot be stale.

        An index that is there but unreadable raises rather than being passed
        over. Falling back to a full scan would answer the same question far
        more slowly and say nothing about why.
        """
        if not self._index_looked_for:
            self._index_looked_for = True
            data = self._appended_index_data()
            if data is None:
                data = self._sidecar_index_data()
            if data is not None:
                self._index = SpatialIndex(data)
        return self._index

    @property
    def has_spatial_index(self):
        """Whether an index was found, in the file or beside it -- and so
        whether a rectangle query can skip most of the file."""
        return self.spatial_index is not None

    def _region(self, min_x, min_y, max_x, max_y):
        """What the C side needs to answer a rectangle query.

        A pair: the region -- the rectangle, then the scale and offset that
        put a point in it, as eight floats the C side takes as one argument
        -- and the half-open ``(start, stop)`` spans of point indices to look
        through. The spans are the index's intervals clamped against the
        point count, or the whole file where there is no index; clamping here
        is what keeps the core from needing to know how many points the file
        claims.
        """
        if min_x > max_x or min_y > max_y:
            raise ValueError("rectangle is inside out: "
                             "min must not exceed max")

        index = self.spatial_index
        num_points = self.num_points
        if index is None:
            spans = [(0, num_points)]
        else:
            # an index is data out of a file like any other, and one that
            # names points this file does not have is not worth decoding
            # towards
            spans = [(start, min(end + 1, num_points))
                     for start, end in index.intervals(min_x, min_y,
                                                       max_x, max_y)
                     if start < num_points]

        scales, offsets = self.scales, self.offsets
        region = (min_x, min_y, max_x, max_y,
                  scales[0], scales[1], offsets[0], offsets[1])
        return region, spans

    def points_within(self, min_x, min_y, max_x, max_y):
        """Yield the points inside a rectangle, in file order.

        The rectangle is half-open -- a point counts when ``min_x <= x <
        max_x`` and ``min_y <= y < max_y`` -- in the georeferenced coordinates
        :meth:`scale` returns, not the integers a point stores. Adjoining
        rectangles therefore partition the points rather than sharing the ones
        on the seam, which is what laszip's own rectangle query does.

        With a spatial index, only the runs of points the index says could be
        inside are decoded. Without one it is a filtered full scan: the same
        points, at the cost of reading everything.

        As with :meth:`points`, each iteration yields the same object with new
        contents; call ``point.copy()`` to keep one. The reader is left
        wherever the last interval ended, so :meth:`seek` before reading
        sequentially again.
        """
        region, spans = self._region(min_x, min_y, max_x, max_y)
        read_within = self._reader.read_within
        for start, stop in spans:
            self.seek(start)
            while True:
                point = read_within(stop, region)
                if point is None:
                    break
                yield point

    # -- reading as arrays -----------------------------------------------

    def _array_field(self, name):
        """Where *name* lives in a decoded point, sized for this file."""
        if name == 'extra_bytes':
            if not self.num_extra_bytes:
                raise ValueError("this file has no extra bytes")
            return _Field(-1, 'u1', width=self.num_extra_bytes)
        try:
            return _ARRAY_FIELDS[name]
        except KeyError:
            raise ValueError(f"unknown point field {name!r}") from None

    def arrays(self, *names, start=None, count=None):
        """Decode points into numpy arrays, one per field.

        Returns ``{name: array}``, each array *count* long and in file order.
        Named without arguments it returns every field this point format
        carries, extra bytes included::

            a = reader.arrays()
            ground = a["Z"][a["classification"] == 2]

        Naming fields reads only those, which is the difference between three
        columns and thirty for a whole file::

            a = reader.arrays("X", "Y", "Z", "gps_time")

        ``red``, ``green``, ``blue`` and ``nir`` are separate columns here,
        where ``Point`` groups them as ``rgb``. ``wave_packet`` and
        ``extra_bytes``, being opaque blobs, come back as ``(count, size)``
        arrays of bytes rather than one column of numbers -- and are most of
        what the no-argument call costs in memory.

        Reading starts where the reader is, or at *start* if given, and runs
        to the end of the file unless *count* says otherwise; a *count* past
        the end stops there, as slicing does. The reader is left after the
        last point read, so successive calls walk the file in blocks.
        """
        if start is not None:
            self.seek(start)
        remaining = self.num_points - self.index
        count = remaining if count is None else min(count, remaining)

        if not names:
            names = _fields_for_point_format(self.point_format,
                                             self.num_extra_bytes)

        out, targets, packed = self._array_columns(names, count)
        self._reader.read_into(targets, count)
        return self._finish_columns(out, packed, count)

    def _array_columns(self, names, count):
        """Arrays for *names*, and the read_into targets that fill them.

        Returns ``(out, targets, packed)``. ``out`` holds a column per name,
        with the sub-byte fields left as None until :meth:`_finish_columns`
        derives them from the byte they share. No names at all means every
        field this file's points carry.
        """
        np = _numpy()
        if not names:
            names = _fields_for_point_format(self.point_format,
                                             self.num_extra_bytes)
        out, targets, packed, byte_columns = {}, [], [], {}
        for name in names:
            f = self._array_field(name)
            if f.mask is None:
                shape = count if f.width == 1 else (count, f.width)
                column = np.empty(shape, dtype=f.dtype)
                targets.append((column, f.offset, column.itemsize * f.width))
                out[name] = column
                continue
            # the packed fields run several to a byte -- four in byte 14
            # alone -- so read each byte once and unpack them from it after
            if f.offset not in byte_columns:
                byte_columns[f.offset] = np.empty(count, dtype=f.dtype)
                targets.append((byte_columns[f.offset], f.offset, 1))
            packed.append((name, f, byte_columns[f.offset]))
            out[name] = None            # placeholder: holds its place in out
        return out, targets, packed

    @staticmethod
    def _finish_columns(out, packed, count):
        """Derive the sub-byte fields, and cut every column down to *count*.

        The cut is what a rectangle query needs: it sizes its arrays for
        every candidate, since how many are inside is what the query is for,
        and does not know the answer until it has read them. A copy rather
        than a view where most of the array is being dropped, so that a
        selective query does not go on holding the candidates it rejected.
        """
        for name, f, byte_column in packed:
            column = byte_column[:count] >> f.shift
            column &= f.mask
            out[name] = column
        for name, column in out.items():
            if len(column) != count:
                out[name] = (column[:count].copy() if count * 2 < len(column)
                             else column[:count])
        return out

    def arrays_within(self, *names, rect):
        """The points inside a rectangle, as numpy arrays.

        :meth:`arrays` and :meth:`points_within` in one: the fields *names*
        asks for, of the points ``rect`` -- ``(min_x, min_y, max_x, max_y)``
        -- contains, selected the same way and with the same half-open edges.

        The index decides which points to decode, and the array path decides
        how cheaply to hand them over::

            a = reader.arrays_within("X", "Y", "Z", rect=(x0, y0, x1, y1))

        Arrays are sized for every candidate the index turns up and trimmed
        to what was really inside, so a query briefly holds more than it
        returns. The reader is left wherever the last interval ended.
        """
        region, spans = self._region(*rect)
        candidates = sum(stop - start for start, stop in spans)
        out, targets, packed = self._array_columns(names, candidates)

        found = 0
        for start, stop in spans:
            self.seek(start)
            # each interval writes behind what the last one found, so the
            # columns are handed over sliced from there
            found += self._reader.read_into_within(
                [(column[found:], offset, size)
                 for column, offset, size in targets], stop, region)

        return self._finish_columns(out, packed, found)

    def xyz_within(self, rect):
        """The georeferenced points inside a rectangle, as ``(N, 3)`` floats.

        :meth:`xyz` restricted to ``rect``, which is
        ``(min_x, min_y, max_x, max_y)`` as :meth:`arrays_within` takes it.
        """
        return self._scaled_xyz(self.arrays_within('X', 'Y', 'Z', rect=rect))

    def xyz(self, start=None, count=None):
        """The georeferenced points, as an ``(N, 3)`` array of floats.

        The scale and offset from the header are applied, so these are
        georeferenced coordinates rather than the stored integers.
        *start* and *count* are as in :meth:`arrays`.
        """
        return self._scaled_xyz(
            self.arrays('X', 'Y', 'Z', start=start, count=count))

    def _scaled_xyz(self, columns):
        """X, Y and Z columns as one georeferenced (N, 3) array."""
        np = _numpy()
        # scaled a column at a time into the answer, which is the only
        # full-size float array this makes
        xyz = np.empty((len(columns['X']), 3), dtype=np.float64)
        scales, offsets = self.scales, self.offsets
        for i, name in enumerate('XYZ'):
            np.multiply(columns[name], scales[i], out=xyz[:, i])
            xyz[:, i] += offsets[i]
        return xyz
