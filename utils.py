import struct

# bytestream parsing functions


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


# bit twiddling functions


def u8_fold(n):
    # eg 0 - 1 = 255
    # eg 255 + 1 = 0
    return n & 0xFF


def u32_zero_bit_0(n):
    # set bit 0 to 0
    return n & 0xFFFFFFFE


class StreamingMedian5:
    # the C++ implementation may save a few operations at the cost of clarity
    def __init__(self):
        self.values = [0, 0, 0, 0, 0]
        self.high = True

    def _add_high(self, v):
        for i in range(5):
            if v < self.values[i]:
                break

        self.values[i+1:] = self.values[i:4]  # shift right
        self.values[i] = v

        # if inserted above the middle value, swap it
        if i > 2:
            self.high = False

    def _add_low(self, v):
        for i in range(4, -1, -1):
            if v > self.values[i]:
                break

        self.values[:i] = self.values[1:i+1]  # shift left
        self.values[i] = v

        # if inserted below the middle value, swap it
        if i < 2:
            self.high = True

    def add(self, v):
        if self.high:
            self._add_high(v)
        else:
            self._add_low(v)

    def get(self):
        return self.values[2]
