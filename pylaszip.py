import struct
from enum import IntEnum
import sys

# LAS file specification
# 1.2:
# https://www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf
# 1.4:
# https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf


class Compressor(IntEnum):
    NONE = 0
    POINTWISE = 1
    POINTWISE_CHUNKED = 2
    LAYERED_CHUNKED = 3


class Coder(IntEnum):
    ARITHMETIC = 0


class ItemType(IntEnum):
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


NUMBER_RETURN_MAP = (
  (15, 14, 13, 12, 11, 10,  9,  8),
  (14,  0,  1,  3,  6, 10, 10,  9),
  (13,  1,  2,  4,  7, 11, 11, 10),
  (12,  3,  4,  5,  8, 12, 12, 11),
  (11,  6,  7,  8,  9, 13, 13, 12),
  (10, 10, 11, 12, 13, 14, 14, 13),
  (9, 10, 11, 12, 13, 14, 15, 14),
  (8,  9, 10, 11, 12, 13, 14, 15)
)


NUMBER_RETURN_LEVEL = (
  (0,  1,  2,  3,  4,  5,  6,  7),
  (1,  0,  1,  2,  3,  4,  5,  6),
  (2,  1,  0,  1,  2,  3,  4,  5),
  (3,  2,  1,  0,  1,  2,  3,  4),
  (4,  3,  2,  1,  0,  1,  2,  3),
  (5,  4,  3,  2,  1,  0,  1,  2),
  (6,  5,  4,  3,  2,  1,  0,  1),
  (7,  6,  5,  4,  3,  2,  1,  0)
)


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


class ArithmeticBitModel:
    BM_LENGTH_SHIFT = 13
    BM_MAX_COUNT = 1 << BM_LENGTH_SHIFT

    def __init__(self):
        self.init()

    def init(self):
        # initialize equiprobable model
        self.bit_0_count = 1
        self.bit_count = 2
        self.bit_0_prob = 1 << (self.BM_LENGTH_SHIFT - 1)

        # start with frequent updates
        self.update_cycle = self.bits_until_update = 4

    def update(self):
        # halve counts when threshold is reached
        self.bit_count += self.update_cycle
        if self.bit_count >= self.BM_MAX_COUNT:
            self.bit_count = (self.bit_count + 1) >> 1
            self.bit_0_count = (self.bit_0_count + 1) >> 1
            if self.bit_0_count == self.bit_count:
                self.bit_count += 1

        # compute scaled bit 0 probability
        scale = 0x80000000 // self.bit_count
        self.bit_0_prob = (self.bit_0_count * scale) >> \
                          (31 - self.BM_LENGTH_SHIFT)

        # update frequency of model updates
        self.update_cycle = (5 * self.update_cycle) >> 2
        self.update_cycle = min(self.update_cycle, 64)
        self.bits_until_update = self.update_cycle

    def __repr__(self):
        return f'ArithmeticBitModel(update_cycle={self.update_cycle}, ' \
            f'bits_until_update={self.bits_until_update}, ' \
            f'bit_0_prob={self.bit_0_prob}, ' \
            f'bit_0Pcount={self.bit_0_count}, bit_count={self.bit_count})'


class ArithmeticModel:
    """An ArithmeticModel is a table of probabilities for a set of symbols."""
    DM_LENGTH_SHIFT = 15
    DM_MAX_COUNT = 1 << DM_LENGTH_SHIFT

    def __init__(self, num_symbols, compress):
        self.num_symbols = num_symbols
        self.compress = compress

        # tables
        self.distribution = None
        self.decoder_table = None
        self.symbol_count = None

    def init(self, table=None):
        if self.distribution is None:
            if self.num_symbols < 2 or self.num_symbols > 2048:
                raise Exception("Invalid number of symbols")

            self.last_symbol = self.num_symbols-1

            if not self.compress and self.num_symbols > 16:
                table_bits = 3
                while self.num_symbols > (1 << (table_bits+2)):
                    table_bits += 1

                self.table_shift = self.DM_LENGTH_SHIFT - table_bits

                self.table_size = 1 << table_bits
                self.decoder_table = [0] * (self.table_size + 2)
            else:  # small alphabet; no table needed
                self.table_shift = self.table_size = 0

            self.distribution = [0] * self.num_symbols
            self.symbol_count = [0] * self.num_symbols

        self.total_count = 0
        self.update_cycle = self.num_symbols
        if table is not None:
            self.symbol_count = table[:]
        else:
            self.symbol_count = [1] * self.num_symbols

        self._update()
        self.symbols_until_update = (self.num_symbols+6) >> 1
        self.update_cycle = self.symbols_until_update

    def _update(self):
        # halve counts when threshold is reached
        self.total_count += self.update_cycle
        if self.total_count > self.DM_MAX_COUNT:
            self.total_count = 0
            for i in range(self.num_symbols):
                self.symbol_count[i] = (self.symbol_count[i] + 1) >> 1
                self.total_count += self.symbol_count[i]

        # compute distribution
        sum, s = 0, 0
        scale = 0x80000000 // self.total_count

        if self.compress or self.table_size == 0:
            for k in range(self.num_symbols):
                self.distribution[k] = (scale*sum) >> \
                                       (31 - self.DM_LENGTH_SHIFT)
                sum += self.symbol_count[k]
        else:
            for k in range(self.num_symbols):
                self.distribution[k] = (scale*sum) >> \
                                       (31 - self.DM_LENGTH_SHIFT)
                sum += self.symbol_count[k]
                w = self.distribution[k] >> self.table_shift
                while s < w:
                    s += 1
                    self.decoder_table[s] = k-1
            self.decoder_table[0] = 0
            while s <= self.table_size:
                s += 1
                self.decoder_table[s] = self.num_symbols - 1

        # set frequency of model updates
        self.update_cycle = (5 * self.update_cycle) >> 2
        max_cycle = (self.num_symbols + 6) << 3
        self.update_cycle = min(self.update_cycle, max_cycle)
        self.symbols_until_update = self.update_cycle

    def __str__(self):
        ret = f"ArithmeticModel(num_symbols={self.num_symbols}," \
          f"compress={self.compress}, distribution={self.distribution}, " \
          f"decoder_table={self.decoder_table}, " \
          f"symbol_count={self.symbol_count}, " \
          f"total_count={self.total_count}, " \
          f"update_cycle={self.update_cycle}, " \
          f"symbols_until_update={self.symbols_until_update})"

        return ret


