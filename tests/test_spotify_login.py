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
    assert matched(ctx_for("no-such-account")) == "fatal"
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


def test_an_address_spotify_does_not_know_is_its_own_reason(phone):
    """"This email isn't linked to Spotify" over a "Create account" the
    flow must never press: this tool signs accounts in and never makes
    one (captured on 3480, 2026-09-18)."""
    ctx = ctx_for("no-such-account")
    out = sl.act_fatal(ctx)
    assert out.kind == "fatal" and out.reason == "no_such_account"
    assert "no account was made" in out.detail

    # And nothing on that page is tappable by the dismiss list.
    assert sl.act_dismiss(ctx) is None
    assert phone["tapped"] == []
    assert not sl._tap_safely(ctx, "Create account")


def test_a_connection_error_that_will_not_clear_blames_the_exit(phone):
    """Reloading fixes it about half the time; the other half is an exit
    Spotify will not serve, and saying so is what makes the builder swap
    it (the operator, 2026-09-18)."""
    ctx = ctx_for("connection-error")
    for _ in range(sl.TRY_AGAIN_TIMES):
        assert sl.act_try_again(ctx) is None
    assert phone["tapped"] == ["Try Again"] * sl.TRY_AGAIN_TIMES
    out = sl.act_try_again(ctx)
    assert out is not None and out.reason == "service_unreachable"

    from geelark_farm import failures

    assert failures.verdict("service_unreachable", "Spotify").needs_a_new_exit
    # And the account is not judged by it.
    assert not failures.verdict("service_unreachable").costs_the_credential


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


def test_a_page_in_another_app_is_named_rather_than_unknown(monkeypatch):
    """Spotify hands a challenge to the browser: the dump is Chrome's,
    nothing matches, and "nothing matched" sends whoever reads it looking
    for a page in the wrong app. The router names the app in front and
    keeps a picture, because a web view leaves almost no hierarchy (the
    operator, 2026-09-18)."""
    from geelark_farm.flows import router

    shot = {"taken": []}
    ctx = sl.Context(client=object(), phone_id="P", creds=CREDS)
    chrome = screen.parse(
        '<hierarchy><node text="Verify you are human" class="x"'
        ' bounds="[0,0][9,9]"/></hierarchy>')

    def refresh(self):
        self.elements = chrome
        self.blob = screen.texts(chrome)

    monkeypatch.setattr(sl.Context, "refresh", refresh)
    monkeypatch.setattr(router.Context, "keep",
                        lambda self, name: shot["taken"].append(name) or [])
    monkeypatch.setattr(router.shell, "foreground_package",
                        lambda c, p: "com.android.chrome")
    monkeypatch.setattr(router.time, "sleep", lambda *a: None)
    monkeypatch.setattr(sl.time, "sleep", lambda *a: None)

    # No screen of the flow's claims a browser page - `fatal` would, on
    # the captcha words, so this asks the router with no screens at all.
    out = router.drive(ctx, [], is_done=lambda: None, budget_seconds=30)
    assert out.reason == "left_the_app"
    assert "com.android.chrome" in out.detail and "spotify" in out.detail
    assert shot["taken"] == ["unknown-screen"], "the page is kept, both ways"

    from geelark_farm import failures

    said = failures.verdict("left_the_app", "Spotify")
    assert "Spotify handed the sign-in to another app" == said.seen
    assert not said.costs_the_credential, "the account is not judged by it"


def test_the_walk_presses_the_account_row_on_a_free_accounts_settings():
    """The row was held back until "Log out" was on screen, to keep the
    profile menu's own "Add account" from answering to the word. On a
    free account's settings page Log out is below the fold, so four
    phones stood on the page they wanted and never pressed the row
    (3604, 2026-09-18). Which page it is on decides instead."""
    from geelark_farm import screen

    menu = ctx_for("profile-menu")
    assert not menu.has(*sl.SETTINGS_PAGE_TEXTS)
    assert menu.find(sl.SETTINGS_LABEL) is not None
    # And the word that used to be pressed there is not the row.
    assert menu.find(sl.ACCOUNT_LABEL).label == "Add account"

    for page in ("settings", "settings-free"):
        ctx = ctx_for(page)
        assert ctx.has(*sl.SETTINGS_PAGE_TEXTS), page
        assert ctx.find(sl.ACCOUNT_LABEL).label == "Account", page
    # The free account's page is the one that has no Log out on it.
    assert not ctx_for("settings-free").has("log out")
    # Its home screen has five tabs, not four.
    free_home = screen.texts(elements_of("settings-free"))
    assert "home, tab 1 of 5" in free_home
    assert sl.HOME_MARKERS[1] == "home, tab 1 of"


