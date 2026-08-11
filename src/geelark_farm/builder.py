"""Build a phone out of the resource pools.

    take a proxy  ──►  create the phone behind it  ──►  boot
      ──►  first usable Gmail  ──►  sign in
             │ the Gmail was bad ──► take the next one, same phone,
             │                       until the pool or the budget runs out
      ──►  install the app
      ──►  first usable app account  ──►  sign in
             │ the account was bad  ──► take the next one, same phone,
             │                          until the pool or the budget runs out
             │ refused at the edge  ──► new exit (refresh, else new proxy),
             │                          same account, same "until"
      ──►  record the phone  ──►  stop it

Nothing in that loop stops at a fixed number of tries. A phone gives up only
when the tab has nothing left to hand it, when the budget will not cover
another attempt, or when the failure says the phone itself is the problem
rather than the credential (FLOW_BLOCKED).

The difference from `orchestrator.py` is what a failure costs. There a row
names its proxy, its Gmail and its app account in advance, so a bad Gmail fails
the row and wastes the phone that was created for it. Here the phone is the
thing being built and the credentials are stock: a bad one is marked in its own
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

**A new exit is a refresh before it is a new proxy.** sx.org will give a proxy
a different exit address three times a day while keeping its host, port and
credentials, so nothing on the phone has to change. Only when that allowance is
gone, or the address comes back the same, does the build take another proxy -
which is possible at all because `/phone/detail/update` can repoint a phone
that already exists (`phones.set_proxy`). When no new exit can be had at all,
the build stops and says so; the account it was carrying goes back to the pool
untouched, because a network that would not carry the request never judged it.

**A proxy is not condemned for one refusal.** It was measured across twelve
attempts: every gateway produced both successes and rejections (2026-08-09). So
a proxy left behind goes back to the pool as `unused` with a note. Only a proxy
GeeLark cannot reach at all is marked `dead`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from . import phones, shell, sxorg
from . import proxy as proxy_mod
from .accounts import Account
from .api import ApiError, Client
from .config import Settings
from .flows import chatgpt_login, google_login, play_install
from .ledger import Ledger
from .pools import Book, Resource
from .sheets import SheetError

log = logging.getLogger(__name__)

_context = threading.local()


class BuildContextFilter(logging.Filter):
    """Stamp every log record with the build its thread is working on."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.row = getattr(_context, "build", "-")
        return True


def install_build_logging() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if any(isinstance(f, BuildContextFilter) for f in handler.filters):
            continue
        handler.addFilter(BuildContextFilter())
        handler.setFormatter(
            logging.Formatter("%(levelname)s [build %(row)s] %(name)s: %(message)s")
        )


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

# Failures where the flow never reached a verdict about the credential at all -
# the app would not start, no login control appeared, the router did not
# recognise the screen. The next credential meets the same wall, so the build
# stops on the phone's problem rather than feeding the pool into it. Everything
# not named here is a verdict about the credential, so a reason added to a flow
# later gets "try the next one" by default, which is the behaviour the flow
# describes.
FLOW_BLOCKED = frozenset({
    "app_not_installed", "app_would_not_start", "no_login_button",
    "google_sheet_stuck", "unknown_screen", "unknown_fatal",
    # Both are the service refusing to keep talking to this device or address,
    # not a judgement on the account being offered.
    "rate_limited", "too_many_attempts",
})

# A network refusal is not the account's fault and not this exit's alone: it
# was measured to be per-session, so the next exit has a real chance where this
# one failed. So a build keeps taking a new exit and retrying the SAME account
# for as long as it can - there is deliberately no fixed limit on the count.
# What bounds it is real resource: the build budget (the loop stops starting an
# attempt once too little of it is left) and the proxy pool (a swap that finds
# nothing left ends the build). Neither leaves the phone reporting the network
# error itself - it reports what actually ran out, budget or proxies.

# How many proxies may be skipped as unreachable before the build gives up.
# Every one of these is a proxy GeeLark could not connect through at all.
MAX_DEAD_PROXIES = 4

