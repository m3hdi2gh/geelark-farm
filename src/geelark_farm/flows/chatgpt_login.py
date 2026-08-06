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
    "email_code_required": (
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
        "OpenAI wants a one-time code from the inbox rather than the "
        "authenticator. The device is signed into that Gmail account, so this "
        "is buildable - it is not built",
}

# The welcome screen's two ways in. Only the second is used: "Continue with
# Google" would sign in whichever account owns the device, which is not the
# account the sheet names.
LOGIN_LABELS = ("Log in", "Log in or sign up", "LOG IN", "Sign in")

# Post-login onboarding, cleared the same way Google's consent pages are.
DISMISS_LABELS = (
    "Continue", "Not now", "Skip", "Maybe later", "Okay", "OK", "Got it",
    "Allow", "Next", "Done", "Start chatting",
)

# Present only once signed in. Deliberately specific: it is the composer, not
# merely the absence of a login button.
COMPOSER_LABELS = (
    "Message", "Ask anything", "Message ChatGPT", "New chat",
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
    is the one step in the pipeline that has none. Keep it narrow - a composer
    element that cannot appear before sign-in - so that at least it cannot be
    satisfied by an empty or half-drawn page.
    """
    return screen.find_first(ctx.elements, COMPOSER_LABELS) is not None


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
        if ctx.tap(label):
            log.info("taking the email login path")
            time.sleep(6)
            return None
    path = ctx.save("no-login-button")
    return Outcome("unknown", "no_login_button",
                   "the welcome screen offered no email login",
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

    Screen("onboarding",
           lambda c: screen.find_first(c.elements, DISMISS_LABELS,
                                       clickable_only=True) is not None,
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
