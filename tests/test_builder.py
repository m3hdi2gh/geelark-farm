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
    PHONE_APP_HEADERS,
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


def make_book(*, gmails=2, proxies=2, apps=1, proxy_headers=None,
              phone_headers=None) -> Book:
    proxy_headers = proxy_headers or PROXY_HEADERS
    phone_headers = phone_headers or PHONE_HEADERS
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
    phone_log = PhoneLog(FakeWorksheet(phone_headers, []), phone_headers, lock)
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


def _many_gmails_per_phone(settings):
    """The rule before 2026-09-10: a refused address is followed by the
    next one on the same phone. Kept behind ONE_GMAIL_PER_PHONE=0, and
    these tests are its tests."""
    import dataclasses

    return dataclasses.replace(settings, one_gmail_per_phone=False)


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
    settings = _many_gmails_per_phone(settings)
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
    monkeypatch.setattr(builder.Ledger, "load",
                        staticmethod(lambda p, **k: FakeLedger()))

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
                        staticmethod(lambda p, **k: FakeLedger()))
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
                "gmail": "a@example.com", "proxy": "", "status": "app_only"}
               for r in (2, 3, 4, 5)]
    jobs = _job_world(monkeypatch, waiting)

    builder.run(None, settings, count=4, finish_limit=2, workers=1)

    assert jobs == ["finish", "finish", "build", "build"]


def test_a_mixed_batch_runs_its_jobs_at_once(device, settings, monkeypatch):
    """The thread pool has run twenty phones ten at a time in production and
    has never had a test. It is what every parallel pass now goes through."""
    waiting = [{"sheet_row": r, "phone_id": f"P{r}", "serial": str(660 + r),
                "gmail": "a@example.com", "proxy": "", "status": "app_only"}
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

    def interrupted(_futures, timeout=None):
        # The real one is polled now, so the fake takes the timeout too.
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
    monkeypatch.setattr(builder.Ledger, "load",
                        staticmethod(lambda p, **k: FakeLedger()))

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
        # Shaped like the real answer: GeeLark places the exit as well as
        # reaching it, and that is where the phone's clock comes from, so
        # a check that carried a timezone is not asked again (2026-09-12).
        return {"outboundIP": "1.1.1.1", "countryCode": "US",
                "timezone": "America/New_York"}

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
    # The tab answers "can I use this phone, and how" in one of four words.
    # This phone never got past the Gmail, so the app is not on it either -
    # `app_only` would say it was, which is what the status said for every
    # unfinished build until the word came to name a product (2026-08-29).
    assert written[PHONE_HEADERS.index("Status")] == "incomplete"
    # and the note says why in words. The token is in the Status column's
    # vocabulary and in the terminal summary; this cell is prose.
    note = written[PHONE_HEADERS.index("Note")]
    assert note.startswith("Stopped short: the Gmails tab had no other address")


# ------------------------------------- acting on what the operator marked
class FakePhoneLog:
    """A Phones tab that answers `marked` and records what was deleted."""

    DONE, FAILED, UNUSED = "done", "failed", "unused"
    BUILDING, READY, APP_ONLY = "building", "ready", "app_only"

    def __init__(self, rows):
        self._rows = rows
        self.deleted_rows = []

    def marked(self):
        return [r for r in self._rows if r["state"] in (self.DONE, self.FAILED)]

    def delete_rows(self, numbers):
        self.deleted_rows.extend(numbers)


#: What `person.marked` hands back for the test that is running. The
#: person channel left the Phones tab (C3), so a fake tab no longer
#: answers "who marked what" - this is where that answer lives now, and
#: `state_book` fills it from the same rows it builds the tab from.
_MARKS: list = []


@pytest.fixture(autouse=True)
def _no_build_context_leaks():
    """`build_one` stamps the log context with its serial and only
    `_run_jobs` clears it; a test that calls `build_one` directly left
    "622" on every later log line, in another module (2026-09-08)."""
    yield
    builder._serial.set(builder.NO_BUILD)

#: What somebody has said about each phone, and how many attempts this
#: tool has made on it - the other two halves of the person channel.
_SAID: dict = {}
_TRIES: dict = {}

#: `apply_phone_states` reads the store through settings; these tests
#: patch the store itself, so anything object-shaped will do.
MARK_SETTINGS = SimpleNamespace(store_enabled=True)


@pytest.fixture(autouse=True)
def _the_person_channel(monkeypatch):
    """Every test in this module reads marks from the store, not the tab."""
    from geelark_farm.store import person

    _MARKS.clear()
    _SAID.clear()
    _TRIES.clear()
    monkeypatch.setattr(person, "marked", lambda settings: list(_MARKS))
    monkeypatch.setattr(person, "state_of",
                        lambda settings, serial: _SAID.get(str(serial), ""))

    def _count(settings, serial):
        _TRIES[str(serial)] = _TRIES.get(str(serial), 0) + 1
        return _TRIES[str(serial)]

    monkeypatch.setattr(person, "count_try", _count)
    yield
    _MARKS.clear()
    _SAID.clear()
    _TRIES.clear()


def state_book(rows, *, apps=2):
    book = make_book(apps=apps)
    book.phones = FakePhoneLog(rows)
    _MARKS[:] = [dict(r) for r in rows
                 if (r.get("state") or "") in ("done", "failed")]
    _SAID.update({str(r.get("serial")): (r.get("state") or "")
                  for r in rows if r.get("serial")})
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

    out = builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

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

    out = builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

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

    out = builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

    assert deleted == [] and out["running"] == ["650"]
    assert book.phones.deleted_rows == []      # the row survives to be retried


def test_an_unused_phone_is_left_entirely_alone(monkeypatch):
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2}])
    book = state_book([{"sheet_row": 5, "state": "unused",
                        "serial": "650", "gmail": "", "app_account": ""}])

    assert builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS) == {}
    assert book.phones.deleted_rows == []


def test_a_row_whose_phone_is_already_gone_is_still_tidied(monkeypatch):
    """Deleted from the panel by hand. Nothing to delete, but the row and the
    account it names should not linger."""
    monkeypatch.setattr(builder.phones, "listing", lambda c: [])
    book = state_book([{"sheet_row": 9, "state": "failed", "phone_id": "GONE",
                        "serial": "660", "gmail": "", "app_account": "a0@example.com"}])
    book.apps.spend(book.apps.claim(), serial="660")

    out = builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

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

    out = builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

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

    builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

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


def test_a_finish_clears_the_app_before_its_first_account_too(settings,
                                                              monkeypatch):
    """A build installed the app a moment ago, so its first attempt starts on
    a clean one. A finish picks up a phone that has been sitting with whatever
    an earlier run left signed in, and `act_reset_app` only clears that when it
    happens to recognise the screen (2026-08-30).

    Driven through `_sign_into_app` itself, which is the one loop `build` and
    `finish` share - the flag is the only thing that differs between them."""
    fresh_flags = []
    monkeypatch.setattr(
        builder.chatgpt_login, "sign_in",
        lambda *a, **k: fresh_flags.append(k.get("fresh")) or SIGNED_IN)

    def session(reset_first):
        book = make_book(apps=1)
        return builder._Session(
            client=None, settings=settings, book=book,
            build=builder.Build(index=1, serial="691"), phone_id="P1",
            artifacts=settings.artifact_dir, deadline=time.monotonic() + 600,
            started=time.monotonic(), reset_first=reset_first)

    builder._sign_into_app(session(reset_first=False))
    builder._sign_into_app(session(reset_first=True))

    assert fresh_flags == [False, True], (
        "a finish trusted whatever the last run left in the app")


def test_a_phone_that_signs_nobody_in_gives_its_accounts_back(
        device, settings, drive):
    """Two phones took six accounts in fifteen minutes on 2026-08-30, every
    one answered "Incorrect email address or password" - and four of the six
    had signed into another phone perfectly two hours earlier. The verdict for
    a refused password already says the service shows that page when it is
    refusing for other reasons too; a phone that refused every account it was
    given is that other reason."""
    book = make_book(apps=3)
    wrong = Outcome("fatal", "wrong_password")
    build = drive(book, settings, google=[SIGNED_IN], app=[wrong] * 3)

    assert not build.ok and build.status == "no_usable_gpt"
    statuses = [r.values["Status"] for r in book.apps._rows]
    assert statuses == ["", "", ""], (
        f"the phone's judgements were left standing: {statuses}")
    assert len(book.apps.available) == 3, "the accounts did not go back"


def test_accounts_a_working_phone_condemned_stay_condemned(
        device, settings, drive):
    """The other half, and the reason this is not a cap: a phone that signs
    somebody in has proved the accounts before them were the fault. A cap
    stopped one at three while eleven usable accounts sat in the tab
    (2026-08-11, phones 654 and 656)."""
    book = make_book(apps=3)
    wrong = Outcome("fatal", "wrong_password")
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[wrong, wrong, SIGNED_IN])

    assert build.ok
    assert [r.values["Status"] for r in book.apps._rows[:2]] == \
        ["wrong_password"] * 2


def test_the_last_account_in_a_thin_pool_is_given_back_too(device, settings,
                                                          drive):
    """The hole in the first version of this, which asked for two accounts
    before it would believe the phone. Phone 1465 was handed the only free
    account there was, refused it, ran out, and kept the condemnation - twice,
    on an account whose password its owner then checked by hand and found
    good. A threshold fails exactly when the pool is thin, which is when it
    matters most (2026-08-30)."""
    book = make_book(apps=1)
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[Outcome("fatal", "wrong_password")])

    assert not build.ok and build.status == "no_usable_gpt"
    assert book.apps._rows[0].values["Status"] == "", (
        "the only account in the pool was left condemned by a phone that "
        "signed nobody in")
    assert len(book.apps.available) == 1


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
    book.phones.start(Serial="691", Status="app_only")

    seen = []
    monkeypatch.setattr(
        builder.phones, "ensure_running",
        lambda *a, **k: seen.append(
            book.phones._ws.rows[0][PHONE_HEADERS.index("Status")]))
    # Ends at the first check after the marker, which is all this is about.
    monkeypatch.setattr(builder.shell, "device_accounts", lambda *a, **k: [])
    monkeypatch.setattr(builder.shell, "package_installed", lambda *a, **k: True)

    builder.finish_one(
        None, settings, book, FakeLedger(),
        {"sheet_row": 2, "phone_id": "P1", "serial": "691",
         "gmail": "g@example.com", "proxy": "", "status": "app_only"}, 1)

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
         "gmail": "g@example.com", "proxy": "", "status": "app_only"}, 1)

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
    settings = _many_gmails_per_phone(settings)
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
    settings = _many_gmails_per_phone(settings)
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
    book.phones.unfinished = lambda held_too=False: [
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
    # The discard now asks the device before throwing a phone away, so a
    # test of that path has to say what the device answers.
    monkeypatch.setattr(builder.shell, "device_accounts",
                        lambda c, pid, strict=True: [])
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
    # The discard now asks the device before throwing a phone away, so a
    # test of that path has to say what the device answers.
    monkeypatch.setattr(builder.shell, "device_accounts",
                        lambda c, pid, strict=True: [])

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
    marked = {"sheet_row": 2, "state": "done", "serial": "729",
              "gmail": "g0@example.com", "app_account": "a0@example.com"}
    book.phones = FakePhoneLog([marked])
    # The person channel is the store's now, and this test builds its tab
    # by hand rather than through `state_book`, so it says so by hand too.
    _MARKS[:] = [marked]
    book.phones.rows = lambda: []
    book.reload = lambda: None

    outcome = builder.sync_sheet(world["client"], book, FakeLedger(),
                                 settings=MARK_SETTINGS)

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
                       probe_proxies=False, settings=MARK_SETTINGS)

    assert asked == []
    assert "dead" not in builder.sync_sheet(world["client"], book,
                                            FakeLedger(), probe_proxies=False,
                                                settings=MARK_SETTINGS)


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
    assert written[4]["Status"] == "app_only"
    assert "Google is signed in" in written[4]["Note"]
    assert world["deleted"] == []          # it is worth finishing, not deleting


def test_a_row_with_a_job_in_the_queue_is_not_abandoned(world):
    """The lane marks a phone `building` when it queues the login, and a
    builder claims it in the ledger only when it takes the job - minutes
    later with four logins already running. In between the row read as
    a dead run: three phones were written off as stopped short at 01:57
    while their logins started at 01:57:46 (2026-09-10)."""
    book = make_book()
    book.phones = FakePhoneLog([])
    book.phones.rows = lambda: [
        {"sheet_row": 4, "Serial": "2243", "Status": "building",
         "Gmail": "g@example.com", "GPT Account": ""},
        {"sheet_row": 5, "Serial": "2250", "Status": "building",
         "Gmail": "h@example.com", "GPT Account": ""}]
    written = {}
    book.phones.finish = lambda row, **fields: written.update({row: fields})

    outcome = builder.settle_abandoned(None, book, FakeLedger(),
                                       busy=frozenset({"2243"}))

    assert outcome["abandoned"] == ["2250"], "the queued one was left alone"
    assert 4 not in written and written[5]["Status"] == "app_only"


