"""Which captcha door a grid goes through, and what happens when it fails.

Two doors answer the reCAPTCHA image grid: CapSolver (a model, one to
three seconds, cheap) and 2Captcha (people, ten to forty seconds, dearer).
The flows do not care which; they hand the grid to `solve_grid` with
whatever the builder gave them as `solver_key`, and read back the same
answer either way - 0-based tiles and the grid's width.

`solver_key` is either a plain CapSolver key, which is how every caller
and test spoke before 2Captcha existed and still works unchanged, or a
`Solvers` built by `of(settings)`: the doors that have a key, in the order
to try them. The first door that answers wins; a door that raises is
logged and the next is tried, so a 2Captcha with no worker free or an
empty balance costs a few seconds, not the build. Only when every door has
failed does the caller see an error - and it treats that as it always
treated a solver failure: the captcha stands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

CAPSOLVER = "capsolver"
TWOCAPTCHA = "2captcha"
DOORS = (TWOCAPTCHA, CAPSOLVER)


@dataclass(frozen=True)
class Solvers:
    """The doors with a key, first to last. Falsy when there are none, so
    `if ctx.solver_key:` reads the same as it did with a bare key."""

    order: tuple[str, ...] = ()
    keys: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return any(self.keys.get(door) for door in self.order)

    def key(self, door: str) -> str:
        return self.keys.get(door, "")


def of(settings) -> Solvers:
    """The doors this configuration opens, `captcha_solver` first.

    With no preference stated, 2Captcha goes first when it has a key -
    a person was paid for precisely because the model was not enough -
    and CapSolver stands behind it. A preference names the first door;
    the other is still the fallback."""
    keys = {CAPSOLVER: getattr(settings, "capsolver_key", "") or "",
            TWOCAPTCHA: getattr(settings, "twocaptcha_key", "") or ""}
    first = (getattr(settings, "captcha_solver", "") or "").strip().lower()
    order = [d for d in DOORS if keys[d]]
    if first in order:
        order.remove(first)
        order.insert(0, first)
    return Solvers(order=tuple(order), keys=keys)


def _through(door: str, key: str, image_b64: str, question: str, *,
             size: int, session=None, watch=None) -> tuple[list[int], int]:
    if door == TWOCAPTCHA:
        from . import twocaptcha
        return twocaptcha.solve_grid(key, image_b64, question, size=size,
                                     session=session, watch=watch)
    from . import capsolver
    # The session only when there is one: the flows never pass it, and
    # their tests fake this door with the narrower signature.
    extra = {"session": session} if session is not None else {}
    return capsolver.solve_grid(key, image_b64, question, watch=watch, **extra)


def solve_grid(solver, image_b64: str, question: str, *, size: int = 0,
               session=None, watch=None) -> tuple[list[int], int]:
    """The grid through the first door that answers.

    `solver` is a `Solvers` or a bare CapSolver key. `size` is the width
    the grid was sent at; 2Captcha's workers are told it, CapSolver reads
    it back itself. Raises the last door's error when none answered."""
    if not isinstance(solver, Solvers):
        return _through(CAPSOLVER, str(solver or ""), image_b64, question,
                        size=size, session=session, watch=watch)
    last: Exception | None = None
    for door in solver.order:
        key = solver.key(door)
        if not key:
            continue
        try:
            return _through(door, key, image_b64, question, size=size,
                            session=session, watch=watch)
        except Exception as exc:                                   # noqa: BLE001
            last = exc
            log.warning("%s did not answer the grid (%s); trying the next "
                        "door", door, exc)
    if last is None:
        raise RuntimeError("no captcha solver has a key")
    raise last
