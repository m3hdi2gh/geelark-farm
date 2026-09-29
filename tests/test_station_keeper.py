"""The Station's keeper verbs: give back, call off, Boot, Change IP, Power
off and the verdicts, the power lock, the warm-phone reservation and the
forgotten sweep's Station branch (2026-09-29).

The verbs are driven against a Book of fake tabs with every store call of
the Station faked, so each refusal is read in its own words. The tests at
the bottom (`needs_cluster`) run the statements this package wrote -
`_stamp_owner`, the legacy sweep's reads and writes - and the give-back
and verdict paths end to end against a real Postgres (`GEELARK_TEST_DSN`).
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import pathlib
import threading
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from geelark_farm import forgotten, keeper, verbs
from geelark_farm import phones as phones_mod
from geelark_farm.store import events as store_events
from geelark_farm.store import station as store_station
from geelark_farm.store import verdicts
from geelark_farm.store import wanted as store_wanted
from tests.test_builder import FakeClaim, FakeLedger, make_book
from tests.test_pools import PHONE_HEADERS, PROXY_HEADERS

#: A Phones tab with the lane and the exit's address, and an exit tab
#: with the lane.
PHONE_HEADERS_LANED = PHONE_HEADERS + ["Purpose", "Exit IP"]
PROXY_HEADERS_LANED = PROXY_HEADERS + ["Purpose"]

UID = 4
STATION = {"by": "sara", "by_id": UID, "station": True}


# ------------------------------------------------------------- helpers
def _book(*, proxies=3, lanes=None, phone="1500", on="SX0", purpose=""):
    """A book whose exits are SX0.. with the given lanes, and one phone on
    `on` (which is spent on it)."""
    book = make_book(proxies=proxies, proxy_headers=PROXY_HEADERS_LANED,
                     phone_headers=PHONE_HEADERS_LANED)
    for i, r in enumerate(book.proxies._rows):
        r.values["Name"] = f"SX{i}"
        lane = (lanes or {}).get(f"SX{i}", "")
        if lane:
            book.proxies.keep_for(r, lane)
    if on:
        book.proxies.spend(book.proxies.find_by_name(on), serial=phone,
                           note="on it")
    if phone:
        book.phones.start(Serial=phone, Gmail="g0@example.com", Proxy=on or "",
                          Status="ready", Purpose=purpose)
    return book


def _row(book, serial="1500"):
    return next(r for r in book.phones.rows() if r["Serial"] == serial)


class Vendor:
    """The cloud's phone calls, recorded in order - with the power lock's
    entry and exit when a test watches the lock too."""

    def __init__(self, monkeypatch, status=phones_mod.STOPPED, url="https://v/1"):
        self.calls: list[tuple] = []
        self.status = status
        self.url = url
        monkeypatch.setattr(phones_mod, "listing", self.listing)
        monkeypatch.setattr(phones_mod, "start", self.start)
        monkeypatch.setattr(phones_mod, "stop", self.stop)
        monkeypatch.setattr(phones_mod, "wait_until_stopped",
                            lambda client, pid, **k:
                            self.calls.append(("wait", pid)) or True)
        monkeypatch.setattr(phones_mod, "set_proxy",
                            lambda client, pid, proxy:
                            self.calls.append(("set", pid, proxy.host)))
        monkeypatch.setattr(verbs.proxy_mod, "check",
                            lambda client, proxy: {"outboundIP": "8.8.8.8"})

    def listing(self, client, **k):
        return [{"id": "P1500", "serialNo": "1500", "status": self.status}]

    def start(self, client, pid, **k):
        self.calls.append(("start", pid))
        return self.url

    def stop(self, client, pid):
        self.calls.append(("stop", pid))

    def names(self):
        return [c[0] for c in self.calls]


class Station:
    """Every store call of the Station a verb makes, faked and recorded."""

    def __init__(self, monkeypatch, **answers):
        self.said: list[tuple] = []
        self.answers = {"holds": True, "station_holder": None,
                        "stored_link": "", "booted": True, "powered_off": True,
                        "exit_shared": False, "is_watched": False,
                        "reserve_warm": "incomplete", "unreserve_warm": True,
                        "give_back": None, "hold_state": None,
                        "expire_power": 0, "power_pending_of": None}
        self.answers.update(answers)
        for name in self.answers:
            monkeypatch.setattr(store_station, name, self._fake(name))

    def _fake(self, name):
        def fake(settings, *args, **kwargs):
            self.said.append((name, args, kwargs))
            answer = self.answers[name]
            if isinstance(answer, Exception):
                raise answer
            return answer(*args, **kwargs) if callable(answer) else answer
        return fake

    def asked(self, name):
        return [(a, k) for n, a, k in self.said if n == name]


@pytest.fixture
def on(make_settings):
    """Settings with the store on (every store call is faked)."""
    return make_settings(store_enabled=True, release_after_minutes=60,
                         live_tab_grace_seconds=45)


# ------------------------------------------------------------- give back
def test_give_back_answers_in_its_words_and_says_why_not(monkeypatch, on):
    fake = Station(monkeypatch, give_back={"id": 1, "lane": "spotify",
                                           "running": True, "off_id": 77})
    status, said, detail = verbs.give_back(
        None, None, on, {"serial": "1500", "by": "sara", "by_id": UID,
                         "where": "station"}, None)
    assert (status, said) == ("done", "Phone 1500 is back on the Spotify shelf.")
    assert detail == {"serial": "1500", "lane": "spotify", "off": 77}
    assert fake.asked("give_back") == [
        ((), {"serial": "1500", "owner_id": UID, "by": "sara"})]

    fake.answers["give_back"] = {"id": 1, "lane": "other", "running": False,
                                 "off_id": None}
    assert verbs.give_back(None, None, on, {"serial": "1500", "by_id": UID},
                           None)[1] == "Phone 1500 is back on the farm."

    fake.answers["give_back"] = None
    for st, words in (
            (None, "phone 1500 is not on the farm any more"),
            ({"state": "failed", "busy": ""}, "phone 1500 is not on the farm any more"),
            ({"state": "taken", "busy": "change_proxy"},
             "phone 1500 is changing its IP - wait for it"),
            ({"state": "taken", "busy": "boot_phone"},
             "phone 1500 is booting - wait for it"),
            ({"state": "taken", "busy": "", "owner_id": 9},
             "phone 1500 is not yours any more")):
        fake.answers["hold_state"] = st
        assert verbs.give_back(None, None, on, {"serial": "1500", "by_id": UID},
                               None) == ("refused", words, None)

    assert verbs.give_back(None, None, on, {}, None)[0] == "refused"
    assert verbs.give_back(None, None, None, {"serial": "1500"}, None) == (
        "failed", "no store to give it back to", None)
    fake.answers["give_back"] = RuntimeError("store down")
    with pytest.raises(RuntimeError):
        verbs.give_back(None, None, on, {"serial": "1500", "by_id": UID}, None)


def test_give_back_and_call_off_run_in_the_request_and_on_the_lane():
    from geelark_farm import serve as serve_mod

    for name in ("give_back", "call_off_build"):
        assert verbs.runs_inline(name), name
        assert serve_mod.ACTION_VERBS[name] is verbs.VERBS[name]
    assert verbs.give_back.lane_safe is True
    assert verbs.call_off_build.lane_safe is True


# ------------------------------------------------------------- call off
def _call_off(monkeypatch, answer, *, named=False):
    asked = []
    monkeypatch.setattr(store_wanted, "call_off",
                        lambda s, wid, **k: asked.append((wid, k)) or answer)
    monkeypatch.setattr(store_wanted, "others_name_exit",
                        lambda s, name, wid: named)
    return asked


def test_calling_off_a_running_build_asks_its_serial_to_stop_only_while_it_builds(
        monkeypatch, on):
    from geelark_farm.store import stops as store_stops

    stops = []
    monkeypatch.setattr(store_stops, "ask", lambda s, serial: stops.append(serial))
    fake = Station(monkeypatch, hold_state={"status": "building", "state": ""})
    asked = _call_off(monkeypatch, {"stage": "running", "serial": "1500",
                                    "proxy_name": "", "status": "running",
                                    "id": 12})
    status, said, detail = verbs.call_off_build(
        None, None, on, {"wanted_id": 12, "name": "wish 12", "admin": False,
                         "by": "sara", "by_id": UID}, None)
    assert status == "done" and detail == {"wanted_id": 12, "stage": "running"}
    assert said.startswith("The build was called off. It stops at its next step")
    assert stops == ["1500"]
    assert asked == [(12, {"by_id": UID, "by": "sara", "admin": False})]

    # Landed meanwhile: no stop, which would end the next job on the phone.
    fake.answers["hold_state"] = {"status": "ready", "state": "taken"}
    assert verbs.call_off_build(None, None, on, {"wanted_id": 12, "by_id": UID},
                                None) == (
        "refused", "That build has already landed on your station.", None)
    assert stops == ["1500"]

    # No phone yet: nothing to stop - the builder's attach ends it.
    _call_off(monkeypatch, {"stage": "running", "serial": "", "proxy_name": "",
                            "status": "running", "id": 12})
    assert verbs.call_off_build(None, None, on, {"wanted_id": 12}, None)[0] == "done"
    assert stops == ["1500"]

    # A stop that cannot be written says so.
    fake.answers["hold_state"] = {"status": "building", "state": ""}
    _call_off(monkeypatch, {"stage": "running", "serial": "1500",
                            "proxy_name": "", "status": "running", "id": 12})
    monkeypatch.setattr(store_stops, "ask",
                        lambda s, serial: (_ for _ in ()).throw(OSError("down")))
    status, said, _ = verbs.call_off_build(None, None, on, {"wanted_id": 12}, None)
    assert (status, said) == ("failed", "could not reach the builders - press "
                                        "Call off again")

    for stage, words in (("ended", "That build has already ended."),
                         ("landed", "That build has already landed on your station.")):
        _call_off(monkeypatch, {"stage": stage, "serial": "", "proxy_name": "",
                                "status": "done", "id": 12})
        assert verbs.call_off_build(None, None, on, {"wanted_id": 12},
                                    None) == ("refused", words, None)
    _call_off(monkeypatch, None)
    assert verbs.call_off_build(None, None, on, {"wanted_id": 12},
                                None) == ("refused", "That build is not yours.",
                                          None)
    _call_off(monkeypatch, {"stage": "already", "serial": "", "proxy_name": "",
                            "status": "running", "id": 12})
    assert verbs.call_off_build(None, None, on, {"wanted_id": 12}, None) == (
        "done", "The build is already being called off.",
        {"wanted_id": 12, "stage": "already"})
    assert verbs.call_off_build(None, None, on, {"wanted_id": "x"}, None)[0] == \
        "refused"
    assert verbs.call_off_build(None, None, None, {"wanted_id": 12}, None)[0] == \
        "failed"


def test_calling_off_a_queued_build_archives_its_waiting_one_off_unless_another_wish_names_it(  # noqa: E501
        monkeypatch, on):
    def book_with_one_off():
        book = make_book(proxies=1)
        book.proxies._rows[0].values["Name"] = "SX1"
        verbs._one_off_exit(book, {"by": "sara"}, "5.6.7.8:1080:u:p",
                            "5.6.7.8:1080")
        return book

    for stage in ("queued", "job_cancelled"):
        book = book_with_one_off()
        _call_off(monkeypatch, {"stage": stage, "serial": "",
                                "proxy_name": "5.6.7.8:1080",
                                "status": "queued", "id": 3})
        status, said, detail = verbs.call_off_build(
            book, None, on, {"wanted_id": 3, "by_id": UID}, None)
        assert (status, said) == ("done", "The build was called off.")
        assert detail == {"wanted_id": 3, "stage": stage}
        assert book.proxies.find_by_name("5.6.7.8:1080") is None, "archived"
        assert book.proxies.find_by_name("SX1") is not None, "stock is untouched"

    book = book_with_one_off()
    _call_off(monkeypatch, {"stage": "queued", "serial": "",
                            "proxy_name": "5.6.7.8:1080", "status": "queued",
                            "id": 3}, named=True)
    assert verbs.call_off_build(book, None, on, {"wanted_id": 3}, None)[0] == "done"
    assert book.proxies.find_by_name("5.6.7.8:1080") is not None, \
        "another wish or a live phone still names it"

    # A named pool exit is stock, never archived by a call-off.
    book = book_with_one_off()
    _call_off(monkeypatch, {"stage": "queued", "serial": "", "proxy_name": "SX1",
                            "status": "queued", "id": 3})
    verbs.call_off_build(book, None, on, {"wanted_id": 3}, None)
    assert book.proxies.find_by_name("SX1") is not None


# ------------------------------------------------------------------ boot
def test_a_station_boot_refuses_a_phone_that_is_not_theirs(monkeypatch, on):
    book = _book()
    vendor = Vendor(monkeypatch)
    fake = Station(monkeypatch, holds=False)
    status, said, detail = verbs.boot_phone(book, None, on, {"serial": "1500",
                                                             **STATION}, object())
    assert (status, said, detail) == (
        "refused", "phone 1500 is not yours any more - it went back to the shelf",
        None)
    assert vendor.calls == [], "never started"
    assert fake.asked("holds") == [(("1500", UID), {})]
    # The question is not guarded: a store that will not answer leaves the
    # row for the lane rather than booting somebody's phone.
    fake.answers["holds"] = RuntimeError("store down")
    with pytest.raises(RuntimeError):
        verbs.boot_phone(book, None, on, {"serial": "1500", **STATION}, object())
    assert vendor.calls == []


def test_a_dashboard_boot_never_takes_over_a_station_hold(monkeypatch, on):
    stamped = []
    monkeypatch.setattr(verbs, "_stamp_owner",
                        lambda s, serial, by_id: stamped.append(by_id))
    book = _book()
    vendor = Vendor(monkeypatch)
    fake = Station(monkeypatch, station_holder=7)
    status, said, _ = verbs.boot_phone(book, None, on,
                                       {"serial": "1500", "by": "ali", "by_id": 9},
                                       object())
    assert (status, said) == ("refused", "phone 1500 is on somebody's station")
    assert vendor.calls == [] and stamped == []
    assert _row(book)["State"] != "taken"

    # Their own Station hold, or nobody's: the dashboard's Boot as today.
    for holder in (9, None, RuntimeError("store down")):
        fake.answers["station_holder"] = holder
        vendor.calls.clear()
        status, said, detail = verbs.boot_phone(
            book, None, on, {"serial": "1500", "by": "ali", "by_id": 9}, object())
        assert status == "done" and detail == {"state": "taken",
                                               "url": "https://v/1"}
        assert said == "phone 1500 started and taken by ali"
        assert vendor.names() == ["start"]
    assert _row(book)["State"] == "taken" and stamped == [9, 9, 9]
    booted = fake.asked("booted")[-1]
    assert booted == (("1500", "https://v/1"), {"started": True}), \
        "the link is kept, and no owner guard on the dashboard's write"


def test_a_station_boot_on_an_on_phone_reuses_the_link_and_never_starts_it(
        monkeypatch, on):
    book = _book()
    vendor = Vendor(monkeypatch, status=phones_mod.RUNNING)
    fake = Station(monkeypatch, stored_link="https://v/kept")
    status, said, detail = verbs.boot_phone(book, None, on,
                                            {"serial": "1500", **STATION},
                                            object())
    assert (status, said) == ("done", "phone 1500 is on")
    assert detail == {"state": "taken", "url": "https://v/kept", "station": True}
    assert vendor.calls == [], "a phone already on is never started again"
    assert fake.asked("booted") == [
        (("1500", "https://v/kept"), {"owner_id": UID, "started": False})]
    assert _row(book)["State"] != "taken", "the Station never writes the State"

    # On, with no link kept: started, which is what produces one.
    fake.answers["stored_link"] = ""
    status, said, detail = verbs.boot_phone(book, None, on,
                                            {"serial": "1500", **STATION},
                                            object())
    assert vendor.names() == ["start"] and detail["url"] == "https://v/1"
    assert fake.asked("booted")[-1][1] == {"owner_id": UID, "started": True}

    # Off: the stored link is not even asked for.
    vendor.status = phones_mod.STOPPED
    vendor.url = ""
    fake.said.clear()
    status, said, detail = verbs.boot_phone(book, None, on,
                                            {"serial": "1500", **STATION},
                                            object())
    assert fake.asked("stored_link") == []
    assert said == ("phone 1500 is on - IranSpoty Cloud gave no live-view link "
                    "back")
    assert detail == {"state": "taken", "station": True}


def test_a_station_boot_given_back_while_starting_stops_it_and_says_so(
        monkeypatch, on):
    book = _book()
    vendor = Vendor(monkeypatch)
    fake = Station(monkeypatch, booted=False)
    status, said, detail = verbs.boot_phone(book, None, on,
                                            {"serial": "1500", **STATION},
                                            object())
    assert (status, said, detail) == (
        "failed", "phone 1500 went back to the shelf while it was booting", None)
    assert vendor.names() == ["start", "stop"]
    assert fake.asked("powered_off") == [(("1500",), {})]

    # A stop that fails is logged, and the answer is the same.
    def broken(client, pid):
        raise phones_mod.PhoneError("no")

    monkeypatch.setattr(phones_mod, "stop", broken)
    assert verbs.boot_phone(book, None, on, {"serial": "1500", **STATION},
                            object())[0] == "failed"


# ------------------------------------------------------------- change IP
def _change(book, on, **payload):
    return verbs.change_proxy(book, None, on, {"serial": "1500", **STATION,
                                               "boot": False, "keep_power": True,
                                               **payload}, object())


def test_change_ip_keeps_the_power_the_page_showed(monkeypatch, on):
    # On stays on: stopped, moved, started, and the link is kept.
    book = _book()
    vendor = Vendor(monkeypatch, status=phones_mod.RUNNING)
    fake = Station(monkeypatch)
    status, said, detail = _change(book, on, was_on_page="on")
    assert status == "done", said
    assert vendor.names() == ["stop", "wait", "set", "start"]
    assert detail == {"was": "SX0", "now": "SX1", "url": "https://v/1",
                      "started": True, "station": True}
    assert fake.asked("booted") == [(("1500", "https://v/1"),
                                     {"owner_id": UID, "started": True})]

    # Off stays off.
    book = _book()
    vendor.status, vendor.calls = phones_mod.STOPPED, []
    fake.said.clear()
    status, said, detail = _change(book, on, was_on_page="off")
    assert vendor.names() == ["set"]
    assert detail == {"was": "SX0", "now": "SX1", "started": False,
                      "station": True}
    assert said == ("phone 1500 is on SX1 now (it is off; it reads the new IP "
                    "when it next starts)")
    assert fake.asked("powered_off") == [(("1500",), {})]

    # Still running in the cloud, but the page drew it Ready: stopped, and
    # not started again.
    book = _book()
    vendor.status, vendor.calls = phones_mod.RUNNING, []
    fake.said.clear()
    status, _, detail = _change(book, on, was_on_page="off")
    assert vendor.names() == ["stop", "wait", "set"]
    assert detail["started"] is False and fake.asked("booted") == []
    assert fake.asked("powered_off") == [(("1500",), {})]

    # Given back while its IP changed: switched off again, in words.
    book = _book()
    vendor.calls = []
    fake.answers["booted"] = False
    status, said, detail = _change(book, on, was_on_page="on")
    assert (status, said) == ("failed", "phone 1500 went back to the shelf while "
                                        "its IP changed")
    assert vendor.names() == ["stop", "wait", "set", "start", "stop"]
    assert detail == {"was": "SX0", "now": "SX1", "off": True, "station": True}

    # Not theirs any more: nothing is claimed or stopped.
    book = _book()
    vendor.calls = []
    fake.answers["holds"] = False
    assert _change(book, on, was_on_page="on") == (
        "refused", "phone 1500 is not yours any more", None)
    assert vendor.calls == [] and book.proxies.status_of(
        book.proxies.find_by_name("SX1")) == "free"


def test_change_ip_claims_the_phones_lane_and_writes_the_exit_ip(monkeypatch, on):
    lanes = {"SX1": "spotify", "SX2": ""}
    book = _book(lanes=lanes)                          # a GPT phone
    Vendor(monkeypatch)
    Station(monkeypatch)
    status, said, detail = _change(book, on, was_on_page="off")
    assert status == "done" and detail["now"] == "SX2", \
        "a GPT phone never gets a Spotify exit"
    assert _row(book)["Exit IP"] == "8.8.8.8"
    assert book.proxies.find_by_name("SX2").values["Last Exit IP"] == "8.8.8.8"

    book = _book(lanes={"SX1": "", "SX2": "spotify"}, purpose="spotify")
    status, _, detail = _change(book, on, was_on_page="off")
    assert detail["now"] == "SX2", "a Spotify phone takes its own lane first"

    # A dead exit is marked and the next one tried.
    book = _book(proxies=4, lanes={"SX1": "spotify"})

    def check(client, proxy):
        if proxy.host == "10.0.0.2":
            raise verbs.proxy_mod.ProxyError("no answer")
        return {"outboundIP": "9.9.9.9"}

    monkeypatch.setattr(verbs.proxy_mod, "check", check)
    status, _, detail = _change(book, on, was_on_page="off")
    assert detail["now"] == "SX3"
    assert book.proxies.status_of(book.proxies.find_by_name("SX2")) == "dead"
    assert _row(book)["Exit IP"] == "9.9.9.9"


def test_change_ip_frees_the_old_ip_only_when_no_other_phone_is_on_it(
        monkeypatch, on):
    book = _book()
    Vendor(monkeypatch)
    fake = Station(monkeypatch, exit_shared=True)
    status, _, _ = _change(book, on, was_on_page="off")
    old = book.proxies.find_by_name("SX0")
    assert status == "done"
    assert book.proxies.status_of(old) == "on a phone", "another phone is on it"
    assert "Phone 1500 left it on" in old.values["Note"]
    assert "another phone is still on it" in old.values["Note"]
    assert fake.asked("exit_shared") == [(("SX0", "1500"), {})]

    # A store that will not answer: shared, the safe side.
    book = _book()
    fake.answers["exit_shared"] = RuntimeError("down")
    _change(book, on, was_on_page="off")
    assert book.proxies.status_of(book.proxies.find_by_name("SX0")) == "on a phone"

    book = _book()
    fake.answers["exit_shared"] = False
    _change(book, on, was_on_page="off")
    old = book.proxies.find_by_name("SX0")
    assert book.proxies.status_of(old) == "free"
    assert "Left phone 1500" in old.values["Note"]


def test_change_ip_with_no_free_exit_in_the_lane_keeps_the_old_one_in_words(
        monkeypatch, on):
    book = _book(lanes={"SX1": "spotify", "SX2": "spotify"})
    vendor = Vendor(monkeypatch, status=phones_mod.RUNNING)
    Station(monkeypatch)
    status, said, detail = _change(book, on, was_on_page="on")
    assert (status, said) == ("failed", "there is no free GPT IP left - phone "
                                        "1500 kept SX0")
    assert detail == {"off": False, "station": True}
    assert vendor.calls == [], "nothing stopped"
    assert _row(book)["Proxy"] == "SX0"

    # The dashboard's press says the same, with no detail as before.
    status, said, detail = verbs.change_proxy(
        book, None, None, {"serial": "1500", "by": "ali"}, object())
    assert status == "failed" and "no free GPT IP" in said and detail is None


def test_change_ip_says_ip_and_names_no_vendor_when_the_cloud_refuses(
        monkeypatch, on):
    book = _book()
    Vendor(monkeypatch, status=phones_mod.RUNNING)
    fake = Station(monkeypatch)

    def refuse(client, pid, proxy):
        raise phones_mod.PhoneError("[45004] proxy check failed")

    monkeypatch.setattr(phones_mod, "set_proxy", refuse)
    status, said, detail = _change(book, on, was_on_page="on")
    assert status == "failed"
    assert said == "the new IP was refused - phone 1500 kept SX0"
    assert detail == {"off": True, "station": True}
    assert book.proxies.status_of(book.proxies.find_by_name("SX1")) == "free"
    assert "45004" not in book.proxies.find_by_name("SX1").values["Note"]
    assert fake.asked("powered_off") == [(("1500",), {})], "it was stopped"

    # The vendor's own words, path and trace id never reach a sentence;
    # its code rides in the detail for a page that needs it.
    from geelark_farm.api import ApiError

    def vendor_refuses(client, pid, proxy):
        raise ApiError(45004, "GeeLark says no", path="/v1/phone/detail/update",
                       trace_id="t-1")

    monkeypatch.setattr(phones_mod, "set_proxy", vendor_refuses)
    status, said, detail = _change(_book(), on, was_on_page="on")
    assert said == "the new IP was refused - phone 1500 kept SX0"
    assert detail == {"off": True, "station": True, "code": 45004}
    status, said, detail = verbs.change_proxy(
        _book(), None, on, {"serial": "1500", "by": "ali"}, object())
    assert (status, said, detail) == (
        "failed", "the new IP was refused - phone 1500 kept SX0",
        {"code": 45004})

    def no_start(client, pid, **k):
        raise ApiError(43043, "GeeLark start failed", path="/v1/phone/start",
                       trace_id="t-2")

    monkeypatch.setattr(phones_mod, "set_proxy", lambda *a: None)
    monkeypatch.setattr(phones_mod, "start", no_start)
    status, said, detail = _change(_book(), on, was_on_page="on")
    assert said == ("phone 1500 is on SX1 now but would not start - press "
                    "Boot again in a minute")
    assert detail["code"] == 43043 and detail["off"] is True
    status, said, detail = verbs.boot_phone(
        _book(), None, on, {"serial": "1500", **STATION}, object())
    assert (status, said, detail) == (
        "failed", "phone 1500 would not start - press Boot again in a minute",
        {"code": 43043})


def test_a_dashboard_change_ip_never_moves_somebodys_station_hold(
        monkeypatch, on):
    book = _book()
    vendor = Vendor(monkeypatch, status=phones_mod.RUNNING)
    Station(monkeypatch, station_holder=7)
    assert verbs.change_proxy(book, None, on, {"serial": "1500", "by_id": 9},
                              object()) == (
        "refused", "phone 1500 is on somebody's station", None)
    assert vendor.calls == []


# ------------------------------------------------------------- power off
def test_power_off_writes_the_phone_off_and_leaves_a_watched_one_on(
        monkeypatch, on):
    vendor = Vendor(monkeypatch, status=phones_mod.RUNNING)
    fake = Station(monkeypatch)
    assert verbs.power_off_phone(None, FakeLedger(), on, {"serial": "1500"},
                                 object()) == (
        "done", "phone 1500 is off - it stops billing", {"off": True})
    assert vendor.names() == ["stop"]
    assert fake.asked("powered_off") == [(("1500",), {})]

    vendor.status = phones_mod.STOPPED
    assert verbs.power_off_phone(None, FakeLedger(), on, {"serial": "1500"},
                                 object())[1] == "phone 1500 was already off"
    assert len(fake.asked("powered_off")) == 2, "already off is written too"

    # The Station's closed rule: a tab beating again keeps its phone on.
    vendor.status, vendor.calls = phones_mod.RUNNING, []
    fake.answers["is_watched"] = True
    assert verbs.power_off_phone(None, FakeLedger(), on,
                                 {"serial": "1500", "why": "closed"},
                                 object()) == (
        "done", "phone 1500 is being watched again - left on", None)
    assert vendor.calls == []
    assert fake.asked("is_watched") == [(("1500", 45), {})]
    # Only the closed rule asks; a give-back switches off whatever the tab.
    verbs.power_off_phone(None, FakeLedger(), on,
                          {"serial": "1500", "why": "given back"}, object())
    assert vendor.names() == ["stop"]
    # A store that will not answer reads unwatched: it is stopped.
    fake.answers["is_watched"] = RuntimeError("down")
    vendor.calls = []
    verbs.power_off_phone(None, FakeLedger(), on,
                          {"serial": "1500", "why": "closed"}, object())
    assert vendor.names() == ["stop"]

    # A run's phone is the run's.
    ledger = FakeLedger({"P1500": FakeClaim()})
    status, said, _ = verbs.power_off_phone(None, ledger, on, {"serial": "1500"},
                                            object())
    assert status == "refused" and "held by a run" in said


# ------------------------------------------------------------- the lock
class Lock:
    """A power lock that writes its entry and exit into the vendor's log."""

    def __init__(self, calls):
        self.calls = calls

    @contextmanager
    def __call__(self, serial):
        self.calls.append(("lock", serial))
        try:
            yield
        finally:
            self.calls.append(("unlock", serial))


