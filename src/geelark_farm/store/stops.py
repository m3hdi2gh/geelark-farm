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
    # One edit under the row's lock: `ask` and `honoured` were each a read
    # on one connection and a write on another, from four processes, so a
    # press and an honour a moment apart could erase each other
    # (2026-09-21, found by audit).
    state.update(settings, KEY,
                 lambda asked: {**_pruned(asked or {}, now), str(serial): now},
                 {})


def live(value, now: float | None = None) -> set[str]:
    """The serials still asked for, off a value already read - for a page
    that has the row in hand and should not open a second connection to
    ask what it says."""
    return set(_pruned(value or {}, now if now is not None else time.time()))


def asked(settings: Settings) -> set[str]:
    """Every serial with a live request."""
    now = time.time()
    return set(_pruned(state.get(settings, KEY, {}) or {}, now))


def honoured(settings: Settings, serial: str) -> None:
    """Take one request out, the build having given up on it."""
    now = time.time()
    state.update(settings, KEY,
                 lambda asked: {k: at
                                for k, at in _pruned(asked or {}, now).items()
                                if k != str(serial)},
                 {})
