"""Row selection and validation.

These decide which rows a batch spends money on, so a bug here creates phones
for accounts that already have one, or signs one account into two phones at
once. Neither raises - both just quietly cost money.
"""

from __future__ import annotations

import threading

import pytest

from geelark_farm.accounts import Account
from geelark_farm.sheets import Row, Sheet, _a1_column, selectable


def make_row(number: int, *, email: str = "a@example.com", status: str = "",
             error: str | None = None) -> Row:
    row = Row(
        number=number,
        sheet_row=number + 1,
        values={"email": email, "status": status},
        account=Account(email=email, password="p", totp_secret="JBSWY3DPEHPK3PXP"),
        error=error,
    )
    return row


# ------------------------------------------------------------- selection
def test_a_blank_status_counts_as_pending():
    """A freshly pasted row should be picked up without anyone typing the word
    'pending' into it."""
    assert selectable([make_row(1, status="")]) == [make_row(1, status="")][:1]


def test_done_rows_are_never_processed_again():
    """The property that makes re-running the tool safe."""
    rows = [make_row(1, status="done"), make_row(2, status="pending")]
    assert [r.number for r in selectable(rows)] == [2]


def test_running_rows_are_left_to_the_run_that_holds_them():
    rows = [make_row(1, status="running"), make_row(2, status="pending")]
    assert [r.number for r in selectable(rows)] == [2]


def test_failed_rows_are_skipped_unless_retry_is_asked_for():
    rows = [make_row(1, status="failed:captcha_shown"), make_row(2)]
    assert [r.number for r in selectable(rows)] == [2]
    assert [r.number for r in selectable(rows, retry_failed=True)] == [1, 2]


def test_an_unusable_row_is_never_selected():
    """Validation happens before a phone exists; a bad row must not reach the
    point where one is created for it."""
    rows = [make_row(1, error="totp_secret is not valid base32"), make_row(2)]
    assert [r.number for r in selectable(rows)] == [2]


# ------------------------------------------------------------ duplicates
def test_the_same_address_twice_is_flagged_not_run_twice():
    """Two rows for one account would sign it into two phones and race each
    other's 2FA - both would probably fail, and the account would take the
    blame."""
    rows = [make_row(1, email="dup@example.com"),
            make_row(2, email="other@example.com"),
            make_row(3, email="DUP@example.com")]     # case must not hide it
    Sheet._flag_duplicates(rows)

    assert rows[0].error is None
    assert rows[1].error is None
    assert "duplicate of row 1" in rows[2].error
    assert [r.number for r in selectable(rows)] == [1, 2]


# --------------------------------------------------------------- ranges
def test_column_letters_survive_past_z():
    """Write-back addresses cells by letter; the sheet has nine columns today
    but the mapping must not break if it grows."""
    assert _a1_column(1) == "A"
    assert _a1_column(9) == "I"
    assert _a1_column(26) == "Z"
    assert _a1_column(27) == "AA"
    assert _a1_column(52) == "AZ"


def test_a_row_stuck_on_running_can_be_reclaimed():
    """If the run holding a row dies without writing an outcome, the row stays
    on "running" - neither done nor retryable, so no later run would ever pick
    it up, and the phone it names would be lost with it.

    That happened for real (2026-08-01) when a ConnectionResetError escaped the
    error handling. --retry-failed reclaims such rows.
    """
    rows = [make_row(1, status="running"), make_row(2, status="done")]

    assert [r.number for r in selectable(rows)] == []
    assert [r.number for r in selectable(rows, retry_failed=True)] == [1]


def test_a_retry_never_erases_a_serial_it_could_not_determine():
    """succeed() used to write serial="" unconditionally, so a retry on a row
    that already had one would blank it. An empty write is a loss, not an
    update."""
    written: list[dict] = []

    class FakeSheet(Sheet):
        def __init__(self):
            pass
        def update(self, row, **fields):
            written.append(fields)

    row = make_row(1)
    FakeSheet().succeed(row, phone_id="P1", serial="", note="n")
    assert "serial" not in written[0]

    FakeSheet().succeed(row, phone_id="P1", serial="454", note="n")
    assert written[1]["serial"] == "454"


