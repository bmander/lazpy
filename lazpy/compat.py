"""LAS 1.4 compatibility mode, in both directions.

Reading: recognise a legacy file that is a disguised LAS 1.4 file, and
rewrite its header as the file it stands in for. Writing: build the records
that put a file into the disguise.
"""

from collections import namedtuple

from ._utils import unsigned_int
from .extra_bytes import (EXTRA_BYTES_ATTRIBUTE_SIZE, ExtraBytesAttribute,
                          _described_width, _extra_bytes_attributes,
                          _pack_attribute)
from .formats import (LASCOMPATIBLE_VLR_KEY, EXTRA_BYTES_VLR_KEY,
                      WKT_VLR_KEY, WKT_GLOBAL_ENCODING_BIT, _POINT_FORMATS)
from .headers import (HEADER_FORMAT_13, HEADER_FORMAT_14, format_size,
                      pack_format, unpack_format, _header_size)

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
# does carry them. Writing such a file happens only when the caller asks for
# it, as in laszip: a file that need not be disguised should not be. This file
# builds the two records at its foot; the C writer folds each point.
# ---------------------------------------------------------------------------

# The compatibility record: two version numbers and a spare, then the LAS 1.4
# header tail a 1.2 or 1.3 header has nowhere to put. HEADER_FORMAT_13 and
# HEADER_FORMAT_14 already describe exactly those fields, so this format reuses
# those tables rather than restating them.
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

# What laszip puts in the record's other version field: its own build date,
# truncated to the sixteen bits there are for it. Nothing reads it;
# compatible_version above decides everything. Still, a file lazpy writes
# carries the build date of the LASzip release its output matches, the release
# named by Writer.LASZIP_VERSION.
_LASZIP_BUILD_DATE = 260810

# The four fields every compatibility-mode file hides, in the order laszip
# appends them, and the fifth it hides only for a point format with a
# near-infrared band. These are the names an "extra bytes" descriptor gives
# them.
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
# file laszip wrote, only format 5, the one with RGB to begin with, can have a
# NIR band, so the unreachable halves never come up.
_UPGRADED_FORMAT = {1: (6, 6), 3: (7, 8), 4: (9, 10), 5: (9, 10)}

# The other direction: what an extended format is written as when it is
# disguised. Written out here rather than derived by inverting the table above,
# which does not invert: legacy formats 4 and 5 both lead to extended format 9
# or 10, and only knowing which of the two carried RGB resolves the ambiguity.
_DISGUISED_FORMAT = {6: 1, 7: 3, 8: 3, 9: 4, 10: 5}


def _layout_of(data):
    """The :class:`CompatibilityLayout` an "extra bytes" record describes, or
    None if the record does not name all four fields in
    `_COMPATIBILITY_ATTRIBUTES`.

    ``nir`` is -1 for a file that hid no near-infrared band.
    """
    starts = {a.name: a.start for a in _extra_bytes_attributes(data)}
    if not all(name in starts for name in _COMPATIBILITY_ATTRIBUTES):
        return None
    return CompatibilityLayout(
        *(starts[name] for name in _COMPATIBILITY_ATTRIBUTES),
        starts.get(_NIR_ATTRIBUTE, -1))


