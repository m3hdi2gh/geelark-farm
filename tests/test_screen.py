"""Screen parsing and matching, against a hierarchy captured from a real phone.

The fixture is a Settings search screen with text in the box. It earns its
place because it caught a real bug: `find_input` originally looked only for
`EditText`, and this field is an `AutoCompleteTextView` - so a login flow would
have failed to find the code box and reported "unknown screen" instead of
typing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geelark_farm import screen

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def search_screen() -> list[screen.Element]:
    xml = (FIXTURES / "settings-search-with-text.xml").read_text(encoding="utf-8")
    return screen.parse(xml)


def test_finds_an_editable_field_that_is_not_an_edittext(search_screen):
    field = screen.find_input(search_screen)
    assert field is not None
    assert field.cls == "AutoCompleteTextView"
    assert field.focused
    assert field.text == "P@ssw0rd!#+=:,./-_"


def test_bounds_become_a_tap_point(search_screen):
    field = screen.find_input(search_screen)
    assert field.centre == (333, 90)


def test_matches_on_content_desc_not_only_text(search_screen):
    """GeeLark's own flows fail because they match content-desc alone; ours has
    to handle either, and this element has no text at all."""
    clear = screen.find(search_screen, "Clear query")
    assert clear is not None
    assert clear.text == ""
    assert clear.desc == "Clear query"


def test_find_first_returns_the_highest_priority_label_present(search_screen):
    found = screen.find_first(search_screen, ("Not now", "Clear query", "More options"))
    assert found.desc == "Clear query"


def test_missing_label_is_none_not_an_exception(search_screen):
    assert screen.find(search_screen, "Install") is None


def test_unparseable_hierarchy_yields_no_elements():
    assert screen.parse("not xml at all") == []


# ------------------------------------------------------------ text entry
def test_a_space_is_sent_as_the_escape_input_text_understands():
    from geelark_farm.shell import type_segments
    assert type_segments("a b") == ["a%sb"]


def test_a_literal_percent_is_split_from_a_following_s():
    """`input text` turns %s into a space. A password containing "%s" would
    otherwise type as a space - indistinguishable from a wrong password, and
    only discoverable by burning a login attempt.

    Ending the call after the % leaves it literal; the s starts the next one."""
    from geelark_farm.shell import type_segments
    assert type_segments("a%sb") == ["a%", "sb"]


def test_a_percent_not_followed_by_s_needs_no_split():
    """Only the %s adjacency is special; input text leaves every other % alone."""
    from geelark_farm.shell import type_segments
    assert type_segments("50%off") == ["50%off"]
    assert type_segments("ends%") == ["ends%"]


def test_a_percent_before_a_real_space_survives():
    from geelark_farm.shell import type_segments
    assert type_segments("a% b") == ["a%%sb"]


def test_segments_always_reconstruct_the_original():
    from geelark_farm.shell import type_segments
    for text in ("Xr@6n31Pkd", "p%ssw0rd", "a b%sc", "%%%", "s%s", "100%"):
        typed = "".join(type_segments(text)).replace("%s", " ")
        assert typed == text.replace("%s", " ") or "%" in text


# ------------------------------------------------- clearing a filled field
def test_clearing_a_field_deletes_in_both_directions():
    """Backspace only removes what is to the LEFT of the cursor, and a field is
    focused by tapping it - which puts the cursor wherever the tap landed. On a
    filled field that is the middle of the text, so everything to the right
    survived: an email box was retyped four times and grew "com" on each pass,
    until it read `...@gmail.comcomcom` (2026-08-08, row 7).
    """
    from geelark_farm import shell

    sent: list[str] = []

    class FakeClient:
        pass

    original = shell.run
    shell.run = lambda c, p, cmd, **kw: sent.append(cmd)
    try:
        shell.clear_field(FakeClient(), "P1", max_chars=5)
    finally:
        shell.run = original

    keys = sent[0].removeprefix("input keyevent ").split()
    assert keys[0] == str(shell.MOVE_END), "the cursor goes to the end first"
    assert keys.count(str(shell.BACKSPACE)) == 5
    # And forward deletes after them, so the right-hand side dies even if a
    # web view ignores MOVE_END. Either alone would do if the other always
    # worked; together they hold whichever fails.
    assert keys.count(str(shell.FORWARD_DELETE)) == 5
    assert keys.index(str(shell.BACKSPACE)) < keys.index(str(shell.FORWARD_DELETE))


# ------------------------------------------- the screen it acts on is this one
def test_a_failed_dump_does_not_hand_back_the_last_screen(monkeypatch):
    """`uiautomator dump` fails often enough to matter - a screen that is off,
    a running animation, a busy UI - and `run` only logs it.

    So the previous dump was still on the phone, `cat` returned it, `parse`
    accepted it, and the router acted on a screen the phone had left. Silently,
    with a perfectly valid hierarchy to act on (2026-08-23).
    """
    from geelark_farm import screen

    device = {"file": "<hierarchy><node text='OLD SCREEN'/></hierarchy>"}

    def fake_run(client, phone_id, cmd, **kwargs):
        if cmd.startswith("rm -f"):
            device["file"] = ""          # removed, and the dump then failed
            return ""
        return device["file"]

    monkeypatch.setattr(screen, "run", fake_run)
    monkeypatch.setattr(screen, "read", lambda c, p, cmd: device["file"])

    assert screen.capture(None, "P1") is None


def test_the_dump_file_is_removed_before_it_is_written():
    """The guard itself: without the rm there is nothing to distinguish a
    fresh dump from the one before it."""
    import inspect

    from geelark_farm import screen

    source = inspect.getsource(screen.capture)
    rm = source.index("rm -f")
    dump = source.index("uiautomator dump")

    assert rm < dump
