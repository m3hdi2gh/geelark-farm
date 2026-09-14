"""The queue a Gmail Google distrusted goes back into.

A Gmail refused with a captcha, a "couldn't verify it's you" or a plain
refusal is not spent: over a week, an address refused with a captcha
signed in on its next try two times in three, and the refusal is Google
distrusting the device and the exit as much as the address.

It used to rest for a day, then two. That cost more than it saved: in the
week this was rewritten the farm ran out of addresses eighty times while
paid ones slept on the timer. So the wait is a floor of minutes now, and
the row's *place in the queue* does the work - `claim` orders by `tries`,
so a row that has been refused is only ever reached when no fresh address
is free. The floor exists for one reason: four builders run at once, and
without it a single address could spend all three of its tries inside ten
minutes, on the same wave and the same gateways (the operator,
2026-09-12).

One refusal never comes back here at all: `failures.SELLERS_FAULT`, the
password that was never right, goes to the refund list because no phone
and no exit can fix it. Google asking for a phone number climbs the
ladder like a captcha and goes to that list only when the ladder is
spent - `failures.OWED_WHEN_EXHAUSTED` (2026-09-13).

`challenge` is written by the pool's `fail`, `revive_due` is a keeper step.
"""

from __future__ import annotations

import datetime as _dt
import logging

from .. import failures
from ..config import Settings
from .db import connect

log = logging.getLogger(__name__)

#: How long a refused row waits before it can be picked again. Not a rest:
#: the time it takes for the wave of builds it was refused in to pass.
FLOOR_MINUTES = 20
#: Refusals before the ladder ends and the row stays set aside.
MAX_TRIES = 3


def _floor(settings: Settings | None = None) -> int:
    minutes = getattr(settings, "ladder_floor_minutes", None)
    try:
        return max(0, int(minutes)) if minutes is not None else FLOOR_MINUTES
    except (TypeError, ValueError):
        return FLOOR_MINUTES


def good_hours(settings: Settings | None) -> tuple[int, int] | None:
    """The window as (start, end) hours UTC, or None for "no window".
    "17-3" is 17:00 up to 03:00 the next day; "9-17" is a plain daytime
    span. Anything unreadable is no window - a typo must not hold the
    whole pool back."""
    said = str(getattr(settings, "signin_good_hours_utc", "") or "").strip()
    if not said:
        return None
    try:
        start, end = (int(part) for part in said.split("-", 1))
    except ValueError:
        log.warning("SIGNIN_GOOD_HOURS_UTC=%r is not two hours; holding "
                    "nothing", said)
        return None
    if not (0 <= start < 24 and 0 <= end < 24) or start == end:
        log.warning("SIGNIN_GOOD_HOURS_UTC=%r is not a window; holding "
                    "nothing", said)
        return None
    return start, end


def in_good_hours(settings: Settings | None,
                  now: _dt.datetime | None = None) -> bool:
    """Whether this is an hour Google lets these exits in. Always, when
    no window is set."""
    window = good_hours(settings)
    if window is None:
        return True
    start, end = window
    hour = (now or _dt.datetime.now(_dt.timezone.utc)).hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def held_from(settings: Settings | None,
              now: _dt.datetime | None = None) -> int | None:
    """The `tries` from which a row is held back right now, or None when
    nothing is: outside the good hours an address on its last try waits.

    The pool was spending last chances in the hours that refuse nearly
    everything: 44 addresses at try 2 of 3, one builder-wave from the
    refund list, at 5% (2026-09-14, 15-18 UTC). A fresh or once-refused
    address still goes out - the operator needs phones around the clock
    - but the last try is worth keeping for the hours it will succeed."""
    if in_good_hours(settings, now):
        return None
    return MAX_TRIES - 1


def challenge(conn, row_id: int, reason: str, *, host: str = "",
              settings: Settings | None = None) -> tuple[int, bool]:
    """Count one refusal against a row already marked with `reason`.
    Returns (tries, comes_back)."""
    floor = _floor(settings)
    cur = conn.execute(
        "UPDATE resources SET tries = tries + 1, last_reason = %s,"
        " last_host = CASE WHEN %s <> '' THEN %s ELSE last_host END,"
        " retry_after = CASE WHEN tries + 1 >= %s THEN NULL"
        "   ELSE now() + make_interval(mins => %s) END,"
        " updated_at = now()"
        " WHERE id = %s RETURNING tries, retry_after IS NOT NULL",
        (str(reason)[:80], str(host)[:80], str(host)[:80], MAX_TRIES, floor,
         int(row_id)))
    row = cur.fetchone()
    if row is None:
        return 0, False
    tries, comes_back = int(row[0]), bool(row[1])
    said = (f"Try {tries} of {MAX_TRIES}: back in the pool in {floor} min, "
            f"behind every fresh address, for a new phone and exit."
            if comes_back else
            f"Try {tries} of {MAX_TRIES}: refused on three phones; needs a "
            f"person.")
    conn.execute(
        "UPDATE resources SET note = left(coalesce(note, '') || ' ' || %s, 400)"
        " WHERE id = %s", (said, int(row_id)))
    return tries, comes_back


def to_refund(conn, row_id: int, reason: str, *, seller: str = "") -> None:
    """Take a row out of the pool and onto the list to claim money back.

    No tries left and no wait: the account wants what we cannot give it,
    or the password was never right. It keeps the reason as its status so
    the tab still filters on it, and `refund_state` is what the Gmail
    Pool's own view reads.
    """
    conn.execute(
        "UPDATE resources SET refund_state = 'to_claim', refund_at = now(),"
        " retry_after = NULL, tries = %s, last_reason = %s,"
        " note = left(coalesce(note, '') || ' ' || %s, 400),"
        " updated_at = now() WHERE id = %s",
        (MAX_TRIES, str(reason)[:80],
         f"No phone or exit can fix this one - it is on the list to claim "
         f"back from {seller or 'the seller'}.", int(row_id)))


def revive_due(settings: Settings) -> list[str]:
    """Put back every row whose floor is behind it. Returns the addresses."""
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE resources SET status = '', serial = '', retry_after = NULL,"
            " note = 'Back in the pool for try ' || (tries + 1) || ' of '"
            "        || %s || ' after ' || last_reason || ', behind every"
            " fresh address (' || to_char(now(), 'YYYY-MM-DD HH24:MI') || ').',"
            " updated_at = now()"
            " WHERE kind = 'gmail' AND error IS NULL AND refund_state = ''"
            "   AND retry_after IS NOT NULL AND retry_after <= now()"
            "   AND status = ANY(%s)"
            " RETURNING address", (MAX_TRIES, sorted(failures.DISTRUST)))
        rows = cur.fetchall()
        conn.commit()
    back = [str(r[0]) for r in rows]
    if back:
        log.info("%d Gmail(s) back in the pool off the ladder: %s", len(back),
                 ", ".join(back))
    return back
