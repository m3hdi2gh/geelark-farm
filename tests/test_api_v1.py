"""The panel API's read side, over real HTTP against a fake store.

The same bargain test_web.py strikes: real sockets and real header parsing,
because this door's bugs live in Authorization headers and JSON encodings,
with the store faked so the suite still runs on a machine that has never
seen the cluster.
"""

from __future__ import annotations

import json

import pytest

import geelark_farm.web.api_v1 as api_mod
import geelark_farm.web.api_v1_read as read_mod
from tests.test_web import web  # noqa: F401  (the live-server fixture)

API_ON = {"web_api": True}
KEY = "a-key-the-panel-was-given"


def _account(**more) -> dict:
    """A resources row as api_v1_read hands one over."""
    row = {"id": 41, "address": "arman.tehrani88@gmail.com", "status": "",
           "error": None, "serial": "", "note": "", "source": "panel",
           "product": "chatgpt", "credential_kind": "password_totp",
           "panel_ref": "ord_84213-a", "client_id": 1, "attempts": 0,
           "failures": 0, "customer_ready": False, "state_changed_at": None,
           "delivered_at": None, "withdrawn_at": None,
           "created_at": "2026-09-05T10:12:40+00:00",
           "updated_at": "2026-09-05T10:12:40+00:00"}
    row.update(more)
    return row


def _client(monkeypatch, role: str = "panel") -> dict:
    """One key that works, and nothing else. Exercises the dispatcher's
    own lockout rather than faking it away."""
    monkeypatch.setattr(api_mod, "_failures", {})
    monkeypatch.setattr(api_mod, "_seen", lambda settings, client_id: None)
    known = {"id": 1, "name": "panel", "role": role,
             "key_hash": api_mod.hash_key(KEY), "webhook_url": "",
             "active": True}
    monkeypatch.setattr(api_mod, "client_for",
                        lambda settings, token: known if token == KEY else None)
    return known


def _get(client, path: str, *, key: str | None = KEY):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    status, got, body = client.request("GET", path, headers=headers)
    return status, dict(got), (json.loads(body) if body else None)


# ------------------------------------------------------------- the switch
def test_the_api_is_absent_until_the_flag_is_on(web, monkeypatch):  # noqa: F811
    """A 404, not a 403: a 403 tells an unknown caller there is something
    here worth coming back for."""
    _client(monkeypatch)
    client = web()
    status, _, body = _get(client, "/api/v1/health")
    assert status == 404 and body["error"]["code"] == "not_found"


def test_web_api_defaults_off(make_settings):
    """The proof a fresh deploy is dark - the fixture had to ask for it."""
    assert make_settings().web_api is False


# --------------------------------------------------------------- the key
@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_a_request_without_a_key_is_refused_and_says_how(web, monkeypatch):  # noqa: F811
    _client(monkeypatch)
    client = web()
    status, headers, body = _get(client, "/api/v1/health", key=None)
    assert status == 401 and body["error"]["code"] == "unauthorized"
    assert headers["WWW-Authenticate"].startswith("Bearer ")
    assert headers["Content-Type"].startswith("application/json")
    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_a_key_in_the_query_string_is_not_a_key(web, monkeypatch):  # noqa: F811
    """A key in a URL lands in access logs, Referer headers and history.
    Only the Authorization header is read."""
    _client(monkeypatch)
    client = web()
    status, _, body = _get(client, f"/api/v1/health?key={KEY}", key=None)
    assert status == 401 and body["error"]["code"] == "unauthorized"


@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_five_wrong_keys_buy_a_wait(web, monkeypatch):  # noqa: F811
    _client(monkeypatch)
    client = web()
    for _ in range(api_mod.LOCKOUT_AFTER):
        status, _, _ = _get(client, "/api/v1/health", key="wrong-one")
        assert status == 401
    status, _, body = _get(client, "/api/v1/health", key="wrong-one")
    assert status == 429 and body["error"]["code"] == "rate_limited"


@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_the_door_reads_only(web, monkeypatch):  # noqa: F811
    _client(monkeypatch)
    client = web()
    status, _, body = client.request(
        "POST", "/api/v1/accounts", "{}",
        headers={"Authorization": f"Bearer {KEY}"})
    assert status == 405 and json.loads(body)["error"]["code"] == "not_allowed"


