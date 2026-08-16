"""What every failure means, and who is to blame for it - in one table.

A flow reports what it saw: `captcha_shown`, `network_ssl_rejected`,
`app_would_not_start`. Deciding what that costs is a different question, and
for most of this project's life it was answered in four frozensets across three
modules. Every behavioural bug worth the name came from getting one of them
wrong, and each was invisible until a batch had already spent the stock:

- a CAPTCHA was filed as a verdict on the exit, so a build spent a proxy on it
  and then retried the very Gmail that caused it (2026-08-11);
- an OpenAI edge refusal was filed as the account's fault once the exit budget
  ran out, condemning an account the service had never examined;
- a phone that could not start the app worked through the whole account pool
  against the same wall;
- `email_code_required` appeared mid-run from a flow that had grown it, and was
  classified by whatever the default happened to be.

So the classification lives here, once, and says three things about each
reason: whose fault it is, what a build should do about it, and what to tell
whoever reads the sheet afterwards.

The point is not tidiness. It is that `test_failures.py` asserts every reason
the flows can emit appears below, so a reason added to a flow cannot reach a
build without someone deciding what it means.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# ---------------------------------------------------------------- who erred
#: The credential that was offered. The service looked at it and said no, so
#: the next one deserves a turn on the same phone.
CREDENTIAL = "credential"
#: Where the request came from. Decided before any account was examined, so the
#: credential is untouched and what has to change is the exit address.
EXIT = "exit"
#: The phone or the app on it. Nothing was decided about the credential, and
#: the next one would meet the same wall.
DEVICE = "device"
#: The service asked for something no unattended run can supply - a code in an
#: inbox, a box only a human can tick. It judged nothing: the credential may be
#: perfectly good, and often is. Set aside for this build, kept for the next.
#:
#: This exists because the alternative was wrong in a way that cost stock.
#: `email_code_required` was filed as the credential's fault and retired the
#: account for good - and three accounts that had already signed in
#: successfully on earlier phones were later retired by it, having done nothing
#: wrong (2026-08-13). The challenge follows the device and the exit, not the
#: account.
CHALLENGED = "challenged"


@dataclass(frozen=True)
class Verdict:
    """What one reason means for the build that hit it."""

    blame: str
    #: What happened, in the words someone would use out loud - "Google showed
    #: a CAPTCHA", not "captcha_shown". A lowercase clause with no full stop,
    #: because it gets dropped into a larger sentence: `a@b.com (Google showed
    #: a CAPTCHA)`.
    #:
    #: This exists so no note has to name a reason token. The Status column is
    #: where the token belongs - it is what you filter and sort on - and the
    #: Note beside it is prose for whoever is reading the row.
    seen: str
    #: What an operator should do, in the sheet or in the panel. Written for
    #: someone reading the tab a day later with no memory of the run.
    advice: str

    @property
    def costs_the_credential(self) -> bool:
        """Whether the credential is spent - marked, and the next one tried."""
        return self.blame == CREDENTIAL

    @property
    def needs_a_new_exit(self) -> bool:
        """Whether the answer is a different exit address, same credential."""
        return self.blame == EXIT

    @property
    def sets_aside(self) -> bool:
        """Whether to keep the credential and try the next one.

        Not the same as costs_the_credential: nothing was decided here, so the
        row keeps its place in the pool.
        """
        return self.blame == CHALLENGED

    @property
    def stops_the_phone(self) -> bool:
        """Whether to give up on this phone rather than feed it more stock."""
        return self.blame == DEVICE


# The table. Reasons are grouped by which flow reports them, because that is
# how you find one when a run surprises you.
VERDICTS: dict[str, Verdict] = {
    # -------------------------------------------------- google_login.py
    "captcha_shown": Verdict(
        CREDENTIAL, "Google showed a CAPTCHA",
        "Google challenged this address. It follows the account, not the IP - "
        "the same exit signs the next one in. Blank the status to try it again "
        "later, ideally on a residential exit."),
    "wrong_password": Verdict(
        CREDENTIAL, "Google would not take the password",
        "The password in the sheet is not the account's. Correct "
        "it and blank the status."),
    "password_changed": Verdict(
        CREDENTIAL, "Google said the password was an old one",
        "Google accepted the address and called the password old. "
        "The account has moved on without us."),
    "verification_blocked": Verdict(
        CREDENTIAL, "Google asked for a verification the run cannot answer",
        "Google wants a verification this cannot answer."),
    "account_disabled": Verdict(
        CREDENTIAL, "Google has disabled the account",
        "Google has disabled the account. Nothing to retry."),
    "sign_in_refused": Verdict(
        CREDENTIAL, "Google refused the sign-in outright",
        "Google refused the sign-in outright."),
    "phone_verification_required": Verdict(
        CREDENTIAL, "Google asked for a phone number",
        "Google wants a phone number. Usually a young account on "
        "an exit it distrusts; a better exit sometimes clears it."),
    "email_not_found": Verdict(
        CREDENTIAL, "Google did not recognise the address",
        "Google does not know this address. Check it for typos."),
    "wrong_2fa_code": Verdict(
        CREDENTIAL, "Google turned down the 2FA code",
        "The code was rejected. Usually the wrong 2FA secret in "
        "the sheet - check the column."),
    "no_authenticator": Verdict(
        CREDENTIAL, "Google asked for a 2FA code and the row has no secret",
        "Google asked for a code and the row has no 2FA secret. "
        "Add it, or the account cannot be used unattended."),
    "no_authenticator_option": Verdict(
        CREDENTIAL, "Google offered no authenticator to use",
        "Google offered no authenticator choice on this account."),

    # ------------------------------------------------- chatgpt_login.py
    "email_code_required": Verdict(
        CHALLENGED,
        "OpenAI emailed a one-time code instead of taking the authenticator",
        "OpenAI emailed a one-time code instead of accepting the "
        "authenticator, which no unattended run can read. It says nothing "
        "about the account - the same addresses have signed in fine on other "
        "phones - so it is set aside rather than marked bad. It waits for you: "
        "give the account an authenticator OpenAI accepts, then blank its "
        "Status to offer it again. Nothing retries it on its own, because "
        "that is what had every run spending five minutes on the same two "
        "accounts."),
    "account_deactivated": Verdict(
        CREDENTIAL, "OpenAI has deactivated the account",
        "OpenAI has deactivated the account."),
    "email_not_accepted": Verdict(
        CREDENTIAL, "the login page would not move past the address",
        "The address was not accepted and no refusal was shown. "
        "Check it against the account it belongs to."),
    "network_ssl_rejected": Verdict(
        EXIT, "the secure connection was refused before the account was sent",
        "The TLS handshake was refused before any account was sent. Measured "
        "per-session rather than per-proxy, so the same proxy often works "
        "again - the build takes a new exit and keeps the account."),
    "request_rejected": Verdict(
        EXIT, "Cloudflare turned the request away at the edge",
        "Cloudflare refused at the edge, with a Ray ID, before the account was "
        "examined. A different exit address is the whole answer."),

    # --------------------------------------------------- play_install.py
    # None of these is a credential's fault: the install happens between the
    # Google sign-in and the app account, and touches neither.
    "play_page_never_loaded": Verdict(
        DEVICE, "the app's Play Store page never loaded",
        "The Play page for the package never appeared. Check the "
        "package id, and that this phone's exit can reach Play at all."),
    "no_install_button": Verdict(
        DEVICE, "the Play Store page had nothing to press",
        "The Play page loaded with nothing to press. Often means the "
        "app is already there in a broken state, or the page is regional."),
    "download_stalled": Verdict(
        DEVICE, "the download stopped making progress",
        "The download stopped making progress and restarting it did "
        "not help. Usually the exit address; the phone is otherwise fine."),
    "play_server_error": Verdict(
        DEVICE, "the Play Store kept returning an error of its own",
        "Play itself returned an error and kept returning it. Leave "
        "the phone and retry later - it is not about the account."),
    "play_not_signed_in": Verdict(
        DEVICE, "the Play Store said no Google account was signed in",
        "Play says to sign in, so the Google account did not reach it "
        "even though the device reported one. The phone is the problem, not "
        "the address - rebuild it."),
    "play_needs_payment": Verdict(
        DEVICE, "the Play Store wanted a payment method first",
        "Play wants a payment method before it will install anything. "
        "Nothing this tool does resolves that; the Google account needs one "
        "added by hand, or use a different one."),
    "app_unavailable": Verdict(
        DEVICE, "the Play Store will not offer the app to this account",
        "Play will not offer the app to this account or country - "
        "'not available in your country', 'item not found'. Usually the exit "
        "address's region; a different proxy region is what changes it."),

    # -------------------------------- the phone, or the app on it, is stuck
    "no_login_button": Verdict(
        DEVICE, "the app showed no way to log in",
        "The app showed no way to log in. Look at the archived screen "
        "under artifacts/ - the app's layout may have moved."),
    "google_sheet_stuck": Verdict(
        DEVICE, "Google's account chooser would not close",
        "Google's account chooser would not close. The phone needs "
        "looking at."),
    "app_would_not_start": Verdict(
        DEVICE, "the app never came to the foreground",
        "The app never came to the foreground. Usually the install is "
        "damaged; delete the phone rather than spending accounts on it."),
    "app_not_installed": Verdict(
        DEVICE, "the app was not on the phone",
        "The package is not on the device, so there was nothing to "
        "sign into."),
    "rate_limited": Verdict(
        DEVICE, "OpenAI stopped answering this phone",
        "OpenAI is refusing to keep talking to this device. Leave it "
        "and come back later; another account now meets the same limit."),
    "too_many_attempts": Verdict(
        DEVICE, "Google stopped answering this phone",
        "Google is refusing to keep talking to this device. Leave it "
        "and come back later."),
    "unknown_screen": Verdict(
        DEVICE, "the app showed a page the run did not recognise",
        "The router did not recognise the page. Its XML is under "
        "artifacts/ - that capture is what a new registry entry is written "
        "from."),
    "unknown_fatal": Verdict(
        DEVICE, "the app refused without saying why",
        "A refusal was detected but not identified. The archived "
        "screen says which."),
    "phone_is_gone": Verdict(
        DEVICE, "the phone was deleted while the build was working on it",
        "GeeLark answered `env not found`, so the phone this build was driving "
        "no longer exists. Something else removed it mid-run - a second "
        "process, or a hand in the panel. Nothing is wrong with the "
        "credentials; they go back to the pool untouched."),
    "phone_would_not_start": Verdict(
        DEVICE, "GeeLark never brought the phone up",
        "GeeLark kept reporting the phone as starting and it never came up. "
        "Nothing about the credentials; the device is the problem. It is "
        "deleted rather than kept, since nothing was signed into it."),
    "budget_exhausted": Verdict(
        DEVICE, "the step ran out of time",
        "The step ran out of time. Raise its budget, or find out what "
        "the phone was waiting for."),
}

#: Why a build stopped, for the reasons the builder raises itself rather than
#: reads off a screen. These never reach `VERDICTS` - there is no credential to
#: blame and nothing to mark - but they do reach the Phones tab, and a row
#: reading `all_exits_refused` tells a person less than a sentence does.
#:
#: Written to complete "the build stopped because ...", so they are lowercase
#: clauses with no full stop, like `Verdict.seen`.
SITUATIONS: dict[str, str] = {
    "all_exits_refused": "every exit in the pool was refused in turn",
    "no_usable_proxy": "the Proxy tab had no free proxy to give it",
    "no_working_proxy": "none of the free proxies answered when tested",
    "proxy_change_refused": "GeeLark would not move the phone to another exit",
    "interrupted": "the run was stopped by hand",
}

#: Reasons a flow may return that mean success, so nothing needs a verdict.
SUCCESSES = frozenset({"signed_in", "already_signed_in", "installed",
                       "already_installed", "logged_in"})


def reasons_reported_by(module) -> set[str]:
    """Every reason one flow module can hand back.

    Two places to look, because a flow names reasons in two ways: literally, in
    an `Outcome(...)` call, and as the keys of its own fatal-reason tables,
    which reach `Outcome` through a variable. Reading only the first missed
    `captcha_shown` - a reason written to the sheet thirty times.

    Derived rather than listed, so that what the sheet offers and what the code
    can write cannot drift apart. They had: the Gmail dropdown offered three
    statuses no build writes and omitted two it does (2026-08-12).
    """
    import ast
    import inspect

    found: set[str] = set()
    source = inspect.getsource(module)
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Outcome"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)):
            found.add(node.args[1].value)
    for attribute in dir(module):
        value = getattr(module, attribute)
        if isinstance(value, dict) and attribute.isupper():
            found.update(k for k in value if isinstance(k, str))
    return found - SUCCESSES


def verdict(reason: str) -> Verdict:
    """What `reason` means.

    An unlisted reason is treated as the device's problem, which is the safe
    default in the only way that matters: it stops the phone instead of feeding
    the pool into something nobody has classified. The test suite exists so
    this never happens in practice.
    """
    return VERDICTS.get(reason, Verdict(
        DEVICE, f"something happened that this tool has no name for ({reason})",
        f"{reason} has no entry in failures.py, so nothing is known "
        f"about it. Add one before trusting what a build did here."))


def situation(reason: str) -> str:
    """Why a build stopped, as a clause that finishes "the build stopped ...".

    Falls back to the token, because a note that reads oddly is better than one
    that leaves out the only word that would let someone search the logs for
    what happened.
    """
    return SITUATIONS.get(reason, f"it stopped with {reason}")


def today() -> str:
    """The date the way a note should say it: `13 Aug 2026`.

    Notes used to carry ISO dates because that is what `strftime('%Y-%m-%d')`
    gives you for free. Nobody writes to another person in ISO, and these
    columns are read by a person.

    The leading zero is stripped rather than asked for. Dropping it is a
    strftime flag and the flag is not the same one everywhere - `%-d` on BSD
    and glibc, `%#d` on Windows, and each raises ValueError on the other. This
    was the only line in the package that branched on the platform, which is a
    poor thing to discover from a traceback inside a sheet write on someone
    else's machine.
    """
    return time.strftime("%d %b %Y").lstrip("0")
