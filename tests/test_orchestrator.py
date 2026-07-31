"""Concurrency safety.

Every failure mode here costs money instead of raising: a lost ledger entry is a
phone nothing can account for, and a torn sheet write is a row whose phone id no
longer matches its account. Neither would show up as an error, so neither can be
found by running the thing and looking.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from geelark_farm.ledger import Ledger


def test_parallel_records_do_not_lose_entries(tmp_path):
    """Two workers creating phones at the same moment must both be recorded.

    Without the lock held across read-modify-write, one entry overwrites the
    other and its phone becomes invisible to `reap` - running, billing, and
    tracked by nothing.
    """
    ledger = Ledger.load(tmp_path)

    def record(index: int) -> None:
        ledger.record(f"PHONE{index}", label=f"row {index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(50)))

    assert len(ledger.entries) == 50
    assert len(Ledger.load(tmp_path).entries) == 50, "the file must agree too"


def test_parallel_claim_and_release_stay_consistent(tmp_path):
    ledger = Ledger.load(tmp_path)
    for index in range(20):
        ledger.record(f"PHONE{index}")

    def cycle(index: int) -> None:
        phone = f"PHONE{index}"
        ledger.claim(phone, label=f"row {index}")
        ledger.release(phone, note="done")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(cycle, range(20)))

    assert ledger.claimed() == []
    reloaded = Ledger.load(tmp_path)
    assert len(reloaded.entries) == 20
    assert all(e.released_at is not None for e in reloaded.entries.values())


def test_the_saved_file_is_never_torn(tmp_path):
    """Written via a temporary file and os.replace, so a reader mid-write sees
    the old file or the new one - never half of either."""
    ledger = Ledger.load(tmp_path)
    ledger.record("PHONE1")

    stop = threading.Event()
    failures: list[Exception] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                Ledger.load(tmp_path)
            except Exception as exc:                              # noqa: BLE001
                failures.append(exc)

    watcher = threading.Thread(target=reader, daemon=True)
    watcher.start()
    for index in range(100):
        ledger.record(f"PHONE{index}")
    stop.set()
    watcher.join(timeout=5)

    assert not failures
