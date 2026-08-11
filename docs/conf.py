"""Sphinx configuration.

The API pages are generated from docstrings, so there is no second copy of
the reference to keep true; docs/index.md includes the README rather than
restating the quickstart.

Autodoc imports lazpy for real, extension and all. It has to: Point,
PointReader and SpatialIndex are the C extension's own types, and mocking
the module away would erase the type you actually iterate over.
"""
from importlib.metadata import version as _version

project = "lazpy"
copyright = "2025, Brandon Martin-Anderson"
author = "Brandon Martin-Anderson"

# From the installed distribution rather than a literal here, so it follows
# pyproject.toml instead of drifting from it.
release = _version("lazpy")

# No napoleon. Nothing here is written in Google or NumPy sections, and with
# it on, the first line of every one-line attribute doc that holds a colon --
# "unscaled x: multiply by the header's scale" -- is read as a field name and
# rendered as a stray "Type:" row.
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",
]

exclude_patterns = ["_build"]

html_theme = "furo"
html_title = f"lazpy {release}"

# Python alone: the documented surface references no numpy type, so a numpy
# inventory would be a download per build that resolves nothing. The timeout
# is here because a build that cannot reach an inventory warns, and warnings
# are errors -- better to fail in ten seconds than to hang on a dead socket.
intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}
intersphinx_timeout = 10

autodoc_member_order = "bysource"
