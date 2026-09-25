"""Run one task on one phone, and end the way every phone job ends.

Everything true of every run lives here, so a task never has to
remember it: the inputs are checked against the spec, the secrets are
kept out of the record, the row is opened before the work and closed
after, the screens are archived where the console reads them, and the
phone is stopped whatever happened.

The ending is not this module's invention. `PhoneRun`, `_ended_by` and
`_let_the_phone_go` in `kit/phone.py` are the one ending a phone job
has, and the pattern in that docstring is followed here exactly - a
third copy of it is the thing AGENTS.md forbids. A task's `Build` is
mostly empty (no Gmail, no exit, no app) and that is fine: a `finish` of
a bare phone looks the same.

What this does NOT do: create a phone, claim an exit or take from a
pool. A task runs on a phone that already exists - one a build made, one
somebody took - and the task says which. Making phones is the builder's
job and giving them exits is `kit/exits.py`'s; a task that needed either
would be a build.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import failures, phones
from ..api import Client
from ..build_result import Build
from ..config import Settings
from ..kit.phone import PhoneRun, _ended_by, _let_the_phone_go
from ..ledger import Ledger
from . import TaskSpec

log = logging.getLogger(__name__)

#: How long a task may take before it is stopped. Shorter than a build's:
#: these read a screen or two, and one that has spent five minutes is one
#: that is lost rather than slow.
BUDGET_SECONDS = 300.0


@dataclass
class Doing:
    """What a task's `run` is handed: the phone, its inputs, and where
    the screens go. The one seam between a task and the world - swap
    what is here and the same task runs live, against a fake, or over a
    recording (`replay.feed`)."""

    client: Client
    phone_id: str
    settings: Settings
    inputs: dict
    serial: str = ""
    artifact_dir: Path | None = None
    watch: Callable[[], None] | None = None
    budget_seconds: float = BUDGET_SECONDS
    said: list[str] = field(default_factory=list)

    def input(self, name: str, default: str = "") -> str:
        return str(self.inputs.get(name, default) or "")


def one(settings: Settings, spec: TaskSpec, given: dict, *, phone_id: str,
        client: Client, ledger: Ledger, serial: str = "",
        by_id: int | None = None, job_id: int | None = None,
        watch: Callable[[], None] | None = None,
        budget_seconds: float = BUDGET_SECONDS) -> Build:
    """One task on one phone. Returns the Build that says how it ended.

    The row is opened first so a run that dies mid-way leaves one saying
    `running`; it is closed in the same `finally` that lets the phone go,
    so there is no path out of here that writes nothing.
    """
    checked = spec.check(given)
    build = Build(index=0, phone_id=phone_id, serial=serial)
    run = PhoneRun(client, build)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = settings.artifact_dir / f"{stamp}-task-{spec.key}-{serial or phone_id}"
    run_id = _open_row(settings, spec, checked, phone_id, serial, by_id, job_id)

    try:
        phones.ensure_running(client, phone_id)
        doing = Doing(client=client, phone_id=phone_id, settings=settings,
                      inputs=checked, serial=serial, artifact_dir=folder,
                      watch=watch, budget_seconds=budget_seconds)
        outcome = spec.module().run(doing)
        # `steps` is read off `trails` - the phase and its screens - so
        # the path is recorded the way a build's is, and one cell reads
        # the same whichever kind of job wrote it.
        build.trails.append((spec.key, list(outcome.trail)))
        return run.finish(outcome.reason, outcome.detail, ok=outcome.ok)
    except Exception as exc:                                      # noqa: BLE001
        return _ended_by(exc, run.finish, settings, f"the task {spec.key}")
    finally:
        _close_row(settings, run_id, build, folder)
        _let_the_phone_go(client, settings, ledger, build, phone_id)


def _open_row(settings: Settings, spec: TaskSpec, checked: dict,
              phone_id: str, serial: str, by_id: int | None,
              job_id: int | None) -> int | None:
    """The row, when there is a store to put it in. A playground with the
    store off runs the task and records nothing, which is the right
    answer there and must not be an error."""
    if not settings.store_enabled:
        return None
    from ..store import task_runs

    try:
        return task_runs.start(settings, task=spec.key,
                               inputs=spec.public(checked), phone_id=phone_id,
                               serial=serial, by_id=by_id, job_id=job_id)
    except Exception as exc:                                      # noqa: BLE001
        log.error("could not open a row for %s (%s); running anyway",
                  spec.key, exc)
        return None


def _close_row(settings: Settings, run_id: int | None, build: Build,
               folder: Path) -> None:
    """Close the row, and put the screens where a console on another host
    can read them - the same mirror a build's artifacts go through."""
    if run_id is None:
        return
    from ..store import artifacts as store_artifacts
    from ..store import task_runs

    verdict = failures.verdict(build.status)
    try:
        store_artifacts.put_dir(settings, folder, build.serial or build.phone_id)
    except Exception as exc:                                      # noqa: BLE001
        log.debug("the screens of %s stayed on disk (%s)", folder, exc)
    task_runs.finish(
        settings, run_id, ok=build.ok, reason=build.status,
        blame="" if build.ok else verdict.blame, detail=build.detail,
        seconds=build.seconds, api_calls=build.api_calls,
        trail=build.steps.split(" > ") if build.steps else (),
        folder=folder.name, serial=build.serial)
