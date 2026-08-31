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
from geelark_farm.flows.router import Outcome

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


def elements_of(fixture: str):
    return screen.parse((FIXTURES / fixture).read_text(encoding="utf-8",
                                                       errors="replace"))


def test_the_settings_page_names_the_account():
    """Against the page as it was actually captured (2026-08-17): the Email
    section shows the address as a plain TextView, no WebView involved.

    The address in it is anonymised. The capture went in with a real one and
    this repository is public, which is the thing the runbook's own credential
    note warns about (2026-08-26).
    """
    named = chatgpt_login.account_email_on(
        elements_of("chatgpt-account-settings.xml"))

    assert named == "testaccount001@example.com"


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


def verify_ctx(email="testaccount001@example.com"):
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
    assert "testaccount001@example.com" in out.detail


def test_the_app_is_brought_back_when_the_phone_has_left_it(monkeypatch):
    """The 2026-08-30 failure, thirty-six times over. Every one of them passed
    email, password and the authenticator, and the page archived when the walk
    gave up was the Play Store's own Subscriptions screen - a real capture,
    the fixture below. `Menu` has never been on that page, so the walk asked
    six times, found nothing, and condemned a session it never looked at."""
    phone = ScriptedPhone(monkeypatch, ["play-store-subscriptions.xml",
                                        "chatgpt-chat-signed-in.xml",
                                        "chatgpt-account-menu.xml",
                                        "chatgpt-account-settings.xml"])
    monkeypatch.setattr(chatgpt_login.shell, "foreground_package",
                        lambda c, p: "com.android.vending")

    def came_back(client, phone_id, package):
        phone.screens.pop(0)          # the app is in front again
        return True

    monkeypatch.setattr(chatgpt_login, "launch", came_back)

    assert chatgpt_login.verify_account(verify_ctx()) is None
    assert phone.tapped == ["Menu", "Account settings"]


def test_the_app_that_is_already_in_front_is_not_relaunched(monkeypatch):
    """The guard: coming back must not become something the walk does on every
    slow render. `launch` kills and restarts, and a restart mid-walk would be
    a way of never reading the session at all."""
    ScriptedPhone(monkeypatch, ["chatgpt-chat-signed-in.xml",
                                "chatgpt-account-menu.xml",
                                "chatgpt-account-settings.xml"])
    monkeypatch.setattr(chatgpt_login.shell, "foreground_package",
                        lambda c, p: "com.openai.chatgpt")
    launched = []
    monkeypatch.setattr(chatgpt_login, "launch",
                        lambda *a: launched.append(a) or True)

    assert chatgpt_login.verify_account(verify_ctx()) is None
    assert launched == [], "it restarted an app that was already in front"


def test_a_walk_that_still_cannot_find_the_app_says_what_was_in_front(
        monkeypatch):
    """Coming back is allowed to fail, and then it is still fatal - `could not
    check` never counts as `checked`. But the reason names the app that was in
    front, which is the fact nobody had until the archives were read by hand
    (2026-08-30)."""
    ScriptedPhone(monkeypatch, ["play-store-subscriptions.xml"])
    monkeypatch.setattr(chatgpt_login.shell, "foreground_package",
                        lambda c, p: "com.android.vending")
    monkeypatch.setattr(chatgpt_login, "launch", lambda *a: False)

    out = chatgpt_login.verify_account(verify_ctx())

    assert out is not None and out.reason == "session_unverified"
    assert "com.android.vending" in out.detail


def test_a_walk_that_never_reaches_settings_is_not_a_pass(monkeypatch):
    """On 2026-08-08 a phone was handed over ready with nobody in the app -
    'could not check' must never again count as 'checked'."""
    ScriptedPhone(monkeypatch, ["chatgpt-account-menu.xml"])   # no Menu here

    out = chatgpt_login.verify_account(verify_ctx())

    assert out is not None and out.reason == "session_unverified"


# ------------------------------------------ answering a code OpenAI emailed
FIXTURES = Path(__file__).parent / "fixtures"


PASSWORD_PAGE = ('<hierarchy>'
                 '<node text="" class="android.widget.EditText" password="true"'
                 ' bounds="[40,300][680,380]"/></hierarchy>')


def test_a_ticked_row_asked_for_a_password_stops_instead_of_typing_nothing():
    """The tick and the screen contradicting each other.

    Before this, the empty password cell went into the box and was submitted.
    OpenAI refused it, and the refusal was written against an account nobody
    had actually tried - so the row was spent on a run that never happened.
    """
    from geelark_farm.flows import chatgpt_login

    ctx = chatgpt_login.Context(
        client=None, phone_id="P",
        creds=Credentials(email="coded@example.com", password="",
                          totp_secret="", email_code_only=True))
    ctx.elements = screen.parse(PASSWORD_PAGE)

    outcome = chatgpt_login.act_password(ctx)
    assert outcome.reason == "unexpected_password_prompt"
    # And nothing was submitted, which is what keeps the account unspent.
    assert not ctx.submitted_password


def test_an_ordinary_row_still_has_its_password_typed(phone, monkeypatch):
    """The guard is narrow on purpose: it is the tick that turns it on, not
    an empty cell, and every row that is not ticked behaves as it always has."""
    from geelark_farm.flows import chatgpt_login

    typed = []
    monkeypatch.setattr(chatgpt_login, "fill",
                        lambda c, f, text: typed.append(text))
    monkeypatch.setattr(chatgpt_login, "submit", lambda c: None)

    ctx = chatgpt_login.Context(client=None, phone_id="P", creds=CREDS)
    ctx.elements = screen.parse(PASSWORD_PAGE)

    assert chatgpt_login.act_password(ctx) is None
    assert typed == [CREDS.password]
    assert ctx.submitted_password


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


