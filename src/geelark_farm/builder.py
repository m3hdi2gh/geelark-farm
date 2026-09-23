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
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from . import (
    apps,
    breaker,
    cancel,
    codes,
    exit_health,
    failures,
    keeper,
    mailbox,
    phones,
    products,
    rows,
    runctx,
    shell,
)
from . import artifacts as archive

# Tests reach the proxy module through here (builder.proxy_mod.check);
# the code that uses it is in kit/exits and keeper now (2026-09-23).
from . import proxy as proxy_mod  # noqa: F401
from .accounts import Account
from .api import Client

# What a build produced and how it is said - build_result since the
# builder review (2026-09-23) - and the Phones tab it is written to
# (rows). The names stay here for their callers.
from .build_result import APPS as APPS
from .build_result import Build as Build
from .build_result import Capacity as Capacity
from .build_result import Reporter as Reporter
from .build_result import _mark as _mark
from .build_result import attempts_of as attempts_of
from .build_result import outcome_of as outcome_of
from .build_result import summarise as summarise
from .cancel import _STOP_HEARD as _STOP_HEARD
from .cancel import _STOP_SEEN as _STOP_SEEN

# How a build hears that it must stop - cancel since the builder review
# (2026-09-23). The names stay here for their callers; the state (the
# sets and the cached store reading) lives in cancel, and a test patches
# it there.
from .cancel import STOP_BY_HAND as STOP_BY_HAND
from .cancel import STOP_POLL_SECONDS as STOP_POLL_SECONDS
from .cancel import STOPPED_BY_A_PERSON as STOPPED_BY_A_PERSON
from .cancel import Aborted as Aborted
from .cancel import _given_up_on as _given_up_on
from .cancel import _hand_stop_wired, _stop_asked, _stop_honoured
from .config import Settings

# An exit's host: the day's captcha tally, the login-rate gate and a
# person's Free - exit_health since the builder review (2026-09-23). The
# names stay here for their callers; the tally's memory lives there.
from .exit_health import CAPTCHA_STRIKES_PER_HOST as CAPTCHA_STRIKES_PER_HOST
from .exit_health import HEAVY_CAPTCHA_ROUNDS as HEAVY_CAPTCHA_ROUNDS
from .exit_health import HELD_BACK as HELD_BACK
from .exit_health import HOST_CLEARS as HOST_CLEARS
from .exit_health import HOST_GATE_NOTE as HOST_GATE_NOTE
from .exit_health import SUSPECT as SUSPECT
from .exit_health import _bump_captcha_host as _bump_captcha_host
from .exit_health import _captcha_hosts_memory as _captcha_hosts_memory
from .exit_health import _strike_captcha_host as _strike_captcha_host
from .exit_health import _struck_hosts
from .exit_health import forgive_host as forgive_host
from .exit_health import gate_hosts as gate_hosts
from .exit_health import gate_threshold as gate_threshold
from .exit_health import host_clears as host_clears

# chatgpt_login is reached through here by the tests that patch its
# sign_in; the builder itself asks the products registry (2026-09-23).
from .flows import chatgpt_login, google_login, play_install  # noqa: F401

# The keeper's reconciliation of the tabs with the world - keeper.py since
# the builder review (2026-09-23). The build path never called it; the
# names stay here for their callers.
from .keeper import STEP_NAMES as STEP_NAMES
from .keeper import _account_on as _account_on
from .keeper import _busy_serials as _busy_serials
from .keeper import _revive_ladder as _revive_ladder
from .keeper import _settle_before_deleting as _settle_before_deleting
from .keeper import _unfinished as _unfinished
from .keeper import apply_phone_states as apply_phone_states
from .keeper import check_proxies as check_proxies
from .keeper import free_abandoned_claims as free_abandoned_claims
from .keeper import settle_abandoned as settle_abandoned
from .keeper import strand_check as strand_check
from .keeper import sync_phone_names as sync_phone_names
from .keeper import sync_phone_proxies as sync_phone_proxies
from .keeper import sync_proxies as sync_proxies
from .keeper import sync_sheet as sync_sheet

# The pieces any automation that drives a phone needs - kit/ since the
# builder review (2026-09-23). The names stay here for their callers.
from .kit import exits as kit_exits
from .kit import install as kit_install
from .kit.exits import ExitLease as ExitLease
from .kit.exits import _align_clock as _align_clock
from .kit.exits import _any_exit_free as _any_exit_free
from .kit.exits import _borrow_exit as _borrow_exit
from .kit.exits import _exit_ip as _exit_ip
from .kit.exits import _exit_up as _exit_up
from .kit.exits import _fresh_proxy as _fresh_proxy
from .kit.exits import _new_exit as _new_exit
from .kit.holds import RELEASE as RELEASE
from .kit.holds import SET_ASIDE as SET_ASIDE
from .kit.holds import SPEND as SPEND
from .kit.holds import _refused_holds as _refused_holds
from .kit.holds import _release as _release
from .kit.install import API_INSTALL_WAIT_SECONDS as API_INSTALL_WAIT_SECONDS
from .kit.install import PLAY_RECIPE_EXITS as PLAY_RECIPE_EXITS
from .kit.install import PLAY_RETRY_FLOOR_SECONDS as PLAY_RETRY_FLOOR_SECONDS
from .kit.install import PLAY_RETRY_REASONS as PLAY_RETRY_REASONS
from .kit.install import _install as _install
from .kit.install import _install_by_recipe as _install_by_recipe
from .kit.phone import ATTEMPT_SECONDS as ATTEMPT_SECONDS
from .kit.phone import PhoneRun as PhoneRun
from .kit.phone import _calls as _calls
from .kit.phone import _discard as _discard
from .kit.phone import _ended_by as _ended_by
from .kit.phone import _let_the_phone_go as _let_the_phone_go
from .kit.phone import _signed_in_after_all as _signed_in_after_all
from .ledger import Ledger
from .logs import NO_BUILD

# Device helpers, with the modules that own the device since the builder
# review (2026-09-23); the names stay here for their callers.
from .phones import FARM_GROUP as FARM_GROUP
from .phones import _bad_model as _bad_model
from .phones import _create_kept as _create_kept
from .phones import _in_the_farms_group as _in_the_farms_group
from .phones import _live_exits as _live_exits
from .phones import _remember_refusal as _remember_refusal
from .pools import Book, PhoneLog, Pool, Resource
from .rows import _condemn as _condemn
from .rows import _count_try as _count_try
from .rows import _note_on_row as _note_on_row
from .rows import _phone_note as _phone_note
from .rows import _phone_status as _phone_status
from .rows import _record as _record
from .rows import _write_row as _write_row

# The run's log context, run ids and event sink - runctx since the
# builder review (2026-09-23). The names stay here for their callers;
# the event sink itself is read in runctx, where set_event_sink puts it.
from .runctx import BuildContextFilter as BuildContextFilter
from .runctx import _build as _build
from .runctx import _next_run_id, _record_event
from .runctx import _run as _run
from .runctx import _serial as _serial
from .runctx import set_event_sink as set_event_sink
from .shell import _touch_method as _touch_method

# Moved to `wishes` with the strict reader the queue uses (the builder
# review, 2026-09-23); the name stays here for its callers.
from .wishes import Wanted as Wanted

log = logging.getLogger(__name__)

#: How often the pool is looked at while it works.
#:
#: Only so the main thread comes back to a bytecode boundary often enough to
#: receive a signal. A bare `wait(futures)` blocks until every worker is done,
#: which meant `docker stop` was not acted on until the whole batch had
#: finished - longer than `stop_grace_period`, so SIGKILL arrived first and the
#: phones stayed up billing (2026-08-29).
#:
#: Its own name. It was `STOP_POLL_SECONDS`, and so was the store poll
#: five hundred lines down - the second definition won, so this tick was
#: 5 s and then 2 s and never the 1 s written here (2026-09-21, found by
#: audit).
PASS_TICK_SECONDS = 1.0

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


#: Captchas met on one exit before the exit is changed rather than the
#: next Gmail spent. A captcha is Google distrusting the address, and one
#: is treated that way; two in a row on the same exit is the exit, and
#: phone 1995 spent three Gmails in an hour on SX44 while every other
#: phone that pass met one captcha or none (the operator, 2026-09-08).
CAPTCHAS_PER_EXIT = 2

# What the Phones tab records. The build knows exactly why it stopped and says
# so in the note; the Status column answers the only question asked of it at a
# glance - can I use this phone. The words are the Phones tab's own
# (PhoneLog); these names stay for the code that reads them here.
READY = PhoneLog.READY
APP_ONLY = PhoneLog.APP_ONLY

#: How a build ends when it stops warm on purpose - see
#: failures.WARM_FOR_OPERATOR, which the breaker reads too.
WARM_FOR_OPERATOR = failures.WARM_FOR_OPERATOR


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