def _inside(calls, vendor_names):
    """Every vendor call sits between a lock and its unlock."""
    depth, seen = 0, []
    for call in calls:
        if call[0] == "lock":
            depth += 1
        elif call[0] == "unlock":
            depth -= 1
        elif call[0] in vendor_names:
            seen.append(call[0])
            assert depth == 1, f"{call[0]} ran outside the power lock: {calls}"
    return seen


def test_every_power_verb_holds_the_phones_lock(monkeypatch, on):
    vendor = Vendor(monkeypatch, status=phones_mod.RUNNING)
    Station(monkeypatch)
    monkeypatch.setattr(phones_mod, "power_lock", Lock(vendor.calls))
    power = ("start", "stop", "wait", "set")

    verbs.boot_phone(_book(), None, on, {"serial": "1500", **STATION}, object())
    vendor.status = phones_mod.STOPPED
    verbs.boot_phone(_book(), None, on, {"serial": "1500", **STATION}, object())
    vendor.status = phones_mod.RUNNING
    _change(_book(), on, was_on_page="on")
    verbs.power_off_phone(None, FakeLedger(), on, {"serial": "1500"}, object())
    assert _inside(vendor.calls, power) == ["start", "start", "stop", "wait",
                                            "set", "start", "stop"]
    assert vendor.calls.count(("lock", "1500")) == 4

    # The forgotten sweep's legacy stop.
    vendor.calls.clear()
    monkeypatch.setattr(forgotten, "_station_sweep",
                        lambda *a, **k: {"off": [], "given_back": []})
    monkeypatch.setattr(forgotten, "overdue", lambda s, m, g=45: [
        {"serial": "1500", "status": "ready", "state": "", "owner": "",
         "on_seconds": 4000}])
    monkeypatch.setattr(forgotten, "_still_legacy", lambda s, serial: True)
    monkeypatch.setattr(store_events, "emit", lambda *a, **k: True)
    forgotten.sweep(object(), on, FakeLedger(),
                    [{"serialNo": "1500", "id": "P1500",
                      "status": phones_mod.RUNNING}])
    assert _inside(vendor.calls, power) == ["stop"]

    # The keeper's stop and delete of a phone marked done.
    vendor.calls.clear()
    from geelark_farm.store import person

    monkeypatch.setattr(person, "marked", lambda s: [
        {"serial": "1500", "state": "done", "gmail": "", "app_account": "",
         "sheet_row": 2}])
    monkeypatch.setattr(keeper.phones, "status",
                        lambda client, pid: phones_mod.STOPPED)
    monkeypatch.setattr(keeper.phones, "delete",
                        lambda client, ids, **k: vendor.calls.append(("delete",)))
    book = _book()
    keeper.apply_phone_states(object(), book, FakeLedger(), on)
    assert _inside(vendor.calls, power + ("delete",)) == ["stop", "delete"]


