"""Bytestream helpers for the header and VLR reader, and their inverses.

The writer serialises a header by walking the same field tables the reader
parses it with, so the two cannot drift: `PACKERS` maps each parser to the
packer that undoes it.
"""
import struct


def unsigned_int(data):
    return int.from_bytes(data, byteorder='little', signed=False)


def signed_int(data):
    return int.from_bytes(data, byteorder='little', signed=True)


def u32_array(data):
    return [unsigned_int(data[i:i+4]) for i in range(0, len(data), 4)]


def u64_array(data):
    return [unsigned_int(data[i:i+8]) for i in range(0, len(data), 8)]


def double(data):
    # '<d', not 'd': the header is little-endian on disk, and 'd' would mean
    # whatever the host is. The two agree on a little-endian host, which is why
    # this went unnoticed until the suite ran on s390x.
    return struct.unpack('<d', data)[0]


def double_array(data):
    return [double(data[i:i+8]) for i in range(0, len(data), 8)]


def cstr(data):
    return data.rstrip(b'\0')


def raw(data):
    """A field with no interpretation of its own: the bytes as they lie.

    For a field whose meaning is elsewhere -- the "extra bytes" descriptor
    holds three of them, whose type is whatever the attribute's own type says.
    """
    return data


def pack_unsigned_int(value, size):
    return int(value).to_bytes(size, byteorder='little', signed=False)


def pack_signed_int(value, size):
    return int(value).to_bytes(size, byteorder='little', signed=True)


def pack_u32_array(values, size):
    return _pack_array(values, size, 4, pack_unsigned_int)


def pack_u64_array(values, size):
    return _pack_array(values, size, 8, pack_unsigned_int)


def pack_double_array(values, size):
    return _pack_array(values, size, 8, pack_double)


def _pack_array(values, size, width, pack):
    packed = b''.join(pack(v, width) for v in values)
    if len(packed) != size:
        raise ValueError(f"expected {size // width} values, got {len(values)}")
    return packed


def pack_double(value, size):
    """The inverse of `double`, which is eight bytes -- but the width comes
    from the field table like every other packer's, so a table that ever
    declares another one is refused rather than quietly written as eight."""
    if size != 8:
        raise ValueError(f"a double is 8 bytes, not {size}")
    return struct.pack('<d', value)


def pack_cstr(value, size):
    """A fixed-width field holding *value*, zero-padded.

    The full width is available: the LAS strings are padded rather than
    terminated, so a 32-byte field may hold 32 characters and no NUL.
    """
    if isinstance(value, str):
        value = value.encode()
    if len(value) > size:
        raise ValueError(f"{value!r} does not fit in {size} bytes")
    return value + b'\0' * (size - len(value))


def pack_raw(value, size):
    """A field with no interpretation, padded out to its width with zeros."""
    if len(value) > size:
        raise ValueError(f"{len(value)} bytes do not fit in {size}")
    return bytes(value) + b'\0' * (size - len(value))


PACKERS = {
    unsigned_int: pack_unsigned_int,
    signed_int: pack_signed_int,
    u32_array: pack_u32_array,
    u64_array: pack_u64_array,
    double_array: pack_double_array,
    double: pack_double,
    cstr: pack_cstr,
    raw: pack_raw,
}
