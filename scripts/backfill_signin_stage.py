"""Give the sign-ins recorded before rev 45 the stage Google answered at.

    python scripts/backfill_signin_stage.py           # say what it would write
    python scripts/backfill_signin_stage.py --write   # write it

A sign-in's stage - a before the password, p after it, c after the code
or the recovery address (store.signins.stage_of) - is recorded by the
builder from rev 45 on. Before that it lives only in the logs: the
router writes one "screen: <name> (visit n)" line for every screen it
reads, under the phone's serial, and a sign-in's screens are the lines
of its phone between the attempt's start and the moment it was
recorded (`at` less its `seconds`). The Gmails page reads the stage to
say where Google let an address in or stopped it (2026-10-07).

Only a blank stage is ever written, so it is safe to run twice; a
sign-in older than the router's lines, or one whose phone left no line
in its window, stays blank - "stage not read" on the page.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from geelark_farm.config import Settings  # noqa: E402
from geelark_farm.store.db import Store, connect  # noqa: E402
from geelark_farm.store.signins import stage_of  # noqa: E402

SCREEN = re.compile(r"^screen: (\S+)")
#: How far before the attempt's own start a screen still counts - the
#: clock that times the attempt starts just before the first screen read.
SLACK = datetime.timedelta(seconds=5)


def main(argv=None) -> int:
    ask = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ask.add_argument("--write", action="store_true", help="write the stages")
    args = ask.parse_args(argv)
    settings = Settings.load()
    with Store(settings) as store:
        blank = store._rows(
            "SELECT id, serial, at, coalesce(seconds, 0) AS seconds FROM signins"
            " WHERE stage = '' AND serial <> '' ORDER BY at")
        lines = store._rows(
            "SELECT serial, at, msg FROM logs WHERE msg LIKE 'screen: %%'"
            " AND serial <> '' ORDER BY serial, at")
    by_serial: dict[str, tuple[list, list]] = {}
    for row in lines:
        hit = SCREEN.match(str(row["msg"] or ""))
        if not hit:
            continue
        ats, names = by_serial.setdefault(str(row["serial"]), ([], []))
        ats.append(row["at"])
        names.append(hit.group(1))
    found: dict[int, str] = {}
    tally = collections.Counter()
    for s in blank:
        ats, names = by_serial.get(str(s["serial"]), ([], []))
        start = s["at"] - datetime.timedelta(seconds=float(s["seconds"])) - SLACK
        lo, hi = bisect.bisect_left(ats, start), bisect.bisect_right(ats, s["at"])
        stage = stage_of(names[lo:hi])
        tally[stage or "unread"] += 1
        if stage:
            found[int(s["id"])] = stage
    print(f"{len(blank)} sign-in(s) with no stage; read from the logs: "
          + ", ".join(f"{k} {n}" for k, n in sorted(tally.items())))
    if not args.write:
        print("nothing written (add --write)")
        return 0
    with connect(settings) as conn:
        for stage in ("a", "p", "c"):
            ids = [i for i, st in found.items() if st == stage]
            if ids:
                conn.execute("UPDATE signins SET stage = %s"
                             " WHERE id = ANY(%s) AND stage = ''", (stage, ids))
        conn.commit()
    print(f"{len(found)} stage(s) written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