def _compatibility_layout(header):
    """A compatibility-mode file's :class:`CompatibilityLayout`, or None.

    None means this is not a compatibility-mode file, which is the answer for
    almost every file. ``nir`` is -1 for one that hid no near-infrared band.

    The test is laszip's: a LAS 1.2 or 1.3 file, a point format that could
    have been an extended one, a compatibility record long enough to hold what
    it should, and an "extra bytes" record naming all four of the hidden
    fields. Anything less is a file laszip reads as the legacy file it claims
    to be, and this function does the same.
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
    return _layout_of(attributes['data'])


def _upgrade_to_las_14(header, layout, num_extra_bytes):
    """Rewrite a compatibility-mode header as the LAS 1.4 one it stands in for.

    `layout` is what :func:`_compatibility_layout` returned and
    `num_extra_bytes` is how many extra bytes a point has left once the hidden
    fields are removed; this function builds the new record length from it.
    The two records that made the file a compatibility-mode file go away,
    since after this there is nothing left for them to describe.
    """
    record = header['variable_length_records'].pop(LASCOMPATIBLE_VLR_KEY)
    header['number_of_variable_length_records'] -= 1
    fields, _ = unpack_format(COMPATIBILITY_RECORD_FORMAT, record['data'])

    # The LAS 1.4 header fields, out of the record that carried them -- the
    # record stores them under the names the header itself uses, because it is
    # laid out by the header's own tables. This zeroes
    # start_of_waveform_data_packet_record and the two extended-record fields
    # however they were written, matching laszip: compatibility mode can carry
    # neither waveform data nor extended records.
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
    if WKT_VLR_KEY in header['variable_length_records']:
        header['global_encoding'] |= WKT_GLOBAL_ENCODING_BIT

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


# ---------------------------------------------------------------------------
# Writing one.
#
# The inverse of everything above: given LAS 1.4 points, build the two records
# that describe the disguise and say where in the extra bytes each hidden
# field goes. The C writer folds the points themselves, using the layout
# _disguise returns -- see writer_recode_compat.
#
# laszip does this only when asked, in laszip_request_compatibility_mode(),
# and so does lazpy: a file that need not be disguised should not be.
# ---------------------------------------------------------------------------

# What the descriptor says about each hidden field: its LAS data type, and for
# the scan angle the scale that says what its numbers stand for. The names come
# from _COMPATIBILITY_ATTRIBUTES, the same tuple the read side looks up, so the
# read and write sides cannot drift apart.
_HIDDEN_DESCRIPTION = b"additional attributes"
_HIDDEN_TYPES_AND_SCALES = ((4, 0.006), (1, None), (1, None), (1, None))
_HIDDEN_ATTRIBUTES = tuple(
    ExtraBytesAttribute(name, data_type, _HIDDEN_DESCRIPTION, scale)
    for name, (data_type, scale) in zip(_COMPATIBILITY_ATTRIBUTES,
                                        _HIDDEN_TYPES_AND_SCALES))
_NIR_HIDDEN_ATTRIBUTE = ExtraBytesAttribute(_NIR_ATTRIBUTE, 3,
                                            _HIDDEN_DESCRIPTION)


def _unknown_attributes(count):
    """Descriptors for extra bytes a file has but has never described.

    laszip writes these, and it has to: the attributes in a record are a
    running layout, each beginning where the last ended, so the only way to
    say where the hidden fields start is to account for everything in front
    of them.
    """
    return [ExtraBytesAttribute(f"unknown {i}".encode(), 1,
                                f"unknown {i}".encode())
            for i in range(count)]


def _disguise(point_format, num_extra_bytes, described):
    """Build the "extra bytes" descriptor and layout that writing
    `point_format` as a disguised legacy file takes.

    `described` is the payload of the "extra bytes" record describing the
    caller's own extra bytes, or None if the caller has no such record. When
    it is None, this function describes them with placeholder descriptors,
    because the hidden fields can only be placed by accounting for every byte
    in front of them.

    Returns three things: the payload of the record that now describes a
    point's extra bytes in full, the :class:`CompatibilityLayout` locating the
    hidden fields within them, and how many extra bytes a disguised point
    carries in total -- the caller's own plus the hidden fields.
    """
    if described is None:
        described = b''.join(_pack_attribute(a) for a in
                             _unknown_attributes(num_extra_bytes))

    hidden = list(_HIDDEN_ATTRIBUTES)
    if _POINT_FORMATS[point_format][3]:         # a near-infrared band to hide
        hidden.append(_NIR_HIDDEN_ATTRIBUTE)

    data = described + b''.join(_pack_attribute(a) for a in hidden)
    return data, _layout_of(data), _described_width(data)


def _compatibility_payload(count=0, by_return=(0,) * 15):
    """The "lascompatible" record's payload: the LAS 1.4 header fields a
    legacy header has nowhere to keep.

    The writer builds this twice: once before the points, since the record
    must sit ahead of them, and again once every point is written and the
    counts are known. The payload is a fixed number of bytes at a fixed place,
    so the second write goes over the first. laszip instead makes the caller
    state the counts up front.
    """
    fields = {'laszip_version': _LASZIP_BUILD_DATE & 0xFFFF,
              'compatible_version': _COMPATIBLE_VERSION_14,
              'unused': 0,
              'extended_number_of_point_records': count,
              'extended_number_of_points_by_return': list(by_return)}
    # the rest are zero: a disguised file can carry neither waveform data nor
    # extended records, both of which arrived with the versions it predates
    for name, _, _ in HEADER_FORMAT_13 + HEADER_FORMAT_14:
        fields.setdefault(name, 0)

    return pack_format(COMPATIBILITY_RECORD_FORMAT, fields)
