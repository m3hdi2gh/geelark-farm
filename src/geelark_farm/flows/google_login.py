"""Sign a Google account into the device, handling every screen Google shows.

Written by hand rather than using GeeLark's googleLogin RPA task, because that
task cannot be extended: it never reaches "Try another way", ships with
placeholder OCR credentials, and reports success while the device is stranded on
a verification screen.

## Why a router and not a script

Google does not present its login screens in a fixed order. Consent pages
appear or do not, a second factor may be a code prompt or a push to another
device, and any step can be followed by an interstitial. A linear script breaks
on the first variation; this is a loop:

    while within budget:
        if the account is on the device      -> success
        read the screen
        find the first registry entry that matches it
        act, or stop with a named reason

Each SCREEN below is one page: a name, a predicate over the parsed elements, an
action, and whether it is terminal. Supporting a newly observed page means
adding one entry - the loop never changes.

## Outcomes

Success is only ever `dumpsys account` showing the expected address; screen text
claiming success is not evidence. Failures are named, so a run can act on them:
`captcha_shown` needs a better IP, `wrong_password` needs a corrected row, and
`unknown_screen` needs a new registry entry and ships its XML to prove it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .. import phones, screen, shell
from ..accounts import Account
from ..api import Client
from . import router
from .router import Outcome, Screen, act_wait, fill, still_loading

log = logging.getLogger(__name__)

#: How often the loop asks the device whether the account has landed. Not
#: every pass: see `sign_in`. Short enough that the answer is never stale by
#: more than one screen capture.
ACCOUNT_CHECK_SECONDS = 8

# Consent and marketing pages: the label to press, in preference order. These
# are handled as one screen because they are interchangeable - each is "some
# page whose only job is to be dismissed".
#: Matched case-insensitively - `screen.find` casefolds both sides - so one
#: spelling of a word is every spelling of it. This carried `I AGREE`,
#: `ACCEPT` and `DON'T TURN ON` beside their lowercase twins, three entries
#: that could never be reached and an example for whoever adds the next one.
DISMISS_LABELS = (
    "I agree", "Agree", "Accept", "I understand",
    "Turn off", "Don't turn on", "No thanks", "Not now",
    "Skip", "Maybe later", "More", "Next", "Done", "Continue",
)

# Anything containing one of these is a dead end for an unattended run.
# Written in plain ASCII: screen.normalize() folds Google's typographic
# punctuation, so "couldn't" here matches "couldn’t" on screen.
FATAL_TEXTS = {
    "captcha_shown": (
        "confirm you're not a robot", "type the text you hear or see",
        "i'm not a robot",
    ),
    "wrong_password": ("wrong password", "incorrect password"),
    # Google accepts the address, then says the password on file is the old
    # one. Nothing on the device can fix that, and without this the flow simply
    # retyped the same password until the visit budget ran out and reported
    # stuck_on_password_entry - five minutes to not say "the password is stale"
    # (2026-08-06, rows 11 and 12).
    "password_changed": ("your password was changed",),
    # Google's risk check, not a broken account: it decided this device and
    # network are too unfamiliar to trust, and says so explicitly. Distinct
    # from a disabled account because the fix is different - the account needs
    # history on a device Google already trusts.
    "verification_blocked": (
        "didn't provide enough info",
        "use a device where you've signed in before",
    ),
    "account_disabled": (
        "account has been disabled", "account was disabled",
        "account has been locked",
    ),
    "sign_in_refused": ("couldn't sign you in",),
    "phone_verification_required": (
        "verify your phone number", "confirm your phone number",
        "enter a phone number", "get a verification code at",
    ),
    "email_not_found": (
        "couldn't find your google account", "enter a valid email",
    ),
    "too_many_attempts": ("too many failed attempts",),
    # Google rejecting the authenticator code. Without this the flow generated
    # a fresh one and sent it again, four times - four wrong codes against a
    # real account - before reporting stuck_on_2fa_code_entry, which names the
    # screen it was standing on (2026-08-10, row 15).
    "wrong_2fa_code": ("wrong code", "invalid code", "that code didn't work"),
}

# Detail lines for the reasons where the fix is not obvious from the name.
FATAL_ADVICE = {
    "captcha_shown":
        "Google is challenging this exit IP; a cleaner proxy is the fix",
    "password_changed":
        "the password in the sheet is the old one - Google says when it was "
        "changed on the archived screen",
    "verification_blocked":
        "Google refused a brand-new device on an unfamiliar network. The "
        "account needs prior history somewhere Google trusts - warming it up "
        "on this proxy first, or using accounts created on it - not a code fix",
    "sign_in_refused":
        "Google declined without saying why; check the screenshot",
    "phone_verification_required":
        "the account wants an SMS code, which this tool has no way to receive",
    "no_authenticator":
        "this account has no 2FA secret in the sheet, and Google asked for a "
        "code anyway. Nothing here can produce one. Either the account does "
        "have 2FA and its secret is missing from the row, or Google decided "
        "this sign-in needed a second factor and the account cannot give one",
    "wrong_2fa_code":
        "Google rejected the authenticator code, so the totp_secret in the "
        "sheet is not this account's - a fresh code from a wrong secret is "
        "wrong every time, which is why this stops rather than retrying. "
        "Check the secret against the account's authenticator setup",
}


@dataclass
class Context(router.Context):
    """The generic context plus the account being signed in."""

    account: Account = None                                     # type: ignore


# --------------------------------------------------------------- primitives
def submit(ctx: Context) -> None:
    """Advance the form. Google labels the button Next, but the on-screen
    keyboard's enter key works when the button is scrolled out of view."""
    ctx.refresh()
    # One spelling each: `screen.find` casefolds, so `NEXT` after `Next` was
    # a second look for a label the first had already matched.
    for label in ("Next", "Continue", "Sign in", "Verify", "Done"):
        if ctx.tap(label):
            return
    shell.keyevent(ctx.client, ctx.phone_id, 66)      # ENTER


