"""What the Station and a phone's Live tab are drawn from, as plain data.

`state` composes the Station's JSON (`GET /station/state`, and the first
paint's island); `live` composes one phone's Live-tab JSON. Both are
readers of `store.station` and `store.verdicts` and add no SQL of their
own. Neither raises: a part that could not be read is logged and left at
its empty value.

Two parts are vital. An empty `phones` list read off a store that did not
answer would make the page drop every card and then bring them back as
arrivals, and a Live tab that could not read its phone would be told it
was released - which is final. So a failed read of those marks the answer
`partial` (a private key the web pops before anything is sent): the JSON
routes then answer "the store is not answering" instead of a picture that
is not true.

Times go out as epoch milliseconds. Nothing in the body is computed from
the clock of the read except `now` (and the late/not-late split of a
build), so the body is stable between reads and its ETag holds while
nothing changes.
"""

from __future__ import annotations

import datetime
import logging
import math

from ..config import Settings

log = logging.getLogger(__name__)

#: The private key a composed answer carries when a vital read failed.
PARTIAL = "partial"
#: The last minutes of the hour the card turns amber and says "back in".
LATE_MINUTES = 15
#: The contract's version, for a script that outlives a deploy.
VERSION = 1
#: The five keys, in the order the bar counts them.
KEYS = ("done", "decline", "or", "auth", "failed")
_LANES = ("gpt", "spotify")
_ALL_LANES = ("gpt", "spotify", "other")
_PRODUCT_TITLES = {"chatgpt": "ChatGPT account", "claude": "Claude account",
                   "spotify": "Spotify account"}


# ---------------------------------------------------------------- helpers
def _ms(value) -> int | None:
    """A stamp as epoch milliseconds; None stays None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, datetime.datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return int(value.timestamp() * 1000)


def _now_ms() -> int:
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)


def _rev() -> str:
    from .. import config

    try:
        from . import assets

        fallback = assets.REV
    except Exception as exc:                                      # noqa: BLE001
        log.warning("the station could not read the build's assets (%s)", exc)
        fallback = ""
    return config.revision() or fallback


def _minutes(seconds) -> int:
    """A typical build in whole minutes, never under one."""
    try:
        return max(1, int(math.ceil(float(seconds) / 60.0)))
    except (TypeError, ValueError) as exc:
        log.debug("a typical time was not a number (%s)", exc)
        return 6


def _lane(word) -> str:
    text = str(word or "").strip().lower()
    return text if text in _ALL_LANES else "gpt"


def _bare(gmail) -> bool:
    from ..store import station as store_station

    return str(gmail or "").strip() in ("", store_station.CROSS)


def _last(row: dict) -> dict | None:
    """The last Boot or Change IP pressed on the phone, settled, or None.
    A refusal settles with no detail, which reads as an empty one."""
    from ..store import station as store_station

    if row.get("last_id") is None:
        return None
    detail = row.get("last_detail")
    d = detail if isinstance(detail, dict) else {}
    return {"id": int(row["last_id"]), "verb": str(row.get("last_verb") or ""),
            "ok": row.get("last_status") == "done",
            "note": str(row.get("last_result") or ""),
            "was": store_station.exit_word(str(d.get("was") or "")),
            "now": store_station.exit_word(str(d.get("now") or "")),
            "started": bool(d.get("started") or d.get("url"))}


def me(user: dict) -> dict:
    """Who is looking, in the words the bar and the profile draw."""
    from ..store import users as store_users
    from . import pages

    name = store_users.shown_name(user)
    out = {"id": int(user.get("id") or 0), "name": name,
           "user": str(user.get("username") or ""),
           "initial": name[:1].upper(), "since": "", "pw": "",
           "daypart": "night"}
    try:
        made = pages._moment(user.get("created_at"))
        if made is not None:
            out["since"] = f"since {made.day} {made:%b %Y}"
        out["pw"] = changed_words(user.get("password_changed_at")
                                  or user.get("created_at"))
        now = pages._moment(datetime.datetime.now(datetime.timezone.utc))
        out["daypart"] = _daypart(now.hour if now is not None else 0)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("the station could not word who is looking (%s)", exc)
    return out


def changed_words(value) -> str:
    """"Changed today / yesterday / N days ago", in calendar days of the
    console's zone; "" when the stamp is not known."""
    from . import pages

    moment = pages._moment(value)
    if moment is None:
        return ""
    now = pages._moment(datetime.datetime.now(datetime.timezone.utc))
    days = (now.date() - moment.date()).days
    if days <= 0:
        return "Changed today"
    if days == 1:
        return "Changed yesterday"
    return f"Changed {days} days ago"


def _daypart(hour: int) -> str:
    if 5 <= hour <= 11:
        return "morning"
    if 12 <= hour <= 16:
        return "afternoon"
    if 17 <= hour <= 21:
        return "evening"
    return "night"


