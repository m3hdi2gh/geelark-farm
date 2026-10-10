"""The Gmails page (2026-10-07): its document in the admin rail, its state,
one Gmail's details for an admin, its presses - over real HTTP against the
`web` fixture's server, with the reader and the queue faked at their
seams - the reader's composition on plain rows, and the page's verbs with
the store's writes faked."""

from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess

import pytest

import geelark_farm.store.actions as actions_mod
import geelark_farm.web.app as app_mod
from geelark_farm import verbs
from geelark_farm.store import gmail_desk
from geelark_farm.web import assets, gmails_read
from tests import test_web as _test_web
from tests.test_web import MUTATIONS_ON, FakeStore, _form

web = _test_web.web
PAGE = {"X-GF-Station": "page"}
U = datetime.timezone.utc


def _t(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 10, day, hour, minute, tzinfo=U)


def _row(rid, address, status="", **more) -> dict:
    row = {"id": rid, "address": address, "seller": "LEO 6OCT", "status": status,
           "last_reason": "", "tries": 0, "retry_after": None, "serial": "",
           "created_at": _t(6, 8), "purchased_on": "2026-10-06", "used_at": "",
           "has_key": True, "has_rec": False, "note": "", "purpose": "",
           "refund_state": "", "error": None, "fixed_at": None, "fixed_by": ""}
    row.update(more)
    return row


def _state(*rows) -> dict:
    return gmails_read.assemble(list(rows), [], [], [], [], [], now=_t(7, 8))


STATE = _state(_row(7, "free@gmail.com"), _row(8, "aside@gmail.com", "set_aside"),
               _row(9, "on@gmail.com", "ready", serial="5441"))


@pytest.fixture
def desk(monkeypatch):
    """The reader answers STATE; the queue writes down what it is asked
    and every press runs at once, settled `done`."""
    asked = []
    monkeypatch.setattr(gmails_read, "state", lambda s, fresh=False: STATE)

    def enqueue(settings, *, verb, payload, requested_by, idem_key):
        asked.append({"verb": verb, "payload": payload, "idem": idem_key})
        return 100 + len(asked)

    monkeypatch.setattr(actions_mod, "enqueue", enqueue)
    monkeypatch.setattr(app_mod._Handler, "_ran_it_now",
                        lambda self, verb, payload, req: "done")
    monkeypatch.setattr(actions_mod, "one", lambda s, req: {
        "status": "done", "result": f"request {req} done", "verb": "gmails_aside",
        "detail": {"ids": [7], "left": {"8": "same"},
                   "changes": [{"id": 7, "before": {"password": "OLD-SECRET"},
                                "after": {"password": "NEW-SECRET"}}]}})
    monkeypatch.setattr(app_mod.signals, "ring", lambda *a, **k: None)
    monkeypatch.setattr(gmail_desk, "scrub_old", lambda *a, **k: 0)
    return asked


def _json(client, path):
    status, headers, body = client.request("GET", path, headers=PAGE)
    return status, dict(headers), (json.loads(body) if body else None)


def _press(client, path, **fields):
    status, _, body = client.request("POST", path, _form(csrf=client.csrf(), **fields),
                                     headers=PAGE)
    assert status == 200, body
    return json.loads(body)


# ------------------------------------------------------------------ the page
def test_the_page_is_the_prototype_in_the_admin_rail(web, desk):
    client = web()
    client.login()
    status, _, body = client.request("GET", "/pools/gmail")
    assert status == 200
    assert '<a href="/pools/gmail" class="rail-link here" aria-current="page">' in body
    assert '<span class="rail-lbl">Gmails</span>' in body, "the rail names it"
    assert assets.GMAILS_CSS_PATH in body and assets.GMAILS_JS_PATH in body
    assert f'<meta name="gf-rev" content="{assets.REV}">' in body
    island = re.search(r'<script type="application/json" id="gf-state">(.*?)</script>',
                       body).group(1)
    assert json.loads(island) == json.loads(json.dumps(STATE))
    assert "{{" not in body and "Prototype" not in body
    assert '<b>mehdi</b><small>admin</small>' in body, "the bar names who is in"
    assert 'id="gf-csrf"' in body and 'class="rail-shell"' in body
    assert "<title>Gmails — IranSpoty</title>" in body


def test_an_operator_never_reaches_it(web, desk, monkeypatch):
    monkeypatch.setattr(FakeStore, "user", {"id": 9, "username": "sara",
                                            "role": "operator", "sees": "own"})
    client = web()
    client.login(username="sara")
    status, headers, _ = client.request("GET", "/pools/gmail")
    assert status == 303 and dict(headers)["Location"] == "/"
    for path in ("/pools/gmail/state", "/pools/gmail/secret?id=7",
                 "/pools/gmail/archive"):
        status, _, answer = _json(client, path)
        assert status == 403 and answer["said"] == "refused", path
    for path in ("/pools/gmail/do", "/pools/gmail/save", "/pools/gmail/add-batch",
                 "/pools/gmail/revert"):
        status, _, body = client.request("POST", path, _form(csrf=client.csrf(), ids="7"),
                                         headers=PAGE)
        assert status == 403 and json.loads(body)["said"] == "refused", path
    assert not desk


def test_the_state_is_json_and_a_304_when_nothing_moved(web, desk):
    client = web()
    client.login()
    status, headers, answer = _json(client, "/pools/gmail/state")
    assert status == 200 and answer["ok"] and answer["rev"] == assets.REV
    assert answer["state"] == json.loads(json.dumps(STATE))
    status, _, body = client.request("GET", "/pools/gmail/state",
                                     headers=dict(PAGE, **{"If-None-Match": headers["ETag"]}))
    assert status == 304 and not body


def test_a_batchs_archive_is_asked_for_by_the_names_it_was_typed_under(web, desk,
                                                                      monkeypatch):
    """A batch with nothing left in the pool opens the archive on itself:
    the page sends every name the batch was typed under, a blank one too
    ("No seller"), and the whole archive is asked without any."""
    asked = []
    monkeypatch.setattr(gmails_read, "archive", lambda s, q="", limit=400, sellers=None: (
        asked.append((q, sellers)) or {"total": 0, "matched": 0, "rows": []}))
    client = web()
    client.login()
    _json(client, "/pools/gmail/archive?seller=LEO%2025SEP&seller=leo%2025%20sep&q=ab")
    _json(client, "/pools/gmail/archive?seller=")
    _json(client, "/pools/gmail/archive?q=x")
    assert asked == [("ab", ["LEO 25SEP", "leo 25 sep"]), ("", [""]), ("x", None)]


