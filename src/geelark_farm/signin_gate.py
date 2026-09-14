"""The rate gate: when Google is refusing everything, probe - do not pour.

The breaker counts the farm's own faults and leaves Google's verdicts out
on purpose (`breaker.NOTHING_HAPPENED`): a distrusted phone is not a bug.
Which is true, and which is how the keeper ordered ten builds a pass into
an hour where none of fifty-nine sign-ins got in, and burned ninety
addresses finding out that the hour had not changed (the operator,
2026-09-14: "no ready phone and a pile of burned Gmails").

Nothing here judges why Google refuses. It reads the last few sign-ins
the way a person reading the /logins page would: if nearly none got in,
the next ten will not either, so send two and see. While the gate is
down the keeper still orders `PROBE_SIZE` builds every
`PROBE_EVERY_SECONDS` - the farm never closes for long, it just stops
paying full price for bad news - and the moment two of the last four
sign-ins get in it opens again by itself. Hand-built phones are not
touched: a person who asked for one asked knowing.

Never load-bearing. A store that will not answer, or too few sign-ins to
judge, means no gate - the keeper builds as it always did.
"""

from __future__ import annotations

import dataclasses
import logging
import time

from .config import Settings

log = logging.getLogger(__name__)

#: How many of the latest sign-ins are read.
WINDOW = 12
#: At most this many of them signed in: the gate closes.
GATE_AT_OR_BELOW = 1
#: The gate opens again once this many of the last `LIFT_LAST` got in.
LIFT_LAST = 4
LIFT_OK = 2
#: While closed, how many builds go out as probes, and how often.
PROBE_SIZE = 2
PROBE_EVERY_SECONDS = 15 * 60

#: Where the gate keeps its own two numbers, in `service_state`.
STATE_KEY = "signin_gate"


@dataclasses.dataclass
class Gate:
    """What the gate decided this pass, for the log and the dashboard."""

    closed: bool = False
    ok: int = 0
    of: int = 0
    #: Seconds until the next probe may go, while closed.
    next_probe_in: int = 0
    #: When the gate closed, unix time; 0 when open.
    since: float = 0.0

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _latest(settings: Settings, n: int) -> list[bool]:
    """The newest `n` sign-ins, newest first, as whether each got in."""
    from .store import signins

    return signins.latest_ok(settings, n)


def _state(settings: Settings) -> dict:
    from .store import state as store_state

    got = store_state.get(settings, STATE_KEY, {}) or {}
    return got if isinstance(got, dict) else {}


def _remember(settings: Settings, state: dict) -> None:
    from .store import state as store_state
    from .store.db import connect

    with connect(settings) as conn:
        store_state.put(conn, STATE_KEY, state)
        conn.commit()


def judge(latest: list[bool], *, was_closed: bool) -> tuple[bool, int, int]:
    """Closed or open, from the sign-ins alone: (closed, ok, of).

    Closing takes a full window with almost nothing in it; opening takes
    two of the last four, so one lucky probe does not reopen the tap and
    a gate that closed on old news reopens on the first good pair."""
    of = len(latest)
    ok = sum(1 for got in latest if got)
    if was_closed:
        recent = latest[:LIFT_LAST]
        return sum(1 for got in recent if got) < LIFT_OK, ok, of
    if of < WINDOW:
        return False, ok, of
    return ok <= GATE_AT_OR_BELOW, ok, of


def throttle(settings: Settings, decision, *, now: float | None = None):
    """The pass's decision, with its warm builds cut to the probe
    allowance while the gate is closed. Returns (decision, Gate).

    `decision.build` is the keeper's own warm-stock builds; wishes are
    ordered separately by the caller and are not touched here."""
    if not getattr(settings, "store_enabled", False):
        return decision, Gate()
    now = time.time() if now is None else now
    try:
        latest = _latest(settings, WINDOW)
        state = _state(settings)
    except Exception as exc:                                      # noqa: BLE001
        log.debug("the sign-in gate could not read (%s); open", exc)
        return decision, Gate()
    was_closed = bool(state.get("closed"))
    closed, ok, of = judge(latest, was_closed=was_closed)
    gate = Gate(closed=closed, ok=ok, of=of)
    if not closed:
        if was_closed:
            log.info("the sign-in gate opens: %d of the last %d got in",
                     sum(1 for g in latest[:LIFT_LAST] if g),
                     min(LIFT_LAST, of))
            _try_remember(settings, {"closed": False})
        return decision, gate
    since = float(state.get("since") or 0) or now
    last_probe = float(state.get("last_probe_at") or 0)
    gate.since = since
    due = last_probe + PROBE_EVERY_SECONDS
    wanted = int(decision.build or 0)
    if not wanted:
        gate.next_probe_in = max(0, int(due - now))
        _try_remember(settings, {"closed": True, "since": since,
                                 "last_probe_at": last_probe})
        return decision, gate
    if now >= due:
        allowed = min(wanted, PROBE_SIZE)
        last_probe = now
        gate.next_probe_in = PROBE_EVERY_SECONDS
        log.warning("the sign-in gate is closed: %d of the last %d got in; "
                    "%d probe build(s) go instead of %d, the next in %d min",
                    ok, of, allowed, wanted, PROBE_EVERY_SECONDS // 60)
    else:
        allowed = 0
        gate.next_probe_in = int(due - now)
        log.info("the sign-in gate is closed: %d of the last %d got in; "
                 "holding %d build(s), the next probe in %d s",
                 ok, of, wanted, gate.next_probe_in)
    if not was_closed:
        log.warning("the sign-in gate closes: %d of the last %d sign-ins got "
                    "in", ok, of)
    _try_remember(settings, {"closed": True, "since": since,
                             "last_probe_at": last_probe})
    return dataclasses.replace(decision, build=allowed), gate


def _try_remember(settings: Settings, state: dict) -> None:
    try:
        _remember(settings, state)
    except Exception as exc:                                      # noqa: BLE001
        # The gate still acted this pass; only its memory of when it
        # last probed is lost, which costs one extra probe at most.
        log.debug("the sign-in gate could not write its state (%s)", exc)