def _may(user: dict) -> dict:
    from . import pages

    return {"take": pages._may(user, "may_take_phones"),
            "ip": pages._may(user, "may_change_proxy"),
            "build": pages._may(user, "may_login_accounts")}


# ---------------------------------------------------------- the Station
def _shelves(settings: Settings, user: dict, typical: dict) -> dict:
    from ..store import station as store_station

    shelves = store_station.shelves(settings)
    line = store_station.line_of(settings, int(user["id"]))
    out = {}
    for lane in _LANES:
        got = shelves.get(lane) or {}
        etas = [_ms(e) for e in (got.get("etas") or [])]
        etas = sorted(e for e in etas if e is not None)
        wait = line.get(lane)
        out[lane] = {
            "ready": int(got.get("ready") or 0),
            "building": int(got.get("building") or 0),
            "eta_at": etas[0] if etas else None,
            "etas": etas,
            "late": int(got.get("late") or 0),
            "typical_min": _minutes(got.get("typical_s")
                                    or typical.get(lane, 360)),
            "line": None if not wait else {
                "id": int(wait["id"]), "position": int(wait["position"]),
                "joined_at": _ms(wait.get("joined_at"))},
        }
    return out


def _phone(row: dict) -> dict:
    """One Station hold as its card draws it."""
    from ..store import station as store_station

    busy = str(row.get("busy") or "")
    url = str(row.get("live_url") or "")
    if busy == "change_proxy":
        power = "changing"
    elif busy == "boot_phone":
        power = "starting"
    elif row.get("running") and url and not row.get("tab_closed"):
        power = "on"
    else:
        power = "off"
    bare = _bare(row.get("gmail"))
    watching = bool(row.get("watching"))
    wish = row.get("wish")
    return {
        "serial": str(row.get("serial") or ""),
        "lane": _lane(row.get("lane")),
        "power": power,
        "exit": store_station.exit_word(str(row.get("proxy_name") or "")),
        "taken_at": _ms(row.get("taken_at")),
        "idle_since": None if watching else _ms(row.get("idle_since")),
        "watching": watching,
        "live": url != "",
        "tab_seen": bool(row.get("tab_seen")),
        "bare": bare,
        "gmail": "" if bare else str(row.get("gmail") or ""),
        "pw": "" if bare else str(row.get("gmail_password") or ""),
        "totp": "" if bare else str(row.get("totp") or ""),
        "arrived": ("line" if row.get("from_line") is not None
                    else "build" if wish is not None else "take"),
        "wish": int(wish) if wish is not None else None,
        "called_off": bool(row.get("called_off")),
        "last": _last(row),
    }


def _chips(row: dict, lane: str) -> list[str]:
    from ..store import station as store_station

    chips = []
    phone_gmail = str(row.get("phone_gmail") or "").strip()
    if row.get("no_gmail"):
        chips.append("bare phone")
    elif str(row.get("gmail") or "").strip():
        chips.append(str(row["gmail"]).strip())
    elif phone_gmail and phone_gmail != store_station.CROSS:
        chips.append(phone_gmail)
    else:
        chips.append("next free Gmail")
    named = str(row.get("proxy_name") or "").strip()
    phone_exit = store_station.exit_word(str(row.get("phone_exit") or ""))
    if named and ":" in named:
        chips.append(store_station.exit_word(named))
    elif named:
        chips.append(named)
    elif phone_exit:
        chips.append(phone_exit)
    elif lane == "other":
        chips.append("any IP")
    else:
        chips.append(store_station.lane_word(lane) + " IP")
    account = (str(row.get("app_account") or "").strip()
               or str(row.get("carry_address") or "").strip())
    if account:
        chips.append(account)
    return chips


def _build(row: dict, typical: dict, now_ms: int) -> dict:
    """One Station build as its card draws it."""
    lane = _lane(row.get("purpose"))
    status = str(row.get("status") or "")
    if status == "failed":
        stage = "failed"
    elif row.get("job_status") == "running":
        stage = "building"
    else:
        stage = "queued"
    usual = float(typical.get(lane) or 360)
    eta_at, late = None, False
    claimed = _ms(row.get("claimed_at"))
    if stage == "building" and claimed is not None:
        due = claimed + int(usual * 1000)
        if due > now_ms:
            eta_at = due
        else:
            late = True
    return {"id": int(row["id"]), "lane": lane, "stage": stage,
            "eta_at": eta_at, "late": late, "typical_min": _minutes(usual),
            "bare": bool(row.get("no_gmail")),
            "called_off": row.get("called_off_at") is not None,
            "reason": str(row.get("detail") or "") if stage == "failed" else "",
            "chips": _chips(row, lane)}


