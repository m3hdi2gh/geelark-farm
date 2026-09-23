"""The run a log line belongs to: its batch, its job and its phone, stamped
on every record; the run ids; and the sink build events go to.

Moved out of builder.py unchanged (the builder review, 2026-09-23), because
every process imports it for its log handlers - the CLI, the console, the
store's log capture - and none of them wants the builder with it. The
lines it logs stay under the builder's logger name, so the console and
the saved log filters read them as they always have.

A leaf: stdlib and `logs` only.
"""
from __future__ import annotations

import itertools
import logging
from contextvars import ContextVar

from .logs import NO_BUILD

#: The logger the moved code writes to: the builder's, as before the move.
BUILD_LOGGER = "geelark_farm.builder"
log = logging.getLogger(BUILD_LOGGER)

#: The batch a thread's work belongs to, and the job within that batch.
#:
#: `default=` is not optional. `ContextVar.get()` with no default raises
#: LookupError, and a filter runs OUTSIDE the try that guards `emit` -
#: `Handler.handle` calls it directly, and neither `callHandlers` nor
#: `Logger._log` catches - so a filter that raises comes back out of the
#: `log.info(...)` call and kills the build on its own log line.
#:
#: ContextVars rather than the `threading.local` that was here: a pool thread
#: is reused, and a local left set leaks into the next job on it. A token and
#: a `reset` in a `finally` cannot.
_run: ContextVar[str] = ContextVar("geelark_run", default=NO_BUILD)
_build: ContextVar[int | str] = ContextVar("geelark_build", default=NO_BUILD)
#: The phone a worker is on, once it has one. Stamped on every log record
#: of that worker: the captured log lines carry the serial, so a page can
#: show "what is phone 1556 doing right now" as the last line it logged -
#: without the builder reporting anything extra.
_serial: ContextVar[str] = ContextVar("geelark_serial", default=NO_BUILD)


#: Where build events go, when anywhere. Injected by `serve` when the store
#: is enabled, never imported: the sheet retirement's trunk rule is that no
#: module imports `store` unconditionally, and an injection point keeps this
#: file ignorant of whether a store even exists. The sink must not raise -
#: store.events.emit already cannot - but the call is guarded anyway,
#: because "the monitoring took the build down" must be impossible from
#: both sides.
_event_sink = None


def set_event_sink(sink) -> None:
    global _event_sink
    _event_sink = sink


def _record_event(kind: str, what: str, **fields) -> None:
    """One event through the sink, when there is one. `what` is the
    event's status word - phrased this way so the scan for the statuses a
    build settles a phone with (`failures.reasons_decided_by_the_builder`)
    does not read an event's word as a verdict. Never raises: an event
    that is not recorded costs one debug line, never the build."""
    if _event_sink is None:
        return
    try:
        _event_sink(kind, status=what, **fields)
    except Exception as exc:                                      # noqa: BLE001
        log.debug("%s event not recorded (%s)", kind, exc)


_RUN_IDS = itertools.count(1)


def _next_run_id() -> str:
    """One id per batch.

    `_run_jobs` is the boundary because it is exactly one batch: under `serve`
    with a pool, one pass submits one `work`, which makes one `builder.run`,
    which makes one `_run_jobs` - so a run id is also a pass's id. Short,
    because it is on every line of the file and of the console.
    """
    return f"r{next(_RUN_IDS)}"


class BuildContextFilter(logging.Filter):
    """Stamp every log record with the run and the build it came from.

    `row` stays exactly what it has always been - the bare int job index, or
    NO_BUILD - because `ui.ReporterLogHandler.emit` and `ui.print_new_notices`
    both gate on `isinstance(row, int)` and the live table's rows are keyed by
    that int. A composite id there does not raise; it silently freezes the
    step column on "starting" for a whole batch.

    Nothing here may raise, for the reason given above the ContextVars.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.build = _build.get()
        record.run = _run.get()
        record.row = record.build
        if not getattr(record, "serial", ""):
            record.serial = _serial.get()
        return True
