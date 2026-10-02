"""The admin rail (2026-10-02): every console page and the admin's
Station, folding to its icons, from the prototype the operator approved.

What is worth holding: it is an admin's and nobody else's, on the
console and on the Station alike; its fold is on <html> before the first
paint, so a folded rail never flashes open; below 1280px an open rail
lies over the page rather than squeezing the Station, which sizes itself
by the window; and it carries the console's name, not a logo - every
page under it carries one.
"""
from __future__ import annotations

import re
from pathlib import Path

from geelark_farm.web import assets, pages, station_pages

STATIC = Path(pages.__file__).resolve().parent / "static"
ADMIN = {"id": 1, "username": "mehdi", "role": "admin", "csrf": "c5rf",
         "sees": "all", "user_admin": True,
         "nav": {"gmail": 3, "proxy": 2, "app": 1, "pending": 0, "needs": 0}}
OPERATOR = {"id": 2, "username": "sara", "role": "operator", "csrf": "c",
            "sees": "own", "nav": {}}


def _state() -> dict:
    from tests.test_station_pages import _state as station_state

    return station_state()


def _rule(head: str) -> str:
    """The first rule of rail.css that starts with `head`, to its brace."""
    rail = assets.RAIL_CSS
    at = rail.index(head)
    return rail[at:rail.index("}", at)]


# ------------------------------------------------------------ who gets it
def test_the_rail_is_an_admins_on_the_console_and_nobody_elses():
    assert '<nav class="rail-nav"' in pages.page("x", "", user=ADMIN)
    assert "<nav" not in pages.page("x", "", user=OPERATOR)
    assert "<nav" not in pages.page("x", "", user=None), "the sign-in page"
    assert "<nav" not in pages.page("x", "", user=ADMIN, bare=True), \
        "the Live tab is the phone's, edge to edge"
    assert pages.rail(OPERATOR) == "" and pages.rail(None) == ""


def test_the_rail_is_on_an_admins_station_and_not_on_an_operators():
    admin = station_pages.station_page(_state(), ADMIN)
    rail = admin[admin.index('<nav class="rail-nav"'):admin.index("</nav>")]
    assert 'href="/station" class="rail-link here" aria-current="page"' in rail
    # The rail sits beside a column that holds the bar and the page.
    assert admin.index('<div class="rail-shell">') < admin.index("<nav") \
        < admin.index('<div class="rail-col">') < admin.index('id="bar"') \
        < admin.index("</main></div></div>")

    operator = station_pages.station_page(_state(), dict(ADMIN,
                                                         role="operator"))
    for word in ("<nav", "rail-shell", "rail-col", "gf-rail"):
        assert word not in operator, word


def test_the_station_holds_the_rail_while_its_dialog_is_up():
    script = (STATIC / "station.js").read_text(encoding="utf-8")
    at = script.index("function hold(on){")
    assert "$('gf-rail')" in script[at:at + 300], (
        "the page under the Build dialog cannot be reached - nor the rail")


# ------------------------------------------------------------ what it says
def test_the_rail_carries_the_consoles_name_and_no_logo():
    drawn = pages.rail(ADMIN, "/pools/gmail")
    assert '<span class="rail-title"><b>Admin</b> console</span>' in drawn
    for logo in ("<img", "brandmark", "linearGradient", "IranSpoty"):
        assert logo not in drawn, logo
    assert 'aria-label="Admin console"' in drawn


def test_the_rail_lights_its_page_and_shows_the_counts():
    drawn = pages.rail(ADMIN, "/pools/gmail")
    assert drawn.count('aria-current="page"') == 1
    assert ('<a href="/pools/gmail" class="rail-link here" '
            'aria-current="page">') in drawn
    assert '<span class="rail-n">3</span>' in drawn
    assert '<span class="rail-n hot">1</span>' in drawn, "the GPT pool"
    # Every word is a label that can fly out beside its icon when folded.
    assert drawn.count('<span class="rail-lbl">') == drawn.count('<a href=')


