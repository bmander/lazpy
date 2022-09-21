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


def get_readers(laszip_header):
    readers = []
    for item in laszip_header['items']:
        if item['type'] == ItemType.POINT10:
            readers.append(point10_reader)
        elif item['type'] == ItemType.GPSTIME11:
            readers.append(gps_time11_reader)
        elif item['type'] in {ItemType.RGB12, ItemType.RGBNIR14}:
            readers.append(rgb_reader)
        elif item['type'] in {ItemType.BYTE, ItemType.BYTE14}:
            readers.append(byte_reader)
        elif item['type'] == ItemType.Point14:
            readers.append(point14_reader)
        elif item['type'] == ItemType.RGBNIR14:
            readers.append(rgbnir14_reader)
        elif item['type'] in {ItemType.WAVEPACKET13, ItemType.WAVEPACKET14}: 
            readers.append(wavepacket_reader)
        else:
            raise Exception("Unknown item type")
    return readers

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
        scale = 0x80000000 / self.bit_count
        self.bit_0_prob = (self.bit_0_count * scale) >> (31 - self.BM_LENGTH_SHIFT)

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
            if self.num_symbols<2 or self.num_symbols>2048:
                raise Exception("Invalid number of symbols")
            
            self.last_symbol = self.num_symbols-1

            if not self.compress and self.num_symbols>16:
                table_bits = 3
                while self.num_symbols > (1 << (table_bits+2)):
                    table_bits += 1

                self.table_shift = self.DM_LENGTH_SHIFT - table_bits

                self.table_size = 1 << table_bits
                self.decoder_table = [0] * (self.table_size + 2)
            else: # small alphabet; no table needed
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
        self.symbols_until_update = self.update_cycle = (self.num_symbols+6) >> 1


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

        if self.compress or self.table_size==0:
            for k in range(self.num_symbols):
                self.distribution[k] = (scale*sum) >> (31 - self.DM_LENGTH_SHIFT)
                sum += self.symbol_count[k]
        else:
            for k in range(self.num_symbols):
                self.distribution[k] = (scale*sum) >> (31 - self.DM_LENGTH_SHIFT)
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
        ret = f"ArithmeticModel(num_symbols={self.num_symbols}, compress={self.compress}, " \
        f"distribution={self.distribution}, decoder_table={self.decoder_table}, " \
        f"symbol_count={self.symbol_count}, total_count={self.total_count}, " \
        f"update_cycle={self.update_cycle}, symbols_until_update={self.symbols_until_update})"

        return ret


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

            while(n > sym+1):
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
        assert bits>0 and bits<=32

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


    def create_symbol_model(self, num_symbols):
        return ArithmeticModel(num_symbols, False)

    def done(self):
        self.fp = None


def not_implemented_func(*args, **kwargs):
    raise NotImplementedError

read_item_compressed_point10_v1 = not_implemented_func
read_item_compressed_point10_v2 = not_implemented_func
read_item_compressed_gpstime11_v1 = not_implemented_func
read_item_compressed_gpstime11_v2 = not_implemented_func
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

