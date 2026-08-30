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
    def load(cls, state_dir: str | Path, *,
             stale_after: float | None = None) -> Ledger:
        """`stale_after` is the window every claim here is measured against.

        `None` means the module default, which is what a call that has no
        Settings to hand gets. Every caller in `src/` passes the resolved
        setting, and a test walks the AST to keep it that way - a call that
        quietly took the default would be the 2026-08-28 bug again.
        """
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
            entry = self._adopt(
                Entry(phone_id=phone_id, created_at=_now(), serial=serial,
                      label=label, proxy=proxy, note=note))
            self.entries[phone_id] = entry
            self.save()
            return entry

    def claim(self, phone_id: str, label: str = "") -> Entry:
        """Mark that a run is working with this phone right now."""
        with self._lock:
            entry = self.entries.get(phone_id) or self.record(phone_id, label=label)
            entry.claimed_at = _now()
            entry.released_at = None
            if label:
                entry.label = label
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
            now = _now()
            held = [phone_id for phone_id, entry in self.entries.items()
                    if entry.is_claimed]
            for phone_id in held:
                self.entries[phone_id].claimed_at = now
            if held:
                self.save()
            return held

    def release(self, phone_id: str, note: str = "") -> None:
        """Mark the run finished with this phone. After this it should be
        stopped, and reap will stop it if it is not."""
        with self._lock:
            entry = self.entries.get(phone_id)
            if not entry:
                return
            entry.released_at = _now()
            if note:
                entry.note = note
            self.save()

    def forget(self, phone_id: str) -> None:
        """Drop a phone that no longer exists (deleted upstream)."""
        with self._lock:
            if self.entries.pop(phone_id, None) is not None:
                self.save()

    # --------------------------------------------------------------- queries
    def get(self, phone_id: str) -> Entry | None:
        return self.entries.get(phone_id)

    def claimed(self) -> list[Entry]:
        return [e for e in self.entries.values() if e.is_claimed]
