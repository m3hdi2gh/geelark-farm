"""How a build hears that it must stop: the console's Stop on its phone,
the service shutting down, and a person marking the phone while it works.

Moved out of builder.py unchanged (the builder review, 2026-09-23). Every
long wait a build makes takes the callable `_hand_stop_wired` returns, so
the next automation that drives a phone needs this and nothing else of
the builder's. Logs under the builder's logger name, as before the move.

A leaf: stdlib, `breaker`, `config`; the store only inside a function.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from . import breaker
from .config import Settings

log = logging.getLogger("geelark_farm.builder")


class Aborted(Exception):
    """The run is shutting down; stop what this build is doing."""


#: Serials somebody asked to stop from the web ("Stop this one", C7). The
#: pass's drain adds to it; every session looks at its next step and, if
#: its phone is named, gives up with `stopped_by_hand` - the same path an
#: interrupt takes, so what it held goes back to its pool. One process,
#: so a set is enough; a serial is taken out the moment it is honoured.
STOP_BY_HAND: set[str] = set()

#: How often a build asks the store whether somebody pressed Stop on it:
#: every step, but a store read at most this often. The set above is the
#: same process's own presses and is read every time.
STOP_POLL_SECONDS = 2.0
_STOP_SEEN: dict = {"at": 0.0, "serials": frozenset()}
#: Serials this process has already raised a stop for. The build is
#: unwinding; the waits its teardown makes must not be told again, and
#: the request stays in the store until `_stop_honoured` takes it out at
#: the end - so the row says Stopping for the whole of the teardown and
#: not for the one tick before the request was deleted.
_STOP_HEARD: set[str] = set()


def _stop_asked(settings: Settings | None, serial: str) -> bool:
    """Whether somebody pressed Stop on this phone - in this process, or
    on the keeper's console with the build running in a builder container
    (the store's `stop_by_hand` key). Taken out the moment it is heard,
    from wherever it was. Never raises: a store that cannot be read is a
    stop not heard yet, and the next step asks again."""
    serial = str(serial or "").strip()
    if not serial:
        return False
    if serial in STOP_BY_HAND:
        STOP_BY_HAND.discard(serial)
        _STOP_HEARD.add(serial)
        return True
    # Heard already: the build is on its way out, and a wait its teardown
    # makes must not be handed the same stop a second time.
    if serial in _STOP_HEARD:
        return False
    if settings is None or not getattr(settings, "store_enabled", False):
        return False
    from .store import stops as store_stops

    now = time.monotonic()
    if now - _STOP_SEEN["at"] >= STOP_POLL_SECONDS:
        try:
            _STOP_SEEN["serials"] = frozenset(store_stops.asked(settings))
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not read the stop requests (%s); asking "
                        "again in %.0fs", exc, STOP_POLL_SECONDS)
            _STOP_SEEN["serials"] = frozenset()
        _STOP_SEEN["at"] = now
    if serial not in _STOP_SEEN["serials"]:
        return False
    # Latched, not consumed. It came out of the store the moment it was
    # heard, so the press lived on only as an exception in flight: one
    # `except Aborted` in the way and it was gone, with nothing left for
    # the next wait to hear - and the Stopping badge, drawn from the
    # store, went back to Building over a teardown still making three
    # GeeLark calls (2026-09-21, found by audit).
    _STOP_HEARD.add(serial)
    return True


def _stop_honoured(settings: Settings | None, serial: str) -> None:
    """The build is over: the request comes out of the store, and the row
    stops saying Stopping. Only for a press this process heard - every
    other build's end writes nothing. Never raises: it runs in a finally,
    and a store that will not answer leaves a request that ages out."""
    serial = str(serial or "").strip()
    if serial not in _STOP_HEARD:
        return
    _STOP_HEARD.discard(serial)
    # And out of the cached reading, so a wait in the next few seconds
    # cannot hear a press the store no longer holds.
    _STOP_SEEN["serials"] = _STOP_SEEN["serials"] - {serial}
    if settings is None or not getattr(settings, "store_enabled", False):
        return
    from .store import stops as store_stops

    try:
        store_stops.honoured(settings, serial)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("stop on %s honoured but could not be taken out of the "
                    "store (%s); it ages out", serial, exc)

def _hand_stop_wired(settings: Settings | None, build,
                     cancelled: Callable[[], bool] | None,
                     ) -> Callable[[], bool]:
    """One callable that hears both ways a build can be stopped.

    `cancelled` was the service's shutdown flag and nothing else - and it
    is what every wait underneath a build takes: the boot, the settle,
    the install, the exit swap. `check_cancelled` heard the console's
    Cancel, but only where `check_cancelled` was called, which is between
    steps; inside a step the flag alone was asked. So a Cancel pressed
    while the phone booted or the app installed - most of a build's
    minutes - went unheard until the sign-in began (the operator,
    2026-09-21: "the cancel button does not work instantly").

    Raised rather than returned for the console's stop, so the word on
    the row is the right one: the waits answer a True with their own
    PhoneError, which build_one files as a phone that would not start -
    and records as a GeeLark refusal. A person pressing Cancel is
    neither. The service's flag still answers True, the way every wait
    already expects.
    """
    def stop_wanted() -> bool:
        if cancelled and cancelled():
            return True
        if _stop_asked(settings, getattr(build, "serial", "")):
            raise Aborted("stopped_by_hand")
        return False
    return stop_wanted


#: The aborts that are a person stopping the work rather than a verdict on
#: the phone. `Aborted` carries both kinds - `no_usable_proxy` and
#: `all_exits_refused` are judgements; these two name a person. Anything
#: raised as `Aborted` that means a person, not a fault, belongs in here
#: (2026-09-06, found by audit).
#:
#: What the set no longer decides is whether the phone is kept. It used
#: to: a phone stopped by hand before its Google account was in was
#: spared the discard "so it can be finished" - and nobody ever finished
#: one. It sat in the table as `incomplete` with a cross for a Gmail,
#: holding an exit and a plan slot, until a person marked it failed by
#: hand (the operator, 2026-09-14, phone 2520: "a phone with no Gmail on
#: it is worth nothing unless I asked for one"). So a stop by hand now
#: deletes a phone nothing was signed into, exactly as a fault would; the
#: phone worth keeping without an account is the one a wish asked for
#: that way. See KEPT_WHEN_EMPTY.
#:
#: The breaker's, not a second copy: it is the breaker that has to know
#: these are not the machine failing. Five Cancels in a quiet stretch
#: counted five failures in a row and opened it (2026-09-21, found by
#: audit) - and this module imports that one, not the other way round.
STOPPED_BY_A_PERSON = breaker.STOPPED_BY_A_PERSON


def _given_up_on(settings: Settings, serial: str, *,
                 own_take: bool = False) -> str:
    """The word somebody has written in this phone's State, if any.

    Checked at the few places a build is about to spend real time, because a
    row can be marked while the run that owns it is minutes into its work.
    `unfinished` keeps a marked row out of the queue, but the build already
    under way never learned - so a phone marked `failed` at 20:06 had the app
    installed on it until 20:36, and the sync then deleted it (2026-08-29).

    `taken` is here too: somebody has claimed the phone by hand and this run
    should let go of it rather than drive it - unless `own_take`: a phone
    asked for by hand is taken by its builder from the moment its row
    exists, and that take is this run's own, not a stranger's. Read as a
    stranger's, every hand-built phone gave up on itself at its first
    check (2026-09-08).
    """
    if not serial:
        return ""
    from .store import person

    state = person.state_of(settings, serial)
    if own_take and state == person.TAKEN:
        return ""
    return state if state in (person.DONE, person.FAILED,
                              person.TAKEN) else ""
