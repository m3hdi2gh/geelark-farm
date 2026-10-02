"""The Proxies page (2026-10-02): its document in the admin rail, its state
and the requests it waits on, its two doors - over real HTTP against the
`web` fixture's server, with the reader and the queue faked at their
seams - and the reader's composition on plain rows."""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess

import pytest

import geelark_farm.store.actions as actions_mod
import geelark_farm.web.app as app_mod
from geelark_farm.web import assets, proxies_read
from tests import test_web as _test_web
from tests.test_web import MUTATIONS_ON, FakeStore, _form

web = _test_web.web
PAGE = {"X-GF-Station": "page"}
U = datetime.timezone.utc


def _t(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 10, day, hour, minute, tzinfo=U)


def _row(pid, name, status="free", serial="", **more) -> dict:
    row = {"id": pid, "proxy_name": name, "host": "H.example", "port": 1000 + pid,
           "username": f"u{pid}", "status": status, "serial": serial,
           "last_exit_ip": "", "note": "", "error": None, "purpose": "",
           "uses_per_day": None, "day_uses": 0, "day_uses_on": None,
           "created_at": _t(1, 8), "source": "web"}
    row.update(more)
    return row


def _state(*rows) -> dict:
    return proxies_read.assemble(list(rows), [], [], [], [], geo={}, archived=3,
                                 archived_names=[], now=_t(2, 8))


STATE = _state(_row(7, "TSP10"), _row(8, "Webshare-ISP-02Oct-1", "set aside", "5441"),
               _row(9, "ISP3", "set aside"), _row(10, "ISP4", "on a phone", "5442"))


@pytest.fixture
def desk(monkeypatch):
    """The reader answers STATE; the queue writes down what it is asked and
    runs every verb here but the two that test through the cloud."""
    asked = []
    monkeypatch.setattr(proxies_read, "state", lambda s, fresh=False: STATE)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)

    def enqueue(settings, *, verb, payload, requested_by, idem_key):
        asked.append({"verb": verb, "payload": payload, "idem": idem_key})
        return 100 + len(asked)

    monkeypatch.setattr(actions_mod, "enqueue", enqueue)
    monkeypatch.setattr(app_mod._Handler, "_ran_it_now", lambda self, verb, payload, req: (
        None if verb in ("mark_proxy_free", "test_proxy", "add_proxies") else "done"))
    monkeypatch.setattr(actions_mod, "one", lambda s, req: {
        "status": "done", "result": f"request {req} done", "detail": None})
    monkeypatch.setattr(app_mod.signals, "ring", lambda *a, **k: None)
    return asked


def _json(client, path):
    status, headers, body = client.request("GET", path, headers=PAGE)
    return status, dict(headers), (json.loads(body) if body else None)


def _press(client, path, **fields):
    status, _, body = client.request("POST", path, _form(csrf=client.csrf(), **fields),
                                     headers=PAGE)
    assert status == 200, body
    return json.loads(body)


def test_the_page_is_the_prototype_in_the_admin_rail(web, desk):
    client = web()
    client.login()
    status, _, body = client.request("GET", "/pools/proxy")
    assert status == 200
    assert '<a href="/pools/proxy" class="rail-link here" aria-current="page">' in body
    assert '<span class="rail-lbl">Proxies</span>' in body, "the rail names it"
    assert assets.PROXIES_CSS_PATH in body and assets.PROXIES_JS_PATH in body
    assert f'<meta name="gf-rev" content="{assets.REV}">' in body
    island = re.search(r'<script type="application/json" id="gf-state">(.*?)</script>',
                       body).group(1)
    assert json.loads(island) == json.loads(json.dumps(STATE))
    assert "{{" not in body and "Prototype" not in body
    assert '<b>mehdi</b><small>admin</small>' in body, "the bar names who is in"
    assert 'id="gf-csrf"' in body and 'class="rail-shell"' in body


def test_an_operator_never_reaches_it(web, desk, monkeypatch):
    monkeypatch.setattr(FakeStore, "user", {"id": 9, "username": "sara",
                                            "role": "operator", "sees": "own"})
    client = web()
    client.login(username="sara")
    status, headers, _ = client.request("GET", "/pools/proxy")
    assert status == 303 and dict(headers)["Location"] == "/"
    status, _, answer = _json(client, "/pools/proxy/state")
    assert status == 403 and answer["said"] == "refused"


def test_the_state_is_json_and_a_304_when_nothing_moved(web, desk):
    client = web()
    client.login()
    status, headers, answer = _json(client, "/pools/proxy/state")
    assert status == 200 and answer["ok"] and answer["rev"] == assets.REV
    assert answer["state"] == json.loads(json.dumps(STATE))
    status, _, body = client.request("GET", "/pools/proxy/state",
                                     headers=dict(PAGE, **{"If-None-Match": headers["ETag"]}))
    assert status == 304 and not body


