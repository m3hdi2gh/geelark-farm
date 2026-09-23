"""The apps the farm signs accounts into, said once.

What the builder knows about each product - its display name, the
service that judges its accounts, the sign-in flow, the package, where
an emailed code comes from - was spread over five tables and three
`if product == ...` ladders in builder.py, and a new app touched eleven
places there before it touched the console (the builder review,
2026-09-23). It is one entry here now.

A leaf: stdlib and config only at import time. The flow is a dotted
module path resolved when it is asked for, so importing this does not
import three sign-in flows, and a test that patches a flow's module
reaches the object the builder calls.

What stays where it was, on purpose: `accounts.SERVED`/`ASKS_A_PERSON`
(the panel contract's words, compared as strings in four places), and
the SQL that reads a blank Product as ChatGPT (pgpool, schema.sql).
"""
from __future__ import annotations

import importlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import ModuleType

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppSpec:
    """One product. Only what today's three need; a field is added when
    a real case needs it, not before."""

    #: The pool's Product word, `Wanted.app`, `Build.app_product`.
    key: str
    #: The word a page says, and what GeeLark's catalogue is searched by.
    name: str
    #: Who judges an account of it - fills `{service}` in a verdict.
    service: str
    #: The sign-in flow's module, imported when asked for.
    flow: str
    #: Its package; "" means `settings.target_package` (ChatGPT's, which
    #: its flow does not name).
    package: str = ""
    #: Where an emailed code comes from: the farm's own mailbox, or the
    #: customer through the panel (`given`). A customer is never asked for
    #: a ChatGPT code (the operator, 2026-09-19).
    codes: str = "mailbox"

    def package_for(self, settings) -> str:
        return self.package or settings.target_package

    def flow_module(self) -> ModuleType:
        return importlib.import_module(self.flow)


#: The products, in the order a page lists them and a phone installs
#: them. The first is what a row with no Product is.
PRODUCTS: dict[str, AppSpec] = {
    "chatgpt": AppSpec("chatgpt", "ChatGPT", "OpenAI",
                       "geelark_farm.flows.chatgpt_login"),
    # All three are in GeeLark's app center - Spotify is its own, ChatGPT
    # and Claude are copies uploaded from a Play-signed phone - and Play
    # is only the fallback now (2026-09-12).
    "spotify": AppSpec("spotify", "Spotify", "Spotify",
                       "geelark_farm.flows.spotify_login",
                       package="com.spotify.music"),
    "claude": AppSpec("claude", "Claude", "Anthropic",
                      "geelark_farm.flows.claude_login",
                      package="com.anthropic.claude", codes="panel"),
}
#: A row with no Product is the console's and the sheet's, and those were
#: always ChatGPT. The one Python copy of that rule.
DEFAULT = "chatgpt"


def product_of(values: Mapping[str, object] | None) -> str:
    """Which product an account row is for, from its Product value."""
    word = str((values or {}).get("Product") or "").strip().lower()
    return word or DEFAULT


def spec(key: str) -> AppSpec | None:
    return PRODUCTS.get(str(key or "").strip().lower())


def spec_of(values: Mapping[str, object] | None) -> AppSpec:
    """The product an account row is for. A Product word the farm does
    not know is served as the default, which is what the builder did."""
    return PRODUCTS.get(product_of(values)) or PRODUCTS[DEFAULT]


_NOT_AN_APP: set[str] = set()


def apps_every_phone(settings) -> tuple[str, ...]:
    """The apps every phone carries: the names in APPS_ON_EVERY_PHONE this
    farm knows how to install, in the order given, without repeats. A
    word that is not one is said once a process, not once a build."""
    out: list[str] = []
    for word in getattr(settings, "apps_on_every_phone", ()):
        app = str(word).strip().lower()
        if app in PRODUCTS:
            if app not in out:
                out.append(app)
        elif app and app not in _NOT_AN_APP:
            _NOT_AN_APP.add(app)
            log.warning("APPS_ON_EVERY_PHONE names %r, which is not one of "
                        "%s; it is skipped", app, ", ".join(sorted(PRODUCTS)))
    return tuple(out)


def named(joined: str) -> str:
    """"chatgpt+spotify" the way a person says it."""
    words = [PRODUCTS[app].name if app in PRODUCTS else app
             for app in joined.split("+") if app]
    if len(words) > 1:
        return f"{', '.join(words[:-1])} and {words[-1]}"
    return words[0] if words else "nothing"


def flow_loggers() -> dict[str, str]:
    """The console's word for each product's sign-in lines, keyed by the
    flow logger's last segment - "claude sign-in" rather than the raw
    `claude_login` a new flow's lines used to show under."""
    return {s.flow.rsplit(".", 1)[-1]: f"{s.key} sign-in"
            for s in PRODUCTS.values()}
