"""Read and write LAS and LAZ point cloud files.

Header and variable-length-record parsing and construction happen here;
everything from the first point onward is handled by the ``lazpy._cpylaz`` C
extension, which is a port of LASzip.

    >>> reader = Reader("cloud.laz")
    >>> reader.num_points
    43271750
    >>> for point in reader:
    ...     print(point.X, point.Y, point.Z, point.classification)

    >>> reader.xyz()                    # or whole columns, if numpy is around
    array([[1500.04, 1700.19, 33.98], ...])

    >>> with Writer("out.laz", point_format=1) as writer:
    ...     writer.write(Point(X=1, Y=2, Z=3, gps_time=4.0))

LAS file specification
    1.2: www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf
    1.4: www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf
"""

from collections import namedtuple
from collections.abc import Mapping
from enum import Enum, IntEnum
import io
import os

# Point, PointReader and PointWriter are re-exported: they are the API for
# driving the container directly, without a Reader or Writer over it.
from ._cpylaz import (PointReader, PointWriter, Point,  # noqa: F401
                      SpatialIndex, LazError)
from ._utils import (unsigned_int, signed_int, u32_array, u64_array, double,
                     cstr, PACKERS)

__all__ = ["Reader", "Writer", "Point", "Chunking", "Compressor", "Coder",
           "ItemType", "Selective", "LazError", "UnsupportedFileError",
           "ExtendedVariableLengthRecord"]

LASZIP_VLR_RECORD_ID = 22204
LASZIP_VLR_USER_ID = b"laszip encoded"

# How a record is looked up: variable_length_records is keyed by
# (user_id, record_id), since LAS namespaces records by user id and a bare id
# collides. LASZIP_VLR_KEY and LASCOMPATIBLE_VLR_KEY are where that stops being
# hypothetical -- both records claim id 22204 and sit in the same file.
LASZIP_VLR_KEY = (LASZIP_VLR_USER_ID, LASZIP_VLR_RECORD_ID)

# The two records a LAS 1.4 compatibility-mode file carries beside the LASzip
# one. The "extra bytes" record is the ordinary LAS one; it is only special
# here because compatibility mode describes the fields it hides in it there.
LASCOMPATIBLE_VLR_KEY = (b"lascompatible", 22204)
EXTRA_BYTES_VLR_KEY = (b"LASF_Spec", 4)

# The extended record an index appended to the file itself travels in. Nothing
# in the LAS header points at it; the LASzip record's "special EVLR" fields do.
LASINDEX_EVLR_KEY = (b"LAStools", 30)


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


class Chunking(Enum):
    """How a file's points are grouped, which is what random access costs.

    Not a field of any record: POINTWISE and POINTWISE_CHUNKED carry the same
    chunk size in the LASzip VLR, and the adaptive case declares it as -1, so
    this is read off the compressor and that field together.
    """
    NONE = 'none'          # one stream of points; seeking decodes forward
    FIXED = 'fixed'        # chunks of Reader.chunk_size points each
    ADAPTIVE = 'adaptive'  # variable-size chunks; the chunk table has them


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
        items.append((ItemType.WAVEPACKET14 if point14
                      else ItemType.WAVEPACKET13, 29, 0))
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


def _header_size(version_minor):
    """How long a LAS 1.`version_minor` header is, by its own tables."""
    return sum(format_size(fmt) for fmt in header_formats(version_minor))


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
# number_of_special_evlrs and offset_to_special_evlrs address extended records
# held apart from the rest, which the LAS header does not count and nothing
# else points at. laszip writes -1 in both, and so does lazpy; what does write
# them is `lasindex -append`, and following them is how an index appended to a
# file is found. See Reader._appended_index_data.
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


# ---------------------------------------------------------------------------
# LAS 1.4 compatibility mode.
#
# laszip can put a LAS 1.4 point in a file that predates LAS 1.4: the point is
# written as format 1, 3, 4 or 5, which is all a 1.2 or 1.3 file may hold, and
# the fields only formats 6-10 have are packed into five extra bytes on the end
# of the record -- seven, if there is a near-infrared band to hide as well. Two
# variable length records say so: a "lascompatible" one holding the LAS 1.4
# header fields the legacy header has no room for, and the ordinary "extra
# bytes" one, which names the hidden fields among the real extra bytes.
#
# Reading such a file means putting the points back together and reporting the
# LAS 1.4 file it stands in for. laszip does this only when asked -- see
# laszip_request_compatibility_mode() -- and lazpy always does, because the
# alternative is handing back points whose 1.4 fields are zero when the file
# does carry them.
# ---------------------------------------------------------------------------

