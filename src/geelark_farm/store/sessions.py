"""Who is logged in, in the store rather than in this process's memory.

Sessions lived in a module-level dict. That works exactly until the
process restarts - and the process restarts on every deploy, so every
change to the code signed everybody out and the operator had to log in
again to see it (2026-09-05). Nothing was broken; the seats were simply
kept somewhere that does not survive a `docker compose up`.

**The cookie is not what is stored.** The table holds `sha256` of the
token, so a copy of the database is not a drawer full of live seats. The
raw token exists only in the browser's cookie and for the length of one
request here, which is the same shape the password columns already have.

**The user row is read fresh on every request** rather than frozen at
login. That is not a performance choice, it is the correctness one: a
permission taken away has to take effect on the next click, and the
in-memory version kept a copy that could only be corrected by ending the
session outright. `end_all_of` stays for the case that is really about
ending seats - a password reset - rather than about stale rights.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from ..config import Settings
from .db import Store

log = logging.getLogger(__name__)

#: Columns a page must never see, whatever `SELECT *` hands back. The same
#: two `check_login` drops, for the same reason.
_SECRET_COLUMNS = ("password_hash", "password_salt")


def _digest(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def start(settings: Settings, user_id: int, *, hours: float) -> tuple[str, str]:
    """Open a seat for this person. Returns `(token, csrf)`.

    The token goes in the cookie; the csrf token is handed to every page
    and checked on every POST but the login itself.
    """
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    with Store(settings) as store:
        store._write(
            "INSERT INTO sessions (token_hash, user_id, csrf, until)"
            " VALUES (%s, %s, %s, now() + %s * interval '1 hour')",
            (_digest(token), int(user_id), csrf, float(hours)))
    return token, csrf


def find(settings: Settings, token: str) -> dict | None:
    """The seat behind this cookie, as `{"user": row, "csrf": ...}`, or None.

    An expired seat, a deleted one, and a user who has since been
    deactivated all answer the same None - the caller sends them to the
    login page and has nothing further to decide.
    """
    if not token:
        return None
    try:
        with Store(settings) as store:
            rows = store._rows(
                "SELECT s.csrf, u.* FROM sessions s JOIN users u"
                " ON u.id = s.user_id"
                " WHERE s.token_hash = %s AND s.until > now() AND u.active",
                (_digest(token),))
    except Exception as exc:                                      # noqa: BLE001
        # A store that cannot be reached is not a forged cookie. Say so
        # and refuse the request rather than logging the person out, which
        # would be a second, wrong story about what went wrong.
        log.warning("could not read the session (%s)", exc)
        return None
    if not rows:
        return None
    row = rows[0]
    csrf = row.pop("csrf", "")
    for column in _SECRET_COLUMNS:
        row.pop(column, None)
    return {"user": row, "csrf": csrf}


def end(settings: Settings, token: str) -> None:
    """Log out. A token that is not there is already logged out."""
    if not token:
        return
    with Store(settings) as store:
        store._write("DELETE FROM sessions WHERE token_hash = %s",
                     (_digest(token),))


def end_all_of(settings: Settings, user_id: int, *, keep: str = "") -> int:
    """End every seat this person holds, except the token given.

    An admin resetting their own password keeps the chair they are sitting
    in; everybody else holding that account is put out.
    """
    with Store(settings) as store:
        rows = store._write(
            "DELETE FROM sessions WHERE user_id = %s AND token_hash <> %s"
            " RETURNING token_hash", (int(user_id), _digest(keep)))
    return len(rows)


def sweep(settings: Settings) -> int:
    """Drop what has expired. Nothing depends on this - `find` already
    refuses an expired row - so it is housekeeping, and never fatal."""
    try:
        with Store(settings) as store:
            rows = store._write(
                "DELETE FROM sessions WHERE until <= now() RETURNING token_hash")
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not sweep expired sessions (%s)", exc)
        return 0
    return len(rows)