def test_a_login_made_with_a_code_is_not_wiped_by_the_reset(monkeypatch):
    """The most expensive thing this branch got wrong.

    A composer only counts when this run put the account there - the app has a
    logged-out mode with the same chat screen, and one was handed over empty
    once. `submitted_password` was how that was asked, and an account signing
    in with an emailed code never sets it. So a login that had just succeeded
    was read as the logged-out mode and `pm clear` wiped it. Twice on phone
    1079, which then reported `app_stuck_on_welcome` (2026-08-22).
    """
    from geelark_farm.flows import chatgpt_login

    ctx = code_context(Mailbox("410473"))
    monkeypatch.setattr(chatgpt_login, "fill", lambda *a: None)
    monkeypatch.setattr(chatgpt_login, "submit", lambda c: None)
    monkeypatch.setattr(chatgpt_login.time, "sleep", lambda *a: None)
    chatgpt_login.act_email_code(ctx)

    # The chat screen it lands on next is this run's doing, and is believed.
    ctx.elements = screen.parse(
        (FIXTURES / "chatgpt-chat-signed-in.xml").read_text(encoding="utf-8"))
    assert chatgpt_login.verified_on_device(ctx)

    # And the entry that would have cleared the app no longer matches it.
    logged_out, = [s for s in chatgpt_login.SCREENS
                   if s.name == "logged_out_chat"]
    assert not logged_out.match(ctx)


def test_a_chat_screen_this_run_did_not_earn_is_still_not_believed():
    """The other half, and the reason the check exists at all: a phone was
    handed over as ready with nobody in the app (2026-08-08)."""
    from geelark_farm.flows import chatgpt_login

    ctx = code_context()
    ctx.elements = screen.parse(
        (FIXTURES / "chatgpt-chat-signed-in.xml").read_text(encoding="utf-8"))

    assert not chatgpt_login.verified_on_device(ctx)
    logged_out, = [s for s in chatgpt_login.SCREENS
                   if s.name == "logged_out_chat"]
    assert logged_out.match(ctx)


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


def test_a_password_that_was_taken_means_the_2fa_is_not_set_up():
    """The page after a password is a different thing from the page instead of
    one. This account can hold an authenticator - OpenAI took its password and
    then emailed a code anyway - so the answer is to set the 2FA up. Fetching
    the code would sign it in once and leave the next build exactly here.
    """
    from geelark_farm.flows import chatgpt_login

    ctx = code_context(Mailbox())          # a source is available...
    ctx.submitted_password = True

    outcome = chatgpt_login.act_email_code(ctx)

    assert outcome.reason == "email_code_required"
    assert "2FA" in outcome.detail or "authenticator" in outcome.detail


def test_a_password_never_asked_for_means_the_code_is_the_only_way_in():
    """...and then a source that produces nothing is a different report again:
    this account cannot hold a password, so nothing here could ever answer it.
    """
    from geelark_farm.flows import chatgpt_login

    outcome = chatgpt_login.act_email_code(code_context())   # NoSource

    assert outcome.kind == "fatal"
    assert outcome.reason == "no_code_source"


def test_the_source_is_not_even_asked_when_a_password_went_in():
    """Not merely that the reason differs - the mailbox must not be read at
    all, or a user is asked for a code that should never have been wanted."""
    from geelark_farm.flows import chatgpt_login

    mailbox = Mailbox()
    ctx = code_context(mailbox)
    ctx.submitted_password = True

    chatgpt_login.act_email_code(ctx)

    assert mailbox.asked == []


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

# ------------------------------- whose session the settings page names
def test_the_address_under_the_email_heading_is_the_one_read():
    """It took the first address anywhere on the page, which the docstring
    already called reading the Email line and was not. The day OpenAI puts a
    second one there - a support link, a workspace - the first wins and a
    phone signed in perfectly correctly is reported as `app_wrong_account`
    (2026-08-23)."""
    from geelark_farm.flows import chatgpt_login

    page = _labelled(["Settings", "support@openai.com", "Parental controls",
                      "Email", "mine@gmail.com", "Appearance"])

    assert chatgpt_login.account_email_on(page) == "mine@gmail.com"


def test_the_real_capture_still_reads_the_same_way():
    """The layout this was written from, so the change cannot have moved it."""
    from geelark_farm.flows import chatgpt_login

    xml = (FIXTURES / "chatgpt-account-settings.xml").read_text(encoding="utf-8")

    assert chatgpt_login.account_email_on(screen.parse(xml)) is not None


def test_a_page_with_no_heading_falls_back_to_the_first_address():
    """An older layout, or one this has not seen. Better than nothing, and it
    is what this always did."""
    from geelark_farm.flows import chatgpt_login

    page = _labelled(["Settings", "someone@example.com", "Appearance"])

    assert chatgpt_login.account_email_on(page) == "someone@example.com"


def _labelled(texts):
    return [screen.Element(text=t, desc="", cls="TextView", resource_id="",
                           bounds="[0,0][10,10]", clickable=False,
                           enabled=True, focused=False, password=False)
            for t in texts]


# =====================================================================
# The handlers (2026-08-25). 52% of this module, and it produces the
# reasons that show up most in a real run: app_no_login_button and
# app_unknown_screen were both on the board this week.
# =====================================================================

