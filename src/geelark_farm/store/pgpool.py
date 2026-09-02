"""The three pools, backed by the resources table instead of three tabs.

Same contract, different floor. `GmailPool`, `ProxyPool` and `AppPool`
are subclassed here and only their I/O is replaced: `load` reads rows out
of Postgres and hands the rest of the program the same `Resource` objects
it has always held - `values` keyed by the SHEET's column names, so every
caller that reads `values.get("Phone Serial")` or `values.get("Times
Used")` keeps working unchanged - and `_set` turns a write keyed by those
same names into one UPDATE. Nothing above this module learns a new word.

What actually changes is the one thing the sheet could never do: `claim`
is one atomic statement (FOR UPDATE SKIP LOCKED), so two workers reaching
it at once get two rows or one row and a None. The process lock and the
re-read that `Pool.claim` needed to approximate that are simply gone.

The database access is one small adapter, `ResourceTable`, kept thin on
purpose: the contract tests run the pools over an in-memory table of the
same shape, and the adapter's few statements are the only SQL here.
"""

from __future__ import annotations

import logging
import threading
import time

from ..accounts import AccountError
from ..config import Settings
from ..pools import AppPool, GmailPool, Pool, ProxyPool, Resource, clip
from ..proxy import ProxyError

log = logging.getLogger(__name__)

#: Columns the adapter stores as something other than text. A value
#: arrives from the pool as the string the sheet would have held.
_INTS = frozenset({"times_used", "port"})
_BOOLS = frozenset({"email_code_only"})
_STAMPS = frozenset({"claimed_at"})


def _to_db(column: str, value):
    """The pool writes sheet-shaped strings; the table wants its types."""
    if column in _INTS:
        try:
            return int(str(value).strip() or 0)
        except ValueError:
            return 0
    if column in _BOOLS:
        return str(value).strip().upper() == "TRUE"
    if column in _STAMPS:
        text = str(value or "").strip()
        return text or None
    return "" if value is None else str(value)


def _stamp(when) -> str:
    """A timestamptz the way the sheet wrote it, so `abandoned` and every
    reader that parses the Claimed column keep working."""
    if when is None:
        return ""
    if isinstance(when, str):
        return when
    return time.strftime(Pool.CLAIM_FORMAT, when.utctimetuple())


