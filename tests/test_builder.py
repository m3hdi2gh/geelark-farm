"""The branching a build does when something fails.

This is where the money is. Every case below was chosen because getting it
wrong is silent and expensive: burning three Gmails against one bad exit
address, handing a signed-in account back to the pool, or putting two phones
behind one proxy. None of them raises.
"""

from __future__ import annotations

import itertools
import threading
import time
from types import SimpleNamespace

import pytest

from geelark_farm import builder, failures
from geelark_farm.flows.play_install import Outcome as InstallOutcome
from geelark_farm.flows.router import Outcome
from geelark_farm.pools import (
    AppPool,
    Book,
    GmailPool,
    HistoryLog,
    PhoneLog,
    ProxyPool,
    Resource,
)
from tests.test_pools import (
    APP_HEADERS,
    GMAIL_HEADERS,
    PHONE_HEADERS,
    PROXY_HEADERS,
    PROXY_HEADERS_OPTIONAL,
    SECRET,
    FakeWorksheet,
    gmail_row,
    proxy_row,
)


@pytest.fixture(autouse=True)
def brisk_heartbeat(monkeypatch):
    """No test here wants a real sixty-second beat.

    `_start_heartbeat` joins its thread for one interval on the way out, so a
    run that leaves the thread going costs a full minute per test - which
    turns a broken stop into a suite that hangs instead of one that fails.
    """
    monkeypatch.setattr(builder.Pool, "HEARTBEAT_SECONDS", 0.01)


SIGNED_IN = Outcome("success", "signed_in")
# The install flow returns its OWN Outcome class, not the router's. Faking
# it with the router's meant every test of the install path asserted
# against an object the real code never returns - which is how
# `installed.trail` passed here and killed ten builds live (2026-08-24).
INSTALLED = InstallOutcome("success", "installed")


def make_book(*, gmails=2, proxies=2, apps=1, proxy_headers=None) -> Book:
    proxy_headers = proxy_headers or PROXY_HEADERS
    lock = threading.Lock()
    gmail_pool = GmailPool(
        FakeWorksheet(GMAIL_HEADERS,
                      [gmail_row(f"g{i}@example.com") for i in range(gmails)]),
        GMAIL_HEADERS, lock)
    proxy_pool = ProxyPool(
        FakeWorksheet(proxy_headers,
                      [proxy_row(f"10.0.0.{i}:9999:u:p", headers=proxy_headers)
                       for i in range(proxies)]),
        proxy_headers, lock)
    app_pool = AppPool(
        FakeWorksheet(APP_HEADERS,
                      [[f"a{i}@example.com", "pw", SECRET, "", "", ""]
                       for i in range(apps)]),
        APP_HEADERS, lock)
    phone_log = PhoneLog(FakeWorksheet(PHONE_HEADERS, []), PHONE_HEADERS, lock)
    book = Book(gmails=gmail_pool, proxies=proxy_pool, apps=app_pool,
                phones=phone_log,
                history=HistoryLog(FakeWorksheet(HistoryLog.HEADERS, []), lock))
    for pool in (book.gmails, book.proxies, book.apps):
        pool.load()
    return book


class FakeLedger:
    """No phone is claimed unless a test says so."""

    def __init__(self, claims=None):
        self.claims = claims or {}

    def claim(self, *a, **k): pass
    def release(self, *a, **k): pass

    def get(self, phone_id):
        return self.claims.get(phone_id)


class FakeClaim:
    """What the ledger says about a phone a run took."""

    def __init__(self, *, is_claimed=True, is_stale=False, label="build 3"):
        self.is_claimed, self.is_stale, self.label = is_claimed, is_stale, label


class Recorder:
    """What the build did to the device, so a test can assert on it."""

    def __init__(self):
        self.created = 0
        self.proxies_set: list[str] = []
        self.stops = 0


@pytest.fixture
def device(monkeypatch):
    recorder = Recorder()

    class Entry:
        phone_id, serial = "PHONE1", "622"

    def create(*a, **k):
        recorder.created += 1
        return Entry()

    monkeypatch.setattr(builder.phones, "create", create)
    monkeypatch.setattr(builder.phones, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(builder.phones, "stop",
                        lambda *a, **k: setattr(recorder, "stops",
                                                recorder.stops + 1))
    # A discard stops the phone and waits for it to come down before asking
    # for the delete - GeeLark refuses to delete one that is still running.
    monkeypatch.setattr(builder.phones, "wait_until_stopped",
                        lambda *a, **k: True)
    monkeypatch.setattr(builder.phones, "prune_ledger", lambda *a, **k: [])
    monkeypatch.setattr(
        builder.phones, "set_proxy",
        lambda c, p, proxy: recorder.proxies_set.append(proxy.host))
    monkeypatch.setattr(builder.proxy_mod, "check",
                        lambda *a, **k: {"outboundIP": "1.1.1.1"})
    monkeypatch.setattr(builder.shell, "third_party_packages",
                        lambda *a, **k: ["com.openai.chatgpt"])
    monkeypatch.setattr(builder.play_install, "install",
                        lambda *a, **k: INSTALLED)
    monkeypatch.setattr(builder.time, "sleep", lambda *a: None)
    return recorder


@pytest.fixture
def drive(monkeypatch):
    """Run one build, with the two logins answering from a script."""

    def run(book, settings, *, google, app=None):
        google_answers = list(google)
        app_answers = list(app or [SIGNED_IN])
        monkeypatch.setattr(builder.google_login, "sign_in",
                            lambda *a, **k: google_answers.pop(0))
        monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                            lambda *a, **k: app_answers.pop(0))
        return builder.build_one(None, settings, book, FakeLedger(), 1)

    return run


@pytest.fixture
def settings(make_settings, tmp_path):
    return make_settings(state_dir=tmp_path,
                         artifact_dir=tmp_path / "artifacts")


# --------------------------------------------------------- the happy path
def test_a_build_spends_one_of_each_and_reports_ready(device, settings, drive):
    book = make_book()
    build = drive(book, settings, google=[SIGNED_IN])

    assert build.ok and build.status == "ready"
    assert build.gmail == "g0@example.com"
    assert build.app_account == "a0@example.com"
    assert device.created == 1
    assert device.stops == 1                 # billing always ends
    # One of each is spent, and nothing else is touched.
    assert [r.credentials.email for r in book.gmails.available] == \
           ["g1@example.com"]
    assert [str(r.proxy.host) for r in book.proxies.available] == ["10.0.0.1"]


# ------------------------------------------------- a credential's own fault
def test_a_bad_gmail_costs_a_gmail_not_a_phone(device, settings, drive):
    """The whole reason this module exists: the next address is tried on the
    phone that is already booted, not on a new one."""
    book = make_book()
    build = drive(book, settings,
                  google=[Outcome("fatal", "wrong_password"), SIGNED_IN])

    assert build.ok
    assert build.gmail == "g1@example.com"
    assert device.created == 1
    assert book.gmails._rows[0].values["Status"] == "wrong_password"


def test_a_captcha_note_does_not_tell_you_to_change_the_proxy(device, settings,
                                                              drive):
    """The build condemns the Gmail on a CAPTCHA and moves on, so the note must
    not carry the flow's proxy-oriented advice, which would tell the reader to
    do the opposite of what happened."""
    book = make_book()
    drive(book, settings,
          google=[Outcome("fatal", "captcha_shown",
                          "Google is challenging this exit IP; a cleaner "
                          "proxy is the fix"), SIGNED_IN])

    from geelark_farm import failures

    note = book.gmails._rows[0].values["Note"]
    assert "proxy is the fix" not in note
    # the sheet carries the taxonomy's advice, which is written for whoever
    # reads that row later rather than for whoever is debugging the flow
    assert note == failures.verdict("captcha_shown", "Google").advice
    assert failures.verdict("captcha_shown").costs_the_credential


def test_a_bad_app_account_does_not_touch_the_proxy(device, settings, drive):
    book = make_book(apps=2)
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[Outcome("fatal", "wrong_password"), SIGNED_IN])

    assert build.ok and build.app_account == "a1@example.com"
    assert device.proxies_set == []
    assert book.apps._rows[0].values["Status"] == "wrong_password"


def test_bad_app_accounts_are_worked_through_past_any_fixed_count(
        device, settings, drive):
    """Per the described flow, a rejected account costs that account and the
    next is tried on the same phone. A cap stopped this at three while eleven
    usable accounts sat in the tab (2026-08-11, phones 654 and 656)."""
    book = make_book(apps=6)
    wrong = Outcome("fatal", "wrong_password")
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[wrong, wrong, wrong, wrong, SIGNED_IN])

    assert build.ok and build.app_account == "a4@example.com"
    assert device.created == 1                    # all on the one phone
    # the four refused are marked, the untried one is still stock
    assert [r.values["Status"] for r in book.apps._rows[:4]] == ["wrong_password"] * 4
    assert [r.credentials.email for r in book.apps.available] == ["a5@example.com"]


def test_an_empty_app_pool_is_what_stops_it_not_a_count(device, settings, drive):
    book = make_book(apps=4)
    wrong = Outcome("fatal", "wrong_password")
    build = drive(book, settings, google=[SIGNED_IN], app=[wrong] * 4)

    assert not build.ok and build.status == "no_usable_gpt"
    assert "no unused account left" in build.detail
    # A build that gives up inside the app phase still reports how long it
    # took; the summary said 0s for several minutes of work (phones 668, 670).
    assert build.seconds > 0


def test_bad_gmails_are_worked_through_past_any_fixed_count(device, settings,
                                                            drive):
    """The same for the Gmail phase - the phone is already booted, so the next
    address is cheap to try."""
    book = make_book(gmails=6)
    wrong = Outcome("fatal", "wrong_password")
    build = drive(book, settings, google=[wrong, wrong, wrong, wrong, SIGNED_IN])

    assert build.ok and build.gmail == "g4@example.com"
    assert device.created == 1


def test_a_phone_level_failure_stops_instead_of_eating_the_pool(
        device, settings, drive):
    """app_would_not_start says nothing about the account - the app never got
    far enough to judge it. Feeding the pool into that wall would lose accounts
    to a broken phone, so the build stops and the account stays stock."""
    book = make_book(apps=5)
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[Outcome("unknown", "app_would_not_start")])

    assert not build.ok
    # not "app_app_would_not_start" - the reason already says app
    assert build.status == "app_would_not_start"
    # nothing was condemned; every account is still available
    assert len(book.apps.available) == 5


def test_a_stuck_router_stops_the_gmail_phase_too(device, settings, drive):
    book = make_book(gmails=5)
    build = drive(book, settings, google=[Outcome("unknown", "unknown_screen")])

    assert not build.ok and build.status == "unknown_screen"
    assert len(book.gmails.available) == 5


def test_a_captcha_costs_the_gmail_not_the_proxy(device, settings, drive):
    """A CAPTCHA looks like a network verdict and is not one: Google raises it
    on the account it is being shown. Swapping the proxy for it would waste the
    proxy and keep the address that caused it."""
    book = make_book()
    build = drive(book, settings,
                  google=[Outcome("fatal", "captcha_shown"), SIGNED_IN])

    assert build.ok
    assert build.gmail == "g1@example.com"          # the next address
    assert device.proxies_set == []                 # the proxy is untouched
    assert book.gmails._rows[0].values["Status"] == "captcha_shown"


# -------------------------------------------------- the exit address's fault
def test_a_refused_exit_waits_for_its_address_to_be_changed(device, settings,
                                                            drive):
    """This used to go straight back to the pool, on the measurement that a
    refusal is per-session rather than per-proxy - which is still true about
    the proxy. It misses the address: nothing here can ask for a new one, and
    freeing the row hands the next build the same address to be refused
    through again.

    Not `dead` and not a failure reason: the proxy is not condemned, it is
    waiting for a hand in the vendor's panel.
    """
    book = make_book()
    drive(book, settings, google=[SIGNED_IN],
          app=[Outcome("fatal", "request_rejected"), SIGNED_IN])

    first = book.proxies._rows[0]
    assert first.values["Status"] == ProxyPool.needs_new_ip
    assert first not in book.proxies.available
    note = first.values["Note"]
    assert failures.verdict("request_rejected").seen in note
    assert "the exit address is the thing that was turned down" in note
    assert "set this cell to `free`" in note


# The exit refusals are OpenAI's, so they only ever arrive in the app phase -
# Google login has no such reason. These drive them there.
def test_the_proxy_swap_stops_the_phone_first(device, settings, drive):
    """Android reads the proxy when the network comes up, and GeeLark's own
    docs refuse the call on a starting phone."""
    book = make_book()
    drive(book, settings, google=[SIGNED_IN],
          app=[Outcome("fatal", "network_ssl_rejected"), SIGNED_IN])

    assert device.stops == 2          # once for the swap, once at the end


def test_an_exit_change_does_not_cost_a_credential(device, settings, drive):
    """The same account is being given a fair hearing, not a second chance."""
    book = make_book(proxies=4)
    refused = Outcome("fatal", "network_ssl_rejected")
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[refused, refused, SIGNED_IN])

    assert build.ok and build.app_account == "a0@example.com"
    assert len(device.proxies_set) == 2


def test_network_refusals_have_no_fixed_cap(device, settings, drive):
    """The build gave up after three exit changes; the described flow says keep
    setting the next proxy and retrying. Five refusals - well past that old cap
    - still reach a sign-in, on the same account. What limits it is the pool:
    each proxy is tried once, so this needs one to create the phone and five to
    swap in."""
    book = make_book(proxies=8)
    refused = Outcome("fatal", "network_ssl_rejected")
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[refused] * 5 + [SIGNED_IN])

    assert build.ok and build.app_account == "a0@example.com"
    assert len(device.proxies_set) == 5      # five swaps, no ceiling at three


def test_working_proxies_that_all_refuse_are_reported_as_refused(
        device, settings, drive):
    """The pool was reachable throughout and the service turned every exit
    down. That is 'all_exits_refused' - and never the network reason itself,
    since the account was never judged and goes back as stock."""
    book = make_book(proxies=3)
    refused = Outcome("fatal", "network_ssl_rejected")
    build = drive(book, settings, google=[SIGNED_IN], app=[refused] * 8)

    assert not build.ok
    assert build.status == "all_exits_refused"
    assert [r.credentials.email for r in book.apps.available] == ["a0@example.com"]


