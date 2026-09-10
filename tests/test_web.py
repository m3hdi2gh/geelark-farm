"""The web UI, driven over real HTTP against a fake store.

Real sockets and real request parsing, because the handler's bugs live in
headers and cookies and encodings - a fake request object would test a
server nobody runs. The store side is faked instead: these tests must run
on a machine that has never seen the cluster.
"""

from __future__ import annotations

import http.client
import re
import threading

import pytest

import geelark_farm.web.app as app_mod

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
              "broken": 0}

    def gmail_pool(settings, view="queued", seller="", page=1, per_page=100):
        seen.update(view=view, seller=seller, page=page)
        out = {"view": view, "counts": counts, "seller": seller,
               "sellers": [{"seller": "egypt", "c": 2}],
               "known_sellers": known, "page": page, "pages": 1,
               "more": False, "total": counts.get(view, 0)}
        if view == "errored":
            out.update(
                rows=[_gmail_row("bad1@x.com", "captcha_shown"),
                      _gmail_row("bad2@x.com", "wrong_2fa_code")],
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
    assert '<tr class="editrow">' in body and 'colspan="6"' in body
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
    assert 'href="/pools/gmail/refund.txt?seller=egypt">Copy 2 addresses' \
        in body

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
    assert got["verb"] == "add_gmails" and got["idem_key"] == "once-abc"
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
def test_with_manual_login_on_the_dashboard_offers_the_buttons(web):
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert body.count("Change IP") == 2, "one per phone, both states"
    # One send per waiting account, on its own row - once in the GPT
    # card's queue and once on the same row inside the manager - and each
    # opens the chooser, which lists the one phone that can take an
    # account (1501: app only, nobody's). The tick-and-send list stood in
    # a panel of its own and went with it.
    assert body.count("data-choose=") == 2
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
    client = web()
    client.login()
    # 1523 is taken by "ali" and the reader is not ali. The table has
    # always answered that with the holder's name and no buttons; this
    # page answered it with Done and Failed - and Failed deletes the phone
    # at the next sync and frees the account on it. One contract, both
    # surfaces (2026-09-07).
    _, _, body = client.request("GET", "/phones/1523")
    assert "with ali" in body
    for door in ("boot", "state", "proxy"):
        assert f'action="/phones/1523/{door}"' not in body, (
            f"{door} on a phone somebody else is holding")

    # And the POST is refused too, not merely undrawn. The page drew the
    # rule and nothing enforced it, so the press went through on a form
    # the page had not offered - and Failed deletes the phone at the next
    # sync and frees the account on it (2026-09-07).
    status, headers, _ = client.request(
        "POST", "/phones/1523/state",
        _form(csrf=client.csrf(), state="failed", sure="1",
              back="/phones/1523"))
    assert status == 303
    assert dict(headers)["Location"] == "/?said=refused"
    assert got == {}, "nothing was queued against somebody else's phone"


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
                        lambda s, serial, folder, name:
                        real(art, serial, folder, name))
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
    _, _, body = web().request("GET", "/login")
    style = body[body.index("<style>"):body.index("</style>")]
    for line in style.splitlines():
        assert line.count("'") % 2 == 0 and line.count('"') % 2 == 0, line
    assert "'IBM Plex Mono'" in style and "'IBM Plex Sans'" in style