class App:
    """The phone, recording what was done and answering what it is asked."""

    def __init__(self, *, taps=(), foreground=None, front_sequence=(),
                 texts=()):
        self.taps = set(taps)
        self.foreground = foreground
        self.front_sequence = list(front_sequence)
        self.texts = list(texts)
        self.tapped: list[str] = []
        self.tried: list[str] = []
        self.commands: list[str] = []
        self.filled: list[str] = []
        self.keys: list[int] = []
        self.points: list[tuple[int, int]] = []

    def install(self, monkeypatch):
        def tap_element(client, phone_id, element):
            self.tried.append(element.label)
            if element.label in self.taps:
                self.tapped.append(element.label)
                return True
            return False

        def tap_first_present(client, phone_id, elements, labels, **kw):
            for label in labels:
                self.tried.append(label)
                if label in self.taps:
                    self.tapped.append(label)
                    return label
            return None

        def front(client, phone_id):
            if self.front_sequence:
                return (self.front_sequence.pop(0)
                        if len(self.front_sequence) > 1
                        else self.front_sequence[0])
            return self.foreground

        monkeypatch.setattr(screen, "tap_element", tap_element)
        monkeypatch.setattr(screen, "tap_first_present", tap_first_present)
        monkeypatch.setattr(chatgpt_login, "fill",
                            lambda ctx, e, text: self.filled.append(text) or True)
        monkeypatch.setattr(chatgpt_login.shell, "foreground_package", front)
        monkeypatch.setattr(chatgpt_login.shell, "run",
                            lambda c, p, cmd, **kw: self.commands.append(cmd))
        monkeypatch.setattr(chatgpt_login.shell, "keyevent",
                            lambda c, p, code: self.keys.append(code))
        monkeypatch.setattr(chatgpt_login.shell, "tap",
                            lambda c, p, x, y: self.points.append((x, y)))
        monkeypatch.setattr(chatgpt_login.time, "sleep", lambda _s: None)
        monkeypatch.setattr(chatgpt_login.Context, "refresh", lambda ctx: None)


@pytest.fixture
def app(monkeypatch):
    def make(**kw):
        made = App(**kw)
        made.install(monkeypatch)
        return made
    return make


def node(label, *, cls="TextView", bounds="[0,0][200,80]", clickable=True,
         password=False, is_input=False):
    return screen.Element(text=label, desc="", cls="EditText" if is_input else cls,
                          resource_id="", bounds=bounds, clickable=clickable,
                          enabled=True, focused=False, password=password)


def gpt_context(*elements, creds=CREDS):
    ctx = chatgpt_login.Context(client=None, phone_id="P", creds=creds)
    ctx.elements = list(elements)
    ctx.blob = screen.texts(ctx.elements)
    return ctx


# ------------------------------------------------------- bringing it forward
def test_the_app_being_in_front_is_asked_rather_than_guessed_from_the_page(app):
    """One monkey and a fixed wait was the whole of this, and it silently did
    nothing twice: the install had only just finished, the app did not come up
    inside eight seconds, and the flow drove on against the Play Store's own
    page - reading "Uninstall" and "Open", matching nothing, and reporting
    app_unknown_screen about a screen that was never this app's (2026-08-08,
    rows 7 and 8)."""
    device = app(foreground="com.openai.chatgpt")

    assert chatgpt_login.launch(None, "P", "com.openai.chatgpt") is True
    assert any("monkey -p com.openai.chatgpt" in c for c in device.commands)


def test_an_app_that_is_slow_to_come_up_is_given_another_go(app):
    device = app(front_sequence=["com.android.vending", "com.openai.chatgpt"])

    assert chatgpt_login.launch(None, "P", "com.openai.chatgpt") is True
    assert len([c for c in device.commands if "monkey" in c]) == 2


def test_an_app_that_never_comes_up_says_so_rather_than_driving_on(app):
    """Returning False is what stops the flow reading somebody else's page."""
    device = app(foreground="com.android.vending")

    assert chatgpt_login.launch(None, "P", "com.openai.chatgpt") is False
    assert len([c for c in device.commands if "monkey" in c]) == \
        chatgpt_login.LAUNCH_ATTEMPTS


def test_a_device_that_will_not_say_is_taken_at_its_word(app):
    """Carrying on beats refusing to start over a diagnostic that is not
    available."""
    app(foreground="")

    assert chatgpt_login.launch(None, "P", "com.openai.chatgpt") is True


# ------------------------------------------------- never the Google button
def test_the_email_path_is_taken_and_the_google_one_refused(app, tmp_path):
    """The Google button would sign in the account that owns the device, and
    the sheet names a different one. Silently signing in the wrong account is
    worse than failing, because it looks like success."""
    device = app(taps={"Log in"})
    ctx = gpt_context(node("Continue with Google"), node("Log in"))
    ctx.artifact_dir = tmp_path

    assert chatgpt_login.act_choose_login(ctx) is None
    assert device.tapped == ["Log in"]


def test_a_label_that_matched_something_googles_is_left_alone(app, tmp_path):
    """Matching is partial, so this is not hypothetical: "Sign in" found "Sign
    in with Google" on the consent sheet and tapped it."""
    device = app(taps={"Sign in with Google"})
    ctx = gpt_context(node("Sign in with Google"))
    ctx.artifact_dir = tmp_path

    out = chatgpt_login.act_choose_login(ctx)

    assert device.tapped == [], "it took the Google path"
    assert out is not None and out.reason == "no_login_button"


