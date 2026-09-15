"""The Claude sign-in flow, against the screens captured on phone 2963
(2026-09-16): welcome, the typed welcome, the code page, a refused code,
the chat, the menu and the settings page that names the account."""

from __future__ import annotations

from pathlib import Path

import pytest

from geelark_farm import codes, screen
from geelark_farm.accounts import Credentials
from geelark_farm.flows import claude_login as cl

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EMAIL = "mehdi.savant@gmail.com"
CREDS = Credentials(email=EMAIL, password="", totp_secret="",
                    email_code_only=True)


def elements_of(name: str) -> list[screen.Element]:
    return screen.parse((FIXTURES / f"claude-{name}.xml").read_text(
        encoding="utf-8"))


def ctx_for(name: str, source=None) -> cl.Context:
    ctx = cl.Context(client=None, phone_id="P", creds=CREDS,
                     codes=source or codes.NoSource())
    ctx.elements = elements_of(name)
    ctx.blob = screen.texts(ctx.elements)
    return ctx


def matched(ctx: cl.Context) -> str | None:
    return next((s.name for s in cl.SCREENS if s.match(ctx)), None)


class Mailbox:
    """A code source that hands over a script of codes and remembers what
    it was asked, including which codes it was told were wrong."""

    def __init__(self, *codes_):
        self.codes, self.asked, self.refused = list(codes_), [], []

    def code_for(self, address, *, since, timeout=None):
        self.asked.append((address, since))
        return self.codes.pop(0) if self.codes else None

    def wrong(self, address):
        self.refused.append(address)


@pytest.fixture
def phone(monkeypatch):
    """A device that records what was done to it instead of doing it."""
    done = {"filled": [], "tapped": [], "keys": [], "run": [], "launched": 0}

    def fill(ctx, element, text):
        done["filled"].append(text)
        return True

    monkeypatch.setattr(cl, "fill", fill)
    monkeypatch.setattr(cl.screen, "tap_element",
                        lambda c, p, e: done["tapped"].append(e.label) or True)
    monkeypatch.setattr(cl.shell, "keyevent",
                        lambda c, p, code: done["keys"].append(code))
    monkeypatch.setattr(cl.shell, "run",
                        lambda c, p, cmd, **k: done["run"].append(cmd) or "")
    monkeypatch.setattr(cl, "launch",
                        lambda *a, **k: done.__setitem__(
                            "launched", done["launched"] + 1) or True)
    monkeypatch.setattr(cl.time, "sleep", lambda *a: None)
    monkeypatch.setattr(cl.Context, "refresh", lambda self: None)
    return done


# ---------------------------------------------------------------- screens
def test_every_captured_screen_is_recognised_as_itself():
    assert matched(ctx_for("welcome")) == "welcome"
    assert matched(ctx_for("welcome-typed")) == "welcome"
    assert matched(ctx_for("welcome-after-change")) == "welcome"
    assert matched(ctx_for("code-page")) == "code_entry"
    assert matched(ctx_for("wrong-code")) == "code_entry"
    # The chat, before this run has typed a code, is a chat this run did
    # not earn.
    assert matched(ctx_for("chat-signed-in")) == "logged_out_chat"
    # The menu and the settings page are the read-back walk's, and no
    # entry claims them - `dismissable` above all: "Close" is on neither.
    assert matched(ctx_for("menu")) is None
    assert matched(ctx_for("settings")) is None


def test_the_welcome_screen_takes_the_email_path_never_google(phone):
    """Untyped: the address goes in and, with no arrow drawn yet, the
    keyboard submits. Typed: the arrow is tapped. "Continue with Google"
    is never what gets tapped."""
    ctx = ctx_for("welcome")
    assert cl.act_welcome(ctx) is None
    assert phone["filled"] == [EMAIL]
    assert phone["keys"] == [66] and phone["tapped"] == []
    assert ctx.email_submissions == 1

    ctx = ctx_for("welcome-typed")
    ctx.creds = Credentials(email="islandalaskans@gmail.com", password="",
                            totp_secret="", email_code_only=True)
    assert cl.act_welcome(ctx) is None
    assert phone["filled"] == [EMAIL], "already typed: not typed again"
    assert phone["tapped"] == ["Continue with Email"]
    assert all("google" not in t.casefold() for t in phone["tapped"])


