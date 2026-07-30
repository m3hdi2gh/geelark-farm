"""A local record of every phone this tool has created.

Why it exists: the spreadsheet is the authority on which accounts are done, but
it is updated at the *end* of a row. Between "phone created" and "row updated"
there is a window where a crash would leave a phone that nothing knows about -
still billing, and invisible to a re-run. Three orphan phones were created that
way in the prototype.

So the ledger is written the instant a phone exists, before anything else can
fail, and `reap` uses it to decide what is safe to stop.

Not a database: a single JSON file, written atomically, guarded by a lock for
the concurrency planned in phase 7.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# A phone claimed for longer than this has been abandoned by a dead process:
# nothing legitimately holds one for hours, and it is billing the whole time.
STALE_CLAIM_SECONDS = 2 * 60 * 60


def _now() -> float:
    return time.time()


@dataclass
class Entry:
    """One phone, and who is responsible for it."""

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
        return self.is_claimed and (_now() - self.claimed_at) > STALE_CLAIM_SECONDS


@dataclass
class Ledger:
    """The phones file. Load, mutate, save - each save is atomic."""

    path: Path
    entries: dict[str, Entry] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def load(cls, state_dir: str | Path) -> Ledger:
        path = Path(state_dir) / "ledger.json"
        ledger = cls(path=path)
        if not path.exists():
            return ledger
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            # A corrupt ledger must not stop a run, but it must be loud: it
            # means reap can no longer tell orphans from claimed phones.
            log.error("ledger at %s is corrupt; treating it as empty. "
                      "Run 'geelark phones' and stop anything unexpected.", path)
            return ledger
        for phone_id, data in (raw.get("phones") or {}).items():
            data.pop("phone_id", None)
            ledger.entries[phone_id] = Entry(phone_id=phone_id, **data)
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
            temp = self.path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temp, self.path)

    # ------------------------------------------------------------ mutations
    def record(self, phone_id: str, *, serial=None, label: str = "",
               proxy: str = "", note: str = "") -> Entry:
        """Register a phone that now exists. Call this before anything else."""
        entry = Entry(phone_id=phone_id, created_at=_now(), serial=serial,
                      label=label, proxy=proxy, note=note)
        self.entries[phone_id] = entry
        self.save()
        return entry

    def claim(self, phone_id: str, label: str = "") -> Entry:
        """Mark that a run is working with this phone right now."""
        entry = self.entries.get(phone_id) or self.record(phone_id, label=label)
        entry.claimed_at = _now()
        entry.released_at = None
        if label:
            entry.label = label
        self.save()
        return entry

    def release(self, phone_id: str, note: str = "") -> None:
        """Mark the run finished with this phone. After this it should be
        stopped, and reap will stop it if it is not."""
        entry = self.entries.get(phone_id)
        if not entry:
            return
        entry.released_at = _now()
        if note:
            entry.note = note
        self.save()

    def forget(self, phone_id: str) -> None:
        """Drop a phone that no longer exists (deleted upstream)."""
        if self.entries.pop(phone_id, None) is not None:
            self.save()

    # --------------------------------------------------------------- queries
    def get(self, phone_id: str) -> Entry | None:
        return self.entries.get(phone_id)

    def claimed(self) -> list[Entry]:
        return [e for e in self.entries.values() if e.is_claimed]
