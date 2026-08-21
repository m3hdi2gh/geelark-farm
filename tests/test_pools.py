"""Claiming from the resource tabs.

Every mistake this file guards against costs an account rather than a minute.
A Gmail handed out twice signs one address into two phones, which is how
accounts get locked; a Gmail handed back after it signed in does the same thing
one run later. Neither raises - both just quietly spend the stock.
"""

from __future__ import annotations

import threading

import pytest

from geelark_farm.pools import AppPool, GmailPool, HistoryLog, PhoneLog, ProxyPool

# The tabs as they are. Columns are located by header name, so these are the
# real shapes rather than a superset - a test that passes against columns the
# sheet does not have proves less than it looks like it does.
GMAIL_HEADERS = ["Purchase Date", "Seller", "Address", "Password", "2FA Secret",
                 "Used Date", "Phone Serial", "Status", "Note"]
PROXY_HEADERS = ["Name", "Proxy String", "Expires", "Last Exit IP",
                 "Used By", "Status", "Note"]
APP_HEADERS = ["Address", "Password", "2FA Secret", "Phone Serial", "Status",
               "Note"]
PHONE_HEADERS = ["Created", "Serial", "State", "Phone ID", "Proxy",
                 "Gmail", "GPT Account", "Status", "Note"]

# Columns the code still understands but this sheet no longer carries: the
# split-out parts, for someone filling the tab by hand, and the sx.org refresh
# hook, which needs a `Port ID` the Unlimited product does not have. Tests that
# exercise either build their worksheet from this instead.
PROXY_HEADERS_OPTIONAL = ["Name", "Proxy String", "Host", "Port", "Username",
                          "Password", "Port ID", "Expires", "Last Exit IP",
                          "Last Refresh", "Used By", "Status", "Note"]

SECRET = "JBSWY3DPEHPK3PXP"


class FakeWorksheet:
    """Enough gspread to answer a read and record the writes."""

    #: gspread exposes the sheet's numeric id, which `delete_rows` addresses
    #: the tab by. Anything will do here; it only has to exist.
    id = 1

    def __init__(self, headers: list[str], rows: list[list[str]]):
        self.headers = headers
        self.rows = [list(r) + [""] * (len(headers) - len(r)) for r in rows]
        self.writes: list[dict] = []
        self.deleted_rows: list[int] = []

    def get_all_values(self):
        return [self.headers, *self.rows]

    #: gspread reaches the workbook through the worksheet, and `delete_rows`
    #: goes that way. One object answers both here.
    @property
    def spreadsheet(self):
        return self

    def row_values(self, _index):
        return self.headers

    def append_row(self, values, **_kwargs):
        # The Sheets append API places the row server-side; here the end of
        # the list is the same thing.
        self.rows.append(list(values) + [""] * (len(self.headers) - len(values)))

    def batch_update(self, payload):
        """Both shapes gspread calls this with.

        A list of cell ranges is a worksheet write; a dict of `requests` is the
        workbook-level API, which is how a row is deleted. They arrive at the
        same method name, so this dispatches on the payload.

        gspread applies the write; so does this, or a second read would
        disagree with the sheet the caller believes it just changed.
        """
        if isinstance(payload, dict):
            for request in payload.get("requests", []):
                span = request["deleteDimension"]["range"]
                index = span["startIndex"]           # 0-based, header included
                self.deleted_rows.append(index + 1)
                if 0 <= index - 1 < len(self.rows):
                    del self.rows[index - 1]
            return None
        for item in payload:
            self.writes.append(item)
            cell = item["range"].split(":")[0]
            column = ord(cell[0]) - 65
            index = int(cell[1:]) - 2          # data rows start at sheet row 2
            while len(self.rows) <= index:
                self.rows.append([""] * len(self.headers))
            values = item["values"][0]
            self.rows[index][column:column + len(values)] = values
        return None


def gmail_pool(rows) -> GmailPool:
    pool = GmailPool(FakeWorksheet(GMAIL_HEADERS, rows), GMAIL_HEADERS,
                     threading.Lock())
    pool.load()
    return pool


def gmail_row(address: str, status: str = "") -> list[str]:
    return ["2026-08-01", "seller", address, "pw", SECRET, "", "", status, ""]


def proxy_pool(rows, headers=None) -> ProxyPool:
    headers = headers or PROXY_HEADERS
    pool = ProxyPool(FakeWorksheet(headers, rows), headers, threading.Lock())
    pool.load()
    return pool


