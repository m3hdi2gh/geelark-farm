"""An interactive console for the whole tool.

`geelark ui` opens a menu; everything the CLI can do is reachable from it,
and a batch draws a live table instead of a wall of interleaved log lines.

This is a layer over the same functions the plain commands call. The
non-interactive CLI stays exactly as it was, because that is what cron, CI and
a piped `geelark build` depend on - a menu that prompts is useless to them.

Two things the menu is for beyond convenience:

- **Seeing the state before acting.** The dashboard puts the sheet, the phones
  and the plan's free slots on one screen. Nearly every confusing moment in
  this project came from acting without one of those three.
- **Watching a parallel run.** Four rows logging at once is unreadable in
  sequence; as a table with one line each it is obvious.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from . import builder, codes, failures, phones
from .api import ApiError, TransportError, build_client
from .builder import Build
from .config import Settings
from .gsheet import GSpreadError, SheetError
from .ledger import Ledger
from .pools import Book

console = Console()

OK = "green"
WARN = "yellow"
BAD = "red"
DIM = "bright_black"


# --------------------------------------------------------------- dashboard
@dataclass
class Snapshot:
    """Everything worth knowing before deciding what to do next."""

    # The resource pools a build draws from. -1 means "not read": the tabs may
    # be missing or unreachable, and a zero there would read as "empty" rather
    # than "not asked".
    proxies_free: int = -1
    gmails_free: int = 0
    apps_free: int = 0
    pools_stuck: int = 0
    #: Rows the pools refuse to hand out - a duplicate of an earlier row, a
    #: 2FA secret that will not decode. They are why a count can be lower than
    #: the number of blank Status cells you can see in the tab, and without
    #: them on this screen that difference looks like the tool miscounting.
    pools_broken: int = 0
    # Phones with a Gmail and no app account: built, one step short, and
    # cheaper to finish than to replace.
    phones_unfinished: int = 0
    phones_total: int = 0
    phones_running: int = 0
    slots_total: int = 0
    slots_free: int = 0
    parallels: int = 0
    error: str = ""

    @property
    def has_pools(self) -> bool:
        return self.proxies_free >= 0


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
            book = Book.open(settings)
            snap.proxies_free = len(book.proxies.available)
            snap.gmails_free = len(book.gmails.available)
            snap.apps_free = len(book.apps.available)
            snap.pools_stuck = sum(len(p.stuck) for p in
                                   (book.proxies, book.gmails, book.apps))
            snap.pools_broken = sum(len(p.broken) for p in
                                    (book.proxies, book.gmails, book.apps))
            snap.phones_unfinished = len(book.phones.unfinished())
        except SheetError as exc:
            snap.error = snap.error or str(exc).splitlines()[0]
    return snap


def dashboard(snap: Snapshot) -> Panel:
    table = Table.grid(padding=(0, 3))
    table.add_column(style=DIM, justify="right")
    table.add_column()

    # The pools `build` draws from. Each count is coloured by its own value -
    # a build needs one of each and stops at whichever runs out, so the empty
    # one is the one to see, not all three dimmed to match it.
    if snap.has_pools:
        def tint(count: int, label: str) -> str:
            return f"[{OK if count else BAD}]{count} {label}[/]"
        pool_bits = "   ".join((tint(snap.proxies_free, "proxies"),
                                tint(snap.gmails_free, "gmails"),
                                tint(snap.apps_free, "gpt")))
        if snap.pools_stuck:
            pool_bits += f"   [{WARN}]{snap.pools_stuck} stuck in_use[/]"
        if snap.pools_broken:
            pool_bits += (f"   [{BAD}]{snap.pools_broken} unusable[/]")
        table.add_row("pools", pool_bits)
        if snap.pools_broken:
            table.add_row("", f"[{DIM}]those rows are in the tab and cannot be "
                              f"handed out - 'Needs attention' says why[/]")

    # Said, not shouted. Which phones are up is worth knowing; what it costs
    # is the operator's business and they have said so, so this stopped being
    # red and stopped saying the word.
    running = (f"[{WARN}]{snap.phones_running} running[/]"
               if snap.phones_running else f"[{DIM}]none running[/]")
    waiting = (f"   [{WARN}]{snap.phones_unfinished} waiting on an app account[/]"
               if snap.phones_unfinished else "")
    table.add_row("phones", f"{snap.phones_total} total   {running}{waiting}")

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

# builder announces "signing in as <email>" / "signing into the app as <email>"
# as it works through the pools. A build has no account column to start with -
# it does not know which Gmail it will use until it tries one - so this is what
# fills it, and updates it when a bad candidate is dropped for the next. The
# run flow already has the address from the row, so only builds ever match.
SIGNING_IN = re.compile(r"signing (?:in|into the app) as (\S+@\S+)")

# Announcements rather than steps. Each is worth having in the log and none of
# them answers the question this column exists for - and two of them are long
# enough to have wrapped the cell, which changes the table's height and is what
# left copies of it on screen (2026-08-09).
NOT_A_STEP = (
    "netType came back",
    "billing:",
    "is stopped - starting it",
    "exits from",
    "stopped ",            # builder's "stopped <id>" - an outcome, not a step
)


@dataclass
class _LiveTable:
    """The half of a live display that is the same for every batch.

    Both the row batch and the build batch feed on the log records the flows
    already emit - each stamped with its worker's key (a row number, a build
    index) - so a handler can turn "screen: password_entry" into that line's
    current step without any flow knowing a console exists. What differs
    between the two is only how a line starts, finishes and renders; the
    plumbing that collects steps, links and warnings is here.
    """

    total: int = 0
    rows: dict[int, dict] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Links seen but not yet printed. Collected here rather than printed from
    # the logging handler, because that runs on the worker threads and every
    # write to the console has to come from the one thread driving Live.
    new_links: list[tuple[int, str, str]] = field(default_factory=list)
    seen_links: set[str] = field(default_factory=set)
    # Warnings and errors, waiting to be printed above the table rather than
    # into the middle of a row.
    new_notices: list[tuple[object, str]] = field(default_factory=list)

    def drain_links(self) -> list[tuple[int, str, str]]:
        """Take the links that have arrived since the last call."""
        with self.lock:
            found, self.new_links = self.new_links, []
            return found

    def notice(self, row, message: str) -> None:
        """Queue something that must be read, to be printed above the table."""
        with self.lock:
            self.new_notices.append((row, message))

    def drain_notices(self) -> list[tuple[object, str]]:
        with self.lock:
            found, self.new_notices = self.new_notices, []
            return found

    def note(self, key: int, message: str) -> None:
        with self.lock:
            entry = self.rows.get(key)
            if not entry:
                return
            if message.startswith(LIVE_PREFIX):
                # Held in its own field rather than shown as a step. As a step
                # the next log line replaced it within a second, which made the
                # one message worth clicking the one you could not click.
                url = message[len(LIVE_PREFIX):].strip()
                if url and url not in self.seen_links:
                    self.seen_links.add(url)
                    self.new_links.append((key, entry.get("phone", ""), url))
                return
            found = CREATED_SERIAL.search(message)
            if found:
                # The serial is what this line is for; the rest of it is the
                # device's model and timezone, which is not a step.
                entry["phone"] = found.group(1)
                return
            found = SIGNING_IN.search(message)
            if found:
                # Who the build is trying now - the account column, not a step.
                entry["email"] = found.group(1)
                return
            if any(n in message for n in NOT_A_STEP):
                return
            if entry["state"] == "working":
                entry["step"] = message

    def _render(self, first_heading: str) -> Table:
        """One line per worker, coloured by state.

        State is carried by colour and a plain word, never by a glyph: the
        check marks this started with rendered as replacement characters in a
        Windows console, which is the terminal this actually runs in.
        """
        table = Table(box=None, padding=(0, 2))
        # No fixed widths: rich sizes to the content, and a width small enough
        # to look tidy in one terminal truncates the word "working" in another.
        table.add_column(first_heading, justify="right", style=DIM)
        table.add_column("account", overflow="ellipsis", no_wrap=True,
                         max_width=34)
        table.add_column("phone", style=DIM)
        # Never wrapped. A cell that wraps makes the row two lines tall, so the
        # table's height changes from one frame to the next for reasons that
        # have nothing to do with how many rows there are - and Live erases its
        # last frame by the height it recorded. A proxy URL wrapping in this
        # column is what left copies of the table on screen (2026-08-09).
        table.add_column("state", overflow="ellipsis", no_wrap=True)
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


@dataclass
class BuildReporter(_LiveTable):
    """A `geelark build` batch, one line per phone being built.

    A build has no account until it signs one in, so the account column fills
    in only when the build finishes - until then the line is carried by its
    serial and the step the flow last reached, which is exactly what the log
    stamping provides.
    """

    def start(self, index: int, total: int, *,
              serial: str = "", gmail: str = "") -> None:
        with self.lock:
            self.rows[index] = {
                "email": gmail, "state": "working", "step": "starting",
                "started": time.monotonic(), "seconds": 0.0, "phone": serial,
            }

    def finish(self, build: Build) -> None:
        with self.lock:
            entry = self.rows.setdefault(build.index, {"email": ""})
            account = build.gmail
            if build.app_account:
                account = f"{account} + {build.app_account}"
            entry.update(state="ready" if build.ok else "failed",
                         step=build.status, seconds=build.seconds,
                         phone=build.serial or build.phone_id[:8],
                         email=account or entry.get("email", ""))

    def render(self) -> Table:
        return self._render("build")


# Which layers narrate. A row's state is the step it has reached, and steps
# happen in the flows and in the phone's lifecycle. screen and shell are the
# mechanics underneath: "tapping 'NEXT' at (615, 843) (clickable=True)" is
# perfectly true and says nothing about where the row has got to - it was what
# the column showed most of the time, because tapping is most of what happens.
#
# An allowlist rather than a denylist of the two noisy modules: a layer added
# below this one should be silent here by default, not until someone notices it
# in the column. phones is on it for its own sake and for two messages the
# console depends on - the serial it creates and the live-view link. builder is
# on it so a build's own coordinating lines - which Gmail it is trying, which
# app account - reach the table; without it the account column of a build stays
# blank until the moment it finishes.
NARRATING = ("geelark_farm.flows.", "geelark_farm.phones", "geelark_farm.builder")


class ReporterLogHandler(logging.Handler):
    """Feeds a batch's output into the live display.

    Two things, in two places. Progress from the narrating layers becomes each
    row's state, in the table. Anything at warning level or above is queued to
    be printed ABOVE the table instead - it is not a step, it is news, and it
    has to stay on screen.

    Warnings used to go straight to the stream handler while the table was
    drawing, which wrote them into the middle of a row and cut them off:

        9  Omega...  phone running; settling for 30s  109s  WARNING [row 9] the cha

    They are worth reading - "the chat screen is up but this run has not signed
    in", "sheet write failed; retrying" - so the answer is to place them rather
    than to silence them (2026-08-09).
    """

    def __init__(self, reporter: _LiveTable):
        super().__init__(level=logging.INFO)
        self.reporter = reporter
        self.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        row = getattr(record, "row", "-")
        if record.levelno >= logging.WARNING:
            self.reporter.notice(row, self.format(record))
            return
        if isinstance(row, int) and record.name.startswith(NARRATING):
            self.reporter.note(row, record.getMessage())


def print_new_notices(live: Live, reporter: _LiveTable) -> None:
    """Put warnings and errors above the table, where they stay readable."""
    for row, message in reporter.drain_notices():
        where = f"#{row}" if isinstance(row, int) else "run"
        live.console.print(f"[{WARN}]{where}[/]  {message}")


def print_new_links(live: Live, reporter: _LiveTable) -> None:
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
    new = reporter.drain_links()
    if not new:
        return

    # Printed straight through the live console, which puts it above the table
    # and redraws - no stopping and starting.
    #
    # This did stop and start for a while, to re-anchor a display whose frames
    # were leaking. That was treating a symptom: the leaks came from the state
    # column wrapping, which made the table's height change between frames, and
    # they went when that column stopped wrapping. What the stopping added was
    # a leftover copy of the table per link printed - eight links, eight copies
    # (2026-08-09). Live keeps its last frame on stop, which is exactly what
    # makes the final table stay on screen, and exactly what made this wrong.
    for number, serial, url in new:
        where = f"#{number}" + (f", phone {serial}" if serial else "")
        live.console.print(f"[{DIM}]{where} - watch live:[/]")
        live.console.print(url, soft_wrap=True)
    live.console.print()


def ask_for_codes(live: Live, pending) -> None:
    """Answer any build stopped on the "check your inbox" page.

    Only reached by accounts with no authenticator on them, where OpenAI
    emails a one-time code instead of asking for one. There is no mailbox to
    read here and deliberately so: whoever is running this is with the person
    signing up, or has the inbox open, and typing six digits is cheaper than
    every other way of getting them.

    Live is stopped for the prompt and started again after, the same as a
    resize does. Reading a line while it is drawing puts the typing underneath
    the table, four times a second.

    The wait belongs to the build, not to this prompt: each request carries
    its own deadline, so walking away simply lets it lapse and the build
    reports that nobody answered.
    """
    for request in pending.waiting():
        live.stop()
        try:
            console.print(
                f"[{WARN}]OpenAI emailed a code to {request.address}[/] "
                f"[{DIM}]- it has no authenticator, so the sign-in cannot go "
                f"on without it[/]")
            console.print(f"[{DIM}]  about {request.seconds_left:.0f}s left; "
                          f"press Enter alone to give up on this one[/]")
            while request.seconds_left > 0:
                typed = Prompt.ask("  code", default="", show_default=False)
                if not typed.strip():
                    pending.give_up(request)
                    console.print(f"[{DIM}]  left unanswered - the account is "
                                  f"set aside, not marked[/]")
                    break
                if pending.answer(request, typed):
                    console.print(f"[{OK}]  thanks - carrying on[/]")
                    break
                console.print(f"[{BAD}]  that is not a six-digit code[/]")
            else:
                pending.give_up(request)
        except (KeyboardInterrupt, EOFError):
            pending.give_up(request)
            console.print(f"\n[{DIM}]  left unanswered[/]")
        finally:
            live.start()


def _restart_after_resize(live: Live, width: int) -> int:
    """Re-anchor the live display when the terminal changes width.

    Live erases its previous frame by moving the cursor up over the number of
    lines it believes it drew. That count was worked out at the old width, so
    after a resize it is wrong, the erase misses, and every refresh lands below
    the last one instead of on top of it - four copies of the table a second,
    for the rest of the run. Resizing the window mid-batch did exactly that
    (2026-08-08).

    Stopping and starting forgets the stale measurement. It costs one leftover
    copy of the table per resize, which is a great deal better than one per
    refresh, and the alternative - drawing on the alternate screen - would take
    the live-view links out of the scrollback with it.
    """
    current = live.console.size.width
    if current == width:
        return width
    live.stop()
    live.start(refresh=True)
    return current


def _drive_live_table(reporter: _LiveTable, context_filter: logging.Filter,
                      work, cancel: threading.Event, pending=None) -> None:
    """Run `work()` in a thread while drawing `reporter.render()` live.

    The context filter goes on the reporter's own handler, not on the stream
    handlers `install_*_logging` touched: those are silenced for the duration
    (see below), so a filter on them never runs and the record reaches here
    with no key stamped - which is what would leave the step column stuck on
    "starting" for the whole batch. On this handler it runs every time, so each
    log line knows which worker it belongs to.

    Ctrl+C is delivered to this thread, not to the worker, so it has to be
    turned into a signal the worker reads. Without that the interrupt only
    ended the drawing: the batch carried on as an orphaned daemon thread, its
    log lines appeared over the menu, and it eventually crashed against a phone
    the console had stopped underneath it (2026-08-11). The first Ctrl+C now
    asks the run to stop - each phone still gets stopped and its resources
    released, which is the part that must not be skipped - and a second one
    abandons the wait for anyone who cannot afford it.
    """
    handler = ReporterLogHandler(reporter)
    handler.addFilter(context_filter)
    root = logging.getLogger()
    # Silence the stream handlers completely for the duration. Anything they
    # print goes straight to the terminal, underneath rich, and lands in the
    # middle of whatever the table was drawing. Warnings are not lost - the
    # handler queues them to be printed above the table instead.
    previous = [(h, h.level) for h in root.handlers]
    for existing, _ in previous:
        existing.setLevel(logging.CRITICAL + 1)
    root.addHandler(handler)

    failure: dict = {"exc": None}

    def runner() -> None:
        try:
            work()
        except Exception as exc:                                  # noqa: BLE001
            failure["exc"] = exc

    worker = threading.Thread(target=runner, daemon=True)
    worker.start()
    abandoned = False
    try:
        with Live(reporter.render(), console=console, refresh_per_second=4,
                  transient=False) as live:
            width = console.size.width
            while worker.is_alive():
                try:
                    width = _restart_after_resize(live, width)
                    print_new_notices(live, reporter)
                    print_new_links(live, reporter)
                    if pending is not None:
                        # Before the redraw: a build stopped on the code page
                        # is not going anywhere until someone answers it, and
                        # the table below would go on saying "entering the
                        # code" with nothing behind it.
                        ask_for_codes(live, pending)
                    live.update(reporter.render())
                    time.sleep(0.25)
                except KeyboardInterrupt:
                    if cancel.is_set():
                        # Asked twice. Stop waiting and say plainly what that
                        # leaves behind, rather than implying a clean stop.
                        abandoned = True
                        break
                    cancel.set()
                    live.console.print(
                        f"[{WARN}]stopping - each phone finishes its current "
                        f"step, then is stopped and its accounts released. "
                        f"This can take a few minutes. Ctrl+C again to stop "
                        f"waiting.[/]")
            print_new_notices(live, reporter)
            print_new_links(live, reporter)
            live.update(reporter.render())
    finally:
        root.removeHandler(handler)
        for existing, level in previous:
            existing.setLevel(level)

    if abandoned:
        console.print(f"[{BAD}]stopped waiting while the run was still "
                      f"working. Phones it had may still be running, and its "
                      f"rows may still say in_use - check 'Phones' and "
                      f"'Resource pools'.[/]")
    else:
        worker.join()
    if failure["exc"]:
        console.print(f"[{BAD}]the run failed: {failure['exc']}[/]")


def build_with_live_table(settings: Settings, **kwargs) -> list[Build]:
    """`build`, drawn as a table instead of a scrolling log."""
    client = build_client(settings)
    settings.ensure_dirs()
    builder.install_build_logging()

    reporter = BuildReporter()
    builds: list[Build] = []
    cancel = threading.Event()

    # An app account with no authenticator is emailed a code instead of
    # being asked for one, and the only person who can supply it is whoever
    # is sitting here. The build waits; the loop below notices and asks.
    pending = codes.Pending()

    def work() -> None:
        nonlocal builds
        builds = builder.run(client, settings, reporter=reporter,
                             cancel=cancel, codes_source=pending, **kwargs)

    _drive_live_table(reporter, builder.BuildContextFilter(), work, cancel,
                      pending)
    return builds


def finish_with_live_table(settings: Settings, **kwargs) -> list[Build]:
    """`finish`, drawn as a table - the same display as a build, because from
    the operator's side it is the same thing happening to fewer steps."""
    client = build_client(settings)
    settings.ensure_dirs()
    builder.install_build_logging()

    reporter = BuildReporter()
    builds: list[Build] = []
    cancel = threading.Event()

    pending = codes.Pending()

    def work() -> None:
        nonlocal builds
        builds = builder.finish_run(client, settings, reporter=reporter,
                                    cancel=cancel, codes_source=pending,
                                    **kwargs)

    _drive_live_table(reporter, builder.BuildContextFilter(), work, cancel,
                      pending)
    return builds