#: The one stop that still keeps a phone nothing was signed into: the
#: run's own shutdown. The process is going down, and a delete that
#: needs a stop, a wait and a call is the half-done thing worse than a
#: row - the next run's sync finds the phone either way.
KEPT_WHEN_EMPTY = frozenset({"interrupted"})


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
    #: What a person chose for this phone, when one did. Only the app half
    #: is read here - the Gmail and the exit were settled before the phone
    #: existed - but the whole wish rides along so nothing has to be
    #: unpacked and passed twice.
    want: Wanted | None = None
    #: What each condemned account was refused for, by address - so the
    #: exoneration at the end can tell the service's own word about a
    #: credential from a phone that never got as far as judging one
    #: (the operator, 2026-09-10).
    judged: dict = field(default_factory=dict)
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
    #: Which service judged each account this phone tried, by address -
    #: the app pool holds two products now and its own `service` names
    #: one of them (2026-09-17).
    judged_by: dict = field(default_factory=dict)
    # Proxies tried and moved on from, with what was seen through each. Held
    # claimed for the rest of the run so a swap cannot hand one back.
    refused_exits: list[tuple[Resource, str]] = field(default_factory=list)
    #: Exits taken from under another phone once the pool ran dry. Not claimed
    #: - they belong to that phone - so they are remembered here instead.
    borrowed: set[str] = field(default_factory=set)
    #: The build's exits, shared with the phases before this one - see
    #: kit.exits.ExitLease. A finish starts its own on the exit it found.
    #: `proxy_row`, `refused_exits`, `borrowed` and `exits` are the lease's,
    #: kept in step with it, because what settles a session reads them.
    lease: ExitLease | None = None

    def __post_init__(self) -> None:
        if self.lease is None:
            self.lease = ExitLease(current=self.proxy_row,
                                   refused=self.refused_exits,
                                   borrowed=self.borrowed, swaps=self.exits)
        self.proxy_row = self.lease.current
        self.refused_exits = self.lease.refused
        self.borrowed = self.lease.borrowed
        self.exits = self.lease.swaps

    def exits_seen(self) -> set[str]:
        """Every exit address this phone has been through, refused or current.

        What bounds the swap loop. A borrowed exit is not claimed - another
        phone owns it - so it leaves no trace in `refused_exits`, and without
        this the build would take the same shared exit back every time.
        """
        return self.lease.seen()

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def check_cancelled(self) -> None:
        if self.cancelled and self.cancelled():
            raise Aborted("interrupted")
        if _stop_asked(getattr(self, "settings", None), self.build.serial):
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
        marked = cancel._given_up_on(s.settings, s.build.serial,
                              own_take=bool(s.want and s.want.requested_by))
        if marked:
            return s.finish("given_up_on",
                            f"somebody wrote {marked!r} in its State while "
                            f"this was running, so it was left alone")
        if s.remaining() <= ATTEMPT_SECONDS:
            _give_back_condemned(s)
            return s.finish("budget_exhausted",
                            "installed, but no budget left for the app login")
        if s.want is not None and not s.want.install_app:
            # Asked for without the app. That is a warm phone, which is what
            # the keeper builds on its own all day, so it stops here in the
            # ordinary way and the next pass finishes it like any other.
            _give_back_condemned(s)
            return s.finish("app_not_asked_for",
                            "built by hand without the app; it is warm and "
                            "the next pass can sign an account into it")
        if s.app_row is None:
            if s.want is not None and s.want.app_account:
                try:
                    s.app_row = _pick_named_app(s.book, s.want.app_account)
                except Aborted as refused:
                    _give_back_condemned(s)
                    return s.finish("chosen_app_unavailable", str(refused))
            elif s.want is not None:
                # Asked for by hand with no account named: the phone is
                # warm - Google in, the app on it - and an account goes on
                # when somebody sends one. Blank used to mean "the next
                # free one"; the card says none now (2026-09-08).
                _give_back_condemned(s)
                return s.finish(
                    WARM_FOR_OPERATOR,
                    "asked for without an account: Google is signed in and "
                    "the app is on it; send an account to it when you want")
            elif (s.settings.manual_login and s.want is None
                  and not (s.attempted or s.set_aside)
                  and (panel := _claim_panel(s)) is not None):
                # Manual login keeps the keeper's hands off the pool - but
                # an account the panel sent through the API was sent, and
                # waiting for a person to press Send made the panel's own
                # path a manual one (2026-09-16). Those, and only those,
                # the keeper signs in by itself.
                s.app_row = panel
            elif s.settings.manual_login and (s.want is None or s.attempted
                                              or s.set_aside):
                # With manual login on, no account goes onto a phone that
                # nobody sent it to. The keeper's own build stops here on
                # purpose - Google in, the app on it, warm - and a finish
                # whose sent account did not sign in does not help itself
                # to the next one (the operator, 2026-09-08).
                _give_back_condemned(s)
                if s.attempted or s.set_aside:
                    judged = "; ".join(
                        f"{address} - " + failures.verdict(
                            reason,
                            s.judged_by.get(address) or s.book.apps.service
                        ).seen
                        for address, reason in s.judged.items())
                    return s.finish(
                        WARM_FOR_OPERATOR,
                        (f"the account sent to it did not sign in: {judged}. "
                         f"No other was taken; the phone stays warm for the "
                         f"next one" if judged else
                         "the account sent to it did not sign in - see what "
                         "it tried - and no other was taken; the phone stays "
                         "warm for the next one"))
                return s.finish(
                    WARM_FOR_OPERATOR,
                    "warm on purpose: Google is signed in and the app is on "
                    "it; an account goes on when an operator sends one")
            else:
                s.app_row = s.book.apps.claim(str(s.build.serial or ''))
            if s.app_row is None:
                _give_back_condemned(s)
                return s.finish("no_usable_gpt",
                                "the Gpt Info tab has no unused account left")
        flow, package = _flow_for(s.settings, s.app_row)
        source = _codes_for(s.settings, s.app_row, s.codes)
        log.info("signing into %s as %s", package,
                 s.app_row.credentials.email)
        outcome = flow.sign_in(
            s.client, s.phone_id, s.app_row.credentials,
            package=package,
            budget_seconds=min(s.settings.app_login_budget_seconds,
                               s.remaining()),
            artifact_dir=s.artifacts,
            # Where a code the service emails is answered from -
            # this account's own source, not the run's (see
            # `_codes_for`). Nothing by default, which reports the page
            # exactly as it always did.
            codes=source,
            # The solver, for the one app flow that meets a captcha:
            # Spotify hands its challenge to Chrome, and reCAPTCHA asks
            # for pictures there rather than taking the tick - five
            # exits running (3644, 2026-09-19). Taken and ignored by the
            # other two flows, exactly as `codes` above is by Spotify's.
            solver_key=s.settings.capsolver_key,
            # As above: a press on Cancel is felt at the next screen, not
            # at the end of this login.
            watch=s.check_cancelled,
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
            s.build.app_product = _product_of(s.app_row)
            s.app_signed_in = True
            return None
        s.build.tried.append((s.app_row.credentials.email, outcome.reason,
                              _service_of(s.app_row)))
        s.judged_by[s.app_row.credentials.email] = _service_of(s.app_row)

        if failures.verdict(outcome.reason).needs_a_new_exit:
            # Refused before the account was looked at, so it is the exit's
            # problem, not the account's. Keep the account, change where the
            # request comes from, and try again - for as long as there is
            # budget and there are proxies. This does not count against the
            # account's attempts and never fails the phone with the network
            # reason: if it runs out, it runs out of budget or proxies, and
            # _new_exit says which. The account goes back as stock, never
            # judged.
            # Owned or borrowed, and what the build holds - the lease's
            # (kit.exits.ExitLease). Refused exits are held, not freed, and
            # released at the end.
            taken = s.lease.swap(s.client, s.settings, s.book, s.build,
                                 s.phone_id, outcome.reason, outcome.reason,
                                 s.remaining())
            s.proxy_row, s.exits = s.lease.current, s.lease.swaps
            # Written down above; only now the wait that can raise.
            _exit_up(s.client, s.settings, s.phone_id, taken, s.remaining(),
                     s.cancelled)
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
            said = failures.verdict(outcome.reason, _service_of(s.app_row))
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
                                              _service_of(s.app_row)).advice)
        s.app_row = None
        s.condemned.append(condemned)
        s.judged[condemned] = outcome.reason
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
    # Not for an account a person sent or named. The rule above was
    # written for the pool path, where a phone works through accounts on
    # its own and a bad phone would eat the pool; a sent account is one
    # the operator watched, and "Incorrect email address or password" on
    # it is the service's word about that credential. Given back, it went
    # straight back to the pool, was sent again, and was refused again
    # - twice in an evening on one address (the operator, 2026-09-10).
    # It stays set aside, with the reason beside it, for a person to
    # check; the phone stays warm. A refusal that judged nothing - a page
    # that would not move, a screen that could not be read - is still
    # the phone's, and still given back.
    by_hand = bool(s.settings.manual_login
                   or (s.want is not None and s.want.app_account))
    kept, given_back = [], []
    for address in s.condemned:
        reason = s.judged.get(address, "")
        if by_hand and failures.verdict(reason).costs_the_credential:
            kept.append(f"{address} ({reason})")
            continue
        row = s.book.apps.find(address)
        if row is None:
            continue
        s.book.apps.release(row, phone_failed=True, note=(
            f"Phone {s.build.serial} refused {len(s.condemned)} accounts and "
            f"signed none in, so the phone or its exit is the likelier fault "
            f"and nothing was judged here. Free to try on another phone."))
        given_back.append(address)
    if kept:
        log.warning("phone %s refused %d sent account(s) with the service's "
                    "own word about them (%s); they stay set aside for a "
                    "person to check", s.build.serial, len(kept),
                    ", ".join(kept))
    if given_back:
        log.warning("phone %s refused %d accounts and signed none in (%s); "
                    "putting them back rather than leaving them condemned",
                    s.build.serial, len(given_back), ", ".join(given_back))