# ------------------------------------------------------------ the answers
@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_health_says_which_kinds_are_actually_served(web, monkeypatch):  # noqa: F811
    _client(monkeypatch)
    monkeypatch.setattr(read_mod, "health", lambda s: {
        "ok": True, "served": {"chatgpt": ["password_totp"], "claude": []},
        "accounts": 312, "warm_phones": 6})
    client = web()
    status, _, body = _get(client, "/api/v1/health")
    assert status == 200 and body["ok"] is True
    assert body["served"] == {"chatgpt": ["password_totp"], "claude": []}
    assert body["warm_phones"] == 6


@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_one_account_reads_by_either_ref(web, monkeypatch):  # noqa: F811
    """The panel's own reference, and the farm-issued one every account
    that predates the panel carries."""
    seen = {}
    _client(monkeypatch)
    monkeypatch.setattr(read_mod, "account",
                        lambda s, ref: seen.update(ref=ref) or _account())
    client = web()
    status, _, body = _get(client, "/api/v1/accounts/ord_84213-a")
    assert status == 200 and seen["ref"] == "ord_84213-a"
    assert body["ref"] == "ord_84213-a" and body["state"] == "queued"
    assert body["email"] == "arman.tehrani88@gmail.com"
    assert body["product"] == "chatgpt"

    _get(client, "/api/v1/accounts/farm_41")
    assert seen["ref"] == "farm_41"

    monkeypatch.setattr(read_mod, "account", lambda s, ref: None)
    status, _, body = _get(client, "/api/v1/accounts/nope")
    assert status == 404 and body["error"]["code"] == "not_found"


@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_no_answer_ever_carries_a_credential(web, monkeypatch):  # noqa: F811
    """Credentials go in and never come out. The row the reader hands over
    has them; the JSON must not."""
    _client(monkeypatch)
    monkeypatch.setattr(read_mod, "account", lambda s, ref: dict(
        _account(), password="S9!kdm2Lqa", totp_secret="JBSWY3DPEHPK3PXP",
        recovery_email="backup@x.com", backup_codes=["8291 4472"]))
    client = web()
    _, _, body = _get(client, "/api/v1/accounts/ord_84213-a")
    flat = json.dumps(body)
    for secret in ("S9!kdm2Lqa", "JBSWY3DPEHPK3PXP", "backup@x.com",
                   "8291 4472"):
        assert secret not in flat, secret


@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_the_list_pages_by_cursor(web, monkeypatch):  # noqa: F811
    seen = {}

    def accounts(settings, *, state="", cursor="", limit=100):
        seen.update(state=state, cursor=cursor, limit=limit)
        return {"rows": [_account(), _account(id=42, panel_ref=None)],
                "more": True, "next_cursor": "Y3Vyc29y"}

    _client(monkeypatch)
    monkeypatch.setattr(read_mod, "accounts", accounts)
    client = web()
    status, _, body = _get(
        client, "/api/v1/accounts?state=queued&cursor=abc&limit=2")
    assert status == 200
    assert seen == {"state": "queued", "cursor": "abc", "limit": 2}
    assert [a["ref"] for a in body["accounts"]] == ["ord_84213-a", "farm_42"]
    assert body["next_cursor"] == "Y3Vyc29y"


@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_the_event_list_is_whatever_was_recorded(web, monkeypatch):  # noqa: F811
    _client(monkeypatch)
    monkeypatch.setattr(read_mod, "account", lambda s, ref: _account())
    import datetime as dt

    monkeypatch.setattr(read_mod, "events", lambda s, row: [
        {"at": dt.datetime(2026, 9, 5, 10, 12, 40, tzinfo=dt.timezone.utc),
         "type": "request", "verb": "add_gpt", "status": "done",
         "result": "in", "by": "panel"}])
    client = web()
    status, _, body = _get(client, "/api/v1/accounts/ord_84213-a/events")
    assert status == 200
    assert body["events"][0]["verb"] == "add_gpt"
    assert body["events"][0]["at"].endswith("Z"), "RFC 3339, UTC"


