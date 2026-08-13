Writing
=======

.. currentmodule:: lazpy

.. autoclass:: Writer
   :members:
   :undoc-members:
   :show-inheritance:

Coordinates
-----------

.. autofunction:: auto_offsets

Extra bytes
-----------

A file declares what its extra bytes mean in a record that describes each
extra attribute. Build that record from :class:`ExtraBytesAttribute` values
with :func:`extra_bytes_record`, and pass it to :class:`Writer` among its
``vlrs``.

.. autoclass:: ExtraBytesAttribute

.. autofunction:: extra_bytes_record
