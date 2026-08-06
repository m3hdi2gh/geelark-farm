"""Command-line behaviour that scripts and habits depend on.

Exit codes and guards, not output formatting. Each of these failed silently:
nothing raised, the command simply did the wrong thing.
"""

from __future__ import annotations

import time

import pytest

from geelark_farm import cli
from geelark_farm import ledger as ledger_mod
from geelark_farm.ledger import Ledger
from geelark_farm.orchestrator import Result


def result(row: int, ok: bool) -> Result:
    return Result(row=row, email=f"r{row}@example.com", ok=ok,
                  reason="ready" if ok else "error")


class Args:
    """argparse.Namespace stand-in."""

    def __init__(self, **kw):
        defaults = dict(limit=None, row=None, retry_failed=False,
                        dry_run=False, workers=None, watch=False)
        defaults.update(kw)
        self.__dict__.update(defaults)


# ------------------------------------------------------------- exit codes
@pytest.mark.parametrize("results,expected", [
    ([], 0),                                        # nothing pending
    ([result(1, True)], 0),
    ([result(1, True), result(2, True)], 0),
    ([result(1, True), result(2, False)], 1),
    ([result(1, False)], 1),
])
def test_run_exit_code(results, expected, tmp_path, monkeypatch, capsys, make_settings):
    """An empty result is success. A finished sheet is the normal state, and
    exiting non-zero for it makes `geelark run` unusable from cron or CI, where
    a no-op has to look like a no-op."""
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    monkeypatch.setattr(cli, "build_client", lambda s: object())
    monkeypatch.setattr(cli, "run_batch", lambda *a, **k: results)

    assert cli.cmd_run(settings, Args()) == expected
    capsys.readouterr()


def test_a_dry_run_always_succeeds(tmp_path, monkeypatch, capsys, make_settings):
    """It changes nothing, so it cannot fail at anything."""
    settings = make_settings(state_dir=tmp_path, artifact_dir=tmp_path)
    monkeypatch.setattr(cli, "build_client", lambda s: object())
    monkeypatch.setattr(cli, "run_batch", lambda *a, **k: [])

    assert cli.cmd_run(settings, Args(dry_run=True)) == 0
    capsys.readouterr()


# ------------------------------------------------------------- busy guard
def claimed_ledger(tmp_path, *, label: str = "row 4 / someone@example.com"):
    ledger = Ledger.load(tmp_path)
    ledger.record("P1", label=label)
    ledger.claim("P1", label=label)
    return ledger


def test_a_phone_another_run_holds_is_refused(tmp_path, make_settings):
    """Two flows on one phone corrupt each other's screen reads, because
    `uiautomator dump` cannot run twice at once. The claim already existed;
    until this pass only `install` consulted it."""
    claimed_ledger(tmp_path)
    settings = make_settings(state_dir=tmp_path)

    with pytest.raises(SystemExit, match="in use by another run"):
        cli.refuse_if_busy(settings, "P1")


def test_a_released_phone_is_not_refused(tmp_path, make_settings):
    ledger = claimed_ledger(tmp_path)
    ledger.release("P1")

    cli.refuse_if_busy(make_settings(state_dir=tmp_path), "P1")   # no raise


def test_a_stale_claim_does_not_lock_a_phone_forever(tmp_path, make_settings):
    """A dead process must not hold a phone hostage."""
    ledger = claimed_ledger(tmp_path)
    ledger.get("P1").claimed_at = time.time() - ledger_mod.STALE_CLAIM_SECONDS - 1
    ledger.save()

    cli.refuse_if_busy(make_settings(state_dir=tmp_path), "P1")   # no raise


def test_an_unknown_phone_is_not_refused(tmp_path, make_settings):
    """Nothing recorded means nothing is holding it."""
    cli.refuse_if_busy(make_settings(state_dir=tmp_path), "NEVER-SEEN")


# ------------------------------------------------------- the live console
def test_the_live_view_link_is_taken_out_of_the_progress_messages():
    """The link arrived as just another log line, so the next one replaced it
    within a second - the one message worth clicking was the one that could not
    be clicked. It is diverted for printing in full instead, and the phone's
    serial is read from the creation line, since that is what mints a fresh
    link once this one expires.
    """
    from geelark_farm.ui import LiveReporter

    class FakeRow:
        number, email = 1, "x@example.com"

    reporter = LiveReporter(total=1)
    reporter.start(1, FakeRow())
    reporter.note(1, "created 631291280617374014 (serial 477): vivo / Android 15")
    reporter.note(1, "watch it live: https://phone.geelark.com/index.html?t=abc")
    reporter.note(1, "screen: password_entry (visit 1)")

    entry = reporter.rows[1]
    assert entry["phone"] == "477"
    # The step is the flow's, never the URL: a step is overwritten by the next
    # one, and that is exactly what must not happen to a link.
    assert entry["step"] == "screen: password_entry (visit 1)"


def test_each_live_link_is_offered_for_printing_exactly_once():
    """The table cell can only offer an OSC 8 hyperlink, and a terminal without
    OSC 8 shows it as the bare word "open" with no way to reach the link - as
    one did on 2026-08-06. So the URL is also printed in full, above the table,
    where it stays in the scrollback and the terminal's own URL detection can
    find it.

    Once, though. The render loop drains this four times a second, so a link
    that came back on every tick would bury the table under its own URL.
    """
    from geelark_farm.ui import LiveReporter

    class FakeRow:
        number, email = 6, "x@example.com"

    reporter = LiveReporter(total=1)
    reporter.start(6, FakeRow())
    reporter.note(6, "created 631291280617374014 (serial 495): vivo / Android 15")
    reporter.note(6, "watch it live: https://phone.geelark.com/i.html?t=abc")

    # The serial travels with the link: it is what mints a fresh one with
    # `geelark start` after this one has expired.
    assert reporter.drain_links() == [(6, "495", "https://phone.geelark.com/i.html?t=abc")]
    assert reporter.drain_links() == []

    # The same URL logged again is still not new.
    reporter.note(6, "watch it live: https://phone.geelark.com/i.html?t=abc")
    assert reporter.drain_links() == []

    # A freshly minted one is.
    reporter.note(6, "watch it live: https://phone.geelark.com/i.html?t=xyz")
    assert reporter.drain_links() == [(6, "495", "https://phone.geelark.com/i.html?t=xyz")]
