"""Stop this one, across processes.

The serials somebody asked to stop from the console, kept in
`service_state` under `KEY` so a builder on any host hears a press made
on the keeper's console. `builder.STOP_BY_HAND` was a set in the keeper's
own memory: with the builds in a container of their own the Cancel button
on a building row did nothing at all (the operator, 2026-09-10).

A request is a serial and when it was made; anything older than
`KEEP_SECONDS` is dropped on the next write, so a stop nobody could honour
- the build had already ended - does not lie in wait for the next phone
to carry that serial (serials are GeeLark's, and never reused, but the
list should not grow for ever either).
"""

from __future__ import annotations

import logging
import time

from ..config import Settings
from . import state
from .db import connect

log = logging.getLogger(__name__)

KEY = "stop_by_hand"
KEEP_SECONDS = 2 * 3600


def _pruned(asked: dict, now: float) -> dict:
    return {serial: at for serial, at in (asked or {}).items()
            if isinstance(at, (int, float)) and now - at < KEEP_SECONDS}


def ask(settings: Settings, serial: str) -> None:
    """Write one request. Raises like any store write: the verb that
    calls this reports a store that is down rather than a stop that
    quietly went nowhere."""
    now = time.time()
    with connect(settings) as conn:
        asked = _pruned(state.get(settings, KEY, {}) or {}, now)
        asked[str(serial)] = now
        state.put(conn, KEY, asked)
        conn.commit()


def asked(settings: Settings) -> set[str]:
    """Every serial with a live request."""
    now = time.time()
    return set(_pruned(state.get(settings, KEY, {}) or {}, now))


def honoured(settings: Settings, serial: str) -> None:
    """Take one request out, the build having given up on it."""
    now = time.time()
    with connect(settings) as conn:
        asked = _pruned(state.get(settings, KEY, {}) or {}, now)
        asked.pop(str(serial), None)
        state.put(conn, KEY, asked)
        conn.commit()
