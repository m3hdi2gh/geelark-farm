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


#: The Postgres channel a queued command rings. The keeper LISTENs on it;
#: a missed notification costs a wait and nothing else, exactly like the
#: in-process bell it stands beside.
NOTIFY_CHANNEL = "geelark_actions"


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
            # The bell, for a keeper in another container: the in-process
            # `signals.queued` cannot reach it, and without this a press
            # waited for the top of the next pass (see serve.Listener).
            # Delivered with the commit, so a listener never wakes to a
            # row it cannot yet see.
            conn.execute(f"NOTIFY {NOTIFY_CHANNEL}")
            conn.commit()
        except Exception as exc:                                  # noqa: BLE001
            conn.rollback()
            cur = conn.execute("SELECT id FROM actions WHERE idem_key = %s",
                               (idem_key,))
            row = cur.fetchone()
            if row is None:
                raise
            log.debug("enqueue of %s hit idem_key %s (%s); answering row %s",
                      verb, idem_key, exc, row[0])
            return row[0]
    # Asking is an event too (C8): the dashboard ticker and the Events
    # page say who asked for what, the moment they asked.
    from . import events

    events.emit(settings, "request", status="queued", user_id=requested_by,
                serial=str(payload.get("serial") or ""),
                detail=f"#{new_id} {verb}: asked by "
                       f"{payload.get('by') or requested_by}")
    return new_id


def pending_for(settings: Settings, *, verb: str, needle: str) -> int | None:
    """The id of a queued or running row of this verb that names the
    same thing (a serial, an exit, an address) - so a second press of
    the same button says "already asked, #240" instead of queueing a
    twin the pass would refuse a minute later."""
    if not needle:
        return None
    with Store(settings) as store:
        rows = store._rows(
            "SELECT id FROM actions WHERE verb = %s"
            " AND status IN ('queued', 'running')"
            " AND payload::text ILIKE %s ORDER BY id LIMIT 1",
            (verb, f"%{needle}%"))
    return int(rows[0]["id"]) if rows else None


def expire_running(conn, *, older_than: float,
                   quick: tuple[str, ...] = (), quick_after: float = 0) -> int:
    """Close rows a restart orphaned: still `running`, taken more than
    `older_than` seconds ago, nothing ever settled them. The phones'
    own stories say what became of the work; the row says why it is
    not still spinning on the Requests page.

    `quick` is the verbs that are over in seconds - the control lane's -
    and they get `quick_after` instead. Measured against a build's clock,
    a boot that a restart orphaned sat on the Requests page spinning for
    two hours, which is a lie about a command that could not have taken
    more than a minute (2026-09-06).
    """
    cur = conn.execute(
        "UPDATE actions SET status = 'failed', finished_at = now(),"
        " result = 'the service restarted while this ran - see the"
        " phones'' stories'"
        " WHERE status = 'running'"
        " AND executed_at < now() - make_interval(secs => CASE"
        "     WHEN verb = ANY(%s) THEN %s ELSE %s END)",
        (list(quick), float(quick_after or older_than), float(older_than)))
    closed = getattr(cur, "rowcount", 0) or 0
    conn.commit()
    return closed


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
    from . import events

    events.emit(settings, "request", status="refused", user_id=requested_by,
                serial=str(payload.get("serial") or ""),
                detail=f"#{new_id} {verb}: {reason}")
    return new_id


#: How many rows the Requests page shows at a time.
PER_PAGE = 50


