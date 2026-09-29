"""The Station's lanes, exits, pairing reserves, landing and builder hooks
(spec sections 4.3, 4.4, 4.6 and 4.7).

The unit tests at the top run anywhere, with every store function faked.
The ones under `needs_cluster` run the real SQL of the modules this
package owns (the other-lane claim of `store/pgpool.py`, and the mirror's
`mark_running` of `store/shadow.py`) against a real Postgres.
"""

from __future__ import annotations

import dataclasses
import os
import threading
import uuid
from types import SimpleNamespace

import pytest

import tests.test_builder as tb
from geelark_farm import builder, purposes
from geelark_farm import serve as serve_mod
from geelark_farm.flows.router import Outcome
from geelark_farm.kit import exits as kit_exits
from geelark_farm.pools import PhoneLog
from tests.test_builder import (
    PHONE_APP_HEADERS,
    SIGNED_IN,
    FakeLedger,
    Running,
    make_book,
)
from tests.test_pools import (
    PROXY_HEADERS,
    FakeWorksheet,
    phone_row,
    proxy_pool,
    proxy_row,
)

LANED = PROXY_HEADERS + ["Purpose"]

# The builder tests' fixtures, used here by name: a faked device, the
# settings they build with, and their autouse guards.
device = tb.device
settings = tb.settings
brisk_heartbeat = tb.brisk_heartbeat
_no_build_context_leaks = tb._no_build_context_leaks
_the_person_channel = tb._the_person_channel


def _laned(string: str, purpose: str, name: str, status: str = "free"):
    row = proxy_row(string, status=status, headers=LANED, name=name)
    row[LANED.index("Purpose")] = purpose
    return row


# ------------------------------------------------------------ the lane words
def test_phone_lane_reads_blank_as_gpt_and_knows_other():
    assert purposes.phone_lane("") == purposes.GPT
    assert purposes.phone_lane(None) == purposes.GPT
    assert purposes.phone_lane("GPT") == purposes.GPT
    assert purposes.phone_lane("claude") == purposes.GPT
    assert purposes.phone_lane(" Spotify ") == purposes.SPOTIFY
    assert purposes.phone_lane("other") == purposes.OTHER
    assert purposes.phone_lane(" Other ") == purposes.OTHER
    assert purposes.phone_lane("nonsense") == purposes.GPT
    # Other is no lane of stock: nothing that counts lanes ever meets it.
    assert purposes.OTHER == "other" and purposes.OTHER not in purposes.ALL
    assert purposes.normal("other") == ""
    assert purposes.word("other") == ""


def test_the_lane_a_wish_for_another_app_is_for_is_other():
    other = builder.Wanted(purpose="other", app="spotify")
    assert builder._lane_for(other) == purposes.OTHER
    assert builder._lane_for(other, "gpt") == purposes.OTHER, (
        "the wish's Other wins over the job's word")
    assert builder._lane_for(builder.Wanted(purpose=" OTHER ")) == purposes.OTHER
    # Everything else is what it was.
    assert builder._lane_for(None) == purposes.GPT
    assert builder._lane_for(None, "spotify") == purposes.SPOTIFY
    assert builder._lane_for(builder.Wanted(app="spotify")) == purposes.SPOTIFY
    assert builder._lane_for(builder.Wanted(purpose="gpt",
                                            app="spotify")) == purposes.GPT


# ------------------------------------------------- claims for the other lane
def test_a_sheet_claim_for_other_takes_unlabelled_then_either_lane():
    pool = proxy_pool([_laned("1.1.1.1:1:u:p", "spotify", "S1"),
                       _laned("1.1.1.2:1:u:p", "gpt", "G1"),
                       _laned("1.1.1.3:1:u:p", "", "E1")], headers=LANED)
    assert sorted(r.name for r in pool.for_lane("other")) == ["E1", "G1", "S1"]
    assert [r.name for r in pool.for_lane("gpt")] == ["G1", "E1"]
    assert pool.claim(purpose="other").name == "E1", "unlabelled first"
    assert pool.claim(purpose="other").name == "G1"
    assert pool.claim(purpose="other").name == "S1"
    assert pool.claim(purpose="other") is None
    # The two lanes are as they were.
    fresh = proxy_pool([_laned("1.1.1.1:1:u:p", "spotify", "S1")],
                       headers=LANED)
    assert fresh.claim(purpose="gpt") is None


