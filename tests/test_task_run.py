"""The Run button: a task onto the builders' queue, and a builder
carrying it out.

What is worth holding here is what a press may NOT do. A task ends by
switching its phone off, so a phone somebody has taken, a phone a run
holds and a phone somebody is using by hand are refused - each before
anything is touched. A task's answer is never a build's verdict, so it
never reaches the breaker. And a task's phone is kept out of the
finish's hands for as long as the task is queued or running.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from geelark_farm import keeper, verbs
from geelark_farm import serve as serve_mod
from geelark_farm import tasks as registry
from geelark_farm.build_result import Build
from geelark_farm.store import jobs as store_jobs
from geelark_farm.web import pages, task_pages
from tests.test_builder import make_book

ADMIN = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
         "is_admin": True, "may_add_gmail": True, "may_change_proxy": True,
         "sees": "all", "username": "mehdi", "nav": {}}
OPERATOR = dict(ADMIN, id=2, role="operator", is_admin=False,
                username="ali")


@dataclass
class Held:
    label: str = "build 3"
    is_claimed: bool = True
    is_stale: bool = False


class FakeLedger:
    def __init__(self, held: dict | None = None):
        self.held = dict(held or {})
        self.claims: list[tuple[str, str]] = []
        self.released: list[str] = []

    def get(self, phone_id):
        return self.held.get(phone_id)

    def claim(self, phone_id, label=""):
        self.claims.append((phone_id, label))

    def release(self, phone_id, note=""):
        self.released.append(phone_id)


# ------------------------------------------------------------- the verb
@pytest.fixture
def farm(monkeypatch, make_settings):
    """A farm with a queue, one warm phone 1500, and GeeLark listing it."""
    from geelark_farm import phones as phones_mod

    book = make_book(proxies=1)
    book.phones.start(Serial="1500", Gmail="g0@example.com", Proxy="SX1",
                      Status="ready")
    monkeypatch.setattr(phones_mod, "listing", lambda client: [
        {"id": "P1500", "serialNo": "1500", "status": phones_mod.STOPPED}])
    queued = []
    monkeypatch.setattr(store_jobs, "open_serials", lambda s: set())
    monkeypatch.setattr(store_jobs, "open_count", lambda s, kind: 0)
    monkeypatch.setattr(store_jobs, "queue", lambda s, kind, payload=None,
                        action_id=None: queued.append(
                            (kind, payload, action_id)) or 77)
    settings = make_settings(store_enabled=True, build_queue=True)
    return SimpleNamespace(book=book, settings=settings, queued=queued)


def ask(farm, ledger=None, **payload):
    asked = {"task": "app_probe", "serial": "1500",
             "inputs": {"package": "com.openai.chatgpt"}, "by": "mehdi",
             "by_id": 1}
    asked.update(payload)
    return verbs.run_task(farm.book, ledger or FakeLedger(), farm.settings,
                          asked, object(), action_id=5)


def test_a_run_becomes_one_task_job_carrying_its_request(farm):
    status, said, detail = ask(farm)

    assert status == "running", said
    assert "queued" in said and "1500" in said
    (kind, payload, action_id), = farm.queued
    assert kind == "task" and action_id == 5
    assert payload["task"] == "app_probe"
    assert payload["inputs"] == {"package": "com.openai.chatgpt"}
    # Under `phone`, where `open_serials` reads a finish's: that is what
    # keeps the phone out of every other job's hands meanwhile.
    assert payload["phone"] == {"serial": "1500", "phone_id": "P1500"}
    assert detail["job"] == 77


@pytest.mark.parametrize("change, words", [
    ({"task": "nope"}, "no task called"),
    ({"inputs": {}}, "needs package"),
    ({"inputs": {"package": "x", "colour": "red"}}, "takes no colour"),
    ({"serial": ""}, "needs a phone"),
    ({"serial": "9999"}, "not in the Phones tab"),
])
def test_what_can_be_answered_without_the_phone_is_answered_in_words(
        farm, change, words):
    status, said, _ = ask(farm, **change)
    assert status in ("refused", "failed")
    assert words in said
    assert farm.queued == [], "nothing queued"


def test_a_taken_phone_is_refused_rather_than_switched_off(farm):
    """The task's ending stops the phone. Somebody holding it would have
    it switched off under them."""
    farm.book.phones.write("1500", State="taken")
    status, said, _ = ask(farm)
    assert status == "refused" and "taken" in said and "release" in said
    assert farm.queued == []


def test_a_phone_being_built_is_refused(farm):
    farm.book.phones.write("1500", Status=farm.book.phones.BUILDING)
    status, said, _ = ask(farm)
    assert status == "refused" and "worked on" in said


def test_a_phone_a_run_holds_is_refused(farm):
    status, said, _ = ask(farm, ledger=FakeLedger({"P1500": Held()}))
    assert status == "refused" and "build 3" in said
    assert farm.queued == []


def test_a_phone_with_a_job_on_its_way_is_refused(farm, monkeypatch):
    monkeypatch.setattr(store_jobs, "open_serials", lambda s: {"1500"})
    status, said, _ = ask(farm)
    assert status == "refused" and "already queued" in said


def test_one_task_at_a_time(farm, monkeypatch):
    """The farm's builds keep their share of GeeLark's two hundred calls
    a minute; the operator chose one at a time (2026-09-27)."""
    monkeypatch.setattr(store_jobs, "open_count",
                        lambda s, kind: verbs.TASKS_AT_ONCE if kind == "task"
                        else 0)
    status, said, _ = ask(farm)
    assert status == "refused" and "one at a time" in said
    assert farm.queued == []


def test_a_farm_without_a_queue_says_so(farm, make_settings):
    farm.settings = make_settings(store_enabled=True, build_queue=False)
    status, said, _ = ask(farm)
    assert status == "failed" and "no build queue" in said


def test_a_task_that_asks_for_a_secret_is_never_queued(farm, monkeypatch):
    """A queue is a table, and a table keeps what it is given."""
    secret = registry.TaskSpec(
        key="needs_key", title="t", summary="s", runs="x",
        inputs=(registry.Field("key", "Key", secret=True),))
    monkeypatch.setitem(registry.TASKS, "needs_key", secret)
    status, said, _ = ask(farm, task="needs_key", inputs={"key": "hunter2"})
    assert status == "refused" and "secret" in said
    assert "hunter2" not in said
    assert farm.queued == []


def test_the_verb_is_reachable_from_the_lane_and_given_its_row():
    assert serve_mod.ACTION_VERBS["run_task"] is verbs.run_task
    assert "run_task" in serve_mod.lane_verbs()
    assert getattr(verbs.run_task, "wants_action", False)


def test_the_drain_hands_a_verb_its_own_row_when_it_asks(monkeypatch,
                                                         make_settings):
    from geelark_farm.store import actions as store_actions

    seen = {}

    def verb(book, ledger, settings, payload, client, action_id=None):
        seen["action_id"] = action_id
        return "running", "queued", None

    verb.wants_action = True
    monkeypatch.setitem(serve_mod.ACTION_VERBS, "probe_verb", verb)
    monkeypatch.setattr(store_actions, "finish", lambda *a, **k: True)
    monkeypatch.setattr(serve_mod, "_event", lambda *a, **k: None)
    serve_mod._run_action(make_settings(), None,
                          {"id": 41, "verb": "probe_verb", "payload": {},
                           "requested_by": 1},
                          book=None, ledger=None, client=None)
    assert seen == {"action_id": 41}


# ------------------------------------------------------- the builder side
@pytest.fixture
def builder_side(monkeypatch, make_settings):
    from geelark_farm import phones as phones_mod
    from geelark_farm.tasks import drive

    finished, settled, driven = [], [], []
    monkeypatch.setattr(store_jobs, "finish",
                        lambda s, job_id, **k: finished.append((job_id, k)))
    monkeypatch.setattr(serve_mod, "_settle_task",
                        lambda s, action_id, key, serial, **k:
                        settled.append((action_id, key, serial, k)))
    monkeypatch.setattr(phones_mod, "status",
                        lambda client, pid: phones_mod.STOPPED)

    def one(settings, spec, given, **k):
        driven.append((spec.key, given, k))
        build = Build(index=0, phone_id=k["phone_id"], serial=k["serial"])
        build.ok, build.status, build.seconds = True, "signed_out", 19.0
        return build

    monkeypatch.setattr(drive, "one", one)
    return SimpleNamespace(settings=make_settings(), finished=finished,
                           settled=settled, driven=driven)


def a_job(**more):
    job = {"id": 9, "kind": "task", "action_id": 5,
           "payload": {"task": "app_probe", "by_id": 1,
                       "inputs": {"package": "com.openai.chatgpt"},
                       "phone": {"serial": "1500", "phone_id": "P1500"}}}
    job.update(more)
    return job


def test_a_builder_carries_a_task_out_and_answers_both_rows(builder_side):
    ledger = FakeLedger()
    serve_mod._carry_task(builder_side.settings, object(), ledger, a_job())

    assert ledger.claims == [("P1500", "task app_probe")]
    (key, given, k), = builder_side.driven
    assert key == "app_probe" and given == {"package": "com.openai.chatgpt"}
    assert k["serial"] == "1500" and k["job_id"] == 9 and k["by_id"] == 1
    (job_id, result), = builder_side.finished
    assert job_id == 9 and result["ok"] and result["status"] == "signed_out"
    (action_id, key, serial, said), = builder_side.settled
    assert (action_id, key, serial) == (5, "app_probe", "1500")
    assert said["status"] == "signed_out"


def test_a_phone_somebody_is_using_is_left_exactly_as_it_was(builder_side,
                                                            monkeypatch):
    """Up, with nobody's claim on it: a person's. Nothing is claimed,
    started or stopped - `run_finish` learned what the other answer
    costs (2026-08-29)."""
    from geelark_farm import phones as phones_mod

    monkeypatch.setattr(phones_mod, "status",
                        lambda client, pid: phones_mod.RUNNING)
    ledger = FakeLedger()
    serve_mod._carry_task(builder_side.settings, object(), ledger, a_job())

    assert builder_side.driven == [] and ledger.claims == []
    assert ledger.released == [], "nothing was let go, so nothing stopped"
    assert builder_side.finished[0][1]["status"] == "in_use_by_hand"


def test_a_phone_claimed_since_the_press_is_refused_at_the_take(builder_side):
    ledger = FakeLedger({"P1500": Held(label="finish 1500")})
    serve_mod._carry_task(builder_side.settings, object(), ledger, a_job())

    assert builder_side.driven == []
    assert builder_side.finished[0][1]["status"] == "phone_busy"
    assert "finish 1500" in builder_side.settled[0][3]["detail"]


def test_a_job_naming_no_task_is_answered_not_crashed(builder_side):
    job = a_job(payload={"task": "gone", "phone": {"serial": "1",
                                                   "phone_id": "P1"}})
    serve_mod._carry_task(builder_side.settings, object(), FakeLedger(), job)
    assert builder_side.finished[0][1]["status"] == "wish_not_understood"


def test_carry_out_hands_a_task_to_the_task_path_not_the_builder(
        builder_side, monkeypatch):
    from geelark_farm import builder

    monkeypatch.setattr(builder, "_run_jobs",
                        lambda *a, **k: pytest.fail("built a phone"))
    serve_mod._carry_out(builder_side.settings, object(), SimpleNamespace(),
                         FakeLedger(), a_job(), threading.Event())
    assert builder_side.driven, "the task ran"


def test_a_tasks_answer_never_reaches_the_breaker(monkeypatch, make_settings):
    """Eleven phones found signed out is eleven answers, not eleven
    failures of the farm's exits."""
    recorded, marked = [], []
    monkeypatch.setattr(store_jobs, "lose_stale", lambda s, older: [])
    monkeypatch.setattr(store_jobs, "unseen", lambda s: [
        {"id": 1, "kind": "task", "result": {"ok": False,
                                             "status": "unrecognised_screen"}},
        {"id": 2, "kind": "build", "result": {"ok": True,
                                              "status": "ready"}}])
    monkeypatch.setattr(store_jobs, "mark_seen",
                        lambda s, ids: marked.extend(ids))
    fuse = SimpleNamespace(reason=lambda: "",
                           record=lambda b: recorded.append(b.status))

    serve_mod._take_results(make_settings(), fuse)

    assert recorded == ["ready"]
    assert marked == [1, 2], "the task's result is still marked seen"