# ------------------------------------------------------------------ screens
# Reasons that describe one option among several rather than a verdict on the
# page. Google's 2-Step list puts "Get a verification code at •••••34" - which
# this tool cannot receive - directly beside "Get a verification code from the
# Google Authenticator app", which it can. Fatal is checked before every other
# entry, so matching the SMS row there killed a login that was one tap from
# working (2026-08-08, row 7).
#
# Everything else in FATAL_TEXTS is a statement about the whole page: a CAPTCHA,
# a changed password, a disabled account. Those stay fatal wherever they appear.
NOT_FATAL_BESIDE_AUTHENTICATOR = frozenset({"phone_verification_required"})

# Google's transient interstitial while it decides what to show next. No
# progress bar on it, so the generic check does not catch it, and there is
# nothing to act on - a row reported unknown_screen from this page having
# simply been read a second too early (2026-08-08, row 1).
CHECKING_TEXTS = ("checking info", "just a moment", "one moment")

# Where the add-account flow actually lives. Settings hosts it and Google Play
# services draws it, so either in front means the flow is still where it should
# be; anything else - a launcher, most likely - means it has been dropped out
# of and has to be started again.
SIGN_IN_PACKAGES = (
    "com.android.settings",
    "com.google.android.gms",
    "com.google.android.gsf",
)


def _fatal_reason(ctx: Context) -> str | None:
    for reason, needles in FATAL_TEXTS.items():
        if not ctx.has(*needles):
            continue
        if reason in NOT_FATAL_BESIDE_AUTHENTICATOR and authenticator_offered(ctx):
            continue
        return reason
    return None


def is_loading(ctx: Context) -> bool:
    """The generic progress bar, or Google saying it is still thinking."""
    return still_loading(ctx) or ctx.has(*CHECKING_TEXTS)


