"""The on-disk layout: header, VLR and LASzip-record format tables, and
the functions that read and write anything those tables describe."""

from ._utils import (unsigned_int, signed_int, u32_array, u64_array, double,
                     cstr, PACKERS)

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


def _can_seek(fp):
    """Whether seeks on `fp` can be expected to work.

    The same rule the C stream applies in file_is_seekable (src/laz_stream.c):
    an object that answers seekable() is taken at its word, since a pipe has a
    seek method that raises, and one that does not answer at all is believed.
    """
    return hasattr(fp, 'seek') and getattr(fp, 'seekable', lambda: True)()
