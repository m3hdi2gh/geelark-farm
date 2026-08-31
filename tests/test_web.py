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
    monkeypatch.setattr(app_mod.read, "events", lambda s, limit=200: [])
    # every test gets clean auth state
    monkeypatch.setattr(app_mod, "_sessions", {})
    monkeypatch.setattr(app_mod, "_failures", {})

    settings = make_settings(store_enabled=True, web_enabled=True, web_port=0,
                             web_mutations=getattr(request, "param", False))
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
    status, _, _ = client.request("GET", "/pools")
    assert status == 403
    status, _, _ = client.request("GET", "/events")
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
