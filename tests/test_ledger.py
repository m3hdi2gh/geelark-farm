"""Ledger and proxy parsing.

Both are pure logic whose failure costs money rather than raising: a ledger that
forgets a phone leaves it billing unnoticed, and a proxy string that parses
wrongly creates a phone on the wrong network - which burns the account, not just
the minutes.
"""

from __future__ import annotations

import json
import logging
import pathlib
import time

import pytest

from geelark_farm import ledger as ledger_mod
from geelark_farm.ledger import Ledger
from geelark_farm.proxy import Proxy, ProxyError, parse


# ------------------------------------------------------------------- ledger
def test_a_recorded_phone_survives_a_reload(tmp_path):
    """The whole point: a crash after creation must not lose the phone."""
    first = Ledger.load(tmp_path)
    first.record("PHONE1", serial="435", label="row 4", proxy="1.2.3.4:1080")

    reloaded = Ledger.load(tmp_path)
    entry = reloaded.get("PHONE1")
    assert entry is not None
    assert entry.label == "row 4"
    assert entry.proxy == "1.2.3.4:1080"


def test_claim_and_release_track_who_is_responsible(tmp_path):
    led = Ledger.load(tmp_path)
    led.record("PHONE1")
    assert not led.get("PHONE1").is_claimed

    led.claim("PHONE1", label="row 7")
    assert led.get("PHONE1").is_claimed
    assert led.claimed() == [led.get("PHONE1")]

    led.release("PHONE1", note="done")
    assert not led.get("PHONE1").is_claimed
    assert led.get("PHONE1").note == "done"


def test_a_claim_goes_stale_so_a_dead_run_cannot_hold_a_phone_forever(tmp_path):
    led = Ledger.load(tmp_path)
    led.record("PHONE1")
    led.claim("PHONE1")

    entry = led.get("PHONE1")
    assert not entry.is_stale

    entry.claimed_at = time.time() - ledger_mod.STALE_CLAIM_SECONDS - 1
    assert entry.is_stale


def test_the_ledger_and_the_pools_go_stale_at_the_same_moment():
    """One question, two records, and they must not answer it differently.

    A run holds its phone in the ledger and its Gmail in the sheet for exactly
    as long as it holds either, and both are only asking "is the process that
    claimed this still alive". When the two numbers drifted apart the gap was
    the bug: the pools were shortened to five minutes once every writer beat,
    the ledger was left at two hours, and in between `free_abandoned_claims`
    handed a dead run's Gmail back while `settle_abandoned` still read that
    run's phone as held - so the same address could be signed into a second
    phone for the next hour and fifty-five minutes (2026-08-28).

    Pinned rather than commented, because a comment did not stop it.
    """
    from geelark_farm import config

    assert ledger_mod.STALE_CLAIM_SECONDS == config.STALE_CLAIM_DEFAULT
    # The pin this test claimed to make and could not: it compared the
    # constant to its own default, so an environment that moved only the
    # credential lease was invisible to it (2026-08-30). It is now enforced by
    # construction instead - a Ledger carries the resolved window and stamps
    # it on every entry - and the three tests below hold the construction up.
    # This line stays because the default is still the fallback for a Ledger
    # loaded without one.


def test_the_phone_lease_is_the_number_the_environment_set(tmp_path):
    """The window a run measures claims against comes from the setting, not
    from the module constant. That gap is what let one ChatGPT account sit on
    two phones for 115 minutes (2026-08-28)."""
    led = ledger_mod.Ledger.load(tmp_path, stale_after=1800)
    led.record("P1", serial="1")
    led.claim("P1")

    led.get("P1").claimed_at = time.time() - 1795
    assert not led.get("P1").is_stale
    led.get("P1").claimed_at = time.time() - 1805
    assert led.get("P1").is_stale


def test_the_window_is_never_written_into_the_ledger_file(tmp_path):
    """A persisted window is a strictly worse version of 2026-08-28: a phone
    claimed under yesterday's number would keep it for ever, across restarts,
    invisibly - and no environment could move it back."""
    led = ledger_mod.Ledger.load(tmp_path, stale_after=1800)
    led.record("P1", serial="1")
    led.claim("P1")
    led.save()

    on_disk = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert "stale_after" not in on_disk["phones"]["P1"]

    # and a Ledger loaded with a different window answers to that one
    again = ledger_mod.Ledger.load(tmp_path, stale_after=300)
    again.get("P1").claimed_at = time.time() - 400
    assert again.get("P1").is_stale, "the reloaded entry kept the old window"