class ResourceTable:
    """The handful of statements the pools need, over psycopg.

    One connection per statement, under one lock: the pools are used from
    build threads at once, a connection is not thread-safe, and at 25ms a
    connect is cheaper than owning a long-lived one's failure modes.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock = threading.Lock()

    def _connect(self):
        from .db import connect

        return connect(self._settings)

    def rows(self, kind: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM resources WHERE kind = %s"
                " ORDER BY sheet_row NULLS LAST, id", (kind,))
            names = [d.name for d in cur.description]
            out = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
            conn.rollback()
            return out

    def update(self, row_id: int, fields: dict) -> None:
        if not fields:
            return
        sets = ", ".join(f"{c} = %s" for c in fields)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE resources SET {sets}, updated_at = now()"
                f" WHERE id = %s", [*fields.values(), row_id])
            conn.commit()

    def claim(self, kind: str, *, free: tuple[str, ...], claimed: str,
              count_use: bool, serial: str = "") -> dict | None:
        """Pick, mark and stamp the first free row of `kind` in one
        statement. The ordering is the sheet's contract: least-used first
        for exits, top of the tab for credentials."""
        order = ("times_used, sheet_row NULLS LAST, id" if count_use
                 else "sheet_row NULLS LAST, id")
        bump = "times_used + 1" if count_use else "times_used"
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"WITH picked AS ("
                f"  SELECT id FROM resources"
                f"  WHERE kind = %s AND error IS NULL"
                f"    AND lower(status) = ANY(%s)"
                f"  ORDER BY {order} FOR UPDATE SKIP LOCKED LIMIT 1)"
                f"UPDATE resources r SET status = %s, claimed_at = now(),"
                f"  times_used = {bump},"
                f"  serial = CASE WHEN %s <> '' THEN %s ELSE r.serial END,"
                f"  updated_at = now()"
                f"  FROM picked WHERE r.id = picked.id RETURNING r.*",
                (kind, list(free), claimed, serial, serial))
            names = [d.name for d in cur.description]
            row = cur.fetchone()
            conn.commit()
            return dict(zip(names, row, strict=True)) if row else None

    def beat(self, ids: list[int]) -> int:
        if not ids:
            return 0
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE resources SET claimed_at = now()"
                " WHERE id = ANY(%s)", (ids,))
            moved = cur.rowcount
            conn.commit()
            return moved

    def delete(self, row_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM resources WHERE id = %s", (row_id,))
            conn.commit()

    def insert(self, row: dict) -> int | None:
        """Add a validated row; None when its identity is already here.
        The partial unique indexes are the duplicate check."""
        columns = list(row)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"INSERT INTO resources ({', '.join(columns)})"
                f" VALUES ({', '.join(['%s'] * len(columns))})"
                f" ON CONFLICT DO NOTHING RETURNING id",
                [row[c] for c in columns])
            got = cur.fetchone()
            conn.commit()
            return got[0] if got else None


class _PgPool(Pool):
    """The I/O half of a pool, over a ResourceTable. Mixed into each of the
    three sheet pools so their vocabulary, ordering rules and special verbs
    (`attach`, `record_exit`, the Used Date, the set-aside reason) are
    inherited rather than copied."""

    kind = ""
    #: Sheet column name -> resources column. What `values` is keyed by,
    #: and what a write keyed by a sheet name lands in.
    COLUMNS: dict[str, str] = {}

    def __init__(self, table: ResourceTable):
        super().__init__(worksheet=None, headers=list(self.COLUMNS),
                         lock=threading.Lock())
        self._table = table

    # ------------------------------------------------------------- reading
    def load(self) -> None:
        self._rows = []
        for row in self._table.rows(self.kind):
            resource = Resource(sheet_row=row.get("sheet_row") or 0,
                                values=self._values_of(row),
                                store_id=row["id"])
            try:
                self._interpret(resource)
            except (AccountError, ProxyError) as exc:
                resource.error = str(exc)
            if row.get("error"):
                resource.error = str(row["error"])
            self._rows.append(resource)
        self._flag_duplicates()

    def _values_of(self, row: dict) -> dict[str, str]:
        values = {}
        for name, column in self.COLUMNS.items():
            raw = row.get(column)
            if column in _STAMPS:
                values[name] = _stamp(raw)
            elif column in _BOOLS:
                values[name] = "TRUE" if raw else "FALSE"
            else:
                values[name] = "" if raw is None else str(raw)
        return values

    # ------------------------------------------------------------- writing
    def _set(self, resource: Resource, fields: dict[str, str]) -> None:
        payload = {}
        for name, value in fields.items():
            if name == self.note_column:
                value = clip(value, self.NOTE_LIMIT)
            column = self.COLUMNS.get(name)
            if column is None:
                log.debug("no %r column in %s; skipping", name, self.tab)
                continue
            payload[column] = _to_db(column, value)
            resource.values[name] = "" if value is None else str(value)
        if payload and resource.store_id is not None:
            self._table.update(resource.store_id, payload)
        self._note_held(resource, fields)

    def _note_held(self, resource: Resource, fields: dict[str, str]) -> None:
        if self.status_column not in fields:
            return
        with self._held_lock:
            if fields[self.status_column] == self.claimed_status:
                self._held[resource.store_id] = resource
            else:
                self._held.pop(resource.store_id, None)

    # ------------------------------------------------------------ claiming
    def claim(self, serial: str = "") -> Resource | None:
        """One statement. The lock and the re-read `Pool.claim` needs are
        the sheet's problem; here the engine hands two racers two rows."""
        row = self._table.claim(
            self.kind, free=tuple(self.available_statuses),
            claimed=self.claimed_status,
            count_use=isinstance(self, ProxyPool), serial=serial)
        if row is None:
            return None
        resource = next((r for r in self._rows if r.store_id == row["id"]),
                        None)
        if resource is None:
            # Added since this Book opened - the case the sheet pool could
            # not take without orphaning a run's rows. Here it is one more
            # Resource, appended, identities untouched.
            resource = Resource(sheet_row=row.get("sheet_row") or 0,
                                values=self._values_of(row),
                                store_id=row["id"])
            try:
                self._interpret(resource)
            except (AccountError, ProxyError) as exc:
                resource.error = str(exc)
            self._rows.append(resource)
        else:
            resource.values.update(self._values_of(row))
        with self._held_lock:
            self._held[resource.store_id] = resource
        log.info("claimed %s from %s%s", resource.label, self.tab,
                 f" for phone {serial}" if serial else "")
        return resource

    # ------------------------------------------------------- stock, by hand
    def append(self, **fields: str) -> Resource:
        """The web's add forms, once the pools live here: one INSERT through
        the same column map the reads use, source 'web'."""
        row: dict = {"kind": self.kind, "source": "web"}
        for name, value in fields.items():
            column = self.COLUMNS.get(name)
            if column is None or column in ("secret",):
                continue
            row[column] = _to_db(column, value)
        if isinstance(self, GmailPool):
            secret = (fields.get("Secret") or "").strip()
            row["recovery_email"] = secret if "@" in secret else ""
            row["totp_secret"] = "" if "@" in secret else secret
        new_id = self._table.insert(row)
        if new_id is None:
            raise ValueError("already in the pool")
        fresh = next((r for r in self._table.rows(self.kind)
                      if r["id"] == new_id), None)
        resource = Resource(sheet_row=0, values=self._values_of(fresh or row),
                            store_id=new_id)
        try:
            self._interpret(resource)
        except (AccountError, ProxyError) as exc:
            resource.error = str(exc)
        self._rows.append(resource)
        return resource

    def delete_row(self, resource: Resource) -> None:
        if resource.store_id is not None:
            self._table.delete(resource.store_id)
        self._rows = [r for r in self._rows if r is not resource]

    def abandoned(self, older_than: float) -> list[Resource]:
        """The sheet pool parses its stamp as local time, which is right on
        the UTC server and a documented quirk everywhere else. This stamp
        is one this module wrote, in UTC, so it is read back as UTC - the
        answer must not move with the timezone of whoever runs the tests."""
        import calendar

        if not self.claimed_at_column:
            return []
        cutoff = time.time() - older_than
        found = []
        for resource in self.stuck:
            stamp = (resource.values.get(self.claimed_at_column) or "").strip()
            if not stamp:
                continue
            try:
                when = calendar.timegm(time.strptime(
                    stamp.rstrip("Zz"), self.CLAIM_FORMAT_UNMARKED))
            except ValueError:
                log.warning("%s row %s is claimed with a stamp nothing can "
                            "read (%r); it will not be freed on its own",
                            self.tab, resource.store_id, stamp)
                continue
            if when < cutoff:
                found.append(resource)
        return found

    def beat(self) -> int:
        with self._held_lock:
            held = list(self._held.values())
        moved = self._table.beat([r.store_id for r in held])
        now = time.strftime(self.CLAIM_FORMAT, time.gmtime())
        for resource in held:
            if self.claimed_at_column:
                resource.values[self.claimed_at_column] = now
        return moved


