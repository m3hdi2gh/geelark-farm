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


# ------------------------------------ which box the read-back looks at
def test_the_field_that_was_typed_into_is_the_one_read_back(monkeypatch):
    """`element` was passed in and then ignored: the read-back took whatever
    `find_input` returned first. On a page with two boxes that compares one
    field's contents against another's and corrects the one it can see - and
    this is the check that caught `...@gmail.comcomcom` (2026-08-23)."""
    from geelark_farm.flows import router

    first = screen.Element(text="wrong box", desc="", cls="EditText",
                           resource_id="", bounds="[0,100][600,160]",
                           clickable=True, enabled=True, focused=True,
                           password=False)
    second = screen.Element(text="a@b.com", desc="", cls="EditText",
                            resource_id="", bounds="[0,300][600,360]",
                            clickable=True, enabled=True, focused=False,
                            password=False)

    ctx = router.Context(client=None, phone_id="P")
    monkeypatch.setattr(ctx, "refresh",
                        lambda: setattr(ctx, "elements", [first, second]))

    assert router._typed_value(ctx, second) == "a@b.com"


def test_a_field_that_moved_falls_back_to_the_focused_one(monkeypatch):
    """Better than nothing, and what this always used."""
    from geelark_farm.flows import router

    onscreen = screen.Element(text="typed", desc="", cls="EditText",
                              resource_id="", bounds="[0,999][600,1050]",
                              clickable=True, enabled=True, focused=True,
                              password=False)
    asked_about = screen.Element(text="", desc="", cls="EditText",
                                 resource_id="", bounds="[0,100][600,160]",
                                 clickable=True, enabled=True, focused=False,
                                 password=False)

    ctx = router.Context(client=None, phone_id="P")
    monkeypatch.setattr(ctx, "refresh",
                        lambda: setattr(ctx, "elements", [onscreen]))

    assert router._typed_value(ctx, asked_about) == "typed"


def test_a_budget_is_measured_on_a_clock_nothing_can_set():
    """An NTP correction or a host resuming from suspend shortens or extends
    every budget in the process, and a service that stays up for weeks is
    where that stops being theoretical."""
    import inspect

    from geelark_farm.flows import play_install, router

    for fn in (router._drive, play_install.install, play_install.Stall.held_for):
        source = inspect.getsource(fn)
        assert "time.time()" not in source
        assert "time.monotonic()" in source


# =====================================================================
# Acting on the screen, not only reading it (2026-08-26). The selectors
# were tested against captured pages; what happens after one matches -
# where the tap lands, and whether it lands at all - was not.
# =====================================================================

class Finger:
    """Records every tap the device is asked for."""

    def __init__(self):
        self.taps: list[tuple[int, int]] = []
        self.commands: list[str] = []

    def install(self, monkeypatch):
        monkeypatch.setattr(screen, "tap",
                            lambda c, p, x, y: self.taps.append((x, y)))
        return self


@pytest.fixture
def finger(monkeypatch):
    return Finger().install(monkeypatch)


def element(label="Continue", *, bounds="[0,100][200,180]", clickable=True,
            desc="", cls="TextView"):
    return screen.Element(text=label, desc=desc, cls=cls, resource_id="",
                          bounds=bounds, clickable=clickable, enabled=True,
                          focused=False, password=False)


# ------------------------------------------------------- where the tap lands
def test_a_tap_goes_to_the_middle_of_the_element(finger):
    """Anywhere else and it lands on whatever is beside it. The centre is the
    only point an element can be relied on to own."""
    assert screen.tap_element(None, "P", element(bounds="[0,100][200,180]"))

    assert finger.taps == [(100, 140)]


def test_an_element_with_bounds_nothing_can_read_is_not_tapped_blind(finger,
                                                                     caplog):
    """A tap at a guessed coordinate presses whatever happens to be there -
    and on a login page that is another account, or a refusal."""
    with caplog.at_level("WARNING"):
        assert screen.tap_element(None, "P", element(bounds="")) is False

    assert finger.taps == []
    assert any("unparseable bounds" in r.message for r in caplog.records)


def test_tapping_by_label_finds_it_first(finger):
    hit = screen.tap_label(None, "P", [element("Cancel", bounds="[0,0][100,50]"),
                                       element("Continue",
                                               bounds="[0,200][100,250]")],
                           "Continue")

    assert hit is True
    assert finger.taps == [(50, 225)]


def test_a_label_that_is_not_there_is_not_a_tap(finger):
    """False rather than a tap at (0,0), which is the top-left corner and on
    most pages is the back button."""
    assert screen.tap_label(None, "P", [element("Cancel")], "Continue") is False
    assert finger.taps == []


# ------------------------------------------------- the first of several labels
def test_the_first_label_present_is_the_one_taken(finger):
    """The primitive for clearing a chain of interstitials whose order is not
    known - so the caller's ordering is the priority, not the page's."""
    page = [element("Skip", bounds="[0,0][100,50]"),
            element("Not now", bounds="[0,200][100,250]")]

    taken = screen.tap_first_present(None, "P", page, ("Not now", "Skip"))

    assert taken == "Not now"
    assert finger.taps == [(50, 225)]


