"""LAS 1.4 compatibility mode: recognising a legacy file that is a LAS
1.4 file in disguise, and rewriting its header as the file it stands
in for."""

from collections import namedtuple

from ._utils import unsigned_int
from .formats import (LASCOMPATIBLE_VLR_KEY, EXTRA_BYTES_VLR_KEY,
                      UnsupportedFileError, _POINT_FORMATS)
from .headers import (EXTRA_BYTES_ATTRIBUTE_FORMAT,
                      EXTRA_BYTES_ATTRIBUTE_SIZE, HEADER_FORMAT_13,
                      HEADER_FORMAT_14, format_size, unpack_format,
                      _header_size)

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
