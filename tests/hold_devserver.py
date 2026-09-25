"""Not a test: holds the `web` fixture's server up so the page can be
driven in a browser. Run explicitly, never collected:

    pytest tests/hold_devserver.py -s -p no:randomly

Writes the port to $GF_DEVPORT_FILE and stops when $GF_DEVSTOP_FILE
appears (or after twenty minutes). Sessions answer any cookie, so no
password is typed anywhere.
"""
import os
import time

import pytest

from tests.test_web import MANUAL_ON, FakeStore, _dash, web  # noqa: F401


def _rows():
    sellers = ["HOAVAN", "LEO", "Ali"]
    rows = []
    i = 0

    def add(address, status, state, serial="", error=None):
        nonlocal i
        i += 1
        rows.append({"id": i, "address": address, "status": status,
                     "seller": sellers[i % 3], "serial": serial, "note": "",
                     "error": error, "state": state, "password": "Hienluong102@",
                     "secret": "A66OUCIDONRH2WL2EYN3P24MA3J47OKU",
                     "second": "authenticator"})

    for n in range(22):
        add(f"fresh{n:02d}@gmail.com", "", "free")
    for n in range(5):
        add(f"held{n}@gmail.com", "in_use", "on a phone", serial=str(1500 + n))
    add("parked@gmail.com", "set_aside", "set_aside")
    for n in range(4):
        add(f"stuck{n}@gmail.com", "captcha_shown", "captcha_shown")
    for n in range(3):
        add(f"noauth{n}@gmail.com", "no_authenticator", "no_authenticator")
    add("torn@gmail.com", "", "broken", error="unreadable row")
    for n in range(9):
        add(f"gone{n}@gmail.com", "used", "used", serial=str(1400 + n))
    return rows


def _spotify_rows():
    """The Spotify pool as it looks after a week: both categories, a few
    already on phones, one set aside and some spent."""
    free = [("nova.reyes@outlook.com", "normal"),
            ("t.abernathy@yahoo.com", "error"),
            ("mila.sund@hotmail.com", "normal"),
            ("ryo.k1@proton.me", "error"),
            ("jun.park88@naver.com", "normal"),
            ("hedda.olsen@gmx.de", "normal"),
            ("marco.vidal@libero.it", "error"),
            ("aya.tanaka@outlook.jp", "normal")]
    rows, i = [], 2300
    for address, category in free:
        i += 1
        rows.append({"id": i, "address": address, "status": "", "serial": "",
                     "note": "", "error": None, "state": "free",
                     "category": category, "password": "Sp0t!" + address[:4],
                     "secret": "", "second": ""})
    for address, category, serial in (
            ("nel.sz@outlook.com", "normal", "3244"),
            ("k.duarte@mail.com", "error", "3242")):
        i += 1
        rows.append({"id": i, "address": address, "status": "in_use",
                     "serial": serial, "note": f"On phone {serial}.",
                     "error": None, "state": "on a phone",
                     "category": category, "password": "Sp0t!" + address[:4],
                     "secret": "", "second": ""})
    i += 1
    rows.append({"id": i, "address": "b.okafor@gmail.com",
                 "status": "needs_human", "serial": "",
                 "note": "The app would not take the password.",
                 "error": None, "state": "set aside", "category": "error",
                 "password": "Sp0t!bok", "secret": "", "second": ""})
    for n in range(4):
        i += 1
        rows.append({"id": i, "address": f"handed{n}@outlook.com",
                     "status": "delivered", "serial": str(3100 + n),
                     "note": "Delivered.", "error": None, "state": "used",
                     "category": "normal" if n % 2 else "error",
                     "password": "Sp0t!old", "secret": "", "second": ""})
    return rows


def _proxies():
    """Exits the way the vendor hands them over: most free, a few under
    a phone, a couple dead."""
    rows, i = [], 4100
    for n in range(14):
        i += 1
        rows.append({"id": i, "address": f"US{25 + n}", "state": "free",
                     "host": f"38.154.{n}.{110 + n}", "port": 6540 + n,
                     "exit_ip": f"38.154.{n}.{110 + n}", "times_used": n,
                     "serial": "", "note": ""})
    for n, serial in enumerate(("3239", "3241", "3242", "3244")):
        i += 1
        rows.append({"id": i, "address": f"US{50 + n}", "state": "on a phone",
                     "host": f"45.61.{n}.{12 + n}", "port": 6320 + n,
                     "exit_ip": f"45.61.{n}.{12 + n}", "times_used": 3 + n,
                     "serial": serial, "note": ""})
    for n in range(2):
        i += 1
        rows.append({"id": i, "address": f"SX{n + 3}", "state": "dead",
                     "host": f"104.239.{n}.{40 + n}", "port": 6712 + n,
                     "exit_ip": "", "times_used": 11 + n, "serial": "",
                     "note": "GeeLark could not reach it twice running."})
    return rows