def _today(settings: Settings, user: dict) -> list[dict]:
    from ..store import station as store_station
    from ..store import verdicts
    from . import pages, read

    bounds = read.day_bounds(settings, pages.today())
    if bounds is None:
        return []
    rows = verdicts.of_person(settings, int(user["id"]), *bounds)
    return [{"id": int(r["id"]), "hm": pages._hhmm(r.get("at")),
             "v": str(r.get("button") or ""), "lane": _lane(r.get("lane")),
             "serial": str(r.get("serial") or ""),
             "gmail": str(r.get("gmail") or ""),
             "exit": (store_station.exit_word(str(r.get("proxy_name") or ""))
                      or str(r.get("exit_ip") or ""))} for r in rows]


def _note_text(kind: str, serial: str, lane: str) -> str:
    from ..store import station as store_station

    if kind == "given back":
        return (f"{serial} was left alone for an hour, so it went back to "
                f"{store_station.home(lane)}.")
    if kind == "switched off":
        return (f"The tab of {serial} was closed, so the phone is switched "
                f"off. It stays yours for an hour from now – Boot brings it "
                f"back.")
    if kind == "returned":
        return f"Phone {serial} is back on {store_station.home(lane)}."
    return ""


def _notes(settings: Settings, user: dict) -> list[dict]:
    from ..store import station as store_station

    out = []
    for n in store_station.notes(settings, int(user["id"])):
        lane = _lane(n.get("lane"))
        serial = str(n.get("serial") or "")
        text = _note_text(str(n.get("kind") or ""), serial, lane)
        if not text:
            continue
        out.append({"id": str(n.get("key") or ""), "at": _ms(n.get("at")),
                    "serial": serial, "lane": lane, "tone": lane, "text": text})
    return out


def _build_form(settings: Settings, typical: dict) -> dict:
    from ..store import station as store_station

    got = store_station.build_form(settings)
    ips = got.get("free_ips") or {}
    return {"gmails_left": int(got.get("gmails_left") or 0),
            "free_ips": {lane: int(ips.get(lane) or 0) for lane in _ALL_LANES},
            "stopped": bool(got.get("stopped")),
            "typical_min": {lane: _minutes(typical.get(lane, 360))
                            for lane in _ALL_LANES}}


def _empty_shelf() -> dict:
    return {"ready": 0, "building": 0, "eta_at": None, "etas": [], "late": 0,
            "typical_min": 6, "line": None}


def state(settings: Settings, user: dict, *, write: bool = True) -> dict:
    """The Station's state for this person. Never raises.

    With `write` (every GET but HEAD): the person's settled build presses
    lose their typed secrets, their place in a line is stamped as seen,
    and then each line's head is served - stamped first, so a waiter back
    from sleep is served on their own first read.
    """
    from ..store import station as store_station

    uid = int(user["id"])
    grace = int(getattr(settings, "live_tab_grace_seconds", 180) or 180)
    if write:
        steps = (("scrub", store_station.scrub_mine, (settings, uid)),
                 ("stamp", store_station.stamp_line, (settings, uid)),
                 ("serve", store_station.serve_lines, (settings,)))
        for name, step, args in steps:
            try:
                step(*args)
            except Exception as exc:                              # noqa: BLE001
                log.warning("station state: %s did not run (%s)", name, exc)
    out: dict = {
        "v": VERSION, "rev": "", "now": _now_ms(),
        "hold_minutes": int(getattr(settings, "release_after_minutes", 60) or 0),
        "late_minutes": LATE_MINUTES,
        "me": {}, "may": {"take": False, "ip": False, "build": False},
        "shelves": {lane: _empty_shelf() for lane in _LANES},
        "tally": dict({k: 0 for k in KEYS}, all=0),
        "phones": [], "builds": [], "today": [], "notes": [],
        "build_form": {"gmails_left": 0,
                       "free_ips": {lane: 0 for lane in _ALL_LANES},
                       "stopped": False,
                       "typical_min": {lane: 6 for lane in _ALL_LANES}},
    }
    typical = {lane: 360.0 for lane in _ALL_LANES}
    partial = False
    now_ms = out["now"]
    for name, fill in (
            ("rev", lambda: out.update(rev=_rev())),
            ("me", lambda: out.update(me=me(user))),
            ("may", lambda: out.update(may=_may(user))),
            ("typical", lambda: typical.update(store_station.typical(settings))),
            ("shelves", lambda: out.update(shelves=_shelves(settings, user,
                                                            typical))),
            ("today", lambda: out.update(today=_today(settings, user))),
            ("notes", lambda: out.update(notes=_notes(settings, user))),
            ("build_form", lambda: out.update(
                build_form=_build_form(settings, typical)))):
        try:
            fill()
        except Exception as exc:                                  # noqa: BLE001
            log.warning("station state: %s did not read (%s)", name, exc)
    # The two vital reads: an empty bench that is not true would drop
    # every card on the page.
    try:
        out["phones"] = [_phone(r) for r in
                         store_station.mine(settings, uid, grace)]
    except Exception as exc:                                      # noqa: BLE001
        log.warning("station state: the phones did not read (%s)", exc)
        partial = True
    try:
        out["builds"] = [_build(r, typical, now_ms) for r in
                         store_station.builds_of(settings, uid)]
    except Exception as exc:                                      # noqa: BLE001
        log.warning("station state: the builds did not read (%s)", exc)
        partial = True
    tally = {k: 0 for k in KEYS}
    for row in out["today"]:
        if row["v"] in tally:
            tally[row["v"]] += 1
    tally["all"] = sum(tally.values())
    out["tally"] = tally
    if partial:
        out[PARTIAL] = True
    return out


