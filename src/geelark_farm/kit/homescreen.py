"""Icons on a phone's home screen (2026-09-30).

The apps every phone carries go in through GeeLark's installer, and it adds
no icon to the home screen: they sit in the app drawer, a swipe and a
search away. The launcher (AOSP's Launcher3 on these phones) keeps its home
screen in a table behind a content provider, and the shell is root, so the
`content` tool adds a row to it exactly as a drag from the drawer would -
and the launcher reloads by itself when a row arrives from outside.

Best effort, and never a failed build: a phone whose launcher is another
one, or whose table reads differently, is left as it was and the reason is
logged. Nothing is ever removed or moved: the row goes into a free cell of
the first home screen, and an app that is already on a home screen (or in
the dock) is left alone, so running it twice adds nothing.
"""

from __future__ import annotations

import logging
import re

from .. import shell
from ..api import Client

log = logging.getLogger(__name__)

LAUNCHER = "com.android.launcher3"
FAVORITES = f"content://{LAUNCHER}.settings/favorites"
#: Launcher3's own constants: where a row lives and what kind it is.
DESKTOP, HOTSEAT = -100, -101
APPLICATION = 0
#: The flags Launcher3 gives every app it launches from the home screen
#: (NEW_TASK | RESET_TASK_IF_NEEDED).
LAUNCH_FLAGS = "0x10200000"

_ROW = re.compile(r"^Row: \d+ (.*)$")
_INT = {name: re.compile(rf"\b{name}=(-?\d+)")
        for name in ("container", "screen", "cellX", "cellY", "spanX", "spanY",
                     "itemType")}
_INTENT = re.compile(r"\bintent=(.*)$")
#: `pkg/Activity` or `pkg/.Activity`, and nothing a shell could read as more.
_COMPONENT = re.compile(r"^[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+$")
_PACKAGE = re.compile(r"^[A-Za-z0-9_.]+$")
_GRID = re.compile(r"launcher_(\d+)_by_(\d+)\.db")


def parse(output: str) -> list[dict]:
    """The rows `content query` printed, as dicts of the columns the
    planner reads. The intent is asked for last, so it runs to the end of
    its line whatever it contains."""
    items = []
    for line in (output or "").splitlines():
        match = _ROW.match(line.strip())
        if not match:
            continue
        body = match.group(1)
        item: dict = {}
        head = body
        found = _INTENT.search(body)
        if found:
            item["intent"] = found.group(1)
            head = body[:found.start()]
        for name, pattern in _INT.items():
            hit = pattern.search(head)
            if hit:
                item[name] = int(hit.group(1))
        if "container" in item:
            items.append(item)
    return items


def _on_a_home_screen(items: list[dict], package: str) -> bool:
    """The package already has an icon on a home screen or in the dock (a
    folder's rows carry their folder's id, so they count too)."""
    marker = f"component={package}/"
    return any(marker in str(item.get("intent") or "") for item in items)


def free_cells(items: list[dict], count: int, *, grid: int = 0
               ) -> tuple[int, list[tuple[int, int]]]:
    """(screen, [(x, y)] * count) - free cells of the first home screen,
    in one row when a row has room, else in reading order. Empty when the
    screen has no room.

    The size of the grid is not asked of the launcher: it is at least what
    the items already on the screen reach, and at least `grid` (the smaller
    number in the launcher's own database name), so a cell inside that box
    is always a real one. The top row of the first screen is never used:
    the launcher keeps it for its search bar, which is in no table, and it
    deletes an icon placed there the moment it loads (measured 2026-09-30:
    three icons at row 0 were gone within seconds, the same three at row 1
    stayed)."""
    desktop = [i for i in items if i.get("container") == DESKTOP
               and i.get("screen") is not None and i.get("cellX") is not None
               and i.get("cellY") is not None]
    if not desktop:
        return 0, []
    screen = min(int(i["screen"]) for i in desktop)
    here = [i for i in desktop if int(i["screen"]) == screen]
    taken: set[tuple[int, int]] = set()
    cols = rows = max(0, int(grid))
    for item in here:
        x, y = int(item["cellX"]), int(item["cellY"])
        sx = max(1, int(item.get("spanX") or 1))
        sy = max(1, int(item.get("spanY") or 1))
        cols, rows = max(cols, x + sx), max(rows, y + sy)
        taken.update((x + dx, y + dy) for dx in range(sx) for dy in range(sy))
    taken.update((x, 0) for x in range(cols))
    for y in range(rows):
        # A row with `count` free cells side by side.
        for start in range(cols - count + 1):
            span = [(x, y) for x in range(start, start + count)]
            if all(cell not in taken for cell in span):
                return screen, span
    free = [(x, y) for y in range(rows) for x in range(cols)
            if (x, y) not in taken]
    return (screen, free[:count]) if len(free) >= count else (screen, [])


