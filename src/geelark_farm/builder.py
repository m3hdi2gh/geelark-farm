"""Build a phone out of the resource pools.

    take a proxy  ──►  create the phone behind it  ──►  boot
      ──►  first usable Gmail  ──►  sign in
             │ the Gmail was bad ──► take the next one, same phone,
             │                       until the pool or the budget runs out
      ──►  install the app
      ──►  first usable app account  ──►  sign in
             │ the account was bad  ──► take the next one, same phone,
             │                          until the pool or the budget runs out
             │ refused at the edge  ──► another proxy, same account,
             │                          same "until"
      ──►  record the phone  ──►  stop it

Nothing in that loop stops at a fixed number of tries. A phone gives up only
when the tab has nothing left to hand it, when the budget will not cover
another attempt, or when the failure says the phone itself is the problem
rather than the credential (see failures.py).

What a failure costs is the difference from the row flow this replaced
(deleted 2026-08-12). There a row named its proxy, its Gmail and its app
account in advance, so a bad Gmail failed the row and wasted the phone that had
been created for it. Here the phone is the thing being built and the
credentials are stock: a bad one is marked in its own
tab and the next is tried on the same device, which is already booted and
already signed in as far as it got.

Three rules decide which branch a failure takes, and they are the only
judgement in this module:

**Only the network refusals are about the exit address.** OpenAI's TLS refusal
and its Cloudflare "problem with your request" are made before any account is
examined, so they say nothing about the credential. Everything else - including
a CAPTCHA - follows the account: Google raises one on an address whose history
it distrusts, while the same exit signs the next account in without a murmur.
So a CAPTCHA costs that Gmail and the next one is tried, on the same phone.

**A new exit means another proxy.** Which is possible at all because
`/phone/detail/update` can repoint a phone that already exists
(`phones.set_proxy`) - it was assumed for most of this project's life that a
proxy was fixed at creation, and everything built on that assumption was
wrong. There was a cheaper way before it: the vendor's `port` product can be
given a new address while keeping its host and credentials, so nothing on the
phone changes. This account holds none of those, so that branch never ran and
is gone (2026-08-25). When no new exit can be had at all, the build stops and
says so; the account it was carrying goes back to the pool untouched, because
a network that would not carry the request never judged it.

**A proxy is not condemned for one refusal.** It was measured across twelve
attempts: every gateway produced both successes and rejections (2026-08-09). So
a proxy left behind goes back to the pool as `unused` with a note. Only a proxy
GeeLark cannot reach at all is marked `dead`.
"""

from __future__ import annotations

import dataclasses
import itertools
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from . import artifacts as archive
from . import breaker, codes, failures, phones, shell
from . import proxy as proxy_mod
from .accounts import Account
from .api import ApiError, Client, TransportError
from .config import Settings
from .flows import chatgpt_login, google_login, play_install
from .gsheet import SheetError
from .ledger import Ledger
from .logs import NO_BUILD
from .pools import Book, Pool, Resource

log = logging.getLogger(__name__)

#: How often the pool is looked at while it works.
#:
#: Only so the main thread comes back to a bytecode boundary often enough to
#: receive a signal. A bare `wait(futures)` blocks until every worker is done,
#: which meant `docker stop` was not acted on until the whole batch had
#: finished - longer than `stop_grace_period`, so SIGKILL arrived first and the
#: phones stayed up billing (2026-08-29).
STOP_POLL_SECONDS = 1.0

#: The batch a thread's work belongs to, and the job within that batch.
#:
#: `default=` is not optional. `ContextVar.get()` with no default raises
#: LookupError, and a filter runs OUTSIDE the try that guards `emit` -
#: `Handler.handle` calls it directly, and neither `callHandlers` nor
#: `Logger._log` catches - so a filter that raises comes back out of the
#: `log.info(...)` call and kills the build on its own log line.
#:
#: ContextVars rather than the `threading.local` that was here: a pool thread
#: is reused, and a local left set leaks into the next job on it. A token and
#: a `reset` in a `finally` cannot.
_run: ContextVar[str] = ContextVar("geelark_run", default=NO_BUILD)
_build: ContextVar[int | str] = ContextVar("geelark_build", default=NO_BUILD)


#: Where build events go, when anywhere. Injected by `serve` when the store
#: is enabled, never imported: the sheet retirement's trunk rule is that no
#: module imports `store` unconditionally, and an injection point keeps this
#: file ignorant of whether a store even exists. The sink must not raise -
#: store.events.emit already cannot - but the call is guarded anyway,
#: because "the monitoring took the build down" must be impossible from
#: both sides.
_event_sink = None


def set_event_sink(sink) -> None:
    global _event_sink
    _event_sink = sink


_RUN_IDS = itertools.count(1)


def _next_run_id() -> str:
    """One id per batch.

    `_run_jobs` is the boundary because it is exactly one batch: under `serve`
    with a pool, one pass submits one `work`, which makes one `builder.run`,
    which makes one `_run_jobs` - so a run id is also a pass's id. Short,
    because it is on every line of the file and of the console.
    """
    return f"r{next(_RUN_IDS)}"


class BuildContextFilter(logging.Filter):
    """Stamp every log record with the run and the build it came from.

    `row` stays exactly what it has always been - the bare int job index, or
    NO_BUILD - because `ui.ReporterLogHandler.emit` and `ui.print_new_notices`
    both gate on `isinstance(row, int)` and the live table's rows are keyed by
    that int. A composite id there does not raise; it silently freezes the
    step column on "starting" for a whole batch.

    Nothing here may raise, for the reason given above the ContextVars.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.build = _build.get()
        record.run = _run.get()
        record.row = record.build
        return True


# A credential the service judged and rejected costs that credential and nothing
# else: the build takes the next one and tries it on the phone it already has,
# for as long as the pool and the budget allow. There is deliberately no cap on
# how many it may work through - a cap would stop a phone with usable stock
# still sitting in the tab, which is what a run of three bad passwords did
# (2026-08-11, phones 654 and 656: three refused, eleven accounts still free).
#
# What bounds it is real: the pool empties (no_usable_gpt / no_usable_gmail) or
# the budget will not cover another attempt (budget_exhausted). Both name what
# actually ran out.
#
# The cost of this is worth stating: a tab full of bad credentials will be worked
# through by one phone until the budget ends. That is the intended trade - every
# one of them is recorded with the reason it failed, so a bad batch surfaces
# itself in a single run instead of three at a time.

# Whose fault a failure is, and therefore what it costs, is decided in
# failures.py - one table, with a test that no flow can report something it
# does not classify. It used to be two frozensets here and two more in the
# module that has since been deleted, and every behavioural bug of the last
# week was one of them being wrong.
#
#   the credential's   mark it, try the next one on this phone
#   the exit's         keep the credential, get a different exit address
#   the device's       stop; the next credential meets the same wall

# What a build needs left to be worth starting another attempt: a stop, a boot
# and a login. Below this the honest thing is to report what it has.
ATTEMPT_SECONDS = 420

# What the Phones tab records. The build knows exactly why it stopped and says
# so in the note; the Status column answers the only question asked of it at a
# glance - can I use this phone.
READY = "ready"
#: A phone with Google signed in and the app installed, and no app account.
#:
#: Named for what it has, not for what it lacks. `incomplete` named the
#: absence, and the absence is the whole product: this is the phone somebody
#: takes to sign a customer's own account into by hand. Read cold in a tab,
#: "incomplete" says "broken", and a reader who believes it either throws away
#: a finished thing or waits for it to finish something it never will
#: (2026-08-29).
#:
#: History rows written before this keep the old word. They are a record of
#: what was said at the time and are not rewritten; `unfinished()` reads the
#: columns rather than the status, so old rows go on working either way.
APP_ONLY = "app_only"

# What becomes of a resource a build was holding. It was a boolean - spent or
# not - and a challenged app account is neither: it was not used, and putting
# it back blank is what made every run pick the same one again.
SPEND = "spend"
RELEASE = "release"
SET_ASIDE = "set aside"

#: App-login reasons where the account was typed in and the phone took the
#: blame, but the account could as easily be the culprit: a session that
#: cannot be read back, or a password page that never moves. One Plus account
#: with a broken payment method drew its nag over the app's settings page and
#: failed this way on four phones in a row - and because the blame said
#: DEVICE, it went back to the pool blank after every one of them, was the
#: only free row, and got re-claimed until the breaker tripped (2026-08-31).
APP_SUSPECTS = frozenset({"session_unverified", "stuck_on_password_entry"})

#: How many *different* phones must end there with the same account before
#: the account is set aside. Different, because one phone burning its own
#: three tries proves nothing about the account - that is the 1465 lesson,
#: where a perfectly good password wore a condemnation a phone had earned.
SUSPECT_STRIKES = 3
_STRIKE = re.compile(r"\(strike (\d+) of \d+, last on phone ([^)]*)\)")

# Held across "claim an address, take an exit, create the phone". See build_one:
# it is what stops a phone being created with nothing to sign in, and what makes
# the serials come out in the order the addresses were taken.
_starting = threading.Lock()


@dataclass
class Build:
    """What one phone's construction produced, for the summary and the tab."""

    index: int
    ok: bool = False
    status: str = "not_started"
    phone_id: str = ""
    serial: str = ""
    proxy: str = ""
    #: What the Proxy tab calls that exit - `SX4`. The Phones tab records this
    #: rather than the address: it is the string you search the vendor's panel
    #: with, and the address is already one column away in the Proxy tab.
    proxy_name: str = ""
    gmail: str = ""
    #: Whether the target app is on the device. The row already said whether
    #: Google was signed in (the Gmail column) and whether the app account
    #: was (GPT Account); this was the one step of the three that nothing
    #: recorded, so `incomplete` covered "waiting on an app account" and "the
    #: app never installed" with the same word and no way to tell them apart
    #: (2026-08-21).
    #:
    #: Three states, not two, because "no app" and "never looked" are
    #: different answers and only one of them belongs on a row. `None` is a
    #: run that did not get far enough to find out: a `finish` that could not
    #: start the phone knows nothing about what is installed on it. As a bool
    #: that run said `False`, and `_record` wrote `incomplete` with a cross in
    #: the App column over a phone that had the app - phone 1415 was demoted
    #: from `app_only` to `incomplete` that way, by an attempt that never
    #: reached the device, and `app_only` is a product somebody sells
    #: (2026-08-30).
    #:
    #: A phone this run created is `False` rather than `None`: it is new, so
    #: nothing is installed on it, and that is knowledge.
    app_installed: bool | None = None
    app_account: str = ""
    detail: str = ""
    seconds: float = 0.0
    #: Where this build's archived pages went, for the prune to judge.
    artifact_dir: str = ""
    #: The screens each phase walked, as (phase, [screen, ...]). Written to
    #: History as one cell, which is the only account of a run that crosses
    #: machines: the log file is per-day and lives on whichever computer
    #: produced it, so nothing about a build on the Mac was readable from
    #: here at all (2026-08-23).
    #:
    #: Kept as parts rather than a formatted string for the reason `tried` is:
    #: what a terminal wants to show and what a sheet cell wants are not the
    #: same shape, and formatting early throws away the choice.
    trails: list[tuple[str, list[str]]] = field(default_factory=list)

    # True when this build's phone could not be confirmed stopped. The summary
    # must never claim nothing is billing while this is set.
    still_running: bool = False
    #: Whether this phone ended up on an exit another phone is also using. The
    #: pool ran dry and the build borrowed rather than stopping; the note says
    #: so, because two accounts arriving from one address is a thing to know.
    shared_exit: bool = False
    #: Every credential this build gave up on, as (address, reason, service).
    #: Kept as parts rather than a formatted string because the two readers
    #: want different words for it: the terminal summary wants the reason
    #: token, which is what you grep the logs for, and the sheet wants the
    #: sentence.
    #:
    #: The service is carried because this list holds both kinds - the Gmails
    #: the Google phase worked through and the app accounts the ChatGPT phase
    #: did - and three of the reasons can come from either. Without it every
    #: one of them was rendered as Google's doing, so an app account OpenAI
    #: refused was reported to the operator as a Google refusal (2026-08-20).
    tried: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def steps(self) -> str:
        """The path this build walked, as one cell.

        Runs of the same screen are collapsed to `name x3`. A screen handled
        three times without progress is the whole tell that something is
        looping, and printing it three times spends the width saying it
        three times.
        """
        parts = []
        for phase, screens in self.trails:
            if not screens:
                continue
            run: list[str] = []
            last, count = "", 0
            for name in [*screens, ""]:
                if name == last:
                    count += 1
                    continue
                if last:
                    run.append(f"{last} x{count}" if count > 1 else last)
                last, count = name, 1
            parts.append(f"{phase}: " + " > ".join(run))
        return " | ".join(parts)

    @property
    def name(self) -> str:
        return f"phone {self.serial}" if self.serial else f"build {self.index}"


@dataclass(frozen=True)
class Capacity:
    """How many ready phones the current stock can produce, and out of what.

    Domain arithmetic, not presentation, which is why it is here rather than in
    the console that asks the question. Getting it wrong offered three phones
    against two app accounts, and the third was certain to end on
    no_usable_gpt having spent a phone, a Gmail and a proxy to get there
    (2026-08-11).

    The trap is that a phone waiting to be finished and a phone built from
    nothing both consume exactly one app account. They cannot be added up
    independently: the app pool caps the run as a whole.
    """

    waiting: int          # phones that need only an app account
    proxies: int
    gmails: int
    app_accounts: int

    @property
    def from_scratch(self) -> int:
        """New phones the proxies and Gmails allow, app accounts aside."""
        return min(self.proxies, self.gmails)

    @property
    def total(self) -> int:
        """Ready phones obtainable now."""
        return min(self.app_accounts, self.waiting + self.from_scratch)

    @property
    def finishing(self) -> int:
        """Of those, how many are finished rather than built. Finishing comes
        first because it is the cheapest ready phone available."""
        return min(self.total, self.waiting)

    @property
    def building(self) -> int:
        return self.total - self.finishing

    @property
    def limited_by(self) -> str:
        """Which pool is actually binding - the one worth topping up.

        Named rather than assumed: "10 gpt accounts is the limit, so 2 phones
        uses them all" is visibly untrue, and a line that does not add up stops
        being read.
        """
        if not self.app_accounts:
            return "app accounts"
        if self.app_accounts <= self.waiting + self.from_scratch:
            return "app accounts"
        if self.proxies <= self.gmails:
            return "proxies"
        return "gmails"


class Reporter(Protocol):
    """Where a run announces its progress - the plain CLI or the console."""

    def start(self, index: int, total: int, *,
              serial: str = "", gmail: str = "") -> None: ...
    def finish(self, build: Build) -> None: ...


class Aborted(Exception):
    """The run is shutting down; stop what this build is doing."""


#: Serials somebody asked to stop from the web ("Stop this one", C7). The
#: pass's drain adds to it; every session looks at its next step and, if
#: its phone is named, gives up with `stopped_by_hand` - the same path an
#: interrupt takes, so what it held goes back to its pool. One process,
#: so a set is enough; a serial is taken out the moment it is honoured.
STOP_BY_HAND: set[str] = set()


@dataclass
class _Session:
    """One phone being worked on, and everything claimed for it.

    Exists so `build` and `finish` drive the app login through the same code.
    They differ only in how the phone got here - built from nothing, or picked
    up already signed in and installed - and the moment those two grew separate
    copies of this loop is the moment its rules start drifting apart.
    """

    client: Client
    settings: Settings
    book: Book
    build: Build
    phone_id: str
    artifacts: Path
    deadline: float
    # When the work on this phone began, so a build that ends inside the app
    # phase still reports how long it took. Without it the summary said 0s for
    # every phone that ran out of accounts - several minutes of work reported
    # as none (2026-08-11, phones 668 and 670).
    started: float = 0.0
    #: Who answers a code OpenAI emails an app account. Nothing by default,
    #: which is what the tool did before the code page could be answered.
    codes: codes.CodeSource = field(default_factory=codes.NoSource)
    cancelled: Callable[[], bool] | None = None
    proxy_row: Resource | None = None
    app_row: Resource | None = None
    app_signed_in: bool = False
    exits: int = 0
    #: How many app logins this phone has been through. The first starts on a
    #: freshly installed app; every one after it has to clear what the last
    #: attempt left on screen.
    attempted: int = 0
    #: Whether even the first attempt has to start from a cleared app. A build
    #: installed the app a moment ago and nothing has touched it, so it does
    #: not; a finish picked up a phone that has been sitting with whatever an
    #: earlier run left signed in, and `act_reset_app` only clears that when it
    #: happens to recognise the screen. Unconditional here is one `pm clear`
    #: and three seconds, against reading a previous session as this account's
    #: problem (2026-08-30).
    reset_first: bool = False
    #: Addresses this phone has condemned, in order. Counted rather than
    #: merely recorded: past a point they stop being evidence about the
    #: accounts and start being evidence about the phone. See
    #: ACCOUNTS_BEFORE_BLAMING_THE_PHONE.
    condemned: list[str] = field(default_factory=list)
    #: Accounts the service challenged rather than judged, with what it asked
    #: for - held so this build does not take them again, and put back at the
    #: end carrying the reason as their status.
    set_aside: list[tuple[Resource, str]] = field(default_factory=list)
    #: The APP_SUSPECTS reason the phone stopped on, if it did - read at
    #: release time so the account it happened with carries a strike.
    suspect_reason: str = ""
    # Proxies tried and moved on from, with what was seen through each. Held
    # claimed for the rest of the run so a swap cannot hand one back.
    refused_exits: list[tuple[Resource, str]] = field(default_factory=list)
    #: Exits taken from under another phone once the pool ran dry. Not claimed
    #: - they belong to that phone - so they are remembered here instead.
    borrowed: set[str] = field(default_factory=set)

    def exits_seen(self) -> set[str]:
        """Every exit address this phone has been through, refused or current.

        What bounds the swap loop. A borrowed exit is not claimed - another
        phone owns it - so it leaves no trace in `refused_exits`, and without
        this the build would take the same shared exit back every time.
        """
        seen = {f"{r.proxy.host}:{r.proxy.port}"
                for r, _ in self.refused_exits if r.proxy}
        seen |= self.borrowed
        if self.proxy_row is not None and self.proxy_row.proxy:
            seen.add(f"{self.proxy_row.proxy.host}:{self.proxy_row.proxy.port}")
        return seen

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def check_cancelled(self) -> None:
        if self.cancelled and self.cancelled():
            raise Aborted("interrupted")
        serial = str(self.build.serial or "").strip()
        if serial and serial in STOP_BY_HAND:
            STOP_BY_HAND.discard(serial)
            raise Aborted("stopped_by_hand")

    def finish(self, status: str, detail: str = "", ok: bool = False) -> Build:
        self.build.ok, self.build.status, self.build.detail = ok, status, detail
        if self.started:
            self.build.seconds = time.monotonic() - self.started
        return self.build


def _sign_into_app(session: _Session) -> Build | None:
    """Work through the app accounts on a phone that is signed in and installed.

    Returns a finished Build when it gives up, or None once an account is in -
    so the caller can carry on to whatever it does after.
    """
    s = session
    while not s.app_signed_in:
        s.check_cancelled()
        # Before another account is spent on it. This loop is where a finish
        # spends most of its minutes, so it is where a row marked mid-run has
        # to be noticed - and an account claimed for a phone about to be
        # deleted is the one cost worth a read of the tab to avoid.
        marked = _given_up_on(s.book, s.build.serial)
        if marked:
            return s.finish("given_up_on",
                            f"somebody wrote {marked!r} in its State while "
                            f"this was running, so it was left alone")
        if s.remaining() <= ATTEMPT_SECONDS:
            _give_back_condemned(s)
            return s.finish("budget_exhausted",
                            "installed, but no budget left for the app login")
        if s.app_row is None:
            s.app_row = s.book.apps.claim(str(s.build.serial or ''))
            if s.app_row is None:
                _give_back_condemned(s)
                return s.finish("no_usable_gpt",
                                "the Gpt Info tab has no unused account left")
        log.info("signing into the app as %s", s.app_row.credentials.email)
        outcome = chatgpt_login.sign_in(
            s.client, s.phone_id, s.app_row.credentials,
            package=s.settings.target_package,
            budget_seconds=min(s.settings.app_login_budget_seconds,
                               s.remaining()),
            artifact_dir=s.artifacts,
            # Where a code OpenAI emails is answered from. Nothing by
            # default, which reports the page exactly as it always did.
            codes=s.codes,
            # Every attempt after the first starts from a cleared app. The
            # previous one left the app wherever it stopped, and the router
            # matches whatever is on screen - so without this, one account's
            # verification page is read as the next account's problem.
            fresh=s.attempted > 0 or s.reset_first,
        )
        s.attempted += 1
        # Each attempt appends its own, so a phone that worked through three
        # accounts leaves three paths rather than one path with the first two
        # missing - and how far each got is most of what separates a bad batch
        # of credentials from a phone that cannot sign anyone in.
        s.build.trails.append(("gpt", outcome.trail))
        if outcome.ok:
            s.build.app_account = s.app_row.credentials.email
            s.app_signed_in = True
            return None
        s.build.tried.append((s.app_row.credentials.email, outcome.reason,
                              s.book.apps.service))

        if failures.verdict(outcome.reason).needs_a_new_exit:
            # Refused before the account was looked at, so it is the exit's
            # problem, not the account's. Keep the account, change where the
            # request comes from, and try again - for as long as there is
            # budget and there are proxies. This does not count against the
            # account's attempts and never fails the phone with the network
            # reason: if it runs out, it runs out of budget or proxies, and
            # _new_exit says which. The account goes back as stock, never
            # judged.
            previous = s.proxy_row
            before = s.exits_seen()
            s.proxy_row = _new_exit(s.client, s.settings, s.book, s.build,
                                    s.phone_id, s.proxy_row, outcome.reason,
                                    s.remaining(), swaps=s.exits,
                                    avoid=s.exits_seen(),
                                    cancelled=s.cancelled)
            taken = s.proxy_row
            if taken is not None and taken.proxy:
                where = f"{taken.proxy.host}:{taken.proxy.port}"
                if where not in before and s.build.shared_exit:
                    # Borrowed rather than claimed, so nothing else records it.
                    s.borrowed.add(where)
                    s.proxy_row = previous   # keep holding what we do own
            if previous is not None and previous is not s.proxy_row:
                # Held, not freed - see _new_exit. Released at the end.
                s.refused_exits.append((previous, outcome.reason))
            s.exits += 1
            continue
        if failures.verdict(outcome.reason).sets_aside:
            # The service asked for something no unattended run can give - a
            # code in an inbox. It judged nothing about the account, so the
            # account is held rather than marked, and goes back on the shelf
            # as available when the build ends. Held rather than released now,
            # so this build does not immediately claim it again.
            s.set_aside.append((s.app_row, outcome.reason))
            s.app_row = None
            continue
        if failures.verdict(outcome.reason).stops_the_phone:
            # The app never got as far as judging this account, so it goes
            # back to the pool untouched and the phone reports its own problem.
            # Named app_* so the runbook entry someone reaches for is the
            # app's, not Google's - unless the reason already says so, since
            # "app_app_would_not_start" helps nobody.
            named = (outcome.reason if outcome.reason.startswith("app")
                     else f"app_{outcome.reason}")
            said = failures.verdict(outcome.reason, s.book.apps.service)
            if outcome.reason in APP_SUSPECTS:
                # The phone keeps the blame, but the account was typed in -
                # _session_holds counts a strike against it on the way out.
                s.suspect_reason = outcome.reason
            _give_back_condemned(s)
            return s.finish(named,
                            f"the app login could not go on with this phone - "
                            f"{said.seen}")
        condemned = s.app_row.credentials.email
        s.book.apps.fail(s.app_row, outcome.reason,
                         note=failures.verdict(outcome.reason,
                                              s.book.apps.service).advice)
        s.app_row = None
        s.condemned.append(condemned)
    return None


#: No threshold, deliberately. The first version of this asked for two
#: accounts before it would believe the phone, and that number was wrong in
#: the one situation it most had to be right in: a thin pool. Phone 1465 was
#: handed the only free account there was, refused it, ran out, and kept the
#: condemnation - twice, in two separate passes, on an account whose password
#: its owner then checked by hand and found perfectly good. The same pass had
#: phone 1468 refuse two and give both back, because two was all that was
#: left to give it (2026-08-30).
#:
#: The signal was never how many a phone ate. It is that the phone signed
#: nobody in, and a phone that signed nobody in has proved nothing about any
#: account it touched.
def _refused_what_it_was_given(session: _Session | None) -> bool:
    """Whether this phone turned down every account it was handed.

    The same test `_give_back_condemned` makes, asked from the outside so the
    tally and the exoneration cannot come to different answers about the same
    run.
    """
    return bool(session and session.condemned and not session.app_signed_in)


def _give_back_condemned(s: _Session) -> None:
    """Undo this phone's judgements when the phone itself is the likelier fault.

    Called only where the app phase gives up having signed nobody in. The
    verdict for a refused password already says the sheet's password "may well
    be right" - the service shows that page when it is refusing for other
    reasons too. A phone that refused every one of them is that other reason.

    Released, not set aside: nothing was learnt about these rows, and an empty
    status is what "nothing was learnt" means. If one of them really is bad it
    will be condemned again on the next phone - and stay condemned, because
    that phone will sign somebody in. That is the whole safeguard against
    handing a genuinely bad account round the pool for ever, and it is enough:
    a bad account only has to meet one working phone.
    """
    if s.app_signed_in or not s.condemned:
        return
    for address in s.condemned:
        row = s.book.apps.find(address)
        if row is None:
            continue
        s.book.apps.release(row, note=(
            f"Phone {s.build.serial} refused {len(s.condemned)} accounts and "
            f"signed none in, so the phone or its exit is the likelier fault "
            f"and nothing was judged here. Free to try on another phone."))
    log.warning("phone %s refused %d accounts and signed none in (%s); "
                "putting them back rather than leaving them condemned",
                s.build.serial, len(s.condemned), ", ".join(s.condemned))


def _fresh_proxy(client: Client, book: Book) -> Resource:
    """Claim a proxy GeeLark can actually reach.

    Checked before it is used, because an unreachable proxy is the one failure
    that is genuinely the proxy's: GeeLark either carried the request or it did
    not. Those are marked `dead` and the next one is tried.

    The proxy being replaced is released only after this returns, so `claim()`
    cannot hand back the very proxy that was just judged.

    There is no cap on how many dead ones it will skip. Each is marked `dead`
    before the next is claimed, so the pool strictly shrinks and this cannot
    spin - and a cap costs working phones: when a whole purchase batch died,
    one build hit five dead proxies in a row and gave up while four live ones
    sat in the tab (2026-08-11).

    The two ways to run out are told apart, because they need different things
    doing. `no_usable_proxy` means the tab has nothing left to hand out.
    `no_working_proxy` means it had rows and every one of them was unreachable,
    which is a fact about the stock rather than about this run.
    """
    skipped = 0
    while True:
        resource = book.proxies.claim()
        if resource is None:
            raise Aborted("no_working_proxy" if skipped else "no_usable_proxy")
        try:
            result = proxy_mod.check(client, resource.proxy)
        except (proxy_mod.ProxyError, ApiError) as exc:
            # The name, not the label: the label carries the whole address,
            # and so does the error after it, so the line printed one
            # credential-bearing URL twice and wrapped over two rows to do it.
            # What the name is for is finding the exit in the vendor's panel;
            # what failed and why is the error's job.
            log.warning("proxy %s is dead: %s", resource.name or resource.label,
                        exc)
            book.proxies.fail(resource, "dead", note=(
                f"GeeLark could not reach it when a phone was put behind it: "
                f"{exc}"))
            skipped += 1
            continue
        book.proxies.record_exit(resource, str(result.get("outboundIP") or ""))
        return resource