def act_fatal(ctx: Context) -> Outcome:
    reason = _fatal_reason(ctx) or "unknown_fatal"
    path = ctx.save(reason)
    detail = FATAL_ADVICE.get(reason,
                              "the screen says this cannot proceed unattended")
    return Outcome("fatal", reason, detail, artifacts=[path] if path else [])


def act_go_back(ctx: Context) -> Outcome | None:
    """Take the page at its word and go back.

    "Something went wrong. Please go back and try again." is Google's generic
    stumble, and it says what to do about it. Row 1 met it 143 seconds in and
    reported unknown_screen, which reads like a gap in this registry rather
    than what it was: a transient failure with printed instructions.

    Bounded by the entry's visit allowance, so a page that keeps returning
    becomes stuck_on_transient_error - a named, diagnosable outcome instead of
    a loop.
    """
    log.info("Google reported a transient error; going back to retry")
    shell.keyevent(ctx.client, ctx.phone_id, 4)          # BACK
    time.sleep(5)

    # Back does not always return to the previous step - once it closed the
    # sign-in outright and left the phone on its home screen, where nothing
    # matched and the row was reported as unknown_screen with a launcher full
    # of app icons in its archive (2026-08-09, row 1).
    #
    # Asked of the device, because a screen this flow does not recognise looks
    # the same whether Google has shown something new or the flow is no longer
    # in Google at all.
    front = shell.foreground_package(ctx.client, ctx.phone_id)
    if front and not any(front.startswith(p) for p in SIGN_IN_PACKAGES):
        log.warning("back left the sign-in (%s is in front); reopening it", front)
        open_add_account(ctx.client, ctx.phone_id)
    return None


def act_account_picker(ctx: Context) -> Outcome | None:
    """The "Add an account" type list - choose Google."""
    if ctx.tap("Google"):
        time.sleep(4)
    return None


def act_email(ctx: Context) -> Outcome | None:
    field = screen.find_input(ctx.elements, password=False)
    if not field:
        return None
    log.info("entering the email address")
    fill(ctx, field, ctx.account.email)
    submit(ctx)
    time.sleep(4)
    return None


def act_password(ctx: Context) -> Outcome | None:
    field = screen.find_input(ctx.elements, password=True)
    if not field:
        return None
    log.info("entering the password")
    fill(ctx, field, ctx.account.password)
    submit(ctx)
    time.sleep(5)
    return None


def act_totp(ctx: Context) -> Outcome | None:
    """Type an authenticator code with enough life left to survive submission."""
    field = screen.find_input(ctx.elements)
    if not field:
        return None
    if not ctx.account.has_authenticator:
        # Accounts sold without 2FA normally never reach this screen. When one
        # does, Google is asking for something the row cannot produce, and
        # saying so beats an AccountError escaping into the catch-all and
        # arriving in the sheet as "error".
        path = ctx.save("no_authenticator")
        return Outcome("fatal", "no_authenticator",
                       FATAL_ADVICE["no_authenticator"],
                       artifacts=[path] if path else [])
    code = ctx.account.totp_now()
    log.info("entering a fresh authenticator code")
    fill(ctx, field, code)
    submit(ctx)
    time.sleep(5)
    return None


# The authenticator row, most specific phrasing first. The full sentence is the
# tappable list item; "Google Authenticator" alone is an inner span whose centre
# may miss the row.
AUTHENTICATOR_LABELS = (
    "Get a verification code from the Google Authenticator app",
    "verification code from the Google Authenticator",
    "Google Authenticator",
)


def authenticator_offered(ctx: Context) -> bool:
    return screen.find_first(ctx.elements, AUTHENTICATOR_LABELS) is not None


def act_choose_authenticator(ctx: Context) -> Outcome | None:
    """Take the authenticator option, which is the only second factor this tool
    can satisfy on its own."""
    for label in AUTHENTICATOR_LABELS:
        if ctx.tap(label):
            log.info("chose the authenticator option")
            time.sleep(5)
            return None
    path = ctx.save("no-authenticator-option")
    return Outcome("fatal", "no_authenticator_option",
                   "the account's 2FA choices do not include an authenticator app",
                   artifacts=[path] if path else [])


