"""The screen router: read what is on the device, act, repeat.

Extracted when a third flow needed it. The loop below is not long, but every
line of it is a lesson paid for by a live run - the unknown streak that stops a
mid-animation dump from being reported as an unrecognised page, the visit
allowance that catches an action having no effect, the archiving of every screen
the first time it is seen. Two copies of that would drift, and the copy that
drifted would be the one nobody was watching.

What stays in each flow is the part that differs: which screens exist, what to
do about them, and what counts as done. `is_done` is passed in rather than
assumed, because the whole discipline of this project rests on it being
something the device can be asked - not something the screen claims.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import screen, shell
from ..api import Client

log = logging.getLogger(__name__)


@dataclass
class Outcome:
    """Why the flow stopped. `ok` is the only success, and it is always backed
    by the device, never by what the screen said."""

    kind: str                 # success | fatal | unknown | budget
    reason: str
    detail: str = ""
    artifacts: list[str] = field(default_factory=list)
    #: The screens this flow walked, in order, repeats and all. Stamped by
    #: `drive` on whatever it returns, so every flow carries one without
    #: having to remember to. Empty on an outcome decided before the loop ran
    #: - `app_not_installed` never saw a screen, and says so by having none.
    trail: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind == "success"

    def __str__(self) -> str:
        text = f"{self.kind}:{self.reason}"
        return f"{text} - {self.detail}" if self.detail else text


@dataclass
class Context:
    """Everything a screen action needs, refreshed each iteration."""

    client: Client
    phone_id: str
    elements: list[screen.Element] = field(default_factory=list)
    blob: str = ""
    raw: str = ""
    artifact_dir: Path | None = None
    seen: dict[str, int] = field(default_factory=dict)
    saved: list[str] = field(default_factory=list)
    #: Every screen as it was reached, in order. `seen` cannot answer this:
    #: it counts visits per name, so `A > B > A > B` and `A > A > B > B` are
    #: the same dictionary, and telling a loop from a straight run is most of
    #: what reading one of these is for.
    trail: list[str] = field(default_factory=list)

    def refresh(self) -> None:
        xml = screen.capture(self.client, self.phone_id)
        self.raw = xml or ""
        self.elements = screen.parse(xml) if xml else []
        self.blob = screen.texts(self.elements)

    def find(self, label: str) -> screen.Element | None:
        return screen.find(self.elements, label)

    def tap(self, label: str) -> bool:
        return screen.tap_label(self.client, self.phone_id, self.elements, label)

    def has(self, *needles: str) -> bool:
        return any(n.casefold() in self.blob for n in needles)

    def save(self, name: str) -> str | None:
        """Archive the current screen so an unrecognised page can become a
        registry entry rather than a mystery."""
        if not self.artifact_dir:
            return None
        stamp = time.strftime("%H%M%S")
        path = self.artifact_dir / f"{stamp}-{name}.xml"
        screen.save_fixture(self.raw, path)
        self.saved.append(str(path))
        return str(path)


@dataclass
class Screen:
    """One recognisable page and what to do about it."""

    name: str
    match: Callable[[Context], bool]
    act: Callable[[Context], Outcome | None]
    # A screen that repeats this many times without the flow progressing is
    # stuck: the action is not having the effect it assumes.
    max_visits: int = 4


def fill(ctx: Context, element: screen.Element, text: str) -> bool:
    """Focus a field and type into it, replacing anything already there.

    The result is read back and corrected once. A field that does not end up
    holding what was typed is the worst kind of quiet failure here: the form
    submits, the service rejects it, the flow sees the same page and tries
    again - and each attempt is an attempt against a real account. One email
    box grew "com" on every pass until it read `...@gmail.comcomcom`, four
    submissions later (2026-08-08, row 7).

    Password fields are exempt: they report dots, so there is nothing to
    compare against.
    """
    if not screen.tap_element(ctx.client, ctx.phone_id, element):
        return False
    time.sleep(1)
    if element.text:
        shell.clear_field(ctx.client, ctx.phone_id, max_chars=len(element.text) + 4)
    shell.type_text(ctx.client, ctx.phone_id, text)
    time.sleep(1)

    if element.password:
        return True
    actual = _typed_value(ctx, element)
    if actual is None or actual == text:
        return True

    log.warning("the field holds %r after typing %r; clearing it properly and "
                "trying once more", actual, text)
    field = screen.find_input(ctx.elements, password=False)
    if field is None or not screen.tap_element(ctx.client, ctx.phone_id, field):
        return False
    # Generously: the point of a second attempt is not to be precise about how
    # much is in there, and deleting past the start of a field costs nothing.
    shell.clear_field(ctx.client, ctx.phone_id, max_chars=len(actual) + len(text))
    shell.type_text(ctx.client, ctx.phone_id, text)
    time.sleep(1)
    return True


def _typed_value(ctx: Context, element: screen.Element) -> str | None:
    """What the field just typed into holds now, or None if it cannot be read.

    Matched on bounds, which is what tells one box from another on a page
    with more than one: a field that was not scrolled is in the same place a
    second later. `element` was passed in from the start and then ignored -
    the read-back took whatever `find_input` returned first, which is the
    focused field and therefore usually right, and usually is not the standard
    this check exists to hold. It is the check that caught
    `...@gmail.comcomcom`, so reading the wrong box makes it worse than
    useless: it compares one field's contents against another's and corrects
    the one it can see.
    """
    ctx.refresh()
    same_box = next((e for e in ctx.elements
                     if e.is_input and not e.password
                     and e.bounds and e.bounds == element.bounds), None)
    if same_box is not None:
        return same_box.text
    # Moved, or the page re-rendered it somewhere else. The focused field is
    # the better guess than nothing, and is what this always used.
    field = screen.find_input(ctx.elements, password=False)
    return field.text if field is not None else None


def still_loading(ctx: Context) -> bool:
    """Whether the page is mid-navigation.

    A ProgressBar node is what an unfinished page looks like, and the same
    screen a moment later has none. Acting on the page underneath one is acting
    on the wrong screen - see act_wait.
    """
    return any("ProgressBar" in e.cls for e in ctx.elements)


def act_wait(ctx: Context) -> Outcome | None:
    """Do nothing, on purpose.

    The page the spinner belongs to has not arrived yet. Row 13 tapped NEXT,
    Google began loading, and the flow read the email page still showing behind
    the spinner - so it retyped the address and tapped NEXT again, four times,
    and reported stuck_on_email_entry having never left the first screen
    (2026-08-06). Watching it live, it was simply slow.
    """
    log.info("the page is still loading; waiting")
    time.sleep(4)
    return None


def drive(ctx: Context, screens: list[Screen], *,
          is_done: Callable[[], Outcome | None],
          budget_seconds: float,
          logger: logging.Logger | None = None) -> Outcome:
    """`_drive`, with the path it walked attached to whatever comes back.

    A wrapper rather than a line before each `return`: the loop below has five
    of them and a sixth would be added one day without this. The path is worth
    having on every one - a success is the shape a healthy run has, which is
    what makes a failure's shape readable.
    """
    outcome = _drive(ctx, screens, is_done=is_done,
                     budget_seconds=budget_seconds, logger=logger)
    outcome.trail = list(ctx.trail)
    return outcome


def _drive(ctx: Context, screens: list[Screen], *,
           is_done: Callable[[], Outcome | None],
           budget_seconds: float,
           logger: logging.Logger | None = None) -> Outcome:
    """Run the loop until something conclusive happens.

    Returns rather than raises: a batch needs to record why a row failed and
    move on, not unwind.
    """
    out = logger or log
    if ctx.artifact_dir:
        ctx.artifact_dir.mkdir(parents=True, exist_ok=True)

    # monotonic, not the wall clock: a deadline measured on a clock
    # something else can set is a deadline that moves. An NTP
    # correction or a host resuming from suspend shortens or extends
    # every budget in the process, and a service that stays up for
    # weeks is where that stops being theoretical.
    deadline = time.monotonic() + budget_seconds
    unknown_streak = 0

    while time.monotonic() < deadline:
        # Device truth first: the only definition of success.
        finished = is_done()
        if finished:
            return finished

        ctx.refresh()
        if not ctx.elements:
            out.info("screen is empty; waiting")
            time.sleep(5)
            continue

        matched = next((s for s in screens if s.match(ctx)), None)
        if matched is None:
            # Could be a transition. Look again before giving up, since a dump
            # taken mid-animation legitimately matches nothing.
            unknown_streak += 1
            if unknown_streak < 3:
                time.sleep(4)
                continue
            path = ctx.save("unknown-screen")
            labels = [e.label for e in ctx.elements if e.label][:12]
            return Outcome("unknown", "unknown_screen",
                           f"nothing matched; on screen: {labels}",
                           artifacts=[path] if path else [])
        unknown_streak = 0

        visits = ctx.seen.get(matched.name, 0) + 1
        ctx.seen[matched.name] = visits
        if visits > matched.max_visits:
            path = ctx.save(f"stuck-{matched.name}")
            return Outcome("unknown", f"stuck_on_{matched.name}",
                           f"handled {visits} times without progress",
                           artifacts=[path] if path else [])

        ctx.trail.append(matched.name)
        out.info("screen: %s (visit %d)", matched.name, visits)
        if visits == 1:
            # Archive each page the first time it is seen, so every run leaves
            # a record of the path it took without anyone having to ask.
            ctx.save(matched.name)
        outcome = matched.act(ctx)
        if outcome:
            return outcome

    path = ctx.save("budget-exhausted")
    return Outcome("budget", "budget_exhausted",
                   f"no result within {budget_seconds:.0f}s; "
                   f"screens seen: {dict(ctx.seen)}",
                   artifacts=[path] if path else [])
