"""The rate gate: when Google refuses nearly everything, probe - do not
pour (2026-09-14)."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

from geelark_farm import signin_gate
from geelark_farm.serve import Decision


def _wire(monkeypatch, latest, state=None):
    """The store, faked: the newest sign-ins and the gate's own memory."""
    kept = dict(state or {})
    monkeypatch.setattr(signin_gate, "_latest", lambda s, n: list(latest)[:n])
    monkeypatch.setattr(signin_gate, "_state", lambda s: dict(kept))
    monkeypatch.setattr(signin_gate, "_remember",
                        lambda s, st: kept.clear() or kept.update(st))
    return kept


ON = SimpleNamespace(store_enabled=True)


def test_the_gate_closes_on_a_window_that_let_almost_nothing_in():
    """Twelve read, one or none in: closed. Fewer than twelve: no verdict
    - a farm that has just started must not be judged on three rows."""
    assert signin_gate.judge([False] * 12, was_closed=False) == (True, 0, 12)
    assert signin_gate.judge([True] + [False] * 11, was_closed=False)[0]
    assert not signin_gate.judge([True, True] + [False] * 10,
                                 was_closed=False)[0]
    assert not signin_gate.judge([False] * 11, was_closed=False)[0]


def test_a_closed_gate_opens_on_two_of_the_last_four():
    """One lucky probe is not a reopening; a good pair is, whatever the
    older ten say."""
    old = [False] * 8
    assert signin_gate.judge([True, False, False, False] + old,
                             was_closed=True)[0], "one in four stays shut"
    assert not signin_gate.judge([True, False, True, False] + old,
                                 was_closed=True)[0]
    assert not signin_gate.judge([False, True, True, False] + old,
                                 was_closed=True)[0]


def test_while_closed_the_warm_builds_become_two_probes_every_quarter_hour(
        monkeypatch):
    """Ten warm builds a pass into an hour that lets none in burned ninety
    addresses. Closed, the keeper sends two, then holds for fifteen
    minutes, then two again - it never stops for long, it stops paying
    full price."""
    kept = _wire(monkeypatch, [False] * 12)
    now = 1_000_000.0

    cut, gate = signin_gate.throttle(ON, Decision(build=10), now=now)
    assert gate.closed and (gate.ok, gate.of) == (0, 12)
    assert cut.build == 2, "the first probe goes at once"
    assert kept["closed"] and kept["last_probe_at"] == now
    assert kept["since"] == now

    cut, gate = signin_gate.throttle(ON, Decision(build=10), now=now + 300)
    assert cut.build == 0, "inside the quarter hour: held"
    assert gate.next_probe_in == signin_gate.PROBE_EVERY_SECONDS - 300
    assert kept["last_probe_at"] == now, "a held pass is not a probe"

    cut, gate = signin_gate.throttle(
        ON, Decision(build=10), now=now + signin_gate.PROBE_EVERY_SECONDS)
    assert cut.build == 2 and kept["last_probe_at"] == now + 900
    assert gate.since == now, "when it closed, kept across passes"

    # Finishing is not building: a decision with no warm builds is
    # returned as it is, and the finish count is never touched.
    cut, gate = signin_gate.throttle(ON, Decision(finish=3), now=now + 1000)
    assert cut.finish == 3 and cut.build == 0 and gate.closed


def test_the_gate_opens_by_itself_and_forgets_its_clock(monkeypatch):
    kept = _wire(monkeypatch, [True, False, True, False] + [False] * 8,
                 state={"closed": True, "since": 5.0, "last_probe_at": 9.0})
    cut, gate = signin_gate.throttle(ON, Decision(build=10), now=100.0)
    assert not gate.closed and cut.build == 10
    assert kept == {"closed": False}


def test_a_store_that_will_not_answer_means_no_gate(monkeypatch):
    """Never load-bearing: the keeper builds as it always did."""
    monkeypatch.setattr(signin_gate, "_latest",
                        lambda s, n: (_ for _ in ()).throw(RuntimeError("down")))
    cut, gate = signin_gate.throttle(ON, Decision(build=4))
    assert cut.build == 4 and not gate.closed
    off = SimpleNamespace(store_enabled=False)
    cut, gate = signin_gate.throttle(off, Decision(build=4))
    assert cut.build == 4 and not gate.closed


def test_the_pass_throttles_after_deciding_and_before_ordering():
    """Wired between the arithmetic and the order, and the wishes are
    ordered from their own list, untouched."""
    from geelark_farm import serve

    source = inspect.getsource(serve)
    decided = source.index("decision = decide(tripped=tripped")
    throttled = source.index("signin_gate.throttle(settings, decision)")
    ordered = source.index('"gate": sign_in_gate.as_dict()')
    assert decided < throttled < ordered
    assert "for want in wishes or []:" in inspect.getsource(serve._order)


def test_the_dashboard_says_probing_and_when_the_next_probe_goes():
    from geelark_farm.web import pages

    word, colour = pages._keeper_words({
        "warm": 0, "target": 10,
        "gate": {"closed": True, "ok": 0, "of": 12, "next_probe_in": 420}})
    assert word.startswith("Probing — Google let in 0 of the last 12")
    assert "next probe in 7 min" in word and colour == "amber"
    word, _ = pages._keeper_words({
        "warm": 0, "target": 10,
        "gate": {"closed": True, "ok": 1, "of": 12, "next_probe_in": 0}})
    assert "a probe is going out now" in word
    word, _ = pages._keeper_words({"warm": 0, "target": 10,
                                   "gate": {"closed": False}})
    assert word.startswith("Building")