def test_a_window_is_not_a_field_so_it_cannot_be_persisted():
    """The mechanical guard behind the test above. `save` serialises every
    dataclass field and `load` restores every field it knows by name, so the
    window has to be a ClassVar to stay out of the file."""
    from dataclasses import fields as dataclass_fields

    assert "stale_after" not in {f.name
                                 for f in dataclass_fields(ledger_mod.Entry)}


def test_a_corrupt_ledger_loads_empty_instead_of_crashing(tmp_path, caplog):
    """A bad ledger must not stop a run - but it must be loud, because reap can
    no longer tell an orphan from a claimed phone."""
    (tmp_path / "ledger.json").write_text("{not json", encoding="utf-8")
    with caplog.at_level("ERROR"):
        led = Ledger.load(tmp_path)
    assert led.entries == {}
    assert "corrupt" in caplog.text


def test_forget_removes_a_deleted_phone(tmp_path):
    led = Ledger.load(tmp_path)
    led.record("PHONE1")
    led.forget("PHONE1")
    assert Ledger.load(tmp_path).entries == {}


# -------------------------------------------------------------------- proxy
@pytest.mark.parametrize("raw", [
    "socks5://user:pass@1.2.3.4:1080",
    "user:pass@1.2.3.4:1080",
    "1.2.3.4:1080:user:pass",
])
def test_every_vendor_format_normalises_to_one_url(raw):
    assert parse(raw) == Proxy("socks5", "1.2.3.4", 1080, "user", "pass")
    assert parse(raw).url == "socks5://user:pass@1.2.3.4:1080"


def test_an_at_sign_inside_the_password_stays_with_the_credentials():
    parsed = parse("socks5://user:p@ss@1.2.3.4:1080")
    assert parsed.password == "p@ss"
    assert parsed.host == "1.2.3.4"


def test_the_password_never_appears_in_the_readable_form():
    parsed = parse("socks5://user:hunter2@1.2.3.4:1080")
    assert "hunter2" not in str(parsed)
    assert "hunter2" in parsed.url      # ...but the URL sent to GeeLark has it


@pytest.mark.parametrize("bad", [
    "", "1.2.3.4", "ftp://a:1", "1.2.3.4:notaport", "1.2.3.4:99999",
])
def test_unusable_proxies_are_rejected_before_a_phone_is_created(bad):
    with pytest.raises(ProxyError):
        parse(bad)


# ------------------------------- a file written by a different version of this
def test_a_field_this_version_does_not_know_is_read_around(tmp_path, caplog):
    """`Entry(**data)` raised TypeError on any key it had not heard of, and
    nothing caught it. A file written by a version with one more field would
    stop the tool from starting at all, while the phones it accounts for went
    on running (2026-08-23)."""
    import json

    (tmp_path / "ledger.json").write_text(json.dumps({"phones": {
        "P1": {"created_at": 1.0, "serial": "832", "cooled_at": 99.0},
    }}), encoding="utf-8")

    led = Ledger.load(tmp_path)

    assert led.get("P1").serial == "832"
    assert "cooled_at" in caplog.text


def test_one_unreadable_entry_does_not_take_the_others_with_it(tmp_path):
    """This is the file that says what exists and what is billing. Nine of ten
    is worse than ten and far better than none."""
    import json

    (tmp_path / "ledger.json").write_text(json.dumps({"phones": {
        "P1": {"created_at": 1.0, "serial": "832"},
        "P2": {"serial": "833"},                     # no created_at at all
        "P3": {"created_at": 3.0, "serial": "834"},
    }}), encoding="utf-8")

    led = Ledger.load(tmp_path)

    assert sorted(led.entries) == ["P1", "P3"]


