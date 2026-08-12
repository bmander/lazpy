import struct
import subprocess
import sys

import pytest

from lazpy import (GEOKEY_DIRECTORY_KEY, WKT_VLR_KEY, Point, Reader, Writer,
                   crs_record, read_crs)
from helpers import a_record, fixture


# ---------------------------------------------------------------------------
# The coordinate reference system.
#
# pyproj is optional -- reading points has never needed it -- so these skip
# rather than fail when it is absent, as the array tests do for numpy. What
# does not need it is the shape of the records themselves, which is checked
# by hand below: a geokey directory is uint16s, and a wrong one would be a
# file no other reader could place.
# ---------------------------------------------------------------------------

try:
    import pyproj
except ImportError:                                     # pragma: no cover
    pyproj = None

needs_pyproj = pytest.mark.skipif(pyproj is None, reason="needs pyproj")

# NAD83(HARN) / Washington South (ftUS), and a geographic one to go with it
PROJECTED = 2927
GEOGRAPHIC = 4326


def geokeys(data):
    """``{key: value}`` from a directory, unpacked here rather than by the
    code under test, so a change to either has to face the other."""
    version, revision, minor, count = struct.unpack_from("<4H", data)
    assert (version, revision, minor) == (1, 1, 0)
    assert len(data) == 8 + count * 8
    entries = struct.unpack_from(f"<{count * 4}H", data, 8)
    return {entries[i]: entries[i + 3] for i in range(0, count * 4, 4)}


def written(tmp_path, name="crs.laz", point_format=1, **kwargs):
    """A one-point file, and its reader."""
    path = str(tmp_path / name)
    with Writer(path, point_format=point_format, **kwargs) as writer:
        writer.write(Point(X=1, Y=2, Z=3))
    return Reader(path)


# ---------------------------------------------------------------------------
# Reading


@needs_pyproj
def test_reads_a_geokey_directory(tmp_path):
    with written(tmp_path, crs=f"EPSG:{PROJECTED}") as reader:
        assert reader.crs.to_epsg() == PROJECTED


@needs_pyproj
def test_reads_a_wkt_record(tmp_path):
    # point format 6 is written as WKT, which is what LAS 1.4 asks for
    with written(tmp_path, point_format=6, crs=f"EPSG:{PROJECTED}") as reader:
        assert WKT_VLR_KEY in reader.header["variable_length_records"]
        assert reader.crs.to_epsg() == PROJECTED


@needs_pyproj
def test_wkt_wins_over_geokeys():
    # a file with both has usually had the WKT added by the newer tool
    records = {WKT_VLR_KEY: crs_record(f"EPSG:{PROJECTED}", wkt=True),
               GEOKEY_DIRECTORY_KEY: crs_record(f"EPSG:{GEOGRAPHIC}")}
    assert read_crs(records).to_epsg() == PROJECTED


@needs_pyproj
def test_a_file_without_a_projection_has_no_crs():
    with Reader(fixture("pt0_v2.laz")) as reader:
        assert reader.crs is None


@needs_pyproj
def test_the_crs_is_parsed_once(tmp_path):
    with written(tmp_path, crs=f"EPSG:{PROJECTED}") as reader:
        assert reader.crs is reader.crs


@needs_pyproj
@pytest.mark.parametrize("data", [
    b"",                                      # nothing at all
    b"\x01\x00\x01\x00\x00\x00\x05\x00",      # five keys, none of them there
    struct.pack("<8H", 1, 1, 0, 1, 3072, 0, 1, 32767),      # user-defined
    struct.pack("<8H", 1, 1, 0, 1, 3072, 0, 1, 0),          # undefined
    struct.pack("<8H", 1, 1, 0, 1, 1024, 0, 1, 1),          # no CRS named
])
def test_unreadable_geokeys_are_no_crs(data):
    # a record lazpy cannot read is None rather than a warning or a raise:
    # nothing here is worth refusing to open a file over, and what a file
    # meant by a damaged record is not ours to guess
    assert read_crs({GEOKEY_DIRECTORY_KEY: a_record(*GEOKEY_DIRECTORY_KEY,
                                                    data)}) is None


@needs_pyproj
@pytest.mark.parametrize("data", [b"", b"\x00", b"PROJCS[unclosed"])
def test_an_unreadable_wkt_record_is_no_crs(data):
    assert read_crs({WKT_VLR_KEY: a_record(*WKT_VLR_KEY, data)}) is None


@needs_pyproj
def test_a_damaged_wkt_record_falls_back_to_the_geokeys():
    records = {WKT_VLR_KEY: a_record(*WKT_VLR_KEY, b"PROJCS[unclosed"),
               GEOKEY_DIRECTORY_KEY: crs_record(f"EPSG:{PROJECTED}")}
    assert read_crs(records).to_epsg() == PROJECTED


