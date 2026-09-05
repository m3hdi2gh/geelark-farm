"""The last three tabs, over Postgres: Phones, History, and the board.

Written as subclasses that override only where the sheet was touched, so
the thirty-odd callers keep the vocabulary they have always used - `Serial`,
`GPT Account`, `App` - and none of them has to learn a column name. What
changes is where a row lives, not what it is called.

**Sheet rows become row ids.** `locate` handed back a line number and
`finish`/`delete_rows` took one; here they hand back and take the store's
`id`. Every caller treats it as an opaque int already, because a sibling
deleting its phone shifted every number below it - the one thing that was
never safe to remember about a sheet row is now the thing that cannot
change.

**The person channel is not here.** `State` and `Tries` left first and
live in `store.person`; the reads that matter go there. What this leaves
on the phone row is the machine's half - status, what is signed in, which
exit, the note.
"""

from __future__ import annotations

import logging
import threading
import time

from ..pools import HistoryLog, PhoneLog, ServiceBoard
from .db import Store

log = logging.getLogger(__name__)

#: The sheet's App column, three-valued. NULL is "nobody looked", which
#: survived one demotion incident already and must not flatten to False.
_MARKS = {True: PhoneLog.YES, False: PhoneLog.NO, None: ""}


def _cells(row: dict) -> dict[str, str]:
    """One phone row in the words the tab used."""
    return {
        "Serial": row.get("serial") or "",
        "Status": row.get("status") or "",
        "State": row.get("state") or "",
        "Phone ID": row.get("phone_id") or "",
        "Gmail": row.get("gmail") or "",
        "GPT Account": row.get("app_account") or "",
        "Proxy": row.get("proxy_name") or "",
        "App": _MARKS.get(row.get("app_installed"), ""),
        "Tries": str(row.get("tries") or 0) if row.get("tries") else "",
        "Note": row.get("note") or "",
        "Created": _when(row.get("created_at")),
    }


#: The tab's column names, and the table's. Anything not here is a column
#: the phones table does not keep, and writing it is a no-op with a line in
#: the log - the same thing `finish` did for a column the tab lacked.
_COLUMNS = {
    "Status": "status", "State": "state", "Phone ID": "phone_id",
    "Gmail": "gmail", "GPT Account": "app_account", "Proxy": "proxy_name",
    "Note": "note", "Serial": "serial",
}


def _when(stamp) -> str:
    return stamp.strftime("%Y-%m-%d %H:%MZ") if stamp else ""


class PgPhoneLog(PhoneLog):
    """The Phones tab, over the `phones` table."""

    def __init__(self, settings):
        self._settings = settings
        self._lock = threading.Lock()
        self._append_lock = threading.Lock()

    # ------------------------------------------------------------ reading
    def _typed_rows(self, what: str):
        with Store(self._settings) as store:
            rows = store._rows(
                "SELECT * FROM phones WHERE done_at IS NULL"
                " ORDER BY serial, id")
        for row in rows:
            yield row["id"], _cells(row)

    def locate(self, serial: str) -> int | None:
        wanted = str(serial or "").strip()
        if not wanted:
            return None
        with Store(self._settings) as store:
            rows = store._rows(
                "SELECT id FROM phones"
                " WHERE serial = %s AND done_at IS NULL", (wanted,))
        return int(rows[0]["id"]) if rows else None

    # ------------------------------------------------------------ writing
    def start(self, **fields: str) -> int:
        """Open a row for a phone a run is working on, and hand back its id."""
        fields.setdefault("Status", self.BUILDING)
        fields.setdefault("State", self.UNUSED)
        columns, values = ["serial"], [str(fields.get("Serial") or "")]
        for name, value in fields.items():
            column = _COLUMNS.get(name)
            if column and column != "serial":
                columns.append(column)
                values.append(value)
        if "App" in fields:
            columns.append("app_installed")
            values.append({self.YES: True, self.NO: False}.get(
                fields["App"], None))
        placeholders = ", ".join(["%s"] * len(columns))
        with Store(self._settings) as store:
            rows = store._rows(
                f"INSERT INTO phones ({', '.join(columns)})"
                f" VALUES ({placeholders}) RETURNING id", tuple(values))
        return int(rows[0]["id"])

    def finish(self, sheet_row: int, **fields: str) -> None:
        """Write to one row, named by the id `locate`/`start` handed back."""
        sets, values = [], []
        for name, value in fields.items():
            if name == "App":
                sets.append("app_installed = %s")
                values.append({self.YES: True, self.NO: False}.get(value))
                continue
            column = _COLUMNS.get(name)
            if column is None:
                log.debug("no %r column on a phone row; skipping", name)
                continue
            sets.append(f"{column} = %s")
            values.append(value)
        if not sets:
            return
        with Store(self._settings) as store:
            store._rows(
                f"UPDATE phones SET {', '.join(sets)}, updated_at = now()"
                f" WHERE id = %s RETURNING id", (*values, int(sheet_row)))

    def delete_rows(self, sheet_rows: list[int]) -> None:
        """Close these rows. Nothing is deleted: a phone that went is the
        answer to "what did we build on Tuesday", and losing that answer is
        the gap this table was made to close."""
        if not sheet_rows:
            return
        with Store(self._settings) as store:
            store._rows(
                "UPDATE phones SET done_at = now(), updated_at = now()"
                " WHERE id = ANY(%s) AND done_at IS NULL RETURNING id",
                ([int(r) for r in sheet_rows],))


class PgHistory(HistoryLog):
    """The History tab, over the `events` table it always duplicated."""

    def __init__(self, settings):
        self._settings = settings
        self._lock = threading.Lock()

    def append(self, **fields: str) -> None:
        from . import events as store_events

        store_events.emit(
            self._settings, "history",
            status=str(fields.get("Event") or ""),
            serial=str(fields.get("Serial") or ""),
            detail="; ".join(f"{k}={v}" for k, v in fields.items()
                             if v and k not in ("Event", "Serial")))


class PgServiceBoard(ServiceBoard):
    """The Service tab's controls, over `service_state`.

    The board was two things at once: a dashboard the pass wrote and a row
    of checkboxes a person ticked. Only the second half is a control, and
    only the second half is here - what the pass says about itself is
    already an event and already on the console's front page.
    """

    def __init__(self, settings):
        self._settings = settings

    def asked(self) -> list[str]:
        from .state import get

        try:
            ticked = get(self._settings, "controls", {}) or {}
        except Exception as exc:                                  # noqa: BLE001
            # Never fatal: a board that cannot be read must not stop the
            # loop that would otherwise be building.
            log.warning("could not read the controls (%s)", exc)
            return []
        return [name for name in self.CONTROLS if ticked.get(name)]

    def taken(self, name: str) -> None:
        """Untick one, so a tick is one request and not a standing one."""
        self._set(name, False)

    def tick(self, name: str) -> bool:
        """Tick one from the web. True if it went in."""
        return self._set(name, True)

    def _set(self, name: str, on: bool) -> bool:
        from .db import connect
        from .state import get, put

        if name not in self.CONTROLS:
            return False
        try:
            ticked = dict(get(self._settings, "controls", {}) or {})
            if on:
                ticked[name] = time.time()
            else:
                ticked.pop(name, None)
            with connect(self._settings) as conn:
                put(conn, "controls", ticked)
                conn.commit()
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not write the control %r (%s)", name, exc)
            return False
        return True

    def write(self, *args, **kwargs) -> None:
        """The dashboard half. The console reads the pass's own event now,
        so there is nothing to paint and nothing to spend a write on."""
        return None
