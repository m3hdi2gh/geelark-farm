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


# ------------------------------------------------------- more than one job
def test_five_accounts_are_finished_while_five_more_phones_are_built():
    """The shape the whole change exists for.

    Five accounts arrive against five warm phones: finish all five, and build
    five more at the same time so the stock is back where it was. One pass, ten
    jobs, instead of ten passes and about seventy minutes.
    """
    decision = decide(**numbers(warm=5, target=5, accounts_waiting=5,
                                free_slots=23, cap=10, gmails=15, exits=38))

    assert (decision.finish, decision.build) == (5, 5)
    assert decision.jobs == 10


def test_the_cap_is_the_most_a_pass_will_take_on():
    """It is what a person sets when the box or the rate limiter says stop."""
    decision = decide(**numbers(warm=5, target=5, accounts_waiting=5,
                                free_slots=23, cap=5, gmails=15, exits=38))

    # The five finishes use the whole cap, so nothing is built this pass. The
    # next pass builds, because those five are no longer warm.
    assert (decision.finish, decision.build) == (5, 0)


def test_it_never_asks_for_more_phones_than_there_is_stock_to_make():
    """Four builds against a two-address tab spends two claims and two live
    proxy checks to create nothing - and ends on `no_usable_gmail`, which the
    breaker ignores, so nothing anywhere counts it."""
    decision = decide(**numbers(warm=0, target=10, free_slots=20,
                                cap=10, gmails=2, exits=9))

    assert decision.build == 2

    decision = decide(**numbers(warm=0, target=10, free_slots=20,
                                cap=10, gmails=9, exits=2))

    assert decision.build == 2


def test_it_never_asks_for_more_phones_than_there_are_slots():
    decision = decide(**numbers(warm=0, target=10, free_slots=3,
                                cap=10, gmails=50, exits=50))

    assert decision.build == 3


def test_finishing_shrinks_the_warm_stock_it_is_topping_up():
    """A phone that gets finished stops being warm, so the hole to fill is
    measured after this pass's finishes rather than before them. Measured the
    other way, a full stock being wholly delivered would build nothing and the
    next pass would start from zero."""
    decision = decide(**numbers(warm=3, target=3, accounts_waiting=3,
                                free_slots=20, cap=6, gmails=50, exits=50))

    assert (decision.finish, decision.build) == (3, 3)


def test_a_pass_with_room_to_build_still_asks_about_slots():
    """The old shortcut said "finishing, so no new slot is needed" and left the
    count unread - which `decide` then refuses to build blind on. Every
    combined pass would have finished and then declined to build, for ever."""
    assert serve_mod.needs_slots(tripped="", warm=5, target=5,
                                 accounts_waiting=5, cap=10)
    # ...and still does not ask when the cap is spent on finishing.
    assert not serve_mod.needs_slots(tripped="", warm=5, target=5,
                                     accounts_waiting=5, cap=5)


def test_a_tripped_breaker_still_finishes_all_of_them():
    """Finishing spends nothing new, and five customers are waiting."""
    decision = decide(**numbers(warm=5, target=5, accounts_waiting=5, cap=10,
                                tripped="5 builds in a row failed"))

    assert decision.finish == 5 and not decision.build


# -------------------------------------------------- controls on the sheet
class Board:
    """A Service tab with boxes that can be ticked."""

    def __init__(self, *ticked):
        self.ticked = list(ticked)
        self.unticked = []
        self.shown = {}

    def asked(self):
        return list(self.ticked)

    def taken(self, name):
        self.unticked.append(name)

    def show(self, **kw):
        self.shown.update(kw)


def _with_board(monkeypatch, board, recorder):
    from geelark_farm import builder

    monkeypatch.setattr(serve_mod, "Book", SimpleNamespace(
        open=lambda s: SimpleNamespace(
            reload=lambda: None, apps=None,
            gmails=SimpleNamespace(available=["g"] * 20),
            proxies=SimpleNamespace(available=["p"] * 20),
            service=board)))
    monkeypatch.setattr(builder, "sync_sheet", lambda *a, **k: {})


def test_a_ticked_box_clears_the_breaker(monkeypatch, make_settings, tmp_path):
    """The sharpest hole there was: the breaker stops the half of the service
    that earns, it is shown on the tab the operator reads, and until now the
    person it waits for had no way at all to answer it."""
    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    recorder = Recorder(warm=3, free=10).install(monkeypatch)
    board = Board("Clear breaker")
    _with_board(monkeypatch, board, recorder)
    fuse = Fuse(tripped="5 builds in a row failed")

    serve_mod.once(object(), settings, fuse, serve_mod.Slots())

    assert fuse.cleared


