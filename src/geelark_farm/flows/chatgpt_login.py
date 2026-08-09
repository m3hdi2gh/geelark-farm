"""Sign an app account into ChatGPT, once the app is installed.

Same router as the Google login, and for the same reason: the screens do not
come in a fixed order. What differs is what can be proved.

## The one weakness in this step, stated plainly

Google's sign-in ends with `dumpsys account` naming the address, and the
install ends with `pm list packages` naming the package. Both are the device
answering a question. **There is no such question for an app's own session.**
The session lives in the app's private storage, which needs root to read.

So this step has to judge from the screen, which is what this project spends
most of its effort distrusting - reporting success from a screen is exactly
what GeeLark's own RPA tasks do, and avoiding it is why this tool exists.

**And the obvious screen check is wrong.** The app has a logged-out mode; its
welcome screen offers "Continue without logging in". That mode has the same
composer, the same text box, the same controls as a signed-in session. On
2026-08-08 a phone opened straight into it and the row was reported ready with
nobody in the app. It was found by someone opening the phone by hand.

The composer therefore only counts once this run has submitted the password
(`Context.submitted_password`), and a chat screen without that is not believed:
`pm clear` puts the app back at its welcome screen, where the ordinary path
applies. That is conservative on purpose. A phone genuinely signed in by an
earlier run gets signed in again rather than assumed, and a wasted login is
worth far more than a phone handed over as ready with nobody in it.

It is still the weakest check in the pipeline. The honest fix is to read the
account's address out of the app's own menu, and that needs a capture of that
menu to write. `verified_on_device()` is the only place that would change.

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
    # The page says "Incorrect email address or password" - "email address",
    # not "email" - so none of the first three needles matched it and the flow
    # retyped the same password until its visits ran out, reporting
    # stuck_on_password_entry after seven and a half minutes (2026-08-09,
    # row 5). Matched on the shortest phrase that is unambiguous now.
    "wrong_password": (
        "incorrect email", "wrong email", "password is incorrect",
        "incorrect password", "wrong password",
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
    # OpenAI inspecting the TLS chain and refusing it, which is the proxy's
    # doing rather than the account's. Distinct from a CAPTCHA: it is not a
    # judgement about whether we look like a person, it is a refusal to speak
    # over this connection at all.
    "network_ssl_rejected": (
        "unexpected ssl certificate", "network configuration issue",
    ),
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
    "network_ssl_rejected":
        "OpenAI refused the TLS chain this proxy presents, so nothing about "
        "the account is involved. Google signed in and the app installed over "
        "the same proxy, so the phone is fine. These exits rotate between "
        "sessions, so a retry may simply land on a different one; if it "
        "recurs on this row, the proxy is intercepting TLS and needs replacing",
    "email_not_accepted":
        "the address was submitted twice and the page did not move, and "
        "OpenAI never said why - no error was on screen either time. So this "
        "is NOT known to be an exit-IP problem; check the archived screens "
        "before changing anything. The phone is fine: Google is signed in and "
        "the app is installed",
    "request_rejected":
        "CHANGE THE EXIT IP. OpenAI's edge refused the sign-in twice - "
        "'There is a problem with your request', with a Cloudflare Ray ID - "
        "which is a judgement on where the request came from, not on the "
        "account or the password. Retry first: these proxies hand out a "
        "different exit each session, and that alone has fixed it before. If "
        "it recurs, put a different proxy in the sheet AND delete this phone, "
        "because a phone keeps the proxy it was created with",
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
    "Ask ChatGPT", "Message ChatGPT", "Ask anything", "New chat",
)

# The controls beside the composer. A placeholder is wording and wording
# changes; these are the affordances of the chat screen and outlast it.
COMPOSER_CONTROLS = (
    "Dictation", "Attachment", "Start a voice conversation",
)

# How many times the address is put to OpenAI before the row is told that its
# exit address is the problem.
#
# Two, not fifty. The refusal - "There is a problem with your request", with a
# Cloudflare Ray ID after it - comes from the edge rather than from the login,
# so it is a judgement about where the request came from and not about what was
# in it. Repeating quickly is the behaviour that layer exists to penalise, and
# every attempt is an attempt against a real account. One resubmission covers a
# genuine one-off; past that the answer is a different address, which no amount
# of pressing this button produces.
MAX_EMAIL_SUBMISSIONS = 2
RESUBMIT_PAUSE = 20.0

# What OpenAI's edge says when it refuses, with a Cloudflare Ray ID after it.
# The toast fades within seconds, so it is only there to be seen if the screen
# is read almost immediately - which is why the reason used to be inferred from
# the count instead. Inferring it meant telling someone to change their exit IP
# on the strength of a page that showed nothing at all (2026-08-10).
REQUEST_PROBLEM_TEXTS = (
    "there is a problem with your request",
    "something went wrong",
)
# How soon after pressing the button to look. Long enough for the answer to
# arrive, short enough to still be there when it does.
GLANCE_SECONDS = 2.0


@dataclass
class Context(router.Context):
    """The generic context plus the app account being signed in."""

    creds: Credentials = None                                   # type: ignore
    package: str = ""
    # How many times the address has been put to OpenAI, and whether its edge
    # was ever actually seen refusing one. See act_email.
    email_submissions: int = 0
    saw_edge_refusal: bool = False
    # Set when this run has actually put the account's password into the form.
    # See verified_on_device: a composer on screen is not evidence of a
    # session, because the app has a logged-out mode that has one too.
    submitted_password: bool = False


LAUNCH_ATTEMPTS = 3


def launch(client: Client, phone_id: str, package: str) -> bool:
    """Bring the app to the front, and check that it came.

    One monkey and a fixed wait was the whole of this, and it silently did
    nothing twice: the install had only just finished, the app did not come up
    inside eight seconds, and the flow drove on against the Play Store's own
    page - reading "Uninstall" and "Open", matching nothing, and reporting
    app_unknown_screen about a screen that was never this app's (2026-08-08,
    rows 7 and 8).

    Asking the device which app is in front is the check, rather than looking
    for something recognisable on screen: an unrecognised page looks identical
    whether the app is showing something new or was never started.
    """
    for attempt in range(1, LAUNCH_ATTEMPTS + 1):
        shell.run(client, phone_id,
                  f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        time.sleep(8)
        front = shell.foreground_package(client, phone_id)
        if not front:
            # The device would not say. Carrying on beats refusing to start
            # over a diagnostic that is not available.
            log.info("could not read the foreground app; assuming it started")
            return True
        if front == package:
            return True
        log.warning("%s is in front, not %s (attempt %d/%d)",
                    front, package, attempt, LAUNCH_ATTEMPTS)
        time.sleep(5)
    return False


def composer_on_screen(ctx: Context) -> bool:
    """Whether the chat screen is up. NOT whether anyone is signed in.

    A text box plus something that only sits beside the composer. The box alone
    would not do - the login page has one; the wording alone would not do
    either, since the placeholder has been reworded at least three times, hence
    the controls around it as a second way to satisfy it.
    """
    if screen.find_input(ctx.elements) is None:
        return False
    return (screen.find_first(ctx.elements, COMPOSER_PLACEHOLDERS) is not None
            or screen.find_first(ctx.elements, COMPOSER_CONTROLS) is not None)


def verified_on_device(ctx: Context) -> bool:
    """The best available evidence that the session exists.

    The composer is not that evidence, and believing it was is the worst bug
    this project has produced. The app has a logged-out mode - its welcome
    screen offers "Continue without logging in" - and that mode has the same
    composer, the same text box, the same controls. On 2026-08-08 a phone
    opened straight into it: no registry entry matched, so nothing was logged
    and nothing was archived, and the next pass through the loop read those
    same elements, saw a composer, and reported the row ready. It was found by
    someone opening the phone by hand.

    Reporting success from a screen is exactly what GeeLark's own RPA tasks do,
    and avoiding it is why this tool exists.

    So the composer only counts once this run has actually submitted the
    password. That is deliberately conservative: a phone genuinely signed in by
    an earlier run now has to be signed in again rather than be assumed, and a
    wasted login is worth far more than a phone handed over as ready with
    nobody in it.

    It is still screen evidence, and it is still the weakest check in the
    pipeline. The honest fix is to read the account's address out of the app's
    own menu, which needs a capture of that menu to write.
    """
    return ctx.submitted_password and composer_on_screen(ctx)


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
    """Put the address in and submit it - or name why that keeps failing.

    Being back on this page with the address still in the box means the last
    submission did not take. WHY it did not take is a separate question, and
    for a while this did not ask it: it assumed OpenAI's edge had refused, and
    told people to change their exit IP on the strength of a page that showed
    nothing at all. Three rows were sent round that loop across two runs, and
    every archived screen was a clean email form (2026-08-10).

    So the page is now glanced at two seconds after the button, while the toast
    is still up, and what it says decides the reason.
    """
    field = screen.find_input(ctx.elements, password=False)
    if not field:
        return None

    resubmitting = field.text.strip().casefold() == ctx.creds.email.casefold()

    if resubmitting and ctx.email_submissions >= MAX_EMAIL_SUBMISSIONS:
        if ctx.saw_edge_refusal:
            path = ctx.save("request_rejected")
            return Outcome("fatal", "request_rejected",
                           FATAL_ADVICE["request_rejected"],
                           artifacts=[path] if path else [])
        path = ctx.save("email_not_accepted")
        return Outcome("unknown", "email_not_accepted",
                       FATAL_ADVICE["email_not_accepted"],
                       artifacts=[path] if path else [])

    if resubmitting:
        log.info("the address is still in the box - the last submission did "
                 "not take; sending it once more")
        time.sleep(RESUBMIT_PAUSE)
    else:
        log.info("entering the app account's email address")
        fill(ctx, field, ctx.creds.email)

    ctx.email_submissions += 1
    submit(ctx)

    # Look while the answer is still on screen. Five seconds later - which is
    # where the loop reads next - the toast has gone and the page is a clean
    # form again, which is exactly how this came to be guessed at.
    time.sleep(GLANCE_SECONDS)
    ctx.refresh()
    if ctx.has(*REQUEST_PROBLEM_TEXTS):
        ctx.saw_edge_refusal = True
        ctx.save("edge-refusal")
        log.warning("OpenAI's edge refused the submission")
    time.sleep(3)
    return None


def act_password(ctx: Context) -> Outcome | None:
    field = screen.find_input(ctx.elements, password=True)
    if not field:
        return None
    log.info("entering the app account's password")
    fill(ctx, field, ctx.creds.password)
    submit(ctx)
    # Recorded because success depends on it: a composer means nothing unless
    # this run put the password in. See verified_on_device.
    ctx.submitted_password = True
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


def act_reset_app(ctx: Context) -> Outcome | None:
    """Wipe the app's state and start it again, so its screen means something.

    Reached when the chat screen is up but this run never signed in - the app
    is either in its logged-out mode or holding a session from an earlier run,
    and from outside those look identical. Guessing costs a phone reported as
    ready with nobody in it, which is what happened.

    `pm clear` settles it: the app comes back at its welcome screen, and the
    ordinary path applies. A session an earlier run left behind is thrown away
    in the process, which is the right trade - signing in again takes a minute
    and the credentials are right here, while assuming costs a phone.
    """
    path = ctx.save("logged-out-chat")
    log.warning("the chat screen is up but this run has not signed in; "
                "clearing the app so its state is known")
    shell.run(ctx.client, ctx.phone_id, f"pm clear {ctx.package}")
    time.sleep(3)
    launch(ctx.client, ctx.phone_id, ctx.package)
    if path:
        ctx.saved.append(path)
    return None


def act_dismiss(ctx: Context) -> Outcome | None:
    """Clear an onboarding card or a permission dialog.

    clickable_only=False, to agree with the entry that matched. Nothing in this
    app reports clickable - not the notification card's buttons, not Android's
    own "Allow" on the permission dialog it raises - so with the default the
    screen matched eight times and tapped nothing, and the row was reported
    stuck on a card one tap from gone (2026-08-07, row 2).

    The label list is therefore the only thing keeping this off the wrong
    control, which is why it is an allowlist of things that decline or proceed
    and never contains a refusal like "Don't allow".
    """
    tapped = screen.tap_first_present(ctx.client, ctx.phone_id, ctx.elements,
                                      DISMISS_LABELS, clickable_only=False)
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

    # The chat screen with nobody signed in. Above onboarding, because a card
    # on that screen is not the thing that matters about it.
    Screen("logged_out_chat",
           lambda c: composer_on_screen(c) and not c.submitted_password,
           act_reset_app, max_visits=2),

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

    if not launch(client, phone_id, package):
        return Outcome("unknown", "app_would_not_start",
                       f"{package} did not come to the front after "
                       f"{LAUNCH_ATTEMPTS} attempts")
    ctx = Context(client=client, phone_id=phone_id, creds=creds,
                  package=package, artifact_dir=artifact_dir)

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
