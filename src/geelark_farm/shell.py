"""Run shell commands on a phone, and answer questions about its real state.

This module is the project's source of truth. GeeLark's RPA tasks report
success without having acted, so every flow confirms its result here instead of
trusting a task status.

Text entry deserves the attention it gets below: `input text` is the only way
to type, and it mangles spaces and shell metacharacters. Google passwords
contain both.
"""

from __future__ import annotations

import logging
import re
import shlex

from .api import Client

log = logging.getLogger(__name__)

# dumpsys prints one of these per real account. 'com.google' alone is NOT
# evidence of an account - it is the registered authenticator type, present on
# a device with none. Matching it produced a false "signed in" reading once.
ACCOUNT_RE = re.compile(r"name=([^\s,}]+@[^\s,}]+)")


class ShellError(Exception):
    """The device said the command did not run."""


def run(client: Client, phone_id: str, cmd: str, *, retry: bool = False,
        strict: bool = False) -> str:
    """Execute `cmd` on the phone and return stdout.

    retry=True is for commands the caller knows are read-only; /shell/execute
    is not retried by default because a repeated `pm uninstall` or `input tap`
    would act twice.

    strict=True raises instead of returning what a failed command produced,
    which is nothing. Without it "the command did not run" and "the command
    ran and found nothing" arrive as the same empty string - and for the two
    questions this module exists to answer, those are opposite answers with
    the same consequence. A failed `dumpsys account` reads as "nobody is
    signed in", which is the reading that condemns a Gmail that is fine.
    """
    data = client.data("/v1/shell/execute", {"id": phone_id, "cmd": cmd},
                       retry=retry) or {}
    if not data.get("status"):
        if strict:
            raise ShellError(f"the phone would not run {cmd!r}")
        log.warning("shell reported failure for %r", cmd)
    return data.get("output") or ""


def read(client: Client, phone_id: str, cmd: str, *,
         strict: bool = False) -> str:
    """run() for commands with no side effects, so they can be retried."""
    return run(client, phone_id, cmd, retry=True, strict=strict)


# ------------------------------------------------------------ verification
def device_accounts(client: Client, phone_id: str) -> list[str]:
    """Google accounts actually present on the device, lowercased."""
    # strict, because an empty answer here is a verdict. This is the check
    # that says whether Google is signed in, and a command that did not run
    # says "nobody is" just as convincingly as one that ran and found nobody.
    output = read(client, phone_id, "dumpsys account", strict=True)
    return sorted({m.lower() for m in ACCOUNT_RE.findall(output)})


