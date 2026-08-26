"""The console's menu and the views behind it.

The menu is a list of keys in one place and a chain of `elif choice ==` in
another, and nothing has ever held the two together. Renumbering it - which is
what splitting it into `do` and `look` was - moves every key, and a key that
lost its branch is silent: you press 7 and the loop redraws as if you had
pressed nothing.
"""

from __future__ import annotations

import ast
import pathlib
import re
from types import SimpleNamespace

import pytest
from rich.console import Console, Group

from geelark_farm import builder, ui
from geelark_farm.api import ApiError, TransportError
from geelark_farm.config import ConfigError
from geelark_farm.ledger import Ledger
from geelark_farm.ui import Snapshot

SRC = pathlib.Path(ui.__file__)


def dispatch_keys() -> set[str]:
    """Every key `run_console` actually has a branch for."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    loop = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "run_console")
    found = set()
    for node in ast.walk(loop):
        if not isinstance(node, ast.Compare) or len(node.comparators) != 1:
            continue
        left, right = node.left, node.comparators[0]
        if (isinstance(left, ast.Name) and left.id == "choice"
                and isinstance(right, ast.Constant)):
            found.add(right.value)
    return found


def rendered(renderable) -> str:
    console = Console(width=100, no_color=True, record=True)
    console.print(renderable)
    return console.export_text()


def test_every_menu_key_does_something_and_nothing_undocumented_does():
    offered = {key for key, _, _ in ui.ACTIONS}
    handled = dispatch_keys()

    assert offered - handled == set(), (
        f"the menu offers {sorted(offered - handled)} with no branch - "
        f"pressing it redraws as if nothing happened")
    assert handled - offered == set(), (
        f"{sorted(handled - offered)} is handled but not on the menu")


def test_the_menu_is_split_by_what_a_choice_costs():
    """The `do` group changes something in the world; `look` only reads. It
    was a flat list of seven, two of which were the same idea twice."""
    text = rendered(ui.menu())

    assert "do" in text and "look" in text
    assert text.index("Build phones") < text.index("look")
    assert text.index("look") < text.index("Needs attention")
    # one entry to end billing, not two - `Stop everything` and `Reap` were
    # separate menu items for one intention
    assert len([line for line in text.splitlines() if "Stop" in line]) == 1


def test_a_failed_phone_is_summarised_in_the_words_the_sheet_uses():
    """This printed `b.status`, so after twenty minutes the console said
    `no_usable_gpt` and left you to open the sheet to find out what it had
    tried - which is where every "what happened?" of the last fortnight
    started."""
    build = builder.Build(
        index=1, ok=False, status="no_usable_gpt", serial="691",
        detail="the Gpt Info tab has no unused account left",
        tried=[("a@example.com", "email_code_required", "OpenAI")])

    text = rendered(ui.build_summary_panel([build]))

    assert "no_usable_gpt" not in text
    assert "the Gpt Info tab has no unused account left" in text
    # and what it tried, which the panel did not show at all
    assert "a@example.com" in text
    assert "OpenAI emailed a one-time code" in text


def test_a_stop_the_builder_raises_is_summarised_too():
    """These carry no detail of their own - the status is the whole story -
    so they are the ones that would fall back to printing the token."""
    build = builder.Build(index=1, ok=False, status="all_exits_refused",
                          serial="695")

    text = rendered(ui.build_summary_panel([build]))

    assert "all_exits_refused" not in text
    assert "every exit in the pool was refused in turn" in text


class FakePhoneLog:
    DONE, FAILED = "done", "failed"

    def __init__(self, rows):
        self._rows = rows

    def marked(self):
        return self._rows


class FakeBook:
    def __init__(self, rows):
        self.phones = FakePhoneLog(rows)


def test_the_preview_says_what_each_mark_costs_before_anything_is_deleted():
    """Deleting a phone is the one irreversible thing here, and it used to
    happen as a side effect of starting a build, before its first line of
    output."""
    book = FakeBook([
        {"sheet_row": 5, "state": "done", "serial": "684",
         "gmail": "g@example.com", "app_account": "a@example.com"},
        {"sheet_row": 9, "state": "failed", "serial": "691",
         "gmail": "h@example.com", "app_account": "b@example.com"},
    ])

    marked, lines = ui.marks_preview(book)
    text = rendered(Group(*lines))

    assert len(marked) == 2
    # both phones go, and both Gmails are spent whichever mark it was
    assert text.count("delete the phone") == 2
    assert text.count("retired, never used again") == 2
    # the app accounts are what differ, and that is the whole reason for two
    # words in the column
    assert "a@example.com - delivered with the phone" in text
    assert "b@example.com - freed, to try on another phone" in text


def test_the_preview_of_an_unmarked_sheet_asks_for_nothing():
    marked, lines = ui.marks_preview(FakeBook([]))

    assert marked == [] and lines == []


def test_the_advice_a_reason_carries_is_what_the_attention_view_shows():
    """`failures.py` has written this advice since the taxonomy landed and
    nothing ever showed it to anyone - answering "what do I do about this row"
    meant looking the reason up by hand."""
    source = SRC.read_text(encoding="utf-8")
    view = source[source.index("def attention_view"):
                  source.index("def pools_view")]

    # ...and named for whichever service judged the row, which is the tab it
    # is in: three reasons are reported by both flows and their wording says
    # who refused.
    assert "failures.verdict(reason, pool.service).advice" in view
    # the pools' own words for their four routine states are not decisions
    assert re.search(r"pool\.flagged", view)


def test_a_pools_own_status_is_explained_by_its_note_not_by_the_taxonomy():
    """`challenged` and `dead` are words a pool writes, not reasons a flow
    reports, so there is no verdict to look up. Asking for one returns the
    fallback, which tells the reader to go and edit failures.py."""
    from geelark_farm import failures

    assert "challenged" not in failures.VERDICTS
    assert "dead" not in failures.VERDICTS
    # what the fallback would have printed into the view
    assert "failures.py" in failures.verdict("challenged").advice

    source = SRC.read_text(encoding="utf-8")
    view = source[source.index("def attention_view"):
                  source.index("def pools_view")]
    # `knows`, not membership of the table: it answers the `stuck_on_*` family
    # by rule as well, so a row set aside on one of those now gets the page it
    # got stuck on where it used to fall through to its note.
    assert "failures.knows(reason)" in view
    assert failures.knows("stuck_on_password_entry")
    assert not failures.knows("dead")


def test_the_summary_does_not_claim_a_phone_has_stopped_billing():
    """`stop` posts the request and returns; GeeLark goes on listing the phone
    as running while it shuts down. So the panel said nothing was billing and
    the dashboard under it said two were RUNNING, in the same breath."""
    build = builder.Build(index=1, ok=True, status="ready", serial="684",
                          gmail="g@example.com", app_account="a@example.com")

    text = rendered(ui.build_summary_panel([build]))

    assert "nothing is billing" not in text
    assert "told to stop" in text


def test_a_report_does_not_delete_phones():
    """`geelark pools` reads. It ran the whole sync for one commit, and the
    first time it was run it deleted six phones because the State column said
    so - which is the right thing for a build to do and not for a report."""
    from geelark_farm import cli

    tree = ast.parse(pathlib.Path(cli.__file__).read_text(encoding="utf-8"))
    pools = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == "cmd_pools")
    calls = [node for node in ast.walk(pools)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "sync_sheet"]

    assert calls, "cmd_pools no longer syncs at all"
    for call in calls:
        passed = {kw.arg: kw.value for kw in call.keywords}
        assert "apply_marks" in passed, "pools must say so either way"
        assert passed["apply_marks"].value is False


def test_the_console_asks_before_carrying_out_the_state_column():
    source = SRC.read_text(encoding="utf-8")
    body = source[source.index("def apply_marks"):]

    assert "Confirm.ask" in body
    assert "apply_marks=apply" in body
    # declining the deletions still syncs the rest, which deletes nothing
    assert "sync the other tabs anyway" in body


def test_quitting_asks_nothing_about_what_is_still_running():
    """It offered to stop everything on the way out of every session. Whether
    a phone should be left up is the operator's call, made deliberately from
    the menu - being asked each time is nagging, not safety."""
    source = SRC.read_text(encoding="utf-8")
    loop = source[source.index("def run_console"):]
    quitting = loop[loop.index('if choice == "q"'):loop.index("if choice ==", 40)]

    assert "Confirm" not in quitting
    assert "stop_all" not in quitting


def test_the_console_updates_the_sheet_before_it_draws_anything():
    """The sync ran inside a build, so opening the console, looking at it and
    closing it changed nothing - and the dashboard showed whatever the sheet
    last recorded rather than what is true."""
    source = SRC.read_text(encoding="utf-8")
    loop = source[source.index("def run_console"):]

    assert loop.index("sync_on_startup") < loop.index("while True")
    startup = source[source.index("def sync_on_startup"):
                     source.index("def run_console")]
    # including the deletions: writing `done` in that column is the request,
    # and the point of writing it there is that the next run acts on it
    assert "apply_marks" not in startup
    assert "sync_sheet" in startup


def test_the_dashboard_states_what_is_running_without_pricing_it():

    text = rendered(ui.dashboard(Snapshot(phones_total=10, phones_running=2)))

    assert "2 running" in text
    assert "billing" not in text.lower()


def test_the_dashboard_says_when_rows_are_being_refused():
    """`2 gpt` beside four blank Status cells reads as the tool miscounting.
    Two of them were duplicates of earlier rows, which the pools refuse so one
    account cannot be signed into two phones at once - true, and invisible on
    the first screen anyone looks at."""

    text = rendered(ui.dashboard(Snapshot(proxies_free=16, gmails_free=18,
                                          apps_free=2, pools_broken=2)))

    assert "2 gpt" in text
    assert "2 unusable" in text
    assert "Needs attention" in text


def test_a_clean_dashboard_says_nothing_about_refused_rows():

    text = rendered(ui.dashboard(Snapshot(proxies_free=16, gmails_free=18,
                                          apps_free=2)))

    assert "unusable" not in text


def test_taking_every_proxy_is_flagged_before_the_run_starts():
    """An exit refusing a login is ordinary, and answering it costs a second
    proxy. A run given as many phones as it has proxies has none to move to -
    which is how phone 762 stopped after doing everything right."""
    source = SRC.read_text(encoding="utf-8")
    body = source[source.index("def confirm_build"):source.index("def confirm_finish")]

    assert "creating >= snap.proxies_free" in body
    assert "none to move to" in body


# --------------------------------------- naming the phone a line is working on
def test_a_finished_phone_is_named_from_the_moment_it_starts():
    """The console learned every serial from the "created ... (serial N)" log
    line, which only a build emits. So the three rows finishing existing
    phones sat unnamed for their whole run and their live links read "#1" with
    no phone in them, even though the job had carried the serial all along
    (2026-08-17)."""
    from geelark_farm.ui import BuildReporter
    reporter = BuildReporter()

    reporter.start(1, 3, serial="823", gmail="NovaEclipse738465@gmail.com")

    assert reporter.rows[1]["phone"] == "823"
    assert reporter.rows[1]["email"] == "NovaEclipse738465@gmail.com"


def test_a_new_build_still_starts_unnamed():
    """A build has no serial until GeeLark answers with one, so the column
    stays empty rather than showing something invented."""
    from geelark_farm.ui import BuildReporter
    reporter = BuildReporter()

    reporter.start(1, 3)

    assert reporter.rows[1]["phone"] == ""
    assert reporter.rows[1]["email"] == ""


def test_the_live_link_of_a_finished_phone_carries_its_serial():
    from geelark_farm.ui import LIVE_PREFIX, BuildReporter
    reporter = BuildReporter()
    reporter.start(1, 1, serial="823")

    reporter.note(1, f"{LIVE_PREFIX} https://phone.geelark.com/x")

    assert reporter.drain_links() == [(1, "823", "https://phone.geelark.com/x")]


def test_the_runner_hands_the_console_the_serial_it_already_has():
    """The seeding is only worth anything if the runner passes it, and the
    runner is where the two kinds of job are told apart."""
    import ast
    import inspect

    from geelark_farm import builder

    source = inspect.getsource(builder._run_jobs)
    call = next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "start")

    assert {k.arg for k in call.keywords} == {"serial", "gmail"}


# ------------------------------------------ what the startup sync shows
def test_the_startup_sync_holds_back_its_own_narration():
    """The sync narrates every phone deleted, every proxy's measured exit and
    every correction at INFO - forty lines of it, interleaved with a spinner
    drawing on the same terminal, above a summary that says the same things in
    the operator's words (2026-08-17)."""
    import logging

    from geelark_farm.ui import quiet_console

    root = logging.getLogger()
    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)
    root.addHandler(stream)
    try:
        with quiet_console():
            assert stream.level == logging.WARNING   # news still gets through
        assert stream.level == logging.INFO          # and it is put back
    finally:
        root.removeHandler(stream)


