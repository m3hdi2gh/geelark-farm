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


def run(client: Client, phone_id: str, cmd: str, *, retry: bool = False) -> str:
    """Execute `cmd` on the phone and return stdout.

    retry=True is for commands the caller knows are read-only; /shell/execute
    is not retried by default because a repeated `pm uninstall` or `input tap`
    would act twice.
    """
    data = client.data("/v1/shell/execute", {"id": phone_id, "cmd": cmd},
                       retry=retry) or {}
    if not data.get("status"):
        log.warning("shell reported failure for %r", cmd)
    return data.get("output") or ""


def read(client: Client, phone_id: str, cmd: str) -> str:
    """run() for commands with no side effects, so they can be retried."""
    return run(client, phone_id, cmd, retry=True)


# ------------------------------------------------------------ verification
def device_accounts(client: Client, phone_id: str) -> list[str]:
    """Google accounts actually present on the device, lowercased."""
    output = read(client, phone_id, "dumpsys account")
    return sorted({m.lower() for m in ACCOUNT_RE.findall(output)})


def package_installed(client: Client, phone_id: str, package: str) -> bool:
    """Whether `package` is really installed. The only acceptable proof."""
    output = read(client, phone_id, f"pm list packages {shlex.quote(package)}")
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
    if "%" in text:
        # `input text` decodes %s as a space and has no escape for a literal
        # percent, so any % would be ambiguous. Refuse instead of guessing.
        raise TypingError(
            "`input text` cannot type a literal '%' (it decodes %s as space). "
            "Use a password without '%', or add an IME-based typing backend."
        )


def type_text(client: Client, phone_id: str, text: str) -> None:
    """Type `text` into the focused field, exactly as given.

    Raises TypingError rather than typing something subtly different - a
    password that types wrong looks identical to a wrong password, and costs a
    login attempt against an account's reputation to discover.
    """
    if not text:
        return
    check_typeable(text)
    payload = text.replace(" ", "%s")
    run(client, phone_id, f"input text {shlex.quote(payload)}")


def clear_field(client: Client, phone_id: str, max_chars: int = 64) -> None:
    """Empty the focused field. select-all + delete is unreliable across
    keyboards, so send backspaces - crude, but it always works."""
    run(client, phone_id, f"input keyevent {' '.join(['67'] * max_chars)}")
