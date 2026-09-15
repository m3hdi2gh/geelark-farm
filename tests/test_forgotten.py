"""The forgotten-phone sweep: what somebody took or booted and left is
switched off and put back after RELEASE_AFTER_MINUTES (2026-09-15)."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from geelark_farm import forgotten
from geelark_farm import phones as phones_mod
from geelark_farm.store import events as store_events


def _row(serial, *, state="taken", owner="ali", taken=4320, on=None,
         status="ready"):
    """One row the way `overdue` hands it back."""
    return {"serial": serial, "status": status, "state": state,
            "owner": owner, "taken_seconds": taken, "on_seconds": on}


def _on(serial, status=phones_mod.RUNNING):
    return {"serialNo": serial, "id": f"P{serial}", "status": status}


class Farm:
    def __init__(self, settings):
        self.settings = settings
        self.rows: list[dict] = []
        self.asked: list[int] = []
        self.stopped: list[str] = []
        self.released: list[str] = []
        self.events: list[tuple] = []
        self.entries: dict = {}

    @property
    def ledger(self):
        return SimpleNamespace(get=lambda pid: self.entries.get(pid))


@pytest.fixture
def farm(monkeypatch, make_settings, tmp_path):
    f = Farm(make_settings(state_dir=tmp_path, store_enabled=True,
                           release_after_minutes=60))
    monkeypatch.setattr(forgotten, "overdue",
                        lambda s, m: f.asked.append(m) or list(f.rows))
    monkeypatch.setattr(phones_mod, "stop",
                        lambda c, pid: f.stopped.append(pid))
    monkeypatch.setattr(forgotten, "_release",
                        lambda s, serial: f.released.append(serial) or True)
    monkeypatch.setattr(store_events, "emit",
                        lambda s, kind, **fields:
                        f.events.append((kind, fields)) or True)
    return f


def test_a_taken_phone_left_on_for_over_an_hour_is_switched_off_and_put_back(
        farm):
    """Boot wrote it taken and started it; nobody pressed Release. Off in
    GeeLark, nobody's in the store, and an event that says whose it was
    and for how long."""
    farm.rows.append(_row("2713", taken=4320))

    outcome = forgotten.sweep(object(), farm.settings, farm.ledger,
                              [_on("2713")])

    assert farm.asked == [60], "the clock is RELEASE_AFTER_MINUTES"
    assert farm.stopped == ["P2713"] and farm.released == ["2713"]
    assert outcome == {"off": ["2713"], "released": ["2713"], "held": []}
    assert farm.events == [("phone", {
        "serial": "2713", "status": "released",
        "detail": "switched off and put back after 1 h 12 min with ali - "
                  "nobody pressed Release"})]


def test_a_taken_phone_that_is_already_off_is_only_put_back(farm):
    """Take without Boot, or booted and switched off by hand but never
    released: the shelf gets it back, and no stop is spent on a phone
    that is not billing."""
    farm.rows.append(_row("2713", taken=2880))

    outcome = forgotten.sweep(object(), farm.settings, farm.ledger,
                              [_on("2713", phones_mod.STOPPED)])

    assert farm.stopped == [] and farm.released == ["2713"]
    assert outcome["off"] == [] and outcome["released"] == ["2713"]
    assert farm.events[0][1]["detail"] == (
        "put back after 48 min with ali - nobody pressed Release (it was "
        "already off)")


def test_a_phone_a_run_holds_is_left_to_the_run(farm):
    """A finish job boots a phone the sweep would otherwise see as on with
    nobody holding it. The ledger's live claim is the run's word, and a
    stale one is not."""
    farm.rows.append(_row("2713", state="", owner="", on=4000))
    farm.entries["P2713"] = SimpleNamespace(is_claimed=True, is_stale=False,
                                            label="r7")

    outcome = forgotten.sweep(object(), farm.settings, farm.ledger,
                              [_on("2713")])

    assert outcome == {"off": [], "released": [], "held": ["2713"]}
    assert farm.stopped == [] and farm.events == []

    farm.entries["P2713"] = SimpleNamespace(is_claimed=True, is_stale=True,
                                            label="r7")
    outcome = forgotten.sweep(object(), farm.settings, farm.ledger,
                              [_on("2713")])
    assert outcome["off"] == ["2713"], "a stale claim holds nothing"


def test_a_hand_built_phone_is_put_back_like_any_other(farm):
    """The build card wrote it taken for whoever asked; an hour of not
    using it ends that like any Take (the operator, 2026-09-15: "release
    the hand-built one too"). The row carries nothing that says it was
    hand-built, and the sweep does not ask."""
    farm.rows.append(_row("2713", taken=5400, on=5400))

    outcome = forgotten.sweep(object(), farm.settings, farm.ledger,
                              [_on("2713")])

    assert farm.stopped == ["P2713"] and farm.released == ["2713"]
    assert outcome["off"] == ["2713"] and outcome["released"] == ["2713"]
    assert "built_by" not in inspect.getsource(forgotten), (
        "no exception for the build card's phones")


def test_a_phone_on_with_nobody_holding_it_is_switched_off(farm):
    """Booted by hand in GeeLark, or released while still up: the console
    showed it as Running and nothing ended its billing (2026-09-08)."""
    farm.rows.append(_row("2713", state="", owner="", on=3700))

    forgotten.sweep(object(), farm.settings, farm.ledger, [_on("2713")])

    assert farm.stopped == ["P2713"] and farm.released == []
    assert farm.events[0][1]["status"] == "switched off"
    assert farm.events[0][1]["detail"] == (
        "switched off after 1 h 1 min on with nobody here holding it - "
        "booted by hand in GeeLark, or released while still up")


def test_a_taken_phone_seen_on_says_whose_it_was_even_when_not_put_back(
        farm, monkeypatch):
    """The store would not take the release: the phone is off and the
    event still names the person, so the row's owner can be asked."""
    farm.rows.append(_row("2713", on=4000))

    def refuse(settings, serial):
        raise RuntimeError("store down")

    monkeypatch.setattr(forgotten, "_release", refuse)
    outcome = forgotten.sweep(object(), farm.settings, farm.ledger,
                              [_on("2713")])

    assert outcome["off"] == ["2713"] and outcome["released"] == []
    assert farm.events[0][1]["detail"] == (
        "switched off after 1 h 6 min on with ali")


def test_a_phone_that_will_not_stop_is_not_put_back(farm, monkeypatch):
    """Released while still billing is the bug this exists to end. GeeLark
    refusing the stop means the phone stays taken and is tried again next
    pass, and nothing is said until something happened."""
    farm.rows.append(_row("2713"))

    def refuse(client, pid):
        raise phones_mod.PhoneError("[40001] busy")

    monkeypatch.setattr(phones_mod, "stop", refuse)
    outcome = forgotten.sweep(object(), farm.settings, farm.ledger,
                              [_on("2713")])

    assert outcome == {"off": [], "released": [], "held": []}
    assert farm.released == [] and farm.events == []


def test_a_phone_the_store_says_on_but_geelark_says_off_says_nothing(farm):
    """Somebody switched it off between the shadow and the sweep, or it is
    not in the listing at all: the next shadow clears the clock, and an
    event about nothing would be noise."""
    farm.rows.append(_row("2713", state="", owner="", on=4000))

    outcome = forgotten.sweep(object(), farm.settings, farm.ledger,
                              [_on("2713", phones_mod.STOPPED)])
    assert outcome == {"off": [], "released": [], "held": []}
    outcome = forgotten.sweep(object(), farm.settings, farm.ledger, [])
    assert outcome == {"off": [], "released": [], "held": []}
    assert farm.stopped == [] and farm.events == []


def test_the_sweep_is_off_at_zero_and_without_a_listing_or_a_store(
        farm, make_settings, tmp_path):
    farm.rows.append(_row("2713"))
    listing = [_on("2713")]

    assert forgotten.sweep(object(), farm.settings, farm.ledger, None) == {
        "off": [], "released": [], "held": []}
    off = make_settings(state_dir=tmp_path, store_enabled=True,
                        release_after_minutes=0)
    forgotten.sweep(object(), off, farm.ledger, listing)
    no_store = make_settings(state_dir=tmp_path, store_enabled=False,
                             release_after_minutes=60)
    forgotten.sweep(object(), no_store, farm.ledger, listing)

    assert farm.asked == [] and farm.stopped == [], (
        "nothing is read, let alone stopped")


def test_a_store_that_will_not_answer_costs_a_warning_not_the_pass(
        farm, monkeypatch):
    def down(settings, minutes):
        raise RuntimeError("store down")

    monkeypatch.setattr(forgotten, "overdue", down)
    assert forgotten.sweep(object(), farm.settings, farm.ledger,
                           [_on("2713")]) == {
        "off": [], "released": [], "held": []}


def test_a_ledger_that_is_none_holds_nothing(farm):
    farm.rows.append(_row("2713"))
    outcome = forgotten.sweep(object(), farm.settings, None, [_on("2713")])
    assert outcome["off"] == ["2713"]


def test_the_span_reads_like_a_person_would_say_it():
    assert forgotten._span(0) == "0 min"
    assert forgotten._span(3599) == "59 min"
    assert forgotten._span(3600) == "1 h 0 min"
    assert forgotten._span(4320.7) == "1 h 12 min"
    assert forgotten._span(None) == "0 min"


def test_what_the_store_is_asked_and_told():
    """The reads and the write, as SQL: a building phone is never read;
    the release puts the owner and the clock back with the state."""
    read = inspect.getsource(forgotten.overdue)
    assert "p.status <> 'building'" in read
    assert ("p.state = 'taken'"
            "         AND p.state_at < now() - %s * interval '1 minute'") in (
        read.replace('"\n            "', ""))
    assert "p.running AND p.running_since IS NOT NULL" in read
    write = inspect.getsource(forgotten._release)
    assert "SET state = '', owner_id = NULL," in write
    assert "state_at = now()" in write
    assert "AND state = 'taken'" in write, "put back only what is still taken"
    assert forgotten.ON == (phones_mod.RUNNING, phones_mod.STARTING)
