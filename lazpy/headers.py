"""The on-disk layout: header, VLR and LASzip-record format tables, and
the functions that read and write anything those tables describe."""

from ._cpylaz import LazError
from .formats import LASZIP_VLR_KEY
from ._utils import (unsigned_int, signed_int, u32_array, u64_array,
                     double_array, double, cstr, raw, PACKERS)

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


def _header_size(version_minor, user_data_size=0):
    """How long a LAS 1.`version_minor` header is, by its own tables.

    A file may keep bytes of its own behind those tables and count them in
    header_size, which is what `user_data_size` is; a reader takes them as
    whatever is left over.
    """
    return (sum(format_size(fmt) for fmt in header_formats(version_minor))
            + user_data_size)


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

# The two fields of that record an appended spatial index is found by, and
# how far into the payload they sit. Named here, where the table they are part
# of is, so that a field added in front of them moves them with it.
LASZIP_SPECIAL_EVLR_FORMAT = LASZIP_RECORD_FORMAT[7:9]

LASZIP_ITEM_FORMAT = (
    ('type', 2, unsigned_int),
    ('size', 2, unsigned_int),
    ('version', 2, unsigned_int),
)

# The GeoTIFF GeoKeyDirectory record's payload, followed by one entry per key
# -- the same header-then-repeated-rows shape the LASzip record has. The keys
# are TIFF's own, borrowed by GeoTIFF and borrowed again by LAS; lazpy.crs
# reads and builds them.
#
# A key states its value in value_offset when tiff_tag_location is 0, and
# otherwise names the record it is held in: 34736 for doubles, 34737 for
# ASCII, with value_offset an index into that record and count a length.
GEOKEY_DIRECTORY_FORMAT = (
    ('key_directory_version', 2, unsigned_int),
    ('key_revision', 2, unsigned_int),
    ('minor_revision', 2, unsigned_int),
    ('number_of_keys', 2, unsigned_int),
)

GEOKEY_ENTRY_FORMAT = (
    ('key_id', 2, unsigned_int),
    ('tiff_tag_location', 2, unsigned_int),
    ('count', 2, unsigned_int),
    ('value_offset', 2, unsigned_int),
)

# One attribute out of an "extra bytes" record, which is how a file says what
# the opaque bytes on the end of its points mean. The records are laid end to
# end, one fixed-width descriptor each.
#
# The type, the option byte and the name are what decide how a point is read;
# the five triples behind them describe what its numbers mean, one value per
# dimension. no_data, min and max are held as whatever type the attribute
# itself has, so they are bytes here and the attribute's own type is what
# reads them; scale and offset are doubles whatever the attribute is.
EXTRA_BYTES_ATTRIBUTE_FORMAT = (
    ('reserved', 2, unsigned_int),
    ('data_type', 1, unsigned_int),
    ('options', 1, unsigned_int),
    ('name', 32, cstr),
    ('unused', 4, unsigned_int),
    ('no_data', 24, raw),
    ('min', 24, raw),
    ('max', 24, raw),
    ('scale', 24, double_array),
    ('offset', 24, double_array),
    ('description', 32, cstr),
)


def format_size(fmt):
    """How many bytes a table occupies."""
    return sum(size for _, size, _ in fmt)


LASZIP_SPECIAL_EVLRS_AT = format_size(LASZIP_RECORD_FORMAT[:7])

VLR_HEADER_SIZE = format_size(VLR_HEADER_FORMAT)
EVLR_HEADER_SIZE = format_size(EVLR_HEADER_FORMAT)
EXTRA_BYTES_ATTRIBUTE_SIZE = format_size(EXTRA_BYTES_ATTRIBUTE_FORMAT)

# The longest payload a variable length record can declare, its length field
# being two bytes wide where an extended record's is eight -- which is the
# whole of the difference between the two tables above.
MAX_VLR_PAYLOAD = 0xFFFF


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


def _can_seek(fp):
    """Whether seeks on `fp` can be expected to work.

    The same rule the C stream applies in file_is_seekable (src/laz_stream.c):
    an object that answers seekable() is taken at its word, since a pipe has a
    seek method that raises, and one that does not answer at all is believed.
    """
    return hasattr(fp, 'seek') and getattr(fp, 'seekable', lambda: True)()


# ---------------------------------------------------------------------------
# Reading and writing the things those tables describe.
#
# Both directions of a record live together here, as both directions of a
# field do in the tables above, so that neither can drift from the other
# without the difference being on the screen at once. Reading a header is not
# a reader's business in particular: a file being appended to needs it as much
# as a file being read, and append_spatial_index is the caller that proves it.
# ---------------------------------------------------------------------------


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


def _read_las_header(fp):
    """Everything in front of the point data, as a dict."""
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
    consumed = header['header_size']
    for _ in range(header['number_of_variable_length_records']):
        vlr = _read_variable_length_record(fp)
        # where its payload begins, for anything that has to go back to
        # it: the LASzip record is patched in place by an appended index
        vlr['offset_to_data'] = consumed + VLR_HEADER_SIZE
        header['variable_length_records'][
            (vlr['user_id'], vlr['record_id'])] = vlr
        consumed += VLR_HEADER_SIZE + len(vlr['data'])

    # Whatever lies between the last record and the point data is the
    # file's own too, as the bytes inside the header are. Counted rather
    # than measured with tell(), which a stream need not answer, and read
    # here because the points begin where it ends.
    padding = header['offset_to_point_data'] - consumed
    header['user_data_after_header'] = fp.read(max(0, padding))

    return header


def _parse_laszip_record(data):
    """The LASzip record's payload, as a dict with its item list."""
    record, offset = unpack_format(LASZIP_RECORD_FORMAT, data)

    record['items'] = []
    for _ in range(record['number_of_items']):
        item, offset = unpack_format(LASZIP_ITEM_FORMAT, data, offset)
        record['items'].append(item)

    record['user_data'] = data[offset:]
    return record


def _pack_laszip_record(fields, items):
    """The inverse: the payload bytes for those fields and items."""
    payload = pack_format(LASZIP_RECORD_FORMAT,
                          {**fields, 'number_of_items': len(items)})
    for item_type, size, version in items:
        payload += pack_format(LASZIP_ITEM_FORMAT,
                               {'type': item_type, 'size': size,
                                'version': version})
    return payload


def _find_laz_header(header):
    """The parsed LASzip record of `header`, or None for an uncompressed
    file."""
    vlr = header['variable_length_records'].get(LASZIP_VLR_KEY)
    if vlr is None:
        return None
    return _parse_laszip_record(vlr['data'])
