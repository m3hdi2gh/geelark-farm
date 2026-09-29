"""The Station's web half: routes, gates, JSON answers, the state readers.

Driven over real HTTP against the `web` fixture's server, with the store
faked at the seams the Station reads through (`store.station`,
`store.actions`, `store.users`, and `station_read` itself where the
route is the thing under test). The composition in `station_read` is
tested on its own against a faked store module, and - at the bottom,
under `needs_cluster` - against a real Postgres, so every statement the
Station's web half makes a page from has been parsed by one.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# The store modules the Station reads, imported here, at collection: one
# first imported inside a test that fakes `store.db.Store` would keep the
# fake for the rest of the run (they bind `Store` when they load).
import geelark_farm.store.sessions
import geelark_farm.store.station  # noqa: F401
import geelark_farm.store.verdicts  # noqa: F401
import geelark_farm.web.app as app_mod
from geelark_farm.web import live, pages, station_read
from tests import test_web as _test_web
from tests.test_web import MUTATIONS_ON, FakeStore, _form

#: The real session read, kept before the `web` fixture fakes the seats.
_REAL_FIND = geelark_farm.store.sessions.find

#: The live-server fixture, by assignment so each test may name it.
web = _test_web.web

PAGE = {"X-GF-Station": "page"}
LIVE = {"X-GF-Station": "live"}
FLAG_ON = {"web_mutations": True, "station_for_operators": True}
PRESS = "Pr3ssAb1"
ADMIN = {"id": 7, "username": "mehdi", "role": "admin", "sees": "all",
         "active": True}
OPERATOR = {"id": 9, "username": "sara", "role": "operator", "sees": "own",
            "active": True, "may_take_phones": True, "may_change_proxy": True,
            "may_login_accounts": True}


class OperationalError(Exception):
    """What psycopg raises when the store does not answer, by name."""


class UniqueViolation(Exception):
    """What psycopg raises on a unique index, by name."""


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _fake_state(n: int = 0, **more) -> dict:
    base = {"v": 1, "rev": "r1", "now": 1790000000000 + n,
            "hold_minutes": 60, "late_minutes": 15,
            "me": {"id": 7, "name": "mehdi", "user": "mehdi", "initial": "M",
                   "since": "", "pw": "", "daypart": "morning"},
            "may": {"take": True, "ip": True, "build": True},
            "shelves": {}, "tally": {}, "phones": [], "builds": [],
            "today": [], "notes": [], "build_form": {}}
    base.update(more)
    return base


class Desk:
    """The fakes one test presses against, and what they were asked."""

    def __init__(self, monkeypatch):
        import geelark_farm.runner as runner_mod
        import geelark_farm.store.actions as store_actions
        from geelark_farm.store import station as store_station
        from geelark_farm.web import read, station_pages

        self.mp = monkeypatch
        self.queued: list[dict] = []
        self.rows: dict[int, dict] = {}
        self.states: list[dict] = []
        self.lives: list[dict] = []
        self.recorded: list[dict] = []
        self.scrubbed: list[int] = []
        self.pending: dict | None = None
        self.twin = None
        self.holds = True
        self.ran = None
        self.enqueue_raises: list[Exception] = []
        self.next_id = 800
        self.state_extra: dict = {}
        self.pend_calls: list[tuple] = []
        self.expired: list[str] = []
        self.press_rows: dict[int, dict] = {}

        def enqueue(settings, *, verb, payload, requested_by, idem_key):
            if self.enqueue_raises:
                raise self.enqueue_raises.pop(0)
            self.next_id += 1
            self.queued.append({"id": self.next_id, "verb": verb,
                                "payload": payload, "by": requested_by,
                                "idem_key": idem_key})
            self.rows[self.next_id] = {"id": self.next_id, "result": "",
                                       "requested_by": requested_by,
                                       "status": "queued", "verb": verb}
            return self.next_id

        def record_refused(settings, *, verb, payload, requested_by, reason):
            self.next_id += 1
            self.rows[self.next_id] = {"id": self.next_id, "verb": verb,
                                       "payload": payload, "result": reason,
                                       "requested_by": requested_by,
                                       "status": "refused"}
            return self.next_id

        def settle(settings, req, *, status, result, detail):
            row = self.rows.setdefault(int(req), {"id": int(req)})
            row.update(status=status, result=result, detail=detail)

        def state(settings, user, *, write=True):
            self.states.append({"user": user, "write": write})
            return _fake_state(len(self.states), **self.state_extra)

        def live_(settings, user, serial, said="", note=""):
            self.lives.append({"user": user, "serial": serial, "said": said,
                               "note": note})
            return {"v": 1, "serial": serial, "conn": "off", "lane": "gpt"}

        def power_pending_of(settings, serial, verbs=None):
            self.pend_calls.append((serial, verbs))
            got = self.pending
            if callable(got):
                return got()
            return got

        def expire_power(settings, serial):
            self.expired.append(serial)
            self.pending = None
            return 1

        def record(settings, **kw):
            self.recorded.append(kw)
            self.next_id += 1
            return self.next_id

        monkeypatch.setattr(store_actions, "enqueue", enqueue)
        monkeypatch.setattr(store_actions, "record_refused", record_refused)
        monkeypatch.setattr(store_actions, "pending_for",
                            lambda s, *, verb, needle: self.twin)
        monkeypatch.setattr(store_actions, "pending_any",
                            lambda s, *, verb: None)
        monkeypatch.setattr(store_actions, "one",
                            lambda s, req: self.rows.get(int(req)))
        monkeypatch.setattr(store_actions, "claim", lambda s, req: True)
        monkeypatch.setattr(store_actions, "settle", settle)
        monkeypatch.setattr(runner_mod, "run_now",
                            lambda s, verb, payload: self.ran)
        monkeypatch.setattr(store_station, "holds",
                            lambda s, serial, uid: self.holds)
        monkeypatch.setattr(store_station, "power_pending_of", power_pending_of)
        monkeypatch.setattr(store_station, "expire_power", expire_power)
        monkeypatch.setattr(store_station, "press_of",
                            lambda s, rid: self.press_rows.get(int(rid)))
        monkeypatch.setattr(store_station, "record", record)
        monkeypatch.setattr(store_station, "scrub",
                            lambda s, rid: self.scrubbed.append(rid))
        monkeypatch.setattr(store_station, "build_form", lambda s: {
            "gmails_left": 4, "free_ips": {"gpt": 3, "spotify": 2, "other": 5},
            "stopped": False})
        monkeypatch.setattr(station_read, "state", state)
        monkeypatch.setattr(station_read, "live", live_)
        monkeypatch.setattr(
            station_pages, "station_page",
            lambda st, user: ('<!--station--><input name="csrf" value="'
                              + str(user.get("csrf", "")) + '">'
                              + json.dumps(st, default=str)))
        monkeypatch.setattr(
            station_pages, "live_page",
            lambda lv, user: "<!--live-->" + json.dumps(lv, default=str))
        monkeypatch.setattr(read, "known", lambda s, kind: {})

    def last(self) -> dict:
        return self.queued[-1]


@pytest.fixture
def desk(monkeypatch):
    return Desk(monkeypatch)


def _as(monkeypatch, user: dict) -> dict:
    fresh = dict(user)
    monkeypatch.setattr(FakeStore, "user", fresh)
    return fresh


def _token(client, path: str = "/") -> str:
    """The session's csrf, as a browser learns it: off a page."""
    _, _, body = client.request("GET", path)
    hit = re.search(r'name="csrf" value="([^"]*)"', body)
    return hit.group(1) if hit else ""


def _signed(web_cls, username: str = "mehdi"):
    client = web_cls()
    client.login(username=username)
    client.token = _token(client)
    return client


def _post(client, path, headers=PAGE, **fields):
    fields.setdefault("csrf", client.token)
    fields.setdefault("press", PRESS)
    status, hdrs, body = client.request("POST", path, _form(**fields),
                                        headers=headers)
    return status, dict(hdrs), body


def _get(client, path, headers=None):
    status, hdrs, body = client.request("GET", path, headers=headers)
    return status, dict(hdrs), body


# ================================================================== rail
def test_the_rail_has_the_station_right_after_the_dashboard_for_an_admin(
        web, desk):
    assert pages._RAIL[0][0] == "/" and pages._RAIL[1] == ("/station",
                                                           "Station", "")
    assert "<rect" in pages._ICONS["/station"]
    client = _signed(web)
    _, _, body = _get(client, "/")
    rail = body[body.index("<nav>"):body.index("</nav>")]
    assert rail.index('href="/"') < rail.index('href="/station"') < \
        rail.index('href="/pools/gmail"')
    # An operator's pages carry no rail at all, and the page loop skips
    # the Station for anybody but an admin.
    op = pages.page("x", "", user=dict(OPERATOR, csrf="c", nav={}))
    assert 'href="/station"' not in op


# ================================================================= gates
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_an_operator_is_sent_home_from_every_station_path_while_the_flag_is_off(
        web, desk, monkeypatch):
    _as(monkeypatch, OPERATOR)
    client = _signed(web, "sara")
    for path in ("/station", "/station/state", "/station/phones/1500",
                 "/station/phones/1500/state"):
        status, hdrs, _ = _get(client, path)
        assert status == 303 and hdrs["Location"] == "/", path
        status, hdrs, body = _get(client, path, headers=PAGE)
        assert status == 403 and hdrs["Content-Type"].startswith(
            "application/json")
        assert json.loads(body) == {
            "ok": False, "said": "refused",
            "note": "That belongs to an admin. Nothing was changed.",
            "go": "/"}
    status, _, body = _post(client, "/station/take", lane="gpt")
    assert status == 403 and json.loads(body)["said"] == "refused"
    assert desk.states == [] and desk.queued == []
    # And the dashboard is still the operator's home.
    status, _, body = _get(client, "/")
    assert status == 200 and "<!--station-->" not in body