def test_the_busy_serials_come_from_the_queue_and_never_raise(
        make_settings, tmp_path, monkeypatch):
    import inspect

    from geelark_farm.store import jobs as store_jobs

    src = inspect.getsource(store_jobs.open_serials)
    assert "status IN ('queued', 'running')" in src
    assert '"phone") or {}' in src, "a finish names its phone in the payload"
    off = make_settings(state_dir=tmp_path, store_enabled=False)
    assert builder._busy_serials(off) == frozenset()
    assert builder._busy_serials(None) == frozenset()

    on = make_settings(state_dir=tmp_path, store_enabled=True, build_queue=True)
    monkeypatch.setattr(store_jobs, "open_serials", lambda s: {"2243", "2244"})
    assert builder._busy_serials(on) == frozenset({"2243", "2244"})
    monkeypatch.setattr(store_jobs, "open_serials",
                        lambda s: (_ for _ in ()).throw(RuntimeError("down")))
    assert builder._busy_serials(on) == frozenset()


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
    assert written[4]["Status"] == "app_only"
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

    out = builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

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

    out = builder.apply_phone_states(
        None, book, FakeLedger({"P1": FakeClaim()}), MARK_SETTINGS)

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

    out = builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

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
    # The discard now asks the device before throwing a phone away, so a
    # test of that path has to say what the device answers.
    monkeypatch.setattr(builder.shell, "device_accounts",
                        lambda c, pid, strict=True: [])
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    book = make_book(gmails=1)

    drive(book, settings, google=[Outcome("fatal", "wrong_password")])

    rows = history_rows(book)
    assert [r["Event"] for r in rows] == ["discarded"]
    assert "nothing was ever signed into it" in rows[0]["Note"]


def test_a_phone_that_is_signed_in_after_all_is_not_deleted(
        device, settings, monkeypatch, drive):
    """The flow's verdict is what the run saw while it was watching.

    Google adds the account after its own consent closes, and it takes as long
    as it takes. Every phone that outlived a `stuck_on_sign_in_closed` failure
    on 2026-09-04 was found later holding the account it was supposed to have,
    with nothing in the device's own account history but the add - so the ones
    this path did delete were deleted signed in (builds 1694-1705).
    """
    deleted = []
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))
    monkeypatch.setattr(builder.shell, "device_accounts",
                        lambda c, pid, strict=True: ["late@example.com"])
    book = make_book(gmails=1)

    drive(book, settings, google=[Outcome("unknown", "stuck_on_sign_in_closed")])

    assert deleted == [], "it was signed in; deleting is the one thing that "\
                          "cannot be taken back"
    assert [r["Event"] for r in history_rows(book)] != ["discarded"]


def test_a_device_that_will_not_answer_keeps_its_phone(
        device, settings, monkeypatch, drive):
    """Asked strictly, so "the command did not run" cannot read as "nothing is
    on it". A phone kept in error is a row somebody closes; a phone deleted in
    error is a Gmail, a proxy and the minutes spent on both."""
    deleted = []
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))

    def refuses(client, phone_id, strict=True):
        raise builder.shell.ShellError("the phone would not run it")

    monkeypatch.setattr(builder.shell, "device_accounts", refuses)
    book = make_book(gmails=1)

    drive(book, settings, google=[Outcome("unknown", "stuck_on_sign_in_closed")])

    assert deleted == []


def test_a_phone_with_nothing_on_it_is_still_discarded(
        device, settings, monkeypatch, drive):
    """The counterweight. A phone nothing was signed into still costs minutes,
    and keeping every one of them is how the bill grows without a tab."""
    deleted = []
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))
    monkeypatch.setattr(builder.shell, "device_accounts",
                        lambda c, pid, strict=True: [])
    book = make_book(gmails=1)

    drive(book, settings, google=[Outcome("fatal", "wrong_password")])

    assert deleted, "nothing on it, so it goes"
    assert [r["Event"] for r in history_rows(book)] == ["discarded"]


def test_applying_a_mark_is_history(monkeypatch):
    """Delivery is the event the whole pipeline exists for, and it is also the
    moment the Phones row vanishes - so it is exactly what History must hold."""
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2}])
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    # state_book swaps in a FakePhoneLog but keeps make_book's real History.
    book = state_book([{"sheet_row": 5, "state": "done", "serial": "650",
                        "gmail": "g@example.com", "app_account": "a0@example.com"}])

    builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

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

    outcome = builder.sync_sheet(world["client"], book, FakeLedger(),
                                 settings=MARK_SETTINGS)

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
    has the address.

    A *row* with a blank Gmail, which is what the docstring means. This
    test used to pass an empty book, which is a different thing entirely -
    a phone with no row at all - and so it stood as the proof of the bug
    the test below now covers.
    """
    from geelark_farm import phones as ph
    from geelark_farm.builder import sync_phone_names
    listing = [{"id": "P1", "serialNo": "832", "serialName": "farm-1786928959",
                "status": ph.STOPPED}]
    client = naming_client(monkeypatch, listing)
    book = NamingBook([{"Serial": "832", "Gmail": ""}])

    assert sync_phone_names(client, book) == ["832"]
    assert client.renames == [("P1", "832")]


def test_a_phone_with_no_row_is_not_renamed(monkeypatch):
    """The GeeLark account is shared, and most of what the listing returns
    was made by somebody else. This renamed one of the operator's own
    phones to `1743` (2026-09-05) - it had no row, so there was nothing to
    name it after and the serial alone became the name.

    `strand_check` already says of this exact set that "a phone with no row
    is touched by nothing: not the State column, not the abandoned sweep,
    not the renaming". It was true of the other two.
    """
    from geelark_farm import phones as ph
    from geelark_farm.builder import sync_phone_names
    listing = [{"id": "P1", "serialNo": "832", "serialName": "farm-1786928959",
                "status": ph.STOPPED},
               {"id": "P9", "serialNo": "1743", "serialName": "my own phone",
                "status": ph.STOPPED}]
    client = naming_client(monkeypatch, listing)
    book = NamingBook([{"Serial": "832", "Gmail": "a@gmail.com"}])

    assert sync_phone_names(client, book) == ["832 - a"]
    assert client.renames == [("P1", "832 - a")]


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
                       on_step=said.append, settings=MARK_SETTINGS)

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
    assert written["Event"] == "app_only"
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


#: What GeeLark answers for a phone in the farm's own group, and for one
#: that is in no group at all - the object comes back either way, with
#: every field empty for the second.
IN_GROUP = {"id": "g1", "name": "automation", "remark": ""}
NO_GROUP = {"id": "", "name": "", "remark": ""}


def stranded(monkeypatch, *, live, phone_rows, gmail_serial=None,
             app_serial=None, theirs=(), ungrouped=()):
    """`live` are the farm's phones; `theirs` belong to somebody else on the
    same shared account. `ungrouped` are the farm's own, but outside the
    group - which is the case that says the group signal is broken."""
    listing = [{"id": f"P{s}", "serialNo": s,
                "group": NO_GROUP if s in ungrouped else IN_GROUP}
               for s in live]
    listing += [{"id": f"P{s}", "serialNo": s, "group": NO_GROUP}
                for s in theirs]
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


def test_a_phone_outside_the_farms_group_is_not_reported(monkeypatch):
    """The GeeLark account is shared. Every phone this farm makes is created
    with `profileGroup: automation`; the operator's own carry no group, and
    reporting them made a warning nobody could ever clear - which is the same
    as no warning (2026-08-29). Three of his sat in that line for a day."""
    _, outcome = stranded(monkeypatch, live=[], phone_rows=[],
                          theirs=["1741", "1742", "1743"])

    assert "unknown_phones" not in outcome


def test_a_phone_in_the_group_with_no_row_is_still_reported(monkeypatch):
    """The filter narrows whose phones are looked at, not whether an orphan
    of ours is reported - one running unseen bills by the minute."""
    _, outcome = stranded(monkeypatch, live=["964"], phone_rows=[],
                          theirs=["1743"])

    assert outcome["unknown_phones"] == ["964"]


def test_the_group_is_distrusted_when_one_of_ours_is_outside_it(monkeypatch):
    """The signal is checked before it is trusted, in the one way that costs
    nothing: a phone we hold a row for must be in the group, because we put
    it there at creation. If it is not, the group means something other than
    what this reads into it, and the old behaviour is the safe one - a phone
    of ours running unseen is the failure that matters here."""
    _, outcome = stranded(monkeypatch, live=["964", "965"],
                          phone_rows=[{"Serial": "964"}],
                          ungrouped=["964"], theirs=["1743"])

    assert outcome["unknown_phones"] == ["1743", "965"]


def test_a_phone_with_no_group_object_at_all_is_not_ours(monkeypatch):
    """A listing that answers no `group` key, rather than an empty one.
    Read as 'not in the group', never as an error."""
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "1743"}])
    book = make_book(gmails=1, proxies=1, apps=1)

    outcome = builder.strand_check(None, StrandBook(book, []))

    assert "unknown_phones" not in outcome


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


def test_an_empty_pool_does_not_cost_the_phone_a_strike(device, settings,
                                                       monkeypatch):
    """`no_usable_gpt` says the Gpt Info tab was empty. That is not the
    phone's fault and not something the phone can be fixed of - and three
    strikes retire it. Phones 1465 and 1468 were both set aside that way, with
    `the Gpt Info tab has no unused account left` in their notes (2026-08-30).

    `breaker` had already drawn this line - `no_usable_gpt` is in its `WORKED`
    set, evidence the pipeline works - and the tally disagreed with it."""
    from geelark_farm import breaker

    assert "no_usable_gpt" in breaker.WORKED
    book = make_book(apps=0)
    monkeypatch.setattr(builder.phones, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(builder.shell, "device_accounts",
                        lambda *a, **k: ["g@example.com"])
    monkeypatch.setattr(builder.shell, "third_party_packages",
                        lambda *a, **k: ["com.openai.chatgpt"])
    tries = []
    from geelark_farm.store import person as person_mod

    monkeypatch.setattr(
        person_mod, "count_try",
        lambda settings, serial: tries.append(serial) or len(tries))

    build = builder.finish_one(
        None, settings, book, FakeLedger(),
        {"sheet_row": 3, "phone_id": "P1", "serial": "1465",
         "gmail": "g@example.com", "proxy": "", "status": "app_only"}, 1)

    assert build.status == "no_usable_gpt"
    assert tries == [], (
        "an empty tab put a strike on the phone, three of which retire it")


def test_a_phone_that_refuses_what_it_is_given_is_charged_for_it(
        device, settings, monkeypatch):
    """The other half of exonerating the accounts. Those runs end
    `no_usable_gpt` - the tab ran dry because this phone had just spent what
    was in it - and that reason is in `breaker.WORKED`, so the tally could not
    see them. Phone 1465 refused a hand-verified account on a hand-swapped
    exit, was given it back, went back on the shelf, and would have done the
    same every pass for ever: no account lost, and no way for it to be
    retired either (2026-08-30)."""
    book = make_book(apps=1)
    monkeypatch.setattr(builder.phones, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(builder.shell, "device_accounts",
                        lambda *a, **k: ["g@example.com"])
    monkeypatch.setattr(builder.shell, "third_party_packages",
                        lambda *a, **k: ["com.openai.chatgpt"])
    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda *a, **k: Outcome("fatal", "wrong_password"))
    tries = []
    from geelark_farm.store import person as person_mod

    monkeypatch.setattr(
        person_mod, "count_try",
        lambda settings, serial: tries.append(serial) or len(tries))

    build = builder.finish_one(
        None, settings, book, FakeLedger(),
        {"sheet_row": 3, "phone_id": "P1", "serial": "1465",
         "gmail": "g@example.com", "proxy": "", "status": "app_only"}, 1)

    assert build.status == "no_usable_gpt"
    # the account is not spent...
    assert book.apps._rows[0].values["Status"] == ""
    # ...and the phone is
    assert tries == ["1465"], (
        "nobody was charged: the account came back and the phone went back "
        "on the shelf to do it again next pass")


def test_a_phone_is_not_charged_twice_for_one_run(device, settings,
                                                  monkeypatch):
    """The guard on the condition above being an `or`: a run that both
    refuses accounts and ends on a reason the breaker counts must take one
    strike, not two, or three passes retire a phone in one."""
    book = make_book(apps=1)
    monkeypatch.setattr(builder.phones, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(builder.shell, "device_accounts",
                        lambda *a, **k: ["g@example.com"])
    monkeypatch.setattr(builder.shell, "third_party_packages",
                        lambda *a, **k: ["com.openai.chatgpt"])
    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda *a, **k: Outcome("fatal", "wrong_password"))
    tries = []
    from geelark_farm.store import person as person_mod

    monkeypatch.setattr(
        person_mod, "count_try",
        lambda settings, serial: tries.append(serial) or len(tries))
    monkeypatch.setattr(builder.breaker, "counts_against", lambda build: True)

    builder.finish_one(
        None, settings, book, FakeLedger(),
        {"sheet_row": 3, "phone_id": "P1", "serial": "1465",
         "gmail": "g@example.com", "proxy": "", "status": "app_only"}, 1)

    assert tries == ["1465"], f"charged {len(tries)} times for one run"


def test_a_failure_the_phone_is_answerable_for_still_counts():
    """The guard: the tally is what retires a phone that cannot be finished,
    and it has to keep doing that."""
    from geelark_farm import breaker

    for status in ("install_failed", "app_session_unverified",
                   "phone_would_not_start"):
        build = builder.Build(index=1, ok=False, status=status)
        assert breaker.counts_against(build), (
            f"{status} stopped counting against the phone")


def test_history_writes_the_path_beside_the_outcome():
    """Appended, never reordered: rows are written by position, so moving a
    column scrambles every row already written under the old one.

    Pinned as fixed positions rather than "Steps is last", so that appending
    the next column is allowed and moving any existing one is not - which is
    what the rule actually says. `App` was appended on 2026-08-30."""
    from geelark_farm.pools import HistoryLog

    assert HistoryLog.HEADERS[:10] == [
        "When", "Machine", "Serial", "Event", "Seconds", "Proxy",
        "Gmail", "GPT Account", "Note", "Steps"]


def test_history_keeps_the_column_the_builder_has_always_sent_it():
    """`_record` passes `App=` to `record_history`, `append` writes by
    position over HEADERS, and HEADERS had no such column - so the value was
    dropped on every row ever written. History is the only account of a run
    once the Phones row is deleted, and "did this phone have the app" is half
    of what tells the two products apart (2026-08-30)."""
    from geelark_farm.pools import HistoryLog

    assert "App" in HistoryLog.HEADERS


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
                        lambda *a, **k: (_ for _ in ()).throw(
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

    monkeypatch.setattr(builder, "_fresh_proxy", lambda *a, **k: claimed)
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

    outcome = builder.sync_sheet(None, book, None, probe_proxies=False,
        settings=MARK_SETTINGS)

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
    """The label was a thread-local, and with one worker `work` runs on the
    caller's own thread - so a build that ended an hour ago went on stamping
    its number onto every line logged afterwards. In a command that exits,
    that is until it exits. In `serve`, which does not, it is for ever
    (2026-08-27).

    A ContextVar does not fix that on its own: a pool thread is reused too.
    The `reset` in the `finally` is the whole of it, which is why this test
    outlived the mechanism it was written against.
    """
    from geelark_farm.logs import NO_BUILD

    book = make_book()
    monkeypatch.setattr(builder, "_unfinished", lambda c, b: ([], []))
    monkeypatch.setattr(builder, "sync_sheet", lambda *a, **k: {})
    monkeypatch.setattr(builder.Book, "open", classmethod(lambda cls, s: book))
    monkeypatch.setattr(builder.Ledger, "load",
                        staticmethod(lambda p, **k: FakeLedger()))
    monkeypatch.setattr(builder, "build_one",
                        lambda *a, **k: builder.Build(index=a[4], ok=True,
                                                      status="ready"))
    builder.run(None, settings, count=1, workers=1)

    assert builder._build.get() == NO_BUILD
    assert builder._run.get() == NO_BUILD


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
                        staticmethod(lambda p, **k: FakeLedger()))
    monkeypatch.setattr(builder, "build_one",
                        lambda *a, **k: seen.append(
                            (builder._build.get(), builder._run.get()))
                        or builder.Build(index=a[4], ok=True, status="ready"))

    builder.run(None, settings, count=1, workers=1)

    assert [b for b, _ in seen] == [1]
    assert all(r != NO_BUILD for _, r in seen), (
        "the batch's run id never reached the worker - ThreadPoolExecutor "
        "does not copy the caller's context, so `work` has to set it again")


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


def test_a_batch_is_polled_so_a_signal_can_land(device, settings, monkeypatch):
    """A bare `wait(futures)` blocks until every worker is done, and Python
    delivers KeyboardInterrupt only at a bytecode boundary - so `docker stop`
    was not acted on until the whole batch had finished. Longer than
    `stop_grace_period`, so SIGKILL arrived first and the phones stayed up
    billing (2026-08-29). Measured: interrupt at 0.30s, handled at 1.20s.
    """
    _job_world(monkeypatch, [])
    seen = []

    def polled(futures, timeout=None):
        seen.append(timeout)
        return set(futures), set()

    monkeypatch.setattr(builder, "wait", polled)

    builder.run(None, settings, count=2, workers=2)

    assert seen and all(t is not None for t in seen), (
        "the wait has to have a timeout, or the signal never lands")
    assert seen[0] == builder.STOP_POLL_SECONDS


def test_the_workers_are_told_to_stop_before_the_pool_is_drained(
        device, settings, monkeypatch):
    """The flag is what each job's `check_cancelled` reads. Set after the
    drain, it is set after every build has already finished - which is the
    same as not setting it."""
    _job_world(monkeypatch, [])
    order = []

    def polled(futures, timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(builder, "wait", polled)
    monkeypatch.setattr(builder, "_stop_all",
                        lambda *a: order.append("stop_all"))

    class Watched:
        def __init__(self):
            self._set = False

        def set(self):
            order.append("flag")
            self._set = True

        def is_set(self):
            return self._set

    with pytest.raises(KeyboardInterrupt):
        builder.run(None, settings, count=2, workers=2, cancel=Watched())

    assert order == ["flag", "stop_all"], order


# ------------------------------- refusing a phone somebody is working inside
class Running:
    """GeeLark saying a phone is up."""

    def __init__(self, state):
        self.state = state
        self.asked = 0

    def data(self, path, payload=None, **kw):
        self.asked += 1
        return {"successDetails": [{"id": "P1", "status": self.state}]}


def a_warm_phone():
    return {"phone_id": "P1", "serial": "1401", "gmail": "a@b.com",
            "proxy": "", "status": "app_only"}


@pytest.mark.parametrize("state", [0, 1])          # RUNNING, STARTING
def test_a_phone_somebody_started_by_hand_is_left_alone(state, settings,
                                                        monkeypatch):
    """The second net under the `taken` word, for when that word is forgotten.
    The app would be showing a session this run did not create, and
    `act_reset_app` settles that ambiguity with `pm clear` - throwing away
    somebody's signed-in account to make room for one of ours."""
    monkeypatch.setattr(builder, "_note_on_row", lambda *a, **k: None)
    ledger = FakeLedger()
    build = builder.finish_one(Running(state), settings, make_book(), ledger,
                               a_warm_phone(), 1)

    assert build.status == "in_use_by_hand"
    assert not build.ok