def build_one(client: Client, settings: Settings, book: Book, ledger: Ledger,
              index: int, *,
              on_phone: Callable[[str], None] | None = None,
              on_ready: Callable[[str], None] | None = None,
              cancelled: Callable[[], bool] | None = None,
              codes_source: codes.CodeSource | None = None) -> Build:
    """Take pooled resources to one stopped, ready phone.

    Returns rather than raises: the caller is a batch, and one bad phone must
    not end it.
    """
    started = time.monotonic()
    deadline = started + settings.build_budget_seconds
    build = Build(index=index)
    phone_id = ""
    proxy_row: Resource | None = None
    gmail_row: Resource | None = None
    log_row: int | None = None
    # Proxies this build tried and moved on from, with what was seen through
    # each. They stay claimed for the rest of the build so a swap cannot hand
    # one back, and are released together at the end.
    refused_exits: list[tuple[Resource, str]] = []
    # The Gmail phase counts its own attempts; the app phase's are the
    # session's, since that loop is shared with `finish`.
    tried_gmails = 0
    session: _Session | None = None
    # Whether the Gmail ended up on the device. Not the same question as "did
    # the build succeed" - see _release. The app account's equivalent lives on
    # the session, which owns that phase.
    gmail_signed_in = False

    def remaining() -> float:
        return deadline - time.monotonic()

    def check_cancelled() -> None:
        if cancelled and cancelled():
            raise Aborted("interrupted")

    def finish(status: str, detail: str = "", ok: bool = False) -> Build:
        build.ok, build.status, build.detail = ok, status, detail
        build.seconds = time.monotonic() - started
        return build

    try:
        # Claiming the Gmail, taking the exit and creating the phone happen
        # under one lock, and in that order, for two reasons.
        #
        # **A phone is not created without an address to sign in.** It used to
        # be: the phone came first and the tab was asked afterwards, so a run
        # that had run out of Gmails still paid for a phone, and two of them sat
        # in the tab as `incomplete` with an empty Gmail column - devices with
        # nothing on them, which `finish` then refuses because there is no
        # Google account to build on (2026-08-14).
        #
        # **The serials come out in the same order as the addresses.** GeeLark
        # numbers a phone when it is created, so whoever creates first gets the
        # lower serial. With the claim and the create apart, two workers
        # interleaved and phone 701 got the second address while 702 got the
        # first. Holding both together costs a few seconds of serial creation
        # at the start of a batch and nothing after it.
        with _starting:
            gmail_row = book.gmails.claim()
            if gmail_row is None:
                return finish("no_usable_gmail",
                              "the Gmails tab has no unused address left, so "
                              "no phone was created")
            proxy_row = _fresh_proxy(client, book)
            build.proxy = str(proxy_row.proxy)
            build.proxy_name = proxy_row.name

            entry = phones.create(client, settings, proxy_row.proxy,
                                  ledger=ledger, label=f"build {index}",
                                  account=gmail_row.label)
        phone_id = entry.phone_id
        build.phone_id = phone_id
        build.serial = str(entry.serial or "")
        # This phone did not exist a moment ago, so nothing is installed on
        # it. Said here rather than left to the field's default so that the
        # default can mean "nobody looked" - which is what a `finish` that
        # never reached the device has to be able to say.
        build.app_installed = False
        if on_phone:
            on_phone(phone_id)
        ledger.claim(phone_id, label=f"build {index}")

        # Serial, id and proxy - the three things that identify this phone
        # somewhere else. The model and region used to be written beside them
        # and cost a phone-list call each build to find out; nothing ever read
        # them back, and every phone had the same two values anyway.
        log_row = book.phones.start(Serial=build.serial,
                                    Proxy=build.proxy_name or build.proxy)
        # The Gmail was claimed inside `_starting`, before this phone existed -
        # it has to be, or a phone can be created with no address to sign in.
        # So the serial goes on now, the moment there is one. Without it the
        # row reads `in_use` with nothing saying which phone, and a tab with
        # several at once can be counted but not read (2026-08-29).
        book.gmails.note_serial(gmail_row, build.serial)

        stamp = time.strftime("%Y%m%d-%H%M%S")
        # By serial, not by batch position: `build3` today and `build3`
        # three weeks ago are different phones, so a directory could not
        # be tied to a device and nothing could decide whether its pages
        # still described anything (2026-08-17).
        artifacts = (settings.artifact_dir
                     / f"{stamp}-build{build.serial or index}")
        build.artifact_dir = str(artifacts)

        phones.ensure_running(client, phone_id,
                              timeout=min(phones.BOOT_SECONDS, remaining()),
                              cancelled=cancelled)
        if on_ready:
            on_ready(phone_id)

        # ------------------------------------------------------- the Gmail
        while not gmail_signed_in:
            check_cancelled()
            if remaining() <= ATTEMPT_SECONDS:
                return finish("budget_exhausted",
                              "ran out of budget before a Gmail signed in")
            if gmail_row is None:
                # The first was claimed before the phone existed; this is the
                # next one, after that address was refused on this device - so
                # this one can say which phone it is on from the start.
                gmail_row = book.gmails.claim(build.serial)
                if gmail_row is None:
                    return finish("no_usable_gmail",
                                  "the Gmails tab had no other address to try "
                                  "on this phone")
            # Every field, rather than the three somebody remembered. `Account`
            # subclasses `Credentials`, and this list was a copy of its fields
            # as they stood the day it was written: `email_code_only` was added
            # later and has been silently dropped here ever since, harmless
            # only because the Gmails tab never sets it. `recovery_email` would
            # have gone the same way, and the flow would have refused a row for
            # having no recovery address while the address sat on the row.
            account = Account(**dataclasses.asdict(gmail_row.credentials),
                              proxy=build.proxy)
            log.info("signing in as %s", account.email)
            outcome = google_login.sign_in(
                client, phone_id, account,
                budget_seconds=min(settings.login_budget_seconds, remaining()),
                artifact_dir=artifacts,
            )
            build.trails.append(("google", outcome.trail))
            if outcome.ok:
                build.gmail = account.email
                gmail_signed_in = True
                # On the row now, not at the end. This is the column that
                # decides whether a phone a killed run left behind is
                # finishable or gets deleted, and it is true from this moment.
                _note_on_row(book, build.serial, Gmail=account.email)
                break
            # Every way a Google sign-in fails is about the account or the
            # device, never the exit: a CAPTCHA is Google distrusting this
            # address's history, not the IP (the network refusals that ARE the
            # exit's fault come only from the app, in the loop below). So the
            # Gmail is marked and the next one is tried on the same phone.
            build.tried.append((account.email, outcome.reason,
                                book.gmails.service))
            if failures.verdict(outcome.reason).stops_the_phone:
                # Nothing was decided about this address, so it keeps its place
                # in the pool - _release puts it back as stock. Trying the next
                # one would only meet the same wall.
                said = failures.verdict(outcome.reason,
                                        book.gmails.service)
                return finish(outcome.reason,
                              f"the Google sign-in could not go on with this "
                              f"phone - {said.seen}")
            # The tab gets the taxonomy's advice, not the flow's. A flow
            # writes for whoever is debugging it; the sheet is read a day
            # later by someone deciding what to do with that row - and for a
            # CAPTCHA the two say opposite things, since the flow suggests a
            # cleaner proxy and the build has just set the address aside.
            book.gmails.fail(gmail_row, outcome.reason,
                             note=failures.verdict(outcome.reason,
                                                  book.gmails.service).advice)
            gmail_row = None
            tried_gmails += 1

        # ----------------------------------------------------- the install
        check_cancelled()
        marked = _given_up_on(book, build.serial)
        if marked:
            return finish("given_up_on",
                          f"somebody wrote {marked!r} in its State while this "
                          f"was running, so it was left alone")
        if remaining() <= 0:
            return finish("budget_exhausted", "signed in, but no time to install")
        installed = play_install.install(
            client, phone_id, settings.target_package,
            budget_seconds=min(settings.install_budget_seconds, remaining()),
            artifact_dir=artifacts,
        )
        build.trails.append(("install", installed.trail))
        if not installed.ok:
            return finish("install_failed",
                          f"the app could not be installed - "
                          f"{failures.verdict(installed.reason).seen}")
        build.app_installed = True

        # ------------------------------------------------- the app account
        session = _Session(client=client, settings=settings, book=book,
                           build=build, phone_id=phone_id, artifacts=artifacts,
                           deadline=deadline, started=started,
                           cancelled=cancelled,
                           codes=codes_source or codes.NoSource(),
                           proxy_row=proxy_row, refused_exits=refused_exits)
        gave_up = _sign_into_app(session)
        if gave_up is not None:
            return gave_up

        # Asked of the device, not of the run's own belief - and logged rather
        # than written to the tab, because "which packages are on it" is a
        # debugging question and the Note column is read by a person.
        packages = shell.third_party_packages(client, phone_id)
        log.info("installed here: %s", ", ".join(packages) or "nothing")
        return finish("ready", "signed into Google and into the app", ok=True)

    except Aborted as exc:
        return finish(str(exc), failures.situation(str(exc)))
    except TransportError as exc:
        # The machine lost its network, which is not an error nobody planned
        # for - it is a named thing that costs nothing. Reported as such, with
        # the traceback left in the log file rather than dumped over a live
        # table: two hundred lines of urllib3 to say the connection went away
        # (2026-08-17).
        log.error("the network went away: %s", exc)
        return finish("network_unreachable",
                      failures.situation("network_unreachable"))
    except phones.PhoneCapacityError as exc:
        # Before `PhoneError`, because it is one. GeeLark had no machine of
        # this Android version free, which says nothing about this phone, this
        # account or this row - and `start` has already asked several times.
        log.warning("no capacity at GeeLark: %s", exc)
        return finish("no_capacity", failures.situation("no_capacity"))
    except phones.PhoneError as exc:
        # Expected, and named. It used to reach the catch-all below and be
        # reported as "an error nobody planned for", which is the wrong thing
        # to tell someone about a phone that simply did not boot - or about one
        # that was deleted underneath the build, which is a different sentence
        # and points at a different culprit.
        vanished = "env not found" in str(exc) or "no longer exists" in str(exc)
        return finish("phone_is_gone" if vanished else "phone_would_not_start",
                      str(exc))
    except Exception as exc:                                      # noqa: BLE001
        # Deliberately broad. Whatever went wrong, the resources this build is
        # holding must go back and the phone must be stopped - an exception
        # escaping here leaves three tabs saying `in_use` and a phone billing.
        log.exception("build %d failed with an unhandled error", index)
        return finish("error", f"an error nobody planned for stopped it: {exc}")
    finally:
        # Once the app phase starts, the session is what holds the claims - it
        # swaps proxies and claims accounts as it goes. Read them back from it
        # here rather than from the locals, because an Aborted raised inside it
        # never returns to update them, and the account it was holding would
        # stay in_use with nothing to free it.
        # The Gmail is this function's own - the session never sees it. A
        # proxy counts as used the moment a phone exists behind it: that phone
        # keeps it until someone deletes the phone, and handing it on would put
        # two devices on one exit address.
        held = [(book.gmails, gmail_row,
                 SPEND if gmail_signed_in else RELEASE, "", "")]
        if session is None:
            held.append((book.proxies, proxy_row,
                         SPEND if phone_id else RELEASE, "", ""))
        else:
            held += _session_holds(book, session, proxy_spent=bool(phone_id))
        _release(book, build, held)
        # A phone with no Google account on it is not a phone. Nothing can be
        # done with it - `finish` refuses it by name, since there is nothing to
        # build on - so it is deleted rather than left occupying a plan slot and
        # a row that reads `incomplete` with an empty Gmail column. Its exit
        # goes back with it, which is why this runs before the row is written.
        #
        # Not while the run is shutting down: an interrupt is not a verdict on
        # the phone, and the next run's sync sees it either way.
        discarded = (phone_id and not gmail_signed_in
                     and build.status != "interrupted"
                     and _discard(client, book, ledger, build))
        # By serial, not by the row number `start` handed back ten minutes ago.
        # Any sibling discarding its phone deletes a row, and every row below it
        # moves up - so that number can have come to mean a different phone.
        if log_row is not None:
            _write_row(book, build, drop=discarded)
        if phone_id and not discarded:
            try:
                phones.stop(client, phone_id)
                log.info("stopped %s", phone_id)
            except Exception as exc:                              # noqa: BLE001
                build.still_running = True
                log.error("COULD NOT STOP %s (%s) - run 'geelark reap'",
                          phone_id, exc)
            ledger.release(phone_id, note=build.status)


def _discard(client: Client, book: Book, ledger: Ledger,
             build: Build) -> bool:
    """Delete a phone nothing was ever signed into, and free its exit.

    Returns whether it went. A delete that fails leaves the phone to be stopped
    and recorded the ordinary way - half-deleting it, with its row dropped and
    the device still there, is the one outcome worse than keeping it.
    """
    try:
        # Stopped first, and not as a courtesy: GeeLark refuses to delete a
        # running phone, and this runs before the stop that the ordinary path
        # does at the end. Both phones this discarded on 2026-08-17 were still
        # running when it asked, so both refusals came back under failDetails
        # while the tool went on to drop their rows.
        phones.stop(client, build.phone_id)
        phones.wait_until_stopped(client, build.phone_id)
        phones.delete(client, [build.phone_id], ledger=ledger)
    except Exception as exc:                                      # noqa: BLE001
        log.error("phone %s has no Google account on it and could not be "
                  "deleted (%s); it is recorded and left alone",
                  build.serial or build.phone_id, exc)
        return False
    log.info("deleted phone %s - nothing was ever signed into it (%s)",
             build.serial or build.phone_id, outcome_of(build))
    book.record_history(
        Serial=build.serial, Event="discarded",
        Seconds=f"{build.seconds:.0f}", Proxy=build.proxy_name or build.proxy,
        Steps=build.steps,
        Note=(f"Deleted rather than kept - nothing was ever signed into it. "
              f"{outcome_of(build).capitalize()}."))
    resource = book.proxies.find_proxy(build.proxy) if build.proxy else None
    if resource is not None:
        book.proxies.release(resource, note=(
            "Free again - the phone taken on it had nothing signed in and was "
            "deleted."))
    build.phone_id = ""
    return True


def finish_one(client: Client, settings: Settings, book: Book, ledger: Ledger,
               phone: dict, index: int, *,
               on_phone: Callable[[str], None] | None = None,
               cancelled: Callable[[], bool] | None = None,
               codes_source: codes.CodeSource | None = None) -> Build:
    """Complete a phone that has everything but its app account.

    A phone that ran out of app accounts is not a failure to throw away: it is
    signed into Google and has the app on it, and only the last step is
    missing. Building a replacement pays for a phone, a Gmail and a proxy to
    get back to where this one already is - so when the tab is topped up, this
    picks it up instead.

    What it is willing to do is checked against the device, not the sheet. The
    sheet says what the run believed; `dumpsys` and `pm list` say what is
    actually there, and a phone whose Google account is gone is not something
    to sign an app account into.
    """
    started = time.monotonic()
    build = Build(index=index, phone_id=phone["phone_id"],
                  serial=phone["serial"], gmail=phone.get("gmail", ""),
                  proxy=phone.get("proxy", ""))
    deadline = started + settings.build_budget_seconds
    session: _Session | None = None

    def finish(status: str, detail: str = "", ok: bool = False) -> Build:
        build.ok, build.status, build.detail = ok, status, detail
        build.seconds = time.monotonic() - started
        return build

    phone_id = build.phone_id
    try:
        if on_phone:
            on_phone(phone_id)

        # A phone that is already running, with nothing in the ledger holding
        # it, is one somebody started by hand and is using right now. Do not
        # drive it.
        #
        # This is the second net under the `taken` word, and it catches the
        # case that word is forgotten in. What it prevents is severe: the app
        # would be showing a session this run did not create, `act_reset_app`
        # reads a chat screen with no `Log in` control as the app's logged-out
        # mode, and settles the ambiguity with `pm clear` - throwing away
        # somebody's signed-in account to make room for one of ours. The flow's
        # own docstring names that cost; it was written about a previous run's
        # session, not about a person's (2026-08-29).
        held = ledger.get(phone_id)
        if held is None or not held.is_claimed or held.is_stale:
            try:
                live = phones.status(client, phone_id)
            except Exception as exc:                              # noqa: BLE001
                # Not knowing is not a reason to refuse - the boot below asks
                # again anyway, and a finish that cannot start is its own
                # named failure.
                log.debug("could not read the state of %s (%s)", phone_id, exc)
            else:
                if live in (phones.RUNNING, phones.STARTING):
                    return finish("in_use_by_hand",
                                  "the phone is already running and nothing "
                                  "here started it, so somebody is using it")

        ledger.claim(phone_id, label=f"finish {build.serial}")
        # Say on the sheet that this phone is in hand, the moment it is. A
        # finish leaves the row reading `incomplete` for its whole length -
        # which is what it read before the finish started - so the tab gave a
        # reader no way to tell a phone being worked on right now from one
        # sitting warm and untouched. The account's row says `in_use` in the
        # same minute, and the two are meant to be read together (2026-08-28).
        #
        # Restored by `_write_row` in the `finally` whatever happens, so an
        # interrupted finish does not leave it saying `building` forever - and
        # `settle_abandoned` treats a `building` row with a stale claim as
        # abandoned, which is exactly what it would be.
        _note_on_row(book, build.serial, Status=book.phones.BUILDING)

        stamp = time.strftime("%Y%m%d-%H%M%S")
        artifacts = settings.artifact_dir / f"{stamp}-finish{build.serial}"
        build.artifact_dir = str(artifacts)
        phones.ensure_running(client, phone_id,
                              timeout=min(phones.BOOT_SECONDS,
                                          deadline - time.monotonic()),
                              cancelled=cancelled)

        # The device is the only truth. A row can say anything; what decides
        # whether this phone can be finished is what is on it.
        present = shell.device_accounts(client, phone_id)
        if not present:
            return finish("no_google_account",
                          "nothing is signed into Google on this phone, so "
                          "there is nothing left to finish; rebuild it")
        build.gmail = build.gmail or present[0]

        if settings.target_package not in shell.third_party_packages(client,
                                                                     phone_id):
            log.info("%s is not installed here; installing it first",
                     settings.target_package)
            installed = play_install.install(
                client, phone_id, settings.target_package,
                budget_seconds=min(settings.install_budget_seconds,
                                   deadline - time.monotonic()),
                artifact_dir=artifacts,
            )
            build.trails.append(("install", installed.trail))
            if not installed.ok:
                # Looked, and it is not there - which is a different answer
                # from the `None` this started as, and the row should say so.
                build.app_installed = False
                # The taxonomy's words, not the flow's. play_install writes its
                # detail for whoever is debugging it - "on screen: [Install,
                # Uninstall]" - and that is not what the tab is read for.
                return finish("install_failed",
                              f"the app could not be installed - "
                              f"{failures.verdict(installed.reason).seen}")
        # Either it was already there or it is now. Read off the device, which
        # is the only thing that settles it - the row may say anything.
        build.app_installed = True

        # The proxy this phone already has, when the tab names one row and
        # only one. Not claimed - the phone owns it - and `_session_holds` is
        # asked with proxy_spent=True, so it is written back as still on this
        # phone rather than released as stock.
        #
        # It was None, so a finish refused at the edge had no row to
        # settle: the exit it was actually on went unrecorded, and the one it
        # took instead was written back as if it had always been there.
        # Ambiguity still answers None, which is exactly today's behaviour
        # (2026-08-23).
        own_exit = book.proxies.find_by_name(build.proxy)
        session = _Session(client=client, settings=settings, book=book,
                           build=build, phone_id=phone_id, artifacts=artifacts,
                           deadline=deadline, started=started,
                           codes=codes_source or codes.NoSource(),
                           cancelled=cancelled, proxy_row=own_exit,
                           # A finish the web ordered names its account
                           # (C6): the verb claimed that row for this serial
                           # already, so the login loop must not claim the
                           # first free one instead.
                           app_row=phone.get("account"),
                           reset_first=True)
        gave_up = _sign_into_app(session)
        if gave_up is not None:
            return gave_up

        # Asked of the device, not of the run's own belief - and logged rather
        # than written to the tab, because "which packages are on it" is a
        # debugging question and the Note column is read by a person.
        packages = shell.third_party_packages(client, phone_id)
        log.info("installed here: %s", ", ".join(packages) or "nothing")
        return finish("ready", "signed into Google and into the app", ok=True)

    except Aborted as exc:
        return finish(str(exc), failures.situation(str(exc)))
    except TransportError as exc:
        # The machine lost its network, which is not an error nobody planned
        # for - it is a named thing that costs nothing. Reported as such, with
        # the traceback left in the log file rather than dumped over a live
        # table: two hundred lines of urllib3 to say the connection went away
        # (2026-08-17).
        log.error("the network went away: %s", exc)
        return finish("network_unreachable",
                      failures.situation("network_unreachable"))
    except phones.PhoneCapacityError as exc:
        log.warning("no capacity at GeeLark: %s", exc)
        return finish("no_capacity", failures.situation("no_capacity"))
    except phones.PhoneError as exc:
        # The same naming `build_one` has had since 2026-08-17. Finishing
        # lacked it, so a phone that would not boot was reported here as "an
        # error nobody planned for" - and a GeeLark capacity refusal, which is
        # nobody's fault at all, counted against the breaker as one (2026-08-28).
        vanished = "env not found" in str(exc) or "no longer exists" in str(exc)
        return finish("phone_is_gone" if vanished else "phone_would_not_start",
                      str(exc))
    except Exception as exc:                                      # noqa: BLE001
        log.exception("finishing %s failed with an unhandled error", build.serial)
        return finish("error", str(exc))
    finally:
        # A proxy swapped in during finishing belongs to this phone now.
        _release(book, build,
                 _session_holds(book, session, proxy_spent=True))
        # One more attempt on the tally, but only for a finish that says
        # something about the phone.
        #
        # `breaker` already draws that line and draws it in two places:
        # `WORKED` is a build that proves the pipeline works, and
        # `NOTHING_HAPPENED` is one where nothing was created and nothing
        # spent. Neither is evidence against the phone, and the tally is
        # nothing but evidence against the phone - so it reads the same sets
        # rather than keeping its own opinion, which is how the two came to
        # disagree. `no_usable_gpt` is in `WORKED`, and it retired phones 1465
        # and 1468 at three strikes each for the Gpt Info tab being empty -
        # which is not their fault and not something they can be fixed of
        # (2026-08-30). `in_use_by_hand` was the one exception written out by
        # hand here; it is in `NOTHING_HAPPENED` and now arrives with the rest.
        #
        # And the phone is charged for refusing accounts it was given, which
        # `breaker` cannot see: those runs end `no_usable_gpt` - the tab ran
        # dry, because this phone had just spent what was in it - and that is
        # in `WORKED`. Without this half, exonerating the accounts left nobody
        # answerable: phone 1465 refused a hand-verified account on a
        # hand-swapped exit, gave it back, went back on the shelf, and would
        # have done it again every pass for ever. `_give_back_condemned` is
        # where the run concludes the phone is the fault; this is the same
        # conclusion, spent (2026-08-30).
        if breaker.counts_against(build) or _refused_what_it_was_given(session):
            _count_try(book, build)
        _write_row(book, build)
        try:
            phones.stop(client, phone_id)
            log.info("stopped %s", phone_id)
        except Exception as exc:                                  # noqa: BLE001
            build.still_running = True
            log.error("COULD NOT STOP %s (%s) - run 'geelark reap'",
                      phone_id, exc)
        ledger.release(phone_id, note=build.status)