def test_a_welcome_screen_with_no_email_login_is_named(app, tmp_path):
    """`app_no_login_button` in the sheet, and it was on the board twice this
    week - so the page that produced it has to be kept."""
    app()
    ctx = gpt_context(node("Something else"))
    ctx.artifact_dir = tmp_path

    out = chatgpt_login.act_choose_login(ctx)

    assert out.kind == "unknown"
    assert out.reason == "no_login_button"
    assert out.artifacts, "the welcome screen was not archived"


# --------------------------------------------------- Google's account chooser
def test_the_sheet_is_closed_by_its_own_control_when_it_has_one(app):
    device = app(taps={"Close"})
    ctx = gpt_context(node("Close"))

    assert chatgpt_login.act_close_google_sheet(ctx) is None
    assert device.tapped == ["Close"]
    assert device.points == [], "it tapped blind with a control on offer"


def test_a_sheet_with_no_way_out_is_dismissed_by_tapping_above_it(app):
    """Whatever the topmost element of the sheet is, the page is above it."""
    app()
    ctx = gpt_context(node("Choose an account", bounds="[0,600][720,700]"))

    assert chatgpt_login.act_close_google_sheet(ctx) is None


def test_a_sheet_that_fills_the_screen_is_reported_not_tapped_at_random(
        app, tmp_path):
    """With nothing above it there is no blank strip to tap, and a tap at the
    top of a full-screen sheet lands on the sheet."""
    app()
    ctx = gpt_context(node("Choose an account", bounds="[0,10][720,90]"))
    ctx.artifact_dir = tmp_path

    out = chatgpt_login.act_close_google_sheet(ctx)

    assert out is not None
    assert out.reason == "google_sheet_stuck"
    assert out.artifacts


# ------------------------------------------------------- the address, twice
def test_the_address_is_typed_and_submitted(app):
    device = app(taps={"Continue"})
    ctx = gpt_context(node("", is_input=True), node("Continue"))

    assert chatgpt_login.act_email(ctx) is None
    assert device.filled == [CREDS.email]
    assert ctx.email_submissions == 1


def test_an_address_still_in_the_box_is_resubmitted_not_retyped(app):
    """Being back on this page with the address still in it means the last
    submission did not take. Retyping on top of it is how a box grows."""
    device = app(taps={"Continue"})
    box = node(CREDS.email, is_input=True)
    ctx = gpt_context(box, node("Continue"))

    chatgpt_login.act_email(ctx)

    assert device.filled == [], "it typed over an address already there"
    assert ctx.email_submissions == 1


def test_an_edge_refusal_is_read_off_the_page_and_not_assumed(app, tmp_path,
                                                              monkeypatch):
    """For a while this did not ask why a submission failed: it assumed
    OpenAI's edge had refused, and told people to change their exit IP on the
    strength of a page that showed nothing at all. Three rows were sent round
    that loop across two runs, and every archived screen was a clean email
    form (2026-08-10)."""
    app(taps={"Continue"})
    ctx = gpt_context(node("", is_input=True), node("Continue"))
    ctx.artifact_dir = tmp_path

    # The module's own needle, not a sentence resembling it. It is the
    # Cloudflare page carrying a Ray ID, recorded in this file's notes; no
    # capture of it exists, which is why the words come from the constant.
    refusal = chatgpt_login.REQUEST_PROBLEM_TEXTS[0]

    def refresh(inner):
        inner.elements = [node(refusal)]
        inner.blob = screen.texts(inner.elements)

    monkeypatch.setattr(chatgpt_login.Context, "refresh", refresh)

    chatgpt_login.act_email(ctx)

    assert ctx.saw_edge_refusal is True


def test_a_clean_page_after_a_failed_submission_is_not_blamed_on_the_exit(
        app, tmp_path, monkeypatch):
    """The counterweight, and the whole of the 2026-08-10 lesson: three rows
    were sent round the retry loop across two runs on the assumption that the
    edge had refused, and every archived screen was a clean email form.

    `chatgpt-request-problem.xml` IS one of those archives - named for what
    the run reported, not for what the page said. It carries neither needle,
    which is the evidence.
    """
    app(taps={"Continue"})
    ctx = gpt_context(node("", is_input=True), node("Continue"))
    ctx.artifact_dir = tmp_path

    archived = (FIXTURES / "chatgpt-request-problem.xml").read_text(
        encoding="utf-8")

    def refresh(inner):
        inner.elements = screen.parse(archived)
        inner.blob = screen.texts(inner.elements)

    monkeypatch.setattr(chatgpt_login.Context, "refresh", refresh)

    chatgpt_login.act_email(ctx)

    assert ctx.saw_edge_refusal is False


def test_an_address_that_keeps_coming_back_is_named_by_what_was_seen(
        app, tmp_path):
    """Two different reasons out of the same loop, and the page decides which:
    `request_rejected` says change the exit, `email_not_accepted` says the
    address is the problem. Telling an operator to rotate an IP over an
    account error costs a proxy and fixes nothing."""
    app(taps={"Continue"})
    box = node(CREDS.email, is_input=True)

    refused = gpt_context(box, node("Continue"))
    refused.artifact_dir = tmp_path
    refused.email_submissions = chatgpt_login.MAX_EMAIL_SUBMISSIONS
    refused.saw_edge_refusal = True

    assert chatgpt_login.act_email(refused).reason == "request_rejected"

    plain = gpt_context(box, node("Continue"))
    plain.artifact_dir = tmp_path
    plain.email_submissions = chatgpt_login.MAX_EMAIL_SUBMISSIONS

    assert chatgpt_login.act_email(plain).reason == "email_not_accepted"