def build_summary_panel(builds: list[Build]) -> Panel:
    """The same shape as summary_panel, for the build flow's own result.

    A build's failure names a resource state, not a row - "no_usable_gpt", "the
    account was never judged" - and a ready one names what it produced, so the
    lines read differently even though the shell is the same.
    """
    if not builds:
        return Panel("nothing was built", border_style=DIM)

    ready = sum(1 for b in builds if b.ok)
    unstopped = [b for b in builds if b.still_running]

    body: list = [Text(f"{ready}/{len(builds)} phones ready",
                       style=f"bold {OK if ready == len(builds) else WARN}")]

    if unstopped:
        body.append(Text(""))
        body.append(Text(f"{len(unstopped)} phone(s) could not be stopped and "
                         f"are still up", style=f"bold {WARN}"))
        for b in unstopped:
            body.append(Text(f"   {b.phone_id}", style=BAD))
        body.append(Text("Stop them from the menu now.", style=BAD))
    else:
        # Not "nothing is billing": `stop` posts the request and returns, and
        # GeeLark goes on listing the phone as running while it shuts down. So
        # this panel said nothing was billing and the dashboard under it said
        # two were RUNNING, in the same breath (2026-08-13).
        body.append(Text("Every phone was told to stop. GeeLark can go on "
                         "showing one as running for a minute after.",
                         style=DIM))

    body.append(Text(""))
    for b in builds:
        if b.ok:
            who = b.gmail + (f" + {b.app_account}" if b.app_account else "")
            body.append(Text(f"   {b.name}  ", style=OK) + Text(who, style=OK))
        else:
            # The same sentence the Phones tab gets. This printed `b.status` -
            # so after twenty minutes the console said `no_usable_gpt` and left
            # you to open the sheet to find out which accounts it had tried.
            body.append(Text(f"   {b.name}  ", style=BAD)
                        + Text(builder.outcome_of(b), style=BAD))
        for attempt in builder.attempts_of(b):
            body.append(Padding(Text(f"tried {attempt}", style=DIM),
                                (0, 0, 0, 6)))

    return Panel(Group(*body), title="build summary", border_style=DIM,
                 padding=(1, 2))