def _borrow_exit(book: Book, avoid: set[str]) -> Resource | None:
    """An exit already behind another phone, when nothing is free.

    This breaks the rule the rest of the module keeps: one phone per exit. It
    is deliberate and it is a last resort, reached only once the pool has
    nothing free at all, because the alternative is what happened to phone 762
    - everything done right, one ordinary refusal, and no second exit to answer
    it with.

    What it costs is worth stating plainly. Two phones behind one address means
    Google and OpenAI can see the two accounts arriving from the same place, so
    a run that shares exits is linking the accounts it builds. That is the
    operator's trade to make and they have made it; the sharing is written into
    both the phone's note and the proxy's, so it is never a surprise later.

    `avoid` is every exit this build has already been through. Without it the
    loop has no bound: a phone refused twice would take back the exit that
    refused it first and go round for as long as its budget lasted, which is
    exactly what holding refused proxies claimed was written to stop
    (2026-08-11, phone 658, forty-nine minutes).
    """
    for resource in book.proxies._rows:
        if resource.error or not resource.proxy:
            continue
        if book.proxies.status_of(resource) != book.proxies.spent_status:
            continue                      # free, dead or claimed - not shared
        if f"{resource.proxy.host}:{resource.proxy.port}" in avoid:
            continue
        return resource
    return None


def _new_exit(client: Client, settings: Settings, book: Book, build: Build,
              phone_id: str, current: Resource | None, why: str, budget: float,
              swaps: int = 0, avoid: set[str] | None = None,
              cancelled: Callable[[], bool] | None = None) -> Resource | None:
    """Get the phone onto a different exit address: another proxy.

    There used to be a cheaper branch first - sx.org can hand a proxy a new
    address while keeping its host, port and credentials, so nothing on the
    phone changes. It is gone: only the vendor's `port` product can do that,
    this account holds none, and buying them is not the plan (2026-08-25).

    The phone is stopped before anything: GeeLark's documentation says not to
    call the update while a phone is starting, and Android reads the proxy
    when the network comes up - a phone left running would keep the exit just
    judged.
    """
    log.warning("%s - getting a different exit address", why)
    phones.stop(client, phone_id)

    # Whether the exit below is one this build took or one it is standing on
    # beside another phone. They are settled in opposite ways and were told
    # apart nowhere: see the refusal handler.
    borrowed = False
    try:
        replacement = _fresh_proxy(client, book)
    except Aborted as exc:
        if str(exc) != "no_usable_proxy":
            # The stock was unreachable, not refusing. Reported as it is:
            # "every exit refused" would send the reader looking at OpenAI when
            # the answer is that their proxies are down (2026-08-11, phone 671,
            # which met three dead proxies from an expired batch and was
            # recorded as though the service had judged it).
            raise
        # An empty pool means two different things, and saying the wrong one
        # sends the reader to the wrong place. A build that has already worked
        # through several exits emptied the pool itself - it holds each refused
        # one claimed - and that is a fact about the pool or the service. A
        # build on its first swap emptied nothing: there was simply no free
        # proxy to move to, which is what happens when a run is given as many
        # phones as it has proxies. Phone 762 was told "every exit in the pool
        # was refused in turn" after being refused exactly once (2026-08-16).
        # Nothing free. Rather than stop here, take one that another phone is
        # already on - see _borrow_exit for what that costs.
        replacement = _borrow_exit(book, avoid or set())
        if replacement is None:
            raise Aborted("all_exits_refused" if swaps
                          else "no_exit_to_move_to") from None
        borrowed = True
        build.shared_exit = True
        log.warning("no free proxy left; sharing %s, which %s is already on",
                    replacement.label,
                    (replacement.values.get("Used By") or "another phone"))
    try:
        phones.set_proxy(client, phone_id, replacement.proxy)
    except ApiError as exc:
        # The phone keeps the proxy it had, so nothing is broken - but this
        # build cannot do what it came here to do, and saying "the login
        # failed" would hide that.
        #
        # Only if it was ours. A borrowed exit is one another phone is running
        # on right now - `_borrow_exit` returns it without claiming it - and
        # releasing that blanks its status and wipes the `Used By` naming its
        # real owner. The next build then claims it, putting a third phone on
        # the address and leaving nothing that says whose it was. The path
        # into this is not exotic: the pool empties, a proxy is borrowed, and
        # GeeLark refuses to move onto it - which is what [45004] is, and a
        # borrowed exit is exactly the kind that draws one.
        if not borrowed:
            book.proxies.release(replacement, note=(
                f"Free again - GeeLark would not move a phone onto it: {exc}"))
        raise Aborted("proxy_change_refused") from exc
    # `current` is deliberately NOT released here. Releasing it put it straight
    # back on the shelf as `unused`, where the very next swap could claim it
    # again - so a phone that kept being refused went round the pool instead of
    # through it, for as long as its budget lasted: phone 658 spent 49 minutes
    # alternating request_rejected and network_ssl_rejected across the same
    # proxies (2026-08-11). Holding it claimed for the rest of the build is
    # what makes the loop terminate: each swap costs one proxy, so the pool is
    # the bound. The caller collects these and releases them all at the end.
    build.proxy = str(replacement.proxy)
    build.proxy_name = replacement.name
    time.sleep(5)
    phones.ensure_running(client, phone_id,
                          timeout=min(phones.BOOT_SECONDS, budget),
                          cancelled=cancelled)
    return replacement


def _suspected(book: Book, session: _Session) -> tuple:
    """What becomes of an account a phone stopped on for an APP_SUSPECTS
    reason: a strike, and at SUSPECT_STRIKES different phones, set aside.

    The count lives in the row's own Note - "(strike 2 of 3, last on phone
    1531)" - so it survives restarts, resets itself the moment the account
    is spent or hand-edited, and is readable by the person whose sheet it
    is. A repeat on the SAME phone keeps the count where it was: that phone
    already took the blame once, and burning three tries on one bad phone
    must not condemn a good account (the 1465 lesson).

    Without this, a DEVICE-blamed failure released the account back blank,
    indistinguishable from a row nobody had tried - and on 2026-08-31 the
    one free account in the pool failed that way on four phones in a row,
    burned all their tries and tripped the breaker.
    """
    row = session.app_row
    said = failures.verdict(session.suspect_reason, book.apps.service).seen
    serial = str(session.build.serial or "")
    seen = _STRIKE.search(row.values.get(book.apps.note_column) or "")
    strikes, last = (int(seen.group(1)), seen.group(2)) if seen else (0, "")
    if not seen or serial != last:
        strikes += 1
    if strikes >= SUSPECT_STRIKES:
        return (book.apps, row, SET_ASIDE,
                f"On {failures.today()} {said}. {SUSPECT_STRIKES} different "
                f"phones in a row ended there with this account, so the "
                f"account is the common factor, not the phones. Log into it "
                f"by hand - a payment or subscription nag drawn over the app "
                f"is the known cause - fix what it shows, then blank this "
                f"status to offer it again.", session.suspect_reason)
    return (book.apps, row, RELEASE,
            f"Free again - {said} (strike {strikes} of {SUSPECT_STRIKES}, "
            f"last on phone {serial}). The phone took the blame, but if "
            f"different phones keep ending there this row is set aside.", "")


def _session_holds(book: Book, session: _Session | None, *,
                   proxy_spent: bool) -> list[tuple]:
    """Everything the app phase is still holding, and what each should become.

    One list, because there were two and they drifted. `build` and `finish`
    each assembled their own, and when set_aside was added only `build` learned
    about it - so two app accounts a finish had challenged sat `in_use` with
    nothing left to free them (2026-08-13, rows 12 and 13).

    `proxy_spent` is the one real difference: a build's proxy is spent the
    moment its phone exists, while a finish only holds one if it swapped an
    exit in, and then that phone owns it too.
    """
    if session is None:
        return []
    today = failures.today()
    app: tuple = (book.apps, session.app_row,
                  SPEND if session.app_signed_in else RELEASE, "", "")
    if (session.app_row is not None and not session.app_signed_in
            and session.suspect_reason):
        app = _suspected(book, session)
    held: list[tuple] = [app]
    if session.proxy_row is not None:
        held.append((book.proxies, session.proxy_row,
                     SPEND if proxy_spent else RELEASE, "", ""))
    # Exits a service refused this phone through. Held back rather than freed:
    # the proxy is not condemned - a refusal is per-session, which is measured -
    # but its *address* has just been turned down, and nothing here can change
    # one: the address is the vendor's to rotate, not ours. Freeing it hands
    # the next build the same address to be refused through again.
    held += [(book.proxies, resource, SET_ASIDE,
              f"On {today} {failures.verdict(why).seen}. The proxy is fine; "
              f"the exit address is the thing that was turned down. Change it "
              f"in the vendor's panel, then set this cell to `free`.", why)
             for resource, why in session.refused_exits]
    # Accounts the service asked something of rather than judged. Its own verb,
    # because neither of the other two is true: it was not spent, and releasing
    # it put it back blank - indistinguishable from a row nobody had tried, so
    # the next run picked the same one and met the same challenge. The reason
    # travels too: it becomes the row's status, so the cell says what was
    # asked rather than a word that needs a glossary.
    held += [(book.apps, resource, SET_ASIDE,
              f"On {today} {failures.verdict(why, book.apps.service).seen}. "
              f"The account was "
              f"asked, not judged - nothing is known against it. Fix what it "
              f"was asked for, then blank this status to offer it again.", why)
             for resource, why in session.set_aside]
    return held


def _release(book: Book, build: Build, held: list[tuple]) -> None:
    """Hand every still-claimed resource its outcome.

    `spent` is what the resource ended up on a device as, not whether the build
    as a whole succeeded. A Gmail that signed in is on that phone whatever
    happens afterwards, so a build that then fails its app login must still
    mark it used - releasing it would hand a signed-in account to the next
    phone, which is the one mistake in this file that costs an account rather
    than a minute. The same goes for a proxy the phone was created behind.

    Runs in a finally, so it must not raise: a sheet error here would replace
    the build's real result with a network complaint, and the resources would
    stay claimed either way.
    """
    for pool, resource, action, note, reason in held:
        if resource is None:
            continue
        try:
            if action == SET_ASIDE:
                pool.set_aside(resource, reason=reason, note=note)
            elif action == SPEND:
                pool.spend(resource, serial=build.serial, note=(
                    f"On phone {build.serial}."
                    if build.ok else
                    f"On phone {build.serial}, which stopped short of ready - "
                    f"see that row in the Phones tab."))
            else:
                # Claimed but never put on a device - the Gmail fetched just as
                # the budget ran out, the app account nothing was tried with,
                # the exit that was swapped away from. It is stock, and it goes
                # back as stock.
                pool.release(resource, note=note or (
                    "Free again - a build claimed it but never got as far as "
                    "using it."))
        except Exception as exc:                                  # noqa: BLE001
            # Broad, and the docstring above says why: this runs in a finally,
            # where an exception does not fail the call - it replaces the value
            # the call was about to return. `SheetError` covered the quota and
            # the network, and `batch_write` re-raises every other APIError
            # untouched: a revoked key or a bad range escaped, took the Build
            # with it, and left the resources after this one in the list still
            # claimed with nothing coming back to free them.
            log.error("%s: could not release %s (%s) - it stays in_use until "
                      "'geelark pools --release-stuck'",
                      pool.tab, resource.label, exc)


def outcome_of(build: Build) -> str:
    """Why this phone ended where it did, as one lowercase clause.

    Shared with the console, which lays the same facts out over several lines
    rather than in one sentence. Two renderings of one build used to be two
    descriptions of it: the tab said what happened and the console printed
    `no_usable_gpt`, which is the token this file spent a day removing from
    everywhere else.
    """
    if build.ok:
        return "signed into Google, and into ChatGPT in the app"
    return build.detail or failures.situation(build.status)


def attempts_of(build: Build) -> list[str]:
    """Every credential this build gave up on, one readable line each."""
    return [f"{email} - {failures.verdict(reason, service).seen}"
            for email, reason, service in build.tried]


