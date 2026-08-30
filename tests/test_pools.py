"""Claiming from the resource tabs.

Every mistake this file guards against costs an account rather than a minute.
A Gmail handed out twice signs one address into two phones, which is how
accounts get locked; a Gmail handed back after it signed in does the same thing
one run later. Neither raises - both just quietly spend the stock.
"""

from __future__ import annotations

import logging
import re
import threading
import time

import pytest

from geelark_farm.pools import (
    AppPool,
    Book,
    GmailPool,
    HistoryLog,
    PhoneLog,
    ProxyPool,
)

# The tabs as they are. Columns are located by header name, so these are the
# real shapes rather than a superset - a test that passes against columns the
# sheet does not have proves less than it looks like it does.
GMAIL_HEADERS = ["Purchase Date", "Seller", "Address", "Password", "Secret",
                 "Used Date", "Phone Serial", "Status", "Note"]
PROXY_HEADERS = ["Name", "Proxy String", "Expires", "Last Exit IP",
                 "Used By", "Status", "Note"]
APP_HEADERS = ["Address", "Password", "2FA Secret", "Phone Serial", "Status",
               "Note"]
PHONE_HEADERS = ["Created", "Serial", "State", "Phone ID", "Proxy",
                 "Gmail", "GPT Account", "Status", "Note"]

# Columns the code still understands but this sheet no longer carries: the
# split-out parts, for someone filling the tab by hand. Tests that exercise
# them build their worksheet from this instead.
PROXY_HEADERS_OPTIONAL = ["Name", "Proxy String", "Host", "Port", "Username",
                          "Password", "Expires", "Last Exit IP",
                          "Used By", "Status", "Note"]

SECRET = "JBSWY3DPEHPK3PXP"


def column_number(letters: str) -> int:
    """"C" -> 3. The inverse of `gsheet.a1_column`, needed only here."""
    number = 0
    for letter in letters:
        number = number * 26 + (ord(letter) - 64)
    return number


class FakeWorksheet:
    """Enough gspread to answer a read and record the writes."""

    #: gspread exposes the sheet's numeric id, which `delete_rows` addresses
    #: the tab by. Anything will do here; it only has to exist.
    id = 1

    def __init__(self, headers: list[str], rows: list[list[str]],
                 row_count: int | None = None):
        self.headers = headers
        self.rows = [list(r) + [""] * (len(headers) - len(r)) for r in rows]
        self.writes: list[dict] = []
        self.deleted_rows: list[int] = []
        #: The grid, which the real thing has and this did not - so a write
        #: past the end was accepted here and refused by Sheets. That is how
        #: 28 phones were created and destroyed inside a minute without a
        #: single test noticing (2026-08-18 and 2026-08-21).
        self.row_count = (len(self.rows) + 1) if row_count is None else row_count
        self.col_count = len(headers)
        self.added_rows = 0

    def add_rows(self, count: int) -> None:
        self.row_count += count
        self.added_rows += count

    def get_all_values(self):
        return [self.headers, *self.rows]

    def get(self, a1: str):
        """One cell or a column range, the way gspread answers it: a list of
        rows, each a list of cells, with trailing blanks dropped."""
        spot = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", a1.upper())
        if not spot:
            raise AssertionError(f"the fake worksheet cannot read {a1!r}")
        col = column_number(spot.group(1))
        first, last = int(spot.group(2)), int(spot.group(4) or spot.group(2))
        grid = [self.headers, *self.rows]
        out = []
        for number in range(first, last + 1):
            row = grid[number - 1] if 0 < number <= len(grid) else []
            cell = row[col - 1] if col - 1 < len(row) else ""
            out.append([cell] if cell != "" else [])
        while out and not out[-1]:
            out.pop()
        return out

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
                # Sheets removes the row from the grid, not just its contents,
                # so the tab shrinks as rows are deleted. That is what left
                # the grid too small for the next append.
                self.row_count = max(1, self.row_count - 1)
            return None
        for item in payload:
            cell = item["range"].split(":")[0]
            column = ord(cell[0]) - 65
            index = int(cell[1:]) - 2          # data rows start at sheet row 2
            if index + 2 > self.row_count:
                # Word for word what Sheets answers, because the caller is
                # meant to grow the grid before writing into it.
                raise RuntimeError(
                    f"APIError: [400]: Invalid data[0]: Range "
                    f"({item['range']}) exceeds grid limits. Max rows: "
                    f"{self.row_count}, max columns: {self.col_count}.")
            self.writes.append(item)
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


def test_a_row_another_run_took_since_the_snapshot_is_not_claimed():
    """The 2026-08-30 incident. `serve` opens a Book per pass and
    SERVE_CONCURRENT lets passes overlap, so a batch ten minutes into its work
    is choosing from a ten-minute-old picture of the tab. That gave one
    ChatGPT account to phone 1435 at 15:19 and to phone 1442 at 15:29, and
    both signed in - one account on two devices, one already handed over.

    The lock cannot see this: the two batches hold different `Pool` objects,
    and each takes its own lock honestly."""
    pool = gmail_pool([gmail_row("a@example.com"), gmail_row("b@example.com")])
    # Another pass claimed the first row after this pool's snapshot was taken.
    status = pool._index[pool.status_column]
    pool._ws.rows[0][status] = pool.claimed_status

    taken = pool.claim()

    assert taken.credentials.email == "b@example.com", (
        "it handed out a row the tab already says is in use")


def test_a_row_taken_since_the_snapshot_stops_being_offered():
    """Reading the cell also repairs this row of the snapshot - one row, by
    value. Reloading the tab instead would replace every `Resource`, which is
    what `claim` must never do while a run is holding three of them."""
    pool = gmail_pool([gmail_row("a@example.com")])
    status = pool._index[pool.status_column]
    pool._ws.rows[0][status] = pool.claimed_status

    assert pool.claim() is None
    assert pool.available == [], "the stale row is still on offer"


def test_a_check_that_cannot_be_made_refuses_the_claim(monkeypatch):
    """A row not taken costs one pass of waiting. A row taken twice costs an
    account on two phones. The costs are not symmetrical, so an unanswerable
    question is answered no."""
    pool = gmail_pool([gmail_row("a@example.com")])

    from geelark_farm import pools
    from geelark_farm.gsheet import SheetError

    def unreachable(*_args, **_kwargs):
        raise SheetError("the tab could not be read")

    monkeypatch.setattr(pools, "read_cell", unreachable)

    assert pool.claim() is None
    assert not pool._ws.writes, "it wrote a claim it could not verify"


def test_a_free_row_is_still_claimed_after_the_check():
    """The guard on the three above: the check must not become a way of never
    claiming anything."""
    pool = gmail_pool([gmail_row("a@example.com")])

    taken = pool.claim()

    assert taken is not None and taken.credentials.email == "a@example.com"
    assert pool.status_of(taken) == pool.claimed_status


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
    row[GMAIL_HEADERS.index("Secret")] = ""
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


