"""Getting off a chat screen this run did not sign in to.

The captures are real - phone 695, 13 Aug 2026, where this cost a minute of
the five the phone took.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geelark_farm import screen
from geelark_farm.accounts import Credentials
from geelark_farm.flows import chatgpt_login

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"

CREDS = Credentials(email="testaccount001@example.com", password="x",
                    totp_secret="JBSWY3DPEHPK3PXP")

GUEST_CHAT = ('<hierarchy><node text="ChatGPT" bounds="[0,0][100,50]"/>'
              '<node text="Log in" bounds="[600,80][700,110]"/></hierarchy>')
NO_WAY_IN = '<hierarchy><node text="ChatGPT" bounds="[0,0][100,50]"/></hierarchy>'


@pytest.fixture
def phone(monkeypatch):
    """A device that records what was done to it instead of doing it."""
    done: dict[str, list] = {"cleared": [], "tapped": [], "launched": 0}

    class FakeShell:
        @staticmethod
        def run(client, phone_id, command):
            done["cleared"].append(command)

    def tap(client, phone_id, element):
        done["tapped"].append(element.label)
        return True

    monkeypatch.setattr(chatgpt_login, "shell", FakeShell)
    monkeypatch.setattr(chatgpt_login.screen, "tap_element", tap)
    monkeypatch.setattr(chatgpt_login, "launch",
                        lambda *a: done.__setitem__("launched",
                                                    done["launched"] + 1) or True)
    monkeypatch.setattr(chatgpt_login.time, "sleep", lambda *a: None)
    return done


def context_for(xml: str) -> chatgpt_login.Context:
    ctx = chatgpt_login.Context(client=None, phone_id="P1", creds=CREDS,
                                package="com.openai.chatgpt")
    ctx.elements = screen.parse(xml)
    return ctx


def test_a_chat_screen_offering_a_login_is_taken_rather_than_cleared(phone):
    """It only ever cleared, and a cleared app comes back to a guest chat as
    often as to the welcome screen - so this entry matched again, and again:
    three times on one phone. The `Log in` button was on every capture, and
    its presence also settles the ambiguity the clear was there for, since the
    app does not offer to log in to a session it already has.
    """
    chatgpt_login.act_reset_app(context_for(GUEST_CHAT))

    assert phone["tapped"] == ["Log in"]
    assert phone["cleared"] == []
    assert phone["launched"] == 0


def test_a_chat_screen_with_no_way_in_is_still_cleared(phone):
    """The case the clear was written for: the app may be holding a session an
    earlier run left, and from outside that looks like its logged-out mode.
    Guessing costs a phone reported as ready with nobody in it."""
    chatgpt_login.act_reset_app(context_for(NO_WAY_IN))

    assert phone["cleared"] == ["pm clear com.openai.chatgpt"]
    assert phone["launched"] == 1
    assert phone["tapped"] == []


@pytest.mark.parametrize("capture", [
    "20260813-050927-finish695/051252-logged_out_chat.xml",
    "20260813-050927-finish695/051322-logged-out-chat.xml",
    "20260813-050927-finish691/051102-logged_out_chat.xml",
])
def test_the_screens_that_were_cleared_all_had_a_way_in(capture, phone):
    """Against the hierarchies the run actually archived, not a hand-written
    one. Skipped rather than failed where the artifacts have been cleaned out -
    they are a working directory, not a fixture set."""
    path = ARTIFACTS / capture
    if not path.exists():
        pytest.skip(f"{capture} is no longer under artifacts/")

    chatgpt_login.act_reset_app(
        context_for(path.read_text(encoding="utf-8", errors="replace")))

    assert phone["tapped"] == ["Log in"]
    assert phone["cleared"] == []
