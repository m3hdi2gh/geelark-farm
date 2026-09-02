"""Reading what somebody pasted from a seller's sheet.

The owner receives stock as a sheet and copies a range: the clipboard
arrives tab-separated, in whatever column order that seller uses. So the
delimiter is detected, not declared, and so is the column order - the
address is the token with an `@`, an authenticator secret is the token
shaped like base32, a second address on a Gmail row is its recovery
address, and the password is whatever remains. Nothing here validates:
that is `store.validate`'s job and the preview calls it per row, so a
bad row is refused with a reason while the good rows still go in.

Colons are not a delimiter for accounts: a real password in this farm
carried one (`them@hmD:72&93$#`, 2026-08-31). They are, and only, for a
proxy string - which is exactly `host:port:user:pass` and nothing else.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
#: An authenticator secret: base32, spaces tolerated, at least 16 chars of
#: it once the spaces go. The sheet's own rule (accounts.normalize_totp).
_BASE32 = re.compile(r"^[A-Za-z2-7 ]{16,}$")
_PROXY = re.compile(r"^\S+:\d{2,5}(:\S*){0,2}$")


def _split(line: str) -> list[str]:
    """Tab first (what a sheet copy gives), then comma, then whitespace."""
    if "\t" in line:
        parts = line.split("\t")
    elif "," in line:
        parts = line.split(",")
    else:
        parts = line.split()
    return [p.strip() for p in parts if p.strip()]


def accounts(text: str) -> list[dict]:
    """One dict per non-empty line: address, password, secret, recovery.
    Missing pieces are empty strings; the row is kept so the preview can
    say what is wrong with it rather than silently dropping it."""
    rows = []
    for raw in (text or "").splitlines():
        parts = _split(raw)
        if not parts:
            continue
        emails = [p for p in parts if _EMAIL.match(p)]
        rest = [p for p in parts if p not in emails]
        secret = next((p for p in rest
                       if _BASE32.match(p) and not p.isdigit()), "")
        rest = [p for p in rest if p != secret]
        password = rest[0] if rest else ""
        rows.append({
            "address": emails[0] if emails else "",
            "recovery": emails[1] if len(emails) > 1 else "",
            "password": password,
            "secret": secret.replace(" ", "").upper() if secret else "",
            "line": raw.strip(),
        })
    return rows


def proxies(text: str) -> list[dict]:
    """One dict per non-empty line: raw (the host:port:user:pass string)
    and name (a short token beside it, or empty for the pass to mint)."""
    rows = []
    for raw in (text or "").splitlines():
        parts = _split(raw)
        if not parts:
            continue
        string = next((p for p in parts if _PROXY.match(p)), "")
        others = [p for p in parts if p != string]
        rows.append({"raw": string, "name": others[0] if others else "",
                     "line": raw.strip()})
    return rows
