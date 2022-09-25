import struct
from enum import IntEnum
import sys

# LAS file specification
# 1.2: https://www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf
# 1.4: https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf


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
        # initialize equiprobable model
        self.bit_0_count = 1
        self.bit_count = 2
        self.bit_0_prob = 1 << (self.BM_LENGTH_SHIFT - 1)

        # start with frequent updates
        self.update_cycle = self.bits_until_update = 1

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


class ArithmeticModel:
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
    AC_MAX_LENGTH = 0xFFFFFFFF
    AC_MIN_LENGTH = 0x01000000

    def __init__(self):
        pass

    def init(self, fp, really_init=True):
        self.fp = fp
        self.length = self.AC_MAX_LENGTH

        if really_init:
            data = fp.read(4)
            self.value = int.from_bytes(data, byteorder='big')

    def decode_bit(self, m):
        # m is an ArithmeticBitModel
        x = m.bit_0_prob * (self.length >> m.BM_LENGTH_SHIFT)

        if self.value < x:
            self.length = x
            m.bit_0_count += 1
        else:
            self.value -= x
            self.length -= x

        if self.length < self.AC_MIN_LENGTH:
            self._renorm_dec_interval()

        m.bits_until_update -= 1
        if m.bits_until_update == 0:
            m.update()

    def _renorm_dec_interval(self):
        while True:
            data = unsigned_int(self.fp.read(1))
            self.value = (self.value << 8) | data
            self.length <<= 8
            if self.length >= self.AC_MIN_LENGTH:
                break

    def decode_symbol(self, m):
        # m is an ArithmeticModel
        y = self.length

        if m.decoder_table is not None:
            self.length >>= m.DM_LENGTH_SHIFT
            dv = self.value // self.length
            t = dv >> m.table_shift

            sym = m.decoder_table[t]
            n = m.decoder_table[t+1] + 1

            while n > sym+1:
                k = (sym + n) >> 1
                if m.distribution[k] > dv:
                    n = k
                else:
                    sym = k

            x = m.distribution[sym] * self.length

            if sym != m.last_symbol:
                y = m.distribution[sym+1] * self.length

        else:
            x = sym = 0
            self.length >>= m.DM_LENGTH_SHIFT
            n = m.num_symbols
            k = n >> 1

            while True:
                z = self.length * m.distribution[k]
                if z > self.value:
                    n = k
                    y = z
                else:
                    sym = k
                    x = z

                k = (sym + n) >> 1
                if k == sym:
                    break

        self.value -= x
        self.length = y - x

        if self.length < self.AC_MIN_LENGTH:
            self._renorm_dec_interval()

        m.symbol_count[sym] += 1

        m.symbols_until_update -= 1
        if m.symbols_until_update == 0:
            m._update()

        assert sym < m.num_symbols

        return sym

    def read_short(self):
        self.length >>= 16
        sym = self.value // self.length

        if self.length < self.AC_MIN_LENGTH:
            self._renorm_dec_interval()

        return sym

    def read_bits(self, bits):
        assert bits > 0 and bits <= 32

        if bits > 19:
            tmp = self.read_short()
            bits = bits - 16
            tmp1 = self.read_bits(bits) << 16
            return tmp1 | tmp

        self.length >>= bits
        sym = self.value // (self.length)
        self.value -= sym * self.length

        if self.length < self.AC_MIN_LENGTH:
            self._renorm_dec_interval()

        return sym

    def read_int(self):
        lower = self.read_short()
        upper = self.read_short()

        return (upper << 16) | lower

    def create_symbol_model(self, num_symbols):
        return ArithmeticModel(num_symbols, False)

    def done(self):
        self.fp = None


