"""What a phone is called, in GeeLark's own list."""

from __future__ import annotations

import pytest

from geelark_farm import phones


def test_a_phone_is_named_by_its_serial_and_its_address():
    """The list read `farm-1786928959`, seven rows deep, differing in the last
    three digits of a unix timestamp - the second the phone was made, which is
    the one thing nobody wants to know (2026-08-17)."""
    assert (phones.display_name("832", "RapidStorm162935@gmail.com")
            == "832 - RapidStorm162935")


def test_the_serial_leads_because_everything_else_is_filed_under_it():
    """The Phones tab is addressed by serial, History records it, and a failed
    build's artifacts are named for it. Sorting by name sorts by serial."""
    serials = (830, 831, 832)
    names = [phones.display_name(s, "a@gmail.com") for s in serials]

    assert names == sorted(names)
    assert all(n.startswith(str(s))
               for n, s in zip(names, serials, strict=True))


def test_a_phone_with_no_serial_yet_is_named_by_its_address():
    """There is a moment before GeeLark answers with a serial, and the phone
    still has to be called something - the name it is created with is also
    what survives if the rename afterwards fails."""
    name = phones.display_name(account="TitanHunter539742@gmail.com")
    assert name == "TitanHunter539742"


def test_a_phone_with_neither_is_left_for_the_caller_to_name():
    assert phones.display_name() == ""
    assert phones.display_name("", "") == ""


def test_the_name_is_ascii_so_geelark_stores_it_intact():
    """A name with a middle dot came back with a replacement character in it -
    `832 ? RapidStorm162935` - measured against the live account."""
    name = phones.display_name("832", "RapidStorm162935@gmail.com")

    assert name.isascii()
    assert phones.NAME_SEPARATOR.isascii()


# ------------------------------------------------------- the rename on create
class FakeClient:
    def __init__(self, serial="832", fail_rename=False):
        self.serial, self.fail_rename = serial, fail_rename
        self.posts: list[tuple[str, dict]] = []

    def data(self, path, payload):
        self.posts.append((path, payload))
        return {"details": [{"code": 0, "id": "P1",
                             "envSerialNo": self.serial}]}

    def post(self, path, payload):
        if self.fail_rename:
            raise RuntimeError("rename refused")
        self.posts.append((path, payload))


class FakeLedger:
    def __init__(self):
        self.recorded = []

    def record(self, phone_id, **fields):
        self.recorded.append((phone_id, fields))
        return type("Entry", (), {"phone_id": phone_id,
                                  "serial": fields.get("serial")})()


def make(monkeypatch, **kwargs):
    from geelark_farm.config import Settings
    from geelark_farm.proxy import Proxy
    settings = Settings.__new__(Settings)
    object.__setattr__(settings, "android", 1)
    object.__setattr__(settings, "region", "us")
    object.__setattr__(settings, "phone_name_prefix", "farm")
    client = FakeClient(**kwargs)
    proxy = Proxy("socks5", "1.2.3.4", 1080, "u", "p")
    phones.create(client, settings, proxy, ledger=FakeLedger(),
                  account="RapidStorm162935@gmail.com")
    return client


def test_creating_a_phone_names_it_with_the_serial_geelark_answers_with(
        monkeypatch):
    client = make(monkeypatch)

    created = next(p for p in client.posts if p[0].endswith("addNew"))
    renamed = next(p for p in client.posts if "detail/update" in p[0])

    # Named before the serial exists, with the half that does.
    assert created[1]["data"][0]["profileName"] == "RapidStorm162935"
    assert renamed[1] == {"id": "P1", "name": "832 - RapidStorm162935"}


def test_a_rename_that_fails_does_not_lose_the_phone(monkeypatch):
    """The phone is made, recorded and usable. A list that reads a little
    worse is not a reason to throw that away."""
    client = make(monkeypatch, fail_rename=True)

    assert any(p[0].endswith("addNew") for p in client.posts)


# ------------------------------------------------------- the live-view link
LIVE_URL = ("https://phone.geelark.com/index.html?isApi=true&target=SG"
            "&id=633179652143186328&envName={name}&envNo=835&w=336"
            "&token=eyJhbGci.OiJIUzI1-NiIs_InR5&lang=en-US&center=true")


