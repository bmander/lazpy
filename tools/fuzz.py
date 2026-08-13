#!/usr/bin/env python3
"""Mutate the fixtures and read the results, looking for crashes, hangs,
and dangling exceptions.

    python tools/fuzz.py [--seed N] [--count N] [--corpus DIR] [--quiet]

A malformed file may raise; it may not crash the interpreter, hang, or leave a
Python exception dangling. This drives `Reader` over mutated copies of
`testdata/` and reports any case that does something else. Each case is fully
determined by its seed, and the seed is printed before the case runs, so a
crasher is reproducible from the last line of output:

    python tools/fuzz.py --seed 41253 --count 1

Findings go in `testdata/malformed/` as regression cases;
`tests/test_malformed.py` reads every file there and asserts only that
opening it does not take the process down with it -- and then you add a
named test beside it saying what that file should raise.

This is a smoke test, not a coverage-guided fuzzer. It finds only the shallow
bugs that random mutation reaches; libFuzzer or Atheris over the same entry
points would go deeper and would need a build of their own.
"""
import argparse
import io
import os
import random
import sys

from lazpy import Reader

TESTDATA = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "testdata")

# What a malformed file is allowed to do, taken from the test suite so that
# the fuzzer and the tests cannot come to disagree about it -- a fuzzer
# reporting findings the tests accept would be worse than either being wrong
# on its own.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tests"))
from helpers import SURVIVABLE as EXPECTED        # noqa: E402

# The header is 227 bytes at its shortest and the point data begins a few
# hundred bytes in, so these two spans are where the fields that drive
# allocation and control flow live -- the interesting bytes to corrupt.
HEADER_END = 375
VLR_START = 227


def mutate(data, rng):
    """One mutated copy of *data*, determined entirely by *rng*."""
    b = bytearray(data)
    how = rng.randrange(6)

    if how == 0:                                    # scattered byte flips
        for _ in range(rng.randrange(1, 16)):
            b[rng.randrange(len(b))] = rng.randrange(256)
    elif how == 1:                                  # truncation
        del b[rng.randrange(1, len(b)):]
    elif how == 2:                                  # a header field, smashed
        offset = rng.randrange(0, HEADER_END)
        width = rng.choice((1, 2, 4, 8))
        if offset + width <= len(b):
            b[offset:offset + width] = rng.choice(
                (b"\xff" * width, b"\x00" * width, rng.randbytes(width)))
    elif how == 3:                                  # the record area, smashed
        start = min(VLR_START, len(b) - 1)
        for _ in range(rng.randrange(1, 8)):
            b[rng.randrange(start, len(b))] = rng.randrange(256)
    elif how == 4:                                  # spliced with another file
        other = rng.choice(list(corpus().values()))
        cut = rng.randrange(len(b))
        b[cut:] = other[cut:cut + rng.randrange(1, 500)]
    else:                                           # junk on the end
        b.extend(rng.randbytes(rng.randrange(1, 200)))

    return bytes(b)


_corpus = {}


def corpus(directory=None):
    """The seed files, read once."""
    if not _corpus:
        directory = directory or TESTDATA
        for name in sorted(os.listdir(directory)):
            if name.endswith((".las", ".laz")):
                with open(os.path.join(directory, name), "rb") as fh:
                    _corpus[name] = fh.read()
    return _corpus


def exercise(data, max_points=2000):
    """Everything a caller might do with a file, done to a bad one.

    Not just reading: the array path, the rectangle query and the properties
    reach different code, and a malformed file has to be survivable through
    all of them.
    """
    with Reader(io.BytesIO(data)) as reader:
        count = min(reader.num_points, max_points)
        for _ in range(count):
            reader.read()

        reader.seek(0)
        reader.checksum(min(count, 500))

        reader.seek(0)
        try:
            reader.arrays(count=min(count, 100))
        except ImportError:                          # numpy not installed
            pass

        list(reader.points_within(1490, 1690, 1510, 1710))
        return reader.chunk_size, reader.warning, len(reader.header)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0,
                        help="first seed to run (default 0)")
    parser.add_argument("--count", type=int, default=500,
                        help="how many cases to run (default 500)")
    parser.add_argument("--corpus", default=TESTDATA,
                        help="directory of seed files (default testdata/)")
    parser.add_argument("--output", default=".",
                        help="where to write the files that crashed "
                             "(default: the current directory)")
    parser.add_argument("--quiet", action="store_true",
                        help="only report findings")
    args = parser.parse_args()

    seeds = corpus(args.corpus)
    if not seeds:
        raise SystemExit(f"no .las or .laz files in {args.corpus}")

    findings = 0
    for seed in range(args.seed, args.seed + args.count):
        rng = random.Random(seed)
        name = rng.choice(list(seeds))
        data = mutate(seeds[name], rng)
        # printed before the case runs, so that a crash -- which kills the
        # interpreter and prints nothing itself -- is still identified by
        # the last line of output
        if not args.quiet:
            print(f"{seed} {name}", flush=True)
        try:
            exercise(data)
        except EXPECTED:
            pass
        except Exception as exc:
            findings += 1
            path = os.path.join(args.output, f"crash-{seed}.laz")
            with open(path, "wb") as fh:
                fh.write(data)
            print(f"FINDING seed {seed} ({name}): "
                  f"{type(exc).__name__}: {exc}\n  written to {path}")

    print(f"{args.count} cases from seed {args.seed}: "
          f"{findings} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