def test_a_one_shot_control_is_unticked_as_soon_as_it_is_read(
        monkeypatch, make_settings, tmp_path):
    """Not after it is acted on. A pass can run for minutes, and a tick that
    lands while it works has to survive to the next pass rather than be wiped
    by a write that means "dealt with"."""
    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    Recorder(warm=3, free=10).install(monkeypatch)
    board = Board("Clear breaker")
    _with_board(monkeypatch, board, None)

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())

    assert board.unticked == ["Clear breaker"]


def test_a_standing_mode_is_left_ticked(monkeypatch, make_settings, tmp_path):
    """Unticking a pause would turn it into one skipped pass, which is not
    what anybody ticking it wants."""
    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    Recorder(warm=0, free=10).install(monkeypatch)
    board = Board("Pause building")
    _with_board(monkeypatch, board, None)

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())

    assert board.unticked == []


def test_pausing_stops_building_and_still_finishes():
    """A customer waiting on an account is not what a pause means to stop, and
    finishing spends nothing new."""
    decision = decide(**numbers(warm=3, target=10, accounts_waiting=1,
                                paused=True))

    assert decision.finish == 1 and not decision.build
    assert "Untick" in decision.warning


def test_a_paused_pass_never_spends_the_rate_limited_call():
    assert not serve_mod.needs_slots(tripped="", warm=0, target=5,
                                     accounts_waiting=0, paused=True)


def test_what_a_sync_could_not_finish_reaches_the_sheet():
    """Every one of these used to be discarded, so a step that was attempted
    and failed lived only in a log on a server nobody reads - and from the
    sheet it looked exactly like a pass where nothing went wrong."""
    said = serve_mod.needs_you({
        "incomplete": ["proxies"],
        "unknown_phones": ["901", "902"],
        "stranded_waiting": [{"row": 4}],
    })

    assert "proxies" in said
    assert "2 phone(s)" in said and "bill" in said
    assert "1 account(s)" in said


def test_a_quiet_sync_says_so_rather_than_nothing():
    assert serve_mod.needs_you({}) == ""


# ------------------------------------------------------------- the passes
class Recorder:
    """What the loop asked the rest of the code to do."""

    def __init__(self, warm=0, free=10, waiting=0, gmails=50, exits=50):
        # Four numbers, because `_look` returns four. The pool depths are
        # generous by default so a test about something else is never
        # accidentally constrained by them.
        self.numbers = (warm, waiting, gmails, exits)
        self.free = free
        self.synced = 0
        self.built = 0
        self.finished = 0
        #: The keyword arguments of the last `builder.run` call. One call does
        #: both jobs now, so "how many finishes and how many builds" is read
        #: from here rather than from two counters.
        self.asked = {}
        self.recorded = []

    def install(self, monkeypatch, *, fails=False):
        from geelark_farm import builder

        monkeypatch.setattr(serve_mod, "_look",
                            lambda c, s, b: self.numbers)
        monkeypatch.setattr(serve_mod.Slots, "look",
                            lambda self_, c, now: self.free)
        # `service` is None the way a real Book's is when the tab could not be
        # made - the shape has to match, or the fake tests a Book that does not
        # exist.
        monkeypatch.setattr(serve_mod, "Book",
                            SimpleNamespace(open=lambda s: SimpleNamespace(
                                reload=lambda: None, apps=None, service=None)))
        monkeypatch.setattr(serve_mod, "Ledger",
                            SimpleNamespace(load=lambda d: None))
        monkeypatch.setattr(builder, "sync_sheet",
                            lambda *a, **k: self.bump("synced") or {})
        monkeypatch.setattr(builder, "run", self._run(fails))
        monkeypatch.setattr(builder, "finish_run",
                            lambda *a, **k: self.bump("finished") or
                            [build(ok=True)])
        return self

    def _run(self, fails):
        def run(*a, **kw):
            self.asked = kw
            self.bump("built")
            jobs = kw.get("count", 1)
            return [build(ok=not fails) for _ in range(jobs)]
        return run

    def bump(self, name):
        setattr(self, name, getattr(self, name) + 1)


def build(ok=True, status=None):
    from geelark_farm.builder import Build
    return Build(index=1, ok=ok, status=status or ("ready" if ok else "error"))


class Fuse:
    def __init__(self, tripped=""):
        self.tripped = tripped
        self.seen = []
        self.cleared = False

    def reason(self):
        return self.tripped

    def record(self, build):
        self.seen.append(build)

    def clear(self):
        # The real Breaker has this and the fake did not, which is the drift
        # `scripts/audit_fakes.py` exists to catch.
        self.cleared = True
        self.tripped = ""


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


