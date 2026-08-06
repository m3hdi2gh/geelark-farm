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


# ------------------------------------------- the g.co/sc security-code page
def test_the_security_code_page_is_not_mistaken_for_the_email_screen():
    """The costliest kind of bug: a wrong answer wearing the wrong name.

    email_entry matched on "sign in", which is also in "Get a code to sign in".
    That page's security-code box is a non-password input, so the entry claimed
    it and typed the address into it. Google answered "This code is invalid",
    the loop repeated until the visit budget ran out, and the row was reported
    as stuck_on_email_entry - having never reached the email screen at all
    (2026-08-05, row 1).

    The fixture is that capture: the field still holds the address that was
    typed into it.
    """
    ctx = context_from("google-security-code-g-co-sc.xml")

    assert ctx.has("get a code to sign in")
    assert matched_screen(ctx).name == "2fa_security_code_prompt"


def test_the_security_code_page_asks_for_another_way():
    """Unattended there is no second browser to visit g.co/sc in, but the page
    offers "Try another way", and on an account with an authenticator that
    leads to the method list and back into the normal path. Confirmed by hand
    on 2026-08-06: one tap, then the sign-in completed unaided."""
    ctx = context_from("google-security-code-g-co-sc.xml")

    assert ctx.has("try another way")
    assert not login.authenticator_offered(ctx)
    assert matched_screen(ctx).act is login.act_try_another_way


def test_the_real_email_screen_still_matches():
    """The counterweight to tightening it. Google's email page is identified by
    text unique to it, so removing "sign in" must not cost us the page itself.
    """
    ctx = context_from("google-email-entry.xml")
    assert matched_screen(ctx).name == "email_entry"


# --------------------------------------------------- a password Google moved
def test_a_changed_password_is_named_rather_than_retyped():
    """Google accepts the address and says the password on file is the old one.
    Nothing on the device fixes that, but the router had no entry for it, so
    password_entry matched and retyped the same password until the visit budget
    ran out - five minutes per row to report stuck_on_password_entry, which
    names the symptom and not the cause (2026-08-06, rows 11 and 12).
    """
    ctx = context_from("google-password-changed.xml")

    assert login._fatal_reason(ctx) == "password_changed"
    assert matched_screen(ctx).name == "fatal"


def test_a_changed_password_costs_the_phone_its_slot():
    """A decision, not a necessity: the phone would work with a corrected
    sheet, but an account whose password moved without us is rarely one we get
    back, and the slot is worth more than the wait."""
    from geelark_farm.orchestrator import UNREUSABLE

    assert "password_changed" in UNREUSABLE


# ------------------------------------------------------ racing the spinner
def test_a_page_that_is_still_loading_is_waited_for_not_acted_on():
    """The costliest way to be wrong: acting on the screen underneath the one
    arriving. Row 13 tapped NEXT, Google began loading, and the flow read the
    email page still showing behind the spinner - so it retyped the address and
    tapped NEXT again, four times, and reported stuck_on_email_entry having
    never left the first screen. Watching it live, it was simply slow.

    The fixture is that capture: a real email page with the progress bar still
    on it, and the address already in the field.
    """
    ctx = context_from("google-email-entry-loading.xml")

    assert login.still_loading(ctx)
    assert matched_screen(ctx).name == "loading"
    assert matched_screen(ctx).act is login.act_wait


def test_the_same_page_without_the_spinner_is_acted_on_normally():
    """The counterweight. Waiting is only right while the bar is there - a
    login that waits for a page already in front of it never finishes."""
    ctx = context_from("google-email-entry.xml")

    assert not login.still_loading(ctx)
    assert matched_screen(ctx).name == "email_entry"


# ------------------------------------------------- the app login, separately
def test_the_app_login_never_takes_the_google_button():
    """"Continue with Google" would sign in whichever account owns the device,
    and the sheet names a different one. Signing in the wrong account is worse
    than failing, because it looks like success."""
    from geelark_farm.flows import chatgpt_login

    assert not any("google" in label.casefold()
                   for label in chatgpt_login.LOGIN_LABELS)


def test_the_app_login_is_the_one_step_with_no_device_truth():
    """Stated as a test so it cannot quietly stop being true. Google's sign-in
    ends with dumpsys naming the address and the install ends with pm naming
    the package; an app's own session lives in private storage that needs root
    to read, so this step's evidence is a composer element on screen.

    Keep it a specific element that cannot appear before sign-in - never the
    absence of an error, which an empty screen also satisfies.
    """
    from geelark_farm import screen
    from geelark_farm.flows import chatgpt_login

    ctx = chatgpt_login.Context(client=None, phone_id="P", creds=ACCOUNT)

    ctx.elements = []
    assert not chatgpt_login.verified_on_device(ctx), "an empty screen is not proof"

    ctx.elements = screen.parse(
        '<hierarchy><node text="Welcome to ChatGPT" class="android.widget.TextView"'
        ' bounds="[0,0][100,100]" /></hierarchy>')
    assert not chatgpt_login.verified_on_device(ctx)

    ctx.elements = screen.parse(
        '<hierarchy><node text="Message ChatGPT" class="android.widget.EditText"'
        ' bounds="[0,0][100,100]" /></hierarchy>')
    assert chatgpt_login.verified_on_device(ctx)


def app_context(fixture: str):
    from geelark_farm.flows import chatgpt_login
    xml = (FIXTURES / fixture).read_text(encoding="utf-8")
    ctx = chatgpt_login.Context(client=None, phone_id="P", creds=ACCOUNT)
    ctx.elements = screen.parse(xml)
    ctx.blob = screen.texts(ctx.elements)
    return ctx