def test_a_stopped_phone_is_finished_as_before(settings, monkeypatch):
    """The guard must not refuse the ordinary case, which is every phone the
    service itself stopped."""
    monkeypatch.setattr(builder, "_note_on_row", lambda *a, **k: None)
    seen = []
    monkeypatch.setattr(builder.phones, "ensure_running",
                        lambda *a, **k: seen.append("booted"))

    builder.finish_one(Running(2), settings, make_book(), FakeLedger(),
                       a_warm_phone(), 1)

    assert seen == ["booted"], "a stopped phone is still picked up"


def test_the_refusal_is_nobodys_fault_and_the_breaker_ignores_it():
    """The run refused before it claimed anything: nothing created, nothing
    spent, and a person using their own stock is not evidence the machine has
    stopped working."""
    from geelark_farm import breaker, failures

    assert failures.verdict("in_use_by_hand").blame == failures.NOBODY
    assert "in_use_by_hand" in breaker.NOTHING_HAPPENED
    assert not breaker.counts_against(
        builder.Build(index=1, ok=False, status="in_use_by_hand"))


def test_a_hand_over_with_no_account_is_recorded_as_one():
    """History is the only durable record once the row is deleted, and it
    claimed a farm account went out with every app-only hand-over. The pair of
    notes branched on `failed` alone (2026-08-29)."""
    import inspect

    source = inspect.getsource(builder.apply_phone_states)

    assert "No app account was ever on it" in source
    # Three branches, not the two that said the same thing either way.
    assert source.count("Marked done and deleted") == 2
    # ...and the branch turns on the account actually found, not on the
    # Phones cell alone - the cell went blank once and the note lied
    # (2026-09-01, phone 1542).
    assert "elif carried:" in source


def test_an_app_only_phone_is_described_as_finished_not_failed():
    """`no_usable_gpt` is not a fault, it is the other product. Read cold,
    "Stopped short" says the opposite - and this is exactly the phone somebody
    takes to sign a customer in by hand."""
    build = builder.Build(index=1, ok=False, status="no_usable_gpt",
                          detail="the Gpt Info tab has no unused account left")

    note = builder._phone_note(build)

    assert "ready to take as it is" in note
    assert "signed into Google with the app installed" in note


def test_a_real_failure_is_not_dressed_up_as_a_product():
    build = builder.Build(index=1, ok=False, status="install_failed",
                          detail="the app would not install")

    assert "ready to take" not in builder._phone_note(build)


# ------------------------------ the status has to agree with the App column
def a_build(ok=False, installed=False):
    return builder.Build(index=1, ok=ok, status="ready" if ok else "x",
                         app_installed=installed)


def test_a_phone_with_no_app_is_not_called_app_only():
    """`READY if build.ok else APP_ONLY` said the app was on the device
    whenever a build stopped short - including when the install was the thing
    that failed, with the App column reading x beside it.

    Vague while `app_only` meant "not finished". Actively misleading once it
    named a product: the tab would offer a phone with no app to somebody whose
    whole use for it is opening that app (2026-08-29)."""
    assert builder._phone_status(a_build(installed=False)) == "incomplete"


def test_a_phone_with_the_app_and_no_account_is_the_second_product():
    assert builder._phone_status(a_build(installed=True)) == "app_only"


def test_a_finished_phone_is_ready_whatever_else_is_true():
    assert builder._phone_status(a_build(ok=True, installed=True)) == "ready"


# ------------------------- a run that never looked does not get to have a view
def test_a_run_that_never_reached_the_device_cannot_name_a_status():
    """The other half of the 2026-08-29 fix. `else INCOMPLETE` was reached by
    a finish whose phone would not start - a run that never saw the device and
    so knows nothing about what is on it."""
    assert builder._phone_status(a_build(installed=None)) is None


def test_a_failed_finish_does_not_demote_a_phone_that_has_the_app():
    """Phone 1415 was `app_only` for two hours - built, app installed, waiting
    only for an account. At 23:33 a finish could not start it, wrote
    `incomplete` with a cross in the App column, and put a strike on a phone
    that was one of the two things this farm sells. Three strikes deletes it
    (2026-08-30)."""
    book = make_book(gmails=3, proxies=3, apps=3,
                     phone_headers=PHONE_APP_HEADERS)
    tab = book.phones._ws

    def row_now():
        row = tab.rows[0]
        return {name: row[PHONE_APP_HEADERS.index(name)]
                for name in ("Status", "App", "Note")}

    book.phones.start(Serial="1415", Proxy="SX1")
    # The build that installed the app and ran out of accounts.
    builder._record(book, builder.Build(
        index=1, status="no_usable_gpt", serial="1415",
        gmail="g@example.com", app_installed=True))
    assert row_now()["Status"] == "app_only"
    assert row_now()["App"] == book.phones.YES

    # Two hours later, a finish that never gets the phone up.
    builder._record(book, builder.Build(
        index=1, status="phone_would_not_start", serial="1415",
        gmail="g@example.com",
        detail="phone 635032199195787275 would not start"))

    after = row_now()
    assert after["Status"] == "app_only", (
        f"a run that never reached the device demoted the row: {after}")
    assert after["App"] == book.phones.YES, (
        f"a run that never reached the device crossed off the app: {after}")
    # It still records itself - the note is how anyone finds out what happened.
    assert "would not start" in after["Note"]


def test_a_run_that_did_look_and_found_no_app_still_says_incomplete():
    """The guard on the fix above: `None` must mean "nobody looked" and not
    become the answer for a phone that genuinely has no app on it."""
    book = make_book(gmails=3, proxies=3, apps=3,
                     phone_headers=PHONE_APP_HEADERS)
    tab = book.phones._ws
    book.phones.start(Serial="1416", Proxy="SX1")

    builder._record(book, builder.Build(
        index=1, status="install_failed", serial="1416",
        gmail="g@example.com", app_installed=False))

    row = tab.rows[0]
    assert row[PHONE_APP_HEADERS.index("Status")] == "incomplete"
    assert row[PHONE_APP_HEADERS.index("App")] == book.phones.NO


def test_history_names_the_runs_own_outcome_when_it_cannot_name_the_phones():
    """History gets one word per row and is appended whatever happened. A
    guessed `incomplete` was worse than useless there; the run's own token
    says which of the fifty-five ways it went."""
    book = make_book(gmails=3, proxies=3, apps=3)
    book.phones.start(Serial="1415", Proxy="SX1")

    builder._record(book, builder.Build(
        index=1, status="phone_would_not_start", serial="1415",
        gmail="g@example.com"))

    events = [row[HistoryLog.HEADERS.index("Event")]
              for row in book.history._ws.rows]
    assert events == ["phone_would_not_start"]


