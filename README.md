# lazpy

A reader for LAS and LAZ point clouds: a port of [LASzip](https://github.com/LASzip/LASzip)'s
decompressor to C, with a Python front end.

Every LAZ point format and every LASzip item version is supported, and decoding
is verified against laszip itself, field for field.

```python
from lazpy import Reader

with Reader("cloud.laz") as reader:
    print(reader.num_points, reader.point_format)

    for point in reader:
        print(point.X, point.Y, point.Z, point.classification)

    reader.seek(1_000_000)          # random access
    point = reader.read()
    x, y, z = reader.scale(point)   # georeferenced floats
```

## Status

Reading is complete. Writing is under way: the entropy coder is bidirectional,
and every item writer is in place — v1 and v2 for point formats 0–5, the
layered v3 and v4 for 6–10 — each producing byte-identical output to laszip.
Still missing above them are the chunking container and a `Writer` front end,
so lazpy cannot yet write a file end to end.

| Point data format | Items | LAZ versions |
|---|---|---|
| 0–5 | POINT10, GPSTIME11, RGB12, WAVEPACKET13, BYTE | uncompressed, v1, v2 |
| 6–10 | POINT14, RGB14, RGBNIR14, WAVEPACKET14, BYTE14 | uncompressed, v3, v4 |

Also handled: LAS 1.0–1.4 headers, variable-length records, extra bytes,
pointwise-chunked and layered-chunked containers, fixed and adaptive chunk
tables, files whose chunk table is missing because the compressor was
interrupted, and selective decompression of LAS 1.4 attribute layers.

Little-endian hosts only; the build fails there rather than mis-decode.

Anything that goes wrong reading a file raises `lazpy.LazError`, except an error
from the underlying file object, which propagates as itself.

## Installing

```bash
pip install lazpy
```

Wheels are published for Linux, macOS and Windows on 64-bit CPython 3.10 and
up, so no compiler is needed. Anywhere else, `pip` builds from the source
distribution; the extension is plain C with no external dependencies.

## Building

```bash
pip install -e .          # builds the extension in place
```

The build is declared in `pyproject.toml` and carries no extra compiler flags —
every compiler setuptools drives already optimizes by default. To build with
warnings on:

```bash
CFLAGS="-Wall -Wextra" pip install -e . --no-build-isolation
```

## Tests

```bash
pip install -e . pytest
pytest
```

Note the editable install: the tests import `lazpy` like any other consumer, so
the extension has to be built before they can run.

The suite has two halves. The unit tests pin the entropy coder — arithmetic
models, decoder and integer decompressor — against known bit-exact vectors and
against the pure-Python reference implementations in `tests/models.py`,
`tests/encoder.py` and `tests/compressor.py`. The encoder has no committed
vectors of its own: it is tested as the decoder's inverse, by round trip, and
by requiring the C and pure-Python encoders to emit the same bytes.

The item writers are pinned harder still. Each `ptN_v0.las` holds the same
points as the `.laz` files beside it, so compressing those records has to
reproduce those files byte for byte — which it does, for every legacy point
format in both v1 and v2.

The end-to-end tests read `testdata/`, which holds a small file for every point
data format crossed with every item version that applies to it, and compare a
checksum of every decoded field of every point against
`testdata/reference_hashes.txt` — values produced by laszip itself. A single
wrong bit anywhere changes the hash.

## Verification

Correctness is established by decoding the same files with laszip and with
lazpy and comparing every field of every point:

- **33 format × version combinations** decode to identical whole-file
  checksums, repeated across chunk sizes of 1, 137, 3000, one less than the
  point count, exactly the point count, and larger than the point count.
- **43,271,750 points** of a real-world format-1 survey file produce a
  checksum identical to laszip's.
- **Random access** lands on the same point a sequential read would, forwards,
  backwards, repeated, and across chunk boundaries, for all 33 files.

`tools/` holds the harness:

- `lazdump.c` — links against liblaszip and dumps every decoded field, either
  as text (`lazdump in.laz out.txt`) or as a whole-file checksum
  (`lazdump in.laz --hash`).
- `compare_with_laszip.py` — reads the same file with lazpy and compares,
  reporting the first mismatching field.

Both are only needed to regenerate or extend the reference data; running the
test suite does not require laszip.

## Performance

Decoding the 43M-point file above:

| | time |
|---|---|
| laszip (its own C++ reader) | 51 s |
| lazpy | 38 s |

Per-point iteration through the Python API runs at roughly 1.3M points/sec; the
in-C `checksum()` path is faster still. `Reader.read()` returns the reader's own
`Point` object, refilled in place, so iterating a large file does not allocate
an object per point — call `point.copy()` to keep one. It also holds the GIL:
decoding a single point costs less than releasing and reacquiring it. The bulk
paths, `checksum()` and `seek()`, do release it.

For LAS 1.4 files, `decompress_selective` skips whole attribute layers:

```python
from lazpy import Reader, Selective

# decode only position; leave intensity, classification, GPS time et al. packed
mask = Selective.CHANNEL_RETURNS_XY | Selective.Z
with Reader("cloud.laz", decompress_selective=mask) as reader:
    ...
```

## Layout

| | |
|---|---|
| `lazpy/__init__.py` | header and VLR parsing, the `Reader` API |
| `lazpy/_utils.py` | bytestream parsing helpers |
| `src/cpylazmodule.c` | Python bindings, built as `lazpy._cpylaz` |
| `src/laz_stream.*` | buffered file and in-memory byte streams, in and out |
| `src/laz_arithmetic.*` | arithmetic models, decoder and encoder |
| `src/laz_intcompressor.*` | entropy-coded integer (de)compressor |
| `src/laz_item.h` | predictors and model banks shared by the readers and writers |
| `src/laz_readitem_raw.c` | uncompressed item readers |
| `src/laz_readitem_v1.c` | LASzip 1.0 item readers |
| `src/laz_readitem_v2.c` | LASzip 2.0 item readers |
| `src/laz_readitem_v3.c` | layered LAS 1.4 item readers, v3 and v4 |
| `src/laz_readpoint.c` | chunking, chunk tables, seeking |
| `src/laz_writeitem.c` | picks the writer for an item, given its type and version |
| `src/laz_writeitem_raw.c` | uncompressed item writers |
| `src/laz_writeitem_v1.c` | LASzip 1.0 item writers |
| `src/laz_writeitem_v2.c` | LASzip 2.0 item writers |
| `tests/test_lazpy.py` | the test suite |
| `tests/models.py`, `tests/encoder.py`, `tests/compressor.py` | pure-Python reference implementations, used as a test oracle — not installed |
| `pyproject.toml` | package metadata, the extension build, and the wheel matrix |

## License

Apache License 2.0 — see `LICENSE`.

Everything in `src/` is a port of [LASzip](https://github.com/LASzip/LASzip),
which is itself Apache 2.0 and copyright rapidlasso GmbH; matching its license
keeps one set of terms over the whole tree. `NOTICE` carries the attribution,
and every derived file says so in its header.

## References

- [LAS 1.2 specification](https://www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf)
- [LAS 1.4 specification](https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf)
- [LASzip](https://github.com/LASzip/LASzip) — the reference implementation this
  is ported from, by Martin Isenburg / rapidlasso.