@needs_pyproj
def test_extended_records_are_looked_in_too():
    # LAS 1.4 may keep the projection behind the point data
    evlrs = {WKT_VLR_KEY: crs_record(f"EPSG:{PROJECTED}", wkt=True)}
    assert read_crs({}, evlrs).to_epsg() == PROJECTED


@pytest.mark.skipif(pyproj is not None, reason="needs pyproj to be absent")
def test_reading_a_crs_without_pyproj_says_so():
    # and says it for any file, not just one that turns out to have a
    # projection record: which files raise is not a thing to have to know
    with Reader(fixture("pt0_v2.laz")) as reader:
        with pytest.raises(ImportError, match="pyproj"):
            reader.crs


# ---------------------------------------------------------------------------
# Writing


@needs_pyproj
def test_geokeys_name_the_projected_system():
    record = crs_record(f"EPSG:{PROJECTED}")
    assert (record["user_id"], record["record_id"]) == GEOKEY_DIRECTORY_KEY
    assert geokeys(record["data"]) == {1024: 1, 3072: PROJECTED}


@needs_pyproj
def test_geokeys_name_a_geographic_system_as_one():
    assert geokeys(crs_record(f"EPSG:{GEOGRAPHIC}")["data"]) == {
        1024: 2, 2048: GEOGRAPHIC}


@needs_pyproj
def test_a_wkt_record_is_null_terminated():
    data = crs_record(f"EPSG:{PROJECTED}", wkt=True)["data"]
    assert data.endswith(b"\0")
    assert b"\0" not in data[:-1]


@needs_pyproj
def test_a_crs_with_no_epsg_code_cannot_be_geokeys():
    # a projection nobody has published: the geokeys can only refer to one
    # that has been, so this is a WKT record or nothing
    local = pyproj.CRS.from_proj4(
        "+proj=tmerc +lat_0=12.34 +lon_0=56.78 +k=0.99987 "
        "+x_0=1234 +y_0=5678 +datum=WGS84 +units=m +no_defs")
    with pytest.raises(ValueError, match="no EPSG code"):
        crs_record(local)
    assert read_crs({WKT_VLR_KEY: crs_record(local, wkt=True)}) == local


@needs_pyproj
def test_las_14_files_flag_their_wkt(tmp_path):
    with written(tmp_path, point_format=6, crs=f"EPSG:{PROJECTED}") as reader:
        assert reader.header["global_encoding"] & 0x10
    with written(tmp_path, crs=f"EPSG:{PROJECTED}") as reader:
        assert not reader.header["global_encoding"] & 0x10


@needs_pyproj
def test_a_file_states_its_projection_once(tmp_path):
    with pytest.raises(ValueError, match="two answers"):
        written(tmp_path, crs=f"EPSG:{PROJECTED}",
                vlrs=[crs_record(f"EPSG:{GEOGRAPHIC}")])


@needs_pyproj
def test_a_projection_record_given_as_a_vlr_still_reads(tmp_path):
    with written(tmp_path, vlrs=[crs_record(f"EPSG:{PROJECTED}")]) as reader:
        assert reader.crs.to_epsg() == PROJECTED


@needs_pyproj
def test_the_projection_record_carries_the_writers_description(tmp_path):
    # as the LASzip record the writer builds does, rather than a second
    # hardcoded name of its own
    with written(tmp_path, crs=f"EPSG:{PROJECTED}",
                 vlr_description=b"who wrote this") as reader:
        record = reader.header["variable_length_records"][
            GEOKEY_DIRECTORY_KEY]
        assert record["description"] == b"who wrote this"


def test_summarising_a_file_without_a_projection_skips_pyproj(tmp_path):
    # pyproj costs more to import than the rest of a summary put together,
    # and a file with no projection record has nothing to spend it on
    script = (
        "import sys;"
        "from lazpy.__main__ import summarize;"
        "from lazpy import Reader;"
        f"reader = Reader({fixture('pt1_v1_pointwise.laz')!r});"
        "summarize(reader);"
        "reader.close();"
        "sys.stderr.write(repr('pyproj' in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", script],
                         capture_output=True, text=True, check=True)
    assert out.stderr == "False"


@needs_pyproj
def test_a_crs_survives_a_copy(tmp_path):
    with written(tmp_path, crs=f"EPSG:{PROJECTED}") as source:
        with written(tmp_path, "copy.laz", crs=source.crs) as copy:
            assert copy.crs == source.crs
