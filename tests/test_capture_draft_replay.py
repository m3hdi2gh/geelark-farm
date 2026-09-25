"""The three tools a new flow is written with: record, draft, replay.

The loop they make is the whole claim: record one walk on a phone,
correct the flow fifty times against that recording for nothing, then
one live run to confirm. So the tests walk the loop end to end - a
recording written by `capture`, a flow drafted from it by `draft`, and
that flow driven over the same recording by `replay` - with no phone
anywhere.

The screens are real Google pages, from the suite's own fixtures.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from geelark_farm import capture, draft, replay
from geelark_farm.flows import router

FIXTURES = Path(__file__).parent / "fixtures"


def walk_of(tmp_path: Path, names: list[str]) -> Path:
    """A recording holding these fixtures, in this order."""
    where = tmp_path / "walk"
    where.mkdir(parents=True, exist_ok=True)
    steps = []
    for i, name in enumerate(names, start=1):
        out = f"{i:03d}.xml"
        (where / out).write_text((FIXTURES / name).read_text(encoding="utf-8"),
                                 encoding="utf-8")
        steps.append({"file": out, "at": float(i), "elements": 0, "labels": []})
    import json

    (where / capture.WALK).write_text(
        json.dumps({"phone": "P", "seconds": 9.0, "addresses_removed": 0,
                    "steps": steps}), encoding="utf-8")
    return where


# ------------------------------------------------------------- recording
class Phone:
    """A client that answers a list of screens, one dump at a time."""

    def __init__(self, pages: list[str]):
        self.pages = list(pages)
        self.at = 0
        self.calls: list[str] = []

    def data(self, path, payload=None, **kwargs):
        self.calls.append(str((payload or {}).get("cmd") or path))
        cmd = str((payload or {}).get("cmd") or "")
        if cmd.startswith("cat "):
            page = self.pages[min(self.at, len(self.pages) - 1)]
            self.at += 1
            return {"status": True, "output": page}
        return {"status": True, "output": ""}

    def post(self, path, payload=None, **kwargs):
        return {"code": 0, "data": self.data(path, payload, **kwargs)}


def test_only_a_screen_that_changed_is_kept(tmp_path, monkeypatch):
    """Polling for ten minutes is three hundred dumps of the same page,
    and a folder like that is evidence of nothing. A page is the same
    page when its labels are - not its XML, which differs by a caret
    between two dumps of a still screen."""
    same = (FIXTURES / "google-email-entry.xml").read_text(encoding="utf-8")
    other = (FIXTURES / "google-password-changed.xml").read_text(encoding="utf-8")
    # The same page four times, with a caret moved in one of them.
    client = Phone([same, same.replace('index="0"', 'index="0" '), same, other])
    monkeypatch.setattr(capture.time, "sleep", lambda s: None)

    where = capture.watch(client, "P", tmp_path / "w", every=0, seconds=0.5)

    kept = capture.read(where)
    assert len(kept["steps"]) == 2, "the still page was kept more than once"
    assert [s["file"] for s in kept["steps"]] == ["001.xml", "002.xml"]
    assert len(capture.screens(where)) == 2


def test_an_address_never_lands_on_disk(tmp_path, monkeypatch):
    """A walk through a sign-in captures whatever was typed into the
    email box. The farm's own artifacts carry real addresses for exactly
    this reason (found while porting them, 2026-09-22)."""
    page = ('<hierarchy><node text="real.person@gmail.com" bounds="[0,0][9,9]"/>'
            '<node text="Signed in as real.person@gmail.com" bounds="[0,0][9,9]"/>'
            '<node text="other@yahoo.com" bounds="[0,0][9,9]"/></hierarchy>')
    monkeypatch.setattr(capture.time, "sleep", lambda s: None)

    where = capture.watch(Phone([page]), "P", tmp_path / "w", every=0,
                          seconds=0.3)

    body = (where / "001.xml").read_text(encoding="utf-8")
    assert "real.person@gmail.com" not in body
    assert "other@yahoo.com" not in body
    # The same address keeps the same stand-in, so a page about two
    # accounts still reads as a page about two accounts.
    assert body.count("user1@example.com") == 2 and "user2@example.com" in body
    assert capture.read(where)["addresses_removed"] == 2


def test_a_folder_of_screens_with_no_walk_is_read_too(tmp_path):
    """A build's archived directory is the same shape, and it is the
    farm's whole history of them."""
    where = tmp_path / "artifacts"
    where.mkdir()
    for name in ("120030-email_entry.xml", "120102-password_entry.xml"):
        (where / name).write_text("<hierarchy/>", encoding="utf-8")
    assert len(capture.screens(where)) == 2


