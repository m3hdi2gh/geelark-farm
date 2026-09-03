"""The web's buttons, carried out by the pass (C5b).

`paste` reads a seller's sheet copy without being told its column order;
each verb in `verbs` runs against a Book of fake tabs the way the drain
runs it, and says in a sentence what it did.
"""

from __future__ import annotations

import pytest

from geelark_farm import serve as serve_mod
from geelark_farm import verbs
from geelark_farm.web import paste
from tests.test_builder import SECRET, make_book


# ------------------------------------------------------------- the paste
def test_a_sheet_copy_is_read_whatever_its_column_order():
    rows = paste.accounts(
        "a@x.com\tKx82!mnQ\tnz3i craw hhs3 ezen 4kqq t2hx wqjm uss5\n"
        "them@hmD:72&93$#\tb@x.com\n"
        "c@x.com, pw3, rec@x.com\n"
        "\n"
        "d@x.com only")
    assert rows[0]["address"] == "a@x.com"
    assert rows[0]["password"] == "Kx82!mnQ"
    assert rows[0]["secret"] == "NZ3ICRAWHHS3EZEN4KQQT2HXWQJMUSS5"
    assert rows[1] == {"address": "b@x.com", "recovery": "",
                       "password": "them@hmD:72&93$#", "secret": "",
                       "line": "them@hmD:72&93$#\tb@x.com"}, \
        "a colon in a password is a colon, not a delimiter"
    assert rows[2]["recovery"] == "rec@x.com" and rows[2]["password"] == "pw3"
    assert rows[3]["address"] == "d@x.com" and rows[3]["password"] == "only"


def test_a_proxy_paste_finds_the_string_and_an_optional_name():
    rows = paste.proxies("SX43\t1.2.3.4:9999:u:p\n5.6.7.8:1080\n")
    assert rows[0] == {"raw": "1.2.3.4:9999:u:p", "name": "SX43",
                       "line": "SX43\t1.2.3.4:9999:u:p"}
    assert rows[1]["raw"] == "5.6.7.8:1080" and rows[1]["name"] == ""


# ------------------------------------------------------------- the verbs
def test_add_gmails_appends_the_new_and_skips_what_is_already_there():
    book = make_book(gmails=1)                  # g0@example.com exists
    status, said, detail = verbs.add_gmails(book, None, None, {
        "by": "mehdi", "seller": "usa",
        "rows": [{"address": "g0@example.com", "password": "pw",
                  "secret": SECRET, "recovery": ""},
                 {"address": "new@example.com", "password": "pw",
                  "secret": SECRET, "recovery": ""},
                 {"address": "nope", "password": "", "secret": "",
                  "recovery": ""}]}, None)

    assert status == "done"
    assert said == "1 gmail added, 1 already in the pool, 1 refused"
    added = book.gmails.find("new@example.com")
    assert added is not None and added.values["Seller"] == "usa"
    assert "Added from the web by mehdi" in added.values["Note"]
    assert added in book.gmails.available, "blank status: stock"
    assert detail["refused"][0].startswith("nope:")


def test_add_proxies_tests_each_and_mints_the_next_name(monkeypatch):
    book = make_book(proxies=2)
    for r in book.proxies._rows:
        r.values["Name"] = ""
    book.proxies._rows[0].values["Name"] = "SX41"

    def check(client, proxy):
        if proxy.host == "10.9.9.9":
            raise verbs.proxy_mod.ProxyError("no answer")
        return {"outboundIP": "8.8.8.8"}

    monkeypatch.setattr(verbs.proxy_mod, "check", check)
    status, said, detail = verbs.add_proxies(book, None, None, {
        "by": "alireza",
        "rows": [{"raw": "10.5.5.5:9999:u:p", "name": ""},
                 {"raw": "10.9.9.9:9999:u:p", "name": "SX99"},
                 {"raw": "10.0.0.0:9999:u:p", "name": ""}]}, object())

    assert status == "done" and detail["added"] == ["SX42", "SX99"]
    assert said == "2 proxies added, 1 already in the pool"
    live = book.proxies.find_by_name("SX42")
    assert book.proxies.status_of(live) == "free"
    assert live.values["Last Exit IP"] == "8.8.8.8"
    dead = book.proxies.find_by_name("SX99")
    assert book.proxies.status_of(dead) == "dead"


