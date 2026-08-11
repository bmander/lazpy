import pytest

import lazpy


# ---------------------------------------------------------------------------
# Docstrings
#
# The API reference is generated from these, so an undocumented attribute is
# a blank line in the published docs rather than a private matter. What makes
# that worth a test is the C extension: a getset entry documents itself by
# passing a string in a table, and passing NULL is just as valid.
#
# The two lists below are derived rather than written out, so a name added to
# __all__ or a type added to the extension is covered the day it appears --
# which is the day it starts rendering on a page.
# ---------------------------------------------------------------------------

def documented(obj):
    return bool((getattr(obj, "__doc__", None) or "").strip())


def public_attributes(obj):
    """What autodoc renders under *obj*: its own public members.

    `vars` and not `dir`, because an inherited attribute is documented where
    it is defined and autodoc does not repeat it here.

    A plain value -- an int class constant, say -- passes the check below on
    its own type's docstring, since a value has nowhere of its own to carry
    one. Methods, properties and getsets are what this really covers.
    """
    return [(name, value) for name, value in vars(obj).items()
            if not name.startswith("_")]


# Everything the extension exports that lazpy re-exports, whether or not it
# is in __all__ -- docs/container.rst gives PointReader and friends a page of
# their own, so they render too.
C_TYPES = sorted(
    (value for value in vars(lazpy).values()
     if isinstance(value, type)
     and getattr(value, "__module__", None) == "lazpy._cpylaz"),
    key=lambda cls: cls.__name__)


class TestDocstrings:

    def test_the_c_types_were_found(self):
        """Otherwise the parametrised tests below pass over an empty list."""
        assert {cls.__name__ for cls in C_TYPES} >= {
            "Point", "PointReader", "PointWriter", "SpatialIndex"}

    @pytest.mark.parametrize("name", lazpy.__all__)
    def test_every_exported_name_is_documented(self, name):
        obj = getattr(lazpy, name)
        assert documented(obj), f"lazpy.{name} has no docstring"
        assert [attr for attr, value in public_attributes(obj)
                if not documented(value)] == []

    @pytest.mark.parametrize("cls", C_TYPES,
                             ids=lambda cls: cls.__name__)
    def test_every_c_type_is_documented(self, cls):
        assert documented(cls), f"{cls.__name__} has no tp_doc"
        assert [attr for attr, value in public_attributes(cls)
                if not documented(value)] == []
