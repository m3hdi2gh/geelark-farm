"""Delete the spent and the errored rows of one pool.

    python scripts/purge_gmails.py                  # Gmail: say what would go
    python scripts/purge_gmails.py --write          # Gmail: delete them
    python scripts/purge_gmails.py --kind app       # the GPT accounts, likewise

What goes: every row that is spent (`used` for a Gmail, `delivered` for a
GPT account), or
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

#: The word each pool spends a row with.
SPENT = {"gmail": "used", "app": "delivered"}


def doomed(store, kind: str) -> list[dict]:
    """The rows that go, in the order they were added."""
    routine = sorted(ROUTINE[kind])
    return store._rows(
        "SELECT r.id, r.address, r.status, r.error, r.seller, r.serial"
        " FROM resources r"
        " WHERE r.kind = %s"
        "   AND (r.status = %s OR r.error IS NOT NULL"
        "        OR (NOT (r.status = ANY(%s)) AND NOT (r.status = ANY(%s))))"
        "   AND NOT EXISTS (SELECT 1 FROM phones p"
        "                   WHERE p.serial = r.serial AND p.done_at IS NULL"
        "                     AND coalesce(r.serial, '') <> '')"
        # A row on the retry ladder is not a verdict, it is a wait: it
        # comes back on its own (store.ladder, 2026-09-10).
        "   AND r.retry_after IS NULL"
        # A row on the refund list is money somebody is still
        # owed; it leaves when that is settled (2026-09-12).
        "   AND coalesce(r.refund_state, '') = ''"
        " ORDER BY r.id", (kind, SPENT[kind], routine, list(KEPT)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--write", action="store_true",
                        help="delete the rows (the default only lists them)")
    parser.add_argument("--by", default="the operator",
                        help="who asked, for the event")
    parser.add_argument("--kind", choices=sorted(SPENT), default="gmail",
                        help="which pool: gmail (default) or app")
    args = parser.parse_args(argv)
    kind, spent = args.kind, SPENT[args.kind]
    name = {"gmail": "Gmail", "app": "GPT account"}[kind]

    settings = Settings.load()
    with Store(settings) as store:
        rows = doomed(store, kind)
        used = [r for r in rows if r["status"] == spent]
        broken = [r for r in rows if r["error"] is not None]
        verdict = [r for r in rows if r not in used and r not in broken]
        print(f"{len(rows)} {name} row(s) would go: {len(used)} {spent}, "
              f"{len(verdict)} with a run's verdict, {len(broken)} unreadable")
        by_word: dict[str, int] = {}
        for r in rows:
            word = "unreadable" if r["error"] is not None else (r["status"] or "?")
            by_word[word] = by_word.get(word, 0) + 1
        for word, n in sorted(by_word.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}  {word}")
        kept = store._rows(
            "SELECT count(*) c FROM resources WHERE kind = %s", (kind,))[0]["c"]
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
        detail=f"{len(gone)} spent and errored {name} rows deleted by "
               f"{args.by} ({len(used)} {spent}, {len(verdict)} verdicts, "
               f"{len(broken)} unreadable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