# ------------------------------------------------------------ Live tab
def _viewer() -> dict:
    from . import pages

    return {"w": pages.VIEWER_WIDTH, "box_w": pages.VIEWER_BOX[0],
            "box_h": pages.VIEWER_BOX[1], "bar": pages.VIEWER_BAR}


def _account(row: dict) -> dict | None:
    carried = str(row.get("carry_address") or "").strip()
    if carried:
        return {"title": "App account", "kind": "", "address": carried,
                "pw": str(row.get("carry_password") or ""), "totp": "",
                "carried": True}
    address = str(row.get("acct_address") or "").strip()
    if not address:
        return None
    product = str(row.get("acct_product") or "").strip().lower()
    return {"title": _PRODUCT_TITLES.get(product, "App account"),
            "kind": (str(row.get("acct_category") or "")
                     if product == "spotify" else ""),
            "address": address, "pw": str(row.get("acct_password") or ""),
            "totp": str(row.get("acct_totp") or ""), "carried": False}


def arrival(said: str, note: str) -> dict | None:
    """What the press that opened this tab answered, from its `?said=`."""
    if not said:
        return None
    word, _, req = str(said).partition(":")
    return {"said": word, "req": int(req) if req.isdigit() else None,
            "note": note or ""}


def live(settings: Settings, user: dict, serial: str, said: str = "",
         note: str = "") -> dict:
    """One phone's Live-tab state, for whoever asks. Never raises.

    Everything about the phone - its link, its Gmail, its account - goes
    out only when it is this person's own Station hold; anybody else is
    told it was released, and nothing more. `note` is the sentence of the
    request `said` names, read by the web under its owner rule.
    """
    from ..store import station as store_station

    uid = int(user["id"])
    grace = int(getattr(settings, "live_tab_grace_seconds", 180) or 180)
    out: dict = {"v": VERSION, "rev": "", "now": _now_ms(),
                 "serial": str(serial), "lane": "gpt", "conn": "released",
                 "exit": "", "taken_at": None, "tab_seen": False, "bare": True,
                 "may": {"take": False, "ip": False}, "last": None,
                 "why": "released", "viewer": _viewer()}
    try:
        out["rev"] = _rev()
    except Exception as exc:                                      # noqa: BLE001
        log.warning("live state: the build was not read (%s)", exc)
    try:
        may = _may(user)
        out["may"] = {"take": may["take"], "ip": may["ip"]}
    except Exception as exc:                                      # noqa: BLE001
        log.warning("live state: the permissions did not read (%s)", exc)
    got = arrival(said, note)
    if got is not None:
        out["arrival"] = got
    try:
        row = store_station.live_phone(settings, str(serial), grace)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("live state: phone %s did not read (%s)", serial, exc)
        out[PARTIAL] = True
        return out
    if row is None:
        return out
    out["lane"] = _lane(row.get("lane"))
    mine = (row.get("done_at") is None and row.get("state") == "taken"
            and row.get("taken_at") is not None
            and row.get("owner_id") is not None
            and int(row["owner_id"]) == uid)
    if not mine:
        out["why"] = ("closed" if row.get("state") in ("done", "failed")
                      else "released")
        return out
    busy = str(row.get("busy") or "")
    url = str(row.get("live_url") or "")
    if busy == "boot_phone":
        conn = "booting"
    elif busy == "change_proxy":
        conn = "changing"
    elif row.get("running") and url:
        conn = "on"
    else:
        conn = "off"
    bare = _bare(row.get("gmail"))
    out.update({
        "conn": conn, "why": "",
        "url": url if conn == "on" else "",
        "exit": store_station.exit_word(str(row.get("proxy_name") or "")),
        "taken_at": _ms(row.get("taken_at")),
        "tab_seen": bool(row.get("tab_seen")),
        "bare": bare,
        "gmail": "" if bare else str(row.get("gmail") or ""),
        "pw": "" if bare else str(row.get("gmail_password") or ""),
        "totp": "" if bare else str(row.get("totp") or ""),
        "acct": _account(row),
        "last": _last(row),
    })
    return out
