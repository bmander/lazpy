The on-disk format
==================

The tables a LAS header is made of and the functions that read and write
anything those tables describe. :class:`lazpy.Reader` and
:class:`lazpy.Writer` use these; a caller parsing a header directly can
too.

.. currentmodule:: lazpy

.. autofunction:: items_for_point_format

.. autofunction:: header_formats

.. autofunction:: format_size

.. autofunction:: unpack_format

.. autofunction:: pack_format