def test_a_request_waited_on_is_told_without_what_a_person_typed(web, desk):
    client = web()
    client.login()
    _, _, answer = _json(client, "/pools/gmail/state?req=5")
    told = answer["reqs"]["5"]
    assert told["status"] == "done" and told["ids"] == [7] and told["left"] == {"8": "same"}
    assert "SECRET" not in json.dumps(answer), "a save's old and new values stay home"


def test_one_gmails_details_are_an_admins_and_never_in_the_state(web, desk, monkeypatch):
    asked = []
    monkeypatch.setattr(gmail_desk, "secrets", lambda s, rid: asked.append(rid) or (
        {"id": 7, "address": "free@gmail.com", "pw": "Pw-1", "key": "JBSWY3DPEHPK3PXP",
         "rec": ""} if rid == 7 else None))
    client = web()
    client.login()
    _, _, answer = _json(client, "/pools/gmail/secret?id=7")
    assert answer == {"ok": True, "id": 7, "address": "free@gmail.com", "pw": "Pw-1",
                      "key": "JBSWY3DPEHPK3PXP", "rec": ""}
    _, _, answer = _json(client, "/pools/gmail/secret?id=99")
    assert answer["ok"] is False and "no longer in the pool" in answer["note"]
    _, _, answer = _json(client, "/pools/gmail/secret?id=x")
    assert answer["ok"] is False
    status, _, _ = client.request("GET", "/pools/gmail/secret?id=7")
    assert status == 403, "only the page's script asks: never a link opened"
    assert asked == [7, 99]
    text = json.dumps(STATE)
    assert "password" not in text and "totp" not in text and "recovery_email" not in text


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_press_is_one_request_for_the_whole_press(web, desk):
    client = web()
    client.login()
    answer = _press(client, "/pools/gmail/do", what="aside", ids="7,8,8", press="p1")
    assert answer["ok"] and answer["status"] == "done" and answer["req"] == 101
    assert answer["ids"] == [7] and answer["state"] == json.loads(json.dumps(STATE))
    assert "SECRET" not in json.dumps(answer)
    (asked,) = desk
    assert asked["verb"] == "gmails_aside"
    assert asked["payload"] == {"ids": [7, 8], "by": "mehdi", "by_id": 7}
    del desk[:]
    for what, verb in (("free", "gmails_queue"), ("mend", "gmails_mend"),
                       ("remove", "gmails_remove")):
        _press(client, "/pools/gmail/do", what=what, ids="9", press="p-" + what)
        assert desk[-1]["verb"] == verb
    _press(client, "/pools/gmail/do", what="for:spotify", ids="7", press="p5")
    _press(client, "/pools/gmail/do", what="for:", ids="7", press="p6")
    assert [(a["verb"], a["payload"]["lane"]) for a in desk[-2:]] == [
        ("gmails_keep_for", "spotify"), ("gmails_keep_for", "")]
    n = len(desk)
    assert _press(client, "/pools/gmail/do", what="for:tiktok", ids="7")["ok"] is False
    assert _press(client, "/pools/gmail/do", what="drop table", ids="7")["ok"] is False
    assert _press(client, "/pools/gmail/do", what="aside", ids="")["ok"] is False
    assert len(desk) == n, "nothing asked of the farm for those"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_save_sends_only_what_the_person_changed(web, desk):
    """A field left alone travels as None, so the farm keeps what it holds
    now - a form opened a while ago never writes back a value somebody
    changed since; an emptied box is told from an untouched one by its
    flag, since a blank field does not arrive at all."""
    client = web()
    client.login()
    _press(client, "/pools/gmail/save", id="7", password="Pw 2", has_password="1",
           key="jbsw y3dp", has_key="1", fixed="1", press="s1")
    _press(client, "/pools/gmail/save", id="7", has_note="1", has_rec="1", press="s2")
    _press(client, "/pools/gmail/save", id="7", has_note="1",
           note="mended by the seller", password="ignored", press="s3")
    sent = [{k: a["payload"][k] for k in ("password", "key", "recovery", "note", "fixed")}
            for a in desk]
    assert sent == [
        {"password": "Pw 2", "key": "jbsw y3dp", "recovery": None, "note": None, "fixed": True},
        {"password": None, "key": None, "recovery": "", "note": "", "fixed": False},
        {"password": None, "key": None, "recovery": None, "note": "mended by the seller",
         "fixed": False}], "only what was flagged changes; an emptied box is not one left alone"
    assert _press(client, "/pools/gmail/save", id="x", has_password="1",
                  password="p")["ok"] is False


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_paste_goes_as_the_page_read_it(web, desk):
    client = web()
    client.login()
    rows = [{"address": "a@gmail.com", "password": "p1", "key": "K" * 16,
             "recovery": "", "extra": "dropped"}, "not a row"]
    back = [{"address": "b@gmail.com", "password": "p2", "key": "", "recovery": "r@x.com"}]
    _press(client, "/pools/gmail/add-batch", rows=json.dumps(rows), back=json.dumps(back),
           seller="LEO 7OCT", lane="spotify", carry="3,4,x", press="a1")
    (asked,) = desk
    assert asked["verb"] == "gmails_add"
    p = asked["payload"]
    assert p["rows"] == [{"address": "a@gmail.com", "password": "p1", "key": "K" * 16,
                          "recovery": ""}]
    assert p["back"] == back and p["seller"] == "LEO 7OCT"
    assert (p["lane"], p["carry"]) == ("spotify", [3, 4])
    assert _press(client, "/pools/gmail/add-batch", rows="{oops")["ok"] is False
    assert _press(client, "/pools/gmail/add-batch", rows="[]", back="[]")["ok"] is False
    _press(client, "/pools/gmail/add-batch", rows=json.dumps(rows), lane="tiktok", press="a2")
    assert desk[-1]["payload"]["lane"] == ""


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_undo_names_the_presses_it_takes_back(web, desk):
    client = web()
    client.login()
    _press(client, "/pools/gmail/revert", reqs="12,13,x", press="u1")
    assert desk[-1]["verb"] == "gmails_revert" and desk[-1]["payload"]["reqs"] == [12, 13]
    assert _press(client, "/pools/gmail/revert", reqs="")["ok"] is False