def test_the_file_log_keeps_everything_regardless(tmp_path):
    """Nothing is lost by muting the terminal - the file handler is the record
    a problem is diagnosed from afterwards."""
    import logging

    from geelark_farm.ui import quiet_console

    root = logging.getLogger()
    to_file = logging.FileHandler(tmp_path / "x.log", encoding="utf-8")
    to_file.setLevel(logging.DEBUG)
    root.addHandler(to_file)
    try:
        with quiet_console():
            assert to_file.level == logging.DEBUG
    finally:
        root.removeHandler(to_file)
        to_file.close()


def test_a_handler_already_quiet_is_left_where_it_was():
    import logging

    from geelark_farm.ui import quiet_console

    root = logging.getLogger()
    stream = logging.StreamHandler()
    stream.setLevel(logging.ERROR)
    root.addHandler(stream)
    try:
        with quiet_console():
            assert stream.level == logging.ERROR
        assert stream.level == logging.ERROR
    finally:
        root.removeHandler(stream)


# ------------------------------------------- what the sync could not do for you
class Pretend:
    """A pool holding whatever the test wants it to hold."""

    def __init__(self, tab, flagged=(), needs_new_ip="change ip"):
        self.tab = tab
        self.flagged = list(flagged)
        self.needs_new_ip = needs_new_ip

    def status_of(self, resource):
        return resource