# --------------------------------------------------------- password and code
def test_the_password_is_recorded_as_having_been_submitted(app):
    """Success depends on it: a composer means nothing unless this run put the
    password in - which is what stops a session left by an earlier run being
    reported as this one's."""
    app(taps={"Continue"})
    ctx = gpt_context(node("", is_input=True, password=True), node("Continue"))

    assert chatgpt_login.act_password(ctx) is None
    assert ctx.submitted_password is True


def test_a_code_is_typed_and_it_is_six_digits(app):
    """From `totp_now`, which waits out a code with too little life left to
    survive being typed - Google answers a late one with "wrong code" and
    counts it against the account."""
    device = app(taps={"Continue"})
    ctx = gpt_context(node("", is_input=True), node("Continue"))

    assert chatgpt_login.act_totp(ctx) is None

    assert len(device.filled) == 1
    assert len(device.filled[0]) == 6 and device.filled[0].isdigit()


# ------------------------------------------------ a chat screen nobody owns
def test_a_chat_screen_with_a_login_control_is_simply_logged_in_from(app,
                                                                     tmp_path):
    """It only ever cleared, and that was slower than it looked: a cleared app
    comes back to a guest chat as often as it comes back to the welcome
    screen, so the clear matched this entry again, and again - three times on
    phone 695, a minute of the five it took (2026-08-13)."""
    device = app(taps={"Log in"})
    ctx = gpt_context(node("Log in"))
    ctx.artifact_dir = tmp_path

    assert chatgpt_login.act_reset_app(ctx) is None
    assert device.tapped == ["Log in"]
    assert not any("pm clear" in c for c in device.commands), "it cleared anyway"


def test_a_chat_screen_with_nothing_to_log_in_with_is_cleared(app, tmp_path):
    """The app may be holding a session from an earlier run, and from outside
    that is indistinguishable from its logged-out mode. Throwing away someone
    else's session costs a minute; assuming costs a phone handed over with
    nobody in it."""
    device = app(foreground="com.openai.chatgpt")
    ctx = gpt_context(node("Ask anything"))
    ctx.artifact_dir = tmp_path

    assert chatgpt_login.act_reset_app(ctx) is None
    assert any("pm clear" in c for c in device.commands)
    assert any("monkey" in c for c in device.commands), "it never restarted"


# ------------------------------------------------- the entry point end to end
class Run:
    """A sign-in with the device and the router both answering a script."""

    def __init__(self, *, installed=True, launches=True, drove=None,
                 account_check=None):
        self.installed = installed
        self.launches = launches
        self.drove = drove or Outcome("success", "logged_in")
        self.account_check = account_check
        self.commands: list[str] = []
        self.launched = 0
        self.booted: list[str] = []
        self.is_done = None
        self.ctx = None

    def install(self, monkeypatch):
        def drive(ctx, screens, *, is_done, budget_seconds, logger=None):
            self.is_done = is_done
            self.ctx = ctx
            return self.drove

        monkeypatch.setattr(chatgpt_login.shell, "package_installed",
                            lambda c, p, pkg: self.installed)
        monkeypatch.setattr(chatgpt_login.shell, "run",
                            lambda c, p, cmd, **kw: self.commands.append(cmd))
        monkeypatch.setattr(chatgpt_login, "launch",
                            lambda c, p, pkg: (setattr(self, "launched",
                                                       self.launched + 1)
                                               or self.launches))
        monkeypatch.setattr(chatgpt_login.router, "drive", drive)
        monkeypatch.setattr(chatgpt_login, "verify_account",
                            lambda ctx: self.account_check)
        monkeypatch.setattr(chatgpt_login.phones, "ensure_running",
                            lambda c, p: self.booted.append(p))
        monkeypatch.setattr(chatgpt_login.time, "sleep", lambda _s: None)
        return self


@pytest.fixture
def run(monkeypatch):
    def make(**kw):
        return Run(**kw).install(monkeypatch)
    return make


PACKAGE = "com.openai.chatgpt"


def test_an_app_that_is_not_there_is_named_before_anything_is_driven(run):
    """A flow driven against a phone with no app reads whatever IS in front -
    a launcher, the Play Store - and reports app_unknown_screen about it."""
    session = run(installed=False)

    out = chatgpt_login.sign_in(None, "P", CREDS, package=PACKAGE)

    assert out.kind == "fatal"
    assert out.reason == "app_not_installed"
    assert session.launched == 0
    assert out.trail == [], "it never saw a screen, and should say so"


def test_an_app_that_will_not_come_up_is_a_named_outcome_too(run):
    session = run(launches=False)

    out = chatgpt_login.sign_in(None, "P", CREDS, package=PACKAGE)

    assert out.reason == "app_would_not_start"
    assert str(chatgpt_login.LAUNCH_ATTEMPTS) in out.detail
    assert session.is_done is None, "it drove a flow against an app that is not up"


def test_a_second_account_on_one_phone_clears_the_app_first(run):
    """Seven rows were signed in as the account of the run before them,
    because the app was still holding that session (2026-08-13). Three of the
    seven had already signed in successfully on earlier phones."""
    session = run()

    chatgpt_login.sign_in(None, "P", CREDS, package=PACKAGE, fresh=True)

    assert any(f"pm clear {PACKAGE}" in c for c in session.commands)


def test_an_ordinary_first_login_does_not_throw_away_a_session(run):
    """Clearing costs a minute and the app's own onboarding again. Only a
    caller that knows it is reusing the phone asks for it."""
    session = run()

    chatgpt_login.sign_in(None, "P", CREDS, package=PACKAGE)

    assert not any("pm clear" in c for c in session.commands)


