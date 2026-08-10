"""Run the pipeline for every pending row.

Per account, in order:

    validate -> check proxy -> create or reuse phone -> boot
    -> sign into Google -> verify against the device
    -> install the app  -> verify against the device
    -> sign into the app, if the row carries app credentials
    -> stop the phone   -> write the outcome back to the sheet

Rules that shape the code:

- **The proxy is checked before a phone is created**, so a dead row costs
  nothing.
- **A phone is stopped in a finally block, always.** The single-account commands
  can afford to leave one running for inspection; an unattended batch cannot.
  A failed phone is stopped and kept, so it can be examined and reused by a
  retry - unless the reason makes it unreusable, in which case it is deleted
  (see UNREUSABLE).
- **A failing row never stops the batch.** Its reason is written to the sheet
  and the run moves on - that is what makes a sheet of fifty rows usable.
- **Every outcome is written to the sheet before the next row starts**, so an
  interrupted run resumes correctly rather than repeating work.
- **Concurrency is across phones, never within one.** One RPA task per phone is
  a hard API constraint, and two flows on one device corrupt each other's screen
  reads. Rows run in parallel up to `MAX_CONCURRENT_PHONES`; each owns exactly
  one phone for its whole life.

## What parallelism required

Three shared things had to be made safe before workers could run at once, and
each failure mode costs money rather than raising:

- the **rate limiter** in `api.py` is a process-wide budget, already
  thread-safe, and blocks rather than rejecting - so more workers means more
  waiting, never a two-hour ban;
- the **ledger** holds its lock across read-modify-write, because a lost entry
  is a phone `reap` cannot account for;
- the **sheet** serialises every gspread call, since gspread is not documented
  thread-safe and several rows finish at once.

Ctrl+C is the other hazard: a worker thread does not receive it, so its `finally`
never runs and its phone would keep billing. `run` therefore tracks every phone
it has started and stops them all on the way out.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import phones, proxy, shell
from .accounts import Account
from .api import ApiError, Client
from .config import Settings
from .flows import chatgpt_login, google_login, play_install
from .ledger import Ledger
from .sheets import Row, Sheet

log = logging.getLogger(__name__)

# Which row the current thread is working on, so interleaved output from
# parallel workers can still be read. Without it, several rows logging
# "entering the password" at once is indistinguishable noise.
_context = threading.local()


class RowContextFilter(logging.Filter):
    """Stamp every log record with the row its thread is processing."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.row = getattr(_context, "row", "-")
        return True


def install_row_logging() -> None:
    """Add the row marker to the root handler's format, once."""
    root = logging.getLogger()
    for handler in root.handlers:
        if any(isinstance(f, RowContextFilter) for f in handler.filters):
            continue
        handler.addFilter(RowContextFilter())
        handler.setFormatter(
            logging.Formatter("%(levelname)s [row %(row)s] %(name)s: %(message)s")
        )


@dataclass
class Result:
    """What happened to one row, for the summary and for the sheet."""

    row: int
    email: str
    ok: bool
    reason: str
    phone_id: str = ""
    serial: str = ""
    seconds: float = 0.0
    detail: str = ""
    # True when this row's phone could not be confirmed stopped. The summary
    # must never claim nothing is billing while this is set.
    still_running: bool = False
    # True when the phone was deleted rather than kept - see UNREUSABLE.
    discarded: bool = False

    @property
    def status_text(self) -> str:
        return "done" if self.ok else f"failed:{self.reason}"


# Failures after which the phone can never become useful, so keeping it only
# holds a plan slot - and a full plan is what stops the *next* row from getting
# a phone at all.
#
# A CAPTCHA is Google's verdict on the proxy's exit address, and the exits here
# are sticky: row 3's answered three checks in a row with the same IP
# (2026-08-04). So a retry through the same proxy meets the same challenge.
#
# This deletes the phone because it was believed a proxy could not be changed
# after creation. It can - `/phone/detail/update`, see phones.set_proxy - and
# `builder.py` swaps the proxy on the phone it already has instead, which is
# the better answer. This path has not been corrected yet; doing so means
# deciding what `--retry-failed` should take the new proxy FROM, since a row
# here names its own.
#
# password_changed is here by decision rather than necessity. Google has
# accepted the address and rejected the password as the old one; the phone
# itself is fine and a corrected sheet could reuse it. But an account whose
# password moved without us is rarely one we get back, and the slot is worth
# more than the wait - so it goes. If those passwords do turn up, take this
# out: the cost of keeping it here is one wasted phone per recovered account.
#
# Everything else keeps its phone. wrong_password is corrected in the sheet and
# retried on the same device, and unknown_screen is a gap in the router that is
# worth looking at before anything is thrown away.
UNREUSABLE = frozenset({"captcha_shown", "password_changed"})