@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_a_dead_store_is_json_not_the_consoles_page(web, monkeypatch):  # noqa: F811
    """The console answers a dead cluster with an HTML page. A client
    parsing JSON must never be handed one."""
    class OperationalError(Exception):
        pass

    def boom(settings):
        raise OperationalError("connection refused")

    _client(monkeypatch)
    monkeypatch.setattr(read_mod, "health", boom)
    client = web()
    status, headers, body = _get(client, "/api/v1/health")
    assert status == 503 and body["error"]["code"] == "unavailable"
    assert headers["Content-Type"].startswith("application/json")


@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_an_unknown_path_under_the_prefix_is_a_json_404(web, monkeypatch):  # noqa: F811
    _client(monkeypatch)
    client = web()
    monkeypatch.setattr(read_mod, "account",
                        lambda s, ref: pytest.fail("read before refusing"))
    status, headers, body = _get(client, "/api/v1/accounts/x/nonsense")
    assert status == 404 and body["error"]["code"] == "not_found"
    assert headers["Content-Type"].startswith("application/json")


# --------------------------------------------------------------- writing
WRITE_ON = {"web_api": True, "web_api_writes": True}


def _wrote(monkeypatch, made=None):
    """The write side faked at its store edge, so what the test exercises
    is the dispatcher's own rules - judging, idempotency, and which states
    may still be changed."""
    import geelark_farm.web.api_v1_write as write_mod

    seen = {"queued": [], "remembered": []}

    def create(settings, row, *, client_id):
        seen["created"] = row
        return made or _account()

    monkeypatch.setattr(write_mod, "create", create)
    monkeypatch.setattr(write_mod, "enqueue",
                        lambda s, **k: seen["queued"].append(k) or 91)
    monkeypatch.setattr(write_mod, "mark_ready",
                        lambda s, ref: seen.update(ready=ref))
    monkeypatch.setattr(write_mod, "mark_withdrawn",
                        lambda s, ref: seen.update(withdrawn=ref))
    monkeypatch.setattr(write_mod, "replay", lambda s, **k: None)
    monkeypatch.setattr(write_mod, "remember",
                        lambda s, **k: seen["remembered"].append(k))
    return seen


