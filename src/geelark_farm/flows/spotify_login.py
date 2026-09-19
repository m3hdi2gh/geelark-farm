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

## The challenge, which is not in the app

On a phone Spotify does not trust, the password does not land on the
home screen: the app hands the sign-in to the browser. Chrome opens a
Custom Tab on `challenge.spotify.com` - "We need to make sure that
you're a human" over a reCAPTCHA v2 tick box (`recaptcha-anchor`) and a
green Continue - and the account is signed in only once that is
answered. Captured on 3644, the first warm phone an `error` account was
ever sent to (2026-09-19); the operator passes it by hand with two taps,
and `act_challenge` does the same two taps.

A phone that has never opened Chrome shows Chrome's own first-run page
first: "Make Chrome your own" over the phone's Gmail, a "Continue as
<first name>" and a "Use without an account". It has a screen of its
own because the dismiss list's "Continue" matched "Continue as Risky"
and signed the browser into the farm's Google account - the one thing
this flow says it never does (3644, 2026-09-19).

## What this flow will not do

**Continue with Google or Facebook.** A phone that has a Gmail on it
would be signed in as that Gmail, which looks like success and is the
wrong account; a bare phone has nothing there at all. Nothing matching
"google" or "facebook" is ever tapped - and since 2026-09-19 that rule
covers the dismiss list too, which used to reach a Google control by
matching the first word of its label. **Sign up.** Never tapped either.
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
from . import recaptcha, router
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
#: "continue as" is the browser's: Chrome's first-run page offers
#: "Continue as <first name>" for the Gmail on the phone, and the
#: dismiss list's "Continue" reached it by partial match and signed the
#: browser in as the farm (3644, 2026-09-19).
NEVER_TAPPED = ("google", "facebook", "sign up", "create account",
                "update payment", "primarycta", "get premium",
                "continue as", "use another account")

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

#: The browser Spotify hands its challenge to. Named so a page drawn by
#: it is recognised as somebody else's - the router's `left_the_app`
#: already says as much, and this flow now has screens for the two pages
#: of Chrome's it will meet.
CHROME_PACKAGE = "com.android.chrome"

#: Chrome's first-run page, on a phone that has never opened the browser:
#: "Make Chrome your own" over the phone's Gmail, with "Continue as
#: <first name>" above "Use without an account". Only the decline is ever
#: pressed (3644, 2026-09-19).
CHROME_SETUP_TEXTS = ("make chrome your own",)
CHROME_DECLINE = "Use without an account"

#: Spotify's challenge, as captured on 3644: the heading, the reCAPTCHA
#: tick box by its own id, and the green button under it. The heading is
#: matched loosely because the same page has been seen worded both ways
#: on the web, and the tick box alone is enough to know the page.
CHALLENGE_TEXTS = ("make sure that you're a human",
                   "make sure that you are a human",
                   "verify you are human", "verify you're human",
                   "confirm you are human", "confirm you're human")
ROBOT_LABEL = "not a robot"
CHALLENGE_BUTTON = "Continue"
#: The image grid reCAPTCHA falls back to when the tick alone will not
#: do - which on 3644 it did, on the first tick ever sent to it
#: (2026-09-19): "Select all images with crosswalks" over a VERIFY, the
#: same 3x3 the Google flow has answered for a fortnight. Answered by
#: the same code, which moved to `recaptcha.py` rather than being
#: copied.
GRID_TEXTS = ("select all images", "select all squares")
#: Grids one sign-in sends to the solver before the exit is blamed
#: instead. Measured on Google over a week: nothing that took more than
#: three grids ever signed in, and each further one spent a phone's
#: billing to find that out (GRIDS_PER_SIGN_IN, 2026-09-12). The same
#: five, until Spotify gives its own number.
GRIDS_PER_CHALLENGE = 5
#: Visits to give a half-drawn grid to finish before it is answered from
#: the picture anyway.
DRAW_WAIT = 3

