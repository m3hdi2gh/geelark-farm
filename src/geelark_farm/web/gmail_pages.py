"""The Gmails page's document (2026-10-07): the prototype the user
approved, served as a template in `static/` with its own stylesheet and
script, in the admin rail like the Proxies page.

The first paint's state rides in a JSON island the script reads (see
`station_pages._island` for why it cannot close its script element);
everything after that comes from `/pools/gmail/state`.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import assets
from .station_pages import _BRAND, _esc, _island, _rail

_HERE = Path(__file__).resolve().parent / "static"
_PAGE = (_HERE / "gmails_page.html").read_text(encoding="utf-8")
_SLOT = re.compile(r"\{\{([A-Z]+)\}\}")
HERE = "/pools/gmail"


def gmails_page(state: dict, user: dict) -> str:
    """The Gmails document, drawn from `gmails_read.state`."""
    from . import pages  # _FAVICON is final only once pages has loaded

    name = str(user.get("username") or "?")
    rail_open, rail_close = _rail(user, HERE)
    values = {
        "FAVICON": _esc(pages._FAVICON),
        "CSS": _esc(assets.GMAILS_CSS_PATH),
        "JS": _esc(assets.GMAILS_JS_PATH),
        "REV": _esc(assets.REV),
        "CSRF": _esc(user.get("csrf") or ""),
        "BRAND": _BRAND,
        "NAME": _esc(name),
        "INITIAL": _esc(name[:1].upper()),
        "ROLE": _esc(user.get("role") or ""),
        "DAY": _esc((state.get("day") or {}).get("word") or ""),
        "RAILOPEN": rail_open,
        "RAILCLOSE": rail_close,
        "STATE": _island(state),
    }
    return _SLOT.sub(lambda m: values[m.group(1)], _PAGE)
