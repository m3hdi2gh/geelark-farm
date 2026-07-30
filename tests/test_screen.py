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