def _given_up_on(book: Book, serial: str) -> str:
    """The word somebody has written in this phone's State, if any.

    Checked at the few places a build is about to spend real time, because a
    row can be marked while the run that owns it is minutes into its work.
    `unfinished` keeps a marked row out of the queue, but the build already
    under way never learned - so a phone marked `failed` at 20:06 had the app
    installed on it until 20:36, and the sync then deleted it (2026-08-29).

    `taken` is here too: somebody has claimed the phone by hand and this run
    should let go of it rather than drive it.
    """
    if not serial:
        return ""
    state = book.phones.state_of(serial)
    return state if state in (book.phones.DONE, book.phones.FAILED,
                              book.phones.TAKEN) else ""


def _phone_status(build: Build) -> str | None:
    """Which of the three words this build ended on, or None for "cannot say".

    `READY if build.ok else APP_ONLY` said the app was on the device whenever
    a build stopped short - including when the install was the thing that
    failed. The `App` column beside it said `x` at the same time.

    Vague while `app_only` meant "not finished". Actively misleading once it
    named a product: the tab would offer a phone with no app to somebody whose
    whole use for it is opening that app (2026-08-29).

    That fix answered one half. The other half is a run that never looked:
    `else PhoneLog.INCOMPLETE` was reached by a `finish` whose phone would not
    start, and wrote `incomplete` over a row that had truthfully said
    `app_only` for two hours. `app_only` is one of the two things this farm
    sells, and the demoted phone was headed for its third strike and deletion
    (phone 1415, 2026-08-30).

    So None, and `_record` leaves the columns it would have written alone.
    """
    from .pools import PhoneLog

    if build.ok:
        return READY
    if build.app_installed is None:
        return None
    return APP_ONLY if build.app_installed else PhoneLog.INCOMPLETE


def _phone_note(build: Build) -> str:
    """What the Phones tab says about this build, in sentences.

    The Status column already carries the verdict - `ready` or `incomplete` -
    and the Gmail, GPT Account and Proxy columns already carry the what. This
    is the only cell with room to say how it went, so it is written as prose
    for someone reading the row rather than as a trace for someone debugging.

    It used to be neither: `no_usable_gpt. tried: a@b.com: email_code_required.
    the Gpt Info tab has no unused account left`, and for a phone that worked,
    the output of `pm list packages`. The reason tokens are still exact in the
    terminal summary and the logs, which is where you want to grep them.
    """
    opening = (f"Ready - {outcome_of(build)}." if build.ok
               else f"Stopped short: {outcome_of(build)}.")
    # `no_usable_gpt` is not a fault, it is a finished product of the other
    # kind: Google is signed in, the app is on it, and only an account is
    # missing. Read cold, "Stopped short" says the opposite - and this phone is
    # exactly the one somebody takes to sign a customer in by hand. The
    # taxonomy already computes the reassuring half and it was being thrown
    # away here (2026-08-29).
    if not build.ok and build.status == "no_usable_gpt":
        opening += (" The phone itself is finished - signed into Google with "
                    "the app installed - and is ready to take as it is if "
                    "somebody is signing in themselves.")
    if build.shared_exit:
        # Said on the phone's own row, because whoever reads it later is
        # deciding whether these accounts can be treated as unrelated.
        opening += (" The pool had nothing free when an exit refused this "
                    "phone, so it shares one with another - both accounts "
                    "reach the services from the same address.")
    if not build.tried:
        return opening
    # Everything it gave up on before getting here. On a ready phone these are
    # the false starts; on one that stopped short they are the whole story.
    attempts = "; ".join(line.replace(" - ", " (", 1) + ")"
                         for line in attempts_of(build))
    lead = "Also tried" if build.ok else "Tried"
    return f"{opening} {lead}: {attempts}."


def _note_on_row(book: Book, serial: str, **fields: str) -> None:
    """Say on a phone's row what has just become true of it.

    The row was written once, at the end, in a `finally`. Everything a build
    learned on the way - which Gmail signed in, above all - lived only in
    memory until then, so a run that died left a row saying nothing had
    happened.

    `settle_abandoned` reads that row to decide whether a phone a dead run
    left behind is worth finishing or is not a phone at all, and it reads the
    Gmail column to do it. Empty for the whole length of a build meant every
    interruption deleted a phone that was signed in and working: 1315 had
    signed into Google, installed the app and signed into ChatGPT, and was
    deleted by the next sync two minutes after a restart (2026-08-28).

    By serial, never by the row number `start` handed back ten minutes ago: a
    sibling discarding its phone deletes a row, and every row below it moves
    up, so that number can have come to mean a different phone.

    Never fatal. The build is what matters and this is only how it is
    remembered - a sheet that will not take the write costs a line in the log
    and the old behaviour, not the run.
    """
    what = ", ".join(fields)
    try:
        if not book.phones.write(serial, **fields):
            log.warning("phone %s has no row in the Phones tab to note %s on",
                        serial, what)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not note %s on phone %s's row (%s); a run "
                    "interrupted from here would leave the row saying less "
                    "than is true", what, serial, exc)


def _count_try(book: Book, build: Build) -> None:
    """Tally one failed finish, and say so on the row when it is the last one.

    Never raises: it is called from a `finally`, where an exception replaces
    the value the function was about to return.
    """
    try:
        made = book.phones.count_try(build.serial)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not count the attempt on %s (%s)", build.serial, exc)
        return
    limit = book.phones.GIVE_UP_AFTER
    if made >= limit:
        log.warning("phone %s has failed %d finishes; it will not be offered "
                    "again until the %s cell is cleared",
                    build.serial, made, book.phones.TRIES_COLUMN)
        build.detail = (f"{build.detail}. Tried {made} times and set aside - "
                        f"clear the {book.phones.TRIES_COLUMN} cell to offer "
                        f"it again").strip(". ")


def _write_row(book: Book, build: Build, *, drop: bool = False) -> None:
    """Put this build in the Phones tab, and never raise doing it.

    Both callers are in a `finally`, where an exception does not merely fail -
    it replaces the value the function was about to return. A sheet that went
    unreachable mid-run therefore threw away three finished Builds and left the
    summary reporting the same urllib3 error three times, in place of what each
    phone had actually reached (2026-08-17).

    The row can be rebuilt from the log and from History. The outcome, once the
    Build carrying it is gone, cannot.
    """
    try:
        if drop:
            book.phones.drop(build.serial)
        else:
            _record(book, build)
    except Exception as exc:                                      # noqa: BLE001
        log.error("could not write %s to the Phones tab (%s) - the run's own "
                  "summary and the log still have it", build.name, exc)


def _record(book: Book, build: Build) -> None:
    """Write the finished phone to the Phones tab. Also in a finally.

    The three step columns read left to right in the order the steps happen:
    Google, then the app, then the app account. Each says the address that
    signed in where there is one to show, and a cross where the step did not
    happen - so `incomplete` beside three crosses and `incomplete` beside two
    addresses are told apart without reading the note.
    """
    note = _phone_note(build)
    cross = book.phones.NO
    status = _phone_status(build)

    def said(value: str) -> str:
        return value or cross

    # Status and App are claims about the device. A run that never reached it
    # makes neither, and the cells keep what the last run that did look put
    # there. Everything else is about the run itself - which exit it used,
    # what happened - and is true whether or not the phone ever came up.
    device: dict[str, str] = {}
    if status is not None:
        device = {"Status": status,
                  "App": book.phones.YES if build.app_installed else cross}

    try:
        wrote = book.phones.write(
            build.serial,
            Proxy=build.proxy_name or build.proxy,
            Gmail=said(build.gmail), Note=note,
            **{"GPT Account": said(build.app_account)}, **device,
        )
        if not wrote:
            log.error("phone %s has no row in the Phones tab to record on; "
                      "its result is in the summary above and nowhere else",
                      build.serial)
    except SheetError as exc:
        log.error("could not record phone %s (%s)", build.serial, exc)
    # The Phones tab is current state - a row marked done is deleted, and with
    # it every answer to "what did we build on Tuesday". History keeps the
    # outcome, appended, whichever machine produced it.
    # History is appended whatever happened, and `Event` is the one word it
    # gets. Where the run cannot name a phone status it names its own outcome
    # instead - `phone_would_not_start` says more about that row than a
    # guessed `incomplete` ever did, and it is already a `failures` token.
    book.record_history(
        Serial=build.serial, Event=status or build.status,
        Seconds=f"{build.seconds:.0f}", Proxy=build.proxy_name or build.proxy,
        Gmail=build.gmail, Note=note, Steps=build.steps,
        **{"GPT Account": build.app_account,
           "App": book.phones.INSTALLED if build.app_installed else ""})


def possible_statuses() -> list[str]:
    """What the Phones tab's Status column can hold.

    Four, because four is how many the reader acts on differently. It used to
    be twenty-four - every reason a build could stop for - and across every run
    ever made only two of them appeared. The rest were noise in a dropdown, and
    the same detail was already in the Note beside them, in full.

    Two of the four are products - `ready` has an account on it, `app_only` has
    the app and waits for somebody to sign one in - and the reader takes either
    off the shelf. `incomplete` is neither: the Gmail signed in and the app
    never arrived, so there is nothing to open. It is the distinction the
    fourth word exists for, and it was missing while every unfinished build
    said `app_only` whatever the App column read.

    What is lost is nothing: `Status` says whether a phone is usable and how,
    `Note` says why not.
    """
    from .pools import PhoneLog

    return [PhoneLog.BUILDING, READY, APP_ONLY, PhoneLog.INCOMPLETE]


def _this_module():
    import sys
    return sys.modules[__name__]


def _account_on(book: Book, serial: str, named: str) -> Resource | None:
    """The app account this phone is carrying, asking both records.

    The Phones row's `GPT Account` cell is the first answer and the usual
    one. When it is blank the Gpt Info tab is asked whether any row is still
    standing on this phone, because that tab keeps its own serial - and the
    two disagreed once. Phone 1542 was signed in at 23:13 and marked `done`
    three hours later with that cell empty, so the delivery went unrecorded:
    the account was neither delivered nor freed, and sat holding a phone that
    no longer existed while every pass warned about it (2026-09-01).

    Only a row the pool calls spent counts. A `delivered` row has been
    settled already, and a blank one is stock that happens to remember the
    serial it was last on - taking either would be this inventing a delivery
    rather than finding one.
    """
    if named:
        return book.apps.find(named)
    column = book.apps.serial_column
    if not column:
        return None
    wanted = str(serial).strip()
    for resource in book.apps._rows:
        if (resource.values.get(column) or "").strip() != wanted:
            continue
        if book.apps.status_of(resource) == book.apps.spent_status:
            return resource
    return None


def _settle_before_deleting(client: Client, phone_id: str, serial: str,
                            timeout: float = 90) -> bool:
    """Stop a phone and wait for it to say so. False if it will not.

    GeeLark will not delete a running phone, and `stop` only posts the request -
    the phone goes on reporting as running while it shuts down. Deleting into
    that window fails, so this waits for the state to settle rather than
    guessing at a sleep.

    A phone that will not stop keeps its row: the next sync finds it again, and
    a row still there is a better outcome than a delete that half worked.
    """
    log.info("phone %s is marked done and still running; stopping it so it "
             "can be deleted", serial)
    try:
        phones.stop(client, phone_id)
    except Exception as exc:                                      # noqa: BLE001
        log.error("could not stop phone %s (%s); its row is kept", serial, exc)
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if phones.status(client, phone_id) == phones.STOPPED:
                return True
        except Exception as exc:                                  # noqa: BLE001
            log.error("phone %s stopped answering (%s)", serial, exc)
            return False
        time.sleep(5)
    log.warning("phone %s has not reported stopped within %.0fs; its row is "
                "kept for the next run", serial, timeout)
    return False


def apply_phone_states(client: Client, book: Book,
                       ledger: Ledger) -> dict[str, list[str]]:
    """Carry out what the operator wrote in the Phones tab's `State` column.

    `Status` is what a run concluded about a phone. `State` is the other
    direction - an instruction back to the tool, written by hand between runs:

        done     finished with it. Delete the phone and drop the row.
        failed   something is wrong with it. Free its app account so a new
                 phone can use it, then delete the phone and drop the row.
        unused   the default. Leave it alone.

    A running phone is never touched, only reported. Deleting one is not a
    documented way to end its billing, and stopping it to make deletion safe is
    not this function's business.

    What happens to the credentials the phone carried follows from which of the
    two it was:

        Gmail        retired as `used` either way. It signed into that phone,
                     and that is the credit it had to spend.
        app account  `delivered` after `done` - the phone was the product and
                     it went out on it. Freed after `failed` - it never got a
                     fair device, so the next build can put it on one.

    Neither is ever left pointing at the deleted phone. A stale serial is how
    thirteen proxies sat out of the pool for days without anyone noticing.
    """
    marked = book.phones.marked()
    if not marked:
        return {}

    alive = {str(p.get("serialNo")): p for p in phones.listing(client)}
    outcome: dict[str, list[str]] = {"deleted": [], "freed": [],
                                     "delivered": [], "retired": [],
                                     "held": [], "running": []}
    finished_rows: list[int] = []

    for row in marked:
        serial = row["serial"]
        present = alive.get(str(serial))
        held = present and ledger.get(present["id"])
        if held is not None and held.is_claimed and not held.is_stale:
            # A run is working on it. That is the only reason to refuse: the
            # power state is not, because a phone left up by a browser tab is
            # nobody's, and `done` on it still means delete.
            outcome["held"].append(serial)
            continue
        if present and present.get("status") in (phones.RUNNING, phones.STARTING):
            # Stopped first, because a running phone cannot be deleted - and
            # this used to stop there and report it, which left `done` half
            # carried out and the row sitting in the tab until someone noticed,
            # closed the viewer and ran the sync again (2026-08-16, 749 and 751).
            if not _settle_before_deleting(client, present["id"], serial):
                outcome["running"].append(serial)
                continue

        failed = row["state"] == book.phones.FAILED

        # The Gmail is spent either way. It signed into that phone without
        # complaint, and whatever became of the phone afterwards, the address
        # has been on one - so it retires rather than going back on the shelf.
        if row["gmail"]:
            address = book.gmails.find(row["gmail"])
            if address is not None:
                book.gmails.retire(
                    address, note=f"Signed into phone {serial}, which was "
                                  f"marked {row['state']} and deleted. Kept "
                                  f"out of the pool from now on.")
                outcome["retired"].append(row["gmail"])

        account = _account_on(book, serial, row["app_account"])
        carried = account.label if account is not None else ""
        if account is not None:
            if failed:
                # It never got a fair phone. Back to the pool, so the next
                # build can put it on one that works.
                book.apps.release(
                    account, note=f"Phone {serial} was marked failed and "
                                  f"deleted before this account got a fair "
                                  f"run. Free to try on another phone.")
                outcome["freed"].append(carried)
            else:
                # `done` means the phone was the product and it has been
                # handed over. The account went with it.
                book.apps.retire(
                    account, note=f"Delivered on phone {serial}, which was marked "
                                  f"done and handed over.")
                outcome["delivered"].append(carried)

        if present:
            try:
                phones.delete(client, [present["id"]], ledger=ledger)
                outcome["deleted"].append(serial)
            except Exception as exc:                              # noqa: BLE001
                log.error("could not delete phone %s (%s); its row is kept",
                          serial, exc)
                continue
        finished_rows.append(row["sheet_row"])
        # Three notes, not two. The pair branched on `failed` alone and said
        # "its app account was delivered with it" whichever way - including for
        # a phone that never had one, which is a whole product: the app is
        # installed and somebody signs a customer's own account in by hand.
        # History is the only durable record once the row is deleted, so it was
        # the one place that claimed a farm account went out with every such
        # hand-over (2026-08-29).
        if failed:
            note = ("Marked failed and deleted; its app account went back to "
                    "the pool for another phone.")
        elif carried:
            note = "Marked done and deleted; its app account was delivered with it."
        else:
            note = ("Marked done and deleted. No app account was ever on it - "
                    "the app was installed and whoever took it signs in "
                    "themselves.")
        book.record_history(
            Serial=serial, Event=row["state"], Gmail=row["gmail"], Note=note,
            **{"GPT Account": carried})

    # Only now, and bottom up: the row numbers were read before any moved.
    book.phones.delete_rows(finished_rows)
    for label, items in outcome.items():
        if items:
            log.info("%s: %s", label, ", ".join(items))
    return outcome


