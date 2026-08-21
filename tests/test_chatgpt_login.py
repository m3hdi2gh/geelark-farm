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


# Three real captures from phones 691 and 695 used to be replayed here, read
# straight out of `artifacts/`. The prune that keeps that directory to a week
# removed them (2026-08-21), and they cannot come back - it is a working
# directory, not a fixture set, which the test said itself.
#
# Deleted rather than left skipping: a test that can never run again reads as
# coverage that is not there. The behaviour it guarded is held by the two
# tests above, against hierarchies written out in full. The lesson is the one
# worth keeping: a capture worth testing against belongs in tests/fixtures/,
# copied there deliberately, not referenced where it happens to have landed.


def test_a_rejected_authenticator_code_is_the_accounts_fault_not_the_pages():
    """Against the page phone 778 was standing on. `google_login` grew this on
    2026-08-10 after four wrong codes went to a real account; this flow was
    never given the equivalent, and OpenAI words it differently - so the code
    was retyped from the same wrong secret until the visits ran out and the
    phone was reported stuck on the page (2026-08-16).
    """
    blob = screen.normalize(
        "Check your authenticator app "
        "Enter the 6-digit code from your authenticator app "
        "Code Incorrect code. Please try again. Continue "
        "Verify another way").casefold()

    assert _reason(blob) == "wrong_2fa_code"


def test_a_refused_emailed_code_is_not_read_as_a_wrong_secret():
    """Both pages show "Incorrect code" when a code is refused. On the
    authenticator page that means the secret in the sheet is not the
    account's, and marks the credential; on this one it means the emailed code
    was wrong or expired, which says nothing about the account. Reading one as
    the other condemns an account that has done nothing wrong.

    The page is not terminal at all any more - it is a screen the flow acts on
    - so what matters is that nothing here calls it a verdict."""
    blob = screen.normalize(
        "Check your inbox Enter the code we sent to a@b.com "
        "Incorrect code. Please try again.").casefold()

    assert _reason(blob) != "wrong_2fa_code"
    assert _reason(blob) is None


def test_the_emailed_code_page_is_still_told_apart_from_the_authenticator():
    """The two boxes are identical; only the sentence about the inbox
    separates them, and matching on "verification code" alone sent TOTP codes
    into this one three times over (2026-08-07, row 4)."""
    from geelark_farm.flows.chatgpt_login import EMAIL_CODE_TEXTS

    emailed = screen.normalize(
        "Check your inbox Enter the code we sent to a@b.com").casefold()
    authenticator = screen.normalize(
        "Enter the 6-digit verification code from your authenticator "
        "app").casefold()

    assert any(n in emailed for n in EMAIL_CODE_TEXTS)
    assert not any(n in authenticator for n in EMAIL_CODE_TEXTS)


def test_a_genuinely_dead_account_is_still_fatal_on_that_page():
    """Only the wrong-code reading is withheld. A page that says the account
    is gone means what it says wherever it appears."""
    blob = screen.normalize(
        "Check your inbox Enter the code we sent "
        "This account has been deactivated").casefold()

    assert _reason(blob) == "account_deactivated"


def _reason(blob: str) -> str | None:
    """`_fatal_reason` reads a Context; these tests supply the page text."""
    ctx = chatgpt_login.Context(client=None, phone_id="P1", creds=CREDS,
                                package="com.openai.chatgpt")
    ctx.blob = blob
    return chatgpt_login._fatal_reason(ctx)


# ----------------------------------------- reading the account back
FIXTURES = Path(__file__).parent / "fixtures"


def elements_of(fixture: str):
    return screen.parse((FIXTURES / fixture).read_text(encoding="utf-8",
                                                       errors="replace"))


def test_the_settings_page_names_the_account():
    """Against the page as it was actually captured (2026-08-17): the Email
    section shows the address as a plain TextView, no WebView involved."""
    named = chatgpt_login.account_email_on(
        elements_of("chatgpt-account-settings.xml"))

    assert named == "mizikilak240@gmail.com"