#: Visits the challenge page gets before the account goes back as
#: `captcha_shown`. Most of them are waiting: reCAPTCHA leaves the box
#: reading unticked for a few seconds while it decides, which is the
#: lesson two Google builds paid for (phones 1787 and 1788, 2026-09-06).
CHALLENGE_VISITS = 14
#: Visits to leave the box alone after tapping it, for the same reason:
#: a second tap unticks what the first ticked.
TICK_AGAIN = 4
#: How many times Continue is pressed on a page that keeps coming back.
CONTINUE_TRIES = 3

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
        "Spotify's challenge page did not clear - the tick box was "
        "answered and reCAPTCHA was not satisfied, which is a verdict on "
        "the exit and the device, not on the account; a cleaner proxy is "
        "the fix",
    "captcha_grid":
        "reCAPTCHA would not take the tick and asked for pictures; only "
        "the tick box is answered here, and a grid is the exit being "
        "distrusted rather than the account",
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
    #: The challenge visit the tick box was last tapped on, so the flow
    #: waits for reCAPTCHA to decide instead of unticking its own tap.
    ticked_on: int | None = None
    #: How many times Continue has been pressed on the challenge page.
    continues: int = 0
    #: The CapSolver key, when one is set. Empty is the whole of "no
    #: solver": a grid is then reported rather than answered, which is
    #: what this flow did before there was one.
    solver_key: str = ""
    #: Grids sent to the solver in this sign-in, and the two counters
    #: that keep one page from eating the budget.
    grids: int = 0
    grid_waits: int = 0
    grid_unplaced: bool = False

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


def _no_size(element) -> bool:
    """Whether this element is laid out with no area, which makes its
    centre a point on the screen that has nothing to do with it."""
    nums = [int(n) for n in re.findall(r"-?\d+", element.bounds)]
    if len(nums) != 4:
        return True
    return nums[2] <= nums[0] or nums[3] <= nums[1]


def _dismissable(ctx: Context):
    """The first dismiss control on the page that this flow is allowed to
    touch, or None.

    The allowlist is matched partially - "Continue" finds "Continue" in a
    longer label - and that is what it is for, since the same button is
    "Continue" on one rendering and "Continue to Spotify" on another. It
    also means one word of the list can reach a control that does
    something else entirely: on Chrome's first-run page "Continue" found
    "Continue as Risky" and signed the browser into the phone's Gmail
    (3644, 2026-09-19). So what the allowlist finds is put through the
    same refusal every other tap in this flow goes through.
    """
    for label in DISMISS_LABELS:
        element = screen.find(ctx.elements, label, clickable_only=False)
        if element is None:
            continue
        if any(word in element.label.casefold() for word in NEVER_TAPPED):
            log.warning("not dismissing with %r: it matched %r", label,
                        element.label)
            continue
        if _no_size(element):
            # A web page's "Skip to content" is a link laid out at
            # [0,0][0,0], and the centre of that is the screen's own
            # top-left corner - so dismissing with it is a tap on
            # whatever is up there (3644, 2026-09-19).
            log.warning("not dismissing with %r: %r has no size", label,
                        element.label)
            continue
        return element
    return None


def act_dismiss(ctx: Context) -> Outcome | None:
    element = _dismissable(ctx)
    tapped = element.label if element is not None and screen.tap_element(
        ctx.client, ctx.phone_id, element) else None
    if tapped:
        log.info("dismissed %r", tapped)
        time.sleep(3)
    return None


# ------------------------------------------------- the browser's two pages
def on_chrome_setup(ctx: Context) -> bool:
    return ctx.has(*CHROME_SETUP_TEXTS)


