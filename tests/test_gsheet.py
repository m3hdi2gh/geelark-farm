"""batch_write's retry classes."""

from __future__ import annotations

import threading

import pytest

from geelark_farm import gsheet
from geelark_farm.gsheet import SheetError, batch_write


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code
    def json(self):
        return {"error": {"code": self.status_code, "message": "x"}}
    @property
    def text(self):
        return "quota"


class Quota(gsheet.APIError):
    """A 429, shaped like the one gspread raises."""
    def __init__(self):
        super().__init__(FakeResponse(429))


class Forbidden(gsheet.APIError):
    def __init__(self):
        super().__init__(FakeResponse(403))


class RecordingSheet:
    def __init__(self, fail_times, error):
        self.fail_times = fail_times
        self.error = error
        self.calls = 0
    def batch_update(self, payload):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error()


def test_a_quota_error_is_waited_out_and_retried(monkeypatch):
    """429 is a wait, not a no: Google allows sixty writes a minute, a big sync
    crosses it, and the same request succeeds a minute later (2026-08-17)."""
    monkeypatch.setattr(gsheet.time, "sleep", lambda *a: None)
    sheet = RecordingSheet(fail_times=2, error=Quota)

    batch_write(sheet, threading.Lock(), [{"range": "A1", "values": [["x"]]}],
                what="row 1")

    assert sheet.calls == 3          # failed twice, then succeeded


def test_a_quota_that_never_clears_becomes_a_sheet_error(monkeypatch):
    monkeypatch.setattr(gsheet.time, "sleep", lambda *a: None)
    sheet = RecordingSheet(fail_times=99, error=Quota)

    with pytest.raises(SheetError, match="quota"):
        batch_write(sheet, threading.Lock(),
                    [{"range": "A1", "values": [["x"]]}], what="row 1")


def test_a_non_quota_api_error_is_not_retried(monkeypatch):
    """A 403 is a real refusal - a revoked key, a bad range - and repeating it
    changes nothing, so it is raised as itself rather than waited on."""
    monkeypatch.setattr(gsheet.time, "sleep", lambda *a: None)
    sheet = RecordingSheet(fail_times=99, error=Forbidden)

    with pytest.raises(gsheet.APIError):
        batch_write(sheet, threading.Lock(),
                    [{"range": "A1", "values": [["x"]]}], what="row 1")
    assert sheet.calls == 1          # tried once, gave up


# --------------------------------------------------- reads, not only writes
def test_a_read_survives_the_quota_the_way_a_write_does(monkeypatch):
    """Writes were wrapped and reads were not, because a person re-ran the
    tool when a read failed. A service polling the tabs every half minute has
    nobody to do that: one 429 would end the loop (2026-08-23)."""
    monkeypatch.setattr(gsheet.time, "sleep", lambda *a: None)
    tries = []

    class Tab:
        def get_all_values(self):
            tries.append(1)
            if len(tries) < 3:
                raise Quota()
            return [["Address"], ["a@b.com"]]

    rows = gsheet.read_values(Tab(), threading.Lock(), what="the Gmails tab")

    assert rows == [["Address"], ["a@b.com"]]
    assert len(tries) == 3


def test_a_read_refused_for_a_real_reason_is_not_retried(monkeypatch):
    """A revoked key or a bad range is a no, not a wait. Retrying it spends
    four backoffs to be told the same thing."""
    monkeypatch.setattr(gsheet.time, "sleep", lambda *a: None)
    tries = []

    class Tab:
        def get_all_values(self):
            tries.append(1)
            raise Forbidden()

    with pytest.raises(gsheet.GSpreadError):
        gsheet.read_values(Tab(), threading.Lock(), what="the Gmails tab")

    assert len(tries) == 1


def test_a_read_that_never_comes_back_says_which_tab_was_lost(monkeypatch):
    monkeypatch.setattr(gsheet.time, "sleep", lambda *a: None)

    class Tab:
        def get_all_values(self):
            raise OSError("connection reset")

    with pytest.raises(gsheet.SheetError, match="the Proxy tab"):
        gsheet.read_values(Tab(), threading.Lock(), what="the Proxy tab")