#: What each step is doing, for a caller that shows progress while it waits.
#: The whole sync takes half a minute or more and used to be one unchanging
#: line with every INFO record the steps emit scrolling through it.
STEP_NAMES = {
    "marks": "carrying out the State column",
    "abandoned": "settling phones a killed run left behind",
    "proxies": "matching the Proxy tab to the panel",
    "repointed": "checking which exit each phone is really on",
    "renamed": "naming the phones in GeeLark",
    "stranded": "looking for phones and accounts that lost each other",
    "unclaimed": "putting back what a dead run was holding",
    "pruned": "clearing out archived pages nothing needs",
    "checked": "testing every free proxy",
}


def sync_sheet(client: Client, book: Book, ledger: Ledger, *,
               apply_marks: bool = True,
               probe_proxies: bool = True,
               on_step: Callable[[str], None] | None = None,
               artifact_dir: Path | None = None,
               stale_claim_seconds: float | None = None,
               ) -> dict[str, list[str]]:
    """Bring all four tabs back into agreement with the world. Every run.

    The pieces existed and were called in a different combination from each of
    three places - `build` did three of them, `finish` did two, the console did
    one - so what a tab said depended on which door you came in by. This is the
    one door.

    The order is not arrangeable:

    1. **Gmails, then Gpt Info, then Phones.** Acting on the State column
       deletes phones, and the credentials a phone carried are named on its
       row - so they have to be settled while the row still exists. A Gmail is
       retired either way, since it signed into that phone whatever became of
       it. An app account is `delivered` if the phone was marked done and freed
       if it was marked failed, because a failed phone never gave it a fair
       device. That is `apply_phone_states`, and its internal order is this.
    2. **Reload.** The rows moved.
    3. **Proxy.** After the deletions, so the exits those phones held are seen
       to be free rather than freed a run later.
    4. **Test what is free.** Last, because steps 1-3 are what decide which
       proxies are free to test.

    Two switches, for the two halves that are not alike:

    `apply_marks` is the half that deletes phones. It is the only irreversible
    thing here, so a caller has to ask for it. `geelark pools` does not - it is
    a report, and a report that deletes six phones because a column said so is
    not one. A run does, and the console does after showing what it will do.

    `probe_proxies` is the part that costs real time - a live connection per
    free proxy - so a caller that only wants the tabs tidied can leave it out.
    Named for the switch rather than the function it turns on, because those
    were the same word for one commit: the parameter shadowed `check_proxies`
    inside this body, and calling it raised `'bool' object is not callable` on
    the first line of every console session (2026-08-14).
    """
    # The dropdowns come from the same table the build consults, so they cannot
    # be right by accident and cannot stay right on their own: a flow grew
    # `wrong_2fa_code` and the Gpt Info column went on refusing it - "Input
    # must fall within specified range" against a status a run had just
    # written (2026-08-16). Regenerated every session, and it writes only when
    # something actually moved.
    try:
        book.sync_lists()
    except SheetError as exc:
        log.warning("could not refresh the Status dropdowns: %s", exc)

    # Each step guarded on its own. A sheet error partway through - the write
    # quota exhausted by a big sync is the one seen in practice - must not
    # discard the steps that already ran: by the time the proxy check writes,
    # the phones are already deleted and the credentials already settled, and
    # unwinding out of the whole sync would leave the console unable to open
    # while reporting none of what it did (2026-08-17).
    def step(name: str, work) -> None:
        if on_step:
            on_step(STEP_NAMES.get(name, name))
        try:
            result = work()
            if isinstance(result, dict):
                outcome.update(result)
            elif result is not None:
                outcome[name] = result
        except Exception as exc:                                  # noqa: BLE001
            # Every step here also talks to GeeLark, and `ApiError`,
            # `TransportError` and `PhoneError` are none of them a
            # `SheetError`. Catching only that left a GeeLark hiccup partway
            # through unwinding the whole sync - the console unable to open,
            # and reporting none of the work that had already been done, which
            # is the exact outcome this guard was added to prevent.
            log.error("sync step %r stopped short: %s", name, exc)
            outcome.setdefault("incomplete", []).append(name)

    outcome: dict[str, list[str]] = {}
    if apply_marks:
        step("marks", lambda: apply_phone_states(client, book, ledger))
    # Before the reload, because both read the Phones tab and this one is what
    # frees a row the last run died holding.
    step("abandoned", lambda: settle_abandoned(client, book, ledger))
    book.reload()
    step("proxies", lambda: sync_proxies(client, book, ledger))
    step("repointed", lambda: sync_phone_proxies(client, book))
    step("renamed", lambda: sync_phone_names(client, book))
    step("stranded", lambda: strand_check(client, book))
    if stale_claim_seconds:
        step("unclaimed", lambda: free_abandoned_claims(book,
                                                        stale_claim_seconds))
    if artifact_dir is not None:
        step("pruned", lambda: archive.prune(
            artifact_dir,
            {str(item.get("serialNo") or "")
             for item in phones.listing(client)}))
    if probe_proxies:
        def check():
            gone, back = check_proxies(client, book)
            return {"dead": [r.label for r in gone],
                    "revived": [r.label for r in back]}
        step("checked", check)
    return {key: items for key, items in outcome.items() if items}


def _live_exits(client: Client) -> dict[str, list[dict]]:
    """What GeeLark says is behind each exit: `host:port` -> the phones on it.

    The only authority on this. The Proxy tab records what a run believed when
    it wrote the row, and the two come apart every time a phone is deleted from
    the panel or moved onto another exit mid-run.

    A list rather than one phone, because an exit can carry more than one since
    a build ran dry and borrowed - and keeping the last one seen would have the
    sync quietly rewrite the tab to name whichever came back second.
    """
    found: dict[str, list[dict]] = {}
    for phone in phones.listing(client):
        config = phone.get("proxy") or {}
        if config.get("server"):
            found.setdefault(f"{config['server']}:{config.get('port')}",
                             []).append(phone)
    return found


def settle_abandoned(client: Client, book: Book,
                    ledger: Ledger) -> dict[str, list[str]]:
    """Close out rows a run was holding when it died.

    `building` means "a run has this right now", which is why every other
    reader skips it - and nothing ever un-set it. A run killed mid-build leaves
    the row saying `building` forever: `unfinished` will not offer it to a
    finish, `marked` only sees the State column, and the phone sits in the
    panel behind a row nobody acts on (2026-08-14, phone 750, left there when a
    stuck boot was interrupted).

    Two things protect a run that is still working, and one of them was not
    enough. The phone being up is the obvious signal - and a phone stuck in
    `starting` reports as `stopped`, so a build patiently waiting for one to
    boot looked exactly like a dead run. This deleted phone 750 out from under
    a live build, which then failed with `env not found` twenty minutes later
    (2026-08-14). The ledger is the other: `claim` is written the moment a
    build takes a phone and cleared when it lets go, so an unreleased claim
    means a process believes it owns this.

    The ledger is the one that answers the question. Being up was treated as
    an answer too, and it is not: a run that lost its network died without
    stopping its phones, so they stayed running with nothing accountable for
    them - and a running phone is settled by nothing, offered to `finish` by
    nothing and deleted by nothing, so the rows sat on `building` for good.
    One that nothing claims is stopped here and then settled like any other.

    What the row becomes follows the rule a build would have applied itself: a
    phone with a Gmail on it is `incomplete` and can be finished; one with
    nothing signed in is deleted, because that is not a phone.
    """
    live = {str(p.get("serialNo")): p for p in phones.listing(client)}
    outcome: dict[str, list[str]] = {"abandoned": [], "discarded": []}
    dropped: list[int] = []

    for row in book.phones.rows():
        if (row.get("Status") or "").strip() != book.phones.BUILDING:
            continue
        serial = row.get("Serial") or ""
        present = live.get(str(serial))
        held = present and ledger.get(present["id"])
        if held is not None and held.is_claimed and not held.is_stale:
            log.info("a run still claims phone %s (%s); leaving it alone",
                     serial, held.label)
            continue

        if present and present.get("status") in (phones.RUNNING,
                                                 phones.STARTING):
            # Running, and nothing is accountable for it. That used to be an
            # unconditional skip, which read as "someone is working on it" -
            # but the ledger above is what answers that, and it has already
            # said no. A run that died without its network never got to stop
            # its phones, so they stayed up; and a phone that is up is settled
            # by nothing, offered by nothing, and deleted by nothing, so its
            # row sat on `building` for good (2026-08-17, phones 838 and 839).
            #
            # Stopping is not housekeeping about cost here - it is the only
            # way the row can be settled at all, since GeeLark will not delete
            # a phone that is still running.
            log.info("phone %s is still running with nothing accountable for "
                     "it; stopping it so its row can be settled", serial)
            try:
                phones.stop(client, present["id"])
                phones.wait_until_stopped(client, present["id"])
            except Exception as exc:                              # noqa: BLE001
                log.error("could not stop abandoned phone %s (%s); its row "
                          "stays `building` until it comes down", serial, exc)
                continue

        # What History is told, either way. Seconds is deliberately left out:
        # the run that did the work died without reporting, and this sync has
        # no duration to offer - a nought there would read as "took no time"
        # rather than "nobody knows". Everything else is on the row.
        recorded = {"Serial": str(serial),
                    "Proxy": (row.get("Proxy") or "").strip(),
                    "Gmail": (row.get("Gmail") or "").strip(),
                    "GPT Account": (row.get("GPT Account") or "").strip()}

        if row.get("Gmail"):
            note = ("Stopped short: the run holding this phone ended before "
                    "it could say why. Google is signed in, so finishing it "
                    "costs only an app account.")
            book.phones.finish(row["sheet_row"], Status=APP_ONLY, Note=note)
            outcome["abandoned"].append(str(serial))
            # This wrote nothing to History at all, so a phone rescued from a
            # killed run left no trace of having been rescued - the tab said
            # `incomplete` and the record of how it got there was missing
            # (2026-08-20).
            book.record_history(Event=APP_ONLY, Note=note, **recorded)
            continue

        # Nothing was ever signed into it - the same rule build_one applies.
        if present:
            try:
                phones.delete(client, [present["id"]], ledger=ledger)
            except Exception as exc:                              # noqa: BLE001
                log.error("phone %s was abandoned with nothing on it and "
                          "could not be deleted (%s)", serial, exc)
                continue
        dropped.append(row["sheet_row"])
        outcome["discarded"].append(str(serial))
        book.record_history(
            Event="discarded",
            Note="A killed run left it mid-build with nothing signed in; "
                 "deleted at the next sync.", **recorded)

    book.phones.delete_rows(dropped)
    for label, items in outcome.items():
        if items:
            log.info("%s: %s", label, ", ".join(items))
    return outcome


def sync_proxies(client: Client, book: Book,
                 ledger: Ledger | None = None) -> dict[str, list[str]]:
    """Make the Proxy tab say what is actually behind each exit.

    Three ways the tab drifts, and it only ever fixed one of them:

    - a phone is deleted from the panel and its proxy stays `on a phone`
      forever. Thirteen of twenty-two were locked to phones that had been gone
      for days, and a run failed with no_usable_proxy while they sat there
      (2026-08-11). This is what `reclaim` was written for.
    - a phone is moved onto another exit mid-run and the old row keeps its
      serial, so one phone holds two proxies (2026-08-13, SX5 and SX18).
    - a row says `free`, or `dead`, with a live phone behind it. Nothing ever
      corrected that direction at all, and the second one is a contradiction:
      the phone is the side of it that demonstrably works.

    `claimed` rows are left alone. That word means a run holds it right now,
    and a second run tidying it away is how two phones end up on one exit.
    """
    live = _live_exits(client)
    changed: dict[str, list[str]] = {"attached": [], "released": [],
                                     "unlisted": []}

    # What GeeLark has been given but the tab has never heard of. Reported, not
    # added: the last time this was asked, twelve of the twenty-three unknown
    # ones were expired sx.org proxies and ten were a second vendor's - so
    # adding them would have filled the pool with rows a build then has to
    # discover are dead. Which of them belong here is the operator's call, and
    # this is what tells them there is one to make.
    known = {f"{r.proxy.host}:{r.proxy.port}:{r.proxy.username}"
             for r in book.proxies._rows if r.proxy}
    try:
        # Every page. It asked for the first and stopped, so past a hundred
        # proxies the rest simply did not exist here and this report - which
        # is the only thing that says GeeLark holds an exit the tab has never
        # heard of - silently stopped mentioning them. The same cap that was
        # fixed in `phones.listing`, in the other place it was written.
        held = []
        for page in range(1, phones.MAX_PAGES + 1):
            batch = (client.data("/v1/proxy/list",
                                 {"page": page, "pageSize": 100})
                     or {}).get("list") or []
            held += batch
            if len(batch) < 100:
                break
    except (ApiError, TransportError) as exc:
        # Never worth failing a sync over - but `held = []` makes every proxy
        # look like one the tab already knows, so the report says there is
        # nothing unlisted when what happened is that it could not look.
        log.warning("could not list GeeLark's own proxies (%s), so nothing is "
                    "reported as unlisted this run", exc)
        held = []
    unlisted = [item for item in held
                if f"{item['server']}:{item['port']}:{item.get('username', '')}"
                not in known]
    changed["unlisted"] = [
        f"{item['server']}:{item['port']} ({item.get('username', '')})"
        for item in unlisted]
    # The raw items too, on the Book for the pass to keep in the store: the
    # web's Proxy Pool page offers "add it to the pool" off this list, and
    # that needs the credentials, not the report's one-line spelling.
    book.unlisted_proxies = [
        {"host": str(item.get("server") or ""),
         "port": str(item.get("port") or ""),
         "username": str(item.get("username") or ""),
         "password": str(item.get("password") or "")}
        for item in unlisted]

    for resource in book.proxies._rows:
        if resource.error or not resource.proxy:
            continue
        status = book.proxies.status_of(resource)
        behind = live.get(f"{resource.proxy.host}:{resource.proxy.port}") or []
        if status == book.proxies.claimed_status:
            # `claimed` means a run is holding it, and this used to stop there
            # because the power state cannot tell a live run from a dead one.
            # The ledger can. A phone sitting on this exit whose claim was
            # released is a build that finished and never wrote the row back,
            # and SX16 and SX17 sat like that through a whole run - claimed,
            # with the note from the release before it, and a ready phone on
            # each (2026-08-16).
            #
            # With nothing behind it there is no phone to ask about, and a run
            # between its claim and its create looks the same, so those are
            # left for `--release-stuck` rather than guessed at.
            if not behind or ledger is None:
                continue
            if any((held := ledger.get(p["id"])) is not None
                   and held.is_claimed and not held.is_stale for p in behind):
                continue
        if behind:
            serial = ", ".join(sorted(
                str(p.get("serialNo") or p.get("id") or "") for p in behind))
            already = (resource.values.get(book.proxies.serial_column) or "")
            if status != book.proxies.spent_status or already.strip() != serial:
                book.proxies.attach(resource, serial)
                changed["attached"].append(f"{resource.label} -> phone {serial}")
        elif status == book.proxies.spent_status:
            book.proxies.release(resource, note=(
                "Free again - no phone is behind this exit any more."))
            changed["released"].append(resource.label)

    for label, items in changed.items():
        if items:
            log.info("proxies %s: %d", label, len(items))
    return changed