def test_the_power_lock_is_one_lock_per_phone():
    a, b = phones_mod.power_lock("1500"), phones_mod.power_lock(1500)
    assert a is b, "one lock for one serial, however it is spelled"
    assert phones_mod.power_lock("1501") is not a
    got = []

    def other():
        got.append(a.acquire(timeout=0.05))

    with a:
        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert got == [False], "a second thread waits for the first"


# ------------------------------------------------------------- verdicts
@pytest.fixture
def closes(monkeypatch):
    """`verdicts.close` and `standing` faked; `person.set_state` must not
    be reached by a verdict."""
    from geelark_farm.store import person

    seen = SimpleNamespace(closed=[], answer={"id": 1}, standing=None)
    monkeypatch.setattr(verdicts, "close",
                        lambda s, **kw: seen.closed.append(kw) or seen.answer)
    monkeypatch.setattr(verdicts, "standing", lambda s, serial: seen.standing)
    monkeypatch.setattr(person, "set_state", lambda *a, **k: pytest.fail(
        "a verdict closes the phone in one statement"))
    monkeypatch.setattr(verbs, "_is_building", lambda s, serial: False)
    return seen


def _verdict(on, button, **more):
    return verbs.set_phone_state(None, None, on, {
        "serial": "1500", "state": verdicts.BUTTONS.get(button, button),
        "button": button, "by": "sara", "by_id": UID, "where": "station",
        **more}, None)


