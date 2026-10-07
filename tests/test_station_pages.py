"""The Station's two documents, its stylesheet and its script (WP-D).

The behaviour of `station.js` is tested by running it
(`tests/dash/station.test.mjs`); what is here is what a document must carry
for that script to find its footing, what it must never carry, and the
rules a substring can check honestly.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import tokenize

import pytest

from geelark_farm.web import assets, station_pages

STATIC = pathlib.Path(assets.__file__).resolve().parent / "static"
PACKAGE = STATIC.parents[1]
USER = {"id": 7, "username": "sara", "role": "admin", "csrf": "c5rf-token"}


def _state(**over) -> dict:
    state = {
        "v": 1, "rev": "abc", "now": 1790000000000, "hold_minutes": 60,
        "late_minutes": 15,
        "me": {"id": 7, "name": "Sara", "user": "sara", "initial": "S",
               "since": "since 12 Sep 2026", "pw": "Changed 12 days ago",
               "daypart": "morning"},
        "may": {"take": True, "ip": True, "build": True},
        "shelves": {"gpt": {"ready": 1, "building": 0, "eta_at": None,
                            "etas": [], "late": 0, "typical_min": 6,
                            "line": None},
                    "spotify": {"ready": 0, "building": 0, "eta_at": None,
                                "etas": [], "late": 0, "typical_min": 7,
                                "line": None}},
        "tally": {"done": 0, "decline": 0, "or": 0, "auth": 0, "failed": 0,
                  "all": 0},
        "phones": [], "builds": [], "today": [], "notes": [],
        "build_form": {"gmails_left": 3,
                       "free_ips": {"gpt": 1, "spotify": 1, "other": 2},
                       "stopped": False,
                       "typical_min": {"gpt": 6, "spotify": 7, "other": 6}},
    }
    state.update(over)
    return state


def _live(**over) -> dict:
    live = {"v": 1, "rev": "abc", "now": 1790000000000, "serial": "5073",
            "lane": "gpt", "conn": "off", "exit": "PC2",
            "taken_at": 1789999000000, "tab_seen": True, "bare": False,
            "gmail": "a@gmail.com", "pw": "p", "totp": "", "acct": None,
            "may": {"take": True, "ip": True}, "last": None, "why": "",
            "viewer": {"w": 360, "box_w": 416, "box_h": 752, "bar": 32}}
    live.update(over)
    return live


def _island(body: str) -> dict:
    found = re.findall(
        r'<script type="application/json" id="gf-state">(.*?)</script>',
        body, flags=re.S)
    assert len(found) == 1, "one island, closed by its own </script>"
    return json.loads(found[0])


# ------------------------------------------------------------- documents
def test_the_station_document_is_its_own_shell():
    body = station_pages.station_page(_state(), USER)

    assert "<title>IranSpoty Station</title>" in body
    assert body.count(assets.STATION_CSS_PATH) == 1
    assert body.count(assets.STATION_JS_PATH) == 1
    assert f'<link rel="stylesheet" href="{assets.STATION_CSS_PATH}">' in body
    assert f'<script src="{assets.STATION_JS_PATH}" defer></script>' in body
    assert assets.CSS_PATH not in body, "never the console's stylesheet"
    assert assets.JS_PATH not in body, "never the console's script"
    assert 'name="gf-live"' not in body
    # An admin's Station wears the console's rail beside it; an
    # operator's has none, and no column either (2026-10-02).
    assert body.count('<nav class="rail-nav"') == 1
    assert '<div class="rail-shell">' in body and 'href="/station"' in body
    op = station_pages.station_page(_state(), dict(USER, role="operator"))
    assert "<nav" not in op and "rail-shell" not in op and "rail-col" not in op
    assert body.count('id="brandgrad"') == 1
    assert re.search(r'<form class="out-form" method="post" action="/logout">'
                     r'<input type="hidden" name="csrf" value="c5rf-token">',
                     body)
    assert 'id="gf-csrf" name="csrf" value="c5rf-token"' in body
    assert 'data-page="station"' in body
    assert "{{" not in body
    assert '<template id="gf-icons">' in body
    for name in ("station_page.html", "station_live.html"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert text.count("{{STATE}}") == 1, name


def test_the_station_document_names_the_person_and_escapes_it():
    me = dict(_state()["me"], name='<b>"Mo"</b>', initial="<")
    body = station_pages.station_page(_state(me=me), USER)
    assert '<b data-me="name">&lt;b&gt;&quot;Mo&quot;&lt;/b&gt;</b>' in body
    assert 'data-me="initial">&lt;</span>' in body


def test_the_first_paint_json_cannot_close_its_script():
    nasty = '</script><b>'
    state = _state(me=dict(_state()["me"], name=nasty, user="{{CSRF}}"),
                   notes=[{"id": "e1", "text": "a\u2028b\u2029c", "tone": "gpt"}])
    body = station_pages.station_page(state, USER)
    assert "</script><b>" not in body
    # The page's own two, and an admin's rail's two (2026-10-02).
    assert body.count("</script>") == 4, "the page's two and the rail's two"
    back = _island(body)
    assert back["me"]["name"] == nasty
    assert back["me"]["user"] == "{{CSRF}}", "a brace in the state is not a placeholder"
    assert back["notes"][0]["text"] == "a\u2028b\u2029c"
    assert "\u2028" not in body and "\u2029" not in body


def test_the_live_tab_document_frames_nothing_until_the_script_does():
    body = station_pages.live_page(_live(serial='50"73', lane="evil"), USER)
    assert re.search(r'<iframe id="lt-view" hidden title="Phone 50&quot;73"', body)
    assert not re.search(r'<iframe[^>]*\bsrc=', body), (
        "no src until the script sets one")
    assert '<section class="lt gpt" id="live"' in body, "the lane is coerced"
    assert '<aside class="lt-side gpt" id="lt-side"' in body
    assert 'data-serial="50&quot;73"' in body
    assert "<title>Phone 50&quot;73 · IranSpoty Station</title>" in body
    assert assets.CSS_PATH not in body and assets.JS_PATH not in body
    assert body.count('id="brandgrad"') == 1
    assert "{{" not in body
    for lane in ("gpt", "spotify", "other"):
        page = station_pages.live_page(_live(lane=lane), USER)
        assert f'<section class="lt {lane}" id="live"' in page
    assert _island(body)["serial"] == '50"73'


# ------------------------------------------------------------- the script
_SHARED = {"said", "gf-csrf", "gf-state", "gf-icons"}
_LIVE_IDS = {"live", "lt-stage", "lt-col", "lt-bar", "lt-box", "lt-view",
             "lt-screen", "lt-tools", "lt-side"}


def test_every_id_the_script_reads_is_in_the_document():
    js = assets.STATION_JS
    ids = set(re.findall(r"\$\('([a-z-]+)'\)", js))
    ids |= set(re.findall(r"getElementById\('([a-z-]+)'\)", js))
    # The ones the script builds from a word.
    station_made = {f"t-{v}" for v in ("done", "decline", "or", "auth", "failed")}
    station_made |= {"take-gpt", "take-spotify", "f-gmail", "f-acct", "f-ip"}
    station = station_pages.station_page(_state(), USER)
    live = station_pages.live_page(_live(), USER)
    assert ids, "the scan found nothing - the regex is wrong"
    for name in sorted(ids | station_made):
        docs = ([station, live] if name in _SHARED
                else [live] if name in _LIVE_IDS else [station])
        for doc in docs:
            assert doc.count(f'id="{name}"') == 1, name
    assert _LIVE_IDS <= ids, "every live id is one the script reads"


def test_the_station_script_builds_no_markup_from_strings():
    js = assets.STATION_JS
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                   "document.write", "eval(", "new Function"):
        assert banned not in js, banned
    assert js.count("fetch(") == 1, "api() is the one door"
    assert js.count("sendBeacon(") == 1
    assert js.count("new EventSource('/live')") == 1
    paths = re.findall(r"\bapi\('([^']*)'", js)
    assert paths
    for path in paths:
        assert path.startswith(("/station/", "/phones/", "/wishes/",
                                "/clienterror")), path


def test_the_station_script_has_nothing_in_it_that_is_never_read():
    """The dead-`var` scan the console's script has, on this one."""
    bare = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group()),
                  assets.STATION_JS, flags=re.S)
    bare = re.sub(r"//[^\n]*", lambda m: " " * len(m.group()), bare)
    bare = re.sub(r"'(?:[^'\\\n]|\\.)*'",
                  lambda m: "'" + " " * (len(m.group()) - 2) + "'", bare)
    bare = re.sub(r'"(?:[^"\\\n]|\\.)*"',
                  lambda m: '"' + " " * (len(m.group()) - 2) + '"', bare)
    closes, stack = {}, []
    for i, ch in enumerate(bare):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            closes[stack.pop()] = i
    opens = sorted(closes)
    dead = []
    for hit in re.finditer(r"\bvar\s+([A-Za-z_$][\w$]*)\s*=", bare):
        name, at = hit.group(1), hit.start()
        block = None
        for o in opens:
            if o < at < closes[o] and (block is None or o > block):
                block = o
        span = bare[block:closes[block]] if block is not None else bare
        if len(re.findall(rf"\b{re.escape(name)}\b", span)) == 1:
            dead.append((name, bare[:at].count("\n") + 1))
    assert not dead, f"worked out and never read: {sorted(dead)}"