def test_a_stale_address_in_the_box_is_replaced_not_appended_to(phone):
    """After "Change email address" the previous address is still in the
    box; `fill` clears it first, so this only has to hand it the box. The
    probe that typed without clearing made
    `islandalaskans@mehdi.savant@gmail.comgmail.com` (2963, 2026-09-16)."""
    ctx = ctx_for("welcome-typed")          # holds islandalaskans@gmail.com
    assert cl.act_welcome(ctx) is None
    assert phone["filled"] == [EMAIL]


def test_the_code_page_asks_the_source_types_the_code_and_believes_the_chat(
        phone):
    source = Mailbox("463606")
    ctx = ctx_for("code-page", source)

    assert cl.act_code(ctx) is None

    assert source.asked == [(EMAIL, ctx.code_since)]
    assert phone["filled"] == ["463606"]
    assert phone["keys"] == [], "six digits submit themselves"
    assert ctx.submitted_code and ctx.awaiting_verdict
    # The chat that follows is this run's doing now, and is believed.
    ctx.elements = elements_of("chat-signed-in")
    ctx.blob = screen.texts(ctx.elements)
    assert cl.verified_on_device(ctx)
    assert matched(ctx) is None, "no entry claims the earned chat"


def test_a_code_of_another_length_is_submitted_with_the_keyboard(phone):
    ctx = ctx_for("code-page", Mailbox("48291"))
    cl.act_code(ctx)
    assert phone["keys"] == [66]


def test_a_refused_code_is_counted_told_to_the_source_and_the_third_ends_it(
        phone):
    """"Incorrect code" under a box still holding the digits is the app's
    verdict: counted once, told to the source so the panel's tries_left
    moves, and a fresh code asked for. The third refusal is wrong_code."""
    source = Mailbox("111111", "222222", "333333")
    ctx = ctx_for("code-page", source)
    cl.act_code(ctx)                       # 111111 typed, verdict pending
    ctx.elements = elements_of("wrong-code")
    ctx.blob = screen.texts(ctx.elements)

    assert cl.act_code(ctx) is None        # refused once; 222222 typed
    assert ctx.wrong_codes == 1 and source.refused == [EMAIL]
    assert phone["filled"] == ["111111", "222222"]

    assert cl.act_code(ctx) is None        # refused twice; 333333 typed
    assert ctx.wrong_codes == 2
    out = cl.act_code(ctx)                 # refused a third time
    assert out is not None and out.reason == "wrong_code"
    assert out.kind == "fatal"
    assert len(source.asked) == 3, "no fourth code asked for"
    assert source.refused == [EMAIL] * 3


def test_a_refusal_is_counted_once_however_often_the_page_is_read(phone):
    """The verdict is read on the visit after typing, once: a page read
    again with the same "Incorrect code" on it (the source has nothing
    new to type) counts nothing more."""
    source = Mailbox("111111")
    ctx = ctx_for("code-page", source)
    cl.act_code(ctx)
    ctx.elements = elements_of("wrong-code")
    ctx.blob = screen.texts(ctx.elements)
    out = cl.act_code(ctx)                 # counted, then nothing to type
    assert ctx.wrong_codes == 1 and out.reason == "code_timeout"
    assert cl.act_code(ctx).reason == "code_timeout"
    assert ctx.wrong_codes == 1, "not a second count"


def test_no_code_is_the_sources_absence_or_its_silence(phone):
    ctx = ctx_for("code-page")             # NoSource
    out = cl.act_code(ctx)
    assert out.kind == "fatal" and out.reason == "no_code_source"
    ctx = ctx_for("code-page", Mailbox())  # a source that never answers
    out = cl.act_code(ctx)
    assert out.reason == "code_timeout"
    assert "POST /ready" in out.detail