class Tabs:
    def __init__(self, gmails=(), apps=(), proxies=(), waiting=0):
        self.gmails = Pretend("Gmails", gmails)
        self.apps = Pretend("Gpt Info", apps)
        self.proxies = Pretend("Proxy", proxies)
        self.phones = type("P", (), {
            "unfinished": lambda _self: list(range(waiting))})()


def rendered_needs(items):
    console = Console(width=100, no_color=True, record=True)
    real, ui.console = ui.console, console
    try:
        ui.show_needs_you(items)
    finally:
        ui.console = real
    return console.export_text().strip()


def test_a_credential_that_only_needs_labelling_is_not_raised():
    """A Gmail that will not sign in is the seller's problem, not yours: the
    status is what a refund is claimed on, and the row is out of the pool
    either way. Putting it up every run asks you to acknowledge something you
    have already dealt with, which is how a block like this stops being read."""
    book = Tabs(gmails=["captcha_shown", "password_changed", "wrong_2fa_code"],
                apps=["email_code_required"])

    assert ui.needs_you(book, {}) == []


def test_a_duplicate_is_not_raised_either():
    """Labelled and never handed out is the whole of what it needs."""
    book = Tabs(gmails=["duplicate"], apps=["duplicate"])

    assert ui.needs_you(book, {}) == []


def test_an_exit_only_the_vendor_can_change_is_raised():
    """Nothing here can give a proxy a new address - these rows carry no port
    id - so this one really does stop until a hand moves."""
    book = Tabs(proxies=["change ip", "dead", "change ip"])

    items = ui.needs_you(book, {})
    text = rendered_needs(items)

    assert [count for count, _, _ in items] == [2]     # `dead` is retested
    assert "vendor" in text


def test_the_two_judgements_the_sync_refuses_are_raised():
    """`strand_check` finds these and does not act: which way each goes is a
    judgement, and the alert has to say where the answer is."""
    outcome = {"stranded_waiting": ["a@b.com (was on phone 950)"],
               "unknown_phones": ["1099"]}

    text = rendered_needs(ui.needs_you(Tabs(), outcome))

    assert "History" in text                 # where to look for the first
    assert "never knew" in text


