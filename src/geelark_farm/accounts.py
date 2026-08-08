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


def normalize_totp_secret(raw: str) -> str:
    """Google shows the key lowercase in groups of four; base32 wants
    uppercase, unspaced and unpadded."""
    return (raw or "").replace(" ", "").replace("-", "").upper().rstrip("=")


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
    if not secret:
        raise AccountError("no totp_secret")

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

    def validate(self, *, what: str = "") -> None:
        """Everything checkable offline. Called before a phone is created,
        because a row that cannot work should cost nothing to reject."""
        where = f"{what} " if what else ""
        if "@" not in self.email:
            raise AccountError(f"{where}{self.email!r} is not an email address")
        if not self.password:
            raise AccountError(f"{where}{self.email}: no password")
        for field, value in (("password", self.password), ("email", self.email)):
            try:
                check_typeable(value)
            except TypingError as exc:
                raise AccountError(
                    f"{where}{self.email}: {field} cannot be typed - {exc}"
                ) from exc
        try:
            check_totp_secret(self.totp_secret)
        except AccountError as exc:
            raise AccountError(f"{where}{self.email}: {exc}") from exc

    def totp_now(self, *, min_life: float = 8.0) -> str:
        """A code with enough life left to survive being typed.

        A code that expires between typing and submitting reads as a wrong
        code, and the service counts that against the account.
        """
        import pyotp

        totp = pyotp.TOTP(self.totp_secret)
        remaining = totp.interval - (time.time() % totp.interval)
        if remaining < min_life:
            time.sleep(remaining + 0.5)
        return totp.now()


@dataclass(frozen=True)
class Account(Credentials):
    """The Google account a phone is built for, and optionally the app account
    to sign into once the app is installed.

    `app` is optional on purpose: a sheet without those columns, or a row with
    them blank, is a complete row that simply stops after the install. Making
    it required would invalidate every existing sheet to add a step that not
    every row wants.
    """

    proxy: str = ""
    row: int | None = None
    app: Credentials | None = None

    @property
    def label(self) -> str:
        """Short identifier for logs and the ledger."""
        where = f"row {self.row} / " if self.row else ""
        return f"{where}{self.email}"

    def validate(self, *, what: str = "") -> None:
        super().validate(what=what)
        if self.app is not None:
            # Named separately in the error, because "the password cannot be
            # typed" is a different row to fix depending on which one it is.
            self.app.validate(what="app account:")


def app_credentials(row: dict) -> Credentials | None:
    """The app account from a sheet row, or None if the row has no such columns
    or leaves them blank."""
    email = (row.get("chatgpt_email") or "").strip()
    password = (row.get("chatgpt_password") or "").strip()
    secret = normalize_totp_secret(row.get("chatgpt_totp") or "")
    if not any((email, password, secret)):
        return None
    return Credentials(email=email, password=password, totp_secret=secret)


def parse_row(row: dict, *, number: int | None = None) -> Account:
    account = Account(
        email=(row.get("email") or "").strip(),
        password=(row.get("password") or "").strip(),
        totp_secret=normalize_totp_secret(row.get("totp_secret") or ""),
        proxy=(row.get("proxy") or "").strip(),
        row=number,
        app=app_credentials(row),
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
