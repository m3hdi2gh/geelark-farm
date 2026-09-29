"""The stock planner (2026-09-30): the arithmetic, the keeper's use of it,
the admin's Stock page, and every statement it sends, against a real
Postgres where `GEELARK_TEST_DSN` is set."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone

import pytest

import geelark_farm.store.stockplan  # noqa: F401 - imported before a fake Store
from geelark_farm import stockplan
from geelark_farm.store import stockplan as store
from tests import test_station_store as _station_store
from tests import test_web as _test_web
from tests.test_web import FakeStore, _form

#: The live-server fixture and the cluster's, by assignment so each test
#: may name them.
web = _test_web.web
farm = _station_store.farm
Raw = _station_store.Raw
needs_cluster = _station_store.needs_cluster
NOW = datetime(2026, 9, 30, 18, 30, tzinfo=timezone.utc)   # 22:00 in Tehran
TZ = "Asia/Tehran"
KNOBS = store.clean_knobs(None)


@pytest.fixture(autouse=True)
def _fresh_planner():
    stockplan._reset()
    yield
    stockplan._reset()


# ============================================================ arithmetic
def test_the_target_is_the_smallest_stock_that_keeps_the_wait_under_the_risk():
    assert stockplan.poisson_target(0, 0.05) == 0
    # λL = 1.8: P(N >= 5) = 3.6% <= 5% and P(N >= 4) = 10.9% > 5%.
    assert stockplan.poisson_target(1.8, 0.05) == 5
    assert stockplan.poisson_target(1.0, 0.05) == 4
    assert stockplan.poisson_target(1.0, 0.20) == 3
    # More demand never asks for fewer; more risk never asks for more.
    means = [0.1, 0.5, 1, 2, 3, 5, 8]
    for risk in (0.01, 0.05, 0.2):
        got = [stockplan.poisson_target(m, risk) for m in means]
        assert got == sorted(got)
    for m in means:
        got = [stockplan.poisson_target(m, r) for r in (0.01, 0.05, 0.2)]
        assert got == sorted(got, reverse=True)
    # It is the definition, not a table: check against the sum itself.
    for m in (0.3, 1.8, 4.2):
        s = stockplan.poisson_target(m, 0.05)
        tail = 1 - sum(math.exp(-m) * m ** k / math.factorial(k)
                       for k in range(s))
        before = 1 - sum(math.exp(-m) * m ** k / math.factorial(k)
                         for k in range(s - 1))
        assert tail <= 0.05 < before


def test_recent_takes_count_less_as_they_age():
    tau = stockplan.HALF_LIFE_MIN / math.log(2)
    one_now = stockplan.recent_rate([NOW], NOW)
    assert one_now == pytest.approx(60.0 / tau)
    half = stockplan.recent_rate([NOW - timedelta(minutes=30)], NOW)
    assert half == pytest.approx(one_now / 2, rel=1e-6)
    # Older than the window, or in the future: nothing.
    assert stockplan.recent_rate([NOW - timedelta(hours=4),
                                  NOW + timedelta(minutes=5)], NOW) == 0


def test_the_profile_is_the_fortnight_by_hour_split_by_each_lanes_share():
    # 22:00 Tehran = 18:30 UTC. Two days of four GPT takes in that hour,
    # and a Spotify take yesterday in it.
    events = []
    for day in (1, 2):
        at = NOW - timedelta(days=day)
        events += [("gpt", at + timedelta(minutes=k)) for k in range(4)]
    events.append(("spotify", NOW - timedelta(days=1, minutes=-10)))
    shape = stockplan.profile(events, NOW, TZ)
    assert shape["days"] == pytest.approx(2.0, abs=0.05)
    assert shape["total"][22] == pytest.approx(9 / 2, rel=0.05)
    assert shape["raw"]["gpt"][22] == pytest.approx(8 / 2, rel=0.05)
    assert shape["raw"]["spotify"][22] == pytest.approx(1 / 2, rel=0.05)
    # The last two days hold every event: the shares are Laplace's.
    assert shape["share"]["gpt"] == pytest.approx((8 + 1) / (9 + 2))
    assert shape["gpt"][22] == pytest.approx(shape["total"][22]
                                             * shape["share"]["gpt"])
    assert sum(shape["share"].values()) == pytest.approx(1.0)
    # The busier of this hour and the next.
    assert stockplan.upcoming([0] * 23 + [5], 23) == 5
    assert stockplan.upcoming([3] + [0] * 23, 23) == 3


def test_a_lane_with_few_builds_borrows_the_others_and_never_goes_past_the_floor():
    builds = {"gpt": {"n": 60, "ok": 27, "build_s": 360.0, "wait_s": 0.0},
              "spotify": {"n": 2, "ok": 0, "build_s": None, "wait_s": 30.0}}
    minutes, success = stockplan.lead_of("gpt", builds)
    assert success == pytest.approx(0.45)
    assert minutes == pytest.approx(6 / 0.45 + stockplan.OVERHEAD_MIN, abs=0.1)
    # Two Spotify builds are too few: GPT's rate and time stand, its own
    # queue wait counts.
    minutes, success = stockplan.lead_of("spotify", builds)
    assert success == pytest.approx(0.45)
    assert minutes == pytest.approx(6 / 0.45 + 0.5 + stockplan.OVERHEAD_MIN,
                                    abs=0.1)
    # Nothing at all: the prior and six minutes.
    assert stockplan.lead_of("gpt", {}) == (
        round(6 / stockplan.SUCCESS_PRIOR + stockplan.OVERHEAD_MIN, 1),
        stockplan.SUCCESS_PRIOR)
    bad = {"gpt": {"n": 50, "ok": 1, "build_s": 300.0, "wait_s": 0.0}}
    assert stockplan.lead_of("gpt", bad)[1] == stockplan.SUCCESS_FLOOR


def _plan(**over):
    args = dict(knobs=KNOBS, now=NOW, tz=TZ, events=[], builds={}, people=0,
                waiting={}, held={}, returns={}, fixed={"gpt": 4, "spotify": 4})
    args.update(over)
    return stockplan.plan(**args)


def test_the_plan_reads_the_recent_pace_while_people_work_and_the_profile_else():
    busy = [("spotify", NOW - timedelta(minutes=k)) for k in range(0, 60, 6)]
    at_work = _plan(events=busy, people=2)["spotify"]
    assert at_work.demand_h == pytest.approx(at_work.recent_h)
    assert at_work.recent_h > at_work.profile_h
    nobody = _plan(events=busy, people=0)["spotify"]
    assert nobody.demand_h == nobody.profile_h
    assert at_work.planned >= nobody.planned


def test_the_line_adds_the_returns_take_away_and_the_knobs_bound_it():
    quiet = _plan()["gpt"]
    assert quiet.planned == KNOBS["min"]["gpt"]           # no demand: the floor
    owed = _plan(waiting={"gpt": 2})["gpt"]
    assert owed.planned == max(KNOBS["min"]["gpt"], quiet.base + 2)
    assert "+2 for the line" in owed.why
    busy = [("gpt", NOW - timedelta(minutes=k)) for k in range(0, 60, 3)]
    back = _plan(events=busy, people=1, held={"gpt": 10},
                 returns={"gpt": {"handed": 20, "returned": 10}})["gpt"]
    assert back.credit == 5
    assert back.planned == max(KNOBS["min"]["gpt"],
                               min(KNOBS["max"]["gpt"], back.base - 5))
    tight = dict(KNOBS, max={"gpt": 2, "spotify": 8})
    capped = _plan(knobs=tight, events=busy, people=1)["gpt"]
    assert capped.planned == 2 and "maximum 2" in capped.why


def test_a_return_rate_is_believed_only_from_ten_handed_out():
    assert stockplan.return_rate("gpt", {}) == stockplan.RETURN_PRIOR
    few = {"gpt": {"handed": 3, "returned": 3}}
    assert stockplan.return_rate("gpt", few) == stockplan.RETURN_PRIOR
    many = {"gpt": {"handed": 20, "returned": 19}}
    assert stockplan.return_rate("gpt", many) == stockplan.RETURN_CAP


def test_the_knobs_are_made_whole_and_bounded():
    got = store.clean_knobs({"mode": "wild", "risk_pct": "7",
                             "min": {"gpt": "5", "spotify": -3},
                             "max": {"gpt": "2", "spotify": "99"},
                             "stale_hours": "0"})
    assert got["mode"] == "watch"
    assert got["risk_pct"] == 5                      # snapped to 1/2/5/10/20
    assert got["min"] == {"gpt": 5, "spotify": 0}
    assert got["max"] == {"gpt": 5, "spotify": 30}   # never under the min
    assert got["stale_hours"] == 1
    assert store.clean_knobs(None) == store.clean_knobs("nonsense")
    assert store.clean_knobs({"mode": "auto"})["mode"] == "auto"


# ================================================================ keeper
class _Read:
    """The planner's store reads, faked."""

    def __init__(self, monkeypatch, *, mode="watch", fail=False, waiting=None):
        self.recorded: list = []
        self.calls = 0
        knobs = store.clean_knobs({"mode": mode})
        monkeypatch.setattr(store, "knobs", lambda s: knobs)

        def boom(*a, **k):
            raise RuntimeError("the store is away")

        def events(s):
            self.calls += 1
            if fail:
                boom()
            return [("gpt", datetime.now(timezone.utc) - timedelta(minutes=k))
                    for k in range(0, 60, 2)]
        monkeypatch.setattr(store, "demand", events)
        monkeypatch.setattr(store, "lead", lambda s: {})
        monkeypatch.setattr(store, "people", lambda s: 2)
        monkeypatch.setattr(store, "held", lambda s: {"gpt": 0, "spotify": 0})
        monkeypatch.setattr(store, "returns", lambda s: {})
        monkeypatch.setattr(store, "waiting",
                            lambda s: waiting or {"gpt": 0, "spotify": 0})
        monkeypatch.setattr(store, "record",
                            lambda s, rows, prune=False: self.recorded.append(rows))


