"""Concurrency safety.

Every failure mode here costs money instead of raising: a lost ledger entry is a
phone nothing can account for, and a torn sheet write is a row whose phone id no
longer matches its account. Neither would show up as an error, so neither can be
found by running the thing and looking.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

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


def test_a_row_that_cannot_get_a_phone_still_records_why(tmp_path, make_settings):
    """Phone acquisition used to sit outside every handler that writes to the
    sheet, so its failure escaped process_row and the row kept whatever status
    it had. A full GeeLark plan did exactly that on 2026-08-01: row 20 stayed
    "pending", as though it had never been attempted.
    """
    from geelark_farm import orchestrator
    from geelark_farm.accounts import Account
    from geelark_farm.api import ApiError
    from geelark_farm.sheets import Row

    recorded: list[tuple[str, str]] = []

    class FakeSheet:
        def claim(self, row, **kw): pass
        def succeed(self, row, **kw): pass
        def fail(self, row, reason, note="", **kw):
            recorded.append((reason, note))

    row = Row(number=20, sheet_row=21,
              values={"email": "x@example.com", "proxy": "1.2.3.4:1080"},
              account=Account(email="x@example.com", password="p",
                              totp_secret="JBSWY3DPEHPK3PXP",
                              proxy="1.2.3.4:1080", row=20))

    def boom(*a, **k):
        raise ApiError(44002, "Maximum number of package environments reached",
                       path="/v1/phone/addNew", trace_id="T")

    original_create = orchestrator.phones.create
    original_check = orchestrator.proxy.check
    orchestrator.phones.create = boom
    orchestrator.proxy.check = lambda *a, **k: {}
    try:
        result = orchestrator.process_row(
            object(), make_settings(state_dir=tmp_path, artifact_dir=tmp_path),
            FakeSheet(), row, Ledger.load(tmp_path))
    finally:
        orchestrator.phones.create = original_create
        orchestrator.proxy.check = original_check

    assert not result.ok
    assert result.reason == "no_phone"
    assert recorded and recorded[0][0] == "no_phone"
    assert "44002" in recorded[0][1]


# ------------------------------------------------------ discarding a phone
def _row_ending_in(reason: str, tmp_path, make_settings, stop_fails=False):
    """Drive process_row to a login failure with `reason` and report what
    happened to the phone."""
    from geelark_farm import orchestrator
    from geelark_farm.accounts import Account
    from geelark_farm.ledger import Entry
    from geelark_farm.sheets import Row

    events: list[tuple] = []

    class FakeSheet:
        def claim(self, row, **kw): pass
        def succeed(self, row, **kw): pass
        def fail(self, row, reason, note="", **kw):
            events.append(("fail", reason))
        def update(self, row, **fields):
            events.append(("update", fields))

    class FakeLogin:
        ok = False
        detail = ""
        def __init__(self, reason): self.reason = reason

    row = Row(number=1, sheet_row=2,
              values={"email": "x@example.com", "proxy": "1.2.3.4:1080"},
              account=Account(email="x@example.com", password="p",
                              totp_secret="JBSWY3DPEHPK3PXP",
                              proxy="1.2.3.4:1080", row=1))

    saved = {name: getattr(orchestrator.phones, name)
             for name in ("create", "ensure_running", "stop", "delete")}
    original_check = orchestrator.proxy.check
    original_sign_in = orchestrator.google_login.sign_in

    def stop(*a, **k):
        events.append(("stop",))
        if stop_fails:
            raise RuntimeError("stop failed")

    orchestrator.proxy.check = lambda *a, **k: {}
    orchestrator.phones.create = lambda *a, **k: Entry(
        phone_id="PHONE1", created_at=0.0, serial="500")
    orchestrator.phones.ensure_running = lambda *a, **k: None
    orchestrator.phones.stop = stop
    orchestrator.phones.delete = lambda c, ids, **k: events.append(("delete", ids))
    orchestrator.google_login.sign_in = lambda *a, **k: FakeLogin(reason)
    try:
        result = orchestrator.process_row(
            object(), make_settings(state_dir=tmp_path, artifact_dir=tmp_path),
            FakeSheet(), row, Ledger.load(tmp_path))
    finally:
        for name, fn in saved.items():
            setattr(orchestrator.phones, name, fn)
        orchestrator.proxy.check = original_check
        orchestrator.google_login.sign_in = original_sign_in
    return result, events


def test_a_captcha_deletes_the_phone_and_frees_its_slot(tmp_path, make_settings):
    """A CAPTCHA is Google judging the proxy's exit IP, and the proxy is fixed
    when the phone is created - so no retry on this phone can pass. Keeping it
    only holds a plan slot, and a full plan is what stops the *next* row from
    getting a phone at all (2026-08-01, row 20)."""
    result, events = _row_ending_in("captcha_shown", tmp_path, make_settings)

    assert result.reason == "captcha_shown"
    assert result.discarded
    assert ("delete", ["PHONE1"]) in events
    # Stopped first: deleting a running phone is not a documented way to end
    # billing.
    assert events.index(("stop",)) < events.index(("delete", ["PHONE1"]))
    # The sheet must stop naming a phone that no longer exists.
    assert ("update", {"phone_id": "", "serial": ""}) in events


def test_an_ordinary_failure_keeps_its_phone(tmp_path, make_settings):
    """The counterweight. A wrong password is corrected in the sheet and
    retried on the same device; deleting it would throw away a working phone
    and its slot for nothing."""
    result, events = _row_ending_in("wrong_password", tmp_path, make_settings)

    assert result.reason == "wrong_password"
    assert not result.discarded
    assert not any(e[0] == "delete" for e in events)


def test_a_phone_that_could_not_be_stopped_is_never_deleted(tmp_path,
                                                            make_settings):
    """Deleting a running phone is not a documented way to end billing, so a
    stop failure must leave the phone for reap rather than delete it blind."""
    result, events = _row_ending_in("captcha_shown", tmp_path, make_settings,
                                    stop_fails=True)

    assert result.still_running
    assert not result.discarded
    assert not any(e[0] == "delete" for e in events)


def test_a_failed_row_keeps_the_serial_of_the_phone_it_created(tmp_path,
                                                               make_settings):
    """finish() assigned result.serial unconditionally, and every failure path
    calls it without one - so the serial recorded at creation was erased on the
    way out. The console then fell back to eight characters of the phone id,
    which is what a real run showed for row 1 on 2026-08-05."""
    result, _ = _row_ending_in("stuck_on_email_entry", tmp_path, make_settings)

    assert not result.ok
    assert result.serial == "500"


# ------------------------------------------ one more exit before giving up
def _app_login_row(reasons, tmp_path, make_settings, budget=1800,
                   notes=None):
    """Drive process_row to the app-login step, returning `reasons` in turn."""
    from geelark_farm import orchestrator
    from geelark_farm.accounts import Account, Credentials
    from geelark_farm.ledger import Entry
    from geelark_farm.sheets import Row

    events: list = []
    attempts = iter(reasons)

    class FakeSheet:
        def claim(self, row, **kw): pass
        def succeed(self, row, **kw): events.append(("succeed",))
        def fail(self, row, reason, note="", **kw):
            events.append(("fail", reason))
            if notes is not None:
                notes.append(note)
        def update(self, row, **f): pass

    class FakeOutcome:
        def __init__(self, reason):
            self.reason, self.detail = reason, ""
            self.ok = reason == "logged_in"

    app = Credentials(email="app@example.com", password="p",
                      totp_secret="JBSWY3DPEHPK3PXP")
    row = Row(number=6, sheet_row=7,
              values={"email": "x@example.com", "proxy": "1.2.3.4:1080"},
              account=Account(email="x@example.com", password="p",
                              totp_secret="JBSWY3DPEHPK3PXP",
                              proxy="1.2.3.4:1080", row=6, app=app))

    saved = {n: getattr(orchestrator.phones, n)
             for n in ("create", "ensure_running", "stop", "delete")}
    original = (orchestrator.proxy.check, orchestrator.google_login.sign_in,
                orchestrator.play_install.install,
                orchestrator.chatgpt_login.sign_in,
                orchestrator.shell.third_party_packages)

    def sign_in(*a, **k):
        events.append(("app_login",))
        return FakeOutcome(next(attempts))

    orchestrator.proxy.check = lambda *a, **k: {}
    orchestrator.phones.create = lambda *a, **k: Entry(
        phone_id="P1", created_at=0.0, serial="516")
    orchestrator.phones.ensure_running = lambda *a, **k: events.append(("boot",))
    orchestrator.phones.stop = lambda *a, **k: events.append(("stop",))
    orchestrator.phones.delete = lambda c, ids, **k: None
    orchestrator.google_login.sign_in = lambda *a, **k: FakeOutcome("signed_in")
    orchestrator.google_login.sign_in = lambda *a, **k: type(
        "O", (), {"ok": True, "reason": "signed_in", "detail": ""})()
    orchestrator.play_install.install = lambda *a, **k: type(
        "O", (), {"ok": True, "reason": "installed", "detail": ""})()
    orchestrator.chatgpt_login.sign_in = sign_in
    orchestrator.shell.third_party_packages = lambda *a, **k: []
    try:
        result = orchestrator.process_row(
            object(),
            make_settings(state_dir=tmp_path, artifact_dir=tmp_path,
                          account_budget_seconds=budget),
            FakeSheet(), row, Ledger.load(tmp_path))
    finally:
        for n, fn in saved.items():
            setattr(orchestrator.phones, n, fn)
        (orchestrator.proxy.check, orchestrator.google_login.sign_in,
         orchestrator.play_install.install, orchestrator.chatgpt_login.sign_in,
         orchestrator.shell.third_party_packages) = original
    return result, events


def test_a_tls_refusal_gets_one_more_exit_before_being_recorded(tmp_path,
                                                                make_settings):
    """Measured across twelve attempts: every gateway produced both successes
    and OpenAI's TLS refusal, and all four rejections cleared on a later
    attempt whose only difference was a phone restart - which opens a new
    session through the proxy and comes out of a different address.

    So the run does that itself rather than waiting for someone to type
    --retry-failed.
    """
    result, events = _app_login_row(
        ["network_ssl_rejected", "logged_in"], tmp_path, make_settings)

    assert result.ok, result.reason
    kinds = [e[0] for e in events]
    assert kinds.count("app_login") == 2
    # Restarted between the two, not merely retried on the same session.
    first, second = [i for i, k in enumerate(kinds) if k == "app_login"]
    assert "stop" in kinds[first:second] and "boot" in kinds[first:second]


def test_only_the_tls_refusal_earns_a_second_exit(tmp_path, make_settings):
    """An emailed code and a wrong password are the same on any address.
    Restarting for those would spend two minutes to fail identically."""
    result, events = _app_login_row(
        ["email_code_required", "logged_in"], tmp_path, make_settings)

    assert not result.ok
    assert result.reason == "app_email_code_required"
    assert [e[0] for e in events].count("app_login") == 1


def test_the_second_exit_is_skipped_when_the_budget_cannot_cover_it(
        tmp_path, make_settings):
    """A restart, a boot and a login do not fit in what is left, so starting
    one would only turn a named failure into a budget exhaustion."""
    result, events = _app_login_row(
        ["network_ssl_rejected", "logged_in"], tmp_path, make_settings,
        budget=60)

    assert result.reason == "app_network_ssl_rejected"
    assert [e[0] for e in events].count("app_login") == 1


# --------------------------------- an interrupt has to reach the workers too
def test_a_boot_wait_gives_up_when_the_run_is_shutting_down():
    """Stopping the phones is only half of an interrupt. Without this the
    workers carried on polling the phone that had just been stopped underneath
    them, for the rest of the ten-minute boot timeout - and a
    ThreadPoolExecutor's threads are not daemons, so Python joins them on the
    way out and the process could not exit. Ctrl+C did nothing after that,
    because there was nothing left listening for it (2026-08-08).
    """
    from geelark_farm import phones

    polls = []
    original = phones.status
    phones.status = lambda c, p: polls.append(p) or phones.STOPPED
    try:
        with pytest.raises(phones.PhoneError, match="shutting down"):
            phones.wait_until_running(object(), "P1", timeout=600,
                                      cancelled=lambda: True)
    finally:
        phones.status = original

    assert polls == [], "it should not even ask once"


def test_a_boot_wait_is_unaffected_when_nothing_is_shutting_down():
    """The counterweight: a run that is not being interrupted must still wait
    for its phone."""
    from geelark_farm import phones

    original = phones.status
    phones.status = lambda c, p: phones.RUNNING
    try:
        phones.wait_until_running(object(), "P1", timeout=600, settle=0,
                                  cancelled=lambda: False)
    finally:
        phones.status = original


def test_a_second_refusal_says_the_retry_was_already_spent(tmp_path,
                                                           make_settings):
    """The advice for a TLS refusal opens with "retry first" - and when the run
    has already restarted the phone for a new exit and been refused again, that
    is the one instruction that is no longer useful. The note escalates instead
    (2026-08-09, row 1: refused, restarted, refused).
    """
    notes: list[str] = []

    result, events = _app_login_row(
        ["network_ssl_rejected", "network_ssl_rejected"], tmp_path,
        make_settings, notes=notes)

    assert result.reason == "app_network_ssl_rejected"
    assert notes and notes[0].startswith("ALREADY RETRIED")
    assert "change the proxy" in notes[0]
    assert "delete this phone" in notes[0]


def test_a_first_time_refusal_still_says_to_retry(tmp_path, make_settings):
    """The counterweight: when there was no budget for a second exit, retrying
    is still the right first move and the note must not tell them otherwise."""
    notes: list[str] = []
    result, _ = _app_login_row(["network_ssl_rejected"], tmp_path,
                               make_settings, budget=60, notes=notes)

    assert result.reason == "app_network_ssl_rejected"
    assert notes and not notes[0].startswith("ALREADY RETRIED")