def _read_item_raw_point10(fp):
    x = unsigned_int(fp.read(4))
    y = unsigned_int(fp.read(4))
    z = unsigned_int(fp.read(4))
    intensity = unsigned_int(fp.read(2))

    bitfield = unsigned_int(fp.read(1))
    return_num = bitfield & 0b00000111
    num_returns = (bitfield & 0b00111000) >> 3
    scan_dir_flag = (bitfield & 0b01000000) >> 6
    edge_of_flight_line = (bitfield & 0b10000000) >> 7

    classification = unsigned_int(fp.read(1))
    scan_angle_rank = unsigned_int(fp.read(1))
    user_data = unsigned_int(fp.read(1))
    point_source_id = unsigned_int(fp.read(2))

    return (x, y, z, intensity, return_num, num_returns, scan_dir_flag, edge_of_flight_line, classification, scan_angle_rank, user_data, point_source_id)

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
            while(range != 0):
                range >>= 1
                corr_bits += 1
            if self.corr_range == (1 << (corr_bits - 1)):
                corr_bits -= 1
            self.corr_min = -self.corr_range//2
            self.corr_max = self.corr_min+self.corr_range-1
        elif bits > 0 and bits < 32:
            self.corr_bits = bits
            self.corr_range = 1 << bits
            self.corr_min = -self.corr_range//2
            self.corr_max = self.corr_min+self.corr_range-1
        else:
            self.corr_bits = 32
            self.corr_range = 0
            self.corr_min = -0x7FFFFFFF
            self.corr_max = 0x7FFFFFFF

        self.m_bits = None
        self.m_corrector = None

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
                    self.m_corrector.append(self.dec.create_symbol_model(1<<i))
                else:
                    self.m_corrector.append(self.dec.create_symbol_model(1<<self.bits_high))

        for i in range(self.contexts):
            self.m_bits[i].init()

        for i in range(1, self.corr_bits):
            self.m_corrector[i].init()


    def _read_corrector(self, model):
        k = self.dec.decode_symbol(model)

        if k != 0:
            if k < 32:
                if k <= self.bits_high:
                    c = self.dec.decode_symbol(self.m_corrector[k])
                else:
                    k1 = k-self.bits_high
                    c = self.dec.decode_symbol(self.m_corrector[k])
                    c1 = self.dec.read_bits(k1)
                    c = (c << k1) | c1
                
                # translate c back into its correct interval
                if c >= (1 << (k-1)):
                    c += 1
                else:
                    c -= (1<<k)-1

            else:
                c = self.corr_min
        else:
            c = self.dec.decode_bit(self.m_corrector[0])

        return c


    def decompress(self, pred, context):
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
        self.readers_raw = []
        for item in reader.header['laszip']['items']:
            if item['type'] == ItemType.POINT10:
                self.readers_raw.append( las_read_item_raw_point10_le )
            elif item['type'] == ItemType.GPSTIME11:
                self.readers_raw.append( las_read_item_raw_gpstime11_le )
            elif item['type'] in {ItemType.RGB12, ItemType.RGB14}:
                self.readers_raw.append( read_item_raw_rgb12 )
            elif item['type'] in {ItemType.BYTE, ItemType.BYTE14}:
                self.readers_raw.append( read_item_raw_byte )
            elif item['type'] == ItemType.RGBNIR14:
                self.readers_raw.append( read_item_raw_rgbnir14 )
            elif item['type'] in {ItemType.WAVEPACKET13, ItemType.WAVEPACKET14}:
                self.readers_raw.append( read_item_raw_wavepacket13 )
            else:
                raise Exception("Unknown item type")

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
                func = type_version_compressed_reader[key]
                self.readers_compressed.append(func)
            else:
                raise Exception("Unknown item type/version")

        # create seek table
        self.seek_point = []
        for item in reader.header['laszip']['items']:
            self.seek_point.append( [0]*item['size'] )

        if reader.header['laszip']['compressor'] != Compressor.POINTWISE:
            self.chunk_size = reader.header['laszip']['chunk_size']
        else:
            raise Exception("Pointwise compressor not supported")


    def init(self, fp):
        self.fp = fp


    def _read_chunk_table(self):
        chunk_table_start_position = unsigned_int( self.fp.read(8) )
        chunks_start = self.fp.tell()

        self.fp.seek(chunk_table_start_position)

        version = unsigned_int( self.fp.read(4) )
        if version != 0:
            raise Exception("Unknown chunk table version")

        number_chunks = unsigned_int( self.fp.read(4) )
        chunk_totals = 0
        tabled_chunks = 1

        self.dec.init(self.fp)

        ic = IntegerCompressor(self.dec, 32, 2)
        ic.init_decompressor()

        # read chunk sizes
        chunk_sizes = []
        pred = 0
        for i in range(number_chunks-1):
            chunk_size = ic.decompress(pred, 1)
            chunk_sizes.append( chunk_size )

            pred = chunk_size
            tabled_chunks += 1

        self.dec.done()

        # calculate chunk offsets
        chunk_starts = [chunks_start]
        for chunk_size in chunk_sizes:
            chunk_starts.append( chunk_starts[-1] + chunk_size )

        self.fp.seek(chunks_start)


    def read(self):
        context = 0

        # init_decoders
        self._read_chunk_table()

        self.point_start = self.fp.tell()

        self.chunk_count += 1

        for reader_raw in self.readers_raw:
            pt = reader_raw(self.fp)
            print(pt)
        exit()


        

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
            ('start_of_first_extended_variable_length_record', 8, unsigned_int),
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

        # Read LASzip record, stored in the data payload of a variable length record
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

    print( reader.npoints )

    for i in range(reader.num_points):
        point = reader.point_reader.read()
        print(point)
        break


if __name__ == '__main__':

    # get first command line argument
    import sys
    if len(sys.argv) > 1:
        filename = sys.argv[1]
    else:
        print("Usage: pylaszip.py filename.laz")
        sys.exit(1)

    main(filename)