class ArithmeticEncoder:
    def __init__(self):
        raise NotImplementedError()


class ArithmeticDecoder:
    """An ArithmeticDecoder decodes a stream of symbols using an arithmetic
    model."""
    AC_MAX_LENGTH = 0xFFFFFFFF
    AC_MIN_LENGTH = 0x01000000

    def __init__(self, fp):
        self.fp = fp

    def start(self):
        self.length = self.AC_MAX_LENGTH

        data = self.fp.read(4)
        self.value = int.from_bytes(data, byteorder='big')

    def _renorm_dec_interval(self):
        """Renormalize the decoder interval."""
        while self.length < self.AC_MIN_LENGTH:
            data = unsigned_int(self.fp.read(1))
            self.value = (self.value << 8) | data
            self.length <<= 8

    def decode_bit(self, m):
        # m is an ArithmeticBitModel
        x = m.bit_0_prob * (self.length >> m.BM_LENGTH_SHIFT)
        sym = (self.value >= x)

        if sym == 0:
            self.length = x
            m.bit_0_count += 1
        else:
            self.value -= x
            self.length -= x

        if self.length < self.AC_MIN_LENGTH:
            self._renorm_dec_interval()

        m.bits_until_update -= 1  # TODO get the model to handle this
        if m.bits_until_update == 0:
            m.update()

        return sym

    def decode_symbol(self, m):
        # m is an ArithmeticModel

        y = self.length

        # use table lookup for faster decoding
        if m.decoder_table is not None:
            self.length >>= m.DM_LENGTH_SHIFT
            dv = self.value // self.length
            t = dv >> m.table_shift

            # use table to get first symbol
            sym = m.decoder_table[t]
            n = m.decoder_table[t+1] + 1

            # finish with bisection search
            while n > sym+1:
                k = (sym + n) >> 1
                if m.distribution[k] > dv:
                    n = k
                else:
                    sym = k

            # compute products
            x = m.distribution[sym] * self.length

            if sym != m.last_symbol:
                y = m.distribution[sym+1] * self.length

        # decode using only multiplications
        else:
            x = sym = 0
            self.length >>= m.DM_LENGTH_SHIFT
            n = m.num_symbols
            k = n >> 1

            # decode via bisection search
            while k != sym:
                z = self.length * m.distribution[k]
                if z > self.value:
                    n = k
                    y = z  # value is smaller
                else:
                    sym = k
                    x = z  # value is larger or equal

                k = (sym + n) >> 1

        # update interval
        self.value -= x
        self.length = y - x

        if self.length < self.AC_MIN_LENGTH:
            self._renorm_dec_interval()

        m.symbol_count[sym] += 1

        m.symbols_until_update -= 1  # TODO get the model to handle this
        if m.symbols_until_update == 0:  # periodic model update
            m._update()

        assert sym < m.num_symbols

        return sym

    def read_bits(self, bits):
        assert bits > 0 and bits <= 32

        if bits > 19:
            lower = self.read_bits(16)
            upper = self.read_bits(bits-16)
            return (upper << 16) | lower

        self.length >>= bits
        sym = self.value // (self.length)
        self.value = self.value % self.length

        if self.length < self.AC_MIN_LENGTH:
            self._renorm_dec_interval()

        return sym

    def read_int(self):
        return self.read_bits(32)

    def create_symbol_model(self, num_symbols):
        return ArithmeticModel(num_symbols, False)

    def __repr__(self):
        return f"ArithmeticDecoder(value={self.value}, length={self.length})"