def _gpt_rows():
    """The GPT pool, so the card beside Spotify is not one line long."""
    free = ["mehdifcb1331@gmail.com", "mhmdzare@gmail.com",
            "sara.kh2201@gmail.com", "arash.nd@gmail.com",
            "n.rostami87@gmail.com"]
    rows, i = [], 900
    for address in free:
        i += 1
        rows.append({"id": i, "address": address, "status": "", "serial": "",
                     "note": "", "error": None, "state": "free",
                     "password": "Gp7!" + address[:4], "secret":
                     "A66OUCIDONRH2WL2EYN3P24MA3J47OKU",
                     "second": "authenticator"})
    for address, serial in (("k.mohseni@gmail.com", "3241"),
                            ("h.salehi44@gmail.com", "3239")):
        i += 1
        rows.append({"id": i, "address": address, "status": "in_use",
                     "serial": serial, "note": f"On phone {serial}.",
                     "error": None, "state": "on a phone",
                     "password": "Gp7!old", "secret": "", "second": ""})
    i += 1
    rows.append({"id": i, "address": "m.zand@gmail.com",
                 "status": "needs_human", "serial": "",
                 "note": "Set aside after three phones refused it.",
                 "error": None, "state": "set aside", "password": "Gp7!zzz",
                 "secret": "", "second": "authenticator"})
    return rows


def _phones():
    """A shelf with both products on it, so the account column has
    something to say."""
    return [
        {"serial": "3239", "status": "ready", "state": "taken",
         "owner": "mehdi", "gmail": "dcivic034@gmail.com",
         "app_account": "z@x.com", "app_product": "", "app_category": "",
         "proxy_name": "US25", "updated_at": "2026-09-17 09:00:00+00"},
        {"serial": "3241", "status": "ready", "state": "",
         "gmail": "duffyetsys25@gmail.com", "app_account": "h@x.com",
         "app_product": "", "app_category": "", "proxy_name": "US27",
         "updated_at": "2026-09-17 09:20:00+00"},
        {"serial": "3242", "status": "ready", "state": "",
         "gmail": "dd6452502@gmail.com", "app_account": "k.duarte@mail.com",
         "app_product": "spotify", "app_category": "error",
         "proxy_name": "US28", "updated_at": "2026-09-17 10:05:00+00"},
        {"serial": "3243", "status": "app_only", "state": "",
         "gmail": "kalvin.b7@gmail.com", "app_account": "",
         "app_product": "", "app_category": "", "proxy_name": "US30",
         "updated_at": "2026-09-17 10:40:00+00"},
        {"serial": "3244", "status": "ready", "state": "", "gmail": "",
         "app_account": "nel.sz@outlook.com", "app_product": "spotify",
         "app_category": "normal", "proxy_name": "US33",
         "updated_at": "2026-09-17 11:10:00+00"},
        # On its maker's shelf: built by hand, released, and bootable by
        # nobody else (2026-09-18).
        {"serial": "3246", "status": "ready", "state": "", "owner": "mehdi",
         "built_by": "mehdi", "gmail": "",
         "app_account": "jacknikolnasli@gmail.com", "app_product": "spotify",
         "app_category": "normal", "proxy_name": "US39",
         "updated_at": "2026-09-17 11:30:00+00"},
        {"serial": "3247", "status": "ready", "state": "", "owner": "ali",
         "built_by": "ali", "gmail": "sam.pe22@gmail.com",
         "app_account": "w@x.com", "app_product": "", "app_category": "",
         "proxy_name": "US41", "updated_at": "2026-09-17 11:40:00+00"},
        {"serial": "3245", "status": "app_only", "state": "", "gmail": "",
         "app_account": "", "app_product": "", "app_category": "",
         "proxy_name": "US34", "updated_at": "2026-09-17 11:55:00+00"},
        {"serial": "3250", "status": "building", "state": "", "gmail": "",
         "app_account": "", "app_product": "", "app_category": "",
         "proxy_name": "US31", "updated_at": "2026-09-17 12:30:00+00"},
    ]


