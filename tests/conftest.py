"""The records/expected pair: deliberately awkward points and what they
decode to, shared by the writing and chunking tests."""

import pytest

from lazpy import Reader
from helpers import awkward_records, load, rebuilt


@pytest.fixture(scope="module")
def records():
    length = load("pt5_v0.las").header["point_data_record_length"]
    return awkward_records(400, length)


@pytest.fixture(scope="module")
def expected(records):
    """What those records decode to when nothing compresses them."""
    fp = rebuilt("pt5_v0.las", b"".join(records), len(records))
    with Reader(fp) as reader:
        return reader.checksum()
