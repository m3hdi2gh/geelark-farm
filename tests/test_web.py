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
        "pulse": {"warm": 5, "target": 5, "tripped": "", "at": 0}})
    monkeypatch.setattr(app_mod.read, "events", lambda s, limit=200: [])
    monkeypatch.setattr(app_mod.read, "nav_counts",
                        lambda s: {"gmail": 3, "proxy": 2, "app": 1,
                                   "pending": 0})
    # every test gets clean auth state
    monkeypatch.setattr(app_mod, "_sessions", {})
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
    assert status == 403
    status, _, _ = client.request("GET", "/needs")
    assert status == 403


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
    assert status == 403


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
    assert status == 403
    status, _, _ = client.request("POST", "/users/new",
                                  f"csrf={client.csrf()}&username=x")
    assert status == 403


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
    monkeypatch.setattr(users_mod, "set_password",
                        lambda s, uid, pw: chosen.append((uid, pw)))
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
           "has_recovery": False, "source": "sheet", "phone_status": "ready"}
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
    assert "2</span> free — enough for the next 2 builds" in body
    assert "Queued <span class=\"n\">2</span>" in body, "the four pills"
    assert 'href="/pools/gmail?view=errored"' in body

    _, _, body = client.request("GET", "/pools/gmail?view=on_phone")
    assert "on@x.com" in body and '<a href="/phones/1551">1551</a>' in body


def test_pool_pages_are_shared_stock_that_everyone_signed_in_sees(
        web, monkeypatch):
    """Stock is shared; what a person may DO on it is the buttons' job."""
    _gmail_active(monkeypatch)
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own"})
    client = web()
    client.login(username="narrow")
    assert client.request("GET", "/pools/gmail")[0] == 200
    status, headers, _ = client.request("GET", "/pools")
    assert status == 303 and dict(headers)["Location"] == "/pools/gmail"


def test_buttons_stay_hidden_until_the_flag_and_the_permission_agree(
        web, monkeypatch):
    _gmail_active(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gmail")
    assert "/pools/gmail/preview" not in body, "flag off: no form"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_add_form_needs_the_flag_and_the_permission_together(
        web, monkeypatch):
    _gmail_active(monkeypatch)
    admin = web()
    admin.login()
    _, _, body = admin.request("GET", "/pools/gmail")
    assert "/pools/gmail/preview" in body

    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "own", "may_add_gmail": False})
    narrow = web()
    narrow.login(username="narrow")
    _, _, body = narrow.request("GET", "/pools/gmail")
    assert "/pools/gmail/preview" not in body
    assert ("Adding gmails needs the add-gmails permission - ask an admin"
            in body), "flag on, permission off: say which one"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_the_add_panel_offers_a_paste_and_a_one_by_one_way_in(
        web, monkeypatch):
    _gmail_active(monkeypatch)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/gmail")
    assert body.count('action="/pools/gmail/preview"') == 2, \
        "the paste box and the folded one-by-one share the preview"
    assert "<summary>add one by hand</summary>" in body, "folded, not a panel"
    assert 'name="pasted"' in body and 'name="address"' in body
    assert 'name="password"' in body and 'name="second"' in body
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
    assert '<span class="badge ready">ready</span>' in body
    assert '<span class="badge info">building</span>' in body
    assert '<span class="badge attn">incomplete</span>' in body
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
    monkeypatch.setattr(app_mod.read, "known",
                        lambda s, kind: {"g0@example.com"})
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
    assert '<span class="badge bad">already in the pool</span>' in body
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
    monkeypatch.setattr(app_mod.read, "known", lambda s, kind: set())
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
def test_a_refusal_is_written_down_with_the_missing_permission(
        web, monkeypatch):
    import geelark_farm.store.actions as actions_mod
    import geelark_farm.store.users as users_mod

    monkeypatch.setattr(users_mod, "may", lambda user, permission: False)
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda *a, **k: pytest.fail("queued anyway"))
    noted = {}

    def record_refused(settings, *, verb, payload, requested_by, reason):
        noted.update(verb=verb, payload=payload, reason=reason)
        return 32

    monkeypatch.setattr(actions_mod, "record_refused", record_refused)
    client = web()
    client.login()
    status, headers, _ = client.request(
        "POST", "/pools/proxy/test", _form(csrf=client.csrf(), name="SX1"))
    assert status == 303
    assert dict(headers)["Location"] == "/pools/proxy?said=refused"
    assert noted["verb"] == "test_proxy" and noted["payload"]["name"] == "SX1"
    assert "may_add_proxy" in noted["reason"]


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
    assert "Everything running" in body, "one sentence, not a bar of numbers"
    assert "Ready to deliver" in body
    assert ">12</b><i>gmail</i>" in body, "the strip, one cell per pool"
    assert ">20</b><i>proxies</i>" in body
    assert "last pass" not in body, "the pass's clock is the alert strip's job"
    assert "IronHawk@gmail.com" in body and "SX27" in body
    assert 'class="badge warn">warm' in body
    assert "arman@gmail.com" in body and "gpt4.avir@proton.me" in body
    assert "manual · mehdi" in body
    assert "Change IP" not in body, "mutations are off"
    assert 'name="addresses"' not in body, "manual login is off"
    assert "log in on their own" in body