def test_dead_stock_is_not_reported_as_the_service_refusing(
        device, settings, drive, monkeypatch):
    """A swap that finds only unreachable proxies is a fact about the stock,
    not a verdict from OpenAI. Reporting it as 'all_exits_refused' sent the
    reader looking at the wrong thing when a whole purchase batch expired
    mid-run (2026-08-11, phone 671)."""
    from geelark_farm.proxy import ProxyError

    def check(client, proxy):
        if proxy.host != "10.0.0.0":          # only the first answers
            raise ProxyError("no answer")
        return {"outboundIP": "1.1.1.1"}

    monkeypatch.setattr(builder.proxy_mod, "check", check)
    book = make_book(proxies=3)
    refused = Outcome("fatal", "network_ssl_rejected")
    build = drive(book, settings, google=[SIGNED_IN], app=[refused] * 8)

    assert build.status == "no_working_proxy"
    assert [r.credentials.email for r in book.apps.available] == ["a0@example.com"]


def test_dead_proxies_are_skipped_past_any_fixed_count(device, settings, drive,
                                                       monkeypatch):
    """Each dead one is marked before the next is claimed, so the pool bounds
    the search and a cap only costs working phones: a build hit five dead
    proxies from an expired batch and gave up while live ones sat in the tab."""
    from geelark_farm.proxy import ProxyError

    def check(client, proxy):
        if proxy.host == "10.0.0.7":          # only the last one answers
            return {"outboundIP": "1.1.1.1"}
        raise ProxyError("no answer")

    monkeypatch.setattr(builder.proxy_mod, "check", check)
    book = make_book(proxies=8)
    build = drive(book, settings, google=[SIGNED_IN])

    assert build.ok                            # it reached the live one
    assert build.proxy.endswith("10.0.0.7:9999")
    assert sum(1 for r in book.proxies._rows
               if r.values["Status"] == "dead") == 7


def test_a_refused_exit_is_not_handed_back_to_the_same_build(device, settings,
                                                             drive):
    """The bug that cost 49 minutes: a swapped-away proxy went straight back to
    the pool as `unused`, so the next swap could claim it again and the phone
    went round the pool instead of through it (2026-08-11, phone 658). Each
    proxy must be tried at most once per build, and the pool is what ends it."""
    book = make_book(proxies=4)
    refused = Outcome("fatal", "network_ssl_rejected")
    build = drive(book, settings, google=[SIGNED_IN], app=[refused] * 10)

    assert build.status == "all_exits_refused"
    # one proxy created the phone, the other three were swapped in - each once
    assert device.proxies_set == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
    assert len(device.proxies_set) == len(set(device.proxies_set))
    # and none of them is offered again until its address has been changed
    assert book.proxies.available == []
    assert all(book.proxies.status_of(r) == ProxyPool.needs_new_ip
               for r in book.proxies._rows[:3])
    assert (failures.verdict("network_ssl_rejected").seen
            in book.proxies._rows[0].values["Note"])


# ------------------------------------------ keeping the Proxy tab current
def test_a_proxy_that_died_since_the_last_run_is_marked_before_anything_starts(
        settings, monkeypatch):
    """A whole purchase batch expired overnight, so a run began against a pool
    a third of which no longer answered - and the count the operator had just
    been shown was fiction (2026-08-11)."""
    from geelark_farm.proxy import ProxyError

    book = make_book(proxies=3)

    def check(client, proxy):
        if proxy.host == "10.0.0.1":
            raise ProxyError("no answer")
        return {"outboundIP": "8.8.8.8"}

    monkeypatch.setattr(builder.proxy_mod, "check", check)
    dead, revived = builder.check_proxies(None, book)

    assert [r.proxy.host for r in dead] == ["10.0.0.1"]
    assert [r.proxy.host for r in book.proxies.available] == ["10.0.0.0",
                                                              "10.0.0.2"]
    # the survivors get their exit recorded while we are asking anyway
    assert book.proxies._rows[0].values["Last Exit IP"] == "8.8.8.8"


def test_checking_leaves_proxies_that_are_already_on_a_phone_alone(
        settings, monkeypatch):
    """Not a candidate for this run, so a call would learn something that
    changes nothing."""
    book = make_book(proxies=2)
    book.proxies.spend(book.proxies.claim(), serial="650")   # now `ok`
    asked = []
    monkeypatch.setattr(builder.proxy_mod, "check",
                        lambda c, p: asked.append(p.host) or {"outboundIP": "1.1.1.1"})

    builder.check_proxies(None, book)

    assert asked == ["10.0.0.1"]


# --------------------------------------------------- what the stock allows
@pytest.mark.parametrize("waiting,proxies,gmails,apps,total,finishing,limit", [
    # the reported case: adding waiting to buildable promised one phone too many
    (1, 13, 13, 2, 2, 1, "app accounts"),
    # accounts to spare, so the thing to top up is not accounts
    (0, 2, 9, 10, 2, 0, "proxies"),
    (0, 9, 2, 10, 2, 0, "gmails"),
    # nothing to build with, but a waiting phone needs only an account
    (3, 0, 0, 4, 3, 3, "proxies"),
    # an empty app tab means no ready phone is obtainable at all
    (3, 5, 5, 0, 0, 0, "app accounts"),
])
def test_capacity_counts_each_app_account_once(waiting, proxies, gmails, apps,
                                               total, finishing, limit):
    """A phone waiting to be finished and a phone built from nothing both
    consume exactly one app account, so they cannot be added up independently -
    the app pool caps the run as a whole."""
    can = builder.Capacity(waiting=waiting, proxies=proxies, gmails=gmails,
                           app_accounts=apps)

    assert can.total == total
    assert can.finishing == finishing          # finishing is the cheaper half
    assert can.building == total - finishing
    assert can.limited_by == limit


# ------------------------------------------- finishing before building anew
def test_a_run_finishes_waiting_phones_before_it_builds_new_ones(
        device, settings, monkeypatch):
    """`count` is how many phones to end up with, not how many to create. A
    phone that already has its Gmail and the app costs one app account; a new
    one costs a phone, a Gmail and a proxy to reach the same place - so four
    sat one step short while a later run built five more beside them."""
    book = make_book()
    waiting = [{"sheet_row": 2, "phone_id": "P1", "serial": "668",
                "gmail": "a@example.com", "proxy": "", "status": "no_usable_gpt"},
               {"sheet_row": 3, "phone_id": "P2", "serial": "670",
                "gmail": "b@example.com", "proxy": "", "status": "no_usable_gpt"}]
    monkeypatch.setattr(builder, "_unfinished", lambda c, b: (waiting, []))
    monkeypatch.setattr(builder, "sync_sheet", lambda *a, **k: {})
    monkeypatch.setattr(builder.Book, "open", classmethod(lambda cls, s: book))
    monkeypatch.setattr(builder.Ledger, "load", staticmethod(lambda p: FakeLedger()))

    jobs = []
    monkeypatch.setattr(builder, "finish_one",
                        lambda *a, **k: jobs.append(("finish", a[4]["serial"]))
                        or builder.Build(index=a[5], ok=True, status="ready"))
    monkeypatch.setattr(builder, "build_one",
                        lambda *a, **k: jobs.append(("build", None))
                        or builder.Build(index=a[4], ok=True, status="ready"))

    builder.run(None, settings, count=3, workers=1)

    assert jobs == [("finish", "668"), ("finish", "670"), ("build", None)]


def _job_world(monkeypatch, waiting):
    """Enough of a world for `run` to dispatch jobs and nothing more."""
    book = make_book()
    monkeypatch.setattr(builder, "_unfinished", lambda c, b: (waiting, []))
    monkeypatch.setattr(builder, "sync_sheet", lambda *a, **k: {})
    monkeypatch.setattr(builder.Book, "open", classmethod(lambda cls, s: book))
    monkeypatch.setattr(builder.Ledger, "load",
                        staticmethod(lambda p: FakeLedger()))
    jobs = []
    monkeypatch.setattr(builder, "finish_one",
                        lambda *a, **k: jobs.append("finish")
                        or builder.Build(index=a[5], ok=True, status="ready"))
    monkeypatch.setattr(builder, "build_one",
                        lambda *a, **k: jobs.append("build")
                        or builder.Build(index=a[4], ok=True, status="ready"))
    return jobs


def test_finish_limit_says_how_many_of_the_jobs_are_finishes(device, settings,
                                                             monkeypatch):
    """`count` is a total that finishing eats first, so a caller who knows only
    two accounts are waiting still gets one finish per waiting phone - and each
    surplus one boots a real phone, finds no account, ends `no_usable_gpt` and
    puts it back, while that very reason clears the breaker (2026-08-28)."""
    waiting = [{"sheet_row": r, "phone_id": f"P{r}", "serial": str(660 + r),
                "gmail": "a@example.com", "proxy": "", "status": "incomplete"}
               for r in (2, 3, 4, 5)]
    jobs = _job_world(monkeypatch, waiting)

    builder.run(None, settings, count=4, finish_limit=2, workers=1)

    assert jobs == ["finish", "finish", "build", "build"]


def test_a_mixed_batch_runs_its_jobs_at_once(device, settings, monkeypatch):
    """The thread pool has run twenty phones ten at a time in production and
    has never had a test. It is what every parallel pass now goes through."""
    waiting = [{"sheet_row": r, "phone_id": f"P{r}", "serial": str(660 + r),
                "gmail": "a@example.com", "proxy": "", "status": "incomplete"}
               for r in (2, 3)]
    jobs = _job_world(monkeypatch, waiting)

    builds = builder.run(None, settings, count=5, finish_limit=2, workers=5)

    # Order is whatever the pool decides, so count rather than sequence.
    assert sorted(jobs) == ["build"] * 3 + ["finish"] * 2
    assert len(builds) == 5
    assert [b.index for b in builds] == sorted(b.index for b in builds), (
        "results come back in job order however they finished")


def test_an_interrupted_build_does_not_let_the_service_carry_on(
        device, settings, monkeypatch):
    """Swallowing it made `docker stop` mean nothing while a build ran.

    SIGTERM arrives as a KeyboardInterrupt, this caught it, the phones were
    stopped - and then `run` returned normally and the serve loop carried on.
    Docker waited out its 120s grace period and SIGKILLed, and in those two
    minutes the loop could start four more passes and create phones that the
    one signal nothing can catch then killed (2026-08-28).
    """
    _job_world(monkeypatch, [])

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(builder, "build_one", interrupted)

    with pytest.raises(KeyboardInterrupt):
        builder.run(None, settings, count=1, workers=1)


def test_an_interrupted_batch_does_not_let_the_service_carry_on_either(
        device, settings, monkeypatch):
    """The pool path, which every parallel pass now goes through."""
    _job_world(monkeypatch, [])

    def interrupted(_futures):
        raise KeyboardInterrupt

    monkeypatch.setattr(builder, "wait", interrupted)

    with pytest.raises(KeyboardInterrupt):
        builder.run(None, settings, count=3, workers=3)


def test_asking_for_fewer_phones_than_are_waiting_builds_nothing_new(
        device, settings, monkeypatch):
    book = make_book()
    waiting = [{"sheet_row": r, "phone_id": f"P{r}", "serial": str(660 + r),
                "gmail": "a@example.com", "proxy": "", "status": "no_usable_gpt"}
               for r in (2, 3, 4)]
    monkeypatch.setattr(builder, "_unfinished", lambda c, b: (waiting, []))
    monkeypatch.setattr(builder, "sync_sheet", lambda *a, **k: {})
    monkeypatch.setattr(builder.Book, "open", classmethod(lambda cls, s: book))
    monkeypatch.setattr(builder.Ledger, "load", staticmethod(lambda p: FakeLedger()))

    jobs = []
    monkeypatch.setattr(builder, "finish_one",
                        lambda *a, **k: jobs.append("finish")
                        or builder.Build(index=a[5], ok=True, status="ready"))
    monkeypatch.setattr(builder, "build_one",
                        lambda *a, **k: jobs.append("build")
                        or builder.Build(index=a[4], ok=True, status="ready"))

    builder.run(None, settings, count=2, workers=1)

    assert jobs == ["finish", "finish"]


# ------------------------------------------------- what must never go back
def test_a_signed_in_gmail_is_kept_even_when_the_build_fails(device, settings, drive):
    """It is on that phone whatever happens next. Releasing it would sign one
    address into a second phone on the next run - the one mistake here that
    costs an account rather than a minute."""
    book = make_book(apps=1)
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[Outcome("fatal", "wrong_password")])

    assert not build.ok and build.status == "no_usable_gpt"
    assert book.gmails._rows[0].values["Status"] == "ready"
    assert "g0@example.com" not in [r.credentials.email
                                    for r in book.gmails.available]


def test_a_proxy_is_never_freed_once_a_phone_exists_behind_it(device, settings, drive):
    """Handing it to the next build would put two devices on one exit."""
    book = make_book(gmails=1)
    build = drive(book, settings,
                  google=[Outcome("fatal", "wrong_password")])

    assert not build.ok
    assert book.proxies._rows[0].values["Status"] == "on a phone"
    assert book.proxies._rows[0].values["Used By"] == "622"


def test_an_untried_app_account_goes_back_as_stock(device, settings, drive):
    """Claimed just as the budget ran out, never put on a device."""
    book = make_book()
    settings = settings.__class__(**{**settings.__dict__,
                                     "build_budget_seconds": 1})
    build = drive(book, settings, google=[SIGNED_IN])

    assert not build.ok
    assert len(book.apps.available) == 1


# ------------------------------------------------------ running out of stock
def test_an_empty_gmail_tab_costs_no_phone_at_all(device, settings, drive):
    """The phone used to come first and the tab be asked afterwards, so a run
    that had run out of addresses still paid for a device - and two of them sat
    in the tab as `incomplete` with an empty Gmail column (2026-08-14)."""
    book = make_book(gmails=0)
    build = drive(book, settings, google=[])

    assert build.status == "no_usable_gmail"
    assert device.created == 0                 # nothing was made to stop
    assert device.stops == 0
    assert book.phones._ws.rows == []          # and nothing was recorded


def test_no_proxy_means_no_phone_is_created(device, settings, drive):
    book = make_book(proxies=0)
    build = drive(book, settings, google=[])

    assert build.status == "no_usable_proxy"
    assert device.created == 0


def test_a_dead_proxy_is_skipped_and_marked(device, settings, drive, monkeypatch):
    """An unreachable proxy is the one failure that really is the proxy's."""
    from geelark_farm.proxy import ProxyError

    calls = []

    def check(client, proxy):
        calls.append(proxy.host)
        if proxy.host == "10.0.0.0":
            raise ProxyError("no answer")
        return {"outboundIP": "1.1.1.1"}

    monkeypatch.setattr(builder.proxy_mod, "check", check)
    book = make_book()
    build = drive(book, settings, google=[SIGNED_IN])

    assert build.ok
    assert book.proxies._rows[0].values["Status"] == "dead"
    assert calls == ["10.0.0.0", "10.0.0.1"]


