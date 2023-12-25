from cpylaz import IntegerCompressor
from utils import StreamingMedian5, u8_fold, u32_zero_bit_0, unsigned_int
from lastypes import LasPoint10


def not_implemented_func(*args, **kwargs):
    raise NotImplementedError


read_item_compressed_point10_v1 = not_implemented_func


class read_item_compressed_point10_v2:

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
            bitfield = self.last_item.bitfield
            if bitfield not in self.m_bit_byte:
                model = self.dec.create_symbol_model(256)
                model.init()
                self.m_bit_byte[bitfield] = model

            bitfield = self.dec.decode_symbol(self.m_bit_byte[bitfield])
            self.last_item.bitfield = bitfield

        r = self.last_item.return_num
        n = self.last_item.num_returns
        m = self.NUMBER_RETURN_MAP[n][r]
        el = self.NUMBER_RETURN_LEVEL[n][r]

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
        k_bits = self.ic_dx.k #TODO should this be ic_dy.k?
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


read_item_compressed_gpstime11_v1 = not_implemented_func


class read_item_compressed_gpstime11_v2:

    LASZIP_GPSTIME_MULTI = 500
    LASZIP_GPSTIME_MULTI_MINUS = -10
    LASZIP_GPSTIME_MULTI_TOTAL = LASZIP_GPSTIME_MULTI - \
        LASZIP_GPSTIME_MULTI_MINUS + 6
    LASZIP_GPSTIME_MULTI_UNCHANGED = LASZIP_GPSTIME_MULTI - \
        LASZIP_GPSTIME_MULTI_MINUS + 1
    LASZIP_GPSTIME_MULTI_CODE_FULL = LASZIP_GPSTIME_MULTI - \
        LASZIP_GPSTIME_MULTI_MINUS + 2

    def __init__(self, dec):
        self.dec = dec

        self.m_gpstime_multi = self.dec.create_symbol_model(
            self.LASZIP_GPSTIME_MULTI_TOTAL)
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
        elif multi < self.LASZIP_GPSTIME_MULTI_UNCHANGED:
            if multi == 0:
                gpstime_diff = self.ic_gpstime.decompress(0, 7)
                self.multi_extreme_counter[self.last] += 1
                if self.multi_extreme_counter[self.last] > 3:
                    self.last_gpstime_diff[self.last] = gpstime_diff
                    self.multi_extreme_counter[self.last] = 0
            elif multi < self.LASZIP_GPSTIME_MULTI:
                pred = multi*self.last_gpstime_diff[self.last]
                context = 2 if multi < 10 else 3
                gpstime_diff = self.ic_gpstime.decompress(pred, context)
            elif multi == self.LASZIP_GPSTIME_MULTI:
                pred = self.LASZIP_GPSTIME_MULTI * \
                    self.last_gpstime_diff[self.last]
                gpstime_diff = self.ic_gpstime.decompress(pred, 4)
                self.multi_extreme_counter[self.last] += 1
                if self.multi_extreme_counter[self.last] > 3:
                    self.last_gpstime_diff[self.last] = gpstime_diff
                    self.multi_extreme_counter[self.last] = 0
            else:
                multi = self.LASZIP_GPSTIME_MULTI - multi
                if multi > self.LASZIP_GPSTIME_MULTI_MINUS:
                    pred = multi*self.last_gpstime_diff[self.last]
                    gpstime_diff = self.ic_gpstime.decompress(pred, 5)
                else:
                    pred = self.LASZIP_GPSTIME_MULTI_MINUS * \
                            self.last_gpstime_diff[self.last]
                    gpstime_diff = self.ic_gpstime.decompress(pred, 6)
                    self.multi_extreme_counter[self.last] += 1
                    if self.multi_extreme_counter[self.last] > 3:
                        self.last_gpstime_diff[self.last] = gpstime_diff
                        self.multi_extreme_counter[self.last] = 0

            self.last_gpstime[self.last] += gpstime_diff
        elif multi == self.LASZIP_GPSTIME_MULTI_CODE_FULL:
            self.next = (self.next+1) & 3
            pred = self.last_gpstime[self.last] >> 32
            val = self.ic_gpstime.decompress(pred, 8)
            val <<= 32
            val = val | self.dec.read_int()
            self.last_gpstime[self.next] = val

            self.last = self.next
            self.last_gpstime_diff[self.last] = 0
            self.multi_extreme_counter[self.last] = 0
        elif multi >= self.LASZIP_GPSTIME_MULTI_CODE_FULL:
            self.last = (self.last+multi-self.LASZIP_GPSTIME_MULTI_CODE_FULL) \
                & 3
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

# raw readers


def las_read_item_raw_point10_le(fp):
    return LasPoint10(bytearray(fp.read(20)))


def las_read_item_raw_gpstime11_le(fp):
    return unsigned_int(fp.read(8))
