"""The Spotify sign-in flow, against the screens captured on phone 3480
(2026-09-17): welcome, the login page bare and typed, the code page, the
password page bare and typed, a refused password, the notifications
prompt, home, the profile menu, settings and the account page that names
the address."""

from __future__ import annotations

from pathlib import Path

import pytest

from geelark_farm import screen
from geelark_farm.accounts import Credentials
from geelark_farm.flows import spotify_login as sl

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EMAIL = "jacknikolnasli@gmail.com"
CREDS = Credentials(email=EMAIL, password="Spotify@123", totp_secret="")


def elements_of(name: str) -> list[screen.Element]:
    return screen.parse((FIXTURES / f"spotify-{name}.xml").read_text(
        encoding="utf-8"))


def ctx_for(name: str) -> sl.Context:
    ctx = sl.Context(client=None, phone_id="P", creds=CREDS)
    ctx.elements = elements_of(name)
    ctx.blob = screen.texts(ctx.elements)
    return ctx


def matched(ctx: sl.Context) -> str | None:
    return next((s.name for s in sl.SCREENS if s.match(ctx)), None)


@pytest.fixture
def phone(monkeypatch):
    """A device that records what was done to it instead of doing it."""
    done = {"filled": [], "tapped": [], "keys": [], "run": [], "launched": 0}

    def fill(ctx, element, text):
        done["filled"].append(text)
        return True

    monkeypatch.setattr(sl, "fill", fill)
    monkeypatch.setattr(sl.screen, "tap_element",
                        lambda c, p, e: done["tapped"].append(e.label) or True)
    monkeypatch.setattr(sl.shell, "keyevent",
                        lambda c, p, code: done["keys"].append(code))
    monkeypatch.setattr(sl.shell, "run",
                        lambda c, p, cmd, **k: done["run"].append(cmd) or "")
    monkeypatch.setattr(sl, "launch",
                        lambda *a, **k: done.__setitem__(
                            "launched", done["launched"] + 1) or True)
    monkeypatch.setattr(sl.time, "sleep", lambda *a: None)
    monkeypatch.setattr(sl.Context, "refresh", lambda self: None)
    return done


# ---------------------------------------------------------------- screens
def test_every_captured_screen_is_recognised_as_itself():
    assert matched(ctx_for("welcome")) == "welcome"
    assert matched(ctx_for("login-page")) == "email_entry"
    assert matched(ctx_for("login-typed")) == "email_entry"
    assert matched(ctx_for("code-page")) == "code_page"
    assert matched(ctx_for("password-page")) == "password_entry"
    assert matched(ctx_for("password-typed")) == "password_entry"
    assert matched(ctx_for("wrong-password")) == "fatal"
    assert matched(ctx_for("notifications")) == "dismissable"
    assert matched(ctx_for("connection-error")) == "connection_error"
    # Home, before this run has typed a password, is a home this run did
    # not earn - and so are the pages under it, which carry the tab strip.
    assert matched(ctx_for("home")) == "logged_out_home"
    assert matched(ctx_for("settings")) == "logged_out_home"


def test_the_welcome_screen_opens_the_login_page_never_sign_up(phone):
    ctx = ctx_for("welcome")
    assert sl.act_welcome(ctx) is None
    assert phone["tapped"] == ["Log in"]


def test_the_login_page_takes_the_email_path_never_google(phone):
    """Untyped: the address goes in and Continue is pressed. Typed: not
    typed again. Google and Facebook are never what gets tapped."""
    ctx = ctx_for("login-page")
    assert sl.act_login_page(ctx) is None
    assert phone["filled"] == [EMAIL]
    assert phone["tapped"] == ["Continue"] and phone["keys"] == []
    assert ctx.email_submissions == 1

    ctx = ctx_for("login-typed")
    assert sl.act_login_page(ctx) is None
    assert phone["filled"] == [EMAIL], "already typed: not typed again"
    assert all(t.casefold() not in ("google", "facebook", "sign up")
               for t in phone["tapped"])


def test_the_code_page_is_passed_through_to_the_password(phone):
    ctx = ctx_for("code-page")
    assert sl.act_code_page(ctx) is None
    assert phone["tapped"] == ["Log in with a password"]
    assert phone["filled"] == [], "no code is ever typed"


def test_the_password_page_types_the_password_and_believes_home_only_then(
        phone):
    ctx = ctx_for("password-page")
    assert not ctx.signed_something_in
    assert sl.act_password(ctx) is None
    assert phone["filled"] == ["Spotify@123"]
    assert phone["tapped"] == ["Log in"]
    assert ctx.submitted_password
    # The box reports password="false" to uiautomator; it is found as the
    # page's box, not by that flag.
    assert screen.find_input(ctx.elements, password=True) is None
    # And only now is the home screen worth believing.
    home = ctx_for("home")
    assert not sl.verified_on_device(home)
    home.submitted_password = True
    assert sl.verified_on_device(home)


