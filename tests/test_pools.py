"""Claiming from the resource tabs.

Every mistake this file guards against costs an account rather than a minute.
A Gmail handed out twice signs one address into two phones, which is how
accounts get locked; a Gmail handed back after it signed in does the same thing
one run later. Neither raises - both just quietly spend the stock.
"""

from __future__ import annotations

import threading

import pytest

from geelark_farm.pools import AppPool, GmailPool, PhoneLog, ProxyPool

GMAIL_HEADERS = ["Purchase Date", "Seller", "Address", "Password", "2FA Secret",
                 "Used Date", "Phone Serial", "Status", "Note"]
PROXY_HEADERS = ["Purchase Date", "Seller", "Proxy String", "Host", "Port",
                 "Username", "Password", "Port ID", "Country", "ASN",
                 "Expires", "Last Exit IP", "Last Refresh", "Used By",
                 "Status", "Note"]
APP_HEADERS = ["Address", "Password", "2FA Secret", "Phone Serial", "Status",
               "Note"]
PHONE_HEADERS = ["Created", "Serial", "Phone ID", "Model", "Region", "Proxy",
                 "Gmail", "GPT Account", "Status", "Note", "State"]

SECRET = "JBSWY3DPEHPK3PXP"


class FakeWorksheet:
    """Enough gspread to answer a read and record the writes."""

    def __init__(self, headers: list[str], rows: list[list[str]]):
        self.headers = headers
        self.rows = [list(r) + [""] * (len(headers) - len(r)) for r in rows]
        self.writes: list[dict] = []

    def get_all_values(self):
        return [self.headers, *self.rows]

    def row_values(self, _index):
        return self.headers

    def batch_update(self, payload):
        # gspread applies the write; so does this, or a second read would
        # disagree with the sheet the caller believes it just changed.
        for item in payload:
            self.writes.append(item)
            cell = item["range"].split(":")[0]
            column = ord(cell[0]) - 65
            index = int(cell[1:]) - 2          # data rows start at sheet row 2
            while len(self.rows) <= index:
                self.rows.append([""] * len(self.headers))
            values = item["values"][0]
            self.rows[index][column:column + len(values)] = values


def gmail_pool(rows) -> GmailPool:
    pool = GmailPool(FakeWorksheet(GMAIL_HEADERS, rows), GMAIL_HEADERS,
                     threading.Lock())
    pool.load()
    return pool


def gmail_row(address: str, status: str = "") -> list[str]:
    return ["2026-08-01", "seller", address, "pw", SECRET, "", "", status, ""]


def proxy_pool(rows) -> ProxyPool:
    pool = ProxyPool(FakeWorksheet(PROXY_HEADERS, rows), PROXY_HEADERS,
                     threading.Lock())
    pool.load()
    return pool


def proxy_row(string: str, status: str = "unused",
              used_by: str = "") -> list[str]:
    row = [""] * len(PROXY_HEADERS)
    row[PROXY_HEADERS.index("Proxy String")] = string
    row[PROXY_HEADERS.index("Used By")] = used_by
    row[PROXY_HEADERS.index("Status")] = status
    return row


# ---------------------------------------------------------------- claiming
def test_the_first_usable_row_is_the_one_taken():
    """'The first usable one' is what the operator sees when they look at the
    tab, so the pool has to mean the same thing by it."""
    pool = gmail_pool([gmail_row("used@example.com", status="ready"),
                       gmail_row("next@example.com")])
    assert pool.claim().credentials.email == "next@example.com"


def test_a_blank_status_is_available_and_a_used_one_is_not():
    pool = gmail_pool([gmail_row("a@example.com"),
                       gmail_row("b@example.com", status="captcha_shown")])
    assert [r.credentials.email for r in pool.available] == ["a@example.com"]


def test_claiming_marks_the_row_so_nothing_else_takes_it():
    """The whole point of writing in_use before handing the row out: without
    it, two workers reaching claim() at once both take the same address."""
    pool = gmail_pool([gmail_row("a@example.com"), gmail_row("b@example.com")])
    first, second = pool.claim(), pool.claim()
    assert first.credentials.email == "a@example.com"
    assert second.credentials.email == "b@example.com"
    assert pool.claim() is None


def test_an_unusable_row_is_never_claimed():
    """Validation happens before a phone exists. A row holding a password in
    the address column must not reach the point where one is created for it."""
    pool = gmail_pool([gmail_row("not-an-address"), gmail_row("a@example.com")])
    assert pool.broken and pool.broken[0].error
    assert pool.claim().credentials.email == "a@example.com"


def test_a_gmail_with_no_2fa_is_usable():
    """Accounts bought without 2FA sign in on the shorter path; an empty secret
    is a fact about the account, not a broken row."""
    row = gmail_row("a@example.com")
    row[GMAIL_HEADERS.index("2FA Secret")] = ""
    pool = gmail_pool([row])
    claimed = pool.claim()
    assert claimed is not None
    assert claimed.credentials.has_authenticator is False


# ----------------------------------------------------------- giving it back
def test_a_released_gmail_can_be_claimed_again():
    """The Gmail that was never tried - fetched as the budget ran out - is
    stock, and has to go back as stock."""
    pool = gmail_pool([gmail_row("a@example.com")])
    claimed = pool.claim()
    pool.release(claimed, note="build ended: budget_exhausted")
    assert [r.credentials.email for r in pool.available] == ["a@example.com"]


