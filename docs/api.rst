API reference
=============

The file front end
------------------

Everything ``from lazpy import *`` gives you: ``Reader`` and ``Writer``, which
read and write a LAS or LAZ *file* -- header and points together -- and the
types and enumerations they hand back or take.

.. automodule:: lazpy
   :members:
   :undoc-members:
   :show-inheritance:

The container layer
-------------------

.. autoclass:: lazpy.PointReader
   :members:
   :undoc-members:

.. autoclass:: lazpy.PointWriter
   :members:
   :undoc-members:

.. autoclass:: lazpy.SpatialIndex
   :members:
   :undoc-members:
