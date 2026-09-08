"""Builds somebody asked for by hand, and what became of them.

The queue between a button and a seven-minute job. A verb drains inside a
pass and must be quick; a build is not. So the verb writes the wish here
and returns, and the build phase of the same pass takes it - the phase that
already runs long jobs in a pool.

Nothing here claims anything. The wish holds the credentials as text, and
`builder.build_one` claims them under the lock that stops one Gmail
reaching two phones. A row taken between the asking and the building fails
by name rather than racing for it.
"""

from __future__ import annotations

import logging

from ..config import Settings
from .db import Store

log = logging.getLogger(__name__)

#: What a wish can be. `running` is written by the pass that took it, so a
#: pass that dies mid-build leaves a row saying so rather than one that
#: looks untouched and is taken again by the next pass.
STATES = ("queued", "running", "done", "failed")


def ask(settings: Settings, *, gmail: str = "", proxy_name: str = "",
        install_app: bool = True, app_account: str = "",
        requested_by: int | None = None, app: str | None = None) -> int:
    """Write one wish. Returns its id, which is what the page says back.

    `app` is which app the phone gets - '' for none, 'chatgpt', 'spotify'.
    Left None it follows `install_app`, which is what every older caller
    means: the app, or no app."""
    if app is None:
        app = "chatgpt" if install_app else ""
    with Store(settings) as store:
        rows = store._write(
            "INSERT INTO wanted_builds"
            " (gmail, proxy_name, install_app, app_account, requested_by, app)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (gmail.strip(), proxy_name.strip(), bool(app),
             app_account.strip(), requested_by, app))
    return int(rows[0]["id"])


def take(settings: Settings, limit: int = 2) -> list[dict]:
    """Claim the oldest wishes, marking them `running` in the same breath.

    Marked before they are built, not after: two passes overlapping is the
    ordinary case on a farm with a worker pool, and a wish that still reads
    `queued` while a pass is building it is a wish the next pass builds
    again - two phones, two Gmails, one request.

    `limit` is small on purpose. These are the most expensive rows in the
    system and nobody asks for ten at once; a person who does gets them over
    a few passes rather than all at the cost of the shortfall.
    """
    with Store(settings) as store:
        return store._write(
            "UPDATE wanted_builds SET status = 'running'"
            " WHERE id IN (SELECT id FROM wanted_builds"
            "              WHERE status = 'queued'"
            "              ORDER BY created_at, id LIMIT %s"
            "              FOR UPDATE SKIP LOCKED)"
            " RETURNING id, gmail, proxy_name, install_app, app_account, app,"
            " requested_by",
            (max(1, int(limit)),))


def settle(settings: Settings, wanted_id: int, *, ok: bool,
           serial: str = "", detail: str = "") -> None:
    """What became of one wish. Never fatal: a build that worked must not be
    reported as failed because the row saying so could not be written."""
    try:
        with Store(settings) as store:
            store._write(
                "UPDATE wanted_builds SET status = %s, serial = %s,"
                " detail = %s, ended_at = now() WHERE id = %s RETURNING id",
                ("done" if ok else "failed", serial, detail[:400],
                 int(wanted_id)))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not record what became of wanted build %s (%s)",
                    wanted_id, exc)


def recent(settings: Settings, limit: int = 8) -> list[dict]:
    """The last few wishes, newest first - what the person who asked reads
    to find out whether it happened."""
    with Store(settings) as store:
        return store._rows(
            "SELECT w.id, w.gmail, w.proxy_name, w.install_app,"
            " w.app_account, w.status, w.serial, w.detail, w.created_at,"
            " coalesce(u.username, '') AS asked_by"
            " FROM wanted_builds w LEFT JOIN users u ON u.id = w.requested_by"
            " ORDER BY w.id DESC LIMIT %s", (max(1, int(limit)),))


def release_stale(settings: Settings, older_than_minutes: int = 45) -> int:
    """Put back wishes a dead pass left `running`.

    Without this a process killed mid-build leaves a row nothing will ever
    finish and nothing will ever retry - the same shape as a credential left
    `in_use`, and the same fix.
    """
    with Store(settings) as store:
        rows = store._write(
            "UPDATE wanted_builds SET status = 'queued'"
            " WHERE status = 'running'"
            "   AND created_at < now() - (interval '1 minute' * %s)"
            " RETURNING id", (max(1, int(older_than_minutes)),))
    if rows:
        log.warning("%d hand-built phone request(s) were left running by a "
                    "pass that did not come back; they are queued again",
                    len(rows))
    return len(rows)
