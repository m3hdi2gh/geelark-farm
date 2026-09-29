"""The Station: an operator's own phones, in the store.

An operator takes a phone from a lane's shelf in one statement, holds it
for an hour that counts only while its Live tab is closed, and gives it
back to the back of its shelf. A person who presses Take on an empty
shelf while that lane has a build coming joins the lane's line, and the
web serves the head of the line whenever it reads the state (rev 42,
2026-09-29).

Every read and write the Station adds lives here, so the verbs, the sweep
and the web share one set of predicates: the shelf the bar counts is the
shelf a Take takes from, and the hour the card draws is the hour the
sweep keeps.

Reads go through `Store._rows`, single writes through `Store._write`, and
anything longer through one `connect()` transaction that commits at its
end. jsonb is bound as `json.dumps(...)` and an array as a `list`. This
module imports nothing from pools, builder, web or verbs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from ..config import Settings
from .actions import NOTIFY_CHANNEL
from .db import Store, connect

log = logging.getLogger(__name__)

#: The two lanes that have a shelf and a line.
LANES = ("gpt", "spotify")


# ------------------------------------------------------ shared fragments
def lane_sql(a: str = "p") -> str:
    """A phone's lane for the Station: the tile, the shelf, the verdict row.
    Blank and 'gpt' are GPT (the builder writes 'gpt'; phones from before
    lanes are blank); 'other' is a hand build for another app."""
    return (f"CASE WHEN lower({a}.purpose) = 'spotify' THEN 'spotify'"
            f" WHEN lower({a}.purpose) = 'other' THEN 'other' ELSE 'gpt' END")


def shelf_lane(a: str = "q") -> str:
    """The shelf lane of a phone that is on a shelf (never 'other')."""
    return f"CASE WHEN lower({a}.purpose) = 'spotify' THEN 'spotify' ELSE 'gpt' END"


#: The three verbs that switch a phone on or off, and how old a pending
#: one may be before it is taken for an orphan a restart left behind.
POWER_VERBS = ("boot_phone", "change_proxy", "power_off_phone")
POWER_STALE_SECONDS = 180


def power_pending(a: str = "p",
                  verbs: tuple = ("boot_phone", "change_proxy")) -> str:
    """A pending, fresh press of one of `verbs` on the phone aliased `a`."""
    names = ", ".join("'" + v + "'" for v in verbs)
    return (f"EXISTS (SELECT 1 FROM actions x"
            f" WHERE x.status IN ('queued', 'running')"
            f" AND x.verb IN ({names}) AND x.payload->>'serial' = {a}.serial"
            f" AND coalesce(x.executed_at, x.requested_at)"
            f"     > now() - interval '{POWER_STALE_SECONDS} seconds')")


def on_shelf(a: str = "q") -> str:
    """On a lane's shelf (brief B4). One predicate for the count and the
    take, so the bar can never say 3 while Take hands out nothing. A
    pending action of any verb keeps the phone off the shelf, except a
    power press older than POWER_STALE_SECONDS (an orphan: `expire_running`
    closes it only after QUICK_COMMAND_SECONDS = 600)."""
    return (f"{a}.done_at IS NULL AND {a}.owner_id IS NULL"
            f" AND {a}.taken_at IS NULL AND {a}.state IN ('', 'unused')"
            f" AND {a}.status IN ('ready', 'app_only') AND NOT {a}.running"
            f" AND lower({a}.purpose) IN ('', 'gpt', 'spotify')"
            f" AND NOT EXISTS (SELECT 1 FROM jobs j"
            f"   WHERE j.status IN ('queued', 'running')"
            f"   AND j.payload->'phone'->>'serial' = {a}.serial)"
            f" AND NOT EXISTS (SELECT 1 FROM actions x"
            f"   WHERE x.status IN ('queued', 'running')"
            f"   AND x.payload->>'serial' = {a}.serial"
            f"   AND NOT (x.verb IN ('boot_phone', 'change_proxy', 'power_off_phone')"
            f"        AND coalesce(x.executed_at, x.requested_at)"
            f"            <= now() - interval '{POWER_STALE_SECONDS} seconds'))")


#: Oldest first; a phone given back gets state_at = now() and goes to the back.
SHELF_ORDER = "state_at, id"


def watching(a: str = "p") -> str:
    """The Live tab is open on the phone: a beat within the grace, no close
    since. Needs the named parameter `grace`."""
    return (f"({a}.tab_closed_at IS NULL AND {a}.watched_at IS NOT NULL"
            f" AND {a}.watched_at >= now() - %(grace)s * interval '1 second')")


def idle_since(a: str = "p") -> str:
    """Since when a Station hold has been left alone: taken, last beat, or
    closed."""
    return f"greatest({a}.taken_at, {a}.watched_at, {a}.tab_closed_at)"


#: The cross a Phones row writes for "none" (pools.PhoneLog.NO).
CROSS = "✗"
LINE_SEEN_SECONDS = 90       # the head of a line must have polled this recently
LINE_GONE_SECONDS = 600      # a wait nobody polled for this long ends as 'gone'
RETURNED_GUARD_MINUTES = 10  # never served what they gave back this recently
SERVED_JUST_NOW_SECONDS = 10 # a Take or Leave this soon after a serve reads the serve
TYPICAL_FALLBACK_S = 360     # six minutes, the prototype's figure

#: A fresh pending Boot or Change IP on the phone aliased p, by verb.
_BUSY = (f"(SELECT x.verb FROM actions x"
         f" WHERE x.status IN ('queued', 'running')"
         f" AND x.verb IN ('boot_phone', 'change_proxy')"
         f" AND x.payload->>'serial' = p.serial"
         f" AND coalesce(x.executed_at, x.requested_at)"
         f"     > now() - interval '{POWER_STALE_SECONDS} seconds'"
         f" ORDER BY x.id LIMIT 1)")


# --------------------------------------------------------------- words
def exit_word(name: str) -> str:
    """An exit as a person reads it: never a user, password or port."""
    t = (name or "").strip()
    if "://" in t or "@" in t:
        t = t.rpartition("@")[2].split("://")[-1]
        return t.rpartition(":")[0] or t
    parts = t.split(":")
    if len(parts) >= 2 and parts[1].isdigit():
        return parts[0]          # host:port and host:port:user:password
    return t


_LANE_WORDS = {"gpt": "GPT", "spotify": "Spotify", "other": "Other"}


def lane_word(lane: str) -> str:
    return _LANE_WORDS.get(str(lane or ""), "GPT")


def home(lane: str) -> str:
    """Where a phone goes when it is given back."""
    if lane == "other":
        return "the farm"
    return "the " + lane_word(lane) + " shelf"


def _lane_of(purpose) -> str:
    """`lane_sql`'s rule, in Python."""
    word = str(purpose or "").strip().lower()
    return word if word in ("spotify", "other") else "gpt"


def _seconds(value) -> float | None:
    return None if value is None else float(value)


