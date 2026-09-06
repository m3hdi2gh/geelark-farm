"""The console's plumbing (2026-09-03): honest timestamps, the serial on
every log line, the pulse that says why, alerts on every page, service
controls and phone states from the web, and the guards around asking.
"""

from __future__ import annotations

import logging
import pathlib
import threading

import pytest

from geelark_farm import builder, verbs
from geelark_farm import serve as serve_mod
from geelark_farm.store import actions as actions_mod
from geelark_farm.store import logdb
from geelark_farm.web import pages, read
from tests.test_builder import make_book
from tests.test_serve import Fuse, Recorder
from tests.test_store import _ScriptedConn
from tests.test_web import _form, web  # noqa: F401  (the live-server fixture)

SRC = pathlib.Path("src/geelark_farm")


# ------------------------------------------------------------ timestamps
def test_the_mirror_stamps_updated_at_only_when_a_row_moved():
    """Every page's 'since' read the last pass's time until the three
    upserts learned IS DISTINCT FROM (2026-09-03)."""
    shadow = (SRC / "store/shadow.py").read_text(encoding="utf-8")
    assert shadow.count("IS DISTINCT FROM") == 3
    assert "updated_at = now()\"" not in shadow.replace(
        "done_at = now(), updated_at = now()", "")


def test_clocks_are_shown_in_the_owners_zone_and_in_words():
    pages.set_zone("Asia/Tehran")
    assert pages._clock("2026-09-01 18:06:12+00:00") == "21:36:12"
    assert pages._when(None) == ""
    assert pages._when("nonsense") == "nonsense"
    assert pages._day("2025-12-31 23:00:00+00:00") == "2026-01-01"
    assert pages._ago("2000-01-01 00:00:00+00:00").endswith("d ago")


def test_the_queued_banner_names_the_request():
    html = pages._said("queued:241", pages._POOL_SAID)
    assert 'href="/requests?hi=241"' in html and "#241" in html
    assert pages._said("nonsense", pages._POOL_SAID) == ""
    assert "#" not in pages._said("refused", pages._POOL_SAID)


# --------------------------------------------------- the serial on a line
def test_every_log_line_of_a_worker_carries_its_phone():
    record = logging.LogRecord("geelark_farm.flows", logging.INFO, "f.py", 1,
                               "screen: password_entry", (), None)
    token = builder._serial.set("1556")
    try:
        builder.BuildContextFilter().filter(record)
    finally:
        builder._serial.reset(token)
    assert record.serial == "1556"
    row = logdb._row(record)
    assert row[5] == "1556"

    bare = logging.LogRecord("geelark_farm.serve", logging.INFO, "s.py", 1,
                             "5 warm of 5", (), None)
    builder.BuildContextFilter().filter(bare)
    assert bare.serial == builder.NO_BUILD
    assert logdb._row(bare)[5] == "", "no phone is an empty column"


# -------------------------------------------------------------- the pulse
def test_a_stopped_pass_still_leaves_a_pulse_that_says_so(monkeypatch,
                                                           make_settings,
                                                           tmp_path):
    settings = make_settings(state_dir=tmp_path, store_enabled=True)
    Recorder(warm=5, free=10).install(monkeypatch)
    monkeypatch.setattr(serve_mod, "_drain_actions", lambda *a, **k: 0)
    monkeypatch.setattr(serve_mod, "_controls",
                        lambda *a, **k: frozenset({"Stop everything"}))
    kept = {}
    monkeypatch.setattr(serve_mod, "_put_state",
                        lambda s, key, value: kept.update({key: value}))
    monkeypatch.setattr(serve_mod, "_show", lambda *a, **k: None)
    serve_mod.once(object(), settings, Fuse(), serve_mod.Slots())
    assert kept["pass"]["stopped"] is True and kept["pass"]["at"] > 0
    assert isinstance(kept["pass"], dict) and "tripped" in kept["pass"]


def test_the_pulse_says_why_and_how_close_the_breaker_is(monkeypatch,
                                                          make_settings,
                                                          tmp_path):
    settings = make_settings(state_dir=tmp_path, warm_stock=5,
                             store_enabled=True)
    Recorder(warm=5, free=10).install(monkeypatch)
    monkeypatch.setattr(serve_mod, "_drain_actions", lambda *a, **k: 0)
    kept = {}
    monkeypatch.setattr(serve_mod, "_shadow",
                        lambda s, b, d, o, pulse=None: kept.update(pulse))
    fuse = Fuse(tripped="5 builds in a row failed")
    fuse.seen = lambda: (5, ["captcha_shown"] * 5)      # the real API

    serve_mod.once(object(), settings, fuse, serve_mod.Slots())

    assert kept["breaker_count"] == 5 and kept["breaker_reasons"][0] == "captcha_shown"
    assert "5 builds in a row" in kept["warning"]
    assert kept["failing"] == 0 and "took" in kept


# ------------------------------------------------------------- the alerts
def test_alerts_come_off_the_pulse_and_name_the_page_that_fixes_them():
    import time

    quiet = read.alerts({"at": time.time(), "tripped": ""}, {"gmail": 3})
    assert quiet == []

    loud = read.alerts({"at": time.time() - 3600, "tripped": "5 failed",
                        "breaker_count": 5, "breaker_reasons": ["captcha"],
                        "paused": True, "failing": 2, "unknown_running": 1},
                       {"gmail": 0})
    texts = " | ".join(a["text"] for a in loud)
    assert "last pass was 60m ago" in texts
    assert "breaker is open (5 of 5" in texts
    assert "paused" in texts and "2 pass(es) in a row failed" in texts
    assert "Gmail pool is empty" in texts and "being billed" in texts
    assert {a["level"] for a in loud} == {"bad", "warn"}

    stopped = read.alerts({"stopped": True, "at": 1}, {"gmail": 5})
    assert stopped[0]["text"].startswith("STOPPED") and len(stopped) == 1


def test_the_alert_strip_renders_on_every_page():
    user = {"id": 1, "username": "m", "role": "admin", "sees": "all",
            "nav": {"alerts": [{"level": "bad", "text": "The Gmail pool "
                                "is empty.", "href": "/pools/gmail"}]}}
    html = pages.page("Anything", "<p>x</p>", user=user)
    # The alert is a box with the link inside it now - a bold lead, the
    # sentence, and a dismiss - so the class and the link sit on two tags.
    assert 'class="alert bad"' in html and 'href="/pools/gmail"' in html
    assert "Gmail pool is empty" in html
    assert 'class="alerts"' not in pages.page("A", "<p>x</p>", user={
        "id": 1, "username": "m", "role": "admin", "sees": "all", "nav": {}})


# ---------------------------------------------------------- the controls
class _Board:
    CONTROLS = ("Clear breaker", "Pause building", "Stop unaccounted phones",
                "Stop everything")

    def __init__(self):
        self.ticked, self.unticked = [], []

    def tick(self, name):
        self.ticked.append(name)
        return True

    def taken(self, name):
        self.unticked.append(name)


def test_service_controls_are_ticks_on_the_sheet():
    book = make_book()
    book.service = _Board()
    status, said, detail = verbs.control(
        book, None, None, {"what": "pause", "by": "mehdi"}, None)
    assert status == "done" and book.service.ticked == ["Pause building"]
    assert "by mehdi" in said and detail == {"control": "Pause building",
                                              "move": "tick"}
    verbs.control(book, None, None, {"what": "start"}, None)
    assert book.service.unticked == ["Stop everything"]
    status, said, _ = verbs.control(book, None, None, {"what": "explode"},
                                    None)
    assert status == "refused"
    book.service = None
    assert verbs.control(book, None, None, {"what": "pause"}, None)[0] == "failed"


def test_the_service_tab_can_be_ticked_from_code():
    from geelark_farm.pools import ServiceBoard

    class Tab:
        def __init__(self):
            self.updates = []

        def update(self, values, a1, **kw):
            self.updates.append((values, a1))

    tab = Tab()
    board = ServiceBoard(tab, threading.Lock())
    assert board.tick("Pause building") is True
    assert tab.updates == [([[True]], "D3")]


# --------------------------------------------------------- phones by hand
def test_a_phone_can_be_marked_taken_done_or_failed_from_the_web(monkeypatch):
    """What a person says about a phone is written to the store now, not to
    the tab: that is what made this button answer in the request that
    pressed it instead of waiting for a pass (C3)."""
    from geelark_farm.store import person

    said_about: dict = {}
    monkeypatch.setattr(
        person, "set_state",
        lambda settings, serial, state: (
            said_about.update({serial: state}) or True) if serial == "1500"
        else False)
    monkeypatch.setattr(verbs, "_is_building", lambda settings, serial: False)
    book = make_book()

    status, said, detail = verbs.set_phone_state(
        book, None, None, {"serial": "1500", "state": "taken", "by": "ali"},
        None)
    assert status == "done" and detail == {"state": "taken"}
    assert said_about["1500"] == "taken"

    verbs.set_phone_state(book, None, None, {"serial": "1500",
                                             "state": "unused"}, None)
    assert said_about["1500"] == "", "unused is the blank cell it always was"

    assert verbs.set_phone_state(book, None, None, {"serial": "1500",
                                                    "state": "lost"},
                                 None)[0] == "refused"
    assert verbs.set_phone_state(book, None, None, {"serial": "9",
                                                    "state": "done"},
                                 None)[0] == "failed"


def test_marking_a_building_phone_done_is_refused():
    book = make_book()
    book.phones.start(Serial="1500", Gmail="g0@example.com",
                      Status=book.phones.BUILDING)
    status, said, _ = verbs.set_phone_state(
        book, None, None, {"serial": "1500", "state": "done"}, None)
    assert status == "refused" and "worked on" in said


def test_clearing_tries_puts_a_given_up_phone_back(monkeypatch):
    """How often this tool has tried a phone is the store's now, with the
    rest of the person channel (C3)."""
    from geelark_farm.store import person

    cleared: list = []
    monkeypatch.setattr(
        person, "clear_tries",
        lambda settings, serial: cleared.append(serial) or serial == "1500")
    book = make_book()

    status, said, _ = verbs.clear_tries(book, None, None,
                                        {"serial": "1500", "by": "mehdi"},
                                        None)

    assert status == "done" and cleared == ["1500"]
    assert verbs.clear_tries(book, None, None, {"serial": "9"},
                             None)[0] == "failed"


def test_offer_again_reaches_the_gmail_tab_too():
    book = make_book(gmails=1)
    g0 = book.gmails._rows[0]
    book.gmails.fail(g0, "captcha_shown", note="x")
    status, said, detail = verbs.offer_again(
        book, None, None, {"address": "g0@example.com", "kind": "gmail",
                           "by": "mehdi"}, None)
    assert status == "done" and g0 in book.gmails.available


def test_removing_a_proxy_keeps_what_it_removed_for_undo():
    book = make_book(proxies=1)
    sx = book.proxies._rows[0]
    sx.values["Name"] = "SX1"
    status, _, detail = verbs.remove_proxy(book, None, None, {"name": "SX1"},
                                           None)
    assert status == "done"
    assert detail["removed"]["name"] == "SX1"
    assert detail["removed"]["raw"].startswith("10.0.0.0:9999")


# --------------------------------------------------------- the queue rules
def test_stuck_running_rows_are_closed_by_the_drain():
    """It reports how many it closed, and the fake says how many matched -
    it used to have no rowcount at all, so this read zero whatever the
    statement did."""
    conn = _ScriptedConn([None], rowcounts=[3])
    assert actions_mod.expire_running(conn, older_than=7200) == 3
    assert "UPDATE actions SET status = 'failed'" in conn.sql[0]
    assert "make_interval" in conn.sql[0] and conn.committed == 1

    quiet = _ScriptedConn([None], rowcounts=[0])
    assert actions_mod.expire_running(quiet, older_than=7200) == 0


@pytest.mark.parametrize("web", [True], indirect=True)
def test_the_same_button_twice_answers_already_asked(web,  # noqa: F811
                                                      monkeypatch):
    monkeypatch.setattr(actions_mod, "pending_for",
                        lambda s, *, verb, needle: 240 if needle == "1549"
                        else None)
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda *a, **k: pytest.fail("queued a twin"))
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/phones/1549/proxy", _form(csrf=client.csrf()))
    assert status == 303
    assert dict(headers)["Location"] == "/?said=already:240"
    _, _, body = client.request("GET", "/?said=already:240")
    assert "Already asked" in body and 'href="/requests?hi=240"' in body


def test_a_dead_store_is_a_page_not_a_traceback(web,  # noqa: F811
                                                 monkeypatch):
    import geelark_farm.web.app as app_mod

    class OperationalError(Exception):
        pass

    def boom(*a, **k):
        raise OperationalError("connection refused")

    monkeypatch.setattr(app_mod.read, "dashboard", boom)
    client = web()
    client.login()
    status, _, body = client.request("GET", "/")
    assert status == 503
    assert "store is not answering" in body
    assert "Something broke" not in body


def test_enqueue_records_the_asking_as_an_event(monkeypatch, make_settings):
    import geelark_farm.store.events as events_mod

    conn = _ScriptedConn([(41,)])
    monkeypatch.setattr("geelark_farm.store.actions.connect", lambda s: conn)
    emitted = []
    monkeypatch.setattr(events_mod, "emit",
                        lambda s, kind, **kw: emitted.append((kind, kw)) or True)
    got = actions_mod.enqueue(make_settings(), verb="change_proxy",
                              payload={"serial": "1549", "by": "ali"},
                              requested_by=8, idem_key="k")
    assert got == 41
    assert emitted == [("request", {"status": "queued", "user_id": 8,
                                    "serial": "1549",
                                    "detail": "#41 change_proxy: asked by ali"})]


def test_a_quick_command_is_not_measured_against_a_builds_clock():
    """A boot that a restart orphaned sat on the Requests page spinning for
    two hours, because the sweep used twice the build budget for every row
    (2026-09-06)."""
    conn = _ScriptedConn([None], rowcounts=[1])
    actions_mod.expire_running(conn, older_than=7200,
                               quick=("boot_phone", "control"),
                               quick_after=600)
    sql = conn.sql[0]
    assert "CASE WHEN verb = ANY(%s) THEN %s ELSE %s END" in sql


def test_the_sweep_still_has_one_clock_when_nothing_is_named_quick():
    conn = _ScriptedConn([None], rowcounts=[0])
    assert actions_mod.expire_running(conn, older_than=7200) == 0
    assert "make_interval" in conn.sql[0]