class StreamingMedian5:
    def __init__(self):
        self.values = [0, 0, 0, 0, 0]
        self.high = True

    def _add_high(self, v):
        # TODO simplify this
        # insert and bubble up

        # if v less than the middle
        if v < self.values[2]:
            # shift upper section up one
            self.values[4] = self.values[3]
            self.values[3] = self.values[2]

            # if v is less than the lowest
            if v < self.values[0]:
                # shift lower half up one and insert v at bottom
                self.values[2] = self.values[1]
                self.values[1] = self.values[0]
                self.values[0] = v
            elif v < self.values[1]:
                # shift lower half up one and insert v in middle
                self.values[2] = self.values[1]
                self.values[1] = v
            else:
                # insert v in middle
                self.values[2] = v
        else:
            if v < self.values[3]:
                self.values[4] = self.values[3]
                self.values[3] = v
            else:
                self.values[4] = v
            self.high = False

    def _add_low(self, v):
        # insert and bubble down

        if v > self.values[2]:
            self.values[0] = self.values[1]
            self.values[1] = self.values[2]
            if v > self.values[4]:
                self.values[2] = self.values[3]
                self.values[3] = self.values[4]
                self.values[4] = v
            elif v > self.values[3]:
                self.values[2] = self.values[3]
                self.values[3] = v
            else:
                self.values[2] = v
        else:
            if v > self.values[1]:
                self.values[0] = self.values[1]
                self.values[1] = v
            else:
                self.values[0] = v
            self.high = True

    def add(self, v):
        if self.high:
            self._add_high(v)
        else:
            self._add_low(v)

    def get(self):
        return self.values[2]


def not_implemented_func(*args, **kwargs):
    raise NotImplementedError


class LasPoint10:

    @classmethod
    def from_bytes(cls, bytes):
        ret = cls()

        ret.x = unsigned_int(bytes[0:4])
        ret.y = unsigned_int(bytes[4:8])
        ret.z = unsigned_int(bytes[8:12])
        ret.intensity = unsigned_int(bytes[12:14])

        bitfield = unsigned_int(bytes[14:15])
        ret.set_bitfield(bitfield)

        ret.classification = unsigned_int(bytes[15:16])
        ret.scan_angle_rank = unsigned_int(bytes[16:17])
        ret.user_data = unsigned_int(bytes[17:18])
        ret.point_source_id = unsigned_int(bytes[18:20])

        return ret

    def __init__(self):
        self.x = 0
        self.y = 0
        self.z = 0
        self.intensity = 0

        self.return_num = 0
        self.num_returns = 0
        self.scan_dir_flag = 0
        self.edge_of_flight_line = 0

        self.classification = 0
        self.scan_angle_rank = 0
        self.user_data = 0
        self.point_source_id = 0

    def bitfield_value(self):
        return self.return_num | (self.num_returns << 3) | \
                (self.scan_dir_flag << 6) | (self.edge_of_flight_line << 7)

    def set_bitfield(self, byte):
        self.return_num = byte & 0b00000111
        self.num_returns = (byte & 0b00111000) >> 3
        self.scan_dir_flag = (byte & 0b01000000) >> 6
        self.edge_of_flight_line = (byte & 0b10000000) >> 7

    def copy(self):
        ret = LasPoint10()

        ret.x = self.x
        ret.y = self.y
        ret.z = self.z
        ret.intensity = self.intensity
        ret.return_num = self.return_num
        ret.num_returns = self.num_returns
        ret.scan_dir_flag = self.scan_dir_flag
        ret.edge_of_flight_line = self.edge_of_flight_line
        ret.classification = self.classification
        ret.scan_angle_rank = self.scan_angle_rank
        ret.user_data = self.user_data
        ret.point_source_id = self.point_source_id

        return ret

    def __str__(self):
        return f"LasPoint10(x={self.x}, y={self.y}, z={self.z}, " \
          f"intensity={self.intensity}, return_num={self.return_num}, " \
          f"num_returns={self.num_returns}, " \
          f"scan_dir_flag={self.scan_dir_flag}, " \
          f"edge_of_flight_line={self.edge_of_flight_line}, " \
          f"classification={self.classification}, " \
          f"scan_angle_rank={self.scan_angle_rank}, " \
          f"user_data={self.user_data}, " \
          f"point_source_id={self.point_source_id})"


def u8_fold(n):
    # eg 0 - 1 = 255
    # eg 255 + 1 = 0
    return n & 0xFF


def u32_zero_bit_0(n):
    # set bit 0 to 0
    return n & 0xFFFFFFFE


read_item_compressed_point10_v1 = not_implemented_func