@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_with_manual_login_on_the_dashboard_offers_the_buttons(web):
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert body.count("Change IP") == 2, "one per phone, both states"
    assert body.count('name="addresses"') == 2
    assert "Log in selected" in body


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
    assert dict(headers)["Location"].startswith("/requests?said=queued")
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
    _, _, body = client.request("GET", "/phones/1523")
    assert 'action="/phones/1523/state"' in body
    assert 'value="unused"' in body, "taken, so it offers Back"
    assert 'name="back" value="/phones/1523"' in body
    assert 'action="/phones/1523/proxy"' in body

    status, _, body = client.request(
        "POST", "/phones/1523/state",
        _form(csrf=client.csrf(), state="failed", back="/phones/1523"))
    assert status == 200 and "Mark phone 1523 failed?" in body
    assert 'name="back" value="/phones/1523"' in body and got == {}
    status, headers, _ = client.request(
        "POST", "/phones/1523/state",
        _form(csrf=client.csrf(), state="failed", sure="1",
              back="/phones/1523"))
    assert status == 303
    assert dict(headers)["Location"] == "/phones/1523?said=queued:63"
    assert got["verb"] == "set_phone_state"
    status, headers, _ = client.request(
        "POST", "/phones/1523/state",
        _form(csrf=client.csrf(), state="taken", back="/evil"))
    assert dict(headers)["Location"].startswith("/?said="), \
        "a back the form made up goes to the dashboard"

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
        assert status == 403, path


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
    monkeypatch.setattr(app_mod.read, "known", lambda s, kind: set())
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
def test_the_proxy_page_names_the_permission_it_lacks(web, monkeypatch):
    import geelark_farm.store.users as users_mod

    monkeypatch.setattr(users_mod, "may", lambda user, permission: False)
    _proxy_pool(monkeypatch, rows=[_proxy_row("SX1"),
                                   _proxy_row("SX2", "dead")],
                state={"unlisted_proxies": [
                    {"host": "1.2.3.4", "port": 9999, "username": "u",
                     "password": "p"}]})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/pools/proxy")
    assert "needs the may_add_proxy permission" in body
    assert 'action="/pools/proxy/preview"' not in body
    for button in ("Test all now", ">Remove<", ">Test<"):
        assert button not in body, button

    _, _, body = client.request("GET", "/pools/proxy?view=needs_hand")
    assert "needs the may_add_proxy permission" in body
    for button in (">Ignore<", "Add to pool", "Test again",
                   "IP changed"):
        assert button not in body, button


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
    strip = body[body.index('class="strip"'):]
    strip = strip[:strip.index("</div>")]
    assert 'color:var(--red)">0</b><i>gmail</i>' in strip
    assert "nothing can be built until rows are added" in strip
    assert 'color:var(--amber)">2</b><i>proxies</i>' in strip
    assert "fewer than the 5 phones the keeper keeps warm" in strip
    assert 'color:var(--amber)">7</b><i>GPT</i>' in strip
    assert "2 of them have no phone to go to" in strip
    # the headline counts only what can be handed over right now, and the
    # rest of the shelf reads beside it, each in its badge's colour
    head = body[body.index("Ready to deliver"):body.index('class="strip"')]
    assert re.search(r'class="n"[^>]*>1</div>', head), "one ready, one taken"
    assert 'color:var(--amber)">1</b>warm' in head
    assert 'color:var(--violet)">1</b>taken' in head
    assert 'color:var(--blue)">1</b>building' in head
    assert "incomplete" not in head, "a count nobody has is not printed"
    assert "behind them" not in head, "counts, not a sentence about them"


