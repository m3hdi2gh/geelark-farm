"""Row selection and validation.

These decide which rows a batch spends money on, so a bug here creates phones
for accounts that already have one, or signs one account into two phones at
once. Neither raises - both just quietly cost money.
"""

from __future__ import annotations

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
