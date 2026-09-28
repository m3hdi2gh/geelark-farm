"""The cockpit: a run's page while it runs.

What is worth holding: the run says where it is at every step, in the
order it happens; the viewer's link - which drives the phone - is shown
only to an admin, only while the run is running, and is blanked when
the run ends; the viewer is drawn the same way on every drawing so the
live swap never reloads it; and a Run press lands on one address that
follows it from the queue to the end, and says so when it will not run.
"""
from __future__ import annotations

from datetime import datetime, timezone

from geelark_farm import tasks
from geelark_farm.store import task_runs
from geelark_farm.tasks import drive
from geelark_farm.web import live, task_pages, task_read
from tests.test_tasks import Conn, Cur, Phone, page_of

ADMIN = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
         "is_admin": True, "may_add_gmail": True, "may_change_proxy": True,
         "sees": "all", "username": "mehdi", "nav": {}}
URL = ("https://phone.geelark.com/index.html?isApi=true&id=639&envNo=4813"
       "&w=336&token=abc")


def a_run(**more) -> dict:
    row = {"id": 5, "task": "app_probe", "status": "running", "ok": False,
           "reason": "", "blame": "", "detail": "", "stage": "booting",
           "live_url": URL, "inputs": {"package": "com.openai.chatgpt"},
           "serial": "4813", "seconds": 0, "api_calls": 0, "trail": "",
           "folder": "", "screens": [],
           "started_at": datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)}
    row.update(more)
    return row


# ------------------------------------------------------------ the stages
def test_a_run_says_where_it_is_at_every_step_in_order(make_settings,
                                                         monkeypatch):
    """starting when the row opens, booting with the link the moment
    GeeLark hands it back, settling once the phone is up, reading while
    the task works - and the row closed after."""
    from geelark_farm import phones as phones_mod
    from geelark_farm.kit import phone as kit_phone

    said = []

    def ensure_running(client, phone_id, *, on_url=None, on_running=None,
                       **k):
        said.append(("start asked",))
        on_url(URL)
        on_running()
        return URL

    monkeypatch.setattr(phones_mod, "ensure_running", ensure_running)
    monkeypatch.setattr(kit_phone.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(kit_phone.cancel, "_stop_honoured",
                        lambda *a, **k: None)
    monkeypatch.setattr(drive, "_open_row", lambda *a, **k: 7)
    monkeypatch.setattr(task_runs, "stage",
                        lambda s, run_id, where, url=None:
                        said.append((run_id, where, url)))
    monkeypatch.setattr(task_runs, "finish",
                        lambda s, run_id, **k: said.append((run_id, "closed")))
    monkeypatch.setattr("geelark_farm.store.artifacts.put_dir",
                        lambda *a, **k: 0)

    class Nothing:
        def release(self, *a, **k):
            pass

    drive.one(make_settings(store_enabled=True), tasks.spec("app_probe"),
              {"package": "com.openai.chatgpt"}, phone_id="P",
              client=Phone(page_of("New chat")), ledger=Nothing())

    assert said == [("start asked",), (7, "booting", URL),
                    (7, "settling", None), (7, "reading", None),
                    (7, "closed")]


def test_a_playground_run_with_no_row_says_nothing(make_settings,
                                                   monkeypatch):
    from geelark_farm import phones as phones_mod
    from geelark_farm.kit import phone as kit_phone

    def ensure_running(client, phone_id, *, on_url=None, on_running=None,
                       **k):
        on_url(URL)
        on_running()

    monkeypatch.setattr(phones_mod, "ensure_running", ensure_running)
    monkeypatch.setattr(kit_phone.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(kit_phone.cancel, "_stop_honoured",
                        lambda *a, **k: None)
    monkeypatch.setattr(task_runs, "stage",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("wrote a stage with no row")))

    class Nothing:
        def release(self, *a, **k):
            pass

    build = drive.one(make_settings(store_enabled=False),
                      tasks.spec("app_probe"), {"package": "com.x"},
                      phone_id="P", client=Phone(page_of("New chat")),
                      ledger=Nothing())
    assert build.ok


def test_a_stage_is_written_only_on_a_running_row_and_never_raises(
        make_settings, monkeypatch):
    conn = Conn()
    monkeypatch.setattr(task_runs, "connect", lambda s: conn)
    task_runs.stage(make_settings(), 7, "booting", URL)
    assert "status = 'running'" in conn.sql[0], \
        "a late stage must not reopen an ended run"
    assert conn.args[0] == ("booting", URL, 7)

    def boom(_s):
        raise RuntimeError("the cluster went away")

    monkeypatch.setattr(task_runs, "connect", boom)
    task_runs.stage(make_settings(), 7, "reading")


def test_closing_a_run_blanks_the_link_that_drives_the_phone(
        make_settings, monkeypatch):
    conn = Conn()
    monkeypatch.setattr(task_runs, "connect", lambda s: conn)
    task_runs.finish(make_settings(), 7, ok=True, reason="signed_out")
    assert "live_url = ''" in conn.sql[0] and "stage = 'ended'" in conn.sql[0]


def test_the_press_finds_its_run_through_the_job(make_settings, monkeypatch):
    conn = Conn([Cur([(12, "app_probe")])])
    monkeypatch.setattr(task_runs, "connect", lambda s: conn)
    assert task_runs.by_action(make_settings(), 6443) == {
        "id": 12, "task": "app_probe"}
    assert "j.action_id = %s" in conn.sql[0]

    monkeypatch.setattr(task_runs, "connect", lambda s: Conn([Cur([])]))
    assert task_runs.by_action(make_settings(), 6443) is None


# ------------------------------------------------------------ the page
def test_a_running_run_draws_its_stages_and_listens_to_the_farm():
    drawn = task_pages.run_page(a_run(stage="settling"), ADMIN, watch=True)
    assert 'name="gf-live" content="farm"' in drawn
    assert "<b>settling</b>" in drawn
    assert drawn.index("booting") < drawn.index("<b>settling</b>")
    assert "data-since=" in drawn, "the clock counts in the browser"
    for region in ("run-head", "run-view", "run-body"):
        assert f'data-live="{region}"' in drawn, region
    assert "0s" not in drawn and "No screen of this run" not in drawn, \
        "nothing that reads as a run that did nothing"


def test_an_ended_run_stops_listening_and_draws_no_stages():
    drawn = task_pages.run_page(
        a_run(status="done", ok=True, reason="signed_out", stage="ended",
              live_url=""), ADMIN, watch=True)
    assert 'name="gf-live"' not in drawn
    assert "data-since=" not in drawn and "<iframe" not in drawn
    # The same three regions, so a page that was watching the run takes
    # the ended one in by the swap.
    for region in ("run-head", "run-view", "run-body"):
        assert f'data-live="{region}"' in drawn, region


def test_the_viewer_is_an_admins_and_only_while_it_runs():
    assert "<iframe" in task_pages.run_page(a_run(), ADMIN, watch=True)
    assert "<iframe" not in task_pages.run_page(a_run(), ADMIN, watch=False)
    assert "<iframe" not in task_pages.run_page(
        a_run(status="failed"), ADMIN, watch=True)
    assert "<iframe" not in task_pages.run_page(
        a_run(live_url=""), ADMIN, watch=True)


def test_the_viewer_is_drawn_the_same_every_time_so_the_swap_leaves_it():
    """The swap replaces a region only when it differs, so a frame whose
    address was set by a script - as the Live tab's is - would look new
    on every drawing and reload the stream under the person watching."""
    one = task_pages._viewer(a_run(), True)
    two = task_pages._viewer(a_run(stage="reading"), True)
    assert one == two
    assert 'src="' in one and "data-src" not in one
    assert "w=360" in one and "w=336" not in one, "the Live tab's width"
    assert "token=abc" in one


def test_the_viewer_fits_a_phone_screen():
    drawn = task_pages._viewer(a_run(), True)
    width = int(task_pages.VIEWER_BOX[0] * task_pages._VIEW_SCALE)
    assert f"width:{width}px" in drawn and width <= 375 - 32


def test_a_press_waiting_for_a_builder_says_it_is_queued():
    drawn = task_pages.waiting_page(
        {"status": "running", "result": "queued",
         "payload": {"serial": "4813"}}, "app_probe", ADMIN)
    assert "<b>in the queue</b>" in drawn
    assert 'name="gf-live" content="farm"' in drawn
    for region in ("run-head", "run-view", "run-body"):
        assert f'data-live="{region}"' in drawn, region


def test_a_press_that_will_not_run_says_why_where_it_landed():
    """The first press on the live farm answered "Queued" and was never
    heard of again; its reason was only on the Requests page
    (2026-09-27)."""
    drawn = task_pages.waiting_page(
        {"status": "refused", "result": "phone 4813 is taken - a task "
                                        "switches its phone off when it "
                                        "ends, so release it first",
         "payload": {"serial": "4813"}}, "app_probe", ADMIN)
    assert 'class="said no"' in drawn and "4813 is taken" in drawn
    assert 'name="gf-live"' not in drawn, "nothing more is coming"


def test_the_run_form_is_sent_by_the_browser_not_the_fetch_layer():
    """The fetch layer draws the answer at the old address, so the live
    stream reloaded the task's page, not the run's. A form with a target
    is left to the browser, which follows the redirect."""
    form = task_pages._run_form(tasks.spec("app_probe"), ADMIN, [])
    assert 'target="_self"' in form


class _Store:
    """The store, answering one row the way Postgres does - the columns
    the query named, and nothing it did not. Faking `actions.one` with a
    payload it never returns is how every live Run came to a 404
    (2026-09-28)."""

    table = {}

    def __init__(self, *a):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def _rows(self, sql, params=()):
        named = [c.strip() for c in
                 sql.split("SELECT", 1)[1].split("FROM", 1)[0].split(",")]
        row = self.table.get(params[0])
        return [{c: row[c] for c in named if c in row}] if row else []


def a_request(monkeypatch, **row):
    from geelark_farm.store import db as store_db

    full = {"id": 6443, "verb": "run_task", "status": "running",
            "result": "queued", "requested_by": 1,
            "payload": '{"task": "app_probe", "serial": "4813"}'}
    full.update(row)
    monkeypatch.setattr(_Store, "table", {6443: full})
    monkeypatch.setattr(store_db, "Store", _Store)


def test_a_live_request_is_matched_to_its_task_by_its_payload(
        make_settings, monkeypatch):
    a_request(monkeypatch)
    monkeypatch.setattr(task_runs, "by_action", lambda s, i: None)
    got = task_read.request(make_settings(store_enabled=True),
                            "app_probe", 6443)
    assert got is not None, "the press must land on its page, not a 404"
    assert got["action"]["payload"]["serial"] == "4813"
    assert got["run"] is None


def test_a_request_of_another_verb_or_task_is_not_shown(make_settings,
                                                        monkeypatch):
    s = make_settings(store_enabled=True)
    a_request(monkeypatch, verb="boot_phone")
    assert task_read.request(s, "app_probe", 6443) is None

    a_request(monkeypatch, payload='{"task": "other"}')
    assert task_read.request(s, "app_probe", 6443) is None
    assert task_read.request(s, "app_probe", 1) is None, "no such request"


def test_the_farms_stream_moves_for_a_run():
    assert "FROM task_runs" in live._FINGERPRINT
    assert live.FARM_COLUMNS == 7


def test_the_route_follows_a_queued_press_to_its_own_address():
    import inspect

    from geelark_farm.web import app

    source = inspect.getsource(app._Handler.do_GET)
    assert 'self._redirect(f"/tasks/{name}/req/{req}")' in source
    assert "task_read.request(self.settings, name, int(req))" in source


def test_the_clock_sits_by_the_word_running_not_after_the_last_stage():
    """At the end of the stages it read "done 1m 42s" under a run that
    was not done (the devserver, 2026-09-28)."""
    drawn = task_pages.run_page(a_run(), ADMIN, watch=True)
    assert drawn.index("data-since=") < drawn.index("in the queue")
    assert drawn.index("data-since=") > drawn.index(">running<")
    assert '<span class="dim">-</span>' not in drawn.split(
        'data-live="run-view"')[0], "no whose-fault dash before a verdict"