# ----------------------------------------------------------- the Phones tab
def test_every_phone_is_recorded_whether_it_worked_or_not(device, settings, drive):
    """One Gmail, and it is refused - so the phone exists, is signed into
    nothing, and there is no second address to try."""
    book = make_book(gmails=1)
    drive(book, settings, google=[Outcome("fatal", "wrong_password")])

    written = book.phones._ws.rows[0]
    assert written[PHONE_HEADERS.index("Serial")] == "622"
    # the tab answers "can I use this phone", in one of three words
    assert written[PHONE_HEADERS.index("Status")] == "incomplete"
    # and the note says why in words. The token is in the Status column's
    # vocabulary and in the terminal summary; this cell is prose.
    note = written[PHONE_HEADERS.index("Note")]
    assert note.startswith("Stopped short: the Gmails tab had no other address")


# ------------------------------------- acting on what the operator marked
class FakePhoneLog:
    """A Phones tab that answers `marked` and records what was deleted."""

    DONE, FAILED, UNUSED = "done", "failed", "unused"
    BUILDING, READY, INCOMPLETE = "building", "ready", "incomplete"

    def __init__(self, rows):
        self._rows = rows
        self.deleted_rows = []

    def marked(self):
        return [r for r in self._rows if r["state"] in (self.DONE, self.FAILED)]

    def delete_rows(self, numbers):
        self.deleted_rows.extend(numbers)


def state_book(rows, *, apps=2):
    book = make_book(apps=apps)
    book.phones = FakePhoneLog(rows)
    return book


def test_a_phone_marked_done_is_deleted_with_its_row(monkeypatch):
    """`State` is the instruction back to the tool: finished with it."""
    deleted = []
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2}])
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))
    book = state_book([{"sheet_row": 5, "state": "done",
                        "serial": "650", "gmail": "g@example.com",
                        "app_account": "a0@example.com"}])

    out = builder.apply_phone_states(None, book, FakeLedger())

    assert deleted == ["P1"] and out["deleted"] == ["650"]
    assert book.phones.deleted_rows == [5]
    # done means the phone was the product and went out with the account on it
    assert out["freed"] == [] and out["delivered"] == ["a0@example.com"]
    assert book.apps._rows[0].values["Status"] == "delivered"
    assert book.apps._rows[0].values["Phone Serial"] == ""   # 650 is gone


def test_a_phone_marked_failed_gives_its_app_account_back(monkeypatch):
    """The account never got a fair phone, so it returns to the pool for the
    next build - which is the whole point of marking one failed."""
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2}])
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    book = state_book([{"sheet_row": 7, "state": "failed",
                        "serial": "651", "gmail": "g@example.com",
                        "app_account": "a0@example.com"}], apps=1)
    book.apps.spend(book.apps.claim(), serial="651")      # as a build left it
    assert book.apps.available == []

    out = builder.apply_phone_states(None, book, FakeLedger())

    assert out["freed"] == ["a0@example.com"]
    assert [r.credentials.email for r in book.apps.available] == ["a0@example.com"]


def test_a_running_phone_is_reported_rather_than_deleted(monkeypatch):
    """Deleting a running phone is not a documented way to end its billing,
    and stopping it to make deletion safe is not this function's business."""
    deleted = []
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 0}])
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))
    book = state_book([{"sheet_row": 5, "state": "done",
                        "serial": "650", "gmail": "", "app_account": ""}])

    out = builder.apply_phone_states(None, book, FakeLedger())

    assert deleted == [] and out["running"] == ["650"]
    assert book.phones.deleted_rows == []      # the row survives to be retried


def test_an_unused_phone_is_left_entirely_alone(monkeypatch):
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2}])
    book = state_book([{"sheet_row": 5, "state": "unused",
                        "serial": "650", "gmail": "", "app_account": ""}])

    assert builder.apply_phone_states(None, book, FakeLedger()) == {}
    assert book.phones.deleted_rows == []


def test_a_row_whose_phone_is_already_gone_is_still_tidied(monkeypatch):
    """Deleted from the panel by hand. Nothing to delete, but the row and the
    account it names should not linger."""
    monkeypatch.setattr(builder.phones, "listing", lambda c: [])
    book = state_book([{"sheet_row": 9, "state": "failed", "phone_id": "GONE",
                        "serial": "660", "gmail": "", "app_account": "a0@example.com"}])
    book.apps.spend(book.apps.claim(), serial="660")

    out = builder.apply_phone_states(None, book, FakeLedger())

    assert out["deleted"] == [] and out["freed"] == ["a0@example.com"]
    assert book.phones.deleted_rows == [9]


def test_the_gmail_is_retired_whichever_way_the_phone_ended(monkeypatch):
    """It signed into that phone without complaint, and that is the credit it
    had to spend - so `done` and `failed` retire it alike, and neither hands it
    back to be signed into a second device."""
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2},
                                   {"id": "P2", "serialNo": "651", "status": 2}])
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    book = state_book([
        {"sheet_row": 4, "state": "done", "serial": "650",
         "gmail": "g0@example.com", "app_account": "a0@example.com"},
        {"sheet_row": 5, "state": "failed", "serial": "651",
         "gmail": "g1@example.com", "app_account": "a1@example.com"}])

    out = builder.apply_phone_states(None, book, FakeLedger())

    assert sorted(out["retired"]) == ["g0@example.com", "g1@example.com"]
    assert [r.values["Status"] for r in book.gmails._rows] == ["used", "used"]
    assert book.gmails.available == []          # never handed out again
    # the app accounts diverge, though: one was delivered, one never got a
    # fair phone and goes back
    assert out["delivered"] == ["a0@example.com"]
    assert out["freed"] == ["a1@example.com"]
    assert [r.credentials.email for r in book.apps.available] == ["a1@example.com"]


def test_a_retired_credential_keeps_no_serial_for_a_deleted_phone(monkeypatch):
    """A stale serial points the reader at nothing. That is how thirteen
    proxies sat out of the pool for days."""
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2}])
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    book = state_book([{"sheet_row": 4, "state": "done",
                        "serial": "650", "gmail": "g0@example.com",
                        "app_account": "a0@example.com"}])
    book.gmails.spend(book.gmails.claim(), serial="650")
    assert book.gmails._rows[0].values["Phone Serial"] == "650"

    builder.apply_phone_states(None, book, FakeLedger())

    assert book.gmails._rows[0].values["Phone Serial"] == ""
    assert book.gmails._rows[0].values["Used Date"]      # the date survives


# ---------------------------------------- one account's page is not the next's
def test_each_app_attempt_after_the_first_starts_from_a_cleared_app(
        device, settings, monkeypatch):
    """`launch` resumes the task the app already had, so the page the previous
    attempt stopped on is still there. Eight archived screens all named the
    first address while seven further accounts were condemned by it
    (2026-08-13)."""
    fresh_flags = []
    answers = [Outcome("fatal", "wrong_password"),
               Outcome("fatal", "wrong_password"), SIGNED_IN]
    monkeypatch.setattr(builder.google_login, "sign_in", lambda *a, **k: SIGNED_IN)
    monkeypatch.setattr(
        builder.chatgpt_login, "sign_in",
        lambda *a, **k: fresh_flags.append(k.get("fresh")) or answers.pop(0))

    book = make_book(apps=3)
    builder.build_one(None, settings, book, FakeLedger(), 1)

    # the first runs on a freshly installed app; every one after it clears
    assert fresh_flags == [False, True, True]


def test_a_challenge_sets_the_account_aside_instead_of_condemning_it(
        device, settings, drive):
    """OpenAI emailing a code says nothing about the account - three addresses
    it retired had already signed in fine on earlier phones. The status is the
    *reason*, because that is the word every other surface uses for the event;
    the first design wrote `challenged` and the operator had to ask what it
    meant (2026-08-17). What says "not condemned" is the Note, which reads
    asked-not-judged, and the blame in failures.py."""
    book = make_book(apps=2)
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[Outcome("fatal", "email_code_required"), SIGNED_IN])

    assert build.ok and build.app_account == "a1@example.com"
    challenged = book.apps._rows[0]
    assert challenged.values["Status"] == "email_code_required"
    assert "asked, not judged" in challenged.values["Note"]
    assert (failures.verdict("email_code_required").seen
            in challenged.values["Note"])


def test_a_challenged_account_is_not_handed_out_twice_in_one_build(
        device, settings, drive):
    """Held rather than released on the spot: released, `claim` would return
    the same first-available row and the build would loop on it."""
    book = make_book(apps=2)
    challenge = Outcome("fatal", "email_code_required")
    build = drive(book, settings, google=[SIGNED_IN], app=[challenge, challenge])

    assert not build.ok and build.status == "no_usable_gpt"
    assert len(build.tried) == 2
    # and afterwards neither is offered again, which is the difference between
    # this and the run before it: they went back blank, so the next run took
    # the same two and met the same challenge - three runs running, five
    # minutes each (2026-08-13)
    assert book.apps.available == []
    assert ([r.values["Status"] for r in book.apps._rows]
            == ["email_code_required"] * 2)


def test_a_finish_says_on_the_row_that_the_phone_is_in_hand(device, settings,
                                                            monkeypatch):
    """Otherwise the tab cannot tell "being worked on" from "sitting warm".

    A finish left the row reading `incomplete` for its whole length - which is
    exactly what it read while the phone sat untouched. The account's row says
    `in_use` in the same minute, and an operator reading only the sheet is
    meant to be able to put the two together (2026-08-28).
    """
    book = make_book(apps=1)
    book.phones.start(Serial="691", Status="incomplete")

    seen = []
    monkeypatch.setattr(
        builder.phones, "ensure_running",
        lambda *a, **k: seen.append(
            book.phones._ws.rows[0][PHONE_HEADERS.index("Status")]))
    # Ends at the first check after the marker, which is all this is about.
    monkeypatch.setattr(builder.shell, "device_accounts", lambda *a, **k: [])

    builder.finish_one(
        None, settings, book, FakeLedger(),
        {"sheet_row": 2, "phone_id": "P1", "serial": "691",
         "gmail": "g@example.com", "proxy": "", "status": "incomplete"}, 1)

    assert seen == [book.phones.BUILDING], (
        "the row must say the phone is in hand while it is")


def test_finishing_gives_back_the_accounts_it_set_aside(device, settings,
                                                        monkeypatch):
    """`finish` assembled its own list of what a session was holding, and when
    set_aside was added only `build` learned about it - so two challenged
    accounts sat `in_use` with nothing left to free them (2026-08-13, rows 12
    and 13 of the Gpt Info tab)."""
    book = make_book(apps=2)
    monkeypatch.setattr(builder.phones, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(builder.shell, "device_accounts",
                        lambda *a, **k: ["g@example.com"])
    monkeypatch.setattr(builder.shell, "third_party_packages",
                        lambda *a, **k: ["com.openai.chatgpt"])
    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda *a, **k: Outcome("fatal", "email_code_required"))

    build = builder.finish_one(
        None, settings, book, FakeLedger(),
        {"sheet_row": 3, "phone_id": "P1", "serial": "691",
         "gmail": "g@example.com", "proxy": "", "status": "incomplete"}, 1)

    assert not build.ok and build.status == "no_usable_gpt"
    # both carry what they were asked for, and neither is left claimed, which
    # was the bug this test was written for
    assert ([r.values["Status"] for r in book.apps._rows]
            == ["email_code_required"] * 2)
    assert book.apps.stuck == []


# --------------------------------------------------------- how a note reads
def notes_written(book) -> list[tuple[str, str]]:
    """Every Note cell a run left behind, with the tab it is in."""
    found = [(pool.tab, row.values.get("Note", ""))
             for pool in (book.gmails, book.proxies, book.apps)
             for row in pool._rows]
    found += [("Phones", line[PHONE_HEADERS.index("Note")])
              for line in book.phones._ws.rows]
    return [(tab, note) for tab, note in found if note]


def test_no_note_makes_the_reader_learn_a_reason_token(device, settings, drive):
    """The Note columns are prose, and this is the test that keeps them prose.

    They were not: `no_usable_gpt. tried: a@b.com: email_code_required` in the
    Phones tab, `phone 685: ready` beside a credential, and for a phone that
    worked, the raw output of `pm list packages`. The tokens are exact and
    still belong in the Status column, the terminal summary and the logs -
    which is where you grep them. The cell a person reads gets sentences.
    """
    book = make_book(gmails=2, apps=2)
    drive(book, settings,
          google=[Outcome("fatal", "captcha_shown"), SIGNED_IN],
          app=[Outcome("fatal", "request_rejected"),
               Outcome("fatal", "email_code_required"), SIGNED_IN])

    written = notes_written(book)
    assert len(written) >= 4, written
    for tab, note in written:
        assert "_" not in note, f"{tab} note names a reason token: {note!r}"
        assert note[0].isupper(), f"{tab} note does not open a sentence: {note!r}"
        assert note.rstrip().endswith("."), f"{tab} note has no full stop: {note!r}"


def test_the_phone_note_says_what_happened_rather_than_listing_packages(
        device, settings, drive):
    """A ready phone used to be described by `pm list packages`, which answers
    a question nobody reading that tab was asking."""
    book = make_book(gmails=2)
    drive(book, settings,
          google=[Outcome("fatal", "captcha_shown"), SIGNED_IN])

    note = book.phones._ws.rows[0][PHONE_HEADERS.index("Note")]
    assert note == ("Ready - signed into Google, and into ChatGPT in the app. "
                    "Also tried: g0@example.com (Google showed a CAPTCHA).")


# ------------------------------------- what the Phones tab is keyed and read by
def test_the_tab_records_the_proxys_name_rather_than_its_address(
        device, settings, drive):
    """`socks5://ul01kyxck1batp2n6q5fmzf7kzs0:***@212.8.252.6:10527` answers
    no question a person reading that row is asking. `SX14` is the string the
    vendor's panel is searched with, and the address is one column away in the
    Proxy tab."""
    book = make_book(proxy_headers=PROXY_HEADERS_OPTIONAL)
    book.proxies._rows[0].values["Name"] = "SX4"
    build = drive(book, settings, google=[SIGNED_IN])

    assert build.ok
    assert build.proxy_name == "SX4"
    assert build.proxy.startswith("socks5://")     # still what logs in
    written = book.phones._ws.rows[0]
    assert written[PHONE_HEADERS.index("Proxy")] == "SX4"


def test_a_tab_with_no_names_still_records_the_address():
    """The Name column is what turns this on. Without it there is nothing to
    write but the address, and that is better than an empty cell."""
    book = make_book()
    assert book.proxies._rows[0].name == ""


