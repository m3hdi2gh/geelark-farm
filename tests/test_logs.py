"""The log file, written so a machine can count it.

The file is the record somebody opens weeks later on a machine they are not
sitting at, and increasingly it is what something else reads to decide whether
to raise an alarm. These pin the parts of that which would break quietly: a
line that is not valid JSON, a field that stops being written, and a value
that cannot be serialised taking the whole line with it.
"""

from __future__ import annotations

import json
import logging

import pytest

from geelark_farm import logs
from geelark_farm.logs import JsonLines, file_formatter


@pytest.fixture
def formatter(monkeypatch):
    """A JsonLines that does not shell out to git or read the hostname."""
    monkeypatch.setattr(logs, "machine", lambda: "builder-1")
    monkeypatch.setattr(logs, "revision", lambda: "v0.1.0-4-gabc1234")
    return JsonLines()


def record(msg="hello", *, args=(), level=logging.INFO, **extra):
    made = logging.LogRecord("geelark_farm.serve", level, "serve.py", 1,
                             msg, args, None)
    for key, value in extra.items():
        setattr(made, key, value)
    return made


def rendered(formatter, made) -> dict:
    line = formatter.format(made)
    assert "\n" not in line, "one object per line, or nothing can read it"
    return json.loads(line)


# ------------------------------------------------------------ what it writes
def test_a_line_is_one_json_object(formatter):
    got = rendered(formatter, record("4 warm of 10"))

    assert got["msg"] == "4 warm of 10"
    assert got["level"] == "INFO"
    assert got["logger"] == "geelark_farm.serve"
    assert got["t"].startswith("20") and got["t"].endswith("+00:00")


def test_every_line_says_which_machine_and_which_commit_wrote_it(formatter):
    """Two fields rather than a header, because a log gets tailed, rotated,
    and concatenated with another machine's - and each of those loses a
    header."""
    got = rendered(formatter, record())

    assert got["machine"] == "builder-1"
    assert got["rev"] == "v0.1.0-4-gabc1234"


def test_a_deployment_with_no_commit_to_name_leaves_the_field_out(monkeypatch):
    """An empty string in every line is noise pretending to be data."""
    monkeypatch.setattr(logs, "machine", lambda: "builder-1")
    monkeypatch.setattr(logs, "revision", lambda: "")

    assert "rev" not in rendered(JsonLines(), record())


def test_the_message_is_formatted_before_it_is_written(formatter):
    """`%s` and its argument are two things until something joins them."""
    got = rendered(formatter, record("%d warm of %d", args=(4, 10)))

    assert got["msg"] == "4 warm of 10"


def test_numbers_logged_beside_the_sentence_become_fields(formatter):
    """The whole point: something can count `warm` without matching on how
    the sentence happens to be worded this month."""
    got = rendered(formatter, record(warm=4, free_slots=24, will="finish"))

    assert got["warm"] == 4 and got["free_slots"] == 24
    assert got["will"] == "finish"


def test_a_build_says_which_row_it_is(formatter):
    got = rendered(formatter, record(row="build 3"))

    assert got["row"] == "build 3"


def test_a_line_from_outside_a_build_does_not_carry_an_empty_row(formatter):
    """The filter puts `row` on every record whether a build is running or
    not, and "" is not a row."""
    assert "row" not in rendered(formatter, record(row=""))


def test_a_traceback_is_kept_as_a_field(formatter):
    """Losing it is losing the record of the thing that went wrong."""
    try:
        raise RuntimeError("geelark went away")
    except RuntimeError:
        import sys
        made = record("a pass failed", level=logging.ERROR)
        made.exc_info = sys.exc_info()

    got = rendered(formatter, made)

    assert "RuntimeError: geelark went away" in got["exc"]


def test_nothing_the_logging_module_puts_on_a_record_leaks_in(formatter):
    """`RESERVED` is derived from a real record rather than written out, so a
    new attribute in some future Python does not start turning up as though a
    caller had passed it."""
    got = rendered(formatter, record())

    for leaked in ("levelno", "pathname", "lineno", "msecs", "args",
                   "exc_text", "stack_info", "process", "thread"):
        assert leaked not in got, leaked


# ------------------------------------------------------- when it goes wrong
def test_a_value_that_cannot_be_serialised_costs_only_its_own_readability(
        formatter):
    """A log line lost is the record of the thing that went wrong, lost."""
    class Opaque:
        def __repr__(self):
            return "<an Opaque>"

    got = rendered(formatter, record(thing=Opaque()))

    assert got["thing"] == "<an Opaque>"


def test_text_that_is_not_ascii_stays_readable(formatter):
    """Escaped to \\uXXXX it is still valid JSON and no longer something a
    person can read - and reading it is half of what the file is for.

    Asserted on the raw line rather than the parsed object: `json.loads` turns
    those escapes back into the same string, so parsing first tests nothing.
    """
    line = formatter.format(record("پروکسی مرده است"))

    assert "پروکسی مرده است" in line
    assert "\\u" not in line


# --------------------------------------------------------------- the choice
def test_json_is_asked_for_and_text_is_the_default():
    assert isinstance(file_formatter("json"), JsonLines)
    assert not isinstance(file_formatter("text"), JsonLines)


def test_the_text_format_is_the_one_the_file_has_always_had():
    """Changing it silently would break anything anyone has ever written to
    read these files."""
    made = record("hello", row="build 3")
    line = file_formatter("text").format(made)

    assert "INFO" in line and "[build 3]" in line
    assert "geelark_farm.serve: hello" in line


def test_anything_unrecognised_is_read_as_text_rather_than_refused():
    """A typo in an env var must not take the logging out with it."""
    assert not isinstance(file_formatter("jsonn"), JsonLines)


def test_a_line_from_outside_a_build_carries_no_row_at_all(formatter):
    """The filter stamps a placeholder rather than nothing, because `[-]` is
    what the text format wants. In JSON it is a field on every line that says
    nothing at all."""
    assert "row" not in rendered(formatter, record(row=logs.NO_BUILD))


def test_the_filter_and_the_formatter_agree_on_what_no_build_looks_like():
    """Written at both ends, they drift; then every line carries a row field
    saying `-` and nothing notices, because it is still valid JSON."""
    import logging as stdlib_logging

    from geelark_farm.builder import BuildContextFilter

    made = stdlib_logging.LogRecord("x", 20, "f", 1, "m", (), None)
    BuildContextFilter().filter(made)

    assert made.row == logs.NO_BUILD
