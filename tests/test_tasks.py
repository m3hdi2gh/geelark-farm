"""The jobs a phone does that are not builds.

Three things are worth holding here and nothing else is: that a task
says what it needs and never writes what it was told in confidence,
that a run ends the one way a phone job ends, and that the row says
whose fault it was - because the row is the only reason the table
exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from geelark_farm import failures, tasks
from geelark_farm.tasks import drive


# --------------------------------------------------------- the registry
def test_every_task_says_what_it_is_and_what_it_needs():
    assert tasks.names(), "no tasks"
    for spec in tasks.TASKS.values():
        assert spec.key == spec.key.lower().replace(" ", "_")
        assert spec.title and spec.summary
        assert spec.module(), f"{spec.key} has no module"
        assert hasattr(spec.module(), "run"), f"{spec.key} has no run()"
        for field in spec.inputs:
            assert field.label, f"{spec.key}.{field.name} has no label"


def test_a_task_names_everything_it_is_missing_at_once():
    """A runner that reports them one at a time makes somebody start a
    phone three times to learn three things."""
    spec = tasks.TaskSpec(
        key="x", title="X", summary="x", runs="geelark_farm.tasks.app_probe",
        inputs=(tasks.Field("a", "A"), tasks.Field("b", "B"),
                tasks.Field("c", "C", required=False)))
    with pytest.raises(ValueError) as caught:
        spec.check({})
    assert "a, b" in str(caught.value) and "c" not in str(caught.value)
    # And a field it does not have is refused rather than carried along.
    with pytest.raises(ValueError, match="takes no d"):
        spec.check({"a": "1", "b": "2", "d": "3"})
    assert spec.check({"a": "1", "b": "2"}) == {"a": "1", "b": "2"}


def test_a_secret_is_named_once_and_kept_out_of_everything():
    spec = tasks.TaskSpec(
        key="x", title="X", summary="x", runs="geelark_farm.tasks.app_probe",
        inputs=(tasks.Field("address", "Address"),
                tasks.Field("password", "Password", secret=True)))
    given = {"address": "user1@example.com", "password": "hunter2"}

    assert spec.public(given) == {"address": "user1@example.com"}
    assert spec.secret_values(given) == ("hunter2",)


def test_the_registry_is_a_leaf():
    """Importing it must not import three tasks' dependencies - the same
    rule `products` keeps, and the reason the module is resolved when it
    is asked for rather than at import."""
    import ast

    source = Path(tasks.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
    assert not [m for m in imported if m.startswith("geelark_farm")
                and m != "geelark_farm"], imported


# ---------------------------------------------------------- app_probe
class Phone:
    """A client that answers one screen and swallows the rest."""

    def __init__(self, page: str, installed: bool = True):
        self.page = page
        self.installed = installed
        self.cmds: list[str] = []

    def data(self, path, payload=None, **kwargs):
        cmd = str((payload or {}).get("cmd") or "")
        self.cmds.append(cmd)
        if cmd.startswith("pm list packages"):
            out = f"package:{cmd.rsplit(' ', 1)[-1]}" if self.installed else ""
            return {"status": True, "output": out}
        if cmd.startswith("cat "):
            return {"status": True, "output": self.page}
        return {"status": True, "output": ""}

    def post(self, path, payload=None, **kwargs):
        return {"code": 0, "data": self.data(path, payload, **kwargs)}


def page_of(*labels: str) -> str:
    nodes = "".join(f'<node text="{t}" bounds="[0,0][9,9]" clickable="true"/>'
                    for t in labels)
    return f"<hierarchy>{nodes}</hierarchy>"


def doing(client, inputs: dict, settings) -> drive.Doing:
    return drive.Doing(client=client, phone_id="P", settings=settings,
                       inputs=inputs, budget_seconds=20)


def test_the_probe_reports_which_screen_the_app_drew(make_settings):
    from geelark_farm.tasks import app_probe

    s = make_settings()
    got = app_probe.run(doing(Phone(page_of("New chat", "Settings")),
                              {"package": "com.openai.chatgpt"}, s))
    assert got.ok and got.reason == "signed_in"

    got = app_probe.run(doing(Phone(page_of("Log in", "Sign up")),
                              {"package": "com.openai.chatgpt"}, s))
    assert got.ok and got.reason == "signed_out"


def test_a_page_neither_list_knows_is_the_answer_not_a_failure(make_settings):
    """It is the thing this task exists to find, and the capture is what
    a new word is written from - so it is nobody's fault."""
    from geelark_farm.tasks import app_probe

    got = app_probe.run(doing(Phone(page_of("Something quite new")),
                              {"package": "com.x"}, make_settings()))
    assert got.reason == "unrecognised_screen"
    assert failures.verdict(got.reason).blame == failures.NOBODY