def test_an_offer_over_the_page_is_cleared_before_the_walk_taps(monkeypatch):
    """"Your trial of Premium features has ended" sits over the home
    screen, takes every tap aimed at what is under it, and closes with a
    control whose only name is the developer's own id (3605,
    2026-09-18)."""
    from geelark_farm import screen

    offer = elements_of("trial-ended")
    # The home screen is under it, which is why the walk kept tapping.
    assert "go to profile and settings" in screen.texts(offer)
    closer = screen.find_first(offer, sl.DISMISS_LABELS, clickable_only=False)
    assert closer is not None and closer.label == "tertiaryCtaDismiss"
    # And the offer beside it is never what gets pressed.
    ctx = ctx_for("trial-ended")
    assert not sl._tap_safely(ctx, "primaryCta")

    ctx, tapped = _walk(monkeypatch, ["trial-ended", "profile-menu",
                                      "settings-free", "account"])
    assert sl.verify_account(ctx) is None
    assert tapped == ["tertiaryCtaDismiss", "Settings and privacy",
                      "Account"]


def test_an_account_whose_plan_is_paused_is_backed_out_of_not_paid(phone):
    """Spotify fills the screen with PLAN PAUSED and one button, Update
    payment. The account is signed in behind it, and the system Back key
    puts the app back where it was - so the login carries on, nothing is
    paid, and the subscription the link at the foot would cancel is left
    alone (3607 and 3609, 2026-09-18)."""
    ctx = ctx_for("plan-paused")
    assert matched(ctx) == "plan_paused", "a page to get past, not a wall"
    assert sl._fatal_reason(ctx) is None

    ctx.seen["plan_paused"] = 1
    assert sl.act_plan_paused(ctx) is None
    assert phone["keys"] == [sl.BACK_KEY]
    assert phone["tapped"] == [], "nothing on that page is pressed"
    assert ctx.plan_paused, "and it is remembered, to be said once"

    # Neither the button nor the link that cancels the subscription.
    assert not sl._tap_safely(ctx, "Update payment")
    assert sl.act_dismiss(ctx) is None and phone["tapped"] == []

    # A page that will not go stops the run rather than being ignored.
    ctx.seen["plan_paused"] = sl.PLAN_PAUSED_TRIES + 1
    out = sl.act_plan_paused(ctx)
    assert out is not None and out.reason == "plan_paused"

    from geelark_farm import failures

    said = failures.verdict("plan_paused", "Spotify")
    assert "plan is paused" in said.seen and said.costs_the_credential


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


# ---------------------------------------------- the browser's two pages
# Both captured on 3644, the first warm phone an `error` account was ever
# sent to (2026-09-19). Spotify hands the sign-in to Chrome after the
# password: Chrome's own first-run page, then challenge.spotify.com with
# a reCAPTCHA tick box on it.
def test_chromes_first_run_page_is_its_own_screen_not_a_thing_to_dismiss():
    """The dismiss list's "Continue" found "Continue as Risky" on this
    page and signed the browser into the phone's Gmail - the one thing
    this flow says it never does (3644, 2026-09-19)."""
    ctx = ctx_for("chrome-setup")
    assert matched(ctx) == "chrome_setup"
    # Both halves of the bug: the page is claimed before `dismissable`
    # gets it, and what `dismissable` would have taken is now refused.
    assert sl._dismissable(ctx) is None


def test_chrome_is_declined_never_signed_in(phone):
    ctx = ctx_for("chrome-setup")
    assert sl.act_chrome_setup(ctx) is None
    assert phone["tapped"] == ["Use without an account"]