class read_item_compressed_point10_v2:
    def __init__(self, dec):
        self.dec = dec

        # create model and integer compressors
        self.m_changed_values = dec.create_symbol_model(64)
        self.ic_intensity = IntegerCompressor(dec, 16, 4)
        self.m_scan_angle_rank = [dec.create_symbol_model(256),
                                  dec.create_symbol_model(256)]
        self.ic_point_source_id = IntegerCompressor(dec, 16)
        # an alternative approach is to use an array of 256 models
        # this is more pythonic but inappropriate for C; when this gets
        # ported to C, the array of 256 models should be used
        self.m_bit_byte = {}
        self.m_classification = {}
        self.m_user_data = {}
        self.ic_dx = IntegerCompressor(dec, 32, 2)
        self.ic_dy = IntegerCompressor(dec, 32, 22)
        self.ic_z = IntegerCompressor(dec, 32, 20)

        self.last_x_diff_median5 = []
        self.last_y_diff_median5 = []
        for i in range(16):
            self.last_x_diff_median5.append(StreamingMedian5())
            self.last_y_diff_median5.append(StreamingMedian5())

        self.last_intensity = [0]*16
        self.last_height = [0]*8

        self.last_item = []

    def init(self, item, context):
        # TODO combine init functions
        # init state
        for i in range(16):
            self.last_x_diff_median5[i] = StreamingMedian5()
            self.last_y_diff_median5[i] = StreamingMedian5()
        self.last_intensity = [0]*16
        self.last_height = [0]*8

        self.m_changed_values.init()
        self.ic_intensity.init_decompressor()
        self.m_scan_angle_rank[0].init()
        self.m_scan_angle_rank[1].init()
        self.ic_point_source_id.init_decompressor()

        for m in self.m_bit_byte.values():
            m.init()
        for m in self.m_classification.values():
            m.init()
        for m in self.m_user_data.values():
            m.init()

        self.ic_dx.init_decompressor()
        self.ic_dy.init_decompressor()
        self.ic_z.init_decompressor()

        self.last_item = item.copy()
        self.last_item.intensity = 0

    def read(self, context):

        changed_values = self.dec.decode_symbol(self.m_changed_values)

        # decompress bit field byte
        if changed_values & 0b100000:
            bitfield = self.last_item.bitfield_value()
            if bitfield not in self.m_bit_byte:
                model = self.dec.create_symbol_model(256)
                model.init()
                self.m_bit_byte[bitfield] = model

            bitfield = self.dec.decode_symbol(self.m_bit_byte[bitfield])
            self.last_item.set_bitfield(bitfield)

        r = self.last_item.return_num
        n = self.last_item.num_returns
        m = NUMBER_RETURN_MAP[n][r]
        el = NUMBER_RETURN_LEVEL[n][r]

        # decompress intensity
        if changed_values & 0b10000:
            context = min(m, 3)
            self.last_item.intensity = self.ic_intensity.decompress(
                                self.last_intensity[m], context)
            self.last_intensity[m] = self.last_item.intensity
        else:
            self.last_item.intensity = self.last_intensity[m]

        # decompress classification
        if changed_values & 0b1000:
            if self.last_item.classification not in self.m_classification:
                self.m_classification[self.last_item.classification] = \
                    self.dec.create_symbol_model(256)
                self.m_classification[self.last_item.classification].init()
            self.last_item.classification = self.dec.decode_symbol(
                self.m_classification[self.last_item.classification])

        # decompress scan angle rank
        if changed_values & 0b100:
            f = self.last_item.scan_dir_flag
            val = self.dec.decode_symbol(self.m_scan_angle_rank[f])
            self.last_item.scan_angle_rank = \
                u8_fold(val + self.last_item.scan_angle_rank)

        # decompress user data
        if changed_values & 0b10:
            if self.last_item.user_data not in self.m_user_data:
                self.m_user_data[self.last_item.user_data] = \
                    self.dec.create_symbol_model(256)
                self.m_user_data[self.last_item.user_data].init()

            model = self.m_user_data[self.last_item.user_data]
            self.last_item.user_data = self.dec.decode_symbol(model)

        # decompress point source ID
        if changed_values & 0b1:
            self.last_item.point_source_id = \
                self.ic_point_source_id.decompress(
                    self.last_item.point_source_id)

        # decompress x
        median = self.last_x_diff_median5[m].get()
        diff = self.ic_dx.decompress(median, int(n == 1))
        self.last_item.x = self.last_item.x + diff
        self.last_x_diff_median5[m].add(diff)

        # decompress y
        median = self.last_y_diff_median5[m].get()
        k_bits = self.ic_dx.k
        context = int(n == 1) + (u32_zero_bit_0(k_bits) if k_bits < 20 else 20)
        diff = self.ic_dy.decompress(median, context)
        self.last_item.y = self.last_item.y + diff
        self.last_y_diff_median5[m].add(diff)

        # decompress z
        k_bits = (self.ic_dx.k + self.ic_dy.k) // 2
        context = int(n == 1) + (u32_zero_bit_0(k_bits) if k_bits < 18 else 18)
        self.last_item.z = self.ic_z.decompress(self.last_height[el], context)
        self.last_height[el] = self.last_item.z

        return self.last_item.copy()


# this is a holdover from laszip; I have no idea what's going on here
LASZIP_GPSTIME_MULTI = 500
LASZIP_GPSTIME_MULTI_MINUS = -10
LASZIP_GPSTIME_MULTI_TOTAL = LASZIP_GPSTIME_MULTI - \
                             LASZIP_GPSTIME_MULTI_MINUS + 6
LASZIP_GPSTIME_MULTI_UNCHANGED = LASZIP_GPSTIME_MULTI - \
                                 LASZIP_GPSTIME_MULTI_MINUS + 1
