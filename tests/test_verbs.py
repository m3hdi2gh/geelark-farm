"""The web's buttons, carried out by the pass (C5b).

`paste` reads a seller's sheet copy without being told its column order;
each verb in `verbs` runs against a Book of fake tabs the way the drain
runs it, and says in a sentence what it did.
"""

from __future__ import annotations

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
                 "test_all_proxies", "remove_proxy"):
        assert serve_mod.ACTION_VERBS[name] is verbs.VERBS[name]


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