def test_a_press_with_actions_off_changes_nothing(web, desk):
    client = web()
    client.login()
    for path in ("/pools/gmail/do", "/pools/gmail/save", "/pools/gmail/add-batch",
                 "/pools/gmail/revert"):
        answer = _press(client, path, what="aside", ids="7", id="7", reqs="1", rows="[]")
        assert answer["ok"] is False and "not switched on" in answer["note"], path
    assert not desk


def test_the_outcome_never_carries_a_typed_value():
    got = app_mod._gmail_outcome({
        "status": "done", "result": "1 Gmail saved", "verb": "gmail_save",
        "detail": {"ids": [7], "said": "password changed", "mended": True,
                   "changes": [{"id": 7, "before": {"password": "OLD"},
                                "after": {"password": "NEW"}}],
                   "left_out": [{"address": "x@gmail.com", "why": "is in the pool already",
                                 "password": "LEAK"}]}})
    assert got["ids"] == [7] and got["mended"] is True
    assert got["refused"] == [{"address": "x@gmail.com", "why": "is in the pool already"}]
    assert "OLD" not in json.dumps(got) and "NEW" not in json.dumps(got)
    assert "LEAK" not in json.dumps(got)


# ---------------------------------------------------------------- the reader
def test_the_reader_tells_each_place_and_when_a_waiting_one_comes_back():
    rows = [_row(1, "Free@Gmail.com"),
            _row(2, "wait@gmail.com", "captcha_shown", retry_after=_t(7, 9, 30),
                 last_reason="captcha_shown", tries=1),
            _row(3, "stop@gmail.com", "phone_verification_required",
                 refund_state="to_claim", tries=3),
            _row(4, "aside@gmail.com", "set_aside", retry_after=_t(7, 9)),
            _row(5, "held@gmail.com", "", refund_state="to_claim",
                 last_reason="password_changed"),
            _row(6, "broken@gmail.com", "", error="bad key"),
            _row(7, "on@gmail.com", "ready", serial="5441"),
            _row(8, "used@gmail.com", "used", used_at="2026-10-06 14:00",
                 serial="5400")]
    got = gmails_read.assemble(rows, [], [], [], [{"serial": "5441", "purpose": "spotify"}],
                               [], now=_t(7, 8))
    by = {e["id"]: e for e in got["rows"]}
    assert (by[1]["st"], by[1]["next"]) == ("", "")
    assert (by[2]["st"], by[2]["next"]) == ("captcha_shown", "2026-10-07 13:00"), \
        "waiting, back at its Tehran hour"
    assert (by[3]["st"], by[3]["next"]) == ("phone_verification_required", ""), \
        "on the seller's list: out of the queue, no hour"
    assert (by[4]["st"], by[4]["next"]) == ("set_aside", "")
    assert by[5]["st"] == "password_changed", "blank but on the list: stopped"
    assert by[6]["st"] == "unreadable"
    assert (by[7]["serial"], by[7]["on"]) == ("5441", "spotify")
    assert (by[8]["serial"], by[8]["used"]) == ("", "2026-10-06")
    assert by[1]["a"] == "Free@Gmail.com", "the address as the pool keeps it"
    assert all(set(e) >= {"key", "rec"} for e in got["rows"])
    assert got["day"] == {"iso": "2026-10-07", "tag": "7OCT", "word": "7 Oct"}


def test_the_reader_reads_sign_ins_presses_and_batches_over_the_archive():
    pool = [_row(1, "Mixed@Gmail.com", "used", seller="LEO 6OCT"),
            _row(2, "fresh@gmail.com", seller="SONJIT 7OCT", purchased_on="2026-10-07",
                 purpose="gpt", fixed_at=_t(7, 6), fixed_by="mehdi")]
    archived = [{"id": 50, "address": "old@gmail.com", "seller": "LEO 6OCT",
                 "status": "used", "purchased_on": "2026-10-05", "created_at": None}]
    signins = [
        {"gmail": "mixed@gmail.com", "at": _t(6, 9), "ok": False, "reason": "captcha_shown",
         "captcha_rounds": 11, "seconds": 210.4, "serial": "5001", "exit_ip": "1.2.3.4",
         "host": "h", "seller": "LEO 6OCT", "stage": "a"},
        {"gmail": "mixed@gmail.com", "at": _t(7, 5), "ok": True, "reason": "",
         "captcha_rounds": 2, "seconds": 300, "serial": "5002", "exit_ip": "",
         "host": "gw.example", "seller": "LEO 6OCT", "stage": "c"},
        {"gmail": "old@gmail.com", "at": _t(3, 9), "ok": True, "reason": "",
         "captcha_rounds": 0, "seconds": 100, "serial": "4000", "exit_ip": "",
         "host": "", "seller": "", "stage": ""}]
    presses = [
        {"gmail": "mixed@gmail.com", "at": _t(7, 6), "button": "done", "by_name": "ali",
         "serial": "5002", "lane": "", "app_account": "spot@x.com"},
        {"gmail": "old@gmail.com", "at": _t(3, 10), "button": "or", "by_name": "sara",
         "serial": "4000", "lane": "gpt", "app_account": ""},
        {"gmail": "mixed@gmail.com", "at": _t(7, 6, 5), "button": "cancel", "by_name": "x",
         "serial": "5002", "lane": "gpt", "app_account": ""}]
    got = gmails_read.assemble(pool, archived, signins, presses, [], [],
                               spotify=["Spot@X.com"], now=_t(7, 8))
    by = {e["id"]: e for e in got["rows"]}
    assert by[1]["t"] == [
        ["2026-10-06 12:30", 0, "captcha_shown", 11, 210, "5001", "1.2.3.4", "a"],
        ["2026-10-07 08:30", 1, "signed_in", 2, 300, "5002", "gw.example", "c"]], \
        "joined in small letters; the exit's address, else its host"
    assert by[1]["p"] == [["2026-10-07 09:30", "d", "ali", "5002", "spotify"]], \
        "a press from before lanes: a Spotify account means Spotify"
    assert by[2]["for"] == "gpt" and by[2]["back"] == {"at": "2026-10-07 09:30",
                                                         "by": "mehdi"}
    assert got["G"]["a"] == {"captcha_shown": 1, "signed_in": 2}
    assert got["G"]["t"] == {"signed_in": 1}, "today is Tehran's"
    assert got["GS"]["a"]["signed_in"] == {"c": 1, "": 1}
    assert got["V"]["a"] == {"done": 1, "or": 1}, "only the five keys count"
    assert got["VP"]["a"] == {"spotify": {"done": 1}, "gpt": {"or": 1}}
    assert got["VPB"]["LEO 6OCT"]["a"] == {"spotify": {"done": 1}, "gpt": {"or": 1}}
    leo = got["batch"]["LEO 6OCT"]
    assert (leo["n"], leo["arch"], leo["first"], leo["last"]) == (2, 1, "2026-10-05",
                                                                  "2026-10-06")
    assert leo["g"]["a"] == {"captcha_shown": 1, "signed_in": 2}
    assert leo["v"]["a"] == {"done": 1, "or": 1}
    assert got["batch"]["SONJIT 7OCT"]["n"] == 1
    assert got["bytry"] == [[1, 2, 1], [2, 1, 1]], "let in by try, over every Gmail"
    assert got["firstSignin"] == "2026-10-03"
    assert [d[0] for d in got["days"]] == ["2026-10-03", "2026-10-04", "2026-10-05",
                                           "2026-10-06", "2026-10-07"], \
        "the quiet days are days too: the pace reads the last three"
    assert got["days"][-1] == ["2026-10-07", 1, 1]
    assert got["archived"] == 1
    assert "now" not in got, "no clock: an unchanged pool is the same answer"