# ---------------------------------------------------------------- draft
def test_a_screens_matcher_is_what_only_that_screen_says(tmp_path):
    """The method, and the whole value of this tool: a person reaches
    for the heading, and the heading is often on three pages."""
    where = walk_of(tmp_path, ["google-email-entry.xml",
                               "google-password-changed.xml",
                               "google-2fa-code-entry.xml"])
    pages = capture.pages(where)
    unique = draft.only_here(pages)

    assert len(unique) == 3
    for i, labels in enumerate(unique):
        for label in labels:
            for j, other in enumerate(pages):
                if j != i:
                    assert label not in {e.label for e in other}, \
                        f"{label!r} is on screen {j + 1} too"
    # And every screen of three different Google pages has something.
    assert all(any(draft._useful(w) for w in labels) for labels in unique)


def test_the_draft_is_python_that_runs(tmp_path):
    where = walk_of(tmp_path, ["google-email-entry.xml",
                               "google-password-changed.xml"])
    source = draft.draft(where)

    assert "SCREENS = [" in source and "Screen(" in source
    assert "ctx.has(" in source
    # It compiles, and it refuses to pretend it knows the acts.
    compile(source, "<draft>", "exec")
    assert source.count("NotImplementedError") == 2, \
        "a draft must not invent what to do on a page"


def test_two_pages_that_read_the_same_are_named_rather_than_invented(tmp_path):
    """A walk that passes the same page twice has nothing unique to
    either. That is a real thing about the walk, and the draft says so
    instead of writing a matcher that cannot work."""
    where = walk_of(tmp_path, ["google-email-entry.xml",
                               "google-2fa-code-entry.xml",
                               "google-email-entry.xml"])
    source = draft.draft(where)

    assert "Nothing on this screen is only on this screen" in source
    assert "screen(s) had no words of their own: 1, 3" in source


def test_a_matcher_is_never_a_clock_a_code_or_a_paragraph():
    assert not draft._useful("12:45")
    assert not draft._useful("418902")
    assert not draft._useful("OK")
    assert not draft._useful("Back")
    assert not draft._useful("x" * 80)
    assert draft._useful("Enter your password")
    # Among what is usable, the one without digits and the shorter wins.
    best = sorted(["Welcome back, Ali", "Enter your password"], key=draft._score)
    assert best[0] == "Enter your password"


def test_a_screens_name_comes_from_its_own_best_words():
    assert draft.slug("Enter your password") == "enter_your_password"
    assert draft.slug("Couldn't sign you in") == "couldn_t_sign_you_in" or \
        draft.slug("Couldn't sign you in").startswith("couldn")
    assert draft.slug("!!!") == "page"


# --------------------------------------------------------------- replay
def test_a_flow_walks_a_recording_with_no_phone_at_all(tmp_path):
    """The claim this tool exists for. Three real Google pages, a flow
    of three screens, and nothing that touches a device."""
    where = walk_of(tmp_path, ["google-email-entry.xml",
                               "google-password-changed.xml"])
    seen = []
    screens = [
        router.Screen("email", lambda c: c.has("use your google account"),
                      lambda c: seen.append("email") or None),
        router.Screen("changed", lambda c: c.has("password was changed"),
                      lambda c: router.Outcome("fatal", "password_changed",
                                               "Google said so")),
    ]
    outcome = replay.over(where, screens, budget_seconds=30)

    assert outcome.reason == "password_changed"
    assert outcome.trail == ["email", "changed"]
    assert seen == ["email"]


def test_a_replay_refuses_to_touch_the_network(tmp_path):
    """A flow that reaches past the screen - a screenshot, an install,
    the solver - must fail loudly here, not quietly hit GeeLark."""
    client = replay.Client()
    client.data("/v1/shell/execute", {"cmd": "input tap 10 20"})
    assert client.commands == ["input tap 10 20"]
    with pytest.raises(replay.ReplayError, match="cannot answer"):
        client.data("/v1/phone/start", {"ids": ["x"]})