def test_a_phone_is_found_by_its_serial_now_that_the_id_is_not_stored(
        monkeypatch):
    """The id was twenty digits nobody reads, in a column beside the serial
    that everything else - the panel, the notes, the operator - calls the
    phone by. It is resolved from the listing at the one moment anything
    needs one."""
    book = make_book()
    book.phones = FakePhoneLog([])
    book.phones.unfinished = lambda: [
        {"sheet_row": 3, "serial": "691", "gmail": "g@example.com",
         "proxy": "SX14", "status": "no accounts left"},
        {"sheet_row": 4, "serial": "999", "gmail": "h@example.com",
         "proxy": "SX1", "status": "no accounts left"}]
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "PHONE-691", "serialNo": "691"}])

    waiting, gone = builder._unfinished(None, book)

    assert [p["serial"] for p in waiting] == ["691"]
    assert waiting[0]["phone_id"] == "PHONE-691"
    # 999 is in the tab and not on the account, so it is skipped rather than
    # driven against an id that does not exist
    assert [p["serial"] for p in gone] == ["999"]


# ------------------------------- a phone is not made without something to sign in
def test_a_phone_with_nothing_signed_into_it_is_deleted(device, settings,
                                                        monkeypatch, drive):
    """One address, refused, and no second to try - so the phone exists with no
    Google account on it. `finish` refuses such a phone by name, so leaving it
    costs a plan slot and puts a row in the tab that reads `incomplete` with an
    empty Gmail column. Two of those prompted this (2026-08-14)."""
    deleted = []
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))
    book = make_book(gmails=1)

    build = drive(book, settings,
                  google=[Outcome("fatal", "wrong_password")])

    assert not build.ok
    assert deleted == ["PHONE1"]
    # Stopped first. This asserted the opposite - "deleting ends it; stopping
    # is moot" - which is the assumption that let two running phones be
    # reported as discarded while GeeLark refused every delete (2026-08-17).
    assert device.stops == 1
    assert book.phones._ws.rows == []         # and no row is left behind
    # the exit it was created on goes back too
    assert len(book.proxies.available) == 2


def test_a_phone_that_got_a_gmail_is_kept_even_when_the_build_fails(
        device, settings, monkeypatch, drive):
    """The rule is about what is *on* the phone, not whether the build won.
    A phone signed into Google with the app installed is most of the work, and
    `finish` picks it up."""
    deleted = []
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))
    book = make_book(apps=0)

    build = drive(book, settings, google=[SIGNED_IN])

    assert not build.ok and build.status == "no_usable_gpt"
    assert deleted == []
    assert book.phones._ws.rows[0][PHONE_HEADERS.index("Gmail")] == \
           "g0@example.com"


def test_a_phone_that_cannot_be_deleted_is_recorded_the_ordinary_way(
        device, settings, monkeypatch, drive):
    """Half-deleting it - row dropped, device still there - is the one outcome
    worse than keeping it."""
    def refuse(*a, **k):
        raise RuntimeError("GeeLark said no")
    monkeypatch.setattr(builder.phones, "delete", refuse)
    book = make_book(gmails=1)

    drive(book, settings, google=[Outcome("fatal", "wrong_password")])

    assert len(book.phones._ws.rows) == 1
    # Twice: the discard stops it before asking for the delete, and the
    # ordinary path stops it again once that was refused. `stop` is non-strict
    # for exactly this - stopping a stopped phone is a success.
    assert device.stops == 2


# ------------------------------------------- the order they come out in
def test_addresses_and_serials_come_out_in_the_same_order(settings,
                                                          monkeypatch):
    """GeeLark numbers a phone when it is created, so whoever creates first
    gets the lower serial. With the claim and the create apart, two workers
    interleaved and the second address landed on the first phone.

    The interleave is forced rather than raced for: whoever claims first is
    made to spend the longest inside `create`, so with the two steps apart the
    creates finish in the opposite order to the claims and the pairing is
    inverted every time. Under one lock the delay cannot reorder anything,
    because the next thread has not claimed yet.
    """
    book = make_book(gmails=4, proxies=4, apps=4)
    made: list[tuple[str, str]] = []
    claimed = itertools.count()
    serials = itertools.count(700)
    order = threading.local()

    real_claim = book.gmails.claim

    def claim():
        row = real_claim()
        order.position = next(claimed)
        return row

    def create(*a, **k):
        # The delay comes first: GeeLark assigns the number when the phone is
        # made, so a delay after it would reorder nothing and the test would
        # pass with the lock removed - which is exactly what it did.
        time.sleep(0.05 * (4 - getattr(order, "position", 0)))

        class Entry:
            phone_id = f"P{next(serials)}"
            serial = str(next(serials))
        return Entry()

    monkeypatch.setattr(book.gmails, "claim", claim)
    monkeypatch.setattr(builder.phones, "create", create)
    monkeypatch.setattr(builder.phones, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(builder.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(builder.proxy_mod, "check",
                        lambda *a, **k: {"outboundIP": "1.1.1.1"})
    monkeypatch.setattr(builder.shell, "third_party_packages",
                        lambda *a, **k: ["com.openai.chatgpt"])
    monkeypatch.setattr(builder.play_install, "install",
                        lambda *a, **k: INSTALLED)
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)
    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)

    def one(index):
        build = builder.build_one(None, settings, book, FakeLedger(), index)
        made.append((int(build.serial), build.gmail))

    threads = [threading.Thread(target=one, args=(i,)) for i in range(1, 5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    made.sort()
    assert len(made) == 4
    assert [address for _, address in made] == sorted(a for _, a in made), (
        f"phone {made} - the serials do not run in the same order as the "
        f"addresses")


# ------------------------------------------------- a renewed proxy comes back
def test_a_dead_proxy_that_answers_again_goes_back_in_the_pool(monkeypatch):
    """These are rented and renewed on the same address, so one that stopped
    answering yesterday is often answering today. Nothing ever looked, so a
    renewed proxy stayed out of the pool until someone blanked the cell."""
    book = make_book(proxies=2)
    buried = book.proxies._rows[0]
    book.proxies.fail(buried, "dead", note="Did not answer.")
    assert len(book.proxies.available) == 1

    monkeypatch.setattr(builder.proxy_mod, "check",
                        lambda c, p: {"outboundIP": "8.8.8.8"})
    dead, revived = builder.check_proxies(None, book)

    assert dead == []
    assert [r.proxy.host for r in revived] == ["10.0.0.0"]
    assert len(book.proxies.available) == 2
    assert buried.values["Last Exit IP"] == "8.8.8.8"
    assert "Answering again" in buried.values["Note"]


def test_a_dead_proxy_that_still_does_not_answer_is_left_as_it_was(monkeypatch):
    """Re-checking must not rewrite the row every run with the same news."""
    from geelark_farm.proxy import ProxyError

    book = make_book(proxies=1)
    buried = book.proxies._rows[0]
    book.proxies.fail(buried, "dead", note="Did not answer: the first reason.")

    monkeypatch.setattr(builder.proxy_mod, "check",
                        lambda c, p: (_ for _ in ()).throw(ProxyError("no")))
    dead, revived = builder.check_proxies(None, book)

    assert dead == [] and revived == []
    assert buried.values["Note"] == "Did not answer: the first reason."


# --------------------------------------------- the call every session starts with
@pytest.fixture
def world(monkeypatch):
    """A panel with two phones on it, and a record of what was done to it."""
    live = [{"id": "P729", "serialNo": "729", "status": 2,
             "proxy": {"type": "socks5", "server": "10.0.0.0", "port": 9999,
                       "username": "u", "password": "p"}},
            {"id": "P730", "serialNo": "730", "status": 2,
             "proxy": {"type": "socks5", "server": "10.0.0.1", "port": 9999,
                       "username": "u", "password": "p"}}]
    done = {"deleted": []}

    def delete(client, ids, ledger=None):
        done["deleted"].extend(ids)
        live[:] = [p for p in live if p["id"] not in ids]

    class FakeClient:
        """Only what the sync asks of it: the saved-proxy listing."""

        def data(self, path, payload=None):
            assert path == "/v1/proxy/list", path
            return {"list": [{"server": "10.0.0.0", "port": 9999,
                              "username": "u", "password": "p"},
                             {"server": "9.9.9.9", "port": 1080,
                              "username": "someone-else", "password": "x"}]}

    monkeypatch.setattr(builder.phones, "listing", lambda c: list(live))
    monkeypatch.setattr(builder.phones, "delete", delete)
    monkeypatch.setattr(builder.proxy_mod, "check",
                        lambda c, p: {"outboundIP": "8.8.8.8"})
    done["live"] = live
    done["client"] = FakeClient()
    return done


def test_the_sync_every_session_starts_with_actually_runs(world, monkeypatch):
    """It had no test at all - everything that reaches it patches it out - so a
    rename inside it broke the first line of every console session and the
    suite stayed green. `'bool' object is not callable` (2026-08-14).
    """
    book = make_book(gmails=2, proxies=2, apps=2)
    book.phones = FakePhoneLog([
        {"sheet_row": 2, "state": "done", "serial": "729",
         "gmail": "g0@example.com", "app_account": "a0@example.com"}])
    book.phones.rows = lambda: []
    book.reload = lambda: None

    outcome = builder.sync_sheet(world["client"], book, FakeLedger())

    # the marked phone went, with its credentials settled either way
    assert world["deleted"] == ["P729"]
    assert outcome["deleted"] == ["729"]
    assert outcome["delivered"] == ["a0@example.com"]
    assert outcome["retired"] == ["g0@example.com"]
    # the proxy it was on is free again, and the one still behind a phone is not
    assert book.proxies._rows[0].values["Status"] == "free"
    assert book.proxies._rows[1].values["Used By"] == "730"
    # and the one GeeLark holds that the tab has never heard of is reported,
    # not added - which of them belong here is the operator's call
    assert outcome["unlisted"] == ["9.9.9.9:1080 (someone-else)"]
    assert len(book.proxies._rows) == 2


def test_the_sync_can_be_asked_to_skip_the_part_that_costs_time(world):
    """A live connection per free proxy is the only slow half, and the switch
    that turns it off is the one that shadowed the function it turns on."""
    asked = []
    book = make_book(gmails=1, proxies=2, apps=1)
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: []
    book.reload = lambda: None

    builder.sync_sheet(world["client"], book, FakeLedger(),
                       probe_proxies=False)

    assert asked == []
    assert "dead" not in builder.sync_sheet(world["client"], book,
                                            FakeLedger(), probe_proxies=False)


# ------------------------------------ rows a run was holding when it died
def test_a_row_left_building_with_a_gmail_becomes_finishable(world):
    """`building` means "a run has this right now", which is why every other
    reader skips it - and nothing ever un-set it. A killed run left phone 750
    saying `building` forever: no finish would offer it, and the phone sat in
    the panel behind a row nobody acts on (2026-08-14)."""
    book = make_book()
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: [
        {"sheet_row": 4, "Serial": "730", "Status": "building",
         "Gmail": "g@example.com", "GPT Account": ""}]
    written = {}
    book.phones.finish = lambda row, **fields: written.update({row: fields})

    outcome = builder.settle_abandoned(None, book, FakeLedger())

    assert outcome["abandoned"] == ["730"]
    assert written[4]["Status"] == "incomplete"
    assert "Google is signed in" in written[4]["Note"]
    assert world["deleted"] == []          # it is worth finishing, not deleting


def test_a_row_left_building_with_nothing_on_it_is_deleted(world):
    """Same rule a build applies to itself: a phone with no Google account is
    not a phone."""
    book = make_book()
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: [
        {"sheet_row": 4, "Serial": "730", "Status": "building",
         "Gmail": "", "GPT Account": ""}]

    outcome = builder.settle_abandoned(None, book, FakeLedger())

    assert outcome["discarded"] == ["730"]
    assert world["deleted"] == ["P730"]
    assert book.phones.deleted_rows == [4]


def test_a_running_phone_a_run_claims_is_left_to_that_run(world):
    """What separates a live run from a dead one is the claim, not the power
    state - a phone being up says only that nobody stopped it."""
    world["live"][0]["status"] = 0                       # 729 is running
    book = make_book()
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: [
        {"sheet_row": 4, "Serial": "729", "Status": "building",
         "Gmail": "", "GPT Account": ""}]

    outcome = builder.settle_abandoned(None, book,
                                       FakeLedger({"P729": FakeClaim()}))

    assert outcome == {"abandoned": [], "discarded": []}
    assert world["deleted"] == []


def test_a_running_phone_nothing_claims_is_stopped_and_then_settled(
        world, monkeypatch):
    """A run that lost its network died without stopping its phones, so they
    stayed up with nothing accountable for them - and a running phone is
    settled by nothing, offered to `finish` by nothing and deleted by nothing.
    Two rows sat on `building` for good (2026-08-17, phones 838 and 839)."""
    world["live"][0]["status"] = 0                       # 729 is running
    stopped = []
    monkeypatch.setattr(builder.phones, "stop",
                        lambda c, phone_id: stopped.append(phone_id))
    monkeypatch.setattr(builder.phones, "wait_until_stopped",
                        lambda *a, **k: True)
    book = make_book()
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: [
        {"sheet_row": 4, "Serial": "729", "Status": "building",
         "Gmail": "", "GPT Account": ""}]

    outcome = builder.settle_abandoned(None, book, FakeLedger())

    assert stopped == ["P729"]               # stopped so it can be settled
    assert outcome["discarded"] == ["729"]   # nothing on it, so not a phone
    assert world["deleted"] == ["P729"]


def test_a_running_phone_that_will_not_stop_keeps_its_row(world, monkeypatch):
    """Better a row that says `building` than one dropped for a phone still
    sitting in the panel."""
    world["live"][0]["status"] = 0
    monkeypatch.setattr(builder.phones, "stop",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("GeeLark said no")))
    book = make_book()
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: [
        {"sheet_row": 4, "Serial": "729", "Status": "building",
         "Gmail": "", "GPT Account": ""}]

    outcome = builder.settle_abandoned(None, book, FakeLedger())

    assert outcome == {"abandoned": [], "discarded": []}
    assert world["deleted"] == []


def test_a_running_phone_with_a_gmail_is_stopped_and_made_finishable(
        world, monkeypatch):
    """The rule is unchanged by the stopping: what is on the phone decides
    what the row becomes."""
    world["live"][0]["status"] = 0
    monkeypatch.setattr(builder.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(builder.phones, "wait_until_stopped",
                        lambda *a, **k: True)
    book = make_book()
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: [
        {"sheet_row": 4, "Serial": "729", "Status": "building",
         "Gmail": "g0@example.com", "GPT Account": ""}]
    written = {}
    book.phones.finish = lambda row, **fields: written.update({row: fields})

    outcome = builder.settle_abandoned(None, book, FakeLedger())

    assert outcome["abandoned"] == ["729"]
    assert written[4]["Status"] == "incomplete"
    assert world["deleted"] == []          # worth finishing, not deleting


def test_a_boot_that_never_finishes_is_named_rather_than_called_unplanned(
        device, settings, monkeypatch, drive):
    """It reached the catch-all and was reported as "an error nobody planned
    for", which is the wrong thing to say about a phone that did not boot."""
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    monkeypatch.setattr(
        builder.phones, "ensure_running",
        lambda *a, **k: (_ for _ in ()).throw(
            builder.phones.PhoneError("phone P1 did not start within 600s")))

    build = drive(make_book(), settings, google=[SIGNED_IN])

    assert build.status == "phone_would_not_start"
    assert "nobody planned for" not in builder.outcome_of(build)
    assert failures.verdict(build.status).stops_the_phone


def test_the_boot_wait_is_capped_rather_than_given_the_whole_budget():
    """A phone GeeLark kept reporting as `starting` was polled for another
    thirty-eight minutes, because every caller handed over its own deadline."""
    import ast
    import pathlib

    source = pathlib.Path(builder.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "ensure_running"]

    assert calls, "no boot waits found - the scan has broken"
    for call in calls:
        timeout = next((kw.value for kw in call.keywords if kw.arg == "timeout"),
                       None)
        assert timeout is not None, f"line {call.lineno} takes the default"
        assert isinstance(timeout, ast.Call) and timeout.func.id == "min", (
            f"builder.py:{call.lineno} hands ensure_running a deadline instead "
            f"of capping it at phones.BOOT_SECONDS")


def test_a_phone_a_run_still_claims_is_left_alone_even_when_it_reads_stopped(
        world):
    """The power state alone was not enough. A phone stuck in `starting`
    reports as `stopped`, so a build patiently waiting for one to boot looked
    exactly like a dead run - and this deleted phone 750 out from under a live
    build, which failed with `env not found` twenty minutes later
    (2026-08-14)."""
    book = make_book()
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: [
        {"sheet_row": 4, "Serial": "730", "Status": "building",
         "Gmail": "", "GPT Account": ""}]
    ledger = FakeLedger({"P730": FakeClaim()})

    outcome = builder.settle_abandoned(None, book, ledger)

    assert outcome == {"abandoned": [], "discarded": []}
    assert world["deleted"] == []


def test_a_claim_old_enough_to_be_stale_does_not_protect_it(world):
    """Otherwise a run killed without releasing its claim protects the row for
    good, which is the state this function exists to clear."""
    book = make_book()
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: [
        {"sheet_row": 4, "Serial": "730", "Status": "building",
         "Gmail": "", "GPT Account": ""}]
    ledger = FakeLedger({"P730": FakeClaim(is_stale=True)})

    outcome = builder.settle_abandoned(None, book, ledger)

    assert outcome["discarded"] == ["730"]
    assert world["deleted"] == ["P730"]


def test_a_result_lands_on_its_own_row_when_a_sibling_deletes_one(
        device, settings, monkeypatch):
    """`start` hands back a row number and a build holds it for ten minutes.
    Any sibling discarding its phone deletes a row, and every row below it
    moves up - so that number comes to mean a different phone, and writing
    through it puts one build's result on another's row and loses both
    (2026-08-14, phone 751 gone from a tab that recorded it).
    """
    book = make_book(gmails=3, proxies=3, apps=3)
    tab = book.phones._ws

    # Three rows, as three builds would have appended them.
    for serial in ("758", "759", "760"):
        book.phones.start(Serial=serial, Proxy="SX1")
    assert [r[PHONE_HEADERS.index("Serial")] for r in tab.rows] == \
           ["758", "759", "760"]

    # 758 discards mid-run. Everything below it shifts up by one.
    book.phones.drop("758")
    assert [r[PHONE_HEADERS.index("Serial")] for r in tab.rows] == \
           ["759", "760"]

    # 760 now finishes, holding the row number it was given at the start.
    builder._record(book, builder.Build(index=3, ok=True, status="ready",
                                        serial="760", gmail="g@example.com"))

    written = {r[PHONE_HEADERS.index("Serial")]:
               r[PHONE_HEADERS.index("Status")] for r in tab.rows}
    assert written == {"759": "building", "760": "ready"}, (
        f"760's result landed on the wrong row: {written}")


# --------------------------------------- sharing an exit when nothing is free
def test_an_exhausted_pool_borrows_an_exit_rather_than_stopping(
        device, settings, drive, monkeypatch):
    """Phone 762 did everything right, met one ordinary refusal, and stopped
    because the run had been given as many phones as it had proxies. With
    nothing free it takes one another phone is already on."""
    book = make_book(proxies=2, apps=1)
    # The second proxy is already behind a phone, so nothing is free once this
    # build takes the first.
    book.proxies.spend(book.proxies._rows[1], serial="900", note="On phone 900.")

    build = drive(book, settings, google=[SIGNED_IN],
                  app=[Outcome("fatal", "network_ssl_rejected"), SIGNED_IN])

    assert build.ok, build.status
    assert build.shared_exit
    assert device.proxies_set == ["10.0.0.1"]        # it moved onto the shared one
    # and the phone's row says so, because that is what someone reading it later
    # is deciding on
    assert "shares one with another" in builder._phone_note(build)


def test_a_borrowed_exit_is_not_taken_from_the_phone_that_owns_it(
        device, settings, drive):
    """It is not claimed and not released: another phone owns it, and handing
    it back to the pool at the end would offer it as free stock."""
    book = make_book(proxies=2, apps=1)
    owned = book.proxies._rows[1]
    book.proxies.spend(owned, serial="900", note="On phone 900.")

    drive(book, settings, google=[SIGNED_IN],
          app=[Outcome("fatal", "network_ssl_rejected"), SIGNED_IN])

    assert book.proxies.status_of(owned) == book.proxies.spent_status
    assert owned.values["Used By"] == "900"
    assert book.proxies.available == []


def test_a_borrowed_exit_is_not_taken_twice_by_the_same_build(
        device, settings, drive):
    """Without that bound the loop never ends: a phone refused twice takes back
    the exit that refused it first and goes round for as long as its budget
    lasts - which is what holding refused proxies claimed was written to stop
    (2026-08-11, phone 658, forty-nine minutes)."""
    book = make_book(proxies=2, apps=1)
    book.proxies.spend(book.proxies._rows[1], serial="900", note="On phone 900.")
    refused = Outcome("fatal", "network_ssl_rejected")

    build = drive(book, settings, google=[SIGNED_IN], app=[refused] * 6)

    assert not build.ok
    assert build.status == "all_exits_refused"
    # one swap onto the shared exit, and then there is genuinely nothing left
    assert device.proxies_set == ["10.0.0.1"]


def test_a_done_phone_that_is_running_is_stopped_and_then_deleted(monkeypatch):
    """`done` means finished with it - delete it. This used to report the
    phone and stop there, so the mark was half carried out and the row sat in
    the tab until someone noticed, closed the viewer and synced again
    (2026-08-16, phones 749 and 751)."""
    stopped, deleted, state = [], [], {"P1": builder.phones.RUNNING}
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650",
                                    "status": state["P1"]}])
    monkeypatch.setattr(builder.phones, "stop",
                        lambda c, pid: (stopped.append(pid),
                                        state.__setitem__(pid,
                                                          builder.phones.STOPPED)))
    monkeypatch.setattr(builder.phones, "status", lambda c, pid: state[pid])
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))
    monkeypatch.setattr(builder.time, "sleep", lambda *a: None)
    # The clock walks, the way its sibling three tests below already does.
    # Without it the settle loop polls a no-op sleep against a real deadline,
    # so a check that stops agreeing the phone is down spins for the whole
    # ninety seconds - and the suite hangs where it should report.
    clock = itertools.count(0, 30)
    monkeypatch.setattr(builder.time, "monotonic", lambda: next(clock))
    book = state_book([{"sheet_row": 5, "state": "done", "serial": "650",
                        "gmail": "g@example.com", "app_account": "a0@example.com"}])

    out = builder.apply_phone_states(None, book, FakeLedger())

    assert stopped == ["P1"] and deleted == ["P1"]
    assert out["deleted"] == ["650"] and not out["running"]
    assert book.phones.deleted_rows == [5]