def test_a_second_verdict_is_refused_and_writes_nothing(on, closes):
    closes.answer, closes.standing = None, {"state": "failed", "busy": "",
                                            "owner_id": None, "status": "ready"}
    assert _verdict(on, "done") == (
        "refused", "phone 1500 is already closed as failed", None)
    assert len(closes.closed) == 1, "one attempt, and it closed nothing"
    closes.standing = None
    assert _verdict(on, "done") == ("failed", "phone 1500 is not on the farm",
                                    None)


def test_a_station_verdict_on_somebody_elses_phone_is_refused(on, closes):
    closes.answer = None
    closes.standing = {"state": "taken", "busy": "", "owner_id": 9,
                       "status": "ready"}
    assert _verdict(on, "decline", mine=True) == (
        "refused", "phone 1500 is not yours any more", None)
    assert closes.closed[-1]["owner_id"] == UID
    assert closes.closed[-1]["where"] == "station"
    # The dashboard's keys ask for no owner, and say something else.
    assert _verdict(on, "decline") == (
        "refused", "phone 1500 is being worked on right now", None)
    assert closes.closed[-1]["owner_id"] is None


def test_a_verdict_while_the_ip_changes_is_refused_in_words(on, closes):
    closes.answer = None
    closes.standing = {"state": "taken", "busy": "change_proxy", "owner_id": UID}
    assert _verdict(on, "done", mine=True)[1] == \
        "phone 1500 is changing its IP - wait for it"
    closes.standing["busy"] = "boot_phone"
    assert _verdict(on, "done", mine=True)[1] == \
        "phone 1500 is booting - wait for it"


def test_a_verdict_store_error_raises_so_the_row_stays_queued(
        monkeypatch, on, closes):
    def down(settings, **kw):
        raise RuntimeError("store down")

    monkeypatch.setattr(verdicts, "close", down)
    with pytest.raises(RuntimeError):
        _verdict(on, "done", mine=True)


def test_auth_is_a_failed_verdict_with_its_own_button(on, closes):
    status, said, detail = _verdict(on, "auth", mine=True)
    assert status == "done" and detail == {"state": "failed", "button": "auth"}
    assert "(auth)" in said
    assert closes.closed == [{"serial": "1500", "button": "auth",
                              "state": "failed", "by": "sara", "by_id": UID,
                              "where": "station", "owner_id": UID}]
    assert verbs.set_phone_state(None, None, on, {
        "serial": "1500", "state": "done", "button": "auth"}, None)[0] == \
        "refused", "auth means failed, never done"


def test_taking_a_phone_on_somebodys_station_is_refused(monkeypatch, on):
    from geelark_farm.store import person

    set_to = []
    monkeypatch.setattr(person, "set_state",
                        lambda s, serial, state: set_to.append(state) or True)
    monkeypatch.setattr(verbs, "_stamp_owner", lambda *a: None)
    fake = Station(monkeypatch, station_holder=7)
    assert verbs.set_phone_state(None, None, on, {
        "serial": "1500", "state": "taken", "by_id": 9}, None) == (
        "refused", "phone 1500 is on somebody's station", None)
    assert set_to == []
    fake.answers["station_holder"] = 9
    assert verbs.set_phone_state(None, None, on, {
        "serial": "1500", "state": "taken", "by_id": 9}, None)[0] == "done"
    assert set_to == ["taken"]
    # Not guarded: it raises, like the state write it stands before.
    fake.answers["station_holder"] = RuntimeError("down")
    with pytest.raises(RuntimeError):
        verbs.set_phone_state(None, None, on, {
            "serial": "1500", "state": "taken", "by_id": 9}, None)


# ------------------------------------------------------ build by hand
def _asked(book, payload):
    asked = {}
    orig = store_wanted.ask
    store_wanted.ask = lambda s, **k: asked.update(k) or 5
    try:
        out = verbs.build_by_hand(book, None, None, {"by": "sara", **payload},
                                  None)
    finally:
        store_wanted.ask = orig
    return out, asked


def test_an_other_build_keeps_its_lane_and_carries_its_account():
    book = make_book(gmails=2, proxies=1)
    (status, said, detail), asked = _asked(book, {
        "purpose": "other", "app": "", "install_app": False, "station": True,
        "gmail": "", "no_gmail": True, "carry_address": "Me@X.com",
        "carry_password": "pw", "proxy_name": ""})
    assert status == "done" and detail == {"wanted_id": 5}
    assert asked["purpose"] == "other" and asked["app"] == ""
    assert asked["app_account"] == ""
    assert (asked["carry_address"], asked["carry_password"]) == ("me@x.com", "pw")
    assert asked["station"] is True
    assert "behind any free IP" in said and "carrying me@x.com" in said
    assert "pw" not in said, "a carried password is never said back"

    # Only an Other phone carries: a GPT build never stores a password it
    # was not asked to sign in.
    (_, _, _), asked = _asked(book, {
        "purpose": "gpt", "app": "", "station": True, "carry_address": "a@x.com",
        "carry_password": "pw"})
    assert asked["purpose"] == "gpt" and asked["carry_password"] == ""
    assert asked["carry_address"] == ""
    # An app decides the lane over the word, as it always did.
    (_, _, _), asked = _asked(book, {"purpose": "other", "app": "chatgpt"})
    assert asked["purpose"] == "gpt"
    # The dashboard's payload is unchanged: no station, nothing carried.
    (_, said, detail), asked = _asked(book, {"app": ""})
    assert asked["station"] is False and asked["carry_address"] == ""
    assert detail == {"wanted_id": 5} and "behind a GPT exit" in said


def test_a_station_build_says_ip_never_exit():
    book = make_book(gmails=1, proxies=2)
    book.proxies._rows[0].values["Name"] = "SX1"
    book.proxies.spend(book.proxies._rows[0], serial="1400", note="on it")
    station = {"station": True, "app": "", "purpose": "gpt"}
    said_all = []
    (status, said, _), _ = _asked(book, dict(station))
    said_all.append(said)
    assert status == "done" and "behind a GPT IP" in said
    (status, said, _), _ = _asked(book, dict(station, proxy_name="SX1"))
    said_all.append(said)
    assert status == "refused" and said.startswith("the IP SX1 is not free")
    (status, said, _), _ = _asked(book, dict(station, proxy_name="nonsense",
                                             proxy_typed=True))
    said_all.append(said)
    assert status == "refused" and said.startswith("that IP was not usable")
    assert not any("exit" in s for s in said_all), said_all
    # The dashboard keeps its words.
    (_, said, _), _ = _asked(book, {"app": "", "proxy_name": "SX1"})
    assert said.startswith("the exit SX1 is not free")


def test_a_named_exit_from_the_other_lane_is_refused_in_words():
    book = make_book(gmails=1, proxies=2, proxy_headers=PROXY_HEADERS_LANED)
    sx1, sx2 = book.proxies._rows
    sx1.values["Name"], sx2.values["Name"] = "SX1", "SX2"
    book.proxies.keep_for(sx1, "spotify")
    (status, said, _), asked = _asked(book, {"app": "", "purpose": "gpt",
                                            "proxy_name": "SX1", "station": True})
    assert (status, said) == ("refused", "the IP SX1 is kept for Spotify - use "
                                         "Auto or another")
    assert asked == {}, "no wish written"
    for purpose in ("spotify", "other"):
        (status, _, _), asked = _asked(book, {"app": "", "purpose": purpose,
                                              "proxy_name": "SX1"})
        assert status == "done" and asked["proxy_name"] == "SX1", purpose
    (status, _, _), _ = _asked(book, {"app": "", "purpose": "gpt",
                                      "proxy_name": "SX2"})
    assert status == "done", "an unlabelled exit serves either lane"


# ------------------------------------------------------------ pairing
def _warm(*serials):
    return [{"sheet_row": i + 2, "serial": s, "gmail": f"g{i}@example.com",
             "proxy": "", "app": "yes", "status": "incomplete",
             "phone_id": f"P{s}", "purpose": ""} for i, s in enumerate(serials)]


def test_a_pairing_skips_a_phone_it_cannot_reserve(monkeypatch, on):
    monkeypatch.setattr(keeper, "_unfinished",
                        lambda client, book, **k: (_warm("1500", "1501"), []))
    monkeypatch.setattr(keeper, "_busy_serials", lambda s: frozenset())
    fake = Station(monkeypatch, reserve_warm=lambda serial, **k:
                   None if serial == "1500" else "app_only")
    book = make_book(apps=2)
    launched = []
    status, said, detail = verbs.login_accounts(
        book, None, on, {"by": "sara", "by_id": UID,
                         "addresses": ["a0@example.com", "a1@example.com"]},
        object(), launch=launched.append)
    assert status == "running"
    jobs = launched[0]
    assert [j["phone"]["serial"] for j in jobs] == ["1501"], \
        "1500 was taken in between: skipped for the next warm phone"
    assert jobs[0]["phone"]["status_before"] == "app_only"
    assert detail["unpaired"] == ["a1@example.com"]
    assert fake.asked("reserve_warm") == [(("1500",), {"owner_id": UID}),
                                          (("1501",), {"owner_id": UID})]

    # A claim that fails after the reservation puts the status back.
    book = make_book(apps=1)
    fake.said.clear()
    monkeypatch.setattr(type(book.apps), "claim_this",
                        lambda self, resource, serial, also=(): False)
    status, said, detail = verbs.login_accounts(
        book, None, on, {"by": "sara", "by_id": UID,
                         "addresses": ["a0@example.com"]},
        object(), launch=launched.append)
    assert fake.asked("unreserve_warm") == [(("1501", "app_only"), {})]
    assert detail["refused"] == ["a0@example.com: taken by another run meanwhile"]

    # The phone a person named cannot be reserved: refused in words.
    monkeypatch.undo()
    monkeypatch.setattr(keeper, "_unfinished",
                        lambda client, book, **k: (_warm("1500", "1501"), []))
    monkeypatch.setattr(keeper, "_busy_serials", lambda s: frozenset())
    Station(monkeypatch, reserve_warm=None)
    status, said, detail = verbs.login_accounts(
        make_book(apps=1), None, on,
        {"by_id": UID, "addresses": ["a0@example.com"], "serial": "1500"},
        object(), launch=launched.append)
    assert detail["refused"] == ["a0@example.com: phone 1500 cannot take an "
                                 "account right now - somebody holds it"]

    # A store that will not answer reads as reserved - the old way.
    Station(monkeypatch, reserve_warm=RuntimeError("down"))
    launched.clear()
    verbs.login_accounts(make_book(apps=1), None, on,
                         {"by_id": UID, "addresses": ["a0@example.com"]},
                         object(), launch=launched.append)
    assert launched[0][0]["phone"]["serial"] == "1500"
    assert "status_before" not in launched[0][0]["phone"]


# ------------------------------------------------------------- the sweep
@pytest.fixture
def sweep(monkeypatch, on):
    seen = SimpleNamespace(events=[], rows=[], given=None, queued=None,
                           legacy=[], stopped=[], off=[], idle=[])
    monkeypatch.setattr(store_station, "overdue",
                        lambda s, m, g, closed=20: list(seen.rows))
    monkeypatch.setattr(store_station, "give_back_idle",
                        lambda s, serial, m, g: seen.idle.append(serial)
                        or seen.given)
    monkeypatch.setattr(store_station, "queue_off_closed",
                        lambda s, serial, g, c: seen.queued)
    monkeypatch.setattr(store_station, "powered_off",
                        lambda s, serial: seen.off.append(serial) or True)
    monkeypatch.setattr(forgotten, "overdue", lambda s, m, g=45: list(seen.legacy))
    monkeypatch.setattr(forgotten, "_release", lambda s, serial: True)
    monkeypatch.setattr(phones_mod, "stop",
                        lambda client, pid: seen.stopped.append(pid))
    monkeypatch.setattr(store_events, "emit",
                        lambda s, kind, **f: seen.events.append((kind, f)) or True)
    seen.settings = on
    return seen


def _hold(serial="1500", why="alone", **more):
    return {"serial": serial, "running": True, "owner_id": UID,
            "purpose": "gpt", "owner": "sara", "why": why,
            "idle_seconds": 3700.0, "closed_seconds": 25.0,
            "unwatched_seconds": 30.0, **more}


def _listed(status=phones_mod.RUNNING):
    return [{"serialNo": "1500", "id": "P1500", "status": status}]


def test_the_station_sweep_runs_even_when_no_legacy_phone_is_over(
        sweep, monkeypatch):
    sweep.rows = [_hold()]
    sweep.given = {"id": 1, "lane": "gpt", "running": True, "off_id": 5,
                   "owner_id": UID}
    outcome = forgotten.sweep(object(), sweep.settings, FakeLedger(), _listed())
    assert outcome == {"off": [], "released": [], "held": [],
                       "given_back": ["1500"]}
    assert sweep.stopped == [], "the Station branch never calls the cloud"

    # A branch that fails costs a warning, not the legacy rules.
    def broken(*a, **k):
        raise RuntimeError("down")

    sweep.legacy = [{"serial": "1500", "status": "ready", "state": "taken",
                     "owner": "ali", "taken_seconds": 4000, "on_seconds": None}]
    monkeypatch.setattr(store_station, "overdue", broken)
    monkeypatch.setattr(forgotten, "_still_legacy", lambda s, serial: True)
    outcome = forgotten.sweep(object(), sweep.settings, FakeLedger(), _listed())
    assert outcome == {"off": ["1500"], "released": ["1500"], "held": []}


def test_the_sweep_gives_back_a_station_hold_left_alone_an_hour_and_says_whose(
        sweep):
    sweep.rows = [_hold()]
    sweep.given = {"id": 1, "lane": "gpt", "running": False, "off_id": None,
                   "owner_id": UID}
    forgotten.sweep(object(), sweep.settings, FakeLedger(), _listed())
    assert sweep.events == [("phone", {
        "serial": "1500", "status": "given back", "user_id": UID,
        "detail": "given back after an hour left alone with sara"})]
    assert sweep.idle == ["1500"]

    # A Boot or a beat that won the race: nothing given back, nothing said.
    sweep.events.clear()
    sweep.given = None
    outcome = forgotten.sweep(object(), sweep.settings, FakeLedger(), _listed())
    assert sweep.events == [] and "given_back" not in outcome


def test_the_sweep_queues_the_power_off_of_a_station_hold_whose_tab_closed_and_keeps_it_theirs(  # noqa: E501
        sweep):
    sweep.rows = [_hold(why="closed")]
    sweep.queued = 91
    outcome = forgotten.sweep(object(), sweep.settings, FakeLedger(), _listed())
    assert outcome["off"] == ["1500"] and sweep.stopped == []
    assert sweep.idle == [], "closed is not the hour: it stays theirs"
    assert sweep.events == [("phone", {
        "serial": "1500", "status": "switched off", "user_id": UID,
        "detail": "switched off 25 s after its live tab closed with sara - it "
                  "stays theirs for an hour"})]

    # Already pending: said once, by the pass that queued it.
    sweep.events.clear()
    sweep.queued = None
    assert forgotten.sweep(object(), sweep.settings, FakeLedger(),
                           _listed())["off"] == []
    assert sweep.events == []

    # Off in the listing: the running flag was stale, and is put right.
    sweep.queued = 91
    forgotten.sweep(object(), sweep.settings, FakeLedger(),
                    _listed(phones_mod.STOPPED))
    assert sweep.off == ["1500"] and sweep.events == []


def test_the_sweep_leaves_a_watched_station_hold_alone_and_the_legacy_rules_skip_it(
        sweep, monkeypatch):
    # Watched: the store's rule never returns it, so nothing happens.
    sweep.rows = []
    outcome = forgotten.sweep(object(), sweep.settings, FakeLedger(), _listed())
    assert outcome == {"off": [], "released": [], "held": []}
    # The legacy rules leave Station holds out, in the store's own words.
    # (Read from the file: the fixture has faked both functions.)
    source = pathlib.Path(forgotten.__file__).read_text(encoding="utf-8")
    legacy = source[source.index("def overdue("):source.index("def _release(")]
    assert "   AND p.taken_at IS NULL" in legacy
    release = source[source.index("def _release("):
                     source.index("def _still_legacy(")]
    assert " AND taken_at IS NULL" in release
    assert "SET state = '', owner_id = NULL," in release
    assert "watched_at = NULL" in release and "tab_closed_at = NULL" in release
    # A phone that became a Station hold after the legacy read is not
    # stopped by the legacy rule.
    sweep.legacy = [{"serial": "1500", "status": "ready", "state": "",
                     "owner": "", "on_seconds": 4000}]
    monkeypatch.setattr(forgotten, "_still_legacy", lambda s, serial: False)
    forgotten.sweep(object(), sweep.settings, FakeLedger(), _listed())
    assert sweep.stopped == []
    monkeypatch.setattr(forgotten, "_still_legacy", lambda s, serial: True)
    forgotten.sweep(object(), sweep.settings, FakeLedger(), _listed())
    assert sweep.stopped == ["P1500"] and sweep.off == ["1500"], \
        "a legacy stop is written off in the store too"


# ------------------------------------------------------------- vendor
def _strings(fn) -> list[str]:
    """Every string a function can say - its docstring left out."""
    tree = ast.parse(inspect.getsource(fn).lstrip())
    top = tree.body[0]
    doc = ast.get_docstring(top, clean=False)
    out = []
    for node in ast.walk(top):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value != doc:
                out.append(node.value)
    return out


def test_the_verbs_name_no_vendor():
    for fn in (verbs.give_back, verbs.call_off_build, verbs._why_not_closed,
               verbs.boot_phone, verbs.change_proxy, verbs.power_off_phone,
               verbs.set_phone_state, verbs._archive_one_off,
               verbs._refused_by_holder, verbs.build_by_hand,
               verbs.login_accounts, forgotten._station_sweep, forgotten.sweep):
        for text in _strings(fn):
            assert "geelark" not in text.lower(), (fn.__name__, text)


# ------------------------------------------- review fixes, 2026-09-29
def test_give_back_closes_orphaned_presses_first_and_says_so_when_no_off_is_queued(
        monkeypatch, on, caplog):
    """keeper-1, store-3: an orphaned power press is closed before the
    give-back, and a give-back with no power-off behind it is an error in
    the log, not a silence."""
    import logging

    fake = Station(monkeypatch, give_back={"id": 1, "lane": "gpt",
                                           "running": True, "off_id": 77})
    verbs.give_back(None, None, on, {"serial": "1500", "by_id": UID}, None)
    names = [n for n, _a, _k in fake.said]
    assert names.index("expire_power") < names.index("give_back")
    assert fake.asked("expire_power") == [(("1500",), {})]
    assert fake.asked("power_pending_of") == [], "an off was queued"

    fake.answers["give_back"] = {"id": 1, "lane": "gpt", "running": True,
                                 "off_id": None}
    with caplog.at_level(logging.ERROR):
        assert verbs.give_back(None, None, on, {"serial": "1500", "by_id": UID},
                               None)[0] == "done"
    assert "given back but no power-off was queued" in caplog.text
    assert fake.asked("power_pending_of") == [(("1500", ("power_off_phone",)),
                                               {})]
    # One already pending (an earlier give-back's): nothing to say.
    caplog.clear()
    fake.answers["power_pending_of"] = {"id": 5, "verb": "power_off_phone",
                                        "stale": False}
    with caplog.at_level(logging.ERROR):
        verbs.give_back(None, None, on, {"serial": "1500", "by_id": UID}, None)
    assert "no power-off" not in caplog.text
    # A store that cannot close the orphans still gives the phone back.
    fake.answers["expire_power"] = RuntimeError("down")
    assert verbs.give_back(None, None, on, {"serial": "1500", "by_id": UID},
                           None)[0] == "done"


def test_a_give_backs_power_off_leaves_a_phone_somebody_took_again(
        monkeypatch, on):
    """keeper-2: the give-back's power-off, run after the phone was taken
    from the shelf again, never switches off the new holder's phone."""
    vendor = Vendor(monkeypatch, status=phones_mod.RUNNING)
    fake = Station(monkeypatch, station_holder=9)
    for why in ("given back", "alone"):
        assert verbs.power_off_phone(None, FakeLedger(), on,
                                     {"serial": "1500", "why": why},
                                     object()) == (
            "done", "phone 1500 was taken again - left on", None)
    assert vendor.calls == [] and fake.asked("powered_off") == []
    # A closed tab's power-off and the dashboard's are not give-backs.
    for why in ("closed", ""):
        vendor.calls.clear()
        verbs.power_off_phone(None, FakeLedger(), on,
                              {"serial": "1500", "why": why}, object())
        assert vendor.names() == ["stop"], why
    # Still on the shelf: switched off.
    fake.answers["station_holder"] = None
    vendor.calls.clear()
    said = verbs.power_off_phone(None, FakeLedger(), on,
                                 {"serial": "1500", "why": "given back"},
                                 object())[1]
    assert said == "phone 1500 is off - it stops billing"
    assert vendor.names() == ["stop"]


def test_every_power_verb_reads_the_phone_inside_its_lock(monkeypatch, on):
    """keeper-4: the listing a power verb decides from is taken under the
    phone's lock, never before it."""
    vendor = Vendor(monkeypatch, status=phones_mod.RUNNING)
    Station(monkeypatch)
    listing = vendor.listing

    def listed(client, **k):
        vendor.calls.append(("list",))
        return listing(client, **k)

    monkeypatch.setattr(phones_mod, "listing", listed)
    monkeypatch.setattr(phones_mod, "power_lock", Lock(vendor.calls))
    verbs.boot_phone(_book(), None, on, {"serial": "1500", **STATION}, object())
    _change(_book(), on, was_on_page="on")
    verbs.power_off_phone(None, FakeLedger(), on, {"serial": "1500"}, object())
    assert _inside(vendor.calls, ("list",)) == ["list", "list", "list"]

    # The sweep stopped the phone while a Boot waited on the lock: the Boot
    # reads it off and starts it, never handing back the dead link.
    vendor.calls.clear()
    vendor.status = phones_mod.STOPPED
    fake = Station(monkeypatch, stored_link="https://v/dead")
    status, said, detail = verbs.boot_phone(
        _book(), None, on, {"serial": "1500", **STATION}, object())
    assert detail["url"] == "https://v/1" and "start" in vendor.names()
    assert fake.asked("stored_link") == []


def test_the_legacy_sweep_clears_the_link_inside_the_power_lock(monkeypatch, on):
    """keeper-m1: the stored link is gone before the lock is let go."""
    calls: list = []
    monkeypatch.setattr(phones_mod, "power_lock", Lock(calls))
    monkeypatch.setattr(phones_mod, "stop",
                        lambda client, pid: calls.append(("stop", pid)))
    monkeypatch.setattr(store_station, "powered_off",
                        lambda s, serial: calls.append(("off", serial)) or True)
    monkeypatch.setattr(forgotten, "_station_sweep",
                        lambda *a, **k: {"off": [], "given_back": []})
    monkeypatch.setattr(forgotten, "overdue", lambda s, m, g=45: [
        {"serial": "1500", "status": "ready", "state": "", "owner": "",
         "on_seconds": 4000}])
    monkeypatch.setattr(forgotten, "_still_legacy", lambda s, serial: True)
    monkeypatch.setattr(store_events, "emit", lambda *a, **k: True)
    out = forgotten.sweep(object(), on, FakeLedger(),
                          [{"serialNo": "1500", "id": "P1500",
                            "status": phones_mod.RUNNING}])
    assert _inside(calls, ("stop", "off")) == ["stop", "off"]
    assert out["off"] == ["1500"]

    # A stop that fails writes nothing off.
    calls.clear()

    def broken(client, pid):
        raise phones_mod.PhoneError("no")

    monkeypatch.setattr(phones_mod, "stop", broken)
    out = forgotten.sweep(object(), on, FakeLedger(),
                          [{"serialNo": "1500", "id": "P1500",
                            "status": phones_mod.RUNNING}])
    assert ("off", "1500") not in calls and out["off"] == []


def test_the_power_verbs_survive_the_clouds_hangs_in_words(monkeypatch, on):
    """keeper-5: a call that never came back is an answer in words, the
    exit it claimed goes back, and the store says off once a stop went
    through."""
    from geelark_farm.api import TransportError

    def hang(*a, **k):
        raise TransportError("no answer after 3 tries")

    # The check of the new exit hangs: nothing touched, the exit put back.
    book = _book()
    vendor = Vendor(monkeypatch, status=phones_mod.RUNNING)
    fake = Station(monkeypatch)
    monkeypatch.setattr(verbs.proxy_mod, "check", hang)
    status, said, detail = _change(book, on, was_on_page="on")
    assert (status, said) == ("failed", "the IP could not be changed right now "
                                        "- phone 1500 kept SX0")
    assert detail == {"off": False, "station": True}
    assert vendor.calls == []
    assert book.proxies.status_of(book.proxies.find_by_name("SX1")) == "free"
    assert _row(book)["Proxy"] == "SX0"
    monkeypatch.setattr(verbs.proxy_mod, "check",
                        lambda client, proxy: {"outboundIP": "8.8.8.8"})

    # The wait after the stop, or the move itself, hangs: the phone was
    # asked to stop, so the store says off and the page offers Boot.
    for where in ("wait_until_stopped", "set_proxy"):
        book = _book()
        vendor.calls.clear()
        fake.said.clear()
        with monkeypatch.context() as m:
            m.setattr(phones_mod, where, hang)
            status, said, detail = _change(book, on, was_on_page="on")
        assert (status, said) == ("failed", "the new IP was refused - phone "
                                            "1500 kept SX0"), where
        assert detail == {"off": True, "station": True}, where
        assert fake.asked("powered_off") == [(("1500",), {})], where
        sx1 = book.proxies.find_by_name("SX1")
        assert book.proxies.status_of(sx1) == "free", where

    # The start on the new exit hangs.
    book = _book()
    fake.said.clear()
    monkeypatch.setattr(phones_mod, "start", hang)
    status, said, detail = _change(book, on, was_on_page="on")
    assert said == ("phone 1500 is on SX1 now but would not start - press "
                    "Boot again in a minute")
    assert detail["off"] is True and fake.asked("powered_off")

    # Boot and Power off.
    vendor.status = phones_mod.STOPPED
    assert verbs.boot_phone(_book(), None, on, {"serial": "1500", **STATION},
                            object()) == (
        "failed", "phone 1500 did not answer - press Boot again in a minute",
        None)
    vendor.status = phones_mod.RUNNING
    monkeypatch.setattr(phones_mod, "stop", hang)
    assert verbs.power_off_phone(None, FakeLedger(), on, {"serial": "1500"},
                                 object()) == (
        "failed", "phone 1500 would not stop - it is tried again", None)


def test_a_second_call_off_asks_the_stop_again_while_the_phone_builds(
        monkeypatch, on):
    """keeper-7: the first press wrote the call-off and stumbled before
    its stop; the second one ('already') asks the stop again."""
    from geelark_farm.store import stops as store_stops

    stops = []
    monkeypatch.setattr(store_stops, "ask", lambda s, serial: stops.append(serial))
    fake = Station(monkeypatch, hold_state={"status": "building", "state": ""})
    _call_off(monkeypatch, {"stage": "already", "serial": "1500",
                            "proxy_name": "", "status": "running", "id": 12})
    assert verbs.call_off_build(None, None, on, {"wanted_id": 12}, None) == (
        "done", "The build is already being called off.",
        {"wanted_id": 12, "stage": "already"})
    assert stops == ["1500"]

    # Landed meanwhile: no stop, and the same answer as before.
    fake.answers["hold_state"] = {"status": "ready", "state": "taken"}
    said = verbs.call_off_build(None, None, on, {"wanted_id": 12}, None)[1]
    assert said == "The build is already being called off."
    assert stops == ["1500"]

    # The phone cannot be read, on either press: said, never a traceback.
    fake.answers["hold_state"] = RuntimeError("down")
    for stage in ("already", "running"):
        _call_off(monkeypatch, {"stage": stage, "serial": "1500",
                                "proxy_name": "", "status": "running", "id": 12})
        assert verbs.call_off_build(None, None, on, {"wanted_id": 12},
                                    None) == (
            "failed", "could not reach the builders - press Call off again",
            None)
    assert stops == ["1500"]


def test_the_station_sweep_closes_orphans_and_queues_one_power_off(
        sweep, monkeypatch):
    """store-3 and rev 42's narrower index: orphaned presses are closed
    before the hour's give-back and the closed tab's power-off, and a
    power-off already queued is not queued again."""
    order: list = []
    pending: list = [None]
    monkeypatch.setattr(store_station, "expire_power",
                        lambda s, serial: order.append(("expire", serial)) or 0)
    monkeypatch.setattr(store_station, "power_pending_of",
                        lambda s, serial, verbs=None:
                        order.append(("pending", serial, verbs)) or pending[0])
    monkeypatch.setattr(store_station, "give_back_idle",
                        lambda s, serial, m, g: order.append(("give", serial))
                        or None)
    monkeypatch.setattr(store_station, "queue_off_closed",
                        lambda s, serial, g, c: order.append(("queue", serial))
                        or 91)
    sweep.rows = [_hold(why="alone")]
    forgotten.sweep(object(), sweep.settings, FakeLedger(), _listed())
    assert order == [("expire", "1500"), ("give", "1500")]

    order.clear()
    sweep.rows = [_hold(why="closed")]
    assert forgotten.sweep(object(), sweep.settings, FakeLedger(),
                           _listed())["off"] == ["1500"]
    assert order == [("expire", "1500"),
                     ("pending", "1500", ("power_off_phone",)),
                     ("queue", "1500")]

    order.clear()
    sweep.events.clear()
    pending[0] = {"id": 91, "verb": "power_off_phone", "stale": False}
    assert forgotten.sweep(object(), sweep.settings, FakeLedger(),
                           _listed())["off"] == []
    assert ("queue", "1500") not in order and sweep.events == []

    # A store that will not answer either question: the power-off is
    # still queued.
    def broken(*a, **k):
        raise RuntimeError("down")

    order.clear()
    monkeypatch.setattr(store_station, "expire_power", broken)
    monkeypatch.setattr(store_station, "power_pending_of", broken)
    assert forgotten.sweep(object(), sweep.settings, FakeLedger(),
                           _listed())["off"] == ["1500"]


# ========================================================== the cluster
DSN = os.environ.get("GEELARK_TEST_DSN", "")
needs_cluster = pytest.mark.skipif(
    not DSN, reason="set GEELARK_TEST_DSN to run store integration tests")
_READY: list = []


class Cluster:
    def __init__(self, settings, tag: str):
        self.s = settings
        self.tag = tag
        self.users: list[int] = []
        self.serials: list[str] = []
        self._n = 0
        self._base = int(tag, 16) % 900000

    def sql(self, text, params=()):
        from geelark_farm.store.db import connect

        with connect(self.s) as conn:
            cur = conn.execute(text, params)
            rows = ([dict(zip([d.name for d in cur.description], r, strict=True))
                     for r in cur.fetchall()] if cur.description else [])
            conn.commit()
        return rows

    def user(self, letter: str) -> int:
        row = self.sql(
            "INSERT INTO users (username, password_hash, password_salt, role,"
            " sees, may_take_phones) VALUES (%s, 'x', 'y', 'operator', 'all',"
            " true) RETURNING id", (f"st_{self.tag}_{letter}",))[0]
        self.users.append(int(row["id"]))
        return int(row["id"])

    def phone(self, *, owner=None, station=False, state="", **cols) -> str:
        self._n += 1
        serial = f"9{self._base:06d}{self._n:02d}"
        self.serials.append(serial)
        base = {"serial": serial, "status": "ready", "state": state,
                "purpose": "gpt", "phone_id": "PH" + serial, "owner_id": owner}
        base.update(cols)
        names = list(base) + (["taken_at"] if station else [])
        holes = ["%s"] * len(base) + (["now()"] if station else [])
        self.sql(f"INSERT INTO phones ({', '.join(names)})"
                 f" VALUES ({', '.join(holes)})", list(base.values()))
        return serial

    def row(self, serial):
        return self.sql("SELECT * FROM phones WHERE serial = %s"
                        " ORDER BY id DESC LIMIT 1", (serial,))[0]

    def cleanup(self):
        uids = self.users or [0]
        serials = self.serials or ["-"]
        for text, params in (
                ("DELETE FROM verdicts WHERE serial = ANY(%s) OR by_id = ANY(%s)",
                 (serials, uids)),
                ("DELETE FROM actions WHERE payload->>'serial' = ANY(%s)"
                 " OR requested_by = ANY(%s)", (serials, uids)),
                ("DELETE FROM wanted_builds WHERE requested_by = ANY(%s)"
                 " OR serial = ANY(%s)", (uids, serials)),
                ("DELETE FROM phones WHERE serial = ANY(%s)", (serials,)),
                ("DELETE FROM events WHERE serial = ANY(%s) OR user_id = ANY(%s)",
                 (serials, uids)),
                ("DELETE FROM users WHERE id = ANY(%s)", (uids,))):
            self.sql(text, params)


@pytest.fixture
def cluster(make_settings):
    from geelark_farm.store import db as store_db

    parts = dict(p.split("=", 1) for p in DSN.split())
    s = make_settings(
        store_enabled=True, store_host=parts["host"],
        store_port=int(parts.get("port", 5432)), store_db=parts["dbname"],
        store_user=parts["user"], store_password=parts["password"],
        release_after_minutes=60, live_tab_grace_seconds=45)
    if not _READY:
        store_db.ensure_schema(s)
        _READY.append(True)
    c = Cluster(s, uuid.uuid4().hex[:8])
    try:
        yield c
    finally:
        c.cleanup()


@needs_cluster
def test_stamp_owner_never_takes_over_a_station_hold_on_the_cluster(cluster):
    a, b = cluster.user("a"), cluster.user("b")
    hold = cluster.phone(owner=a, station=True, state="taken")
    taken = cluster.row(hold)["taken_at"]

    verbs._stamp_owner(cluster.s, hold, b)
    row = cluster.row(hold)
    assert row["owner_id"] == a and row["taken_at"] == taken, "somebody else's"
    verbs._stamp_owner(cluster.s, hold, str(a))
    row = cluster.row(hold)
    assert row["owner_id"] == a and row["taken_at"] == taken, "its clock is kept"
    verbs._stamp_owner(cluster.s, hold, None)
    assert cluster.row(hold)["owner_id"] == a, "a Station hold is never cleared"

    legacy = cluster.phone(owner=a, state="taken")
    verbs._stamp_owner(cluster.s, legacy, b)
    assert cluster.row(legacy)["owner_id"] == b, "an admin ends a legacy hold"
    verbs._stamp_owner(cluster.s, legacy, None)
    assert cluster.row(legacy)["owner_id"] is None
    free = cluster.phone()
    verbs._stamp_owner(cluster.s, free, a)
    row = cluster.row(free)
    assert row["owner_id"] == a and row["taken_at"] is None


@needs_cluster
def test_the_legacy_sweep_reads_and_writes_leave_station_holds_alone(cluster):
    a = cluster.user("a")
    old = "now() - interval '2 hours'"
    legacy = cluster.phone(owner=a, state="taken", live_url="https://v/l")
    hold = cluster.phone(owner=a, station=True, state="taken")
    cluster.sql(f"UPDATE phones SET state_at = {old}, watched_at = {old}"
                " WHERE serial = ANY(%s)", ([legacy, hold],))

    over = {r["serial"] for r in forgotten.overdue(cluster.s, 60, 45)}
    assert legacy in over and hold not in over

    assert forgotten._release(cluster.s, hold) is False
    row = cluster.row(hold)
    assert row["state"] == "taken" and row["owner_id"] == a
    assert forgotten._release(cluster.s, legacy) is True
    row = cluster.row(legacy)
    assert (row["state"], row["owner_id"], row["last_owner_id"]) == ("", None, a)
    assert row["watched_at"] is None and row["tab_closed_at"] is None
    assert row["live_url"] == ""
    assert forgotten._release(cluster.s, legacy) is False, "once"

    assert forgotten._still_legacy(cluster.s, legacy) is True
    assert forgotten._still_legacy(cluster.s, hold) is False
    assert forgotten._still_legacy(cluster.s, "90000000000") is False

    forgotten._mark_off(cluster.s, hold)
    assert cluster.row(hold)["running"] is False


@needs_cluster
def test_give_back_and_a_station_verdict_run_end_to_end_on_the_cluster(cluster):
    a, b = cluster.user("a"), cluster.user("b")
    hold = cluster.phone(owner=a, station=True, state="taken", purpose="spotify",
                         gmail="g@example.com", proxy_name="SX1")
    who = {"by": "sara", "by_id": a, "where": "station"}

    assert verbs.give_back(None, None, cluster.s, {"serial": hold, "by_id": b},
                           None) == ("refused",
                                     f"phone {hold} is not yours any more", None)
    status, said, detail = verbs.give_back(None, None, cluster.s,
                                           {"serial": hold, **who}, None)
    assert (status, said) == ("done", f"Phone {hold} is back on the Spotify "
                                      f"shelf.")
    row = cluster.row(hold)
    assert (row["state"], row["owner_id"], row["taken_at"]) == ("", None, None)
    assert (row["gmail"], row["proxy_name"]) == ("g@example.com", "SX1")
    off = cluster.sql("SELECT id, payload FROM actions WHERE verb ="
                      " 'power_off_phone' AND payload->>'serial' = %s", (hold,))
    assert [r["id"] for r in off] == [detail["off"]]
    assert off[0]["payload"]["why"] == "given back"
    assert verbs.give_back(None, None, cluster.s, {"serial": hold, **who},
                           None)[1] == f"phone {hold} is not yours any more"

    # A Boot still pending keeps the phone: in its words.
    busy = cluster.phone(owner=a, station=True, state="taken")
    cluster.sql("INSERT INTO actions (verb, payload, requested_by, status)"
                " VALUES ('boot_phone', %s, %s, 'running')",
                (json.dumps({"serial": busy}), a))
    assert verbs.give_back(None, None, cluster.s, {"serial": busy, **who},
                           None)[1] == f"phone {busy} is booting - wait for it"

    # A Station verdict: once, and only the holder's.
    mine = cluster.phone(owner=a, station=True, state="taken")
    press = {"serial": mine, "state": "failed", "button": "auth", "mine": True}
    assert verbs.set_phone_state(None, None, cluster.s,
                                 dict(press, by="ali", by_id=b), None) == (
        "refused", f"phone {mine} is not yours any more", None)
    status, said, detail = verbs.set_phone_state(None, None, cluster.s,
                                                 dict(press, **who), None)
    assert status == "done" and detail == {"state": "failed", "button": "auth"}
    assert verbs.set_phone_state(None, None, cluster.s, dict(press, **who),
                                 None) == (
        "refused", f"phone {mine} is already closed as failed", None)
    rows = cluster.sql("SELECT button, state, lane, by_id, pressed_on"
                       " FROM verdicts WHERE serial = %s", (mine,))
    assert rows == [{"button": "auth", "state": "failed", "lane": "gpt",
                     "by_id": a, "pressed_on": "station"}]
    row = cluster.row(mine)
    assert (row["state"], row["owner_id"], row["taken_at"]) == ("failed", None,
                                                                None)


def _press(cluster, verb, serial, *, status="queued", age=0, by=None):
    """A power press on the cluster, `age` seconds old."""
    return cluster.sql(
        "INSERT INTO actions (verb, payload, requested_by, status,"
        " requested_at, executed_at)"
        " VALUES (%s, %s, %s, %s, now() - %s * interval '1 second',"
        " CASE WHEN %s = 'running' THEN now() - %s * interval '1 second' END)"
        " RETURNING id",
        (verb, json.dumps({"serial": serial}), by, status, age, status,
         age))[0]["id"]


def _status(cluster, action_id):
    return cluster.sql("SELECT status, result FROM actions WHERE id = %s",
                       (action_id,))[0]


@needs_cluster
def test_a_power_press_closed_while_it_waited_in_its_batch_never_runs(
        cluster, monkeypatch):
    """keeper-2: freshness starts when a press really starts. A press the
    web closed as an orphan while it waited down a slow batch is skipped,
    and one still open is stamped fresh before it runs."""
    from geelark_farm import serve as serve_mod
    from geelark_farm.store.db import connect

    serial = cluster.phone()
    ran = []
    monkeypatch.setitem(serve_mod.ACTION_VERBS, "power_off_phone",
                        lambda *a: ran.append(a[3]) or ("done", "off", None))
    monkeypatch.setattr(serve_mod, "_event", lambda *a, **k: None)
    closed = _press(cluster, "power_off_phone", serial, status="running",
                    age=400)
    assert store_station.expire_power(cluster.s, serial) == 1
    with connect(cluster.s) as conn:
        assert serve_mod._run_action(
            cluster.s, conn, {"id": closed, "verb": "power_off_phone",
                              "payload": {"serial": serial},
                              "requested_by": None},
            book=None, ledger=None, client=None) == 1
    assert ran == [], "a press closed while it waited is not run"
    assert _status(cluster, closed)["result"].startswith("closed: no answer")

    waiting = _press(cluster, "power_off_phone", serial, status="running",
                     age=170)
    with connect(cluster.s) as conn:
        serve_mod._run_action(
            cluster.s, conn, {"id": waiting, "verb": "power_off_phone",
                              "payload": {"serial": serial},
                              "requested_by": None},
            book=None, ledger=None, client=None)
    assert ran == [{"serial": serial}]
    row = cluster.sql("SELECT status, executed_at > now() - interval '1 minute'"
                      " AS fresh FROM actions WHERE id = %s", (waiting,))[0]
    assert row == {"status": "done", "fresh": True}


@needs_cluster
def test_a_give_back_over_an_orphaned_press_still_switches_the_phone_off(
        cluster, monkeypatch):
    """keeper-1, store-3: a Boot a restart left `running` four minutes ago
    neither blocks the give-back nor swallows its power-off, and it is
    closed so it never runs after the phone is back."""
    a = cluster.user("a")
    who = {"by": "sara", "by_id": a, "where": "station"}
    hold = cluster.phone(owner=a, station=True, state="taken", running=True)
    orphan = _press(cluster, "boot_phone", hold, status="running", age=240,
                    by=a)
    status, said, detail = verbs.give_back(None, None, cluster.s,
                                           {"serial": hold, **who}, None)
    assert status == "done" and detail["off"] is not None
    assert _status(cluster, orphan)["status"] == "failed"
    off = cluster.sql("SELECT status FROM actions WHERE id = %s",
                      (detail["off"],))
    assert off == [{"status": "queued"}]

    # The hour's give-back, over the same kind of orphan.
    idle = cluster.phone(owner=a, station=True, state="taken", running=True)
    cluster.sql("UPDATE phones SET taken_at = now() - interval '2 hours'"
                " WHERE serial = %s", (idle,))
    orphan = _press(cluster, "change_proxy", idle, status="running", age=240,
                    by=a)
    forgotten._close_orphans(cluster.s, idle)
    back = store_station.give_back_idle(cluster.s, idle, 60, 45)
    assert back is not None and back["off_id"] is not None
    assert _status(cluster, orphan)["status"] == "failed"

    # A closed tab's power-off, queued once however many passes see it.
    closed = cluster.phone(owner=a, station=True, state="taken", running=True)
    cluster.sql("UPDATE phones SET tab_closed_at = now() - interval '1 minute'"
                " WHERE serial = %s", (closed,))
    _press(cluster, "boot_phone", closed, status="running", age=240, by=a)
    live = {closed: {"serialNo": closed, "id": "PH" + closed,
                     "status": phones_mod.RUNNING}}
    everyone = store_station.overdue
    monkeypatch.setattr(store_station, "overdue", lambda *a, **k: [
        r for r in everyone(*a, **k) if r["serial"] == closed])
    for _ in range(3):
        forgotten._station_sweep(cluster.s, live, 60, 45)
    offs = cluster.sql("SELECT status FROM actions WHERE verb = 'power_off_phone'"
                       " AND payload->>'serial' = %s", (closed,))
    assert offs == [{"status": "queued"}]


@needs_cluster
def test_a_boot_queued_before_a_give_back_is_refused_by_its_holder_check(
        cluster, monkeypatch):
    """D1: a Station Boot that runs after its phone was given back finds
    no hold and starts nothing."""
    a = cluster.user("a")
    hold = cluster.phone(owner=a, station=True, state="taken")
    book = _book(phone=hold, on="")
    started = []
    monkeypatch.setattr(phones_mod, "listing", lambda client, **k: [
        {"id": "PH" + hold, "serialNo": hold, "status": phones_mod.STOPPED}])
    monkeypatch.setattr(phones_mod, "start",
                        lambda *a, **k: started.append(1) or "https://v/1")
    assert store_station.give_back(cluster.s, serial=hold, owner_id=a,
                                   by="sara") is not None
    status, said, _ = verbs.boot_phone(book, None, cluster.s,
                                       {"serial": hold, "by": "sara",
                                        "by_id": a, "station": True}, object())
    assert (status, said) == ("refused", f"phone {hold} is not yours any more "
                                         f"- it went back to the shelf")
    assert started == []


@needs_cluster
def test_a_power_off_is_queued_beside_a_pending_boot_but_two_boots_are_not(
        cluster):
    """D1 (keeper-3, deploy-5): the one-press index covers Boot and Change
    IP only, so a Release's or a give-back's power-off is always queued,
    while a second Boot or Change IP on one phone is still refused."""
    from psycopg.errors import UniqueViolation

    from geelark_farm.store import actions as store_actions

    a = cluster.user("a")
    serial = cluster.phone(owner=a, state="taken")
    tag = uuid.uuid4().hex[:8]
    store_actions.enqueue(cluster.s, verb="boot_phone",
                          payload={"serial": serial}, requested_by=a,
                          idem_key=f"b1:{tag}")
    off = store_actions.enqueue(cluster.s, verb="power_off_phone",
                                payload={"serial": serial}, requested_by=a,
                                idem_key=f"o1:{tag}")
    assert off is not None
    store_actions.enqueue(cluster.s, verb="power_off_phone",
                          payload={"serial": serial}, requested_by=a,
                          idem_key=f"o2:{tag}")
    with pytest.raises(UniqueViolation):
        store_actions.enqueue(cluster.s, verb="change_proxy",
                              payload={"serial": serial}, requested_by=a,
                              idem_key=f"c1:{tag}")