def test_a_swap_mid_build_stays_in_the_builds_lane(monkeypatch):
    """`ExitLease.purpose` reaches `_fresh_proxy` through `_new_exit`, and a
    lease with no lane makes the call it always made."""
    book = make_book(proxies=3)
    first = book.proxies.claim()                        # the build's own
    asked = []

    def fresh(client, book_, **kw):
        asked.append(kw)
        return book.proxies.claim()

    monkeypatch.setattr(kit_exits, "_fresh_proxy", fresh)
    monkeypatch.setattr(kit_exits.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(kit_exits.phones, "set_proxy", lambda *a, **k: None)

    lease = kit_exits.ExitLease(current=first, purpose="spotify")
    on = lease.swap(None, None, book, builder.Build(index=1), "P", "refused",
                    "network", 60)
    assert on is lease.current and on is not first
    assert asked == [{"settings": None, "purpose": "spotify"}]

    asked.clear()
    plain = kit_exits.ExitLease(current=on)
    plain.swap(None, None, book, builder.Build(index=1), "P", "refused",
               "network", 60)
    assert asked == [{"settings": None}], "no lane, no keyword"


def test_a_build_hands_its_lane_to_its_lease(device, settings, monkeypatch):
    """`build_one` sets the lease's lane right after the build's, so every
    later swap claims where the first exit was claimed."""
    seen = {}
    real = builder._acquire

    def acquire(st):
        seen["lease"], seen["purpose"] = st.lease.purpose, st.purpose
        return real(st)

    monkeypatch.setattr(builder, "_BUILD_PHASES",
                        (acquire,) + builder._BUILD_PHASES[1:])
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)
    monkeypatch.setattr(builder.chatgpt_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)
    builder.build_one(None, settings, make_book(), FakeLedger(), 1,
                      purpose="spotify")
    assert seen == {"lease": "spotify", "purpose": "spotify"}


