# lazpy

LAS and LAZ point clouds, read and written: a port of
[LASzip](https://github.com/LASzip/LASzip) to C, with a Python front end.

Every LAZ point format and every LASzip item version is supported in both
directions, and both are verified against laszip itself, field for field.

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

## Writing

```python
from lazpy import Reader, Writer

# thin a survey down to its ground returns, still compressed
with Reader("cloud.laz") as reader, \
     Writer("ground.laz", point_format=reader.point_format,
            scales=reader.scales, offsets=reader.offsets) as writer:
    for point in reader:
        if point.classification == 2:
            writer.write(point)
```

Points can also be built from nothing:

```python
from lazpy import Point, Writer

with Writer("out.laz", point_format=1, scales=(0.01, 0.01, 0.01)) as writer:
    writer.write(Point(X=125_000, Y=473_000, Z=1_200,
                       gps_time=356_000.5, classification=2,
                       return_number=1, number_of_returns=1))
```

The header, the LASzip VLR and the chunk table are the writer's business. The
point count, the counts by return number and the bounding box are filled in
when the file is closed, which is why the output has to be seekable; anything
else can be set through `writer.header` until then.

The compressor follows the file name — `.las` writes plain LAS — and the item
version follows the point format, as laszip's own default does: v2 for formats
0–5, v3 for 6–10. `laz_version=` overrides it.

`chunk_size=` sets how many points share a chunk, which is what random access
on the way back in costs. `chunk_size=0xFFFFFFFF` leaves the boundaries to you
instead:

```python
with Writer("out.laz", point_format=1, chunk_size=0xFFFFFFFF) as writer:
    for flight_line in flight_lines:
        for point in flight_line:
            writer.write(point)
        writer.chunk()          # end this chunk here
```

`compressor=` picks the container. It defaults to the chunked one, and
`Compressor.POINTWISE` selects LASzip's original instead — the whole file as
one stream, no chunk table, and so no random access when it is read back.
Point formats 6–10 have only the layered container and take no choice.

## Status

Complete in both directions.

| Point data format | Items | LAZ versions |
|---|---|---|
| 0–5 | POINT10, GPSTIME11, RGB12, WAVEPACKET13, BYTE | uncompressed, v1, v2 |
| 6–10 | POINT14, RGB14, RGBNIR14, WAVEPACKET14, BYTE14 | uncompressed, v3, v4 |

Also handled: LAS 1.0–1.4 headers, variable-length records, extra bytes,
pointwise-chunked and layered-chunked containers, fixed and adaptive chunk
tables, files whose chunk table is missing because the compressor was
interrupted, and selective decompression of LAS 1.4 attribute layers.

`Writer` needs a seekable output, because the point count and the bounding box
are only known once the last point is written and they belong at the front of
the file. The point block below it does not: a chunk table written to a stream
that cannot seek puts its own offset at the end of the file instead, and lazpy
reads that variant back. Reaching it means driving `lazpy.PointWriter` and
writing a header yourself.

Not yet: extended variable-length records, spatial indexing, and LAS 1.4
compatibility mode.

Little-endian hosts only; the build fails there rather than mis-code a point.

Anything that goes wrong reading or writing a file raises `lazpy.LazError`,
except an error from the underlying file object, which propagates as itself.

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

The write side is pinned harder still. Each `ptN_v0.las` holds the same points
as the `.laz` files beside it, so compressing those points has to reproduce
those files byte for byte — which it does, for every point format, chunk table
included, which is a far stronger claim than a round trip.

The end-to-end tests read `testdata/`, which holds a small file for every point
data format crossed with every item version that applies to it, and compare a
checksum of every decoded field of every point against
`testdata/reference_hashes.txt` — values produced by laszip itself. A single
wrong bit anywhere changes the hash. The same checksums are the oracle for
writing: a file lazpy writes and reads back has to reproduce the checksum of
the file its points came from, for all 33 format × version combinations.

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

And by handing laszip what lazpy wrote:

- **33 format × version combinations** written by lazpy — every point format
  uncompressed, and at each item version it has — open in laszip with no error
  and no warning, and decode to the checksum of the file their points came
  from.
- **Byte-identical output**: given laszip's own points, chunk size and item
  version, the point block lazpy produces is the one laszip produced, chunk
  table and all.
- **Non-seekable output** — where the chunk table's offset is appended rather
  than patched in — reads back correctly in both laszip and lazpy.

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
