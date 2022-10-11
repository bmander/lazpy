from enum import IntEnum
import sys
from utils import unsigned_int, u32_array, u64_array, double, cstr, \
    signed_int
from cpylaz import ArithmeticDecoder, IntegerCompressor
import readers as rd

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


TYPE_RAW_READER = {
    ItemType.POINT10: rd.las_read_item_raw_point10_le,
    ItemType.GPSTIME11: rd.las_read_item_raw_gpstime11_le,
    # ItemType.RGB12: las_read_item_raw_rgb12_le,
    # ItemType.BYTE: las_read_item_raw_byte_le,
    # ItemType.RGBNIR14: las_read_item_raw_rgbnir14_le,
    # ItemType.WAVEPACKET13: las_read_item_raw_wavepacket13_le,
}


TYPE_VERSION_COMPRESSED_READER = {
    (ItemType.POINT10, 1): rd.read_item_compressed_point10_v1,
    (ItemType.POINT10, 2): rd.read_item_compressed_point10_v2,
    (ItemType.GPSTIME11, 1): rd.read_item_compressed_gpstime11_v1,
    (ItemType.GPSTIME11, 2): rd.read_item_compressed_gpstime11_v2,
    (ItemType.RGB12, 1): rd.read_item_compressed_rgb12_v1,
    (ItemType.RGB12, 2): rd.read_item_compressed_rgb12_v2,
    (ItemType.BYTE, 1): rd.read_item_compressed_byte_v1,
    (ItemType.BYTE, 2): rd.read_item_compressed_byte_v2,
    (ItemType.POINT14, 3): rd.read_item_compressed_point14_v3,
    (ItemType.POINT14, 4): rd.read_item_compressed_point14_v4,
    (ItemType.RGB14, 3): rd.read_item_compressed_rgb12_v3,
    (ItemType.RGB14, 4): rd.read_item_compressed_rgb12_v4,
    (ItemType.RGBNIR14, 3): rd.read_item_compressed_rgbnir14_v3,
    (ItemType.RGBNIR14, 4): rd.read_item_compressed_rgbnir14_v4,
    (ItemType.BYTE14, 3): rd.read_item_compressed_byte_v3,
    (ItemType.BYTE14, 4): rd.read_item_compressed_byte_v4,
    (ItemType.WAVEPACKET13, 1):
        rd.read_item_compressed_wavepacket13_v1,
    (ItemType.WAVEPACKET14, 3):
        rd.read_item_compressed_wavepacket14_v3,
    (ItemType.WAVEPACKET14, 4):
        rd.read_item_compressed_wavepacket14_v4
}


class Reader:
    def __init__(self):
        pass

    def _init_point_reader_functions(self):

        # get raw reader functions
        self.readers_raw = []
        for item in self.laz_header['items']:
            func = TYPE_RAW_READER.get(item['type'])

            if func is None:
                raise Exception("Unknown item type")

            self.readers_raw.append(func)

        # get compressed reader functions
        self.readers_compressed = []
        for item in self.laz_header['items']:
            key = (item['type'], item['version'])

            if key not in TYPE_VERSION_COMPRESSED_READER:
                raise Exception("Unkown item type/version")

            compressed_reader_class = TYPE_VERSION_COMPRESSED_READER[key]
            compressed_reader = compressed_reader_class(self.dec)

            self.readers_compressed.append(compressed_reader)

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
        HEADER_FORMAT_12 = (
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

        HEADER_FORMAT_13 = (
            ('start_of_waveform_data_packet_record', 8, unsigned_int),
        )

        HEADER_FORMAT_14 = (
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
        read_into_header(fp, header, HEADER_FORMAT_12)
        bytes_read = header_section_size(HEADER_FORMAT_12)

        # Ensure the file is a LAS file
        if header['file_signature'] != b'LASF':
            raise Exception("Invalid file signature")

        # Read 1.3 header fields
        if header['version_major'] == 1 and header['version_minor'] >= 3:
            read_into_header(fp, header, HEADER_FORMAT_13)
            bytes_read += header_section_size(HEADER_FORMAT_13)

        # Read 1.4 header fields
        if header['version_major'] == 1 and header['version_minor'] >= 4:
            read_into_header(fp, header, HEADER_FORMAT_14)
            bytes_read += header_section_size(HEADER_FORMAT_14)

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

    @property
    def chunk_size(self):
        return self.laz_header['chunk_size']

    @staticmethod
    def _read_chunk_table(fp, dec):
        chunk_table_start_position = unsigned_int(fp.read(8))
        chunks_start = fp.tell()

        fp.seek(chunk_table_start_position)

        version = unsigned_int(fp.read(4))
        if version != 0:
            raise Exception("Unknown chunk table version")

        number_chunks = unsigned_int(fp.read(4))

        dec.start()

        # read chunk sizes
        ic = IntegerCompressor(dec, 32, 2)
        ic.init_decompressor()

        chunk_sizes = []
        chunk_size = 0
        for _ in range(number_chunks-1):
            chunk_size = ic.decompress(chunk_size, 1)
            chunk_sizes.append(chunk_size)

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

        # read standard las header
        self.header = Reader._read_las_header(self.fp)
        
        # read laz header
        self.laz_header = Reader._read_laz_header(self.header)

        if self.laz_header['compressor'] == Compressor.POINTWISE:
            raise Exception("Pointwise compressor not supported")

        # clear the bit that indicates that the file is compressed
        self.header['point_data_format_id'] &= 0b01111111

        # create decoder
        if self.laz_header['coder'] == Coder.ARITHMETIC:
            self.dec = ArithmeticDecoder(self.fp)
        else:
            raise Exception("Unknown coder")

        self.chunk_starts = self._read_chunk_table(self.fp, self.dec)

        self._init_point_reader_functions()

        # indicate the reader is at the end of the chunk in order
        # to force a read of the next chunk
        self.chunk_count = self.chunk_size

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