def test_a_finish_swaps_in_the_phones_own_lane(settings, monkeypatch):
    """A finish's session lease takes the phone's lane from the job."""
    seen = []

    def sign(session):
        seen.append(session.lease.purpose)
        return builder.Build(index=1, status="stop")

    monkeypatch.setattr(builder, "_sign_into_app", sign)
    monkeypatch.setattr(builder.phones, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(builder.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(builder.shell, "device_accounts",
                        lambda *a, **k: ["a@b.com"])
    monkeypatch.setattr(builder.shell, "third_party_packages",
                        lambda *a, **k: [settings.target_package])
    for purpose, lane in (("spotify", "spotify"), ("", "gpt"),
                          ("other", "other")):
        phone = {"phone_id": "P1", "serial": "1401", "gmail": "a@b.com",
                 "proxy": "", "status": "app_only", "purpose": purpose}
        builder.finish_one(Running(2), settings, make_book(), FakeLedger(),
                           phone, 1)
        assert seen[-1] == lane


def test_a_borrow_never_crosses_lanes():
    pool = proxy_pool([_laned("1.1.1.1:1:u:p", "spotify", "S1"),
                       _laned("1.1.1.2:1:u:p", "gpt", "G1")], headers=LANED)
    for row in pool._rows:
        pool.spend(row, serial="900", note="On phone 900.")
    book = SimpleNamespace(proxies=pool)

    assert kit_exits._borrow_exit(book, set(), purpose="gpt").name == "G1"
    assert kit_exits._borrow_exit(book, set(), purpose="spotify").name == "S1"
    assert kit_exits._borrow_exit(book, set()).name == "S1", "no lane: any"
    assert kit_exits._borrow_exit(book, set(), purpose="other").name == "S1"
    assert kit_exits._borrow_exit(book, {"1.1.1.2:1"}, purpose="gpt") is None, (
        "the other lane's is never borrowed, however full the pool")


def test_a_named_exit_from_the_other_lane_is_refused_in_words():
    pool = proxy_pool([_laned("1.1.1.1:1:u:p", "spotify", "S1"),
                       _laned("1.1.1.2:1:u:p", "", "E1")], headers=LANED)
    with pytest.raises(builder.Aborted) as refused:
        builder._pick(pool, "S1", "exit", purpose="gpt")
    assert str(refused.value) == "the IP S1 is kept for another lane"
    assert "exit" not in str(refused.value)
    assert [r.name for r in pool.available] == ["S1", "E1"], "nothing claimed"
    # Its own lane, an Other build, an unlabelled exit and a caller that
    # names no lane all get it.
    assert builder._pick(pool, "E1", "exit", purpose="gpt").name == "E1"
    assert builder._pick(pool, "S1", "exit", purpose="other").name == "S1"
    again = proxy_pool([_laned("1.1.1.1:1:u:p", "spotify", "S1")],
                       headers=LANED)
    assert builder._pick(again, "S1", "exit").name == "S1"
    again = proxy_pool([_laned("1.1.1.1:1:u:p", "spotify", "S1")],
                       headers=LANED)
    assert builder._pick(again, "S1", "exit", purpose="spotify").name == "S1"


def test_a_named_exit_build_passes_its_lane_to_the_pick(device, settings,
                                                        monkeypatch):
    picked = []
    real = builder._pick

    def pick(pool, wanted, what, **kw):
        picked.append((what, kw))
        return real(pool, wanted, what, **kw)

    monkeypatch.setattr(builder, "_pick", pick)
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)
    book = make_book(proxies=2)
    name = book.proxies._rows[1].label
    builder.build_one(None, settings, book, FakeLedger(), 1,
                      want=builder.Wanted(proxy_name=name, app="",
                                          purpose="spotify"))
    assert ("exit", {"purpose": "spotify"}) in picked
    assert all(kw == {} for what, kw in picked if what != "exit"), (
        "only the exit is judged by lane")


# ---------------------------------------------------------- warm stock
def test_an_other_phone_is_never_warm_stock():
    headers = PHONE_APP_HEADERS + ["Purpose"]
    rows = []
    for serial, purpose in (("991", "other"), ("992", "gpt"), ("993", ""),
                            ("994", "Other"), ("995", "spotify")):
        row = phone_row(serial, headers=headers)
        row[headers.index("Purpose")] = purpose
        rows.append(row)
    log = PhoneLog(FakeWorksheet(headers, rows), headers, threading.Lock())

    assert [r["serial"] for r in log.unfinished()] == ["992", "993", "995"]
    assert [r["serial"] for r in log.unfinished(held_too=True)] == [
        "992", "993", "995"], "the keeper does not count it either"


# ------------------------------------------------------- pairing reserves
def test_the_keepers_finish_loop_skips_a_phone_it_cannot_reserve(
        make_settings, monkeypatch):
    from geelark_farm import keeper
    from geelark_farm.store import jobs as store_jobs
    from geelark_farm.store import station as store_station

    ordered, asked = [], []
    monkeypatch.setattr(store_jobs, "queue",
                        lambda s, kind, payload=None, action_id=None:
                        ordered.append((kind, payload)) or 1)
    monkeypatch.setattr(keeper, "_unfinished", lambda client, book, **k: (
        [{"serial": "7", "purpose": ""}, {"serial": "8", "purpose": "spotify"},
         {"serial": "9", "purpose": ""}], []))
    monkeypatch.setattr(keeper, "_busy_serials", lambda s: set())

    def reserve(s, serial, **kw):
        asked.append((serial, kw))
        return {"7": None, "8": "app_only", "9": ""}[serial]

    monkeypatch.setattr(store_station, "reserve_warm", reserve)
    settings = make_settings(store_enabled=True, build_queue=True)
    decision = SimpleNamespace(build=0, finish=3, jobs=3)

    n = serve_mod._order(settings, None, None, decision, [])

    assert asked == [("7", {}), ("8", {}), ("9", {})], (
        "the keeper reserves as nobody: owner_id is not passed")
    assert n == 2
    assert ordered == [
        ("finish", {"phone": {"serial": "8", "purpose": "spotify",
                              "status_before": "app_only"}}),
        ("finish", {"phone": {"serial": "9", "purpose": ""}})], (
        "7 was taken meanwhile; a blank status rides as nothing")


def test_a_store_that_cannot_reserve_reads_as_reserved(make_settings,
                                                       monkeypatch, caplog):
    from geelark_farm import keeper
    from geelark_farm.store import jobs as store_jobs
    from geelark_farm.store import station as store_station

    ordered = []
    monkeypatch.setattr(store_jobs, "queue",
                        lambda s, kind, payload=None, action_id=None:
                        ordered.append(payload) or 1)
    monkeypatch.setattr(keeper, "_unfinished",
                        lambda client, book, **k: ([{"serial": "7"}], []))
    monkeypatch.setattr(keeper, "_busy_serials", lambda s: set())

    def broken(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(store_station, "reserve_warm", broken)
    n = serve_mod._order(make_settings(store_enabled=True, build_queue=True),
                         None, None, SimpleNamespace(build=0, finish=1, jobs=1),
                         [])
    assert n == 1 and ordered == [{"phone": {"serial": "7"}}]
    assert "could not reserve phone 7" in caplog.text
    # With the store off nothing is asked.
    monkeypatch.setattr(store_station, "reserve_warm",
                        lambda *a, **k: pytest.fail("asked with the store off"))
    assert serve_mod._reserve_warm(make_settings(store_enabled=False), "7") == ""


def test_the_builders_own_batches_reserve_each_phone_first(settings,
                                                          monkeypatch):
    from geelark_farm import keeper
    from geelark_farm.store import station as store_station

    ran = []
    monkeypatch.setattr(builder, "_run_jobs",
                        lambda client, s, book, jobs, **k: ran.append(jobs) or [])
    monkeypatch.setattr(keeper, "_unfinished", lambda client, book, **k: (
        [{"serial": "7"}, {"serial": "8"}], []))
    monkeypatch.setattr(store_station, "reserve_warm",
                        lambda s, serial, **k: None if serial == "7"
                        else "incomplete")
    on = dataclasses.replace(settings, store_enabled=True)
    book = make_book()

    builder.finish_run(None, on, book=book, ledger=FakeLedger())
    assert ran[-1] == [{"kind": "finish",
                        "phone": {"serial": "8", "status_before": "incomplete"}}]

    builder.run(None, on, count=2, book=book, ledger=FakeLedger())
    finishes = [j for j in ran[-1] if j["kind"] == "finish"]
    assert finishes == [{"kind": "finish", "phone": {
        "serial": "8", "status_before": "incomplete"}}]
    assert len([j for j in ran[-1] if j["kind"] == "build"]) == 0, (
        "a phone taken meanwhile is not replaced by a build here")

    monkeypatch.setattr(store_station, "reserve_warm",
                        lambda s, serial, **k: None)
    ran.clear()
    assert builder.finish_run(None, on, book=book, ledger=FakeLedger()) == []
    assert ran == [], "nothing left to finish, nothing run"

    # The store off: every phone goes as it came, nothing asked.
    monkeypatch.setattr(store_station, "reserve_warm",
                        lambda *a, **k: pytest.fail("asked with the store off"))
    builder.finish_run(None, settings, book=book, ledger=FakeLedger())
    assert [j["phone"] for j in ran[-1]] == [{"serial": "7"}, {"serial": "8"}]


def test_a_finish_that_ends_early_puts_back_the_status_it_was_reserved_from(
        settings, monkeypatch):
    """The pairing turned the phone `building` before the finish began, so
    the row's own word is `building`: what goes back is what the job
    carries."""
    book = make_book(gmails=3, proxies=3, apps=3,
                     phone_headers=PHONE_APP_HEADERS)
    tab = book.phones._ws
    book.phones.start(Serial="1401", Proxy="SX1")
    builder._record(book, builder.Build(
        index=1, status="no_usable_gpt", serial="1401",
        gmail="a@b.com", app_installed=True))

    def status():
        return tab.rows[0][PHONE_APP_HEADERS.index("Status")]

    builder.rows._note_on_row(book, "1401", Status=book.phones.BUILDING)
    assert status() == book.phones.BUILDING, "reserved by the pairing"

    def would_not_start(*a, **k):
        raise builder.phones.PhoneError("phone P1 would not start")

    monkeypatch.setattr(builder.phones, "ensure_running", would_not_start)
    monkeypatch.setattr(builder.phones, "stop", lambda *a, **k: None)
    phone = {"phone_id": "P1", "serial": "1401", "gmail": "a@b.com",
             "proxy": "", "status": "app_only", "status_before": "incomplete"}

    build = builder.finish_one(Running(2), settings, book, FakeLedger(),
                               phone, 1)

    assert build.status == "phone_would_not_start"
    assert status() == "incomplete", "the reserved-from status, not building"


def test_a_finish_refused_for_a_phone_in_use_puts_back_its_reserved_status(
        settings, monkeypatch):
    notes = []
    monkeypatch.setattr(builder.rows, "_note_on_row",
                        lambda book, serial, **k: notes.append((serial, k)))
    monkeypatch.setattr(builder.phones, "stop",
                        lambda *a, **k: pytest.fail("the person's phone"))
    phone = {"phone_id": "P1", "serial": "1401", "gmail": "a@b.com",
             "proxy": "", "status": "app_only", "status_before": "app_only"}

    build = builder.finish_one(Running(0), settings, make_book(), FakeLedger(),
                               phone, 1)

    assert build.status == "in_use_by_hand"
    assert notes == [("1401", {"Status": "app_only"})]


# --------------------------------------------------------- wish attach
def _store_wishes(monkeypatch, *, attach=False, called_off=False):
    from geelark_farm.store import station as store_station
    from geelark_farm.store import wanted as store_wanted

    seen = {"attach": [], "land": [], "called_off": []}
    monkeypatch.setattr(store_wanted, "attach",
                        lambda s, wid, serial: seen["attach"].append(
                            (wid, serial)) or attach)
    monkeypatch.setattr(store_wanted, "called_off",
                        lambda s, wid: seen["called_off"].append(wid)
                        or called_off)
    monkeypatch.setattr(store_station, "land",
                        lambda s, *, serial, wanted_id: seen["land"].append(
                            (serial, wanted_id)) or True)
    return seen


def test_a_wish_called_off_before_its_phone_existed_ends_at_attach(
        device, settings, monkeypatch):
    deleted = []
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))
    signed = []
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: signed.append(1) or SIGNED_IN)
    seen = _store_wishes(monkeypatch, attach=True)
    book = make_book(gmails=1, proxies=2)

    build = builder.build_one(None, settings, book, FakeLedger(), 1,
                              want=builder.Wanted(wanted_id=41, requested_by=3,
                                                  app=""))

    assert seen["attach"] == [(41, "622")]
    assert build.status == "stopped_by_hand" and not build.ok
    assert signed == [], "nothing was signed in"
    assert deleted == ["PHONE1"], "the empty phone goes"
    assert len(book.gmails.available) == 1, "its Gmail goes back"
    assert len(book.proxies.available) == 2, "and so does its exit"
    assert seen["land"] == [], "an empty phone never lands"


