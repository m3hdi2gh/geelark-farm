"""The spreadsheet: input rows in, status out.

The sheet is both the work queue and the state store. `status` is what makes a
re-run safe:

    pending          not attempted
    running          claimed by a run in progress
    done             phone ready - skipped on every later run
    failed:<reason>  named failure, e.g. failed:captcha_shown

Columns are located by header name rather than position, so the sheet can be
reordered or annotated without breaking anything. Only the four input columns
are required; the rest are written back and created as needed.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from requests.exceptions import RequestException

from .accounts import (
    Account,
    AccountError,
    app_credentials,
    normalize_totp_secret,
)
from .config import Settings

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

INPUT_COLUMNS = ("proxy", "email", "password", "totp_secret")
# The app account, signed into after the install. Optional: a sheet without
# these columns, or a row that leaves them blank, is a complete row that simply
# stops after the install. They are not in INPUT_COLUMNS because a row is not
# blank-and-skippable just because it has no app credentials.
APP_COLUMNS = ("chatgpt_email", "chatgpt_password", "chatgpt_totp")
OUTPUT_COLUMNS = ("status", "phone_id", "serial", "note", "updated_at")

PENDING, RUNNING, DONE = "pending", "running", "done"


class SheetError(Exception):
    """The spreadsheet cannot be read or written."""


@dataclass
class Row:
    """One spreadsheet row: its credentials, its state, and where it lives."""

    number: int                    # 1-based position among the data rows
    sheet_row: int                 # the actual row number in the sheet
    values: dict[str, str]
    account: Account | None = None
    error: str | None = None       # why the row is unusable, if it is
    _extras: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def status(self) -> str:
        return (self.values.get("status") or "").strip().lower()

    @property
    def email(self) -> str:
        return (self.values.get("email") or "").strip()

    @property
    def phone_id(self) -> str:
        return (self.values.get("phone_id") or "").strip()

    @property
    def is_done(self) -> bool:
        return self.status == DONE

    @property
    def is_failed(self) -> bool:
        return self.status.startswith("failed")

    @property
    def is_pending(self) -> bool:
        # A blank status counts as pending: a freshly pasted row should be
        # picked up without anyone having to type the word.
        return self.status in ("", PENDING)


class Sheet:
    """A worksheet, read once and written back a row at a time.

    Every call through gspread is serialised by a lock. gspread is not
    documented as thread-safe, and with workers running in parallel several
    rows finish at once; a torn write here would corrupt the record of which
    phone belongs to which account. The calls are brief next to a five-minute
    row, so serialising them costs nothing worth measuring.
    """

    def __init__(self, worksheet, headers: list[str]):
        self._ws = worksheet
        self.headers = headers
        self._index = {name: i for i, name in enumerate(headers)}
        self._lock = threading.Lock()

    # ------------------------------------------------------------- opening
    @classmethod
    def open(cls, settings: Settings) -> Sheet:
        settings.require_sheets()
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as exc:                                # pragma: no cover
            raise SheetError(f"missing dependency: {exc}") from exc

        credentials = Credentials.from_service_account_file(
            str(settings.service_account_json), scopes=SCOPES
        )
        client = gspread.authorize(credentials)

        try:
            spreadsheet = client.open_by_key(settings.sheet_id)
            worksheet = spreadsheet.worksheet(settings.sheet_tab)
        except Exception as exc:                                  # gspread errors vary
            raise SheetError(cls._explain(exc, settings)) from exc

        headers = [h.strip() for h in worksheet.row_values(1)]
        missing = [c for c in INPUT_COLUMNS if c not in headers]
        if missing:
            raise SheetError(
                f"the sheet is missing required column(s): {', '.join(missing)}\n"
                f"row 1 must contain: {', '.join(INPUT_COLUMNS + OUTPUT_COLUMNS)}\n"
                f"found: {headers}"
            )
        return cls(worksheet, headers)

    @staticmethod
    def _explain(exc: Exception, settings: Settings) -> str:
        """Turn Google's API errors into the action that fixes them."""
        text = str(exc)
        email = "the service account"
        try:
            import json
            email = json.loads(
                settings.service_account_json.read_text(encoding="utf-8")
            ).get("client_email", email)
        except Exception:                                          # noqa: BLE001
            pass

        if "PERMISSION_DENIED" in text or "403" in text:
            return (f"the service account cannot open this sheet.\n"
                    f"Share it with {email} as an Editor.")
        if "not found" in text.lower() or "404" in text:
            return (f"no sheet with id {settings.sheet_id!r}, or no tab named "
                    f"{settings.sheet_tab!r}.\n"
                    f"The tab name is the label at the bottom of the page.")
        if "API has not been used" in text or "SERVICE_DISABLED" in text:
            return ("the Google Sheets API is not enabled for this project.\n"
                    "Enable it in the Cloud console under APIs & Services.")
        return f"could not open the sheet: {exc}"

    # ------------------------------------------------------------- reading
    def read(self) -> list[Row]:
        """Every data row, with its credentials validated.

        Validation happens here rather than at use, because a row that cannot
        work should be rejected before a phone is created for it.
        """
        with self._lock:
            raw = self._ws.get_all_values()
        rows: list[Row] = []
        for offset, line in enumerate(raw[1:], start=1):
            values = {
                name: (line[i].strip() if i < len(line) else "")
                for name, i in self._index.items()
            }
            if not any(values.get(c) for c in INPUT_COLUMNS):
                continue                       # a blank spacer row, not a gap
            row = Row(number=len(rows) + 1, sheet_row=offset + 1, values=values)
            try:
                account = Account(
                    email=values["email"],
                    password=values["password"],
                    totp_secret=normalize_totp_secret(values["totp_secret"]),
                    proxy=values["proxy"],
                    row=row.number,
                    app=app_credentials(values),
                )
                account.validate()
                row.account = account
            except (AccountError, KeyError) as exc:
                row.error = str(exc)
            rows.append(row)

        self._flag_duplicates(rows)
        return rows

    @staticmethod
    def _flag_duplicates(rows: list[Row]) -> None:
        """Two rows for one address would sign the same account into two
        phones and race each other's 2FA."""
        seen: dict[str, int] = {}
        for row in rows:
            key = row.email.lower()
            if not key:
                continue
            if key in seen:
                row.error = (row.error or
                             f"duplicate of row {seen[key]} ({row.email})")
            else:
                seen[key] = row.number

    # ------------------------------------------------------------- writing
    def update(self, row: Row, **fields: str) -> None:
        """Write back the output columns for one row, in a single API call.

        Unknown or absent columns are skipped rather than failing: someone who
        deletes the `note` column should lose notes, not the whole run.
        """
        fields.setdefault("updated_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        payload = []
        for name, value in fields.items():
            index = self._index.get(name)
            if index is None:
                log.debug("no %r column in the sheet; skipping", name)
                continue
            payload.append({
                "range": f"{_a1_column(index + 1)}{row.sheet_row}",
                "values": [[value]],
            })
            row.values[name] = value
        if payload:
            self._write(payload, row)

    # How many times a write is attempted before the outcome is given up on.
    WRITE_ATTEMPTS = 4

    def _write(self, payload: list[dict], row: Row) -> None:
        """Send a batch update, retrying transient network failures.

        This is where a row's outcome becomes durable, so it is the last place
        that should fail on a blip. On 2026-08-06 a ConnectionResetError landed
        here while recording row 13's login failure; the exception unwound into
        process_row's catch-all, which recorded "error" instead - so the run
        reported a network fault where a diagnosis should have been, and the
        reason the login actually failed was lost.

        Only the network is retried. An APIError means Google understood and
        refused - a bad range, a revoked key - and repeating it changes nothing.
        """
        for attempt in range(1, self.WRITE_ATTEMPTS + 1):
            try:
                with self._lock:
                    self._ws.batch_update(payload)
                return
            except (OSError, RequestException) as exc:
                if attempt == self.WRITE_ATTEMPTS:
                    raise SheetError(
                        f"row {row.number}: the sheet could not be written "
                        f"after {attempt} attempts ({exc})"
                    ) from exc
                delay = min(2 ** attempt, 8) + random.uniform(0, 0.5)
                log.warning("row %d: sheet write failed (%s); retrying in "
                            "%.1fs", row.number, exc, delay)
                time.sleep(delay)

    def claim(self, row: Row, phone_id: str = "") -> None:
        self.update(row, status=RUNNING, phone_id=phone_id, note="")

    def succeed(self, row: Row, phone_id: str, serial: str = "",
                note: str = "") -> None:
        fields = {"status": DONE, "phone_id": phone_id, "note": note}
        # Never blank a serial that is already there. A retry that could not
        # determine it must leave the previous value alone rather than erase
        # it - writing an empty string is a loss, not an update.
        if serial:
            fields["serial"] = str(serial)
        self.update(row, **fields)

    def fail(self, row: Row, reason: str, note: str = "",
             phone_id: str = "") -> None:
        self.update(row, status=f"failed:{reason}", note=note,
                    **({"phone_id": phone_id} if phone_id else {}))


def _a1_column(number: int) -> str:
    """1 -> A, 27 -> AA. Sheets ranges are letters, dictionaries are not."""
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def selectable(rows: list[Row], *, retry_failed: bool = False) -> list[Row]:
    """Rows a run should process: pending, valid, and not already done.

    `running` rows are normally left alone, since another run may be holding
    them. `retry_failed` picks them up too, because a row can only be stuck
    there if the run that claimed it died without writing an outcome - and
    without this it is neither done nor retryable, so it is simply lost, along
    with the phone it names.

    Use it when no other run is in progress; `geelark phones --ledger` shows
    whether one is.
    """
    chosen = []
    for row in rows:
        if row.error or not row.account:
            continue
        if row.is_pending:
            chosen.append(row)
        elif retry_failed and (row.is_failed or row.status == RUNNING):
            chosen.append(row)
    return chosen