# App-login failures that a different exit address would probably not have.
# The phone keeps its proxy, but a restart opens a new session through it and
# comes out somewhere else, so the row gets one more attempt before it is
# recorded as failed.
#
# Only where the evidence supports it. OpenAI's TLS refusal was measured across
# twelve attempts: every gateway produced both successes and rejections, and
# all four rejections cleared on a later attempt - one whose only difference
# was that the phone had been restarted in between.
#
# The edge refusal - "There is a problem with your request", with a Cloudflare
# Ray ID - is the same kind of verdict: made about where the request came from,
# before the account or the password was examined, and seen from two different
# Cloudflare datacenters on two runs of the same row. So it gets the same one
# extra address.
#
# Note what this costs that row: the flow submits the address twice per
# attempt, so a retried row puts it to OpenAI four times. Spread across two
# network sessions two minutes apart, from different exits - which is the shape
# of the thing that works, not the rapid repetition that a bot-protection layer
# exists to punish.
#
# Nothing else here behaves this way: an emailed code and a wrong password are
# the same on any address.
RETRY_ON_A_NEW_EXIT = frozenset({"network_ssl_rejected", "request_rejected"})

# What that second attempt needs: a restart, a boot, and a login. Below this
# there is no point starting, and the row is better off reporting its reason.
NEW_EXIT_SECONDS = 420


def _discard(client: Client, sheet: Sheet, row: Row, phone_id: str,
             ledger: Ledger, result: Result) -> None:
    """Delete a phone no retry could use, and stop the sheet pointing at it.

    Failing to delete is logged, never raised: the row's real outcome is
    already written, and losing it to a cleanup error would be the worse bug.
    The cost of the failure is one held slot, so it says so.
    """
    try:
        phones.delete(client, [phone_id], ledger=ledger)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("row %d: could not delete %s (%s); it still holds a "
                    "plan slot - 'geelark delete --phone %s'",
                    row.number, phone_id, exc, phone_id)
        return
    result.discarded = True
    log.info("row %d: deleted %s - %s cannot be retried on this proxy",
             row.number, phone_id, result.reason)
    try:
        # The columns named a phone that no longer exists. Clearing them keeps
        # the sheet honest; status and note still carry what happened.
        sheet.update(row, phone_id="", serial="")
    except Exception as exc:                                      # noqa: BLE001
        log.warning("row %d: deleted %s but could not clear the sheet (%s)",
                    row.number, phone_id, exc)


def _existing_phone(client: Client, phone_id: str) -> bool:
    """Whether a phone recorded on a row still exists and is usable."""
    if not phone_id:
        return False
    try:
        return phones.status(client, phone_id) not in (None, phones.EXPIRED)
    except (ApiError, phones.PhoneError):
        return False


