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


def ctx(elements, *, key="K", seen=None, taps=None, tries=0):
    account = Account(email="a@x.com", password="pw", totp_secret="")
    c = g.Context(client=None, phone_id="P", account=account,
                  solver_key=key, captcha_max=3, captcha_tries=tries)
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


def test_the_words_alone_are_never_tapped_and_nothing_is_poked(monkeypatch):
    """"I'm not a robot" is also prose on that page, and tapping prose does
    nothing. With no tick box on screen the flow waits - it does not press
    NEXT under a captcha that has not been answered."""
    tapped, submitted = [], []
    monkeypatch.setattr(g.screen, "tap_element",
                        lambda client, pid, el: tapped.append(el.label))
    monkeypatch.setattr(g, "submit", lambda c: submitted.append(True))
    c = ctx([el("I'm not a robot", "[40,300][320,380]")], seen={"captcha": 1})
    assert g.act_captcha(c) is None
    assert tapped == [] and submitted == []
    assert c.captcha_tries == 0


def test_the_limit_turns_into_the_captcha_fatal():
    """Three attempts - a tick or a grid sent to the solver - and no more.
    Visits spent waiting for reCAPTCHA to answer are not attempts."""
    c = ctx([el("Confirm you're not a robot", "[0,0][1080,200]")],
            seen={"captcha": 9}, tries=3)
    out = g.act_captcha(c)
    assert out is not None and out.kind == "fatal"
    assert out.reason == "captcha_shown"


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
    assert c.captcha_tries == 1 and c.captcha_ticked

    # The next visit on the same flow: reCAPTCHA still reads unticked while
    # it decides, and tapping again unticks what the first tap ticked -
    # which is what two live builds did. Wait, and never poke with NEXT.
    tapped.clear()
    assert g.act_captcha(c) is None
    assert tapped == [] and submitted == []
    assert c.captcha_tries == 1, "waiting is not an attempt"


def _grid_ctx(**kw):
    import pathlib
    xml = pathlib.Path("tests/fixtures/google-captcha-grid.xml")
    els = screen.parse(xml.read_text(encoding="utf-8", errors="replace"))
    return ctx(els, **kw), els


def test_the_question_is_read_from_both_of_its_nodes():
    """Google splits it: "Select all images with" on one line, the object
    on the next. Reading only the first is the question without its
    subject, which is what CapSolver was handed (build 1781)."""
    c, _ = _grid_ctx()
    question = g._grid_instruction(c)
    assert question == "Select all images with crosswalks"
    from geelark_farm import capsolver
    assert capsolver.question_id(question) == "/m/014xcs"


def test_the_grid_rectangle_is_read_off_the_real_screen():
    """The tiles are pictures in a WebView and in no tree; the rectangle
    comes from the heading above them, the button row below, and the fact
    that a reCAPTCHA grid is square."""
    c, _ = _grid_ctx()
    rect = g._grid_rect(c)
    assert rect == (84, 198, 662, 776)
    # Above the button row (VERIFY starts at y=901), as it must be.
    assert rect[3] < 898
    # A 3x3 over it: the middle tile sits in the middle of the grid.
    assert g._tile_points(rect, 3, [4]) == [(373, 487)]


def test_the_real_grid_is_solved_and_its_tiles_tapped(monkeypatch):
    c, _ = _grid_ctx(seen={"captcha": 2})
    asked = {}
    monkeypatch.setattr(g, "_grab_grid_b64", lambda c, rect: "B64")
    monkeypatch.setattr("geelark_farm.capsolver.solve_grid",
                        lambda key, image, q: asked.update(q=q) or [0, 4, 8])
    tapped, submitted = [], []
    monkeypatch.setattr(g.shell, "tap",
                        lambda client, pid, x, y: tapped.append((x, y)))
    monkeypatch.setattr(g, "submit", lambda c: submitted.append(True))

    assert g.act_captcha(c) is None
    assert asked["q"] == "Select all images with crosswalks"
    assert tapped == g._tile_points((84, 198, 662, 776), 3, [0, 4, 8])
    assert submitted == [True]


def test_a_three_by_three_is_not_read_as_sixteen_tiles(monkeypatch):
    """"Click verify once there are none left" belongs to the 3x3 that
    refreshes; reading it as a 4x4 taps sixteen places on nine tiles."""
    c, _ = _grid_ctx(seen={"captcha": 1})
    monkeypatch.setattr(g, "_grab_grid_b64", lambda c, rect: "B64")
    monkeypatch.setattr("geelark_farm.capsolver.solve_grid",
                        lambda key, image, q: [8])
    tapped = []
    monkeypatch.setattr(g.shell, "tap",
                        lambda client, pid, x, y: tapped.append((x, y)))
    monkeypatch.setattr(g, "submit", lambda c: None)
    assert g.act_captcha(c) is None
    # Tile 8 of a 3x3 is the bottom-right; of a 4x4 it would be mid-left.
    assert tapped == [(565, 679)]