class StreamingMedian5:
    def __init__(self):
        self.values = [0, 0, 0, 0, 0]
        self.high = True

    def _add_high(self, v):
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
        self.ic_point_source_ID = IntegerCompressor(dec, 16)
        self.m_bit_byte = [None]*256
        self.m_classification = [None]*256
        self.m_user_data = [None]*256
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
        self.ic_point_source_ID.init_decompressor()

        for i in range(256):
            if self.m_bit_byte[i] is not None:
                self.m_bit_byte[i].init()
            if self.m_classification[i] is not None:
                self.m_classification[i].init()
            if self.m_user_data[i] is not None:
                self.m_user_data[i].init()

        self.ic_dx.init_decompressor()
        self.ic_dy.init_decompressor()
        self.ic_z.init_decompressor()

        self.last_item = LasPoint10.from_bytes(item)
        self.last_item.intensity = 0

    def read(self, item, context):
        # TODO eliminate 'item' as a parameter
        ret = self.last_item.copy()

        changed_values = self.dec.decode_symbol(self.m_changed_values)

        r = self.last_item.return_num
        n = self.last_item.num_returns
        m = NUMBER_RETURN_MAP[n][r]
        el = NUMBER_RETURN_LEVEL[n][r]

        if changed_values != 0:
            # decompress bit field byte
            if changed_values & 0b100000:
                bitfield = self.last_item.bitfield_value()
                if self.m_bit_byte[bitfield] is None: # TODO is this redundant?
                    model = self.dec.create_symbol_model(256)
                    model.init()
                    self.m_bit_byte[bitfield] = model

                bitfield = self.dec.decode_symbol(self.m_bit_byte[bitfield])
                ret.set_bitfield(bitfield)

            # decompress intensity
            if changed_values & 0b10000:
                context = min(m, 3)
                ret.intensity = self.ic_intensity.decompress(
                                    self.last_intensity[m], context)
                self.last_intensity[m] = ret.intensity
            else:
                ret.intensity = self.last_intensity[m]

            # decompress classification
            if changed_values & 0b1000:
                if self.m_classification[self.last_item.classification] is None: # TODO is this redundant?
                    self.m_classification[self.last_item.classification] = \
                        self.dec.create_symbol_model(256)
                    self.m_classification[self.last_item.classification].init()
                ret.classification = self.dec.decode_symbol(
                    self.m_classification[self.last_item.classification])

            # decompress scan angle rank
            if changed_values & 0b100:
                f = self.last_item.scan_dir_flag
                val = self.dec.decode_symbol(self.m_scan_angle_rank[f])
                ret.scan_angle_rank = u8_fold(val +
                                              self.last_item.scan_angle_rank)

            # decompress user data
            if changed_values & 0b10:
                if self.m_user_data[self.last_item.user_data] is None: # TODO is this redundant?
                    self.m_user_data[self.last_item.user_data] = \
                        self.dec.create_symbol_model(256)
                    self.m_user_data[self.last_item.user_data].init()

                model = self.m_user_data[self.last_item.user_data]
                ret.user_data = self.dec.decode_symbol(model)

            # decompress point source ID
            if changed_values & 0b1:
                ret.point_source_ID = self.ic_point_source_ID.decompress(
                    self.last_item.point_source_ID)

        # decompress x
        median = self.last_x_diff_median5[m].get()
        diff = self.ic_dx.decompress(median, int(n == 1))
        ret.x = self.last_item.x + diff
        self.last_x_diff_median5[m].add(diff)

        # decompress y
        median = self.last_y_diff_median5[m].get()
        k_bits = self.ic_dx.k
        context = int(n == 1) + (u32_zero_bit_0(k_bits) if k_bits < 20 else 20)
        diff = self.ic_dy.decompress(median, context)
        ret.y = self.last_item.y + diff
        self.last_y_diff_median5[m].add(diff)

        # decompress z
        k_bits = (self.ic_dx.k + self.ic_dy.k) // 2
        context = int(n == 1) + (u32_zero_bit_0(k_bits) if k_bits < 18 else 18)
        ret.z = self.ic_z.decompress(self.last_height[el], context)
        self.last_height[el] = ret.z

        self.last_item = ret
        return ret


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

        import pdb; pdb.set_trace()
        self.last_gpstime = [unsigned_int(item), 0, 0, 0]

    def _read_lastdiff_zero(self, item, context):
        multi = self.dec.decode_symbol(self.m_gpstime_0diff)

        if multi == 1:  # the difference fits in 32 bits
            import pdb; pdb.set_trace()
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
            self.last = (self.last+self.multi-2) ^ 3
            self.read(item, context)

    def _read_lastdiff_nonzero(self, item, context):
        # TODO this is a mess

        multi = self.dec.decode_symbol(self.m_gpstime_multi)

        if multi == 1:
            pred = self.last_gpstime[self.last]
            val = self.ic_gpstime.decompress(pred, 1)
            self.last_gpstime[self.last] += val

            self.multi_extreme_counter[self.last] = 0
        elif multi < LASZIP_GPSTIME_MULTI_UNCHANGED:
            if multi == 0:
                gpstime_diff = self.ic_gpstime.decompress(0, 7)
                self.multi_extreme_counter[self.last] += 1
                if self.multi_extreme_counter[self.last] > 3:
                    self.last_gpstime[self.last] = gpstime_diff
                    self.multi_extreme_counter[self.last] = 0
            elif multi > LASZIP_GPSTIME_MULTI:
                pred = multi*self.last_gpstime[self.last]
                context = 2 if multi < 10 else 3
                gpstime_diff = self.ic_gpstime.decompress(pred, context)
            elif multi == LASZIP_GPSTIME_MULTI:
                pred = LASZIP_GPSTIME_MULTI*self.last_gpstime[self.last]
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
            self.read(item, context)

    def read(self, item, context):
        if self.last_gpstime_diff[self.last] == 0:
            self._read_lastdiff_zero(item, context)
        else:
            self._read_lastdiff_nonzero(item, context)

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
    return fp.read(20)


