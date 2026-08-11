"""The writing front end: :class:`Writer` and what only it needs."""

from collections import namedtuple

from ._cpylaz import PointWriter, LazError
from ._utils import cstr, pack_cstr
from .compat import _attribute_size, _extra_bytes_attributes
from .formats import (EXTRA_BYTES_VLR_KEY, LASZIP_VLR_KEY, Compressor,
                      Coder, UnsupportedFileError, _POINT_FORMATS,
                      items_for_point_format, _versioned_items,
                      _min_version_minor)
from .headers import (EXTRA_BYTES_ATTRIBUTE_FORMAT, MAX_VLR_PAYLOAD,
                      VLR_HEADER_FORMAT, LASZIP_RECORD_FORMAT,
                      LASZIP_ITEM_FORMAT, header_formats, pack_format,
                      _header_size, _can_seek)


def _as_i32(value):
    """A chunk size as the LASzip VLR declares it: signed, so the U32_MAX that
    selects variable-size chunks is written as -1."""
    return value - 0x100000000 if value > 0x7FFFFFFF else value


def _user_id(value):
    """A record's user id as a reader will key it by.

    Through the pair that writes the field and reads it back, so that a name
    given padded, or as text, is the one thing it can be on the way out --
    and one given too long is refused here rather than deeper down.
    """
    return cstr(pack_cstr(value, 16))


# ---------------------------------------------------------------------------
# Variable length records.
#
# A record is a mapping -- the shape Reader hands back -- with `user_id`,
# `record_id` and `data`, and optionally `description` and `reserved`. The
# payload length is taken from the payload rather than from the field that
# states it, so a record copied from a file whose length field lies is written
# as the bytes it really has.
#
# They are written in the order given, and the LASzip record last, which is
# where laszip puts its own: it appends to the header's records rather than
# leading with it.
# ---------------------------------------------------------------------------


def _records(vlrs):
    """`vlrs` ready to write: a list of records, in file order.

    Accepts a mapping keyed by ``(user_id, record_id)``, as
    ``header["variable_length_records"]`` is, or any iterable of records --
    so copying a file's records is handing them over as they came.

    A LASzip record among them is dropped rather than refused, which is what
    laszip does with one too: it describes how the file it came from was
    compressed, and the file being written has its own answer to that.
    """
    if hasattr(vlrs, 'values'):
        vlrs = vlrs.values()

    records = []
    keys = set()
    for vlr in vlrs:
        key = (_user_id(vlr['user_id']), vlr['record_id'])
        if key == LASZIP_VLR_KEY:
            continue
        if key in keys:
            raise ValueError(f"two records claim {key[0]!r} {key[1]}, and a "
                             "reader can only find one of them")
        records.append(_record(key, bytes(vlr['data']),
                               vlr.get('description', b''),
                               vlr.get('reserved', 0)))
        keys.add(key)
    return records


def _record(key, data, description, reserved=0):
    """One record, sized by the payload it holds."""
    if len(data) > MAX_VLR_PAYLOAD:
        raise ValueError(
            f"record {key[0]!r} {key[1]} holds {len(data)} bytes, over the "
            f"{MAX_VLR_PAYLOAD} a variable length record can declare")
    return {
        'reserved': reserved,
        'user_id': key[0],
        'record_id': key[1],
        'record_length_after_header': len(data),
        'description': description,
        'data': data,
    }


def _pack_vlr(record):
    """One record on disk: its 54-byte header, then its payload."""
    return pack_format(VLR_HEADER_FORMAT, record) + record['data']


def _extra_bytes_width(records, declared):
    """How many extra bytes a point carries, given the records and what the
    caller said.

    The "extra bytes" record is the file's own account of them, so it decides
    when it is there, and `declared` is only checked against it: a file whose
    descriptor and record length disagree is one nothing can read, and it is
    cheaper to refuse it here than to explain it later.
    """
    descriptor = next((record for record in records
                       if (record['user_id'],
                           record['record_id']) == EXTRA_BYTES_VLR_KEY), None)
    if descriptor is None:
        return 0 if declared is None else declared

    described = sum(a.size for a in _extra_bytes_attributes(
        descriptor['data']))
    if declared is not None and declared != described:
        raise ValueError(
            f"the extra bytes record describes {described} bytes per point, "
            f"but num_extra_bytes is {declared}")
    return described


# ---------------------------------------------------------------------------
# The "extra bytes" record, which is how a file says what its extra bytes
# mean. laszip builds one an attribute at a time, in laszip_add_attribute();
# this builds the whole record from the attributes, since it has to be
# complete before the header that counts it is written.
# ---------------------------------------------------------------------------

ExtraBytesAttribute = namedtuple(
    "ExtraBytesAttribute", "name data_type description scale offset",
    defaults=(b'', None, None))