# ------------------------------------------------------------------ views
def attention_view(settings: Settings) -> Panel:
    """Everything waiting on a decision, and what the decision is.

    The view this console did not have. Every question asked of this project
    in a fortnight - which accounts were wrongly condemned, why that phone
    stopped, what is safe to retry - was answered by opening the workbook and
    reading Status columns by eye, then looking up what each reason meant.

    All of it was already in the code: `failures.py` has written the advice
    since the taxonomy landed, and nothing ever showed it to anyone.
    """
    book = Book.open(settings)
    blocks: list = []

    for pool in (book.gmails, book.apps, book.proxies):
        flagged = pool.flagged
        if not flagged:
            continue
        blocks.append(Text(f"{pool.tab} - {len(flagged)} set aside",
                           style=f"bold {WARN}"))
        for resource in flagged:
            reason = pool.status_of(resource)
            blocks.append(Text(f"   {resource.label}  ", style="") +
                          Text(reason, style=BAD))
            # The taxonomy's words when the status is a reason a flow gave.
            # When it is a pool's own word - `challenged`, `dead` - there is no
            # verdict to look up and asking for one returns the fallback, which
            # tells the reader to go and edit failures.py. The note the run left
            # already says what happened.
            # `knows` rather than `reason in VERDICTS`: the table is no longer
            # the only source of a verdict - `stuck_on_*` is answered by a rule
            # - so membership was the wrong question, and a row set aside on
            # one of those showed its note where it could have shown the page
            # it got stuck on.
            advice = (failures.verdict(reason).advice if failures.knows(reason)
                      else (resource.values.get(pool.note_column) or "").strip())
            # Padded rather than indented with spaces, so the second line of a
            # long one lands under the first instead of back at the margin.
            blocks.append(Padding(Text(advice, style=DIM), (0, 0, 0, 6)))
        blocks.append(Text(""))

    waiting = book.phones.unfinished()
    if waiting:
        blocks.append(Text(f"phones one step short - {len(waiting)}",
                           style=f"bold {WARN}"))
        for phone in waiting:
            blocks.append(Text(f"   phone {phone['serial']}  {phone['gmail']}"))
            blocks.append(Padding(Text(phone["status"], style=DIM), (0, 0, 0, 6)))
        blocks.append(Text("Finish them from the menu - no new phone, Gmail or "
                           "proxy is spent.", style=DIM))
        blocks.append(Text(""))

    stuck = [(pool, r) for pool in (book.proxies, book.gmails, book.apps)
             for r in pool.stuck]
    if stuck:
        blocks.append(Text(f"claimed by a run that is gone - {len(stuck)}",
                           style=f"bold {WARN}"))
        for pool, resource in stuck:
            blocks.append(Text(f"   {pool.tab}: {resource.label}", style=DIM))
        blocks.append(Text("Free these from 'What I have to work with'.",
                           style=DIM))
        blocks.append(Text(""))

    broken = [(pool, r) for pool in (book.proxies, book.gmails, book.apps)
              for r in pool.broken]
    if broken:
        blocks.append(Text(f"rows this tool cannot use - {len(broken)}",
                           style=f"bold {BAD}"))
        for pool, resource in broken:
            blocks.append(Padding(Text(f"{pool.tab} row {resource.sheet_row}: "
                                       f"{resource.error}", style=BAD),
                                  (0, 0, 0, 3)))
        blocks.append(Text(""))

    if not blocks:
        return Panel(Text("Nothing is waiting on you.", style=OK),
                     title="needs attention", border_style=DIM, padding=(1, 2))
    return Panel(Group(*blocks[:-1]), title="needs attention",
                 border_style=DIM, padding=(1, 2))