def process_row(client: Client, settings: Settings, sheet: Sheet, row: Row,
                ledger: Ledger,
                on_ready: Callable[[str], None] | None = None,
                on_phone: Callable[[str], None] | None = None,
                cancelled: Callable[[], bool] | None = None) -> Result:
    """Take one row from pending to a stopped, ready phone.

    Returns a Result rather than raising: the caller is a batch, and one bad
    account must not end it.
    """
    account: Account = row.account
    started = time.monotonic()
    phone_id = ""
    result = Result(row=row.number, email=account.email, ok=False,
                    reason="not_started")

    # The outer bound on how long this row may hold a phone, and therefore on
    # what it can cost. Each step gets whichever is smaller: its own budget, or
    # what is left of this one. Without that the step budgets simply add up -
    # boot, login and install together exceed ACCOUNT_BUDGET_SECONDS, so the
    # setting described as a spend cap capped nothing.
    deadline = started + settings.account_budget_seconds

    def remaining() -> float:
        return deadline - time.monotonic()

    def finish(ok: bool, reason: str, detail: str = "", serial: str = "") -> Result:
        result.ok, result.reason, result.detail = ok, reason, detail
        result.phone_id = phone_id
        # Never blank a serial already known. The failure paths call finish()
        # without one, which used to erase the serial recorded when the phone
        # was created - so every failed row reported a phone with no serial,
        # and the console fell back to eight characters of the phone id.
        result.serial = serial or result.serial
        result.seconds = time.monotonic() - started
        return result

    try:
        parsed = proxy.parse(account.proxy)
        proxy.check(client, parsed)          # before anything is created
    except proxy.ProxyError as exc:
        sheet.fail(row, "proxy_unusable", note=str(exc)[:200])
        return finish(False, "proxy_unusable", str(exc))

    # Getting a phone is inside its own handler, because it is the one step
    # that can fail before there is anything to clean up - and a failure here
    # used to escape process_row entirely, leaving the row with no recorded
    # reason at all. A full GeeLark plan ([44002]) did exactly that: the row sat
    # at "pending" as though nothing had been tried.
    try:
        # Reuse the phone a previous attempt created for this row: it is
        # already bound to the right proxy, and creating another one pays twice.
        if _existing_phone(client, row.phone_id):
            phone_id = row.phone_id
            log.info("row %d: reusing phone %s", row.number, phone_id)
            # Creation is where the serial normally comes from, so a reused
            # phone would otherwise leave the column blank. Cheapest first.
            entry = ledger.get(phone_id)
            result.serial = str(
                row.values.get("serial")
                or (entry.serial if entry and entry.serial else "")
                or phones.serial_of(client, phone_id)
            )
        else:
            entry = phones.create(client, settings, parsed, ledger=ledger,
                                  label=account.label)
            phone_id = entry.phone_id
            result.serial = str(entry.serial or "")
    except Exception as exc:                                      # noqa: BLE001
        log.error("row %d: could not get a phone: %s", row.number, exc)
        sheet.fail(row, "no_phone", note=str(exc)[:200])
        return finish(False, "no_phone", str(exc))

    if on_phone:
        # Register before booting, so an interrupt during boot still knows
        # about this phone.
        on_phone(phone_id)
    ledger.claim(phone_id, label=account.label)
    sheet.claim(row, phone_id=phone_id, serial=result.serial)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    artifact_dir = settings.artifact_dir / f"{stamp}-row{row.number}"

    try:
        phones.ensure_running(client, phone_id, timeout=remaining(),
                              cancelled=cancelled)
        if on_ready:
            # The caller may want to watch: fired after boot, before the
            # first screen is touched, so nothing is missed.
            on_ready(phone_id)

        if remaining() <= 0:
            sheet.fail(row, "account_budget_exhausted",
                       note="the phone took too long to boot",
                       phone_id=phone_id, serial=result.serial)
            return finish(False, "account_budget_exhausted",
                          f"no time left after boot "
                          f"({settings.account_budget_seconds}s budget)")

        login = google_login.sign_in(
            client, phone_id, account,
            budget_seconds=min(settings.login_budget_seconds, remaining()),
            artifact_dir=artifact_dir,
        )
        if not login.ok:
            sheet.fail(row, login.reason, note=login.detail[:200],
                       phone_id=phone_id, serial=result.serial)
            return finish(False, login.reason, login.detail)

        if remaining() <= 0:
            sheet.fail(row, "account_budget_exhausted",
                       note="signed in, but no time left to install",
                       phone_id=phone_id, serial=result.serial)
            return finish(False, "account_budget_exhausted",
                          "signed in, but the account budget ran out first")

        installed = play_install.install(
            client, phone_id, settings.target_package,
            budget_seconds=min(settings.install_budget_seconds, remaining()),
            artifact_dir=artifact_dir,
        )
        if not installed.ok:
            sheet.fail(row, installed.reason, note=installed.detail[:200],
                       phone_id=phone_id, serial=result.serial)
            return finish(False, installed.reason, installed.detail)

        # The app's own account, if the row carries one. A row without those
        # columns is complete at this point, which is what keeps a sheet that
        # predates them working unchanged.
        app_note = ""
        if account.app is not None:
            if remaining() <= 0:
                sheet.fail(row, "account_budget_exhausted",
                           note="installed, but no time left to sign into the app",
                           phone_id=phone_id, serial=result.serial)
                return finish(False, "account_budget_exhausted",
                              "installed, but the account budget ran out first")

            def app_login():
                return chatgpt_login.sign_in(
                    client, phone_id, account.app,
                    package=settings.target_package,
                    budget_seconds=min(settings.app_login_budget_seconds,
                                       remaining()),
                    artifact_dir=artifact_dir,
                )

            signed = app_login()
            took_a_new_exit = False
            if (signed.reason in RETRY_ON_A_NEW_EXIT
                    and remaining() > NEW_EXIT_SECONDS):
                # Not the account and not the proxy: measured across twelve
                # attempts, every gateway produced both successes and this
                # failure, and all four rejections cleared on a later attempt.
                # What every one of those later attempts had in common was a
                # phone restart, which opens a new session through the proxy
                # and comes out of a different exit address.
                #
                # So the run does that itself rather than waiting for someone
                # to type --retry-failed. It belongs here rather than in the
                # flow: the flow acts on a device, this layer owns whether the
                # device is running.
                log.warning("row %d: %s - restarting the phone for a new exit "
                            "address and trying the app login once more",
                            row.number, signed.reason)
                phones.stop(client, phone_id)
                time.sleep(5)
                phones.ensure_running(client, phone_id, timeout=remaining(),
                                      cancelled=cancelled)
                took_a_new_exit = True
                signed = app_login()

            if not signed.ok:
                # Named apart from the Google reasons on purpose. The phone is
                # not a failure in the way a phone that never signed in is: it
                # has the account, it has the app, and only the last step is
                # missing - so the reason has to say which login it was, or the
                # runbook entry someone reaches for will be the wrong one.
                reason = f"app_{signed.reason}"
                # The note leads with what still works. Every app_ failure
                # leaves a phone that is signed into Google and has the app on
                # it - only the last step is missing - and the fix is almost
                # always a different app account rather than anything about
                # this phone. Whoever reads the sheet needs that before they
                # decide to throw it away.
                note = (f"phone is ready: Google signed in, app installed. "
                        f"Only the app login failed - {signed.detail}")
                if took_a_new_exit:
                    # The advice for this reason opens with "retry first", and
                    # that is exactly what just happened - refused twice, on
                    # two different exit addresses. Saying so is the difference
                    # between a sheet suggesting what was already tried and a
                    # sheet naming the next thing to do.
                    note = ("ALREADY RETRIED on a second exit address and "
                            "refused again - change the proxy AND delete this "
                            "phone. " + note)
                sheet.fail(row, reason, note=note[:200],
                           phone_id=phone_id, serial=result.serial)
                return finish(False, reason, signed.detail)
            app_note = f"; app: {account.app.email}"

        # Every step above verified before returning; this is the record of
        # what the phone is being handed over with.
        packages = shell.third_party_packages(client, phone_id)
        note = f"{account.email}; apps: {', '.join(packages) or 'none'}{app_note}"
        sheet.succeed(row, phone_id=phone_id, serial=result.serial, note=note[:200])
        return finish(True, "ready", note, serial=result.serial)

    except Exception as exc:                                      # noqa: BLE001
        # Deliberately broad. Whatever went wrong, this row's reason must reach
        # the sheet: an exception escaping here leaves the row stuck on
        # "running", which no later run will select - the work is neither done
        # nor retryable, and the phone it names is invisible.
        log.exception("row %d failed with an unhandled error", row.number)
        sheet.fail(row, "error", note=str(exc)[:200],
                   phone_id=phone_id, serial=result.serial)
        return finish(False, "error", str(exc))
    finally:
        # Unconditional: an unattended batch must never leave a phone billing,
        # whatever went wrong - including Ctrl+C.
        if phone_id:
            try:
                phones.stop(client, phone_id)
                log.info("row %d: stopped %s", row.number, phone_id)
            except Exception as exc:                              # noqa: BLE001
                # Recorded on the result, not only logged. The summary asserts
                # that nothing is billing, and it may only say so if that is
                # true - a log line hundreds of lines up is not a substitute.
                result.still_running = True
                log.error("row %d: COULD NOT STOP %s (%s) - run 'geelark reap'",
                          row.number, phone_id, exc)
            else:
                # Only after a confirmed stop: deleting a running phone is not
                # a documented way to end billing, so a phone this run could
                # not stop is left for reap rather than deleted underneath it.
                if result.reason in UNREUSABLE:
                    _discard(client, sheet, row, phone_id, ledger, result)
            ledger.release(phone_id, note=result.reason)


