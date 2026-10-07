"""What the Gmails page draws: the whole Gmail pool, as one answer.

The page is the prototype the user approved (2026-10-07). It filters,
sorts and counts by itself, so it is handed every Gmail of the pool -
where it stands, every sign-in it has had and every press on the phone
it reached - and the counts it judges batches and ranges by, read over
the pool and the archive together, so a batch whose Gmails are all spent
is still judged.

A sign-in is [when, let in 1 | refused 0, Google's reason, captcha
rounds, seconds, phone, the address it came out at, stage]. A press is
[when, d x o a f, who, phone, product]. What a person did to a Gmail on
this page - set it aside, put it back, marked it fixed, changed its
details, kept it for a product - is a line of its story, read off the
requests that did it; a change somebody took back with Undo is not.

Times are Tehran wall-clock strings, "YYYY-MM-DD HH:MM" (proxies_read):
the page compares them as strings and subtracts them as if they were
UTC, which is exact in a zone without daylight saving.

No password, key or recovery address rides in this answer - only
whether a Gmail has a key or a recovery address. An admin's drawer asks
for one Gmail's own (`secrets`).

`assemble` is the whole reading and takes plain rows, so it is tested
without a database; `state` fetches them.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import threading
import time

from ..config import Settings
from .proxies_read import MONTHS, TEHRAN, local

log = logging.getLogger(__name__)

BUTTONS = ("done", "decline", "or", "auth", "failed")
CODE = {"done": "d", "decline": "x", "or": "o", "auth": "a", "failed": "f"}
LANES = ("gpt", "spotify", "other")
#: The reasons that put a Gmail on the ladder (failures.DISTRUST): with a
#: `retry_after`, such a Gmail is waiting to come back on its own.
DISTRUST = frozenset({"captcha_shown", "captcha_text", "verification_blocked",
                      "phone_verification_required", "sign_in_refused",
                      "too_many_attempts"})
#: The verbs whose requests are lines of a Gmail's story.
STORY_VERBS = ("gmails_aside", "gmails_queue", "gmails_mend",
               "gmails_keep_for", "gmail_save", "gmails_add", "gmails_revert",
               "free_gmail", "edit_gmail")
LANE_WORD = {"gpt": "GPT", "spotify": "Spotify"}


def _day(iso: str) -> str:
    """"2026-09-29" as "29 Sep"."""
    return f"{int(iso[8:10])} {MONTHS[int(iso[5:7]) - 1]}" if len(iso) >= 10 else ""


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _date(text) -> str:
    """The YYYY-MM-DD a sheet-era text column starts with, or ""."""
    hit = _DATE.match(str(text or "").strip())
    return hit.group(0) if hit else ""


def note_words(note: str) -> str:
    """The farm's note in the page's words: a proxy is a proxy, never an
    exit (the user, 2026-10-02), there is no refund anywhere on the page
    (2026-10-02), and the switch is how a Gmail goes back in the queue."""
    text = str(note or "")
    text = re.sub(r"\s+-\s+it is on the list to claim back from[^.]*\.", ".", text)
    text = re.sub(r"Press Free on the row", "Turn it on in its row", text)
    text = re.sub(r"\b([Aa])n exit\b", lambda m: m.group(1) + " proxy", text)
    text = re.sub(r"\b([Ee])xits\b",
                  lambda m: "Proxies" if m.group(1) == "E" else "proxies", text)
    text = re.sub(r"\b([Ee])xit\b",
                  lambda m: "Proxy" if m.group(1) == "E" else "proxy", text)
    kept = [s for s in re.split(r"(?<=[.!?])\s+", text)
            if not re.search(r"refund|claim(ed)? (it )?back|list to claim", s, re.I)]
    return " ".join(kept).strip()[:400]


def _lane(lane: str, app_account: str, spotify: set) -> str:
    """The product a press was on: its own word since rev 42; before it, a
    Spotify account means Spotify and anything else GPT - the farm's own
    rule (store/schema.sql, rev 42)."""
    word = str(lane or "").strip().lower()
    if word in LANES:
        return word
    return "spotify" if str(app_account or "").lower() in spotify else "gpt"


def _bump(m: dict, key: str, n: int = 1) -> None:
    m[key] = m.get(key, 0) + n


def _ranges(when: str, since: dict) -> tuple[str, ...]:
    out = ["a"]
    if when >= since["d"]:
        out.append("d")
    if when >= since["t"]:
        out.append("t")
    return tuple(out)


def _place(row: dict) -> tuple[str, str]:
    """(the status word the page reads, its `next`): the row's own status,
    except that a row on the seller's list or one the farm cannot read is
    out of the queue whatever its status says - nothing claims it."""
    status = str(row.get("status") or "").strip().lower()
    reason = str(row.get("last_reason") or "").strip().lower()
    if row.get("error") and not status:
        return "unreadable", ""
    if str(row.get("refund_state") or "").strip() and not status:
        return reason or "held_out", ""
    waiting = (status in DISTRUST and row.get("retry_after") is not None
               and not str(row.get("refund_state") or "").strip()
               and not row.get("error"))
    return status, local(row.get("retry_after")) if waiting else ""


def _story(events, by_id: dict, by_address: dict) -> dict[int, list]:
    """Each Gmail's lines from the requests that changed it - less the
    changes somebody took back."""
    undone = set()
    for ev in events:
        if ev["verb"] != "gmails_revert":
            continue
        detail = ev.get("detail") if isinstance(ev.get("detail"), dict) else {}
        for req in detail.get("reqs") or []:
            for rid in detail.get("back") or []:
                if str(req).isdecimal() and str(rid).isdecimal():
                    undone.add((int(req), int(rid)))
    out: dict[int, list] = {}

    def line(rid, at, cls, title, small):
        out.setdefault(rid, []).append([at, cls, title, small])

    for ev in events:
        verb, at = ev["verb"], local(ev.get("requested_at"))
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        detail = ev.get("detail") if isinstance(ev.get("detail"), dict) else {}
        by = str(payload.get("by") or ev.get("by_name") or "someone")
        req = int(ev.get("id") or 0)
        ids = [int(i) for i in detail.get("ids") or [] if str(i).isdecimal()]
        ids = [i for i in ids if (req, i) not in undone and i in by_id]
        if verb == "gmails_aside":
            for i in ids:
                line(i, at, "aside", "Set aside", f"out of the queue until turned on · by {by}")
        elif verb == "gmails_queue":
            for i in ids:
                line(i, at, "queue", "Put back in the queue", f"by {by}")
        elif verb == "gmails_mend":
            for i in ids:
                line(i, at, "back", "Marked as fixed",
                     f"back in the pool, its tries start again · by {by}")
        elif verb == "gmails_keep_for":
            lane = str(detail.get("lane") or "")
            for i in ids:
                if lane in LANE_WORD:
                    other = LANE_WORD["spotify" if lane == "gpt" else "gpt"]
                    line(i, at, "edit", f"Kept for {LANE_WORD[lane]}",
                         f"{other} builds pass it by · by {by}")
                else:
                    line(i, at, "edit", "Open to any product", f"by {by}")
        elif verb == "gmail_save":
            said = str(detail.get("said") or "")
            for i in ids:
                if said:
                    line(i, at, "edit", "Details changed", f"{said} · by {by}")
                if detail.get("mended"):
                    line(i, at, "back", "Marked as fixed",
                         f"back in the pool, its tries start again · by {by}")
        elif verb == "gmails_add":
            for r in detail.get("returned") or []:
                i = int(r.get("id") or 0)
                if (req, i) in undone or i not in by_id:
                    continue
                if r.get("said"):
                    line(i, at, "edit", "Details changed",
                         f"{r['said']} · from a pasted line · by {by}")
                if r.get("mended", True):
                    line(i, at, "back", "Marked as fixed",
                         f"back in the pool, its tries start again · by {by}")
        elif verb in ("free_gmail", "edit_gmail"):
            i = by_address.get(str(payload.get("address") or "").strip().lower())
            if i is None:
                continue
            if verb == "free_gmail":
                line(i, at, "queue", "Put back in the queue", f"by {by}")
            else:
                line(i, at, "edit", "Details changed", f"by {by}")
    for lines in out.values():
        lines.sort(key=lambda x: x[0])
    return out


def assemble(pool, archived, signins, presses, phones, events, *,
             spotify=(), now: datetime.datetime) -> dict:
    """The page's whole state from plain rows (see the module's words)."""
    now = now.astimezone(TEHRAN)
    today = now.strftime("%Y-%m-%d")
    since = {"t": today + " 00:00",
             "d": (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d") + " 00:00"}
    spotify = {str(a).lower() for a in spotify or ()}

    # Which batch each address belongs to: the pool's row, else the
    # archive's, else what the sign-in itself wrote down.
    seller_of: dict[str, str] = {}
    for r in archived:
        seller_of[str(r.get("address") or "").lower()] = str(r.get("seller") or "")
    for r in pool:
        seller_of[str(r.get("address") or "").lower()] = str(r.get("seller") or "")

    def empty():
        return {"t": {}, "d": {}, "a": {}}

    batch: dict[str, dict] = {}

    def batch_of(key):
        return batch.setdefault(key, {"n": 0, "arch": 0, "first": "", "last": "",
                                      "g": empty(), "v": empty()})

    for r, arch in [(r, False) for r in pool] + [(r, True) for r in archived]:
        b = batch_of(str(r.get("seller") or ""))
        b["n"] += 1
        b["arch"] += 1 if arch else 0
        when = _date(r.get("purchased_on")) or local(r.get("created_at"))[:10]
        if when:
            b["first"] = min(b["first"] or when, when)
            b["last"] = max(b["last"], when)

    # ---------------------------------------------------------- sign-ins
    signins = sorted(signins, key=lambda s: s["at"])
    G, GS = empty(), empty()
    days: dict[str, list] = {}
    tries_at: dict[str, int] = {}
    bytry: dict[int, list] = {}
    tries_of: dict[str, list] = {}
    first = ""
    for s in signins:
        when = local(s["at"])
        gmail = str(s.get("gmail") or "").lower()
        ok = bool(s.get("ok"))
        reason = "signed_in" if ok else (str(s.get("reason") or "") or "unknown")
        stage = str(s.get("stage") or "")
        first = first or when[:10]
        for r in _ranges(when, since):
            _bump(G[r], reason)
            GS[r].setdefault(reason, {})
            _bump(GS[r][reason], stage)
        d = days.setdefault(when[:10], [when[:10], 0, 0])
        d[1] += 1
        d[2] += 1 if ok else 0
        tries_at[gmail] = tries_at.get(gmail, 0) + 1
        t = bytry.setdefault(min(tries_at[gmail], 4), [min(tries_at[gmail], 4), 0, 0])
        t[1] += 1
        t[2] += 1 if ok else 0
        key = seller_of.get(gmail, str(s.get("seller") or ""))
        for r in _ranges(when, since):
            _bump(batch_of(key)["g"][r], reason)
        tries_of.setdefault(gmail, []).append([
            when, 1 if ok else 0, reason, int(s.get("captcha_rounds") or 0),
            int(round(float(s.get("seconds") or 0))), str(s.get("serial") or ""),
            str(s.get("exit_ip") or s.get("host") or ""), stage])
    # Every day from the first sign-in to today, the quiet ones too: the
    # pace is read over the last three whole days.
    if first:
        day = datetime.date.fromisoformat(first)
        end = now.date()
        while day <= end:
            iso = day.isoformat()
            days.setdefault(iso, [iso, 0, 0])
            day += datetime.timedelta(days=1)

    # ------------------------------------------------------------ presses
    V, VP = empty(), empty()
    VPB: dict[str, dict] = {}
    presses_of: dict[str, list] = {}
    for p in sorted(presses, key=lambda p: p["at"]):
        button = str(p.get("button") or "")
        if button not in CODE:
            continue
        when = local(p["at"])
        gmail = str(p.get("gmail") or "").lower()
        lane = _lane(p.get("lane"), p.get("app_account"), spotify)
        key = seller_of.get(gmail, "")
        for r in _ranges(when, since):
            _bump(V[r], button)
            _bump(VP[r].setdefault(lane, {}), button)
            _bump(batch_of(key)["v"][r], button)
            _bump(VPB.setdefault(key, empty())[r].setdefault(lane, {}), button)
        presses_of.setdefault(gmail, []).append(
            [when, CODE[button], str(p.get("by_name") or ""),
             str(p.get("serial") or ""), lane])

    # ----------------------------------------------------------- the pool
    lane_of = {}
    for ph in phones:
        word = str(ph.get("purpose") or "").strip().lower()
        lane_of[str(ph.get("serial") or "")] = word if word in LANES else "gpt"
    by_id = {int(r["id"]): r for r in pool}
    by_address = {str(r.get("address") or "").lower(): int(r["id"]) for r in pool}
    story = _story(events, by_id, by_address)
    rows = []
    for r in pool:
        address = str(r.get("address") or "")
        low = address.lower()
        st, nxt = _place(r)
        serial = str(r.get("serial") or "").strip()
        e = {"id": int(r["id"]), "a": address, "sell": str(r.get("seller") or ""),
             "st": st, "why": str(r.get("last_reason") or ""),
             "tries": int(r.get("tries") or 0), "next": nxt,
             "serial": serial if st in ("in_use", "ready") else "",
             "added": local(r.get("created_at")),
             "bought": _date(r.get("purchased_on")),
             "used": _date(r.get("used_at")),
             "key": bool(r.get("has_key")), "rec": bool(r.get("has_rec")),
             "note": note_words(r.get("note") or ""),
             "t": tries_of.get(low, []), "p": presses_of.get(low, []),
             "for": (str(r.get("purpose") or "").strip().lower()
                     if str(r.get("purpose") or "").strip().lower() in LANE_WORD
                     else "")}
        if st in ("in_use", "ready") and serial in lane_of:
            e["on"] = lane_of[serial]
        if r.get("fixed_at") is not None:
            e["back"] = {"at": local(r["fixed_at"]), "by": str(r.get("fixed_by") or "")}
        if story.get(e["id"]):
            e["log"] = story[e["id"]]
        rows.append(e)
    rows.sort(key=lambda e: e["id"])

    tag = f"{now.day}{MONTHS[now.month - 1].upper()}"
    # No clock in the answer: an unchanged pool is the same answer, and the
    # page's poll a 304 (proxies_read).
    return {"day": {"iso": today, "tag": tag, "word": _day(today)},
            "archived": len(archived), "firstSignin": first,
            "days": [days[k] for k in sorted(days)],
            "bytry": [bytry[k] for k in sorted(bytry)],
            "G": G, "GS": GS, "V": V, "VP": VP, "VPB": VPB,
            "batch": batch, "rows": rows}


_POOL = ("SELECT id, address, seller, status, last_reason, tries, retry_after,"
         " serial, created_at, purchased_on, used_at,"
         " coalesce(totp_secret, '') <> '' AS has_key,"
         " coalesce(recovery_email, '') <> '' AS has_rec,"
         " note, purpose, refund_state, error, fixed_at, fixed_by"
         " FROM resources WHERE kind = 'gmail'")
_ARCHIVED = ("SELECT id, address, seller, status, archived_at,"
             " payload->>'purchased_on' AS purchased_on,"
             " payload->>'created_at' AS created_at"
             " FROM resources_archive WHERE kind = 'gmail'")
_SIGNINS = ("SELECT gmail, at, ok, reason, captcha_rounds, seconds, serial,"
            " exit_ip, host, seller, stage FROM signins")
_PRESSES = ("SELECT gmail, at, button, by_name, serial, lane, app_account"
            " FROM verdicts WHERE coalesce(gmail, '') <> ''")
_PHONES = "SELECT serial, purpose FROM phones WHERE done_at IS NULL"
#: A story's words only - never a request's typed details - and sixty
#: days of them, which is longer than a Gmail stays in the pool.
_EVENTS = ("SELECT a.id, a.verb, a.requested_at,"
           " jsonb_build_object('by', a.payload->'by',"
           "   'address', a.payload->'address') AS payload,"
           " jsonb_build_object('ids', a.detail->'ids', 'said', a.detail->'said',"
           "   'mended', a.detail->'mended', 'lane', a.detail->'lane',"
           "   'returned', a.detail->'returned', 'reqs', a.detail->'reqs',"
           "   'back', a.detail->'back') AS detail,"
           " coalesce(u.username, '') AS by_name"
           " FROM actions a LEFT JOIN users u ON u.id = a.requested_by"
           " WHERE a.verb = ANY(%s) AND a.status = 'done'"
           "   AND a.requested_at > now() - interval '60 days'")
_SPOTIFY = ("SELECT lower(address) FROM resources"
            " WHERE kind = 'app' AND lower(product) = 'spotify'"
            " UNION SELECT lower(address) FROM resources_archive"
            " WHERE kind = 'app' AND lower(payload->>'product') = 'spotify'")

#: The last answer, for a few seconds: every open page asks every twenty,
#: and two admins' pages need not read the same rows twice.
_KEEP_SECONDS = 4.0
_kept: dict = {}
_lock = threading.Lock()
#: Bumped by `forget`: a read that began before a press is not kept over
#: the state the press left (a slower read finishing last would be).
_gen = [0]


def _archived_rows(rows) -> list[dict]:
    """The archive's own stamps come back as text from its payload."""
    out = []
    for r in rows:
        r = dict(r)
        made = r.get("created_at")
        if isinstance(made, str) and made:
            try:
                r["created_at"] = datetime.datetime.fromisoformat(made)
            except ValueError:
                log.debug("archived Gmail %s has a stamp nothing reads (%r);"
                          " its batch's days go by its purchase date",
                          r.get("id"), made)
                r["created_at"] = None
        out.append(r)
    return out


