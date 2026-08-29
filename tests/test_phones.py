"""The phone lifecycle: what one is called, and starting, stopping,
proxying and reaping it."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from geelark_farm import phones
from geelark_farm.ledger import STALE_CLAIM_SECONDS, Ledger
from geelark_farm.proxy import Proxy


def test_a_phone_is_named_by_its_serial_and_its_address():
    """The list read `farm-1786928959`, seven rows deep, differing in the last
    three digits of a unix timestamp - the second the phone was made, which is
    the one thing nobody wants to know (2026-08-17)."""
    assert (phones.display_name("832", "MerylQuinn162935@example.com")
            == "832 - MerylQuinn162935")


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
    name = phones.display_name(account="PiperHolt539742@example.com")
    assert name == "PiperHolt539742"


def test_a_phone_with_neither_is_left_for_the_caller_to_name():
    assert phones.display_name() == ""
    assert phones.display_name("", "") == ""


def test_the_name_is_ascii_so_geelark_stores_it_intact():
    """A name with a middle dot came back with a replacement character in it -
    `832 ? MerylQuinn162935` - measured against the live account."""
    name = phones.display_name("832", "MerylQuinn162935@example.com")

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
                  account="MerylQuinn162935@example.com")
    return client


def test_creating_a_phone_names_it_with_the_serial_geelark_answers_with(
        monkeypatch):
    client = make(monkeypatch)

    created = next(p for p in client.posts if p[0].endswith("addNew"))
    renamed = next(p for p in client.posts if "detail/update" in p[0])

    # Named before the serial exists, with the half that does.
    assert created[1]["data"][0]["profileName"] == "MerylQuinn162935"
    assert renamed[1] == {"id": "P1", "name": "832 - MerylQuinn162935"}


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

    # It gave up inside the settle, which is what `asked` twice proves: once
    # at the top of the loop, once by the settle itself. `sum(slept) < 30` was
    # here and asserted nothing - the settle never sleeps before its first
    # check, so it was reading `0 < 30` (2026-08-23).
    assert asked["n"] == 2
    assert slept == []


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


# ==================================================================
# The lifecycle itself (2026-08-24). Everything above this line is
# about naming, links, deletes and pagination; nothing in this file
# started, stopped, proxied or reaped a phone. `reapable` decides
# which running phones get stopped, and the only two tests that
# named it patched a fake over it and read its docstring.
# ==================================================================

class Panel:
    """GeeLark's side of the conversation, as far as these functions see it.

    Keyed by path rather than by call order: a test that knows the order
    breaks whenever an unrelated call is added, and the order is not what any
    of this is about.
    """

    def __init__(self, on_account=(), statuses=None):
        self.items = [dict(item) for item in on_account]
        self.statuses = dict(statuses or {})
        self.asked: list[tuple[str, dict]] = []
        self.polls = 0
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.refuse_status = None
        self.refuse_start = None

    def _state(self, phone_id):
        return self.statuses.get(phone_id, phones.STOPPED)

    def data(self, path, payload=None, **kwargs):
        payload = payload or {}
        self.asked.append((path, payload))
        if path == "/v1/phone/list":
            start = (payload["page"] - 1) * payload["pageSize"]
            page = self.items[start:start + payload["pageSize"]]
            # The listing carries each phone's state, and `reapable` reads it
            # from here rather than asking per phone - so it comes from the
            # same place `/phone/status` answers from.
            return {"items": [{**item, "status": self._state(item["id"])}
                              for item in page]}
        if path == "/v1/phone/status":
            phone_id = payload["ids"][0]
            self.polls += 1
            if self.refuse_status:
                return {"failDetails": [self.refuse_status]}
            return {"successDetails": [{"id": phone_id,
                                        "status": self._state(phone_id)}]}
        if path == "/v1/phone/start":
            phone_id = payload["ids"][0]
            if self.refuse_start:
                return {"failDetails": [self.refuse_start]}
            self.started.append(phone_id)
            self.statuses[phone_id] = phones.RUNNING
            return {"successDetails": [
                {"id": phone_id,
                 "url": "https://geelark.example/live?envName=832 - a@b.com",
                 "chargingMethod": "per minute"}]}
        raise AssertionError(f"nothing here answers {path}")

    def post(self, path, payload=None, **kwargs):
        payload = payload or {}
        self.asked.append((path, payload))
        if path == "/v1/phone/stop":
            for phone_id in payload["ids"]:
                self.stopped.append(phone_id)
                self.statuses[phone_id] = phones.STOPPED
        return {}


def running(*phone_ids):
    """A panel where these phones are up and spending money."""
    return Panel(on_account=[{"id": p} for p in phone_ids],
                 statuses={p: phones.RUNNING for p in phone_ids})


def ledger_at(tmp_path) -> Ledger:
    return Ledger(path=tmp_path / "ledger.json")


@pytest.fixture
def virtual_time(monkeypatch):
    """Sleeping advances a clock instead of a person.

    Every wait here polls with `time.sleep(POLL_SECONDS)` against a
    `time.monotonic` deadline of up to ten minutes. Left real, a loop that
    stops agreeing the phone came up blocks for the whole ten minutes rather
    than failing - so a broken check would hang CI instead of reporting, and
    a mutation run against this file cannot finish at all.

    Advancing the clock rather than skipping the sleep is the part that
    matters: a sleep that does nothing turns the same loop into a busy spin
    that never reaches its deadline either.
    """
    now = 0.0

    def monotonic():
        return now

    def sleep(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr(time, "monotonic", monotonic)
    monkeypatch.setattr(time, "sleep", sleep)


# ------------------------------------------------ which phones should be off
def test_a_running_phone_nothing_created_is_not_accounted_for(tmp_path):
    """The ledger is written the instant a phone exists, so a running phone
    with no entry was made outside this tool or the ledger was lost. Either
    way nothing here is answerable for it and it is billing."""
    panel = running("P1")

    verdicts = phones.reapable(panel, ledger_at(tmp_path))

    assert [p for p, _ in verdicts] == ["P1"]
    assert "not in the ledger" in dict(verdicts)["P1"]


def test_a_phone_its_run_has_finished_with_should_already_be_off(tmp_path):
    """`release` says the run is done. A phone still up after that is one
    whose stop did not happen, and this is the backstop."""
    ledger = ledger_at(tmp_path)
    ledger.record("P1")
    ledger.claim("P1")
    ledger.release("P1")

    verdicts = phones.reapable(running("P1"), ledger)

    assert "already released" in dict(verdicts)["P1"]


def test_a_claim_hours_old_means_the_process_that_made_it_is_gone(tmp_path):
    """Nothing legitimately holds a phone for hours, and it bills the whole
    time. The age goes in the reason because that is what tells the operator
    whether to believe it."""
    ledger = ledger_at(tmp_path)
    entry = ledger.record("P1")
    ledger.claim("P1")
    # Three hours, said outright. It used to be `STALE_CLAIM_SECONDS + 3600`,
    # which read as "past the window" but asserted on "3.0h" - true only while
    # the window happened to be two hours. Shortening the window to five
    # minutes made the age 1.1h and failed a test about the wording of the age
    # (2026-08-28). The age it reports and the window it is past are two
    # different facts, and only one of them is being checked here.
    entry.claimed_at = time.time() - 3 * 60 * 60
    assert entry.is_stale, "three hours must be past any sane window"

    reason = dict(phones.reapable(running("P1"), ledger))["P1"]

    assert "owner gone" in reason
    assert "3.0h ago" in reason


def test_a_phone_made_but_never_claimed_is_nobody_s(tmp_path):
    """`geelark create --start` makes a phone and deliberately does not claim
    it, which is exactly the case that used to bill unnoticed."""
    ledger = ledger_at(tmp_path)
    ledger.record("P1")

    reason = dict(phones.reapable(running("P1"), ledger))["P1"]

    assert "never claimed" in reason


def test_a_run_in_progress_is_left_alone(tmp_path):
    """The one case that must never be reaped. Stopping this phone kills a
    build halfway, and the run that owned it goes on driving a dead device."""
    ledger = ledger_at(tmp_path)
    ledger.record("P1")
    ledger.claim("P1")

    assert phones.reapable(running("P1"), ledger) == []


def test_a_phone_that_is_already_off_is_not_reaped(tmp_path):
    """It costs nothing, so there is nothing to decide - and reaping it would
    write a release over a ledger entry that is telling the truth."""
    panel = Panel(on_account=[{"id": "P1"}],
                  statuses={"P1": phones.STOPPED})

    assert phones.reapable(panel, ledger_at(tmp_path)) == []


def test_a_phone_still_booting_counts_as_one_that_is_spending(tmp_path):
    """`starting` bills the same as `running`. Reading only RUNNING here
    leaves an orphan that never finished booting up forever."""
    panel = Panel(on_account=[{"id": "P1"}],
                  statuses={"P1": phones.STARTING})

    assert [p for p, _ in phones.reapable(panel, ledger_at(tmp_path))] == ["P1"]


# ------------------------------------------------------- and stopping them
def test_a_dry_run_stops_nothing_but_still_reports_the_count(tmp_path):
    """What `--dry-run` is for: the operator sees the list before anything
    is acted on."""
    panel = running("P1", "P2")

    count = phones.reap(panel, ledger_at(tmp_path), dry_run=True)

    assert count == 2
    assert panel.stopped == []


def test_reaping_stops_the_phone_and_records_why(tmp_path):
    """The note is the only surviving explanation of why a phone went down,
    and `phones --ledger` is where it is read."""
    ledger = ledger_at(tmp_path)
    ledger.record("P1")
    panel = running("P1")

    assert phones.reap(panel, ledger) == 1

    assert panel.stopped == ["P1"]
    entry = ledger.get("P1")
    assert entry.released_at is not None
    assert "reaped: created but never claimed" == entry.note


def test_the_list_shown_is_the_list_acted_on(tmp_path):
    """A second lookup could disagree with the first, and the operator would
    have approved something other than what happened - so a caller that has
    already displayed its verdicts hands them back in."""
    panel = running("P1", "P2")
    approved = [("P1", "already released by its run")]

    count = phones.reap(panel, ledger_at(tmp_path), verdicts=approved)

    assert count == 1
    assert panel.stopped == ["P1"], "it looked again instead of using the list"


# --------------------------------------------------------- starting a phone
def test_a_phone_already_up_is_not_started_again(virtual_time):
    """Billing is per running minute and `start` is the call that begins
    spending. Returning None is how the caller knows it does not own the
    stop."""
    panel = running("P1")

    assert phones.ensure_running(panel, "P1", settle=0) is None
    assert panel.started == []


def test_starting_a_stopped_phone_hands_back_the_live_link(virtual_time):
    """The link is the fastest way to see what a flow is actually doing, and
    it comes back percent-encoded because every name this tool writes now has
    spaces in it."""
    panel = Panel(statuses={"P1": phones.STOPPED})

    url = phones.ensure_running(panel, "P1", settle=0)

    assert panel.started == ["P1"]
    assert url is not None
    assert " " not in url


def test_an_expired_phone_is_refused_before_anything_is_spent(virtual_time):
    """Expired is not a state starting fixes. Trying costs a call and leaves
    the caller believing it owns a phone that is not there."""
    panel = Panel(statuses={"P1": phones.EXPIRED})

    with pytest.raises(phones.PhoneError, match="expired"):
        phones.ensure_running(panel, "P1", settle=0)

    assert panel.started == []


def test_the_link_is_offered_before_the_boot_wait_not_after(virtual_time):
    """Without this the link surfaces a minute and a half later, by which
    time whatever you wanted to watch has already happened."""
    panel = Panel(statuses={"P1": phones.STOPPED})
    seen: list[str] = []

    polls_when_offered = []

    def on_url(url):
        seen.append(url)
        polls_when_offered.append(panel.polls)

    phones.ensure_running(panel, "P1", settle=0, on_url=on_url)

    assert len(seen) == 1
    # One poll had happened: the "is it already up?" check before starting.
    # The boot wait's own poll is what must still be ahead of it.
    assert polls_when_offered == [1]
    assert panel.polls == 2, "the boot wait never polled, so nothing was waited on"


def test_a_refused_start_is_not_reported_as_a_started_phone(tmp_path):
    """`/phone/start` answers code 0 at the envelope whatever happens inside
    it and puts refusals under failDetails - the shape that cost two phones
    on `/phone/delete`."""
    panel = Panel(statuses={"P1": phones.STOPPED})
    panel.refuse_start = {"code": 43001, "msg": "no free slot"}

    with pytest.raises(phones.PhoneError, match="43001"):
        phones.start(panel, "P1")


def test_a_refused_status_says_so_rather_than_reading_as_stopped(tmp_path):
    """A refusal that came back as None would read as "not running" to
    `ensure_running`, which would start a phone that is already up."""
    panel = running("P1")
    panel.refuse_status = {"code": 42001, "msg": "no such phone"}

    with pytest.raises(phones.PhoneError, match="42001"):
        phones.status(panel, "P1")


def test_a_phone_the_answer_does_not_mention_has_no_status(tmp_path):
    """Not an error and not a state: GeeLark simply did not say."""
    panel = Panel()
    panel.statuses = {}
    panel.data = lambda path, payload=None, **kw: {"successDetails": [
        {"id": "SOMEONE-ELSE", "status": phones.RUNNING}]}

    assert phones.status(panel, "P1") is None


# --------------------------------------------------- pointing it somewhere new
def test_changing_the_exit_sends_what_geelark_asks_for(tmp_path):
    """The call the whole retry story rests on. It was assumed for most of
    this project's life that a proxy was fixed at creation, which made a
    CAPTCHA a reason to delete the phone."""
    panel = Panel()

    phones.set_proxy(panel, "P1", Proxy("socks5", "1.2.3.4", 1080, "u", "pw"))

    path, payload = panel.asked[-1]
    assert path == "/v1/phone/detail/update"
    assert payload["id"] == "P1"
    assert payload["proxyConfig"] == {"typeId": 1, "server": "1.2.3.4",
                                      "port": 1080, "username": "u",
                                      "password": "pw"}


def test_each_scheme_is_sent_as_the_number_geelark_knows_it_by(tmp_path):
    """Sending the wrong id is not refused - GeeLark configures the phone for
    a protocol the proxy does not speak, and the failure surfaces later as a
    network problem on a phone that has already been paid for."""
    for scheme, type_id in (("socks5", 1), ("http", 2), ("https", 3)):
        panel = Panel()

        phones.set_proxy(panel, "P1", Proxy(scheme, "1.2.3.4", 1080, "u", "p"))

        assert panel.asked[-1][1]["proxyConfig"]["typeId"] == type_id


# ------------------------------------------------ forgetting what is gone
def test_a_phone_deleted_in_the_panel_stops_haunting_the_ledger(tmp_path):
    """Phones get deleted from GeeLark directly. Without this the ledger
    grows forever with entries for devices that are gone, which hides the
    ones that still matter."""
    ledger = ledger_at(tmp_path)
    ledger.record("P1")
    ledger.record("GONE")

    forgotten = phones.prune_ledger(running("P1"), ledger)

    assert forgotten == ["GONE"]
    assert ledger.get("GONE") is None
    assert ledger.get("P1") is not None


# ------------------------------------------------- what mutation found (2026-08-24)
def test_the_state_numbers_are_the_ones_geelark_sends():
    """Everything else here is written in terms of these names, so a test that
    uses the names moves with them and holds whatever they say. They are not
    ours to choose: they are the numbers `/phone/status` answers with, and
    getting one wrong misreads every phone on the account.
    """
    assert (phones.RUNNING, phones.STARTING,
            phones.STOPPED, phones.EXPIRED) == (0, 1, 2, 3)
    assert phones.STATUS_NAMES[0] == "running"
    assert phones.STATUS_NAMES[3] == "expired"


def test_stopping_a_phone_that_is_already_down_is_not_an_error():
    """"Never strict" is the whole contract of this function. Stopping is what
    cleanup does, and cleanup runs on paths where the phone may well be down
    already - a refusal raised from here would abort the tidy-up that was
    trying to make sure nothing is left billing.
    """
    class Strictly:
        """Refuses the way the real client does when strict is left on."""

        def post(self, path, payload=None, *, strict=True, **kwargs):
            if strict:
                raise AssertionError("stop asked for strict; it must not")
            return {"code": 45001, "msg": "phone is not running"}

    phones.stop(Strictly(), "P1")          # no raise


def test_a_phone_that_expires_while_booting_is_not_waited_out(virtual_time):
    """`ensure_running` checks for expiry before it starts anything, so that
    check is not this one. A phone can expire between the first look and the
    boot finishing, and without this the wait polls a dead device until the
    ten-minute deadline and then blames the timeout.
    """
    panel = Panel(statuses={"P1": phones.STARTING})
    answers = iter([phones.STARTING, phones.EXPIRED])

    real_data = panel.data

    def data(path, payload=None, **kwargs):
        if path == "/v1/phone/status":
            panel.statuses["P1"] = next(answers, phones.EXPIRED)
        return real_data(path, payload, **kwargs)

    panel.data = data

    with pytest.raises(phones.PhoneError, match="expired"):
        phones.wait_until_running(panel, "P1", settle=0)


def test_a_phone_still_booting_is_waited_for_rather_than_written_off(
        virtual_time):
    """The other half of the same check, and the half that catches reading it
    backwards: every boot passes through `starting`, so a rule that treats
    anything-but-expired as expired refuses every phone there is - while
    still raising the same "has expired" that the test above asks for.
    """
    panel = Panel(statuses={"P1": phones.STARTING})
    answers = iter([phones.STARTING, phones.STARTING, phones.RUNNING])
    real_data = panel.data

    def data(path, payload=None, **kwargs):
        if path == "/v1/phone/status":
            panel.statuses["P1"] = next(answers, phones.RUNNING)
        return real_data(path, payload, **kwargs)

    panel.data = data

    phones.wait_until_running(panel, "P1", settle=0)   # no raise

    assert panel.polls == 3, "it stopped polling before the phone was up"


class RefusingPanel:
    """Answers `addNew` the way GeeLark does when it will not make the phone:
    an envelope that succeeded, carrying a detail that did not."""

    def __init__(self, detail):
        self.detail = detail
        self.posts = []

    def data(self, path, payload=None, **kwargs):
        self.posts.append((path, payload))
        return {"details": [self.detail]}

    def post(self, path, payload=None, **kwargs):
        self.posts.append((path, payload))
        return {}


def test_a_refused_creation_is_not_recorded_as_a_phone():
    """The refusal still carries an id, so reading "code 0 OR an id" accepts
    it - and the caller is handed a ledger entry for a phone that was never
    made, which is the shape that cost two phones on `/phone/delete`
    (2026-08-17, 840 and 841).
    """
    from geelark_farm.config import Settings

    settings = Settings.__new__(Settings)
    for name, value in (("android", 1), ("region", "us"),
                        ("phone_name_prefix", "farm")):
        object.__setattr__(settings, name, value)

    client = RefusingPanel({"code": 45004, "id": "P1",
                            "msg": "the proxy did not answer"})
    ledger = FakeLedger()

    with pytest.raises(phones.PhoneError, match="creation failed"):
        phones.create(client, settings, Proxy("socks5", "1.2.3.4", 1080,
                                              "u", "p"),
                      ledger=ledger, account="a@example.com")

    assert ledger.recorded == [], "a phone that does not exist went in the ledger"


# --------------------------------- GeeLark running out of machines for a while
def capacity_refusal():
    return {"failDetails": [{"id": "P1", "code": phones.CAPACITY_REFUSED,
                             "msg": "High demand for Android 15 cloud phones. "
                                    "Please try again later or use another "
                                    "Android version."}]}


def test_a_capacity_refusal_is_asked_again_rather_than_raised_at_once(
        monkeypatch):
    """GeeLark runs out of machines of one Android version for minutes at a
    time and advises trying again. It is safe to: the refusal means no phone
    was started, so asking twice cannot start two (2026-08-28)."""
    asks = []

    def answer(path, payload=None, **kw):
        asks.append(path)
        return ({"successDetails": [{"url": "https://watch/me"}]}
                if len(asks) == 3 else capacity_refusal())

    monkeypatch.setattr(phones.time, "sleep", lambda *_: None)
    client = SimpleNamespace(data=answer)

    url = phones.start(client, "P1")

    assert len(asks) == 3          # refused, refused, then through
    assert url == "https://watch/me"


def test_it_gives_up_after_enough_tries_and_says_which_refusal_it_was(
        monkeypatch):
    """A shortage that outlasts the retries is still an answer, and the run
    that gets it must be able to tell it from a phone that will not boot."""
    monkeypatch.setattr(phones.time, "sleep", lambda *_: None)
    client = SimpleNamespace(data=lambda *a, **k: capacity_refusal())

    with pytest.raises(phones.PhoneCapacityError) as caught:
        phones.start(client, "P1", attempts=2)

    assert "43043" in str(caught.value)


def test_a_capacity_error_is_still_a_phone_error(monkeypatch):
    """Callers that only know the general case must keep catching it."""
    assert issubclass(phones.PhoneCapacityError, phones.PhoneError)


def test_every_other_refusal_is_raised_on_the_first_answer(monkeypatch):
    """Asking again about a phone that has been deleted only takes longer to
    say the same thing - and each ask is a live call."""
    asks = []

    def answer(path, payload=None, **kw):
        asks.append(path)
        return {"failDetails": [{"id": "P1", "code": 43005,
                                 "msg": "env not found"}]}

    monkeypatch.setattr(phones.time, "sleep", lambda *_: None)

    with pytest.raises(phones.PhoneError) as caught:
        phones.start(SimpleNamespace(data=answer), "P1")

    assert len(asks) == 1
    assert not isinstance(caught.value, phones.PhoneCapacityError)


def test_asking_zero_times_is_refused_rather_than_silently_doing_nothing():
    """Without the guard the loop never runs and the success path reads a
    `data` that was never fetched."""
    with pytest.raises(ValueError):
        phones.start(SimpleNamespace(data=lambda *a, **k: {}), "P1", attempts=0)


def test_it_asks_exactly_as_many_times_as_it_says(monkeypatch):
    """Each ask is a live call to GeeLark, and the number is the whole of what
    `attempts` promises. Off by one either way is a call nobody asked for or
    one the caller was counting on."""
    asks = []
    monkeypatch.setattr(phones.time, "sleep", lambda *_: None)

    def answer(path, payload=None, **kw):
        asks.append(path)
        return capacity_refusal()

    with pytest.raises(phones.PhoneCapacityError):
        phones.start(SimpleNamespace(data=answer), "P1", attempts=3)

    assert len(asks) == 3


def test_one_attempt_means_one_ask_and_no_retrying(monkeypatch):
    """`attempts=1` is the old behaviour, and a caller with its own budget
    may want exactly that. It must be allowed rather than refused as too few."""
    asks = []
    monkeypatch.setattr(phones.time, "sleep",
                        lambda *_: pytest.fail("waited with nothing to wait for"))

    def answer(path, payload=None, **kw):
        asks.append(path)
        return capacity_refusal()

    with pytest.raises(phones.PhoneCapacityError):
        phones.start(SimpleNamespace(data=answer), "P1", attempts=1)

    assert len(asks) == 1


# --------------------------------- a capacity refusal, whatever type it arrives as
class Refusing:
    """GeeLark answering `/v1/phone/start` with a refusal."""

    def __init__(self, code, times=99):
        self.code = code
        self.times = times
        self.asked = 0

    def data(self, path, payload=None, **kw):
        self.asked += 1
        if self.asked > self.times:
            return {}
        return {"failDetails": [{"code": self.code,
                                 "msg": "High demand for Android 15 cloud "
                                        "phones. Please try again later."}]}


@pytest.mark.parametrize("code", [43043, "43043"])
def test_a_capacity_refusal_is_named_however_the_code_is_typed(code, monkeypatch):
    """`43043 != "43043"` sent it down the branch for everything else: raised
    as a bare PhoneError on the first answer instead of being retried, and
    written into the sheet as `error` - which counts against the breaker -
    rather than `no_capacity`, which does not (2026-08-29)."""
    monkeypatch.setattr(phones.time, "sleep", lambda s: None)
    client = Refusing(code)

    with pytest.raises(phones.PhoneCapacityError):
        phones.start(client, "P1", attempts=2)

    assert client.asked == 2, "and it was retried rather than given up on"


def test_any_other_refusal_is_still_raised_at_once(monkeypatch):
    """Retrying a phone that has expired only takes longer to say so."""
    monkeypatch.setattr(phones.time, "sleep", lambda s: None)
    client = Refusing("40004")

    with pytest.raises(phones.PhoneError) as raised:
        phones.start(client, "P1", attempts=3)

    assert not isinstance(raised.value, phones.PhoneCapacityError)
    assert client.asked == 1


def test_a_capacity_refusal_that_clears_is_not_an_error_at_all(monkeypatch):
    """The refusal means no phone was started, so asking twice cannot start
    two - which is why it is safe to retry."""
    monkeypatch.setattr(phones.time, "sleep", lambda s: None)
    client = Refusing("43043", times=1)

    phones.start(client, "P1", attempts=3)

    assert client.asked == 2
