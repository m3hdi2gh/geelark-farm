"""Command-line behaviour that scripts and habits depend on.

Exit codes and guards, not output formatting. Each of these failed silently:
nothing raised, the command simply did the wrong thing.
"""

from __future__ import annotations

import pathlib
import sys
import time
from types import SimpleNamespace

import pytest

from geelark_farm import cli
from geelark_farm import ledger as ledger_mod
from geelark_farm.accounts import AccountError, Credentials
from geelark_farm.api import TransportError
from geelark_farm.config import ConfigError
from geelark_farm.gsheet import SheetError
from geelark_farm.ledger import Ledger
from geelark_farm.proxy import ProxyError
from geelark_farm.shell import ShellError


class Args:
    """argparse.Namespace stand-in."""

    def __init__(self, **kw):
        defaults = dict(count=1, limit=None, dry_run=False, workers=None,
                        watch=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


def build(index: int, ok: bool):
    from geelark_farm.builder import Build
    return Build(index=index, ok=ok, status="ready" if ok else "error")


# ------------------------------------------------------------- exit codes
@pytest.mark.parametrize("builds,expected", [
    ([], 0),                                        # nothing to do
    ([build(1, True)], 0),
    ([build(1, True), build(2, True)], 0),
    ([build(1, True), build(2, False)], 1),
    ([build(1, False)], 1),
])
def test_build_exit_code(builds, expected, tmp_path, monkeypatch, capsys,
                         make_settings):
    """An empty result is success. Nothing waiting and nothing buildable is the
    normal state of a finished pool, and exiting non-zero for it makes the
    command unusable from cron or CI, where a no-op has to look like one."""
    from geelark_farm import builder as builder_mod

    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    monkeypatch.setattr(cli, "build_client", lambda s: object())
    monkeypatch.setattr(builder_mod, "run", lambda *a, **k: builds)

    assert cli.cmd_build(settings, Args()) == expected
    capsys.readouterr()


def test_a_dry_run_always_succeeds(tmp_path, monkeypatch, capsys, make_settings):
    """It changes nothing, so it cannot fail at anything."""
    from geelark_farm import builder as builder_mod

    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    monkeypatch.setattr(cli, "build_client", lambda s: object())
    monkeypatch.setattr(builder_mod, "run", lambda *a, **k: [])

    assert cli.cmd_build(settings, Args(dry_run=True)) == 0
    capsys.readouterr()


# ------------------------------------------------------------- busy guard
def claimed_ledger(tmp_path, *, label: str = "row 4 / someone@example.com"):
    ledger = Ledger.load(tmp_path)
    ledger.record("P1", label=label)
    ledger.claim("P1", label=label)
    return ledger


def test_a_phone_another_run_holds_is_refused(tmp_path, make_settings):
    """Two flows on one phone corrupt each other's screen reads, because
    `uiautomator dump` cannot run twice at once. The claim already existed;
    until this pass only `install` consulted it."""
    claimed_ledger(tmp_path)
    settings = make_settings(state_dir=tmp_path)

    with pytest.raises(SystemExit, match="in use by another run"):
        cli.refuse_if_busy(settings, "P1")


def test_a_released_phone_is_not_refused(tmp_path, make_settings):
    ledger = claimed_ledger(tmp_path)
    ledger.release("P1")

    cli.refuse_if_busy(make_settings(state_dir=tmp_path), "P1")   # no raise


def test_a_stale_claim_does_not_lock_a_phone_forever(tmp_path, make_settings):
    """A dead process must not hold a phone hostage."""
    ledger = claimed_ledger(tmp_path)
    ledger.get("P1").claimed_at = time.time() - ledger_mod.STALE_CLAIM_SECONDS - 1
    ledger.save()

    cli.refuse_if_busy(make_settings(state_dir=tmp_path), "P1")   # no raise


def test_an_unknown_phone_is_not_refused(tmp_path, make_settings):
    """Nothing recorded means nothing is holding it."""
    cli.refuse_if_busy(make_settings(state_dir=tmp_path), "NEVER-SEEN")


# ------------------------------------------------------- the live console
def test_the_live_view_link_is_taken_out_of_the_progress_messages():
    """The link arrived as just another log line, so the next one replaced it
    within a second - the one message worth clicking was the one that could not
    be clicked. It is diverted for printing in full instead, and the phone's
    serial is read from the creation line, since that is what mints a fresh
    link once this one expires.
    """
    from geelark_farm.ui import BuildReporter


    reporter = BuildReporter()
    reporter.start(1, 1)
    reporter.note(1, "created 631291280617374014 (serial 477): vivo / Android 15")
    reporter.note(1, "watch it live: https://phone.geelark.com/index.html?t=abc")
    reporter.note(1, "screen: password_entry (visit 1)")

    entry = reporter.rows[1]
    assert entry["phone"] == "477"
    # The step is the flow's, never the URL: a step is overwritten by the next
    # one, and that is exactly what must not happen to a link.
    assert entry["step"] == "screen: password_entry (visit 1)"


def test_each_live_link_is_offered_for_printing_exactly_once():
    """The table cell can only offer an OSC 8 hyperlink, and a terminal without
    OSC 8 shows it as the bare word "open" with no way to reach the link - as
    one did on 2026-08-06. So the URL is also printed in full, above the table,
    where it stays in the scrollback and the terminal's own URL detection can
    find it.

    Once, though. The render loop drains this four times a second, so a link
    that came back on every tick would bury the table under its own URL.
    """
    from geelark_farm.ui import BuildReporter


    reporter = BuildReporter()
    reporter.start(6, 1)
    reporter.note(6, "created 631291280617374014 (serial 495): vivo / Android 15")
    reporter.note(6, "watch it live: https://phone.geelark.com/i.html?t=abc")

    # The serial travels with the link: it is what mints a fresh one with
    # `geelark start` after this one has expired.
    assert reporter.drain_links() == [(6, "495", "https://phone.geelark.com/i.html?t=abc")]
    assert reporter.drain_links() == []

    # The same URL logged again is still not new.
    reporter.note(6, "watch it live: https://phone.geelark.com/i.html?t=abc")
    assert reporter.drain_links() == []

    # A freshly minted one is.
    reporter.note(6, "watch it live: https://phone.geelark.com/i.html?t=xyz")
    assert reporter.drain_links() == [(6, "495", "https://phone.geelark.com/i.html?t=xyz")]


def test_only_the_narrating_layers_reach_the_state_column():
    """The column showed "tapping 'NEXT' at (615, 843) (clickable=True)" most
    of the time, because tapping is most of what happens - true, and no answer
    to where the row has got to. screen and shell are the mechanics under the
    flows, so they no longer narrate.
    """
    import logging

    from geelark_farm.ui import BuildReporter, ReporterLogHandler


    reporter = BuildReporter()
    reporter.start(1, 1)
    handler = ReporterLogHandler(reporter)

    def emit(logger: str, message: str) -> None:
        record = logging.LogRecord(logger, logging.INFO, "f", 1, message,
                                   None, None)
        record.row = 1
        handler.emit(record)

    emit("geelark_farm.flows.google_login", "entering the password")
    emit("geelark_farm.screen", "tapping 'NEXT' at (615, 843) (clickable=True)")
    emit("geelark_farm.shell", "input text ****")
    assert reporter.rows[1]["step"] == "entering the password"

    # phones must keep narrating: the console reads the serial and the
    # live-view link out of its messages, so silencing it would take both.
    emit("geelark_farm.phones", "created 631 (serial 477): vivo / Android 15")
    assert reporter.rows[1]["phone"] == "477"
    emit("geelark_farm.phones", "watch it live: https://phone.geelark.com/i?t=1")
    assert reporter.drain_links() == [(1, "477", "https://phone.geelark.com/i?t=1")]


def test_printing_links_does_not_restart_the_display():
    """Live keeps its last frame when it stops - that is what makes the final
    table stay on screen. So stopping around each link print left a copy of the
    table behind every time: eight links, eight copies (2026-08-09).

    Printing through the live console is already handled by rich; the stopping
    was there to re-anchor frames that were leaking for a different reason, and
    that reason - a wrapping state column - is fixed at its source.
    """
    import io

    from rich.console import Console
    from rich.live import Live

    from geelark_farm.ui import BuildReporter, print_new_links


    console = Console(width=100, force_terminal=True, file=io.StringIO())
    reporter = BuildReporter()
    for n in (1, 2):
        reporter.start(n, 2)
        reporter.note(n, f"watch it live: https://phone.geelark.com/i?row={n}")

    calls: list[str] = []
    with Live(reporter.render(), console=console) as live:
        real_stop, real_start = live.stop, live.start
        live.stop = lambda: (calls.append("stop"), real_stop())[1]
        live.start = lambda refresh=False: (calls.append("start"),
                                            real_start(refresh))[1]
        print_new_links(live, reporter)
        during_the_print = list(calls)      # before the block's own stop

    assert during_the_print == [], "the display is never stopped to print"
    assert console.file.getvalue().count("phone.geelark.com") == 2


def test_a_resize_restarts_the_live_display_once():
    """Live erases its last frame by moving the cursor up over the number of
    lines it believes it drew - a count worked out at the old width. After a
    resize the erase misses and every refresh lands below the last one instead
    of on top of it: four copies of the table a second for the rest of the run,
    which is what resizing the window mid-batch produced (2026-08-08).
    """
    import io

    from rich.console import Console
    from rich.live import Live

    from geelark_farm.ui import _restart_after_resize

    console = Console(width=100, force_terminal=True, file=io.StringIO())
    calls: list[str] = []

    with Live("frame", console=console) as live:
        real_stop, real_start = live.stop, live.start
        live.stop = lambda: (calls.append("stop"), real_stop())[1]
        live.start = lambda refresh=False: (calls.append("start"),
                                            real_start(refresh))[1]

        assert _restart_after_resize(live, 100) == 100
        assert calls == [], "an unchanged width costs nothing"

        console.size = (140, 30)
        after = _restart_after_resize(live, 100)
        assert after != 100
        assert calls == ["stop", "start"], "once, not once per refresh"


def test_the_state_column_shows_steps_not_announcements():
    """The column wrapped onto a second line when it held a proxy URL, which
    changes the table's height between frames - and Live erases its last frame
    by the height it recorded, so copies of the table stayed on screen
    (2026-08-09).

    Height is now a function of the row count alone: the column never wraps,
    and the long announcements never reach it. None of them answered the
    question it exists for anyway.
    """
    from geelark_farm.ui import BuildReporter


    reporter = BuildReporter()
    reporter.start(1, 1)

    for noise in (
        "created 6318 (serial 542): vivo V2419A / Android 15, USA / New_York",
        "netType came back 0 (Wi-Fi) despite requesting mobile data",
        "phone is stopped - starting it (billing is per minute)",
        "billing: Per-minute usage",
        "socks5://ul01k:***@190.2.143.20:10406 exits from 156.241.217.238",
    ):
        reporter.note(1, noise)

    # The serial was taken from the first of those; none became the step.
    assert reporter.rows[1]["phone"] == "542"
    assert reporter.rows[1]["step"] == "starting"

    reporter.note(1, "phone starting (1)")
    assert reporter.rows[1]["step"] == "phone starting (1)"


def test_warnings_go_above_the_table_not_into_it():
    """While the table is drawing, anything the stream handler prints lands
    underneath rich and in the middle of whatever row was being drawn:

        9  Omega...  phone running; settling for 30s  109s  WARNING [row 9] the cha

    They are worth reading - "the chat screen is up but this run has not signed
    in", "sheet write failed; retrying" - so they are placed rather than
    silenced (2026-08-09).
    """
    import logging

    from geelark_farm.ui import BuildReporter, ReporterLogHandler


    reporter = BuildReporter()
    reporter.start(9, 1)
    handler = ReporterLogHandler(reporter)

    def emit(name: str, level: int, message: str) -> None:
        record = logging.LogRecord(name, level, "f", 1, message, None, None)
        record.row = 9
        handler.emit(record)

    emit("geelark_farm.phones", logging.INFO, "phone starting (1)")
    emit("geelark_farm.flows.chatgpt_login", logging.WARNING,
         "the chat screen is up but this run has not signed in")

    # The step is the step; the warning is not.
    assert reporter.rows[9]["step"] == "phone starting (1)"

    notices = reporter.drain_notices()
    assert len(notices) == 1
    row, text = notices[0]
    assert row == 9
    assert "WARNING" in text and "has not signed in" in text
    assert reporter.drain_notices() == [], "each one is printed once"


def test_a_run_level_warning_is_kept_too():
    """Not every warning belongs to a row - the ones from before any row starts
    have no number, and dropping them would lose the ones about the run
    itself."""
    import logging

    from geelark_farm.ui import BuildReporter, ReporterLogHandler

    reporter = BuildReporter()
    handler = ReporterLogHandler(reporter)

    record = logging.LogRecord("geelark_farm.api", logging.WARNING, "f", 1,
                               "waited 4s for rate limit", None, None)
    record.row = "-"
    handler.emit(record)

    assert len(reporter.drain_notices()) == 1


def test_the_build_default_never_promises_more_phones_than_gpt_accounts():
    """Every ready phone consumes one app account, whether it was built from
    nothing or finished from one that was waiting - so the app pool caps the
    whole run. Adding the two together offered three phones against two
    accounts, and the third was certain to end on no_usable_gpt having spent a
    phone, a Gmail and a proxy to get there (2026-08-11).
    """
    from types import SimpleNamespace
    from unittest.mock import patch

    from geelark_farm.ui import Snapshot, confirm_build

    settings = SimpleNamespace(max_concurrent_phones=1)
    asked = []

    def run(**pools):
        snap = Snapshot(slots_total=30, slots_free=21, **pools)
        with patch("geelark_farm.ui.IntPrompt.ask",
                   side_effect=lambda *a, **k: asked.append(k["default"])
                   or k["default"]), \
             patch("geelark_farm.ui.Confirm.ask", return_value=False):
            confirm_build(settings, snap)
        return asked.pop(0)

    # the reported case: one waiting phone, two accounts -> two phones, not three
    assert run(proxies_free=13, gmails_free=13, apps_free=2,
               phones_unfinished=1) == 2
    asked.clear()
    # accounts to spare: the proxies and Gmails bind instead
    assert run(proxies_free=2, gmails_free=9, apps_free=10,
               phones_unfinished=0) == 2
    asked.clear()
    # nothing to build with, but waiting phones need only an account each
    assert run(proxies_free=0, gmails_free=0, apps_free=4,
               phones_unfinished=3) == 3


def test_a_build_line_fills_its_account_only_when_it_finishes():
    """A build has no account until it signs one in, so the column is blank
    while it works and names the pair it ended with once it is done. The serial
    still arrives mid-build, from the creation line, the same way a row's does.
    """
    from geelark_farm.builder import Build
    from geelark_farm.ui import BuildReporter

    reporter = BuildReporter()
    reporter.start(1, 3)
    reporter.note(1, "created 631 (serial 500): vivo / Android 15")
    assert reporter.rows[1]["email"] == ""        # nothing signed in yet
    assert reporter.rows[1]["phone"] == "500"

    reporter.finish(Build(index=1, ok=True, status="ready", serial="500",
                          gmail="g@example.com", app_account="a@example.com"))
    assert reporter.rows[1]["email"] == "g@example.com + a@example.com"


def test_a_builds_steps_reach_its_state_column():
    """The step column only updates if the record carries the build's index,
    and that is stamped by BuildContextFilter - which has to be on the handler
    that actually runs (the reporter's), not the silenced stream handlers. With
    it on the reporter handler, a flow log with no row set gets one and lands.
    """
    import logging

    from geelark_farm import builder, logs
    from geelark_farm.ui import BuildReporter, ReporterLogHandler

    reporter = BuildReporter()
    reporter.start(7, 1)
    handler = ReporterLogHandler(reporter)
    handler.addFilter(builder.BuildContextFilter())

    # Restored afterwards. It is a thread-local, so leaving it set labels
    # every later line on this thread as build 7 - which is the same bug the
    # code it is testing was written to avoid, arriving through the test.
    try:
        builder._context.build = 7
        record = logging.LogRecord("geelark_farm.flows.chatgpt_login",
                                   logging.INFO, "f", 1,
                                   "screen: password_entry", None, None)
        # No record.row set: the filter on the handler must supply it.
        handler.handle(record)
    finally:
        builder._context.build = logs.NO_BUILD

    assert reporter.rows[7]["step"] == "screen: password_entry"


def test_a_builds_account_column_fills_from_its_own_log():
    """A build does not know its Gmail until it tries one, so the account
    column is blank until 'signing in as <email>' reaches it - and it follows
    the build to the next candidate when the first is dropped. The run flow
    already has the address from the row, so this only ever matters to builds.
    """
    from geelark_farm.ui import BuildReporter

    reporter = BuildReporter()
    reporter.start(1, 1)
    assert reporter.rows[1]["email"] == ""

    reporter.note(1, "signing in as first@example.com")
    assert reporter.rows[1]["email"] == "first@example.com"
    # ...and it is not mistaken for a step.
    assert reporter.rows[1]["step"] == "starting"

    reporter.note(1, "signing in as second@example.com")   # first was bad
    assert reporter.rows[1]["email"] == "second@example.com"
    reporter.note(1, "signing into the app as gpt@example.com")
    assert reporter.rows[1]["email"] == "gpt@example.com"


# ------------------------------------------------------------- the log file
def test_every_invocation_leaves_a_debug_file_whatever_the_console_shows(
        tmp_path, make_settings):
    """The log used to go to the console and die with the terminal - a problem
    hit on one machine was undebuggable from the other, and even on the same
    one, closing the window destroyed the only record."""
    import logging

    settings = make_settings(log_dir=tmp_path, log_level="WARNING")
    root = logging.getLogger()
    before = list(root.handlers)
    path = cli._configure_logging(settings)
    added = [h for h in root.handlers if h not in before]
    try:
        assert path is not None and path.parent == tmp_path
        log = logging.getLogger("geelark_farm.test")
        log.debug("a detail nobody watches live")
        log.warning("a thing worth seeing")
        for handler in added:
            handler.flush()
        text = path.read_text(encoding="utf-8")
        # DEBUG reaches the file even though the console is at WARNING...
        assert "a detail nobody watches live" in text
        assert "a thing worth seeing" in text
        # ...and each line says when, which is what a file is for.
        assert text.splitlines()[0][:2] == "20"
        console = next(h for h in added
                       if not isinstance(h, logging.FileHandler))
        assert console.level == logging.WARNING
    finally:
        for handler in added:
            root.removeHandler(handler)
            handler.close()


def test_a_log_directory_that_cannot_be_made_does_not_kill_the_cli(
        tmp_path, make_settings):
    """A machine where the directory cannot be written gets console logging
    and a warning, not a dead CLI."""
    import logging

    blocker = tmp_path / "taken"
    blocker.write_text("a file where the directory should go")
    settings = make_settings(log_dir=blocker / "logs")
    root = logging.getLogger()
    before = list(root.handlers)
    path = cli._configure_logging(settings)
    added = [h for h in root.handlers if h not in before]
    try:
        assert path is None
        assert added, "console logging must still be configured"
    finally:
        for handler in added:
            root.removeHandler(handler)
            handler.close()


# ------------------------------------------- the commands the code talks about
def test_every_command_the_code_names_is_a_command_that_exists():
    """`geelark run` was named in three places, one of them the error a
    newcomer gets when a tab is missing - so the message that fires exactly
    when someone is lost sent them to a command that has never existed, along
    with a GOOGLE_SHEET_TAB setting nothing reads (2026-08-17).

    Commands get renamed and dropped; the prose that mentions them does not
    move with them. This is the sweep that notices.
    """
    import re
    from pathlib import Path

    from geelark_farm.cli import build_parser

    actions = next(a for a in build_parser()._actions
                   if getattr(a, "choices", None) and a.dest == "command")
    real = set(actions.choices) | {"--help"}

    # Quoted with a backtick or an apostrophe, which is how every one of them
    # is written and what tells a command apart from the same words used as
    # prose - `geelark verify`'s own checklist has a row labelled "geelark
    # api", which is not a command and should not be read as one.
    named: dict[str, list[str]] = {}
    src = Path(__file__).parent.parent / "src"
    for path in sorted(src.rglob("*.py")):
        for found in re.findall(r"[`']geelark ([a-z][a-z-]*)",
                                path.read_text(encoding="utf-8")):
            named.setdefault(found, []).append(path.name)

    unknown = {name: sorted(set(where))
               for name, where in named.items() if name not in real}
    assert not unknown, f"named in the code but not a command: {unknown}"


def test_the_missing_tab_message_says_what_to_do_about_it():
    """The one error a first run is most likely to hit. It has to name the
    tabs that are required, and say which ones it makes itself - otherwise
    someone creates all six by hand and wonders why two look wrong."""
    import inspect

    from geelark_farm import pools

    source = inspect.getsource(pools.Book.open)
    message = source[source.index("has no tab(s) named"):]
    message = message[:message.index("wrong spreadsheet")]

    assert "GOOGLE_SHEET_ID" in message      # the usual real cause
    assert "LISTS_TAB" in message and "HISTORY_TAB" in message
    assert "automatically" in message


def test_watching_a_build_does_not_wait_for_a_terminal_that_is_not_there():
    """`--watch` stops after each phone starts so a person can open the live
    view. Run without a terminal - a container, a cron entry, a piped shell -
    that `input()` is an EOFError, and the phone it had just started is left
    up with the run dead underneath it."""
    import inspect

    from geelark_farm import cli

    source = inspect.getsource(cli.cmd_build)

    assert "sys.stdin.isatty()" in source
    assert source.index("isatty") < source.index('input("Open it')


# ============================ one door onto a phone, and what it owns
class FakePhones:
    """Enough of `phones` to watch a command's effect on a device."""

    RUNNING, STARTING, STOPPED, EXPIRED = 0, 1, 2, 3
    STATUS_NAMES = {0: "running", 1: "starting", 2: "stopped", 3: "expired"}

    def __init__(self, *, already_running=False):
        self.already_running = already_running
        self.started, self.stopped = [], []

    def listing(self, client, page_size=100):
        return [{"id": "P1", "serialNo": "801",
                 "status": self.RUNNING if self.already_running
                 else self.STOPPED}]

    def newest(self, client):
        return self.listing(client)[0]

    def ensure_running(self, client, phone_id, **kwargs):
        if self.already_running:
            return None                      # nothing to hand back
        self.started.append(phone_id)
        return "https://watch/me"

    def stop(self, client, phone_id):
        self.stopped.append(phone_id)

    def screenshot(self, client, phone_id, **kwargs):
        return "https://shot"


class FakeLedger:
    """Enough of `Ledger` to see what a command told it.

    `claim` is here because a command that drives a phone for minutes has to
    say so - `refuse_if_busy` is only a guard if something on the other side
    ever sets a claim for it to find.
    """

    def __init__(self, entry=None):
        self.entry = entry
        self.claimed: list[tuple[str, str]] = []
        self.released: list[tuple[str, str]] = []

    def get(self, phone_id):
        return self.entry

    def claim(self, phone_id, label=""):
        self.claimed.append((phone_id, label))

    def release(self, phone_id, note=""):
        self.released.append((phone_id, note))


def _wire(monkeypatch, fake, *, claimed=False, ledger=None):
    from geelark_farm import cli as cli_mod

    monkeypatch.setattr(cli_mod, "phones", fake)
    monkeypatch.setattr(cli_mod, "build_client", lambda s: object())
    entry = SimpleNamespace(is_claimed=claimed, is_stale=False, label="a build")
    book = ledger if ledger is not None else FakeLedger(entry if claimed else None)
    monkeypatch.setattr(cli_mod.Ledger, "load", staticmethod(lambda d: book))
    return cli_mod


def test_a_diagnostic_stops_the_phone_it_started(monkeypatch, capsys, settings):
    """`phones.py` says anything that starts a phone owns stopping it. These
    started one and walked away, leaving it up with nothing in the ledger
    accounting for it (2026-08-23)."""
    fake = FakePhones(already_running=False)
    cli_mod = _wire(monkeypatch, fake)
    monkeypatch.setattr(cli_mod.phones, "screenshot",
                        lambda c, p, **k: "https://shot")

    cli_mod.cmd_screenshot(settings, SimpleNamespace(phone=None))

    assert fake.started == ["P1"]
    assert fake.stopped == ["P1"]


def test_a_phone_that_was_already_up_is_left_up(monkeypatch, capsys, settings):
    """It belongs to whatever had it running."""
    fake = FakePhones(already_running=True)
    cli_mod = _wire(monkeypatch, fake)

    cli_mod.cmd_screenshot(settings, SimpleNamespace(phone=None))

    assert fake.started == []
    assert fake.stopped == []


def test_a_diagnostic_refuses_a_phone_another_run_is_driving(monkeypatch, settings):
    """`uiautomator dump` cannot run twice at once, and `resolve_phone`
    prefers the running phone - so a bare `dump` aims at the build."""
    fake = FakePhones(already_running=True)
    cli_mod = _wire(monkeypatch, fake, claimed=True)

    with pytest.raises(SystemExit, match="in use by another run"):
        cli_mod.cmd_screenshot(settings, SimpleNamespace(phone=None))

    assert fake.started == []


def test_every_command_that_drives_a_phone_goes_through_the_one_door():
    """The guard existed and only two of seven used it."""
    import inspect

    from geelark_farm import cli as cli_mod

    for name in ("cmd_dump", "cmd_tap", "cmd_shell", "cmd_type",
                 "cmd_screenshot"):
        source = inspect.getsource(getattr(cli_mod, name))
        assert "with device(" in source, f"{name} drives a phone unguarded"


# ============================================ a serial that is not there
def test_a_phone_with_no_serial_does_not_crash_the_listing(
        monkeypatch, capsys, settings):
    """`.get(key, default)` does not apply its default when the key is present
    and null, and formatting None raises. Every other reader in the package
    already writes `str(... or "")` (2026-08-23)."""
    from geelark_farm import cli as cli_mod

    class Unnamed(FakePhones):
        def listing(self, client, page_size=100):
            return [{"id": "P1", "serialNo": None, "status": self.STOPPED}]

        def prune_ledger(self, client, ledger):
            return []

    fake = Unnamed()
    _wire(monkeypatch, fake)

    cli_mod.cmd_phones(settings, SimpleNamespace(ledger=False))

    assert "serial" in capsys.readouterr().out


# ================================================= flags that do nothing
def test_every_flag_the_parser_offers_is_read_by_its_command():
    """`--watch` was declared on `login`, helped, and never read - the command
    took it and drove straight past (2026-08-23)."""
    import inspect
    import re

    from geelark_farm import cli as cli_mod

    source = inspect.getsource(cli_mod)
    unread = []
    for parser, flag in re.findall(r'p_(\w+)\.add_argument\(\s*"--([a-z-]+)"',
                                   source):
        command = getattr(cli_mod, f"cmd_{parser}", None)
        if command is None:
            continue
        if f"args.{flag.replace('-', '_')}" not in inspect.getsource(command):
            unread.append(f"--{flag} on {parser}")
    assert not unread, f"declared and never read: {unread}"


# ======================================= the list of commands in the docs
def test_the_module_says_which_commands_exist_and_is_right():
    """It named `run` and `rows`, which were renamed and removed, and omitted
    fourteen that exist - while claiming to be the full surface."""
    import re

    from geelark_farm import cli as cli_mod

    doc = cli_mod.__doc__ or ""
    grouped = doc.split("Commands are grouped by what they are for:")[-1]
    # Only the lines that are a group - two or more names after a label -
    # rather than every lowercase word after the marker. Taking the words let
    # a command mentioned in the prose below the list count as listed, which
    # is not what the list is for.
    named: set[str] = set()
    for line in grouped.splitlines():
        if not line.startswith("  ") or "  " not in line.strip():
            continue
        _label, _, names = line.strip().rpartition("  ")
        named |= {n.strip() for n in names.split(",")}

    real = set(re.findall(r'add_parser\(\s*"([a-z-]+)"',
                          pathlib.Path("src/geelark_farm/cli.py")
                          .read_text(encoding="utf-8")))

    missing = sorted(real - named)
    assert not missing, f"the docstring does not mention {missing}"


def test_the_cli_names_the_two_sheet_failures_it_did_not():
    """A revoked key raises GSpreadError rather than SheetError, and every
    command that opens the book can raise ConfigError from `require_sheets`.
    Neither was caught, so both ended in a traceback."""
    import inspect

    from geelark_farm import cli as cli_mod

    source = inspect.getsource(cli_mod.main)

    assert "except GSpreadError" in source
    assert source.count("except ConfigError") == 2


# =====================================================================
# The parts of the CLI that decide something (2026-08-26). Most of this
# module is a command building a client, calling one function and
# printing - which is why 35% coverage is not 35% of the risk. These
# are the pieces where a wrong answer costs a phone or an account.
# =====================================================================

class Panel:
    """GeeLark's phone list, and what was done to it."""

    def __init__(self, *items, newest=None):
        self.items = list(items)
        self._newest = newest
        self.started: list[str] = []
        self.stopped: list[str] = []

    def install(self, monkeypatch, *, already_running=False):
        monkeypatch.setattr(cli.phones, "listing", lambda c: self.items)
        monkeypatch.setattr(cli.phones, "newest", lambda c: self._newest)
        monkeypatch.setattr(
            cli.phones, "ensure_running",
            lambda c, pid, **kw: (None if already_running
                                  else (self.started.append(pid) or "url")))
        monkeypatch.setattr(cli.phones, "stop",
                            lambda c, pid: self.stopped.append(pid))
        monkeypatch.setattr(cli, "refuse_if_busy", lambda s, pid: None)
        return self


def phone(pid, *, status=None):
    return {"id": pid, "status": cli.phones.RUNNING if status is None
            else status}


# ------------------------------------------------ which phone a command means
def test_an_explicit_phone_always_wins(monkeypatch):
    """It is the one thing the operator said out loud."""
    Panel(phone("P1")).install(monkeypatch)

    assert cli.resolve_phone(None, "P9") == "P9"


def test_the_single_running_phone_is_the_one_being_worked_on(monkeypatch,
                                                             capsys):
    """`geelark dump` is what you reach for while watching a build go wrong,
    which is when a build is on that phone."""
    Panel(phone("P1"), phone("P2", status=cli.phones.STOPPED)).install(
        monkeypatch)

    assert cli.resolve_phone(None, None) == "P1"
    assert "using the running phone" in capsys.readouterr().out


def test_several_running_phones_is_refused_rather_than_guessed(monkeypatch):
    """Picking wrong means typing a password into the wrong device."""
    Panel(phone("P1"), phone("P2")).install(monkeypatch)

    with pytest.raises(SystemExit) as caught:
        cli.resolve_phone(None, None)

    said = str(caught.value)
    assert "P1" in said and "P2" in said, "which ones is the actionable part"
    assert "--phone" in said


def test_with_nothing_running_the_newest_phone_is_taken(monkeypatch):
    """The one just built is the one a diagnostic is almost always about."""
    Panel(phone("P1", status=cli.phones.STOPPED),
          newest={"id": "P7"}).install(monkeypatch)

    assert cli.resolve_phone(None, None) == "P7"


def test_with_no_phones_at_all_it_says_so(monkeypatch):
    Panel(newest=None).install(monkeypatch)

    with pytest.raises(SystemExit):
        cli.resolve_phone(None, None)


# ------------------------------------------- who owns stopping the phone again
def test_a_phone_this_command_started_is_stopped_afterwards(monkeypatch,
                                                            capsys):
    """`phones.py` says anything that starts a phone owns stopping it. Five of
    these commands started one and walked away, leaving it up with nothing in
    the ledger accounting for it."""
    panel = Panel(phone("P1")).install(monkeypatch)

    with cli.device(None, None, "P1") as phone_id:
        assert phone_id == "P1"

    assert panel.started == ["P1"]
    assert panel.stopped == ["P1"]
    assert "billing ended" in capsys.readouterr().out


def test_a_phone_that_was_already_up_is_left_alone(monkeypatch):
    """It belongs to whatever had it - and that is usually a build."""
    panel = Panel(phone("P1")).install(monkeypatch, already_running=True)

    with cli.device(None, None, "P1"):
        pass

    assert panel.stopped == [], "it stopped a phone it did not start"


def test_a_command_that_fails_still_puts_the_phone_back(monkeypatch):
    """The whole reason this is a context manager. A diagnostic that raises
    used to leave the phone running."""
    panel = Panel(phone("P1")).install(monkeypatch)

    with pytest.raises(RuntimeError):
        with cli.device(None, None, "P1"):
            raise RuntimeError("the dump failed")

    assert panel.stopped == ["P1"]


def test_a_phone_another_run_is_driving_is_refused(monkeypatch):
    """`uiautomator dump` cannot run twice at once, so two flows on one phone
    corrupt each other's screen reads."""
    Panel(phone("P1")).install(monkeypatch)
    monkeypatch.setattr(cli, "refuse_if_busy",
                        lambda s, pid: (_ for _ in ()).throw(
                            SystemExit("phone P1 is busy")))

    with pytest.raises(SystemExit):
        with cli.device(None, None, "P1"):
            pass


# --------------------------------------------------- which account to drive
def test_a_row_outside_the_sheet_is_refused_by_number(monkeypatch,
                                                       make_settings):
    """"--row 40" against nine usable Gmails is a typo, and the range is what
    makes it obvious which."""
    settings = make_settings(sheet_id="abc")

    class Pool:
        available = [object(), object()]

    monkeypatch.setattr("geelark_farm.pools.Book.open",
                        staticmethod(lambda s: type("B", (), {
                            "gmails": Pool, "proxies": Pool})()))

    with pytest.raises(SystemExit, match="1..2"):
        cli.pick_account(settings, 40)


def test_the_diagnostic_path_reads_stock_without_claiming_it(monkeypatch,
                                                              make_settings):
    """A debugging session that quietly consumed stock from under a running
    build would be its own kind of bug."""
    settings = make_settings(sheet_id="abc")
    claimed: list[str] = []

    row = type("Row", (), {
        "credentials": Credentials(email="a@b.com", password="pw",
                                   totp_secret="JBSWY3DPEHPK3PXP"),
        "values": {"Proxy String": "socks5://u:p@1.2.3.4:1080"},
    })()

    class Pool:
        available = [row]

        @staticmethod
        def claim():
            claimed.append("claimed")

    monkeypatch.setattr("geelark_farm.pools.Book.open",
                        staticmethod(lambda s: type("B", (), {
                            "gmails": Pool, "proxies": Pool})()))

    account = cli.pick_account(settings, 1)

    assert account.email == "a@b.com"
    assert account.proxy == "socks5://u:p@1.2.3.4:1080"
    assert claimed == [], "the diagnostic path took stock out of the pool"


def test_a_sheet_with_no_free_exit_says_which_half_is_missing(monkeypatch,
                                                               make_settings):
    settings = make_settings(sheet_id="abc")
    row = type("Row", (), {
        "credentials": Credentials(email="a@b.com", password="pw",
                                   totp_secret=""),
        "values": {},
    })()

    monkeypatch.setattr("geelark_farm.pools.Book.open",
                        staticmethod(lambda s: type("B", (), {
                            "gmails": type("G", (), {"available": [row]}),
                            "proxies": type("P", (), {"available": []})})()))

    with pytest.raises(SystemExit, match="no proxy is free"):
        cli.pick_account(settings, 1)


# ------------------------------------------------ what an operator is told
@pytest.mark.parametrize("error,prefix,code", [
    (AccountError("row 4 has no password"), "account:", 1),
    (SheetError("quota"), "sheet:", 2),
    (ConfigError("GOOGLE_SHEET_ID is not set"), "config:", 2),
    (ProxyError("SX4 is unusable"), "proxy:", 1),
    (cli.phones.PhoneError("phone 801 has expired"), "phone:", 1),
    (ShellError("the phone would not run 'dumpsys'"), "device:", 1),
    (TransportError("connection reset"), "network:", 1),
])
def test_every_failure_an_operator_can_act_on_gets_a_line_not_a_traceback(
        monkeypatch, capsys, error, prefix, code):
    """A traceback says the tool broke; these say the setup did, and which
    part. The exit code separates "fix your configuration" (2) from "that
    attempt failed" (1), which is what a script around this reads."""
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda: object()))
    monkeypatch.setattr(cli, "_configure_logging", lambda *a, **k: None)

    def boom(settings, args):
        raise error

    monkeypatch.setitem(cli.main.__globals__, "cmd_plan", boom)
    monkeypatch.setattr(sys, "argv", ["geelark", "plan"])

    assert cli.main() == code
    assert capsys.readouterr().err.startswith(prefix)


