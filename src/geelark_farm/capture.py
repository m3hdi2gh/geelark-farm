"""Record a walk: the screens a person went through, in order.

The first of the three tools a new flow is written with (`draft` and
`replay` are the others). `cli.dump` takes one screen; this watches a
phone while somebody drives it in GeeLark's viewer and keeps every page
they reach.

Two decisions make the recording worth reading:

**Only a screen that changed is kept.** Polling every two seconds for
ten minutes is three hundred dumps, nearly all of them the same page,
and a folder like that is not evidence of anything. A page is the same
page when the set of labels on it is the same - not the XML, which
differs by a cursor or a clock between two dumps of a still screen.

**An address never lands on disk.** A walk through a sign-in captures
whatever was typed into the email box, and the farm's own artifacts
carry real addresses for exactly that reason (found while porting them,
2026-09-22). Every address becomes `user1@example.com`, the same one
each time inside a walk, so a page about two accounts still reads as a
page about two accounts. A flow must not match on an address anyway.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

from . import screen
from .api import Client

log = logging.getLogger(__name__)

#: How often the phone is asked. Two seconds is the farm's own dump
#: cadence; faster spends the shared rate limit for nothing.
EVERY = 2.0
#: How long a recording runs before it stops itself.
SECONDS = 600.0
#: What a walk is written as.
WALK = "walk.json"

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


class Redactor:
    """Addresses out, the same stand-in for the same address."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def __call__(self, raw: str) -> str:
        return _EMAIL.sub(self._stand_in, raw or "")

    def _stand_in(self, found: re.Match) -> str:
        key = found.group(0).casefold()
        if key not in self._seen:
            self._seen[key] = f"user{len(self._seen) + 1}@example.com"
        return self._seen[key]

    @property
    def count(self) -> int:
        return len(self._seen)


def fingerprint(elements: list[screen.Element]) -> str:
    """What makes this page this page: its labels, sorted.

    Not the XML. Two dumps of a still screen differ by a caret, a clock
    and the scroll offsets, so comparing the text would keep every one
    of them.
    """
    return "\n".join(sorted({e.label for e in elements if e.label}))


def watch(client: Client, phone_id: str, directory: Path, *,
          every: float = EVERY, seconds: float = SECONDS,
          cancelled: Callable[[], bool] | None = None,
          on_screen: Callable[[int, list[screen.Element]], None] | None = None,
          ) -> Path:
    """Record until the time runs out or `cancelled` says stop.

    Returns the folder. Never raises over one bad dump: a phone between
    pages answers nothing, and that is a page not to keep rather than a
    reason to end the recording.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    redact = Redactor()
    steps: list[dict] = []
    was = ""
    started = time.monotonic()
    deadline = started + seconds

    while time.monotonic() < deadline:
        if cancelled and cancelled():
            break
        try:
            raw = screen.capture(client, phone_id)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("no dump this time (%s)", exc)
            raw = None
        if raw:
            elements = screen.parse(raw)
            mark = fingerprint(elements)
            if mark and mark != was:
                was = mark
                name = f"{len(steps) + 1:03d}.xml"
                (directory / name).write_text(redact(raw), encoding="utf-8")
                steps.append({"file": name,
                              "at": round(time.monotonic() - started, 1),
                              "elements": len(elements),
                              "labels": [e.label for e in elements
                                         if e.label][:12]})
                log.info("screen %d: %s", len(steps),
                         ", ".join(steps[-1]["labels"][:4]) or "(no labels)")
                if on_screen:
                    on_screen(len(steps), elements)
        time.sleep(every)

    (directory / WALK).write_text(json.dumps(
        {"phone": phone_id, "seconds": round(time.monotonic() - started, 1),
         "addresses_removed": redact.count, "steps": steps}, indent=2),
        encoding="utf-8")
    log.info("kept %d screen(s) in %s", len(steps), directory)
    return directory


def read(directory: Path) -> dict:
    """A walk back off disk."""
    return json.loads((Path(directory) / WALK).read_text(encoding="utf-8"))


def screens(directory: Path) -> list[str]:
    """Every screen of a walk as XML, in the order it was reached.

    Falls back to the folder's own `*.xml` when there is no `walk.json`,
    so a directory of screens a build archived can be read too - that is
    the farm's whole artifact history, and it is the same shape.
    """
    directory = Path(directory)
    if (directory / WALK).exists():
        names = [step["file"] for step in read(directory)["steps"]]
    else:
        names = sorted(p.name for p in directory.glob("*.xml"))
    out = []
    for name in names:
        path = directory / name
        if path.exists():
            out.append(path.read_text(encoding="utf-8"))
    return out


def pages(directory: Path) -> list[list[screen.Element]]:
    """Every screen of a walk, parsed."""
    return [screen.parse(xml) for xml in screens(directory)]