def sync_phone_proxies(client: Client, book: Book) -> list[str]:
    """Correct the Phones tab's Proxy column from the phone itself.

    A phone that swapped exits mid-run has its row rewritten by the build that
    moved it - but only if that build got as far as recording. One that was
    repointed by hand in the panel, or by a run that died, keeps the string it
    was created with, and that cell is what someone reads to answer "which
    exit is this phone on".
    """
    live = {str(phone.get("serialNo")): phone
            for phone in phones.listing(client)}
    # host:port -> what the Proxy tab calls it, so the correction is written in
    # the same words a build would have written.
    named = {f"{r.proxy.host}:{r.proxy.port}": (r.name or str(r.proxy))
             for r in book.proxies._rows if r.proxy}
    corrected = []
    for row in book.phones.rows():
        phone = live.get(str(row.get("Serial")))
        if phone is None:
            continue
        config = phone.get("proxy") or {}
        if not config.get("server"):
            continue
        actual = named.get(f"{config['server']}:{config.get('port')}",
                           f"{config['server']}:{config.get('port')}")
        if (row.get("Proxy") or "").strip() != actual:
            book.phones.finish(row["sheet_row"], Proxy=actual)
            corrected.append(f"phone {row.get('Serial')}")
    if corrected:
        log.info("corrected the exit recorded for %d phone(s)", len(corrected))
    return corrected


def sync_phone_names(client: Client, book: Book) -> list[str]:
    """Give every phone in GeeLark the name its serial and address say.

    The panel used to list `farm-1786928959` nine rows deep, differing in the
    last digits of a unix timestamp - the second the phone was made, which is
    the one fact nobody ever needs. Phones are named properly at creation now;
    this is for the ones made before that, for any renamed by hand, and for a
    phone whose Gmail was still blank in the tab when it was created.

    A running phone is left alone. GeeLark's own note about `/phone/detail/
    update` is that it must not be called against a phone that is coming up,
    and a tidier list is not worth reaching into a build that is under way -
    the next sync catches it once it has stopped.
    """
    named = {str(row.get("Serial") or "").strip(): (row.get("Gmail") or "").strip()
             for row in book.phones.rows()}
    renamed = []
    for phone in phones.listing(client):
        if phone.get("status") in (phones.RUNNING, phones.STARTING):
            continue
        serial = str(phone.get("serialNo") or "").strip()
        wanted = phones.display_name(serial, named.get(serial, ""))
        if not wanted or wanted == (phone.get("serialName") or "").strip():
            continue
        try:
            phones.rename(client, phone["id"], wanted)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not rename phone %s (%s)", serial, exc)
            continue
        renamed.append(wanted)
    if renamed:
        log.info("renamed %d phone(s) in GeeLark", len(renamed))
    return renamed


def free_abandoned_claims(book: Book, older_than: float) -> list[str]:
    """Put back every credential a dead run left claimed.

    The manual release exists because the tool could not tell a run that died
    holding a row from one using it right now - and handing the same Gmail to
    two phones is worse than leaving one out of the pool. So it reported them
    and waited for a hand on the console, which meant three Gmails and three
    exits sat out for a day, twice in three days.

    A claim time settles it, the same way the ledger's does for phones - and
    a live run keeps its own stamps moving, so a stamp that has stopped is
    proof the run that wrote it is gone. `older_than` is how long a claim may
    go unrefreshed; anything newer is left alone, and the console still offers
    to release those by hand.
    """
    freed = []
    for pool in (book.gmails, book.proxies, book.apps):
        for resource in pool.abandoned(older_than):
            pool.release(resource, note=(
                f"Claimed and never released. A run refreshes what it is "
                f"holding every {Pool.HEARTBEAT_SECONDS}s, and nothing "
                f"refreshed this for {older_than / 60:.0f} minutes, so the "
                f"run that took it is gone. Freed automatically on "
                f"{failures.today()}."))
            freed.append(f"{pool.tab}: {resource.label}")
    if freed:
        log.info("freed %d row(s) a dead run left claimed", len(freed))
    return freed


def strand_check(client: Client, book: Book) -> dict[str, list[str]]:
    """Two ways the sheet and the panel come apart, both of which cost stock.

    **A phone GeeLark has that the tab has never heard of.** Every settling
    path here reads the Phones tab and acts on rows, so a phone with no row is
    touched by nothing: not the State column, not the abandoned sweep, not the
    renaming. Phone 964 sat running for a day that way after an older version
    recorded it as discarded when the delete had actually been refused - the
    row went, the phone did not (2026-08-20). Reported rather than deleted,
    for the same reason an unlisted proxy is: which of them belong here is the
    operator's call, and a report that deletes phones is not a report.

    **A credential still held against a phone that is gone.** `reclaim_proxies`
    has done this for exits since a stale serial held thirteen of them out of
    the pool for days; nothing did it for credentials, so two app accounts and
    a Gmail sat `ready` against phones deleted days earlier, out of the pool
    and waiting for nobody.

    A Gmail is retired outright, because the rule about it is not in doubt: it
    signed into a phone, and that is the credit it had to spend, whatever
    became of the phone. An app account is only reported - `delivered` and
    `freed` are a judgement about whether it ever got a fair device, and
    guessing wrong either retires an account that was never used or frees one
    that is with a customer.
    """
    listing = phones.listing(client)
    alive = {str(p.get("serialNo") or "") for p in listing}
    known = {str(row.get("Serial") or "").strip() for row in book.phones.rows()}
    outcome: dict[str, list[str]] = {}

    unknown = sorted(s for s in alive if s and s not in known)
    if unknown:
        outcome["unknown_phones"] = unknown
        # Which of them are running, separately, because the two are different
        # problems with different answers. A running one bills by the minute
        # and `Stop unaccounted phones` deals with it. A stopped one costs
        # nothing per minute but still holds a profile slot - and slots are
        # what bound the warm stock - and the only answer to that is a person
        # deleting it in the panel. Reported as one thing, the urgent half was
        # unanswerable and the whole line became a warning nobody could ever
        # clear (2026-08-29).
        running = {str(p.get("serialNo") or "") for p in listing
                   if p.get("status") in (phones.RUNNING, phones.STARTING)}
        billing = sorted(s for s in unknown if s in running)
        if billing:
            outcome["unknown_running"] = billing
        log.warning("%d phone(s) exist that the Phones tab has never heard "
                    "of: %s%s", len(unknown), ", ".join(unknown),
                    f" ({len(billing)} running)" if billing else "")

    retired, waiting = [], []
    for pool, held in ((book.gmails, retired), (book.apps, waiting)):
        for resource in pool._rows:
            serial = (resource.values.get(pool.serial_column) or "").strip()
            if not serial or serial in alive:
                continue
            if pool.status_of(resource) != pool.spent_status:
                continue                  # already settled, or never handed out
            held.append(f"{resource.label} (was on phone {serial})")
            if pool is book.gmails:
                pool.retire(resource, note=(
                    f"Phone {serial} no longer exists. An address that has "
                    f"signed into a phone is spent whatever became of it, so "
                    f"this retires rather than going back on the shelf."))
    if retired:
        outcome["stranded_retired"] = retired
    if waiting:
        outcome["stranded_waiting"] = waiting
        log.warning("%d app account(s) are held against a phone that is gone: "
                    "%s", len(waiting), ", ".join(waiting))
    return outcome


def reclaim_proxies(client: Client, book: Book) -> list[Resource]:
    """Put back every proxy whose phone has been deleted.

    Run before a batch for the same reason `prune_ledger` is: the sheet records
    what was true when it was written, and phones get deleted from the panel
    without telling it. Left alone, each deletion permanently removes a working
    proxy from the pool.
    """
    in_use = set()
    for phone in phones.listing(client):
        config = phone.get("proxy") or {}
        if config.get("server"):
            in_use.add(f"{config['server']}:{config.get('port')}")
    freed = book.proxies.reclaim(in_use)
    if freed:
        log.info("freed %d proxy(s) whose phone no longer exists", len(freed))
    return freed


def check_proxies(client: Client, book: Book) -> tuple[list[Resource],
                                                       list[Resource]]:
    """Test the proxies a run could take, and correct the tab both ways.

    A dead proxy used to be discovered by claiming it: the build spent an
    attempt, marked it, and took the next one. That is fine for one, and it was
    a whole purchase batch that expired overnight - eight of them - so a run
    began against a pool a third of which no longer answered, and the count the
    operator had just been shown was fiction (2026-08-11).

    `dead` is tested too, and that is the half this was missing. These proxies
    are rented and renewed on the same address, so one that stopped answering
    yesterday is often answering again today - and nothing ever looked, so a
    renewed proxy stayed out of the pool until someone noticed and blanked the
    cell by hand. The check costs one call either way; the only difference is
    whether the answer can put a row back.

    A proxy already behind a phone is not tested: it is not a candidate for
    this run, and the call would learn something that changes nothing. Checked
    in parallel because they are independent and each takes a few seconds; the
    rate limiter in api.py keeps the burst honest.

    Returns (newly dead, revived).
    """
    buried = [r for r in book.proxies._rows if not r.error and r.proxy
              and book.proxies.status_of(r) == book.proxies.dead_status]
    free = book.proxies.available + buried
    if not free:
        return [], []

    def test(resource: Resource) -> tuple[Resource, str | None, str]:
        try:
            result = proxy_mod.check(client, resource.proxy)
        except (proxy_mod.ProxyError, ApiError) as exc:
            return resource, None, str(exc)[:200]
        return resource, str(result.get("outboundIP") or ""), ""

    dead, revived = [], []
    was_dead = {id(r) for r in buried}
    with ThreadPoolExecutor(max_workers=min(8, len(free)),
                            thread_name_prefix="proxy-check") as pool:
        for resource, exit_ip, error in pool.map(test, free):
            if exit_ip is None:
                if id(resource) in was_dead:
                    continue                      # still dead, still says so
                # The name, not the label, for the reason given where the
                # build reports the same thing: the error already carries the
                # address, and the label carries it again.
                log.warning("proxy %s is dead: %s",
                            resource.name or resource.label, error)
                book.proxies.fail(resource, book.proxies.dead_status, note=(
                    f"Did not answer when the pool was checked: {error}"))
                dead.append(resource)
            elif id(resource) in was_dead:
                log.info("proxy %s answers again; back in the pool",
                         resource.label)
                book.proxies.release(resource, note=(
                    f"Answering again as of {failures.today()}, so it is back "
                    f"in the pool. It had been marked dead."))
                book.proxies.record_exit(resource, exit_ip)
                revived.append(resource)
            else:
                book.proxies.record_exit(resource, exit_ip)
    if dead:
        log.info("%d proxy(s) had died since the last run", len(dead))
    if revived:
        log.info("%d proxy(s) marked dead are answering again", len(revived))
    return dead, revived


def _unfinished(client: Client, book: Book) -> tuple[list[dict], list[dict]]:
    """Phones one step short, split into those that still exist and those that
    do not. GeeLark's own listing is what says which."""
    pending = book.phones.unfinished()
    # Resolved here rather than stored in the tab. The id is a machine's
    # handle - twenty digits nobody reads - and the serial is what the panel,
    # the notes and the operator all call the phone by, so the sheet keeps the
    # serial and this turns it into an id at the one moment anything needs one.
    by_serial = {str(p.get("serialNo")): p.get("id")
                 for p in phones.listing(client)}
    waiting, gone = [], []
    for row in pending:
        phone_id = by_serial.get(str(row["serial"]))
        (gone if phone_id is None else waiting).append({**row,
                                                       "phone_id": phone_id})
    return waiting, gone


def _run_jobs(client: Client, settings: Settings, book: Book,
              jobs: list[dict], *, workers: int | None,
              reporter: Reporter | None,
              on_ready: Callable[[str], None] | None,
              cancel: threading.Event | None,
              ledger: Ledger | None = None,
              codes_source: codes.CodeSource | None = None) -> list[Build]:
    """Run a mixed list of build and finish jobs, up to `workers` at a time.

    One runner for both, because they are the same thing to everyone watching:
    a phone being worked on, one line in the table, one row in the tab. Only
    the first steps differ.
    """
    settings.ensure_dirs()
    # The caller's, when it has one. A Ledger rewrites the whole file from its
    # own dict, so two of them in a process erase each other's phones - and a
    # phone missing from the ledger is one `reap` calls an orphan and stops
    # (2026-08-29).
    ledger = ledger if ledger is not None else Ledger.load(settings.state_dir,
                        stale_after=settings.stale_claim_seconds)
    phones.prune_ledger(client, ledger)
    run_id = _next_run_id()
    run_token = _run.set(run_id)

    total = len(jobs)
    started: set[str] = set()
    started_lock = threading.Lock()
    shutting_down = cancel if cancel is not None else threading.Event()

    def note_phone(phone_id: str) -> None:
        with started_lock:
            started.add(phone_id)

    def work(index: int, job: dict) -> Build:
        """One job, with this thread's log lines labelled while it runs.

        Reset when the job ends rather than left behind. With one worker
        `work` is called on the caller's own thread, so a build that finished
        an hour ago went on labelling every line after it - and `serve` is a
        process that does not end, so "an hour ago" becomes "for ever"
        (2026-08-27). A pool thread is reused, so the same is true of every
        worker; contextvars do not fix that, the reset does.

        Both ids are set here, on the worker thread, and the run id as well as
        the build one. `ThreadPoolExecutor.submit` does not copy the caller's
        context, so the run id set in `_run_jobs` - which runs on the batch's
        own thread - is invisible inside this one unless it is set again.
        """
        job_run = _run.set(run_id)
        job_build = _build.set(index)
        try:
            return _run_job(index, job)
        finally:
            _build.reset(job_build)
            _run.reset(job_run)

    def _run_job(index: int, job: dict) -> Build:
        if reporter:
            # A finish job knows which phone it is before it touches it. A
            # build does not - it has no serial until GeeLark answers with one
            # - and the console used to learn every serial the same way, from
            # the creation log line, so the three rows finishing existing
            # phones sat there unnamed while their live links read "#1" with
            # no phone in them (2026-08-17).
            known = job["phone"] if job["kind"] == "finish" else {}
            reporter.start(index, total,
                           serial=str(known.get("serial") or ""),
                           gmail=str(known.get("gmail") or ""))
        elif job["kind"] == "finish":
            print(f"\n=== finishing phone {job['phone']['serial']} "
                  f"({index}/{total}) ===", flush=True)
        else:
            print(f"\n=== building phone {index}/{total} ===", flush=True)

        if job["kind"] == "finish":
            build = finish_one(client, settings, book, ledger, job["phone"],
                               index, on_phone=note_phone,
                               cancelled=shutting_down.is_set,
                               codes_source=codes_source)
        else:
            build = build_one(client, settings, book, ledger, index,
                              on_phone=note_phone, on_ready=on_ready,
                              cancelled=shutting_down.is_set,
                              codes_source=codes_source)
        # Nothing else in the archive says how the build went: a
        # success's pages and a failure's look alike from outside, and which
        # it was decides how long they are worth keeping.
        if build.artifact_dir:
            archive.record(Path(build.artifact_dir),
                           ok=build.ok, status=build.status)
        # The one line where the reason token and the duration appear
        # together, and it went to stdout alone - so it reached `docker logs`,
        # which is capped and does not survive a rebuild, and never the log
        # file, which is bind-mounted and already JSON on the server. Counting
        # failures by reason meant reading the container's memory before it
        # rolled (2026-08-30). `extra` lands each field beside the message in
        # the JSON line - see logs.JsonLines - so `jq` can group by them.
        log.info("%s %s: %s (%.0fs)", build.name,
                 "OK" if build.ok else "FAIL", build.status, build.seconds,
                 extra={"outcome": build.status, "ok": build.ok,
                        "seconds": round(build.seconds), "serial": build.serial,
                        "gmail": build.gmail, "proxy": build.proxy_name,
                        "app_account": build.app_account})
        if _event_sink is not None:
            try:
                _event_sink(
                    "build_finished", run_id=_run.get(), build=str(index),
                    serial=build.serial, status=build.status,
                    seconds=round(build.seconds, 1),
                    detail=(f"ok={build.ok} gmail={build.gmail} "
                            f"proxy={build.proxy_name} "
                            f"app={build.app_account}"))
            except Exception:                                     # noqa: BLE001
                log.warning("the event sink raised; the build is unaffected",
                            exc_info=True)
        if reporter:
            reporter.finish(build)
        else:
            mark = "OK" if build.ok else "FAIL"
            print(f"  {build.name} {mark}: {build.status} "
                  f"({build.seconds:.0f}s)", flush=True)
        return build

    stop_beating = _start_heartbeat(book, ledger, run_id)
    try:
        return _drive_jobs(client, settings, jobs, work=work, workers=workers,
                           started=started, ledger=ledger, total=total,
                           on_ready=on_ready, shutting_down=shutting_down)
    finally:
        stop_beating()
        # The batch's own thread stops belonging to it. Nothing here installs
        # a format any more, so there is none to put back - which is why the
        # 2026-08-23 incident cannot recur rather than being guarded against.
        _run.reset(run_token)


