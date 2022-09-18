from re import A
import struct
from enum import IntEnum

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
    RGBNIR14 = 11
    WAVEPACKET14 = 12
    BYTE14 = 13

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


def read_variable_length_record(fp):
    record = {}
    record['reserved'] = unsigned_int(fp.read(2))
    record['user_id'] = cstr(fp.read(16))
    record['record_id'] = unsigned_int(fp.read(2))
    record['record_length_after_header'] = unsigned_int(fp.read(2))
    record['description'] = cstr(fp.read(32))
    record['data'] = fp.read(record['record_length_after_header'])
    return record


def read_las_header(fp):
    header_format_12 = {
        'file_signature': (4, cstr),
        'file_source_id': (2, unsigned_int),
        'global_encoding': (2, unsigned_int),
        'guid_data_1': (4, unsigned_int),
        'guid_data_2': (2, unsigned_int),
        'guid_data_3': (2, unsigned_int),
        'guid_data_4': (8, cstr),
        'version_major': (1, unsigned_int),
        'version_minor': (1, unsigned_int),
        'system_identifier': (32, cstr),
        'generating_software': (32, cstr),
        'file_creation_day': (2, unsigned_int),
        'file_creation_year': (2, unsigned_int),
        'header_size': (2, unsigned_int),
        'offset_to_point_data': (4, unsigned_int),
        'number_of_variable_length_records': (4, unsigned_int),
        'point_data_format_id': (1, unsigned_int),
        'point_data_record_length': (2, unsigned_int),
        'number_of_point_records': (4, unsigned_int),
        'number_of_points_by_return': (4*5, u32_array),
        'x_scale_factor': (8, double),
        'y_scale_factor': (8, double),
        'z_scale_factor': (8, double),
        'x_offset': (8, double),
        'y_offset': (8, double),
        'z_offset': (8, double),
        'max_x': (8, double),
        'min_x': (8, double),
        'max_y': (8, double),
        'min_y': (8, double),
        'max_z': (8, double),
        'min_z': (8, double),
    }

    header_format_13 = {
        'start_of_waveform_data_packet_record': (8, unsigned_int),
    }

    header_format_14 = {
        'start_of_first_extended_variable_length_record': (8, unsigned_int),
        'number_of_extended_variable_length_records': (4, unsigned_int),
        'number_of_point_records': (8, unsigned_int),
        'number_of_points_by_return': (8*15, u64_array),
    }

    def read_into_header(header, format):
        bytes_read = 0
        for key, (size, func) in format.items():
            bytes_read += size
            header[key] = func(fp.read(size))
        return bytes_read

    header = {}
    bytes_read = 0

    # Read header
    bytes_read += read_into_header(header, header_format_12)

    # Check that the file is a LAS file
    if header['file_signature'] != b'LASF':
        raise Exception("Invalid file signature")

    # Read 1.3 header fields
    if header['version_major'] == 1 and header['version_minor'] >= 3:
        bytes_read += read_into_header(header, header_format_13)

    # Read 1.4 header fields
    if header['version_major'] == 1 and header['version_minor'] >= 4:
        bytes_read += read_into_header(header, header_format_14)

    # Read user data
    user_data_size = header['header_size'] - bytes_read
    header['user_data'] = fp.read(user_data_size)

    # Read variable length records
    header['variable_length_records'] = {}
    for i in range(header['number_of_variable_length_records']):
        vlr = read_variable_length_record(fp)
        header['variable_length_records'][vlr['record_id']] = vlr

    return header


def parse_laszip_header(data):
    header_format = {
        'compressor': (2, unsigned_int),
        'coder': (2, unsigned_int),
        'version_major': (1, unsigned_int),
        'version_minor': (1, unsigned_int),
        'version_revision': (2, unsigned_int),
        'options': (4, unsigned_int),
        'chunk_size': (4, signed_int),
        'number_of_special_evlrs': (8, signed_int),
        'offset_to_special_evlrs': (8, signed_int),
        'number_of_items': (2, unsigned_int),
    }

    header = {}
    offset = 0
    for key, (size, func) in header_format.items():
        header[key] = func(data[offset:offset+size])
        offset += size

    header['items'] = []
    for i in range(header['number_of_items']):
        item = {}
        item['type'] = unsigned_int(data[offset:offset+2])
        item['size'] = unsigned_int(data[offset+2:offset+4])
        item['version'] = unsigned_int(data[offset+4:offset+6])
        offset += 6
        header['items'].append(item)

    header['user_data'] = data[offset:]

    return header

def get_decoder(laszip_header):
    if laszip_header['coder'] == Coder.ARITHMETIC:
        return ArithmeticDecoder()
    else:
        raise Exception("Unknown coder")

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

    def _init(self, table=None):
        if self.distribution is None:
            if self.num_symbols<2 or self.num_symbols>2048:
                raise Exception("Invalid number of symbols")
            
            self.last_symbol = self.num_symbols-1

            if not self.compress and self.num_symbols>16:
                self.table_bits = 3
                while self.num_symbols > (1 << (self.table_bits+2)):
                    self.table_bits += 1

                self.table_size = 1 << self.table_bits
                self.table_shift = self.DM_LENGTH_SHIFT - self.table_bits

                self.decoder_table = [0] * (self.table_size + 2)
            else:
                self.table_size = self.table_shift = 0

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


class ArithmeticDecoder:
    AC_MAX_LENGTH = 0xFFFFFFFF
    AC_MIN_LENGTH = 0x01000000

    def __init__(self, fp, really_init=True):
        self.fp = fp
        self.length = self.AC_MAX_LENGTH

        if really_init:
            self.value = unsigned_int(fp.read(4))

    def decode_bit(self, m):
        # m is a ArithmeticBitModel
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

class Reader:
    def __init__(self):
        pass

    def open(self, filename):
        pass

    def read_point(self):
        pass


def main(filename):
    print("Opening file: {}".format(filename))

    with open(filename, 'rb') as f:
        # Read basic LAS header
        header = read_las_header(f)

        print(header)

        # Read LASzip header, stored as a variable length record in the LAS header
        LASZIP_VLR_ID = 22204
        laszip_vlr = header['variable_length_records'].get(LASZIP_VLR_ID)
        if laszip_vlr is None:
            raise Exception("File is not compressed with LASzip")

        laszip_header = parse_laszip_header(laszip_vlr['data'])
        print("LASzip header: {}".format(laszip_header))

        print( laszip_header['coder'] == Coder.ARITHMETIC)

        print( laszip_header['coder'] == 0 )

        print( Coder.ARITHMETIC == 0)

        decoder = get_decoder(laszip_header)
        readers = get_readers(laszip_header)



if __name__ == '__main__':

    # get first command line argument
    import sys
    if len(sys.argv) > 1:
        filename = sys.argv[1]
    else:
        print("Usage: pylaszip.py filename.laz")
        sys.exit(1)

    main(filename)