def _post(client, path, body, *, key=KEY, idem=None):
    headers = {"Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}
    if idem:
        headers["Idempotency-Key"] = idem
    status, got, raw = client.request("POST", path, json.dumps(body),
                                      headers=headers)
    return status, dict(got), (json.loads(raw) if raw else None)


def _delete(client, path):
    status, got, raw = client.request(
        "DELETE", path, "", headers={"Authorization": f"Bearer {KEY}"})
    return status, dict(got), (json.loads(raw) if raw else None)


@pytest.mark.parametrize("web", [API_ON], indirect=True)
def test_writes_are_405_until_their_own_switch(web, monkeypatch):  # noqa: F811
    """WEB_API opens the door; WEB_API_WRITES lets a client change what is
    behind it - the shape WEB_MUTATIONS already has on the console."""
    _client(monkeypatch)
    _wrote(monkeypatch)
    client = web()
    status, _, body = _post(client, "/api/v1/accounts", {})
    assert status == 405 and body["error"]["code"] == "not_allowed"


@pytest.mark.parametrize("web", [WRITE_ON], indirect=True)
def test_a_bots_key_may_not_change_an_account(web, monkeypatch):  # noqa: F811
    """The bot's whole job is one code. It reads, and it does not buy."""
    _client(monkeypatch, role="bot")
    _wrote(monkeypatch)
    client = web()
    status, _, body = _post(client, "/api/v1/accounts", {})
    assert status == 403 and body["error"]["code"] == "forbidden"


@pytest.mark.parametrize("web", [WRITE_ON], indirect=True)
def test_a_payload_is_judged_before_anything_is_written(web, monkeypatch):  # noqa: F811
    """A refused payload leaves nothing behind for anybody to poll - which
    is the difference between refused and stuck."""
    seen = _wrote(monkeypatch)
    _client(monkeypatch)
    client = web()
    good = {"ref": "ord_1", "product": "chatgpt",
            "credential_kind": "password_totp",
            "credentials": {"email": "a@x.com", "password": "pw"}}
    for bad, field in (
            ({**good, "ref": ""}, "ref"),
            ({**good, "ref": "x" * 65}, "ref"),
            ({**good, "product": "gemini"}, "product"),
            ({**good, "credential_kind": "telepathy"}, "credential_kind"),
            ({**good, "credentials": "not-an-object"}, "credentials"),
            ({**good, "credentials": {"email": "a@x.com"}},
             "credentials.password"),
            ({**good, "credentials": {"email": "not-an-address",
                                      "password": "pw"}}, "credentials"),
            ({**good, "credentials": {"email": "a@x.com", "password": "pw",
                                      "totp_secret": "nope!!"}},
             "credentials")):
        status, _, body = _post(client, "/api/v1/accounts", bad)
        assert status == 422, bad
        assert body["error"]["code"] == "invalid"
        assert body["error"]["field"] == field, bad
    assert seen["queued"] == [], "and nothing was queued for any of them"


@pytest.mark.parametrize("web", [WRITE_ON], indirect=True)
def test_a_good_account_is_written_then_queued_for_the_sheet(
        web, monkeypatch):  # noqa: F811
    """Both halves: the row is born in the store so a GET straight after
    the POST finds it, and a request carries it into the tab the keeper
    reads - which only a pass may write."""
    seen = _wrote(monkeypatch)
    _client(monkeypatch)
    client = web()
    status, _, body = _post(client, "/api/v1/accounts", {
        "ref": "ord_84213-a", "product": "chatgpt",
        "credential_kind": "password_totp",
        "credentials": {"email": "arman.tehrani88@gmail.com",
                        "password": "S9!kdm2Lqa",
                        "totp_secret": "JBSWY3DPEHPK3PXP"}})
    assert status == 201 and body["ref"] == "ord_84213-a"
    assert body["state"] == "queued"
    assert seen["created"]["address"] == "arman.tehrani88@gmail.com"
    assert seen["queued"][0]["verb"] == "add_panel_account"
    assert seen["queued"][0]["payload"] == {"ref": "ord_84213-a"}
    carried = json.dumps(seen["queued"][0]["payload"])
    assert "S9!kdm2Lqa" not in carried and "JBSWY3DP" not in carried, (
        "a request is rendered on a page; a credential must not ride in one")


@pytest.mark.parametrize("web", [WRITE_ON], indirect=True)
def test_the_same_ref_or_address_twice_is_one_account(web, monkeypatch):  # noqa: F811
    import geelark_farm.web.api_v1_write as write_mod

    _wrote(monkeypatch)
    _client(monkeypatch)
    client = web()
    body = {"ref": "ord_1", "product": "chatgpt",
            "credential_kind": "password_totp",
            "credentials": {"email": "a@x.com", "password": "pw"}}
    for token, word in (("already_ref", "ref"),
                        ("already_address", "address")):
        monkeypatch.setattr(write_mod, "create",
                            lambda s, row, client_id, t=token: t)
        status, _, got = _post(client, "/api/v1/accounts", body)
        assert status == 409 and got["error"]["code"] == "already_exists"
        assert word in got["error"]["message"]


@pytest.mark.parametrize("web", [WRITE_ON], indirect=True)
def test_a_retried_key_is_answered_from_the_first_time(web, monkeypatch):  # noqa: F811
    """The whole point of the header: a panel that retries a POST it never
    saw the answer to must not buy the account twice."""
    import geelark_farm.web.api_v1_write as write_mod

    seen = _wrote(monkeypatch)
    _client(monkeypatch)
    client = web()
    body = {"ref": "ord_1", "product": "chatgpt",
            "credential_kind": "password_totp",
            "credentials": {"email": "a@x.com", "password": "pw"}}
    status, _, first = _post(client, "/api/v1/accounts", body, idem="k-1")
    assert status == 201
    assert seen["remembered"][0]["key"] == "k-1"

    monkeypatch.setattr(write_mod, "replay",
                        lambda s, **k: {"status": 201, "body": first})
    monkeypatch.setattr(write_mod, "create",
                        lambda s, row, client_id: pytest.fail("wrote twice"))
    status, headers, again = _post(client, "/api/v1/accounts", body,
                                   idem="k-1")
    assert status == 201 and again == first
    assert headers["Idempotent-Replayed"] == "true"


@pytest.mark.parametrize("web", [WRITE_ON], indirect=True)
def test_ready_only_moves_an_account_that_is_waiting(web, monkeypatch):  # noqa: F811
    seen = _wrote(monkeypatch)
    _client(monkeypatch)
    monkeypatch.setattr(read_mod, "SERVED",
                        {"chatgpt": ("password_totp",),
                         "claude": ("email_code_customer",)})
    monkeypatch.setattr(read_mod, "account", lambda s, ref: _account(
        product="claude", credential_kind="email_code_customer"))
    client = web()
    status, _, body = _post(client, "/api/v1/accounts/ord_84213-a/ready", {})
    assert status == 202 and seen["ready"] == "ord_84213-a"
    assert body["ref"] == "ord_84213-a"

    monkeypatch.setattr(read_mod, "account",
                        lambda s, ref: _account(status="ready"))
    status, _, body = _post(client, "/api/v1/accounts/ord_84213-a/ready", {})
    assert status == 409 and body["error"]["code"] == "invalid_state"
    assert body["error"]["state"] == "ready"


@pytest.mark.parametrize("web", [WRITE_ON], indirect=True)
def test_withdrawing_stamps_the_row_and_queues_the_sheet(web, monkeypatch):  # noqa: F811
    seen = _wrote(monkeypatch)
    _client(monkeypatch)
    monkeypatch.setattr(read_mod, "account", lambda s, ref: _account())
    client = web()
    status, _, body = _delete(client, "/api/v1/accounts/ord_84213-a")
    assert status == 200 and seen["withdrawn"] == "ord_84213-a"
    assert seen["queued"][0]["verb"] == "withdraw_panel_account"
    assert body["ref"] == "ord_84213-a"


@pytest.mark.parametrize("web", [WRITE_ON], indirect=True)
def test_it_is_too_late_to_take_back_a_phone_that_is_running(
        web, monkeypatch):  # noqa: F811
    """A phone is booked and billing against it. Taking the row out from
    under a running sign-in is the one thing this has to refuse."""
    seen = _wrote(monkeypatch)
    _client(monkeypatch)
    client = web()
    for word in ("in_use", "ready", "delivered"):
        monkeypatch.setattr(read_mod, "account",
                            lambda s, ref, w=word: _account(status=w))
        code, _, body = _delete(client, "/api/v1/accounts/ord_84213-a")
        assert code == 409, word
        assert body["error"]["code"] == "invalid_state"
    assert "withdrawn" not in seen


@pytest.mark.parametrize("web", [WRITE_ON], indirect=True)
def test_the_panel_may_not_change_a_row_the_sheet_owns(web, monkeypatch):  # noqa: F811
    """A farm_<id> ref names an account that came from the sheet. The panel
    may read those; it did not buy them and may not take them."""
    _wrote(monkeypatch)
    _client(monkeypatch)
    monkeypatch.setattr(read_mod, "account",
                        lambda s, ref: _account(panel_ref=None))
    client = web()
    code, _, body = _delete(client, "/api/v1/accounts/farm_41")
    assert code == 404 and body["error"]["code"] == "not_found"


@pytest.mark.parametrize("web", [WRITE_ON], indirect=True)
def test_a_body_that_is_not_an_object_or_is_enormous_is_refused(
        web, monkeypatch):  # noqa: F811
    _wrote(monkeypatch)
    _client(monkeypatch)
    client = web()
    head = {"Authorization": f"Bearer {KEY}"}
    for raw in ("{not json", json.dumps([1, 2, 3]),
                "x" * (api_mod.MAX_BODY + 1)):
        status, _, body = client.request("POST", "/api/v1/accounts", raw,
                                         headers=head)
        assert status == 422, raw[:20]
        assert json.loads(body)["error"]["field"] == "body"


# ------------------------------------------------- the pure parts, alone
def test_the_state_is_a_view_over_the_pools_own_word():
    """Every branch, because this mapping is the whole contract: a wrong
    word here is a customer told the wrong thing about their order."""
    say = read_mod.state_of
    assert say(_account(status="")) == "queued"
    assert say(_account(status="imported")) == "queued"
    assert say(_account(status="in_use")) == "signing_in"
    assert say(_account(status="ready")) == "ready"
    assert say(_account(status="delivered")) == "delivered"
    assert say(_account(status="payment_problem")) == "needs_human"
    assert say(_account(status="", error="not an email")) == "invalid"
    assert say(_account(status="ready", error="x")) == "invalid", \
        "a row validation refused is not stock, whatever its status says"
    # The word the code path will write, spoken before anything writes it.
    assert say(_account(status="needs_code")) == "needs_code"
    # Withdrawn is a column, not a status word: the mirror rewrites status
    # from the sheet every pass, so a word written there would not last.
    assert say(_account(withdrawn_at="2026-09-05T11:00:00+00:00")) == "withdrawn"
    assert say(_account(status="ready",
                        withdrawn_at="2026-09-05T11:00:00+00:00")) == "withdrawn"


def test_a_kind_the_farm_cannot_serve_yet_is_blocked_not_queued():
    """The panel sees the truth rather than a queue that never moves."""
    say = read_mod.state_of
    assert say(_account(credential_kind="google_backup_codes")) == "blocked"
    assert say(_account(product="claude",
                        credential_kind="email_code_customer")) == "blocked"
    assert read_mod.blocked_of(
        _account(credential_kind="email_code_auto")) == "kind_not_served_yet"
    assert read_mod.blocked_of(_account()) is None


def test_an_account_whose_code_comes_from_a_person_waits_for_them(
        monkeypatch):
    """Once that kind is served, it still does not go on a phone until the
    panel says the customer is at their keyboard - a phone billing while
    nobody types is the thing this state exists to prevent."""
    monkeypatch.setattr(read_mod, "SERVED",
                        {"chatgpt": ("password_totp",),
                         "claude": ("email_code_customer",)})
    row = _account(product="claude", credential_kind="email_code_customer")
    assert read_mod.state_of(row) == "waiting_customer"
    assert read_mod.state_of(dict(row, customer_ready=True)) == "queued"


def test_every_account_has_a_ref_including_the_ones_older_than_the_panel():
    assert read_mod.ref_of(_account()) == "ord_84213-a"
    assert read_mod.ref_of(_account(panel_ref=None)) == "farm_41"


def test_the_cursor_survives_a_round_trip_and_junk_falls_back():
    import datetime as dt

    row = {"id": 41, "updated_at": dt.datetime(2026, 9, 5, 10, 12, 40,
                                               tzinfo=dt.timezone.utc)}
    handle = read_mod._cursor(row)
    assert read_mod._decode(handle) == ("2026-09-05T10:12:40+00:00", 41)
    for junk in ("", "not-base64!!", "Y3Vyc29y", "!!!"):
        assert read_mod._decode(junk) is None or isinstance(
            read_mod._decode(junk), tuple)
    assert read_mod._decode("bm90LWEtcGFpcg==") is None, \
        "a cursor a client invented is the first page, not a traceback"


def test_stamps_leave_as_utc_whatever_they_arrived_as():
    """The console's clock is Tehran. A machine's is not."""
    import datetime as dt

    aware = dt.datetime(2026, 9, 5, 13, 42, tzinfo=dt.timezone(
        dt.timedelta(hours=3, minutes=30)))
    assert api_mod.rfc3339(aware) == "2026-09-05T10:12:00Z"
    naive = dt.datetime(2026, 9, 5, 10, 12)
    assert api_mod.rfc3339(naive) == "2026-09-05T10:12:00Z"
    assert api_mod.rfc3339(None) is None
    assert api_mod.rfc3339("") is None


def test_a_key_is_hashed_fast_and_never_stored():
    """SHA-256, not scrypt: 256 bits of minted entropy has no dictionary,
    and this runs on every request."""
    token, digest, prefix = api_mod.mint_key()
    assert len(token) > 32 and digest == api_mod.hash_key(token)
    assert prefix == token[:api_mod.PREFIX_LEN] and token != prefix
    assert api_mod.hash_key("a") != api_mod.hash_key("b")
