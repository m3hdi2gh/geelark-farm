"""The two lanes of stock: phones and exits kept for GPT, and for Spotify.

A phone is built for one or the other, and so is the exit it is built
behind. The build itself is the same - the Gmail goes in and every app
goes on - what differs is which exits it may take, which accounts it may
later be sent, and which shelf it is counted on. Operators booting a phone
read the lane off the row, so a phone kept for Spotify is not spent on a
GPT account by mistake (the operator, 2026-09-29).

"GPT" is ChatGPT and Claude together: one lane for the AI products, one
for Spotify. Product keys (products.py) map onto lanes here and nowhere
else, so a fourth product is one line.

A leaf: stdlib only.
"""

from __future__ import annotations

GPT = "gpt"
SPOTIFY = "spotify"
#: Every lane, in the order the console draws them.
ALL = (GPT, SPOTIFY)
#: The word a person reads.
WORDS = {GPT: "GPT", SPOTIFY: "Spotify"}

_PRODUCTS = {"chatgpt": GPT, "claude": GPT, "spotify": SPOTIFY}
_SAID = {"gpt": GPT, "chatgpt": GPT, "claude": GPT, "ai": GPT,
         "spotify": SPOTIFY}


def of_product(product: str | None) -> str:
    """The lane a product belongs to. A blank or unknown product is the
    GPT lane, which is what a blank Product has always meant."""
    return _PRODUCTS.get(str(product or "").strip().lower(), GPT)


def normal(text: str | None) -> str:
    """A lane as somebody typed or stored it - "GPT", "spotify", a
    product key - or "" for a word that is no lane at all."""
    return _SAID.get(str(text or "").strip().lower(), "")


def word(purpose: str | None) -> str:
    """What a lane is called on a page; "" for no lane."""
    return WORDS.get(str(purpose or "").strip().lower(), "")


def fits(purpose: str | None, wanted: str) -> bool:
    """Whether a row kept for `purpose` may serve a build for `wanted`.
    An unlabelled row ("") serves either lane; a labelled one serves its
    own. `wanted` blank asks for anything."""
    have = normal(purpose)
    return not wanted or not have or have == wanted


def targets(settings) -> dict[str, int]:
    """How many warm phones each lane keeps: WARM_STOCK_SPOTIFY for
    Spotify and the rest of WARM_STOCK for GPT."""
    total = int(getattr(settings, "warm_stock", 0) or 0)
    spotify = min(total, max(0, int(getattr(settings, "warm_stock_spotify", 0)
                                    or 0)))
    return {GPT: total - spotify, SPOTIFY: spotify}
