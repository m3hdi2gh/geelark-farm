"""Tests for the two pieces of api.py that are pure logic with expensive
failure modes: the signature, and the rate limiter.

The limiter is worth testing because getting it wrong bans the API key for two
hours - a failure that cannot be discovered safely by trying it.
"""

from __future__ import annotations

import pytest

from geelark_farm.api import RETRY_SAFE_PATHS, Client, RateLimiter


def test_signature_is_uppercase_sha256_of_the_documented_concatenation(make_settings):
    client = Client(make_settings(), limiter=RateLimiter(10))
    headers = client.auth_headers(trace_id="TRACE1", ts="1700000000000")

    import hashlib

    expected = hashlib.sha256(
        b"APPID" + b"TRACE1" + b"1700000000000" + b"TRACE1"[:6] + b"APIKEY"
    ).hexdigest().upper()

    assert headers["sign"] == expected
    assert headers["nonce"] == "TRACE1"[:6]
    assert headers["sign"].isupper()
    assert len(headers["sign"]) == 64


def test_signature_changes_every_call(make_settings):
    """A replayed signature would be indistinguishable from a stuck clock."""
    client = Client(make_settings(), limiter=RateLimiter(10))
    assert client.auth_headers()["sign"] != client.auth_headers()["sign"]


def test_limiter_allows_up_to_capacity_then_makes_the_caller_wait():
    limiter = RateLimiter(per_minute=3, window=60.0)
    # Deterministic mode: passing `now` returns the wait instead of sleeping.
    assert limiter.acquire(now=100.0) == 0
    assert limiter.acquire(now=100.1) == 0
    assert limiter.acquire(now=100.2) == 0

    waited = limiter.acquire(now=100.3)
    assert waited == pytest.approx(59.7, abs=0.01)


def test_limiter_frees_slots_once_the_window_has_passed():
    limiter = RateLimiter(per_minute=2, window=60.0)
    limiter.acquire(now=0.0)
    limiter.acquire(now=1.0)
    assert limiter.acquire(now=61.0) == 0     # the 0.0 hit has aged out


def test_only_read_only_endpoints_retry_by_default():
    """A timed-out write may already have been applied server side, so writes
    must never be repeated automatically."""
    assert "/v1/phone/list" in RETRY_SAFE_PATHS
    for mutating in ("/v1/phone/addNew", "/v1/phone/start", "/v1/phone/stop",
                     "/v1/shell/execute", "/v1/rpa/task/googleLogin"):
        assert mutating not in RETRY_SAFE_PATHS


def test_the_signature_is_made_after_the_wait_not_before(monkeypatch,
                                                        make_settings):
    """A signature carries the millisecond it was made in, and the limiter
    blocks - for up to a full window when the budget is spent. Signing first
    sends a timestamp as stale as the wait was long, which is what [40003]
    rejects.

    Invisible today: a local cap of 120 against a real 200 means the limiter
    almost never blocks. A service that never stops is what makes it block.
    """
    from geelark_farm import api

    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(api.time, "time", lambda: clock["now"])

    class SlowLimiter:
        def acquire(self):
            clock["now"] += 45          # a wait long enough to matter
            return 45.0

    sent = {}

    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"code": 0, "data": {}}

    class Session:
        def post(self, url, headers=None, json=None, timeout=None):
            sent["ts"] = int(headers["ts"])
            return Response()

    client = api.Client(make_settings(), limiter=SlowLimiter(),
                        session=Session())
    client.post("/v1/phone/list")

    # The timestamp is the moment the request actually left, not the moment
    # the caller asked for it 45 seconds earlier.
    assert sent["ts"] == int(clock["now"] * 1000)
