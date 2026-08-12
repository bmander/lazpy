import io
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

from lazpy.__main__ import main
from helpers import FIXTURES, fixture


# ---------------------------------------------------------------------------
# The command line: `python -m lazpy cloud.laz`, and the `lazpy` command.
#
# It is a summary of a file, so it asks the library most of what can be asked
# about one -- which is what these check. Its own formatting is not pinned;
# what is, is that every fixture can be summarised without the reading API
# refusing anything it offers.
# ---------------------------------------------------------------------------


def run(*argv):
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(argv)
    return code, out.getvalue()


@pytest.mark.parametrize("name", FIXTURES)
def test_every_fixture_can_be_summarised(name):
    code, text = run(fixture(name), "3")
    assert code == 0
    assert "points" in text and "bounds x" in text


def test_it_prints_what_it_was_asked_for():
    code, text = run(fixture("pt1_v2.laz"), "4")
    assert code == 0
    assert "first 4 points:" in text
    rows = text.split("first 4 points:")[1].splitlines()[2:]
    assert [ln.split()[0] for ln in rows if ln.strip()] == ["0", "1", "2", "3"]


def test_a_file_with_more_asked_for_than_it_has():
    """min(count, num_points), so a small file is not padded with nothing."""
    code, text = run(fixture("pt1_v2.laz"), "100000")
    assert code == 0
    assert "first 500 points:" in text


def test_no_arguments_is_the_usage_message():
    code, text = run()
    assert code == 1
    assert "python -m lazpy" in text


def test_the_module_is_runnable():
    """That __main__ is wired up at all, which importing it cannot show."""
    done = subprocess.run(
        [sys.executable, "-m", "lazpy", fixture("pt1_v2.laz"), "1"],
        capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "point format     1" in done.stdout