def _pass(settings):
    return stockplan.for_pass(settings, lanes={"gpt": {"warm": 3},
                                               "spotify": {"warm": 1}},
                              coming_by={"gpt": 1}, fixed={"gpt": 4, "spotify": 6})


def test_watching_builds_to_warm_stock_and_records_what_it_would_set(
        monkeypatch, make_settings):
    read = _Read(monkeypatch, mode="watch")
    targets, urgent = _pass(make_settings())
    assert targets == {"gpt": 4, "spotify": 6} and urgent == {}
    [rows] = read.recorded
    gpt = next(r for r in rows if r["lane"] == "gpt")
    assert gpt["mode"] == "watch" and gpt["used"] == 4 and gpt["fixed"] == 4
    assert gpt["warm"] == 3 and gpt["coming"] == 1
    assert gpt["planned"] > 4                    # thirty takes an hour
    # A second pass inside the minute reads nothing heavy and writes nothing.
    _pass(make_settings())
    assert read.calls == 1 and len(read.recorded) == 1


def test_steering_builds_to_the_plan_and_the_line_is_urgent(monkeypatch,
                                                            make_settings):
    read = _Read(monkeypatch, mode="auto", waiting={"gpt": 0, "spotify": 2})
    targets, urgent = _pass(make_settings())
    [rows] = read.recorded
    planned = {r["lane"]: r["planned"] for r in rows}
    assert targets == planned and urgent == {"gpt": 0, "spotify": 2}
    assert all(r["used"] == r["planned"] for r in rows)