def test_a_phone_a_run_is_working_on_is_still_refused(monkeypatch):
    """The one reason to leave a marked phone alone. The power state is not:
    a phone left up by a browser tab is nobody's, and `done` on it means
    delete."""
    stopped, deleted = [], []
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650",
                                    "status": builder.phones.RUNNING}])
    monkeypatch.setattr(builder.phones, "stop", lambda c, pid: stopped.append(pid))
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))
    book = state_book([{"sheet_row": 5, "state": "done", "serial": "650",
                        "gmail": "", "app_account": ""}])

    out = builder.apply_phone_states(None, book, FakeLedger({"P1": FakeClaim()}))

    assert stopped == [] and deleted == []
    assert out["held"] == ["650"]
    assert book.phones.deleted_rows == []


def test_a_phone_that_will_not_stop_keeps_its_row(monkeypatch):
    """A row still there is a better outcome than a delete that half worked."""
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650",
                                    "status": builder.phones.RUNNING}])
    monkeypatch.setattr(builder.phones, "stop", lambda c, pid: None)
    monkeypatch.setattr(builder.phones, "status",
                        lambda c, pid: builder.phones.RUNNING)   # never settles
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: pytest.fail("deleted a "
                                                                "running phone"))
    monkeypatch.setattr(builder.time, "sleep", lambda *a: None)
    clock = itertools.count(0, 30)          # walks past the timeout instantly
    monkeypatch.setattr(builder.time, "monotonic", lambda: next(clock))
    book = state_book([{"sheet_row": 5, "state": "done", "serial": "650",
                        "gmail": "", "app_account": ""}])

    out = builder.apply_phone_states(None, book, FakeLedger())

    assert out["running"] == ["650"]
    assert book.phones.deleted_rows == []


def test_a_claim_a_finished_build_never_wrote_back_is_corrected(world):
    """`claimed` means a run is holding it, and the sync stopped there because
    the power state cannot tell a live run from a dead one. The ledger can: a
    phone on this exit whose claim was released is a build that finished and
    never wrote the row back. SX16 and SX17 sat like that through a whole run,
    each with a ready phone on it (2026-08-16)."""
    book = make_book(proxies=2)
    stale = book.proxies._rows[0]
    book.proxies.claim()                       # as a build that died left it
    assert book.proxies.status_of(stale) == book.proxies.claimed_status

    changed = builder.sync_proxies(world["client"], book, FakeLedger())

    assert book.proxies.status_of(stale) == book.proxies.spent_status
    assert stale.values["Used By"] == "729"
    assert any("729" in line for line in changed["attached"])


def test_a_claim_a_live_run_still_holds_is_left_alone(world):
    """The one reason to leave it: a build is working on that phone now."""
    book = make_book(proxies=2)
    stale = book.proxies._rows[0]
    book.proxies.claim()
    ledger = FakeLedger({"P729": FakeClaim()})

    builder.sync_proxies(world["client"], book, ledger)

    assert book.proxies.status_of(stale) == book.proxies.claimed_status


def test_a_claim_with_no_phone_behind_it_is_left_for_release_stuck(world):
    """There is no phone to ask about, and a run between its claim and its
    create looks exactly the same - so this is not guessed at."""
    # The fixture's two phones sit on 10.0.0.0 and 10.0.0.1, so the third
    # proxy is the one with nothing behind it.
    book = make_book(proxies=3)
    lonely = book.proxies._rows[2]
    book.proxies._set(lonely, {"Status": book.proxies.claimed_status})
    book.proxies.load()
    lonely = book.proxies._rows[2]

    builder.sync_proxies(world["client"], book, FakeLedger())

    assert book.proxies.status_of(lonely) == book.proxies.claimed_status


# ---------------------------------------------------- the record that survives
def history_rows(book):
    return [dict(zip(HistoryLog.HEADERS, r, strict=True))
            for r in book.history._ws.rows]


def test_every_finished_phone_leaves_a_history_row(device, settings, drive):
    """The Phones tab is current state - a row marked done is deleted, and
    with it every answer to "what did we build on Tuesday". This is the row
    that stays."""
    book = make_book()
    drive(book, settings, google=[SIGNED_IN])

    rows = history_rows(book)
    assert len(rows) == 1
    row = rows[0]
    assert row["Event"] == "ready" and row["Serial"] == "622"
    assert row["Gmail"] == "g0@example.com"
    assert row["GPT Account"] == "a0@example.com"
    assert row["When"] and row["Machine"]
    assert row["Note"].startswith("Ready")


def test_a_discarded_phone_is_history_too(device, settings, monkeypatch, drive):
    """A phone created and thrown away cost real minutes; without a row it
    never happened, and 'why is the bill bigger than the tab' has no answer."""
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    book = make_book(gmails=1)

    drive(book, settings, google=[Outcome("fatal", "wrong_password")])

    rows = history_rows(book)
    assert [r["Event"] for r in rows] == ["discarded"]
    assert "nothing was ever signed into it" in rows[0]["Note"]


def test_applying_a_mark_is_history(monkeypatch):
    """Delivery is the event the whole pipeline exists for, and it is also the
    moment the Phones row vanishes - so it is exactly what History must hold."""
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2}])
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    # state_book swaps in a FakePhoneLog but keeps make_book's real History.
    book = state_book([{"sheet_row": 5, "state": "done", "serial": "650",
                        "gmail": "g@example.com", "app_account": "a0@example.com"}])

    builder.apply_phone_states(None, book, FakeLedger())

    rows = [dict(zip(HistoryLog.HEADERS, r, strict=True))
            for r in book.history._ws.rows]
    assert [r["Event"] for r in rows] == ["done"]
    assert rows[0]["GPT Account"] == "a0@example.com"
    assert "delivered" in rows[0]["Note"]


