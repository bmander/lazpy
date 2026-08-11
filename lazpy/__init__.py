"""Read and write LAS and LAZ point cloud files.

Most programs need two names: :class:`Reader`, which opens a file and hands
back its header and points -- one at a time, or as numpy arrays -- and
:class:`Writer`, which builds a file from the points given to it.
Everything else here is a type they exchange: the :class:`Point` itself,
the enumerations that describe a file, and the errors they raise.

    >>> reader = Reader("cloud.laz")
    >>> reader.num_points
    43271750
    >>> for point in reader:
    ...     print(point.X, point.Y, point.Z, point.classification)

    >>> reader.xyz()                    # or whole columns, if numpy is around
    array([[1500.04, 1700.19, 33.98], ...])

    >>> with Writer("out.laz", point_format=1) as writer:
    ...     writer.write(Point(X=1, Y=2, Z=3, gps_time=4.0))

Headers and variable length records are parsed and built in this package;
everything from the first point onward is the ``lazpy._cpylaz`` C
extension, a port of LASzip.

LAS file specification
    1.2: www.asprs.org/a/society/committees/standards/asprs_las_format_v12.pdf
    1.4: www.asprs.org/wp-content/uploads/2010/12/LAS_1_4_r13.pdf
"""

# Point, PointReader, PointWriter and SpatialIndex are re-exported: they are
# the API for driving the container directly, without a Reader or Writer over
# it. Only Point is in __all__ below, because only Point is part of reading a
# file the ordinary way; the other three are for the caller who has a point
# block and no LAS header to describe it, who can name them to import them.
# docs/container.rst gives them a page of their own for the same reason.
from ._cpylaz import (PointReader, PointWriter, Point,  # noqa: F401
                      SpatialIndex, LazError)
from .formats import (LASZIP_VLR_RECORD_ID, LASZIP_VLR_USER_ID,  # noqa: F401
                      LASZIP_VLR_KEY, LASCOMPATIBLE_VLR_KEY,
                      EXTRA_BYTES_VLR_KEY, LASINDEX_EVLR_KEY,
                      UnsupportedFileError, Compressor, Coder, Chunking,
                      Selective, ItemType, items_for_point_format)
from .headers import (header_formats, format_size,  # noqa: F401
                      unpack_format, pack_format, EXTRA_BYTES_ATTRIBUTE_SIZE)
from .extra_bytes import (ExtraBytesAttribute,  # noqa: F401
                          extra_bytes_record)
from .reader import Reader, ExtendedVariableLengthRecord  # noqa: F401
from .writer import (Writer, auto_offsets,  # noqa: F401
                     append_spatial_index)

__all__ = ["Reader", "Writer", "Point", "Chunking", "Compressor", "Coder",
           "ItemType", "Selective", "LazError", "UnsupportedFileError",
           "ExtendedVariableLengthRecord", "ExtraBytesAttribute",
           "extra_bytes_record", "auto_offsets", "append_spatial_index"]