# ------------------------------------------------ shelves, line and take
_SHELF_COUNT = (f"SELECT {shelf_lane('q')} AS lane, count(*) AS n"
                f" FROM phones q WHERE {on_shelf('q')} GROUP BY 1")

_KEEPER_BUILDS = (
    "SELECT coalesce(nullif(lower(payload->>'purpose'), ''), 'gpt') AS lane,"
    " status, claimed_at"
    " FROM jobs"
    " WHERE kind = 'build' AND status IN ('queued', 'running')"
    " AND payload->'want' IS NULL")

_TYPICAL = (
    "SELECT lane, percentile_cont(0.5) WITHIN GROUP (ORDER BY s) AS typical_s,"
    " count(*) AS n"
    " FROM (SELECT coalesce(nullif(lower(payload->>'purpose'), ''),"
    "                       nullif(lower(payload->'want'->>'purpose'), ''),"
    "                       'gpt') AS lane,"
    "              extract(epoch FROM done_at - claimed_at) AS s"
    "         FROM jobs"
    "        WHERE kind = 'build' AND status = 'done' AND claimed_at IS NOT NULL"
    "          AND done_at IS NOT NULL"
    "          AND coalesce(result->>'worked', '') = 'true'"
    "        ORDER BY id DESC LIMIT 200) t"
    " GROUP BY lane")


def _empty_shelves() -> dict:
    return {lane: {"ready": 0, "building": 0, "etas": [], "late": 0,
                   "typical_s": float(TYPICAL_FALLBACK_S)} for lane in LANES}


def typical(settings: Settings) -> dict:
    """Seconds a successful build of each lane takes (the median of the
    last 200), six minutes for a lane with fewer than three. Never raises."""
    out = {"gpt": float(TYPICAL_FALLBACK_S), "spotify": float(TYPICAL_FALLBACK_S),
           "other": float(TYPICAL_FALLBACK_S)}
    try:
        with Store(settings) as store:
            rows = store._rows(_TYPICAL)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not read how long a build takes (%s)", exc)
        return out
    for row in rows:
        lane = str(row.get("lane") or "")
        if (lane in out and int(row.get("n") or 0) >= 3
                and row.get("typical_s") is not None):
            out[lane] = float(row["typical_s"])
    return out


def shelves(settings: Settings) -> dict:
    """What is on each lane's shelf, and what is on its way. Never raises;
    a failure answers the empty shape."""
    out = _empty_shelves()
    try:
        with Store(settings) as store:
            counts = store._rows(_SHELF_COUNT)
            builds = store._rows(_KEEPER_BUILDS)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not read the shelves (%s)", exc)
        return out
    usual = typical(settings)
    for lane in LANES:
        out[lane]["typical_s"] = usual[lane]
    for row in counts:
        if row["lane"] in out:
            out[row["lane"]]["ready"] = int(row["n"])
    now = datetime.now(timezone.utc)
    for row in builds:
        lane = str(row.get("lane") or "")
        if lane not in out:
            continue
        shelf = out[lane]
        shelf["building"] += 1
        if row.get("status") != "running" or row.get("claimed_at") is None:
            continue
        eta = row["claimed_at"] + timedelta(seconds=shelf["typical_s"])
        if eta > now:
            shelf["etas"].append(eta)
        else:
            shelf["late"] += 1
    for lane in LANES:
        out[lane]["etas"].sort()
    return out


_EXPIRE_WAITS = (
    "UPDATE station_line SET ended_at = now(), ended_why = 'gone',"
    " updated_at = now()"
    " WHERE ended_at IS NULL AND seen_at < now() - %s * interval '1 second'"
    " RETURNING id")

_NOT_RETURNED = ("NOT (q.last_owner_id IS NOT DISTINCT FROM {who}"
                 " AND q.state_at > now() - %(guard)s * interval '1 minute')")

_SERVE = (
    "WITH head AS ("
    "  SELECT w.id, w.user_id FROM station_line w"
    "    JOIN users u ON u.id = w.user_id AND u.active"
    "                AND (u.role = 'admin' OR u.may_take_phones)"
    "   WHERE w.lane = %(lane)s AND w.ended_at IS NULL"
    "     AND w.seen_at > now() - %(seen)s * interval '1 second'"
    "     AND EXISTS (SELECT 1 FROM phones q"
    f"                 WHERE {on_shelf('q')} AND {shelf_lane('q')} = %(lane)s"
    "                   AND " + _NOT_RETURNED.format(who="w.user_id") + ")"
    "   ORDER BY w.joined_at, w.id LIMIT 1 FOR UPDATE OF w SKIP LOCKED),"
    " picked AS ("
    "  SELECT q.id FROM phones q, head"
    f"   WHERE {on_shelf('q')} AND {shelf_lane('q')} = %(lane)s"
    "     AND " + _NOT_RETURNED.format(who="head.user_id") +
    "   ORDER BY q.state_at, q.id LIMIT 1 FOR UPDATE OF q SKIP LOCKED),"
    " took AS ("
    "  UPDATE phones p SET state = 'taken', owner_id = head.user_id,"
    "         taken_at = now(), state_at = now(), watched_at = NULL,"
    "         tab_closed_at = NULL, live_url = '', updated_at = now()"
    "    FROM picked, head WHERE p.id = picked.id"
    "  RETURNING p.serial, head.id AS wait_id, head.user_id),"
    " served AS ("
    "  UPDATE station_line w SET ended_at = now(), ended_why = 'served',"
    "         serial = took.serial, updated_at = now()"
    "    FROM took WHERE w.id = took.wait_id"
    "  RETURNING w.id, w.user_id, w.serial, w.lane)"
    " INSERT INTO actions (verb, payload, requested_by, status, result,"
    "                      executed_at, finished_at, idem_key)"
    " SELECT 'serve_line',"
    "        jsonb_build_object('lane', s.lane, 'serial', s.serial, 'wait', s.id,"
    "                           'by_id', s.user_id),"
    "        s.user_id, 'done',"
    "        'Your ' || CASE WHEN s.lane = 'spotify' THEN 'Spotify' ELSE 'GPT' END"
    "          || ' phone is here: ' || s.serial || '. Press Boot to switch it on.',"
    "        now(), now(), 'serve:' || s.id"
    "   FROM served s"
    " RETURNING requested_by AS user_id, payload->>'serial' AS serial,"
    "           payload->>'lane' AS lane")


def serve_lines(settings: Settings,
                lanes: tuple[str, ...] = LANES) -> list[dict]:
    """Hand the next shelf phone to the head of each lane's line. Returns
    one `{"user_id", "serial", "lane"}` a waiter served. Never raises."""
    served: list[dict] = []
    try:
        with Store(settings) as store:
            gone = store._write(_EXPIRE_WAITS, (LINE_GONE_SECONDS,))
        if gone:
            log.info("%d wait(s) in a station line ended: nobody looked for "
                     "ten minutes", len(gone))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not end the waits nobody looks at (%s)", exc)
    for lane in lanes:
        if lane not in LANES:
            continue
        params = {"lane": lane, "seen": LINE_SEEN_SECONDS,
                  "guard": RETURNED_GUARD_MINUTES}
        try:
            for _ in range(10):
                with connect(settings) as conn:
                    row = conn.execute(_SERVE, params).fetchone()
                    conn.commit()
                if row is None:
                    break
                served.append({"user_id": int(row[0]), "serial": str(row[1]),
                               "lane": str(row[2])})
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not serve the %s line (%s)", lane, exc)
    return served