def test_who_sees_which_pages_is_unchanged():
    narrow = dict(ADMIN, sees="own", user_admin=False)
    drawn = pages.rail(narrow)
    for path in ("/events", "/needs", "/logins", "/users"):
        assert f'href="{path}"' not in drawn, path
    assert 'href="/users"' in pages.rail(ADMIN)


def test_the_way_out_is_a_real_form_with_its_token():
    drawn = pages.rail(ADMIN)
    assert re.search(r'<form class="rail-foot" method="post" action="/logout">'
                     r'.*<input type="hidden" name="csrf" value="c5rf">'
                     r'<button class="rail-out" type="submit" '
                     r'aria-label="Log out"', drawn)


# --------------------------------------------------------------- the fold
def test_the_fold_is_on_html_before_the_rail_is_drawn():
    """A rail folded on the last page would flash open on this one if its
    state waited for a deferred script."""
    drawn = pages.rail(ADMIN)
    boot, wire = drawn.index(pages._RAIL_BOOT), drawn.index(pages._RAIL_WIRE)
    assert boot == 0, "first, before the dock"
    assert boot < drawn.index('<div class="rail-dock">') < wire
    assert "localStorage.getItem('gf-rail')" in pages._RAIL_BOOT
    assert "setAttribute('data-rail','icons')" in pages._RAIL_BOOT
    assert "innerWidth<1280" in pages._RAIL_BOOT, (
        "below 1280px it always starts folded; open, it would cover the page")


def test_the_fold_button_says_what_it_does():
    drawn = pages.rail(ADMIN)
    assert re.search(r'<button class="rail-fold" id="gf-rail-fold" '
                     r'type="button" aria-controls="gf-rail" '
                     r'aria-expanded="true" aria-label="Fold the menu"', drawn)
    assert 'id="gf-rail"' in drawn, "what the button controls"
    for said in ("'Open the menu'", "'Fold the menu'", "aria-expanded"):
        assert said in pages._RAIL_WIRE, said
    # An opening is remembered only where the rail can stay open beside
    # the page; Esc folds one that lies over it.
    assert "innerWidth>=1280" in pages._RAIL_WIRE
    assert "e.key==='Escape'" in pages._RAIL_WIRE


# ------------------------------------------------------------- the scroll
def test_the_list_scrolls_with_the_consoles_own_scrollbar():
    """On a window shorter than the list the rail scrolled with the
    console's thin bar on its pages and with the browser's wide grey one
    on the Station, which never loads console.css (the operator,
    2026-10-02). The rail carries the console's rule itself now."""
    console = (STATIC / "console.css").read_text(encoding="utf-8")
    line2 = re.search(r"--line2:(#[0-9a-f]{6})", console).group(1)
    assert ("*{scrollbar-width:thin;scrollbar-color:var(--line2) transparent}"
            in console), "the console's own rule, which the rail copies"
    links = _rule(".rail-links{")
    assert "overflow-y:auto" in links and "overflow-x:hidden" in links
    assert f"scrollbar-width:thin;scrollbar-color:{line2} transparent" in links


def test_the_folded_strip_scrolls_and_its_words_still_fly_out():
    """Folded, the strip did not scroll: it let each word fly out past its
    edge, so on a short window the last icon lay over the avatar and the
    links under it could not be reached (the operator, 2026-10-02). It
    scrolls with no bar now, and each word is a fixed box the wire script
    stands beside the link under the pointer or the focus."""
    above_the_phone = assets.RAIL_CSS.split("@media (max-width:900px)")[0]
    assert "overflow:visible" not in above_the_phone
    assert "scrollbar-width:none" in _rule(
        'html[data-rail="icons"] .rail-links{')
    word = _rule('html[data-rail="icons"] .rail-lbl{')
    assert "position:fixed" in word
    assert "left:var(--rail-x" in word and "top:var(--rail-y" in word
    for said in ("'mouseover'", "'focusin'", "'--rail-x'", "'--rail-y'"):
        assert said in pages._RAIL_WIRE, said


