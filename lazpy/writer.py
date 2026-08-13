"""The writing front end: :class:`Writer` and what only it needs."""

import io
import math

from ._cpylaz import PointWriter, LazError
from ._utils import cstr, pack_cstr
from .compat import _compatibility_payload, _disguise, _DISGUISED_FORMAT
from .extra_bytes import _described_width
from .formats import (EXTRA_BYTES_VLR_KEY, LASCOMPATIBLE_VLR_KEY,
                      LASINDEX_EVLR_KEY, LASZIP_VLR_KEY,
                      PROJECTION_VLR_KEYS, WKT_GLOBAL_ENCODING_BIT,
                      Compressor,
                      Coder, UnsupportedFileError, _POINT_FORMATS,
                      items_for_point_format, _versioned_items,
                      _default_version_minor, _min_version_minor)
from .crs import crs_record
from .reader import _array_field, _numpy
from .headers import (EVLR_HEADER_FORMAT, LASZIP_SPECIAL_EVLRS_AT,
                      LASZIP_SPECIAL_EVLR_FORMAT, MAX_VLR_PAYLOAD,
                      VLR_HEADER_FORMAT, VLR_HEADER_SIZE,
                      header_formats, pack_format, _header_size, _can_seek,
                      _read_las_header, _pack_laszip_record)


# What a point's X, Y and Z can hold: they are signed 32-bit, and scaling and
# offsetting exist to keep a coordinate from overflowing them.
_I32_MIN, _I32_MAX = -0x80000000, 0x7FFFFFFF

#: The scales a file is written to unless the caller says otherwise, in the
#: units its coordinates are in -- centimetres, for a survey in metres.
DEFAULT_SCALES = (0.01, 0.01, 0.01)


def _as_i32(value):
    """A chunk size as the LASzip VLR declares it: signed, so the U32_MAX that
    selects variable-size chunks is written as -1."""
    return value - 0x100000000 if value > _I32_MAX else value


def _quantize(value):
    """A float as the integer a point holds, rounded laszip's way.

    I32_QUANTIZE in LASzip's mydefs.hpp: a half goes away from zero, where
    Python's own round() would send it to the nearer even number. The two
    disagree on exactly the coordinates that land on a half scale unit, which
    is where the points of a regular grid land constantly.
    """
    return int(value + 0.5) if value >= 0 else int(value - 0.5)


# laszip's own step for an automatic offset, out of laszip_auto_offset(): the
# offset is rounded down to a multiple of ten million scale units, so that a
# file's offsets are round numbers rather than wherever its middle happened to
# fall, and two files of the same survey are likely to share them.
_OFFSET_STEP = 10_000_000


def auto_offsets(mins, maxs, scales=DEFAULT_SCALES):
    """Offsets that bring a survey within reach of the integers a point holds.

    ``mins`` and ``maxs`` are the ``(x, y, z)`` extremes of the points to be
    written and ``scales`` what they will be stored to. The offsets returned
    put the middle of that box near zero, which is how projected coordinates
    come to fit in the signed 32-bit integer a point holds: a UTM northing of
    six and a half million metres, stored to the millimetre, is six times what
    that integer reaches from an offset of zero.

    This is ``laszip_auto_offset()``, including its rounding of each offset
    down to a multiple of ten million scale units.

        >>> auto_offsets((515000.0, 6748000.0, 0.0),
        ...              (516000.0, 6749000.0, 400.0))
        (500000.0, 6700000.0, 0.0)
    """
    offsets = []
    for low, high, scale in zip(mins, maxs, scales):
        if not scale > 0:
            raise ValueError(f"a scale factor must be positive, not {scale}")
        middle = (low + high) / 2
        offsets.append(math.floor(middle / scale / _OFFSET_STEP)
                       * _OFFSET_STEP * scale)
    return tuple(offsets)


