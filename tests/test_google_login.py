"""The login router's recognition, against hierarchies captured from real runs.

Every fixture here is a page Google actually served. That is the point: the
router's whole value is that its selectors are observed rather than guessed, and
these tests are what stop a guess from creeping back in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from geelark_farm import screen
from geelark_farm.accounts import Account
from geelark_farm.flows import google_login as login

FIXTURES = Path(__file__).parent / "fixtures"

ACCOUNT = Account(email="testaccount001@example.com", password="x",
                  totp_secret="JBSWY3DPEHPK3PXP")


def context_from(fixture: str) -> login.Context:
    xml = (FIXTURES / fixture).read_text(encoding="utf-8")
    ctx = login.Context(client=None, phone_id="PHONE", account=ACCOUNT)
    ctx.elements = screen.parse(xml)
    ctx.blob = screen.texts(ctx.elements)
    return ctx


def matched_screen(ctx: login.Context):
    return next((s for s in login.SCREENS if s.match(ctx)), None)


# ------------------------------------------------- typographic punctuation
def test_google_apostrophes_are_folded_so_ascii_selectors_match():
    """Google writes "Couldn't" with U+2019. A selector typed with an ASCII
    apostrophe missed it, which turned a named failure into "unknown screen"
    on the first real run."""
    assert screen.normalize("Couldn’t sign you in") == "Couldn't sign you in"
    assert screen.normalize("Play Pass") == "Play Pass"
    assert screen.normalize("say “hello”") == 'say "hello"'


def test_the_verification_block_is_recognised_and_named():
    """The exact page that ended run 1. It must be fatal with a specific
    reason, not 'unknown', because the fix is account provenance rather than
    anything in this code."""
    ctx = context_from("google-verification-blocked.xml")
    assert login._fatal_reason(ctx) == "verification_blocked"

    found = matched_screen(ctx)
    assert found is not None
    assert found.name == "fatal"

    outcome = found.act(ctx)
    assert outcome.kind == "fatal"
    assert outcome.reason == "verification_blocked"
    assert "unfamiliar network" in outcome.detail


def test_a_dead_end_is_not_treated_as_a_page_to_dismiss():
    """The page offers 'TRY AGAIN', which is in DISMISS_LABELS' neighbourhood.
    Fatal must win, or the router would loop until its budget expired."""
    ctx = context_from("google-verification-blocked.xml")
    assert matched_screen(ctx).name == "fatal"


@pytest.mark.parametrize("text,expected", [
    ("Confirm you’re not a robot", "captcha_shown"),
    ("Wrong password. Try again", "wrong_password"),
    ("This account has been disabled", "account_disabled"),
    ("Verify your phone number to continue", "phone_verification_required"),
    ("Couldn’t find your Google Account", "email_not_found"),
])
def test_each_fatal_reason_is_detected_from_its_screen_text(text, expected):
    ctx = login.Context(client=None, phone_id="P", account=ACCOUNT)
    ctx.blob = screen.normalize(text).casefold()
    assert login._fatal_reason(ctx) == expected


def test_an_ordinary_consent_page_is_not_fatal():
    ctx = login.Context(client=None, phone_id="P", account=ACCOUNT)
    ctx.blob = screen.normalize("Don’t turn on backup").casefold()
    assert login._fatal_reason(ctx) is None


# ------------------------------------------------------- 2FA option ranking
def test_the_authenticator_is_taken_when_it_is_on_screen():
    """The costliest bug so far. Google shows the authenticator row and "Try
    another way" on the SAME page. Two live runs pressed "Try another way",
    which Google read as "I have nothing else" and refused the sign-in with
    "You didn't provide enough info". The authenticator must always win."""
    ctx = context_from("google-2fa-method-list.xml")

    assert login.authenticator_offered(ctx)
    assert matched_screen(ctx).name == "2fa_authenticator_offered"


def test_try_another_way_is_a_last_resort_not_a_first_move():
    ctx = context_from("google-2fa-method-list.xml")
    # The page does offer it - the router simply must not choose it here.
    assert ctx.has("try another way")
    assert matched_screen(ctx).name != "2fa_push_to_other_device"


def test_try_another_way_still_applies_when_no_authenticator_is_offered():
    ctx = login.Context(client=None, phone_id="P", account=ACCOUNT)
    ctx.blob = ("2-step verification check your pixel tap yes "
                "try another way").casefold()
    ctx.elements = []
    assert not login.authenticator_offered(ctx)
    assert matched_screen(ctx).name == "2fa_push_to_other_device"


def test_the_code_field_is_found_although_it_has_no_label():
    """Google's verification-code box carries no text and no content-desc, so it
    is findable only by class. This is the field the whole 2FA path depends on."""
    ctx = context_from("google-2fa-code-entry.xml")
    assert matched_screen(ctx).name == "2fa_code_entry"

    field = screen.find_input(ctx.elements)
    assert field is not None
    assert field.label == ""          # nothing to match on but the class
    assert field.focused
