"""A local record of every phone this tool has created.

Why it exists: the spreadsheet is the authority on which accounts are done, but
it is updated at the *end* of a row. Between "phone created" and "row updated"
there is a window where a crash would leave a phone that nothing knows about -
still billing, and invisible to a re-run. Three orphan phones were created that
way in the prototype.

So the ledger is written the instant a phone exists, before anything else can
fail, and `reap` uses it to decide what is safe to stop.

Not a database: a single JSON file, written atomically, guarded by a lock for
rows running in parallel.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import ClassVar

from . import config

log = logging.getLogger(__name__)

# A phone claimed for longer than this has been abandoned by a dead process:
# nothing legitimately holds one for that long, and it is billing the whole time.
#
# The same window the sheet's pools use, and it has to stay the same window.
# They answer one question between them - "is the process that claimed this
# still alive" - and a run holds its phone and its Gmail for exactly as long as
# it holds either.
#
# When they disagreed, the gap was the bug. `STALE_CLAIM_DEFAULT` was shortened
# to five minutes once every writer beat (config.py), and this was left at two
# hours: `free_abandoned_claims` handed a dead run's Gmail back to the pool
# after five minutes while `settle_abandoned` still read that run's phone as
# held, so for the next hour and fifty-five minutes the same address could be
# signed into a second phone - the one mistake here that costs an account
# rather than a minute (2026-08-28).
#
# The default, and only the default. The number a run actually uses is
# `settings.stale_claim_seconds`, resolved once and handed to `Ledger.load`,
# which stamps it onto every Entry it holds. This is what an Entry built by
# hand answers with, and what a Ledger loaded without a window falls back to.
#
# It was the phone lease itself until 2026-08-31, and that is what made the
# bug above possible: `.env` moved the credential lease and could not move
# this one, so the two describing the same dead run could disagree.
STALE_CLAIM_SECONDS = config.STALE_CLAIM_DEFAULT


#: The store the ledger lives in when `use_store` was called - see
#: `PgLedger`. Empty means the file, as it always was.
_STORE: dict = {}


def use_store(settings) -> bool:
    """Route every `Ledger.shared` and `Ledger.load` in this process to the
    store's `phone_claims` table (LEDGER_IN_PG=1 with the store on).
    Called once at start by every role; the call sites do not change."""
    if getattr(settings, "ledger_in_pg", False) and getattr(
            settings, "store_enabled", False):
        _STORE["settings"] = settings
        return True
    _STORE.pop("settings", None)
    _STORE.pop("ledger", None)
    return False


#: The process's Ledgers, one per file - see `Ledger.shared`.
_SHARED: dict = {}
_SHARED_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


@dataclass
class Entry:
    """One phone, and who is responsible for it."""

    #: The staleness window this entry is measured against, in seconds.
    #:
    #: A ClassVar and not a dataclass field, deliberately. `save` serialises
    #: every field through `asdict`, and `load` restores every field it knows
    #: by name - so a window kept as a field would be written into
    #: ledger.json and read back on the next start, and a phone claimed under
    #: yesterday's window would keep yesterday's window for ever, across
    #: restarts, invisibly. That is a worse version of the bug above, not a
    #: fix for it.
    #:
    #: The Ledger holding this entry sets it per instance; the class value is
    #: the module default, for an Entry built by hand.
    stale_after: ClassVar[float] = STALE_CLAIM_SECONDS

    phone_id: str
    created_at: float
    serial: str | int | None = None
    label: str = ""              # e.g. "row 4 / user@example.com"
    proxy: str = ""              # endpoint only, never the password
    # Set while a run is working with this phone; cleared when it is finished.
    # A claim older than STALE_CLAIM_SECONDS means the owner died.
    claimed_at: float | None = None
    released_at: float | None = None
    note: str = ""

    @property
    def is_claimed(self) -> bool:
        return self.claimed_at is not None and self.released_at is None

    @property
    def is_stale(self) -> bool:
        return self.is_claimed and (_now() - self.claimed_at) > self.stale_after


@dataclass
class Ledger:
    """The phones file. Load, mutate, save - each save is atomic.

    Every mutation holds the lock across read-modify-write, not just across the
    file write. With workers running in parallel, two threads recording phones
    at the same moment would otherwise interleave and one entry would be lost -
    and a phone missing from the ledger is a phone `reap` cannot account for,
    left billing with nothing tracking it.

    Re-entrant because the mutators call save(), which takes the lock too.
    """

    path: Path
    entries: dict[str, Entry] = field(default_factory=dict)
    #: Resolved once per process from `settings.stale_claim_seconds` and
    #: stamped onto every Entry this Ledger holds. Per Ledger rather than per
    #: Entry so that two Ledger objects in one process cannot come to
    #: disagree the way the constant and the setting did.
    stale_after: float = STALE_CLAIM_SECONDS
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    #: The claims *this process* made. `beat` restamps these and no others:
    #: it restamped every unreleased claim in the file, so a process that
    #: started after a kill kept the dead run's claims fresh forever, and
    #: `settle_abandoned` left three `building` rows - two of them running
    #: and billing - "to a run" that no longer existed, through two more
    #: restarts (2026-09-08, phones 1991, 1992, 1995).
    _mine: set = field(default_factory=set, repr=False)
    #: The file's mtime as of the last read or write by this object: a
    #: reload happens only when somebody else has written since.
    _seen_mtime: int = field(default=-1, repr=False)

    def _reload(self, force: bool = False) -> None:
        """Read the file again before changing it.

        Two processes share this file now - the keeper, which settles and
        prunes, and a builder, which claims and releases (phase 4). Each
        `save` writes the whole file from its own dict, so a process that
        changed it a second ago would have its change erased by the other's
        next save: a claim the builder had just written, gone under the
        keeper's prune, and a phone with no claim is one `settle_abandoned`
        deletes mid-login. Re-reading first makes every mutation
        read-modify-write against the file, not against a memory of it;
        `_mine` keeps saying which claims are this process's to beat.
        """
        try:
            stamp = self.path.stat().st_mtime_ns
        except OSError:
            return                                # no file yet: nothing newer
        if stamp == self._seen_mtime and not force:
            return                                # nobody else wrote it
        try:
            fresh = type(self).load(self.path.parent,
                                    stale_after=self.stale_after)
        except Exception as exc:                                   # noqa: BLE001
            log.warning("could not re-read the ledger before writing it "
                        "(%s); writing what this process remembers", exc)
            return
        # Merged into the objects already held, not swapped for new ones:
        # a caller that keeps the Entry `record` handed back must go on
        # seeing the phone it recorded (the reaper's tests do exactly that).
        for phone_id, theirs in fresh.entries.items():
            mine = self.entries.get(phone_id)
            if mine is None:
                self.entries[phone_id] = theirs
                continue
            for name in (f.name for f in dataclass_fields(Entry)):
                setattr(mine, name, getattr(theirs, name))
        for phone_id in list(self.entries):
            if phone_id not in fresh.entries:
                del self.entries[phone_id]
        self._seen_mtime = stamp

    def _adopt(self, entry: Entry) -> Entry:
        """Every Entry this Ledger holds is measured against this Ledger's
        window and no other.

        One assignment, at the two places an entry arrives, rather than a
        lookup inside `is_stale`: that property is read on a dataclass that
        knows nothing but itself, and giving it a Settings to consult would
        need credentials the test suite has never had.
        """
        entry.stale_after = self.stale_after      # type: ignore[misc]
        return entry

    @staticmethod
    def _read(path: Path, attempts: int = 10) -> str:
        """Read the file, retrying the Windows replace window.

        While `os.replace` swaps the file in, Windows denies other handles - so
        a reader that happens to open at that instant gets PermissionError even
        though nothing is wrong. Retrying is the whole fix; the file is either
        the old one or the new one, never half of either.
        """
        # At least one, so the loop cannot fall through. What was here was
        # `return ""` after it, which is reachable only with attempts=0 and
        # would have answered an empty file - and an empty ledger reads as
        # "no phones exist", which is the one answer that must never be
        # guessed (2026-08-23).
        for attempt in range(max(1, attempts)):
            try:
                return path.read_text(encoding="utf-8")
            except PermissionError:
                if attempt == max(1, attempts) - 1:
                    raise
                time.sleep(0.02 * (attempt + 1))
        raise AssertionError(  # pragma: no cover
            "unreachable: the loop above returns or raises")

    @classmethod
    def shared(cls, state_dir: str | Path, *,
               stale_after: float | None = None) -> Ledger:
        """The one Ledger this process holds for that file.

        `save` rewrites the whole file from the object's own dict, so two
        Ledgers in one process erase each other's phones: the pass loaded
        one per pass, the lane one per turn, the housekeeper one per turn,
        and each job beat the one it was handed. A claim the lane's job had
        just written would vanish under the pass's next save, and a phone
        with no claim is one `reap` stops mid-login (B-1, 2026-09-08). One
        object, one lock, one file - the service asks here, never `load`.
        """
        if _STORE.get("settings") is not None:
            return _pg_shared(stale_after)
        path = Path(state_dir) / "ledger.json"
        with _SHARED_LOCK:
            found = _SHARED.get(path)
            if found is None:
                found = _SHARED[path] = cls.load(state_dir,
                                                 stale_after=stale_after)
            return found

    @classmethod
    def load(cls, state_dir: str | Path, *,
             stale_after: float | None = None) -> Ledger:
        """`stale_after` is the window every claim here is measured against.

        `None` means the module default, which is what a call that has no
        Settings to hand gets. Every caller in `src/` passes the resolved
        setting, and a test walks the AST to keep it that way - a call that
        quietly took the default would be the 2026-08-28 bug again.
        """
        if _STORE.get("settings") is not None and cls is Ledger:
            # The store's ledger answers every `load` too: a command-line
            # tool or a fallback that opened the file would otherwise read
            # a copy nothing writes any more.
            return _pg_shared(stale_after)
        path = Path(state_dir) / "ledger.json"
        ledger = cls(path=path,
                     stale_after=(STALE_CLAIM_SECONDS if stale_after is None
                                  else stale_after))
        if not path.exists():
            return ledger
        try:
            raw = json.loads(cls._read(path) or "{}")
        except json.JSONDecodeError:
            # A corrupt ledger must not stop a run, but it must be loud: it
            # means reap can no longer tell orphans from claimed phones.
            log.error("ledger at %s is corrupt; treating it as empty. "
                      "Run 'geelark phones' and stop anything unexpected.", path)
            return ledger
        known = {f.name for f in dataclass_fields(Entry)} - {"phone_id"}
        for phone_id, data in (raw.get("phones") or {}).items():
            data.pop("phone_id", None)
            # Only the fields this version knows, and one bad entry does not
            # take the rest with it. `Entry(**data)` raised TypeError on any
            # key it had not heard of, and nothing caught it - so a file
            # written by a version with one more field would stop the tool
            # from starting at all, while the phones it accounts for went on
            # running (2026-08-23).
            #
            # This is the file that says what exists and what is billing.
            # Loading nine of ten entries is worse than ten and far better
            # than none.
            unknown = sorted(set(data) - known)
            if unknown:
                log.warning("ledger entry %s has fields this version does not "
                            "know (%s); reading the rest of it",
                            phone_id, ", ".join(unknown))
            try:
                ledger.entries[phone_id] = ledger._adopt(Entry(
                    phone_id=phone_id,
                    **{k: v for k, v in data.items() if k in known}))
            except TypeError as exc:
                log.error("ledger entry %s could not be read (%s); it is "
                          "skipped, so `geelark phones` is the only thing "
                          "that can account for it", phone_id, exc)
        try:
            ledger._seen_mtime = path.stat().st_mtime_ns
        except OSError as exc:
            log.debug("could not stamp the ledger's mtime after loading (%s)", exc)
        return ledger

    def save(self) -> None:
        """Write via a temporary file and replace, so an interrupted write
        cannot leave a truncated ledger - the one file that must survive a
        crash."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "phones": {
                    phone_id: {k: v for k, v in asdict(entry).items()
                               if k != "phone_id"}
                    for phone_id, entry in self.entries.items()
                }
            }
            temp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self._replace(temp)
            try:
                self._seen_mtime = self.path.stat().st_mtime_ns
            except OSError as exc:
                log.debug("could not stamp the ledger's mtime after saving "
                          "(%s)", exc)

    def _replace(self, temp: Path, attempts: int = 10) -> None:
        """os.replace, retried.

        On Windows the replace fails with PermissionError while any other
        handle has the destination open - and something reading the ledger at
        the moment a parallel run writes it is exactly that. Caught by a
        concurrency test rather than in production, where the symptom would have
        been a phone silently missing from the ledger.
        """
        for attempt in range(attempts):
            try:
                os.replace(temp, self.path)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    temp.unlink(missing_ok=True)
                    log.error("could not write the ledger at %s; a phone may "
                              "not be recorded. Run 'geelark phones'.", self.path)
                    return
                time.sleep(0.02 * (attempt + 1))

    # ------------------------------------------------------------ mutations
    def record(self, phone_id: str, *, serial=None, label: str = "",
               proxy: str = "", note: str = "") -> Entry:
        """Register a phone that now exists. Call this before anything else."""
        with self._lock:
            self._reload(force=True)
            entry = self._adopt(
                Entry(phone_id=phone_id, created_at=_now(), serial=serial,
                      label=label, proxy=proxy, note=note))
            self.entries[phone_id] = entry
            self.save()
            return entry

    def claim(self, phone_id: str, label: str = "") -> Entry:
        """Mark that a run is working with this phone right now."""
        with self._lock:
            self._reload(force=True)
            entry = self.entries.get(phone_id) or self.record(phone_id, label=label)
            entry.claimed_at = _now()
            entry.released_at = None
            if label:
                entry.label = label
            self._mine.add(phone_id)
            self.save()
            return entry

    def beat(self) -> list[str]:
        """Restamp every claim this process is holding. Returns their ids.

        A claim was written once and never refreshed, and `is_stale` is five
        minutes - so a build past its fifth minute reads as abandoned to
        anything that asks, including `settle_abandoned` and
        `apply_phone_states`, both of which spare a phone only while its claim
        is live and unstale.

        Nothing has been hurt by that yet for one reason: passes are serial, so
        while a build is running no other pass is looking. That is the whole of
        the protection, and it is not a property of the ledger - it is a
        property of the loop's shape. This is what makes the ledger say the
        truth on its own, which is the prerequisite for ever letting two passes
        overlap (2026-08-29).
        """
        with self._lock:
            self._reload(force=True)
            now = _now()
            held = [phone_id for phone_id, entry in self.entries.items()
                    if entry.is_claimed and phone_id in self._mine]
            for phone_id in held:
                self.entries[phone_id].claimed_at = now
            if held:
                self.save()
            return held

    def release(self, phone_id: str, note: str = "") -> None:
        """Mark the run finished with this phone. After this it should be
        stopped, and reap will stop it if it is not."""
        with self._lock:
            self._reload(force=True)
            entry = self.entries.get(phone_id)
            if not entry:
                return
            entry.released_at = _now()
            if note:
                entry.note = note
            self._mine.discard(phone_id)
            self.save()

    def forget(self, phone_id: str) -> None:
        """Drop a phone that no longer exists (deleted upstream)."""
        with self._lock:
            self._mine.discard(phone_id)
            self._reload(force=True)
            if self.entries.pop(phone_id, None) is not None:
                self.save()

    # --------------------------------------------------------------- queries
    def get(self, phone_id: str) -> Entry | None:
        with self._lock:
            self._reload()
            return self.entries.get(phone_id)

    def claimed(self) -> list[Entry]:
        with self._lock:
            self._reload()
            return [e for e in self.entries.values() if e.is_claimed]


