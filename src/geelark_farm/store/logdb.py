"""Log capture: the process's own INFO-and-up lines, batched into the store.

The JSON file on disk stays the complete record - this is the copy a page
can filter by run, by phone, by level, without a shell on the server. Three
rules, each the price of an incident elsewhere in this program:

**Never in the path of a build.** Records go through a bounded queue and a
`QueueHandler`; the logging call returns the moment the record is queued.
One listener thread turns the queue into batched INSERTs on its own short
connection. A full queue drops the record and counts it - a build that
waits on a log line is a build that a slow database can stall.

**Self-disabling.** Three failed flushes in a row and the capture takes
itself off the root logger with one warning, so a cluster that is down
costs one line rather than a warning per second for the rest of the day.
`serve` installs it once per process; a restart brings it back.

**Its own lines are not captured.** The listener's own warnings are logged
under this module's name, which `_row` skips - or a failing flush would log
a warning that queues a record that fails to flush.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from logging.handlers import QueueHandler

from ..config import Settings, machine
from ..logs import NO_BUILD, RESERVED

log = logging.getLogger(__name__)

#: How many records may wait for the listener. Past this they are dropped,
#: counted, and said once - the file has them either way.
QUEUE_SIZE = 5000
#: A flush happens at whichever comes first.
BATCH = 200
FLUSH_SECONDS = 1.0
#: Failed flushes in a row before the capture switches itself off.
FAILURES_BEFORE_OFF = 3
#: How long a captured line lives in the table.
KEEP_DAYS = 30
#: How often the listener prunes what is older than KEEP_DAYS.
PRUNE_EVERY = 24 * 3600

#: Record attributes that become columns rather than `extra`.
_COLUMNS = frozenset({"run", "row", "build", "serial"})


def _plain(value) -> str:
    return "" if value in ("", None, NO_BUILD) else str(value)


def _row(record: logging.LogRecord) -> tuple | None:
    """One record as the tuple the INSERT wants, or None to skip it."""
    if record.name == __name__:
        return None
    run = getattr(record, "run", "") or ""
    build = getattr(record, "row", "") or getattr(record, "build", "") or ""
    extra = {}
    for key, value in record.__dict__.items():
        if key in RESERVED or key in _COLUMNS:
            continue
        extra[key] = value
    try:
        text = record.getMessage()
    except Exception as exc:                                      # noqa: BLE001
        # A bad format string must not lose the line, only its arguments.
        log.debug("record %r did not format (%s)", record.msg, exc)
        text = str(record.msg)
    if record.exc_text:
        text = f"{text}\n{record.exc_text}"
    return (
        datetime.fromtimestamp(record.created, tz=timezone.utc),
        record.levelname, record.name,
        "" if run == NO_BUILD else str(run),
        "" if build == NO_BUILD else str(build),
        _plain(getattr(record, "serial", "")),
        machine(), text[:4000],
        json.dumps(extra, default=str) if extra else None,
    )


class Capture:
    """The handler on the root logger and the thread behind it."""

    def __init__(self, settings: Settings, *, connect=None) -> None:
        from . import db

        self.settings = settings
        self._connect = connect or (lambda: db.connect(settings))
        self.queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.handler = _DroppingQueueHandler(self.queue, self)
        self.handler.setLevel(logging.INFO)
        # The same stamp the file gets: which run and which build the line
        # belongs to, read off the worker's context. On this handler, not
        # trusted to have been put there by a handler before it.
        from ..builder import BuildContextFilter

        self.handler.addFilter(BuildContextFilter())
        self.dropped = 0
        self.written = 0
        self.failures = 0
        self.disabled = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="log-capture",
                                        daemon=True)
        self._last_prune = 0.0

    # ------------------------------------------------------------ wiring
    def start(self) -> Capture:
        logging.getLogger().addHandler(self.handler)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        """Flush what is queued and end the thread. For an orderly exit;
        the Watchdog's os._exit skips it, and loses at most one second."""
        logging.getLogger().removeHandler(self.handler)
        self._stop.set()
        self._thread.join(timeout)

    def _switch_off(self, why: str) -> None:
        if self.disabled:
            return
        self.disabled = True
        logging.getLogger().removeHandler(self.handler)
        log.warning("log capture switched itself off (%s); the JSON file "
                    "on disk stays the complete record", why)

    # ---------------------------------------------------------- the loop
    def _run(self) -> None:
        pending: list[tuple] = []
        last_flush = time.monotonic()
        while not (self._stop.is_set() and self.queue.empty()):
            if self.disabled:
                return
            record = None
            with contextlib.suppress(queue.Empty):
                record = self.queue.get(timeout=FLUSH_SECONDS / 4)
            if record is not None:
                row = _row(record)
                if row is not None:
                    pending.append(row)
            due = (len(pending) >= BATCH
                   or (pending and time.monotonic() - last_flush
                       >= FLUSH_SECONDS))
            if due:
                self._flush(pending)
                pending = []
                last_flush = time.monotonic()
            if time.monotonic() - self._last_prune >= PRUNE_EVERY:
                self._prune()
        if pending:
            self._flush(pending)

    def flush_now(self) -> None:
        """Drain the queue synchronously. Tests, and an orderly stop."""
        pending = []
        while not self.queue.empty():
            record = None
            with contextlib.suppress(queue.Empty):
                record = self.queue.get_nowait()
            if record is None:
                break
            row = _row(record)
            if row is not None:
                pending.append(row)
        if pending:
            self._flush(pending)

    def _flush(self, rows: list[tuple]) -> None:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO logs (at, level, logger, run, build,"
                        " serial, machine, msg, extra)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
                conn.commit()
            self.written += len(rows)
            self.failures = 0
        except Exception as exc:                                  # noqa: BLE001
            self.failures += 1
            log.warning("log capture could not write %d line(s) (%s)",
                        len(rows), exc)
            if self.failures >= FAILURES_BEFORE_OFF:
                self._switch_off(f"{self.failures} failed flushes in a row: "
                                 f"{exc}")

    def _prune(self) -> None:
        self._last_prune = time.monotonic()
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM logs WHERE at < now() - make_interval(days"
                    " => %s)", (KEEP_DAYS,))
                conn.commit()
        except Exception as exc:                                  # noqa: BLE001
            log.warning("log capture could not prune old lines (%s)", exc)


class _DroppingQueueHandler(QueueHandler):
    """A QueueHandler that drops on a full queue instead of blocking or
    printing a traceback per record."""

    def __init__(self, target: queue.Queue, owner: Capture) -> None:
        super().__init__(target)
        self.owner = owner

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        # The stock prepare() formats the message and drops args/exc_info so
        # the record is safe to pickle across processes. Same thread here,
        # and `_row` wants exc_text - so format the exception, keep the rest.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(
                record.exc_info)
        return record

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.owner.dropped += 1
            if self.owner.dropped == 1:
                log.warning("log capture queue is full; dropping lines "
                            "until it drains (the file keeps them)")


def install(settings: Settings) -> Capture | None:
    """Start capturing, once per process. None when the flag is off."""
    if not (settings.store_enabled and settings.log_db):
        return None
    capture = Capture(settings).start()
    log.info("log capture on: INFO and up, batched into the store, kept "
             "%d days", KEEP_DAYS)
    return capture