def stamp_line(settings: Settings, user_id: int) -> None:
    """This person is still looking. Never moves `updated_at`: a poll is
    not news for the live stream. Never raises."""
    try:
        with Store(settings) as store:
            store._write(
                "UPDATE station_line SET seen_at = now()"
                " WHERE user_id = %s AND ended_at IS NULL RETURNING id",
                (int(user_id),))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not stamp the line of user %s (%s)", user_id, exc)


_POSITION = ("(SELECT count(*) FROM station_line o"
             " WHERE o.lane = w.lane AND o.ended_at IS NULL"
             " AND (o.joined_at, o.id) <= (w.joined_at, w.id))")


def line_of(settings: Settings, user_id: int) -> dict:
    """This person's open waits, by lane: `{"id", "position", "joined_at"}`
    or None. Never raises."""
    out: dict = {lane: None for lane in LANES}
    try:
        with Store(settings) as store:
            rows = store._rows(
                "SELECT w.id, w.lane, w.joined_at, " + _POSITION + " AS position"
                " FROM station_line w WHERE w.user_id = %s AND w.ended_at IS NULL",
                (int(user_id),))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not read the line of user %s (%s)", user_id, exc)
        return out
    for row in rows:
        if row["lane"] in out:
            out[row["lane"]] = {"id": int(row["id"]),
                                "position": int(row["position"]),
                                "joined_at": row["joined_at"]}
    return out


_SERVED_RECENTLY = (
    "SELECT l.serial FROM station_line l"
    " JOIN phones p ON p.serial = l.serial AND p.done_at IS NULL"
    "              AND p.owner_id = l.user_id AND p.taken_at IS NOT NULL"
    " WHERE l.user_id = %s AND l.lane = %s AND l.ended_why = 'served'"
    "   AND l.ended_at > now() - %s * interval '1 second'"
    " ORDER BY l.id DESC LIMIT 1")


def served_recently(settings: Settings, user_id: int, lane: str) -> str:
    """The serial a serve handed this person in this lane a moment ago, if
    it is still their Station hold, else ""."""
    with Store(settings) as store:
        rows = store._rows(_SERVED_RECENTLY,
                           (int(user_id), lane, SERVED_JUST_NOW_SECONDS))
    return str(rows[0]["serial"]) if rows else ""


_PRESS = ("INSERT INTO actions (verb, payload, requested_by, status,"
          " executed_at, idem_key)"
          " VALUES (%s, %s, %s, 'running', now(), %s)"
          " ON CONFLICT (idem_key) DO NOTHING RETURNING id")

_CLOSE_PRESS = ("UPDATE actions SET status = %s, result = %s, detail = %s,"
                " finished_at = now() WHERE id = %s")

_CLAIM = (
    "UPDATE phones p SET state = 'taken', owner_id = %(uid)s, taken_at = now(),"
    "       state_at = now(), watched_at = NULL, tab_closed_at = NULL,"
    "       live_url = '', updated_at = now()"
    " WHERE p.id = (SELECT q.id FROM phones q WHERE " + on_shelf("q") +
    "                AND " + shelf_lane("q") + " = %(lane)s"
    "              ORDER BY q.state_at, q.id LIMIT 1 FOR UPDATE SKIP LOCKED)"
    " RETURNING p.serial")

_BUILDING = (
    "SELECT count(*) FROM jobs"
    " WHERE kind = 'build' AND status IN ('queued', 'running')"
    " AND payload->'want' IS NULL"
    " AND coalesce(nullif(lower(payload->>'purpose'), ''), 'gpt') = %s")

_MY_POSITION = (
    "SELECT w.id, " + _POSITION + " AS position"
    " FROM station_line w"
    " WHERE w.user_id = %s AND w.lane = %s AND w.ended_at IS NULL")


def _twin(conn, idem_key: str, lane: str) -> dict:
    """What the first press with this key answered."""
    row = conn.execute(
        "SELECT id, status, result, detail FROM actions WHERE idem_key = %s",
        (idem_key,)).fetchone()
    if row is None:
        return {"action_id": 0, "outcome": "no", "serial": "", "lane": lane,
                "position": 0, "sentence": "", "twice": True}
    detail = row[3] if isinstance(row[3], dict) else {}
    return {"action_id": int(row[0]),
            "outcome": str(detail.get("outcome") or "no"),
            "serial": str(detail.get("serial") or ""),
            "lane": str(detail.get("lane") or lane),
            "position": int(detail.get("position") or 0),
            "sentence": str(row[2] or ""), "twice": True}


def _position(conn, user_id: int, lane: str) -> int:
    row = conn.execute(_MY_POSITION, (user_id, lane)).fetchone()
    return int(row[1]) if row else 0


def take(settings: Settings, *, lane: str, user_id: int, by: str,
         idem_key: str) -> dict:
    """One press of Take GPT or Take Spotify, in one transaction: a phone
    from the shelf, or a place in the line, or no. Raises on a store
    failure; the web answers 503."""
    if lane not in LANES:
        raise ValueError("not a lane")
    word = lane_word(lane)
    uid = int(user_id)
    pressed = json.dumps({"lane": lane, "by": by, "by_id": uid})
    with connect(settings) as conn:
        row = conn.execute(_PRESS, ("take_phone", pressed, uid,
                                    idem_key)).fetchone()
        if row is None:
            conn.rollback()
            answer = _twin(conn, idem_key, lane)
            conn.rollback()
            return answer
        action_id = int(row[0])
        outcome, serial, position, claimed = "no", "", 0, False
        # The wait is locked before the served-a-moment-ago check, so a serve
        # that was handing this person a phone right now has committed by
        # the time the check reads (it held the wait's lock), and a serve
        # that comes later skips the locked wait: never two phones.
        wait = conn.execute(
            "SELECT id FROM station_line WHERE user_id = %s AND lane = %s"
            " AND ended_at IS NULL FOR UPDATE", (uid, lane)).fetchone()
        got = conn.execute(_SERVED_RECENTLY,
                           (uid, lane, SERVED_JUST_NOW_SECONDS)).fetchone()
        if got is not None:
            outcome, serial = "took", str(got[0])
            sentence = (f"Your {word} phone is here: {serial}."
                        f" Press Boot to switch it on.")
        else:
            got = conn.execute(_CLAIM, {"uid": uid, "lane": lane}).fetchone()
            if got is not None:
                outcome, serial, claimed = "took", str(got[0]), True
                sentence = f"Phone {serial} is yours. Press Boot to switch it on."
                if wait is not None:
                    conn.execute(
                        "UPDATE station_line SET ended_at = now(),"
                        " ended_why = 'served', serial = %s, updated_at = now()"
                        " WHERE id = %s", (serial, wait[0]))
            elif wait is not None:
                outcome, position = "line", _position(conn, uid, lane)
                sentence = f"You are already in line for the next {word} phone."
            elif int(conn.execute(_BUILDING, (lane,)).fetchone()[0]) > 0:
                conn.execute(
                    "INSERT INTO station_line (user_id, lane) VALUES (%s, %s)"
                    " ON CONFLICT (user_id, lane) WHERE ended_at IS NULL"
                    " DO NOTHING RETURNING id", (uid, lane))
                outcome, position = "line", _position(conn, uid, lane)
                if position == 1:
                    sentence = f"You are first in line for the next {word} phone."
                else:
                    sentence = (f"You are number {position} in line for the"
                                f" next {word} phone.")
            else:
                sentence = f"Nothing on the {word} shelf, and nothing is being built."
        detail: dict = {"outcome": outcome, "lane": lane}
        if serial:
            detail["serial"] = serial
        if position:
            detail["position"] = position
        conn.execute(_CLOSE_PRESS, ("refused" if outcome == "no" else "done",
                                    sentence, json.dumps(detail), action_id))
        if claimed:
            conn.execute(f"NOTIFY {NOTIFY_CHANNEL}")
        conn.commit()
    return {"action_id": action_id, "outcome": outcome, "serial": serial,
            "lane": lane, "position": position, "sentence": sentence,
            "twice": False}