def test_offer_again_only_touches_a_row_a_run_set_aside():
    book = make_book(apps=2)
    a0, a1 = book.apps._rows
    book.apps.set_aside(a0, reason="payment_problem", note="fix it")

    status, said, detail = verbs.offer_again(
        book, None, None, {"address": "a0@example.com", "by": "mehdi"}, None)
    assert status == "done" and detail == {"was": "payment_problem"}
    assert a0 in book.apps.available
    assert "Offered again from the web by mehdi" in a0.values["Note"]

    status, said, _ = verbs.offer_again(
        book, None, None, {"address": "a1@example.com", "by": "mehdi"}, None)
    assert status == "refused" and "not set aside" in said


def test_remove_proxy_refuses_a_row_a_phone_is_behind_and_drops_a_free_one():
    book = make_book(proxies=2)
    busy, free = book.proxies._rows
    busy.values["Name"], free.values["Name"] = "SX1", "SX2"
    book.proxies.spend(busy, serial="1600", note="on it")

    status, said, _ = verbs.remove_proxy(book, None, None, {"name": "SX1"},
                                         None)
    assert status == "refused" and "a phone is behind it" in said

    status, said, _ = verbs.remove_proxy(book, None, None, {"name": "SX2"},
                                         None)
    assert status == "done" and "GeeLark still holds it" in said
    assert book.proxies.find_by_name("SX2") is None
    assert book.proxies._ws.deleted_rows == [free.sheet_row]


def test_every_web_verb_is_registered_with_the_drain():
    for name in ("add_gmails", "add_gpt", "add_proxies", "adopt_proxy",
                 "offer_again", "mark_proxy_free", "test_proxy",
                 "test_all_proxies", "remove_proxy", "ignore_proxy"):
        assert serve_mod.ACTION_VERBS[name] is verbs.VERBS[name]


