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
    return struct.unpack('d', bytes)[0]


def cstr(bytes):
    return bytes.rstrip(b'\0')
