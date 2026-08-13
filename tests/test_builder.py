"""The branching a build does when something fails.

This is where the money is. Every case below was chosen because getting it
wrong is silent and expensive: burning three Gmails against one bad exit
address, handing a signed-in account back to the pool, or putting two phones
behind one proxy. None of them raises.
"""

from __future__ import annotations

import threading

import pytest

from geelark_farm import builder, failures
from geelark_farm.flows.router import Outcome
from geelark_farm.pools import AppPool, Book, GmailPool, PhoneLog, ProxyPool
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

SIGNED_IN = Outcome("success", "signed_in")
INSTALLED = Outcome("success", "installed")


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
                phones=phone_log)
    for pool in (book.gmails, book.proxies, book.apps):
        pool.load()
    return book


class FakeLedger:
    def claim(self, *a, **k): pass
    def release(self, *a, **k): pass


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
    monkeypatch.setattr(builder.phones, "info", lambda *a, **k: {})
    monkeypatch.setattr(builder.phones, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(builder.phones, "stop",
                        lambda *a, **k: setattr(recorder, "stops",
                                                recorder.stops + 1))
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
    assert note == failures.verdict("captcha_shown").advice
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
def test_a_swapped_out_proxy_goes_back_to_the_pool_not_condemned(device, settings,
                                                                 drive):
    """Measured across twelve attempts: every gateway produced both successes
    and these refusals. Condemning one for a single refusal is wrong."""
    book = make_book()
    drive(book, settings, google=[SIGNED_IN],
          app=[Outcome("fatal", "request_rejected"), SIGNED_IN])

    first = book.proxies._rows[0]
    assert first.values["Status"] == "free"
    # said in words, and said to be about the attempt rather than the proxy -
    # this row is the one someone reads when deciding whether to keep buying
    # from this vendor
    note = first.values["Note"]
    assert failures.verdict("request_rejected").seen in note
    assert "not about this proxy" in note


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
    # and they all come back to the pool afterwards, with what was seen
    assert len(book.proxies.available) == 3
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
    dead = builder.check_free_proxies(None, book)

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

    builder.check_free_proxies(None, book)

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


# ------------------------------------------------------- refreshing an exit
@pytest.fixture
def sx(monkeypatch):
    """sx.org answering, and a record of what it was asked for."""
    calls: list[str] = []
    monkeypatch.setattr(builder.sxorg, "refresh",
                        lambda key, port_id: calls.append(str(port_id)))
    return calls


def with_port_ids(book, exit_ip="9.9.9.9"):
    """Give every proxy row a Port ID and a known exit, as a refreshable proxy
    would have. Needs PROXY_HEADERS_OPTIONAL: the live sheet dropped both
    columns, since the Unlimited product has no Port ID to put in one."""
    for offset, resource in enumerate(book.proxies._rows):
        book.proxies._set(resource, {"Port ID": str(100 + offset),
                                     "Last Exit IP": exit_ip})


def test_a_refusal_refreshes_the_proxy_before_taking_another(device, settings,
                                                             drive, sx,
                                                             monkeypatch):
    """The host, port and credentials do not change, so the phone needs no
    update call - which makes this cheaper than another proxy in every way."""
    seen = ["9.9.9.9", "8.8.8.8"]         # before the refresh, and after
    monkeypatch.setattr(builder.proxy_mod, "check",
                        lambda *a, **k: {"outboundIP": seen.pop(0)})
    settings = settings.__class__(**{**settings.__dict__,
                                     "sxorg_api_key": "KEY"})
    book = make_book(proxy_headers=PROXY_HEADERS_OPTIONAL)
    with_port_ids(book)
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[Outcome("fatal", "request_rejected"), SIGNED_IN])

    assert build.ok
    assert sx == ["100"]                  # the proxy it already had
    assert device.proxies_set == []       # no second proxy was taken
    assert book.proxies._rows[0].values["Last Exit IP"] == "8.8.8.8"
    assert book.proxies._rows[0].values["Last Refresh"].endswith(" x1")