def pools_view(settings: Settings) -> Panel:
    """What a build has to draw on: the sheet's stock and the plan's slots.

    One view, because they are one question. They were two menu entries, and
    the answer to "why did that phone never get created" is in whichever of
    them you did not open.
    """
    book = Book.open(settings)
    table = Table.grid(padding=(0, 3))
    table.add_column(style=DIM, justify="right")
    table.add_column()
    for pool in (book.proxies, book.gmails, book.apps):
        bits = f"[{OK if pool.available else BAD}]{len(pool.available)} available[/]"
        if pool.flagged:
            bits += f"   [{WARN}]{len(pool.flagged)} set aside[/]"
        if pool.stuck:
            bits += f"   [{WARN}]{len(pool.stuck)} stuck in_use[/]"
        if pool.broken:
            bits += f"   [{BAD}]{len(pool.broken)} unusable[/]"
        table.add_row(pool.tab, bits)
        for resource in pool.broken:
            table.add_row("", f"[{BAD}]row {resource.sheet_row}: "
                              f"{resource.error}[/]")

    try:
        client = build_client(settings)
        info = cached_plan(client)
        used = len(phones.listing(client))
        total = info.get("profiles") or 0
        free = info.get("availableProfiles") or 0
        table.add_row("", "")
        table.add_row("plan slots", f"{total} total, "
                                    f"[{OK if free else BAD}]{free} free[/]"
                                    f"   [{DIM}]{used} are cloud phones[/]")
        other = total - free - used
        if other > 0:
            # The answer to a create failing while the tab looks full.
            table.add_row("", f"[{WARN}]{other} held by something else - "
                              f"browser profiles share this pool[/]")
        parallel = info.get("parallels")
        table.add_row("parallel", str(parallel) if parallel else
                      f"[{WARN}]none - concurrent phones may cost extra[/]")
        table.add_row("renews", time.strftime(
            "%d %b %Y", time.localtime(info.get("expirationTime", 0))))
    except (ApiError, TransportError) as exc:
        table.add_row("plan", f"[{BAD}]{str(exc).splitlines()[0]}[/]")

    return Panel(table, title="what I have to work with", border_style=DIM,
                 padding=(1, 2))