def test_a_clean_sheet_says_nothing_at_all():
    """It runs every startup. A block that appears when there is nothing to do
    teaches you to skip the block."""
    assert ui.needs_you(Tabs(), {}) == []
    assert rendered_needs([]) == ""


def test_a_phone_waiting_on_an_account_is_not_called_a_problem():
    """It is work you can do from the menu, and the dashboard says so already."""
    assert ui.needs_you(Tabs(waiting=6), {}) == []


def test_the_startup_shows_it_after_the_summary():
    """The wiring: without it the block exists and nobody ever sees it."""
    import inspect

    source = inspect.getsource(ui.sync_on_startup)

    assert "show_sync(outcome)" in source
    assert "show_needs_you(needs_you(" in source
    assert source.index("show_sync(outcome)") < source.index("show_needs_you")


# ------------------------------------ what a build leaves behind to read
def test_the_log_file_keeps_recording_while_the_table_draws(tmp_path):
    """The terminal is quiet during a build because rich owns it. The file is
    the thing that should be writing while it does.

    It was not. `FileHandler` is a subclass of `StreamHandler`, so silencing
    "the stream handlers" silenced the file with them, and every build run
    from the menu recorded nothing at all - a ten-minute build of phone 1079
    left the log untouched from before it started to after it failed
    (2026-08-22).
    """
    import logging
    import threading

    path = tmp_path / "run.log"
    file = logging.FileHandler(path, encoding="utf-8")
    file.setLevel(logging.DEBUG)
    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)

    root = logging.getLogger()
    was = root.level
    root.setLevel(logging.DEBUG)          # what `geelark` sets it to for real
    root.addHandler(file)
    root.addHandler(stream)
    try:
        def work():
            logging.getLogger("geelark_farm.flows.test").info(
                "entering the app account's password")

        ui._drive_live_table(_QuietTable(), logging.Filter(), work,
                             threading.Event())
    finally:
        root.removeHandler(file)
        root.removeHandler(stream)
        root.setLevel(was)
        file.close()

    assert "entering the app account's password" in path.read_text(encoding="utf-8")
    # And the terminal handler was still silenced, and put back afterwards.
    assert stream.level == logging.INFO


class _QuietTable:
    """Enough of the reporter for the drive loop to draw nothing."""

    def render(self):
        from rich.text import Text
        return Text("")

    def drain_notices(self):
        return []

    def drain_links(self):
        return []


# ------------------------------------------------ what the console looks like
def test_the_console_leaves_the_colour_of_what_it_prints_alone():
    """rich colours what it recognises unless told not to.

    A warning naming a proxy came out with the host in bright green and the
    port in cyan - four colours on one address, none of them meaning anything,
    beside a palette where green, yellow and red each mean something exact.
    """
    import io

    from rich.console import Console

    # Built the way ui.console is, and forced to a terminal so the escapes
    # would show if there were any.
    same = Console(file=io.StringIO(), force_terminal=True, width=120,
                   highlight=ui.console._highlight)
    with same.capture() as cap:
        same.print("proxy SX39 is dead: socks5://u:p@82.38.66.153:10615")

    assert "[" not in cap.get()


def test_a_warning_above_the_table_does_not_lead_with_an_import_path():
    """`geelark_farm.flows.chatgpt_login:` is eleven words in front of the
    sentence that matters, answering a question nobody at a live table is
    asking. The file log keeps it for when someone is."""
    import logging

    handler = ui.ReporterLogHandler(_QuietTable())
    record = logging.LogRecord("geelark_farm.builder", logging.WARNING, "", 0,
                               "proxy SX39 is dead", None, None)

    line = handler.format(record)

    assert line == "WARNING: proxy SX39 is dead"


def test_a_dead_proxy_is_named_once_not_twice():
    """`Resource.label` carries the whole address and so does the error after
    it, so the line printed one credential-bearing URL twice and wrapped over
    two rows to do it. The name is what finds the exit in the vendor's panel;
    what failed and why is the error's job."""
    import inspect
    import re

    from geelark_farm import builder

    flat = " ".join(inspect.getsource(builder).split())
    named = re.findall(r'"proxy %s is dead: %s", ([\w. ]+?),', flat)

    assert named, "the warning moved - update this test with it"
    # The name first. `resource.label` alone is what said it twice.
    assert all(arg.startswith("resource.name") for arg in named), named


# ------------------------------- what is stopped is what was approved
def test_the_phones_stopped_are_the_phones_the_list_showed(monkeypatch):
    """`reap` takes its verdicts back so that what it stops is what was
    displayed - its docstring says so, and the CLI passes them. This, the only
    other caller and the one that actually shows a list to a person, looked
    again instead. A run claiming or releasing a phone in the seconds someone
    spends reading the question is all it takes (2026-08-23)."""
    import geelark_farm.ui as ui

    shown = [("P1", "not in the ledger"), ("P2", "already released")]
    looks = {"n": 0}

    def reapable(client, ledger):
        looks["n"] += 1
        # A second answer, different from the first - which is the whole point.
        return shown if looks["n"] == 1 else [("P9", "arrived since")]

    reaped = {}
    monkeypatch.setattr(ui, "build_client", lambda s: object())
    monkeypatch.setattr(ui.Ledger, "load",
                        staticmethod(lambda d: SimpleNamespace(
                            get=lambda i: None)))
    monkeypatch.setattr(ui.phones, "listing", lambda c: [
        {"id": "P1", "serialNo": "801", "status": ui.phones.RUNNING},
        {"id": "P2", "serialNo": "802", "status": ui.phones.RUNNING},
    ])
    monkeypatch.setattr(ui.phones, "reapable", reapable)
    monkeypatch.setattr(ui.phones, "reap",
                        lambda c, book, verdicts=None, dry_run=False:
                        reaped.setdefault("got", verdicts) or len(verdicts or []))
    monkeypatch.setattr(ui.Prompt, "ask", staticmethod(lambda *a, **k: "u"))

    ui.stop_phones(SimpleNamespace(state_dir="/nowhere"))

    assert reaped["got"] == shown