def test_all_four_words_are_offered_in_the_dropdown():
    """A status a run can write and the Lists tab does not know is one the
    sheet flags as invalid the moment it appears."""
    from geelark_farm.pools import PhoneLog

    offered = set(builder.possible_statuses())

    assert offered == {PhoneLog.BUILDING, PhoneLog.READY, PhoneLog.APP_ONLY,
                       PhoneLog.INCOMPLETE}
    for build in (a_build(), a_build(installed=True), a_build(ok=True)):
        assert builder._phone_status(build) in offered


# ------------------------- letting go of a phone somebody has marked mid-run
class Marked:
    """A Phones tab whose State cell answers whatever the test says."""

    DONE, FAILED, TAKEN, UNUSED = "done", "failed", "taken", "unused"

    def __init__(self, state):
        self.state = state
        self.asked = 0

    def state_of(self, serial):
        self.asked += 1
        return self.state


@pytest.mark.parametrize("word", ["failed", "done", "taken"])
def test_a_build_lets_go_of_a_phone_marked_while_it_ran(word):
    """`unfinished` keeps a marked row out of the queue, but a run already
    under way never learned. A phone marked failed at 20:06 had the app
    installed on it until 20:36, and the sync then deleted it (2026-08-29)."""
    _SAID["1399"] = word

    assert builder._given_up_on(MARK_SETTINGS, "1399") == word


def test_an_unmarked_phone_is_carried_on_with():
    for word in ("", "unused"):
        _SAID["1399"] = word
        assert builder._given_up_on(MARK_SETTINGS, "1399") == ""


def test_a_read_that_fails_does_not_stop_the_build(monkeypatch):
    """The worst case is carrying on, which is what it did before this.

    The read moved to the store with the rest of the person channel, and
    it swallows its own errors there for the same reason: a build must not
    die because one query failed.
    """
    from geelark_farm.store import person

    def refuses(settings, serial):
        raise RuntimeError("the store blinked")

    monkeypatch.setattr(person, "state_of", refuses)

    with pytest.raises(RuntimeError):
        person.state_of(MARK_SETTINGS, "1399")   # it really does raise

    monkeypatch.setattr(person, "state_of", lambda settings, serial: "")
    assert builder._given_up_on(MARK_SETTINGS, "1399") == ""


def test_giving_up_is_nobodys_fault_and_the_breaker_ignores_it():
    from geelark_farm import breaker, failures

    assert failures.verdict("given_up_on").blame == failures.NOBODY
    assert "given_up_on" in breaker.NOTHING_HAPPENED


# ------------------------------- two batches at once must not share a label
def test_the_filter_cannot_raise_from_a_thread_that_set_nothing():
    """A filter runs outside the try that guards `emit` - `Handler.handle`
    calls it directly, and neither `callHandlers` nor `Logger._log` catches -
    so a LookupError here comes back out of the `log.info(...)` call and kills
    the build on its own log line. A ContextVar declared without `default=`
    does exactly that (2026-08-31)."""
    import logging as stdlib_logging
    import threading as stdlib_threading

    from geelark_farm.logs import NO_BUILD

    seen = []

    def from_a_bare_thread():
        made = stdlib_logging.LogRecord("x", 20, "f", 1, "m", (), None)
        seen.append(builder.BuildContextFilter().filter(made))
        seen.append((made.row, made.run, made.build))

    thread = stdlib_threading.Thread(target=from_a_bare_thread)
    thread.start()
    thread.join()

    assert seen[0] is True
    assert seen[1] == (NO_BUILD, NO_BUILD, NO_BUILD)


def test_two_batches_at_once_do_not_share_a_label(device, settings,
                                                  monkeypatch):
    """The bug this whole change is for. `row` is the job's index within its
    batch, so with several batches in flight `[1]` labelled several phones at
    once - and the file is the only account of a run that crosses machines.

    Stated positively: the same `row` value appears under two different run
    ids, and no line is ambiguous once both are read together."""
    import logging as stdlib_logging

    lines = []

    class Capture(stdlib_logging.Handler):
        def emit(self, record):
            lines.append((getattr(record, "run", None),
                          getattr(record, "row", None)))

    handler = Capture()
    handler.addFilter(builder.BuildContextFilter())
    root = stdlib_logging.getLogger()
    was = root.level
    root.addHandler(handler)
    root.setLevel(stdlib_logging.INFO)

    def one_batch():
        book = make_book()
        monkeypatch.setattr(builder, "_unfinished", lambda c, b: ([], []))
        monkeypatch.setattr(builder, "sync_sheet", lambda *a, **k: {})
        monkeypatch.setattr(builder.Book, "open",
                            classmethod(lambda cls, s: book))
        monkeypatch.setattr(builder.Ledger, "load",
                            staticmethod(lambda p, **k: FakeLedger()))
        monkeypatch.setattr(
            builder, "build_one",
            lambda *a, **k: stdlib_logging.getLogger(
                "geelark_farm.builder").info("in a job")
            or builder.Build(index=a[4], ok=True, status="ready"))
        builder.run(None, settings, count=1, workers=1)

    try:
        one_batch()
        one_batch()
    finally:
        root.removeHandler(handler)
        root.setLevel(was)

    runs = {run for run, row in lines if row == 1}
    assert len(runs) == 2, (
        f"the two batches did not get separate run ids, so `row` 1 names "
        f"two phones and nothing tells them apart: {sorted(lines)}")
    # and the thing that made it ambiguous is still true, which is the point:
    # the same job index really does appear under both.
    assert all(any(run == r and row == 1 for run, row in lines) for r in runs)


# ------------------------------------------------- suspect app-login strikes
def _suspect_session(note, serial, reason="session_unverified"):
    """The slice of a _Session that _session_holds reads, with the app row's
    Note carrying whatever the last release wrote there."""
    row = SimpleNamespace(values={"Note": note})
    return SimpleNamespace(app_row=row, app_signed_in=False,
                           suspect_reason=reason,
                           build=SimpleNamespace(serial=serial),
                           proxy_row=None, refused_exits=[], set_aside=[])


def _apps_book():
    return SimpleNamespace(apps=SimpleNamespace(note_column="Note",
                                                service="OpenAI"))


def test_a_suspect_failure_writes_a_strike_not_a_blank_release():
    """2026-08-31: a DEVICE-blamed app failure released the account back
    blank - indistinguishable from a row nobody had tried - and the one free
    account in the pool went around four phones before the breaker tripped.
    A strike in the Note is what makes the second phone's failure legible."""
    held = builder._session_holds(_apps_book(),
                                  _suspect_session("", "1523"),
                                  proxy_spent=False)
    pool, row, action, note, reason = held[0]
    assert action == builder.RELEASE
    assert "(strike 1 of 3, last on phone 1523)" in note


def test_the_same_phone_failing_again_adds_no_strike():
    """One phone burning its own tries proves nothing about the account -
    the 1465 lesson, where a good password wore a phone's condemnation."""
    held = builder._session_holds(
        _apps_book(),
        _suspect_session("Free again - whatever (strike 1 of 3, last on "
                         "phone 1523). tail", "1523"),
        proxy_spent=False)
    _, _, action, note, _ = held[0]
    assert action == builder.RELEASE
    assert "(strike 1 of 3, last on phone 1523)" in note


def test_the_third_different_phone_sets_the_account_aside():
    held = builder._session_holds(
        _apps_book(),
        _suspect_session("Free again - whatever (strike 2 of 3, last on "
                         "phone 1531).", "1533"),
        proxy_spent=False)
    _, _, action, note, reason = held[0]
    assert action == builder.SET_ASIDE
    assert reason == "session_unverified"
    assert "blank this status" in note


def test_an_unsuspected_release_stays_exactly_what_it_was():
    """The strike path must not touch the ordinary case: budget exhausted,
    account claimed but never typed in."""
    held = builder._session_holds(_apps_book(),
                                  _suspect_session("", "1523", reason=""),
                                  proxy_spent=False)
    assert held[0][2] == builder.RELEASE and held[0][3] == ""


def test_a_stops_the_phone_suspect_reason_is_recorded_on_the_session(
        monkeypatch):
    """The wiring: _sign_into_app must stamp suspect_reason on the way out,
    or the strike path above is dead code behind a field nobody sets."""
    session = SimpleNamespace(
        app_signed_in=False, attempted=0, reset_first=False, condemned=[],
        set_aside=[], suspect_reason="", refused_exits=[], exits=0,
        # Nobody chose this phone's credentials: the pool did, as it does
        # for every phone the keeper builds on its own.
        want=None,
        app_row=SimpleNamespace(credentials=SimpleNamespace(
            email="a@b.com", password="x", totp_secret="")),
        book=SimpleNamespace(apps=SimpleNamespace(service="OpenAI")),
        build=builder.Build(index=1, serial="1523"),
        client=None, phone_id="P1", codes=None,
        settings=SimpleNamespace(target_package="com.openai.chatgpt",
                                 app_login_budget_seconds=100),
        artifacts=None, cancelled=None,
        check_cancelled=lambda: None,
        remaining=lambda: 999.0,
        exits_seen=lambda: set(),
    )

    def finish(status, detail="", ok=False):
        session.build.ok, session.build.status = ok, status
        return session.build

    session.finish = finish

    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda client, phone_id, creds, **kw: SimpleNamespace(
                            ok=False, reason="session_unverified", detail="",
                            trail=[]))
    monkeypatch.setattr(builder, "_given_up_on", lambda book, serial, **k: "")

    out = builder._sign_into_app(session)

    assert out is not None and out.status == "app_session_unverified"
    assert session.suspect_reason == "session_unverified"


def test_a_done_phone_whose_cell_lost_the_account_still_delivers_it(
        monkeypatch):
    """The Phones row is not the only record of what a phone is carrying.

    Phone 1542 was signed into at 23:13 and marked `done` three hours later
    with its `GPT Account` cell empty. The delivery went unrecorded, so the
    account was neither delivered nor freed and sat holding a phone that no
    longer existed. The Gpt Info row knew all along - it keeps its own
    serial - so that is asked when the cell is blank (2026-09-01).
    """
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2}])
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    book = state_book([{"sheet_row": 5, "state": "done", "serial": "650",
                        "gmail": "g@example.com", "app_account": ""}], apps=1)
    book.apps.spend(book.apps.claim(), serial="650")     # as the finish left it

    out = builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

    assert out["delivered"] == ["a0@example.com"]
    assert book.apps._rows[0].values["Status"] == "delivered"
    assert book.apps._rows[0].values["Phone Serial"] == ""
    # and History says what really happened, not "no app account was ever on it"
    written = " ".join(book.history._ws.rows[-1])
    assert "delivered with it" in written and "a0@example.com" in written


def test_a_failed_phone_whose_cell_lost_the_account_still_frees_it(monkeypatch):
    """The same hole, the other way round: a lost cell must not strand an
    account that never got a fair phone either."""
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2}])
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    book = state_book([{"sheet_row": 5, "state": "failed", "serial": "650",
                        "gmail": "", "app_account": ""}], apps=1)
    book.apps.spend(book.apps.claim(), serial="650")

    out = builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

    assert out["freed"] == ["a0@example.com"]
    assert [r.credentials.email for r in book.apps.available] == \
        ["a0@example.com"]


def test_a_row_that_only_remembers_the_serial_is_not_called_a_delivery(
        monkeypatch):
    """The guard on the fallback. A `delivered` row has been settled once
    already and a blank one is stock that happens to remember the phone it
    was last on - taking either would be inventing a delivery rather than
    finding one, and `delivered` is not reversible."""
    monkeypatch.setattr(builder.phones, "listing",
                        lambda c: [{"id": "P1", "serialNo": "650", "status": 2}])
    monkeypatch.setattr(builder.phones, "delete", lambda c, ids, ledger=None: None)
    book = state_book([{"sheet_row": 5, "state": "done", "serial": "650",
                        "gmail": "", "app_account": ""}], apps=2)
    settled, stock = book.apps._rows
    book.apps.retire(settled, note="went out yesterday")
    settled.values["Phone Serial"] = "650"          # a serial it still recalls
    stock.values["Phone Serial"] = "650"            # never blanked when freed

    out = builder.apply_phone_states(None, book, FakeLedger(), MARK_SETTINGS)

    assert out["delivered"] == [] and out["freed"] == []
    assert stock.values["Status"] == ""             # still stock
    written = " ".join(book.history._ws.rows[-1])
    assert "No app account was ever on it" in written


# --------------------------------------- a phone somebody asked for by hand
def test_a_chosen_gmail_is_taken_rather_than_the_next_one(monkeypatch):
    """Somebody chose this row. Quietly building with another spends the
    wrong Gmail and reads as success, which is the kind of help nobody
    asked for."""
    taken = []

    class Row:
        label = "chosen@example.com"

    class Pool:
        available = [Row()]

        @staticmethod
        def claim_this(resource):
            taken.append(resource.label)
            return True

    got = builder._pick(Pool, "Chosen@Example.com", "Gmail")

    assert got.label == "chosen@example.com", "matched without case getting in"
    assert taken == ["chosen@example.com"]


