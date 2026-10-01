"""The daily cap on an exit (rev 44, 2026-10-01)."""

from __future__ import annotations

import inspect

from geelark_farm.store import pgpool
from geelark_farm.store.pgpool import PgProxyPool, at_daily_cap
from tests.test_pgpool import MemoryTable


def test_an_exit_at_its_cap_today_is_held():
    v = {"Uses per day": "2", "Uses today": "2", "Uses on": "2026-10-01"}
    assert at_daily_cap(v, today="2026-10-01")
    assert not at_daily_cap(dict(v, **{"Uses today": "1"}), today="2026-10-01")


def test_a_new_day_or_no_cap_frees_it():
    v = {"Uses per day": "2", "Uses today": "5", "Uses on": "2026-09-30"}
    assert not at_daily_cap(v, today="2026-10-01")
    assert not at_daily_cap(dict(v, **{"Uses per day": ""}),
                            today="2026-09-30")


def test_the_claim_and_the_count_read_the_cap():
    sql, params = PgProxyPool(MemoryTable()).held_back()
    assert "uses_per_day IS NULL" in sql and "day_uses < uses_per_day" in sql
    assert "Asia/Tehran" in sql and params == ()
    lane_sql, lane_params = PgProxyPool(MemoryTable())._lane("gpt")
    assert lane_sql.startswith(sql) and lane_params == ("gpt",)


def test_the_claim_counts_the_day_for_exits_only():
    src = inspect.getsource(pgpool.ResourceTable.claim)
    assert "day_uses = CASE WHEN r.day_uses_on = " in src
    assert 'if count_use else ""' in src


def test_available_leaves_out_a_capped_exit(monkeypatch):
    monkeypatch.setattr(pgpool, "_tehran_today", lambda: "2026-10-01")
    table = MemoryTable()
    table.add("proxy", host="1.1.1.1", port=1, proxy_name="US1",
              uses_per_day=2, day_uses=2, day_uses_on="2026-10-01")
    table.add("proxy", host="2.2.2.2", port=2, proxy_name="US2",
              uses_per_day=2, day_uses=1, day_uses_on="2026-10-01")
    pool = PgProxyPool(table)
    pool.load()
    assert [r.values["Name"] for r in pool.available] == ["US2"]