def test_a_recording_that_runs_out_says_so_rather_than_blaming_the_flow(
        tmp_path):
    """It used to keep answering the last page until the flow was called
    `stuck_on_<page>` - which reads as a bug in the flow, when what
    happened is that the recording was short (2026-09-25)."""
    where = walk_of(tmp_path, ["google-email-entry.xml"])
    screens = [router.Screen("email", lambda c: c.has("sign in"),
                             lambda c: None, max_visits=20)]
    outcome = replay.over(where, screens, budget_seconds=30)

    assert outcome.reason == "walk_ended", outcome
    assert "1 screen(s)" in outcome.detail and "email" in outcome.detail
    # The flow did get a couple more looks first, so one still waiting on
    # a page that was drawing is not cut off mid-act.
    assert outcome.trail.count("email") > 1


def test_nothing_waits_while_a_replay_runs():
    """A flow's waits are for a device that is drawing; here the next
    screen is already decided."""
    import time as clock

    with replay.no_waiting() as slept:
        began = clock.monotonic()
        clock.sleep(5)
        clock.sleep(4)
        assert clock.monotonic() - began < 0.5
    assert slept == [5, 4], "what it would have waited, for the record"
    # And put back afterwards, or nothing in this process sleeps again.
    began = clock.monotonic()
    clock.sleep(0.05)
    assert clock.monotonic() - began >= 0.03


def test_a_replay_of_a_waiting_flow_still_finishes_at_once(tmp_path):
    """Without this a replay takes half a minute, and a tool nobody runs
    fifty times is not the tool."""
    import time as clock

    where = walk_of(tmp_path, ["google-email-entry.xml"])
    screens = [router.Screen("email", lambda c: c.has("sign in"),
                             router.act_wait, max_visits=20)]
    began = clock.monotonic()
    outcome = replay.over(where, screens, budget_seconds=30)

    assert clock.monotonic() - began < 2.0, "it sat through the waits"
    assert outcome.reason == "walk_ended"


def test_the_loop_end_to_end(tmp_path):
    """Record, draft, replay - the thing the three tools are for.

    The draft's matchers are used as written; only the acts are filled
    in, which is exactly the work the draft leaves to a person.
    """
    where = walk_of(tmp_path, ["google-email-entry.xml",
                               "google-password-changed.xml"])
    source = draft.draft(where)

    # Fill in the acts the way a person would, and run what comes out.
    ready = source.replace(
        "    raise NotImplementedError('say what to do here')",
        "    return None")
    module: dict = {}
    exec(compile(ready, "<draft>", "exec"), module)          # noqa: S102

    outcome = replay.over(where, module["SCREENS"], budget_seconds=30)

    # Both pages were recognised, each by the matcher the draft chose,
    # in the order they were recorded. The run then ends because the
    # walk does: there were two screens and the flow wanted a third.
    assert outcome.trail[0] != outcome.trail[1], \
        "the drafted matchers did not tell the two pages apart"
    assert outcome.reason == "walk_ended", outcome


def test_a_screen_name_is_always_a_name_python_will_take(tmp_path):
    """Google writes "Verify it's you", and `is_verify_it's_you` is not
    a name Python will take - the draft was uncompilable on one of
    Google's own pages (found by running it, 2026-09-25)."""
    assert draft.slug("Verify it's you") == "verify_its_you"
    assert draft.slug("Couldn't sign you in") == "couldnt_sign_you_in"

    where = walk_of(tmp_path, ["google-verify-its-you-app-code.xml",
                               "google-captcha-checkbox.xml"])
    compile(draft.draft(where), "<draft>", "exec")


def test_a_button_is_suggested_before_a_label_that_shares_its_word(tmp_path):
    """On Google's email page 'Sign in' is the heading and 'NEXT' is the
    button. The heading was suggested first, because it matched a word a
    flow usually wants (2026-09-25)."""
    where = walk_of(tmp_path, ["google-email-entry.xml"])
    page = capture.pages(where)[0]
    found = draft.controls(page)

    names = [e.label for e in found]
    assert "NEXT" in names, names
    # The empty box leads - a form is filled before it is submitted -
    # then the button, and the heading comes after both.
    assert found[0].is_input, names
    assert names.index("NEXT") < names.index("Sign in"), names
