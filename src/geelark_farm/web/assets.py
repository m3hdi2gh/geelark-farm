"""The two files the browser needs, which the page no longer carries.

The stylesheet and the console's one script used to be literals inside
`pages.py` - 54KB of CSS inside a `.format()`ed template with every
brace doubled, and 76KB of JavaScript inside a non-raw triple-quoted
string. Two things came of that and both bit.

The first is size: 130,569 bytes went out with every single response,
unchanged, uncacheable, on a page the browser re-fetches every few
seconds while the farm builds.

The second is worse. Code inside a Python string is invisible to every
tool this project runs - no syntax check, no linter, no dead-code
warning - and the tests that touched it asserted substrings of its
source. That is how `var typing` came to be computed in `mayRedraw` and
never read: the guard that holds a redraw while somebody is typing was
dead from September to 2026-09-20, under a green test asserting
`"function mayRedraw()" in script`. A substring cannot see an unused
variable. Out here `node --check` and eslint can.

Served under a name that is the content's own hash, so a deploy
invalidates the cache by construction and there is no path handling and
nothing to traverse: one known name answers, everything else is a 404.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

_HERE = Path(__file__).resolve().parent / "static"

#: The admin rail's own rules (2026-10-02). The console's pages and the
#: admin's Station both draw the rail, and neither document loads the
#: other's stylesheet, so it is one file appended to both.
RAIL_CSS = (_HERE / "rail.css").read_text(encoding="utf-8")
CSS = (_HERE / "console.css").read_text(encoding="utf-8") + "\n" + RAIL_CSS
JS = (_HERE / "dash.js").read_text(encoding="utf-8")
#: The operator Station's own pair: its document never loads the
#: console's (2026-09-29).
STATION_CSS = ((_HERE / "station.css").read_text(encoding="utf-8")
               + "\n" + RAIL_CSS)
STATION_JS = (_HERE / "station.js").read_text(encoding="utf-8")
#: The full horizontal logo - the mark beside the IranSpoty wordmark, the
#: wordmark on `currentColor`. The Station draws it in its header and the
#: sign-in card above the form (2026-10-02): one file, so the two never
#: spell the name differently. The whole pack is in `brand/`.
BRAND_LOGO = (_HERE / "station_brand.svg").read_text(encoding="utf-8").strip()


def _rev(*texts: str) -> str:
    """What these files are, together. `_rev(CSS, JS)` is the formula the
    console's pair was named by before the Station joined it."""
    return hashlib.sha256("\0".join(texts).encode("utf-8")).hexdigest()[:12]


#: What this build's files are. Short enough to read in a URL, long
#: enough that two builds cannot collide. The Station's pair folds in, so
#: a change to either page's files reloads both.
REV = _rev(CSS, JS, STATION_CSS, STATION_JS)

#: The stylesheet is presentation and is served to anybody who can reach
#: the host - the sign-in page needs it and has no session yet. The
#: script is the console's behaviour: which endpoints exist, which
#: fields they take, what each press does. That waits for a session and
#: is cached `private`.
CSS_PATH = f"/s/{REV}.css"
JS_PATH = f"/s/{REV}.js"
STATION_CSS_PATH = f"/s/station-{REV}.css"
STATION_JS_PATH = f"/s/station-{REV}.js"

_KIND = {CSS_PATH: ("text/css; charset=utf-8", False),
         JS_PATH: ("text/javascript; charset=utf-8", True),
         STATION_CSS_PATH: ("text/css; charset=utf-8", False),
         STATION_JS_PATH: ("text/javascript; charset=utf-8", True)}
_BODY = {CSS_PATH: CSS, JS_PATH: JS,
         STATION_CSS_PATH: STATION_CSS, STATION_JS_PATH: STATION_JS}


def served(path: str) -> tuple[str, str, bool] | None:
    """The body, its type, and whether it needs a session - or None for
    a name this build does not answer to.

    A name that is not this build's is a 404 rather than a redirect to
    the current one: the only way to ask for a stale name is to be a
    stale page, and a stale page has a stale script to go with it. It
    should reload, which `gf-rev` makes it do.
    """
    kind = _KIND.get(path)
    if kind is None:
        return None
    return _BODY[path], kind[0], kind[1]
