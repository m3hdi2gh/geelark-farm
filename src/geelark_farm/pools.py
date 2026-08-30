"""The resource tabs, as pools something can be claimed from.

The sheet this replaced had a different shape, where one row *was* one phone:
a proxy, a Gmail and an app account chosen in advance, and a failure anywhere
in that row failed the whole row. That works while every resource is good and
gets expensive when they are not - a bad Gmail costs a phone, and the proxy it
was paired with is condemned along with it. It was deleted on 2026-08-12.

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
from .gsheet import (
    SCOPES,
    SheetError,
    a1_column,
    batch_write,
    read_cell,
    read_values,
    with_timeout,
)
from .proxy import Proxy, ProxyError
from .proxy import parse as parse_proxy

log = logging.getLogger(__name__)

GMAILS_TAB = "Gmails"
PROXY_TAB = "Proxy"
APPS_TAB = "Gpt Info"
PHONES_TAB = "Phones"
LISTS_TAB = "Lists"
HISTORY_TAB = "History"
SERVICE_TAB = "Service"

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

#: How much of a note any tab keeps. One number, because the Phones tab, the
#: History tab and a resource row are read beside each other, and a sentence
#: should not be cut at three different lengths depending on where it landed.
NOTE_LIMIT = 500


def clip(value: str, limit: int = NOTE_LIMIT) -> str:
    """Shorten a cell, and say that it was shortened.

    The Phones tab and History are not pools, so `Pool._set` does not reach
    them; they cut with a plain slice, which ends a note mid-word and a
    `Steps` cell mid-screen-name with nothing to show for it.
    """
    if len(value) <= limit:
        return value
    return value[:limit - 1].rstrip() + "…"


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
    #: Who judges the credentials in this tab. Three reasons are reported by
    #: both login flows, and their wording names the service - so a row's own
    #: tab is what says whether it was Google or OpenAI that refused.
    service = "the service"
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

    #: The column stamped with the time a row was claimed, so a run that dies
    #: holding it can be told from one that is using it right now. Without it
    #: `in_use` says only "somebody took this", and the only way back was a
    #: hand on the console - which left three Gmails and three exits out of
    #: the pool for a day each time a run died (2026-08-21, 2026-08-22).
    #:
    #: Named per tab because the Proxy tab already stamps one for the exit
    #: rotation, and a second column holding the same value would be noise.
    claimed_at_column = ""

    #: Columns rendered as checkboxes. Held here rather than deduced, because
    #: what needs knowing is which columns Sheets fills in on its own - see
    #: `_has_content`.
    checkbox_columns: frozenset[str] = frozenset()

    #: How the stamp is written. Sortable and readable, and the same format
    #: the Proxy tab has been using since the rotation landed.
    #:
    #: The `Z` says which clock it is, and it is there because its absence cost
    #: an hour. The server runs on UTC; a reader in Iran is UTC+3:30, so every
    #: stamp on the sheet looked three and a half hours stale, and a row claimed
    #: ninety seconds earlier was read as stuck for over three hours
    #: (2026-08-28). The sheet is read by people in a different timezone from
    #: the machine writing it, and an unmarked local-looking stamp is a wrong
    #: answer rather than a missing one.
    CLAIM_FORMAT = "%Y-%m-%d %H:%M:%SZ"

    #: The same stamp without the marker. Rows claimed before the `Z` are still
    #: on the sheet, and one that cannot be parsed is never freed - so reading
    #: has to accept both, whatever writing does. `_when` strips the marker and
    #: parses with this.
    CLAIM_FORMAT_UNMARKED = "%Y-%m-%d %H:%M:%S"

    #: How often a live run restamps what it is holding. The staleness window
    #: has to be a large multiple of this: a beat can be late - a network blip,
    #: a sheet timeout, a machine that swapped - and being late must not read
    #: as being dead. Ten beats of margin is the rule of thumb, so a window
    #: under ten minutes needs this lowered to match.
    HEARTBEAT_SECONDS = 60

    def __init__(self, worksheet, headers: list[str], lock: threading.Lock):
        self._ws = worksheet
        self._lock = lock
        self.headers = headers
        self._index = {name: i for i, name in enumerate(headers)}
        self._rows: list[Resource] = []
        self._claim_lock = threading.Lock()
        #: The rows THIS process is holding right now, by sheet row. Kept so
        #: `beat` can restamp them: a claim that is being refreshed is one a
        #: live run still wants, and that is what tells it apart from a claim
        #: a dead run left behind. Maintained in `_set` rather than in
        #: `claim`/`release`, because that is the one place every path that
        #: starts or ends a claim passes through - including any added later.
        self._held: dict[int, Resource] = {}
        self._held_lock = threading.Lock()

    # ------------------------------------------------------------- reading
    def load(self) -> None:
        raw = read_values(self._ws, self._lock, what=f"the {self.tab} tab")
        self._rows = []
        for offset, line in enumerate(raw[1:], start=2):
            values = {
                name: (line[i].strip() if i < len(line) else "")
                for name, i in self._index.items()
            }
            if not self._has_content(values):
                continue                       # a blank spacer row, not a gap
            resource = Resource(sheet_row=offset, values=values)
            try:
                self._interpret(resource)
            except (AccountError, ProxyError) as exc:
                resource.error = str(exc)
            self._rows.append(resource)
        self._flag_duplicates()

    def _has_content(self, values: dict[str, str]) -> bool:
        """Whether this is a row someone typed, or grid below the data.

        An untouched checkbox is not blank: putting the boxes on a column
        writes `FALSE` into every row of the grid, including the hundreds
        nobody has filled in. Counting that as content turned 29 empty rows
        of the `Gpt Info` tab into 29 rows refused for having no address the
        first time the column went up (2026-08-22).

        A box that is *ticked* on an otherwise empty row does count. Somebody
        did that on purpose, and a row that says "this account signs in with
        an emailed code" without naming the account should be told about.
        """
        return any(value for name, value in values.items()
                   if name not in self.checkbox_columns
                   or value.strip().upper() == "TRUE")

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
        day (2026-08-13, quietmeadow@example.com on rows 5 and 14).

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
    def claim(self, serial: str = "") -> Resource | None:
        """Take the first usable row, marking it so nothing else can.

        The sheet write happens under the same lock as the choice. Without
        that, two workers reaching this at once both see the same first row
        available and both take it.

        `serial` is the phone the row is being taken for, and it is written
        now rather than when the row is spent. Without it the two tabs cannot
        be joined while the work is happening: the Phones tab says `building`
        and this one says `in_use`, and nothing on either says which `in_use`
        belongs to which phone. With three phones running at once that is the
        difference between a tab you can read and a tab you can only count
        (2026-08-29).
        """
        with self._claim_lock:
            for resource in self.available:
                if not self._still_free(resource):
                    continue
                self._set(resource, self._claim_fields(resource, serial))
                log.info("claimed %s from %s%s", resource.label, self.tab,
                         f" for phone {serial}" if serial else "")
                return resource
        # Deliberately NOT re-read here. Looking again would find a row pasted
        # in since the snapshot - which is the thing a service wants and a
        # `geelark build` at a terminal never needed. But `load` replaces every
        # Resource in `_rows`, and a run in flight is holding three of them: its
        # Gmail, its exit and its app account. After a reload those are orphans,
        # and every check that works by identity - the exits this build has
        # already been refused, the accounts it set aside - silently stops
        # matching. Three tests caught exactly that (2026-08-29).
        #
        # The cost of not doing it is one pass of latency: the build ends
        # `no_usable_gpt`, becomes a warm phone, and the next pass finishes it
        # with the account that arrived. That is a wait, not a loss.
        return None

    def note_serial(self, resource: Resource, serial: str) -> None:
        """Say which phone this claimed row is for, once there is one.

        Some rows are claimed before the phone exists - a Gmail has to be,
        because a phone created with no address to sign in is a phone paid for
        and wasted. This is how such a row stops reading `in_use` with nothing
        beside it.
        """
        if serial and self.serial_column:
            self._set(resource, {self.serial_column: serial})

    def _still_free(self, resource: Resource) -> bool:
        """Ask the sheet, not the snapshot, whether this row is still free.

        `_rows` is a picture taken when the Book was opened, and `serve` opens
        a Book per pass (`serve.py`) while SERVE_CONCURRENT lets passes
        overlap - so a batch that has been running ten minutes chooses from a
        picture that old. On 2026-08-30 that gave
        `niloofarizadifard7466@gmail.com` to phone 1435 at 15:19 and to phone
        1442 at 15:29. Both signed in: one ChatGPT account on two devices, one
        of them already handed over.

        `_claim_lock` did not help and could not have. The two batches held
        different `Pool` objects and each took its own lock honestly; what was
        missing was looking at the row rather than at the memory of it.

        One cell, not the tab. Re-reading the tab would replace every
        `Resource`, which is the one thing `claim` must not do - see the note
        at the end of it. Writing the fresh value into this row's own `values`
        is safe, changes no identities, and takes the row out of `available`
        for the rest of this run.

        This narrows the window to the moment between this read and the write
        below. It does not close it: two processes reading a free cell at the
        same instant still both write. Closing it needs a store that can
        compare and set in one operation, which a spreadsheet cannot.

        A read that fails refuses the claim. The costs are not symmetrical: a
        row not taken is one pass of waiting, and a row taken twice is an
        account on two phones.
        """
        index = self._index.get(self.status_column)
        if index is None:                    # no status column to check
            return True
        cell = f"{a1_column(index + 1)}{resource.sheet_row}"
        try:
            fresh = read_cell(self._ws, self._lock, cell,
                              what=f"{self.tab} {cell}")
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not check whether %s is still free (%s); "
                        "leaving it alone", resource.label, exc)
            return False
        if fresh.strip().lower() in self.available_statuses:
            return True
        # Someone else took it while this snapshot aged. Record what the sheet
        # actually says, so `available` stops offering it.
        log.info("%s was taken by another run while this pass was working "
                 "(the tab says %r); looking further down %s",
                 resource.label, fresh, self.tab)
        resource.values[self.status_column] = fresh
        return False

    def _claim_fields(self, resource: Resource,
                      serial: str = "") -> dict[str, str]:
        """What claiming writes. The status, and whatever else a pool needs
        recorded at the moment a row leaves it."""
        fields = {self.status_column: self.claimed_status}
        if self.claimed_at_column:
            fields[self.claimed_at_column] = time.strftime(self.CLAIM_FORMAT)
        if serial and self.serial_column:
            fields[self.serial_column] = serial
        return fields

    def abandoned(self, older_than: float) -> list[Resource]:
        """Rows whose claim has stopped being refreshed.

        `older_than` is the staleness window. A live run restamps what it
        holds every `HEARTBEAT_SECONDS` - see `beat` - so a stamp that has
        stopped moving is proof the run that wrote it is gone, and the window
        only has to outlast a few late beats.

        It was the build budget before the heartbeat, because with nothing
        refreshing a claim the only safe answer was "longer than any run could
        legitimately hold one". It defaults to five minutes now
        (`config.STALE_CLAIM_DEFAULT`), which is five missed beats - a decision
        taken when the server became the only writer (2026-08-28). A machine
        running a version that does not beat puts that back: a window shorter
        than the holder's silence hands a live run's row to somebody else
        mid-build.

        `ledger.STALE_CLAIM_SECONDS` is the same number and has to stay the
        same number - the two answer one question between them, and the gap
        between a short one here and a long one there is a window in which the
        same Gmail reaches a second phone.

        A row with no stamp - one claimed before the column existed - is left
        alone, because "no time recorded" is not "a long time ago".
        """
        if not self.claimed_at_column:
            return []
        cutoff = time.time() - older_than
        found = []
        for resource in self.stuck:
            stamp = (resource.values.get(self.claimed_at_column) or "").strip()
            if not stamp:
                continue
            try:
                # The marker is stripped rather than parsed, so a row claimed
                # before the `Z` existed still reads. Refusing those would have
                # left every claim on the sheet at the moment this shipped
                # unfreeable for good.
                when = time.mktime(time.strptime(
                    stamp.rstrip("Zz"), self.CLAIM_FORMAT_UNMARKED))
            except ValueError:
                # Said, not skipped. A row claimed with a stamp nothing can
                # read is never old enough to be abandoned, so it stays
                # `in_use` for good and nothing anywhere says why - which is
                # one of the ways a row sits stuck with no explanation
                # (2026-08-23). Naming it is what turns that into something a
                # person can fix.
                log.warning("%s row %d is claimed with a date nothing can "
                            "read (%r), so it will never be freed on its own "
                            "- correct the %s cell or clear the status",
                            self.tab, resource.sheet_row, stamp,
                            self.claimed_at_column)
                continue
            if when < cutoff:
                found.append(resource)
        return found

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
                             self.note_column: note})

    # ------------------------------------------------------------- writing
    #: How much of a note is kept. The column is read by a person beside
    #: three columns that are not prose; past this it stops being readable and
    #: starts being a wall. `fail` trimmed its own and every other way of
    #: writing one - retire, release, set_aside, spend - passed the text
    #: straight through, so the guard held on the path that happened to have
    #: it and nowhere else. Here instead, where every one of them arrives.
    NOTE_LIMIT = NOTE_LIMIT

    def _set(self, resource: Resource, fields: dict[str, str]) -> None:
        payload = []
        for name, value in fields.items():
            if name == self.note_column:
                value = clip(value, self.NOTE_LIMIT)
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
        self._note_held(resource, fields)

    def _note_held(self, resource: Resource, fields: dict[str, str]) -> None:
        """Follow the row in and out of this process's keeping.

        A write that sets the status to the claimed one takes it; a write that
        sets the status to anything else gives it back. A write that does not
        touch the status column says nothing about either - which is what the
        heartbeat's own write is, so beating cannot make a row look claimed.
        """
        if self.status_column not in fields:
            return
        with self._held_lock:
            if fields[self.status_column] == self.claimed_status:
                self._held[resource.sheet_row] = resource
            else:
                self._held.pop(resource.sheet_row, None)

    # --------------------------------------------------------- the heartbeat
    def beat(self) -> int:
        """Restamp every row this process is holding, and say how many.

        The stamp is what `abandoned` reads, so refreshing it is how a live
        run says "still mine". Without it the only safe staleness window was
        the whole build budget - an hour - because a claim younger than that
        might belong to a run in progress. A beat every minute makes a stamp
        ten minutes old proof that nobody is there.

        One batch write for the whole pool, not one per row: a run holds three
        rows per phone and fifteen phones at a time, and forty-five separate
        writes a minute is a quota problem rather than a heartbeat.
        """
        column = self._index.get(self.claimed_at_column) if \
            self.claimed_at_column else None
        if column is None:
            return 0
        now = time.strftime(self.CLAIM_FORMAT)
        with self._held_lock:
            held = list(self._held.values())
        payload = []
        for resource in held:
            payload.append({
                "range": f"{a1_column(column + 1)}{resource.sheet_row}",
                "values": [[now]],
            })
            resource.values[self.claimed_at_column] = now
        if payload:
            batch_write(self._ws, self._lock, payload,
                        what=f"{self.tab} heartbeat")
        return len(payload)


class GmailPool(Pool):
    tab = GMAILS_TAB
    service = "Google"
    claimed_at_column = "Claimed"
    serial_column = "Phone Serial"
    # An address that has signed into a phone has spent whatever first-use
    # credit it had, whether that phone went on to work or not. `used` retires
    # it: the Used Date beside it says when.
    retired_status = "used"

    #: One column for whatever the account answers a Google challenge with.
    #:
    #: Two were a column each for the two kinds, which meant every row left one
    #: of them blank and a reader had to look at both to learn anything. They
    #: merge because the values cannot be mistaken for each other: an
    #: authenticator key is base32 - A-Z and 2-7, nothing else - and an address
    #: always carries an `@`. So the cell says which it is, and nothing has to
    #: be declared beside it.
    SECRET_COLUMN = "Secret"

    #: What a `Seller` promises about how its accounts answer a challenge, and
    #: the column that has to be filled for the promise to hold.
    #:
    #: The flow does not read this - it reads the cells, and picks whichever
    #: option Google offers that the row can answer. This is here to catch the
    #: row where the two disagree, before a phone is created for it. A recovery
    #: address pasted into `2FA Secret` is refused for not being a base32 key,
    #: which is true and unhelpful; a row that says `Egypt` with an empty
    #: `Recovery Email` says exactly what is wrong with it.
    #:
    #: Only these two names carry a promise. Any other seller is unchecked, so
    #: an older batch keeps working and a new one is not forced into a category
    #: before anybody knows which it is.
    SELLERS = {"usa": "an authenticator key", "egypt": "a recovery address"}

    def _interpret(self, resource: Resource) -> None:
        values = resource.values
        secret = values.get(self.SECRET_COLUMN, "").strip()
        # `@` is the whole test, and it is decisive: base32 has no `@` in it,
        # and no address is without one.
        recovery = secret if "@" in secret else ""
        credentials = Credentials(
            email=values.get("Address", ""),
            password=values.get("Password", ""),
            totp_secret="" if recovery else normalize_totp_secret(secret),
            recovery_email=recovery,
        )
        # Named, like the app account's. Without it a broken row here and a
        # broken row in `Gpt Info` read identically, and the reader is left
        # to guess which tab to open.
        credentials.validate(what="gmail:")

        promised = self.SELLERS.get(values.get("Seller", "").strip().casefold())
        # Only when the cell holds the *wrong kind*, never when it is empty.
        #
        # Requiring a value refused a whole third kind that nothing had
        # accounted for: accounts sold with no second factor at all, which
        # sign in on password alone and which this code has always handled -
        # `validate` guards its secret check with `if self.totp_secret` and
        # `has_authenticator` is simply False. Two such rows sat unusable
        # under a rule written that morning (2026-08-29).
        #
        # An empty cell is a fact about the account. A cell holding the other
        # seller's kind of value is a mistake, and that is what this catches.
        if promised and secret:
            carries = ("a recovery address" if recovery
                       else "an authenticator key")
            if carries != promised:
                raise AccountError(
                    f"gmail: {credentials.email}: the Seller column says "
                    f"{values.get('Seller', '').strip()!r}, and those accounts "
                    f"answer Google with {promised} - but the Secret column "
                    f"carries {carries}. Fix the cell, or change the Seller.")
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
    service = "OpenAI"
    claimed_at_column = "Claimed"
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

    #: Ticked when the account has no password and no authenticator, and the
    #: only way in is a code the service emails. A checkbox rather than a word
    #: because it is a fact about the account with two states and no shades.
    #:
    #: It is declared rather than inferred for a reason: a blank password cell
    #: means "this account cannot hold one" just as often as it means "nobody
    #: has filled it in yet", and reading the second as the first is how a row
    #: that could never work costs a phone. Untouched, the column is empty and
    #: every row means exactly what it meant before it existed.
    EMAIL_CODE_COLUMN = "Email code"
    checkbox_columns = frozenset({EMAIL_CODE_COLUMN})

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
            # Sheets writes a checkbox as the literal TRUE or FALSE, and a
            # column nobody has ticked yet reads back as an empty string.
            email_code_only=(values.get(self.EMAIL_CODE_COLUMN) or "")
                            .strip().upper() == "TRUE",
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
    #: is the *address*: nothing here can ask for a new one, and the only
    #: thing that changes one is a hand in the vendor's panel. Sending the row
    #: back unmarked hands the next build the same address to be refused
    #: through again.
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

    #: When this exit was last handed to a phone - for the operator to read,
    #: not for the code to sort by. Created automatically if the tab lacks it.
    last_used_column = "Last Used"
    claimed_at_column = last_used_column

    #: How many phones this exit has carried. This is what the ordering below
    #: actually sorts on.
    uses_column = "Times Used"

    @property
    def available(self) -> list[Resource]:
        """Free exits, least used first.

        The base pool hands out the first usable row, which for credentials is
        the contract - "the first one you see in the tab". For exits it is the
        wrong shape: the top of the tab carried every build while the bottom
        sat idle, so a handful of addresses did all the work and collected all
        the suspicion, which is the opposite of what having twenty is for.

        A count, not a timestamp. The operator asked for one full round over
        every proxy before any repeat, and least-used-first *is* that round,
        exactly, with no dependence on how fast the claims arrive: after one
        round every count is 1, the tie falls to sheet order, and the second
        round walks the tab again from the top. A timestamp could not promise
        it - a batch claims its exits inside the same second, every stamp came
        out equal, and the top row went out over and over.

        An unused proxy counts 0, so it is preferred to one used at all, and a
        tab where nobody has filled the column in behaves as it did before.
        """
        free = super().available
        return sorted(free, key=lambda r: (self._uses(r), r.sheet_row))

    def _uses(self, resource: Resource) -> int:
        raw = (resource.values.get(self.uses_column) or "").strip()
        try:
            return int(raw)
        except ValueError:
            # Anything the column cannot be read as a number - a note someone
            # typed, a stray character - counts as never used rather than
            # stopping the run.
            return 0

    def _claim_fields(self, resource: Resource,
                      serial: str = "") -> dict[str, str]:
        fields = super()._claim_fields(resource, serial)
        fields[self.uses_column] = str(self._uses(resource) + 1)
        return fields

    def record_exit(self, resource: Resource, exit_ip: str) -> None:
        """The address the proxy actually came out of, which is the one Google
        and OpenAI judge - never the gateway host in the credentials.

        Only when it changed. This is called for every free proxy every sync,
        and these exits are mostly stable between runs - writing an unchanged
        value spent one of the sixty writes-per-minute Google allows to say
        nothing, and a sync of twenty-one proxies did exactly that until it hit
        the quota (2026-08-17)."""
        if exit_ip and exit_ip != (resource.values.get("Last Exit IP") or "").strip():
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
        # `change ip` survives. Un-marking is meant for `dead`, where a live
        # phone behind the exit contradicts the row outright; this one is an
        # instruction waiting for somebody to change an address in the
        # vendor's panel, and a phone being on it does not make that done.
        # The serial is still recorded, so the tab says both true things.
        keep = self.status_of(resource) == self.needs_new_ip
        self._set(resource, {
            self.status_column: (self.needs_new_ip if keep
                                 else self.spent_status),
            self.serial_column: serials,
            self.note_column: (
                f"Shared by phones {serials} - a build ran out of free exits "
                f"and took this one. Both accounts reach the services from "
                f"this address." if shared else f"On phone {serials}.")})

    def find_by_name(self, name: str) -> Resource | None:
        """The row the vendor's panel calls `SX4`, or None if that is not one
        row exactly.

        Ambiguity answers None on purpose. The caller uses this to decide
        which exit a phone is on, and acting on the wrong row would refresh an
        address some other phone is using - spending one of its three a day
        and moving an exit nobody asked to move. Two rows with one name is a
        sheet to fix, not a guess to make.
        """
        wanted = (name or "").strip().lower()
        if not wanted:
            return None
        found = [r for r in self._rows
                 if r.proxy and r.name.strip().lower() == wanted]
        return found[0] if len(found) == 1 else None

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


class PhoneLog:
    """The `Phones` tab: one row per phone this tool built.

    Written twice. Once when the phone exists and is being worked on, so an
    interrupted run still leaves something that names the phone in GeeLark's
    list; once when the build ends, with what it ended as.
    """

    tab = PHONES_TAB

    #: What the App column says when the target app is on the device. The
    #: third step of three, and the only one the row did not record: `Gmail`
    #: says Google is signed in and `GPT Account` says the app account is, so
    #: without this `incomplete` covered "waiting on an app account" and "the
    #: app never installed" with one word (2026-08-21).
    APP_COLUMN = "App"
    #: How many finishes this phone has been through without becoming ready.
    #:
    #: A phone keeps its Gmail and its empty GPT Account whatever goes wrong,
    #: so `unfinished` goes on offering it - and a fault that is not going to
    #: clear on its own is then a boot, a wait and a failure every time an
    #: account arrives. An install that fails because the network was slow does
    #: succeed on the second go, which is why the answer is a few tries and not
    #: one (2026-08-29).
    TRIES_COLUMN = "Tries"
    #: Tried this many times without success, and it stops being offered.
    #: Three, because the retry is worth having and the fourth has never told
    #: anybody anything the third did not.
    GIVE_UP_AFTER = 3

    #: How the three step columns read at a glance. `Gmail` and `GPT Account`
    #: hold the address that signed in, because the address is the useful
    #: fact; `App` has no address to show, so it gets the tick. All three say
    #: `NO` when the step did not happen.
    #:
    #: The mark is a display convention and stops at the edge of this class.
    #: Everything downstream was written when a blank meant "did not happen",
    #: and `not cell("Gmail")` reads a cross as an address - so `said()` turns
    #: it back into the blank the rest of the code expects, and every reader
    #: here goes through it.
    YES = "✓"
    NO = "✗"
    INSTALLED = YES

    #: The three columns the marks above appear in. `said` is for these and
    #: only these: it is a display convention, and applying it to every cell
    #: means a `\u2717` typed into Status or Note reads as an empty one.
    STEP_COLUMNS = ("Gmail", "GPT Account", APP_COLUMN)

    #: Columns Sheets fills in on its own. Putting checkboxes on one writes
    #: `FALSE` into every row of the grid, so "is this row blank" has to know
    #: to ignore an untouched box - see `Pool._has_content`, where the same
    #: rule already lives and where 29 empty rows became 29 broken ones
    #: without it (2026-08-22). None here yet; the rule is, so the first tick
    #: added to this tab does not have to rediscover it.
    checkbox_columns: frozenset[str] = frozenset()

    @classmethod
    def said(cls, value: str) -> str:
        """The address a cell holds, or "" if it says the step never happened."""
        value = (value or "").strip()
        return "" if value == cls.NO else value

    def _cells(self, line: list) -> dict[str, str]:
        return {name: (line[i].strip() if i < len(line) else "")
                for name, i in self._index.items()}

    def _typed_rows(self, what: str):
        """Every row of the tab somebody actually typed, as (row, cells).

        The three readers below each walked the whole tab with their own copy
        of this, so a rule about what counts as a row had to be remembered
        three times - and the checkbox rule that `Pool` learned in August was
        remembered in none of them.
        """
        raw = read_values(self._ws, self._lock, what=what)
        for offset, line in enumerate(raw[1:], start=2):
            cells = self._cells(line)
            if any(value for name, value in cells.items()
                   if name not in self.checkbox_columns
                   or value.strip().upper() == "TRUE"):
                yield offset, cells

    #: What a run writes in `Status`. Three, because three is how many the
    #: reader acts on differently - see builder.possible_statuses.
    BUILDING = "building"      # a run holds it right now
    READY = "ready"            # signed in, installed, app account on it
    APP_ONLY = "app_only"     # Google in, the app on it, no account
    #: Neither a product nor a run in progress: the Gmail signed in but the app
    #: never made it onto the device, so there is nothing to sign an account
    #: into and nothing to hand anybody.
    #:
    #: It used to be `app_only` - every build that was not `ready` was - and
    #: the `App` column beside it said `x` while the status said the app was
    #: there. That was merely vague while the word meant "not finished". It
    #: became actively misleading the day the word came to name a product
    #: somebody takes off the shelf (2026-08-29).
    INCOMPLETE = "incomplete"

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
        fields.setdefault("Created", time.strftime("%Y-%m-%d %H:%MZ"))
        fields.setdefault("Status", self.BUILDING)
        fields.setdefault("State", self.UNUSED)
        line = [""] * self.width
        for name, value in fields.items():
            index = self._index.get(name)
            if index is not None:
                line[index] = value
        with self._append_lock:
            with self._lock:
                used = len(read_values(self._ws, what="the Phones tab"))
                # The grid has to reach the row before anything can be written
                # into it. `delete_rows` removes rows from the grid itself, so
                # a sync that clears out finished phones shrinks the tab to
                # what is left - and the next build appends past the end and
                # is refused with "exceeds grid limits". Every phone in that
                # batch then dies on its first sheet write, having already
                # been created: 28 phones on two separate days, each made and
                # destroyed inside a minute (2026-08-18 and 2026-08-21).
                #
                # Under the same lock as the count, or two appends race for
                # the room one of them made.
                short = (used + 1) - self._ws.row_count
                if short > 0:
                    self._ws.add_rows(short)
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
        found = []
        for offset, cells in self._typed_rows("the Phones tab"):
            def cell(name: str, cells: dict = cells) -> str:
                return cells.get(name, "")

            # `building` means a run holds it right now; `ready` means there
            # is nothing left to do. Everything else is a candidate, whatever
            # word it uses - rows written before the statuses were collapsed
            # still say things like no_usable_gpt, and they are picked up on
            # exactly the same test as the ones that say `incomplete`.
            if cell("Status") in (self.BUILDING, self.READY) or not cell("Serial"):
                continue
            # Anything written in `State` means a person has said something
            # about this phone, and none of the three things they can say
            # leaves it available: `taken` is out with somebody, and `done` and
            # `failed` are about to be carried out by the sync. Read here as
            # well as in `marked()` because this is the list a run picks from,
            # and a phone somebody is holding must not be on it.
            if cell("State").strip().casefold() not in ("", self.UNUSED):
                continue
            if self.tries(cells) >= self.GIVE_UP_AFTER:
                # Still in the tab, still readable, simply not offered again.
                # Clearing the cell puts it back in the queue, which is what
                # somebody does after fixing whatever it kept failing on.
                continue
            if not self.said(cell("Gmail")) or self.said(cell("GPT Account")):
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
                          "gmail": self.said(cell("Gmail")),
                          "proxy": cell("Proxy"),
                          "app": self.said(cell(self.APP_COLUMN)),
                          "status": reason})
        # The ones that already have the app come first. Both cost the same
        # app account - the scarce thing - but one of them also needs the
        # install, three or four minutes of a budget that could have gone to
        # the next phone. Offering them in this order turns the same handful
        # of accounts into ready phones sooner.
        found.sort(key=lambda row: row["app"] != self.INSTALLED)
        return found

    def state_of(self, serial: str) -> str:
        """What a person has written in this phone's State cell, right now.

        Read live rather than from anything the run remembers. The point of it
        is to notice a word typed *since* the run started: `unfinished` keeps a
        marked row out of the queue, but a build already under way has no way
        to learn it has been marked, and goes on spending minutes on a phone
        the next sync is going to delete (2026-08-29).

        Never raises. A build must not die because this read failed - the worst
        case is that it carries on, which is what it did before this existed.
        """
        wanted = str(serial).strip()
        try:
            for _offset, cells in self._typed_rows("the Phones tab"):
                if (cells.get("Serial") or "").strip() == wanted:
                    return (cells.get("State") or "").strip().casefold()
        except Exception as exc:                                  # noqa: BLE001
            log.debug("could not read the State of %s (%s)", wanted, exc)
        return ""

    @staticmethod
    def tries(cells: dict) -> int:
        """How many finishes this row has been through. Unreadable counts as
        none: a hand-typed word in the cell must not take a phone out of the
        queue for ever without saying so."""
        try:
            return int(str(cells.get(PhoneLog.TRIES_COLUMN, "")).strip() or 0)
        except ValueError:
            return 0

    def count_try(self, serial: str) -> int:
        """Record one more attempt on this phone, and say how many that makes.

        By serial rather than a remembered row number, like everything else
        that writes here after a build has started: a sibling discarding its
        phone shifts every row below it.
        """
        wanted = str(serial).strip()
        for _offset, cells in self._typed_rows("the Phones tab"):
            if (cells.get("Serial") or "").strip() != wanted:
                continue
            made = self.tries(cells) + 1
            self.write(serial, **{self.TRIES_COLUMN: str(made)})
            return made
        return 0

    def counts(self) -> dict[str, int]:
        """How many phones of each kind the tab holds.

        Three numbers a person actually asks for, and only one of them existed:
        `unfinished()` answered "how much raw stock is there", which is the
        loop's question. The other two are the consumer's - how many phones can
        be taken right now with an account already on them, how many without -
        and how many are out with somebody and therefore not on the shelf.

        Counted off the same rows the rest of this class reads, so it costs
        nothing beyond the read that has already happened.
        """
        ready = waiting = taken = 0
        for _offset, cells in self._typed_rows("the Phones tab"):
            state = (cells.get("State") or "").strip().casefold()
            if state == self.TAKEN:
                taken += 1
                continue
            if state not in ("", self.UNUSED):
                continue                    # done or failed, about to be swept
            if not (cells.get("Serial") or "").strip():
                continue
            if cells.get("Status") == self.READY:
                ready += 1
            elif (cells.get("Status") != self.BUILDING
                  and self.said(cells.get("Gmail", ""))
                  and not self.said(cells.get("GPT Account", ""))):
                waiting += 1
        return {"ready": ready, "app_only": waiting, "taken": taken}

    #: What the operator writes in `State` to say what should happen next.
    #: `Status` is what a run concluded; this is an instruction back to it.
    DONE = "done"          # finished with - delete the phone
    FAILED = "failed"      # something is wrong with it - free its app account
    UNUSED = "unused"      # the default: leave it alone
    #: Out with somebody, and not to be touched. The one word here that is not
    #: an instruction to act - it is an instruction to STOP acting.
    #:
    #: Without it there was nothing a person could write that this loop would
    #: honour. A phone with the app and no account is exactly the product one
    #: consumer takes and signs a customer's own account into by hand - and to
    #: `unfinished` it was indistinguishable from raw stock, so the next
    #: account pasted into Gpt Info could send a run at it. `act_reset_app`
    #: then finds a chat screen this run did not sign in, reads it as the
    #: app's logged-out mode, and `pm clear`s the customer's session away.
    #:
    #: `marked()` deliberately still matches only `done` and `failed`, so this
    #: word never deletes a phone. It only takes it off the shelf
    #: (2026-08-29).
    TAKEN = "taken"

    def marked(self) -> list[dict]:
        """Rows the operator has marked `done` or `failed`.

        Read every time rather than cached: the whole point of the column is
        that it is edited by hand between runs.
        """
        found = []
        for offset, cells in self._typed_rows("the Phones tab"):
            def cell(name: str, cells: dict = cells) -> str:
                return cells.get(name, "")

            state = cell("State").lower()
            if state in (self.DONE, self.FAILED):
                found.append({"sheet_row": offset, "state": state,
                              "serial": cell("Serial"),
                              "gmail": self.said(cell("Gmail")),
                              "app_account": self.said(cell("GPT Account"))})
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
        found = []
        for offset, cells in self._typed_rows("the Phones tab"):
            # `said` on the step columns only. It was applied to every cell,
            # which is a display convention leaking past the class that says
            # it stops there - and a cross typed into Status would have read
            # as an empty one.
            row = {name: self.said(value) if name in self.STEP_COLUMNS else value
                   for name, value in cells.items()}
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
        raw = read_values(self._ws, self._lock, what="the Phones tab")
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

    #: The column that holds prose. Named so `finish` can keep it to a size,
    #: which is `Pool._set`'s job on a resource row and was nobody's here -
    #: so the guard lived at one call site in the builder and every other way
    #: of writing a note went past it (2026-08-23).
    note_column = "Note"

    def finish(self, sheet_row: int, **fields: str) -> None:
        payload = []
        for name, value in fields.items():
            if name == self.note_column:
                value = clip(value)
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

    #: Appended to, never reordered. Rows are written by position, so moving
    #: a column would scramble every row already written under the old one -
    #: which is also why `Steps` sits after `Note` rather than beside the
    #: fields it belongs with.
    HEADERS = ["When", "Machine", "Serial", "Event", "Seconds", "Proxy",
               "Gmail", "GPT Account", "Note", "Steps"]

    def __init__(self, worksheet, lock: threading.Lock):
        self._ws = worksheet
        self._lock = lock

    #: Prose columns, kept to a size for the reason `PhoneLog` keeps its Note
    #: to one: a row of this tab is read beside the others, and `Steps` cut
    #: mid-screen-name says less than one that says it was cut.
    LONG_COLUMNS = ("Note", "Steps")

    def append(self, **fields: str) -> None:
        row = [clip(str(fields.get(name, "")))
               if name in self.LONG_COLUMNS else str(fields.get(name, ""))
               for name in self.HEADERS]
        with self._lock:
            self._ws.append_row(row, value_input_option="RAW")


class ServiceBoard:
    """What the service is doing, in the one place the operator actually reads.

    All of this is in the log already, and the log is on a server the operator
    does not read. Four things stop the loop and none of them wrote a word to
    the spreadsheet: an open breaker, no free profile slots, an empty pool, and
    a pass that throws. From the sheet, every one of them looks the same as a
    loop that is correctly idle - a tab that has simply gone quiet
    (2026-08-28).

    Rewritten in place each pass rather than appended: this is the *current*
    state, and History is where "what happened" belongs. The labels live in
    column A and are written once, at creation; a pass writes only column B, as
    one range, so it costs one call however many rows there are.

    Never fatal. A service that cannot write its dashboard must keep building -
    losing the display is worth a line in the log and not a stopped run.
    """

    #: Written down the sheet in this order. Appended to rather than reordered,
    #: for the same reason History's are: the labels are written once and a
    #: reordering would leave every existing tab labelled wrong.
    ROWS = ("Last pass", "Machine", "Version", "Doing", "Warm stock",
            "Accounts waiting", "Free slots", "Breaker", "Needs you", "Note",
            # Appended, never reordered - the labels are written once and a
            # reordering leaves every existing tab labelled wrong.
            #
            # The three above the fold are the loop's own numbers. These two
            # are the consumer's, and they are the only question he actually
            # has: how many phones can I take right now, of each kind.
            "Ready to take", "App-only to take", "Out with somebody")

    #: Things a person can ask for, as checkboxes in column D beside their
    #: labels in column C. Appended to rather than reordered, like ROWS.
    #:
    #: Checkboxes rather than a word to type, and the reason is the State
    #: column: it takes free text, `done` is matched exactly, and `dome` is
    #: silently nothing at all. A checkbox has two states and no third.
    #:
    #: The loop reads these before it decides and unticks each one the moment
    #: it has read it - not after acting. A pass can run for minutes, and a
    #: tick that lands while it works has to survive to the next pass rather
    #: than be wiped by a write that means "I have dealt with this".
    CONTROLS = ("Clear breaker", "Pause building", "Stop unaccounted phones",
                "Stop everything")

    #: Ticked means "keep doing this", not "do this once", so these are left
    #: ticked after they are read. Unticking a mode would turn a pause into a
    #: single skipped pass, which is not what anybody ticking it wants.
    STANDING = frozenset({"Pause building", "Stop everything"})

    def __init__(self, worksheet, lock: threading.Lock):
        self._ws = worksheet
        self._lock = lock

    def asked(self) -> list[str]:
        """Which controls are ticked, in CONTROLS order.

        Never fatal: a dashboard that cannot be read must not stop the loop
        that would otherwise be building.
        """
        try:
            with self._lock:
                cells = self._ws.get(f"D2:D{len(self.CONTROLS) + 1}")
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not read the %s controls (%s)", SERVICE_TAB, exc)
            return []
        ticked = []
        for index, name in enumerate(self.CONTROLS):
            row = cells[index] if index < len(cells) else []
            if row and str(row[0]).strip().upper() == "TRUE":
                ticked.append(name)
        return ticked

    def taken(self, name: str) -> None:
        """Untick one control, so a tick is one request and not a standing one.

        One cell at a time rather than the block, so a control ticked while
        this pass was working is still ticked when the next pass looks.
        """
        try:
            row = self.CONTROLS.index(name) + 2
            with self._lock:
                self._ws.update([[False]], f"D{row}",
                                value_input_option="USER_ENTERED")
        except Exception as exc:                                  # noqa: BLE001
            # Left ticked, so it will be done again next pass. Every control
            # here is safe to repeat - that is why they are the three.
            log.warning("could not untick %r on the %s tab (%s)",
                        name, SERVICE_TAB, exc)

    def show(self, **fields: str) -> None:
        values = [[clip(str(fields.get(name, "")))] for name in self.ROWS]
        try:
            with self._lock:
                self._ws.update(values,
                                f"B2:B{len(self.ROWS) + 1}",
                                value_input_option="RAW")
        except Exception as exc:                              # noqa: BLE001
            log.warning("could not write the %s tab (%s); carrying on",
                        SERVICE_TAB, exc)


#: The Service tab's colours, as the Sheets API wants them (0..1 floats).
#: The same header band the four hand-made tabs already wore. This one used to
#: be a shade darker and a point larger, which is the kind of difference nobody
#: names and everybody sees (2026-08-29).
_SLATE = {"red": 0.22, "green": 0.28, "blue": 0.31}      # header band
_PAPER = {"red": 0.95, "green": 0.96, "blue": 0.96}      # label column
_RULE = {"red": 0.85, "green": 0.87, "blue": 0.88}       # gridlines
_WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}


def _dress_service(worksheet, rows: int) -> None:
    """Make the Service tab worth looking at.

    The other tabs were laid out by a person and this one is made by the tool,
    so without this it arrives as a bare grid beside four tidy ones - and the
    tab whose whole purpose is being read is the one nobody wants to read.

    Done once, when the tab is created. `Book.open` runs on every pass, and
    formatting on every pass would be one more API call every thirty seconds
    to change nothing.

    Cosmetic, so it is never allowed to matter: a workbook that refuses the
    formatting still gets its dashboard.
    """
    last = rows + 1                        # header row plus one row per field
    grid = {"sheetId": worksheet.id, "startRowIndex": 0, "endRowIndex": last,
            "startColumnIndex": 0, "endColumnIndex": 4}
    edge = {"style": "SOLID", "width": 1, "color": _RULE}

    def band(row_from, row_to, col_from, col_to, fmt, fields):
        return {"repeatCell": {
            "range": {"sheetId": worksheet.id,
                      "startRowIndex": row_from, "endRowIndex": row_to,
                      "startColumnIndex": col_from, "endColumnIndex": col_to},
            "cell": {"userEnteredFormat": fmt},
            "fields": f"userEnteredFormat({fields})"}}

    def width(column, pixels):
        return {"updateDimensionProperties": {
            "range": {"sheetId": worksheet.id, "dimension": "COLUMNS",
                      "startIndex": column, "endIndex": column + 1},
            "properties": {"pixelSize": pixels}, "fields": "pixelSize"}}

    try:
        worksheet.spreadsheet.batch_update({"requests": [
            # The header stays put when the tab is scrolled, which matters the
            # day somebody adds a field to the bottom of ROWS.
            {"updateSheetProperties": {
                "properties": {"sheetId": worksheet.id,
                               "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount"}},
            band(0, 1, 0, 4,
                 {"backgroundColor": _SLATE,
                  "verticalAlignment": "MIDDLE",
                  "textFormat": {"bold": True, "fontSize": 10,
                                 "foregroundColor": _WHITE}},
                 "backgroundColor,textFormat,verticalAlignment"),
            # The labels read as labels rather than as more values. Column C
            # gets the same treatment: it labels the checkboxes beside it.
            band(1, last, 0, 1,
                 {"backgroundColor": _PAPER, "verticalAlignment": "MIDDLE",
                  "textFormat": {"bold": True}},
                 "backgroundColor,textFormat,verticalAlignment"),
            band(1, last, 2, 3,
                 {"backgroundColor": _PAPER, "verticalAlignment": "MIDDLE",
                  "textFormat": {"bold": True}},
                 "backgroundColor,textFormat,verticalAlignment"),
            # `Note` is a sentence, and a sentence that runs off the edge of
            # the cell is the one field here nobody would be able to read.
            band(1, last, 1, 2,
                 {"wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"},
                 "wrapStrategy,verticalAlignment"),
            # The boxes sit under their heading rather than off to its left.
            band(1, last, 3, 4,
                 {"horizontalAlignment": "CENTER",
                  "verticalAlignment": "MIDDLE"},
                 "horizontalAlignment,verticalAlignment"),
            width(0, 190),
            width(1, 620),
            width(2, 210),
            width(3, 60),
            {"updateBorders": {"range": grid, "innerHorizontal": edge,
                               "innerVertical": edge, "top": edge,
                               "bottom": edge, "left": edge, "right": edge}},
        ]})
    except Exception as exc:                                  # noqa: BLE001
        log.warning("could not lay out the %s tab (%s); it will still be "
                    "written, it will just look plain", SERVICE_TAB, exc)


def _make_checkbox(worksheet, position: int) -> None:
    """Turn a freshly added column into real checkboxes.

    Without this the column is text, and a column of the words TRUE and FALSE
    is a column you can typo into. A checkbox has two states and no third, so
    the only thing a hand can do to it is the thing it is for.

    The header row is left out and no end row is given, so the rule covers the
    column to the bottom of the grid - a row pasted in next week gets its box
    without anyone remembering this.
    """
    worksheet.spreadsheet.batch_update({"requests": [{
        "setDataValidation": {
            "range": {"sheetId": worksheet.id, "startRowIndex": 1,
                      "startColumnIndex": position,
                      "endColumnIndex": position + 1},
            "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True},
        },
    }]})


def ensure_columns(worksheet, *columns: str,
                   checkboxes: tuple[str, ...] = ()) -> list[str]:
    """Add columns this tool writes but the operator never fills in.

    `_set` skips a column the tab does not have, silently and by design -
    which is right for the optional ones and wrong for a column something now
    depends on: the exit rotation counts uses to order the pool, and without
    the column every row reads as never used and the order never changes.

    Returns the headers as they now stand, so a column that could not be added
    simply leaves the tab behaving as it did before rather than stopping a run.
    """
    found = [h.strip() for h in worksheet.row_values(1)]
    for column in columns:
        if column in found:
            continue
        try:
            # The grid has to be wide enough before anything can be written
            # into it. A tab sized to its content stops at the last column
            # someone typed - the Proxy tab was six wide - and writing past
            # that is refused as "exceeds grid limits", so the column was
            # never added and the rotation it feeds read every row as never
            # used (2026-08-17).
            short = len(found) + 1 - worksheet.col_count
            if short > 0:
                worksheet.add_cols(short)
            worksheet.update_cell(1, len(found) + 1, column)
            if column in checkboxes:
                _make_checkbox(worksheet, len(found))
            log.info("added the %r column to %s", column, worksheet.title)
            found = found + [column]
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not add %r to %s (%s)",
                        column, worksheet.title, exc)
    return found


def missing_tabs(tabs) -> list[str]:
    """Which of the four a run cannot start without are absent.

    Its own function so it can be asked directly. Inline in `Book.open`, the
    only way to reach it was to build a whole fake gspread client, so nobody
    did - and inverting the test, which makes every workbook look broken, was
    a change no test objected to (2026-08-23).
    """
    return [name for name in (GMAILS_TAB, PROXY_TAB, APPS_TAB, PHONES_TAB)
            if name not in tabs]


class Book:
    """The workbook and its four tabs, sharing one lock.

    One lock rather than one per tab: gspread is not documented thread-safe and
    the tabs are reached through the same client, so a per-tab lock would
    serialise nothing that matters.
    """

    def __init__(self, gmails: GmailPool, proxies: ProxyPool, apps: AppPool,
                 phones: PhoneLog, lists=None, history: HistoryLog | None = None,
                 lock: threading.Lock | None = None,
                 service: ServiceBoard | None = None):
        self.gmails = gmails
        self.proxies = proxies
        self.apps = apps
        self.phones = phones
        # Only sync_lists needs these, and only when a real workbook is open.
        self._lists = lists
        self.history = history
        #: None off a real workbook - a `build` typed by hand has no dashboard
        #: to keep, and every caller checks before writing.
        self.service = service
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
        fields.setdefault("When", time.strftime("%Y-%m-%d %H:%MZ"))
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

        client = with_timeout(gspread.authorize(
            Key.from_service_account_file(str(settings.service_account_json),
                                          scopes=SCOPES)))
        try:
            book = client.open_by_key(settings.sheet_id)
        except Exception as exc:                                  # gspread errors vary
            raise SheetError(f"could not open the spreadsheet: {exc}") from exc

        lock = threading.Lock()
        tabs = {ws.title: ws for ws in book.worksheets()}
        missing = missing_tabs(tabs)
        if missing:
            raise SheetError(
                f"the spreadsheet has no tab(s) named: {', '.join(missing)}\n"
                f"found: {', '.join(sorted(tabs))}\n"
                f"{GMAILS_TAB}, {PROXY_TAB} and {APPS_TAB} are stock you "
                f"fill in and {PHONES_TAB} is where a build writes what it "
                f"produced, so all four have to exist before a run. "
                f"{LISTS_TAB}, {HISTORY_TAB} and {SERVICE_TAB} are made "
                f"automatically.\n"
                f"If none of the names above look familiar, GOOGLE_SHEET_ID "
                f"is pointing at the wrong spreadsheet."
            )

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
            # An existing tab predates whatever was appended to HEADERS since
            # it was made. Every name is passed, not just the new one: the
            # list is append-only, so anything a tab is missing is a suffix of
            # it, and `ensure_columns` appends in the order given - which puts
            # them exactly where rows are written by position.
            ensure_columns(sheet, *HistoryLog.HEADERS)
            history = HistoryLog(sheet, lock)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("no History tab this session (%s)", exc)

        # Made here for the same reason History is: it is machine-written, and
        # requiring the operator to create it by hand would mean every workbook
        # is missing it until the morning somebody needs to know why the
        # service went quiet.
        service = None
        try:
            if SERVICE_TAB in tabs:
                sheet = tabs[SERVICE_TAB]
                if sheet.col_count < 4:
                    # A tab made before the controls existed. Writing C2 into a
                    # two-column grid is a 400, and the `except` below would
                    # take the whole board down with it - so the one thing the
                    # operator reads would go blank because a feature was
                    # added. Widen it in place instead (2026-08-29).
                    sheet.resize(rows=max(sheet.row_count,
                                          len(ServiceBoard.ROWS) + 1), cols=4)
                    sheet.update([["What", "Now", "Ask for", "Tick"]], "A1:D1",
                                 value_input_option="RAW")
                    _make_checkbox(sheet, 3)
                    _dress_service(sheet, len(ServiceBoard.ROWS))
            else:
                sheet = book.add_worksheet(
                    SERVICE_TAB, rows=len(ServiceBoard.ROWS) + 1, cols=4)
                sheet.update([["What", "Now", "Ask for", "Tick"]], "A1:D1",
                             value_input_option="RAW")
                # Real checkboxes, not the words TRUE and FALSE. The State
                # column is what a typo-able control looks like: `done` is
                # matched exactly, so `dome` is silently nothing.
                _make_checkbox(sheet, 3)
                # Only for a tab this made, and only once: the four stock tabs
                # were laid out by a person, and this one arriving as a bare
                # grid beside them is how the tab whose whole job is being read
                # becomes the one nobody reads.
                _dress_service(sheet, len(ServiceBoard.ROWS))
            # The labels, written once. A pass writes only column B, so these
            # are what makes the numbers beside them mean anything - and
            # rewriting them each time would double the cost of every pass.
            sheet.update([[name] for name in ServiceBoard.ROWS],
                         f"A2:A{len(ServiceBoard.ROWS) + 1}",
                         value_input_option="RAW")
            sheet.update([[name] for name in ServiceBoard.CONTROLS],
                         f"C2:C{len(ServiceBoard.CONTROLS) + 1}",
                         value_input_option="RAW")
            service = ServiceBoard(sheet, lock)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("no %s tab this session (%s)", SERVICE_TAB, exc)

        pools = cls(
            gmails=GmailPool(tabs[GMAILS_TAB],
                             ensure_columns(tabs[GMAILS_TAB],
                                            GmailPool.claimed_at_column), lock),
            proxies=ProxyPool(tabs[PROXY_TAB],
                              ensure_columns(tabs[PROXY_TAB],
                                             ProxyPool.uses_column,
                                             ProxyPool.last_used_column), lock),
            apps=AppPool(tabs[APPS_TAB],
                         ensure_columns(
                             tabs[APPS_TAB], AppPool.claimed_at_column,
                             AppPool.EMAIL_CODE_COLUMN,
                             checkboxes=(AppPool.EMAIL_CODE_COLUMN,)), lock),
            phones=PhoneLog(tabs[PHONES_TAB],
                            ensure_columns(tabs[PHONES_TAB],
                                           PhoneLog.APP_COLUMN,
                                           PhoneLog.TRIES_COLUMN), lock),
            lists=tabs.get(LISTS_TAB), history=history, lock=lock,
            service=service,
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
            # And the State column, which is the other direction: what a person
            # tells the loop to do about a phone. It had no list at all, so it
            # took free text and `dome` was silently nothing - the failure mode
            # the checkbox controls on the Service tab exist to avoid.
            "Phone States": [PhoneLog.DONE, PhoneLog.FAILED, PhoneLog.TAKEN,
                             PhoneLog.UNUSED],
        }

        grid = read_values(self._lists, self._lock, what="the Lists tab")
        head = grid[0]

        def column_now(letter_index: int) -> list[str]:
            return [row[letter_index].strip() if letter_index < len(row) else ""
                    for row in grid[1:]]

        payload = []
        deepest = 0
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
                deepest = max(deepest, offset + 2)
                payload.append({"range": f"{letter}{offset + 2}",
                                "values": [[value]]})
        if payload:
            with self._lock:
                # Same grid rule as appending a phone row: a dropdown longer
                # than the tab is refused with "exceeds grid limits", and a
                # flow growing one new reason is all it takes. The Phones tab
                # lost 28 phones to the row version of this before anything
                # noticed (2026-08-21).
                #
                # `deepest` is counted as the ranges are built rather than
                # parsed back out of them: `int("A2"[1:])` reads 2 and
                # `int("AA2"[1:])` raises, so re-deriving a number the loop
                # was holding would fail on the twenty-seventh column of a tab
                # this one is free to grow.
                short = deepest - self._lists.row_count
                if short > 0:
                    self._lists.add_rows(short)
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

    def beat(self) -> int:
        """Say "still mine" about every row this process is holding.

        Called on a timer for as long as a run is working. What it buys is the
        staleness window: a claim nobody is refreshing is a claim nobody is
        using, whatever the build budget says.
        """
        return sum(pool.beat()
                   for pool in (self.gmails, self.proxies, self.apps))

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