def test_a_chosen_row_that_is_not_free_is_refused_by_name(monkeypatch):
    """Not a fallback to the next one: the answer to "that one is gone" is
    to say so, not to spend a different account and call it done."""
    class Pool:
        available = []

    with pytest.raises(builder.Aborted) as refused:
        builder._pick(Pool, "gone@example.com", "Gmail")

    assert "gone@example.com" in str(refused.value)
    assert "not free" in str(refused.value)


def test_a_row_taken_between_the_asking_and_the_claiming_is_refused():
    """The wish is written a pass before the build, so the row can go in
    between. `claim_this` is what settles it, under the lock."""
    class Row:
        label = "gone@example.com"

    class Pool:
        available = [Row()]

        @staticmethod
        def claim_this(resource):
            return False

    with pytest.raises(builder.Aborted) as refused:
        builder._pick(Pool, "gone@example.com", "Gmail")

    assert "taken while this was being asked for" in str(refused.value)


def test_the_wish_reaches_the_build_and_its_id_comes_back(monkeypatch):
    """The two halves of a hand-built phone are a pass apart: a verb writes
    the wish, and the build phase takes it. This is the join - the wish
    reaching `build_one`, and the id coming back on the Build so the person
    who asked can be told what happened."""
    seen = {}

    def fake_build_one(client, settings, book, ledger, index, **kwargs):
        seen["want"] = kwargs.get("want")
        return builder.Build(index=index, ok=True, status="ready",
                             serial="1600")

    monkeypatch.setattr(builder, "build_one", fake_build_one)
    monkeypatch.setattr(builder.phones, "prune_ledger",
                        lambda client, ledger: None)
    wish = builder.Wanted(gmail="a@example.com", proxy_name="SX9",
                          install_app=False, wanted_id=77)

    built = builder._run_jobs(
        None, _settings_for_jobs(), object(),
        [{"kind": "build", "phone": None, "want": wish}],
        workers=1, reporter=None, on_ready=None, cancel=None,
        ledger=FakeLedger())

    assert seen["want"] is wish, "the credentials somebody chose"
    assert built[0].wanted_id == 77, "and the row that is waiting to hear"


def _settings_for_jobs():
    """Only what `_run_jobs` reads before it hands a job over."""
    import tempfile
    from pathlib import Path as _Path

    tmp = _Path(tempfile.gettempdir())
    return SimpleNamespace(
        ensure_dirs=lambda: None, state_dir=tmp, artifact_dir=tmp,
        stale_claim_seconds=3600, max_concurrent_phones=1)


def test_a_stop_by_hand_does_not_delete_the_phone_it_stopped():
    """The discard guard compared against the literal word "interrupted",
    so `Aborted("stopped_by_hand")` was not spared: a phone stopped before
    its Google account was in would be deleted, while the operator was told
    "nothing was lost - the phone is in the tab and can be finished"
    (failures.py). It was safe only by accident, because the sole reader of
    STOP_BY_HAND ran after the sign-in, and it stops being safe the moment
    a stop can reach the sign-in (2026-09-06)."""
    from geelark_farm import builder as builder_mod

    assert "stopped_by_hand" in builder_mod.STOPPED_BY_A_PERSON
    assert "interrupted" in builder_mod.STOPPED_BY_A_PERSON
    # And only those: an abort that judges the build still discards the
    # phone it could not use.
    for verdict in ("no_usable_proxy", "no_working_proxy",
                    "all_exits_refused", "proxy_change_refused"):
        assert verdict not in builder_mod.STOPPED_BY_A_PERSON


def test_every_way_a_person_stops_a_build_is_named_in_one_place():
    """A third stop word added to `Aborted` and not to the set is a phone
    deleted the next time somebody presses the button."""
    import re
    from pathlib import Path

    from geelark_farm import builder as builder_mod
    from geelark_farm import failures

    source = Path("src/geelark_farm/builder.py").read_text(encoding="utf-8")
    raised = set(re.findall(r'raise Aborted\("([a-z_]+)"\)', source))
    # The ones failures.py says nobody is to blame for and that name a
    # person rather than a fault.
    by_hand = {word for word in raised
               if "stopped" in failures.situation(word).lower()
               or "you stopped" in failures.situation(word).lower()}
    assert by_hand <= builder_mod.STOPPED_BY_A_PERSON, (
        f"{by_hand - builder_mod.STOPPED_BY_A_PERSON} would delete a phone")


def test_stop_this_one_is_heard_during_the_google_sign_in(monkeypatch):
    """`STOP_BY_HAND` had one reader, on the session, and the session is
    not built until after the Google sign-in and the install. A stop asked
    for during those - most of a build's minutes - was heard only when they
    ended: up to twenty-five minutes of a phone billing by the minute after
    somebody said stop (2026-09-06)."""
    from geelark_farm import builder as builder_mod

    watched = []
    builder_mod.STOP_BY_HAND.add("1901")
    try:
        # The callable build_one hands to google_login.sign_in as `watch`,
        # rebuilt here with the same closure shape: a build with a serial,
        # and no service-wide stop.
        build = builder_mod.Build(index=0, serial="1901")

        def check_cancelled() -> None:
            serial = str(build.serial or "").strip()
            if serial and serial in builder_mod.STOP_BY_HAND:
                builder_mod.STOP_BY_HAND.discard(serial)
                raise builder_mod.Aborted("stopped_by_hand")
            watched.append("kept going")

        with pytest.raises(builder_mod.Aborted, match="stopped_by_hand"):
            check_cancelled()
        assert watched == []
        assert "1901" not in builder_mod.STOP_BY_HAND, "taken, not left set"
    finally:
        builder_mod.STOP_BY_HAND.discard("1901")


def test_the_sign_in_watch_is_the_check_that_hears_a_hand_stop():
    """The wiring, read off the source: the callable handed to
    `google_login.sign_in` as `watch=` must be the one that consults
    STOP_BY_HAND, not a closure that only knows about the service
    stopping."""
    import inspect

    from geelark_farm import builder as builder_mod

    source = inspect.getsource(builder_mod.build_one)
    assert "watch=check_cancelled" in source
    body = source.partition("def check_cancelled()")[2].partition(
        "def finish(")[0]
    assert "_stop_asked(settings, build.serial)" in body, (
        "the sign-in's watch cannot hear Stop")
    session = inspect.getsource(builder_mod._Session.check_cancelled)
    assert '_stop_asked(getattr(self, "settings", None), self.build.serial)' in session


def test_a_stop_pressed_on_the_console_reaches_a_build_in_another_container(
        make_settings, tmp_path, monkeypatch):
    """STOP_BY_HAND was a set in the keeper's memory, and the builds run in
    a builder container: Cancel on a building row did nothing (the
    operator, 2026-09-10). The store carries the press now; a build asks
    it every step, reading at most every few seconds, and takes the
    request out the moment it is heard."""
    from geelark_farm import builder as builder_mod
    from geelark_farm.store import stops as store_stops

    settings = make_settings(state_dir=tmp_path, store_enabled=True)
    asked = {"2241"}
    reads = []
    monkeypatch.setattr(store_stops, "asked",
                        lambda s: reads.append(1) or set(asked))
    gone = []
    monkeypatch.setattr(store_stops, "honoured",
                        lambda s, serial: gone.append(serial))
    monkeypatch.setattr(builder_mod, "_STOP_SEEN",
                        {"at": 0.0, "serials": frozenset()})

    assert builder_mod._stop_asked(settings, "2240") is False
    assert builder_mod._stop_asked(settings, "2241") is True
    assert gone == ["2241"], "taken out where it was written"
    assert builder_mod._stop_asked(settings, "2241") is False, "heard once"
    assert len(reads) == 1, "one store read for the three asks - throttled"
    # The same process's own press is heard without the store.
    builder_mod.STOP_BY_HAND.add("2242")
    assert builder_mod._stop_asked(settings, "2242") is True
    assert "2242" not in builder_mod.STOP_BY_HAND
    # No store, no serial: nothing to hear.
    off = make_settings(state_dir=tmp_path, store_enabled=False)
    assert builder_mod._stop_asked(off, "2241") is False
    assert builder_mod._stop_asked(settings, "") is False
    # A store that cannot be read is a stop not heard yet, not a crash.
    monkeypatch.setattr(builder_mod, "_STOP_SEEN",
                        {"at": 0.0, "serials": frozenset()})
    monkeypatch.setattr(store_stops, "asked",
                        lambda s: (_ for _ in ()).throw(RuntimeError("down")))
    assert builder_mod._stop_asked(settings, "2241") is False


# ------------------------------ warm on purpose, with manual login (2026-09-08)
def _session_for(settings, *, apps=2, want=None):
    book = make_book(apps=apps)
    s = builder._Session(
        client=None, settings=settings, book=book,
        build=builder.Build(index=1, serial="691"), phone_id="P1",
        artifacts=settings.artifact_dir, deadline=time.monotonic() + 600,
        started=time.monotonic())
    s.want = want
    s.build.app_installed = True
    return s


def test_the_keepers_build_stops_warm_and_takes_no_account_under_manual_login(
        monkeypatch, make_settings, tmp_path):
    """Five warm phones, no account on any of them until an operator sends
    one (2026-09-08). With accounts sitting in the pool, the keeper's own
    build used to sign the next one in by itself."""
    settings = make_settings(state_dir=tmp_path, manual_login=True)
    signed = []
    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda *a, **k: signed.append(1) or SIGNED_IN)
    s = _session_for(settings, apps=2)

    build = builder._sign_into_app(s)

    assert build is not None and build.status == builder.WARM_FOR_OPERATOR
    assert not build.ok and "warm on purpose" in build.detail
    assert signed == [], "nothing was signed in"
    assert len(s.book.apps.available) == 2, "nothing was claimed"
    assert builder._phone_status(build) == builder.APP_ONLY
    from geelark_farm import breaker
    assert not breaker.counts_against(build), "the stock being kept is not a failure"
    assert breaker.shows_it_works(build)


def test_without_manual_login_the_keepers_build_still_takes_an_account(
        monkeypatch, make_settings, tmp_path):
    settings = make_settings(state_dir=tmp_path, manual_login=False)
    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)
    s = _session_for(settings, apps=2)

    assert builder._sign_into_app(s) is None
    assert s.build.app_account == "a0@example.com"


def test_a_by_hand_build_with_no_account_named_stops_warm(
        monkeypatch, make_settings, tmp_path):
    """Blank used to mean "the next free one"; the card says none now, and
    none means none - an account goes on when somebody sends one
    (2026-09-08). Named, it is used."""
    settings = make_settings(state_dir=tmp_path, manual_login=False)
    signed = []
    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda *a, **k: signed.append(1) or SIGNED_IN)
    s = _session_for(settings, apps=2,
                     want=builder.Wanted(wanted_id=1, gmail="", app_account=""))

    build = builder._sign_into_app(s)
    assert build is not None and build.status == builder.WARM_FOR_OPERATOR
    assert "asked for without an account" in build.detail
    assert signed == [] and len(s.book.apps.available) == 2

    s = _session_for(settings, apps=2,
                     want=builder.Wanted(wanted_id=2, app_account="a1@example.com"))
    assert builder._sign_into_app(s) is None
    assert s.build.app_account == "a1@example.com"


def test_the_app_a_hand_built_phone_gets_is_what_was_asked_for(make_settings,
                                                                 tmp_path):
    settings = make_settings(state_dir=tmp_path)
    assert builder._package_for(settings, "chatgpt") == settings.target_package
    assert builder._package_for(settings, "spotify") == builder.SPOTIFY_PACKAGE
    assert builder._package_for(settings, "claude") == builder.CLAUDE_PACKAGE
    assert builder.Wanted().app == "chatgpt", "the keeper's own phones"
    assert builder.APPS == {"chatgpt": "ChatGPT", "spotify": "Spotify",
                            "claude": "Claude"}
    import inspect

    src = inspect.getsource(builder.build_one)
    assert 'app = want.app if want is not None else "chatgpt"' in src
    assert '"signed into Google; no app was asked for"' in src, "none: ready at once"
    assert 'if app != "chatgpt":' in src, "Spotify: ready once installed"
    assert '_package_for(settings, app)' in src


def test_a_sent_account_that_fails_does_not_pull_the_next_one_under_manual_login(
        monkeypatch, make_settings, tmp_path):
    """The operator sent one account. When it is refused, the phone stays
    warm for the next one they choose - it does not work through the pool
    on its own (2026-09-08)."""
    settings = make_settings(state_dir=tmp_path, manual_login=True)
    tried = []
    monkeypatch.setattr(
        builder.chatgpt_login, "sign_in",
        lambda c, p, creds, **k: tried.append(creds.email)
        or Outcome("fatal", "wrong_password"))
    s = _session_for(settings, apps=3)
    s.app_row = s.book.apps.claim("691")            # what login_accounts did

    build = builder._sign_into_app(s)

    assert build.status == builder.WARM_FOR_OPERATOR
    assert "did not sign in" in build.detail
    assert "a0@example.com - " in build.detail, "named, with the reason"
    assert "password" in build.detail
    assert tried == ["a0@example.com"], "one attempt, the one that was sent"
    # The service's own word about a sent account stands: a0 is set aside
    # with the reason beside it, for a person to check (the operator,
    # 2026-09-10) - and a1 and a2 were never taken.
    assert [r.credentials.email for r in s.book.apps.available] == [
        "a1@example.com", "a2@example.com"]
    assert s.book.apps._rows[0].values["Status"] == "wrong_password"


