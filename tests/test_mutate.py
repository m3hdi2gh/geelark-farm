"""The mutation harness itself.

It lived in a scratch directory for the whole of the test audit and was
deleted twice by cleanups, which is half of why it is here. The other half is
that it produced wrong results once, silently: two runs over one file
disagreed because the child was reading stale bytecode, and a mutation
reported as killed on that basis is a hole the report says is covered.

A dev tool, so this is not exhaustive - it holds the parts that were wrong
before, and the contract the callers rely on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.mutate import (  # noqa: E402
    CHILD_ENV,
    RUN_TIMEOUT,
    count,
    mutate,
    pytest_path,
)

SOURCE = """
def decide(value, limit):
    if value < limit:
        return True
    return False


def guard(items):
    if not items:
        return 0
    return len(items)
"""


# ------------------------------------------------------------ what it changes
def test_it_finds_every_kind_of_change_it_claims_to():
    """Comparisons, booleans, a dropped `not`, and the small integers. A
    change it cannot make is a hole it cannot report."""
    made = {mutate(SOURCE, i)[1] for i in range(count(SOURCE))}
    kinds = {what.split(": ", 1)[1] for what in made}

    assert "Lt -> GtE" in kinds
    assert "True -> False" in kinds
    assert "False -> True" in kinds
    assert "dropped `not`" in kinds
    assert "0 -> 1" in kinds


def test_each_index_changes_exactly_one_thing():
    """The whole method rests on it: two changes at once and a survivor says
    nothing about either."""
    import ast

    # Against the unparsed original, not the file as written: `ast.unparse`
    # reformats everything, so comparing with the source counts every line as
    # changed. The harness compares the same way.
    baseline = ast.unparse(ast.parse(SOURCE)).splitlines()

    for index in range(count(SOURCE)):
        changed, what = mutate(SOURCE, index)
        differences = [a for a, b in zip(baseline, changed.splitlines(),
                                         strict=True) if a != b]
        assert len(differences) == 1, f"{what} moved {len(differences)} lines"


def test_the_source_it_returns_is_still_python():
    """It is written to disk and imported. Anything that does not compile is
    reported as a killed mutation - a false negative dressed as a result."""
    import ast

    for index in range(count(SOURCE)):
        ast.parse(mutate(SOURCE, index)[0])


def test_counting_is_stable_so_an_index_means_the_same_thing_twice():
    """A report names a mutation by index. If the count moved between runs the
    names would point at different changes."""
    assert count(SOURCE) == count(SOURCE)
    assert mutate(SOURCE, 0)[1] == mutate(SOURCE, 0)[1]


def test_a_name_says_which_line_it_touched():
    """The report is read by looking the line up, so this is most of what
    makes it usable."""
    _, what = mutate(SOURCE, 0)

    assert what.startswith("line ")
    assert ": " in what


# --------------------------------------------------- what went wrong before
def test_the_child_is_told_not_to_write_bytecode():
    """CPython invalidates a .pyc on the source's size and its mtime truncated
    to whole seconds. This rewrites one file dozens of times a second, and
    `ast.unparse` output for opposite mutations of one operator is very often
    the same length - so a run could execute bytecode compiled for the
    mutation before it. Two runs over one file disagreed, 23 then 21."""
    assert CHILD_ENV.get("PYTHONDONTWRITEBYTECODE") == "1"


def test_a_run_that_will_not_finish_is_bounded():
    """A mutation that breaks a poll loop makes the tests wait out a real
    deadline. Without a bound the whole run stops there."""
    assert 0 < RUN_TIMEOUT <= 300


def test_the_pytest_it_runs_is_the_one_in_the_virtualenv(tmp_path,
                                                          monkeypatch):
    """Not the bare name off PATH: that may belong to another environment, and
    a run against the wrong interpreter reports on code nobody is changing.

    Both layouts, because this repository is developed on Windows and run on a
    Mac.
    """
    monkeypatch.chdir(tmp_path)

    windows = tmp_path / ".venv" / "Scripts"
    windows.mkdir(parents=True)
    (windows / "pytest.exe").write_text("", encoding="utf-8")
    assert "Scripts" in pytest_path()

    for path in windows.iterdir():
        path.unlink()
    posix = tmp_path / ".venv" / "bin"
    posix.mkdir(parents=True)
    (posix / "pytest").write_text("", encoding="utf-8")
    assert pytest_path().endswith("pytest")


def test_with_no_virtualenv_it_falls_back_rather_than_failing(tmp_path,
                                                              monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert pytest_path() == "pytest"


# ------------------------------------------------------------ the interface
def test_called_with_nothing_it_explains_itself_and_says_so(capsys):
    """Exit 2, so a script that shells out to it does not read the usage text
    as a clean run with no survivors."""
    from scripts import mutate as harness

    monkeypatched = list(sys.argv)
    sys.argv = ["mutate.py"]
    try:
        assert harness.main() == 2
    finally:
        sys.argv = monkeypatched

    assert "survivor" in capsys.readouterr().out


def test_a_file_with_nothing_to_change_is_not_an_error():
    """Constants, a docstring, no branches. Zero is an answer."""
    assert count('"""Nothing here."""\nNAME = "geelark"\n') == 0


@pytest.mark.parametrize("index", [0, 1])
def test_two_mutations_may_share_a_name(index):
    """`range(1, attempts + 1)` has two ones on one line, so a name is a label
    rather than an identity - which is why a survivor sometimes has to be
    found by walking the indices rather than by matching its name."""
    source = "def f(attempts):\n    return list(range(1, attempts + 1))\n"

    names = [mutate(source, i)[1] for i in range(count(source))]

    assert len(names) == 2
    assert names[0] == names[1]
    assert mutate(source, 0)[0] != mutate(source, 1)[0]