def test_pause_lets_a_task_through_and_stop_does_not():
    assert "task" in serve_mod.PAUSED_KINDS
    assert "build" not in serve_mod.PAUSED_KINDS


# ------------------------------------------ the phone, kept out of a finish
def test_a_phone_with_a_job_on_it_is_not_offered_to_a_finish(monkeypatch):
    """A task leaves a warm phone's row reading warm; `busy` is what keeps
    a finish from pairing an account with it meanwhile."""
    from geelark_farm import phones as phones_mod

    book = SimpleNamespace(phones=SimpleNamespace(
        unfinished=lambda **k: [{"serial": "1500"}, {"serial": "1501"}]))
    monkeypatch.setattr(phones_mod, "listing", lambda client: [])
    listing = [{"serialNo": "1500", "id": "P1500"},
               {"serialNo": "1501", "id": "P1501"}]

    waiting, _ = keeper._unfinished(object(), book, listing=listing,
                                    busy={"1500"})
    assert [p["serial"] for p in waiting] == ["1501"]

    waiting, _ = keeper._unfinished(object(), book, listing=listing)
    assert len(waiting) == 2, "nothing busy, nothing kept back"


def test_the_keepers_finishes_and_the_login_button_both_ask_what_is_busy():
    import inspect

    assert "busy=keeper._busy_serials(settings)" in inspect.getsource(
        serve_mod._order)
    assert "busy=keeper._busy_serials(settings)" in inspect.getsource(
        verbs.login_accounts)