def app_screen(ctx):
    from geelark_farm.flows import chatgpt_login
    return next((s for s in chatgpt_login.SCREENS if s.match(ctx)), None)


def test_the_login_page_is_not_mistaken_for_the_welcome_screen():
    """"Log in or sign up" is the button on the welcome screen AND the heading
    of the page it opens. Matching that phrase alone, the flow arrived exactly
    where it wanted to be and then tapped the new page's own title until it ran
    out of visits (2026-08-07, row 1). The tap coordinates are the tell: y=1216
    the first time, y=366 the second.

    What separates them is the text box: the login page has one, the welcome
    screen has none.
    """
    ctx = app_context("chatgpt-login-page.xml")

    assert screen.find_input(ctx.elements) is not None
    assert app_screen(ctx).name == "email_entry"


def test_the_welcome_screen_still_matches():
    ctx = app_context("chatgpt-welcome.xml")

    assert screen.find_input(ctx.elements) is None
    assert app_screen(ctx).name == "welcome"


def test_the_web_view_exposes_a_real_text_field():
    """The open question this whole step was written around. OpenAI renders the
    form in a web view, and had its fields not surfaced in the hierarchy the
    approach would have needed replacing. They do: a genuine EditText, clickable
    and focusable (captured 2026-08-07)."""
    ctx = app_context("chatgpt-login-page.xml")

    field = screen.find_input(ctx.elements, password=False)
    assert field is not None
    assert "EditText" in field.cls
    assert field.clickable


def test_the_google_consent_sheet_is_closed_not_accepted():
    """The app raises Google's account chooser by itself after the email path
    is chosen, offering the account that owns the device. Accepting it would
    sign in the wrong account and raise nothing: the app would work, the
    composer would appear, and the row would be recorded as ready.

    It covers the login page completely - while it is up, the page's text field
    is not in the hierarchy at all - so it has to be recognised on its own.
    """
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-google-account-sheet.xml")

    assert screen.find_input(ctx.elements) is None, "the page underneath is gone"
    assert app_screen(ctx).name == "google_account_sheet"
    assert app_screen(ctx).act is chatgpt_login.act_close_google_sheet
    # And the way out is on the sheet itself.
    assert screen.find_first(ctx.elements,
                             chatgpt_login.CLOSE_SHEET_LABELS) is not None


def test_no_login_label_can_ever_match_a_google_button():
    """"Sign in" was in the login labels and matching is partial, so it found
    "Sign in with Google" on the consent sheet and tapped it (2026-08-07). The
    phrase is gone, and a second lock refuses any match containing "google" -
    the app would not complain about the wrong account, which is what makes
    this the worst failure available here."""
    from geelark_farm.flows import chatgpt_login

    for label in chatgpt_login.LOGIN_LABELS:
        assert "sign in" != label.casefold()

    ctx = app_context("chatgpt-google-account-sheet.xml")
    for label in chatgpt_login.LOGIN_LABELS:
        found = ctx.find(label)
        if found is not None:
            assert chatgpt_login.GOOGLE_BUTTON not in found.label.casefold(), (
                f"{label!r} still reaches {found.label!r}")


def test_this_app_version_offers_log_in_another_way():
    """The welcome screen has no "Log in or sign up" any more - the July
    capture's wording. It is a different phrase now, which is why these are
    phrases rather than variants of one word."""
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-welcome-another-way.xml")

    assert app_screen(ctx).name == "welcome"
    found = screen.find_first(ctx.elements, chatgpt_login.LOGIN_LABELS)
    assert found is not None and found.label == "Log in another way"


def test_the_chat_screen_counts_as_signed_in():
    """The first sign-in that actually worked was reported as an unknown
    screen. The flow had reached the chat page - the login was complete, the
    account was in - but the composer's placeholder had been reworded to "Ask
    ChatGPT" since the list was written, so nothing recognised it.

    The fixture is that screen.
    """
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-signed-in.xml")
    assert chatgpt_login.verified_on_device(ctx)


def test_the_login_page_is_never_read_as_signed_in():
    """The counterweight, and the reason a text box alone cannot be the test:
    the login page has one too. Calling that success would hand over a phone
    that never signed in, recorded as ready."""
    from geelark_farm.flows import chatgpt_login

    for fixture in ("chatgpt-login-page.xml", "chatgpt-welcome.xml",
                    "chatgpt-welcome-another-way.xml",
                    "chatgpt-google-account-sheet.xml"):
        ctx = app_context(fixture)
        assert not chatgpt_login.verified_on_device(ctx), fixture


def test_a_reworded_placeholder_does_not_break_the_check_again():
    """Wording moves; the controls beside the composer have not. Either half
    satisfies the second condition, so the next rename costs a fixture rather
    than a failed run."""
    from geelark_farm import screen as screen_mod
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-signed-in.xml")
    # Strip every known placeholder and the check must still hold.
    survivors = []
    for e in ctx.elements:
        label = e.label.casefold()
        if any(p.casefold() == label for p in chatgpt_login.COMPOSER_PLACEHOLDERS):
            continue
        survivors.append(e)
    ctx.elements = survivors

    assert screen_mod.find_first(
        ctx.elements, chatgpt_login.COMPOSER_PLACEHOLDERS) is None
    assert chatgpt_login.verified_on_device(ctx)