def test_stopping_everything_acts_on_the_list_it_printed(monkeypatch):
    """Same rule for the other branch: `stop_all` listed a second time."""
    import geelark_farm.ui as ui

    monkeypatch.setattr(ui, "build_client", lambda s: object())
    monkeypatch.setattr(ui.Ledger, "load",
                        staticmethod(lambda d: SimpleNamespace(
                            get=lambda i: None)))
    monkeypatch.setattr(ui.phones, "reapable", lambda c, book: [])
    monkeypatch.setattr(ui.phones, "listing", lambda c: [
        {"id": "P1", "serialNo": "801", "status": ui.phones.RUNNING},
    ])
    monkeypatch.setattr(ui.Prompt, "ask", staticmethod(lambda *a, **k: "a"))

    asked = {}
    monkeypatch.setattr(ui, "stop_all",
                        lambda s, targets=None: asked.setdefault("t", targets))

    ui.stop_phones(SimpleNamespace(state_dir="/nowhere"))

    assert asked["t"] == ["P1"]


# ------------------------------------------- text from a log line is text
def test_a_stray_closing_tag_does_not_bring_the_table_down():
    """`state` is the last thing a flow logged, and rich reads square brackets
    as markup. A stray `[/]` raises MarkupError from inside the draw loop - on
    the thread holding the display, while the workers carry on."""
    reporter = ui.BuildReporter(total=1)
    reporter.start(1, 1)
    reporter.note(1, "on screen: [/] and other things")

    rendered = _plain(reporter.render())

    assert "[/]" in rendered


def test_a_style_name_in_a_message_is_not_eaten():
    reporter = ui.BuildReporter(total=1)
    reporter.start(1, 1)
    reporter.note(1, "the app said [dim] to nobody")

    assert "[dim]" in _plain(reporter.render())


def _plain(renderable) -> str:
    import io

    from rich.console import Console
    buffer = io.StringIO()
    Console(file=buffer, width=200, highlight=False).print(renderable)
    return buffer.getvalue()


# ------------------------------------------------ the session stays open
def test_every_failure_the_cli_names_is_named_here_too():
    """A revoked key raises GSpreadError rather than SheetError, stopping a
    phone raises PhoneError, and anything reaching the device raises
    ShellError. Each of them ended the session with a traceback, and a console
    is something you leave open for hours."""
    import inspect

    source = inspect.getsource(ui.run_console)

    for name in ("GSpreadError", "ShellError", "PhoneError", "ProxyError",
                 "AccountError", "ConfigError", "ApiError", "TransportError",
                 "SheetError"):
        assert name in source, f"{name} still ends the session"


def test_a_bug_is_reported_without_closing_the_console():
    """Not a failure this tool has a name for, which makes it one of its own.
    Losing what is on the screen helps nobody."""
    import inspect

    source = inspect.getsource(ui.run_console)

    assert "except Exception" in source
    assert "log.exception" in source
    assert "except EOFError" in source


# ======================== the console opens whatever the sheet turns out to be
def test_a_missing_key_file_does_not_stop_the_console_opening(monkeypatch,
                                                              capsys):
    """`Book.open` asks `require_sheets` first, which raises ConfigError when
    the service-account file is not where the settings say. It was not in the
    list, and this call is made before the menu loop starts - so the console
    died with a traceback rather than opening, and the one command that
    explains the problem is the one it could not offer (2026-08-23)."""
    from geelark_farm.config import ConfigError

    monkeypatch.setattr(ui, "build_client", lambda s: object())
    monkeypatch.setattr(ui.Book, "open", staticmethod(
        lambda s: (_ for _ in ()).throw(ConfigError("no service-account file"))))

    ui.sync_on_startup(SimpleNamespace(sheet_id="abc", state_dir="/nowhere",
                                       artifact_dir="/nowhere",
                                       build_budget_seconds=3600))

    printed = capsys.readouterr().out
    assert "no service-account file" in printed
    assert "geelark verify" in printed


def test_a_failure_nobody_named_still_leaves_the_console_open(monkeypatch,
                                                              capsys):
    """"Never fatal" is the whole contract, and naming the failures it
    survives is a list that will always be one short."""
    monkeypatch.setattr(ui, "build_client", lambda s: object())
    monkeypatch.setattr(ui.Book, "open", staticmethod(
        lambda s: (_ for _ in ()).throw(RuntimeError("something new"))))

    ui.sync_on_startup(SimpleNamespace(sheet_id="abc", state_dir="/nowhere",
                                       artifact_dir="/nowhere",
                                       build_budget_seconds=3600))

    assert "something new" in capsys.readouterr().out


def test_the_dashboard_reports_a_sheet_failure_rather_than_raising():
    """Its docstring says failures are reported and never raised, and
    `SheetError` alone did not mean it."""
    import inspect

    source = inspect.getsource(ui.take_snapshot)

    assert "except Exception" in source


