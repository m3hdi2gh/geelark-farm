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

    A store that cannot be reached **raises**: it is not a missing seat.
    It answered None until 2026-09-29, and the caller then told every
    signed-in person they were signed out - the Station sent its page to
    /login and each Live tab stopped beating for good, so one blip of the
    cluster switched every held phone off after the grace. Raised, the
    error reaches the web's own store-down answer (503 `down`).
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
    except Exception as exc:
        log.warning("could not read the session (%s)", exc)
        raise
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


#: How long this browser's old seat outlives a password change. The new
#: cookie reaches the browser one round trip after the commit, and a Live
#: tab's beat or the page's pull already in flight still carries the old
#: one: deleted at once, that request read "signed out" and the tab
#: stopped beating for good (2026-09-29). Every other browser is still
#: put out at once.
OLD_SEAT_GRACE_SECONDS = 60


def rotate(conn, token: str, user_id: int, *, hours: float) -> str | None:
    """A new seat for this browser, on the caller's connection and inside
    its transaction (it does not commit): the same csrf, so the Live tabs
    this browser opened keep working, and every other seat of this person
    ended. This browser's old seat is not ended but cut to
    `OLD_SEAT_GRACE_SECONDS`, for the requests already on their way with
    the old cookie. Returns the new raw token, or None when the old seat
    was already gone - every seat of the person is then ended. The raw
    tokens never reach SQL; only their digests do."""
    new = secrets.token_urlsafe(32)
    old = _digest(token)
    row = conn.execute(
        "INSERT INTO sessions (token_hash, user_id, csrf, until)"
        " SELECT %s, user_id, csrf, now() + %s * interval '1 hour' FROM sessions"
        " WHERE token_hash = %s AND user_id = %s AND until > now()"
        " RETURNING token_hash",
        (_digest(new), float(hours), old, int(user_id))).fetchone()
    conn.execute("DELETE FROM sessions WHERE user_id = %s"
                 " AND token_hash NOT IN (%s, %s)",
                 (int(user_id), _digest(new), old))
    conn.execute("UPDATE sessions SET until = least(until, now()"
                 " + %s * interval '1 second')"
                 " WHERE token_hash = %s AND user_id = %s",
                 (int(OLD_SEAT_GRACE_SECONDS), old, int(user_id)))
    return new if row is not None else None


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