class PgGmailPool(_PgPool, GmailPool):
    kind = "gmail"
    COLUMNS = {
        "Purchase Date": "purchased_on", "Seller": "seller",
        "Address": "address", "Password": "password", "Secret": "totp_secret",
        "Used Date": "used_at", "Phone Serial": "serial", "Status": "status",
        "Note": "note", "Claimed": "claimed_at",
    }

    def _values_of(self, row: dict) -> dict[str, str]:
        values = super()._values_of(row)
        # One Secret cell in the sheet held either kind; two columns here.
        values["Secret"] = row.get("recovery_email") or row.get(
            "totp_secret") or ""
        return values


class PgAppPool(_PgPool, AppPool):
    kind = "app"
    COLUMNS = {
        "Address": "address", "Password": "password",
        "2FA Secret": "totp_secret", "Phone Serial": "serial",
        "Status": "status", "Note": "note", "Claimed": "claimed_at",
        "Email code": "email_code_only",
    }


class PgProxyPool(_PgPool, ProxyPool):
    kind = "proxy"
    COLUMNS = {
        "Name": "proxy_name", "Proxy String": "proxy_string",
        "Host": "host", "Port": "port", "Username": "username",
        "Password": "proxy_pass", "Last Exit IP": "last_exit_ip",
        "Used By": "serial", "Status": "status", "Note": "note",
        "Times Used": "times_used", "Last Used": "claimed_at",
    }

    def _values_of(self, row: dict) -> dict[str, str]:
        values = super()._values_of(row)
        # The joined string the sheet's parser reads, rebuilt from the
        # identity triple plus the password.
        parts = [row.get("host") or "", str(row.get("port") or ""),
                 row.get("username") or "", row.get("proxy_pass") or ""]
        values["Proxy String"] = ":".join(p for p in parts if p)
        return values