def test_a_refresh_that_lands_on_the_same_address_is_not_a_new_exit(
        device, settings, drive, sx, monkeypatch):
    """It spent one of the day's three and achieved nothing. Retrying into it
    would meet the same refusal and call it a second opinion."""
    monkeypatch.setattr(builder.proxy_mod, "check",
                        lambda *a, **k: {"outboundIP": "9.9.9.9"})
    settings = settings.__class__(**{**settings.__dict__,
                                     "sxorg_api_key": "KEY"})
    book = make_book(proxy_headers=PROXY_HEADERS_OPTIONAL)
    with_port_ids(book, exit_ip="9.9.9.9")
    drive(book, settings, google=[SIGNED_IN],
          app=[Outcome("fatal", "request_rejected"), SIGNED_IN])

    assert sx == ["100"]
    assert device.proxies_set == ["10.0.0.1"]      # fell through to the next


def test_the_daily_allowance_is_read_from_the_sheet(device, settings, drive,
                                                    sx, monkeypatch):
    """It is the vendor's allowance, and it does not reset when a run ends."""
    import time as real_time

    settings = settings.__class__(**{**settings.__dict__,
                                     "sxorg_api_key": "KEY"})
    book = make_book(proxy_headers=PROXY_HEADERS_OPTIONAL)
    with_port_ids(book)
    today = real_time.strftime("%Y-%m-%d")
    book.proxies._set(book.proxies._rows[0],
                      {"Last Refresh": f"{today} x{builder.sxorg.REFRESHES_PER_DAY}"})
    drive(book, settings, google=[SIGNED_IN],
          app=[Outcome("fatal", "request_rejected"), SIGNED_IN])

    assert sx == []                                # nothing left to spend
    assert device.proxies_set == ["10.0.0.1"]


def test_a_proxy_with_no_port_id_is_never_refreshed(device, settings, drive, sx):
    """The Unlimited product does not appear in the vendor's port listing, so a
    blank Port ID means 'cannot be refreshed', not 'not filled in yet'."""
    settings = settings.__class__(**{**settings.__dict__,
                                     "sxorg_api_key": "KEY"})
    book = make_book()
    drive(book, settings, google=[SIGNED_IN],
          app=[Outcome("fatal", "request_rejected"), SIGNED_IN])

    assert sx == []
    assert device.proxies_set == ["10.0.0.1"]


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
def test_an_empty_gmail_tab_is_named_as_such(device, settings, drive):
    book = make_book(gmails=0)
    build = drive(book, settings, google=[])

    assert build.status == "no_usable_gmail"
    assert device.created == 1                 # the phone still gets stopped
    assert device.stops == 1


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
    book = make_book(gmails=0)
    drive(book, settings, google=[])

    written = book.phones._ws.rows[0]
    assert written[PHONE_HEADERS.index("Serial")] == "622"
    # the tab answers "can I use this phone", in one of three words
    assert written[PHONE_HEADERS.index("Status")] == "incomplete"
    # and the note says why in words. The token is in the Status column's
    # vocabulary and in the terminal summary; this cell is prose.
    note = written[PHONE_HEADERS.index("Note")]
    assert note == ("Stopped short: the Gmails tab has no unused address left.")


# ------------------------------------- acting on what the operator marked
class FakePhoneLog:
    """A Phones tab that answers `marked` and records what was deleted."""

    DONE, FAILED, UNUSED = "done", "failed", "unused"

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
    it retired had already signed in fine on earlier phones. So it is not
    marked with the reason, which is what `fail` would do and would mean the
    account is bad. It gets its own word instead."""
    book = make_book(apps=2)
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[Outcome("fatal", "email_code_required"), SIGNED_IN])

    assert build.ok and build.app_account == "a1@example.com"
    challenged = book.apps._rows[0]
    assert challenged.values["Status"] == AppPool.challenged_status
    assert challenged.values["Status"] != "email_code_required"
    assert "Challenged" in challenged.values["Note"]


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
            == [AppPool.challenged_status] * 2)


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
    # both were challenged, neither judged - so neither carries a reason, and
    # neither is left claimed, which was the bug this test was written for
    assert ([r.values["Status"] for r in book.apps._rows]
            == [AppPool.challenged_status] * 2)
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