def test_a_chat_this_run_did_not_earn_is_cleared(phone):
    ctx = ctx_for("chat-signed-in")
    assert not cl.verified_on_device(ctx)
    cl.act_reset_app(ctx)
    assert phone["run"] == ["pm clear com.anthropic.claude"]
    assert phone["launched"] == 1


# ------------------------------------------------------ reading it back
def test_the_settings_page_names_the_account_and_the_others_do_not():
    assert cl.account_email_on(elements_of("settings")) == EMAIL
    for name in ("welcome", "welcome-typed", "code-page", "chat-signed-in",
                 "menu"):
        assert cl.account_email_on(elements_of(name)) is None, name
    # The code page shows the address in prose ("We sent an email to\n...")
    # and must not be mistaken for the settings page: prose is not a
    # label-shaped line.
    assert cl.account_email_on(elements_of("wrong-code")) is None


class ScriptedPhone:
    """The walk's screens in order, with what was tapped recorded."""

    def __init__(self, ctx, *names):
        self.ctx, self.names, self.tapped = ctx, list(names), []

    def refresh(self):
        name = self.names.pop(0) if self.names else "settings"
        self.ctx.elements = elements_of(name)
        self.ctx.blob = screen.texts(self.ctx.elements)


def _walk(monkeypatch, *names, email=EMAIL):
    ctx = cl.Context(client=None, phone_id="P",
                     creds=Credentials(email=email, password="",
                                       totp_secret="", email_code_only=True))
    ctx.submitted_code = True
    phone = ScriptedPhone(ctx, *names)
    monkeypatch.setattr(ctx, "refresh", phone.refresh)
    monkeypatch.setattr(cl.screen, "tap_element",
                        lambda c, p, e: phone.tapped.append(e.desc) or True)
    monkeypatch.setattr(cl.time, "sleep", lambda *a: None)
    return ctx, phone


def test_verify_account_walks_the_real_screens(monkeypatch):
    """chat -> "Open menu, ..." -> "Mehdi, Settings" -> the address at the
    top of the settings page. Run live on 2963 the day it was written."""
    ctx, phone = _walk(monkeypatch, "chat-signed-in", "menu", "settings")
    assert cl.verify_account(ctx) is None
    assert phone.tapped == ["Open menu, new feature available",
                            "Mehdi, Settings"]


def test_a_different_account_in_the_app_is_fatal(monkeypatch):
    ctx, _ = _walk(monkeypatch, "chat-signed-in", "menu", "settings",
                   email="somebody.else@example.com")
    out = cl.verify_account(ctx)
    assert out is not None and out.reason == "app_wrong_account"
    assert EMAIL in out.detail


def test_a_walk_that_never_reaches_settings_is_not_a_pass(monkeypatch):
    ctx, _ = _walk(monkeypatch, *(["welcome"] * cl.WALK_STEPS))
    out = cl.verify_account(ctx)
    assert out is not None and out.reason == "session_unverified"
    assert out.kind == "fatal"


def test_the_flow_is_wired_to_the_product_and_the_panel_serves_it():
    from geelark_farm import accounts, builder

    assert accounts.SERVED["claude"] == ("email_code_customer",)
    assert accounts.SERVED["spotify"] == ()
    assert builder._product_of(type("R", (), {"values": {"Product": "claude"}})()) == "claude"
    assert builder._product_of(type("R", (), {"values": {}})()) == "chatgpt"
    assert builder._product_of(None) == "chatgpt"

    class S:
        target_package = "com.openai.chatgpt"

    flow, package = builder._flow_for(
        S(), type("R", (), {"values": {"Product": "claude"}})())
    assert flow is cl and package == "com.anthropic.claude"
    flow, package = builder._flow_for(S(), type("R", (), {"values": {}})())
    assert flow.__name__.endswith("chatgpt_login")
    assert package == "com.openai.chatgpt"
    # The pool now hands a ready Claude customer to a phone.
    assert not accounts.held_back("claude", "email_code_customer", True)
    assert accounts.held_back("claude", "email_code_customer", False)