@pytest.mark.parametrize("fixture", ["chatgpt-chat-signed-in.xml",
                                     "chatgpt-account-menu.xml"])
def test_the_other_screens_name_no_account(fixture):
    """The chat screen and the sidebar hold no address, so a verifier that
    stopped early would read nothing rather than the wrong thing."""
    assert chatgpt_login.account_email_on(elements_of(fixture)) is None


def test_the_walk_to_the_settings_page_exists_on_the_real_screens():
    """Each step of verify_account's path, against the captured screens: Menu
    is on the chat screen, Account settings is in the sidebar."""
    chat = elements_of("chatgpt-chat-signed-in.xml")
    drawer = elements_of("chatgpt-account-menu.xml")

    assert screen.find_first(chat, chatgpt_login.MENU_LABELS) is not None
    assert screen.find_first(drawer,
                             chatgpt_login.ACCOUNT_SETTINGS_LABELS) is not None
    # and neither control appears on the other screen, so a tap cannot land
    # on the wrong page's element
    assert screen.find_first(drawer, chatgpt_login.MENU_LABELS) is None


class ScriptedPhone:
    """A phone that answers each capture from a script and records taps."""

    def __init__(self, monkeypatch, screens):
        self.screens = list(screens)
        self.tapped = []
        monkeypatch.setattr(chatgpt_login.screen, "capture",
                            lambda c, p: (FIXTURES / self.screens[0]).read_text(
                                encoding="utf-8", errors="replace"))
        monkeypatch.setattr(chatgpt_login.screen, "tap_element",
                            self._tap)
        monkeypatch.setattr(chatgpt_login.time, "sleep", lambda *a: None)

    def _tap(self, client, phone_id, element):
        self.tapped.append(element.label)
        if len(self.screens) > 1:
            self.screens.pop(0)
        return True


def verify_ctx(email="mizikilak240@gmail.com"):
    ctx = chatgpt_login.Context(
        client=None, phone_id="P1", package="com.openai.chatgpt",
        creds=Credentials(email=email, password="x",
                          totp_secret="JBSWY3DPEHPK3PXP"))
    return ctx


def test_verify_account_walks_the_real_screens(monkeypatch):
    """chat -> Menu -> sidebar -> Account settings -> the page that names the
    address. Every screen in the walk is a capture from a live phone."""
    phone = ScriptedPhone(monkeypatch, ["chatgpt-chat-signed-in.xml",
                                        "chatgpt-account-menu.xml",
                                        "chatgpt-account-settings.xml"])

    assert chatgpt_login.verify_account(verify_ctx()) is None
    assert phone.tapped == ["Menu", "Account settings"]


def test_a_different_account_in_the_app_is_fatal(monkeypatch):
    """The catastrophic case the whole verifier exists for: the app is signed
    in, but as someone else."""
    ScriptedPhone(monkeypatch, ["chatgpt-chat-signed-in.xml",
                                "chatgpt-account-menu.xml",
                                "chatgpt-account-settings.xml"])

    out = chatgpt_login.verify_account(verify_ctx(email="other@example.com"))

    assert out is not None and out.reason == "app_wrong_account"
    assert "mizikilak240@gmail.com" in out.detail


def test_a_walk_that_never_reaches_settings_is_not_a_pass(monkeypatch):
    """On 2026-08-08 a phone was handed over ready with nobody in the app -
    'could not check' must never again count as 'checked'."""
    ScriptedPhone(monkeypatch, ["chatgpt-account-menu.xml"])   # no Menu here

    out = chatgpt_login.verify_account(verify_ctx())

    assert out is not None and out.reason == "session_unverified"


# ------------------------------------------ answering a code OpenAI emailed
FIXTURES = Path(__file__).parent / "fixtures"


