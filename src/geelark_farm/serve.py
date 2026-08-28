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
from .pools import Book

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
                accounts_waiting: int) -> bool:
    """Whether this pass's decision turns on how many slots are free.

    The same numbers `decide` reaches its first three answers from, asked
    again here for one reason: the number it does not have costs a call to an
    endpoint that allows one a minute, and most passes never look at it.
    """
    if accounts_waiting and warm:
        return False              # finishing, which takes no new slot
    if tripped:
        return False              # not building anyway
    return warm < target


@dataclass
class Decision:
    """What one pass should do, and why it is not doing the rest."""

    finish: bool = False
    build: bool = False
    #: Said out loud when there is something a person has to do about it.
    warning: str = ""

    @property
    def idle(self) -> bool:
        return not self.finish and not self.build


def decide(*, tripped: str, warm: int, target: int, free_slots: int | None,
           accounts_waiting: int) -> Decision:
    """What to do this pass, from five numbers and nothing else.

    `tripped` is the breaker's reason, empty when it is closed. `free_slots`
    is None when nobody has managed to read it - which only matters on the one
    branch that turns on it, and which is not a reason to build blind.
    """
    if accounts_waiting and warm:
        # Somebody is waiting at the end of this one, and it spends nothing
        # that has not already been bought. It happens even with the breaker
        # open: what tripped the breaker was building, and this is not that.
        return Decision(finish=True)

    if tripped:
        return Decision(warning=tripped)

    if accounts_waiting and not warm:
        # An account arrived and there is no phone to put it on. Building is
        # the answer and the branch below is already about to do it; this is
        # only worth a line because it is the case the warm stock exists to
        # prevent, and seeing it means the stock is not keeping up.
        log.info("an account is waiting and no warm phone is ready for it")

    if warm >= target:
        return Decision()

    if free_slots is None:
        # Building without knowing is how you meet [44002] at phone creation,
        # having already spent the reads that got you there. One pass costs
        # less than one blind build.
        return Decision(warning=(
            "how many profile slots are free could not be read, so nothing "
            "is being built this pass rather than built blind."))

    if free_slots < 1:
        return Decision(warning=(
            f"no free profile slots, so the warm stock is stuck at {warm} of "
            f"{target}. A finished phone holds its slot until somebody marks "
            f"it done in the State column - that is what frees one."))

    return Decision(build=True)


def _look(client: Client, settings: Settings, book: Book) -> tuple[int, int]:
    """Warm phones, and accounts with nowhere to go yet.

    Not the free slots. Those cost a call to an endpoint with a limit of one a
    minute, and the two numbers here settle most passes without them.
    """
    from . import builder

    warm, _gone = builder._unfinished(client, book)
    return len(warm), len(book.apps.available)


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


def healthy(settings: Settings, now: float | None = None) -> tuple[bool, str]:
    """Whether a pass has happened recently enough, and what to say."""
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
    return True, f"a pass began {since / 60:.0f} minute(s) ago"


def probe_due(last: float | None, now: float,
              every: float = PROBE_EVERY_SECONDS) -> bool:
    """Whether the exits are due a re-test.

    `None` is "not since this process started", which is due: a service that
    has just come back is the one case where the exits may well have changed
    while nothing was watching.
    """
    return last is None or (now - last) >= every


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

    warm, waiting = _look(client, settings, book)
    tripped = fuse.reason()
    # Asked only when the answer changes what happens, which is the pass that
    # is about to build. A full stock, a waiting account or an open breaker
    # all settle it without ever looking.
    free = (slots.look(client, time.monotonic())
            if needs_slots(tripped=tripped, warm=warm,
                           target=settings.warm_stock,
                           accounts_waiting=waiting)
            else None)
    decision = decide(tripped=tripped, warm=warm,
                      target=settings.warm_stock, free_slots=free,
                      accounts_waiting=waiting)
    # The numbers go beside the sentence as well as inside it. On the console
    # this reads as prose; in a JSON log file they are fields something can
    # count without matching on the wording, which is what makes an alarm on
    # "the stock has been short for an hour" possible at all.
    log.info("%d warm of %d, %s free slot(s), %d account(s) waiting",
             warm, settings.warm_stock,
             free if free is not None else "not asked about", waiting,
             extra={"warm": warm, "target": settings.warm_stock,
                    "free_slots": free, "accounts_waiting": waiting,
                    "will": ("finish" if decision.finish else
                             "build" if decision.build else "nothing")})
    if decision.warning:
        log.warning("%s", decision.warning)

    if decision.finish:
        for build in builder.finish_run(client, settings, limit=1):
            fuse.record(build)
    if decision.build:
        for build in builder.run(client, settings, count=1):
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
            log.exception("a pass failed; carrying on to the next one")
        done += 1
        if stop.is_set() or (passes is not None and done >= passes):
            break
        sleep(settings.serve_interval_seconds)
    log.info("stopped after %d pass(es)", done)
    return 0