def test_a_ledger_written_by_this_version_still_round_trips(tmp_path):
    """The guard must not quietly drop fields the code does use."""
    led = Ledger.load(tmp_path)
    led.record("P1", serial="832", label="row 4 / a@b.com", proxy="h:1")
    led.claim("P1")

    again = Ledger.load(tmp_path)

    assert again.get("P1").label == "row 4 / a@b.com"
    assert again.get("P1").proxy == "h:1"
    assert again.get("P1").is_claimed


# ================== the Windows replace window, which CI can never reach
#
# `_read` and `_replace` both retry PermissionError, and both were written for
# one thing: on Windows, `os.replace` fails while any other handle has the
# destination open - which is exactly what a second run reading the ledger is.
# The docstrings credit a concurrency test and none existed; coverage put both
# loops at zero, and CI cannot reach them either, because Linux does not raise
# it. So they are driven here, with the failure supplied rather than provoked
# (2026-08-23).
def test_a_read_retries_the_window_and_gets_the_file(tmp_path, monkeypatch):
    """Retrying is the whole fix: the file is either the old one or the new
    one, never half of either."""
    path = tmp_path / "ledger.json"
    path.write_text("{}", encoding="utf-8")
    tries = []
    real = pathlib.Path.read_text

    def sometimes(self, *a, **k):
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError("the replace has it open")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", sometimes)
    monkeypatch.setattr(ledger_mod.time, "sleep", lambda s: None)

    assert Ledger._read(path) == "{}"
    assert len(tries) == 3


def test_a_read_that_never_gets_in_says_so(tmp_path, monkeypatch):
    """It raises rather than answering an empty ledger, which would read as
    "no phones exist" - the one answer that must never be guessed."""
    path = tmp_path / "ledger.json"
    path.write_text("{}", encoding="utf-8")

    def never(self, *a, **k):
        raise PermissionError("still held")

    monkeypatch.setattr(pathlib.Path, "read_text", never)
    monkeypatch.setattr(ledger_mod.time, "sleep", lambda s: None)

    with pytest.raises(PermissionError):
        Ledger._read(path, attempts=3)


