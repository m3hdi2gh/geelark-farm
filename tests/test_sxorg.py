"""Asking the proxy vendor for a new exit address.

The cheap way out of a refused exit: the host, port and credentials stay the
same, so the phone needs no update call. Nothing here had a test - not the
call, not any of the five different ways it can come back (2026-08-25).
"""

from __future__ import annotations

import pytest
import requests

from geelark_farm import sxorg

KEY = "sx-live-abc123"


class Answer:
    """As much of a requests Response as this reads."""

    def __init__(self, payload=None, *, status=200, text="", boom=False):
        self.payload = payload
        self.status_code = status
        self.text = text
        self.boom = boom

    def json(self):
        if self.boom:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self.payload


@pytest.fixture
def asked(monkeypatch):
    """Answers with whatever is put in `.reply`, and records the request."""
    calls: list[tuple[str, dict, float | None]] = []
    box = type("Box", (), {"reply": Answer({"success": True}), "raises": None})()

    def get(url, params=None, timeout=None):
        calls.append((url, params or {}, timeout))
        if box.raises:
            raise box.raises
        return box.reply

    monkeypatch.setattr(sxorg.requests, "get", get)
    box.calls = calls
    return box


# ----------------------------------------------------- nothing to ask with
def test_no_key_is_refused_before_anything_is_sent(asked):
    """Naming the variable is the whole value of the message: the operator has
    to know which line of .env is missing, not that "sx.org failed"."""
    with pytest.raises(sxorg.SxError) as caught:
        sxorg.refresh("", 100)

    assert "SXORG_API_KEY" in str(caught.value)
    assert asked.calls == [], "it went to the network without a key"


# --------------------------------------------------------- the request itself
def test_the_port_is_in_the_path_and_the_key_is_a_parameter(asked):
    """Both halves are the vendor's shape, and getting either wrong answers
    `proxy not found` - which reads like the wrong product rather than a
    malformed request."""
    sxorg.refresh(KEY, 100)

    url, params, _ = asked.calls[0]

    assert url.endswith("/v2/proxy/refresh/100")
    assert params == {"apiKey": KEY}


def test_the_request_cannot_hang_a_build_forever(asked):
    """`requests` has no timeout unless it is given one, and this runs inside
    a build with a budget the vendor knows nothing about."""
    sxorg.refresh(KEY, 100)

    _, _, timeout = asked.calls[0]

    assert timeout == sxorg.TIMEOUT
    assert 0 < sxorg.TIMEOUT <= 60


# ---------------------------------------------------- the four ways it fails
def test_a_vendor_that_cannot_be_reached_says_which_vendor(asked):
    """One of three services a build talks to. "Connection aborted" on its own
    sends whoever reads it looking at the wrong one."""
    asked.raises = requests.ConnectionError("Max retries exceeded")

    with pytest.raises(sxorg.SxError, match="could not reach sx.org"):
        sxorg.refresh(KEY, 100)


def test_an_answer_that_is_not_json_reports_what_came_back(asked):
    """A gateway error page is HTML, and `.json()` on it raises something that
    says nothing about the status code - which is the part worth knowing."""
    asked.reply = Answer(boom=True, status=502,
                         text="<html><title>502 Bad Gateway</title>")

    with pytest.raises(sxorg.SxError) as caught:
        sxorg.refresh(KEY, 100)

    assert "502" in str(caught.value)
    assert "Bad Gateway" in str(caught.value)


def test_a_refusal_carries_the_vendor_s_own_words(asked):
    """`proxy not found` is the one worth recognising: it means this port id is
    not on this account, which is what a row from the Unlimited product looks
    like. Replacing it with our own wording would hide that."""
    asked.reply = Answer({"success": False, "message": "proxy not found"})

    with pytest.raises(sxorg.SxError) as caught:
        sxorg.refresh(KEY, 100)

    # The sentence itself, not the envelope around it. A dict dumped into the
    # message technically contains the words and is what the operator reads.
    assert str(caught.value) == "proxy not found"


def test_a_refusal_with_no_message_shows_what_did_come_back(asked):
    """There is nothing to quote, so the answer itself is the only evidence of
    what the vendor did. "None" is worse than useless - it reads like a bug in
    this file rather than an answer from sx.org."""
    asked.reply = Answer({"success": False, "code": 4041})

    with pytest.raises(sxorg.SxError) as caught:
        sxorg.refresh(KEY, 100)

    said = str(caught.value)
    assert said != "None"
    assert "4041" in said, "the only thing the vendor said is missing"


def test_a_missing_success_field_is_a_refusal_not_a_success(asked):
    """`.get("success")` on an answer that has changed shape returns None, and
    treating that as "it worked" spends the allowance and reports a new exit
    that never happened."""
    asked.reply = Answer({"status": "ok"})

    with pytest.raises(sxorg.SxError):
        sxorg.refresh(KEY, 100)


# ------------------------------------------------------------- and when it works
def test_a_refresh_that_worked_raises_nothing(asked):
    asked.reply = Answer({"success": True, "message": "refreshed"})

    assert sxorg.refresh(KEY, 100) is None


# ------------------------------------------------------- the key must not travel
@pytest.mark.parametrize("failure", ["unreachable", "not json"])
def test_the_key_is_never_in_what_gets_reported(asked, failure):
    """The vendor wants its key in the query string, and `requests` puts the
    whole URL into the text of a connection error. `_refreshed` logs that text,
    so one unreachable moment would write SXORG_API_KEY in clear into the day's
    log - the file that gets pasted into a chat window (2026-08-25).
    """
    if failure == "unreachable":
        asked.raises = requests.ConnectionError(
            f"Max retries exceeded with url: /v2/proxy/refresh/100?apiKey={KEY}")
    else:
        asked.reply = Answer(boom=True, status=500,
                             text=f"bad request to ?apiKey={KEY}")

    with pytest.raises(sxorg.SxError) as caught:
        sxorg.refresh(KEY, 100)

    assert KEY not in str(caught.value)
    assert "***" in str(caught.value)


def test_the_key_is_not_in_the_chained_cause_either(asked):
    """`raise ... from exc` keeps the original, and its text still has the URL
    in it - so a traceback printed anywhere would show the key under "the
    direct cause of the following exception"."""
    asked.raises = requests.ConnectionError(f"...?apiKey={KEY}")

    with pytest.raises(sxorg.SxError) as caught:
        sxorg.refresh(KEY, 100)

    assert KEY not in str(caught.value.__cause__ or "")
