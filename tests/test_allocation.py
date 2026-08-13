import io

import pytest

from lazpy import _cpylaz as cpylaz
from lazpy import Reader, LazError, Selective
from helpers import compress, fixture, las_records, load, packed_records


# ---------------------------------------------------------------------------
# Running out of memory.
#
# Model memory is allocated lazily, some of it partway through a chunk, so no
# input file reaches these failure paths -- only an allocator that runs out.
# cpylaz._alloc_fail_after arms one; the comment on laz_alloc_fail_after in
# src/laz_arithmetic.h says why it has to exist.
#
# The sweeps below arm it at every count in turn, so every allocation a whole
# read or a whole write makes is failed once. What they assert is uniform: an
# exception, never a crash and never a half-decoded point or a half-written
# chunk handed back as if it were whole.
# ---------------------------------------------------------------------------

@pytest.fixture
def failing_allocator():
    """Arms the model allocator to fail after n allocations.

    Disarms it however the test ends: the countdown is process-wide, so a test
    that left it armed would take every later test down with it.
    """
    try:
        yield cpylaz._alloc_fail_after
    finally:
        cpylaz._alloc_fail_after(-1)


# One fixture per compressed item version. Between them they name every
# compressed reader and writer there is: v1 and v2 cover POINT10, GPSTIME11,
# RGB12, WAVEPACKET13 and BYTE; v3 and v4 cover POINT14, RGB14, RGBNIR14,
# WAVEPACKET14 and BYTE14, in both of the flavours the layered coders have.
ALLOCATION_FIXTURES = ["pt5_v1.laz", "pt5_v2.laz", "pt7_v3.laz", "pt10_v4.laz"]

# What to feed the writers for each, from the same sources the byte-identity
# tests above use: the .las records for the legacy formats, and the fixture's
# own decoded points repacked for the LAS 1.4 ones.
ALLOCATION_RECORDS = {
    "pt5_v1.laz": lambda: las_records("pt5_v0.las"),
    "pt5_v2.laz": lambda: las_records("pt5_v0.las"),
    "pt7_v3.laz": lambda: packed_records("pt7_v3.laz"),
    "pt10_v4.laz": lambda: packed_records("pt10_v4.laz"),
}


def sweep_allocation_failures(arm, attempt):
    """Run `attempt` with the allocator failing after 0, 1, 2 ... allocations.

    `attempt` returns how far it got, and raises LazError if the allocator
    got in its way. Returns the progress of every failed run, then of the
    first complete one -- so the last entry is the attempt run unimpeded.
    """
    progress = []
    while len(progress) < 100000:
        arm(len(progress))
        try:
            progress.append(attempt())
        except LazError as exc:
            progress.append(exc.progress)
            continue
        finally:
            arm(-1)
        return progress
    raise AssertionError("the allocation sweep did not converge")


def failing_at(exc, progress):
    """Tag a LazError with how far the run got, for the sweep to collect."""
    exc.progress = progress
    return exc


@pytest.mark.parametrize("name", ALLOCATION_FIXTURES)
def test_reading_survives_every_allocation_failure(name, failing_allocator):
    """No allocation failure while decoding turns into a crash, and none of
    them yields the point it failed on."""
    data = load(name).data

    def read_all():
        got = []
        try:
            with Reader(io.BytesIO(data)) as reader:
                for point in reader:
                    got.append((point.X, point.Y, point.Z))
        except LazError as exc:
            raise failing_at(exc, got)
        return got

    *partials, expected = sweep_allocation_failures(failing_allocator,
                                                    read_all)

    assert len(partials) > 100, "the fixture allocates too little to be a test"
    # every point that did come out is the point that file holds there
    assert all(got == expected[:len(got)] for got in partials)
    # A failure after the chunk's first point is one the reader met partway
    # through decoding -- the on-demand bank path, which init() cannot report.
    assert max(len(got) for got in partials) > 1, "no failure landed mid-chunk"


@pytest.mark.parametrize("name", ALLOCATION_FIXTURES)
def test_writing_survives_every_allocation_failure(name, failing_allocator):
    """Nor does one while encoding: a chunk that cannot be modelled is an
    error, not a chunk quietly written without those points in it."""
    f = load(name)
    records = ALLOCATION_RECORDS[name]()
    start = f.header["offset_to_point_data"]

    def write_all():
        written = 0

        def counting():
            nonlocal written
            for record in records:
                yield record
                written += 1

        fp = io.BytesIO(f.data[:start])
        fp.seek(0, io.SEEK_END)
        try:
            compress(fp, counting(), f.items, f.compressor, f.chunk_size)
        except LazError as exc:
            raise failing_at(exc, written)
        return written

    *partials, complete = sweep_allocation_failures(failing_allocator,
                                                    write_all)

    assert complete == len(records)
    assert len(partials) > 100, "the fixture allocates too little to be a test"
    # as above: a failure after the first point of a chunk is one the writer
    # met in the middle of encoding rather than while starting the chunk. The
    # tail of the sweep fails in done(), with every record written but the
    # last chunk unclosed -- an error there is still an error.
    assert max(partials) > 1, "no failure landed mid-chunk"


# ---------------------------------------------------------------------------
# Not allocating in the first place.
#
# The sweep above uses the armed allocator to reach failure paths. The same
# counter answers a different question: how many models a read builds at all.
# The smallest budget a read survives on is that number, and bisecting for it
# costs a dozen reads where the sweep's linear walk would cost seventeen
# hundred.
# ---------------------------------------------------------------------------

def survives_with(arm, name, mask, budget):
    """Whether decoding `name` under `mask` completes on `budget` models.

    The armed allocator fails the allocation after `budget` of them, so this
    is "the read needs no more than `budget`" -- and it is monotonic in
    `budget`, which is what makes the bisection below sound.
    """
    arm(budget)
    try:
        with Reader(fixture(name), decompress_selective=mask) as reader:
            reader.checksum()
        return True
    except (LazError, MemoryError):
        return False
    finally:
        arm(-1)


def model_allocations(arm, name, mask):
    """How many models decoding `name` under `mask` allocates.

    The smallest budget it survives on, found by bisection -- walking it takes
    as many whole reads as there are models, and there are hundreds.
    """
    low, high = 0, 1
    while not survives_with(arm, name, mask, high):
        high *= 2
    while low < high:
        middle = (low + high) // 2
        if survives_with(arm, name, mask, middle):
            high = middle
        else:
            low = middle + 1
    return low


@pytest.mark.parametrize("name", ["pt7_v3.laz", "pt8_v4.laz"])
def test_a_dropped_colour_layer_builds_no_models(name, failing_allocator):
    """Skipping colour skips its models, not just its bytes.

    A layer nobody asked for is stepped over in layer_load, so its models are
    never decoded with -- but they were still being built, once per context,
    for RGB14 (issue #99). Its two neighbours in laz_readitem_v3.c had always
    guarded that on `requested`; this pins the third.

    Stated as "one model short of the full read is enough" rather than as a
    count, so that a model added to Rgb14Context does not fail this test. The
    saving is 7 models per context over 4 contexts today; what a lost guard
    looks like is no saving at all.
    """
    mask = Selective.ALL & ~(Selective.RGB | Selective.NIR)
    everything = model_allocations(failing_allocator, name, Selective.ALL)
    assert survives_with(failing_allocator, name, mask, everything - 1)