def test_a_farm_with_nothing_ready_says_so_quietly(web, monkeypatch):
    _dash(monkeypatch, phones=[{"serial": "1502", "status": "app_only",
                                "state": ""}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    head = body[body.index("Ready to deliver"):body.index('class="strip"')]
    assert re.search(r'class="n" style="color:var\(--dim\)">0</div>', head)
    assert "Take one to hand it over" not in head


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
    assert 'colspan="3"' in body, "spans the gmail / gpt / proxy columns"
    assert "starting" in body, "a phone with no line yet"
    assert 'href="/phones/1556"' in body
    assert 'http-equiv="refresh"' in body


def test_the_keepers_warning_sits_above_the_tiles_with_the_fix_linked(
        web, monkeypatch):
    _dash(monkeypatch, pulse={"warm": 2, "target": 5, "tripped": "",
                              "at": 0, "warning": "the Gmail tab has no "
                                                  "free rows to build from"})
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert "the Gmail tab has no free rows" in body
    warn = body[body.index('class="alert warn"'):]
    assert 'href="/pools/gmail"' in body[:body.index('class="alert warn"') + 200]
    assert "open the Gmail pool" in warn[:400]
    assert body.index('class="alert warn"') < body.index("Ready to deliver")

    _dash(monkeypatch, pulse={"warm": 2, "target": 5, "at": 0,
                              "tripped": "captcha_shown x5",
                              "warning": "captcha_shown x5"})
    _, _, body = client.request("GET", "/")
    assert 'href="/events?kind=breaker"' in body


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
             ("1500", "1501", "1502", "1503")]
    assert order == sorted(order), "ready, warm, incomplete, building"
    assert 'class="hand"' not in body, "the hand-over line is on /phones/1500"
    assert "waiting for an account" in body, "the warm row says what it lacks"


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_take_back_done_and_failed_are_gated_and_the_deleting_ones_ask(
        web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    _dash(monkeypatch, phones=[
        {"serial": "1500", "status": "ready", "state": "",
         "gmail": "IronHawk@gmail.com", "app_account": "h@x.com",
         "proxy_name": "SX27"},
        {"serial": "1501", "status": "ready", "state": "taken",
         "owner": "ali", "updated_at": "2026-09-03 10:00:00+00"}])
    got = {}
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda s, **k: got.update(k) or 61)
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    assert 'class="dim">ali' in body and app_mod.pages._when(
        "2026-09-03 10:00:00+00") in body
    assert '/phones/1500/state' in body and 'value="taken"' in body
    assert 'value="unused"' in body and "Release" in body, \
        "a taken phone can be let go"
    assert body.count('value="done"') == 1, "only the taken phone closes here"
    assert body.count('value="failed"') == 1
    assert body.count("Change IP") == 2, "in both states, beside the rest"
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
    assert "added 14m ago" in body
    assert 'class="pick tick"' in body and ".pick:has(input:checked)" in body
    assert "5 warm phones can take them" in body and "Log in selected" in body

    base["pulse"] = {"warm": 0, "target": 5, "tripped": "", "at": 0}
    _, _, body = client.request("GET", "/")
    assert "Log in selected" not in body
    assert "no warm phone is free" in body


def test_the_ticker_tells_requests_and_events_as_sentences(web, monkeypatch):
    _dash(monkeypatch,
          recent=[{"at": "2026-09-01 18:04:31+00", "kind": "build_finished",
                   "serial": "1551", "status": "ready",
                   "detail": "ok=True gmail=x"},
                  {"at": "2026-09-01 17:36:02+00", "kind": "breaker",
                   "serial": "", "status": "tripped",
                   "detail": "5 in a row: captcha_shown"}],
          asked=[{"id": 241, "verb": "login_accounts", "status": "running",
                  "payload": {"addresses": ["a@x.com", "b@x.com"]},
                  "at": "2026-09-01 18:06:12+00", "requested_by": "mehdi"},
                 {"id": 240, "verb": "change_proxy", "status": "failed",
                  "payload": {"serial": "1549"},
                  "at": "2026-09-01 18:02:51+00", "requested_by": "alireza"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    hhmm = app_mod.pages._clock("2026-09-01 18:06:12+00")[:5]
    assert (f'{hhmm}</span> <b>mehdi</b> asked: Log in 2 accounts → '
            f'<span style="color:var(--blue)">running</span>') in body
    assert 'phone <a href="/phones/1551">1551</a> became ready' in body
    assert "the breaker tripped — 5 in a row: captcha_shown" in body
    assert ('Change IP on <a href="/phones/1549">1549</a> → '
            '<span style="color:var(--red)">failed</span>') in body
    foot = body[body.index("all events") - 2000:body.index("all events")]
    assert foot.index("mehdi") < foot.index("1551") < foot.index("alireza"), \
        "newest first, requests and events interleaved by time"


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
         "owner": "ali"}])
    client = web()
    client.login()
    _, _, body = client.request("GET", "/")
    for klass, label in (("quiet go", "Take"), ("quiet", "Release"),
                         ("quiet ok", "Done"), ("quiet bad", "Failed"),
                         ("quiet warn", "Change IP")):
        assert f'class="{klass}">{label}<' in body, label
    assert 'class="quiet live"' in body and ">Boot<" in body


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
    assert "accounts log in on their own" in body, "that line stays"


def test_the_service_row_fits_the_pulse_and_is_admin_only():
    """The Settings page's parts, tested where they live. Which controls
    are offered follows the pulse: a running service is paused or
    stopped, a tripped one is resumed and cleared, a stopped one starts."""
    admin = {"id": 1, "username": "mehdi", "role": "admin", "csrf": "x",
             "mutations": True}
    flags = {"web_mutations": True, "manual_login": False, "log_db": True,
             "pools_in_pg": False, "web_user_admin": True}

    running = {"pulse": {"warm": 5, "target": 5, "tripped": "",
                         "paused": False, "at": 0}}
    row = app_mod.pages._service_row(running, admin, flags)
    assert 'action="/service/pause"' in row and "Pause building" in row
    assert 'action="/service/stop"' in row
    assert "/service/resume" not in row and "/service/clear_breaker" not in row
    assert "WEB_MUTATIONS" in row and "POOLS_IN_PG" in row

    tripped = {"pulse": {"warm": 5, "target": 5, "tripped": "captcha x5",
                         "paused": True, "at": 0}}
    row = app_mod.pages._service_row(tripped, admin, flags)
    assert 'action="/service/resume"' in row and "Resume building" in row
    assert 'action="/service/clear_breaker"' in row and "/service/pause" not in row

    stopped = {"pulse": {"stopped": True, "at": 0, "tripped": ""}}
    row = app_mod.pages._service_row(stopped, admin, flags)
    assert 'action="/service/start"' in row and "/service/stop" not in row

    operator = {"id": 9, "username": "narrow", "role": "operator",
                "csrf": "x", "mutations": True}
    assert app_mod.pages._service_row(running, operator, flags) == "", \
        "no ticked permission shows them"


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


@pytest.mark.parametrize("web", [MUTATIONS_ON], indirect=True)
def test_needs_buttons_follow_each_permission_and_say_which_is_missing(
        web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    _needs(monkeypatch)
    noted = {}
    monkeypatch.setattr(actions_mod, "record_refused",
                        lambda s, **k: noted.update(k) or 82)
    monkeypatch.setattr(actions_mod, "enqueue",
                        lambda *a, **k: pytest.fail("queued anyway"))
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "all", "may_add_gmail": True,
                         "may_add_gpt": False, "may_take_phones": False})
    client = web()
    client.login(username="narrow")
    _, _, body = client.request("GET", "/needs")
    assert body.count('action="/needs/offer"') == 1, "gmail yes, app no"
    assert 'name="kind" value="gmail"' in body
    assert "offering accounts again needs the may_add_gpt permission" in body
    assert "/needs/clear" not in body
    assert "clearing tries needs the may_take_phones permission" in body

    status, headers, _ = client.request(
        "POST", "/needs/offer",
        _form(csrf=client.csrf(), kind="app", address="a@y.com"))
    assert status == 303 and dict(headers)["Location"] == "/needs?said=refused"
    assert "may_add_gpt" in noted["reason"]


