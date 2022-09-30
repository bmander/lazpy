from utils import unsigned_int


class LasPoint10:
    # TODO
    # internally represent the point as a 20-byte array, and make
    # all the methods static functions tht operate on the array

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