def test_a_sent_account_the_phone_never_judged_is_still_given_back(
        monkeypatch, make_settings, tmp_path):
    """The exoneration is for refusals that judged nothing about the
    credential - the page would not move, the screen could not be read.
    Those stay the phone's, sent account or not."""
    settings = make_settings(state_dir=tmp_path, manual_login=True)
    monkeypatch.setattr(
        builder.chatgpt_login, "sign_in",
        lambda c, p, creds, **k: Outcome("fatal", "stuck_on_password_entry"))
    s = _session_for(settings, apps=2)
    s.app_row = s.book.apps.claim("691")

    build = builder._sign_into_app(s)

    assert build.status == "app_stuck_on_password_entry"
    assert s.condemned == [] and s.judged == {}, "nothing was judged"
    assert s.book.apps._rows[0].values["Status"] != "stuck_on_password_entry"


def test_an_account_named_on_the_card_that_is_refused_stays_set_aside(
        monkeypatch, make_settings, tmp_path):
    """Named by a person, not drawn from the pool: the same rule as a sent
    one, with manual login off."""
    settings = make_settings(state_dir=tmp_path, manual_login=False)
    monkeypatch.setattr(
        builder.chatgpt_login, "sign_in",
        lambda c, p, creds, **k: Outcome("fatal", "wrong_password"))
    s = _session_for(settings, apps=2,
                     want=builder.Wanted(wanted_id=3, app_account="a0@example.com"))

    build = builder._sign_into_app(s)

    assert build is not None and not build.ok
    assert s.book.apps._rows[0].values["Status"] == "wrong_password"
    assert [r.credentials.email for r in s.book.apps.available] == [
        "a1@example.com"]


def test_a_hand_built_phone_is_its_builders_from_the_moment_it_exists():
    """Taken and owned by whoever asked, and marked built by them; the
    keeper's own phones carry none of that (the operator, 2026-09-08)."""
    import inspect

    assert builder.Wanted().requested_by is None
    src = inspect.getsource(builder.build_one)
    assert '{"State": "taken", "Built by": str(want.requested_by),' in src
    assert '"Owner": str(want.requested_by)}' in src
    assert "if want is not None and want.requested_by else {})" in src


def test_the_keepers_phone_carries_spotify_beside_chatgpt(device, settings,
                                                          drive, monkeypatch):
    """A warm phone is warm once both are on; a phone asked for by hand
    gets exactly the app it asked for (the operator, 2026-09-08)."""
    installed = []
    monkeypatch.setattr(
        builder.play_install, "install",
        lambda client, phone_id, package, **k: installed.append(package)
        or INSTALLED)
    book = make_book(apps=1)
    build = drive(book, settings, google=[SIGNED_IN])
    assert build.ok and installed == [settings.target_package,
                                      builder.SPOTIFY_PACKAGE]
    assert build.app == "chatgpt+spotify"

    installed.clear()
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)
    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)
    book = make_book(apps=1)
    build = builder.build_one(None, settings, book, FakeLedger(), 1,
                              want=builder.Wanted(app="chatgpt",
                                                  app_account="a0@example.com"))
    assert build.ok and installed == [settings.target_package]
    assert build.app == "chatgpt"


def test_spotify_not_installing_is_a_note_not_a_failed_phone(
        device, settings, drive, monkeypatch):
    from geelark_farm.flows.play_install import Outcome as Install

    def install(client, phone_id, package, **k):
        if package == builder.SPOTIFY_PACKAGE:
            return Install("fatal", "install_failed", trail=[])
        return INSTALLED
    monkeypatch.setattr(builder.play_install, "install", install)
    build = drive(make_book(apps=1), settings, google=[SIGNED_IN])
    assert build.ok and build.app == "chatgpt"
    assert ("spotify", "install_failed", "Play") in build.tried


def test_a_hand_built_phones_own_take_is_not_a_stranger_giving_up_on_it(
        monkeypatch, make_settings, tmp_path):
    """Taken by its builder from the moment its row exists, and read as a
    stranger's take, every hand-built phone gave up on itself at its
    first check: "somebody wrote taken in its State" (2026-09-08)."""
    from geelark_farm.store import person

    settings = make_settings(state_dir=tmp_path, store_enabled=True)
    _SAID["1958"] = "taken"
    assert builder._given_up_on(settings, "1958") == "taken"
    assert builder._given_up_on(settings, "1958", own_take=True) == ""
    _SAID["1958"] = "failed"
    assert builder._given_up_on(settings, "1958", own_take=True) == "failed"
    import inspect

    src = inspect.getsource(builder.build_one)
    assert "own_take=bool(want and want.requested_by)" in src
    src = inspect.getsource(builder._sign_into_app)
    assert "own_take=bool(s.want and s.want.requested_by)" in src


# ------------------------------------------- Spotify from GeeLark's installer
def _boot_fires(monkeypatch):
    """`ensure_running` as the real one behaves: the boot hook fires."""
    monkeypatch.setattr(builder.phones, "ensure_running",
                        lambda *a, **k: k.get("on_running") and k["on_running"]())


def test_the_keepers_spotify_is_ordered_at_boot_and_only_chatgpt_walks_play(
        device, settings, drive, monkeypatch):
    """Spotify comes from GeeLark's app center, ordered the moment the phone
    is up and left to land during the sign-in; ChatGPT is not in the center
    and still walks Play (2026-09-08)."""
    _boot_fires(monkeypatch)
    ordered, waited, played = [], [], []
    monkeypatch.setattr(builder.apps, "begin",
                        lambda c, p, package, **k: ordered.append(package)
                        or True)
    monkeypatch.setattr(builder.apps, "wait_installed",
                        lambda c, p, package, **k: waited.append(package)
                        or True)
    monkeypatch.setattr(builder.play_install, "install",
                        lambda c, p, package, **k: played.append(package)
                        or INSTALLED)

    build = drive(make_book(apps=1), settings, google=[SIGNED_IN])

    assert build.ok and build.app == "chatgpt+spotify"
    assert ordered == [builder.SPOTIFY_PACKAGE]
    assert waited == [builder.SPOTIFY_PACKAGE]
    assert played == [settings.target_package], "Spotify never walked Play"


def test_an_order_geelark_never_lands_falls_back_to_play(
        device, settings, drive, monkeypatch):
    _boot_fires(monkeypatch)
    monkeypatch.setattr(builder.apps, "begin", lambda *a, **k: True)
    monkeypatch.setattr(builder.apps, "wait_installed", lambda *a, **k: False)
    played = []
    monkeypatch.setattr(builder.play_install, "install",
                        lambda c, p, package, **k: played.append(package)
                        or INSTALLED)

    build = drive(make_book(apps=1), settings, google=[SIGNED_IN])

    assert build.ok and build.app == "chatgpt+spotify"
    assert played == [settings.target_package, builder.SPOTIFY_PACKAGE]


def test_a_center_without_the_app_or_the_door_shut_means_play_as_before(
        device, settings, drive, monkeypatch, make_settings):
    _boot_fires(monkeypatch)
    monkeypatch.setattr(builder.apps, "begin", lambda *a, **k: False)
    monkeypatch.setattr(builder.apps, "wait_installed",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("nothing was ordered")))
    played = []
    monkeypatch.setattr(builder.play_install, "install",
                        lambda c, p, package, **k: played.append(package)
                        or INSTALLED)
    build = drive(make_book(apps=1), settings, google=[SIGNED_IN])
    assert build.ok and played == [settings.target_package,
                                   builder.SPOTIFY_PACKAGE]

    # The door shut by hand: GeeLark is never asked.
    asked = []
    monkeypatch.setattr(builder.apps, "begin",
                        lambda *a, **k: asked.append(1) or True)
    played.clear()
    import dataclasses
    off = dataclasses.replace(settings, app_install_api=False)
    build = drive(make_book(apps=1), off, google=[SIGNED_IN])
    assert build.ok and asked == [] and played == [settings.target_package,
                                                   builder.SPOTIFY_PACKAGE]


def test_a_hand_built_spotify_phone_takes_the_same_door(
        device, settings, drive, monkeypatch):
    _boot_fires(monkeypatch)
    ordered, played = [], []
    monkeypatch.setattr(builder.apps, "begin",
                        lambda c, p, package, **k: ordered.append(package)
                        or True)
    monkeypatch.setattr(builder.apps, "wait_installed", lambda *a, **k: True)
    monkeypatch.setattr(builder.play_install, "install",
                        lambda c, p, package, **k: played.append(package)
                        or INSTALLED)
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)

    build = builder.build_one(None, settings, make_book(apps=1), FakeLedger(),
                              1, want=builder.Wanted(app="spotify"))
    assert build.ok and build.app == "spotify"
    assert ordered == [builder.SPOTIFY_PACKAGE] and played == []

    # A ChatGPT-only hand build orders nothing: the center has no ChatGPT,
    # and Spotify was not asked for.
    ordered.clear()
    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)
    build = builder.build_one(None, settings, make_book(apps=1), FakeLedger(),
                              1, want=builder.Wanted(app="chatgpt",
                                                     app_account="a0@example.com"))
    assert build.ok and ordered == []


def test_a_build_bills_its_own_api_calls_and_a_warm_phone_reads_warm(
        device, settings, drive, monkeypatch):
    """Per build, from the client's per-thread count - what decides how many
    phones may be built at once against the 200-a-minute limit (B-5). And
    the log line for a phone kept warm on purpose said FAIL for a day."""
    build = drive(make_book(apps=1), settings, google=[SIGNED_IN])
    assert build.api_calls == 0, "no client, no calls"

    class Counting:
        def calls_here(self):
            return 7
    assert builder._calls(Counting()) == 7 and builder._calls(None) == 0

    warm = builder.Build(index=1, ok=False, status=builder.WARM_FOR_OPERATOR)
    assert builder._mark(warm) == "WARM"
    assert builder._mark(builder.Build(index=1, ok=True, status="ready")) == "OK"
    assert builder._mark(builder.Build(index=1, ok=False,
                                       status="install_failed")) == "FAIL"


# --------------------------------------------- two captchas change the exit
def test_two_captchas_on_one_exit_change_the_exit_not_the_third_gmail(
        device, settings, drive):
    """A captcha is Google distrusting the address, and one is treated that
    way; two in a row on the same exit is the exit. Phone 1995 spent three
    Gmails in an hour on SX44 while every other phone that pass met one
    captcha or none (the operator, 2026-09-08)."""
    settings = _many_gmails_per_phone(settings)
    captcha = Outcome("fatal", "captcha_shown")
    book = make_book(gmails=3, proxies=3)
    build = drive(book, settings, google=[captcha, captcha, SIGNED_IN])

    assert build.ok and build.gmail == "g2@example.com"
    assert len(device.proxies_set) == 1, "one swap, after the second captcha"
    # Both addresses that met a captcha are set aside, as before.
    assert [r.credentials.email for r in book.gmails.available] == []
    # The phone is on the swapped-in exit for the third address.
    assert device.proxies_set[0] in build.proxy

    # One captcha alone does not move the phone.
    device.proxies_set.clear()
    book = make_book(gmails=3, proxies=3)
    build = drive(book, settings, google=[captcha, SIGNED_IN])
    assert build.ok and device.proxies_set == []


def test_no_exit_to_move_to_is_not_a_failed_build(device, settings, drive,
                                                    monkeypatch):
    """The next Gmail goes on the same exit, as before - and the phone,
    which `_new_exit` stops before it looks, is brought back up first."""
    settings = _many_gmails_per_phone(settings)
    captcha = Outcome("fatal", "captcha_shown")
    started = []
    monkeypatch.setattr(builder.phones, "ensure_running",
                        lambda *a, **k: started.append(1))
    book = make_book(gmails=3, proxies=1)
    build = drive(book, settings, google=[captcha, captcha, SIGNED_IN])

    assert build.ok and build.gmail == "g2@example.com"
    assert device.proxies_set == []
    assert len(started) == 2, "once at boot, once after the refused swap"