# =========================================== the lifecycle commands
# Seventeen command functions ran their `def` line and nothing else. These
# are the guards and exit codes inside them: what a script that calls
# `geelark <command>` and reads `$?` is actually relying on.
def entry(phone_id="P1", serial="801"):
    """A real ledger.Entry, not something shaped like one."""
    from geelark_farm.ledger import Entry
    return Entry(phone_id=phone_id, created_at=0.0, serial=serial)


class Lifecycle(FakePhones):
    """The calls the lifecycle commands make, and a record of each."""

    class PhoneError(Exception):
        pass

    def __init__(self, *, state=None, **kw):
        super().__init__(**kw)
        self.state = state if state is not None else self.STOPPED
        self.created, self.deleted, self.waited, self.reaped = [], [], [], []
        self.plan_info = {"plan": 1, "monthlyFee": 50, "profiles": 30,
                          "availableProfiles": 4, "parallels": 5,
                          "expirationTime": 0}
        self.verdicts: list[tuple[str, str]] = []

    def prune_ledger(self, client, ledger):
        return []

    def status(self, client, phone_id):
        return self.state

    def create(self, client, settings, parsed, *, ledger=None, name=None,
               label=""):
        self.created.append((parsed, name, label))
        return entry()

    def start(self, client, phone_id):
        self.started.append(phone_id)
        return "https://watch/me"

    def wait_until_running(self, client, phone_id):
        self.waited.append(phone_id)

    def delete(self, client, phone_ids, *, ledger=None):
        self.deleted.extend(phone_ids)

    def plan(self, client):
        return self.plan_info

    def reapable(self, client, ledger):
        return self.verdicts

    def reap(self, client, ledger, *, verdicts=None, dry_run=False):
        self.reaped.extend(verdicts or [])
        return len(verdicts or [])


