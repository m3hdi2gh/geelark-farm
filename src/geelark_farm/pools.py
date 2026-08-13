"""The resource tabs, as pools something can be claimed from.

`sheets.py` reads a different shape of sheet, where one row *is* one phone: a
proxy, a Gmail and an app account chosen in advance, and a failure anywhere in
that row fails the whole row. That works while every resource is good and gets
expensive when they are not - a bad Gmail costs a phone, and the proxy it was
paired with is condemned along with it.

Here the resources are pools. A build claims a proxy, then the first usable
Gmail, then the first usable app account, and a bad one costs only itself: the
next candidate is tried on the same phone. What comes out is written to the
`Phones` tab, which is the record of what was actually produced.

Four tabs, located by header name so columns can be reordered or annotated:

    Gmails     Address, Password, 2FA Secret       -> Used Date, Phone Serial,
                                                      Status, Note
    Proxy      Proxy String (or Host/Port/User/Pass)
                                                   -> Last Exit IP, Used By,
                                                      Status, Note
    Gpt Info   Address, Password, 2FA Secret       -> Phone Serial, Status, Note
    Phones     everything a finished phone is

A resource is available when its Status is blank. Claiming writes a holding
status before the row is handed out, so a second run - or a second worker -
cannot take it, and a run that dies leaves visible evidence rather than a
silent double-use. `geelark pools --release-stuck` is how those come back.

Each tab has its own words for that, because they are not the same thing. A
credential is consumed: blank, `in_use`, then `ready` or the reason it failed.
A proxy is occupied and then let go: `free`, `claimed`, `on a phone`, `dead`.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from .accounts import AccountError, Credentials, normalize_totp_secret
from .config import Settings
from .gsheet import SCOPES, SheetError, a1_column, batch_write
from .proxy import Proxy, ProxyError
from .proxy import parse as parse_proxy

log = logging.getLogger(__name__)

GMAILS_TAB = "Gmails"
PROXY_TAB = "Proxy"
APPS_TAB = "Gpt Info"
PHONES_TAB = "Phones"
LISTS_TAB = "Lists"

# The default vocabulary a tab's Status column speaks. A pool can override any
# of it - the Proxy tab does, because a proxy is not consumed the way a
# credential is, and words that fit one read as nonsense on the other.
#
# `in_use` is written before the resource leaves the pool. The alternative is
# claiming in memory only, which is safe for one process and silently signs the
# same Gmail into two phones as soon as there are two.
IN_USE = "in_use"

# Blank means nobody has used this yet: a freshly pasted row should be picked
# up without anyone typing a word into it.
AVAILABLE = frozenset({""})

# What a resource's Status becomes when the build it served succeeded.
SPENT = "ready"


@dataclass
class Resource:
    """One row of a resource tab, and what it turned out to be."""

    sheet_row: int
    values: dict[str, str]
    credentials: Credentials | None = None
    proxy: Proxy | None = None
    error: str | None = None

    @property
    def label(self) -> str:
        """What to call this row in a log line or a note.

        A proxy leads with the name it has in the vendor's panel, when the tab
        carries one. "proxy SX13 is dead" is something you can act on - find it
        in the panel, renew it - where a host and port send you comparing
        strings across two windows.
        """
        if self.credentials:
            return self.credentials.email
        if self.proxy:
            name = (self.values.get("Name") or "").strip()
            return f"{name} ({self.proxy})" if name else str(self.proxy)
        return f"row {self.sheet_row}"


class Pool:
    """One resource tab: read once, handed out a row at a time.

    The read is deliberately not repeated. A build takes minutes and the tabs
    are edited by hand between runs, not during them; re-reading before every
    claim would cost an API call each time and still not make concurrent hand
    editing safe.
    """

    tab = ""
    # Where the outcome goes. Named per tab because the columns differ.
    status_column = "Status"
    note_column = "Note"
    serial_column = ""

    # The words this tab's Status column uses. Overridable, because "ready"
    # and "in_use" describe a credential being consumed, and a proxy is not
    # consumed - it is occupied and then let go.
    available_statuses = AVAILABLE
    claimed_status = IN_USE
    spent_status = SPENT
    #: Where a credential ends up once the phone carrying it is gone. Not in
    #: available_statuses, so nothing claims it again - which is the point.
    retired_status = "used"

    def __init__(self, worksheet, headers: list[str], lock: threading.Lock):
        self._ws = worksheet
        self._lock = lock
        self.headers = headers
        self._index = {name: i for i, name in enumerate(headers)}
        self._rows: list[Resource] = []
        self._claim_lock = threading.Lock()

    # ------------------------------------------------------------- reading
    def load(self) -> None:
        with self._lock:
            raw = self._ws.get_all_values()
        self._rows = []
        for offset, line in enumerate(raw[1:], start=2):
            values = {
                name: (line[i].strip() if i < len(line) else "")
                for name, i in self._index.items()
            }
            if not any(values.values()):
                continue                       # a blank spacer row, not a gap
            resource = Resource(sheet_row=offset, values=values)
            try:
                self._interpret(resource)
            except (AccountError, ProxyError) as exc:
                resource.error = str(exc)
            self._rows.append(resource)
        self._flag_duplicates()

    def _identity(self, resource: Resource) -> str:
        """What makes two rows the same resource rather than two of them."""
        if resource.credentials:
            return resource.credentials.email.strip().lower()
        if resource.proxy:
            return f"{resource.proxy.host}:{resource.proxy.port}"
        return ""

    def _flag_duplicates(self) -> None:
        """Refuse every row after the first that names the same thing.

        Two rows for one address hand it out twice: the same account signs into
        two phones and their 2FA codes race each other. Two rows for one proxy
        put two devices behind one exit, which is the thing `on a phone` exists
        to prevent.

        The old single-row sheet checked this and the check was lost when that
        module went. A duplicate app account was sitting in the tab within the
        day (2026-08-13, tararrashnooo@gmail.com on rows 5 and 14).

        The first occurrence is kept, so the fix is to delete the later row -
        and the error says which one it is.
        """
        seen: dict[str, int] = {}
        for resource in self._rows:
            key = self._identity(resource)
            if not key or resource.error:
                continue
            if key in seen:
                resource.error = (f"duplicate of row {seen[key]} ({key}) - "
                                  f"delete one of them")
            else:
                seen[key] = resource.sheet_row

    def _interpret(self, resource: Resource) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def status_of(self, resource: Resource) -> str:
        return (resource.values.get(self.status_column) or "").strip().lower()

    @property
    def available(self) -> list[Resource]:
        """Usable rows, in sheet order. Order is the whole contract: "the first
        usable one" is what the operator sees when they look at the tab."""
        return [r for r in self._rows
                if not r.error and self.status_of(r) in self.available_statuses]

    @property
    def broken(self) -> list[Resource]:
        return [r for r in self._rows if r.error]

    @property
    def stuck(self) -> list[Resource]:
        """Rows a dead run left claimed. Nothing can use these until they are
        released, and nothing will release them on its own."""
        return [r for r in self._rows
                if self.status_of(r) == self.claimed_status]

    @property
    def flagged(self) -> list[Resource]:
        """Rows a run judged and set aside, with the reason it gave.

        Everything that is not one of the four routine states - free, claimed,
        on a device, retired. What is left is the pile someone has to make a
        decision about, and until now the only way to see it was to open the
        tab and read the Status column by eye.

        Derived by elimination rather than by listing the failure reasons,
        because the reasons are `failures.py`'s to know and this should not
        need editing when one is added.
        """
        settled = set(self.available_statuses) | {
            self.claimed_status, self.spent_status, self.retired_status}
        return [r for r in self._rows
                if not r.error and self.status_of(r) not in settled]

    # ------------------------------------------------------------ claiming
    def claim(self) -> Resource | None:
        """Take the first usable row, marking it so nothing else can.

        The sheet write happens under the same lock as the choice. Without
        that, two workers reaching this at once both see the same first row
        available and both take it.
        """
        with self._claim_lock:
            for resource in self._rows:
                if (resource.error
                        or self.status_of(resource) not in self.available_statuses):
                    continue
                self._set(resource, {self.status_column: self.claimed_status})
                log.info("claimed %s from %s", resource.label, self.tab)
                return resource
        return None

    def retire(self, resource: Resource, *, note: str = "") -> None:
        """Take a credential out of circulation for good.

        The serial goes with it. It named a phone that has just been deleted,
        so leaving it there points whoever reads the row at nothing - the same
        stale-reference problem that quietly held thirteen proxies out of the
        pool (2026-08-11).
        """
        fields = {self.status_column: self.retired_status,
                  self.note_column: note}
        if self.serial_column:
            fields[self.serial_column] = ""
        self._set(resource, fields)

    def find(self, email: str) -> Resource | None:
        """The row holding this address, whatever state it is in."""
        wanted = (email or "").strip().lower()
        if not wanted:
            return None
        return next((r for r in self._rows if r.credentials
                     and r.credentials.email.lower() == wanted), None)

    def release(self, resource: Resource, *, note: str = "") -> None:
        """Put a claimed row back, available again.

        For resources a build touched but did not spend - the Gmail that was
        never tried because the proxy was the problem, the proxy swapped out on
        suspicion. Condemning those would throw away good stock on the strength
        of a failure that was measured to be about the session rather than the
        resource (2026-08-09).
        """
        self._set(resource, {self.status_column: "", self.note_column: note})

    def set_aside(self, resource: Resource, *, note: str = "") -> None:
        """Put a row back after the service asked for something rather than
        judging it. For most pools that is the same as releasing it."""
        self.release(resource, note=note)

    def spend(self, resource: Resource, *, serial: str = "",
              note: str = "") -> None:
        """Mark a row as used up by a phone that worked."""
        fields = {self.status_column: self.spent_status,
                  self.note_column: note}
        if self.serial_column and serial:
            fields[self.serial_column] = str(serial)
        self._set(resource, fields)

    def fail(self, resource: Resource, reason: str, *, note: str = "") -> None:
        """Record what was wrong with a row, in the vocabulary of the tab's own
        Status list, so the column stays a thing you can filter on."""
        self._set(resource, {self.status_column: reason,
                             self.note_column: note[:500]})

    # ------------------------------------------------------------- writing
    def _set(self, resource: Resource, fields: dict[str, str]) -> None:
        payload = []
        for name, value in fields.items():
            index = self._index.get(name)
            if index is None:
                log.debug("no %r column in %s; skipping", name, self.tab)
                continue
            payload.append({
                "range": f"{a1_column(index + 1)}{resource.sheet_row}",
                "values": [[value]],
            })
            resource.values[name] = value
        if payload:
            batch_write(self._ws, self._lock, payload,
                        what=f"{self.tab} row {resource.sheet_row}")