def test_a_ticked_row_is_read_as_one_that_signs_in_with_an_emailed_code():
    """The tick is what makes an empty password mean something.

    Without a column saying so, a blank password cell means either "this
    account cannot hold one" or "nobody has filled it in yet", and reading the
    second as the first hands out a row that cannot work.
    """
    headers = APP_HEADERS + [AppPool.EMAIL_CODE_COLUMN]
    pool = AppPool(FakeWorksheet(headers, [
        ["coded@example.com", "", "", "", "", "", "TRUE"],
        ["normal@example.com", "pw", SECRET, "", "", "", ""],
    ]), headers, threading.Lock())
    pool.load()
    first = pool.claim().credentials
    assert first.signs_in_with_an_emailed_code
    # And the row that is not ticked keeps today's meaning: a password is
    # still required of it, blank cell or not.
    assert not pool.claim().credentials.signs_in_with_an_emailed_code


def test_the_empty_grid_below_the_data_is_not_read_as_rows():
    """Putting the boxes on a column writes FALSE into every row of the grid.

    The first run after the column went up read 29 untouched rows of the
    `Gpt Info` tab as rows with content in them, and refused all 29 for having
    no address (2026-08-22).
    """
    headers = APP_HEADERS + [AppPool.EMAIL_CODE_COLUMN]
    pool = AppPool(FakeWorksheet(headers, [
        ["real@example.com", "pw", SECRET, "", "", "", "FALSE"],
        ["", "", "", "", "", "", "FALSE"],
        ["", "", "", "", "", "", "FALSE"],
    ]), headers, threading.Lock())
    pool.load()
    assert [r.values["Address"] for r in pool._rows] == ["real@example.com"]


def test_a_box_ticked_on_a_row_with_nothing_else_is_not_ignored():
    """The other half: somebody did that on purpose, and a row claiming to
    sign in with an emailed code without naming the account is worth saying."""
    headers = APP_HEADERS + [AppPool.EMAIL_CODE_COLUMN]
    pool = AppPool(FakeWorksheet(headers, [
        ["", "", "", "", "", "", "TRUE"],
    ]), headers, threading.Lock())
    pool.load()
    assert "not an email address" in pool.broken[0].error


def test_a_ticked_row_survives_having_no_password_and_no_secret():
    """`validate` is what stands between the sheet and a wasted phone, and on
    a row like this the two things it checks for are correctly absent."""
    headers = APP_HEADERS + [AppPool.EMAIL_CODE_COLUMN]
    pool = AppPool(FakeWorksheet(headers, [
        ["coded@example.com", "", "", "", "", "", "TRUE"],
    ]), headers, threading.Lock())
    pool.load()
    pool.claim().credentials.validate()  # does not raise


def test_an_unticked_row_with_no_password_is_still_refused():
    """The safe half of the pair, and the reason the tick has to be explicit.

    An unticked row with an empty password is a half-filled row, not an
    account of the other kind, and it is never handed to a phone.
    """
    headers = APP_HEADERS + [AppPool.EMAIL_CODE_COLUMN]
    pool = AppPool(FakeWorksheet(headers, [
        ["blank@example.com", "", SECRET, "", "", "", ""],
    ]), headers, threading.Lock())
    pool.load()
    assert pool.claim() is None
    assert "no password" in pool.broken[0].error


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
    book = Book(gmails=gmail_pool([]), proxies=proxy_pool([]),
                apps=AppPool(FakeWorksheet(APP_HEADERS, []), APP_HEADERS,
                             threading.Lock()),
                phones=PhoneLog(FakeWorksheet(PHONE_HEADERS, []), PHONE_HEADERS,
                                threading.Lock()))

    book.record_history(Serial="1", Event="ready")     # simply nothing happens


def test_a_history_append_that_fails_is_logged_rather_than_raised(caplog):
    """Both halves. It asserted only that nothing was raised, so the log line
    could have become `pass` and a History write failing would have gone
    silent - against the rule this project keeps everywhere else: a thing that
    must not stop the run still has to be loud (2026-08-23)."""
    book, tab = history_book()

    def refuse(*a, **k):
        raise ConnectionError("mid-write reset")
    tab.append_row = refuse

    with caplog.at_level(logging.ERROR):
        book.record_history(Serial="1", Event="ready")

    assert "mid-write reset" in caplog.text
    assert "1" in caplog.text          # and which row was lost


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
        self.rules = []

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

    #: The two gspread attributes `_make_checkbox` addresses the tab by.
    id = 7

    @property
    def spreadsheet(self):
        tab = self

        class Book:
            @staticmethod
            def batch_update(body):
                tab.rules.append(body["requests"][0]["setDataValidation"])

        return Book


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


def test_a_column_asked_for_as_a_checkbox_gets_the_boxes():
    """A column of the words TRUE and FALSE is a column you can typo into.

    The rule starts below the header and names no last row, so it covers the
    column to the bottom of the grid - a row pasted in later gets its box
    without anyone remembering to come back here.
    """
    from geelark_farm.pools import ensure_columns
    tab = NarrowTab(["Address", "Password"])

    ensure_columns(tab, "Email code", checkboxes=("Email code",))

    rule, = tab.rules
    assert rule["rule"]["condition"]["type"] == "BOOLEAN"
    assert rule["range"]["startColumnIndex"] == 2       # the column just added
    assert rule["range"]["startRowIndex"] == 1          # not the header
    assert "endRowIndex" not in rule["range"]


def test_a_checkbox_column_already_there_is_not_re_ruled():
    """Startup runs this every time, and re-sending the rule on every run
    would spend an API call to change nothing."""
    from geelark_farm.pools import ensure_columns
    tab = NarrowTab(["Address", "Email code"])

    ensure_columns(tab, "Email code", checkboxes=("Email code",))

    assert tab.rules == []


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
              status="app_only", headers=None):
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
        phone_row("991", app=PhoneLog.YES),     # waiting on an account
        phone_row("992", app=PhoneLog.NO),      # the app never installed
    ])

    reached = {row["serial"]: row["app"] for row in log.unfinished()}

    # The cross is a display convention; downstream sees the blank it always
    # saw, so nothing that reads "did this happen" had to learn a new word.
    assert reached == {"991": PhoneLog.YES, "992": ""}