def test_a_wish_that_was_not_called_off_builds_on(device, settings,
                                                  monkeypatch):
    seen = _store_wishes(monkeypatch, attach=False)
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)
    build = builder.build_one(None, settings, make_book(), FakeLedger(), 1,
                              want=builder.Wanted(wanted_id=42, requested_by=3,
                                                  app=""))
    assert seen["attach"] == [(42, "622")]
    assert build.ok, build.detail
    # A keeper build (no wish) asks nothing.
    seen["attach"].clear()
    builder.build_one(None, settings, make_book(), FakeLedger(), 2)
    assert seen["attach"] == []


# ----------------------------------------------------- called-off bare
def test_a_called_off_bare_build_is_discarded_and_a_dashboard_stopped_one_is_kept(
        device, settings, monkeypatch):
    from geelark_farm import builder as builder_mod

    deleted = []
    monkeypatch.setattr(builder.phones, "delete",
                        lambda c, ids, ledger=None: deleted.extend(ids))

    def bare_stopped(wanted_id):
        builder_mod.STOP_BY_HAND.add("622")
        try:
            return builder.build_one(
                None, settings, make_book(), FakeLedger(), 1,
                want=builder.Wanted(no_gmail=True, app="", wanted_id=wanted_id,
                                    requested_by=3))
        finally:
            builder_mod.STOP_BY_HAND.discard("622")

    seen = _store_wishes(monkeypatch, called_off=True)
    build = bare_stopped(43)
    assert build.status == "stopped_by_hand"
    assert seen["called_off"] == [43]
    assert deleted == ["PHONE1"], "called off: nobody wants it"
    assert seen["land"] == []

    deleted.clear()
    seen = _store_wishes(monkeypatch, called_off=False)
    build = bare_stopped(44)
    assert build.status == "stopped_by_hand"
    assert deleted == [], "stopped from the old dashboard: kept"

    deleted.clear()
    seen = _store_wishes(monkeypatch, called_off=True)
    build = bare_stopped(None)
    assert deleted == [] and seen["called_off"] == [], (
        "a bare build with no wish row is never asked about")


