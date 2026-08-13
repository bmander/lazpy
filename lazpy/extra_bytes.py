"""The "extra bytes" record: what a file says the opaque bytes on the end
of each of its points mean, read and built.

Both halves live together because they are inverses, and neither belongs to
the reader or the writer alone: compatibility mode reads a descriptor to find
the LAS 1.4 fields a legacy file is hiding, and writes one to say where it
put them.
"""

from collections import namedtuple

from .formats import EXTRA_BYTES_VLR_KEY, UnsupportedFileError
from .headers import (EXTRA_BYTES_ATTRIBUTE_FORMAT,
                      EXTRA_BYTES_ATTRIBUTE_SIZE, pack_format, unpack_format)

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
    every attribute begins where the one before it ended -- which is why
    `start` can be derived rather than stated.
    """
    start = 0
    for offset in range(0, len(data) - EXTRA_BYTES_ATTRIBUTE_SIZE + 1,
                        EXTRA_BYTES_ATTRIBUTE_SIZE):
        fields, _ = unpack_format(EXTRA_BYTES_ATTRIBUTE_FORMAT, data, offset)
        size = _attribute_size(fields['data_type'], fields['options'])
        yield _Attribute(fields['name'], offset, start, size)
        start += size


def _described_width(data):
    """How many extra bytes a point carries, by this record's account."""
    return sum(a.size for a in _extra_bytes_attributes(data))


# ---------------------------------------------------------------------------
# Building one.
#
# laszip builds a record an attribute at a time, in laszip_add_attribute();
# extra_bytes_record builds the whole record from the attributes, since it has
# to be complete before the header that counts it is written.
# ---------------------------------------------------------------------------

ExtraBytesAttribute = namedtuple(
    "ExtraBytesAttribute", "name data_type description scale offset",
    defaults=(b'', None, None))
ExtraBytesAttribute.__doc__ = """One attribute of a point's extra bytes.

    `data_type` is the LAS data type: 1 to 10 for the scalar types, in the
    order the specification lists them, and the deprecated array types above
    that. `scale` and `offset` turn the stored number into the quantity it
    stands for; leaving them out says the number is the quantity.
    """

# The option bits that say a descriptor's scale and offset were set at all;
# an attribute leaving them out is one whose numbers stand for themselves.
_SCALE_GIVEN = 0x08
_OFFSET_GIVEN = 0x10

# What the descriptor holds where those bits are clear, which is laszip's
# doing: laszip writes a scale of one and an offset of zero even for an
# attribute it was told nothing about, which means the same as no scale at all.
_NO_SCALE, _NO_OFFSET = 1.0, 0.0


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
        # one value per dimension, and an attribute has one scale to give, so
        # it goes to the first -- the only dimension a scalar type has, and
        # the one laszip_add_attribute() sets. The other two keep the values
        # that mean no scaling at all.
        'scale': [float(attribute.scale if attribute.scale is not None
                        else _NO_SCALE), _NO_SCALE, _NO_SCALE],
        'offset': [float(attribute.offset if attribute.offset is not None
                         else _NO_OFFSET), _NO_OFFSET, _NO_OFFSET],
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