def test_a_read_begun_before_a_press_is_not_kept_over_it(monkeypatch):
    """A slower read that began before a press finishes after the press's
    own read: it answers its caller, and is not kept for the next."""
    import threading

    gate, reads = threading.Event(), []

    class Store:
        def __init__(self, settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def _rows(self, sql, params=()):
            if sql.startswith("SELECT id, address") and not reads:
                reads.append("slow")
                gate.wait(5)
            return []

    import geelark_farm.store.db as db_mod

    monkeypatch.setattr(db_mod, "Store", Store)
    import itertools

    calls = itertools.count()
    monkeypatch.setattr(gmails_read, "assemble", lambda *a, **k: {"call": next(calls)})
    gmails_read.forget()
    slow = {}
    t = threading.Thread(target=lambda: slow.update(got=gmails_read.state(None, fresh=True)))
    t.start()
    while not reads:
        pass
    gmails_read.forget()                  # a press lands
    reads.append("press")
    fresh = gmails_read.state(None, fresh=True)
    gate.set()
    t.join(5)
    assert fresh == {"call": 0} and slow["got"] == {"call": 1}, "the slow one answers its caller"
    assert gmails_read.state(None) == {"call": 0}, "and the press's read is the one kept"
    gmails_read.forget()


def test_a_gmail_the_page_cannot_place_counts_in_no_batch_of_its_own_making():
    """A press or a sign-in of a Gmail in neither the pool nor the archive -
    a row deleted for good - counts in the totals, and never under "No
    seller"; a sign-in that named its batch still counts there."""
    pool = [_row(1, "here@gmail.com", seller=""), _row(2, "kept@gmail.com", seller="LEO 1OCT")]
    signins = [{"gmail": "gone@gmail.com", "at": _t(6, 9), "ok": True, "reason": "",
                "seller": ""},
               {"gmail": "gone2@gmail.com", "at": _t(6, 9), "ok": False,
                "reason": "captcha_shown", "seller": "LEO 1OCT"},
               {"gmail": "here@gmail.com", "at": _t(6, 9), "ok": True, "reason": "",
                "seller": ""}]
    presses = [{"gmail": "gone@gmail.com", "at": _t(6, 10), "button": "done", "by_name": "a",
                "serial": "1", "lane": "gpt", "app_account": ""},
               {"gmail": "here@gmail.com", "at": _t(6, 10), "button": "or", "by_name": "a",
                "serial": "2", "lane": "gpt", "app_account": ""}]
    got = gmails_read.assemble(pool, [], signins, presses, [], [], now=_t(7, 8))
    assert sum(got["V"]["a"].values()) == 2, "both presses count in the totals"
    assert got["batch"][""]["v"]["a"] == {"or": 1}, "only the Gmail filed without a seller"
    assert got["batch"][""]["g"]["a"] == {"signed_in": 1}
    assert got["batch"]["LEO 1OCT"]["g"]["a"] == {"captcha_shown": 1}


def test_the_farms_notes_are_in_the_pages_words():
    words = gmails_read.note_words
    assert words("No phone or exit can fix this one - it is on the list to claim "
                 "back from LEO 6OCT.") == "No phone or proxy can fix this one."
    assert words("Usually a young account on an exit it distrusts; a better exit "
                 "sometimes clears it.") == ("Usually a young account on a proxy it "
                                             "distrusts; a better proxy sometimes "
                                             "clears it.")
    assert words("Press Free on the row to put it back in the pool and try it again "
                 "later, ideally on a residential exit.") == (
        "Turn it on in its row to put it back in the pool and try it again later, "
        "ideally on a residential proxy.")
    assert words("Exits rest. The refund is owed.") == "Proxies rest."
    assert words("") == ""


def test_a_gmails_story_is_read_off_the_requests_that_changed_it():
    pool = [_row(1, "a@gmail.com"), _row(2, "b@gmail.com")]

    def ev(rid, verb, at, detail=None, payload=None):
        return {"id": rid, "verb": verb, "requested_at": at, "by_name": "",
                "payload": dict(payload or {}, by="mehdi"), "detail": detail or {}}

    events = [ev(10, "gmails_aside", _t(7, 5), {"ids": [1, 2]}),
              ev(11, "gmails_queue", _t(7, 5, 10), {"ids": [1]}),
              ev(12, "gmails_keep_for", _t(7, 5, 20), {"ids": [1], "lane": "spotify"}),
              ev(13, "gmail_save", _t(7, 5, 30), {"ids": [1], "said": "password changed",
                                                  "mended": True}),
              ev(14, "gmails_revert", _t(7, 5, 31), {"reqs": [11], "back": [1]}),
              ev(15, "free_gmail", _t(7, 5, 40), payload={"address": "B@gmail.com"}),
              ev(16, "gmails_add", _t(7, 5, 50), {"returned": [{"id": 2, "said": "key added"}]})]
    got = gmails_read.assemble(pool, [], [], [], [], events, now=_t(7, 8))
    by = {e["id"]: e for e in got["rows"]}
    assert [x[1:3] for x in by[1]["log"]] == [
        ["aside", "Set aside"], ["edit", "Kept for Spotify"],
        ["edit", "Details changed"], ["back", "Marked as fixed"]], \
        "the put-back that was undone is not part of its story"
    assert by[1]["log"][2] == ["2026-10-07 09:00", "edit", "Details changed",
                               "password changed · by mehdi"]
    assert [x[1:3] for x in by[2]["log"]] == [
        ["aside", "Set aside"], ["queue", "Put back in the queue"],
        ["edit", "Details changed"], ["back", "Marked as fixed"]]


# ------------------------------------------------------------------ the verbs
def _desk(monkeypatch, **answers):
    for name, value in answers.items():
        monkeypatch.setattr(gmail_desk, name, value)


def test_a_press_says_what_moved_and_why_the_rest_stayed(monkeypatch):
    _desk(monkeypatch, aside=lambda s, ids, by: {
        "changed": [{"id": 1, "address": "a@gmail.com", "before": {"status": ""},
                     "after": {"status": "set_aside"}, "ver": "v1"}],
        "left": {2: "phone", 3: "phone", 4: "same", 5: "unreadable"}})
    status, said, detail = verbs.gmails_aside(None, None, None, {"ids": [1, 2, 3, 4, 5],
                                                                 "by": "mehdi"}, None)
    assert status == "done"
    assert said == ("1 Gmail set aside by mehdi (2 on a phone; 1 already that way;"
                    " 1 with details the farm cannot read)")
    assert detail["ids"] == [1] and detail["left"] == {"2": "phone", "3": "phone",
                                                       "4": "same", "5": "unreadable"}
    assert detail["changes"][0]["ver"] == "v1"


def test_every_page_verb_runs_in_the_request_and_on_a_lane():
    for name in ("gmails_aside", "gmails_queue", "gmails_mend", "gmails_keep_for",
                 "gmails_remove", "gmail_save", "gmails_add", "gmails_revert",
                 "restore_gmail"):
        assert verbs.runs_inline(name), name
        assert getattr(verbs.VERBS[name], "lane_safe", False), name


def test_a_refused_save_says_why_and_changes_nothing(monkeypatch):
    def save(*a, **k):
        raise gmail_desk.Refused("x@gmail.com is on a phone now; its details stay as "
                                 "the build read them until the phone is done.")

    _desk(monkeypatch, save=save)
    status, said, detail = verbs.gmail_save(None, None, None, {"id": 7, "password": "p"},
                                            None)
    assert status == "refused" and "on a phone now" in said and detail is None


def test_undo_takes_back_only_the_persons_own_recent_presses(monkeypatch):
    now = datetime.datetime.now(U)
    rows = {
        1: {"verb": "gmails_aside", "status": "done", "requested_by": 7,
            "requested_at": now, "detail": {"changes": [{"id": 5, "ver": "a"}]}},
        2: {"verb": "gmails_aside", "status": "done", "requested_by": 8,
            "requested_at": now, "detail": {"changes": [{"id": 6, "ver": "b"}]}},
        3: {"verb": "gmails_aside", "status": "done", "requested_by": 7,
            "requested_at": now - datetime.timedelta(hours=1),
            "detail": {"changes": [{"id": 7, "ver": "c"}]}},
        4: {"verb": "gmails_add", "status": "done", "requested_by": 7,
            "requested_at": now, "detail": {"changes": [{"id": 8, "ver": "d"}]}},
        5: {"verb": "gmail_save", "status": "refused", "requested_by": 7,
            "requested_at": now, "detail": None}}
    monkeypatch.setattr(actions_mod, "one", lambda s, req: rows.get(req))
    seen = []
    _desk(monkeypatch, revert=lambda s, changes, by: seen.append(changes) or {
        "back": [5], "moved": []})
    status, said, detail = verbs.gmails_revert(None, None, None, {
        "reqs": [1, 2, 3, 4, 5, 9], "by": "mehdi", "by_id": 7}, None)
    assert seen == [[{"id": 5, "ver": "a"}]], \
        "someone else's, an old one, an add and a refused save are not taken back"
    assert status == "done" and detail == {"reqs": [1], "back": [5], "moved": []}
    assert said == "1 Gmail put back as they were by mehdi"
    status, said, _ = verbs.gmails_revert(None, None, None, {"reqs": [2], "by_id": 7}, None)
    assert status == "refused"


def test_the_paste_answer_counts_what_went_in_and_what_stayed_out(monkeypatch):
    _desk(monkeypatch, add=lambda s, rows, **k: {
        "added": [{"id": 1, "address": "a@gmail.com"}],
        "returned": [{"id": 2, "address": "b@gmail.com", "said": "key added",
                      "changed": [{"id": 2, "before": {"password": "OLD"}}],
                      "mended": True}],
        "refused": [{"address": "c@gmail.com", "why": "is in the pool already"}],
        "carried": [], "seller": "LEO 7OCT", "lane": ""})
    status, said, detail = verbs.gmails_add(None, None, None, {"rows": [{}], "by": "mehdi"},
                                            None)
    assert status == "done"
    assert said == "1 Gmail added to LEO 7OCT, 1 Gmail back fixed, 1 refused by mehdi"
    assert detail["added"] == [1] and detail["returned"] == [
        {"id": 2, "said": "key added", "mended": True}]
    assert detail["left_out"] == [{"address": "c@gmail.com", "why": "is in the pool already"}]
    assert "refused" not in detail, "Requests draws that key as lines of text"


def test_a_paste_is_judged_whole_before_anything_is_written(monkeypatch):
    """Too many lines, too many to carry or an unknown product: refused
    before the first row is written - never half a paste."""
    def connect(settings):
        raise AssertionError("a refused paste reached the store")

    monkeypatch.setattr(gmail_desk, "connect", connect)
    line = {"address": "a@gmail.com", "password": "p", "key": "", "recovery": ""}
    for kwargs, words in (
            ({"rows": [line] * 1500, "back": [line] * 501}, "at most 2000"),
            ({"rows": [line], "carry": list(range(1, 2002))}, "At most 2000"),
            ({"rows": [line], "lane": "tiktok"}, "not a product"),
            ({"rows": [line], "seller": "  "}, "Name the seller")):
        args = {"seller": "LEO 7OCT", "lane": "", "by": "t"}
        args.update(kwargs)
        rows = args.pop("rows")
        with pytest.raises(gmail_desk.Refused, match=words):
            gmail_desk.add(None, rows, **args)


class _AddConn:
    """A store that takes every new row and finds every refused one."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.last = sql
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return (11,) if self.last.startswith("INSERT") else (5,)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_a_paste_whose_later_step_fails_keeps_what_it_added(monkeypatch):
    """The new rows are written first; a pasted-back line or the batch's
    product that fails after them is told as left out, never raised - a
    raise would run the whole paste again over the rows it added."""
    def boom(*a, **k):
        raise RuntimeError("the store hiccuped")

    monkeypatch.setattr(gmail_desk, "connect", lambda s: _AddConn())
    monkeypatch.setattr(gmail_desk, "save", boom)
    monkeypatch.setattr(gmail_desk, "keep_for", boom)
    got = gmail_desk.add(
        None, [{"address": "n@gmail.com", "password": "p", "key": "", "recovery": ""}],
        seller="LEO 7OCT", lane="gpt", by="t",
        back=[{"address": "o@gmail.com", "password": "p2", "key": "", "recovery": ""}],
        carry=[3])
    assert got["added"] == [{"id": 11, "address": "n@gmail.com"}]
    assert got["returned"] == [] and got["carried"] == []
    assert got["refused"] == [{"address": "o@gmail.com",
                               "why": "could not be saved just now; paste its line again"}]


def test_restore_puts_a_removed_gmail_back_by_its_id(monkeypatch):
    asked = []
    _desk(monkeypatch, restore=lambda s, rid, by: asked.append((rid, by)) or (
        {"id": rid, "address": "a@gmail.com", "ver": "v"} if rid == 5 else None))
    assert verbs.restore_gmail(None, None, None, {"id": "5", "by": "mehdi"}, None) == (
        "done", "a@gmail.com put back as it was by mehdi", {"ids": [5]})
    status, said, _ = verbs.restore_gmail(None, None, None, {"id": 6, "by": "mehdi"}, None)
    assert status == "refused" and "no longer in the archive" in said
    status, _, _ = verbs.restore_gmail(None, None, None, {"id": "-1"}, None)
    assert status == "refused" and asked == [(5, "mehdi"), (6, "mehdi")]


class _ScrubConn:
    def __init__(self, rows):
        self.rows, self.wrote, self.sql = rows, {}, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if sql.startswith("UPDATE"):
            self.wrote[params[2]] = (params[0].obj, params[1].obj)
        return self

    def fetchall(self):
        return self.rows

    def commit(self):
        pass


def test_the_typed_details_leave_a_settled_press_once_its_undo_has_passed(monkeypatch):
    save = {"id": 7, "password": "NEW-PW", "key": "JBSWY3DPEHPK3PXP", "recovery": "r@x.org",
            "note": "kept", "fixed": True, "by": "mehdi"}
    save_detail = {"ids": [7], "said": "password changed", "changes": [
        {"id": 7, "before": {"password": "OLD", "totp_secret": "K", "recovery_email": "",
                             "note": "a", "status": "x"},
         "after": {"password": "NEW-PW", "totp_secret": "K2", "recovery_email": "r@x.org",
                   "note": "b", "status": ""}, "ver": "v"}]}
    paste = {"rows": [{"address": "n@gmail.com", "password": "P1", "key": "K", "recovery": ""}],
             "back": [{"address": "o@gmail.com", "password": "P2", "key": "", "recovery": ""}],
             "seller": "LEO 7OCT", "lane": "", "carry": [3], "by": "mehdi"}
    conn = _ScrubConn([(1, save, save_detail), (2, paste, None), (3, "odd", ["odd"])])
    monkeypatch.setattr(gmail_desk, "connect", lambda s: conn)
    assert gmail_desk.scrub_old(None, every=0) == 3
    sql, params = conn.sql[0]
    assert "status IN ('done', 'failed', 'refused', 'cancelled')" in sql
    assert params[1] == datetime.timedelta(minutes=20), "past the Undo window"
    payload, detail = conn.wrote[1]
    assert payload == {"id": 7, "note": "kept", "fixed": True, "by": "mehdi"}
    change = detail["changes"][0]
    assert change["before"] == {"note": "a", "status": "x"} and change["after"] == {
        "note": "b", "status": ""}
    assert detail["said"] == "password changed" and detail["scrubbed"] == "1"
    payload, detail = conn.wrote[2]
    assert payload["rows"] == [{"address": "n@gmail.com"}]
    assert payload["back"] == [{"address": "o@gmail.com"}] and payload["seller"] == "LEO 7OCT"
    assert detail == {"scrubbed": "1"}
    assert conn.wrote[3] == ({}, {"scrubbed": "1"})
    assert "NEW-PW" not in json.dumps([p for p, _ in conn.wrote.values()])
    # A look a minute; a store that cannot be reached is no error.
    assert gmail_desk.scrub_old(None) == 0 and len(conn.sql) == 4
    monkeypatch.setattr(gmail_desk, "connect", lambda s: (_ for _ in ()).throw(OSError("down")))
    assert gmail_desk.scrub_old(None, every=0) == 0


def test_only_what_was_touched_answers_to_the_pages_stricter_words():
    """A key the farm already holds that is short but works is no reason to
    refuse a note; the same key typed now is."""
    check = gmail_desk.check_details
    short = "JBSWY3DPEHPK"
    assert check("a@gmail.com", "p", short, "Back@X.org", touched=["password"]) == {
        "password": "p", "key": short, "recovery": "Back@X.org"}
    with pytest.raises(gmail_desk.Refused, match="16 or more letters"):
        check("a@gmail.com", "p", short, "", touched=["key"])
    with pytest.raises(gmail_desk.Refused, match="not an authenticator key"):
        check("a@gmail.com", "p", "not a key!", "", touched=[])


def test_an_edit_of_a_gmail_with_both_factors_keeps_both():
    """The old editor writes one Secret cell back, which `_set` maps onto
    the key's column: a row with a key and a recovery address keeps both
    when the key comes back, and an address never stays in the key's
    column."""
    from geelark_farm.store.pgpool import PgGmailPool

    def split(secret, before):
        return PgGmailPool._split_secret(None, {"Secret": secret},
                                         {"totp_secret": secret, "seller": "s"}, before)

    both = {"Secret": "JBSWY3DPEHPK3PXP", "Recovery Email": "back@x.org"}
    assert split("JBSWY3DPEHPK3PXP", both) == {"totp_secret": "JBSWY3DPEHPK3PXP",
                                               "seller": "s"}, "the address stays"
    assert split("JBSWY3DPEHPK3PXQ", both) == {"totp_secret": "JBSWY3DPEHPK3PXQ",
                                               "seller": "s"}
    assert split("other@x.org", both) == {"recovery_email": "other@x.org",
                                          "totp_secret": "", "seller": "s"}
    assert split("", both) == {"totp_secret": "", "recovery_email": "", "seller": "s"}
    key_only = {"Secret": "JBSWY3DPEHPK3PXP", "Recovery Email": ""}
    assert split("new@x.org", key_only) == {"recovery_email": "new@x.org",
                                            "totp_secret": "", "seller": "s"}
    rec_only = {"Secret": "back@x.org", "Recovery Email": "back@x.org"}
    assert split("back@x.org", rec_only) == {"recovery_email": "back@x.org",
                                             "totp_secret": "", "seller": "s"}
    assert split("JBSWY3DPEHPK3PXQ", rec_only) == {"totp_secret": "JBSWY3DPEHPK3PXQ",
                                                   "recovery_email": "", "seller": "s"}


def test_a_details_check_says_what_is_wrong_in_the_pages_words():
    check = gmail_desk.check_details
    assert check("a@gmail.com", " Pw-1 ", "jbsw y3dp ehpk 3pxp", "Back@Outlook.com") == {
        "password": "Pw-1", "key": "JBSWY3DPEHPK3PXP", "recovery": "back@outlook.com"}
    for args, words in ((("a@gmail.com", "", "", ""), "needs its password"),
                        (("a@gmail.com", "p", "abc", ""), "16 or more letters"),
                        (("a@gmail.com", "p", "", "nope"), "looks like name@outlook.com"),
                        (("a@gmail.com", "p", "", "A@gmail.com"), "another mailbox")):
        with pytest.raises(gmail_desk.Refused, match=words):
            check(*args)


# ------------------------------------------------------------- the paste
#: Lines as sellers' sheets hand them over, and the ways they go wrong. A
#: password here carries the characters real ones do.
_PASTE = [
    "a1@gmail.com\tPw:72&93$#\tJBSWY3DPEHPK3PXP",
    "a2@gmail.com\tp;w|d,1\trec@outlook.com",
    "a3@gmail.com\tmy pass word\tjbsw y3dp ehpk 3pxp",
    "Pw-first-4\ta4@gmail.com\tJBSWY3DPEHPK3PXP",
    "a5@gmail.com\trec5@outlook.com\tPw-5",
    "17\ta6@gmail.com\tPw-6\tJBSWY3DPEHPK3PXP",
    "a7@gmail.com\tPw-7\tJBSWY3DPEHPK3PXP\tsold 2 Oct",
    "a8@gmail.com Pw-8 jbsw y3dp ehpk 3pxp",
    "a9@gmail.com,Pw-9,rec9@outlook.com",
    "a10@gmail.com\tA@123456789.b",
    "a11@gmail.com\tchaobuoisangvuive\tJBSWY3DPEHPK3PXP",
    "a12@gmail.com\tJBSWY3DPEHPK3PXP",
    "a13@gmail.com",
    "no address here\tPw",
    "a14@gmail.com\t123456789012345678",
    "  a15@gmail.com   Pw-15   ",
]


def _read_in_the_page(lines):
    import pathlib

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    js = pathlib.Path(assets.__file__).with_name("static").joinpath("gmails.js").read_text(
        encoding="utf-8")
    reader = js[js.index("/* reader:start"):js.index("/* reader:end */")]
    script = reader + "\nprocess.stdout.write(JSON.stringify(JSON.parse(process.argv[1]).map(parseLine)));"
    done = subprocess.run([node, "-e", script, json.dumps(lines)], capture_output=True,
                          text=True, timeout=60, check=True)
    return json.loads(done.stdout)


def test_the_page_reads_a_paste_as_the_dashboard_does():
    """Every line is read by both readers to the same address, password,
    key, recovery address and pieces left over - the page's reader is the
    dashboard's, so a batch the dashboard takes is taken here."""
    from geelark_farm.web import paste

    page = _read_in_the_page(_PASTE)
    for line, mine, theirs in zip(_PASTE, page, paste.accounts("\n".join(_PASTE)), strict=True):
        if not theirs["address"]:
            assert mine is None, line
            continue
        wanted = {"address": theirs["address"].lower(), "pass": theirs["password"],
                  "key": theirs["secret"], "rec": theirs["recovery"].lower(),
                  "odd": theirs["unread"]}
        got = {k: mine[k] for k in wanted}
        assert got == wanted, line
        assert bool(mine["error"]) == ("could not tell" in str(theirs.get("error") or "")), line
    # What the sellers send, read as they mean it.
    by = {line: x for line, x in zip(_PASTE, page)}
    assert by[_PASTE[0]]["pass"] == "Pw:72&93$#" and by[_PASTE[0]]["key"] == "JBSWY3DPEHPK3PXP"
    assert by[_PASTE[1]]["pass"] == "p;w|d,1" and by[_PASTE[1]]["rec"] == "rec@outlook.com"
    assert by[_PASTE[3]]["pass"] == "Pw-first-4", "the password before the address"
    assert by[_PASTE[6]]["odd"] == ["sold 2 Oct"], "a piece it cannot place is said"


def test_the_page_also_reads_a_sellers_colons_dashes_and_both_factors():
    page = _read_in_the_page([
        "name@gmail.com:Pw-5512:LM4TQ6BHX2YKNZ3W",
        "b2@gmail.com|Pw-2|rec2@outlook.com",
        "b3@gmail.com\tPw-3\tJBSW-Y3DP-EHPK-3PXP",
        "b4@gmail.com\tPw-4\tJBSWY3DPEHPK3PXP\trec4@outlook.com"])
    assert [(x["address"], x["pass"], x["key"], x["rec"], x["odd"]) for x in page] == [
        ("name@gmail.com", "Pw-5512", "LM4TQ6BHX2YKNZ3W", "", []),
        ("b2@gmail.com", "Pw-2", "", "rec2@outlook.com", []),
        ("b3@gmail.com", "Pw-3", "JBSWY3DPEHPK3PXP", "", []),
        ("b4@gmail.com", "Pw-4", "JBSWY3DPEHPK3PXP", "rec4@outlook.com", [])]
    assert page[3]["second"] == "key and recovery"


# --------------------------------------------------------------- the assets
def test_the_gmails_pair_is_served_and_the_script_parses(tmp_path):
    assert assets.served(assets.GMAILS_CSS_PATH) == (
        assets.GMAILS_CSS, "text/css; charset=utf-8", False)
    assert assets.served(assets.GMAILS_JS_PATH) == (
        assets.GMAILS_JS, "text/javascript; charset=utf-8", True)
    assert assets.GMAILS_CSS.endswith(assets.RAIL_CSS), "the rail's rules ride along"
    assert "stand-in" not in assets.GMAILS_JS and "Prototype" not in assets.GMAILS_CSS
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the script is unchecked here")
    path = tmp_path / "gmails.js"
    path.write_text(assets.GMAILS_JS, encoding="utf-8")
    done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr.strip()


# ------------------------------------------------------------- the farm side
def test_a_build_takes_its_lanes_gmails_then_the_unlabelled_never_the_others():
    """A Gmail kept for one product (the Gmails page): the claim asks the
    table for the lane's own first, then for the ones kept for neither -
    two statements, as the exits' are - and never for the other lane's."""
    from geelark_farm.store.pgpool import PgGmailPool

    asked = []

    class Table:
        _settings = None

        def rows(self, kind):
            return []

        def claim(self, kind, **k):
            asked.append(k["held_back"])
            return None

        def free_count(self, kind, *, free, hold_tries_from=None,
                       held_back=("", ())):
            asked.append(("count", held_back))
            return {"gpt": 1, "spotify": 4}.get((held_back[1] or ("",))[-1], 0)

    pool = PgGmailPool(Table())
    assert pool.claim(purpose="spotify") is None
    assert asked == [(" AND lower(coalesce(purpose, '')) = %s", ("spotify",)),
                     (" AND lower(coalesce(purpose, '')) = %s", ("",))]
    del asked[:]
    pool.claim()
    assert asked == [("", ())], "no lane asked: any row, as before"
    del asked[:]
    pool.claim(purpose="other")
    claims = [a for a in asked if a[0] != "count"]
    assert [c[1] for c in claims] == [("",), ("spotify",), ("gpt",)], \
        "a hand build for another app: unlabelled first, then the lane that can spare it"
    del asked[:]
    assert pool.free_now("gpt") == 1, "the table's own count"
    assert asked == [("count", (" AND lower(coalesce(purpose, '')) IN (%s, '')",
                                ("gpt",)))], "a lane counts its own and the unlabelled"


def test_the_keeper_orders_no_build_a_lane_has_no_gmail_for():
    from geelark_farm import serve as serve_mod

    targets = {"gpt": 4, "spotify": 4}
    lanes = {"gpt": {"warm": 0, "exits": 9, "gmails": 0},
             "spotify": {"warm": 0, "exits": 9, "gmails": 3}}
    assert serve_mod._lanes_to_build(6, lanes, targets) == {"gpt": 0, "spotify": 3}
    # A pool that cannot count by lane: the exits alone bound it, as before.
    plain = {"gpt": {"warm": 0, "exits": 1}, "spotify": {"warm": 0, "exits": 1}}
    assert serve_mod._lanes_to_build(6, plain, targets) == {"gpt": 1, "spotify": 1}


def test_the_look_counts_each_lanes_gmails_when_the_pool_can():
    from types import SimpleNamespace

    from geelark_farm import serve as serve_mod

    class Exits:
        available = [1, 2]

        @staticmethod
        def for_lane(lane):
            return [1]

    class Gmails:
        @staticmethod
        def free_now(lane=""):
            return {"gpt": 2, "spotify": 5}[lane]

    book = SimpleNamespace(proxies=Exits(), gmails=Gmails())
    got = serve_mod._lanes(book, [])
    assert got == {"gpt": {"warm": 0, "exits": 1, "gmails": 2},
                   "spotify": {"warm": 0, "exits": 1, "gmails": 5}}

    class Broken:
        @staticmethod
        def free_now(lane=""):
            raise RuntimeError("no store")

    got = serve_mod._lanes(SimpleNamespace(proxies=Exits(), gmails=Broken()), [])
    assert "gmails" not in got["gpt"], "an uncountable lane is bounded by its exits"


def test_a_sign_ins_stage_is_read_off_the_screens_it_went_through():
    from geelark_farm import builder
    from geelark_farm.store import signins
    from types import SimpleNamespace

    stage = signins.stage_of
    assert stage(["email_entry", "captcha", "captcha"]) == "a"
    assert stage(["email_entry", "password_entry", "2fa_verify_phone"]) == "p"
    assert stage(["email_entry", "password_without_a_box"]) == "p"
    assert stage(["email_entry", "password_entry", "2fa_code_entry"]) == "c"
    assert stage(["email_entry", "password_entry", "recovery_email_confirm"]) == "c"
    assert stage([]) == "" and stage(None) == ""
    assert builder._stage_of(SimpleNamespace(trail=["password_entry"])) == "p"
    assert builder._stage_of(SimpleNamespace()) == ""


def test_the_pair_up_asks_for_the_builds_own_lane(monkeypatch, make_settings):
    from types import SimpleNamespace

    from geelark_farm import builder

    asked = []

    class Gmails:
        def claim(self, serial="", avoid_host="", purpose=""):
            asked.append(purpose)
            return SimpleNamespace(values={"Last Host": ""}, label="g@x.com")

        def release(self, resource, *, note="", phone_failed=False):
            raise AssertionError("nothing to give back")

    monkeypatch.setattr(builder.kit_exits, "_fresh_proxy",
                        lambda client, book, **k: SimpleNamespace(name="SX1"))
    book = SimpleNamespace(gmails=Gmails())
    row, exit_row = builder._pair_up(None, book, make_settings(), purpose="spotify")
    assert asked == ["spotify"] and exit_row.name == "SX1"
