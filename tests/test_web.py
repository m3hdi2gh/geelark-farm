"""The web UI, driven over real HTTP against a fake store.

Real sockets and real request parsing, because the handler's bugs live in
headers and cookies and encodings - a fake request object would test a
server nobody runs. The store side is faked instead: these tests must run
on a machine that has never seen the cluster.
"""

from __future__ import annotations

import http.client
import pathlib
import inspect
import re
import threading
import time

import pytest

import geelark_farm.web.app as app_mod
from geelark_farm.web import assets

#: What `read.pool_rows` hands the manager: one free row and one held,
#: per pool, which is enough for every question the sheet asks - a card
#: with something under it, a chip with something to filter, and a row
#: with the two doors on it.
FAKE_POOL_ROWS = {
    "gmail": [{"id": 1, "address": "free@gmail.com", "status": "",
               "seller": "dalir", "serial": "", "note": "", "error": None,
               "state": "free", "password": "Kx82!mnQ",
               "secret": "JBSWY3DPEHPK3PXP", "second": "authenticator"},
              {"id": 2, "address": "busy@gmail.com", "status": "in_use",
               "seller": "dalir", "serial": "1500", "note": "", "error": None,
               "state": "on a phone"}],
    "gpt": [{"id": 3, "address": "waiting@x.com", "status": "", "serial": "",
             "note": "", "error": None, "state": "free"}],
    "proxy": [{"id": 4, "address": "SX7", "status": "", "host": "1.2.3.4",
               "port": 1080, "exit_ip": "5.6.7.8", "times_used": 2,
               "serial": "", "note": "", "error": None, "state": "free"}],
}


class FakeStore:
    """check_login the way the real one answers: a row, or one None."""

    user = {"id": 7, "username": "mehdi", "role": "admin", "sees": "all"}
    password = "correct-horse"

    def __init__(self, settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def check_login(self, username, password):
        if username == self.user["username"] and password == self.password:
            return dict(self.user)
        return None


@pytest.fixture
def web(request, monkeypatch, make_settings):
    """A live server on an ephemeral port, faked reads, torn down after.

    Indirect parametrization with True turns web_mutations on for tests
    of the action verbs; everyone else gets the read-only default."""
    monkeypatch.setattr("geelark_farm.store.db.Store", FakeStore)
    monkeypatch.setattr(app_mod.read, "snapshot",
                        lambda s, owner_id=None: {
                            "phones": {"ready": 2, "app_only": 1},
                            "stock": {"gmail": {"free": 3, "unusable": 1}},
                            "last_event": None,
                            "scoped": owner_id})
    monkeypatch.setattr(app_mod.read, "phones",
                        lambda s, owner_id=None: [{
                            "serial": "1500", "status": "ready", "state": "",
                            "app_installed": True,
                            "gmail": "<script>alert(1)</script>",
                            "app_account": "", "proxy_name": "SX1",
                            "tries": 0, "note": "fine", "updated_at": ""}])
    monkeypatch.setattr(app_mod.read, "pools",
                        lambda s: {"counts": [], "broken": []})
    monkeypatch.setattr(app_mod.read, "dashboard", lambda s, owner_id=None: {
        "phones": [{"serial": "1500", "status": "ready", "state": "",
                    "gmail": "IronHawk@gmail.com", "app_account": "h@x.com",
                    "proxy_name": "SX27"},
                   {"serial": "1501", "status": "app_only", "state": "",
                    "gmail": "Stone@gmail.com", "app_account": "",
                    "proxy_name": "SX31"}],
        "stock": {"gmail": {"free": 12, "on_phones": 5, "used": 7},
                  "proxy": {"free": 20, "on_phones": 14, "dead": 1},
                  "app": {"awaiting": 2, "panel": 1, "manual": 1}},
        "awaiting": [{"address": "arman@gmail.com", "source": "panel",
                      "added_by": "", "created_at": None},
                     {"address": "gpt4.avir@proton.me", "source": "web",
                      "added_by": "mehdi", "created_at": None}],
        "queue": {"running": 0, "queued": 0},
        "recent": [],
        "pool_rows": FAKE_POOL_ROWS,
        "pulse": {"warm": 5, "target": 5, "tripped": "", "at": 0}})
    monkeypatch.setattr(app_mod.read, "events", lambda s, limit=200: [])
    monkeypatch.setattr(app_mod.read, "nav_counts",
                        lambda s: {"gmail": 3, "proxy": 2, "app": 1,
                                   "pending": 0})
    # every test gets clean auth state. Sessions live in the store now, so
    # what is faked is that module rather than a dict on the handler - the
    # seam is "a seat is kept somewhere", and where is `test_sessions`.
    _seats: dict[str, dict] = {}

    def _start(settings, user_id, *, hours):
        token = f"t{len(_seats) + 1}"
        # The row itself, not a copy: the real `find` reads it fresh from
        # the table on every request, so a change made in place must show
        # through the seat - while a *different* person signing in later
        # must not reach back and change this one.
        _seats[token] = {"user_id": user_id, "row": FakeStore.user,
                         "csrf": f"c{len(_seats) + 1}"}
        return token, _seats[token]["csrf"]

    def _find(settings, token):
        seat = _seats.get(token or "")
        if seat is None:
            return None
        return {"user": dict(seat["row"]), "csrf": seat["csrf"]}

    def _end(settings, token):
        _seats.pop(token or "", None)

    def _end_all_of(settings, user_id, *, keep=""):
        gone = [t for t, seat in _seats.items()
                if seat["user_id"] == user_id and t != keep]
        for token in gone:
            _seats.pop(token)
        return len(gone)

    from geelark_farm.store import sessions as store_sessions

    monkeypatch.setattr(store_sessions, "start", _start)
    monkeypatch.setattr(store_sessions, "find", _find)
    monkeypatch.setattr(store_sessions, "end", _end)
    monkeypatch.setattr(store_sessions, "end_all_of", _end_all_of)
    monkeypatch.setattr(app_mod, "_failures", {})

    overrides = getattr(request, "param", None)
    if isinstance(overrides, bool):          # the original shorthand
        overrides = {"web_mutations": overrides}
    settings = make_settings(store_enabled=True, web_enabled=True, web_port=0,
                             **(overrides or {}))
    server = app_mod.start(settings)
    port = server.server_address[1]

    class Client:
        def __init__(self):
            self.cookie = ""
            self.port = port

        def request(self, method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            headers = dict(headers or {})
            if self.cookie:
                headers["Cookie"] = self.cookie
            if body is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read().decode("utf-8")
            got_cookie = resp.getheader("Set-Cookie")
            if got_cookie:
                self.cookie = got_cookie.split(";")[0]
            conn.close()
            return resp.status, resp.getheaders(), data

        def login(self, password="correct-horse", username="mehdi"):
            return self.request(
                "POST", "/login",
                f"username={username}&password={password}")

        def csrf(self):
            """The token as a browser would learn it: off the page."""
            _, _, body = self.request("GET", "/")
            hit = re.search(r'name="csrf" value="([^"]*)"', body)
            return hit.group(1) if hit else ""

    try:
        yield Client
    finally:
        server.shutdown()
        server.server_close()


def test_every_page_demands_a_session_first(web):
    """Read-only is not public: the mirror holds addresses and notes."""
    status, headers, _ = web().request("GET", "/phones")
    assert status == 303
    assert dict(headers)["Location"] == "/login"


def test_a_good_login_sets_a_cookie_and_opens_the_dashboard(web):
    client = web()
    status, headers, _ = client.login()
    assert status == 303 and dict(headers)["Location"] == "/"
    assert client.cookie.startswith("gf=")

    status, _, body = client.request("GET", "/")
    assert status == 200 and "Dashboard" in body


def test_wrong_name_and_wrong_password_read_identically(web):
    """The login page must not be a username oracle."""
    client = web()
    _, _, wrong_pw = client.login(password="nope")
    _, _, wrong_user = client.login(username="ghost")
    assert wrong_pw == wrong_user


def test_five_failures_buy_a_lockout(web):
    client = web()
    for _ in range(5):
        client.login(password="nope")
    status, _, body = client.login(password="correct-horse")
    assert status == 429, "the right password bypassed the lockout"


def test_what_people_typed_into_the_sheet_cannot_script_the_page(web):
    """The mirror carries spreadsheet text; a Note or a Gmail cell is
    exactly where a <script> would sit."""
    client = web()
    client.login()
    _, _, body = client.request("GET", "/phones")
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_an_own_scoped_user_is_kept_out_of_the_farm_pages(web, monkeypatch):
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own"})
    client = web()
    client.login(username="narrow")
    status, _, _ = client.request("GET", "/events")
    assert status == 303  # sent home, not shown a wall
    status, _, _ = client.request("GET", "/needs")
    assert status == 303  # sent home, not shown a wall


def test_logout_needs_the_token_and_then_works(web):
    """CSRF in one story: a bare POST bounces and costs nothing, and the
    same POST with the page's own token ends the session for real."""
    client = web()
    client.login()
    status, _, _ = client.request("POST", "/logout", "x=1")
    assert status == 403
    status, _, _ = client.request("GET", "/")
    assert status == 200, "the refused POST killed the session"

    status, _, _ = client.request("POST", "/logout",
                                  f"csrf={client.csrf()}")
    assert status == 303
    status, headers, _ = client.request("GET", "/")
    assert status == 303 and dict(headers)["Location"] == "/login"


def test_a_token_from_another_session_buys_nothing(web):
    """Per-session tokens: knowing your own is not knowing anyone's."""
    alice, bob = web(), web()
    alice.login()
    bob.login()
    status, _, _ = bob.request("POST", "/logout", f"csrf={alice.csrf()}")
    assert status == 403


def test_a_foreign_origin_is_refused_even_with_the_token(web):
    client = web()
    client.login()
    token = client.csrf()
    status, _, _ = client.request(
        "POST", "/logout", f"csrf={token}",
        headers={"Origin": "https://evil.example"})
    assert status == 403
    # and the browser's own origin passes - the check is a filter, not a wall
    status, _, _ = client.request(
        "POST", "/logout", f"csrf={token}",
        headers={"Origin": f"http://127.0.0.1:{client.port}"})
    assert status == 303


def test_cancel_is_shut_while_the_mutations_flag_is_off(web):
    """Stage 3's promise survives stage 5: with the flag off, the web can
    still not change anything, token or no token."""
    client = web()
    client.login()
    status, _, body = client.request("POST", "/requests/5/cancel",
                                     f"csrf={client.csrf()}")
    assert status == 403 and "not switched on" in body


@pytest.mark.parametrize("web", [True], indirect=True)
def test_cancel_reaches_the_store_and_says_what_happened(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    got = {}

    def fake_cancel(settings, *, action_id, user_id, is_admin):
        got.update(action_id=action_id, user_id=user_id, is_admin=is_admin)
        return "cancelled"

    monkeypatch.setattr(actions_mod, "cancel", fake_cancel)
    client = web()
    client.login()
    status, headers, _ = client.request("POST", "/requests/5/cancel",
                                        f"csrf={client.csrf()}")
    assert status == 303
    assert dict(headers)["Location"] == "/requests?said=cancelled"
    assert got == {"action_id": 5, "user_id": 7, "is_admin": True}


def test_requests_page_offers_undo_only_while_queued(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    rows = [
        {"id": 2, "verb": "noop", "payload": {}, "status": "queued",
         "result": "", "requested_at": "2026-09-01 10:00:00+00",
         "executed_at": None, "requested_by": "mehdi"},
        {"id": 1, "verb": "noop", "payload": {}, "status": "done",
         "result": "did nothing, successfully",
         "requested_at": "2026-09-01 09:00:00+00",
         "executed_at": "2026-09-01 09:00:30+00", "requested_by": "mehdi"},
    ]
    monkeypatch.setattr(actions_mod, "listing", lambda s, **k: list(rows))
    client = web()
    client.login()
    status, _, body = client.request("GET", "/requests")
    assert status == 200
    assert body.count("/cancel") == 1, "undo offered off the queued row"
    assert "badge queued" in body and "badge done" in body
    assert 'http-equiv="refresh"' in body, "a pending list must follow itself"

    monkeypatch.setattr(actions_mod, "listing", lambda s, **k: rows[1:])
    _, _, body = client.request("GET", "/requests")
    assert 'http-equiv="refresh"' not in body, "a settled list sits still"
    assert "/cancel" not in body


def test_the_said_banner_speaks_only_known_words(web, monkeypatch):
    """?said= comes off the address bar, which is user input like any
    other: known tokens get their sentence, anything else gets silence."""
    import geelark_farm.store.actions as actions_mod

    monkeypatch.setattr(actions_mod, "listing", lambda s, **k: [])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/requests?said=cancelled")
    assert "Cancelled - it never ran." in body
    _, _, body = client.request("GET", "/requests?said=whatever")
    assert 'class="said"' not in body


def test_the_web_is_never_imported_with_the_flag_off(monkeypatch,
                                                     make_settings):
    """The same runtime promise the store makes: flag off means serve
    cannot touch web code at all."""
    import geelark_farm.serve as serve_mod

    settings = make_settings()
    assert not settings.web_enabled

    monkeypatch.setattr("geelark_farm.web.start",
                        lambda s: (_ for _ in ()).throw(
                            AssertionError("web imported with the flag off")))
    # run() would loop forever; the wiring block is what we are testing,
    # and it sits before the first pass - one pass with passes=0 exits.
    stop = threading.Event()
    stop.set()
    serve_mod.run(settings, stop=stop, passes=0)


def test_the_pages_speak_from_the_mirror_not_the_book():
    """The budget rule as an import rule: web.read may import store and
    config, never pools, builder, gsheet or api."""
    import ast
    import pathlib

    web_dir = pathlib.Path("src/geelark_farm/web")
    forbidden = {"pools", "builder", "gsheet", "api", "phones", "shell"}
    hits = []
    for path in web_dir.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if set(name.split(".")) & forbidden:
                    hits.append(f"{path.name}: {name}")
    assert not hits, f"the web reached past the mirror: {hits}"


def test_the_routine_sets_match_what_the_pools_call_settled():
    """ROUTINE is the one knowing duplication of the sheet vocabulary in the
    web (it may not import pools); this derives the same sets from the Pool
    classes and holds the two copies together - the SELLERS pin's shape."""
    from geelark_farm.pools import AppPool, GmailPool, ProxyPool
    from geelark_farm.web.read import ROUTINE

    for pool_cls, kind in ((GmailPool, "gmail"), (AppPool, "app"),
                           (ProxyPool, "proxy")):
        settled = set(pool_cls.available_statuses) | {
            pool_cls.claimed_status, pool_cls.spent_status,
            pool_cls.retired_status}
        assert ROUTINE[kind] == settled, (
            f"{kind}: the web and the pool disagree about what is settled")


def test_needs_page_names_the_orphans_and_explains_the_flags(web, monkeypatch):
    import geelark_farm.web.app as app_mod

    monkeypatch.setattr(app_mod.read, "needs", lambda s: {
        "orphaned": [{"kind": "app", "who": "nazarihassan1997@outlook.com",
                      "status": "ready", "serial": "1398"}],
        "flagged": [{"kind": "gmail", "who": "x@y.com",
                     "status": "wrong_password", "serial": "", "note": ""}],
        "broken": [], "given_up": []})
    client = web()
    client.login()
    status, _, body = client.request("GET", "/needs")

    assert status == 200
    assert "nazarihassan1997@outlook.com" in body and "1398" in body
    # the verdict's own words, not the token alone
    assert "would not take the password" in body


def test_needs_page_is_scope_gated_like_the_other_farm_pages(web,
                                                             monkeypatch):
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own"})
    client = web()
    client.login(username="narrow")
    status, _, _ = client.request("GET", "/needs")
    assert status == 303  # sent home, not shown a wall


def test_a_status_the_verdict_table_never_heard_of_renders_as_data(web,
                                                                   monkeypatch):
    """Rows written before a rename are data, not errors."""
    import geelark_farm.web.app as app_mod

    monkeypatch.setattr(app_mod.read, "needs", lambda s: {
        "orphaned": [], "given_up": [], "broken": [],
        "flagged": [{"kind": "gmail", "who": "old@row.com",
                     "status": "some_forgotten_word", "serial": "",
                     "note": ""}]})
    client = web()
    client.login()
    status, _, body = client.request("GET", "/needs")
    assert status == 200 and "some_forgotten_word" in body


# ------------------------------------------------------------- users (C1)
ADMIN_ON = {"web_user_admin": True}


def _people(monkeypatch, rows=None):
    """The users module as the routes see it: a listing and a lookup."""
    import geelark_farm.store.users as users_mod

    rows = rows if rows is not None else [
        {"id": 7, "username": "mehdi", "role": "admin", "sees": "all",
         "active": True, "must_change_password": False,
         "last_login_at": None, "created_at": None,
         **{c: False for c in users_mod.PERMISSION_COLUMNS}},
        {"id": 9, "username": "narrow", "role": "operator", "sees": "own",
         "active": True, "must_change_password": False,
         "last_login_at": None, "created_at": None,
         **{c: False for c in users_mod.PERMISSION_COLUMNS},
         "may_add_gmail": True},
    ]
    monkeypatch.setattr(users_mod, "listing", lambda s: list(rows))
    monkeypatch.setattr(users_mod, "get",
                        lambda s, uid: next((dict(r) for r in rows
                                             if r["id"] == uid), None))
    return users_mod


def test_the_users_page_does_not_exist_while_the_flag_is_off(web):
    """Flag off means the page is not there - not forbidden, absent."""
    client = web()
    client.login()
    status, _, body = client.request("GET", "/users")
    assert status == 404
    assert 'href="/users"' not in body


@pytest.mark.parametrize("web", [ADMIN_ON], indirect=True)
def test_the_users_page_is_admin_only(web, monkeypatch):
    _people(monkeypatch)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own"})
    client = web()
    client.login(username="narrow")
    status, _, _ = client.request("GET", "/users")
    assert status == 303  # sent home, not shown a wall
    status, _, _ = client.request("POST", "/users/new",
                                  f"csrf={client.csrf()}&username=x")
    assert status == 403, "a refused POST stays a refusal"


@pytest.mark.parametrize("web", [ADMIN_ON], indirect=True)
def test_an_admin_creates_a_person_and_sees_the_password_exactly_once(
        web, monkeypatch):
    users_mod = _people(monkeypatch)
    made = {}

    def create(settings, *, username, role, sees, permissions):
        made.update(username=username, role=role, sees=sees,
                    permissions=permissions)
        return 11, "once-only-pw"

    monkeypatch.setattr(users_mod, "create", create)
    client = web()
    client.login()
    status, _, listing = client.request("GET", "/users")
    assert status == 200 and "narrow" in listing and "add gmails" in listing

    status, _, body = client.request(
        "POST", "/users/new",
        f"csrf={client.csrf()}&username=Sara&role=operator&sees=own"
        f"&may_add_gmail=1&may_take_phones=1")
    assert status == 200 and "once-only-pw" in body
    assert made["username"] == "sara"          # lowered, like the tabs
    assert made["permissions"]["may_add_gmail"] is True
    assert made["permissions"]["may_take_phones"] is True
    assert made["permissions"]["may_add_gpt"] is False
    # and nowhere after that page
    _, _, again = client.request("GET", "/users")
    assert "once-only-pw" not in again


@pytest.mark.parametrize("web", [ADMIN_ON], indirect=True)
def test_a_permission_edit_ends_that_persons_sessions_but_not_the_editors(
        web, monkeypatch):
    """There is no second copy of the user row to refresh: a session acts
    on the rights it signed in with, so a change ends the session and the
    person signs in again with the new ones."""
    users_mod = _people(monkeypatch)
    saved = {}
    monkeypatch.setattr(users_mod, "update",
                        lambda s, uid, **kw: saved.update(id=uid, **kw))
    admin = web()
    admin.login()
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own"})
    narrow = web()
    narrow.login(username="narrow")
    assert narrow.request("GET", "/")[0] == 200

    status, headers, _ = admin.request(
        "POST", "/users/9",
        f"csrf={admin.csrf()}&role=operator&sees=own&active=1"
        f"&may_change_proxy=1")
    assert status == 303
    assert dict(headers)["Location"] == "/users?id=9&said=saved"
    assert saved["id"] == 9 and saved["by"] == 7
    assert saved["permissions"]["may_change_proxy"] is True
    assert saved["active"] is True

    status, headers, _ = narrow.request("GET", "/")
    assert status == 303 and dict(headers)["Location"] == "/login"
    assert admin.request("GET", "/")[0] == 200


@pytest.mark.parametrize("web", [ADMIN_ON], indirect=True)
def test_a_refused_edit_comes_back_with_the_reason(web, monkeypatch):
    users_mod = _people(monkeypatch)
    monkeypatch.setattr(users_mod, "update",
                        lambda s, uid, **kw: (_ for _ in ()).throw(
                            ValueError("that would leave no active admin")))
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/users/7", f"csrf={client.csrf()}&role=operator&sees=all"
                            f"&active=1")
    assert status == 303
    assert "leave%20no%20active%20admin" in dict(headers)["Location"]
    _, _, body = client.request("GET", dict(headers)["Location"])
    assert "leave no active admin" in body


def test_a_one_time_password_buys_only_the_page_to_replace_it(web,
                                                              monkeypatch):
    """Sign in with a minted password and every page is the one where you
    choose your own - until you have."""
    import geelark_farm.store.users as users_mod

    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own", "must_change_password": True})
    chosen = []

    def _chose(settings, uid, pw):
        chosen.append((uid, pw))
        # What the real one does, and the reason the handler no longer
        # patches a copy: `set_password` clears the flag in the row, and
        # the row is read again on the next request.
        FakeStore.user["must_change_password"] = False

    monkeypatch.setattr(users_mod, "set_password", _chose)
    client = web()
    client.login(username="narrow")
    status, headers, _ = client.request("GET", "/")
    assert status == 303 and dict(headers)["Location"] == "/password"
    status, _, body = client.request("GET", "/password")
    assert status == 200 and "Choose your password" in body
    token = re.search(r'name="csrf" value="([^"]*)"', body).group(1)

    status, _, body = client.request(
        "POST", "/password", f"csrf={token}&password=abcdefgh&again=nope")
    assert status == 200 and "do not match" in body and chosen == []

    status, headers, _ = client.request(
        "POST", "/password", f"csrf={token}&password=abcdefgh&again=abcdefgh")
    assert status == 303 and dict(headers)["Location"] == "/"
    assert chosen == [(9, "abcdefgh")]
    assert client.request("GET", "/")[0] == 200


# --------------------------------------------------------- the pools (C5)
MUTATIONS_ON = {"web_mutations": True}


def _gmail_row(address, status="", **more):
    row = {"id": 1, "address": address, "status": status, "serial": "",
           "seller": "egypt", "purchased_on": "2026-08-30", "used_at": "",
           "note": "", "updated_at": "2026-09-02 10:00:00", "has_totp": True,
           "has_recovery": False, "source": "sheet", "phone_status": "ready",
           "password": "pw", "totp_secret": "JBSWY3DPEHPK3PXP",
           "recovery_email": ""}
    row.update(more)
    return row


def _gmail_active(monkeypatch, seen=None, queued=None, on_phone=None):
    """read.gmail_pool as the page now asks for it: one view, one list of
    rows, and the counts every pill shows."""
    seen = seen if seen is not None else {}
    known = ["egypt", "usa"]
    counts = {"queued": 2, "on_phone": 1, "used": 5, "errored": 3,
              "owed": 2, "refunded": 0,
              "broken": 0}

    def gmail_pool(settings, view="queued", seller="", page=1, per_page=100):
        seen.update(view=view, seller=seller, page=page)
        out = {"view": view, "counts": counts, "seller": seller,
               "sellers": [{"seller": "egypt", "c": 2}],
               "known_sellers": known, "page": page, "pages": 1,
               "more": False, "total": counts.get(view, 0)}
        if view == "errored":
            out.update(
                rows=[dict(_gmail_row("bad1@x.com", "captcha_shown"),
                           tries=1, retry_after="2026-09-12 09:00:00",
                           refund_state=""),
                      dict(_gmail_row("bad2@x.com", "wrong_2fa_code"),
                           tries=3, retry_after=None,
                           refund_state="to_claim")],
                reasons=[{"status": "captcha_shown", "c": 1},
                         {"status": "wrong_2fa_code", "c": 1}],
                broken=[], total=2, pages=3, more=page < 3)
        elif view == "used":
            out.update(rows=[_gmail_row("old@x.com", "used", serial="1490",
                                        used_at="2026-08-30 08:00:00")],
                       pages=2, more=page < 2)
        elif view == "on_phone":
            out["rows"] = (on_phone if on_phone is not None else
                           [_gmail_row("on@x.com", "ready", serial="1551")])
        else:
            out["rows"] = (queued if queued is not None else
                           [_gmail_row("q1@x.com"), _gmail_row("q2@x.com")])
        return out

    monkeypatch.setattr(app_mod.read, "gmail_pool", gmail_pool)
    monkeypatch.setattr(app_mod.read, "gmail_sellers", lambda s: known)
    return seen


def test_the_rail_shows_the_stock_counts_and_lights_the_page(web,
                                                             monkeypatch):
    _gmail_active(monkeypatch)
    client = web()
    client.login()
    status, _, body = client.request("GET", "/pools/gmail")
    assert status == 200
    assert 'href="/pools/gmail" class="here"' in body
    assert '<span class="n">3</span>' in body          # gmail free count
    assert "q1@x.com" in body and "q2@x.com" in body, "queued is the default"
    assert '<b class="figure" style="color:var(--green)">2</b> free' in body
    assert "enough for the next 2 builds" in body
    assert 'Queued<span class="n" style="color:var(--green)">2</span>' in body, \
        "four pills, each count in the colour of what the view holds"
    assert 'href="/pools/gmail?view=errored"' in body

    _, _, body = client.request("GET", "/pools/gmail?view=on_phone")
    assert "on@x.com" in body and '<a href="/phones/1551">1551</a>' in body


def test_an_operator_has_the_dashboard_and_one_phone_and_nothing_else(
        web, monkeypatch):
    """The design's last requirement, and the one that made the rest of it
    necessary: an operator's whole day is the dashboard, so the pages that
    are somebody keeping the farm reading it are not theirs.

    Hiding the rail is not access. A link that is not drawn is still a URL,
    and an operator who once had these pages has them bookmarked - so this
    is the half of that decision that holds.
    """
    _gmail_active(monkeypatch)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own"})
    client = web()
    client.login(username="narrow")

    for path in ("/pools/gmail", "/pools/proxy", "/pools/gpt", "/pools",
                 "/requests", "/needs", "/events", "/logs", "/phones"):
        status, headers, _ = client.request("GET", path)
        assert status == 303 and dict(headers)["Location"] == "/", path

    # What is theirs: the dashboard, and one phone reached from it.
    assert client.request("GET", "/")[0] == 200
    # Whether this particular phone is theirs is the `sees` rule's answer,
    # and a different question - what matters here is that the gate above
    # is not the thing standing in the way.
    _, _, story = client.request("GET", "/phones/1523")
    assert "belongs to an admin" not in story
    # And there is no rail at all - not even one entry. A column down the
    # side whose only link is the page you are already on is furniture.
    _, _, body = client.request("GET", "/")
    assert "<nav>" not in body
    assert ">Gmail Pool<" not in body and ">Events<" not in body
    # The two things every console needs somewhere moved up beside the
    # title: who you are, and how you leave.
    assert 'class="whoout"' in body
    assert ">narrow<" in body and "Log out" in body


def test_an_operator_cannot_post_to_a_page_they_no_longer_have(
        web, monkeypatch):
    """The GET gate and the POST gate answer different questions, and a
    POST that slips through is a row written rather than a page seen."""
    _gmail_active(monkeypatch)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own", "may_add_gmail": True})
    client = web()
    client.login(username="narrow")
    token = client.csrf()

    # Ignoring an exit GeeLark holds is the admin's - an operator keeps
    # the pool (add, test, free, remove) but does not decide what the farm
    # stops accounting for. So this door is shut whatever the person can
    # do with the pools they own.
    status, _, body = client.request(
        "POST", "/pools/proxy/ignore",
        _form(csrf=token, host="1.2.3.4", port="1080", username="u"))
    assert status == 403 and "Nothing was changed" in body

    # The doors that stayed open are the ones the dashboard posts through:
    # adding stock moved onto it, and so did editing and removing a Gmail
    # row, because that is where a person now works on that pool. What the
    # handler then makes of the request is its own business.
    status, _, body = client.request(
        "POST", "/pools/gmail/remove", _form(csrf=token, address="a@x.com"))
    assert status != 403 and "Nothing was changed" not in body
    status, _, body = client.request(
        "POST", "/pools/gmail/preview", _form(csrf=token, pasted="a@x.com"))
    assert status != 403 and "Nothing was changed" not in body


def test_an_admin_keeps_every_page(web, monkeypatch):
    """The counterweight. Taking the console away from an operator must
    not take it away from the person who runs it."""
    _gmail_active(monkeypatch)
    client = web()
    client.login()
    assert client.request("GET", "/pools/gmail")[0] == 200


def test_buttons_stay_hidden_until_the_flag_and_the_permission_agree(
        web, monkeypatch):
    _gmail_active(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gmail")
    assert "/pools/gmail/preview" not in body, "flag off: no form"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_queued_row_shows_its_secret_and_password_and_can_be_edited(
        web, monkeypatch):
    """Everything the sheet keeps about an account, in the row: the value
    it answers a challenge with and which kind that is, the password, and
    the two buttons that change or remove it."""
    import geelark_farm.store.actions as actions_mod

    _gmail_active(monkeypatch, queued=[
        _gmail_row("key@x.com", id=11, password="pw-one",
                   totp_secret="JBSWY3DPEHPK3PXP", recovery_email=""),
        _gmail_row("rec@x.com", id=12, password="pw-two", has_totp=False,
                   has_recovery=True, totp_secret="",
                   recovery_email="backup@x.com"),
        _gmail_row("bare@x.com", id=13, password="", has_totp=False,
                   totp_secret="", recovery_email="")])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gmail")
    assert "<th>secret</th><th>password</th>" in body, "named, not '2fa'"
    assert "JBSWY3DPEHPK3PXP" in body and ">authenticator<" in body
    assert "backup@x.com" in body and ">recovery<" in body
    assert ">no second factor<" in body, "and the row to notice is loud"
    assert "pw-one" in body and "pw-two" in body
    assert 'href="/pools/gmail?view=queued&edit=11">Edit</a>' in body
    assert body.count('action="/pools/gmail/remove"') == 3

    # Edit draws that one row as a form, over every column
    _, _, body = client.request("GET", "/pools/gmail?view=queued&edit=12")
    assert '<tr class="editrow" data-key="gmail:' in body and 'colspan="6"' in body
    assert 'name="new_address" value="rec@x.com"' in body
    assert 'name="password" value="pw-two"' in body
    assert 'name="secret" value="backup@x.com"' in body
    assert 'name="seller" value="egypt"' in body
    assert 'name="purchased" value="2026-08-30"' in body
    assert '<datalist id="sellers">' in body, "the sellers already known"
    assert body.count('href="/pools/gmail?view=queued">Cancel</a>') == 1

    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 81)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    status, headers, _ = client.request(
        "POST", "/pools/gmail/edit",
        _form(csrf=client.csrf(), address="rec@x.com",
              new_address="rec2@x.com", password="pw-new",
              secret="other@x.com", seller="usa", purchased="2026-09-01",
              back="/pools/gmail?view=queued"))
    assert status == 303
    assert dict(headers)["Location"] == \
        "/pools/gmail?view=queued&said=queued:81"
    assert got["verb"] == "edit_gmail"
    assert got["payload"]["address"] == "rec@x.com"
    assert got["payload"]["new_address"] == "rec2@x.com"
    assert got["payload"]["secret"] == "other@x.com"
    assert got["payload"]["purchased"] == "2026-09-01"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_removing_a_gmail_asks_once_with_the_address(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    _gmail_active(monkeypatch)
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 82)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    client = web()
    client.login()
    status, _, body = client.request(
        "POST", "/pools/gmail/remove",
        _form(csrf=client.csrf(), address="q1@x.com",
              back="/pools/gmail?view=queued"))
    assert status == 200 and "Remove q1@x.com from the pool?" in body
    assert got == {}, "nothing queued until the person says so"

    status, headers, _ = client.request(
        "POST", "/pools/gmail/remove",
        _form(csrf=client.csrf(), address="q1@x.com", sure="1",
              back="/pools/gmail?view=queued"))
    assert status == 303
    assert dict(headers)["Location"] == \
        "/pools/gmail?view=queued&said=queued:82"
    assert got["verb"] == "remove_gmail"
    assert got["payload"]["address"] == "q1@x.com"

    _, headers, _ = client.request(
        "POST", "/pools/gmail/remove",
        _form(csrf=client.csrf(), address="q1@x.com", sure="1",
              back="/evil"))
    assert dict(headers)["Location"] == "/pools/gmail?said=queued:82", \
        "only the pool's own views are places to come back to"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_add_panel_offers_a_paste_and_a_one_by_one_way_in(
        web, monkeypatch):
    _gmail_active(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gmail")
    assert body.count('action="/pools/gmail/preview"') == 1, \
        "one way in: a line typed into the box is the same keystrokes"
    assert "add one by hand" not in body
    assert 'name="pasted"' in body and 'class="addbox"' in body
    assert '<option value="egypt">egypt</option>' in body
    assert 'name="new_seller" placeholder="or a new seller"' in body


def test_each_view_shows_one_list_and_pages_it(web, monkeypatch):
    """Four views, one table each - the queued stock, what is signed in,
    what was spent, what the seller owes back."""
    on_phone = [_gmail_row("a@x.com", "ready", serial="1551",
                           phone_status="ready"),
                _gmail_row("b@x.com", "ready", serial="1552",
                           phone_status="building"),
                _gmail_row("c@x.com", "ready", serial="1553",
                           phone_status="incomplete"),
                _gmail_row("d@x.com", "in_use", serial="1554",
                           phone_status="building")]
    seen = _gmail_active(monkeypatch, on_phone=on_phone)
    client = web()
    client.login()

    _, _, body = client.request("GET", "/pools/gmail?view=on_phone&page=2")
    assert seen == {"view": "on_phone", "seller": "", "page": 2}
    assert '<a href="/phones/1551">1551</a>' in body
    assert '<span class="badge ready">Ready</span>' in body
    assert '<span class="badge info">Building</span>' in body
    assert '<span class="badge attn">Incomplete</span>' in body
    assert '<span class="badge in_use">signing in</span>' in body
    assert 'name="pasted"' not in body, "the add box belongs to Queued"

    _, _, body = client.request("GET", "/pools/gmail?view=queued")
    assert "q1@x.com" in body and "the keeper claims from the top" in body


def test_an_empty_pool_says_what_to_do_about_it(web, monkeypatch):
    _gmail_active(monkeypatch, queued=[])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gmail")
    assert "paste a seller&#x27;s sheet above" in body


def test_the_errored_view_filters_by_seller_and_offers_the_refund_list(
        web, monkeypatch):
    seen = _gmail_active(monkeypatch)
    asked = {}

    def errored_addresses(settings, seller=""):
        asked["seller"] = seller
        return ["bad1@x.com", "bad2@x.com"]

    monkeypatch.setattr(app_mod.read, "errored_addresses", errored_addresses)
    client = web()
    client.login()
    status, _, body = client.request(
        "GET", "/pools/gmail?view=errored&seller=egypt&page=2")
    assert status == 200
    assert seen == {"view": "errored", "seller": "egypt", "page": 2}
    assert "captcha <b" in body and "wrong 2fa <b" in body, \
        "the tally, in words"
    assert 'color:var(--amber);font-size:12.5px">captcha</span>' in body
    assert 'color:var(--red);font-size:12.5px">wrong 2fa</span>' in body, \
        "a wrong secret is the seller's fault and is coloured red"
    assert "showed a CAPTCHA" in body, "what happened, from the verdict"
    assert "page 2 of 3" in body
    assert 'href="/pools/gmail?view=errored&seller=egypt&page=1">← newer' \
        in body
    assert 'href="/pools/gmail?view=errored&seller=egypt&page=3">older' \
        in body
    assert 'href="/pools/gmail/refund.txt?seller=egypt">Copy 2 to claim back' \
        in body
    # The two piles read differently now: one comes back on its own,
    # the other is money (the operator, 2026-09-12).
    assert "back in the queue" in body and "try 2 of 3" in body
    assert "To claim back" in body
    assert "action=" not in body.split("To claim back")[1][:80], (
        "the two buttons need the permission; this user has none")

    status, headers, text = client.request(
        "GET", "/pools/gmail/refund.txt?seller=egypt")
    assert status == 200 and asked == {"seller": "egypt"}
    assert dict(headers)["Content-Type"].startswith("text/plain")
    assert text == "bad1@x.com\nbad2@x.com\n"


def test_the_used_view_pages_and_links_the_phone(web, monkeypatch):
    seen = _gmail_active(monkeypatch)
    client = web()
    client.login()
    status, _, body = client.request("GET", "/pools/gmail?view=used")
    assert status == 200 and seen["page"] == 1
    assert '<a href="/phones/1490">1490</a>' in body
    assert "Aug 30 " in body, "used-at through the owner's clock"
    assert "retired with the phone" in body
    assert "page 1 of 2" in body and "older →" in body
    assert 'href="/pools/gmail?view=used&seller=&page=2"' in body
    _, _, body = client.request("GET", "/pools/gmail?view=used&page=2")
    assert seen["page"] == 2 and "← newer" in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_proxy_page_offers_to_adopt_what_geelark_holds(web, monkeypatch):
    _proxy_pool(monkeypatch, rows=[_proxy_row("SX1")],
                state={"unlisted_proxies": [
                    {"host": "1.2.3.4", "port": "9999", "username": "u",
                     "password": "p"}]})
    client = web()
    client.login()
    status, _, body = client.request("GET", "/pools/proxy?view=needs_hand")
    assert status == 200
    assert "1.2.3.4:9999 (u)" in body and "Add to pool" in body
    assert "not in the pool" in body, "a stray is a job like any other"

    _, _, body = client.request("GET", "/pools/proxy")
    assert "SX1" in body and 'name="name" value="SX1"' in body  # Remove


def test_the_gpt_delivered_view_searches_and_pages(web, monkeypatch):
    seen = {}

    def gpt_pool(settings, view="waiting", q="", page=1, per_page=50):
        seen.update(view=view, q=q, page=page)
        return {"view": "delivered",
                "counts": {"waiting": 0, "on_phone": 0, "needs_human": 0,
                           "delivered": 108},
                "rows": [{"id": 1, "address": "d@x.com", "status": "delivered",
                          "serial": "1542", "source": "manual",
                          "added_by": None, "added_by_name": None,
                          "note": "went out", "updated_at": "2026-09-01",
                          "created_at": "", "email_code_only": False,
                          "has_totp": True}],
                "q": q, "page": page, "more": True}

    monkeypatch.setattr(app_mod.read, "gpt_pool", gpt_pool)
    client = web()
    client.login()
    status, _, body = client.request(
        "GET", "/pools/gpt?view=delivered&q=abc&page=2")
    assert status == 200
    assert seen == {"view": "delivered", "q": "abc", "page": 2}
    assert "d@x.com" in body and "1542" in body
    assert "older →" in body and "← newer" in body


# ------------------------------------------------------ C5b: the buttons
# A paste is previewed with a verdict per row and only the good rows are
# carried; a button becomes one queued row with the person's name in it;
# a refusal is recorded, not just answered; the adopt form never carries
# the password.

def _form(**fields) -> str:
    from urllib.parse import urlencode

    return urlencode(fields)


@pytest.mark.parametrize("web", [True], indirect=True)
def test_the_gmail_preview_judges_each_pasted_row(web, monkeypatch):
    # `known` answers {identity: what state that row is in}, so the badge
    # can say where the duplicate already is (2026-09-07).
    monkeypatch.setattr(app_mod.read, "known",
                        lambda s, kind: {"g0@example.com": "free"})
    monkeypatch.setattr(app_mod.read, "gmail_sellers",
                        lambda s: ["egypt", "usa"])
    client = web()
    client.login()
    pasted = ("g0@example.com\tpw\tJBSWY3DPEHPK3PXP\n"
              "new@example.com\tpw2\n"
              "not-an-address\tpw3")
    status, _, body = client.request(
        "POST", "/pools/gmail/preview",
        _form(csrf=client.csrf(), seller="usa", pasted=pasted))
    assert status == 200
    # And where it is: "already in the pool" was said about rows the
    # manager deliberately does not list, so the operator went looking for
    # a row that is not there (2026-09-07).
    assert '<span class="badge bad">already in the pool - free</span>' in body
    assert '<span class="badge ok">ok</span>' in body
    assert "no address" in body or "not-an-address" in body
    carried = re.search(r'<textarea name="rows" hidden>([^<]*)</textarea>',
                        body).group(1)
    assert carried == "new@example.com\tpw2\t", \
        "only the good row travels to the confirm"
    assert "Add 1 (skip 2)" in body
    # the paste is kept under the verdicts, seller and all, for a second go
    kept = re.search(r'<textarea name="pasted">([^<]*)</textarea>',
                     body).group(1)
    assert kept == pasted
    assert '<option value="usa" selected>usa</option>' in body
    assert "Edit and preview again" in body


@pytest.mark.parametrize("web", [True], indirect=True)
def test_one_by_one_is_the_paste_form_with_three_boxes(web, monkeypatch):
    monkeypatch.setattr(app_mod.read, "known", lambda s, kind: {})
    monkeypatch.setattr(app_mod.read, "gmail_sellers", lambda s: ["egypt"])
    client = web()
    client.login()
    status, _, body = client.request(
        "POST", "/pools/gmail/preview",
        _form(csrf=client.csrf(), address="solo@example.com",
              password="Kx82!mnQ", second="rec@example.com",
              seller="egypt", new_seller="turkey"))
    assert status == 200
    carried = re.search(r'<textarea name="rows" hidden>([^<]*)</textarea>',
                        body).group(1)
    assert carried == "solo@example.com\tKx82!mnQ\trec@example.com"
    assert "Add 1 (skip 0)" in body
    assert 'name="seller" value="turkey"' in body, \
        "a typed seller beats the one left in the select"
    assert 'name="new_seller" placeholder="or a new seller" size="16" ' \
           'value="turkey"' in body
    kept = re.search(r'<textarea name="pasted">([^<]*)</textarea>',
                     body).group(1)
    assert kept == "solo@example.com\tKx82!mnQ\trec@example.com", \
        "the three boxes became one line the person can still edit"


@pytest.mark.parametrize("web", [True], indirect=True)
def test_confirming_the_add_queues_the_rows_under_the_persons_name(
        web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    got = {}

    def enqueue(settings, *, verb, payload, requested_by, idem_key):
        got.update(verb=verb, payload=payload, requested_by=requested_by,
                   idem_key=idem_key)
        return 31

    monkeypatch.setattr(actions_mod, "enqueue", enqueue)
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/pools/gmail/add",
        _form(csrf=client.csrf(), seller="usa", idem="once-abc",
              rows="new@example.com\tpw2\tJBSWY3DPEHPK3PXP"))
    assert status == 303
    assert dict(headers)["Location"].startswith("/pools/gmail?said=queued")
    assert got["verb"] == "add_gmails"
    assert got["idem_key"].startswith("once-abc:"), "the press, then its words"
    assert got["payload"]["seller"] == "usa"
    assert got["payload"]["by"] == "mehdi" and got["requested_by"] == 7
    assert got["payload"]["rows"] == [{
        "address": "new@example.com", "password": "pw2",
        "secret": "JBSWY3DPEHPK3PXP", "recovery": ""}]


@pytest.mark.parametrize("web", [True], indirect=True)
def test_adopting_an_exit_takes_its_password_from_the_pass_not_the_form(
        web, monkeypatch):
    import geelark_farm.store.actions as actions_mod
    import geelark_farm.store.state as state_mod

    monkeypatch.setattr(state_mod, "get", lambda s, key, default=None: [
        {"host": "1.2.3.4", "port": 9999, "username": "u",
         "password": "kept-by-the-pass"}])
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 33)
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/pools/proxy/adopt",
        _form(csrf=client.csrf(), host="1.2.3.4", port="9999", username="u",
              password="from-the-form"))
    assert status == 303
    assert got["verb"] == "adopt_proxy"
    assert got["payload"]["password"] == "kept-by-the-pass"

    status, headers, _ = client.request(
        "POST", "/pools/proxy/adopt",
        _form(csrf=client.csrf(), host="9.9.9.9", port="1", username=""))
    assert dict(headers)["Location"] == "/pools/proxy?said=gone"


def test_every_pool_button_is_shut_while_the_mutations_flag_is_off(web):
    client = web()
    client.login()
    for path in ("/pools/gpt/offer", "/pools/proxy/free",
                 "/pools/gmail/add", "/pools/proxy/test-all"):
        status, _, body = client.request(
            "POST", path, _form(csrf=client.csrf(), name="SX1", address="a"))
        assert status == 403 and "not switched on" in body, path


# ---------------------------------------------------- C6: the dashboard
MANUAL_ON = {"web_mutations": True, "manual_login": True}


def test_the_dashboard_shows_the_stock_the_phones_and_who_is_waiting(web):
    client = web()
    client.login()
    status, _, body = client.request("GET", "/")
    assert status == 200
    assert "Stocked \u2014 5 of 5 phones warm" in body, \
        "what the keeper is doing, not a mood"
    # The pools stand on their side in the rail now, one row each.
    assert ">12</b><span class=\"t\">Gmail" in body, "one row per pool"
    assert ">20</b><span class=\"t\">Proxies" in body
    assert "Instance manager" in body, "the page says what it is"
    assert "last pass" not in body, "the pass's clock is the alert strip's job"
    assert "IronHawk@gmail.com" in body and "SX27" in body
    assert 'class="badge warn">App only' in body
    # The accounts with no phone are the GPT card's list now; the panel
    # that carried them went with "Needs a decision" (2026-09-05).
    assert "waiting@x.com" in body
    assert "Awaiting login" not in body and "Needs a decision" not in body
    assert "Change IP" not in body, "mutations are off"
    assert 'name="addresses"' not in body, "manual login is off"


@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_with_manual_login_on_the_dashboard_offers_the_buttons(web,
                                                                monkeypatch):
    # The sheet is fetched now, so the fixture has to answer that read.
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert body.count("Change IP") == 2, "one per phone, both states"
    # One send per waiting account, on its own row - once in the GPT
    # card's queue and once on the same row inside the manager - and each
    # opens the chooser, which lists the one phone that can take an
    # account (1501: app only, nobody's). The tick-and-send list stood in
    # a panel of its own and went with it.
    # One on the card in the rail, one on the row in the sheet - and
    # the sheet is fetched now (2026-09-21).
    assert body.count("data-choose=") == 1
    assert _sheets(client, ("gpt",)).count("data-choose=") == 1
    assert body.count('name="serial" value="1501"') == 1
    assert "&rarr; phone" in body
    assert "Log in selected" not in body


@pytest.mark.parametrize("web", [True], indirect=True)
def test_log_in_selected_is_a_no_op_while_manual_login_is_off(web,
                                                              monkeypatch):
    import geelark_farm.store.actions as actions_mod

    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda *a, **k: pytest.fail("queued anyway"))
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/accounts/login",
        f"csrf={client.csrf()}&addresses=a%40x.com")
    assert status == 303 and dict(headers)["Location"] == "/?said=auto"


@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_log_in_selected_queues_every_ticked_account_in_one_command(
        web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 41)
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/accounts/login",
        f"csrf={client.csrf()}&addresses=a%40x.com&addresses=b%40x.com")
    assert status == 303 and dict(headers)["Location"].startswith("/?said=queued")
    assert got["verb"] == "login_accounts"
    assert got["payload"]["addresses"] == ["a@x.com", "b@x.com"]
    assert got["payload"]["by"] == "mehdi"

    status, headers, _ = client.request(
        "POST", "/accounts/login", f"csrf={client.csrf()}")
    assert dict(headers)["Location"] == "/?said=none"


@pytest.mark.parametrize("web", [True], indirect=True)
def test_change_proxy_is_one_queued_command_for_that_serial(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 42)
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/phones/1500/proxy", f"csrf={client.csrf()}")
    assert status == 303 and dict(headers)["Location"].startswith("/?said=queued")
    assert got["verb"] == "change_proxy"
    assert got["payload"]["serial"] == "1500"
    assert got["payload"]["by"] == "mehdi"


# ------------------------------------------------------ C7: Requests
_REQUESTS = [
    {"id": 241, "verb": "login_accounts", "status": "running",
     "payload": {"addresses": ["arman.tehrani88@gmail.com",
                               "nvd.sharifi@outlook.com"], "by": "mehdi"},
     "result": "logging in 2 account(s) in parallel",
     "detail": {"phones": [
         {"serial": "1549", "account": "arman.tehrani88@gmail.com",
          "status": "booting", "ok": None},
         {"serial": "1550", "account": "nvd.sharifi@outlook.com",
          "status": "booting", "ok": None}]},
     "requested_at": "2026-09-01 18:06:12+00:00",
     "executed_at": "2026-09-01 18:06:30+00:00", "finished_at": None,
     "requested_by": "mehdi"},
    {"id": 240, "verb": "change_proxy", "status": "queued",
     "payload": {"serial": "1549", "by": "alireza"}, "result": "",
     "detail": None, "requested_at": "2026-09-01 18:02:51+00:00",
     "executed_at": None, "finished_at": None, "requested_by": "alireza"},
    {"id": 239, "verb": "add_gmails", "status": "done",
     "payload": {"rows": [{}] * 26, "seller": "egypt"},
     "result": "24 gmails added, 2 already in the pool", "detail": None,
     "requested_at": "2026-09-01 17:58:03+00:00",
     "executed_at": "2026-09-01 17:58:20+00:00",
     "finished_at": "2026-09-01 17:58:20.3+00:00", "requested_by": "mehdi"},
    {"id": 238, "verb": "change_proxy", "status": "failed",
     "payload": {"serial": "1551"}, "result": "no free exit answered",
     "detail": None, "requested_at": "2026-09-01 17:41:20+00:00",
     "executed_at": "2026-09-01 17:41:30+00:00",
     "finished_at": "2026-09-01 17:42:34+00:00", "requested_by": "alireza"},
]


def test_describe_says_each_command_in_words():
    from geelark_farm.web.pages import describe

    assert describe("login_accounts", _REQUESTS[0]["payload"]) == (
        "Log in 2 accounts", "arman.tehrani88, nvd.sharifi")
    assert describe("login_accounts", {"addresses": ["a@x.com"]}) == (
        "Log in 1 account", "a")
    assert describe("add_gmails", _REQUESTS[2]["payload"]) == (
        "Add 26 gmails", "seller egypt")
    assert describe("add_proxies", {"rows": [{}]}) == ("Add 1 proxy", "")
    assert describe("add_proxies", {"rows": [{}, {}]}) == ("Add 2 proxies", "")
    assert describe("change_proxy", {"serial": "1549"}) == (
        "Change IP on 1549", "")
    assert describe("remove_proxy", {"name": "SX3"}) == (
        "Remove SX3", "from the pool")
    assert describe("noop", {}) == ("Noop", "")


@pytest.mark.parametrize("web", [True], indirect=True)
def test_the_requests_page_reads_as_a_story_with_a_line_per_phone(
        web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    monkeypatch.setattr(actions_mod, "listing",
                        lambda s, **k: list(_REQUESTS))
    monkeypatch.setattr(actions_mod, "counts",
                        lambda s, **k: {"running": 1, "queued": 1,
                                        "done": 1, "failed": 1})
    client = web()
    client.login()
    status, _, body = client.request("GET", "/requests")
    assert status == 200
    assert "Log in 2 accounts" in body and "arman.tehrani88, nvd.sharifi" in body
    assert ('↳ <a href="/phones/1549">1549</a> — '
            "arman.tehrani88@gmail.com") in body, "the serial is a link"
    assert body.count("Stop this one") == 2, "one per phone still working"
    assert body.count("/phones/1549/stop") == 1
    assert ("waits for #241 to release the phone - same phone, one at a "
            "time") in body, "the queued change on 1549 says who it waits for"
    assert "Add 26 gmails" in body and "seller egypt" in body
    assert "/requests/238/retry" in body and "Retry" in body
    assert "/requests/240/cancel" in body
    assert "/requests/239/retry" not in body, "done is done"
    assert "running · 1" in body and "failed · 1" in body and "all · 4" in body
    assert "mine only" in body, "an admin can narrow to their own"
    clock = app_mod.pages._clock("2026-09-01 18:06:12+00:00")
    assert clock in body, "asked, as a clock"
    assert "— 1m 04s" in body, "how long the failed change took"
    assert 'http-equiv="refresh"' in body


def test_the_view_pill_and_mine_only_reach_the_store(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    asked = {}
    monkeypatch.setattr(actions_mod, "listing",
                        lambda s, **k: asked.update(k) or [])
    monkeypatch.setattr(actions_mod, "counts", lambda s, **k: {})
    client = web()
    client.login()
    client.request("GET", "/requests?view=failed&mine=1")
    assert asked["view"] == "failed" and asked["everyone"] is False
    client.request("GET", "/requests?view=bogus")
    assert asked["view"] == "" and asked["everyone"] is True


@pytest.mark.parametrize("web", [True], indirect=True)
def test_retry_queues_a_failed_request_again(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    got = {}
    monkeypatch.setattr(actions_mod, "retry",
                        lambda s, **k: got.update(k) or 77)
    client = web()
    client.login()
    status, headers, _ = client.request("POST", "/requests/238/retry",
                                        f"csrf={client.csrf()}")
    assert status == 303
    assert dict(headers)["Location"].startswith("/requests?said=queued")
    assert got == {"action_id": 238, "user_id": 7, "is_admin": True}

    monkeypatch.setattr(actions_mod, "retry", lambda s, **k: "not_failed")
    _, headers, _ = client.request("POST", "/requests/239/retry",
                                   f"csrf={client.csrf()}")
    assert dict(headers)["Location"] == "/requests?said=not_failed"


@pytest.mark.parametrize("web", [True], indirect=True)
def test_stop_this_one_is_one_queued_command_for_that_phone(web,
                                                            monkeypatch):
    import geelark_farm.store.actions as actions_mod

    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 43)
    client = web()
    client.login()
    status, headers, _ = client.request("POST", "/phones/1549/stop",
                                        f"csrf={client.csrf()}")
    assert status == 303
    # Back to where it was pressed: the dashboard, unless the form named
    # the phone's own page. Requests sends nobody to itself.
    assert dict(headers)["Location"].startswith("/?said=queued")
    assert got["verb"] == "stop_phone"
    assert got["payload"]["serial"] == "1549"
    assert got["payload"]["by"] == "mehdi"


# ------------------------------------------------ C8: events, logs, story
def _c8_reads(monkeypatch):
    monkeypatch.setattr(app_mod.read, "signals", lambda s: {
        "builds": {"ok": 6, "failed": 1}, "gmail_free": 12,
        "gmail_per_day": 5.0, "gmail_days": 2.4,
        "pulse": {"at": 0, "tripped": "", "breaker_count": 2,
                  "breaker_limit": 5}, "last_stock": None})
    monkeypatch.setattr(app_mod.read, "events_feed",
                        lambda s, **k: {"rows": [
                            {"id": 10, "at": "2026-09-02 18:06:12+00",
                             "kind": "request", "run_id": "", "build": "",
                             "serial": "", "status": "queued",
                             "seconds": None,
                             "detail": "#241 login_accounts: asked by mehdi"},
                            {"id": 9, "at": "2026-09-02 18:04:31+00",
                             "kind": "build_finished", "run_id": "r9",
                             "build": "1", "serial": "1551",
                             "status": "ready", "seconds": 264,
                             "detail": "ok=True gmail=x@gmail.com proxy=SX3 "
                                       "app=h@x.com"},
                            {"id": 8, "at": "2026-09-02 17:46:07+00",
                             "kind": "build_finished", "run_id": "r8",
                             "build": "1", "serial": "1533",
                             "status": "payment_problem", "seconds": 279,
                             "detail": "ok=False gmail=y@gmail.com proxy=SX4 "
                                       "app="},
                            {"id": 7, "at": "2026-09-02 17:36:02+00",
                             "kind": "breaker", "run_id": "", "build": "",
                             "serial": "", "status": "cleared",
                             "seconds": None,
                             "detail": "cleared by hand from the sheet"}],
                            "counts": {"all": 4, "builds": 2, "phones": 0,
                                       "accounts": 0, "breaker": 1,
                                       "requests": 1, "stock": 0,
                                       "passes": 0},
                            "page": 1, "pages": 1, "total": 4, "asked": k,
                            "day": k.get("day", "")})
    monkeypatch.setattr(app_mod.read, "logs", lambda s, **k: {
        "rows": [{"id": 77, "at": "2026-09-02 17:41:35.2+00",
                  "level": "WARNING",
                  "logger": "geelark_farm.chatgpt_login", "run": "r8",
                  "build": "1", "serial": "1533",
                  "msg": "com.android.vending is in front"},
                 {"id": 75, "at": "2026-09-02 17:41:30+00", "level": "INFO",
                  "logger": "geelark_farm.builder", "run": "r8",
                  "build": "1", "serial": "1533", "msg": "signing in"}],
        "more": True, "today": 31204, "asked": k,
        "loggers": ["geelark_farm.builder", "geelark_farm.chatgpt_login"]})
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: (
        None if serial != "1523" else {
            "serial": "1523",
            "phone": {"serial": "1523", "status": "app_only", "state": "taken",
                      "owner": "ali", "tries": 3, "note": "",
                      "gmail": "BlazeWolf@gmail.com", "app_account": "",
                      "proxy_name": "SX3", "created_at":
                      "2026-09-01 14:09:40+00", "updated_at":
                      "2026-09-02 09:18:00+00", "done_at": None},
            "timeline": [
                {"at": "2026-09-01 14:09:40+00", "source": "event",
                 "kind": "phone", "status": "created", "run": "r1/1",
                 "text": "created behind SX3 for BlazeWolf@gmail.com",
                 "seconds": None},
                {"at": "2026-09-01 14:23:00+00", "source": "event",
                 "kind": "build_finished", "status": "payment_problem",
                 "run": "r2/1", "seconds": 281,
                 "text": "ok=False gmail=BlazeWolf@gmail.com proxy=SX3 app="},
                {"at": "2026-09-01 14:33:00+00", "source": "event",
                 "kind": "build_finished", "status": "payment_problem",
                 "run": "r3/1", "seconds": 270,
                 "text": "ok=False gmail=BlazeWolf@gmail.com proxy=SX3 app="},
                {"at": "2026-09-01 14:44:00+00", "source": "event",
                 "kind": "build_finished", "status": "payment_problem",
                 "run": "r4/1", "seconds": 266,
                 "text": "ok=False gmail=BlazeWolf@gmail.com proxy=SX3 app="},
                {"at": "2026-09-01 15:00:00+00", "source": "event",
                 "kind": "account", "status": "set_aside", "run": "r4/1",
                 "text": "them@gmail.com: payment_problem", "seconds": None},
                {"at": "2026-09-01 17:36:00+00", "source": "request",
                 "kind": "request", "status": "done", "run": "#229",
                 "id": 229, "verb": "clear_tries",
                 "payload": {"serial": "1523"}, "requested_by": "mehdi",
                 "result": "tries cleared",
                 "text": "mehdi asked: clear_tries -> done: tries cleared",
                 "seconds": None},
                {"at": "2026-09-01 17:42:00+00", "source": "artifact",
                 "kind": "screens", "status": "failed payment_problem",
                 "run": "20260901-174200-finish1523",
                 "folder": "20260901-174200-finish1523",
                 "files": ["verify-lost.xml", "verify-nag.xml"],
                 "text": "2 screen(s) archived", "seconds": None}]}))


def test_the_events_page_has_its_signals_pills_and_phone_links(web,
                                                                monkeypatch):
    _c8_reads(monkeypatch)
    client = web()
    client.login()
    today = app_mod.pages.today()
    status, _, body = client.request("GET", "/events?kind=builds&q=1551")
    assert status == 200
    assert "builds, last hour" in body and "~2 days" in body
    assert (f'href="/events?kind=builds&q=1551&day={today}" class="here"'
            in body)
    assert "breaker · 1" in body
    assert 'href="/phones/1551"' in body
    assert "build ok" in body and "cleared by hand" in body
    assert 'href="/logs"' in body


def test_the_events_page_reads_one_day_in_prose_and_exports_it(web,
                                                              monkeypatch):
    """The feed is one day at a time (today unless asked), the breaker
    tile counts the streak, the 'what' column says it in words, the run
    opens its log lines, and the CSV carries the same filter uncapped."""
    from html import escape

    from geelark_farm.failures import verdict

    _c8_reads(monkeypatch)
    whole = []
    monkeypatch.setattr(app_mod.read, "events_rows",
                        lambda s, **k: whole.append(k) or [
                            {"at": "2026-09-02 18:04:31+00", "kind": "stock",
                             "run_id": "", "build": "", "serial": "",
                             "status": "gmail", "seconds": None,
                             "detail": "24 gmails, added by mehdi"}])
    client = web()
    client.login()
    today = app_mod.pages.today()
    status, _, body = client.request("GET", "/events")
    assert status == 200
    assert f'name="day" value="{today}"' in body, "today unless asked"
    assert f'day={today}" class="here">today</a>' in body
    assert "2 of 5" in body and "in a row" in body, "the breaker's streak"
    assert "ready — signed in as h@x.com" in body
    assert f"payment_problem — {escape(verdict('payment_problem').seen)}" in body
    assert 'href="/requests?hi=241">#241</a>' in body
    assert 'href="/logs?run=r9">r9/1</a>' in body

    _, _, body = client.request("GET", "/events?day=2026-09-01&kind=stock")
    assert 'href="/events?kind=stock&q=&day=2026-09-01&page=' not in body
    assert "<span>2026-09-01</span>" in body, "a chosen day is a lit chip"
    assert 'href="/events.csv?kind=stock&q=&day=2026-09-01"' in body

    _, _, body = client.request("GET", "/events?day=not-a-date")
    assert f'name="day" value="{today}"' in body, "a typo reads as today"
    _, _, body = client.request("GET", "/events?day=all")
    assert 'day=all" class="here">all days</a>' in body

    status, headers, text = client.request(
        "GET", "/events.csv?kind=stock&q=&day=2026-09-01")
    assert status == 200 and "text/csv" in dict(headers)["Content-Type"]
    assert whole == [{"kind": "stock", "q": "", "day": "2026-09-01"}]
    assert text.startswith("at,kind,run,build,serial,status,seconds,detail")
    assert "24 gmails, added by mehdi" in text


def test_the_logs_page_filters_and_shows_the_captured_lines(web,
                                                            monkeypatch):
    _c8_reads(monkeypatch)
    client = web()
    client.login()
    status, _, body = client.request(
        "GET", "/logs?level=warning&phone=1533&q=vending")
    assert status == 200
    assert "com.android.vending is in front" in body
    assert "[r8/1]" in body and "badge warn" in body
    assert "31,204 lines today" in body
    assert 'value="1533"' in body


LOG_DB_ON = {"log_db": True}


class _FakeCapture:
    def __init__(self, **state):
        self.__dict__.update(state)


@pytest.mark.parametrize("web", [LOG_DB_ON], indirect=True)
def test_the_logs_page_says_how_the_capture_is_and_reads_older(web,
                                                                monkeypatch):
    """The header says whether the capture is on and what it wrote; the
    logger is picked from a select of known names; WARNING rows are
    tinted; 'older' reads past the newest page by id."""
    from geelark_farm.store import logdb

    _c8_reads(monkeypatch)
    monkeypatch.setattr(logdb, "CURRENT", _FakeCapture(
        written=31204, dropped=2, disabled=False, off_at=None, off_why=""))
    client = web()
    client.login()
    status, _, body = client.request("GET", "/logs?run=r8&logger=builder")
    assert status == 200
    assert "capture on</span> · 31,204 written · 2 dropped" in body
    assert '<select name="logger">' in body
    assert '<option value="geelark_farm.builder">builder</option>' in body
    assert '<tr class="warn">' in body, "a WARNING line is tinted as a row"
    assert 'href="/logs?level=INFO&logger=builder&run=r8&phone=&q=' \
           '&before=75">older →</a>' in body
    assert "nothing" not in body.split("<table>")[1].split("</table>")[0]

    monkeypatch.setattr(logdb, "CURRENT", _FakeCapture(
        written=9, dropped=0, disabled=True, off_at=1_700_000_000.0,
        off_why="3 failed flushes in a row: cluster unreachable"))
    _, _, body = client.request("GET", "/logs?logger_text=chatgpt")
    assert "capture switched itself OFF at" in body
    assert "cluster unreachable" in body and "a restart brings it back" in body
    assert '<option value="geelark_farm.chatgpt_login">' in body
    assert 'name="logger_text" value="chatgpt"' in body


@pytest.mark.parametrize("web", [{}, LOG_DB_ON], indirect=True)
def test_an_empty_log_table_says_which_nothing_it_is(web, monkeypatch):
    from geelark_farm.store import logdb

    _c8_reads(monkeypatch)
    monkeypatch.setattr(app_mod.read, "logs", lambda s, **k: {
        "rows": [], "more": False, "today": 0, "loggers": []})
    monkeypatch.setattr(logdb, "CURRENT", None)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/logs?level=ERROR&run=r8")
    _, _, plain = client.request("GET", "/logs")
    if "LOG_DB is not set" in body:
        assert "nothing captured yet - LOG_DB is off" in body
        assert "nothing captured yet - LOG_DB is off" in plain
    else:
        assert "nothing at ERROR for run r8" in body
        assert "LOG_DB" not in body.split("<table>")[1]
        assert "nothing at INFO yet" in plain
        assert "capture not started in this process" in body


def test_a_phone_story_joins_events_requests_and_screens(web, monkeypatch):
    """Two lines an entry - what happened, then what it means; the same
    failure three times in a row is one entry listing its times; the
    screens are links; and the story closes with where the phone is."""
    from html import escape

    from geelark_farm.failures import verdict

    _c8_reads(monkeypatch)
    client = web()
    client.login()
    status, _, body = client.request("GET", "/phones/1523")
    assert status == 200
    assert "Phone 1523" in body and "BlazeWolf@gmail.com" in body
    assert "Created behind SX3 for BlazeWolf@gmail.com" in body
    seen = verdict("payment_problem")
    assert body.count(escape(seen.seen)) == 2, "the builds, then set aside"
    assert escape(seen.advice) in body
    assert "· 3 times" in body and "[r2/1]" in body and "[r4/1]" in body
    times = app_mod.pages._hhmm("2026-09-01 14:23:00+00")
    assert times in body and body.count("ok=False") == 0
    assert "them@gmail.com set aside: " + escape(seen.seen) in body
    assert "<b>mehdi</b> asked: Clear tries on 1523" in body
    assert 'href="/requests?hi=229">#229</a>' in body
    assert "2 screens archived" in body
    assert ('href="/phones/1523/screens/20260901-174200-finish1523/'
            'verify-lost.xml">verify-lost.xml</a>') in body
    assert "Now: out with ali" in body
    assert "Tries 3 of 3 — given up until cleared" in body
    assert 'href="/logs?phone=1523"' in body

    # the story is a table with a title, and the row it ends on is lit
    assert "<th>when</th><th>what</th><th>what happened</th>" in body
    assert ">Its story <" in body and "entries</span>" in body
    assert '<tr class="now">' in body
    assert "2026-09-01 14:23:00" not in body, "human clocks, not stamps"

    # what the phone is, in one selectable line, and no panel around it
    assert "Hand over" not in body, "a panel for one line is not a panel"
    assert 'class="hand"' in body and "1523 · " in body
    assert '<a href="/" class="dim">← Dashboard</a>' in body
    assert "← Events" not in body, "the dashboard is where you came from"

    status, _, _ = client.request("GET", "/phones/9999")
    assert status == 404


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_story_offers_the_phone_buttons_and_returns_there(web,
                                                              monkeypatch):
    import geelark_farm.store.actions as actions_mod

    _c8_reads(monkeypatch)
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 63)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "sara", "role": "operator",
                         "sees": "all", "may_take_phones": True,
                         "may_change_proxy": True,
                         "may_login_accounts": True})
    client = web()
    client.login(username="sara")
    # 1523 is taken by "ali" and the reader is another operator. The
    # table has always answered that with the holder's name and no
    # buttons; this page answered it with Done and Failed - and Failed
    # deletes the phone at the next sync and frees the account on it.
    # One contract, both surfaces (2026-09-07). An admin is the one
    # exception, since 2026-09-15 - tested below.
    _, _, body = client.request("GET", "/phones/1523")
    assert "with ali" in body
    for door in ("boot", "state", "proxy"):
        assert f'action="/phones/1523/{door}"' not in body, (
            f"{door} on a phone somebody else is holding")

    # And the POST is refused too, not merely undrawn. The page drew the
    # rule and nothing enforced it, so the press went through on a form
    # the page had not offered - and Failed deletes the phone at the next
    # sync and frees the account on it (2026-09-07).
    monkeypatch.setattr("geelark_farm.store.actions.record_refused",
                        lambda *a, **k: 88)
    status, headers, _ = client.request(
        "POST", "/phones/1523/state",
        _form(csrf=client.csrf(), state="failed", sure="1",
              back="/phones/1523"))
    assert status == 303
    # Back to the page the press was made on, with the request's id, so
    # the banner reads "phone 1523 is with ali" off the row - not the
    # bare word, whose sentence is about a missing permission
    # (2026-09-21, found by audit).
    assert dict(headers)["Location"] == "/phones/1523?said=no:88"
    assert got == {}, "nothing was queued against somebody else's phone"

    # The admin sees the three ways back on ali's phone, and the press
    # lands - with ali's name on the request.
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 7, "username": "mehdi", "role": "admin",
                         "sees": "all"})
    admin = web()
    admin.login()
    _, _, body = admin.request("GET", "/phones/1523")
    assert 'action="/phones/1523/state"' in body
    assert 'action="/phones/1523/boot"' not in body, "taken: not Boot"
    status, headers, _ = admin.request(
        "POST", "/phones/1523/state",
        _form(csrf=admin.csrf(), state="failed", sure="1",
              back="/phones/1523"))
    assert status == 303 and "refused" not in dict(headers)["Location"]
    assert {k: got["payload"][k] for k in ("serial", "state", "held_by")} == {
        "serial": "1523", "state": "failed", "held_by": "ali"}


    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "all", "may_take_phones": False})
    narrow = web()
    narrow.login(username="narrow")
    _, _, body = narrow.request("GET", "/phones/1523")
    assert "/phones/1523/state" not in body
    assert "needs the may_take_phones permission" in body


def test_archived_screens_are_served_only_from_their_own_folder(
        web, monkeypatch, tmp_path, make_settings):
    """A guarded static route: one .xml, inside artifact_dir, in a folder
    named for this serial. Anything else - another phone's folder, a
    walk upwards, a file that is not a screen - is a 404."""
    import geelark_farm.web.read as read_mod

    _c8_reads(monkeypatch)
    mine = tmp_path / "20260901-174200-finish1523"
    mine.mkdir()
    (mine / "verify-lost.xml").write_text("<hierarchy/>", encoding="utf-8")
    (mine / "outcome.txt").write_text("failed x\n", encoding="utf-8")
    other = tmp_path / "20260901-174300-finish1600"
    other.mkdir()
    (other / "page.xml").write_text("<theirs/>", encoding="utf-8")
    (tmp_path / "secret.xml").write_text("<root/>", encoding="utf-8")
    real = read_mod.screen_file
    art = make_settings(artifact_dir=tmp_path)
    monkeypatch.setattr(app_mod.read, "screen_file",
                        lambda s, serial, folder, name, **k:
                        real(art, serial, folder, name, **k))
    client = web()
    client.login()
    status, headers, text = client.request(
        "GET", "/phones/1523/screens/20260901-174200-finish1523/"
               "verify-lost.xml")
    assert status == 200 and text == "<hierarchy/>"
    assert dict(headers)["Content-Type"].startswith("text/plain")
    for path in ("/phones/1523/screens/20260901-174300-finish1600/page.xml",
                 "/phones/1600/screens/20260901-174200-finish1523/"
                 "verify-lost.xml",
                 "/phones/1523/screens/20260901-174200-finish1523/"
                 "outcome.txt",
                 "/phones/1523/screens/20260901-174200-finish1523/"
                 "..%2Fsecret.xml",
                 "/phones/1523/screens/..%2F20260901-174200-finish1523/"
                 "verify-lost.xml",
                 "/phones/1523/screens/20260901-174200-finish1523/"
                 "%2E%2E%5Csecret.xml"):
        status, _, _ = client.request("GET", path)
        assert status == 404, path
    assert real(art, "1523", "..", "secret.xml") is None
    assert real(art, "1523", "20260901-174200-finish1523",
                "../secret.xml") is None


def test_the_three_are_admin_only(web, monkeypatch):
    _c8_reads(monkeypatch)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own"})
    client = web()
    client.login(username="narrow")
    for path in ("/events", "/events.csv", "/logs", "/phones/1523",
                 "/phones/1523/screens/x/y.xml"):
        status, _, _ = client.request("GET", path)
        # A page that is not theirs sends them home; a phone that is not
        # theirs is refused by the page itself, in its own words.
        want = 403 if path.startswith("/phones/") else 303
        assert status == want, path


# ------------------------------------------------------- users, as drawn
@pytest.mark.parametrize("web", [ADMIN_ON], indirect=True)
def test_the_users_listing_reads_as_the_mockup_and_reset_asks_first(
        web, monkeypatch):
    import geelark_farm.store.users as users_mod

    rows = [
        {"id": 7, "username": "mehdi", "role": "admin", "sees": "all",
         "active": True, "must_change_password": False,
         "last_login_at": "2026-09-03 10:00:00+00", "created_at": None,
         **{c: False for c in users_mod.PERMISSION_COLUMNS}},
        {"id": 9, "username": "alireza", "role": "operator", "sees": "own",
         "active": True, "must_change_password": False,
         "last_login_at": None, "created_at": None,
         **{c: False for c in users_mod.PERMISSION_COLUMNS},
         "may_add_gmail": True, "may_take_phones": True},
        {"id": 12, "username": "sara", "role": "operator", "sees": "own",
         "active": False, "must_change_password": False,
         "last_login_at": None, "created_at": None,
         **{c: False for c in users_mod.PERMISSION_COLUMNS},
         "may_add_gpt": True},
    ]
    _people(monkeypatch, rows)
    reset = []
    monkeypatch.setattr(users_mod, "reset_password",
                        lambda s, uid: reset.append(uid) or "once-pw")
    client = web()
    client.login()
    status, _, body = client.request("GET", "/users?id=9")
    assert status == 200 and "2 can sign in" in body
    assert '<span class="avatar">m</span>' in body
    assert body.count('<span class="avatar">a</span>') == 2, \
        "in the row and in the editor's header"
    assert "everything, including the service controls" in body
    assert ('<span class="badge">add gmail</span> '
            '<span class="badge">take phones</span>') in body
    assert '<span class="badge manual">admin</span>' in body
    assert '<span class="badge info">operator</span>' in body
    assert app_mod.pages._when("2026-09-03 10:00:00+00") in body
    assert "never" in body
    assert '<tr class="off">' in body
    assert "kept for the record - their requests still carry the name" in body
    assert "add gpt" not in body, "a deactivated person's ticks are not said"
    assert "users are deactivated, never deleted" in body

    status, _, body = client.request("POST", "/users/9/reset",
                                     _form(csrf=client.csrf()))
    assert status == 200 and "Reset alireza&#x27;s password?" in body
    assert "Keep it" in body and reset == [], "asked first, nothing minted"
    status, _, body = client.request("POST", "/users/9/reset",
                                     _form(csrf=client.csrf(), sure="1"))
    assert status == 200 and "once-pw" in body and reset == [9]


def test_the_users_listing_puts_admins_first_and_the_deactivated_last():
    from geelark_farm.store import users as users_mod

    assert users_mod.LISTING_ORDER == (
        " ORDER BY active DESC, (role = 'admin') DESC, username")


# ---------------------------------------------- the one destructive button
@pytest.mark.parametrize("web", [True], indirect=True)
def test_remove_asks_once_with_the_name_before_it_queues(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 51)
    client = web()
    client.login()
    status, _, body = client.request(
        "POST", "/pools/proxy/remove", _form(csrf=client.csrf(), name="SX3"))
    assert status == 200 and "Remove SX3 from the pool?" in body
    assert 'name="sure" value="1"' in body and "Keep it" in body
    assert got == {}, "nothing queued until the person says so"

    status, headers, _ = client.request(
        "POST", "/pools/proxy/remove",
        _form(csrf=client.csrf(), name="SX3", sure="1"))
    assert status == 303 and got["verb"] == "remove_proxy"
    assert got["payload"]["name"] == "SX3"


# ------------------------------------------------------- the Proxy Pool
def _proxy_row(name, status="free", **more):
    row = {"id": 1, "name": name, "host": "10.0.0.1", "port": 9999,
           "username": "u", "status": status, "serial": "",
           "last_exit_ip": "10.0.0.1", "times_used": 2, "note": "",
           "updated_at": "2026-09-02 10:00:00", "error": None}
    row.update(more)
    return row


def _proxy_pool(monkeypatch, rows=(), state=None, seen=None):
    """read.proxy_pool as the page now asks for it: one view at a time,
    the rows bucketed by the reader's own rule, and what the pass keeps in
    service_state (unlisted_proxies / ignored_proxies / proxy_tests)
    answered by key the way the real `state.get` does."""
    import geelark_farm.store.state as state_mod
    from geelark_farm.web.read import proxy_bucket

    kept = dict(state or {})
    monkeypatch.setattr(state_mod, "get",
                        lambda s, key, default=None: kept.get(key, default))
    rows = [dict(r, bucket=proxy_bucket(r["status"])) for r in rows]
    seen = seen if seen is not None else {}

    def proxy_pool(settings, view="free", q="", page=1, per_page=50,
                   unlisted=None):
        seen.update(view=view, q=q, page=page)
        strays = list(unlisted or [])
        by = {}
        for r in rows:
            by.setdefault(r["bucket"], []).append(r)
        trouble = by.get("needs_new_ip", []) + by.get("dead", [])
        counts = {"free": len(by.get("free", [])),
                  "on_phone": len(by.get("on_phone", [])),
                  "needs_new_ip": len(by.get("needs_new_ip", [])),
                  "dead": len(by.get("dead", [])),
                  "strays": len(strays),
                  "needs_hand": len(trouble) + len(strays),
                  "all": len(rows)}
        out = {"view": view, "counts": counts, "q": q, "page": page,
               "rows": [], "strays": [], "more": False, "pages": 1,
               "total": 0}
        if view == "needs_hand":
            out.update(rows=trouble, strays=strays,
                       total=counts["needs_hand"])
        elif view == "all":
            want = [r for r in rows if not q or q.lower() in
                    f"{r['name']} {r['host']} {r['serial']}".lower()]
            out.update(rows=want, total=len(want))
        else:
            out.update(rows=by.get(view, []), total=len(by.get(view, [])))
        return out

    monkeypatch.setattr(app_mod.read, "proxy_pool", proxy_pool)
    return seen


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_proxy_add_panel_offers_paste_and_one_by_one(web, monkeypatch):
    _proxy_pool(monkeypatch)
    monkeypatch.setattr(app_mod.read, "known", lambda s, kind: {})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/proxy")
    assert body.count('action="/pools/proxy/preview"') == 2, \
        "paste from the vendor, and one by one"
    assert "<summary>add one by hand</summary>" in body, "folded, not a panel"
    for box in ("host", "port", "username", "password", "name"):
        assert f'name="{box}"' in body, box
    # the five boxes become one pasted line, judged by the same preview
    status, _, body = client.request(
        "POST", "/pools/proxy/preview",
        _form(csrf=client.csrf(), host="1.2.3.4", port="9999", username="u",
              password="p", name="SX50"))
    assert status == 200
    carried = re.search(r'<textarea name="rows" hidden>([^<]*)</textarea>',
                        body).group(1)
    assert carried == "SX50\t1.2.3.4:9999:u:p"
    assert "Add 1 (skip 0)" in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_ignored_exits_leave_the_held_list_and_can_be_seen(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    _proxy_pool(monkeypatch, state={
        "unlisted_proxies": [
            {"host": "1.2.3.4", "port": 9999, "username": "u", "password": "p"},
            {"host": "5.6.7.8", "port": "1080", "username": "", "password": ""}],
        "ignored_proxies": ["1.2.3.4:9999:u"]})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/proxy?view=needs_hand")
    assert "5.6.7.8:1080" in body and "1.2.3.4:9999 (u)" not in body
    assert body.count('action="/pools/proxy/ignore"') == 1
    assert "1 ignored exit" in body
    assert 'href="/pools/proxy?view=needs_hand&ignored=1"' in body

    _, _, body = client.request(
        "GET", "/pools/proxy?view=needs_hand&ignored=1")
    assert "Ignored" in body and "1.2.3.4:9999:u" in body
    assert 'action="/pools/proxy/ignore"' not in body

    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 61)
    status, headers, _ = client.request(
        "POST", "/pools/proxy/ignore",
        _form(csrf=client.csrf(), host="5.6.7.8", port="1080", username=""))
    assert status == 303
    assert dict(headers)["Location"] == "/pools/proxy?said=queued:61"
    assert got["verb"] == "ignore_proxy"
    assert {k: got["payload"][k] for k in ("host", "port", "username")} == {
        "host": "5.6.7.8", "port": "1080", "username": ""}


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_last_test_column_reads_the_stamps_the_pass_kept(
        web, monkeypatch):
    import time

    now = time.time()
    long_note = "Google refused this exit on 1528 - change it at the " \
                "vendor, then mark it free so a build can take it again"
    _proxy_pool(monkeypatch, rows=[
        _proxy_row("SX1", last_exit_ip="208.207.213.45"),
        _proxy_row("SX2", "dead"),
        _proxy_row("SX3", "change ip", note=long_note)],
        state={"proxy_tests": {
            "SX1": {"at": now - 42 * 60, "ok": True, "exit": "208.207.213.45"},
            "SX2": {"at": now - 2 * 3600, "ok": False, "exit": ""}}})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/proxy")
    assert "42m ago · ok" in body, "coloured, off the pass's own stamp"
    assert "every one tested 42m ago" in body, "the newest free stamp"
    assert "208.207.213.45" in body, "the exit column shows last_exit_ip"
    assert body.count('action="/pools/proxy/test"') == 1
    assert body.count('action="/pools/proxy/remove"') == 1

    _, _, body = client.request("GET", "/pools/proxy?view=needs_hand")
    assert "2h ago · dead" in body, "when the dead one last failed"
    assert f'title="{long_note}"' in body, "the whole note, on hover"
    assert "…" in body and long_note not in body.replace(
        f'title="{long_note}"', ""), "and clipped in the cell"

    _, _, body = client.request("GET", "/pools/proxy?view=all")
    assert ">never<" in body, "SX3 was never tested"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_work_list_is_one_table_of_every_kind_of_trouble(web, monkeypatch):
    """Three panels stacked - needs a new IP, dead, held by GeeLark - are
    one list now: to a person they are the same thing, a job with the one
    button that answers it."""
    import time

    _proxy_pool(monkeypatch, rows=[
        _proxy_row("SX1"),
        _proxy_row("N01", "change ip", note="Google refused it on 1528"),
        _proxy_row("D01", "dead")],
        state={"unlisted_proxies": [
            {"host": "9.9.9.9", "port": "1080", "username": "u9",
             "password": "p"}],
            "proxy_tests": {"D01": {"at": time.time() - 7200, "ok": False}}})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/proxy?view=needs_hand")
    assert "3</span> need a hand — 2 exits and 1 stray" in body
    assert "needs a new IP" in body and ">dead<" in body
    assert "not in the pool" in body and "9.9.9.9:1080 (u9)" in body
    assert "IP changed — free it" in body and "Test again" in body
    assert "Add to pool" in body and ">Ignore<" in body
    assert "Google refused it on 1528" in body, "the row's own note"
    assert "change the IP in the vendor" in body, "and what to do about it"
    assert "SX1" not in body, "a free exit is not a job"


def test_the_phone_column_links_the_serial(web, monkeypatch):
    _proxy_pool(monkeypatch, rows=[
        _proxy_row("SX27", "on a phone", serial="1551"),
        _proxy_row("SX8", "change ip", serial="1528")])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/proxy?view=on_phone")
    assert '<a href="/phones/1551">1551</a>' in body
    assert "1528" not in body, "a refused exit is a job, not a phone"

    _, _, body = client.request("GET", "/pools/proxy?view=needs_hand")
    assert '<a href="/phones/1528">1528</a>' in body, \
        "and the job says which phone asked for it"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_button_pressed_on_the_work_list_comes_back_to_it(web, monkeypatch):
    """The page is four views now, so landing on the free shelf after
    pressing Test on the work list would lose the place - and the banner
    is appended with & when the place already carries a view."""
    import geelark_farm.store.actions as actions_mod

    _proxy_pool(monkeypatch, rows=[_proxy_row("D01", "dead")])
    monkeypatch.setattr(actions_mod, "enqueue", lambda s, **k: 71)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/proxy?view=needs_hand")
    assert 'name="back" value="/pools/proxy?view=needs_hand"' in body
    assert "never tested, dead since" in body, \
        "a dead exit nobody ever tested does not 'fail its last test never'"

    _, headers, _ = client.request(
        "POST", "/pools/proxy/test",
        _form(csrf=client.csrf(), name="D01",
              back="/pools/proxy?view=needs_hand"))
    assert dict(headers)["Location"] == \
        "/pools/proxy?view=needs_hand&said=queued:71"

    _, headers, _ = client.request(
        "POST", "/pools/proxy/test",
        _form(csrf=client.csrf(), name="D01", back="/evil"))
    assert dict(headers)["Location"] == "/pools/proxy?said=queued:71", \
        "only the four views are places to come back to"


@pytest.mark.parametrize("web", [True], indirect=True)
def test_put_it_back_queues_the_removed_row_again(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 71)
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/pools/proxy/restore",
        _form(csrf=client.csrf(), name="SX3", raw="1.2.3.4:9999:u:p"))
    assert status == 303
    assert dict(headers)["Location"] == "/requests?said=queued:71"
    assert got["verb"] == "add_proxies"
    assert got["payload"]["rows"] == [{"raw": "1.2.3.4:9999:u:p",
                                       "name": "SX3"}]
    assert got["idem_key"].startswith("restore:SX3:")

    got.clear()
    status, headers, _ = client.request(
        "POST", "/pools/proxy/restore", _form(csrf=client.csrf(), name="SX3"))
    assert dict(headers)["Location"] == "/requests?said=gone" and got == {}


def test_the_stylesheet_never_breaks_a_quoted_string_across_lines(web):
    """A wrap that split `'IBM Plex Mono'` over two lines made the whole
    stylesheet unparseable and every page rendered as plain text
    (2026-09-03). CSS strings cannot contain a raw newline."""
    style = assets.CSS
    for line in style.splitlines():
        assert line.count("'") % 2 == 0 and line.count('"') % 2 == 0, line
    assert "'IBM Plex Mono'" in style and "'IBM Plex Sans'" in style


def test_a_pill_is_a_direct_child_or_its_count_becomes_one(web):
    """`.pills span` matched the count inside each pill as well as the
    pill itself, so every count wore the background, the padding and the
    divider of the pill around it - four boxes inside four boxes, which
    is what the row looked like (2026-09-04)."""
    style = assets.CSS
    # Comments out first: one of them names the selector this refuses.
    rules = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
    assert ".pills>a" in rules and ".pills>span" in rules
    assert ".pills a," not in rules and ".pills span" not in rules, \
        "a pill is a direct child; anything inside one is not a pill"


def test_the_pool_column_lists_are_qualified_for_their_joins():
    """Gmail Pool joins phones and Gpt Pool joins users; both tables have an
    `id`, a `status`, an `updated_at`. An unqualified list made Postgres
    answer "column reference is ambiguous" and the page 500 (2026-09-03)."""
    from geelark_farm.web import read

    for cols in (read._GMAIL_COLUMNS, read._APP_COLUMNS):
        for piece in cols.split(","):
            assert piece.strip().startswith("r."), piece


def test_the_session_cookie_is_secure_behind_the_https_proxy(web):
    """Caddy terminates TLS on the domain and says so in X-Forwarded-Proto;
    over the plain-http ssh tunnel the flag would hide the cookie."""
    client = web()
    _, headers, _ = client.request(
        "POST", "/login", "username=mehdi&password=correct-horse",
        headers={"X-Forwarded-Proto": "https"})
    cookie = dict(headers)["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie
    plain = web()
    _, headers, _ = plain.login()
    assert "Secure" not in dict(headers)["Set-Cookie"]


def test_head_answers_like_get_without_a_body(web):
    """An uptime monitor sends HEAD; the stdlib handler said 501."""
    import http.client

    client = web()
    conn = http.client.HTTPConnection("127.0.0.1", client.port, timeout=5)
    conn.request("HEAD", "/login")
    resp = conn.getresponse()
    body = resp.read()
    assert resp.status == 200 and body == b""
    assert int(resp.getheader("Content-Length")) > 1000


# ------------------------------------- the dashboard, second pass (C9 audit)
def _sheet_read(base):
    """`read.pool_sheet` off whatever `pool_rows` the fixture holds."""
    def one(settings, kind):
        listed = base.get("pool_rows") or {}
        return {kind: list(listed.get(kind) or []),
                "totals": dict(listed.get("totals") or {}),
                "pending": dict(base.get("pending") or {}),
                "stamp": str(base.get("stamp") or "s1")}
    return one


def _sheets(client, kinds=("gmail", "gpt", "spotify", "proxy")):
    """What used to be `body[body.index('id="poolov"'):]`.

    The three pool sheets left the dashboard on 2026-09-21 - they were
    925,488 of its 1,012,694 bytes, drawn shut - and are fetched one at
    a time from `/pools/<kind>/sheet` when a door is pressed.
    """
    out = []
    for kind in kinds:
        status, _, body = client.request("GET", f"/pools/{kind}/sheet")
        assert status == 200, (kind, status)
        out.append(body)
    return "".join(out)


def _dash(monkeypatch, **more):
    """The dashboard's read, with the fixture's rows and whatever a test
    wants changed on top."""
    base = app_mod.read.dashboard(None)
    base.update(more)
    monkeypatch.setattr(app_mod.read, "dashboard", lambda s, owner_id=None:
                        dict(base))
    # The manager's sheets are fetched when the drawer is pulled rather
    # than drawn shut into every dashboard, so the fixture has to answer
    # that read as well (2026-09-21).
    monkeypatch.setattr(app_mod.read, "pool_sheet", _sheet_read(base))
    monkeypatch.setattr(app_mod.read, "pool_row", _row_read(base))
    return base


def _row_read(base):
    """`read.pool_row` off the fixture's rows: the one address asked for."""
    def one(settings, kind, address):
        want = str(address).strip().casefold()
        rows = (base.get("pool_rows") or {}).get(kind) or []
        row = next((r for r in rows
                    if str(r.get("address") or "").strip().casefold() == want),
                   None)
        return {"row": row, "pending": dict(base.get("pending") or {})}
    return one


def test_the_tiles_warn_with_thresholds_and_say_the_consequence(web,
                                                                monkeypatch):
    _dash(monkeypatch,
          stock={"gmail": {"free": 0, "on_phones": 5, "used": 7},
                 "proxy": {"free": 2, "on_phones": 14, "dead": 1},
                 "app": {"awaiting": 7, "panel": 4, "manual": 3}},
          phones=[{"serial": "1500", "status": "ready", "state": ""},
                  {"serial": "1501", "status": "ready", "state": "taken",
                   "owner": "ali"},
                  {"serial": "1502", "status": "app_only", "state": ""},
                  {"serial": "1503", "status": "building", "state": ""}],
          pulse={"warm": 5, "target": 5, "tripped": "", "at": 0})
    client = web()
    client.login()
    status, _, body = client.request("GET", "/")
    assert status == 200
    # One card per pool in the rail now, each wearing the colour of how
    # short it is and saying the consequence where a hand can reach it.
    card = body[body.index('<section class="pool"'):]
    assert 'color:var(--red)">0</b><span class="t">Gmail' in card
    assert "nothing can be built until rows are added" in card
    assert 'color:var(--amber)">2</b><span class="t">Proxies' in card
    assert "fewer than the 5 phones the keeper keeps warm" in card
    assert 'color:var(--amber)">7</b><span class="t">GPT accounts' in card
    # Proxies are the admin's pool, so the row says so instead of
    # offering a way in that leads nowhere for an operator.
    # An admin gets the proxy pool's door; the `admin` lock is what an
    # operator sees in its place (one door per card, 2026-09-05).
    assert 'data-pool="proxy">Manage</button>' in card
    assert 'class="lock">admin<' not in card
    # And with mutations off there is no add door at all - the same rule
    # every other button on this console follows.
    assert 'class="addfold"' not in card
    assert "2 of them have no phone to go to" in card
    # The row of shelf counts above the table is gone: the table below is
    # already grouped by state, and a page should not say a number twice.
    assert "<i>building</i>" not in body
    assert "<i>taken</i>" not in body


def test_the_keeper_sentence_says_what_it_is_doing_in_every_state():
    """Five states, read top to bottom - the order is the point: a
    stopped service must never read as "building" because its numbers
    happen to be short."""
    say = app_mod.pages._keeper_words
    # It named the sheet, which has been shut since 2026-09-07 and which
    # an operator never had. Not "press Start" either: they are not
    # granted that one.
    assert say({"stopped": True, "warm": 0, "target": 5}) == (
        "Stopped — nothing is running until somebody starts it again", "red")
    assert say({"tripped": "captcha x5", "warm": 2, "target": 5}) == (
        "Stopped by the breaker — nothing is being built", "red")
    assert say({"paused": True, "warm": 2, "target": 5}) == (
        "Paused — nothing new is being built", "amber")
    assert say({"warm": 2, "target": 5}) == (
        "Building — 2 of 5 phones warm", "amber")
    assert say({"warm": 6, "target": 5}) == (
        "Stocked — 6 of 5 phones warm", "green")
    assert say({"stopped": True, "paused": True, "tripped": "x",
                "warm": 0, "target": 5})[1] == "red", "stopped wins"
    quiet = app_mod.pages._status_sentence({"pulse": {}})
    assert quiet == '<span class="dim">no pass has reported yet</span>', \
        "and a farm nothing has reported on claims nothing"


def test_a_farm_with_nothing_ready_says_so_quietly(web, monkeypatch):
    _dash(monkeypatch, phones=[{"serial": "1502", "status": "app_only",
                                "state": ""}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    # The counts strip is gone, so a farm with nothing ready says it where
    # it is true: in the sentence, and in the table.
    assert "<i>ready</i>" not in body, "the counts strip is gone"
    assert 'class="badge warn">App only' in body, "the one phone it does have"
    assert 'class="badge ok">ready' not in body, "and nothing that is ready"


def test_a_building_row_shows_its_last_log_line_and_how_long(web,
                                                             monkeypatch):
    import datetime as dt

    started = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=96)
    _dash(monkeypatch,
          phones=[{"serial": "1556", "status": "building", "state": ""},
                  {"serial": "1557", "status": "building", "state": ""}],
          progress={"1556": {"serial": "1556", "run": "r9",
                             "logger": "geelark_farm.flows.google_login",
                             "msg": "typed the password, waiting for the "
                                    "2-step screen", "at": started,
                             "started": started}})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert "google sign-in: typed the password, waiting for the 2-step screen" \
        in body
    assert re.search(r"· 9[6-9]s</span>", body), "elapsed since the first line"
    assert 'colspan="4"' in body, \
        "spans gmail, gpt account, ip and age; the buttons cell holds Cancel"
    assert "starting" in body, "a phone with no line yet"
    assert 'href="/phones/1556"' in body
    assert 'http-equiv="refresh"' in body


def test_the_keepers_warning_is_said_once_above_the_title(
        web, monkeypatch):
    """It was said twice: the strip above the title, and an amber line
    inside the page carrying the same sentence. A page that says the same
    thing twice is a page where a reader learns to skip both (2026-09-05).

    The strip is the one that stayed, because it is above the title and
    on every page. What the in-page line uniquely had - a link to the
    pool - is now the card in the rail, which is nearer the hand.
    """
    say = "the Gmail tab has no free rows to build from"
    _dash(monkeypatch, pulse={"warm": 2, "target": 5, "tripped": "",
                              "at": 0, "warning": say})
    # The strip is fed by the alerts on the nav, which is where every page
    # gets it - the dashboard no longer has a copy of its own.
    monkeypatch.setattr(app_mod.read, "nav_counts",
                        lambda s: {"gmail": 0, "proxy": 2, "app": 1,
                                   "pending": 0,
                                   "alerts": [{"level": "warn", "text": say,
                                               "href": "/pools/gmail"}]})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert body.count("the Gmail tab has no free rows") == 1
    # And the one place it is said is the Gmail card, not a strip: the
    # card's count is the rest of the story, and a strip beside it said
    # the same thing twice (2026-09-05).
    where = body.index("the Gmail tab has no free rows")
    assert body.rindex('<section class="pool"', 0, where) < where
    assert 'class="alert warn"' not in body


def test_phones_are_ordered_ready_warm_incomplete_building_and_handed_over(
        web, monkeypatch):
    _dash(monkeypatch, phones=[
        {"serial": "1503", "status": "building", "state": ""},
        {"serial": "1502", "status": "incomplete", "state": ""},
        {"serial": "1501", "status": "app_only", "state": "",
         "gmail": "Stone@gmail.com", "proxy_name": "SX31"},
        {"serial": "1500", "status": "ready", "state": "",
         "gmail": "IronHawk@gmail.com", "app_account": "h@x.com",
         "proxy_name": "SX27"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    order = [body.index(f'href="/phones/{s}"') for s in
             ("1500", "1501", "1503")]
    assert order == sorted(order), "ready, warm, building"
    assert "waiting for one" in body, \
        "the warm row says what its own column lacks"
    # The incomplete one is a row like the others, last, wearing the amber
    # of something that wants a look - and outside the Free view.
    assert 'class="didnot"' not in body
    assert body.index('href="/phones/1501"') < body.index('href="/phones/1502"')
    assert body.index('href="/phones/1502"') < body.index('href="/phones/1503"')
    row = body[body.rindex("<tr", 0, body.index('href="/phones/1502"')):]
    assert row.startswith('<tr data-view="incomplete"')


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_take_back_done_and_failed_are_gated_and_the_deleting_ones_ask(
        web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch, phones=[
        {"serial": "1500", "status": "ready", "state": "",
         "gmail": "IronHawk@gmail.com", "app_account": "h@x.com",
         "proxy_name": "SX27"},
        {"serial": "1501", "status": "ready", "state": "taken",
         "owner": "mehdi", "updated_at": "2026-09-03 10:00:00+00"}])
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 61)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    # Who has it sits under the badge; when it last changed has its own
    # column now, and saying it twice was the page saying a number twice.
    # Whose it is rides as a second pill beside the status, not under it.
    assert '>With you</span>' in body
    assert 'class="badge ready">Ready</span> <span class="badge manual"' \
        not in body, "one pill, not two, once it is taken (2026-09-08)"
    assert app_mod.pages._ago("2026-09-03 10:00:00+00") in body
    # The free phone offers Boot, not Take (2026-09-16); the door still
    # knows the word, below.
    assert '/phones/1500/boot' in body
    assert '/phones/1500/state' not in body and 'value="taken"' not in body
    assert 'value="unused"' in body and "Release" in body, \
        "a taken phone can be let go"
    assert body.count('value="done"') == 1, "only the taken phone closes here"
    assert body.count('value="failed"') == 1
    # Only on the free phone: a phone you hold comes back first - Done,
    # Failed, Release - and its exit is changed once it is back.
    assert body.count("Change IP") == 1, "on the free phone only"
    shelf = body.index('/phones/1500/boot')
    assert shelf < body.index('/phones/1501/state'), \
        "the shelf first, then what is out with somebody"

    status, headers, _ = client.request(
        "POST", "/phones/1500/state", _form(csrf=client.csrf(), state="taken"))
    assert status == 303 and dict(headers)["Location"].startswith("/?said=queued")
    assert got["verb"] == "set_phone_state"
    assert got["payload"]["serial"] == "1500"
    assert got["payload"]["state"] == "taken" and got["payload"]["by"] == "mehdi"

    got.clear()
    status, _, body = client.request(
        "POST", "/phones/1500/state", _form(csrf=client.csrf(), state="done"))
    assert status == 200 and "Mark phone 1500 done?" in body
    assert "no undo" in body and got == {}, "asked first, nothing queued"
    status, _, _ = client.request(
        "POST", "/phones/1500/state",
        _form(csrf=client.csrf(), state="done", sure="1"))
    assert status == 303 and got["payload"]["state"] == "done"

    status, _, _ = client.request(
        "POST", "/phones/1500/state", _form(csrf=client.csrf(), state="dome"))
    assert status == 404, "a State word the sheet never had"

    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "all", "may_take_phones": False})
    narrow = web()
    narrow.login(username="narrow")
    _, _, body = narrow.request("GET", "/")
    assert "/phones/1500/state" not in body
    assert "needs the may_take_phones permission" in body, \
        "flag on, permission off: say which one"


@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_awaiting_cards_say_how_long_ago_and_count_the_warm_phones(
        web, monkeypatch):
    import datetime as dt

    ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=14)
    base = _dash(monkeypatch, awaiting=[
        {"address": "arman@gmail.com", "source": "panel", "added_by": "",
         "created_at": ago}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    # The panel went (2026-09-05): the accounts with no phone are the GPT
    # card's number and the list under it, and sending one is a button on
    # its row. What the panel alone said - how long ago, how many warm
    # phones - was a second copy of the card's count and the status line.
    assert "Awaiting login" not in body
    assert 'class="pick tick"' not in body
    assert "waiting@x.com" in body
    assert "&rarr; phone" in body

    base["pulse"] = {"warm": 0, "target": 5, "tripped": "", "at": 0}
    _, _, body = client.request("GET", "/")
    assert "&rarr; phone" in body, "the button does not come and go"


def test_the_dashboard_carries_no_events(web, monkeypatch):
    """Events are a developer's line on an operator's page.

    The ticker read well and told the truth, and it still went: this page
    is what somebody handing out phones sees all day, and the last thing
    the breaker said is not one of the two questions they came with. The
    events page keeps all of it, and the rail keeps the way there.
    """
    _dash(monkeypatch,
          recent=[{"at": "2026-09-01 18:04:31+00", "kind": "build_finished",
                   "serial": "1551", "status": "ready",
                   "detail": "ok=True gmail=x"}],
          asked=[{"id": 241, "verb": "login_accounts", "status": "running",
                  "payload": {"addresses": ["a@x.com", "b@x.com"]},
                  "at": "2026-09-01 18:06:12+00", "requested_by": "mehdi"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    assert "all events" not in body
    assert "became ready" not in body
    assert "asked: Log in 2 accounts" not in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_boot_opens_a_tab_that_waits_for_the_live_screen(web, monkeypatch):
    """The live-view URL is the answer to the start call, so the press
    cannot hand one over on the spot. It opens a tab that watches its own
    request and goes to the screen when it lands."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch, phones=[
        {"serial": "1500", "status": "ready", "state": "",
         "gmail": "IronHawk@gmail.com", "app_account": "h@x.com",
         "proxy_name": "SX27"}])
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 71)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert 'action="/phones/1500/boot"' in body and "Boot" in body
    assert 'target="_blank"' in body, "the dashboard tab stays where it is"

    status, headers, _ = client.request(
        "POST", "/phones/1500/boot", _form(csrf=client.csrf()))
    assert status == 303
    assert dict(headers)["Location"] == "/phones/1500/live?said=queued:71"
    assert got["verb"] == "boot_phone" and got["payload"]["serial"] == "1500"

    row = {"id": 71, "verb": "boot_phone", "status": "queued", "result": "",
           "detail": None, "requested_by": 1}
    monkeypatch.setattr(actions_mod, "one", lambda s, aid: row)
    status, _, body = client.request(
        "GET", "/phones/1500/live?said=queued:71")
    assert status == 200 and "Starting 1500" in body
    assert 'http-equiv="refresh"' in body, "the tab checks back by itself"

    row.update(status="done", result="phone 1500 started and taken by mehdi",
               detail={"state": "taken",
                       "url": "https://phone.geelark.com/i?t=abc"})
    status, _, body = client.request(
        "GET", "/phones/1500/live?said=queued:71")
    # The viewer inside this tab, not a redirect to GeeLark's page: the
    # tab's closing is the phone's off switch (2026-09-16).
    assert status == 200
    assert ('<iframe id="gf-view" data-src="https://phone.geelark.com/i?t=abc" '
            'allow="clipboard-read; clipboard-write; fullscreen">') in body
    assert 'base="https://phone.geelark.com/i?t=abc"' in body
    assert "fetch('/phones/'+serial+'/watching'" in body
    assert "setInterval(beat,15000)" in body
    # A real close is said outright, so it never waits on the beat's
    # grace - and a released phone is said on the page, not only in the
    # bar (operators found "token has expired" instead, 2026-09-16).
    assert "navigator.sendBeacon('/phones/'+serial+'/closing'" in body
    assert "window.addEventListener('pagehide',closing)" in body
    assert "if(document.visibilityState==='visible') beat();" in body
    assert "if(r.status===410){gone=true; released();}" in body
    # GeeLark's viewer times out on some operators' routes (Firefox's own
    # page inside the frame, 2026-09-16): a reload of the frame alone,
    # without closing the tab that keeps the phone on.
    assert 'id="gf-reload"' in body
    assert "frame.setAttribute('src','about:blank')" in body
    assert "This phone was put back" in body
    assert 'var serial="1500"' in body and "<nav>" not in body, "bare"
    # One fixed width for the viewer, and its box scaled to the window:
    # the phone fits any monitor, Back and Home included (2026-09-16).
    assert "u.searchParams.set('w',String(W))" in body
    assert "var k=Math.min(h/BOX_H,w/BOX_W);" in body
    assert "frame.style.transform='scale('+k+')'" in body
    assert "window.addEventListener('resize',fit)" in body
    assert "width:416px;height:752px" in body
    assert "gf-live" not in body and "addEventListener('submit'" not in body

    row.update(status="failed", result="phone 1500 would not start: "
                                       "[43043] no capacity", detail=None)
    status, _, body = client.request(
        "GET", "/phones/1500/live?said=queued:71")
    assert status == 200 and "1500 did not start" in body
    assert "no capacity" in body and 'http-equiv="refresh"' not in body


def test_the_boot_tab_says_a_refusal_rather_than_sitting_blank(web,
                                                               monkeypatch):
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/phones/1500/live?said=refused")
    assert "Not allowed" in body and "ask an admin" in body
    assert 'http-equiv="refresh"' not in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_each_button_wears_the_colour_of_what_it_does(web, monkeypatch):
    """Green finishes well, red finishes badly, blue is the ordinary next
    step, violet starts a phone and amber repairs one."""
    _dash(monkeypatch, phones=[
        {"serial": "1500", "status": "ready", "state": ""},
        {"serial": "1501", "status": "ready", "state": "taken",
         "owner": "mehdi"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    # Colour is for the three that end something. The exit button is
    # ordinary work on an ordinary phone: it wore amber, which is a page
    # shouting and so emphasising nothing (2026-09-05). Boot is the one
    # button on a free row since Take went (2026-09-16), and the one
    # filled one: a power sign and no outline.
    for klass, label in (("quiet", "Release"), ("quiet ok", "Done"),
                         ("quiet bad", "Failed"), ("quiet", "Change IP")):
        assert f'class="{klass}">{label}<' in body, label
    assert '<button class="boot" title=' in body
    assert "</svg>Boot</button>" in body
    assert ">Take<" not in body, "Take went with the Live tab's close"
    assert 'class="quiet live"' not in body, "Boot is not a fourth colour"
    assert 'class="quiet warn">Change IP' not in body, "nor is the exit"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_phone_somebody_else_holds_offers_only_their_name(web, monkeypatch):
    """The three ways a phone comes back belong to the person holding it.
    Offering them to another operator is offering to act on a phone that
    is not theirs (the contract, 2026-09-05)."""
    _dash(monkeypatch, phones=[{"serial": "1501", "status": "ready",
                                "state": "taken", "owner": "ali"}])
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "sara", "role": "operator",
                         "sees": "all", "may_take_phones": True,
                         "may_change_proxy": True})
    client = web()
    client.login(username="sara")
    _, _, body = client.request("GET", "/")
    start = body.index('href="/phones/1501"')
    row = body[start:body.index("</tr>", start)]
    assert '<span class="age">with ali</span>' in row
    for label in ("Release", "Done", "Failed", "Boot", "Take", "Change IP"):
        assert f">{label}<" not in row, label
    assert 'class="badge manual" title="Ready">With ali</span>' in row


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_an_admin_may_end_anybody_s_hold_from_the_table(web, monkeypatch):
    """Operators forget phones; the person running the farm needs to
    release, close or write off one without waiting for whoever took it
    (the operator, 2026-09-15). The row still says whose it is, and the
    two that delete the phone still ask first. Boot, Take and Change IP
    stay off a taken row, as they are for its holder."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch, phones=[{"serial": "1501", "status": "ready",
                                "state": "taken", "owner": "ali"}])
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 64)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    start = body.index('href="/phones/1501"')
    row = body[start:body.index("</tr>", start)]
    for label in ("Release", "Done", "Failed"):
        assert f">{label}<" in row, label
    for label in ("Boot", "Take", "Change IP"):
        assert f">{label}<" not in row, label
    assert '<span class="age">with ali</span>' not in row
    assert 'title="Ready">With ali</span>' in row, "still says whose it is"

    # The POST goes through, and the request says whose phone it was.
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": {"serial": serial, "status": "ready",
                                    "state": "taken", "owner": "ali"},
        "timeline": []})
    status, headers, _ = client.request(
        "POST", "/phones/1501/state",
        _form(csrf=client.csrf(), state="unused", back="/"))
    assert status == 303 and "refused" not in dict(headers)["Location"]
    assert got["verb"] == "set_phone_state"
    assert got["payload"]["held_by"] == "ali"
    assert got["payload"]["state"] == "unused"
    from geelark_farm.web import pages as pages_mod

    head, aside = pages_mod.describe("set_phone_state", got["payload"])
    assert (head, aside) == ("Mark phone 1501 unused", "it was with ali")
    # Their own phone carries no such note.
    assert pages_mod.describe("set_phone_state",
                              {"serial": "1501", "state": "done"}) == (
        "Mark phone 1501 done", "")


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_taken_phone_offers_no_boot_until_it_is_released(web, monkeypatch):
    """Boot starts a phone and takes it in one press, so it belongs to a
    phone nobody holds. On a taken row it offers to take what is already
    taken, and the row it would produce is the row it is already on."""
    _dash(monkeypatch, phones=[{"serial": "1501", "status": "ready",
                                "state": "taken", "owner": "mehdi"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    assert ">Boot<" not in body, "it is already taken"
    # The three ways being taken ends are still there, so the row is not
    # simply emptier - it is the right shape for where the phone is.
    for label in ("Release", "Done", "Failed"):
        assert f">{label}<" in body, label


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_phone_on_the_shelf_still_offers_boot(web, monkeypatch):
    """The counterweight: taking a phone by booting it is the whole point
    of the button, and that is what a shelf row is for."""
    _dash(monkeypatch, phones=[{"serial": "1500", "status": "ready",
                                "state": ""}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    assert ">Boot<" in body
    assert 'action="/phones/1500/boot"' in body


def test_the_dashboard_no_longer_carries_the_service_line(web, monkeypatch):
    """Pausing the service and reading the flags are a settings question,
    not a front-page one; they leave together and come back on a page of
    their own. The row itself still renders - it has somewhere to go."""
    _dash(monkeypatch, pulse={"warm": 5, "target": 5, "tripped": "",
                              "paused": False, "at": 0})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert "WEB_MUTATIONS" not in body and "POOLS_IN_PG" not in body
    assert 'class="svc"' not in body and "/service/" not in body


def test_the_controls_follow_the_pulse_and_the_person():
    """Which controls are offered follows the pulse - a running service is
    paused or stopped, a tripped one is resumed and cleared, a stopped one
    starts - and who is offered them follows the person.

    This tested `_service_row`, a renderer at the foot of the page that
    nothing ever called. So the buttons it composed had no home and nobody
    could press any of them: an operator who pasted a batch of Gmails had
    to go and find an admin, and the admin had no button either. They sit
    beside the status line now, which is where a person is already looking
    when it says building has stopped (2026-09-07).
    """
    admin = {"id": 1, "username": "mehdi", "role": "admin", "csrf": "x",
             "mutations": True}

    running = {"pulse": {"warm": 5, "target": 5, "tripped": "",
                         "paused": False, "at": 0}}
    row = app_mod.pages._controls(running, admin)
    assert 'action="/service/pause"' in row and "Pause building" in row
    assert 'action="/service/stop"' in row
    assert "/service/resume" not in row and "/service/clear_breaker" not in row

    tripped = {"pulse": {"warm": 5, "target": 5, "tripped": "captcha x5",
                         "paused": True, "at": 0}}
    row = app_mod.pages._controls(tripped, admin)
    assert 'action="/service/resume"' in row and "Resume building" in row
    assert 'action="/service/clear_breaker"' in row and "/service/pause" not in row

    stopped = {"pulse": {"stopped": True, "at": 0, "tripped": ""}}
    row = app_mod.pages._controls(stopped, admin)
    assert 'action="/service/start"' in row and "/service/stop" not in row

    # An operator gets the one control that is about stock rather than
    # about the service, and only while the breaker is actually open.
    keeper = {"id": 9, "username": "narrow", "role": "operator", "csrf": "x",
              "mutations": True, "may_add_gmail": True}
    assert app_mod.pages._controls(running, keeper) == ""
    theirs = app_mod.pages._controls(tripped, keeper)
    assert 'action="/service/clear_breaker"' in theirs
    for shut in ("pause", "resume", "stop", "start"):
        assert f'/service/{shut}"' not in theirs, f"{shut} is the admin's"

    # And somebody who may not add stock is offered nothing at all.
    watcher = {"id": 10, "username": "eyes", "role": "operator", "csrf": "x",
               "mutations": True}
    assert app_mod.pages._controls(tripped, watcher) == ""


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_service_buttons_still_ask_once_and_are_admin_only(
        web, monkeypatch):
    """The route outlives the row: a Settings page will post to it, and
    the confirm and the 403 are what make it safe to."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 71)
    client = web()
    client.login()
    status, _, body = client.request(
        "POST", "/service/pause", _form(csrf=client.csrf()))
    assert status == 200 and "Pause building?" in body and got == {}
    status, headers, _ = client.request(
        "POST", "/service/pause", _form(csrf=client.csrf(), sure="1"))
    assert status == 303 and dict(headers)["Location"].startswith("/?said=queued")
    assert got["verb"] == "control" and got["payload"]["what"] == "pause"
    status, _, _ = client.request(
        "POST", "/service/reboot", _form(csrf=client.csrf(), sure="1"))
    assert status == 404

    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "all", "may_take_phones": True})
    narrow = web()
    narrow.login(username="narrow")
    got.clear()
    status, _, _ = narrow.request(
        "POST", "/service/stop", _form(csrf=narrow.csrf(), sure="1"))
    assert status == 403 and got == {}


def test_the_rail_counts_what_needs_attention(web, monkeypatch):
    monkeypatch.setattr(app_mod.read, "nav_counts",
                        lambda s: {"gmail": 3, "proxy": 2, "app": 0,
                                   "pending": 0, "needs": 4})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    rail = body[body.index('href="/needs"'):body.index('href="/events"')]
    assert '<span class="n hot">4</span>' in rail

    monkeypatch.setattr(app_mod.read, "nav_counts",
                        lambda s: {"gmail": 3, "proxy": 2, "app": 0,
                                   "pending": 0, "needs": 0})
    _, _, body = client.request("GET", "/")
    rail = body[body.index('href="/needs"'):body.index('href="/events"')]
    assert '<span class="n">0</span>' in rail


def _needs(monkeypatch):
    monkeypatch.setattr(app_mod.read, "needs", lambda s: {
        "orphaned": [], "broken": [],
        "flagged": [{"kind": "gmail", "who": "x@y.com",
                     "status": "wrong_password", "serial": "", "note": ""},
                    {"kind": "app", "who": "a@y.com",
                     "status": "payment_problem", "serial": "", "note": ""},
                    {"kind": "proxy", "who": "SX9", "status": "change ip",
                     "serial": "", "note": ""}],
        "given_up": [{"serial": "1398", "status": "app_only", "tries": 3,
                      "note": "three strikes"}]})


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_needs_offers_again_and_clears_tries_through_the_queue(web,
                                                               monkeypatch):
    import geelark_farm.store.actions as actions_mod

    _needs(monkeypatch)
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 81)
    client = web()
    client.login()
    status, _, body = client.request("GET", "/needs")
    assert status == 200
    assert body.count('action="/needs/offer"') == 2, "gmail and app, not proxy"
    assert 'name="kind" value="gmail"' in body and 'value="x@y.com"' in body
    assert 'action="/needs/clear"' in body and 'value="1398"' in body
    assert 'href="/phones/1398"' in body

    status, headers, _ = client.request(
        "POST", "/needs/offer",
        _form(csrf=client.csrf(), kind="gmail", address="x@y.com"))
    assert status == 303 and dict(headers)["Location"].startswith(
        "/needs?said=queued")
    assert got["verb"] == "offer_again"
    assert got["payload"]["address"] == "x@y.com"
    assert got["payload"]["kind"] == "gmail"

    status, headers, _ = client.request(
        "POST", "/needs/clear", _form(csrf=client.csrf(), serial="1398"))
    assert status == 303 and got["verb"] == "clear_tries"
    assert got["payload"]["serial"] == "1398"

    _, _, body = client.request("GET", dict(headers)["Location"])
    assert "#81 on Requests" in body


def _app_row(address, status="", **more):
    row = {"id": 1, "address": address, "status": status, "serial": "",
           "source": "manual", "added_by": 7, "added_by_name": "mehdi",
           "note": "", "updated_at": "2026-09-01 18:04:00+00:00",
           "created_at": "2026-09-01 15:02:00+00:00",
           "email_code_only": False, "has_totp": True}
    row.update(more)
    return row


def _gpt_active(monkeypatch, waiting=(), on_phone=(), needs_human=(),
                broken=(), seen=None):
    """read.gpt_pool as the page now asks for it: one view, one list of
    rows, and the counts every pill shows. Panel and hand-added accounts
    share the waiting list - `source` is what tells them apart."""
    seen = seen if seen is not None else {}
    lists = {"waiting": list(waiting), "on_phone": list(on_phone),
             "needs_human": list(needs_human)}
    counts = {"waiting": len(lists["waiting"]),
              "on_phone": len(lists["on_phone"]),
              "needs_human": len(lists["needs_human"]),
              "delivered": 108, "broken": len(broken)}

    def gpt_pool(settings, view="waiting", q="", page=1, per_page=50):
        seen.update(view=view, q=q, page=page)
        rows = lists.get(view, [])
        out = {"view": view, "counts": counts, "rows": rows, "q": q,
               "page": page, "more": False, "total": len(rows), "pages": 1}
        if view == "needs_human":
            out["broken"] = list(broken)
        return out

    monkeypatch.setattr(app_mod.read, "gpt_pool", gpt_pool)
    return seen


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_gpt_paste_is_previewed_row_by_row_and_confirmed_as_rows(
        web, monkeypatch):
    import geelark_farm.store.actions as actions_mod
    from tests.test_builder import SECRET

    _gpt_active(monkeypatch)
    monkeypatch.setattr(app_mod.read, "known",
                        lambda s, kind: {"dup@x.com": "delivered"}
                        if kind == "app" else {})
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 91)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gpt")
    assert 'action="/pools/gpt/preview"' in body, "the paste box"
    assert 'action="/pools/gpt/add"' in body, "and the by-hand form"

    pasted = f"good@x.com\tpw1\t{SECRET}\ndup@x.com\tpw2\nnope\n"
    status, _, body = client.request(
        "POST", "/pools/gpt/preview", _form(csrf=client.csrf(), pasted=pasted))
    assert status == 200
    assert "preview — nothing is added yet" in body
    assert body.count('class="badge ok">ok') == 1
    assert "this account has been delivered" in body, (
        "dup@x.com is known to the mirror, and saying only 'already in the "
        "pool' sends them to a list that does not carry delivered rows")
    assert body.count('class="badge bad"') == 2, "the duplicate and nope"
    assert "Add 1 (skip 2)" in body
    assert f"good@x.com\tpw1\t{SECRET}" in body, "only the good row is carried"
    assert "dup@x.com\tpw2" not in body.split('name="rows"')[1].split(
        "</textarea>")[0]
    assert 'name="pasted">good@x.com' in body, "the paste stays editable"

    status, headers, _ = client.request(
        "POST", "/pools/gpt/add",
        _form(csrf=client.csrf(), idem="k-1",
              rows=f"good@x.com\tpw1\t{SECRET}"))
    assert status == 303
    assert dict(headers)["Location"] == "/pools/gpt?said=queued:91"
    assert got["verb"] == "add_gpt" and got["idem_key"].startswith("k-1:")
    assert got["payload"]["rows"] == [
        {"address": "good@x.com", "password": "pw1", "secret": SECRET,
         "email_code_only": False}]
    assert got["payload"]["by"] == "mehdi"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_refused_by_hand_account_comes_back_filled_in_with_the_reason(
        web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    _gpt_active(monkeypatch)
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda *a, **k: pytest.fail("queued anyway"))
    client = web()
    client.login()
    status, _, body = client.request(
        "POST", "/pools/gpt/add",
        _form(csrf=client.csrf(), address="nope", password="pw",
              secret="", email_code="1"))
    assert status == 200, "the page, not a redirect that empties the form"
    assert 'name="address" placeholder="email address" autocomplete="off" ' \
           'value="nope"' in body
    assert 'value="pw"' in body and 'value="1" checked' in body
    assert '<p class="err">' in body and "nope" in body.split(
        '<p class="err">')[1].split("</p>")[0], "the exact reason"
    assert 'action="/pools/gpt/preview"' in body, "the paste box is still there"
    assert '<details class="fold" open>' in body, "the fold it was typed in"
    assert '<div class="pills">' in body, "and the four views"


@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_the_gpt_pool_ticks_waiting_rows_and_logs_them_in_from_there(
        web, monkeypatch):
    """One list for both sources, ticks on it, and one button. What is
    already on a phone is a different question and a different view."""
    import geelark_farm.store.actions as actions_mod

    _gpt_active(monkeypatch,
                waiting=[_app_row("a@x.com", source="panel"),
                         _app_row("c@x.com")],
                on_phone=[_app_row("b@x.com", "in_use", source="panel",
                                   serial="1550")])
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 92)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gpt")
    assert body.count('type="checkbox" name="addresses"') == 2
    assert 'name="addresses" value="a@x.com"' in body
    assert 'name="addresses" value="c@x.com"' in body
    assert 'value="b@x.com"' not in body, "what is on a phone has its own view"
    assert 'class="badge panel">panel' in body, "one list, one where-from"
    assert "manual · mehdi" in body
    assert "2</span> waiting" in body, "the sentence, not a bar of numbers"
    assert "Log in selected" in body
    assert 'name="back" value="/pools/gpt"' in body
    assert "log in on their own" not in body

    _, _, body = client.request("GET", "/pools/gpt?view=on_phone")
    assert "b@x.com" in body and '<a href="/phones/1550">1550</a>' in body
    assert 'class="badge in_use">signing in' in body
    assert 'name="pasted"' not in body, "the add box belongs to Waiting"

    status, headers, _ = client.request(
        "POST", "/accounts/login",
        _form(csrf=client.csrf(), back="/pools/gpt") + "&addresses=a%40x.com"
        "&addresses=c%40x.com")
    assert status == 303
    assert dict(headers)["Location"] == "/pools/gpt?said=queued:92"
    assert got["verb"] == "login_accounts"
    assert got["payload"]["addresses"] == ["a@x.com", "c@x.com"]

    _, headers, _ = client.request(
        "POST", "/accounts/login", _form(csrf=client.csrf(), back="/pools/gpt"))
    assert dict(headers)["Location"] == "/pools/gpt?said=none"
    _, _, body = client.request("GET", "/pools/gpt?said=none")
    assert "Tick at least one account first." in body

    _, headers, _ = client.request(
        "POST", "/accounts/login", _form(csrf=client.csrf(), back="/evil"))
    assert dict(headers)["Location"] == "/?said=none", \
        "only the two pages with ticks are places to go back to"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_waiting_sentence_answers_whether_a_phone_can_take_them(
        web, monkeypatch):
    """The one question the front door exists for. With no pass to read a
    pulse from, the page says the number it knows and claims nothing
    about phones."""
    _gpt_active(monkeypatch, waiting=[_app_row("a@x.com"),
                                      _app_row("b@x.com")])
    nav = {"gmail": 3, "proxy": 2, "app": 2, "pending": 0}
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gpt")
    assert "2</span> waiting for a phone" in body, "no pulse, no claim"

    for warm, tail in ((6, "6 warm phones can take them"),
                       (1, "only 1 warm phone free"),
                       (0, "no warm phone is free for them")):
        monkeypatch.setattr(
            app_mod.read, "nav_counts",
            lambda s, w=warm: dict(nav, pulse={"warm": w, "target": 5,
                                               "tripped": "", "at": 0}))
        _, _, body = client.request("GET", "/pools/gpt")
        assert f"waiting — {tail}" in body, warm


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_what_happened_shows_the_instruction_not_the_paragraph(
        web, monkeypatch):
    """A verdict's advice is a paragraph of reasoning that ends in one
    instruction. The table shows the instruction; the reasoning rides in
    the title, where it costs no rows."""
    from geelark_farm.failures import verdict

    _gpt_active(monkeypatch,
                needs_human=[_app_row("h@x.com", "payment_problem")])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gpt?view=needs_human")
    said = verdict("payment_problem")
    esc = app_mod.pages.esc
    assert esc(said.seen) in body
    assert esc(said.advice.split(". ")[-1]) in body, "what to do about it"
    assert f'title="{esc(said.advice)}"' in body, "the whole of it, on hover"
    middle = esc(said.advice.split(". ")[1])
    assert body.count(middle) == 1, \
        "the reasoning is in the title alone, not loose in the cell"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_by_hand_folds_are_not_forms_inside_forms(web, monkeypatch):
    """A form inside a form is not HTML: the parser drops the inner tag,
    and its button then submits the outer one. Both pools' by-hand folds
    shipped that way (2026-09-04)."""
    import re

    _gmail_active(monkeypatch)
    _gpt_active(monkeypatch, waiting=[_app_row("a@x.com")])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gpt")
    assert "<summary>add one by hand</summary>" in body, "the gpt fold"
    for path in ("/pools/gmail", "/pools/gpt"):
        status, _, body = client.request("GET", path)
        assert status == 200, path
        depth = 0
        for token in re.findall(r"</?form", body):
            depth += 1 if token == "<form" else -1
            assert depth in (0, 1), f"{path}: a form inside a form"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_with_manual_login_off_the_gpt_pool_says_accounts_log_in_on_their_own(
        web, monkeypatch):
    _gpt_active(monkeypatch, waiting=[_app_row("c@x.com")])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gpt")
    assert 'name="addresses"' not in body and "Log in selected" not in body
    assert "accounts log in on their own on the next pass" in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_needs_a_human_says_what_was_seen_and_what_to_do(web, monkeypatch):
    from geelark_farm.failures import verdict

    _gpt_active(monkeypatch, needs_human=[
        _app_row("h@x.com", "payment_problem", source="panel",
                 added_by_name=None, note="raw sheet note"),
        _app_row("k@x.com", "made_up_word", note="only the note")])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gpt?view=needs_human")
    assert "Needs a human" in body and "2</span> set aside" in body
    assert "payment problem" in body, "the reason in words, not the token"
    said = verdict("payment_problem")
    assert app_mod.pages.esc(said.seen) in body
    assert app_mod.pages.esc(said.advice) in body
    assert "raw sheet note" not in body, "the sentence replaces the note"
    assert "only the note" in body, "an unknown word keeps the note"
    assert 'class="badge panel">panel' in body
    assert 'class="badge manual">manual · mehdi' in body
    assert body.count('action="/pools/gpt/offer"') == 2
    assert 'name="address" value="h@x.com"' in body

    _gpt_active(monkeypatch, needs_human=[_app_row("h@x.com", "captcha_shown")],
                broken=[{"id": 4, "address": "gpt9@aytack",
                         "error": "the address is not an email"}])
    _, _, body = client.request("GET", "/pools/gpt?view=needs_human")
    assert "1</span> set aside" in body
    assert "Refused before the pool" in body and "gpt9@aytack" in body


def test_the_delivered_view_links_phones_counts_pages_and_exports_csv(
        web, monkeypatch):
    seen = {}
    stamp = "2026-09-01 18:04:00+00:00"

    def gpt_pool(settings, view="waiting", q="", page=1, per_page=50):
        return {"view": "delivered",
                "counts": {"waiting": 0, "on_phone": 0, "needs_human": 0,
                           "delivered": 873},
                "rows": [_app_row("d@x.com", "delivered", serial="1542",
                                  note="went out", updated_at=stamp)],
                "q": q, "page": page, "more": True, "total": 61,
                "pages": 2}

    def delivered_rows(settings, q=""):
        seen["q"] = q
        return [{"address": "d@x.com", "serial": "1542", "updated_at": stamp,
                 "source": "manual"},
                {"address": "e@x.com", "serial": "", "updated_at": None,
                 "source": "panel"}]

    monkeypatch.setattr(app_mod.read, "gpt_pool", gpt_pool)
    monkeypatch.setattr(app_mod.read, "delivered_rows", delivered_rows)
    client = web()
    client.login()
    status, _, body = client.request(
        "GET", "/pools/gpt?view=delivered&q=a%20b&page=2")
    assert status == 200
    assert '<a href="/phones/1542">1542</a>' in body
    assert "page 2 of 2" in body
    assert 'href="/pools/gpt?view=delivered&q=a%20b&page=1">← newer' in body
    assert 'href="/pools/gpt?view=delivered&q=a%20b&page=3">older →' in body
    assert 'href="/pools/gpt/delivered.csv?q=a%20b">Export CSV' in body
    assert '61 of 873 delivered accounts match "a b"' in body

    status, headers, body = client.request(
        "GET", "/pools/gpt/delivered.csv?q=a%20b")
    assert status == 200 and seen == {"q": "a b"}
    got = dict(headers)
    assert got["Content-Type"].startswith("text/csv")
    assert got["Content-Disposition"] == 'attachment; filename="gpt-delivered.csv"'
    when = app_mod.pages._moment(stamp).isoformat(timespec="minutes")
    assert body.splitlines() == ["address,serial,delivered_at,source",
                                 f"d@x.com,1542,{when},manual",
                                 "e@x.com,,,panel"]


def test_describe_reads_the_service_controls_and_counts_gpt_adds():
    from geelark_farm.web.pages import describe

    assert describe("control", {"what": "pause"}) == ("Pause building", "")
    assert describe("control", {"what": "resume"}) == ("Resume building", "")
    assert describe("control", {"what": "clear_breaker"}) == (
        "Clear breaker", "")
    assert describe("control", {"what": "stop"}) == ("Stop everything", "")
    assert describe("control", {"what": "start"}) == ("Start again", "")
    assert describe("control", {}) == ("Control", "")
    assert describe("add_gpt", {"rows": [{"address": "a@x.com"}]}) == (
        "Add 1 GPT account", "a@x.com")
    assert describe("add_gpt", {"rows": [{"address": f"u{i}@x.com"}
                                         for i in range(8)]}) == (
        "Add 8 GPT accounts",
        "u0@x.com, u1@x.com, u2@x.com, u3@x.com, u4@x.com, u5@x.com …")


@pytest.mark.parametrize("web", [True], indirect=True)
def test_the_requests_page_pages_highlights_and_tells_the_sub_stories(
        web, monkeypatch):
    import datetime

    import geelark_farm.store.actions as actions_mod

    now = datetime.datetime.now(datetime.timezone.utc)
    ago = (now - datetime.timedelta(minutes=30)).isoformat()
    lately = (now - datetime.timedelta(minutes=5)).isoformat()
    rows = list(_REQUESTS) + [
        {"id": 237, "verb": "add_gmails", "status": "done",
         "payload": {"rows": [{}] * 3, "seller": "usa"},
         "result": "1 gmail added, 1 already in the pool, 1 refused",
         "detail": {"added": ["ok@x.com"], "skipped": ["dup@x.com"],
                    "refused": ["FireHawk@x.com: seller usa accounts come "
                                "with an authenticator key, but this one "
                                "carries a recovery address"]},
         "requested_at": ago, "executed_at": ago, "finished_at": ago,
         "requested_by": "mehdi"},
        {"id": 236, "verb": "remove_proxy", "status": "done",
         "payload": {"name": "SX3"}, "result": "SX3 removed from the pool",
         "detail": {"removed": {"name": "SX3", "raw": "1.2.3.4:9999:u:p",
                                "status": "", "note": ""}},
         "requested_at": ago, "executed_at": ago, "finished_at": ago,
         "requested_by": "mehdi"},
        {"id": 235, "verb": "test_all_proxies", "status": "running",
         "payload": {}, "result": "", "detail": None,
         "requested_at": lately, "executed_at": lately, "finished_at": None,
         "requested_by": "mehdi"},
    ]
    asked = {}
    monkeypatch.setattr(actions_mod, "listing",
                        lambda s, **k: asked.update(k) or list(rows))
    monkeypatch.setattr(actions_mod, "counts",
                        lambda s, **k: {"running": 2, "queued": 1,
                                        "done": 117, "failed": 1})
    wanted = {}
    monkeypatch.setattr(app_mod.read, "latest_lines",
                        lambda s, serials: wanted.update(
                            serials=list(serials)) or {
                            "1549": {"serial": "1549",
                                     "logger": "geelark_farm.chatgpt_login",
                                     "msg": "totp accepted, reading the "
                                            "session back\nmore",
                                     "started": None}})
    client = web()
    client.login()
    status, _, body = client.request("GET", "/requests?page=2&hi=239")
    assert status == 200
    assert asked["page"] == 2 and asked["view"] == ""
    assert wanted["serials"] == ["1549", "1550"], "the phones still working"
    assert "page 2 of 3" in body, "121 rows, fifty a page"
    assert 'href="/requests?view=&page=1">← newer' in body
    assert "older →" not in body, "fifty-one rows would have said so"
    assert '<tr class="hi"><td class="muted">239</td>' in body
    assert body.count('class="hi"') == 1
    assert 'class="live">live' in body
    assert ("↳ FireHawk@x.com: seller usa accounts come with an authenticator "
            "key, but this one carries a recovery address") in body
    assert "↳ dup@x.com: already in the pool" in body
    assert "chatgpt sign-in: totp accepted, reading the session back" in body
    assert "more</td>" not in body, "only the first line of the message"
    assert body.count("booting") == 1, "1550 has no line yet and says so"
    assert "Put it back" in body
    assert 'action="/pools/proxy/restore"' in body
    assert 'name="raw" value="1.2.3.4:9999:u:p"' in body
    assert 'name="name" value="SX3"' in body
    stuck = body.count("stuck? the pass closes it after two build budgets")
    assert stuck == 1, "#241 has run since the fixture's day; #235 for 5m"
    row235 = body.split('<td class="muted">235</td>')[1].split("</tr>")[0]
    assert "stuck?" not in row235

    rows.extend([dict(rows[-1], id=200 - i) for i in range(48)])
    _, _, body = client.request("GET", "/requests?view=running&mine=1")
    assert "older →" in body and 'href="/requests?view=running&mine=1&page=2"' \
        in body
    assert "page 1 of 1" in body, "the pill count is what is known"


def test_a_csv_cell_that_starts_like_a_formula_opens_as_text():
    """An event's detail or a note is free text the pass wrote; one that
    begins with = + - or @ would run as a formula in a spreadsheet, so
    the export writes it with a quote in front."""
    text = app_mod._events_csv([
        {"at": None, "kind": "stock", "run_id": "", "build": "",
         "serial": "1551", "status": "gmail", "seconds": 12,
         "detail": "=HYPERLINK(\"http://x\")"},
        {"at": None, "kind": "stock", "run_id": "@r1", "build": "",
         "serial": "", "status": "-x", "seconds": None,
         "detail": "24 gmails, added by mehdi"}])
    lines = text.splitlines()
    assert lines[1].endswith(",12,\"'=HYPERLINK(\"\"http://x\"\")\"")
    assert lines[2] == ",stock,'@r1,,,'-x,,\"24 gmails, added by mehdi\""
    text = app_mod._delivered_csv([
        {"address": "+d@x.com", "serial": "1542", "updated_at": None,
         "source": "manual"}])
    assert text.splitlines()[1] == "'+d@x.com,1542,,manual"


def test_no_page_or_export_may_be_cached(web, monkeypatch):
    """A one-time password shows once, a refused form comes back with
    what was typed: no browser or proxy keeps a copy of any page."""
    monkeypatch.setattr(app_mod.read, "delivered_rows", lambda s, q="": [])
    client = web()
    client.login()
    for path in ("/", "/pools/gpt/delivered.csv"):
        status, headers, _ = client.request("GET", path)
        assert status == 200
        assert dict(headers)["Cache-Control"] == "no-store", path


def test_a_day_that_is_not_a_date_is_said_so(caplog):
    import logging

    with caplog.at_level(logging.DEBUG, logger="geelark_farm.web.read"):
        assert app_mod.read.day_bounds(object(), "not-a-date") is None
    assert "'not-a-date' is not a day" in caplog.text


def test_every_query_that_calls_a_row_free_says_it_is_still_on_the_sheet():
    """The bug this closes was a reading, not a write.

    The mirror never deletes - a row that leaves a tab keeps its history,
    which is what makes "what did we build on Tuesday" answerable. So
    `status = '' AND error IS NULL` counts every account ever seen whose
    status happened to be blank when it was removed. On 2026-09-05 the
    Gmails tab held six rows and none free while the front page said
    nineteen, and the alert strip beside it said nothing could be built.

    A sweep rather than a list, because the next query to count free stock
    is written by somebody who never read this docstring.
    """
    from pathlib import Path
    src = (Path(app_mod.__file__).parent / "read.py").read_text(
        encoding="utf-8")

    # Each `store._rows(...)` call, as one string: the SQL is written as
    # adjacent literals, so a predicate and its table can be lines apart.
    calls, depth, buf = [], 0, ""
    for chunk in src.split("store._rows(")[1:]:
        depth, buf = 1, ""
        for char in chunk:
            depth += (char == "(") - (char == ")")
            if not depth:
                break
            buf += char
        calls.append(" ".join(buf.split()))

    assert calls, "the sweep found no queries at all, so it proves nothing"
    guilty = [q for q in calls if "on_sheet" in q]
    assert not guilty, (
        "these read the retired sheet flag, which nothing has written since "
        "POOLS_IN_PG went on - a frozen gate, not a gate: "
        + " | ".join(q[:90] for q in guilty))


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_an_operator_can_see_which_accounts_stopped(web, monkeypatch):
    """The gap this closes was one I put there.

    The count of stopped accounts sat behind a link to Needs attention,
    which is an admin page - so the one thing on the dashboard an operator
    has to act on rather than watch was the one thing they could not see.
    The list is small and it is theirs, so it lives where they already are.
    """
    # The panel went (2026-09-05, "we still see attention here"). The rows
    # it listed are in their pool's manager, wearing their own word, with a
    # chip that shows only them - beside the rows they are judged against.
    _dash(monkeypatch, pool_rows={
        "gmail": [{"id": 1, "address": "rhea@example.com",
                   "status": "phone_verification_required", "seller": "",
                   "serial": "", "note": "", "error": None,
                   "state": "phone_verification_required"}],
        "gpt": [{"id": 2, "address": "hollis@example.com",
                 "status": "no_code_source", "serial": "1512", "note": "",
                 "error": None, "state": "no_code_source"}],
        "proxy": []})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    assert "Needs a decision" not in body

    ov = _sheets(client)
    assert "rhea@example.com" in ov and "hollis@example.com" in ov
    assert 'class="badge attn">phone_verification_required' in ov
    assert "1512" in ov, "and which phone it was on"


def test_the_stopped_card_is_absent_when_nothing_stopped(web, monkeypatch):
    """On a good day it is empty, and an empty panel saying "nothing
    stopped" is a line of noise on a page that is about the phones."""
    _dash(monkeypatch, stopped=[])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    assert "Needs a decision" not in body


def test_the_dashboards_one_script_sends_only_the_pages_own_forms(
        web, monkeypatch):
    """The console has no script anywhere else, and this is the exception.

    What it may do: arrange what is on the page, and send the page's own
    forms without leaving it - the same action, the same fields, the same
    HTML back, with only <main> swapped so the manager stays open and the
    scroll stays put (the operator, 2026-09-05: every button reloaded the
    page even when nothing needed it). What it may not do: invent a
    request. Every fetch here is `form.action` or the page itself, and
    nothing is built from markup strings.
    """
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    script = assets.JS
    for forbidden in ("XMLHttpRequest", "innerHTML", "document.write",
                      "action ="):
        assert forbidden not in script, forbidden
    # Three, and only these: the page's own forms, the page itself, and
    # the phone link a serial is - the drawer fetches what the link would
    # have opened.
    calls = re.findall(r"fetch\(([^,)]+)", script)
    # `/clienterror` is the script saying it broke, and it is the one
    # address here that no form on the page declares. It carries the
    # page's own csrf token, it is answered with 204 and nothing else,
    # and without it the console has no way at all to report a throw
    # (2026-09-20).
    # And `/pools/<kind>/sheet`, which is the manager's own drawer: it
    # left the dashboard on 2026-09-21 and is fetched when a door is
    # pressed. The door is still a form that goes to the pool's page
    # without the script.
    # And `door`: the editor's one-row credentials, `/pools/<kind>/
    # credentials?address=`, fetched when Edit is pressed (2026-09-21).
    assert calls and all(c.strip() in ("'/clienterror'",
                                       "'/pools/' + kind + '/sheet'",
                                       "door",
                                       "form.action",
                                       "location.pathname + location.search",
                                       "href")
                         for c in calls), calls
    # The one real submit is the fallback when the network fails.
    assert script.count(".submit(") == 1
    assert "addEventListener('submit'" in script
    # The three views are hidden until the script shows them: buttons
    # that do nothing are worse than none.
    assert 'id="seg" role="group" aria-label="Show" hidden' in body
    assert 'id="phones"' in body and 'id="nohits"' in body

def test_every_page_a_person_sees_carries_the_one_script(web, monkeypatch):
    """It was the dashboard's alone, and "if a second page ever needs one,
    that is a decision somebody makes on purpose". Made on 2026-09-14:
    Requests, Events and Logs wore the same breathing "live" dot and the
    same refresh meta, and the meta sits inside <noscript> on purpose -
    so in any browser with scripts on they never refreshed at all. The
    pool pages promised "the page shows the answer" with nothing on them
    to show it. One script, the same one, on every page a signed-in
    person sees; and one only - the dashboard must not get it twice."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    _c8_reads(monkeypatch)
    _gmail_active(monkeypatch)
    _proxy_pool(monkeypatch)
    _gpt_active(monkeypatch)
    monkeypatch.setattr(actions_mod, "listing", lambda s, **k: [])
    monkeypatch.setattr(app_mod.read, "events_rows", lambda s, **k: [])
    monkeypatch.setattr(app_mod.read, "logs", lambda s, **k: {
        "rows": [], "more": False, "today": 0, "loggers": []})
    client = web()
    client.login()
    # The script is on every page a signed-in person sees - once, as a
    # link under its own hash, rather than 76KB of source in the body.
    for path in ("/", "/pools/gmail", "/pools/proxy", "/pools/gpt",
                 "/phones", "/requests", "/events", "/logs"):
        status, _, body = client.request("GET", path)
        assert status == 200, path
        assert body.count(f'<script src="{assets.JS_PATH}"') == 1, path
        assert "function swapMain(doc)" not in body, path

    # Listening is a different question, and the answer is no unless the
    # page says otherwise. It used to be yes unless the page said
    # otherwise, so a pool page redrew itself under a half-filled edit
    # form because nobody had thought to turn it off there (the
    # operator, 2026-09-20).
    moves = {"/": "farm", "/requests": "farm", "/events": "farm",
             "/logs": "logs"}
    for path in ("/", "/pools/gmail", "/pools/proxy", "/pools/gpt",
                 "/phones", "/requests", "/events", "/logs"):
        _, _, body = client.request("GET", path)
        want = moves.get(path)
        if want:
            assert f'<meta name="gf-live" content="{want}">' in body, path
        else:
            assert 'name="gf-live"' not in body, (
                f"{path} moves under whoever is using it, for no reason")
    # And the dot means it now: Requests is live with nothing pending,
    # Events no longer claims a thirty-second refresh it never did.
    _, _, requests = client.request("GET", "/requests")
    assert 'class="live">live' in requests
    _, _, events = client.request("GET", "/events")
    assert "refreshes every 30s" not in events
    _, _, logs = client.request("GET", "/logs")
    assert 'class="live">live' in events and 'class="live">live' in logs


def test_the_script_listens_only_where_the_page_says_to():
    """The Boot tab reloads itself whole and must not also swap; the
    store-down page has no user and so no script - it reloads itself the
    way Boot does, since its <noscript> meta never fired in a browser."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "document.querySelector('meta[name=\"gf-live\"]')" in script
    assert "if (!which || !which.content) return;" in script
    assert "which.content === 'logs' ? '/live?logs=1'" in script
    user = {"id": 1, "username": "test", "role": "operator", "csrf": "c"}
    boot = pages.live_page("1862", user, said="queued:70", row={})
    assert "gf-live" not in boot
    assert "addEventListener('submit'" not in boot
    down = pages.store_down_page(None)
    assert "setTimeout(function(){ location.reload(); }, 30000)" in down
    assert "gf-live" not in down and "addEventListener('submit'" not in down
    # Here is the page this is, not the dashboard: on Requests a Retry
    # answers with Requests.
    assert ("new URL(url, location.href).pathname === location.pathname"
            in script)


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_building_by_hand_asks_for_what_was_chosen(web, monkeypatch):
    """The form's default is the farm's own behaviour: everything blank is
    exactly the phone the keeper would have built next, and every field is
    a departure from it."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch, choose={
        "gmails": [{"label": "pick@example.com"}],
        "proxies": [{"label": "SX9"}],
        "apps": [{"label": "gpt@example.com"}]})
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 88)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert 'action="/phones/build"' in body
    assert "pick@example.com" in body, "a free Gmail is offered in the dialog"
    assert "gpt@example.com" in body, "a free account too"
    assert "SX9" not in body, "the exit is the build's business now"
    assert 'name="proxy_name"' not in body
    assert "auto &mdash; the next free one" in body, "blank means the pool decides"
    assert 'value="none">none &mdash; no Google account' in body
    assert 'select name="app"' not in body, "which app is not a choice"
    assert "ChatGPT, Spotify and Claude already on it" in body

    status, headers, _ = client.request(
        "POST", "/phones/build",
        _form(csrf=client.csrf(), gmail="pick@example.com",
              proxy_name="SX9", account_kind="chatgpt:",
              app_account="gpt@example.com"))

    assert status == 303 and dict(headers)["Location"].startswith("/")
    assert got["verb"] == "build_by_hand"
    assert got["payload"]["gmail"] == "pick@example.com"
    assert got["payload"]["no_gmail"] is False
    assert got["payload"]["proxy_name"] == "SX9", "the API may still name one"
    assert got["payload"]["app"] == "chatgpt"
    assert got["payload"]["install_app"] is True
    assert got["payload"]["app_account"] == "gpt@example.com"

    # No account: the phone comes up warm, with all three apps on it and
    # nothing signed into any of them. The kind box is what says so now,
    # and empty is its first option.
    client.request("POST", "/phones/build", _form(csrf=client.csrf()))
    assert got["payload"]["app"] == ""
    assert got["payload"]["install_app"] is False
    assert got["payload"]["app_account"] == ""

    # An `app` named in the form is still ignored - the card has no such
    # box, and a phone carries all three whatever anybody posts. What
    # decides the account is the kind box, and without it there is no
    # account to name.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), app="spotify",
                         app_account="gpt@example.com"))
    assert got["payload"]["app"] == ""
    assert got["payload"]["app_account"] == ""

    # Named properly, the same account goes on as a Spotify one - on
    # the one phone a new build may carry it on, a bare one.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="none",
                         account_kind="spotify:normal",
                         app_account="s@example.com"))
    assert got["payload"]["app"] == "spotify"
    assert got["payload"]["app_category"] == "normal"
    assert got["payload"]["app_account"] == "s@example.com"

    # No Gmail: a bare phone, signed in nowhere, whatever the other boxes
    # carried. It still comes with the three apps - that is the builder's
    # business, not the card's.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="none",
                         app_account="gpt@example.com"))
    assert got["payload"]["no_gmail"] is True
    assert got["payload"]["gmail"] == "" and got["payload"]["app"] == ""
    assert got["payload"]["app_account"] == ""
    assert got["payload"]["gmail_typed"] is False


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_whether_an_address_is_new_is_decided_here_not_asked_for(
        web, monkeypatch):
    """There were two boxes - a picker and a typed one - and a sentence
    saying which won. Typing an address the pool already had meant "add
    it again", silently. One box now, and this end asks the pool.
    """
    import geelark_farm.store.actions as actions_mod
    import geelark_farm.web.read as read_mod

    _dash(monkeypatch)
    monkeypatch.setattr(read_mod, "known",
                        lambda s, kind: {"picked@example.com": "free"})
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 89)
    client = web()
    client.login()

    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="picked@example.com",
                         install_app="1"))
    assert got["payload"]["gmail"] == "picked@example.com"
    assert got["payload"]["gmail_typed"] is False, "the pool has this one"

    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="Bought.Today@example.com",
                         gmail_password="pw", install_app="1"))
    assert got["payload"]["gmail"] == "Bought.Today@example.com"
    assert got["payload"]["gmail_typed"] is True, "the pool has never seen it"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_store_that_cannot_answer_does_not_add_a_duplicate(web, monkeypatch):
    """Read as "we have it": building on a row that exists is the
    ordinary case, and a duplicate is the one that costs something."""
    import geelark_farm.store.actions as actions_mod
    import geelark_farm.web.read as read_mod

    _dash(monkeypatch)

    def angry(settings, kind):
        raise RuntimeError("no route to host")

    monkeypatch.setattr(read_mod, "known", angry)
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 90)
    client = web()
    client.login()

    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="a@example.com",
                         install_app="1"))
    assert got["payload"]["gmail_typed"] is False


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_one_box_per_credential_and_the_free_rows_drop_down(web, monkeypatch):
    """A picker and a box for the same answer were two controls and a
    sentence about which one won. A datalist is one control that does
    both."""
    from geelark_farm.web import pages

    _dash(monkeypatch, choose={"gmails": [{"label": "a@x.com"}],
                               "proxies": [{"label": "SX1"}],
                               "apps": [{"label": "g@x.com"}]})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    # Two choices, each a select - the exit is the build's own business,
    # and so is which app (the operator, 2026-09-12): the Gmail (auto,
    # none, or "choose...", which opens a dialog to type one or pick a
    # free one) and the account.
    card = body[body.index('class="byhand"'):body.index("Build</button>")]
    for name in ("gmail", "account_kind", "app_account"):
        assert f'<select name="{name}"' in card, name
    assert '<select name="app"' not in card, "every phone carries all three"
    assert 'name="proxy_name"' not in card, "an exit is never chosen here"
    assert 'name="gmail" data-new="gmail-new"' in card
    assert 'name="app_account" data-new="account-new"' in card
    assert card.count("choose&hellip;</option>") == 2
    assert "type a new one" not in card
    assert '<option value="">auto &mdash; the next free one (1 free)</option>' in card
    assert '<option value="none">none &mdash; no Google account</option>' in card
    # "none" moved to the kind box, which is the question it answers.
    assert 'data-bare="1" data-gmail="1">none &mdash; sign in later' in card
    assert 'value="spotify:normal" data-bare="1">' in card, (
        "the one kind a bare phone may carry, and only a bare one")
    assert 'value="chatgpt:eco"' in card
    # Offered on neither since 2026-09-24: a new build never signed an
    # error account in, and its row's Send is the door.
    assert 'value="spotify:error"' not in card
    assert 'name="install_app"' not in card, "the tick is long gone"
    assert '<optgroup label="pick one">' not in card, (
        "the free rows moved into the dialog")
    assert "Exit:" not in body, "nothing about the exit at all (the operator)"
    for ident in ("gmail-new", "account-new"):
        assert f'<dialog class="editor" id="{ident}"' in body, ident
    # The dialog: the boxes, then the free rows to pick from.
    gdlg = body[body.index('id="gmail-new"'):body.index('id="account-new"')]
    assert 'data-field="gmail_secret"' in gdlg
    assert "Authenticator key" in gdlg and "empty = the account has none" in gdlg
    assert '<input type="radio" name="pick-gmail-new" value="a@x.com"> a@x.com' in gdlg
    adlg = body[body.index('id="account-new"'):]
    assert 'data-field="app_secret"' in adlg
    assert ('<input type="radio" name="pick-account-new" value="g@x.com"> '
            'g@x.com') in adlg
    for name in ("gmail_password", "gmail_secret", "app_password", "app_secret"):
        assert f'<input type="hidden" name="{name}" value="">' in body, name
    script = pages._DASH_SCRIPT
    assert "gmailPick.value === 'none'" in script, "no Gmail: one kind only"
    assert "(bare ? o.dataset.bare : o.dataset.gmail) === '1'" in script, (
        "each kind is offered on the one phone it belongs on")
    assert "acctPick.disabled = !kind" in script
    assert 'select[name="app"]' not in script, "there is no App box to gate"
    assert "function openNew(pick, was)" in script
    assert "' (from the pool)'" in script, "a picked row says where it came from"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_bare_phone_carries_no_account_however_the_boxes_were_left(
        web, monkeypatch):
    """No Google account means nothing to sign an app account into, so
    one named alongside would be spent on a phone with nowhere to put it.
    The apps themselves still go on - that is not the card's question any
    more (the operator, 2026-09-12)."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 90)
    client = web()
    client.login()

    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="none",
                         app_account="gpt@example.com"))

    assert got["payload"]["no_gmail"] is True
    assert got["payload"]["install_app"] is False
    assert got["payload"]["app_account"] == ""

    # And with a Gmail, the account is exactly what was chosen.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="a@example.com",
                         account_kind="chatgpt:",
                         app_account="gpt@example.com"))
    assert got["payload"]["install_app"] is True
    assert got["payload"]["app_account"] == "gpt@example.com"

    # The one account a bare phone may carry is a `normal` Spotify one:
    # it wants a phone with no Google account, which is the only reason
    # to ask for one with an account on it (2026-09-17).
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="none",
                         account_kind="spotify:normal",
                         app_account="s@example.com"))
    assert got["payload"]["no_gmail"] is True
    assert got["payload"]["app"] == "spotify"
    assert got["payload"]["app_category"] == "normal"
    assert got["payload"]["app_account"] == "s@example.com"

    # Any other kind alongside "no Gmail" is refused rather than quietly
    # dropped - the page does not offer it, and the route is not the
    # page.
    before = dict(got)
    status, _, _ = client.request(
        "POST", "/phones/build",
        _form(csrf=client.csrf(), gmail="none", account_kind="chatgpt:eco",
              app_account="e@example.com"))
    assert status == 303
    assert got == before, "nothing was asked for"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_build_form_is_absent_with_nothing_to_build_from(web, monkeypatch):
    """A form that can only be refused is worse than a sentence saying
    why."""
    _dash(monkeypatch, stock={"gmail": {"free": 0}, "proxy": {"free": 9},
                              "app": {"awaiting": 2}})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    # An empty Gmail pool is the case the form is for - an address bought
    # this morning is in no pool - so the form stays and the hint says so.
    assert 'action="/phones/build"' in body
    assert '<option value="" disabled>auto &mdash; the pool is empty</option>' in body
    assert ('<option value="none" selected>none &mdash; no Google account'
            '</option>') in body
    assert "The pool has nothing free - type one above." in body

    _dash(monkeypatch, stock={"gmail": {"free": 3}, "proxy": {"free": 0},
                              "app": {"awaiting": 2}})
    _, _, body = client.request("GET", "/")
    assert 'action="/phones/build"' not in body
    assert "There is no free exit to build with" in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_adding_stock_opens_on_the_dashboard_and_comes_back_to_it(
        web, monkeypatch):
    """The pool tabs went with the rail, so `+ add` cannot be a link to one
    any more. It opens the manager on the page an operator has, and that
    posts to the same preview the tab always posted to - only the door
    moved."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    # Four pools now: the Spotify accounts joined the three (2026-09-17).
    # In the sheets, each fetched when its door is pressed - the paste
    # box left the dashboard with the rest of the manager (2026-09-21).
    sheets = _sheets(client)
    assert sheets.count('class="addbox"') == 4, "every pool (2026-09-08)"
    assert 'action="/pools/gmail/preview"' in sheets
    assert 'action="/pools/gpt/preview"' in sheets
    # The card carries the button and the manager carries the form, so a
    # card whose button opens nothing is the one thing to refuse.
    assert 'data-pool="gmail">Manage</button>' in body
    assert "Manage all" not in body, "one door per card, not two"
    assert '<div class="ov" id="poolov"' in body
    # And where it returns to, so confirming does not land on a page the
    # person who pressed it may not have.
    assert 'name="back" value="/"' in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_add_door_needs_the_permission(web, monkeypatch):
    """A door somebody may not walk through is not drawn for them."""
    _dash(monkeypatch)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "all", "may_add_gmail": True,
                         "may_add_gpt": False})
    client = web()
    client.login(username="narrow")
    sheets = _sheets(client)

    assert 'action="/pools/gmail/preview"' in sheets
    assert 'action="/pools/gpt/preview"' not in sheets


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_stock_command_answers_in_the_request_that_asked(web, monkeypatch):
    """Stage 2, end to end. The row is still written - the Requests page is
    the record of what was asked and by whom - but the work happens here
    and the banner says it is already in, not that it is queued."""
    import geelark_farm.runner as runner_mod
    import geelark_farm.store.actions as actions_mod

    _gmail_active(monkeypatch)
    enqueued, settled = {}, {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: enqueued.update(k) or 501)
    monkeypatch.setattr(actions_mod, "settle",
                        lambda s, i, **k: settled.update(dict(k, id=i)))
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    # The web claims the row before it works it, which is what
    # every other writer in the system does (2026-09-21).
    monkeypatch.setattr(actions_mod, "claim", lambda s, i: True)
    monkeypatch.setattr(runner_mod, "run_now",
                        lambda s, verb, payload: ("done", "1 gmail added",
                                                  {"added": ["a@x.com"]}))
    client = web()
    client.login()

    status, headers, _ = client.request(
        "POST", "/pools/gmail/add",
        _form(csrf=client.csrf(), rows="a@x.com\tpw", seller="Nima"))

    assert status == 303
    assert dict(headers)["Location"].endswith("said=done:501")
    assert enqueued["verb"] == "add_gmails", "still written down"
    assert settled["status"] == "done" and settled["id"] == 501


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_command_the_runner_refuses_still_goes_to_the_queue(web,
                                                              monkeypatch):
    """The fallback is the queue, which is where everything was before
    this existed - so the worst the instant path can do is be no faster."""
    import geelark_farm.runner as runner_mod
    import geelark_farm.store.actions as actions_mod

    _gmail_active(monkeypatch)
    monkeypatch.setattr(actions_mod, "enqueue", lambda s, **k: 502)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    monkeypatch.setattr(runner_mod, "run_now", lambda s, verb, payload: None)
    client = web()
    client.login()

    _, headers, _ = client.request(
        "POST", "/pools/gmail/add",
        _form(csrf=client.csrf(), rows="a@x.com\tpw", seller="Nima"))

    assert dict(headers)["Location"].endswith("said=queued:502")


# --------------------------------------------- the pool manager (2026-09-05)
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_each_pool_card_lists_what_its_number_counts(web, monkeypatch):
    """The count answers "how many" and was the whole card. The list under
    it answers the question a person actually had next - which ones - and
    that was two pages away."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    card = body[body.index('<section class="pool"'):]
    assert "free@gmail.com" in card, "the free row is under its own count"
    assert 'class="queue"' in card
    # And only the free ones: a row on a phone is not stock, and a card
    # that lists it is a card that promises what it cannot hand over.
    queue = card[card.index('class="queue"'):card.index("</ul>")]
    assert "busy@gmail.com" not in queue


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_manager_holds_every_row_and_opens_shut(web, monkeypatch):
    """Each sheet is fetched when its door is pressed, and holds every
    row of that pool - not only the free ones the card lists.

    They used to ride in the response, all three, shut: 925,488 of the
    dashboard's 1,012,694 bytes for the 99 presses in 100 that never
    happen (2026-09-21)."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    ov = _sheets(client)
    for kind in ("gmail", "gpt", "proxy"):
        assert f'data-sheet="{kind}" hidden' in ov, kind
    # The held row the card would not list is here, because this is the
    # working list rather than the shelf.
    assert "busy@gmail.com" in ov
    assert 'id="poolov" hidden' in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_both_account_rows_carry_both_doors_and_a_proxy_row_carries_none(
        web, monkeypatch):
    """Only the endpoints that exist are drawn. A button that leads
    nowhere is worse than no button."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    ov = _sheets(client)
    gmail = ov[ov.index('data-sheet="gmail"'):ov.index('data-sheet="gpt"')]
    assert 'action="/pools/gmail/edit"' in gmail
    assert 'action="/pools/gmail/remove"' in gmail
    gpt = ov[ov.index('data-sheet="gpt"'):ov.index('data-sheet="proxy"')]
    assert 'action="/pools/gpt/edit"' in gpt
    assert 'action="/pools/gpt/remove"' in gpt
    assert 'name="seller"' not in gpt, "a GPT row has no seller to edit"
    proxy = ov[ov.index('data-sheet="proxy"'):]
    # The proxy rows carry Test and Remove, and name the row by `name`,
    # which is what the proxy routes read (2026-09-08).
    assert 'action="/pools/proxy/test"' in proxy
    assert 'action="/pools/proxy/remove"' in proxy
    assert 'name="name" value="SX7"' in proxy
    assert 'name="address"' not in proxy.split("<tbody", 1)[1]


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_doors_need_the_same_permission_adding_does(web, monkeypatch):
    """Being able to add a row and not fix a typo in it was the odd half,
    so both doors ride on `may_add_gmail` - and without it, neither is
    drawn."""
    _dash(monkeypatch)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "all", "may_add_gmail": False})
    client = web()
    client.login(username="narrow")
    _, _, body = client.request("GET", "/")

    assert 'action="/pools/gmail/edit"' not in body
    assert 'action="/pools/gmail/remove"' not in body
    # The rows are still listed: seeing the pool is not changing it.
    assert "free@gmail.com" in body


def test_the_manager_reads_spent_rows_under_a_cap_of_their_own():
    """A `used` Gmail and a `delivered` account were left out as "the
    archive" until the operator asked for the pool in three views, spent
    among them (2026-09-08). They come under their own cap, so a thousand
    used Gmails can never push the batch pasted a minute ago off the
    bottom of the live list."""
    import inspect

    from geelark_farm.web import read

    # The queries are module constants now, shared with the one-row read
    # (2026-09-21); the sheet's function only formats them.
    assert "status {op} 'used'" in read._GMAIL_Q
    assert "status {op} 'delivered'" in read._GPT_Q
    body = inspect.getsource(read._pool_rows)
    # Three: the Gmails, the GPT accounts and the Spotify ones, which
    # share the app table and are told apart by `product` (2026-09-17).
    assert body.count('format(op="<>", more="")') == 3, "live rows, read apart"
    assert body.count('format(op="=", more="")') == 3, "spent rows, read apart"
    assert "on_sheet" not in body, "the sheet flag is retired"
    # What the editor opens with is read by its own door now, one row at
    # a time; the sheet's queries carry no credential (2026-09-21).
    assert "AS password" in read._CREDS and "AS secret" in read._CREDS
    assert "AS password" not in read._GMAIL_Q and "AS password" not in read._GPT_Q


# ---------------------------------------------- the contract, slice B
@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_the_manager_carries_the_chooser_and_the_drawer_shut(web, monkeypatch):
    """Which phone an account goes to is chosen, not taken from the top;
    and a serial opens the phone's page here rather than leaving. Both
    ride in the one response, shut, like the pool sheets."""
    _dash(monkeypatch, phones=[
        {"serial": "1500", "status": "app_only", "state": "", "gmail": "",
         "app_account": "", "proxy_name": "SX27"},
        {"serial": "1501", "status": "app_only", "state": "taken",
         "owner": "ali", "app_account": "", "proxy_name": "SX1"},
        {"serial": "1502", "status": "ready", "state": "",
         "app_account": "h@x.com", "proxy_name": "SX2"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    send = body[body.index('data-sheet="send" hidden'):]
    send = send[:send.index("</section>")]
    assert 'name="serial" value="1500"' in send, "warm and nobody's"
    assert 'value="1501"' not in send, "somebody holds it"
    assert 'value="1502"' not in send, "it already has an account"
    assert 'data-sheet="phone" hidden' in body
    assert "[data-drawer]" in body or 'data-drawer' in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_editor_offers_the_rows_status(web, monkeypatch):
    """free, set aside, or the word it has - and greyed when a phone is
    behind the row, because the phone decides that one."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    ov = _sheets(client)
    gmail = ov[ov.index('data-sheet="gmail"'):ov.index('data-sheet="gpt"')]
    assert gmail.count('<dialog class="editor"') == 1, "one per sheet"
    editor = gmail[gmail.index('<dialog class="editor"'):]
    assert '<select name="state"><option value="free">free</option>' in editor
    assert '<option value="set aside">set aside</option>' in editor
    assert 'name="csrf"' in editor, "the pool tab's own form, token and all"

    def row(address):
        at = gmail.index(f"<td>{address}</td>")
        return gmail[gmail.rfind("<tr", 0, at):at]

    assert 'data-state="free"' in row("free@gmail.com")
    assert 'data-state="on a phone"' in row("busy@gmail.com")
    # The editor reads these for its one row when Edit is pressed; they
    # no longer ride in every row of the sheet (2026-09-21).
    assert 'data-password' not in row("free@gmail.com")
    assert 'data-secret' not in row("free@gmail.com")
    assert "Kx82!mnQ" not in row("free@gmail.com")
    assert 'data-sellername="dalir"' in row("free@gmail.com")
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "f.state.add(new Option(word, word))" in script, "the row's own word"
    assert "f.state.disabled = state === 'on a phone'" in script


@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_a_chosen_phone_rides_in_the_login_request(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue", lambda s, **k: got.update(k) or 5)
    client = web()
    client.login()
    client.request("POST", "/accounts/login",
                   _form(csrf=client.csrf(), addresses="a@x.com", serial="1500"))
    assert got.get("payload", {}).get("serial") == "1500"


def test_the_browsers_own_refresh_lives_inside_noscript(web, monkeypatch):
    """A meta refresh fires once parsed, whether or not the script later
    removes the tag - and it fired under an open manager and wiped the
    paste in it (2026-09-05). So the browser's refresh is for browsers
    without the script only, and the script reads the interval off a
    plain meta and refreshes by a quiet swap that waits for a quiet
    moment."""
    _dash(monkeypatch, phones=[{"serial": "1503", "status": "building",
                                "state": ""}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    head = body[:body.index("<body")]
    assert '<noscript><meta http-equiv="refresh" content="10"></noscript>' in head
    assert '<meta name="gf-refresh" content="10">' in head
    # Never a live one: the script cannot cancel it once it is parsed.
    assert head.count('http-equiv="refresh"') == 1
    script = assets.JS
    assert "meta[name=\"gf-refresh\"]" in script
    assert "reloadWhenSettled" in script


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_building_row_can_be_called_off(web, monkeypatch):
    """The one thing to do to a build under way: Cancel, which is the
    "stop this one" door on the row it is about. The job gives up at its
    next step and puts back what it held."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch, phones=[{"serial": "1503", "status": "building",
                                "state": ""}])
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue", lambda s, **k: got.update(k) or 9)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    row = body[body.index('href="/phones/1503"'):]
    row = row[:row.index("</tr>")]
    assert 'action="/phones/1503/stop"' in row and ">Cancel<" in row
    assert 'colspan="4"' in row, "the progress line leaves room for it"

    status, headers, _ = client.request(
        "POST", "/phones/1503/stop", _form(csrf=client.csrf(), back="/"))
    assert got["verb"] == "stop_phone" and got["payload"]["serial"] == "1503"
    assert status == 303 and dict(headers)["Location"].startswith("/?said=")


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_remove_can_be_undone_from_the_toast(web, monkeypatch):
    """A remove says "removed" and names the pool; the toast for it carries
    Undo, and Undo puts the row back from what the request kept - through
    the pool's own add verb, judged the way a paste is."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    got = []
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.append(k) or 41)
    monkeypatch.setattr("geelark_farm.verbs.runs_inline", lambda v: False)
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/pools/gmail/remove",
        _form(csrf=client.csrf(), address="free@gmail.com", sure="1",
              back="/"))
    assert status == 303
    # Queued rather than run here (runs_inline is off), so "queued"; the
    # word only changes when the verb answered in the request.
    assert dict(headers)["Location"].startswith("/?said=queued:41")

    _, _, body = client.request("GET", "/?said=removed-gmail:41")
    toast = body[body.index('class="said toast undo"'):]
    toast = toast[:toast.index("</p>")]
    assert 'action="/pools/gmail/undo"' in toast
    assert 'name="req" value="41"' in toast and ">Undo<" in toast

    monkeypatch.setattr(actions_mod, "one", lambda s, i: {
        "id": i, "verb": "remove_gmail", "status": "done", "result": "",
        "requested_by": 7,
        "detail": {"removed": {"Address": "free@gmail.com", "Password": "pw",
                               "Secret": "back@x.com", "Seller": "dalir",
                               "Purchase Date": "2026-09-01"}}})
    status, headers, _ = client.request(
        "POST", "/pools/gmail/undo", _form(csrf=client.csrf(), req="41"))
    assert status == 303
    assert got[-1]["verb"] == "add_gmails"
    row = got[-1]["payload"]["rows"][0]
    assert row["address"] == "free@gmail.com" and row["password"] == "pw"
    assert row["recovery"] == "back@x.com" and row["secret"] == ""
    assert got[-1]["payload"]["seller"] == "dalir"
    assert got[-1]["idem_key"].startswith("undo-41:")


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_undo_puts_back_only_what_a_remove_kept(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: pytest.fail("nothing to put back"))
    monkeypatch.setattr(actions_mod, "one", lambda s, i: {
        "id": i, "verb": "edit_gmail", "status": "done", "result": "",
        "requested_by": 7, "detail": {"changed": ["Seller"]}})
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/pools/gmail/undo", _form(csrf=client.csrf(), req="41"))
    assert status == 303 and dict(headers)["Location"] == "/?said=gone"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_removed_spotify_row_comes_back_as_a_spotify_row(web, monkeypatch):
    """The toast's Undo went through the GPT door, which re-added the row
    with `add_gpt` - no product, no category - so a removed Spotify
    account came back as a GPT one (2026-09-17). Its own word, its own
    door, and the GPT door refuses what came out of the other pool."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    got = []
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.append(k) or 52)
    monkeypatch.setattr("geelark_farm.verbs.runs_inline", lambda v: False)
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/pools/spotify/remove",
        _form(csrf=client.csrf(), address="nova@x.com", sure="1", back="/"))
    assert status == 303
    assert dict(headers)["Location"].startswith("/?said=queued:52")
    assert got[-1]["verb"] == "remove_app"

    _, _, body = client.request("GET", "/?said=removed-spotify:52")
    toast = body[body.index('class="said toast undo"'):]
    toast = toast[:toast.index("</p>")]
    assert "out of the Spotify pool" in toast
    assert 'action="/pools/spotify/undo"' in toast

    kept = {"Address": "nova@x.com", "Password": "pw", "2FA Secret": "",
            "Email code": "FALSE", "Product": "spotify", "Category": "error"}
    monkeypatch.setattr(actions_mod, "one", lambda s, i: {
        "id": i, "verb": "remove_app", "status": "done", "result": "",
        "requested_by": 7, "detail": {"removed": kept}})
    status, headers, _ = client.request(
        "POST", "/pools/spotify/undo", _form(csrf=client.csrf(), req="52"))
    assert status == 303
    assert got[-1]["verb"] == "add_spotify"
    assert got[-1]["payload"]["category"] == "error"
    assert got[-1]["payload"]["rows"] == [{"address": "nova@x.com",
                                           "password": "pw"}]
    assert got[-1]["idem_key"].startswith("undo-52:")

    # What came out of one pool goes back into that pool only.
    status, headers, _ = client.request(
        "POST", "/pools/gpt/undo", _form(csrf=client.csrf(), req="52"))
    assert status == 303 and dict(headers)["Location"] == "/?said=gone"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_undo_does_not_put_a_delivered_account_back_as_stock(web,
                                                             monkeypatch):
    """Undo re-adds through the pool's add, which makes a free row: a
    delivered account removed and undone would be offered to the next
    phone. The archive keeps it; the toast says so (2026-09-27)."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: pytest.fail("nothing to put back"))
    kept = {"Address": "done@x.com", "Password": "pw", "Product": "spotify",
            "Category": "normal", "Status": "delivered"}
    monkeypatch.setattr(actions_mod, "one", lambda s, i: {
        "id": i, "verb": "remove_app", "status": "done", "result": "",
        "requested_by": 7, "detail": {"removed": kept}})
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/pools/spotify/undo", _form(csrf=client.csrf(), req="53"))
    assert status == 303
    assert dict(headers)["Location"] == "/?said=kept_delivered"
    _, _, body = client.request("GET", "/?said=kept_delivered")
    assert "archive" in body


def test_the_gpt_pool_page_and_badge_leave_the_spotify_rows_out(monkeypatch):
    """One table, two pools: every read the GPT Pool page and its badge
    make says which product, or the page lists Spotify accounts as GPT
    ones and the badge counts them (2026-09-17)."""
    from geelark_farm.web import read

    seen = []

    class _Store:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def _rows(self, sql, params=()):
            seen.append(sql)
            return [{"waiting": 0, "on_phone": 0, "delivered": 0,
                     "needs_human": 0, "broken": 0, "n": 0}]

    monkeypatch.setattr(read, "Store", _Store)
    for view in ("waiting", "on_phone", "needs_human", "delivered"):
        read.gpt_pool(None, view=view)
    about_apps = [s for s in seen if "kind = 'app'" in s]
    assert about_apps, "the page reads the app pool"
    assert all(read.NOT_SPOTIFY in s for s in about_apps)
    assert "{NOT_SPOTIFY}" in inspect.getsource(read.nav_counts)
    assert read.NOT_SPOTIFY in read._DELIVERED_MATCH


def test_the_dashboard_keeps_itself_current_even_when_idle(web, monkeypatch):
    """A build that starts after the page was opened must show up without
    a hand on F5: ten seconds while building, thirty otherwise."""
    _dash(monkeypatch, phones=[{"serial": "1500", "status": "ready",
                                "state": ""}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert '<meta name="gf-refresh" content="15">' in body
    # And which build drew it, so a deploy reaches an open tab by itself.
    assert 'name="gf-rev" content="' in body
    assert 'meta[name="gf-rev"]' in assets.JS
    # And it is not the empty string any more: the image has no `.git`
    # in it, so `revision()` answered "" and the reload-on-deploy check
    # compared "" with "" for as long as it has existed (2026-09-20).
    assert 'name="gf-rev" content=""' not in body

    _dash(monkeypatch, phones=[{"serial": "1503", "status": "building",
                                "state": ""}])
    _, _, body = client.request("GET", "/")
    assert '<meta name="gf-refresh" content="10">' in body


def test_the_tabs_cross_is_not_an_address(web, monkeypatch):
    """A phone that stopped before sign-in carries the tab's cross in its
    Gmail cell. The cell says what is missing, not the mark."""
    _dash(monkeypatch, phones=[{"serial": "1500", "status": "incomplete",
                                "state": "", "gmail": "✗", "app_account": "✗"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    row = body[body.index('href="/phones/1500"'):]
    row = row[:row.index("</tr>")]
    assert "✗" not in row
    assert "no Gmail on it" in row


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_set_aside_row_carries_free_and_a_free_one_does_not(web, monkeypatch):
    _dash(monkeypatch, pool_rows={
        "gmail": [{"id": 1, "address": "stuck@gmail.com",
                   "status": "captcha_shown", "seller": "", "serial": "",
                   "note": "", "error": None, "state": "captcha_shown"},
                  {"id": 2, "address": "free@gmail.com", "status": "",
                   "seller": "", "serial": "", "note": "", "error": None,
                   "state": "free"},
                  {"id": 3, "address": "busy@gmail.com", "status": "in_use",
                   "seller": "", "serial": "1500", "note": "", "error": None,
                   "state": "on a phone"}],
        "gpt": [], "proxy": []})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    ov = _sheets(client)

    def doors(address):
        start = ov.index(f'<td>{address}</td>')
        return ov[start:ov.index("</tr>", start)]

    assert 'action="/pools/gmail/free"' in doors("stuck@gmail.com")
    assert ">Free<" in doors("stuck@gmail.com")
    assert 'action="/pools/gmail/free"' not in doors("free@gmail.com")
    assert 'action="/pools/gmail/free"' not in doors("busy@gmail.com")


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_preview_shows_every_piece_in_full_and_refuses_what_it_could_not_read(
        web, monkeypatch):
    """A person checking a paste has to see what is about to be written -
    the password, the whole key - and a line with a piece the reader
    could not place is refused with that piece named, not trimmed."""
    _dash(monkeypatch)
    monkeypatch.setattr(app_mod.read, "known", lambda s, kind: {})
    monkeypatch.setattr(app_mod.read, "gmail_sellers", lambda s: [])
    client = web()
    client.login()
    _, _, body = client.request(
        "POST", "/pools/gmail/preview",
        _form(csrf=client.csrf(), back="/",
              pasted="ok@x.com pw1 xpui mde3 bpjl iulh qiuq 6uri bxe3 72wj\n"
                     "odd@x.com pw2 stray"))
    assert "pw1" in body and "XPUIMDE3BPJLIULHQIUQ6URIBXE372WJ" in body
    assert "········" not in body
    assert "not understood: stray" in body
    assert ">Add 1 (skip 1)<" in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_queued_command_rings_the_bell(web, monkeypatch):
    """The row is written either way and the pass finds it either way; the
    bell only decides whether that is in about a second or in thirty."""
    import geelark_farm.store.actions as actions_mod
    from geelark_farm import signals

    _dash(monkeypatch)
    monkeypatch.setattr(actions_mod, "enqueue", lambda s, **k: 81)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    signals.queued.clear()
    client = web()
    client.login()
    status, _, _ = client.request("POST", "/phones/1549/stop",
                                  _form(csrf=client.csrf(), back="/"))
    assert status == 303
    assert signals.queued.is_set(), "nobody rang for a queued command"
    signals.queued.clear()


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_an_operator_can_clear_the_breaker_after_adding_stock(web, monkeypatch):
    """The breaker means "builds keep failing", and the answer to it is
    nearly always fresh stock - which is the one thing an operator is
    trusted to add. They could add a batch of Gmails and then had to find
    an admin before the farm would use them (the operator, 2026-09-07).

    The other three controls stay the admin's: they are about the service,
    not about the stock.
    """
    import geelark_farm.store.actions as actions_mod

    # An operator, which the fixture's user is not: the whole point is what
    # somebody who is not an admin may press.
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 7, "username": "mehdi", "role": "operator",
                         "sees": "own", "may_add_gmail": True})
    seen = []
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: seen.append(k) or 9)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    client = web()
    client.login()
    status, _, _ = client.request(
        "POST", "/service/clear_breaker",
        _form(csrf=client.csrf(), sure="1", back="/"))

    assert status == 303
    assert [k["verb"] for k in seen] == ["control"]
    assert seen[0]["payload"]["what"] == "clear_breaker"

    for shut in ("pause", "stop", "start"):
        seen.clear()
        client.request("POST", f"/service/{shut}",
                       _form(csrf=client.csrf(), sure="1", back="/"))
        assert [k["verb"] for k in seen] == [], f"{shut} is the admin's"


def test_the_editors_cancel_closes_the_editor_and_not_the_manager():
    """It carried the overlay's own `data-shut`, so pressing Cancel threw
    the whole manager away and put the person back on the dashboard -
    three clicks from where they were (the operator, 2026-09-07)."""
    import inspect

    from geelark_farm.web import pages

    body = inspect.getsource(pages._pool_editor)
    assert 'data-close-edit="1"' in body
    assert "data-shut" not in body, "that attribute shuts the whole overlay"
    script = pages._DASH_SCRIPT
    assert "data-close-edit" in script
    assert script.index("data-close-edit") < script.index("[data-shut]"), (
        "the editor's Cancel has to be answered before the overlay's")


def test_a_form_stops_being_busy_when_its_request_ends_well_too():
    """`form.busy button` is pointer-events:none, and the class only came
    off in the `catch`. A form that answered successfully stayed locked,
    so after a preview and a Back the Preview button was dead to a real
    click while looking perfectly ordinary (the operator, 2026-09-07)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    kept = script.split(".then(function(got){", 1)[1][:700]
    assert "form.classList.remove('busy')" in kept, (
        "the lock has to come off on the way through, not only on error")


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_refusal_is_not_reported_as_a_success(web, monkeypatch):
    """`said_word` is what the press was FOR, not what it did. The work
    runs in the request now, so its verdict is known before the redirect -
    and it was thrown away: a refusal, a failure and a success all left as
    one green tick reading "Done". The dashboard's own Build button was
    refused every time somebody left Gmail on "auto" and said "Done - it
    is already in" (the operator, 2026-09-07)."""
    import geelark_farm.store.actions as actions_mod

    monkeypatch.setattr(actions_mod, "enqueue", lambda s, **k: 51)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    # The web claims the row before it works it, which is what
    # every other writer in the system does (2026-09-21).
    monkeypatch.setattr(actions_mod, "claim", lambda s, i: True)
    monkeypatch.setattr(actions_mod, "settle", lambda s, r, **k: None)
    # `_ran_it_now` imports `run_now` inside itself, so the module is what
    # has to answer differently.
    monkeypatch.setattr("geelark_farm.runner.run_now",
                        lambda s, verb, payload: (
                            "refused", "the Gmail x@y is not free", None))
    client = web()
    client.login()
    _, headers, _ = client.request(
        "POST", "/phones/build", _form(csrf=client.csrf(), gmail="", ip=""))

    assert "said=no:51" in dict(headers)["Location"], (
        "a refusal must not wear the word the press was asked for")


def test_the_banner_for_a_refusal_is_not_green_and_says_which_one():
    """The general word cannot name the address or say why, and green with
    a tick is what every press wore whatever it did."""
    from geelark_farm.web import pages

    banner = pages._said("no:51", pages._DASH_SAID, None,
                         "the Gmail x@y is not free")
    assert 'class="said no toast"' in banner
    assert "the Gmail x@y is not free" in banner
    assert ".said.no" in assets.CSS

    plain = pages._said("done:1", pages._DASH_SAID, None)
    assert "said no" not in plain, "a success keeps the tick"


def test_each_phone_press_says_what_that_press_did():
    """All five landed on "Done - it is already in", a sentence written
    for pasted stock and shown after a confirm page about deleting the
    phone (2026-09-07)."""
    from geelark_farm.web import pages

    for state, word in (("taken", "took"), ("unused", "released"),
                        ("done", "closed"), ("failed", "written-off")):
        assert pages.PHONE_STATES[state]["said"] == word
        assert word in pages._DASH_SAID, f"{word} has no sentence"
    assert "cancelled" in pages._DASH_SAID
    assert len({pages._DASH_SAID[w] for w in
                ("took", "released", "closed", "written-off", "cancelled")}) == 5


def test_the_same_address_twice_in_one_paste_is_counted_once():
    """Two overlapping copies of a range both read "ok", the button said
    "Add 2 (skip 0)", and the verb quietly added one - so the number on
    the button was not the number that went in (2026-09-07)."""
    from geelark_farm.web import pages

    rows = [{"address": "a@x.com"}, {"address": "b@x.com"},
            {"address": "A@X.com"}]
    app_mod._Handler._mark_twice(rows)

    assert [r.get("twice") for r in rows] == [False, False, True]
    assert len(pages._good(rows)) == 2
    assert "earlier line" in pages._verdict_badge(rows[2])


def test_a_refusal_is_shown_in_words_the_buyer_can_act_on():
    """The person reading a preview bought these accounts; they did not
    write the validator (2026-09-07)."""
    from geelark_farm.web import pages

    badge = pages._verdict_badge(
        {"error": "totp_secret is not valid base32 (Incorrect padding)"})
    assert "does not look like an authenticator key" in badge
    assert "Incorrect padding" in badge, "the raw words stay on the hover"

    assert "no address on this line" in pages._verdict_badge(
        {"error": "gmail: '' is not an email address"})


def test_the_editor_keeps_what_it_does_not_show(monkeypatch):
    """Neither box shows what it holds, so a blank one has to mean "leave
    it". It meant "clear it": somebody correcting a seller's name saved
    the row and deleted the authenticator key they had paid for, and a
    blank password was not "leave it" but a refusal reading "no password"
    (the operator, 2026-09-07)."""
    from geelark_farm import verbs
    from tests.test_builder import SECRET, make_book

    book = make_book(gmails=1)
    row = book.gmails._rows[0]
    address = row.values["Address"]
    row.values["Password"] = "kept-password"
    row.values[book.gmails.SECRET_COLUMN] = SECRET

    status, said, _ = verbs.edit_gmail(
        book, None, None,
        {"by": "mehdi", "address": address, "new_address": address,
         "password": "", "secret": "", "seller": "newseller"}, None)

    assert status == "done", said
    after = book.gmails.find(address)
    assert after.values["Password"] == "kept-password"
    assert after.values[book.gmails.SECRET_COLUMN] == SECRET
    assert after.values["Seller"] == "newseller"


def test_the_tick_is_how_you_say_you_meant_to_clear_the_key():
    from geelark_farm import verbs
    from tests.test_builder import SECRET, make_book

    book = make_book(gmails=1)
    row = book.gmails._rows[0]
    address = row.values["Address"]
    row.values[book.gmails.SECRET_COLUMN] = SECRET

    status, said, _ = verbs.edit_gmail(
        book, None, None,
        {"by": "mehdi", "address": address, "new_address": address,
         "password": "", "secret": "", "clear_secret": "1"}, None)

    assert status == "done", said
    assert book.gmails.find(address).values[book.gmails.SECRET_COLUMN] == ""


def test_the_editor_shows_what_the_row_holds_and_clears_only_on_purpose():
    """The boxes were blank "for safety", so a person checking whether a
    key was pasted wrong had nothing to check it against (the operator,
    2026-09-08). The dialog is filled from the row; a blank secret still
    leaves it, and the tick is how you mean blank (2026-09-07). The
    values ride on the row, never in what the search reads."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_add_gmail": True, "may_add_gpt": True}
    rows = [{"address": "a@x.com", "seller": "usa", "state": "free"}]
    for kind in ("gmail", "gpt"):
        editor = pages._pool_editor(kind, user, rows)
        assert editor.startswith('<dialog class="editor"')
        assert 'name="password"' in editor and 'type="password"' not in editor
        assert 'name="secret"' in editor and 'placeholder="none"' in editor
        assert 'name="clear_secret"' in editor, "and a way to mean blank"
        assert ('name="seller"' in editor) == (kind == "gmail")
    assert '<option value="usa">' in pages._pool_editor("gmail", user, rows)
    script = pages._DASH_SCRIPT
    # Read for the one row when its Edit is pressed, not off the row's
    # data attributes (2026-09-21).
    assert "f.password.value = creds.password || ''" in script
    assert "f.secret.value = creds.secret || ''" in script
    # The answer decides. It used to close whatever came back - twenty-nine
    # lines before the code that works out whether the save went through -
    # so a refusal threw the typing away and put its reason on the page
    # behind the overlay backdrop (the operator, 2026-09-20).
    order = script.index("var worked = !nothing.test")
    assert script.index("dropEditor(dlg);", order) > order, (
        "the editor closes after the verdict is known, not before it")
    assert "if (!worked) { editorSays(dlg, doc); return; }" in script, (
        "a refusal keeps the editor open and says why inside it")

    table = pages._pool_table(
        "gmail", [dict(rows[0], password="p4ss", secret="JBSWY3DP",
                       second="authenticator", serial="")], user)
    assert 'data-password' not in table and 'data-secret' not in table
    assert "p4ss" not in table and "JBSWY3DP" not in table
    assert "<tr class=\"editrow\"" not in table, "no row under the row"
    found = table.split('data-find="', 1)[1].split('"', 1)[0]
    assert "p4ss" not in found and "JBSWY3DP" not in found


def test_the_gpt_box_stops_promising_none():
    """Blank does not mean none - it spends the next free account. An
    operator saving one for a customer lost it to a box that said
    otherwise (2026-09-07)."""
    from geelark_farm.web import pages

    card = pages._build_card(
        {"choose": {"apps": [{"label": "a@x.com"}]},
         "stock": {"gmail": {"free": 2}, "proxy": {"free": 3}}},
        {"id": 1, "role": "operator", "csrf": "c", "mutations": True,
         "may_login_accounts": True})
    assert '<select name="app_account"' in card
    assert '<select name="account_kind"' in card
    assert 'none &mdash; sign in later' in card, (
        "an account only when one is chosen (2026-09-08)")
    assert pages.NEXT_FREE not in card
    assert 'name="app_secret"' in card, "a typed account needs its key"


def test_a_phone_asked_for_by_hand_is_on_the_page_from_the_moment_it_is_asked():
    """Nothing in the web package read `wanted_builds`, so a press
    vanished: the toast is gone in four seconds, the table does not change
    until a phone exists, and a wish that failed before one did wrote its
    reason into a column nobody could see. `store.wanted.recent`'s own
    docstring calls itself "what the person who asked reads to find out
    whether it happened", and it had no callers at all (2026-09-07)."""
    from geelark_farm.web import pages

    data = {"wishes": [
        {"id": 1, "gmail": "wait@x.com", "status": "queued", "created_at": None,
         "proxy_name": "SX9"},
        {"id": 2, "gmail": "", "status": "running", "created_at": None},
        {"id": 3, "gmail": "", "status": "queued", "created_at": None,
         "no_gmail": True, "app": ""},
        {"id": 4, "gmail": "bad@x.com", "status": "failed", "created_at": None,
         "detail": "bad@x.com - Google refused the password", "serial": "2236",
         "asked_by": "mehdi"},
    ]}
    me = {"id": 1, "username": "mehdi", "role": "operator", "csrf": "c"}
    assert not hasattr(pages, "_wishes"), "the panel is gone (the operator)"
    rows = pages._wish_rows(data, me)
    assert rows.count("<tr") == 4, "every wish is a row of the table"
    assert "wait@x.com" in rows and "waiting for a builder" in rows
    assert "the next free Gmail" in rows, "a wish with no address named"
    assert "a phone is being made for it" in rows
    assert "a bare phone" in rows
    assert '<span class="badge info">Building</span>' in rows
    assert '<span class="badge ">Queued</span>' in rows
    assert pages._wish_rows({"wishes": []}, me) == ""
    # A running wish whose phone is in the table already, Building and
    # built by the same person, is that row and not a second one.
    import datetime as dt

    asked = dt.datetime(2026, 9, 10, 1, 0)
    later = {"wishes": [dict(data["wishes"][1], asked_by="mehdi",
                             created_at=asked)],
             "phones": [{"serial": "2250", "status": "building",
                         "built_by": "mehdi",
                         "created_at": asked + dt.timedelta(minutes=1)}]}
    assert pages._wish_rows(later, me) == ""
    earlier = dict(later, phones=[dict(later["phones"][0],
                                       created_at=asked - dt.timedelta(minutes=1))])
    assert "a phone is being made for it" in pages._wish_rows(earlier, me)
    rows = pages._wish_rows({"wishes": [data["wishes"][3]]}, me)
    assert rows.count("<tr") == 1, "the failed one"
    assert '<span class="badge failed">Failed</span>' in rows
    assert "built by mehdi" in rows
    assert "bad@x.com - Google refused the password" in rows
    assert 'href="/phones/2236"' in rows
    assert 'action="/wishes/4/dismiss"' in rows and "Dismiss" in rows
    assert 'data-view="mine"' in rows
    # Somebody else's: read, not dismissed - unless by an admin.
    other = {"id": 2, "username": "ali", "role": "operator", "csrf": "c"}
    theirs = pages._wish_rows(data, other)
    assert "Dismiss" not in theirs and "with mehdi" in theirs
    assert 'data-view="theirs"' in theirs
    admin = {"id": 3, "username": "root", "role": "admin", "csrf": "c"}
    assert 'action="/wishes/4/dismiss"' in pages._wish_rows(data, admin)
    # No phone ever existed: a dash where the serial goes.
    none_yet = pages._wish_rows({"wishes": [dict(data["wishes"][3], serial="")]},
                                me)
    assert "&mdash;" in none_yet and 'href="/phones/' not in none_yet


def test_the_page_watches_itself_while_a_wish_is_pending():
    """It sat on the slow cadence because it did not know anything was
    pending: a hand build settles `done` in `actions` at once, so the
    queue count is zero while the wish is still waiting."""
    import re

    from geelark_farm.web import pages

    data = {"phones": [], "stock": {}, "queue": {"running": 0, "queued": 0},
            "pulse": {}, "choose": {}, "pool_rows": {}, "awaiting": [],
            "wishes": [{"gmail": "a@x.com", "status": "queued",
                        "created_at": None}]}
    user = {"id": 1, "username": "a", "role": "operator", "csrf": "c",
            "mutations": True, "may_login_accounts": True}
    page = pages.dashboard(data, user)
    found = re.search(r'name="gf-refresh" content="(\d+)"', page)
    assert found and found.group(1) == "10", "a pending wish is a busy page"


def test_the_build_card_says_when_a_press_would_be_lost():
    """A stopped pass returns long before it takes the wishes, and the
    breaker and the pause hold back only the keeper's own batch."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "operator", "csrf": "c", "mutations": True,
            "may_login_accounts": True}
    base = {"choose": {}, "stock": {"gmail": {"free": 2},
                                    "proxy": {"free": 3}}}

    stopped = pages._build_card(dict(base, pulse={"stopped": True}), user)
    assert "nothing will be built until an admin starts it" in stopped
    assert "<form" not in stopped, "no form where the press would be lost"

    held = pages._build_card(dict(base, pulse={"tripped": "x"}), user)
    assert "still built" in held and "<form" in held
    assert "still built" not in pages._build_card(dict(base, pulse={}), user)


def test_the_overlay_puts_every_borrowed_body_back_before_it_hides():
    """Closed mid-preview, a sheet stayed on it: reopen Manage and you got
    the old preview with no paste box and no list - and `showInSheet` had
    swapped away the drawer's mount, so the next serial click left the
    dashboard altogether (2026-09-07)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    shut = script.split("function shut(){", 1)[1].split("function ", 1)[0]
    assert "restoreSheet" in shut, "every sheet hands its body back first"
    assert shut.index("restoreSheet") < shut.index("o.hidden = true"), (
        "and before it hides, or the bodies are put back into nothing")


def test_a_press_inside_the_drawer_comes_back_to_the_drawer():
    """The drawer's answer is the phone's own page, and `showInSheet`
    drops `.top` - which is where that page keeps its buttons. One press
    emptied the drawer of every control it had (2026-09-07)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "sheet.dataset.sheet === 'phone'" in script
    assert "openDrawer(got.url)" in script, "with the said token on it"
    assert "if (!body) { location.assign(href); return; }" in script, (
        "a missing mount is a deliberate fallback, not a TypeError")
    drawer = script.split("function openDrawer(", 1)[1]
    assert "script, .alerts, .banner" in drawer, (
        "the page's own alerts do not come into the drawer with it")


def test_the_drawer_does_not_freeze_the_page_behind_it():
    """It holds no box to type in, so a page frozen behind it is a build
    nobody can watch move. A manager does freeze it - that is the point of
    `heldOpen` - and the drawer is the exception written into it."""
    from geelark_farm.web import pages

    # From the test itself to the next function, so a nested one does not
    # cut it short: the rule lives in `heldOpen`, which `settled` asks.
    gate = pages._DASH_SCRIPT.split("function heldOpen(){", 1)[1]
    gate = gate.split("function reloadWhenSettled", 1)[0]
    assert "openKind !== 'phone'" in gate
    assert ("if (heldOpen() || busyHere())"
            " { settled.since = 0; return false; }" in gate)


def test_the_drawer_says_what_a_press_did_and_can_stop_a_build():
    from geelark_farm.web import pages

    user = {"id": 1, "username": "a", "role": "operator", "csrf": "c",
            "mutations": True, "may_take_phones": True,
            "may_login_accounts": True}
    story = {"serial": "1900", "timeline": [],
             "phone": {"serial": "1900", "status": "building", "state": ""}}
    drawn = pages.phone_story_page(story, user, said="took:9")

    assert "It is yours" in drawn, "a press inside the drawer is answered"
    assert drawn.index("It is yours") < drawn.index('class="top"'), (
        "outside .top, which the drawer hides")
    assert 'action="/phones/1900/stop"' in drawn, (
        "the one thing there is to do about a phone being built")


def test_the_search_reads_the_row_and_not_its_own_buttons():
    """It matched `tr.textContent`, which includes the buttons - so
    "free", the most natural word to type, kept nearly every row (a free
    row matches its chip, a set-aside row matches its own Free button) and
    "edit" or "remove" kept all of them (2026-09-07)."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_add_gmail": True}
    table = pages._pool_table(
        "gmail", [{"address": "a@x.com", "state": "set aside",
                   "seller": "Egypt", "second": "authenticator",
                   "serial": ""}], user)

    assert 'data-find="' in table
    found = table.split('data-find="', 1)[1].split('"', 1)[0]
    assert "a@x.com" in found and "set aside" in found and "egypt" in found
    assert "remove" not in found and "edit" not in found
    assert "(tr.dataset.find || '')" in pages._DASH_SCRIPT


def test_one_seller_is_one_option_however_it_was_typed():
    """"Ali · 12" and "ali · 8" were two people, and a trailing space
    showed a count beside a name that then matched nothing (2026-09-07)."""
    from geelark_farm.web import pages

    rows = [{"seller": "Ali"}, {"seller": "ali "}, {"seller": " ALI"}]
    assert pages._sellers_of(rows) == {"ali": ("Ali", 3)}

    picker = pages._seller_filter("gmail", rows)
    assert picker.count("<option") == 2, "every seller, and Ali"
    assert 'value="ali">Ali · 3' in picker


def test_the_manager_says_when_the_list_is_not_the_whole_pool():
    """The cap was silent and the search only looks at what was drawn, so
    an address that happened to be the 340th row answered "Nothing matches
    that" to a search that had never seen it (2026-09-07)."""
    from geelark_farm.web import pages

    live = [{"state": "free"}] * 300
    spent = [{"state": "used"}] * 300
    said = pages._capped(live, {"live": 412, "spent": 0})
    assert said.count("300") == 1 and "412" in said
    assert "the search only looks at these" in said
    assert "spent" not in said, "only the part that was cut is mentioned"
    both = pages._capped(live + spent, {"live": 412, "spent": 900})
    assert "300 of 412 current and errored rows" in both
    assert "300 of 900 spent rows" in both
    assert pages._capped(live[:12], {"live": 12, "spent": 0}) == ""
    assert pages._capped(live[:12], None) == ""


def test_the_building_row_reads_the_run_that_is_holding_the_phone():
    """It took the newest line for the serial with no run and no time
    about it, so a build ten seconds old showed yesterday's failure
    sentence and four hours of elapsed time (2026-09-07)."""
    import inspect

    from geelark_farm.web import read

    sql = inspect.getsource(read._latest_lines)
    assert "JOIN claims" not in sql, (
        "the claims table has never had a row; joined on it, every "
        "building row read 'starting' for three days (2026-09-10)")
    assert "p.done_at IS NULL" in sql and "l.at >= p.created_at" in sql
    assert "m.at >= p.created_at" in sql
    assert "p.created_at AS started" in sql, (
        "and how long it has been going, from the row's own start rather "
        "than from the first line of whatever ran last")


def test_the_page_starts_at_the_top_and_stays_there():
    """`main` is a column flex box at least 100vh tall, and the operator
    always got `alone` - so the dashboard was centred vertically and
    re-centred after every live swap, sliding half a row under the cursor
    each time a row appeared (2026-09-07)."""
    from geelark_farm.web import pages

    who = {"id": 1, "username": "a", "role": "operator"}
    assert '<main class="full">' in pages.page("t", "body", user=who)
    assert '<main class="alone">' in pages.login(), "the sign-in card floats"
    assert "main.full{padding:40px 20px}" in assets.CSS


def test_the_overlay_takes_the_page_behind_it_out_of_the_tab_order():
    """Tab walked from the last row of the sheet onto the buttons under
    the dark backdrop, and Enter pressed whichever it landed on."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "page.inert = !!off" in script
    assert "behind(true)" in script and "behind(false)" in script
    # And the manager opens on the box it exists for.
    assert "open.querySelector('.addbox textarea')" in script


def test_a_press_in_flight_cannot_be_fired_twice_by_the_keyboard():
    """`pointer-events:none` does not stop Enter on a focused submit."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "pressed.disabled = true" in script
    assert script.count("pressed.disabled = false") == 2, (
        "cleared on the way through and on the way out")
    assert "form.busy{cursor:progress}" in assets.CSS


def test_the_confirm_is_placed_in_the_window_and_does_not_outlive_a_scroll():
    """It sat at the row position on the document, so a Remove near the
    foot of a long list asked below the fold - the press looked like it
    had done nothing - and one scroll left the bubble over another row."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "window.innerHeight - size.height - 8" in script
    assert "window.innerWidth - size.width - 8" in script
    assert "{capture: true, once: true}" in script
    assert ".mini{position:fixed" in assets.CSS


def test_an_empty_view_says_what_is_empty_about_it():
    """One fixed sentence about a search, shown after pressing "With me"
    on a quiet morning - and this table has no search."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "You are not holding any phone" in script
    assert "Nothing is free right now" in script
    assert "Nothing here matches that." in script, "the other views keep it"


# ---------------------------------------- the Gmail sheet, redrawn (2026-09-08)
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_pool_reads_in_three_views_with_current_pressed(web, monkeypatch):
    """current, errored, spent - the operator asked for the three
    (2026-09-08). Current is what the farm can still use: free, on a
    phone, or set aside by hand. A word a run left is errored. Proxies
    have no spent rows, so no chip promises them."""
    def gmail_row(i, address, status, state, serial=""):
        return {"id": i, "address": address, "status": status, "seller": "",
                "serial": serial, "note": "", "error": None, "state": state}

    _dash(monkeypatch, pool_rows={
        "gmail": [gmail_row(1, "free@gmail.com", "", "free"),
                  gmail_row(2, "held@gmail.com", "in_use", "on a phone", "1500"),
                  gmail_row(3, "kept@gmail.com", "set_aside", "set_aside"),
                  gmail_row(4, "stuck@gmail.com", "captcha_shown",
                            "captcha_shown"),
                  gmail_row(5, "gone@gmail.com", "used", "used")],
        "gpt": [],
        "proxy": [{"id": 6, "address": "SX7", "status": "", "host": "1.2.3.4",
                   "port": 1080, "exit_ip": "", "times_used": 0, "serial": "",
                   "note": "", "error": None, "state": "free"}]})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    ov = _sheets(client)
    assert "spent and delivered rows are not listed" not in ov
    gmail = ov[ov.index('data-sheet="gmail"'):ov.index('data-sheet="gpt"')]
    assert 'data-group="current" aria-pressed="true">current<b>3</b>' in gmail
    assert 'data-group="errored" aria-pressed="false">errored<b>1</b>' in gmail
    assert 'data-group="spent" aria-pressed="false">spent<b>1</b>' in gmail

    def row(address):
        at = gmail.index(f"<td>{address}</td>")
        return gmail[gmail.rfind("<tr", 0, at):at]

    assert 'data-group="current"' in row("kept@gmail.com"), "parked by hand"
    assert 'data-group="errored"' in row("stuck@gmail.com")
    assert 'data-group="spent"' in row("gone@gmail.com")
    proxy = ov[ov.index('data-sheet="proxy"'):]
    assert 'data-group="spent"' not in proxy[:proxy.index("<tbody")]
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "tr.dataset.group === group" in script
    assert "Nothing here matches that" in script, (
        "a match under another chip is named, not denied")


def test_adding_stock_has_no_date_box_and_only_the_filter_row_sticks(web):
    """The one picker nobody used made the paste row look like a form,
    and the paste box stuck to the top beside the search - the two slid
    over each other on scroll (the operator, 2026-09-08). And the sheet
    showing a page of its own was `.sub`, which is the subtitle class:
    the preview sat in two thirds of the sheet, cut off at the edge."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_add_gmail": True}
    box = pages._pool_add_box("gmail", user, [])
    assert 'type="date"' not in box and 'name="purchased"' not in box
    assert 'name="seller"' in box, "the seller stays"

    rules = re.sub(r"/\*.*?\*/", "", assets.CSS, flags=re.S)
    assert ".sheetbody .addbox,.sheetbody .filters{position:sticky" not in rules
    assert ".sheetbody .filters{position:sticky" in rules
    assert ".sheetbody.sub" not in rules and ".sheetbody.shown{" in rules
    assert "tr.editrow" not in rules and "dialog.editor{" in rules


@pytest.mark.parametrize("web", [True], indirect=True)
def test_the_preview_is_one_card_with_the_table_inside_it(web, monkeypatch):
    """Three panels - the table, a form with the button, the paste again
    - read as a mess inside the sheet (the operator, 2026-09-08, the
    third time). One card: the count at the top, the rows in a table
    that scrolls inside it, the confirm at the foot, and no date."""
    monkeypatch.setattr(app_mod.read, "known", lambda s, kind: {})
    monkeypatch.setattr(app_mod.read, "gmail_sellers", lambda s: [])
    client = web()
    client.login()
    status, _, body = client.request(
        "POST", "/pools/gmail/preview",
        _form(csrf=client.csrf(), seller="usa",
              pasted="a@example.com\tKx82!mnQ\tJBSWY3DPEHPK3PXP\n"
                     "not an address\tx"))
    assert status == 200
    card = body[body.index('class="panel preview"'):]
    card = card[:card.index("</form>")]
    assert "<h3>1 row to add, 1 to skip</h3>" in card
    assert "nothing is written until you press Add" in card
    assert '<div class="wrap"><table>' in card
    assert "Add 1 (skip 1)" in card, "the confirm is in the same card"
    assert "seller: usa" in card
    assert 'type="date"' not in body and "bought today" not in body


# ------------------------------------- the table after a press (2026-09-08)
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_taken_phone_keeps_its_place_and_wears_one_pill(web, monkeypatch):
    """Take sent the row to the foot of its group, under the cursor, and
    grew it a second pill; a marked phone still offered every button
    while the sync had not got to it yet (the operator, 2026-09-08)."""
    _dash(monkeypatch, phones=[
        {"serial": "1856", "status": "ready", "state": "taken",
         "owner": "mehdi", "gmail": "a@gmail.com", "app_account": "x@y.com"},
        {"serial": "1862", "status": "ready", "state": "",
         "gmail": "b@gmail.com", "app_account": "z@y.com"},
        {"serial": "1870", "status": "ready", "state": "failed",
         "owner": "mehdi", "gmail": "c@gmail.com", "app_account": "w@y.com"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    assert body.index('href="/phones/1856"') < body.index('href="/phones/1862"'), (
        "by serial, taken or not")
    start = body.index('href="/phones/1856"')
    row = body[start:body.index("</tr>", start)]
    assert row.count('class="badge') == 1, "With you, and nothing beside it"
    assert 'title="Ready">With you</span>' in row
    # Marked failed: gone from the table the moment the press lands, and
    # out of the count - the operator has nothing left to do with it. The
    # phone's own page still tells its story until the sync closes it.
    assert 'href="/phones/1870"' not in body
    assert "2 phones" in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_done_and_failed_ask_beside_the_button(web, monkeypatch):
    """The two that delete the phone went to a page of their own to ask;
    Remove already asked in a bubble beside the button. Same bubble, same
    words the page had (the operator, 2026-09-08)."""
    from geelark_farm.web import pages

    _dash(monkeypatch, phones=[{"serial": "1856", "status": "ready",
                                "state": "taken", "owner": "mehdi"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    start = body.index('href="/phones/1856"')
    row = body[start:body.index("</tr>", start)]
    assert ('data-ask="Phone 1856 failed? The phone is deleted in GeeLark '
            'within a few seconds') in row
    assert 'data-yes="Yes, phone 1856 is failed"' in row
    assert ('data-ask="Phone 1856 done? The phone is deleted in GeeLark '
            'within a few seconds') in row
    assert row.count("data-ask=") == 2, "Release asks nothing"
    script = pages._DASH_SCRIPT
    assert "askFirst(form, form.dataset.ask, form.dataset.yes || 'Yes')" in script
    assert "function askFirst(form, question, answer)" in script


def test_the_send_sheet_counts_a_cross_as_no_account(web, monkeypatch):
    """The build writes a cross into the account column of a warm phone,
    and the sheet read the cross as an account - so with a warm phone on
    the list it said none could take one (the operator, 2026-09-08)."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_login_accounts": True}
    data = {"phones": [{"serial": "1848", "status": "app_only",
                        "app_account": "\u2717", "state": "",
                        "proxy_name": "SX22"}]}
    sheet = pages._send_sheet(data, user)
    assert 'name="serial" value="1848"' in sheet
    assert "No phone can take an account" not in sheet
    gone = dict(data, phones=[dict(data["phones"][0], state="failed")])
    assert 'value="1848"' not in pages._send_sheet(gone, user), "leaving"


# ---------------------------------- what GeeLark has on, and Boot (2026-09-08)
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_phone_geelark_has_on_reads_running_and_offers_no_boot(web,
                                                                  monkeypatch):
    """Booted from the console or by hand in GeeLark, a running phone is
    billing - and it read as free, Boot and all, so nobody could tell it
    was on (the operator, 2026-09-08)."""
    from geelark_farm.web import pages

    _dash(monkeypatch, phones=[
        {"serial": "1862", "status": "ready", "state": "", "running": True,
         "gmail": "a@gmail.com", "app_account": "x@y.com"},
        {"serial": "1848", "status": "app_only", "state": "", "running": True,
         "gmail": "b@gmail.com", "app_account": "\u2717"},
        {"serial": "1856", "status": "ready", "state": "taken",
         "owner": "mehdi", "running": True, "gmail": "c@gmail.com"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    def row(serial):
        start = body.index(f'href="/phones/{serial}"')
        return body[start:body.index("</tr>", start)]

    assert 'nobody here holds it">Running</span>' in row("1862")
    # Neither Boot (it is on) nor Take (gone, 2026-09-16): the keeper
    # switches it off after an hour and the row offers Boot again then.
    assert ">Boot<" not in row("1862") and ">Take<" not in row("1862")
    assert ">Change IP<" in row("1862")
    # A phone being built is on because the build has it: Building.
    _dash(monkeypatch, phones=[{"serial": "1939", "status": "building",
                                "state": "", "running": True}])
    _, _, built = client.request("GET", "/")
    assert 'class="badge info">Building</span>' in built
    assert "Running" not in built[built.index('id="phones"'):built.index("</table>")]
    assert "With you &middot; on</span>" in row("1856")
    # Off again: the ordinary row, Boot and all.
    _dash(monkeypatch, phones=[{"serial": "1862", "status": "ready",
                                "state": "", "running": False}])
    _, _, again = client.request("GET", "/")
    assert ">Boot<" in again
    table = again[again.index('id="phones"'):again.index("</table>")]
    assert "Running" not in table
    # And the send sheet does not offer a phone that is on: the finish
    # would refuse it as in use by hand.
    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_login_accounts": True}
    sheet = pages._send_sheet(
        {"phones": [{"serial": "1848", "status": "app_only",
                     "app_account": "\u2717", "state": "", "running": True}]},
        user)
    assert 'value="1848"' not in sheet


def test_the_live_tab_asks_again_by_itself_and_the_dashboard_says_so():
    """The browser's refresh went inside <noscript> for the dashboard's
    sake, and the live tab has no script - so it never asked again, and
    Boot looked wired to nothing while the link sat in the request's row
    (the operator, 2026-09-08). The dashboard also says what the press
    did, since the answer opens in another tab."""
    from geelark_farm.web import pages

    user = {"id": 1, "username": "test", "role": "operator", "csrf": "c"}
    waiting = pages.live_page("1862", user, said="queued:70", row={})
    assert "setTimeout(function(){ location.reload(); }, 3000)" in waiting
    assert "GeeLark is starting it" in waiting
    started = pages.live_page("1862", user, said="queued:70",
                              row={"status": "done", "result": "started"})
    assert "location.reload()" not in started, "nothing left to wait for"
    script = pages._DASH_SCRIPT
    assert "toast('Starting ' + (which || 'the phone')" in script
    assert "in the new tab as soon as GeeLark hands the link back" in script
    assert "var waiting = /[?&]said=queued/.test(got.url);" in script, (
        "queued is not done: look again shortly")
    assert "if (waiting) climb([2500, 5000, 10000, 20000]);" in script


def test_the_mirror_marks_what_is_running_in_one_statement():
    from geelark_farm import phones as phones_mod
    from geelark_farm import serve as serve_mod
    from geelark_farm.store import shadow

    class Cur:
        rowcount = 2
        executed = []

        def execute(self, sql, params=None):
            self.executed.append((" ".join(sql.split()), params))

    cur = Cur()
    assert shadow.mark_running(cur, ["1862", "1848"]) == 2
    sql, params = cur.executed[0]
    assert "SET running = (serial = ANY(%s))" in sql
    assert "running_since = CASE WHEN serial = ANY(%s) THEN now() END" in sql, (
        "the clock the forgotten-phone sweep reads starts with the flip")
    assert "done_at IS NULL" in sql and params == (["1862", "1848"],) * 3
    assert shadow.mark_running(cur, None) == 0 and len(cur.executed) == 1, (
        "a listing that could not be read says nothing")

    class Client:
        def data(self, path, params):
            return {"items": [
                {"serialNo": "1862", "status": phones_mod.RUNNING},
                {"serialNo": "1848", "status": phones_mod.STOPPED},
                {"serialNo": "1900", "status": phones_mod.STARTING}]}

    assert serve_mod._running(Client()) == ["1862", "1900"]
    assert serve_mod._running(object()) is None


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_release_queues_a_power_off_beside_the_mark(web, monkeypatch):
    """Release is Boot the other way: back on the shelf, and off, so it
    stops billing (the operator, 2026-09-08). Take queues no such thing."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch, phones=[{"serial": "1862", "status": "ready",
                                "state": "taken", "owner": "mehdi"}])
    queued = []
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: queued.append(k) or len(queued))
    client = web()
    client.login()

    client.request("POST", "/phones/1862/state",
                   _form(csrf=client.csrf(), state="unused"))
    assert [q["verb"] for q in queued] == ["power_off_phone", "set_phone_state"]
    assert queued[0]["payload"]["serial"] == "1862"
    assert queued[0]["payload"]["by"] == "mehdi"

    queued.clear()
    client.request("POST", "/phones/1862/state",
                   _form(csrf=client.csrf(), state="taken"))
    assert [q["verb"] for q in queued] == ["set_phone_state"]


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_hand_built_phone_says_who_built_it(web, monkeypatch):
    _dash(monkeypatch, phones=[
        {"serial": "1950", "status": "app_only", "state": "taken",
         "owner": "ali", "built_by": "ali", "gmail": "a@gmail.com"},
        {"serial": "1951", "status": "app_only", "state": "",
         "built_by": None, "gmail": "b@gmail.com"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    def row(serial):
        start = body.index(f'href="/phones/{serial}"')
        return body[start:body.index("</tr>", start)]

    assert ('class="dim maker" title="asked for on the build card">'
            'built by ali</span>') in row("1950")
    assert "built by" not in row("1951"), "the keeper's own phone"


def test_a_failed_wish_with_words_explain_does_not_know_still_draws():
    """`explain` answered nothing for a detail it had no verdict for, and
    the wishes panel unpacked that nothing as a pair - so one failed
    hand-built phone took the whole dashboard down (2026-09-08)."""
    from geelark_farm.web import pages

    data = {"wishes": [{"id": 1, "gmail": "a@x.com", "proxy_name": "",
                        "install_app": True, "app": "chatgpt",
                        "app_account": "", "status": "failed",
                        "detail": "the Gmail a@x.com is not free",
                        "created_at": None, "asked_by": "mehdi"}]}
    me = {"id": 1, "username": "mehdi", "role": "operator", "csrf": "c"}
    assert "is not free" in pages._wish_rows(data, me)
    assert "it did not say why" in pages._wish_rows(
        {"wishes": [dict(data["wishes"][0], detail="")]}, me)


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_proxy_pool_is_kept_by_whoever_may_change_an_exit(web, monkeypatch):
    """Manage on the card, a paste box, and Free / Test / Remove on the
    rows - for the tick that already lets somebody change a phone's exit.
    It was the admin's alone (the operator, 2026-09-08)."""
    import geelark_farm.store.actions as actions_mod
    from geelark_farm.web import paste

    _dash(monkeypatch, pool_rows={
        "gmail": [], "gpt": [],
        "proxy": [{"id": 4, "address": "SX7", "status": "dead", "host": "1.2.3.4",
                   "port": 1080, "exit_ip": "", "times_used": 2, "serial": "",
                   "note": "", "error": None, "state": "dead"},
                  {"id": 5, "address": "SX8", "status": "", "host": "1.2.3.5",
                   "port": 1080, "exit_ip": "", "times_used": 0, "serial": "",
                   "note": "", "error": None, "state": "free"}]})
    got = []
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.append(k) or len(got))
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    assert 'data-pool="proxy">Manage</button>' in body
    sheet = _sheets(client, ("proxy",))
    assert 'action="/pools/proxy/preview"' in sheet, "a paste box"
    assert "socks5://user:pass@host:port" in sheet

    def row(name):
        at = sheet.index(f"<td>{name}</td>")
        return sheet[at:sheet.index("</tr>", at)]

    # A dead one is tested - Test is what frees one that answers - and
    # a free one is tested or removed; Free as a door is for an exit that
    # wants a new address, or one a dead run left `starting`.
    assert 'action="/pools/proxy/test"' in row("SX7")
    assert 'action="/pools/proxy/free"' not in row("SX7")
    assert 'action="/pools/proxy/free"' not in row("SX8"), "a free one is free"
    assert 'action="/pools/proxy/test"' in row("SX8")

    # The vendor's line: a name, then the address as a URL.
    rows = paste.proxies("SX40 socks5://ul01m07t:jn7Ols6u@190.2.141.12:10448")
    assert rows == [{"raw": "socks5://ul01m07t:jn7Ols6u@190.2.141.12:10448",
                     "name": "SX40",
                     "line": "SX40 socks5://ul01m07t:jn7Ols6u@190.2.141.12:10448"}]

    # The doors go through the same permission, and come back to the page.
    status, headers, _ = client.request(
        "POST", "/pools/proxy/test",
        _form(csrf=client.csrf(), name="SX7", back="/"))
    assert status == 303 and dict(headers)["Location"].startswith("/?said=")
    assert got[-1]["verb"] == "test_proxy"
    assert got[-1]["payload"]["name"] == "SX7"
    monkeypatch.setattr(app_mod.read, "known", lambda s, kind: {})
    status, _, preview = client.request(
        "POST", "/pools/proxy/preview",
        _form(csrf=client.csrf(), back="/",
              pasted="SX40 socks5://ul01m07t:jn7Ols6u@190.2.141.12:10448"))
    assert status == 200 and 'class="panel preview"' in preview
    assert '<input type="hidden" name="back" value="/">' in preview
    assert "SX40" in preview and "Add 1 (skip 0)" in preview



@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_proxy_sheet_reads_like_the_proxy_tab(web, monkeypatch):
    """What the Proxy tab said, said here, with why and since when - and
    two chips, not four: an exit is either in play or it is a job (the
    operator, 2026-09-14). `claimed` is not an error, it is a build that
    took the exit seconds ago (2026-09-09)."""
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    rows = [
        {"id": 1, "address": "SX1", "status": "", "host": "1.1.1.1",
         "port": 10, "exit_ip": "9.9.9.1", "times_used": 3, "serial": "",
         "note": "", "error": None, "updated_at": now, "claimed_at": None},
        {"id": 2, "address": "SX2", "status": "on a phone", "host": "1.1.1.2",
         "port": 10, "exit_ip": "9.9.9.2", "times_used": 5, "serial": "2013",
         "note": "On phone 2013, which stopped short of ready.",
         "error": None, "updated_at": now - dt.timedelta(minutes=40),
         "claimed_at": now - dt.timedelta(minutes=41)},
        {"id": 3, "address": "SX3", "status": "claimed", "host": "1.1.1.3",
         "port": 10, "exit_ip": "", "times_used": 0, "serial": "",
         "note": "", "error": None, "updated_at": now,
         "claimed_at": now - dt.timedelta(seconds=20)},
        {"id": 4, "address": "SX4", "status": "dead", "host": "1.1.1.4",
         "port": 10, "exit_ip": "", "times_used": 8, "serial": "",
         "note": "GeeLark could not reach it when a phone was put behind "
                 "it: socks5://u:***@1.1.1.4:10 - Proxy connection failed",
         "error": None, "updated_at": now - dt.timedelta(hours=2),
         "claimed_at": None},
        {"id": 5, "address": "SX5", "status": "change ip", "host": "1.1.1.5",
         "port": 10, "exit_ip": "9.9.9.5", "times_used": 25, "serial": "",
         "note": "", "error": None, "updated_at": now, "claimed_at": None},
        {"id": 6, "address": "SX6", "status": "suspect", "host": "1.1.1.6",
         "port": 10, "exit_ip": "9.9.9.6", "times_used": 2, "serial": "",
         "note": "Suspect - 3 Google challenges on 1.1.1.6 today.",
         "error": None, "updated_at": now, "claimed_at": None},
    ]
    from geelark_farm.web import read as read_mod
    for r in rows:
        r["state"] = read_mod._pool_state("proxy", r)
    assert [r["state"] for r in rows] == [
        "free", "on a phone", "starting", "dead", "needs new IP", "suspect"]

    _dash(monkeypatch, pool_rows={"gmail": [], "gpt": [], "proxy": rows})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    sheet = _sheets(client, ("proxy",))
    head = sheet[:sheet.index("<tbody")]

    # Two chips and `all`, each with its count: in play is free, on a
    # phone and starting; the rest is the work list.
    assert 'data-group="" aria-pressed="true">all<b>6</b>' in head
    assert ('data-group="free / on a phone" aria-pressed="false">'
            'free / on a phone<b>3</b>') in head
    assert ('data-group="dead / set aside" aria-pressed="false">'
            'dead / set aside<b>3</b>') in head
    assert 'data-group="dead"' not in head, "four chips became two"
    # The tab's columns.
    for col in ("Name", "State", "Address", "Exit IP", "Used", "Phone"):
        assert f"<th>{col}</th>" in head
    assert "<th>Note</th>" not in head, "no Note column (the operator, 2026-09-09)"
    # Test all covers every exit no build is holding - the dead one, the
    # one waiting for an address and the one the gate set aside - so the
    # count it shows is the whole work list, not the dead alone.
    assert 'action="/pools/proxy/test-all"' in head
    assert "Test all · 3 set aside" in head
    # Free all answers that whole list, and is shown only under its chip.
    assert 'action="/pools/proxy/free-all"' in head
    assert 'data-for-group="dead / set aside" hidden' in head
    assert "Free all · 3" in head

    def row(name):
        at = sheet.index(f"<td>{name}</td>")
        return sheet[sheet.rfind("<tr", 0, at):sheet.index("</tr>", at)]

    # Free: Test and Remove, nothing to free.
    assert "/pools/proxy/test" in row("SX1") and "/pools/proxy/remove" in row("SX1")
    assert "/pools/proxy/free" not in row("SX1")
    # On a phone: the phone, since when on the pill's hover, and no doors
    # - the phone decides.
    assert "<td>2013</td>" in row("SX2") and 'title="since 40m ago"' in row("SX2")
    assert "/pools/proxy/" not in row("SX2")
    # Starting: in play - that is where it is going - says a build took
    # it, Free only.
    assert 'data-group="free / on a phone"' in row("SX3")
    assert 'title="a build took it 20s ago' in row("SX3")
    assert "/pools/proxy/free" in row("SX3")
    assert "/pools/proxy/test" not in row("SX3")
    # Dead: why on the hover, and Test - answering is what frees it.
    assert "Proxy connection failed" in row("SX4")
    assert "<td>Proxy" not in row("SX4") and "<td>GeeLark" not in row("SX4")
    assert "/pools/proxy/test" in row("SX4") and "/pools/proxy/free" not in row("SX4")
    # Needs a new IP: a job, Free (tested first) offered.
    assert 'data-group="dead / set aside"' in row("SX5")
    assert "/pools/proxy/free" in row("SX5")
    # Suspect - a host Google kept challenging - the same three doors,
    # and the count on the hover (2026-09-09).
    assert 'data-group="dead / set aside"' in row("SX6")
    assert "/pools/proxy/free" in row("SX6") and "/pools/proxy/test" in row("SX6")
    assert "3 Google challenges" in row("SX6")


def test_a_screen_is_served_from_the_store_first_and_the_disk_second(
        make_settings, tmp_path, monkeypatch):
    from geelark_farm.store import artifacts as store_artifacts
    from geelark_farm.web import read as read_mod

    folder = tmp_path / "20260910-010203-build622"
    folder.mkdir()
    (folder / "a.xml").write_text("<disk/>", encoding="utf-8")
    settings = make_settings(artifact_dir=tmp_path, artifacts_in_pg=True)

    monkeypatch.setattr(store_artifacts, "get",
                        lambda s, serial, f, n: b"<store/>")
    assert read_mod.screen_bytes(settings, "622", folder.name, "a.xml") == b"<store/>"
    monkeypatch.setattr(store_artifacts, "get", lambda s, serial, f, n: None)
    assert read_mod.screen_bytes(settings, "622", folder.name, "a.xml") == b"<disk/>"
    assert read_mod.screen_bytes(settings, "622", "..", "a.xml") is None
    assert read_mod.screen_bytes(settings, "622", folder.name, "a.png") is None


# ------------------------------------------ the build card (2026-09-10)
def test_a_failed_wish_is_dismissed_by_a_press_and_the_press_is_the_askers(
        web, monkeypatch):
    """A direct write, like the users page: nothing runs and nothing is
    queued. The store decides who may - the asker, or an admin - and a
    press that changed nothing says so rather than nothing."""
    import geelark_farm.store.wanted as wanted_mod

    _dash(monkeypatch)
    calls = []

    def dismiss(settings, wanted_id, *, user_id, admin):
        calls.append((wanted_id, user_id, admin))
        return wanted_id == 4

    monkeypatch.setattr(wanted_mod, "dismiss", dismiss)
    client = web()
    client.login()

    status, headers, _ = client.request("POST", "/wishes/4/dismiss",
                                        _form(csrf=client.csrf()))
    assert status == 303 and dict(headers)["Location"] == "/?said=dismissed"
    assert calls[-1][0] == 4 and calls[-1][1] == 7, "the seat's own id"
    assert calls[-1][2] is True, "the fake seat is an admin"

    status, headers, _ = client.request("POST", "/wishes/9/dismiss",
                                        _form(csrf=client.csrf()))
    assert status == 303 and dict(headers)["Location"] == "/?said=no"

    status, headers, _ = client.request("POST", "/wishes/x/dismiss",
                                        _form(csrf=client.csrf()))
    assert status == 303 and dict(headers)["Location"] == "/?said=no"
    assert len(calls) == 2, "a bad id never reaches the store"


def test_a_failed_wish_stays_a_day_not_forever():
    """The column arrived after weeks of wishes, and every failure since
    June stood up on the dashboard at once (the operator, 2026-09-10)."""
    import inspect

    from geelark_farm.web import read

    sql = inspect.getsource(read.dashboard)
    at = sql.index("w.status = 'failed' AND w.dismissed_at IS NULL")
    assert "> now() - interval '24 hours'" in sql[at:at + 200]


def test_the_live_link_of_a_building_phone_is_the_builders_newest_start_line():
    """GeeLark answers the start call with the link; the builder logs it,
    once per start, so the newest such line of the phone's current run is
    the screen as it is now - after an exit swap too."""
    import inspect
    from types import SimpleNamespace

    from geelark_farm.web import read

    sql = inspect.getsource(read._live_links)
    assert "l.msg LIKE 'watch it live: %%'" in sql
    assert "JOIN claims" not in sql and "l.at >= p.created_at" in sql, (
        "this phone's own life, not last week's")
    assert read._live_links(SimpleNamespace(_rows=lambda *a: []), []) == {}

    store = SimpleNamespace(_rows=lambda sql, params: [
        {"serial": "2241", "msg": "watch it live: https://phone.geelark.com/x?id=1 "},
        {"serial": "2242", "msg": "watch it live: nothing"}])
    assert read._live_links(store, ["2241", "2242"]) == {
        "2241": "https://phone.geelark.com/x?id=1"}


def test_a_building_row_offers_watch_live_beside_cancel():
    from geelark_farm.web import pages

    user = {"id": 1, "username": "a", "role": "operator", "csrf": "c",
            "mutations": True, "may_login_accounts": True,
            "may_take_phones": True}
    rows = pages._phone_rows({
        "phones": [{"serial": "2241", "status": "building", "state": ""},
                   {"serial": "2242", "status": "building", "state": ""}],
        "progress": {},
        "live": {"2241": "https://phone.geelark.com/x?id=1&a=b"}}, user)
    r1 = rows[:rows.index("</tr>")]
    r2 = rows[rows.index("</tr>"):]
    assert ('<a class="btn quiet live" target="_blank" rel="noopener" '
            'href="https://phone.geelark.com/x?id=1&amp;a=b"') in r1
    assert "Watch live</a>" in r1 and "Cancel" in r1
    assert "Watch live" not in r2 and "Cancel" in r2, "no link yet: no dead button"


# --------------------------------------- the login-rate work (2026-09-10)
def test_the_login_rate_page_reads_every_dimension_and_is_the_admins(
        web, monkeypatch):
    from geelark_farm.web import app as app_mod

    data = {"days": 7, "min_sample": 5,
            "totals": {"ok": 230, "n": 624, "gmails": 378, "rate": 0.37},
            "by": {"seller": [{"key": "LEO", "ok": 23, "n": 29, "rate": 0.79},
                              {"key": "UK", "ok": 0, "n": 65, "rate": 0.0}],
                   "model": [{"key": "OPPO PLN110", "ok": 5, "n": 6, "rate": 0.83}],
                   "host": [{"key": "190.2.141.31", "ok": 1, "n": 11, "rate": 0.09}],
                   "day": [{"key": "2026-09-09", "ok": 107, "n": 181, "rate": 0.59}],
                   "reason": [{"key": "signed_in", "ok": 230, "n": 230, "rate": 1.0},
                              {"key": "captcha_shown", "ok": 0, "n": 130, "rate": 0.0}],
                   "position": [{"key": "1", "ok": 180, "n": 423, "rate": 0.43},
                                {"key": "2", "ok": 2, "n": 3, "rate": 0.67}]}}
    asked = []
    monkeypatch.setattr(app_mod.read, "logins",
                        lambda s, days=7: asked.append(days) or dict(data, days=days))
    client = web()
    client.login()

    status, _, body = client.request("GET", "/logins?days=14")
    assert status == 200 and asked == [14]
    assert "<h2>Login rate</h2>" in body and "37%" in body
    assert "230</b><span>of 624 attempts" in body
    for word in ("LEO", "UK", "OPPO PLN110", "190.2.141.31", "2026-09-09",
                 "captcha_shown", "Gmail #"):
        assert word in body, word
    assert "79%" in body and "83%" in body and "9%" in body
    assert 'class=thin' in body, "two attempts is too few to judge"
    assert 'href="/logins"' in body, "on the rail"
    assert '<option value="14" selected>' in body
    # A day count that is not a number is a week.
    client.request("GET", "/logins?days=x")
    assert asked[-1] == 7


def test_the_login_rate_page_is_not_an_operators():
    from geelark_farm.web import pages

    who = {"id": 1, "username": "a", "role": "operator", "sees": "own"}
    assert 'href="/logins"' not in pages.page("t", "body", user=who)
    admin = {"id": 1, "username": "a", "role": "admin", "sees": "all"}
    assert 'href="/logins"' in pages.page("t", "body", user=admin)


def test_the_login_rate_reader_asks_the_store_for_each_dimension(monkeypatch):
    from geelark_farm.store import signins as store_signins
    from geelark_farm.web import read

    asked = []
    monkeypatch.setattr(store_signins, "rates",
                        lambda s, name, days: asked.append((name, days)) or [
                            {"key": "2", "ok": 1, "n": 2, "rate": .5},
                            {"key": "1", "ok": 3, "n": 4, "rate": .75}])
    monkeypatch.setattr(store_signins, "totals",
                        lambda s, days: {"ok": 4, "n": 6, "gmails": 5,
                                         "rate": 4 / 6})
    got = read.logins(None, days=400)
    assert got["days"] == 90, "capped"
    assert {name for name, _ in asked} == {"seller", "model", "host", "day",
                                           "reason", "position"}
    assert [r["key"] for r in got["by"]["position"]] == ["1", "2"], "in order"
    assert got["totals"]["gmails"] == 5 and got["min_sample"] == 5


def test_a_failed_wish_with_its_phone_on_the_shelf_is_not_a_row_of_its_own():
    import inspect

    from geelark_farm.web import read

    sql = inspect.getsource(read.dashboard)
    at = sql.index("w.status = 'failed' AND w.dismissed_at IS NULL")
    assert "NOT EXISTS (SELECT 1 FROM phones p" in sql[at:at + 500]
    assert "p.serial = w.serial" in sql[at:at + 600]



def test_a_refund_row_offers_the_two_words_to_whoever_may_add_gmails():
    """Paid, or refused. Nothing here puts the address back in the pool -
    it left the moment Google said the account itself was the problem -
    so the only thing left to record is whether the money came back
    (the operator, 2026-09-12)."""
    from geelark_farm.web import pages

    owed = {"id": 2, "address": "owed@x.com", "status": "password_changed",
            "tries": 3, "retry_after": None, "refund_state": "to_claim"}
    waiting = {"id": 3, "address": "back@x.com", "status": "captcha_shown",
               "tries": 1, "retry_after": "2026-09-12 09:00:00",
               "refund_state": ""}
    admin = {"username": "mehdi", "role": "admin", "mutations": True,
             "csrf": "t"}

    # The cell is handed where the person is, page and all (2026-09-21).
    here = "/pools/gmail?view=errored&seller=hoavan1&page=2"
    cell = pages._refund_cell(owed, admin, here)
    assert "To claim back" in cell
    assert cell.count('action="/pools/gmail/refund"') == 2
    assert 'value="claimed"' in cell and 'value="refused"' in cell
    assert "seller=hoavan1&amp;page=2" in cell, "it comes back to the list it was on"

    assert "back in the queue" in pages._refund_cell(waiting, admin, here)
    assert pages._refund_cell(
        dict(owed, refund_state="claimed"), admin, here) == (
        '<span class="badge green">Paid back</span>'), "settled, no buttons"
    looker = {"username": "ali", "role": "operator", "mutations": False}
    assert "action=" not in pages._refund_cell(owed, looker, here)



# ------------------------------------------- the API's own keys (2026-09-12)
def _clients(monkeypatch, rows=None):
    """read.api_clients and the store module behind the page, faked at
    their edges the way _people fakes the Users page."""
    import geelark_farm.store.api_clients as store_clients

    rows = rows if rows is not None else [
        {"id": 1, "name": "panel", "role": "panel", "key_prefix": "XyQ4iYRl",
         "active": True, "webhook_url": "", "has_secret": False,
         "created_at": None, "last_seen_at": None, "requests_today": 4,
         "accounts_today": 12, "accounts_today_utc": 9}]
    seen = {"minted": [], "rotated": [], "active": [], "webhook": []}

    monkeypatch.setattr(app_mod.read, "api_clients", lambda s: {
        "rows": rows, "day": "2026-09-12", "cap": 100, "per_minute": 600,
        "writes": False})
    monkeypatch.setattr(store_clients, "listing", lambda s: rows)
    monkeypatch.setattr(store_clients, "get", lambda s, i: rows[0])

    def create(settings, *, name, role):
        if name == "panel":
            raise Exception("duplicate key value violates unique constraint")
        if not name:
            raise ValueError("a name of 1 to 60 characters")
        seen["minted"].append((name, role))
        return 7, "the-new-token"

    monkeypatch.setattr(store_clients, "create", create)
    monkeypatch.setattr(store_clients, "rotate",
                        lambda s, i: seen["rotated"].append(i) or
                        ("panel", "rotated-token"))
    monkeypatch.setattr(store_clients, "set_active",
                        lambda s, i, a: seen["active"].append((i, a)) or "panel")
    monkeypatch.setattr(
        store_clients, "set_webhook",
        lambda s, i, *, url, secret="": seen["webhook"].append((i, url, secret))
        or "panel")
    return seen


@pytest.mark.parametrize("web", [{"web_mutations": True}], indirect=True)
def test_the_api_clients_page_lists_the_keys_and_what_each_did_today(
        web, monkeypatch):  # noqa: F811
    """Until this page there was no way to hand out or kill a key without
    ssh and the CLI - which meant the person who runs the farm could not
    stop a client that had gone wrong (2026-09-12)."""
    _clients(monkeypatch)
    client = web()
    client.login()

    status, _, body = client.request("GET", "/api-clients")

    assert status == 200
    assert "API clients" in body and "panel" in body
    assert "XyQ4iYRl" in body, "the prefix tells two keys apart"
    assert "9</span> accounts (UTC), 4 requests" in body, (
        "the number the cap is enforced on, beside the cap that says UTC")
    assert "12 today here" in body, "and the console's own day beside it"
    assert 'action="/api-clients/1/rotate"' in body
    assert 'href="/api-clients"' in body, "and it is on the rail"
    assert "the token itself is never stored" in body


@pytest.mark.parametrize("web", [{"web_mutations": True}], indirect=True)
def test_a_minted_key_is_shown_once_and_is_never_in_a_url(
        web, monkeypatch):  # noqa: F811
    """A token in an address is a token in the history, the log and the
    banner - so the POST answers with the page instead of redirecting."""
    seen = _clients(monkeypatch)
    client = web()
    client.login()
    token = client.csrf()

    status, headers, body = client.request(
        "POST", "/api-clients/new",
        _form(csrf=token, name="bot", role="bot"))

    assert status == 200, "answered, not redirected"
    assert seen["minted"] == [("bot", "bot")]
    assert "the-new-token" in body and "shown only now" in body
    assert "Location" not in dict(headers)

    _, _, again = client.request("GET", "/api-clients")
    assert "the-new-token" not in again, "and never again"


@pytest.mark.parametrize("web", [{"web_mutations": True}], indirect=True)
def test_a_name_that_exists_is_refused_rather_than_rotated(
        web, monkeypatch):  # noqa: F811
    """The CLI's mint ends in ON CONFLICT (name) DO UPDATE, which is right
    at a terminal and wrong on a page: retyping a name there would
    silently invalidate a live panel's key."""
    seen = _clients(monkeypatch)
    client = web()
    client.login()

    _, _, body = client.request(
        "POST", "/api-clients/new",
        _form(csrf=client.csrf(), name="panel", role="panel"))

    assert "that name is taken" in body
    assert "New key on its row" in body, "and it says what to do instead"
    assert seen["minted"] == [] and seen["rotated"] == []


@pytest.mark.parametrize("web", [{"web_mutations": True}], indirect=True)
def test_a_new_key_asks_first_because_the_old_one_stops_at_once(
        web, monkeypatch):  # noqa: F811
    seen = _clients(monkeypatch)
    client = web()
    client.login()
    token = client.csrf()

    _, _, asking = client.request("POST", "/api-clients/1/rotate",
                                  _form(csrf=token))
    assert "stops working the moment" in asking and seen["rotated"] == []

    _, _, body = client.request("POST", "/api-clients/1/rotate",
                                _form(csrf=token, sure="1"))
    assert seen["rotated"] == [1]
    assert "rotated-token" in body and "key replaced" in body


@pytest.mark.parametrize("web", [{"web_mutations": True}], indirect=True)
def test_a_key_can_be_switched_off_and_a_webhook_needs_https(
        web, monkeypatch):  # noqa: F811
    import geelark_farm.store.api_clients as store_clients

    seen = _clients(monkeypatch)
    client = web()
    client.login()
    token = client.csrf()

    status, headers, _ = client.request("POST", "/api-clients/1/active",
                                        _form(csrf=token, active="0"))
    assert status == 303 and seen["active"] == [(1, False)]
    assert dict(headers)["Location"] == "/api-clients?said=off"

    status, headers, _ = client.request(
        "POST", "/api-clients/1/webhook",
        _form(csrf=token, url="https://panel.example/hook", secret="s3cret"))
    assert status == 303 and seen["webhook"] == [(1, "https://panel.example/hook",
                                                  "s3cret")]

    def refuse(settings, client_id, *, url, secret=""):
        raise ValueError("an https:// address - an event carries an account")

    monkeypatch.setattr(store_clients, "set_webhook", refuse)
    _, _, body = client.request("POST", "/api-clients/1/webhook",
                                _form(csrf=token, url="http://panel.example"))
    assert "an https:// address" in body


@pytest.mark.parametrize("web", [{"web_mutations": True}], indirect=True)
def test_the_keys_are_an_admins_and_an_operator_is_sent_home(
        web, monkeypatch):  # noqa: F811
    """A GET goes home the way every other admin page here answers one; a
    POST refuses, because a form that quietly does nothing is how somebody
    comes to believe they did something."""
    _clients(monkeypatch)
    client = web()
    client.login()
    token = client.csrf()
    import geelark_farm.web.app as mod

    original = mod._Handler._user

    def operator(self):
        who = original(self)
        return dict(who, role="operator") if who else who

    monkeypatch.setattr(mod._Handler, "_user", operator)

    status, headers, _ = client.request("GET", "/api-clients")
    assert status == 303 and dict(headers)["Location"] == "/"

    # With the session's real token, so the refusal is the admin gate's
    # and not the CSRF check standing in front of it (the review).
    status, _, body = client.request("POST", "/api-clients/new",
                                     _form(csrf=token, name="n", role="bot"))
    assert status == 403 and "Nothing was changed" in body, (
        "a refusal, not a redirect: a form that quietly does nothing is "
        "how somebody comes to believe they did something")



def test_a_phone_being_built_for_somebody_says_building_first():
    """A phone ordered from the card is reserved for whoever asked for it
    from the moment it exists, so the taken pill took the place of the
    only word that matters while a build is running on it: four rows read
    "With you" while they were being made, which reads as finished and
    handed over (the operator, 2026-09-11)."""
    from geelark_farm.web import pages

    building = {"serial": "2358", "status": "building", "state": "taken",
                "owner": "mehdi", "running": True}

    mine = pages._phone_badge(building, me="mehdi")
    assert "Building" in mine and "With you" not in mine
    assert "yours" in mine and 'class="badge info"' in mine

    theirs = pages._phone_badge(dict(building, owner="alirez"), me="mehdi")
    assert "Building" in theirs and "alirez" in theirs

    # Nobody holds it: the word on its own, as before.
    loose = pages._phone_badge(dict(building, state="", owner=""), me="mehdi")
    assert loose == '<span class="badge info">Building</span>'

    # And once it is built, whose it is is the whole answer again.
    done = pages._phone_badge(dict(building, status="ready"), me="mehdi")
    assert "With you" in done and "Building" not in done


def test_the_account_column_only_promises_an_account_a_phone_can_take():
    """It used to read the wish's single app out of the phone's `App name`
    cell - "spotify" meant no account was coming, empty meant the phone
    was done. Every phone carries all three now, so that cell says
    "chatgpt+spotify+claude" on every row and both branches went quiet: a
    bare phone read "waiting for one" over an account nothing can send,
    because a finished phone is on no warm list (the operator,
    2026-09-12)."""
    from geelark_farm.web import pages

    three = "chatgpt+spotify+claude"

    # Warm: an account really is coming, whatever is installed.
    warm = pages._account_cell({"status": "app_only", "app": three,
                                "gmail": "g@x.com", "app_account": ""})
    assert "waiting for one" in warm

    # Bare and finished: nothing is coming, and it says why.
    bare = pages._account_cell({"status": "ready", "app": three,
                                "gmail": "", "app_account": "✗"})
    assert "no Google account" in bare and "waiting" not in bare

    # Finished with Google on it but no account signed in.
    alone = pages._account_cell({"status": "ready", "app": three,
                                 "gmail": "g@x.com", "app_account": ""})
    assert "none signed in" in alone and "waiting" not in alone

    # And the address itself whenever there is one.
    done = pages._account_cell({"status": "ready", "app": three,
                                "gmail": "g@x.com", "app_account": "a@x.com"})
    assert "a@x.com" in done


def test_the_claude_card_stands_under_spotify_shut(web, monkeypatch):
    """The rail already reads as the three products it will hold, but
    the Claude card has no door and no number: its accounts arrive from
    the bot, and a Manage that opened an empty sheet would promise a way
    in that does not exist yet (the operator, 2026-09-17)."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    card = body[body.index('<section class="pool off"'):]
    card = card[:card.index("</section>")]
    assert "Claude accounts" in card and "not open yet" in card
    assert 'aria-disabled="true"' in card
    assert "<button" not in card, "no door"
    assert 'data-sheet="claude"' not in body, "no sheet either"
    # Under Spotify, in the right rail.
    side = body[body.index('<aside class="side" data-live="side">'):]
    assert side.index("Spotify accounts") < side.index("Claude accounts")


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_build_card_asks_for_an_account_not_a_gpt_account(web,
                                                              monkeypatch):
    """The same box is where a Spotify account will be chosen once its
    login exists (the operator, 2026-09-17)."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    card = body[body.index("<h3>Build one now</h3>"):]
    card = card[:card.index("</form>")]
    assert "<span>Account</span>" in card
    assert "GPT account</span>" not in card
    # The dialog the box opens stands after the form, in the same panel.
    assert "Choose an account" in body and "Choose a GPT account" not in body


@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_a_spotify_rows_send_goes_where_its_kind_wants(web, monkeypatch):
    """`error` wants the warm phone the chooser offers - the GPT pool's
    own Send. `normal` wants a phone with no Google account, and none is
    kept warm, so its press asks for one to be built with the account
    named (2026-09-17)."""
    import geelark_farm.store.actions as actions_mod

    rows = [{"id": 1, "address": "nova@x.com", "status": "", "serial": "",
             "note": "", "error": None, "state": "free",
             "category": "normal", "password": "p", "secret": "",
             "second": ""},
            {"id": 2, "address": "tab@x.com", "status": "", "serial": "",
             "note": "", "error": None, "state": "free",
             "category": "error", "password": "p", "secret": "",
             "second": ""}]
    base = _dash(monkeypatch)
    _dash(monkeypatch, pool_rows=dict(base["pool_rows"], spotify=rows),
          spotify={"normal": 1, "error": 1})
    got = []
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.append(k) or 61)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    sheet = _sheets(client, ("spotify",))
    sheet = sheet[:sheet.index("</section>")]
    normal = sheet[sheet.index("nova@x.com"):sheet.index("tab@x.com")]
    assert 'action="/accounts/spotify/build"' in normal
    # `+` for the kind whose phone does not exist yet, `→` for the kind
    # that goes onto one that does (2026-09-18).
    assert "+ phone</button>" in normal and "&rarr; phone" not in normal
    errored = sheet[sheet.index("tab@x.com"):]
    assert 'action="/accounts/login"' in errored and "&rarr; phone" in errored

    # On the card, where the row is 314px wide, the kind is its mark
    # alone and the address is split so the name survives the clip - the
    # pill and the words left an ellipsis where the name was (the
    # operator, 2026-09-18). The sheet keeps the word: it has the room.
    card = body[body.index("Spotify accounts"):]
    card = card[:card.index("</section>")]
    queue = card[card.index('<ul class="queue">'):]
    assert 'class="cat dot normal"' in queue
    assert 'class="cat normal"' not in queue, "no word beside the address"
    assert "<b>nova</b><i>@x.com</i>" in queue
    assert "+ phone</button>" in queue
    # The line above the list still counts them by word, and so does the
    # sheet: both have the room for it.
    assert 'class="cat normal">normal</span><b>1</b>' in card
    assert 'class="cat normal"' in sheet, "the sheet says the word"
    assert "/accounts/spotify/build" not in errored

    status, headers, _ = client.request(
        "POST", "/accounts/spotify/build",
        _form(csrf=client.csrf(), address="nova@x.com", back="/"))
    assert status == 303
    assert got[-1]["verb"] == "build_by_hand"
    payload = got[-1]["payload"]
    assert payload["no_gmail"] is True and payload["app"] == "spotify"
    assert payload["app_account"] == "nova@x.com"


def test_a_sheet_showing_a_preview_is_not_a_list_to_update():
    """The pool's rows were merged into the preview's own table, so the
    whole pool appeared under the two rows being previewed - and the
    list waiting behind it stayed as it was before the paste, which is
    what Back, the x and the next Manage all showed. A pool five
    accounts had just been added to read as empty until the page was
    reloaded (the operator, 2026-09-18)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    # The guard comes before the row merge, or the merge has happened.
    guard = script.index("var shown = mine.querySelector('.sheetbody.shown')")
    merge = script.index("var body = mine.querySelector('tbody')")
    assert guard < merge
    assert "if (waiting) shown._was = waiting;" in script, (
        "what the swap brought waits behind the preview")


def test_a_paste_that_landed_leaves_the_box_and_the_preview():
    """A box still holding what is now in the pool invites the same
    paste twice, and the sheet stayed on the preview it had already
    acted on (the operator, 2026-09-18)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "if (/[/]add$/.test(form.action) && !turned.test(got.url)) {" in script
    assert "restoreSheet(sheet);" in script
    assert "sheet.querySelectorAll('textarea[name=pasted]')" in script
    # An add that was turned away keeps what was typed: that is the
    # thing to correct.
    assert ("var turned = /[?&]said=(no|refused|already|bad|none)(?:[:&]|$)/;"
            in script)
    # And a browser reload does not put it back either.
    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_add_gmail": True, "may_add_gpt": True}
    for kind in ("gmail", "gpt", "spotify"):
        box = pages._pool_add_box(kind, user, [])
        assert 'name="pasted"' in box and 'autocomplete="off"' in box, kind


def test_send_is_offered_only_on_a_row_that_is_free():
    """Every row but one on a phone had a Send - including a delivered
    account and one a run had set aside, both of which the verb refuses
    because neither is claimable (2026-09-18)."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_add_gpt": True, "may_login_accounts": True}
    for kind, extra in (("gpt", {}), ("spotify", {"category": "error"})):
        free = dict({"address": "a@x.com", "state": "free"}, **extra)
        assert "&rarr; phone" in pages._pool_row_doors(kind, free, user, True)
        for state in ("used", "delivered", "set aside", "on a phone",
                      "broken"):
            row = dict(free, state=state)
            doors = pages._pool_row_doors(kind, row, user, True)
            assert "&rarr; phone" not in doors, f"{kind} {state}"
    # The bare-phone door for a normal Spotify account follows the same
    # rule - it spends the account just as surely.
    normal = {"address": "n@x.com", "state": "used", "category": "normal"}
    assert "+ phone" not in pages._pool_row_doors("spotify", normal, user, True)


def test_a_delivered_account_offers_remove_and_nothing_else():
    """Free and Edit on a delivered row both lead to a refusal - a door
    that leads nowhere is worse than no door - and Remove is the one
    thing the operator wants done with it (2026-09-27)."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "is_admin": True, "may_add_gpt": True}
    for kind in ("gpt", "spotify"):
        doors = pages._pool_row_doors(
            kind, {"address": "d@x.com", "state": "delivered",
                   "category": "normal"}, user, True)
        assert "Remove</button>" in doors, kind
        assert ">Free</button>" not in doors and "data-edit" not in doors


def test_the_status_says_which_account_the_phone_carries():
    """Three things in one cell - a product, a kind and an address - made
    the account column a paragraph to read on every row. What the phone
    is belongs with the word for what the phone is; the column keeps the
    address and nothing else (the operator, 2026-09-17)."""
    from geelark_farm.web import pages

    gpt = {"status": "ready", "app_account": "a@x.com", "app_product": ""}
    assert ">GPT<" in pages._carries(gpt)
    assert "Spotify" not in pages._account_cell(gpt)

    spot = {"status": "ready", "app_account": "k@x.com",
            "app_product": "spotify", "app_category": "error"}
    line = pages._carries(spot)
    assert 'class="carries error"' in line and "Spotify error" in line
    # The rule the word stands for, where a hover can reach it.
    assert "goes on a phone that has a Gmail" in line
    # And the column beside it is the address, as it is for every phone.
    assert pages._account_cell(spot) == pages._addr_cell("k@x.com", "")

    # Nothing signed in: nothing said.
    assert pages._carries(dict(spot, app_account="✗")) == ""


def test_the_spotify_paste_picks_its_kind_with_two_tiles():
    """The kind was a dropdown standing between a label and a hint, which
    made this pool look like nothing else in the console and hid the one
    thing a paste can get wrong (the operator, 2026-09-17)."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_add_gpt": True, "may_add_gmail": True}
    box = pages._pool_add_box("spotify", user, [])
    assert '<select name="category"' not in box, "a list hides the rule"
    assert box.count('type="radio" name="category"') == 2
    assert 'value="normal" id="cat-spotify-normal" checked' in box
    assert "goes on a phone with no Gmail" in box
    assert "goes on a phone that has a Gmail" in box
    # One kind per paste, said beside the press rather than above the box.
    assert "one kind per paste" in box

    # The pool is in the id: both add boxes can be open on the dashboard
    # at once, and two labels pointing at one id bind to the wrong input
    # (2026-09-19).
    gpt = pages._pool_add_box("gpt", user, [])
    assert 'id="cat-gpt-plain"' in gpt and 'id="cat-gpt-eco"' in gpt
    assert "cat-spotify" not in gpt

    # And the pools with no kinds keep the shape they had.
    assert 'class="kindpick"' not in pages._pool_add_box("gmail", user, [])


def test_the_spotify_sheet_sifts_by_kind():
    """Two stocks that go on two kinds of phone, in one list, is a list
    you have to read twice. The status chips answer whether a row can
    still be used; these answer which kind it is, and they sift together
    (the operator, 2026-09-17)."""
    from geelark_farm.web import pages

    rows = [{"address": "a@x.com", "state": "free", "category": "normal"},
            {"address": "b@x.com", "state": "free", "category": "error"},
            {"address": "c@x.com", "state": "used", "category": "error"}]
    chips = pages._kind_chips("spotify", rows)
    assert 'data-cat=""' in chips and "both<b>3</b>" in chips
    assert 'data-cat="normal"' in chips and "normal<b>1</b>" in chips
    assert 'data-cat="error"' in chips and "error<b>2</b>" in chips
    assert "data-cat=" not in pages._kind_chips("gmail", rows), (
        "a pool with no kinds sifts by none")

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_add_gpt": True}
    assert 'data-cat="error"' in pages._pool_table("spotify", rows, user)
    assert "data-cat=" not in pages._pool_table("gmail", rows, user)

    script = pages._DASH_SCRIPT
    assert ("var cats = sheet.querySelectorAll('.filters .pill[data-cat]');"
            in script)
    assert "&& (!cat || tr.dataset.cat === cat);" in script
    # And the kind survives the swap the live layer makes under it.
    assert "cat: onCat ? onCat.dataset.cat : null," in script
    # The editor opens on the row's own kind, and does not reach for the
    # key box this pool does not have.
    assert "if (f.secret) f.secret.value = creds.secret || '';" in script
    assert "if (f.category) f.category.value = tr.dataset.cat || 'normal';" \
        in script
    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_add_gpt": True}
    editor = pages._pool_editor("spotify", user, rows)
    assert 'name="category"' in editor and 'name="secret"' not in editor


def test_a_choice_put_back_by_the_dialog_re_gates_the_card():
    """Setting .value fires nothing, so Cancel on the Gmail dialog left
    the account box live over a bare build (2026-09-12)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "pick.dispatchEvent(new Event('change'));" in script
    assert script.index("pick.value = was;") < script.index(
        "pick.dispatchEvent(new Event('change'));")


def test_the_proxy_manager_is_ordered_by_the_number_in_the_name():
    """The vendor's panel lists them SX1, SX2, SX3, and that is the order
    a person works down when they are changing addresses. It was by
    `times_used`, the builder's order, which shuffled the list under the
    hand using it (the operator, 2026-09-14)."""
    import inspect

    from geelark_farm.web import read as read_mod

    src = read_mod._PROXY_Q
    assert "regexp_replace(coalesce(proxy_name, '')" in src
    assert "'[^0-9]', '', 'g'), '')::bigint NULLS LAST" in src
    assert "ORDER BY times_used" not in src, "not the builder's order"


# ----------------------------------- the page moves when the farm does
def test_the_pulse_bumps_only_when_the_fingerprint_moves():
    """One number for the whole console: a page that hears it swaps, and
    one that hears nothing does nothing (2026-09-14)."""
    from geelark_farm.web import live

    pulse = live.Pulse()
    assert pulse.revision == 0 and pulse.everything == 0
    farm = ("p", "ph", "e", "a", "w", "s")
    # The first fingerprint is where the farm is, not a change - it must
    # not wake a page that has just loaded.
    assert pulse.bump(farm + ("l1",)) is False and pulse.revision == 1
    assert pulse.bump(farm + ("l1",)) is False, "the same mark is not news"
    assert pulse.revision == 1
    assert pulse.bump(("p", "ph", "e2", "a", "w", "s", "l1")) is True
    assert pulse.revision == 2 and pulse.everything == 2
    # A log line alone moves `everything` and not the farm's own count:
    # the dashboard does not redraw for a build's chatter, the Logs page
    # does (2026-09-14).
    assert pulse.bump(("p", "ph", "e2", "a", "w", "s", "l2")) is True
    assert pulse.revision == 2 and pulse.everything == 3
    assert pulse.count() == 2 and pulse.count(logs=True) == 3
    # Waiting: past the number you have, or nothing before the timeout.
    assert pulse.wait(1, 0.01) == 2
    assert pulse.wait(2, 0.01) is None
    assert pulse.wait(2, 0.01, logs=True) == 3
    assert pulse.wait(3, 0.01, logs=True) is None
    assert pulse.hold(1) == 1 and pulse.hold(1) == 2 and pulse.hold(-1) == 1


def test_a_waiter_is_woken_by_the_bump_not_by_the_clock():
    import threading

    from geelark_farm.web import live

    pulse = live.Pulse()
    pulse.bump(("first",))
    got = []
    waiter = threading.Thread(
        target=lambda: got.append(pulse.wait(pulse.revision, 5.0)))
    waiter.start()
    time.sleep(0.05)
    pulse.bump(("second",))
    waiter.join(timeout=2)
    assert got == [2], "it came back on the bump, not on the five seconds"


def test_the_fingerprint_asks_about_every_table_a_page_draws(monkeypatch,
                                                             make_settings):
    from geelark_farm.store import db as store_db
    from geelark_farm.web import live

    asked = []

    class _Store:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def _rows(self, sql, params=()):
            asked.append(sql)
            return [{"pools": "2026-09-14", "phones": None, "events": 7,
                     "actions": 3, "wanted": None, "state": "2026-09-21",
                     "logs": 900}]

    monkeypatch.setattr(store_db, "Store", _Store)
    mark = live.take(make_settings(store_enabled=True))

    assert mark == ("2026-09-14", "", "7", "3", "", "2026-09-21", "900")
    # service_state too: the keeper's pulse, the GeeLark strip, the
    # breaker and a Cancel that has landed are drawn from it, and none of
    # them could move the revision (2026-09-21, found by audit).
    for table in ("resources", "phones", "events", "actions", "wanted_builds",
                  "service_state", "logs"):
        assert table in asked[0], table
    # The log lines are the last column, apart from the farm's own.
    assert mark[:live.FARM_COLUMNS] == ("2026-09-14", "", "7", "3", "",
                                        "2026-09-21")

    # A store that will not answer is not a crash and not a change.
    monkeypatch.setattr(store_db, "Store",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("down")))
    assert live.take(make_settings(store_enabled=True)) is None


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_live_stream_sends_the_revision_and_keeps_itself_warm(
        web, monkeypatch):
    """Server-Sent Events: one long GET, a number per change. The page
    still has its timer, so a browser that cannot hold this loses the
    second and nothing else."""
    from geelark_farm.web import live

    client = web()
    client.login()
    # Capped, so a runaway cannot take every thread of the server.
    monkeypatch.setattr(live, "MAX_STREAMS", 0)
    status, _, body = client.request("GET", "/live")
    assert status == 503 and "timer" in body


def test_the_dashboard_listens_on_the_stream_and_keeps_its_timer():
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "new EventSource(which.content === 'logs' ? '/live?logs=1'" in script
    assert ": '/live');" in script
    assert "listen.on" in script, "one stream per tab, not one per swap"
    assert "listen();" in script
    # The timer stays: a proxy that will not carry a stream must not mean
    # a page that never updates - and every re-check goes through the one
    # clock, so the soonest one stands.
    assert "function lookAgain(ms)" in script
    assert "if (init.timer && init.due && init.due <= due) return false;" in script
    assert "lookAgain(Math.max(250, SWAP_FLOOR - since));" in script, (
        "the stream asks for soon, but no sooner than the floor")
    assert "feed.onerror" in script


def test_a_swap_puts_the_manager_back_the_way_it_was():
    """It kept which sheet was open and nothing else, so every press -
    and the timer, every thirty seconds, unasked - threw the chip back to
    `all` and emptied the search (the operator, 2026-09-14)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "function viewNow()" in script and "function viewBack(" in script
    for kept in ("aria-pressed=\"true\"", ".poolfind", ".sellerpick",
                 "scrollTop"):
        assert kept in script, kept
    # Taken before the swap, put back after it.
    assert "var kept = openKind, seen = viewNow(), place = placeNow();" in script
    assert "viewBack(seen);" in script


def test_one_rows_press_replaces_one_row():
    """A Test or a Free is about a single exit; swapping the whole of
    `main` for it is why the list jumped (2026-09-14)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "function swapRow(doc, key, gone)" in script
    # A queued answer is the row BEFORE the press - the lane carries it
    # out a moment later - so it is never swapped in, and the page looks
    # again shortly instead (2026-09-14).
    assert "if (waiting) climb([2500, 5000, 10000, 20000]);" in script
    # And a refusal is not a row to redraw: it is a sentence to read. The
    # words that mean nothing changed are a closed list, so a new verb's
    # success word never lands in it by accident.
    assert "var worked = !nothing.test(got.url);" in script
    assert ("(queued|no|refused|already|twice|gone|off|none|bad|auto)"
            "(?:[:&]|$)") in script
    # A word boundary, written as one. `\\b` in the Python string arrived
    # in the browser as a backspace character, so the test matched
    # nothing and every refusal redrew its row after all (2026-09-14).
    assert "\x08" not in script
    assert "if (worked && isHere(got.url) && key && swapRow(doc, key)) {" in script
    assert "sayIt(doc);" in script, "the answer's own sentence is shown"
    # The chip counts are recounted off the table, so they cannot drift
    # - every set of chips the sheet has, not only the states.
    assert ("if (v !== undefined) tally[k][v] = (tally[k][v] || 0) + 1;"
            in script)
    # And the row carries the key that finds it. The loop that draws it
    # came out of `_pool_table` on 2026-09-21, so a press about one row
    # can be answered with that row - drawn by the same code, or the two
    # would come to differ.
    assert 'data-key="{esc(key)}"' in _source(pages._pool_table_rows)
    assert "_pool_table_rows(kind, rows, user, manual_login, pending)" in _source(
        pages._pool_table)


def test_a_press_says_what_it_is_doing_and_to_what():
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "pressed.dataset.busy" in script
    assert "function actOn(form, sheet)" in script
    assert "tr.classList.add('acting')" in script
    drawn = pages.page("x", "", user={"username": "a", "role": "admin"})
    assert "tr.acting{opacity:.45" in assets.CSS, (
        "the dimmed rows are styled")


def _source(fn):
    import inspect

    return inspect.getsource(fn)


def test_the_dashboard_script_is_valid_javascript(tmp_path):
    """The whole console's behaviour is one script in one string, and a
    single bad character in it is not a broken feature - it is a page
    with no working buttons at all, because the browser stops at the
    first syntax error and never reaches `init`.

    One regex escape mangled on its way through Python's string rules
    did exactly that: a character class arrived with its backslash
    halved, it did not parse, and Manage, the chips and every door died
    together while the page still drew perfectly (the operator,
    2026-09-14: "Manage does nothing"). Nothing in the suite could see
    it - the other script tests ask whether a sentence is present,
    which it was.

    Skipped where node is not installed.
    """
    import re
    import shutil
    import subprocess

    from geelark_farm.web import pages

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the script is unchecked here")
    body = re.sub(r"^\s*<script>|</script>\s*$", "",
                  pages._DASH_SCRIPT.strip(), flags=re.S)
    path = tmp_path / "dash.js"
    path.write_text(body, encoding="utf-8")
    done = subprocess.run([node, "--check", str(path)],
                          capture_output=True, text=True)
    assert done.returncode == 0, (
        "the dashboard's script does not parse, so every button on the "
        "console is dead: " + done.stderr.strip())


def test_a_swap_keeps_the_reader_where_they_were():
    """Reading row ninety of the phone table, the operator was thrown
    back to the top about every thirteen seconds - which is how often the
    farm moved while it was building. `.slab>.tscroll` is its own
    scrollport, and a swap builds it again at zero (2026-09-14)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "function placeNow()" in script and "function placeBack(" in script
    assert "window.scrollY" in script and "window.scrollTo(0, kept.win)" in script
    assert "querySelectorAll('.tscroll, .queue')" in script
    assert "!el.closest('#poolov')" in script, (
        "the manager's own sheets are viewBack's, not this one's")
    assert "var kept = openKind, seen = viewNow(), place = placeNow();" in script
    assert "placeBack(place);" in script


def test_the_page_waits_for_a_hand_that_is_moving():
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "var SCROLL_QUIET = 1200;" in script
    assert "Date.now() - (scrolled.at || 0) < SCROLL_QUIET" in script
    # Registered once for the tab, not once per swap - `init` runs on
    # every swap and a listener per swap is a listener per thirty seconds.
    assert script.count("addEventListener('scroll', scrolled") == 1
    assert "passive: true" in script, "a scroll listener must not block it"
    # And a selection the reader made is not thrown away mid-read.
    assert "window.getSelection" in script


def test_the_live_stream_does_not_redraw_faster_than_a_person_reads():
    """The fingerprint moves every second or two while the farm builds -
    honest, and far more often than anybody can read (2026-09-14)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "var SWAP_FLOOR = 4000;" in script
    assert "lookAgain(Math.max(250, SWAP_FLOOR - since));" in script
    assert "swapMain.at = Date.now();" in script


def test_the_queued_toast_says_what_actually_happens():
    """It promised "the next pass starts it within about thirty seconds",
    which was true when only a pass could write the sheet. A command
    rings a bell now and a lane takes it - 0.1s to 5s for almost all of
    them (2026-09-14)."""
    from geelark_farm.web import pages

    for said in (pages._POOL_SAID["queued"], pages._SAID["queued"],
                 pages._DASH_SAID["queued"]):
        assert "thirty" not in said and "next pass" not in said
        assert "~30s" not in said
        assert "starts within a second" in said


# ---------------- a swap must never destroy what the person is doing
def test_a_swap_keeps_what_was_typed_and_not_yet_sent():
    """Filling the build card with an account bought this morning, then
    clicking away to glance at the table: the next tick emptied the card
    silently, and Build then spent a pool Gmail instead of the one that
    was typed (the audit, 2026-09-14)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "function typedNow()" in script and "function typedBack(" in script
    assert "el.value !== was" in script, "only what differs from the server's"
    assert "o.defaultSelected" in script and "el.defaultValue" in script
    assert "var typed = typedNow();" in script and "typedBack(typed);" in script
    # A value the dialog added to a select is not in the fresh copy of
    # it, so it is put back too - or the select falls to its first
    # option, which is the whole trap.
    assert "el.insertBefore(made, el.firstChild);" in script


def test_a_question_waiting_for_an_answer_holds_the_page():
    """`askFirst` puts the bubble in `main`, so a swap deleted it
    mid-read and the press was lost - and with the stream that is
    seconds, not thirty (2026-09-14)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    # Both of these sat inside `mayRedraw`, which `settled` overrides
    # after HELD_CEILING - so a question nobody had answered, and an
    # editor holding unsent typing, each got twenty seconds and then the
    # swap took them anyway. They are working surfaces, not gestures
    # somebody left standing, so they hold for as long as they are up
    # (the operator, 2026-09-20).
    assert "document.querySelector('.mini')" in script
    assert "document.querySelector('dialog[open]')" in script
    gate = script[script.index("function busyHere()"):]
    # ...and a press in flight (2026-09-22, band 6): see `pressing`.
    assert "return !!(pressing > 0 || document.querySelector('.mini')" in gate
    assert ("if (heldOpen() || busyHere())"
            " { settled.since = 0; return false; }" in script), (
        "held while it is up, not until the ceiling")


def test_the_page_is_never_held_silently_for_ever():
    """A selection is not a gesture - it lasts until they click
    elsewhere - so the guard that waits for one would wait for ever,
    while the status line kept its breathing live dot (2026-09-14)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "var HELD_CEILING = 20000;" in script
    assert "function mayRedraw()" in script
    assert "Date.now() - settled.since > HELD_CEILING" in script


def test_the_managers_scroll_is_read_off_the_box_that_scrolls():
    """`.sheetbody>.tscroll` is overflow:visible on purpose so the sticky
    headers work, and an overflow:visible box always reports scrollTop 0
    - so the manager's place was never actually kept (2026-09-14)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert script.count("sheet.querySelector('.sheetbody')") >= 2
    assert "|| sheet.querySelector('.tscroll')" in script, "the fallback"
    # And reopening the sheet must not focus the paste box, which scrolls
    # the body back to the top under the restore.
    assert "function show(kind, fresh, justIn)" in script
    assert "if (fresh === false) return;" in script
    assert "show(kept, false);" in script


def test_the_drawer_refreshes_in_place_instead_of_blinking():
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "function openDrawer(href, again)" in script
    assert "openDrawer(drawerHref, true);" in script
    assert "var mark = ++openDrawer.turn;" in script, "a stale answer is dropped"
    assert "if (reading && top) reading.scrollTop = top;" in script
    assert "if (!again) location.assign(href);" in script, (
        "a blip while reading does not throw them off the dashboard")


def test_a_background_refresh_that_failed_never_lands():
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    # One preamble for both handlers now: they had drifted, and the
    # submit half had no `r.ok` test at all, so a 500 from a verb was
    # parsed and installed inside the pool sheet (2026-09-20).
    gate = script[script.index("function answer(r, say){"):]
    gate = gate[:gate.index("\n  }")]
    assert "if (!r.ok) {" in gate
    assert "report('http ' + r.status" in gate
    assert "if (!answer(r, false)) return null;" in script
    assert "if (r.redirected && !isHere(r.url)) return null;" in script
    assert "if (html === null) { lookAgain(5000); return; }" in script
    # Asked again when the answer comes back, not only before it is sent.
    assert "if (!settled()) { lookAgain(5000); return; }" in script


def test_the_nothing_changed_words_match_as_words(tmp_path):
    """Run, not read: the regex that decides whether a press changed
    anything, evaluated by node against the addresses a press answers
    with. Skipped where node is not installed."""
    import re
    import shutil
    import subprocess

    from geelark_farm.web import pages

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the regex is unchecked here")
    line = re.search(r"var nothing = (/.+?/);", pages._DASH_SCRIPT).group(1)
    probe = tmp_path / "probe.js"
    probe.write_text(
        "var nothing = " + line + ";\n"
        "var out = ['/?said=queued:71', '/?said=no:71', '/?said=none',"
        " '/?said=twice:3', '/?said=done:71', '/?said=freed:71',"
        " '/pools/proxy?view=dead&said=refused:2&x=1',"
        " '/?said=nothing:9'].map(function(u){ return +nothing.test(u); });\n"
        "process.stdout.write(out.join(''));\n", encoding="utf-8")
    got = subprocess.run([node, str(probe)], capture_output=True, text=True)
    assert got.returncode == 0, got.stderr
    # queued, no, none, twice and refused mean nothing changed; done and
    # freed did; "nothing" is not "no" - the boundary holds.
    assert got.stdout == "11110010", got.stdout


def test_every_form_carries_a_press_and_no_two_are_the_same():
    """The stamp beside the csrf token: minted when the form is drawn, so
    the same button pressed again after the page moved is a new request
    and the same drawing sent twice is one (2026-09-14)."""
    from geelark_farm.web import pages

    user = {"id": 1, "username": "test", "role": "operator", "csrf": "c"}
    one, two = pages._csrf(user), pages._csrf(user)
    assert 'name="csrf" value="c"' in one
    assert one.count('name="press" value="') == 1
    assert one != two, "one stamp per drawing"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_press_is_the_request_key_and_the_minute_is_the_fallback(
        web, monkeypatch):
    """The key was the wall-clock minute, so a second Test inside the same
    minute folded into the first - already finished - and was answered
    "Queued" over nothing (the operator, 2026-09-14). With the stamp, two
    presses are two rows; without it, an old tab still gets the minute."""
    import geelark_farm.store.actions as actions_mod

    _proxy_pool(monkeypatch, rows=[_proxy_row("D01", "dead")])
    keys = []
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: keys.append(k["idem_key"]) or 71)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    monkeypatch.setattr(actions_mod, "one", lambda s, i: None)
    client = web()
    client.login()
    for stamp in ("abc123", "def456"):
        client.request("POST", "/pools/proxy/test",
                       _form(csrf=client.csrf(), name="D01", press=stamp))
    client.request("POST", "/pools/proxy/test",
                   _form(csrf=client.csrf(), name="D01"))
    # The press, then the press's words (2026-09-21): the stamp is the
    # second-to-last part.
    assert keys[0].split(":")[-2] == "abc123"
    assert keys[1].split(":")[-2] == "def456"
    assert keys[0].rsplit(":", 2)[0] == keys[1].rsplit(":", 2)[0], (
        "same verb, same target, same person")
    assert keys[2].split(":")[-2].isdigit(), "no stamp: the minute, as before"
    assert len(set(keys)) == 3


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_same_drawing_sent_twice_is_told_so_not_queued(web, monkeypatch):
    """A double-tap or a back-button re-POST hands `enqueue` the row the
    first press made. That row has run; "Queued" over it was a lie, and
    the page looked again for a change that had already happened.

    Which of its two branches it took is `enqueue`'s own answer now. It
    used to be re-derived here by reading the row back and comparing the
    database host's `requested_at` to this container's clock with a
    one-second tolerance - so two machines a second apart turned a first
    press into a second one (2026-09-21).
    """
    import geelark_farm.store.actions as actions_mod

    _proxy_pool(monkeypatch, rows=[_proxy_row("D01", "dead")])
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    monkeypatch.setattr(actions_mod, "claim", lambda s, i: True)
    wrote = {"fresh": True}
    monkeypatch.setattr(
        actions_mod, "enqueue",
        lambda s, **k: actions_mod.Queued(71, fresh=wrote["fresh"]))
    client = web()
    client.login()

    # The press that wrote the row, answered as the press it is.
    _, headers, _ = client.request(
        "POST", "/pools/proxy/test",
        _form(csrf=client.csrf(), name="D01", press="same"))
    assert dict(headers)["Location"] == "/pools/proxy?said=queued:71"

    # The same drawing of the button sent again: one row, already run.
    wrote["fresh"] = False
    _, headers, _ = client.request(
        "POST", "/pools/proxy/test",
        _form(csrf=client.csrf(), name="D01", press="same"))
    assert dict(headers)["Location"] == "/pools/proxy?said=twice:71"


def test_an_unpatched_enqueue_is_read_as_a_first_press():
    """A plain int is what every other caller and every fake hands back,
    and a first press is the safe reading of one."""
    assert getattr(41, "fresh", True) is True


def test_the_live_tabs_beat_stamps_the_phone_and_says_when_it_is_over(
        web, monkeypatch):
    """Twenty seconds apart while the tab is open: a plain stamp, no
    request. 410 once the phone is not taken any more, so the tab stops
    beating for nobody and says so. Operators may send it - it is their
    tab."""
    from geelark_farm.store import person

    src = inspect.getsource(person.watch)
    assert "SET watched_at = now()" in src
    assert "AND state = 'taken'" in src, "a beat on a phone put back is nothing"
    seen = []
    monkeypatch.setattr(person, "watch",
                        lambda s, serial: seen.append(serial) or True)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "sara", "role": "operator",
                         "sees": "all", "may_take_phones": True})
    client = web()
    client.login(username="sara")
    status, _, body = client.request("POST", "/phones/1500/watching",
                                     _form(csrf=client.csrf()))
    assert status == 200 and body == "watching" and seen == ["1500"]

    monkeypatch.setattr(person, "watch", lambda s, serial: False)
    status, _, body = client.request("POST", "/phones/1500/watching",
                                     _form(csrf=client.csrf()))
    assert status == 410 and body == "released"

    # No CSRF, no stamp: the beat is a form post like every other.
    status, _, _ = client.request("POST", "/phones/1500/watching",
                                  _form(csrf="wrong"))
    assert status == 403


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_live_tabs_closing_beacon_is_noted_and_the_next_beat_clears_it(
        web, monkeypatch):
    """pagehide fires on a reload as well as a close; the beacon stamps
    the closing, the reloaded page's first beat clears it, and the sweep
    waits longer than that before acting (forgotten.TAB_CLOSED_SECONDS).
    """
    from geelark_farm.store import person

    assert "tab_closed_at = NULL" in inspect.getsource(person.watch)
    closed = inspect.getsource(person.tab_closed)
    assert "SET tab_closed_at = now()" in closed
    assert "AND state = 'taken'" in closed
    seen = []
    monkeypatch.setattr(person, "tab_closed",
                        lambda s, serial: seen.append(serial) or True)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "sara", "role": "operator",
                         "sees": "all", "may_take_phones": True})
    client = web()
    client.login(username="sara")
    status, _, body = client.request("POST", "/phones/1500/closing",
                                     _form(csrf=client.csrf()))
    assert status == 200 and body == "noted" and seen == ["1500"]


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_live_tab_writes_the_phones_gmail_in_the_margin_for_its_holder(
        web, monkeypatch):
    """Address, password (hidden until shown), the authenticator's code
    computed in the page and its key - beside the screen, for whoever
    holds the phone (the operator, 2026-09-16). Somebody else's phone
    gets no margin at all."""
    import geelark_farm.store.actions as actions_mod
    from geelark_farm.web import pages
    from geelark_farm.web import read as read_mod

    row = {"id": 71, "verb": "boot_phone", "status": "done", "result": "ok",
           "detail": {"state": "taken",
                      "url": "https://phone.geelark.com/i?t=abc"},
           "requested_by": 7}
    monkeypatch.setattr(actions_mod, "one", lambda s, aid: row)
    creds = {"address": "islandalaskans@gmail.com", "password": "pa$$w<rd",
             "totp_secret": "JBSWY3DPEHPK3PXP"}
    monkeypatch.setattr(read_mod, "gmail_on_phone",
                        lambda s, serial: dict(creds, asked=serial))
    monkeypatch.setattr(read_mod, "account_on_phone", lambda s, serial: None)
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": {"serial": serial, "status": "app_only",
                                    "state": "taken", "owner": "mehdi"},
        "timeline": []})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/phones/1500/live?said=queued:71")
    assert '<aside id="gf-side">' in body and "<h3>On this phone</h3>" in body
    assert '<code id="gf-addr">islandalaskans@gmail.com</code>' in body
    # The bar that ran across the top lives in the margin now, so the
    # stage is the window's whole height (the operator, 2026-09-16).
    assert '<div class="viewbar">' not in body
    assert '<aside id="gf-side"><div class="gf-head"><b>1500</b>' in body
    assert "var h=stage.clientHeight||window.innerHeight," in body
    assert 'data-value="pa$$w&lt;rd"' in body, "escaped, and hidden"
    assert "\u2022" * 8 in body and "pa$$w<rd" not in body
    assert 'data-secret="JBSWY3DPEHPK3PXP"' in body
    # The code, not the key it is made from (the operator, 2026-09-16).
    assert "Authenticator key" not in body and 'id="gf-key"' not in body
    assert ">JBSWY3DPEHPK3PXP<" not in body
    assert "b32(el.getAttribute('data-secret'))" in body
    assert "%1000000" in body and "setInterval(tick,1000)" in body
    assert "navigator.clipboard.writeText" in body
    # No account on this one, so no second box.
    assert "account</h3>" not in body

    # Somebody else's phone: the margin is not drawn, whatever the
    # store would say.
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": {"serial": serial, "status": "app_only",
                                    "state": "taken", "owner": "ali"},
        "timeline": []})
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "sara", "role": "operator",
                         "sees": "all", "may_take_phones": True})
    other = web()
    other.login(username="sara")
    _, _, body = other.request("GET", "/phones/1500/live?said=queued:71")
    assert "On this phone" not in body and "JBSWY3DPEHPK3PXP" not in body
    assert 'id="gf-reload"' in body, "the margin still carries the controls"

    # No Gmail on the phone: no margin, and the page still draws.
    drawn = pages.viewer_page("1500", {"csrf": "c"}, "https://x/", creds=None)
    assert "On this phone" not in drawn and 'id="gf-view"' in drawn
    # A row with no key says so rather than computing nothing.
    drawn = pages.viewer_page("1500", {"csrf": "c"}, "https://x/",
                              creds={"address": "a@b.com", "password": "",
                                     "totp_secret": ""})
    assert "none on the row" in drawn and 'id="gf-totp"' not in drawn


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_live_tab_changes_the_ip_without_leaving_the_page(web,
                                                              monkeypatch):
    """Change IP beside Reload viewer (the operator, 2026-09-16): the same
    POST the dashboard's button sends, with `boot=1` and this page as
    `back`, sent by fetch - so the tab never navigates, its beat never
    stops, and the phone stays theirs while it is stopped, moved and
    started again. The page Boot's tab waits on is read, not shown: its
    title says whether to keep asking, swap screens, or offer Boot."""
    import geelark_farm.store.actions as actions_mod

    row = {"id": 71, "verb": "boot_phone", "status": "done", "result": "ok",
           "detail": {"state": "taken",
                      "url": "https://phone.geelark.com/i?t=abc"},
           "requested_by": 1}
    monkeypatch.setattr(actions_mod, "one", lambda s, aid: row)
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": {"serial": serial, "status": "app_only",
                                    "state": "taken", "owner": "mehdi"},
        "timeline": []})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/phones/1500/live?said=queued:71")
    assert 'id="gf-ip"' in body and ">Change IP</button>" in body
    assert body.index('id="gf-reload"') < body.index('id="gf-ip"')
    assert ('<form method="post" class="inline" id="gf-boot" hidden '
            'action="/phones/1500/boot">') in body
    assert "fetch('/phones/'+serial+'/proxy',{method:'POST'," in body
    assert "body:form+'&boot=1&back='+encodeURIComponent(here)" in body
    assert "new DOMParser().parseFromString(t," in body
    assert "if(title.indexOf('Changing')===0&&asks<40)" in body
    assert "base=view.getAttribute('data-src'); show();" in body
    assert "if(title.indexOf(' is off')>0)" in body
    assert "if(bootf) bootf.hidden=false" in body
    assert "if(at.pathname!==here)" in body, "a refusal lands on /"

    # The press: one change_proxy request with `boot`, and the redirect
    # is to this page, which is where the fetch reads the answer.
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 72)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    status, headers, _ = client.request(
        "POST", "/phones/1500/proxy",
        _form(csrf=client.csrf(), boot="1", back="/phones/1500/live"))
    assert status == 303
    assert dict(headers)["Location"] == "/phones/1500/live?said=queued:72"
    assert got["verb"] == "change_proxy"
    assert got["payload"]["serial"] == "1500"
    assert got["payload"]["boot"] is True
    # The dashboard's press carries `boot` false, and goes back home.
    status, headers, _ = client.request(
        "POST", "/phones/1500/proxy", _form(csrf=client.csrf()))
    assert dict(headers)["Location"] == "/?said=queued:72"
    assert got["payload"]["boot"] is False
    # And no address the form made up.
    status, headers, _ = client.request(
        "POST", "/phones/1500/proxy",
        _form(csrf=client.csrf(), boot="1", back="/phones/1501/live"))
    assert dict(headers)["Location"] == "/?said=queued:72"

    # What the fetch reads back, state by state.
    row = {"id": 72, "verb": "change_proxy", "status": "queued",
           "result": "", "detail": None, "requested_by": 1}
    monkeypatch.setattr(actions_mod, "one", lambda s, aid: row)
    _, _, body = client.request("GET", "/phones/1500/live?said=queued:72")
    assert "<h2" in body and "Changing the IP of 1500" in body
    assert "next free exit" in body

    row.update(status="done", result="phone 1500 is on SX2 now and started "
                                     "again",
               detail={"was": "SX1", "now": "SX2",
                       "url": "https://phone.geelark.com/i?t=new"})
    _, _, body = client.request("GET", "/phones/1500/live?said=queued:72")
    assert 'data-src="https://phone.geelark.com/i?t=new"' in body

    row.update(status="failed", result="phone 1500 is on SX2 now but GeeLark "
                                       "has no machine free to start it",
               detail={"was": "SX1", "now": "SX2", "off": True})
    _, _, body = client.request("GET", "/phones/1500/live?said=queued:72")
    assert "1500 is off" in body and "no machine free" in body
    assert 'http-equiv="refresh"' not in body

    row.update(status="failed", result="the Proxy tab has no free exit left",
               detail=None)
    _, _, body = client.request("GET", "/phones/1500/live?said=queued:72")
    assert "1500 kept its exit" in body and "no free exit" in body
    assert "did not start" not in body, "it is still running as it was"

    # Not the holder's tick: no button, and the margin still draws.
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "sara", "role": "operator",
                         "sees": "all", "may_take_phones": True,
                         "may_change_proxy": False})
    other = web()
    other.login(username="sara")
    row.update(status="done", result="ok",
               detail={"state": "taken",
                       "url": "https://phone.geelark.com/i?t=abc"})
    _, _, body = other.request("GET", "/phones/1500/live?said=queued:71")
    assert 'id="gf-ip"' not in body and 'id="gf-reload"' in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_live_tab_offers_done_and_failed_beside_its_controls(web,
                                                                 monkeypatch):
    """The dashboard's two ends of a phone, in the margin (the operator,
    2026-09-16). Both delete the phone, so both ask - in the page, by the
    browser's own dialog - and go home afterwards, since the screen they
    were beside is gone. The server's own confirm still stands for a
    press without the script, and the state door is the same one."""
    import geelark_farm.store.actions as actions_mod

    row = {"id": 71, "verb": "boot_phone", "status": "done", "result": "ok",
           "detail": {"state": "taken",
                      "url": "https://phone.geelark.com/i?t=abc"},
           "requested_by": 1}
    monkeypatch.setattr(actions_mod, "one", lambda s, aid: row)
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": {"serial": serial, "status": "app_only",
                                    "state": "taken", "owner": "mehdi"},
        "timeline": []})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/phones/1500/live?said=queued:71")
    acts = body[body.index('<div class="gf-acts">'):]
    acts = acts[:acts.index("</div>") + 6]
    assert acts.count('action="/phones/1500/state"') == 2
    assert 'name="state" value="done"' in acts
    assert 'name="state" value="failed"' in acts
    assert acts.count('name="back" value="/"') == 2, "the phone is gone"
    assert acts.count("data-ask=") == 2
    assert 'class="quiet ok">Done<' in acts and 'class="quiet bad">Failed<' in acts
    assert body.index('id="gf-ip"') < body.index('<div class="gf-acts">')
    assert body.index('<div class="gf-acts">') < body.index("</aside>")
    assert "window.confirm(f.getAttribute('data-ask'))" in body
    assert "s.name='sure'; s.value='1'" in body

    # The press, answered: one set_phone_state, and home.
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 73)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    status, headers, _ = client.request(
        "POST", "/phones/1500/state",
        _form(csrf=client.csrf(), state="failed", sure="1", back="/"))
    assert status == 303
    assert dict(headers)["Location"] == "/?said=queued:73", "home, not here"
    assert got["verb"] == "set_phone_state"
    assert got["payload"]["state"] == "failed"

    # Somebody who may not take phones sees neither, and no dialog.
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "sara", "role": "operator",
                         "sees": "all", "may_take_phones": False})
    other = web()
    other.login(username="sara")
    _, _, body = other.request("GET", "/phones/1500/live?said=queued:71")
    assert 'class="gf-acts"' not in body and "window.confirm" not in body
    assert 'id="gf-reload"' in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_take_is_gone_and_boot_is_the_one_door_onto_a_free_phone(web,
                                                                 monkeypatch):
    """Boot takes the phone and the Live tab's closing releases it, so
    Take - a hold with no way back but a button somebody forgets - went
    (the operator, 2026-09-16). A free row offers Boot and Change IP; a
    taken one Release, Done and Failed; the phone's own page the same.
    The state door still knows the word, for the requests already in
    flight and the panel's own rows."""
    _dash(monkeypatch, phones=[
        {"serial": "1500", "status": "ready", "state": ""},
        {"serial": "1501", "status": "ready", "state": "taken",
         "owner": "mehdi"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    def row(serial):
        start = body.index(f'href="/phones/{serial}"')
        return body[start:body.index("</tr>", start)]

    free, taken = row("1500"), row("1501")
    assert ">Take<" not in body and 'value="taken"' not in body
    assert "</svg>Boot</button>" in free and ">Change IP<" in free
    for label in ("Release", "Done", "Failed"):
        assert f">{label}<" not in free and f">{label}<" in taken, label
    assert "</svg>Boot</button>" not in taken

    from geelark_farm.web import pages
    user = {"csrf": "c", "mutations": True, "role": "admin",
            "username": "mehdi", "id": 1}
    assert pages._state_forms(user, {"serial": "1500", "state": ""}) == []
    assert len(pages._state_forms(user, {"serial": "1500",
                                         "state": "taken"})) == 3


def test_a_swap_keeps_the_manager_somebody_is_reading(web, monkeypatch):
    """The overlay is a child of `main`, so every swap replaced it with a
    fresh copy and showed that again - and the sheet blinked out and back
    under the operator's hand each time a builder wrote a row, every few
    seconds while the farm was busy (the operator, 2026-09-17). Now the
    nodes under the hand stay: the open sheet's rows are matched by key
    and only the ones that moved are swapped, its chips recounted, the
    closed sheets taken fresh, and the page behind it made inert again.
    The listeners a sheet carries are bound once per node, and the sift
    reads its rows live, so a kept sheet neither doubles up nor sifts
    rows that are gone."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    script = assets.JS
    assert "var held = (kept && kept !== 'phone') ? ov() : null;" in script
    # The overlay never leaves the DOM: its fade and the sheet's rise are
    # CSS animations, and a node taken out and put back plays them again.
    assert "if (n !== held) here.removeChild(n);" in script
    assert "here.insertBefore(n, held);" in script
    assert "keepSheet(held, kept, brought);" in script
    assert "else if (held) behind(true);" in script
    assert "else if (kept && kept !== 'send') show(kept, false);" in script
    keep = assets.JS[assets.JS.index("function keepSheet(held, kind, fresh)"):]
    assert "fresh.replaceWith(held)" not in keep
    assert "if (old) old.replaceWith(s); else held.appendChild(s);" in keep
    assert "else if (same(old) !== same(tr)) old.replaceWith(tr);" in keep
    assert "if (!old) body.insertBefore(tr, none);" in keep
    assert "if (!want[k]) have[k].remove();" in keep
    assert "name=\"press\"" in keep, "the one-time token is not a change"
    # Bound once, read live.
    bind = assets.JS[assets.JS.index(
        "document.querySelectorAll('#poolov .sheet').forEach"):]
    # The same guard, now said the way every binding site says it:
    # `once(node, what)`, because with regions a node survives a swap
    # and a second set of listeners is a second sift (2026-09-21).
    assert "if (once(sheet, 'sift')) {" in bind
    assert ("var body = function(){ return sheet.querySelectorAll("
            "'tbody tr:not(.none)'); };") in bind
    assert "var rows = body();" in bind


def test_the_page_holds_still_while_a_sheet_is_open(web, monkeypatch):
    """A sheet somebody opened is a working surface, and the news behind
    it can wait. The rule was written once and lost - it sat below an
    unconditional `return` in `settled`, so it never ran, and the manager
    blinked once per held-ceiling under the operator's hand (2026-09-17).
    Held while it is open, with one refresh the moment it closes."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    held = assets.JS[assets.JS.index("function heldOpen()"):]
    assert "return !!(o && !o.hidden && openKind && openKind !== 'phone');" in held
    settled = assets.JS[assets.JS.index("function settled()"):]
    assert ("if (heldOpen() || busyHere()) "
            "{ settled.since = 0; return false; }" in settled)
    # The dead line went with it: it sat after a `return` and named two
    # variables that live in another function.
    assert "return !(o && !o.hidden && openKind !== 'phone') && !typing;" \
        not in body
    shut = assets.JS[assets.JS.index("function shut()"):]
    assert "lookAgain(300);" in shut[:shut.index("function behind(")]


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_hand_built_phone_waits_on_its_makers_shelf_with_boot_on_the_row(
        web, monkeypatch):
    """The build card used to hand its phone over `taken`, and a taken row
    has no Boot: looking at what you had just built meant pressing Release
    and then Boot, which takes it again (the operator, 2026-09-18: "I want
    them released, so it can be booted, but nobody except the maker can
    boot it").

    So the build puts it back on the shelf with its maker's name on it.
    The pill is the plain status word, the line under it says whose shelf,
    and the row offers the one press that was missing beside the two that
    end it. Release and Change IP are on the phone's own page: a fourth
    button is one more than this column fits.
    """
    _dash(monkeypatch, phones=[{"serial": "1502", "status": "ready",
                                "state": "", "owner": "mehdi",
                                "built_by": "mehdi",
                                "app_account": "jack@gmail.com"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    start = body.index('href="/phones/1502"')
    row = body[start:body.index("</tr>", start)]

    assert 'action="/phones/1502/boot"' in row
    for label in ("Boot", "Done", "Failed"):
        assert f">{label}<" in row, label
    for label in ("Release", "Change IP", "Take"):
        assert f">{label}<" not in row, f"{label} is on the phone's own page"
    # Not "With you": nobody is holding it, and the status word is the
    # news. Whose shelf it is waiting on rides under it.
    assert '<span class="badge ready">Ready</span>' in row
    assert "With you" not in row
    assert "kept for you" in row
    assert body[body.rindex("<tr", 0, start):start].startswith(
        '<tr data-view="mine"'), "theirs, so not under Free"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_nobody_else_boots_a_phone_kept_for_its_maker_not_even_an_admin(
        web, monkeypatch):
    """"Nobody except the maker can boot it" - and an admin is not an
    exception, for the same reason they are not one on a taken phone:
    ending somebody's hold is theirs, taking the phone over is not
    (2026-09-15). The other two ways a phone comes back are still the
    admin's, as they always were."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch, phones=[{"serial": "1503", "status": "ready",
                                "state": "", "owner": "ali",
                                "built_by": "ali"}])
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 71)
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": {"serial": serial, "status": "ready",
                                    "state": "", "owner": "ali",
                                    "built_by": "ali"},
        "timeline": []})

    # Another operator: the row says whose it is and offers nothing.
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "sara", "role": "operator",
                         "sees": "all", "may_take_phones": True,
                         "may_change_proxy": True})
    sara = web()
    sara.login(username="sara")
    _, _, body = sara.request("GET", "/")
    start = body.index('href="/phones/1503"')
    row = body[start:body.index("</tr>", start)]
    assert '<span class="age">built for ali</span>' in row
    for label in ("Boot", "Release", "Done", "Failed", "Change IP"):
        assert f">{label}<" not in row, label

    # The admin: no Boot drawn, and the press refused if one is forged.
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 7, "username": "mehdi", "role": "admin",
                         "sees": "all"})
    admin = web()
    admin.login()
    _, _, body = admin.request("GET", "/")
    start = body.index('href="/phones/1503"')
    row = body[start:body.index("</tr>", start)]
    assert 'action="/phones/1503/boot"' not in row, "not theirs to start"
    assert ">Done<" in row and ">Failed<" in row, "ending it still is"
    monkeypatch.setattr("geelark_farm.store.actions.record_refused",
                        lambda *a, **k: 88)
    status, headers, _ = admin.request(
        "POST", "/phones/1503/boot", _form(csrf=admin.csrf()))
    assert status == 303
    # Boot's form opens its Live tab, so that is where the answer is read.
    assert dict(headers)["Location"] == "/phones/1503/live?said=no:88"
    assert got == {}, "nothing was queued against ali's phone"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_phones_own_page_is_where_a_kept_phone_is_given_back(
        web, monkeypatch):
    """Release is the door that turns a phone kept for its maker back into
    the farm's: it clears the owner, and the keeper may finish it or send
    it an account again. Rare, so the table leaves it to this page - which
    also keeps Boot and Change IP."""
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": {"serial": serial, "status": "ready",
                                    "state": "", "owner": "mehdi",
                                    "built_by": "mehdi"},
        "timeline": []})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/phones/1504")

    for door in ("boot", "state", "proxy"):
        assert f'action="/phones/1504/{door}"' in body, door
    for label in ("Boot", "Release", "Done", "Failed", "Change IP"):
        assert f">{label}<" in body, label
    assert "on the shelf, kept for mehdi" in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_live_tab_writes_the_account_on_the_phone_beside_the_gmail(
        web, monkeypatch):
    """The Gmail was the whole margin, and a bare Spotify phone has no
    Gmail - so the phone whose only reason to exist is the account on it
    showed nothing at all beside the screen (the operator, 2026-09-19:
    "show the kind of account and its details too").

    Which product, which kind for a Spotify one, then the same three
    rows the Gmail gets - on their own ids, so the two boxes' show
    buttons do not reach into each other.
    """
    import geelark_farm.store.actions as actions_mod
    from geelark_farm.web import read as read_mod

    row = {"id": 72, "verb": "boot_phone", "status": "done", "result": "ok",
           "detail": {"state": "taken",
                      "url": "https://phone.geelark.com/i?t=abc"},
           "requested_by": 7}
    monkeypatch.setattr(actions_mod, "one", lambda s, aid: row)
    monkeypatch.setattr(read_mod, "gmail_on_phone", lambda s, serial: None)
    monkeypatch.setattr(read_mod, "account_on_phone", lambda s, serial: {
        "address": "jack@spotifylovers.biz", "password": "Spot!fy@123",
        "totp_secret": "", "product": "spotify", "category": "normal",
        "email_code_only": False})
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": {"serial": serial, "status": "ready",
                                    "state": "taken", "owner": "mehdi"},
        "timeline": []})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/phones/1500/live?said=queued:72")

    assert "<h3>Spotify account</h3>" in body
    assert "Spotify normal</span>" in body, "the kind, as the table says it"
    assert '<code id="gf-acct-addr">jack@spotifylovers.biz</code>' in body
    assert 'data-value="Spot!fy@123"' in body and "Spot!fy@123<" not in body
    assert 'data-show="gf-acct-pw"' in body
    # No Gmail on a bare phone, so no Gmail box - and the script is still
    # there for the one box that is.
    assert "<h3>On this phone</h3>" not in body
    assert "data-copy" in body and "navigator.clipboard.writeText" in body

    # Somebody else's phone: no margin at all, the same rule as the Gmail.
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": {"serial": serial, "status": "ready",
                                    "state": "taken", "owner": "ali"},
        "timeline": []})
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "sara", "role": "operator",
                         "sees": "all", "may_take_phones": True})
    other = web()
    other.login(username="sara")
    _, _, body = other.request("GET", "/phones/1500/live?said=queued:72")
    # By the heading, not the words: the stylesheet every page carries
    # has a comment with "Spotify account" in it.
    assert "<h3>Spotify account</h3>" not in body
    assert "Spot!fy@123" not in body and "gf-acct-addr" not in body


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_gpt_account_in_the_margin_is_named_for_its_own_product(
        web, monkeypatch):
    """A row with no product is a ChatGPT one - the pool's oldest
    default - and it has no kind, so no chip is drawn over it."""
    import geelark_farm.store.actions as actions_mod
    from geelark_farm.web import read as read_mod

    row = {"id": 73, "verb": "boot_phone", "status": "done", "result": "ok",
           "detail": {"url": "https://phone.geelark.com/i?t=abc"},
           "requested_by": 7}
    monkeypatch.setattr(actions_mod, "one", lambda s, aid: row)
    monkeypatch.setattr(read_mod, "gmail_on_phone", lambda s, serial: None)
    monkeypatch.setattr(read_mod, "account_on_phone", lambda s, serial: {
        "address": "buyer@example.com", "password": "", "totp_secret": "",
        "product": "", "category": "", "email_code_only": True})
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": {"serial": serial, "status": "ready",
                                    "state": "taken", "owner": "mehdi"},
        "timeline": []})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/phones/1500/live?said=queued:73")

    assert "<h3>ChatGPT account</h3>" in body
    assert "GPT</span>" in body, "the same chip the phones table draws"
    assert 'class="carries normal"' not in body, "no kind on a GPT row"
    assert "none on the row" in body, "no password, said rather than empty"
    assert "by a code emailed to it" in body


def test_the_gpt_sheet_sifts_standard_from_eco():
    """Two ways in, in one list, is a list you read twice: an eco
    account has no password and signs in by a code emailed to it, and
    knowing which a row is is the first thing anybody asks of this pool
    (the operator, 2026-09-19).

    The standard rows carry no word in the cell - they are what this
    pool has always held - so the chip and the row agree on `standard`
    rather than on the empty string, which is the `both` chip.
    """
    from geelark_farm.web import pages

    rows = [{"address": "a@x.com", "state": "free", "category": ""},
            {"address": "b@x.com", "state": "free", "category": "eco"},
            {"address": "c@x.com", "state": "used", "category": "eco"}]
    chips = pages._kind_chips("gpt", rows)
    assert 'data-cat=""' in chips and "both<b>3</b>" in chips
    assert 'data-cat="standard"' in chips and "standard<b>1</b>" in chips
    assert 'data-cat="eco"' in chips and "eco<b>2</b>" in chips

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "may_add_gpt": True}
    table = pages._pool_table("gpt", rows, user)
    assert 'data-cat="standard"' in table and 'data-cat="eco"' in table
    # The sifting is the script the Spotify sheet already uses; nothing
    # about it is per pool, so the same press works here.
    assert "&& (!cat || tr.dataset.cat === cat);" in pages._DASH_SCRIPT


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_route_will_not_take_a_kind_the_card_does_not_offer(
        web, monkeypatch):
    """The half after the colon becomes a pool row's Category. Unchecked,
    anything posted there stranded the account under a word nothing
    serves - and a kind the verb cannot act on was dropped in silence
    rather than refused (2026-09-19)."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 91)
    client = web()
    client.login()

    for kind in ("chatgpt:made_up", "spotify:", "nonsense", "claude:"):
        got.clear()
        status, _, _ = client.request(
            "POST", "/phones/build",
            _form(csrf=client.csrf(), gmail="a@example.com",
                  account_kind=kind, app_account="x@example.com"))
        assert status == 303
        assert not got, f"{kind} reached the builder"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_each_spotify_kind_is_offered_on_the_one_phone_it_belongs_on(
        web, monkeypatch):
    """A `normal` account wants a phone with no Google account and an
    `error` one wants a phone that has a Gmail, and the two do not
    overlap. Offered on both, `normal` sat in the list on every ordinary
    build and the server refused every press (2026-09-19)."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 92)
    client = web()
    client.login()

    # normal on a phone with a Gmail: refused before it can be spent.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="a@example.com",
                         account_kind="spotify:normal",
                         app_account="s@example.com"))
    assert not got, "a normal account must not be sent to a phone with a Gmail"

    # error on a bare phone: the same, the other way round.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="none",
                         account_kind="spotify:error",
                         app_account="s@example.com"))
    assert not got

    # normal on the phone it wants.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="none",
                         account_kind="spotify:normal",
                         app_account="s@example.com"))
    assert got["payload"]["app"] == "spotify"
    assert got["payload"]["app_category"] == "normal"
    got.clear()

    # error on a phone with a Gmail: refused too, and told where it goes.
    # A new build installed Spotify and said ready, the account never
    # signed in (the builder review, 2026-09-23; the operator chose the
    # refusal, 2026-09-24).
    from geelark_farm import verbs

    refused = []
    monkeypatch.setattr(actions_mod, "record_refused",
                        lambda s, **k: refused.append(k["reason"]) or 93)
    status, headers, _ = client.request(
        "POST", "/phones/build",
        _form(csrf=client.csrf(), gmail="a@example.com",
              account_kind="spotify:error", app_account="s@example.com"))
    assert not got, "an error account must not reach a new build"
    assert refused == [verbs.SPOTIFY_ERROR_ON_A_BUILD]
    assert "Send on its row" in refused[0]


def test_the_account_box_always_has_an_option_worth_nothing():
    """Cancel puts a select back to a value it held. With no such option
    the box landed on selectedIndex -1, posted nothing, and the form
    built a phone with a kind named and no account on it - an exit and a
    Gmail spent for a warm phone nobody asked for (2026-09-19)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "none.value = ''" in script, (
        "the rebuilt list needs an option worth nothing to fall back to")
    assert "acctPick.value = rows.length ? rows[0] : '';" in script
    # And the list is not rebuilt when the kind did not change, so a
    # chosen account survives a touch of the Gmail box.
    assert "if (kind === builtFor) return;" in script, (
        "changing the Gmail must not silently re-pick the account")


def test_the_secret_cell_names_all_three_kinds_and_says_when_a_row_has_two():
    """A Gmail answers with a key, with a recovery address, or with
    neither. A row holding both used to be shown as a recovery row,
    which is how a working key went unseen (2026-09-19)."""
    from geelark_farm.web import pages

    def word(**row):
        cell = pages._secret_cell(row)
        return cell.split('px">')[1].split("</span>")[0]

    assert word(totp_secret="JBSWY3DPEHPK3PXP") == "authenticator"
    assert word(recovery_email="keeper@x.com") == "recovery"
    assert word() == "no second factor"
    assert word(totp_secret="JBSWY3DPEHPK3PXP",
                recovery_email="keeper@x.com") == "authenticator + recovery"


def test_the_gmail_editor_offers_the_key_not_the_address():
    """It rewrites every cell it shows, so a box filled with the address
    of a row that also has a key saves the address over the key."""
    from geelark_farm.web import pages

    row = {"address": "both@x.com", "totp_secret": "JBSWY3DPEHPK3PXP",
           "recovery_email": "keeper@x.com", "seller": "", "status": ""}
    editor = pages._gmail_edit_row(
        {"id": 1, "csrf": "c", "role": "admin"}, row, "active", 6)
    assert "JBSWY3DPEHPK3PXP" in editor


# ------------------------------------------- what GeeLark says about itself
def _glark(**found):
    from geelark_farm.web import pages

    return pages._geelark_line(
        {"geelark": found},
        {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
         "is_admin": True, "may_login_accounts": True})


def test_the_foot_keeps_every_critical_reading_in_sight():
    """Every reading is on the page, not behind a hover.

    A line could not hold them: trimmed to fit it dropped numbers
    somebody wanted, and untrimmed it wrapped into a ragged second row
    and read as clutter (the operator, 2026-09-20). The grid holds all
    four with room under each for the thing that qualifies it, so
    nothing that matters lives in a `title` any more.
    """
    import datetime
    import time

    from geelark_farm.web import read

    ends = (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=25, hours=2))
    plan = {"plan": 1, "profiles": 40, "availableProfiles": 32,
            "parallels": 0, "monthlyFee": 26,
            "expirationTime": int(ends.timestamp())}
    line = _glark(plan=plan, at=time.time() - 300, phones_total=6,
                  phones_running=3,
                  trouble=read.geelark_trouble(plan, {}, {}))
    # Only what is drawn: attributes are struck out, so a reading that
    # is merely in a title does not count as shown.
    shown = re.sub(r"<[^>]*>", "\x00", line)

    for reading in (
            "GeeLark", "read 5m ago",
            # The money question, and the honest answer to it.
            "Balance", "unavailable", "waiting for wallet reading",
            # The [44002] ceiling, and whose phones are under it.
            "Phone slots", "8 / 40", "6 ours", "2 elsewhere",
            # What is costing money as it is read.
            "Running now", "billed by the minute",
            # The deadline, the count and the fee.
            "Subscription", f"{ends.day} {ends.strftime('%b %Y')}",
            "25 days left", "$26/mo"):
        assert reading in shown, f"{reading!r} is not on the page"

    # And on the one word that is always there, where all of it came
    # from and the caveat under the whole thing.
    tag = re.search(r'class="gltag" title="([^"]*)"', line).group(1)
    assert "0 parallel" in tag
    assert "/v1/pay/wallet" in tag
    assert "every five minutes" in tag


def test_the_subscription_reading_gives_the_date_the_count_and_the_cost():
    """Three facts about one deadline, and no arithmetic left for the
    reader: the old line printed `ends 16 Oct` and made them work out
    the rest, or dropped the count entirely when it was far off."""
    import datetime
    import time

    def foot(days):
        when = (datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(days=days, hours=2))
        plan = {"profiles": 40, "availableProfiles": 32, "monthlyFee": 26,
                "expirationTime": int(when.timestamp())}
        return _glark(plan=plan, at=time.time(), trouble=[]), when

    far, when = foot(60)
    assert f"{when.day} {when.strftime('%b %Y')}" in far
    assert "60 days left" in far, "the count is wanted whether or not it bites"
    assert "$26/mo" in far

    near, _ = foot(1)
    assert "1 day left" in near, "not `1 days left`"


def test_geelark_shouts_on_the_alert_strip_when_it_is_the_thing_that_is_wrong():
    """The foot of the page is where these numbers belong while nothing
    is the matter. When one of them is what stops the farm it belongs at
    the top, with everything else that has gone wrong (the operator,
    2026-09-20)."""
    import time

    from geelark_farm.web import read

    well = {"geelark_plan": {"plan": {"profiles": 40,
                                      "availableProfiles": 32,
                                      "expirationTime": 1792110224}}}
    assert read.geelark_alerts(well) == [], "quiet while all is well"

    refused = dict(well, geelark_refusal={
        "said": "start failed [41001] balance not enough",
        "at": time.time() - 60})
    raised = read.geelark_alerts(refused)
    assert [a["level"] for a in raised] == ["bad"]
    assert "will not start" in raised[0]["text"]
    assert "does not report the balance" in raised[0]["text"], (
        "it says why there was no earlier warning")

    full = {"geelark_plan": {"plan": {"profiles": 40,
                                      "availableProfiles": 0,
                                      "expirationTime": 1792110224}}}
    assert read.geelark_alerts(full)[0]["level"] == "bad"
    nearly = {"geelark_plan": {"plan": {"profiles": 40,
                                        "availableProfiles": 2,
                                        "expirationTime": 1792110224}}}
    assert read.geelark_alerts(nearly)[0]["level"] == "warn"


def test_a_subscription_running_out_is_raised_before_it_does():
    import datetime

    from geelark_farm.web import read

    def days_out(n):
        when = (datetime.datetime.now(datetime.timezone.utc)
                + datetime.timedelta(days=n, hours=1))
        return read.geelark_alerts(
            {"geelark_plan": {"plan": {"profiles": 40,
                                       "availableProfiles": 32,
                                       "expirationTime": int(when.timestamp())}}})

    assert days_out(30) == [], "a month out is not news"
    assert days_out(5)[0]["level"] == "warn"
    assert days_out(1)[0]["level"] == "bad"


def test_the_foot_is_quiet_until_something_is_wrong():
    """It sits at the foot of the page to be scrolled past, so nothing
    on it is coloured while nothing is the matter - and the whole of it
    colours at once when something is (the operator, 2026-09-20)."""
    import time

    from geelark_farm.web import read

    def foot(plan):
        return _glark(plan=plan, at=time.time(), phones_total=6,
                      phones_running=2,
                      trouble=read.geelark_trouble(plan, {}, {}))

    calm = foot({"profiles": 40, "availableProfiles": 32,
                 "expirationTime": 1792110224})
    assert 'class="glfoot"' in calm, "no grade at all while all is well"
    assert 'class="glcell bad"' not in calm
    assert 'class="glcell warn"' not in calm

    # And it grades by the same judgement the alert strip uses, so the
    # two cannot disagree - on the block, which carries the hairline,
    # the dot and the wordmark, and on the reading that is wrong.
    nearly = foot({"profiles": 40, "availableProfiles": 2})
    assert 'class="glfoot warn"' in nearly
    assert 'class="glcell warn"' in nearly
    gone = foot({"profiles": 40, "availableProfiles": 0})
    assert 'class="glfoot bad"' in gone
    assert 'class="glcell bad"' in gone


def test_a_phone_refused_for_an_empty_account_is_said_in_geelarks_own_words():
    """The API has no balance in it, so a refusal is the only reading
    there is - and it lived in a log line nobody was watching while
    nineteen builds were turned down (2026-09-19). It is the first
    reading on the foot, because it is the one that stops the farm."""
    import time

    from geelark_farm.web import read

    plan = {"profiles": 40, "availableProfiles": 32}
    refused = {"said": "start failed [41001] balance not enough",
               "at": time.time() - 60}
    line = _glark(plan=plan, at=time.time(),
                  trouble=read.geelark_trouble(plan, refused, {}))
    shown = re.sub(r"<[^>]*>", "\x00", line)

    assert "out of credit" in shown
    assert "balance not enough" in shown, "GeeLark's own words, on the page"
    assert 'class="glfoot bad"' in line
    assert line.index("Balance") < line.index("Phone slots"), (
        "the reading that stops the farm comes first")


def test_a_refusal_stops_being_news_when_a_phone_comes_up():
    """A clock was the wrong test for whether a refusal is still true.

    It was an hour, and an hour was wrong in both directions on the
    same day: it dropped an empty account that was still empty, and it
    went on saying `out of credit` three minutes after forty-seven
    phones had been built (the operator, 2026-09-20). A phone being
    created is the one thing that retires a refusal, because a refusal
    is the last word on creating phones until one is created.
    """
    import time

    from geelark_farm.web import read

    plan = {"profiles": 40, "availableProfiles": 40}
    now = time.time()
    refused = {"said": "start failed [41001] balance not enough",
               "code": 41001, "msg": "balance not enough", "at": now - 90}

    def foot(built_at):
        return _glark(plan=plan, at=now, refusal=refused["said"],
                      refused_at=refused["at"],
                      trouble=read.geelark_trouble(plan, refused, {},
                                                   built_at))

    # A phone that came up after it: whatever was wrong is over.
    cleared = foot(now - 30)
    assert "balance not enough" not in cleared
    assert "unavailable" in cleared, "and it says so rather than nothing"
    assert 'class="glfoot bad"' not in cleared

    # One that came up before it says nothing about it either way.
    assert "balance not enough" in foot(now - 600)
    assert "balance not enough" in foot(None), "no phone ever built"


def test_a_money_refusal_outlives_the_clock():
    """An account that was empty two hours ago is empty now: nothing has
    been built since to say otherwise. The hour-long window dropped it
    and the foot went grey while the farm was stopped dead."""
    import time

    from geelark_farm.web import pages, read

    plan = {"profiles": 40, "availableProfiles": 40}
    old = time.time() - pages.REFUSAL_SHOWN_FOR - 7200
    refused = {"said": "start failed [41001] balance not enough",
               "code": 41001, "msg": "balance not enough", "at": old}
    line = _glark(plan=plan, at=time.time(), refusal=refused["said"],
                  refused_at=old,
                  trouble=read.geelark_trouble(plan, refused, {}))

    assert "out of credit" in line
    assert 'class="glfoot bad"' in line


def test_only_geelarks_own_words_make_it_the_money():
    """`out of credit` in red while forty-seven phones were being built,
    on a day whose one refusal was a proxy GeeLark could not check
    (the operator, 2026-09-20). Every refusal wears the same shape;
    only the code says which problem it is."""
    import time

    from geelark_farm.web import read

    plan = {"profiles": 40, "availableProfiles": 32}
    now = time.time()
    proxy = {"said": "creation failed [45004] check proxy failed",
             "code": 45004, "msg": "check proxy failed", "at": now - 300}
    line = _glark(plan=plan, at=now, refusal=proxy["said"],
                  refused_at=proxy["at"], phones_total=11,
                  trouble=read.geelark_trouble(plan, proxy, {}))

    assert "out of credit" not in line, "the account was never asked about"
    assert "unavailable" in line, "the balance reading is still unread"
    # It gets a reading of its own, and amber: one build turned down
    # among many is worth seeing and is not a stop.
    assert "Last refusal" in line
    assert "check proxy failed" in line
    assert "[45004]" in line
    assert 'class="glfoot warn"' in line
    assert 'class="glfoot bad"' not in line

    # And the sentence at the top does not prescribe a top-up for it.
    told = read.geelark_alerts({"geelark_plan": {"plan": plan},
                                "geelark_refusal": proxy, "pulse": {}})
    assert "topped up" not in told[0]["text"]
    assert "check proxy failed" in told[0]["text"]


def test_a_row_written_before_the_code_was_kept_apart_still_reads():
    """Rows already in `service_state` have the whole payload in `said`
    and no code beside it."""
    import time

    from geelark_farm.web import read

    plan = {"profiles": 40, "availableProfiles": 32}
    legacy = {"said": "start failed [41001] balance not enough",
              "at": time.time() - 120}
    assert read._is_about_money(legacy)
    assert not read._is_about_money(
        {"said": "creation failed [45004] check proxy failed"})


def test_building_having_stopped_is_a_reading_of_its_own():
    """The breaker up on refusals no pool can fix is not a fact about
    the money - but it is a stop, so it is red and it says so."""
    import time

    from geelark_farm.web import read

    plan = {"profiles": 40, "availableProfiles": 32}
    line = _glark(plan=plan, at=time.time(), phones_total=11,
                  trouble=read.geelark_trouble(
                      plan, {}, {"tripped": True,
                                 "breaker_reasons":
                                     ["phone_would_not_start"] * 5}))

    assert "Building" in line and "stopped" in line
    assert 'class="glfoot bad"' in line
    assert "out of credit" not in line, "nothing said the account is empty"


def test_a_reading_the_keeper_has_stopped_refreshing_says_so():
    """Numbers that look like readings and are memories are worse than
    no numbers. The plan is read every few minutes, so one an hour old
    means nothing is reading it - and then the four above it are the
    last thing known, not the state of the account."""
    import time

    from geelark_farm.web import read

    plan = {"profiles": 40, "availableProfiles": 32,
            "expirationTime": 1792110224}
    fresh = _glark(plan=plan, at=time.time(), trouble=[])
    assert 'class="glage"' in fresh and 'class="glfoot"' in fresh

    old = _glark(plan=plan, at=time.time() - read.READING_STALE_AFTER - 60,
                 trouble=[])
    assert 'class="glage warn"' in old
    assert 'class="glfoot warn"' in old, "the whole block, not the stamp alone"


def test_trouble_no_reading_speaks_for_still_reaches_the_foot():
    """The promise the arrangement rests on is that the foot says
    whatever the alert strip says. A kind added to
    `read.geelark_trouble` and forgotten here would break it in
    silence, so anything unspoken for gets a reading of its own."""
    import time

    line = _glark(plan={"profiles": 40, "availableProfiles": 32},
                  at=time.time(),
                  trouble=[{"kind": "weather", "level": "bad",
                            "href": "/", "short": "the datacentre is down",
                            "text": "Nobody can reach the phones."}])

    assert "the datacentre is down" in line
    assert 'class="glfoot bad"' in line


def test_the_line_says_nothing_when_the_keeper_has_not_read_the_plan_yet():
    assert _glark() == ""


def test_the_geelark_line_is_at_the_foot_and_not_in_the_rail():
    """Asked for there: it is the ground the farm stands on, not
    anybody's work, and it should cost no room until it is wanted."""
    import inspect

    from geelark_farm.web import pages

    body = inspect.getsource(pages.dashboard)
    rail = body.index('class="rail"')
    foot = body.index("_geelark_line(data, user)")
    assert foot > body.index("_pool_manager("), "it goes after the page"
    assert "_geelark_line" not in body[rail:body.index('</div>', rail)]


def test_the_breaker_does_not_send_you_to_a_pool_that_is_full():
    """Its one red line said "Add fresh stock" whatever had happened. On
    the night GeeLark's balance ran out that was six builds refused by
    somebody else's billing, and it sent the operator to a pool that was
    perfectly full (2026-09-19)."""
    from geelark_farm.web import read

    def advice(*reasons):
        return read._what_to_do({"breaker_reasons": list(reasons)})

    # Nothing the pool can fix.
    said = advice("phone_would_not_start", "phone_would_not_start")
    assert "will not help" in said and "Add fresh stock" not in said
    # The case the old sentence was written for.
    assert "Add fresh stock" in advice("wrong_password", "wrong_password")
    # A mixture is not either one.
    mixed = advice("wrong_password", "phone_would_not_start")
    assert "some of it is not" in mixed
    # And every one of them still says how to clear it.
    for said in (advice(), advice("wrong_password"),
                 advice("phone_would_not_start")):
        assert "Clear breaker" in said


def test_the_foot_is_never_grey_while_the_top_is_red():
    """The invariant the whole arrangement rests on. Somebody glancing
    at the bottom of the console and seeing nothing but grey concluded
    GeeLark was in its normal state, on a night the account had no
    money in it (the operator, 2026-09-20).

    Both are drawn from `read.geelark_trouble`, so this asks that the
    rendering keeps the promise the shared source makes - and that it
    keeps it on the block itself, which is what carries the hairline,
    the dot and the wordmark that a glance actually lands on.
    """
    import time

    from geelark_farm.web import pages, read

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "is_admin": True, "may_login_accounts": True}
    plan = {"plan": 1, "profiles": 40, "availableProfiles": 32,
            "parallels": 0, "monthlyFee": 26,
            "expirationTime": 1792110224}
    now = time.time()
    states = {
        "all well": (plan, {}, {}),
        "refused just now": (plan, {"said": "balance not enough",
                                    "at": now}, {}),
        # The one that caught us: no refusal was ever recorded, because
        # once the breaker is up nothing tries to start a phone.
        "breaker, and no refusal on file": (
            plan, {}, {"tripped": True,
                       "breaker_reasons": ["phone_would_not_start"] * 5}),
        "slots gone": (dict(plan, availableProfiles=0), {}, {}),
        "slots nearly gone": (dict(plan, availableProfiles=2), {}, {}),
        "plan about to end": (
            dict(plan, expirationTime=int(now + 86400)), {}, {}),
    }
    for name, (p, refused, pulse) in states.items():
        trouble = read.geelark_trouble(p, refused, pulse)
        line = pages._geelark_line(
            {"geelark": {"plan": p, "at": now, "phones_total": 6,
                         "phones_running": 0, "trouble": trouble}}, user)
        loud = ("bad" if 'class="glfoot bad"' in line
                else "warn" if 'class="glfoot warn"' in line else "")
        if not trouble:
            assert not loud, f"{name}: shouted about nothing"
            continue
        worst = "bad" if any(t["level"] == "bad" for t in trouble) else "warn"
        assert loud == worst, (
            f"{name}: the top reads {worst} and the foot reads "
            f"{loud or 'grey'}")
        # The grade is on the block, so it reaches the dot and the
        # wordmark as well as the reading that is wrong - a glance at
        # the corner is enough.
        assert '<span class="gldot"></span>' in line
        assert f'class="glcell {worst}"' in line, (
            f"{name}: nothing said which reading it was")


# ---------------------------------------- a press says what it did (step 1)
def test_every_word_a_press_can_answer_with_has_a_sentence():
    """The one test that stops this whole class coming back.

    `back` is the request's own field, so a press made in the pool
    manager can land on the dashboard or on a pool page, and every table
    a `back` can reach has to know every word `_act` can say. `no` was
    missing from `_POOL_SAID`, so `_said` returned "" and a refusal on a
    pool page drew no banner at all - the press looked like it had done
    nothing (the operator, 2026-09-20).
    """
    import inspect
    import re

    from geelark_farm.web import app, pages

    act = inspect.getsource(app._Handler._act)
    # What `_act` itself can answer with, read off its own source rather
    # than listed here: a list is a second place to remember.
    # The words `_act` settles on, whether it hands them straight to
    # `_said_url` or names them first - it builds `said` and then either
    # answers with one row or redirects (2026-09-21).
    words = set(re.findall(r'_said_url\(back, f?"([a-z][a-z-]*)(?::|")', act))
    words |= set(re.findall(r'said = f?"([a-z][a-z-]*)(?::|")', act))
    words.add("done")                    # the default `said_word`
    # And the words a caller passes for a press that means something
    # more particular than "done".
    words |= set(re.findall(r'said_word="([a-z-]+)"',
                            inspect.getsource(app)))

    assert {"refused", "no", "queued", "already", "twice"} <= words, (
        "the reader above stopped finding what _act answers with")
    missing = sorted(w for w in words if w not in pages._DASH_SAID)
    assert not missing, f"no sentence on the dashboard for {missing}"
    # A pool press posts its own `back`, so the pool tables must know
    # everything `_act` can say. The two that are fixed to a phone page
    # or to "/" are the exceptions, named so they cannot grow quietly.
    lands_on_a_pool = words - {"asked", "cancelled", "dismissed"}
    gaps = sorted(w for w in lands_on_a_pool if w not in pages._POOL_SAID)
    assert not gaps, f"no sentence on the pool pages for {gaps}"


def test_the_verbs_own_sentence_survives_a_press_that_worked():
    """free_gmail settles "x@y is back on the shelf" and the operator was
    shown "Done - it is already in." - a sentence about pasting stock -
    because the note was read back only when the word was `no` (the
    operator, 2026-09-20)."""
    import inspect

    from geelark_farm.web import app

    said = inspect.getsource(app._Handler._said_note)
    assert 'if word != "no" or not req.isdigit():' not in said, (
        "a press that WORKED has a sentence worth reading too")
    assert "if not req.isdigit():" in said, (
        "a token with no request id has no row to read")


def test_the_pool_pages_are_handed_the_sentence_and_the_reader():
    """`_said` prefers the verb's own words and needs the reader to draw
    an Undo. All four pool pages were called with neither."""
    import inspect

    from geelark_farm.web import app, pages

    for name in ("gmail_pool_page", "gpt_pool_page", "proxy_pool_page",
                 "needs_page"):
        fn = getattr(pages, name)
        assert "said_note" in inspect.signature(fn).parameters, name
        body = inspect.getsource(fn)
        assert "_said(said, _POOL_SAID, user, said_note)" in body, name

    # Four pool pages, plus the dashboard, which has had it all along -
    # and the pool pages' one-row answer (2026-09-22).
    handler = inspect.getsource(app)
    assert handler.count("said_note=self._said_note(") == 6, (
        "every pool page is handed the settled row's own sentence")


def test_a_banner_is_dressed_wherever_it_arrives():
    """`up` is the only rule that lifts a banner out of the page and over
    the manager's backdrop. It lived inside `init()`, and `sayIt` runs
    after `swapRow` has already called `init()` - so a one-row press drew
    its answer undressed, behind a 72%-black backdrop: rendered
    perfectly, invisible perfectly (the operator, 2026-09-20)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "function dressToast(said){" in script
    say = script[script.index("function sayIt(doc){"):]
    say = say[:say.index("\n  }")]
    assert "dressToast(said);" in say, "the one that has no next init()"
    # And still from init, for a banner the server sent with the page.
    assert "dressToast(document.querySelector('.said.toast'));" in script


def test_a_row_the_press_moved_says_where_it_went():
    """Free takes an errored Gmail to `current`, and the sift at the end
    of `init()` hid it on the spot - the row simply vanished from under
    the chip being looked at."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "moved(theirs, sheet);" in script
    moved = script[script.index("function moved(tr, sheet){"):]
    assert "tr.classList.add('moved');" in moved
    assert "'now under '" in moved
    # Kept for one sift, not exempted: the next one files it away.
    assert "var went = tr.classList.contains('moved');" in script
    assert "if (went) tr.classList.remove('moved');" in script


def test_the_slow_looking_doors_say_what_they_are_doing():
    """A Test all said nothing for sixteen calls and read as a hang; the
    account pools' own doors had never been given the same treatment."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "is_admin": True, "may_add_gmail": True, "may_add_gpt": True}
    doors = pages._pool_row_doors(
        "gmail", {"address": "x@y.com", "state": "set aside"}, user)
    assert 'data-busy="Freeing&hellip;"' in doors
    assert 'data-busy="Removing&hellip;"' in doors
    assert 'data-busy="Saving&hellip;"' in pages._pool_editor(
        "gmail", user, [{"address": "x@y.com", "seller": "LEO"}])


# ------------------------------ the editor keeps what was typed (step 2)
def test_a_click_on_the_dark_asks_before_it_throws_work_away():
    """`showModal` puts the dialog in the top layer, so a click anywhere
    in the ~85% of the screen outside a 560px box hit-tests to the
    <dialog> itself - and that closed the editor and discarded minutes of
    correction, with no question and no way back (the operator,
    2026-09-20)."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert ("if (e.target.matches('dialog.editor')) {\n"
            "      askToDrop" in script)
    ask = script[script.index("function askToDrop(dlg){"):]
    ask = ask[:ask.index("\n  }")]
    assert "if (!editorDirty(dlg)) { closeEditor(dlg); return; }" in ask, (
        "nothing typed, nothing to ask about")
    assert "'Throw away the changes to '" in ask
    # Escape is the same hand, one key over.
    assert "dlg.addEventListener('cancel', function(ev){" in script
    assert "ev.preventDefault();\n        askToDrop(dlg);" in script


def test_the_editor_keeps_a_draft_of_what_was_not_saved():
    """openEditor was a pure refill from the row's data attributes, so
    whatever closed the dialog took the typing with it."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "var drafts = {};" in script
    assert "keepDraft(dlg);" in script
    assert "drafts[draftKey(dlg, address)]" in script, "offered back on reopen"
    # Gone when the work went through, and when they said throw it away.
    drop = script[script.index("function dropEditor(dlg){"):]
    drop = drop[:drop.index("\n  }")]
    assert "delete drafts[draftKey(" in drop
    # A dialog the editor did not put the value in must not be told it
    # was typed in: only a real input event sets dirty.
    assert "dlg.addEventListener('input', function(){" in script


def test_the_editor_is_told_why_a_save_was_refused():
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "is_admin": True, "may_add_gmail": True}
    editor = pages._pool_editor("gmail", user, [{"address": "x@y.com"}])
    assert '<p class="editsay" hidden></p>' in editor, "a place for it inside"
    says = pages._DASH_SCRIPT
    assert "function editorSays(dlg, doc, words, bad){" in says
    assert "slot.classList.toggle('bad', bad !== false);" in says


# -------------------------------- the guards that were not there (step 3)
def test_a_typing_hand_actually_holds_the_redraw():
    """`var typing` was worked out and then nothing read it, so from
    September the guard its own comment describes did not exist: every
    page but the dashboard swapped under a typing hand on the
    four-second floor (the operator, 2026-09-20)."""
    from geelark_farm.web import pages

    gate = pages._DASH_SCRIPT[
        pages._DASH_SCRIPT.index("function mayRedraw(){"):]
    gate = gate[:gate.index("\n  }")]
    assert "var typing = !!live" in gate
    assert "if (typing) return false;" in gate, (
        "computed and dropped on the floor is how it shipped")
    assert gate.index("var typing") < gate.index("if (typing)")
    # And nothing else in there is worked out and never read.
    assert "var o = ov();" not in gate


def test_an_open_dialog_is_held_for_as_long_as_it_is_open():
    """`.mini` and `dialog[open]` sat inside `mayRedraw`, which `settled`
    overrides after HELD_CEILING - so an unanswered question and an
    editor full of unsent typing each got twenty seconds."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    held = script[script.index("function settled(){"):]
    held = held[:held.index("\n  }")]
    assert "if (heldOpen() || busyHere())" in held
    assert held.index("busyHere()") < held.index("HELD_CEILING"), (
        "above the ceiling, not under it")


def test_the_caret_comes_back_where_it_was():
    """Six routines put the page back together after a swap and not one
    of them ever kept the cursor: the values returned and the caret went
    to the top, which reads as being thrown out mid-word."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "kept.at = {key: whichField(live)};" in script
    assert "kept.at.from = live.selectionStart;" in script
    back = script[script.index("function typedBack(kept){"):]
    assert "box.focus({preventScroll: true});" in back
    assert "box.setSelectionRange(kept.at.from, kept.at.to);" in back
    # Before the values, so a handler they wake cannot steal the focus.
    assert back.index("box.focus(") < back.index("if (!kept.length) return;")


def test_a_select_put_back_says_so():
    """Setting `.value` fires nothing, so the build card's own gate never
    heard the Gmail come back and left the account box hidden and
    disabled - and Build then posted a kind with no account on it."""
    from geelark_farm.web import pages

    back = pages._DASH_SCRIPT[
        pages._DASH_SCRIPT.index("function typedBack(kept){"):]
    back = back[:back.index("\n  function ")]
    assert "if (el.tagName === 'SELECT') told.push(el);" in back
    assert ("told.forEach(function(el){"
            " el.dispatchEvent(new Event('change')); });" in back)


# ----------------------- the page links its assets rather than carrying them
def test_the_page_links_the_assets_and_carries_neither(web, monkeypatch):
    """130,569 bytes of stylesheet and script went out with every single
    response, unchanged, uncacheable, on a page the browser re-fetches
    every few seconds while the farm builds (2026-09-20)."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    assert f'<link rel="stylesheet" href="{assets.CSS_PATH}">' in body
    assert f'<script src="{assets.JS_PATH}" defer></script>' in body
    assert "<style>" not in body, "the stylesheet is not in the page"
    assert "(function(){" not in body, "and neither is the script"
    # Both names are the content's own hash, so a deploy invalidates the
    # cache by construction.
    assert assets.REV in assets.CSS_PATH and assets.REV in assets.JS_PATH


def test_an_asset_is_served_exactly_and_only_under_this_builds_name(
        web, monkeypatch):
    _dash(monkeypatch)
    client = web()
    client.login()

    def cache(head):
        return dict((k.lower(), v) for k, v in head).get("cache-control", "")

    status, head, body = client.request("GET", assets.JS_PATH)
    assert status == 200
    assert body == assets.JS, "the served script is the file on disk"
    assert "immutable" in cache(head)
    assert "private" in cache(head), (
        "the script is what each press does; it waits for a session")

    status, head, body = client.request("GET", assets.CSS_PATH)
    assert status == 200 and body == assets.CSS
    assert "public" in cache(head), (
        "the sign-in page needs the stylesheet and has no session yet")

    # A name this build does not answer to is a 404, not a redirect to
    # the current one: a page asking for a stale name is a stale page.
    for wrong in ("/s/deadbeefcafe.js", "/s/deadbeefcafe.css",
                  "/s/../pages.py.js"):
        status, _, _ = client.request("GET", wrong)
        assert status == 404, wrong


def test_the_stylesheet_is_reachable_without_a_session(web):
    """The sign-in page needs it before anybody has one."""
    status, _, body = web().request("GET", assets.CSS_PATH)
    assert status == 200 and body == assets.CSS
    _, _, login = web().request("GET", "/login")
    assert assets.CSS_PATH in login


def test_the_script_is_not_handed_out_before_a_session(web):
    status, _, _ = web().request("GET", assets.JS_PATH)
    assert status in (302, 303), (
        "which endpoints exist and what each press does is not public")


def test_the_fold_arrows_are_arrows_and_not_a_control_character():
    r"""`content:" \2304"` is a CSS escape for the fold arrow, and `\230`
    is an OCTAL escape in a non-raw Python string - so for as long as
    the stylesheet lived inside pages.py the browser was handed U+0098,
    a control character, where the arrow belonged. Out in a .css file
    there are no Python escapes to apply (2026-09-20).

    Raw strings below, deliberately: writing this test any other way
    reproduces the bug inside the test, which is how it was written the
    first time.
    """
    assert r"\2304" in assets.CSS and r"\2303" in assets.CSS
    assert "\x98" not in assets.CSS, "a Python escape ate a CSS one"


def test_the_script_has_nothing_in_it_that_is_never_read():
    """The check that would have caught it.

    `var typing` was worked out in `mayRedraw` and then nothing read it,
    so the guard that holds a redraw while somebody is typing was dead
    from September to 2026-09-20 - under a green test asserting
    `"function mayRedraw()" in script`. A substring cannot see an unused
    variable.

    Counting names over the whole file cannot see it either: there is a
    second `typing` in the keydown handler, and between them the name
    occurs plenty. So this counts inside the block the `var` is actually
    in - which is where the language decides the question too.
    """
    import re

    # Comments and string bodies out first: a name inside either is not
    # a use of it.
    bare = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group()), assets.JS,
                  flags=re.S)
    bare = re.sub(r"//[^\n]*", lambda m: " " * len(m.group()), bare)
    bare = re.sub(r"'(?:[^'\\\n]|\\.)*'",
                  lambda m: "'" + " " * (len(m.group()) - 2) + "'", bare)
    bare = re.sub(r'"(?:[^"\\\n]|\\.)*"',
                  lambda m: '"' + " " * (len(m.group()) - 2) + '"', bare)

    # Where every brace's partner is, so a declaration can be asked what
    # block it is in.
    closes, stack = {}, []
    for i, ch in enumerate(bare):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            closes[stack.pop()] = i

    opens = sorted(closes)
    dead = []
    for hit in re.finditer(r"\bvar\s+([A-Za-z_$][\w$]*)\s*=", bare):
        name, at = hit.group(1), hit.start()
        # The innermost block this declaration sits in.
        block = None
        for o in opens:
            if o < at < closes[o] and (block is None or o > block):
                block = o
        span = bare[block:closes[block]] if block is not None else bare
        if len(re.findall(rf"\b{re.escape(name)}\b", span)) == 1:
            dead.append((name, bare[:at].count("\n") + 1))
    assert not dead, (
        "worked out and never read - and the console shipped a month "
        f"like this once: {sorted(dead)}")

def test_the_script_parses(tmp_path):
    """One bad character in this file is a console with no working
    buttons at all, drawn perfectly (the operator, 2026-09-14: "Manage
    does nothing")."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed; the script is unchecked here")
    path = tmp_path / "dash.js"
    path.write_text(assets.JS, encoding="utf-8")
    done = subprocess.run([node, "--check", str(path)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr.strip()


def test_the_assets_ride_in_the_wheel():
    """An editable install reads them off the tree; a wheel has to carry
    them or the console renders as plain text with dead buttons."""
    import pathlib
    import tomllib

    here = pathlib.Path(assets.__file__).resolve()
    conf = tomllib.loads(
        (here.parents[3] / "pyproject.toml").read_text(encoding="utf-8"))
    data = conf["tool"]["setuptools"]["package-data"]
    assert "static/*" in data.get("geelark_farm.web", []), (
        "the stylesheet and the script are not packaged")


# ------------------------------- the console can say that it broke (step 6)
def test_a_throw_in_the_console_reaches_the_log(web, monkeypatch, caplog):
    """There was no channel at all by which the console could report
    that it had broken: a throw leaves the page looking perfectly
    ordinary with half its buttons dead, and the only way anybody has
    ever found out is the operator saying so (2026-09-20)."""
    import logging

    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    token = re.search(r'name="csrf" value="([^"]*)"', body).group(1)

    with caplog.at_level(logging.WARNING, logger="geelark_farm.web.app"):
        status, _, _ = client.request(
            "POST", "/clienterror",
            body=f"csrf={token}&message=boom&where=/pools/gmail"
                 f"&rev=abc123&stack=at+swapMain")
    assert status == 204, "the page has lost its footing; do not answer it"
    said = "\n".join(r.getMessage() for r in caplog.records)
    assert "console broke for mehdi on /pools/gmail: boom" in said
    assert "abc123" in said and "at swapMain" in said


def test_a_report_without_the_pages_token_is_refused(web, monkeypatch):
    _dash(monkeypatch)
    client = web()
    client.login()
    status, _, _ = client.request("POST", "/clienterror",
                                  body="message=boom&csrf=wrong")
    assert status == 403


def test_every_request_is_logged_with_what_it_cost(web, monkeypatch, caplog):
    """The capture starts at INFO and request lines went to DEBUG, so
    not one request has ever reached the log table - and "the console
    feels slow" had no number anywhere to check it against."""
    import logging

    _dash(monkeypatch)
    client = web()
    client.login()
    with caplog.at_level(logging.INFO, logger="geelark_farm.web.app"):
        client.request("GET", "/phones")
        # The line is written when the answer is out, not when it is
        # begun - that is the only moment its size is known - so it
        # lands on the server thread a beat after the client has its
        # response. Waited for rather than assumed.
        lines = []
        for _ in range(100):
            lines = [r.getMessage() for r in caplog.records
                     if r.getMessage().startswith("web GET")]
            if lines:
                break
            time.sleep(0.02)
    assert lines, "no request line at INFO"
    assert any("/phones" in x and "ms" in x and "bytes" in x for x in lines), (
        lines)
    # With a real byte count on it: `send_response` calls `log_request`
    # before a single header has gone out, so the line said `0 bytes`
    # for every request on the day it was added (2026-09-21).
    import re as _re

    sizes = [int(m.group(1)) for x in lines
             if (m := _re.search(r"(\d+) bytes", x))]
    assert any(n > 100 for n in sizes), f"every answer weighed nothing: {lines}"
    # Not the stream: one connection held open for hours, whose line
    # would say nothing true about how long anything took.
    assert not any("/live" in x for x in lines)


def test_a_throw_after_the_write_does_not_send_the_form_again():
    """The submit chain's `catch` covered the whole response handler and
    its recovery is a second, native POST - so a throw in `swapMain`
    AFTER the write had gone through sent the same command twice and
    navigated the page away (2026-09-20)."""
    js = assets.JS
    assert "var sent = false;" in js
    assert "sent = true;" in js
    tail = js[js.index(".catch(function(err){"):]
    tail = tail[:tail.index("\n      });")]
    assert "if (sent) {" in tail
    assert tail.index("if (sent) {") < tail.index("form.submit();"), (
        "only the request failing is a reason to send it again")
    assert "report(" in tail and "toast(" in tail


def test_both_fetch_handlers_refuse_a_bad_answer_the_same_way():
    """They had drifted: `reload` refused a non-OK answer and the submit
    handler did not, so a 500 from a verb had its error page parsed and
    installed inside the pool sheet."""
    js = assets.JS
    assert js.count("function answer(r, say){") == 1
    # Two: the background reload, and the open sheet's conditional look
    # with its stamp (2026-09-21) - neither is somebody waiting on a
    # click, so neither toasts a refusal.
    assert js.count("answer(r, false)") == 2, "the background refreshes"
    # The press and the sheet fetch: both say so when the server
    # refuses, because both are somebody waiting on a click.
    # Three: the press, the sheet fetch, and the editor's credentials -
    # each somebody waiting on a click, each told when the server refuses.
    assert js.count("answer(r, true)") == 3
    # Two: the background reload, and the open sheet's conditional look
    # with its stamp (2026-09-21) - neither is somebody waiting on a
    # click, so neither toasts a refusal.
    assert js.count("answer(r, false)") == 2, "the background refreshes"
    assert r"if (r.redirected && /\/login(\?|$)/.test(r.url)) {" in js


# --------------------------------- where a press comes back to (step 9)
def test_a_back_is_rebuilt_from_its_parts_not_matched_whole():
    """A whole-string allowlist can only hold the addresses somebody
    thought to write down; everything else was silently replaced by the
    default. Press Paid on a list filtered to one seller and you landed
    on the unfiltered queued view (2026-09-20)."""
    from geelark_farm.web import app as app_mod

    def back(asked, default="/pools/gmail"):
        return app_mod._back_to({"back": asked}, default)

    # What the allowlist could not hold, and now survives.
    assert back("/pools/gmail?view=errored&seller=LEO%2018SEP") == (
        "/pools/gmail?view=errored&seller=LEO+18SEP")
    assert back("/pools/gmail?view=queued&page=3") == (
        "/pools/gmail?view=queued&page=3")
    assert back("/pools/proxy?view=needs_hand&q=US25") == (
        "/pools/proxy?view=needs_hand&q=US25")

    # And what it must still refuse. A rebuild is stricter than a match,
    # not looser: an unknown path, an unknown parameter, a view this
    # page does not have, a page number that is not a number.
    assert back("https://elsewhere.example/x") == "/pools/gmail"
    assert back("/pools/gmail?view=queued&evil=1") == (
        "/pools/gmail?view=queued")
    assert back("/pools/gmail?view=nonesuch") == "/pools/gmail"
    assert back("/pools/gmail?page=notanumber") == "/pools/gmail"
    assert back("//evil.example") == "/pools/gmail"
    assert back("/pools/gmail?edit=3&edit=9") == "/pools/gmail?edit=3"
    assert back("") == "/pools/gmail"
    # The same request always rebuilds to the same address, whatever
    # order it arrived in - which is what the idempotency key wants.
    assert back("/pools/gmail?page=2&view=used") == back(
        "/pools/gmail?view=used&page=2")


def test_an_errored_gmail_can_be_edited_and_freed_where_it_is_listed():
    """A row Google refused is exactly the row somebody wants to
    correct, and the only place to do it was the dashboard's overlay -
    where a click on the dark area threw the correction away (the
    operator, 2026-09-20)."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "is_admin": True, "may_add_gmail": True, "sees": "all",
            "username": "mehdi", "nav": {}}
    row = {"id": 41, "address": "torn@gmail.com", "status": "no_authenticator",
           "updated_at": "2026-09-19 10:00:00+00", "seller": "LEO",
           "totp_secret": "", "recovery_email": "", "password": "p"}
    data = {"rows": [row], "view": "errored", "counts": {}, "seller": "LEO",
            "page": 2, "pages": 3, "known_sellers": ["LEO"]}

    drawn = pages.gmail_pool_page(data, user, advice=lambda s: None)
    assert "Free</button>" in drawn, "it can go back on the shelf"
    assert ">Edit</a>" in drawn, "and it can be corrected first"
    # Carrying where the person is, so the press comes back to it.
    assert "/pools/gmail?view=errored&amp;seller=LEO&amp;page=2" in drawn

    # And Edit on this page opens the row on this page.
    opened = pages.gmail_pool_page(data, user, advice=lambda s: None,
                                   editing=41)
    assert 'class="editrow"' in opened
    assert 'action="/pools/gmail/edit"' in opened


def test_the_script_is_tested_by_running_it(tmp_path):
    """The suite reads this file's source and asserts substrings of it.
    That is how `var typing` came to be computed in `mayRedraw` and
    never read for a month, under a green test asserting
    `"function mayRedraw()" in script` (2026-09-20).

    `tests/dash/` runs it. Skipped where node and its one dependency
    are not installed - `npm install` puts them there - because the farm
    itself needs neither.
    """
    import pathlib
    import shutil
    import subprocess

    root = pathlib.Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if not node or not (root / "node_modules" / "linkedom").exists():
        pytest.skip("node/linkedom not installed; run `npm install`")
    done = subprocess.run([node, "--test", "tests/dash/"], cwd=root,
                          capture_output=True, text=True)
    assert done.returncode == 0, (
        "the console's script does not behave:\n"
        + done.stdout[-4000:] + done.stderr[-2000:])


# --------------------------- the drawer is fetched when it is pulled (step 7)
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_pool_sheet_is_a_fragment_and_not_a_page(web, monkeypatch):
    _dash(monkeypatch)
    client = web()
    client.login()
    status, _, body = client.request("GET", "/pools/gmail/sheet")

    assert status == 200
    assert body.startswith(
        '<section class="sheet" data-sheet="gmail"'), body[:80]
    assert "<html" not in body and "<main" not in body, (
        "it is mounted inside the overlay the page already has")
    # And it carries what the manager is for: the paste box, the chips,
    # the table and the editor.
    for part in ('class="addbox"', 'class="filters"', 'class="pooltable"',
                 'class="editor"'):
        assert part in body, part

    status, _, _ = client.request("GET", "/pools/nonesuch/sheet")
    assert status == 404


def test_an_operator_can_still_open_the_manager(web, monkeypatch):
    """They have never had the pool PAGES and have always had the
    manager. The sheet leaving the dashboard must not quietly take it
    from them (2026-09-21)."""
    _dash(monkeypatch)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own", "may_add_gmail": True})
    client = web()
    client.login(username="narrow")

    status, _, body = client.request("GET", "/pools/gmail/sheet")
    assert status == 200 and 'data-sheet="gmail"' in body
    # And the page it is a drawer over is still not theirs.
    status, _, _ = client.request("GET", "/pools/gmail")
    assert status in (302, 303)


def test_the_dashboard_reads_only_the_free_rows_for_its_cards():
    """`_pool_rows` read every live row of every pool with its password
    and its second factor, twice - once for the cards and once for the
    manager drawn shut inside the page. The cards list free rows and
    count the rest (2026-09-21)."""
    import inspect

    from geelark_farm.web import read

    source = inspect.getsource(read.dashboard)
    assert "_card_rows(store)" in source
    assert "_pool_rows(store)" not in source
    narrow = inspect.getsource(read._card_rows)
    assert "password" not in narrow and "totp_secret" not in narrow, (
        "the cards do not need a credential and must not carry one")


# ------------------------- one row's press is answered with one row (step 8)
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_one_row_press_is_answered_with_one_row(web, monkeypatch):
    """It answered 303 to the dashboard, and the script then fetched and
    DOMParsed the whole page to lift one `<tr>` out of it."""
    import geelark_farm.runner as runner_mod
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    monkeypatch.setattr(actions_mod, "enqueue", lambda *a, **k: 41)
    monkeypatch.setattr(actions_mod, "pending_for", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "settle", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "one", lambda *a, **k: {
        "result": "held@gmail.com is back on the shelf"})
    monkeypatch.setattr(runner_mod, "run_now", lambda *a, **k: (
        "done", "held@gmail.com is back on the shelf", None))
    client = web()
    client.login()
    _, _, page = client.request("GET", "/")
    token = re.search(r'name="csrf" value="([^"]*)"', page).group(1)

    status, _, body = client.request(
        "POST", "/pools/gmail/free",
        body=f"csrf={token}&address=held@gmail.com&back=/",
        headers={"X-GF-Row": "gmail"})

    assert status == 200, "a fragment, not a redirect"
    assert len(body) < 4000, f"{len(body)} bytes for one row"
    assert 'class="rowanswer"' in body
    assert "is back on the shelf" in body, "the verb's own words"
    assert "<html" not in body

    # And without the header - a browser with no script - the redirect
    # it has always answered with. That contract is what the whole live
    # layer rests on.
    status, head, _ = client.request(
        "POST", "/pools/gmail/free",
        body=f"csrf={token}&address=held@gmail.com&back=/")
    assert status == 303
    where = dict((k.lower(), v) for k, v in head)["location"]
    assert where.startswith("/?said=")


def test_only_the_pools_whose_route_is_wired_ask_for_a_row():
    """A kind the server cannot draw must not be asked to."""
    js = assets.JS
    # The proxy pool joined on 2026-09-22, with its own page's Free,
    # Test and Remove (read.pool_row has read a proxy by name since
    # band 4); refund and offer are the Gmail and Gpt pages' doors.
    assert "var ROW_ANSWERS = {gmail: 1, gpt: 1, spotify: 1, proxy: 1};" in js
    assert "asking['X-GF-Row'] = rowKind;" in js
    assert "/[/](free|edit|remove|refund|offer|test)$/.test(form.action)" in js
    assert "if (rowView) asking['X-GF-View'] = rowView;" in js
    assert "form.closest('tr').dataset.pageView" in js, (
        "not data-view, which is the phone table's chip word")


# ------------------------------- the write path claims what it works (step 10)
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_web_claims_a_row_before_it_works_it(web, monkeypatch):
    """It was the only writer in the system that did not.

    It enqueued the row, rang the keeper's bell and then ran the verb
    with the row still `queued` - and `take_batch` claims any queued
    row, and five verbs are in both the inline set and the lane's. Two
    operators and a keeper on one bell, and a duplicated `build_by_hand`
    is two phones and two accounts spent (2026-09-21).
    """
    import inspect

    import geelark_farm.runner as runner_mod
    import geelark_farm.store.actions as actions_mod
    from geelark_farm.web import app as app_mod

    ran = inspect.getsource(app_mod._Handler._ran_it_now)
    assert "store_actions.claim(self.settings, req)" in ran
    assert ran.index("claim(") < ran.index("run_now("), (
        "claimed before it is worked, not after")

    # And a row somebody else already has is not run here.
    _gmail_active(monkeypatch)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    monkeypatch.setattr(actions_mod, "enqueue", lambda s, **k: 91)
    monkeypatch.setattr(actions_mod, "claim", lambda s, i: False)
    worked = []
    monkeypatch.setattr(runner_mod, "run_now",
                        lambda *a, **k: worked.append(a) or ("done", "", None))
    client = web()
    client.login()
    _, headers, _ = client.request(
        "POST", "/pools/gmail/free",
        _form(csrf=client.csrf(), address="x@y.com", back="/"))

    assert not worked, "it ran a row the lane had already taken"
    assert "said=queued:91" in dict(headers)["Location"], (
        "and says it is queued, which it is")


def test_the_bell_rings_only_when_the_lane_has_the_work(web, monkeypatch):
    """`signals.ring` went before the inline attempt, which is an
    invitation for the keeper to claim a row this request is about to
    work."""
    import inspect

    from geelark_farm.web import app as app_mod

    act = inspect.getsource(app_mod._Handler._act)
    assert act.index("_ran_it_now(") < act.index("signals.ring("), (
        "the bell goes after this request has had its turn")


def test_a_claimed_row_that_did_not_run_is_put_back(web, monkeypatch):
    """Claimed and not run would sit `running` until `expire_running`
    closed it in an hour with a sentence about a restart."""
    import inspect

    from geelark_farm.web import app as app_mod

    ran = inspect.getsource(app_mod._Handler._ran_it_now)
    assert 'status="queued"' in ran, "it hands the row straight back"


def test_a_duplicate_press_is_found_by_the_field_and_not_the_text():
    """`payload::text ILIKE '%needle%'` matched an address that merely
    CONTAINED the one being asked about, and paid for a sequential scan
    with a jsonb cast on every press (2026-09-21)."""
    import inspect

    from geelark_farm.store import actions as store_actions

    found = inspect.getsource(store_actions.pending_for)
    assert "payload::text ILIKE" not in found
    assert "payload->>'address' = %s" in found
    schema = (pathlib.Path(store_actions.__file__).parent
              / "schema.sql").read_text(encoding="utf-8")
    assert "actions_pending" in schema, "and an index under it"


def test_the_rails_counts_are_read_only_when_a_page_draws_them(monkeypatch):
    """Nine subqueries on every request, including the presses that
    answer 303 and draw no rail at all."""
    from geelark_farm.web import app as app_mod

    reads = []
    counts = app_mod._RailCounts(lambda: reads.append(1) or {"gmail": 3})
    assert not reads, "read before anybody asked"
    assert counts.get("gmail") == 3
    assert len(reads) == 1
    counts.get("proxy")
    assert len(reads) == 1, "read twice for one request"


def test_the_session_is_read_once_per_request():
    """`do_POST` reads it to CSRF-check the press and `_user()` read it
    again from the same cookie a few lines later."""
    import inspect

    from geelark_farm.web import app as app_mod

    found = inspect.getsource(app_mod._Handler._entry)
    assert "_held_entry" in found
    assert "_UNREAD" in found, "told apart from a session that is None"


# ------------------------- the swap replaces regions, not the page (step 12)
def test_the_phone_table_is_a_region_the_server_owns(web, monkeypatch):
    """The update model was "refetch the whole page, replaceChildren on
    <main>, then put the operator's state back by hand" - six routines
    doing the putting back, and the caret covered by none of them. What
    is inside a region is the server's; what is outside it is left
    exactly as it stands, so there is nothing to put back (2026-09-21).
    """
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    assert '<div data-live="phones">' in body
    # Every block the server draws, by name. The table was the only
    # region for a day, and the swap - finding it - returned before
    # touching anything else: the status line, the alert strip, the
    # counts, the accounts card, the build card and the GeeLark foot
    # stood still from the moment the tab was opened until it was
    # reloaded (2026-09-21, found by audit the same evening). A block
    # added to <main> has to declare itself here or it never moves.
    regions = re.findall(r'data-live="([a-z]+)"', body)
    # The Send list is one too, where the Send sheet is drawn at all -
    # only with manual login on (2026-09-26).
    sends = ['send'] if 'data-sheet="send"' in body else []
    assert sorted(regions) == sorted(
        ["alerts", "top", "rail", "tally", "phones", "build", "side",
         "foot"] + sends), regions
    assert len(regions) == len(set(regions)), "a region is one node"
    # And none of them inside another: `swapRegions` replaces each
    # region's children, and a region under a region would be replaced
    # twice - once with its own copy, once as part of its parent's.
    for name in regions:
        at = body.index(f'data-live="{name}"')
        assert body.rfind("<main", 0, at) >= 0
    js = assets.JS
    assert "if (!held && sameBones(here, fresh) && swapRegions(here, fresh))" \
        in js
    # And the whole-of-main path is still there for everything else.
    assert "here.replaceChildren.apply(here, nodes);" in js


def test_a_page_that_changed_shape_does_not_take_the_region_path():
    """A card appearing, an alert arriving, the build card going away
    with a permission - a region swap cannot carry any of those."""
    js = assets.JS
    bones = js[js.index("function sameBones(here, fresh){"):]
    bones = bones[:bones.index("\n  }")]
    assert "mine.length > 0" in bones, "a page with no region is not 'same'"
    assert "mine.join" in bones and "theirs.join" in bones


def test_every_listener_is_bound_once_per_node():
    """`init()` runs after every swap and exactly one binding site had a
    guard. That was safe only because the swap threw the nodes away with
    their listeners - and regions stop doing that, so a second set would
    be a second sift, a second confirm, a second submit (2026-09-21).

    Named one by one, because that is what a reviewer has to check: a
    new binding site inside `init` is a new line here or a leak.
    """
    js = assets.JS
    assert "function once(node, what){" in js
    for node, what in (("b", "seg"),                 # the three views
                       ("x", "dismiss"),             # an alert put away
                       ("p", "gate"),                # the build card's Gmail
                       ("kindPick", "kind"),         # and its kind
                       ("pick", "new"),              # and its "type one"
                       ("byhand", "stuck"),          # and its submit
                       ("sheet", "sift"),            # a pool sheet
                       ("dlg", "editor")):           # the row editor
        assert f"once({node}, '{what}')" in js, f"{node} binds unguarded"

    # And the guard is a set, not a flag: one node can be bound for two
    # different things without the second being taken for the first.
    made = js[js.index("function once(node, what){"):]
    made = made[:made.index(chr(10) + "  }")]
    assert "node.dataset.bound" in made
    assert "indexOf('|' + what + '|')" in made

def test_a_confirm_bubble_takes_its_listener_with_it():
    """`askFirst` added a document keydown listener per bubble and
    removed it only when Escape was what dismissed it - so a confirm
    answered with the mouse left one behind, one per press."""
    js = assets.JS
    ask = js[js.index("function askFirst(form, question, answer){"):]
    ask = ask[:ask.index("\n  // A word on this page")]
    assert "box.remove = function(){" in ask
    assert "document.removeEventListener('keydown', esc);" in ask


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_build_asked_to_stop_says_so_on_its_row(web, monkeypatch):
    """The press was written and the row went on saying Building until
    the build actually died - minutes later, with nothing on the page to
    show it had landed, so it was pressed again (the operator,
    2026-09-21)."""
    _dash(monkeypatch,
          phones=[{"serial": "1503", "status": "building", "state": ""},
                  {"serial": "1504", "status": "building", "state": ""}],
          stops_asked=["1503"])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    row = body[body.index('href="/phones/1503"'):]
    row = row[:row.index("</tr>")]
    assert ">Stopping<" in row, "the row says the press landed"
    assert 'action="/phones/1503/stop"' not in row, "and is not offered twice"
    assert "disabled" in row and "Stopping&hellip;" in row

    # The one nobody pressed Cancel on still has its door.
    other = body[body.index('href="/phones/1504"'):]
    other = other[:other.index("</tr>")]
    assert 'action="/phones/1504/stop"' in other and ">Building<" in other


# ------------------------- a queued press is a fact every surface draws
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_phone_with_a_command_on_its_way_shows_that_door_pressed(
        web, monkeypatch):
    """The answer said Queued, the toast went, and the row went on
    offering the same door over a press the lane had not yet carried out
    - so it was pressed again (the operator, 2026-09-20; the audit,
    2026-09-21). The read returns what is pending, on the same
    connection, and the row draws it."""
    _dash(monkeypatch,
          phones=[{"serial": "1504", "status": "ready", "state": ""},
                  {"serial": "1505", "status": "ready", "state": ""}],
          pending={"1504": "boot_phone"})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    row = body[body.index('href="/phones/1504"'):]
    row = row[:row.index("</tr>")]
    assert "Booting&hellip;" in row and "disabled" in row
    assert 'action="/phones/1504/boot"' not in row, "not offered twice"
    assert "<form" not in row.split('class="act"')[1], "nothing to send"
    other = body[body.index('href="/phones/1505"'):]
    other = other[:other.index("</tr>")]
    assert 'action="/phones/1505/boot"' in other, "its neighbour keeps its door"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_pool_sheet_draws_a_pending_row_with_its_door_pressed(
        web, monkeypatch):
    base = _dash(monkeypatch)
    rows = base.get("pool_rows") or {}
    address = next(r["address"] for r in (rows.get("proxy") or [])
                   if r.get("state") == "free")
    base["pending"] = {address: "test_proxy"}
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/proxy/sheet")

    row = body[body.index(f'data-key="proxy:{address}"'):]
    row = row[:row.index("</tr>")]
    assert "Testing&hellip;" in row and "disabled" in row
    assert 'action="/pools/proxy/test"' not in row


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_phone_page_says_stopping_and_what_is_on_its_way(web,
                                                            monkeypatch):
    """The dashboard's row said Stopping the moment Cancel was pressed;
    this page - where a build is watched - went on offering Cancel over
    the press it had already taken (2026-09-21, found by audit)."""
    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "timeline": [],
        "phone": {"serial": serial, "status": "building", "state": "",
                  "owner": ""},
        "stop_asked": True, "pending": ""})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/phones/1503")
    assert ">Stopping<" in body and "Stopping&hellip;" in body
    assert 'action="/phones/1503/stop"' not in body

    monkeypatch.setattr(app_mod.read, "phone_holder", lambda s, serial: (
        (app_mod.read.phone_story(s, serial) or {}).get("phone")))
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "timeline": [],
        "phone": {"serial": serial, "status": "ready", "state": "",
                  "owner": ""},
        "stop_asked": False, "pending": "change_proxy"})
    _, _, body = client.request("GET", "/phones/1503")
    assert "Changing IP&hellip;" in body
    assert 'action="/phones/1503/proxy"' not in body
    assert 'action="/phones/1503/boot"' not in body, "one door, pressed"


@pytest.mark.parametrize("web", [True], indirect=True)
def test_the_requests_page_shows_a_stop_that_has_landed(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod
    from geelark_farm.store import stops as store_stops

    monkeypatch.setattr(actions_mod, "listing",
                        lambda s, **k: list(_REQUESTS))
    monkeypatch.setattr(actions_mod, "counts", lambda s, **k: {})
    monkeypatch.setattr(store_stops, "asked", lambda s: {"1549"})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/requests")
    assert "/phones/1549/stop" not in body, "pressed already: not offered"
    assert "Stopping&hellip;" in body
    assert body.count("Stop this one") == 1, "the other phone keeps its door"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_sweep_pressed_twice_is_one_sweep(web, monkeypatch):
    """Test all and Free all name nothing, so the double-press guard
    never found their twin: a second press was a second ~27s sweep of
    GeeLark (2026-09-21, found by audit)."""
    import geelark_farm.store.actions as actions_mod

    monkeypatch.setattr(actions_mod, "pending_any",
                        lambda s, *, verb: 240 if verb == "test_all_proxies"
                        else None)
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda *a, **k: pytest.fail("queued a twin"))
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/pools/proxy/test-all", _form(csrf=client.csrf()))
    assert status == 303
    assert "said=already:240" in dict(headers)["Location"]


def test_the_pending_read_names_every_target_a_command_carries():
    from geelark_farm.web import read

    class _S:
        def _rows(self, sql, params=()):
            assert "status IN ('queued', 'running')" in sql
            return [{"verb": "boot_phone", "serial": "1504", "address": None,
                     "name": None},
                    {"verb": "test_proxy", "serial": None, "address": None,
                     "name": "SX3"},
                    {"verb": "login_accounts", "serial": "1600",
                     "address": "a@b.com", "name": None},
                    {"verb": "change_proxy", "serial": "1504",
                     "address": None, "name": None}]

    got = read._pending(_S())
    assert got == {"1504": "boot_phone", "SX3": "test_proxy",
                   "1600": "login_accounts", "a@b.com": "login_accounts"}, (
        "the first command a target waits on is the one drawn")


# ------------------------------------------- the sheet tells the truth
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_an_open_sheet_is_told_304_until_something_it_is_drawn_from_moves(
        web, monkeypatch):
    """The fetched sheet was a snapshot for the life of the tab. An open
    drawer asks again with the stamp it was drawn from, and the 726KB
    are sent again only when something moved (2026-09-21)."""
    base = _dash(monkeypatch)
    client = web()
    client.login()

    status, head, body = client.request("GET", "/pools/gmail/sheet")
    tag = dict(head).get("ETag")
    assert status == 200 and tag and tag.startswith('W/"'), tag
    assert 'data-sheet="gmail"' in body

    status, head, body = client.request("GET", "/pools/gmail/sheet",
                                        headers={"If-None-Match": tag})
    assert status == 304 and body == ""
    assert dict(head).get("ETag") == tag

    base["stamp"] = "s2"
    status, head, body = client.request("GET", "/pools/gmail/sheet",
                                        headers={"If-None-Match": tag})
    assert status == 200 and dict(head).get("ETag") != tag
    assert 'data-sheet="gmail"' in body


def test_the_press_key_carries_the_words_and_not_only_the_press():
    """A corrected Save of the same row from the same drawing of the
    sheet was "the same press, sent twice" and thrown away with a green
    "already went through" over it (2026-09-21, found by audit)."""
    assert app_mod._digest({"a": 1, "by": "x", "by_id": 2}) == \
        app_mod._digest({"a": 1}), "who pressed is not what was pressed"
    assert app_mod._digest({"a": 1}) != app_mod._digest({"a": 2})
    assert len(app_mod._digest({})) == 10
    src = inspect.getsource(app_mod._Handler._act)
    assert 'idem_key=f"{idem}:{_digest(payload)}"' in src


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_remove_in_the_gpt_and_spotify_drawers_answers_with_the_row(
        web, monkeypatch):
    """The Gmail door answered with its row and these two did not, so a
    Remove in the drawer left the row on screen exactly as it was
    (2026-09-21, found by audit)."""
    import geelark_farm.store.actions as actions_mod
    from geelark_farm import runner as runner_mod

    _dash(monkeypatch)
    monkeypatch.setattr(actions_mod, "enqueue", lambda *a, **k: 71)
    monkeypatch.setattr(actions_mod, "claim", lambda *a, **k: True)
    monkeypatch.setattr(actions_mod, "pending_for", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "settle", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "one", lambda *a, **k: {"result": "gone"})
    monkeypatch.setattr(runner_mod, "run_now",
                        lambda *a, **k: ("done", "gone", None))
    client = web()
    client.login()
    for kind in ("gpt", "spotify"):
        status, _, body = client.request(
            "POST", f"/pools/{kind}/remove",
            body=f"csrf={client.csrf()}&address=x%40y.com&sure=1&back=/",
            headers={"X-GF-Row": kind})
        assert status == 200, (kind, status)
        assert 'class="rowanswer"' in body and f'data-row-kind="{kind}"' in body


# ------------------------------------------- band 4: bytes and milliseconds
@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_editor_reads_one_rows_credentials_when_its_door_is_pressed(
        web, monkeypatch):
    """~500 passwords and TOTP secrets rode in every drawer fetched, for
    the one row in a hundred anybody opened (2026-09-21, found by
    audit). The same people, the same door, one row at a time."""
    _dash(monkeypatch)
    monkeypatch.setattr(app_mod.read, "credentials",
                        lambda s, kind, address: (
                            {"password": "Kx82!mnQ", "secret": "JBSWY3DP"}
                            if (kind, address) == ("gmail", "free@gmail.com")
                            else None))
    client = web()
    client.login()
    status, head, body = client.request(
        "GET", "/pools/gmail/credentials?address=free%40gmail.com")
    assert status == 200
    assert dict(head).get("Content-Type", "").startswith("application/json")
    import json as _json
    assert _json.loads(body) == {"password": "Kx82!mnQ", "secret": "JBSWY3DP"}

    status, _, _ = client.request(
        "GET", "/pools/gmail/credentials?address=nobody%40gmail.com")
    assert status == 404
    status, _, _ = client.request("GET", "/pools/proxy/credentials?address=SX1")
    assert status == 404, "an exit has no editor"
    # And the operator's allowlist lets the door through: the drawer is
    # theirs and always has been.
    assert app_mod._operator_may_get("/pools/gmail/credentials")


def test_the_one_row_answer_reads_one_row(monkeypatch):
    """`_row_answer` read the whole pool - passwords and all - to find one
    address; the phone doors read a phone's whole story to answer who
    holds it (2026-09-21, found by audit)."""
    src = inspect.getsource(app_mod._Handler._row_answer)
    assert "read.pool_row(self.settings, kind, address)" in src
    assert "read.pool_sheet(" not in src
    holder = inspect.getsource(app_mod._Handler._holder_of)
    assert "read.phone_holder(self.settings, serial)" in holder
    assert "phone_story" not in holder


def test_pool_row_is_the_sheets_own_query_narrowed_to_one_address(monkeypatch):
    """The same text draws the sheet and the single row, so the two
    cannot come to differ; the credentials are in neither."""
    from geelark_farm.web import read

    asked = []

    class _S:
        def __init__(self, settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def _rows(self, sql, params=()):
            asked.append((sql, params))
            if "status IN ('queued', 'running')" in sql:
                return []
            return [{"id": 1, "address": "a@x.com"}] if "= lower(%s)" in sql \
                else []

    monkeypatch.setattr(read, "Store", _S)
    got = read.pool_row(None, "gmail", "A@x.com")
    # With the one word the sheet groups and badges by. `_pool_rows`
    # set it on every sheet row and this did not, so the row a press
    # answered with wore a "-" badge and "now under errored" whatever
    # the press had done (the operator, 2026-09-22).
    assert got["row"] == {"id": 1, "address": "a@x.com", "state": "free"}
    from geelark_farm.web import pages

    drawn = pages.row_answer("gmail", got["row"], "done:1", {"id": 1})
    assert 'data-state="free"' in drawn and 'data-group="current"' in drawn
    sql, params = asked[0]
    assert "lower(address) = lower(%s)" in sql and params == ("A@x.com", 1)
    assert "AS password" not in sql and "AS secret" not in sql, (
        "no credential value is selected for a row")
    assert "AS second" in sql, "the kind of second factor stays"
    asked.clear()
    read.pool_row(None, "proxy", "SX3")
    assert "lower(proxy_name) = lower(%s)" in asked[0][0]
    # And the credentials read is its own, for its own door.
    asked.clear()
    read.credentials(None, "gmail", "a@x.com")
    assert "password" in asked[0][0] and "LIMIT 1" in asked[0][0]


# ------------------------------------ the back-URL rebuild, finished
def test_the_refund_buttons_come_back_to_the_page_they_were_pressed_on():
    """`_refund_cell` rebuilt its own back from the view and the seller
    and forgot the page, so Paid on page three of a seller's list - the
    two presses made over and over while working a refund list - came
    back to page one (2026-09-21, found by audit)."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "is_admin": True, "may_add_gmail": True, "sees": "all",
            "username": "mehdi", "nav": {}}
    row = {"id": 214, "address": "owed@gmail.com", "status": "wrong_password",
           "updated_at": "2026-09-19 10:00:00+00", "seller": "ali",
           "refund_state": "to_claim", "totp_secret": "", "recovery_email": "",
           "password": "p"}
    data = {"rows": [row], "view": "errored", "counts": {}, "seller": "ali",
            "page": 3, "pages": 3, "known_sellers": ["ali"]}
    drawn = pages.gmail_pool_page(data, user, advice=lambda s: None)
    here = "/pools/gmail?view=errored&amp;seller=ali&amp;page=3"
    refund = drawn[drawn.index('action="/pools/gmail/refund"'):]
    assert f'name="back" value="{here}"' in refund[:600], "Paid carries the page"
    # Paid, Not paid - and the Free door after them carries it too.
    assert refund.count(f'name="back" value="{here}"') == 3, "and Not paid"
    # And the server keeps it: page is among what the rebuild allows.
    assert app_mod._back_to({"back": "/pools/gmail?view=errored&seller=ali&page=3"},
                            "/pools/gmail") == \
        "/pools/gmail?view=errored&seller=ali&page=3"


def test_offer_again_comes_back_to_the_set_aside_list_it_was_pressed_on():
    """The Offer again form carried no back and its route named the bare
    path - the Waiting view - so a press on page two of the set-aside
    list drew the Waiting list under an address bar that still said
    otherwise. And `_BACK_VIEWS` for the GPT pool listed a `set_aside`
    the page never had and lacked the `needs_human` it has, so no door
    could have come back to it anyway (2026-09-21, found by audit)."""
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "is_admin": True, "may_add_gpt": True, "may_login_accounts": True,
            "sees": "all", "username": "mehdi", "nav": {}}
    row = {"id": 7, "address": "set@x.com", "status": "no_code", "serial": "",
           "note": "", "source": "web", "updated_at": "2026-09-19 10:00:00+00"}
    data = {"rows": [row], "view": "needs_human", "counts": {}, "q": "",
            "page": 2, "pages": 2}
    drawn = pages.gpt_pool_page(data, user, manual_login=True)
    assert 'action="/pools/gpt/offer"' in drawn
    assert 'name="back" value="/pools/gpt?view=needs_human&amp;page=2"' in drawn
    assert tuple(pages.GPT_VIEWS) == app_mod._BACK_VIEWS["/pools/gpt"], (
        "the rebuild allows exactly the views the page has")
    assert app_mod._back_to({"back": "/pools/gpt?view=needs_human&page=2"},
                            "/pools/gpt") == "/pools/gpt?view=needs_human&page=2"
    # The Waiting view's first page is the bare path, as it always was.
    assert pages._gpt_here("waiting", "", 1) == "/pools/gpt"
    assert pages._gpt_here("delivered", "a b", 3) == \
        "/pools/gpt?view=delivered&q=a%20b&page=3"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_offer_door_returns_where_the_form_said(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod
    from geelark_farm import runner as runner_mod

    _dash(monkeypatch)
    monkeypatch.setattr(actions_mod, "enqueue", lambda *a, **k: 73)
    monkeypatch.setattr(actions_mod, "claim", lambda *a, **k: True)
    monkeypatch.setattr(actions_mod, "pending_for", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "settle", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "one", lambda *a, **k: {"result": "ok"})
    monkeypatch.setattr(runner_mod, "run_now",
                        lambda *a, **k: ("done", "offered", None))
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/pools/gpt/offer",
        _form(csrf=client.csrf(), address="set@x.com",
              back="/pools/gpt?view=needs_human&page=2"))
    assert status == 303
    assert dict(headers)["Location"] == \
        "/pools/gpt?view=needs_human&page=2&said=done:73"


@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_log_in_selected_comes_back_with_the_search_and_the_page(
        web, monkeypatch):
    """The login door matched its back whole against two bare paths, so
    ticks on page two of a search came back to page one of the plain
    list (2026-09-21, found by audit). Rebuilt like every other door,
    and still only to the two pages with ticks on them."""
    import geelark_farm.store.actions as actions_mod

    _gpt_active(monkeypatch, waiting=[_app_row("a@x.com")], on_phone=[])
    monkeypatch.setattr(actions_mod, "enqueue", lambda s, **k: 94)
    monkeypatch.setattr(actions_mod, "pending_for", lambda s, **k: None)
    client = web()
    client.login()
    _, headers, _ = client.request(
        "POST", "/accounts/login",
        _form(csrf=client.csrf(), back="/pools/gpt?q=abc&page=2")
        + "&addresses=a%40x.com")
    assert dict(headers)["Location"] == "/pools/gpt?q=abc&page=2&said=queued:94"
    _, headers, _ = client.request(
        "POST", "/accounts/login",
        _form(csrf=client.csrf(), back="/pools/gpt?q=abc&page=2"))
    assert dict(headers)["Location"] == "/pools/gpt?q=abc&page=2&said=none"
    for elsewhere in ("/pools/gmail?view=queued", "/evil", "/requests"):
        _, headers, _ = client.request(
            "POST", "/accounts/login",
            _form(csrf=client.csrf(), back=elsewhere) + "&addresses=a%40x.com")
        assert dict(headers)["Location"] == "/?said=queued:94", elsewhere


# ------------------------------- the pool pages answer with their own row
def test_each_pool_page_view_is_one_query_shared_with_its_one_row_read(
        monkeypatch):
    """The dashboard's `pool_row` reads the sheet's columns, which is
    why the dedicated pages had no one-row answer: their rows are
    drawn per view. Now each view is one SELECT that the page and
    `page_row` both use, narrowed to one address for the answer
    (2026-09-22, found by audit)."""
    from geelark_farm.web import read

    for view in ("queued", "on_phone", "used", "errored"):
        sql, order, params = read._gmail_view(view, "LEO")
        assert sql.startswith("SELECT ") and " WHERE r.kind = 'gmail'" in sql
        assert order.startswith(" ORDER BY ")
    assert read._gmail_view("errored", "LEO")[2][-1] == "leo"
    for view in ("waiting", "on_phone", "needs_human", "delivered"):
        sql, order, params = read._gpt_view(view, "abc")
        assert sql.startswith("SELECT ") and " WHERE r.kind = 'app'" in sql
    assert "_gmail_view(view, seller)" in inspect.getsource(read.gmail_pool)
    assert "_gpt_view(view, q)" in inspect.getsource(read.gpt_pool)
    assert "_PROXY_SELECT" in inspect.getsource(read.proxy_pool)

    asked = []

    class _S:
        def __init__(self, settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def _rows(self, sql, params=()):
            asked.append((sql, params))
            if "status IN ('queued', 'running')" in sql:
                return []
            if "count(*) FILTER" in sql:
                return [{"queued": 4, "errored": 2, "on_phone": 0, "used": 1,
                         "waiting": 3, "needs_human": 1, "delivered": 9}]
            if "GROUP BY status" in sql:
                return [{"status": "free", "c": 3}, {"status": "dead", "c": 1}]
            if "proxy_name) = lower(%s)" in sql:
                return [{"id": 3, "name": "SX1", "host": "h", "serial": "",
                         "status": "free"}]
            if "= lower(%s)" in sql:
                return [{"id": 1, "address": "a@x.com",
                         "status": "wrong_password"}]
            return []

    monkeypatch.setattr(read, "Store", _S)
    got = read.page_row(None, "gmail", "errored", "A@x.com", seller="LEO")
    assert got["view"] == "errored" and got["row"]["address"] == "a@x.com"
    assert got["counts"]["errored"] == 2 and "pending" in got
    sql, params = asked[0]
    assert " AND lower(r.address) = lower(%s) ORDER BY " in sql
    assert sql.endswith(" LIMIT 1") and params[-1] == "A@x.com"
    assert params[-2] == "leo", "the seller the list was cut by"
    # An unknown view lands where the page would have.
    assert read.page_row(None, "gpt", "nonesuch", "a@x.com")["view"] == \
        "waiting"
    # A proxy: read by name, then judged against the view like the page
    # cuts its buckets; a `free` row is not on `needs_hand`.
    got = read.page_row(None, "proxy", "free", "sx1")
    assert got["row"]["name"] == "SX1" and got["counts"] == {
        "free": 3, "on_phone": 0, "needs_new_ip": 0, "dead": 1, "strays": 0,
        "needs_hand": 1, "all": 4}
    assert read.page_row(None, "proxy", "needs_hand", "sx1")["row"] is None
    assert read.page_row(None, "proxy", "all", "sx1", q="zzz")["row"] is None


def test_every_row_of_the_pool_pages_says_which_row_and_view_drew_it():
    from geelark_farm.web import pages

    user = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
            "is_admin": True, "may_add_gmail": True, "may_add_gpt": True,
            "may_change_proxy": True, "may_login_accounts": True,
            "sees": "all", "username": "mehdi", "nav": {}}
    row = {"id": 41, "address": "torn@gmail.com", "status": "no_authenticator",
           "updated_at": "2026-09-19 10:00:00+00", "seller": "LEO",
           "totp_secret": "", "recovery_email": "", "password": "p",
           "purchased_on": "2026-09-01"}
    for view in ("queued", "errored", "used", "on_phone"):
        data = {"rows": [row], "view": view, "counts": {}, "seller": "",
                "page": 1, "pages": 1, "known_sellers": []}
        drawn = pages.gmail_pool_page(data, user, advice=lambda s: None)
        assert (f'<tr data-key="gmail:torn@gmail.com" data-page-view="{view}">'
                in drawn), view
    # The row drawn as a form keeps its key, so a Save's answer lands on it.
    data = {"rows": [row], "view": "queued", "counts": {}, "seller": "",
            "page": 1, "pages": 1, "known_sellers": []}
    edited = pages.gmail_pool_page(data, user, advice=lambda s: None,
                                   editing=41)
    assert ('<tr class="editrow" data-key="gmail:torn@gmail.com"'
            ' data-page-view="queued">' in edited)

    app_row = {"id": 7, "address": "set@x.com", "status": "no_code",
               "serial": "", "note": "", "source": "web", "has_totp": True,
               "email_code_only": False,
               "updated_at": "2026-09-19 10:00:00+00",
               "created_at": "2026-09-19 09:00:00+00"}
    for view in ("waiting", "needs_human", "on_phone", "delivered"):
        data = {"rows": [app_row], "view": view, "counts": {}, "q": "",
                "page": 1, "pages": 1}
        drawn = pages.gpt_pool_page(data, user, manual_login=True)
        assert (f'<tr data-key="gpt:set@x.com" data-page-view="{view}">'
                in drawn), view

    proxy = {"id": 3, "name": "SX1", "host": "10.0.0.1", "port": 1080,
             "status": "free", "serial": "", "last_exit_ip": "",
             "times_used": 0, "note": "",
             "updated_at": "2026-09-19 10:00:00+00", "bucket": "free"}
    for view, status, bucket in (("free", "free", "free"),
                                 ("on_phone", "on a phone", "on_phone"),
                                 ("all", "free", "free"),
                                 ("needs_hand", "dead", "dead")):
        data = {"rows": [dict(proxy, status=status, bucket=bucket)],
                "view": view, "counts": {}, "q": "", "page": 1, "pages": 1,
                "tests": {}, "strays": []}
        drawn = pages.proxy_pool_page(data, user)
        assert f'<tr data-key="proxy:SX1" data-page-view="{view}">' in drawn, \
            view

    # And the phone table's own chip word is untouched by it.
    assert 'data-view="' not in pages._row_key("gmail", "a@x.com", "queued")


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_press_on_a_pool_page_is_answered_with_that_pages_own_row(
        web, monkeypatch):
    """Under fresh pills, and with no row when the press moved it out of
    the view - a Free on the errored list - which the script removes."""
    import geelark_farm.store.actions as actions_mod
    from geelark_farm import runner as runner_mod
    from geelark_farm.web import read

    _dash(monkeypatch)
    monkeypatch.setattr(actions_mod, "enqueue", lambda *a, **k: 77)
    monkeypatch.setattr(actions_mod, "claim", lambda *a, **k: True)
    monkeypatch.setattr(actions_mod, "pending_for", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "settle", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "one", lambda *a, **k: {"result": "ok"})
    monkeypatch.setattr(runner_mod, "run_now",
                        lambda *a, **k: ("done", "back on the shelf", None))
    monkeypatch.setattr(app_mod._Handler, "_proxy_state",
                        lambda self: ([{"host": "9.9.9.9"}], [], {}))
    asked = []
    answers = {"gmail": {"id": 2, "address": "owed@gmail.com",
                         "status": "wrong_password", "seller": "ali",
                         "updated_at": "2026-09-19 10:00:00+00",
                         "refund_state": "to_claim"},
               "proxy": None}

    def page_row(settings, kind, view, address, *, seller="", q="",
                 strays=0):
        asked.append((kind, view, address, seller, q, strays))
        return {"view": view, "row": answers[kind], "pending": {},
                "counts": {"errored": 2, "queued": 5, "free": 3,
                           "needs_hand": 1}}

    monkeypatch.setattr(read, "page_row", page_row)
    client = web()
    client.login()

    status, _, body = client.request(
        "POST", "/pools/gmail/refund",
        body=f"csrf={client.csrf()}&address=owed%40gmail.com&state=claimed"
             "&back=/pools/gmail%3Fview%3Derrored%26seller%3Dali%26page%3D3",
        headers={"X-GF-Row": "gmail", "X-GF-View": "errored"})
    assert status == 200, body[:300]
    assert ('class="rowanswer" data-row-kind="gmail" data-row-view="errored"'
            in body)
    pills = body[body.index('<div class="pills">'):body.index("<table>")]
    assert ">Errored<span" in pills and ">2</span>" in pills, pills
    assert ('<tr data-key="gmail:owed@gmail.com" data-page-view="errored">'
            in body), "the row, as the errored view draws it"
    assert ('name="back" value="/pools/gmail?view=errored&amp;seller=ali'
            '&amp;page=3"' in body)
    assert asked == [("gmail", "errored", "owed@gmail.com", "ali", "", 0)], (
        "the list was cut by the seller the page was on")

    # A proxy door: the row is named, not addressed; the strays GeeLark
    # holds count on the Needs a hand pill.
    status, _, body = client.request(
        "POST", "/pools/proxy/free",
        body=f"csrf={client.csrf()}&name=SX1"
             "&back=/pools/proxy%3Fview%3Dneeds_hand",
        headers={"X-GF-Row": "proxy", "X-GF-View": "needs_hand"})
    assert status == 200, body[:300]
    assert 'data-row-kind="proxy" data-row-view="needs_hand"' in body
    assert "<table></table>" in body, "gone from this view: no row"
    assert asked[-1] == ("proxy", "needs_hand", "SX1", "", "", 1)

    # Without the view header the dashboard's sheet answer stands.
    src = inspect.getsource(app_mod._Handler._row_answer)
    assert "self.headers.get(self.VIEW_ASKED)" in src
    assert "read.pool_row(self.settings, kind, address)" in src


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_save_in_the_gpt_and_spotify_drawers_answers_with_the_row(
        web, monkeypatch):
    """The Gmail Save answered with its row and these two did not
    (2026-09-22) - and the first cut of this fix put `row_of` inside
    `_add_back(...)`, which a substring pin read as done."""
    import geelark_farm.store.actions as actions_mod
    from geelark_farm import runner as runner_mod

    _dash(monkeypatch)
    monkeypatch.setattr(actions_mod, "enqueue", lambda *a, **k: 78)
    monkeypatch.setattr(actions_mod, "claim", lambda *a, **k: True)
    monkeypatch.setattr(actions_mod, "pending_for", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "settle", lambda *a, **k: None)
    monkeypatch.setattr(actions_mod, "one", lambda *a, **k: {"result": "saved"})
    monkeypatch.setattr(runner_mod, "run_now",
                        lambda *a, **k: ("done", "saved", None))
    client = web()
    client.login()
    for kind in ("gpt", "spotify"):
        status, _, body = client.request(
            "POST", f"/pools/{kind}/edit",
            body=f"csrf={client.csrf()}&address=x%40y.com&new_address=x%40y.com"
                 f"&password=pw&back=/",
            headers={"X-GF-Row": kind})
        assert status == 200, (kind, status, body[:200])
        assert 'class="rowanswer"' in body and f'data-row-kind="{kind}"' in body


def test_the_pool_page_doors_ask_for_a_row():
    src = inspect.getsource(app_mod)
    assert src.count('row_of="gmail"') == 3, "edit, remove, refund"
    assert src.count('row_of="gpt"') == 3, "edit, remove, offer"
    assert src.count('row_of="spotify"') == 2, "edit, remove"
    assert src.count("row_of=kind)") == 1, "the shared Free door"
    assert src.count('row_of="proxy"') == 1, "free, test, remove share one"
    assert 'payload.get("address") or payload.get("name")' in \
        inspect.getsource(app_mod._Handler._act)


def test_the_phones_table_says_whose_gmail_is_on_each_phone(monkeypatch):
    """Under the address, small, the way the maker's name sits under
    the status - the operator reads the table by seller when a batch
    goes wrong (2026-09-22). Nothing under a bare phone."""
    from geelark_farm.web import pages, read

    src = inspect.getsource(read.dashboard)
    assert "coalesce(rg.seller, '') AS gmail_seller" in src
    assert "lower(rg.address) = lower(p.gmail)" in src
    with_seller = {"gmail": "IronHawk@gmail.com", "gmail_seller": "LEO 21SEP"}
    assert pages._seller_line(with_seller) == (
        '<span class="dim maker seller" title="the seller this Gmail came '
        'from">LEO 21SEP</span>')
    assert pages._seller_line({"gmail": "", "gmail_seller": "LEO"}) == ""
    assert pages._seller_line({"gmail": "a@x.com", "gmail_seller": ""}) == ""
    assert "_seller_line(r)" in inspect.getsource(pages._phone_rows)


@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_the_send_list_is_a_region_and_moves_while_it_is_open(
        web, monkeypatch):
    """The Send sheet sat outside every region, so the region swap never
    touched it: phone 4444 was still offered seven minutes after an
    account went onto it, and the press was refused (the operator,
    2026-09-26)."""
    _dash(monkeypatch, phones=[
        {"serial": "4444", "status": "app_only", "state": "", "gmail": "",
         "app_account": "", "proxy_name": "US37"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    send = body[body.index('data-sheet="send"'):]
    send = send[:send.index("</section>")]
    assert '<div data-live="send">' in send, "the list is a region"

    js = assets.JS
    keep = js[js.index("function keepSheet(held, kind, fresh){"):]
    keep = keep[:keep.index("\n  }\n")]
    branch = keep[keep.index("if (kind === 'send') {"):]
    # Open: replaced when it changed, with the account put back on the
    # new rows - the click that opened the sheet filled the old ones.
    assert "!sendSame(list, flist)" in branch
    assert "i.value = who;" in branch
    assert "function sendSame(mine, fresh){" in js



def test_a_send_that_went_through_closes_its_sheet_and_says_so():
    """The keeper took the press in two seconds, and the sheet stayed
    open over a page that holds still for an open sheet - the account
    still waiting, the phone still warm - so it read as nothing at all
    (the operator, 2026-09-26). Shut, the swap takes the region path,
    which leaves the banner out; it is put in by hand."""
    js = assets.JS
    at = js.index("if (sheet && sheet.dataset.sheet === 'send'"
                  " && !turned.test(got.url)) {")
    branch = js[at:js.index("}", at)]
    assert "shut();" in branch and "swapMain(doc);" in branch
    assert branch.index("swapMain(doc);") < branch.index("sayIt(doc);")
    # Turned away, it stays open with the reason: the test is `turned`.
    assert js.index("var turned = ") < at


# ------------------------------------------ the phone's journey (2026-09-26)
def _journey_4435():
    import json
    import pathlib
    from datetime import datetime, timezone

    from geelark_farm.web import journey

    data = json.loads((pathlib.Path(__file__).parent / "fixtures"
                       / "journey.json").read_text(encoding="utf-8"))
    phone = data["4435"]
    return journey.runs_from(
        phone["lines"], phone["folders"],
        [datetime(2026, 9, 25, 17, 59, 28, tzinfo=timezone.utc)]), phone


def _journey_story(monkeypatch):
    runs, _ = _journey_4435()
    monkeypatch.setattr(app_mod.read, "phone_story", lambda s, serial: {
        "serial": serial, "phone": None, "timeline": [
            {"at": "2026-09-25 18:06:12+00", "source": "event",
             "kind": "build_finished", "status": "phone_distrusted",
             "run": "r1/1", "text": "ok=False", "seconds": 406}],
        "stop_asked": False, "pending": ""})
    monkeypatch.setattr(app_mod.read, "phone_journey", lambda s, serial: runs)


def test_the_phone_page_draws_where_its_run_spent_its_time(web, monkeypatch):
    """Its story was a list of events; where a run lost its minutes and
    which screen it stopped on were a search through log lines (the
    operator, 2026-09-26)."""
    _journey_story(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/phones/4435")

    panel = body[body.index("Its journey"):body.index("Its story")]
    assert "Build" in panel and "phone_distrusted" in panel
    # The reason the flow named its last screen for, and who it blames.
    assert "The service showed a CAPTCHA" in panel
    assert "blamed on the account" in panel
    # The stages, the one that stopped open, screen by screen.
    assert 'class="jseg failed"' in panel and "× Google" in panel
    assert "Google, screen by screen" in panel
    folder = "20260925-175928-build4435"
    assert f'src="/phones/4435/wire/{folder}/180404-captcha.xml"' in panel
    assert "captcha ×11" in panel
    assert panel.count("→ answered, and another came") == 3
    assert f'src="/phones/4435/shot/{folder}/captcha-screen.png"' in panel
    assert 'loading="lazy"' in panel


def test_a_screenshot_is_for_those_who_may_take_phones(web, monkeypatch):
    _journey_story(monkeypatch)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "all", "may_take_phones": False})
    client = web()
    client.login(username="narrow")
    _, _, body = client.request("GET", "/phones/4435")
    assert "/shot/" not in body
    assert "shown to those who may take phones" in body
    assert "/wire/" in body, "the drawings are not the phone's screen"


def test_journey_pictures_are_served_only_from_the_phones_own_folder(
        web, monkeypatch, tmp_path, make_settings):
    import geelark_farm.web.read as read_mod

    _, phone = _journey_4435()
    folder = "20260925-175928-build4435"
    mine = tmp_path / folder
    mine.mkdir()
    (mine / "180404-captcha.xml").write_text(
        phone["xml"]["180404-captcha.xml"], encoding="utf-8")
    (mine / "captcha-screen.png").write_bytes(b"png-bytes")
    other = tmp_path / "20260925-175928-build4436"
    other.mkdir()
    (other / "180404-captcha.xml").write_text("<theirs/>", encoding="utf-8")
    real = read_mod.screen_file
    art = make_settings(artifact_dir=tmp_path)
    monkeypatch.setattr(app_mod.read, "screen_file",
                        lambda s, serial, folder, name, **k:
                        real(art, serial, folder, name, **k))
    client = web()
    client.login()

    status, headers, body = client.request(
        "GET", f"/phones/4435/wire/{folder}/180404-captcha.xml")
    assert status == 200 and dict(headers)["Content-Type"] == "image/svg+xml"
    assert body.startswith("<svg") and "pe•••@gmail.com" in body
    assert "default-src 'none'" in dict(headers)["Content-Security-Policy"]
    status, headers, body = client.request(
        "GET", f"/phones/4435/shot/{folder}/captcha-screen.png")
    assert status == 200 and dict(headers)["Content-Type"] == "image/png"
    assert body == "png-bytes"
    for bad in (f"/phones/4435/wire/{folder}/captcha-screen.png",
                f"/phones/4435/shot/{folder}/180404-captcha.xml",
                "/phones/4435/wire/20260925-175928-build4436/180404-captcha.xml",
                f"/phones/4435/wire/{folder}/..%2F..%2Fsecret.xml",
                f"/phones/4435/other/{folder}/180404-captcha.xml"):
        status, _, _ = client.request("GET", bad)
        assert status == 404, bad

    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "all", "may_take_phones": False})
    narrow = web()
    narrow.login(username="narrow")
    status, _, _ = narrow.request(
        "GET", f"/phones/4435/shot/{folder}/captcha-screen.png")
    assert status == 403
    status, _, _ = narrow.request(
        "GET", f"/phones/4435/wire/{folder}/180404-captcha.xml")
    assert status == 200