# Failures that are a verdict on the exit address rather than on the
# credential. These get a new exit and retry the SAME credential; everything
# else moves on to the next credential.
#
# Both are OpenAI's, and both arrive before an account has been submitted: a
# TLS handshake refused outright, and a Cloudflare edge refusal carrying a Ray
# ID. A CAPTCHA is deliberately NOT here. It looks like a network verdict and
# is not one - Google raises it on the account it is being shown, and burning a
# proxy for it wastes the proxy and keeps the Gmail that caused it.
EXIT_VERDICTS = frozenset({"network_ssl_rejected", "request_rejected"})

# The note left on a Gmail this build set aside for a CAPTCHA. The flow's own
# advice for captcha_shown says "a cleaner proxy is the fix" - true for the
# single-row `run`, which changes the proxy - but here the build treats a
# CAPTCHA as the address's own problem and moves to the next Gmail, so that
# advice would tell the reader to do the opposite of what happened. This says
# what the build did, and how to undo it if the address is believed good.
CAPTCHA_SET_ASIDE = ("CAPTCHA at sign-in - set aside and the next Gmail tried. "
                     "A residential exit usually clears it; blank this status "
                     "to let the address be tried again.")

# What a build needs left to be worth starting another attempt: a stop, a boot
# and a login. Below this the honest thing is to report what it has.
ATTEMPT_SECONDS = 420


@dataclass
class Build:
    """What one phone's construction produced, for the summary and the tab."""

    index: int
    ok: bool = False
    status: str = "not_started"
    phone_id: str = ""
    serial: str = ""
    proxy: str = ""
    gmail: str = ""
    app_account: str = ""
    detail: str = ""
    seconds: float = 0.0
    # True when this build's phone could not be confirmed stopped. The summary
    # must never claim nothing is billing while this is set.
    still_running: bool = False
    tried: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"phone {self.serial}" if self.serial else f"build {self.index}"


class Reporter(Protocol):
    """Where a run announces its progress - the plain CLI or the console."""

    def start(self, index: int, total: int) -> None: ...
    def finish(self, build: Build) -> None: ...


class Aborted(Exception):
    """The run is shutting down; stop what this build is doing."""


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
    cancelled: Callable[[], bool] | None = None
    proxy_row: Resource | None = None
    app_row: Resource | None = None
    app_signed_in: bool = False
    exits: int = 0
    # Proxies tried and moved on from, with what was seen through each. Held
    # claimed for the rest of the run so a swap cannot hand one back.
    refused_exits: list[tuple[Resource, str]] = field(default_factory=list)

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def check_cancelled(self) -> None:
        if self.cancelled and self.cancelled():
            raise Aborted("interrupted")

    def finish(self, status: str, detail: str = "", ok: bool = False) -> Build:
        self.build.ok, self.build.status, self.build.detail = ok, status, detail
        return self.build


