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
_FINGERPRINT = (
    "SELECT (SELECT max(updated_at) FROM resources) AS pools,"
    "       (SELECT max(updated_at) FROM phones) AS phones,"
    "       (SELECT max(id) FROM events) AS events,"
    "       (SELECT max(id) FROM actions) AS actions,"
    "       (SELECT max(id) FROM wanted_builds) AS wanted"
)


class Pulse:
    """The revision, and the people waiting for it to move."""

    def __init__(self) -> None:
        self._seen = threading.Condition()
        self.revision = 0
        self.watching = 0
        self._mark: tuple | None = None

    def bump(self, mark: tuple) -> bool:
        """Note a fingerprint. True when it was new."""
        with self._seen:
            if mark == self._mark:
                return False
            first = self._mark is None
            self._mark, self.revision = mark, self.revision + 1
            self._seen.notify_all()
        return not first

    def wait(self, since: int, timeout: float) -> int | None:
        """The revision once it is past `since`, or None if it did not
        move before `timeout`."""
        deadline = time.monotonic() + timeout
        with self._seen:
            while self.revision <= since:
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._seen.wait(left)
            return self.revision

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
                 for k in ("pools", "phones", "events", "actions", "wanted"))


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