#: How many addresses one build looks past when the exit pool cannot
#: serve the first. Each one is a single statement, and the pool's hosts
#: are few: past a handful the answer is "there are no exits for these
#: addresses", not "ask again".
GMAILS_PAST = 4


def _held_note(book: Book) -> str:
    """The addresses the hour is keeping back, named - or a pool that
    reads "no unused address left" while 44 sit in it looks empty for
    no reason (2026-09-14)."""
    count = getattr(book.gmails, "held_now", lambda: 0)()
    if not count:
        return ""
    return (f"; {count} address(es) on their last try are held back until "
            f"the hours Google lets these exits in (SIGNIN_GOOD_HOURS_UTC)")


def _pair_up(client: Client, book: Book, settings: Settings):
    """A free Gmail, and an exit that is not on the host it was refused on.

    The address is claimed first - a phone must never be created with
    nothing to sign into it - and the address is also what says which
    host to keep away from. So when the pool has no exit off that host,
    the address is held and the next one asked for, rather than the build
    dying and handing it straight back: five builds in a row took the
    same address, found no exit for it, released it untouched and were
    each counted a failure. That is a loop at the speed of a pass, and it
    opened the breaker in two and a half minutes (2026-09-13).

    Returns (None, None) when the pool has no free address at all. Raises
    `no_other_exit` when it had addresses and the exits could serve none
    of them - nothing is created and nothing is spent either way.
    """
    held: list = []
    try:
        for _ in range(GMAILS_PAST):
            row = book.gmails.claim()
            if row is None:
                if held:
                    raise Aborted("no_other_exit")
                return None, None
            avoid = str((getattr(row, "values", None) or {})
                        .get("Last Host") or "")
            try:
                return row, kit_exits._fresh_proxy(client, book, settings=settings,
                                         avoid_host=avoid)
            except Aborted as refused:
                held.append(row)
                if str(refused) != "no_other_exit":
                    raise
                log.info("no exit off %s for %s; holding it and asking for "
                         "the next address", avoid, row.label)
        raise Aborted("no_other_exit")
    finally:
        # Held, never used: every one of them goes back exactly as it was.
        for row in held:
            try:
                book.gmails.release(row)
            except Exception as exc:                              # noqa: BLE001
                log.warning("could not put %s back (%s)", row.label, exc)


#: Spotify's package; ChatGPT's is `settings.target_package`.
SPOTIFY_PACKAGE = products.PRODUCTS["spotify"].package
CLAUDE_PACKAGE = products.PRODUCTS["claude"].package
#: How many addresses one build may spend before it stops and says so.
#: Bounded by the budget alone, a phone on a bad exit or a bad batch of
#: accounts ate address after address and reported budget_exhausted,
#: which blames nothing (the build card, 2026-09-10).
GMAILS_PER_BUILD = 5


def _apps_every_phone(settings: Settings) -> tuple[str, ...]:
    """The apps every phone carries - see `products.apps_every_phone`."""
    return products.apps_every_phone(settings)


def _named(joined: str) -> str:
    """"chatgpt+spotify" the way a person says it."""
    return products.named(joined)


def _record_signin(settings: Settings, build: Build, *, gmail: str,
                   seller: str, host: str, position: int, reason: str,
                   ok: bool, seconds: float, captcha_rounds: int,
                   age_seconds: float | None = None, exit_ip: str = "",
                   touch: str = "", dumps: int = 0,
                   proxy_name: str = "") -> None:
    """One row in the store's `signins`, when there is a store."""
    if not getattr(settings, "store_enabled", False):
        return
    try:
        from . import geo
        from .store import signins as store_signins

        store_signins.record(settings, serial=build.serial, gmail=gmail,
                             seller=seller, host=host, model=build.model,
                             position=position, reason=reason, ok=ok,
                             seconds=seconds, captcha_rounds=captcha_rounds,
                             age_seconds=age_seconds,
                             exit_country=geo.country_for(settings, exit_ip)
                             if exit_ip else "",
                             touch=touch, dumps=dumps,
                             proxy_name=proxy_name)
    except Exception as exc:                                      # noqa: BLE001
        log.debug("sign-in not recorded (%s)", exc)


#: Builds start within seconds of each other, four at a time, and every
#: sign-in of theirs then reaches Google from one host within a minute.
#: A pause before the first address, different for each, spreads them
#: (2026-09-11). Nothing when the cadence is off.
SIGN_IN_STAGGER_SECONDS = (2.0, 40.0)


def _claim_panel(s):
    """The next panel-sent account for this phone, or None - and None
    on a pool that has no such notion (the sheet's)."""
    claim = getattr(s.book.apps, "claim_panel", None)
    if not callable(claim):
        return None
    return claim(str(s.build.serial or ""))


#: Which service judges an account of each product - the name that goes
#: into the reason a person reads. The app pool's own `service` is
#: OpenAI, which was every account's until a second product arrived
#: (2026-09-17): a Spotify row refused for its password said "OpenAI
#: would not take the password".
SERVICES = {key: spec.service for key, spec in products.PRODUCTS.items()}


def _service_of(app_row) -> str:
    return products.spec_of(getattr(app_row, "values", None)).service


def _product_of(app_row) -> str:
    """Which product an account row is for: the panel's `product` column,
    read through the pool's "Product" value; a row with none is the
    console's and the sheet's, and those were always ChatGPT."""
    return products.product_of(getattr(app_row, "values", None))


def _flow_for(settings: Settings, app_row):
    """The sign-in flow and the package for this account's product, off
    the registry: a new product is an entry in `products`, not another
    branch here (the builder review, 2026-09-23)."""
    spec = products.spec_of(getattr(app_row, "values", None))
    return spec.flow_module(), spec.package_for(settings)


def _package_for(settings: Settings, app: str) -> str:
    return (products.spec(app) or products.PRODUCTS[products.DEFAULT]
            ).package_for(settings)


def _install_the_rest(client: Client, settings: Settings, build: Build,
                      phone_id: str, *, done: str, ordered: dict,
                      remaining, artifacts, cancelled, play: bool = True
                      ) -> None:
    """The apps every phone carries beside the one the build is judged on.

    Never a failed phone: the app a wish named is what its account goes
    into, and one that came up without Claude is still the phone somebody
    asked for - so each of these is a note on the row instead. All of them
    were ordered from GeeLark's center the moment the phone booted, so by
    now most are one `pm list packages` away from done (2026-09-12).

    Writes what ended up on the phone into `build.app`, the word the row
    carries: "chatgpt+spotify+claude" when all three went on.
    """
    on = [done] if done else []
    for app in _apps_every_phone(settings):
        if app == done:
            continue
        if cancelled is not None and cancelled():
            break
        if remaining() <= 0:
            log.warning("no time left for %s on %s", APPS[app], build.serial)
            build.tried.append((app, "budget_exhausted", "GeeLark"))
            continue
        taken = bool(ordered.get(app))
        if not taken and settings.app_install_api:
            # The order at boot is an optimisation, not the only chance:
            # a phone that was already up never fired that hook, and a
            # bare phone has no Play Store to fall back on - the Store
            # asks to be signed in (2026-09-12).
            taken = apps.begin(client, phone_id, _package_for(settings, app),
                               name=APPS[app], settings=settings)
        got = kit_install._install(client, phone_id, _package_for(settings, app),
                       name=APPS[app], ordered=taken,
                       budget=min(settings.install_budget_seconds, remaining()),
                       artifacts=artifacts, cancelled=cancelled, play=play)
        build.trails.append(("install", got.trail))
        if got.ok:
            on.append(app)
        else:
            log.warning("%s did not install on %s (%s); the phone goes on "
                        "without it", APPS[app], build.serial, got.reason)
            build.tried.append((app, got.reason, "Play" if play else "GeeLark"))
    build.app = "+".join(on)


