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
    Phones     Created, Serial, State, Proxy, Gmail, GPT Account, Status, Note

The Phones tab is keyed on `Serial` - the number the panel, the notes and the
operator all call a phone by. GeeLark's own id is not stored: it is twenty
digits nobody reads, and the listing turns a serial into one at the moment
something needs to address the phone. `Proxy` there holds the exit's *name*,
`SX4`, since the address it stands for is one column away in the Proxy tab.

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
from .config import Settings, machine
from .gsheet import SCOPES, SheetError, a1_column, batch_write
from .proxy import Proxy, ProxyError
from .proxy import parse as parse_proxy

log = logging.getLogger(__name__)

GMAILS_TAB = "Gmails"
PROXY_TAB = "Proxy"
APPS_TAB = "Gpt Info"
PHONES_TAB = "Phones"
LISTS_TAB = "Lists"
HISTORY_TAB = "History"

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
    def name(self) -> str:
        """The vendor's name for this row - `SX4` - when the tab carries one.

        Blank for a tab with no Name column, and every caller falls back to the
        address, so adding the column is what turns this on rather than a
        setting.
        """
        return (self.values.get("Name") or "").strip()

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
            return f"{self.name} ({self.proxy})" if self.name else str(self.proxy)
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
        """What makes two rows the same resource rather than two of them.

        A proxy is its endpoint *and* its username. Host and port alone looked
        right while every proxy was its own gateway, and it is wrong for a
        vendor that multiplexes: ten live proxies on one `79.127.168.43:50101`,
        told apart only by the username, would have been read as one row and
        nine duplicates (2026-08-14). Two rows that agree on all three really
        are the same proxy, which is the case this exists to catch.
        """
        if resource.credentials:
            return resource.credentials.email.strip().lower()
        if resource.proxy:
            return (f"{resource.proxy.host}:{resource.proxy.port}"
                    f":{resource.proxy.username}")
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

    def _off_a_phone(self, status: str, note: str) -> dict[str, str]:
        """The fields for any row leaving a device, whatever it leaves for.

        The serial column names the phone that has this resource *now*, so
        anything that takes it off one clears it. Leaving a serial behind
        points whoever reads the row at a phone that no longer exists, and the
        same stale reference quietly held thirteen proxies out of the pool for
        days (2026-08-11).

        It was written out once, in `retire`, and the reasoning applied to
        every one of these. `release` did not do it - so an app account freed
        because its phone was marked failed went back into the pool still
        naming that phone, and three rows in the live tab said `Phone Serial
        684` about a phone deleted hours earlier (2026-08-13).
        """
        fields = {self.status_column: status, self.note_column: note}
        if self.serial_column:
            fields[self.serial_column] = ""
        return fields

    def retire(self, resource: Resource, *, note: str = "") -> None:
        """Take a credential out of circulation for good."""
        self._set(resource, self._off_a_phone(self.retired_status, note))

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
        self._set(resource, self._off_a_phone("", note))

    def set_aside(self, resource: Resource, *, reason: str = "",
                  note: str = "") -> None:
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

    #: The fallback only. A set-aside account normally gets the *reason* as
    #: its status - `email_code_required` - because that is the word every
    #: other surface already uses for the same event: the terminal summary,
    #: the logs, the History note. The first design wrote `challenged` here,
    #: and the operator had to ask what it meant (2026-08-17); a status that
    #: needs a glossary is not doing its one job.
    #:
    #: What has NOT changed since the word was introduced (2026-08-13, when a
    #: set-aside row went back blank and three runs re-proved the same two
    #: accounts): the row still waits for a person. None of these words is in
    #: `available_statuses`, so nothing claims the account until the status is
    #: blanked - and the Note still says it was asked rather than judged,
    #: which is the difference between this and `fail()`.
    challenged_status = "challenged"

    def set_aside(self, resource: Resource, *, reason: str = "",
                  note: str = "") -> None:
        self._set(resource, self._off_a_phone(reason or self.challenged_status,
                                              note))

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

    #: What an exit gets when a service refused the connection through it.
    #:
    #: This reverses what the module used to do, deliberately and on the
    #: operator's instruction. A refusal was measured to be per-session rather
    #: than per-proxy - across twelve attempts every gateway produced both
    #: successes and rejections (2026-08-09) - so a refused exit went straight
    #: back to the pool. That is still true about the *proxy*. What it misses
    #: is the *address*: these rows carry no `Port ID`, so nothing here can ask
    #: sx.org for a new exit address, and the only thing that changes one is a
    #: hand in the vendor's panel. Sending the row back unmarked hands the next
    #: build the same address to be refused through again.
    #:
    #: Not in `available_statuses`, so it waits. Blank the cell - or write
    #: `free` - once the address has been changed.
    needs_new_ip = "change ip"

    #: A proxy GeeLark could not reach. Not a verdict for good: these are
    #: rented by the month and renewed on the same address, so one that stopped
    #: answering yesterday is often answering again today. Re-tested every run
    #: alongside the free ones - see builder.check_proxies.
    dead_status = "dead"

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
        self._set(resource, self._off_a_phone("free", note))

    def set_aside(self, resource: Resource, *, reason: str = "",
                  note: str = "") -> None:
        """Hold an exit back until its address has been changed by hand.

        The reason is not written: whichever refusal it was, the remedy on
        this tab is the same one - `change ip` - and that is what the column
        answers."""
        self._set(resource, self._off_a_phone(self.needs_new_ip, note))

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
        shared = "," in serials
        self._set(resource, {
            self.status_column: self.spent_status,
            self.serial_column: serials,
            self.note_column: (
                f"Shared by phones {serials} - a build ran out of free exits "
                f"and took this one. Both accounts reach the services from "
                f"this address." if shared else f"On phone {serials}.")})

    def find_proxy(self, address: str) -> Resource | None:
        """The row for this exit, matched on host and port.

        The rest of the string is not compared: the caller has `socks5://user:
        ***@host:port`, with the password already masked for logging, and the
        pair that identifies a row is the endpoint anyway.
        """
        endpoint = (address or "").rpartition("@")[2] or (address or "")
        host, _, port = endpoint.partition(":")
        if not host or not port:
            return None
        return next((r for r in self._rows if r.proxy
                     and r.proxy.host == host and str(r.proxy.port) == port),
                    None)

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
            if cell("Status") in (self.BUILDING, self.READY) or not cell("Serial"):
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
            found.append({"sheet_row": offset, "serial": cell("Serial"),
                          "gmail": cell("Gmail"), "proxy": cell("Proxy"),
                          "status": reason})
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

    def locate(self, serial: str) -> int | None:
        """The row this phone is on *now*.

        A row number is not a durable handle to a phone. `start` hands one back
        and a build holds it for ten minutes while its siblings work, and any
        one of them deleting its own row shifts every row below it up - so the
        number a build remembers can come to mean a different phone. Writing
        through it then puts one build's result on another's row and loses
        both (2026-08-14, phone 751).

        The serial is durable, so everything that writes after a build has been
        running looks the row up again by it.
        """
        wanted = str(serial).strip()
        if not wanted:
            return None
        index = self._index.get("Serial")
        if index is None:
            return None
        with self._lock:
            raw = self._ws.get_all_values()
        for offset, line in enumerate(raw[1:], start=2):
            if index < len(line) and line[index].strip() == wanted:
                return offset
        return None

    def write(self, serial: str, **fields: str) -> bool:
        """Write to the row this phone is on now. False if it has none."""
        sheet_row = self.locate(serial)
        if sheet_row is None:
            return False
        self.finish(sheet_row, **fields)
        return True

    def drop(self, serial: str) -> bool:
        """Remove this phone's row, wherever it has moved to."""
        sheet_row = self.locate(serial)
        if sheet_row is None:
            return False
        self.delete_rows([sheet_row])
        return True

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


