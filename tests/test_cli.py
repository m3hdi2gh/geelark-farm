"""Command-line behaviour that scripts and habits depend on.

Exit codes and guards, not output formatting. Each of these failed silently:
nothing raised, the command simply did the wrong thing.
"""

from __future__ import annotations

import pathlib
import time
from types import SimpleNamespace

import pytest

from geelark_farm import cli
from geelark_farm import ledger as ledger_mod
from geelark_farm.ledger import Ledger


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

    from geelark_farm import builder
    from geelark_farm.ui import BuildReporter, ReporterLogHandler

    reporter = BuildReporter()
    reporter.start(7, 1)
    handler = ReporterLogHandler(reporter)
    handler.addFilter(builder.BuildContextFilter())

    builder._context.build = 7
    record = logging.LogRecord("geelark_farm.flows.chatgpt_login", logging.INFO,
                               "f", 1, "screen: password_entry", None, None)
    # No record.row set: the filter on the handler is what must supply it.
    handler.handle(record)
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


def _wire(monkeypatch, fake, *, claimed=False):
    from geelark_farm import cli as cli_mod

    monkeypatch.setattr(cli_mod, "phones", fake)
    monkeypatch.setattr(cli_mod, "build_client", lambda s: object())
    entry = SimpleNamespace(is_claimed=claimed, is_stale=False, label="a build")
    monkeypatch.setattr(cli_mod.Ledger, "load", staticmethod(
        lambda d: SimpleNamespace(get=lambda i: entry if claimed else None,
                                  release=lambda i, note="": None)))
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
