"""The stock planner's reads and writes (rev 43, 2026-09-30).

What the planner (`geelark_farm.stockplan`) needs to know about the farm,
each one statement: how often the operators take a phone of each lane
(the demand), how long a build of each lane takes and how often it
works (the lead time), who is at work right now, who is waiting in a
line, and what it set last - plus the admin's knobs, kept in
`service_state` under STOCK_PLAN_KEY.

A demand is one request for a phone from a shelf:
  - every press of Take on the Station, whatever it answered (a phone, a
    place in the line, or "nothing on the shelf" - an unmet one counts
    as much as a met one);
  - the first Boot of a stock phone nobody took through the Station: how
    the dashboard handed phones out before the Station, and how an admin
    still can. A phone built by hand for somebody (`built_by`) or for
    another app is nobody's shelf, and is left out.

Reads go through `Store._rows` and never start with WITH; writes commit.
Nothing here raises to its caller except `set_knobs` (a bad form) and
`record` (the planner logs it).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import Settings
from .db import Store, connect

log = logging.getLogger(__name__)

#: The lanes with a shelf the planner fills.
LANES = ("gpt", "spotify")
#: The `service_state` key the admin's knobs live under.
STOCK_PLAN_KEY = "stock_plan"
#: Watch: the planner computes and records, WARM_STOCK's split is built
#: toward. Auto: the planner's targets are built toward.
MODES = ("watch", "auto")
RISKS = (1, 2, 5, 10, 20)
DEFAULT_KNOBS = {
    "mode": "watch",
    "risk_pct": 5,
    "min": {"gpt": 1, "spotify": 1},
    "max": {"gpt": 8, "spotify": 8},
    "stale_hours": 6,
}
#: How far back the demand is read: two weeks of hours of the day.
DEMAND_DAYS = 14
#: The plan rows kept for the page and for judging a watching planner.
KEEP_DAYS = 14

_LANE_OF_TAKE = ("CASE WHEN lower(coalesce(a.payload->>'lane', '')) = 'spotify'"
                 " THEN 'spotify' ELSE 'gpt' END")
_LANE_OF_PHONE = ("CASE WHEN lower(coalesce(p.purpose, '')) = 'spotify'"
                  " THEN 'spotify' ELSE 'gpt' END")

#: One row per request for a phone, oldest first (see the module note).
_DEMAND = (
    "SELECT lane, at FROM ("
    " SELECT " + _LANE_OF_TAKE + " AS lane, a.requested_at AS at"
    "   FROM actions a"
    "  WHERE a.verb = 'take_phone'"
    "    AND a.requested_at > now() - %(since)s::interval"
    " UNION ALL"
    " SELECT " + _LANE_OF_PHONE + " AS lane, b.at"
    "   FROM (SELECT a.payload->>'serial' AS serial, min(a.requested_at) AS at"
    "           FROM actions a"
    "          WHERE a.verb = 'boot_phone'"
    "            AND a.requested_at > now() - %(since)s::interval"
    "            AND coalesce(a.payload->>'serial', '') <> ''"
    "          GROUP BY 1) b"
    "   LEFT JOIN LATERAL (SELECT q.purpose, q.built_by FROM phones q"
    "                       WHERE q.serial = b.serial"
    "                       ORDER BY q.id DESC LIMIT 1) p ON true"
    "  WHERE lower(coalesce(p.purpose, '')) <> 'other' AND p.built_by IS NULL"
    "    AND NOT EXISTS (SELECT 1 FROM actions t"
    "                     WHERE t.verb IN ('take_phone', 'serve_line')"
    "                       AND coalesce(t.detail->>'serial',"
    "                                    t.payload->>'serial') = b.serial)"
    ") e ORDER BY at")

#: The last 60 stock builds of each lane that really ran: how long a good
#: one took from claim to landing, how long a build waited to be claimed,
#: and how many of them worked. A build that ended at once (no Gmail, no
#: exit) says nothing about how long a phone takes, and is left out.
_LEAD = (
    "SELECT lane, count(*) AS n, count(*) FILTER (WHERE ok) AS ok,"
    " percentile_cont(0.5) WITHIN GROUP (ORDER BY run_s) FILTER (WHERE ok)"
    "   AS build_s,"
    " percentile_cont(0.5) WITHIN GROUP (ORDER BY wait_s) AS wait_s"
    " FROM (SELECT coalesce(nullif(lower(payload->>'purpose'), ''), 'gpt') AS lane,"
    "              (status = 'done' AND coalesce(result->>'worked', '') = 'true')"
    "                AS ok,"
    "              extract(epoch FROM done_at - claimed_at) AS run_s,"
    "              extract(epoch FROM claimed_at - created_at) AS wait_s,"
    "              row_number() OVER (PARTITION BY coalesce(nullif(lower("
    "                payload->>'purpose'), ''), 'gpt') ORDER BY id DESC) AS k"
    "         FROM jobs"
    "        WHERE kind = 'build' AND payload->'want' IS NULL"
    "          AND status IN ('done', 'failed', 'lost')"
    "          AND claimed_at IS NOT NULL AND done_at IS NOT NULL"
    "          AND created_at > now() - interval '3 days'"
    "          AND ((status = 'done' AND coalesce(result->>'worked', '') = 'true')"
    "               OR done_at - claimed_at >= interval '60 seconds')) t"
    " WHERE k <= 60 GROUP BY lane")

#: Who is at work: a press in the last half hour, or a Live tab beating.
_PEOPLE = (
    "SELECT count(DISTINCT uid) AS n FROM ("
    " SELECT requested_by AS uid FROM actions"
    "  WHERE requested_by IS NOT NULL"
    "    AND requested_at > now() - interval '30 minutes'"
    " UNION"
    " SELECT owner_id FROM phones"
    "  WHERE done_at IS NULL AND owner_id IS NOT NULL"
    "    AND watched_at > now() - interval '3 minutes') t")

#: The open line of each lane, whoever has looked in the last 90 seconds.
_WAITING = (
    "SELECT lane, count(*) AS n FROM station_line"
    " WHERE ended_at IS NULL AND seen_at > now() - interval '90 seconds'"
    " GROUP BY lane")

#: Phones held right now that were taken in the last hour and have no
#: verdict yet - a share of them comes back to the shelf.
_HELD = (
    "SELECT " + _LANE_OF_PHONE + " AS lane, count(*) AS n FROM phones p"
    " WHERE p.done_at IS NULL AND p.owner_id IS NOT NULL AND p.state = 'taken'"
    "   AND lower(coalesce(p.purpose, '')) <> 'other'"
    "   AND coalesce(p.taken_at, p.state_at) > now() - interval '60 minutes'"
    " GROUP BY 1")

#: Over a week: phones handed out through the Station, and those that came
#: back to their shelf (Give back, or the hour left alone).
_RETURNS = (
    "SELECT lane, sum(out) AS handed, sum(back) AS returned FROM ("
    " SELECT coalesce(a.detail->>'lane', a.payload->>'lane') AS lane,"
    "        1 AS out, 0 AS back"
    "   FROM actions a WHERE a.verb = 'take_phone'"
    "    AND a.detail->>'outcome' = 'took'"
    "    AND a.requested_at > now() - interval '7 days'"
    " UNION ALL"
    " SELECT a.payload->>'lane', 1, 0 FROM actions a"
    "  WHERE a.verb = 'serve_line' AND a.requested_at > now() - interval '7 days'"
    " UNION ALL"
    " SELECT " + _LANE_OF_PHONE + ", 0, 1"
    "   FROM actions a"
    "   LEFT JOIN LATERAL (SELECT q.purpose FROM phones q"
    "                       WHERE q.serial = a.payload->>'serial'"
    "                       ORDER BY q.id DESC LIMIT 1) p ON true"
    "  WHERE a.verb = 'give_back' AND a.status = 'done'"
    "    AND a.requested_at > now() - interval '7 days'"
    ") t WHERE lane IN ('gpt', 'spotify') GROUP BY lane")

#: How long the people who asked for a phone waited for it: a Take that
#: handed one over at once waited nothing; a wait in the line lasted from
#: joining to being served (or leaving, or still open).
_WAITS = (
    "SELECT coalesce(a.detail->>'lane', a.payload->>'lane') AS lane,"
    " 0::double precision AS wait_s, 'took' AS how"
    " FROM actions a WHERE a.verb = 'take_phone'"
    "  AND a.requested_at > now() - %(since)s::interval"
    "  AND a.detail->>'outcome' = 'took'"
    " UNION ALL"
    " SELECT lane, extract(epoch FROM coalesce(ended_at, now()) - joined_at),"
    " coalesce(nullif(ended_why, ''), 'waiting')"
    " FROM station_line WHERE joined_at > now() - %(since)s::interval")

#: What the refused Takes were (the shelf empty and nothing on its way).
_REFUSED = (
    "SELECT coalesce(a.detail->>'lane', a.payload->>'lane') AS lane,"
    " count(*) AS n FROM actions a WHERE a.verb = 'take_phone'"
    "  AND a.requested_at > now() - %(since)s::interval"
    "  AND a.detail->>'outcome' = 'no' GROUP BY 1")

_RUNWAY = (
    "SELECT (SELECT count(*) FROM resources"
    "         WHERE kind = 'gmail' AND status = '' AND owner_id IS NULL"
    "           AND (retry_after IS NULL OR retry_after <= now())) AS free,"
    "       (SELECT count(*) FROM jobs"
    "         WHERE kind = 'build' AND created_at > now() - interval '6 hours'"
    "           AND claimed_at IS NOT NULL"
    "           AND (status = 'running'"
    "                OR (status = 'done' AND coalesce(result->>'worked', '') = 'true')"
    "                OR done_at - claimed_at >= interval '60 seconds'))"
    "         AS spent_6h")

_RECORD = (
    "INSERT INTO stock_plans (lane, mode, demand_h, recent_h, profile_h,"
    " lead_min, success, planned, fixed, used, warm, coming, waiting, held,"
    " people, why) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
    " %s, %s, %s, %s)")

_PRUNE = "DELETE FROM stock_plans WHERE at < now() - %s::interval"

#: The day, ten minutes a point, for the page's chart.
_HISTORY = (
    "SELECT to_timestamp(floor(extract(epoch FROM at) / 600) * 600) AS slot,"
    " lane, max(planned) AS planned, max(used) AS used, max(fixed) AS fixed,"
    " round(avg(warm)::numeric, 1)::double precision AS warm,"
    " max(waiting) AS waiting, max(mode) AS mode"
    " FROM stock_plans WHERE at > now() - %(since)s::interval"
    " GROUP BY 1, 2 ORDER BY 1, 2")

_LATEST = (
    "SELECT DISTINCT ON (lane) lane, at, mode, demand_h, recent_h, profile_h,"
    " lead_min, success, planned, fixed, used, warm, coming, waiting, held,"
    " people, why"
    " FROM stock_plans WHERE at > now() - interval '1 hour'"
    " ORDER BY lane, at DESC")


def _interval(hours: float) -> str:
    return f"{float(hours)} hours"


def demand(settings: Settings, *, days: int = DEMAND_DAYS) -> list[tuple]:
    """Every request for a phone of the last `days`, as (lane, at), oldest
    first. Raises: the planner decides what a failure means."""
    with Store(settings) as store:
        rows = store._rows(_DEMAND, {"since": _interval(days * 24)})
    return [(str(r["lane"]), r["at"]) for r in rows
            if str(r.get("lane") or "") in LANES and r.get("at") is not None]


def lead(settings: Settings) -> dict:
    """{lane: {n, ok, build_s, wait_s}} of the last stock builds that ran.
    A lane with none is missing. Raises."""
    with Store(settings) as store:
        rows = store._rows(_LEAD)
    out = {}
    for r in rows:
        lane = str(r.get("lane") or "")
        if lane not in LANES:
            continue
        out[lane] = {"n": int(r.get("n") or 0), "ok": int(r.get("ok") or 0),
                     "build_s": (float(r["build_s"]) if r.get("build_s")
                                 is not None else None),
                     "wait_s": float(r.get("wait_s") or 0.0)}
    return out


def people(settings: Settings) -> int:
    """How many people are at work right now. Raises."""
    with Store(settings) as store:
        rows = store._rows(_PEOPLE)
    return int(rows[0]["n"] or 0) if rows else 0


def _by_lane(settings: Settings, sql: str, params=()) -> dict:
    with Store(settings) as store:
        rows = store._rows(sql, params)
    out = {lane: 0 for lane in LANES}
    for r in rows:
        lane = str(r.get("lane") or "")
        if lane in out:
            out[lane] = int(r.get("n") or 0)
    return out


def waiting(settings: Settings) -> dict:
    """{lane: people waiting in its line and looking}. Raises."""
    return _by_lane(settings, _WAITING)


def held(settings: Settings) -> dict:
    """{lane: phones taken in the last hour and still held}. Raises."""
    return _by_lane(settings, _HELD)


def returns(settings: Settings) -> dict:
    """{lane: {handed, returned}} over a week. Raises."""
    with Store(settings) as store:
        rows = store._rows(_RETURNS)
    out = {lane: {"handed": 0, "returned": 0} for lane in LANES}
    for r in rows:
        lane = str(r.get("lane") or "")
        if lane in out:
            out[lane] = {"handed": int(r.get("handed") or 0),
                         "returned": int(r.get("returned") or 0)}
    return out


def record(settings: Settings, rows: list[dict], *, prune: bool = False) -> None:
    """One plan row per lane; now and then the rows past KEEP_DAYS go.
    Raises: the planner logs it and carries on."""
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.executemany(_RECORD, [(
                r["lane"], r["mode"], float(r["demand_h"]), float(r["recent_h"]),
                float(r["profile_h"]), float(r["lead_min"]), float(r["success"]),
                int(r["planned"]), int(r["fixed"]), int(r["used"]),
                int(r["warm"]), int(r["coming"]), int(r["waiting"]),
                int(r["held"]), int(r["people"]), str(r["why"])[:500])
                for r in rows])
            if prune:
                cur.execute(_PRUNE, (f"{KEEP_DAYS} days",))
        conn.commit()


# ------------------------------------------------------------ the knobs
def _whole(value, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def clean_knobs(raw) -> dict:
    """What was stored (or typed), made whole: every key present, every
    number inside its bounds, a max never under its min. Never raises."""
    raw = raw if isinstance(raw, dict) else {}
    out = json.loads(json.dumps(DEFAULT_KNOBS))
    mode = str(raw.get("mode") or "")
    if mode in MODES:
        out["mode"] = mode
    risk = _whole(raw.get("risk_pct"), 1, 20, out["risk_pct"])
    out["risk_pct"] = min(RISKS, key=lambda r: abs(r - risk))
    for lane in LANES:
        low = _whole((raw.get("min") or {}).get(lane) if isinstance(
            raw.get("min"), dict) else None, 0, 20, out["min"][lane])
        high = _whole((raw.get("max") or {}).get(lane) if isinstance(
            raw.get("max"), dict) else None, 1, 30, out["max"][lane])
        out["min"][lane], out["max"][lane] = low, max(low, high)
    out["stale_hours"] = _whole(raw.get("stale_hours"), 1, 72, out["stale_hours"])
    for key in ("updated_at", "updated_by"):
        if raw.get(key):
            out[key] = str(raw[key])
    return out


def knobs(settings: Settings) -> dict:
    """The admin's knobs, or the defaults when none were saved or the store
    will not answer. Never raises."""
    from .state import get

    try:
        return clean_knobs(get(settings, STOCK_PLAN_KEY, None))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not read the stock planner's settings (%s); the "
                    "defaults stand", exc)
        return clean_knobs(None)


def set_knobs(settings: Settings, typed: dict, *, by: str, by_id: int) -> dict:
    """Save the admin's knobs and leave an actions row saying who changed
    what. Returns what was saved. Raises on a store failure."""
    from .state import put

    saved = clean_knobs(typed)
    saved["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    saved["updated_by"] = str(by)
    doing = "steers the stock" if saved["mode"] == "auto" else "watches"
    said = (f"Stock planner: {doing},"
            f" risk {saved['risk_pct']}%, GPT {saved['min']['gpt']}-"
            f"{saved['max']['gpt']}, Spotify {saved['min']['spotify']}-"
            f"{saved['max']['spotify']}, stale after {saved['stale_hours']} h")
    with connect(settings) as conn:
        put(conn, STOCK_PLAN_KEY, saved)
        conn.execute(
            "INSERT INTO actions (verb, payload, requested_by, status, result,"
            " executed_at, finished_at) VALUES ('set_stock_plan', %s, %s,"
            " 'done', %s, now(), now())",
            (json.dumps({"by": str(by), "by_id": int(by_id),
                         **{k: saved[k] for k in ("mode", "risk_pct", "min",
                                                  "max", "stale_hours")}}),
             int(by_id), said))
        conn.commit()
    return saved


# ---------------------------------------------------------- the page's reads
def latest(settings: Settings) -> dict:
    """{lane: the last plan row of the past hour}. Raises."""
    with Store(settings) as store:
        rows = store._rows(_LATEST)
    return {str(r["lane"]): r for r in rows}


def history(settings: Settings, *, hours: int = 24) -> list[dict]:
    """The plan, ten minutes a point, for the last `hours`. Raises."""
    with Store(settings) as store:
        return store._rows(_HISTORY, {"since": _interval(hours)})


def waits(settings: Settings, *, hours: int = 24) -> dict:
    """{lane: {asked, at_once, waited, refused, p50_s, p95_s, max_s}} for the
    requests of the last `hours`. Raises."""
    params = {"since": _interval(hours)}
    with Store(settings) as store:
        rows = store._rows(_WAITS, params)
        refused = store._rows(_REFUSED, params)
    out = {}
    for lane in LANES:
        mine = [r for r in rows if str(r.get("lane") or "") == lane]
        times = sorted(float(r.get("wait_s") or 0.0) for r in mine)
        no = next((int(r["n"]) for r in refused
                   if str(r.get("lane") or "") == lane), 0)
        out[lane] = {
            "asked": len(mine),
            "at_once": sum(1 for r in mine if r.get("how") == "took"),
            "waited": sum(1 for r in mine if r.get("how") != "took"),
            "refused": no,
            "p50_s": _quantile(times, 0.5), "p95_s": _quantile(times, 0.95),
            "max_s": times[-1] if times else 0.0}
    return out


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
    return float(values[index])


def idle(settings: Settings) -> list[dict]:
    """The phones on the shelves, the longest idle first. Raises."""
    from .station import on_shelf, shelf_lane

    with Store(settings) as store:
        return store._rows(
            "SELECT q.serial, " + shelf_lane("q") + " AS lane,"
            " extract(epoch FROM now() - greatest(q.created_at,"
            "   coalesce(q.state_at, q.created_at))) AS idle_s"
            " FROM phones q WHERE " + on_shelf("q") +
            " ORDER BY idle_s DESC")


def runway(settings: Settings) -> dict:
    """{free, per_hour, hours}: the Gmails a build could take now, how many
    the builds of the last six hours spent an hour, and how long the free
    ones last at that rate (None when nothing is being spent). Raises."""
    with Store(settings) as store:
        rows = store._rows(_RUNWAY)
    free = int(rows[0]["free"] or 0) if rows else 0
    per_hour = (int(rows[0]["spent_6h"] or 0) / 6.0) if rows else 0.0
    return {"free": free, "per_hour": round(per_hour, 1),
            "hours": (round(free / per_hour, 1) if per_hour > 0 else None)}
