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
from .db import Store, connect

log = logging.getLogger(__name__)

#: What a wish can be. `running` is written by the pass that took it, so a
#: pass that dies mid-build leaves a row saying so rather than one that
#: looks untouched and is taken again by the next pass.
STATES = ("queued", "running", "done", "failed")


def ask(settings: Settings, *, gmail: str = "", proxy_name: str = "",
        install_app: bool = True, app_account: str = "",
        requested_by: int | None = None, app: str | None = None,
        no_gmail: bool = False, purpose: str = "", station: bool = False,
        carry_address: str = "", carry_password: str = "") -> int:
    """Write one wish. Returns its id, which is what the page says back.

    `app` is which app the phone gets - '' for none, 'chatgpt', 'spotify',
    'claude'. Left None it follows `install_app`, which is what every
    older caller means: the app, or no app. `no_gmail` asks for a bare
    phone: nothing signed in, and so no app and no account either.

    `station` marks a wish pressed on the Station, which lands on its
    asker's Station. `carry_address` and `carry_password` are an Other
    phone's app account: carried for its Live tab, never signed in."""
    if app is None:
        app = "chatgpt" if install_app else ""
    if no_gmail:
        # The one account a bare phone carries: a `normal` Spotify one,
        # which wants exactly that phone (2026-09-17).
        gmail = ""
        if app != "spotify":
            app, app_account = "", ""
    with Store(settings) as store:
        rows = store._write(
            "INSERT INTO wanted_builds"
            " (gmail, proxy_name, install_app, app_account, requested_by,"
            "  station, carry_address, carry_password, app, no_gmail, purpose)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (gmail.strip(), proxy_name.strip(), bool(app),
             app_account.strip(), requested_by, bool(station),
             str(carry_address or "").strip(), str(carry_password or ""),
             app, bool(no_gmail), str(purpose or "").strip().lower()))
    return int(rows[0]["id"])


def on_its_way(settings: Settings | None, *addresses: str) -> str:
    """Which of these addresses a wish still open already names, or "".

    Nothing is claimed when a wish is written - the build claims its
    Gmail and its account by name when it reaches them, minutes later -
    so until then the rows read free, and a second press on the same
    account asked for a second phone (the operator, 2026-09-27). No
    store, no answer: a caller without one has nothing to ask."""
    wanted = [a.strip().lower() for a in addresses if a and a.strip()]
    if (settings is None or not wanted
            or not getattr(settings, "store_enabled", False)):
        return ""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT lower(app_account) AS app_account, lower(gmail) AS gmail"
            " FROM wanted_builds WHERE status IN ('queued', 'running')")
    named = {str(r[k]) for r in rows for k in ("app_account", "gmail")
             if r.get(k)}
    return next((a for a in wanted if a in named), "")


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
            "UPDATE wanted_builds SET status = 'running', updated_at = now()"
            " WHERE id IN (SELECT id FROM wanted_builds"
            "              WHERE status = 'queued'"
            "              ORDER BY created_at, id LIMIT %s"
            "              FOR UPDATE SKIP LOCKED)"
            " RETURNING id, gmail, proxy_name, install_app, app_account, app,"
            " requested_by, no_gmail, purpose",
            (max(1, int(limit)),))


def settle(settings: Settings, wanted_id: int, *, ok: bool,
           serial: str = "", detail: str = "") -> None:
    """What became of one wish. Never fatal: a build that worked must not be
    reported as failed because the row saying so could not be written."""
    try:
        with Store(settings) as store:
            store._write(
                "UPDATE wanted_builds SET status = %s, serial = %s,"
                " detail = %s, ended_at = now(), updated_at = now()"
                " WHERE id = %s RETURNING id",
                ("done" if ok else "failed", serial, detail[:400],
                 int(wanted_id)))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not record what became of wanted build %s (%s)",
                    wanted_id, exc)


