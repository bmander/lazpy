import struct

# LAS file specification
# 1.2: https://www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf
# 1.4: https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf


def unsigned_int(bytes):
    return int.from_bytes(bytes, byteorder='little', signed=False)


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


def read_header(fp):
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
    header['variable_length_records'] = []
    for i in range(header['number_of_variable_length_records']):
        header['variable_length_records'].append(read_variable_length_record(fp))

    return header


def main(filename):
    print("Opening file: {}".format(filename))

    with open(filename, 'rb') as f:
        print(read_header(f))


if __name__ == '__main__':

    # get first command line argument
    import sys
    if len(sys.argv) > 1:
        filename = sys.argv[1]
    else:
        print("Usage: pylaszip.py filename.laz")
        sys.exit(1)

    main(filename)
