# lazpy

LAS and LAZ point clouds, read and written: a port of
[LASzip](https://github.com/LASzip/LASzip) to C, with a Python front end.

lazpy reads and writes every LAZ point format and every LASzip item version.

## Installing

```bash
pip install lazpy
```

Installing also gives you a `lazpy` command, which summarises a file and
prints a few of its points:

```bash
lazpy cloud.laz          # or: python -m lazpy cloud.laz
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

`xyz()` and `arrays()` both take `start=` and `count=`:

```python
while reader.index < reader.num_points:
    block = reader.arrays("X", "Y", "Z", count=10_000_000)
    ...
```

numpy is an optional dependency — `pip install lazpy[numpy]` — and lazpy
imports it only when you call an array method.

## Reading a region

```python
with Reader("cloud.laz") as reader:
    for point in reader.points_within(min_x, min_y, max_x, max_y):
        ...
```

The rectangle is half-open — a point counts when `min_x <= x < max_x` and
`min_y <= y < max_y` — in the georeferenced coordinates `scale()` returns, so
adjoining rectangles partition the points rather than sharing the ones on the
seam. laszip's own rectangle query behaves the same way.

If the file has a LASzip spatial index, the query decodes only the chunks the
index says could hold a point in the rectangle. A 40-metre square of a
million-point file decodes about 49,000 points instead of all of them, and
takes a twentieth of the time. Without an index the query is a filtered full
scan: the same points, at the cost of reading everything.

lazpy looks for an index in two places: a `.lax` file beside the cloud, and
inside the file itself, where `lasindex -append` puts one. The embedded index
wins, since it cannot be stale. `reader.has_spatial_index` reports whether
either was found.

```python
reader.spatial_index.bounds        # the indexed area
reader.spatial_index.intervals(min_x, min_y, max_x, max_y)
```

A region can also be a circle — the natural shape when selecting around a
point. With an index, a circle reaches fewer cells than the square around it,
since the circle never touches the cells in the square's corners.

```python
for point in reader.points_within_circle(x, y, radius=30.0):
    ...
```

Every query also accepts the region as a keyword, `rect=` or `circle=`:

```python
reader.points_within(rect=(x0, y0, x1, y1))
reader.points_within(circle=(x, y, 30.0))
```

The array methods take the same `rect=` and `circle=` regions, and select
exactly the points `points_within` selects:

```python
a = reader.arrays_within("X", "Y", "classification", rect=(x0, y0, x1, y1))
xyz = reader.xyz_within(rect=(x0, y0, x1, y1))     # (N, 3) scaled floats
xyz = reader.xyz_within(circle=(x, y, 30.0))       # or around a point
```

`spatial_index.intervals()` exposes the raw index: the runs of point indices
a rectangle reaches. `points_within` seeks between those runs.

## Building an index

To build an index for a file that has none:

```python
with Reader("cloud.laz") as reader:
    reader.write_spatial_index(cell_size=10.0)      # writes cloud.lax
```

`cell_size` sets the width of the quadtree's leaves, in the units the
coordinates are in. `minimum_points` and `maximum_intervals` control the
coarsening: cells holding fewer than `minimum_points` between them merge into
their parent, and runs of point indices merge until at most
`maximum_intervals` are left; a negative value means that many per cell.
These are the same three parameters `lasindex` takes.

`build_spatial_index()` returns the same bytes without writing them, and
`append_spatial_index(path, data)` embeds them inside the file itself, where
`lasindex -append` puts one:

```python
from lazpy import Reader, append_spatial_index

with Reader("cloud.laz") as reader:
    index = reader.build_spatial_index(cell_size=10.0)
append_spatial_index("cloud.laz", index)
```

Building reads every point twice, both passes in C: lazpy cannot place a
point in a cell until it knows the extent of them all.

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

You can also build points from scratch:

```python
from lazpy import Point, Writer

with Writer("out.laz", point_format=1, scales=(0.01, 0.01, 0.01)) as writer:
    writer.write(Point(X=125_000, Y=473_000, Z=1_200,
                       gps_time=356_000.5, classification=2,
                       return_number=1, number_of_returns=1))
```

The writer handles the header, the LASzip VLR and the chunk table itself. It
fills in the point count, the counts by return number and the bounding box
when the file is closed, which is why the output has to be seekable. You can
set anything else through `writer.header` until then.

`write_arrays` is the array counterpart of `write`: it takes the
`{name: array}` dict that `arrays` returns, and writes zero for any field it
is not given. Together the two array methods convert a file in blocks:

```python
with Reader("in.laz") as reader, Writer("out.laz", reader.point_format,
                                        scales=reader.scales,
                                        offsets=reader.offsets) as writer:
    while reader.index < reader.num_points:
        writer.write_arrays(reader.arrays(count=1_000_000))
```

Everything else a file says about itself lives in variable length records —
most importantly, its coordinate reference system — and the writer takes
them through `vlrs=`. They are the same records `Reader` hands back, so copying a
file means passing them along:

```python
with Reader("cloud.laz") as reader:
    header = reader.header
    with Writer("copy.laz", point_format=reader.point_format,
                scales=reader.scales, offsets=reader.offsets,
                vlrs=header["variable_length_records"]) as writer:
        for point in reader:
            writer.write(point)
```

The writer drops the source file's own LASzip record and builds a fresh one
for the file being written; the dropped record described how the *source*
was compressed. Ordinary records have to be given up front, because
the header records how far past itself the points begin.

LAS 1.4's extended records go in `evlrs=`, or into `writer.evlrs` at any time
before the file is closed. They sit behind the point data, so unlike ordinary
records you need not have them ready before writing the points — convenient
for a record computed *from* the points:

```python
with Writer("out.laz", point_format=6) as writer:
    for point in points:
        writer.write(point)
    writer.evlrs.append(
        {"user_id": b"LASF_Projection", "record_id": 2112, "data": wkt})
```

Extended records are also the only records that can carry a payload over
64 KB, and they need LAS 1.4 — the only header with fields to point at them.

lazpy will build one record for you: the "extra bytes" descriptor, which says
what the opaque bytes on the end of each point mean. A writer given the
descriptor takes `num_extra_bytes` from it:

```python
from lazpy import ExtraBytesAttribute, extra_bytes_record

amplitude = ExtraBytesAttribute(b"amplitude", 3, scale=0.01)   # u16
with Writer("out.laz", point_format=1,
            vlrs=[extra_bytes_record([amplitude])]) as writer:
    ...
```

The writer stores points exactly as given, so georeferenced coordinates have
to be turned into the integers a point holds. `writer.unscale()` does that
conversion — the inverse of `Reader.scale`, rounding as laszip rounds.
`auto_offsets()` picks offsets the survey fits inside; projected coordinates
need them, because with the default offsets of zero their integers overflow.

```python
from lazpy import auto_offsets

offsets = auto_offsets(mins, maxs, scales)        # mins, maxs of the survey
with Writer("out.laz", point_format=1, offsets=offsets,
            scales=(0.001, 0.001, 0.001)) as writer:
    X, Y, Z = writer.unscale(515000.0, 6748000.0, 33.5)
    writer.write(Point(X=X, Y=Y, Z=Z, classification=2))
```

The writer raises on a coordinate its scales and offsets cannot represent,
rather than wrapping it into a point somewhere else entirely.

The compressor follows the file name — `.las` writes plain LAS — and the item
version follows the point format, matching laszip's own default: v2 for
formats 0–5, v3 for 6–10. `laz_version=` overrides that. `chunk_size=` sets
how many points share a chunk. `version_minor=` picks the LAS version: 1.0 to
1.4, defaulting to 1.2 unless the point format needs newer.

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
    crs = reader.crs                          # the CRS the file declares

rows = ((y.max() - y) / RES).astype(np.intp)  # north-up: row 0 is the top
cols = ((x - x.min()) / RES).astype(np.intp)

dsm = np.full((rows.max() + 1, cols.max() + 1), -np.inf, dtype="float32")
np.maximum.at(dsm, (rows, cols), z)           # highest return wins the cell
dsm[np.isneginf(dsm)] = np.nan                # cells no point landed in

with rasterio.open("dsm.tif", "w", driver="GTiff",
                   width=dsm.shape[1], height=dsm.shape[0], count=1,
                   dtype="float32", nodata=np.nan, crs=crs,
                   transform=from_origin(x.min(), y.max(), RES, RES)) as dst:
    dst.write(dsm, 1)
```

## Coordinate reference systems

`reader.crs` is the coordinate reference system the file declares, as a
[pyproj](https://pyproj4.github.io/pyproj/) CRS — or `None` if the file
declares nothing usable. `Writer` takes the same object back:

```python
with Writer("out.laz", point_format=1, crs=reader.crs) as writer:
    ...
```

lazpy reads the CRS from the GeoTIFF geokeys, or from the OGC WKT record LAS
1.4 uses in their place, and writes it the same way: WKT for point formats
6–10, geokeys otherwise. `pip install lazpy[crs]` adds pyproj; a program that
never touches a CRS does not need it.

lazpy reports the CRS exactly as the file declares it; it does not validate
the declaration or infer a correction. Files that declare an EPSG code in
metres while storing coordinates in feet do exist — the `ProjLinearUnits`
geokey is the usual clue — and for such a file `reader.crs` is the code it
names. `lazpy cloud.laz` prints the declared code, and passing the CRS you
actually want to whatever consumes the coordinates is a one-line change.

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

The reader exposes which kind of chunking a file uses — worth checking,
since `seek()` is only cheap when the file is chunked:

```python
reader.chunking      # Chunking.NONE, .FIXED or .ADAPTIVE
reader.chunk_size    # points per chunk; None unless chunking is FIXED
```

lazpy also reads the extended variable-length records LAS 1.4 keeps behind
the point data, keyed by `(user_id, record_id)` — the record id alone is not
a key, since LAS namespaces records by user id:

```python
wkt = reader.header["extended_variable_length_records"][(b"LASF_Projection", 2112)]
wkt["data"]     # read from the file here, not when it was opened
```

Ordinary variable-length records are keyed the same way, in
`header["variable_length_records"]`.

lazpy reads the LASzip spatial index, both as a sidecar `.lax` and as the
extended record an appended one lives in; see "Reading a region" above.

Both host byte orders work. LAS and LAZ are little-endian on disk and a
decoded point is in host order, so a big-endian host converts between the two
in the raw item coders (`src/laz_readitem_raw.c` and its writing counterpart);
the rule the rest of the code follows is written out above `laz_le_get16` in
`src/laz_types.h`. A file decodes to the same values, and compresses to the
same bytes, on either kind of machine. CI runs the suite on s390x under
emulation, since nothing else in the matrix is big-endian.

## Compatibility mode

lazpy reads and writes LAS 1.4 compatibility mode. A file that says it is
LAS 1.2 or 1.3 but carries a `lascompatible` record is a LAS 1.4 file in
disguise: its points are stored in a legacy format with the 1.4-only fields
packed into extra bytes. lazpy puts them back together, so such a file reads
as the 1.4 file it stands in for — `version_minor` 4, `point_format` 6–10,
the extended fields populated, and the extra bytes that carried them gone
from the point. laszip does the same when asked with
`laszip_request_compatibility_mode()`; lazpy always does it, except for the
LAS 1.5 form of the mode, which it has no header for. Note that
`header_size` and `offset_to_point_data` then describe the LAS 1.4 file being
reported rather than the bytes on disk, as they do in laszip.

Pass `compatibility=True` to write one — a file for readers that predate LAS
1.4, the readers the mode is named for:

```python
with Writer("legacy.laz", point_format=8, compatibility=True) as writer:
    writer.write(point)          # a LAS 1.4 point, written as a 1.2 one
```

The writer sends the points out as format 1, 3, 4 or 5, folding what those
cannot hold into extra bytes: the legacy scan angle field keeps a coarse
value with the remainder alongside, and the return numbers and the
classification keep as much as their narrower fields allow. Reading the file
back — with lazpy, or with laszip asked for the same mode — gives the LAS 1.4
points that went in. Any other reader sees a legacy file whose points are as
nearly right as a legacy file can make them.

## Errors and warnings

Anything that goes wrong reading or writing a file raises `lazpy.LazError`,
except an error from the underlying file object, which propagates as itself.
That holds for a malformed file too: a corrupt header or chunk is an
exception, not a crash, a hang, or a decode of bytes that were never there.
`tools/fuzz.py` checks this, and `testdata/malformed/` holds the files it has
found so far.

`reader.warnings` lists what was wrong with a file but not wrong enough to
refuse it — a missing chunk table, or fewer extended records than the header
claims — and `reader.warning` is the first of them.

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

The out-of-memory paths are covered too, and testing them needs help from
the C side: lazpy allocates model memory lazily, so no input file can reach
those failure branches on its own. `_cpylaz._alloc_fail_after(n)` makes the
model allocator start returning NULL after `n` allocations, and the tests
sweep it across every allocation a whole read or write makes. It is a test hook rather than
API, and it is not thread-safe.

### Building with warnings

An ordinary install compiles with whatever flags Python was built with, so
that a warning new to some future compiler cannot stop anyone installing
lazpy. CI holds the extension to a stricter standard; to reproduce that
build:

```bash
CFLAGS="-Wall -Wextra -Werror -Wno-missing-field-initializers" pip install -e .
```

`-Wno-missing-field-initializers` is there because only one thing trips it:
the `{NULL}` sentinel ending each CPython method and getset table.

## License

Apache License 2.0 — see `LICENSE`. Everything in `src/` is a port of
[LASzip](https://github.com/LASzip/LASzip), itself Apache 2.0 and copyright
rapidlasso GmbH; `NOTICE` carries the attribution.

## References

- [LAS 1.2 specification](https://www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf)
- [LAS 1.4 specification](https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf)
- [LASzip](https://github.com/LASzip/LASzip) — the reference implementation lazpy
  is ported from, by Martin Isenburg / rapidlasso.
