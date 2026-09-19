"""Sign an app account into Claude, once the app is installed.

The same router as the ChatGPT login, and the same discipline: the screens
are matched from captures of a live phone, the credential goes in only by
the email path, and a chat screen is believed only once this run has put
a code into the app - and then the app's own settings page is read back
to name the account.

## The path

    welcome  ->  email typed, "Continue with Email"  ->  code  ->  chat

Captured on phone 2963 (2026-09-16). The welcome screen carries one
button, "Continue with Google", and one box, "Enter your email"; the
arrow that submits the address ("Continue with Email") only appears once
something is typed. The code page says "Enter the code we sent to your
email" over a single box, and takes the code the moment six digits are in
it - there is no button. A refused code leaves "Incorrect code" under the
box with the digits still in it. A right one lands straight on the chat
screen: "Evening, Mehdi", a box whose placeholder is "Chat with Claude...",
and beside it "Add to chat", "Start speech input" and "Voice Mode".

## Reading the account back

"Open menu" (top left) opens the sidebar; its bottom-left control is
"<name>, Settings"; the settings page names the address as its first
line. That is the app naming its session - the standard `dumpsys
account` sets for Google - and it is what `verify_account` reads.

## Where the code comes from

Claude has no password: the code it emails is the whole credential. It is
asked of `Context.codes` - the panel's answer through the store
(store.codes.PgCodes) in a builder container, nothing anywhere else - and
the contract's three tries apply here: the third refused code ends the
attempt as `wrong_code`, and a wait that runs out ends it as
`code_timeout`. Neither says anything about the account.

## What this flow will not do

**Continue with Google.** That signs in the Gmail that owns the phone,
not the account the panel sent, and it looks like success. Guarded the
way the ChatGPT flow guards it: nothing matching "google" is ever tapped.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import codes as codes_mod
from .. import phones, screen, shell
from ..accounts import Credentials
from ..api import Client
from . import router
from .router import Outcome, Screen, act_wait, fill, still_loading

log = logging.getLogger(__name__)

PACKAGE = "com.anthropic.claude"

#: The welcome screen, as captured: its two ways in.
WELCOME_TEXTS = ("continue with google", "enter your email")
#: The arrow beside the typed address (a content-desc, not text).
EMAIL_SUBMIT_LABELS = ("Continue with Email",)
#: Never tapped, whatever label happens to match.
GOOGLE_BUTTON = "google"

#: The code page, as captured.
CODE_PAGE_TEXTS = ("enter the code we sent to your email", "we sent an email to")
#: What the page says under the box when a code is refused (captured with
#: 000000 on 2963). "expired" is a guess at the wording of a stale one.
WRONG_CODE_TEXTS = ("incorrect code", "invalid code", "expired")
#: How many refused codes end the attempt - the contract's number, and the
#: store's (store.codes.TRIES).
MAX_WRONG_CODES = 3
#: How long after the sixth digit to look for the app's verdict.
VERDICT_SECONDS = 8.0

#: Anything containing one of these cannot proceed unattended.
FATAL_TEXTS = {
    "captcha_shown": (
        "verify you are human", "verify you're human", "i'm not a robot",
        "confirm you are human", "checking if the site connection is secure",
    ),
    "rate_limited": ("too many attempts", "too many requests",
                     "try again later"),
    "account_disabled": ("account has been disabled", "account is disabled",
                         "has been suspended"),
    "email_not_accepted": ("enter a valid email",
                                      "invalid email"),
}

FATAL_ADVICE = {
    "captcha_shown":
        "Anthropic is challenging this exit IP; a cleaner proxy is the fix "
        "and no code change helps",
    "rate_limited":
        "Anthropic is rate-limiting sign-ins from this exit or for this "
        "address; wait, or use another exit",
    "account_disabled":
        "the app says the account is disabled; nothing on a phone changes "
        "that",
    "email_not_accepted":
        "the app would not take the address as an email; check the row",
    "no_code_source":
        "Claude emails a code and nothing was connected that could supply "
        "it. The builders read codes from the store, so this means the run "
        "was started without one; the phone is reused",
    "code_timeout":
        "Claude emailed a code and the panel supplied none in the time "
        "allowed. Nothing was judged about the account; POST /ready puts it "
        "back in the queue when the customer is at their keyboard. The "
        "phone is reused",
    "wrong_code":
        "three codes in a row were refused by the app. Nothing was judged "
        "about the account beyond that; POST /ready puts it back in the "
        "queue, and a fresh code is asked for. The phone is reused",
    "app_wrong_account":
        "the app's settings name a different address from the one this run "
        "typed - a session left by an earlier run, or the wrong account. "
        "The app is cleared on the next attempt",
    "session_unverified":
        "the chat screen came up but the walk to the settings page, which "
        "names the account, did not reach it; the sign-in is not believed "
        "without it",
}

#: Post-login onboarding, cleared the way the ChatGPT flow clears it: an
#: allowlist of things that proceed or decline, never "press any button".
#: The account that was signed in on 2963 went straight to the chat; a
#: brand-new account may not, and these are what such pages carry.
DISMISS_LABELS = (
    "Continue", "Not now", "Skip", "Maybe later", "Okay", "OK", "Got it",
    "Agree", "I agree", "Accept", "Allow", "Next", "Done", "Get started",
)

#: Never tapped by the dismiss path, whatever label happens to match.
#: The allowlist above is matched partially, which is what lets one of
#: its words reach a control that does something else: on Chrome's
#: first-run page "Continue" finds "Continue as <first name>" and signs
#: the browser into the phone's own Gmail. The Spotify flow did exactly
#: that (3644, 2026-09-19), and this flow runs on the same warm phones
#: with the same word in its list. `GOOGLE_BUTTON` was guarded only at
#: the taps that go looking for it, never here.
NEVER_DISMISSED = ("google", "apple", "microsoft", "facebook",
                   "continue as", "use another account",
                   "sign up", "create account")

#: The chat screen's composer placeholder (captured), plus the wordings
#: Anthropic has used elsewhere, so a reword costs one entry, not a build.
COMPOSER_PLACEHOLDERS = (
    "Chat with Claude", "Message Claude", "Reply to Claude",
    "How can I help you today", "Ask Claude",
)
#: The controls beside it (captured) - the affordances outlast the wording.
COMPOSER_CONTROLS = ("Add to chat", "Start speech input", "Voice Mode",
                     "Incognito chat")

#: The walk from the chat to the page that names the account. `Open menu`
#: is the start of a longer content-desc ("Open menu, new feature
#: available"), so it is matched as a prefix; the settings control's desc
#: is "<name>, Settings", matched on its tail.
MENU_DESC_PREFIX = "open menu"
SETTINGS_DESC_SUFFIX = "settings"
EMAIL_TEXT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.ASCII)
#: How many screens the walk may take before it is called not a pass.
WALK_STEPS = 6


@dataclass
class Context(router.Context):
    """The generic context plus the account being signed in."""

    creds: Credentials = None                                   # type: ignore
    package: str = PACKAGE
    codes: codes_mod.CodeSource = field(default_factory=codes_mod.NoSource)
    #: When this attempt reached the code page. Mail older than that is an
    #: earlier attempt's.
    code_since: float = 0.0
    #: How many times the address was submitted from the welcome screen.
    email_submissions: int = 0
    #: Set once this run has put a code into the app. A chat screen before
    #: that is not this run's doing and is not believed.
    submitted_code: bool = False
    #: The last code typed, and whether the app has yet said what it thinks
    #: of it. "Incorrect code" under a box still holding it is the verdict.
    last_code: str = ""
    awaiting_verdict: bool = False
    wrong_codes: int = 0

    @property
    def signed_something_in(self) -> bool:
        return self.submitted_code


# ------------------------------------------------------------------ screens
def on_welcome(ctx: Context) -> bool:
    return ctx.has(*WELCOME_TEXTS) and screen.find_input(ctx.elements) is not None


def on_code_page(ctx: Context) -> bool:
    return ctx.has(*CODE_PAGE_TEXTS) and screen.find_input(ctx.elements) is not None


def composer_on_screen(ctx: Context) -> bool:
    """Whether the chat screen is up. NOT whether anyone is signed in."""
    if screen.find_first(ctx.elements, COMPOSER_CONTROLS) is not None:
        return True
    if screen.find_input(ctx.elements) is None:
        return False
    return screen.find_first(ctx.elements, COMPOSER_PLACEHOLDERS) is not None


def verified_on_device(ctx: Context) -> bool:
    """The composer only counts once this run has put a code in - the same
    rule the ChatGPT flow paid for (a phone handed over with nobody in the
    app, 2026-08-08)."""
    return ctx.signed_something_in and composer_on_screen(ctx)


def _fatal_reason(ctx: Context) -> str | None:
    for reason, needles in FATAL_TEXTS.items():
        if ctx.has(*needles):
            return reason
    return None


def act_fatal(ctx: Context) -> Outcome:
    reason = _fatal_reason(ctx) or "unknown_fatal"
    path = ctx.save(reason)
    return Outcome("fatal", reason,
                   FATAL_ADVICE.get(reason,
                                    "the screen says this cannot proceed "
                                    "unattended"),
                   artifacts=[path] if path else [])


def act_welcome(ctx: Context) -> Outcome | None:
    """Type the address and take the email path - never the Google one.

    `fill` clears whatever the box holds first: after "Change email
    address" the previous address is still in it, and typing into that
    made `islandalaskans@mehdi.savant@gmail.comgmail.com` (2963,
    2026-09-16).
    """
    box = screen.find_input(ctx.elements, password=False)
    if box is None:
        return None
    typed = box.text.strip().casefold() == ctx.creds.email.casefold()
    if not typed:
        log.info("entering the app account's email address")
        if not fill(ctx, box, ctx.creds.email):
            return None
        ctx.refresh()
    arrow = screen.find_first(ctx.elements, EMAIL_SUBMIT_LABELS)
    if arrow is not None and GOOGLE_BUTTON not in arrow.label.casefold():
        if screen.tap_element(ctx.client, ctx.phone_id, arrow):
            ctx.email_submissions += 1
            log.info("submitted the address via %r", arrow.label)
            time.sleep(5)
            return None
    # No arrow drawn yet: the keyboard's enter submits the same field.
    shell.keyevent(ctx.client, ctx.phone_id, 66)
    ctx.email_submissions += 1
    log.info("submitted the address with the keyboard")
    time.sleep(5)
    return None


def _refused(ctx: Context) -> bool:
    return ctx.has(*WRONG_CODE_TEXTS)


def _count_refusal(ctx: Context) -> Outcome | None:
    """A refused code, counted once, and the third one ends the attempt."""
    if not ctx.awaiting_verdict:
        return None
    ctx.awaiting_verdict = False
    ctx.wrong_codes += 1
    log.warning("Claude refused code %r (%d of %d)", ctx.last_code,
                ctx.wrong_codes, MAX_WRONG_CODES)
    wrong = getattr(ctx.codes, "wrong", None)
    if callable(wrong):
        try:
            wrong(ctx.creds.email)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("the refusal was not recorded (%s)", exc)
    if ctx.wrong_codes >= MAX_WRONG_CODES:
        path = ctx.save("wrong_code")
        return Outcome("fatal", "wrong_code", FATAL_ADVICE["wrong_code"],
                       artifacts=[path] if path else [])
    return None


def act_code(ctx: Context) -> Outcome | None:
    """Ask for the emailed code, type it, and read the app's verdict.

    The page takes the code the moment six digits are in the box, so there
    is nothing to press. The app's verdict is read on the next visit, not
    here: a refused code leaves "Incorrect code" under the box and the
    router comes straight back to this page, where it is counted against
    the contract's three, told to the code source (so the panel's
    `tries_left` moves), and the next code is asked for. Not read right
    after typing, because the last code's "Incorrect code" can still be
    on the page while the app is validating the new one, and would be
    counted against it.
    """
    box = screen.find_input(ctx.elements)
    if box is None:
        return None
    if _refused(ctx):
        ended = _count_refusal(ctx)
        if ended is not None:
            return ended
    since = ctx.code_since or time.time()
    ctx.code_since = since
    code = ctx.codes.code_for(ctx.creds.email, since=since)
    if code is None:
        reason = ("no_code_source" if isinstance(ctx.codes, codes_mod.NoSource)
                  else "code_timeout")
        path = ctx.save(reason)
        return Outcome("fatal", reason, FATAL_ADVICE[reason],
                       artifacts=[path] if path else [])
    log.info("entering the code emailed to %s", ctx.creds.email)
    if not fill(ctx, box, code):
        return None
    ctx.last_code = code
    ctx.submitted_code = True
    ctx.awaiting_verdict = True
    if len(code) != 6:
        # Six digits submit themselves; anything else needs the keyboard.
        shell.keyevent(ctx.client, ctx.phone_id, 66)
    time.sleep(VERDICT_SECONDS)
    return None


def act_dismiss(ctx: Context) -> Outcome | None:
    element = screen.find_dismissable(ctx.elements, DISMISS_LABELS,
                                      never=NEVER_DISMISSED)
    if element is not None and screen.tap_element(ctx.client, ctx.phone_id,
                                                  element):
        log.info("dismissed %r", element.label)
        time.sleep(3)
    return None


def act_reset_app(ctx: Context) -> Outcome | None:
    """A chat screen this run did not sign in to: an earlier session, or
    the app's own logged-out mode. `pm clear` settles which."""
    ctx.save("chat-not-earned")
    log.warning("the chat screen is up and this run has not signed in; "
                "clearing the app so its state is known")
    shell.run(ctx.client, ctx.phone_id, f"pm clear {ctx.package}")
    time.sleep(3)
    launch(ctx.client, ctx.phone_id, ctx.package)
    return None


