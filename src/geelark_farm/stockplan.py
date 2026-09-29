"""The stock planner: how many warm phones each lane keeps, from how the
operators use them (2026-09-30).

A fixed WARM_STOCK split was wrong both ways on its first night: four GPT
phones sat on the shelf while every Spotify phone was taken within
minutes. The planner sets each lane's target from what it measures, so an
operator waits as little as possible and a phone rarely waits long.

It is a base-stock rule. Every take starts a replacement build, and a
build takes `L` minutes to land (its time, over the share of builds that
work, plus the queue and a pass to notice). With takes arriving at `λ` an
hour, the builds under way at any moment are Poisson with mean `λ·L/60`,
and an operator finds the shelf empty exactly when `S` or more are under
way. So the target is the smallest `S` with P(N >= S) <= risk - the
admin's one knob for "how often may somebody wait".

`λ` is the larger of two readings while anybody is at work: the recent
takes (a half-life of 30 minutes) and the hour-of-day profile of the last
two weeks for this hour and the next (so a shift that always starts at
nine is stocked before nine). With nobody at work, the profile alone - a
quiet night keeps the floor, and the morning is warmed ahead. The profile
is the farm's total, split by each lane's share of the last two days: a
lane opened yesterday has no fortnight of its own.

On top: every person waiting in a lane's line is a phone owed now, and a
share of the phones taken in the last hour comes back unused (Give back,
the hour) - counted as on its way, which is what kept GPT at seven
against four the first night. The target stays between the admin's min
and max for the lane.

`for_pass` is what the keeper calls each pass. In the `watch` mode it
returns WARM_STOCK's split and only records what it would have set; in
`auto` it returns its own targets. Any failure answers WARM_STOCK's split:
the planner can never stop a pass from building.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

LANES = ("gpt", "spotify")
WORDS = {"gpt": "GPT", "spotify": "Spotify"}
#: Recent takes count half after this long.
HALF_LIFE_MIN = 30.0
RECENT_HOURS = 3.0
#: The window a lane's share of the demand is read over.
SHARE_HOURS = 48.0
#: A pass to see the shortfall and a builder to claim the job.
OVERHEAD_MIN = 1.0
#: A lane whose builds mostly fail is not given an unbounded lead time.
SUCCESS_FLOOR = 0.3
SUCCESS_PRIOR = 0.7
#: How many builds a lane needs before its own rate is believed.
TRUSTED_BUILDS = 5
BUILD_FALLBACK_S = 360.0
#: The share of held phones that comes back, until there is a week of it.
RETURN_PRIOR = 0.1
RETURN_CAP = 0.5
TRUSTED_HANDED = 10
#: How often the heavy reads are refreshed, and a plan row written.
PLAN_EVERY_S = 60.0
RECORD_EVERY_S = 60.0


@dataclass
class LanePlan:
    lane: str
    recent_h: float
    profile_h: float
    demand_h: float
    lead_min: float
    success: float
    mean: float
    base: int
    waiting: int
    held: int
    credit: int
    planned: int
    fixed: int
    people: int
    why: str


def poisson_target(mean: float, risk: float) -> int:
    """The smallest S with P(N >= S) <= risk for N ~ Poisson(mean)."""
    mean = max(0.0, float(mean))
    risk = min(0.5, max(1e-6, float(risk)))
    if mean <= 0.0:
        return 0
    p = math.exp(-mean)
    cdf = 0.0
    k = 0
    while k < 500:
        cdf += p
        if 1.0 - cdf <= risk:
            return k + 1
        k += 1
        p *= mean / k
    return k


def recent_rate(times: list, now: datetime, *,
                half_life_min: float = HALF_LIFE_MIN,
                hours: float = RECENT_HOURS) -> float:
    """Takes an hour now, each take weighted down by its age."""
    tau = half_life_min / math.log(2)
    since = now - timedelta(hours=hours)
    total = 0.0
    for at in times:
        if at is None or at < since or at > now:
            continue
        total += math.exp(-(now - at).total_seconds() / 60.0 / tau)
    return total / tau * 60.0


def _zone(tz: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(tz or "Asia/Tehran")
    except Exception as exc:                                      # noqa: BLE001
        log.warning("no time zone %r (%s); UTC", tz, exc)
        return timezone.utc


def profile(events: list, now: datetime, tz: str, *, days: int = 14) -> dict:
    """{"total": [24 takes an hour], lane: [24], "days": n, "share": {lane}}.

    `total` is every take of the window by hour of the day, over the days
    the window holds; each lane's row is that total times the lane's share
    of the last SHARE_HOURS (a lane of a day's age borrows the fortnight's
    shape). `raw` is each lane's own count by hour, for the page."""
    zone = _zone(tz)
    start = now - timedelta(days=days)
    kept = [(lane, at) for lane, at in events if at is not None and at >= start]
    first = min((at for _, at in kept), default=None)
    span = ((now - first).total_seconds() / 86400.0) if first else 0.0
    span = max(1.0, min(float(days), span))
    total = [0.0] * 24
    raw = {lane: [0.0] * 24 for lane in LANES}
    for lane, at in kept:
        hour = at.astimezone(zone).hour
        total[hour] += 1.0
        if lane in raw:
            raw[lane][hour] += 1.0
    total = [n / span for n in total]
    raw = {lane: [n / span for n in raw[lane]] for lane in LANES}
    recent = [lane for lane, at in kept
              if at >= now - timedelta(hours=SHARE_HOURS)]
    share = {lane: (recent.count(lane) + 1.0) / (len(recent) + len(LANES))
             for lane in LANES}
    out = {"total": total, "days": round(span, 1), "share": share, "raw": raw}
    for lane in LANES:
        out[lane] = [n * share[lane] for n in total]
    return out


