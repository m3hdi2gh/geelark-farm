"""The captcha screen: tap the checkbox, solve a grid, and give up within
a limit - all without a phone or a network."""
from __future__ import annotations

import base64
import io

import pytest

from geelark_farm import screen
from geelark_farm.accounts import Account
from geelark_farm.flows import google_login as g

#: Where the tiles sit on the real screen beside these tests, in the view
#: hierarchy's own numbers. Every test that fakes the cut uses it, so a tap
#: is always checked against a place a phone really had tiles.
SENT_AT = (47, 247, 677, 877)


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
    monkeypatch.setattr(g, "_answer",
                        lambda c, rect: submitted.append(True))
    c = ctx([el("I'm not a robot", "[40,300][320,380]")], seen={"captcha": 1})
    assert g.act_captcha(c) is None
    assert tapped == [] and submitted == []
    assert c.captcha_tries == 0


def test_the_limit_turns_into_the_captcha_fatal():
    """Three captchas in one sign-in and no more. Separate captchas, which
    is what the operator asked for - not rounds, and not visits."""
    c = ctx([el("Confirm you're not a robot", "[0,0][1080,200]")],
            seen={"captcha": 9})
    # Four: one, one after the password page, one after the 2FA page - and
    # a fourth, which is one past what this is allowed to answer.
    c.trail = ["captcha", "password_entry", "captcha", "loading", "captcha",
               "2fa_code_entry", "captcha", "dismissable", "captcha"]
    out = g.act_captcha(c)
    assert out is not None and out.kind == "fatal"
    assert out.reason == "captcha_shown"


def test_a_grid_that_cannot_be_placed_is_never_tapped(monkeypatch):
    # Instruction but no Verify button -> no rect -> no tap, no solve.
    c = ctx([el("Select all images with cars", "[0,200][1080,300]")],
            seen={"captcha": 1})
    called = []
    monkeypatch.setattr("geelark_farm.capsolver.solve_grid",
                        lambda *a, **k: called.append(1) or ([], 0))
    monkeypatch.setattr(g.shell, "tap",
                        lambda *a: called.append("tap"))
    assert g.act_captcha(c) is None
    assert called == [], "nothing solved, nothing tapped"


def test_the_real_checkbox_is_ticked_then_left_alone_then_tried_again(
        monkeypatch):
    """From the screen a build actually met (tests/fixtures): the tick is a
    CheckBox and NEXT is a separate button.

    Tapped again on the next visit it unticks what it just ticked, which is
    what two live builds did until the limit. Never tapped again at all, a
    challenge that opens and closes itself is waited out to the end of the
    budget with none of the three tries spent (phone 1815). So: tick, leave
    it alone while reCAPTCHA decides, and try once more if nothing came of
    it."""
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
    monkeypatch.setattr(g, "_answer",
                        lambda c, rect: submitted.append(True))
    assert g.act_captcha(c) is None
    assert tapped == [(82, 564)] and submitted == []
    assert c.captcha_tries == 1 and c.captcha_ticked_on == 1

    # The next visit on the same flow: reCAPTCHA still reads unticked while
    # it decides, and tapping again unticks what the first tap ticked -
    # which is what two live builds did. Wait, and never poke with NEXT.
    tapped.clear()
    assert g.act_captcha(c) is None
    assert tapped == [] and submitted == []
    assert c.captcha_tries == 1, "waiting is not an attempt"

    # Four visits on, nothing has come of it. The challenge is not going to
    # open itself, so the box is tried once more - and that is a try.
    c.seen["captcha"] = 5
    assert g.act_captcha(c) is None
    assert tapped == [(82, 564)] and submitted == []
    assert c.captcha_tries == 2


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


def test_the_window_is_read_off_the_real_screen():
    """The band the tiles are inside: below the heading's last line, above
    the challenge's button row, the screen's own width."""
    c, _ = _grid_ctx()
    assert g._grid_rect(c) == (34, 198, 690, 898)


def test_the_real_grid_is_solved_and_its_tiles_tapped(monkeypatch):
    c, _ = _grid_ctx(seen={"captcha": 2})
    asked = {}
    monkeypatch.setattr(g, "_grab_grid_b64",
                        lambda c, win, size, scan=True: ("B64", SENT_AT))
    monkeypatch.setattr("geelark_farm.capsolver.solve_grid",
                        lambda key, image, q, watch=None:
                        (asked.update(q=q) or ([0, 4, 8], 0)))
    tapped, submitted = [], []
    monkeypatch.setattr(g.shell, "tap",
                        lambda client, pid, x, y: tapped.append((x, y)))
    monkeypatch.setattr(g, "_answer",
                        lambda c, rect: submitted.append(True))

    assert g.act_captcha(c) is None
    assert asked["q"] == "Select all images with crosswalks"
    assert tapped == g._tile_points(SENT_AT, 3, [0, 4, 8])
    assert submitted == [True]


