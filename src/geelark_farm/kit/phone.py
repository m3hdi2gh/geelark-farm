"""The end of a phone's job: how it ended, whether anything is signed
into it after all, deleting one that holds nothing worth keeping, and
the phone let go.

Any automation that drives a phone ends the way a build does:

    run = PhoneRun(client, build)
    try:
        ...                       # return run.finish("ready", ok=True)
    except Exception as exc:      # noqa: BLE001
        return _ended_by(exc, run.finish, settings, "what it was doing")
    finally:
        _let_the_phone_go(client, settings, ledger, build, phone_id)

Moved out of builder.py unchanged, under their old names (the builder
review, 2026-09-23). Logs under the builder's logger name, as before.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .. import cancel, failures, phones, shell
from ..api import Client, TransportError
from ..build_result import Build, outcome_of
from ..cancel import Aborted
from ..config import Settings
from ..ledger import Ledger
from ..pools import Book, Resource

log = logging.getLogger("geelark_farm.builder")


# What a build needs left to be worth starting another attempt: a stop, a boot
# and a login. Below this the honest thing is to report what it has.
ATTEMPT_SECONDS = 420


def _calls(client) -> int:
    count = getattr(client, "calls_here", None)
    return int(count()) if callable(count) else 0


@dataclass
class PhoneRun:
    """One job on one phone: when it started, what the client had spent in
    calls by then, and the one `finish` that says how it ended - seconds
    and calls included, on every ending. The build and the finish each
    had their own copy, and the finish's counted no calls (the builder
    review, 2026-09-23)."""

    client: Client | None
    build: Build
    started: float = field(default_factory=time.monotonic)
    calls_before: int | None = None

    def __post_init__(self) -> None:
        if self.calls_before is None:
            self.calls_before = _calls(self.client)

    def finish(self, status: str, detail: str = "", ok: bool = False) -> Build:
        build = self.build
        build.ok, build.status, build.detail = ok, status, detail
        build.seconds = time.monotonic() - self.started
        build.api_calls = _calls(self.client) - self.calls_before
        return build


def _signed_in_after_all(client: Client, build: Build) -> bool:
    """Ask the device itself, once, before throwing the phone away.

    The flow's verdict is what the run saw while it was watching. Google adds
    the account after its own consent closes, and it takes as long as it takes:
    every phone that survived a `stuck_on_sign_in_closed` failure on 2026-09-04
    was found later holding the account it was supposed to have, with nothing
    in the device's own account history but the add. The ones that did not
    survive were deleted by this path, signed in, on the strength of a verdict
    that was already out of date when it was written.

    Deleting is the one thing here that cannot be undone: a phone kept in error
    is a row somebody closes, and a phone deleted in error is a Gmail, a proxy
    and the minutes spent on both. So this is asked strictly, and anything that
    is not a clear "there is nothing on it" keeps the phone.
    """
    try:
        present = shell.device_accounts(client, build.phone_id, strict=True)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("phone %s could not say whether it is signed in (%s), so "
                    "it is kept rather than deleted",
                    build.serial or build.phone_id, exc)
        return True
    if not present:
        return False
    log.warning("phone %s is signed in as %s after all - the run gave up "
                "before Google finished. Keeping it.",
                build.serial or build.phone_id, ", ".join(present))
    return True


def _discard(client: Client, book: Book, ledger: Ledger,
             build: Build, *, exit_row: Resource | None = None) -> bool:
    """Delete a phone nothing was ever signed into, and free its exit.

    Returns whether it went. A delete that fails leaves the phone to be stopped
    and recorded the ordinary way - half-deleting it, with its row dropped and
    the device still there, is the one outcome worse than keeping it.
    """
    try:
        # Stopped first, and not as a courtesy: GeeLark refuses to delete a
        # running phone, and this runs before the stop that the ordinary path
        # does at the end. Both phones this discarded on 2026-08-17 were still
        # running when it asked, so both refusals came back under failDetails
        # while the tool went on to drop their rows.
        phones.stop(client, build.phone_id)
        phones.wait_until_stopped(client, build.phone_id)
        phones.delete(client, [build.phone_id], ledger=ledger)
    except Exception as exc:                                      # noqa: BLE001
        log.error("phone %s has no Google account on it and could not be "
                  "deleted (%s); it is recorded and left alone",
                  build.serial or build.phone_id, exc)
        return False
    log.info("deleted phone %s - nothing was ever signed into it (%s)",
             build.serial or build.phone_id, outcome_of(build))
    book.record_history(
        Serial=build.serial, Event="discarded",
        Seconds=f"{build.seconds:.0f}", Proxy=build.proxy_name or build.proxy,
        Steps=build.steps,
        Note=(f"Deleted rather than kept - nothing was ever signed into it. "
              f"{outcome_of(build).capitalize()}."))
    # The exit this build owned (`exit_row`, from its lease), not the one the
    # phone was last on: that may be borrowed from another phone, and freeing
    # it put another phone's exit back on the shelf (the builder review,
    # 2026-09-23). Without a lease, the exit it was on - and only if nothing
    # else is recorded behind it.
    resource = exit_row
    if resource is None and build.proxy:
        resource = book.proxies.find_proxy(build.proxy)
        behind = str((getattr(resource, "values", None) or {})
                     .get(book.proxies.serial_column) or "") if resource else ""
        if behind and behind != str(build.serial):
            resource = None
    if resource is not None:
        book.proxies.release(resource, note=(
            "Free again - the phone taken on it had nothing signed in and was "
            "deleted."))
    build.phone_id = ""
    return True


def _ended_by(exc: Exception, finish, settings: Settings, what: str) -> Build:
    """How an exception out of a phone job ends it - one ladder for the
    build and the finish, which had two copies of it (the builder review,
    2026-09-23). Called from inside their `except`, so `log.exception`
    still has the traceback. `finish` is a PhoneRun's, or anything with
    its signature."""
    if isinstance(exc, Aborted):
        return finish(str(exc), failures.situation(str(exc)))
    if isinstance(exc, TransportError):
        # The machine lost its network, which is not an error nobody planned
        # for - it is a named thing that costs nothing. Reported as such, with
        # the traceback left in the log file rather than dumped over a live
        # table: two hundred lines of urllib3 to say the connection went away
        # (2026-08-17).
        log.error("the network went away: %s", exc)
        return finish("network_unreachable",
                      failures.situation("network_unreachable"))
    if isinstance(exc, phones.WaitInterrupted):
        # The run is shutting down, not a phone that would not start: filed
        # as the stop it is, and the empty phone is kept (KEPT_WHEN_EMPTY) -
        # a delete needs a stop, a wait and a call, and the process is going
        # down (the builder review, 2026-09-23).
        log.info("%s", exc)
        return finish("interrupted", failures.situation("interrupted"))
    if isinstance(exc, phones.PhoneCapacityError):
        # Before `PhoneError`, because it is one. GeeLark had no machine of
        # this Android version free, which says nothing about this phone, this
        # account or this row - and `start` has already asked several times.
        log.warning("no capacity at GeeLark: %s", exc)
        return finish("no_capacity", failures.situation("no_capacity"))
    if isinstance(exc, phones.PhoneError):
        # Expected, and named. It used to reach the catch-all below and be
        # reported as "an error nobody planned for", which is the wrong thing
        # to tell someone about a phone that simply did not boot - or about one
        # that was deleted underneath the build, which is a different sentence
        # and points at a different culprit. A GeeLark capacity refusal, which
        # is nobody's fault at all, counted against the breaker as one in the
        # finish until it had the same naming (2026-08-28).
        vanished = "env not found" in str(exc) or "no longer exists" in str(exc)
        if not vanished:
            phones._remember_refusal(settings, str(exc))
        return finish("phone_is_gone" if vanished else "phone_would_not_start",
                      str(exc))
    # Deliberately broad. Whatever went wrong, the resources this job is
    # holding must go back and the phone must be stopped - an exception
    # escaping here leaves three tabs saying `in_use` and a phone billing.
    log.exception("%s failed with an unhandled error", what)
    return finish("error", f"an error nobody planned for stopped it: {exc}")


def _let_the_phone_go(client: Client, settings: Settings, ledger: Ledger,
                      build: Build, phone_id: str) -> None:
    """The last of every phone job: the phone stopped - billing ends - and
    its claim released, then the stop request answered. Shared by the build
    and the finish, which each had a copy. `phone_id` empty: nothing left to
    stop (a discarded phone, or none was made)."""
    if phone_id:
        try:
            phones.stop(client, phone_id)
            log.info("stopped %s", phone_id)
        except Exception as exc:                                  # noqa: BLE001
            build.still_running = True
            log.error("COULD NOT STOP %s (%s) - run 'geelark reap'",
                      phone_id, exc)
        ledger.release(phone_id, note=build.status)
    # Last, so the row says Stopping until there is nothing left to stop.
    cancel._stop_honoured(settings, build.serial)