# ------------------------------------------------ what the router is told
def test_a_chat_screen_alone_is_not_a_login(run, monkeypatch):
    """The composer says a chat screen is up, NOT that anyone is signed in -
    and least of all that THIS run signed in. That distinction is the whole
    of `verified_on_device`."""
    session = run()
    monkeypatch.setattr(chatgpt_login, "verified_on_device", lambda ctx: False)

    chatgpt_login.sign_in(None, "P", CREDS, package=PACKAGE)
    session.ctx.elements = [node("Ask anything")]

    assert session.is_done() is None


def test_the_screen_is_read_from_what_the_loop_already_fetched(run,
                                                               monkeypatch):
    """Unlike the other two steps this reads the screen, because the app's
    session is not visible to the device - so it has to cost nothing extra."""
    session = run()
    monkeypatch.setattr(chatgpt_login, "verified_on_device", lambda ctx: True)

    chatgpt_login.sign_in(None, "P", CREDS, package=PACKAGE)

    session.ctx.elements = []
    assert session.is_done() is None, "it claimed success off an empty screen"

    session.ctx.elements = [node("Ask anything")]
    out = session.is_done()
    assert out is not None and out.ok


# --------------------------------------------------- and whose session it is
def test_a_login_the_app_does_not_confirm_is_not_reported_as_one(run):
    """The router proved a chat screen. This proves whose it is - and a phone
    handed over signed into the wrong account looks exactly like success."""
    refused = Outcome("fatal", "wrong_account", "the app names somebody else")
    session = run(account_check=refused)

    out = chatgpt_login.sign_in(None, "P", CREDS, package=PACKAGE)

    assert out is refused
    del session


def test_a_confirmed_login_says_the_app_itself_named_the_account(run):
    run(drove=Outcome("success", "logged_in", artifacts=["a.xml"],
                      trail=["welcome", "email"]))

    out = chatgpt_login.sign_in(None, "P", CREDS, package=PACKAGE)

    assert out.ok
    assert CREDS.email in out.detail
    assert "settings" in out.detail
    # The path and the pages survive the second verdict; without them the
    # History row for a successful build has no Steps and no artifacts.
    assert out.trail == ["welcome", "email"]
    assert out.artifacts == ["a.xml"]


def test_a_flow_that_failed_is_handed_back_untouched(run):
    """The account check only runs on a success. Running it on a failure
    replaces a named reason with whatever the settings page happens to say."""
    failed = Outcome("unknown", "no_login_button")
    session = run(drove=failed,
                  account_check=Outcome("fatal", "wrong_account"))

    assert chatgpt_login.sign_in(None, "P", CREDS, package=PACKAGE) is failed
    del session


def test_the_phone_is_booted_by_the_call_that_promises_to(run):
    session = run()

    chatgpt_login.sign_in_on_phone(None, "P", CREDS, package=PACKAGE)

    assert session.booted == ["P"]


# --------------------------------------- what each screen insists on as well
def gpt_matched(ctx):
    found = next((s for s in chatgpt_login.SCREENS if s.match(ctx)), None)
    return found.name if found else None


def test_the_welcome_screen_is_the_one_with_nowhere_to_type():
    """Without that guard this matched the login page too, because "Log in or
    sign up" is both the button here and the heading there - so the flow
    arrived where it wanted to be and then tapped that page's own title until
    it ran out of visits (2026-08-07, row 1). The tap coordinates in the log
    are the tell: y=1216 the first time, y=366 the second."""
    welcome = gpt_context(node("Log in"))
    assert gpt_matched(welcome) == "welcome"

    # The same words with a box to type in is the login page, not this.
    login_page = gpt_context(node("Log in"), node("", is_input=True))
    assert gpt_matched(login_page) != "welcome"


def test_the_email_page_is_the_word_and_the_box():
    """The field carries no label of its own - "Email" is a sibling TextView
    above it - so this matches the box and the word, not a sentence. The first
    attempt looked for "email address" / "your email"; the page says only
    "Email", so nothing matched and the welcome entry below claimed it."""
    assert gpt_matched(gpt_context(node("Email"),
                                   node("", is_input=True))) == "email_entry"
    assert gpt_matched(gpt_context(node("Email"))) != "email_entry"


def test_a_chat_screen_this_run_signed_into_is_not_reset():
    """`submitted_password` is what separates "the app was already logged in"
    from "this run logged it in" - and clearing the second throws away the
    login that just happened."""
    # The captured chat screen, so "a composer is up" means what the app
    # actually draws rather than a label that reads like one.
    chat = screen.parse((FIXTURES / "chatgpt-chat-signed-in.xml")
                        .read_text(encoding="utf-8"))

    ours = gpt_context(*chat)
    ours.submitted_password = True

    assert gpt_matched(ours) != "logged_out_chat"

    theirs = gpt_context(*chat)
    assert gpt_matched(theirs) == "logged_out_chat"


def test_an_onboarding_card_does_not_have_to_claim_to_be_clickable():
    """Nothing in this app reports clickable=true - every label so far,
    including the two buttons on the notification card, is a plain TextView
    whose centre taps correctly. Requiring the flag meant this entry could
    never match, so a signed-in session sat on an onboarding card and was
    reported as an unknown screen (2026-08-07, row 2)."""
    card = screen.Element(text="Not now", desc="", cls="TextView",
                          resource_id="", bounds="[0,0][200,80]",
                          clickable=False, enabled=True, focused=False,
                          password=False)

    assert gpt_matched(gpt_context(card)) == "onboarding"


