"""Delete the spent and the errored Gmail rows from the pool.

    python scripts/purge_gmails.py            # say what would go, touch nothing
    python scripts/purge_gmails.py --write    # delete them

What goes: every Gmail row that is `used` (spent on a delivered phone), or
that a run left a verdict on (captcha_shown, no_authenticator, wrong
password, ...), or that is unreadable (`error` set). What stays: free rows,
rows on a phone (`in_use`, `ready`), rows a person set aside by hand, and
any row whose serial is a phone still on the farm - a verdict on a row a
live phone is behind is not this script's to judge.

A hard delete, the same one the console's Remove does (pgpool.delete_row).
The history of what each address did stays in `events`; the row itself,
password and all, is gone. One event says how many went and who asked.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geelark_farm.config import Settings                          # noqa: E402
from geelark_farm.store import events as store_events             # noqa: E402
from geelark_farm.store.db import Store                            # noqa: E402
from geelark_farm.web.read import IMPORTED, ROUTINE                # noqa: E402

#: Words that are not errors and not spent: the row is stock, or parked.
KEPT = ("", "in_use", "ready", "set_aside", IMPORTED)


def doomed(store) -> list[dict]:
    """The rows that go, in the order they were added."""
    routine = sorted(ROUTINE["gmail"])
    return store._rows(
        "SELECT r.id, r.address, r.status, r.error, r.seller, r.serial"
        " FROM resources r"
        " WHERE r.kind = 'gmail'"
        "   AND (r.status = 'used' OR r.error IS NOT NULL"
        "        OR (NOT (r.status = ANY(%s)) AND NOT (r.status = ANY(%s))))"
        "   AND NOT EXISTS (SELECT 1 FROM phones p"
        "                   WHERE p.serial = r.serial AND p.done_at IS NULL"
        "                     AND coalesce(r.serial, '') <> '')"
        " ORDER BY r.id", (routine, list(KEPT)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--write", action="store_true",
                        help="delete the rows (the default only lists them)")
    parser.add_argument("--by", default="the operator",
                        help="who asked, for the event")
    args = parser.parse_args(argv)

    settings = Settings.load()
    with Store(settings) as store:
        rows = doomed(store)
        used = [r for r in rows if r["status"] == "used"]
        broken = [r for r in rows if r["error"] is not None]
        verdict = [r for r in rows if r not in used and r not in broken]
        print(f"{len(rows)} Gmail row(s) would go: {len(used)} used, "
              f"{len(verdict)} with a run's verdict, {len(broken)} unreadable")
        by_word: dict[str, int] = {}
        for r in rows:
            word = "unreadable" if r["error"] is not None else (r["status"] or "?")
            by_word[word] = by_word.get(word, 0) + 1
        for word, n in sorted(by_word.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}  {word}")
        kept = store._rows(
            "SELECT count(*) c FROM resources WHERE kind = 'gmail'")[0]["c"]
        print(f"{int(kept) - len(rows)} row(s) stay (free, on a phone, "
              f"set aside)")
        if not args.write:
            print("nothing was deleted - run again with --write")
            return 0
        if not rows:
            return 0
        gone = store._write(
            "DELETE FROM resources WHERE id = ANY(%s) RETURNING id",
            ([int(r["id"]) for r in rows],))
        print(f"deleted {len(gone)} row(s)")
    store_events.emit(
        settings, "stock", status="purged",
        detail=f"{len(gone)} spent and errored Gmail rows deleted by "
               f"{args.by} ({len(used)} used, {len(verdict)} verdicts, "
               f"{len(broken)} unreadable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