def marks_preview(book: Book) -> tuple[list[dict], list]:
    """What `Apply what I marked` would do, before it does any of it.

    Deleting a phone is the one irreversible thing this console can be asked
    to do, and it was reachable only as a side effect of starting a build -
    where it happened before the first line of output.
    """
    marked = book.phones.marked()
    lines: list = []
    for row in marked:
        done = row["state"] == book.phones.DONE
        lines.append(Text(f"   phone {row['serial']}  ",
                          style="") + Text(row["state"],
                                           style=OK if done else WARN))
        lines.append(Text("      delete the phone and drop its row", style=DIM))
        if row["gmail"]:
            lines.append(Text(f"      {row['gmail']} - retired, never used "
                              f"again", style=DIM))
        if row["app_account"]:
            what = ("delivered with the phone" if done else
                    "freed, to try on another phone")
            lines.append(Text(f"      {row['app_account']} - {what}", style=DIM))
    return marked, lines



def setup_view(settings: Settings) -> Table:
    """The `geelark verify` checklist, in the console's colours.

    Here because the console is what someone opens on a machine they have just
    set the tool up on, and the checks are most wanted exactly then - not
    after a build has failed for a reason that turns out to be a tab shared
    read-only.
    """
    from . import verify

    styles = {verify.OK: (OK, "ok"), verify.FATAL: (BAD, "FAIL"),
              verify.WARN: (WARN, "warn"), verify.INFO: (DIM, "--"),
              verify.SKIP: (DIM, "--")}

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="right")
    table.add_column(style="bold")
    table.add_column()
    for check in verify.run_checks(settings):
        style, mark = styles.get(check.state, (DIM, check.state))
        head, *rest = check.detail.split("\n")
        table.add_row(f"[{style}]{mark}[/]", check.name, head)
        for line in rest:
            table.add_row("", "", f"[{DIM}]{line.strip()}[/]")
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