def _pick_named_app(book: Book, wanted: str):
    """The app account somebody named, claimed by name.

    A held-back row is never in `available`: the automatic claim leaves
    it alone because no flow serves its kind, or because the kind is
    one the pool must not hand out by itself. That is exactly the row a
    person's Send names - a Spotify account, whose category decides
    which phone it may go on (2026-09-17), and an `eco` GPT account,
    which the keeper must not take off the shelf by itself
    (2026-09-19). Named, it is taken with `claim_this`, which asks only
    whether the row is free. Everything the claim would offer anyway
    goes through `_pick`, whose refusals the console already words.
    """
    from . import accounts as domain

    want = wanted.strip().lower()
    resource = next((r for r in book.apps._rows
                     if (r.label or "").strip().lower() == want), None)
    values = (getattr(resource, "values", None) or {})
    held = resource is not None and domain.held_back(
        products.product_of(values),
        str(values.get("Credential kind") or "").strip(),
        str(values.get("Customer ready") or "").strip().upper() == "TRUE")
    if resource is None or not held:
        return _pick(book.apps, wanted, "account")
    if resource.error or (book.apps.status_of(resource)
                          not in book.apps.available_statuses):
        raise Aborted(f"the account {wanted} is not free - it is already on "
                      f"a phone, set aside, or not there at all")
    if not book.apps.claim_this(resource):
        raise Aborted(f"the account {wanted} was taken while this was being "
                      f"asked for")
    return resource


def _codes_for(settings: Settings, row, given):
    """Where this account's emailed code comes from - by product, not
    by kind.

    A ChatGPT code is emailed to an address the farm owns and is read
    out of the farm's own mailbox. **A customer is never asked for a
    ChatGPT code** (the operator, 2026-09-19), so where no mailbox is
    configured the answer is NoSource - the page is reported and the
    account set aside, exactly as before. Handing this one the panel's
    source would open a request nobody is ever asked to answer and sit
    on a phone for the whole of CODE_WAIT_MINUTES.

    Claude's code is the customer's own, read out of their inbox and
    typed into the panel, which is `given` - the source the run was
    started with.
    """
    if products.spec_of(getattr(row, "values", None)).codes == "panel":
        return given
    return mailbox.from_settings(settings) or codes.NoSource()


def _pick(pool, wanted: str, what: str):
    """The free row a person named, or a refusal saying why not.

    Raises rather than falling back to the next free row: somebody chose
    this one, and quietly building with another is the kind of help nobody
    asked for - it spends the wrong Gmail and reads as success.
    """
    want = wanted.strip().lower()
    for resource in pool.available:
        if (resource.label or "").strip().lower() == want:
            if pool.claim_this(resource):
                return resource
            raise Aborted(f"the {what} {wanted} was taken while this was "
                          f"being asked for")
    raise Aborted(f"the {what} {wanted} is not free in the tab - it is "
                  f"already on a phone, set aside, or not there at all")


@dataclass
class _BuildState:
    """Everything one build carries from one phase to the next.

    They were twelve locals and three closures inside one 667-line function,
    shared across six phases through eighteen returns (the builder review,
    2026-09-23). Named here, each phase says what it reads and writes, and
    the teardown reads the same object whatever phase ended the build.
    """

    client: Client
    settings: Settings
    book: Book
    ledger: Ledger
    index: int
    build: Build
    started: float
    deadline: float
    on_phone: Callable[[str], None] | None = None
    on_ready: Callable[[str], None] | None = None
    #: The wired one: every wait underneath takes it - see _hand_stop_wired.
    cancelled: Callable[[], bool] | None = None
    codes_source: codes.CodeSource | None = None
    want: Wanted | None = None
    #: A bare phone claims no address and signs nothing in.
    bare: bool = False
    phone_id: str = ""
    #: The exit the phone is on right now; a borrow makes it one the build
    #: does not own - the lease knows which it owns.
    proxy_row: Resource | None = None
    gmail_row: Resource | None = None
    log_row: int | None = None
    # Proxies this build tried and moved on from, with what was seen through
    # each. They stay claimed for the rest of the build so a swap cannot hand
    # one back, and are released together at the end.
    refused_exits: list = field(default_factory=list)
    # Every exit this build owns, has refused or has borrowed, across all of
    # its phases - see kit.exits.ExitLease.
    lease: ExitLease = field(default_factory=ExitLease)
    # The Gmail phase counts its own attempts; the app phase's are the
    # session's, since that loop is shared with `finish`.
    tried_gmails: int = 0
    captchas_here: int = 0                 # on the exit the phone is on now
    # One exit change for a text captcha per build.
    text_captchas: int = 0
    session: _Session | None = None
    # Whether the Gmail ended up on the device. Not the same question as "did
    # the build succeed" - see _release. The app account's equivalent lives on
    # the session, which owns that phase.
    gmail_signed_in: bool = False
    # Whether a sign-in was ever started on it. A phone stopped before that
    # has nothing to be asked about - and asking a phone that may not even
    # be up answers "could not say", which keeps it (see _discard's caller).
    asked_google: bool = False
    #: The apps ordered from GeeLark's installer at boot, by name.
    ordered: dict = field(default_factory=dict)
    artifacts: Path | None = None
    phone_made_at: float = 0.0
    #: What this job costs and how it ended - taken when the state is,
    #: which is before the first call (kit.phone.PhoneRun).
    run: PhoneRun | None = None

    def __post_init__(self) -> None:
        # One list: what the lease refuses is what the holds release.
        self.lease.refused = self.refused_exits
        if self.run is None:
            self.run = PhoneRun(self.client, self.build, started=self.started)

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def check_cancelled(self) -> None:
        """Both ways this build can be stopped: the service going down, and
        a person pressing Stop this one on its row.

        Only the first was checked here, and the second is the one an
        operator presses. `STOP_BY_HAND` had exactly one reader,
        `_Session.check_cancelled`, and the session is not built until
        after the Google sign-in and the install - so a stop asked for
        during those, which is most of a build's minutes, was heard only
        when they ended. Up to twenty-five minutes of a phone billing by
        the minute after somebody said stop (2026-09-06, found by audit).
        """
        if self.cancelled and self.cancelled():
            raise Aborted("interrupted")
        # STOP_BY_HAND, and the store's copy of it for a build running in
        # another container - see _stop_asked (2026-09-10).
        if _stop_asked(self.settings, self.build.serial):
            raise Aborted("stopped_by_hand")

    def finish(self, status: str, detail: str = "", ok: bool = False) -> Build:
        return self.run.finish(status, detail, ok)


def build_one(client: Client, settings: Settings, book: Book, ledger: Ledger,
              index: int, *,
              on_phone: Callable[[str], None] | None = None,
              on_ready: Callable[[str], None] | None = None,
              cancelled: Callable[[], bool] | None = None,
              codes_source: codes.CodeSource | None = None,
              want: Wanted | None = None) -> Build:
    """Take pooled resources to one stopped, ready phone.

    `want` is somebody choosing instead of the pool: a Gmail, an exit,
    whether the app goes on at all, and which account signs into it.

    Returns rather than raises: the caller is a batch, and one bad phone must
    not end it.
    """
    started = time.monotonic()
    build = Build(index=index)
    st = _BuildState(client=client, settings=settings, book=book,
                     ledger=ledger, index=index, build=build, started=started,
                     deadline=started + settings.build_budget_seconds,
                     on_phone=on_phone,
                     on_ready=on_ready, codes_source=codes_source, want=want)
    # From here on, `cancelled` is the wired one: it is what every wait
    # underneath takes, and the console's Cancel has to reach those too.
    st.cancelled = _hand_stop_wired(settings, build, cancelled)
    try:
        # In order, each ending the build by returning its Build - or None
        # to hand on to the next. The app phase always ends it.
        for step in _BUILD_PHASES:
            ended = step(st)
            if ended is not None:
                return ended
        return build
    except Exception as exc:                                      # noqa: BLE001
        return _ended_by(exc, st.finish, settings, f"build {index}")
    except BaseException:
        # Ctrl+C on the CLI's own run: the run's shutdown, filed as one, so
        # the empty phone is kept rather than deleted (KEPT_WHEN_EMPTY) -
        # it was left on whatever word the build had reached (2026-09-23).
        st.finish("interrupted", failures.situation("interrupted"))
        raise
    finally:
        _let_the_build_go(st)