def test_a_build_pass_builds_rather_than_re_finishing_a_warm_phone(
        monkeypatch, make_settings, tmp_path):
    """The one that kept the warm stock at one, and hid until it was raised.

    `builder.run` finishes before it builds by default, which is what a person
    typing it wants. Called from a pass that had already decided to build, it
    called `_unfinished` and got back the very phones `_look` had just counted
    as warm, so `to_finish` took the count and `to_build` was left at 0 - the
    build became a finish. That finish then found no account to use, because a
    pass only reaches the build branch when none is waiting, and the phone went
    back to `incomplete`. Warm never passed 1 and a real cloud phone was booted
    and stopped every pass, with the resulting `no_usable_gpt` *clearing* the
    breaker each time (2026-08-28).

    Every other test of this branch stubs `builder.run` whole and asserts on
    `count`, which is exactly why it stayed green. This one goes through the
    real `run` and watches which job it dispatches.
    """
    from test_builder import FakeLedger, make_book

    from geelark_farm import builder

    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    book = make_book(apps=0)                    # nothing waiting to be finished
    warm = [{"sheet_row": 2, "phone_id": "P2", "serial": "662",
             "gmail": "a@example.com", "proxy": "", "status": "no_usable_gpt"}]

    monkeypatch.setattr(builder, "_unfinished", lambda c, b: (warm, []))
    monkeypatch.setattr(builder, "sync_sheet", lambda *a, **k: {})
    monkeypatch.setattr(builder.Book, "open", classmethod(lambda cls, s: book))
    monkeypatch.setattr(builder.Ledger, "load",
                        staticmethod(lambda p: FakeLedger()))
    monkeypatch.setattr(serve_mod.Slots, "look", lambda self_, c, now: 10)
    monkeypatch.setattr(builder.phones, "prune_ledger", lambda c, l: [])

    jobs = []
    monkeypatch.setattr(builder, "build_one", lambda *a, **k: jobs.append("build")
                        or builder.Build(index=1, ok=True, status="ready"))
    monkeypatch.setattr(builder, "finish_one", lambda *a, **k: jobs.append("finish")
                        or builder.Build(index=1, ok=True, status="ready"))

    client = SimpleNamespace(data=lambda *a, **kw: {})
    serve_mod.once(client, settings, Fuse(), serve_mod.Slots())

    assert jobs == ["build"], (
        "a build pass must build - finishing here re-cooks a warm phone that "
        "has no account waiting for it")


def board_book(monkeypatch, shown):
    """A Book whose Service tab records what it was asked to display.

    Nothing invented here. An earlier version of this fake gave `phones` a
    `CLAIM_FORMAT`, which `PhoneLog` does not have - the format belongs to
    `Pool`. The fake was the only place that attribute existed, so the tests
    were green and the server threw on every single pass (2026-08-28). A fake
    that carries what the real object lacks tests a program nobody is running.
    """
    monkeypatch.setattr(
        serve_mod, "Book",
        SimpleNamespace(open=lambda s: SimpleNamespace(
            reload=lambda: None, apps=None,
            gmails=SimpleNamespace(available=["g"] * 20),
            proxies=SimpleNamespace(available=["p"] * 20),
            service=SimpleNamespace(show=lambda **kw: shown.update(kw),
                                    asked=lambda: [],
                                    taken=lambda name: None))))


def test_a_pass_says_on_the_sheet_what_it_is_doing(monkeypatch, make_settings,
                                                   tmp_path):
    """The operator reads the spreadsheet and nothing else.

    All of this was in the log, on a server they do not read. From the sheet, a
    loop that had stopped building looked exactly like a loop that was
    correctly idle - a tab that had gone quiet (2026-08-28).
    """
    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    shown = {}
    Recorder(warm=1, free=10).install(monkeypatch)
    board_book(monkeypatch, shown)

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())

    assert "building 1" in shown["Doing"]
    assert shown["Warm stock"] == "1 of 3"
    assert shown["Free slots"] == "10"
    assert shown["Breaker"] == "closed"
    assert shown["Last pass"].endswith("Z"), "which clock, said outright"


def test_an_open_breaker_reaches_the_sheet_and_not_only_the_log(
        monkeypatch, make_settings, tmp_path):
    """It stops the loop building, and a log line saying so is no use to
    somebody who cannot read the log."""
    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    shown = {}
    Recorder(warm=1, free=10).install(monkeypatch)
    board_book(monkeypatch, shown)

    serve_mod.once(object(), settings, Fuse("5 builds in a row failed"),
                   serve_mod.Slots())

    assert "5 builds in a row failed" in shown["Breaker"]
    assert "5 builds in a row failed" in shown["Note"]
    assert shown["Doing"] == "nothing to do"


def test_a_pass_asks_for_its_jobs_to_run_at_once(monkeypatch, make_settings,
                                                 tmp_path):
    """Without `workers`, `_drive_jobs` falls back to `max_concurrent_phones`
    - and ten jobs would run one after another *inside one pass*: seventy
    minutes instead of fifteen, which is the opposite of the point."""
    settings = make_settings(state_dir=tmp_path, warm_stock=5,
                             max_concurrent_phones=10)
    recorder = Recorder(warm=5, free=20, waiting=5).install(monkeypatch)

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())

    assert recorder.asked.get("count") == 10
    assert recorder.asked.get("finish_limit") == 5
    assert recorder.asked.get("workers") == 10, (
        "the jobs have to be handed over as concurrent, not just as many")


