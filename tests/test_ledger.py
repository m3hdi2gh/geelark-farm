"""Ledger and proxy parsing.

Both are pure logic whose failure costs money rather than raising: a ledger that
forgets a phone leaves it billing unnoticed, and a proxy string that parses
wrongly creates a phone on the wrong network - which burns the account, not just
the minutes.
"""

from __future__ import annotations

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
    from geelark_farm.config import Settings

    assert ledger_mod.STALE_CLAIM_SECONDS == config.STALE_CLAIM_DEFAULT
    # And against the number the run will actually use. The line above pins
    # the ledger to the default; the credential side resolves the *setting*,
    # which `.env.example` invites you to override. Uncomment that line and
    # the phone lease stays 300s while the credential lease becomes 3600s -
    # the exact gap of 2026-08-28, which this test claimed to prevent and
    # could not see (2026-08-30).
    assert ledger_mod.STALE_CLAIM_SECONDS == Settings.load().stale_claim_seconds, (
        "STALE_CLAIM_SECONDS in the environment moves the credential lease "
        "and not the phone lease; they have to be one number")


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