def leave_line(settings: Settings, *, lane: str, user_id: int, by: str,
               idem_key: str) -> dict:
    """Leave a lane's line, as its own recorded press. Raises."""
    if lane not in LANES:
        raise ValueError("not a lane")
    word = lane_word(lane)
    uid = int(user_id)
    pressed = json.dumps({"lane": lane, "by": by, "by_id": uid})
    with connect(settings) as conn:
        row = conn.execute(_PRESS, ("leave_line", pressed, uid,
                                    idem_key)).fetchone()
        if row is None:
            conn.rollback()
            first = _twin(conn, idem_key, lane)
            conn.rollback()
            return {"action_id": first["action_id"],
                    "outcome": "left" if first["outcome"] == "left" else "no",
                    "sentence": first["sentence"], "twice": True}
        action_id = int(row[0])
        left = conn.execute(
            "UPDATE station_line SET ended_at = now(), ended_why = 'left',"
            " updated_at = now()"
            " WHERE user_id = %s AND lane = %s AND ended_at IS NULL RETURNING id",
            (uid, lane)).fetchone()
        detail: dict = {"lane": lane}
        if left is not None:
            outcome = "left"
            sentence = f"You left the line for a {word} phone."
        else:
            outcome = "no"
            got = conn.execute(_SERVED_RECENTLY,
                               (uid, lane, SERVED_JUST_NOW_SECONDS)).fetchone()
            if got is not None:
                detail["serial"] = str(got[0])
                sentence = f"Too late - phone {got[0]} was already yours."
            else:
                sentence = f"You are not in line for a {word} phone."
        detail["outcome"] = outcome
        conn.execute(_CLOSE_PRESS, ("done" if outcome == "left" else "refused",
                                    sentence, json.dumps(detail), action_id))
        conn.commit()
    return {"action_id": action_id, "outcome": outcome, "sentence": sentence,
            "twice": False}


# ------------------------------------------------------ a Station hold
def holds(settings: Settings, serial: str, user_id: int) -> bool:
    """Whether the phone is this person's Station hold right now."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT 1 AS ok FROM phones"
            " WHERE serial = %s AND done_at IS NULL AND state = 'taken'"
            "   AND taken_at IS NOT NULL AND owner_id = %s",
            (str(serial), int(user_id)))
    return bool(rows)


def station_holder(settings: Settings, serial: str) -> int | None:
    """The owner of a Station hold on that serial, or None when the phone
    is not a Station hold."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT owner_id FROM phones"
            " WHERE serial = %s AND done_at IS NULL AND taken_at IS NOT NULL"
            " ORDER BY id DESC LIMIT 1", (str(serial),))
    if not rows or rows[0]["owner_id"] is None:
        return None
    return int(rows[0]["owner_id"])