def act_try_another_way(ctx: Context) -> Outcome | None:
    """Only reached when the authenticator is NOT among the visible options.

    "Try another way" asks Google to widen the list, and it is a last resort:
    if the authenticator is already on screen and this is pressed instead,
    Google reads it as "I have nothing else" and refuses the sign-in outright
    with "You didn't provide enough info" (measured 2026-07-30, twice). Hence
    the guard, and hence this screen ranking below the method list.
    """
    log.info("no authenticator option visible; asking for another way")
    if ctx.tap("Try another way"):
        time.sleep(4)
    return None


def act_dismiss(ctx: Context) -> Outcome | None:
    tapped = screen.tap_first_present(ctx.client, ctx.phone_id, ctx.elements,
                                      DISMISS_LABELS)
    if tapped:
        log.info("dismissed %r", tapped)
        time.sleep(3)
    return None


# Order matters: the first match wins, so fatal checks come first and the
# catch-all dismissal comes last.
SCREENS: list[Screen] = [
    Screen("fatal", lambda c: _fatal_reason(c) is not None, act_fatal, max_visits=1),

    # Ranked above every screen that acts, and below fatal only because a page
    # that says the sign-in cannot proceed says so whether or not it is still
    # painting. Waiting is the cheapest thing this loop can do and the login
    # budget bounds it either way, so the visit allowance is generous: the cost
    # of waiting a little too long is seconds, and the cost of acting too early
    # is a whole login.
    Screen("loading", is_loading, act_wait, max_visits=20),

    # Google's generic stumble, which prints its own remedy. Above the acting
    # screens because the page underneath it is not the page it claims to be.
    Screen("transient_error",
           lambda c: c.has("something went wrong",
                           "please go back and try again"),
           act_go_back, max_visits=3),

    # Code entry outranks the method list: once a code box is on screen the
    # choice has already been made, and re-choosing would leave it.
    Screen("2fa_code_entry",
           lambda c: (c.has("authenticator", "verification code", "2-step",
                            "enter code")
                      and screen.find_input(c.elements) is not None),
           act_totp),

    # The authenticator, whenever it is visible. This must outrank "try another
    # way": Google presents both on the same page, and pressing the latter while
    # the former is available gets the sign-in refused outright.
    Screen("2fa_authenticator_offered", authenticator_offered,
           act_choose_authenticator),

    # Only when no authenticator row is present.
    Screen("2fa_push_to_other_device",
           lambda c: (c.has("try another way")
                      and c.has("check your", "tap yes", "2-step verification")
                      and not authenticator_offered(c)),
           act_try_another_way),

    Screen("2fa_method_list",
           lambda c: (c.has("choose how you want to sign in",
                            "other ways to verify")
                      and not authenticator_offered(c)),
           act_choose_authenticator, max_visits=1),

    # Google's g.co/sc challenge: "To get your security code, go to g.co/sc in
    # a new browser window". Unattended, that is a dead end - there is no other
    # browser to go to - but the page also offers "Try another way", and on an
    # account with an authenticator that leads to the method list and straight
    # back into the normal path. Confirmed by hand on 2026-08-06: one tap, then
    # the flow finished the sign-in unaided.
    #
    # Ranked below the authenticator entry for the usual reason: if a screen
    # ever offers both, taking the authenticator is always right.
    Screen("2fa_security_code_prompt",
           lambda c: (c.has("get a code to sign in", "g.co/sc",
                            "get your security code")
                      and not authenticator_offered(c)),
           act_try_another_way, max_visits=2),

    Screen("password_entry",
           lambda c: screen.find_input(c.elements, password=True) is not None,
           act_password),

    # Matched on text unique to the email page, never on "sign in". That
    # phrase appears on half of Google's verification screens - including
    # "Get a code to sign in", whose security-code box is also a non-password
    # input, so this entry claimed it and typed the address into it (2026-08-05,
    # row 1). Google answered "This code is invalid", the loop repeated until
    # the visit budget ran out, and the row failed as "stuck_on_email_entry"
    # while never having been on the email screen at all.
    #
    # A screen this no longer matches is archived as unknown_screen, which is a
    # task. Typing an address into whatever box is on offer is a wrong answer
    # wearing the wrong name.
    Screen("email_entry",
           lambda c: (c.has("email or phone", "enter your email",
                            "forgot email", "use your google account")
                      and screen.find_input(c.elements, password=False) is not None),
           act_email),

    Screen("add_account_picker",
           lambda c: c.has("add an account", "add account") and c.find("Google")
           is not None,
           act_account_picker),

    Screen("dismissable",
           lambda c: screen.find_first(c.elements, DISMISS_LABELS,
                                       clickable_only=True) is not None,
           act_dismiss, max_visits=8),
]