def test_a_three_by_three_is_not_read_as_sixteen_tiles(monkeypatch):
    """"Click verify once there are none left" belongs to the 3x3 that
    refreshes; reading it as a 4x4 taps sixteen places on nine tiles."""
    c, _ = _grid_ctx(seen={"captcha": 1})
    monkeypatch.setattr(g, "_grab_grid_b64",
                        lambda c, win, size, scan=True: ("B64", SENT_AT))
    monkeypatch.setattr("geelark_farm.capsolver.solve_grid",
                        lambda key, image, q, watch=None: ([8], 0))
    tapped = []
    monkeypatch.setattr(g.shell, "tap",
                        lambda client, pid, x, y: tapped.append((x, y)))
    monkeypatch.setattr(g, "_answer", lambda c, rect: None)
    assert g.act_captcha(c) is None
    # Tile 8 of a 3x3 is the bottom-right; of a 4x4 it would be mid-left,
    # which on this grid is (125, 640).
    assert tapped == [(572, 772)]


def _skip_grid_ctx(**kw):
    """The other grid shape, off a real phone (2026-09-05, build 1793): a
    one-shot 4x4 whose heading ends "If there are none, click skip"."""
    import pathlib
    xml = pathlib.Path("tests/fixtures/google-captcha-grid-skip.xml")
    els = screen.parse(xml.read_text(encoding="utf-8", errors="replace"))
    return ctx(els, **kw), els


def test_the_window_is_bounded_by_the_heading_and_the_button_row():
    """The tree cannot say where the tiles start - they are pictures in a
    WebView - only what they are between. The floor is the challenge's own
    button row, found by class and position: found by wording, "Verify"
    matched the page's `Verify it's you` heading, which sits *above* the
    grid, so the floor came out higher than the ceiling and seven grids
    went by untouched before the phone gave up (build 1793)."""
    c, _ = _skip_grid_ctx()
    assert g._grid_rect(c) == (34, 198, 690, 898)


def test_the_heading_ends_at_click_skip_as_well_as_at_click_verify():
    """Two wordings, one per grid shape. Matching only "click verify" read
    the rest of the page into the question."""
    c, _ = _skip_grid_ctx()
    assert g._grid_instruction(c) == "Select all squares with stairs"
    from geelark_farm import capsolver

    assert capsolver.question_id(g._grid_instruction(c)) == "/m/01lynh"


def test_a_grid_that_cannot_be_placed_is_saved_once_and_not_once_a_visit():
    saved = []
    c, _ = _grid_ctx()
    c.save = lambda name: saved.append(name)
    c.elements = [el("Select all images with", "[84,89][314,118]"),
                  el("crosswalks", "[84,115][662,171]")]
    for _ in range(4):
        assert g.act_captcha(c) is None
    assert saved == ["captcha_grid_unplaced"]


def test_waiting_ends_in_the_operators_captcha_error_not_the_routers_phrase():
    """A captcha that never clears used to run the screen out of visits, and
    the build was reported `stuck_on_captcha` - a phrase about the tool, not
    the answer that was asked for (builds 1793 and 1795)."""
    c, _ = _skip_grid_ctx(seen={"captcha": g.CAPTCHA_VISITS - 1})
    out = g.act_captcha(c)
    assert out is not None and out.kind == "fatal"
    assert out.reason == "captcha_shown"
    assert "never cleared" in out.detail


def _real_screen(monkeypatch, *, at=1):
    """The captcha screen a phone actually met (2026-09-05, build 1807),
    served to the flow as its screenshot. `at` scales the picture without
    touching the tree, which is how a device whose screenshot is not its
    hierarchy's own width is reproduced."""
    import pathlib

    from PIL import Image

    png = pathlib.Path("tests/fixtures/google-captcha-grid-screen.png")
    shot = Image.open(png).convert("RGB")
    if at != 1:
        shot = shot.resize((shot.width * at, shot.height * at))
    buf = io.BytesIO()
    shot.save(buf, format="PNG")
    monkeypatch.setattr(g.phones, "screenshot", lambda client, pid: "http://s")
    monkeypatch.setattr(
        "requests.get",
        lambda url, timeout: type("R", (), {"content": buf.getvalue()}))