def test_a_request_waited_on_is_told_by_names_and_counts_only(web, desk, monkeypatch):
    monkeypatch.setattr(actions_mod, "one", lambda s, req: None if req == 6 else {
        "status": "failed", "result": "2 proxies added", "detail": {
            "added": ["W-02Oct-1", "W-02Oct-2"], "dead": ["W-02Oct-2"],
            "skipped": ["h:1"], "refused": ["h:2:user:S3CRET: bad port"]}})
    client = web()
    client.login()
    status, _, answer = _json(client, "/pools/proxy/state?req=5,6")
    assert answer["reqs"] == {"5": {"status": "failed", "result": "2 proxies added",
                                    "added": ["W-02Oct-1", "W-02Oct-2"],
                                    "dead": ["W-02Oct-2"], "skipped": 1, "refused": 1}}
    assert "S3CRET" not in json.dumps(answer), "a pasted line's password stays home"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_press_is_one_request_a_proxy_by_its_state(web, desk):
    client = web()
    client.login()
    answer = _press(client, "/pools/proxy/do", what="free", ids="7,8,9,99", press="p1")
    said = {i["id"]: i for i in answer["items"]}
    assert said[7]["said"] == "skip", "already in play"
    assert said[8]["said"] == "done", "set aside under its phone: kept on it"
    assert said[9] == {"id": 9, "said": "queued", "req": 102}, "tested first"
    assert said[99]["said"] == "gone"
    assert [a["verb"] for a in desk] == ["unshelve_proxy", "mark_proxy_free"]
    assert desk[0]["payload"]["name"] == "Webshare-ISP-02Oct-1"
    assert desk[0]["payload"]["by"] == "mehdi" and desk[0]["payload"]["by_id"] == 7
    assert answer["state"]["exits"][0]["n"] == "ISP3", "the pool comes back with it"
    del desk[:]
    _press(client, "/pools/proxy/do", what="aside", ids="7,9,10", press="p2")
    assert [(a["verb"], a["payload"]["name"]) for a in desk] == [
        ("shelve_proxy", "TSP10"), ("shelve_proxy", "ISP4")]
    del desk[:]
    _press(client, "/pools/proxy/do", what="lane:gpt", ids="7", press="p3")
    _press(client, "/pools/proxy/do", what="cap:3", ids="7", press="p4")
    _press(client, "/pools/proxy/do", what="cap:0", ids="7", press="p5")
    _press(client, "/pools/proxy/do", what="test", ids="7", press="p6")
    assert [(a["verb"], a["payload"].get("purpose", a["payload"].get("cap")))
            for a in desk] == [("keep_proxy_for", "gpt"), ("cap_proxy", 3),
                               ("test_proxy", None)], "no cap is already no cap"
    assert _press(client, "/pools/proxy/do", what="drop table", ids="7")["ok"] is False


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_batch_goes_to_the_farm_to_be_tested_and_named_there(web, desk):
    client = web()
    client.login()
    answer = _press(client, "/pools/proxy/add-batch", lines="u:p@h.example:1\nh2:2",
                    seller="Webshare", type="ISP", lane="gpt", cap="2", press="b1")
    assert answer["ok"] and answer["said"] == "queued" and answer["req"] == 101
    (asked,) = desk
    now = datetime.datetime.now(proxies_read.TEHRAN)
    assert asked["verb"] == "add_proxies"
    assert asked["payload"]["rows"] == [{"raw": "u:p@h.example:1", "name": ""},
                                        {"raw": "h2:2", "name": ""}]
    assert asked["payload"]["batch"] == {
        "seller": "Webshare", "type": "ISP",
        "tag": now.strftime("%d") + proxies_read.MONTHS[now.month - 1]}
    assert (asked["payload"]["purpose"], asked["payload"]["cap"]) == ("gpt", "2")
    refused = _press(client, "/pools/proxy/add-batch", lines="h:1", seller="!!")
    assert refused == {"ok": False, "note": "Name the seller to name them."}
    assert len(desk) == 1


def test_a_press_with_actions_off_changes_nothing(web, desk):
    client = web()
    client.login()
    answer = _press(client, "/pools/proxy/do", what="aside", ids="7")
    assert answer["ok"] is False and "not switched on" in answer["note"]
    assert not desk


def test_which_request_a_press_is_follows_the_state_drawn():
    verb = app_mod._proxy_verb
    free = {"s": "free", "after": False, "lane": "", "cap": 0}
    held = dict(free, s="phone")
    leaving = dict(held, after=True)
    for e, on, off in ((free, None, "shelve_proxy"), (held, None, "shelve_proxy"),
                       (leaving, "unshelve_proxy", None),
                       (dict(free, s="aside"), "mark_proxy_free", None),
                       (dict(free, s="dead"), "mark_proxy_free", None)):
        assert verb("free", e)[0] == on and verb("aside", e)[0] == off, e
    assert verb("lane:spotify", free) == ("keep_proxy_for", {"purpose": "spotify"})
    assert verb("lane:", free) == (None, {}), "already on either lane"
    assert verb("cap:2", free) == ("cap_proxy", {"cap": 2})
    assert verb("remove", held) == ("remove_proxy", {}), "the verb refuses a phone's"
    for word in ("free", "aside", "test", "remove", "lane:", "lane:gpt", "cap:0", "cap:99"):
        assert app_mod._PROXY_PRESS.fullmatch(word), word
    for word in ("cap:100", "lane:tiktok", "free ", "shelve"):
        assert not app_mod._PROXY_PRESS.fullmatch(word), word