def act_chrome_setup(ctx: Context) -> Outcome | None:
    """Decline Chrome's offer to sign in, and never take it.

    The browser is here to draw one challenge page and close again. What
    it must not do on the way is adopt the phone's Google account, which
    is what the "Continue as <first name>" above the decline does - and
    what this flow did once, by partial match, before this screen
    existed (3644, 2026-09-19).
    """
    if _tap_safely(ctx, CHROME_DECLINE):
        log.info("declined Chrome's sign-in; the browser stays signed out")
        time.sleep(4)
        return None
    # Not the wording we captured. Kept rather than guessed at: the one
    # thing worse than not getting past this page is getting past it by
    # pressing something that signs the browser in.
    kept = ctx.keep("chrome-setup")
    return Outcome("unknown", "chrome_setup_unknown",
                   f"Chrome is asking to be set up and {CHROME_DECLINE!r} "
                   f"is not on the page; nothing else here is safe to "
                   f"press", artifacts=kept)


def _robot_box(ctx: Context):
    """reCAPTCHA's own tick box, not the words beside it.

    A CheckBox whose label says "not a robot": the page also carries the
    same words as plain text, and tapping prose does nothing.
    """
    for element in ctx.elements:
        if ("checkbox" in (element.cls or "").lower()
                and ROBOT_LABEL in (element.label or "").lower()):
            return element
    return None


def on_challenge(ctx: Context) -> bool:
    return ctx.has(*CHALLENGE_TEXTS) or _robot_box(ctx) is not None


def act_grid(ctx: Context, visit: int) -> Outcome | None:
    """Send the image grid to CapSolver and tap what it names.

    The same widget the Google flow answers, in the same shape - the
    heading split across two nodes, "Click verify once there are none
    left", a VERIFY under the tiles, and no tiles in the accessibility
    tree at all, so the block is found in the picture (3644,
    2026-09-19). So it is answered by the same code, which moved to
    `recaptcha.py` to be shared rather than copied.

    Every way this can fail ends where the page ended before there was a
    solver: `captcha_grid`, which blames the exit and leaves the account
    alone.
    """
    if not ctx.solver_key:
        kept = ctx.keep("captcha-grid")
        return Outcome("fatal", "captcha_grid",
                       f"an image grid and no CapSolver key is set: "
                       f"{FATAL_ADVICE['captcha_grid']}", artifacts=kept)
    if ctx.grids >= GRIDS_PER_CHALLENGE:
        kept = ctx.keep("captcha-grid")
        return Outcome("fatal", "captcha_grid",
                       f"{ctx.grids} grids answered and it is still "
                       f"asking, which is the exit being refused rather "
                       f"than a puzzle being got wrong", artifacts=kept)
    instruction = recaptcha.instruction_on(ctx)
    if not instruction:
        # The heading is the question, and without it there is nothing to
        # ask the solver. A page mid-draw; the visit allowance ends it.
        time.sleep(4)
        return None
    tiles = recaptcha.tile_buttons(ctx)
    if not tiles and recaptcha.half_drawn(ctx) and ctx.grid_waits < DRAW_WAIT:
        # Some tiles, not all. A picture taken now holds whichever corner
        # has arrived, and one came out 206 pixels across and was answered
        # as though it were the whole grid (1836, 2026-09-06).
        ctx.grid_waits += 1
        time.sleep(4)
        return None
    ctx.grid_waits = 0
    window = recaptcha.tiles_box(tiles) if tiles else recaptcha.grid_rect(ctx)
    if window is None:
        # A grid this cannot place. Never a tap on coordinates guessed
        # from nothing - and the page is kept once, not once a visit.
        if not ctx.grid_unplaced:
            ctx.grid_unplaced = True
            ctx.keep("captcha-grid-unplaced")
        time.sleep(4)
        return None
    size = recaptcha.grid_size(instruction, tiles)
    if ctx.grids == 0:
        ctx.keep("captcha-grid")
    got = recaptcha.grab_grid_b64(ctx, window, size, scan=not tiles)
    if got is None:
        return None
    image, rect = got
    ctx.grids += 1
    try:
        from .. import capsolver

        answer, read = capsolver.solve_grid(ctx.solver_key, image,
                                            instruction, watch=ctx.check)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("captcha not solved (%s)", exc)
        return None
    if read and read != size:
        # The solver read a different grid than the one it was sent, so
        # its indices are about a picture nobody has. Left alone.
        log.warning("sent a %dx%d grid and the answer is about a %dx%d one",
                    size, size, read, read)
        return None
    recaptcha.tap_answer(ctx, answer, tiles=tiles, rect=rect, size=size,
                         instruction=instruction)
    return None