def test_the_welcome_screen_is_not_argued_with_forever():
    """Two visits: one to take the email path, one if the tap did not land.
    A third is the loop that took row 1's whole budget."""
    welcome = next(s for s in chatgpt_login.SCREENS if s.name == "welcome")
    chat = next(s for s in chatgpt_login.SCREENS
                if s.name == "logged_out_chat")

    assert welcome.max_visits == 2
    assert chat.max_visits == 2


# --------------------------------------- the last of it (what mutation found)
def test_a_verdict_page_carries_the_advice_for_its_own_reason(app, tmp_path):
    """The reason goes in a Status cell and the advice goes in the Note beside
    it, so an operator can act without opening the archive."""
    app()
    ctx = gpt_context(node("Verify you are human"))
    ctx.artifact_dir = tmp_path

    out = chatgpt_login.act_fatal(ctx)

    assert out.kind == "fatal"
    assert out.reason in chatgpt_login.FATAL_ADVICE
    assert out.detail == chatgpt_login.FATAL_ADVICE[out.reason]
    assert out.artifacts


def test_a_page_that_is_fatal_for_no_named_reason_still_says_something(
        app, tmp_path):
    """`act_fatal` runs off the screen matcher, and a matcher that fires with
    no reason behind it would otherwise produce an Outcome with an empty
    reason - which lands in the sheet as a blank cell."""
    app()
    ctx = gpt_context(node("Nothing recognisable"))
    ctx.artifact_dir = tmp_path

    out = chatgpt_login.act_fatal(ctx)

    assert out.reason == "unknown_fatal"
    assert out.detail, "a verdict with nothing to read"


def test_a_settings_page_with_no_address_on_it_is_not_a_confirmed_login(
        app, tmp_path, monkeypatch):
    """The second verdict has to be able to fail. A settings page that shows
    no address proves nothing about whose session this is, and reporting
    success off it is a phone handed over on an assumption."""
    app(taps={"Menu", "Account settings"})
    # Both steps of the walk are reachable; only the address is missing.
    ctx = gpt_context(node("Menu"), node("Account settings"))
    ctx.artifact_dir = tmp_path
    monkeypatch.setattr(chatgpt_login, "account_email_on", lambda els: None)

    out = chatgpt_login.verify_account(ctx)

    assert out is not None
    assert out.reason == "session_unverified"
    assert out.artifacts


def test_the_dismiss_list_is_an_allowlist_and_not_any_button(app):
    """It runs with clickable_only off, so the label list is the only thing
    keeping it off the wrong control - which is why it never contains a
    refusal like "Don't allow"."""
    assert "Don't allow" not in chatgpt_login.DISMISS_LABELS
    assert "Deny" not in chatgpt_login.DISMISS_LABELS

    device = app(taps={"Not now"})
    ctx = gpt_context(node("Not now"))

    assert chatgpt_login.act_dismiss(ctx) is None
    assert device.tapped == ["Not now"]


def test_a_code_box_outranks_the_password_box():
    """Once a code is being asked for, the password has already been accepted -
    and both pages carry an input, so order is the only thing separating
    them."""
    names = [s.name for s in chatgpt_login.SCREENS]

    assert names.index("totp_entry") < names.index("password_entry")


def test_a_loading_web_view_is_waited_out_far_longer_than_a_native_page():
    """A web view spends much more of its life painting than a native screen
    does, so this matters more here than in the Google flow - where it was
    still worth a whole login."""
    loading = next(s for s in chatgpt_login.SCREENS if s.name == "loading")

    assert loading.max_visits >= 20


def test_a_password_page_without_its_box_is_left_alone(app):
    device = app()
    ctx = gpt_context(node("", is_input=True))      # visible, not masked

    assert chatgpt_login.act_password(ctx) is None
    assert device.filled == []
    assert ctx.submitted_password is False, "it recorded a password it never sent"


def test_a_code_page_without_its_box_is_left_alone(app):
    device = app()
    ctx = gpt_context(node("Enter the code"))

    assert chatgpt_login.act_totp(ctx) is None
    assert device.filled == []


# ------------------------------- the welcome screen after OpenAI redrew it
def fixture_ctx(name, **kw):
    """A Context holding a captured screen, the way `refresh` leaves one."""
    from geelark_farm.flows import chatgpt_login

    xml = (FIXTURES / f"{name}.xml").read_text(encoding="utf-8")
    ctx = chatgpt_login.Context(client=None, phone_id="P1", creds=CREDS,
                                package="com.openai.chatgpt", **kw)
    ctx.raw = xml
    ctx.elements = screen.parse(xml)
    # `blob` is what `has` reads, and only `refresh` sets it. A test that
    # assigns `elements` alone gets a Context that answers `has` with False
    # for everything - which routes the welcome screen to `onboarding` and
    # looks like a bug in the registry (2026-08-28).
    ctx.blob = screen.texts(ctx.elements)
    return ctx


def routes_to(ctx):
    from geelark_farm.flows import chatgpt_login

    return [(s.name, s.act.__name__) for s in chatgpt_login.SCREENS
            if s.match(ctx)]


def test_the_redrawn_welcome_screen_is_still_the_welcome_screen():
    """OpenAI replaced three choices with one "Continue" and moved the words
    "Log in" into a paragraph (2026-08-28). The entry still has to claim it,
    or the flow meets an unknown screen instead of a known one."""
    first = routes_to(fixture_ctx("chatgpt-welcome-continue-only"))[0]

    assert first == ("welcome", "act_choose_login")