def _sign_into_app(session: _Session) -> Build | None:
    """Work through the app accounts on a phone that is signed in and installed.

    Returns a finished Build when it gives up, or None once an account is in -
    so the caller can carry on to whatever it does after.
    """
    s = session
    while not s.app_signed_in:
        s.check_cancelled()
        if s.remaining() <= ATTEMPT_SECONDS:
            return s.finish("budget_exhausted",
                            "installed, but no budget left for the app login")
        if s.app_row is None:
            s.app_row = s.book.apps.claim()
            if s.app_row is None:
                return s.finish("no_usable_gpt",
                                "the Gpt Info tab has no unused account left")
        log.info("signing into the app as %s", s.app_row.credentials.email)
        outcome = chatgpt_login.sign_in(
            s.client, s.phone_id, s.app_row.credentials,
            package=s.settings.target_package,
            budget_seconds=min(s.settings.app_login_budget_seconds,
                               s.remaining()),
            artifact_dir=s.artifacts,
        )
        if outcome.ok:
            s.build.app_account = s.app_row.credentials.email
            s.app_signed_in = True
            return None
        s.build.tried.append(f"{s.app_row.credentials.email}: {outcome.reason}")

        if outcome.reason in EXIT_VERDICTS:
            # Refused before the account was looked at, so it is the exit's
            # problem, not the account's. Keep the account, change where the
            # request comes from, and try again - for as long as there is
            # budget and there are proxies. This does not count against the
            # account's attempts and never fails the phone with the network
            # reason: if it runs out, it runs out of budget or proxies, and
            # _new_exit says which. The account goes back as stock, never
            # judged.
            previous = s.proxy_row
            s.proxy_row = _new_exit(s.client, s.settings, s.book, s.build,
                                    s.phone_id, s.proxy_row, outcome.reason,
                                    s.remaining(), cancelled=s.cancelled)
            if previous is not None and previous is not s.proxy_row:
                # Held, not freed - see _new_exit. Released at the end.
                s.refused_exits.append((previous, outcome.reason))
            s.exits += 1
            continue
        if outcome.reason in FLOW_BLOCKED:
            # The app never got as far as judging this account, so it goes
            # back to the pool untouched and the phone reports its own
            # problem. Named app_* so the runbook entry someone reaches for
            # is the app's, not Google's - the same convention as `run`.
            return s.finish(f"app_{outcome.reason}",
                            f"the app login could not proceed on this phone: "
                            f"{outcome.detail}")
        s.book.apps.fail(s.app_row, outcome.reason, note=outcome.detail[:300])
        s.app_row = None
    return None


def _fresh_proxy(client: Client, book: Book) -> Resource:
    """Claim a proxy GeeLark can actually reach.

    Checked before it is used, because an unreachable proxy is the one failure
    that is genuinely the proxy's: GeeLark either carried the request or it did
    not. Those are marked `dead` and the next one is tried.

    The proxy being replaced is released only after this returns, so `claim()`
    cannot hand back the very proxy that was just judged.
    """
    for _ in range(MAX_DEAD_PROXIES + 1):
        resource = book.proxies.claim()
        if resource is None:
            raise Aborted("no_usable_proxy")
        try:
            result = proxy_mod.check(client, resource.proxy)
        except (proxy_mod.ProxyError, ApiError) as exc:
            log.warning("proxy %s is dead: %s", resource.label, exc)
            book.proxies.fail(resource, "dead", note=str(exc)[:200])
            continue
        book.proxies.record_exit(resource, str(result.get("outboundIP") or ""))
        return resource
    raise Aborted("no_usable_proxy")


