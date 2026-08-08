"""An interactive console for the whole tool.

`geelark ui` opens a menu; everything the CLI can do is reachable from it,
and a batch draws a live table instead of a wall of interleaved log lines.

This is a layer over the same functions the plain commands call. The
non-interactive CLI stays exactly as it was, because that is what cron, CI and
a piped `geelark run` depend on - a menu that prompts is useless to them.

Two things the menu is for beyond convenience:

- **Seeing the state before acting.** The dashboard puts the sheet, the phones
  and the plan's free slots on one screen. Nearly every confusing moment in
  this project came from acting without one of those three.
- **Watching a parallel run.** Four rows logging at once is unreadable in
  sequence; as a table with one line each it is obvious.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from . import phones
from .api import ApiError, TransportError, build_client
from .config import Settings
from .ledger import Ledger
from .orchestrator import Result, install_row_logging
from .orchestrator import run as run_batch
from .sheets import Sheet, SheetError, selectable

console = Console()

OK = "green"
WARN = "yellow"
BAD = "red"
DIM = "bright_black"


# --------------------------------------------------------------- dashboard
@dataclass
class Snapshot:
    """Everything worth knowing before deciding what to do next."""

    rows_total: int = 0
    rows_done: int = 0
    rows_pending: int = 0
    rows_failed: int = 0
    # What --retry-failed would actually pick up: the pending rows as well as
    # the failed and stuck ones. Counting only the failures understated it.
    rows_retryable: int = 0
    rows_bad: int = 0
    phones_total: int = 0
    phones_running: int = 0
    slots_total: int = 0
    slots_free: int = 0
    parallels: int = 0
    error: str = ""


# The plan endpoint allows one request per minute, and the menu redraws after
# every action - without this the dashboard answers [40007] more often than it
# answers the question. Plan limits change on the scale of a subscription, so a
# minute-old value is as good as a fresh one.
_PLAN_TTL = 55.0
PLAN_RATE_LIMITED = 40007
_plan_cache: tuple[float, dict] | None = None


def cached_plan(client) -> dict:
    """The plan, at most once a minute.

    Its own limiter is separate from the 200/min one in api.py, so the shared
    rate limiter cannot see it coming. If the window has not elapsed the call
    returns [40007]; the last known answer is better than an error message
    where a number should be, since these limits barely change.
    """
    global _plan_cache
    now = time.monotonic()
    if _plan_cache and now - _plan_cache[0] < _PLAN_TTL:
        return _plan_cache[1]
    try:
        info = phones.plan(client)
    except ApiError as exc:
        if exc.code == PLAN_RATE_LIMITED and _plan_cache:
            return _plan_cache[1]
        raise
    _plan_cache = (now, info)
    return info


def take_snapshot(settings: Settings) -> Snapshot:
    """Read the world. Failures are reported, never raised - a dashboard that
    crashes because one lookup failed is worse than a partial one, and each
    source is caught on its own so a rate-limited plan lookup cannot blank the
    phone count that was already read."""
    snap = Snapshot()
    client = build_client(settings)
    try:
        items = phones.listing(client)
        snap.phones_total = len(items)
        snap.phones_running = sum(1 for p in items
                                  if p.get("status") in (phones.RUNNING,
                                                         phones.STARTING))
    except (ApiError, TransportError) as exc:
        snap.error = str(exc).splitlines()[0]

    try:
        info = cached_plan(client)
        snap.slots_total = info.get("profiles") or 0
        snap.slots_free = info.get("availableProfiles") or 0
        snap.parallels = info.get("parallels") or 0
    except (ApiError, TransportError) as exc:
        snap.error = snap.error or str(exc).splitlines()[0]

    if settings.sheet_id:
        try:
            rows = Sheet.open(settings).read()
            snap.rows_total = len(rows)
            snap.rows_done = sum(1 for r in rows if r.is_done)
            snap.rows_failed = sum(1 for r in rows if r.is_failed)
            snap.rows_bad = sum(1 for r in rows if r.error)
            snap.rows_pending = len(selectable(rows))
            snap.rows_retryable = len(selectable(rows, retry_failed=True))
        except SheetError as exc:
            snap.error = snap.error or str(exc).splitlines()[0]
    return snap


def dashboard(snap: Snapshot) -> Panel:
    table = Table.grid(padding=(0, 3))
    table.add_column(style=DIM, justify="right")
    table.add_column()

    sheet_bits = [f"[{OK}]{snap.rows_done} done[/]"]
    if snap.rows_pending:
        sheet_bits.append(f"[{WARN}]{snap.rows_pending} to do[/]")
    if snap.rows_failed:
        sheet_bits.append(f"[{BAD}]{snap.rows_failed} failed[/]")
    if snap.rows_bad:
        sheet_bits.append(f"[{BAD}]{snap.rows_bad} unusable[/]")
    table.add_row("sheet",
                  f"{snap.rows_total} rows   " + "   ".join(sheet_bits)
                  if snap.rows_total else "[bright_black]not configured[/]")

    # Running phones are the only thing here that costs money by the second.
    billing = (f"[{BAD}]{snap.phones_running} RUNNING (billing)[/]"
               if snap.phones_running else f"[{OK}]none running[/]")
    table.add_row("phones", f"{snap.phones_total} total   {billing}")

    slots = f"{snap.slots_free} free of {snap.slots_total}"
    if snap.slots_total and not snap.slots_free:
        slots = f"[{BAD}]{slots} - the next create will fail[/]"
    parallel = (f"   parallel limit [{WARN}]{snap.parallels}[/]"
                if not snap.parallels else f"   parallel limit {snap.parallels}")
    table.add_row("plan", slots + parallel)

    if snap.error:
        table.add_row("problem", f"[{BAD}]{snap.error}[/]")

    return Panel(table, title="[bold]geelark-farm[/]", border_style=DIM,
                 padding=(1, 2))


# ------------------------------------------------------------ live batch
# How phones.start announces the live view. Matching on the message is the
# price of taking progress from the logs the flows already emit, which is also
# what keeps the flows unaware that a console exists.
LIVE_PREFIX = "watch it live:"
# phones.create announces "created <id> (serial <n>): <model> ...". Reading the
# serial from it fills the phone column while the row is still working, which
# is what mints a fresh link with `geelark start` once this one has expired.
CREATED_SERIAL = re.compile(r"created \S+ \(serial (\w+)\)")


@dataclass
class LiveReporter:
    """Draws a batch as one line per row, updated as it happens.

    Row progress comes from the log records the flows already emit: they are
    stamped with the row number by RowContextFilter, so a handler can turn
    "screen: password_entry" into that row's current step without any flow
    knowing a console exists.
    """

    total: int
    rows: dict[int, dict] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Links seen but not yet printed. Collected here rather than printed from
    # the logging handler, because that runs on the worker threads and every
    # write to the console has to come from the one thread driving Live.
    new_links: list[tuple[int, str, str]] = field(default_factory=list)
    seen_links: set[str] = field(default_factory=set)

    def start(self, index: int, row) -> None:
        with self.lock:
            self.rows[row.number] = {
                "email": row.email, "state": "working", "step": "starting",
                "started": time.monotonic(), "seconds": 0.0, "phone": "",
            }

    def finish(self, result: Result) -> None:
        with self.lock:
            entry = self.rows.setdefault(result.row, {"email": result.email})
            entry.update(state="ready" if result.ok else "failed",
                         step=result.reason, seconds=result.seconds,
                         phone=result.serial or result.phone_id[:8])

    def drain_links(self) -> list[tuple[int, str, str]]:
        """Take the links that have arrived since the last call."""
        with self.lock:
            found, self.new_links = self.new_links, []
            return found

    def note(self, row: int, message: str) -> None:
        with self.lock:
            entry = self.rows.get(row)
            if not entry:
                return
            if message.startswith(LIVE_PREFIX):
                # Held in its own field rather than shown as a step. As a step
                # the next log line replaced it within a second, which made the
                # one message worth clicking the one you could not click.
                url = message[len(LIVE_PREFIX):].strip()
                if url and url not in self.seen_links:
                    self.seen_links.add(url)
                    self.new_links.append((row, entry.get("phone", ""), url))
                return
            found = CREATED_SERIAL.search(message)
            if found:
                entry["phone"] = found.group(1)
            if entry["state"] == "working":
                entry["step"] = message

    def render(self) -> Table:
        """One line per row.

        State is carried by colour and a plain word, never by a glyph: the
        check marks this started with rendered as replacement characters in a
        Windows console, which is the terminal this actually runs in.
        """
        table = Table(box=None, padding=(0, 2))
        # No fixed widths: rich sizes to the content, and a width small enough
        # to look tidy in one terminal truncates the word "working" in another.
        # One state column, not two - on a finished row the state and the last
        # step are the same thing said twice.
        table.add_column("row", justify="right", style=DIM)
        table.add_column("account", overflow="ellipsis", no_wrap=True,
                         max_width=34)
        table.add_column("phone", style=DIM)
        table.add_column("state")
        table.add_column("time", justify="right", style=DIM)

        with self.lock:
            for number in sorted(self.rows):
                e = self.rows[number]
                if e["state"] == "working":
                    style, seconds = WARN, time.monotonic() - e["started"]
                    state = e.get("step", "working")
                elif e["state"] == "ready":
                    style, seconds, state = OK, e["seconds"], "ready"
                else:
                    style, seconds = BAD, e["seconds"]
                    state = e.get("step", "failed")
                table.add_row(str(number), e.get("email", ""),
                              e.get("phone", ""),
                              f"[{style}]{state}[/]", f"{seconds:.0f}s")
        return table


# Which layers narrate. A row's state is the step it has reached, and steps
# happen in the flows and in the phone's lifecycle. screen and shell are the
# mechanics underneath: "tapping 'NEXT' at (615, 843) (clickable=True)" is
# perfectly true and says nothing about where the row has got to - it was what
# the column showed most of the time, because tapping is most of what happens.
#
# An allowlist rather than a denylist of the two noisy modules: a layer added
# below this one should be silent here by default, not until someone notices it
# in the column. phones is on it for its own sake and for two messages the
# console depends on - the serial it creates and the live-view link.
NARRATING = ("geelark_farm.flows.", "geelark_farm.phones", "geelark_farm.proxy")


class ReporterLogHandler(logging.Handler):
    """Feeds each row's current activity into the live table.

    Only INFO from the narrating layers is interesting; warnings and errors
    still reach the normal handler, so a real problem is never swallowed by the
    pretty output.
    """

    def __init__(self, reporter: LiveReporter):
        super().__init__(level=logging.INFO)
        self.reporter = reporter

    def emit(self, record: logging.LogRecord) -> None:
        row = getattr(record, "row", "-")
        if isinstance(row, int) and record.name.startswith(NARRATING):
            self.reporter.note(row, record.getMessage())


def print_new_links(live: Live, reporter: LiveReporter) -> None:
    """Write each live-view link once, above the table.

    This started as a column holding an OSC 8 hyperlink on the word "open".
    Terminals that do not implement OSC 8 - PyCharm's among them - showed the
    word and nothing else, so the only route to the link depended on a terminal
    capability. Printing the URL in full needs nothing from the terminal: it
    stays in the scrollback instead of being redrawn away, terminals that
    linkify URLs of their own accord pick it up, and it can always be selected
    and copied. That works everywhere, so the column went.

    soft_wrap keeps it in one logical line. Rich's own wrapping would insert
    real line breaks mid-URL, and a URL broken by a newline is one no terminal
    will detect and no double-click will select.
    """
    for number, serial, url in reporter.drain_links():
        where = f"row {number}" + (f", phone {serial}" if serial else "")
        live.console.print(f"[{DIM}]{where} - watch live:[/]")
        live.console.print(url, soft_wrap=True)
        live.console.print()


def run_with_live_table(settings: Settings, **kwargs) -> list[Result]:
    """`run`, drawn as a table instead of a scrolling log."""
    client = build_client(settings)
    settings.ensure_dirs()
    install_row_logging()

    reporter = LiveReporter(total=0)
    handler = ReporterLogHandler(reporter)
    root = logging.getLogger()
    # Quieten the stream handler for the duration: its lines would fight with
    # the live table for the same terminal. Warnings and above still print.
    previous = [(h, h.level) for h in root.handlers]
    for existing, _ in previous:
        existing.setLevel(logging.WARNING)
    root.addHandler(handler)

    results: list[Result] = []
    failure: Exception | None = None

    def work() -> None:
        nonlocal results, failure
        try:
            results = run_batch(client, settings, reporter=reporter, **kwargs)
        except Exception as exc:                                  # noqa: BLE001
            failure = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    try:
        with Live(reporter.render(), console=console, refresh_per_second=4,
                  transient=False) as live:
            while worker.is_alive():
                print_new_links(live, reporter)
                live.update(reporter.render())
                time.sleep(0.25)
            print_new_links(live, reporter)
            live.update(reporter.render())
    finally:
        root.removeHandler(handler)
        for existing, level in previous:
            existing.setLevel(level)

    worker.join()
    if failure:
        console.print(f"[{BAD}]the run failed: {failure}[/]")
    return results


def summary_panel(results: list[Result]) -> Panel:
    if not results:
        return Panel("nothing was processed", border_style=DIM)

    ready = sum(1 for r in results if r.ok)
    unstopped = [r for r in results if r.still_running]

    body: list = []
    line = Text(f"{ready}/{len(results)} phones ready",
                style=f"bold {OK if ready == len(results) else WARN}")
    body.append(line)

    if unstopped:
        # The one claim that must never be made loosely.
        body.append(Text(""))
        body.append(Text(f"{len(unstopped)} PHONE(S) COULD NOT BE STOPPED - "
                         f"STILL BILLING", style=f"bold {BAD}"))
        for r in unstopped:
            body.append(Text(f"   row {r.row}: {r.phone_id}", style=BAD))
        body.append(Text("Run 'reap' from the menu now.", style=BAD))
    else:
        body.append(Text("All phones are stopped; nothing is billing.",
                         style=DIM))

    failed = [r for r in results if not r.ok]
    if failed:
        body.append(Text(""))
        for r in failed:
            body.append(Text(f"   row {r.row} {r.email}: {r.reason}", style=BAD))

    return Panel(Group(*body), title="summary", border_style=DIM, padding=(1, 2))


# ------------------------------------------------------------------ views
def rows_table(settings: Settings) -> Table:
    table = Table(box=None, padding=(0, 2))
    table.add_column("row", justify="right", style=DIM, width=3)
    table.add_column("account", overflow="ellipsis", no_wrap=True,
                     max_width=34)
    table.add_column("status")
    table.add_column("phone", style=DIM)

    for row in Sheet.open(settings).read():
        if row.error:
            style, status = BAD, row.error[:44]
        elif row.is_done:
            style, status = OK, "done"
        elif row.is_failed:
            style, status = BAD, row.status
        elif row.status == "running":
            style, status = WARN, "running"
        else:
            style, status = DIM, "pending"
        table.add_row(str(row.number), row.email,
                      f"[{style}]{status}[/]", row.phone_id)
    return table


def phones_table(settings: Settings) -> Table:
    client = build_client(settings)
    ledger = Ledger.load(settings.state_dir)
    phones.prune_ledger(client, ledger)

    table = Table(box=None, padding=(0, 2))
    table.add_column("phone", style=DIM)
    table.add_column("serial", justify="right")
    table.add_column("state")
    table.add_column("device", style=DIM)
    table.add_column("belongs to", overflow="ellipsis", no_wrap=True)

    for item in phones.listing(client):
        state = item.get("status")
        running = state in (phones.RUNNING, phones.STARTING)
        entry = ledger.get(item.get("id"))
        equipment = item.get("equipmentInfo") or {}
        table.add_row(
            str(item.get("id")), str(item.get("serialNo", "?")),
            f"[{BAD if running else OK}]{phones.STATUS_NAMES.get(state, state)}[/]",
            f"{equipment.get('deviceBrand', '?')} {equipment.get('osVersion', '?')}",
            (entry.label if entry else "[bright_black]not in ledger[/]"),
        )
    return table


def plan_panel(settings: Settings) -> Panel:
    client = build_client(settings)
    # Through the cache, like the dashboard: asking twice inside a minute is
    # what the endpoint refuses, and the menu redraws constantly.
    info = cached_plan(client)
    used = len(phones.listing(client))
    total = info.get("profiles") or 0
    free = info.get("availableProfiles") or 0

    table = Table.grid(padding=(0, 3))
    table.add_column(style=DIM, justify="right")
    table.add_column()
    table.add_row("plan", "Pro" if info.get("plan") == 1 else "Base")
    table.add_row("monthly", f"${info.get('monthlyFee')}")
    table.add_row("expires", time.strftime(
        "%Y-%m-%d", time.localtime(info.get("expirationTime", 0))))
    table.add_row("slots", f"{total} total, "
                           f"[{OK if free else BAD}]{free} free[/]")
    table.add_row("", f"{used} are cloud phones")
    other = total - free - used
    if other > 0:
        table.add_row("", f"[{WARN}]{other} held by something else - browser "
                          f"profiles share this pool[/]")
    table.add_row("parallel", str(info.get("parallels"))
                  + (f"  [{WARN}](concurrent phones beyond this may cost extra)[/]"
                     if not info.get("parallels") else ""))
    return Panel(table, title="subscription", border_style=DIM, padding=(1, 2))


# ------------------------------------------------------------------- menu
ACTIONS = [
    ("1", "Run pending rows", "create, sign in, install, stop - the main job"),
    ("2", "Retry failed and stuck rows", "reuses the phones they already have"),
    ("3", "Validate the sheet", "spends nothing"),
    ("4", "Phones", "what exists, and what is billing"),
    ("5", "Stop everything", "ends all billing now"),
    ("6", "Reap", "stop phones nothing is accountable for"),
    ("7", "Subscription", "slots, free slots, parallel limit"),
    ("q", "Quit", ""),
]


def menu() -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_column(style=DIM)
    for key, label, hint in ACTIONS:
        table.add_row(key, label, hint)
    return table


def confirm_run(settings: Settings, snap: Snapshot, *,
                retry_failed: bool) -> dict | None:
    """Ask what to run, and make the cost visible before anything starts."""
    available = snap.rows_retryable if retry_failed else snap.rows_pending
    if not available:
        console.print(f"[{DIM}]nothing to do[/]")
        return None

    # Two separate questions, because they mean different things and only one
    # of them changes the bill. How many rows decides how many phones are
    # created; how many at a time decides only how long the wall clock is.
    count = IntPrompt.ask(f"how many rows (of {available})", default=available)
    count = max(1, min(count, available))

    workers = IntPrompt.ask("how many at a time",
                            default=min(settings.max_concurrent_phones, count))
    workers = max(1, min(workers, count))
    if snap.parallels and workers > snap.parallels:
        console.print(f"[{WARN}]the plan's parallel limit is {snap.parallels}; "
                      f"more may cost extra[/]")
    if count > snap.slots_free:
        console.print(f"[{WARN}]only {snap.slots_free} plan slot(s) are free; "
                      f"rows past that will fail with no_phone[/]")

    console.print(f"\n[{WARN}]{count} row(s), {workers} at a time. "
                  f"Phones bill per running minute.[/]")
    if not Confirm.ask("start", default=True):
        return None
    return {"workers": workers, "retry_failed": retry_failed, "limit": count}


def run_console(settings: Settings) -> int:
    """The loop. Draw the state, take one action, draw it again."""
    console.print()
    while True:
        try:
            snap = take_snapshot(settings)
        except Exception as exc:                                  # noqa: BLE001
            console.print(f"[{BAD}]could not read the current state: {exc}[/]")
            snap = Snapshot(error=str(exc))

        console.print(dashboard(snap))
        console.print(Align.left(menu()))
        console.print()

        try:
            choice = Prompt.ask("action", choices=[k for k, _, _ in ACTIONS],
                                default="q", show_choices=False)
        except (EOFError, KeyboardInterrupt):
            # Ctrl+C at the menu, or stdin closing under it - which is what a
            # Ctrl+C during the batch above leaves behind. Untrapped, this
            # ended the process from inside the loop with a traceback, past
            # every check that says whether anything is still billing
            # (2026-08-08). Quitting is a menu choice, so make it one.
            console.print()
            choice = "q"
        console.print()

        try:
            if choice == "q":
                if snap.phones_running:
                    console.print(f"[{BAD}]{snap.phones_running} phone(s) are "
                                  f"still running and billing.[/]")
                    if Confirm.ask("stop them before quitting", default=True):
                        stop_all(settings)
                return 0

            if choice in ("1", "2"):
                options = confirm_run(settings, snap,
                                      retry_failed=(choice == "2"))
                if options:
                    results = run_with_live_table(settings, **options)
                    console.print(summary_panel(results))

            elif choice == "3":
                console.print(rows_table(settings))

            elif choice == "4":
                console.print(phones_table(settings))

            elif choice == "5":
                stop_all(settings)

            elif choice == "6":
                reap(settings)

            elif choice == "7":
                console.print(plan_panel(settings))

        except (ApiError, TransportError, SheetError) as exc:
            console.print(f"[{BAD}]{exc}[/]")
        except KeyboardInterrupt:
            console.print(f"\n[{DIM}]cancelled[/]")

        console.print()


def stop_all(settings: Settings) -> None:
    client = build_client(settings)
    ledger = Ledger.load(settings.state_dir)
    targets = [p["id"] for p in phones.listing(client)
               if p.get("status") in (phones.RUNNING, phones.STARTING)]
    if not targets:
        console.print(f"[{DIM}]nothing is running[/]")
        return
    for phone_id in targets:
        phones.stop(client, phone_id)
        ledger.release(phone_id, note="stopped from the console")
        console.print(f"[{OK}]stopped {phone_id}[/]")


def reap(settings: Settings) -> None:
    client = build_client(settings)
    ledger = Ledger.load(settings.state_dir)
    verdicts = phones.reapable(client, ledger)
    if not verdicts:
        console.print(f"[{OK}]nothing to reap[/]")
        return
    for phone_id, reason in verdicts:
        console.print(f"  {phone_id}  [{DIM}]{reason}[/]")
    if Confirm.ask(f"stop {len(verdicts)} phone(s)", default=True):
        phones.reap(client, ledger, verdicts=verdicts)
        console.print(f"[{OK}]stopped {len(verdicts)}; billing ended[/]")
