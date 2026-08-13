#!/usr/bin/env python3
"""Re-derive the committed laszip references with a freshly built laszip.

The test suite checks lazpy against `testdata/reference_hashes.txt` and
`testdata/reference_inside.txt`. Nothing checks those two files themselves --
they hold what laszip said at the time the fixtures were generated, whichever
version that happened to be, and a suite that agrees with a stale reference is
agreeing with nothing. This re-runs `lazdump` over every fixture and every
reference rectangle and names any line that no longer reproduces.

    python tools/verify_sweep.py path/to/lazdump [testdata_dir]

Needs a `lazdump` built against liblaszip; tools/README.md says how. It checks
every reference, prints each disagreement, and exits non-zero if there were
any.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TESTDATA = os.path.join(os.path.dirname(HERE), "testdata")


def lazdump(binary, *args):
    """Run lazdump and return its output words."""
    result = subprocess.run([binary, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"{binary} {' '.join(args)} failed:\n"
                         f"{result.stderr.strip()}")
    return result.stdout.split()


def check_hashes(binary, testdata):
    """Every whole-file checksum in reference_hashes.txt, recomputed."""
    failures = []
    with open(os.path.join(testdata, "reference_hashes.txt")) as fh:
        lines = fh.read().splitlines()
    for line in lines:
        name, digest, count = line.split()
        got_digest, got_count = lazdump(
            binary, os.path.join(testdata, name), "--hash")
        if (got_digest, got_count) != (digest, count):
            failures.append(f"{name}: reference says {count} points / "
                            f"{digest}, laszip now says {got_count} points "
                            f"/ {got_digest}")
    return len(lines), failures


def check_queries(binary, testdata):
    """Every rectangle query in reference_inside.txt, re-run.

    The reference line is `file minx miny maxx maxy count hash`, but
    lazdump prints the hash before the count, so this unpacks both values
    by name rather than comparing the two orderings as lists.
    """
    failures = []
    with open(os.path.join(testdata, "reference_inside.txt")) as fh:
        lines = fh.read().splitlines()
    for line in lines:
        name, *rect, count, digest = line.split()
        got_digest, got_count = lazdump(
            binary, os.path.join(testdata, name), "--inside", *rect)
        if (got_digest, got_count) != (digest, count):
            failures.append(
                f"{name} [{' '.join(rect)}]: reference says {count} points "
                f"/ {digest}, laszip now says {got_count} points / "
                f"{got_digest}")
    return len(lines), failures


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    binary = sys.argv[1]
    testdata = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TESTDATA

    files, hash_failures = check_hashes(binary, testdata)
    queries, query_failures = check_queries(binary, testdata)

    failures = hash_failures + query_failures
    for failure in failures:
        print(f"FAIL  {failure}")
    if failures:
        print(f"\n{len(failures)} of {files + queries} references no longer "
              f"reproduce")
        return 1

    print(f"OK    {files} whole-file checksums and {queries} rectangle "
          f"queries reproduce exactly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