def foreground_package(client: Client, phone_id: str) -> str:
    """Which app is in front, or "" if the device will not say.

    Asking the device rather than reading the screen, because "this is not my
    app" is exactly the judgement a screen cannot be trusted to make - an
    unrecognised page looks the same whether the app is showing something new
    or was never brought to the front at all.

    Empty on any doubt: a caller that cannot find out should carry on rather
    than act on a guess.
    """
    try:
        out = read(client, phone_id,
                   "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
    except Exception:                                             # noqa: BLE001
        return ""
    found = re.search(r"([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/", out)
    return found.group(1) if found else ""


def package_installed(client: Client, phone_id: str, package: str) -> bool:
    """Whether `package` is really installed. The only acceptable proof."""
    # strict for the same reason as device_accounts: this is proof, and a
    # command that never ran must not be able to disprove anything.
    output = read(client, phone_id, f"pm list packages {shlex.quote(package)}",
                  strict=True)
    return f"package:{package}" in output


def third_party_packages(client: Client, phone_id: str) -> list[str]:
    output = read(client, phone_id, "pm list packages -3")
    return sorted(line.removeprefix("package:").strip()
                  for line in output.splitlines() if line.startswith("package:"))


# -------------------------------------------------------------- interaction
def tap(client: Client, phone_id: str, x: int, y: int) -> None:
    run(client, phone_id, f"input tap {x} {y}")


def keyevent(client: Client, phone_id: str, code: int) -> None:
    """Send a key. 66 = ENTER, 61 = TAB, 4 = BACK, 67 = DEL."""
    run(client, phone_id, f"input keyevent {code}")


def launch_url(client: Client, phone_id: str, url: str) -> str:
    """Open a URL or deep link with the default handler."""
    return run(client, phone_id,
               f"am start -a android.intent.action.VIEW -d {shlex.quote(url)}")


def force_stop(client: Client, phone_id: str, package: str) -> None:
    run(client, phone_id, f"am force-stop {shlex.quote(package)}")


# --------------------------------------------------------------- typing
# `input text` has two independent hazards, and both must be handled or a
# password silently types as something else:
#
#   1. The shell. /shell/execute runs the string through a shell, so $ ` \ " '
#      and friends are interpreted before `input` ever sees them. Fixed by
#      single-quoting the whole argument (shlex.quote).
#   2. `input text` itself. It splits its argument on spaces, and decodes %s as
#      a space. So a literal space must be sent as %s, and a literal % must be
#      avoided entirely - there is no escape for it.
#
# Anything outside printable ASCII cannot be typed this way at all.
_TYPEABLE = re.compile(r"^[\x20-\x7e]*$")


class TypingError(Exception):
    """The text cannot be typed reliably on this device."""


def check_typeable(text: str) -> None:
    """Raise TypingError if `text` cannot be typed exactly as given.

    Separate from type_text so a password can be validated offline, before a
    phone is created for it - a row that cannot be typed should fail in
    validation, not halfway through a login.
    """
    if not _TYPEABLE.match(text):
        bad = sorted({c for c in text if not _TYPEABLE.match(c)})
        raise TypingError(
            f"cannot type non-ASCII characters {bad} with `input text`; "
            f"an IME such as ADBKeyboard would be required"
        )


def type_segments(text: str) -> list[str]:
    """Split `text` into payloads that `input text` will type verbatim.

    Two facts about `input text` drive this (AOSP `Input.java`):

    - it turns the two-character sequence `%s` into a space, and deletes the
      `%`;
    - a `%` followed by anything else - including the end of the argument - is
      left alone.

    So a space is sent as `%s`, and a literal `%` is safe unless an `s` happens
    to follow it. That one case is handled by ending the call right after the
    `%` and starting the next one with the `s`, which types both literally.

    This replaces a blanket refusal of `%`, which had blocked an otherwise
    perfectly good account whose password contained one.
    """
    segments: list[str] = []
    current = ""
    for char in text:
        piece = "%s" if char == " " else char
        if current.endswith("%") and piece.startswith("s"):
            segments.append(current)
            current = ""
        current += piece
    if current:
        segments.append(current)
    return segments


def type_text(client: Client, phone_id: str, text: str) -> None:
    """Type `text` into the focused field, exactly as given.

    Raises TypingError rather than typing something subtly different - a
    password that types wrong looks identical to a wrong password, and costs a
    login attempt against an account's reputation to discover.
    """
    if not text:
        return
    check_typeable(text)
    for payload in type_segments(text):
        run(client, phone_id, f"input text {shlex.quote(payload)}")


MOVE_END = 123          # KEYCODE_MOVE_END
BACKSPACE = 67          # KEYCODE_DEL, deletes to the LEFT of the cursor
FORWARD_DELETE = 112    # KEYCODE_FORWARD_DEL, deletes to the right


def clear_field(client: Client, phone_id: str, max_chars: int = 64) -> None:
    """Empty the focused field.

    select-all + delete is unreliable across keyboards, so this deletes
    character by character - but backspace only removes what is to the LEFT of
    the cursor, and a field is focused by tapping it, which puts the cursor
    wherever the tap landed. On a filled field that is the middle of the text,
    so everything to the right survived: an email box was retyped four times
    and grew "com" on each pass, until the address in it was
    `...@gmail.comcomcom` (2026-08-08, row 7).

    So the cursor is sent to the end first, and forward deletes follow the
    backspaces. Either alone would do if the other always worked - together
    they hold whether or not a web view honours MOVE_END.
    """
    keys = ([MOVE_END] + [BACKSPACE] * max_chars + [FORWARD_DELETE] * max_chars)
    run(client, phone_id,
        f"input keyevent {' '.join(str(k) for k in keys)}")