LAUNCH_ATTEMPTS = 3


def launch(client: Client, phone_id: str, package: str = PACKAGE) -> bool:
    """Bring the app to the front, and check that it came (the ChatGPT
    flow's lesson: a monkey that silently did nothing, twice)."""
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
def _by_desc(elements: list[screen.Element], *, prefix: str = "",
             suffix: str = "") -> screen.Element | None:
    """An element whose content-desc starts or ends with the given words,
    case-insensitively - for the two controls whose desc carries prose
    around the word that matters."""
    for e in elements:
        desc = e.desc.strip().casefold()
        if not desc:
            continue
        if prefix and desc.startswith(prefix):
            return e
        if suffix and desc.endswith(suffix):
            return e
    return None


def account_email_on(elements: list[screen.Element]) -> str | None:
    """The address the settings page names: its first line (captured), or
    the first address anywhere on it. Never a text box: the welcome
    screen's box holds the address this run typed, which is a claim,
    not the app naming its session."""
    return next((e.label.strip() for e in elements
                 if not e.is_input and EMAIL_TEXT.fullmatch(e.label.strip())),
                None)


def verify_account(ctx: Context) -> Outcome | None:
    """Read the signed-in address out of the app's own settings.

    The composer check proves a chat screen; it cannot prove whose. This
    walks chat -> Open menu -> "<name>, Settings" and reads the address
    at the top, which is the app itself naming its session. A walk that
    cannot reach the page is a failure, not a pass; an address that is
    not the one just typed is its own reason.
    """
    for _step in range(WALK_STEPS):
        ctx.check()
        ctx.refresh()
        if not ctx.elements:
            time.sleep(3)
            continue
        named = account_email_on(ctx.elements)
        if named and ctx.has("settings") and not composer_on_screen(ctx):
            if named.casefold() == ctx.creds.email.casefold():
                log.info("the app's settings name %s", named)
                return None
            path = ctx.save("app-wrong-account")
            return Outcome("fatal", "app_wrong_account",
                           f"the app names {named}, not {ctx.creds.email}: "
                           f"{FATAL_ADVICE['app_wrong_account']}",
                           artifacts=[path] if path else [])
        settings = _by_desc(ctx.elements, suffix=SETTINGS_DESC_SUFFIX)
        if settings is not None:
            screen.tap_element(ctx.client, ctx.phone_id, settings)
            time.sleep(4)
            continue
        menu = _by_desc(ctx.elements, prefix=MENU_DESC_PREFIX)
        if menu is not None:
            screen.tap_element(ctx.client, ctx.phone_id, menu)
            time.sleep(4)
            continue
        time.sleep(3)
    path = ctx.save("session-unverified")
    return Outcome("fatal", "session_unverified",
                   FATAL_ADVICE["session_unverified"],
                   artifacts=[path] if path else [])