# ======================= the build's log format does not outlive the build
def test_the_console_format_is_put_back_after_a_build():
    """It was replaced and never restored, so every console line for the rest
    of a session carried `[build -]` - a label for a build that is not
    running (2026-08-23)."""
    import io
    import logging

    from geelark_farm import builder as builder_mod

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    was = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    log = logging.getLogger("geelark_farm.pools")
    try:
        log.warning("before")
        restore = builder_mod.install_build_logging()
        log.warning("during")
        restore()
        log.warning("after")
    finally:
        root.removeHandler(handler)
        root.setLevel(was)

    before, during, after = buffer.getvalue().splitlines()
    assert "[build" in during
    assert "[build" not in before
    assert "[build" not in after


def test_a_run_puts_the_format_back_however_it_ends():
    """Including when a job raises, which is when a session is most likely to
    carry on afterwards."""
    import inspect

    from geelark_farm import builder as builder_mod

    source = inspect.getsource(builder_mod._run_jobs)

    assert "restore_logging = install_build_logging()" in source
    assert "finally:" in source
    assert "restore_logging()" in source


def test_a_phone_with_no_serial_is_shown_as_unknown_not_as_none():
    """The third place `.get(key, default)` was read as covering a present
    null. Here `str()` keeps it from raising and prints the word None."""
    import inspect

    source = inspect.getsource(ui.phones_table)

    assert 'item.get("serialNo") or "?"' in source


# ------------------------------------- every sync that acts frees what is stuck
def _sync_calls(module, function: str):
    """The `sync_sheet(...)` calls inside one function, as keyword names."""
    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    owner = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == function)
    def named(call):
        # `builder.sync_sheet(...)` from ui.py, bare `sync_sheet(...)` inside
        # builder.py itself - the same call, two node shapes.
        return getattr(call.func, "attr", None) or getattr(call.func, "id", None)

    return [{kw.arg for kw in node.keywords}
            for node in ast.walk(owner)
            if isinstance(node, ast.Call) and named(node) == "sync_sheet"]


def test_every_sync_that_acts_puts_back_what_a_dead_run_was_holding():
    """`stale_claim_seconds` is what frees a credential a killed run left
    claimed, and it is off unless the caller asks for it.

    Three of the four callers that act asked. `Update the sheet` did not - so
    the action named "make all four tabs agree with the panel" was the only
    one that left rows stuck as `in_use`, while the dashboard went on counting
    them and telling the operator to free them by hand (2026-08-25).

    Named per function rather than swept, because the fourth caller -
    `geelark pools` - is a report and must NOT act. That one is pinned by
    test_a_report_does_not_delete_phones.
    """
    from geelark_farm import builder as builder_mod
    from geelark_farm import ui as ui

    acting = [(ui, "sync_on_startup"), (ui, "apply_marks"),
              (builder_mod, "run"), (builder_mod, "finish_run")]

    for module, function in acting:
        calls = _sync_calls(module, function)
        assert calls, f"{function} no longer syncs at all"
        for passed in calls:
            assert "stale_claim_seconds" in passed, (
                f"{module.__name__}.{function} syncs without freeing the rows "
                f"a dead run left claimed")


def test_updating_the_sheet_does_everything_opening_the_console_does():
    """The two run the same sync, so the menu action cannot quietly do less.
    It did: no artifact prune and no claim release, both of which the startup
    had."""
    from geelark_farm import ui as ui

    # `on_step` drives the startup spinner and says nothing about what the
    # sync does, so it is not part of the comparison.
    presentation = {"on_step"}
    startup = _sync_calls(ui, "sync_on_startup")[0] - presentation
    menu = _sync_calls(ui, "apply_marks")[0] - presentation

    missing = startup - menu
    assert not missing, f"`Update the sheet` skips {sorted(missing)}"


# =====================================================================
# The console's decisions (2026-08-26). Most of this module draws
# tables, and a wrong colour is visible. These are the parts where an
# answer becomes a run: how many phones, how many at once, and what
# the plan number is when GeeLark will not say.
# =====================================================================

def answers(monkeypatch, *replies, start=True):
    """Feed the prompts a script, and record the defaults they offered.

    Both kinds: `confirm_build` asks for two numbers and then for a yes, and
    an unpatched one reads stdin - which under pytest is an OSError rather
    than the answer the test meant to give.
    """
    offered: list[int] = []
    queue = list(replies)

    def ask(prompt, default=None, **kwargs):
        offered.append(default)
        return queue.pop(0) if queue else default

    monkeypatch.setattr(ui.IntPrompt, "ask", staticmethod(ask))
    monkeypatch.setattr(ui.Confirm, "ask",
                        staticmethod(lambda *a, **k: start))
    return offered


def snapshot(**over):
    """A dashboard reading, with the fields these tests care about named.

    `has_pools` is not one of them: it is a property, and a negative
    `proxies_free` is the sentinel for "the resource tabs are not in this
    sheet at all".
    """
    base = dict(phones_total=0, phones_running=0, phones_unfinished=0,
                proxies_free=0, gmails_free=0, apps_free=0, pools_stuck=0,
                slots_free=30, slots_total=30, parallels=0)
    base.update(over)
    return ui.Snapshot(**base)


NO_TABS = -1