def code_context(source=None, fixture="chatgpt-email-code.xml"):
    """The real capture of the page, with a code source behind it."""
    from geelark_farm import codes
    from geelark_farm.flows import chatgpt_login

    xml = (FIXTURES / fixture).read_text(encoding="utf-8")
    ctx = chatgpt_login.Context(
        client=None, phone_id="P",
        creds=Credentials(email="a@b.com", password="p", totp_secret=""),
        codes=source or codes.NoSource())
    ctx.elements = screen.parse(xml)
    ctx.blob = screen.texts(ctx.elements)
    return ctx


class Mailbox:
    """A source that hands over one code, and remembers what it was asked."""

    def __init__(self, code="481920"):
        self.code, self.asked = code, []

    def code_for(self, address, *, since, timeout=180):
        self.asked.append((address, since))
        return self.code


class SilentMailbox:
    def code_for(self, address, *, since, timeout=180):
        return None


def test_a_code_that_arrives_is_typed_in(monkeypatch):
    """The counterpart of act_totp: same box, same submit, and the only
    difference is where the digits came from."""
    from geelark_farm.flows import chatgpt_login

    typed = []
    monkeypatch.setattr(chatgpt_login, "fill",
                        lambda ctx, field, text: typed.append(text))
    monkeypatch.setattr(chatgpt_login, "submit", lambda ctx: None)
    monkeypatch.setattr(chatgpt_login.time, "sleep", lambda *a: None)
    mailbox = Mailbox()

    outcome = chatgpt_login.act_email_code(code_context(mailbox))

    assert typed == ["481920"]
    assert outcome is None                   # the router carries on


def test_the_code_is_asked_for_by_the_account_being_signed_in(monkeypatch):
    from geelark_farm.flows import chatgpt_login

    monkeypatch.setattr(chatgpt_login, "fill", lambda *a: None)
    monkeypatch.setattr(chatgpt_login, "submit", lambda ctx: None)
    monkeypatch.setattr(chatgpt_login.time, "sleep", lambda *a: None)
    mailbox = Mailbox()

    chatgpt_login.act_email_code(code_context(mailbox))

    address, since = mailbox.asked[0]
    assert address == "a@b.com"
    assert since > 0          # only mail newer than this attempt counts


def test_the_same_attempt_keeps_asking_from_the_same_moment(monkeypatch):
    """Otherwise each visit moves the line forward and a code that arrived
    while the page was painting is never seen."""
    from geelark_farm.flows import chatgpt_login

    monkeypatch.setattr(chatgpt_login, "fill", lambda *a: None)
    monkeypatch.setattr(chatgpt_login, "submit", lambda ctx: None)
    monkeypatch.setattr(chatgpt_login.time, "sleep", lambda *a: None)
    mailbox = Mailbox()
    ctx = code_context(mailbox)

    chatgpt_login.act_email_code(ctx)
    chatgpt_login.act_email_code(ctx)

    assert mailbox.asked[0][1] == mailbox.asked[1][1]


def test_no_mailbox_reports_exactly_what_it_always_did():
    """The default. This can be merged and nothing changes until a source is
    configured: the page is reported, the account set aside for a human."""
    from geelark_farm.flows import chatgpt_login

    outcome = chatgpt_login.act_email_code(code_context())

    assert outcome.kind == "fatal"
    assert outcome.reason == "email_code_required"


def test_a_mailbox_that_produced_nothing_is_a_different_sentence():
    """"We never looked" and "we looked and waited" are not the same thing to
    whoever reads the row - the second one points at the mailbox."""
    from geelark_farm.flows import chatgpt_login

    outcome = chatgpt_login.act_email_code(code_context(SilentMailbox()))

    assert outcome.kind == "fatal"
    assert outcome.reason == "email_code_never_arrived"


def test_neither_outcome_blames_the_account():
    """OpenAI judged nothing about it - it asked for a code and never got an
    answer - so the row keeps its place in the pool either way."""
    from geelark_farm import failures

    for reason in ("email_code_required", "email_code_never_arrived"):
        verdict = failures.verdict(reason)
        assert verdict.sets_aside, reason
        assert not verdict.costs_the_credential, reason
