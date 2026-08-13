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
        {"sheet_row": 5, "state": "done", "phone_id": "P1", "serial": "684",
         "gmail": "g@example.com", "app_account": "a@example.com"},
        {"sheet_row": 9, "state": "failed", "phone_id": "P2", "serial": "691",
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
    assert "reason in failures.VERDICTS" in view


def test_the_summary_does_not_claim_a_phone_has_stopped_billing():
    """`stop` posts the request and returns; GeeLark goes on listing the phone
    as running while it shuts down. So the panel said nothing was billing and
    the dashboard under it said two were RUNNING, in the same breath."""
    build = builder.Build(index=1, ok=True, status="ready", serial="684",
                          gmail="g@example.com", app_account="a@example.com")

    text = rendered(ui.build_summary_panel([build]))

    assert "nothing is billing" not in text
    assert "told to stop" in text