# ------------------------------------------------- Gpt Pool and Requests
# The paste-and-preview way in for GPT accounts, the by-hand form that
# comes back filled in when refused, ticks on the pool page itself, the
# set-aside section in words, the delivered archive's pager and CSV, and
# the Requests page's pages, highlights and sub-stories.

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
                        lambda s, kind: {"dup@x.com"} if kind == "app"
                        else set())
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
    assert "already in the pool" in body, "dup@x.com is known to the mirror"
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
    for path in ("/pools/gmail", "/pools/gpt"):
        status, _, body = client.request("GET", path)
        assert status == 200, path
        assert "<summary>add one by hand</summary>" in body, path
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


@pytest.mark.parametrize("web", [MANUAL_ON], indirect=True)
def test_the_gpt_pool_names_each_permission_it_lacks(web, monkeypatch):
    _gpt_active(monkeypatch, waiting=[_app_row("c@x.com")],
                needs_human=[_app_row("h@x.com", "payment_problem")])
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "all", "may_add_gpt": False,
                         "may_login_accounts": False})
    client = web()
    client.login(username="narrow")
    _, _, body = client.request("GET", "/pools/gpt")
    assert 'action="/pools/gpt/preview"' not in body
    assert "adding accounts needs the may_add_gpt permission" in body
    assert 'name="addresses"' not in body
    assert "logging accounts in needs the may_login_accounts permission" in body
    assert "log in on their own" not in body, "manual login is on"

    _, _, body = client.request("GET", "/pools/gpt?view=needs_human")
    assert "Offer again" not in body
    assert "offering accounts again needs the may_add_gpt permission" in body


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


@pytest.mark.parametrize("web", [True], indirect=True)
def test_put_it_back_needs_the_add_proxy_permission(web, monkeypatch):
    import geelark_farm.store.actions as actions_mod

    monkeypatch.setattr(actions_mod, "listing", lambda s, **k: [
        {"id": 236, "verb": "remove_proxy", "status": "done",
         "payload": {"name": "SX3"}, "result": "SX3 removed",
         "detail": {"removed": {"name": "SX3", "raw": "1.2.3.4:9999:u:p"}},
         "requested_at": "2026-09-01 18:06:12+00:00",
         "executed_at": "2026-09-01 18:06:30+00:00",
         "finished_at": "2026-09-01 18:06:31+00:00",
         "requested_by": "mehdi"}])
    monkeypatch.setattr(actions_mod, "counts", lambda s, **k: {"done": 1})
    monkeypatch.setattr(FakeStore, "user",
                        {"id": 9, "username": "narrow", "role": "operator",
                         "sees": "all", "may_add_proxy": False})
    client = web()
    client.login(username="narrow")
    _, _, body = client.request("GET", "/requests")
    assert "SX3 removed" in body and "Put it back" not in body
    assert 'class="live"' not in body, "nothing pending"


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
