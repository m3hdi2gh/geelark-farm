"""Run the pipeline for every pending row.

Per account, in order:

    validate -> check proxy -> create or reuse phone -> boot
    -> sign into Google -> verify against the device
    -> install the app  -> verify against the device
    -> stop the phone   -> write the outcome back to the sheet

Rules that shape the code:

- **The proxy is checked before a phone is created**, so a dead row costs
  nothing.
- **A phone is stopped in a finally block, always.** The single-account commands
  can afford to leave one running for inspection; an unattended batch cannot.
  Failed phones are stopped but not deleted, so they can still be examined.
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

from . import phones, proxy, shell
from .accounts import Account
from .api import ApiError, Client
from .config import Settings
from .flows import google_login, play_install
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

    @property
    def status_text(self) -> str:
        return "done" if self.ok else f"failed:{self.reason}"


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
                on_phone: Callable[[str], None] | None = None) -> Result:
    """Take one row from pending to a stopped, ready phone.

    Returns a Result rather than raising: the caller is a batch, and one bad
    account must not end it.
    """
    account: Account = row.account
    started = time.monotonic()
    phone_id = ""
    result = Result(row=row.number, email=account.email, ok=False,
                    reason="not_started")

    def finish(ok: bool, reason: str, detail: str = "", serial: str = "") -> Result:
        result.ok, result.reason, result.detail = ok, reason, detail
        result.phone_id, result.serial = phone_id, serial
        result.seconds = time.monotonic() - started
        return result

    try:
        parsed = proxy.parse(account.proxy)
        proxy.check(client, parsed)          # before anything is created
    except proxy.ProxyError as exc:
        sheet.fail(row, "proxy_unusable", note=str(exc)[:200])
        return finish(False, "proxy_unusable", str(exc))

    # Reuse the phone a previous attempt created for this row: it is already
    # bound to the right proxy, and creating another one pays twice.
    if _existing_phone(client, row.phone_id):
        phone_id = row.phone_id
        log.info("row %d: reusing phone %s", row.number, phone_id)
    else:
        entry = phones.create(client, settings, parsed, ledger=ledger,
                              label=account.label)
        phone_id = entry.phone_id
        result.serial = str(entry.serial or "")

    if on_phone:
        # Register before booting, so an interrupt during boot still knows
        # about this phone.
        on_phone(phone_id)
    ledger.claim(phone_id, label=account.label)
    sheet.claim(row, phone_id=phone_id)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    artifact_dir = settings.artifact_dir / f"{stamp}-row{row.number}"

    try:
        phones.ensure_running(client, phone_id)
        if on_ready:
            # The caller may want to watch: fired after boot, before the
            # first screen is touched, so nothing is missed.
            on_ready(phone_id)

        login = google_login.sign_in(
            client, phone_id, account,
            budget_seconds=settings.login_budget_seconds,
            artifact_dir=artifact_dir,
        )
        if not login.ok:
            sheet.fail(row, login.reason, note=login.detail[:200],
                       phone_id=phone_id)
            return finish(False, login.reason, login.detail)

        installed = play_install.install(
            client, phone_id, settings.target_package,
            budget_seconds=settings.install_budget_seconds,
            artifact_dir=artifact_dir,
        )
        if not installed.ok:
            sheet.fail(row, installed.reason, note=installed.detail[:200],
                       phone_id=phone_id)
            return finish(False, installed.reason, installed.detail)

        # Both steps already verified against the device; this is the record of
        # what the phone is being handed over with.
        packages = shell.third_party_packages(client, phone_id)
        note = f"{account.email}; apps: {', '.join(packages) or 'none'}"
        sheet.succeed(row, phone_id=phone_id, serial=result.serial, note=note[:200])
        return finish(True, "ready", note, serial=result.serial)

    except Exception as exc:                                      # noqa: BLE001
        # Deliberately broad. Whatever went wrong, this row's reason must reach
        # the sheet: an exception escaping here leaves the row stuck on
        # "running", which no later run will select - the work is neither done
        # nor retryable, and the phone it names is invisible.
        log.exception("row %d failed with an unhandled error", row.number)
        sheet.fail(row, "error", note=str(exc)[:200], phone_id=phone_id)
        return finish(False, "error", str(exc))
    finally:
        # Unconditional: an unattended batch must never leave a phone billing,
        # whatever went wrong - including Ctrl+C.
        if phone_id:
            try:
                phones.stop(client, phone_id)
                log.info("row %d: stopped %s", row.number, phone_id)
            except Exception as exc:                              # noqa: BLE001
                log.error("row %d: COULD NOT STOP %s (%s) - run 'geelark reap'",
                          row.number, phone_id, exc)
            ledger.release(phone_id, note=result.reason)


def run(client: Client, settings: Settings, *, limit: int | None = None,
        only_row: int | None = None, retry_failed: bool = False,
        dry_run: bool = False, workers: int | None = None,
        on_ready: Callable[[str], None] | None = None) -> list[Result]:
    """Process the sheet's pending rows and return one Result each."""
    from .sheets import selectable

    sheet = Sheet.open(settings)
    rows = sheet.read()
    chosen = selectable(rows, retry_failed=retry_failed)
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

    def note_phone(phone_id: str) -> None:
        with started_lock:
            started.add(phone_id)

    def work(index: int, row: Row) -> Result:
        _context.row = row.number
        print(f"\n=== row {row.number} ({index}/{len(chosen)}): {row.email} ===",
              flush=True)
        result = process_row(client, settings, sheet, row, ledger,
                             on_ready=on_ready, on_phone=note_phone)
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
            lines.append(f"          phone {r.phone_id}"
                         + (f" (serial {r.serial})" if r.serial else ""))

    ready = sum(1 for r in results if r.ok)
    lines.append("-" * 72)
    lines.append(f" {ready}/{len(results)} phones ready. "
                 f"All phones are stopped; nothing is billing.")
    if ready < len(results):
        lines.append(" Failed rows keep their phones for inspection - the sheet "
                     "records why.")
        if artifact_dir:
            lines.append(f" Screen captures: {artifact_dir}")
    return "\n".join(lines)