def test_the_folded_strip_holds_its_links_without_a_sideways_scroll():
    """Inside its 1px border the strip is 67px: 12 + a 44px link + 11.
    With 12 on both sides the link poked a pixel past the edge and the
    strip could slide sideways (measured in the pane, 2026-10-02)."""
    assert "padding:14px 12px" in _rule(".rail-links{")
    assert "padding-right:11px" in _rule('html[data-rail="icons"] .rail-links{')
    assert "width:44px" in _rule('html[data-rail="icons"] .rail-link{')


def test_the_lit_link_is_brought_into_view():
    """The last links lie below the fold of a short window, so their own
    page lit a link nobody could see."""
    assert "l.querySelector('.here')" in pages._RAIL_WIRE
    assert "l.scrollTop+=" in pages._RAIL_WIRE


# -------------------------------------------------------------- the rules
def test_the_rails_rules_reach_both_documents():
    rail = (STATIC / "rail.css").read_text(encoding="utf-8")
    assert assets.RAIL_CSS == rail
    assert assets.CSS.endswith("\n" + rail)
    assert assets.STATION_CSS.endswith("\n" + rail)
    console = (STATIC / "console.css").read_text(encoding="utf-8")
    assert not re.search(r"(^|[},\s])nav[\s{.:]", console), (
        "the old rail's rules are gone from the console's own file")


def test_an_open_rail_lies_over_the_page_below_1280px():
    """The Station sizes itself by the window, not by the room beside the
    rail: an open rail on a 1100px window pushed its bar 112px past the
    edge (measured 2026-10-02)."""
    rail = assets.RAIL_CSS
    narrow = rail[rail.index("@media (max-width:1279px){"):]
    narrow = narrow[:narrow.index("}\n}") + 3]
    assert ".rail-dock{width:68px}" in narrow
    assert "box-shadow" in narrow
    assert '.rail-nav{position:absolute;' in rail, \
        "the rail is laid over its dock"


def test_no_breakpoint_pairs_a_minimum_with_a_maximum():
    """At 125% scaling a 1001px window measures 1000.8px and falls between
    a max-width:1000 and a min-width:1001 (the Station learned it)."""
    rail = assets.RAIL_CSS
    assert "min-width:" not in "".join(re.findall(r"@media[^{]*", rail))
    assert "@media (max-width:900px){" in rail, "the phone's top bar"


def test_on_a_phone_the_way_out_follows_the_last_link():
    """At 900px and below the rail is the old top bar. Its links sit in a
    box of their own, so the way out stood beside the whole block, halfway
    down an eight-row bar at 375px (seen in the pane, 2026-10-02)."""
    rail = assets.RAIL_CSS
    phone = rail[rail.index("@media (max-width:900px){"):]
    assert ('.rail-links,html[data-rail="icons"] .rail-links{display:contents}'
            in phone)


def test_the_rail_and_the_dashboards_rail_keep_their_own_rules():
    """The dashboard's row of pool cards is .rail (console.css). The admin
    rail was <nav class="rail"> on its first draw and each took the other's
    rules: the pool row lay over the folded rail, 68px wide, on top of the
    phones table (caught in the pane, 2026-10-02)."""
    rules = re.sub(r"/\*.*?\*/", "", assets.RAIL_CSS, flags=re.S)
    assert not re.search(r"\.rail(?![-\w])", rules), (
        "a bare .rail rule reaches the dashboard's pool row")
    drawn = pages.rail(ADMIN, "/pools/gmail")
    classes = {word for value in re.findall(r'class="([^"]*)"', drawn)
               for word in value.split()}
    assert "rail" not in classes, "the console's .rail rules reach the menu"
    # The only words without the prefix are each styled under a parent
    # the rail is not in, or not at all outside rail.css.
    assert {c for c in classes if not c.startswith("rail-")} <= {
        "here", "hot", "fold-chev"}


def test_every_line_of_the_rules_keeps_its_quotes_balanced():
    for line in assets.RAIL_CSS.splitlines():
        assert line.count("'") % 2 == 0 and line.count('"') % 2 == 0, line