class _Conn:
    """A store connection that remembers nothing and commits gladly."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def commit(self):
        pass


def _fake_state(monkeypatch, kept: dict) -> dict:
    """service_state faked: `get` answers from `kept`, `put` lands in the
    dict this returns."""
    import geelark_farm.store.db as db_mod
    import geelark_farm.store.state as state_mod

    written = {}
    monkeypatch.setattr(db_mod, "connect", lambda s: _Conn())
    monkeypatch.setattr(state_mod, "get",
                        lambda s, key, default=None: kept.get(key, default))
    monkeypatch.setattr(state_mod, "put",
                        lambda conn, key, value: written.update({key: value}))
    return written


def test_ignore_proxy_keeps_the_triple_in_service_state(monkeypatch,
                                                        make_settings):
    written = _fake_state(monkeypatch, {"ignored_proxies": ["9.9.9.9:1:x"]})
    triple = {"host": "1.2.3.4", "port": "9999", "username": "u"}

    status, said, _ = verbs.ignore_proxy(
        None, None, make_settings(store_enabled=True), triple, None)
    assert status == "done" and "1.2.3.4:9999:u" in said
    assert written == {"ignored_proxies": ["9.9.9.9:1:x", "1.2.3.4:9999:u"]}

    written.clear()
    status, said, _ = verbs.ignore_proxy(None, None, make_settings(), triple,
                                         None)
    assert status == "failed" and written == {}, "no store, nothing kept"


def test_a_test_stamps_when_the_exit_last_answered(monkeypatch,
                                                   make_settings):
    import time

    book = make_book(proxies=1)
    book.proxies._rows[0].values["Name"] = "SX1"
    monkeypatch.setattr(verbs.proxy_mod, "check",
                        lambda client, proxy: {"outboundIP": "8.8.8.8"})
    written = _fake_state(monkeypatch, {"proxy_tests": {
        "SX9": {"at": 1.0, "ok": False, "exit": ""}}})

    status, said, _ = verbs.test_proxy(
        book, None, make_settings(store_enabled=True), {"name": "SX1"},
        object())
    assert status == "done" and "exit 8.8.8.8" in said
    stamps = written["proxy_tests"]
    assert stamps["SX9"] == {"at": 1.0, "ok": False, "exit": ""}, \
        "the other names' stamps are kept"
    assert stamps["SX1"]["ok"] is True and stamps["SX1"]["exit"] == "8.8.8.8"
    assert time.time() - stamps["SX1"]["at"] < 5


def test_the_drain_hands_the_geelark_client_to_the_handler(monkeypatch,
                                                            make_settings):
    import geelark_farm.store.actions as actions_mod
    import geelark_farm.store.db as db_mod

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    seen = {}
    monkeypatch.setattr(db_mod, "connect", lambda s: Conn())
    monkeypatch.setattr(actions_mod, "take_batch",
                        lambda conn, *, controls_only: [
                            {"id": 1, "verb": "spy", "payload": {},
                             "requested_by": 7}])
    monkeypatch.setattr(actions_mod, "finish",
                        lambda conn, aid, *, status, result, detail=None: None)
    monkeypatch.setitem(
        serve_mod.ACTION_VERBS, "spy",
        lambda book, ledger, settings, payload, client:
        seen.update(client=client) or ("done", "", None))
    settings = make_settings(store_enabled=True, web_mutations=True)

    serve_mod._drain_actions(settings, None, None, controls_only=False,
                             client="the client")
    assert seen == {"client": "the client"}


# ------------------------------------------------- C6: the phone commands
def _warm(*serials):
    return [{"sheet_row": i + 2, "serial": s, "gmail": f"g{i}@example.com",
             "proxy": "", "app": "yes", "status": "incomplete",
             "phone_id": f"P{s}"} for i, s in enumerate(serials)]


def test_claim_this_takes_the_named_row_or_says_no():
    book = make_book(apps=2)
    a0, a1 = book.apps._rows

    assert book.apps.claim_this(a1, "1500") is True
    assert book.apps.status_of(a1) == "in_use"
    assert a1.values["Phone Serial"] == "1500"
    assert book.apps.claim_this(a1, "1501") is False, "not free any more"
    assert a0 in book.apps.available, "the next one down was not taken"


def test_login_selected_pairs_each_chosen_account_with_a_warm_phone(
        monkeypatch):
    from geelark_farm import builder

    book = make_book(apps=4)
    a0, a1, a2, a3 = book.apps._rows
    book.apps.spend(a2, serial="1400", note="already on a phone")
    monkeypatch.setattr(builder, "_unfinished",
                        lambda client, book_: (_warm("1500", "1501"), []))
    launched = []

    status, said, detail = verbs.login_accounts(
        book, None, None, {"by": "mehdi", "addresses": [
            "a0@example.com", "a1@example.com", "a2@example.com",
            "nobody@example.com", "a3@example.com"]},
        object(), launch=launched.append)

    assert status == "running"
    jobs = launched[0]
    assert [(j["kind"], j["phone"]["serial"], j["phone"]["account"].label)
            for j in jobs] == [("finish", "1500", "a0@example.com"),
                               ("finish", "1501", "a1@example.com")]
    assert book.apps.status_of(a0) == "in_use"
    assert a0.values["Phone Serial"] == "1500"
    assert book.apps.status_of(a3) == "", "no warm phone: left free"
    assert detail["unpaired"] == ["a3@example.com"]
    assert detail["refused"] == ["a2@example.com: ready",
                                 "nobody@example.com: not in the Gpt Info tab"]
    assert "logging in 2 account(s) in parallel" in said
    assert "no warm phone" in said


def test_login_selected_refuses_to_run_without_a_launcher(monkeypatch):
    book = make_book(apps=1)
    status, said, _ = verbs.login_accounts(
        book, None, None, {"addresses": ["a0@example.com"]}, object())
    assert status == "failed" and "cannot start" in said
    assert book.apps.status_of(book.apps._rows[0]) == "", "nothing claimed"
    assert serve_mod.ACTION_VERBS["login_accounts"].needs_launch is True


def _phone_on(book, serial, proxy_name):
    row = book.phones.start(Serial=serial, Gmail="g0@example.com",
                            Proxy=proxy_name, Status="ready")
    return row


def test_change_proxy_moves_the_phone_to_the_next_free_exit(monkeypatch):
    from geelark_farm import phones as phones_mod

    book = make_book(proxies=2)
    sx1, sx2 = book.proxies._rows
    sx1.values["Name"], sx2.values["Name"] = "SX1", "SX2"
    book.proxies.spend(sx1, serial="1500", note="on it")
    _phone_on(book, "1500", "SX1")
    monkeypatch.setattr(phones_mod, "listing", lambda client: [
        {"id": "P1500", "serialNo": "1500", "status": phones_mod.RUNNING}])
    done = []
    monkeypatch.setattr(phones_mod, "stop",
                        lambda client, pid: done.append(("stop", pid)))
    monkeypatch.setattr(phones_mod, "wait_until_stopped",
                        lambda client, pid, **k:
                        done.append(("wait", pid)) or True)
    monkeypatch.setattr(phones_mod, "set_proxy",
                        lambda client, pid, proxy:
                        done.append(("set", pid, proxy.host)))

    status, said, detail = verbs.change_proxy(
        book, None, None, {"serial": "1500", "by": "alireza"}, object())

    assert status == "done", said
    assert done == [("stop", "P1500"), ("wait", "P1500"),
                    ("set", "P1500", "10.0.0.1")], \
        "stopped first, then told GeeLark"
    assert book.proxies.status_of(sx2) == "on a phone"
    assert sx2.values["Used By"] == "1500"
    assert book.proxies.status_of(sx1) == "free"
    assert "Left phone 1500" in sx1.values["Note"]
    row = next(r for r in book.phones.rows() if r["Serial"] == "1500")
    assert row["Proxy"] == "SX2"
    assert detail == {"was": "SX1", "now": "SX2"}


def test_change_proxy_gives_the_exit_back_when_geelark_refuses(monkeypatch):
    from geelark_farm import phones as phones_mod

    book = make_book(proxies=2)
    sx1, sx2 = book.proxies._rows
    sx1.values["Name"], sx2.values["Name"] = "SX1", "SX2"
    book.proxies.spend(sx1, serial="1500", note="on it")
    _phone_on(book, "1500", "SX1")
    monkeypatch.setattr(phones_mod, "listing", lambda client: [
        {"id": "P1500", "serialNo": "1500", "status": phones_mod.STOPPED}])

    def refuse(client, pid, proxy):
        raise phones_mod.PhoneError("[45004] proxy check failed")

    monkeypatch.setattr(phones_mod, "set_proxy", refuse)

    status, said, _ = verbs.change_proxy(
        book, None, None, {"serial": "1500", "by": "alireza"}, object())

    assert status == "failed" and "45004" in said
    assert book.proxies.status_of(sx2) == "free", "given back"
    assert book.proxies.status_of(sx1) == "on a phone", "kept"
    row = next(r for r in book.phones.rows() if r["Serial"] == "1500")
    assert row["Proxy"] == "SX1"


def test_change_proxy_refuses_a_phone_a_run_is_working_on():
    book = make_book(proxies=2)
    book.phones.start(Serial="1500", Gmail="g0@example.com", Proxy="SX1",
                      Status=book.phones.BUILDING)

    status, said, _ = verbs.change_proxy(
        book, None, None, {"serial": "1500"}, object())

    assert status == "refused" and "worked on" in said
    assert len(book.proxies.available) == 2, "no exit was claimed"


# ------------------------------------------------- C7: stop this one
def test_login_selected_answers_running_with_a_line_per_phone(monkeypatch):
    from geelark_farm import builder

    book = make_book(apps=2)
    monkeypatch.setattr(builder, "_unfinished",
                        lambda client, book_: (_warm("1500", "1501"), []))

    status, said, detail = verbs.login_accounts(
        book, None, None, {"by": "mehdi", "addresses": [
            "a0@example.com", "a1@example.com"]},
        object(), launch=lambda jobs: None)

    assert status == "running", "the phones are booting; the launcher settles"
    assert detail["phones"] == [
        {"serial": "1500", "account": "a0@example.com", "status": "booting",
         "ok": None},
        {"serial": "1501", "account": "a1@example.com", "status": "booting",
         "ok": None}]


def test_stop_this_one_reaches_the_session_at_its_next_step():
    from geelark_farm import builder
    from geelark_farm.builder import Build

    builder.STOP_BY_HAND.clear()
    status, said, _ = verbs.stop_phone(None, None, None, {"serial": "1549"},
                                       None)
    assert status == "done" and "1549" in said
    assert "1549" in builder.STOP_BY_HAND

    session = object.__new__(builder._Session)
    session.cancelled = None
    session.build = Build(index=1, serial="1549")
    with pytest.raises(builder.Aborted, match="stopped_by_hand"):
        session.check_cancelled()
    assert "1549" not in builder.STOP_BY_HAND, "honoured once, then gone"

    other = object.__new__(builder._Session)
    other.cancelled = None
    other.build = Build(index=2, serial="1550")
    other.check_cancelled()                     # not named: carries on
    assert serve_mod.ACTION_VERBS["stop_phone"] is verbs.stop_phone


# --------------------------------------------------- C8: stock is an event
def test_stock_arriving_is_an_event_when_there_is_a_store(monkeypatch,
                                                          make_settings):
    import geelark_farm.store.events as events_mod

    emitted = []
    monkeypatch.setattr(events_mod, "emit",
                        lambda s, kind, **kw: emitted.append((kind, kw))
                        or True)
    book = make_book(gmails=0)
    rows = [{"address": "new@example.com", "password": "pw",
             "secret": SECRET, "recovery": ""}]

    verbs.add_gmails(book, None, make_settings(store_enabled=True),
                     {"by": "mehdi", "seller": "usa", "rows": rows}, None)
    assert emitted == [("stock", {"status": "gmail",
                                  "detail": "1 gmail added by mehdi"})]

    emitted.clear()
    verbs.add_gmails(make_book(gmails=0), None, make_settings(),
                     {"by": "mehdi", "rows": rows}, None)
    assert emitted == [], "no store, no connection attempt"


def test_an_account_set_aside_is_an_event_on_its_phone(monkeypatch):
    from geelark_farm import builder

    seen = []
    monkeypatch.setattr(builder, "_event_sink",
                        lambda kind, **kw: seen.append((kind, kw)))
    book = make_book(apps=1)
    account = book.apps._rows[0]
    made = builder.Build(index=2, serial="1533")

    builder._release(book, made, [(book.apps, account, builder.SET_ASIDE,
                                   "the note", "payment_problem")])

    assert book.apps.status_of(account) == "payment_problem"
    assert seen == [("account", {"run_id": "-", "build": "2",
                                 "serial": "1533", "status": "set_aside",
                                 "detail": "a0@example.com: payment_problem"})]


def test_offer_again_reaches_the_gmail_tab_when_the_kind_says_so():
    """The Needs attention page offers gmails again too; the kind in the
    payload picks the tab, and without one the app tab is searched."""
    book = make_book(gmails=1, apps=1)
    g0 = book.gmails._rows[0]
    book.gmails.fail(g0, "wrong_password", note="fix it")

    status, said, detail = verbs.offer_again(
        book, None, None, {"address": "g0@example.com", "kind": "gmail",
                           "by": "mehdi"}, None)
    assert status == "done" and detail == {"was": "wrong_password"}
    assert g0 in book.gmails.available

    status, said, _ = verbs.offer_again(
        book, None, None, {"address": "g0@example.com", "by": "mehdi"}, None)
    assert status == "failed" and "Gpt" in said, "no kind: the app tab"


def test_add_gpt_takes_several_rows_and_says_what_it_skipped():
    """The paste's confirm hands add_gpt a list, and each row is judged
    the way the by-hand one is: the known one skipped, the bad one
    refused with its reason, the rest appended as awaiting login."""
    book = make_book(apps=1)                    # a0@example.com exists
    status, said, detail = verbs.add_gpt(book, None, None, {
        "by": "mehdi",
        "rows": [{"address": "a0@example.com", "password": "pw",
                  "secret": SECRET, "email_code_only": False},
                 {"address": "new@example.com", "password": "pw",
                  "secret": SECRET, "email_code_only": False},
                 {"address": "nope", "password": "pw", "secret": "",
                  "email_code_only": False}]}, None)

    assert status == "done"
    assert said == "1 account added, 1 already in the pool, 1 refused"
    assert detail["added"] == ["new@example.com"]
    assert detail["skipped"] == ["a0@example.com"]
    assert detail["refused"][0].startswith("nope:")
    added = book.apps.find("new@example.com")
    assert added is not None and added.values["2FA Secret"] == SECRET
    assert "Added from the web by mehdi" in added.values["Note"]
    assert added in book.apps.available, "blank status: awaiting login"
