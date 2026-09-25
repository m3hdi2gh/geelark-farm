"""Every run of a task: what was asked, what came of it, whose fault.

A build has the Phones tab and History; a sign-in has `signins`. A task
had nowhere, so the one question a playground exists to answer - "does
this automation work, and how often?" - could only be answered by
reading logs. This is the row.

Two columns carry the weight. `reason` is the flow's own word, the one
`failures.py` has a sentence for. `blame` is what that word means for
whoever reads a hundred of them: forty runs failing on the exit is a
proxy problem, forty on the device is our bug, and a rate alone cannot
tell them apart. Both are written at the end, from `failures.verdict`.

What never lands here: a secret. The runner writes `spec.public(...)`,
which is the inputs with the fields the task marked secret taken out.
"""
from __future__ import annotations

import json
import logging

from ..config import Settings
from .db import connect

log = logging.getLogger(__name__)


def start(settings: Settings, *, task: str, inputs: dict | None = None,
          phone_id: str = "", serial: str = "", by_id: int | None = None,
          job_id: int | None = None) -> int:
    """Open a row the moment the run begins, and return its id.

    Written before the work rather than after it, so a run that dies
    without finishing is a row saying `running` with a start time -
    which is a thing somebody can see and ask about. A run recorded only
    at the end leaves nothing at all when it is the end that fails.
    """
    with connect(settings) as conn:
        cur = conn.execute(
            "INSERT INTO task_runs (task, inputs, phone_id, serial, by_id,"
            " job_id) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (str(task), json.dumps(inputs or {}), str(phone_id or ""),
             str(serial or ""), by_id, job_id))
        new_id = int(cur.fetchone()[0])
        conn.commit()
    return new_id


def finish(settings: Settings, run_id: int, *, ok: bool, reason: str,
           blame: str = "", detail: str = "", seconds: float = 0.0,
           api_calls: int = 0, trail: tuple[str, ...] | list[str] = (),
           folder: str = "", serial: str = "") -> None:
    """Close the row. Never raises into a run: the work is done, and a
    row that could not be written is not a reason to undo it."""
    try:
        with connect(settings) as conn:
            conn.execute(
                "UPDATE task_runs SET status = %s, ok = %s, reason = %s,"
                " blame = %s, detail = left(%s, 2000), seconds = %s,"
                " api_calls = %s, trail = left(%s, 1000), folder = %s,"
                " serial = coalesce(nullif(%s, ''), serial),"
                " ended_at = now() WHERE id = %s",
                ("done" if ok else "failed", bool(ok), str(reason),
                 str(blame), str(detail), float(seconds), int(api_calls),
                 " > ".join(trail), str(folder), str(serial), int(run_id)))
            conn.commit()
    except Exception as exc:                                      # noqa: BLE001
        log.error("could not close task run %s (%s)", run_id, exc)


def one(settings: Settings, run_id: int) -> dict | None:
    with connect(settings) as conn:
        cur = conn.execute("SELECT * FROM task_runs WHERE id = %s",
                           (int(run_id),))
        row = cur.fetchone()
        cols = [c.name for c in cur.description] if cur.description else []
    return dict(zip(cols, row, strict=False)) if row else None


def recent(settings: Settings, task: str = "", limit: int = 50) -> list[dict]:
    where, params = ("", [int(limit)])
    if task:
        where, params = ("WHERE task = %s", [str(task), int(limit)])
    with connect(settings) as conn:
        cur = conn.execute(
            f"SELECT * FROM task_runs {where} ORDER BY id DESC LIMIT %s",
            params)
        rows = cur.fetchall()
        cols = [c.name for c in cur.description] if cur.description else []
    return [dict(zip(cols, r, strict=False)) for r in rows]


def tally(settings: Settings, task: str = "", days: int = 7) -> dict:
    """How a task has been doing, and - when it has not - whose fault.

    The number a graduation is decided on. `blames` is what makes it
    readable: a rate says something is wrong, and only this says what.
    """
    where = "WHERE started_at > now() - make_interval(days => %s)"
    params: list = [int(days)]
    if task:
        where += " AND task = %s"
        params.append(str(task))
    with connect(settings) as conn:
        cur = conn.execute(
            f"SELECT count(*) AS runs,"
            f" count(*) FILTER (WHERE ok) AS worked,"
            f" coalesce(percentile_cont(0.5) WITHIN GROUP"
            f"   (ORDER BY seconds), 0) AS median_seconds"
            f" FROM task_runs {where}", params)
        head = cur.fetchone() or (0, 0, 0)
        cur = conn.execute(
            f"SELECT reason, blame, count(*) AS n FROM task_runs {where}"
            f"   AND NOT ok GROUP BY reason, blame ORDER BY n DESC", params)
        bad = cur.fetchall()
    runs, worked = int(head[0] or 0), int(head[1] or 0)
    reasons: dict[str, int] = {}
    blames: dict[str, int] = {}
    for reason, blame, n in bad:
        reasons[str(reason)] = reasons.get(str(reason), 0) + int(n)
        blames[str(blame) or "?"] = blames.get(str(blame) or "?", 0) + int(n)
    return {"runs": runs, "worked": worked,
            "rate": (worked / runs) if runs else 0.0,
            "median_seconds": float(head[2] or 0),
            "reasons": reasons, "blames": blames}
