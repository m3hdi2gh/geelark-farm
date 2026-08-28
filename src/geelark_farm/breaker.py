"""Stop building after enough builds in a row have gone wrong.

Run by hand, a bad build is somebody watching it fail and stopping. Run as a
service with `restart: always`, nothing is watching: a broken deploy, a
revoked key or a run of dead exits would keep taking Gmails and proxies out of
the pool and turning them into nothing, for as long as the pool lasts. This is
the thing between that and a morning spent finding out where the stock went.

Three decisions worth stating, because none of them is obvious.

**It counts consecutive failures, not a rate.** A rate needs a window and a
window needs tuning, and the question here is not "how often does this fail"
but "has it stopped working". One success is enough to say it has not.

**It is written down.** `restart: always` means the process that trips the
breaker is not the process that has to still know about it a second later, and
a counter in memory would be reset by the very restart the breaker is there to
stop being pointless.

**A person clears it.** Nothing here reopens on a timer: a breaker that closes
itself is a delay, and what tripped it is still true. `clear()` is the console
or a deleted file, and either way it is a decision somebody made after looking.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: How many in a row before it opens.
LIMIT = 5

#: A build that got a phone to one step short of ready. The whole point of
#: the warm stock, and evidence the pipeline works - so it clears the count
#: exactly as a delivered phone does. Counting it would trip the breaker on
#: the first quiet afternoon.
WORKED = frozenset({"no_usable_gpt"})

#: Nothing was created and nothing was spent, and the verdicts say so. There
#: is nothing burning for a breaker to stop, and equally nothing that says the
#: machine still works - so these leave the count exactly where it was. A
#: breaker silently defused by an empty pool between two real failures is
#: worse than one that never tripped.
#: `no_capacity` is here for the same reason and it took a live deployment to
#: see it: GeeLark ran out of machines of one Android version, every attempt
#: cost a second and nothing else, and each one counted. Five in a row - which
#: is an ordinary afternoon - would have opened the breaker over a shortage at
#: somebody else's datacentre (2026-08-28).
NOTHING_HAPPENED = frozenset({"no_usable_gmail", "no_usable_proxy",
                              "no_capacity"})


def counts_against(build) -> bool:
    """Whether this build is evidence that something has stopped working.

    Everything that is not one of the two sets above counts, including the
    reasons whose blame is `nobody`: `network_unreachable` and
    `all_exits_refused` are not "nothing to do", they are "this machine
    cannot work right now", and a loop that keeps trying through them is
    exactly what this exists to stop.
    """
    return not build.ok and build.status not in (WORKED | NOTHING_HAPPENED)


def shows_it_works(build) -> bool:
    """Whether this build is evidence that it still does."""
    return bool(build.ok) or build.status in WORKED


@dataclass
class Breaker:
    """The count, and the file it survives a restart in."""

    path: Path
    limit: int = LIMIT

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # No file yet, or one that cannot be read. Both mean the same
            # thing to a caller - nothing is known against this machine - and
            # neither is worth refusing to build over.
            return {}

    def _write(self, state: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError as exc:
            # A machine that cannot write here still has to build. It loses
            # the breaker, which is worth a loud line and not a dead service.
            log.error("could not record the build outcome for the breaker "
                      "(%s); it cannot trip on this machine", exc)

    def record(self, build) -> None:
        """Take one build's outcome into account.

        Three answers, not two: it failed, it worked, or nothing happened -
        and the third leaves the count alone rather than clearing it.
        """
        if not counts_against(build):
            if not shows_it_works(build):
                return                     # nothing happened; nothing to say
            state = self._read()
            if state.get("consecutive"):
                log.info("a build worked; the breaker's count goes back to 0")
            self._write({"consecutive": 0})
            return

        state = self._read()
        count = int(state.get("consecutive") or 0) + 1
        reasons = list(state.get("reasons") or [])[-(self.limit - 1):]
        reasons.append(build.status)
        self._write({"consecutive": count, "reasons": reasons})
        if count >= self.limit:
            log.error("%d builds in a row have failed (%s) - not building "
                      "again until somebody clears this",
                      count, ", ".join(reasons))
        else:
            log.warning("%d build(s) in a row have failed (%s)",
                        count, build.status)

    def seen(self) -> tuple[int, list[str]]:
        """How many failures in a row, and what they were.

        `reason` answers "is it open", which is what the loop asks. This is
        what a person asks: how close is it, and to what.
        """
        state = self._read()
        return int(state.get("consecutive") or 0), list(state.get("reasons") or [])

    def reason(self) -> str:
        """Why building is stopped, or "" when it is not.

        A sentence rather than a boolean, because whoever finds the service
        idle needs to know what it saw, and the count alone does not say.
        """
        state = self._read()
        count = int(state.get("consecutive") or 0)
        if count < self.limit:
            return ""
        reasons = ", ".join(state.get("reasons") or []) or "no reason recorded"
        return (f"{count} builds in a row failed ({reasons}). Nothing will be "
                f"built until this is cleared.")

    def clear(self) -> None:
        """Somebody has looked at it and decided to carry on."""
        self._write({"consecutive": 0})
        log.info("the breaker was cleared by hand")
