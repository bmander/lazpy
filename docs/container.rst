The container layer
===================

``Reader`` and ``Writer`` are built on the three classes below, which
handle the point block alone, with no LAS header over it. Reach for them
directly only when some other header already describes the point block.

.. currentmodule:: lazpy

.. autoclass:: PointReader
   :members:
   :undoc-members:

.. autoclass:: PointWriter
   :members:
   :undoc-members:

.. autoclass:: SpatialIndex
   :members:
   :undoc-members:
