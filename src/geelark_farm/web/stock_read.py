"""What the admin's Stock page reads: the planner's knobs, what it set
last and over the day, the demand it reads it from, and how long the
operators waited (2026-09-30).

Every read stands alone: one that fails is named in `partial` and the
page draws the rest. Nothing here writes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


def state(settings) -> dict:
    from .. import purposes, stockplan
    from ..store import station as store_station
    from ..store import stockplan as store

    now = datetime.now(timezone.utc)
    out: dict = {"now": now, "knobs": store.knobs(settings),
                 "fixed": purposes.targets(settings), "latest": {},
                 "history": [], "profile": None, "takes_24h": {},
                 "waits": {}, "idle": [], "runway": None, "shelves": {},
                 "tz": getattr(settings, "web_tz", "Asia/Tehran"),
                 "partial": []}

    def read(name: str, call):
        try:
            return call()
        except Exception as exc:                                  # noqa: BLE001
            log.warning("the stock page could not read %s (%s)", name, exc)
            out["partial"].append(name)
            return None

    out["latest"] = read("the last plan", lambda: store.latest(settings)) or {}
    out["history"] = read("the day's plans",
                          lambda: store.history(settings, hours=24)) or []
    events = read("the demand", lambda: store.demand(settings)) or []
    out["profile"] = stockplan.profile(events, now, out["tz"])
    out["takes_24h"] = takes_by_hour(events, now)
    out["waits"] = read("the waits", lambda: store.waits(settings, hours=24)) or {}
    out["idle"] = read("the shelves' ages", lambda: store.idle(settings)) or []
    out["runway"] = read("the Gmails", lambda: store.runway(settings))
    out["shelves"] = read("the shelves",
                          lambda: store_station.shelves(settings)) or {}
    return out


def takes_by_hour(events: list, now: datetime) -> dict:
    """{lane: [24 counts]}: the takes of each of the last 24 hours, the
    oldest hour first and the hour now last."""
    from ..stockplan import LANES

    out = {lane: [0] * 24 for lane in LANES}
    top = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    for lane, at in events:
        if lane not in out or at is None:
            continue
        back = int((top - at).total_seconds() // 3600)
        if 0 <= back < 24:
            out[lane][23 - back] += 1
    return out
