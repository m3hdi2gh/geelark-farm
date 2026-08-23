"""One account's credentials, validated before anything is spent on it.

Normally filled from the spreadsheet. A gitignored TSV under secrets/ can stand
in when no sheet is configured, with the same columns, so the source is the only
thing that differs.
"""

from __future__ import annotations

import base64
import binascii
import csv
import time
from dataclasses import dataclass
from pathlib import Path

from .shell import TypingError, check_typeable


class AccountError(ValueError):
    """The row cannot be used, and no phone should be created for it."""


# A-Z and 2-7. Not 0, 1 or 8, which is why a secret that is really something
# else - an address, a password - almost always contains a character from
# outside it.
BASE32_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


#: Characters that occupy a cell and say nothing. A secret pasted out of a
#: browser or a chat window arrives padded with these, and they are invisible
#: in the sheet - so the row looks perfectly ordinary and is refused for
#: containing a character nobody can see. Two Gpt Info rows sat unusable that
#: way, each holding a valid key behind two U+3164 fillers (2026-08-22).
#:
#: Only the ones that carry no information. Stripping anything outside base32
#: would turn a password or an address pasted into the wrong column into
#: something that decodes, and rejecting those is what this validation is for
#: (2026-08-09, a cell holding `fifa19.900t@pAss`).
INVISIBLE = "​‌‍⁠﻿ ㅤ⠀᠎"


def normalize_totp_secret(raw: str) -> str:
    """Google shows the key lowercase in groups of four; base32 wants
    uppercase, unspaced and unpadded."""
    stripped = "".join(c for c in (raw or "")
                       if not c.isspace() and c not in INVISIBLE)
    return stripped.replace("-", "").upper().rstrip("=")


def check_totp_secret(secret: str) -> None:
    """Raise AccountError if `secret` cannot produce codes, saying which
    problem it has.

    b32decode checks the length before it checks the characters, so anything
    whose length is not a multiple of eight is reported as "Incorrect padding"
    however wrong its contents are. A cell holding an email address by mistake
    came back as a padding complaint, which sent the reader looking at the
    wrong thing entirely (2026-08-08, row 7).

    So the characters are checked first and named, and the length is padded the
    way pyotp pads it - because pyotp is what actually generates the codes, and
    rejecting a secret it would accept would be our bug rather than the sheet's.
    """
    outside = sorted({c for c in secret if c not in BASE32_ALPHABET})
    if outside:
        raise AccountError(
            f"totp_secret is not an authenticator key: it contains "
            f"{', '.join(repr(c) for c in outside)}, and base32 keys are only "
            f"A-Z and 2-7. Check the column - a {len(secret)}-character value "
            f"with these in it is usually something else pasted by mistake"
        )

    padded = secret + "=" * (-len(secret) % 8)
    try:
        base64.b32decode(padded, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise AccountError(f"totp_secret is not valid base32 ({exc})") from exc


@dataclass(frozen=True)
class Credentials:
    """An address, a password and an authenticator secret.

    Two sets of these are signed into on one phone - the Google account that
    owns the device, and the account inside the app - and neither the checks
    nor the code generation differ between them, so they share this.
    """

    email: str
    password: str
    totp_secret: str

    @property
    def has_authenticator(self) -> bool:
        """Whether this account can answer a code prompt at all.

        Some accounts are sold without 2FA. They sign in on the shorter path -
        password, consent, done - and the router never reaches the code screen,
        so the secret is genuinely optional. What is not optional is knowing
        which kind an account is, because a code prompt on one of these is a
        dead end rather than a step.
        """
        return bool(self.totp_secret)

    def validate(self, *, what: str = "") -> None:
        """Everything checkable offline. Called before a phone is created,
        because a row that cannot work should cost nothing to reject."""
        where = f"{what} " if what else ""
        # An "@" alone is not an address. A chatgpt_email cell held
        # "fifa19.900t@pAss" - a password that had drifted into the wrong
        # column - and passed, so a phone was created for it and the sign-in
        # was refused with a reason that pointed at the network (2026-08-09,
        # row 10). Validation exists to reject a row before it costs anything,
        # and this one it let through.
        local, _, domain = self.email.partition("@")
        if not local or "." not in domain or " " in domain or not domain:
            raise AccountError(
                f"{where}{self.email!r} is not an email address - it needs a "
                f"name, an @ and a domain with a dot in it"
            )
        if not self.password:
            raise AccountError(f"{where}{self.email}: no password")
        for field, value in (("password", self.password), ("email", self.email)):
            try:
                check_typeable(value)
            except TypingError as exc:
                raise AccountError(
                    f"{where}{self.email}: {field} cannot be typed - {exc}"
                ) from exc
        # An empty secret is a fact about the account, not a broken row: some
        # are sold without 2FA and sign in on the shorter path. A secret that
        # is PRESENT is still checked, so a cell holding something else - the
        # 'fifa19.900t@pAss' of 2026-08-09 - is still caught here rather than
        # ten minutes into a phone.
        if self.totp_secret:
            try:
                check_totp_secret(self.totp_secret)
            except AccountError as exc:
                raise AccountError(f"{where}{self.email}: {exc}") from exc

    def totp_now(self, *, min_life: float = 8.0) -> str:
        """A code with enough life left to survive being typed.

        A code that expires between typing and submitting reads as a wrong
        code, and the service counts that against the account.
        """
        if not self.totp_secret:
            raise AccountError(
                f"{self.email} has no authenticator secret, so no code can be "
                f"produced for it")
        import pyotp

        totp = pyotp.TOTP(self.totp_secret)
        remaining = totp.interval - (time.time() % totp.interval)
        if remaining < min_life:
            time.sleep(remaining + 0.5)
        return totp.now()


@dataclass(frozen=True)
class Account(Credentials):
    """The Google account a phone is built for.

    It once carried an `app` credential too, read from `chatgpt_email` and
    friends - columns from before the resource tabs existed. Nothing ever read
    it back: it was built, validated, and dropped. The app account comes from
    the `Gpt Info` tab now, claimed separately, so a phone can work through
    several of them (2026-08-23).
    """

    proxy: str = ""
    row: int | None = None

    @property
    def label(self) -> str:
        """Short identifier for logs and the ledger."""
        where = f"row {self.row} / " if self.row else ""
        return f"{where}{self.email}"


def parse_row(row: dict, *, number: int | None = None) -> Account:
    account = Account(
        email=(row.get("email") or "").strip(),
        password=(row.get("password") or "").strip(),
        totp_secret=normalize_totp_secret(row.get("totp_secret") or ""),
        proxy=(row.get("proxy") or "").strip(),
        row=number,
    )
    account.validate()
    return account


def load_dev_accounts(path: str | Path) -> list[Account]:
    """Read the stand-in TSV. Rows are numbered from 1 as in a spreadsheet
    body, so `--row 2` means the same thing here as it will there."""
    file = Path(path)
    if not file.exists():
        raise AccountError(
            f"{file} not found. Set GOOGLE_SHEET_ID to read the sheet, or put "
            f"the accounts there with columns: proxy, email, password, totp_secret"
        )
    lines = file.read_text(encoding="utf-8").splitlines()
    return [parse_row(row, number=i)
            for i, row in enumerate(csv.DictReader(lines, delimiter="\t"), start=1)]