# The compatibility record: two version numbers and a spare, then the LAS 1.4
# header tail a 1.2 or 1.3 header has nowhere to put -- which is exactly the
# tables that describe it, so it is those rather than a second copy of them.
# laszip also writes a form 18 bytes longer, for LAS 1.5.
COMPATIBILITY_RECORD_FORMAT = (
    ('laszip_version', 2, unsigned_int),
    ('compatible_version', 2, unsigned_int),
    ('unused', 4, unsigned_int),
) + HEADER_FORMAT_13 + HEADER_FORMAT_14
COMPATIBILITY_RECORD_SIZE = format_size(COMPATIBILITY_RECORD_FORMAT)

# What `compatible_version` in that record says the file stands in for: laszip
# writes 3 for a LAS 1.4 file and 4 for a LAS 1.5 one. lazpy has no LAS 1.5
# header to report, so a 1.5 file is left as the legacy file it says it is
# rather than half-upgraded into something it is not.
_COMPATIBLE_VERSION_14 = 3

# The four fields every compatibility-mode file hides, in the order laszip
# appends them, and the fifth it hides only for a point format with a
# near-infrared band. Names are what an "extra bytes" descriptor calls them.
_COMPATIBILITY_ATTRIBUTES = (b"LAS 1.4 scan angle",
                             b"LAS 1.4 extended returns",
                             b"LAS 1.4 classification",
                             b"LAS 1.4 flags and channel")
_NIR_ATTRIBUTE = b"LAS 1.4 NIR band"

# Where those five live in a point's extra bytes, which is all the point reader
# needs to put a point back together. Named rather than positional because the
# order is agreed with the C reader, which unpacks this straight into fields.
CompatibilityLayout = namedtuple(
    "CompatibilityLayout",
    "scan_angle extended_returns classification flags_and_channel nir")

# What a legacy point format becomes once the hidden fields are folded back in,
# by whether the file hid a near-infrared band too. This is laszip's own
# branching, including for formats 4 and 5, which it treats as one case -- in a
# file laszip wrote, only the one that had RGB to begin with can have a NIR
# band, so the unreachable halves never come up.
_UPGRADED_FORMAT = {1: (6, 6), 3: (7, 8), 4: (9, 10), 5: (9, 10)}

# One attribute descriptor out of an "extra bytes" record: a fixed 192 bytes,
# of which only the type, its option byte and the name matter here. The rest is
# no-data/min/max/scale/offset, which describe values rather than layout.
EXTRA_BYTES_ATTRIBUTE_FORMAT = (
    ('reserved', 2, unsigned_int),
    ('data_type', 1, unsigned_int),
    ('options', 1, unsigned_int),
    ('name', 32, cstr),
)
EXTRA_BYTES_ATTRIBUTE_SIZE = 192

# The widths of data types 1 to 10. Type 0 means undocumented bytes, as many as
# the option byte says; 11 to 30 were arrays of two and three, deprecated in
# 2018 and gone from LASzip's own writer, but still sized by the same table.
_ATTRIBUTE_SIZES = (1, 1, 2, 2, 4, 4, 8, 8, 4, 8)

# An attribute, as _extra_bytes_attributes reports it: where its descriptor
# sits in the record, and where and how wide the attribute is in a point.
_Attribute = namedtuple("_Attribute", "name offset start size")


def _attribute_size(data_type, options):
    """The width of an attribute of this type, in a point's extra bytes."""
    if data_type == 0:
        return options
    if data_type > 3 * len(_ATTRIBUTE_SIZES):
        raise UnsupportedFileError(
            f"unknown extra bytes attribute data type {data_type}")
    # the deprecated types are the ten scalar ones over again, two and three
    # to an attribute
    dimensions, scalar = divmod(data_type - 1, len(_ATTRIBUTE_SIZES))
    return _ATTRIBUTE_SIZES[scalar] * (dimensions + 1)


def _extra_bytes_attributes(data):
    """The attributes an "extra bytes" record describes, in file order.

    Each is an :class:`_Attribute`. The descriptors are a running layout --
    every attribute begins where the one before it ended -- which is what
    makes `start` derivable rather than stated.
    """
    start = 0
    for offset in range(0, len(data) - EXTRA_BYTES_ATTRIBUTE_SIZE + 1,
                        EXTRA_BYTES_ATTRIBUTE_SIZE):
        fields, _ = unpack_format(EXTRA_BYTES_ATTRIBUTE_FORMAT, data, offset)
        size = _attribute_size(fields['data_type'], fields['options'])
        yield _Attribute(fields['name'], offset, start, size)
        start += size


