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
#: Nobody. The build stopped without anything being judged: the pool ran dry,
#: an exit could not be swapped, a hand stopped the run. Nothing is marked and
#: nothing is spent - the only thing to record is what ran out, and what to do
#: so it does not run out again.
#:
#: These live in the same table as the rest because they reach the same reader.
#: They used to sit in a second one holding only a clause, so a row reading
#: `no_usable_proxy` got a sentence where a row reading `wrong_password` got a
#: sentence and advice - and the console had to know which table a status came
#: from before it could say anything about it (2026-08-17).
NOBODY = "nobody"


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
    "session_unverified": Verdict(
        DEVICE, "the signed-in session could not be read back from the app",
        "The login looked complete but the walk to the app's own settings - "
        "where the address is read back - never got there. The phone is not "
        "handed over on looks alone: that is how one was delivered with "
        "nobody in the app. The archived screen shows where the walk "
        "stopped; if OpenAI moved its menus, the walk in the ChatGPT login "
        "flow is what to update."),
    "app_wrong_account": Verdict(
        DEVICE, "the app's own settings name a different account",
        "The app is signed in, but as someone else - a session from an "
        "earlier run survived where a fresh login was expected. Nothing is "
        "wrong with the credentials. Rebuild the phone, or clear the app "
        "and finish it again."),
    "email_code_never_arrived": Verdict(
        CHALLENGED,
        "OpenAI emailed a one-time code and none arrived in the time allowed",
        "OpenAI emailed a code rather than asking the authenticator, and the "
        "mailbox produced nothing within the wait. Nothing was judged about "
        "the account - it is set aside rather than marked, and keeps its "
        "place in the pool. Check the mailbox is reachable and the message is "
        "not held up, then blank the status to try it again; the phone is "
        "reused, so a retry costs nothing but the attempt."),
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

    # ------------------------------------------------------------- NOBODY
    # The builder raises these itself rather than reading them off a screen.
    # Nothing was judged and nothing is marked, but they reach the same reader
    # as everything above - and until they were written like everything above,
    # that reader got a sentence where every other row got a sentence and
    # something to do about it.
    "all_exits_refused": Verdict(
        NOBODY, "every exit in the pool was refused in turn",
        "Every free proxy was tried and each one was refused before the "
        "account was ever sent. That is a run out of usable exits, not a bad "
        "account - the credentials went back untouched. Change the addresses "
        "of the rows marked `change ip` in the Proxy tab, or add proxies."),
    "no_exit_to_move_to": Verdict(
        NOBODY,
        "an exit refused it and the Proxy tab had no free one to move to",
        "An exit refused this build and there was nothing free to move it to "
        "- a run given as many phones as it has proxies keeps none spare. "
        "Either build fewer at a time than there are proxies, or add some."),
    "no_usable_proxy": Verdict(
        NOBODY, "the Proxy tab had no free proxy to give it",
        "No phone was created, so nothing was spent. The Proxy tab has "
        "nothing free: rows are `claimed` or `on a phone` from builds that "
        "still hold them, or `change ip` and `dead` and waiting on you."),
    "no_working_proxy": Verdict(
        NOBODY, "none of the free proxies answered when tested",
        "Every free proxy was tested and none answered, so no phone was "
        "created. These are rented monthly and often answer again the next "
        "day; the rows are marked `dead` and retested on the next run."),
    "proxy_change_refused": Verdict(
        NOBODY, "GeeLark would not move the phone to another exit",
        "GeeLark refused the swap, so the phone kept the exit it had and the "
        "build stopped rather than go on through an address that had already "
        "refused it. Nothing is wrong with the credentials."),
    "network_unreachable": Verdict(
        NOBODY, "this machine lost its connection",
        "The network went away mid-build - GeeLark, Google Sheets and Google's "
        "own token endpoint all stopped resolving at once, which is this "
        "machine and not any of them. Nothing was judged and nothing was "
        "spent. Check the connection, run `geelark verify`, and if the run "
        "died holding rows they are freed with `geelark pools "
        "--release-stuck`."),
    "interrupted": Verdict(
        NOBODY, "the run was stopped by hand",
        "You stopped the run. The phone was stopped and every row it held "
        "went back to its pool, so nothing was lost - the phone is in the tab "
        "and can be finished."),
}

#: The reasons nothing is to blame for. Derived, so it cannot disagree with the
#: table: it was a second dictionary for most of this project's life and the
#: two drifted apart in shape, one carrying advice and the other not.
SITUATIONS: dict[str, str] = {reason: found.seen
                              for reason, found in VERDICTS.items()
                              if found.blame == NOBODY}

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
    # The router reports one reason per screen, built from the screen's name -
    # `Outcome("unknown", f"stuck_on_{matched.name}")` - so it is a JoinedStr
    # and never a literal to be found below. Enumerated from the registry
    # instead, which is the only place those names exist.
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Screen" and node.args
                and isinstance(node.args[0], ast.Constant)):
            found.add(f"{STUCK_PREFIX}{node.args[0].value}")
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


#: The router's own reason, one per screen: `stuck_on_totp_entry`. Built from
#: the screen's name, so it is never a literal anywhere - which is how
#: twenty-one of them reached a build without a verdict between them, and why
#: phone 778 was told "something happened that this tool has no name for"
#: about the most ordinary thing a flow does (2026-08-16).
#:
#: One rule rather than twenty-one entries, because they all mean the same
#: thing: the page kept coming back and the action was not moving it. That is
#: the device's problem whichever page it was, and the page belongs in the
#: words rather than in the table.
STUCK_PREFIX = "stuck_on_"


def _stuck(reason: str) -> Verdict:
    page = reason[len(STUCK_PREFIX):].replace("_", " ")
    return Verdict(
        DEVICE, f"the app kept returning to the {page} page",
        f"The flow handled the {page} page over and over and it never moved "
        f"on, so whatever the action does there is not having the effect it "
        f"assumes. The capture is under artifacts/ as `stuck-"
        f"{reason[len(STUCK_PREFIX):]}` - that page is what a fix is written "
        f"from.")


def knows(reason: str) -> bool:
    """Whether the taxonomy has a verdict for this, rather than a fallback.

    The table is no longer the only source of one - the `stuck_on_` family is
    answered by a rule - so asking `reason in VERDICTS` is asking the wrong
    question, and it is the question the test asked while twenty-one reasons
    went unclassified.
    """
    return (reason in VERDICTS
            or (reason.startswith(STUCK_PREFIX)
                and len(reason) > len(STUCK_PREFIX)))


def verdict(reason: str) -> Verdict:
    """What `reason` means.

    An unlisted reason is treated as the device's problem, which is the safe
    default in the only way that matters: it stops the phone instead of feeding
    the pool into something nobody has classified. The test suite exists so
    this never happens in practice.
    """
    if reason.startswith(STUCK_PREFIX) and len(reason) > len(STUCK_PREFIX):
        return _stuck(reason)
    return VERDICTS.get(reason, Verdict(
        DEVICE, f"something happened that this tool has no name for ({reason})",
        f"{reason} has no entry in failures.py, so nothing is known "
        f"about it. Add one before trusting what a build did here."))


def situation(reason: str) -> str:
    """Why a build stopped, as a clause that finishes "the build stopped ...".

    One lookup now, into the same table as everything else. Falls back to the
    token, because a note that reads oddly is better than one that leaves out
    the only word that would let someone search the logs for what happened.
    """
    if not knows(reason):
        return f"it stopped with {reason}"
    return verdict(reason).seen


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