def _start_heartbeat(book: Book, ledger: Ledger | None = None,
                     run_id: str = NO_BUILD) -> Callable[[], None]:
    """Restamp what this run is holding, for as long as it is holding it.

    Returns the way to stop. A daemon thread so an interpreter on its way out
    is never held open by it, and a stop that waits, so the last beat cannot
    land after the run has released everything and re-stamp a row somebody
    else has since taken.

    Every failure is logged and the beat goes on. A beat that gives up
    silently is the dangerous one: the run keeps working, the stamps stop
    moving, and the next sync anywhere frees the rows out from under it.
    """
    stop = threading.Event()

    def beating() -> None:
        # A Thread starts with a fresh, empty context, so without this the
        # beat's warnings carry no run at all - and under concurrency four
        # batches each start one, all saying the same thing about different
        # runs (2026-08-31).
        _run.set(run_id)
        while not stop.wait(Pool.HEARTBEAT_SECONDS):
            try:
                held = book.beat()
            except Exception as exc:                              # noqa: BLE001
                log.warning("could not refresh the claims this run is "
                            "holding (%s); it will try again in %ds", exc,
                            Pool.HEARTBEAT_SECONDS)
                continue
            if held:
                log.debug("refreshed %d claim(s)", held)
            if ledger is not None:
                # The ledger's claims too. They were written once and never
                # refreshed, and its staleness window is the same five minutes
                # the sheet uses - so a build past its fifth minute read as
                # abandoned to `settle_abandoned` and `apply_phone_states`,
                # both of which spare a phone only while its claim is live.
                # Serial passes were the only thing keeping that harmless.
                try:
                    ledger.beat()
                except Exception as exc:                          # noqa: BLE001
                    log.warning("could not refresh the ledger claims this run "
                                "is holding (%s)", exc)

    thread = threading.Thread(target=beating, name="claims", daemon=True)
    thread.start()

    def done() -> None:
        stop.set()
        thread.join(timeout=Pool.HEARTBEAT_SECONDS)

    return done


def _drive_jobs(client, settings, jobs, *, work, workers, started, ledger,
                total, on_ready, shutting_down) -> list[Build]:
    """Run the jobs, one at a time or in a pool. Split out so `_run_jobs` can
    put the logging back however this returns."""
    parallel = max(1, workers or settings.max_concurrent_phones)
    if on_ready and parallel > 1:
        log.info("--watch works on one phone at a time")
        parallel = 1
    parallel = min(parallel, total)

    if parallel == 1:
        builds: list[Build] = []
        try:
            for index, job in enumerate(jobs, start=1):
                builds.append(work(index, job))
        except KeyboardInterrupt:
            print("\ninterrupted - stopping here", flush=True)
            shutting_down.set()
            _stop_all(client, started, ledger)
            # Re-raised, because swallowing it made `docker stop` mean nothing
            # while a build was running. SIGTERM arrives here as a
            # KeyboardInterrupt (cli.stop_on_sigterm), this caught it, the
            # phones were stopped - and then `run` returned normally, `once`
            # returned normally, and the serve loop carried on. Docker waited
            # out `stop_grace_period` (120s) and SIGKILLed, and in those two
            # minutes the loop could start four more passes and create phones
            # that the one signal nothing can catch then killed. The grace
            # period exists to prevent exactly that.
            #
            # Everything above has already run: the phones are stopped and the
            # rows released. `cli` catches this last and prints a line instead
            # of a traceback, and `serve.run` re-raises it to end the loop -
            # both were written expecting it to arrive (2026-08-28).
            raise
        return builds

    log.info("%d phones, %d at a time", total, parallel)
    futures = {}
    interrupted = False
    with ThreadPoolExecutor(max_workers=parallel,
                            thread_name_prefix="phone") as pool:
        futures = {pool.submit(work, i, j): i
                   for i, j in enumerate(jobs, start=1)}
        try:
            # Polled, and that is the whole point of the loop. A bare
            # `wait(futures)` is not interruptible: the main thread blocks in
            # it and Python delivers KeyboardInterrupt only at a bytecode
            # boundary, so the signal was not seen until every worker had
            # finished anyway. Measured both shapes - the interrupt landed at
            # 0.30s and was handled at 1.20s, after all three builds ran to
            # completion. Moving the `try` inside the `with` changed nothing,
            # because the problem was never where the handler sat.
            #
            # Polling returns to bytecode every second, so the signal lands
            # there, the workers are told, and they abort at their next
            # `check_cancelled()`. Measured at 0.51s against a 6-second batch.
            #
            # Not FIRST_EXCEPTION. `build_one` and `finish_one` catch
            # everything and return a Build, so a future here practically
            # never raises - and returning early would change nothing anyway,
            # because leaving the `with` shuts the pool down and waits for all
            # of them. It read as a policy the code does not have.
            while True:
                _done, pending = wait(futures, timeout=STOP_POLL_SECONDS)
                if not pending:
                    break
        except KeyboardInterrupt:
            # Inside the `with`, and that is the whole point. Wrapped around it
            # instead, leaving the block ran `shutdown(wait=True)` *before* this
            # body - so the flag that tells the workers to stop was set only
            # after every one of them had finished its build. Measured: the
            # interrupt landed at 0.30s and this line was reached at 1.20s,
            # after all three workers had run to completion.
            #
            # With real 7-10 minute builds that is longer than
            # `stop_grace_period`, so Docker's SIGKILL arrived first and every
            # phone the batch had started stayed up, billing, with nothing left
            # alive to stop it. Re-raising (2026-08-28) made the loop end; it
            # did not make the stop arrive (2026-08-29).
            #
            # Set here, the workers see it at their next `check_cancelled()`
            # and abort into their own `finally`, which stops the phone and
            # releases the row.
            interrupted = True
            print("\ninterrupted - stopping every phone this run started",
                  flush=True)
            shutting_down.set()
            for future in futures:
                future.cancel()
    # The drain happened above, with the flag already set.
    if interrupted:
        _stop_all(client, started, ledger)
        raise KeyboardInterrupt      # see the serial path above

    builds = []
    for future, index in futures.items():
        if future.cancelled():
            continue
        try:
            builds.append(future.result())
        except Exception as exc:                                  # noqa: BLE001
            log.error("phone %d raised: %s", index, exc)
            builds.append(Build(index=index, status="error", detail=str(exc)))
    builds.sort(key=lambda b: b.index)
    return builds


def run(client: Client, settings: Settings, *, count: int,
        workers: int | None = None, dry_run: bool = False,
        reporter: Reporter | None = None,
        on_ready: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None,
        finish_first: bool = True,
        finish_limit: int | None = None,
        book: Book | None = None,
        ledger: Ledger | None = None,
        codes_source: codes.CodeSource | None = None) -> list[Build]:
    """Produce `count` ready phones, finishing before building.

    `count` is how many phones are worked on, not how many new ones are made.
    A phone that already has its Gmail and its app and wants only an account is
    the cheapest ready phone available: it costs one app account, where a new
    one costs a phone, a Gmail and a proxy to reach the same place. So those go
    first, and only the remainder is built from nothing.

    That is also what the operator means. Four phones sat one step short while
    a later run built five more from scratch beside them, because "build 5"
    only ever meant "create 5" (2026-08-11). Pass finish_first=False for a
    caller that really does mean new phones.

    `cancel` lets a caller on another thread stop the run. The console needs
    it: Ctrl+C is delivered to the main thread, which is drawing the table, and
    never reaches the worker running this - so without a signal to pass in, the
    interrupt left the build running as an orphan (2026-08-11).
    """
    # A caller that has already opened the book and synced hands both in.
    # Without that this opened a SECOND Book and ran a SECOND full sync of the
    # same workbook every pass - `serve.once` had just done exactly that, so
    # every pass paid for two, and the decision `_show` had published was
    # computed against the state before the second one mutated it.
    #
    # Two Books is not merely wasteful, either. `Pool._claim_lock` is per
    # instance, so two of them are two different locks over two snapshots -
    # and that lock is the only thing stopping one Gmail reaching two phones
    # (2026-08-29).
    synced = book is not None
    book = book if book is not None else Book.open(settings)
    ledger = ledger if ledger is not None else Ledger.load(settings.state_dir,
                        stale_after=settings.stale_claim_seconds)
    if not dry_run and not synced:
        sync_sheet(client, book, ledger,
                   artifact_dir=settings.artifact_dir,
                   stale_claim_seconds=settings.stale_claim_seconds)

    waiting: list[dict] = []
    gone: list[dict] = []
    if finish_first:
        waiting, gone = _unfinished(client, book)
    # `count` alone cannot say "finish exactly two and build exactly five". It
    # is a total, and finishing takes from it first - so a caller that knows
    # only two accounts are waiting still gets `min(count, len(waiting))`
    # finishes, and every finish past the second one boots a real phone, finds
    # no account, ends `no_usable_gpt` and puts the phone back. That is the
    # 2026-08-28 deadlock, once per surplus job, and its `no_usable_gpt`
    # *clears* the breaker so nothing counts it.
    #
    # `finish_limit` is how a caller that has already counted says so. None
    # keeps the old behaviour, which is what a person typing `geelark build 5`
    # wants: use the phones that are already half-built before making new ones.
    to_finish = waiting[:count if finish_limit is None else min(finish_limit,
                                                               count)]
    to_build = count - len(to_finish)

    if dry_run:
        # Not actually freed here, but counted: the pool numbers below would be
        # wrong by exactly this much, and that is the difference between "there
        # is nothing left" and "nothing has been put back".
        stale = len([r for r in book.proxies._rows
                     if book.proxies.status_of(r) == book.proxies.spent_status])
        live = len({f"{(p.get('proxy') or {}).get('server')}:"
                    f"{(p.get('proxy') or {}).get('port')}"
                    for p in phones.listing(client)})
        if stale > live:
            print(f"note: {stale - live} proxy(s) are held by phones that no "
                  f"longer exist and would be freed first\n")
        print(f"{count} phone(s) would be worked on:")
        if to_finish:
            print(f"  {len(to_finish)} finished "
                  f"(no new phone, Gmail or proxy spent):")
            for phone in to_finish:
                print(f"      phone {phone['serial']:<6} {phone['gmail']:<34} "
                      f"stopped at {phone['status']}")
        print(f"  {to_build} built from the pools:")
        for pool in (book.proxies, book.gmails, book.apps):
            print(f"      {pool.tab:<10} {len(pool.available):>3} available"
                  f"{f', {len(pool.stuck)} stuck in_use' if pool.stuck else ''}"
                  f"{f', {len(pool.broken)} unusable' if pool.broken else ''}")
        for pool in (book.proxies, book.gmails, book.apps):
            for resource in pool.broken:
                print(f"  ! {pool.tab} row {resource.sheet_row}: {resource.error}")
        if gone:
            print(f"\n{len(gone)} row(s) name a phone that no longer exists "
                  f"and are skipped: {', '.join(p['serial'] for p in gone)}")
        print("\nNothing was created and nothing was written (--dry-run).")
        return []

    if gone:
        log.info("skipping %d row(s) whose phone no longer exists", len(gone))
    if to_finish:
        log.info("%d phone(s) need only an app account; finishing those first",
                 len(to_finish))

    jobs = ([{"kind": "finish", "phone": p} for p in to_finish]
            + [{"kind": "build", "phone": None} for _ in range(to_build)])
    if not jobs:
        return []
    return _run_jobs(client, settings, book, jobs, workers=workers,
                     reporter=reporter, on_ready=on_ready, cancel=cancel, ledger=ledger,
                     codes_source=codes_source)


def finish_run(client: Client, settings: Settings, *, limit: int | None = None,
               workers: int | None = None, dry_run: bool = False,
               reporter: Reporter | None = None,
               cancel: threading.Event | None = None,
               book: Book | None = None,
               ledger: Ledger | None = None,
               codes_source: codes.CodeSource | None = None) -> list[Build]:
    """Complete every phone that is one step short, and build nothing."""
    synced = book is not None
    book = book if book is not None else Book.open(settings)
    ledger = ledger if ledger is not None else Ledger.load(settings.state_dir,
                        stale_after=settings.stale_claim_seconds)
    if not dry_run and not synced:
        # A finish reuses the phone's own exit and only takes a free one if
        # it has to swap, so the pool check is worth its seconds here too -
        # that is the run that discovers a swap has nowhere to go.
        sync_sheet(client, book, ledger,
                   artifact_dir=settings.artifact_dir,
                   stale_claim_seconds=settings.stale_claim_seconds)
    pending, gone = _unfinished(client, book)
    if limit:
        pending = pending[:limit]

    if dry_run:
        print(f"{len(pending)} phone(s) would be finished:")
        for phone in pending:
            print(f"  phone {phone['serial']:<6} {phone['gmail']:<34} "
                  f"(stopped at {phone['status']})")
        if gone:
            print(f"\n{len(gone)} row(s) name a phone that no longer exists "
                  f"and are skipped: {', '.join(p['serial'] for p in gone)}")
        print(f"\napp accounts free: {len(book.apps.available)}")
        print("\nNothing was changed (--dry-run).")
        return []

    if gone:
        log.info("skipping %d row(s) whose phone no longer exists", len(gone))
    if not pending:
        return []
    jobs = [{"kind": "finish", "phone": p} for p in pending]
    return _run_jobs(client, settings, book, jobs, workers=workers,
                     reporter=reporter, on_ready=None, cancel=cancel,
                     ledger=ledger, codes_source=codes_source)


def _stop_all(client: Client, phone_ids: set[str], ledger: Ledger) -> None:
    """Last-resort cleanup: stop every phone this run started."""
    for phone_id in sorted(phone_ids):
        try:
            phones.stop(client, phone_id)
            ledger.release(phone_id, note="stopped by interrupt cleanup")
            print(f"  stopped {phone_id}", flush=True)
        except Exception as exc:                                  # noqa: BLE001
            log.error("COULD NOT STOP %s (%s) - run 'geelark reap' now",
                      phone_id, exc)


def summarise(builds: list[Build]) -> str:
    """The end-of-run table."""
    if not builds:
        return "nothing was built"

    lines = ["", "=" * 72, "SUMMARY", "=" * 72]
    for b in builds:
        mark = "ready " if b.ok else "FAILED"
        lines.append(f" {mark}  {b.name:<14} {b.status:<22} {b.seconds:>5.0f}s")
        if b.gmail:
            lines.append(f"          {b.gmail}"
                         f"{f'  +  {b.app_account}' if b.app_account else ''}")
        if b.proxy:
            lines.append(f"          via {b.proxy}")
        # The token, not the sentence: this is the copy you grep the logs and
        # the artifacts with. The sheet gets the sentence.
        for email, reason, _service in b.tried:
            lines.append(f"          tried {email}: {reason}")

    ready = sum(1 for b in builds if b.ok)
    unstopped = [b for b in builds if b.still_running]
    lines.append("-" * 72)
    lines.append(f" {ready}/{len(builds)} phones ready.")
    if unstopped:
        lines.append("")
        lines.append(f" *** {len(unstopped)} PHONE(S) COULD NOT BE STOPPED - "
                     f"THESE ARE STILL BILLING ***")
        for b in unstopped:
            lines.append(f"     {b.phone_id}")
        lines.append(" Run 'geelark reap' now.")
    else:
        lines.append(" Every phone was told to stop. GeeLark can go on showing "
                     "one as running for a minute after.")
    if ready < len(builds):
        lines.append(" The Phones tab records every phone, ready or not; the "
                     "resource tabs record why each credential failed.")
    return "\n".join(lines)