def hold_state(settings: Settings, serial: str) -> dict | None:
    """Why a Station write did nothing: `{"state", "owner_id", "station",
    "status", "busy"}`, busy being a fresh pending Boot or Change IP."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT p.state, p.owner_id, p.taken_at IS NOT NULL AS station,"
            " p.status, coalesce(" + _BUSY + ", '') AS busy"
            " FROM phones p WHERE p.serial = %s AND p.done_at IS NULL"
            " ORDER BY p.id DESC LIMIT 1", (str(serial),))
    return rows[0] if rows else None


def _give_back_sql(where: str, why: str, prefix: str) -> str:
    """The give-back, its power-off and the carried password, in one
    statement. `where` narrows the phone row aliased p."""
    return (
        "WITH b AS ("
        "  UPDATE phones p SET state = '', owner_id = NULL,"
        "         last_owner_id = p.owner_id, state_at = now(), taken_at = NULL,"
        "         watched_at = NULL, tab_closed_at = NULL, live_url = '',"
        "         updated_at = now()"
        "   WHERE " + where +
        "  RETURNING p.id, p.serial, p.purpose, p.running, p.last_owner_id),"
        " carried AS ("
        "  UPDATE wanted_builds w SET carry_password = '', updated_at = now()"
        "    FROM b WHERE lower(b.purpose) = 'other' AND w.serial = b.serial"
        "     AND w.carry_password <> ''"
        "  RETURNING w.id),"
        " off AS ("
        "  INSERT INTO actions (verb, payload, requested_by, idem_key)"
        "  SELECT 'power_off_phone',"
        "         jsonb_build_object('serial', b.serial, 'by', %(by)s::text,"
        "                            'by_id', b.last_owner_id, 'why', '" + why + "'),"
        "         b.last_owner_id,"
        "         '" + prefix + "' || b.serial || ':' || b.id || ':'"
        "           || floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint"
        "    FROM b"
        "  ON CONFLICT DO NOTHING"
        "  RETURNING id)"
        " SELECT b.id, b.purpose, b.running, (SELECT min(id) FROM off) AS off_id,"
        "        (SELECT count(*) FROM carried) AS carried, b.last_owner_id"
        "   FROM b")


_GIVE_BACK = _give_back_sql(
    "p.serial = %(serial)s AND p.done_at IS NULL AND p.state = 'taken'"
    " AND p.taken_at IS NOT NULL AND p.owner_id = %(uid)s"
    " AND p.status <> 'building'"
    " AND NOT " + power_pending("p"),
    "given back", "off:giveback:")

_GIVE_BACK_IDLE = _give_back_sql(
    "p.serial = %(serial)s AND p.done_at IS NULL AND p.state = 'taken'"
    " AND p.taken_at IS NOT NULL AND p.status <> 'building'"
    " AND NOT " + watching() +
    " AND " + idle_since() + " < now() - %(minutes)s * interval '1 minute'"
    " AND NOT " + power_pending("p"),
    "alone", "off:hour:")


#: Close the stale pending power presses of one serial (orphans a restart
#: left `running`, or presses that waited in a batch past the three
#: minutes): the web, the give-backs and the closed-tab switch-off all go
#: through it, and the keeper runs no press that was closed while it waited.
_EXPIRE_STALE = (
    "UPDATE actions SET status = 'failed', finished_at = now(),"
    " result = 'closed: no answer for three minutes - a restart took it'"
    " WHERE status IN ('queued', 'running') AND payload->>'serial' = %(serial)s"
    "   AND verb IN ('boot_phone', 'change_proxy', 'power_off_phone')"
    "   AND coalesce(executed_at, requested_at)"
    "       <= now() - %(stale)s * interval '1 second'"
    " RETURNING id")


def _expire_stale(conn, serial: str) -> int:
    """`_EXPIRE_STALE` on this connection, as its own statement before the
    write that follows it: a sibling CTE would have no order against the
    write, and the write must never meet a press that is only an orphan."""
    rows = conn.execute(_EXPIRE_STALE, {"serial": str(serial),
                                        "stale": POWER_STALE_SECONDS}).fetchall()
    if rows:
        log.info("phone %s: %d power press(es) nobody answered for three "
                 "minutes closed", serial, len(rows))
    return len(rows)


def _given(conn, sql: str, params: dict) -> dict | None:
    _expire_stale(conn, params["serial"])
    row = conn.execute(sql, params).fetchone()
    if row is None:
        conn.commit()
        return None
    conn.execute(f"NOTIFY {NOTIFY_CHANNEL}")
    conn.commit()
    return {"id": int(row[0]), "lane": _lane_of(row[1]), "running": bool(row[2]),
            "off_id": int(row[3]) if row[3] is not None else None,
            "owner_id": int(row[5]) if row[5] is not None else None}


def give_back(settings: Settings, *, serial: str, owner_id: int,
              by: str) -> dict | None:
    """Give a Station hold back to the back of its shelf, switched off.
    None means nothing was given back (`hold_state` says why)."""
    with connect(settings) as conn:
        row = _given(conn, _GIVE_BACK, {"serial": str(serial),
                                         "uid": int(owner_id), "by": str(by)})
    if row is None:
        return None
    return {k: row[k] for k in ("id", "lane", "running", "off_id")}


def give_back_idle(settings: Settings, serial: str, minutes: int,
                   grace: int) -> dict | None:
    """The hour's give-back: only while the rule still holds, so a Boot or
    a beat that just cleared the clocks wins."""
    with connect(settings) as conn:
        return _given(conn, _GIVE_BACK_IDLE,
                      {"serial": str(serial), "by": "the hour",
                       "minutes": int(minutes), "grace": int(grace)})


_QUEUE_OFF_CLOSED = (
    "INSERT INTO actions (verb, payload, requested_by, idem_key)"
    " SELECT 'power_off_phone',"
    "        jsonb_build_object('serial', p.serial, 'by', 'the station',"
    "                           'by_id', p.owner_id, 'why', 'closed'),"
    "        p.owner_id,"
    "        'off:closed:' || p.serial || ':'"
    "          || floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint"
    "   FROM phones p"
    "  WHERE p.serial = %(serial)s AND p.done_at IS NULL AND p.state = 'taken'"
    "    AND p.taken_at IS NOT NULL AND p.status <> 'building' AND p.running"
    "    AND NOT " + watching() +
    "    AND (p.tab_closed_at IS NULL"
    "         OR p.tab_closed_at < now() - %(closed)s * interval '1 second')"
    "    AND NOT " + power_pending("p") +
    "    AND NOT " + power_pending("p", ("power_off_phone",)) +
    " ON CONFLICT DO NOTHING"
    " RETURNING id")


def queue_off_closed(settings: Settings, serial: str, grace: int,
                     closed: int) -> int | None:
    """Queue the power-off of a Station hold whose tab closed, only if the
    rule still holds in the statement that queues it. The action id, or
    None (the rule no longer holds, or a power-off is already pending). A
    stale press on the phone is closed first: an orphan never holds the
    switch-off back."""
    with connect(settings) as conn:
        _expire_stale(conn, serial)
        row = conn.execute(_QUEUE_OFF_CLOSED,
                           {"serial": str(serial), "grace": int(grace),
                            "closed": int(closed)}).fetchone()
        if row is not None:
            conn.execute(f"NOTIFY {NOTIFY_CHANNEL}")
        conn.commit()
    return int(row[0]) if row is not None else None


def is_watched(settings: Settings, serial: str, grace: int) -> bool:
    """A Station hold whose Live tab is beating right now."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT 1 AS ok FROM phones p"
            " WHERE p.serial = %(serial)s AND p.done_at IS NULL"
            "   AND p.taken_at IS NOT NULL AND " + watching(),
            {"serial": str(serial), "grace": int(grace)})
    return bool(rows)


def watch(settings: Settings, serial: str, user_id: int, grace: int) -> bool:
    """The Station tab's beat, scoped to the holder. `updated_at` moves
    only on a transition, so a steady beat costs the stream nothing."""
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET watched_at = now(), tab_closed_at = NULL,"
            " updated_at = CASE WHEN tab_closed_at IS NOT NULL"
            "   OR watched_at IS NULL"
            "   OR watched_at < now() - %(grace)s * interval '1 second'"
            "   THEN now() ELSE updated_at END"
            " WHERE serial = %(serial)s AND done_at IS NULL AND state = 'taken'"
            "   AND taken_at IS NOT NULL AND owner_id = %(uid)s"
            " RETURNING id",
            {"serial": str(serial), "uid": int(user_id), "grace": int(grace)})
    return bool(rows)


def tab_closed(settings: Settings, serial: str, user_id: int) -> bool:
    """The Station tab is closing: the card turns Ready and its hour starts."""
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET tab_closed_at = now(), updated_at = now()"
            " WHERE serial = %s AND done_at IS NULL AND state = 'taken'"
            "   AND taken_at IS NOT NULL AND owner_id = %s"
            " RETURNING id", (str(serial), int(user_id)))
    return bool(rows)


def stored_link(settings: Settings, serial: str) -> str:
    """The viewer link Boot got, or ""."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT live_url FROM phones WHERE serial = %s AND done_at IS NULL"
            " ORDER BY id DESC LIMIT 1", (str(serial),))
    return str(rows[0]["live_url"] or "") if rows else ""


def booted(settings: Settings, serial: str, url: str, *,
           owner_id: int | None = None, started: bool = True) -> bool:
    """The phone is on: keep its link (https only) and restart its clocks.
    With `owner_id`, it lands only on that person's Station hold."""
    url = url if str(url or "").startswith("https://") else ""
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET live_url = %(url)s, running = true,"
            " running_since = CASE WHEN %(started)s THEN now()"
            "                      ELSE coalesce(running_since, now()) END,"
            " watched_at = now(), tab_closed_at = NULL, updated_at = now()"
            " WHERE serial = %(serial)s AND done_at IS NULL"
            "   AND (%(uid)s::bigint IS NULL"
            "        OR (state = 'taken' AND taken_at IS NOT NULL"
            "            AND owner_id = %(uid)s::bigint))"
            " RETURNING id",
            {"url": url, "started": bool(started), "serial": str(serial),
             "uid": int(owner_id) if owner_id is not None else None})
    return bool(rows)