# ------------------------------------------------------------------ delete
def test_a_running_phone_is_not_deleted_out_from_under_its_billing(
        monkeypatch, capsys, settings):
    """Deleting a running phone is the one lifecycle call GeeLark refuses
    per-item while answering `code: 0` at the envelope, so the refusal is
    caught here rather than read back out of a batch answer."""
    fake = Lifecycle(state=Lifecycle.RUNNING)
    cli_mod = _wire(monkeypatch, fake)

    code = cli_mod.cmd_delete(settings, SimpleNamespace(phone="P1", yes=True))

    assert code == 1
    assert fake.deleted == []
    assert "stop it first" in capsys.readouterr().err


def test_deleting_a_stopped_phone_needs_saying_yes(monkeypatch, capsys,
                                                   settings):
    fake = Lifecycle()
    cli_mod = _wire(monkeypatch, fake)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    code = cli_mod.cmd_delete(settings, SimpleNamespace(phone="P1", yes=False))

    assert code == 1 and fake.deleted == []
    assert "cancelled" in capsys.readouterr().out


def test_yes_skips_the_prompt_that_would_block_a_script(monkeypatch, settings):
    """Without --yes this reads stdin, and a script that pipes nothing in gets
    an EOFError instead of a deletion."""
    fake = Lifecycle()
    cli_mod = _wire(monkeypatch, fake)

    def refuse(prompt=""):
        raise AssertionError("--yes still asked")

    monkeypatch.setattr("builtins.input", refuse)

    assert cli_mod.cmd_delete(settings, SimpleNamespace(phone="P1",
                                                        yes=True)) == 0
    assert fake.deleted == ["P1"]