class GmailPool(Pool):
    tab = GMAILS_TAB
    serial_column = "Phone Serial"
    # An address that has signed into a phone has spent whatever first-use
    # credit it had, whether that phone went on to work or not. `used` retires
    # it: the Used Date beside it says when.
    retired_status = "used"

    def _interpret(self, resource: Resource) -> None:
        values = resource.values
        credentials = Credentials(
            email=values.get("Address", ""),
            password=values.get("Password", ""),
            totp_secret=normalize_totp_secret(values.get("2FA Secret", "")),
        )
        credentials.validate()
        resource.credentials = credentials

    def spend(self, resource: Resource, *, serial: str = "",
              note: str = "") -> None:
        super().spend(resource, serial=serial, note=note)
        # Only the Gmails tab records when: it is the column someone sorts by
        # when they want to know how old the oldest live account is.
        if "Used Date" in self._index:
            self._set(resource, {"Used Date": time.strftime("%Y-%m-%d")})


class AppPool(Pool):
    tab = APPS_TAB
    serial_column = "Phone Serial"
    # `delivered` rather than `used`: this account went out on a phone the
    # operator finished with, which is the product. An account on a phone that
    # FAILED is freed instead - it never got a fair device.
    retired_status = "delivered"

    #: What an account gets when OpenAI asks for something no unattended run
    #: can supply - a code in an inbox. Not a judgement on the account, which
    #: is why it is not one of the failure reasons, and not blank either.
    #:
    #: It was blank. A challenged account went back indistinguishable from a
    #: row nobody had ever tried, so every run picked the same two again and
    #: spent five minutes each proving the same thing - three runs running, and
    #: the tab showed nothing at all against them (2026-08-13). "The sheet has
    #: not updated" was the right reading of it: the only record was a Note.
    #:
    #: Deliberately not in `available_statuses`, so it waits for a person. That
    #: is not the same as condemning it - `fail()` would put the reason in this
    #: column and mean the account is bad, and nothing here says that. Blank
    #: the cell and the next run takes it again.
    challenged_status = "challenged"

    def set_aside(self, resource: Resource, *, note: str = "") -> None:
        self._set(resource, {self.status_column: self.challenged_status,
                             self.note_column: note})

    def _interpret(self, resource: Resource) -> None:
        values = resource.values
        credentials = Credentials(
            email=values.get("Address", ""),
            password=values.get("Password", ""),
            totp_secret=normalize_totp_secret(values.get("2FA Secret", "")),
        )
        credentials.validate(what="app account:")
        resource.credentials = credentials


