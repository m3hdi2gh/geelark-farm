"""What one phone's build produced, and the words it is reported in: the
Build record, the capacity a run is sized by, the reporter protocol, and
the one-line and multi-line summaries.

Moved out of builder.py unchanged (the builder review, 2026-09-23). The
console, the CLI and the row writer read these; none of them needs the
build pipeline to do it. A leaf: stdlib, `failures`, `products`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from . import failures, products
from .failures import WARM_FOR_OPERATOR


@dataclass
class Build:
    """What one phone's construction produced, for the summary and the tab."""

    index: int
    ok: bool = False
    status: str = "not_started"
    #: The hand-built request this came from, when it came from one. The
    #: person who asked is watching a row on the dashboard, and this is what
    #: joins their row to what happened.
    wanted_id: int | None = None
    #: Who the phone was opened for, by user id, when it was asked for by
    #: hand. The row is `taken` by them while the build runs - that is
    #: what "Building - yours" reads off - and goes back on their shelf
    #: when it ends: off, still theirs, and bootable by them in one press
    #: (the operator, 2026-09-18). None for the keeper's own phones.
    built_for: int | None = None
    phone_id: str = ""
    serial: str = ""
    proxy: str = ""
    #: What the Proxy tab calls that exit - `SX4`. The Phones tab records this
    #: rather than the address: it is the string you search the vendor's panel
    #: with, and the address is already one column away in the Proxy tab.
    proxy_name: str = ""
    gmail: str = ""
    #: "brand model" as GeeLark reported it, for the sign-in record.
    model: str = ""
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
    #: Which app this phone carries: '' for none, 'chatgpt', 'spotify'.
    app: str = ""
    app_account: str = ""
    #: Which product the account on it is for - what the good-build
    #: sentence names, since 2026-09-17 a Spotify phone is not "signed
    #: into ChatGPT". Empty when nothing is signed in.
    app_product: str = ""
    detail: str = ""
    seconds: float = 0.0
    #: GeeLark requests this build sent, for the count that decides how
    #: many phones may be built at once against the 200-a-minute limit.
    api_calls: int = 0
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


#: The apps a phone can be built with, and what each is called on a page.
#: Read off `products`, which owns everything the farm knows about each
#: app (the builder review, 2026-09-23); these names stay for the code
#: and the tests that read them here.
APPS = {key: spec.name for key, spec in products.PRODUCTS.items()}


def _mark(build) -> str:
    """OK, FAIL - or WARM: a phone kept warm on purpose is not a failure,
    and read as one in the log for a day (2026-09-08)."""
    if build.ok:
        return "OK"
    return "WARM" if build.status == WARM_FOR_OPERATOR else "FAIL"


def outcome_of(build: Build) -> str:
    """Why this phone ended where it did, as one lowercase clause.

    Shared with the console, which lays the same facts out over several lines
    rather than in one sentence. Two renderings of one build used to be two
    descriptions of it: the tab said what happened and the console printed
    `no_usable_gpt`, which is the token this file spent a day removing from
    everywhere else.
    """
    if build.ok:
        # From the facts, not one sentence for every good build: a bare
        # phone has no Google account, and a Spotify phone no ChatGPT
        # account, and the one sentence said both of a bare Spotify phone
        # (3517, 2026-09-17).
        product = APPS.get(build.app_product or "chatgpt", "the app")
        if not build.gmail:
            return ("a bare phone - no Google account" + (
                f", with {build.app_account} signed into {product}"
                if build.app_account else ", nothing signed in"))
        if build.app_account:
            return f"signed into Google, and into {product} in the app"
        # Ready with no app account: a phone asked for without one, or with
        # Spotify or Claude on it and nobody to sign in. It said "and into
        # ChatGPT in the app" of every one of them (the builder review,
        # 2026-09-23).
        on = products.named(build.app) if build.app else ""
        return ("signed into Google, with no app account"
                + (f" - {on} on the phone" if on else ""))
    return build.detail or failures.situation(build.status)


def attempts_of(build: Build) -> list[str]:
    """Every credential this build gave up on, one readable line each."""
    return [f"{email} - {failures.verdict(reason, service).seen}"
            for email, reason, service in build.tried]


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