class Reporter(Protocol):
    """Where a run announces its progress.

    The plain CLI prints lines as they happen; the interactive console draws a
    live table instead. Neither knows about the other - `run` just tells whoever
    is listening when a row starts and finishes.
    """

    def start(self, index: int, row: Row) -> None: ...
    def finish(self, result: Result) -> None: ...


def run(client: Client, settings: Settings, *, limit: int | None = None,
        only_row: int | None = None, retry_failed: bool = False,
        failed_only: bool = False,
        dry_run: bool = False, workers: int | None = None,
        on_ready: Callable[[str], None] | None = None,
        reporter: Reporter | None = None) -> list[Result]:
    """Process the sheet's pending rows and return one Result each."""
    from .sheets import selectable

    sheet = Sheet.open(settings)
    rows = sheet.read()
    chosen = selectable(rows, retry_failed=retry_failed,
                        failed_only=failed_only)
    if only_row is not None:
        chosen = [r for r in chosen if r.number == only_row]
    if limit:
        chosen = chosen[:limit]

    skipped = [r for r in rows if r.error]
    for row in skipped:
        log.warning("row %d (%s) is unusable: %s",
                    row.number, row.email or "?", row.error)

    if dry_run:
        print(f"{len(chosen)} row(s) would be processed:")
        for row in chosen:
            print(f"  row {row.number}  {row.email}")
        if skipped:
            print(f"\n{len(skipped)} unusable row(s) would be skipped.")
        print("\nNothing was created and nothing was written (--dry-run).")
        return []

    ledger = Ledger.load(settings.state_dir)
    phones.prune_ledger(client, ledger)
    install_row_logging()

    if not chosen:
        return []

    count = max(1, workers or settings.max_concurrent_phones)
    if on_ready and count > 1:
        # --watch stops for a keypress; several phones cannot share one prompt.
        log.info("--watch runs one row at a time")
        count = 1
    count = min(count, len(chosen))

    # Phones this run has started, so an interrupt can stop every one of them.
    # A worker thread never receives Ctrl+C, so its own finally will not fire.
    started: set[str] = set()
    started_lock = threading.Lock()

    # And the other half of an interrupt: telling the workers to stop waiting.
    # Stopping their phones is not enough on its own - they carry on polling
    # the phone that was just stopped underneath them, for the rest of the boot
    # timeout, and a ThreadPoolExecutor's threads are not daemons, so Python
    # joins them on the way out and the process cannot exit (2026-08-08).
    shutting_down = threading.Event()

    def note_phone(phone_id: str) -> None:
        with started_lock:
            started.add(phone_id)

    def work(index: int, row: Row) -> Result:
        _context.row = row.number
        if reporter:
            reporter.start(index, row)
        else:
            print(f"\n=== row {row.number} ({index}/{len(chosen)}): "
                  f"{row.email} ===", flush=True)
        result = process_row(client, settings, sheet, row, ledger,
                             on_ready=on_ready, on_phone=note_phone,
                             cancelled=shutting_down.is_set)
        if reporter:
            reporter.finish(result)
        else:
            mark = "OK" if result.ok else "FAIL"
            print(f"  row {row.number} {mark}: {result.reason} "
                  f"({result.seconds:.0f}s)", flush=True)
        return result

    if count == 1:
        results: list[Result] = []
        try:
            for index, row in enumerate(chosen, start=1):
                results.append(work(index, row))
        except KeyboardInterrupt:
            print("\ninterrupted - stopping here", flush=True)
            shutting_down.set()
            _stop_all(client, started, ledger)
        return results

    log.info("%d rows, %d at a time", len(chosen), count)
    futures = {}
    try:
        with ThreadPoolExecutor(max_workers=count,
                                thread_name_prefix="row") as pool:
            futures = {pool.submit(work, i, r): r
                       for i, r in enumerate(chosen, start=1)}
            wait(futures, return_when=FIRST_EXCEPTION)
    except KeyboardInterrupt:
        print("\ninterrupted - stopping every phone this run started", flush=True)
        # Set first. Cancelling a future does nothing to a row already running,
        # and stopping its phone does not release it either - it carries on
        # polling the phone that was just stopped underneath it. This is what
        # lets those workers finish, and therefore what lets the process exit.
        shutting_down.set()
        for future in futures:
            future.cancel()
        _stop_all(client, started, ledger)

    results = []
    for future, row in futures.items():
        if future.cancelled():
            continue
        try:
            results.append(future.result())
        except Exception as exc:                                  # noqa: BLE001
            log.error("row %d raised: %s", row.number, exc)
            results.append(Result(row=row.number, email=row.email, ok=False,
                                  reason="error", detail=str(exc)))
    results.sort(key=lambda r: r.row)
    return results


