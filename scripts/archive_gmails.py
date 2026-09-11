"""Archive the spent and the errored rows of one pool.

    python scripts/archive_gmails.py                 # Gmail: say what would go
    python scripts/archive_gmails.py --write         # Gmail: archive them
    python scripts/archive_gmails.py --kind app      # the GPT accounts, likewise
    python scripts/archive_gmails.py --restore 41,42 # put two rows back
    python scripts/archive_gmails.py --write --with-ladder   # and the waiters

What goes is exactly what `purge_gmails.py` would delete - the same
`doomed`, so the same rules: spent rows, rows a run left a verdict on,
unreadable rows; never a free row, a row on a live phone, a row somebody
set aside by hand, or a row waiting on the retry ladder - the ladder's
rows come back on their own and are only ever taken by `--with-ladder`,
which is a thing to be asked for, not a default.

What is different is where it goes. The purge deletes; this moves the
whole row into `resources_archive` (store.pool_archive), so the address,
the password, the authenticator key and the verdict it left with are all
still readable, and `--restore` puts a row back as free stock. The pool,
the console and the builder see what they would have seen after a purge:
the row is not stock any more.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from purge_gmails import SPENT, doomed  # noqa: E402

from geelark_farm.config import Settings  # noqa: E402
from geelark_farm.store import events as store_events  # noqa: E402
from geelark_farm.store import pool_archive  # noqa: E402
from geelark_farm.store.db import Store  # noqa: E402

NAMES = {"gmail": "Gmail", "app": "GPT account"}


def waiting(store, kind: str) -> list[dict]:
    """The rows the retry ladder is holding for another try.

    Not doomed and not spent: each one comes back on its own, on a fresh
    phone behind a fresh exit, and taking it out costs an address that
    signs in two times in three on its next try. So this is never part of
    a routine sweep - only `--with-ladder` reaches it, and the operator
    asked for that once, to clear the pool down to what is really stock
    (2026-09-11).
    """
    return store._rows(
        "SELECT r.id, r.address, r.status, r.error, r.seller, r.serial"
        " FROM resources r"
        " WHERE r.kind = %s AND r.retry_after IS NOT NULL"
        "   AND NOT EXISTS (SELECT 1 FROM phones p"
        "                   WHERE p.serial = r.serial AND p.done_at IS NULL"
        "                     AND coalesce(r.serial, '') <> '')"
        " ORDER BY r.id", (kind,))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--write", action="store_true",
                        help="archive the rows (the default only lists them)")
    parser.add_argument("--by", default="the operator",
                        help="who asked, for the event and the archive")
    parser.add_argument("--kind", choices=sorted(SPENT), default="gmail",
                        help="which pool: gmail (default) or app")
    parser.add_argument("--restore", default="",
                        help="ids to put back in the pool, comma separated")
    parser.add_argument("--with-ladder", action="store_true",
                        help="also the rows waiting on the retry ladder "
                             "(they would have come back on their own)")
    args = parser.parse_args(argv)
    kind, spent = args.kind, SPENT[args.kind]
    name = NAMES[kind]
    settings = Settings.load()

    if args.restore:
        ids = [int(part) for part in args.restore.replace(",", " ").split()]
        back = pool_archive.restore(settings, ids)
        for row in back:
            print(f"back in the pool: {row['address']} (id {row['id']})")
        print(f"{len(back)} of {len(ids)} row(s) restored")
        if back:
            store_events.emit(
                settings, "stock", status="restored",
                detail=f"{len(back)} archived {name} row(s) put back in the "
                       f"pool by {args.by}")
        return 0

    with Store(settings) as store:
        rows = doomed(store, kind)
        held = waiting(store, kind) if args.with_ladder else []
        rows += held
        used = [r for r in rows if r["status"] == spent]
        broken = [r for r in rows if r["error"] is not None]
        verdict = [r for r in rows if r not in used and r not in broken]
        print(f"{len(rows)} {name} row(s) would be archived: {len(used)} "
              f"{spent}, {len(verdict)} with a run's verdict, {len(broken)} "
              f"unreadable")
        by_word: dict[str, int] = {}
        for r in rows:
            word = "unreadable" if r["error"] is not None else (r["status"] or "?")
            by_word[word] = by_word.get(word, 0) + 1
        for word, n in sorted(by_word.items(), key=lambda kv: -kv[1]):
            print(f"  {n:4d}  {word}")
        if held:
            print(f"{len(held)} of them were waiting on the retry ladder and "
                  f"would have come back on their own")
        kept = store._rows(
            "SELECT count(*) c FROM resources WHERE kind = %s", (kind,))[0]["c"]
        stay = ("free, on a phone, or set aside" if args.with_ladder else
                "free, on a phone, set aside, or waiting on the ladder")
        print(f"{int(kept) - len(rows)} row(s) stay ({stay})")
        if not args.write:
            print("nothing was archived - run again with --write")
            return 0
        if not rows:
            return 0
    moved = pool_archive.archive(settings, [int(r["id"]) for r in rows],
                                 by=args.by)
    print(f"archived {len(moved)} row(s); they are in resources_archive, "
          f"not gone")
    store_events.emit(
        settings, "stock", status="archived",
        detail=f"{len(moved)} spent and errored {name} rows archived by "
               f"{args.by} ({len(used)} {spent}, {len(verdict)} verdicts, "
               f"{len(broken)} unreadable, {len(held)} off the ladder)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