def test_a_network_blip_does_not_cost_a_row_its_outcome(monkeypatch):
    """This is where a row's outcome becomes durable, so it is the last place
    that should fail on a blip. On 2026-08-06 a ConnectionResetError landed
    here while recording row 13's login failure; it unwound into process_row's
    catch-all, which wrote "error" instead. The run reported a network fault
    where a diagnosis should have been, and the real reason was lost.
    """
    import geelark_farm.sheets as sheets_mod

    attempts: list[list[dict]] = []

    class FlakyWorksheet:
        def batch_update(self, payload):
            attempts.append(payload)
            if len(attempts) < 3:
                raise ConnectionResetError(10054, "forcibly closed")

    sheet = Sheet.__new__(Sheet)
    sheet._ws = FlakyWorksheet()
    sheet._lock = threading.Lock()
    sheet._index = {"status": 4, "note": 7, "updated_at": 8}

    row = make_row(13)
    monkeypatch.setattr(sheets_mod.time, "sleep", lambda _s: None)
    sheet.update(row, status="failed:captcha_shown", note="why")

    assert len(attempts) == 3, "it retried until the write landed"
    assert row.values["status"] == "failed:captcha_shown"


def test_a_write_that_never_lands_says_so_as_a_sheet_error(monkeypatch):
    """Retrying forever would hold a phone; the run has to be told instead, in
    a form its error handling already knows."""
    import geelark_farm.sheets as sheets_mod

    class DeadWorksheet:
        def batch_update(self, payload):
            raise ConnectionResetError(10054, "forcibly closed")

    sheet = Sheet.__new__(Sheet)
    sheet._ws = DeadWorksheet()
    sheet._lock = threading.Lock()
    sheet._index = {"status": 4}
    sheet.WRITE_ATTEMPTS = 2          # keep the test quick
    monkeypatch.setattr(sheets_mod.time, "sleep", lambda _s: None)

    with pytest.raises(sheets_mod.SheetError, match="could not be written"):
        sheet.update(make_row(13), status="failed:x")


# --------------------------------------------------- the app account columns
def test_a_sheet_without_the_app_columns_still_works():
    """The columns were added to sheets that already existed and already had
    rows marked done. Requiring them would have invalidated every one of those,
    to add a step not every row wants."""
    from geelark_farm.accounts import app_credentials, parse_row

    values = {"proxy": "1.2.3.4:1080", "email": "a@example.com",
              "password": "p", "totp_secret": "JBSWY3DPEHPK3PXP"}

    assert app_credentials(values) is None
    assert parse_row(values, number=1).app is None


def test_blank_app_columns_are_the_same_as_absent_ones():
    """Adding the headers should not oblige every row to fill them in."""
    from geelark_farm.accounts import app_credentials

    assert app_credentials({"chatgpt_email": "", "chatgpt_password": "",
                            "chatgpt_totp": ""}) is None


def test_an_unusable_app_account_is_rejected_before_a_phone_is_created():
    """The same rule as the Google credentials: a row that cannot work should
    cost nothing to reject. The message has to say which of the two accounts is
    at fault, or the wrong cell gets corrected."""
    from geelark_farm.accounts import AccountError, parse_row

    values = {"proxy": "1.2.3.4:1080", "email": "a@example.com",
              "password": "p", "totp_secret": "JBSWY3DPEHPK3PXP",
              "chatgpt_email": "b@example.com", "chatgpt_password": "q",
              "chatgpt_totp": "not-base32!"}

    with pytest.raises(AccountError, match="app account"):
        parse_row(values, number=1)


