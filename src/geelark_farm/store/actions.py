"""The command queue's storage half: rows in, rows out, nothing executed.

Execution lives in serve's drain, beside the Book and the locks it needs -
this module deliberately cannot reach the sheet, so nothing here can grow
into a second writer by accident.
"""

from __future__ import annotations

import json
import logging
import time

from ..config import Settings
from .db import Store, connect

log = logging.getLogger(__name__)

#: How many a single drain takes. A pass must stay a pass, not become a
#: worker chewing an unbounded backlog while the farm waits.
DRAIN_BATCH = 20


def enqueue(settings: Settings, *, verb: str, payload: dict,
            requested_by: int, idem_key: str) -> int:
    """Insert one command; a duplicate idem_key returns the FIRST row's id.

    That makes a double-submit (double-tap, back-button re-POST, browser
    retry) indistinguishable from success, which is the design: the person
    pressed the button once as far as they are concerned.
    """
    with connect(settings) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO actions (verb, payload, requested_by, idem_key)"
                " VALUES (%s, %s, %s, %s) RETURNING id",
                (verb, json.dumps(payload), requested_by, idem_key))
            new_id = cur.fetchone()[0]
            conn.commit()
            return new_id
        except Exception:                                         # noqa: BLE001
            conn.rollback()
            cur = conn.execute("SELECT id FROM actions WHERE idem_key = %s",
                               (idem_key,))
            row = cur.fetchone()
            if row is not None:
                return row[0]
            raise


def record_refused(settings: Settings, *, verb: str, payload: dict,
                   requested_by: int, reason: str) -> int:
    """A command that never ran because the person may not give it.

    Written as a row all the same - `refused`, with the reason - so the
    Requests page says what was asked and why nothing happened, instead of
    a 403 nobody remembers. Nothing drains it: refused is terminal.
    """
    with connect(settings) as conn:
        cur = conn.execute(
            "INSERT INTO actions (verb, payload, requested_by, status,"
            " result, executed_at) VALUES (%s, %s, %s, 'refused', %s, now())"
            " RETURNING id",
            (verb, json.dumps(payload), requested_by, reason))
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def listing(settings: Settings, *, user_id: int,
            everyone: bool = False, limit: int = 50,
            view: str = "") -> list[dict]:
    """Newest first. `everyone` is the admin's whole queue; off, only the
    person's own rows. `view` narrows to one status (C7's pills)."""
    with Store(settings) as store:
        return store._rows(
            "SELECT a.id, a.verb, a.payload, a.status, a.result, a.detail,"
            " a.requested_at, a.executed_at, a.finished_at,"
            " u.username AS requested_by"
            " FROM actions a JOIN users u ON u.id = a.requested_by"
            " WHERE (%s OR a.requested_by = %s)"
            " AND (%s = '' OR a.status = %s)"
            " ORDER BY a.id DESC LIMIT %s",
            (everyone, user_id, view, view, limit))


def counts(settings: Settings, *, user_id: int,
           everyone: bool = False) -> dict:
    """How many rows each status has, for the pills above the list."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT status, count(*) AS c FROM actions"
            " WHERE (%s OR requested_by = %s) GROUP BY status",
            (everyone, user_id))
    return {r["status"]: r["c"] for r in rows}


def retry(settings: Settings, *, action_id: int, user_id: int,
          is_admin: bool) -> int | str:
    """Queue a failed command again, as a new row that names the old one.

    Only `failed`: a refused one needs a permission, not a retry, and a
    done one is done. Returns the new id, or 'not_failed' / 'not_yours'.
    """
    with connect(settings) as conn:
        cur = conn.execute(
            "SELECT verb, payload, requested_by, status FROM actions"
            " WHERE id = %s", (action_id,))
        row = cur.fetchone()
        if row is None or (not is_admin and row[2] != user_id):
            return "not_yours"
        verb, payload, _by, status = row
        if status != "failed":
            return "not_failed"
        payload = dict(payload or {}, retry_of=action_id)
        cur = conn.execute(
            "INSERT INTO actions (verb, payload, requested_by, idem_key)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (verb, json.dumps(payload), user_id,
             f"retry:{action_id}:{int(time.time())}"))
        new_id = cur.fetchone()[0]
        conn.commit()
        return new_id


def settle(settings: Settings, action_id: int, *, status: str, result: str,
           detail: dict | None = None) -> None:
    """Close a command from outside the drain - the launcher, minutes
    later, when the phone work it started has ended."""
    with connect(settings) as conn:
        finish(conn, action_id, status=status, result=result, detail=detail)


def cancel(settings: Settings, *, action_id: int, user_id: int,
           is_admin: bool) -> str:
    """Withdraw a command that has not been drained yet.

    The undo the queue gives for free: until the pass takes it, pressing
    the button never happened. Returns 'cancelled', 'too_late' (already
    drained - the truthful answer, not an error) or 'not_yours'.
    """
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE actions SET status = 'cancelled',"
            " result = 'cancelled before it ran'"
            " WHERE id = %s AND status = 'queued'"
            " AND (%s OR requested_by = %s) RETURNING id",
            (action_id, is_admin, user_id))
        got = cur.fetchone()
        conn.commit()
        if got is not None:
            return "cancelled"
        cur = conn.execute(
            "SELECT requested_by FROM actions WHERE id = %s", (action_id,))
        row = cur.fetchone()
        if row is None or (not is_admin and row[0] != user_id):
            return "not_yours"
        return "too_late"


# --------------------------------------------------------------- the drain's
def take_batch(conn, *, controls_only: bool) -> list[dict]:
    """Claim up to DRAIN_BATCH queued commands, oldest first, marking them
    running. Runs on the serve pass's own connection and transaction."""
    wanted = ("verb = 'control'" if controls_only else "verb <> 'control'")
    cur = conn.execute(
        f"UPDATE actions SET status = 'running', executed_at = now()"
        f" WHERE id IN (SELECT id FROM actions"
        f"   WHERE status = 'queued' AND {wanted}"
        f"   ORDER BY id FOR UPDATE SKIP LOCKED LIMIT {DRAIN_BATCH})"
        f" RETURNING id, verb, payload, requested_by")
    rows = [dict(zip(("id", "verb", "payload", "requested_by"), r,
                     strict=True)) for r in cur.fetchall()]
    conn.commit()
    return rows


#: The statuses a command never leaves. `running` is not one: a handler
#: that started phone work answers `running`, and the launcher settles the
#: row when the work ends - so finished_at is stamped only on these.
TERMINAL = ("done", "failed", "refused", "cancelled")


def finish(conn, action_id: int, *, status: str, result: str,
           detail: dict | None = None) -> None:
    conn.execute(
        "UPDATE actions SET status = %s, result = %s, detail = %s,"
        " finished_at = CASE WHEN %s THEN now() ELSE finished_at END"
        " WHERE id = %s",
        (status, result, json.dumps(detail) if detail else None,
         status in TERMINAL, action_id))
    conn.commit()