def build_one(client: Client, settings: Settings, book: Book, ledger: Ledger,
              index: int, *,
              on_phone: Callable[[str], None] | None = None,
              on_ready: Callable[[str], None] | None = None,
              cancelled: Callable[[], bool] | None = None) -> Build:
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
    app_row: Resource | None = None
    log_row: int | None = None
    # Proxies this build tried and moved on from, with what was seen through
    # each. They stay claimed for the rest of the build so a swap cannot hand
    # one back, and are released together at the end.
    refused_exits: list[tuple[Resource, str]] = []
    # The Gmail phase counts its own attempts; the app phase's are the
    # session's, since that loop is shared with `finish`.
    tried_gmails = 0
    session: _Session | None = None
    # Whether each credential ended up on the device. Not the same question as
    # "did the build succeed" - see _release.
    gmail_signed_in = False
    app_signed_in = False

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
        proxy_row = _fresh_proxy(client, book)
        build.proxy = str(proxy_row.proxy)

        entry = phones.create(client, settings, proxy_row.proxy, ledger=ledger,
                              label=f"build {index}")
        phone_id = entry.phone_id
        build.phone_id = phone_id
        build.serial = str(entry.serial or "")
        if on_phone:
            on_phone(phone_id)
        ledger.claim(phone_id, label=f"build {index}")

        details = phones.info(client, phone_id).get("equipmentInfo") or {}
        log_row = book.phones.start(
            Serial=build.serial, **{"Phone ID": phone_id},
            Model=f"{details.get('deviceBrand', '')} "
                  f"{details.get('deviceModel', '')}".strip(),
            Region=details.get("countryName") or settings.region,
            Proxy=build.proxy,
        )

        stamp = time.strftime("%Y%m%d-%H%M%S")
        artifacts = settings.artifact_dir / f"{stamp}-build{index}"

        phones.ensure_running(client, phone_id, timeout=remaining(),
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
                gmail_row = book.gmails.claim()
                if gmail_row is None:
                    return finish("no_usable_gmail",
                                  "the Gmails tab has no unused address left")
            account = Account(
                email=gmail_row.credentials.email,
                password=gmail_row.credentials.password,
                totp_secret=gmail_row.credentials.totp_secret,
                proxy=build.proxy,
            )
            log.info("signing in as %s", account.email)
            outcome = google_login.sign_in(
                client, phone_id, account,
                budget_seconds=min(settings.login_budget_seconds, remaining()),
                artifact_dir=artifacts,
            )
            if outcome.ok:
                build.gmail = account.email
                gmail_signed_in = True
                break
            # Every way a Google sign-in fails is about the account or the
            # device, never the exit: a CAPTCHA is Google distrusting this
            # address's history, not the IP (the network refusals that ARE the
            # exit's fault come only from the app, in the loop below). So the
            # Gmail is marked and the next one is tried on the same phone.
            build.tried.append(f"{account.email}: {outcome.reason}")
            if outcome.reason in FLOW_BLOCKED:
                # Nothing was decided about this address, so it keeps its place
                # in the pool - _release puts it back as stock. Trying the next
                # one would only meet the same wall.
                return finish(outcome.reason,
                              f"the sign-in could not proceed on this phone: "
                              f"{outcome.detail}")
            note = (CAPTCHA_SET_ASIDE if outcome.reason == "captcha_shown"
                    else outcome.detail[:300])
            book.gmails.fail(gmail_row, outcome.reason, note=note)
            gmail_row = None
            tried_gmails += 1

        # ----------------------------------------------------- the install
        check_cancelled()
        if remaining() <= 0:
            return finish("budget_exhausted", "signed in, but no time to install")
        installed = play_install.install(
            client, phone_id, settings.target_package,
            budget_seconds=min(settings.install_budget_seconds, remaining()),
            artifact_dir=artifacts,
        )
        if not installed.ok:
            return finish("install_failed", installed.detail)

        # ------------------------------------------------- the app account
        session = _Session(client=client, settings=settings, book=book,
                           build=build, phone_id=phone_id, artifacts=artifacts,
                           deadline=deadline, cancelled=cancelled,
                           proxy_row=proxy_row, refused_exits=refused_exits)
        gave_up = _sign_into_app(session)
        if gave_up is not None:
            return gave_up

        packages = shell.third_party_packages(client, phone_id)
        return finish("ready", f"apps: {', '.join(packages) or 'none'}", ok=True)

    except Aborted as exc:
        return finish(str(exc), f"the build stopped: {exc}")
    except Exception as exc:                                      # noqa: BLE001
        # Deliberately broad. Whatever went wrong, the resources this build is
        # holding must go back and the phone must be stopped - an exception
        # escaping here leaves three tabs saying `in_use` and a phone billing.
        log.exception("build %d failed with an unhandled error", index)
        return finish("error", str(exc))
    finally:
        # Once the app phase starts, the session is what holds the claims - it
        # swaps proxies and claims accounts as it goes. Read them back from it
        # here rather than from the locals, because an Aborted raised inside it
        # never returns to update them, and the account it was holding would
        # stay in_use with nothing to free it.
        if session is not None:
            proxy_row, app_row = session.proxy_row, session.app_row
            app_signed_in = session.app_signed_in
            refused_exits = session.refused_exits
        # A proxy counts as used the moment a phone exists behind it: that
        # phone keeps it until someone deletes the phone, and handing it to the
        # next build would put two devices on one exit address.
        held = [(book.proxies, proxy_row, bool(phone_id), ""),
                (book.gmails, gmail_row, gmail_signed_in, ""),
                (book.apps, app_row, app_signed_in, "")]
        # Every exit this build tried and left behind. `unused`, not condemned:
        # these refusals were measured to be per-session rather than per-proxy,
        # so the stock goes back on the shelf saying what was seen through it.
        held += [(book.proxies, resource, False,
                  f"{why} seen through it on {time.strftime('%Y-%m-%d')}")
                 for resource, why in refused_exits]
        _release(book, build, held)
        if log_row is not None:
            _record(book, log_row, build)
        if phone_id:
            try:
                phones.stop(client, phone_id)
                log.info("stopped %s", phone_id)
            except Exception as exc:                              # noqa: BLE001
                build.still_running = True
                log.error("COULD NOT STOP %s (%s) - run 'geelark reap'",
                          phone_id, exc)
            ledger.release(phone_id, note=build.status)


def finish_one(client: Client, settings: Settings, book: Book, ledger: Ledger,
               phone: dict, index: int, *,
               on_phone: Callable[[str], None] | None = None,
               cancelled: Callable[[], bool] | None = None) -> Build:
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
        ledger.claim(phone_id, label=f"finish {build.serial}")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        artifacts = settings.artifact_dir / f"{stamp}-finish{build.serial}"
        phones.ensure_running(client, phone_id, timeout=deadline - time.monotonic(),
                              cancelled=cancelled)

        # The device is the only truth. A row can say anything; what decides
        # whether this phone can be finished is what is on it.
        present = shell.device_accounts(client, phone_id)
        if not present:
            return finish("no_google_account",
                          "nothing is signed into Google on this phone, so "
                          "there is nothing to finish - rebuild it")
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
            if not installed.ok:
                return finish("install_failed", installed.detail)

        # The proxy this phone already has. It is not claimed from the pool -
        # the phone owns it - so a swap must not release it back as stock,
        # which is why proxy_row starts as None.
        session = _Session(client=client, settings=settings, book=book,
                           build=build, phone_id=phone_id, artifacts=artifacts,
                           deadline=deadline, cancelled=cancelled)
        gave_up = _sign_into_app(session)
        if gave_up is not None:
            return gave_up

        packages = shell.third_party_packages(client, phone_id)
        return finish("ready", f"apps: {', '.join(packages) or 'none'}", ok=True)

    except Aborted as exc:
        return finish(str(exc), f"finishing stopped: {exc}")
    except Exception as exc:                                      # noqa: BLE001
        log.exception("finishing %s failed with an unhandled error", build.serial)
        return finish("error", str(exc))
    finally:
        held: list[tuple] = []
        if session is not None:
            held.append((book.apps, session.app_row, session.app_signed_in, ""))
            # A proxy swapped IN during finishing belongs to this phone now.
            if session.proxy_row is not None:
                held.append((book.proxies, session.proxy_row, True, ""))
            held += [(book.proxies, resource, False,
                      f"{why} seen through it on {time.strftime('%Y-%m-%d')}")
                     for resource, why in session.refused_exits]
        _release(book, build, held)
        _record(book, phone["sheet_row"], build)
        try:
            phones.stop(client, phone_id)
            log.info("stopped %s", phone_id)
        except Exception as exc:                                  # noqa: BLE001
            build.still_running = True
            log.error("COULD NOT STOP %s (%s) - run 'geelark reap'",
                      phone_id, exc)
        ledger.release(phone_id, note=build.status)


def _refreshed(client: Client, settings: Settings, book: Book,
               current: Resource) -> bool:
    """Ask sx.org for a new exit on the proxy the phone already has.

    Cheaper than another proxy in every way that matters: the host, port and
    credentials do not change, so the phone needs no update call - only a
    restart, which it needs anyway.

    Returns whether the exit actually moved. A refresh that comes back on the
    same address has spent one of the day's three and achieved nothing, so it
    must not be reported as a new exit - the caller would retry into the same
    refusal and call it a second opinion.
    """
    port_id = book.proxies.port_id(current)
    if not port_id:
        log.info("%s has no Port ID, so sx.org cannot refresh it", current.label)
        return False
    if not settings.sxorg_api_key:
        log.info("SXORG_API_KEY is not set, so no proxy can be refreshed")
        return False
    spent = book.proxies.refreshes_today(current)
    if spent >= sxorg.REFRESHES_PER_DAY:
        log.info("%s has used all %d refreshes today",
                 current.label, sxorg.REFRESHES_PER_DAY)
        return False

    before = (current.values.get("Last Exit IP") or "").strip()
    try:
        sxorg.refresh(settings.sxorg_api_key, port_id)
    except sxorg.SxError as exc:
        log.warning("sx.org would not refresh %s: %s", current.label, exc)
        return False
    # Recorded even if the address turns out unchanged: the allowance was spent
    # either way, and a count that only tracks the successes will hand the
    # vendor a fourth request tomorrow morning and be surprised.
    book.proxies.note_refresh(current)

    try:
        after = str(proxy_mod.check(client, current.proxy).get("outboundIP") or "")
    except (proxy_mod.ProxyError, ApiError) as exc:
        log.warning("%s did not answer after the refresh: %s", current.label, exc)
        return False
    book.proxies.record_exit(current, after)
    if before and after == before:
        log.warning("%s refreshed to the same address (%s) - taking another "
                    "proxy instead", current.label, after)
        return False
    log.info("%s refreshed: %s -> %s", current.label, before or "?", after)
    return True


def _new_exit(client: Client, settings: Settings, book: Book, build: Build,
              phone_id: str, current: Resource | None, why: str, budget: float,
              cancelled: Callable[[], bool] | None = None) -> Resource | None:
    """Get the phone onto a different exit address, the cheapest way first.

    The phone is stopped before anything, for two reasons that both apply
    whichever branch is taken: GeeLark's documentation says not to call the
    update while a phone is starting, and Android reads the proxy when the
    network comes up - a phone left running would keep the exit just judged.
    """
    log.warning("%s - getting a different exit address", why)
    phones.stop(client, phone_id)

    if current is not None and _refreshed(client, settings, book, current):
        # Same credentials, different address: nothing on the phone changes.
        time.sleep(5)
        phones.ensure_running(client, phone_id, timeout=budget,
                              cancelled=cancelled)
        return current

    try:
        replacement = _fresh_proxy(client, book)
    except Aborted:
        # Every proxy this build could claim has now been tried and refused.
        # Named apart from a build that never got one at all: this phone has
        # been through the pool, and that is a fact about the pool or the
        # service, not about the account it is carrying.
        raise Aborted("all_exits_refused") from None
    try:
        phones.set_proxy(client, phone_id, replacement.proxy)
    except ApiError as exc:
        # The phone keeps the proxy it had, so nothing is broken - but this
        # build cannot do what it came here to do, and saying "the login
        # failed" would hide that.
        book.proxies.release(replacement, note=f"GeeLark refused it: {exc}")
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
    time.sleep(5)
    phones.ensure_running(client, phone_id, timeout=budget, cancelled=cancelled)
    return replacement


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
    for pool, resource, spent, note in held:
        if resource is None:
            continue
        try:
            if spent:
                pool.spend(resource, serial=build.serial,
                           note=f"phone {build.serial}: {build.status}")
            else:
                # Claimed but never put on a device - the Gmail fetched just as
                # the budget ran out, the app account nothing was tried with,
                # the exit that was swapped away from. It is stock, and it goes
                # back as stock.
                pool.release(resource,
                             note=note or f"build ended: {build.status}")
        except SheetError as exc:
            log.error("%s: could not release %s (%s) - it stays in_use until "
                      "'geelark pools --release-stuck'",
                      pool.tab, resource.label, exc)


def _record(book: Book, sheet_row: int, build: Build) -> None:
    """Write the finished phone to the Phones tab. Also in a finally."""
    note = build.detail
    if build.tried:
        note = f"tried: {'; '.join(build.tried)}. {note}"
    try:
        book.phones.finish(
            sheet_row, Status=build.status, Proxy=build.proxy,
            Gmail=build.gmail, Note=note[:500],
            **{"GPT Account": build.app_account},
        )
    except SheetError as exc:
        log.error("could not record phone %s (%s)", build.phone_id, exc)


def run(client: Client, settings: Settings, *, count: int,
        workers: int | None = None, dry_run: bool = False,
        reporter: Reporter | None = None,
        on_ready: Callable[[str], None] | None = None,
        cancel: threading.Event | None = None) -> list[Build]:
    """Build `count` phones from the pools.

    `cancel` lets a caller on another thread stop the run. The console needs
    it: Ctrl+C is delivered to the main thread, which is drawing the table, and
    never reaches the worker running this - so without a signal to pass in, the
    interrupt left the build running as an orphan (2026-08-11).
    """
    book = Book.open(settings)

    if dry_run:
        print(f"{count} phone(s) would be built from:")
        for pool in (book.proxies, book.gmails, book.apps):
            print(f"  {pool.tab:<10} {len(pool.available):>3} available"
                  f"{f', {len(pool.stuck)} stuck in_use' if pool.stuck else ''}"
                  f"{f', {len(pool.broken)} unusable' if pool.broken else ''}")
        for pool in (book.proxies, book.gmails, book.apps):
            for resource in pool.broken:
                print(f"  ! {pool.tab} row {resource.sheet_row}: {resource.error}")
        print("\nNothing was created and nothing was written (--dry-run).")
        return []

    settings.ensure_dirs()
    ledger = Ledger.load(settings.state_dir)
    phones.prune_ledger(client, ledger)
    install_build_logging()

    started: set[str] = set()
    started_lock = threading.Lock()
    shutting_down = cancel if cancel is not None else threading.Event()

    def note_phone(phone_id: str) -> None:
        with started_lock:
            started.add(phone_id)

    def work(index: int) -> Build:
        _context.build = index
        if reporter:
            reporter.start(index, count)
        else:
            print(f"\n=== building phone {index}/{count} ===", flush=True)
        build = build_one(client, settings, book, ledger, index,
                          on_phone=note_phone, on_ready=on_ready,
                          cancelled=shutting_down.is_set)
        if reporter:
            reporter.finish(build)
        else:
            mark = "OK" if build.ok else "FAIL"
            print(f"  {build.name} {mark}: {build.status} "
                  f"({build.seconds:.0f}s)", flush=True)
        return build

    parallel = max(1, workers or settings.max_concurrent_phones)
    if on_ready and parallel > 1:
        log.info("--watch builds one phone at a time")
        parallel = 1
    parallel = min(parallel, count)

    if parallel == 1:
        builds: list[Build] = []
        try:
            for index in range(1, count + 1):
                builds.append(work(index))
        except KeyboardInterrupt:
            print("\ninterrupted - stopping here", flush=True)
            shutting_down.set()
            _stop_all(client, started, ledger)
        return builds

    log.info("%d phones, %d at a time", count, parallel)
    futures = {}
    try:
        with ThreadPoolExecutor(max_workers=parallel,
                                thread_name_prefix="build") as pool:
            futures = {pool.submit(work, i): i for i in range(1, count + 1)}
            wait(futures, return_when=FIRST_EXCEPTION)
    except KeyboardInterrupt:
        print("\ninterrupted - stopping every phone this run started", flush=True)
        shutting_down.set()
        for future in futures:
            future.cancel()
        _stop_all(client, started, ledger)

    builds = []
    for future, index in futures.items():
        if future.cancelled():
            continue
        try:
            builds.append(future.result())
        except Exception as exc:                                  # noqa: BLE001
            log.error("build %d raised: %s", index, exc)
            builds.append(Build(index=index, status="error", detail=str(exc)))
    builds.sort(key=lambda b: b.index)
    return builds


def finish_run(client: Client, settings: Settings, *, limit: int | None = None,
               workers: int | None = None, dry_run: bool = False,
               reporter: Reporter | None = None,
               cancel: threading.Event | None = None) -> list[Build]:
    """Complete every phone that is one step short, oldest first."""
    book = Book.open(settings)
    pending = book.phones.unfinished()
    live = {p.get("id") for p in phones.listing(client)}
    gone = [p for p in pending if p["phone_id"] not in live]
    pending = [p for p in pending if p["phone_id"] in live]
    if limit:
        pending = pending[:limit]

    if dry_run:
        print(f"{len(pending)} phone(s) would be finished:")
        for phone in pending:
            print(f"  phone {phone['serial']:<6} {phone['gmail']:<34} "
                  f"(stopped at {phone['status']})")
        if gone:
            print(f"\n{len(gone)} row(s) name a phone that no longer exists "
                  f"and are skipped: "
                  f"{', '.join(p['serial'] for p in gone)}")
        print(f"\napp accounts free: {len(book.apps.available)}")
        print("\nNothing was changed (--dry-run).")
        return []

    if gone:
        log.info("skipping %d row(s) whose phone no longer exists", len(gone))
    if not pending:
        return []

    settings.ensure_dirs()
    ledger = Ledger.load(settings.state_dir)
    install_build_logging()

    started: set[str] = set()
    started_lock = threading.Lock()
    shutting_down = cancel if cancel is not None else threading.Event()

    def note_phone(phone_id: str) -> None:
        with started_lock:
            started.add(phone_id)

    def work(index: int, phone: dict) -> Build:
        _context.build = index
        if reporter:
            reporter.start(index, len(pending))
        else:
            print(f"\n=== finishing phone {phone['serial']} "
                  f"({index}/{len(pending)}) ===", flush=True)
        build = finish_one(client, settings, book, ledger, phone, index,
                           on_phone=note_phone,
                           cancelled=shutting_down.is_set)
        if reporter:
            reporter.finish(build)
        else:
            mark = "OK" if build.ok else "FAIL"
            print(f"  {build.name} {mark}: {build.status} "
                  f"({build.seconds:.0f}s)", flush=True)
        return build

    parallel = min(max(1, workers or settings.max_concurrent_phones),
                   len(pending))
    if parallel == 1:
        builds: list[Build] = []
        try:
            for index, phone in enumerate(pending, start=1):
                builds.append(work(index, phone))
        except KeyboardInterrupt:
            print("\ninterrupted - stopping here", flush=True)
            shutting_down.set()
            _stop_all(client, started, ledger)
        return builds

    log.info("%d phones to finish, %d at a time", len(pending), parallel)
    futures = {}
    try:
        with ThreadPoolExecutor(max_workers=parallel,
                                thread_name_prefix="finish") as pool:
            futures = {pool.submit(work, i, p): p
                       for i, p in enumerate(pending, start=1)}
            wait(futures, return_when=FIRST_EXCEPTION)
    except KeyboardInterrupt:
        print("\ninterrupted - stopping every phone this run started", flush=True)
        shutting_down.set()
        for future in futures:
            future.cancel()
        _stop_all(client, started, ledger)

    builds = []
    for future, phone in futures.items():
        if future.cancelled():
            continue
        try:
            builds.append(future.result())
        except Exception as exc:                                  # noqa: BLE001
            log.error("finishing %s raised: %s", phone["serial"], exc)
            builds.append(Build(index=0, status="error", detail=str(exc),
                                serial=phone["serial"]))
    builds.sort(key=lambda b: b.index)
    return builds


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
        for note in b.tried:
            lines.append(f"          tried {note}")

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
        lines.append(" All phones are stopped; nothing is billing.")
    if ready < len(builds):
        lines.append(" The Phones tab records every phone, ready or not; the "
                     "resource tabs record why each credential failed.")
    return "\n".join(lines)