class ProxyPool(Pool):
    tab = PROXY_TAB
    serial_column = "Used By"

    # A proxy is not spent by being used - it keeps working, and the column
    # says where it is rather than whether it is gone.
    #
    # The words matter more here than anywhere else in the sheet, because the
    # tab is read at a glance to answer "what have I got". `ok` beside `unused`
    # invited exactly the wrong reading - that `ok` meant healthy and `unused`
    # meant idle, when both are healthy and `ok` means busy.
    available_statuses = frozenset({"", "free",
                                    # what earlier runs wrote, so a row nobody
                                    # has touched since is still usable
                                    "unused"})
    claimed_status = "claimed"
    spent_status = "on a phone"

    def _interpret(self, resource: Resource) -> None:
        values = resource.values
        raw = values.get("Proxy String", "")
        if not raw:
            # The parts are the authority when the joined string is missing:
            # someone filling the tab by hand fills the columns.
            host, port = values.get("Host", ""), values.get("Port", "")
            user, password = values.get("Username", ""), values.get("Password", "")
            raw = ":".join(p for p in (host, port, user, password) if p)
        resource.proxy = parse_proxy(raw)

    def release(self, resource: Resource, *, note: str = "") -> None:
        # `free` rather than blank: this column is also the record of whether a
        # proxy works, and a blank there reads as "never checked".
        self._set(resource, {self.status_column: "free", self.note_column: note})

    def spend(self, resource: Resource, *, serial: str = "",
              note: str = "") -> None:
        fields = {self.status_column: self.spent_status, self.note_column: note}
        if serial:
            # Appended, not replaced: a proxy that has carried two phones has
            # to name both, or the column cannot answer "what is on this exit".
            previous = (resource.values.get(self.serial_column) or "").strip()
            existing = [p.strip() for p in previous.split(",") if p.strip()]
            if str(serial) not in existing:
                existing.append(str(serial))
            fields[self.serial_column] = ", ".join(existing)
        self._set(resource, fields)

    def record_exit(self, resource: Resource, exit_ip: str) -> None:
        """The address the proxy actually came out of, which is the one Google
        and OpenAI judge - never the gateway host in the credentials."""
        if exit_ip:
            self._set(resource, {"Last Exit IP": exit_ip})

    def attach(self, resource: Resource, serials: str) -> None:
        """Say which phone is behind this exit, replacing whatever was there.

        Not `spend`, which appends: that is right while a run is working,
        because a swap has to name both the exit it left and the one it took.
        This is the other direction - the answer read back off GeeLark, which
        is the only thing that actually knows - so it replaces.

        It also un-marks a proxy someone had written off. A row saying `dead`
        with a live phone behind it is a contradiction, and the phone is the
        side of it that is demonstrably working.
        """
        self._set(resource, {self.status_column: self.spent_status,
                             self.serial_column: serials,
                             self.note_column: f"On phone {serials}."})

    def reclaim(self, in_use: set[str]) -> list[Resource]:
        """Free proxies held by a phone that no longer exists.

        `ok` means "a phone is behind this", which is what stops two devices
        sharing one exit. Nothing was undoing it when the phone went away, so
        every deleted phone quietly took a working proxy out of circulation:
        thirteen of twenty-two were locked to phones that had been gone for
        days, and a run failed with no_usable_proxy while they sat there
        (2026-08-11).

        `in_use` is the set of `host:port` a live phone is actually using -
        asked of the vendor, not of this sheet, since the sheet is the thing
        being corrected. Rows that are `in_use` (claimed by a running build)
        are untouched: that build has not created its phone yet.
        """
        freed = []
        for resource in self._rows:
            if (self.status_of(resource) != self.spent_status
                    or not resource.proxy):
                continue
            if f"{resource.proxy.host}:{resource.proxy.port}" in in_use:
                continue
            self.release(resource, note=(
                "Free again - the phone that was behind it no longer exists."))
            self._set(resource, {self.serial_column: ""})
            freed.append(resource)
        return freed

    @staticmethod
    def port_id(resource: Resource) -> str:
        """What sx.org needs to refresh this proxy, or "" if the row has none.

        Empty is the normal case for the Unlimited product, which does not
        appear in the vendor's port listing at all - so this being blank means
        "cannot be refreshed", not "not filled in yet".
        """
        return (resource.values.get("Port ID") or "").strip()

    def refreshes_today(self, resource: Resource) -> int:
        """How much of today's allowance this proxy has already spent.

        Kept in the sheet rather than in memory, because the allowance is the
        vendor's and it does not reset when a run ends. The cell reads
        `2026-08-11 x2`, which is also legible to whoever is looking at the tab
        wondering why a proxy stopped being refreshed.
        """
        raw = (resource.values.get("Last Refresh") or "").strip()
        date, _, count = raw.partition(" x")
        if date.strip() != time.strftime("%Y-%m-%d"):
            return 0
        try:
            return int(count)
        except ValueError:
            return 1                       # a date with no count is one refresh

    def note_refresh(self, resource: Resource) -> None:
        spent = self.refreshes_today(resource) + 1
        self._set(resource,
                  {"Last Refresh": f"{time.strftime('%Y-%m-%d')} x{spent}"})