@pytest.mark.parametrize("web", [FLAG_ON], indirect=True)
def test_with_the_flag_on_an_operators_home_is_the_station_and_the_admins_is_not(
        web, desk, monkeypatch):
    client = _signed(web)
    _, _, body = _get(client, "/")
    assert "<!--station-->" not in body, "an admin's / is the dashboard"
    _as(monkeypatch, OPERATOR)
    client = _signed(web, "sara")
    status, _, body = _get(client, "/")
    assert status == 200 and body.startswith("<!--station-->")
    assert desk.states[-1]["user"]["username"] == "sara"
    status, _, body = _get(client, "/station/state", headers=PAGE)
    assert status == 200 and json.loads(body)["v"] == 1
    # Its presses join the operator's doors.
    from geelark_farm.store import station as store_station

    monkeypatch.setattr(store_station, "serve_lines", lambda s, lanes=(): [])
    monkeypatch.setattr(store_station, "take", lambda s, **k: {
        "action_id": 5, "outcome": "took", "serial": "1500", "lane": "gpt",
        "position": 0, "sentence": "Phone 1500 is yours.", "twice": False})
    status, _, body = _post(client, "/station/take", lane="gpt")
    assert status == 200 and json.loads(body)["said"] == "took"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_state_answers_json_no_store_and_a_304_for_its_etag(web, desk):
    client = _signed(web)
    status, hdrs, body = _get(client, "/station/state", headers=PAGE)
    assert status == 200
    assert hdrs["Content-Type"] == "application/json; charset=utf-8"
    assert hdrs["Cache-Control"] == "no-store"
    assert hdrs["X-Content-Type-Options"] == "nosniff"
    tag = hdrs["ETag"]
    assert re.fullmatch(r'W/"[0-9a-f]{16}"', tag)
    assert json.loads(body)["now"] == 1790000000001
    # The next read's `now` moved and nothing else did: the same tag.
    status, hdrs, body = _get(client, "/station/state",
                              headers=dict(PAGE, **{"If-None-Match": tag}))
    assert status == 304 and body == "" and hdrs["ETag"] == tag
    desk.state_extra = {"phones": [{"serial": "1500"}]}
    status, hdrs, _ = _get(client, "/station/state",
                           headers=dict(PAGE, **{"If-None-Match": tag}))
    assert status == 200 and hdrs["ETag"] != tag
    assert all(s["write"] for s in desk.states)
    # A state a vital read could not make is not sent as the truth.
    desk.state_extra = {station_read.PARTIAL: True}
    status, _, body = _get(client, "/station/state", headers=PAGE)
    assert status == 503 and json.loads(body)["said"] == "down"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_station_page_is_never_drawn_empty_from_a_partial_state(web, desk):
    """A vital read that failed makes the page the store-down page, the
    way the Live tab's document does - never a Station that says "No
    phone yet" with an open Build over a farm that did not answer."""
    client = _signed(web)
    status, _, body = _get(client, "/station")
    assert status == 200 and "<!--station-->" in body
    desk.state_extra = {station_read.PARTIAL: True}
    status, hdrs, body = _get(client, "/station")
    assert status == 503, status
    assert "<!--station-->" not in body
    assert "Location" not in hdrs
    assert body == pages.store_down_page()


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_head_request_writes_nothing_and_sends_no_body(web, desk):
    client = _signed(web)
    for path in ("/station/state", "/station"):
        status, hdrs, body = client.request("HEAD", path, headers=PAGE)
        assert status == 200 and body == "", path
        assert int(dict(hdrs)["Content-Length"]) > 0
    assert [s["write"] for s in desk.states] == [False, False]


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_every_gate_answers_json_to_the_station(web, desk, monkeypatch):
    client = web()
    # No session, GET and POST.
    status, _, body = _get(client, "/station/state", headers=PAGE)
    assert status == 401 and json.loads(body) == {
        "ok": False, "said": "signed-out", "note": "You are signed out.",
        "go": "/login"}
    status, _, body = client.request("POST", "/station/take",
                                     _form(csrf="x", lane="gpt"), headers=PAGE)
    assert status == 401 and json.loads(body)["go"] == "/login"
    # Without the header nothing changed: the login page.
    status, hdrs, _ = _get(client, "/station/state")
    assert status == 303 and dict(hdrs)["Location"] == "/login"
    client = _signed(web)
    # A stale session.
    status, _, body = _post(client, "/station/take", csrf="wrong", lane="gpt")
    assert status == 403 and json.loads(body) == {
        "ok": False, "said": "stale",
        "note": "Your session changed - reload the page.", "go": None}
    # A foreign Origin.
    status, _, body = _post(client, "/phones/1500/boot",
                            headers=dict(LIVE, Origin="http://evil.example"),
                            station="1")
    assert status == 403 and json.loads(body)["said"] == "origin"
    assert json.loads(body)["note"] == ("That came from another site. "
                                        "Nothing was changed.")
    # A store error and a crash, on a GET and on a POST.
    from geelark_farm.store import station as store_station

    for raised, word, code in ((OperationalError("gone"), "down", 503),
                               (RuntimeError("boom"), "broke", 500)):
        def boom(*a, _e=raised, **k):
            raise _e

        monkeypatch.setattr(station_read, "state", boom)
        status, hdrs, body = _get(client, "/station/state", headers=PAGE)
        assert status == code and json.loads(body)["said"] == word
        assert hdrs["Content-Type"].startswith("application/json")
        monkeypatch.setattr(store_station, "holds", boom)
        status, hdrs, body = _post(client, "/phones/1500/boot", headers=LIVE,
                                   station="1")
        assert status == code and json.loads(body)["said"] == word
        status, hdrs, body = _post(client, "/station/phones/1500/back")
        assert status == code and json.loads(body)["said"] == word
        # And the page without the header still gets its page.
        status, hdrs, body = _get(client, "/station/state")
        assert status == code and hdrs["Content-Type"].startswith("text/html")
    # A person who must choose their own password first.
    monkeypatch.setitem(FakeStore.user, "must_change_password", True)
    client = web()
    client.login()
    token = _token(client, "/password")
    assert token
    status, _, body = _get(client, "/station/state", headers=PAGE)
    assert status == 403 and json.loads(body) == {
        "ok": False, "said": "password",
        "note": "Choose your own password first.", "go": "/password"}
    status, _, body = client.request("POST", "/station/take",
                                     _form(csrf=token, lane="gpt"), headers=PAGE)
    assert status == 403 and json.loads(body)["said"] == "password"
    # An unknown Station path.
    monkeypatch.setitem(FakeStore.user, "must_change_password", False)
    status, _, body = _get(client, "/station/nothing", headers=PAGE)
    assert status == 404 and json.loads(body) == {
        "ok": False, "said": "none", "note": "Nothing here."}
    status, _, body = client.request("POST", "/station/nothing",
                                     _form(csrf=token), headers=PAGE)
    assert status == 404 and json.loads(body)["said"] == "none"


def test_actions_switched_off_answer_json_to_the_station(web, desk):
    client = _signed(web)
    for path, fields in (("/station/take", {"lane": "gpt"}),
                         ("/station/build", {"kind": "gpt"}),
                         ("/phones/1500/boot", {"station": "1"}),
                         ("/station/phones/1500/back", {})):
        status, _, body = _post(client, path, **fields)
        assert status == 403, path
        assert json.loads(body) == {"ok": False, "said": "off",
                                    "note": "Actions are not switched on yet."}
    assert desk.queued == []


# ============================================================== presses
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_station_press_answers_json_and_a_plain_one_still_redirects(
        web, desk, monkeypatch):
    from geelark_farm.store import wanted as store_wanted

    monkeypatch.setattr(store_wanted, "dismiss",
                        lambda s, ident, *, user_id, admin: True)
    client = _signed(web)
    presses = (
        ("/phones/1500/boot", {"station": "1"}, "/station/phones/1500"),
        ("/phones/1500/proxy", {"station": "1", "keep_power": "1",
                                "was": "on"}, "/station"),
        ("/phones/1500/state", {"state": "decline", "sure": "1",
                                "where": "station"}, "/station"),
        ("/station/phones/1500/back", {"where": "station"}, "/station"),
        ("/station/build", {"kind": "gpt", "gmail_mode": "auto",
                            "acct_mode": "none", "ip_mode": "auto"}, "/station"),
        ("/wishes/12/dismiss", {}, "/"),
    )
    for path, fields, back in presses:
        status, hdrs, body = _post(client, path, headers=None, **fields)
        assert status == 303, path
        assert hdrs["Location"].startswith(back + "?said="), (path, hdrs)
        status, hdrs, body = _post(client, path, **fields)
        assert status == 200 and hdrs["Content-Type"].startswith(
            "application/json"), path
        got = json.loads(body)
        assert set(got) >= {"ok", "said", "req", "note", "pending"}, path
        assert got["ok"] is True, (path, got)
    boot = [q for q in desk.queued if q["verb"] == "boot_phone"][0]
    assert boot["payload"]["station"] is True
    proxy = [q for q in desk.queued if q["verb"] == "change_proxy"][0]
    assert {k: proxy["payload"][k] for k in ("boot", "keep_power", "station",
                                            "was_on_page")} == {
        "boot": False, "keep_power": True, "station": True, "was_on_page": "on"}
    verdict = [q for q in desk.queued if q["verb"] == "set_phone_state"][0]
    assert verdict["payload"]["mine"] is True
    assert verdict["payload"]["where"] == "station"
    assert verdict["payload"]["button"] == "decline"
    assert verdict["payload"]["state"] == "failed"
    back = [q for q in desk.queued if q["verb"] == "give_back"][0]
    assert back["payload"]["where"] == "station"
    assert back["idem_key"].startswith(f"giveback:1500:7:{PRESS}:")
    # The dismiss the Station pressed is recorded; the plain one is not.
    assert [r["verb"] for r in desk.recorded] == ["dismiss_build"]
    assert desk.recorded[0]["payload"] == {"wanted_id": 12}
    assert desk.recorded[0]["status"] == "done"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_answer_reads_the_word_the_request_and_the_note(web, desk):
    client = _signed(web)
    desk.ran = ("done", "Phone 1500 is back on the GPT shelf.",
                {"serial": "1500", "lane": "gpt", "off": 3})
    status, _, body = _post(client, "/station/phones/1500/back")
    got = json.loads(body)
    assert status == 200
    assert got["said"] == "gave-back" and got["req"] == desk.last()["id"]
    assert got["note"] == "Phone 1500 is back on the GPT shelf."
    assert got["ok"] is True and got["pending"] is False
    # Queued: no sentence on the row yet, so the table's.
    desk.ran = None
    got = json.loads(_post(client, "/station/phones/1500/back", press="p2")[2])
    assert got["said"] == "queued" and got["pending"] is True
    assert got["note"] == pages._DASH_SAID["queued"]
    # A word with no request at all.
    desk.twin = 4242
    got = json.loads(_post(client, "/station/phones/1500/back", press="p3")[2])
    assert got["said"] == "already" and got["req"] == 4242
    assert got["note"] == pages._DASH_SAID["already"] and got["pending"] is True


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_json_answers_carry_the_fresh_state_for_the_page_and_the_live_state_for_a_tab(
        web, desk):
    client = _signed(web)
    got = json.loads(_post(client, "/station/phones/1500/back")[2])
    assert got["state"]["v"] == 1 and "live" not in got
    assert desk.states[-1]["write"] is True
    got = json.loads(_post(client, "/phones/1500/boot", headers=LIVE,
                           station="1", press="p2")[2])
    assert got["live"] == {"v": 1, "serial": "1500", "conn": "off",
                           "lane": "gpt"}
    assert "state" not in got
    got = json.loads(_post(client, "/station/phones/1500/back", headers=LIVE,
                           press="p3")[2])
    assert got["live"]["serial"] == "1500"
    # A state made with a vital read missing is left out, not sent.
    desk.state_extra = {station_read.PARTIAL: True}
    got = json.loads(_post(client, "/station/phones/1500/back", press="p4")[2])
    assert "state" not in got and got["ok"] is True


@pytest.mark.parametrize("web", [FLAG_ON], indirect=True)
def test_a_request_sentence_is_read_only_by_who_asked_for_it(
        web, desk, monkeypatch):
    desk.rows[55] = {"id": 55, "result": "phone 1500 is with ali",
                     "requested_by": 99, "status": "refused"}
    desk.rows[56] = {"id": 56, "result": "your own words",
                     "requested_by": 9, "status": "refused"}
    _as(monkeypatch, OPERATOR)
    client = web()
    client.login(username="sara")
    _get(client, "/station/phones/1500?said=no:55")
    assert desk.lives[-1]["said"] == "no:55" and desk.lives[-1]["note"] == ""
    _get(client, "/station/phones/1500?said=no:56")
    assert desk.lives[-1]["note"] == "your own words"
    # The method itself: the dashboard's callers pass no user and read on.
    handler = app_mod._Handler.__new__(app_mod._Handler)
    handler.settings = None
    assert handler._said_note("no:55", dict(OPERATOR)) == ""
    assert handler._said_note("no:55", dict(ADMIN)) == "phone 1500 is with ali"
    assert handler._said_note("no:55") == "phone 1500 is with ali"
    assert handler._said_note("no") == ""


# ================================================================ take
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_take_is_one_store_call_with_the_press_as_its_key_and_needs_the_tick(
        web, desk, monkeypatch):
    from geelark_farm.store import station as store_station

    calls = []
    answer = {"action_id": 61, "outcome": "took", "serial": "1500",
              "lane": "gpt", "position": 0,
              "sentence": "Phone 1500 is yours. Press Boot to switch it on.",
              "twice": False}
    monkeypatch.setattr(store_station, "serve_lines",
                        lambda s, lanes=("gpt", "spotify"): calls.append(
                            ("serve", lanes)) or [])
    monkeypatch.setattr(store_station, "take",
                        lambda s, **k: calls.append(("take", k)) or dict(answer))
    monkeypatch.setattr(store_station, "leave_line",
                        lambda s, **k: calls.append(("leave", k)) or {
                            "action_id": 62, "outcome": "left",
                            "sentence": "You left the line for a GPT phone.",
                            "twice": False})
    client = _signed(web)
    status, _, body = _post(client, "/station/take", lane="gpt")
    got = json.loads(body)
    assert status == 200
    assert calls == [("serve", ("gpt",)),
                     ("take", {"lane": "gpt", "user_id": 7, "by": "mehdi",
                               "idem_key": f"take:gpt:7:{PRESS}"})]
    assert got["said"] == "took" and got["ok"] is True and got["req"] == 61
    assert got["note"] == answer["sentence"] and "state" in got
    # In line, and a double press.
    answer.update(outcome="line", serial="", position=2,
                  sentence="You are number 2 in line for the next GPT phone.")
    got = json.loads(_post(client, "/station/take", lane="gpt")[2])
    assert got["said"] == "in-line" and got["ok"] is True
    answer.update(twice=True)
    got = json.loads(_post(client, "/station/take", lane="gpt")[2])
    assert got["said"] == "twice" and got["ok"] is True
    assert got["note"] == answer["sentence"]
    answer.update(outcome="no", twice=False,
                  sentence="Nothing on the GPT shelf, and nothing is being built.")
    got = json.loads(_post(client, "/station/take", lane="gpt")[2])
    assert got["said"] == "no" and got["ok"] is False
    assert got["note"] == answer["sentence"]
    # Leaving.
    got = json.loads(_post(client, "/station/line/leave", lane="gpt")[2])
    assert got["said"] == "left" and got["ok"] is True
    assert calls[-1] == ("leave", {"lane": "gpt", "user_id": 7, "by": "mehdi",
                                   "idem_key": f"leave:gpt:7:{PRESS}"})
    # A lane that is not one.
    n = len(calls)
    got = json.loads(_post(client, "/station/take", lane="other")[2])
    assert got == {"ok": False, "said": "no", "req": None,
                   "note": "Pick GPT or Spotify.", "pending": False}
    assert len(calls) == n
    # Without the header: back to the Station.
    status, hdrs, _ = _post(client, "/station/take", headers=None, lane="gpt")
    assert status == 303 and hdrs["Location"] == "/station"