LASZIP_GPSTIME_MULTI_CODE_FULL = LASZIP_GPSTIME_MULTI - \
                                 LASZIP_GPSTIME_MULTI_MINUS + 2


read_item_compressed_gpstime11_v1 = not_implemented_func


class read_item_compressed_gpstime11_v2:
    def __init__(self, dec):
        self.dec = dec

        self.m_gpstime_multi = self.dec.create_symbol_model(
            LASZIP_GPSTIME_MULTI_TOTAL)
        self.m_gpstime_0diff = self.dec.create_symbol_model(6)
        self.ic_gpstime = IntegerCompressor(dec, 32, 9)

    def init(self, item, context):
        self.last = 0
        self.next = 0
        self.last_gpstime_diff = [0, 0, 0, 0]
        self.multi_extreme_counter = [0, 0, 0, 0]

        self.m_gpstime_multi.init()
        self.m_gpstime_0diff.init()
        self.ic_gpstime.init_decompressor()

        self.last_gpstime = [item, 0, 0, 0]

    def _read_lastdiff_zero(self, context):
        multi = self.dec.decode_symbol(self.m_gpstime_0diff)

        if multi == 1:  # the difference fits in 32 bits
            val = self.ic_gpstime.decompress(0, 0)
            self.last_gpstime_diff[self.last] = val
            self.last_gpstime[self.last] += val
            self.multi_extreme_counter[self.last] = 0
        elif multi == 2:  # the difference is large
            self.next = (self.next + 1) & 3
            val = self.ic_gpstime.decompress(
                    self.last_gpstime[self.last] >> 32, 8)
            val <<= 32
            val = val | self.dec.read_int()
            self.last_gpstime[self.next] = val

            self.last = self.next
            self.last_gpstime_diff[self.last] = 0
            self.multi_extreme_counter[self.last] = 0
        elif multi > 2:  # switch to another sequence
            self.last = (self.last+multi-2) & 3
            self.read(context)

    def _read_lastdiff_nonzero(self, context):
        # TODO this is a mess

        multi = self.dec.decode_symbol(self.m_gpstime_multi)

        if multi == 1:
            pred = self.last_gpstime_diff[self.last]
            val = self.ic_gpstime.decompress(pred, 1)
            self.last_gpstime[self.last] += val

            self.multi_extreme_counter[self.last] = 0
        elif multi < LASZIP_GPSTIME_MULTI_UNCHANGED:
            if multi == 0:
                gpstime_diff = self.ic_gpstime.decompress(0, 7)
                self.multi_extreme_counter[self.last] += 1
                if self.multi_extreme_counter[self.last] > 3:
                    self.last_gpstime_diff[self.last] = gpstime_diff
                    self.multi_extreme_counter[self.last] = 0
            elif multi < LASZIP_GPSTIME_MULTI:
                pred = multi*self.last_gpstime_diff[self.last]
                context = 2 if multi < 10 else 3
                gpstime_diff = self.ic_gpstime.decompress(pred, context)
            elif multi == LASZIP_GPSTIME_MULTI:
                pred = LASZIP_GPSTIME_MULTI*self.last_gpstime_diff[self.last]
                gpstime_diff = self.ic_gpstime.decompress(pred, 4)
                self.multi_extreme_counter[self.last] += 1
                if self.multi_extreme_counter[self.last] > 3:
                    self.last_gpstime_diff[self.last] = gpstime_diff
                    self.multi_extreme_counter[self.last] = 0
            else:
                multi = LASZIP_GPSTIME_MULTI - multi
                if multi > LASZIP_GPSTIME_MULTI_MINUS:
                    pred = multi*self.last_gpstime_diff[self.last]
                    gpstime_diff = self.ic_gpstime.decompress(pred, 5)
                else:
                    pred = LASZIP_GPSTIME_MULTI_MINUS * \
                            self.last_gpstime_diff[self.last]
                    gpstime_diff = self.ic_gpstime.decompress(pred, 6)
                    self.multi_extreme_counter[self.last] += 1
                    if self.multi_extreme_counter[self.last] > 3:
                        self.last_gpstime_diff[self.last] = gpstime_diff
                        self.multi_extreme_counter[self.last] = 0

            self.last_gpstime[self.last] += gpstime_diff
        elif multi == LASZIP_GPSTIME_MULTI_CODE_FULL:
            self.next = (self.next+1) & 3
            pred = self.last_gpstime[self.last] >> 32
            val = self.ic_gpstime.decompress(pred, 8)
            val <<= 32
            val = val | self.dec.read_int()
            self.last_gpstime[self.next] = val

            self.last = self.next
            self.last_gpstime_diff[self.last] = 0
            self.multi_extreme_counter[self.last] = 0
        elif multi >= LASZIP_GPSTIME_MULTI_CODE_FULL:
            self.last = (self.last+multi-LASZIP_GPSTIME_MULTI_CODE_FULL) & 3
            self.read(context)

    def read(self, context):
        if self.last_gpstime_diff[self.last] == 0:
            self._read_lastdiff_zero(context)
        else:
            self._read_lastdiff_nonzero(context)

        return self.last_gpstime[self.last]