def test_a_pill_is_a_direct_child_or_its_count_becomes_one(web):
    """`.pills span` matched the count inside each pill as well as the
    pill itself, so every count wore the background, the padding and the
    divider of the pill around it - four boxes inside four boxes, which
    is what the row looked like (2026-09-04)."""
    _, _, body = web().request("GET", "/login")
    style = body[body.index("<style>"):body.index("</style>")]
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
def _dash(monkeypatch, **more):
    """The dashboard's read, with the fixture's rows and whatever a test
    wants changed on top."""
    base = app_mod.read.dashboard(None)
    base.update(more)
    monkeypatch.setattr(app_mod.read, "dashboard", lambda s, owner_id=None:
                        dict(base))
    return base


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
    assert '/phones/1500/state' in body and 'value="taken"' in body
    assert 'value="unused"' in body and "Release" in body, \
        "a taken phone can be let go"
    assert body.count('value="done"') == 1, "only the taken phone closes here"
    assert body.count('value="failed"') == 1
    # Only on the free phone: a phone you hold comes back first - Done,
    # Failed, Release - and its exit is changed once it is back.
    assert body.count("Change IP") == 1, "on the free phone only"
    shelf = body.index('/phones/1500/state')
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
    status, headers, _ = client.request(
        "GET", "/phones/1500/live?said=queued:71")
    assert status == 303
    assert dict(headers)["Location"] == "https://phone.geelark.com/i?t=abc"

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
    # Colour is for the three that end something - and Take, which is the
    # one thing a row is for. Boot and the exit button are ordinary work
    # on an ordinary phone: they wore green and amber, which is a page
    # shouting five times and so emphasising nothing (2026-09-05).
    for klass, label in (("quiet go", "Take"), ("quiet", "Release"),
                         ("quiet ok", "Done"), ("quiet bad", "Failed"),
                         ("quiet", "Change IP")):
        assert f'class="{klass}">{label}<' in body, label
    assert ">Boot<" in body
    assert 'class="quiet live"' not in body, "Boot is not a fourth colour"
    assert 'class="quiet warn">Change IP' not in body, "nor is the exit"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_a_phone_somebody_else_holds_offers_only_their_name(web, monkeypatch):
    """The three ways a phone comes back belong to the person holding it.
    Offering them to anybody else is offering to act on a phone that is
    not theirs (the contract, 2026-09-05)."""
    _dash(monkeypatch, phones=[{"serial": "1501", "status": "ready",
                                "state": "taken", "owner": "ali"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    start = body.index('href="/phones/1501"')
    row = body[start:body.index("</tr>", start)]
    assert '<span class="age">with ali</span>' in row
    for label in ("Release", "Done", "Failed", "Boot", "Take", "Change IP"):
        assert f">{label}<" not in row, label
    assert 'class="badge manual" title="Ready">With ali</span>' in row


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
    assert got["verb"] == "add_gpt" and got["idem_key"] == "k-1"
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
    ov = body[body.index('id="poolov"'):]
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

    script = body[body.index("<script>"):body.index("</script>")]
    for forbidden in ("XMLHttpRequest", "innerHTML", "document.write",
                      "action ="):
        assert forbidden not in script, forbidden
    # Three, and only these: the page's own forms, the page itself, and
    # the phone link a serial is - the drawer fetches what the link would
    # have opened.
    calls = re.findall(r"fetch\(([^,)]+)", script)
    assert calls and all(c.strip() in ("form.action",
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

def test_only_the_dashboard_carries_a_script(web, monkeypatch):
    """The exception is one page wide. If a second page ever needs one,
    that is a decision somebody makes on purpose, not a drift."""
    _dash(monkeypatch)
    client = web()
    client.login()
    for path in ("/pools/gmail", "/pools/proxy", "/pools/gpt", "/phones"):
        _, _, body = client.request("GET", path)
        assert "<script>" not in body, path


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
    assert 'value="claude">Claude' in body
    assert 'value="">none &mdash; Google only' in body

    status, headers, _ = client.request(
        "POST", "/phones/build",
        _form(csrf=client.csrf(), gmail="pick@example.com",
              proxy_name="SX9", app="chatgpt",
              app_account="gpt@example.com"))

    assert status == 303 and dict(headers)["Location"].startswith("/")
    assert got["verb"] == "build_by_hand"
    assert got["payload"]["gmail"] == "pick@example.com"
    assert got["payload"]["no_gmail"] is False
    assert got["payload"]["proxy_name"] == "SX9", "the API may still name one"
    assert got["payload"]["app"] == "chatgpt"
    assert got["payload"]["install_app"] is True
    assert got["payload"]["app_account"] == "gpt@example.com"

    # Spotify: no account rides along, whatever the box said.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), app="spotify",
                         app_account="gpt@example.com"))
    assert got["payload"]["app"] == "spotify"
    assert got["payload"]["install_app"] is True
    assert got["payload"]["app_account"] == ""
    # None: no app at all.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), app="none"))
    assert got["payload"]["app"] == "" and got["payload"]["install_app"] is False
    # Claude is an app now.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), app="claude",
                         app_account="gpt@example.com"))
    assert got["payload"]["app"] == "claude"
    assert got["payload"]["app_account"] == "", "an account is ChatGPT's only"
    # No Gmail: a bare phone, whatever the other boxes carried.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="none", app="chatgpt",
                         app_account="gpt@example.com"))
    assert got["payload"]["no_gmail"] is True
    assert got["payload"]["gmail"] == "" and got["payload"]["app"] == ""
    assert got["payload"]["app_account"] == ""
    assert got["payload"]["gmail_typed"] is False
    # The old form's tick still means ChatGPT.
    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), install_app="1"))
    assert got["payload"]["app"] == "chatgpt"


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

    # Three choices, each a select - the exit is the build's own business
    # now: the Gmail (auto, none, or "choose...", which opens a dialog to
    # type one or pick a free one), the app, and the account (the
    # operator, 2026-09-10).
    card = body[body.index('class="byhand"'):body.index("Build</button>")]
    for name in ("gmail", "app", "app_account"):
        assert f'<select name="{name}"' in card, name
    assert 'name="proxy_name"' not in card, "an exit is never chosen here"
    assert 'name="gmail" data-new="gmail-new"' in card
    assert 'name="app_account" data-new="account-new"' in card
    assert card.count("choose&hellip;</option>") == 2
    assert "type a new one" not in card
    assert '<option value="">auto &mdash; the next free one (1 free)</option>' in card
    assert '<option value="none">none &mdash; no Google account</option>' in card
    assert '<option value="chatgpt" selected>ChatGPT</option>' in card
    assert '<option value="spotify">Spotify</option>' in card
    assert '<option value="claude">Claude</option>' in card
    assert '<option value="">none &mdash; Google only</option>' in card
    assert '<option value="">none &mdash; sign in later</option>' in card
    assert 'name="install_app"' not in card, "the tick became the App choice"
    assert '<optgroup label="pick one">' not in card, "the free rows moved into the dialog"
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
    assert '<input type="radio" name="pick-account-new" value="g@x.com"> g@x.com' in adlg
    for name in ("gmail_password", "gmail_secret", "app_password", "app_secret"):
        assert f'<input type="hidden" name="{name}" value="">' in body, name
    script = pages._DASH_SCRIPT
    assert "acctPick.disabled = !on" in script, "an account only with ChatGPT"
    assert "gmailPick.value === 'none'" in script, "no Gmail: no app, no account"
    assert "appPick.disabled = bare" in script
    assert "function openNew(pick, was)" in script
    assert "' (from the pool)'" in script, "a picked row says where it came from"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_without_the_app_no_gpt_account_is_asked_for(web, monkeypatch):
    """Unticking the app is asking for a warm phone. Carrying an account
    alongside would spend one on a phone that has nowhere to sign it in."""
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch)
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 90)
    client = web()
    client.login()

    client.request("POST", "/phones/build",
                   _form(csrf=client.csrf(), gmail="a@example.com",
                         app_account="gpt@example.com"))

    assert got["payload"]["install_app"] is False
    assert got["payload"]["app_account"] == ""


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
    assert '<option value="none" selected>none &mdash; no Google account</option>' in body
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

    assert body.count('class="addbox"') == 3, "all three pools (2026-09-08)"
    assert 'action="/pools/gmail/preview"' in body
    assert 'action="/pools/gpt/preview"' in body
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
    _, _, body = client.request("GET", "/")

    assert 'action="/pools/gmail/preview"' in body
    assert 'action="/pools/gpt/preview"' not in body


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
    """All three sheets ride in the one response - the rows are read for
    the cards anyway - so the manager cannot fail to open."""
    _dash(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")

    ov = body[body.index('<div class="ov" id="poolov"'):]
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

    ov = body[body.index('<div class="ov" id="poolov"'):]
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

    body = inspect.getsource(read._pool_rows)
    assert "status {op} 'used'" in body and "status {op} 'delivered'" in body
    assert body.count('format(op="<>")') == 2, "live rows, read apart"
    assert body.count('format(op="=")') == 2, "spent rows, read apart"
    assert "on_sheet" not in body, "the sheet flag is retired"
    assert "AS password" in read._HELD and "AS secret" in read._HELD, (
        "what the editor opens with")


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

    ov = body[body.index('id="poolov"'):]
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
    assert 'data-password="Kx82!mnQ"' in row("free@gmail.com")
    assert 'data-secret="JBSWY3DPEHPK3PXP"' in row("free@gmail.com")
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
    script = body[body.index("<script>"):body.index("</script>")]
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
    assert got[-1]["idem_key"] == "undo-41"


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
    assert 'meta[name="gf-rev"]' in body[body.index("<script>"):]

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
    ov = body[body.index('id="poolov"'):]

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
    assert ".said.no" in pages.page("t", "", user={"id": 1, "username": "a",
                                                   "role": "operator"})

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
    assert "f.password.value = tr.dataset.password || ''" in script
    assert "f.secret.value = tr.dataset.secret || ''" in script
    assert "closeEditor(form.closest('dialog.editor'))" in script, (
        "the answer shows in the sheet, not under the dialog")

    table = pages._pool_table(
        "gmail", [dict(rows[0], password="p4ss", secret="JBSWY3DP",
                       second="authenticator", serial="")], user)
    assert 'data-password="p4ss"' in table and 'data-secret="JBSWY3DP"' in table
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
    assert '<option value="">none &mdash; sign in later</option>' in card, (
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
    nobody can watch move."""
    from geelark_farm.web import pages

    gate = pages._DASH_SCRIPT.split("function settled(){", 1)[1]
    # To the next function, so a nested one does not cut it short.
    gate = gate.split("function reloadWhenSettled", 1)[0]
    assert "openKind !== 'phone'" in gate


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
    assert "main.full{padding:40px 20px}" in pages.page("t", "", user=who)


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
    assert "form.busy{cursor:progress}" in pages.page(
        "t", "", user={"id": 1, "username": "a", "role": "operator"})


def test_the_confirm_is_placed_in_the_window_and_does_not_outlive_a_scroll():
    """It sat at the row position on the document, so a Remove near the
    foot of a long list asked below the fold - the press looked like it
    had done nothing - and one scroll left the bubble over another row."""
    from geelark_farm.web import pages

    script = pages._DASH_SCRIPT
    assert "window.innerHeight - size.height - 8" in script
    assert "window.innerWidth - size.width - 8" in script
    assert "{capture: true, once: true}" in script
    assert ".mini{position:fixed" in pages.page(
        "t", "", user={"id": 1, "username": "a", "role": "operator"})


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

    ov = body[body.index('id="poolov"'):]
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

    _, _, body = web().request("GET", "/login")
    style = body[body.index("<style>"):body.index("</style>")]
    rules = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
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
    assert 'data-ask="Phone 1856 failed? The next sync deletes' in row
    assert 'data-yes="Yes, phone 1856 is failed"' in row
    assert 'data-ask="Phone 1856 done? The next sync deletes' in row
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
    assert ">Boot<" not in row("1862") and ">Take<" in row("1862")
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
    assert "if (/[?&]said=queued/.test(got.url))" in script, (
        "queued is not done: look again shortly")


def test_the_mirror_marks_what_is_running_in_one_statement():
    from geelark_farm import serve as serve_mod
    from geelark_farm import phones as phones_mod
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
    assert "done_at IS NULL" in sql and params == (["1862", "1848"],) * 2
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

    assert 'class="dim maker" title="asked for on the build card">built by ali</span>' in row("1950")
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
    sheet = body[body.index('data-sheet="proxy"'):]
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
    """What the Proxy tab said, said here: free / on a phone / starting /
    dead, with why and since when, all of it on one list with the tab's
    chips - and `claimed` no longer filed as an error, since it is a
    build that took the exit seconds ago (the operator, 2026-09-09)."""
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
    sheet = body[body.index('data-sheet="proxy"'):]
    head = sheet[:sheet.index("<tbody")]

    # The tab's chips, `all` pressed, each with its count.
    assert 'data-group="" aria-pressed="true">all<b>6</b>' in head
    assert 'data-group="free" aria-pressed="false">free<b>1</b>' in head
    assert 'data-group="on a phone" aria-pressed="false">on a phone<b>2</b>' \
        in head
    assert 'data-group="dead" aria-pressed="false">dead<b>3</b>' in head
    # The tab's columns.
    for col in ("Name", "State", "Address", "Exit IP", "Used", "Phone"):
        assert f"<th>{col}</th>" in head
    assert "<th>Note</th>" not in head, "no Note column (the operator, 2026-09-09)"
    # Test all, saying how many dead ones it would give another chance.
    assert 'action="/pools/proxy/test-all"' in head
    assert "Test all · 2 dead" in head

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
    # Starting: filed with the phones, says a build took it, Free only.
    assert 'data-group="on a phone"' in row("SX3")
    assert 'title="a build took it 20s ago' in row("SX3")
    assert "/pools/proxy/free" in row("SX3")
    assert "/pools/proxy/test" not in row("SX3")
    # Dead: why on the hover, and Test - answering is what frees it.
    assert "Proxy connection failed" in row("SX4")
    assert "<td>Proxy" not in row("SX4") and "<td>GeeLark" not in row("SX4")
    assert "/pools/proxy/test" in row("SX4") and "/pools/proxy/free" not in row("SX4")
    # Needs a new IP: filed with the dead, Free (tested first) offered.
    assert 'data-group="dead"' in row("SX5")
    assert "/pools/proxy/free" in row("SX5")
    # Suspect - a host Google kept challenging - the same three doors,
    # and the count on the hover (2026-09-09).
    assert 'data-group="dead"' in row("SX6")
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