@pytest.mark.parametrize("web", [FLAG_ON], indirect=True)
def test_a_take_without_the_tick_is_recorded_and_leaving_needs_none(
        web, desk, monkeypatch):
    """Refused and written down, and nothing taken. A person whose tick
    went while they waited can still step out of the line."""
    from geelark_farm.store import station as store_station

    took = []
    monkeypatch.setattr(store_station, "take",
                        lambda s, **k: took.append(k) or {})
    monkeypatch.setattr(store_station, "serve_lines",
                        lambda s, lanes=(): took.append(lanes) or [])
    monkeypatch.setattr(store_station, "leave_line", lambda s, **k: {
        "action_id": 62, "outcome": "left",
        "sentence": "You left the line for a GPT phone.", "twice": False})
    _as(monkeypatch, dict(OPERATOR, may_take_phones=False))
    client = _signed(web, "sara")
    got = json.loads(_post(client, "/station/take", lane="spotify")[2])
    assert got["said"] == "refused" and got["ok"] is False
    assert got["note"] == pages._DASH_SAID["refused"] and "state" in got
    assert took == []
    assert desk.recorded[-1] == {
        "verb": "take_phone", "payload": {"lane": "spotify"},
        "requested_by": 9, "status": "refused",
        "result": "sara may not do this - permission may_take_phones is off",
        "idem_key": f"take:spotify:9:{PRESS}"}
    got = json.loads(_post(client, "/station/line/leave", lane="gpt")[2])
    assert got["said"] == "left" and got["ok"] is True


# ============================================================= verdicts
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_verdict_from_the_station_needs_the_phone_to_be_yours_even_for_an_admin(
        web, desk):
    client = _signed(web)
    desk.holds = False
    for where in ("station", "station-live"):
        got = json.loads(_post(client, "/phones/1500/state", state="done",
                               sure="1", where=where)[2])
        assert got["said"] == "no" and got["ok"] is False
        assert got["note"] == "phone 1500 is not yours any more"
    assert desk.queued == []
    desk.holds = True
    for key in ("done", "decline", "or", "auth", "failed"):
        got = json.loads(_post(client, "/phones/1500/state", state=key,
                               sure="1", where="station-live",
                               press="p" + key)[2])
        assert got["said"] == "queued", key
    assert [q["payload"]["button"] for q in desk.queued] == [
        "done", "decline", "or", "auth", "failed"]
    assert [q["payload"]["state"] for q in desk.queued] == [
        "done", "failed", "failed", "failed", "failed"]
    assert all(q["payload"]["mine"] is True
               and q["payload"]["where"] == "station-live" for q in desk.queued)
    # The Station has five keys, not the dashboard's Take and Release.
    status, _, body = _post(client, "/phones/1500/state", state="taken",
                            sure="1", where="station")
    assert status == 404 and json.loads(body)["said"] == "none"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_verdict_without_sure_is_a_409_for_the_station_and_a_page_elsewhere(
        web, desk):
    client = _signed(web)
    status, _, body = _post(client, "/phones/1500/state", state="auth",
                            where="station")
    assert status == 409 and json.loads(body) == {
        "ok": False, "said": "ask",
        "note": "Press the key a second time to close the phone."}
    status, hdrs, body = _post(client, "/phones/1500/state", headers=None,
                               state="decline", where="dash")
    assert status == 200 and hdrs["Content-Type"].startswith("text/html")
    assert "Mark phone 1500 declined?" in body
    assert desk.queued == []


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_second_verdict_with_another_button_is_refused_and_the_same_one_is_pending(
        web, desk):
    client = _signed(web)
    desk.twin = 70
    desk.press_rows[70] = {"id": 70, "verb": "set_phone_state",
                           "status": "running", "button": "done"}
    got = json.loads(_post(client, "/phones/1500/state", state="decline",
                           sure="1", where="station")[2])
    assert got["ok"] is False and got["said"] == "no"
    assert got["note"] == "phone 1500 is already being closed as Done"
    got = json.loads(_post(client, "/phones/1500/state", state="done",
                           sure="1", where="station")[2])
    assert got["ok"] is True and got["said"] == "already" and got["req"] == 70
    assert got["pending"] is True
    # The dashboard's own verdicts never ask.
    desk.press_rows.clear()
    status, hdrs, _ = _post(client, "/phones/1500/state", headers=None,
                            state="decline", sure="1", where="dash")
    assert hdrs["Location"] == "/?said=already:70"
    assert desk.queued == []


# ============================================================ power
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_release_waits_for_a_pending_boot_or_change_ip(web, desk):
    """D1 took the power-off out of the one-press index: a dashboard
    Release beside a pending Boot could switch the phone off first, and
    the Boot then started it again and marked it taken."""
    client = _signed(web)
    for verb, words in (("boot_phone", "phone 1500 is booting - wait for it"),
                        ("change_proxy",
                         "phone 1500 is changing its IP - wait for it")):
        desk.pending = {"id": 5, "verb": verb, "stale": False}
        status, hdrs, _ = _post(client, "/phones/1500/state", headers=None,
                                state="unused", where="dash")
        assert status == 303 and "said=no:" in hdrs["Location"], verb
        assert desk.rows[desk.next_id]["result"] == words
        assert desk.queued == [], verb
        assert desk.pend_calls[-1] == ("1500", ("boot_phone", "change_proxy"))
    # A pending power-off is no reason to wait: the Release goes through
    # and queues its own.
    desk.pending = None
    _post(client, "/phones/1500/state", headers=None, state="unused",
          where="dash")
    assert [q["verb"] for q in desk.queued][:1] == ["power_off_phone"]


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_boot_and_change_ip_refuse_each_other_while_one_is_pending(web, desk):
    client = _signed(web)
    desk.pending = {"id": 5, "verb": "change_proxy", "stale": False}
    got = json.loads(_post(client, "/phones/1500/boot", headers=LIVE,
                           station="1")[2])
    assert got["ok"] is False
    assert got["note"] == "phone 1500 is changing its IP - wait for it"
    desk.pending = {"id": 6, "verb": "boot_phone", "stale": False}
    got = json.loads(_post(client, "/phones/1500/proxy", station="1",
                           keep_power="1", was="off")[2])
    assert got["note"] == "phone 1500 is booting - wait for it"
    desk.pending = {"id": 7, "verb": "power_off_phone", "stale": False}
    got = json.loads(_post(client, "/phones/1500/boot", headers=LIVE,
                           station="1")[2])
    assert got["note"] == ("phone 1500 is switching off - press Boot again in "
                           "a moment")
    assert desk.queued == []
    # The dashboard's Boot and Change IP refuse the same way.
    desk.pending = {"id": 5, "verb": "change_proxy", "stale": False}
    status, hdrs, _ = _post(client, "/phones/1500/boot", headers=None)
    assert status == 303 and "/phones/1500/live?said=no:" in hdrs["Location"]
    assert desk.rows[desk.next_id]["result"] == (
        "phone 1500 is changing its IP - wait for it")
    desk.pending = {"id": 6, "verb": "boot_phone", "stale": False}
    status, hdrs, _ = _post(client, "/phones/1500/proxy", headers=None)
    assert status == 303 and hdrs["Location"].startswith("/?said=no:")
    assert desk.queued == []
    # A stale one is an orphan: closed, and the press goes through.
    desk.pending = {"id": 5, "verb": "change_proxy", "stale": True}
    got = json.loads(_post(client, "/phones/1500/boot", headers=LIVE,
                           station="1", press="p2")[2])
    assert desk.expired == ["1500"] and got["said"] == "queued"
    assert desk.last()["verb"] == "boot_phone"
    # The unique index, when two presses raced past the check.
    seq = [None, {"id": 90, "verb": "change_proxy", "stale": False}]
    desk.pending = lambda: seq.pop(0) if seq else None
    desk.enqueue_raises = [UniqueViolation("actions_one_power_press")]
    got = json.loads(_post(client, "/phones/1500/boot", headers=LIVE,
                           station="1", press="p3")[2])
    assert got["ok"] is False
    assert got["note"] == "phone 1500 is changing its IP - wait for it"
    seq[:] = [None, {"id": 91, "verb": "boot_phone", "stale": False}]
    desk.enqueue_raises = [UniqueViolation("actions_one_power_press")]
    got = json.loads(_post(client, "/phones/1500/boot", headers=LIVE,
                           station="1", press="p4")[2])
    assert got["said"] == "already" and got["req"] == 91
    # Gone by the time it was asked: the press is tried once more.
    seq[:] = []
    desk.enqueue_raises = [UniqueViolation("actions_one_power_press")]
    n = len(desk.queued)
    got = json.loads(_post(client, "/phones/1500/boot", headers=LIVE,
                           station="1", press="p5")[2])
    assert got["said"] == "queued" and len(desk.queued) == n + 1
    # Anything else from the queue is raised as it always was.
    desk.enqueue_raises = [RuntimeError("no")]
    status, _, body = _post(client, "/phones/1500/boot", headers=LIVE,
                            station="1", press="p6")
    assert status == 500 and json.loads(body)["said"] == "broke"
    # And a Station Boot on a phone that is not yours never gets that far.
    desk.holds = False
    got = json.loads(_post(client, "/phones/1500/boot", headers=LIVE,
                           station="1", press="p7")[2])
    assert got["note"] == "phone 1500 is not yours any more"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_station_beat_and_close_are_scoped_to_the_holder(
        web, desk, monkeypatch):
    from geelark_farm.store import person
    from geelark_farm.store import station as store_station

    calls = []
    monkeypatch.setattr(store_station, "watch",
                        lambda s, serial, uid, grace: calls.append(
                            ("station.watch", serial, uid, grace)) or True)
    monkeypatch.setattr(store_station, "tab_closed",
                        lambda s, serial, uid: calls.append(
                            ("station.closed", serial, uid)) or True)
    monkeypatch.setattr(person, "watch",
                        lambda s, serial, uid=None: calls.append(
                            ("person.watch", serial, uid)) or False)
    monkeypatch.setattr(person, "tab_closed",
                        lambda s, serial, uid=None: calls.append(
                            ("person.closed", serial, uid)) or True)

    class Timer:
        def __init__(self, seconds, fn):
            self.daemon = False

        def start(self):
            return None

    monkeypatch.setattr(app_mod.threading, "Timer", Timer)
    client = _signed(web)
    status, _, body = client.request("POST", "/phones/1500/watching",
                                     _form(csrf=client.token, station="1"))
    assert status == 200 and body == "watching"
    status, _, body = client.request("POST", "/phones/1500/watching",
                                     _form(csrf=client.token))
    assert status == 410 and body == "released"
    client.request("POST", "/phones/1500/closing",
                   _form(csrf=client.token, station="1"))
    client.request("POST", "/phones/1500/closing", _form(csrf=client.token))
    assert calls == [("station.watch", "1500", 7, 180),
                     ("person.watch", "1500", 7),
                     ("station.closed", "1500", 7),
                     ("person.closed", "1500", 7)]


# ============================================================= build
_SECRET = "Kx82!mnQ"


def _build(client, press=PRESS, **fields):
    base = {"kind": "gpt", "gmail_mode": "auto", "gmail_line": "",
            "acct_mode": "none", "acct_line": "", "ip_mode": "auto",
            "ip_line": ""}
    base.update(fields)
    return json.loads(_post(client, "/station/build", press=press, **base)[2])


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
@pytest.mark.parametrize("fields, want", [
    ({}, {"gmail": "", "no_gmail": False, "app": "", "install_app": False,
          "app_account": "", "purpose": "gpt", "proxy_name": "",
          "proxy_typed": False, "station": True, "app_secret": "",
          "carry_address": "", "carry_password": ""}),
    ({"gmail_mode": "manual", "gmail_line": f"A.B@Gmail.com:{_SECRET}:KEY 2X"},
     {"gmail": "a.b@gmail.com", "gmail_password": _SECRET,
      "gmail_secret": "KEY 2X", "gmail_typed": True, "no_gmail": False}),
    ({"gmail_mode": "none", "kind": "spotify"},
     {"no_gmail": True, "gmail": "", "purpose": "spotify"}),
    ({"acct_mode": "manual", "acct_line": f"M@x.com:{_SECRET}"},
     {"app": "chatgpt", "install_app": True, "app_account": "m@x.com",
      "app_category": "", "app_password": _SECRET, "app_typed": True}),
    ({"acct_mode": "manual", "acct_line": "m@x.com"},
     {"app": "chatgpt", "app_account": "m@x.com", "app_category": "eco",
      "app_password": ""}),
    ({"kind": "spotify", "gmail_mode": "none", "acct_mode": "manual",
      "acct_line": f"s@x.com:{_SECRET}"},
     {"app": "spotify", "install_app": True, "app_account": "s@x.com",
      "app_category": "normal", "app_password": _SECRET, "no_gmail": True}),
    ({"kind": "other", "acct_mode": "manual", "acct_line": f"O@x.com:{_SECRET}"},
     {"app": "", "install_app": False, "app_account": "", "purpose": "other",
      "carry_address": "o@x.com", "carry_password": _SECRET}),
    ({"kind": "other", "acct_mode": "manual", "acct_line": "o@x.com"},
     {"carry_address": "o@x.com", "carry_password": ""}),
    ({"ip_mode": "manual", "ip_line": "h.x:1080:u:pw"},
     {"proxy_name": "h.x:1080:u:pw", "proxy_typed": True}),
])
def test_the_build_dialog_maps_onto_build_by_hand(web, desk, fields, want):
    client = _signed(web)
    got = _build(client, **fields)
    assert got["ok"] is True and got["said"] == "queued"
    payload = desk.last()["payload"]
    assert desk.last()["verb"] == "build_by_hand"
    for key, value in want.items():
        assert payload[key] == value, key
    assert payload["by"] == "mehdi" and payload["by_id"] == 7
    assert desk.last()["idem_key"].startswith(
        f"byhand:{fields.get('kind', 'gpt')}:7:{PRESS}:")
    assert _SECRET not in desk.last()["idem_key"]


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
@pytest.mark.parametrize("fields, note, field", [
    ({"kind": "phone"}, "Pick what the phone is for.", ""),
    ({"gmail_mode": "manual", "gmail_line": "a@b.com"},
     "Type it as email:password:2FA key.", "gmail"),
    ({"acct_mode": "manual", "acct_line": "not an address"},
     "Type it as email:password, or the email alone.", "acct"),
    ({"ip_mode": "manual", "ip_line": "h.x:1080:user"},
     "Type it as host:port:user:password.", "ip"),
    ({"gmail_mode": "none", "acct_mode": "manual", "acct_line": "m@x.com:pw"},
     "A GPT account needs a Gmail on the phone – set Gmail to Auto or Manual.",
     "acct"),
    ({"kind": "spotify", "acct_mode": "manual", "acct_line": "s@x.com:pw"},
     "A Spotify account goes on a phone with no Gmail – set Gmail to None.",
     "acct"),
    ({"kind": "spotify", "gmail_mode": "none", "acct_mode": "manual",
      "acct_line": "s@x.com"},
     "A Spotify account needs its password: email:password.", "acct"),
    ({"gmails_left": 0},
     "No free Gmail in the pool – type one under Manual, or pick None.",
     "gmail"),
    ({"free_gpt": 0}, "No free GPT IP – type one under Manual.", "ip"),
    ({"kind": "spotify", "free_spotify": 0},
     "No free Spotify IP – type one under Manual.", "ip"),
    ({"kind": "other", "free_other": 0},
     "No free IP – type one under Manual.", "ip"),
])
def test_the_build_dialog_refuses_what_no_flow_supports_in_its_words(
        web, desk, monkeypatch, fields, note, field):
    from geelark_farm.store import station as store_station

    fields = dict(fields)
    form = {"gmails_left": fields.pop("gmails_left", 4),
            "free_ips": {"gpt": fields.pop("free_gpt", 3),
                         "spotify": fields.pop("free_spotify", 2),
                         "other": fields.pop("free_other", 5)},
            "stopped": False}
    monkeypatch.setattr(store_station, "build_form", lambda s: form)
    client = _signed(web)
    got = _build(client, **fields)
    assert got["ok"] is False and got["said"] == "no"
    assert got["note"] == note and got["field"] == field
    assert desk.queued == []
    refused = desk.rows[got["req"]]
    assert refused["verb"] == "build_by_hand"
    assert set(refused["payload"]) == {"kind", "gmail_mode", "acct_mode",
                                       "ip_mode", "by", "by_id"}


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_stopped_farm_is_not_a_reason_to_refuse_a_build(
        web, desk, monkeypatch):
    from geelark_farm.store import station as store_station

    monkeypatch.setattr(store_station, "build_form", lambda s: {
        "gmails_left": 4, "free_ips": {"gpt": 3, "spotify": 2, "other": 5},
        "stopped": True})
    client = _signed(web)
    assert _build(client)["ok"] is True
    assert desk.last()["verb"] == "build_by_hand"


@pytest.mark.parametrize("web", [FLAG_ON], indirect=True)
def test_a_build_without_the_tick_never_records_a_typed_secret(
        web, desk, monkeypatch):
    _as(monkeypatch, dict(OPERATOR, may_login_accounts=False))
    client = _signed(web, "sara")
    got = _build(client, gmail_mode="manual",
                 gmail_line=f"a@b.com:{_SECRET}:KEY",
                 acct_mode="manual", acct_line=f"m@x.com:{_SECRET}",
                 ip_mode="manual", ip_line=f"h.x:1080:u:{_SECRET}")
    assert got["ok"] is False
    assert got["note"] == ("sara may not do this - permission "
                           "may_login_accounts is off")
    assert desk.queued == []
    row = desk.rows[got["req"]]
    assert _SECRET not in json.dumps(row["payload"])
    assert row["payload"] == {"kind": "gpt", "gmail_mode": "manual",
                              "acct_mode": "manual", "ip_mode": "manual",
                              "by": "sara", "by_id": 9}


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_retried_build_press_is_one_request(web, desk, monkeypatch):
    from geelark_farm.web import read

    seen = []

    def known(settings, kind):
        seen.append(kind)
        # The first press put the Gmail into the pool.
        return {} if len(seen) == 1 else {"a@b.com": "free"}

    monkeypatch.setattr(read, "known", known)
    client = _signed(web)
    line = f"a@b.com:{_SECRET}:KEY"
    _build(client, gmail_mode="manual", gmail_line=line)
    _build(client, gmail_mode="manual", gmail_line=line)
    first, second = desk.queued[-2:]
    assert first["payload"]["gmail_typed"] is True
    assert second["payload"]["gmail_typed"] is False
    assert first["idem_key"] == second["idem_key"]
    # Two real presses are two requests.
    _build(client, press="another", gmail_mode="manual", gmail_line=line)
    assert desk.queued[-1]["idem_key"] != first["idem_key"]


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_station_builds_secrets_leave_the_request_row_once_it_ran(
        web, desk):
    client = _signed(web)
    desk.ran = ("done", "asked for a GPT phone", {"wanted_id": 3})
    got = _build(client)
    assert got["said"] == "asked" and got["ok"] is True
    assert desk.scrubbed == [desk.last()["id"]]
    # Queued for the lane: the next state read scrubs it.
    desk.ran = None
    _build(client, press="p2")
    assert desk.scrubbed == [desk.queued[0]["id"]]
    # The dashboard's own builds keep today's rows.
    desk.ran = ("done", "asked", {"wanted_id": 4})
    _post(client, "/phones/build", headers=None, gmail="", account_kind="")
    assert desk.last()["payload"].get("station") is None
    assert desk.scrubbed == [desk.queued[0]["id"]]


def test_the_state_read_scrubs_the_persons_builds_and_head_does_not(
        monkeypatch, make_settings):
    fake = _fake_store(monkeypatch)
    user = dict(ADMIN, mutations=True)
    station_read.state(make_settings(store_enabled=True), user)
    assert fake.calls[:3] == [("scrub_mine", 7), ("stamp_line", 7),
                              ("serve_lines",)]
    fake.calls.clear()
    station_read.state(make_settings(store_enabled=True), user, write=False)
    assert not {"scrub_mine", "stamp_line", "serve_lines"} & {
        c[0] for c in fake.calls}


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_calling_a_build_off_is_one_request_for_that_wish(web, desk,
                                                          monkeypatch):
    import geelark_farm.store.actions as store_actions

    needles = []
    monkeypatch.setattr(store_actions, "pending_for",
                        lambda s, *, verb, needle: needles.append(
                            (verb, needle)) or None)
    client = _signed(web)
    got = json.loads(_post(client, "/station/builds/12/off")[2])
    assert got["said"] == "queued"
    assert desk.last()["verb"] == "call_off_build"
    assert {k: desk.last()["payload"][k] for k in ("wanted_id", "name",
                                                   "admin")} == {
        "wanted_id": 12, "name": "wish 12", "admin": True}
    assert desk.last()["idem_key"].startswith(f"calloff:12:7:{PRESS}:")
    assert needles == [("call_off_build", "wish 12")]
    status, _, body = _post(client, "/station/builds/x1/off")
    assert status == 404 and json.loads(body)["said"] == "none"
    desk.ran = ("done", "The build was called off.", {"wanted_id": 12})
    got = json.loads(_post(client, "/station/builds/12/off", press="p2")[2])
    assert got["said"] == "called-off" and got["note"] == (
        "The build was called off.")


# ============================================================ profile
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_profile_saves_a_name_a_username_and_a_password_in_the_prototypes_words(
        web, desk, monkeypatch):
    from geelark_farm.store import users as store_users

    me = _as(monkeypatch, dict(ADMIN))
    asked = []
    monkeypatch.setattr(store_users, "set_name",
                        lambda s, uid, name: asked.append(("name", uid, name))
                        or name)

    def set_username(s, uid, name):
        asked.append(("user", uid, name))
        if name == "taken":
            raise ValueError("That username is taken.")
        return name.strip().lower()

    monkeypatch.setattr(store_users, "set_username", set_username)
    wrong = {"on": True}

    def change_password(s, uid, current, new, *, token, hours):
        asked.append(("pw", uid, token, hours))
        if wrong["on"]:
            raise store_users.WrongPassword("That is not your current password.")
        return "t-new"

    monkeypatch.setattr(store_users, "change_password", change_password)
    client = _signed(web)
    # Name.
    got = json.loads(_post(client, "/station/me/name", name="  Sara  ")[2])
    assert got["ok"] is True and got["said"] == "saved"
    assert got["note"] == "Name saved." and got["me"]["name"] == "Sara"
    assert asked[-1] == ("name", 7, "Sara")
    assert desk.recorded[-1]["verb"] == "profile_name"
    assert desk.recorded[-1]["payload"] == {"name": "Sara"}
    got = json.loads(_post(client, "/station/me/name", name="   ")[2])
    assert got == {"ok": False, "said": "bad",
                   "note": "Your name cannot be empty.", "field": "name"}
    # Username: one field, no current password.
    got = json.loads(_post(client, "/station/me/username", username="Sara.K")[2])
    assert got["ok"] is True and got["me"]["user"] == "sara.k"
    assert got["note"] == "Username saved. Use it the next time you sign in."
    assert desk.recorded[-1]["payload"] == {"username": "sara.k",
                                            "was": "mehdi"}
    got = json.loads(_post(client, "/station/me/username", username="taken")[2])
    assert got == {"ok": False, "said": "bad",
                   "note": "That username is taken.", "field": "user"}
    # Password: the shape first, in the prototype's order.
    for fields, note in (
            ({"current": "", "password": "longenough", "again": "longenough"},
             "Type your current password first."),
            ({"current": "old", "password": "short", "again": "short"},
             "The new password needs at least 8 characters."),
            ({"current": "old", "password": "longenough", "again": "other1234"},
             "The two new passwords are not the same."),
            ({"current": "longenough", "password": "longenough",
              "again": "longenough"},
             "The new password is the same as the current one.")):
        got = json.loads(_post(client, "/station/me/password", **fields)[2])
        assert got == {"ok": False, "said": "bad", "note": note, "field": "pw"}
    assert not [a for a in asked if a[0] == "pw"]
    good = {"current": "old-one", "password": "new-password",
            "again": "new-password"}
    # A wrong current password counts toward the lockout, under the name
    # and under the id; a rename in between forgives nothing.
    for n in range(app_mod.LOCKOUT_AFTER):
        if n == 2:
            me["username"] = "renamed"
        got = json.loads(_post(client, "/station/me/password", **good)[2])
        assert got["note"] == "That is not your current password."
        assert got["field"] == "pw"
    assert len(app_mod._failures["id:7"]) == app_mod.LOCKOUT_AFTER
    status, _, body = _post(client, "/station/me/password", **good)
    assert status == 429 and json.loads(body) == {
        "ok": False, "said": "locked",
        "note": "Too many wrong answers in a row - try again in a few minutes."}
    status, _, _ = _post(client, "/station/me/username", username="again")
    assert status == 429
    # The lockout passes; the right password rotates the seat.
    app_mod._failures.clear()
    wrong["on"] = False
    status, hdrs, body = _post(client, "/station/me/password", **good)
    got = json.loads(body)
    assert status == 200 and got["ok"] is True
    assert got["note"] == "Password changed. Your other browsers are signed out."
    assert got["me"]["pw"] == "Changed today" and "go" not in got
    assert hdrs["Set-Cookie"] == "gf=t-new; HttpOnly; SameSite=Lax; Path=/"
    assert asked[-1] == ("pw", 7, "t1", app_mod.SESSION_HOURS)
    assert desk.recorded[-1]["verb"] == "profile_password"
    assert desk.recorded[-1]["payload"] == {}
    assert "id:7" not in app_mod._failures
    # The fake seats know nothing of the new cookie; keep sitting in ours.
    client.cookie = "gf=t1"
    # This browser's seat already gone: changed, and off to sign in.
    monkeypatch.setattr(store_users, "change_password",
                        lambda s, uid, cur, new, *, token, hours: None)
    status, hdrs, body = _post(client, "/station/me/password", **good)
    assert json.loads(body)["go"] == "/login" and "Set-Cookie" not in hdrs
    # Not gated by WEB_MUTATIONS, and plain forms go back to the Station.
    status, hdrs, _ = _post(client, "/station/me/name", headers=None,
                            name="Sara")
    assert status == 303 and hdrs["Location"] == "/station"


def test_the_profile_is_not_gated_by_the_mutation_flag(web, desk, monkeypatch):
    from geelark_farm.store import users as store_users

    monkeypatch.setattr(store_users, "set_name", lambda s, uid, name: name)
    client = _signed(web)
    got = json.loads(_post(client, "/station/me/name", name="Mehdi")[2])
    assert got["ok"] is True and got["me"]["name"] == "Mehdi"


def test_the_seat_cookie_is_one_spelling_for_login_and_the_profile():
    import inspect

    login = inspect.getsource(app_mod._Handler._login)
    assert "self._seat_cookie(token)" in login
    seat = inspect.getsource(app_mod._Handler._seat_cookie)
    assert "HttpOnly; SameSite=Lax; Path=/" in seat
    assert "X-Forwarded-Proto" in seat


# ============================================================ Live tab
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_live_tab_page_and_state_are_the_holders_and_say_released_to_anyone_else(
        web, monkeypatch):
    from geelark_farm.store import station as store_station
    from geelark_farm.web import station_pages

    row = _live_row(owner_id=7)
    monkeypatch.setattr(store_station, "live_phone",
                        lambda s, serial, grace: dict(row))
    monkeypatch.setattr(station_pages, "live_page",
                        lambda lv, user: "<!--live-->" + json.dumps(lv))
    client = _signed(web)
    status, hdrs, body = _get(client, "/station/phones/1500/state",
                              headers=LIVE)
    got = json.loads(body)
    assert status == 200 and hdrs["Cache-Control"] == "no-store"
    assert got["conn"] == "on" and got["url"] == "https://view.example/p"
    assert got["pw"] == "gpw" and got["acct"]["address"] == "m@x.com"
    status, _, body = _get(client, "/station/phones/1500")
    assert status == 200 and '"conn": "on"' in body
    # Somebody else's: released, and nothing of theirs.
    row["owner_id"] = 99
    got = json.loads(_get(client, "/station/phones/1500/state", headers=LIVE)[2])
    assert got["conn"] == "released" and got["why"] == "released"
    for key in ("url", "gmail", "pw", "totp", "acct"):
        assert key not in got, key
    assert "gpw" not in json.dumps(got) and "view.example" not in json.dumps(got)
    status, _, body = _get(client, "/station/phones/1500")
    assert status == 200 and "gpw" not in body
    # Not a serial: nothing here.
    status, _, _ = _get(client, "/station/phones/15x0")
    assert status == 404
    # A store that did not answer is not "released", which is final.
    def down(*a, **k):
        raise OperationalError("gone")

    monkeypatch.setattr(store_station, "live_phone", down)
    status, _, body = _get(client, "/station/phones/1500/state", headers=LIVE)
    assert status == 503 and json.loads(body)["said"] == "down"
    status, _, _ = _get(client, "/station/phones/1500")
    assert status == 503


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_station_polls_are_kept_out_of_the_request_log(web, desk, caplog):
    client = _signed(web)
    with caplog.at_level(logging.INFO, logger="geelark_farm.web.app"):
        _get(client, "/station/state", headers=PAGE)
        _get(client, "/station/phones/1500/state", headers=LIVE)
        _get(client, "/station")
        client.request("POST", "/phones/1500/watching",
                       _form(csrf=client.token))
        # Each line is written once its answer is out, on the server's
        # own thread.
        time.sleep(0.3)
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("web ")]
    assert not [x for x in lines if "/state" in x], lines
    assert any(" /station " in x for x in lines), lines
    assert any("/phones/1500/watching" in x for x in lines), "beats stay"


def test_the_stream_takes_thirty_two_listeners():
    assert live.MAX_STREAMS == 32
    assert live.FARM_COLUMNS == 10
    for table in ("verdicts", "station_line", "FROM jobs WHERE kind = 'build'",
                  "max(updated_at) FROM wanted_builds"):
        assert table in live._FINGERPRINT, table
    assert "heartbeat_at" not in live._FINGERPRINT
    assert "seen_at" not in live._FINGERPRINT


def test_the_new_words_have_sentences():
    for word in ("gave-back", "called-off", "marked-auth"):
        assert pages._DASH_SAID[word] and pages._POOL_SAID[word], word
    assert pages._DASH_SAID["marked-auth"] == (
        "Marked Auth - the phone is deleted in a moment and the account that "
        "was on it freed.")
    assert pages._DASH_SAID["gave-back"] == (
        "Given back - the phone goes to the back of its shelf, switched off.")
    assert pages._DASH_SAID["called-off"] == "The build was called off."
    auth = pages.PHONE_STATES["auth"]
    assert auth == {"label": "Auth", "klass": "quiet bad", "sure": True,
                    "said": "marked-auth", "word": "Auth", "state": "failed",
                    "text": pages.PHONE_STATES["decline"]["text"]}
    assert pages.ENDINGS == ("done", "decline", "or"), "the dashboard's three"


def test_the_station_flag_is_in_the_example_file(monkeypatch, tmp_path):
    from geelark_farm.config import Settings

    root = pathlib.Path(__file__).parent.parent
    assert "STATION_FOR_OPERATORS" in (root / ".env.example").read_text(
        encoding="utf-8")
    monkeypatch.setattr("geelark_farm.config.ENV_FILE", tmp_path / "none.env")
    monkeypatch.setenv("GEELARK_APP_ID", "x")
    monkeypatch.setenv("GEELARK_API_KEY", "y")
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "a"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "l"))
    monkeypatch.delenv("STATION_FOR_OPERATORS", raising=False)
    assert Settings.load().station_for_operators is False
    for word, on in (("1", True), ("on", True), ("0", False), ("no", False)):
        monkeypatch.setenv("STATION_FOR_OPERATORS", word)
        assert Settings.load().station_for_operators is on, word


def test_the_station_modules_name_no_vendor():
    """The words a person reads say IranSpoty Cloud (2026-09-28)."""
    import io
    import tokenize

    for name in ("station_read.py",):
        src = (pathlib.Path(station_read.__file__).parent / name).read_text(
            encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.STRING:
                assert "geelark" not in tok.string.lower(), tok.string
    for text in (json.dumps(app_mod._STATION_ERRORS),
                 json.dumps(app_mod._POWER_WORDS)):
        assert "geelark" not in text.lower()


# ======================================================== station_read
class _FakeStation:
    """The store module `station_read` reads, as plain answers."""

    def __init__(self):
        self.calls: list[tuple] = []
        now = _now_dt()
        self.now = now
        self.mine = [
            {"serial": "5073", "lane": "gpt", "proxy_name": "h.x:1080:u:pw",
             "running": True, "live_url": "https://view.example/5073",
             "taken_at": now - timedelta(minutes=30),
             "idle_since": now - timedelta(minutes=5), "watching": False,
             "tab_closed": False, "tab_seen": True, "gmail": "g@gmail.com",
             "gmail_password": "gpw", "totp": "JBSWY3DPEHPK3PXP",
             "busy": None, "wish": None, "called_off": False,
             "from_line": 41, "last_id": 901, "last_verb": "change_proxy",
             "last_status": "done", "last_result": "moved",
             "last_detail": {"was": "PC2", "now": "socks5://u:p@n.x:1080",
                             "started": False}},
            {"serial": "5074", "lane": "spotify", "proxy_name": "PS1",
             "running": True, "live_url": "", "taken_at": now,
             "idle_since": now, "watching": True, "tab_closed": False,
             "tab_seen": False, "gmail": "✗", "gmail_password": "",
             "totp": "", "busy": "boot_phone", "wish": 12,
             "called_off": True, "from_line": None, "last_id": 5,
             "last_verb": "boot_phone", "last_status": "refused",
             "last_result": "no machine free", "last_detail": None},
            {"serial": "5075", "lane": "other", "proxy_name": "",
             "running": True, "live_url": "https://view.example/5075",
             "taken_at": now, "idle_since": now, "watching": False,
             "tab_closed": True, "tab_seen": True, "gmail": "",
             "gmail_password": "x", "totp": "y", "busy": "", "wish": None,
             "called_off": False, "from_line": None, "last_id": None},
        ]
        self.builds = [
            {"id": 123, "status": "running", "purpose": "gpt", "gmail": "",
             "no_gmail": False, "proxy_name": "", "app_account": "",
             "carry_address": "", "detail": "", "serial": "",
             "called_off_at": None, "job_status": "running",
             "claimed_at": now - timedelta(minutes=1), "phone_exit": "",
             "phone_gmail": ""},
            {"id": 124, "status": "failed", "purpose": "OTHER", "gmail": "",
             "no_gmail": True, "proxy_name": "h.y:1080:u:pw",
             "app_account": "", "carry_address": "c@x.com",
             "detail": "no free IP", "serial": "", "called_off_at": None,
             "job_status": None, "claimed_at": None, "phone_exit": "",
             "phone_gmail": ""},
            {"id": 125, "status": "queued", "purpose": "spotify",
             "gmail": "", "no_gmail": False, "proxy_name": "PS9",
             "app_account": "s@x.com", "carry_address": "", "detail": "",
             "serial": "5099", "called_off_at": now, "job_status": "queued",
             "claimed_at": None, "phone_exit": "", "phone_gmail": "p@gmail.com"},
            {"id": 126, "status": "running", "purpose": "", "gmail": "",
             "no_gmail": False, "proxy_name": "", "app_account": "",
             "carry_address": "", "detail": "", "serial": "5100",
             "called_off_at": None, "job_status": "running",
             "claimed_at": now - timedelta(hours=1), "phone_exit": "PC7",
             "phone_gmail": "✗"},
        ]


def _fake_store(monkeypatch, fake: _FakeStation | None = None) -> _FakeStation:
    from geelark_farm.store import station as store_station
    from geelark_farm.store import verdicts
    from geelark_farm.web import read

    fake = fake or _FakeStation()
    now = fake.now

    def note(name, value):
        def call(*args, **kw):
            fake.calls.append((name,) + tuple(a for a in args[1:2]))
            return value() if callable(value) else value
        return call

    monkeypatch.setattr(store_station, "scrub_mine", note("scrub_mine", 0))
    monkeypatch.setattr(store_station, "stamp_line", note("stamp_line", None))
    monkeypatch.setattr(store_station, "serve_lines",
                        lambda s, lanes=("gpt", "spotify"): fake.calls.append(
                            ("serve_lines",)) or [])
    monkeypatch.setattr(store_station, "typical", note("typical", {
        "gpt": 330.0, "spotify": 400.0, "other": 360.0}))
    monkeypatch.setattr(store_station, "shelves", note("shelves", {
        "gpt": {"ready": 3, "building": 2, "etas": [now + timedelta(minutes=4)],
                "late": 1, "typical_s": 330.0},
        "spotify": {"ready": 0, "building": 0, "etas": [], "late": 0,
                    "typical_s": 400.0}}))
    monkeypatch.setattr(store_station, "line_of", note("line_of", {
        "gpt": None,
        "spotify": {"id": 41, "position": 2, "joined_at": now}}))
    monkeypatch.setattr(store_station, "mine",
                        lambda s, uid, grace: fake.calls.append(
                            ("mine", uid, grace)) or [dict(r) for r in fake.mine])
    monkeypatch.setattr(store_station, "builds_of",
                        note("builds_of", lambda: [dict(r) for r in fake.builds]))
    monkeypatch.setattr(store_station, "notes", note("notes", [
        {"key": "e9", "at": now, "serial": "5061", "kind": "given back",
         "lane": "gpt"},
        {"key": "e8", "at": now, "serial": "5062", "kind": "switched off",
         "lane": "spotify"},
        {"key": "a7", "at": now, "serial": "5063", "kind": "returned",
         "lane": "other"},
        {"key": "e6", "at": now, "serial": "5064", "kind": "odd", "lane": ""}]))
    monkeypatch.setattr(store_station, "build_form", note("build_form", {
        "gmails_left": 12, "free_ips": {"gpt": 5, "spotify": 3, "other": 8},
        "stopped": True}))
    monkeypatch.setattr(verdicts, "of_person", lambda s, uid, a, b: [
        {"id": 555, "at": now, "button": "done", "serial": "5058",
         "gmail": "k@gmail.com", "proxy_name": "PS4", "exit_ip": "1.1.1.1",
         "lane": "spotify"},
        {"id": 554, "at": now, "button": "auth", "serial": "5057",
         "gmail": "", "proxy_name": "", "exit_ip": "2.2.2.2", "lane": "gpt"},
        {"id": 553, "at": now, "button": "done", "serial": "5056",
         "gmail": "", "proxy_name": "h.z:1080:u:pw", "exit_ip": "",
         "lane": "gpt"}])
    real = read.day_bounds
    monkeypatch.setattr(read, "day_bounds", lambda s, day: real(s, day))
    return fake


def _is_ms(value) -> bool:
    return isinstance(value, int) and value > 1_600_000_000_000


def test_station_read_never_raises_and_composes_the_contract(
        monkeypatch, make_settings):
    fake = _fake_store(monkeypatch)
    user = dict(ADMIN, display_name="Sara", mutations=True,
                created_at=datetime(2026, 9, 12, 8, tzinfo=timezone.utc),
                password_changed_at=None)
    got = station_read.state(make_settings(store_enabled=True), user)
    assert station_read.PARTIAL not in got
    assert set(got) == {"v", "rev", "now", "hold_minutes", "late_minutes", "me",
                        "may", "shelves", "tally", "phones", "builds", "today",
                        "notes", "build_form"}
    json.dumps(got)                               # every value is plain JSON
    assert got["v"] == 1 and _is_ms(got["now"]) and got["rev"]
    assert got["hold_minutes"] == 60 and got["late_minutes"] == 15
    me = got["me"]
    assert me["name"] == "Sara" and me["user"] == "mehdi" and me["initial"] == "S"
    assert me["since"] == "since 12 Sep 2026"
    assert re.fullmatch(r"Changed (today|yesterday|\d+ days ago)", me["pw"])
    assert me["daypart"] in ("morning", "afternoon", "evening", "night")
    assert got["may"] == {"take": True, "ip": True, "build": True}
    gpt, spotify = got["shelves"]["gpt"], got["shelves"]["spotify"]
    assert gpt["ready"] == 3 and gpt["building"] == 2 and gpt["late"] == 1
    assert gpt["typical_min"] == 6 and spotify["typical_min"] == 7
    assert _is_ms(gpt["eta_at"]) and gpt["etas"] == [gpt["eta_at"]]
    assert gpt["line"] is None and spotify["eta_at"] is None
    assert spotify["line"]["position"] == 2 and _is_ms(spotify["line"]["joined_at"])
    # The tally counts the rows the list shows.
    assert got["tally"] == {"done": 2, "decline": 0, "or": 0, "auth": 1,
                            "failed": 0, "all": 3}
    assert [r["v"] for r in got["today"]] == ["done", "auth", "done"]
    assert got["today"][0]["exit"] == "PS4" and got["today"][1]["exit"] == "2.2.2.2"
    assert got["today"][2]["exit"] == "h.z"
    assert re.fullmatch(r"\d\d:\d\d", got["today"][0]["hm"])
    # Phones.
    on, booting, closed = got["phones"]
    assert on["power"] == "on" and on["exit"] == "h.x" and on["live"] is True
    assert on["gmail"] == "g@gmail.com" and on["pw"] == "gpw"
    assert on["totp"] == "JBSWY3DPEHPK3PXP" and on["bare"] is False
    assert on["arrived"] == "line" and on["wish"] is None
    assert _is_ms(on["taken_at"]) and _is_ms(on["idle_since"])
    assert on["last"] == {"id": 901, "verb": "change_proxy", "ok": True,
                          "note": "moved", "was": "PC2", "now": "n.x",
                          "started": False}
    assert booting["power"] == "starting" and booting["bare"] is True
    assert booting["gmail"] == booting["pw"] == booting["totp"] == ""
    assert booting["idle_since"] is None, "watching: the hour is not running"
    assert booting["arrived"] == "build" and booting["wish"] == 12
    assert booting["called_off"] is True
    assert booting["last"] == {"id": 5, "verb": "boot_phone", "ok": False,
                               "note": "no machine free", "was": "",
                               "now": "", "started": False}
    # A tab just closed reads Ready though the phone still runs; a blank
    # Gmail is a bare phone and shows no secret.
    assert closed["power"] == "off" and closed["lane"] == "other"
    assert closed["bare"] is True and closed["pw"] == "" and closed["last"] is None
    # A running phone with no link is Ready too (decision 13).
    fake.mine[0]["live_url"] = ""
    assert station_read.state(make_settings(store_enabled=True), user)[
        "phones"][0]["power"] == "off"
    fake.mine[0]["busy"] = "change_proxy"
    assert station_read.state(make_settings(store_enabled=True), user)[
        "phones"][0]["power"] == "changing"
    # Builds.
    running, failed, queued, late = got["builds"]
    assert running["stage"] == "building" and _is_ms(running["eta_at"])
    assert running["late"] is False and running["typical_min"] == 6
    assert running["chips"] == ["next free Gmail", "GPT IP"]
    assert failed["stage"] == "failed" and failed["lane"] == "other"
    assert failed["reason"] == "no free IP" and failed["bare"] is True
    assert failed["eta_at"] is None
    assert failed["chips"] == ["bare phone", "h.y", "c@x.com"]
    assert queued["stage"] == "queued" and queued["called_off"] is True
    assert queued["chips"] == ["p@gmail.com", "PS9", "s@x.com"]
    assert queued["reason"] == ""
    assert late["late"] is True and late["eta_at"] is None
    assert late["chips"] == ["next free Gmail", "PC7"]
    # Notes, in the prototype's words; an unknown kind is left out.
    assert [n["id"] for n in got["notes"]] == ["e9", "e8", "a7"]
    assert got["notes"][0]["text"] == (
        "5061 was left alone for an hour, so it went back to the GPT shelf.")
    assert got["notes"][1]["text"] == (
        "The tab of 5062 was closed, so the phone is switched off. It stays "
        "yours for an hour from now – Boot brings it back.")
    assert got["notes"][2]["text"] == "Phone 5063 is back on the farm."
    assert got["notes"][1]["tone"] == "spotify" and _is_ms(got["notes"][0]["at"])
    assert got["build_form"] == {
        "gmails_left": 12, "free_ips": {"gpt": 5, "spotify": 3, "other": 8},
        "stopped": True, "typical_min": {"gpt": 6, "spotify": 7, "other": 6}}
    # The line is stamped before it is served.
    names = [c[0] for c in fake.calls]
    assert names.index("stamp_line") < names.index("serve_lines")
    assert ("mine", 7, 180) in fake.calls


def test_station_read_keeps_its_shape_when_the_store_does_not_answer(
        monkeypatch, make_settings):
    from geelark_farm.store import station as store_station
    from geelark_farm.store import verdicts
    from geelark_farm.web import read

    def down(*a, **k):
        raise OperationalError("gone")

    for name in ("scrub_mine", "stamp_line", "serve_lines", "typical",
                 "shelves", "line_of", "mine", "builds_of", "notes",
                 "build_form", "live_phone"):
        monkeypatch.setattr(store_station, name, down)
    monkeypatch.setattr(verdicts, "of_person", down)
    user = dict(ADMIN, mutations=True)
    s = make_settings(store_enabled=True)
    got = station_read.state(s, user)
    assert got[station_read.PARTIAL] is True
    assert got["phones"] == [] and got["builds"] == [] and got["today"] == []
    assert got["tally"]["all"] == 0 and set(got["shelves"]) == {"gpt", "spotify"}
    lv = station_read.live(s, user, "1500")
    assert lv[station_read.PARTIAL] is True and lv["conn"] == "released"
    # No day, no results.
    monkeypatch.setattr(read, "day_bounds", lambda s, day: None)
    _fake_store(monkeypatch)
    monkeypatch.setattr(read, "day_bounds", lambda s, day: None)
    got = station_read.state(s, user)
    assert got["today"] == [] and got["tally"]["all"] == 0


def _live_row(**more) -> dict:
    now = _now_dt()
    row = {"serial": "1500", "lane": "gpt", "proxy_name": "PC2",
           "running": True, "live_url": "https://view.example/p",
           "taken_at": now, "idle_since": now, "watching": True,
           "tab_closed": True, "tab_seen": True, "gmail": "g@gmail.com",
           "gmail_password": "gpw", "totp": "T0TP", "busy": None,
           "wish": None, "called_off": False, "from_line": None,
           "last_id": None, "state": "taken", "owner_id": 7, "done_at": None,
           "app_account": "m@x.com", "acct_address": "m@x.com",
           "acct_password": "apw", "acct_totp": "", "acct_product": "chatgpt",
           "acct_category": "", "acct_email_code": False,
           "carry_address": "", "carry_password": ""}
    row.update(more)
    return row


def test_the_live_state_is_the_holders_and_released_to_anyone_else(
        monkeypatch, make_settings):
    from geelark_farm.store import station as store_station

    row = _live_row()
    monkeypatch.setattr(store_station, "live_phone",
                        lambda s, serial, grace: dict(row) if row else None)
    s = make_settings(store_enabled=True)
    user = dict(ADMIN, mutations=True)
    got = station_read.live(s, user, "1500", said="no:944", note="booting")
    json.dumps(got)
    assert got["conn"] == "on", "the tab asking is open, whatever the beacon"
    assert got["url"] == "https://view.example/p" and got["why"] == ""
    assert got["viewer"] == {"w": pages.VIEWER_WIDTH,
                             "box_w": pages.VIEWER_BOX[0],
                             "box_h": pages.VIEWER_BOX[1],
                             "bar": pages.VIEWER_BAR}
    assert got["acct"] == {"title": "ChatGPT account", "kind": "",
                           "address": "m@x.com", "pw": "apw", "totp": "",
                           "carried": False}
    assert got["arrival"] == {"said": "no", "req": 944, "note": "booting"}
    assert got["may"] == {"take": True, "ip": True} and _is_ms(got["taken_at"])
    assert "arrival" not in station_read.live(s, user, "1500")
    for busy, conn in (("boot_phone", "booting"), ("change_proxy", "changing")):
        row["busy"] = busy
        got = station_read.live(s, user, "1500")
        assert got["conn"] == conn and got["url"] == ""
    row["busy"] = None
    row["live_url"] = ""
    assert station_read.live(s, user, "1500")["conn"] == "off"
    row.update(acct_product="spotify", acct_category="error")
    got = station_read.live(s, user, "1500")
    assert got["acct"]["title"] == "Spotify account"
    assert got["acct"]["kind"] == "error"
    row.update(carry_address="o@x.com", carry_password="opw")
    assert station_read.live(s, user, "1500")["acct"] == {
        "title": "App account", "kind": "", "address": "o@x.com", "pw": "opw",
        "totp": "", "carried": True}
    row.update(carry_address="", acct_address=None)
    assert station_read.live(s, user, "1500")["acct"] is None
    row.update(gmail="✗")
    got = station_read.live(s, user, "1500")
    assert got["bare"] is True and got["pw"] == "" and got["totp"] == ""
    # Anybody else: released, with nothing of the phone's.
    for change, why in (({"owner_id": 99}, "released"),
                        ({"state": "done", "owner_id": None,
                          "done_at": _now_dt()}, "closed"),
                        ({"state": "failed", "owner_id": None}, "closed"),
                        ({"state": "", "owner_id": None}, "released"),
                        ({"taken_at": None}, "released")):
        row = _live_row(**change)
        got = station_read.live(s, user, "1500")
        assert got["conn"] == "released" and got["why"] == why, change
        for key in ("url", "gmail", "pw", "totp", "acct"):
            assert key not in got, (change, key)
        assert got["last"] is None and got["exit"] == ""
    row = None
    got = station_read.live(s, user, "1500")
    assert got["conn"] == "released" and got["why"] == "released"


def test_the_password_line_counts_calendar_days_in_the_console_zone():
    zone = pages._ZONE
    now = datetime.now(zone)
    assert station_read.changed_words(now) == "Changed today"
    assert station_read.changed_words(now - timedelta(days=1)) == (
        "Changed yesterday")
    assert station_read.changed_words(now - timedelta(days=12)) == (
        "Changed 12 days ago")
    assert station_read.changed_words(None) == ""
    assert [station_read._daypart(h) for h in (4, 5, 11, 12, 16, 17, 21, 22)] \
        == ["night", "morning", "morning", "afternoon", "afternoon", "evening",
            "evening", "night"]


# ====================================================== against a cluster
DSN = os.environ.get("GEELARK_TEST_DSN", "")
needs_cluster = pytest.mark.skipif(
    not DSN, reason="set GEELARK_TEST_DSN to run store integration tests")


@pytest.fixture
def farm(make_settings):
    from geelark_farm.store import db as store_db
    from tests.test_station_store import Farm

    parts = dict(p.split("=", 1) for p in DSN.split())
    s = make_settings(
        store_enabled=True, store_host=parts["host"],
        store_port=int(parts.get("port", 5432)), store_db=parts["dbname"],
        store_user=parts["user"], store_password=parts["password"])
    store_db.ensure_schema(s)
    f = Farm(s, uuid.uuid4().hex[:8])
    try:
        yield f
    finally:
        f.cleanup()


@needs_cluster
def test_the_fingerprint_reads_the_real_tables(farm):
    """The eleven columns, off a real Postgres: every table the Station
    draws moves it."""
    from tests.test_station_store import Raw

    before = live.take(farm.s)
    assert before is not None and len(before) == 11
    a = farm.user("a")
    farm.insert("verdicts", button="done", state="done", serial="9",
                by_id=a)
    farm.wait(a, "gpt")
    farm.job(payload={"purpose": "gpt"}, created_at=Raw("now() + interval '1 day'"))
    after = live.take(farm.s)
    assert after[7] != before[7], "a verdict moves it"
    assert after[8] != before[8], "the line moves it"
    assert after[9] != before[9], "a build job moves it"


@needs_cluster
def test_the_state_and_the_live_tab_compose_off_a_real_store(farm):
    """station_read over the real statements of store.station: the page's
    whole state and one Live tab, for a person with a phone on, a bare
    phone booting, a build on its way and a result today."""
    from tests.test_station_store import Raw

    a = farm.user("a", role="admin")
    b = farm.user("b")
    row = farm.one("SELECT * FROM users WHERE id = %s", (a,))
    user = dict(row, mutations=True)
    gmail = f"g{farm.tag}@gmail.com"
    farm.resource("gmail", address=gmail, password="gpw",
                  totp_secret="JBSWY3DPEHPK3PXP", status="in_use")
    on = farm.hold(a, gmail=gmail, proxy_name="h.x:1080:u:pw", running=True,
                   live_url="https://view.example/p",
                   watched_at=Raw("now()"))
    bare = farm.hold(a, gmail="✗", purpose="spotify")
    farm.action("boot_phone", bare, a, status="running",
                executed_at=Raw("now()"))
    theirs = farm.hold(b, gmail=f"h{farm.tag}@gmail.com")
    farm.wish(a, purpose="other", no_gmail=True)
    farm.insert("verdicts", button="auth", state="failed", serial="9x",
                phone_id="PH9x", by_id=a, lane="gpt")
    got = station_read.state(farm.s, user)
    assert station_read.PARTIAL not in got, got
    json.dumps(got)
    phones = {p["serial"]: p for p in got["phones"]}
    assert set(phones) == {on, bare}, "only this person's own holds"
    assert phones[on]["power"] == "on" and phones[on]["exit"] == "h.x"
    assert phones[on]["pw"] == "gpw" and phones[on]["watching"] is True
    assert phones[on]["idle_since"] is None and _is_ms(phones[on]["taken_at"])
    assert phones[bare]["power"] == "starting" and phones[bare]["bare"] is True
    assert phones[bare]["lane"] == "spotify" and phones[bare]["pw"] == ""
    assert [b_["lane"] for b_ in got["builds"]] == ["other"]
    assert got["builds"][0]["chips"] == ["bare phone", "any IP"]
    assert got["builds"][0]["stage"] == "queued"
    assert got["tally"]["auth"] == 1 and got["tally"]["all"] == 1
    assert got["today"][0]["serial"] == "9x"
    assert set(got["shelves"]) == {"gpt", "spotify"}
    assert got["me"]["user"] == row["username"]
    assert got["me"]["pw"].startswith("Changed")
    # HEAD reads the same and writes nothing.
    assert station_read.state(farm.s, user, write=False)["phones"] == got["phones"]
    mine = station_read.live(farm.s, user, on)
    assert station_read.PARTIAL not in mine
    assert mine["conn"] == "on" and mine["url"] == "https://view.example/p"
    assert mine["gmail"] == gmail and mine["totp"] == "JBSWY3DPEHPK3PXP"
    other = station_read.live(farm.s, user, theirs)
    assert other["conn"] == "released" and "pw" not in other
    gone = station_read.live(farm.s, user, "0")
    assert gone["conn"] == "released" and station_read.PARTIAL not in gone


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_retry_the_power_index_refuses_answers_already_not_a_500(web,
                                                                   monkeypatch):
    """Rev 42 allows one pending power press per phone, so retrying a
    failed Boot while another is pending is refused by the index: the
    Requests page says it is already asked, it does not break."""
    import geelark_farm.store.actions as actions_mod

    def refused(s, **k):
        raise UniqueViolation("actions_one_power_press")

    monkeypatch.setattr(actions_mod, "retry", refused)
    client = web()
    client.login()
    status, headers, _ = client.request("POST", "/requests/240/retry",
                                        f"csrf={client.csrf()}")
    assert status == 303
    assert dict(headers)["Location"] == "/requests?said=already"

    def broke(s, **k):
        raise RuntimeError("something else")

    monkeypatch.setattr(actions_mod, "retry", broke)
    status, _, _ = client.request("POST", "/requests/241/retry",
                                  f"csrf={client.csrf()}")
    assert status == 500


# ================================================= review round (2026-09-29)
def _store_goes_down(monkeypatch):
    """From here on the session read cannot reach the store, the way
    psycopg says so - through the real `sessions.find`, not the web
    fixture's fake seats."""
    from geelark_farm.store import sessions as store_sessions

    class Down:
        def __init__(self, settings):
            raise OperationalError("connection timeout expired")

    monkeypatch.setattr(store_sessions, "find", _REAL_FIND)
    monkeypatch.setattr(store_sessions, "Store", Down)


_DOWN = {"ok": False, "said": "down",
         "note": "The farm's store is not answering - try again in a moment."}


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_store_that_does_not_answer_is_down_never_signed_out(
        web, desk, monkeypatch):
    """web-1 / front-behaviour-1: a blip of the cluster answered 401
    signed-out, which sent every Station to /login and released every Live
    tab for good. It is the 503 `down` the script waits out."""
    client = _signed(web)
    _store_goes_down(monkeypatch)
    for path, headers in (("/station/state", PAGE),
                          ("/station/phones/1500/state", LIVE)):
        status, _, body = _get(client, path, headers=headers)
        assert status == 503 and json.loads(body) == _DOWN, path
    for path, fields in (("/phones/1500/watching", {"station": "1"}),
                         ("/station/take", {"lane": "gpt"}),
                         ("/phones/1500/boot", {"station": "1"})):
        status, _, body = _post(client, path, headers=LIVE, **fields)
        assert status == 503 and json.loads(body) == _DOWN, path
    # Without the header: the store-down page, not the sign-in page.
    status, hdrs, body = _get(client, "/station")
    assert status == 503 and "The store is not answering" in body
    assert "Location" not in hdrs
    status, hdrs, _ = _post(client, "/phones/1500/state", headers=None,
                            state="unused")
    assert status == 503 and "Location" not in hdrs
    assert desk.queued == [] and desk.states == []


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_store_down_page_never_echoes_a_password(web, desk, monkeypatch):
    """web-m1: the retry form writes every posted field back into the
    page. A door whose form carries a password gets no retry at all."""
    client = _signed(web)
    _store_goes_down(monkeypatch)
    for path in ("/password", "/station/me/password"):
        status, _, body = _post(client, path, headers=None,
                                current="Old-Secret-1", password="New-Secret-2",
                                again="New-Secret-2")
        assert status == 503 and "The store is not answering" in body, path
        assert "Secret" not in body and "<form" not in body, path
    status, _, body = _post(client, "/phones/build", headers=None,
                            gmail="x@gmail.com", gmail_password="Gm-Secret-3")
    assert status == 503 and "Secret" not in body
    # A form with nothing secret in it is still kept for a second press.
    status, _, body = _post(client, "/phones/1500/state", headers=None,
                            state="unused")
    assert 'name="state" value="unused"' in body
    # And the page itself drops a secret whatever door it came from.
    got = pages.store_down_page(("/pools/gmail/add", {
        "address": ["a@gmail.com"], "password": ["Pool-Secret-4"]}))
    assert "Pool-Secret-4" not in got and "<form" not in got
    got = pages.store_down_page(("/pools/gmail/add", {
        "address": ["a@gmail.com"], "password": [""]}))
    assert 'name="address" value="a@gmail.com"' in got


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_store_down_pages_try_again_is_not_a_stale_session(web, desk):
    """The kept form left out `csrf`, so pressing Try again once the store
    was back could only ever answer 403 "Stale session"."""
    client = _signed(web)
    with pytest.MonkeyPatch.context() as down:
        _store_goes_down(down)
        status, _, body = _post(client, "/phones/1500/state", headers=None,
                                state="unused")
    assert status == 503
    action = re.search(r'<form method="post" action="([^"]+)"', body).group(1)
    kept = dict(re.findall(
        r'<input type="hidden" name="([^"]+)" value="([^"]*)">', body))
    assert kept.get("csrf") == client.token
    status, _, body = client.request("POST", action, _form(**kept))
    assert status != 403 and "Stale session" not in body, (status, body)


@pytest.mark.parametrize("web", [FLAG_ON], indirect=True)
def test_the_boot_tab_frames_only_the_askers_own_request(
        web, desk, monkeypatch):
    """web-2: `?said=done:<id>` framed any request's viewer link for any
    operator - ids are sequential. Only who asked, or an admin."""
    monkeypatch.setattr(app_mod._Handler, "_gmail_for_the_holder",
                        lambda self, user, serial: None)
    monkeypatch.setattr(app_mod._Handler, "_account_for_the_holder",
                        lambda self, user, serial: None)
    monkeypatch.setattr(app_mod.read, "lane_of", lambda s, serial: "gpt")
    url = "https://viewer.example.test/SECRET-98001"
    desk.rows[500] = {"id": 500, "verb": "boot_phone", "status": "done",
                      "result": "on", "detail": {"url": url, "station": True},
                      "requested_by": 7}
    desk.rows[501] = dict(desk.rows[500], id=501, requested_by=9)
    _as(monkeypatch, OPERATOR)
    client = _signed(web, "sara")
    for serial in ("98001", "12345"):
        status, _, body = _get(client, f"/phones/{serial}/live?said=done:500")
        assert status == 200 and url not in body, serial
    status, _, body = _get(client, "/phones/98001/live?said=done:501")
    assert status == 200 and url in body, "the asker's own is framed"
    _as(monkeypatch, ADMIN)
    client = _signed(web)
    status, _, body = _get(client, "/phones/98001/live?said=done:501")
    assert status == 200 and url in body, "an admin's is any"


@pytest.mark.parametrize("web", [FLAG_ON], indirect=True)
def test_watch_live_frames_no_screen_of_a_phone_somebody_else_holds(
        web, desk, monkeypatch):
    """The builder's link outlives the build on a phone nobody restarted,
    and /phones/<s>/watch is an operator door: another person's hold - a
    Station one included - is not framed from there. A phone being built
    (nobody's), one's own hold and an admin still are."""
    url = "https://viewer.example.test/SECRET-98001"
    monkeypatch.setattr(app_mod.read, "live_link", lambda s, serial: url)
    held = {"98001": {"serial": "98001", "state": "taken", "owner": "ali"},
            "98002": {"serial": "98002", "state": "", "owner": None},
            "98003": {"serial": "98003", "state": "taken", "owner": "sara"}}
    monkeypatch.setattr(app_mod.read, "phone_holder",
                        lambda s, serial: held.get(serial))
    _as(monkeypatch, OPERATOR)
    client = _signed(web, "sara")
    status, _, body = _get(client, "/phones/98001/watch")
    assert status == 200 and url not in body and "Not allowed" in body
    for serial in ("98002", "98003"):
        status, _, body = _get(client, f"/phones/{serial}/watch")
        assert status == 200 and url in body, serial
    _as(monkeypatch, ADMIN)
    client = _signed(web)
    status, _, body = _get(client, "/phones/98001/watch")
    assert status == 200 and url in body, "an admin watches any"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_profile_change_that_committed_answers_saved_whatever_its_record(
        web, desk, monkeypatch):
    """web-4: the actions row is written after the change committed. A
    store blip there answered "broke" - for the password with no new
    cookie, so the person was signed out and told it failed."""
    from geelark_farm.store import station as store_station
    from geelark_farm.store import users as store_users

    def record(settings, **kw):
        raise OperationalError("gone for a moment")

    monkeypatch.setattr(store_station, "record", record)
    monkeypatch.setattr(store_users, "set_name", lambda s, uid, name: name)
    monkeypatch.setattr(store_users, "set_username",
                        lambda s, uid, name: name.strip().lower())
    monkeypatch.setattr(store_users, "change_password",
                        lambda s, uid, cur, new, *, token, hours: "t-new")
    client = _signed(web)
    status, _, body = _post(client, "/station/me/name", name="Sara")
    assert status == 200 and json.loads(body)["note"] == "Name saved."
    status, _, body = _post(client, "/station/me/username", username="sara.k")
    assert status == 200 and json.loads(body)["ok"] is True
    status, hdrs, body = _post(client, "/station/me/password",
                               current="old-one", password="new-password",
                               again="new-password")
    assert status == 200 and json.loads(body)["ok"] is True
    assert hdrs["Set-Cookie"].startswith("gf=t-new;")


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_header_opens_no_station_for_an_operator_while_the_flag_is_off(
        web, desk, monkeypatch):
    """web-5: the shared doors answered the Station's whole state - and ran
    its writing read - to an operator the trial keeps off the Station."""
    from geelark_farm.store import wanted as store_wanted

    monkeypatch.setattr(store_wanted, "dismiss",
                        lambda s, wid, *, user_id, admin=False: True)
    _as(monkeypatch, OPERATOR)
    client = _signed(web, "sara")
    status, _, body = _post(client, "/phones/1500/boot", station="1")
    got = json.loads(body)
    assert status == 200 and "state" not in got
    status, _, body = _post(client, "/wishes/1/dismiss")
    got = json.loads(body)
    assert status == 200 and "state" not in got
    assert got["note"] == "Taken off your station."
    assert desk.states == [], "the state read (and its writes) never ran"
    assert not [r for r in desk.recorded if r["verb"] == "dismiss_build"]
    # A Live tab still gets its own phone's state.
    status, _, body = _post(client, "/phones/1500/boot", headers=LIVE,
                            station="1")
    assert json.loads(body)["live"]["serial"] == "1500"


@pytest.mark.parametrize("web", [FLAG_ON], indirect=True)
def test_with_the_flag_on_the_operators_presses_carry_the_state(
        web, desk, monkeypatch):
    from geelark_farm.store import wanted as store_wanted

    monkeypatch.setattr(store_wanted, "dismiss",
                        lambda s, wid, *, user_id, admin=False: True)
    _as(monkeypatch, OPERATOR)
    client = _signed(web, "sara")
    got = json.loads(_post(client, "/wishes/1/dismiss")[2])
    assert got["state"]["v"] == 1
    assert [r["verb"] for r in desk.recorded] == ["dismiss_build"]


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_poll_that_is_refused_or_down_leaves_its_line_in_the_log(
        web, desk, monkeypatch, caplog):
    """web-6: only the polls' 200s and 304s are kept out of the log."""
    client = _signed(web)
    with caplog.at_level(logging.INFO, logger="geelark_farm.web.app"):
        _get(client, "/station/state", headers=PAGE)
        _store_goes_down(monkeypatch)
        _get(client, "/station/state", headers=PAGE)
        _get(client, "/station/phones/1500/state", headers=LIVE)
        time.sleep(0.3)
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("web ")]
    assert not [x for x in lines if "/state 200" in x], lines
    assert any("/station/state 503" in x for x in lines), lines
    assert any("/station/phones/1500/state 503" in x for x in lines), lines


def test_a_failed_station_build_offers_no_retry():
    """web-7: its row lost the typed passwords once it ran, so a Retry
    replayed a Gmail with no password or an exit cut to host:port."""
    user = {"id": 7, "username": "mehdi", "role": "admin", "sees": "all",
            "csrf": "c", "nav": {}}
    base = {"status": "failed", "result": "no free IP", "requested_by": 7,
            "requested_at": None, "detail": None}
    rows = [dict(base, id=1, verb="build_by_hand",
                 payload={"station": True, "gmail": "x@gmail.com"}),
            dict(base, id=2, verb="build_by_hand", payload={"gmail": ""}),
            dict(base, id=3, verb="boot_phone", payload={"serial": "1"})]
    got = pages.requests_page(rows, user)
    assert 'action="/requests/1/retry"' not in got
    assert 'action="/requests/2/retry"' in got
    assert 'action="/requests/3/retry"' in got
    said = pages.requests_page([], user, said="station_build")
    assert "Press Build again on the Station." in said


def _no_writes(monkeypatch):
    from geelark_farm.store import station as store_station

    def writer(*a, **k):
        raise AssertionError("a read-only state wrote")

    for name in ("scrub_mine", "stamp_line", "serve_lines"):
        monkeypatch.setattr(store_station, name, writer)


