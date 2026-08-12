"""The projection records: GeoTIFF geokeys and the OGC WKT string.

A LAS file names its coordinate reference system in one of two records, both
under the ``LASF_Projection`` user id. The older is the GeoTIFF
GeoKeyDirectory, record 34735, a list of numbered keys borrowed wholesale from
the TIFF tags that carry georeferencing; LAS 1.4 replaced it for the extended
point formats with record 2112, which holds an OGC WKT string. Files in the
wild carry either, and some carry both.

What both are asked for here is the same thing: the system the coordinates are
in, as a :class:`pyproj.CRS`. The keys that describe a *user-defined* system
one parameter at a time -- and the value records, 34736 and 34737, that such a
description spills into -- are not read, because a CRS assembled from them
would be a definition rather than a reference to a published one, and nothing
in a LAS file says the assembly was done right.

Nor is anything second-guessed. A file that names an EPSG code in metres while
its coordinates are plainly in feet, which the ``ProjLinearUnits`` key is the
usual sign of, gets reported as the code it names; whether the producer meant
the code's foot-based sibling or made a mistake is a judgement this is in no
position to make. See issue #91.
"""

from .formats import GEOKEY_DIRECTORY_KEY, WKT_VLR_KEY
from .headers import (GEOKEY_DIRECTORY_FORMAT, GEOKEY_ENTRY_FORMAT,
                      format_size, pack_format, unpack_format)

# The two geokeys that name a system outright, and the model type that says
# which of them to expect. Everything else in a directory either describes a
# user-defined system or annotates one, and is not read.
_GT_MODEL_TYPE = 1024
_GEOGRAPHIC_TYPE = 2048
_PROJECTED_CS_TYPE = 3072

_MODEL_TYPE_PROJECTED = 1
_MODEL_TYPE_GEOGRAPHIC = 2

# "SHALL be EPSG codes" -- OGC GeoTIFF 1.1, requirements classes
# ProjectedCRSGeoKey and GeodeticCRSGeoKey. Outside the range are 0, meaning
# undefined, and 32767, meaning user-defined: neither is a code to look up.
_EPSG_CODES = range(1024, 32767)

_DIRECTORY_SIZE = format_size(GEOKEY_DIRECTORY_FORMAT)
_ENTRY_SIZE = format_size(GEOKEY_ENTRY_FORMAT)

# The directory revision everything since 1995 has written, and the only one
# whose entry layout is the one above.
_DIRECTORY_VERSION = {'key_directory_version': 1, 'key_revision': 1,
                      'minor_revision': 0}


def _pyproj():
    """pyproj, or an ImportError that says what wants it."""
    try:
        import pyproj
    except ImportError:
        raise ImportError(
            "reading and writing a CRS needs pyproj; install it with "
            "`pip install pyproj` or `pip install lazpy[crs]`") from None
    return pyproj


def _geokeys(data):
    """``{key_id: value}`` for the keys a GeoKeyDirectory states inline.

    Keys whose value lives in one of the value records are left out, along
    with the whole directory if it is too short to hold what it declares --
    unpack_format reads a short record as zeros rather than refusing it, so
    the length is checked here.
    """
    if len(data) < _DIRECTORY_SIZE:
        return {}
    directory, offset = unpack_format(GEOKEY_DIRECTORY_FORMAT, data)
    count = directory['number_of_keys']
    if len(data) < _DIRECTORY_SIZE + count * _ENTRY_SIZE:
        return {}

    keys = {}
    for _ in range(count):
        entry, offset = unpack_format(GEOKEY_ENTRY_FORMAT, data, offset)
        # a key with its value anywhere but in the entry itself points into
        # 34736 or 34737, which is a user-defined system being spelled out
        if entry['tiff_tag_location'] == 0:
            keys[entry['key_id']] = entry['value_offset']
    return keys


def _from_geokeys(data, pyproj):
    keys = _geokeys(data)
    # A projected system wins over a geographic one when both are named: the
    # geographic key is then the datum the projection is on, and the
    # coordinates in the file are the projected ones.
    for key in (_PROJECTED_CS_TYPE, _GEOGRAPHIC_TYPE):
        if keys.get(key) in _EPSG_CODES:
            try:
                return pyproj.CRS.from_epsg(keys[key])
            except pyproj.exceptions.CRSError:
                return None      # a code PROJ has never heard of
    return None