def test_a_refused_password_is_fatal_and_named():
    ctx = ctx_for("wrong-password")
    out = sl.act_fatal(ctx)
    assert out.kind == "fatal" and out.reason == "wrong_password"


def test_the_notifications_prompt_is_declined_not_accepted(phone):
    ctx = ctx_for("notifications")
    assert sl.act_dismiss(ctx) is None
    assert phone["tapped"] == ["Not now"]


def test_a_dropped_connection_is_tried_again_not_blamed(phone):
    """Seen right after `pm clear` on 3480: the app's first request did
    not get through the exit. Try Again is pressed; the account is not
    judged."""
    ctx = ctx_for("connection-error")
    assert sl.act_try_again(ctx) is None
    assert phone["tapped"] == ["Try Again"]
    assert sl._fatal_reason(ctx) is None


def test_a_home_this_run_did_not_earn_is_cleared(phone):
    ctx = ctx_for("home")
    assert sl.act_reset_app(ctx) is None
    assert phone["run"] == ["pm clear com.spotify.music"]
    assert phone["launched"] == 1


# ----------------------------------------------------------- the read-back
def test_the_account_page_names_the_address_and_the_others_do_not():
    assert sl.account_email_on(elements_of("account")) == EMAIL
    # The login page's box holds the typed address: a claim, not the app
    # naming its session, and never read as one.
    assert sl.account_email_on(elements_of("login-typed")) is None
    assert sl.account_email_on(elements_of("home")) is None
    assert sl.account_email_on(elements_of("settings")) is None
    # The code page masks the address (j************i@g***l.com).
    assert sl.account_email_on(elements_of("code-page")) is None


def _walk(monkeypatch, pages: list[str], creds: Credentials = CREDS):
    """A device whose screen is the next captured page each refresh."""
    tapped: list[str] = []
    ctx = sl.Context(client=None, phone_id="P", creds=creds)
    queue = list(pages)

    def refresh(self):
        name = queue.pop(0) if queue else pages[-1]
        self.elements = elements_of(name)
        self.blob = screen.texts(self.elements)

    monkeypatch.setattr(sl.Context, "refresh", refresh)
    monkeypatch.setattr(sl.screen, "tap_element",
                        lambda c, p, e: tapped.append(e.label) or True)
    monkeypatch.setattr(sl.time, "sleep", lambda *a: None)
    return ctx, tapped


def test_verify_account_walks_the_real_screens(monkeypatch):
    ctx, tapped = _walk(monkeypatch,
                        ["home", "profile-menu", "settings", "account"])
    assert sl.verify_account(ctx) is None
    assert tapped == ["Go to profile and settings", "Settings and privacy",
                      "Account"]


def test_a_different_account_in_the_app_is_fatal(monkeypatch):
    other = Credentials(email="somebody@else.com", password="pw",
                        totp_secret="")
    ctx, _ = _walk(monkeypatch, ["home", "profile-menu", "settings",
                                 "account"], creds=other)
    out = sl.verify_account(ctx)
    assert out is not None and out.reason == "app_wrong_account"
    assert EMAIL in out.detail


def test_a_walk_that_never_reaches_the_account_page_is_not_a_pass(
        monkeypatch):
    ctx, _ = _walk(monkeypatch, ["home"])
    out = sl.verify_account(ctx)
    assert out is not None and out.reason == "session_unverified"


def test_sign_in_refuses_a_phone_without_the_app(monkeypatch):
    monkeypatch.setattr(sl.shell, "package_installed", lambda *a, **k: False)
    out = sl.sign_in(None, "P", CREDS)
    assert out.kind == "fatal" and out.reason == "app_not_installed"


# ------------------------------------------------------------- the wiring
def test_the_flow_is_wired_to_the_product_and_normal_stays_off_the_claim():
    import inspect

    from geelark_farm import accounts, builder
    from geelark_farm.store import pgpool

    # Nothing serves the product: the automatic claim knows no
    # categories, so it leaves every Spotify row where it is and Send
    # names one by hand (builder._pick_named_app).
    assert accounts.SERVED["spotify"] == ()
    assert accounts.held_back("spotify", "password", False)

    class S:
        target_package = "com.openai.chatgpt"

    flow, package = builder._flow_for(
        S(), type("R", (), {"values": {"Product": "spotify"}})())
    assert flow is sl and package == "com.spotify.music"
    # A named row is the person's choice: `claim_this` asks only whether
    # the row is free, never what the automatic claim holds back.
    assert "held_back" not in inspect.getsource(pgpool.PgAppPool.claim_this)
    assert "claim_this" in inspect.getsource(builder._pick_named_app)