def _compatibility_layout(header):
    """A compatibility-mode file's :class:`CompatibilityLayout`, or None.

    None means this is not a compatibility-mode file, which is the answer for
    almost every file. ``nir`` is -1 for one that hid no near-infrared band.

    The test is laszip's: a LAS 1.2 or 1.3 file, a point format that could
    have been an extended one, a compatibility record long enough to hold what
    it should, and an "extra bytes" record naming all four of the hidden
    fields. Anything less is a file laszip reads as the legacy file it says it
    is, and so does this.
    """
    if header['version_major'] != 1 or header['version_minor'] >= 4:
        return None
    if header['point_data_format_id'] not in _UPGRADED_FORMAT:
        return None

    vlrs = header['variable_length_records']
    record = vlrs.get(LASCOMPATIBLE_VLR_KEY)
    if record is None or len(record['data']) < COMPATIBILITY_RECORD_SIZE:
        return None
    fields, _ = unpack_format(COMPATIBILITY_RECORD_FORMAT, record['data'])
    if fields['compatible_version'] != _COMPATIBLE_VERSION_14:
        return None

    attributes = vlrs.get(EXTRA_BYTES_VLR_KEY)
    if attributes is None:
        return None
    starts = {a.name: a.start
              for a in _extra_bytes_attributes(attributes['data'])}
    if not all(name in starts for name in _COMPATIBILITY_ATTRIBUTES):
        return None
    return CompatibilityLayout(
        *(starts[name] for name in _COMPATIBILITY_ATTRIBUTES),
        starts.get(_NIR_ATTRIBUTE, -1))


def _upgrade_to_las_14(header, layout, num_extra_bytes):
    """Rewrite a compatibility-mode header as the LAS 1.4 one it stands in for.

    `layout` is what :func:`_compatibility_layout` returned and
    `num_extra_bytes` how many extra bytes a point has left once the hidden
    fields are taken out of them, which is what the new record length is built
    from. The two records that made the file a compatibility-mode file go away,
    since after this there is nothing left for them to describe.
    """
    record = header['variable_length_records'].pop(LASCOMPATIBLE_VLR_KEY)
    header['number_of_variable_length_records'] -= 1
    fields, _ = unpack_format(COMPATIBILITY_RECORD_FORMAT, record['data'])

    # The LAS 1.4 header fields, out of the record that carried them -- which
    # holds them under the names the header itself uses, since it is described
    # by the header's own tables. The two that address extended records are
    # read as zero however they were written, as laszip reads them:
    # compatibility mode cannot carry any.
    for name, _, _ in HEADER_FORMAT_13 + HEADER_FORMAT_14:
        header[name] = fields[name]
    header['start_of_waveform_data_packet_record'] = 0
    header['start_of_first_extended_variable_length_record'] = 0
    header['number_of_extended_variable_length_records'] = 0

    _drop_compatibility_attributes(header)

    # the LAS 1.4 header is longer than the one the file has by exactly the
    # tables it has that the file's version does not
    grew = _header_size(4) - _header_size(header['version_minor'])
    header['header_size'] += grew
    header['offset_to_point_data'] += grew
    header['version_minor'] = 4

    # LAS 1.4 notes an OGC WKT record in the header; 1.2 and 1.3 had no bit
    # to say it with, so it is only knowable from the record itself
    if (b'LASF_Projection', 2112) in header['variable_length_records']:
        header['global_encoding'] |= 1 << 4

    point_format = _UPGRADED_FORMAT[
        header['point_data_format_id']][layout.nir != -1]
    header['point_data_format_id'] = point_format
    header['point_data_record_length'] = (_POINT_FORMATS[point_format][0]
                                          + num_extra_bytes)