def _screen_ctx(**kw):
    import pathlib
    xml = pathlib.Path("tests/fixtures/google-captcha-grid-screen.xml")
    els = screen.parse(xml.read_text(encoding="utf-8", errors="replace"))
    return ctx(els, **kw), els


def test_the_tiles_are_found_in_the_picture_not_measured_off_the_page(
        monkeypatch):
    """The block of photographs, in a screen a phone actually met. Measured
    off the page instead, the blue banner's own padding put the top fifty
    pixels high and the bottom a whole row short, and CapSolver would not
    read it as a grid at all (build 1807)."""
    from PIL import Image

    c, _ = _screen_ctx()
    _real_screen(monkeypatch)
    got = g._grab_grid_b64(c, g._grid_rect(c), 4)
    assert got is not None
    image, rect = got
    assert rect == SENT_AT
    sent = Image.open(io.BytesIO(base64.b64decode(image)))
    # 450 across, which is the size CapSolver reads a 4x4 from. Sent at the
    # phone's own 631 it answered `{"hasObject": false, "type": ""}` four
    # builds running; at 450 the same picture came back [13, 14, 15].
    assert sent.size == (450, 450)


def test_the_tiles_are_found_on_a_phone_whose_screenshot_is_not_its_tree(
        monkeypatch):
    """The window comes off the view hierarchy and the picture is the
    device's own. Assuming the two numbers agree searches a band of the
    wrong part of the screen."""
    c, _ = _screen_ctx()
    assert g._screen_width(c) == 720
    _real_screen(monkeypatch, at=2)
    got = g._grab_grid_b64(c, g._grid_rect(c), 4)
    assert got is not None
    # Twice the picture, the same answer to within a pixel of resampling:
    # the taps go where the tree says, not where the picture is.
    assert all(abs(a - b) <= 2 for a, b in zip(got[1], SENT_AT, strict=True))


def test_a_screen_with_no_photographs_on_it_is_not_a_grid(monkeypatch):
    """Nothing is cut out of a flat screen, and nothing is tapped on one."""
    from PIL import Image

    c, _ = _screen_ctx()
    blank = Image.new("RGB", (720, 1440), "white")
    buf = io.BytesIO()
    blank.save(buf, format="PNG")
    monkeypatch.setattr(g.phones, "screenshot", lambda client, pid: "http://s")
    monkeypatch.setattr(
        "requests.get",
        lambda url, timeout: type("R", (), {"content": buf.getvalue()}))
    assert g._grab_grid_b64(c, g._grid_rect(c), 4) is None


def test_an_answer_about_a_grid_we_did_not_send_is_left_alone(monkeypatch):
    """The size the picture goes out at is what the solver reads the shape
    from, so an answer about a different shape is about a picture nobody
    has. The same 4x4 sent at 300 came back as a 3x3, naming three tiles
    that had no crosswalk in them (2026-09-06)."""
    c, _ = _screen_ctx()
    monkeypatch.setattr(g, "_grab_grid_b64",
                        lambda c, win, size, scan=True: ("B64", SENT_AT))
    monkeypatch.setattr("geelark_farm.capsolver.solve_grid",
                        lambda key, image, q, watch=None: ([8, 4, 7], 3))
    tapped, submitted = [], []
    monkeypatch.setattr(g.shell, "tap",
                        lambda client, pid, x, y: tapped.append((x, y)))
    monkeypatch.setattr(g, "_answer",
                        lambda c, rect: submitted.append(True))
    assert g.act_captcha(c) is None
    assert tapped == [] and submitted == []


def _tiles_ctx(**kw):
    """A grid whose tiles the page is offering, off a real phone
    (2026-09-06, build 1812): sixteen buttons all called `Image
    challenge`, each with its own bounds."""
    import pathlib
    xml = pathlib.Path("tests/fixtures/google-captcha-grid-tiles.xml")
    els = screen.parse(xml.read_text(encoding="utf-8", errors="replace"))
    return ctx(els, **kw), els


