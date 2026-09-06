"""Take out of the workbook everything that is in the workbook and nowhere else.

    python scripts/export_the_sheet.py            # look, write nothing
    python scripts/export_the_sheet.py --write    # write it into the store

Run this before the sheet is closed, while there is still a client that can
read it. Two things live only there:

**The old History rows.** `PgHistory` routes every new history line into
`events`, but there is no backfill anywhere in `store/`, so rows written
before that switch exist on the History tab and in no other place. Nothing
reads them - they are the answer to "what did we build in July", which is a
question a person asks, not the program. Closing the door does not delete
them; the workbook survives whatever we stop doing to it. But a workbook
nobody opens is a workbook nobody re-shares the day the service-account key
rotates, so they are moved while there is still something that can move them.

**The refused stock rows.** `importer.pull` has two loops and only one of
them inserts: a row the reader could not understand gets a note written back
into its cell and is never put in the store (`store/importer.py`), because
`Pool.available` excludes anything with an error. So every unreadable row a
person ever pasted - a typo, a shape nobody thought of, a password with a
tab in it - is on the tab and nowhere else, along with the reason it was
refused. That is the list of what the friend paid for and the farm never
took, which is worth more than the History rows and is the thing this was
written for.

Never writes to the sheet. Reading is all it does there, and `--write` means
the store.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geelark_farm import gsheet  # noqa: E402
from geelark_farm.config import Settings, load_env  # noqa: E402
from geelark_farm.gsheet import SCOPES  # noqa: E402
from geelark_farm.pools import (  # noqa: E402
    APPS_TAB,
    GMAILS_TAB,
    HISTORY_TAB,
    PROXY_TAB,
)

#: What marks a row this script wrote, so a second run is visible rather than
#: silently doubling everything.
MARK = "exported from the workbook"


def _tab(book, name: str):
    for sheet in book.worksheets():
        if sheet.title == name:
            return sheet
    return None


def _rows(sheet) -> list[dict]:
    """A tab as dicts, keyed by its own header row."""
    values = gsheet.read_values(sheet)
    if not values:
        return []
    headers = [h.strip() for h in values[0]]
    out = []
    for number, line in enumerate(values[1:], start=2):
        row = {headers[i]: (line[i] if i < len(line) else "")
               for i in range(len(headers))}
        row["_row"] = number
        out.append(row)
    return out


def history(book) -> list[dict]:
    sheet = _tab(book, HISTORY_TAB)
    if sheet is None:
        print(f"no {HISTORY_TAB} tab; nothing to take")
        return []
    rows = [r for r in _rows(sheet) if any(v for k, v in r.items()
                                           if k != "_row")]
    print(f"{HISTORY_TAB}: {len(rows)} row(s)")
    return rows


def refused(book) -> list[dict]:
    """Stock rows the importer could not read, with the reason it gave.

    Found by the note the importer wrote back rather than by re-running the
    reader: the note is what a person would look at, and re-judging a row
    now with today's reader would quietly drop the ones that have since
    become readable - which are exactly the ones worth knowing about.
    """
    out = []
    for name in (GMAILS_TAB, PROXY_TAB, APPS_TAB):
        sheet = _tab(book, name)
        if sheet is None:
            continue
        for row in _rows(sheet):
            note = (row.get("Note") or "").strip()
            status = (row.get("Status") or "").strip()
            if status.lower() == "imported":
                continue
            if not any(v for k, v in row.items() if k != "_row"):
                continue
            out.append({"tab": name, "row": row["_row"], "note": note,
                        "cells": {k: v for k, v in row.items()
                                  if k != "_row" and v}})
        print(f"{name}: {sum(1 for r in out if r['tab'] == name)} row(s) the "
              f"store never took")
    return out


def write(settings: Settings, kept: dict) -> None:
    from geelark_farm.store import events as store_events

    for row in kept["history"]:
        store_events.emit(settings, "history",
                          detail=f"{MARK}: " + json.dumps(row, sort_keys=True))
    for row in kept["refused"]:
        store_events.emit(settings, "history",
                          detail=f"{MARK} (refused): "
                                 + json.dumps(row, sort_keys=True))
    print(f"wrote {len(kept['history'])} history and {len(kept['refused'])} "
          f"refused row(s) into events, each marked {MARK!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="put it in the store; without this, only look")
    parser.add_argument("--out", type=Path,
                        help="also write the whole lot to this JSON file")
    args = parser.parse_args()

    load_env()
    settings = Settings.load()
    settings.require_sheets()
    # The same two lines Book.open uses, on purpose: one door to the
    # workbook, opened the one way that is known to work.
    import gspread
    from google.oauth2.service_account import Credentials as Key

    client = gsheet.with_timeout(gspread.authorize(
        Key.from_service_account_file(str(settings.service_account_json),
                                      scopes=SCOPES)))
    book = client.open_by_key(settings.sheet_id)

    kept = {"history": history(book), "refused": refused(book)}
    if args.out:
        args.out.write_text(json.dumps(kept, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        print(f"wrote {args.out}")
    if args.write:
        write(settings, kept)
    else:
        print("nothing written to the store - pass --write when the counts "
              "above look right")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
