"""A phone's journey: its runs, the stages each went through, the screens
it saw on the way, and a drawing of each screen.

The phone's page told its story as a list of events - what ended each
run, in one line - and where a run lost its minutes, or which screen it
stopped on, was a search through its log lines (the operator, 2026-09-26:
"a timeline with the time of each step and the screen where it failed").
Everything needed is already written down: the builder's own log lines
mark each stage, the router names every screen it reads, and the screens
themselves are archived as uiautomator XML, with a real screenshot where
a flow failed. This reads them back; it writes nothing and imports
nothing that does.

The stage marks are the builder's own words. `MARKS` lists them, and a
test reads each one back out of the source that writes it, so a reworded
log line fails the suite instead of quietly flattening every journey.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import escape

log = logging.getLogger(__name__)

#: The log lines a journey is read from, by what each marks - and the
#: module that writes it, for the test that keeps the two in step.
MARKS = {
    "created": ("created ", "geelark_farm/phones.py"),
    "boot": (" - starting it (billing is per minute)", "geelark_farm/phones.py"),
    "settle": ("phone running; settling for", "geelark_farm/phones.py"),
    "google": ("signing in as ", "geelark_farm/builder.py"),
    "app": ("signing into ", "geelark_farm/builder.py"),
    "installed": (" is on, from GeeLark's installer", "geelark_farm/kit/install.py"),
}
#: `phone 4435 FAIL: phone_distrusted (406s)` - builder.py's closing line.
END = re.compile(r"^phone (\d+) (OK|WARM|FAIL): (\w+) \((\d+)s\)")
#: `screen: captcha (visit 3)` - the router's, under the flow's logger.
SCREEN = re.compile(r"^screen: (\w+) \(visit (\d+)\)")
#: `captcha: 'Select all images with a fire hydrant' -> tiles [3, 5, 6] ...`
CAPTCHA = re.compile(r"^captcha: '(.+?)' -> tiles (\[[^\]]*\])")
#: `tapping 'SKIP' at (75, 1291) (clickable=True)`
TAP = re.compile(r"^tapping ['\"](.+?)['\"] at ")

#: Which stage a flow's screens belong to, by the flow's logger.
FLOW_STAGE = {"google_login": "google", "chatgpt_login": "app",
              "claude_login": "app", "spotify_login": "app",
              "play_install": "apps"}

#: The stages in the order a build meets them, with the word a page shows.
STAGES = {"created": "Created", "boot": "Booted", "settle": "Settled",
          "google": "Google", "apps": "Apps", "app": "App account"}

#: A folder is `YYYYMMDD-HHMMSS-build4435` or `...-finish4445`.
FOLDER = re.compile(r"^(\d{8})-(\d{6})-(build|finish)(\d+)$")
#: A screen file is `HHMMSS-<screen>.xml`; a failure's picture
#: `HHMMSS-<reason>.png` or a fixed name like `captcha-screen.png`.
TIMED = re.compile(r"^(\d{6})-(.+)\.(xml|png)$")


def _utc(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace(" ", "T")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _logger_tail(name: str) -> str:
    return str(name or "").rsplit(".", 1)[-1]


class _Run:
    def __init__(self, at: datetime, kind: str):
        self.start = at
        self.kind = kind            # "build" or "finish"
        self.end: datetime | None = None
        self.mark = ""              # OK / WARM / FAIL
        self.status = ""
        self.seconds: int | None = None
        self.stages: list[dict] = []

    @property
    def stage(self) -> dict | None:
        return self.stages[-1] if self.stages else None

    def open(self, name: str, at: datetime, **more) -> dict:
        if self.stage is not None and self.stage["end"] is None:
            self.stage["end"] = at
        stage = {"name": name, "word": STAGES[name], "start": at, "end": None,
                 "screens": [], "captchas": [], "state": "done", **more}
        self.stages.append(stage)
        return stage

    def close(self, at: datetime) -> None:
        if self.stage is not None and self.stage["end"] is None:
            self.stage["end"] = at


def runs_from(lines, folders=(), created=()) -> list[dict]:
    """The runs a phone went through, oldest first, from its log lines
    (`at`, `logger`, `msg` - in any order), its archived folders
    (`folder`, `files`, `images`) and when it was created (the `phone
    created` events: phones.create logs before the build has a serial, so
    that line is never under the phone's own). Never raises on a line it
    does not know: that line is simply not a mark."""
    marks = [{"at": when, "logger": "geelark_farm.phones",
              "msg": "created (serial )"} for when in created or ()]
    ordered = sorted(({"at": _utc(line["at"]), "logger": line.get("logger", ""),
                       "msg": str(line.get("msg") or "")}
                      for line in [*marks, *lines]),
                     key=lambda line: line["at"])
    runs: list[_Run] = []
    run: _Run | None = None
    screen: dict | None = None

    def begin(at: datetime, kind: str) -> _Run:
        nonlocal run, screen
        if run is not None and run.end is None:
            run.close(at)
        run, screen = _Run(at, kind), None
        runs.append(run)
        return run

    for line in ordered:
        at, msg, tail = line["at"], line["msg"], _logger_tail(line["logger"])
        if tail == "phones" and msg.startswith(MARKS["created"][0]) \
                and "(serial " in msg:
            begin(at, "build").open("created", at)
            continue
        if msg.endswith(MARKS["boot"][0]) or MARKS["boot"][0] in msg:
            if run is None or run.end is not None:
                begin(at, "finish")
            run.open("boot", at)
            continue
        if run is None or run.end is not None:
            continue
        # First whether this line starts a stage...
        if msg.startswith(MARKS["settle"][0]):
            run.open("settle", at)
        elif tail == "builder" and msg.startswith(MARKS["google"][0]):
            run.open("google", at, detail=masked(msg[len(MARKS["google"][0]):]))
            screen = None
        elif tail == "builder" and msg.startswith(MARKS["app"][0]):
            run.open("app", at, detail=masked(msg[len(MARKS["app"][0]):]))
            screen = None
        elif MARKS["installed"][0] in msg or tail == "play_install":
            if run.stage is None or run.stage["name"] not in ("apps", "app"):
                run.open("apps", at)
                screen = None
        # ...then what it says inside the stage it is in.
        if (found := SCREEN.match(msg)) is not None:
            stage = run.stage
            if stage is None or stage["name"] in ("created", "boot", "settle"):
                stage = run.open(FLOW_STAGE.get(tail, "google"), at)
            name = found.group(1)
            if screen is not None and screen["name"] == name \
                    and screen in stage["screens"]:
                screen["visits"] += 1
                screen["last"] = at
            else:
                screen = {"name": name, "at": at, "last": at, "visits": 1,
                          "taps": [], "file": ""}
                stage["screens"].append(screen)
        elif (found := CAPTCHA.match(msg)) is not None and run.stage:
            run.stage["captchas"].append({"at": at, "prompt": found.group(1),
                                          "tiles": found.group(2)})
        elif (found := TAP.match(msg)) is not None and screen is not None:
            if found.group(1) not in screen["taps"]:
                screen["taps"].append(masked(found.group(1)))
        elif (found := END.match(msg)) is not None:
            run.close(at)
            run.end, run.mark = at, found.group(2)
            run.status, run.seconds = found.group(3), int(found.group(4))
            screen = None
    out = []
    for r in runs:
        folder = _folder_for(r, folders)
        # The folder says which it was; without one, a run that was
        # created or signed into Google was a build.
        found = FOLDER.match(str((folder or {}).get("folder") or ""))
        if found:
            r.kind = found.group(3)
        elif any(st["name"] in ("created", "google") for st in r.stages):
            r.kind = "build"
        out.append(_finished(r, folder))
    return out


def _folder_for(run: _Run, folders) -> dict | None:
    """The archived folder this run wrote: its kind, and its stamp between
    a little before the run began and its end."""
    best = None
    for f in folders or ():
        found = FOLDER.match(str(f.get("folder") or ""))
        if not found:
            continue
        stamp = datetime.strptime(found.group(1) + found.group(2),
                                  "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        late = run.end or (run.start + timedelta(hours=2))
        if run.start - timedelta(minutes=3) <= stamp <= late:
            if best is None or abs(stamp - run.start) < abs(best[0] - run.start):
                best = (stamp, f)
    return best[1] if best else None


def _finished(run: _Run, folder: dict | None) -> dict:
    stages = run.stages
    last = stages[-1] if stages else None
    if last is not None:
        if run.end is None:
            last["state"] = "running"
        elif run.mark == "FAIL":
            last["state"] = "failed"
        elif last["name"] == "app" and run.mark != "OK":
            # Ended warm with an app sign-in under way: the account did
            # not go in - refused, or a code nobody could supply - and
            # the phone was kept for the next one.
            last["state"] = "refused"
    for stage in stages:
        end = stage["end"] or run.end
        stage["seconds"] = (int((end - stage["start"]).total_seconds())
                            if end else None)
        screens = stage["screens"]
        for i, s in enumerate(screens):
            upto = screens[i + 1]["at"] if i + 1 < len(screens) else end
            s["seconds"] = int((upto - s["at"]).total_seconds()) if upto else None
            s["offset"] = int((s["at"] - stage["start"]).total_seconds())
    files = _attach(stages, folder)
    shot = _last_shot(folder) if folder else ""
    return {"kind": run.kind, "start": run.start, "end": run.end,
            "mark": run.mark, "status": run.status, "seconds": run.seconds,
            "stages": stages, "folder": (folder or {}).get("folder", ""),
            "shot": shot, "files": files,
            "last_dump": _last_dump(stages, folder) if folder else "",
            "failed_at": next((s["name"] for s in stages
                               if s["state"] in ("failed", "refused",
                                                 "running")), "")}


def _attach(stages: list[dict], folder: dict | None) -> int:
    """Each screen its archived XML: the file named for that screen, saved
    the second it was read (a second either side - the dump is written
    as the router reads it). Returns how many were matched."""
    if not folder:
        return 0
    by_key = {}
    for name in folder.get("files") or ():
        found = TIMED.match(name)
        if found and found.group(3) == "xml":
            by_key.setdefault(found.group(2), []).append(
                (int(found.group(1)), name))
    matched = 0
    for stage in stages:
        for s in stage["screens"]:
            hhmmss = int(s["at"].strftime("%H%M%S"))
            for stamp, name in by_key.get(s["name"], ()):
                if abs(_seconds(stamp) - _seconds(hhmmss)) <= 2:
                    s["file"] = name
                    matched += 1
                    break
    return matched


def _last_dump(stages: list[dict], folder: dict) -> str:
    """The XML a failing flow saved of where it stopped - named for the
    reason, not a screen (`180610-captcha_shown.xml`) - so a run with no
    screenshot still has a picture of its last screen."""
    used = {s["file"] for st in stages for s in st["screens"] if s["file"]}
    timed = sorted((n for n in folder.get("files") or ()
                    if TIMED.match(n) and n.endswith(".xml") and n not in used),
                   reverse=True)
    return timed[0] if timed else ""


def _seconds(hhmmss: int) -> int:
    return hhmmss // 10000 * 3600 + hhmmss // 100 % 100 * 60 + hhmmss % 100


def _last_shot(folder: dict) -> str:
    """The real screenshot of where the run stopped: the latest timed
    one, else the full captcha screen, else any."""
    images = [n for n in folder.get("images") or () if n.endswith(".png")]
    timed = sorted((n for n in images if TIMED.match(n)), reverse=True)
    if timed:
        return timed[0]
    for name in ("captcha-screen.png", "challenge-screen.png"):
        if name in images:
            return name
    return sorted(images)[0] if images else ""


# ------------------------------------------------------ the drawing
_EMAIL = re.compile(r"([A-Za-z0-9._%+-]{1,2})[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+)")
_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def masked(text: str) -> str:
    """An address as its first two letters and its domain - the page is a
    picture of a screen, not a list of the pool."""
    return _EMAIL.sub(lambda m: f"{m.group(1)}•••@{m.group(2)}", text)


def wireframe_svg(xml: bytes, width: int = 150) -> str:
    """A screen as the phone reported it, drawn small: the texts where
    they stood, buttons as buttons, boxes as boxes. From the uiautomator
    dump, so every screen has a picture, not only the one a flow saved a
    screenshot of. Addresses are masked; nothing else is kept."""
    try:
        root = ET.fromstring(xml.decode("utf-8", "replace"))
    except ET.ParseError as exc:
        # A dump cut short when the flow stopped: drawn as an empty screen,
        # and said, so a page of blank cards has a reason in the log.
        log.info("a screen dump would not parse, drawn empty (%s)", exc)
        root = None
    screen_w, screen_h = 720, 1440
    leaves = []
    for node in (root.iter("node") if root is not None else ()):
        found = _BOUNDS.match(node.get("bounds") or "")
        if not found:
            continue
        x1, y1, x2, y2 = (int(v) for v in found.groups())
        if x1 == 0 and y1 == 0 and x2 >= screen_w * 0.9:
            screen_w, screen_h = max(screen_w, x2), max(screen_h, y2)
        if list(node):
            continue
        text = (node.get("text") or node.get("content-desc") or "").strip()
        cls = (node.get("class") or "").rsplit(".", 1)[-1]
        if not text and cls != "EditText":
            continue
        leaves.append((x1, y1, x2, y2, masked(text), cls,
                       node.get("clickable") == "true"))
    scale = width / screen_w
    height = round(screen_h * scale)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
             f'height="{height}" viewBox="0 0 {width} {height}" '
             f'font-family="system-ui, sans-serif">',
             f'<rect width="{width}" height="{height}" rx="10" '
             f'fill="#f4f6f9"/>']
    for x1, y1, x2, y2, text, cls, click in leaves[:60]:
        x, y = x1 * scale, y1 * scale
        w, h = max(4.0, (x2 - x1) * scale), max(4.0, (y2 - y1) * scale)
        size = max(6.5, min(11.0, h * 0.55))
        words = text if len(text) <= 34 else text[:32] + "…"
        if cls == "EditText":
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                         f'height="{h:.1f}" rx="2" fill="#ffffff" '
                         f'stroke="#4a78c8" stroke-width="0.8"/>')
            color = "#1f2733"
        elif click and len(text) <= 24:
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
                         f'height="{h:.1f}" rx="3" fill="#dde6f5"/>')
            color = "#27448a"
        else:
            color = "#3c4453"
        if words:
            parts.append(f'<text x="{x + 2:.1f}" y="{y + h / 2 + size / 3:.1f}" '
                         f'font-size="{size:.1f}" fill="{color}">'
                         f'{escape(words)}</text>')
    parts.append("</svg>")
    return "".join(parts)