def test_a_chrome_setup_page_we_cannot_decline_stops_rather_than_guesses(
        phone, monkeypatch):
    ctx = ctx_for("chrome-setup")
    ctx.elements = [e for e in ctx.elements
                    if "without an account" not in e.label.lower()]
    ctx.blob = screen.texts(ctx.elements)
    monkeypatch.setattr(sl.Context, "keep", lambda self, name: [])
    out = sl.act_chrome_setup(ctx)
    assert out is not None and out.reason == "chrome_setup_unknown"
    assert phone["tapped"] == []


def test_the_challenge_page_is_recognised_and_is_not_a_fatal_on_sight():
    """It used to be: `captcha_shown` was in FATAL_TEXTS, so the flow gave
    up on the page the operator passes with two taps."""
    ctx = ctx_for("challenge")
    assert matched(ctx) == "challenge"
    assert sl._fatal_reason(ctx) is None
    box = sl._robot_box(ctx)
    assert box is not None and not box.checked
    assert box.bounds == "[132,526][181,575]"


def test_the_tick_box_is_tapped_once_and_then_left_alone(phone):
    ctx = ctx_for("challenge")
    assert sl.act_challenge(ctx) is None
    assert phone["tapped"] == ["I'm not a robot"]
    assert ctx.ticked_on == 0
    # reCAPTCHA reads unticked for a few seconds while it decides, and a
    # second tap unticks what the first ticked (1787 and 1788).
    for visit in range(1, sl.TICK_AGAIN):
        ctx.seen["challenge"] = visit
        assert sl.act_challenge(ctx) is None
    assert phone["tapped"] == ["I'm not a robot"]
    # Long enough, and it never took: tapped again, not waited on forever.
    ctx.seen["challenge"] = sl.TICK_AGAIN
    assert sl.act_challenge(ctx) is None
    assert phone["tapped"] == ["I'm not a robot", "I'm not a robot"]


def test_continue_is_pressed_once_the_box_is_ticked(phone):
    ctx = ctx_for("challenge")
    ctx.elements = [e if "not a robot" not in e.label.lower()
                    else screen.Element(**{**e.__dict__, "checked": True})
                    for e in ctx.elements]
    ctx.blob = screen.texts(ctx.elements)
    assert sl.act_challenge(ctx) is None
    assert phone["tapped"] == ["Continue"]
    assert ctx.continues == 1


def test_a_challenge_that_will_not_clear_ends_where_it_did_before(
        phone, monkeypatch):
    monkeypatch.setattr(sl.Context, "keep", lambda self, name: [])
    ctx = ctx_for("challenge")
    ctx.seen["challenge"] = sl.CHALLENGE_VISITS - 1
    out = sl.act_challenge(ctx)
    assert out is not None and out.kind == "fatal"
    assert out.reason == "captcha_shown"
    # And pressing Continue at a page that keeps coming back is bounded.
    ctx = ctx_for("challenge")
    ctx.continues = sl.CONTINUE_TRIES
    ctx.ticked_on = 0
    ctx.elements = [e for e in ctx.elements
                    if "not a robot" not in e.label.lower()]
    ctx.blob = screen.texts(ctx.elements)
    out = sl.act_challenge(ctx)
    assert out is not None and out.reason == "captcha_shown"


def test_an_image_grid_is_named_rather_than_read_as_a_tick_that_failed(
        monkeypatch):
    monkeypatch.setattr(sl.Context, "keep", lambda self, name: [])
    ctx = ctx_for("challenge")
    ctx.blob = ctx.blob + " select all images with traffic lights"
    out = sl.act_challenge(ctx)
    assert out is not None and out.reason == "captcha_grid"

    from geelark_farm import failures

    said = failures.verdict("captcha_grid", "Spotify")
    assert said.blame == failures.EXIT


def test_a_dismiss_word_never_reaches_a_google_control_or_a_sizeless_link():
    """Two stray taps from one run: "Continue" took "Continue as Risky",
    and "Skip" took a web page's "Skip to content", which is laid out at
    [0,0][0,0] - so its centre is the screen's top-left corner."""
    ctx = ctx_for("challenge")
    skip = screen.find(ctx.elements, "Skip to content", clickable_only=False)
    assert skip is not None and skip.bounds == "[0,0][0,0]"
    assert sl._no_size(skip)
    found = sl._dismissable(ctx)
    assert found is None or found.label != "Skip to content"
