# lazpy

LAS and LAZ point clouds, read and written: a port of
[LASzip](https://github.com/LASzip/LASzip) to C, with a Python front end.

## Features

- Every LAZ point format (0–10) and every LASzip item version, reading and
  writing
- LAS 1.0–1.4, variable length records, extra bytes, LAS 1.4 compatibility
  mode
- numpy array reading and writing (`pip install lazpy[numpy]`)
- Rectangle and circle queries, accelerated by LASzip `.lax` spatial
  indexes, which lazpy also builds
- Coordinate reference systems as pyproj objects (`pip install lazpy[crs]`)
- Malformed input raises `lazpy.LazError`; `reader.warnings` collects
  non-fatal defects
- A `lazpy` CLI that summarises a file

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

As numpy arrays:

```python
with Reader("cloud.laz") as reader:
    xyz = reader.xyz()                        # (N, 3) float64, scaled
    a = reader.arrays(start=0)                # {name: array}, every field
    ground = a["Z"][a["classification"] == 2]
```

`xyz()` and `arrays()` take `start=` and `count=` for reading in blocks.

## Spatial queries

```python
with Reader("cloud.laz") as reader:
    for point in reader.points_within(rect=(x0, y0, x1, y1)):
        ...
    xyz = reader.xyz_within(circle=(x, y, 30.0))
```

With a LASzip spatial index — a `.lax` sidecar, or embedded by
`lasindex -append` — a query decodes only the chunks that can hold matching
points; without one it is a filtered full scan. To build an index:

```python
reader.write_spatial_index(cell_size=10.0)    # writes cloud.lax
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

`write_arrays()` takes the dict `arrays()` returns; together they convert a
file in blocks:

```python
with Reader("in.laz") as reader, Writer("out.laz", reader.point_format,
                                        scales=reader.scales,
                                        offsets=reader.offsets) as writer:
    while reader.index < reader.num_points:
        writer.write_arrays(reader.arrays(count=1_000_000))
```

The writer handles the header, the LASzip VLR and the chunk table. Keyword
arguments cover the rest: `vlrs=` and `evlrs=` for records, `crs=` for a
coordinate reference system, `chunk_size=`, `version_minor=`, `laz_version=`,
and `compatibility=True` for LAS 1.4 compatibility mode. A `.las` file name
writes plain LAS.

`writer.unscale()` converts georeferenced floats to the integers points
store, and `auto_offsets(mins, maxs, scales)` picks offsets a survey fits
inside.

## Coordinate reference systems

`reader.crs` is the CRS the file declares, as a
[pyproj](https://pyproj4.github.io/pyproj/) CRS — `None` if it declares
nothing usable — and `Writer` takes the same object back through `crs=`.
lazpy reports the declaration as it stands; it does not validate or correct
it.

## What is supported

| Point data format | Items | LAZ versions |
|---|---|---|
| 0–5 | POINT10, GPSTIME11, RGB12, WAVEPACKET13, BYTE | uncompressed, v1, v2 |
| 6–10 | POINT14, RGB14, RGBNIR14, WAVEPACKET14, BYTE14 | uncompressed, v3, v4 |

Also: pointwise- and layered-chunked containers, fixed, adaptive and missing
chunk tables, selective decompression, spatial indexes, LAS 1.4
compatibility mode in both directions, and both host byte orders.

## Development

```bash
pip install -e . pytest
pytest
flake8
```

The tests check lazpy's output byte-for-byte against reference data produced
by laszip itself; `tools/` holds the harness that generated it.

## License

Apache License 2.0 — see `LICENSE`. Everything in `src/` is a port of
[LASzip](https://github.com/LASzip/LASzip), itself Apache 2.0 and copyright
rapidlasso GmbH; `NOTICE` carries the attribution.

## References

- [LAS 1.2 specification](https://www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf)
- [LAS 1.4 specification](https://www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf)
- [LASzip](https://github.com/LASzip/LASzip) — the reference implementation
  lazpy is ported from, by Martin Isenburg / rapidlasso.