def powered_off(settings: Settings, serial: str) -> bool:
    """The phone is off: its link goes with it."""
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET running = false, running_since = NULL,"
            " live_url = '', updated_at = now()"
            " WHERE serial = %s AND done_at IS NULL RETURNING id",
            (str(serial),))
    return bool(rows)


def land(settings: Settings, *, serial: str, wanted_id: int) -> bool:
    """A Station build becomes a Station hold of its requester."""
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones p SET state = 'taken', taken_at = now(),"
            " state_at = now(), watched_at = NULL, tab_closed_at = NULL,"
            " live_url = '', updated_at = now()"
            " FROM wanted_builds w"
            " WHERE w.id = %(wid)s AND w.station AND p.serial = %(serial)s"
            "   AND p.done_at IS NULL AND p.status <> 'building'"
            "   AND p.owner_id = w.requested_by AND p.state IN ('', 'taken')"
            " RETURNING p.id",
            {"wid": int(wanted_id), "serial": str(serial)})
    return bool(rows)


# ------------------------------------- warm phones and power presses
def reserve_warm(settings: Settings, serial: str, *,
                 owner_id: int | None = None) -> str | None:
    """Mark a warm phone `building` only if nobody holds it. Returns the
    status it had, or None when it is not free to pair."""
    with Store(settings) as store:
        rows = store._write(
            "WITH old AS ("
            "  SELECT id, status FROM phones"
            "   WHERE serial = %(serial)s AND done_at IS NULL AND taken_at IS NULL"
            "     AND state IN ('', 'unused') AND status <> 'building'"
            "     AND (owner_id IS NULL OR owner_id = %(uid)s::bigint)"
            "   ORDER BY id DESC LIMIT 1 FOR UPDATE)"
            " UPDATE phones p SET status = 'building', updated_at = now()"
            "   FROM old WHERE p.id = old.id"
            " RETURNING old.status",
            {"serial": str(serial),
             "uid": int(owner_id) if owner_id is not None else None})
    return str(rows[0]["status"]) if rows else None


def unreserve_warm(settings: Settings, serial: str, status: str) -> bool:
    """Put back the status `reserve_warm` returned, when the pairing did
    not happen after all."""
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET status = %s, updated_at = now()"
            " WHERE serial = %s AND done_at IS NULL AND status = 'building'"
            "   AND NOT EXISTS (SELECT 1 FROM jobs j"
            "                    WHERE j.status IN ('queued', 'running')"
            "                      AND j.payload->'phone'->>'serial' = phones.serial)"
            " RETURNING id", (str(status), str(serial)))
    return bool(rows)


def power_pending_of(settings: Settings, serial: str,
                     verbs: tuple = POWER_VERBS) -> dict | None:
    """The oldest queued or running press of `verbs` on the serial, fresh
    or not: `{"id", "verb", "stale"}`."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT id, verb,"
            " coalesce(executed_at, requested_at)"
            "   <= now() - %s * interval '1 second' AS stale"
            " FROM actions"
            " WHERE status IN ('queued', 'running') AND verb = ANY(%s)"
            "   AND payload->>'serial' = %s"
            " ORDER BY id LIMIT 1",
            (POWER_STALE_SECONDS, list(verbs), str(serial)))
    return rows[0] if rows else None


def expire_power(settings: Settings, serial: str) -> int:
    """Close the stale pending power presses of that serial (an orphan a
    restart left `running`). Returns how many."""
    with connect(settings) as conn:
        closed = _expire_stale(conn, serial)
        conn.commit()
    return closed


def press_of(settings: Settings, action_id: int) -> dict | None:
    """What a pending twin is."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT id, verb, status, payload->>'button' AS button, requested_by"
            " FROM actions WHERE id = %s", (int(action_id),))
    return rows[0] if rows else None


def exit_shared(settings: Settings, name: str, serial: str) -> bool:
    """Whether another live phone is on the exit `name`."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT EXISTS (SELECT 1 FROM phones WHERE done_at IS NULL"
            " AND proxy_name = %s AND serial <> %s) AS shared",
            (str(name), str(serial)))
    return bool(rows and rows[0]["shared"])


# ------------------------------------------- the sweep's Station branch
_OVER = (
    " FROM phones p LEFT JOIN users u ON u.id = p.owner_id"
    " WHERE p.done_at IS NULL AND p.status <> 'building'"
    "   AND p.taken_at IS NOT NULL AND p.state = 'taken'"
    "   AND NOT " + watching() +
    "   AND NOT " + power_pending("p") +
    "   AND (" + idle_since() + " < now() - %(minutes)s * interval '1 minute'"
    "        OR (p.running AND (p.tab_closed_at IS NULL"
    "            OR p.tab_closed_at < now() - %(closed)s * interval '1 second')))")

_WHY = ("CASE WHEN " + idle_since() + " < now() - %(minutes)s * interval '1 minute'"
        " THEN 'alone' ELSE 'closed' END")


def overdue(settings: Settings, minutes: int, grace: int, *,
            closed: int = 20) -> list[dict]:
    """Station holds that are over, each tagged `why`: 'alone' after the
    hour unwatched, 'closed' when a running phone's tab closed."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT p.serial, p.running, p.owner_id, p.purpose,"
            " coalesce(u.username, '') AS owner, " + _WHY + " AS why,"
            " extract(epoch FROM now() - " + idle_since() + ") AS idle_seconds,"
            " extract(epoch FROM now() - p.tab_closed_at) AS closed_seconds,"
            " extract(epoch FROM now() - p.watched_at) AS unwatched_seconds"
            + _OVER + " ORDER BY p.serial",
            {"minutes": int(minutes), "grace": int(grace),
             "closed": int(closed)})
    for row in rows:
        for key in ("idle_seconds", "closed_seconds", "unwatched_seconds"):
            row[key] = _seconds(row[key])
    return rows