def _acquire(st: _BuildState) -> Build | None:
    """The Gmail, the exit and the phone, taken together under one lock,
    and the phone's row and artifacts directory."""
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
    # A bare phone claims no address and signs nothing in: the Gmail
    # phase is skipped whole, and the phone is ready once it is up.
    st.bare = bool(st.want is not None and st.want.no_gmail)
    # A named account on a phone with a Gmail is signed in only when its
    # product says so (products.signs_in_on_gmail_build - ChatGPT's
    # alone today): the install phase ends any other app at "ready", and
    # a Spotify `error` account named there was installed for and never
    # signed in, with nobody told (the builder review, 2026-09-23). The
    # card refuses it; this is the net under the card, before anything
    # is claimed or a phone is paid for.
    if (not st.bare and st.want is not None and st.want.app_account
            and not products.signs_in_on_gmail_build(st.want.app)):
        return st.finish("chosen_app_unavailable",
                         f"{st.want.app_account} is a {st.want.app} account, "
                         f"and a new phone with a Gmail does not sign those "
                         f"in - a warm phone takes it through Send on its "
                         f"row")
    with _starting:
        # Somebody who named an exit gets that exit, so the pairing
        # below - which is about choosing one - has no part to play.
        chosen_exit = bool(st.want and st.want.proxy_name)
        st.proxy_row = None
        if st.bare:
            st.gmail_row = None
        elif st.want and st.want.gmail:
            st.gmail_row = _pick(st.book.gmails, st.want.gmail, "Gmail")
        elif chosen_exit:
            st.gmail_row = st.book.gmails.claim()
        else:
            # A Gmail off the queue carries the host it was refused
            # on, and a second try from the same host is the one thing
            # that made the first one worthless (the operator,
            # 2026-09-12). The two are chosen together, and an address
            # the exits cannot serve is looked past rather than handed
            # back for the next pass to pick up again (2026-09-13).
            st.gmail_row, st.proxy_row = _pair_up(st.client, st.book, st.settings)
        if st.gmail_row is None and not st.bare:
            return st.finish("no_usable_gmail",
                          "the Gmails tab has no unused address left, so "
                          "no phone was created" + _held_note(st.book))
        if chosen_exit:
            st.proxy_row = _pick(st.book.proxies, st.want.proxy_name, "exit")
        elif st.proxy_row is None:
            st.proxy_row = kit_exits._fresh_proxy(
                st.client, st.book, settings=st.settings,
                avoid_host=str((getattr(st.gmail_row, "values", None) or {})
                               .get("Last Host") or ""))
        st.build.proxy = str(st.proxy_row.proxy)
        st.build.proxy_name = st.proxy_row.name
        st.lease.current = st.proxy_row

        entry = _create_kept(st.client, st.settings, st.ledger, st.proxy_row.proxy,
                             label=f"build {st.index}",
                             account=st.gmail_row.label if st.gmail_row else "")
    st.phone_id = entry.phone_id
    st.build.phone_id = st.phone_id
    st.build.serial = str(entry.serial or "")
    st.build.model = str(getattr(entry, "model", "") or "")
    _serial.set(st.build.serial or NO_BUILD)
    # The first line of this phone's story (C8): born behind which
    # exit, for which address.
    _record_event("phone", "created", run_id=_run.get(), build=str(st.index),
                  serial=st.build.serial,
                  detail=f"created behind {st.build.proxy_name} for "
                         f"{st.gmail_row.label if st.gmail_row else 'nobody'}"
                         + (" - a bare phone, as asked" if st.bare else ""))
    # This phone did not exist a moment ago, so nothing is installed on
    # it. Said here rather than left to the field's default so that the
    # default can mean "nobody looked" - which is what a `finish` that
    # never reached the device has to be able to say.
    st.build.app_installed = False
    if st.on_phone:
        st.on_phone(st.phone_id)
    st.ledger.claim(st.phone_id, label=f"build {st.index}")

    # Serial, id and proxy - the three things that identify this phone
    # somewhere else. The model and region used to be written beside them
    # and cost a phone-list call each build to find out; nothing ever read
    # them back, and every phone had the same two values anyway.
    # A phone asked for by hand is its builder's from the start: taken
    # and owned by them, marked with who built it. The keeper's own
    # phones carry none of that.
    theirs = ({"State": "taken", "Built by": str(st.want.requested_by),
               "Owner": str(st.want.requested_by)}
              if st.want is not None and st.want.requested_by else {})
    if theirs:
        st.build.built_for = int(st.want.requested_by)
    st.log_row = st.book.phones.start(Serial=st.build.serial,
                                Proxy=st.build.proxy_name or st.build.proxy,
                                **theirs)
    # The Gmail was claimed inside `_starting`, before this phone existed -
    # it has to be, or a phone can be created with no address to sign in.
    # So the serial goes on now, the moment there is one. Without it the
    # row reads `in_use` with nothing saying which phone, and a tab with
    # several at once can be counted but not read (2026-08-29).
    if st.gmail_row is not None:
        st.book.gmails.note_serial(st.gmail_row, st.build.serial)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    # By serial, not by batch position: `build3` today and `build3`
    # three weeks ago are different phones, so a directory could not
    # be tied to a device and nothing could decide whether its pages
    # still described anything (2026-08-17).
    st.artifacts = (st.settings.artifact_dir
                 / f"{stamp}-build{st.build.serial or st.index}")
    st.build.artifact_dir = str(st.artifacts)

def _bring_up(st: _BuildState) -> Build | None:
    """The phone booted - with every app ordered from GeeLark's installer
    the moment it runs - its clock set to the exit's zone, and the
    sign-ins staggered."""
    # Every app the phone is to carry goes in through GeeLark's own
    # installer, ordered the moment the phone reports running and left
    # to land while the settle and the Google sign-in go on. The
    # center takes the orders at once: three given inside two seconds
    # on phone 2184 were all taken, and the missing app was on the
    # phone fourteen seconds later (2026-09-12). Play is the fallback,
    # and only for the app the build is judged on. A bare phone gets
    # them too: bare is about the accounts, and the Play Store it
    # cannot walk is not needed for any of this.
    st.ordered = {}

    def order_apps() -> None:
        if not st.settings.app_install_api:
            return
        for wanted in _apps_every_phone(st.settings):
            st.ordered[wanted] = apps.begin(
                st.client, st.phone_id, _package_for(st.settings, wanted),
                name=APPS[wanted], settings=st.settings)

    phones.ensure_running(st.client, st.phone_id,
                          timeout=min(phones.BOOT_SECONDS, st.remaining()),
                          cancelled=st.cancelled, on_running=order_apps)
    st.phone_made_at = time.monotonic()
    if st.on_ready:
        st.on_ready(st.phone_id)
    _align_clock(st.client, st.settings, st.phone_id, st.proxy_row)
    if not st.bare:
        shell.pause(*SIGN_IN_STAGGER_SECONDS)