def upcoming(rows: list, hour: int) -> float:
    """The busier of this hour and the next, so a shift is warmed ahead."""
    return max(float(rows[hour % 24]), float(rows[(hour + 1) % 24]))


def lead_of(lane: str, builds: dict) -> tuple[float, float]:
    """(minutes a phone takes to land, the share of builds that work)."""
    mine = builds.get(lane) or {}
    other = next((builds[x] for x in LANES if x != lane and x in builds), {})

    def trusted(row: dict) -> bool:
        return int(row.get("n") or 0) >= TRUSTED_BUILDS

    source = mine if trusted(mine) else other if trusted(other) else {}
    success = (int(source.get("ok") or 0) / int(source["n"])) if source else \
        SUCCESS_PRIOR
    success = max(SUCCESS_FLOOR, min(1.0, success))
    build_s = mine.get("build_s") or other.get("build_s") or BUILD_FALLBACK_S
    wait_s = float(mine.get("wait_s") or 0.0)
    minutes = float(build_s) / 60.0 / success + wait_s / 60.0 + OVERHEAD_MIN
    return round(minutes, 1), round(success, 2)


def return_rate(lane: str, returns: dict) -> float:
    row = returns.get(lane) or {}
    handed = int(row.get("handed") or 0)
    if handed < TRUSTED_HANDED:
        return RETURN_PRIOR
    return min(RETURN_CAP, int(row.get("returned") or 0) / handed)


def plan(knobs: dict, *, now: datetime, tz: str, events: list, builds: dict,
         people: int, waiting: dict, held: dict, returns: dict,
         fixed: dict) -> dict:
    """{lane: LanePlan} from what was read. Pure: the tests drive it."""
    shape = profile(events, now, tz)
    hour = now.astimezone(_zone(tz)).hour
    risk = float(knobs.get("risk_pct") or 5) / 100.0
    out = {}
    for lane in LANES:
        times = [at for x, at in events if x == lane]
        recent = recent_rate(times, now)
        ahead = upcoming(shape[lane], hour)
        demand = max(recent, ahead) if people > 0 else ahead
        minutes, success = lead_of(lane, builds)
        mean = demand / 60.0 * minutes
        base = poisson_target(mean, risk)
        owed = int(waiting.get(lane) or 0)
        taken = int(held.get(lane) or 0)
        credit = int(round(return_rate(lane, returns) * taken))
        low = int((knobs.get("min") or {}).get(lane, 1))
        high = int((knobs.get("max") or {}).get(lane, 8))
        planned = max(low, min(high, base + owed - credit))
        why = _why(lane, demand=demand, recent=recent, ahead=ahead,
                   people=people, minutes=minutes, success=success,
                   base=base, owed=owed, credit=credit, planned=planned,
                   low=low, high=high, risk=risk)
        out[lane] = LanePlan(
            lane=lane, recent_h=round(recent, 2), profile_h=round(ahead, 2),
            demand_h=round(demand, 2), lead_min=minutes, success=success,
            mean=round(mean, 2), base=base, waiting=owed, held=taken,
            credit=credit, planned=planned, fixed=int(fixed.get(lane, 0)),
            people=int(people), why=why)
    return out


