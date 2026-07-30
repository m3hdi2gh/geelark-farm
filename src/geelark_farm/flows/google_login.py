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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import phones, screen, shell
from ..accounts import Account
from ..api import Client

log = logging.getLogger(__name__)

# Consent and marketing pages: the label to press, in preference order. These
# are handled as one screen because they are interchangeable - each is "some
# page whose only job is to be dismissed".
DISMISS_LABELS = (
    "I agree", "I AGREE", "Agree", "Accept", "ACCEPT", "I understand",
    "Turn off", "Don't turn on", "DON'T TURN ON", "No thanks", "Not now",
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
}

# Detail lines for the reasons where the fix is not obvious from the name.
FATAL_ADVICE = {
    "captcha_shown":
        "Google is challenging this exit IP; a cleaner proxy is the fix",
    "verification_blocked":
        "Google refused a brand-new device on an unfamiliar network. The "
        "account needs prior history somewhere Google trusts - warming it up "
        "on this proxy first, or using accounts created on it - not a code fix",
    "sign_in_refused":
        "Google declined without saying why; check the screenshot",
    "phone_verification_required":
        "the account wants an SMS code, which this tool has no way to receive",
}


@dataclass
class Outcome:
    """Why the flow stopped. `ok` is the only success, and it is always backed
    by the device, never by what the screen said."""

    kind: str                 # success | fatal | unknown | budget
    reason: str
    detail: str = ""
    artifacts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind == "success"

    def __str__(self) -> str:
        text = f"{self.kind}:{self.reason}"
        return f"{text} - {self.detail}" if self.detail else text


@dataclass
class Context:
    """Everything a screen action needs, refreshed each iteration."""

    client: Client
    phone_id: str
    account: Account
    elements: list[screen.Element] = field(default_factory=list)
    blob: str = ""
    artifact_dir: Path | None = None
    seen: dict[str, int] = field(default_factory=dict)
    saved: list[str] = field(default_factory=list)

    def refresh(self) -> None:
        xml = screen.capture(self.client, self.phone_id)
        self.raw = xml or ""
        self.elements = screen.parse(xml) if xml else []
        self.blob = screen.texts(self.elements)

    def find(self, label: str) -> screen.Element | None:
        return screen.find(self.elements, label)

    def tap(self, label: str) -> bool:
        return screen.tap_label(self.client, self.phone_id, self.elements, label)

    def has(self, *needles: str) -> bool:
        return any(n.casefold() in self.blob for n in needles)

    def save(self, name: str) -> str | None:
        """Archive the current screen so an unrecognised page can become a
        registry entry rather than a mystery."""
        if not self.artifact_dir:
            return None
        stamp = time.strftime("%H%M%S")
        path = self.artifact_dir / f"{stamp}-{name}.xml"
        screen.save_fixture(getattr(self, "raw", ""), path)
        self.saved.append(str(path))
        return str(path)


@dataclass
class Screen:
    """One recognisable page and what to do about it."""

    name: str
    match: Callable[[Context], bool]
    act: Callable[[Context], Outcome | None]
    # A screen that repeats this many times without the flow progressing is
    # stuck: the action is not having the effect it assumes.
    max_visits: int = 4


# --------------------------------------------------------------- primitives
def fill(ctx: Context, element: screen.Element, text: str) -> bool:
    """Focus a field and type into it, replacing anything already there."""
    if not screen.tap_element(ctx.client, ctx.phone_id, element):
        return False
    time.sleep(1)
    if element.text:
        shell.clear_field(ctx.client, ctx.phone_id, max_chars=len(element.text) + 4)
    shell.type_text(ctx.client, ctx.phone_id, text)
    time.sleep(1)
    return True


def submit(ctx: Context) -> None:
    """Advance the form. Google labels the button Next, but the on-screen
    keyboard's enter key works when the button is scrolled out of view."""
    ctx.refresh()
    for label in ("Next", "NEXT", "Continue", "Sign in", "Verify", "Done"):
        if ctx.tap(label):
            return
    shell.keyevent(ctx.client, ctx.phone_id, 66)      # ENTER


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
    code = ctx.account.totp_now()
    log.info("entering a fresh authenticator code")
    fill(ctx, field, code)
    submit(ctx)
    time.sleep(5)
    return None