def _stop_all(client: Client, phone_ids: set[str], ledger: Ledger) -> None:
    """Last-resort cleanup: stop every phone this run started.

    Only reached when the normal per-row `finally` cannot run - an interrupt in
    the main thread while workers hold phones. Failures are reported, never
    swallowed: this is the last thing between a crash and a phone billing all
    night.
    """
    for phone_id in sorted(phone_ids):
        try:
            phones.stop(client, phone_id)
            ledger.release(phone_id, note="stopped by interrupt cleanup")
            print(f"  stopped {phone_id}", flush=True)
        except Exception as exc:                                  # noqa: BLE001
            log.error("COULD NOT STOP %s (%s) - run 'geelark reap' now",
                      phone_id, exc)


def summarise(results: list[Result], *, artifact_dir: Path | None = None) -> str:
    """The end-of-run table. Rows are what the user actually acts on next."""
    if not results:
        return "nothing was processed"

    lines = ["", "=" * 72, "SUMMARY", "=" * 72]
    for r in results:
        mark = "ready " if r.ok else "FAILED"
        lines.append(f" {mark}  row {r.row:<3} {r.email:<34} "
                     f"{r.reason:<26} {r.seconds:>5.0f}s")
        if r.phone_id:
            detail = f" (serial {r.serial})" if r.serial else ""
            if r.discarded:
                detail += " - deleted, its slot is free again"
            lines.append(f"          phone {r.phone_id}{detail}")

    ready = sum(1 for r in results if r.ok)
    unstopped = [r for r in results if r.still_running]
    lines.append("-" * 72)
    lines.append(f" {ready}/{len(results)} phones ready.")
    if unstopped:
        # The one line anyone reads. It has to be alarming when it should be:
        # a network blip during cleanup once left a phone billing while the
        # summary said everything was stopped.
        lines.append("")
        lines.append(f" *** {len(unstopped)} PHONE(S) COULD NOT BE STOPPED - "
                     f"THESE ARE STILL BILLING ***")
        for r in unstopped:
            lines.append(f"     row {r.row}: {r.phone_id}")
        lines.append(" Run 'geelark reap' now.")
    else:
        lines.append(" All phones are stopped; nothing is billing.")
    if ready < len(results):
        kept = sum(1 for r in results if not r.ok and r.phone_id
                   and not r.discarded)
        discarded = sum(1 for r in results if r.discarded)
        if kept:
            lines.append(" Failed rows keep their phones for inspection and "
                         "retry - the sheet records why.")
        if discarded:
            lines.append(f" {discarded} phone(s) deleted: a CAPTCHA is a verdict "
                         "on the proxy's exit IP. Give those rows a different "
                         "proxy - or use 'geelark build', which swaps the proxy "
                         "on the phone rather than throwing it away.")
        if artifact_dir:
            lines.append(f" Screen captures: {artifact_dir}")
    return "\n".join(lines)