def one(settings: Settings, action_id: int) -> dict | None:
    """One request by id, for a page that is waiting on it - the Boot
    button's new tab watches its own row until the pass settles it."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id, verb, status, result, detail, requested_by"
            " FROM actions WHERE id = %s", (int(action_id),)).fetchall()
    if not rows:
        return None
    return dict(zip(("id", "verb", "status", "result", "detail",
                     "requested_by"), rows[0], strict=True))


def listing(settings: Settings, *, user_id: int,
            everyone: bool = False, limit: int = PER_PAGE,
            view: str = "", page: int = 1) -> list[dict]:
    """Newest first, `limit` rows from page `page` - plus one more when an
    older page exists, so the caller can offer it without a count. The
    total for "page N of M" comes from `counts`, which the page reads
    anyway for its pills. `everyone` is the admin's whole queue; off,
    only the person's own rows. `view` narrows to one status (C7)."""
    offset = max(0, int(page or 1) - 1) * limit
    with Store(settings) as store:
        return store._rows(
            "SELECT a.id, a.verb, a.payload, a.status, a.result, a.detail,"
            " a.requested_at, a.executed_at, a.finished_at,"
            " coalesce(u.username, c.name, '?') AS requested_by"
            " FROM actions a LEFT JOIN users u ON u.id = a.requested_by"
            " LEFT JOIN api_clients c ON c.id = a.client_id"
            " WHERE (%s OR a.requested_by = %s)"
            " AND (%s = '' OR a.status = %s)"
            " ORDER BY a.id DESC LIMIT %s OFFSET %s",
            (everyone, user_id, view, view, limit + 1, offset))


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
def take_batch(conn, *, controls_only: bool,
               only: tuple[str, ...] | None = None) -> list[dict]:
    """Claim up to DRAIN_BATCH queued commands, oldest first, marking them
    running.

    `only` narrows the claim to a named set of verbs, which is how a second
    drainer takes its own work without a second queue. `FOR UPDATE SKIP
    LOCKED` is what makes two drainers safe, and it was written in from the
    start; until now only one thread had ever used it. Two drainers over
    overlapping sets is fine and deliberate - whoever reaches a row first
    takes it - so the pass stays a backstop for everything the lane can do.
    """
    if only is not None:
        wanted = "verb = ANY(%s)"
        params: tuple = (list(only),)
    else:
        wanted = "verb = 'control'" if controls_only else "verb <> 'control'"
        params = ()
    cur = conn.execute(
        f"UPDATE actions SET status = 'running', executed_at = now()"
        f" WHERE id IN (SELECT id FROM actions"
        f"   WHERE status = 'queued' AND {wanted}"
        f"   ORDER BY id FOR UPDATE SKIP LOCKED LIMIT {DRAIN_BATCH})"
        f" RETURNING id, verb, payload, requested_by", *([params] if params else []))
    rows = [dict(zip(("id", "verb", "payload", "requested_by"), r,
                     strict=True)) for r in cur.fetchall()]
    conn.commit()
    return rows


#: The statuses a command never leaves. `running` is not one: a handler
#: that started phone work answers `running`, and the launcher settles the
#: row when the work ends - so finished_at is stamped only on these.
TERMINAL = ("done", "failed", "refused", "cancelled")


def finish(conn, action_id: int, *, status: str, result: str,
           detail: dict | None = None) -> bool:
    """Write a command's outcome. False when the row was already closed and
    this would have re-opened it.

    A closed row cannot be re-opened, and that guard is here rather than at
    the caller because two writers reach this from different connections. A
    login is settled by the launcher when its phones end - `settle`, on its
    own connection - and with SERVE_CONCURRENT off that launcher runs
    *inside* the handler, so the drain then wrote the handler's own
    "running" straight over the finished row. `running` is not terminal, so
    nothing closed it again: the Requests page showed a login that had ended
    minutes ago as still going, until `expire_running` swept it two hours
    later and said the service had restarted, which was untrue
    (2026-09-06, found by audit).

    Terminal over terminal is still allowed - the last word on a row that
    is already closed is a correction, not a re-opening.
    """
    cur = conn.execute(
        "UPDATE actions SET status = %s, result = %s, detail = %s,"
        " finished_at = CASE WHEN %s THEN now() ELSE finished_at END"
        " WHERE id = %s AND (finished_at IS NULL OR %s)",
        (status, result, json.dumps(detail) if detail else None,
         status in TERMINAL, action_id, status in TERMINAL))
    conn.commit()
    return bool(getattr(cur, "rowcount", 1))