# --------------------------------------- what mutation found (2026-08-26)
class Flaky:
    """A sheet that fails a set number of times with a chosen error."""

    def __init__(self, fail_times, error):
        self.fail_times = fail_times
        self.error = error
        self.calls = 0

    def batch_update(self, payload):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error


def test_a_network_blip_is_retried_and_not_given_up_on(monkeypatch):
    """The quota path was held both ways; this one only ever asserted that it
    eventually fails - which is also true of a call that gives up on its first
    refusal. A blip is a blip, and a sync that abandons a row over one is a
    row left half written."""
    monkeypatch.setattr(gsheet.time, "sleep", lambda *a: None)
    sheet = Flaky(fail_times=2, error=OSError("connection reset"))

    batch_write(sheet, threading.Lock(), [{"range": "A1", "values": [["x"]]}],
                what="row 1")

    assert sheet.calls == 3, "it did not try again after a network failure"


def test_the_number_of_attempts_is_the_number_asked_for(monkeypatch):
    """Named, because "it retries" is true of one retry and of twenty, and the
    difference is how long a run sits on a sheet that is not coming back."""
    monkeypatch.setattr(gsheet.time, "sleep", lambda *a: None)
    sheet = Flaky(fail_times=99, error=OSError("down"))

    with pytest.raises(SheetError, match="3 attempts"):
        batch_write(sheet, threading.Lock(),
                    [{"range": "A1", "values": [["x"]]}], what="row 1",
                    attempts=3)

    assert sheet.calls == 3


def test_a_quota_waits_far_longer_than_a_blip(monkeypatch):
    """Two backoffs on purpose. A 429 is a wait for a window Google measures
    in minutes, so it backs off toward a full one; a dropped connection is
    back in seconds, and waiting a minute on it wastes the run's budget."""
    naps = []
    monkeypatch.setattr(gsheet.time, "sleep", naps.append)

    batch_write(Flaky(fail_times=1, error=Quota()), threading.Lock(),
                [{"range": "A1", "values": [["x"]]}], what="row 1")
    quota_wait = naps.pop()

    batch_write(Flaky(fail_times=1, error=OSError("reset")), threading.Lock(),
                [{"range": "A1", "values": [["x"]]}], what="row 1")
    blip_wait = naps.pop()

    assert quota_wait > blip_wait * 3
    assert blip_wait < 10, "a dropped connection should not cost a minute"


def test_neither_backoff_grows_without_a_ceiling(monkeypatch):
    """Doubling forever puts the fourth wait past anything a build can spend,
    inside a run whose budget the sheet knows nothing about."""
    naps = []
    monkeypatch.setattr(gsheet.time, "sleep", naps.append)
    sheet = Flaky(fail_times=99, error=Quota())

    with pytest.raises(SheetError):
        batch_write(sheet, threading.Lock(),
                    [{"range": "A1", "values": [["x"]]}], what="row 1",
                    attempts=8)

    assert max(naps) <= 62, "the quota backoff has no ceiling"
    assert all(nap > 0 for nap in naps)


# ------------------------------- the second thing 1a97d7e took with it
def test_column_letters_survive_past_z():
    """Every write here addresses a cell by letter, and `Pool._set` builds
    that address from a column index - so this is what decides which column a
    status lands in.

    It had a test until the row flow was deleted and took tests/test_sheets.py
    with it (1a97d7e, 2026-08-12). That file was mixed, and this is the second
    piece of live code found stranded by it - `accounts.totp_now` was the
    first (2026-08-26).
    """
    from geelark_farm.gsheet import a1_column

    assert a1_column(1) == "A"
    assert a1_column(9) == "I"
    assert a1_column(26) == "Z"
    assert a1_column(27) == "AA"
    assert a1_column(52) == "AZ"
    assert a1_column(53) == "BA"
    assert a1_column(702) == "ZZ"
    assert a1_column(703) == "AAA"


def test_a_column_number_below_one_has_no_letters():
    """The loop is what makes this safe: there is no column zero, and an
    empty answer is a range Sheets refuses rather than a cell it overwrites."""
    from geelark_farm.gsheet import a1_column

    assert a1_column(0) == ""
