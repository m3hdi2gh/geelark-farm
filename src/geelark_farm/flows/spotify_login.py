"""Sign a Spotify account into the Spotify app, once the app is installed.

The same router as the other logins and the same discipline: the screens
are matched from captures of a live phone, the credential goes in only by
the email-and-password path, and the home screen is believed only once
this run has put the password in - and then the app's own account page is
read back to name the address.

## The path

    welcome  ->  "Log in"  ->  email, "Continue"  ->  code page,
    "Log in with a password"  ->  password, "Log in"  ->  home

Captured on phone 3480, a bare phone with no Google account (2026-09-17).
The welcome screen is "Millions of songs. Free on Spotify." over "Sign up
free" and "Log in". The login page ("Log in to Spotify") has one box under
"Email", a "Continue" under it, then "Or log in with" Google and Facebook.
Continue does not go to a password: Spotify's default is an emailed code
("Enter the code we emailed you"), and the password is a link at the foot
of that page, "Log in with a password". The password page has the box
under "Password", a "Show password" eye and "Log in". The box reports
`password="false"` to uiautomator even though it masks what is typed, so
it is found as the page's only box, not by that flag.

A refused password is a dialog: "This email and password combination is
incorrect." with an OK. A right one lands on "Turn on notifications" with
"Not now" under it, and then the home screen: "Go to profile and
settings" top left and the four tabs, "Home, Tab 1 of 4" onwards.

## Reading the account back

"Go to profile and settings" opens the menu; "Settings and privacy" is on
it; the settings page's first row is "Account"; and the account page
names the address under "Email" - the app itself naming its session,
which is the standard `dumpsys account` sets for Google and what
`verify_account` reads. The username above it is Spotify's generated one
(`31u5nofnu...`), not the address, so it is the Email line that is read.

## What this flow will not do

**Continue with Google or Facebook.** A phone that has a Gmail on it
would be signed in as that Gmail, which looks like success and is the
wrong account; a bare phone has nothing there at all. Nothing matching
"google" or "facebook" is ever tapped. **Sign up.** Never tapped either.
**Take the emailed code.** The account is an address and a password;
the code page is passed through to the password page every time.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from .. import phones, screen, shell
from ..accounts import Credentials
from ..api import Client
from . import router
from .router import Outcome, Screen, act_wait, fill, still_loading

log = logging.getLogger(__name__)

PACKAGE = "com.spotify.music"

#: The welcome screen, as captured: its two ways in.
WELCOME_TEXTS = ("sign up free",)
WELCOME_BUTTON = "Log in"
#: The login page: the address box and the button under it.
LOGIN_PAGE_TEXTS = ("or log in with",)
LOGIN_TITLE = "log in to spotify"
CONTINUE_LABEL = "Continue"
#: The code page Spotify shows first, and the link off it to the password.
CODE_PAGE_TEXTS = ("enter the code we emailed you", "enter 6-digit code")
PASSWORD_LINK = "Log in with a password"
#: The password page: the eye beside the box is what tells it from the
#: address page, which has the same title and the same one box.
PASSWORD_PAGE_TEXTS = ("show password",)
LOGIN_BUTTON = "Log in"
#: Never tapped, whatever label happens to match. "Create account" is
#: on the page for an address Spotify does not know: this flow signs
#: accounts in and never makes one (2026-09-18). The two CTAs are
#: Spotify asking for money, and the farm never pays (2026-09-16).
NEVER_TAPPED = ("google", "facebook", "sign up", "create account",
                "update payment", "primarycta", "get premium")

#: The home screen (captured): the profile control and the tab strip.
#: "of 4" on a Premium account, "of 5" on a free one - which has a
#: Premium tab of its own (3604, 2026-09-18).
HOME_MARKERS = ("go to profile and settings", "home, tab 1 of")
#: An account whose Premium lapsed: Spotify fills the screen with
#: "PLAN PAUSED / Your last payment didn't work" and one button, Update
#: payment (3607, 2026-09-18). The system Back key puts the app back
#: where it was, with the account signed in behind it and nothing paid,
#: cancelled or pressed (3609, 2026-09-18) - so the login carries on and
#: says out loud that this account is not the Premium one it was sold
#: as. The link at the foot cancels the subscription; it is not taken,
#: because Back costs the account nothing.
PLAN_PAUSED_TEXTS = ("plan paused", "your last payment didn't work",
                     "your last payment did not work")
#: How many times Back is pressed at it before the page is called
#: something this run cannot get past.
PLAN_PAUSED_TRIES = 3
BACK_KEY = 4

#: The page the app shows when its first request after a clear does not
#: get through the exit (seen on 3480 right after `pm clear`, 2026-09-17):
#: "Something went wrong" over "Check your connection and try again." and
#: a "Try Again". Pressed, with a pause for the exit to settle; a page
#: that keeps coming back is the exit's problem, not the account's.
CONNECTION_ERROR_TEXTS = ("check your connection and try again",)
TRY_AGAIN_LABEL = "Try Again"
#: How many times Try Again is pressed before the exit is blamed rather
#: than the app. Reloading fixes it about as often as it does not, and
#: the other half is a proxy Spotify will not serve (the operator,
#: 2026-09-18) - so this ends as an EXIT reason, which makes the builder
#: swap the exit and try the same account again.
TRY_AGAIN_TIMES = 3

#: Anything containing one of these cannot proceed unattended.
FATAL_TEXTS = {
    # The dialog a refused password brings up (captured with a wrong
    # password on 3480), and the wordings Spotify has used on the web.
    "wrong_password": (
        "email and password combination is incorrect",
        "incorrect username or password", "incorrect password",
        "wrong password",
    ),
    # No account was ever made with this address (captured on 3480 with
    # a made-up one, 2026-09-18): "This email isn't linked to Spotify /
    # Looks like this address doesn't have a Spotify account yet." over
    # "Create account" and "Go back". Nothing on a phone fixes it and no
    # other password will either, so it is its own reason rather than a
    # refused password. Fatal is what keeps the flow off "Create
    # account", which would make an account nobody asked for.
    "no_such_account": (
        "is not linked to spotify", "isn't linked to spotify",
        "does not have a spotify account", "doesn't have a spotify account",
        "no account with that email", "could not find an account",
    ),
    "captcha_shown": (
        "verify you are human", "verify you're human", "i'm not a robot",
        "confirm you are human", "confirm you're human",
    ),
    "rate_limited": ("too many attempts", "too many requests",
                     "try again later"),
    "account_disabled": ("account has been disabled", "account is disabled",
                         "has been suspended", "account has been closed"),
}

FATAL_ADVICE = {
    "wrong_password":
        "Spotify would not take the password; try it by hand before "
        "changing the row",
    "no_such_account":
        "Spotify says no account was made with this address - the row is "
        "not an account yet, whatever its password says",
    "service_unreachable":
        "the app could not reach Spotify through this exit after "
        "several tries; the exit is the thing to change",
    "plan_paused":
        "the account is signed in, but Spotify says its plan is paused "
        "for an unpaid bill - the farm never pays, so a person decides "
        "what this row is worth",
    "captcha_shown":
        "Spotify is challenging this exit IP; a cleaner proxy is the fix "
        "and no code change helps",
    "rate_limited":
        "Spotify is rate-limiting sign-ins from this exit or for this "
        "address; wait, or use another exit",
    "account_disabled":
        "the app says the account is disabled; nothing on a phone changes "
        "that",
    "app_wrong_account":
        "the app's account page names a different address from the one "
        "this run typed - a session left by an earlier run, or the wrong "
        "account. The app is cleared on the next attempt",
    "session_unverified":
        "the home screen came up but the walk to the account page, which "
        "names the address, did not reach it; the sign-in is not believed "
        "without it",
}

#: Post-login onboarding, cleared the way the other flows clear it: an
#: allowlist of things that decline or proceed, never "press any button".
#: "Turn on notifications" is not on it on purpose - the declining word
#: is "Not now" (captured). Matched without regard to `clickable`: the
#: app renders its buttons as plain TextViews inside a clickable parent.
DISMISS_LABELS = (
    "Not now", "Skip", "Maybe later", "No thanks", "Later", "Dismiss",
    "Got it", "Continue", "Next", "Done",
    # Spotify's own upsell sheets carry no words on their controls: the
    # one that closes "Your trial of Premium features has ended" is
    # described as `tertiaryCtaDismiss`, the developer's own id, and the
    # one beside it - `primaryCta` - is the offer. Only the dismiss
    # (3605, 2026-09-18).
    "tertiaryCtaDismiss", "secondaryCtaDismiss",
)

#: The walk from home to the page that names the account (all captured).
PROFILE_DESC = "Go to profile and settings"
SETTINGS_LABEL = "Settings and privacy"
ACCOUNT_LABEL = "Account"
ACCOUNT_PAGE_TEXTS = ("account details",)
#: The settings page, by rows it has on every account - and the
#: page where "Account" means the account page rather than the
#: profile menu's "Add account".
SETTINGS_PAGE_TEXTS = ("parental controls", "about and support")
EMAIL_TEXT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.ASCII)
#: How many screens the walk may take before it is called not a pass.
WALK_STEPS = 8


@dataclass
class Context(router.Context):
    """The generic context plus the account being signed in."""

    creds: Credentials = None                                   # type: ignore
    package: str = PACKAGE
    #: Set once this run has put the password in. A home screen before
    #: that is not this run's doing and is not believed.
    submitted_password: bool = False
    #: How many times the address was submitted from the login page.
    email_submissions: int = 0
    #: How many times the app has said it could not reach Spotify.
    reloads: int = 0
    #: Whether this account's plan turned out to be paused - said once,
    #: however many times the page comes back.
    plan_paused: bool = False

    @property
    def signed_something_in(self) -> bool:
        return self.submitted_password


# ------------------------------------------------------------------ screens
def on_welcome(ctx: Context) -> bool:
    return ctx.has(*WELCOME_TEXTS) and ctx.find(WELCOME_BUTTON) is not None


def on_login_page(ctx: Context) -> bool:
    """The address page: the title, a box, and the other ways in under it
    - which is what tells it from the password page."""
    return (ctx.has(LOGIN_TITLE) and ctx.has(*LOGIN_PAGE_TEXTS)
            and screen.find_input(ctx.elements) is not None)


def on_code_page(ctx: Context) -> bool:
    return ctx.has(*CODE_PAGE_TEXTS)


def on_password_page(ctx: Context) -> bool:
    return ctx.has(*PASSWORD_PAGE_TEXTS) and screen.find_input(ctx.elements) is not None


def home_on_screen(ctx: Context) -> bool:
    """Whether the home screen is up. NOT whether anyone is signed in."""
    return ctx.has(*HOME_MARKERS)


def verified_on_device(ctx: Context) -> bool:
    """Home only counts once this run has put the password in - the rule
    every flow here pays for (a phone handed over with nobody in the app,
    2026-08-08)."""
    return ctx.signed_something_in and home_on_screen(ctx)


def _fatal_reason(ctx: Context) -> str | None:
    for reason, needles in FATAL_TEXTS.items():
        if ctx.has(*needles):
            return reason
    return None


def _tap_safely(ctx: Context, label: str) -> bool:
    """Tap the control with this label, unless the match is one of the
    things this flow never touches."""
    element = ctx.find(label)
    if element is None:
        return False
    if any(word in element.label.casefold() for word in NEVER_TAPPED):
        log.warning("not tapping %r for %r", element.label, label)
        return False
    return screen.tap_element(ctx.client, ctx.phone_id, element)


def act_fatal(ctx: Context) -> Outcome:
    reason = _fatal_reason(ctx) or "unknown_fatal"
    # Both ways: a refusal Spotify draws in a web view leaves a hierarchy
    # with one line in it, and the picture is what a new wording is read
    # off next week (2026-09-17).
    kept = ctx.keep(reason)
    # One line naming the account and the page, at WARNING: the reason
    # reaches the row and the note, and this is what a week of these is
    # read from on the logs page (the operator, 2026-09-18).
    log.warning("Spotify refused %s: %s (%s)", ctx.creds.email, reason,
                ", ".join(kept) or "no capture")
    return Outcome("fatal", reason,
                   FATAL_ADVICE.get(reason,
                                    "the screen says this cannot proceed "
                                    "unattended"),
                   artifacts=kept)


def act_welcome(ctx: Context) -> Outcome | None:
    if _tap_safely(ctx, WELCOME_BUTTON):
        log.info("opening the login page")
        time.sleep(4)
    return None


def act_login_page(ctx: Context) -> Outcome | None:
    """Type the address and press Continue - never Google or Facebook.

    `fill` clears whatever the box holds first, so a second visit after a
    slip does not append to the address already in it (the Claude flow's
    lesson, 2026-09-16).
    """
    box = screen.find_input(ctx.elements)
    if box is None:
        return None
    # Case-insensitively: the app's own box capitalised the first two
    # letters of an address typed into it (zz... came back ZZ..., 3480
    # 2026-09-18), and Spotify treats the address as one either way.
    typed = box.text.strip().casefold() == ctx.creds.email.casefold()
    if not typed:
        log.info("entering the Spotify account's email address")
        if not fill(ctx, box, ctx.creds.email):
            return None
        ctx.refresh()
    if _tap_safely(ctx, CONTINUE_LABEL):
        ctx.email_submissions += 1
        log.info("submitted the address")
        time.sleep(5)
        return None
    # No Continue found: the keyboard's enter submits the same field.
    shell.keyevent(ctx.client, ctx.phone_id, 66)
    ctx.email_submissions += 1
    log.info("submitted the address with the keyboard")
    time.sleep(5)
    return None


def act_code_page(ctx: Context) -> Outcome | None:
    """Spotify offers an emailed code first. The account is an address
    and a password, so the link at the foot of the page is taken."""
    if _tap_safely(ctx, PASSWORD_LINK):
        log.info("taking the password path instead of the emailed code")
        time.sleep(4)
    return None


def act_password(ctx: Context) -> Outcome | None:
    box = screen.find_input(ctx.elements)
    if box is None:
        return None
    log.info("entering the Spotify account's password")
    # Told it is a password box, since the dump does not say so: `fill`
    # reads a plain box back and corrects it, and read this one back as
    # eleven dots - then cleared and retyped a password that was right
    # (3480, 2026-09-17).
    if not fill(ctx, replace(box, password=True), ctx.creds.password):
        return None
    ctx.refresh()
    if not _tap_safely(ctx, LOGIN_BUTTON):
        # The button is a TextView the dump can miss mid-paint; the
        # keyboard's enter submits the same form.
        shell.keyevent(ctx.client, ctx.phone_id, 66)
    # Recorded because success depends on it: home means nothing unless
    # this run put the password in. See verified_on_device.
    ctx.submitted_password = True
    time.sleep(8)
    return None


def on_connection_error(ctx: Context) -> bool:
    return ctx.has(*CONNECTION_ERROR_TEXTS)


def act_try_again(ctx: Context) -> Outcome | None:
    """Reload, up to a point. Past it the exit is what to change, and
    saying so is what makes the builder change it - the account is never
    judged by this (2026-09-18)."""
    ctx.reloads += 1
    if ctx.reloads > TRY_AGAIN_TIMES:
        kept = ctx.keep("service-unreachable")
        return Outcome("fatal", "service_unreachable",
                       f"the app said it could not reach Spotify "
                       f"{ctx.reloads} times through this exit: "
                       f"{FATAL_ADVICE['service_unreachable']}",
                       artifacts=kept)
    log.info("the app could not reach Spotify (%d of %d); waiting and "
             "trying again", ctx.reloads, TRY_AGAIN_TIMES)
    time.sleep(6)
    _tap_safely(ctx, TRY_AGAIN_LABEL)
    time.sleep(6)
    return None


def on_plan_paused(ctx: Context) -> bool:
    return ctx.has(*PLAN_PAUSED_TEXTS)


def act_plan_paused(ctx: Context) -> Outcome | None:
    """Back out of Spotify's unpaid-bill page and carry on.

    The account is signed in behind it; the page is a nag about the
    subscription, not about the sign-in. Back was tried before the
    link at its foot for the obvious reason: cancelling somebody's
    subscription to get past a page is a large thing to do for a small
    reason, and Back does it for nothing (3609, 2026-09-18).

    Said at WARNING once, because a phone handed over with this account
    on it is not the Premium account it was sold as, and the only place
    that is written down is here.
    """
    if ctx.seen.get("plan_paused", 0) > PLAN_PAUSED_TRIES:
        kept = ctx.keep("plan-paused")
        return Outcome("fatal", "plan_paused", FATAL_ADVICE["plan_paused"],
                       artifacts=kept)
    if not ctx.plan_paused:
        ctx.plan_paused = True
        ctx.keep("plan-paused")
        log.warning("%s: Spotify says this account's plan is paused for an "
                    "unpaid bill. Backing out of the page - the account is "
                    "signed in, and nothing is paid or cancelled",
                    ctx.creds.email)
    shell.keyevent(ctx.client, ctx.phone_id, BACK_KEY)
    time.sleep(4)
    return None


def act_dismiss(ctx: Context) -> Outcome | None:
    tapped = screen.tap_first_present(ctx.client, ctx.phone_id, ctx.elements,
                                      DISMISS_LABELS, clickable_only=False)
    if tapped:
        log.info("dismissed %r", tapped)
        time.sleep(3)
    return None


def act_reset_app(ctx: Context) -> Outcome | None:
    """A home screen this run did not sign in to: an earlier session.
    `pm clear` puts the app back at its welcome screen."""
    ctx.save("home-not-earned")
    log.warning("the home screen is up and this run has not signed in; "
                "clearing the app so its state is known")
    shell.run(ctx.client, ctx.phone_id, f"pm clear {ctx.package}")
    time.sleep(3)
    launch(ctx.client, ctx.phone_id, ctx.package)
    return None


LAUNCH_ATTEMPTS = 3


def launch(client: Client, phone_id: str, package: str = PACKAGE) -> bool:
    """Bring the app to the front, and check that it came."""
    for attempt in range(1, LAUNCH_ATTEMPTS + 1):
        shell.run(client, phone_id,
                  f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        time.sleep(8)
        front = shell.foreground_package(client, phone_id)
        if not front or front == package:
            return True
        log.warning("%s is in front, not %s (attempt %d/%d)",
                    front, package, attempt, LAUNCH_ATTEMPTS)
        time.sleep(5)
    return False


# ------------------------------------------------- reading the account back
def account_email_on(elements: list[screen.Element]) -> str | None:
    """The address the account page names: the first address on it that
    is not in a text box. Never a text box: the login page's box holds
    the address this run typed, which is a claim, not the app naming its
    session."""
    return next((e.label.strip() for e in elements
                 if not e.is_input and EMAIL_TEXT.fullmatch(e.label.strip())),
                None)


def on_account_page(ctx: Context) -> bool:
    return ctx.has(*ACCOUNT_PAGE_TEXTS)


def verify_account(ctx: Context) -> Outcome | None:
    """Read the signed-in address out of the app's own account page.

    The home check proves a home screen; it cannot prove whose. This
    walks home -> profile -> Settings and privacy -> Account and reads
    the Email line, which is the app itself naming its session. A walk
    that cannot reach the page is a failure, not a pass; an address that
    is not the one just typed is its own reason.
    """
    for _step in range(WALK_STEPS):
        ctx.check()
        ctx.refresh()
        if not ctx.elements:
            time.sleep(3)
            continue
        if on_account_page(ctx):
            named = account_email_on(ctx.elements)
            if named and named.casefold() == ctx.creds.email.casefold():
                log.info("the app's account page names %s", named)
                return None
            kept = ctx.keep("app-wrong-account")
            return Outcome("fatal", "app_wrong_account",
                           f"the app names {named or 'nobody'}, not "
                           f"{ctx.creds.email}: "
                           f"{FATAL_ADVICE['app_wrong_account']}",
                           artifacts=kept)
        # An offer over the page takes every tap aimed at what is under
        # it, so it goes first: four phones walked to a settings page
        # they never reached because "Your trial of Premium features has
        # ended" was on top of the home screen, and the walk tapped the
        # profile control behind it eight times (3605, 2026-09-18).
        cleared = screen.tap_first_present(
            ctx.client, ctx.phone_id, ctx.elements, DISMISS_LABELS,
            clickable_only=False)
        if cleared:
            log.info("cleared %r on the way to the account page", cleared)
            time.sleep(3)
            continue
        # Which page is under the hand decides what to tap. Asking for
        # "Account" anywhere was the trap: the profile menu's own "Add
        # account" answers to that word, so the tap had to be held back
        # until the settings page - and the test for it was "Log out is
        # on screen", which is below the fold on a free account's
        # settings page. Four phones stood on the page they wanted and
        # never pressed the row (3604, 2026-09-18).
        if ctx.has(*SETTINGS_PAGE_TEXTS):
            label = ACCOUNT_LABEL
        elif ctx.find(SETTINGS_LABEL) is not None:
            label = SETTINGS_LABEL
        else:
            label = PROFILE_DESC
        element = ctx.find(label)
        if element is not None:
            log.info("walking to the account page by %r", label)
            screen.tap_element(ctx.client, ctx.phone_id, element)
            time.sleep(4)
        else:
            time.sleep(3)
    # Both ways: where a walk stopped is a page to read next week, and
    # the hierarchy of an offer drawn over another page says little.
    kept = ctx.keep("session-unverified")
    return Outcome("fatal", "session_unverified",
                   FATAL_ADVICE["session_unverified"], artifacts=kept)


# Order matters: the first match wins.
SCREENS: list[Screen] = [
    Screen("fatal", lambda c: _fatal_reason(c) is not None, act_fatal,
           max_visits=1),
    Screen("loading", still_loading, act_wait, max_visits=25),
    Screen("connection_error", on_connection_error, act_try_again,
           max_visits=TRY_AGAIN_TIMES + 2),
    Screen("plan_paused", on_plan_paused, act_plan_paused,
           max_visits=PLAN_PAUSED_TRIES + 2),
    Screen("code_page", on_code_page, act_code_page, max_visits=3),
    # The password page before the address page: both carry the title
    # and one box, and only this one carries the eye.
    Screen("password_entry", on_password_page, act_password, max_visits=3),
    Screen("email_entry", on_login_page, act_login_page, max_visits=3),
    Screen("welcome", on_welcome, act_welcome, max_visits=3),
    Screen("logged_out_home",
           lambda c: home_on_screen(c) and not c.signed_something_in,
           act_reset_app, max_visits=2),
    Screen("dismissable",
           lambda c: screen.find_first(c.elements, DISMISS_LABELS,
                                       clickable_only=False) is not None,
           act_dismiss, max_visits=6),
]


def reset(client: Client, phone_id: str, package: str = PACKAGE) -> None:
    shell.run(client, phone_id, f"pm clear {package}")
    time.sleep(3)


def sign_in(client: Client, phone_id: str, creds: Credentials, *,
            package: str = PACKAGE, budget_seconds: float = 900,
            artifact_dir: Path | None = None,
            fresh: bool = False,
            codes=None,
            watch: Callable[[], None] | None = None) -> Outcome:
    """Drive the Spotify login to a named outcome. Returns rather than
    raises, like every flow: a batch records why and moves on. `codes`
    is accepted for the builder's sake and unused: nothing here is
    answered by a code."""
    if not shell.package_installed(client, phone_id, package):
        return Outcome("fatal", "app_not_installed",
                       f"{package} is not on this phone")
    if fresh:
        reset(client, phone_id, package)
    if not launch(client, phone_id, package):
        return Outcome("unknown", "app_would_not_start",
                       f"{package} did not come to the front after "
                       f"{LAUNCH_ATTEMPTS} attempts")
    ctx = Context(client=client, phone_id=phone_id, creds=creds,
                  package=package, artifact_dir=artifact_dir)

    def logged_in() -> Outcome | None:
        if ctx.elements and verified_on_device(ctx):
            return Outcome("success", "logged_in",
                           f"{creds.email}: the home screen is up after "
                           f"this run's password")
        return None

    out = router.drive(ctx, SCREENS, is_done=logged_in, watch=watch,
                       budget_seconds=budget_seconds, logger=log)
    if not out.ok:
        return out
    # The router proved a home screen. This proves whose it is.
    failed = verify_account(ctx)
    if failed is not None:
        failed.trail = out.trail
        return failed
    return Outcome("success", "logged_in",
                   f"the app's own account page names {creds.email}",
                   artifacts=out.artifacts, trail=out.trail)


def sign_in_on_phone(client: Client, phone_id: str, creds: Credentials,
                     **kwargs) -> Outcome:
    phones.ensure_running(client, phone_id)
    return sign_in(client, phone_id, creds, **kwargs)