def state(settings: Settings, *, fresh: bool = False) -> dict:
    """The page's state, read now - or the one read a moment ago."""
    with _lock:
        if not fresh and _kept and time.monotonic() - _kept["at"] < _KEEP_SECONDS:
            return _kept["state"]
        began = _gen[0]
    from ..store.db import Store

    with Store(settings) as store:
        pool = store._rows(_POOL)
        archived = _archived_rows(store._rows(_ARCHIVED))
        signins = store._rows(_SIGNINS)
        presses = store._rows(_PRESSES)
        phones = store._rows(_PHONES)
        events = store._rows(_EVENTS, (list(STORY_VERBS),))
        spotify = [r["lower"] for r in store._rows(_SPOTIFY)]
    answer = assemble(pool, archived, signins, presses, phones, events,
                      spotify=spotify,
                      now=datetime.datetime.now(datetime.timezone.utc))
    with _lock:
        if _gen[0] == began:
            _kept.update(at=time.monotonic(), state=answer)
    return answer


def forget() -> None:
    """Drop the answer kept a moment ago - after a press, the next read is
    the pool as the press left it, and a read already on its way when the
    press landed is not kept."""
    with _lock:
        _kept.clear()
        _gen[0] += 1


def etag(answer: dict) -> str:
    """The answer's own name: an unchanged pool is a 304, not half a
    megabyte."""
    import hashlib

    text = json.dumps(answer, sort_keys=True, default=str, separators=(",", ":"))
    return '"' + hashlib.sha256(text.encode("utf-8")).hexdigest()[:20] + '"'


