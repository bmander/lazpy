# lazpy

LAS and LAZ point clouds, read and written: a port of
[LASzip](https://github.com/LASzip/LASzip) to C, with a Python front end.

Every LAZ point format and every LASzip item version is supported for both read and write.

## Installing

```bash
pip install lazpy
```

## Reading

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

## Reading as arrays

```python
with Reader("cloud.laz") as reader:
    xyz = reader.xyz()                  # (N, 3) float64, scaled and offset

    a = reader.arrays(start=0)          # {name: array}, every field there is
    ground = a["Z"][a["classification"] == 2]

    a = reader.arrays("X", "Y", "gps_time", start=0)     # or just these
```

Both take `start=` and `count=`:

```python
while reader.index < reader.num_points:
    block = reader.arrays("X", "Y", "Z", count=10_000_000)
    ...
```

numpy is an optional dependency — `pip install lazpy[numpy]` — and is imported
only when one of these is called.

## Reading a region

```python
with Reader("cloud.laz") as reader:
    for point in reader.points_within(min_x, min_y, max_x, max_y):
        ...
```

The rectangle is half-open — a point counts when `min_x <= x < max_x` and
`min_y <= y < max_y` — in the georeferenced coordinates `scale()` returns, so
adjoining rectangles partition the points rather than sharing the ones on the
seam. This is what laszip's own rectangle query does.

If the file has a LASzip spatial index, this decodes only the chunks the index
says could hold a point in the rectangle. A 40-metre square of a
million-point file decodes about 49,000 points instead of all of them, and
takes a twentieth of the time. Without an index it is a filtered full scan:
the same points, at the cost of reading everything.

An index is looked for in the `.lax` file beside the cloud, and inside the file
itself for one that `lasindex -append` put there — that one wins, since it
cannot be stale. `reader.has_spatial_index` says which happened.

```python
reader.spatial_index.bounds        # the indexed area
reader.spatial_index.intervals(min_x, min_y, max_x, max_y)
```

The array API has the same form, which is how to read a region and read it
fast at once:

```python
a = reader.arrays_within("X", "Y", "classification", rect=(x0, y0, x1, y1))
xyz = reader.xyz_within(rect=(x0, y0, x1, y1))     # (N, 3) scaled floats
```

They select exactly what `points_within` selects. Arrays are sized for every
point the index turns up as a candidate and cut down to the ones really
inside, since how many that is is what the query is for.

`intervals()` is the index itself: the runs of point indices a rectangle
reaches, which is what `points_within` seeks between. Writing a `.lax` is not
supported; `lasindex` from LAStools writes them.

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

The header, the LASzip VLR and the chunk table are handled by the writer. The
point count, the counts by return number and the bounding box are filled in
when the file is closed, which is why the output has to be seekable; anything
else can be set through `writer.header` until then.

Everything a file says about itself beyond that fixed header is a variable
length record — where on the earth its coordinates are, above all — and
`vlrs=` is where they go. They are the same records `Reader` hands back, so
copying a file is passing them along:

```python
with Reader("cloud.laz") as reader:
    header = reader.header
    with Writer("copy.laz", point_format=reader.point_format,
                scales=reader.scales, offsets=reader.offsets,
                vlrs=header["variable_length_records"]) as writer:
        for point in reader:
            writer.write(point)
```

The source file's own LASzip record is dropped on the way, since it describes
how *that* file was compressed; the writer builds the one that describes this
one. Records have to be given up front, because the header records how far
past itself the points begin.

LAS 1.4's extended records go in `evlrs=`, or into `writer.evlrs` at any point
before the file is closed — they sit behind the point data, so unlike the
ordinary records they need not be known before the points are written, which
is what writing something computed *from* the points needs:

```python
with Writer("out.laz", point_format=6) as writer:
    for point in points:
        writer.write(point)
    writer.evlrs.append(
        {"user_id": b"LASF_Projection", "record_id": 2112, "data": wkt})
```

They are what carries a payload no ordinary record can — over 64 KB — and
they need LAS 1.4, whose header is the only one with fields to point at them.

The one record lazpy will build for you is the "extra bytes" descriptor, which
says what the opaque bytes on the end of each point mean. A writer given one
takes `num_extra_bytes` from it:

```python
from lazpy import ExtraBytesAttribute, extra_bytes_record

amplitude = ExtraBytesAttribute(b"amplitude", 3, scale=0.01)   # u16
with Writer("out.laz", point_format=1,
            vlrs=[extra_bytes_record([amplitude])]) as writer:
    ...
```

Points are written as they are given, so georeferenced coordinates have to be
turned into the integers a point stores. `unscale()` is that — the inverse of
`Reader.scale`, rounding as laszip rounds — and `auto_offsets()` picks offsets
a survey fits inside, which for projected coordinates it will not by default:

```python
from lazpy import auto_offsets

offsets = auto_offsets(mins, maxs, scales)        # mins, maxs of the survey
with Writer("out.laz", point_format=1, offsets=offsets,
            scales=(0.001, 0.001, 0.001)) as writer:
    X, Y, Z = writer.unscale(515000.0, 6748000.0, 33.5)
    writer.write(Point(X=X, Y=Y, Z=Z, classification=2))
```

A coordinate these scales and offsets cannot reach raises rather than wrapping
into a point somewhere else entirely.

The compressor follows the file name — `.las` writes plain LAS — and the item
version follows the point format, as laszip's own default does: v2 for formats
0–5, v3 for 6–10. `laz_version=` overrides it. `chunk_size=` sets how many
points share a chunk. `version_minor=` picks the LAS version: 1.0 to 1.4, or
1.2 by default unless the point format needs newer.

## Example: LAZ to GeoTIFF

Rasterize a point cloud to a 1-metre digital surface model,
taking the highest return in each cell, and write it with
[rasterio](https://rasterio.readthedocs.io/).

```python
import numpy as np
import rasterio
from rasterio.transform import from_origin
from lazpy import Reader

RES = 1.0                                    # metres per pixel

with Reader("cloud.laz") as reader:
    x, y, z = reader.xyz().T

rows = ((y.max() - y) / RES).astype(np.intp)  # north-up: row 0 is the top
cols = ((x - x.min()) / RES).astype(np.intp)

dsm = np.full((rows.max() + 1, cols.max() + 1), -np.inf, dtype="float32")
np.maximum.at(dsm, (rows, cols), z)           # highest return wins the cell
dsm[np.isneginf(dsm)] = np.nan                # cells no point landed in

with rasterio.open("dsm.tif", "w", driver="GTiff",
                   width=dsm.shape[1], height=dsm.shape[0], count=1,
                   dtype="float32", nodata=np.nan, crs="EPSG:32610",
                   transform=from_origin(x.min(), y.max(), RES, RES)) as dst:
    dst.write(dsm, 1)
```

## What is supported

| Point data format | Items | LAZ versions |
|---|---|---|
| 0–5 | POINT10, GPSTIME11, RGB12, WAVEPACKET13, BYTE | uncompressed, v1, v2 |
| 6–10 | POINT14, RGB14, RGBNIR14, WAVEPACKET14, BYTE14 | uncompressed, v3, v4 |

Also handled: LAS 1.0–1.4 headers, variable-length records, extra bytes,
pointwise-chunked and layered-chunked containers, fixed and adaptive chunk
tables, files whose chunk table is missing because the compressor was
interrupted, and selective decompression of LAS 1.4 attribute layers
(`decompress_selective=` with a `Selective` mask).

Which of those a file uses is on the reader — and matters, since chunking is
what makes `seek()` cheap:

```python
reader.chunking      # Chunking.NONE, .FIXED or .ADAPTIVE
reader.chunk_size    # points per chunk; None unless chunking is FIXED
```

The extended variable-length records LAS 1.4 keeps behind the point data are
read as well, keyed by `(user_id, record_id)` — the id alone is not a key,
since LAS namespaces records by user id:

```python
wkt = reader.header["extended_variable_length_records"][(b"LASF_Projection", 2112)]
wkt["data"]     # read from the file here, not when it was opened
```

Ordinary variable-length records are keyed the same way, in
`header["variable_length_records"]`.

LAS 1.4 compatibility mode is read and written. A file that says it is LAS 1.2 or 1.3 but
carries a `lascompatible` record is a LAS 1.4 file in disguise — its points are
written in a legacy format with the 1.4-only fields packed into extra bytes.
lazpy puts them back together, so such a file reads as the 1.4 file it stands
in for: `version_minor` 4, `point_format` 6–10, the extended fields populated,
and the extra bytes that carried them no longer among a point's own. This is
what laszip's `laszip_request_compatibility_mode()` does, and lazpy always does
it. The one variant left alone is the LAS 1.5 form, which lazpy has no header
for. Note that `header_size` and `offset_to_point_data` then describe the LAS
1.4 file being reported rather than the bytes on disk, as they do in laszip.

Writing one is `compatibility=True`, which is where the mode earns its name —
a file for readers that predate LAS 1.4:

```python
with Writer("legacy.laz", point_format=8, compatibility=True) as writer:
    writer.write(point)          # a LAS 1.4 point, written as a 1.2 one
```

The points go out as format 1, 3, 4 or 5, with what those cannot hold folded
into extra bytes: the scan angle keeps a rank and the remainder rides along,
the return numbers and the classification keep as much as their narrower
fields allow. Reading it back — with lazpy, or with laszip asked for the same
mode — gives the LAS 1.4 points that went in. Anything else sees a legacy
file whose points are as nearly right as a legacy file can make them.

The LASzip spatial index is read, both as a sidecar `.lax` and as the extended
record an appended one lives in; see "Reading a region" above.

Not yet: *writing* spatial indexes.

Both host byte orders work. LAS and LAZ are little-endian on disk and a
decoded point is in host order, so a big-endian host converts between the two
in the raw item coders (`src/laz_readitem_raw.c` and its writing counterpart);
the rule the rest of the code follows is written out above `laz_le_get16` in
`src/laz_types.h`. A file decodes to the same values, and compresses to the
same bytes, on either kind of machine. CI runs the suite on s390x under
emulation, since nothing else in the matrix is big-endian.

Anything that goes wrong reading or writing a file raises `lazpy.LazError`,
except an error from the underlying file object, which propagates as itself.
That holds for a malformed file too: a corrupt header or chunk is an
exception, not a crash, a hang, or a decode of bytes that were never there.
`tools/fuzz.py` is what checks it, and `testdata/malformed/` holds what it has
found so far.

## Development

```bash
pip install -e . pytest        # the tests import lazpy like any other consumer
pytest
flake8
```

The end-to-end tests compare a checksum of every decoded field of every point
in `testdata/` against values produced by laszip itself, in both directions:
files lazpy writes have to read back to the same checksum, and given laszip's
own points and chunk size, the point block lazpy produces is byte-identical to
laszip's. `tools/` holds the harness that regenerated that reference data;
running the tests does not require laszip.

The out-of-memory paths are covered too, which needs help from the C side:
model memory is allocated lazily, so nothing an input file can do reaches
those failure branches. `_cpylaz._alloc_fail_after(n)` makes the model
allocator start returning NULL after `n` allocations, and the tests sweep it
across every allocation a whole read or write makes. It is a test hook rather
than API, and it is not thread-safe.

### Building with warnings

An ordinary install compiles with whatever flags Python was built with, so
that a warning new to some future compiler cannot stop anyone installing
lazpy. CI holds the extension to a stricter standard, and this is that build:

```bash
CFLAGS="-Wall -Wextra -Werror -Wno-missing-field-initializers" pip install -e .
```

`-Wno-missing-field-initializers` because the only thing that trips it is the
`{NULL}` sentinel ending each CPython method and getset table.

## License

Apache License 2.0 — see `LICENSE`. Everything in `src/` is a port of
[LASzip](https://github.com/LASzip/LASzip), itself Apache 2.0 and copyright
rapidlasso GmbH; `NOTICE` carries the attribution.

## References

- [LAS 1.2 specification](https://www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf)
- [LAS 1.4 specification](https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf)
- [LASzip](https://github.com/LASzip/LASzip) — the reference implementation this
  is ported from, by Martin Isenburg / rapidlasso.
