"""The reading front end: :class:`Reader` and what only it needs."""

from collections import namedtuple
from collections.abc import Mapping
import io
import os

from ._cpylaz import PointReader, SpatialIndex, LazError, POINT_LAYOUT
from .formats import (LASINDEX_EVLR_KEY, Compressor, Coder,
                      Chunking, Selective, UnsupportedFileError,
                      items_for_point_format, _POINT_FORMATS)
from .headers import (EVLR_HEADER_FORMAT, EVLR_HEADER_SIZE, unpack_format,
                      _can_seek, _read_las_header, _find_laz_header)
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
# Where each one sits comes from C, through POINT_LAYOUT: the extension takes
# it from LazPoint with offsetof, so the offsets are stated once, in the file
# that decides them. Restating them here used to mean that reordering the
# struct made every array silently wrong, with only a test between that and a
# released version.
#
# What stays is what C has no opinion about: the numpy type a field lands in,
# and how the ones packed into part of a byte come out of it. Types are
# spelled in native order ('='), because read_into copies the decoded point's
# bytes straight through and a decoded point is in host order -- see the
# byte-order note on LazPoint. The sub-byte shifts are host-independent:
# LazPoint packs those bytes by hand rather than leaving them to the compiler,
# for exactly this reason.
#
# A field's offset is a member's plus how far into it the field starts, which
# is only ever non-zero for the four colours sharing `rgb`. -1 means the extra
# bytes instead, which are not part of the struct at all.
# ---------------------------------------------------------------------------

# the one width C states rather than a numpy dtype implying it
_WAVEPACKET_WIDTH = POINT_LAYOUT['wave_packet'][1]

_Field = namedtuple("_Field", "offset dtype width shift mask",
                    defaults=(1, 0, None))

# Each field as its own column: the member of LazPoint it comes out of, how
# far into that member it starts, and the rest. `within` is for the two
# members that hold more than one field end to end -- the three colours and
# the near infrared share `rgb`, as they do on disk.
_Column = namedtuple("_Column", "member dtype within width shift mask",
                     defaults=(0, 1, 0, None))

_ARRAY_COLUMNS = {
    'X': _Column('X', '=i4'),
    'Y': _Column('Y', '=i4'),
    'Z': _Column('Z', '=i4'),
    'intensity': _Column('intensity', '=u2'),
    'return_number': _Column('returns_and_flags', 'u1', mask=0x07),
    'number_of_returns': _Column('returns_and_flags', 'u1', shift=3,
                                 mask=0x07),
    'scan_direction_flag': _Column('returns_and_flags', 'u1', shift=6,
                                   mask=0x01),
    'edge_of_flight_line': _Column('returns_and_flags', 'u1', shift=7,
                                   mask=0x01),
    'classification': _Column('classification_bits', 'u1', mask=0x1F),
    'synthetic_flag': _Column('classification_bits', 'u1', shift=5, mask=0x01),
    'keypoint_flag': _Column('classification_bits', 'u1', shift=6, mask=0x01),
    'withheld_flag': _Column('classification_bits', 'u1', shift=7, mask=0x01),
    'scan_angle_rank': _Column('scan_angle_rank', 'i1'),
    'user_data': _Column('user_data', 'u1'),
    'point_source_ID': _Column('point_source_ID', '=u2'),
    'extended_scan_angle': _Column('extended_scan_angle', '=i2'),
    'extended_point_type': _Column('extended_flags', 'u1', mask=0x03),
    'extended_scanner_channel': _Column('extended_flags', 'u1', shift=2,
                                        mask=0x03),
    'extended_classification_flags': _Column('extended_flags', 'u1', shift=4,
                                             mask=0x0F),
    'extended_classification': _Column('extended_classification', 'u1'),
    'extended_return_number': _Column('extended_returns', 'u1', mask=0x0F),
    'extended_number_of_returns': _Column('extended_returns', 'u1', shift=4,
                                          mask=0x0F),
    'gps_time': _Column('gps_time', '=f8'),
    'red': _Column('rgb', '=u2'),
    'green': _Column('rgb', '=u2', within=2),
    'blue': _Column('rgb', '=u2', within=4),
    'nir': _Column('rgb', '=u2', within=6),
    # a wavepacket keeps its on-disk order in the point, so it is bytes here
    'wave_packet': _Column('wave_packet', 'u1', width=_WAVEPACKET_WIDTH),
}