def test_a_sync_step_that_fails_does_not_discard_the_ones_before_it(
        world, monkeypatch):
    """The write quota exhausted partway through a sync must not unwind the
    whole thing: by the time the proxy check writes, the phones are deleted and
    the credentials settled, and crashing out would leave the console unable to
    open while reporting none of it (2026-08-17)."""
    from geelark_farm.gsheet import SheetError

    book = make_book(proxies=2)
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: []
    book.reload = lambda: None
    # The proxy check is the step that crashed live; make it raise.
    monkeypatch.setattr(builder, "check_proxies",
                        lambda c, b: (_ for _ in ()).throw(
                            SheetError("row 5: the sheet's write quota stayed "
                                       "exhausted")))
    # An earlier step that does real work, so we can prove it survived.
    monkeypatch.setattr(builder, "sync_proxies",
                        lambda c, b, ledger: {"released": ["SX9"]})

    outcome = builder.sync_sheet(world["client"], book, FakeLedger())

    assert outcome["released"] == ["SX9"]          # the earlier step is kept
    assert outcome["incomplete"] == ["checked"]    # and the failure is named


# ------------------------------------------------ naming the phones in GeeLark
class NamingClient:
    def __init__(self, listing):
        self._listing = listing
        self.renames: list[tuple[str, str]] = []

    def data(self, path, payload=None):
        return {"items": self._listing}

    def post(self, path, payload):
        assert path == "/v1/phone/detail/update"
        self.renames.append((payload["id"], payload["name"]))


class NamingBook:
    def __init__(self, rows):
        self.phones = type("P", (), {"rows": lambda _self: rows})()


def naming_client(monkeypatch, listing):
    from geelark_farm import builder, phones
    client = NamingClient(listing)
    monkeypatch.setattr(phones, "listing", lambda c: listing)
    monkeypatch.setattr(builder.phones, "listing", lambda c: listing)
    return client


def test_a_phone_left_with_a_timestamp_name_is_renamed(monkeypatch):
    """The panel listed `farm-1786928959` nine rows deep. Phones made before
    the naming existed still carry those, and so does one renamed by hand."""
    from geelark_farm import phones as ph
    from geelark_farm.builder import sync_phone_names
    listing = [{"id": "P1", "serialNo": "832", "serialName": "farm-1786928959",
                "status": ph.STOPPED}]
    client = naming_client(monkeypatch, listing)
    book = NamingBook([{"Serial": "832", "Gmail": "AldenBrooke465837@example.com"}])

    assert sync_phone_names(client, book) == ["832 - AldenBrooke465837"]
    assert client.renames == [("P1", "832 - AldenBrooke465837")]


def test_a_phone_already_named_right_is_not_written_again(monkeypatch):
    from geelark_farm import phones as ph
    from geelark_farm.builder import sync_phone_names
    listing = [{"id": "P1", "serialNo": "832",
                "serialName": "832 - AldenBrooke465837", "status": ph.STOPPED}]
    client = naming_client(monkeypatch, listing)
    book = NamingBook([{"Serial": "832", "Gmail": "AldenBrooke465837@example.com"}])

    assert sync_phone_names(client, book) == []
    assert client.renames == []


def test_a_running_phone_is_left_for_the_next_sync(monkeypatch):
    """A tidier list is not worth reaching into a build that is under way -
    GeeLark's own note on detail/update is not to call it against a phone that
    is coming up."""
    from geelark_farm import phones as ph
    from geelark_farm.builder import sync_phone_names
    listing = [{"id": "P1", "serialNo": "827", "serialName": "farm-1786928922",
                "status": ph.RUNNING},
               {"id": "P2", "serialNo": "829", "serialName": "farm-1786928936",
                "status": ph.STARTING}]
    client = naming_client(monkeypatch, listing)
    book = NamingBook([{"Serial": "827", "Gmail": "a@gmail.com"},
                       {"Serial": "829", "Gmail": "b@gmail.com"}])

    assert sync_phone_names(client, book) == []
    assert client.renames == []


def test_a_phone_with_no_gmail_in_the_tab_is_named_by_its_serial_alone(
        monkeypatch):
    """Half a name is still the half that matters - the serial is the key
    everything else is filed under. The next sync completes it once the tab
    has the address."""
    from geelark_farm import phones as ph
    from geelark_farm.builder import sync_phone_names
    listing = [{"id": "P1", "serialNo": "832", "serialName": "farm-1786928959",
                "status": ph.STOPPED}]
    client = naming_client(monkeypatch, listing)

    assert sync_phone_names(client, NamingBook([])) == ["832"]
    assert client.renames == [("P1", "832")]


def test_a_rename_that_is_refused_does_not_stop_the_others(monkeypatch):
    from geelark_farm import phones as ph
    from geelark_farm.builder import sync_phone_names
    listing = [{"id": "P1", "serialNo": "832", "serialName": "farm-1",
                "status": ph.STOPPED},
               {"id": "P2", "serialNo": "833", "serialName": "farm-2",
                "status": ph.STOPPED}]
    client = naming_client(monkeypatch, listing)

    def refuse(path, payload):
        if payload["id"] == "P1":
            raise RuntimeError("refused")
        client.renames.append((payload["id"], payload["name"]))
    client.post = refuse
    book = NamingBook([{"Serial": "832", "Gmail": "a@gmail.com"},
                       {"Serial": "833", "Gmail": "b@gmail.com"}])

    assert sync_phone_names(client, book) == ["833 - b"]


def test_the_sync_says_what_it_is_doing_while_it_does_it(world):
    """The whole sync takes half a minute or more behind one unchanging line,
    with every INFO record its steps emit scrolling through it. A caller that
    draws a spinner needs to be told which part is running."""
    book = make_book(gmails=1, proxies=2, apps=1)
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: []
    book.reload = lambda: None
    said = []

    builder.sync_sheet(world["client"], book, FakeLedger(),
                       on_step=said.append)

    assert said[0] == "carrying out the State column"
    assert "testing every free proxy" in said
    # Human phrases, not the internal keys the outcome is filed under.
    assert not any("_" in phrase for phrase in said)


def test_every_step_has_something_to_say_about_itself():
    """A step added without a phrase would show the console its key."""
    import ast
    import inspect

    source = inspect.getsource(builder.sync_sheet)
    keys = {node.args[0].value
            for node in ast.walk(ast.parse(source.lstrip()))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "step"
            and node.args and isinstance(node.args[0], ast.Constant)}

    assert keys, "no step calls found - the sweep is looking in the wrong place"
    assert keys <= set(builder.STEP_NAMES), keys - set(builder.STEP_NAMES)


# --------------------------------------------- the network going away mid-run
def test_a_lost_connection_is_a_named_outcome_not_an_unhandled_error(
        device, settings, monkeypatch):
    """DNS stopped resolving for GeeLark, Sheets and Google's token endpoint
    at once - the machine's network, not any of them - and all three builds
    were reported as "an error nobody planned for" over two hundred lines of
    urllib3 traceback in a live table (2026-08-17)."""
    from geelark_farm.api import TransportError

    book = make_book()
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: (_ for _ in ()).throw(
                            TransportError("/v1/shell/execute failed after 3 "
                                           "attempt(s): getaddrinfo failed")))

    build = builder.build_one(None, settings, book, FakeLedger(), 1)

    assert build.status == "network_unreachable"
    assert not build.ok
    assert "lost its connection" in build.detail
    assert "urllib3" not in build.detail and "Traceback" not in build.detail


def test_nothing_is_spent_when_the_connection_drops(device, settings,
                                                    monkeypatch):
    """Nothing was judged, so no credential may be marked and no exit
    condemned - the run is repeatable once the network is back."""
    from geelark_farm.api import TransportError

    book = make_book()
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: (_ for _ in ()).throw(
                            TransportError("getaddrinfo failed")))

    builder.build_one(None, settings, book, FakeLedger(), 1)

    found = failures.verdict("network_unreachable")
    assert found.blame == failures.NOBODY
    assert not found.costs_the_credential
    assert not found.needs_a_new_exit
    # The address goes back to the pool unmarked, not condemned with a reason.
    assert book.gmails._rows[0].values["Status"] in ("", "free")


def test_a_sheet_that_will_not_take_the_row_does_not_lose_the_build(
        device, settings, drive, monkeypatch):
    """`_record` runs in a `finally`, where an exception does not merely fail -
    it replaces the value the function was about to return. Three finished
    Builds were thrown away that way, and the summary reported the same
    urllib3 error three times in place of what each phone had reached."""
    def unreachable(*a, **k):
        raise ConnectionError("Failed to resolve 'sheets.googleapis.com'")
    monkeypatch.setattr(builder, "_record", unreachable)

    build = drive(make_book(), settings, google=[SIGNED_IN])

    assert build.ok and build.status == "ready"      # what it actually reached


# ------------------------------------ what a settled phone leaves in History
class RecordingBook:
    """A book that remembers what was written to the History tab."""

    def __init__(self, book):
        self._book = book
        self.history = []

    def __getattr__(self, name):
        return getattr(self._book, name)

    def record_history(self, **fields):
        self.history.append(fields)


