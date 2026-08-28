"""The loop that replaces somebody typing the commands.

Most of this is about `decide`, which is a pure function of five numbers and
is where every judgement the service makes actually lives. It is worth being
able to argue with that without a network, a sheet or a clock in the way -
each of the three things it is careful about costs money to get wrong.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from geelark_farm import serve as serve_mod
from geelark_farm.serve import decide


def numbers(**kw):
    base = dict(tripped="", warm=10, target=10, free_slots=5,
                accounts_waiting=0)
    base.update(kw)
    return base


# --------------------------------------------------------------- deciding
def test_a_waiting_account_is_finished_before_anything_is_topped_up():
    """Both want the same pass; only one has somebody waiting at the end."""
    decision = decide(**numbers(warm=3, target=10, accounts_waiting=1))

    assert decision.finish and not decision.build


def test_finishing_still_happens_with_the_breaker_open():
    """It spends nothing new - the phone, the Gmail and the exit are already
    bought - and a customer waiting on an account is the one thing that
    should still happen while somebody works out why building stopped."""
    decision = decide(**numbers(warm=3, accounts_waiting=1,
                                tripped="5 builds in a row failed"))

    assert decision.finish


def test_a_tripped_breaker_stops_building():
    decision = decide(**numbers(warm=0, target=10,
                                tripped="5 builds in a row failed"))

    assert not decision.build
    assert "5 builds in a row failed" in decision.warning


def test_a_full_stock_does_nothing_and_says_nothing():
    decision = decide(**numbers(warm=10, target=10))

    assert decision.idle and not decision.warning


def test_a_short_stock_builds_one():
    decision = decide(**numbers(warm=9, target=10))

    assert decision.build and not decision.finish


def test_no_slots_is_said_in_words_rather_than_found_at_44002():
    """The fix is a person marking rows done, and nothing here can do it -
    so it has to be a sentence somebody reads, not an API error."""
    decision = decide(**numbers(warm=4, target=10, free_slots=0))

    assert not decision.build
    assert "no free profile slots" in decision.warning
    assert "State column" in decision.warning


def test_an_account_with_no_warm_phone_to_put_it_on_builds_instead():
    """The case the warm stock exists to prevent. It must not sit and wait
    for a phone that nothing is building."""
    decision = decide(**numbers(warm=0, target=10, accounts_waiting=1))

    assert decision.build and not decision.finish


def test_it_never_finishes_a_phone_that_is_not_there():
    """`finish_run` with nothing waiting is a wasted sheet read at best."""
    decision = decide(**numbers(warm=0, target=0, accounts_waiting=3))

    assert not decision.finish


# ------------------------------------------------------------- the passes
class Recorder:
    """What the loop asked the rest of the code to do."""

    def __init__(self, warm=0, free=10, waiting=0):
        self.numbers = (warm, waiting)
        self.free = free
        self.synced = 0
        self.built = 0
        self.finished = 0
        self.recorded = []

    def install(self, monkeypatch, *, fails=False):
        from geelark_farm import builder

        monkeypatch.setattr(serve_mod, "_look",
                            lambda c, s, b: self.numbers)
        monkeypatch.setattr(serve_mod.Slots, "look",
                            lambda self_, c, now: self.free)
        monkeypatch.setattr(serve_mod, "Book",
                            SimpleNamespace(open=lambda s: SimpleNamespace(
                                reload=lambda: None, apps=None)))
        monkeypatch.setattr(serve_mod, "Ledger",
                            SimpleNamespace(load=lambda d: None))
        monkeypatch.setattr(builder, "sync_sheet",
                            lambda *a, **k: self.bump("synced") or {})
        monkeypatch.setattr(builder, "run",
                            lambda *a, **k: self.bump("built") or
                            [build(ok=not fails)])
        monkeypatch.setattr(builder, "finish_run",
                            lambda *a, **k: self.bump("finished") or
                            [build(ok=True)])
        return self

    def bump(self, name):
        setattr(self, name, getattr(self, name) + 1)


def build(ok=True, status=None):
    from geelark_farm.builder import Build
    return Build(index=1, ok=ok, status=status or ("ready" if ok else "error"))


class Fuse:
    def __init__(self, tripped=""):
        self.tripped = tripped
        self.seen = []

    def reason(self):
        return self.tripped

    def record(self, build):
        self.seen.append(build)


def test_a_pass_syncs_before_it_decides(monkeypatch, settings):
    """The sync is what carries out the State column, so a phone somebody
    marked done is deleted and its slot is back before this counts them."""
    recorder = Recorder(warm=0, free=10).install(monkeypatch)

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())

    assert recorder.synced == 1


def test_a_pass_builds_when_the_stock_is_short(monkeypatch, make_settings,
                                                tmp_path):
    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    recorder = Recorder(warm=1, free=10).install(monkeypatch)

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())

    assert recorder.built == 1 and recorder.finished == 0


def test_every_build_it_starts_is_shown_to_the_breaker(monkeypatch,
                                                        make_settings,
                                                        tmp_path):
    """Otherwise the count never moves and the breaker is decoration."""
    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    Recorder(warm=1, free=10).install(monkeypatch, fails=True)
    fuse = Fuse()

    serve_mod.once(object(), settings, fuse, serve_mod.Slots())

    assert len(fuse.seen) == 1 and not fuse.seen[0].ok


def test_a_pass_that_dies_does_not_take_the_service_with_it(
        monkeypatch, make_settings, tmp_path, caplog):
    """The next pass begins by syncing the sheet, which is also how it
    recovers from whatever this one left half-done."""
    settings = make_settings(state_dir=tmp_path, serve_interval_seconds=0)

    def explode(*a, **kw):
        raise RuntimeError("geelark went away")

    monkeypatch.setattr(serve_mod, "once", explode)
    monkeypatch.setattr(serve_mod, "build_client", lambda s: object())

    assert serve_mod.run(settings, passes=2, sleep=lambda s: None) == 0
    assert sum("a pass failed" in r.getMessage() for r in caplog.records) == 2


def test_being_stopped_ends_the_loop_rather_than_the_pass(
        monkeypatch, make_settings, tmp_path):
    """SIGTERM arrives as KeyboardInterrupt, and the cleanup it reaches is
    inside the build - so it must not be swallowed as "a pass failed"."""
    settings = make_settings(state_dir=tmp_path)

    def interrupted(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(serve_mod, "once", interrupted)
    monkeypatch.setattr(serve_mod, "build_client", lambda s: object())

    with pytest.raises(KeyboardInterrupt):
        serve_mod.run(settings, passes=1, sleep=lambda s: None)


def test_a_stop_event_ends_it_between_passes(monkeypatch, make_settings,
                                              tmp_path):
    settings = make_settings(state_dir=tmp_path)
    stop = threading.Event()
    passes = []

    def counted(*a, **kw):
        passes.append(1)
        stop.set()

    monkeypatch.setattr(serve_mod, "once", counted)
    monkeypatch.setattr(serve_mod, "build_client", lambda s: object())

    serve_mod.run(settings, stop=stop, sleep=lambda s: None)

    assert len(passes) == 1


def test_it_does_not_sleep_after_its_last_pass(monkeypatch, make_settings,
                                                tmp_path):
    """`--once` should return, not wait out an interval it has no use for."""
    settings = make_settings(state_dir=tmp_path, serve_interval_seconds=999)
    slept = []

    monkeypatch.setattr(serve_mod, "once", lambda *a, **kw: None)
    monkeypatch.setattr(serve_mod, "build_client", lambda s: object())

    serve_mod.run(settings, passes=1, sleep=slept.append)

    assert slept == []


def test_a_pass_that_is_doing_something_is_not_idle():
    assert not decide(**numbers(warm=1, target=10)).idle
    assert not decide(**numbers(warm=1, accounts_waiting=1)).idle


def test_one_free_slot_is_enough_to_build_in():
    """Refusing to use the last one wastes it: nothing else is going to come
    along and use it better."""
    assert decide(**numbers(warm=1, target=10, free_slots=1)).build


def test_a_stock_that_is_not_keeping_up_is_said_out_loud(caplog):
    """An account arriving with no warm phone for it is the case the stock
    exists to prevent, so seeing it is the signal that it is behind."""
    import logging

    with caplog.at_level(logging.INFO, logger="geelark_farm.serve"):
        decide(**numbers(warm=0, target=10, accounts_waiting=1))

    assert any("no warm phone is ready" in r.getMessage()
               for r in caplog.records)


def test_a_stock_that_is_keeping_up_says_nothing(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="geelark_farm.serve"):
        decide(**numbers(warm=4, target=10, accounts_waiting=1))

    assert not any("no warm phone is ready" in r.getMessage()
                   for r in caplog.records)


def test_a_pass_builds_one_phone_and_finishes_one(monkeypatch, make_settings,
                                                   tmp_path):
    """One per pass is the pacing of the whole service. Two would double the
    rate the pools drain at without anything saying so."""
    from geelark_farm import builder

    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    asked = {}
    Recorder(warm=1, free=10).install(monkeypatch)
    monkeypatch.setattr(builder, "run",
                        lambda c, s, **kw: asked.update(kw) or [build()])

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())

    assert asked.get("count") == 1


def test_a_finish_takes_one_phone_at_a_time_too(monkeypatch, make_settings,
                                                 tmp_path):
    from geelark_farm import builder

    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    asked = {}
    Recorder(warm=2, free=10, waiting=1).install(monkeypatch)
    monkeypatch.setattr(builder, "finish_run",
                        lambda c, s, **kw: asked.update(kw) or [build()])

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())

    assert asked.get("limit") == 1


def test_starting_it_already_stopped_does_nothing_at_all(monkeypatch,
                                                          make_settings,
                                                          tmp_path):
    """A stop that arrived before the first pass has to be read before the
    first pass, not after it."""
    settings = make_settings(state_dir=tmp_path)
    stop = threading.Event()
    stop.set()
    passes = []

    monkeypatch.setattr(serve_mod, "once",
                        lambda *a, **kw: passes.append(1))
    monkeypatch.setattr(serve_mod, "build_client", lambda s: object())

    serve_mod.run(settings, stop=stop, sleep=lambda s: None)

    assert passes == []


def test_the_numbers_it_decides_from_come_from_the_panel_and_the_sheet(
        monkeypatch, settings):
    """`decide` is only as good as these, and nothing else checks that they
    are read off the right things.

    The free slots are not among them any more: they cost a call to an
    endpoint that allows one a minute, and most passes never look at them.
    """
    from geelark_farm import builder

    book = SimpleNamespace(apps=SimpleNamespace(available=["a", "b", "c"]))
    monkeypatch.setattr(builder, "_unfinished",
                        lambda c, b: ([{"serial": "1"}, {"serial": "2"}], []))
    monkeypatch.setattr(serve_mod.phones, "plan",
                        lambda c: pytest.fail("the plan was read for nothing"))

    assert serve_mod._look(object(), settings, book) == (2, 3)


# ----------------------------------------------------- saying it is still alive
def test_a_pass_says_it_began(make_settings, tmp_path):
    """`restart: always` brings back a process that died. It does nothing for
    one that is alive and stuck, and from outside those look identical."""
    settings = make_settings(state_dir=tmp_path)

    serve_mod.beat(settings)

    assert (tmp_path / serve_mod.HEARTBEAT_FILE).read_text(encoding="utf-8")


def test_a_service_that_cannot_write_its_heartbeat_still_serves(make_settings,
                                                                 tmp_path,
                                                                 caplog):
    """It should look unhealthy, not stop."""
    settings = make_settings(state_dir=tmp_path / "beat")
    (tmp_path / "beat").mkdir()
    (tmp_path / "beat" / serve_mod.HEARTBEAT_FILE).mkdir()

    serve_mod.beat(settings)                       # must not raise

    assert any("heartbeat" in r.getMessage() for r in caplog.records)


def test_nothing_has_run_yet_is_not_healthy(make_settings, tmp_path):
    """True on a container that has only just come up, which is what
    `start_period` in the healthcheck is for."""
    ok, said = serve_mod.healthy(make_settings(state_dir=tmp_path))

    assert not ok and "no pass has run yet" in said


def test_a_pass_a_moment_ago_is_healthy(make_settings, tmp_path):
    settings = make_settings(state_dir=tmp_path)
    serve_mod.beat(settings)

    ok, _said = serve_mod.healthy(settings)

    assert ok


def test_a_pass_from_long_enough_ago_is_not(make_settings, tmp_path):
    import time

    settings = make_settings(state_dir=tmp_path)
    serve_mod.beat(settings)
    past = time.time() + serve_mod.stale_after(settings) + 1

    ok, said = serve_mod.healthy(settings, now=past)

    assert not ok and "past the" in said


def test_a_build_running_long_is_not_mistaken_for_a_stuck_one(make_settings,
                                                               tmp_path):
    """A pass that is building legitimately takes as long as a build is
    allowed to. Calling that unhealthy would restart a working build."""
    import time

    settings = make_settings(state_dir=tmp_path, build_budget_seconds=3600,
                             serve_interval_seconds=30)
    serve_mod.beat(settings)

    ok, _said = serve_mod.healthy(settings, now=time.time() + 3600)

    assert ok


def test_a_heartbeat_that_is_not_a_number_is_not_healthy(make_settings,
                                                          tmp_path):
    """A half-written file after a hard kill is not evidence of a live pass."""
    settings = make_settings(state_dir=tmp_path)
    (tmp_path / serve_mod.HEARTBEAT_FILE).write_text("", encoding="utf-8")

    ok, _said = serve_mod.healthy(settings)

    assert not ok


def test_the_loop_beats_before_every_pass(monkeypatch, make_settings,
                                           tmp_path):
    """Before, not after: a pass that never returns is exactly the case this
    exists to catch, and it would never reach an `after`."""
    settings = make_settings(state_dir=tmp_path)
    beats = []
    monkeypatch.setattr(serve_mod, "beat", lambda s: beats.append(1))
    monkeypatch.setattr(serve_mod, "build_client", lambda s: object())
    monkeypatch.setattr(serve_mod, "once", lambda *a, **kw: None)

    serve_mod.run(settings, passes=3, sleep=lambda s: None)

    assert len(beats) == 3


# ------------------------------------------- how often the slots are counted
def a_plan(free):
    return lambda c: {"availableProfiles": free}


def test_the_slots_are_read_once_and_then_remembered(monkeypatch):
    """`/v1/pay/plan/info` allows one call a minute on a budget of its own.
    The loop asked every pass, so every other pass raised [40007] and died -
    and a pass that dies loses the sync with it (2026-08-28)."""
    reads = []
    monkeypatch.setattr(serve_mod.phones, "plan",
                        lambda c: reads.append(1) or {"availableProfiles": 7})
    slots = serve_mod.Slots(every=300)

    assert [slots.look(None, now) for now in (1000, 1030, 1060, 1290)] \
        == [7, 7, 7, 7]
    assert len(reads) == 1


def test_it_asks_again_once_the_interval_is_up(monkeypatch):
    answers = iter([{"availableProfiles": 7}, {"availableProfiles": 2}])
    monkeypatch.setattr(serve_mod.phones, "plan", lambda c: next(answers))
    slots = serve_mod.Slots(every=300)

    assert slots.look(None, 1000) == 7
    assert slots.look(None, 1301) == 2


def test_a_read_that_fails_keeps_the_last_answer(monkeypatch, caplog):
    """A number that is five minutes old is a better basis than no number,
    and far better than a pass that dies for want of one."""
    monkeypatch.setattr(serve_mod.phones, "plan", a_plan(7))
    slots = serve_mod.Slots(every=300)
    slots.look(None, 1000)

    def broken(c):
        raise RuntimeError("[40007] too many requests")

    monkeypatch.setattr(serve_mod.phones, "plan", broken)

    assert slots.look(None, 1400) == 7
    assert any("could not read how many slots" in r.getMessage()
               for r in caplog.records)


def test_a_first_read_that_fails_answers_nothing_rather_than_a_number(
        monkeypatch):
    """Zero would read as "no slots" and stop building; any other number
    would be invented. None is the truth, and `decide` knows what to do."""
    def broken(c):
        raise RuntimeError("[40007] too many requests")

    monkeypatch.setattr(serve_mod.phones, "plan", broken)

    assert serve_mod.Slots().look(None, 1000) is None


def test_a_failed_read_waits_its_turn_before_asking_again(monkeypatch):
    """Retrying next pass is asking too often, which is the whole problem."""
    reads = []

    def broken(c):
        reads.append(1)
        raise RuntimeError("[40007] too many requests")

    monkeypatch.setattr(serve_mod.phones, "plan", broken)
    slots = serve_mod.Slots(every=300)

    slots.look(None, 1000)
    slots.look(None, 1030)
    slots.look(None, 1060)

    assert len(reads) == 1


# ------------------------------------------------ and when they are asked at all
@pytest.mark.parametrize("numbers,wanted", [
    (dict(tripped="", warm=1, target=1, accounts_waiting=0), False),
    (dict(tripped="", warm=0, target=1, accounts_waiting=0), True),
    (dict(tripped="", warm=1, target=5, accounts_waiting=1), False),
    (dict(tripped="5 failed", warm=0, target=5, accounts_waiting=0), False),
    (dict(tripped="", warm=0, target=5, accounts_waiting=1), True),
])
def test_the_slots_are_asked_for_only_when_the_answer_changes_anything(
        numbers, wanted):
    """A full stock, a waiting account and an open breaker each settle the
    pass without ever looking - and looking costs a rate-limited call."""
    assert serve_mod.needs_slots(**numbers) is wanted


def test_a_pass_that_is_not_building_never_touches_the_plan(monkeypatch,
                                                             make_settings,
                                                             tmp_path):
    """The common case: the stock is full and there is nothing to do."""
    settings = make_settings(state_dir=tmp_path, warm_stock=1)
    Recorder(warm=1, waiting=0).install(monkeypatch)
    monkeypatch.setattr(serve_mod.Slots, "look",
                        lambda *a: pytest.fail("read the plan for nothing"))

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())


def test_building_blind_is_refused_when_the_count_is_unknown():
    """[44002] at phone creation is what building without knowing looks like,
    and by then the reads that got there are spent."""
    decision = decide(**numbers(warm=0, target=5, free_slots=None))

    assert not decision.build
    assert "could not be read" in decision.warning


# ------------------------------------------------------ re-testing the exits
# Restored after a careless edit removed them, which the mutation run found by
# reporting survivors on lines that had had killers the day before.
def test_a_service_that_has_just_come_back_tests_the_exits():
    """The one case where they may well have changed while nothing was
    watching."""
    assert serve_mod.probe_due(None, now=10.0)


def test_they_are_not_tested_again_thirty_seconds_later():
    """34 of the 43 calls a pass made were one live connection per exit, to
    answer a question whose answer changes on the scale of days."""
    assert not serve_mod.probe_due(1000.0, now=1030.0, every=3600)


def test_they_are_tested_again_once_the_hour_is_up():
    assert serve_mod.probe_due(1000.0, now=4600.0, every=3600)


def test_the_loop_tests_them_on_the_first_pass_and_not_the_second(
        monkeypatch, make_settings, tmp_path):
    settings = make_settings(state_dir=tmp_path)
    asked = []
    monkeypatch.setattr(serve_mod, "build_client", lambda s: object())
    monkeypatch.setattr(serve_mod, "once",
                        lambda *a, probe_proxies=True, **kw:
                        asked.append(probe_proxies))

    serve_mod.run(settings, passes=3, sleep=lambda s: None)

    assert asked == [True, False, False]


def test_a_pass_hands_the_answer_on_to_the_sync(monkeypatch, settings):
    """It is `sync_sheet` that does the probing, so the decision has to reach
    it or it is a decision about nothing."""
    from geelark_farm import builder

    asked = {}
    Recorder(warm=0, free=10).install(monkeypatch)
    monkeypatch.setattr(builder, "sync_sheet",
                        lambda *a, **k: asked.update(k) or {})

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots(),
                   probe_proxies=False)

    assert asked.get("probe_proxies") is False


def test_an_empty_stock_with_nobody_waiting_is_a_cold_start_not_a_warning(
        caplog):
    """The line means "an account turned up and there was nothing for it".
    An empty stock on its own is what the first pass after a deploy looks
    like, and saying it then teaches whoever reads the log to ignore it."""
    import logging

    with caplog.at_level(logging.INFO, logger="geelark_farm.serve"):
        decide(**numbers(warm=0, target=10, accounts_waiting=0))

    assert not any("no warm phone is ready" in r.getMessage()
                   for r in caplog.records)


def test_a_plan_that_reports_no_number_is_read_as_no_slots(monkeypatch):
    """GeeLark sends the key as null on some plans, and `.get` does not apply
    a default to a key that is present - the same trap the phone listing hit.
    One free slot invented out of a null is one blind build."""
    monkeypatch.setattr(serve_mod.phones, "plan",
                        lambda c: {"availableProfiles": None})

    assert serve_mod.Slots().look(None, 1000) == 0


def test_the_first_heartbeat_can_make_the_directory_it_lives_in(make_settings,
                                                                tmp_path):
    """`state/` does not exist until something makes it, and on a fresh
    deployment the first thing to want it is this."""
    settings = make_settings(state_dir=tmp_path / "state" / "sub")

    serve_mod.beat(settings)

    assert (settings.state_dir / serve_mod.HEARTBEAT_FILE).read_text(
        encoding="utf-8")