def test_a_spent_gmail_is_not_available_again():
    pool = gmail_pool([gmail_row("a@example.com")])
    pool.spend(pool.claim(), serial="622")
    assert pool.available == []


def test_spending_a_gmail_records_which_phone_took_it():
    pool = gmail_pool([gmail_row("a@example.com")])
    claimed = pool.claim()
    pool.spend(claimed, serial="622")
    assert claimed.values["Phone Serial"] == "622"
    assert claimed.values["Used Date"]


def test_a_stuck_row_is_reported_and_released_on_request():
    """A run that dies leaves its claims behind. Nothing frees them on its own,
    so they have to be visible."""
    pool = gmail_pool([gmail_row("a@example.com", status="in_use")])
    assert len(pool.stuck) == 1
    assert pool.available == []
    pool.release(pool.stuck[0])
    assert len(pool.available) == 1


# ----------------------------------------------------------------- proxies
def test_a_released_proxy_goes_back_as_unused_not_blank():
    """That column is also the record of whether a proxy works, and a blank
    there reads as 'never checked' rather than 'free'."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:user:pass")])
    claimed = pool.claim()
    pool.release(claimed, note="request_rejected seen through it")
    assert claimed.values["Status"] == "unused"
    assert len(pool.available) == 1


def test_a_used_proxy_keeps_every_phone_it_has_carried():
    """A proxy that has carried two phones has to name both, or the column
    cannot answer 'what is on this exit'."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:user:pass", used_by="600")])
    claimed = pool.claim()
    pool.spend(claimed, serial="622")
    assert claimed.values["Used By"] == "600, 622"


def test_a_proxy_is_parsed_from_its_parts_when_the_string_is_missing():
    """Someone filling the tab by hand fills the columns."""
    row = [""] * len(PROXY_HEADERS)
    for name, value in (("Host", "1.2.3.4"), ("Port", "9999"),
                        ("Username", "u"), ("Password", "p")):
        row[PROXY_HEADERS.index(name)] = value
    pool = proxy_pool([row])
    claimed = pool.claim()
    assert claimed.proxy.host == "1.2.3.4"
    assert claimed.proxy.port == 9999
    assert claimed.proxy.username == "u"


def test_an_ok_proxy_is_taken_as_already_spoken_for():
    """`ok` means a phone is behind it. Handing it out again would put two
    devices on one exit address."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:u:p", status="ok"),
                       proxy_row("5.6.7.8:9999:u:p")])
    assert pool.claim().proxy.host == "5.6.7.8"


def test_a_proxy_is_freed_when_its_phone_is_gone():
    """`ok` means a phone is behind it, and nothing undid that when the phone
    was deleted - so every deleted phone quietly took a working proxy out of
    circulation. Thirteen of twenty-two were locked to phones long gone while a
    run failed for want of one (2026-08-11)."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:u:p", status="ok", used_by="650"),
                       proxy_row("5.6.7.8:9999:u:p", status="ok", used_by="651")])

    freed = pool.reclaim({"5.6.7.8:9999"})        # only 651 still exists

    assert [r.proxy.host for r in freed] == ["1.2.3.4"]
    assert [r.proxy.host for r in pool.available] == ["1.2.3.4"]
    # the stale serial goes with it, or the column keeps naming a dead phone
    assert freed[0].values["Used By"] == ""
    # the one still in use is untouched
    assert pool._rows[1].values["Status"] == "ok"


def test_reclaiming_never_takes_a_row_a_run_is_holding():
    """`in_use` is a build that has claimed a proxy and not yet created its
    phone. Freeing that would hand the same exit to two devices."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:u:p", status="in_use")])

    assert pool.reclaim(set()) == []
    assert pool._rows[0].values["Status"] == "in_use"


@pytest.mark.parametrize("status", ["dead", "captcha", "expired",
                                    "tls_intercepted", "edge_refused"])
def test_a_condemned_proxy_is_never_claimed(status):
    pool = proxy_pool([proxy_row("1.2.3.4:9999:u:p", status=status)])
    assert pool.claim() is None


# ------------------------------------------------------------ the app pool
def test_the_app_pool_reads_the_same_three_columns():
    pool = AppPool(FakeWorksheet(APP_HEADERS,
                                 [["gpt@example.com", "pw", SECRET, "", "", ""]]),
                   APP_HEADERS, threading.Lock())
    pool.load()
    claimed = pool.claim()
    assert claimed.credentials.email == "gpt@example.com"
    assert claimed.credentials.totp_secret == SECRET


# ---------------------------------------------------------- the Phones tab
def test_a_phone_is_recorded_before_it_is_finished():
    """An interrupted run still has to leave something naming the phone in
    GeeLark's list, or nobody can find what it was paying for."""
    worksheet = FakeWorksheet(PHONE_HEADERS, [])
    log = PhoneLog(worksheet, PHONE_HEADERS, threading.Lock())
    row = log.start(Serial="622", **{"Phone ID": "63224"})
    assert row == 2
    written = worksheet.writes[0]["values"][0]
    assert written[PHONE_HEADERS.index("Serial")] == "622"
    assert written[PHONE_HEADERS.index("Status")] == "building"
    assert written[PHONE_HEADERS.index("State")] == "Unused"


def test_a_second_phone_lands_on_the_next_row():
    worksheet = FakeWorksheet(PHONE_HEADERS, [])
    log = PhoneLog(worksheet, PHONE_HEADERS, threading.Lock())
    assert log.start(Serial="622") == 2
    assert log.start(Serial="623") == 3