def proxy_row(string: str, status: str = "free", used_by: str = "",
              headers=None, name: str = "") -> list[str]:
    headers = headers or PROXY_HEADERS
    row = [""] * len(headers)
    row[headers.index("Name")] = name
    row[headers.index("Proxy String")] = string
    row[headers.index("Used By")] = used_by
    row[headers.index("Status")] = status
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
def test_a_released_proxy_goes_back_as_free_not_blank():
    """That column is also the record of whether a proxy works, and a blank
    there reads as 'never checked' rather than 'free'."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:user:pass")])
    claimed = pool.claim()
    pool.release(claimed, note="request_rejected seen through it")
    assert claimed.values["Status"] == "free"
    assert len(pool.available) == 1


def test_a_proxy_is_named_by_the_panel_name_when_it_has_one():
    """"proxy SX13 is dead" is something to act on; a host and port send you
    comparing strings across two windows."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:u:p", name="SX13")])
    assert pool.claim().label.startswith("SX13 (")

    bare = proxy_pool([proxy_row("1.2.3.4:9999:u:p")])
    assert bare.claim().label.startswith("socks5://")


def test_a_used_proxy_keeps_every_phone_it_has_carried():
    """A proxy that has carried two phones has to name both, or the column
    cannot answer 'what is on this exit'."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:user:pass", used_by="600")])
    claimed = pool.claim()
    pool.spend(claimed, serial="622")
    assert claimed.values["Used By"] == "600, 622"


def test_a_proxy_is_parsed_from_its_parts_when_the_string_is_missing():
    """Someone filling the tab by hand fills the columns. The sheet carries
    only the joined string now, but the code still understands both."""
    row = [""] * len(PROXY_HEADERS_OPTIONAL)
    for name, value in (("Host", "1.2.3.4"), ("Port", "9999"),
                        ("Username", "u"), ("Password", "p")):
        row[PROXY_HEADERS_OPTIONAL.index(name)] = value
    pool = proxy_pool([row], PROXY_HEADERS_OPTIONAL)
    claimed = pool.claim()
    assert claimed.proxy.host == "1.2.3.4"
    assert claimed.proxy.port == 9999
    assert claimed.proxy.username == "u"


def test_an_ok_proxy_is_taken_as_already_spoken_for():
    """`ok` means a phone is behind it. Handing it out again would put two
    devices on one exit address."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:u:p", status="on a phone"),
                       proxy_row("5.6.7.8:9999:u:p")])
    assert pool.claim().proxy.host == "5.6.7.8"


def test_a_proxy_is_freed_when_its_phone_is_gone():
    """`ok` means a phone is behind it, and nothing undid that when the phone
    was deleted - so every deleted phone quietly took a working proxy out of
    circulation. Thirteen of twenty-two were locked to phones long gone while a
    run failed for want of one (2026-08-11)."""
    pool = proxy_pool([
        proxy_row("1.2.3.4:9999:u:p", status="on a phone", used_by="650"),
        proxy_row("5.6.7.8:9999:u:p", status="on a phone", used_by="651")])

    freed = pool.reclaim({"5.6.7.8:9999"})        # only 651 still exists

    assert [r.proxy.host for r in freed] == ["1.2.3.4"]
    assert [r.proxy.host for r in pool.available] == ["1.2.3.4"]
    # the stale serial goes with it, or the column keeps naming a dead phone
    assert freed[0].values["Used By"] == ""
    # the one still in use is untouched
    assert pool._rows[1].values["Status"] == "on a phone"


