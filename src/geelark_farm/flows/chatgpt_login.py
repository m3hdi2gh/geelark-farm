"""Sign an app account into ChatGPT, once the app is installed.

Same router as the Google login, and for the same reason: the screens do not
come in a fixed order. What differs is what can be proved.

## The one weakness in this step, stated plainly

Google's sign-in ends with `dumpsys account` naming the address, and the
install ends with `pm list packages` naming the package. Both are the device
answering a question. **There is no such question for an app's own session.**
The session lives in the app's private storage, which is unreadable without
root, so the best available evidence is that the composer is on screen and the
welcome screen is not.

That is screen evidence, which this project spends most of its effort
distrusting - GeeLark's RPA tasks report success from exactly this kind of
claim. It is used here because the alternative is no check at all, and it is
kept as narrow as possible: a specific element that only exists once signed in,
never the absence of an error. `verified_on_device()` is where a stronger check
would go if these phones ever turn out to be rooted, and it is the only place
that would need to change.

## The path

    welcome  ->  "Log in"  ->  email  ->  password  ->  code  ->  composer

The email and password fields are rendered by a web view rather than by
Android, so they carry no useful resource ids and are matched by class and
position within the page.

## What this flow will not do

**Solve a bot check.** OpenAI puts Cloudflare in front of sign-in from
addresses it does not like, exactly as Google does, and no UI automation
answers it. It is a named fatal reason so the row says so.

**Read a code out of an inbox.** If OpenAI emails a one-time code instead of
accepting the authenticator, the run stops with `email_code_required`. The
device is signed into that Gmail account, so reading it on the phone is
possible in principle - it is simply not built, and pretending otherwise by
silently timing out would be worse.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .. import phones, screen, shell
from ..accounts import Credentials
from ..api import Client
from . import router
from .router import Outcome, Screen, act_wait, fill, still_loading

log = logging.getLogger(__name__)

# Anything containing one of these cannot proceed unattended.
FATAL_TEXTS = {
    "captcha_shown": (
        "verify you are human", "verify you're human", "i'm not a robot",
        "confirm you are human", "checking if the site connection is secure",
    ),
    "wrong_password": (
        "incorrect email or password", "wrong email or password",
        "password is incorrect",
    ),
    # An account with no authenticator set up. OpenAI emails a code instead,
    # and the page asking for it says "verification code" - which is also what
    # the authenticator page says, so totp_entry claimed it and typed TOTP
    # codes into it three times, each answered "Incorrect code" (2026-08-07,
    # row 4). Being fatal, and checked first, is what keeps that from
    # happening: the needles below are what the page actually said.
    "email_code_required": (
        "check your inbox", "resend email", "we just sent to",
        "check your email for a code", "enter the code we sent",
        "we sent a code to your email",
    ),
    "account_deactivated": (
        "account has been deactivated", "account is deactivated",
        "account has been suspended",
    ),
    "rate_limited": ("too many attempts", "try again later"),
}

FATAL_ADVICE = {
    "captcha_shown":
        "OpenAI is challenging this exit IP, the same way Google does; a "
        "cleaner proxy is the fix and no code change helps",
    "email_code_required":
        "this app account has no authenticator, so OpenAI emails a one-time "
        "code instead. The phone is fine: Google is signed in and the app is "
        "installed. Set up 2FA on the app account, or put one that has it in "
        "the sheet, and retry - the phone is reused",
}

# The way in that is not Google. Matching is case-insensitive and partial, so
# these are phrases rather than variants of one word - and "Sign in" is
# deliberately not among them: it matched "Sign in with Google" on the consent
# sheet, which is the one button in this whole flow that must never be pressed
# (2026-08-07). GOOGLE_BUTTON below is the second lock on that door.
LOGIN_LABELS = (
    "Log in another way",
    "Log in or sign up",
    "Log in with email",
    "Log in",
)

# Never tapped, however a label happens to match. Signing in the account that
# owns the device instead of the one the sheet names would not raise anything -
# the app would work, the composer would appear, and the row would be recorded
# as ready with the wrong account on it. That is the worst failure available
# here, so it is guarded twice.
GOOGLE_BUTTON = "google"

# Google's account-chooser sheet, which the app raises by itself after the
# email path is chosen. It covers the login page - while it is up, the page's
# text field is not in the hierarchy at all.
GOOGLE_SHEET_TEXTS = ("sign in with google", "use your account for")
CLOSE_SHEET_LABELS = ("Close sheet", "Close", "Dismiss")

# Post-login onboarding, cleared the same way Google's consent pages are.
DISMISS_LABELS = (
    "Continue", "Not now", "Skip", "Maybe later", "Okay", "OK", "Got it",
    "Allow", "Next", "Done", "Start chatting",
)

# The composer's placeholder. It has been worded at least three ways across
# versions, and the first live sign-in reached the chat screen and reported
# app_unknown_screen because this list had two of them and not the third
# (2026-08-07: "Ask ChatGPT").
COMPOSER_PLACEHOLDERS = (
    "Ask ChatGPT", "Message ChatGPT", "Ask anything", "New chat", "Message",
)

# The controls beside the composer. A placeholder is wording and wording
# changes; these are the affordances of the chat screen and outlast it.
COMPOSER_CONTROLS = (
    "Dictation", "Attachment", "Start a voice conversation",
)


@dataclass
class Context(router.Context):
    """The generic context plus the app account being signed in."""

    creds: Credentials = None                                   # type: ignore


def launch(client: Client, phone_id: str, package: str) -> None:
    """Bring the app to the front, from wherever it was."""
    shell.run(client, phone_id,
              f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    time.sleep(8)


def verified_on_device(ctx: Context) -> bool:
    """The best available evidence that the session exists.

    See the module docstring: this is screen evidence, not device truth, and it
    is the one step in the pipeline that has none. So it is made as hard to
    satisfy accidentally as screen evidence can be - a text box AND something
    that only sits beside the chat composer.

    The box alone would not do: the login page has one too. The wording alone
    would not do either, and that is not hypothetical - the first successful
    sign-in was reported as an unknown screen because the placeholder had been
    reworded since this list was written. Hence two ways to satisfy the second
    half: the placeholder, whose wording moves, and the controls around it,
    which have not.
    """
    if screen.find_input(ctx.elements) is None:
        return False
    return (screen.find_first(ctx.elements, COMPOSER_PLACEHOLDERS) is not None
            or screen.find_first(ctx.elements, COMPOSER_CONTROLS) is not None)


# ------------------------------------------------------------------ screens
def _fatal_reason(ctx: Context) -> str | None:
    for reason, needles in FATAL_TEXTS.items():
        if ctx.has(*needles):
            return reason
    return None


def act_fatal(ctx: Context) -> Outcome:
    reason = _fatal_reason(ctx) or "unknown_fatal"
    path = ctx.save(reason)
    detail = FATAL_ADVICE.get(reason,
                              "the screen says this cannot proceed unattended")
    return Outcome("fatal", reason, detail, artifacts=[path] if path else [])


def act_choose_login(ctx: Context) -> Outcome | None:
    """Take the email path, never "Continue with Google".

    The Google button would sign in the account that owns the device, and the
    sheet names a different one. Silently signing in the wrong account is worse
    than failing, because it looks like success.
    """
    for label in LOGIN_LABELS:
        found = ctx.find(label)
        if found is None:
            continue
        if GOOGLE_BUTTON in found.label.casefold():
            # The label matched something Google's. Matching here is partial,
            # so this is not hypothetical: "Sign in" found "Sign in with
            # Google" on the consent sheet and tapped it.
            log.warning("%r matched %r; refusing to take the Google path",
                        label, found.label)
            continue
        if screen.tap_element(ctx.client, ctx.phone_id, found):
            log.info("taking the email login path via %r", found.label)
            time.sleep(6)
            return None
    path = ctx.save("no-login-button")
    return Outcome("unknown", "no_login_button",
                   "the welcome screen offered no email login",
                   artifacts=[path] if path else [])


def act_close_google_sheet(ctx: Context) -> Outcome | None:
    """Dismiss Google's account chooser without choosing anything.

    The app raises it by itself after the email path is chosen, offering the
    account that owns the device - which is not the account the sheet names.
    Tapping outside it is what closes it, and the sheet helpfully provides the
    target: a clickable View labelled "Close sheet" covering the whole area
    above it (captured 2026-08-07).
    """
    tapped = screen.tap_first_present(ctx.client, ctx.phone_id, ctx.elements,
                                      CLOSE_SHEET_LABELS)
    if tapped:
        log.info("closed Google's account chooser (%r)", tapped)
        time.sleep(4)
        return None
    # No labelled way out, so tap the strip above the sheet directly: whatever
    # the topmost element of the sheet is, the page is above it.
    top = min((e.centre[1] for e in ctx.elements
               if e.centre and e.label and GOOGLE_BUTTON not in e.label.casefold()),
              default=0)
    if top > 200:
        shell.tap(ctx.client, ctx.phone_id, 360, top // 2)
        log.info("tapped above Google's sheet to dismiss it")
        time.sleep(4)
        return None
    path = ctx.save("google-sheet-stuck")
    return Outcome("unknown", "google_sheet_stuck",
                   "Google's account chooser would not close",
                   artifacts=[path] if path else [])


def act_email(ctx: Context) -> Outcome | None:
    field = screen.find_input(ctx.elements, password=False)
    if not field:
        return None
    log.info("entering the app account's email address")
    fill(ctx, field, ctx.creds.email)
    submit(ctx)
    time.sleep(5)
    return None


def act_password(ctx: Context) -> Outcome | None:
    field = screen.find_input(ctx.elements, password=True)
    if not field:
        return None
    log.info("entering the app account's password")
    fill(ctx, field, ctx.creds.password)
    submit(ctx)
    time.sleep(6)
    return None


def act_totp(ctx: Context) -> Outcome | None:
    """Type a code with enough life left to survive submission."""
    field = screen.find_input(ctx.elements)
    if not field:
        return None
    log.info("entering a fresh authenticator code")
    fill(ctx, field, ctx.creds.totp_now())
    submit(ctx)
    time.sleep(6)
    return None


def act_dismiss(ctx: Context) -> Outcome | None:
    tapped = screen.tap_first_present(ctx.client, ctx.phone_id, ctx.elements,
                                      DISMISS_LABELS)
    if tapped:
        log.info("dismissed %r", tapped)
        time.sleep(3)
    return None


def submit(ctx: Context) -> None:
    """Advance the form. The web view's button labels vary, and the keyboard's
    enter key works when the button is below the fold."""
    ctx.refresh()
    for label in ("Continue", "CONTINUE", "Next", "Log in", "Submit", "Verify"):
        if ctx.tap(label):
            return
    shell.keyevent(ctx.client, ctx.phone_id, 66)      # ENTER


# Order matters: the first match wins.
SCREENS: list[Screen] = [
    Screen("fatal", lambda c: _fatal_reason(c) is not None, act_fatal,
           max_visits=1),

    # Above everything that acts. A web view spends much more of its life
    # painting than a native screen does, so this matters more here than it
    # does in the Google flow, where it was still worth a whole login.
    Screen("loading", still_loading, act_wait, max_visits=25),

    # The code box outranks the password box: once a code is being asked for,
    # the password has already been accepted.
    Screen("totp_entry",
           lambda c: (c.has("authentication code", "verification code",
                            "two-factor", "6-digit", "one-time code")
                      and screen.find_input(c.elements) is not None),
           act_totp),

    Screen("password_entry",
           lambda c: screen.find_input(c.elements, password=True) is not None,
           act_password),

    # Above the login screens, because it covers them: while this sheet is up
    # the login page's text field is not in the hierarchy at all, so nothing
    # below would match the page underneath anyway.
    Screen("google_account_sheet",
           lambda c: c.has(*GOOGLE_SHEET_TEXTS),
           act_close_google_sheet, max_visits=3),

    # The field carries no label of its own - "Email" is a sibling TextView
    # above it - so this matches the box and the word, not a sentence. The
    # first attempt looked for "email address" / "your email"; the page says
    # only "Email", so nothing matched and the welcome entry below claimed it.
    Screen("email_entry",
           lambda c: (c.has("email")
                      and screen.find_input(c.elements, password=False)
                      is not None),
           act_email),

    # No text field anywhere is what makes this the welcome screen rather than
    # the login page. Without that guard this entry matched the login page too,
    # because "Log in or sign up" is both the button here and the heading
    # there - so the flow arrived where it wanted to be and then tapped that
    # page's own title until it ran out of visits (2026-08-07, row 1). The tap
    # coordinates in the log are the tell: y=1216 the first time, y=366 the
    # second.
    Screen("welcome",
           lambda c: (screen.find_input(c.elements) is None
                      and (c.has("welcome to chatgpt",
                                 "continue without logging in")
                           or screen.find_first(c.elements, LOGIN_LABELS)
                           is not None)),
           act_choose_login, max_visits=2),

    # Not clickable_only. Nothing in this app reports clickable=true - every
    # label so far, including the two buttons on the notification card, is a
    # plain TextView whose centre taps correctly. Requiring the flag meant this
    # entry could never match, so a signed-in session sat on an onboarding card
    # and was reported as an unknown screen (2026-08-07, row 2).
    Screen("onboarding",
           lambda c: screen.find_first(c.elements, DISMISS_LABELS) is not None,
           act_dismiss, max_visits=8),
]


def sign_in(client: Client, phone_id: str, creds: Credentials, *,
            package: str, budget_seconds: float = 600,
            artifact_dir: Path | None = None) -> Outcome:
    """Drive the app login to a named outcome.

    Returns rather than raises: a batch needs to record why a row failed and
    move on, not unwind.
    """
    if not shell.package_installed(client, phone_id, package):
        return Outcome("fatal", "app_not_installed",
                       f"{package} is not on this phone")

    launch(client, phone_id, package)
    ctx = Context(client=client, phone_id=phone_id, creds=creds,
                  artifact_dir=artifact_dir)

    def logged_in() -> Outcome | None:
        # Unlike the other two steps this reads the screen, because the app's
        # session is not visible to the device. It runs on the elements the
        # last refresh already fetched, so it costs nothing extra.
        if ctx.elements and verified_on_device(ctx):
            return Outcome("success", "logged_in",
                           f"{creds.email}: the composer is on screen")
        return None

    return router.drive(ctx, SCREENS, is_done=logged_in,
                        budget_seconds=budget_seconds, logger=log)


def sign_in_on_phone(client: Client, phone_id: str, creds: Credentials,
                     **kwargs) -> Outcome:
    """sign_in(), but ensure the phone is up first."""
    phones.ensure_running(client, phone_id)
    return sign_in(client, phone_id, creds, **kwargs)