def _read_item_raw_gpstime11(fp):
    gps_time = double(fp.read(8))
    return (gps_time,)


def las_read_item_raw_gpstime11_le(fp):
    return fp.read(8)


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


class PointReader:
    def __init__(self, reader):
        if not sys.byteorder == 'little':
            raise NotImplementedError("Only little endian is supported")

        self.point_size = 0
        self.chunk_count = 0

        # create decoder
        if reader.header['laszip']['coder'] == Coder.ARITHMETIC:
            self.dec = ArithmeticDecoder()
        else:
            raise Exception("Unknown coder")

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
        for item in reader.header['laszip']['items']:
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
        for i, item in enumerate(reader.header['laszip']['items']):
            key = (item['type'], item['version'])
            if key in type_version_compressed_reader:
                compressed_reader_class = type_version_compressed_reader[key]
                compressed_reader = compressed_reader_class(self.dec)

                self.readers_compressed.append(compressed_reader)
            else:
                raise Exception("Unknown item type/version")

        # create seek table
        self.seek_point = []
        for item in reader.header['laszip']['items']:
            self.seek_point.append([0]*item['size'])

        if reader.header['laszip']['compressor'] != Compressor.POINTWISE:
            self.chunk_size = reader.header['laszip']['chunk_size']
        else:
            raise Exception("Pointwise compressor not supported")

        self.readers = None
        self.chunk_starts = None

    def init(self, fp):
        self.fp = fp

    def _read_chunk_table(self):
        chunk_table_start_position = unsigned_int(self.fp.read(8))
        chunks_start = self.fp.tell()

        self.fp.seek(chunk_table_start_position)

        version = unsigned_int(self.fp.read(4))
        if version != 0:
            raise Exception("Unknown chunk table version")

        number_chunks = unsigned_int(self.fp.read(4))
        # chunk_totals = 0
        tabled_chunks = 1

        self.dec.init(self.fp)

        ic = IntegerCompressor(self.dec, 32, 2)
        ic.init_decompressor()

        # read chunk sizes
        chunk_sizes = []
        pred = 0
        for i in range(number_chunks-1):
            chunk_size = ic.decompress(pred, 1)
            chunk_sizes.append(chunk_size)

            pred = chunk_size
            tabled_chunks += 1

        self.dec.done()

        # calculate chunk offsets
        self.chunk_starts = [chunks_start]
        for chunk_size in chunk_sizes:
            self.chunk_starts.append(self.chunk_starts[-1] + chunk_size)

        self.fp.seek(chunks_start)
        self.point_start = chunks_start

    def read(self):
        context = 0

        # if chunk table hasn't been read, read it
        if self.chunk_starts is None:
            self._read_chunk_table()

        point = []

        # if the compressed readers haven't been initialized,
        # read the first uncompressed point and then initialize them
        if self.readers is None:
            for reader_raw in self.readers_raw:
                pt_section = reader_raw(self.fp)
                point.append(pt_section)

            for i, reader_compressed in enumerate(self.readers_compressed):
                reader_compressed.init(point[i], context)

            self.dec.init(self.fp)

            self.readers = self.readers_compressed
        else:
            for reader in self.readers:
                pt_section = reader.read(point, context)
                point.append(pt_section)

        return point


class Reader:
    def __init__(self):
        pass

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
    def _read_laz_header(fp):
        # Read standard LAS header
        header = Reader._read_las_header(fp)

        # Read LASzip record, stored in the data payload of a variable length
        # record
        LASZIP_VLR_ID = 22204
        laszip_vlr = header['variable_length_records'].get(LASZIP_VLR_ID)
        if laszip_vlr is None:
            raise Exception("File is not compressed with LASzip")

        header['laszip'] = Reader._parse_laszip_record(laszip_vlr['data'])

        # clear the bit that indicates that the file is compressed
        header['point_data_format_id'] &= 0b01111111

        return header

    @property
    def num_points(self):
        return self.header['number_of_point_records']

    def open(self, filename):
        fp = open(filename, 'rb')

        self.header = self._read_laz_header(fp)

        self.point_reader = PointReader(self)
        self.point_reader.init(fp)

        self.npoints = self.header['number_of_point_records']
        self.p_count = 0


def main(filename):
    print("Opening file: {}".format(filename))

    reader = Reader()

    reader.open(filename)

    print("num points: ", reader.npoints)

    for i in range(reader.num_points):
        if i > 10:
            break

        point = reader.point_reader.read()
        print(i, ":", [str(x) for x in point])


if __name__ == '__main__':

    # get first command line argument
    if len(sys.argv) > 1:
        filename = sys.argv[1]
    else:
        print("Usage: pylaszip.py filename.laz")
        sys.exit(1)

    main(filename)
