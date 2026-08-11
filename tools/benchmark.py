#!/usr/bin/env python3
"""Time the decoder against a file big enough for the numbers to mean anything.

    python tools/benchmark.py [--points N] [--chunk-size N] [--format N]
                              [--file cloud.laz] [--repeat N]

The fixtures in `testdata/` are 500 points each, so a whole file is one small
chunk and every adaptive model stays in its most update-heavy early phase.
Nothing about steady-state decoding can be measured with them. This generates
a file at a production chunk size instead -- or reads one you already have,
with `--file` -- and times the paths that matter:

    sequential    read() per point, the slowest way out and the most common
    checksum      the same decode with no Python object per point: the floor
    arrays        the columnar path, which is what a caller should reach for
    seek          random access, which pays a chunk's worth of model setup
                  per jump and so is where per-chunk costs show up

Times are the best of `--repeat` runs, since the interesting comparisons are
between builds and the noise is all upward.
"""
import argparse
import io
import os
import random
import sys
import time

from lazpy import Point, Reader, Writer


def generate(points, chunk_size, point_format):
    """A synthetic file, in memory, at a realistic chunk size."""
    rng = random.Random(7)
    buf = io.BytesIO()
    with Writer(buf, point_format=point_format, scales=(0.01, 0.01, 0.01),
                chunk_size=chunk_size) as writer:
        for i in range(points):
            writer.write(Point(
                X=rng.randrange(0, 200_000), Y=rng.randrange(0, 200_000),
                Z=rng.randrange(0, 5_000), intensity=rng.randrange(0, 4096),
                gps_time=1_000_000 + i * 0.0004,
                classification=rng.choice((1, 2, 2, 2, 3, 5, 6)),
                return_number=1 + i % 3, number_of_returns=3,
                point_source_ID=rng.randrange(1, 20)))
    return buf.getvalue()


def best_of(repeat, fn):
    """The fastest run, and whatever it returned."""
    best, value = None, None
    for _ in range(repeat):
        start = time.perf_counter()
        value = fn()
        elapsed = time.perf_counter() - start
        if best is None or elapsed < best:
            best = elapsed
    return best, value


def sequential(data):
    with Reader(io.BytesIO(data)) as reader:
        n = 0
        for _ in reader:
            n += 1
        return n


def checksum(data):
    with Reader(io.BytesIO(data)) as reader:
        return reader.checksum()[1]


def arrays(data):
    with Reader(io.BytesIO(data)) as reader:
        try:
            return len(reader.arrays("X", "Y", "Z")["X"])
        except ImportError:
            return None


def seeks(data, count=2000):
    """Random access, which is where per-chunk costs land.

    Every jump lands in a chunk the reader has to set up from nothing, so
    this measures chunk-init and decode-to-position rather than throughput.
    """
    rng = random.Random(11)
    with Reader(io.BytesIO(data)) as reader:
        num_points = reader.num_points
        for _ in range(count):
            reader.seek(rng.randrange(num_points))
            reader.read()
        return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--points", type=int, default=2_000_000)
    parser.add_argument("--chunk-size", type=int, default=50_000)
    parser.add_argument("--format", type=int, default=1,
                        help="point data format (default 1)")
    parser.add_argument("--file", help="time this file instead of a new one")
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()

    if args.file:
        with open(args.file, "rb") as fh:
            data = fh.read()
        with Reader(io.BytesIO(data)) as reader:
            points, chunk_size = reader.num_points, reader.chunk_size
        print(f"{os.path.basename(args.file)}: {points:,} points, "
              f"chunks of {chunk_size}, {len(data) / 1e6:.1f} MB")
    else:
        started = time.perf_counter()
        data = generate(args.points, args.chunk_size, args.format)
        points = args.points
        print(f"generated {points:,} points of format {args.format}, "
              f"chunks of {args.chunk_size}, {len(data) / 1e6:.1f} MB "
              f"in {time.perf_counter() - started:.1f}s")

    print(f"\n{'':12s} {'seconds':>9s} {'per point':>12s}")
    for label, fn, divisor in (
            ("sequential", lambda: sequential(data), points),
            ("checksum", lambda: checksum(data), points),
            ("arrays", lambda: arrays(data), points),
            ("seek", lambda: seeks(data), 2000)):
        elapsed, value = best_of(args.repeat, fn)
        if value is None:
            print(f"{label:12s} {'-':>9s}   (numpy not installed)")
            continue
        unit = "us/seek" if label == "seek" else "ns/point"
        per = elapsed / divisor * (1e6 if label == "seek" else 1e9)
        print(f"{label:12s} {elapsed:9.3f} {per:8.1f} {unit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
