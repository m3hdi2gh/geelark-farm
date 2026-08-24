"""What the screen archive keeps, and for how long.

87MB across 457 directories and 5242 XML files, three weeks deep, describing
phones that were deleted a fortnight before - and most of it the pages of
builds that went perfectly (2026-08-17). The rules are the operator's: a
success is worth keeping while its phone exists, a failure for a week.
"""

from __future__ import annotations

import pathlib
import time

from geelark_farm import artifacts

DAY = 86400


def build_dir(root, name, *, age_days=0, outcome=None, files=("page.xml",)):
    directory = root / name
    directory.mkdir()
    for one in files:
        (directory / one).write_text("<hierarchy/>", encoding="utf-8")
    if outcome is not None:
        artifacts.record(directory, ok=outcome[0], status=outcome[1])
    if age_days:
        when = time.time() - age_days * DAY
        for path in list(directory.iterdir()) + [directory]:
            import os
            os.utime(path, (when, when))
    return directory


def test_a_success_is_kept_while_its_phone_exists(tmp_path):
    """The evidence is about a device, so it is worth having exactly as long
    as the device is."""
    build_dir(tmp_path, "20260817-060502-build835", outcome=(True, "ready"))

    assert artifacts.prune(tmp_path, {"835"}) == []
    assert (tmp_path / "20260817-060502-build835").exists()


def test_a_success_goes_once_its_phone_is_deleted(tmp_path):
    """Once the phone is gone the pages describe nothing - and age does not
    come into it, because the phone is what they were about."""
    build_dir(tmp_path, "20260817-060502-build835", outcome=(True, "ready"))

    assert artifacts.prune(tmp_path, {"836"}) == ["20260817-060502-build835"]
    assert not (tmp_path / "20260817-060502-build835").exists()


def test_a_failure_is_kept_for_a_week_even_without_its_phone(tmp_path):
    """A failure outlives its phone on purpose: the phone is usually the first
    thing deleted, and the failure is the thing still worth reading."""
    build_dir(tmp_path, "20260810-060502-build820", age_days=3,
              outcome=(False, "download_stalled"))

    assert artifacts.prune(tmp_path, set()) == []


def test_a_failure_older_than_a_week_goes(tmp_path):
    build_dir(tmp_path, "20260801-060502-build700", age_days=9,
              outcome=(False, "download_stalled"))

    assert artifacts.prune(tmp_path, set()) == ["20260801-060502-build700"]


def test_a_directory_with_nothing_to_read_is_treated_as_a_failure(tmp_path):
    """Which is the side that keeps things longer. A hand-run `geelark login`
    writes no outcome, and neither did anything from before this existed."""
    build_dir(tmp_path, "20260816-055654-login-row1", age_days=2)
    build_dir(tmp_path, "20260730-055654-login-row1", age_days=18)

    assert artifacts.prune(tmp_path, set()) == ["20260730-055654-login-row1"]


def test_the_batch_position_is_not_mistaken_for_a_serial(tmp_path):
    """`build3` is where the phone sat in the batch. Reading it as serial 3
    would tie a directory to whichever phone happens to be numbered 3."""
    assert artifacts.serial_of(tmp_path / "20260817-060502-build835") == "835"
    assert artifacts.serial_of(tmp_path / "20260817-060502-finish823") == "823"
    assert artifacts.serial_of(tmp_path / "20260730-055654-login-row1") == ""


def test_a_dry_run_removes_nothing_but_reports_the_same_list(tmp_path):
    build_dir(tmp_path, "20260801-060502-build700", age_days=9,
              outcome=(False, "x"))

    listed = artifacts.prune(tmp_path, set(), dry_run=True)

    assert listed == ["20260801-060502-build700"]
    assert (tmp_path / "20260801-060502-build700").exists()
    assert artifacts.prune(tmp_path, set()) == listed


def test_loose_files_beside_the_directories_are_left_alone(tmp_path):
    (tmp_path / "notes.txt").write_text("mine", encoding="utf-8")

    assert artifacts.prune(tmp_path, set()) == []
    assert (tmp_path / "notes.txt").exists()


def test_an_archive_that_does_not_exist_yet_is_not_an_error(tmp_path):
    assert artifacts.prune(tmp_path / "nothing-here", set()) == []


def test_recording_an_outcome_never_raises(tmp_path):
    """A build that has done its work is not failed over a note about
    itself."""
    artifacts.record(tmp_path / "was-never-created", ok=True, status="ready")


def test_the_outcome_is_readable_by_a_person(tmp_path):
    """Someone browsing artifacts/ should be able to see how a build went
    without opening every page in it."""
    directory = build_dir(tmp_path, "20260817-060502-build835",
                          outcome=(False, "download_stalled"))

    said = (directory / artifacts.OUTCOME_FILE).read_text(encoding="utf-8")

    assert said.strip() == "failed download_stalled"


def test_a_directory_with_no_serial_is_not_kept_by_an_unnamed_phone(tmp_path):
    """An unnamed phone puts "" in the set and `serial_of` answers "" for a
    name it cannot read, so the two matched and the directory was kept for
    ever on the strength of nothing (2026-08-23)."""
    old = tmp_path / "20260101-000000-something-else"
    old.mkdir()
    (old / "outcome.txt").write_text("ok ready\n", encoding="utf-8")

    removed = artifacts.prune(tmp_path, {"", "801"}, now=_now())

    assert removed == [old.name]


def test_a_directory_that_vanishes_mid_prune_does_not_stop_it(tmp_path,
                                                              monkeypatch):
    """Gone between listing and asking. Neither that nor an unreadable one is
    worth ending a prune over."""
    kept = tmp_path / "20260101-000000-build801"
    kept.mkdir()
    (kept / "outcome.txt").write_text("ok ready\n", encoding="utf-8")
    ghost = tmp_path / "20260101-000000-build999"
    ghost.mkdir()

    real_stat = pathlib.Path.stat

    def stat(self, *a, **k):
        if self.name.endswith("build999"):
            raise OSError("it went away")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "stat", stat)

    assert artifacts.prune(tmp_path, {"801"}, now=_now()) == []


def _now() -> float:
    import time
    return time.time()
