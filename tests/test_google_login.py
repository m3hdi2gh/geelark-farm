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
    to read, so this step's evidence is a composer element on screen - plus
    this run having actually signed in, because the composer alone is a thing
    the logged-out app has too.
    """
    from geelark_farm import screen
    from geelark_farm.flows import chatgpt_login

    ctx = chatgpt_login.Context(client=None, phone_id="P", creds=ACCOUNT)
    ctx.submitted_password = True

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
    ctx.submitted_password = True
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
    ctx.submitted_password = True
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


# ------------------------------------- what the five-row batch taught (08-07)
def test_googles_transient_error_is_retried_not_reported_as_a_gap():
    """"Something went wrong. Please go back and try again." prints its own
    remedy. Row 1 met it 143 seconds in and reported unknown_screen, which
    reads like a missing registry entry rather than what it was."""
    ctx = context_from("google-transient-error.xml")

    assert matched_screen(ctx).name == "transient_error"
    assert matched_screen(ctx).act is login.act_go_back
    # Bounded: a page that keeps coming back becomes a named outcome, not a loop.
    assert matched_screen(ctx).max_visits <= 3


def test_the_notification_card_is_dismissed_although_nothing_is_clickable():
    """Nothing in the ChatGPT app reports clickable=true - every label, both
    buttons on this card included, is a plain TextView whose centre taps
    correctly. Requiring the flag meant the onboarding entry could never match,
    so a signed-in session sat on this card and was reported as an unknown
    screen (2026-08-07, row 2)."""
    ctx = app_context("chatgpt-notification-card.xml")

    assert all(not e.clickable for e in ctx.elements), "still nothing clickable"
    assert app_screen(ctx).name == "onboarding"

    # And of the two buttons, the one that declines must win.
    from geelark_farm.flows import chatgpt_login
    assert screen.find_first(ctx.elements,
                             chatgpt_login.DISMISS_LABELS).label == "Maybe later"


def test_an_emailed_code_is_answered_on_its_own_screen_not_with_a_totp():
    """This account had no authenticator, so OpenAI emailed a code. The page
    says "verification code", which is what the authenticator page says too, so
    totp_entry claimed it and typed TOTP codes into it three times - each
    answered "Incorrect code", each burning an attempt (2026-08-07, row 4).

    The page used to be named and given up on. It is answered now, and against
    the real capture the thing that matters is unchanged and stronger: the
    screen that claims it is its own, and never the authenticator's.
    """
    ctx = app_context("chatgpt-email-code.xml")

    assert app_screen(ctx).name == "email_code_entry"


def test_the_advice_for_an_emailed_code_says_the_phone_is_fine():
    """The phone signed into Google and installed the app; only the app account
    is unusable. Whoever reads the sheet needs that before deciding to throw it
    away."""
    from geelark_farm.flows import chatgpt_login

    advice = chatgpt_login.FATAL_ADVICE["email_code_required"]
    assert "phone is fine" in advice
    assert "retry" in advice


def test_the_permission_dialog_is_answered_although_nothing_is_clickable():
    """The matcher was fixed and the action was not: tap_first_present defaults
    to clickable_only, so the screen matched eight times and tapped nothing,
    and the row was reported stuck on a dialog one tap from gone (2026-08-07,
    row 2's retry). Android's own permission dialog reports clickable=false
    here too.
    """
    from geelark_farm import screen as screen_mod
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-notification-permission.xml")

    assert all(not e.clickable for e in ctx.elements)
    assert app_screen(ctx).name == "onboarding"
    # The default would find nothing to press; the flow passes False.
    assert screen_mod.find_first(ctx.elements, chatgpt_login.DISMISS_LABELS,
                                 clickable_only=True) is None
    assert screen_mod.find_first(ctx.elements,
                                 chatgpt_login.DISMISS_LABELS) is not None


def test_the_dismiss_list_never_contains_a_refusal():
    """With clickable_only off, the label list is the only thing keeping this
    off the wrong control - and the dialog it answers offers "Don't allow"
    right beside "Allow"."""
    from geelark_farm.flows import chatgpt_login

    for label in chatgpt_login.DISMISS_LABELS:
        assert "don't" not in label.casefold()
        assert "deny" not in label.casefold()


def test_openai_refusing_the_proxys_tls_is_named_as_such():
    """"For your security, ChatGPT can't connect while this network is
    presenting an unexpected SSL certificate." Nothing about the account is
    involved - the proxy is intercepting TLS - and it was reported as an
    unknown screen, which points at the registry instead (2026-08-08, row 4).
    """
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-ssl-rejected.xml")

    assert chatgpt_login._fatal_reason(ctx) == "network_ssl_rejected"
    assert app_screen(ctx).name == "fatal"


def test_a_composer_is_not_proof_of_a_session():
    """The worst bug this project has produced, and the one it exists to
    prevent: a row reported ready with nobody signed into the app.

    ChatGPT has a logged-out mode - its welcome screen offers "Continue without
    logging in" - and that mode has the same composer, the same text box, the
    same controls. A phone opened straight into it. No registry entry matched,
    so nothing was logged and nothing was archived; the next pass through the
    loop read those same elements, saw a composer, and called it success. It
    was found by someone opening the phone by hand.
    """
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-signed-in.xml")

    # Exactly the screen a successful login ends on...
    assert chatgpt_login.composer_on_screen(ctx)
    # ...and not evidence, until this run has put the password in.
    assert not ctx.submitted_password
    assert not chatgpt_login.verified_on_device(ctx)

    ctx.submitted_password = True
    assert chatgpt_login.verified_on_device(ctx)


def test_an_unproven_chat_screen_is_reset_rather_than_believed():
    """From outside, "logged out" and "signed in by an earlier run" look
    identical. Guessing costs a phone reported ready with nobody in it, so the
    flow stops guessing: pm clear brings the app back to its welcome screen and
    the ordinary path applies."""
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-signed-in.xml")
    matched = app_screen(ctx)

    assert matched.name == "logged_out_chat"
    assert matched.act is chatgpt_login.act_reset_app
    # Bounded, or a screen that keeps coming back becomes a loop of wipes.
    assert matched.max_visits <= 2


def test_the_reset_never_runs_once_the_password_is_in():
    """The counterweight. Clearing the app after a real login would throw the
    session away and then report failure for the screen it had just earned."""
    ctx = app_context("chatgpt-signed-in.xml")
    ctx.submitted_password = True

    assert app_screen(ctx) is None or app_screen(ctx).name != "logged_out_chat"


# --------------------------- OpenAI's edge refusing the request (2026-08-08)
def _email_page(submissions: int):
    """The email page with the address already in the box, after `submissions`
    attempts - which is what a refused submission leaves behind."""
    ctx = app_context("chatgpt-request-problem.xml")
    ctx.creds = ACCOUNT
    ctx.email_submissions = submissions
    return ctx


def test_the_edge_refusal_is_only_named_when_it_was_seen():
    """Telling someone to change their exit IP is worth being sure about. For
    two runs this was inferred from the address still being in the box, and
    every archived screen was a clean email form with no error on it - three
    rows sent round that loop on the strength of nothing (2026-08-10).

    Now the page is glanced at while the toast is still up, and only what was
    actually read decides the reason.
    """
    from geelark_farm import screen
    from geelark_farm.flows import chatgpt_login

    ctx = _email_page(submissions=chatgpt_login.MAX_EMAIL_SUBMISSIONS)
    assert screen.find_input(ctx.elements, password=False).text == ACCOUNT.email
    assert not ctx.has("there is a problem"), "the message has already faded"

    # Never seen: the reason says so rather than blaming the network.
    outcome = chatgpt_login.act_email(ctx)
    assert outcome is not None and outcome.reason == "email_not_accepted"
    assert "NOT known to be an exit-IP problem" in outcome.detail

    # Seen at the time: the reason names it, and says what to do.
    ctx = _email_page(submissions=chatgpt_login.MAX_EMAIL_SUBMISSIONS)
    ctx.saw_edge_refusal = True
    outcome = chatgpt_login.act_email(ctx)
    assert outcome is not None and outcome.reason == "request_rejected"
    assert "CHANGE THE EXIT IP" in outcome.detail


def test_one_resubmission_is_allowed_before_giving_up():
    """A genuine one-off should not cost a row, and two attempts is not a
    pattern anything would penalise."""
    from geelark_farm.flows import chatgpt_login

    ctx = _email_page(submissions=1)
    assert chatgpt_login.MAX_EMAIL_SUBMISSIONS == 2

    # Still under the limit, so it resubmits rather than reporting - which
    # needs a device, so only the decision is checked here.
    assert ctx.email_submissions < chatgpt_login.MAX_EMAIL_SUBMISSIONS


def test_the_reason_says_what_to_do_about_it():
    """The point of the reason is that the sheet tells you the next action
    without anyone having to read a log."""
    from geelark_farm.flows import chatgpt_login

    advice = chatgpt_login.FATAL_ADVICE["request_rejected"]
    assert "CHANGE THE EXIT IP" in advice
    # And the trap that would waste the change: a phone keeps the proxy it was
    # created with, so a new proxy needs a new phone.
    assert "delete this phone" in advice


# ------------------------------ a fatal that was one option among several
def test_the_sms_row_is_not_fatal_beside_the_authenticator():
    """Google's 2-Step list puts "Get a verification code at •••••34" - which
    this tool cannot receive - directly beside "Get a verification code from
    the Google Authenticator app", which it can.

    Fatal is checked before every other entry, so matching the SMS row there
    ended a login that was one tap from working (2026-08-08, row 7).
    """
    ctx = context_from("google-2fa-list-with-sms.xml")

    assert ctx.has("get a verification code at"), "the SMS row is on the page"
    assert login.authenticator_offered(ctx), "and so is the one that works"

    assert login._fatal_reason(ctx) is None
    assert matched_screen(ctx).name == "2fa_authenticator_offered"


def test_the_sms_demand_is_still_fatal_on_its_own():
    """The counterweight. When SMS really is the only way offered, nothing here
    can receive it and the row has to say so."""
    ctx = login.Context(client=None, phone_id="P", account=ACCOUNT)
    ctx.elements = []
    ctx.blob = screen.normalize("Get a verification code at (•••) •••-••34").casefold()

    assert not login.authenticator_offered(ctx)
    assert login._fatal_reason(ctx) == "phone_verification_required"


def test_googles_checking_interstitial_is_waited_for():
    """"Checking info..." with nothing else on the page, and no progress bar to
    catch it by. A row reported unknown_screen from this, having been read a
    second too early (2026-08-08, row 1)."""
    ctx = context_from("google-checking-info.xml")

    assert login.is_loading(ctx)
    assert matched_screen(ctx).name == "loading"


def test_the_app_is_confirmed_in_front_before_the_flow_drives_it():
    """Two rows installed the app, launched it, and drove against the Play
    Store's own page - reading "Uninstall" and "Open", matching nothing, and
    reporting app_unknown_screen about a screen that was never this app's
    (2026-08-08, rows 7 and 8). The launch never checked that anything came up.

    Asked of the device, not the screen: an unrecognised page looks identical
    whether the app is showing something new or was never started.
    """
    from geelark_farm import screen as screen_mod
    from geelark_farm.flows import chatgpt_login

    els = screen_mod.parse(
        (FIXTURES / "play-page-after-install.xml").read_text(encoding="utf-8"))
    ctx = chatgpt_login.Context(client=None, phone_id="P", creds=ACCOUNT)
    ctx.elements = els
    ctx.blob = screen_mod.texts(els)

    # Nothing here belongs to the app, and nothing in the registry claims it -
    # which is correct, and is why the check has to happen before the loop.
    assert not chatgpt_login.composer_on_screen(ctx)
    assert app_screen(ctx) is None
    assert chatgpt_login.LAUNCH_ATTEMPTS >= 2


def test_the_foreground_check_never_blocks_on_its_own_failure():
    """A diagnostic that is unavailable must not stop a launch that would have
    worked - so an unreadable answer means carry on."""
    from geelark_farm import shell

    original = shell.read
    shell.read = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no shell"))
    try:
        assert shell.foreground_package(object(), "P1") == ""
    finally:
        shell.read = original


# ------------------------------------------------ what 2026-08-09 taught
def test_chatgpt_names_a_rejected_password_instead_of_retyping_it():
    """The page says "Incorrect email address or password" - "email address",
    not "email" - so none of the needles matched, and the flow retyped the same
    password until its visits ran out: seven and a half minutes to report
    stuck_on_password_entry about a password the service had already refused
    (2026-08-09, row 5)."""
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-wrong-password.xml")

    assert ctx.has("incorrect email address or password")
    assert chatgpt_login._fatal_reason(ctx) == "wrong_password"
    assert app_screen(ctx).name == "fatal"


def test_back_out_of_the_sign_in_is_noticed_and_undone():
    """"Something went wrong. Please go back and try again." is answered with
    BACK, and once that closed the sign-in outright: the phone was left on its
    launcher and the row reported unknown_screen with a screenful of app icons
    in its archive (2026-08-09, row 1).

    The launcher is not in the registry and should not be - what identifies
    this is the device saying which app is in front.
    """
    ctx = context_from("android-home-screen.xml")

    assert matched_screen(ctx) is None, "nothing here belongs to a login"
    assert "com.android.settings" in login.SIGN_IN_PACKAGES
    assert "com.google.android.gms" in login.SIGN_IN_PACKAGES


# ------------------------------ signed in, and not recognised (2026-08-10)
def test_voice_mode_is_still_the_chat_screen():
    """Both rows walked the whole path - welcome, email, password, code,
    onboarding - and were then reported as an unknown screen. One had landed in
    voice mode, where the composer is replaced by "Hold to speak to ChatGPT"
    and there is no text box at all.

    The check insisted on a box, which described one way the chat screen looks
    rather than what makes it the chat screen.
    """
    from geelark_farm import screen
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-voice-mode.xml")
    ctx.submitted_password = True

    assert screen.find_input(ctx.elements) is None, "voice mode has no box"
    assert chatgpt_login.composer_on_screen(ctx)
    assert chatgpt_login.verified_on_device(ctx)


def test_the_updated_terms_dialog_is_dismissed():
    """The other row met "We've updated our Terms of Use and Privacy Policy"
    with an Agree button - which was not in this flow's dismiss list, though
    the Google flow has accepted the equivalent since July."""
    from geelark_farm import screen
    from geelark_farm.flows import chatgpt_login

    ctx = app_context("chatgpt-updated-terms.xml")

    assert app_screen(ctx).name == "onboarding"
    assert screen.find_first(ctx.elements,
                             chatgpt_login.DISMISS_LABELS).label == "Agree"


def test_the_dismiss_list_still_refuses_nothing_by_accident():
    """It is matched without requiring clickable, so the list is the only guard
    on what gets pressed."""
    from geelark_farm.flows import chatgpt_login

    for label in chatgpt_login.DISMISS_LABELS:
        low = label.casefold()
        assert "don't" not in low and "deny" not in low and "disagree" not in low


def test_the_login_page_is_not_read_as_a_composer():
    """The counterweight to accepting controls on their own: the login page has
    a text box, and must never look like the chat screen."""
    from geelark_farm.flows import chatgpt_login

    for fixture in ("chatgpt-login-page.xml", "chatgpt-welcome.xml",
                    "chatgpt-request-problem.xml"):
        ctx = app_context(fixture)
        ctx.submitted_password = True
        assert not chatgpt_login.composer_on_screen(ctx), fixture


def test_a_rejected_authenticator_code_stops_rather_than_repeating():
    """Google answered "Wrong code. Try again." and the flow generated a fresh
    code and sent it again, four times - four wrong codes against a real
    account - before reporting stuck_on_2fa_code_entry, which names the screen
    it was standing on (2026-08-10, row 15).

    A fresh code from a wrong secret is wrong every time, so repeating cannot
    help and only spends attempts.
    """
    ctx = context_from("google-wrong-2fa-code.xml")

    assert ctx.has("wrong code")
    assert login._fatal_reason(ctx) == "wrong_2fa_code"
    assert matched_screen(ctx).name == "fatal"
    assert "totp_secret" in login.FATAL_ADVICE["wrong_2fa_code"]


def test_a_code_page_without_a_rejection_is_still_answered():
    """The counterweight: the ordinary code prompt must keep being typed into.
    """
    ctx = context_from("google-2fa-code-entry.xml")

    assert login._fatal_reason(ctx) is None
    assert matched_screen(ctx).name == "2fa_code_entry"


def test_a_code_prompt_on_an_account_without_2fa_is_named():
    """Accounts sold without 2FA normally never reach the code screen. When one
    does, Google is asking for something the row cannot produce - and before
    this the AccountError escaped into process_row's catch-all and arrived in
    the sheet as "error", which says nothing (2026-08-10)."""
    from geelark_farm.accounts import Account

    ctx = context_from("google-2fa-code-entry.xml")
    ctx.account = Account(email="a@example.com", password="p", totp_secret="")

    assert matched_screen(ctx).name == "2fa_code_entry"
    outcome = login.act_totp(ctx)
    assert outcome is not None
    assert outcome.reason == "no_authenticator"
    assert "no 2FA secret" in outcome.detail


def test_the_same_prompt_is_answered_when_there_is_a_secret():
    """The counterweight: nothing changes for the accounts that have one."""
    ctx = context_from("google-2fa-code-entry.xml")
    assert ctx.account.has_authenticator


# --------------------------------- what the loop costs while it watches
def test_the_device_is_not_asked_on_every_pass(monkeypatch):
    """It costs a request each time, out of a budget that is process-wide and
    bans the key for two hours when it runs out - and four builds at once ask
    four times (2026-08-23)."""
    from geelark_farm.flows import google_login

    asked = []
    monkeypatch.setattr(google_login.shell, "device_accounts",
                        lambda *a, **k: asked.append(1) or [])
    monkeypatch.setattr(google_login, "open_add_account", lambda *a, **k: None)

    driven = {}

    def fake_drive(ctx, screens, *, is_done, budget_seconds, logger=None):
        driven["is_done"] = is_done
        return google_login.Outcome("budget", "budget_exhausted")

    monkeypatch.setattr(google_login.router, "drive", fake_drive)
    google_login.sign_in(None, "P1", _account())

    before = len(asked)
    for _ in range(20):                       # twenty passes of the loop
        driven["is_done"]()

    # One, not twenty: the rest fall inside the same window.
    assert len(asked) - before == 1


def test_the_poll_does_not_end_a_login_over_one_refused_command(monkeypatch):
    """Not strict: an empty answer here means "not yet"."""
    from geelark_farm.flows import google_login

    seen = {}
    monkeypatch.setattr(google_login.shell, "device_accounts",
                        lambda *a, **k: seen.update(k) or [])
    monkeypatch.setattr(google_login, "open_add_account", lambda *a, **k: None)

    driven = {}

    def fake_drive(ctx, screens, *, is_done, budget_seconds, logger=None):
        driven["is_done"] = is_done
        return google_login.Outcome("budget", "budget_exhausted")

    monkeypatch.setattr(google_login.router, "drive", fake_drive)
    google_login.sign_in(None, "P1", _account())
    seen.clear()
    driven["is_done"]()

    assert seen.get("strict") is False


def _account():
    from geelark_farm.accounts import Account
    return Account(email="a@b.com", password="pw", totp_secret="")


# ============================== a label list is matched case-insensitively
def test_no_label_list_carries_a_spelling_it_can_never_reach():
    """`screen.find` casefolds both sides, so one spelling of a word is every
    spelling of it. Three entries in DISMISS_LABELS and one in `submit` were
    second looks for labels the first had already matched - and an example
    for whoever adds the next one (2026-08-23)."""
    import ast
    import pathlib

    offenders = []
    for path in pathlib.Path("src/geelark_farm").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.Tuple, ast.List)):
                continue
            values = [e.value for e in node.elts
                      if isinstance(e, ast.Constant) and isinstance(e.value, str)
                      and e.value.strip()]
            folded = [v.casefold() for v in values]
            if len(folded) != len(set(folded)):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"unreachable spellings in {offenders}"