def test_the_old_welcome_screen_is_claimed_by_the_same_entry():
    """The change adds a case rather than replacing one: a phone that has not
    updated the app still meets the screen this was written for."""
    first = routes_to(fixture_ctx("chatgpt-welcome"))[0]

    assert first == ("welcome", "act_choose_login")


def test_continue_is_taken_when_the_welcome_screen_offers_nothing_else(
        phone, monkeypatch):
    """It leads to the guest chat, which carries a "Log in" of its own and
    which `logged_out_chat` already takes - so this is a step on the way, not
    a second path into the account."""
    from geelark_farm.flows import chatgpt_login

    tapped = []
    monkeypatch.setattr(chatgpt_login.screen, "tap_element",
                        lambda c, p, e: tapped.append(e.label) or True)

    out = chatgpt_login.act_choose_login(
        fixture_ctx("chatgpt-welcome-continue-only"))

    assert out is None                      # carry on rather than give up
    assert tapped == ["Continue"]


def test_a_real_login_control_is_still_preferred_over_continue(phone,
                                                               monkeypatch):
    """The old screen has both, and the login label is the shorter way in."""
    from geelark_farm.flows import chatgpt_login

    tapped = []
    monkeypatch.setattr(chatgpt_login.screen, "tap_element",
                        lambda c, p, e: tapped.append(e.label) or True)

    chatgpt_login.act_choose_login(fixture_ctx("chatgpt-welcome"))

    assert tapped == ["Log in or sign up"]


def test_the_google_button_is_never_what_continue_finds():
    """On the old screen a bare search for "continue" finds "Continue with
    Google" first. Tapping it signs in the account that owns the device
    rather than the one the sheet names, which looks like success."""
    from geelark_farm.flows.chatgpt_login import _continue_button

    ctx = fixture_ctx("chatgpt-welcome")
    picked = _continue_button(ctx.elements)

    assert picked is not None
    assert "google" not in picked.label.casefold()


def test_a_sentence_is_never_taken_for_a_button():
    """"By continuing, you agree to our Terms" shares the screen with the
    button. Tapping prose instead of a control is a mistake this project has
    already made once, on Play's "Try again"."""
    from geelark_farm.flows.chatgpt_login import _continue_button

    ctx = fixture_ctx("chatgpt-welcome-continue-only")

    assert _continue_button(ctx.elements).label == "Continue"


def test_a_welcome_screen_with_no_way_off_it_still_gives_up(phone,
                                                            monkeypatch):
    """The fallback must not turn every unrecognised welcome into a tap on
    nothing - `no_login_button` is still the honest answer when there is no
    control at all."""
    from geelark_farm.flows import chatgpt_login

    ctx = chatgpt_login.Context(client=None, phone_id="P1", creds=CREDS,
                                package="com.openai.chatgpt")
    ctx.elements = screen.parse(
        '<hierarchy><node text="Welcome to ChatGPT" class="android.widget'
        '.TextView" bounds="[0,0][100,50]"/></hierarchy>')

    out = chatgpt_login.act_choose_login(ctx)

    assert out is not None and out.reason == "no_login_button"


def test_the_guest_chat_continue_leads_to_is_one_the_flow_already_knows():
    """The whole reason tapping Continue is safe: what it produces is a
    screen with a registry entry, and that entry taps the login on it."""
    first = routes_to(fixture_ctx("chatgpt-guest-chat-with-login"))[0]

    assert first == ("logged_out_chat", "act_reset_app")


def test_the_payment_nag_is_the_accounts_own_verdict(monkeypatch):
    """The 2026-08-31 failure, twelve times over: a Plus account with a
    broken payment method drew its nag over the app and twelve verifies
    took device blame for it. The page names the account, so the answer is
    the account's status - immediately, with nothing tapped, because the
    dump still shows Menu underneath the modal and a trusted tap would land
    on the scrim. The fixture is the real capture from phone 1534."""
    phone = ScriptedPhone(monkeypatch, ["chatgpt-payment-nag.xml"])

    out = chatgpt_login.verify_account(verify_ctx())

    assert out is not None and out.reason == "payment_problem"
    assert phone.tapped == [], "something was tapped under the modal"


def test_the_payment_verdict_sets_the_account_aside_not_the_phone():
    """The whole point of the reason: the account wears the status and the
    phone is not condemned - the opposite of what session_unverified did to
    four phones in one afternoon."""
    from geelark_farm import failures

    said = failures.verdict("payment_problem")
    assert said.sets_aside
    assert not said.stops_the_phone and not said.costs_the_credential


def test_the_nag_fixture_really_hides_the_walk_from_taps():
    """What makes the ordering matter: on the real capture the sidebar's
    Menu is findable while the nag is up - a walk that trusts it taps a
    scrim. And the nag's own labels are all present to be noticed."""
    nag = elements_of("chatgpt-payment-nag.xml")
    assert screen.find_first(nag, chatgpt_login.MENU_LABELS) is not None
    assert screen.find_first(nag, chatgpt_login.PAYMENT_NAG_LABELS) is not None
    # and no other screen in the walk shows the nag, so a false positive
    # cannot press BACK on a healthy page
    for fixture in ("chatgpt-chat-signed-in.xml", "chatgpt-account-menu.xml",
                    "chatgpt-account-settings.xml"):
        assert screen.find_first(elements_of(fixture),
                                 chatgpt_login.PAYMENT_NAG_LABELS) is None