class PhoneLog:
    """The `Phones` tab: one row per phone this tool built.

    Written twice. Once when the phone exists and is being worked on, so an
    interrupted run still leaves something that names the phone in GeeLark's
    list; once when the build ends, with what it ended as.
    """

    tab = PHONES_TAB

    #: What a run writes here. Three, because three is how many the reader acts
    #: on differently - see builder.possible_statuses.
    BUILDING = "building"      # a run holds it right now
    READY = "ready"            # signed in, installed, app account on it
    INCOMPLETE = "incomplete"  # anything else; the Note says what happened

    def __init__(self, worksheet, headers: list[str], lock: threading.Lock):
        self._ws = worksheet
        self._lock = lock
        self._index = {name: i for i, name in enumerate(headers)}
        self.width = len(headers)
        self._append_lock = threading.Lock()

    def start(self, **fields: str) -> int:
        """Append a row and return the sheet row it landed on.

        The row number is found and written under one lock: two workers
        appending at once would otherwise both find the same first empty row.
        """
        fields.setdefault("Created", time.strftime("%Y-%m-%d %H:%M"))
        fields.setdefault("Status", self.BUILDING)
        fields.setdefault("State", self.UNUSED)
        line = [""] * self.width
        for name, value in fields.items():
            index = self._index.get(name)
            if index is not None:
                line[index] = value
        with self._append_lock:
            with self._lock:
                used = len(self._ws.get_all_values())
            sheet_row = used + 1
            batch_write(self._ws, self._lock,
                        [{"range": f"A{sheet_row}:"
                                   f"{a1_column(self.width)}{sheet_row}",
                          "values": [line]}],
                        what=f"{self.tab} row {sheet_row}")
        return sheet_row

    def unfinished(self) -> list[dict]:
        """Phones that got a Gmail but never an app account.

        Read from the columns rather than from Status, because Status names why
        a build stopped and there are several ways to stop one step short -
        the tab emptied, every exit was refused, the budget ran out. What they
        have in common is the thing that matters here: a Gmail on the device
        and no app account beside it.

        `building` is excluded: a run may be holding it right now.
        """
        with self._lock:
            rows = self._ws.get_all_values()
        found = []
        for offset, line in enumerate(rows[1:], start=2):
            if not any(line):
                continue

            def cell(name: str, line: list = line) -> str:
                index = self._index.get(name)
                return (line[index].strip()
                        if index is not None and index < len(line) else "")

            # `building` means a run holds it right now; `ready` means there
            # is nothing left to do. Everything else is a candidate, whatever
            # word it uses - rows written before the statuses were collapsed
            # still say things like no_usable_gpt, and they are picked up on
            # exactly the same test as the ones that say `incomplete`.
            if cell("Status") in (self.BUILDING, self.READY) or not cell("Phone ID"):
                continue
            if not cell("Gmail") or cell("GPT Account"):
                continue
            # The reason is the head of the note now, not the status - which
            # says only whether the phone is usable. Whoever is deciding what
            # to finish wants the reason.
            # The first sentence of the note, without the opening the note
            # writes for the tab - "Stopped short:" is worth saying in a cell
            # whose neighbours are prose and not in a list of things to finish.
            # Split on ". " rather than ".", or an address or a version number
            # in the sentence cuts it in half.
            reason = cell("Note").split(". ")[0].strip() or cell("Status")
            reason = reason.removeprefix("Stopped short: ").rstrip(".")
            found.append({"sheet_row": offset, "phone_id": cell("Phone ID"),
                          "serial": cell("Serial"), "gmail": cell("Gmail"),
                          "proxy": cell("Proxy"), "status": reason})
        return found

    #: What the operator writes in `State` to say what should happen next.
    #: `Status` is what a run concluded; this is an instruction back to it.
    DONE = "done"          # finished with - delete the phone
    FAILED = "failed"      # something is wrong with it - free its app account
    UNUSED = "unused"      # the default: leave it alone

    def marked(self) -> list[dict]:
        """Rows the operator has marked `done` or `failed`.

        Read every time rather than cached: the whole point of the column is
        that it is edited by hand between runs.
        """
        with self._lock:
            rows = self._ws.get_all_values()
        found = []
        for offset, line in enumerate(rows[1:], start=2):
            if not any(line):
                continue

            def cell(name: str, line: list = line) -> str:
                index = self._index.get(name)
                return (line[index].strip()
                        if index is not None and index < len(line) else "")

            state = cell("State").lower()
            if state in (self.DONE, self.FAILED):
                found.append({"sheet_row": offset, "state": state,
                              "phone_id": cell("Phone ID"),
                              "serial": cell("Serial"),
                              "gmail": cell("Gmail"),
                              "app_account": cell("GPT Account")})
        return found

    def delete_rows(self, sheet_rows: list[int]) -> None:
        """Remove rows for phones that no longer exist.

        Bottom up, because deleting one shifts everything below it - and the
        numbers were read before any of them moved.
        """
        if not sheet_rows:
            return
        requests = [{"deleteDimension": {"range": {
            "sheetId": self._ws.id, "dimension": "ROWS",
            "startIndex": n - 1, "endIndex": n}}}
            for n in sorted(sheet_rows, reverse=True)]
        with self._lock:
            self._ws.spreadsheet.batch_update({"requests": requests})

    def rows(self) -> list[dict]:
        """Every phone this tab records, as it stands.

        `unfinished` and `marked` each read the whole tab to answer one
        question about it. This answers "what does the tab say", so a caller
        with a third question - which proxy does it think each phone is on -
        does not need a fourth reader.
        """
        with self._lock:
            raw = self._ws.get_all_values()
        found = []
        for offset, line in enumerate(raw[1:], start=2):
            if not any(line):
                continue
            row = {name: (line[i].strip() if i < len(line) else "")
                   for name, i in self._index.items()}
            row["sheet_row"] = offset
            found.append(row)
        return found

    def finish(self, sheet_row: int, **fields: str) -> None:
        payload = []
        for name, value in fields.items():
            index = self._index.get(name)
            if index is None:
                log.debug("no %r column in %s; skipping", name, self.tab)
                continue
            payload.append({
                "range": f"{a1_column(index + 1)}{sheet_row}",
                "values": [[value]],
            })
        if payload:
            batch_write(self._ws, self._lock, payload,
                        what=f"{self.tab} row {sheet_row}")


