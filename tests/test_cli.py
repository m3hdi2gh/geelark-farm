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