def _array_fields():
    """The columns above, placed where the C struct really keeps them.

    Only the placing comes from C; which numpy type a field lands in, and how
    the ones sharing a byte are unpacked out of it, are numpy's business and
    stay here. The width of a blob is checked against what C says rather than
    restated, so a wavepacket that grew would be a failure to import rather
    than a column reading 29 bytes of a longer field.
    """
    fields = {}
    for name, column in _ARRAY_COLUMNS.items():
        offset, size = POINT_LAYOUT[column.member]
        if column.within + column.width > size:
            raise RuntimeError(
                f"{name} does not fit in LazPoint.{column.member}")
        fields[name] = _Field(offset + column.within, column.dtype,
                              column.width, column.shift, column.mask)
    return fields


_ARRAY_FIELDS = _array_fields()

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


def _array_field(name, num_extra_bytes):
    """Where *name* lives in a decoded point, for a file with this many extra
    bytes. Both directions of the array API ask: a column is read out of the
    same place it is written into."""
    if name == 'extra_bytes':
        if not num_extra_bytes:
            raise ValueError("this file has no extra bytes")
        return _Field(-1, 'u1', width=num_extra_bytes)
    try:
        return _ARRAY_FIELDS[name]
    except KeyError:
        raise ValueError(f"unknown point field {name!r}") from None


