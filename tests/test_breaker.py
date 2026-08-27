"""The thing that stops an unwatched service burning the pool.

Run by hand, a bad build is somebody watching it fail. Run as a service with
`restart: always`, nothing is watching, and a broken deploy would keep taking
Gmails and proxies out of the pool and turning them into nothing for as long
as the pool lasts.
"""

from __future__ import annotations

from geelark_farm import breaker as breaker_mod
from geelark_farm.breaker import Breaker, counts_against, shows_it_works


def build(ok: bool, status: str = "error"):
    """A real Build, so the fields read here are the fields it has."""
    from geelark_farm.builder import Build
    return Build(index=1, ok=ok, status="ready" if ok else status)


# ------------------------------------------------- what counts as a failure
def test_a_warm_phone_waiting_for_an_account_is_not_a_failure():
    """`no_usable_gpt` is the whole point of the warm stock: a phone built to
    one step short of ready. Counting it would trip the breaker on the first
    quiet afternoon."""
    assert not counts_against(build(False, "no_usable_gpt"))


def test_an_empty_pool_is_not_a_failure_either():
    """The verdicts say it themselves - no phone was created and nothing was
    spent - so there is nothing burning for a breaker to stop."""
    assert not counts_against(build(False, "no_usable_gmail"))
    assert not counts_against(build(False, "no_usable_proxy"))


def test_a_machine_that_cannot_reach_the_network_does_count():
    """Its blame is `nobody`, which is not the same as nothing being wrong:
    a loop that keeps trying through it is what this exists to stop."""
    assert counts_against(build(False, "network_unreachable"))
    assert counts_against(build(False, "all_exits_refused"))


def test_a_phone_that_worked_never_counts():
    assert not counts_against(build(True))
    assert shows_it_works(build(True))


def test_an_empty_pool_is_neither_evidence_for_nor_against():
    assert not counts_against(build(False, "no_usable_gmail"))
    assert not shows_it_works(build(False, "no_usable_gmail"))


def test_every_expected_outcome_is_a_reason_the_code_can_write():
    """A renamed reason would quietly stop being excluded, and the breaker
    would start tripping on the loop doing its job."""
    from geelark_farm import failures

    known = set(failures.VERDICTS) | set(failures.SITUATIONS)
    named = breaker_mod.WORKED | breaker_mod.NOTHING_HAPPENED

    assert named <= known, named - known


# --------------------------------------------------------------- the count
def test_it_opens_only_after_enough_in_a_row(tmp_path):
    fuse = Breaker(tmp_path / "breaker.json", limit=3)

    for _ in range(2):
        fuse.record(build(False, "phone_never_started"))
    assert fuse.reason() == ""

    fuse.record(build(False, "phone_never_started"))
    assert "3 builds in a row failed" in fuse.reason()


def test_one_phone_that_worked_puts_the_count_back(tmp_path):
    """Consecutive, not a rate: a rate needs a window and a window needs
    tuning, and the question is whether it has stopped working."""
    fuse = Breaker(tmp_path / "breaker.json", limit=3)

    fuse.record(build(False, "phone_never_started"))
    fuse.record(build(False, "phone_never_started"))
    fuse.record(build(True))
    fuse.record(build(False, "phone_never_started"))

    assert fuse.reason() == ""


def test_a_warm_phone_is_the_pipeline_working_and_clears_the_count(tmp_path):
    """It built a phone, signed it into Google and installed the app. That
    the account had not arrived yet says nothing against the machine."""
    fuse = Breaker(tmp_path / "breaker.json", limit=2)
    fuse.record(build(False, "phone_never_started"))
    fuse.record(build(False, "no_usable_gpt"))
    fuse.record(build(False, "phone_never_started"))

    assert fuse.reason() == ""


def test_an_empty_pool_leaves_the_count_exactly_where_it_was(tmp_path):
    """Nothing was created and nothing was spent, so nothing is known either
    way - and a breaker silently defused by an empty pool between two real
    failures is worse than one that never tripped."""
    fuse = Breaker(tmp_path / "breaker.json", limit=2)
    fuse.record(build(False, "phone_never_started"))
    fuse.record(build(False, "no_usable_gmail"))
    fuse.record(build(False, "phone_never_started"))

    assert "2 builds in a row failed" in fuse.reason()


def test_it_survives_the_restart_it_exists_to_make_pointless(tmp_path):
    """`restart: always` means the process that trips it is not the process
    that has to still know a second later. A counter in memory would be
    cleared by the very restart this is here to stop being useless."""
    path = tmp_path / "breaker.json"
    first = Breaker(path, limit=2)
    first.record(build(False, "phone_never_started"))
    first.record(build(False, "phone_never_started"))

    assert "2 builds in a row failed" in Breaker(path, limit=2).reason()


def test_it_says_what_it_saw_and_not_only_that_it_tripped(tmp_path):
    """Whoever finds the service idle needs to know what stopped it."""
    fuse = Breaker(tmp_path / "breaker.json", limit=2)
    fuse.record(build(False, "proxy_blocked"))
    fuse.record(build(False, "phone_never_started"))

    said = fuse.reason()

    assert "proxy_blocked" in said and "phone_never_started" in said


def test_it_only_reopens_because_somebody_decided_to(tmp_path):
    """Nothing here reopens on a timer: a breaker that closes itself is a
    delay, and what tripped it is still true."""
    path = tmp_path / "breaker.json"
    fuse = Breaker(path, limit=2)
    fuse.record(build(False, "phone_never_started"))
    fuse.record(build(False, "phone_never_started"))
    assert fuse.reason()

    fuse.clear()

    assert fuse.reason() == ""
    assert Breaker(path, limit=2).reason() == ""


# ------------------------------------------------------- when it cannot write
def test_a_machine_that_cannot_write_still_builds(tmp_path, caplog):
    """It loses the breaker, which is worth a loud line and not a dead
    service."""
    fuse = Breaker(tmp_path / "nowhere" / "x" / "breaker.json", limit=2)
    fuse.path.parent.mkdir(parents=True)
    fuse.path.mkdir()                    # a directory where the file goes

    fuse.record(build(False, "phone_never_started"))

    assert fuse.reason() == ""
    assert any("cannot trip" in r.getMessage() for r in caplog.records)


def test_an_unreadable_file_is_not_held_against_the_machine(tmp_path):
    """Nothing is known against it, which is what an absent file means too."""
    path = tmp_path / "breaker.json"
    path.write_text("{ this is not json", encoding="utf-8")

    assert Breaker(path).reason() == ""


def test_clearing_it_actually_starts_from_zero(tmp_path):
    """Half-clearing looks identical until the next failure, which would then
    trip a breaker somebody had just decided to carry on past."""
    fuse = Breaker(tmp_path / "breaker.json", limit=2)
    fuse.record(build(False, "phone_never_started"))
    fuse.record(build(False, "phone_never_started"))
    fuse.clear()

    fuse.record(build(False, "phone_never_started"))

    assert fuse.reason() == ""


def test_the_first_run_on_a_fresh_machine_can_write_its_file(tmp_path):
    """`state/` does not exist until something makes it, and the first thing
    to want it may well be this."""
    fuse = Breaker(tmp_path / "state" / "sub" / "breaker.json", limit=1)

    fuse.record(build(False, "phone_never_started"))

    assert "1 builds in a row failed" in fuse.reason()