def _google_phase(st: _BuildState) -> Build | None:
    """Google signed in on the phone: the Gmail ladder, the exit swaps
    Google's distrust asks for, and every sign-in recorded."""
    # ------------------------------------------------------- the Gmail
    while not st.gmail_signed_in and not st.bare:
        st.check_cancelled()
        if st.remaining() <= ATTEMPT_SECONDS:
            return st.finish("budget_exhausted",
                          "ran out of budget before a Gmail signed in")
        if st.gmail_row is None:
            # The first was claimed before the phone existed; this is the
            # next one, after that address was refused on this device - so
            # this one can say which phone it is on from the start.
            st.gmail_row = st.book.gmails.claim(
                st.build.serial,
                avoid_host=str(getattr(getattr(st.proxy_row, "proxy", None),
                                       "host", "") or ""))
            if st.gmail_row is None:
                return st.finish("no_usable_gmail",
                              "the Gmails tab had no other address to try "
                              "on this phone" + _held_note(st.book))
        # Every field, rather than the three somebody remembered. `Account`
        # subclasses `Credentials`, and this list was a copy of its fields
        # as they stood the day it was written: `email_code_only` was added
        # later and has been silently dropped here ever since, harmless
        # only because the Gmails tab never sets it. `recovery_email` would
        # have gone the same way, and the flow would have refused a row for
        # having no recovery address while the address sat on the row.
        account = Account(**dataclasses.asdict(st.gmail_row.credentials),
                          proxy=st.build.proxy)
        chosen = bool(st.want is not None and st.want.gmail)
        # The dashboard's building row reads this line: which address,
        # and how far into the build's five this one is.
        if chosen:
            log.info("signing in as %s (chosen on the build card)",
                     account.email)
        elif getattr(st.settings, "one_gmail_per_phone", True):
            # One per phone: a distrust refusal ends the build here;
            # only a credential verdict (wrong password) goes on to
            # the next address, up to the cap.
            log.info("signing in as %s (Gmail %d on this phone; one per "
                     "phone unless the password is wrong)",
                     account.email, st.tried_gmails + 1)
        else:
            log.info("signing in as %s (Gmail %d of %d on this phone)",
                     account.email, st.tried_gmails + 1, GMAILS_PER_BUILD)
        attempt_started = time.monotonic()
        st.asked_google = True
        outcome = google_login.sign_in(
            st.client, st.phone_id, account,
            budget_seconds=min(st.settings.login_budget_seconds, st.remaining()),
            artifact_dir=st.artifacts,
            solver_key=st.settings.capsolver_key,
            captcha_max=st.settings.captcha_max_attempts,
            # "Cancel" reaches inside the sign-in. Checked only here,
            # between build steps, it could not: a sign-in walking a
            # captcha is one step, so a phone somebody had stopped went
            # on answering grids for another five minutes.
            watch=st.check_cancelled,
        )
        st.build.trails.append(("google", outcome.trail))
        # Counted against the exit's host whether or not the captcha was
        # eventually passed: a phone that solves thirteen rounds on
        # 190.2.143.20 and signs in is still thirteen rounds this host
        # cost, and the host's other exits are set aside at three such
        # sign-ins in a day (the operator, 2026-09-09).
        # A heavy one only: with young accounts nearly every sign-in
        # meets a three-round captcha on any host, and counting those
        # set aside four exits on two ordinary hosts in one evening.
        # What marks a bad host is the sign-in that eats the rounds -
        # thirteen to forty-three on 190.2.143.20 - or never gets
        # through at all (the operator, 2026-09-09).
        rounds = sum(1 for s in (outcome.trail or []) if s == "captcha")
        _record_signin(
            st.settings, st.build, gmail=account.email,
            seller=str((st.gmail_row.values or {}).get("Seller") or ""),
            host=str(getattr(getattr(st.proxy_row, "proxy", None), "host", "")
                     or ""),
            position=st.tried_gmails + 1, reason=outcome.reason,
            ok=bool(outcome.ok),
            seconds=time.monotonic() - attempt_started,
            captcha_rounds=rounds,
            age_seconds=attempt_started - st.phone_made_at,
            exit_ip=_exit_ip(st.proxy_row),
            touch=_touch_method(st.phone_id),
            dumps=int(getattr(outcome, "dumps", 0) or 0),
            proxy_name=str(getattr(st.proxy_row, "name", "") or ""))
        heavy = (rounds >= HEAVY_CAPTCHA_ROUNDS
                 or outcome.reason == "captcha_shown")
        if heavy and st.proxy_row is not None:
            try:
                exit_health._strike_captcha_host(st.settings, st.book, st.proxy_row)
            except Exception as exc:                           # noqa: BLE001
                log.warning("could not count the captcha against the "
                            "exit's host (%s)", exc)
        if (failures.verdict(outcome.reason).needs_a_new_exit
                and st.proxy_row is not None and st.text_captchas < 1
                and not outcome.ok):
            # The exit's, not the address's: reCAPTCHA served text or
            # audio, which it does when it does not trust the address
            # at all. The exit is changed and the same address goes
            # again, unmarked, once per build (2026-09-10).
            st.text_captchas += 1
            try:
                st.proxy_row = st.lease.swap(
                    st.client, st.settings, st.book, st.build, st.phone_id,
                    "reCAPTCHA offered only text or audio on this exit",
                    outcome.reason, st.remaining())
            except Aborted as exc:
                # Not a person's stop - see the Play recipe's handler.
                if str(exc) in STOPPED_BY_A_PERSON:
                    raise
                log.warning("the exit could not be changed (%s); the "
                            "same address goes again on it", exc)
                phones.ensure_running(
                    st.client, st.phone_id,
                    timeout=min(phones.BOOT_SECONDS, st.remaining()),
                    cancelled=st.cancelled)
            else:
                _exit_up(st.client, st.settings, st.phone_id, st.proxy_row,
                         st.remaining(), st.cancelled)
            continue
        if outcome.ok:
            signed_as = _signed_in_as(st.book, st.gmail_row, account.email,
                                      outcome)
            st.build.gmail = signed_as
            st.gmail_signed_in = True
            # On the row now, not at the end. This is the column that
            # decides whether a phone a killed run left behind is
            # finishable or gets deleted, and it is true from this moment.
            rows._note_on_row(st.book, st.build.serial, Gmail=signed_as)
            break
        # Every way a Google sign-in fails is about the account or the
        # device, never the exit: a CAPTCHA is Google distrusting this
        # address's history, not the IP (the network refusals that ARE the
        # exit's fault come only from the app, in the loop below). So the
        # Gmail is marked and the next one is tried on the same phone.
        st.build.tried.append((account.email, outcome.reason,
                            st.book.gmails.service))
        if failures.verdict(outcome.reason).stops_the_phone:
            # Nothing was decided about this address, so it keeps its place
            # in the pool - _release puts it back as stock. Trying the next
            # one would only meet the same wall.
            said = failures.verdict(outcome.reason,
                                    st.book.gmails.service)
            return st.finish(outcome.reason,
                          f"the Google sign-in could not go on with this "
                          f"phone - {said.seen}")
        # The tab gets the taxonomy's advice, not the flow's. A flow
        # writes for whoever is debugging it; the sheet is read a day
        # later by someone deciding what to do with that row - and for a
        # CAPTCHA the two say opposite things, since the flow suggests a
        # cleaner proxy and the build has just set the address aside.
        st.book.gmails.fail(st.gmail_row, outcome.reason,
                         note=failures.verdict(outcome.reason,
                                              st.book.gmails.service).advice,
                         host=str(getattr(getattr(st.proxy_row, "proxy", None),
                                          "host", "") or ""),
                         settings=st.settings)
        st.gmail_row = None
        st.tried_gmails += 1
        said = failures.verdict(outcome.reason, st.book.gmails.service).seen
        if chosen:
            # Theirs, not the pool's: somebody named this address, and
            # the next free one is not what they asked for. It is set
            # aside above with the reason beside it; the build stops
            # and says which address and why (the build card,
            # 2026-09-10).
            return st.finish("chosen_gmail_failed",
                          f"{account.email} - {said}")
        if (failures.retryable(outcome.reason)
                and getattr(st.settings, "one_gmail_per_phone", True)):
            # One Gmail per phone. Google distrusting the first address
            # is Google distrusting the device and the exit: the second
            # address on the same phone signed in 54% of the time
            # against 73% for the first, the fifth never (2026-09-10).
            # The phone goes - nothing is signed into it - and the next
            # address gets a fresh one; this address waits on the
            # ladder (pgpool.fail) and comes back for a fresh phone too.
            return st.finish("phone_distrusted",
                          f"Google distrusted this phone on "
                          f"{account.email} ({said}); the next address "
                          f"goes on a fresh phone and exit")
        if st.tried_gmails >= GMAILS_PER_BUILD:
            tally = ", ".join(f"{email} ({reason})"
                              for email, reason, _ in st.build.tried[-st.tried_gmails:])
            return st.finish("gmails_exhausted",
                          f"{st.tried_gmails} Gmails from the pool were "
                          f"refused on this phone in a row - {tally}")
        # Two refusals of Google's distrust kind on this exit: the
        # exit is changed before the next address is tried on it. The
        # address just set aside stays set aside, and the next one
        # gets a fresh exit. No exit to move to is not a failed build:
        # the next address goes on the same exit, as before. It
        # counted captchas alone, so a phone asked for a phone number
        # five addresses running kept the same exit throughout -
        # every one of Google's distrust pages is the exit's to
        # answer for (2026-09-13).
        if failures.retryable(outcome.reason):
            st.captchas_here += 1
        if st.captchas_here >= CAPTCHAS_PER_EXIT and st.proxy_row is not None:
            try:
                st.proxy_row = st.lease.swap(
                    st.client, st.settings, st.book, st.build, st.phone_id,
                    f"{st.captchas_here} Gmails met a captcha on this exit",
                    "captcha_shown", st.remaining())
            except Aborted as exc:
                # Not a person's stop - see the Play recipe's handler.
                if str(exc) in STOPPED_BY_A_PERSON:
                    raise
                log.warning("the exit could not be changed (%s); the "
                            "next Gmail goes on the same one", exc)
                # _new_exit stops the phone before it looks for an
                # exit, so a refusal leaves it down.
                phones.ensure_running(
                    st.client, st.phone_id,
                    timeout=min(phones.BOOT_SECONDS, st.remaining()),
                    cancelled=st.cancelled)
            else:
                # The refused exit is held, not freed - the lease has it,
                # released at the end, marked with why.
                _exit_up(st.client, st.settings, st.phone_id, st.proxy_row,
                         st.remaining(), st.cancelled)
            st.captchas_here = 0