def append_spatial_index(path, data):
    """Put a spatial index inside the file it indexes, and say where it went.

    ``lasindex -append``'s doing, and what :func:`Reader.build_spatial_index`
    makes the bytes for: this function writes the index at the end of the file
    as an extended record and patches the LASzip record's
    ``number_of_special_evlrs`` and ``offset_to_special_evlrs`` to point at
    it. Nothing in the LAS header mentions the index, so those two fields are
    the only way back to it -- which is why the file has to be a compressed
    one, a plain LAS file having no LASzip record to carry them.

    :class:`Reader` prefers an index found this way over one in a ``.lax``
    beside the file, since this one cannot be stale.
    """
    with open(path, 'r+b') as fp:
        header = _read_las_header(fp)
        laszip = header['variable_length_records'].get(LASZIP_VLR_KEY)
        if laszip is None:
            raise LazError("only a compressed file can carry an index inside "
                           "it: the LASzip record is what points at one")

        fp.seek(0, io.SEEK_END)
        at = fp.tell()
        record = _record(LASINDEX_EVLR_KEY, bytes(data),
                         b'LAX spatial indexing (LASindex)')
        fp.write(pack_format(EVLR_HEADER_FORMAT, record))
        fp.write(record['data'])

        fp.seek(laszip['offset_to_data'] + LASZIP_SPECIAL_EVLRS_AT)
        fp.write(pack_format(LASZIP_SPECIAL_EVLR_FORMAT,
                             {'number_of_special_evlrs': 1,
                              'offset_to_special_evlrs': at}))
    return at


def _user_id(value):
    """A record's user id as a reader will key it by.

    Runs the value through `pack_cstr` and back through `cstr`, so a name
    given padded or given as text becomes the same bytes a reader will key by
    -- and a name too long to fit is refused here rather than deeper down.
    """
    return cstr(pack_cstr(value, 16))


# ---------------------------------------------------------------------------
# Variable length records.
#
# A record is a mapping -- the shape Reader hands back -- with `user_id`,
# `record_id` and `data`, and optionally `description` and `reserved`. The
# writer takes the payload length from the payload rather than from
# `record_length_after_header`, so a record copied from a file whose length
# field lies goes out as the bytes it really has.
#
# They are written in the order given, and the LASzip record last, which is
# where laszip puts its own: it appends to the header's records rather than
# leading with it.
# ---------------------------------------------------------------------------


def _keyed(vlrs):
    """`vlrs` ready to write: records under ``(user_id, record_id)``.

    Accepts a mapping keyed that way, as ``header["variable_length_records"]``
    is, or any iterable of records -- so copying a file's records is handing
    them over as they came. Records ``_keyed`` has already normalized pass
    through unchanged, so the writer can check the extended records both when
    the caller gives them and again when it writes them.

    The key is built here, through ``_user_id``, because here is where a name
    given as text or padded becomes the bytes a reader will look for. Keying
    the records by that normalized name, rather than storing it alongside
    them, lets everything downstream ask for a record by key and get the same
    answer a reader would: there is no second spelling to keep in step.

    File order is insertion order, which is what a dict gives and what the
    records are written in.

    ``_keyed`` drops a LASzip record among them rather than refusing it, as
    laszip does with one too: such a record describes how the file it came
    from was compressed, and the file being written has its own answer to
    that.
    """
    if hasattr(vlrs, 'values'):
        vlrs = vlrs.values()

    records = {}
    for vlr in vlrs:
        key = (_user_id(vlr['user_id']), vlr['record_id'])
        if key == LASZIP_VLR_KEY:
            continue
        if key in records:
            raise ValueError(f"two records claim {key[0]!r} {key[1]}, and a "
                             "reader can only find one of them")
        records[key] = _record(key, bytes(vlr['data']),
                               vlr.get('description', b''),
                               vlr.get('reserved', 0))
    return records


def _records(vlrs):
    """The same, as a list in file order.

    The writer holds the extended records this way because ``writer.evlrs``
    is documented as a list a caller appends to up to the last moment, and
    appending is the one operation a mapping would make the caller spell
    differently. The ordinary records never leave the constructor, so they
    keep their keys.
    """
    return list(_keyed(vlrs).values())


def _add_crs(records, crs, wkt, description):
    """`records` with a projection record for `crs` on the end.

    A caller either hands over a CRS and lets ``_add_crs`` build the record,
    or builds it themselves and passes it among ``vlrs``. Doing both is
    refused: it is two answers to one question, and which of them a reader
    found would be a matter of record order.
    """
    if any(key in records for key in PROJECTION_VLR_KEYS):
        raise ValueError("crs= and a projection record in vlrs are two "
                         "answers to where the points are; give one")
    return {**records, **_keyed([crs_record(crs, wkt=wkt,
                                            description=description)])}


