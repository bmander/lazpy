"""The vocabulary of a LAS or LAZ file: enumerations, errors, and the
point formats with the items each is made of."""

from collections import namedtuple
from enum import Enum, IntEnum

from ._cpylaz import LazError

LASZIP_VLR_RECORD_ID = 22204
LASZIP_VLR_USER_ID = b"laszip encoded"

# How a record is looked up: variable_length_records is keyed by
# (user_id, record_id), since LAS namespaces records by user id and a bare id
# collides. LASZIP_VLR_KEY and LASCOMPATIBLE_VLR_KEY are a real collision, not
# a hypothetical one: both records claim id 22204 and can sit in the same file.
LASZIP_VLR_KEY = (LASZIP_VLR_USER_ID, LASZIP_VLR_RECORD_ID)

# The two records a LAS 1.4 compatibility-mode file carries beside the LASzip
# one. The "extra bytes" record is the ordinary LAS one; it matters here
# because a compatibility-mode file describes its hidden LAS 1.4 fields as
# entries in that record.
LASCOMPATIBLE_VLR_KEY = (b"lascompatible", 22204)
EXTRA_BYTES_VLR_KEY = (b"LASF_Spec", 4)

# The extended record that carries a spatial index appended to the end of the
# file. Nothing in the LAS header points at it; the LASzip record's "special
# EVLR" fields do.
LASINDEX_EVLR_KEY = (b"LAStools", 30)

# Where a file states its coordinate reference system: the GeoTIFF
# GeoKeyDirectory, and the OGC WKT string LAS 1.4 uses in its place for the
# extended point formats. lazpy.crs reads and builds both.
GEOKEY_DIRECTORY_KEY = (b"LASF_Projection", 34735)
WKT_VLR_KEY = (b"LASF_Projection", 2112)
PROJECTION_VLR_KEYS = (GEOKEY_DIRECTORY_KEY, WKT_VLR_KEY)

#: Bit 4 of a header's ``global_encoding``: the file states its coordinate
#: reference system in the WKT record rather than the geokeys. LAS 1.4 added
#: it, so a 1.2 or 1.3 file carrying WKT has no way to say so and the record
#: itself is the only evidence.
WKT_GLOBAL_ENCODING_BIT = 0x10


# LazError is defined in the C extension so decode failures raised from C and
# header failures raised from Python are one catchable category.


class UnsupportedFileError(LazError):
    """Raised for a well-formed file whose encoding lazpy cannot handle."""


class Compressor(IntEnum):
    """How the points are packed, as the LASzip VLR declares it.

    NONE is plain LAS, and the other three are the containers LASzip has had
    in turn. POINTWISE is the original: one stream from the first point to
    the last, with nothing to seek by. The chunked two cut it into
    independently decodable chunks and put a table of their offsets behind
    the points, which is what makes random access cheap. LAYERED_CHUNKED
    additionally splits each chunk into a byte layer per attribute, so a
    reader can skip the ones it was not asked for; the LAS 1.4 point formats
    are written that way.
    """
    NONE = 0
    POINTWISE = 1
    POINTWISE_CHUNKED = 2
    LAYERED_CHUNKED = 3


class Coder(IntEnum):
    """The entropy coder, of which LASzip has only ever defined one."""
    ARITHMETIC = 0


class Chunking(Enum):
    """How a file's points are grouped, which sets what random access costs.

    Not a field of any record: POINTWISE and POINTWISE_CHUNKED carry the same
    chunk size in the LASzip VLR, and the adaptive case declares it as -1, so
    :attr:`Reader.chunking` derives a file's grouping from the compressor and
    that field together.
    """
    NONE = 'none'          # one stream of points; seeking decodes forward
    FIXED = 'fixed'        # chunks of Reader.chunk_size points each
    ADAPTIVE = 'adaptive'  # variable-size chunks; the chunk table has them


#: The ``chunk_size`` that asks :class:`Writer` for adaptive chunking, where
#: the caller ends each chunk itself with ``chunk()``. The reading side gives
#: this state a name, ``Chunking.ADAPTIVE``; the writing side had only the raw
#: number, so a caller who wanted adaptive chunking had to copy a magic
#: constant out of a docstring.
ADAPTIVE_CHUNK_SIZE = 0xFFFFFFFF


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
    """The kinds of item a point record is made of.

    A point is a list of items, each with a type from here, a size in bytes
    and a coder version; the LASzip VLR carries that list, and
    :func:`items_for_point_format` reconstructs it for an uncompressed file.
    POINT10 through WAVEPACKET13 are the LAS 1.0-1.3 groupings, POINT14
    onward their LAS 1.4 replacements, and BYTE and BYTE14 are extra bytes.
    The scalar types are laszip's own and no point format uses them.
    """
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
#
# Named rather than a bare tuple because the callers outside this module want
# one field each -- the record size, or whether the format is an extended one
# -- and reached for it by index, so a column inserted here would have gone
# unnoticed until something read the wrong one.
_PointFormat = namedtuple("_PointFormat",
                          "size gps_time rgb nir wavepacket point14")

_POINT_FORMATS = {n: _PointFormat(*row) for n, row in {
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
}.items()}


def _point_format(point_format):
    """The layout of `point_format`, or the one refusal there is for one.

    Every caller that looks a format up wants the same words for a format
    there is no layout for, and used to spell them itself -- two copies of the
    message, and a third caller that defaulted the lookup instead so that a
    format it could not describe reached the next line as a size of zero.
    """
    try:
        return _POINT_FORMATS[point_format]
    except KeyError:
        raise UnsupportedFileError(
            f"unknown point data format {point_format}") from None


def items_for_point_format(point_format, point_size):
    """Derive the LASzip item layout of an *uncompressed* point record.

    Compressed files carry this list explicitly in the LASzip VLR; for plain
    LAS it has to be reconstructed from the point format and record length,
    with any surplus bytes becoming a trailing BYTE item.

    Returns a list of ``(type, size, version)`` triples with version 0.
    """
    fmt = _point_format(point_format)
    point14 = fmt.point14

    extra = point_size - fmt.size
    if extra < 0:
        raise LazError(
            f"point size {point_size} is {-extra} bytes too small "
            f"for point data format {point_format}")

    items = [(ItemType.POINT14 if point14 else ItemType.POINT10,
              30 if point14 else 20, 0)]
    if fmt.gps_time:
        items.append((ItemType.GPSTIME11, 8, 0))
    if fmt.rgb:
        if point14:
            items.append((ItemType.RGBNIR14, 8, 0) if fmt.nir
                         else (ItemType.RGB14, 6, 0))
        else:
            items.append((ItemType.RGB12, 6, 0))
    if fmt.wavepacket:
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

    Colour arrived in LAS 1.2, wavepackets in 1.3 and the extended point types
    in 1.4; formats 0 and 1 are as old as the format itself. A writer checks a
    caller's requested version against this. The default when the caller gives
    none is a different question -- see :func:`_default_version_minor` -- since
    nobody wants a LAS 1.0 file by accident.
    """
    fmt = _point_format(point_format)
    return (4 if fmt.point14 else 3 if fmt.wavepacket else 2 if fmt.rgb else 0)


def _default_version_minor(point_format):
    """The LAS version a writer given none picks: 1.2 unless the point format
    needs newer.

    The oldest version that can hold a format 0 or 1 point is 1.0, but a file
    written today should not claim to predate the century, and 1.2 is what
    every tool reads. A caller who wants 1.0 or 1.1 asks for it.
    """
    return max(2, _min_version_minor(point_format))