# -------------------------------------------------------------------- reap
def test_reaping_nothing_is_success(monkeypatch, capsys, settings):
    """The normal state of a tidy account, and a non-zero here would make the
    command unusable from cron."""
    fake = Lifecycle()
    cli_mod = _wire(monkeypatch, fake)

    assert cli_mod.cmd_reap(settings, SimpleNamespace(dry_run=False)) == 0
    assert "nothing to reap" in capsys.readouterr().out


def test_a_dry_run_reap_stops_nothing(monkeypatch, capsys, settings):
    fake = Lifecycle()
    fake.verdicts = [("P1", "not in the ledger")]
    cli_mod = _wire(monkeypatch, fake)

    assert cli_mod.cmd_reap(settings, SimpleNamespace(dry_run=True)) == 0
    assert fake.reaped == []
    assert "would be stopped" in capsys.readouterr().out


def test_a_real_reap_stops_exactly_what_it_listed(monkeypatch, settings):
    """The verdicts are handed on rather than looked up again: a phone that
    was claimed or released in the seconds between is a phone this would
    otherwise stop by surprise."""
    fake = Lifecycle()
    fake.verdicts = [("P1", "not in the ledger"), ("P2", "already released")]
    cli_mod = _wire(monkeypatch, fake)

    assert cli_mod.cmd_reap(settings, SimpleNamespace(dry_run=False)) == 0
    assert fake.reaped == fake.verdicts


# ------------------------------------------------------------------ create
def test_a_proxy_is_checked_before_a_phone_is_paid_for(monkeypatch, capsys,
                                                       settings):
    """The order is the point: a phone created behind an exit that does not
    carry traffic is a slot spent and a row that cannot finish."""
    fake = Lifecycle()
    cli_mod = _wire(monkeypatch, fake)
    order = []
    monkeypatch.setattr(cli_mod.proxy, "check",
                        lambda c, p: order.append("checked") or
                        {"outboundIP": "1.1.1.1", "country": "US"})
    monkeypatch.setattr(fake, "create",
                        lambda *a, **k: order.append("created") or entry())

    code = cli_mod.cmd_create(settings, SimpleNamespace(
        proxy="socks5://u:p@h:1080", name=None, label="", start=False))

    assert code == 0
    assert order == ["checked", "created"]
    assert fake.started == []               # not booted without --start


def test_creating_with_start_waits_for_the_phone_before_saying_it_is_up(
        monkeypatch, capsys, settings):
    """The live-view link is printed next to "running", and a link handed out
    before the phone answers opens on nothing."""
    fake = Lifecycle()
    cli_mod = _wire(monkeypatch, fake)
    monkeypatch.setattr(cli_mod.proxy, "check",
                        lambda c, p: {"outboundIP": "1.1.1.1", "country": ""})

    assert cli_mod.cmd_create(settings, SimpleNamespace(
        proxy="socks5://u:p@h:1080", name=None, label="", start=True)) == 0
    assert fake.started == ["P1"] and fake.waited == ["P1"]
    assert "unknown country" in capsys.readouterr().out