def act_try_another_way(ctx: Context) -> Outcome | None:
    """Google pushed a prompt to a device the account already trusts - often a
    phone from an earlier run - instead of asking for a code. Switch to the
    authenticator, which is the only factor available unattended."""
    log.info("2FA went to another device; switching to the authenticator")
    if ctx.tap("Try another way"):
        time.sleep(4)
    return None


def act_choose_authenticator(ctx: Context) -> Outcome | None:
    """The list of second factors. Anything but the authenticator needs a human
    or a phone number, so if it is absent this is a dead end."""
    for label in ("Google Authenticator", "Authenticator", "verification code from"):
        if ctx.tap(label):
            time.sleep(5)
            return None
    path = ctx.save("no-authenticator-option")
    return Outcome("fatal", "no_authenticator_option",
                   "the account's 2FA choices do not include an authenticator app",
                   artifacts=[path] if path else [])


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

    Screen("2fa_push_to_other_device",
           lambda c: c.has("try another way") and (
               c.has("check your", "tap yes", "2-step verification")),
           act_try_another_way),

    Screen("2fa_method_list",
           lambda c: c.has("choose how you want to sign in",
                           "try another way to sign in", "other ways to verify"),
           act_choose_authenticator),

    Screen("2fa_code_entry",
           lambda c: (c.has("authenticator", "verification code", "2-step",
                            "enter code")
                      and screen.find_input(c.elements) is not None),
           act_totp),

    Screen("password_entry",
           lambda c: screen.find_input(c.elements, password=True) is not None,
           act_password),

    Screen("email_entry",
           lambda c: (c.has("sign in", "email or phone", "enter your email")
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
    deadline = time.time() + budget_seconds
    unknown_streak = 0

    while time.time() < deadline:
        # Device truth first: the only definition of success.
        if account.email.lower() in shell.device_accounts(client, phone_id):
            return Outcome("success", "signed_in",
                           f"{account.email} is on the device")

        ctx.refresh()
        if not ctx.elements:
            log.info("screen is empty; waiting")
            time.sleep(5)
            continue

        matched = next((s for s in SCREENS if s.match(ctx)), None)
        if matched is None:
            # Could be a transition. Look again before giving up, since a dump
            # taken mid-animation legitimately matches nothing.
            unknown_streak += 1
            if unknown_streak < 3:
                time.sleep(4)
                continue
            path = ctx.save("unknown-screen")
            labels = [e.label for e in ctx.elements if e.label][:12]
            return Outcome("unknown", "unknown_screen",
                           f"nothing matched; on screen: {labels}",
                           artifacts=[path] if path else [])
        unknown_streak = 0

        visits = ctx.seen.get(matched.name, 0) + 1
        ctx.seen[matched.name] = visits
        if visits > matched.max_visits:
            path = ctx.save(f"stuck-{matched.name}")
            return Outcome("unknown", f"stuck_on_{matched.name}",
                           f"handled {visits} times without progress",
                           artifacts=[path] if path else [])

        log.info("screen: %s (visit %d)", matched.name, visits)
        if visits == 1:
            # Archive each page the first time it is seen, so a run builds the
            # catalogue in docs/google-login-screens.md as a side effect.
            ctx.save(matched.name)
        outcome = matched.act(ctx)
        if outcome:
            return outcome

    path = ctx.save("budget-exhausted")
    return Outcome("budget", "budget_exhausted",
                   f"no result within {budget_seconds:.0f}s; "
                   f"screens seen: {dict(ctx.seen)}",
                   artifacts=[path] if path else [])


def sign_in_on_phone(client: Client, phone_id: str, account: Account,
                     **kwargs) -> Outcome:
    """sign_in(), but ensure the phone is up first."""
    phones.ensure_running(client, phone_id)
    return sign_in(client, phone_id, account, **kwargs)
