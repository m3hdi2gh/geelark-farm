"""Read-only wallet polling, outside page requests and the phone workers.

The web role owns this collector. A shared five-minute reservation survives
restarts and prevents concurrent consoles from spending the endpoint budget.
Only this module writes geelark_wallet; failures retain the last good reading.
"""

from __future__ import annotations

import logging
import threading
import time
from decimal import Decimal, InvalidOperation

from .api import build_client

log = logging.getLogger(__name__)
INTERVAL = 300.0
KEY = "geelark_wallet"


def reading(data) -> dict:
    """Reject incomplete/non-finite answers instead of inventing zero credit."""
    if not isinstance(data, dict):
        raise ValueError("wallet response is not an object")
    result = {}
    for key in ("balance", "giftMoney", "availableTimeAddOn"):
        value = data.get(key)
        if value is None or isinstance(value, bool):
            raise ValueError("wallet response is incomplete")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("wallet response is not numeric") from exc
        if not number.is_finite():
            raise ValueError("wallet response is not finite")
        if key == "availableTimeAddOn":
            if number < 0 or number != number.to_integral_value():
                raise ValueError("wallet time is not a non-negative integer")
            result[key] = int(number)
        else:
            result[key] = str(number)
    return result


def refresh(settings, *, now=None, client=None) -> bool:
    """Reserve a poll in a short transaction; do HTTP after releasing its lock."""
    # Inside the function, like every other store import outside the
    # store: `start` runs only with the store on, and the module must
    # import without it (tests/test_store.py's guard).
    from .store import state

    stamp = time.time() if now is None else now
    claimed = False

    def reserve(previous):
        nonlocal claimed
        previous = dict(previous or {})
        last = previous.get("attempted_at")
        if last is not None and stamp - float(last) < INTERVAL:
            return previous
        claimed = True
        previous["attempted_at"] = stamp
        return previous

    state.update(settings, KEY, reserve, default={})
    if not claimed:
        return False
    try:
        data = reading((client or build_client(settings)).data(
            "/v1/pay/wallet", timeout=15, total=20, attempts=1, retry=False))
    except Exception as exc:  # noqa: BLE001
        # No upstream response text or credentials in the store or console.
        log.warning("wallet refresh failed (%s)", type(exc).__name__)
        data = None

    def finish(previous):
        previous = dict(previous or {})
        # A delayed request must not overwrite a newer collector's answer.
        if previous.get("attempted_at") != stamp:
            return previous
        if data is not None:
            previous.update(wallet=data, at=stamp, failed=False)
        else:
            previous["failed"] = True
        return previous

    state.update(settings, KEY, finish, default={})
    return data is not None


def start(settings, stop: threading.Event):
    """One daemon collector; a failure cannot interrupt an operator request."""
    if not settings.store_enabled or stop.is_set():
        return None

    def collect():
        while not stop.is_set():
            try:
                refresh(settings)
            except Exception as exc:  # noqa: BLE001
                log.warning("wallet cache unavailable (%s)", type(exc).__name__)
            if stop.wait(INTERVAL):
                return

    thread = threading.Thread(target=collect, name="wallet", daemon=True)
    thread.start()
    return thread