# ------------------------------------------ a host Google keeps challenging
def test_three_challenges_on_one_host_in_a_day_set_its_free_exits_aside(
        make_settings, monkeypatch):
    """The vendor sells several ports on one address, and Google's opinion
    is of the address: on 190.2.143.20 one phone ate forty-three captcha
    rounds while phones on 212.8.248.20 met three or none (the operator,
    2026-09-09). At the third challenge in a day, every free exit on that
    host is set aside as suspect; the other host is untouched."""
    from types import SimpleNamespace

    builder._captcha_hosts_memory.clear()
    monkeypatch.setattr(builder.failures, "today", lambda: "2026-09-09")

    def exit_(name, host):
        return SimpleNamespace(name=name, label=name,
                               proxy=SimpleNamespace(host=host))

    failed = []
    free = [exit_("SX4", "190.2.143.20"), exit_("SX10", "190.2.143.20"),
            exit_("SX1", "212.8.248.20")]
    book = SimpleNamespace(proxies=SimpleNamespace(
        available=free,
        fail=lambda r, status, note="": failed.append((r.name, status, note))))
    settings = make_settings()            # no store: the day lives in memory
    held = exit_("SX44", "190.2.143.20")  # the one the build is on

    assert builder._strike_captcha_host(settings, book, held) == []
    assert builder._strike_captcha_host(settings, book, held) == []
    assert failed == [], "two challenges are a bad day, not a verdict"
    assert builder._strike_captcha_host(settings, book, held) == ["SX4", "SX10"]
    assert [(n, s) for n, s, _ in failed] == [("SX4", "suspect"),
                                              ("SX10", "suspect")]
    assert "3 Google challenges on 190.2.143.20" in failed[0][2]
    assert "Press Free" in failed[0][2]

    # Another day starts the count again.
    monkeypatch.setattr(builder.failures, "today", lambda: "2026-09-10")
    failed.clear()
    assert builder._strike_captcha_host(settings, book, held) == []
    assert failed == []


def test_a_sign_in_that_met_a_captcha_on_the_way_in_still_counts_against_the_host(
        device, settings, drive, monkeypatch):
    """A phone that solves thirteen rounds on 190.2.143.20 and signs in is
    still thirteen rounds that host cost (the operator, 2026-09-09)."""
    settings = _many_gmails_per_phone(settings)
    struck = []
    monkeypatch.setattr(builder, "_strike_captcha_host",
                        lambda s, b, row: struck.append(row.proxy.host) or [])
    heavy = Outcome("success", "signed_in",
                    trail=["email_entry"] + ["captcha"] * 6
                    + ["password_entry", "2fa_code_entry"])
    build = drive(make_book(), settings, google=[heavy])

    assert build.ok
    assert len(struck) == 1, "once per sign-in, however many rounds"

    # Three to five rounds is what a young account meets anywhere: not a
    # word against the host (four exits on two ordinary hosts were set
    # aside in one evening before this line, 2026-09-09).
    struck.clear()
    light = Outcome("success", "signed_in",
                    trail=["email_entry", "captcha", "captcha", "captcha",
                           "password_entry"])
    build = drive(make_book(), settings, google=[light])
    assert build.ok and struck == [], "a light captcha is not a strike"

    # One that never got through is, however few rounds it took.
    struck.clear()
    lost = Outcome("fatal", "captcha_shown", trail=["email_entry", "captcha"])
    build = drive(make_book(), settings, google=[lost, SIGNED_IN])
    assert build.ok and len(struck) == 1

    struck.clear()
    build = drive(make_book(), settings, google=[SIGNED_IN])
    assert build.ok and struck == [], "no captcha, no strike"


def test_an_exit_handed_back_onto_a_struck_host_goes_back_as_suspect():
    """The tally only sets aside what is free the moment it fills; the two
    exits a build was holding would otherwise be the first two the next
    build takes (2026-09-09)."""
    from types import SimpleNamespace

    freed, failed = [], []
    pool = SimpleNamespace(
        release=lambda r, note="": freed.append(r.label),
        fail=lambda r, status, note="": failed.append((r.label, status, note)))
    book = SimpleNamespace(proxies=pool, apps=None, gmails=None)
    bad = SimpleNamespace(label="SX44",
                          proxy=SimpleNamespace(host="190.2.143.20"))
    good = SimpleNamespace(label="SX1",
                           proxy=SimpleNamespace(host="212.8.248.20"))

    builder._release(book, builder.Build(index=1), [
        (pool, bad, builder.RELEASE, "", ""),
        (pool, good, builder.RELEASE, "", ""),
    ], suspect_hosts={"190.2.143.20"})

    assert freed == ["SX1"]
    assert [(n, s) for n, s, _ in failed] == [("SX44", "suspect")]
    assert "Press Free" in failed[0][2]


# ----------------------------------------------- the operator's Play recipe
def _play(kind, reason):
    from geelark_farm.flows import play_install

    return play_install.Outcome(kind, reason)


def test_a_play_page_without_install_gets_a_new_exit_and_a_cleared_play(
        device, settings, drive, monkeypatch):
    """The operator's recipe: stop the phone, another exit, start it,
    force-stop and clear the Play Store, open the page again - usually
    the third exit does it (2026-09-10)."""
    answers = [_play("fatal", "no_install_button"),
               _play("fatal", "app_unavailable"),
               _play("success", "installed")]
    # Spotify's own install rides after ChatGPT's on a keeper build; once
    # the script is spent it simply lands.
    monkeypatch.setattr(builder, "_install",
                        lambda *a, **k: (answers.pop(0) if answers
                                         else _play("success", "installed")))
    resets = []
    monkeypatch.setattr(builder, "_reset_play",
                        lambda c, p: resets.append(p))
    build = drive(make_book(proxies=4), settings, google=[SIGNED_IN])

    assert build.ok and build.app_installed
    assert len(device.proxies_set) == 2, "two exits before the page had Install"
    assert len(resets) == 2, "Play cleared after each move"
    assert "10.0.0.2" in build.proxy, "the phone ends on the exit it moved to"


def test_a_parked_download_gets_play_cleared_first_and_an_exit_second(
        device, settings, drive, monkeypatch):
    answers = [_play("fatal", "download_stalled"),
               _play("fatal", "download_stalled"),
               _play("success", "installed")]
    # Spotify's own install rides after ChatGPT's on a keeper build; once
    # the script is spent it simply lands.
    monkeypatch.setattr(builder, "_install",
                        lambda *a, **k: (answers.pop(0) if answers
                                         else _play("success", "installed")))
    resets = []
    monkeypatch.setattr(builder, "_reset_play",
                        lambda c, p: resets.append(p))
    build = drive(make_book(proxies=4), settings, google=[SIGNED_IN])

    assert build.ok
    assert len(resets) == 2
    assert len(device.proxies_set) == 1, (
        "the first stall only clears Play; the second moves the exit")


def test_a_play_refusal_the_recipe_cannot_answer_is_not_retried(
        device, settings, drive, monkeypatch):
    answers = [_play("fatal", "play_needs_payment")]
    # Spotify's own install rides after ChatGPT's on a keeper build; once
    # the script is spent it simply lands.
    monkeypatch.setattr(builder, "_install",
                        lambda *a, **k: (answers.pop(0) if answers
                                         else _play("success", "installed")))
    monkeypatch.setattr(builder, "_reset_play",
                        lambda c, p: (_ for _ in ()).throw(AssertionError("no")))
    build = drive(make_book(proxies=4), settings, google=[SIGNED_IN])

    assert not build.ok and build.status == "install_failed"
    assert device.proxies_set == []


def test_the_recipe_stops_at_three_exits(device, settings, drive, monkeypatch):
    answers = [_play("fatal", "no_install_button")] * 6
    # Spotify's own install rides after ChatGPT's on a keeper build; once
    # the script is spent it simply lands.
    monkeypatch.setattr(builder, "_install",
                        lambda *a, **k: (answers.pop(0) if answers
                                         else _play("success", "installed")))
    monkeypatch.setattr(builder, "_reset_play", lambda c, p: None)
    build = drive(make_book(proxies=6), settings, google=[SIGNED_IN])

    assert not build.ok and build.status == "install_failed"
    assert len(device.proxies_set) == 3


# ------------------------------------------ the build card (2026-09-10)
def test_a_bare_phone_claims_no_gmail_signs_nothing_in_and_is_kept(
        device, settings, monkeypatch):
    """No Google account: the Gmail phase is skipped whole, the phone is
    ready the moment it is up, and the rule that deletes a phone with
    nothing signed into it does not apply - it has nothing on purpose."""
    asked = []
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: asked.append(1) or SIGNED_IN)
    book = make_book(gmails=2, apps=1)

    build = builder.build_one(None, settings, book, FakeLedger(), 1,
                              want=builder.Wanted(no_gmail=True, app=""))

    assert build.ok and build.status == "ready", build.detail
    assert "bare phone" in build.detail
    assert asked == [], "nothing was signed in"
    assert build.gmail == "" and build.app == ""
    assert build.phone_id, "kept, not discarded"
    assert len(book.gmails.available) == 2, "no address was claimed"
    assert len(book.apps.available) == 1


def test_a_chosen_gmail_that_fails_stops_the_build_and_says_which(
        device, settings, monkeypatch):
    """Somebody named this address on the card; the next free one is not
    what they asked for. It is set aside with the reason, the build ends
    with the address and the reason in one sentence, and the phone -
    with nothing on it - goes."""
    wrong = Outcome("fatal", "wrong_password")
    monkeypatch.setattr(builder.google_login, "sign_in", lambda *a, **k: wrong)
    monkeypatch.setattr(builder.shell, "device_accounts", lambda *a, **k: [])
    book = make_book(gmails=2)

    build = builder.build_one(None, settings, book, FakeLedger(), 1,
                              want=builder.Wanted(gmail="g0@example.com"))

    assert build.status == "chosen_gmail_failed"
    assert "g0@example.com" in build.detail and "password" in build.detail
    assert [r.label for r in book.gmails.available] == ["g1@example.com"], (
        "the chosen one is set aside; the other was never touched")
    assert ("g0@example.com", "wrong_password", "Google") in build.tried


def test_five_refused_gmails_end_the_build_with_a_tally(device, settings,
                                                        monkeypatch):
    """Bounded by the budget alone, a bad exit or a bad batch ate address
    after address and reported budget_exhausted, which blames nothing."""
    wrong = Outcome("fatal", "wrong_password")
    monkeypatch.setattr(builder.google_login, "sign_in", lambda *a, **k: wrong)
    book = make_book(gmails=7)

    build = builder.build_one(None, settings, book, FakeLedger(), 1)

    assert build.status == "gmails_exhausted"
    assert build.detail.startswith("5 Gmails from the pool were refused")
    assert build.detail.count("wrong_password") == 5
    assert len(book.gmails.available) == 2, "five set aside, two untouched"
    assert builder.GMAILS_PER_BUILD == 5


def test_the_sixth_gmail_is_not_reached_when_the_fifth_signs_in(
        device, settings, drive):
    wrong = Outcome("fatal", "wrong_password")
    build = drive(make_book(gmails=6), settings,
                  google=[wrong, wrong, wrong, wrong, SIGNED_IN])
    assert build.ok and build.gmail == "g4@example.com"


# --------------------------------------- the login-rate work (2026-09-10)
def test_a_distrusted_first_gmail_ends_the_build_and_spares_the_rest(
        device, settings, drive):
    """One Gmail per phone: Google distrusting the first address is Google
    distrusting the device and the exit - the second address on the same
    phone signed in 54 times in 100 against 73 for the first, the fifth
    never. The phone goes, the other addresses are never touched, and
    the refused one is marked (the ladder brings it back)."""
    book = make_book(gmails=3)
    build = drive(book, settings,
                  google=[Outcome("fatal", "captcha_shown"), SIGNED_IN])

    assert build.status == "phone_distrusted" and not build.ok
    assert "g0@example.com" in build.detail and "fresh phone" in build.detail
    assert [r.credentials.email for r in book.gmails.available] == [
        "g1@example.com", "g2@example.com"], "never handed to this phone"
    assert book.gmails._rows[0].values["Status"] == "captcha_shown"
    assert ("g0@example.com", "captcha_shown", "Google") in build.tried


def test_a_wrong_password_is_not_distrust_and_the_next_gmail_still_goes_on(
        device, settings, drive):
    """The rule is about Google's distrust pages, not about a credential
    the service judged: a wrong password says nothing about the phone."""
    build = drive(make_book(gmails=2), settings,
                  google=[Outcome("fatal", "wrong_password"), SIGNED_IN])
    assert build.ok and build.gmail == "g1@example.com"


def test_the_distrust_reasons_are_the_ladders_reasons():
    from geelark_farm import failures

    assert failures.retryable("captcha_shown")
    assert failures.retryable("phone_verification_required")
    assert failures.retryable("verification_blocked")
    assert not failures.retryable("wrong_password")
    assert not failures.retryable("stuck_on_dismissable")
    assert failures.knows("phone_distrusted") and failures.knows("captcha_text")
    assert failures.verdict("phone_distrusted").stops_the_phone
    assert failures.verdict("captcha_text").needs_a_new_exit


def test_a_phone_on_a_condemned_model_is_deleted_and_made_again(
        make_settings, tmp_path, monkeypatch):
    """GeeLark's API takes no model; the only choice is after the fact,
    and a phone a few seconds old costs nothing but those seconds."""
    settings = make_settings(state_dir=tmp_path,
                             bad_models=("vivo V2362A", "Redmi 2311DRK48C"),
                             model_retries=3)
    models = iter(["vivo V2362A", "Redmi 2311DRK48C", "OPPO PLN110"])
    made, deleted = [], []

    class Entry:
        def __init__(self, model):
            self.model, self.phone_id, self.serial = model, f"P{len(made)}", "1"

    def create(client, s, proxy, *, ledger, label, account):
        e = Entry(next(models)); made.append(e.phone_id); return e

    monkeypatch.setattr(builder.phones, "create", create)
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))

    entry = builder._create_kept(None, settings, FakeLedger(), None,
                                 label="build 1", account="a@x.com")
    assert entry.model == "OPPO PLN110"
    assert made == ["P0", "P1", "P2"] and deleted == ["P0", "P1"]
    assert builder._bad_model(settings, "vivo V2362A (something)")
    assert builder._bad_model(settings, "REDMI 2311DRK48C")
    assert not builder._bad_model(settings, "OPPO PLN110")
    assert not builder._bad_model(settings, "")


