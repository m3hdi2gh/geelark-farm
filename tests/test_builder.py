"""The branching a build does when something fails.

This is where the money is. Every case below was chosen because getting it
wrong is silent and expensive: burning three Gmails against one bad exit
address, handing a signed-in account back to the pool, or putting two phones
behind one proxy. None of them raises.
"""

from __future__ import annotations

import threading

import pytest

from geelark_farm import builder
from geelark_farm.flows.router import Outcome
from geelark_farm.pools import AppPool, Book, GmailPool, PhoneLog, ProxyPool
from tests.test_pools import (
    APP_HEADERS,
    GMAIL_HEADERS,
    PHONE_HEADERS,
    PROXY_HEADERS,
    SECRET,
    FakeWorksheet,
    gmail_row,
    proxy_row,
)

SIGNED_IN = Outcome("success", "signed_in")
INSTALLED = Outcome("success", "installed")


def make_book(*, gmails=2, proxies=2, apps=1) -> Book:
    lock = threading.Lock()
    gmail_pool = GmailPool(
        FakeWorksheet(GMAIL_HEADERS,
                      [gmail_row(f"g{i}@example.com") for i in range(gmails)]),
        GMAIL_HEADERS, lock)
    proxy_pool = ProxyPool(
        FakeWorksheet(PROXY_HEADERS,
                      [proxy_row(f"10.0.0.{i}:9999:u:p") for i in range(proxies)]),
        PROXY_HEADERS, lock)
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


def test_a_bad_app_account_does_not_touch_the_proxy(device, settings, drive):
    book = make_book(apps=2)
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[Outcome("fatal", "wrong_password"), SIGNED_IN])

    assert build.ok and build.app_account == "a1@example.com"
    assert device.proxies_set == []
    assert book.apps._rows[0].values["Status"] == "wrong_password"


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
    assert first.values["Status"] == "unused"
    assert "request_rejected" in first.values["Note"]


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


def test_exit_changes_run_out_without_condemning_the_account(
        device, settings, drive):
    """Refused at the edge past every exit change: the network's verdict, not
    the account's. The account was never judged, so it must go back as stock -
    condemning it would lose a good account to a bad afternoon."""
    book = make_book(proxies=8)
    refused = Outcome("fatal", "network_ssl_rejected")
    build = drive(book, settings, google=[SIGNED_IN],
                  app=[refused] * (builder.MAX_EXIT_CHANGES + 1))

    assert not build.ok
    assert build.status == "network_ssl_rejected"
    assert len(device.proxies_set) == builder.MAX_EXIT_CHANGES
    # the account is back on the shelf, not marked with a network reason
    assert [r.credentials.email for r in book.apps.available] == ["a0@example.com"]


# ------------------------------------------------------- refreshing an exit
@pytest.fixture
def sx(monkeypatch):
    """sx.org answering, and a record of what it was asked for."""
    calls: list[str] = []
    monkeypatch.setattr(builder.sxorg, "refresh",
                        lambda key, port_id: calls.append(str(port_id)))
    return calls


def with_port_ids(book, exit_ip="9.9.9.9"):
    """Give every proxy row a Port ID and a known exit, as a refreshable
    proxy would have."""
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
    book = make_book()
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
    book = make_book()
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
    book = make_book()
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
    assert book.proxies._rows[0].values["Status"] == "ok"
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
    assert written[PHONE_HEADERS.index("Status")] == "no_usable_gmail"
