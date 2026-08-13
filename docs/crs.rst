Coordinate reference systems
============================

.. automodule:: lazpy.crs

:class:`lazpy.Reader` exposes a file's coordinate reference system as
:attr:`~lazpy.Reader.crs`, and :class:`lazpy.Writer` takes one through its
``crs`` argument. Both are built on the two functions below, which a caller
working with records directly can use on their own.

.. currentmodule:: lazpy

.. autofunction:: read_crs

.. autofunction:: crs_record