ExtraBytesAttribute.__doc__ = """One attribute of a point's extra bytes.

    `data_type` is the LAS data type: 1 to 10 for the scalar types, in the
    order the specification lists them, and the deprecated array types above
    that. `scale` and `offset` are what turn the stored number into the
    quantity it stands for; leaving them out says the number is the quantity.
    """

# The option bits that say a descriptor's scale and offset were set at all;
# an attribute leaving them out is one whose numbers stand for themselves.
_SCALE_GIVEN = 0x08
_OFFSET_GIVEN = 0x10


def _pack_attribute(attribute):
    """One attribute descriptor, by the table that reads it back.

    no_data, min and max are left empty: they describe the range of a
    quantity rather than how to find it, and nothing here has an opinion
    about that.
    """
    if attribute.data_type == 0:
        raise ValueError(
            "data type 0 means undocumented bytes, whose width is in the "
            "option byte this puts scale and offset in; describe them with a "
            "record built by hand")
    _attribute_size(attribute.data_type, 0)     # raises for an unknown type

    options = 0
    if attribute.scale is not None:
        options |= _SCALE_GIVEN
    if attribute.offset is not None:
        options |= _OFFSET_GIVEN

    return pack_format(EXTRA_BYTES_ATTRIBUTE_FORMAT, {
        'reserved': 0,
        'data_type': attribute.data_type,
        'options': options,
        'name': attribute.name,
        'unused': 0,
        'no_data': b'', 'min': b'', 'max': b'',
        # one value per dimension, of which only the first matters for the
        # scalar types, and all three the same for the array ones
        'scale': [float(attribute.scale or 0.0)] * 3,
        'offset': [float(attribute.offset or 0.0)] * 3,
        'description': attribute.description,
    })


def extra_bytes_record(attributes, description=b''):
    """The "extra bytes" record describing `attributes`, as a record a
    :class:`Writer` takes.

    The attributes are laid out in the order given, which is the order they
    sit in a point:

        >>> record = extra_bytes_record([
        ...     ExtraBytesAttribute(b"amplitude", 3, scale=0.01),
        ...     ExtraBytesAttribute(b"echo width", 3)])
        >>> Writer("out.laz", point_format=1, vlrs=[record])  # doctest: +SKIP

    A writer given one takes the size of a point's extra bytes from it, so
    ``num_extra_bytes`` need not be given as well -- and if it is, it has to
    agree.
    """
    return {
        'user_id': EXTRA_BYTES_VLR_KEY[0],
        'record_id': EXTRA_BYTES_VLR_KEY[1],
        'description': description,
        'data': b''.join(_pack_attribute(a) for a in attributes),
    }