# Manual login on, as the server has it: without it no Send door is
# drawn at all, and those are half of what there is to look at.
@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_hold(web, monkeypatch):  # noqa: F811
    from geelark_farm.store import sessions as store_sessions

    spotify = _spotify_rows()
    base = _dash(monkeypatch, pool_rows={
        "gmail": _rows(), "gpt": _gpt_rows(),
        "spotify": spotify,
        "proxy": _proxies(), "totals": {"gmail": {"live": 36, "spent": 40}}},
          # The headline numbers say what the lists under them hold.
          stock={"gmail": {"free": 22, "on_phones": 5, "used": 9},
                 "proxy": {"free": 14, "on_phones": 4, "dead": 2},
                 "app": {"awaiting": 5, "panel": 2, "manual": 3}},
          phones=_phones(),
          spotify={"normal": sum(1 for r in spotify
                                 if r["state"] == "free"
                                 and r["category"] == "normal"),
                   "error": sum(1 for r in spotify
                                if r["state"] == "free"
                                and r["category"] == "error")},
          choose={"gmails": [{"label": f"fresh{n:02d}@gmail.com"} for n in range(6)],
                  "proxies": [{"label": f"SX{n}"} for n in (1, 4, 5, 10)],
                  "apps": [{"label": "mehdifcb1331@gmail.com"},
                           {"label": "mhmdzare@gmail.com"}]})
    # Nothing is connected: no queue, no cluster, no GeeLark. Every
    # press is written down nowhere and answers "Queued", which is what
    # the real console says when the keeper will do it on its next pass.
    # Without this the store raises on the first write and the page a
    # button lands on is "Something broke" (2026-09-17).
    import geelark_farm.runner as runner_mod
    import geelark_farm.store.actions as actions_mod
    ticket = {"n": 9000}

    def _enqueue(settings, **kw):
        ticket["n"] += 1
        print(f"  press: {kw.get('verb')} {kw.get('payload')}")
        return ticket["n"]

    monkeypatch.setattr(actions_mod, "enqueue", _enqueue)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    monkeypatch.setattr(actions_mod, "one", lambda s, req: None)
    monkeypatch.setattr(runner_mod, "run_now", lambda s, verb, payload: None)

    import geelark_farm.web.app as app_mod
    monkeypatch.setattr(app_mod.read, "known", lambda s, kind: {})
    monkeypatch.setattr(app_mod.read, "gmail_sellers", lambda s: ["LEO"])
    # Phone 4435's journey, from the real fixture (tests/fixtures/
    # journey.json): its page draws the stages, the screens from their
    # XML and - where $GF_DEVSHOT names a png - the screenshot
    # (2026-09-26).
    _journey_4435(monkeypatch, app_mod)
    monkeypatch.setattr(
        store_sessions, "find",
        lambda settings, token: {"user": dict(FakeStore.user), "csrf": "c1"})
    client = web()
    with open(os.environ["GF_DEVPORT_FILE"], "w") as f:
        f.write(str(client.port))
    stop = os.environ["GF_DEVSTOP_FILE"]
    # How long to hold it up, in minutes: twenty by default, longer
    # when somebody is testing by hand (GF_DEVMINUTES).
    minutes = int(os.environ.get("GF_DEVMINUTES") or 20)
    # A file at $GF_DEVFLIP_FILE makes warm phone 3243 ready, the way an
    # account going onto it does - so a list that should move while the
    # page is open can be watched moving (the Send sheet, 2026-09-26).
    flip = os.environ.get("GF_DEVFLIP_FILE") or ""
    for _ in range(minutes * 60):
        if os.path.exists(stop):
            break
        if flip and os.path.exists(flip):
            for phone in base.get("phones") or []:
                if phone.get("serial") == "3243":
                    phone.update(status="ready", app_account="flip@x.com")
            os.remove(flip)
            print("  flipped 3243 to ready")
        time.sleep(1)


def _journey_4435(monkeypatch, app_mod):
    import json
    import pathlib
    from datetime import datetime, timezone

    from geelark_farm.web import journey

    data = json.loads((pathlib.Path(__file__).parent / "fixtures"
                       / "journey.json").read_text(encoding="utf-8"))["4435"]
    runs = journey.runs_from(
        data["lines"], data["folders"],
        [datetime(2026, 9, 25, 17, 59, 28, tzinfo=timezone.utc)])
    real_story = app_mod.read.phone_story

    def story(settings, serial):
        if serial != "4435":
            return real_story(settings, serial)
        return {"serial": "4435", "phone": None, "stop_asked": False,
                "pending": "", "timeline": [
                    {"at": "2026-09-25 18:06:12+00", "source": "event",
                     "kind": "build_finished", "status": "phone_distrusted",
                     "run": "r1/1", "text": "ok=False", "seconds": 406}]}

    shot = os.environ.get("GF_DEVSHOT") or ""

    def screen(settings, serial, folder, name, *, suffix=".xml"):
        if serial != "4435":
            return None
        if suffix == ".png":
            return (pathlib.Path(shot).read_bytes()
                    if shot and os.path.exists(shot) else None)
        text = data["xml"].get(name)
        return text.encode("utf-8") if text else None

    monkeypatch.setattr(app_mod.read, "phone_story", story)
    monkeypatch.setattr(app_mod.read, "phone_journey",
                        lambda s, serial: runs if serial == "4435" else [])
    monkeypatch.setattr(app_mod.read, "screen_bytes", screen)
