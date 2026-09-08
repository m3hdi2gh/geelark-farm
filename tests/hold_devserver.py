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

from tests.test_web import MUTATIONS_ON, FakeStore, _dash, web  # noqa: F401


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


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_hold(web, monkeypatch):
    from geelark_farm.store import sessions as store_sessions

    _dash(monkeypatch, pool_rows={
        "gmail": _rows(), "gpt": [
            {"id": 900, "address": "waiting@x.com", "status": "", "serial": "",
             "note": "", "error": None, "state": "free",
             "password": "pw", "secret": "", "second": ""}],
        "proxy": [], "totals": {"gmail": {"live": 36, "spent": 40}}})
    import geelark_farm.web.app as app_mod
    monkeypatch.setattr(app_mod.read, "known", lambda s, kind: {})
    monkeypatch.setattr(app_mod.read, "gmail_sellers", lambda s: ["LEO"])
    monkeypatch.setattr(
        store_sessions, "find",
        lambda settings, token: {"user": dict(FakeStore.user), "csrf": "c1"})
    client = web()
    with open(os.environ["GF_DEVPORT_FILE"], "w") as f:
        f.write(str(client.port))
    stop = os.environ["GF_DEVSTOP_FILE"]
    for _ in range(1200):
        if os.path.exists(stop):
            break
        time.sleep(1)