def _why(lane: str, *, demand, recent, ahead, people, minutes, success,
         base, owed, credit, planned, low, high, risk) -> str:
    """The plan in one sentence, for the page and the log."""
    who = (f"{people} at work" if people else "nobody at work")
    if people and recent >= ahead:
        pace = f"{demand:.1f} takes an hour now"
    else:
        pace = f"{demand:.1f} takes an hour, usual for this hour"
    said = (f"{WORDS[lane]}: {pace} ({who}); a phone lands about "
            f"{minutes:.0f} min after it is ordered ({success:.0%} of builds "
            f"work), so {base} on the shelf keep the chance of a wait under "
            f"{risk:.0%}")
    extra = []
    if owed:
        extra.append(f"+{owed} for the line")
    if credit:
        extra.append(f"-{credit} likely to come back")
    if planned == low and base + owed - credit < low:
        extra.append(f"raised to the minimum {low}")
    if planned == high and base + owed - credit > high:
        extra.append(f"held to the maximum {high}")
    return said + ("; " + ", ".join(extra) if extra else "") + f" -> {planned}."


# ------------------------------------------------------------- the keeper
_cache: dict = {"at": 0.0, "read": None}
_last_record = {"at": 0.0, "pruned": 0.0}


def _reset() -> None:
    """For the tests: forget what was read and when a row was written."""
    _cache.update(at=0.0, read=None)
    _last_record.update(at=0.0, pruned=0.0)


def _read(settings) -> dict:
    """The heavy reads, at most once a PLAN_EVERY_S. Raises."""
    from .store import stockplan as store

    clock = time.monotonic()
    if _cache["read"] is not None and clock - _cache["at"] < PLAN_EVERY_S:
        return _cache["read"]
    read = {"events": store.demand(settings), "builds": store.lead(settings),
            "people": store.people(settings), "held": store.held(settings),
            "returns": store.returns(settings)}
    _cache.update(at=clock, read=read)
    return read


def for_pass(settings, *, lanes: dict, coming_by: dict,
             fixed: dict) -> tuple[dict, dict]:
    """(targets, urgent) for this pass. `targets` is WARM_STOCK's split in
    the watch mode or on any failure, the planner's in auto; `urgent` is
    who waits in each lane's line (auto only). Never raises."""
    from .store import stockplan as store

    fixed = {lane: int(fixed.get(lane, 0)) for lane in LANES}
    knobs = store.knobs(settings)
    try:
        read = _read(settings)
        waiting = store.waiting(settings)
        plans = plan(knobs, now=datetime.now(timezone.utc),
                     tz=getattr(settings, "web_tz", "Asia/Tehran"),
                     waiting=waiting, fixed=fixed, **read)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("the stock planner could not plan (%s); building to "
                    "WARM_STOCK", exc)
        return fixed, {}
    auto = knobs.get("mode") == "auto"
    targets = ({lane: plans[lane].planned for lane in LANES} if auto
               else dict(fixed))
    urgent = ({lane: plans[lane].waiting for lane in LANES} if auto else {})
    _write(settings, plans, targets, lanes=lanes, coming_by=coming_by,
           mode=knobs.get("mode") or "watch")
    return targets, urgent


def _write(settings, plans: dict, used: dict, *, lanes: dict, coming_by: dict,
           mode: str) -> None:
    """A plan row a lane, at most once a RECORD_EVERY_S. Never raises."""
    from .store import stockplan as store

    clock = time.monotonic()
    if clock - _last_record["at"] < RECORD_EVERY_S:
        return
    _last_record["at"] = clock
    prune = clock - _last_record["pruned"] > 3600.0
    if prune:
        _last_record["pruned"] = clock
    rows = []
    for lane in LANES:
        row = asdict(plans[lane])
        row.update(mode=mode, used=int(used.get(lane, 0)),
                   warm=int((lanes.get(lane) or {}).get("warm", 0)),
                   coming=int(coming_by.get(lane, 0)))
        rows.append(row)
        log.debug("stock plan %s", row["why"],
                 extra={"stock_plan": {k: row[k] for k in (
                     "lane", "mode", "demand_h", "lead_min", "planned",
                     "fixed", "used", "warm", "coming", "waiting")}})
    try:
        store.record(settings, rows, prune=prune)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not record the stock plan (%s)", exc)