def test_the_app_totp_secret_is_normalised_like_the_google_one():
    """Google shows the key lowercase in groups of four, and so does everyone
    else; base32 wants uppercase and unspaced."""
    from geelark_farm.accounts import app_credentials

    creds = app_credentials({"chatgpt_email": "b@example.com",
                             "chatgpt_password": "q",
                             "chatgpt_totp": "jbsw y3dp ehpk 3pxp"})
    assert creds.totp_secret == "JBSWY3DPEHPK3PXP"
    assert len(creds.totp_now()) == 6


def test_a_failed_row_records_the_serial_of_the_phone_it_left_behind():
    """The serial is what names a phone in GeeLark's own list, so it is what
    someone reads when they go and look at what went wrong - and a failure is
    exactly when they do. Only succeed() wrote it, so every failed row pointed
    at a phone id and an empty serial (2026-08-07, row 1)."""
    written: list[dict] = []

    class FakeSheet(Sheet):
        def __init__(self):
            pass
        def update(self, row, **fields):
            written.append(fields)

    FakeSheet().fail(make_row(1), "app_stuck_on_welcome",
                     phone_id="631709801071509864", serial="503")

    assert written[0]["serial"] == "503"
    assert written[0]["status"] == "failed:app_stuck_on_welcome"

    # And the same rule as succeed(): an empty serial is a loss, not an update.
    FakeSheet().fail(make_row(1), "x", phone_id="P1", serial="")
    assert "serial" not in written[1]


def test_a_retried_write_sends_a_fresh_payload():
    """gspread rewrites the payload it is given: batch_update prefixes every
    range with the worksheet title, in place. Retrying the same list therefore
    sent 'geelark'!'geelark'!I3 and drew a 400 - which is not retryable, so the
    caller recorded "error" and row 2's real reason was lost (2026-08-07).

    The exact loss the retry exists to prevent, caused by the retry.
    """
    seen: list[str] = []

    class PrefixingWorksheet:
        """Behaves as gspread does: mutates, then fails the first time."""
        def batch_update(self, payload):
            for item in payload:
                item["range"] = f"'geelark'!{item['range']}"
                seen.append(item["range"])
            if len(seen) == 1:
                raise ConnectionResetError(10054, "forcibly closed")

    sheet = Sheet.__new__(Sheet)
    sheet._ws = PrefixingWorksheet()
    sheet._lock = threading.Lock()
    sheet._index = {"status": 8}

    sheet.update(make_row(2), status="failed:app_unknown_screen")

    assert seen == ["'geelark'!I3", "'geelark'!I3"], (
        f"the retry re-sent a mutated range: {seen}")


# ------------------------------------------ what a bad totp cell should say
def test_a_cell_holding_the_wrong_thing_says_so_not_padding():
    """b32decode checks the length before the characters, so anything whose
    length is not a multiple of eight comes back as "Incorrect padding" however
    wrong its contents are. A chatgpt_totp cell holding an email address by
    mistake was reported as a padding problem, which points at the wrong thing
    entirely (2026-08-08, row 7)."""
    from geelark_farm.accounts import AccountError, check_totp_secret

    with pytest.raises(AccountError) as caught:
        check_totp_secret("EVIFOPUL007@GMAIL.COM")

    message = str(caught.value)
    assert "not an authenticator key" in message
    assert "'@'" in message and "'.'" in message
    assert "padding" not in message.casefold()


def test_a_secret_pyotp_accepts_is_not_rejected():
    """pyotp pads before decoding, and pyotp is what actually generates the
    codes - so rejecting a length it would have accepted is our bug rather than
    the sheet's."""
    import pyotp

    from geelark_farm.accounts import check_totp_secret

    for secret in ("JBSWY3DPEHPK3PXP",              # 16, already a multiple
                   "JBSWY3DPEHPK3PXPJBSW",          # 20, needs padding
                   "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"):   # 32
        check_totp_secret(secret)                    # no raise
        assert len(pyotp.TOTP(secret).now()) == 6


def test_an_empty_secret_is_named_as_empty():
    from geelark_farm.accounts import AccountError, check_totp_secret

    with pytest.raises(AccountError, match="no totp_secret"):
        check_totp_secret("")