def test_a_planner_that_cannot_read_builds_to_warm_stock(monkeypatch,
                                                         make_settings, caplog):
    _Read(monkeypatch, mode="auto", fail=True)
    targets, urgent = _pass(make_settings())
    assert targets == {"gpt": 4, "spotify": 6} and urgent == {}
    assert "could not plan" in caplog.text


def test_a_lane_somebody_waits_on_is_built_for_first():
    from geelark_farm.serve import _lanes_to_build

    lanes = {"gpt": {"warm": 0, "exits": 5}, "spotify": {"warm": 2, "exits": 5}}
    targets = {"gpt": 5, "spotify": 3}
    # Without a line the shorter shelf goes first.
    assert _lanes_to_build(1, lanes, targets) == {"gpt": 1, "spotify": 0}
    # With one person waiting for Spotify, Spotify's build goes first.
    got = _lanes_to_build(1, lanes, targets, urgent={"spotify": 1})
    assert got == {"gpt": 0, "spotify": 1}
    got = _lanes_to_build(3, lanes, targets, urgent={"spotify": 1})
    assert got == {"gpt": 2, "spotify": 1}
    # A lane with no room is never urgent.
    full = {"gpt": 5, "spotify": 2}
    assert _lanes_to_build(1, lanes, full, urgent={"spotify": 3}) == \
        {"gpt": 1, "spotify": 0}


