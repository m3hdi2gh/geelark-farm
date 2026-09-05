"""The captcha screen: tap the checkbox, solve a grid, and give up within
a limit - all without a phone or a network."""
from __future__ import annotations

from geelark_farm import screen
from geelark_farm.accounts import Account
from geelark_farm.flows import google_login as g


def el(label, bounds, cls="TextView"):
    return screen.Element(text=label, desc="", cls=cls, resource_id="",
                          bounds=bounds, clickable=True, enabled=True,
                          focused=False, password=False)


def ctx(elements, *, key="K", seen=None, taps=None):
    account = Account(email="a@x.com", password="pw", totp_secret="")
    c = g.Context(client=None, phone_id="P", account=account,
                  solver_key=key, captcha_max=3)
    c.elements = elements
    c.blob = screen.texts(elements)
    c.seen = seen or {}
    c._taps = taps if taps is not None else []
    c.tap = lambda label: (c._taps.append(label) or True) \
        if screen.find(elements, label) else False
    return c


def test_the_tile_math_maps_indices_to_centres():
    # A 3x3 grid over [0,100]-[300,400]: 100-wide tiles.
    pts = g._tile_points((0, 100, 300, 400), 3, [0, 4, 8])
    assert pts == [(50, 150), (150, 250), (250, 350)]
    assert g._tile_points((0, 100, 300, 400), 3, [99]) == []


def test_a_captcha_is_fatal_without_a_key_and_handled_with_one():
    els = [el("Confirm you're not a robot", "[0,0][1080,200]")]
    assert g._captcha_present(ctx(els)) is True
    assert g._captcha_present(ctx(els, key="")) is False
    # Without a key, _fatal_reason still names it - unchanged behaviour.
    assert g._fatal_reason(ctx(els, key="")) == "captcha_shown"
    assert g._fatal_reason(ctx(els, key="K")) is None


def test_the_words_alone_are_never_tapped_as_a_tick_box(monkeypatch):
    """"I'm not a robot" is also prose on that page, and tapping prose does
    nothing. Only a CheckBox is ticked; with none on screen the form is
    submitted instead of poking at text."""
    tapped, submitted = [], []
    monkeypatch.setattr(g.screen, "tap_element",
                        lambda client, pid, el: tapped.append(el.label))
    monkeypatch.setattr(g, "submit", lambda c: submitted.append(True))
    c = ctx([el("I'm not a robot", "[40,300][320,380]")], seen={"captcha": 1})
    assert g.act_captcha(c) is None
    assert tapped == [] and submitted == [True]


def test_the_limit_turns_into_the_captcha_fatal():
    c = ctx([el("Confirm you're not a robot", "[0,0][1080,200]")],
            seen={"captcha": 4})     # one past captcha_max
    out = g.act_captcha(c)
    assert out is not None and out.kind == "fatal"
    assert out.reason == "captcha_shown"


def test_a_grid_is_solved_and_its_tiles_tapped(monkeypatch):
    taps = []
    els = [
        el("Select all images with traffic lights", "[0,200][1080,300]"),
        el("Verify", "[860,1400][1060,1500]", cls="Button"),
        el("frame", "[0,0][1080,1920]", cls="FrameLayout"),
    ]
    c = ctx(els, seen={"captcha": 1})
    monkeypatch.setattr(g, "_grab_screenshot_b64", lambda c: "B64")
    monkeypatch.setattr("geelark_farm.capsolver.solve_grid",
                        lambda key, image, q: [0, 8])
    monkeypatch.setattr(g, "submit", lambda c: taps.append("submit"))
    tapped = []
    monkeypatch.setattr(g.shell, "tap",
                        lambda client, pid, x, y: tapped.append((x, y)))
    assert g.act_captcha(c) is None
    # grid rect: left/right 0..1080, top 300 (instruction bottom),
    # bottom 1400 (verify top) -> a 3x3 of 360x~366.
    assert tapped == g._tile_points((0, 300, 1080, 1400), 3, [0, 8])
    assert taps == ["submit"]


def test_a_grid_that_cannot_be_placed_is_never_tapped(monkeypatch):
    # Instruction but no Verify button -> no rect -> no tap, no solve.
    c = ctx([el("Select all images with cars", "[0,200][1080,300]")],
            seen={"captcha": 1})
    called = []
    monkeypatch.setattr("geelark_farm.capsolver.solve_grid",
                        lambda *a, **k: called.append(1) or [])
    monkeypatch.setattr(g.shell, "tap",
                        lambda *a: called.append("tap"))
    assert g.act_captcha(c) is None
    assert called == [], "nothing solved, nothing tapped"


def test_the_real_checkbox_screen_is_ticked_once_then_submitted(monkeypatch):
    """From the screen a build actually met (tests/fixtures): the tick is a
    CheckBox and NEXT is a separate button. Tapping "the checkbox" twice
    unticks what the first tap ticked, so the box is only tapped while it
    is empty and the second visit submits instead."""
    import pathlib

    xml = pathlib.Path("tests/fixtures/google-captcha-checkbox.xml")
    els = screen.parse(xml.read_text(encoding="utf-8", errors="replace"))
    box = g._robot_checkbox(ctx(els))
    assert box is not None and box.clickable and not box.checked
    assert box.centre == (82, 564), "the tick box, not the words beside it"

    tapped, submitted = [], []
    c = ctx(els, seen={"captcha": 1})
    monkeypatch.setattr(g.screen, "tap_element",
                        lambda client, pid, el: tapped.append(el.centre))
    monkeypatch.setattr(g, "submit", lambda c: submitted.append(True))
    assert g.act_captcha(c) is None
    assert tapped == [(82, 564)] and submitted == []

    # Second visit, box now ticked: submit rather than untick it.
    ticked = [screen.Element(**{**el.__dict__, "checked": True})
              if el is box else el for el in els]
    c2 = ctx(ticked, seen={"captcha": 2})
    tapped.clear()
    assert g.act_captcha(c2) is None
    assert tapped == [] and submitted == [True]
