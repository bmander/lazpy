#!/usr/bin/env python3
"""Compare lazpy's decoded points against a laszip reference dump.

The reference dump is produced by tools/lazdump.c (built against liblaszip);
each line holds every decoded field of one point, with gps_time written as raw
IEEE-754 bits so the comparison is exact rather than approximate.

    python tools/compare_with_laszip.py cloud.laz reference.txt [max_points]

Exits non-zero and prints the first mismatching field on failure.
"""
import struct
import sys

sys.path.insert(0, ".")
from lazpy import Reader  # noqa: E402

# Column layout of a reference dump line, in order.
SCALAR_FIELDS = [
    "X", "Y", "Z", "intensity", "return_number", "number_of_returns",
    "scan_direction_flag", "edge_of_flight_line", "classification",
    "synthetic_flag", "keypoint_flag", "withheld_flag", "scan_angle_rank",
    "user_data", "point_source_ID",
]
# then: gps_bits, rgb[0..3], extended_*, num_extra_bytes, wave_packet[29], extra
EXTENDED_FIELDS = [
    "extended_point_type", "extended_scanner_channel",
    "extended_classification_flags", "extended_classification",
    "extended_scan_angle", "extended_return_number",
    "extended_number_of_returns",
]


def gps_bits(value):
    """Reinterpret a float as the u64 the reference dump prints."""
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def compare(laz_path, dump_path, max_points=None):
    reader = Reader(laz_path)
    mismatches = []
    n = 0

    with open(dump_path) as fh:
        header = fh.readline()
        if not header.startswith("#"):
            raise SystemExit(f"{dump_path}: missing header line")

        for line in fh:
            cols = line.split()
            if not cols:
                continue
            if max_points is not None and n >= max_points:
                break

            expected_index = int(cols[0])
            point = reader.read()
            got = {}
            want = {}

            for i, name in enumerate(SCALAR_FIELDS, start=1):
                got[name] = getattr(point, name)
                want[name] = int(cols[i])

            got["gps_time"] = gps_bits(point.gps_time)
            want["gps_time"] = int(cols[16])

            rgb = point.rgb
            for i in range(4):
                got[f"rgb{i}"] = rgb[i]
                want[f"rgb{i}"] = int(cols[17 + i])

            for i, name in enumerate(EXTENDED_FIELDS, start=21):
                got[name] = getattr(point, name)
                want[name] = int(cols[i])

            num_extra = int(cols[28])
            wave = point.wave_packet
            for i in range(29):
                got[f"wave{i}"] = wave[i]
                want[f"wave{i}"] = int(cols[29 + i])

            extra = point.extra_bytes
            for i in range(num_extra):
                got[f"extra{i}"] = extra[i] if i < len(extra) else None
                want[f"extra{i}"] = int(cols[58 + i])

            for name in want:
                if got[name] != want[name]:
                    mismatches.append(
                        (expected_index, name, got[name], want[name]))
                    if len(mismatches) >= 10:
                        break
            if len(mismatches) >= 10:
                break
            n += 1

    return n, mismatches


def hash_file(laz_path, max_points=None):
    """Whole-file hash, computed in C so 40M-point files stay practical."""
    reader = Reader(laz_path)
    n = reader.num_points if max_points is None else min(max_points, reader.num_points)
    return reader.checksum(n)


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    laz_path, dump_path = sys.argv[1], sys.argv[2]
    max_points = int(sys.argv[3]) if len(sys.argv) > 3 else None

    if dump_path == "--hash":
        h, n = hash_file(laz_path, max_points)
        print(f"{h} {n}")
        return 0

    n, mismatches = compare(laz_path, dump_path, max_points)

    if mismatches:
        print(f"FAIL  {laz_path}: {len(mismatches)} mismatch(es) "
              f"in the first {n + 1} points")
        for index, name, got, want in mismatches:
            print(f"  point {index}: {name} = {got}, laszip says {want}")
        return 1

    print(f"OK    {laz_path}: {n} points match laszip exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