def test_a_name_with_spaces_does_not_cut_the_link_in_half():
    """GeeLark pastes the profile name into envName= without encoding it.
    Harmless while every name was farm-1786928959; with `835 - DuskFury738465`
    the terminal stopped selecting at the first space, so the link printed
    above a batch could only be copied in pieces (2026-08-17)."""
    url = phones.tidy_url(LIVE_URL.format(name="835 - DuskFury738465"))

    assert " " not in url
    assert "envName=835%20-%20DuskFury738465" in url


def test_the_signed_token_is_left_exactly_as_it_came():
    """Re-encoding the whole query would be the obvious fix and would risk the
    one value that must not change."""
    url = phones.tidy_url(LIVE_URL.format(name="835 - DuskFury738465"))

    assert "token=eyJhbGci.OiJIUzI1-NiIs_InR5" in url
    assert url.endswith("&lang=en-US&center=true")


def test_a_link_with_nothing_to_fix_is_returned_unchanged():
    plain = LIVE_URL.format(name="farm-1786928959")

    assert phones.tidy_url(plain) == plain
    assert phones.tidy_url("https://example.com/no-query") == \
        "https://example.com/no-query"


# ------------------------------------------------- a delete that did not happen
class DeleteClient:
    """Answers the way GeeLark does: `code: 0` whatever became of the phones,
    with the refusals underneath."""

    def __init__(self, refused=()):
        self.refused = {str(one) for one in refused}
        self.posts = []

    def post(self, path, payload=None):
        self.posts.append((path, payload))
        ids = (payload or {}).get("ids") or []
        fail = [{"code": 42001, "id": one, "msg": "env not found"}
                for one in ids if str(one) in self.refused]
        return {"code": 0, "msg": "success",
                "data": {"totalAmount": len(ids),
                         "successAmount": len(ids) - len(fail),
                         "failAmount": len(fail), "failDetails": fail}}


class ForgetfulLedger:
    def __init__(self):
        self.forgotten = []

    def forget(self, phone_id):
        self.forgotten.append(phone_id)


def test_a_refused_delete_is_not_reported_as_a_deletion():
    """`/phone/delete` returns `code: 0` at the envelope whatever happens to
    the phones inside it. Not reading failDetails meant two running phones
    were recorded as discarded, had their rows dropped and their exits freed,
    and are still in the panel with nothing in the sheet that knows about
    them (2026-08-17, phones 840 and 841)."""
    client = DeleteClient(refused=["P1"])
    ledger = ForgetfulLedger()

    with pytest.raises(phones.PhoneError) as caught:
        phones.delete(client, ["P1"], ledger=ledger)

    assert "42001" in str(caught.value)
    assert ledger.forgotten == []          # and it is still ours to account for


def test_a_delete_that_worked_is_forgotten_from_the_ledger():
    client = DeleteClient()
    ledger = ForgetfulLedger()

    phones.delete(client, ["P1"], ledger=ledger)

    assert ledger.forgotten == ["P1"]


def test_a_partial_delete_keeps_what_went_and_reports_what_did_not():
    """Whatever did go is forgotten first, so a partial delete leaves no
    ghosts on either side of the line."""
    client = DeleteClient(refused=["P2"])
    ledger = ForgetfulLedger()

    with pytest.raises(phones.PhoneError):
        phones.delete(client, ["P1", "P2"], ledger=ledger)

    assert ledger.forgotten == ["P1"]


def test_waiting_for_a_stop_gives_up_rather_than_blocking_a_run(monkeypatch):
    """`stop` posts the request and returns; GeeLark goes on reporting the
    phone as running while it shuts down, and refuses to delete one that is
    still up."""
    monkeypatch.setattr(phones, "status", lambda *a, **k: phones.RUNNING)
    monkeypatch.setattr(phones.time, "sleep", lambda *a: None)
    # `monotonic`, which is what the wait measures against - a deadline on a
    # clock something else can set is a deadline that moves.
    #
    # The clock stops rather than running out. `phones.time` is the `time`
    # module itself, so patching it patches it for the whole process, and
    # holding the last value keeps the test saying what it means - after this
    # much time, give up - rather than depending on how many times anything
    # happens to ask what time it is. It depended on exactly that while it
    # patched `time`: `logging` stamps each record with `time.time()` below
    # 3.13, took a fifth value the list did not have, and the test raised
    # StopIteration on both versions CI builds (2026-08-23).
    ticks = [0, 1, 2, 999]
    monkeypatch.setattr(phones.time, "monotonic",
                        lambda: ticks.pop(0) if len(ticks) > 1 else ticks[0])

    assert phones.wait_until_stopped(object(), "P1", timeout=60) is False