read_item_compressed_rgb12_v1 = not_implemented_func
read_item_compressed_rgb12_v2 = not_implemented_func
read_item_compressed_byte_v1 = not_implemented_func
read_item_compressed_byte_v2 = not_implemented_func
read_item_compressed_point14_v3 = not_implemented_func
read_item_compressed_point14_v4 = not_implemented_func
read_item_compressed_rgb12_v3 = not_implemented_func
read_item_compressed_rgb12_v4 = not_implemented_func
read_item_compressed_rgbnir14_v3 = not_implemented_func
read_item_compressed_rgbnir14_v4 = not_implemented_func
read_item_compressed_byte_v3 = not_implemented_func
read_item_compressed_byte_v4 = not_implemented_func
read_item_compressed_wavepacket13_v1 = not_implemented_func
read_item_compressed_wavepacket14_v3 = not_implemented_func
read_item_compressed_wavepacket14_v4 = not_implemented_func


def las_read_item_raw_point10_le(fp):
    return LasPoint10.from_bytes(fp.read(20))


def _read_item_raw_gpstime11(fp):
    gps_time = double(fp.read(8))
    return (gps_time,)


def las_read_item_raw_gpstime11_le(fp):
    return unsigned_int(fp.read(8))


class IntegerCompressor:
    def __init__(self, dec_or_enc, bits=16, contexts=1, bits_high=8, range=0):
        if type(dec_or_enc) == ArithmeticDecoder:
            self.dec = dec_or_enc
            self.enc = None
        elif type(dec_or_enc) == ArithmeticEncoder:
            self.dec = None
            self.enc = dec_or_enc

        self.bits = bits
        self.contexts = contexts
        self.bits_high = bits_high
        self.range = range

        if range != 0:
            self.corr_bits = 0
            self.corr_range = range
            while range != 0:
                range >>= 1
                self.corr_bits += 1
            if self.corr_range == (1 << (self.corr_bits - 1)):
                self.corr_bits -= 1
            self.corr_min = -self.corr_range // 2
            self.corr_max = self.corr_min+self.corr_range-1
        elif bits > 0 and bits < 32:
            self.corr_bits = bits
            self.corr_range = 1 << bits
            self.corr_min = -self.corr_range // 2
            self.corr_max = self.corr_min+self.corr_range-1
        else:
            self.corr_bits = 32
            self.corr_range = 0
            self.corr_min = -0x7FFFFFFF
            self.corr_max = 0x7FFFFFFF

        self.m_bits = None
        self.m_corrector = None

        self.k = 0

    def init_decompressor(self):
        assert self.dec

        if self.m_bits is None:
            self.m_bits = []
            for i in range(self.contexts):
                model = self.dec.create_symbol_model(self.corr_bits+1)
                self.m_bits.append(model)

            self.m_corrector = [ArithmeticBitModel()]
            for i in range(1, self.corr_bits):
                if i <= self.bits_high:
                    self.m_corrector.append(
                        self.dec.create_symbol_model(1 << i))
                else:
                    self.m_corrector.append(
                        self.dec.create_symbol_model(1 << self.bits_high))

        for i in range(self.contexts):
            self.m_bits[i].init()

        self.m_corrector[0].init()

        for i in range(1, self.corr_bits):
            self.m_corrector[i].init()

    def _read_corrector(self, model):
        self.k = self.dec.decode_symbol(model)

        if self.k != 0:
            if self.k < 32:
                if self.k <= self.bits_high:
                    c = self.dec.decode_symbol(self.m_corrector[self.k])
                else:
                    k1 = self.k-self.bits_high
                    c = self.dec.decode_symbol(self.m_corrector[self.k])
                    c1 = self.dec.read_bits(k1)
                    c = (c << k1) | c1

                # translate c back into its correct interval
                if c >= (1 << (self.k-1)):
                    c += 1
                else:
                    c -= (1 << self.k)-1

            else:
                c = self.corr_min
        else:
            c = self.dec.decode_bit(self.m_corrector[0])

        return c

    def decompress(self, pred, context=0):
        assert self.dec

        real = pred + self._read_corrector(self.m_bits[context])

        if real < 0:
            real += self.corr_range
        elif real >= self.corr_range:
            real -= self.corr_range

        return real


