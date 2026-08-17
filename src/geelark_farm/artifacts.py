"""Keeping the screen archive to the part of it anyone would look at.

Every flow archives the pages it goes through, which is what makes a failure
diagnosable at all - and a build that went perfectly archives twelve of them
too, pages where nothing happened that anyone will ever open. Nothing removed
any of it: 87MB across 457 directories and 5242 XML files, going back three
weeks, on a machine where the phones they describe were deleted a fortnight
ago (2026-08-17).

Two rules, from what the archive is actually for:

- **A build that worked keeps its pages while its phone exists.** The evidence
  is about a device, so it is worth having exactly as long as the device is.
  Once the phone is gone the pages describe nothing.
- **A build that failed keeps its pages for a week.** Long enough to come back
  to on Monday, and a failure is worth keeping past its phone precisely
  because the phone is usually the first thing deleted.

Anything the rules cannot read - a directory from before builds were named
after their phone, a hand-run `geelark login` - is treated as a failure, which
is the side that keeps things longer.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: How long a failed build's pages are kept. The operator's number.
FAILURE_DAYS = 7

#: Written when a build ends, because nothing else in the directory says how
#: it went - the pages of a success and of a failure look alike from outside,
#: and guessing from filenames would make the rule depend on the archive
#: naming of every flow.
OUTCOME_FILE = "outcome.txt"

#: `20260817-060502-build835` / `20260817-060455-finish823`. The serial is the
#: part that ties a directory to a phone; directories named `build3` - the
#: position in the batch - are from before that and match nothing.
SERIAL_IN_NAME = re.compile(r"-(?:build|finish)(\d+)$")


def record(directory: Path, *, ok: bool, status: str) -> None:
    """Note how the build that filled this directory ended.

    Never raises. A build that has done its work is not failed over a note
    about itself, and the prune treats an unreadable directory as a failure -
    which keeps it, rather than losing it.
    """
    try:
        if not directory.exists():
            return                       # nothing was archived, nothing to say
        (directory / OUTCOME_FILE).write_text(
            f"{'ok' if ok else 'failed'} {status}\n", encoding="utf-8")
    except OSError as exc:
        log.debug("could not write the outcome of %s (%s)", directory, exc)


def _succeeded(directory: Path) -> bool:
    try:
        return (directory / OUTCOME_FILE).read_text(
            encoding="utf-8").startswith("ok")
    except OSError:
        return False


def serial_of(directory: Path) -> str:
    found = SERIAL_IN_NAME.search(directory.name)
    return found.group(1) if found else ""


def prune(root: Path, live_serials: set[str], *,
          now: float | None = None, dry_run: bool = False) -> list[str]:
    """Remove what neither rule keeps. Returns the directory names removed.

    `live_serials` is what GeeLark says exists - the panel, not the sheet,
    because the question is whether the device is still there.
    """
    if not root.exists():
        return []
    cutoff = (now if now is not None else time.time()) - FAILURE_DAYS * 86400
    removed = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        if _succeeded(directory):
            if serial_of(directory) in live_serials:
                continue                 # its phone is still there
        elif directory.stat().st_mtime >= cutoff:
            continue                     # a failure, still within the week
        if not dry_run:
            try:
                shutil.rmtree(directory)
            except OSError as exc:
                log.warning("could not remove %s (%s)", directory, exc)
                continue
        removed.append(directory.name)
    if removed:
        log.info("pruned %d archived build(s)", len(removed))
    return removed