def test_a_phone_created_by_hand_is_not_claimed(monkeypatch, settings):
    """A claim means "a run is working on this right now", and `reapable`
    reads a fresh one as exactly that. Claiming here started a phone billing
    and hid it from the reaper for as long as the claim took to go stale,
    with nothing working on it."""
    fake = Lifecycle()
    book = FakeLedger()
    cli_mod = _wire(monkeypatch, fake, ledger=book)
    monkeypatch.setattr(cli_mod.proxy, "check", lambda c, p: {})

    cli_mod.cmd_create(settings, SimpleNamespace(
        proxy="socks5://u:p@h:1080", name=None, label="", start=True))

    assert book.claimed == []


# ------------------------------------------------------------- start / stop
def test_starting_without_wait_does_not_block(monkeypatch, settings):
    fake = Lifecycle()
    cli_mod = _wire(monkeypatch, fake)

    assert cli_mod.cmd_start(settings, SimpleNamespace(phone="P1",
                                                       wait=False)) == 0
    assert fake.started == ["P1"] and fake.waited == []


def test_stopping_everything_when_nothing_runs_says_so(monkeypatch, capsys,
                                                       settings):
    fake = Lifecycle(already_running=False)
    cli_mod = _wire(monkeypatch, fake)

    assert cli_mod.cmd_stop(settings, SimpleNamespace(phone=None,
                                                      all=True)) == 0
    assert fake.stopped == []
    assert "nothing is running" in capsys.readouterr().out


def test_stopping_a_phone_by_hand_releases_its_claim_too(monkeypatch,
                                                          settings):
    """Otherwise every later command refuses it as busy until the claim goes
    stale hours on, and stopping it by hand is a deliberate "I am done"."""
    fake = Lifecycle(already_running=True)
    book = FakeLedger()
    cli_mod = _wire(monkeypatch, fake, ledger=book)

    assert cli_mod.cmd_stop(settings, SimpleNamespace(phone=None,
                                                      all=True)) == 0
    assert fake.stopped == ["P1"]
    assert [p for p, _ in book.released] == ["P1"]


# -------------------------------------------------------------------- plan
def test_a_full_plan_says_the_error_code_the_next_create_will_fail_with(
        monkeypatch, capsys, settings):
    """[44002] is the whole answer to "why was my phone refused", and it
    arrives from GeeLark with nothing else attached."""
    fake = Lifecycle()
    fake.plan_info = dict(fake.plan_info, availableProfiles=0)
    cli_mod = _wire(monkeypatch, fake)

    assert cli_mod.cmd_plan(settings, SimpleNamespace()) == 0
    assert "44002" in capsys.readouterr().out


def test_slots_this_api_cannot_list_are_named_rather_than_left_missing(
        monkeypatch, capsys, settings):
    """The pool is shared with browser profiles, which live behind the local
    agent. Without this the numbers simply do not add up and the search is
    for a phone that does not exist."""
    fake = Lifecycle()
    # 30 slots, 4 free, 1 phone: 25 are something else.
    cli_mod = _wire(monkeypatch, fake)

    cli_mod.cmd_plan(settings, SimpleNamespace())

    assert "browser profiles share this pool" in capsys.readouterr().out


def test_a_plan_with_no_expiry_does_not_report_1970(monkeypatch, capsys,
                                                     settings):
    """`localtime(0)` renders as 1 Jan 1970, which reads as an expired plan."""
    fake = Lifecycle()
    fake.plan_info = dict(fake.plan_info, expirationTime=None)
    cli_mod = _wire(monkeypatch, fake)

    cli_mod.cmd_plan(settings, SimpleNamespace())

    assert "1970" not in capsys.readouterr().out


# ------------------------------------------------------------------- proxy
def test_a_backconnect_gateway_is_named_because_google_judges_the_exit(
        monkeypatch, capsys, settings):
    fake = Lifecycle()
    cli_mod = _wire(monkeypatch, fake)
    monkeypatch.setattr(cli_mod.proxy, "check",
                        lambda c, p: {"outboundIP": "9.9.9.9", "country": ""})

    assert cli_mod.cmd_proxy(settings,
                             SimpleNamespace(url="socks5://u:p@h:1080")) == 0
    out = capsys.readouterr().out
    assert "gateway" in out
    # And an empty country is reported as a gap in GeeLark's lookup, never as
    # a verdict on the address.
    assert "not reported by GeeLark" in out


# ================================================ the diagnostic commands
# Each of these is reached for while something is going wrong, so what they
# return matters more than usual: a 0 from a diagnostic that read nothing
# says the phone is fine.
def test_a_dump_that_reads_nothing_is_a_failure_not_an_empty_screen(
        monkeypatch, capsys, settings):
    fake = FakePhones(already_running=True)
    cli_mod = _wire(monkeypatch, fake)
    monkeypatch.setattr(cli_mod.screen, "capture", lambda c, p: None)

    code = cli_mod.cmd_dump(settings, SimpleNamespace(phone="P1", save=None))

    assert code == 1
    assert "could not read" in capsys.readouterr().err


def test_a_dump_can_keep_what_it_read_as_a_fixture(monkeypatch, tmp_path,
                                                   make_settings):
    """`tests/fixtures/` is the record of how each screen actually looks, and
    the only way one gets there is this flag."""
    settings = make_settings(state_dir=tmp_path)
    fake = FakePhones(already_running=True)
    cli_mod = _wire(monkeypatch, fake)
    monkeypatch.setattr(cli_mod.screen, "capture",
                        lambda c, p: "<hierarchy></hierarchy>")
    saved = tmp_path / "kept.xml"

    code = cli_mod.cmd_dump(settings, SimpleNamespace(phone="P1",
                                                      save=str(saved)))

    assert code == 0 and saved.read_text(encoding="utf-8")


def test_a_tap_that_matched_nothing_lists_what_was_there(monkeypatch, capsys,
                                                          settings):
    """Exit 1 and the screen beside it: "no element matching X" on its own
    sends you back for a dump you could have had here."""
    from geelark_farm.screen import Element

    fake = FakePhones(already_running=True)
    cli_mod = _wire(monkeypatch, fake)
    button = Element(text="Cancel", desc="", cls="android.widget.Button",
                     resource_id="", bounds="[0,0][10,10]", clickable=True,
                     enabled=True, focused=False, password=False)
    monkeypatch.setattr(cli_mod.screen, "read_screen", lambda c, p: [button])
    monkeypatch.setattr(cli_mod.screen, "tap_label",
                        lambda c, p, e, label: False)

    code = cli_mod.cmd_tap(settings, SimpleNamespace(phone="P1",
                                                     label="Install"))

    assert code == 1
    assert "Cancel" in capsys.readouterr().err


def test_a_shell_command_is_strict_because_silence_is_an_answer(
        monkeypatch, capsys, settings):
    """"It printed nothing" and "it did not run" are the two answers you are
    choosing between, and this reported success for both."""
    fake = FakePhones(already_running=True)
    cli_mod = _wire(monkeypatch, fake)
    asked = {}
    monkeypatch.setattr(cli_mod.shell, "run",
                        lambda c, p, cmd, **kw: asked.update(kw) or "output")

    assert cli_mod.cmd_shell(settings, SimpleNamespace(phone="P1",
                                                       cmd="ls")) == 0
    assert asked.get("strict") is True
    assert "output" in capsys.readouterr().out


def test_typing_that_the_device_refuses_is_a_failure(monkeypatch, capsys,
                                                      settings):
    from geelark_farm.shell import TypingError

    fake = FakePhones(already_running=True)
    cli_mod = _wire(monkeypatch, fake)

    def refuse(client, phone_id, text):
        raise TypingError("no field is focused")

    monkeypatch.setattr(cli_mod.shell, "type_text", refuse)

    code = cli_mod.cmd_type(settings, SimpleNamespace(phone="P1", text="abc"))

    assert code == 1
    assert "no field is focused" in capsys.readouterr().err


def test_a_screenshot_that_did_not_happen_is_a_failure(monkeypatch, capsys,
                                                        settings):
    class NoShot(FakePhones):
        def screenshot(self, client, phone_id, **kwargs):
            return None

    cli_mod = _wire(monkeypatch, NoShot(already_running=True))

    code = cli_mod.cmd_screenshot(settings, SimpleNamespace(phone="P1"))

    assert code == 1
    assert "screenshot failed" in capsys.readouterr().err


# ==================================================== login and install
# The two long flows. Each takes a phone for minutes, so the questions are
# who knows it is taken and who puts it back.
def install_outcome(ok=True):
    """The real class the real function answers with."""
    from geelark_farm.flows.play_install import Outcome
    return Outcome(kind="success" if ok else "fatal",
                   reason="installed" if ok else "no_install_button")


def _wire_install(monkeypatch, fake, ledger, *, accounts_on=("a@example.com",),
                  ok=True):
    cli_mod = _wire(monkeypatch, fake, ledger=ledger)
    monkeypatch.setattr(cli_mod.shell, "device_accounts",
                        lambda c, p, **k: list(accounts_on))
    monkeypatch.setattr(cli_mod.shell, "third_party_packages",
                        lambda c, p: ["com.openai.chatgpt"])
    monkeypatch.setattr(cli_mod.play_install, "install",
                        lambda *a, **k: install_outcome(ok))
    return cli_mod


def install_args(**kw):
    base = dict(phone="P1", package="com.example", keep=False, watch=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_an_install_says_it_is_driving_the_phone_it_drives(
        monkeypatch, tmp_path, make_settings):
    """`refuse_if_busy` is only a guard if something on the other side sets a
    claim for it to find. This asked the question for ten minutes and never
    answered it, so a `dump` or a build arriving meanwhile found the phone
    free and corrupted both screen reads (2026-08-27)."""
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    book = FakeLedger()
    cli_mod = _wire_install(monkeypatch, Lifecycle(already_running=True), book)

    assert cli_mod.cmd_install(settings, install_args()) == 0
    assert [p for p, _ in book.claimed] == ["P1"]
    assert [p for p, _ in book.released] == ["P1"]


def test_a_phone_this_install_booted_is_not_left_billing_when_it_turns_away(
        monkeypatch, tmp_path, make_settings):
    """The account check is a refusal, not a crash, so it returned before the
    try that stops the phone - and the phone it had just started stayed up
    with the command dead underneath it (2026-08-27)."""
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    fake = Lifecycle(already_running=False)
    book = FakeLedger()
    cli_mod = _wire_install(monkeypatch, fake, book, accounts_on=())

    code = cli_mod.cmd_install(settings, install_args())

    assert code == 1
    assert fake.started == ["P1"]          # it did boot the phone
    assert fake.stopped == ["P1"]          # and it put it back
    assert [p for p, _ in book.released] == ["P1"]


def test_an_install_that_failed_still_puts_the_phone_back(
        monkeypatch, tmp_path, make_settings):
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    fake = Lifecycle(already_running=True)
    cli_mod = _wire_install(monkeypatch, fake, FakeLedger(), ok=False)

    assert cli_mod.cmd_install(settings, install_args()) == 1
    assert fake.stopped == ["P1"]


def test_keep_leaves_the_phone_running_and_says_what_that_costs(
        monkeypatch, tmp_path, capsys, make_settings):
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    fake = Lifecycle(already_running=True)
    cli_mod = _wire_install(monkeypatch, fake, FakeLedger())

    assert cli_mod.cmd_install(settings, install_args(keep=True)) == 0
    assert fake.stopped == []
    assert "LEFT RUNNING" in capsys.readouterr().out


def test_an_install_watched_without_a_terminal_does_not_wait_for_nobody(
        monkeypatch, tmp_path, make_settings):
    """With no terminal the input is an EOFError, raised over a phone this
    has just started."""
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    cli_mod = _wire_install(monkeypatch, Lifecycle(already_running=True),
                            FakeLedger())
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: False)

    def refuse(prompt=""):
        raise AssertionError("waited for a terminal that is not there")

    monkeypatch.setattr("builtins.input", refuse)

    assert cli_mod.cmd_install(settings, install_args(watch=True)) == 0