# -------------------------------------------------------------- the page
def test_the_form_is_drawn_from_the_tasks_own_fields():
    """A new task gets a form nobody wrote."""
    spec = registry.spec("app_probe")
    drawn = task_pages._run_form(spec, ADMIN, [
        {"serial": "4435", "status": "ready", "state": "",
         "gmail": "g@example.com"}], serial="4435")

    assert 'action="/tasks/app_probe/run"' in drawn
    for field in spec.inputs:
        assert f'name="in_{field.name}"' in drawn
    assert drawn.count(" required") == 1 + len(spec.required()), \
        "the phone and each required field"
    assert '<option value="4435">ready · g@example.com</option>' in drawn
    assert 'value="4435"' in drawn, "?serial= fills the phone in"
    assert 'name="csrf"' in drawn


def test_a_serial_that_is_not_a_number_is_not_written_into_the_form():
    drawn = task_pages._run_form(registry.spec("app_probe"), ADMIN, [],
                                 serial='"><script>')
    assert "<script>" not in drawn


def test_a_secret_task_gets_no_form(monkeypatch):
    secret = registry.TaskSpec(
        key="needs_key", title="t", summary="s", runs="x",
        inputs=(registry.Field("key", "Key", secret=True),))
    drawn = task_pages._run_form(secret, ADMIN, [])
    assert "<form" not in drawn and "command line" in drawn


def test_only_an_admin_on_a_switched_on_console_gets_the_form(make_settings):
    on = make_settings(web_mutations=True)
    assert task_pages.may_run(ADMIN, on)
    assert not task_pages.may_run(OPERATOR, on)
    assert not task_pages.may_run(ADMIN, make_settings(web_mutations=False))

    data = {"spec": registry.spec("app_probe"), "rows": [], "tally": {}}
    assert "Run it" in task_pages.task_page(data, ADMIN, may_run=True)
    shown = task_pages.task_page(data, ADMIN, may_run=False)
    assert "Run it" not in shown and "geelark task app_probe" in shown


def test_every_banner_a_run_can_come_back_with_is_drawn():
    """The first table held (tone, words) pairs, and `_said` escapes what
    it is given - which a tuple is not. Never drawn, so never seen."""
    for word in ("queued", "already", "twice", "refused", "no"):
        drawn = pages._said(f"{word}:12", task_pages._TASK_SAID, ADMIN)
        assert task_pages._TASK_SAID[word] in drawn, word
    refused = pages._said("no:12", task_pages._TASK_SAID, ADMIN,
                          "phone 1500 is taken")
    assert 'class="said no' in refused and "1500 is taken" in refused


def test_the_requests_page_says_what_was_run_where():
    head, aside = pages.describe("run_task", {
        "task": "app_probe", "serial": "1500",
        "inputs": {"package": "com.openai.chatgpt"}})
    assert head == "Run app_probe on 1500"
    assert "package=com.openai.chatgpt" in aside