class Book:
    """The workbook and its four tabs, sharing one lock.

    One lock rather than one per tab: gspread is not documented thread-safe and
    the tabs are reached through the same client, so a per-tab lock would
    serialise nothing that matters.
    """

    def __init__(self, gmails: GmailPool, proxies: ProxyPool, apps: AppPool,
                 phones: PhoneLog, lists=None, lock: threading.Lock | None = None):
        self.gmails = gmails
        self.proxies = proxies
        self.apps = apps
        self.phones = phones
        # Only sync_lists needs these, and only when a real workbook is open.
        self._lists = lists
        self._lock = lock or threading.Lock()

    @classmethod
    def open(cls, settings: Settings) -> Book:
        settings.require_sheets()
        try:
            import gspread
            from google.oauth2.service_account import Credentials as Key
        except ImportError as exc:                                # pragma: no cover
            raise SheetError(f"missing dependency: {exc}") from exc

        client = gspread.authorize(
            Key.from_service_account_file(str(settings.service_account_json),
                                          scopes=SCOPES))
        try:
            book = client.open_by_key(settings.sheet_id)
        except Exception as exc:                                  # gspread errors vary
            raise SheetError(f"could not open the spreadsheet: {exc}") from exc

        lock = threading.Lock()
        tabs = {ws.title: ws for ws in book.worksheets()}
        missing = [name for name in (GMAILS_TAB, PROXY_TAB, APPS_TAB, PHONES_TAB)
                   if name not in tabs]
        if missing:
            raise SheetError(
                f"the spreadsheet has no tab(s) named: {', '.join(missing)}\n"
                f"found: {', '.join(sorted(tabs))}\n"
                f"`geelark build` reads the resource tabs; `geelark run` reads "
                f"the single-row sheet named by GOOGLE_SHEET_TAB."
            )

        def headers(name: str) -> list[str]:
            return [h.strip() for h in tabs[name].row_values(1)]

        pools = cls(
            gmails=GmailPool(tabs[GMAILS_TAB], headers(GMAILS_TAB), lock),
            proxies=ProxyPool(tabs[PROXY_TAB], headers(PROXY_TAB), lock),
            apps=AppPool(tabs[APPS_TAB], headers(APPS_TAB), lock),
            phones=PhoneLog(tabs[PHONES_TAB], headers(PHONES_TAB), lock),
            lists=tabs.get(LISTS_TAB), lock=lock,
        )
        for pool in (pools.gmails, pools.proxies, pools.apps):
            pool.load()
        return pools

    def sync_lists(self) -> dict[str, list[str]]:
        """Rewrite the Lists tab so each dropdown offers what a run can write.

        The lists were maintained by hand and drifted, in both directions at
        once: the Gmail column offered three statuses no build ever writes -
        device failures, which stop the phone rather than mark the address -
        and omitted two it does. A dropdown that disagrees with the code is
        worse than none, because it invites setting a status by hand that the
        pool will then act on.

        Derived from failures.py, so the answer comes from the same table the
        build consults. Run it after a flow grows a new reason.
        """
        if self._lists is None:
            raise SheetError("this workbook has no Lists tab to sync")

        from . import builder, failures
        from .flows import chatgpt_login, google_login

        def credential_reasons(module) -> list[str]:
            return sorted(r for r in failures.reasons_reported_by(module)
                          if failures.verdict(r).costs_the_credential)

        wanted = {
            "Gmail Statuses": [GmailPool.claimed_status, GmailPool.spent_status,
                               GmailPool.retired_status,
                               *credential_reasons(google_login)],
            "GPT Statuses": [AppPool.claimed_status, AppPool.spent_status,
                             AppPool.retired_status,
                             # Not a failure reason - the account was asked
                             # something, not judged - so the taxonomy does not
                             # supply it and it has to be named here.
                             AppPool.challenged_status,
                             *credential_reasons(chatgpt_login)],
            # The proxy tab's words are its own - a proxy is occupied and let
            # go, never judged - so they come from the pool, not the taxonomy.
            "Proxy Statuses": ["free", ProxyPool.claimed_status,
                               ProxyPool.spent_status, "dead"],
            # A phone's status is what a build ended on, which is the builder's
            # vocabulary rather than any one flow's.
            "Phone Statuses": builder.possible_statuses(),
        }

        with self._lock:
            grid = self._lists.get_all_values()
        head = grid[0]
        payload = []
        for column, values in wanted.items():
            if column not in head:
                continue
            letter = a1_column(head.index(column) + 1)
            # Cleared to the bottom first: a shorter list must not leave the
            # tail of the old one behind, still selectable.
            for offset in range(max(len(grid) - 1, len(values))):
                value = values[offset] if offset < len(values) else ""
                payload.append({"range": f"{letter}{offset + 2}",
                                "values": [[value]]})
        if payload:
            batch_write(self._lists, self._lock, payload, what="Lists")
        return wanted

    def reload(self) -> None:
        """Re-read the pools after something changed a tab underneath them.

        Deleting a phone frees its app account, and the in-memory rows were
        read before that happened - a build using them would not see the
        account it had just been handed back.
        """
        for pool in (self.gmails, self.proxies, self.apps):
            pool.load()

    def release_stuck(self) -> int:
        """Free every row a dead run left claimed. Reported rather than done
        quietly: a row that is genuinely in use by another run would be handed
        out twice by this."""
        freed = 0
        for pool in (self.gmails, self.proxies, self.apps):
            for resource in pool.stuck:
                pool.release(resource, note=(
                    "Freed by hand: it was left claimed, and no run was "
                    "holding it."))
                log.info("%s: released %s", pool.tab, resource.label)
                freed += 1
        return freed