# ---------------------------------------------------------------- login
def account(row=1):
    from geelark_farm.accounts import Account
    return Account(email="a@example.com", password="pw", totp_secret="",
                   proxy="socks5://u:p@h:1080", row=row)


def login_outcome(ok=True):
    """The router's Outcome, which is the class `sign_in` really answers with."""
    from geelark_farm.flows.router import Outcome
    return Outcome(kind="success" if ok else "fatal",
                   reason="signed_in" if ok else "wrong_password")


def _wire_login(monkeypatch, fake, book, *, ok=True):
    cli_mod = _wire(monkeypatch, fake, ledger=book)
    monkeypatch.setattr(cli_mod, "pick_account", lambda s, row: account(row))
    monkeypatch.setattr(cli_mod.proxy, "check",
                        lambda c, p: {"outboundIP": "1.1.1.1"})
    monkeypatch.setattr(cli_mod.shell, "device_accounts",
                        lambda c, p, **k: ["a@example.com"])
    monkeypatch.setattr(cli_mod.google_login, "sign_in",
                        lambda *a, **k: login_outcome(ok))
    return cli_mod


def login_args(**kw):
    base = dict(row=1, phone="P1", keep=False, watch=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_login_releases_the_phone_however_it_ends(monkeypatch, tmp_path,
                                                     make_settings):
    """An interrupted experiment that left the claim set made every later
    command refuse the phone as busy until it went stale."""
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    book = FakeLedger()
    cli_mod = _wire_login(monkeypatch, Lifecycle(already_running=True), book,
                          ok=False)

    assert cli_mod.cmd_login(settings, login_args()) == 1
    assert [p for p, _ in book.claimed] == ["P1"]
    assert [p for p, _ in book.released] == ["P1"]


def test_a_login_that_created_the_phone_keeps_it_for_inspection(
        monkeypatch, tmp_path, capsys, make_settings):
    """It made the phone and the login failed on it, so the phone is the
    evidence - and it names the command that deletes it."""
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    cli_mod = _wire_login(monkeypatch, Lifecycle(already_running=True),
                          FakeLedger(), ok=False)

    assert cli_mod.cmd_login(settings, login_args(phone=None)) == 1
    out = capsys.readouterr().out
    assert "kept for inspection" in out and "geelark delete" in out


def test_a_login_stops_the_phone_it_was_given_unless_told_to_keep_it(
        monkeypatch, tmp_path, make_settings):
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    fake = Lifecycle(already_running=True)
    cli_mod = _wire_login(monkeypatch, fake, FakeLedger())

    assert cli_mod.cmd_login(settings, login_args()) == 0
    assert fake.stopped == ["P1"]


# --------------------------------------------------------- pick_account
def test_a_row_outside_the_dev_file_is_refused_by_number(monkeypatch,
                                                          make_settings):
    """"--row 9 is out of range (1..2)" is the whole answer; a traceback out
    of a list index is not."""
    from geelark_farm import cli as cli_mod

    settings = make_settings(sheet_id="")
    monkeypatch.setattr(cli_mod.accounts, "load_dev_accounts",
                        lambda path: [account(1), account(2)])

    with pytest.raises(SystemExit, match=r"1\.\.2"):
        cli_mod.pick_account(settings, 9)


# ---------------------------------------------------------------- pools
class FakePool:
    def __init__(self, tab, available=(), stuck=(), broken=()):
        self.tab = tab
        self.available, self.stuck, self.broken = (list(available),
                                                   list(stuck), list(broken))


class FakeBook:
    def __init__(self):
        self.reloaded = 0
        self.proxies = FakePool("Proxy", available=[1])
        self.gmails = FakePool("Gmails", available=[1, 2])
        self.apps = FakePool("Gpt Info")

    def sync_lists(self):
        return {"Status": ["ready", "error"]}

    def release_stuck(self):
        return 3

    def reload(self):
        self.reloaded += 1


def _wire_pools(monkeypatch):
    from geelark_farm import cli as cli_mod
    from geelark_farm.pools import Book

    book = FakeBook()
    monkeypatch.setattr(Book, "open", staticmethod(lambda s: book))
    monkeypatch.setattr(cli_mod, "build_client", lambda s: object())
    monkeypatch.setattr(cli_mod.Ledger, "load",
                        staticmethod(lambda d: FakeLedger()))
    return cli_mod, book


def pools_args(**kw):
    base = dict(sync_lists=False, release_stuck=False, no_sync=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_rewriting_the_dropdowns_does_not_go_on_to_report_the_tabs(
        monkeypatch, capsys, settings):
    """It is a write, asked for on its own. Reporting afterwards would sync
    the sheet as a side effect of a command that says it only rewrites lists."""
    cli_mod, book = _wire_pools(monkeypatch)

    assert cli_mod.cmd_pools(settings, pools_args(sync_lists=True)) == 0
    assert book.reloaded == 0
    assert "Status: ready, error" in capsys.readouterr().out


def test_freeing_stuck_rows_says_how_many_it_freed(monkeypatch, capsys,
                                                    settings):
    cli_mod, book = _wire_pools(monkeypatch)

    assert cli_mod.cmd_pools(settings, pools_args(release_stuck=True)) == 0
    assert "released 3 row(s)" in capsys.readouterr().out


def test_no_sync_reports_the_tabs_without_correcting_them_first(
        monkeypatch, capsys, settings):
    """The default corrects the sheet before reporting, so the numbers are
    what a run would find. --no-sync is for seeing what the sheet says."""
    from geelark_farm import builder as builder_mod

    cli_mod, book = _wire_pools(monkeypatch)

    def refuse(*a, **k):
        raise AssertionError("--no-sync synced anyway")

    monkeypatch.setattr(builder_mod, "sync_sheet", refuse)

    assert cli_mod.cmd_pools(settings, pools_args(no_sync=True)) == 0
    out = capsys.readouterr().out
    assert "--no-sync" in out and "2 available" in out


# --------------------------------------------------------------- verify
def check(name, state, detail="fine"):
    from geelark_farm.verify import Check
    return Check(name, state, detail)


def cli_mod_verify(monkeypatch, settings):
    from geelark_farm import cli as cli_mod
    return cli_mod.cmd_verify(settings, SimpleNamespace())


def test_a_setup_with_something_broken_exits_non_zero(monkeypatch, capsys,
                                                       settings):
    """This is what a first-run script branches on."""
    from geelark_farm import verify as verify_mod

    monkeypatch.setattr(verify_mod, "run_checks", lambda s: [
        check("api key", verify_mod.OK),
        check("sheet", verify_mod.FATAL, "shared as a Viewer\ngrant Editor"),
    ])

    code = cli_mod_verify(monkeypatch, settings)

    assert code == 1
    out = capsys.readouterr().out
    assert "1 thing(s) to fix: sheet" in out
    # The remedy is indented under the line it belongs to, not run together.
    assert "grant Editor" in out


def test_warnings_alone_are_usable_and_say_so(monkeypatch, capsys, settings):
    """A warning is something a run would stop on, not something broken - and
    exiting 1 for it would make the check unusable as a gate."""
    from geelark_farm import verify as verify_mod

    monkeypatch.setattr(verify_mod, "run_checks", lambda s: [
        check("api key", verify_mod.OK),
        check("free slots", verify_mod.WARN, "none left"),
    ])

    assert cli_mod_verify(monkeypatch, settings) == 0
    assert "Usable." in capsys.readouterr().out


def test_a_clean_setup_says_so_without_qualification(monkeypatch, capsys,
                                                      settings):
    from geelark_farm import verify as verify_mod

    monkeypatch.setattr(verify_mod, "run_checks", lambda s: [
        check("api key", verify_mod.OK)])

    assert cli_mod_verify(monkeypatch, settings) == 0
    assert "Everything checks out." in capsys.readouterr().out


# ----------------------------------------------------------------- ping
def test_ping_counts_what_is_billing_and_says_how_to_stop_it(monkeypatch,
                                                              capsys, settings):
    """An informational command, with one thing on it worth saying loudly."""
    cli_mod = _wire(monkeypatch, Lifecycle(already_running=True))

    assert cli_mod.cmd_ping(settings, SimpleNamespace()) == 0
    out = capsys.readouterr().out
    assert "1 phone(s) RUNNING and billing" in out
    assert "geelark stop --all" in out


def test_ping_on_a_quiet_account_says_nothing_about_billing(monkeypatch,
                                                             capsys, settings):
    cli_mod = _wire(monkeypatch, Lifecycle(already_running=False))

    assert cli_mod.cmd_ping(settings, SimpleNamespace()) == 0
    assert "billing" not in capsys.readouterr().out


# --------------------------------------------------------------- finish
@pytest.mark.parametrize("builds,expected", [
    ([], 0),                                        # a finished pool
    ([build(1, True)], 0),
    ([build(1, True), build(2, False)], 1),
])
def test_finish_exit_code(builds, expected, tmp_path, monkeypatch, capsys,
                          make_settings):
    """Same rule as `build`: nothing to finish is the normal state, and a
    non-zero for it makes the command unusable from cron."""
    from geelark_farm import builder as builder_mod

    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    monkeypatch.setattr(cli, "build_client", lambda s: object())
    monkeypatch.setattr(builder_mod, "finish_run", lambda *a, **k: builds)
    monkeypatch.setattr(builder_mod, "summarise", lambda b: "summary")

    assert cli.cmd_finish(settings, Args()) == expected
    if not builds:
        assert "nothing to finish" in capsys.readouterr().out


def test_a_finish_dry_run_reports_nothing_and_succeeds(tmp_path, monkeypatch,
                                                        make_settings):
    """--dry-run spends nothing, so there is no summary to print and no
    failure to inherit an exit code from."""
    from geelark_farm import builder as builder_mod

    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    monkeypatch.setattr(cli, "build_client", lambda s: object())
    monkeypatch.setattr(builder_mod, "finish_run",
                        lambda *a, **k: [build(1, False)])

    def refuse(builds):
        raise AssertionError("a dry run summarised anyway")

    monkeypatch.setattr(builder_mod, "summarise", refuse)

    assert cli.cmd_finish(settings, Args(dry_run=True)) == 0


# ------------------------------------------- what the listing adds on request
@pytest.mark.parametrize("entry_for,expected", [
    (None, "[not in ledger]"),
    (SimpleNamespace(is_claimed=True, label="build 3"), "[claimed: build 3]"),
    (SimpleNamespace(is_claimed=False, label="build 3"), "[build 3]"),
])
def test_the_ledger_column_says_which_of_three_things_a_phone_is(
        entry_for, expected, monkeypatch, capsys, settings):
    """A phone GeeLark shows and the ledger does not know is the one worth
    finding: nothing local is accountable for it, so nothing will stop it."""
    fake = Lifecycle(already_running=True)
    book = FakeLedger(entry_for)
    cli_mod = _wire(monkeypatch, fake, ledger=book)

    assert cli_mod.cmd_phones(settings, SimpleNamespace(ledger=True)) == 0
    assert expected in capsys.readouterr().out


def test_the_listing_says_what_is_billing_before_anything_else_is_read(
        monkeypatch, capsys, settings):
    cli_mod = _wire(monkeypatch, Lifecycle(already_running=True))

    cli_mod.cmd_phones(settings, SimpleNamespace(ledger=False))

    assert "RUNNING and billing" in capsys.readouterr().out


# --------------------------------------------- what pools says is wrong
def test_pools_names_the_rows_that_cannot_be_used_and_why(monkeypatch, capsys,
                                                           settings):
    """A broken row is one nobody will notice until a build skips it. The row
    number and the reason are what turns it into a thing to fix."""
    from geelark_farm import builder as builder_mod

    cli_mod, book = _wire_pools(monkeypatch)
    book.gmails.stuck = [1, 2]
    book.gmails.broken = [SimpleNamespace(sheet_row=7,
                                          error="2FA secret is not base32")]
    monkeypatch.setattr(builder_mod, "sync_sheet",
                        lambda *a, **k: {"retired": ["a@example.com"]})

    assert cli_mod.cmd_pools(settings, pools_args()) == 0
    out = capsys.readouterr().out
    assert "retired: a@example.com" in out
    assert "2 stuck as in_use" in out and "--release-stuck" in out
    assert "row 7: 2FA secret is not base32" in out
    assert book.reloaded == 1


# ------------------------------------------------- exit codes out of main
def test_running_with_no_command_prints_help_and_succeeds(capsys):
    """Bare `geelark` is somebody finding out what it does, not a failure."""
    assert cli.main([]) == 0
    assert "usage:" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("raised,code,prefix", [
    (ProxyError("bad url"), 1, "proxy:"),
    (AccountError("2FA secret is not base32"), 1, "account:"),
    (ShellError("device did not answer"), 1, "device:"),
    (TransportError("connection reset"), 1, "network:"),
    (ConfigError("SHEET_ID is not set"), 2, "config:"),
    (SheetError("no Status column"), 2, "sheet:"),
])
def test_each_failure_leaves_by_its_own_door_with_its_own_code(
        raised, code, prefix, monkeypatch, capsys, tmp_path, make_settings):
    """2 is "your setup is wrong", 1 is "this run did not work". A script that
    retries on 1 and stops on 2 depends on the difference, and every one of
    these used to arrive as a traceback."""
    settings = make_settings(state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda: settings))

    def fail(settings, args):
        raise raised

    monkeypatch.setattr(cli, "cmd_ping", fail)

    assert cli.main(["ping"]) == code
    # In stderr rather than at the front of it: the logging banner is written
    # there too, and it is written first.
    assert f"{prefix} " in capsys.readouterr().err


# ------------------------------------------- the other side of each branch
def test_wait_blocks_until_the_phone_has_actually_settled(monkeypatch,
                                                           settings):
    """Without it the command returns while the phone is still booting, and
    the next thing to touch it fails for a reason that is not its own."""
    fake = Lifecycle()
    cli_mod = _wire(monkeypatch, fake)

    assert cli_mod.cmd_start(settings, SimpleNamespace(phone="P1",
                                                       wait=True)) == 0
    assert fake.waited == ["P1"]


def test_stopping_one_phone_does_not_touch_the_others(monkeypatch, settings):
    fake = Lifecycle(already_running=True)
    cli_mod = _wire(monkeypatch, fake)

    assert cli_mod.cmd_stop(settings, SimpleNamespace(phone="P9",
                                                      all=False)) == 0
    assert fake.stopped == ["P9"]


def test_a_tap_that_landed_says_nothing_and_succeeds(monkeypatch, capsys,
                                                      settings):
    cli_mod = _wire(monkeypatch, FakePhones(already_running=True))
    monkeypatch.setattr(cli_mod.screen, "read_screen", lambda c, p: [])
    monkeypatch.setattr(cli_mod.screen, "tap_label",
                        lambda c, p, e, label: True)

    assert cli_mod.cmd_tap(settings, SimpleNamespace(phone="P1",
                                                     label="Install")) == 0
    assert capsys.readouterr().err == ""


def test_typing_that_worked_says_how_much_went_in(monkeypatch, capsys,
                                                   settings):
    """The count is the check: a field that silently swallowed half the
    password looks identical to one that took it."""
    cli_mod = _wire(monkeypatch, FakePhones(already_running=True))
    monkeypatch.setattr(cli_mod.shell, "type_text", lambda c, p, text: None)

    assert cli_mod.cmd_type(settings, SimpleNamespace(phone="P1",
                                                      text="secret")) == 0
    assert "6 character(s)" in capsys.readouterr().out


def test_the_nth_row_of_the_dev_file_is_the_nth_row(monkeypatch,
                                                     make_settings):
    """Numbered from 1 as in a spreadsheet body, so `--row 2` means the same
    thing here as it will once the sheet exists."""
    from geelark_farm import cli as cli_mod

    settings = make_settings(sheet_id="")
    monkeypatch.setattr(cli_mod.accounts, "load_dev_accounts",
                        lambda path: [account(1), account(2)])

    assert cli_mod.pick_account(settings, 2).row == 2


def test_a_login_left_running_says_what_that_costs(monkeypatch, tmp_path,
                                                    capsys, make_settings):
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    fake = Lifecycle(already_running=True)
    cli_mod = _wire_login(monkeypatch, fake, FakeLedger())

    assert cli_mod.cmd_login(settings, login_args(keep=True)) == 0
    assert fake.stopped == []
    assert "LEFT RUNNING" in capsys.readouterr().out


def test_a_login_watched_without_a_terminal_drives_on(monkeypatch, tmp_path,
                                                       make_settings):
    """The flag was declared, helped, and never read - and once it was read,
    a machine with no terminal would have got an EOFError instead."""
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    cli_mod = _wire_login(monkeypatch, Lifecycle(already_running=False),
                          FakeLedger())
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: False)

    def refuse(prompt=""):
        raise AssertionError("waited for a terminal that is not there")

    monkeypatch.setattr("builtins.input", refuse)

    assert cli_mod.cmd_login(settings, login_args(watch=True)) == 0


