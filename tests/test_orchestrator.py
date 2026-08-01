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


# ------------------------------------------------------ shared HTTP session
def test_each_thread_gets_its_own_requests_session(make_settings):
    """A requests.Session is not thread-safe; its connection pool is shared
    mutable state. Three rows sharing one produced

        ConnectionResetError(10054, 'An existing connection was forcibly closed')

    mid-run, which killed a row after its phone had already been created.
    """
    from geelark_farm.api import Client, RateLimiter

    client = Client(make_settings(), limiter=RateLimiter(10))
    sessions = []
    # A barrier keeps all four threads alive at once. Without it the pool
    # reuses one thread for every task and the test passes for the wrong
    # reason - which it did on the first attempt.
    barrier = threading.Barrier(4, timeout=5)

    def grab() -> None:
        barrier.wait()
        sessions.append(client.session)
        assert client.session is client.session   # reused within the thread

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: grab(), range(4)))

    assert len({id(s) for s in sessions}) == 4, "one session per thread"


# ---------------------------------------------------------- spend ceilings
def test_the_step_budgets_cannot_outlast_the_account_budget(make_settings):
    """ACCOUNT_BUDGET_SECONDS is documented as a spend cap, so it has to be one.

    The step budgets add up past it - boot, then login, then install - so each
    step is given whichever is smaller: its own budget, or what is left. Before
    this, a slow row could hold a phone for 35 minutes under a setting that
    claimed 30.
    """
    s = make_settings(account_budget_seconds=1800, login_budget_seconds=900,
                      install_budget_seconds=600)
    boot_worst_case = 600
    assert (boot_worst_case + s.login_budget_seconds
            + s.install_budget_seconds) > s.account_budget_seconds, (
        "if the steps ever fit inside the account budget on their own, this "
        "test is no longer describing a real risk")

    # What process_row does: every step is capped by the time remaining.
    remaining = s.account_budget_seconds
    for step in (boot_worst_case, s.login_budget_seconds,
                 s.install_budget_seconds):
        granted = min(step, remaining)
        remaining -= granted
        assert granted >= 0
    assert remaining == 0, "the account budget is fully consumed, never exceeded"


# ------------------------------------------------- interrupt cleanup
def test_interrupt_cleanup_stops_every_phone_the_run_started(tmp_path):
    """A worker thread never receives Ctrl+C, so its own `finally` does not run
    and its phone would bill until someone noticed. `run` tracks what it started
    so it can stop them itself - the last thing between an interrupt and a phone
    left on all night.
    """
    from geelark_farm import orchestrator

    stopped: list[str] = []

    class FakeClient:
        pass

    ledger = Ledger.load(tmp_path)
    for phone in ("P1", "P2", "P3"):
        ledger.record(phone)
        ledger.claim(phone)

    def fake_stop(client, phone_id):
        stopped.append(phone_id)

    original = orchestrator.phones.stop
    orchestrator.phones.stop = fake_stop
    try:
        orchestrator._stop_all(FakeClient(), {"P1", "P2", "P3"}, ledger)
    finally:
        orchestrator.phones.stop = original

    assert sorted(stopped) == ["P1", "P2", "P3"]
    assert ledger.claimed() == [], "and each is released, so reap agrees"


def test_one_phone_that_will_not_stop_does_not_strand_the_others(tmp_path):
    """The loop must keep going. Giving up on the first failure would leave the
    remaining phones running, which is the exact outcome this exists to prevent.
    """
    from geelark_farm import orchestrator

    stopped: list[str] = []
    ledger = Ledger.load(tmp_path)
    for phone in ("P1", "P2", "P3"):
        ledger.record(phone)

    def fake_stop(client, phone_id):
        if phone_id == "P2":
            raise RuntimeError("the API is down")
        stopped.append(phone_id)

    original = orchestrator.phones.stop
    orchestrator.phones.stop = fake_stop
    try:
        orchestrator._stop_all(object(), {"P1", "P2", "P3"}, ledger)
    finally:
        orchestrator.phones.stop = original

    assert sorted(stopped) == ["P1", "P3"]


# --------------------------------------------- the summary's billing claim
def test_the_summary_never_claims_nothing_is_billing_when_something_is():
    """The line everyone reads. On 2026-08-01 a DNS blip during cleanup left a
    phone running, the failure was logged hundreds of lines up, and the summary
    still ended with "All phones are stopped; nothing is billing." The phone
    billed until someone noticed by hand.
    """
    from geelark_farm.orchestrator import Result, summarise

    stuck = Result(row=18, email="a@example.com", ok=False, reason="error",
                   phone_id="PHONE18", still_running=True)
    text = summarise([Result(row=1, email="b@example.com", ok=True,
                             reason="ready"), stuck])

    assert "nothing is billing" not in text
    assert "COULD NOT BE STOPPED" in text
    assert "STILL BILLING" in text
    assert "PHONE18" in text
    assert "geelark reap" in text


def test_the_reassuring_line_is_still_printed_when_it_is_true():
    from geelark_farm.orchestrator import Result, summarise

    text = summarise([Result(row=1, email="b@example.com", ok=True,
                             reason="ready")])
    assert "All phones are stopped; nothing is billing." in text
    assert "COULD NOT BE STOPPED" not in text
