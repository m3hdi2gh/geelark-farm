"""Ledger and proxy parsing.

Both are pure logic whose failure costs money rather than raising: a ledger that
forgets a phone leaves it billing unnoticed, and a proxy string that parses
wrongly creates a phone on the wrong network - which burns the account, not just
the minutes.
"""

from __future__ import annotations

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