def test_a_watched_build_mints_its_link_per_phone_and_does_not_block(
        monkeypatch, tmp_path, make_settings):
    """The live-view token expires within seconds, so it is minted as each
    phone comes up rather than once at the start - and with no terminal there
    is nobody to wait for."""
    from geelark_farm import builder as builder_mod

    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    fake = Lifecycle()
    cli_mod = _wire(monkeypatch, fake)
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input",
                        lambda prompt="": pytest.fail("blocked on nobody"))
    monkeypatch.setattr(builder_mod, "summarise", lambda b: "summary")

    def run(client, settings_, *, count, workers, dry_run, on_ready):
        on_ready("P7")
        return [build(1, True)]

    monkeypatch.setattr(builder_mod, "run", run)

    assert cli_mod.cmd_build(settings, Args(watch=True)) == 0
    assert fake.started == ["P7"]


def test_the_console_is_imported_only_when_it_is_asked_for(monkeypatch,
                                                            settings):
    """rich is a heavy import, and a broken terminal must not be able to stop
    the plain commands from working."""
    import geelark_farm.ui as ui_mod

    monkeypatch.setattr(ui_mod, "run_console", lambda s: 7)

    assert cli.cmd_ui(settings, SimpleNamespace()) == 7


def gspread_refusal(message: str):
    """The exception gspread raises, built the way gspread builds it.

    `GSpreadError` is `gspread.exceptions.APIError`, and it takes the HTTP
    response rather than a string - it reads the error out of the body itself.
    """
    response = SimpleNamespace(
        text=message, status_code=403,
        json=lambda: {"error": {"code": 403, "message": message,
                                "status": "PERMISSION_DENIED"}})
    return cli.GSpreadError(response)


@pytest.mark.parametrize("raised,code,prefix", [
    (gspread_refusal("the key was revoked"), 2, "sheet:"),
    (cli.ApiError(44002, "no slots left", path="/phone/addNew",
                  trace_id="T1"), 1, "api:"),
])
def test_the_two_doors_that_arrive_from_further_away(raised, code, prefix,
                                                     monkeypatch, capsys,
                                                     tmp_path, make_settings):
    """A refusal gspread does not turn into a SheetError, and an error GeeLark
    puts in the envelope. Both used to arrive as a traceback - and the older
    test for this read the source for an `except` line rather than raising
    one, which cannot tell a caught exception from a commented-out one."""
    settings = make_settings(state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda: settings))

    def fail(settings_, args):
        raise raised

    monkeypatch.setattr(cli, "cmd_ping", fail)

    assert cli.main(["ping"]) == code
    assert f"{prefix} " in capsys.readouterr().err


def test_a_config_that_will_not_load_stops_before_any_command_runs(
        monkeypatch, capsys):
    """Every command needs settings, so a credential problem surfaces here
    rather than partway through one that has already spent money."""
    def fail():
        raise ConfigError("GEELARK_API_KEY is not set")

    monkeypatch.setattr(cli.Settings, "load", staticmethod(fail))

    assert cli.main(["ping"]) == 2
    assert "config: GEELARK_API_KEY is not set" in capsys.readouterr().err


# ------------------------------------- the last branches, and the sweep
def test_a_dump_prints_the_elements_it_found(monkeypatch, capsys, settings):
    """The whole command: `geelark dump` is read, not parsed, so an element
    that never reaches the terminal is a screen you cannot see."""
    from geelark_farm.screen import Element

    cli_mod = _wire(monkeypatch, FakePhones(already_running=True))
    monkeypatch.setattr(cli_mod.screen, "capture", lambda c, p: "<x/>")
    monkeypatch.setattr(cli_mod.screen, "parse", lambda xml: [
        Element(text="Install", desc="", cls="android.widget.Button",
                resource_id="", bounds="[0,0][9,9]", clickable=True,
                enabled=True, focused=False, password=False)])

    assert cli_mod.cmd_dump(settings, SimpleNamespace(phone="P1",
                                                      save=None)) == 0
    assert "Install" in capsys.readouterr().out


