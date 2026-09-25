"""What the Tasks page reads: the registry, the runs, and one run.

In a module of its own rather than in `read.py`, which is two thousand
lines of the pools' and the dashboard's own queries and is edited by
whoever is working on those. Nothing here is shared with them: a task's
rows are its own table.

A reader, like `read.py`: it opens no phone and writes nothing.
"""
from __future__ import annotations

import logging

from .. import tasks as registry
from ..config import Settings

log = logging.getLogger(__name__)

#: How many runs a task's page lists before it pages. A person reading
#: this is asking "how has it been going", and fifty answers that.
PER_PAGE = 50
#: The window the numbers on the list are counted over.
DAYS = 7


def listing(settings: Settings) -> dict:
    """Every task, with how it has been doing - the page's front door.

    A task with no runs is still listed, and says so. The list is what
    tells somebody a task exists at all, and a list that hides the ones
    nobody has run yet hides exactly the new ones.
    """
    out = []
    counts: dict[str, dict] = {}
    if settings.store_enabled:
        from ..store import task_runs

        try:
            for spec in registry.TASKS.values():
                counts[spec.key] = task_runs.tally(settings, spec.key,
                                                   days=DAYS)
        except Exception as exc:                                  # noqa: BLE001
            # The page is worth drawing without its numbers: the list of
            # what exists comes from the registry, not the cluster.
            log.warning("the task numbers could not be read (%s)", exc)
            counts = {}
    for spec in registry.TASKS.values():
        out.append({"key": spec.key, "title": spec.title,
                    "summary": spec.summary,
                    "inputs": [{"name": f.name, "label": f.label,
                                "required": f.required, "secret": f.secret,
                                "help": f.help} for f in spec.inputs],
                    "tally": counts.get(spec.key) or {}})
    return {"tasks": out, "days": DAYS, "counted": bool(counts)}


def runs(settings: Settings, task: str = "", page: int = 1) -> dict:
    """One task's runs, newest first, with the numbers under them."""
    spec = registry.spec(task) if task else None
    page = max(1, int(page or 1))
    out = {"task": task, "spec": spec, "page": page, "rows": [],
           "tally": {}, "more": False, "days": DAYS}
    if not settings.store_enabled:
        return out
    from ..store import task_runs

    try:
        rows = task_runs.recent(settings, task, limit=PER_PAGE + 1)
        out["tally"] = task_runs.tally(settings, task, days=DAYS)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("the runs of %s could not be read (%s)", task or "-", exc)
        return out
    out["rows"] = rows[:PER_PAGE]
    out["more"] = len(rows) > PER_PAGE
    return out


def one(settings: Settings, run_id: int) -> dict | None:
    """One run, with the screens it kept.

    The screens are the phone's own archive, read the way a build's are
    (`read.screen_file`) and drawn by the same `journey.wireframe_svg` -
    a task's page shows no viewer of its own. A run whose folder never
    reached the store lists nothing and says so rather than looking
    empty.
    """
    if not settings.store_enabled:
        return None
    from ..store import task_runs

    row = task_runs.one(settings, int(run_id))
    if row is None:
        return None
    row["screens"] = _screens(settings, row)
    return row


def _screens(settings: Settings, row: dict) -> list[dict]:
    """What the run archived, in the order the flow met it - which is the
    order the names carry, because the router stamps each with the time
    it was saved."""
    serial = str(row.get("serial") or "")
    folder = str(row.get("folder") or "")
    if not (serial and folder):
        return []
    from . import read

    try:
        # `_stored` is what the phone's own page lists its folders with,
        # so a task's screens are found the same way a build's are and
        # there is no second lister to keep in step.
        found = next((f for f in read._stored(settings, serial)
                      if f["folder"] == folder), None)
    except Exception as exc:                                      # noqa: BLE001
        log.debug("no screens for run %s (%s)", row.get("id"), exc)
        return []
    names = list((found or {}).get("files") or [])
    pictures = set((found or {}).get("images") or [])
    out = []
    for name in sorted(names):
        if not name.endswith(".xml"):
            continue
        stamp, _, rest = name.partition("-")
        out.append({
            "name": name,
            "at": f"{stamp[:2]}:{stamp[2:4]}:{stamp[4:6]}"
                  if len(stamp) == 6 and stamp.isdigit() else "",
            "screen": rest.removesuffix(".xml").replace("-", " "),
            "wire": f"/phones/{serial}/wire/{folder}/{name}",
            # Only where the flow actually saved one: a screenshot is
            # taken on the paths that fail, not on every screen.
            "shot": (f"/phones/{serial}/shot/{folder}/{shot}"
                     if (shot := f"{name.removesuffix('.xml')}.png")
                     in pictures else ""),
        })
    return out