def act_challenge(ctx: Context) -> Outcome | None:
    """Answer Spotify's challenge the way the operator does: tick the box,
    let reCAPTCHA decide, press Continue.

    Every way out of here that is not the tick passing ends as
    `captcha_shown` - the reason this page had before anything tried to
    answer it - so a phone is never worse off for the attempt.
    """
    visit = ctx.seen.get("challenge", 0)
    if ctx.has(*GRID_TEXTS):
        return act_grid(ctx, visit)
    if visit >= CHALLENGE_VISITS - 1:
        kept = ctx.keep("captcha_shown")
        return Outcome("fatal", "captcha_shown",
                       f"the challenge was still up after "
                       f"{CHALLENGE_VISITS} looks: "
                       f"{FATAL_ADVICE['captcha_shown']}", artifacts=kept)
    box = _robot_box(ctx)
    if box is not None and not box.checked:
        if (ctx.ticked_on is not None
                and visit - ctx.ticked_on < TICK_AGAIN):
            # Still deciding. reCAPTCHA leaves the box reading unticked
            # for a few seconds after a tap, and a second tap unticks
            # what the first ticked - two Google builds spent their whole
            # allowance doing exactly that (1787 and 1788, 2026-09-06).
            log.info("waiting for reCAPTCHA to decide (%d of %d)",
                     visit - ctx.ticked_on, TICK_AGAIN)
            time.sleep(4)
            return None
        if ctx.ticked_on is None:
            # The page as it arrived, once: what the challenge looked
            # like before anything was pressed is the only picture worth
            # having when a week of these is read back.
            ctx.keep("captcha-page")
        log.info("%s: Spotify is challenging this phone; ticking "
                 "\"I'm not a robot\"", ctx.creds.email)
        screen.tap_element(ctx.client, ctx.phone_id, box)
        ctx.ticked_on = visit
        time.sleep(5)
        return None
    # Ticked, or gone from the page because reCAPTCHA has taken it away.
    # Either way the button under it is what submits the challenge.
    if ctx.continues >= CONTINUE_TRIES:
        kept = ctx.keep("captcha_shown")
        return Outcome("fatal", "captcha_shown",
                       f"Continue was pressed {ctx.continues} times and "
                       f"the challenge did not clear: "
                       f"{FATAL_ADVICE['captcha_shown']}", artifacts=kept)
    if _tap_safely(ctx, CHALLENGE_BUTTON):
        ctx.continues += 1
        log.info("the box is ticked; pressing Continue (%d of %d)",
                 ctx.continues, CONTINUE_TRIES)
        time.sleep(8)
        return None
    # No button yet: the page redraws itself between the tick and the
    # button becoming real. The visit allowance is what ends this.
    time.sleep(4)
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
    # The browser's two pages, ahead of everything the app draws and
    # ahead of `dismissable` above all: both carry words the dismiss
    # list answers to, and neither is a page of Spotify's to dismiss.
    Screen("chrome_setup", on_chrome_setup, act_chrome_setup, max_visits=3),
    Screen("challenge", on_challenge, act_challenge,
           max_visits=CHALLENGE_VISITS),
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
            solver_key: str = "",
            watch: Callable[[], None] | None = None) -> Outcome:
    """Drive the Spotify login to a named outcome. Returns rather than
    raises, like every flow: a batch records why and moves on. `codes`
    is accepted for the builder's sake and unused: nothing here is
    answered by a code. `solver_key` is used - without one an image
    grid is reported rather than answered."""
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
                  package=package, artifact_dir=artifact_dir,
                  solver_key=solver_key)

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
