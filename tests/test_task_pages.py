"""The Tasks pages: what there is to run, how it went, and one run.

Read-only pages, so what is worth holding is what they SAY. Three
things: that a failed run does not look like a success, that a task
nobody has run is still listed, and that the page shows no screen it
was not told about - the guards that already stand behind a build's
archived screens stand behind a task's, because it is the same route.
"""
from __future__ import annotations

from geelark_farm import failures
from geelark_farm.web import task_pages, task_read

USER = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
        "is_admin": True, "may_add_gmail": True, "may_change_proxy": True,
        "sees": "all", "username": "mehdi", "nav": {}}
LOOKER = {"id": 2, "role": "operator", "csrf": "c", "mutations": False,
          "is_admin": False, "sees": "own", "username": "ali", "nav": {}}


def a_run(**more) -> dict:
    row = {"id": 91, "task": "app_probe", "status": "failed", "ok": False,
           "reason": "unrecognised_screen", "blame": "nobody",
           "detail": "chrome drew a page neither list knows",
           "inputs": {"package": "com.android.chrome"}, "serial": "4435",
           "seconds": 35, "api_calls": 13, "trail": "app_probe: loading",
           "folder": "20260926-0146-task", "started_at": None, "screens": []}
    row.update(more)
    return row


# ------------------------------------------------------------- the list
def test_a_task_nobody_has_run_is_still_listed():
    """The list is what tells somebody a task exists at all, so one that
    hides the ones with no runs hides exactly the new ones."""
    drawn = task_pages.tasks_page({
        "days": 7, "counted": True,
        "tasks": [{"key": "brand_new", "title": "A new one", "summary": "s",
                   "inputs": [], "tally": {}}]}, USER)
    assert "brand_new" in drawn and "no runs yet" in drawn


def test_a_rate_never_stands_without_the_count_under_it():
    """Two runs and one worked is not "50%" - the pools learned this."""
    said = task_pages._rate({"runs": 2, "worked": 1})
    assert "50%" in said and "of 2 runs" in said
    assert "of 1 run<" in task_pages._rate({"runs": 1, "worked": 1})
    assert "median 19s" in task_pages._rate(
        {"runs": 3, "worked": 3, "median_seconds": 19})


def test_the_list_says_whose_fault_the_failures_were():
    """A rate says something is wrong; only this says what."""
    drawn = task_pages.tasks_page({
        "days": 7, "counted": True,
        "tasks": [{"key": "app_probe", "title": "T", "summary": "s",
                   "inputs": [{"name": "package", "required": True}],
                   "tally": {"runs": 10, "worked": 6,
                             "blames": {"exit": 3, "device": 1}}}]}, USER)
    assert "exit 3" in drawn and "device 1" in drawn


def test_the_page_draws_without_its_numbers_when_the_store_is_off():
    """What exists comes from the registry, not the cluster."""
    drawn = task_pages.tasks_page({
        "days": 7, "counted": False,
        "tasks": [{"key": "app_probe", "title": "T", "summary": "s",
                   "inputs": [], "tally": {}}]}, USER)
    assert "app_probe" in drawn and "store is off" in drawn


# -------------------------------------------------------------- one run
def test_a_failed_run_is_never_drawn_as_a_success():
    """`.said` is green with a tick and `.said.no` is red with a bang -
    the console's two, and the only two. A guessed `.said.bad` drew a
    failed run green (the devserver, 2026-09-26)."""
    bad = task_pages.run_page(a_run(), USER)
    assert 'class="said no"' in bad, "a failure must wear the red banner"

    good = task_pages.run_page(
        a_run(ok=True, status="done", reason="signed_out", blame=""), USER)
    assert 'class="said no"' not in good
    assert 'class="said "' in good or 'class="said"' in good


def test_a_run_says_what_the_reason_means_when_the_table_knows_it():
    drawn = task_pages.run_page(a_run(), USER, advice=failures.verdict)
    said = failures.verdict("unrecognised_screen")
    assert said.seen in drawn and said.advice[:40] in drawn


def test_a_reason_the_table_never_heard_of_falls_back_to_the_detail():
    """Rather than printing the safe default at somebody as though it
    were a finding."""
    drawn = task_pages.run_page(
        a_run(reason="something_new", detail="what actually happened"),
        USER, advice=lambda reason: None)
    assert "what actually happened" in drawn
    assert "no name for" not in drawn


def test_a_run_with_nothing_to_say_about_its_path_says_nothing():
    """`trails` carries the phase's name, and a task's phase is its own
    key - a panel holding a colon says the same word twice."""
    assert "What it walked" not in task_pages.run_page(
        a_run(trail="app_probe: "), USER)
    assert "What it walked" in task_pages.run_page(
        a_run(trail="app_probe: loading > signed_out"), USER)


def test_the_inputs_are_shown_and_said_to_hold_no_secret():
    drawn = task_pages.run_page(a_run(), USER)
    assert "com.android.chrome" in drawn
    assert "secret is never written here" in drawn


def test_the_screens_are_drawn_by_the_phone_pages_own_route():
    """No viewer of its own: two drawings of one thing is how they come
    to disagree."""
    drawn = task_pages.run_page(a_run(screens=[
        {"name": "180245-loading.xml", "at": "18:02:45", "screen": "loading",
         "wire": "/phones/4435/wire/f/180245-loading.xml", "shot": ""},
        {"name": "180610-captcha.xml", "at": "18:06:10", "screen": "captcha",
         "wire": "/phones/4435/wire/f/180610-captcha.xml",
         "shot": "/phones/4435/shot/f/180610-captcha.png"}]), USER)

    assert drawn.count('class="jcard"') == 2, "the journey's own card"
    assert "/phones/4435/wire/f/180245-loading.xml" in drawn
    # A photo link only where the flow saved one: a screenshot is taken
    # on the paths that fail, not on every screen.
    assert drawn.count(">photo</a>") == 1


def test_a_run_whose_screens_never_reached_the_store_says_so():
    assert "No screen of this run" in task_pages.run_page(a_run(), USER)


# ------------------------------------------------------------ the reader
def test_the_reader_finds_a_runs_screens_the_way_the_phone_page_does(
        monkeypatch, make_settings):
    """Through `read._stored`, so there is no second lister of a
    folder's files to keep in step with the first."""
    from geelark_farm.web import read

    monkeypatch.setattr(read, "_stored", lambda settings, serial: [
        {"folder": "other", "files": ["1.xml"], "images": []},
        {"folder": "mine", "files": ["180245-loading.xml",
                                     "180610-captcha.xml", "outcome.txt"],
         "images": ["180610-captcha.png"]}])

    got = task_read._screens(make_settings(), {"serial": "4435",
                                               "folder": "mine", "id": 1})

    assert [s["screen"] for s in got] == ["loading", "captcha"], \
        "only the XML, and in the order the names carry"
    assert got[0]["wire"] == "/phones/4435/wire/mine/180245-loading.xml"
    assert got[0]["at"] == "18:02:45"
    assert got[0]["shot"] == "", "no photo was saved for that one"
    assert got[1]["shot"].endswith("/shot/mine/180610-captcha.png")


def test_a_run_with_no_folder_has_no_screens(make_settings):
    assert task_read._screens(make_settings(), {"serial": "4435",
                                                "folder": "", "id": 1}) == []
    assert task_read._screens(make_settings(), {"serial": "", "folder": "f",
                                                "id": 1}) == []


def test_the_reader_lists_every_task_even_with_no_store(make_settings):
    got = task_read.listing(make_settings(store_enabled=False))
    assert [t["key"] for t in got["tasks"]], "the registry, not the cluster"
    assert got["counted"] is False


def test_the_numbers_failing_do_not_take_the_page_with_them(
        monkeypatch, make_settings):
    """The list of what exists comes from the registry; a cluster that
    will not answer costs the numbers, not the page."""
    from geelark_farm.store import task_runs

    def boom(*a, **k):
        raise RuntimeError("the cluster went away")

    monkeypatch.setattr(task_runs, "tally", boom)
    got = task_read.listing(make_settings(store_enabled=True))
    assert [t["key"] for t in got["tasks"]]
    assert got["counted"] is False


# ------------------------------------------------------------- the route
def test_the_pages_are_shut_to_somebody_with_no_pool_tick():
    """A task's rows say what happened to the farm's own stock, so the
    same tick that opens the pools opens these - and an operator, who
    has one page, has never had the pool pages either."""
    assert task_pages.may_see(USER) is True
    assert task_pages.may_see(LOOKER) is False
    # And the route asks before it reads anything.
    import inspect

    from geelark_farm.web import app as app_mod

    source = inspect.getsource(app_mod._Handler.do_GET)
    at = source.index('if path == "/tasks"')
    assert "task_pages.may_see(user)" in source[at:at + 700]


def test_a_run_id_that_belongs_to_another_task_is_not_shown(
        monkeypatch, make_settings):
    """`/tasks/<name>/<id>` names both, and a row whose task is not the
    one in the path is a 404 rather than somebody else's run under this
    heading."""
    import inspect

    from geelark_farm.web import app as app_mod

    source = inspect.getsource(app_mod._Handler.do_GET)
    assert 'str(row.get("task")) != name' in source
