"""What the console watches so a page can move the moment the farm does.

The dashboard used to ask again every thirty seconds, whatever had
happened: a phone that finished a second after a swap sat wrong for
twenty-nine more, and an operator pressing Free watched a list that was
already stale (the operator, 2026-09-14). This is the other half - the
console notices, and the page hears about it in about a second.

One thread per web process, not one per browser. It asks the store for a
fingerprint of everything the pages draw - the newest change to a pool
row, a phone, an event, a request - and when that fingerprint moves it
bumps a revision and wakes everybody waiting on it. Browsers hold a
Server-Sent Events connection and are told the new number; what they do
with it is their business, which here is the quiet swap they already do.

Deliberately a poll and not `LISTEN`: the tables a page draws are written
by three containers and a dozen verbs, and a NOTIFY at each of them is a
dozen places to forget one. One small query a second against a table of
two hundred rows costs less than the thirty-second full page fetch it
replaces, and a fingerprint that is a moment stale only means the page
hears at the next tick.

Never load-bearing. A store that will not answer, a thread that dies, a
browser with no EventSource - each one falls back to the timer the page
has always had.
"""

from __future__ import annotations

import logging
import threading
import time

from ..config import Settings

log = logging.getLogger(__name__)

#: How often the fingerprint is taken. A second is under the time it
#: takes to read the answer off the screen, and the query is three index
#: maxima.
EVERY = 1.0
#: How long a browser's connection waits before it is sent a keepalive
#: comment. Proxies close a silent stream; Caddy's own idle is longer
#: than this.
KEEPALIVE = 20.0
#: How many browsers may hold the stream open at once. Each is a thread
#: of the server, and the console is read by a handful of people; past
#: this the page keeps its timer and nothing breaks.
MAX_STREAMS = 12

#: The fingerprint: the newest thing each of the drawn tables knows. The
#: counts are not in it on purpose - a row deleted moves no `updated_at`,
#: and `events` records the deletion anyway.
#:
#: The log lines are the last column and are counted apart: they move
#: for every line a build writes, and only the Logs page draws them. A
#: page on the farm's stream does not hear them (2026-09-14).
#:
#: `service_state` is in it too: the keeper's pulse, the GeeLark
#: strip's reading, the breaker, and a Cancel that has landed on a row
#: are all drawn from it, and none of them could move the revision - a
#: press in one tab, the badge clearing when the build gave up, the
#: breaker tripping, all waited on the thirty-second timer (2026-09-21,
#: found by audit).
_FINGERPRINT = (
    "SELECT (SELECT max(updated_at) FROM resources) AS pools,"
    "       (SELECT max(updated_at) FROM phones) AS phones,"
    "       (SELECT max(id) FROM events) AS events,"
    "       (SELECT max(id) FROM actions) AS actions,"
    "       (SELECT max(id) FROM wanted_builds) AS wanted,"
    "       (SELECT max(updated_at) FROM service_state) AS state,"
    "       (SELECT max(id) FROM logs) AS logs"
)
#: Which columns of it are the farm's own; the rest is the log lines.
FARM_COLUMNS = 6


class Pulse:
    """The revision, and the people waiting for it to move."""

    def __init__(self) -> None:
        self._seen = threading.Condition()
        #: The farm's own moves - what every page but Logs listens to.
        self.revision = 0
        #: Every move, log lines included.
        self.everything = 0
        self.watching = 0
        self._mark: tuple | None = None

    def bump(self, mark: tuple) -> bool:
        """Note a fingerprint. True when it was new.

        The last column is the log lines: a fingerprint that moved only
        there bumps `everything` and leaves `revision` alone."""
        with self._seen:
            if mark == self._mark:
                return False
            first = self._mark is None
            farm = mark[:FARM_COLUMNS]
            if first or farm != self._mark[:FARM_COLUMNS]:
                self.revision += 1
            self.everything += 1
            self._mark = mark
            self._seen.notify_all()
        return not first

    def count(self, *, logs: bool = False) -> int:
        return self.everything if logs else self.revision

    def wait(self, since: int, timeout: float, *,
             logs: bool = False) -> int | None:
        """The count once it is past `since`, or None if it did not move
        before `timeout`. `logs` picks the count that also moves for a
        log line."""
        deadline = time.monotonic() + timeout
        with self._seen:
            while self.count(logs=logs) <= since:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._seen.wait(left)
            return self.count(logs=logs)

    def hold(self, delta: int) -> int:
        with self._seen:
            self.watching += delta
            return self.watching


#: One per process. The web role starts the watcher; everything else
#: reads `revision` and gets 0, which is a page that never hears a tick
#: and keeps its timer.
pulse = Pulse()


def take(settings: Settings) -> tuple | None:
    """One fingerprint, or None when the store will not answer."""
    try:
        from ..store.db import Store

        with Store(settings) as store:
            rows = store._rows(_FINGERPRINT)
    except Exception as exc:                                      # noqa: BLE001
        log.debug("the live fingerprint could not be read (%s)", exc)
        return None
    if not rows:
        return None
    row = rows[0]
    return tuple(str(row.get(k) or "")
                 for k in ("pools", "phones", "events", "actions", "wanted",
                           "state",
                           "logs"))


def watch(settings: Settings, stop: threading.Event) -> None:
    """Take the fingerprint until told to stop. Never raises."""
    quiet = 0
    while not stop.is_set():
        mark = take(settings)
        if mark is None:
            # Backing off rather than hammering a store that is down, and
            # the pages keep their own timer meanwhile.
            quiet = min(quiet + 1, 10)
        else:
            quiet = 0
            if pulse.bump(mark):
                log.debug("the farm moved; revision %d", pulse.revision)
        stop.wait(EVERY * (1 + quiet))


def start(settings: Settings, stop: threading.Event) -> threading.Thread | None:
    """The watcher, if this process should have one."""
    if not getattr(settings, "store_enabled", False):
        return None
    thread = threading.Thread(target=watch, args=(settings, stop),
                              name="live-pulse", daemon=True)
    thread.start()
    log.info("watching the store for changes; pages hear within ~%.0fs", EVERY)
    return thread
