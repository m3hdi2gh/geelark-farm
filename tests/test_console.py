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

from rich.console import Console, Group

from geelark_farm import builder, ui

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
        tried=[("a@example.com", "email_code_required")])

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

    assert "failures.verdict(reason).advice" in view
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
    from geelark_farm.ui import Snapshot

    text = rendered(ui.dashboard(Snapshot(phones_total=10, phones_running=2)))

    assert "2 running" in text
    assert "billing" not in text.lower()


def test_the_dashboard_says_when_rows_are_being_refused():
    """`2 gpt` beside four blank Status cells reads as the tool miscounting.
    Two of them were duplicates of earlier rows, which the pools refuse so one
    account cannot be signed into two phones at once - true, and invisible on
    the first screen anyone looks at."""
    from geelark_farm.ui import Snapshot

    text = rendered(ui.dashboard(Snapshot(proxies_free=16, gmails_free=18,
                                          apps_free=2, pools_broken=2)))

    assert "2 gpt" in text
    assert "2 unusable" in text
    assert "Needs attention" in text


def test_a_clean_dashboard_says_nothing_about_refused_rows():
    from geelark_farm.ui import Snapshot

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


# --------------------------------------- answering a code from the console
class FakeLive:
    def __init__(self):
        self.stops, self.starts = 0, 0

    def stop(self):
        self.stops += 1

    def start(self):
        self.starts += 1


def waiting_source(address="a@b.com", timeout=5):
    import threading
    import time

    from geelark_farm import codes
    source = codes.Pending()
    worker = threading.Thread(
        target=lambda: source.code_for(address, since=time.time(),
                                       timeout=timeout),
        daemon=True)
    worker.start()
    deadline = time.time() + 2
    while not source.waiting() and time.time() < deadline:
        time.sleep(0.01)
    return source, worker


def test_the_prompt_stops_the_live_table_while_it_reads(monkeypatch):
    """Reading a line while Live is drawing puts the typing underneath the
    table, four times a second."""
    from geelark_farm import ui
    monkeypatch.setattr(ui.Prompt, "ask", staticmethod(lambda *a, **k: "481920"))
    source, worker = waiting_source()
    live = FakeLive()

    ui.ask_for_codes(live, source)

    assert live.stops == 1 and live.starts == 1
    worker.join(2)
    assert source.waiting() == []


def test_an_empty_answer_gives_up_on_that_build(monkeypatch):
    """Pressing Enter alone is how you say "nobody is going to answer this" -
    the account is set aside, not marked."""
    from geelark_farm import ui
    monkeypatch.setattr(ui.Prompt, "ask", staticmethod(lambda *a, **k: ""))
    source, worker = waiting_source()

    ui.ask_for_codes(FakeLive(), source)

    worker.join(2)
    assert not worker.is_alive()
    assert source.waiting() == []


def test_a_mistyped_code_is_asked_for_again(monkeypatch):
    from geelark_farm import ui
    answers = iter(["48192", "481920"])
    monkeypatch.setattr(ui.Prompt, "ask",
                        staticmethod(lambda *a, **k: next(answers)))
    source, worker = waiting_source()

    ui.ask_for_codes(FakeLive(), source)

    worker.join(2)
    assert source.waiting() == []
    assert next(answers, "used both") == "used both"


def test_nothing_waiting_means_nothing_is_asked_and_the_table_keeps_drawing():
    """The common case, four times a second - it must not touch Live."""
    from geelark_farm import codes, ui
    live = FakeLive()

    ui.ask_for_codes(live, codes.Pending())

    assert live.stops == 0 and live.starts == 0


def test_the_live_table_is_restarted_even_if_the_prompt_is_interrupted(
        monkeypatch):
    """Ctrl+C at the prompt must not leave the display stopped for the rest of
    the batch."""
    from geelark_farm import ui

    def interrupted(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(ui.Prompt, "ask", staticmethod(interrupted))
    source, worker = waiting_source()
    live = FakeLive()

    ui.ask_for_codes(live, source)

    assert live.starts == live.stops == 1
    worker.join(2)
    assert not worker.is_alive()


def test_the_batch_hands_its_builds_a_source_a_person_can_answer():
    """The wiring: without it the flow gets NoSource and every account with no
    authenticator is set aside exactly as before."""
    import ast
    import inspect

    from geelark_farm import ui

    for name in ("build_with_live_table", "finish_with_live_table"):
        source = inspect.getsource(getattr(ui, name))
        tree = ast.parse(source.lstrip())
        assert "codes.Pending()" in source, name
        passed = {k.arg for node in ast.walk(tree)
                  if isinstance(node, ast.Call) for k in node.keywords}
        assert "codes_source" in passed, name