def test_a_login_names_the_artifacts_it_kept(monkeypatch, tmp_path, capsys,
                                              make_settings):
    """They are the evidence of what the screen looked like when it stopped,
    and a path nobody is told about is a file nobody opens."""
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    cli_mod = _wire_login(monkeypatch, Lifecycle(already_running=True),
                          FakeLedger(), ok=False)
    outcome = login_outcome(ok=False)
    outcome.artifacts.append("artifacts/2026-login/wrong_password.xml")
    monkeypatch.setattr(cli_mod.google_login, "sign_in",
                        lambda *a, **k: outcome)

    cli_mod.cmd_login(settings, login_args())

    assert "wrong_password.xml" in capsys.readouterr().out


def test_an_install_names_the_artifacts_it_kept(monkeypatch, tmp_path, capsys,
                                                 make_settings):
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    cli_mod = _wire_install(monkeypatch, Lifecycle(already_running=True),
                            FakeLedger(), ok=False)
    outcome = install_outcome(ok=False)
    outcome.artifacts.append("artifacts/2026-install/no_install_button.xml")
    monkeypatch.setattr(cli_mod.play_install, "install",
                        lambda *a, **k: outcome)

    cli_mod.cmd_install(settings, install_args())

    assert "no_install_button.xml" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["login", "install", "build"])
def test_watching_waits_when_there_is_somebody_to_wait_for(
        command, monkeypatch, tmp_path, make_settings):
    """The other half of the terminal guard. Skipping the wait when there is
    a terminal would make the flag do nothing at all, which is what it did
    before it was read (2026-08-23)."""
    from geelark_farm import builder as builder_mod

    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    fake = Lifecycle(already_running=True)
    waited = []
    monkeypatch.setattr("builtins.input",
                        lambda prompt="": waited.append(prompt) or "")

    if command == "build":
        cli_mod = _wire(monkeypatch, fake)
        monkeypatch.setattr(builder_mod, "summarise", lambda b: "summary")
        monkeypatch.setattr(builder_mod, "run",
                            lambda c, s, *, count, workers, dry_run, on_ready:
                            on_ready("P7") or [build(1, True)])
    elif command == "login":
        cli_mod = _wire_login(monkeypatch, fake, FakeLedger())
    else:
        cli_mod = _wire_install(monkeypatch, fake, FakeLedger())
    monkeypatch.setattr(cli_mod.sys.stdin, "isatty", lambda: True)

    handler = {"build": lambda: cli_mod.cmd_build(settings, Args(watch=True)),
               "login": lambda: cli_mod.cmd_login(settings,
                                                  login_args(watch=True)),
               "install": lambda: cli_mod.cmd_install(settings,
                                                      install_args(watch=True)),
               }[command]

    assert handler() == 0
    assert len(waited) == 1 and "Enter" in waited[0]


def test_every_command_the_parser_offers_has_something_to_run():
    """`main` guards against a subcommand with no handler by refusing at the
    point somebody types it. This is the same question asked here, where the
    answer costs nothing to find."""
    import re

    source = pathlib.Path("src/geelark_farm/cli.py").read_text(encoding="utf-8")
    declared = set(re.findall(r'add_parser\(\s*"([a-z-]+)"', source))
    dispatched = set(re.findall(r'^\s+"([a-z-]+)": cmd_\w+,', source, re.M))

    assert declared, "the sweep stopped matching how subcommands are declared"
    assert declared == dispatched, declared ^ dispatched


# --------------------------------------------- what this build calls itself
def test_the_version_names_the_commit_when_there_is_one(monkeypatch):
    """`geelark --version` said `0.1.0` for every commit ever made, which
    answers nothing about a machine you are not sitting at."""
    from geelark_farm import cli as cli_mod

    monkeypatch.setattr(cli_mod, "revision", lambda: "v0.1.0-4-gabc1234")

    assert cli_mod.version_line() == "geelark-farm 0.1.0 (v0.1.0-4-gabc1234)"


def test_the_version_is_still_a_version_without_a_checkout(monkeypatch):
    """A deployment with no `.git` gets the version alone rather than an
    empty pair of brackets."""
    from geelark_farm import cli as cli_mod

    monkeypatch.setattr(cli_mod, "revision", lambda: "")

    assert cli_mod.version_line() == "geelark-farm 0.1.0"


def test_the_log_says_which_code_wrote_it(monkeypatch, tmp_path,
                                           make_settings, capsys):
    """The log file is what answers "what happened" after the fact, and a
    file that does not say which commit produced it can only be read against
    a guess."""
    from geelark_farm import cli as cli_mod

    settings = make_settings(state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setattr(cli_mod.Settings, "load", staticmethod(lambda: settings))
    monkeypatch.setattr(cli_mod, "revision", lambda: "abc1234")
    monkeypatch.setattr(cli_mod, "cmd_ping", lambda s, a: 0)

    assert cli_mod.main(["ping"]) == 0
    assert "abc1234" in capsys.readouterr().err


# ------------------------------------------------- being stopped politely
def test_the_signal_a_container_is_stopped_with_reaches_the_cleanup():
    """Everything that puts a phone back hangs off KeyboardInterrupt, and
    Python raises that for SIGINT and nothing else. SIGTERM's default kills
    the process where it stands - no finally, no _stop_all, and every phone
    the run started left up and billing (2026-08-27)."""
    import signal

    before = signal.getsignal(signal.SIGTERM)
    try:
        cli.stop_on_sigterm()
        installed = signal.getsignal(signal.SIGTERM)

        assert callable(installed) and installed is not before
        with pytest.raises(KeyboardInterrupt):
            installed(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, before)


def test_every_command_is_stoppable_that_way(monkeypatch, tmp_path,
                                              make_settings):
    """Installed in `main`, so it covers the long ones - build, finish, and
    the loop that will run them - without each having to remember."""
    import signal

    settings = make_settings(state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda: settings))
    monkeypatch.setattr(cli, "cmd_ping", lambda s, a: 0)

    before = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        cli.main(["ping"])

        assert signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL
    finally:
        signal.signal(signal.SIGTERM, before)


def test_installing_it_off_the_main_thread_is_not_an_error():
    """Signals can only be installed from the main thread. Anywhere else this
    is not applicable, and a worker calling it must not die of it."""
    import threading

    failed = []

    def off_thread():
        try:
            cli.stop_on_sigterm()
        except Exception as exc:            # noqa: BLE001
            failed.append(exc)

    worker = threading.Thread(target=off_thread)
    worker.start()
    worker.join()

    assert not failed


def test_being_stopped_is_not_reported_as_a_crash(monkeypatch, capsys,
                                                   tmp_path, make_settings):
    """Ctrl+C and `docker stop` both arrive here, and both arrive last -
    every `finally` and every inner handler has already put its phones back.
    All that is left is how it looks, and a traceback for a routine restart
    makes a normal stop read as a crash in a log opened next week."""
    settings = make_settings(state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda: settings))

    def interrupted(settings_, args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "cmd_serve", interrupted)

    assert cli.main(["serve"]) == 130
    assert "stopped" in capsys.readouterr().err


def test_the_cleanup_still_runs_before_that(monkeypatch, tmp_path,
                                             make_settings):
    """It is caught at the top, so anything deeper has already unwound. A
    handler that caught it earlier would be one that stopped the phones from
    being put back."""
    settings = make_settings(state_dir=tmp_path, log_dir=tmp_path)
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda: settings))
    cleaned = []

    def interrupted(settings_, args):
        try:
            raise KeyboardInterrupt
        finally:
            cleaned.append("phones put back")

    monkeypatch.setattr(cli, "cmd_serve", interrupted)

    cli.main(["serve"])

    assert cleaned == ["phones put back"]


# ------------------------------------- being able to see and clear the guard
def breaker_args(clear=False):
    return SimpleNamespace(clear=clear)


def test_the_breaker_can_be_read_without_knowing_where_it_lives(
        monkeypatch, tmp_path, capsys, make_settings):
    """It was written before there was any way to look at it: a file under
    `state/` that nothing mentioned. A service that had stopped could only be
    diagnosed by somebody who already knew the layout (2026-08-28)."""
    from geelark_farm.breaker import Breaker
    from geelark_farm.builder import Build
    from geelark_farm.serve import BREAKER_FILE

    settings = make_settings(state_dir=tmp_path)
    fuse = Breaker(tmp_path / BREAKER_FILE, limit=5)
    fuse.record(Build(index=1, ok=False, status="proxy_blocked"))

    assert cli.cmd_breaker(settings, breaker_args()) == 0
    out = capsys.readouterr().out
    assert "1 failure(s) in a row of the 5" in out
    assert "proxy_blocked" in out


def test_a_tripped_breaker_says_so_and_exits_non_zero(monkeypatch, tmp_path,
                                                       capsys, make_settings):
    """So a script watching the service can tell, and so a person is told
    what to do rather than only what is wrong."""
    from geelark_farm.breaker import Breaker
    from geelark_farm.builder import Build
    from geelark_farm.serve import BREAKER_FILE

    settings = make_settings(state_dir=tmp_path)
    fuse = Breaker(tmp_path / BREAKER_FILE)
    for _ in range(fuse.limit):
        fuse.record(Build(index=1, ok=False, status="phone_never_started"))

    assert cli.cmd_breaker(settings, breaker_args()) == 1
    out = capsys.readouterr().out
    assert "in a row failed" in out
    assert "--clear" in out


def test_clearing_it_is_one_command_rather_than_a_deleted_file(
        monkeypatch, tmp_path, capsys, make_settings):
    from geelark_farm.breaker import Breaker
    from geelark_farm.builder import Build
    from geelark_farm.serve import BREAKER_FILE

    settings = make_settings(state_dir=tmp_path)
    fuse = Breaker(tmp_path / BREAKER_FILE)
    for _ in range(fuse.limit):
        fuse.record(Build(index=1, ok=False, status="phone_never_started"))
    assert fuse.reason()

    assert cli.cmd_breaker(settings, breaker_args(clear=True)) == 0
    assert "cleared" in capsys.readouterr().out
    assert Breaker(tmp_path / BREAKER_FILE).reason() == ""


def test_it_reads_the_file_the_service_actually_writes(tmp_path,
                                                        make_settings):
    """Two names for one file is a command that reports on nothing."""
    from geelark_farm import serve as serve_mod
    from geelark_farm.breaker import Breaker
    from geelark_farm.builder import Build

    settings = make_settings(state_dir=tmp_path)
    # What `serve.run` builds, written the way it writes it.
    Breaker(settings.state_dir / serve_mod.BREAKER_FILE).record(
        Build(index=1, ok=False, status="proxy_blocked"))

    count, _reasons = Breaker(
        settings.state_dir / serve_mod.BREAKER_FILE).seen()

    assert count == 1