def test_the_keeper_asks_the_planner_after_the_queue_is_counted():
    """The pass asks the planner once it knows what is coming, and builds
    to its answer: the decision, the order by lane and the pulse."""
    import inspect

    from geelark_farm import serve

    source = inspect.getsource(serve)
    ask = source.index("stockplan.for_pass(")
    assert source.index("coming_by = store_jobs.lanes(settings)") < ask
    assert ask < source.index("decision = decide(")
    assert "target=stock_target" in source
    assert "urgent=urgent)" in source
    assert '"target": stock_target' in source


# ================================================================== page
def _data(**over):
    now = datetime.now(timezone.utc)
    row = {"at": now, "mode": "watch", "demand_h": 6.3, "recent_h": 2.0,
           "profile_h": 6.3, "lead_min": 15.0, "success": 0.45, "planned": 5,
           "fixed": 4, "used": 4, "warm": 7, "coming": 0, "waiting": 1,
           "held": 0, "people": 3,
           "why": "GPT: 6.3 takes an hour <script>alert(1)</script> -> 5."}
    data = {"now": now, "knobs": store.clean_knobs(None),
            "fixed": {"gpt": 4, "spotify": 6},
            "latest": {"gpt": row, "spotify": dict(row, planned=4)},
            "history": [{"slot": now - timedelta(minutes=10 * k), "lane": "gpt",
                         "planned": 5, "used": 4, "fixed": 4, "warm": 7.0,
                         "waiting": 0, "mode": "watch"} for k in range(12)],
            "profile": stockplan.profile([("gpt", now - timedelta(hours=2))],
                                         now, TZ),
            "takes_24h": {"gpt": [1] * 24, "spotify": [0] * 23 + [3]},
            "waits": {"gpt": {"asked": 4, "at_once": 3, "waited": 1,
                              "refused": 0, "p50_s": 0, "p95_s": 200,
                              "max_s": 207}},
            "idle": [{"serial": "5135", "lane": "gpt", "idle_s": 7 * 3600},
                     {"serial": "5184", "lane": "spotify", "idle_s": 600}],
            "runway": {"free": 0, "per_hour": 9.7, "hours": None},
            "shelves": {"gpt": {"ready": 7, "building": 0},
                        "spotify": {"ready": 4, "building": 1}},
            "tz": TZ, "partial": []}
    data.update(over)
    return data


USER = {"id": 7, "username": "mehdi", "role": "admin", "sees": "all",
        "csrf": "c5rf", "user_admin": True, "mutations": True, "nav": {}}


