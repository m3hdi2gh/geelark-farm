"""Scale-out step 5: prove, against a real Postgres, that builders never
share a job and processes never lose a phone claim.

Runs the store's own statements - the `jobs.take` shape with FOR UPDATE
SKIP LOCKED, and the `phone_claims` upsert - from N threads at once on
throw-away tables, then checks the outcome. Nothing of the farm's own
tables is touched. Run inside a container:

    python scripts/soak_scale.py --builders 4 --jobs 200 --claims 300
"""

from __future__ import annotations

import argparse
import threading
import time
from collections import Counter

from geelark_farm.config import Settings
from geelark_farm.store import db


def soak_jobs(settings: Settings, builders: int, total: int,
              hold: float = 0.02) -> tuple[bool, str]:
    with db.connect(settings) as conn:
        conn.execute("DROP TABLE IF EXISTS jobs_soak")
        conn.execute(
            "CREATE TABLE jobs_soak (id bigint GENERATED ALWAYS AS IDENTITY"
            " PRIMARY KEY, status text NOT NULL DEFAULT 'queued',"
            " claimed_by text)")
        for _ in range(total):
            conn.execute("INSERT INTO jobs_soak DEFAULT VALUES")
        conn.commit()

    taken: dict[str, list[int]] = {}
    lock = threading.Lock()

    def builder(name: str) -> None:
        mine: list[int] = []
        while True:
            with db.connect(settings) as conn:
                cur = conn.execute(
                    "UPDATE jobs_soak SET status = 'running', claimed_by = %s"
                    " WHERE id IN (SELECT id FROM jobs_soak WHERE status = 'queued'"
                    "              ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %s)"
                    " RETURNING id", (name, 3))
                rows = [r[0] for r in cur.fetchall()]
                conn.commit()
            if not rows:
                break
            mine.extend(rows)
            time.sleep(hold)          # a build is not free; let the others in
        with lock:
            taken[name] = mine

    threads = [threading.Thread(target=builder, args=(f"b{i}",))
               for i in range(builders)]
    started = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    took = time.monotonic() - started
    every = [j for ids in taken.values() for j in ids]
    dupes = [j for j, n in Counter(every).items() if n > 1]
    with db.connect(settings) as conn:
        cur = conn.execute("SELECT count(*) FROM jobs_soak WHERE status = 'queued'")
        left = cur.fetchone()[0]
        conn.execute("DROP TABLE jobs_soak")
        conn.commit()
    ok = not dupes and len(every) == total and left == 0
    spread = ", ".join(f"{k}={len(v)}" for k, v in sorted(taken.items()))
    return ok, (f"{builders} builders took {len(every)}/{total} jobs in "
                f"{took:.1f}s, {len(dupes)} taken twice, {left} left; {spread}")


def soak_claims(settings: Settings, workers: int, total: int) -> tuple[bool, str]:
    with db.connect(settings) as conn:
        conn.execute("DROP TABLE IF EXISTS claims_soak")
        conn.execute(
            "CREATE TABLE claims_soak (phone_id text PRIMARY KEY,"
            " claimed_at double precision, released_at double precision,"
            " claimed_by text NOT NULL DEFAULT '')")
        conn.commit()

    def worker(name: str, mine: list[str]) -> None:
        for phone_id in mine:
            with db.connect(settings) as conn:
                conn.execute(
                    "INSERT INTO claims_soak (phone_id, claimed_at, claimed_by)"
                    " VALUES (%s, %s, %s) ON CONFLICT (phone_id) DO UPDATE SET"
                    " claimed_at = EXCLUDED.claimed_at, released_at = NULL,"
                    " claimed_by = EXCLUDED.claimed_by", (phone_id, time.time(), name))
                conn.commit()
        # Beat only what is mine, as the ledger does.
        with db.connect(settings) as conn:
            conn.execute(
                "UPDATE claims_soak SET claimed_at = %s"
                " WHERE phone_id = ANY(%s) AND released_at IS NULL",
                (time.time(), mine))
            conn.commit()
        for phone_id in mine[::2]:
            with db.connect(settings) as conn:
                conn.execute("UPDATE claims_soak SET released_at = %s"
                             " WHERE phone_id = %s", (time.time(), phone_id))
                conn.commit()

    plan = {f"w{i}": [f"P{i}-{n}" for n in range(total // workers)]
            for i in range(workers)}
    threads = [threading.Thread(target=worker, args=(k, v)) for k, v in plan.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with db.connect(settings) as conn:
        cur = conn.execute(
            "SELECT claimed_by, count(*) FILTER (WHERE released_at IS NULL),"
            " count(*) FROM claims_soak GROUP BY claimed_by ORDER BY claimed_by")
        rows = cur.fetchall()
        conn.execute("DROP TABLE claims_soak")
        conn.commit()
    expected_total = sum(len(v) for v in plan.values())
    got_total = sum(r[2] for r in rows)
    held = {r[0]: r[1] for r in rows}
    want = {k: len(v) - len(v[::2]) for k, v in plan.items()}
    ok = got_total == expected_total and held == want
    return ok, (f"{workers} workers, {got_total}/{expected_total} claims kept, "
                f"held per worker {held} (wanted {want})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--builders", type=int, default=4)
    ap.add_argument("--jobs", type=int, default=200)
    ap.add_argument("--claims", type=int, default=300)
    args = ap.parse_args()
    settings = Settings.load()
    ok1, said1 = soak_jobs(settings, args.builders, args.jobs)
    print(("PASS " if ok1 else "FAIL ") + said1)
    ok2, said2 = soak_claims(settings, args.builders, args.claims)
    print(("PASS " if ok2 else "FAIL ") + said2)
    return 0 if ok1 and ok2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