def dismiss(settings: Settings, wanted_id: int, *, user_id: int | None,
            admin: bool = False) -> bool:
    """Take a failed wish off the dashboard. Whoever asked may, and an
    admin may; nobody else - the row is theirs to read. True when a row
    changed."""
    with Store(settings) as store:
        rows = store._write(
            "UPDATE wanted_builds SET dismissed_at = now(), updated_at = now()"
            " WHERE id = %s AND status = 'failed' AND dismissed_at IS NULL"
            "   AND (%s OR requested_by = %s)"
            " RETURNING id",
            (int(wanted_id), bool(admin), user_id))
    return bool(rows)


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
    `in_use`, and the same fix. A wish whose build job is still open is not
    dead, and one that was called off is not put back (rev 42).
    """
    with Store(settings) as store:
        rows = store._write(
            "UPDATE wanted_builds SET status = 'queued', updated_at = now()"
            " WHERE status = 'running'"
            "   AND created_at < now() - (interval '1 minute' * %s)"
            "   AND called_off_at IS NULL"
            "   AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind = 'build'"
            "                    AND j.status IN ('queued', 'running')"
            "                    AND j.payload->'want'->>'wanted_id'"
            "                        = wanted_builds.id::text)"
            " RETURNING id", (max(1, int(older_than_minutes)),))
    if rows:
        log.warning("%d hand-built phone request(s) were left running by a "
                    "pass that did not come back; they are queued again",
                    len(rows))
    return len(rows)


def attach(settings: Settings, wanted_id: int, serial: str) -> bool:
    """Pair a wish with the phone its build created. True when the build
    must stop: the wish was called off, or it is no longer running. Never
    raises; a failure answers False."""
    try:
        with Store(settings) as store:
            rows = store._write(
                "UPDATE wanted_builds SET serial = %s, updated_at = now()"
                " WHERE id = %s"
                " RETURNING (called_off_at IS NOT NULL OR status <> 'running')"
                " AS called_off", (str(serial), int(wanted_id)))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not pair wanted build %s with phone %s (%s)",
                    wanted_id, serial, exc)
        return False
    return bool(rows and rows[0]["called_off"])


def called_off(settings: Settings, wanted_id: int) -> bool:
    """Whether the wish was called off. Never raises; a failure answers
    False."""
    try:
        with Store(settings) as store:
            rows = store._rows(
                "SELECT called_off_at IS NOT NULL AS off FROM wanted_builds"
                " WHERE id = %s", (int(wanted_id),))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not read whether wanted build %s was called off "
                    "(%s)", wanted_id, exc)
        return False
    return bool(rows and rows[0]["off"])


#: A queued wish called off: failed, dismissed and marked, in one write.
_CALLED_OFF_QUEUED = (
    "UPDATE wanted_builds SET status = 'failed', detail = %s, ended_at = now(),"
    " dismissed_at = now(), called_off_at = now(), called_off_by = %s,"
    " updated_at = now()"
    " WHERE id = %s")


def call_off(settings: Settings, wanted_id: int, *, by_id: int, by: str,
             admin: bool) -> dict | None:
    """Call a wish off, in one transaction. None when there is no such
    wish or it is not this person's (an admin may call off any). Otherwise
    `{"stage", "serial", "proxy_name", "status", "id"}`, the stage being
    `ended`, `already`, `queued`, `job_cancelled`, `landed` or `running`."""
    wid = int(wanted_id)
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT id, status, serial, proxy_name, requested_by, called_off_at"
            " FROM wanted_builds WHERE id = %s FOR UPDATE", (wid,)).fetchone()
        if row is None or (not admin and row[4] != by_id):
            conn.rollback()
            return None
        status, serial = str(row[1]), str(row[2] or "")
        proxy_name = str(row[3] or "")
        said = f"called off by {by}"
        if status in ("done", "failed"):
            stage = "ended"
        elif row[5] is not None:
            stage = "already"
        elif status == "queued":
            conn.execute(_CALLED_OFF_QUEUED, (said, by_id, wid))
            stage = "queued"
        else:
            cancelled = conn.execute(
                "UPDATE jobs SET status = 'failed', seen = true, done_at = now(),"
                " result = jsonb_build_object('ok', false, 'worked', false,"
                "          'status', 'stopped_by_hand', 'serial', '',"
                "          'detail', 'called off', 'seconds', 0,"
                "          'wanted_id', %s::bigint)"
                " WHERE kind = 'build' AND status = 'queued'"
                "   AND payload->'want'->>'wanted_id' = %s::text"
                " RETURNING id", (wid, str(wid))).fetchall()
            running = int(conn.execute(
                "SELECT count(*) FROM jobs WHERE kind = 'build'"
                " AND status = 'running'"
                " AND payload->'want'->>'wanted_id' = %s::text",
                (str(wid),)).fetchone()[0])
            if cancelled and not running:
                conn.execute(_CALLED_OFF_QUEUED, (said, by_id, wid))
                stage = "job_cancelled"
            else:
                phone = None
                if serial:
                    phone = conn.execute(
                        "SELECT status FROM phones WHERE serial = %s"
                        " AND done_at IS NULL ORDER BY id DESC LIMIT 1",
                        (serial,)).fetchone()
                if phone is not None and phone[0] != "building":
                    stage = "landed"
                else:
                    conn.execute(
                        "UPDATE wanted_builds SET called_off_at = now(),"
                        " called_off_by = %s, dismissed_at = now(),"
                        " updated_at = now() WHERE id = %s", (by_id, wid))
                    stage = "running"
        conn.commit()
    return {"stage": stage, "serial": serial, "proxy_name": proxy_name,
            "status": status, "id": wid}


def others_name_exit(settings: Settings, proxy_name: str,
                     wanted_id: int) -> bool:
    """Whether a one-off exit is still named by another open wish or a
    live phone."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT (EXISTS (SELECT 1 FROM wanted_builds WHERE id <> %(wid)s"
            "                 AND status IN ('queued', 'running')"
            "                 AND proxy_name = %(name)s)"
            "        OR EXISTS (SELECT 1 FROM phones WHERE done_at IS NULL"
            "                    AND proxy_name = %(name)s)) AS named",
            {"wid": int(wanted_id), "name": str(proxy_name)})
    return bool(rows and rows[0]["named"])