def test_a_write_that_never_lands_is_loud_and_leaves_no_temp(tmp_path,
                                                             monkeypatch,
                                                             caplog):
    """A phone missing from the ledger is a phone `reap` cannot account for,
    left billing with nothing tracking it - so this must not pass quietly."""
    led = Ledger.load(tmp_path)
    monkeypatch.setattr(ledger_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(ledger_mod.os, "replace",
                        lambda a, b: (_ for _ in ()).throw(
                            PermissionError("held open")))

    with caplog.at_level(logging.ERROR):
        led.record("P1", serial="801")

    assert "could not write the ledger" in caplog.text
    assert "geelark phones" in caplog.text          # and what to do about it
    # Nothing left behind for the next run to trip over.
    assert not list(tmp_path.glob("*.tmp"))


def test_releasing_a_phone_nothing_recorded_does_nothing(tmp_path):
    """Not an error: `reap` releases by id and the ledger may have been lost
    or pruned since."""
    led = Ledger.load(tmp_path)

    led.release("NEVER-SEEN", note="stopped by hand")

    assert led.entries == {}


# --------------------------------------- what mutation found (2026-08-26)
def test_a_ledger_held_open_by_another_reader_is_written_anyway(tmp_path,
                                                                monkeypatch):
    """On Windows `os.replace` fails with PermissionError while any other
    handle has the destination open - and something reading the ledger at the
    moment a parallel run writes it is exactly that. Caught by a concurrency
    test rather than in production, where the symptom would have been a phone
    silently missing from the ledger.

    Only "it eventually gives up" was held. That is also true of a save that
    never retries at all.
    """
    import os

    ledger = Ledger(path=tmp_path / "ledger.json")
    ledger.record("P1")

    real = os.replace
    refusals = [PermissionError("in use"), PermissionError("in use")]

    def replace(src, dst):
        if refusals:
            raise refusals.pop()
        return real(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(ledger_mod.time, "sleep", lambda _s: None)

    ledger.record("P2")

    assert refusals == [], "it gave up before the handle was released"
    assert "P2" in Ledger.load(tmp_path).entries


def test_a_ledger_that_will_not_write_leaves_no_temporary_behind(tmp_path,
                                                                 monkeypatch):
    """The half-written file is worse than the failure: the next load reads a
    directory with a stray `.tmp` in it, and a crash mid-run leaves one that
    nothing ever cleans up."""
    import os

    ledger = Ledger(path=tmp_path / "ledger.json")
    monkeypatch.setattr(os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(
                            PermissionError("never free")))
    monkeypatch.setattr(ledger_mod.time, "sleep", lambda _s: None)

    ledger.record("P1")          # returns rather than raising

    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == [], f"left {leftovers} behind"


def test_a_read_blocked_by_the_replace_window_is_tried_again(tmp_path,
                                                             monkeypatch):
    """The other half of the same Windows behaviour: while `os.replace` swaps
    the file in, a reader that happens to open at that instant gets
    PermissionError even though nothing is wrong. The file is either the old
    one or the new one, never half of either - so retrying is the whole fix."""
    path = tmp_path / "ledger.json"
    path.write_text('{"phones": {"P9": {"created_at": 1.0}}}',
                    encoding="utf-8")

    real = pathlib.Path.read_text
    refusals = [PermissionError("mid-replace")]

    def read_text(self, *a, **kw):
        if refusals and self == path:
            raise refusals.pop()
        return real(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    monkeypatch.setattr(ledger_mod.time, "sleep", lambda _s: None)

    assert "P9" in Ledger.load(tmp_path).entries
    assert refusals == [], "it never hit the refusal"


def test_the_directory_the_ledger_lives_in_is_made_for_it(tmp_path):
    """`state/` on a fresh checkout does not exist, and the ledger is written
    the instant a phone does - before anything else has had a reason to make
    it."""
    nested = tmp_path / "state" / "runs"
    ledger = Ledger(path=nested / "ledger.json")

    ledger.record("P1")

    assert (nested / "ledger.json").exists()


def test_the_read_gives_the_replace_window_a_fixed_number_of_tries(tmp_path,
                                                                   monkeypatch):
    """"It retries" is true of one attempt and of a hundred, and the
    difference is whether a run blocks on a file another process is holding.
    Ten, and then the refusal is real."""
    path = tmp_path / "ledger.json"
    path.write_text('{"phones": {}}', encoding="utf-8")

    tries = []
    real = pathlib.Path.read_text

    def read_text(self, *a, **kw):
        if self == path:
            tries.append(1)
            raise PermissionError("held open")
        return real(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    monkeypatch.setattr(ledger_mod.time, "sleep", lambda _s: None)

    with pytest.raises(PermissionError):
        Ledger._read(path)

    assert len(tries) == 10


def test_the_replace_gives_up_after_a_fixed_number_of_tries(tmp_path,
                                                            monkeypatch):
    """The same question on the writing side. Giving up early loses a phone
    from the ledger; never giving up blocks the run that recorded it."""
    import os

    ledger = Ledger(path=tmp_path / "ledger.json")
    tries = []

    def replace(src, dst):
        tries.append(1)
        raise PermissionError("held open")

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(ledger_mod.time, "sleep", lambda _s: None)

    ledger.record("P1")          # returns rather than raising

    assert len(tries) == 10


def test_each_wait_is_longer_than_the_one_before(tmp_path, monkeypatch):
    """A fixed pause spends the whole allowance inside the window it is
    waiting out. Growing it means the last try is the one most likely to
    land."""
    import os

    ledger = Ledger(path=tmp_path / "ledger.json")
    naps = []

    monkeypatch.setattr(os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(
                            PermissionError("held")))
    monkeypatch.setattr(ledger_mod.time, "sleep", naps.append)

    ledger.record("P1")

    assert naps == sorted(naps)
    assert naps[-1] > naps[0]


# ----------------------------------------- keeping a live claim looking live
def test_a_held_claim_is_restamped(tmp_path, monkeypatch):
    """It was written once and never refreshed, and the window is five
    minutes - so a build past its fifth minute read as abandoned to
    `settle_abandoned` and `apply_phone_states`, both of which spare a phone
    only while its claim is live. Serial passes were the only thing keeping
    that harmless (2026-08-29)."""
    from geelark_farm import ledger as ledger_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(ledger_mod, "_now", lambda: clock["t"])
    book = ledger_mod.Ledger.load(tmp_path)
    book.record("P1", label="build 1")
    book.claim("P1")

    clock["t"] += ledger_mod.STALE_CLAIM_SECONDS + 1
    assert book.get("P1").is_stale, "this is the state it used to be left in"

    assert book.beat() == ["P1"]
    assert not book.get("P1").is_stale


def test_a_released_claim_is_left_alone(tmp_path, monkeypatch):
    """Restamping one would make a finished phone look like a live build, and
    nothing would ever clean it up."""
    from geelark_farm import ledger as ledger_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(ledger_mod, "_now", lambda: clock["t"])
    book = ledger_mod.Ledger.load(tmp_path)
    book.record("P1")
    book.claim("P1")
    book.release("P1")

    assert book.beat() == []
    assert not book.get("P1").is_claimed


def test_a_beat_survives_a_restart(tmp_path, monkeypatch):
    """The stamp has to be on disk, not in this process's memory: the thing it
    protects against is another process's sync."""
    from geelark_farm import ledger as ledger_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(ledger_mod, "_now", lambda: clock["t"])
    book = ledger_mod.Ledger.load(tmp_path)
    book.record("P1")
    book.claim("P1")
    clock["t"] += 400
    book.beat()

    assert not ledger_mod.Ledger.load(tmp_path).get("P1").is_stale


def test_a_dead_runs_claims_are_not_kept_fresh_by_the_next_process(
        tmp_path, monkeypatch):
    """`beat` restamped every unreleased claim in the file, so a process that
    started after a kill kept the dead run's claims fresh forever, and
    `settle_abandoned` left three `building` rows - two of them running and
    billing - "to a run" that no longer existed, through two more restarts
    (2026-09-08, phones 1991, 1992, 1995)."""
    from geelark_farm import ledger as ledger_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(ledger_mod, "_now", lambda: clock["t"])
    dead = ledger_mod.Ledger.load(tmp_path)
    dead.record("P1", label="build 1")
    dead.claim("P1")

    # The next process: it claims one of its own and beats.
    fresh = ledger_mod.Ledger.load(tmp_path)
    fresh.record("P2", label="build 1")
    fresh.claim("P2")
    clock["t"] += ledger_mod.STALE_CLAIM_SECONDS + 1
    assert fresh.beat() == ["P2"]

    again = ledger_mod.Ledger.load(tmp_path)
    assert again.get("P1").is_stale, "the dead run's claim went stale"
    assert not again.get("P2").is_stale

    # Released or forgotten, a claim is no longer this process's to beat.
    fresh.release("P2")
    assert fresh.beat() == []


def test_two_processes_on_one_ledger_do_not_erase_each_other(tmp_path,
                                                             monkeypatch):
    """Phase 4 puts the keeper and a builder on the same file. Each save
    wrote the whole file from its own memory, so a claim the builder had
    just written vanished under the keeper's next prune - and a phone
    with no claim is one settle_abandoned deletes mid-login. Every
    mutation re-reads the file first now (2026-09-10)."""
    from geelark_farm import ledger as ledger_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(ledger_mod, "_now", lambda: clock["t"])
    keeper = ledger_mod.Ledger.load(tmp_path)
    keeper.record("OLD", label="old")
    builder = ledger_mod.Ledger.load(tmp_path)     # a second process

    builder.record("NEW", label="build 1")
    builder.claim("NEW")
    keeper.forget("OLD")                            # the keeper's prune

    fresh = ledger_mod.Ledger.load(tmp_path)
    assert fresh.get("NEW") is not None and fresh.get("NEW").is_claimed, (
        "the builder's claim survived the keeper's save")
    assert fresh.get("OLD") is None
    # And the keeper sees the builder's claim without reloading by hand.
    assert keeper.get("NEW").is_claimed
    # The beat is still only this process's own claims.
    clock["t"] += 400
    assert keeper.beat() == []
    assert builder.beat() == ["NEW"]


# ---------------------------------------------------- the store's ledger
class _Conn:
    """Records every statement; answers the next scripted rows."""

    def __init__(self, rows=None):
        self.sql = []
        self.params = []
        self.rows = list(rows or [])
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)
        answer = self.rows.pop(0) if self.rows else []

        class Cur:
            @staticmethod
            def fetchone():
                return answer[0] if answer else None

            @staticmethod
            def fetchall():
                return answer
        return Cur()

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


def _pg(monkeypatch, make_settings, tmp_path, rows=None):
    from geelark_farm import ledger as ledger_mod
    from geelark_farm.store import db

    conn = _Conn(rows)
    monkeypatch.setattr(db, "connect", lambda s: conn)
    settings = make_settings(state_dir=tmp_path, ledger_in_pg=True,
                             store_enabled=True, store_host="db",
                             store_password="pw")
    ledger = ledger_mod.PgLedger(settings, stale_after=300)
    ledger._imported = True               # no file to import in these
    return ledger, conn


def test_the_stores_ledger_answers_the_same_verbs_with_entries(
        monkeypatch, make_settings, tmp_path):
    """Scale-out step 1: one row per phone in `phone_claims`, so a keeper
    and any number of builders on any host share one answer to whose a
    phone is (2026-09-10)."""
    from geelark_farm import ledger as ledger_mod

    clock = {"t": 1000.0}
    monkeypatch.setattr(ledger_mod, "_now", lambda: clock["t"])
    row = ("P1", "7", "build 1", "1.2.3.4:10", "", 1000.0, 1000.0, None)
    ledger, conn = _pg(monkeypatch, make_settings, tmp_path,
                       rows=[[], [row], [], [row], [("P1",)], [row], [], [row]])

    got = ledger.record("P1", serial="7", label="build 1", proxy="1.2.3.4:10")
    assert "INSERT INTO phone_claims" in conn.sql[0]
    assert got.phone_id == "P1" and got.serial == "7"

    got = ledger.claim("P1")
    assert "ON CONFLICT (phone_id) DO UPDATE" in conn.sql[2]
    assert got.is_claimed and not got.is_stale
    assert ledger.beat() == ["P1"], "only this process's claims are restamped"
    assert "phone_id = ANY(%s)" in conn.sql[4]

    clock["t"] += 400
    assert ledger.get("P1").is_stale, "the window is the Entry's own"
    assert [e.phone_id for e in ledger.claimed()] == []      # scripted empty
    assert list(ledger.entries) == ["P1"]

    ledger.release("P1", note="done")
    assert "SET released_at" in conn.sql[-1]
    assert ledger.beat() == [], "released, so no longer this process's"
    ledger.forget("P1")
    assert conn.sql[-1].startswith("DELETE FROM phone_claims")
    assert conn.commits >= 4


def test_use_store_routes_shared_and_load_to_the_stores_ledger(
        monkeypatch, make_settings, tmp_path):
    from geelark_farm import ledger as ledger_mod
    from geelark_farm.store import db

    monkeypatch.setattr(db, "connect", lambda s: _Conn())
    settings = make_settings(state_dir=tmp_path, ledger_in_pg=True,
                             store_enabled=True, store_host="db",
                             store_password="pw")
    assert ledger_mod.use_store(settings)
    try:
        one = ledger_mod.Ledger.shared(tmp_path, stale_after=300)
        two = ledger_mod.Ledger.load(tmp_path, stale_after=300)
        assert isinstance(one, ledger_mod.PgLedger) and one is two
    finally:
        ledger_mod.use_store(make_settings(state_dir=tmp_path))
    assert not isinstance(ledger_mod.Ledger.shared(tmp_path, stale_after=300),
                          ledger_mod.PgLedger)


def test_the_file_is_imported_into_an_empty_table_once(monkeypatch,
                                                       make_settings, tmp_path):
    from geelark_farm import ledger as ledger_mod

    old = ledger_mod.Ledger.load(tmp_path)
    old.record("P9", serial="9", label="old")
    old.claim("P9")
    ledger, conn = _pg(monkeypatch, make_settings, tmp_path,
                       rows=[[(0,)], [], [("P9", "9", "old", "", "", 1.0,
                                         2.0, None)]])
    ledger._imported = False

    assert ledger.get("P9").phone_id == "P9"
    inserts = [q for q in conn.sql if q.startswith("INSERT INTO phone_claims")]
    assert len(inserts) == 1
    assert conn.params[conn.sql.index(inserts[0])][0] == "P9"
    ledger.get("P9")
    assert len([q for q in conn.sql if "count(*)" in q]) == 1, "asked once"
