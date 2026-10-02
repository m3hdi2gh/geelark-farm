"""What the Proxies page draws: every proxy of the pool, as one answer.

The page is the prototype the user called final (2026-10-02), and it does
its own filtering, sorting and charting, so it is handed the whole pool at
once - a few dozen proxies - with, for each, its state and lane, its daily
count and cap, where its address comes out, what the operators pressed on
its phones over three ranges, and every use it has had.

A use is a phone given the proxy: built on it (a sign-in, let in or not)
or moved onto it with Change IP. Each carries when it ended - the phone
moved on to another proxy, or closed - so the page can tell how long a
phone held the proxy from how long the proxy then sat idle. A press goes
to the latest use of that phone on that proxy before it.

Times are Tehran wall-clock strings, "YYYY-MM-DD HH:MM". The page compares
them as strings and subtracts them as if they were UTC, which is exact in
a zone without daylight saving (Iran has had none since 2022).

`assemble` is the whole reading and takes plain rows, so it is tested
without a database; `state` fetches the rows.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import threading
import time
from zoneinfo import ZoneInfo

from ..config import Settings

log = logging.getLogger(__name__)

TEHRAN = ZoneInfo("Asia/Tehran")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec")
BUTTONS = ("done", "decline", "or", "auth", "failed")
CODE = {"done": "d", "decline": "x", "or": "o", "auth": "a", "failed": "f"}
#: A batch proxy's name: Seller-Type-DDMon-n, the type optional.
BATCH = re.compile(r"^([A-Za-z0-9]+)(?:-([A-Za-z0-9]+))?-(\d{2}[A-Z][a-z]{2})-(\d+)$")
_MOVED = re.compile(r"phone (\d+) is on (\S+) now")
_FAMILY = re.compile(r"[A-Za-z]+")


def local(when) -> str:
    """A timestamp as Tehran wall-clock "YYYY-MM-DD HH:MM", "" for none."""
    if not when:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.astimezone(TEHRAN).strftime("%Y-%m-%d %H:%M")


def _word(iso: str) -> str:
    """"2026-09-29" as "29 Sep"."""
    return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1]}" if len(iso) >= 10 else ""


def _proxy_words(note: str) -> str:
    """The farm's notes say "exit" for the proxy itself; the page says
    proxy (the user, 2026-10-02)."""
    note = re.sub(r"\ban exit\b", "a proxy", note or "")
    return re.sub(r"\bexit\b", "proxy", re.sub(r"\bexits\b", "proxies", note))


def _state_of(status: str, serial: str, error) -> tuple[str, bool]:
    """The page's four states, and whether a proxy on a phone leaves play
    once that phone goes (set aside under it)."""
    word = (status or "").strip().lower()
    if error or word == "dead":
        return "dead", False
    if word in ("", "free", "unused"):
        return "free", False
    if word in ("on a phone", "claimed"):
        return "phone", False
    if word == "set aside" and serial:
        return "phone", True
    return "aside", False


def batch_of(name: str) -> dict:
    """A batch proxy's parts - seller, type, number and its batch key - or
    {} for a name from before batches (TSP10)."""
    hit = BATCH.match(name or "")
    if not hit:
        return {}
    seller, kind, day, k = hit.groups()
    return {"seller": seller, "type": kind or "", "k": int(k),
            "batch": f"{seller}|{kind or ''}|{day}".lower()}


def _uses(names: set, signins, moves, phones, presses, now) -> dict:
    """Every use of each proxy in `names`: [when, 1 signed in | 0 refused
    | 2 moved onto it, press d x o a f or "", 1 if the proxy came out at a
    new address, the phone, who pressed, when the use ended or ""]."""
    events = []    # [name, at, how, serial, ip]
    for r in signins:
        events.append([r["proxy_name"], r["at"], 1 if r["ok"] else 0,
                       str(r["serial"] or ""), r["exit_ip"] or ""])
    for r in moves:
        hit = _MOVED.search(r["result"] or "")
        if hit and r["requested_at"]:
            events.append([hit.group(2), r["requested_at"], 2, hit.group(1), ""])
    events.sort(key=lambda e: (e[0], e[1]))

    # When each use ended: the phone's next use on another proxy, or the
    # phone closing - whichever came first, and only once it has happened.
    closes: dict[str, list] = {}
    for r in phones:
        closes.setdefault(str(r["serial"] or ""), []).append(
            (r["created_at"], r["done_at"]))
    by_serial: dict[str, list] = {}
    for e in events:
        by_serial.setdefault(e[3], []).append(e)
    end_of: dict[int, object] = {}
    slack = datetime.timedelta(hours=2)
    for serial, es in by_serial.items():
        es.sort(key=lambda e: e[1])
        for i, e in enumerate(es):
            nxt = next((x for x in es[i + 1:] if x[0] != e[0]), None)
            made = [c for c in closes.get(serial, []) if c[0] and c[0] <= e[1] + slack]
            done = max(made, key=lambda c: c[0])[1] if made else None
            ends = [t for t in (nxt[1] if nxt else None, done)
                    if t is not None and e[1] <= t <= now]
            end_of[id(e)] = min(ends) if ends else None

    by_name: dict[str, list] = {}
    for e in events:
        if e[0] in names:
            by_name.setdefault(e[0], []).append(e)
    press_of: dict[tuple, tuple] = {}
    for r in presses:
        uses = by_name.get(r["proxy_name"], [])
        hits = [i for i, e in enumerate(uses)
                if e[3] == str(r["serial"] or "") and e[1] <= r["at"]]
        if hits:   # in time order, so the last press wins
            press_of[(r["proxy_name"], hits[-1])] = (CODE[r["button"]],
                                                     r["by_name"] or "")
    out: dict[str, list] = {}
    for name, es in by_name.items():
        last_ip = ""
        for i, (_, at, how, serial, ip) in enumerate(es):
            fresh = 1 if ip and last_ip and ip != last_ip else 0
            last_ip = ip or last_ip
            press, by = press_of.get((name, i), ("", ""))
            out.setdefault(name, []).append(
                [local(at), how, press, fresh, serial, by, local(end_of.get(id(es[i])))])
    return out


def assemble(pool, signins, moves, phones, presses, *, geo: dict,
             archived: int, archived_names, now: datetime.datetime) -> dict:
    """The page's whole state from plain rows (see the module's words)."""
    now = now.astimezone(TEHRAN)
    today = now.strftime("%Y-%m-%d")
    since = {"t": today + " 00:00",
             "d": (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d") + " 00:00",
             "a": ""}
    presses = sorted((p for p in presses if p["button"] in CODE),
                     key=lambda p: p["at"])
    first = min((p["at"] for p in presses), default=None)
    track = local(first)[:10] + " 00:00" if first else ""

    rows = [r for r in pool if (r.get("proxy_name") or "").strip()
            and (r.get("source") or "") != "one-off"]
    names = {r["proxy_name"] for r in rows}
    uses = _uses(names, signins, moves, phones, presses, now)

    counts: dict[str, dict] = {}
    for p in presses:
        when = local(p["at"])
        v = counts.setdefault(p["proxy_name"], {k: [0] * 5 for k in "tda"})
        i = BUTTONS.index(p["button"])
        v["a"][i] += 1
        if when >= since["d"]:
            v["d"][i] += 1
        if when >= since["t"]:
            v["t"][i] += 1

    exits = []
    for r in rows:
        name = r["proxy_name"]
        serial = str(r.get("serial") or "").strip()
        s, after = _state_of(r.get("status"), serial, r.get("error"))
        ip = str(r.get("last_exit_ip") or "")
        place = geo.get(ip) if isinstance(geo.get(ip), dict) else {}
        lane = str(r.get("purpose") or "").strip().lower()
        day_on = str(r.get("day_uses_on") or "")[:10]
        host = str(r.get("host") or "")
        e = {"id": int(r["id"]), "n": name, "s": s, "after": after,
             "serial": serial if s == "phone" else "",
             "lane": lane if lane in ("gpt", "spotify") else "",
             "today": int(r.get("day_uses") or 0) if day_on == today else 0,
             "cap": int(r.get("uses_per_day") or 0),
             "ip": ip, "cc": str(place.get("cc") or ""),
             "isp": str(place.get("isp") or ""),
             "note": _proxy_words(str(r.get("note") or ""))[:240],
             "added": local(r.get("created_at"))[:10],
             "ep": f"{host.lower()}:{r.get('port') or ''}:{r.get('username') or ''}",
             "end": f"{host}:{r.get('port') or ''}",
             "user": str(r.get("username") or ""),
             "v": counts.get(name, {k: [0] * 5 for k in "tda"}),
             "u": uses.get(name, [])}
        parts = batch_of(name)
        e.update(parts)
        hit = _FAMILY.match(name)
        e["f"] = parts.get("seller") or (hit.group(0) if hit else name)
        exits.append(e)
    exits.sort(key=lambda e: e["n"].lower())

    # The highest number each batch has given, the archive's included.
    issued: dict[str, int] = {}
    for name in [*names, *archived_names]:
        parts = batch_of(name)
        if parts:
            issued[parts["batch"]] = max(issued.get(parts["batch"], 0), parts["k"])
    return {"exits": exits, "archived": int(archived), "issued": issued,
            "now": now.strftime("%Y-%m-%d %H:%M"), "since": since,
            "track": track, "trackWord": _word(track),
            "day": {"iso": today, "tag": now.strftime("%d") + MONTHS[now.month - 1],
                    "word": _word(today)}}


_POOL = ("SELECT id, proxy_name, host, port, username, status, serial,"
         " last_exit_ip, note, error, purpose, uses_per_day, day_uses,"
         " day_uses_on, created_at, source"
         " FROM resources WHERE kind = 'proxy'")
_SIGNINS = ("SELECT proxy_name, at, ok, serial, coalesce(exit_ip, '') AS exit_ip"
            " FROM signins WHERE coalesce(proxy_name, '') <> ''")
_MOVES = ("SELECT requested_at, coalesce(result::text, '') AS result"
          " FROM actions WHERE verb = 'change_proxy'")
_PHONES = "SELECT serial, created_at, done_at FROM phones"
_PRESSES = ("SELECT proxy_name, serial, at, button, coalesce(by_name, '') AS by_name"
            " FROM verdicts WHERE coalesce(proxy_name, '') <> ''"
            " AND button IN ('done', 'decline', 'or', 'auth', 'failed')")
_ARCHIVED = "SELECT count(*) AS n FROM resources_archive WHERE kind = 'proxy'"

#: The last answer, for a few seconds: every open page asks every twenty,
#: and two admins' pages need not read the same rows twice.
_KEEP_SECONDS = 4.0
_kept: dict = {}
_lock = threading.Lock()


def state(settings: Settings, *, fresh: bool = False) -> dict:
    """The page's state, read now - or the one read a moment ago."""
    with _lock:
        if not fresh and _kept and time.monotonic() - _kept["at"] < _KEEP_SECONDS:
            return _kept["state"]
    from ..store import pool_archive
    from ..store import state as store_state
    from ..store.db import Store

    with Store(settings) as store:
        pool = store._rows(_POOL)
        signins = store._rows(_SIGNINS)
        moves = store._rows(_MOVES)
        phones = store._rows(_PHONES)
        presses = store._rows(_PRESSES)
        archived = int((store._rows(_ARCHIVED) or [{"n": 0}])[0]["n"])
    geo = store_state.get(settings, "geo_ips", {}) or {}
    answer = assemble(pool, signins, moves, phones, presses,
                      geo=geo if isinstance(geo, dict) else {},
                      archived=archived,
                      archived_names=pool_archive.proxy_names(settings),
                      now=datetime.datetime.now(datetime.timezone.utc))
    with _lock:
        _kept.update(at=time.monotonic(), state=answer)
    return answer


def etag(answer: dict) -> str:
    """The answer's own name: an unchanged pool is a 304, not 40KB."""
    import hashlib

    text = json.dumps(answer, sort_keys=True, default=str, separators=(",", ":"))
    return '"' + hashlib.sha256(text.encode("utf-8")).hexdigest()[:20] + '"'


def archive(settings: Settings, limit: int = 300) -> list[dict]:
    """The removed proxies, newest first, for the page's archive drawer."""
    from ..store.db import Store

    with Store(settings) as store:
        rows = store._rows(
            "SELECT payload->>'proxy_name' AS name, payload->>'host' AS host,"
            " payload->>'port' AS port, status, archived_at, archived_by"
            " FROM resources_archive WHERE kind = 'proxy'"
            " ORDER BY archived_at DESC, id DESC LIMIT %s", (int(limit),))
    return [{"n": str(r["name"] or "?"), "at": local(r["archived_at"]),
             "by": str(r["archived_by"] or ""), "was": str(r["status"] or ""),
             "end": f"{r['host'] or ''}:{r['port'] or ''}"} for r in rows]