# ------------------------------------------------------------------- menu
# Split by what a choice costs, because that is the distinction that matters
# when you are about to press one: the first group changes something in the
# world - phones, billing, the sheet - and the second only reads.
#
# The flat list of seven mixed them, and two of the seven were the same idea
# twice: `Stop everything` and `Reap` both exist to end billing, one of them
# selectively. They are one choice with a question now. `Subscription` is gone
# as a destination - its two useful numbers are already on the dashboard, and
# the rest of them belong beside the pools they compete with.
DOING = [
    ("1", "Build phones",
     "take a proxy, sign in a Gmail, install, sign in ChatGPT - the main job"),
    ("2", "Finish waiting phones",
     "phones one step short - no new phone, Gmail or proxy is spent"),
    ("3", "Update the sheet",
     "make all four tabs agree with the panel, and carry out the State column"),
    ("4", "Stop running phones", "whatever is up, or only the unaccounted"),
]
LOOKING = [
    ("5", "Needs attention",
     "what a run set aside, and what to do about each - start here"),
    ("6", "What I have to work with", "the pool tabs, plan slots, anything stuck"),
    ("7", "Phones", "what exists, and what is running"),
    ("8", "Check the setup",
     "credentials, the sheet, the tabs and columns - what is missing"),
]
ACTIONS = DOING + LOOKING + [("q", "Quit", "")]


def menu() -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="right")
    table.add_column()
    table.add_column(style=DIM)
    for heading, group in (("do", DOING), ("look", LOOKING)):
        table.add_row("", f"[{DIM}]{heading}[/]", "")
        for key, label, hint in group:
            table.add_row(key, label, hint)
        table.add_row("", "", "")
    table.add_row("q", "Quit", "")
    return table


def confirm_build(settings: Settings, snap: Snapshot) -> dict | None:
    """Ask how many phones to end up with, defaulting to what the stock allows.

    What that number is, and which pool decides it, is `builder.Capacity` - the
    arithmetic is the domain's, not the console's. This shows it and takes the
    answer.
    """
    if not snap.has_pools:
        console.print(f"[{BAD}]the resource tabs (Gmails, Proxy, Gpt Info) are "
                      f"not in this sheet - `build` has nothing to read[/]")
        return None

    can = builder.Capacity(waiting=snap.phones_unfinished,
                           proxies=snap.proxies_free, gmails=snap.gmails_free,
                           app_accounts=snap.apps_free)

    console.print(f"[{DIM}]pools: {can.proxies} proxies, {can.gmails} gmails, "
                  f"{can.app_accounts} gpt[/]")
    if can.waiting:
        # Said before the number is asked for, because it changes what the
        # number costs: these need an app account and nothing else.
        console.print(f"[{OK}]{can.waiting} phone(s) already have a Gmail and "
                      f"the app - those are finished first, and cost only an "
                      f"app account each[/]")
    if can.total:
        if can.limited_by == "app accounts":
            why = (f"{can.app_accounts} gpt account(s) is the limit - one per "
                   f"ready phone - so this uses them all")
        else:
            why = (f"{can.limited_by} run out first, leaving "
                   f"{can.app_accounts - can.total} gpt account(s) spare")
        console.print(f"[{DIM}]{can.total} phone(s) ({can.finishing} finished, "
                      f"{can.building} new): {why}[/]")
    elif not can.app_accounts:
        console.print(f"[{WARN}]the Gpt Info tab is empty; every phone would "
                      f"stop at no_usable_gpt[/]")
    else:
        console.print(f"[{WARN}]no proxies or Gmails free and no phone waiting; "
                      f"a build has nothing to work with[/]")
    ceiling = can.total

    count = IntPrompt.ask("how many phones to end up with",
                          default=max(1, ceiling))
    count = max(1, count)

    workers = IntPrompt.ask("how many at a time",
                            default=min(settings.max_concurrent_phones, count))
    workers = max(1, min(workers, count))
    if snap.parallels and workers > snap.parallels:
        console.print(f"[{WARN}]the plan's parallel limit is {snap.parallels}; "
                      f"more may cost extra[/]")
    # A phone per proxy is the arithmetic, and it is not the whole story: an
    # exit refusing a login is ordinary - it happened in each of the last three
    # runs - and answering it costs a second proxy. Take every proxy for a phone
    # and the first refusal has nowhere to go. Phone 762 got that far and
    # stopped, having done everything right (2026-08-16).
    creating = max(0, count - snap.phones_unfinished)
    if snap.has_pools and creating >= snap.proxies_free > 0:
        console.print(f"[{WARN}]that takes every free proxy, so a phone whose "
                      f"exit gets refused will have none to move to. "
                      f"{creating - 1} would leave one spare.[/]")
    if creating > snap.slots_free:
        console.print(f"[{WARN}]only {snap.slots_free} plan slot(s) are free "
                      f"and {creating} phone(s) would be created; the rest "
                      f"will fail to create a phone[/]")

    finishing = min(count, snap.phones_unfinished)
    split = (f" ({finishing} finished, {creating} built new)"
             if finishing else "")
    console.print(f"\n[{WARN}]{count} phone(s){split}, {workers} at a time. "
                  f"[/]")
    if not Confirm.ask("start", default=True):
        return None
    return {"count": count, "workers": workers}