def test_one_call_does_both_jobs(monkeypatch, make_settings, tmp_path):
    """Never two concurrent calls. `finish_run` and `run` each open their own
    Book, and a Pool's claim lock is per instance - two Books have two locks,
    and the serialisation that stops one Gmail reaching two phones stops
    holding."""
    settings = make_settings(state_dir=tmp_path, warm_stock=5,
                             max_concurrent_phones=10)
    recorder = Recorder(warm=5, free=20, waiting=5).install(monkeypatch)

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())

    assert recorder.built == 1, "one runner"
    assert recorder.finished == 0, "finish_run is not called separately"


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


def test_a_finish_says_exactly_how_many_of_the_jobs_are_finishes(
        monkeypatch, make_settings, tmp_path):
    """`count` alone would be read as "finish as many as you can".

    Finishing takes from `count` first, so a pass asking for one finish and
    two builds would get three finishes - and the two with no account to use
    would each boot a real phone, end `no_usable_gpt` and put it back, while
    that very reason cleared the breaker (2026-08-28).
    """
    settings = make_settings(state_dir=tmp_path, warm_stock=3)
    recorder = Recorder(warm=2, free=10, waiting=1).install(monkeypatch)

    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())

    assert recorder.asked.get("finish_limit") == 1, (
        "one account is waiting, so exactly one of these is a finish")
    assert recorder.asked.get("count") == 1, "and the cap is 1 by default"


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

    book = SimpleNamespace(
        apps=SimpleNamespace(available=["a", "b", "c"]),
        gmails=SimpleNamespace(available=["g", "h", "i", "j"]),
        proxies=SimpleNamespace(available=["p"]))
    monkeypatch.setattr(builder, "_unfinished",
                        lambda c, b: ([{"serial": "1"}, {"serial": "2"}], []))
    monkeypatch.setattr(serve_mod.phones, "plan",
                        lambda c: pytest.fail("the plan was read for nothing"))

    assert serve_mod._look(object(), settings, book) == (2, 3, 4, 1)


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


def test_a_loop_whose_every_pass_dies_is_not_healthy(make_settings, tmp_path):
    """The blind spot the heartbeat cannot see.

    `beat` is stamped before the pass is attempted, on purpose, so a pass that
    hangs still goes stale. That also means a pass that *throws* stamps it
    exactly as a working one does - so a service whose every pass died on the
    first call reported healthy forever, `restart: always` never fired, and the
    operator saw a quiet tab indistinguishable from a correctly idle loop
    (2026-08-28).
    """
    settings = make_settings(state_dir=tmp_path)
    serve_mod.beat(settings)
    for _ in range(serve_mod.FAILING_LIMIT):
        serve_mod.note_pass(settings, ok=False)

    ok, said = serve_mod.healthy(settings)

    assert not ok
    assert "in a row" in said and "nothing is getting through" in said


def test_one_pass_that_works_makes_it_healthy_again(make_settings, tmp_path):
    """Consecutive, like the breaker: the question is whether it has stopped
    working, and one pass getting through answers it."""
    settings = make_settings(state_dir=tmp_path)
    serve_mod.beat(settings)
    for _ in range(serve_mod.FAILING_LIMIT + 3):
        serve_mod.note_pass(settings, ok=False)

    serve_mod.note_pass(settings, ok=True)

    assert serve_mod.healthy(settings)[0]


def test_a_few_failures_are_not_yet_a_dead_service(make_settings, tmp_path):
    """One flaky call must not raise an alarm - but it is said out loud, so a
    person reading the line knows it is not a clean run."""
    settings = make_settings(state_dir=tmp_path)
    serve_mod.beat(settings)
    serve_mod.note_pass(settings, ok=False)

    ok, said = serve_mod.healthy(settings)

    assert ok
    assert "1 failed in a row" in said


def test_a_failing_pass_is_counted_by_the_loop_itself(monkeypatch,
                                                      make_settings, tmp_path):
    """The counter is worth nothing if `run` forgets to feed it."""
    settings = make_settings(state_dir=tmp_path, serve_interval_seconds=0)

    def explode(*a, **kw):
        raise RuntimeError("geelark went away")

    monkeypatch.setattr(serve_mod, "once", explode)
    monkeypatch.setattr(serve_mod, "build_client", lambda s: object())

    serve_mod.run(settings, passes=serve_mod.FAILING_LIMIT,
                  sleep=lambda s: None)

    assert not serve_mod.healthy(settings)[0]


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