def _install_phase(st: _BuildState) -> Build | None:
    """The app the account goes into, and the rest every phone carries - or
    the bare phone and the no-account phone, which end here."""
    # ----------------------------------------------------- the install
    st.check_cancelled()
    marked = cancel._given_up_on(st.settings, st.build.serial,
                          own_take=bool(st.want and st.want.requested_by))
    if marked:
        return st.finish("given_up_on",
                      f"somebody wrote {marked!r} in its State while this "
                      f"was running, so it was left alone")
    if st.bare:
        # Bare is about the accounts, not the apps: nothing is signed
        # in anywhere, and the phone still carries all three, because
        # from the center they cost it seconds rather than the Play
        # Store's minutes (the operator, 2026-09-12).
        _install_the_rest(st.client, st.settings, st.build, st.phone_id, done="",
                          ordered=st.ordered, remaining=st.remaining,
                          artifacts=st.artifacts, cancelled=st.cancelled,
                          play=False)
        if not (st.want is not None and st.want.app_account):
            return st.finish("ready", f"a bare phone - no Google account, "
                                   f"as asked. On the phone: "
                                   f"{_named(st.build.app)}", ok=True)
        # A bare phone with an account named for it: a `normal`
        # Spotify account, which wants exactly this phone - no Google
        # account on it - and goes in now, through the same session
        # a warm phone's account goes in through (2026-09-17).
        if st.remaining() <= 0:
            return st.finish("budget_exhausted",
                          "built, but no time to sign the account in")
        st.session = _Session(client=st.client, settings=st.settings, book=st.book,
                           build=st.build, phone_id=st.phone_id,
                           artifacts=st.artifacts, deadline=st.deadline,
                           started=st.started, cancelled=st.cancelled,
                           codes=st.codes_source or codes.NoSource(),
                           lease=st.lease, want=st.want)
        gave_up = _sign_into_app(st.session)
        if gave_up is not None:
            return gave_up
        # Whichever app the account is for - the registry's word, not
        # "Spotify" whatever it was (the builder review, 2026-09-23).
        return st.finish("ready", f"a bare phone - no Google account, as "
                               f"asked - with {st.build.app_account} "
                               f"signed into "
                               f"{APPS.get(st.build.app_product, 'the app')}",
                      ok=True)
    if st.remaining() <= 0:
        return st.finish("budget_exhausted", "signed in, but no time to install")
    # Which app the account goes into, if any. The keeper's own
    # phones sign into ChatGPT; a hand-built one signs into what was
    # asked for - none, ChatGPT, Spotify or Claude. That choice is
    # about the account, not about what is on the phone: every phone
    # carries all three either way (the operator, 2026-09-12). It is
    # also the one install a build can fail on, which is why it goes
    # first and through the Play recipe.
    app = st.want.app if st.want is not None else products.DEFAULT
    st.build.app = app
    if not app:
        # Nothing is signed into anything, but the phone still carries
        # the apps every phone carries (the operator, 2026-09-12).
        st.build.app_installed = False
        _install_the_rest(st.client, st.settings, st.build, st.phone_id, done="",
                          ordered=st.ordered, remaining=st.remaining,
                          artifacts=st.artifacts, cancelled=st.cancelled)
        return st.finish("ready", f"signed into Google; no app account was "
                               f"asked for. On the phone: "
                               f"{_named(st.build.app)}", ok=True)
    # Every swap the recipe makes goes through the lease, which knows
    # the exit the build owns before the wait that can raise.
    installed, st.proxy_row = _install_by_recipe(
        st.client, st.settings, st.book, st.build, st.phone_id,
        _package_for(st.settings, app), name=APPS[app],
        ordered=bool(st.ordered.get(app)),
        remaining=st.remaining, artifacts=st.artifacts, cancelled=st.cancelled,
        lease=st.lease, proxy_row=st.proxy_row,
    )
    st.build.trails.append(("install", installed.trail))
    if not installed.ok:
        return st.finish("install_failed",
                      f"the app could not be installed - "
                      f"{failures.verdict(installed.reason).seen}")
    st.build.app_installed = True
    # And the rest of them. Every phone carries all three now, the
    # keeper's own and the ones asked for by hand alike: the center
    # installs them in the background off one call, so they cost the
    # build seconds rather than the Play Store's minutes (the
    # operator, 2026-09-12). One that does not go on is a note, never
    # a failed phone - the app the wish named is already on.
    _install_the_rest(st.client, st.settings, st.build, st.phone_id, done=app,
                      ordered=st.ordered, remaining=st.remaining,
                      artifacts=st.artifacts, cancelled=st.cancelled)
    if not products.signs_in_on_gmail_build(app):
        return st.finish("ready", f"signed into Google, and "
                               f"{_named(st.build.app)} on the phone",
                      ok=True)

def _app_phase(st: _BuildState) -> Build | None:
    """The app account signed in, through the session a finish shares."""
    # ------------------------------------------------- the app account
    st.session = _Session(client=st.client, settings=st.settings, book=st.book,
                       build=st.build, phone_id=st.phone_id, artifacts=st.artifacts,
                       deadline=st.deadline, started=st.started,
                       cancelled=st.cancelled,
                       codes=st.codes_source or codes.NoSource(),
                       lease=st.lease, want=st.want)
    gave_up = _sign_into_app(st.session)
    if gave_up is not None:
        return gave_up

    # Asked of the device, not of the run's own belief - and logged rather
    # than written to the tab, because "which packages are on it" is a
    # debugging question and the Note column is read by a person.
    packages = shell.third_party_packages(st.client, st.phone_id)
    log.info("installed here: %s", ", ".join(packages) or "nothing")
    return st.finish("ready", "signed into Google and into the app", ok=True)



#: build_one's phases, in the order it runs them.
_BUILD_PHASES = (_acquire, _bring_up, _google_phase, _install_phase,
                 _app_phase)


