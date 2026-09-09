"""The build queue: what the keeper orders, and what a builder carries out.

One row per phone to work on - a `build` from the pools, or a `finish` of
a phone that has its Gmail and wants an account. The keeper writes rows
and reads results; a builder in a container of its own takes rows with
`FOR UPDATE SKIP LOCKED`, so two builders can never take the same one,
and reports back on the row. Restarting the keeper or the console then
touches no build, and restarting a builder loses only the rows it was
on - which `lose_stale` names when their heartbeat stops (the operator,
2026-09-09: "why should a change to a page wait for a build?").

A row's payload is JSON: `{"want": {...}}` for a hand-built phone (the
`builder.Wanted` fields), `{}` for the keeper's own; `{"phone": {...}}`
for a finish, with `account_address` in it when a command chose the
account. Nothing here imports the builder: the shapes are dicts, and the
builder role turns them back into what `_run_jobs` takes.
"""

from __future__ import annotations

import json
import logging

from ..config import Settings
from .db import connect

log = logging.getLogger(__name__)

#: The Postgres channel a queued job rings; a builder LISTENs on it.
NOTIFY_CHANNEL = "geelark_jobs"

_COLUMNS = "id, kind, payload, action_id, status, claimed_by"


def _row(cols: str, values) -> dict:
    out = dict(zip([c.strip() for c in cols.split(",")], values))
    if isinstance(out.get("payload"), str):
        out["payload"] = json.loads(out["payload"])
    return out


def queue(settings: Settings, kind: str, payload: dict | None = None, *,
          action_id: int | None = None) -> int:
    """Order one job. Rings the builders' bell with the commit."""
    with connect(settings) as conn:
        cur = conn.execute(
            "INSERT INTO jobs (kind, payload, action_id) VALUES (%s, %s, %s)"
            " RETURNING id", (kind, json.dumps(payload or {}), action_id))
        new_id = cur.fetchone()[0]
        conn.execute(f"NOTIFY {NOTIFY_CHANNEL}")
        conn.commit()
    return new_id


def take(settings: Settings, worker: str, limit: int = 1) -> list[dict]:
    """Up to `limit` queued jobs, now this worker's. SKIP LOCKED is what
    keeps two builders off one row: each takes what the other has not
    locked, and a row is `running` from the moment it is handed out."""
    if limit < 1:
        return []
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'running', claimed_by = %s,"
            " claimed_at = now(), heartbeat_at = now()"
            " WHERE id IN (SELECT id FROM jobs WHERE status = 'queued'"
            "              ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %s)"
            f" RETURNING {_COLUMNS}", (worker, limit))
        rows = [_row(_COLUMNS, r) for r in cur.fetchall()]
        conn.commit()
    return rows


def beat(settings: Settings, ids: list[int]) -> int:
    """Say "still on these" - what `lose_stale` reads."""
    if not ids:
        return 0
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE jobs SET heartbeat_at = now()"
            " WHERE id = ANY(%s) AND status = 'running'", (list(ids),))
        moved = cur.rowcount
        conn.commit()
    return moved


def finish(settings: Settings, job_id: int, *, ok: bool, status: str,
           serial: str = "", detail: str = "", seconds: float = 0.0,
           wanted_id: int | None = None, worked: bool | None = None) -> None:
    """What became of one job. The keeper reads it through `unseen`.

    `worked` is the breaker's word for it: a phone kept warm on purpose
    is not `ok` as a Build, and read as `failed` here for a night
    (2026-09-10). Done is ok or worked; the result keeps both."""
    done = bool(ok) or bool(worked)
    result = {"ok": bool(ok), "worked": done, "status": status,
              "serial": str(serial or ""), "detail": (detail or "")[:400],
              "seconds": round(seconds), "wanted_id": wanted_id}
    with connect(settings) as conn:
        conn.execute(
            "UPDATE jobs SET status = %s, result = %s, done_at = now()"
            " WHERE id = %s",
            ("done" if done else "failed", json.dumps(result), job_id))
        conn.commit()


def counts(settings: Settings) -> tuple[int, int]:
    """(builds, finishes) queued or running - what the keeper counts as
    already on its way, so it does not order them twice."""
    with connect(settings) as conn:
        cur = conn.execute(
            "SELECT count(*) FILTER (WHERE kind = 'build'),"
            " count(*) FILTER (WHERE kind = 'finish')"
            " FROM jobs WHERE status IN ('queued', 'running')")
        builds, finishes = cur.fetchone()
        conn.rollback()
    return int(builds or 0), int(finishes or 0)


def unseen(settings: Settings) -> list[dict]:
    """Finished jobs the keeper has not yet taken into account - for the
    breaker and the events. Marked seen with `mark_seen`."""
    cols = "id, kind, result, action_id"
    with connect(settings) as conn:
        cur = conn.execute(
            f"SELECT {cols} FROM jobs WHERE status IN ('done', 'failed', 'lost')"
            " AND NOT seen ORDER BY id")
        rows = []
        for r in cur.fetchall():
            row = dict(zip([c.strip() for c in cols.split(",")], r))
            if isinstance(row.get("result"), str):
                row["result"] = json.loads(row["result"])
            rows.append(row)
        conn.rollback()
    return rows


def mark_seen(settings: Settings, ids: list[int]) -> None:
    if not ids:
        return
    with connect(settings) as conn:
        conn.execute("UPDATE jobs SET seen = true WHERE id = ANY(%s)",
                     (list(ids),))
        conn.commit()


def lose_stale(settings: Settings, older_than: float) -> list[int]:
    """Running jobs whose builder stopped beating: marked `lost`, so the
    keeper counts them as gone and orders again. The phone a lost job
    left behind is `settle_abandoned`'s, as any dead run's is."""
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'lost', done_at = now(),"
            " result = COALESCE(result, '{}'::jsonb)"
            "   || jsonb_build_object('ok', false, 'status', 'builder_lost')"
            " WHERE status = 'running'"
            "   AND heartbeat_at < now() - make_interval(secs => %s)"
            " RETURNING id", (float(older_than),))
        ids = [r[0] for r in cur.fetchall()]
        conn.commit()
    if ids:
        log.warning("%d job(s) lost their builder (no heartbeat for %.0fs): "
                    "%s", len(ids), older_than, ids)
    return ids