def test_the_smokes_read_only_state_writes_nothing(monkeypatch, make_settings,
                                                   caplog):
    """deploy-M2: `state(write=False)` is the smoke's read - HEAD's too -
    and runs none of the three writers."""
    _fake_store(monkeypatch)
    _no_writes(monkeypatch)
    with caplog.at_level(logging.WARNING):
        got = station_read.state(make_settings(store_enabled=True),
                                 dict(ADMIN, mutations=True), write=False)
    assert station_read.PARTIAL not in got and got["phones"]
    assert "did not run" not in caplog.text


@needs_cluster
def test_the_smoke_reads_the_real_store_and_writes_nothing(
        farm, monkeypatch, caplog):
    """deploy-M2 against a real Postgres: what smoke_station.py runs - the
    user by id, the read-only state, the page, one Live tab - with the
    person waiting in a line. Nothing is served or written, and no read
    logs a warning."""
    from geelark_farm.store import users as store_users
    from geelark_farm.web import station_pages
    from tests.test_station_store import Raw

    _no_writes(monkeypatch)
    a = farm.user("a", role="admin")
    farm.wait(a, "spotify", joined_at=Raw("now() - interval '5 minutes'"))
    on = farm.hold(a, running=True, live_url="https://view.example/s")
    user = dict(store_users.get(farm.s, a), csrf="smoke")
    with caplog.at_level(logging.WARNING):
        st = station_read.state(farm.s, user, write=False)
        html = station_pages.station_page(st, user)
        lv = station_read.live(farm.s, user, on)
        station_pages.live_page(lv, user)
    assert station_read.PARTIAL not in st and station_read.PARTIAL not in lv
    assert [p["serial"] for p in st["phones"]] == [on]
    assert "{{" not in html and lv["conn"] == "on"
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert farm.one("SELECT ended_at FROM station_line WHERE user_id = %s",
                    (a,))["ended_at"] is None
    assert farm.sql("SELECT 1 FROM actions WHERE requested_by = %s",
                    (a,)) == []


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_power_clash_is_read_as_a_boot_or_change_ip_never_a_power_off(
        web, desk):
    """D1: the unique index holds Boot and Change IP only, so the press
    that refused this one is looked for among those two - an older
    pending power-off (which never clashes) is not named as the cause."""
    rows = [{"id": 70, "verb": "power_off_phone", "stale": False},
            {"id": 71, "verb": "change_proxy", "stale": False}]
    calls = {"n": 0}

    def pending():
        calls["n"] += 1
        if calls["n"] == 1:
            return None            # the pre-check raced past both
        want = desk.pend_calls[-1][1] or ("boot_phone", "change_proxy",
                                          "power_off_phone")
        return next((r for r in rows if r["verb"] in want), None)

    desk.pending = pending
    desk.enqueue_raises = [UniqueViolation("actions_one_power_press")]
    client = _signed(web)
    got = json.loads(_post(client, "/phones/1500/boot", headers=LIVE,
                           station="1")[2])
    assert desk.pend_calls[-1] == ("1500", ("boot_phone", "change_proxy"))
    assert got["ok"] is False
    assert got["note"] == "phone 1500 is changing its IP - wait for it"


# ============================================ skeptic round (2026-09-29)
@pytest.mark.parametrize("raised", ["ConnectionTimeout", "AdminShutdown",
                                    "OperationalError"])
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_real_psycopg_outage_is_down_not_broke(web, desk, monkeypatch,
                                                 raised):
    """The outage web-1 was found with is a connect that timed out, and
    psycopg raises `ConnectionTimeout` for it - a subclass whose own name
    is not OperationalError. Matched by name alone it answered 500
    `broke`; a cluster restart (`AdminShutdown`) did the same."""
    import psycopg

    kind = getattr(psycopg.errors, raised, None) or psycopg.OperationalError
    from geelark_farm.store import sessions as store_sessions

    class Down:
        def __init__(self, settings):
            raise kind("connection timeout expired")

    client = _signed(web)
    monkeypatch.setattr(store_sessions, "find", _REAL_FIND)
    monkeypatch.setattr(store_sessions, "Store", Down)
    status, _, body = _get(client, "/station/state", headers=PAGE)
    assert status == 503 and json.loads(body) == _DOWN
    status, _, body = _post(client, "/phones/1500/watching", headers=LIVE,
                            station="1")
    assert status == 503 and json.loads(body) == _DOWN
    status, hdrs, body = _get(client, "/station")
    assert status == 503 and "The store is not answering" in body
    assert "Location" not in hdrs


def test_the_store_is_down_for_every_operational_error_by_ancestry():
    import psycopg

    for exc in (psycopg.errors.ConnectionTimeout("t"),
                psycopg.errors.AdminShutdown("a"), psycopg.OperationalError("o"),
                OperationalError("fake")):
        assert app_mod._store_down(exc), type(exc).__name__
    wrapped = RuntimeError("wrapped")
    wrapped.__cause__ = psycopg.errors.ConnectionTimeout("t")
    assert app_mod._store_down(wrapped)
    assert not app_mod._store_down(ValueError("no"))
    assert not app_mod._store_down(psycopg.errors.UniqueViolation("u"))


@pytest.mark.parametrize("web", [FLAG_ON], indirect=True)
def test_somebody_elses_boot_tab_says_not_allowed_and_stops_reloading(
        web, desk, monkeypatch):
    """web-2, second half: a request that is not the reader's is not
    framed - and not left reading "Starting" and reloading for ever."""
    monkeypatch.setattr(app_mod.read, "lane_of", lambda s, serial: "gpt")
    desk.rows[502] = {"id": 502, "verb": "boot_phone", "status": "done",
                      "result": "phone 98001 started and taken by ali",
                      "detail": {"url": "https://viewer.example.test/S"},
                      "requested_by": 7}
    _as(monkeypatch, OPERATOR)
    client = _signed(web, "sara")
    status, _, body = _get(client, "/phones/98001/live?said=done:502")
    assert status == 200 and "Not allowed" in body
    assert "viewer.example.test" not in body and "taken by ali" not in body
    assert "location.reload" not in body


def test_the_store_down_page_keeps_no_pasted_preview_rows():
    """A paste's preview carries its accounts - passwords and keys - in
    `rows`; the store-down page must not write them back."""
    got = pages.store_down_page(("/pools/gmail/confirm", {
        "rows": ["a@gmail.com:Pw-Secret-5:JBSWY3DP"], "idem": ["x"]}))
    assert "Pw-Secret-5" not in got and "<form" not in got