def test_the_last_phone_is_kept_when_every_retry_is_a_bad_model(
        make_settings, tmp_path, monkeypatch):
    settings = make_settings(state_dir=tmp_path, bad_models=("vivo",),
                             model_retries=2)
    count = {"n": 0}

    class Entry:
        model, phone_id, serial = "vivo V2362A", "P", "1"

    def create(*a, **k):
        count["n"] += 1; return Entry()

    monkeypatch.setattr(builder.phones, "create", create)
    monkeypatch.setattr(builder.phones, "delete", lambda *a, **k: None)
    assert builder._create_kept(None, settings, FakeLedger(), None,
                                label="b", account="") is not None
    assert count["n"] == 3, "the original and two retries"
    # A delete that fails keeps the phone rather than leaking it.
    count["n"] = 0
    monkeypatch.setattr(builder.phones, "delete",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    builder._create_kept(None, settings, FakeLedger(), None, label="b", account="")
    assert count["n"] == 1


def test_every_google_sign_in_is_recorded_with_its_position_and_host(
        device, make_settings, tmp_path, drive, monkeypatch):
    from geelark_farm.store import signins as store_signins

    settings = make_settings(state_dir=tmp_path,
                             artifact_dir=tmp_path / "artifacts",
                             store_enabled=True, one_gmail_per_phone=False)
    rows = []
    monkeypatch.setattr(store_signins, "record",
                        lambda s, **k: rows.append(k) or True)
    build = drive(make_book(gmails=2), settings,
                  google=[Outcome("fatal", "wrong_password"), SIGNED_IN])

    assert build.ok
    assert [(r["gmail"], r["position"], r["reason"], r["ok"]) for r in rows] == [
        ("g0@example.com", 1, "wrong_password", False),
        ("g1@example.com", 2, "signed_in", True)]
    assert rows[0]["host"] == "10.0.0.0" and rows[0]["serial"] == "622"
    assert rows[0]["seconds"] >= 0
    # Off without a store; a record that fails is a debug line.
    quiet = make_settings(state_dir=tmp_path, store_enabled=False)
    builder._record_signin(quiet, builder.Build(index=1), gmail="a", seller="",
                           host="", position=1, reason="x", ok=False,
                           seconds=1.0, captcha_rounds=0)
    monkeypatch.setattr(store_signins, "record",
                        lambda s, **k: (_ for _ in ()).throw(RuntimeError("down")))
    builder._record_signin(settings, builder.Build(index=1), gmail="a", seller="",
                           host="", position=1, reason="x", ok=False,
                           seconds=1.0, captcha_rounds=0)


def test_the_host_gate_sets_aside_exits_on_a_bad_host_and_frees_them_back(
        make_settings, tmp_path, monkeypatch):
    from geelark_farm.store import signins as store_signins

    settings = make_settings(state_dir=tmp_path, store_enabled=True,
                             pools_in_pg=True, host_gate_min=5,
                             host_gate_rate=0.5)
    book = make_book(proxies=2)          # 10.0.0.0 and 10.0.0.1
    monkeypatch.setattr(store_signins, "host_rates", lambda s, days=7: [
        {"key": "10.0.0.0", "ok": 1, "n": 10, "rate": 0.1},
        {"key": "10.0.0.1", "ok": 8, "n": 10, "rate": 0.8}])

    outcome = builder.gate_hosts(book, settings)

    assert outcome["gated"] == [book.proxies._rows[0].name or
                                book.proxies._rows[0].label]
    assert book.proxies._rows[0].values["Status"] == builder.SUSPECT
    assert book.proxies._rows[0].values["Note"].startswith("Login rate 1/10")
    assert book.proxies._rows[1].values["Status"] in ("", "free")
    # The host recovers: what the gate set aside comes back, and only that.
    monkeypatch.setattr(store_signins, "host_rates", lambda s, days=7: [
        {"key": "10.0.0.0", "ok": 6, "n": 10, "rate": 0.6}])
    outcome = builder.gate_hosts(book, settings)
    assert len(outcome["ungated"]) == 1
    assert book.proxies._rows[0].values["Status"] in ("", "free")
    # A suspect the captcha tally made is not the gate's to free.
    book.proxies.fail(book.proxies._rows[1], builder.SUSPECT,
                      note="Suspect - 3 Google challenges today")
    monkeypatch.setattr(store_signins, "host_rates", lambda s, days=7: [
        {"key": "10.0.0.1", "ok": 9, "n": 10, "rate": 0.9}])
    assert builder.gate_hosts(book, settings)["ungated"] == []
    assert book.proxies._rows[1].values["Status"] == builder.SUSPECT


def test_the_keeper_runs_the_ladder_and_the_gate_only_with_the_store(
        make_settings, tmp_path, monkeypatch):
    import inspect

    src = inspect.getsource(builder.sync_sheet)
    assert 'step("retried", lambda: _revive_ladder(settings))' in src
    assert 'step("hosts", lambda: gate_hosts(book, settings))' in src
    assert "pools_in_pg" in src.split('step("retried"')[0][-400:]
    assert "retried" in builder.STEP_NAMES and "hosts" in builder.STEP_NAMES


def test_a_text_captcha_changes_the_exit_and_tries_the_same_address_again(
        device, settings, drive):
    book = make_book(gmails=2, proxies=3)
    build = drive(book, settings,
                  google=[Outcome("fatal", "captcha_text"), SIGNED_IN])

    assert build.ok and build.gmail == "g0@example.com", "the same address"
    assert len(device.proxies_set) == 1, "one exit change"
    assert [r.credentials.email for r in book.gmails.available] == [
        "g1@example.com"], "the other was never touched"
    assert not any(reason == "captcha_text" for _, reason, _ in build.tried), (
        "the address was not marked for the exit's fault")


def test_a_second_text_captcha_in_one_build_is_a_refusal_like_any_other(
        device, settings, drive):
    """Once per build: an exit change that did not help is not repeated."""
    text = Outcome("fatal", "captcha_text")
    book = make_book(gmails=2, proxies=3)
    build = drive(book, settings, google=[text, text, SIGNED_IN])

    assert len(device.proxies_set) == 1
    assert build.status == "phone_distrusted", (
        "the second one is distrust: the phone goes, the address climbs "
        "the ladder")
    assert [r.credentials.email for r in book.gmails.available] == [
        "g1@example.com"]




def test_the_phones_clock_follows_its_exit(make_settings, tmp_path, monkeypatch):
    """Every exit is in Europe and every phone kept a US clock - six hours
    of dissonance Google can read (2026-09-11)."""
    from types import SimpleNamespace

    from geelark_farm import geo

    settings = make_settings(state_dir=tmp_path, geo_align=True)
    # GeeLark silent here; the address lookup is the fallback path. The
    # remembered places are the process's own dict, so an earlier test's
    # exit would answer for this one.
    monkeypatch.setattr(geo, "_memory", {})
    monkeypatch.setattr(geo, "by_proxy", lambda *a, **k: None)
    monkeypatch.setattr(geo, "timezone_for", lambda s, ip: {
        "212.8.252.6": "Europe/Amsterdam", "1.1.1.1": ""}.get(ip, ""))
    ran = []
    monkeypatch.setattr(builder.shell, "run",
                        lambda c, p, cmd, **k: ran.append(cmd) or "CEST")
    exit_row = SimpleNamespace(values={"Last Exit IP": "212.8.252.6"},
                               proxy=SimpleNamespace(host="10.0.0.9"))

    assert builder._align_clock(None, settings, "P", exit_row) == "Europe/Amsterdam"
    assert ran == ["settings put global auto_time_zone 0; "
                   "setprop persist.sys.timezone Europe/Amsterdam; date +%Z"]
    # No exit IP recorded yet: the proxy's host is the address.
    assert builder._exit_ip(SimpleNamespace(values={}, proxy=SimpleNamespace(
        host="1.1.1.1"))) == "1.1.1.1"
    ran.clear()
    assert builder._align_clock(None, settings, "P", SimpleNamespace(
        values={}, proxy=SimpleNamespace(host="1.1.1.1"))) == ""
    assert ran == [], "nothing known: the clock stays"
    # Off, and a zone that is not a zone, do nothing.
    off = make_settings(state_dir=tmp_path, geo_align=False)
    assert builder._align_clock(None, off, "P", exit_row) == ""
    monkeypatch.setattr(geo, "timezone_for", lambda s, ip: "x; rm -rf /")
    assert builder._align_clock(None, settings, "P", exit_row) == ""
    assert ran == []
    # A shell that refuses is a warning, not a failed build.
    monkeypatch.setattr(geo, "timezone_for", lambda s, ip: "Europe/Amsterdam")
    monkeypatch.setattr(builder.shell, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    assert builder._align_clock(None, settings, "P", exit_row) == ""


def test_the_clock_asks_geelark_before_the_address_lookup(
        make_settings, tmp_path, monkeypatch):
    """For a whole day every build logged "could not place exit ... name
    resolution" and no phone's clock was ever set: this server cannot
    resolve ip-api.com. GeeLark's own check answers for every proxy we
    own, so it goes first and the address lookup is what is left when it
    says nothing (2026-09-12)."""
    from types import SimpleNamespace

    from geelark_farm import geo

    settings = make_settings(state_dir=tmp_path, geo_align=True)
    asked = {}

    def by_proxy(client, proxy, s, *, also=""):
        asked.update(proxy=proxy, also=also)
        return {"tz": "America/New_York", "cc": "US", "ip": "185.68.81.45"}

    monkeypatch.setattr(geo, "known", lambda s, ip: None)
    monkeypatch.setattr(geo, "by_proxy", by_proxy)
    monkeypatch.setattr(geo, "timezone_for", lambda s, ip: "Europe/Amsterdam")
    ran = []
    monkeypatch.setattr(builder.shell, "run",
                        lambda c, p, cmd, **k: ran.append(cmd) or "EDT")
    exit_proxy = SimpleNamespace(host="10.0.0.9")
    row = SimpleNamespace(values={"Last Exit IP": "212.8.252.6"},
                          proxy=exit_proxy)

    assert builder._align_clock(None, settings, "P", row) == "America/New_York"
    assert asked == {"proxy": exit_proxy, "also": "212.8.252.6"}, (
        "the row's own exit is filed too, so the sign-in record has a country")
    assert "setprop persist.sys.timezone America/New_York" in ran[0]

    # And nobody is asked at all when the build's own proxy check already
    # said where the exit is - which is the ordinary path.
    asked.clear(), ran.clear()
    monkeypatch.setattr(geo, "known",
                        lambda s, ip: {"tz": "Europe/Amsterdam", "cc": "NL"})
    assert builder._align_clock(None, settings, "P", row) == "Europe/Amsterdam"
    assert asked == {}, "the check that claimed the proxy was the lookup"


def test_the_sign_in_record_carries_age_exit_country_touch_and_dumps(
        make_settings, tmp_path, monkeypatch):
    import inspect

    from geelark_farm import geo
    from geelark_farm.store import signins as store_signins

    settings = make_settings(state_dir=tmp_path, store_enabled=True)
    rows = []
    monkeypatch.setattr(store_signins, "record",
                        lambda s, **k: rows.append(k) or True)
    monkeypatch.setattr(geo, "country_for", lambda s, ip: "NL" if ip else "")
    builder._record_signin(settings, builder.Build(index=1, serial="7"),
                           gmail="a@x.com", seller="s", host="h", position=1,
                           reason="signed_in", ok=True, seconds=90.0,
                           captcha_rounds=0, age_seconds=140.0,
                           exit_ip="212.8.252.6", touch="kernel", dumps=9)
    assert rows[0]["age_seconds"] == 140.0 and rows[0]["exit_country"] == "NL"
    assert rows[0]["touch"] == "kernel" and rows[0]["dumps"] == 9
    src = inspect.getsource(builder.build_one)
    for needle in ("age_seconds=attempt_started - phone_made_at",
                   "exit_ip=_exit_ip(proxy_row)",
                   "touch=_touch_method(phone_id)",
                   "_align_clock(client, settings, phone_id, proxy_row)",
                   "shell.pause(*SIGN_IN_STAGGER_SECONDS)"):
        assert needle in src, needle
    assert "_align_clock(client, settings, phone_id, replacement)" in \
        inspect.getsource(builder._new_exit)
    monkeypatch.setattr(builder.shell, "_touch_ready", {"P": (1.0, 1.0), "Q": None})
    assert builder._touch_method("P") == "kernel"
    assert builder._touch_method("Q") == "input"
    assert builder._touch_method("R") == "input"