def test_the_device_is_asked_whether_the_app_is_there_at_all(make_settings):
    """One shell call, and much less than a budget spent watching a
    screen that will never be the app's."""
    from geelark_farm.tasks import app_probe

    client = Phone(page_of("New chat"), installed=False)
    got = app_probe.run(doing(client, {"package": "com.x"}, make_settings()))

    assert got.reason == "app_not_installed"
    assert failures.verdict(got.reason).blame == failures.DEVICE
    assert not [c for c in client.cmds if c.startswith("monkey")], \
        "it launched an app it had just been told is not there"


def test_the_words_can_be_given_for_one_run(make_settings):
    from geelark_farm.tasks import app_probe

    got = app_probe.run(doing(
        Phone(page_of("Your library", "Search")),
        {"package": "com.spotify.music", "signed_in_words": "your library"},
        make_settings()))
    assert got.reason == "signed_in"


# ------------------------------------------------------------ the run
def test_a_task_run_ends_the_way_every_phone_job_ends():
    """`kit/phone.py` owns the ending, and AGENTS.md forbids a third
    copy of it. This is the test that says so in code rather than in a
    comment."""
    import inspect

    source = inspect.getsource(drive.one)
    assert "PhoneRun(client, build)" in source
    assert "_ended_by(exc, run.finish" in source
    assert "_let_the_phone_go(client, settings, ledger, build, phone_id)" \
        in source
    # And in a `finally`, so no path out of here leaves a phone running.
    tree = __import__("ast").parse(source.lstrip())
    fn = next(n for n in __import__("ast").walk(tree)
              if isinstance(n, __import__("ast").FunctionDef))
    tries = [n for n in __import__("ast").walk(fn)
             if isinstance(n, __import__("ast").Try) and n.finalbody]
    assert tries, "no finally"
    last = "".join(__import__("ast").unparse(n) for n in tries[-1].finalbody)
    assert "_let_the_phone_go" in last and "_close_row" in last


