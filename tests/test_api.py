"""Tests for the two pieces of api.py that are pure logic with expensive
failure modes: the signature, and the rate limiter.

The limiter is worth testing because getting it wrong bans the API key for two
hours - a failure that cannot be discovered safely by trying it.
"""

from __future__ import annotations

import pytest

from geelark_farm.api import RETRY_SAFE_PATHS, Client, RateLimiter
from tests.conftest import make_settings


def test_signature_is_uppercase_sha256_of_the_documented_concatenation():
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


def test_signature_changes_every_call():
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