class HistoryLog:
    """The append-only record of what happened, visible from every machine.

    Two gaps this closes at once. The Phones tab is a *current-state* table:
    a row marked `done` is deleted, so "what did we build on Tuesday and why
    did two of them fail" had no answer anywhere. And the run summary was
    printed to a terminal and died with it - on the other machine it was
    never visible at all.

    One row per event, appended, never edited or deleted. `Machine` says which
    device wrote it, which is what makes a problem hit on the Mac readable
    from Windows. Columns are fixed and written by position, because this tab
    is machine-written; reordering them by hand would scramble later rows.

    `append_row` rather than the find-a-row-then-write dance `PhoneLog.start`
    does: the Sheets append API places the row server-side, so two workers
    appending at once cannot land on the same line.
    """

    HEADERS = ["When", "Machine", "Serial", "Event", "Seconds", "Proxy",
               "Gmail", "GPT Account", "Note"]

    def __init__(self, worksheet, lock: threading.Lock):
        self._ws = worksheet
        self._lock = lock

    def append(self, **fields: str) -> None:
        row = [str(fields.get(name, "")) for name in self.HEADERS]
        with self._lock:
            self._ws.append_row(row, value_input_option="RAW")


class Book:
    """The workbook and its four tabs, sharing one lock.

    One lock rather than one per tab: gspread is not documented thread-safe and
    the tabs are reached through the same client, so a per-tab lock would
    serialise nothing that matters.
    """

    def __init__(self, gmails: GmailPool, proxies: ProxyPool, apps: AppPool,
                 phones: PhoneLog, lists=None, history: HistoryLog | None = None,
                 lock: threading.Lock | None = None):
        self.gmails = gmails
        self.proxies = proxies
        self.apps = apps
        self.phones = phones
        # Only sync_lists needs these, and only when a real workbook is open.
        self._lists = lists
        self.history = history
        self._lock = lock or threading.Lock()

    def record_history(self, **fields: str) -> None:
        """Append one event to the History tab, if this workbook has one.

        When and Machine are filled here so no caller can forget them - they
        are the two columns the tab exists for. Never raises: history is a
        record of the work, not part of it, and a build must not fail because
        its footnote could not be written.
        """
        if self.history is None:
            return
        fields.setdefault("When", time.strftime("%Y-%m-%d %H:%M"))
        fields.setdefault("Machine", machine())
        try:
            self.history.append(**fields)
        except Exception as exc:                                  # noqa: BLE001
            log.error("the History row was not written (%s): %s", exc, fields)

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

        # The one tab this tool creates for itself. The four above are stock
        # someone fills in, so a missing one is an error worth stopping on;
        # History is machine-written, and demanding the operator make it by
        # hand would just mean every workbook is missing it until the day
        # somebody needs what it would have held.
        history = None
        try:
            if HISTORY_TAB in tabs:
                sheet = tabs[HISTORY_TAB]
            else:
                sheet = book.add_worksheet(
                    HISTORY_TAB, rows=2000, cols=len(HistoryLog.HEADERS))
                sheet.append_row(HistoryLog.HEADERS)
                log.info("created the %s tab", HISTORY_TAB)
            history = HistoryLog(sheet, lock)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("no History tab this session (%s)", exc)

        pools = cls(
            gmails=GmailPool(tabs[GMAILS_TAB], headers(GMAILS_TAB), lock),
            proxies=ProxyPool(tabs[PROXY_TAB], headers(PROXY_TAB), lock),
            apps=AppPool(tabs[APPS_TAB], headers(APPS_TAB), lock),
            phones=PhoneLog(tabs[PHONES_TAB], headers(PHONES_TAB), lock),
            lists=tabs.get(LISTS_TAB), history=history, lock=lock,
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
            # Set-aside reasons are offered too: a set-aside row carries its
            # reason as the status now, so the dropdown has to know the word
            # or the sheet flags the very value a run just wrote.
            return sorted(r for r in failures.reasons_reported_by(module)
                          if failures.verdict(r).costs_the_credential
                          or failures.verdict(r).sets_aside)

        wanted = {
            "Gmail Statuses": [GmailPool.claimed_status, GmailPool.spent_status,
                               GmailPool.retired_status,
                               *credential_reasons(google_login)],
            "GPT Statuses": [AppPool.claimed_status, AppPool.spent_status,
                             AppPool.retired_status,
                             *credential_reasons(chatgpt_login)],
            # The proxy tab's words are its own - a proxy is occupied and let
            # go, never judged - so they come from the pool, not the taxonomy.
            "Proxy Statuses": ["free", ProxyPool.claimed_status,
                               ProxyPool.spent_status,
                               ProxyPool.needs_new_ip,
                               ProxyPool.dead_status],
            # A phone's status is what a build ended on, which is the builder's
            # vocabulary rather than any one flow's.
            "Phone Statuses": builder.possible_statuses(),
        }

        with self._lock:
            grid = self._lists.get_all_values()
        head = grid[0]

        def column_now(letter_index: int) -> list[str]:
            return [row[letter_index].strip() if letter_index < len(row) else ""
                    for row in grid[1:]]

        payload = []
        for column, values in wanted.items():
            if column not in head:
                continue
            index = head.index(column)
            # Compared before writing, so a run that changes nothing sends
            # nothing. This is called every session now, and a write per
            # session against a tab that has not moved is an API call spent to
            # learn what a read already said.
            if [v for v in column_now(index) if v] == list(values):
                continue
            letter = a1_column(index + 1)
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