def test_a_run_with_no_store_still_runs_and_records_nothing(
        make_settings, monkeypatch):
    """A playground has the store off. Running the task and writing no
    row is the right answer there, and must not be an error."""
    from geelark_farm import phones as phones_mod
    from geelark_farm.kit import phone as kit_phone

    s = make_settings(store_enabled=False)
    monkeypatch.setattr(phones_mod, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(kit_phone.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(kit_phone.cancel, "_stop_honoured", lambda *a, **k: None)

    class Nothing:
        def release(self, *a, **k):
            pass

    build = drive.one(s, tasks.spec("app_probe"),
                      {"package": "com.openai.chatgpt"}, phone_id="P",
                      client=Phone(page_of("New chat")), ledger=Nothing())
    assert build.ok and build.status == "signed_in"
    assert build.seconds >= 0


def test_the_row_carries_whose_fault_it_was(make_settings, monkeypatch):
    """The one reason the table exists: a rate says something is wrong
    and only the blame says what."""
    from geelark_farm import phones as phones_mod
    from geelark_farm.kit import phone as kit_phone
    from geelark_farm.store import task_runs

    s = make_settings(store_enabled=True)
    monkeypatch.setattr(phones_mod, "ensure_running", lambda *a, **k: None)
    monkeypatch.setattr(kit_phone.phones, "stop", lambda *a, **k: None)
    monkeypatch.setattr(kit_phone.cancel, "_stop_honoured", lambda *a, **k: None)
    monkeypatch.setattr(drive, "_open_row", lambda *a, **k: 7)
    closed = {}
    monkeypatch.setattr(task_runs, "finish",
                        lambda settings, run_id, **k: closed.update(
                            k, run_id=run_id))
    monkeypatch.setattr("geelark_farm.store.artifacts.put_dir",
                        lambda *a, **k: 0)

    class Nothing:
        def release(self, *a, **k):
            pass

    drive.one(s, tasks.spec("app_probe"), {"package": "com.x"},
              phone_id="P", client=Phone(page_of("Nothing known here")),
              ledger=Nothing())

    assert closed["run_id"] == 7
    assert closed["ok"] is False
    assert closed["reason"] == "unrecognised_screen"
    assert closed["blame"] == failures.NOBODY, closed
    assert closed["folder"].startswith("2") and "task-app_probe" in \
        closed["folder"]


# ---------------------------------------------------------- the table
class Cur:
    def __init__(self, rows=(), description=None):
        self._rows = list(rows)
        self.description = description

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class Conn:
    """Records the SQL, answers what it was told to."""

    def __init__(self, answers=()):
        self.sql: list[str] = []
        self.args: list = []
        self._answers = list(answers)
        self.committed = 0

    def execute(self, sql, params=()):
        self.sql.append(" ".join(sql.split()))
        self.args.append(params)
        return self._answers.pop(0) if self._answers else Cur()

    def commit(self):
        self.committed += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_row_is_opened_before_the_work_and_closed_after(
        make_settings, monkeypatch):
    """A run recorded only at the end leaves nothing at all when it is
    the end that fails."""
    from geelark_farm.store import task_runs

    conn = Conn([Cur([(41,)])])
    monkeypatch.setattr(task_runs, "connect", lambda s: conn)

    new_id = task_runs.start(make_settings(), task="app_probe",
                             inputs={"package": "com.x"}, phone_id="P")
    assert new_id == 41
    assert "INSERT INTO task_runs" in conn.sql[0]
    assert json.loads(conn.args[0][1]) == {"package": "com.x"}
    assert conn.committed == 1

    conn = Conn()
    monkeypatch.setattr(task_runs, "connect", lambda s: conn)
    task_runs.finish(make_settings(), 41, ok=True, reason="signed_in",
                     blame="", seconds=12.5, trail=["a", "b"])
    assert "UPDATE task_runs SET status = %s" in conn.sql[0]
    assert conn.args[0][0] == "done" and conn.args[0][1] is True
    assert "a > b" in conn.args[0]


def test_closing_a_row_never_raises_into_a_run(make_settings, monkeypatch):
    """The work is done; a row that could not be written is not a reason
    to undo it."""
    from geelark_farm.store import task_runs

    def boom(_settings):
        raise RuntimeError("the cluster went away")

    monkeypatch.setattr(task_runs, "connect", boom)
    task_runs.finish(make_settings(), 1, ok=True, reason="signed_in")


def test_the_tally_counts_the_blames_not_only_the_rate(
        make_settings, monkeypatch):
    from geelark_farm.store import task_runs

    conn = Conn([Cur([(10, 6, 41.0)]),
                 Cur([("captcha_shown", "credential", 3),
                      ("screen_unreadable", "device", 1)])])
    monkeypatch.setattr(task_runs, "connect", lambda s: conn)

    got = task_runs.tally(make_settings(), "app_probe", days=7)

    assert got["runs"] == 10 and got["worked"] == 6
    assert got["rate"] == 0.6 and got["median_seconds"] == 41.0
    assert got["blames"] == {"credential": 3, "device": 1}
    assert "make_interval" in conn.sql[0], "the window is a real one"


def test_the_schema_names_the_table_and_the_revision_moved():
    """The guard in test_store checks SCHEMA_REV against the file; this
    checks the table the runner writes to is actually in it."""
    from geelark_farm.store import db

    text = (Path(db.__file__).parent / "schema.sql").read_text(
        encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS task_runs" in text
    # The guard in test_store reads `-- rev N:`, so the header has to be
    # written that way - a section-shaped one left it reading rev 35.
    assert "-- rev 36: every run of a task" in text
    assert db.SCHEMA_REV == "36"
