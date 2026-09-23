"""The origin checker's queue: an address in, how its account was made out.

One row per address somebody asked about. The console queues a paste as a
*run*; a checker in a container of its own takes rows one at a time with
`FOR UPDATE SKIP LOCKED` and writes the answer back on the row.

Not the `jobs` table on purpose. Nothing here touches a phone, a pool or
the builder, and a checker sharing the build queue would stop answering
the moment somebody paused building (2026-09-24).

**The cache is the point of the run/queue split.** Customers send the
same addresses again, and a usable exit is the scarce thing: an address
answered in the last few days is copied into the new run at once,
`from_cache`, and no browser is spent on it. Only the three real answers
are cached - an `unclear` teaches nothing, and serving it again would
turn one bad minute on one exit into a permanent verdict.
"""

from __future__ import annotations

import logging

from ..config import Settings
from .db import connect

log = logging.getLogger(__name__)

#: The answers a check can end with. `unclear` is an answer too - it is
#: what the page offers a retry on - but it is never cached.
VERDICTS = ("google", "password", "none", "unclear")
KEEPABLE = ("google", "password", "none")

#: How long an answer stands for. Seven days: long enough that a customer
#: re-sending last week's list costs nothing, short enough that somebody
#: who has since changed how they sign in is asked about again.
CACHE_DAYS = 7

#: How long a row's history is kept. These are other people's addresses
#: and there is no reason to hold them for ever (2026-09-24).
KEEP_DAYS = 90

_COLUMNS = ("id, address, run_id, status, verdict, reason, exit_name,"
            " cleared, from_cache, created_at, settled_at, seconds")


def _row(values) -> dict:
    return dict(zip([c.strip() for c in _COLUMNS.split(",")], values,
                    strict=True))


def enqueue(settings: Settings, addresses: list[str], *,
            asked_by: int | None = None, cache_days: int = CACHE_DAYS,
            run_id: int | None = None) -> dict:
    """Queue a paste as one run. Returns the run and what it cost.

    An address already answered within `cache_days` is written straight
    in as settled, so the page shows it immediately and the run's list is
    complete. `cache_days=0` asks about every one of them again.
    """
    wanted, seen = [], set()
    for raw in addresses:
        address = str(raw or "").strip()
        low = address.lower()
        # A paste with the same address twice is one question, and
        # spending two exits on it would be the operator paying for
        # their own typing (2026-09-24).
        if address and low not in seen:
            seen.add(low)
            wanted.append(address)
    if not wanted:
        return {"run_id": run_id or 0, "queued": 0, "cached": 0,
                "addresses": []}

    queued = cached = 0
    with connect(settings) as conn:
        if run_id is None:
            cur = conn.execute("SELECT nextval('origin_run_id')")
            run_id = int(cur.fetchone()[0])
        known: dict[str, tuple] = {}
        if cache_days > 0:
            cur = conn.execute(
                "SELECT DISTINCT ON (lower(address)) lower(address), verdict,"
                " reason FROM origin_checks"
                " WHERE status = 'done' AND verdict = ANY(%s)"
                "   AND lower(address) = ANY(%s)"
                "   AND settled_at > now() - make_interval(days => %s)"
                " ORDER BY lower(address), settled_at DESC",
                (list(KEEPABLE), [a.lower() for a in wanted],
                 int(cache_days)))
            known = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        for address in wanted:
            had = known.get(address.lower())
            if had:
                conn.execute(
                    "INSERT INTO origin_checks (address, run_id, asked_by,"
                    " status, verdict, reason, from_cache, settled_at)"
                    " VALUES (%s, %s, %s, 'done', %s, %s, true, now())",
                    (address, run_id, asked_by, had[0], had[1]))
                cached += 1
            else:
                conn.execute(
                    "INSERT INTO origin_checks (address, run_id, asked_by)"
                    " VALUES (%s, %s, %s)", (address, run_id, asked_by))
                queued += 1
        conn.commit()
    log.info("origin run %s: %d to ask about, %d already known",
             run_id, queued, cached)
    return {"run_id": int(run_id), "queued": queued, "cached": cached,
            "addresses": wanted}


