"""Talking to a Google spreadsheet, without knowing what is in it.

The transport half of the sheet layer: authorising, writing a batch of cells,
and turning a column number into the letters a range needs. Which tabs exist
and what the columns mean belongs to `pools.py`; this is what both would have
had to copy.
"""

from __future__ import annotations

import logging
import random
import threading
import time

from gspread.exceptions import APIError
from requests.exceptions import RequestException

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


#: gspread's own API error, re-exported so callers catch a
#: quota or transport failure without importing gspread. A 429
#: is turned into SheetError inside batch_write; this is for the
#: rarer errors that reach a caller some other way.
GSpreadError = APIError


class SheetError(Exception):
    """The spreadsheet cannot be read or written."""


def batch_write(worksheet, lock: threading.Lock, payload: list[dict], *,
                what: str, attempts: int = 4) -> None:
    """Send a batch update, retrying transient network failures.

    This is where an outcome becomes durable, so it is the last place that
    should fail on a blip. On 2026-08-06 a ConnectionResetError landed here
    while recording row 13's login failure; the exception unwound into
    process_row's catch-all, which recorded "error" instead - so the run
    reported a network fault where a diagnosis should have been, and the reason
    the login actually failed was lost.

    The network is retried, and so is one APIError: 429, quota exceeded.
    Google allows sixty writes a minute per user, and a big sync - a dozen
    phones settled, twenty-one proxies checked - can cross that (2026-08-17).
    Unlike a bad range or a revoked key, a 429 is a wait, not a no: the same
    request succeeds a minute later. It gets a longer backoff than a network
    blip, because the window it is waiting out is measured in a minute.

    Each attempt sends its own copy of the payload, because gspread rewrites
    the one it is given: batch_update prefixes every range with the worksheet
    title in place. Retrying the same list therefore sent
    `'geelark'!'geelark'!I3` and drew a 400, which the caller could not retry
    and recorded as "error" - the exact loss this function exists to prevent,
    caused by this function (2026-08-07, row 2).

    `what` names the thing being recorded - a row, a Gmail, a phone - because
    the only reason anyone reads this error is to find out what was lost.
    """
    for attempt in range(1, attempts + 1):
        fresh = [dict(item) for item in payload]
        try:
            with lock:
                worksheet.batch_update(fresh)
            return
        except APIError as exc:
            if getattr(exc.response, "status_code", None) != 429:
                raise                         # a real refusal, not a wait
            if attempt == attempts:
                raise SheetError(
                    f"{what}: the sheet's write quota stayed exhausted after "
                    f"{attempt} attempts ({exc})"
                ) from exc
            # The quota is per minute, so the wait is measured in that: back
            # off toward a full window rather than the seconds a blip needs.
            delay = min(15 * attempt, 60) + random.uniform(0, 2)
            log.warning("%s: sheet write quota exceeded; waiting %.0fs",
                        what, delay)
            time.sleep(delay)
        except (OSError, RequestException) as exc:
            if attempt == attempts:
                raise SheetError(
                    f"{what}: the sheet could not be written after "
                    f"{attempt} attempts ({exc})"
                ) from exc
            delay = min(2 ** attempt, 8) + random.uniform(0, 0.5)
            log.warning("%s: sheet write failed (%s); retrying in %.1fs",
                        what, exc, delay)
            time.sleep(delay)


def a1_column(number: int) -> str:
    """1 -> A, 27 -> AA. Sheets ranges are letters, dictionaries are not."""
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters
