"""A phone's journey read back from its log lines and archived screens.

The fixture is two real phones (2026-09-25), addresses masked and the
viewer links dropped: 4435, a build Google kept showing captchas to, and
4445, a warm build and then three finishes whose sent account did not go
in."""
import json
import pathlib
from datetime import datetime, timezone

import pytest

from geelark_farm.web import journey

FIXTURE = json.loads((pathlib.Path(__file__).parent / "fixtures" / "journey.json")
                     .read_text(encoding="utf-8"))
CREATED_4435 = [datetime(2026, 9, 25, 17, 59, 28, tzinfo=timezone.utc)]


def _runs(serial, created=()):
    phone = FIXTURE[serial]
    return journey.runs_from(phone["lines"], phone["folders"], created)


def test_a_build_google_kept_captcha_ing_reads_as_one_failed_run():
    (run,) = _runs("4435", CREATED_4435)
    assert (run["kind"], run["mark"], run["status"], run["seconds"]) == (
        "build", "FAIL", "phone_distrusted", 406)
    assert [s["name"] for s in run["stages"]] == [
        "created", "boot", "settle", "google"]
    boot, google = run["stages"][1], run["stages"][3]
    assert boot["seconds"] == 125 and boot["state"] == "done"
    assert google["state"] == "failed" and run["failed_at"] == "google"
    # The screens, in order, eleven captcha visits as one card.
    assert [(s["name"], s["visits"]) for s in google["screens"]] == [
        ("loading", 1), ("dismissable", 1), ("email_entry", 1),
        ("captcha", 11)]
    assert google["screens"][1]["taps"] == ["SKIP"]
    # Three grids CapSolver answered, each followed by another.
    assert [c["prompt"] for c in google["captchas"]] == [
        "Select all images with a fire hydrant",
        "Select all images with a fire hydrant",
        "Select all images with crosswalks"]
    # Each screen its archived XML, and the picture of where it stopped.
    assert [s["file"] for s in google["screens"]] == [
        "180245-loading.xml", "180259-dismissable.xml",
        "180313-email_entry.xml", "180404-captcha.xml"]
    assert run["shot"] == "captcha-screen.png"
    assert run["last_dump"] == "180610-captcha_shown.xml"
    assert "person@" not in google["detail"], "the address is masked"


def test_a_warm_build_and_the_finishes_after_it_are_separate_runs():
    runs = _runs("4445")
    assert [(r["kind"], r["mark"]) for r in runs] == [
        ("build", "WARM"), ("finish", "WARM"), ("finish", "WARM"),
        ("finish", "WARM")]
    build = runs[0]
    # Warm on purpose: every stage done, nothing failed.
    assert [s["name"] for s in build["stages"]] == [
        "boot", "settle", "google", "apps"]
    assert all(s["state"] == "done" for s in build["stages"])
    assert build["failed_at"] == ""
    # A finish whose account did not go in - refused, or a code nobody
    # could read - ends on its app stage, marked so.
    for finish in runs[1:]:
        app = finish["stages"][-1]
        assert app["name"] == "app" and app["state"] == "refused"
        assert [s["name"] for s in app["screens"]][-1] == "email_code_entry"
        assert finish["failed_at"] == "app"


def test_a_run_still_going_is_running_not_finished():
    lines = [line for line in FIXTURE["4435"]["lines"]
             if not line["msg"].startswith("phone 4435 FAIL")]
    (run,) = journey.runs_from(lines, FIXTURE["4435"]["folders"], CREATED_4435)
    assert run["end"] is None and run["mark"] == ""
    assert run["stages"][-1]["state"] == "running"


def test_lines_it_does_not_know_are_not_marks():
    runs = journey.runs_from([
        {"at": "2026-09-25 10:00:00+00", "logger": "x.y", "msg": "hello"},
        {"at": "2026-09-25 10:00:01+00", "logger": "geelark_farm.builder",
         "msg": "phone 12 FAIL: nonsense"}])
    assert runs == []


@pytest.mark.parametrize("stage", sorted(journey.MARKS))
def test_every_mark_is_still_written_by_the_code_that_writes_it(stage):
    """The journey is read from the builder's own words. Reworded, every
    journey would flatten quietly; so each is read back out of its
    source."""
    words, where = journey.MARKS[stage]
    source = (pathlib.Path(journey.__file__).parents[2] / where).read_text(
        encoding="utf-8")
    probe = words.strip()
    if stage == "boot":
        probe = "- starting it (billing is per minute)"
    if stage == "installed":
        probe = "is on, from GeeLark's installer"
    assert probe in source, f"{where} no longer writes {probe!r}"


def test_the_closing_line_is_the_builders_own_format():
    root = pathlib.Path(journey.__file__).parents[1]
    builder = (root / "builder.py").read_text(encoding="utf-8")
    assert 'log.info("%s %s: %s (%.0fs)", build.name,' in builder
    result = (root / "build_result.py").read_text(encoding="utf-8")
    assert '"OK"' in result and '"WARM"' in result and '"FAIL"' in result
    router = (root / "flows" / "router.py").read_text(encoding="utf-8")
    assert 'out.info("screen: %s (visit %d)", matched.name, visits)' in router


# ------------------------------------------------------------ drawings
def test_a_screen_is_drawn_from_its_dump_with_the_address_masked():
    xml = FIXTURE["4435"]["xml"]["180404-captcha.xml"].encode()
    svg = journey.wireframe_svg(xml, width=150)
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" width="150"')
    assert "Verify it" in svg and "not a robot" in svg
    assert "person@" not in svg and "pe•••@gmail.com" in svg
    assert "<script" not in svg


def test_a_drawing_escapes_what_the_screen_said():
    xml = (b'<hierarchy><node bounds="[0,0][720,1440]">'
           b'<node text="&lt;script&gt;x&lt;/script&gt; &amp; me" '
           b'class="android.widget.TextView" bounds="[10,10][300,60]"/>'
           b'</node></hierarchy>')
    svg = journey.wireframe_svg(xml)
    assert "<script>" not in svg and "&lt;script&gt;" in svg


def test_a_dump_that_will_not_parse_is_an_empty_screen_not_an_error():
    svg = journey.wireframe_svg(b"not xml at all")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
