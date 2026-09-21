"""Wallet integration: offline, no real account or database."""

import copy
import threading
import time
from types import SimpleNamespace

import pytest

from geelark_farm import wallet
from geelark_farm.web import pages, read


@pytest.fixture
def cache(monkeypatch):
    kept = {}

    def update(settings, key, fn, default=None):
        assert key == "geelark_wallet"
        kept[key] = fn(copy.deepcopy(kept.get(key, default)))
        return kept[key]

    # The store module itself: wallet imports it inside `refresh`, behind
    # the flag, like every store import outside the store.
    from geelark_farm.store import state

    monkeypatch.setattr(state, "update", update)
    return kept


def test_zero_is_a_reading_and_restarts_share_the_poll_budget(cache):
    calls = []

    def data(path, **kw):
        calls.append((path, kw))
        return {"balance": 0, "giftMoney": 0, "availableTimeAddOn": 3811}

    client = SimpleNamespace(data=data)
    assert wallet.refresh(None, now=1000, client=client)
    assert not wallet.refresh(None, now=1001, client=client)
    assert not wallet.refresh(None, now=1299, client=client)
    assert wallet.refresh(None, now=1300, client=client)
    assert len(calls) == 2
    assert calls[0] == ("/v1/pay/wallet", dict(
        timeout=15, total=20, attempts=1, retry=False))
    assert cache[wallet.KEY]["wallet"]["balance"] == "0"


def test_failure_keeps_last_success_and_does_not_expose_response(cache):
    good = SimpleNamespace(data=lambda *a, **k: {
        "balance": 4.25, "giftMoney": 2, "availableTimeAddOn": 8})
    wallet.refresh(None, now=1000, client=good)

    def fail(*a, **kw):
        raise RuntimeError("private upstream contents")

    assert not wallet.refresh(None, now=1300,
                              client=SimpleNamespace(data=fail))
    assert cache[wallet.KEY]["at"] == 1000
    assert cache[wallet.KEY]["wallet"]["balance"] == "4.25"
    assert cache[wallet.KEY]["failed"] is True
    assert "private" not in str(cache)
    assert wallet.refresh(None, now=1600, client=good)
    assert cache[wallet.KEY]["failed"] is False


@pytest.mark.parametrize("data", [None, {}, {"balance": 0},
    {"balance": "NaN", "giftMoney": 0, "availableTimeAddOn": 1},
    {"balance": 0, "giftMoney": None, "availableTimeAddOn": 1},
    {"balance": True, "giftMoney": 0, "availableTimeAddOn": 1},
    {"balance": 0, "giftMoney": 0, "availableTimeAddOn": 1.5}])
def test_bad_answers_are_not_zero(data):
    with pytest.raises(ValueError):
        wallet.reading(data)


def test_late_answer_cannot_replace_a_newer_reading(cache):
    def data(*a, **kw):
        cache[wallet.KEY] = {"attempted_at": 1400, "at": 1400,
                             "wallet": {"balance": "9"}}
        return {"balance": 0, "giftMoney": 0, "availableTimeAddOn": 0}

    wallet.refresh(None, now=1000, client=SimpleNamespace(data=data))
    assert cache[wallet.KEY]["wallet"]["balance"] == "9"


def test_stopped_or_storeless_web_never_polls(make_settings):
    stop = threading.Event()
    assert wallet.start(make_settings(store_enabled=False), stop) is None
    stop.set()
    assert wallet.start(make_settings(store_enabled=True), stop) is None


def test_footer_shows_cash_gift_and_time_without_guessing_no_credit():
    now = time.time()
    found = {"wallet_reading": {"at": now, "wallet": {
        "balance": "0", "giftMoney": "1.25", "availableTimeAddOn": 3811}}}
    line = pages._geelark_line({"geelark": found}, {"role": "admin", "mutations": True})
    assert "$0.00" in line and "$1.25 gift credit" in line
    assert "3,811 min" in line and "out of credit" not in line
    assert "not reported" not in line
    # Old readings remain visible, but no longer pretend to be current.
    found["wallet_reading"]["at"] = now - 3600
    assert "last known" in pages._gl_balance(found, [])["note"]
    assert pages._gl_time_addon(found)["tone"] == "warn"
    found["wallet_reading"].update(at=now, failed=True)
    assert "refresh failed" in pages._gl_balance(found, [])["note"]
    assert pages._gl_balance(found, [])["value"] == "$0.00"


def test_wallet_does_not_hide_a_live_phone_refusal():
    found = {"wallet_reading": {"at": time.time(), "wallet": {
        "balance": "4", "giftMoney": "0", "availableTimeAddOn": 10}},
        "trouble": [{"kind": "refused", "short": "balance not enough",
                     "level": "bad", "text": "a phone was refused"}]}
    line = pages._geelark_line({"geelark": found}, {"role": "admin", "mutations": True})
    assert "$4.00" in line and "balance not enough" in line


def test_read_passes_the_cached_wallet_to_the_footer():
    snapshot = {"at": 1000, "wallet": {"balance": "0"}}

    def rows(sql):
        if "service_state" in sql:
            assert "'geelark_wallet'" in sql
            return [{"key": "geelark_wallet", "value": snapshot}]
        return []

    assert read._geelark(SimpleNamespace(_rows=rows))["wallet_reading"] == snapshot