# Order matters: the first match wins.
SCREENS: list[Screen] = [
    Screen("fatal", lambda c: _fatal_reason(c) is not None, act_fatal,
           max_visits=1),
    Screen("loading", still_loading, act_wait, max_visits=25),
    # Above the welcome screen: the code page has a box too. Visited once
    # per code and once per verdict, three codes at most.
    Screen("code_entry", on_code_page, act_code, max_visits=8),
    Screen("welcome", on_welcome, act_welcome, max_visits=3),
    Screen("logged_out_chat",
           lambda c: composer_on_screen(c) and not c.signed_something_in,
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
            codes: codes_mod.CodeSource | None = None,
            solver_key: str = "",
            watch: Callable[[], None] | None = None) -> Outcome:
    """Drive the Claude login to a named outcome. Returns rather than
    raises, like every flow: a batch records why and moves on.

    `solver_key` is accepted for the builder's sake and unused, as
    `codes` is by the Spotify flow: the builder calls one signature."""
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
        if ctx.elements and verified_on_device(ctx):
            return Outcome("success", "logged_in",
                           f"{creds.email}: the composer is on screen after "
                           f"this run's code")
        return None

    out = router.drive(ctx, SCREENS, is_done=logged_in, watch=watch,
                       budget_seconds=budget_seconds, logger=log)
    if not out.ok:
        return out
    # The router proved a chat screen. This proves whose it is.
    failed = verify_account(ctx)
    if failed is not None:
        failed.trail = out.trail
        return failed
    return Outcome("success", "logged_in",
                   f"the app's own settings name {creds.email}",
                   artifacts=out.artifacts, trail=out.trail)


def sign_in_on_phone(client: Client, phone_id: str, creds: Credentials,
                     **kwargs) -> Outcome:
    phones.ensure_running(client, phone_id)
    return sign_in(client, phone_id, creds, **kwargs)
