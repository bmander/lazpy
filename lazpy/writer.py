"""The writing front end: :class:`Writer` and what only it needs."""

from ._cpylaz import PointWriter, LazError
from .formats import (LASZIP_VLR_RECORD_ID, LASZIP_VLR_USER_ID, Compressor,
                      Coder, UnsupportedFileError, _POINT_FORMATS,
                      items_for_point_format, _versioned_items,
                      _min_version_minor)
from .headers import (VLR_HEADER_FORMAT, LASZIP_RECORD_FORMAT,
                      LASZIP_ITEM_FORMAT, header_formats, pack_format,
                      _header_size, _can_seek)


def _as_i32(value):
    """A chunk size as the LASzip VLR declares it: signed, so the U32_MAX that
    selects variable-size chunks is written as -1."""
    return value - 0x100000000 if value > 0x7FFFFFFF else value


class Writer:
    """Write points to a LAS or LAZ file.

    The mirror of :class:`Reader`: the header and the LASzip VLR are built
    here, and everything from the first point onward is the C extension's.

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
                 laz_version=None, chunk_size=50000, num_extra_bytes=0,
                 version_minor=None, system_identifier=b'',
                 generating_software=None, vlr_description=b'lazpy',
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

        ``system_identifier``, ``generating_software`` and ``vlr_description``
        are free text the file carries about its own provenance.
        """
        self.fp = None
        self.header = None
        self._writer = None
        self._closed = False
        self._owns_fp = False

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

        # built before the header, because the header records how far past
        # itself the points begin
        vlr = (self._pack_laszip_vlr(vlr_description) if self.compressed
               else b'')

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

    def _build_header(self, record_length, version_minor, vlr_size, scales,
                      offsets, system_identifier, generating_software,
                      file_creation):
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
            'number_of_variable_length_records': 1 if self.compressed else 0,
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

    def _pack_laszip_vlr(self, description):
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
            'description': description,
        }) + record

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
