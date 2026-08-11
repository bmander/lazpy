API reference
=============

The file front end
------------------

.. automodule:: lazpy
   :members:
   :undoc-members:
   :show-inheritance:

The container layer
-------------------

What ``Reader`` and ``Writer`` drive: the point block alone, with no LAS
header over it. Reach for these only with a point block some other header
describes.

.. autoclass:: lazpy.PointReader
   :members:
   :undoc-members:

.. autoclass:: lazpy.PointWriter
   :members:
   :undoc-members:

.. autoclass:: lazpy.SpatialIndex
   :members:
   :undoc-members:
