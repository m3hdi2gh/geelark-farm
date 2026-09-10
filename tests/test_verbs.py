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
                       "unread": [],
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
                        lambda conn, *, controls_only, only=None: [
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


def test_editing_a_gmail_writes_the_cells_and_is_judged_first(monkeypatch):
    """The row editor is a hand in the spreadsheet, so it is judged the
    way a pasted row is: a secret that is neither a key nor an address
    never reaches the cell."""
    from tests.test_builder import SECRET

    book = make_book(gmails=1)
    g0 = book.gmails._rows[0]
    g0.values.update({"Address": "old@example.com", "Password": "old",
                      "Secret": SECRET, "Seller": "usa"})

    status, said, detail = verbs.edit_gmail(
        book, None, None, {"address": "g0@example.com",
                           "new_address": "new@example.com",
                           "password": "fresh", "secret": "back@example.com",
                           "seller": "egypt", "purchased": "2026-09-04",
                           "by": "mehdi"}, None)

    assert status == "done", said
    assert g0.values["Address"] == "new@example.com"
    assert g0.values["Password"] == "fresh"
    assert g0.values["Secret"] == "back@example.com"
    assert g0.values["Seller"] == "egypt"
    assert "Address" in detail["changed"] and "Password" in detail["changed"]

    # Garbage never reaches the cell: Credentials judges it first.
    status, said, _ = verbs.edit_gmail(
        book, None, None, {"address": "new@example.com",
                           "new_address": "new@example.com",
                           "password": "fresh", "secret": "nope!!",
                           "seller": "egypt", "by": "mehdi"}, None)
    assert status == "refused", said
    assert g0.values["Secret"] == "back@example.com", "the cell is untouched"

    # And what only the tab knows - this seller promises a recovery
    # address, so a key here reads back as broken - is written, refused
    # and put straight back.
    status, said, _ = verbs.edit_gmail(
        book, None, None, {"address": "new@example.com",
                           "new_address": "new@example.com",
                           "password": "fresh", "secret": SECRET,
                           "seller": "egypt", "by": "mehdi"}, None)
    assert status == "refused" and "Seller column" in said
    assert g0.values["Secret"] == "back@example.com", "rolled back"
    assert g0.credentials is not None, "and the row still reads"


def test_a_gmail_a_phone_is_behind_is_neither_edited_nor_removed():
    book = make_book(gmails=1)
    g0 = book.gmails._rows[0]
    book.gmails.claim()

    for verb in (verbs.edit_gmail, verbs.remove_gmail):
        status, said, _ = verb(book, None, None,
                               {"address": "g0@example.com",
                                "secret": "", "by": "mehdi"}, None)
        assert status == "refused" and "a phone is behind it" in said
    assert g0 in book.gmails._rows


def test_a_gpt_row_is_edited_and_judged_like_a_gmail_one():
    """The GPT pool had no editor and no remove, so the manager could draw
    neither door for it. Same shape as the Gmail pair, same rule."""
    from tests.test_builder import SECRET

    book = make_book(apps=1)
    a0 = book.apps._rows[0]
    address = a0.values["Address"]
    a0.values.update({"Password": "old", "2FA Secret": ""})

    status, said, detail = verbs.edit_app(
        book, None, None, {"address": address,
                           "new_address": "new@example.com",
                           "password": "fresh", "secret": SECRET,
                           "by": "mehdi"}, None)
    assert status == "done", said
    assert a0.values["Address"] == "new@example.com"
    assert a0.values["Password"] == "fresh"
    assert a0.values["2FA Secret"] == SECRET
    assert "Address" in detail["changed"]

    status, said, _ = verbs.edit_app(
        book, None, None, {"address": "new@example.com",
                           "new_address": "new@example.com",
                           "password": "fresh", "secret": "nope!!",
                           "by": "mehdi"}, None)
    assert status == "refused", said
    assert a0.values["2FA Secret"] == SECRET, "the cell is untouched"


def test_a_gpt_row_a_phone_is_behind_is_neither_edited_nor_removed():
    book = make_book(apps=1)
    a0 = book.apps._rows[0]
    address = a0.values["Address"]
    book.apps.claim()

    for verb in (verbs.edit_app, verbs.remove_app):
        status, said, _ = verb(book, None, None,
                               {"address": address, "secret": "",
                                "by": "mehdi"}, None)
        assert status == "refused" and "a phone is behind it" in said
    assert a0 in book.apps._rows


def test_removing_a_gpt_row_keeps_what_it_removed():
    book = make_book(apps=1)
    a0 = book.apps._rows[0]
    address = a0.values["Address"]
    a0.values["Password"] = "pw"

    status, said, detail = verbs.remove_app(
        book, None, None, {"address": address, "by": "mehdi"}, None)

    assert status == "done" and "removed from the pool" in said
    assert a0 not in book.apps._rows
    assert detail["removed"]["Address"] == address
    assert detail["removed"]["Password"] == "pw"


def test_removing_a_gmail_keeps_the_row_it_removed():
    book = make_book(gmails=1)
    g0 = book.gmails._rows[0]
    g0.values["Password"] = "pw"

    status, said, detail = verbs.remove_gmail(
        book, None, None, {"address": "g0@example.com", "by": "mehdi"}, None)

    assert status == "done" and "removed from the pool" in said
    assert g0 not in book.gmails._rows
    assert detail["removed"]["Address"] == "g0@example.com"
    assert detail["removed"]["Password"] == "pw"


# ------------------------------------------- the panel's two verbs (C9)
# These run unattended against the live Gpt Info tab the first time a
# panel POSTs an account, so they are tested against the real AppPool over
# a fake worksheet - the same bargain every other verb here strikes.

def _in_the_store(monkeypatch, **more):
    """The row api_v1_write already wrote. The store edge is faked; the
    pool underneath it is the real one."""
    row = {"address": "panel1@example.com", "password": "pw-panel",
           "secret": SECRET, "email_code_only": False}
    row.update(more)
    monkeypatch.setattr(verbs, "_panel_row", lambda settings, ref: row)
    return row


def test_a_panel_account_goes_from_the_store_into_the_tab(monkeypatch):
    """The half only a pass may do. The API already wrote the row - so a
    GET straight after the POST finds it - and this is what makes the
    keeper able to see it at all."""
    book = make_book(apps=1)
    _in_the_store(monkeypatch)

    status, said, detail = verbs.add_panel_account(
        book, None, None, {"ref": "ord_1", "by": "panel"}, None)

    assert status == "done", said
    assert detail == {"ref": "ord_1", "address": "panel1@example.com"}
    added = book.apps.find("panel1@example.com")
    assert added is not None, "it reached the tab"
    assert added.values["Password"] == "pw-panel"
    assert added.values["2FA Secret"] == SECRET
    assert added.values["Status"] == "", "free stock, for the keeper to claim"
    assert "ord_1" in added.values["Note"], "the note names the order"
    assert added in book.apps.available


def test_a_panel_account_already_in_the_tab_is_done_not_failed(monkeypatch):
    """A request can be drained twice - a retry, a restart mid-pass. The
    second run must not append a duplicate, and must not read as a
    failure either: the account is where it was asked to be."""
    book = make_book(apps=1)
    _in_the_store(monkeypatch, address="a0@example.com")

    status, said, _ = verbs.add_panel_account(
        book, None, None, {"ref": "ord_1", "by": "panel"}, None)

    assert status == "done" and "already in" in said
    assert len(book.apps._rows) == 1, "no duplicate row"


def test_a_panel_account_the_store_never_had_is_failed(monkeypatch):
    monkeypatch.setattr(verbs, "_panel_row", lambda settings, ref: None)
    book = make_book(apps=1)

    status, said, detail = verbs.add_panel_account(
        book, None, None, {"ref": "ord_missing", "by": "panel"}, None)

    assert status == "failed" and "not a row in the store" in said
    assert detail is None
    assert len(book.apps._rows) == 1, "nothing was appended"


def test_an_account_the_tab_would_not_take_says_so_on_the_row(monkeypatch):
    """The panel reads accounts, not requests. A failure only the request
    knows about leaves the account reading `queued` forever, with nothing
    ever going to claim it."""
    book = make_book(apps=1)
    _in_the_store(monkeypatch)
    broke = {}
    monkeypatch.setattr(verbs, "_panel_broke",
                        lambda settings, ref, why: broke.update(ref=ref,
                                                                why=why))

    def refuse(**fields):
        raise RuntimeError("the tab is full")

    monkeypatch.setattr(book.apps, "append", refuse)
    status, said, _ = verbs.add_panel_account(
        book, None, None, {"ref": "ord_1", "by": "panel"}, None)

    assert status == "failed" and "the tab is full" in said
    assert broke["ref"] == "ord_1" and "the tab is full" in broke["why"]


def test_withdrawing_takes_the_row_out_of_the_tab(monkeypatch):
    book = make_book(apps=2)
    _in_the_store(monkeypatch, address="a0@example.com")

    status, said, detail = verbs.withdraw_panel_account(
        book, None, None, {"ref": "ord_1", "by": "panel"}, None)

    assert status == "done" and "taken out" in said
    assert detail == {"ref": "ord_1"}
    assert book.apps.find("a0@example.com") is None
    assert len(book.apps._rows) == 1, "and only that one"


def test_withdrawing_refuses_a_row_a_phone_is_behind(monkeypatch):
    """The one thing this has to refuse: a phone is booked and billing
    against the account, and taking its row out from under a running
    sign-in is worse than telling the panel it is too late."""
    book = make_book(apps=2)
    a0 = book.apps._rows[0]
    book.apps.claim_this(a0, "1500")
    _in_the_store(monkeypatch, address="a0@example.com")

    status, said, _ = verbs.withdraw_panel_account(
        book, None, None, {"ref": "ord_1", "by": "panel"}, None)

    assert status == "refused" and "a phone is behind it" in said
    assert book.apps.find("a0@example.com") is not None, "still there"


def test_withdrawing_one_that_never_reached_the_tab_is_done(monkeypatch):
    """Withdrawn between the POST and the pass that would have added it.
    Nothing to take out is the asked-for state, not a failure."""
    book = make_book(apps=1)
    _in_the_store(monkeypatch, address="never-arrived@example.com")

    status, said, _ = verbs.withdraw_panel_account(
        book, None, None, {"ref": "ord_1", "by": "panel"}, None)

    assert status == "done" and "nothing to take out" in said


def test_both_panel_verbs_are_reachable_from_the_drain():
    """Registered, and the same object the drain would run - the pin the
    other verbs already carry."""
    import geelark_farm.serve as serve_mod

    for name in ("add_panel_account", "withdraw_panel_account"):
        assert serve_mod.ACTION_VERBS[name] is verbs.VERBS[name]


def test_boot_starts_the_phone_takes_it_and_keeps_the_live_link(monkeypatch):
    """One press does both halves: GeeLark is asked to start, which is the
    only thing that produces a live-view URL, and the phone is written
    taken - somebody watching a screen is holding it."""
    from geelark_farm import phones as phones_mod

    book = make_book(proxies=1)
    _phone_on(book, "1500", "SX1")
    monkeypatch.setattr(phones_mod, "listing", lambda client: [
        {"id": "P1500", "serialNo": "1500", "status": phones_mod.STOPPED}])
    started = []
    monkeypatch.setattr(phones_mod, "start", lambda client, pid, **k:
                        started.append(pid) or "https://phone.geelark.com/i?t=1")

    status, said, detail = verbs.boot_phone(
        book, None, None, {"serial": "1500", "by": "mehdi"}, object())

    assert status == "done", said
    assert started == ["P1500"]
    assert detail == {"state": "taken",
                      "url": "https://phone.geelark.com/i?t=1"}
    row = next(r for r in book.phones.rows() if r["Serial"] == "1500")
    assert row["State"] == "taken", "started means taken"


def test_boot_says_so_when_geelark_will_not_start_the_phone(monkeypatch):
    from geelark_farm import phones as phones_mod

    book = make_book(proxies=1)
    _phone_on(book, "1500", "SX1")
    monkeypatch.setattr(phones_mod, "listing", lambda client: [
        {"id": "P1500", "serialNo": "1500", "status": phones_mod.STOPPED}])

    def refuse(client, pid, **k):
        raise phones_mod.PhoneError("[43043] no capacity")

    monkeypatch.setattr(phones_mod, "start", refuse)

    status, said, detail = verbs.boot_phone(
        book, None, None, {"serial": "1500", "by": "mehdi"}, object())

    assert status == "failed" and "no capacity" in said
    assert detail is None
    row = next(r for r in book.phones.rows() if r["Serial"] == "1500")
    assert row["State"] != "taken", "a phone that never started is not taken"

    # A capacity refusal is not retried here. The builder waits four times
    # over half a minute for a machine; inside the drain that is a whole
    # pass spent asleep, and the person is watching a tab they can press
    # again.
    asked = {}

    def busy(client, pid, **k):
        asked.update(k)
        raise phones_mod.PhoneCapacityError("[43043] no capacity")

    monkeypatch.setattr(phones_mod, "start", busy)
    status, said, _ = verbs.boot_phone(
        book, None, None, {"serial": "1500", "by": "mehdi"}, object())
    assert asked == {"attempts": 1}
    assert status == "failed" and "press Boot again" in said


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


# ------------------------------- what may answer inside a web request (C3)
def test_a_verb_that_drives_a_phone_never_runs_in_a_web_request():
    """`client` is GeeLark: booting a phone, testing an exit, deleting a
    profile - seconds to minutes of somebody else's network. A person
    waiting on a form must not be holding it."""
    from geelark_farm import verbs

    for verb in ("boot_phone", "change_proxy", "login_accounts",
                 "test_proxy", "test_all_proxies", "adopt_proxy"):
        assert not verbs.runs_inline(verb), verb


def test_a_verb_that_writes_the_workbook_never_runs_in_a_web_request():
    """Opening the workbook is about six seconds against Google - fine
    once a pass, absurd on every click. `control` is the one that proves
    the rule has to read more than `book.<name>`: it reaches the Service
    board through `getattr`, which no attribute scan would ever see."""
    from geelark_farm import verbs

    for verb in ("control",):
        assert not verbs.runs_inline(verb), verb


def test_the_stock_verbs_answer_in_the_request_that_asked():
    """The point of the whole change: adding accounts, judging one that
    stopped, asking for a phone by hand - none of these needs a pass any
    more, and none of them should make somebody wait for one."""
    from geelark_farm import verbs

    for verb in ("add_gmails", "add_gpt", "offer_again", "edit_gmail",
                 "remove_gmail", "edit_app", "remove_app", "build_by_hand",
                 # These two joined when the person channel left the tab:
                 # Take, Done, Failed and Release are the buttons an
                 # operator presses all day, and they were the last ones
                 # still waiting on a pass (C3).
                 "set_phone_state", "clear_tries"):
        assert verbs.runs_inline(verb), verb


def test_an_unknown_verb_is_never_run_inline():
    """Closed by default: the fallback is the queue, which is where
    everything was before this existed."""
    from geelark_farm import verbs

    assert not verbs.runs_inline("no_such_verb")


def test_the_editor_can_free_a_row_or_set_it_aside():
    """The status field in the row editor: free is release - back on the
    shelf, serial cleared; set aside is a word in the pool's own column."""
    book = make_book(gmails=1)
    g0 = book.gmails._rows[0]
    address = g0.values["Address"]

    status, said, detail = verbs.edit_gmail(
        book, None, None, {"address": address, "new_address": address,
                           "password": g0.values.get("Password", "pw") or "pw",
                           "secret": "", "seller": g0.values.get("Seller", ""),
                           "state": "set aside", "by": "mehdi"}, None)
    assert status == "done", said
    assert book.gmails.status_of(g0) == "set_aside"
    assert "Status" in detail["changed"]

    status, said, detail = verbs.edit_gmail(
        book, None, None, {"address": address, "new_address": address,
                           "password": g0.values.get("Password", "pw") or "pw",
                           "secret": "", "seller": g0.values.get("Seller", ""),
                           "state": "free", "by": "mehdi"}, None)
    assert status == "done", said
    assert book.gmails.status_of(g0) == ""

    # The word it already has is not a change.
    status, said, detail = verbs.edit_gmail(
        book, None, None, {"address": address, "new_address": address,
                           "password": g0.values.get("Password", "pw") or "pw",
                           "secret": "", "seller": g0.values.get("Seller", ""),
                           "state": "free", "by": "mehdi"}, None)
    assert "Status" not in detail["changed"]


def test_a_named_phone_is_the_only_one_offered_to_the_account(monkeypatch):
    """The chooser names the phone. Named, it is the one used; a name that
    is not a warm phone is a refusal in words, not the next one in line."""
    from geelark_farm import builder

    book = make_book(apps=1)
    a0 = book.apps._rows[0]
    warm = [{"serial": "1500"}, {"serial": "1501"}]
    monkeypatch.setattr(builder, "_unfinished", lambda c, b: (list(warm), []))
    launched = []

    status, said, detail = verbs.login_accounts(
        book, None, None, {"addresses": [a0.values["Address"]],
                           "serial": "1501", "by": "mehdi"},
        client=object(), launch=launched.append)
    assert status == "running", said
    assert detail["phones"][0]["serial"] == "1501"

    status, said, _ = verbs.login_accounts(
        book, None, None, {"addresses": [a0.values["Address"]],
                           "serial": "1999", "by": "mehdi"},
        client=object(), launch=launched.append)
    assert status == "refused" and "1999" in said


def test_free_puts_a_set_aside_row_back_as_it_is():
    """One press where the editor was three, and nothing else on the row
    is touched - the editor rewrites every cell it shows."""
    book = make_book(gmails=1)
    g0 = book.gmails._rows[0]
    address = g0.values["Address"]
    book.gmails.set_aside(g0, reason="captcha_shown", note="Google challenged")
    g0.values["Status"] = "captcha_shown"
    password = g0.values.get("Password")

    status, said, _ = verbs.free_gmail(
        book, None, None, {"address": address, "by": "mehdi"}, None)
    assert status == "done" and "back on the shelf" in said
    assert book.gmails.status_of(g0) == ""
    assert g0.values.get("Password") == password

    status, said, _ = verbs.free_gmail(
        book, None, None, {"address": address, "by": "mehdi"}, None)
    assert status == "done" and "already free" in said


def test_free_refuses_a_row_a_phone_is_behind():
    book = make_book(apps=1)
    a0 = book.apps._rows[0]
    address = a0.values["Address"]
    book.apps.claim()
    status, said, _ = verbs.free_app(
        book, None, None, {"address": address, "by": "mehdi"}, None)
    assert status == "refused" and "a phone is behind it" in said


def test_a_paste_can_say_when_the_stock_was_bought():
    """`purchased_on` is the column the "how old is this stock" question is
    answered from, and the add stamped today whatever the person meant - so
    a batch bought last month entered as bought today, an answer nobody
    could correct except by editing every row. The sheet carried the date;
    the console had nowhere to type it (2026-09-06)."""
    book = make_book()
    verbs.add_gmails(book, None, None, {
        "by": "mehdi", "seller": "usa", "purchased": "2026-08-01",
        "rows": [{"address": "old@x.com", "password": "pw",
                  "secret": SECRET, "recovery": ""}]}, None)
    row = book.gmails.find("old@x.com")
    assert row is not None
    assert row.values["Purchase Date"] == "2026-08-01"


def test_a_paste_that_says_nothing_still_means_today():
    book = make_book()
    verbs.add_gmails(book, None, None, {
        "by": "mehdi", "seller": "usa",
        "rows": [{"address": "new@x.com", "password": "pw",
                  "secret": SECRET, "recovery": ""}]}, None)
    row = book.gmails.find("new@x.com")
    assert row is not None and row.values["Purchase Date"] == verbs._stamp()


def test_leaving_the_gmail_box_empty_asks_for_the_next_free_one():
    """The box says "auto" and the hint says the next one in the pool is
    used, and `builder.build_one` does exactly that when the wish names no
    Gmail. The verb refused it outright, so the dashboard's own main
    button did nothing at all - under a green tick reading "Done - it is
    already in" (the operator, 2026-09-07)."""
    book = make_book(gmails=1)
    asked = {}
    from unittest.mock import patch

    import geelark_farm.store.wanted as wanted_mod

    with patch.object(wanted_mod, "ask",
                      lambda s, **k: asked.update(k) or 5):
        status, said, detail = verbs.build_by_hand(
            book, None, None, {"by": "mehdi", "install_app": False}, None)

    assert status == "done", said
    assert asked["gmail"] == "", "the pass claims the next free row"
    assert "the next free Gmail" in said


def test_a_gmail_that_is_not_free_is_refused_at_the_press():
    """It checked only that the address existed, so a spent one was
    accepted here and refused half an hour later by the pass, where
    nobody was looking."""
    book = make_book(gmails=1)
    row = book.gmails._rows[0]
    address = row.values["Address"]
    book.gmails.claim()                       # now on a phone

    status, said, _ = verbs.build_by_hand(
        book, None, None,
        {"by": "mehdi", "gmail": address, "install_app": False}, None)

    assert status == "refused"
    assert address in said and "not free" in said


def test_every_inline_verb_answers_with_three_parts():
    """`runs_inline` means the web unpacks the answer in the request that
    asked for it. `build_by_hand` answered with two parts, so the press
    landed as "Something broke" - a blank error page on the dashboard's
    own form - and left the row unsettled, so the pass ran it again and
    asked for a second phone (2026-09-07)."""
    import ast
    import inspect

    wrong = []
    for name, fn in sorted(verbs.VERBS.items()):
        if not verbs.runs_inline(name):
            continue
        body = ast.parse(inspect.getsource(fn))
        top = next(n for n in ast.walk(body) if isinstance(n, ast.FunctionDef))
        for node in ast.walk(top):
            # Only this verb's own returns: a nested helper answers in its
            # own shape and is unpacked by the code that calls it.
            if not isinstance(node, ast.Return) or not node.value:
                continue
            if any(node in ast.walk(inner) for inner in ast.walk(top)
                   if isinstance(inner, ast.FunctionDef) and inner is not top):
                continue
            # Only a tuple written out here can be judged. `return
            # refused` and `return _summary(...)` hand back whatever they
            # were given, and what they were given is checked where it is
            # built - by the tests for those helpers.
            if (isinstance(node.value, ast.Tuple)
                    and len(node.value.elts) != 3):
                wrong.append(f"{name}:{node.lineno}")
    assert not wrong, ("these answer with something other than "
                       f"(status, said, detail): {wrong}")


def test_login_selected_marks_the_phone_building_before_the_job_starts(
        monkeypatch):
    """The pass counts the warm phones right after the drain, and a phone
    with an account on the way is not warm. Marked by the job a few
    seconds in, it was still counted, and the replacement waited a whole
    interval (2026-09-08)."""
    from geelark_farm import builder

    book = make_book(apps=1)
    _phone_on(book, "1500", "")
    monkeypatch.setattr(builder, "_unfinished",
                        lambda client, book_: (_warm("1500"), []))
    seen_at_launch = []

    def launch(jobs):
        seen_at_launch.extend(
            r["Status"] for r in book.phones.rows() if r["Serial"] == "1500")

    status, _, _ = verbs.login_accounts(
        book, None, None, {"by": "mehdi", "addresses": ["a0@example.com"]},
        object(), launch=launch)

    assert status == "running"
    assert seen_at_launch == [book.phones.BUILDING], "before the job, not by it"


def test_release_powers_the_phone_off_unless_a_run_holds_it(monkeypatch):
    """Release left the phone running, billing, until somebody noticed it
    under Running (the operator, 2026-09-08)."""
    from geelark_farm import phones as phones_mod

    stopped = []
    monkeypatch.setattr(phones_mod, "listing", lambda client, **k: [
        {"serialNo": "1862", "id": "P1", "status": phones_mod.RUNNING},
        {"serialNo": "1863", "id": "P2", "status": phones_mod.STOPPED}])
    monkeypatch.setattr(phones_mod, "stop",
                        lambda client, phone_id: stopped.append(phone_id))

    class Held:
        is_claimed, is_stale, label = True, False, "finish 1862"

    class Ledger:
        def __init__(self, held=None):
            self.held = held

        def get(self, phone_id):
            return self.held

    status, said, detail = verbs.power_off_phone(
        None, Ledger(), None, {"serial": "1862"}, object())
    assert status == "done" and stopped == ["P1"] and detail == {"off": True}
    assert "stops billing" in said

    status, said, _ = verbs.power_off_phone(
        None, Ledger(), None, {"serial": "1863"}, object())
    assert status == "done" and "already off" in said and stopped == ["P1"]

    status, said, _ = verbs.power_off_phone(
        None, Ledger(Held()), None, {"serial": "1862"}, object())
    assert status == "refused" and "held by a run" in said

    status, _, _ = verbs.power_off_phone(
        None, Ledger(), None, {"serial": "1899"}, object())
    assert status == "failed"
    assert verbs.power_off_phone.lane_safe is True


# ------------------------------------------ the build card (2026-09-10)
def test_a_bare_phone_is_asked_for_with_nothing_else():
    """No Gmail means no app and no account, whatever else the payload
    carried - the boxes are off on the page, and ignored here."""
    book = make_book(gmails=1)
    asked = {}
    from unittest.mock import patch

    import geelark_farm.store.wanted as wanted_mod

    with patch.object(wanted_mod, "ask",
                      lambda s, **k: asked.update(k) or 7):
        status, said, _ = verbs.build_by_hand(
            book, None, None,
            {"by": "mehdi", "no_gmail": True, "gmail": "g0@example.com",
             "app": "chatgpt", "app_account": "a0@example.com"}, None)

    assert status == "done", said
    assert asked["no_gmail"] is True
    assert (asked["gmail"], asked["app"], asked["app_account"]) == ("", "", "")
    assert asked["install_app"] is False
    assert "bare phone" in said and "request 7" in said


def test_claude_is_an_app_the_card_may_ask_for():
    book = make_book(gmails=1)
    asked = {}
    from unittest.mock import patch

    import geelark_farm.store.wanted as wanted_mod

    with patch.object(wanted_mod, "ask",
                      lambda s, **k: asked.update(k) or 8):
        status, said, _ = verbs.build_by_hand(
            book, None, None,
            {"by": "mehdi", "app": "claude", "app_account": "a0@example.com"},
            None)

    assert status == "done", said
    assert asked["app"] == "claude" and asked["no_gmail"] is False
    assert asked["app_account"] == "", "an account is ChatGPT's only"
    assert "with Claude" in said


def test_stop_this_one_is_written_where_the_builders_read_it(
        make_settings, tmp_path, monkeypatch):
    """The set in this process is heard by nobody in the builder container
    (the operator, 2026-09-10). The store's copy is - and a store that
    cannot take the press says so rather than answering done."""
    from geelark_farm import builder as builder_mod
    from geelark_farm.store import stops as store_stops

    settings = make_settings(state_dir=tmp_path, store_enabled=True)
    written = []
    monkeypatch.setattr(store_stops, "ask",
                        lambda s, serial: written.append(serial))
    try:
        status, said, _ = verbs.stop_phone(None, None, settings,
                                           {"serial": "2241"}, None)
        assert status == "done" and "2241" in said
        assert written == ["2241"]
        assert "2241" in builder_mod.STOP_BY_HAND
    finally:
        builder_mod.STOP_BY_HAND.discard("2241")

    monkeypatch.setattr(store_stops, "ask",
                        lambda s, serial: (_ for _ in ()).throw(RuntimeError("down")))
    try:
        status, said, _ = verbs.stop_phone(None, None, settings,
                                           {"serial": "2243"}, None)
        assert status == "failed" and "could not be written" in said
    finally:
        builder_mod.STOP_BY_HAND.discard("2243")

    off = make_settings(state_dir=tmp_path, store_enabled=False)
    written.clear()
    try:
        assert verbs.stop_phone(None, None, off, {"serial": "9"}, None)[0] == "done"
        assert written == [], "no store, no store write"
    finally:
        builder_mod.STOP_BY_HAND.discard("9")