def archive(settings: Settings, *, q: str = "", limit: int = 400) -> dict:
    """The archived Gmails, newest first - those matching `q` when given -
    for the page's archive drawer: {rows, total, matched}."""
    from ..store.db import Store

    q = str(q or "").strip().lower()[:80]
    like = f"%{q}%"
    with Store(settings) as store:
        rows = store._rows(
            "SELECT id, address, seller, status, archived_at, archived_by,"
            " payload->>'used_at' AS used_at"
            " FROM resources_archive WHERE kind = 'gmail'"
            "   AND (%s = '' OR lower(address) LIKE %s OR lower(seller) LIKE %s)"
            " ORDER BY archived_at DESC, id DESC LIMIT %s",
            (q, like, like, max(1, min(int(limit), 2000))))
        counted = (store._rows(
            "SELECT count(*) AS n, count(*) FILTER (WHERE %s = ''"
            "   OR lower(address) LIKE %s OR lower(seller) LIKE %s) AS hits"
            " FROM resources_archive WHERE kind = 'gmail'", (q, like, like))
            or [{"n": 0, "hits": 0}])[0]
    return {"total": int(counted["n"] or 0), "matched": int(counted["hits"] or 0),
            "rows": [
        {"id": int(r["id"]), "a": str(r["address"] or ""),
         "f": str(r["seller"] or ""), "was": str(r["status"] or ""),
         "at": local(r["archived_at"]), "by": str(r["archived_by"] or ""),
         "used": _date(r.get("used_at"))} for r in rows]}