def test_the_phone_that_only_needs_an_account_is_offered_first():
    """Both cost the same app account - the scarce thing - but one of them
    also needs the install. In this order the same handful of accounts turns
    into ready phones sooner."""
    log = phone_log([
        phone_row("990", app=PhoneLog.NO),
        phone_row("991", app=PhoneLog.YES),
        phone_row("992", app=PhoneLog.NO),
        phone_row("993", app=PhoneLog.YES),
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
    log = phone_log([phone_row("991", app=PhoneLog.YES,
                               account="a@example.com", status="ready")])

    assert log.unfinished() == []


def test_a_cross_reads_as_the_step_never_happening():
    """`not cell("Gmail")` reads a cross as an address, so every reader here
    goes through `said()` and the mark stops at the edge of the class."""
    log = phone_log([
        phone_row("991", gmail=PhoneLog.NO, app=PhoneLog.NO),
        phone_row("992", gmail="g@example.com", app=PhoneLog.YES,
                  account=PhoneLog.NO),
    ])

    offered = {row["serial"]: row for row in log.unfinished()}

    # 991 has no Google account on it, so there is nothing to finish
    assert "991" not in offered
    # 992 has one, and its crossed-out account reads as absent, not as taken
    assert offered["992"]["gmail"] == "g@example.com"


def test_a_crossed_out_account_is_not_settled_as_if_it_were_one():
    """`marked` feeds apply_phone_states, which looks the address up in the
    Gpt Info tab. A cross would send it looking for an account called it."""
    headers = PHONE_APP_HEADERS
    line = [""] * len(headers)
    for name, value in (("Serial", "991"), ("State", "done"),
                        ("Gmail", "g@example.com"),
                        ("GPT Account", PhoneLog.NO)):
        line[headers.index(name)] = value

    marked = phone_log([line]).marked()

    assert marked[0]["gmail"] == "g@example.com"
    assert marked[0]["app_account"] == ""


def test_rows_hands_out_the_blank_and_not_the_cross():
    """`rows` feeds settle_abandoned and sync_phone_names, and both ask "is
    there a Gmail here" the way every reader did before the mark existed. A
    cross is truthy, so a leak here would name a phone `983 - X` and keep a
    phone with no Google account on it as finishable."""
    log = phone_log([phone_row("983", gmail=PhoneLog.NO, app=PhoneLog.NO,
                               account=PhoneLog.NO)])

    row = log.rows()[0]

    assert row["Gmail"] == ""
    assert row["GPT Account"] == ""
    assert row[PhoneLog.APP_COLUMN] == ""
    assert row["Serial"] == "983"          # and the rest is untouched


# -------------------------------- writing past the end of the grid
def test_a_row_is_appended_into_a_tab_that_had_no_room_for_it():
    """Sheets removes rows from the grid, so a sync that clears out finished
    phones shrinks the tab to what is left. The next append lands past the end
    and is refused - and every phone in that batch dies on its first sheet
    write, having already been created. 28 phones went that way on two
    separate days (2026-08-18 and 2026-08-21)."""
    tab = FakeWorksheet(PHONE_APP_HEADERS, [], row_count=1)   # header only
    log = PhoneLog(tab, PHONE_APP_HEADERS, threading.Lock())

    row = log.start(Serial="1002", Proxy="SX11")

    assert row == 2
    assert tab.added_rows == 1
    assert tab.get_all_values()[1][PHONE_APP_HEADERS.index("Serial")] == "1002"


def test_appending_into_a_tab_with_room_adds_nothing():
    tab = FakeWorksheet(PHONE_APP_HEADERS, [], row_count=50)
    log = PhoneLog(tab, PHONE_APP_HEADERS, threading.Lock())

    log.start(Serial="1002")

    assert tab.added_rows == 0


def test_the_grid_shrinks_as_rows_are_deleted_and_the_next_append_still_works():
    """The whole cycle in one test: build, clear out, build again."""
    tab = FakeWorksheet(PHONE_APP_HEADERS, [], row_count=1)
    log = PhoneLog(tab, PHONE_APP_HEADERS, threading.Lock())

    rows = [log.start(Serial=str(900 + n)) for n in range(3)]
    assert rows == [2, 3, 4]

    log.delete_rows(rows)                       # the sync clears them out
    assert tab.row_count == 1                   # ...and the grid comes with it

    assert log.start(Serial="1002") == 2        # the next build still lands
    assert tab.get_all_values()[1][PHONE_APP_HEADERS.index("Serial")] == "1002"


def test_a_dropdown_longer_than_its_tab_is_written_anyway():
    """The same rule for the Lists tab - a flow growing one new reason is all
    it takes for the column to outrun the grid."""
    columns = ["Gmail Statuses", "Proxy Statuses", "GPT Statuses",
               "Phone Statuses", "Phone States"]
    tab = FakeWorksheet(columns, [], row_count=1)
    book = Book(gmails=gmail_pool([]), proxies=proxy_pool([]),
                apps=AppPool(FakeWorksheet(APP_HEADERS, []), APP_HEADERS,
                             threading.Lock()),
                phones=PhoneLog(FakeWorksheet(PHONE_HEADERS, []), PHONE_HEADERS,
                                threading.Lock()),
                lists=tab, lock=threading.Lock())

    wanted = book.sync_lists()

    assert tab.added_rows > 0
    written = {w["values"][0][0] for w in tab.writes}
    assert set(wanted["Proxy Statuses"]) <= written


def _lists_book(tab):
    return Book(gmails=gmail_pool([]), proxies=proxy_pool([]),
                apps=AppPool(FakeWorksheet(APP_HEADERS, []), APP_HEADERS,
                             threading.Lock()),
                phones=PhoneLog(FakeWorksheet(PHONE_HEADERS, []), PHONE_HEADERS,
                                threading.Lock()),
                lists=tab, lock=threading.Lock())


def test_a_tab_one_row_short_is_still_grown():
    """The existing test starts the tab at one row, so the shortfall is large
    and `short > 0` reading as `short > 1` survives it. One row short is the
    case that actually happens - a flow grows a single new reason - and it is
    the shape that took 28 phones on the row version of this rule.
    """
    columns = ["Gmail Statuses", "Proxy Statuses", "GPT Statuses",
               "Phone Statuses", "Phone States"]

    # What the tab has to reach, learned rather than guessed: the lists come
    # from failures.py and grow whenever a flow does.
    roomy = FakeWorksheet(columns, [], row_count=500)
    _lists_book(roomy).sync_lists()
    deepest = max(int(w["range"][1:]) for w in roomy.writes)

    tab = FakeWorksheet(columns, [], row_count=deepest - 1)
    _lists_book(tab).sync_lists()

    assert tab.added_rows == 1
    assert tab.row_count >= deepest


# ------------------------------ credentials a dead run was still holding
CLAIMED_HEADERS = ["Address", "Password", "2FA Secret", "Claimed",
                   "Phone Serial", "Status", "Note"]


def claimed_row(address, *, when="", status="in_use"):
    line = [""] * len(CLAIMED_HEADERS)
    for name, value in (("Address", address), ("Password", "pw"),
                        ("Claimed", when), ("Status", status)):
        line[CLAIMED_HEADERS.index(name)] = value
    return line


def claimed_pool(rows):
    pool = GmailPool(FakeWorksheet(CLAIMED_HEADERS, rows), CLAIMED_HEADERS,
                     threading.Lock())
    pool.load()
    return pool


def stamp(seconds_ago):
    return time.strftime(GmailPool.CLAIM_FORMAT,
                         time.localtime(time.time() - seconds_ago))


def test_claiming_records_when():
    """Without it `in_use` says only "somebody took this", and the only way
    back was a hand on the console."""
    pool = claimed_pool([claimed_row("a@b.com", status="")])

    taken = pool.claim()

    assert taken.values["Claimed"]
    assert pool.status_of(taken) == pool.claimed_status


def test_a_claim_older_than_any_budget_is_abandoned():
    """Nothing may keep a credential past the outer bound on the phone it was
    claimed for, so a stamp older than that is proof the run is gone."""
    pool = claimed_pool([claimed_row("old@b.com", when=stamp(7200)),
                         claimed_row("fresh@b.com", when=stamp(60))])

    assert [r.label for r in pool.abandoned(3600)] == ["old@b.com"]


def test_a_claim_stamped_before_the_utc_marker_is_still_freed():
    """Reading has to accept what writing used to produce.

    The `Z` was added so a reader in another timezone stops reading UTC as
    local - a row claimed ninety seconds earlier looked stuck for over three
    hours from Iran (2026-08-28). But every claim already on the sheet was
    written without it, and a stamp that cannot be parsed is never old enough
    to be abandoned: it stays `in_use` for good. Refusing the old shape would
    have stranded every claimed row at the moment this shipped.
    """
    old = time.strftime(GmailPool.CLAIM_FORMAT_UNMARKED,
                        time.localtime(time.time() - 7200))
    assert not old.endswith("Z"), "the point of the test is the missing marker"
    pool = claimed_pool([claimed_row("before@b.com", when=old)])

    assert [r.label for r in pool.abandoned(3600)] == ["before@b.com"]


def test_a_claim_is_stamped_as_utc_so_a_reader_elsewhere_can_tell():
    """The machine writing this runs on UTC and the people reading it do not."""
    pool = claimed_pool([claimed_row("a@b.com", status="")])

    taken = pool.claim()

    assert taken.values["Claimed"].endswith("Z")


def test_a_claim_inside_the_budget_is_left_to_the_run_that_has_it():
    """Handing the same Gmail to two phones is worse than leaving one out of
    the pool, which is why this waits rather than guesses."""
    pool = claimed_pool([claimed_row("a@b.com", when=stamp(60))])

    assert pool.abandoned(3600) == []


def test_a_row_with_no_stamp_is_left_alone():
    """One claimed before the column existed. "No time recorded" is not "a
    long time ago"."""
    pool = claimed_pool([claimed_row("a@b.com", when="")])

    assert pool.abandoned(3600) == []
    assert pool.stuck                      # still reported for the hand route


def test_something_a_hand_typed_into_the_column_is_not_read_as_a_time():
    pool = claimed_pool([claimed_row("a@b.com", when="yesterday-ish")])

    assert pool.abandoned(3600) == []


def test_a_row_that_is_not_claimed_is_never_abandoned():
    pool = claimed_pool([claimed_row("a@b.com", when=stamp(7200),
                                     status="ready")])

    assert pool.abandoned(3600) == []


def test_the_proxy_tab_reuses_the_stamp_it_already_writes():
    """It has recorded one since the exit rotation landed, and a second column
    holding the same value would be noise."""
    assert ProxyPool.claimed_at_column == ProxyPool.last_used_column
    assert GmailPool.claimed_at_column not in (ProxyPool.last_used_column, "")


# ------------------------------------------- what a row says when it goes wrong
def test_a_broken_gmail_row_says_which_tab_it_is_in():
    """A broken row here and a broken row in `Gpt Info` read identically, and
    the reader was left to guess which tab to open (2026-08-23)."""
    gmails = GmailPool(FakeWorksheet(GMAIL_HEADERS, [
        ["", "", "not-an-address", "pw", SECRET, "", "", "", ""],
    ]), GMAIL_HEADERS, threading.Lock())
    apps = AppPool(FakeWorksheet(APP_HEADERS, [
        ["also-not-one", "pw", SECRET, "", "", ""],
    ]), APP_HEADERS, threading.Lock())
    gmails.load()
    apps.load()

    assert gmails.broken[0].error.startswith("gmail:")
    assert apps.broken[0].error.startswith("app account:")


# ------------------------------------------------------- how long a note may be
def test_a_note_is_kept_to_a_size_whichever_way_it_is_written():
    """`fail` trimmed its own and every other way of writing one passed the
    text straight through, so the guard held on the path that happened to have
    it and nowhere else."""
    headers = GMAIL_HEADERS
    worksheet = FakeWorksheet(headers, [
        ["", "", "a@b.com", "pw", SECRET, "", "", "", ""],
    ])
    pool = GmailPool(worksheet, headers, threading.Lock())
    pool.load()
    row = pool._rows[0]
    long_note = "x" * 4000

    for write in (lambda: pool.retire(row, note=long_note),
                  lambda: pool.release(row, note=long_note),
                  lambda: pool.fail(row, "dead", note=long_note),
                  lambda: pool.spend(row, note=long_note)):
        write()
        assert len(row.values["Note"]) == pool.NOTE_LIMIT


def test_a_note_that_fits_is_left_exactly_as_it_was():
    headers = GMAIL_HEADERS
    pool = GmailPool(FakeWorksheet(headers, [
        ["", "", "a@b.com", "pw", SECRET, "", "", "", ""],
    ]), headers, threading.Lock())
    pool.load()
    row = pool._rows[0]

    pool.retire(row, note="Signed into phone 832.")

    assert row.values["Note"] == "Signed into phone 832."


# ------------------------------------------- which exit a phone is on
def test_a_proxy_is_found_by_the_name_the_panel_uses():
    pool = ProxyPool(FakeWorksheet(PROXY_HEADERS, [
        ["SX4", "socks5://u:p@1.2.3.4:1080", "", "", "", "", ""],
        ["SX9", "socks5://u:p@5.6.7.8:1080", "", "", "", "", ""],
    ]), PROXY_HEADERS, threading.Lock())
    pool.load()

    assert pool.find_by_name("SX9").sheet_row == 3
    assert pool.find_by_name("sx4").sheet_row == 2      # however it is typed


def test_two_rows_with_one_name_answer_nothing():
    """Ambiguity answers None on purpose: the caller uses this to decide which
    exit a phone is on, and the wrong row means refreshing an address some
    other phone is using - spending one of its three a day and moving an exit
    nobody asked to move."""
    pool = ProxyPool(FakeWorksheet(PROXY_HEADERS, [
        ["SX4", "socks5://u:p@1.2.3.4:1080", "", "", "", "", ""],
        ["SX4", "socks5://u:p@5.6.7.8:1080", "", "", "", "", ""],
    ]), PROXY_HEADERS, threading.Lock())
    pool.load()

    assert pool.find_by_name("SX4") is None
    assert pool.find_by_name("") is None
    assert pool.find_by_name("nothing like it") is None


# ============================ what the Phones tab keeps, and how it reads
def test_the_phones_tab_keeps_a_note_to_a_size_itself():
    """`Pool._set` does this for a resource row and nobody did it here, so the
    guard lived at one call site in the builder and every other way of writing
    a note went past it (2026-08-23)."""
    from geelark_farm.pools import NOTE_LIMIT

    worksheet = FakeWorksheet(PHONE_HEADERS, [
        ["2026-08-23", "801", "unused", "", "", "", "", "", ""]])
    log = PhoneLog(worksheet, PHONE_HEADERS, threading.Lock())

    log.finish(2, Note="x" * 4000)

    written = worksheet.writes[-1]["values"][0][0]
    assert len(written) == NOTE_LIMIT
    assert written.endswith("\u2026")


def test_history_keeps_its_prose_columns_to_a_size_too():
    from geelark_farm.pools import NOTE_LIMIT, HistoryLog

    worksheet = FakeWorksheet(HistoryLog.HEADERS, [])
    history = HistoryLog(worksheet, threading.Lock())

    history.append(Serial="801", Note="n" * 4000, Steps="s" * 4000)

    row = worksheet.rows[-1]
    assert len(row[HistoryLog.HEADERS.index("Note")]) == NOTE_LIMIT
    assert len(row[HistoryLog.HEADERS.index("Steps")]) == NOTE_LIMIT


def test_a_cross_outside_the_step_columns_is_left_alone():
    """`said` is a display convention, and the class that owns it says it
    stops there. Applied to every cell, a cross typed into Status read as an
    empty one."""
    headers = PHONE_HEADERS
    log = PhoneLog(FakeWorksheet(headers, [
        ["2026-08-23", "801", "unused", "", "", PhoneLog.NO, PhoneLog.NO,
         PhoneLog.NO, "a note"],
    ]), headers, threading.Lock())

    row = log.rows()[0]

    assert row["Gmail"] == ""              # a step that did not happen
    assert row["Status"] == PhoneLog.NO    # not a step column, left as typed


def test_the_three_readers_agree_about_what_a_row_is():
    """Each walked the whole tab with its own copy of the rule, so the
    checkbox rule `Pool` learned in August was remembered in none of them."""
    import inspect

    source = inspect.getsource(PhoneLog)

    # One definition and six users: rows, unfinished, marked, counts,
    # count_try, state_of. The number is the point - a reader that walked the
    # tab with its own copy of the rule is exactly what this catches.
    assert source.count("_typed_rows(") == 7
    assert "if not any(line)" not in source


def test_an_untouched_checkbox_does_not_make_a_phone_row():
    """The same rule as `Pool._has_content`, so the first tick added to this
    tab does not have to rediscover it."""
    headers = [*PHONE_HEADERS, "Delivered"]

    class Ticked(PhoneLog):
        checkbox_columns = frozenset({"Delivered"})

    log = Ticked(FakeWorksheet(headers, [
        ["2026-08-23", "801", "unused", "", "", "", "", "", "", "FALSE"],
        ["", "", "", "", "", "", "", "", "", "FALSE"],
    ]), headers, threading.Lock())

    assert [r["Serial"] for r in log.rows()] == ["801"]


# ================================= an instruction is not un-marked by a sync
def test_an_exit_waiting_on_a_new_address_keeps_saying_so():
    """Un-marking is meant for `dead`, where a live phone behind the exit
    contradicts the row outright. This one is an instruction waiting for
    somebody to change an address in the vendor's panel, and a phone being on
    it does not make that done (2026-08-23)."""
    pool = ProxyPool(FakeWorksheet(PROXY_HEADERS, [
        ["SX4", "socks5://u:p@1.2.3.4:1080", "", "",
         "", ProxyPool.needs_new_ip, ""],
    ]), PROXY_HEADERS, threading.Lock())
    pool.load()
    row = pool._rows[0]

    pool.attach(row, "801")

    assert row.values["Status"] == ProxyPool.needs_new_ip
    assert row.values["Used By"] == "801"      # and it says which phone


def test_an_exit_written_off_as_dead_is_un_marked_by_a_live_phone():
    """The case un-marking exists for: the phone is the side of the
    contradiction that is demonstrably working."""
    pool = ProxyPool(FakeWorksheet(PROXY_HEADERS, [
        ["SX4", "socks5://u:p@1.2.3.4:1080", "", "",
         "", ProxyPool.dead_status, ""],
    ]), PROXY_HEADERS, threading.Lock())
    pool.load()
    row = pool._rows[0]

    pool.attach(row, "801")

    assert row.values["Status"] == ProxyPool.spent_status


# ================================================ the dropdowns' own grid
def test_the_lists_grid_is_sized_from_the_rows_it_is_writing():
    """`int("A2"[1:])` reads 2 and `int("AA2"[1:])` raises, so re-deriving a
    number the loop was holding would fail on the twenty-seventh column of a
    tab this one is free to grow."""
    import inspect


    source = inspect.getsource(Book.sync_lists)

    assert 'int(item["range"]' not in source
    assert "deepest = max(deepest, offset + 2)" in source


# ==================================================================
# What mutation found nothing was holding (2026-08-23). Each of these
# is a change to pools.py that the suite did not object to.
# ==================================================================

# ------------------------------------------------ which row is this exit
def _exits(*addresses) -> ProxyPool:
    rows = [[f"SX{i}", a, "", "", "", "", ""]
            for i, a in enumerate(addresses, start=1)]
    return proxy_pool(rows)


def test_an_exit_is_found_by_its_own_endpoint_and_not_the_first_row():
    """`r.proxy and host == ... and port == ...` with `or` in place of `and`
    returns whichever row has a proxy at all - so `_discard` frees somebody
    else's exit. `find_proxy` was mentioned nowhere in this suite."""
    pool = _exits("socks5://u:p@1.2.3.4:1080", "socks5://u:p@5.6.7.8:2080")
    pool.load()

    assert pool.find_proxy("5.6.7.8:2080").sheet_row == 3
    assert pool.find_proxy("1.2.3.4:1080").sheet_row == 2


def test_an_exit_nothing_matches_is_not_answered_with_the_first_one():
    pool = _exits("socks5://u:p@1.2.3.4:1080")
    pool.load()

    assert pool.find_proxy("9.9.9.9:1080") is None
    assert pool.find_proxy("1.2.3.4:9999") is None


def test_an_address_carrying_credentials_still_finds_its_row():
    """What callers actually hold is `socks5://user:***@host:port`, with the
    password already masked for logging."""
    pool = _exits("socks5://u:p@1.2.3.4:1080")
    pool.load()

    assert pool.find_proxy("socks5://u:***@1.2.3.4:1080") is not None


def test_an_address_that_is_not_one_answers_nothing():
    pool = _exits("socks5://u:p@1.2.3.4:1080")
    pool.load()

    assert pool.find_proxy("") is None
    assert pool.find_proxy("nonsense") is None


# --------------------------------------- a phone a run is holding right now
def test_a_phone_a_run_is_building_is_not_offered_as_one_to_finish():
    """`building` means a run holds it. Offering it hands a second run the
    phone the first is driving - and `or` reading as `and` is all it takes."""
    headers = PHONE_HEADERS
    log = PhoneLog(FakeWorksheet(headers, [
        ["2026-08-23", "801", "unused", "", "SX1", "a@b.com", "", "building", ""],
        ["2026-08-23", "802", "unused", "", "SX2", "c@d.com", "", "incomplete",
         "Stopped short: no account."],
    ]), headers, threading.Lock())

    assert [row["serial"] for row in log.unfinished()] == ["802"]


def test_a_row_with_no_serial_is_not_offered_either():
    """Nothing can be finished without the number everything is filed under."""
    headers = PHONE_HEADERS
    log = PhoneLog(FakeWorksheet(headers, [
        ["2026-08-23", "", "unused", "", "SX1", "a@b.com", "", "incomplete", ""],
    ]), headers, threading.Lock())

    assert log.unfinished() == []


# --------------------------------------------- where a header goes and comes
def test_a_column_is_read_from_and_written_to_the_first_row():
    """Off by one either way and the header lands on the first row of data,
    or every column reads as absent and is added again."""
    from geelark_farm.pools import ensure_columns

    tab = NarrowTab(["Address", "Password"], col_count=26)
    tab.asked, tab.wrote = [], []
    real_values, real_cell = tab.row_values, tab.update_cell

    def row_values(row):
        tab.asked.append(row)
        return real_values(row)

    def update_cell(row, col, value):
        tab.wrote.append((row, col, value))
        return real_cell(row, col, value)

    tab.row_values, tab.update_cell = row_values, update_cell
    ensure_columns(tab, "Claimed")

    assert tab.asked == [1]
    assert tab.wrote == [(1, 3, "Claimed")]


# ------------------------------------------------- a row that is already bad
def test_a_row_that_is_already_broken_keeps_the_reason_it_has():
    """Overwriting it with `duplicate of row N` hides the real one - and a
    row with no key at all would become the duplicate every later keyless row
    is measured against."""
    headers = GMAIL_HEADERS
    pool = GmailPool(FakeWorksheet(headers, [
        ["", "", "not-an-address", "pw", SECRET, "", "", "", ""],
        ["", "", "also-not-one", "pw", SECRET, "", "", "", ""],
    ]), headers, threading.Lock())
    pool.load()

    for row in pool.broken:
        assert "not an email address" in row.error
        assert "duplicate" not in row.error


def test_the_second_row_naming_one_address_is_the_one_refused():
    headers = GMAIL_HEADERS
    pool = GmailPool(FakeWorksheet(headers, [
        ["", "", "a@b.com", "pw", SECRET, "", "", "", ""],
        ["", "", "a@b.com", "pw", SECRET, "", "", "", ""],
    ]), headers, threading.Lock())
    pool.load()

    assert len(pool.broken) == 1
    assert pool.broken[0].sheet_row == 3
    assert "duplicate of row 2" in pool.broken[0].error


# ------------------------------------------- what "needs attention" contains
def test_only_a_row_set_aside_with_a_reason_is_flagged():
    """This drives the console's `Needs attention`. Reading the test the
    other way round lists every healthy row as one that needs a decision."""
    headers = GMAIL_HEADERS
    pool = GmailPool(FakeWorksheet(headers, [
        ["", "", "free@b.com", "pw", SECRET, "", "", "", ""],
        ["", "", "busy@b.com", "pw", SECRET, "", "", "in_use", ""],
        ["", "", "done@b.com", "pw", SECRET, "", "", "used", ""],
        ["", "", "bad@b.com", "pw", SECRET, "", "", "wrong_password", ""],
        ["", "", "broken", "pw", SECRET, "", "", "", ""],
    ]), headers, threading.Lock())
    pool.load()

    assert [r.credentials.email for r in pool.flagged] == ["bad@b.com"]


# ---------------------------------------------- what a note says about sharing
def test_an_exit_carrying_two_phones_says_so_and_one_does_not():
    """Backwards, it tells the operator two accounts share an address when
    they do not - and says nothing when they do."""
    pool = _exits("socks5://u:p@1.2.3.4:1080")
    pool.load()
    row = pool._rows[0]

    pool.attach(row, "801")
    assert "On phone 801." == row.values["Note"]

    pool.attach(row, "801, 802")
    assert "Shared by phones" in row.values["Note"]


# ------------------------------------------- whether the row was there to write
def test_writing_to_a_phone_with_no_row_says_it_had_none():
    """`_record` logs "has no row in the Phones tab to record on" off this,
    and the warning dies with the answer."""
    headers = PHONE_HEADERS
    log = PhoneLog(FakeWorksheet(headers, [
        ["2026-08-23", "801", "unused", "", "", "", "", "", ""],
    ]), headers, threading.Lock())

    assert log.write("801", Status="ready") is True
    assert log.write("999", Status="ready") is False


def test_dropping_a_phone_with_no_row_says_it_had_none():
    headers = PHONE_HEADERS
    worksheet = FakeWorksheet(headers, [
        ["2026-08-23", "801", "unused", "", "", "", "", "", ""],
    ])
    log = PhoneLog(worksheet, headers, threading.Lock())

    assert log.drop("999") is False
    assert log.drop("801") is True


# ------------------------------------------------- how many were put back
def test_release_stuck_counts_what_it_freed():
    """"released 0" beside three freed rows is a report nobody can act on."""
    book = Book(gmails=gmail_pool([
        ["", "", "a@b.com", "pw", SECRET, "", "", "in_use", ""],
        ["", "", "c@d.com", "pw", SECRET, "", "", "in_use", ""],
        ["", "", "e@f.com", "pw", SECRET, "", "", "", ""],
    ]), proxies=proxy_pool([]),
        apps=AppPool(FakeWorksheet(APP_HEADERS, []), APP_HEADERS,
                     threading.Lock()),
        phones=PhoneLog(FakeWorksheet(PHONE_HEADERS, []), PHONE_HEADERS,
                        threading.Lock()))
    book.apps.load()

    assert book.release_stuck() == 2


def test_release_stuck_counts_nothing_when_nothing_is_stuck():
    book = Book(gmails=gmail_pool([
        ["", "", "a@b.com", "pw", SECRET, "", "", "", ""],
    ]), proxies=proxy_pool([]),
        apps=AppPool(FakeWorksheet(APP_HEADERS, []), APP_HEADERS,
                     threading.Lock()),
        phones=PhoneLog(FakeWorksheet(PHONE_HEADERS, []), PHONE_HEADERS,
                        threading.Lock()))
    book.apps.load()

    assert book.release_stuck() == 0


# ------------------------------------------------ the four tabs a run needs
def test_the_tabs_a_run_cannot_start_without_are_named_when_absent():
    """Inline in `Book.open`, reaching this meant building a whole fake
    gspread client, so nobody did - and inverting it, which makes every
    workbook look broken, was a change no test objected to."""
    from geelark_farm.pools import missing_tabs

    assert missing_tabs({"Gmails": 1, "Proxy": 1, "Gpt Info": 1,
                         "Phones": 1}) == []
    assert missing_tabs({"Gmails": 1, "Phones": 1}) == ["Proxy", "Gpt Info"]
    assert missing_tabs({}) == ["Gmails", "Proxy", "Gpt Info", "Phones"]


def test_the_tabs_made_automatically_are_not_demanded():
    """`Lists` and `History` are this tool's own; asking the operator to make
    them by hand would mean every workbook is missing one."""
    from geelark_farm.pools import missing_tabs

    assert missing_tabs({"Gmails": 1, "Proxy": 1, "Gpt Info": 1,
                         "Phones": 1, "Lists": 1, "History": 1}) == []


# ================================================================
# The claim heartbeat (2026-08-25). A live run says "still mine"
# about what it holds, so a stamp that stopped moving is proof the
# holder is gone - and the wait to reclaim can be minutes instead
# of a whole build budget.
# ================================================================

CLAIMED_GMAIL_HEADERS = ["Purchase Date", "Seller", "Address", "Password",
                         "2FA Secret", "Used Date", "Phone Serial", "Status",
                         "Note", "Claimed"]


def _beating_pool(rows=1):
    body = [["", "", f"a{i}@b.com", "pw", SECRET, "", "", "", "", ""]
            for i in range(rows)]
    pool = GmailPool(FakeWorksheet(CLAIMED_GMAIL_HEADERS, body),
                     CLAIMED_GMAIL_HEADERS, threading.Lock())
    pool.load()
    return pool


def _stamp_of(pool, resource):
    return (resource.values.get(pool.claimed_at_column) or "").strip()


def test_a_row_this_run_claimed_is_restamped_by_a_beat(monkeypatch):
    """The whole point. `abandoned` reads this cell, so moving it forward is
    how a run in progress keeps its credentials."""
    pool = _beating_pool()
    monkeypatch.setattr(time, "strftime",
                        lambda fmt, *a: "2026-08-25 00:00:00")
    claimed = pool.claim()
    assert _stamp_of(pool, claimed) == "2026-08-25 00:00:00"

    monkeypatch.setattr(time, "strftime",
                        lambda fmt, *a: "2026-08-25 00:01:00")

    assert pool.beat() == 1
    assert _stamp_of(pool, claimed) == "2026-08-25 00:01:00"


def test_a_row_that_was_given_back_is_not_restamped():
    """Refreshing a row this run no longer holds is how the heartbeat would
    become the bug it exists to fix: the row reads as claimed-and-alive
    forever, and nothing ever frees it."""
    pool = _beating_pool()
    claimed = pool.claim()

    pool.release(claimed)

    assert pool.beat() == 0


def test_every_way_a_claim_ends_stops_the_beat():
    """Tracked in `_set` rather than in claim/release, because that is the one
    place all of them pass through - including any added later. A path that
    ended a claim without being noticed here would leave the row beating."""
    for finish in ("release", "spend", "fail", "set_aside", "retire"):
        pool = _beating_pool()
        claimed = pool.claim()
        assert pool.beat() == 1, f"{finish}: nothing was held to begin with"

        method = getattr(pool, finish, None)
        if method is None:
            continue
        if finish == "fail":
            method(claimed, "wrong_password")
        else:
            method(claimed)

        assert pool.beat() == 0, f"{finish} left the row beating"


def test_a_beat_does_not_make_an_unclaimed_row_look_claimed():
    """The beat writes only the stamp. If that write were read as a claim,
    beating would take rows nothing asked for."""
    pool = _beating_pool(rows=2)
    claimed = pool.claim()
    other = next(r for r in pool._rows if r is not claimed)

    pool.beat()

    assert pool.status_of(other) in pool.available_statuses
    assert pool.beat() == 1, "the beat itself claimed something"


def test_the_beat_lands_in_the_claimed_cell_and_nowhere_near_it():
    """Read off the grid, not off the row object.

    `beat` sets `resource.values` itself, so an in-memory check passes however
    the range was addressed - and the sheet is the shared state. One column
    out and the stamp never moves, so a live run's rows go stale underneath
    it, while whatever column it landed on is quietly overwritten.

    `Claimed` is deliberately not the last column here: at the end of the row
    an off-by-one writes into empty space and looks harmless.
    """
    headers = ["Address", "Password", "2FA Secret", "Phone Serial", "Status",
               "Claimed", "Note"]
    pool = AppPool(FakeWorksheet(headers, [["a@b.com", "pw", SECRET, "", "",
                                            "", "keep me"]]),
                   headers, threading.Lock())
    pool.load()
    pool.claim()

    pool.beat()

    row = pool._ws.rows[0]
    assert row[headers.index("Claimed")].startswith("20")
    assert row[headers.index("Note")] == "keep me"
    assert row[headers.index("Status")] == pool.claimed_status


def test_a_pool_with_no_claim_column_beats_nothing():
    """The Phones tab has no such column and neither did any tab before the
    stamp existed. Nothing to refresh is not an error."""
    headers = ["Address", "Password", "2FA Secret", "Phone Serial", "Status",
               "Note"]
    pool = AppPool(FakeWorksheet(headers, [["a@b.com", "pw", SECRET,
                                            "", "", ""]]),
                   headers, threading.Lock())
    pool.load()
    pool.claim()

    assert pool.beat() == 0


def test_one_write_covers_every_row_the_pool_is_holding():
    """A run holds three rows a phone and fifteen phones at a time. Forty-five
    separate writes a minute is a quota problem rather than a heartbeat."""
    pool = _beating_pool(rows=3)
    for _ in range(3):
        pool.claim()

    # Counted at the API call, not at the cells: the fake records one entry
    # per range whether they arrived together or one at a time, so the write
    # log cannot tell the two apart.
    calls = []
    real = pool._ws.batch_update

    def batch_update(payload):
        calls.append(payload)
        return real(payload)

    pool._ws.batch_update = batch_update

    assert pool.beat() == 3

    assert len(calls) == 1, "one batch, not one call per row"
    assert len(calls[0]) == 3


# --------------------------------------------- what a seller promises about a row
SELLER_HEADERS = ["Seller", "Address", "Password", "Secret",
                  "Status", "Note", "Phone Serial"]


def seller_pool(rows) -> GmailPool:
    pool = GmailPool(FakeWorksheet(SELLER_HEADERS, rows), SELLER_HEADERS,
                     threading.Lock())
    pool.load()
    return pool


def test_a_usa_row_carrying_a_recovery_address_is_refused():
    """The Seller says these answer with a code, and this one cannot. Caught
    here rather than on a booted phone, which is where the cost is."""
    pool = seller_pool([["USA", "a@b.com", "pw", "rec@e.com", "", "", ""]])

    assert not pool.available
    assert "authenticator key" in pool._rows[0].error


def test_an_egypt_row_carrying_an_authenticator_key_is_refused():
    pool = seller_pool([["Egypt", "a@b.com", "pw", SECRET, "", "", ""]])

    assert not pool.available
    assert "recovery address" in pool._rows[0].error


def test_each_kind_is_usable_when_it_carries_what_it_promises():
    pool = seller_pool([
        ["USA", "a@b.com", "pw", SECRET, "", "", ""],
        ["Egypt", "c@d.com", "pw", "rec@e.com", "", "", ""],
    ])

    assert len(pool.available) == 2


def test_a_seller_nobody_has_categorised_is_left_alone():
    """Only the two named ones promise anything. An older batch keeps working
    and a new one is not forced into a category before anybody knows which it
    is."""
    pool = seller_pool([["Hoavan", "a@b.com", "pw", "", "", "", ""]])

    assert len(pool.available) == 1


def test_the_seller_name_is_matched_however_it_is_typed():
    pool = seller_pool([[" egypt ", "a@b.com", "pw", SECRET, "", "", ""]])

    assert not pool.available


# ------------------------------- which phone a claimed row is being used for
def test_a_claimed_row_names_the_phone_it_was_taken_for():
    """Without it the two tabs cannot be joined while the work is happening:
    Phones says `building`, Gpt Info says `in_use`, and nothing on either says
    which `in_use` belongs to which phone. With three at once that is the
    difference between a tab you can read and one you can only count."""
    pool = gmail_pool([gmail_row("a@b.com"), gmail_row("c@d.com")])

    row = pool.claim("1421")

    assert row is not None
    assert row.values[pool.serial_column] == "1421"


def test_a_claim_with_no_phone_yet_leaves_the_serial_alone():
    """`claim()` is also called where no phone exists - the diagnostic paths -
    and writing an empty serial over one would lose it."""
    pool = gmail_pool([gmail_row("a@b.com")])

    row = pool.claim()

    assert row is not None
    assert not row.values[pool.serial_column]


def test_claiming_still_marks_the_status():
    """The serial is added to what claiming writes, not instead of it.

    (This tab has no `Claimed` column - `_set` skips a column the tab lacks,
    which is the behaviour that lets the optional ones be absent.)"""
    pool = gmail_pool([gmail_row("a@b.com")])

    row = pool.claim("1421")

    assert row.values[pool.status_column] == pool.claimed_status
    assert row.values[pool.serial_column] == "1421"


# ---------------------------------------- a phone somebody has taken away
def phones_tab(rows):
    from geelark_farm.pools import PhoneLog
    head = ["Created", "Serial", "State", "Proxy", "Gmail", "GPT Account",
            "Status", "Note"]
    return PhoneLog(FakeWorksheet(head, rows), head, threading.Lock())


def a_phone(serial, state="", status="app_only", gpt=""):
    return ["2026-08-29", serial, state, "SX1", "a@b.com", gpt, status, ""]


def test_a_taken_phone_is_never_offered_for_finishing():
    """The worst thing in the codebase for the consumer. A phone with the app
    and no account is what he takes to sign a customer's own account into by
    hand - and to `unfinished` it was raw stock, so the next account pasted
    into Gpt Info sent a run at it. `act_reset_app` then finds a chat screen
    this run did not sign in, reads it as the app's logged-out mode, and
    `pm clear`s the customer's session away (2026-08-29)."""
    log = phones_tab([a_phone("1401"), a_phone("1402", state="taken")])

    serials = [row["serial"] for row in log.unfinished()]

    assert serials == ["1401"]


def test_taking_a_phone_never_deletes_it():
    """`marked()` is what the sync carries out, and it must not learn this
    word: the phone is the product, and deleting it is the one thing that
    cannot be undone."""
    from geelark_farm.pools import PhoneLog

    log = phones_tab([a_phone("1402", state="taken")])

    assert log.marked() == []
    assert PhoneLog.TAKEN not in (PhoneLog.DONE, PhoneLog.FAILED)


def test_a_row_a_person_has_written_on_is_left_alone():
    """`done` and `failed` are about to be carried out by the sync; offering
    them for finishing in the meantime races it."""
    log = phones_tab([a_phone("1403", state="done"),
                      a_phone("1404", state="failed"),
                      a_phone("1405", state="unused"),
                      a_phone("1406")])

    assert [r["serial"] for r in log.unfinished()] == ["1405", "1406"]


def test_the_counts_say_what_can_be_taken_of_each_kind():
    """The consumer's only real question, and no reader answered it."""
    log = phones_tab([
        a_phone("1410", status="ready", gpt="x@y.com"),
        a_phone("1411", status="ready", gpt="z@y.com"),
        a_phone("1412"),                      # app-only, on the shelf
        a_phone("1413", state="taken"),       # out with somebody
        a_phone("1414", status="building"),   # a run holds it
    ])

    assert log.counts() == {"ready": 2, "app_only": 1, "taken": 1}


def test_a_taken_phone_is_not_counted_as_stock():
    """Ten taken and the tab said `10 of 10` while the shelf was bare."""
    log = phones_tab([a_phone(str(1420 + n), state="taken") for n in range(3)])

    assert log.counts()["app_only"] == 0
    assert log.unfinished() == []


# ------------------------------------------ every Sheets call has a deadline
def test_the_sheets_client_is_given_a_timeout():
    """gspread's default is `None` - no timeout at all. A build hit a fatal
    screen, wrote its artifact, and went silent inside the sheet write that
    follows: no CPU, no log line, the pass frozen at twelve minutes. The
    GeeLark client got a deadline that morning for the same failure; this is
    the other half of it (2026-08-29)."""
    import inspect

    from geelark_farm import pools, verify
    from geelark_farm.gsheet import SHEET_TIMEOUT, with_timeout

    connect, read = SHEET_TIMEOUT
    assert connect > 0 and read > 0

    seen = []

    class Fake:
        def set_timeout(self, value):
            seen.append(value)

    assert isinstance(with_timeout(Fake()), Fake)
    assert seen == [SHEET_TIMEOUT]

    # Both places that build a client have to go through it, or one path is
    # bounded and the other is not.
    for module in (pools, verify):
        source = inspect.getsource(module)
        for line in source.splitlines():
            if "gspread.authorize(" in line:
                assert "with_timeout(" in line, (module.__name__, line.strip())


# --------------------------------------------- giving up on a hopeless phone
def tries_tab(rows):
    from geelark_farm.pools import PhoneLog
    head = ["Created", "Serial", "State", "Proxy", "Gmail", "App",
            "GPT Account", "Status", "Note", "Tries"]
    return PhoneLog(FakeWorksheet(head, rows), head, threading.Lock())


def a_try_row(serial, tries="", status="app_only"):
    return ["2026-08-29", serial, "", "SX1", "a@b.com", "✓", "", status, "",
            tries]


def test_a_phone_that_keeps_failing_stops_being_offered():
    """A phone keeps its Gmail and its empty GPT Account whatever goes wrong,
    so `unfinished` went on offering it - and a fault that will not clear on
    its own was then a boot, a wait and a failure every time an account
    arrived (2026-08-29)."""
    from geelark_farm.pools import PhoneLog

    log = tries_tab([a_try_row("1501", tries=str(PhoneLog.GIVE_UP_AFTER)),
                     a_try_row("1502", tries="1")])

    assert [r["serial"] for r in log.unfinished()] == ["1502"]


def test_the_retry_is_worth_having():
    """An install that failed because the network was slow succeeds on the
    second go, which is why the answer is a few tries and not one."""
    from geelark_farm.pools import PhoneLog

    assert PhoneLog.GIVE_UP_AFTER > 1
    log = tries_tab([a_try_row("1503", tries="1"), a_try_row("1504", tries="2")])

    assert len(log.unfinished()) == 2


def test_clearing_the_cell_puts_the_phone_back():
    """What somebody does after fixing whatever it kept failing on."""
    from geelark_farm.pools import PhoneLog

    log = tries_tab([a_try_row("1505", tries=str(PhoneLog.GIVE_UP_AFTER))])
    assert log.unfinished() == []

    back = tries_tab([a_try_row("1505", tries="")])
    assert [r["serial"] for r in back.unfinished()] == ["1505"]


def test_a_hand_typed_word_does_not_retire_a_phone_for_ever():
    """Unreadable counts as none: a cell somebody typed into must not take a
    phone out of the queue silently and permanently."""
    log = tries_tab([a_try_row("1506", tries="lots")])

    assert [r["serial"] for r in log.unfinished()] == ["1506"]


def test_counting_an_attempt_writes_it_back():
    log = tries_tab([a_try_row("1507", tries="1")])

    assert log.count_try("1507") == 2
    assert [r["serial"] for r in tries_tab(log._ws.rows).unfinished()] == ["1507"]


def test_counting_a_phone_that_is_not_there_is_not_an_error():
    """Called from a `finally`, where raising replaces the outcome."""
    log = tries_tab([a_try_row("1508")])

    assert log.count_try("9999") == 0


def test_an_account_with_no_second_factor_at_all_is_usable():
    """A third kind nothing had accounted for: sold with neither an
    authenticator nor a recovery address, signing in on the password alone.
    The flow has always handled it - `validate` guards its secret check with
    `if self.totp_secret` and `has_authenticator` is simply False - but a
    Seller rule written that morning demanded a value and refused two such
    rows (2026-08-29)."""
    for seller in ("USA", "Egypt", "Hoavan", ""):
        pool = seller_pool([[seller, "a@b.com", "pw", "", "", "", ""]])
        assert len(pool.available) == 1, seller
        assert pool.available[0].credentials.totp_secret == ""
        assert pool.available[0].credentials.recovery_email == ""


def test_an_empty_cell_is_a_fact_and_a_wrong_one_is_a_mistake():
    """The distinction the rule turns on."""
    empty = seller_pool([["USA", "a@b.com", "pw", "", "", "", ""]])
    wrong = seller_pool([["USA", "c@d.com", "pw", "rec@e.com", "", "", ""]])

    assert len(empty.available) == 1
    assert not wrong.available
