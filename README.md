# lazpy

LAS and LAZ point clouds, read and written: a port of
[LASzip](https://github.com/LASzip/LASzip) to C, with a Python front end.

Every LAZ point format and every LASzip item version is supported in both
directions.

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
0–5, v3 for 6–10. `laz_version=` overrides it. `chunk_size=` sets how many
points share a chunk, which is what random access on the way back in costs.

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

The extended variable-length records LAS 1.4 keeps behind the point data are
read as well, keyed by `(user_id, record_id)` — the id alone is not a key,
since LAS namespaces records by user id:

```python
wkt = reader.header["extended_variable_length_records"][(b"LASF_Projection", 2112)]
wkt["data"]     # read from the file here, not when it was opened
```

Not yet: *writing* extended variable-length records, spatial indexing, and LAS
1.4 compatibility mode.

Anything that goes wrong reading or writing a file raises `lazpy.LazError`,
except an error from the underlying file object, which propagates as itself.

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

## License

Apache License 2.0 — see `LICENSE`. Everything in `src/` is a port of
[LASzip](https://github.com/LASzip/LASzip), itself Apache 2.0 and copyright
rapidlasso GmbH; `NOTICE` carries the attribution.

## References

- [LAS 1.2 specification](https://www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf)
- [LAS 1.4 specification](https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf)
- [LASzip](https://github.com/LASzip/LASzip) — the reference implementation this
  is ported from, by Martin Isenburg / rapidlasso.