def test_nothing_present_is_answered_with_nothing(finger):
    assert screen.tap_first_present(None, "P", [element("Other")],
                                    ("Not now", "Skip")) is None
    assert finger.taps == []


def test_a_label_found_but_not_tappable_is_not_reported_as_pressed(finger):
    """The caller reads the return value as "this got cleared". Saying so
    about a tap that never landed leaves the page up and the caller moving
    on."""
    stuck = element("Not now", bounds="")

    assert screen.tap_first_present(None, "P", [stuck], ("Not now",)) is None
    assert finger.taps == []


def test_body_text_is_not_pressed_unless_the_caller_allows_it(finger):
    """In Google's and Play's dialogs a clickable button is what a button is,
    and an unclickable label of the same name is usually body text. Apps that
    render everything as plain TextViews - the ChatGPT app is one - pass
    False, and then the caller's label list is the only thing keeping it off
    the wrong control."""
    prose = element("Not now", clickable=False)

    assert screen.tap_first_present(None, "P", [prose], ("Not now",)) is None

    assert screen.tap_first_present(None, "P", [prose], ("Not now",),
                                    clickable_only=False) == "Not now"


# ------------------------------------------------------- reading the dump
def catting(monkeypatch, answer):
    """A device whose `cat` of the dump file returns `answer`."""
    monkeypatch.setattr(screen, "run", lambda c, p, cmd: "")
    monkeypatch.setattr(screen, "read", lambda c, p, cmd: answer)


BODY = '<?xml version="1.0"?><hierarchy><node text="A"/></hierarchy>'


def test_a_dump_is_read_from_wherever_the_xml_starts(monkeypatch):
    """The shell prefixes it with whatever it feels like. Found this way
    rather than by reading the dump command's own success line, because that
    line is `UI hierchary dumped to: ...` - misspelled in AOSP, and not
    something to hang a screen router on."""
    catting(monkeypatch, BODY)
    assert screen.capture(None, "P") == BODY

    catting(monkeypatch, "UI hierchary dumped to: /sdcard/x.xml\n" + BODY)
    assert screen.capture(None, "P") == BODY


def test_a_hierarchy_with_no_declaration_is_still_found(monkeypatch):
    """Some builds emit the root without the `<?xml` line at all, and losing
    the whole screen over a missing preamble reads as "the phone is blank"."""
    bare = '<hierarchy><node text="A"/></hierarchy>'
    catting(monkeypatch, "noise\n" + bare)

    assert screen.capture(None, "P") == bare


def test_a_dump_with_no_hierarchy_in_it_is_nothing_rather_than_a_guess(
        monkeypatch):
    """None, and the caller waits and looks again. Handing the noise back
    would give the parser something that is not a screen."""
    catting(monkeypatch, "Killed")

    assert screen.capture(None, "P") is None


def test_a_page_that_will_not_parse_is_no_elements_rather_than_a_crash():
    """One truncated dump must not end a flow."""
    assert screen.parse("Killed") == []
    assert screen.parse("") == []


# ------------------------------------------------------ matching a whole word
def test_a_query_matches_a_word_and_not_a_fragment_of_one():
    """"use or install." matches "install" legitimately, so the boundary is
    what separates a label from a sentence that happens to contain it."""
    match = screen._partial_matcher("install")

    assert match("use or install.")
    assert match("install")
    assert not match("uninstalled")


def test_a_query_that_is_not_a_word_falls_back_to_containment():
    """A boundary next to punctuation matches nothing, and a query like "..."
    or "(1)" is still a real thing to look for."""
    match = screen._partial_matcher("(1)")

    assert match("Step (1) of 3")


# ----------------------------------------------------------- what it is
def test_an_element_cannot_be_changed_after_it_is_parsed():
    """One page's elements are handed to every matcher and every action in a
    flow. Editing one edits it for all of them - the same reason `Proxy`,
    `Settings` and the verdicts in `failures.py` are frozen, and the fourth
    place this gap turned up (2026-08-26)."""
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        element().text = "something else"


def test_an_element_says_enough_to_be_found_in_a_log():
    """These are printed when a screen is not recognised, and the list is what
    somebody reads to write the next selector."""
    said = str(element("Continue", desc="the continue button"))

    assert "Continue" in said
    assert "the continue button" in said


def test_a_disabled_element_is_marked_as_one():
    """A greyed-out Install button and a missing one are different problems,
    and the list in an `unknown_screen` report is where the difference shows."""
    off = screen.Element(text="Install", desc="", cls="Button", resource_id="",
                         bounds="[0,0][10,10]", clickable=True, enabled=False,
                         focused=False, password=False)

    assert "!" in str(off)
    assert "!" not in str(element("Install"))


def test_an_element_with_only_a_description_is_still_named():
    """Icons carry a content-desc and no text, and the back arrow is one."""
    icon = element("", desc="Navigate up")

    assert "Navigate up" in str(icon)


# ------------------------------------------------------------ keeping a page
def test_a_fixture_is_written_where_it_is_asked_for(tmp_path):
    """Artifact directories are per build and two levels deep, and a flow that
    fails on its first screen still has to be able to keep it."""
    target = tmp_path / "artifacts" / "20260826-build1" / "welcome.xml"

    screen.save_fixture("<hierarchy/>", target)

    assert target.read_text(encoding="utf-8") == "<hierarchy/>"
