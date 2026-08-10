"""Bytestream helpers for the header and VLR reader, and their inverses.

The writer serialises a header by walking the same field tables the reader
parses it with, so the two cannot drift: `PACKERS` maps each parser to the
packer that undoes it.
"""
import struct


def unsigned_int(bytes):
    return int.from_bytes(bytes, byteorder='little', signed=False)


def signed_int(bytes):
    return int.from_bytes(bytes, byteorder='little', signed=True)


def u32_array(bytes):
    return [unsigned_int(bytes[i:i+4]) for i in range(0, len(bytes), 4)]


def u64_array(bytes):
    return [unsigned_int(bytes[i:i+8]) for i in range(0, len(bytes), 8)]


def double(bytes):
    # '<d', not 'd': the header is little-endian on disk, and 'd' would mean
    # whatever the host is. The two agree on a little-endian host, which is why
    # this went unnoticed until the suite ran on s390x.
    return struct.unpack('<d', bytes)[0]


def cstr(bytes):
    return bytes.rstrip(b'\0')


def pack_unsigned_int(value, size):
    return int(value).to_bytes(size, byteorder='little', signed=False)


def pack_signed_int(value, size):
    return int(value).to_bytes(size, byteorder='little', signed=True)


def pack_u32_array(values, size):
    return _pack_array(values, size, 4)


def pack_u64_array(values, size):
    return _pack_array(values, size, 8)


def _pack_array(values, size, width):
    packed = b''.join(pack_unsigned_int(v, width) for v in values)
    if len(packed) != size:
        raise ValueError(f"expected {size // width} values, got {len(values)}")
    return packed


def pack_double(value, size):
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


PACKERS = {
    unsigned_int: pack_unsigned_int,
    signed_int: pack_signed_int,
    u32_array: pack_u32_array,
    u64_array: pack_u64_array,
    double: pack_double,
    cstr: pack_cstr,
}