def confirm_finish(settings: Settings, snap: Snapshot) -> dict | None:
    """Ask how many one-step-short phones to complete.

    The app pool is the real ceiling here: finishing needs one account per
    phone and nothing else, so a pool smaller than the queue means some of them
    will stop where they stopped last time.
    """
    if not snap.phones_unfinished:
        console.print(f"[{DIM}]no phone is waiting on an app account[/]")
        return None
    if not snap.apps_free:
        console.print(f"[{WARN}]{snap.phones_unfinished} phone(s) are waiting, "
                      f"but the Gpt Info tab is empty - top it up first, or "
                      f"they will stop exactly where they stopped before[/]")
        if not Confirm.ask("go anyway", default=False):
            return None

    count = IntPrompt.ask(
        f"how many to finish (of {snap.phones_unfinished}, "
        f"{snap.apps_free} account(s) free)",
        default=min(snap.phones_unfinished, snap.apps_free) or 1)
    count = max(1, min(count, snap.phones_unfinished))

    workers = IntPrompt.ask("how many at a time",
                            default=min(settings.max_concurrent_phones, count))
    workers = max(1, min(workers, count))

    console.print(f"\n[{WARN}]{count} phone(s), {workers} at a time. No new "
                  f"phone, Gmail or proxy is spent.[/]")
    if not Confirm.ask("start", default=True):
        return None
    return {"limit": count, "workers": workers}


@contextlib.contextmanager
def quiet_console():
    """Hold back INFO logging from the terminal for the duration.

    The startup sync narrates itself at INFO - every phone deleted, every
    proxy's measured exit address, every correction - and those records went
    to the terminal while a spinner was drawing on it. The result was forty
    lines of interleaved detail above a summary that says the same things in
    the words the operator uses, and the spinner redrawing between each one.

    Warnings and errors still come through: those are news, and the summary
    does not carry them. Nothing is lost either way - the file handler keeps
    every record at DEBUG whatever this does.
    """
    muted = [handler for handler in logging.getLogger().handlers
             if isinstance(handler, logging.StreamHandler)
             and not isinstance(handler, logging.FileHandler)
             and handler.level < logging.WARNING]
    levels = [handler.level for handler in muted]
    for handler in muted:
        handler.setLevel(logging.WARNING)
    try:
        yield
    finally:
        for handler, level in zip(muted, levels, strict=True):
            handler.setLevel(level)


def sync_on_startup(settings: Settings) -> None:
    """Bring the tabs up to date before the first screen is drawn.

    The sync ran inside a build, so opening the console, looking at it and
    closing it changed nothing - and the numbers on the dashboard were
    whatever the sheet last recorded rather than what is true. Running the
    program is meant to be what updates it, and this is the first thing the
    program does.

    It carries out the State column too, deletions included. That is what
    writing `done` in that column means, and the point of writing it there
    rather than pressing something is that the next run acts on it.

    Never fatal. A console that will not open because a proxy check timed out
    is worse than one showing yesterday's numbers, and the panel below says
    which it is.
    """
    if not settings.sheet_id:
        return
    try:
        with quiet_console(), console.status("opening the sheet...") as spinner:
            outcome = builder.sync_sheet(
                build_client(settings), Book.open(settings),
                Ledger.load(settings.state_dir),
                artifact_dir=settings.artifact_dir,
                on_step=lambda doing: spinner.update(f"{doing}..."))
    except (ApiError, TransportError, SheetError, GSpreadError) as exc:
        # GSpreadError included because a sheet quota (429) can surface from a
        # read - Book.open, a reload - that does not pass through batch_write's
        # 429 handling, and "never fatal" has to mean it (2026-08-17).
        console.print(f"[{WARN}]could not update the sheet first: {exc}[/]")
        return
    show_sync(outcome)
    if outcome:
        console.print()


def run_console(settings: Settings) -> int:
    """The loop. Draw the state, take one action, draw it again."""
    console.print()
    sync_on_startup(settings)
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
            # ended the process from inside the loop with a traceback
            # (2026-08-08). Quitting is a menu choice, so make it one.
            console.print()
            choice = "q"
        console.print()

        try:
            if choice == "q":
                # No parting question about what is still running. Whether a
                # phone should be left up is the operator's call and they make
                # it deliberately, from the menu - being asked on the way out
                # of every session is nagging, not safety.
                return 0

            if choice == "1":
                options = confirm_build(settings, snap)
                if options:
                    builds = build_with_live_table(settings, **options)
                    console.print(build_summary_panel(builds))

            elif choice == "2":
                options = confirm_finish(settings, snap)
                if options:
                    builds = finish_with_live_table(settings, **options)
                    console.print(build_summary_panel(builds))

            elif choice == "3":
                apply_marks(settings)

            elif choice == "4":
                stop_phones(settings)

            elif choice == "5":
                console.print(attention_view(settings))

            elif choice == "6":
                console.print(pools_view(settings))
                if snap.pools_stuck and Confirm.ask(
                        f"release {snap.pools_stuck} row(s) stuck as in_use - "
                        f"only if no other run is in progress", default=False):
                    freed = Book.open(settings).release_stuck()
                    console.print(f"[{OK}]released {freed}[/]")

            elif choice == "7":
                console.print(phones_table(settings))

            elif choice == "8":
                console.print(setup_view(settings))

        except (ApiError, TransportError, SheetError) as exc:
            console.print(f"[{BAD}]{exc}[/]")
        except KeyboardInterrupt:
            console.print(f"\n[{DIM}]cancelled[/]")

        console.print()