class Reader:
    def __init__(self):
        pass

    def _init_point_reader_functions(self):

        # create raw_readers
        type_raw_reader = {
            ItemType.POINT10: las_read_item_raw_point10_le,
            ItemType.GPSTIME11: las_read_item_raw_gpstime11_le,
            # ItemType.RGB12: las_read_item_raw_rgb12_le,
            # ItemType.BYTE: las_read_item_raw_byte_le,
            # ItemType.RGBNIR14: las_read_item_raw_rgbnir14_le,
            # ItemType.WAVEPACKET13: las_read_item_raw_wavepacket13_le,
        }

        self.readers_raw = []
        self.point_size = 0  # TODO eliminate?
        for item in self.laz_header['items']:
            func = type_raw_reader.get(item['type'])

            if func is None:
                raise Exception("Unknown item type")

            self.readers_raw.append(func)

            self.point_size += item['size']

        # create compressed readers
        type_version_compressed_reader = {
            (ItemType.POINT10, 1): read_item_compressed_point10_v1,
            (ItemType.POINT10, 2): read_item_compressed_point10_v2,
            (ItemType.GPSTIME11, 1): read_item_compressed_gpstime11_v1,
            (ItemType.GPSTIME11, 2): read_item_compressed_gpstime11_v2,
            (ItemType.RGB12, 1): read_item_compressed_rgb12_v1,
            (ItemType.RGB12, 2): read_item_compressed_rgb12_v2,
            (ItemType.BYTE, 1): read_item_compressed_byte_v1,
            (ItemType.BYTE, 2): read_item_compressed_byte_v2,
            (ItemType.POINT14, 3): read_item_compressed_point14_v3,
            (ItemType.POINT14, 4): read_item_compressed_point14_v4,
            (ItemType.RGB14, 3): read_item_compressed_rgb12_v3,
            (ItemType.RGB14, 4): read_item_compressed_rgb12_v4,
            (ItemType.RGBNIR14, 3): read_item_compressed_rgbnir14_v3,
            (ItemType.RGBNIR14, 4): read_item_compressed_rgbnir14_v4,
            (ItemType.BYTE14, 3): read_item_compressed_byte_v3,
            (ItemType.BYTE14, 4): read_item_compressed_byte_v4,
            (ItemType.WAVEPACKET13, 1): read_item_compressed_wavepacket13_v1,
            (ItemType.WAVEPACKET14, 3): read_item_compressed_wavepacket14_v3,
            (ItemType.WAVEPACKET14, 4): read_item_compressed_wavepacket14_v4,
        }
        self.readers_compressed = []
        for i, item in enumerate(self.laz_header['items']):
            key = (item['type'], item['version'])
            if key in type_version_compressed_reader:
                compressed_reader_class = type_version_compressed_reader[key]
                compressed_reader = compressed_reader_class(self.dec)

                self.readers_compressed.append(compressed_reader)
            else:
                raise Exception("Unknown item type/version")

        # create seek table
        self.seek_point = []  # TODO eliminate?
        for item in self.laz_header['items']:
            self.seek_point.append([0]*item['size'])

        # number of points per chunk
        self.chunk_size = self.laz_header['chunk_size']  # TODO eliminate?

        # indicate the reader is at the end of the chunk in order
        # to force a read of the next chunk
        self.chunk_count = self.chunk_size

    @staticmethod
    def _read_variable_length_record(fp):
        record = {}
        record['reserved'] = unsigned_int(fp.read(2))
        record['user_id'] = cstr(fp.read(16))
        record['record_id'] = unsigned_int(fp.read(2))
        record['record_length_after_header'] = unsigned_int(fp.read(2))
        record['description'] = cstr(fp.read(32))
        record['data'] = fp.read(record['record_length_after_header'])
        return record

    @staticmethod
    def _read_las_header(fp):
        header_format_12 = (
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
            ('number_of_points_by_return', 4*5, u32_array),
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

        header_format_13 = (
            ('start_of_waveform_data_packet_record', 8, unsigned_int),
        )

        header_format_14 = (
            ('start_of_first_extended_variable_length_record', 8,
             unsigned_int),
            ('number_of_extended_variable_length_records', 4, unsigned_int),
            ('number_of_point_records', 8, unsigned_int),
            ('number_of_points_by_return', 8*15, u64_array),
        )

        def read_into_header(fp, header, format):
            for name, size, func in format:
                header[name] = func(fp.read(size))

        def header_section_size(format):
            return sum([size for name, size, func in format])

        header = {}

        # Read header
        read_into_header(fp, header, header_format_12)
        bytes_read = header_section_size(header_format_12)

        # Ensure the file is a LAS file
        if header['file_signature'] != b'LASF':
            raise Exception("Invalid file signature")

        # Read 1.3 header fields
        if header['version_major'] == 1 and header['version_minor'] >= 3:
            read_into_header(fp, header, header_format_13)
            bytes_read += header_section_size(header_format_13)

        # Read 1.4 header fields
        if header['version_major'] == 1 and header['version_minor'] >= 4:
            read_into_header(fp, header, header_format_14)
            bytes_read += header_section_size(header_format_14)

        # Read user data, if any
        user_data_size = header['header_size'] - bytes_read
        header['user_data'] = fp.read(user_data_size)

        # Read variable length records
        header['variable_length_records'] = {}
        for i in range(header['number_of_variable_length_records']):
            vlr = Reader._read_variable_length_record(fp)
            header['variable_length_records'][vlr['record_id']] = vlr

        return header

    @staticmethod
    def _parse_laszip_record(data):
        laszip_record_format = (
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

        laszip_record = {}
        offset = 0
        for name, size, func in laszip_record_format:
            laszip_record[name] = func(data[offset:offset+size])
            offset += size

        laszip_record['items'] = []
        for i in range(laszip_record['number_of_items']):
            item = {}
            item['type'] = unsigned_int(data[offset:offset+2])
            item['size'] = unsigned_int(data[offset+2:offset+4])
            item['version'] = unsigned_int(data[offset+4:offset+6])
            offset += 6
            laszip_record['items'].append(item)

        laszip_record['user_data'] = data[offset:]

        return laszip_record

    @staticmethod
    def _read_laz_header(header):

        # Read LASzip record, stored in the data payload of a variable length
        # record
        LASZIP_VLR_ID = 22204
        laszip_vlr = header['variable_length_records'].get(LASZIP_VLR_ID)
        if laszip_vlr is None:
            raise Exception("File is not compressed with LASzip")

        return Reader._parse_laszip_record(laszip_vlr['data'])

    @property
    def num_points(self):
        return self.header['number_of_point_records']

    @staticmethod
    def _read_chunk_table(fp, dec):
        chunk_table_start_position = unsigned_int(fp.read(8))
        chunks_start = fp.tell()

        fp.seek(chunk_table_start_position)

        version = unsigned_int(fp.read(4))
        if version != 0:
            raise Exception("Unknown chunk table version")

        number_chunks = unsigned_int(fp.read(4))
        # chunk_totals = 0
        tabled_chunks = 1

        dec.start()

        ic = IntegerCompressor(dec, 32, 2)
        ic.init_decompressor()

        # read chunk sizes
        chunk_sizes = []
        pred = 0
        for i in range(number_chunks-1):
            chunk_size = ic.decompress(pred, 1)
            chunk_sizes.append(chunk_size)

            pred = chunk_size
            tabled_chunks += 1

        # calculate chunk offsets
        chunk_starts = [chunks_start]
        for chunk_size in chunk_sizes:
            chunk_starts.append(chunk_starts[-1] + chunk_size)

        fp.seek(chunks_start)

        return chunk_starts

    def open(self, filename):
        if not sys.byteorder == 'little':
            raise NotImplementedError("Only little endian is supported")

        self.fp = open(filename, 'rb')

        # Read standard LAS header
        self.header = Reader._read_las_header(self.fp)
        self.laz_header = Reader._read_laz_header(self.header)

        # clear the bit that indicates that the file is compressed
        self.header['point_data_format_id'] &= 0b01111111

        if self.laz_header['compressor'] == Compressor.POINTWISE:
            raise Exception("Pointwise compressor not supported")

        # create decoder
        if self.laz_header['coder'] == Coder.ARITHMETIC:
            self.dec = ArithmeticDecoder(self.fp)
        else:
            raise Exception("Unknown coder")

        self.chunk_starts = self._read_chunk_table(self.fp, self.dec)

        self._init_point_reader_functions()

    def read(self):
        context = 0

        point = []

        # if this is a new chunk
        # read the first uncompressed point and then initialize them
        if self.chunk_count == self.chunk_size:
            for reader_raw, reader_compressed in zip(self.readers_raw,
                                                     self.readers_compressed):
                pt_section = reader_raw(self.fp)
                reader_compressed.init(pt_section, context)

                point.append(pt_section)

            self.dec.start()

            self.chunk_count = 0
        else:
            for reader in self.readers_compressed:
                pt_section = reader.read(context)
                point.append(pt_section)

        self.chunk_count += 1
        return point

    def jump_to_chunk(self, chunk):
        self.fp.seek(self.chunk_starts[chunk])
        self.chunk_count = self.chunk_size


def read_txtfile_entries(filename):
    """Read a text file and return a list of lines."""
    with open(filename, 'r') as f:
        for line in f:
            yield [int(x) for x in line.split()]


def fast_forward(iterable, n):
    for i in range(n):
        next(iterable)


def main(filename, txtpoints_filename):

    print("Opening file: {}".format(filename))

    reader = Reader()

    reader.open(filename)

    print("num points: ", reader.num_points)

    target_point_index = 60000
    chunk_index = target_point_index // reader.chunk_size

    i_start = chunk_index*reader.chunk_size

    entries = read_txtfile_entries(txtpoints_filename)

    if chunk_index > 0:
        print(f"fast forwarding to desired point to i:{i_start} "
              f"chunk:{chunk_index}")
        fast_forward(entries, i_start)
        reader.jump_to_chunk(chunk_index)

    for i, entry in zip(range(i_start, reader.num_points), entries):

        try:
            point = reader.read()
        except Exception as e:
            print("error at point: ", i)
            raise e

        comp = [i, point[0].x, point[0].y, point[0].z, point[0].intensity,
                point[1]]

        if comp != entry:
            print("mismatch at ", i)
            print("us", comp)
            print("them", entry)
            exit()

        if i % 1000 == 0:
            print(i, ":", [str(x) for x in point])


if __name__ == '__main__':

    # get first command line argument
    if len(sys.argv) > 2:
        filename = sys.argv[1]
        pointstxt = sys.argv[2]
    else:
        print("Usage: pylaszip.py filename.laz points.txt")
        sys.exit(1)

    main(filename, pointstxt)