def test_a_bare_build_that_ends_ready_is_never_asked_whether_it_was_called_off(
        device, settings, monkeypatch):
    seen = _store_wishes(monkeypatch, called_off=True)
    build = builder.build_one(None, settings, make_book(), FakeLedger(), 1,
                              want=builder.Wanted(no_gmail=True, app="",
                                                  wanted_id=45, requested_by=3))
    assert build.ok and seen["called_off"] == []
    assert seen["land"] == [("622", 45)]


def test_called_off_is_never_fatal(monkeypatch, caplog):
    from geelark_farm.store import wanted as store_wanted

    def broken(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(store_wanted, "called_off", broken)
    assert builder._called_off(None, 7) is False
    assert "could not ask whether wish 7 was called off" in caplog.text


# ------------------------------------------------------------- landing
def test_a_station_build_lands_as_a_station_hold_and_a_dashboard_one_does_not(
        device, settings, monkeypatch):
    seen = _store_wishes(monkeypatch)
    monkeypatch.setattr(builder.google_login, "sign_in",
                        lambda *a, **k: SIGNED_IN)

    build = builder.build_one(None, settings, make_book(), FakeLedger(), 1,
                              want=builder.Wanted(wanted_id=46, requested_by=3,
                                                  app=""))
    assert build.ok and seen["land"] == [("622", 46)], (
        "every wish is offered to land; land itself knows a Station wish")

    seen["land"].clear()
    builder.build_one(None, settings, make_book(), FakeLedger(), 2)
    assert seen["land"] == [], "a keeper build has no wish to land"


def test_a_failed_empty_wish_build_never_lands(device, settings, monkeypatch):
    monkeypatch.setattr(builder.phones, "delete", lambda *a, **k: None)
    monkeypatch.setattr(builder.shell, "device_accounts", lambda *a, **k: [])
    seen = _store_wishes(monkeypatch)
    wrong = Outcome("fatal", "wrong_password")
    monkeypatch.setattr(builder.google_login, "sign_in", lambda *a, **k: wrong)
    book = make_book(gmails=2)
    build = builder.build_one(None, settings, book, FakeLedger(), 1,
                              want=builder.Wanted(gmail="g0@example.com",
                                                  wanted_id=47, requested_by=3))
    assert not build.ok
    assert seen["land"] == []


def test_landing_is_never_fatal(monkeypatch, caplog):
    from geelark_farm.store import station as store_station

    def broken(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(store_station, "land", broken)
    builder._land_on_station(None, "622", 5)
    assert "phone 622 did not land on the station" in caplog.text


def test_the_new_words_name_no_vendor():
    import inspect

    for fn in (builder._pick, builder._called_off, builder._land_on_station,
               builder._reserve_for_finish, serve_mod._reserve_warm,
               kit_exits._borrow_exit):
        source = inspect.getsource(fn)
        body = source.split('"""', 2)[-1]
        assert "geelark" not in body.lower(), fn.__name__


# ========================================================= against a cluster
DSN = os.environ.get("GEELARK_TEST_DSN", "")
needs_cluster = pytest.mark.skipif(
    not DSN, reason="set GEELARK_TEST_DSN to run store integration tests")
_SCHEMA_READY: list = []


@pytest.fixture
def cluster(make_settings):
    from geelark_farm.store import db as store_db

    parts = dict(p.split("=", 1) for p in DSN.split())
    s = make_settings(
        store_enabled=True, store_host=parts["host"],
        store_port=int(parts.get("port", 5432)), store_db=parts["dbname"],
        store_user=parts["user"], store_password=parts["password"],
        pools_in_pg=True)
    if not _SCHEMA_READY:
        store_db.ensure_schema(s)
        _SCHEMA_READY.append(True)
    return s


@needs_cluster
def test_an_other_claim_takes_unlabelled_first_then_the_fuller_lane(cluster):
    from geelark_farm.store.pgpool import PgProxyPool, ResourceTable

    table = ResourceTable(cluster)
    tag = "b2lanes-" + uuid.uuid4().hex[:8]
    with table._connect() as conn:
        free = conn.execute(
            "SELECT count(*) FROM resources WHERE kind = 'proxy'"
            " AND lower(status) IN ('', 'free', 'unused')").fetchone()[0]
        conn.rollback()
    assert free == 0, "this database holds free exits this test does not own"
    ids = []

    def add(name: str, purpose: str, port: int) -> None:
        ids.append(table.insert(dict(
            kind="proxy", host=f"{name.lower()}-{tag}.test", port=port,
            username="u", proxy_pass="p", proxy_name=f"{name}-{tag}",
            status="free", source="sheet", purpose=purpose, note=tag)))

    try:
        add("E1", "", 1)
        add("G1", "gpt", 2)
        add("S1", "spotify", 3)
        add("S2", "spotify", 4)
        pool = PgProxyPool(table)
        pool.load()
        assert pool.free_now("other") == 4, "an Other build may take any"
        assert pool.free_now("gpt") == 2 and pool.free_now("spotify") == 3

        got = [pool.claim(serial="9b2", purpose="other") for _ in range(5)]
        names = [r.name.split("-")[0] if r is not None else None for r in got]
        # Unlabelled first; then Spotify, which has more free (2 vs 1);
        # then a tie, which GPT wins; then the last Spotify; then nothing.
        assert names == ["E1", "S1", "G1", "S2", None], names
        assert pool.free_now("other") == 0

        # The two lanes keep today's rule on the same rows.
        with table._connect() as conn:
            conn.execute("UPDATE resources SET status = 'free', serial = ''"
                         " WHERE note = %s", (tag,))
            conn.commit()
        pool.load()
        assert pool.claim(purpose="gpt").name.startswith("G1")
        assert pool.claim(purpose="gpt").name.startswith("E1")
        assert pool.claim(purpose="gpt") is None, "never a Spotify exit"
    finally:
        with table._connect() as conn:
            conn.execute("DELETE FROM resources WHERE note = %s", (tag,))
            conn.commit()


@needs_cluster
def test_mark_running_spares_a_fresh_boot_and_blanks_the_link_of_a_phone_seen_off(
        cluster):
    from geelark_farm.store import db as store_db
    from geelark_farm.store import shadow

    base = uuid.uuid4().int % 1_000_000
    serials = {k: f"9{base:06d}{n:02d}" for n, k in enumerate(
        ("fresh", "old", "never", "on", "nulled", "listed_on"))}
    with store_db.connect(cluster) as conn:
        try:
            for key, running, since, url in (
                    ("fresh", True, "now()", "https://v/fresh"),
                    ("old", True, "now() - interval '5 minutes'", "https://v/old"),
                    ("never", False, "NULL", ""),
                    ("on", False, "NULL", "https://v/stale"),
                    ("nulled", True, "NULL", "https://v/nulled"),
                    ("listed_on", True, "now() - interval '5 minutes'",
                     "https://v/kept")):
                conn.execute(
                    "INSERT INTO phones (serial, phone_id, status, state,"
                    " purpose, running, running_since, live_url, updated_at)"
                    f" VALUES (%s, %s, 'ready', '', 'gpt', %s, {since}, %s,"
                    " now() - interval '1 hour')",
                    (serials[key], "PH" + serials[key], running, url))
            with conn.cursor() as cur:
                # Only these rows' answers are judged; every other live row
                # in the database is left where it was by listing it as it
                # stands.
                cur.execute("SELECT serial FROM phones WHERE done_at IS NULL"
                            " AND running AND NOT (serial = ANY(%s))",
                            (list(serials.values()),))
                others_on = [r[0] for r in cur.fetchall()]
                listed = others_on + [serials["on"], serials["listed_on"]]
                changed = shadow.mark_running(cur, listed)
                cur.execute("SELECT serial, running, running_since IS NOT NULL,"
                            " live_url, updated_at > now() - interval '1 minute'"
                            " FROM phones WHERE serial = ANY(%s)",
                            (list(serials.values()),))
                got = {r[0]: r[1:] for r in cur.fetchall()}
            by = {k: got[s] for k, s in serials.items()}
            assert by["fresh"] == (True, True, "https://v/fresh", False), (
                "a Boot in the last 120 s is not flipped off by a stale listing")
            assert by["old"] == (False, False, "", True), (
                "seen off: off, its clock and its link gone")
            assert by["never"] == (False, False, "", False), "untouched"
            assert by["on"] == (True, True, "https://v/stale", True), (
                "seen on: running with a fresh clock; the link is not the "
                "mirror's to write")
            assert by["nulled"] == (False, False, "", True), (
                "a running row with no clock is still judged")
            assert by["listed_on"] == (True, True, "https://v/kept", False), (
                "an unchanged row is not touched")
            assert changed >= 3
        finally:
            conn.rollback()
