"""The API's practice room.

The panel's author cannot write a client against a door he may not push.
But a real POST puts an account in the pool, and an account in the pool
gets a phone, a Gmail and an exit spent on it within a pass - which is
why the write half of `/api/v1` has been switched off since the day it
was built. A key whose role is `sandbox` resolves that: its accounts land
in `api_sandbox`, a table shaped like the columns the API reads, which
nothing else in the farm ever looks at.

Two verbs beyond the ordinary ones:

* `simulate` moves one of those rows to any state the contract has, so a
  client can be written - and shown - against `needs_human` or
  `delivered` without waiting for a phone to get there.
* `sweep` throws the room out after `KEEP_DAYS`, so practice does not
  accumulate.

Everything else - the reads, the validation, the idempotency, the error
shapes - is the same code on the same paths. That is the point: what he
builds against the sandbox is what will run against the farm.
"""

from __future__ import annotations

import logging

from ..config import Settings
from ..store.db import connect

log = logging.getLogger(__name__)

#: How long a practice row lives. Long enough to write a client against
#: over a weekend, short enough that nobody mistakes the room for stock.
KEEP_DAYS = 14

#: Every state a client can be shown, and the columns that produce it.
#: The values are what a real row would carry, so `state_of` decides the
#: answer here exactly as it does for the pool - one reader, one truth.
DRIVEN = {
    "queued": {"status": "", "customer_ready": True, "serial": ""},
    "signing_in": {"status": "in_use", "customer_ready": True,
                   "serial": "1601"},
    "ready": {"status": "ready", "customer_ready": True, "serial": "1601"},
    "delivered": {"status": "delivered", "customer_ready": True,
                  "serial": "1601"},
    "needs_human": {"status": "sign_in_refused", "customer_ready": True},
    "invalid": {"error": "a sandbox row made invalid on purpose"},
    "withdrawn": {"withdrawn_at": "now"},
}

#: The three states `simulate` will not act out, and why each is left.
#:
#: * `blocked` is not a state of the account at all - it says this farm
#:   does not serve that kind yet, so a client that wants to see it POSTs
#:   a kind that is not served and gets it for real.
#: * `waiting_customer` and `needs_code` belong to the code path, and the
#:   only kinds that reach them are not served by anything today - so a
#:   row put in one would be showing a client a state the farm cannot
#:   currently produce. They arrive together with that path.
NOT_DRIVEN = ("blocked", "waiting_customer", "needs_code")


class Refused(Exception):
    """What `simulate` raises for a state nobody can be put in. Carries
    the field, so the door answers in its own error shape."""

    def __init__(self, message: str, field: str = "state"):
        super().__init__(message)
        self.field = field


def simulate(settings: Settings, *, client_id: int, ref: str, state: str,
             reason: str = "") -> None:
    """Move one practice row to `state`.

    `reason` is only read for `needs_human`, and only a word the farm's
    own vocabulary knows: the panel branches on that token, so a sandbox
    that could hand out an invented one would be teaching the client a
    word the farm will never send.
    """
    from .. import failures

    wanted = str(state or "").strip()
    if wanted in NOT_DRIVEN:
        raise Refused(f"{wanted} is not a state to be put in; "
                      f"POST a kind that is not served instead")
    if wanted not in DRIVEN:
        raise Refused("one of " + ", ".join(sorted(DRIVEN)))
    fields = dict(DRIVEN[wanted])
    if wanted == "needs_human" and reason:
        if not failures.knows(reason):
            raise Refused("a reason the farm itself uses", "reason")
        fields["status"] = reason
    sets, params = [], []
    for column, value in fields.items():
        if value == "now":
            sets.append(f"{column} = now()")
            continue
        sets.append(f"{column} = %s")
        params.append(value)
    # Every state but `withdrawn` and `invalid` is a fresh start for the
    # two columns the others set, or a row walked to `ready` and then
    # back to `queued` would keep the phone it never had.
    if wanted not in ("withdrawn", "invalid"):
        sets.append("withdrawn_at = NULL")
        sets.append("error = NULL")
    if wanted == "signing_in":
        sets.append("attempts = attempts + 1")
    if wanted == "delivered":
        sets.append("delivered_at = now()")
    with connect(settings) as conn:
        conn.execute(
            f"UPDATE api_sandbox SET {', '.join(sets)},"
            f" state_changed_at = now(), updated_at = now()"
            f" WHERE client_id = %s AND lower(panel_ref) = lower(%s)",
            (*params, int(client_id), str(ref)))
        conn.commit()


def sweep(settings: Settings) -> int:
    """Throw out practice rows older than `KEEP_DAYS`. Returns how many."""
    with connect(settings) as conn:
        cur = conn.execute(
            "DELETE FROM api_sandbox"
            " WHERE created_at < now() - make_interval(days => %s)"
            " RETURNING id", (KEEP_DAYS,))
        gone = len(cur.fetchall())
        conn.commit()
    if gone:
        log.info("swept %d sandbox account(s) older than %d days",
                 gone, KEEP_DAYS)
    return gone
