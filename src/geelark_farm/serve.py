"""Run the thing continuously instead of typing it.

What a person does today is: keep some phones built to one step short of
ready, and complete one the moment an account turns up. Neither half is new -
`build` against an empty `Gpt Info` tab produces exactly that phone, and
`finish` completes it without spending another phone, Gmail or proxy. This is
the loop around them, and the judgement about what to do on each pass.

The judgement is `decide`, which is a pure function of five numbers. It is
separate from everything that talks to GeeLark on purpose: what this service
should do next is the part worth being sure about, and it can be argued with
in a test that has no network, no sheet and no clock.

Three things it is careful about, all of which cost money to get wrong:

**A tripped breaker stops building, not everything.** Finishing spends nothing
new - the phone, the Gmail and the exit are already bought - and a customer
waiting on an account is the one thing that should still happen while somebody
works out why the last five builds failed.

**Slots are read before building, not discovered at [44002].** A finished
phone holds its slot until a person marks it delivered, so a run of
undelivered phones is what runs the plan out of room. That is worth saying in
words rather than as an API error, because the fix is a person marking rows
and nothing here can do it.

They are read sparingly, though, and that took a deployment to learn. The
endpoint allows one call a minute on a budget of its own, this loop runs every
thirty seconds, and asking every pass meant every other pass died of [40007] -
losing the sync with it. So the count is asked for only on the pass that is
about to build, and then no oftener than every five minutes; a full stock, a
waiting account or an open breaker each settle the pass without ever looking.

**Finishing comes before topping up.** Both want the same pass; only one has
somebody waiting at the end of it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from . import phones
from .api import Client, build_client
from .breaker import Breaker
from .config import Settings
from .ledger import Ledger
from .pools import Book, Pool

log = logging.getLogger(__name__)

#: What the breaker's count is kept in, under `state/`.
BREAKER_FILE = "breaker.json"

#: Touched at the start of every pass, and read by `--healthcheck`.
#:
#: `restart: always` brings back a process that died. It does nothing at all
#: for one that is alive and stuck - a socket with no timeout, a lock nobody
#: releases - and from outside those two look identical. This is the
#: difference.
HEARTBEAT_FILE = "heartbeat"

#: Where the count of consecutive failed passes lives, and how many in a row
#: mean the service is not working.
#:
#: The heartbeat says a pass *began*; this says whether any of them got
#: through. Five at the default interval is two and a half minutes, which is
#: short enough to catch a revoked key on the same afternoon and long enough
#: that a single flaky call does not raise an alarm.
FAILING_FILE = "failed-passes"
FAILING_LIMIT = 5

#: How often the exits are re-tested.
#:
#: Measured before this existed: a pass made 43 GeeLark calls in 37 seconds
#: and 34 of them were `/v1/proxy/check` - one live connection per exit, every
#: thirty seconds, to answer a question whose answer changes on the scale of
#: days. It is still worth asking, because a dead exit found here is a build
#: that does not fail later; it is not worth asking 120 times an hour.
PROBE_EVERY_SECONDS = 3600

#: How often the free-slot count is read.
#:
#: `/v1/pay/plan/info` allows one call a minute on a budget of its own,
#: separate from the account's 200. This loop asked every pass, so every other
#: pass raised [40007] and died - and a pass that dies loses the sync too, so a
#: phone somebody had marked done waited an extra cycle to be deleted. Three of
#: the first six passes on the server went that way (2026-08-28).
#:
#: Five minutes is seven times under the limit and still fresh enough: a slot
#: frees when a phone is deleted, which is a delivery, not a second.
PLAN_EVERY_SECONDS = 300


@dataclass
class Slots:
    """How many profile slots are free, asked no oftener than that changes.

    Kept across passes because the answer stays true between them, and because
    the endpoint that gives it is rate-limited on its own.
    """

    every: float = PLAN_EVERY_SECONDS
    free: int | None = None
    read_at: float | None = None

    def look(self, client: Client, now: float) -> int | None:
        """The count, reading it again only when it is old enough to."""
        if self.read_at is not None and (now - self.read_at) < self.every:
            return self.free
        self.read_at = now
        try:
            self.free = int(phones.plan(client).get("availableProfiles") or 0)
        except Exception as exc:                                  # noqa: BLE001
            # The last answer, or None if there has never been one. Waiting
            # out the interval before asking again rather than retrying next
            # pass: what this exists to avoid is asking too often.
            log.warning("could not read how many slots are free (%s); "
                        "carrying on with %s", exc,
                        "the last answer" if self.free is not None
                        else "no answer at all")
        return self.free


def needs_slots(*, tripped: str, warm: int, target: int,
                accounts_waiting: int, cap: int = 1) -> bool:
    """Whether this pass's decision turns on how many slots are free.

    The same numbers `decide` reaches its first answers from, asked again here
    for one reason: the number it does not have costs a call to an endpoint
    that allows one a minute, and most passes never look at it.

    It used to answer False whenever anything was being finished, because a
    pass did one job and a finishing pass was therefore not a building one.
    With a cap above 1 that is no longer true - a pass can finish two and build
    three - and the old shortcut left `free_slots` at None on exactly those
    passes, which `decide` then refuses to build blind on. Every combined pass
    would have finished and then declined to build, for ever.
    """
    if tripped:
        return False              # not building anyway
    to_finish = min(accounts_waiting, warm, cap)
    if cap - to_finish < 1:
        return False              # no room left to build this pass
    return (warm - to_finish) < target


@dataclass
class Decision:
    """How much of each thing this pass should do, and why not the rest.

    Counts rather than flags. They were booleans while a pass did one job, and
    one job a pass meant ten phones took ten passes - about seventy minutes of
    wall clock for work the builder can already run at once. The machinery was
    never the limit: `_drive_jobs` has had a thread pool the whole time and has
    run twenty phones ten at a time in production (2026-08-25). What was
    missing was a caller that asked for more than one.
    """

    finish: int = 0
    build: int = 0
    #: Said out loud when there is something a person has to do about it.
    warning: str = ""

    @property
    def idle(self) -> bool:
        return not self.finish and not self.build

    @property
    def jobs(self) -> int:
        return self.finish + self.build


def decide(*, tripped: str, warm: int, target: int, free_slots: int | None,
           accounts_waiting: int, cap: int = 1,
           gmails: int | None = None, exits: int | None = None) -> Decision:
    """How much to do this pass, from the numbers and nothing else.

    `tripped` is the breaker's reason, empty when it is closed. `free_slots`
    is None when nobody has managed to read it - which only matters on the
    branches that turn on it, and which is not a reason to build blind.

    `cap` is the most jobs one pass may run at once. It defaults to 1, which is
    what this did for its whole life: one job a pass, ten phones in ten passes.

    `gmails` and `exits` are how deep the pools are. None means "not counted,
    do not constrain" - a caller that does not know must not be second-guessed
    here. Counted, they stop a pass asking for four builds against a two-
    address tab: the two surplus builds spend a claim and a live proxy check
    each, create nothing, end `no_usable_gmail` - and that reason is in
    `breaker.NOTHING_HAPPENED`, so nothing anywhere counts them.

    The order below is load-bearing and unchanged. Finishing is settled first,
    above the breaker, because it spends nothing new and somebody is waiting at
    the end of it; what tripped the breaker was building, and this is not that.
    """
    to_finish = min(accounts_waiting, warm, cap)

    if tripped:
        return Decision(finish=to_finish, warning=tripped)

    if accounts_waiting and not warm:
        # An account arrived and there is no phone to put it on. Building is
        # the answer and the arithmetic below is already about to do it; this
        # is only worth a line because it is the case the warm stock exists to
        # prevent, and seeing it means the stock is not keeping up.
        log.info("an account is waiting and no warm phone is ready for it")

    room = cap - to_finish
    # A phone that gets finished stops being warm, so the hole to fill is
    # measured after this pass's finishes, not before them.
    short = target - (warm - to_finish)
    if room < 1 or short < 1:
        return Decision(finish=to_finish)

    if free_slots is None:
        # Building without knowing is how you meet [44002] at phone creation,
        # having already spent the reads that got you there. One pass costs
        # less than one blind build.
        return Decision(finish=to_finish, warning=(
            "how many profile slots are free could not be read, so nothing "
            "is being built this pass rather than built blind."))

    if free_slots < 1:
        return Decision(finish=to_finish, warning=(
            f"no free profile slots, so the warm stock is stuck at {warm} of "
            f"{target}. A finished phone holds its slot until somebody marks "
            f"it done in the State column - that is what frees one."))

    limits = [short, room, free_slots]
    if gmails is not None:
        limits.append(gmails)
    if exits is not None:
        limits.append(exits)
    to_build = min(limits)

    if to_build < 1:
        return Decision(finish=to_finish, warning=(
            f"the warm stock is {warm} of {target} and nothing can be built: "
            f"the Gmails or Proxy tab has no usable row left."))

    return Decision(finish=to_finish, build=to_build)


def _look(client: Client, settings: Settings,
          book: Book) -> tuple[int, int, int, int]:
    """Warm phones, accounts with nowhere to go yet, and how deep the pools are.

    Not the free slots. Those cost a call to an endpoint with a limit of one a
    minute, and the numbers here settle most passes without them.

    The pool depths are free: the tabs are already loaded and `available` is a
    list comprehension over rows in memory. They exist so a pass that may ask
    for several builds cannot ask for more than there is stock to make - four
    builds against a two-address tab spends two claims and two live proxy
    checks to create nothing, and ends on a reason the breaker ignores.
    """
    from . import builder

    warm, _gone = builder._unfinished(client, book)
    return (len(warm), len(book.apps.available),
            len(book.gmails.available), len(book.proxies.available))


def beat(settings: Settings) -> None:
    """Say that a pass has begun. Never fatal: a service that cannot write
    here should carry on and look unhealthy, not stop."""
    try:
        path = settings.state_dir / HEARTBEAT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")
    except OSError as exc:
        log.warning("could not touch the heartbeat (%s)", exc)


def stale_after(settings: Settings) -> float:
    """How long without a pass means something is wrong.

    Derived rather than configured, because the honest answer follows from
    two numbers that are already set. A pass that is building legitimately
    takes as long as a build is allowed to, and then the next one waits out
    the interval; anything past both, twice over, is not a slow pass.
    """
    return 2 * (settings.build_budget_seconds + settings.serve_interval_seconds)


def note_pass(settings: Settings, *, ok: bool) -> int:
    """Record whether a pass got through, and answer how many have not.

    The heartbeat alone cannot see this. It is stamped before the pass is
    attempted - deliberately, so a pass that hangs still shows as stale - which
    means a pass that *throws* stamps it just as a pass that works does. A
    service whose every pass died on the first call therefore looked healthy
    forever: `restart: always` never fires, `docker ps` keeps saying healthy,
    and the operator, who reads only the sheet, sees a tab that has gone quiet
    and cannot tell it from a loop that is correctly idle (2026-08-28).

    On disk rather than in memory because the healthcheck is a second process:
    `geelark serve --healthcheck` is run by Docker, not by the loop.

    Never fatal, for the same reason `beat` is not.
    """
    path = settings.state_dir / FAILING_FILE
    try:
        if ok:
            path.unlink(missing_ok=True)
            return 0
        # No file and an unreadable one mean the same thing here - nothing is
        # known against the loop yet - which is what `_failing` already answers.
        failed = _failing(settings) + 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(failed), encoding="utf-8")
        return failed
    except OSError as exc:
        log.warning("could not record how the pass went (%s)", exc)
        return 0


def _failing(settings: Settings) -> int:
    """How many passes in a row have thrown."""
    try:
        return int((settings.state_dir / FAILING_FILE)
                   .read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def healthy(settings: Settings, now: float | None = None) -> tuple[bool, str]:
    """Whether the service is doing its job, and what to say.

    Two different failures, and the heartbeat only sees the first: a loop that
    has stopped or hung, and a loop that is running perfectly on time while
    every pass dies. The second is the one that used to report healthy.
    """
    failed = _failing(settings)
    if failed >= FAILING_LIMIT:
        return False, (f"{failed} passes in a row have failed - the loop is "
                       f"running but nothing is getting through")

    path = settings.state_dir / HEARTBEAT_FILE
    try:
        last = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        # No pass has finished starting yet. On a container that has just come
        # up this is the truth and not a fault, which is what `start_period`
        # in the healthcheck is for.
        return False, "no pass has run yet"
    since = (now if now is not None else time.time()) - last
    limit = stale_after(settings)
    if since > limit:
        return False, (f"the last pass began {since / 60:.0f} minutes ago, "
                       f"past the {limit / 60:.0f} this should ever take")
    said = f"a pass began {since / 60:.0f} minute(s) ago"
    return True, f"{said} ({failed} failed in a row)" if failed else said


def probe_due(last: float | None, now: float,
              every: float = PROBE_EVERY_SECONDS) -> bool:
    """Whether the exits are due a re-test.

    `None` is "not since this process started", which is due: a service that
    has just come back is the one case where the exits may well have changed
    while nothing was watching.
    """
    return last is None or (now - last) >= every


def _show(book: Book, settings: Settings, decision: Decision, *, warm: int,
          waiting: int, free: int | None, tripped: str, failed: int) -> None:
    """Put this pass's state where the operator can see it.

    Every number here is already in the log, and the log is on a server the
    operator does not read. This is the same pass, said on the spreadsheet.

    Guarded rather than assumed: a `build` typed by hand has no dashboard, and
    a workbook the tab could not be made in still has to run.
    """
    if book.service is None:
        return
    from . import __version__
    from .config import machine, revision

    stamp = revision()
    # Both halves, because a pass can now do both at once and a reader who is
    # told only one of them will wonder where the other phones went.
    busy = []
    if decision.finish:
        busy.append(f"finishing {decision.finish} for waiting account(s)")
    if decision.build:
        busy.append(f"building {decision.build} warm phone(s)")
    doing = " and ".join(busy) or "nothing to do"
    note = decision.warning
    if failed:
        note = (f"{failed} pass(es) in a row have failed - see the log. "
                f"{note}").strip()
    book.service.show(**{
        # `Pool`'s format, not `book.phones`'s: PhoneLog is not a Pool and has
        # no CLAIM_FORMAT. A test fake invented one, which is how this reached
        # the server and threw on every pass until the sheet said so
        # (2026-08-28).
        "Last pass": time.strftime(Pool.CLAIM_FORMAT),
        "Machine": machine(),
        "Version": f"{__version__} ({stamp})" if stamp else __version__,
        "Doing": doing,
        "Warm stock": f"{warm} of {settings.warm_stock}",
        "Accounts waiting": str(waiting),
        "Free slots": "not asked this pass" if free is None else str(free),
        "Breaker": tripped or "closed",
        "Note": note,
    })


def once(client: Client, settings: Settings, fuse: Breaker, slots: Slots, *,
         probe_proxies: bool = True) -> Decision:
    """One pass: bring the sheet up to date, then act on what it says."""
    from . import builder

    book = Book.open(settings)
    ledger = Ledger.load(settings.state_dir)
    # The same call a person's run makes, so the two cannot disagree about
    # what the sheet means. This is also what carries out the State column -
    # a phone marked done is deleted here and its slot comes back.
    builder.sync_sheet(client, book, ledger,
                       probe_proxies=probe_proxies,
                       artifact_dir=settings.artifact_dir,
                       stale_claim_seconds=settings.stale_claim_seconds)
    book.reload()

    warm, waiting, gmails, exits = _look(client, settings, book)
    tripped = fuse.reason()
    cap = max(1, settings.max_concurrent_phones)
    # Asked only when the answer changes what happens, which is a pass with
    # room to build. A full stock or an open breaker settle it without looking.
    free = (slots.look(client, time.monotonic())
            if needs_slots(tripped=tripped, warm=warm,
                           target=settings.warm_stock,
                           accounts_waiting=waiting, cap=cap)
            else None)
    decision = decide(tripped=tripped, warm=warm,
                      target=settings.warm_stock, free_slots=free,
                      accounts_waiting=waiting, cap=cap,
                      gmails=gmails, exits=exits)
    # The numbers go beside the sentence as well as inside it. On the console
    # this reads as prose; in a JSON log file they are fields something can
    # count without matching on the wording, which is what makes an alarm on
    # "the stock has been short for an hour" possible at all.
    log.info("%d warm of %d, %s free slot(s), %d account(s) waiting",
             warm, settings.warm_stock,
             free if free is not None else "not asked about", waiting,
             extra={"warm": warm, "target": settings.warm_stock,
                    "free_slots": free, "accounts_waiting": waiting,
                    "gmails_free": gmails, "exits_free": exits,
                    "to_finish": decision.finish, "to_build": decision.build,
                    "will": ("finish" if decision.finish else
                             "build" if decision.build else "nothing")})
    if decision.warning:
        log.warning("%s", decision.warning)
    # Written before the work rather than after it, so a build that takes four
    # minutes reads as `building` for those four minutes instead of leaving the
    # tab on the last thing that finished.
    _show(book, settings, decision, warm=warm, waiting=waiting, free=free,
          tripped=tripped, failed=_failing(settings))

    if decision.jobs:
        # One call, one Book, one runner - never `finish_run` and `run` as two
        # concurrent calls. Each opens its own Book, and `Pool`'s claim lock is
        # per instance: two Books have two locks, and the serialisation that
        # stops one Gmail reaching two phones stops holding.
        #
        # `finish_limit` says exactly how many of these jobs are finishes.
        # Without it `count` is a total that finishing eats first, so a pass
        # asking for two finishes and three builds would get five finishes -
        # and the three with no account to use would each boot a real phone,
        # end `no_usable_gpt`, and put it back. That is the 2026-08-28
        # deadlock, once per surplus job, and `no_usable_gpt` is in
        # `breaker.WORKED`, so nothing would count it.
        #
        # `workers` must be passed. Left to its default `_drive_jobs` falls
        # back to `max_concurrent_phones`, and a pass of ten jobs would run
        # them one after another inside one pass - seventy minutes instead of
        # fifteen, which is the opposite of the point.
        for build in builder.run(client, settings,
                                 count=decision.jobs,
                                 finish_limit=decision.finish,
                                 workers=decision.jobs,
                                 finish_first=bool(decision.finish)):
            fuse.record(build)
    return decision


def run(settings: Settings, *, stop: threading.Event | None = None,
        passes: int | None = None, sleep=time.sleep) -> int:
    """Keep going until something stops it.

    `stop` is how a signal reaches it, `passes` is how a test reaches an end,
    and `sleep` is injectable so a test does not spend the interval waiting
    for it.
    """
    settings.ensure_dirs()
    client = build_client(settings)
    fuse = Breaker(settings.state_dir / BREAKER_FILE)
    # Kept across passes: the count stays true between them, and the
    # endpoint that gives it allows one call a minute.
    slots = Slots()
    stop = stop or threading.Event()

    log.info("serving: %d warm phones, a pass every %ds",
             settings.warm_stock, settings.serve_interval_seconds)
    done = 0
    probed: float | None = None
    while not stop.is_set() and (passes is None or done < passes):
        now = time.monotonic()
        probe = probe_due(probed, now)
        if probe:
            probed = now
        beat(settings)
        try:
            once(client, settings, fuse, slots, probe_proxies=probe)
        except KeyboardInterrupt:
            raise
        except Exception:                                     # noqa: BLE001
            # A pass that dies must not take the service with it. The next one
            # begins by syncing the sheet, which is also how it recovers from
            # whatever the last one left half-done.
            #
            # But carrying on quietly forever is its own failure: counted here
            # so the healthcheck can tell "running" from "working".
            failed = note_pass(settings, ok=False)
            log.exception("a pass failed (%d in a row); carrying on to the "
                          "next one", failed)
        else:
            note_pass(settings, ok=True)
        done += 1
        if stop.is_set() or (passes is not None and done >= passes):
            break
        sleep(settings.serve_interval_seconds)
    log.info("stopped after %d pass(es)", done)
    return 0