# --------------------------------------------------- how many phones, and why
def test_the_default_is_what_the_stock_can_actually_reach(monkeypatch,
                                                           make_settings):
    """The arithmetic is `builder.Capacity`'s - the domain's, not the
    console's. This offers it as the default so the ordinary answer is one
    keypress."""
    offered = answers(monkeypatch)
    snap = snapshot(proxies_free=5, gmails_free=5, apps_free=3, slots_free=30)

    ui.confirm_build(make_settings(), snap)

    assert offered[0] == 3, "the app accounts are the limit, and the default"


def test_asking_for_none_is_read_as_one(monkeypatch, make_settings):
    """Zero is not an answer to "how many phones"; it is a typo or an empty
    line, and a run of nothing looks identical to a run that failed."""
    answers(monkeypatch, 0, 1)

    options = ui.confirm_build(make_settings(),
                                   snapshot(proxies_free=2, gmails_free=2,
                                            apps_free=2))

    assert options is None or options["count"] >= 1


def test_workers_never_exceed_the_number_of_phones(monkeypatch,
                                                    make_settings):
    """Four workers for two phones is two threads that start, find nothing to
    do and exit - and a live table with two empty rows in it."""
    answers(monkeypatch, 2, 9)

    options = ui.confirm_build(make_settings(),
                                   snapshot(proxies_free=9, gmails_free=9,
                                            apps_free=9))

    assert options["workers"] <= options["count"] == 2


def test_a_sheet_with_no_resource_tabs_is_refused_before_anything_is_asked(
        monkeypatch, make_settings):
    """`build` has nothing to read, and asking "how many phones" first would
    take an answer it cannot act on."""
    asked = answers(monkeypatch, 5)

    assert ui.confirm_build(make_settings(),
                                snapshot(proxies_free=NO_TABS)) is None
    assert asked == [], "it asked before it knew there was anything to build"


# ------------------------------------------------------- the plan, cached
def test_the_plan_is_asked_for_at_most_once_a_minute(monkeypatch):
    """Its own limiter is separate from the 200/min one in api.py, so the
    shared rate limiter cannot see it coming."""
    monkeypatch.setattr(ui, "_plan_cache", None)
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(ui.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ui.phones, "plan",
                        lambda c: calls.append(1) or {"profiles": 30})

    assert ui.cached_plan(None)["profiles"] == 30
    ui.cached_plan(None)

    assert len(calls) == 1, "it asked twice inside the window"

    clock[0] += ui._PLAN_TTL + 1
    ui.cached_plan(None)

    assert len(calls) == 2, "the window never expired"


def test_a_rate_limited_plan_falls_back_to_the_last_answer(monkeypatch):
    """The last known answer is better than an error message where a number
    should be - these limits barely change."""
    monkeypatch.setattr(ui, "_plan_cache", None)
    clock = [1000.0]
    monkeypatch.setattr(ui.time, "monotonic", lambda: clock[0])

    answers_from_geelark = [{"profiles": 30}]

    def plan(client):
        if answers_from_geelark:
            return answers_from_geelark.pop()
        raise ApiError(ui.PLAN_RATE_LIMITED, "too many requests",
                       path="/v1/pay/plan/info", trace_id="T")

    monkeypatch.setattr(ui.phones, "plan", plan)

    assert ui.cached_plan(None)["profiles"] == 30

    clock[0] += ui._PLAN_TTL + 1

    assert ui.cached_plan(None)["profiles"] == 30, "it lost the number"


def test_a_rate_limit_with_nothing_cached_is_still_an_error(monkeypatch):
    """Nothing to fall back on. Swallowing it would draw a dashboard whose
    plan line is silently made up."""
    monkeypatch.setattr(ui, "_plan_cache", None)
    monkeypatch.setattr(ui.time, "monotonic", lambda: 1000.0)

    def plan(client):
        raise ApiError(ui.PLAN_RATE_LIMITED, "too many requests",
                       path="/v1/pay/plan/info", trace_id="T")

    monkeypatch.setattr(ui.phones, "plan", plan)

    with pytest.raises(ApiError):
        ui.cached_plan(None)


def test_any_other_api_error_is_not_hidden_behind_a_stale_number(monkeypatch):
    """A revoked key is not a rate limit, and reporting last minute's plan for
    it says the account is fine when it is not."""
    monkeypatch.setattr(ui, "_plan_cache", (1000.0, {"profiles": 30}))
    monkeypatch.setattr(ui.time, "monotonic", lambda: 9_999.0)

    def plan(client):
        raise ApiError(40003, "signature rejected",
                       path="/v1/pay/plan/info", trace_id="T")

    monkeypatch.setattr(ui.phones, "plan", plan)

    with pytest.raises(ApiError):
        ui.cached_plan(None)


# ---------------------------------------- reading the world for the dashboard
class World:
    """Every source the dashboard reads, each able to fail on its own."""

    def __init__(self, *, items=None, plan=None, book=None,
                 items_boom=None, plan_boom=None, book_boom=None):
        self.items = items or []
        self.plan = plan or {}
        self.book = book
        self.items_boom = items_boom
        self.plan_boom = plan_boom
        self.book_boom = book_boom

    def install(self, monkeypatch):
        def listing(client):
            if self.items_boom:
                raise self.items_boom
            return self.items

        def plan(client):
            if self.plan_boom:
                raise self.plan_boom
            return self.plan

        def open_book(settings):
            if self.book_boom:
                raise self.book_boom
            return self.book

        monkeypatch.setattr(ui, "build_client", lambda s: None)
        monkeypatch.setattr(ui.phones, "listing", listing)
        monkeypatch.setattr(ui, "cached_plan", plan)
        monkeypatch.setattr(ui.Book, "open", staticmethod(open_book))
        return self


