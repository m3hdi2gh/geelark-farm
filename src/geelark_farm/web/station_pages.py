"""The Station's two documents: the operator's page and a phone's Live tab.

Both are templates in `static/`, read once at import like the assets, with
their placeholders filled per request. They carry their own stylesheet and
script (`station.css`, `station.js`) and never the console's: no rail, no
alert strip, nothing from `user["nav"]`. The first paint's state rides in a
JSON island the script reads; everything after that comes from
`/station/state` (or the Live tab's own state).
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .. import config
from . import assets

_HERE = Path(__file__).resolve().parent / "static"

_STATION = (_HERE / "station_page.html").read_text(encoding="utf-8")
_LIVE = (_HERE / "station_live.html").read_text(encoding="utf-8")
#: The full horizontal logo, inlined once per document.
_BRAND = (_HERE / "station_brand.svg").read_text(encoding="utf-8").strip()
#: `<template id="gf-icons">`: every static SVG the script clones.
_ICONS = (_HERE / "station_icons.html").read_text(encoding="utf-8").strip()

_LANES = ("gpt", "spotify", "other")
_SLOT = re.compile(r"\{\{([A-Z]+)\}\}")


def _island(obj) -> str:
    """The state as JSON that cannot close the script element it sits in.

    `<` is the only character that can end a `<script>` early (`</script>`)
    or open a comment (`<!--`); as `\\u003c` it is the same string to
    `JSON.parse`. U+2028 and U+2029 are escaped too, for any reader that
    still takes the island for JavaScript.
    """
    text = json.dumps(obj, default=str, separators=(",", ":"))
    return (text.replace("<", "\\u003c").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def _fill(template: str, values: dict) -> str:
    """Every placeholder in one pass, so nothing a value carries - a name
    with braces in it, an address - is ever read as a placeholder. An
    unknown placeholder is a KeyError: a template and this module out of
    step should fail the tests, not render `{{X}}`."""
    return _SLOT.sub(lambda m: values[m.group(1)], template)


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _common(state: dict, user: dict, title: str) -> dict:
    from . import pages  # _FAVICON is final only once pages has loaded
    return {
        "TITLE": _esc(title),
        "FAVICON": _esc(pages._FAVICON),
        "CSS": _esc(assets.STATION_CSS_PATH),
        "JS": _esc(assets.STATION_JS_PATH),
        "REV": _esc(config.revision() or assets.REV),
        "CSRF": _esc(user.get("csrf") or ""),
        "BRAND": _BRAND,
        "ICONS": _ICONS,
        "STATE": _island(state),
    }


def station_page(state: dict, user: dict) -> str:
    """The Station document, drawn from `station_read.state`."""
    me = state.get("me") if isinstance(state.get("me"), dict) else {}
    name = str(me.get("name") or user.get("username") or "")
    initial = str(me.get("initial") or name[:1].upper())
    values = _common(state, user, "IranSpoty Station")
    values["NAME"] = _esc(name)
    values["INITIAL"] = _esc(initial)
    return _fill(_STATION, values)


def live_page(live: dict, user: dict) -> str:
    """A phone's Live tab, drawn from `station_read.live`."""
    serial = str(live.get("serial") or "")
    lane = str(live.get("lane") or "")
    if lane not in _LANES:
        lane = "gpt"
    values = _common(live, user, f"Phone {serial} · IranSpoty Station")
    values["SERIAL"] = _esc(serial)
    values["LANE"] = lane
    return _fill(_LIVE, values)