class Writer:
    """Write points to a LAS or LAZ file.

    The mirror of :class:`Reader`: the header and the variable length records
    are built here, and everything from the first point onward is the C
    extension's.

        >>> with Writer("out.laz", point_format=6, scales=(0.01,) * 3) as w:
        ...     for point in points:
        ...         w.write(point)

    A point is a :class:`Point` -- one built with ``Point(X=..., ...)``, or one
    that came from a reader -- or the raw bytes of its record, which is what a
    file being converted already has.

    One detail of the LAS 1.4 point formats matters when building points by
    hand: three of the four classification flags exist twice over.
    A record keeps synthetic, keypoint and withheld in the same four bits as
    overlap, but a decoded point splits them. When writing a record, the
    writer takes those three from ``synthetic_flag``, ``keypoint_flag`` and
    ``withheld_flag``, and only the overlap bit from
    ``extended_classification_flags`` -- LASzip's rule, matched so these are
    byte for byte the files laszip would have written.

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
                 offsets=(0.0, 0.0, 0.0), compressed=None, compressor=None,
                 laz_version=None, chunk_size=50000, num_extra_bytes=None,
                 version_minor=None, system_identifier=b'',
                 generating_software=None, vlrs=(), vlr_description=b'lazpy',
                 file_creation=(0, 0)):
        """Open *filename* for writing points of *point_format*.

        ``compressed`` defaults to LAZ unless the name ends in ``.las``.
        ``compressor`` picks which container the points go in, defaulting to
        the chunked one for the point format; ``laz_version`` picks the LASzip
        item encoding, 1 or 2 for point formats 0-5 and 3 or 4 for 6-10,
        defaulting to what laszip itself would choose. Both are meaningless
        for an uncompressed file and rejected there. ``version_minor`` picks
        the LAS version, defaulting to the oldest one that can describe the
        point format.

        ``chunk_size`` is how many points share a chunk, which sets what
        random access costs on read-back. ``0xFFFFFFFF`` leaves the boundaries
        to the caller, who ends each chunk with ``chunk()``. It is recorded in
        the VLR whatever the container, as laszip records it, but POINTWISE
        has no chunks for it to describe.

        ``scales`` and ``offsets`` are how the integer coordinates of a point
        become georeferenced ones; they are recorded in the header and applied
        to nothing here, since points are written as they are given.

        ``vlrs`` are the variable length records the file carries besides the
        LASzip one, which is the writer's own: a coordinate reference system,
        an "extra bytes" descriptor, whatever a file being copied had. They
        are taken here rather than later because the header records how far
        past itself the points begin.

        ``num_extra_bytes`` is how many opaque bytes ride on the end of each
        point. It defaults to what the "extra bytes" record among ``vlrs``
        describes, and to none when there is no such record.

        ``system_identifier``, ``generating_software`` and ``vlr_description``
        are free text the file carries about its own provenance.
        """
        self.fp = None
        self.header = None
        self._writer = None
        self._closed = False
        self._owns_fp = False

        records = _records(vlrs)
        num_extra_bytes = _extra_bytes_width(records, num_extra_bytes)
        if num_extra_bytes < 0:
            raise ValueError("num_extra_bytes cannot be negative")

        # raises for a point format there is no layout for, so everything
        # below can look one up
        record_length = (_POINT_FORMATS.get(point_format, (0,))[0]
                         + num_extra_bytes)
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
            compressor = self._resolve_compressor(compressor, point14)
            self.items = _versioned_items(self.items, laz_version)
        else:
            if laz_version not in (None, 0):
                raise ValueError("an uncompressed file has no item version")
            if compressor not in (None, Compressor.NONE):
                raise ValueError("an uncompressed file has no compressor")
            laz_version = 0
            compressor = Compressor.NONE
        self.laz_version = laz_version
        self.compressor = compressor
        self.chunk_size = chunk_size

        # the LASzip record goes last, where laszip puts its own: it appends
        # to the records it was given
        if self.compressed:
            records.append(self._laszip_record(vlr_description))
        # packed before the header, because the header records how far past
        # itself the points begin
        block = b''.join(_pack_vlr(record) for record in records)

        self._open(filename)
        try:
            self.header = self._build_header(
                record_length, version_minor, len(records), len(block),
                scales, offsets, system_identifier, generating_software,
                file_creation)
            self.fp.write(self._pack_header(self.header))
            self.fp.write(block)
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

    @staticmethod
    def _resolve_compressor(compressor, point14):
        """The container to put the points in, defaulted and checked together
        so the rule behind both is written once.

        The LAS 1.4 items are layered and need the container that carries
        layers; the legacy items cannot use it. Of the two containers the
        legacy items do have, POINTWISE is LASzip's original: the whole file
        as one stream, no chunk table, and so no random access on the way back
        in -- which is why the chunked one leads and is the default.

        Unlike the item-version check beside this, the mismatch it refuses is
        not merely inconvenient: laz_writepoint_setup refuses it too, because
        a container and its writers that disagree about layering would call
        through a hook that is not there.
        """
        allowed = ((Compressor.LAYERED_CHUNKED,) if point14 else
                   (Compressor.POINTWISE_CHUNKED, Compressor.POINTWISE))
        if compressor is None:
            return allowed[0]
        if compressor not in allowed:
            raise UnsupportedFileError(
                f"compressor {Compressor(compressor).name} is not one of "
                f"{tuple(c.name for c in allowed)} for this point format")
        return Compressor(compressor)

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
            raise ValueError(
                "writing needs a seekable file, because the point count and "
                "bounding box are only known at the end")

    def _build_header(self, record_length, version_minor, num_records,
                      vlr_size, scales, offsets, system_identifier,
                      generating_software, file_creation):
        header_size = _header_size(version_minor)
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
            'number_of_variable_length_records': num_records,
            # the high bit is what tells a reader the points are compressed
            'point_data_format_id': (self.point_format
                                     | (0x80 if self.compressed else 0)),
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
    def _pack_header(header):
        """The header bytes, from the tables Reader parses them with.

        A caller may set header fields until the file is closed, but not ones
        that change how long the header is -- that length is already in the
        file, in header_size and again in offset_to_point_data.
        """
        version_minor = header['version_minor']
        if _header_size(version_minor) != header['header_size']:
            raise LazError(
                f"a LAS 1.{version_minor} header is "
                f"{_header_size(version_minor)} bytes, but this file "
                f"declares {header['header_size']}")
        return b''.join(pack_format(fmt, header)
                        for fmt in header_formats(version_minor))

    def _laszip_record(self, description):
        """The LASzip record, the inverse of Reader._parse_laszip_record."""
        major, minor, revision = self.LASZIP_VERSION
        payload = pack_format(LASZIP_RECORD_FORMAT, {
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
            payload += pack_format(LASZIP_ITEM_FORMAT,
                                   {'type': item_type, 'size': size,
                                    'version': version})

        return _record(LASZIP_VLR_KEY, payload, description)

    # -- writing ---------------------------------------------------------

    def write(self, point):
        """Append one point, as a :class:`Point` or as its record bytes.

        Raises ValueError once the writer is closed.
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
            header['extended_number_of_points_by_return'] = \
                list(by_return[1:16])

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