def settled(world, monkeypatch, row):
    monkeypatch.setattr(builder.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(builder.phones, "wait_until_stopped",
                        lambda *a, **k: True)
    book = RecordingBook(make_book())
    book._book.phones = FakePhoneLog([])
    book._book.phones.rows = lambda: [row]
    book._book.phones.finish = lambda r, **f: None
    outcome = builder.settle_abandoned(None, book, FakeLedger())
    return outcome, book.history


def test_a_rescued_phone_is_recorded_like_any_other(world, monkeypatch):
    """This wrote nothing to History at all, so a phone rescued from a killed
    run left no trace of having been rescued: the tab said `incomplete` and
    how it got there was missing (2026-08-20)."""
    outcome, history = settled(world, monkeypatch, {
        "sheet_row": 4, "Serial": "730", "Status": "building",
        "Proxy": "SX7", "Gmail": "g@example.com", "GPT Account": ""})

    assert outcome["abandoned"] == ["730"]
    assert len(history) == 1
    written = history[0]
    assert written["Serial"] == "730"
    assert written["Event"] == "incomplete"
    assert written["Proxy"] == "SX7"           # the row knew it all along
    assert written["Gmail"] == "g@example.com"
    assert "Google is signed in" in written["Note"]


def test_a_discarded_phone_records_the_exit_it_was_on(world, monkeypatch):
    """Two of these had no Proxy where the fifteen written by a build did -
    one event, two writers, different completeness."""
    _, history = settled(world, monkeypatch, {
        "sheet_row": 4, "Serial": "730", "Status": "building",
        "Proxy": "SX7", "Gmail": "", "GPT Account": ""})

    assert history[0]["Event"] == "discarded"
    assert history[0]["Proxy"] == "SX7"


def test_neither_invents_a_duration_it_does_not_have(world, monkeypatch):
    """The run that did the work died without reporting. A nought would read
    as "took no time" rather than "nobody knows"."""
    for gmail in ("g@example.com", ""):
        _, history = settled(world, monkeypatch, {
            "sheet_row": 4, "Serial": "730", "Status": "building",
            "Proxy": "SX7", "Gmail": gmail, "GPT Account": ""})

        assert "Seconds" not in history[0], history[0]


# ------------------------------- phones and credentials that lost each other
class StrandBook:
    def __init__(self, book, rows):
        self._book = book
        self._rows = rows

    def __getattr__(self, name):
        return getattr(self._book, name)

    @property
    def phones(self):
        log = FakePhoneLog([])
        log.rows = lambda: self._rows
        return log


def stranded(monkeypatch, *, live, phone_rows, gmail_serial=None,
             app_serial=None):
    listing = [{"id": f"P{s}", "serialNo": s} for s in live]
    monkeypatch.setattr(builder.phones, "listing", lambda c: listing)
    book = make_book(gmails=1, proxies=1, apps=1)
    if gmail_serial is not None:
        book.gmails.spend(book.gmails._rows[0], serial=gmail_serial)
    if app_serial is not None:
        book.apps.spend(book.apps._rows[0], serial=app_serial)
    wrapped = StrandBook(book, phone_rows)
    return book, builder.strand_check(None, wrapped)


def test_a_phone_the_tab_has_never_heard_of_is_reported(monkeypatch):
    """Every settling path reads the Phones tab and acts on rows, so a phone
    with no row is touched by nothing. Phone 964 ran for a day that way after
    an older version recorded it as discarded when the delete had actually
    been refused (2026-08-20)."""
    _, outcome = stranded(monkeypatch, live=["964"], phone_rows=[])

    assert outcome["unknown_phones"] == ["964"]


def test_a_phone_with_a_row_is_not_reported(monkeypatch):
    _, outcome = stranded(monkeypatch, live=["964"],
                          phone_rows=[{"Serial": "964"}])

    assert "unknown_phones" not in outcome


def test_it_reports_rather_than_deletes(monkeypatch):
    """Which of them belong here is the operator's call, and a report that
    deletes phones is not a report - `geelark pools` learned that once."""
    deleted = []
    monkeypatch.setattr(builder.phones, "delete",
                        lambda *a, **k: deleted.append(a))

    stranded(monkeypatch, live=["964"], phone_rows=[])

    assert deleted == []


def test_a_gmail_whose_phone_is_gone_is_retired(monkeypatch):
    """The rule about it is not in doubt: it signed into a phone, and that is
    the credit it had to spend, whatever became of the phone."""
    book, outcome = stranded(monkeypatch, live=[], phone_rows=[],
                             gmail_serial="968")

    assert outcome["stranded_retired"]
    assert book.gmails.status_of(book.gmails._rows[0]) == \
        book.gmails.retired_status


def test_a_gmail_whose_phone_still_exists_is_left_alone(monkeypatch):
    book, outcome = stranded(monkeypatch, live=["968"],
                             phone_rows=[{"Serial": "968"}],
                             gmail_serial="968")

    assert "stranded_retired" not in outcome
    assert book.gmails.status_of(book.gmails._rows[0]) == \
        book.gmails.spent_status


def test_an_app_account_is_reported_and_not_touched(monkeypatch):
    """`delivered` and `freed` are a judgement about whether it ever got a
    fair device. Guessing wrong either retires an account that was never used
    or frees one that is with a customer."""
    book, outcome = stranded(monkeypatch, live=[], phone_rows=[],
                             app_serial="965")

    assert outcome["stranded_waiting"]
    assert book.apps.status_of(book.apps._rows[0]) == book.apps.spent_status


def test_a_credential_already_settled_is_not_touched_again(monkeypatch):
    """Only rows still held against a phone count - a row already retired has
    had its decision made."""
    book = make_book(gmails=1, proxies=1, apps=1)
    book.gmails.retire(book.gmails._rows[0])
    monkeypatch.setattr(builder.phones, "listing", lambda c: [])
    wrapped = StrandBook(book, [])

    outcome = builder.strand_check(None, wrapped)

    assert "stranded_retired" not in outcome


def test_a_crossed_out_gmail_does_not_name_a_phone(monkeypatch):
    """`sync_phone_names` builds the name from the tab's Gmail. A cross read
    as an address renames the phone `983 - X` in the panel."""
    from geelark_farm import phones as ph
    from geelark_farm.pools import PhoneLog
    listing = [{"id": "P1", "serialNo": "983", "serialName": "farm-1",
                "status": ph.STOPPED}]
    client = naming_client(monkeypatch, listing)
    book = NamingBook([{"Serial": "983", "Gmail": PhoneLog.said(PhoneLog.NO)}])

    renamed = builder.sync_phone_names(client, book)

    assert renamed == ["983"]
    assert client.renames == [("P1", "983")]


def test_a_phone_whose_gmail_is_crossed_out_is_not_kept_as_finishable(
        world, monkeypatch):
    """`settle_abandoned` asks `if row["Gmail"]`. A cross is truthy, so a
    phone with nothing signed into it would be marked `incomplete` and offered
    to `finish` for ever instead of being discarded."""
    from geelark_farm.pools import PhoneLog
    monkeypatch.setattr(builder.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(builder.phones, "wait_until_stopped",
                        lambda *a, **k: True)
    book = RecordingBook(make_book())
    book._book.phones = FakePhoneLog([])
    # as rows() hands it over, after the mark has been undone
    book._book.phones.rows = lambda: [
        {"sheet_row": 4, "Serial": "730", "Status": "building",
         "Proxy": "SX7", "Gmail": PhoneLog.said(PhoneLog.NO),
         "GPT Account": PhoneLog.said(PhoneLog.NO)}]
    book._book.phones.finish = lambda r, **f: None

    outcome = builder.settle_abandoned(None, book, FakeLedger())

    assert outcome["discarded"] == ["730"]     # not kept, not finishable
    assert outcome["abandoned"] == []


def test_a_dead_runs_claims_are_put_back_on_the_next_sync():
    """Three Gmails and three exits sat out of the pool for a day, twice in
    three days, because the only way back was a hand on the console
    (2026-08-21, 2026-08-22)."""
    import threading

    from geelark_farm.pools import GmailPool
    from tests.test_pools import CLAIMED_HEADERS, FakeWorksheet, claimed_row

    def stamped(seconds_ago):
        return time.strftime(GmailPool.CLAIM_FORMAT,
                             time.localtime(time.time() - seconds_ago))

    pool = GmailPool(
        FakeWorksheet(CLAIMED_HEADERS,
                      [claimed_row("old@b.com", when=stamped(7200)),
                       claimed_row("fresh@b.com", when=stamped(60))]),
        CLAIMED_HEADERS, threading.Lock())
    pool.load()
    book = type("Book", (), {"gmails": pool, "proxies": pool.__class__(
        FakeWorksheet(CLAIMED_HEADERS, []), CLAIMED_HEADERS, threading.Lock()),
        "apps": pool.__class__(FakeWorksheet(CLAIMED_HEADERS, []),
                               CLAIMED_HEADERS, threading.Lock())})()
    book.proxies.load()
    book.apps.load()

    freed = builder.free_abandoned_claims(book, 3600)

    assert len(freed) == 1 and "old@b.com" in freed[0]
    assert pool.status_of(pool._rows[0]) in pool.available_statuses
    assert pool.status_of(pool._rows[1]) == pool.claimed_status


def test_the_note_says_why_the_row_was_taken_back():
    """The Note cell is the operator's only account of what happened to a row
    they did not touch. It said "no run can hold one past its own budget",
    which stopped being the reason the moment a run began refreshing its own
    claims - and would read as plainly false with the window set to ten
    minutes and the budget still an hour (2026-08-25).
    """
    from tests.test_pools import CLAIMED_HEADERS, FakeWorksheet, claimed_row

    def empty():
        return GmailPool(FakeWorksheet(CLAIMED_HEADERS, []), CLAIMED_HEADERS,
                         threading.Lock())

    pool = GmailPool(
        FakeWorksheet(CLAIMED_HEADERS,
                      [claimed_row("old@b.com", when="2020-01-01 00:00:00")]),
        CLAIMED_HEADERS, threading.Lock())
    pool.load()
    book = type("Book", (), {"gmails": pool, "proxies": empty(),
                             "apps": empty()})()
    book.proxies.load()
    book.apps.load()

    builder.free_abandoned_claims(book, 600)

    note = pool._rows[0].values["Note"]
    assert "refresh" in note, "it does not say what actually decided this"
    assert "10 minutes" in note, "the window it was measured against is the fact"
    assert "budget" not in note


def test_the_sync_measures_against_the_window_not_the_budget(monkeypatch):
    """It WAS the build budget, and had to be: with no way to tell a live
    claim from an abandoned one, the only safe answer was "longer than any run
    could legitimately hold a credential".

    A run now restamps what it holds every minute, so a stamp that has stopped
    moving is proof on its own and the window is a number of its own. It still
    defaults to the build budget - shortening it is only safe once every
    machine on this sheet is refreshing - but the sync must read the window,
    or setting it would change nothing (2026-08-25).
    """
    import ast
    import inspect

    source = inspect.getsource(builder.run)
    call = next(n for n in ast.walk(ast.parse(source.lstrip()))
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "sync_sheet")
    passed = {k.arg: ast.unparse(k.value) for k in call.keywords}

    assert passed["stale_claim_seconds"] == "settings.stale_claim_seconds"


def test_the_window_is_five_missed_heartbeats_and_not_a_whole_budget(
        monkeypatch):
    """It used to default to the build budget, because before the heartbeat
    the only safe answer was "longer than any run could legitimately hold
    one". A run now restamps what it holds every sixty seconds, so a stamp
    that has not moved in five minutes is not a slow run - it is a gone one.

    What the old answer cost, the day it changed: a run was interrupted
    holding an app account, its phone was discarded, and the account sat
    `in_use` and unusable for the rest of an hour (2026-08-28).

    It is not free. A window shorter than a live holder's silence hands that
    holder's row to somebody else mid-build, so this is only right while every
    machine that claims against this sheet beats - which is why it is a
    default and not a constant.
    """
    from geelark_farm.config import STALE_CLAIM_DEFAULT, Settings

    monkeypatch.setenv("GEELARK_APP_ID", "id")
    monkeypatch.setenv("GEELARK_API_KEY", "key")
    monkeypatch.setenv("BUILD_BUDGET_SECONDS", "1234")
    monkeypatch.delenv("STALE_CLAIM_SECONDS", raising=False)

    assert Settings.load().stale_claim_seconds == STALE_CLAIM_DEFAULT
    assert STALE_CLAIM_DEFAULT == 5 * 60

    monkeypatch.setenv("STALE_CLAIM_SECONDS", "600")
    assert Settings.load().stale_claim_seconds == 600
    assert Settings.load().build_budget_seconds == 1234, "the two are separate"


def test_the_window_is_several_beats_wide_rather_than_one(monkeypatch):
    """One missed beat is a slow sheet write, not a dead run. The margin is
    the whole reason this is not simply `HEARTBEAT_SECONDS`."""
    from geelark_farm.config import STALE_CLAIM_DEFAULT
    from geelark_farm.pools import Pool

    assert STALE_CLAIM_DEFAULT >= 4 * Pool.HEARTBEAT_SECONDS


# --------------------------------------------- the path a build walked
def test_the_path_a_build_walked_is_one_cell_per_phase():
    """History is the only account of a run that crosses machines: the log
    file is per-day and lives on whichever computer produced it, so nothing
    about a build on the Mac was readable from here at all (2026-08-23)."""
    build = builder.Build(index=1, trails=[
        ("google", ["email_entry", "password_entry", "totp_entry"]),
        ("install", ["search", "app_page", "open"]),
        ("gpt", ["welcome", "email_entry", "email_code_entry", "onboarding"]),
    ])

    assert build.steps == (
        "google: email_entry > password_entry > totp_entry"
        " | install: search > app_page > open"
        " | gpt: welcome > email_entry > email_code_entry > onboarding")


def test_a_screen_handled_again_and_again_is_counted_not_repeated():
    """A screen handled three times without progress is the whole tell that
    something is looping, and printing it three times spends the width saying
    it three times."""
    build = builder.Build(index=1, trails=[
        ("gpt", ["welcome", "email_entry", "email_entry", "email_entry"])])

    assert build.steps == "gpt: welcome > email_entry x3"


def test_the_order_is_kept_not_just_the_count():
    """`Context.seen` counts visits per name, so `A > B > A > B` and
    `A > A > B > B` are the same dictionary - and telling a loop from a
    straight run is most of what reading one of these is for."""
    loop = builder.Build(index=1, trails=[("gpt", ["a", "b", "a", "b"])])
    straight = builder.Build(index=2, trails=[("gpt", ["a", "a", "b", "b"])])

    assert loop.steps == "gpt: a > b > a > b"
    assert straight.steps == "gpt: a x2 > b x2"
    assert loop.steps != straight.steps


def test_a_phase_that_never_saw_a_screen_is_left_out():
    """`app_not_installed` is decided before the loop runs. An empty phase
    named with nothing after it would read as a step that happened."""
    build = builder.Build(index=1, trails=[
        ("google", ["email_entry"]), ("install", []), ("gpt", [])])

    assert build.steps == "google: email_entry"


def test_each_account_a_phone_worked_through_leaves_its_own_path():
    """How far each got is most of what separates a bad batch of credentials
    from a phone that cannot sign anyone in."""
    build = builder.Build(index=1, trails=[
        ("gpt", ["welcome", "email_entry", "password_entry"]),
        ("gpt", ["welcome", "email_entry", "onboarding"]),
    ])

    assert build.steps.count("gpt:") == 2


def test_a_build_that_never_started_has_nothing_to_say():
    assert builder.Build(index=1).steps == ""


def test_the_router_hands_the_path_back_on_every_outcome():
    """A wrapper rather than a line before each `return`: the loop has five of
    them and a sixth would be added one day without it. The path is worth
    having on a success too - that is the shape a healthy run has, which is
    what makes a failure's shape readable."""
    from geelark_farm.flows import router

    class FakeContext(router.Context):
        def refresh(self):
            # Anything non-empty: the loop only asks whether a screen was read.
            self.elements = ["on screen"]
            self.blob = "on screen"

    seen = []
    screens = [router.Screen("first", lambda c: len(seen) < 1,
                             lambda c: seen.append(1)),
               router.Screen("second", lambda c: True,
                             lambda c: router.Outcome("fatal", "stopped"))]
    ctx = FakeContext(client=None, phone_id="P")

    out = router.drive(ctx, screens, is_done=lambda: None, budget_seconds=5)

    assert out.reason == "stopped"
    assert out.trail == ["first", "second"]


def test_a_flow_that_stops_before_the_loop_carries_an_empty_path():
    """`app_not_installed` never saw a screen, and says so by having none."""
    from geelark_farm.flows.router import Outcome

    assert Outcome("fatal", "app_not_installed").trail == []


def test_history_writes_the_path_beside_the_outcome():
    """Appended, never reordered: rows are written by position, so moving a
    column scrambles every row already written under the old one."""
    from geelark_farm.pools import HistoryLog

    assert HistoryLog.HEADERS[-1] == "Steps"
    assert HistoryLog.HEADERS.index("Note") < HistoryLog.HEADERS.index("Steps")


# ------------------------------------------ an exit we are standing on, not on
def test_a_borrowed_exit_is_not_handed_back_when_the_swap_is_refused(
        monkeypatch):
    """`_borrow_exit` returns an exit another phone is running on, without
    claiming it. Releasing that blanks its status and wipes the `Used By`
    naming its real owner - so the next build claims it, a third phone lands
    on the address, and nothing says whose it was (2026-08-23)."""
    from geelark_farm.api import ApiError

    book = Book.__new__(Book)
    released = []

    class Proxies:
        spent_status = "on a phone"
        _rows = []

        @staticmethod
        def status_of(r):
            return "on a phone"

        @staticmethod
        def release(resource, *, note=""):
            released.append(resource)

    object.__setattr__(book, "proxies", Proxies())

    borrowed = Resource(sheet_row=9, values={"Used By": "812"})
    borrowed.proxy = builder.proxy_mod.parse("socks5://u:p@1.2.3.4:1080")
    Proxies._rows = [borrowed]

    monkeypatch.setattr(builder, "_fresh_proxy",
                        lambda *a: (_ for _ in ()).throw(
                            builder.Aborted("no_usable_proxy")))
    monkeypatch.setattr(builder.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(builder.phones, "set_proxy",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ApiError(45004, "proxy did not answer",
                                     path="/p", trace_id="T")))
    build = builder.Build(index=1)

    with pytest.raises(builder.Aborted, match="proxy_change_refused"):
        builder._new_exit(None, None, book, build, "P1", None, "why", 60)

    assert released == [], "freed an exit another phone is running on"


def test_an_exit_this_build_claimed_is_handed_back_when_the_swap_is_refused(
        monkeypatch):
    """The other half: one we took is ours to give back, and holding it would
    keep good stock out of the pool for nothing."""
    from geelark_farm.api import ApiError

    book = Book.__new__(Book)
    released = []

    class Proxies:
        @staticmethod
        def release(resource, *, note=""):
            released.append(resource)

    object.__setattr__(book, "proxies", Proxies())

    claimed = Resource(sheet_row=4, values={})
    claimed.proxy = builder.proxy_mod.parse("socks5://u:p@5.6.7.8:1080")

    monkeypatch.setattr(builder, "_fresh_proxy", lambda *a: claimed)
    monkeypatch.setattr(builder.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(builder.phones, "set_proxy",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ApiError(45004, "no", path="/p", trace_id="T")))

    with pytest.raises(builder.Aborted, match="proxy_change_refused"):
        builder._new_exit(None, None, book, builder.Build(index=1),
                          "P1", None, "why", 60)

    assert released == [claimed]


# --------------------------------------- what must not escape from a finally
def test_releasing_survives_a_refusal_the_quota_guard_does_not_cover():
    """`_release` runs in a finally, where an exception does not fail the call
    - it replaces the value the call was about to return. It caught only
    `SheetError`, and `batch_write` re-raises every other APIError untouched
    (2026-08-23)."""
    book = Book.__new__(Book)
    freed = []

    class Pool:
        tab = "Gmails"

        @staticmethod
        def release(resource, *, note=""):
            raise RuntimeError("the key was revoked")

        @staticmethod
        def spend(resource, *, serial="", note=""):
            freed.append(resource)

    first = Resource(sheet_row=2, values={})
    second = Resource(sheet_row=3, values={})

    builder._release(book, builder.Build(index=1), [
        (Pool(), first, builder.RELEASE, "", ""),
        (Pool(), second, builder.SPEND, "", ""),
    ])

    # It did not raise, and the one after the failure still had its turn.
    assert freed == [second]


def test_a_sync_step_survives_a_geelark_failure(monkeypatch):
    """Every step also talks to GeeLark, and ApiError, TransportError and
    PhoneError are none of them a SheetError - so a hiccup partway through
    unwound the whole sync, which is what the guard exists to prevent."""
    from geelark_farm.api import TransportError

    book = Book.__new__(Book)
    object.__setattr__(book, "sync_lists", lambda: None)
    object.__setattr__(book, "reload", lambda: None)

    monkeypatch.setattr(builder, "apply_phone_states",
                        lambda *a, **k: {"deleted": ["1001"]})
    monkeypatch.setattr(builder, "settle_abandoned",
                        lambda *a, **k: (_ for _ in ()).throw(
                            TransportError("geelark went away")))
    # Two shapes, not one. `sync_proxies` and `strand_check` answer with a
    # dict that `step` merges into the report; the other two answer with a
    # list it files under the step's own name. One lambda for all four sent
    # half of them down a branch they never take in a real sync.
    for name in ("sync_proxies", "strand_check"):
        monkeypatch.setattr(builder, name, lambda *a, **k: {})
    for name in ("sync_phone_proxies", "sync_phone_names"):
        monkeypatch.setattr(builder, name, lambda *a, **k: [])

    outcome = builder.sync_sheet(None, book, None, probe_proxies=False)

    # The step that ran is still reported, and the one that died is named.
    assert outcome["deleted"] == ["1001"]
    assert outcome["incomplete"] == ["abandoned"]


# --------------------------------- what a finished phone says it walked
def test_a_finish_that_installs_records_that_it_did():
    """`build_one` records the install it does and `finish_one` did not, so a
    phone completed rather than built left no `install:` in its Steps cell
    (2026-08-23)."""
    import inspect

    source = inspect.getsource(builder.finish_one)

    assert "play_install.install(" in source
    assert 'trails.append(("install"' in source


def test_a_finish_knows_which_exit_the_phone_is_already_on():
    """With None it had no row to settle: the exit the phone was actually on
    went unrecorded, and whatever it took instead was written back as if it had
    always been there."""
    import inspect

    source = inspect.getsource(builder.finish_one)

    assert "find_by_name(build.proxy)" in source
    assert "proxy_row=own_exit" in source


def test_the_proxies_geelark_holds_are_read_past_the_first_page():
    """The report that says GeeLark has an exit the tab has never heard of
    silently stopped mentioning them past a hundred - the same cap that was
    fixed in `phones.listing`, in the other place it was written."""
    import inspect

    source = inspect.getsource(builder.sync_proxies)

    assert "MAX_PAGES" in source
    assert '"page": page' in source


def test_a_cell_that_is_cut_says_it_was_cut():
    """The Phones tab and History are not pools, so `_set` does not reach them
    and they cut with a plain slice - a note ending mid-word and a Steps cell
    mid-screen-name, with nothing to say it was cut."""
    from geelark_farm.pools import clip

    assert clip("short", 10) == "short"
    assert clip("x" * 40, 10).endswith("\u2026")
    assert len(clip("x" * 40, 10)) == 10
    # And no trailing space left in front of the mark.
    assert not clip("word " + "y" * 40, 6).endswith(" \u2026")


def test_the_fields_of_a_build_are_declared_in_one_run():
    """`steps` sat between them, so half the fields came after a method."""
    import ast
    import inspect

    body = ast.parse(inspect.getsource(builder.Build).lstrip()).body[0].body
    kinds = [type(node).__name__ for node in body]
    first_method = next(i for i, k in enumerate(kinds) if k == "FunctionDef")

    assert "AnnAssign" not in kinds[first_method:]


# ============================================================
# 'Outcome' object has no attribute 'trail' (2026-08-24).
# Ten builds died on it live. The suite was green throughout,
# because the fake install returned a class the real install
# never returns.
# ============================================================

def test_every_flow_outcome_carries_what_the_builder_reads_off_it():
    """The builder reads `.trail`, `.ok`, `.reason` and `.artifacts` off
    whatever a flow hands back - and the flows do not share one class. The
    router defines an Outcome for the two sign-ins, and play_install defines
    its own for the install.

    Nothing tied the two together. Both are called `Outcome`, both have `ok`
    and `reason`, and only one had `trail` - so the divergence was invisible
    at every call site and in every test.
    """
    from geelark_farm.flows.play_install import Outcome as Install
    from geelark_farm.flows.router import Outcome as Routed

    for made in (Install("success", "installed"),
                 Routed("success", "signed_in")):
        for attribute in ("ok", "reason", "trail", "artifacts"):
            assert hasattr(made, attribute), (
                f"{type(made).__module__}.Outcome has no {attribute!r}, and "
                f"the builder reads it off every flow outcome")


def test_the_install_fake_is_the_class_the_real_install_returns():
    """A fake is worth something only if it is the shape the code will meet.

    This one was the router's Outcome, which has a trail; the real install
    returns play_install's, which did not. Pinned by identity rather than by
    duck-typing, because duck-typing is exactly what failed to notice.
    """
    from geelark_farm.flows import play_install

    assert type(INSTALLED) is play_install.Outcome


def test_an_install_outcome_is_recorded_without_asking_it_for_a_trail_it_lacks():
    """The end of the story, at the line that crashed: an install outcome goes
    into `build.trails` and the Steps cell renders without it contributing a
    phase, because installing walks no screens.
    """
    build = builder.Build(index=1)
    installed = INSTALLED

    build.trails.append(("install", installed.trail))
    build.trails.append(("google", ["password", "totp"]))

    assert installed.trail == []
    assert "install" not in build.steps
    assert "password" in build.steps


# ------------------------------------------------- the thread that does the beating
class Beating:
    """A book that counts beats, and can be told to fail some of them."""

    def __init__(self, fail_first=0):
        self.beats = 0
        self.fail_first = fail_first
        self.started = threading.Event()

    def beat(self):
        self.beats += 1
        self.started.set()
        if self.beats <= self.fail_first:
            raise RuntimeError("the sheet was unreachable")
        return 3


def test_a_run_refreshes_what_it_is_holding_while_it_works(monkeypatch):
    """Nothing else moves those stamps. If this thread does not run, a long
    build's own claims go stale underneath it and the next sync anywhere frees
    the rows it is still using."""
    monkeypatch.setattr(builder.Pool, "HEARTBEAT_SECONDS", 0.01)
    book = Beating()

    stop = builder._start_heartbeat(book)
    try:
        assert book.started.wait(timeout=5), "it never beat at all"
    finally:
        stop()

    assert book.beats >= 1


def test_the_beating_stops_when_the_run_does(monkeypatch):
    """A beat that lands after the run has released everything would restamp
    a row somebody else has since claimed."""
    monkeypatch.setattr(builder.Pool, "HEARTBEAT_SECONDS", 0.01)
    book = Beating()

    stop = builder._start_heartbeat(book)
    assert book.started.wait(timeout=5)
    stop()

    settled = book.beats
    time.sleep(0.2)

    assert book.beats == settled, "it went on beating after being stopped"


def test_a_beat_that_fails_is_retried_rather_than_abandoned(monkeypatch):
    """The dangerous failure is the quiet one: the run keeps working, the
    stamps stop moving, and the next sync frees the rows out from under it. A
    network blip must not do that."""
    monkeypatch.setattr(builder.Pool, "HEARTBEAT_SECONDS", 0.01)
    book = Beating(fail_first=2)

    stop = builder._start_heartbeat(book)
    try:
        deadline = time.monotonic() + 5
        while book.beats < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop()

    assert book.beats >= 4, "it gave up after the first failure"


def test_the_run_starts_and_stops_the_heartbeat_around_the_work():
    """Pinned at the call site: the thread is started outside the try and
    stopped in the finally, so no path out of a run leaves it beating."""
    import ast
    import inspect

    source = inspect.getsource(builder._run_jobs)
    body = source[source.index("_start_heartbeat"):]

    assert "stop_beating()" in body
    # Not cleandoc: that is for docstrings and it reflows the body. A
    # module-level function's source is already at column zero.
    tree = ast.parse(source)
    tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
    assert any("stop_beating" in ast.unparse(node.finalbody) for node in tries), \
        "nothing stops the heartbeat on the way out"


# ------------------------------------- the label a finished build leaves behind
def test_a_finished_build_stops_labelling_the_lines_after_it(
        device, settings, monkeypatch):
    """`_context.build` is a thread-local, and with one worker `work` runs on
    the caller's own thread - so a build that ended an hour ago went on
    stamping its number onto every line logged afterwards. In a command that
    exits, that is until it exits. In `serve`, which does not, it is for ever
    (2026-08-27).

    The same shape as the console formatter that was replaced and never put
    back, which is why the label is cleared in a `finally` rather than after
    the return.
    """
    from geelark_farm.logs import NO_BUILD

    book = make_book()
    monkeypatch.setattr(builder, "_unfinished", lambda c, b: ([], []))
    monkeypatch.setattr(builder, "sync_sheet", lambda *a, **k: {})
    monkeypatch.setattr(builder.Book, "open", classmethod(lambda cls, s: book))
    monkeypatch.setattr(builder.Ledger, "load",
                        staticmethod(lambda p: FakeLedger()))
    monkeypatch.setattr(builder, "build_one",
                        lambda *a, **k: builder.Build(index=a[4], ok=True,
                                                      status="ready"))
    builder._context.build = NO_BUILD

    builder.run(None, settings, count=1, workers=1)

    assert getattr(builder._context, "build", NO_BUILD) == NO_BUILD


def test_the_label_is_on_while_the_build_is_running(device, settings,
                                                     monkeypatch):
    """Clearing it is only right if it was ever set: a build's lines are
    exactly what the label is for."""
    from geelark_farm.logs import NO_BUILD

    book = make_book()
    seen = []
    monkeypatch.setattr(builder, "_unfinished", lambda c, b: ([], []))
    monkeypatch.setattr(builder, "sync_sheet", lambda *a, **k: {})
    monkeypatch.setattr(builder.Book, "open", classmethod(lambda cls, s: book))
    monkeypatch.setattr(builder.Ledger, "load",
                        staticmethod(lambda p: FakeLedger()))
    monkeypatch.setattr(builder, "build_one",
                        lambda *a, **k: seen.append(builder._context.build)
                        or builder.Build(index=a[4], ok=True, status="ready"))
    builder._context.build = NO_BUILD

    builder.run(None, settings, count=1, workers=1)

    assert seen == [1]


# ------------------------------ GeeLark having no machine free for a while
def test_a_capacity_refusal_is_named_rather_than_called_an_error(
        monkeypatch, tmp_path, make_settings):
    """It reached the catch-all and was recorded as `error`, which is a name
    the breaker counts - so a shortage at GeeLark, which costs a second and
    says nothing about us, was on its way to stopping the service
    (2026-08-28)."""
    from geelark_farm import builder, phones

    captured = {}

    def refuse(*a, **k):
        raise phones.PhoneCapacityError(
            "start failed [43043] High demand for Android 15 cloud phones.")

    monkeypatch.setattr(builder.phones, "ensure_running", refuse)
    monkeypatch.setattr(builder, "_write_row",
                        lambda *a, **k: captured.setdefault("row", a))

    # Both doors: `build_one` has named PhoneError since August, `finish_one`
    # never did, and this arrived through the second one.
    import inspect
    for name in ("build_one", "finish_one"):
        source = inspect.getsource(getattr(builder, name))
        assert "PhoneCapacityError" in source, f"{name} does not name it"
        assert source.index("PhoneCapacityError") < source.index(
            "except phones.PhoneError"), f"{name} catches the general case first"


def test_finishing_names_a_phone_that_will_not_boot_the_way_building_does():
    """The two paths had different vocabularies for the same failure: a
    phone that would not start was `phone_would_not_start` from a build and
    "an error nobody planned for" from a finish."""
    import inspect

    from geelark_farm import builder

    for name in ("build_one", "finish_one"):
        source = inspect.getsource(getattr(builder, name))
        assert "phone_would_not_start" in source, name
        assert "phone_is_gone" in source, name


# ------------------ a killed run leaving a row that says what is true of it
def test_the_gmail_reaches_the_row_the_moment_google_is_signed_in():
    """The column `settle_abandoned` reads to decide whether a phone a dead
    run left behind is finishable or is not a phone at all. It was written
    once, at the end, in a finally - so for the whole length of a build it was
    empty, and any interruption deleted a working phone (2026-08-28, phone
    1315: signed into Google, app installed, signed into ChatGPT, deleted by
    the next sync two minutes after a restart)."""
    import inspect

    from geelark_farm import builder

    source = inspect.getsource(builder.build_one)
    signed_in = source.index("gmail_signed_in = True")
    recorded = source.index("_note_on_row(book, build.serial, Gmail=")

    # Beside the line that makes it true, not somewhere after the loop.
    assert recorded - signed_in < 400


def test_a_note_on_a_row_is_written_by_serial_not_by_row_number():
    """`start` hands back a row number, and a sibling discarding its phone
    deletes a row and moves every row below it up - so that number can have
    come to mean a different phone by the time this runs."""
    import inspect

    from geelark_farm import builder

    source = inspect.getsource(builder._note_on_row)

    assert "book.phones.write(serial" in source
    assert "log_row" not in source


def test_a_row_that_cannot_be_written_does_not_end_the_run(caplog):
    """The build is what matters; this is only how it is remembered."""
    from geelark_farm import builder

    class Refuses:
        def write(self, serial, **fields):
            raise RuntimeError("the sheet quota is exhausted")

    builder._note_on_row(SimpleNamespace(phones=Refuses()), "1315",
                         Gmail="a@example.com")

    assert any("could not note" in r.getMessage() for r in caplog.records)


def test_a_phone_with_no_row_left_is_said_out_loud(caplog):
    """It has been discarded underneath this build, which is worth a line
    rather than a silent no-op."""
    from geelark_farm import builder

    class Gone:
        def write(self, serial, **fields):
            return False

    builder._note_on_row(SimpleNamespace(phones=Gone()), "1315",
                         Gmail="a@example.com")

    assert any("no row in the Phones tab" in r.getMessage()
               for r in caplog.records)


def test_only_the_field_it_was_given_is_written():
    """Status stays `building` - the run is not over - and Note is not
    trampled with a sentence about a build that is still going."""
    from geelark_farm import builder

    wrote = {}

    class Row:
        def write(self, serial, **fields):
            wrote.update(fields)
            return True

    builder._note_on_row(SimpleNamespace(phones=Row()), "1315",
                         Gmail="a@example.com")

    assert wrote == {"Gmail": "a@example.com"}