def _from_wkt(data, pyproj):
    text = bytes(data).split(b'\0', 1)[0].decode('utf-8', errors='replace')
    if not text.strip():
        return None
    try:
        return pyproj.CRS.from_wkt(text)
    except pyproj.exceptions.CRSError:
        return None              # not WKT, or WKT PROJ cannot make sense of


def read_crs(vlrs, evlrs=None):
    """The CRS these records declare, or None.

    Both arguments are mappings keyed by ``(user_id, record_id)``, as
    ``header['variable_length_records']`` and its extended counterpart are;
    passing the second is how a file that keeps its projection behind the
    point data is read.

    The WKT record is preferred over the geokeys wherever both are readable.
    LAS 1.4 says as much for the extended point formats, and a file carrying
    both has usually had the WKT added by the newer tool of the two.

    None means the file said nothing this can use: no projection record, a
    record too short or too damaged to parse, a system named as user-defined,
    or an EPSG code the PROJ database does not have. Nothing is warned about
    -- see the module docstring.

    pyproj is wanted whatever the records turn out to say, so that asking a
    file with no projection is the same call as asking one with a damaged
    record.
    """
    pyproj = _pyproj()
    for key, parse in ((WKT_VLR_KEY, _from_wkt),
                       (GEOKEY_DIRECTORY_KEY, _from_geokeys)):
        for records in (vlrs, evlrs or {}):
            record = records.get(key)
            if record is not None:
                crs = parse(record['data'], pyproj)
                if crs is not None:
                    return crs
    return None


def crs_record(crs, wkt=False, description=b''):
    """The record stating *crs*, as a record a :class:`~lazpy.Writer` takes.

    *crs* is anything ``pyproj.CRS.from_user_input`` accepts -- a
    :class:`pyproj.CRS`, an ``"EPSG:2927"``, a WKT string.

    The default is a GeoKeyDirectory, which every LAS version reads. ``wkt``
    asks for the OGC WKT record instead, which is what the LAS 1.4 point
    formats call for; a writer given a ``crs`` chooses between them by point
    format, so this is only for building a record by hand::

        >>> Writer("out.laz", point_format=1,
        ...        vlrs=[crs_record("EPSG:2927")])   # doctest: +SKIP

    Writing geokeys needs *crs* to have an EPSG code, since a directory
    referring to a published system is all that is written; a CRS without one
    can only be written as WKT.
    """
    crs = _pyproj().CRS.from_user_input(crs)
    if wkt:
        # LAS calls this record ASCII, but a WKT string out of PROJ has
        # degree signs in it and every reader takes UTF-8, laszip and laspy
        # included; null-terminated, as the record is defined
        key, data = WKT_VLR_KEY, crs.to_wkt().encode('utf-8') + b'\0'
    else:
        key, data = GEOKEY_DIRECTORY_KEY, _directory(crs)
    return {'user_id': key[0], 'record_id': key[1],
            'description': description, 'data': data}


def _directory(crs):
    """A GeoKeyDirectory naming *crs* and nothing else."""
    if crs.is_projected:
        keys = {_GT_MODEL_TYPE: _MODEL_TYPE_PROJECTED,
                _PROJECTED_CS_TYPE: _epsg(crs)}
    elif crs.is_geographic:
        keys = {_GT_MODEL_TYPE: _MODEL_TYPE_GEOGRAPHIC,
                _GEOGRAPHIC_TYPE: _epsg(crs)}
    else:
        raise ValueError(
            f"{crs.name} is neither projected nor geographic, and the "
            "geokeys have no way to name it; write it as WKT instead")

    data = pack_format(GEOKEY_DIRECTORY_FORMAT,
                       dict(_DIRECTORY_VERSION, number_of_keys=len(keys)))
    # in ascending key id, as the format requires
    return data + b''.join(
        pack_format(GEOKEY_ENTRY_FORMAT,
                    {'key_id': key, 'tiff_tag_location': 0, 'count': 1,
                     'value_offset': keys[key]})
        for key in sorted(keys))


def _epsg(crs):
    code = crs.to_epsg()
    if code not in _EPSG_CODES:
        raise ValueError(
            f"{crs.name} has no EPSG code in the range the geokeys can "
            "state, so it cannot be written as a GeoKeyDirectory; write it "
            "as WKT instead")
    return code