def _drop_compatibility_attributes(header):
    """Take the hidden LAS 1.4 fields out of the "extra bytes" record.

    They were only ever there to describe the file's disguise, so once the
    disguise is off they would be describing bytes a caller no longer has. A
    record that described nothing else goes away with them.
    """
    vlr = header['variable_length_records'][EXTRA_BYTES_VLR_KEY]
    hidden = frozenset(_COMPATIBILITY_ATTRIBUTES + (_NIR_ATTRIBUTE,))
    kept = b''.join(vlr['data'][a.offset:a.offset + EXTRA_BYTES_ATTRIBUTE_SIZE]
                    for a in _extra_bytes_attributes(vlr['data'])
                    if a.name not in hidden)
    if kept:
        vlr['data'] = kept
        vlr['record_length_after_header'] = len(kept)
    else:
        del header['variable_length_records'][EXTRA_BYTES_VLR_KEY]
        header['number_of_variable_length_records'] -= 1


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
    """Sequential and random access to the points of a LAS or LAZ file.

    ``header`` is what the file says about itself, field by field, plus its
    variable length records keyed by ``(user_id, record_id)``. It is the file's
    own account rather than the bytes on disk in one case: a LAS 1.4
    compatibility-mode file is reported as the LAS 1.4 file it stands in for,
    so ``header_size`` and ``offset_to_point_data`` describe that file and not
    where anything sits in this one.
    """

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
        """How many points share a chunk, or None if that is not a number.

        None for an unchunked file -- plain LAS, or the POINTWISE container,
        which is a single stream however large a chunk size the LASzip VLR
        happens to carry -- and None for adaptive chunking, where the chunks
        are whatever sizes the writer chose and only the chunk table knows
        them. Check :attr:`chunking` to tell those two apart; the underlying
        ``PointReader.chunk_starts`` has the boundaries themselves, once
        reading has read the table.
        """
        if self.chunking is not Chunking.FIXED:
            return None
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
        """Whether a rectangle query can skip past most of the file."""
        return self.spatial_index is not None

    def points_within(self, min_x, min_y, max_x, max_y):
        """Yield the points inside a rectangle, in file order.

        The rectangle is half-open -- a point counts when ``min_x <= x <
        max_x`` and ``min_y <= y < max_y`` -- in the georeferenced coordinates
        :meth:`scale` returns, not the integers a point stores. Adjoining
        rectangles therefore partition the points rather than sharing the ones
        on the seam, which is what laszip's own rectangle query does.

        With a spatial index this decodes only the runs of points the index
        says could be in the rectangle, which for a small rectangle in a large
        file is a small fraction of it. Without one it is a filtered full scan:
        the same points, at the cost of reading everything.

        As with :meth:`points`, each iteration yields the same object with new
        contents; call ``point.copy()`` to keep one. The reader is left
        wherever the last interval ended, so :meth:`seek` before reading
        sequentially again.
        """
        if min_x > max_x or min_y > max_y:
            raise ValueError("rectangle is inside out: "
                             "min must not exceed max")

        index = self.spatial_index
        num_points = self.num_points
        if index is None:
            spans = [(0, num_points - 1)] if num_points else []
        else:
            spans = index.intervals(min_x, min_y, max_x, max_y)

        sx, sy = self._scale_offset[0], self._scale_offset[1]
        ox, oy = self._scale_offset[3], self._scale_offset[4]
        read = self._reader.read          # hoisted: this loop runs per point
        for start, end in spans:
            # an index is data out of a file like any other, and one that names
            # points this file does not have is not worth decoding towards
            if start >= num_points:
                continue
            end = min(end, num_points - 1)
            self.seek(start)
            for _ in range(end - start + 1):
                point = read()
                x = point.X * sx + ox
                if x < min_x or x >= max_x:
                    continue
                y = point.Y * sy + oy
                if y < min_y or y >= max_y:
                    continue
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
        carries, which for a format with extra bytes includes them::

            a = reader.arrays()
            ground = a["Z"][a["classification"] == 2]

        Naming fields reads only those, which is the difference between three
        columns and thirty for a whole file::

            a = reader.arrays("X", "Y", "Z", "gps_time")

        ``red``, ``green``, ``blue`` and ``nir`` are separate columns here,
        where ``Point`` groups them as ``rgb``. ``wave_packet`` and
        ``extra_bytes``, being opaque blobs, come back as ``(count, size)``
        arrays of bytes rather than one column of numbers -- and are most of
        the memory the default costs on the formats that have them.

        Reading starts where the reader is, or at *start* if given, and runs
        to the end of the file unless *count* says otherwise; a *count* past
        the end of the file stops at the end of the file, as slicing does. The
        reader is left after the last point read, so successive calls walk the
        file in blocks -- which is how a file too big to hold at once gets
        read.
        """
        np = _numpy()
        if start is not None:
            self.seek(start)
        remaining = self.num_points - self.index
        count = remaining if count is None else min(count, remaining)

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
            packed.append((name, f))
            out[name] = None            # placeholder: holds its place in out

        self._reader.read_into(targets, count)

        for name, f in packed:
            out[name] = (byte_columns[f.offset] >> f.shift) & f.mask
        return out

    def xyz(self, start=None, count=None):
        """The georeferenced points, as an ``(N, 3)`` array of floats.

        The scale and offset from the header are applied, so these are the
        coordinates the file is about rather than the integers it stores.
        *start* and *count* are as in :meth:`arrays`.
        """
        np = _numpy()
        columns = self.arrays('X', 'Y', 'Z', start=start, count=count)
        # scaled a column at a time into the answer, which is the only
        # full-size float array this makes
        xyz = np.empty((len(columns['X']), 3), dtype=np.float64)
        scales, offsets = self.scales, self.offsets
        for i, name in enumerate('XYZ'):
            np.multiply(columns[name], scales[i], out=xyz[:, i])
            xyz[:, i] += offsets[i]
        return xyz


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

        ``chunk_size`` is how many points share a chunk, which is what random
        access costs on the way back in. ``0xFFFFFFFF`` leaves the boundaries
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
