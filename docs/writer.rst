Writing
=======

.. currentmodule:: lazpy

.. autoclass:: Writer
   :members:
   :undoc-members:
   :show-inheritance:

Extra bytes
-----------

What a file says its extra bytes mean: a record built from the attributes
they hold, which a :class:`Writer` takes among its ``vlrs``.

.. autoclass:: ExtraBytesAttribute

.. autofunction:: extra_bytes_record