def test_a_phone_that_came_down_is_waited_for_no_longer(monkeypatch):
    monkeypatch.setattr(phones, "status", lambda *a, **k: phones.STOPPED)

    assert phones.wait_until_stopped(object(), "P1") is True


# ------------------------------------------------- every phone, not the first 100
class PagedClient:
    """A phone list long enough to need more than one page."""

    def __init__(self, count):
        self.count = count
        self.pages_asked = []

    def data(self, path, payload=None, **kwargs):
        page = payload["page"]
        size = payload["pageSize"]
        self.pages_asked.append(page)
        start = (page - 1) * size
        return {"items": [{"id": f"P{i}"}
                          for i in range(start, min(start + size, self.count))]}


def test_a_phone_past_the_first_page_still_exists():
    """Twenty callers ask this what exists, including the sync that decides
    which rows have lost their phone. Past a hundred, the rest would read as
    stranded and be settled while the phones stayed up and billing."""
    client = PagedClient(250)

    items = phones.listing(client, page_size=100)

    assert len(items) == 250
    assert client.pages_asked == [1, 2, 3]


def test_a_short_page_ends_the_listing():
    """One call when there is one page of phones, which is every day."""
    client = PagedClient(7)

    assert len(phones.listing(client, page_size=100)) == 7
    assert client.pages_asked == [1]


def test_an_exactly_full_page_asks_once_more():
    """The only way to know a full page was the last one is to ask."""
    client = PagedClient(100)

    assert len(phones.listing(client, page_size=100)) == 100
    assert client.pages_asked == [1, 2]


def test_the_settle_wait_answers_an_interrupt(monkeypatch):
    """The loop around it checked `cancelled` and this did not, so a run being
    shut down still owed every worker up to half a minute in a sleep nothing
    could reach.

    Cancelled part-way through rather than at the start, because the outer
    guard catches that case on its own and would pass either way.
    """
    monkeypatch.setattr(phones, "status", lambda *a, **k: phones.RUNNING)
    slept = []
    monkeypatch.setattr(phones.time, "sleep", lambda s: slept.append(s))

    # False while the phone is found running, True once the settle is under
    # way - which is where an interrupt actually lands.
    asked = {"n": 0}

    def cancelled() -> bool:
        asked["n"] += 1
        return asked["n"] > 1

    with pytest.raises(phones.PhoneError, match="shutting down"):
        phones.wait_until_running(object(), "P1", settle=30,
                                  cancelled=cancelled)

    # It gave up inside the settle instead of sleeping the whole of it.
    assert sum(slept) < 30


def test_the_settle_still_settles_when_nothing_is_cancelling(monkeypatch):
    monkeypatch.setattr(phones, "status", lambda *a, **k: phones.RUNNING)
    slept = []
    monkeypatch.setattr(phones.time, "sleep", lambda s: slept.append(s))

    phones.wait_until_running(object(), "P1", settle=30)

    assert sum(slept) == 30


def test_the_two_functions_nothing_called_are_gone():
    """`info` and `serial_of` walked the whole phone list to answer about one
    phone, and no caller ever asked. The only references left were test
    patches for a function the code under test never reached (2026-08-23)."""
    assert not hasattr(phones, "info")
    assert not hasattr(phones, "serial_of")


def test_the_reasons_to_reap_are_counted_the_way_they_are_written():
    """The docstring said three and the code had four - and the fourth is the
    case `geelark create --start` used to hide from."""
    doc = phones.reapable.__doc__ or ""
    # Counted off the stripped lines: 3.13 dedents a docstring and the
    # versions CI builds do not, so a pattern with leading spaces in it would
    # pass on one and fail on the other - which is how this suite went red for
    # three weeks once already.
    listed = sum(1 for line in doc.splitlines()
                 if line.strip().startswith("- "))

    assert "Four cases say no" in doc
    assert listed == 4