def test_the_station_script_parses(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the script is unchecked here")
    path = tmp_path / "station.js"
    path.write_text(assets.STATION_JS, encoding="utf-8")
    done = subprocess.run([node, "--check", str(path)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr.strip()


def test_the_icons_hold_every_name_the_script_clones():
    icons = (STATIC / "station_icons.html").read_text(encoding="utf-8")
    names = set(re.findall(r'data-i="([a-z-]+)"', icons))
    wanted = {"tick", "cross", "ban", "lock", "alert", "power", "back", "copy",
              "ok", "globe", "reload", "arrow", "clock", "rim", "logo-gpt",
              "logo-spotify", "logo-other"}
    assert wanted <= names
    for lane in ("gpt", "spotify", "other"):
        assert re.search(rf'<svg data-i="logo-{lane}" class="logo"', icons)
    used = set(re.findall(r"icon\('([a-z-]+)'\)", assets.STATION_JS))
    assert used <= names, used - names
    assert icons.startswith('<template id="gf-icons">')
    assert "<script" not in icons


# ------------------------------------------------------------ stylesheet
_KEPT = [
    ":root", ".bar", ".bar.tight", ".brand", ".bn", ".day", ".day .ring .seg",
    ".takes", ".take", ".take.spotify", ".take.soon", ".take.build",
    ".take[disabled]", ".who", ".who .me", ".bench", ".col", ".col.spotify",
    ".col.other", ".col.active", ".col.out", ".stamp", ".col.arrive",
    ".col.settled", ".spark", ".hd", ".app", ".app.other", ".st", ".hd .ip",
    ".hd .age", ".hold", ".open", ".steps", ".step", ".step .v.code.roll",
    ".step .timer", ".step.none", ".verdict", ".verdict .armed .rim circle",
    ".openrow", ".doors", ".col.ghost", ".g-orb", ".g-ring", ".g-eta",
    ".g-bar", ".g-chips span", ".g-off", ".empty", ".e-mark", ".said",
    ".said.no", ".scrim", ".dlg", ".kinds", ".kind", ".rows", ".row",
    ".seg2", ".row .say", ".row .typed", ".dlg footer", ".dlg .go",
    ".profile", ".p-back", ".p-id", ".p-av", ".pcard", ".pl", ".pform",
    ".hist", ".hr", ".hist-empty", "body.on-live", ".lt", ".lt-stage",
    ".lt-col", ".lt-bar", ".lt-box", ".lt-screen", ".lt-tools", ".ph-wait",
    ".ph-wait .spin", ".ph-wait .pw", ".lt-side", ".lt-back", ".lt-note",
    ".lt-doors", ".lt-h", ".lt-kind",
]
_APPENDED = [
    ".openrow .boot{flex:1;min-width:0;display:flex}",
    ".openrow .boot .open{flex:1;min-width:0}",
    ".who .out-form{display:contents}",
    ".lt-col{aspect-ratio:auto;height:auto;max-height:none}",
    ".lt-bar,.lt-box{flex:none}",
    ".lt-box{position:relative;overflow:hidden}",
    "#lt-view{flex:none;border:0;display:block;transform-origin:0 0;"
    "background:#000}",
    ".lt-col{box-sizing:content-box;flex:none}",
    "@media (max-width:900px){.lt-stage{height:auto;overflow:visible}}",
    "@media (max-width:900px){.lt{scrollbar-gutter:stable}}",
    ".col.ghost.failed .g-ring,.col.ghost.failed .g-orb::after"
    "{animation:none;opacity:0}",
    ".col.ghost.failed .app{filter:saturate(.45) brightness(.66)}",
    ".col.ghost.failed .g-t b{color:var(--bad-ink)}",
    ".dlg > .say{font:12px/1.4 var(--sans);color:var(--bad-ink)}",
    ".dlg > .say:empty{display:none}",
    ".dlg-note{margin:8px 0 0;font:12px/1.4 var(--sans);color:var(--dim)}",
]


def _selectors(css: str) -> set[str]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found = set()
    for hit in re.finditer(r"([^{}@;]+)\{", css):
        for sel in hit.group(1).split(","):
            sel = " ".join(sel.split())
            if sel and not sel.startswith(("from", "to")) and "%" not in sel:
                found.add(sel)
    return found


def test_the_station_css_is_the_prototypes():
    css = assets.STATION_CSS
    have = _selectors(css)
    for sel in _KEPT:
        assert sel in have, sel
    for gone in (".proto", ".lt-proto", ".ph-app", ".ph-btn", ".ph-status",
                 ".ph-home", ".ph-logo"):
        assert not any(s == gone or s.startswith(gone + " ")
                       or s.startswith(gone + ".") or s.startswith(gone + ":")
                       for s in have), gone
    at = 0
    for rule in _APPENDED:
        found = css.find(rule, at)
        assert found >= 0, rule
        at = found + len(rule)
    assert not re.search(r"min-width\s*:\s*\d", " ".join(
        re.findall(r"@media[^{]*", css))), "no min-width media query"
    assert "@media (prefers-reduced-motion:reduce)" in css
    for moment in ("rise", "bloom", "stamp", "spark", "roll", "bump", "moved",
                   "busy"):
        assert re.search(rf"(@keyframes {moment}\b|\.{moment}\b)", css), moment


#: The Results capsule's steps, read on the width of its own column.
_DAY_QUERIES = [
    "@container day (max-width:344.98px){.day .ttl{display:none}}",
    "@container day (max-width:289.98px){.day{gap:10px;padding:0 15px 0 10px}"
    ".day .sep{margin:0 2px}.day .n small{font-size:9px;letter-spacing:.06em}}",
    "@container day (max-width:249.98px){.day .ttl{display:flex}.day .sep,"
    ".day .n{display:none}.day{padding:0 16px 0 9px}}",
    "@container day (max-width:83.98px){.day .ttl{display:none}"
    ".day{padding:0 9px}}",
]


def test_results_is_shaped_by_its_own_column_not_the_window():
    """front-visual-1/-2: a scrollbar, a long name or a waiting Take once
    pushed the capsule over the logo, because the steps read the window.
    The capsule sits in its own container, the steps read that, and the
    phone-width rule still has the last word."""
    css = assets.STATION_CSS
    page = station_pages.station_page(_state(), USER)
    assert re.search(r'<div class="dayc">\s*<div class="day" aria-label="Results '
                     r'today">', page), "the capsule has a column of its own"
    rules = dict(re.findall(r"^(\.dayc?)\{([^}]*)\}", css, flags=re.M))
    assert "container:day/inline-size" in rules[".dayc"]
    assert "grid-area:day" in rules[".dayc"]
    assert "grid-area" not in rules[".day"] and "justify-self" not in rules[".day"]
    at = css.index("@media (max-width:1040px)")
    for q in _DAY_QUERIES:
        found = css.find(q, at)
        assert found >= 0, q
        at = found + len(q)
    assert css.find("@media (max-width:560px){.day .ttl,.day .sep,.day .n"
                    "{display:none}.day{padding:0 9px}", at) > at
    # No window-width rule shapes the capsule any more, but for the height
    # rule at 1320px and the phone-width ones.
    shaped = [m.group(1) for m in re.finditer(
        r"@media \(max-width:(\d+)px\)\{((?:[^{}]*\{[^{}]*\})*)\}", css)
        if re.search(r"\.day[ .{]", m.group(2))]
    assert shaped == ["1320", "560"], shaped
    assert "@media (max-width:340px){.dayc{display:none}}" in css
    assert "@media (max-width:1440px){.who .nm{display:none}" in css


def test_the_station_css_keeps_every_prototype_rule_when_the_prototype_is_here():
    """The full check, run where the prototype's source can be read
    (`STATION_PROTOTYPE=<path to desk9.src.html>`): every selector of its
    lines 8-688 that §6.4 keeps is in the stylesheet, in the same order."""
    proto = os.environ.get("STATION_PROTOTYPE", "")
    if not proto or not pathlib.Path(proto).is_file():
        pytest.skip("the prototype's source is not on this machine")
    lines = pathlib.Path(proto).read_text(encoding="utf-8").split("\n")
    for n, (was, _) in _BAR_REWRITE.items():
        assert lines[n - 1] == was, f"prototype line {n} is not what was rewritten"
    kept = []
    for n in range(8, 689):
        if 589 <= n <= 614 or 647 <= n <= 650 or 652 <= n <= 655:
            continue
        line = lines[n - 1]
        if n == 675:
            line = line.replace(".proto,.lt-proto{display:none}", "")
        if n in _BAR_REWRITE:
            kept.extend(_BAR_REWRITE[n][1])
            continue
        kept.append(line)
    assert assets.STATION_CSS.startswith("\n".join(kept) + "\n"), (
        "the prototype's rules, verbatim and in order, but for the bar's")


# The only lines of the prototype the stylesheet rewrites: the Results capsule
# is shaped by the width of its own column (the lead's measured fix, review
# 2026-09-29), not by the window's. Each prototype line -> what stands in its
# place (none: deleted).
_BAR_REWRITE = {
    75: (".day{grid-area:day;justify-self:center;display:flex;align-items:center;"
         "gap:12px;height:56px;padding:0 18px 0 11px;border-radius:14px;",
         [".dayc{grid-area:day;min-width:0;container:day/inline-size;display:flex;"
          "justify-content:center}",
          ".day{display:flex;align-items:center;gap:12px;height:56px;"
          "padding:0 18px 0 11px;border-radius:14px;"]),
    659: ("   its ring and title, then its ring alone. Under 1000px the bar takes two",
          ["   its ring and title, then its ring alone. Under 1040px the bar takes "
           "two"]),
    662: ("@media (max-width:1439px){.day .ttl{display:none}}", []),
    663: ("@media (max-width:1400px){.day{gap:10px;padding:0 15px 0 10px}"
          ".day .sep{margin:0 2px}.day .n small{font-size:9px;letter-spacing:.06em}}",
          []),
    664: ("@media (max-width:1360px){.who .nm{display:none}.who .me{padding:4px}}",
          ["@media (max-width:1440px){.who .nm{display:none}.who .me{padding:4px}}"]),
    667: ("@media (max-width:1240px){.day .ttl{display:flex}.day .sep,.day .n"
          "{display:none}.day{padding:0 16px 0 9px}}", []),
    668: ("@media (max-width:1120px){.day .ttl{display:none}.day{padding:0 9px}}",
          []),
    669: ("@media (max-width:1000px){.bar,.bar.tight{grid-template-columns:auto "
          "minmax(0,1fr) auto;grid-template-areas:\"brand day who\" "
          "\"takes takes takes\";row-gap:12px}",
          ["@media (max-width:1040px){.bar,.bar.tight{grid-template-columns:auto "
           "minmax(0,1fr) auto;grid-template-areas:\"brand day who\" "
           "\"takes takes takes\";row-gap:12px}"]),
    670: (" .who{justify-self:end;padding-left:0;border-left:0}.takes{width:100%}"
          ".take{flex:1}.take .n{margin-left:auto;padding-left:6px}",
          [" .who{justify-self:end;padding-left:0;border-left:0}.takes{width:100%}"
           ".take{flex:1}.take .n{margin-left:auto;padding-left:6px}}"]),
    671: (" .day .sep{display:block}.day .n{display:flex}"
          ".day{padding:0 15px 0 10px}}", []),
    672: ("@media (max-width:680px){.day .ttl{display:flex}.day .sep,.day .n"
          "{display:none}.day{padding:0 16px 0 9px}}",
          ["/* Results is shaped by the room its own column has, not by the "
           "window: the",
           "   names, the Takes and a scrollbar all take from it. Its widths "
           "as drawn:",
           "   full 381, no title 326, tight 286, title only 120, ring alone "
           "58. */",
           *_DAY_QUERIES]),
    676: ("@media (max-width:560px){.day .ttl{display:none}.day{padding:0 9px}"
          ".bar,.bar.tight{column-gap:14px}",
          ["@media (max-width:560px){.day .ttl,.day .sep,.day .n{display:none}"
           ".day{padding:0 9px}.bar,.bar.tight{column-gap:14px}"]),
    682: ("@media (max-width:340px){.day{display:none}}",
          ["@media (max-width:340px){.dayc{display:none}}"]),
}


# ------------------------------------------------------------ the vendor
_VENDOR = "gee" + "lark"


def _strings_naming_the_vendor(path: pathlib.Path) -> list[str]:
    allowed = re.compile(
        r"geelark_(farm|plan|refusal|wallet|run|build|serial|actions|jobs)"
        r"|[a-z]+\.geelark\.com|/geelark/|geelark\.py"
        r"|_geelark_\w+|geelark task |\bgeelark\b(?![ '])", re.I)
    src = path.read_text(encoding="utf-8")
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    middle = getattr(tokenize, "FSTRING_MIDDLE", None)
    found = []
    for i, tok in enumerate(toks):
        if tok.type == middle:
            text = tok.string.lower()
            if _VENDOR in text and _VENDOR in allowed.sub("", text):
                found.append(f"{path.name}:{tok.start[0]}")
            continue
        if tok.type != tokenize.STRING or _VENDOR not in tok.string.lower():
            continue
        j = i - 1
        while j >= 0 and toks[j].type in (tokenize.NL, tokenize.COMMENT):
            j -= 1
        if j < 0 or toks[j].type in (tokenize.INDENT, tokenize.NEWLINE,
                                     tokenize.DEDENT, tokenize.ENCODING):
            continue                                    # a docstring
        if _VENDOR in allowed.sub("", tok.string).lower():
            found.append(f"{path.name}:{tok.start[0]}: {tok.string[:70]}")
    return found


def test_nothing_the_station_says_names_the_vendor():
    """IranSpoty Cloud in every sentence an operator can read (2026-09-28).
    The other packages' Station modules are scanned when they exist."""
    modules = [PACKAGE / "web" / "station_pages.py",
               PACKAGE / "web" / "station_read.py",
               PACKAGE / "store" / "station.py"]
    found = []
    for path in modules:
        if path.is_file():
            found += _strings_naming_the_vendor(path)
    assert not found, "\n".join(found)
    code = re.sub(r"//[^\n]*", "", assets.STATION_JS)
    assert _VENDOR not in code.lower(), "the script's own words"
    for name in ("station.css", "station_page.html", "station_live.html",
                 "station_brand.svg", "station_icons.html"):
        text = (STATIC / name).read_text(encoding="utf-8").lower()
        assert _VENDOR not in text, name
    for body in (station_pages.station_page(_state(), USER),
                 station_pages.live_page(_live(), USER)):
        assert _VENDOR not in body.lower()


# -------------------------------------------------------------- assets
def test_the_station_assets_are_served_exactly_and_fold_into_the_rev():
    assert assets.served(assets.STATION_CSS_PATH) == (
        assets.STATION_CSS, "text/css; charset=utf-8", False)
    assert assets.served(assets.STATION_JS_PATH) == (
        assets.STATION_JS, "text/javascript; charset=utf-8", True)
    assert assets.served(assets.CSS_PATH) == (
        assets.CSS, "text/css; charset=utf-8", False)
    assert assets.served(assets.JS_PATH) == (
        assets.JS, "text/javascript; charset=utf-8", True)
    assert assets.served("/s/station-deadbeefcafe.js") is None
    assert assets.STATION_CSS_PATH == f"/s/station-{assets.REV}.css"
    assert assets.STATION_JS_PATH == f"/s/station-{assets.REV}.js"
    assert assets._rev("a", "b") != assets._rev("a", "bx")
    import hashlib
    assert assets._rev(assets.CSS, assets.JS) == hashlib.sha256(
        (assets.CSS + "\0" + assets.JS).encode("utf-8")).hexdigest()[:12]
    assert assets.REV == assets._rev(assets.CSS, assets.JS,
                                     assets.STATION_CSS, assets.STATION_JS,
                                     assets.PROXIES_CSS, assets.PROXIES_JS,
                                     assets.GMAILS_CSS, assets.GMAILS_JS)
    # The Station's file with the rail's appended: the admin's Station
    # draws the console's rail (2026-10-02).
    assert assets.STATION_CSS == (STATIC / "station.css").read_text(
        encoding="utf-8") + "\n" + (STATIC / "rail.css").read_text(
        encoding="utf-8")
    assert assets.STATION_JS == (STATIC / "station.js").read_text(
        encoding="utf-8")


def test_the_pages_carry_the_build_they_were_drawn_by(monkeypatch):
    from geelark_farm import config
    monkeypatch.setattr(config, "revision", lambda: "")
    body = station_pages.station_page(_state(), USER)
    assert f'<meta name="gf-rev" content="station-{assets.REV}">' in body
    monkeypatch.setattr(config, "revision", lambda: "1ad334c-dirty")
    body = station_pages.live_page(_live(), USER)
    assert '<meta name="gf-rev" content="station-1ad334c-dirty">' in body


def test_the_station_names_its_build_apart_from_the_dashboards(monkeypatch):
    """With STATION_FOR_OPERATORS on, an operator's `/` is the Station. A
    dashboard left open across the switch fetches `/` for its next swap,
    and dash.js pours the answer's `<main>` into its own shell unless the
    answer's `gf-rev` differs - then it reloads the tab whole. So the
    Station's documents name the build apart from the console's pages,
    and its state answers with the same name the page carries (the
    script reloads when the two differ)."""
    from geelark_farm import config
    from geelark_farm.web import pages, station_read

    rev = re.compile(r'<meta name="gf-rev" content="([^"]*)">')
    for revision in ("", "292543f"):
        monkeypatch.setattr(config, "revision", lambda r=revision: r)
        station = rev.search(station_pages.station_page(_state(), USER))
        live = rev.search(station_pages.live_page(_live(), USER))
        console = rev.search(pages.page("Dashboard", "<p>x</p>", user=dict(
            USER, user_admin=False, mutations=False, nav={})))
        assert station and live and console
        assert station.group(1) == live.group(1) == station_pages.station_rev()
        assert console.group(1) and station.group(1) != console.group(1)
        assert station.group(1).startswith("station-")
        assert station_read._rev() == station.group(1)
