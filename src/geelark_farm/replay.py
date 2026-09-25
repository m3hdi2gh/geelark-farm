"""Run a flow over screens it has already met. No phone, no cost.

The third of the three tools, and the one that pays for the other two.
A correction to a flow costs a phone boot, a live run and real minutes;
over a recording it costs under a second, so a flow can be wrong fifty
times before it ever meets a device.

What a replay proves is exactly what a flow's own code decides: which
screen was recognised, what the flow chose to do about it, and where it
ended. That is where flows go wrong. What it cannot prove is anything
about the device - whether the tap landed, whether the text arrived -
and those need a phone.

Three things make it honest:

- **The client refuses.** Anything but a shell command raises, so a flow
  that reaches past the screen - a screenshot, an install, the solver -
  fails loudly here instead of quietly touching the network.
- **The screens run out, and say so.** The last one is answered a few
  more times, so a flow still waiting on it is not cut off mid-act; then
  the replay ends as `walk_ended`, naming the screen it was on. It used
  to keep answering the last page until the flow was called
  `stuck_on_<page>`, which blames the flow for a recording that was
  short (found by its own test, 2026-09-25).
- **Nothing sleeps.** A flow's waits are for a device that is drawing;
  here the next screen is already decided. `time.sleep` is a no-op for
  the length of the replay and put back after - the one heavy-handed
  thing in this module, and it is what takes a replay from half a
  minute to under a second.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from pathlib import Path

from . import capture, screen
from .flows import router

log = logging.getLogger(__name__)


class ReplayError(RuntimeError):
    """The flow asked a recording something it cannot answer."""


class Client:
    """Stands in for `api.Client`. Swallows what acts on the phone and
    refuses everything else."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, payload: dict | None = None, **kwargs) -> dict:
        self.calls.append((path, dict(payload or {})))
        if path == "/v1/shell/execute":
            # A tap, a keystroke, a `dumpsys`: recorded and swallowed.
            # The screen it would have changed is the next one already.
            return {"code": 0, "data": {"status": True, "output": ""}}
        raise ReplayError(
            f"the flow called {path}, which a recording cannot answer. "
            f"Replay proves the screen decisions; run it on a phone for "
            f"the rest.")

    def data(self, path: str, payload: dict | None = None, **kwargs):
        return self.post(path, payload, **kwargs).get("data")

    @property
    def commands(self) -> list[str]:
        """Every shell command the flow ran, in order - what it did."""
        return [str(p.get("cmd") or "") for path, p in self.calls
                if path == "/v1/shell/execute"]


def feed(ctx, walk: list[str]):
    """Make `ctx.refresh()` answer these screens instead of a phone.

    The context itself is whatever the flow uses - `router.Context` or a
    flow's own subclass of it - so this works for any flow without the
    router knowing replay exists.
    """
    if not walk:
        raise ReplayError("nothing to replay: the walk has no screens")
    order = list(walk)
    at = [0]

    def refresh() -> None:
        i = min(at[0], len(order) - 1)
        at[0] += 1
        ctx.dumps += 1
        ctx.raw = order[i]
        ctx.elements = screen.parse(ctx.raw)
        ctx.blob = screen.texts(ctx.elements)

    ctx.refresh = refresh
    ctx.past_the_end = lambda: at[0] - len(order)
    return ctx


#: How many times the last screen is answered again before the replay
#: says the walk ended. A flow waiting on a page that is still drawing
#: asks once or twice; more than that and it is waiting for a page the
#: recording never caught.
#:
#: Two, not three, so this wins the race against the router's own stuck
#: rule: a Screen's default `max_visits` is 4, and at three spare looks
#: the flow was called `stuck_on_<page>` one turn before the walk could
#: say it had ended (2026-09-25).
SPARE_LOOKS = 2


@contextmanager
def no_waiting():
    """`time.sleep` does nothing, for as long as this is held."""
    slept = []
    real = time.sleep

    def nap(seconds: float) -> None:
        slept.append(seconds)

    time.sleep = nap
    try:
        yield slept
    finally:
        time.sleep = real


def over(directory: Path, screens: list, *, is_done=None,
         ctx=None, budget_seconds: float = 900.0,
         logger: logging.Logger | None = None) -> router.Outcome:
    """Drive `screens` over the walk in `directory`.

    `is_done` is the flow's own proof of success, and on a recording
    there is usually none to give: a sign-in ends when the account is on
    the device, and there is no device. Left out, the replay ends the
    way the recorded screens end - at a page the flow calls fatal, at a
    screen it cannot get past, or by running out.
    """
    walk = capture.screens(Path(directory))
    ctx = ctx or router.Context(client=Client(), phone_id="replay")
    if ctx.client is None:
        ctx.client = Client()
    feed(ctx, walk)

    def ended() -> router.Outcome | None:
        """The caller's own proof first; then, once the walk has run
        out, the walk running out."""
        said = is_done() if is_done else None
        if said is not None:
            return said
        if ctx.past_the_end() > SPARE_LOOKS:
            where = ctx.trail[-1] if ctx.trail else "nothing recognised"
            return router.Outcome(
                "unknown", "walk_ended",
                f"the recording has {len(walk)} screen(s) and the flow "
                f"wanted more; it was on {where}. Record further, or this "
                f"is where the flow does something the walk never saw.")
        return None

    with no_waiting():
        return router.drive(ctx, screens, is_done=ended,
                            budget_seconds=budget_seconds,
                            logger=logger or log)


def load_screens(dotted: str) -> list:
    """The `SCREENS` of a flow module named as `package.module`."""
    import importlib

    module = importlib.import_module(dotted)
    found = getattr(module, "SCREENS", None)
    if not found:
        raise ReplayError(f"{dotted} has no SCREENS to replay")
    return found
