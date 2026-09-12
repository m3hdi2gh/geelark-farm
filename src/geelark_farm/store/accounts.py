"""What happened to one app account, written where a panel can read it.

`GET /accounts/{ref}/events` has always been able to answer - it reads
`events` for `kind = 'account'` and a detail naming the address - and
nothing ever wrote one, so the timeline a customer is shown was the
requests somebody made and nothing the farm did. This is the other half:
one row per state change, in the API's own vocabulary, so the answer to
"what became of it" is the same word the account object carries.

Written from the pool, because the pool is the one place every transition
passes through (store.pgpool.PgAppPool), and never fatal: an account that
moved but was not recorded is a gap in a story, while a build stopped by
a monitoring write is a phone. The same rule `events.emit` already keeps.

The vocabulary is `web.api_v1_read.API_STATES` and not a second list of
words that would drift from it - this module imports nothing from the web
package, so the agreement is kept by the test that reads both.
"""

from __future__ import annotations

import logging

from ..config import Settings

log = logging.getLogger(__name__)

#: The events kind `api_v1_read.events` filters on. One word, and the
#: reason a new one must never be invented for this.
KIND = "account"


def _address(row) -> str:
    """The address, from a store row or from a pool Resource."""
    values = getattr(row, "values", None)
    if isinstance(values, dict):
        return str(values.get("Address") or "").strip()
    if isinstance(row, dict):
        return str(row.get("address") or "").strip()
    return ""


def _serial_of(row) -> str:
    values = getattr(row, "values", None)
    if isinstance(values, dict):
        return str(values.get("Phone Serial") or "").strip()
    if isinstance(row, dict):
        return str(row.get("serial") or "").strip()
    return ""


def moved(settings: Settings | None, row, state: str, *, serial: str = "",
          reason: str = "") -> bool:
    """One state change of one account. True if it was recorded.

    `row` is whatever the caller is holding - the dict a claim returned
    or the Resource a verb was given - so no caller has to convert one
    into the other to say what it just did.
    """
    address = _address(row)
    if settings is None or not address:
        log.debug("an account moved to %r with nothing to name it", state)
        return False
    where = serial or _serial_of(row)
    said = f"{address} is {state}"
    if reason:
        said += f" ({reason})"
    if where:
        said += f" on phone {where}"
    try:
        from . import events as store_events

        return store_events.emit(settings, KIND, status=str(state),
                                 serial=where, detail=said + ".")
    except Exception as exc:                                      # noqa: BLE001
        log.warning("the account event for %s was not written (%s); the "
                    "build is unaffected", address, exc)
        return False