def test_the_tiles_are_taken_from_the_page_when_the_page_offers_them():
    """Exact, counted, and tappable as tiles. Scanned for in the picture
    instead, this screen offered no rectangle at all - the first button
    below the heading was a tile, so the floor came out 46 pixels under the
    ceiling and ten visits went by doing nothing (build 1812)."""
    c, _ = _tiles_ctx()
    tiles = g._tile_buttons(c)
    assert len(tiles) == 16
    assert g._tiles_box(tiles) == (44, 244, 680, 879)
    # Reading order, so the solver's indices land on the right squares.
    assert g._box(tiles[0])[:2] == [44, 244]
    assert g._box(tiles[3])[:2] == [518, 244]
    assert g._box(tiles[4])[:2] == [44, 402]


def test_a_half_drawn_grid_is_not_answered():
    """Nine or sixteen. Any other number is a page mid-render."""
    c, _ = _tiles_ctx()
    c.elements = [e for e in c.elements
                  if "image challenge" not in (e.label or "").lower()][:4]
    assert g._tile_buttons(c) == []


def test_the_solvers_tiles_are_tapped_where_the_page_says_they_are(
        monkeypatch):
    c, _ = _tiles_ctx()
    monkeypatch.setattr(g, "_grab_grid_b64",
                        lambda c, box, size, scan=True: ("B64", box))
    monkeypatch.setattr("geelark_farm.capsolver.solve_grid",
                        lambda key, image, q, watch=None: ([5, 6, 9, 10], 4))
    tapped, submitted = [], []
    monkeypatch.setattr(g.screen, "tap_element",
                        lambda client, pid, el: tapped.append(el.bounds))
    monkeypatch.setattr(g, "_answer",
                        lambda c, rect: submitted.append(True))
    assert g.act_captcha(c) is None
    assert tapped == ["[202,402][362,562]", "[360,402][522,562]",
                      "[202,561][362,722]", "[360,561][522,722]"]
    assert submitted == [True]


def test_the_count_of_tiles_beats_the_wording(monkeypatch):
    """Sixteen buttons is a 4x4 whatever the heading says, and the size the
    picture goes out at is what the solver reads the shape from."""
    c, _ = _tiles_ctx()
    sent = {}
    monkeypatch.setattr(g, "_grab_grid_b64",
                        lambda c, box, size, scan=True:
                        sent.update(size=size, scan=scan) or ("B64", box))
    monkeypatch.setattr("geelark_farm.capsolver.solve_grid",
                        lambda key, image, q, watch=None: ([], 4))
    monkeypatch.setattr(g, "_answer", lambda c, rect: None)
    assert g.act_captcha(c) is None
    assert sent == {"size": 4, "scan": False}


def test_the_window_is_the_challenges_card_and_not_the_whole_screen():
    """Scanned across the whole width, a grid came out starting at x=134
    and running to the screen's own edge at 720 - there was contrast
    outside the card to find (2026-09-06, phone 1831). The frame is a
    `reCAPTCHA` View, and the page carries two: the collapsed tick-box
    widget is the other, told apart by not holding the heading."""
    c, _ = _screen_ctx()
    ask = next(g._box(e) for e in c.elements
               if "select all" in (e.label or "").lower())
    assert g._card_around(c, ask) == [34, 39, 690, 986]


def test_one_flat_column_of_tiles_does_not_move_the_grid(monkeypatch):
    """The width follows from the height, because a reCAPTCHA grid is
    square and centred in its card. Trimmed for contrast the way the rows
    are, a plain wall down the left edge takes the edge with it and every
    tap after that lands a column over."""
    from PIL import Image

    shot = Image.open("tests/fixtures/google-captcha-grid-screen.png")
    flat = shot.convert("RGB").copy()
    # Paint the leftmost column of tiles a flat grey.
    for x in range(47, 205):
        for y in range(247, 877):
            flat.putpixel((x, y), (200, 200, 200))
    c, _ = _screen_ctx()
    assert g._tiles_in(flat, g._grid_rect(c)) == SENT_AT


def test_one_captcha_that_redraws_is_still_one_captcha():
    """A challenge takes tiles away as they are answered and draws fresh
    ones in their place, going through `loading` on the way. Counting each
    of those as another captcha spent the whole limit on one page, and
    three phones in a row were failed for answering correctly (phones 1834
    to 1836)."""
    c = ctx([])
    c.trail = ["captcha"] * 4 + ["loading", "captcha", "captcha"]
    assert g._captchas_met(c) == 1
    c.trail += ["password_entry", "captcha"]
    assert g._captchas_met(c) == 2