def _record(key, data, description, reserved=0):
    """One record, sized by the payload it holds."""
    return {
        'reserved': reserved,
        'user_id': key[0],
        'record_id': key[1],
        'record_length_after_header': len(data),
        'description': description,
        'data': data,
    }


def _pack_vlr(record):
    """One record on disk: its 54-byte header, then its payload.

    This function checks the ceiling on the payload, because this header is
    what imposes it: the length field is two bytes wide. An extended record,
    whose field is eight, carries anything larger.
    """
    length = record['record_length_after_header']
    if length > MAX_VLR_PAYLOAD:
        raise ValueError(
            f"record {record['user_id']!r} {record['record_id']} holds "
            f"{length} bytes, over the {MAX_VLR_PAYLOAD} a variable length "
            f"record can declare; an extended record is what holds that much")
    return pack_format(VLR_HEADER_FORMAT, record) + record['data']


def _extra_bytes_width(records, declared):
    """How many extra bytes a point carries, given the records and what the
    caller said.

    The "extra bytes" record is the file's own account of them, so when the
    file has one it decides the width and `declared` is only checked against
    it: a file whose descriptor and record length disagree is one nothing can
    read, and refusing it here is cheaper than explaining it later.
    """
    descriptor = records.get(EXTRA_BYTES_VLR_KEY)
    if descriptor is None:
        return 0 if declared is None else declared

    described = _described_width(descriptor['data'])
    if declared is not None and declared != described:
        raise ValueError(
            f"the extra bytes record describes {described} bytes per point, "
            f"but num_extra_bytes is {declared}")
    return described


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
    ``extended_classification_flags`` -- LASzip's rule, which lazpy matches so
    the files it writes are byte for byte the ones laszip would have written.

    Some header fields are not knowable until the last point has been written:
    the point count, the counts by return number, the bounding box, and where
    the extended records that follow the points begin. They are filled in by
    ``close()``, which is why the output has to be seekable. Everything else
    can be set through ``writer.header`` until then, so long as it does not
    change how long the header is.
    """

    #: LASzip's own version, which is what the LASzip VLR records: the encoding
    #: of the point block, not the software that produced it. lazpy names
    #: itself in the header's ``generating_software`` instead.
    LASZIP_VERSION = (3, 5, 1)

    def __init__(self, filename, point_format, *, scales=DEFAULT_SCALES,
                 offsets=(0.0, 0.0, 0.0), compressed=None, compressor=None,
                 laz_version=None, chunk_size=50000, num_extra_bytes=None,
                 version_minor=None, system_identifier=b'',
                 generating_software=None, crs=None, vlrs=(), evlrs=(),
                 vlr_description=b'lazpy', file_creation=(0, 0),
                 compatibility=False, user_data_in_header=b'',
                 user_data_after_header=b''):
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
        random access costs on read-back. :data:`ADAPTIVE_CHUNK_SIZE` leaves
        the boundaries to the caller, who ends each chunk with ``chunk()``.
        The chunk size goes into the LASzip VLR whatever the container, as
        laszip records it, but POINTWISE has no chunks for it to describe.

        ``scales`` and ``offsets`` are how the integer coordinates of a point
        become georeferenced ones; they are recorded in the header and applied
        to nothing here, since points are written as they are given.

        ``crs`` is the coordinate reference system the points are in --
        anything ``pyproj.CRS.from_user_input`` takes, so a
        :class:`pyproj.CRS`, an ``"EPSG:2927"``, a WKT string. It is written
        as an OGC WKT record for the LAS 1.4 point formats, which is what
        those call for, and as GeoTIFF geokeys otherwise::

            with Writer("out.laz", point_format=1,
                        crs=reader.crs) as writer:

        Passing a projection record in ``vlrs`` as well is refused, since a
        file can only have one and a reader would find whichever came first.

        ``vlrs`` are the variable length records the file carries besides the
        LASzip one, which is the writer's own: an "extra bytes" descriptor,
        whatever a file being copied had, a projection record built by hand
        with :func:`~lazpy.crs_record` rather than handed over as ``crs``.
        They are taken here rather than later because the header records how
        far past itself the points begin.

        ``evlrs`` are the extended records, which LAS 1.4 keeps behind the
        point data and which may hold payloads no ordinary record can. They
        are written by ``close()``, so unlike ``vlrs`` they need not all be
        known here: ``writer.evlrs`` is the list, and appending to it up to
        the last moment is as good as passing it in. The writer reads the
        records passed in here rather than at the end, so records taken from
        a reader -- whose payloads are read on demand -- do not depend on
        that reader outliving this writer.

        ``num_extra_bytes`` is how many opaque bytes ride on the end of each
        point. It defaults to what the "extra bytes" record among ``vlrs``
        describes, and to none when there is no such record.

        ``user_data_in_header`` is anything the caller keeps between the
        header fields LAS defines and the records behind them, which is where
        a producer may put whatever the format has no field for; it lengthens
        the header by exactly its own length, and comes back as
        ``header["user_data"]``. ``user_data_after_header`` is the same for
        the space between the last record and the first point, which the
        header states by aiming ``offset_to_point_data`` past it. laszip
        carries both under those names.

        ``compatibility`` writes a LAS 1.4 point format as the legacy file it
        can be disguised as, for readers that predate LAS 1.4; see
        :meth:`_disguise_as_legacy` for what that costs.

        ``system_identifier``, ``generating_software`` and ``vlr_description``
        are free text the file carries about its own provenance.
        """
        self.fp = None
        self.header = None
        self._writer = None
        self._closed = False
        self._failure = None
        self._owns_fp = False
        self._compatibility_at = None

        records = _keyed(vlrs)
        num_extra_bytes = _extra_bytes_width(records, num_extra_bytes)
        if num_extra_bytes < 0:
            raise ValueError("num_extra_bytes cannot be negative")

        self.point_format = point_format
        #: The point format the file itself declares, which is the one asked
        #: for unless it is being disguised as a legacy one.
        self.written_format = point_format
        self.compat_layout = None
        if compatibility:
            records, num_extra_bytes = self._disguise_as_legacy(
                records, num_extra_bytes, version_minor, vlr_description)

        # raises for a point format there is no layout for, so everything
        # below can look one up
        record_length = (_POINT_FORMATS.get(self.written_format, (0,))[0]
                         + num_extra_bytes)
        self.items = items_for_point_format(self.written_format, record_length)
        point14 = _POINT_FORMATS[self.written_format][5]

        # after the disguise, so a LAS 1.4 file written as a legacy one states
        # its projection the way the version it claims to be would
        if crs is not None:
            records = _add_crs(records, crs, wkt=point14,
                               description=vlr_description)

        if compressed is None:
            compressed = not str(filename).lower().endswith('.las')
        if version_minor is None:
            version_minor = _default_version_minor(self.written_format)
        self._check_version(self.written_format, version_minor)

        #: The extended records to write behind the point data, which may be
        #: added to until the file is closed.
        self.evlrs = _records(evlrs)
        if self.evlrs:
            self._check_extended(version_minor)

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
            records[LASZIP_VLR_KEY] = self._laszip_record(vlr_description)
        # packed before the header, because the header records how far past
        # itself the points begin
        block = b''.join(_pack_vlr(record) for record in records.values())
        block += bytes(user_data_after_header)

        self._open(filename)
        try:
            self.header = self._build_header(
                record_length, version_minor, len(records), len(block),
                scales, offsets, system_identifier, generating_software,
                file_creation, user_data=bytes(user_data_in_header),
                padding=bytes(user_data_after_header))
            # LAS 1.4 notes a WKT record in the header, as compat.py does for
            # a file being upgraded into one
            if crs is not None and point14:
                self.header['global_encoding'] |= WKT_GLOBAL_ENCODING_BIT
            # the record the counts go back into leads the block, which is
            # where _disguise_as_legacy put it
            if self.compatibility:
                self._compatibility_at = (self.header['header_size']
                                          + VLR_HEADER_SIZE)
            self.fp.write(self._pack_header(self.header))
            self.fp.write(block)
            self._writer = PointWriter(self.fp, self.items,
                                       int(self.compressor),
                                       chunk_size=chunk_size,
                                       compatibility=self.compat_layout)
        except Exception:
            self._close_file()
            raise

    # -- construction ----------------------------------------------------

    def _disguise_as_legacy(self, records, num_extra_bytes, version_minor,
                            description):
        """Set this writer up to hide a LAS 1.4 point format in a legacy file.

        laszip's ``laszip_request_compatibility_mode()`` on the writing side:
        the points go out as format 1, 3, 4 or 5, which is all a LAS 1.2 or
        1.3 file may hold, with the fields only formats 6-10 have folded into
        five extra bytes on the end of each record -- seven, where there is a
        near-infrared band to hide as well. Two records say so, and are added
        to `records` here: the "lascompatible" one holding the LAS 1.4 header
        fields the legacy header cannot state, and the "extra bytes" one
        naming the hidden fields among a point's real extra bytes.

        The disguise costs whatever the legacy fields are too narrow to hold.
        The scan angle keeps a rank and the remainder rides in the extra
        bytes; the return numbers and the classification keep as much as their
        narrower fields can and the difference rides along too. A reader that
        puts them back together -- lazpy's own, or laszip asked for the same
        mode -- gets the LAS 1.4 points that went in. One that does not sees a
        legacy file whose points are as nearly right as a legacy file can
        make them.

        Returns the records to write -- the two that describe the disguise
        leading them -- and the number of extra bytes a record now holds.
        """
        if self.point_format not in _DISGUISED_FORMAT:
            raise UnsupportedFileError(
                f"compatibility mode is for the LAS 1.4 point formats 6 to "
                f"10, not {self.point_format}")
        if version_minor is not None and version_minor >= 4:
            raise UnsupportedFileError(
                "compatibility mode is how a LAS 1.4 point format reaches a "
                "file that predates it; a LAS 1.4 file writes it as it is")

        self.written_format = _DISGUISED_FORMAT[self.point_format]

        # what the caller's own extra bytes are already described as, if they
        # are: the hidden fields go behind them, and can only be placed by a
        # record that accounts for everything in front of them
        rest = dict(records)
        described = rest.pop(EXTRA_BYTES_VLR_KEY, None)
        descriptor, self.compat_layout, num_extra_bytes = _disguise(
            self.point_format, num_extra_bytes,
            described['data'] if described else None)

        # the two that describe the disguise lead, as laszip writes them, and
        # the descriptor that was among the caller's own is now one of them
        disguise = {
            LASCOMPATIBLE_VLR_KEY: _record(
                LASCOMPATIBLE_VLR_KEY, _compatibility_payload(), description),
            EXTRA_BYTES_VLR_KEY: _record(
                EXTRA_BYTES_VLR_KEY, descriptor, description),
        }
        return {**disguise, **rest}, num_extra_bytes

    @staticmethod
    def _check_version(point_format, version_minor):
        if version_minor not in (0, 1, 2, 3, 4):
            raise UnsupportedFileError(
                f"lazpy writes LAS 1.0 to 1.4, not 1.{version_minor}")
        minimum = _min_version_minor(point_format)
        if version_minor < minimum:
            raise UnsupportedFileError(
                f"point data format {point_format} needs LAS 1.{minimum}")

    @staticmethod
    def _check_extended(version_minor):
        """Refuse extended records to a file that cannot point at them."""
        if version_minor < 4:
            raise UnsupportedFileError(
                f"extended variable length records need LAS 1.4, and this is "
                f"a LAS 1.{version_minor} file, whose header has no fields to "
                f"say where they are")

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
                      generating_software, file_creation, user_data,
                      padding):
        header_size = _header_size(version_minor, len(user_data))
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
            # written behind the fields above, which is what makes the header
            # longer than its version's tables
            'user_data': user_data,
            # what was written behind the records rather than in the header,
            # so that a reader copying this file on finds it where it found
            # the source's. Already written by the time this is built, unlike
            # every other field here.
            'user_data_after_header': padding,
            'number_of_variable_length_records': num_records,
            # the high bit is what tells a reader the points are compressed
            'point_data_format_id': (self.written_format
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
        user_data = header['user_data']
        declared = _header_size(version_minor, len(user_data))
        if declared != header['header_size']:
            raise LazError(
                f"a LAS 1.{version_minor} header and {len(user_data)} bytes "
                f"of user data are {declared} bytes, but this file declares "
                f"{header['header_size']}")
        return b''.join(pack_format(fmt, header)
                        for fmt in header_formats(version_minor)) + user_data

    def _laszip_record(self, description):
        """This file's LASzip record: what it says, wrapped as a record.

        ``headers._pack_laszip_record`` builds the payload, and lives beside
        the parse it inverts."""
        major, minor, revision = self.LASZIP_VERSION
        payload = _pack_laszip_record({
            'compressor': self.compressor,
            'coder': Coder.ARITHMETIC,
            'version_major': major,
            'version_minor': minor,
            'version_revision': revision,
            # laszip sets the low bit for a file written in compatibility
            # mode, and nothing else in the field. A reader does not need the
            # bit -- the two records declare the disguise themselves, which is
            # the only way an uncompressed disguised file can declare it --
            # but lazpy sets it anyway, as laszip does.
            'options': 1 if self.compatibility else 0,
            'chunk_size': _as_i32(self.chunk_size),
            'number_of_special_evlrs': -1,      # none, as laszip writes it
            'offset_to_special_evlrs': -1,
        }, self.items)

        return _record(LASZIP_VLR_KEY, payload, description)

    # -- writing ---------------------------------------------------------

    def write(self, point):
        """Append one point, as a :class:`Point` or as its record bytes.

        Raises ValueError once the writer is closed.
        """
        self._writer.write(point)

    def write_arrays(self, columns, count=None):
        """Append points from numpy arrays, one per field.

        The inverse of :meth:`Reader.arrays`, and takes what that returns:
        ``{name: array}``, keyed by the same field names::

            with Reader("in.laz") as reader, Writer("out.laz", 1) as writer:
                while reader.index < reader.num_points:
                    writer.write_arrays(reader.arrays(count=1_000_000))

        That conversion loop is what the method is for. Reading in bulk has
        never had a counterpart here, so a conversion ran at the speed of
        ``write()`` -- a Python call, a type check and a point object each
        time -- however fast the reading side went.

        Fields that ``columns`` does not name are written as zero, so a caller
        who reads four fields and writes them back gets a file whose other
        fields are empty rather than one that repeats the last point. Names
        are those of :meth:`Reader.arrays`, ``extra_bytes`` included.

        Needs numpy, as the array side of reading does.
        """
        np = _numpy()
        if count is None:
            count = min((len(column) for column in columns.values()),
                        default=0)
        if not count:
            return

        # The fields packed several to a byte are given back unpacked, so they
        # are put back together here: one byte per group, or'd from whichever
        # of its fields the caller named.
        targets, bytes_by_offset = [], {}
        for name, column in columns.items():
            field = _array_field(name, self.num_extra_bytes)
            column = np.ascontiguousarray(column[:count])
            if field.mask is None:
                if column.dtype != np.dtype(field.dtype):
                    column = column.astype(field.dtype)
                targets.append((column, field.offset,
                                column.itemsize * field.width))
                continue
            packed = bytes_by_offset.get(field.offset)
            if packed is None:
                packed = bytes_by_offset[field.offset] = np.zeros(
                    count, dtype=field.dtype)
                targets.append((packed, field.offset, 1))
            packed |= ((column.astype(field.dtype) & field.mask)
                       << field.shift)

        self._writer.write_from(targets, count)

    def unscale(self, x, y, z):
        """The integer coordinates a point standing at ``(x, y, z)`` holds.

        The inverse of :meth:`Reader.scale`, and ``laszip_set_coordinates()``:
        it takes the georeferenced coordinate a survey is in and returns what
        a point stores, through this file's own scales and offsets.

            >>> X, Y, Z = writer.unscale(x, y, z)       # doctest: +SKIP
            >>> writer.write(Point(X=X, Y=Y, Z=Z, classification=2))

        A half scale unit rounds away from zero, which is what laszip does and
        not what Python's round() does. A coordinate these scales and offsets
        cannot reach raises rather than wrapping into a point somewhere else
        entirely -- laszip checks the same thing once, over the bounding box,
        in laszip_check_for_integer_overflow(); :func:`auto_offsets` is how to
        pick offsets a survey does fit inside.
        """
        # read out of the header rather than cached as Reader caches them:
        # a caller may set an offset through writer.header until close
        header = self.header
        stored = []
        for value, axis in zip((x, y, z), 'xyz'):
            scale = header[f'{axis}_scale_factor']
            offset = header[f'{axis}_offset']
            quantized = _quantize((value - offset) / scale)
            if not _I32_MIN <= quantized <= _I32_MAX:
                raise ValueError(
                    f"{axis} = {value} is {quantized} at this file's scale "
                    f"of {scale} and offset of {offset}, which no point can "
                    f"hold; a coarser scale or a nearer offset would")
            stored.append(quantized)
        return tuple(stored)

    def chunk(self):
        """Close the open chunk, for variable-size chunking.

        Only meaningful for a file opened with
        ``chunk_size=ADAPTIVE_CHUNK_SIZE``, where
        the boundaries are the caller's to choose.
        """
        self._writer.chunk()

    def close(self):
        """Finish the point block, write the extended records behind it, and
        fill in the header fields that needed every point to be known.

        ``close()`` checks everything the caller can still put right before it
        finishes the point block, so a mistake in the header or the extended
        records is something to correct and close again rather than something
        that costs the file.

        Idempotent once it has worked, and not over a failure: a close that
        raised leaves the file unfinished, and every later close says so
        rather than returning as though it had been finished.
        """
        if self._failure is not None:
            raise LazError(
                "this writer was left unfinished by a close that failed, and "
                "the file it wrote is missing the header fields only close "
                "can fill in") from self._failure
        if self._closed:
            return
        self._check_closable()
        try:
            self._writer.done()
            self._write_extended_records()
            self._patch_header()
        except BaseException as exc:
            # BaseException, so that an interrupt counts too: it leaves the
            # file exactly as unfinished as any other failure does. What is
            # raised on a later close names that state rather than repeating
            # this, which would report an interrupt where none was asked for
            # -- and would be uncatchable by the caller's except Exception.
            self._failure = exc
            raise
        finally:
            self._close_file()
        self._closed = True

    def _check_closable(self):
        """Everything close() needs from what the caller set, asked before the
        point block is finished rather than after.

        The extended records, the LAS version that permits them, and the
        header's declared length are all settled the moment the caller sets
        them, but the writer used to discover a problem with any of them only
        after done() had written the chunk table -- past the point where the
        file can still be saved, so a header field edited to an impossible
        length cost every point written. Asked here, nothing has happened yet
        and the writer is still closable.

        _records is built to be run twice rather than to cache its result: it
        passes records it has already normalized through unchanged, so the
        writer can check the extended records when the caller gives them and
        again when it writes them. The header is packed and thrown away, which
        costs a few hundred bytes and is the only way to ask whether it still
        fits the length the file has already declared.
        """
        records = _records(self.evlrs)
        if records:
            self._check_extended(self.header['version_minor'])
        self._pack_header(self.header)

    def _close_file(self):
        if self.fp is not None and self._owns_fp:
            self.fp.close()
        self.fp = None

    def _write_extended_records(self):
        """Write the extended records behind the point block, and aim the
        header at them.

        They are cheap because they go behind the point block: everything in
        front of them is already written and none of it moves, so the two
        header fields that address them are two more for _patch_header to
        fill in.
        """
        records = _records(self.evlrs)
        if not records:
            return
        self._check_extended(self.header['version_minor'])

        start = self.fp.tell()
        for record in records:
            # the payload goes out on its own rather than joined to its
            # header, since an extended record is exactly what holds a
            # payload too big to want a second copy of
            self.fp.write(pack_format(EVLR_HEADER_FORMAT, record))
            self.fp.write(record['data'])
        self.header['start_of_first_extended_variable_length_record'] = start
        self.header['number_of_extended_variable_length_records'] = \
            len(records)

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
        # for when it reads a file back. A disguised file has only the legacy
        # fields to say it in, and says it there.
        legacy = self.written_format < 6 and count <= 0xFFFFFFFF
        header['number_of_point_records'] = count if legacy else 0
        header['number_of_points_by_return'] = (list(by_return[1:6]) if legacy
                                                else [0] * 5)
        if header['version_minor'] >= 4:
            header['extended_number_of_point_records'] = count
            header['extended_number_of_points_by_return'] = \
                list(by_return[1:16])
        elif self.compatibility:
            # the counts are of LAS 1.4 return numbers: the legacy field
            # states the five it has room for, and the "lascompatible" record
            # states the rest
            self._patch_compatibility_record(count, by_return[1:16])

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

    def _patch_compatibility_record(self, count, by_return):
        """Rewrite the "lascompatible" record with the LAS 1.4 counts.

        It went out before the first point with the count fields zero, because
        that is where a variable length record has to be; it is a fixed number
        of bytes, so the finished one goes over it.
        """
        payload = _compatibility_payload(count, by_return)
        end = self.fp.tell()
        self.fp.seek(self._compatibility_at)
        self.fp.write(payload)
        self.fp.seek(end)

    @property
    def compatibility(self):
        """Whether this file is a LAS 1.4 one in a legacy disguise: the
        format it wears is not the format it holds."""
        return self.written_format != self.point_format

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
