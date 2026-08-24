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

The composer therefore only counts once this run has put a credential in -
a password, or a code the service emailed (`Context.signed_something_in`) -
and a chat screen without that is not believed:
`pm clear` puts the app back at its welcome screen, where the ordinary path
applies. That is conservative on purpose. A phone genuinely signed in by an
earlier run gets signed in again rather than assumed, and a wasted login is
worth far more than a phone handed over as ready with nobody in it.

The composer check decides when the *login* is done. What closes the gap is
`verify_account`, which runs after it: the flow walks chat -> Menu -> Account
settings and reads the Email line back, which is the app itself naming its
session - the same standard `dumpsys account` sets for Google. A walk that
cannot reach the page is a failure, not a pass, and an address that is not
the one just typed is its own reason. Built from captures of a live phone
(2026-08-17), like every other screen here.

## The path

    welcome  ->  "Log in"  ->  email  ->  password  ->  code  ->  composer

The email and password fields are rendered by a web view rather than by
Android, so they carry no useful resource ids and are matched by class and
position within the page.

## What this flow will not do

**Solve a bot check.** OpenAI puts Cloudflare in front of sign-in from
addresses it does not like, exactly as Google does, and no UI automation
answers it. It is a named fatal reason so the row says so.

**Read a code out of an inbox by itself.** An account that can hold an
authenticator and is emailed a code anyway has its 2FA misconfigured, and the
run stops with `email_code_required` rather than papering over it. An account
that was never offered a password box is the other kind: a code is the only
way in, and one is asked for from whatever `Context.codes` is - which is a
person at the console today.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import codes as codes_mod
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
    "account_deactivated": (
        "account has been deactivated", "account is deactivated",
        "account has been suspended",
    ),
    # OpenAI rejecting the authenticator code. `google_login` grew this on
    # 2026-08-10 after four wrong codes went to a real account, and this flow
    # was never given the equivalent - OpenAI words it differently, so nothing
    # here matched. Phone 778 typed a fresh code from the same wrong secret
    # over and over against `Incorrect code. Please try again.` and was
    # reported as stuck on the page it was standing on (2026-08-16).
    #
    # A fresh code from a wrong secret is wrong every time, so this stops
    # rather than retrying, and the account is marked so the phone can try the
    # next one.
    "wrong_2fa_code": (
        "incorrect code", "invalid code", "code is incorrect",
        "that code didn't work",
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

#: The page that says a code has been emailed rather than asked of an
#: authenticator. These used to sit in FATAL_TEXTS, which is matched first and
#: ends the attempt - so the moment this page appeared the account was set
#: aside and the phone moved on. It is a screen with an action now; whether
#: anything can be done on it depends on whether a mailbox is reachable.
#:
#: The needles stay this specific for the reason they were written: the page
#: says "verification code", and so does the authenticator page, so matching
#: on that alone sent TOTP codes into this box three times over (2026-08-07,
#: row 4). What separates them is the sentence about the inbox.
EMAIL_CODE_TEXTS = (
    "check your inbox", "resend email", "we just sent to",
    "check your email for a code", "enter the code we sent",
    "we sent a code to your email",
)


FATAL_ADVICE = {
    "wrong_2fa_code":
        "OpenAI rejected the authenticator code, so the 2FA secret in the "
        "sheet is not this account's. A fresh code from a wrong secret is "
        "wrong every time, which is why this stops rather than retrying",
    "captcha_shown":
        "OpenAI is challenging this exit IP, the same way Google does; a "
        "cleaner proxy is the fix and no code change helps",
    "unexpected_password_prompt":
        "this row is ticked as one that signs in with an emailed code, and "
        "OpenAI asked it for a password. One of the two is wrong: either the "
        "tick does not belong on this row, or the account has a password that "
        "is not in the sheet",
    "no_code_source":
        "this app account was never asked for a password, so it is one that "
        "cannot hold one - an emailed code is the only way in, and nothing "
        "was connected that could supply it. Connect the bot, or retry while "
        "whoever can read the code is there. The phone is reused",
    "email_code_never_arrived":
        "OpenAI emailed a one-time code for this app account and none arrived "
        "in the time allowed. The account was not judged and is untouched: "
        "check the mailbox is reachable and that the message is not held up, "
        "then retry. The phone is reused",
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
# Still an allowlist of things that proceed or decline, never "press any
# button" - with clickable_only off it is the only thing keeping this off the
# wrong control. "Agree" is here for the updated-terms dialog the app raises
# after a login, which stopped a signed-in row dead (2026-08-10, row 14); the
# Google flow has accepted the equivalent since July.
DISMISS_LABELS = (
    "Continue", "Not now", "Skip", "Maybe later", "Okay", "OK", "Got it",
    "Agree", "I agree", "Accept", "Allow", "Next", "Done", "Start chatting",
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
#
# The last two are voice mode, which has no text box at all - the composer is
# replaced by "Hold to speak to ChatGPT". A signed-in phone sat in it and was
# reported as an unknown screen, because the check insisted on a box that mode
# does not have (2026-08-10, row 13).
COMPOSER_CONTROLS = (
    "Dictation", "Attachment", "Start a voice conversation",
    "Hold to speak to ChatGPT", "Return to keyboard",
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
    #: Where a code emailed to this account is read from. The default reads
    #: nothing, which is what the tool did before this screen existed.
    codes: codes_mod.CodeSource = field(default_factory=codes_mod.NoSource)
    #: When this attempt reached the page. Mail older than that belongs to an
    #: earlier attempt, and its code is expired - typing it would have OpenAI
    #: refuse a perfectly good account.
    code_since: float = 0.0
    #: Set when this run has put an emailed code into the form. Kept apart
    #: from `submitted_password` because the two answer different questions:
    #: that one says which kind of account this is, and this one does not.
    submitted_code: bool = False

    @property
    def signed_something_in(self) -> bool:
        """Whether this run has put a credential of any kind into the app.

        What `verified_on_device` and `logged_out_chat` actually need to know,
        and for a while `submitted_password` was the only way to ask it. That
        worked while a password was the only way in.

        It stopped working the moment an account signed in with an emailed
        code. Nothing ever set the flag, so a *successful* login landed on the
        chat screen and was read as the app's logged-out mode - and
        `act_reset_app` did what that screen deserves: `pm clear`, wiping the
        session that had just been earned. Twice on phone 1079, which then
        reported `app_stuck_on_welcome` (2026-08-22).
        """
        return self.submitted_password or self.submitted_code


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

    Its own controls are enough on their own - an Attachment button or a voice
    control belongs to nothing else in this app. Failing those, a text box with
    a known placeholder: the box alone would not do, since the login page has
    one too.

    The box used to be required in both cases. Voice mode has no box, so a
    signed-in phone sitting in it was reported as an unknown screen (2026-08-10,
    row 13) - the check was describing one way the chat screen looks rather
    than what makes it the chat screen.
    """
    if screen.find_first(ctx.elements, COMPOSER_CONTROLS) is not None:
        return True
    if screen.find_input(ctx.elements) is None:
        return False
    return screen.find_first(ctx.elements, COMPOSER_PLACEHOLDERS) is not None


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
    return ctx.signed_something_in and composer_on_screen(ctx)


# ------------------------------------------------- reading the account back
#: The path from the chat screen to the page that names the account, each
#: step a content-desc observed on a live phone (2026-08-17, phone 805):
#: `Menu` opens the sidebar, `Account settings` opens the page whose Email
#: section shows the address as a plain TextView.
MENU_LABELS = ("Menu",)
ACCOUNT_SETTINGS_LABELS = ("Account settings",)

EMAIL_TEXT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.ASCII)


#: The heading the account's own address sits under on the settings page.
#: Observed beside it on the live capture: `Parental controls`, `Email`,
#: `mizikilak240@gmail.com`, `Appearance`.
EMAIL_HEADING = "email"


def account_email_on(elements: list[screen.Element]) -> str | None:
    """The address the settings page names, or None if none is on it.

    The one under the `Email` heading, and only then the first one anywhere.
    It took the first, which the docstring already called reading the Email
    line and was not: today that page carries exactly one address, and the
    day OpenAI puts another on it - a support link, a workspace, a second
    section - the first one wins and this reports `app_wrong_account` about a
    phone that is signed in perfectly correctly (2026-08-23).

    This is the check that closed the weakest link in the pipeline. It should
    not be the one that decides on whichever address it happens to meet.
    """
    labels = [e.label.strip() for e in elements]
    for index, text in enumerate(labels):
        if text.casefold() != EMAIL_HEADING:
            continue
        for after in labels[index + 1:index + 4]:
            if EMAIL_TEXT.fullmatch(after):
                return after
    # No heading found - an older layout, or one this has not seen. Better
    # than nothing, and it is what this always did.
    return next((t for t in labels if EMAIL_TEXT.fullmatch(t)), None)


def verify_account(ctx: Context) -> Outcome | None:
    """Read the signed-in address out of the app's own settings.

    The composer check above proves a chat screen; it cannot prove whose. This
    walks chat -> Menu -> Account settings and reads the Email line, which is
    the app itself naming its session - the same standard `dumpsys account`
    sets for Google. It closes the weakest check in the pipeline, flagged in
    this module's docstring since the day it was written.

    Runs only after a login the router already called successful, so the phone
    is on the chat screen. Returns None when the address matches, or the fatal
    Outcome to report instead. Failing to *reach* the page is its own reason,
    not a pass: on 2026-08-08 a phone was handed over ready with nobody in the
    app, and "could not check" must never again count as "checked".

    The phone is stopped right after a build, so nothing navigates back.
    """
    for labels, name in ((MENU_LABELS, "sidebar"),
                         (ACCOUNT_SETTINGS_LABELS, "account settings")):
        found = None
        for _ in range(6):
            ctx.refresh()
            found = screen.find_first(ctx.elements, labels)
            if found is not None:
                break
            time.sleep(3)
        if found is None or not screen.tap_element(ctx.client, ctx.phone_id,
                                                   found):
            path = ctx.save("verify-lost")
            return Outcome("fatal", "session_unverified",
                           f"the {name} control never appeared, so the "
                           f"session could not be read back",
                           artifacts=[path] if path else [])
        time.sleep(2)

    named = None
    for _ in range(6):
        ctx.refresh()
        named = account_email_on(ctx.elements)
        if named is not None:
            break
        time.sleep(3)
    if named is None:
        path = ctx.save("verify-no-address")
        return Outcome("fatal", "session_unverified",
                       "the settings page showed no address to read",
                       artifacts=[path] if path else [])
    if named.casefold() != ctx.creds.email.casefold():
        path = ctx.save("verify-wrong-account")
        return Outcome("fatal", "app_wrong_account",
                       f"the app's settings name {named}, not "
                       f"{ctx.creds.email}",
                       artifacts=[path] if path else [])
    log.info("the app's own settings name %s", named)
    return None


# ------------------------------------------------------------------ screens
def _fatal_reason(ctx: Context) -> str | None:
    """The terminal reason this page carries, if any.

    `wrong_2fa_code` is withheld on the emailed-code page, and that exception
    is the whole reason this is a function rather than a loop. Both pages show
    "Incorrect code" when a code is refused, and on the authenticator page
    that means the secret in the sheet is not the account's - a verdict that
    marks the credential. On this page it means the emailed code was wrong or
    expired, which says nothing about the account at all. Reading one as the
    other condemns an account that has done nothing wrong, which is what this
    ordering has always existed to prevent (2026-08-07, row 4).
    """
    on_email_code = ctx.has(*EMAIL_CODE_TEXTS)
    for reason, needles in FATAL_TEXTS.items():
        if reason == "wrong_2fa_code" and on_email_code:
            continue
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
    if ctx.creds.email_code_only:
        # The row declared this account has no password, and OpenAI has just
        # asked for one. Without this the empty cell would be typed into the
        # box and submitted, and the refusal that follows would be reported
        # against an account that was never really tried.
        #
        # The device is the truth and the row is a claim; where they disagree,
        # it is the claim that is wrong.
        path = ctx.save("unexpected_password_prompt")
        return Outcome("fatal", "unexpected_password_prompt",
                       FATAL_ADVICE["unexpected_password_prompt"],
                       artifacts=[path] if path else [])
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


def act_email_code(ctx: Context) -> Outcome | None:
    """Answer a code OpenAI emailed, if the inbox can be reached.

    The counterpart of `act_totp`, and the difference is only where the digits
    come from: an authenticator has them already, a mailbox has to be waited
    on. Everything after that - type, submit, let the page settle - is the
    same, because from the page's point of view it is the same box.

    Three ways out, and each is a different thing to tell the operator:

    - a code arrives and is typed, and the router carries on;
    - no mailbox is configured, so nothing here could ever answer this page.
      That is `email_code_required`, exactly as before this screen existed;
    - a mailbox is configured and nothing came within the wait. The account
      is untouched either way - OpenAI judged nothing about it - but "we
      never looked" and "we looked and waited" are not the same sentence.
    """
    field = screen.find_input(ctx.elements)
    if not field:
        return None                       # still painting; the router retries

    # What this page means depends entirely on whether a password went in
    # before it, and the flow already knows: `submitted_password` is set the
    # moment one is typed.
    #
    # After a password, the account is one that *can* hold an authenticator -
    # OpenAI took the password and then asked for an emailed code instead,
    # which says the 2FA on it is not set up. The answer is to set it up, not
    # to fetch the code: fetching it signs the account in once and leaves the
    # next build in exactly the same place.
    #
    # Without one, the account was never offered a password box at all. Those
    # cannot hold a password or an authenticator, so an emailed code is the
    # only way in and fetching it is the whole point.
    if ctx.submitted_password:
        path = ctx.save("email_code_required")
        return Outcome("fatal", "email_code_required",
                       FATAL_ADVICE["email_code_required"],
                       artifacts=[path] if path else [])

    since = ctx.code_since or time.time()
    ctx.code_since = since
    code = ctx.codes.code_for(ctx.creds.email, since=since)
    if code is None:
        reason = ("no_code_source" if isinstance(ctx.codes, codes_mod.NoSource)
                  else "email_code_never_arrived")
        path = ctx.save(reason)
        return Outcome("fatal", reason, FATAL_ADVICE.get(reason, ""),
                       artifacts=[path] if path else [])

    log.info("entering the code emailed to %s", ctx.creds.email)
    fill(ctx, field, code)
    submit(ctx)
    # The same record `act_password` keeps, for the same reason: what comes
    # next is a chat screen, and whether to believe it turns entirely on
    # whether this run is what put the account there.
    ctx.submitted_code = True
    time.sleep(6)
    return None


def act_reset_app(ctx: Context) -> Outcome | None:
    """Get off a chat screen this run did not sign in to.

    Two ways off it, and which one applies is written on the screen.

    **A `Log in` control is on it.** Then nobody is signed in - the app does not
    offer to log in to a session it already has - and the ambiguity this action
    was written for is not there. Tap it and carry on.

    **Nothing to log in with.** Then the app may be holding a session from an
    earlier run, and from outside that is indistinguishable from its logged-out
    mode. `pm clear` settles it. Throwing away someone else's session costs a
    minute; assuming costs a phone handed over with nobody in it, which is what
    happened before this action existed.

    It only ever cleared, and that was slower than it looked: a cleared app
    comes back to a guest chat as often as it comes back to the welcome screen,
    so the clear matched this entry again, and again - three times on phone 695,
    a minute of the five it took (2026-08-13). The `Log in` button was on every
    one of those captures.
    """
    path = ctx.save("logged-out-chat")
    if path:
        ctx.saved.append(path)

    button = screen.find_first(ctx.elements, LOGIN_LABELS)
    if button is not None and screen.tap_element(ctx.client, ctx.phone_id,
                                                 button):
        log.info("the chat screen is up with nobody signed in; taking its "
                 "%r", button.label)
        return None

    log.warning("the chat screen is up, this run has not signed in, and there "
                "is nothing to log in with; clearing the app so its state is "
                "known")
    shell.run(ctx.client, ctx.phone_id, f"pm clear {ctx.package}")
    time.sleep(3)
    launch(ctx.client, ctx.phone_id, ctx.package)
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
    for label in ("Continue", "Next", "Log in", "Submit", "Verify"):
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

    # Above totp_entry, and that ordering is the whole point of it. Both pages
    # ask for a "verification code" in an identical box; only this one says a
    # code was emailed. Matched the other way round, the authenticator screen
    # claims this page and types TOTP codes into it until it runs out of
    # visits (2026-08-07, row 4).
    Screen("email_code_entry",
           lambda c: (c.has(*EMAIL_CODE_TEXTS)
                      and screen.find_input(c.elements) is not None),
           act_email_code, max_visits=3),

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
           lambda c: composer_on_screen(c) and not c.signed_something_in,
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


def reset(client: Client, phone_id: str, package: str) -> None:
    """Put the app back at its welcome screen.

    Between one account and the next, this is not optional. `launch` resumes
    the task the app already had, so a page left behind by the previous
    attempt is still there when the next one starts - and the router matches
    it, and reports the previous account's problem against an address it never
    typed.

    That is not hypothetical. One account genuinely needed an emailed code, and
    the seven tried after it on the same phone were each condemned by its
    verification page: eight archived screens, all naming the first address
    (2026-08-13). Three of the seven had already signed in successfully on
    earlier phones.
    """
    shell.run(client, phone_id, f"pm clear {package}")
    time.sleep(3)


def sign_in(client: Client, phone_id: str, creds: Credentials, *,
            package: str, budget_seconds: float = 600,
            artifact_dir: Path | None = None,
            fresh: bool = False,
            codes: codes_mod.CodeSource | None = None) -> Outcome:
    """Drive the app login to a named outcome.

    `fresh` clears the app first. A caller trying a second account on one phone
    must pass it: see reset().

    Returns rather than raises: a batch needs to record why a row failed and
    move on, not unwind.
    """
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
                  codes=codes or codes_mod.NoSource())

    def logged_in() -> Outcome | None:
        # Unlike the other two steps this reads the screen, because the app's
        # session is not visible to the device. It runs on the elements the
        # last refresh already fetched, so it costs nothing extra.
        if ctx.elements and verified_on_device(ctx):
            return Outcome("success", "logged_in",
                           f"{creds.email}: the composer is on screen")
        return None

    out = router.drive(ctx, SCREENS, is_done=logged_in,
                       budget_seconds=budget_seconds, logger=log)
    if not out.ok:
        return out
    # The router proved a chat screen. This proves whose it is.
    failed = verify_account(ctx)
    if failed is not None:
        return failed
    return Outcome("success", "logged_in",
                   f"the app's own settings name {creds.email}",
                   artifacts=out.artifacts, trail=out.trail)


def sign_in_on_phone(client: Client, phone_id: str, creds: Credentials,
                     **kwargs) -> Outcome:
    """sign_in(), but ensure the phone is up first."""
    phones.ensure_running(client, phone_id)
    return sign_in(client, phone_id, creds, **kwargs)