def test_a_fresh_captcha_gets_fresh_rounds(monkeypatch):
    """The rounds a captcha is allowed are its own. Carried over, the
    second captcha of a flow would start already spent."""
    c = ctx([el("Confirm you're not a robot", "[0,0][720,200]"),
             el("I'm not a robot", "[58,541][106,588]", cls="CheckBox")],
            seen={"captcha": 6})
    c.captcha_tries, c.captcha_met, c.captcha_ticked_on = 9, 1, 5
    c.trail = ["captcha", "password_entry", "captcha"]
    monkeypatch.setattr(g.screen, "tap_element", lambda *a: None)
    assert g.act_captcha(c) is None
    assert c.captcha_met == 2
    assert c.captcha_tries == 1, "the tick of the new captcha, and no more"


def test_the_challenges_own_button_is_pressed_and_not_the_pages():
    """The sign-in page carries a NEXT of its own at the foot of the
    screen, and `submit` matches on the word. Pressed there, the answer
    never goes anywhere: phone 1839 tapped the same four tiles five rounds
    running and got the same grid back every time, because nothing had ever
    been submitted (2026-09-06).

    Nor is it the topmost button under the tiles: that row carries four
    icon buttons - another challenge, audio, liveness, help - and the
    leftmost of those is what position alone picks.
    """
    c, _ = _tiles_ctx()
    button = g._challenge_button(c, below=879)
    assert button is not None
    assert button.label == "SKIP" and button.bounds == "[510,901][676,973]"

    page_next = next(e for e in c.elements if e.label == "NEXT")
    assert page_next.bounds == "[543,1264][687,1342]", "the page's own"


def test_a_grid_that_never_finishes_drawing_is_not_waited_on_forever():
    """Waited on without a bound, a grid part-way through drawing took
    twenty-four visits and the phone with it (phone 1839)."""
    c, _ = _tiles_ctx()
    half = [e for e in c.elements
            if "image challenge" not in (e.label or "").lower()]
    tiles = [e for e in c.elements
             if "image challenge" in (e.label or "").lower()][:5]
    c.elements = half + tiles
    for i in range(g._DRAW_WAIT):
        assert g.act_captcha(c) is None
        assert c.captcha_waited == i + 1, "waiting, and counting it"
    # Past the wait it stops waiting and goes at the picture instead.
    assert c.captcha_waited == g._DRAW_WAIT


def test_the_button_is_read_from_the_screen_as_it_is_after_the_taps(
        monkeypatch):
    """The button row sits under the tiles and moves with them - one
    challenge showed it at y=937 and at y=988 on two rounds - and its word
    changes from SKIP to VERIFY as soon as a tile is taken."""
    c, _ = _tiles_ctx()
    moved = [e for e in c.elements if e.label != "SKIP"]
    moved.append(el("VERIFY", "[510,951][676,1023]", cls="Button"))

    def refresh():
        c.elements = moved

    c.refresh = refresh
    tapped = []
    monkeypatch.setattr(g.screen, "tap_element",
                        lambda client, pid, e: tapped.append(e.bounds))
    g._answer(c, (44, 244, 680, 879))
    assert tapped == ["[510,951][676,1023]"], "where the button is now"


def test_a_stop_is_felt_inside_the_captcha_act_not_only_between_screens(
        monkeypatch):
    """One captcha act can spend six and a half minutes in a single turn -
    a screenshot poll of a minute, a download of another, and three tries
    at the solver at ninety seconds each - and the router does not come
    back round until it ends. A phone somebody had stopped kept answering
    the captcha through all of it (2026-09-06)."""
    class Stopped(Exception):
        pass

    c, _ = _screen_ctx()
    c.watch = lambda: (_ for _ in ()).throw(Stopped)
    looked = []
    monkeypatch.setattr(g.phones, "screenshot",
                        lambda client, pid: looked.append(1) or "http://s")
    with pytest.raises(Stopped):
        g._grab_grid_b64(c, g._grid_rect(c), 4)
    assert looked == [], "stopped before the minute-long poll, not after it"


def test_the_solver_is_asked_to_stop_between_its_tries(monkeypatch):
    """Three tries at ninety seconds is four and a half minutes in one
    call, and it is the longest single wait in the flow."""
    from geelark_farm import capsolver

    class Stopped(Exception):
        pass

    seen = []

    def watch():
        seen.append(1)
        if len(seen) == 2:
            raise Stopped

    class Flaky:
        @staticmethod
        def post(url, json=None, timeout=None):
            raise RuntimeError("no route to host")

    with pytest.raises(Stopped):
        capsolver.solve_grid("K", "b", "cars", session=Flaky(), watch=watch)
    assert seen == [1, 1], "asked before each try, and stopped on the second"