def stop_all(settings: Settings) -> None:
    """Stop every running phone."""
    client = build_client(settings)
    ledger = Ledger.load(settings.state_dir)
    targets = [p["id"] for p in phones.listing(client)
               if p.get("status") in (phones.RUNNING, phones.STARTING)]
    if not targets:
        console.print(f"[{DIM}]nothing is running[/]")
        return
    for phone_id in targets:
        phones.stop(client, phone_id)
        ledger.release(phone_id, note="Stopped from the console.")
        console.print(f"[{OK}]stopped {phone_id}[/]")


def stop_phones(settings: Settings) -> None:
    """One choice where there were two.

    `Stop everything` and `Reap` were separate menu entries for one intention,
    and which one you wanted depended on a distinction - whether a run is
    accountable for a phone - that the menu never showed you. So show it, then
    ask.
    """
    client = build_client(settings)
    ledger = Ledger.load(settings.state_dir)
    running = [p for p in phones.listing(client)
               if p.get("status") in (phones.RUNNING, phones.STARTING)]
    if not running:
        console.print(f"[{OK}]nothing is running[/]")
        return

    loose = {phone_id for phone_id, _ in phones.reapable(client, ledger)}
    for item in running:
        entry = ledger.get(item.get("id"))
        who = (f"[{DIM}]{entry.label}[/]" if entry and item["id"] not in loose
               else f"[{WARN}]nothing is accountable for it[/]")
        console.print(f"  phone {item.get('serialNo', '?')}  {who}")

    if not loose:
        console.print(f"\n[{DIM}]a run is accountable for all of them; it "
                      f"stops them itself when it ends[/]")
    choice = Prompt.ask(
        f"\nstop [a]ll {len(running)}"
        + (f", just the [u]naccounted {len(loose)}" if loose else "")
        + ", or [n]othing",
        choices=["a", "u", "n"] if loose else ["a", "n"], default="n")
    if choice == "a":
        stop_all(settings)
    elif choice == "u":
        phones.reap(client, ledger)
        console.print(f"[{OK}]stopped {len(loose)}[/]")


#: What each key of a sync report means, in the order a person would want to
#: read it: what was decided about the credentials, then what happened to the
#: phones, then the exits.
SYNC_LABELS = [
    ("retired", "Gmails retired - they had been on a phone that is now gone"),
    ("delivered", "app accounts delivered with a phone marked done"),
    ("freed", "app accounts freed - their phone was marked failed"),
    ("deleted", "phones deleted, from GeeLark and from the tab"),
    ("held", "marked, but a run is working on them - left exactly as they are"),
    ("running", "marked, but they would not stop, so they are still there"),
    ("abandoned", "phones a killed run left mid-build - finishable, Google is "
                  "already on them"),
    ("discarded", "phones a killed run left with nothing signed into them"),
    ("attached", "proxies now recorded against the phone actually on them"),
    ("released", "proxies freed - nothing is behind them"),
    ("repointed", "phones whose recorded exit was out of date"),
    ("pruned", "archived page dumps removed - their phone is gone, or the "
               "failure they recorded is a week old"),
    ("renamed", "phones given the name their serial and address say - the "
                "running ones are left for the next sync"),
    ("dead", "free proxies that no longer answer"),
    ("revived", "proxies marked dead that are answering again"),
    ("unlisted", "proxies GeeLark holds that the Proxy tab has never heard of "
                 "- add the ones you want a build to use"),
]


def show_sync(outcome: dict[str, list[str]]) -> None:
    if not outcome:
        console.print(f"[{OK}]every tab already agreed with the panel[/]")
        return
    for key, meaning in SYNC_LABELS:
        items = outcome.get(key)
        if not items:
            continue
        style = WARN if key in ("running", "dead", "unlisted") else OK
        console.print(f"[{style}]{len(items)} {meaning}[/]")
        for item in items:
            console.print(f"   [{DIM}]{item}[/]")


def apply_marks(settings: Settings) -> None:
    """Bring every tab back into agreement with the panel.

    Two things at once, because they are one job and doing half of it is what
    left a tab disagreeing with the other three. The half that deletes phones
    is the only irreversible thing this console does, and it used to happen
    only at the start of a build, before its first line of output - so it is
    previewed here and nothing else waits on the answer.
    """
    book = Book.open(settings)
    marked, lines = marks_preview(book)
    apply = False
    if marked:
        console.print(Panel(Group(*lines), title="what the State column asks for",
                            border_style=DIM, padding=(1, 2)))
        console.print(f"[{WARN}]Deleting a phone cannot be undone.[/]")
        apply = Confirm.ask(f"apply {len(marked)} mark(s) and sync",
                            default=False)
        if not apply and not Confirm.ask(
                "sync the other tabs anyway - nothing in that half deletes "
                "anything", default=True):
            return
    else:
        console.print(f"[{DIM}]no phone is marked done or failed; syncing the "
                      f"tabs against the panel[/]")

    show_sync(builder.sync_sheet(build_client(settings), book,
                                 Ledger.load(settings.state_dir),
                                 apply_marks=apply))
