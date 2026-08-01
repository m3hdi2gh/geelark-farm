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


def normalize_totp_secret(raw: str) -> str:
    """Google shows the key lowercase in groups of four; base32 wants
    uppercase, unspaced and unpadded."""
    return (raw or "").replace(" ", "").replace("-", "").upper().rstrip("=")


@dataclass(frozen=True)
class Account:
    email: str
    password: str
    totp_secret: str
    proxy: str = ""
    row: int | None = None

    @property
    def label(self) -> str:
        """Short identifier for logs and the ledger."""
        where = f"row {self.row} / " if self.row else ""
        return f"{where}{self.email}"

    def validate(self) -> None:
        """Everything checkable offline. Called before a phone is created,
        because a row that cannot work should cost nothing to reject."""
        if "@" not in self.email:
            raise AccountError(f"{self.email!r} is not an email address")
        if not self.password:
            raise AccountError(f"{self.email}: no password")
        for field, value in (("password", self.password), ("email", self.email)):
            try:
                check_typeable(value)
            except TypingError as exc:
                raise AccountError(
                    f"{self.email}: {field} cannot be typed - {exc}"
                ) from exc
        try:
            base64.b32decode(self.totp_secret, casefold=True)
        except (binascii.Error, ValueError) as exc:
            raise AccountError(
                f"{self.email}: totp_secret is not valid base32 ({exc})"
            ) from exc

    def totp_now(self, *, min_life: float = 8.0) -> str:
        """A code with enough life left to survive being typed.

        A code that expires between typing and submitting reads as a wrong
        code, and Google counts that against the account.
        """
        import pyotp

        totp = pyotp.TOTP(self.totp_secret)
        remaining = totp.interval - (time.time() % totp.interval)
        if remaining < min_life:
            time.sleep(remaining + 0.5)
        return totp.now()


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