# ------------------------------------------------------------- the route
class _FakeHandler:
    """Just enough of `_Handler` for `_task_post` to be called unbound."""

    def __init__(self, path):
        self.path = path
        self.acted = None
        self.answered = None

    def _act(self, user, permission, verb, payload, *, idem, back):
        self.acted = (permission, verb, payload, back)

    def _minute_key(self, user, verb, target):
        return f"{verb}:{target}"

    def _html(self, code, body):
        self.answered = code


def test_the_run_post_passes_on_only_the_fields_the_task_declares():
    from geelark_farm.web import app

    fake = _FakeHandler("/tasks/app_probe/run")
    app._Handler._task_post(fake, ADMIN, {
        "serial": " 1500 ", "in_package": " com.openai.chatgpt ",
        "in_signed_in_words": "", "in_colour": "red", "csrf": "c"})

    permission, verb, payload, back = fake.acted
    assert (permission, verb, back) == ("admin", "run_task",
                                        "/tasks/app_probe")
    assert payload == {"task": "app_probe", "serial": "1500",
                       "inputs": {"package": "com.openai.chatgpt"}}


def test_the_run_post_for_a_task_that_does_not_exist_is_a_404():
    from geelark_farm.web import app

    fake = _FakeHandler("/tasks/nope/run")
    app._Handler._task_post(fake, ADMIN, {"serial": "1"})
    assert fake.answered == 404 and fake.acted is None


def test_the_run_post_never_forwards_a_secret(monkeypatch):
    from geelark_farm.web import app

    monkeypatch.setitem(registry.TASKS, "needs_key", registry.TaskSpec(
        key="needs_key", title="t", summary="s", runs="x",
        inputs=(registry.Field("key", "Key", secret=True),
                registry.Field("where", "Where"))))
    fake = _FakeHandler("/tasks/needs_key/run")
    app._Handler._task_post(fake, ADMIN, {"serial": "1", "in_key": "hunter2",
                                          "in_where": "x"})
    assert fake.acted[2]["inputs"] == {"where": "x"}


# ------------------------------------------------------------ the schema
def test_the_database_accepts_every_kind_a_builder_takes():
    """The first Run on the live farm was refused by `jobs_kind_check`:
    every test above fakes `store_jobs.queue`, so none of them ever met
    the table (2026-09-27). What the schema file leaves in force - the
    last definition of the constraint - must allow every kind the
    builders are told to take."""
    import re
    from pathlib import Path

    import geelark_farm.store as store_pkg

    schema = (Path(store_pkg.__file__).parent / "schema.sql").read_text(
        encoding="utf-8")
    # From `CREATE TABLE jobs` on: the pools' table has a `kind` too.
    at = schema.index("CREATE TABLE IF NOT EXISTS jobs")
    rules = [m.group(1) for m in re.finditer(
        r"(?:CONSTRAINT jobs_kind_check\s+CHECK|kind\s+text NOT NULL CHECK)"
        r"\s*\(kind IN \(([^)]*)\)\)", schema) if m.start() > at]
    allowed = set(re.findall(r"'(\w+)'", rules[-1]))
    assert set(serve_mod.HANDLED_KINDS) <= allowed, allowed