# How far apart two points of a cell may be before the second starts a run of
# its own. LASinterval's own default, and the same number laz_index.c merges a
# query's runs by: a gap that small costs a reader the points inside it and
# saves it a seek. lasindex gives no flag for it, so neither does this.
_RUN_GAP = 1000


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

    Behaves like the dict a regular VLR is: every field of the record header
    is a key, plus ``offset_to_data`` -- where the payload begins in the file
    -- and ``data``, the payload itself.

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
    point, or use :meth:`seek`, :meth:`arrays` and
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
        self.items = None
        self.num_extra_bytes = None
        self._scale_offset = None
        self._reader = None
        self._was_opened = False
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
        """Open a file by path. Also accepts an already-open binary file.

        A reader that was already open is closed first, so opening a second
        file through the same object does not strand the first one's handle.
        """
        self.close()
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
        self._was_opened = True
        return self

    def _setup(self):
        self.header = _read_las_header(self.fp)
        # before the point reader takes the file over, since this seeks past
        # the point data and back
        records, self._evlr_warning = self._read_evlrs(self.fp, self.header)
        self.header['extended_variable_length_records'] = records
        self.laz_header = _find_laz_header(self.header)

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

    # -- properties ------------------------------------------------------

    def _points(self):
        """The point reader, or a refusal saying why there is not one.

        Everything that touches the points goes through here, so that a
        reader with no file behind it says so in one recognisable way rather
        than letting an internal None surface as whatever the next line
        happens to do with it. ValueError, and closed-means-closed, are what
        :class:`Writer` already answers in the same situation.
        """
        if self._reader is None:
            raise ValueError("reader is closed" if self._was_opened
                             else "reader is not open")
        return self._reader

    def _fields(self):
        """The header, or the same refusal. Kept after close, since it was
        read once and describes a file that has not changed."""
        if self.header is None:
            raise ValueError("reader is not open")
        return self.header

    @property
    def num_points(self):
        """How many points the file holds, which is also ``len(reader)``.

        The LAS 1.4 count where the header has one, since the legacy field
        is only 32 bits and saturates.
        """
        return self._fields()['number_of_point_records']

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
        return self._fields()['point_data_format_id']

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
        h = self._fields()
        return (h['x_scale_factor'], h['y_scale_factor'], h['z_scale_factor'])

    @property
    def offsets(self):
        """``(x, y, z)`` offsets, added to the scaled coordinates."""
        h = self._fields()
        return (h['x_offset'], h['y_offset'], h['z_offset'])

    @property
    def index(self):
        """Index of the next point to be read."""
        return self._points().index

    @property
    def warnings(self):
        """The non-fatal problems found while reading this file, as a tuple.

        Each is something recoverable: a chunk table that is missing or
        corrupt, which costs random access -- seeking then has to decode
        forward instead of jumping to a chunk -- or extended records behind
        the point data that are fewer than the header claims. The chunk table
        comes first, being the one that affects reading points.

        A spatial index has warnings of its own, under
        ``spatial_index.warning``; they are not here because asking would
        make every reader go looking for an index it may never want.
        """
        table = self._reader.warning if self._reader is not None else None
        return tuple(w for w in (table, self._evlr_warning) if w)

    @property
    def warning(self):
        """The first of :attr:`warnings`, or None.

        What to look at when one warning is as good as another, which for
        reporting a file is usually the case.
        """
        found = self.warnings
        return found[0] if found else None

    # -- reading ---------------------------------------------------------

    def read(self):
        """Decode the next point.

        The returned :class:`Point` is the reader's own buffer and is
        overwritten by the next ``read()``; call ``point.copy()`` to keep it.
        """
        return self._points().read()

    def checksum(self, count=None):
        """Decode *count* points and return ``(fnv1a_hash, points_read)``.

        Defaults to every point remaining in the file. Hashes every decoded
        field entirely in C, so a whole file verifies against a laszip
        reference quickly even at tens of millions of points. Advances the
        reader.
        """
        if count is None:
            count = self.num_points - self.index
        return self._points().checksum(max(0, count))

    def seek(self, index):
        """Position the reader so the next ``read()`` returns point *index*."""
        if index < 0 or index > self.num_points:
            raise IndexError(f"point index {index} out of range")
        self._points().seek(index)

    def scale(self, point):
        """Return the georeferenced (x, y, z) of *point* as floats."""
        sx, sy, sz, ox, oy, oz = self._scale_offset
        return (point.X * sx + ox, point.Y * sy + oy, point.Z * sz + oz)

    def points(self, start=0, count=None):
        """Yield *count* points from point *start*, without copying.

        Each iteration yields the same object with new contents, so store
        ``point.copy()`` if points need to outlive the loop.

        *start* is a position to go to, not a number of points to skip, so
        the default reads the file from the beginning however much of it has
        been read already. Being a generator, it goes there when the first
        point is asked for rather than when it is called.

        Note that :meth:`arrays` and :meth:`xyz` spell the same argument
        ``start`` but default it to where the reader already is; only this
        one rewinds.
        """
        self.seek(start)
        remaining = self.num_points - start if count is None else count
        read = self._points().read      # hoisted: this loop runs per point
        for _ in range(remaining):
            yield read()

    def __iter__(self):
        """Yield the points from here on, so that iterating a reader something
        has already read from carries on rather than starting again."""
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

    def _sidecar_path(self):
        """Where an index of this file belongs, or None for a file object.

        An index is found by the name of the file it indexes, so a reader
        opened on a stream neither finds one nor has anywhere to put one.
        """
        if self._path is None:
            return None
        return os.path.splitext(self._path)[0] + '.lax'

    def _sidecar_index_data(self):
        """The index in the ".lax" beside this file, or None."""
        path = self._sidecar_path()
        if path is None:
            return None
        try:
            with open(path, 'rb') as fp:
                return fp.read()
        except OSError:
            return None

    def build_spatial_index(self, cell_size=1.0, minimum_points=100000,
                            maximum_intervals=-20):
        """Build a spatial index over this file's points, as bytes.

        What ``lasindex`` does, and the other half of the index this reader
        already knows how to use: the bytes are a ``.lax`` file's whole
        contents, so writing them beside the cloud is all it takes --
        :meth:`write_spatial_index` does that.

        ``cell_size`` is how wide the quadtree's leaves are, in the units the
        coordinates are in; the tree is deep enough to reach it over the area
        the points cover. ``minimum_points`` and ``maximum_intervals`` are
        the coarsening: cells holding fewer than that between them merge into
        their parent, and the runs of point indices merge until at most that
        many are left -- negative meaning that many per cell, which is how
        lasindex is usually asked.

        Two passes over the points, both in C: where each one falls cannot be
        settled until the extent of them all is known. The reader is left at
        the end of the file.
        """
        if not self.num_points:
            raise LazError("a file with no points has nothing to index")
        self.seek(0)
        bounds = self._point_bounds()
        self.seek(0)
        return self._points().build_index(
            self.num_points, bounds, self.scales[:2], self.offsets[:2],
            float(cell_size), int(minimum_points), int(maximum_intervals),
            _RUN_GAP)

    def _point_bounds(self):
        """The area the points really cover, georeferenced.

        The header's bounding box would do and would cost nothing, but a file
        whose header is wrong -- or is a placeholder, as every fixture's is --
        would get a tree with everything in one cell. laszip's own index
        creation goes by the points too.
        """
        stored = self._points().bounds(self.num_points)
        scales, offsets = self.scales, self.offsets
        return tuple(value * scales[i % 2] + offsets[i % 2]
                     for i, value in enumerate(stored))

    def write_spatial_index(self, path=None, **kwargs):
        """Build an index and write it beside the file, as ``lasindex`` does.

        The path defaults to this file's own with a ``.lax`` extension, which
        is where :attr:`spatial_index` looks for one. Everything else is
        :meth:`build_spatial_index`'s. Returns the path written.
        """
        if path is None:
            path = self._sidecar_path()
            if path is None:
                raise ValueError("a reader opened on a file object has no "
                                 "name to put an index beside")
        data = self.build_spatial_index(**kwargs)
        with open(path, 'wb') as fp:
            fp.write(data)
        return path

    @property
    def spatial_index(self):
        """The file's spatial index, or None if it has none.

        Looked for the first time it is asked about rather than when the file
        is opened, so a reader that only ever walks the points pays nothing for
        it. An index inside the file is preferred to one beside it: it travels
        with the file, so it is the one that cannot be stale.

        An index that is there but unreadable raises rather than being
        ignored. Falling back to a full scan would answer the same question
        far more slowly and say nothing about why.
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

    def _region(self, rect=None, circle=None):
        """What the C side needs to answer a query over an area.

        The area is a rectangle ``(min_x, min_y, max_x, max_y)`` or a circle
        ``(center_x, center_y, radius)``, one or the other.

        A pair: the region -- the rectangle, the scale and offset that put a
        point in it, and the circle, as eleven floats the C side takes as one
        argument -- and the half-open ``(start, stop)`` spans of point indices
        to look through. The spans are the index's intervals clamped against
        the point count, or the whole file where there is no index; clamping
        here is what keeps the core from needing to know how many points the
        file claims.
        """
        if (rect is None) == (circle is None):
            raise TypeError("a query is over a rectangle or a circle")

        if circle is None:
            min_x, min_y, max_x, max_y = rect
            center_x = center_y = radius = 0.0
            if min_x > max_x or min_y > max_y:
                raise ValueError("rectangle is inside out: "
                                 "min must not exceed max")
        else:
            center_x, center_y, radius = circle
            if radius < 0:
                raise ValueError("a circle's radius cannot be negative")
            # the rectangle goes unread for a circle: the index is asked for
            # the circle itself, and so is every candidate point
            min_x = min_y = max_x = max_y = 0.0

        index = self.spatial_index
        num_points = self.num_points
        if circle is not None and not radius:
            spans = []                      # a circle of no size holds nothing
        elif index is None:
            spans = [(0, num_points)]
        else:
            if circle is None:
                intervals = index.intervals(min_x, min_y, max_x, max_y)
            else:
                intervals = index.intervals_within_circle(center_x, center_y,
                                                          radius)
            # an index is data out of a file like any other, and one that
            # names points this file does not have is not worth decoding
            # towards
            spans = [(start, min(end + 1, num_points))
                     for start, end in intervals
                     if start < num_points]

        scales, offsets = self.scales, self.offsets
        region = (min_x, min_y, max_x, max_y,
                  scales[0], scales[1], offsets[0], offsets[1],
                  center_x, center_y, radius)
        return region, spans

    @staticmethod
    def _area(bounds, rect, circle):
        """One area from either spelling of it.

        A rectangle may be given as four numbers or as ``rect=``, so that the
        point queries and the array queries take the same arguments while the
        older positional form goes on working.
        """
        if not bounds:
            return rect, circle
        if rect is not None or circle is not None:
            raise TypeError("give a rectangle once, not twice")
        if len(bounds) != 4:
            raise TypeError("a rectangle is min_x, min_y, max_x, max_y")
        return bounds, None

    def points_within(self, *bounds, rect=None, circle=None):
        """Yield the points inside a rectangle, in file order.

        The rectangle is half-open -- a point counts when ``min_x <= x <
        max_x`` and ``min_y <= y < max_y`` -- in the georeferenced coordinates
        :meth:`scale` returns, not the integers a point stores. Adjoining
        rectangles therefore partition the points rather than sharing the ones
        on the seam, which is what laszip's own rectangle query does.

        With a spatial index, only the runs of points the index says could be
        inside are decoded. Without one it is a filtered full scan: the same
        points, at the cost of reading everything.

        The rectangle is four numbers, or ``rect=(min_x, min_y, max_x,
        max_y)``; ``circle=(center_x, center_y, radius)`` selects that shape
        instead. Those are the arguments :meth:`arrays_within` and
        :meth:`xyz_within` take, so a query keeps its shape when it moves
        between them.

        As with :meth:`points`, each iteration yields the same object with new
        contents; call ``point.copy()`` to keep one. The reader is left
        wherever the last interval ended, so :meth:`seek` before reading
        sequentially again.
        """
        rect, circle = self._area(bounds, rect, circle)
        return self._points_in(*self._region(rect=rect, circle=circle))

    def points_within_circle(self, center_x, center_y, radius):
        """Yield the points inside a circle, in file order.

        :meth:`points_within` over a circle rather than a rectangle, which is
        what selecting around a point wants. A point counts when it is
        strictly nearer the centre than `radius`, in the georeferenced
        coordinates :meth:`scale` returns.

        With a spatial index this reaches fewer cells than the square around
        the circle would -- the corners of that square are cells a circle
        never touches -- so it decodes less as well as returning less.

        The same as ``points_within(circle=(center_x, center_y, radius))``,
        named because selecting around a point is worth a name.
        """
        return self.points_within(circle=(center_x, center_y, radius))

    def _points_in(self, region, spans):
        """Yield the points a query selects, interval by interval."""
        read_within = self._points().read_within
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
        return _array_field(name, self.num_extra_bytes)

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
        self._points().read_into(targets, count)
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

    def arrays_within(self, *names, rect=None, circle=None):
        """The points inside a rectangle or a circle, as numpy arrays.

        :meth:`arrays` and :meth:`points_within` in one: the fields *names*
        asks for, of the points the area contains, selected the same way and
        with the same edges. The area is ``rect``, which is
        ``(min_x, min_y, max_x, max_y)``, or ``circle``, which is
        ``(center_x, center_y, radius)``.

        The index decides which points to decode, and the array path decides
        how cheaply to hand them over::

            a = reader.arrays_within("X", "Y", "Z", rect=(x0, y0, x1, y1))
            a = reader.arrays_within("X", "Y", circle=(x, y, 30.0))

        Arrays are sized for the candidates being looked through and trimmed
        to what was really inside, so a query briefly holds more than it
        returns -- but never more than :data:`WITHIN_BLOCK` points' worth at
        a time, which is what keeps a small query over a file with no index
        from sizing itself for the whole file. The reader is left wherever
        the last interval ended.
        """
        region, spans = self._region(rect=rect, circle=circle)
        blocks = [self._within_block(names, region, start, stop)
                  for span_start, span_stop in spans
                  for start, stop in self._blocks(span_start, span_stop)]
        return self._joined(names, blocks)

    #: How many points an area query looks through at once. Its arrays are
    #: sized for that many, so it bounds what a query costs where the index
    #: cannot narrow it: without an index every point is a candidate, and
    #: sizing for the candidates would be sizing for the file.
    WITHIN_BLOCK = 1 << 20

    @classmethod
    def _blocks(cls, start, stop):
        while start < stop:
            end = min(stop, start + cls.WITHIN_BLOCK)
            yield start, end
            start = end

    def _within_block(self, names, region, start, stop):
        """The points of one run that are inside the region, as columns."""
        out, targets, packed = self._array_columns(names, stop - start)
        self.seek(start)
        found = self._points().read_into_within(targets, stop, region)
        return self._finish_columns(out, packed, found)

    def _joined(self, names, blocks):
        """One set of columns from several, without copying where there is
        only one -- which is every query small enough to fit a block, and so
        every indexed query over a modest area."""
        if len(blocks) == 1:
            return blocks[0]
        if not blocks:
            out, _, packed = self._array_columns(names, 0)
            return self._finish_columns(out, packed, 0)
        np = _numpy()
        return {name: np.concatenate([block[name] for block in blocks])
                for name in blocks[0]}

    def xyz_within(self, rect=None, circle=None):
        """The georeferenced points inside an area, as ``(N, 3)`` floats.

        :meth:`xyz` restricted to ``rect`` or ``circle``, which are what
        :meth:`arrays_within` takes them to be.
        """
        return self._scaled_xyz(self.arrays_within('X', 'Y', 'Z', rect=rect,
                                                   circle=circle))

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