def test_the_stock_page_shows_each_lane_its_charts_and_the_settings():
    from geelark_farm.web import stock_pages

    body = stock_pages.stock_page(_data(), USER, said="saved")
    assert "<h2>Stock" in body and "watching" in body
    assert body.count('class="chart"') == 3          # two days, one profile
    assert "Takes an hour, by hour of the day" in body
    assert "No free Gmail" in body
    # The why sentence is text, never markup.
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    # The stale phone is amber, the fresh one is not.
    assert '<tr class="stale"><td>5135</td>' in body
    assert '<tr class=""><td>5184</td>' in body
    # The form posts to its own door with the session's token.
    form = body[body.index('<form method="post" action="/stock"'):]
    assert 'name="csrf" value="c5rf"' in form
    for name in ("mode", "risk_pct", "min_gpt", "max_gpt", "min_spotify",
                 "max_spotify", "stale_hours"):
        assert f'name="{name}"' in form
    assert "Saved." in body
    assert "geelark" not in body.lower()


def test_a_stock_page_with_a_read_missing_says_so_and_draws_the_rest():
    from geelark_farm.web import stock_pages

    body = stock_pages.stock_page(_data(partial=["the waits"], latest={}),
                                  USER)
    assert "Some of this could not be read" in body and "the waits" in body
    assert "No plan yet" in body


def test_the_takes_of_the_day_fall_in_their_hour():
    from geelark_farm.web import stock_read

    now = datetime(2026, 9, 30, 18, 30, tzinfo=timezone.utc)
    got = stock_read.takes_by_hour([
        ("gpt", now - timedelta(minutes=5)),       # this hour: last slot
        ("gpt", now - timedelta(hours=1)),         # the hour before
        ("spotify", now - timedelta(hours=30)),    # outside the day
        ("other", now)], now)
    assert got["gpt"][23] == 1 and got["gpt"][22] == 1
    assert sum(got["spotify"]) == 0


# ================================================================== web
def _signed(client_cls, username: str = "mehdi"):
    client = client_cls()
    client.login(username=username)
    _, _, body = client.request("GET", "/")
    hit = re.search(r'name="csrf" value="([^"]*)"', body)
    client.token = hit.group(1) if hit else ""
    return client


def test_the_rail_has_stock_after_the_station():
    from geelark_farm.web import pages

    paths = [p for p, _, _ in pages._RAIL]
    assert paths.index("/stock") == paths.index("/station") + 1
    assert "<path" in pages._ICONS["/stock"]


def test_the_admin_reads_the_stock_page_and_an_operator_is_sent_home(
        web, monkeypatch):
    from geelark_farm.web import stock_read

    monkeypatch.setattr(stock_read, "state", lambda s: _data())
    client = _signed(web)
    status, _, body = client.request("GET", "/stock")
    assert status == 200 and "<h2>Stock" in body
    operator = {"id": 9, "username": "sara", "role": "operator", "sees": "all",
                "active": True}
    monkeypatch.setattr(FakeStore, "user", operator)
    other = _signed(web, "sara")
    status, headers, _ = other.request("GET", "/stock")
    assert status == 303 and dict(headers)["Location"] == "/"
    status, _, _ = other.request("POST", "/stock", _form(csrf=other.token,
                                                         mode="auto"))
    assert status == 403


def test_the_settings_are_saved_by_their_admin_and_a_failure_says_so(
        web, monkeypatch):
    saved: list = []

    def keep(settings, typed, *, by, by_id):
        saved.append((typed, by, by_id))
        return typed

    monkeypatch.setattr(store, "set_knobs", keep)
    client = _signed(web)
    status, headers, _ = client.request("POST", "/stock", _form(
        csrf=client.token, mode="auto", risk_pct="10", min_gpt="2",
        max_gpt="6", min_spotify="1", max_spotify="9", stale_hours="8"))
    assert status == 303 and dict(headers)["Location"] == "/stock?said=saved"
    [(typed, by, by_id)] = saved
    assert typed == {"mode": "auto", "risk_pct": "10",
                     "min": {"gpt": "2", "spotify": "1"},
                     "max": {"gpt": "6", "spotify": "9"}, "stale_hours": "8"}
    assert by == "mehdi" and by_id == 7

    def broken(*a, **k):
        raise RuntimeError("the store is away")

    monkeypatch.setattr(store, "set_knobs", broken)
    status, headers, _ = client.request("POST", "/stock", _form(
        csrf=client.token, mode="auto"))
    assert status == 303 and dict(headers)["Location"] == "/stock?said=no"
    # And a stale token changes nothing.
    status, _, _ = client.request("POST", "/stock", _form(csrf="nope"))
    assert status == 403