# ------------------------------------------------------------ the store's
def _pg_shared(stale_after: float | None):
    with _SHARED_LOCK:
        found = _STORE.get("ledger")
        if found is None:
            found = _STORE["ledger"] = PgLedger(
                _STORE["settings"],
                stale_after=(STALE_CLAIM_SECONDS if stale_after is None
                             else stale_after))
        return found


_CLAIM_COLUMNS = ("phone_id, serial, label, proxy, note, created_at,"
                  " claimed_at, released_at")


class PgLedger:
    """The ledger in the store's `phone_claims` table - the same verbs as
    `Ledger`, the same `Entry` answers, one row per phone instead of one
    file per host.

    Every verb is one statement against the store, so two builders and a
    keeper on three hosts read and write the same rows with the store's
    own locking between them; nothing is remembered here but which claims
    this process made (`_mine`), which is what `beat` restamps. Timestamps
    are the epoch floats the file held, so `Entry.is_stale` and the window
    it is measured against are untouched.
    """

    def __init__(self, settings, *, stale_after: float = STALE_CLAIM_SECONDS):
        self._settings = settings
        self.stale_after = stale_after
        self.path = Path(settings.state_dir) / "ledger.json"
        self._mine: set = set()
        self._lock = threading.RLock()
        self._imported = False

    # ----------------------------------------------------------- plumbing
    def _connect(self):
        from .store import db

        return db.connect(self._settings)

    def _entry(self, row) -> Entry:
        (phone_id, serial, label, proxy, note, created_at, claimed_at,
         released_at) = row
        entry = Entry(phone_id=phone_id, created_at=float(created_at),
                      serial=serial or None, label=label or "",
                      proxy=proxy or "", note=note or "",
                      claimed_at=(None if claimed_at is None
                                  else float(claimed_at)),
                      released_at=(None if released_at is None
                                   else float(released_at)))
        entry.stale_after = self.stale_after      # type: ignore[misc]
        return entry

    def _import_file_once(self, conn) -> None:
        """The file's phones into an empty table, the first time. A phone
        the file knew and the table did not would otherwise be nobody's,
        which is the one thing this ledger exists to prevent."""
        if self._imported:
            return
        self._imported = True
        if not self.path.exists():
            return
        cur = conn.execute("SELECT count(*) FROM phone_claims")
        if int((cur.fetchone() or [0])[0]):
            return
        old = Ledger.load(self.path.parent, stale_after=self.stale_after)
        for phone_id, entry in old.entries.items():
            conn.execute(
                f"INSERT INTO phone_claims ({_CLAIM_COLUMNS})"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (phone_id) DO NOTHING",
                (phone_id, str(entry.serial or ""), entry.label, entry.proxy,
                 entry.note, entry.created_at, entry.claimed_at,
                 entry.released_at))
        conn.commit()
        if old.entries:
            log.info("imported %d phone(s) from %s into the store's ledger",
                     len(old.entries), self.path)

    def save(self) -> None:
        """Nothing to do: every verb wrote its row already."""

    # ------------------------------------------------------------ writing
    def record(self, phone_id: str, *, serial=None, label: str = "",
               proxy: str = "", note: str = "") -> Entry:
        with self._lock, self._connect() as conn:
            self._import_file_once(conn)
            conn.execute(
                f"INSERT INTO phone_claims ({_CLAIM_COLUMNS})"
                " VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL)"
                " ON CONFLICT (phone_id) DO NOTHING",
                (phone_id, str(serial or ""), label, proxy, note, _now()))
            conn.commit()
            return self._fetch(conn, phone_id)

    def claim(self, phone_id: str, label: str = "") -> Entry:
        with self._lock, self._connect() as conn:
            self._import_file_once(conn)
            conn.execute(
                f"INSERT INTO phone_claims ({_CLAIM_COLUMNS}, claimed_by)"
                " VALUES (%s, '', %s, '', '', %s, %s, NULL, %s)"
                " ON CONFLICT (phone_id) DO UPDATE SET"
                "   claimed_at = EXCLUDED.claimed_at, released_at = NULL,"
                "   claimed_by = EXCLUDED.claimed_by,"
                "   label = CASE WHEN EXCLUDED.label <> '' THEN EXCLUDED.label"
                "                ELSE phone_claims.label END,"
                "   updated_at = now()",
                (phone_id, label, _now(), _now(), _who()))
            conn.commit()
            self._mine.add(phone_id)
            return self._fetch(conn, phone_id)

    def beat(self) -> list[str]:
        with self._lock:
            mine = sorted(self._mine)
            if not mine:
                return []
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE phone_claims SET claimed_at = %s, updated_at = now()"
                    " WHERE phone_id = ANY(%s) AND claimed_at IS NOT NULL"
                    "   AND released_at IS NULL RETURNING phone_id",
                    (_now(), mine))
                held = [r[0] for r in cur.fetchall()]
                conn.commit()
            return held

    def release(self, phone_id: str, note: str = "") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE phone_claims SET released_at = %s,"
                " note = CASE WHEN %s <> '' THEN %s ELSE note END,"
                " updated_at = now() WHERE phone_id = %s",
                (_now(), note, note, phone_id))
            conn.commit()
            self._mine.discard(phone_id)

    def forget(self, phone_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM phone_claims WHERE phone_id = %s",
                         (phone_id,))
            conn.commit()
            self._mine.discard(phone_id)

    # ------------------------------------------------------------ reading
    def _fetch(self, conn, phone_id: str) -> Entry | None:
        cur = conn.execute(
            f"SELECT {_CLAIM_COLUMNS} FROM phone_claims WHERE phone_id = %s",
            (phone_id,))
        row = cur.fetchone()
        return self._entry(row) if row else None

    def get(self, phone_id: str) -> Entry | None:
        with self._lock, self._connect() as conn:
            self._import_file_once(conn)
            entry = self._fetch(conn, phone_id)
            conn.rollback()
            return entry

    def claimed(self) -> list[Entry]:
        with self._lock, self._connect() as conn:
            self._import_file_once(conn)
            cur = conn.execute(
                f"SELECT {_CLAIM_COLUMNS} FROM phone_claims"
                " WHERE claimed_at IS NOT NULL AND released_at IS NULL")
            rows = [self._entry(r) for r in cur.fetchall()]
            conn.rollback()
            return rows

    @property
    def entries(self) -> dict[str, Entry]:
        """Every phone the ledger knows, as `Ledger.entries` was read: a
        dict by phone id. A snapshot - writing into it changes nothing."""
        with self._lock, self._connect() as conn:
            self._import_file_once(conn)
            cur = conn.execute(f"SELECT {_CLAIM_COLUMNS} FROM phone_claims")
            rows = {r[0]: self._entry(r) for r in cur.fetchall()}
            conn.rollback()
            return rows


def _who() -> str:
    """Which process holds a claim: the machine name, for the row."""
    try:
        return config.machine()
    except Exception:                                              # noqa: BLE001
        return ""
