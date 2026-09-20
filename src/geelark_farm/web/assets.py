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

CSS = (_HERE / "console.css").read_text(encoding="utf-8")
JS = (_HERE / "dash.js").read_text(encoding="utf-8")

#: What this pair is. Short enough to read in a URL, long enough that
#: two builds cannot collide.
REV = hashlib.sha256(
    (CSS + "\0" + JS).encode("utf-8")).hexdigest()[:12]

#: The stylesheet is presentation and is served to anybody who can reach
#: the host - the sign-in page needs it and has no session yet. The
#: script is the console's behaviour: which endpoints exist, which
#: fields they take, what each press does. That waits for a session and
#: is cached `private`.
CSS_PATH = f"/s/{REV}.css"
JS_PATH = f"/s/{REV}.js"

_KIND = {CSS_PATH: ("text/css; charset=utf-8", False),
         JS_PATH: ("text/javascript; charset=utf-8", True)}


def served(path: str) -> tuple[str, str, bool] | None:
    """The body, its type, and whether it needs a session - or None for
    a name this build does not answer to.

    A name that is not this build's is a 404 rather than a redirect to
    the current one: the only way to ask for a stale name is to be a
    stale page, and a stale page has a stale script to go with it. It
    should reload, which `gf-rev` makes it do.
    """
    if path == CSS_PATH:
        return CSS, _KIND[CSS_PATH][0], False
    if path == JS_PATH:
        return JS, _KIND[JS_PATH][0], True
    return None