def _intent(component: str) -> str:
    return ("#Intent;action=android.intent.action.MAIN;"
            "category=android.intent.category.LAUNCHER;"
            f"launchFlags={LAUNCH_FLAGS};component={component};end")


def _insert(title: str, component: str, screen: int, x: int, y: int) -> str:
    binds = (f"--bind title:s:'{title}' --bind intent:s:'{_intent(component)}'"
             f" --bind container:i:{DESKTOP} --bind screen:i:{screen}"
             f" --bind cellX:i:{x} --bind cellY:i:{y} --bind spanX:i:1"
             f" --bind spanY:i:1 --bind itemType:i:{APPLICATION}"
             f" --bind profileId:i:0")
    return f"content insert --uri {FAVORITES} {binds}"


def _query(client: Client, phone_id: str) -> str:
    return shell.read(
        client, phone_id,
        f"content query --uri {FAVORITES} --projection "
        f"container:screen:cellX:cellY:spanX:spanY:itemType:intent")


def _component(client: Client, phone_id: str, package: str) -> str:
    """The activity the launcher would start for the package, or ''."""
    if not _PACKAGE.match(package):
        return ""
    out = shell.read(
        client, phone_id,
        "cmd package resolve-activity --brief -a android.intent.action.MAIN "
        f"-c android.intent.category.LAUNCHER {package}")
    last = [ln.strip() for ln in out.splitlines() if ln.strip()][-1:]
    return last[0] if last and _COMPONENT.match(last[0]) else ""


def _grid(client: Client, phone_id: str) -> int:
    out = shell.read(client, phone_id,
                     f"ls /data/user/0/{LAUNCHER}/databases 2>/dev/null")
    hit = _GRID.search(out)
    return min(int(hit.group(1)), int(hit.group(2))) if hit else 0


def pin(client: Client, phone_id: str, apps: list[tuple[str, str]],
        *, serial: str = "") -> list[str]:
    """Put an icon for each (package, title) on the phone's first home
    screen. Returns the packages that were added; never raises."""
    who = serial or phone_id
    try:
        before = parse(_query(client, phone_id))
        if not before:
            log.info("no home screen to add icons to on %s (another launcher, "
                     "or it has not started yet)", who)
            return []
        wanted = []
        for package, title in apps:
            if _on_a_home_screen(before, package):
                continue
            component = _component(client, phone_id, package)
            if not component:
                log.info("%s has no icon to add on %s (not installed?)",
                         title, who)
                continue
            wanted.append((package, title, component))
        if not wanted:
            return []
        screen, cells = free_cells(before, len(wanted),
                                   grid=_grid(client, phone_id))
        if not cells:
            log.info("the home screen of %s has no room for %d icons", who,
                     len(wanted))
            return []
        for (_, title, component), (x, y) in zip(wanted, cells, strict=True):
            shell.run(client, phone_id, _insert(title, component, screen, x, y))
        after = parse(_query(client, phone_id))
        added = [package for package, _, _ in wanted
                 if _on_a_home_screen(after, package)]
        missed = [package for package, _, _ in wanted if package not in added]
        if missed:
            log.warning("the home screen of %s did not take an icon for %s",
                        who, ", ".join(missed))
        else:
            log.info("home screen of %s: added %s", who, ", ".join(added))
        return added
    except Exception as exc:                                       # noqa: BLE001
        log.warning("could not add the icons to the home screen of %s (%s)",
                    who, exc)
        return []
