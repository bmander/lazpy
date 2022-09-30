from utils import unsigned_int


class LasPoint10:
    def __init__(self, bytes: bytes):
        self.bytes = bytearray(bytes)

    @property
    def x(self):
        return unsigned_int(self.bytes[0:4])

    @x.setter
    def x(self, value):
        self.bytes[0:4] = value.to_bytes(4, byteorder='little')

    @property
    def y(self):
        return unsigned_int(self.bytes[4:8])

    @y.setter
    def y(self, value):
        self.bytes[4:8] = value.to_bytes(4, byteorder='little')

    @property
    def z(self):
        return unsigned_int(self.bytes[8:12])

    @z.setter
    def z(self, value):
        self.bytes[8:12] = value.to_bytes(4, byteorder='little')

    @property
    def intensity(self):
        return unsigned_int(self.bytes[12:14])

    @intensity.setter
    def intensity(self, value):
        self.bytes[12:14] = value.to_bytes(2, byteorder='little')

    @property
    def bitfield(self):
        return unsigned_int(self.bytes[14:15])

    @bitfield.setter
    def bitfield(self, value):
        self.bytes[14:15] = value.to_bytes(1, byteorder='little')

    @property
    def return_num(self):
        return unsigned_int(self.bytes[14:15]) & 0b00000111

    @property
    def num_returns(self):
        return (unsigned_int(self.bytes[14:15]) & 0b00111000) >> 3

    @property
    def scan_dir_flag(self):
        return (unsigned_int(self.bytes[14:15]) & 0b01000000) >> 6

    @property
    def edge_of_flight_line(self):
        return (unsigned_int(self.bytes[14:15]) & 0b10000000) >> 7

    @property
    def classification(self):
        return unsigned_int(self.bytes[15:16])

    @classification.setter
    def classification(self, value):
        self.bytes[15:16] = value.to_bytes(1, byteorder='little')

    @property
    def scan_angle_rank(self):
        return unsigned_int(self.bytes[16:17])

    @scan_angle_rank.setter
    def scan_angle_rank(self, value):
        self.bytes[16:17] = value.to_bytes(1, byteorder='little')

    @property
    def user_data(self):
        return unsigned_int(self.bytes[17:18])

    @user_data.setter
    def user_data(self, value):
        self.bytes[17:18] = value.to_bytes(1, byteorder='little')

    @property
    def point_source_id(self):
        return unsigned_int(self.bytes[18:20])

    @point_source_id.setter
    def point_source_id(self, value):
        self.bytes[18:20] = value.to_bytes(2, byteorder='little')

    def copy(self):
        return LasPoint10(bytes(self.bytes))

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