# ------------------------------------------------------------------- driver
def open_add_account(client: Client, phone_id: str) -> None:
    """Start the flow at Android's own add-Google-account entry point.

    Driving Settings ourselves means no dependence on GeeLark's RPA, and the
    intent goes straight there rather than navigating menus whose layout varies
    by Android skin.
    """
    shell.run(client, phone_id, "am force-stop com.android.settings")
    time.sleep(2)
    shell.run(
        client, phone_id,
        "am start -a android.settings.ADD_ACCOUNT_SETTINGS "
        "--esa account_types com.google",
    )
    time.sleep(6)


def sign_in(client: Client, phone_id: str, account: Account, *,
            budget_seconds: float = 900, artifact_dir: Path | None = None,
            already_open: bool = False) -> Outcome:
    """Drive the login to a named outcome.

    Returns rather than raises: a batch needs to record why a row failed and
    move on, not unwind.
    """
    if artifact_dir:
        artifact_dir.mkdir(parents=True, exist_ok=True)

    present = shell.device_accounts(client, phone_id)
    if account.email.lower() in present:
        return Outcome("success", "already_signed_in", f"accounts: {present}")
    if present:
        # A different account on a device we are about to sign into is a
        # mismatch worth naming: Play can refuse installs for the wrong one.
        log.warning("device already has %s", present)

    if not already_open:
        open_add_account(client, phone_id)

    ctx = Context(client=client, phone_id=phone_id, account=account,
                  artifact_dir=artifact_dir)

    # The device is the only truth for this step - the account is either in
    # `dumpsys account` or it is not - so unlike the app login this cannot be
    # read off elements already fetched. It costs a request each time it is
    # asked, out of a budget that is process-wide and bans the key for two
    # hours when it runs out, and four builds at once ask four times.
    #
    # So it is asked on a clock rather than every pass. Google puts the
    # account on the device at the end of a consent it takes several seconds
    # to render; noticing that a few seconds late costs nothing, and the loop
    # is doing a screen capture of its own either way.
    last_asked = [0.0]

    def signed_in() -> Outcome | None:
        now = time.monotonic()
        if now - last_asked[0] < ACCOUNT_CHECK_SECONDS:
            return None
        last_asked[0] = now
        # Not strict: this is a poll, and an empty answer here means "not
        # yet". Raising over one refused `dumpsys` would end the login.
        present = shell.device_accounts(client, phone_id, strict=False)
        if account.email.lower() in present:
            return Outcome("success", "signed_in",
                           f"{account.email} is on the device")
        return None

    return router.drive(ctx, SCREENS, is_done=signed_in,
                        budget_seconds=budget_seconds, logger=log)


def sign_in_on_phone(client: Client, phone_id: str, account: Account,
                     **kwargs) -> Outcome:
    """sign_in(), but ensure the phone is up first."""
    phones.ensure_running(client, phone_id)
    return sign_in(client, phone_id, account, **kwargs)