# ============================================================== cluster
def _stamp(minutes_ago: float) -> Raw:
    return Raw(f"now() - interval '{float(minutes_ago)} minutes'")


@needs_cluster
def test_a_take_and_a_first_boot_are_one_demand_each(farm):
    a = farm.user("a")
    before = len(store.demand(farm.s))
    # A Take on the Station, whatever it answered.
    farm.insert("actions", verb="take_phone", payload={"lane": "spotify",
                "tag": farm.tag}, requested_by=a, status="done",
                detail={"outcome": "took", "lane": "spotify",
                        "serial": "x" + farm.tag})
    # A stock phone booted twice from the dashboard: one demand.
    stock = farm.phone(purpose="gpt")
    farm.action("boot_phone", stock, a, status="done")
    farm.action("boot_phone", stock, a, status="done")
    # A phone the Station handed out, then booted: its Take was the demand.
    taken = farm.phone(purpose="spotify")
    farm.insert("actions", verb="take_phone", payload={"lane": "spotify",
                "tag": farm.tag}, requested_by=a, status="done",
                detail={"outcome": "took", "lane": "spotify", "serial": taken})
    farm.action("boot_phone", taken, a, status="done")
    # A phone built by hand for somebody, and one for another app: no shelf.
    mine = farm.phone(purpose="gpt", built_by=a)
    farm.action("boot_phone", mine, a, status="done")
    other = farm.phone(purpose="other")
    farm.action("boot_phone", other, a, status="done")
    got = store.demand(farm.s)
    assert len(got) - before == 3
    assert [lane for lane, _ in got[before:]].count("spotify") == 2


@needs_cluster
def test_the_lead_time_comes_from_the_builds_that_ran(farm):
    for k in range(4):
        farm.job(status="done", payload={"purpose": "spotify"},
                 claimed_at=_stamp(10 + k), created_at=_stamp(10.5 + k),
                 done_at=_stamp(4 + k), result={"worked": True})
    farm.job(status="failed", payload={"purpose": "spotify"},
             claimed_at=_stamp(30), created_at=_stamp(30),
             done_at=_stamp(27), result={"worked": False})
    # Ended at once (no Gmail): says nothing about how long a phone takes.
    farm.job(status="failed", payload={"purpose": "spotify"},
             claimed_at=_stamp(31), created_at=_stamp(31),
             done_at=Raw("now() - interval '31 minutes' + interval '2 seconds'"),
             result={"worked": False})
    # A hand build is not stock.
    farm.job(status="done", payload={"purpose": "spotify",
                                     "want": {"wanted_id": 1}},
             claimed_at=_stamp(9), created_at=_stamp(9), done_at=_stamp(1),
             result={"worked": True})
    got = store.lead(farm.s)["spotify"]
    assert got["n"] >= 5 and got["ok"] >= 4
    assert got["build_s"] == pytest.approx(360.0, abs=5)


@needs_cluster
def test_who_is_at_work_who_waits_and_who_holds(farm):
    a, b = farm.user("a"), farm.user("b")
    people = store.people(farm.s)
    farm.action("take_phone", "", a, status="done")
    assert store.people(farm.s) >= people + 1
    waits = store.waiting(farm.s)["spotify"]
    farm.wait(b, "spotify")
    farm.wait(a, "gpt", seen_at=_stamp(5))            # not looking any more
    assert store.waiting(farm.s)["spotify"] == waits + 1
    held = store.held(farm.s)["gpt"]
    farm.hold(a, purpose="gpt")
    farm.hold(b, purpose="gpt", taken_at=_stamp(90), state_at=_stamp(90))
    assert store.held(farm.s)["gpt"] == held + 1