def _let_the_build_go(st: _BuildState) -> None:
    """The end of a build, whatever ended it: what it held settled, an empty
    phone discarded, the row written, the phone let go."""
    book, build, session = st.book, st.build, st.session
    # Once the app phase starts, the session is what holds the claims - it
    # swaps proxies and claims accounts as it goes. Read them back from it
    # here rather than from the state, because an Aborted raised inside it
    # never returns to update them, and the account it was holding would
    # stay in_use with nothing to free it.
    # The Gmail is the build's own - the session never sees it. A
    # proxy counts as used the moment a phone exists behind it: that phone
    # keeps it until someone deletes the phone, and handing it on would put
    # two devices on one exit address.
    held = [(book.gmails, st.gmail_row,
             SPEND if st.gmail_signed_in else RELEASE, "", "")]
    if session is None:
        # The exit the build owns - never one borrowed from another
        # phone, which the lease keeps apart (2026-09-23).
        held.append((book.proxies, st.lease.current,
                     SPEND if st.phone_id else RELEASE, "", ""))
        # The exits swapped away from before the app phase began. A
        # session releases its own; with none yet, nobody did, and a
        # build that ended in its Gmail phase after a swap - stopped,
        # refused, or finished without an app account - left them
        # `in_use` for good (2026-09-21, found by audit).
        held += _refused_holds(book, st.lease.refused)
    else:
        held += _session_holds(book, session, proxy_spent=bool(st.phone_id))
    _release(book, build, held, suspect_hosts=_struck_hosts(st.settings))
    # A phone with no Google account on it is not a phone. Nothing can be
    # done with it - `finish` refuses it by name, since there is nothing to
    # build on - so it is deleted rather than left occupying a plan slot and
    # a row that reads `incomplete` with an empty Gmail column. Its exit
    # goes back with it, which is why this runs before the row is written.
    #
    # Not while the run is shutting down: an interrupt is not a verdict on
    # the phone, and the next run's sync sees it either way.
    # A bare phone has none on purpose, and stays. A stop by hand does
    # not spare it any more - see STOPPED_BY_A_PERSON for why - only
    # the run's own shutdown does. And the device is asked whether it
    # is signed in after all only when a sign-in was ever started on
    # it: a phone stopped twelve seconds after it was created cannot
    # be, and asking it anyway answered "could not say" and kept it.
    empty = bool(st.phone_id and not st.gmail_signed_in and not st.bare
                 and build.status not in KEPT_WHEN_EMPTY
                 and not (st.asked_google
                          and _signed_in_after_all(st.client, build)))
    discarded = empty and _discard(st.client, book, st.ledger, build,
                                   exit_row=st.lease.current)
    # By serial, not by the row number `start` handed back ten minutes ago.
    # Any sibling discarding its phone deletes a row, and every row below it
    # moves up - so that number can have come to mean a different phone.
    if st.log_row is not None:
        rows._write_row(book, build, drop=discarded)
        if empty and not discarded:
            rows._condemn(book, build)
    _let_the_phone_go(st.client, st.settings, st.ledger, build,
                      "" if discarded else st.phone_id)


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
    # As a build's: seconds and what the finish cost in calls, on every
    # ending - one PhoneRun for both (kit/phone.py).
    finish = PhoneRun(client, build, started=started).finish

    phone_id = build.phone_id
    _serial.set(build.serial or NO_BUILD)
    # As in build_one: the console's Cancel rides in the one callable
    # every wait underneath takes.
    cancelled = _hand_stop_wired(settings, build, cancelled)

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
    #
    # Asked before the `try` and before `on_phone`, and answered without
    # touching the phone. It was asked inside both, so the refusal ran
    # the `finally` - which stops the phone - and `on_phone` had already
    # put it in the set `_stop_all` stops at shutdown: the net meant to
    # keep the run's hands off a person's phone switched that phone off
    # (2026-09-23, found by the builder review).
    held = ledger.get(phone_id)
    if held is None or not held.is_claimed or held.is_stale:
        try:
            live = phones.status(client, phone_id)
        except Exception as exc:                                  # noqa: BLE001
            # Not knowing is not a reason to refuse - the boot below asks
            # again anyway, and a finish that cannot start is its own
            # named failure.
            log.debug("could not read the state of %s (%s)", phone_id, exc)
        else:
            if live in (phones.RUNNING, phones.STARTING):
                finish("in_use_by_hand",
                       "the phone is already running and nothing here "
                       "started it, so somebody is using it")
                # Nothing was claimed, nothing was written, and the phone
                # is left exactly as it was. A Stop pressed on it is
                # answered all the same.
                _stop_honoured(settings, build.serial)
                return build

    # What the row said before this finish marked it `building`. An
    # ending that never reached the device - a phone that would not
    # start, a stop before the checks - makes no claim about it
    # (`_phone_status` is None), and `_record` then left the cell on
    # `building`; the keeper's `settle_abandoned` relabelled it
    # `app_only` with "ended before it could say why" (2026-09-23,
    # found by the builder review). It goes back to this instead.
    status_before = book.phones.status_of(build.serial)
    try:
        if on_phone:
            on_phone(phone_id)

        ledger.claim(phone_id, label=f"finish {build.serial}")
        # Say on the sheet that this phone is in hand, the moment it is. A
        # finish leaves the row reading `incomplete` for its whole length -
        # which is what it read before the finish started - so the tab gave a
        # reader no way to tell a phone being worked on right now from one
        # sitting warm and untouched. The account's row says `in_use` in the
        # same minute, and the two are meant to be read together (2026-08-28).
        #
        # Restored in the `finally` whatever happens: `_write_row` writes
        # the Status the run found, and an ending that found none puts
        # back `status_before`. Without the second half an interrupted
        # finish left it saying `building`, and `settle_abandoned` treats
        # a `building` row with a stale claim as abandoned.
        rows._note_on_row(book, build.serial, Status=book.phones.BUILDING)

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

        # The app of the account this finish signs in - the registry's
        # package for its product, not ChatGPT's whatever the account was
        # for - and through GeeLark's installer first, Play after, as a
        # build does (the builder review, 2026-09-23). A finish with no
        # account named signs in the pool's next one, which is ChatGPT's.
        spec = products.spec_of(getattr(phone.get("account"), "values", None))
        package = spec.package_for(settings)
        if package not in shell.third_party_packages(client, phone_id):
            log.info("%s is not installed here; installing it first", package)
            ordered = bool(settings.app_install_api and apps.begin(
                client, phone_id, package, name=spec.name, settings=settings))
            installed = kit_install._install(
                client, phone_id, package, name=spec.name, ordered=ordered,
                budget=min(settings.install_budget_seconds,
                           deadline - time.monotonic()),
                artifacts=artifacts, cancelled=cancelled)
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

    except Exception as exc:                                      # noqa: BLE001
        return _ended_by(exc, finish, settings, f"finishing {build.serial}")
    except BaseException:
        # As in build_one: Ctrl+C on the CLI's own run, filed as the
        # shutdown it is rather than left on whatever word it had reached.
        finish("interrupted", failures.situation("interrupted"))
        raise
    finally:
        # A proxy swapped in during finishing belongs to this phone now.
        # The hosts at their strike count today go back as suspect, as a
        # build's do - a finish released them as stock (the builder review,
        # 2026-09-23).
        _release(book, build,
                 _session_holds(book, session, proxy_spent=True),
                 suspect_hosts=_struck_hosts(settings))
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
            rows._count_try(settings, book, build)
        rows._write_row(book, build)
        if (rows._phone_status(build) is None and status_before
                and status_before != book.phones.BUILDING):
            try:
                rows._note_on_row(book, build.serial, Status=status_before)
            except Exception as exc:                              # noqa: BLE001
                log.error("could not put %s back to %s (%s)", build.serial,
                          status_before, exc)
        _let_the_phone_go(client, settings, ledger, build, phone_id)


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
    # The service of this account's own product - the pool's `service` is
    # OpenAI, and a Claude or Spotify row's note said "OpenAI" (the builder
    # review, 2026-09-23).
    said = failures.verdict(session.suspect_reason, _service_of(row)).seen
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
    held += _refused_holds(book, session.refused_exits)
    # Accounts the service asked something of rather than judged. Its own verb,
    # because neither of the other two is true: it was not spent, and releasing
    # it put it back blank - indistinguishable from a row nobody had tried, so
    # the next run picked the same one and met the same challenge. The reason
    # travels too: it becomes the row's status, so the cell says what was
    # asked rather than a word that needs a glossary.
    held += [(book.apps, resource, SET_ASIDE,
              f"On {today} "
              f"{failures.verdict(why, _service_of(resource)).seen}. "
              f"The account was "
              f"asked, not judged - nothing is known against it. Fix what it "
              f"was asked for, then blank this status to offer it again.", why)
             for resource, why in session.set_aside]
    return held


def _signed_in_as(book: Book, gmail_row, given: str, outcome) -> str:
    """The address the device holds, and the pool row renamed to it when
    that is not the address it was sold under (2026-09-16: lrinki795
    signed in as dearinki2wwiih). Never fatal: a row that will not take
    the rename still signed in, and the phone's own row says the name."""
    from .accounts import same_google_account

    held = str(getattr(outcome, "signed_in_as", "") or "").strip()
    if not held or same_google_account(held, given):
        return given
    log.warning("%s signed in as %s - the address on the row is a sign-in "
                "alias; the row is renamed to what the device holds",
                given, held)
    rename = getattr(book.gmails, "rename", None)
    if callable(rename) and gmail_row is not None:
        try:
            rename(gmail_row, held,
                   note=f"Sold as {given}; signs in as {held} (the device "
                        f"said so on {time.strftime('%Y-%m-%d')}).")
        except Exception as exc:                                  # noqa: BLE001
            log.warning("the Gmail row could not be renamed to %s (%s)",
                        held, exc)
    return held


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
    `Note` says why not. The list is the Phones tab's own now
    (`PhoneLog.possible_statuses`).
    """
    return PhoneLog.possible_statuses()


def _run_jobs(client: Client, settings: Settings, book: Book,
              jobs: list[dict], *, workers: int | None,
              reporter: Reporter | None,
              on_ready: Callable[[str], None] | None,
              cancel: threading.Event | None,
              ledger: Ledger | None = None,
              codes_source: codes.CodeSource | None = None,
              on_done: Callable[[dict, Build], None] | None = None
              ) -> list[Build]:
    """Run a mixed list of build and finish jobs, up to `workers` at a time.

    `on_done` hears each job the moment it ends, with the job and its
    Build - what a scheduler counting jobs in flight needs, since a batch
    of two ends only when the slower one does (2026-09-08).

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
            _serial.set(NO_BUILD)

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
            want = job.get("want")
            build = build_one(client, settings, book, ledger, index,
                              on_phone=note_phone, on_ready=on_ready,
                              cancelled=shutting_down.is_set,
                              codes_source=codes_source,
                              want=want)
            if want is not None:
                build.wanted_id = want.wanted_id
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
                 _mark(build), build.status, build.seconds,
                 extra={"outcome": build.status, "ok": build.ok,
                        "seconds": round(build.seconds), "serial": build.serial,
                        "api_calls": build.api_calls,
                        "gmail": build.gmail, "proxy": build.proxy_name,
                        "app_account": build.app_account})
        if runctx._event_sink is not None:
            try:
                runctx._event_sink(
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
            mark = _mark(build)
            print(f"  {build.name} {mark}: {build.status} "
                  f"({build.seconds:.0f}s)", flush=True)
        if on_done is not None:
            try:
                on_done(job, build)
            except Exception:                                     # noqa: BLE001
                log.warning("on_done raised; the build is unaffected",
                            exc_info=True)
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
                _done, pending = wait(futures, timeout=PASS_TICK_SECONDS)
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
        codes_source: codes.CodeSource | None = None,
        wanted: list[Wanted] | None = None,
        on_done: Callable[[dict, Build], None] | None = None) -> list[Build]:
    """Produce `count` ready phones, finishing before building.

    `wanted` are phones somebody asked for by hand, each with the
    credentials they chose. They run beside the shortfall rather than
    instead of it: a person asking for one phone is not asking for the
    farm to stop keeping itself stocked.

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
        keeper.sync_sheet(client, book, ledger, settings=settings,
                   artifact_dir=settings.artifact_dir,
                   stale_claim_seconds=settings.stale_claim_seconds)

    waiting: list[dict] = []
    gone: list[dict] = []
    if finish_first:
        waiting, gone = keeper._unfinished(client, book)
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
            + [{"kind": "build", "phone": None, "want": w}
               for w in (wanted or [])]
            + [{"kind": "build", "phone": None} for _ in range(to_build)])
    if not jobs:
        return []
    return _run_jobs(client, settings, book, jobs, workers=workers,
                     reporter=reporter, on_ready=on_ready, cancel=cancel, ledger=ledger,
                     codes_source=codes_source, on_done=on_done)


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
        keeper.sync_sheet(client, book, ledger, settings=settings,
                   artifact_dir=settings.artifact_dir,
                   stale_claim_seconds=settings.stale_claim_seconds)
    pending, gone = keeper._unfinished(client, book)
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


