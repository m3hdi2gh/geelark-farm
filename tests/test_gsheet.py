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