@needs_cluster
def test_a_plan_is_recorded_read_back_and_drawn_by_ten_minutes(farm):
    rows = [dict(lane=lane, mode="watch", demand_h=1.5, recent_h=1.0,
                 profile_h=1.5, lead_min=12.0, success=0.5, planned=3, fixed=4,
                 used=4, warm=2, coming=1, waiting=0, held=0, people=1,
                 why=f"test {farm.tag}") for lane in ("gpt", "spotify")]
    try:
        store.record(farm.s, rows, prune=True)
        latest = store.latest(farm.s)
        assert latest["gpt"]["planned"] == 3 and latest["spotify"]["used"] == 4
        points = [r for r in store.history(farm.s, hours=1)
                  if r["lane"] == "gpt"]
        assert points and isinstance(points[-1]["slot"], datetime)
    finally:
        farm.sql("DELETE FROM stock_plans WHERE why = %s", (f"test {farm.tag}",))


@needs_cluster
def test_the_knobs_are_saved_with_who_changed_them(farm):
    a = farm.user("a")
    kept = farm.one("SELECT value FROM service_state WHERE key = %s",
                    (store.STOCK_PLAN_KEY,))
    try:
        saved = store.set_knobs(farm.s, {"mode": "auto", "risk_pct": 10,
                                         "min": {"gpt": 2, "spotify": 1},
                                         "max": {"gpt": 6, "spotify": 5}},
                                by="st_" + farm.tag, by_id=a)
        assert saved["mode"] == "auto" and saved["updated_by"] == "st_" + farm.tag
        got = store.knobs(farm.s)
        assert got["mode"] == "auto" and got["max"] == {"gpt": 6, "spotify": 5}
        row = farm.one("SELECT verb, status, result, payload FROM actions"
                       " WHERE requested_by = %s AND verb = 'set_stock_plan'", (a,))
        assert row["status"] == "done" and "steers the stock" in row["result"]
        assert row["payload"]["mode"] == "auto"
    finally:
        if kept is None:
            farm.sql("DELETE FROM service_state WHERE key = %s",
                     (store.STOCK_PLAN_KEY,))
        else:
            farm.sql("UPDATE service_state SET value = %s::jsonb WHERE key = %s",
                     (json.dumps(kept["value"]), store.STOCK_PLAN_KEY))


@needs_cluster
def test_the_waits_the_shelves_and_the_gmails(farm):
    a = farm.user("a")
    before = store.waits(farm.s)["gpt"]
    farm.insert("actions", verb="take_phone", payload={"lane": "gpt"},
                requested_by=a, status="done",
                detail={"outcome": "took", "lane": "gpt", "serial": "y"})
    farm.wait(a, "gpt", joined_at=_stamp(3), ended_at=_stamp(1),
              ended_why="served")
    got = store.waits(farm.s)["gpt"]
    assert got["asked"] == before["asked"] + 2
    assert got["at_once"] == before["at_once"] + 1
    assert got["waited"] == before["waited"] + 1
    assert got["max_s"] >= 110
    shelf = farm.phone(purpose="spotify", created_at=_stamp(120),
                       state_at=_stamp(90))
    idle = {r["serial"]: r for r in store.idle(farm.s)}
    assert idle[shelf]["lane"] == "spotify"
    assert idle[shelf]["idle_s"] == pytest.approx(90 * 60, abs=30)
    free = store.runway(farm.s)["free"]
    farm.resource("gmail", address=f"st{farm.tag}@example.com", password="p",
                  status="")
    assert store.runway(farm.s)["free"] == free + 1