def test_reclaiming_never_takes_a_row_a_run_is_holding():
    """`in_use` is a build that has claimed a proxy and not yet created its
    phone. Freeing that would hand the same exit to two devices."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:u:p", status="claimed")])

    assert pool.reclaim(set()) == []
    assert pool._rows[0].values["Status"] == "claimed"


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
    assert written[PHONE_HEADERS.index("State")] == "unused"


def test_a_second_phone_lands_on_the_next_row():
    worksheet = FakeWorksheet(PHONE_HEADERS, [])
    log = PhoneLog(worksheet, PHONE_HEADERS, threading.Lock())
    assert log.start(Serial="622") == 2
    assert log.start(Serial="623") == 3


# ------------------------------------------------------------- duplicates
def test_the_same_address_twice_is_only_handed_out_once():
    """Two rows for one address sign it into two phones, and their 2FA codes
    race each other. The single-row sheet checked this; the check was lost when
    that module went, and a duplicate app account was in the tab within the day
    (2026-08-13, rows 5 and 14)."""
    pool = gmail_pool([gmail_row("a@example.com"),
                       gmail_row("b@example.com"),
                       gmail_row("A@Example.com")])      # same address, cased

    assert [r.credentials.email for r in pool.available] == ["a@example.com",
                                                             "b@example.com"]
    late = pool._rows[2]
    assert late.error and "duplicate of row 2" in late.error
    assert pool.claim().credentials.email == "a@example.com"
    assert pool.claim().credentials.email == "b@example.com"
    assert pool.claim() is None                          # never the third


def test_the_same_proxy_twice_would_put_two_phones_on_one_exit():
    """Identical rows - the case this check exists for."""
    pool = proxy_pool([proxy_row("1.2.3.4:9999:u:p"),
                       proxy_row("1.2.3.4:9999:u:p")])

    assert len(pool.available) == 1
    assert "duplicate of row 2" in pool._rows[1].error


def test_two_credentials_on_one_gateway_are_two_proxies():
    """This test used to assert the opposite, and it was wrong. Measured on
    the live account: ten usernames on `79.127.168.43:50101` returned ten
    different outbound addresses - 138.36.95.62, 209.101.201.161,
    185.228.193.23 and so on (2026-08-14). Reading them as one proxy and nine
    duplicates would refuse nine working exits.
    """
    pool = proxy_pool([proxy_row("1.2.3.4:9999:u:p"),
                       proxy_row("1.2.3.4:9999:other:pass")])

    assert len(pool.available) == 2
    assert pool.broken == []


def test_a_row_that_was_already_unusable_keeps_its_own_error():
    """The reason a row cannot be used is more useful than 'duplicate'."""
    pool = gmail_pool([gmail_row("a@example.com"), gmail_row("not-an-address")])

    assert "not an email address" in pool._rows[1].error


# ------------------------------------ what happens to the serial on the way out
def test_every_way_off_a_phone_drops_the_serial():
    """The serial column names the phone that has this row *now*. Only
    `retire` cleared it, and the reasoning was the same for all of them - so an
    app account freed because its phone was marked failed went back into the
    pool still naming that phone, and three rows in the live tab said
    `Phone Serial 684` about a phone deleted hours earlier (2026-08-13).
    """
    for leaving in ("release", "retire", "set_aside"):
        pool = AppPool(FakeWorksheet(APP_HEADERS, [
            ["a@example.com", "pw", SECRET, "", "", ""]]), APP_HEADERS,
            threading.Lock())
        pool.load()
        row = pool._rows[0]
        pool.spend(row, serial="684", note="On phone 684.")
        assert row.values["Phone Serial"] == "684"

        getattr(pool, leaving)(row, note="whatever became of it")

        assert row.values["Phone Serial"] == "", (
            f"{leaving} left the row naming a phone it is no longer on")


def test_a_proxy_let_go_stops_naming_its_phone_too():
    """`Used By` is the same column by another name."""
    pool = ProxyPool(FakeWorksheet(PROXY_HEADERS,
                                   [proxy_row("10.0.0.1:9999:u:p")]),
                     PROXY_HEADERS, threading.Lock())
    pool.load()
    row = pool._rows[0]
    pool.spend(row, serial="691", note="On phone 691.")
    assert row.values["Used By"] == "691"

    pool.release(row, note="nothing is behind it")

    assert row.values["Used By"] == ""
    assert pool.status_of(row) == "free"


def test_one_gateway_can_carry_more_than_one_proxy():
    """Host and port alone looked right while every proxy was its own gateway.
    A vendor that multiplexes on the username breaks it: ten live proxies on
    one endpoint would be read as one row and nine duplicates, and the tab
    would refuse nine working exits (2026-08-14)."""
    pool = ProxyPool(FakeWorksheet(PROXY_HEADERS, [
        proxy_row("79.127.168.43:50101:user_1:pw"),
        proxy_row("79.127.168.43:50101:user_2:pw"),
        proxy_row("79.127.168.43:50101:user_3:pw"),
    ]), PROXY_HEADERS, threading.Lock())
    pool.load()

    assert pool.broken == []
    assert len(pool.available) == 3



# --------------------------------------------------- keeping the dropdowns honest
class FakeBookLists:
    """A Lists tab and the three pools sync_lists reads the vocabulary from."""

    def __init__(self, existing):
        headers = ["Gmail Statuses", "GPT Statuses", "Proxy Statuses",
                   "Phone Statuses"]
        rows = [[existing[h][i] if i < len(existing[h]) else ""
                 for h in headers] for i in range(12)]
        self.sheet = FakeWorksheet(headers, rows)


def lists_book(existing):
    from geelark_farm.pools import Book
    tab = FakeBookLists(existing).sheet
    book = Book(gmails=gmail_pool([]), proxies=proxy_pool([]),
                apps=AppPool(FakeWorksheet(APP_HEADERS, []), APP_HEADERS,
                             threading.Lock()),
                phones=PhoneLog(FakeWorksheet(PHONE_HEADERS, []), PHONE_HEADERS,
                                threading.Lock()),
                lists=tab)
    return book, tab


def test_a_reason_a_flow_grew_reaches_the_dropdown():
    """The column went on refusing `wrong_2fa_code` after the flow grew it -
    "Input must fall within specified range" against a status a run had just
    written (2026-08-16)."""
    book, tab = lists_book({"Gmail Statuses": [], "GPT Statuses": [],
                            "Proxy Statuses": [], "Phone Statuses": []})

    wanted = book.sync_lists()

    assert "wrong_2fa_code" in wanted["GPT Statuses"]
    assert "change ip" in wanted["Proxy Statuses"]
    written = {w["values"][0][0] for w in tab.writes}
    assert "wrong_2fa_code" in written


def test_dropdowns_that_already_agree_are_not_rewritten():
    """Called every session now, so a run that changes nothing must send
    nothing - a write against a tab that has not moved is an API call spent to
    learn what the read already said."""
    book, tab = lists_book({"Gmail Statuses": [], "GPT Statuses": [],
                            "Proxy Statuses": [], "Phone Statuses": []})
    book.sync_lists()
    tab.writes.clear()

    book.sync_lists()

    assert tab.writes == []


# ------------------------------------------------------------- the History tab
def history_book():
    from geelark_farm.pools import Book
    tab = FakeWorksheet(HistoryLog.HEADERS, [])
    book = Book(gmails=gmail_pool([]), proxies=proxy_pool([]),
                apps=AppPool(FakeWorksheet(APP_HEADERS, []), APP_HEADERS,
                             threading.Lock()),
                phones=PhoneLog(FakeWorksheet(PHONE_HEADERS, []), PHONE_HEADERS,
                                threading.Lock()),
                history=HistoryLog(tab, threading.Lock()))
    return book, tab


def test_a_history_row_carries_when_and_which_machine():
    """The two columns the tab exists for: two machines share one spreadsheet
    and nothing else, so a row that does not say who wrote it answers half the
    question it was kept for."""
    book, tab = history_book()

    book.record_history(Serial="762", Event="ready", Gmail="g@example.com")

    row = dict(zip(HistoryLog.HEADERS, tab.rows[0], strict=True))
    assert row["Serial"] == "762" and row["Event"] == "ready"
    assert row["When"] and row["Machine"]


def test_a_workbook_without_a_history_tab_still_works():
    """History is a record of the work, not part of it - a build must not fail
    because its footnote could not be written."""
    from geelark_farm.pools import Book
    book = Book(gmails=gmail_pool([]), proxies=proxy_pool([]),
                apps=AppPool(FakeWorksheet(APP_HEADERS, []), APP_HEADERS,
                             threading.Lock()),
                phones=PhoneLog(FakeWorksheet(PHONE_HEADERS, []), PHONE_HEADERS,
                                threading.Lock()))

    book.record_history(Serial="1", Event="ready")     # simply nothing happens


def test_a_history_append_that_fails_does_not_raise():
    book, tab = history_book()

    def refuse(*a, **k):
        raise ConnectionError("mid-write reset")
    tab.append_row = refuse

    book.record_history(Serial="1", Event="ready")     # logged, not raised


# ------------------------------------------ not spending writes to say nothing
def test_an_unchanged_exit_is_not_rewritten():
    """`record_exit` is called for every free proxy every sync, and these
    exits are mostly stable - writing an unchanged value spent one of the
    sixty writes a minute Google allows to say nothing, and a sync of twenty-one
    proxies hit the quota doing it (2026-08-17)."""
    row = proxy_row("1.2.3.4:9999:u:p")
    pool = proxy_pool([row])
    resource = pool._rows[0]
    pool.record_exit(resource, "5.6.7.8")            # first time: a real change
    pool._ws.writes.clear()

    pool.record_exit(resource, "5.6.7.8")            # same value: no write

    assert pool._ws.writes == []
    assert resource.values["Last Exit IP"] == "5.6.7.8"

    pool.record_exit(resource, "9.9.9.9")            # a new exit: written again
    assert pool._ws.writes


# --------------------------------------------- taking the exits in turn
PROXY_HEADERS_ROTATION = [*PROXY_HEADERS, "Times Used", "Last Used"]


def rotating_pool(rows):
    return proxy_pool(rows, headers=PROXY_HEADERS_ROTATION)


def rotation_row(string, uses="", status="free"):
    row = proxy_row(string, status=status, headers=PROXY_HEADERS_ROTATION)
    row[PROXY_HEADERS_ROTATION.index("Times Used")] = uses
    return row


def test_a_never_used_exit_is_preferred_to_any_used_one():
    """A blank count reads as zero, so a fresh proxy goes out before one that
    has already carried a phone - which is what makes the first round cover
    the whole tab."""
    pool = rotating_pool([
        rotation_row("10.0.0.1:9999:u:p", "3"),
        rotation_row("10.0.0.2:9999:u:p", ""),
        rotation_row("10.0.0.3:9999:u:p", "1"),
    ])

    assert [r.proxy.host for r in pool.available] == [
        "10.0.0.2", "10.0.0.3", "10.0.0.1"]


def test_the_least_used_exit_goes_out_next():
    """The top of the tab used to carry every build while the bottom sat idle,
    so a handful of addresses did all the work and collected all the
    suspicion (2026-08-17)."""
    pool = rotating_pool([
        rotation_row("10.0.0.1:9999:u:p", "7"),
        rotation_row("10.0.0.2:9999:u:p", "2"),
        rotation_row("10.0.0.3:9999:u:p", "4"),
    ])

    assert pool.claim().proxy.host == "10.0.0.2"


def test_claiming_counts_the_use_and_notes_the_time():
    pool = rotating_pool([rotation_row("10.0.0.1:9999:u:p", "4"),
                          rotation_row("10.0.0.2:9999:u:p", "9")])

    first = pool.claim()

    assert first.values["Times Used"] == "5"         # counted, so it goes last
    assert first.values["Last Used"]                 # written, not blank
    assert pool.status_of(first) == pool.claimed_status


def test_a_whole_round_is_used_before_any_repeat():
    """The operator's words: one round over every proxy, then the next round
    starts repeating. Claim, release, claim again - and nothing comes back
    until everything else has been out.

    This is the test a timestamp could not pass: eight claims land inside the
    same second, every stamp came out equal, and the top row went out all
    eight times.
    """
    pool = rotating_pool(
        [rotation_row(f"10.0.0.{i}:9999:u:p") for i in range(4)])
    taken = []

    for _ in range(8):                     # two full rounds of four
        resource = pool.claim()
        taken.append(resource.proxy.host)
        pool.release(resource, note="back")

    assert sorted(taken[:4]) == ["10.0.0.0", "10.0.0.1", "10.0.0.2", "10.0.0.3"]
    assert sorted(taken[4:]) == sorted(taken[:4])
    assert taken[:4] == taken[4:]          # and the rounds keep the same order


def test_a_count_nobody_can_read_does_not_stop_the_run():
    """The column is in the operator's sheet, so it can hold anything a hand
    types into it. An unreadable count reads as never used."""
    pool = rotating_pool([rotation_row("10.0.0.1:9999:u:p", "2"),
                          rotation_row("10.0.0.2:9999:u:p", "lots")])

    assert pool.claim().proxy.host == "10.0.0.2"


def test_a_tab_without_the_column_behaves_as_it_always_did():
    """Every row reads blank, so the order is sheet order - which is what the
    pool did before the column existed."""
    pool = proxy_pool([proxy_row(f"10.0.0.{i}:9999:u:p") for i in range(3)])

    assert [r.proxy.host for r in pool.available] == [
        "10.0.0.0", "10.0.0.1", "10.0.0.2"]


# ------------------------------------------- making room for a column we add
class NarrowTab:
    """A tab sized to its content, which is what Sheets gives you when nobody
    has widened it by hand."""

    def __init__(self, headers, col_count=None):
        self.title = "Proxy"
        self._headers = list(headers)
        self.col_count = col_count if col_count is not None else len(headers)
        self.added = 0

    def row_values(self, row):
        return list(self._headers)

    def add_cols(self, n):
        self.col_count += n
        self.added += n

    def update_cell(self, row, col, value):
        if col > self.col_count:
            raise RuntimeError(
                f"Range (Proxy!{chr(64 + col)}1) exceeds grid limits. "
                f"Max columns: {self.col_count}")
        self._headers = self._headers + [value]


def test_a_tab_with_no_spare_columns_is_widened_first():
    """The Proxy tab was exactly six columns wide, so writing a seventh header
    was refused as "exceeds grid limits" - the column was never added, and the
    rotation that reads it saw every proxy as never used (2026-08-17)."""
    from geelark_farm.pools import ensure_columns
    tab = NarrowTab(["Name", "Proxy String", "Last Exit IP", "Used By",
                     "Status", "Note"])

    headers = ensure_columns(tab, "Times Used", "Last Used")

    assert headers[-2:] == ["Times Used", "Last Used"]
    assert tab.added == 2                    # room made for each of them


def test_a_tab_with_room_to_spare_is_not_widened():
    from geelark_farm.pools import ensure_columns
    tab = NarrowTab(["Name", "Status"], col_count=26)

    assert ensure_columns(tab, "Times Used") == ["Name", "Status", "Times Used"]
    assert tab.added == 0


def test_a_column_already_there_is_left_alone():
    from geelark_farm.pools import ensure_columns
    tab = NarrowTab(["Name", "Times Used"])

    assert ensure_columns(tab, "Times Used") == ["Name", "Times Used"]
    assert tab.added == 0


def test_a_column_that_cannot_be_added_does_not_stop_the_run():
    """The tab then behaves as it did before the column existed, which is the
    whole reason `_set` skips unknown columns."""
    from geelark_farm.pools import ensure_columns
    tab = NarrowTab(["Name"])
    tab.add_cols = lambda n: (_ for _ in ()).throw(RuntimeError("no"))

    assert ensure_columns(tab, "Times Used") == ["Name"]


# ------------------------------------ how far a phone actually got
PHONE_APP_HEADERS = [*PHONE_HEADERS, "App"]


def phone_log(rows, headers=None):
    headers = headers or PHONE_APP_HEADERS
    return PhoneLog(FakeWorksheet(headers, rows), headers, threading.Lock())


def phone_row(serial, *, gmail="g@example.com", app="", account="",
              status="incomplete", headers=None):
    headers = headers or PHONE_APP_HEADERS
    line = [""] * len(headers)
    for name, value in (("Serial", serial), ("Gmail", gmail), ("App", app),
                        ("GPT Account", account), ("Status", status),
                        ("Note", "Stopped short: something.")):
        if name in headers:              # a tab from before the column existed
            line[headers.index(name)] = value
    return line


def test_the_row_says_which_of_the_three_steps_a_phone_reached():
    """`Gmail` said Google was in and `GPT Account` said the app account was.
    Nothing recorded the step between them, so `incomplete` covered "waiting
    on an app account" and "the app never installed" with one word and no way
    to tell them apart (2026-08-21)."""
    log = phone_log([
        phone_row("991", app="installed"),      # waiting on an account
        phone_row("992", app=""),               # the app never installed
    ])

    reached = {row["serial"]: row["app"] for row in log.unfinished()}

    assert reached == {"991": "installed", "992": ""}


def test_the_phone_that_only_needs_an_account_is_offered_first():
    """Both cost the same app account - the scarce thing - but one of them
    also needs the install. In this order the same handful of accounts turns
    into ready phones sooner."""
    log = phone_log([
        phone_row("990", app=""),
        phone_row("991", app="installed"),
        phone_row("992", app=""),
        phone_row("993", app="installed"),
    ])

    assert [row["serial"] for row in log.unfinished()][:2] == ["991", "993"]


def test_a_tab_without_the_column_still_offers_everything():
    """Rows written before the column existed read as blank, which is the
    truthful answer - nobody recorded it - and they are still finishable."""
    log = phone_log([phone_row("991", headers=PHONE_HEADERS),
                     phone_row("992", headers=PHONE_HEADERS)],
                    headers=PHONE_HEADERS)

    offered = log.unfinished()

    assert [row["serial"] for row in offered] == ["991", "992"]
    assert all(row["app"] == "" for row in offered)


def test_a_finished_phone_is_not_offered_whatever_the_app_column_says():
    log = phone_log([phone_row("991", app="installed",
                               account="a@example.com", status="ready")])

    assert log.unfinished() == []