def take(settings: Settings, worker: str, limit: int = 1) -> list[dict]:
    """Up to `limit` queued checks, now this worker's.

    SKIP LOCKED for the same reason the build queue has it: two checkers
    must never take one row. Oldest first, so a run finishes before the
    next one starts and the page fills top to bottom.
    """
    if limit < 1:
        return []
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE origin_checks SET status = 'running', claimed_by = %s,"
            " claimed_at = now()"
            " WHERE id IN (SELECT id FROM origin_checks"
            "              WHERE status = 'queued'"
            "              ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %s)"
            f" RETURNING {_COLUMNS}", (worker, limit))
        rows = [_row(r) for r in cur.fetchall()]
        conn.commit()
    return rows


def settle(settings: Settings, check_id: int, verdict: str, reason: str = "",
           *, exit_name: str = "", cleared: bool = False,
           seconds: float = 0.0) -> None:
    """What became of one check. An unknown verdict is written `unclear`
    rather than refused: the checker must always be able to close a row,
    or a crash leaves it `running` for ever."""
    word = verdict if verdict in VERDICTS else "unclear"
    if word != verdict:
        reason = f"{reason} (the checker said {verdict!r})".strip()
    with connect(settings) as conn:
        conn.execute(
            "UPDATE origin_checks SET status = 'done', verdict = %s,"
            " reason = %s, exit_name = %s, cleared = %s, seconds = %s,"
            " settled_at = now() WHERE id = %s",
            (word, (reason or "")[:400], (exit_name or "")[:40],
             bool(cleared), float(seconds), int(check_id)))
        conn.commit()


def retry(settings: Settings, run_id: int) -> int:
    """Put this run's unclear rows back in the queue, as they are.

    The same row rather than a new one: the operator is asking the same
    question a second time, and a second row would double the run's list
    and the counts on it.
    """
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE origin_checks SET status = 'queued', verdict = '',"
            " reason = '', claimed_by = '', claimed_at = NULL,"
            " settled_at = NULL, from_cache = false"
            " WHERE run_id = %s AND status = 'done' AND verdict = 'unclear'",
            (int(run_id),))
        moved = cur.rowcount
        conn.commit()
    return int(moved or 0)


def run(settings: Settings, run_id: int) -> list[dict]:
    """One run's rows, oldest first - the page's whole list."""
    with connect(settings) as conn:
        cur = conn.execute(
            f"SELECT {_COLUMNS} FROM origin_checks WHERE run_id = %s"
            " ORDER BY id", (int(run_id),))
        rows = [_row(r) for r in cur.fetchall()]
        conn.rollback()
    return rows


def latest_run(settings: Settings) -> int:
    """The newest run there is, or 0 - what the page opens on."""
    with connect(settings) as conn:
        cur = conn.execute("SELECT max(run_id) FROM origin_checks")
        got = cur.fetchone()
        conn.rollback()
    return int(got[0]) if got and got[0] is not None else 0


def counts(settings: Settings, run_id: int) -> dict:
    """The run's tally, in the words the page's filters use."""
    with connect(settings) as conn:
        cur = conn.execute(
            "SELECT count(*) AS all,"
            " count(*) FILTER (WHERE verdict = 'google') AS google,"
            " count(*) FILTER (WHERE verdict = 'password') AS password,"
            " count(*) FILTER (WHERE verdict = 'none') AS none,"
            " count(*) FILTER (WHERE verdict = 'unclear') AS unclear,"
            " count(*) FILTER (WHERE status <> 'done') AS working"
            " FROM origin_checks WHERE run_id = %s", (int(run_id),))
        got = cur.fetchone()
        conn.rollback()
    names = ("all", "google", "password", "none", "unclear", "working")
    return {name: int(n or 0) for name, n in zip(names, got or (), strict=False)}


def purge(settings: Settings, keep_days: int = KEEP_DAYS) -> int:
    """Forget checks older than `keep_days`. These are other people's
    addresses; holding them for ever is a choice nobody made."""
    with connect(settings) as conn:
        cur = conn.execute(
            "DELETE FROM origin_checks"
            " WHERE created_at < now() - make_interval(days => %s)",
            (int(keep_days),))
        gone = cur.rowcount
        conn.commit()
    if gone:
        log.info("forgot %d origin check(s) older than %d days",
                 gone, keep_days)
    return int(gone or 0)
