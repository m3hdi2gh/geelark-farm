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


#: One group of an authenticator key the way Google shows it - `fioi p2yx
#: bzu7 gax3` - four base32 characters. A line split on plain spaces turns
#: the key into eight of these, none of which is a key on its own.
_GROUP = re.compile(r"^[A-Za-z2-7]{4}$")


def _regroup(tokens: list[str]) -> tuple[str, list[str]]:
    """Find a key that arrived as spaced groups and put it back together.

    Returns the key (unspaced, uppercased) and the tokens that were not
    part of it. The run has to be at least four groups of exactly four,
    which is what Google prints and what a password never looks like -
    `chaobuoisang` is twelve letters, and one token, and stays a password.
    Eleven Gmails went into the pool with no secret because their keys
    were pasted this way and the reader quietly threw the groups away
    (2026-09-06).
    """
    best, at = 0, -1
    run = 0
    for i, tok in enumerate(tokens + [""]):
        if _GROUP.match(tok):
            run += 1
            continue
        if run > best:
            best, at = run, i - run
        run = 0
    if best < 4:
        return "", list(tokens)
    key = "".join(tokens[at:at + best]).upper()
    return key, tokens[:at] + tokens[at + best:]


def accounts(text: str) -> list[dict]:
    """One dict per non-empty line: address, password, secret, recovery,
    and `unread` - the pieces of the line nothing here could place.
    Missing pieces are empty strings; the row is kept so the preview can
    say what is wrong with it rather than silently dropping it, and a
    piece that was not understood is said the same way - a key that is
    thrown away without a word is a phone that stops at the 2-step
    screen a week later."""
    rows = []
    for raw in (text or "").splitlines():
        parts = _split(raw)
        if not parts:
            continue
        emails = [p for p in parts if _EMAIL.match(p)]
        rest = [p for p in parts if p not in emails]
        # The LAST base32-shaped token, not the first. A seller's list
        # reads address, password, key - and a password of sixteen plain
        # letters is base32-shaped, so taking the first claimed the
        # password as the key and left the row reading "no password" on a
        # line that visibly had one (2026-09-07).
        keys = [p for p in rest if _BASE32.match(p) and not p.isdigit()]
        secret = keys[-1] if keys else ""
        rest = [p for p in rest if p != secret]
        if not secret:
            secret, rest = _regroup(rest)
        password = rest[0] if rest else ""
        row = {
            "address": emails[0] if emails else "",
            "recovery": emails[1] if len(emails) > 1 else "",
            "password": password,
            "secret": secret.replace(" ", "").upper() if secret else "",
            "unread": rest[1:] + emails[2:],
            "line": raw.strip(),
        }
        # Two things the reader can see are wrong but cannot resolve, said
        # here rather than guessed at. Taking a key and leaving no password
        # is the guess that used to be made silently; carrying both a key
        # and a recovery address is a line where only one survives, and it
        # was the key that was dropped, under a green "ok".
        if secret and not password:
            row["password"], row["secret"] = secret, ""
            row["error"] = ("could not tell the password from the 2fa key "
                            "on this line - put them in separate columns")
        elif row["secret"] and row["recovery"]:
            row["error"] = ("this line has both an authenticator key and a "
                            "recovery address, and a row keeps one - delete "
                            "whichever is wrong and preview again")
        rows.append(row)
    return rows


#: A port on its own, which is what the second column of a four-column
#: vendor list looks like once the line is split.
_PORT = re.compile(r"^\d{2,5}$")


def _joined(parts: list[str]) -> tuple[str, list[str]]:
    """An exit assembled from separate columns, and what was left over.

    The Proxy tab has always taken both shapes - its own heading says
    "Proxy String (or Host/Port/User/Pass)" - and both the sheet pool
    (`ProxyPool._interpret`) and the importer join the four cells the same
    way when the joined cell is blank, because somebody filling a tab by
    hand fills the columns. The console's paste box could read only the
    joined string, so a vendor list in four columns pasted as nothing at
    all: every line refused, with no hint that the shape was the problem
    (2026-09-06, found while closing the sheet).

    Host, port, and then user and password in that order, which is the
    order the columns are in and the order both existing joins use.
    """
    at = next((i for i, part in enumerate(parts) if _PORT.match(part)), None)
    if at is None or at == 0:
        return "", parts
    host = parts[at - 1]
    rest = parts[at + 1:]
    # A name can sit either side of the four, so only what looks like
    # credentials is taken: two tokens at most, and never one with a colon
    # in it, which would be a joined string somebody split by accident.
    creds = [p for p in rest[:2] if ":" not in p]
    used = {at - 1, at, *range(at + 1, at + 1 + len(creds))}
    joined = ":".join([host, parts[at], *creds])
    return joined, [p for i, p in enumerate(parts) if i not in used]


def proxies(text: str) -> list[dict]:
    """One dict per non-empty line: raw (the host:port:user:pass string)
    and name (a short token beside it, or empty for the pass to mint).

    The joined string wins when there is one; a line of separate columns is
    assembled the way the tab's own reader assembles it.
    """
    rows = []
    for raw in (text or "").splitlines():
        parts = _split(raw)
        if not parts:
            continue
        string = next((p for p in parts if _PROXY.match(p)), "")
        others = [p for p in parts if p != string]
        if not string:
            string, others = _joined(parts)
        rows.append({"raw": string, "name": others[0] if others else "",
                     "line": raw.strip()})
    return rows
