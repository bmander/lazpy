Examples
========

.. currentmodule:: lazpy

Longer worked examples than the quickstart carries. Each one is a whole
program: what it reads, what it writes, and the parts of the API it takes to
get from one to the other.

LAZ to GeoTIFF
--------------

Rasterize a point cloud to a 1-metre digital surface model, taking the
highest return in each cell, and write it with
`rasterio <https://rasterio.readthedocs.io/>`_.

This one needs more than lazpy on its own: :meth:`Reader.xyz` wants numpy
(``pip install lazpy[numpy]``), :attr:`Reader.crs` wants pyproj
(``pip install lazpy[crs]``), and rasterio brings GDAL for the write.

.. code-block:: python

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

A few things worth pulling out of it:

:meth:`Reader.xyz` returns the scaled coordinates as one ``(n, 3)`` array, so
``.T`` unpacks it into three columns without a Python-level loop over points.
It reads the whole file into memory at once, which is what makes the
vectorised binning below possible; :meth:`Reader.points` is the streaming
alternative for a cloud that will not fit.

``np.maximum.at`` is the part that makes this a *surface* model rather than a
last-one-wins scatter. Cells collect many returns, and unbuffered addition at
repeated indices would keep whichever point happened to land last; taking the
maximum keeps the highest, which is the top of the canopy or the roof.

The rows are computed from ``y.max()`` downward because raster row 0 is the
north edge while LAS y increases northward — getting that backwards flips the
image, and it flips silently, since nothing about the array shape changes.

Cells no point landed in stay ``-inf`` through the binning and become ``nan``
at the end, declared to rasterio as ``nodata``. A gap in a point cloud is
missing data rather than ground at elevation zero, and saying so keeps
whatever reads the raster next from averaging the holes into the terrain.

:attr:`Reader.crs` is passed straight through to rasterio, so the GeoTIFF
declares the projection the LAZ file declared. See
:doc:`crs` for what lazpy reads it from, and for the one case worth knowing
about: lazpy reports the declaration as it finds it and does not correct a
file whose declared units disagree with its stored coordinates.