def test_the_reader_reads_uses_presses_ends_and_batches():
    pool = [_row(7, "Webshare-ISP-02Oct-3", "set aside", "5441", last_exit_ip="1.2.3.4",
                 note="the exit stays", purpose="gpt", uses_per_day=2, day_uses=1,
                 day_uses_on=datetime.date(2026, 10, 2), created_at=_t(1, 20)),
            _row(8, "TSP10", error="bad port"),
            _row(9, "X1", "one-off", source="one-off"),
            _row(10, "", "free")]
    signins = [{"proxy_name": "Webshare-ISP-02Oct-3", "at": _t(1, 10), "ok": True,
                "serial": 5441, "exit_ip": "1.2.3.4"},
               {"proxy_name": "TSP10", "at": _t(1, 9), "ok": False, "serial": 5440,
                "exit_ip": ""}]
    moves = [{"requested_at": _t(1, 11), "result": "phone 5441 is on TSP10 now"}]
    phones = [{"serial": "5441", "created_at": _t(1, 9, 55), "done_at": _t(1, 12)},
              {"serial": "5440", "created_at": _t(1, 8, 55), "done_at": _t(1, 9, 1)}]
    presses = [{"proxy_name": "TSP10", "serial": 5441, "at": _t(1, 11, 30),
                "button": "or", "by_name": "ali"},
               {"proxy_name": "TSP10", "serial": 5441, "at": _t(2, 7),
                "button": "done", "by_name": "ali"}]
    got = proxies_read.assemble(pool, signins, moves, phones, presses,
                                geo={"1.2.3.4": {"cc": "CA", "isp": "Web2"}}, archived=5,
                                archived_names=["Webshare-ISP-02Oct-7", "TSP3"],
                                now=_t(2, 8))
    by = {e["n"]: e for e in got["exits"]}
    assert set(by) == {"TSP10", "Webshare-ISP-02Oct-3"}, "no one-off, no nameless row"
    tsp, web_ = by["TSP10"], by["Webshare-ISP-02Oct-3"]
    assert tsp["s"] == "dead"
    # Tehran times; a refused sign-in ends when its phone closes; the move
    # ends when the phone closes, and the later press wins.
    assert tsp["u"] == [["2026-10-01 12:30", 0, "", 0, "5440", "", "2026-10-01 12:31"],
                        ["2026-10-01 14:30", 2, "d", 0, "5441", "ali", "2026-10-01 15:30"]]
    assert tsp["v"] == {"t": [1, 0, 0, 0, 0], "d": [1, 0, 1, 0, 0], "a": [1, 0, 1, 0, 0]}
    assert (web_["s"], web_["after"], web_["serial"]) == ("phone", True, "5441")
    assert web_["u"] == [["2026-10-01 13:30", 1, "", 0, "5441", "", "2026-10-01 14:30"]]
    assert (web_["today"], web_["cap"], web_["lane"]) == (1, 2, "gpt")
    assert (web_["cc"], web_["isp"], web_["note"]) == ("CA", "Web2", "the proxy stays")
    assert (web_["seller"], web_["type"], web_["k"], web_["batch"]) == (
        "Webshare", "ISP", 3, "webshare|isp|02oct")
    assert (web_["ep"], web_["end"], web_["user"]) == ("h.example:1007:u7",
                                                        "H.example:1007", "u7")
    assert web_["added"] == "2026-10-01" and tsp["f"] == "TSP"
    assert got["issued"] == {"webshare|isp|02oct": 7}, "the archive's numbers count"
    assert got["since"] == {"t": "2026-10-02 00:00", "d": "2026-09-30 00:00", "a": ""}
    assert (got["track"], got["trackWord"]) == ("2026-10-01 00:00", "1 Oct")
    assert got["day"] == {"iso": "2026-10-02", "tag": "02Oct", "word": "2 Oct"}
    assert got["archived"] == 5
    assert "password" not in json.dumps(got) and "proxy_pass" not in json.dumps(got)


def test_the_proxies_pair_is_served_and_the_script_parses(tmp_path):
    assert assets.served(assets.PROXIES_CSS_PATH) == (
        assets.PROXIES_CSS, "text/css; charset=utf-8", False)
    assert assets.served(assets.PROXIES_JS_PATH) == (
        assets.PROXIES_JS, "text/javascript; charset=utf-8", True)
    assert assets.PROXIES_CSS.endswith(assets.RAIL_CSS), "the rail's rules ride along"
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the script is unchecked here")
    path = tmp_path / "proxies.js"
    path.write_text(assets.PROXIES_JS, encoding="utf-8")
    done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr.strip()
