"""Command-line behaviour that scripts and habits depend on.

Exit codes and guards, not output formatting. Each of these failed silently:
nothing raised, the command simply did the wrong thing.
"""

from __future__ import annotations

import time

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