def a_book(*, proxies=0, gmails=0, apps=0, stuck=0, unfinished=0):
    def pool(free, held):
        return type("Pool", (), {
            "available": [object()] * free,
            "stuck": [object()] * held,
            "broken": [],
        })()

    return type("Book", (), {
        "proxies": pool(proxies, stuck),
        "gmails": pool(gmails, 0),
        "apps": pool(apps, 0),
        "phones": type("P", (), {
            "unfinished": staticmethod(lambda: [object()] * unfinished)})(),
    })()


def test_the_dashboard_reads_every_source_it_shows(monkeypatch, make_settings):
    World(items=[{"status": ui.phones.RUNNING}, {"status": ui.phones.STOPPED}],
          plan={"profiles": 30, "availableProfiles": 7, "parallels": 3},
          book=a_book(proxies=4, gmails=5, apps=2, stuck=1,
                      unfinished=6)).install(monkeypatch)

    snap = ui.take_snapshot(make_settings(sheet_id="abc"))

    assert (snap.phones_total, snap.phones_running) == (2, 1)
    assert (snap.slots_total, snap.slots_free, snap.parallels) == (30, 7, 3)
    assert (snap.proxies_free, snap.gmails_free, snap.apps_free) == (4, 5, 2)
    assert snap.pools_stuck == 1
    assert snap.phones_unfinished == 6
    assert snap.error == ""


def test_a_phone_that_is_starting_counts_as_running(monkeypatch,
                                                     make_settings):
    """It is billing, which is what the number is for."""
    World(items=[{"status": ui.phones.STARTING}]).install(monkeypatch)

    assert ui.take_snapshot(make_settings()).phones_running == 1


def test_one_source_failing_does_not_blank_the_others(monkeypatch,
                                                       make_settings):
    """Each source is caught on its own, so a rate-limited plan lookup cannot
    blank the phone count that was already read."""
    World(items=[{"status": ui.phones.RUNNING}],
          plan_boom=TransportError("plan is unreachable")).install(monkeypatch)

    snap = ui.take_snapshot(make_settings())

    assert snap.phones_total == 1, "the count it already had was thrown away"
    assert "unreachable" in snap.error


def test_the_first_failure_is_the_one_reported(monkeypatch, make_settings):
    """One line of room on the dashboard, and the earliest failure is the one
    the others are most likely downstream of."""
    World(items_boom=TransportError("no route to host"),
          plan_boom=TransportError("plan is unreachable")).install(monkeypatch)

    assert "no route" in ui.take_snapshot(make_settings()).error


def test_a_sheet_that_cannot_be_opened_is_reported_not_raised(monkeypatch,
                                                               make_settings):
    """`SheetError` alone did not mean it: a revoked key raises GSpreadError
    and a missing key file raises ConfigError, and the dashboard is the wrong
    place to learn either by traceback."""
    for failure in (ConfigError("no service account file"),
                    RuntimeError("APIError: [403] revoked")):
        World(book_boom=failure).install(monkeypatch)

        snap = ui.take_snapshot(make_settings(sheet_id="abc"))

        assert snap.error, f"{failure!r} produced no message"


def test_with_no_sheet_configured_the_pools_are_simply_not_read(monkeypatch,
                                                                make_settings):
    """A tool used without a sheet is a supported way to run it, not an
    error."""
    World(book_boom=AssertionError("the book was opened")).install(monkeypatch)

    snap = ui.take_snapshot(make_settings(sheet_id=""))

    assert snap.error == ""


# ------------------------------------------------------- stopping them
def test_the_phones_stopped_are_the_phones_that_were_approved(monkeypatch,
                                                               make_settings,
                                                               tmp_path):
    """Looking again would be a second answer to the same question, and the
    phones stopped would not be the phones shown - the reason `phones.reap`
    takes its verdicts the same way."""
    stopped: list[str] = []
    monkeypatch.setattr(ui, "build_client", lambda s: None)
    monkeypatch.setattr(ui.phones, "listing",
                        lambda c: [{"id": "LATER", "status": ui.phones.RUNNING}])
    monkeypatch.setattr(ui.phones, "stop", lambda c, pid: stopped.append(pid))

    ui.stop_all(make_settings(state_dir=tmp_path), targets=["P1", "P2"])

    assert stopped == ["P1", "P2"], "it looked again instead of using the list"


def test_stopping_everything_releases_each_phone_in_the_ledger(monkeypatch,
                                                               make_settings,
                                                               tmp_path):
    """Otherwise reap goes on seeing them as claimed, and the next sync
    reports phones nobody is holding as ones somebody is."""
    monkeypatch.setattr(ui, "build_client", lambda s: None)
    monkeypatch.setattr(ui.phones, "stop", lambda c, pid: None)
    settings = make_settings(state_dir=tmp_path)

    ledger = Ledger.load(tmp_path)
    ledger.record("P1")
    ledger.claim("P1")

    ui.stop_all(settings, targets=["P1"])

    assert Ledger.load(tmp_path).get("P1").released_at is not None


def test_nothing_running_says_so_rather_than_printing_an_empty_list(
        monkeypatch, make_settings, tmp_path, capsys):
    monkeypatch.setattr(ui, "build_client", lambda s: None)
    monkeypatch.setattr(ui.phones, "listing", lambda c: [])

    ui.stop_all(make_settings(state_dir=tmp_path))

    assert "nothing is running" in rendered_out(capsys)


def rendered_out(capsys) -> str:
    return capsys.readouterr().out