def still_over(settings: Settings, serial: str, minutes: int, grace: int,
               closed: int) -> str:
    """Re-check one row: 'alone', 'closed' or ''."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT " + _WHY + " AS why" + _OVER + " AND p.serial = %(serial)s",
            {"minutes": int(minutes), "grace": int(grace),
             "closed": int(closed), "serial": str(serial)})
    return str(rows[0]["why"]) if rows else ""


# ------------------------------------------------- what a page reads
_PHONE_COLUMNS = (
    "p.serial, " + lane_sql() + " AS lane, p.proxy_name, p.running, p.live_url,"
    " p.taken_at, " + idle_since() + " AS idle_since, "
    + watching() + " AS watching,"
    " p.tab_closed_at IS NOT NULL AS tab_closed,"
    " (p.watched_at IS NOT NULL OR p.tab_closed_at IS NOT NULL) AS tab_seen,"
    " coalesce(p.gmail, '') AS gmail,"
    " coalesce(g.password, '') AS gmail_password,"
    " coalesce(g.totp_secret, '') AS totp,"
    " (SELECT a.verb FROM actions a"
    "   WHERE a.verb IN ('boot_phone', 'change_proxy')"
    "     AND a.payload->>'serial' = p.serial"
    "     AND a.status IN ('queued', 'running')"
    "     AND coalesce(a.executed_at, a.requested_at)"
    f"        > now() - interval '{POWER_STALE_SECONDS} seconds'"
    "   ORDER BY a.id DESC LIMIT 1) AS busy,"
    " w.id AS wish, w.called_off_at IS NOT NULL AS called_off,"
    " (SELECT l.id FROM station_line l WHERE l.serial = p.serial"
    "     AND l.user_id = p.owner_id AND l.ended_why = 'served'"
    "     AND l.ended_at > p.taken_at - interval '1 second'"
    "   ORDER BY l.id DESC LIMIT 1) AS from_line,"
    " la.id AS last_id, la.verb AS last_verb, la.status AS last_status,"
    " la.result AS last_result, la.detail AS last_detail")

_PHONE_JOINS = (
    " FROM phones p"
    " LEFT JOIN LATERAL (SELECT r.password, r.totp_secret FROM resources r"
    "                     WHERE r.kind = 'gmail' AND lower(r.address) = lower(p.gmail)"
    "                     ORDER BY r.id DESC LIMIT 1) g"
    "        ON coalesce(p.gmail, '') NOT IN ('', %(cross)s)"
    " LEFT JOIN LATERAL (SELECT x.id, x.called_off_at FROM wanted_builds x"
    "                     WHERE x.station AND x.serial = p.serial"
    "                       AND x.requested_by = p.owner_id"
    "                     ORDER BY x.id DESC LIMIT 1) w ON true"
    " LEFT JOIN LATERAL (SELECT a.id, a.verb, a.status, a.result, a.detail"
    "                      FROM actions a"
    "                     WHERE a.verb IN ('boot_phone', 'change_proxy')"
    "                       AND a.payload->>'serial' = p.serial"
    "                       AND a.requested_by = p.owner_id"
    "                       AND a.status IN ('done', 'failed', 'refused')"
    "                       AND a.finished_at > now() - interval '10 minutes'"
    "                     ORDER BY a.id DESC LIMIT 1) la ON true")

_MINE = ("SELECT " + _PHONE_COLUMNS + _PHONE_JOINS +
         " WHERE p.owner_id = %(uid)s AND p.done_at IS NULL AND p.state = 'taken'"
         "   AND p.taken_at IS NOT NULL AND p.status <> 'building'"
         " ORDER BY p.taken_at, p.id")

_LIVE_PHONE = (
    "SELECT " + _PHONE_COLUMNS + ","
    " p.state, p.owner_id, p.done_at, p.app_account,"
    " acc.address AS acct_address, coalesce(acc.password, '') AS acct_password,"
    " coalesce(acc.totp_secret, '') AS acct_totp,"
    " coalesce(acc.product, '') AS acct_product,"
    " coalesce(acc.category, '') AS acct_category,"
    " coalesce(acc.email_code_only, false) AS acct_email_code,"
    " coalesce(wc.carry_address, '') AS carry_address,"
    " coalesce(wc.carry_password, '') AS carry_password"
    + _PHONE_JOINS +
    " LEFT JOIN LATERAL (SELECT r.address, r.password, r.totp_secret, r.product,"
    "                           r.category, r.email_code_only FROM resources r"
    "                     WHERE r.kind = 'app'"
    "                       AND lower(r.address) = lower(p.app_account)"
    "                     ORDER BY r.id DESC LIMIT 1) acc"
    "        ON coalesce(p.app_account, '') NOT IN ('', %(cross)s)"
    " LEFT JOIN LATERAL (SELECT x.carry_address, x.carry_password"
    "                      FROM wanted_builds x"
    "                     WHERE x.serial = p.serial AND x.carry_address <> ''"
    "                       AND x.requested_by = p.owner_id"
    "                     ORDER BY x.id DESC LIMIT 1) wc ON true"
    " WHERE p.serial = %(serial)s ORDER BY p.id DESC LIMIT 1")


def mine(settings: Settings, user_id: int, grace: int) -> list[dict]:
    """This person's Station holds, oldest first. Raises; the web catches."""
    with Store(settings) as store:
        return store._rows(_MINE, {"uid": int(user_id), "grace": int(grace),
                                   "cross": CROSS})


def live_phone(settings: Settings, serial: str, grace: int) -> dict | None:
    """The one phone for its Live tab, whoever holds it - the closed row
    too, so the tab can tell "closed" from "gone"."""
    with Store(settings) as store:
        rows = store._rows(_LIVE_PHONE, {"serial": str(serial),
                                         "grace": int(grace), "cross": CROSS})
    return rows[0] if rows else None


def builds_of(settings: Settings, user_id: int) -> list[dict]:
    """This person's Station wishes that are still a card."""
    with Store(settings) as store:
        return store._rows(
            "SELECT w.id, w.status, lower(w.purpose) AS purpose, w.gmail,"
            " w.no_gmail, w.proxy_name, w.app_account, w.carry_address,"
            " w.detail, w.serial, w.created_at, w.ended_at, w.called_off_at,"
            " j.status AS job_status, j.claimed_at,"
            " coalesce(ph.proxy_name, '') AS phone_exit,"
            " coalesce(ph.gmail, '') AS phone_gmail"
            " FROM wanted_builds w"
            " LEFT JOIN LATERAL (SELECT status, claimed_at FROM jobs"
            "                     WHERE kind = 'build'"
            "                       AND payload->'want'->>'wanted_id' = w.id::text"
            "                     ORDER BY id DESC LIMIT 1) j ON true"
            " LEFT JOIN LATERAL (SELECT proxy_name, gmail FROM phones"
            "                     WHERE serial = w.serial AND done_at IS NULL"
            "                     ORDER BY id DESC LIMIT 1) ph ON w.serial <> ''"
            " WHERE w.requested_by = %(uid)s AND w.station"
            "   AND (w.status IN ('queued', 'running')"
            "        OR (w.status = 'failed' AND w.dismissed_at IS NULL"
            "            AND w.ended_at > now() - interval '24 hours'))"
            "   AND NOT EXISTS (SELECT 1 FROM phones x"
            "                    WHERE x.serial = w.serial AND w.serial <> ''"
            "                      AND x.done_at IS NULL"
            "                      AND x.owner_id = w.requested_by"
            "                      AND x.state = 'taken'"
            "                      AND x.taken_at IS NOT NULL"
            "                      AND x.status <> 'building')"
            " ORDER BY w.id", {"uid": int(user_id)})


