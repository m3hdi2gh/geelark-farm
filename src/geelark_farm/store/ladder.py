"""The retry ladder for Gmails Google distrusted.

A Gmail refused with a captcha, a "verify your phone number" or a
"couldn't verify it's you" is not spent: over a week, an address refused
with a captcha signed in on its next try two times in three, and every
one of the 203 addresses ever signed in had an authenticator key - the
refusal is Google distrusting the device and the exit as much as the
address. So the row waits (a day, then two) and comes back on its own
for a fresh phone and exit; the third refusal is a person's to look at.

`challenge` is written by the pool's `fail`, `revive_due` is a keeper
step (the login-rate work, 2026-09-10).
"""

from __future__ import annotations

import logging

from .. import failures
from ..config import Settings
from .db import connect

log = logging.getLogger(__name__)

#: How long a row waits after its first and second refusal.
WAITS_HOURS = (24, 48)
#: Refusals before the ladder ends and the row stays set aside.
MAX_TRIES = len(WAITS_HOURS) + 1


def challenge(conn, row_id: int, reason: str) -> tuple[int, bool]:
    """Count one refusal against a row already marked with `reason`.
    Returns (tries, comes_back)."""
    cur = conn.execute(
        "UPDATE resources SET tries = tries + 1, last_reason = %s,"
        " retry_after = CASE WHEN tries + 1 >= %s THEN NULL"
        "   ELSE now() + make_interval(hours => CASE WHEN tries = 0 THEN %s"
        "                                         ELSE %s END) END,"
        " updated_at = now()"
        " WHERE id = %s RETURNING tries, retry_after IS NOT NULL",
        (str(reason)[:80], MAX_TRIES, WAITS_HOURS[0], WAITS_HOURS[1],
         int(row_id)))
    row = cur.fetchone()
    if row is None:
        return 0, False
    tries, comes_back = int(row[0]), bool(row[1])
    hours = WAITS_HOURS[min(tries, len(WAITS_HOURS)) - 1]
    said = (f"Try {tries} of {MAX_TRIES}: back in the pool in {hours} h for "
            f"a fresh phone and exit." if comes_back else
            f"Try {tries} of {MAX_TRIES}: refused on three phones; needs a "
            f"person.")
    conn.execute(
        "UPDATE resources SET note = left(coalesce(note, '') || ' ' || %s, 400)"
        " WHERE id = %s", (said, int(row_id)))
    return tries, comes_back


def revive_due(settings: Settings) -> list[str]:
    """Put back every row whose wait is over. Returns the addresses."""
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE resources SET status = '', serial = '', retry_after = NULL,"
            " note = 'Back in the pool for try ' || (tries + 1) || ' of '"
            "        || %s || ' after ' || last_reason || ' ('"
            "        || to_char(now(), 'YYYY-MM-DD HH24:MI') || ').',"
            " updated_at = now()"
            " WHERE kind = 'gmail' AND error IS NULL"
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
