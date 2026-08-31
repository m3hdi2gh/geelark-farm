"""Append-only events: what happened, written where a query can count it.

History's successor and the monitoring substrate. Three rules, all inherited
from incidents:

**Never fatal.** An emit follows `beat` and `note_pass`: log once, carry on.
A monitoring write that can stop a phone being built is worse than no
monitoring at all.

**Its own short connection.** Events are written from the loop's thread and
from build workers; a shared connection would put psycopg's thread rules in
the path of every log-adjacent call. Connecting costs 25ms against a
same-network cluster and a pass emits a handful of events, so one
connection per emit is simpler than one lock per process.

**Closed vocabulary, open detail.** `kind` and `status` are what alerts and
the cutover criterion filter on - logs.py:7 says outright that a rephrased
sentence breaks whatever was counting it. Prose goes in `detail`, clipped.
"""

from __future__ import annotations

import logging

from ..config import Settings, machine

log = logging.getLogger(__name__)

#: The same guard NOTE_LIMIT gives sheet cells: past this, detail stops
#: being readable and starts being storage.
DETAIL_LIMIT = 500


def emit(settings: Settings, kind: str, *, run_id: str = "",
         build: str = "", serial: str = "", status: str = "",
         seconds: float | None = None, detail: str = "",
         user_id: int | None = None) -> bool:
    """Write one event. True if it landed; False never raises past here."""
    try:
        from .db import connect

        with connect(settings) as conn:
            conn.execute(
                "INSERT INTO events (kind, machine, run_id, build, serial,"
                " status, seconds, detail, user_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (kind, machine(), str(run_id), str(build), str(serial),
                 status, seconds, (detail or "")[:DETAIL_LIMIT], user_id))
            conn.commit()
        return True
    except Exception as exc:                                      # noqa: BLE001
        log.warning("event %r was not recorded (%s); the run is unaffected",
                    kind, exc)
        return False