def notes(settings: Settings, user_id: int) -> list[dict]:
    """What happened to this person's phones without a press on this page,
    in the last ten minutes, newest first, at most 10. Never raises."""
    try:
        with Store(settings) as store:
            events = store._rows(
                "SELECT e.id, e.at, e.serial, e.status AS kind,"
                " (SELECT " + lane_sql("x") +
                "    FROM phones x WHERE x.serial = e.serial"
                "   ORDER BY x.id DESC LIMIT 1) AS lane"
                " FROM events e"
                " WHERE e.kind = 'phone' AND e.user_id = %s"
                "   AND e.status IN ('given back', 'switched off')"
                "   AND e.at > now() - interval '10 minutes'"
                " ORDER BY e.id DESC LIMIT 10", (int(user_id),))
            returned = store._rows(
                "SELECT a.id, coalesce(a.finished_at, a.requested_at) AS at,"
                " a.payload->>'serial' AS serial,"
                " coalesce(a.detail->>'lane', 'gpt') AS lane"
                " FROM actions a"
                " WHERE a.verb = 'give_back' AND a.requested_by = %s"
                "   AND a.status = 'done'"
                "   AND a.payload->>'where' = 'station-live'"
                "   AND coalesce(a.finished_at, a.requested_at)"
                "       > now() - interval '10 minutes'"
                " ORDER BY a.id DESC LIMIT 10", (int(user_id),))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not read the notes of user %s (%s)", user_id, exc)
        return []
    out = [{"key": "e" + str(r["id"]), "at": r["at"], "serial": str(r["serial"]),
            "kind": str(r["kind"]), "lane": str(r["lane"] or "gpt")}
           for r in events]
    out += [{"key": "a" + str(r["id"]), "at": r["at"],
             "serial": str(r["serial"] or ""), "kind": "returned",
             "lane": str(r["lane"] or "gpt")} for r in returned]
    out.sort(key=lambda n: n["at"], reverse=True)
    return out[:10]


_BUILD_FORM = (
    "SELECT (SELECT count(*) FROM resources WHERE kind = 'gmail' AND error IS NULL"
    "          AND coalesce(refund_state, '') = '' AND lower(status) = '') AS gmails,"
    "       (SELECT count(*) FROM resources WHERE kind = 'proxy' AND error IS NULL"
    "          AND lower(status) IN ('', 'free', 'unused')"
    "          AND lower(coalesce(purpose, '')) IN ('gpt', '')) AS gpt_ips,"
    "       (SELECT count(*) FROM resources WHERE kind = 'proxy' AND error IS NULL"
    "          AND lower(status) IN ('', 'free', 'unused')"
    "          AND lower(coalesce(purpose, '')) IN ('spotify', '')) AS spotify_ips,"
    "       (SELECT count(*) FROM resources WHERE kind = 'proxy' AND error IS NULL"
    "          AND lower(status) IN ('', 'free', 'unused')) AS other_ips,"
    "       (SELECT value FROM service_state WHERE key = 'pass') AS pulse")


def build_form(settings: Settings) -> dict:
    """What the build dialog says about stock. Never raises."""
    out = {"gmails_left": 0, "free_ips": {"gpt": 0, "spotify": 0, "other": 0},
           "stopped": False}
    try:
        with Store(settings) as store:
            rows = store._rows(_BUILD_FORM)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not read what the build dialog offers (%s)", exc)
        return out
    if not rows:
        return out
    row = rows[0]
    pulse = row.get("pulse")
    out["gmails_left"] = int(row["gmails"] or 0)
    out["free_ips"] = {"gpt": int(row["gpt_ips"] or 0),
                       "spotify": int(row["spotify_ips"] or 0),
                       "other": int(row["other_ips"] or 0)}
    out["stopped"] = bool((pulse if isinstance(pulse, dict) else {}).get("stopped"))
    return out


# ------------------------------------------------- records and hygiene
def record(settings: Settings, *, verb: str, payload: dict, requested_by: int,
           status: str, result: str, detail: dict | None = None,
           idem_key: str | None = None) -> int:
    """A press that ran in the web without the queue. The payload must
    never carry a secret; the caller strips it. Returns the row's id (the
    first row's, for a key already used)."""
    with connect(settings) as conn:
        row = conn.execute(
            "INSERT INTO actions (verb, payload, requested_by, status, result,"
            " detail, executed_at, finished_at, idem_key)"
            " VALUES (%s, %s, %s, %s, %s, %s, now(), now(), %s)"
            " ON CONFLICT (idem_key) DO NOTHING RETURNING id",
            (verb, json.dumps(payload), int(requested_by), status, result,
             json.dumps(detail) if detail else None, idem_key)).fetchone()
        if row is None:
            row = conn.execute("SELECT id FROM actions WHERE idem_key = %s",
                               (idem_key,)).fetchone()
        conn.commit()
    return int(row[0]) if row is not None else 0


#: Takes the typed secrets out of a Station build's row, and shortens a
#: typed IP to its host:port.
_SCRUBBED = (
    "payload = (payload - ARRAY['gmail_password', 'gmail_secret', 'app_password',"
    "                           'app_secret', 'carry_password'])"
    "          || CASE WHEN payload->>'proxy_typed' = 'true'"
    "                  THEN jsonb_build_object('proxy_name',"
    "                         split_part(payload->>'proxy_name', ':', 1) || ':'"
    "                         || split_part(payload->>'proxy_name', ':', 2))"
    "                  ELSE '{}'::jsonb END")


def scrub(settings: Settings, action_id: int) -> None:
    """Scrub one Station build the moment it has settled. Never raises."""
    try:
        with Store(settings) as store:
            store._write(
                "UPDATE actions SET " + _SCRUBBED +
                " WHERE id = %s AND verb = 'build_by_hand'"
                "   AND payload->>'station' = 'true'"
                "   AND status IN ('done', 'failed', 'refused')"
                " RETURNING id", (int(action_id),))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not scrub the build press %s (%s)", action_id, exc)


def scrub_mine(settings: Settings, user_id: int) -> int:
    """Scrub every settled Station build of this person that still holds a
    secret. Returns how many. Never raises."""
    try:
        with Store(settings) as store:
            rows = store._write(
                "UPDATE actions SET " + _SCRUBBED +
                " WHERE verb = 'build_by_hand' AND requested_by = %s"
                "   AND payload->>'station' = 'true'"
                "   AND status IN ('done', 'failed', 'refused', 'cancelled')"
                "   AND (payload ?| ARRAY['gmail_password', 'gmail_secret',"
                "                         'app_password', 'app_secret',"
                "                         'carry_password']"
                "        OR (payload->>'proxy_typed' = 'true'"
                "            AND split_part(payload->>'proxy_name', ':', 3) <> ''))"
                " RETURNING id", (int(user_id),))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not scrub the build presses of user %s (%s)",
                    user_id, exc)
        return 0
    return len(rows)
