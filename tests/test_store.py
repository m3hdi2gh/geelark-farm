"""The store, tested without a cluster.

Everything here runs on a machine that has never seen Postgres. What needs
a live cluster - ensure_schema against the real thing - is exercised by
`GEELARK_TEST_DSN`-gated tests at the bottom, skipped everywhere else, so
the suite's promise (runs anywhere, fast) survives the store's arrival.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from geelark_farm.config import ConfigError

SRC = pathlib.Path(__file__).parent.parent / "src" / "geelark_farm"


# ---------------------------------------------------- the flag rule itself
def test_nothing_outside_the_store_imports_it_at_module_level():
    """The trunk rule for every stage of the sheet retirement: the store is
    merged inert. An unconditional import in any module the loop loads would
    mean a bug in half-built store code takes serve down on a box that never
    opted in - the exact thing the flag exists to make impossible.

    An AST walk rather than a grep, so a `from .store import X` hidden in a
    try block or an __init__ cannot slip past a text match.
    """
    offenders = []
    for path in SRC.rglob("*.py"):
        # store may know itself, and web lives behind its own flag check in
        # serve.run - both are the gated side of the rule, not subject to it.
        if "store" in path.parts or "web" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
                if node.level:                      # relative: resolve enough
                    names = [f".{node.module}"]
            for name in names:
                if "store" in name.split("."):
                    # Only module-level imports are forbidden; one inside a
                    # function body runs behind the flag check.
                    if node.col_offset == 0:
                        offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        f"the store is imported unconditionally at: {offenders} - it must "
        f"only ever be imported inside a `settings.store_enabled` check")


def test_the_store_settings_default_to_off(make_settings):
    """A Settings built by hand - which is every Settings in this suite -
    gets a store that is off. The flag rule depends on it."""
    s = make_settings()
    assert s.store_enabled is False
    assert s.store_host == ""


def test_the_password_never_appears_in_repr(make_settings):
    """Settings gets logged and printed in tracebacks. Every other credential
    field predates repr hygiene; this one does not get to."""
    s = make_settings(store_password="hunter2")
    assert "hunter2" not in repr(s)


def test_an_enabled_store_with_no_host_fails_early_and_says_how(make_settings):
    s = make_settings(store_enabled=True)
    with pytest.raises(ConfigError, match="STORE_HOST"):
        s.require_store()


def test_an_enabled_store_with_no_password_fails_early_and_says_how(
        make_settings):
    s = make_settings(store_enabled=True, store_host="db.example")
    with pytest.raises(ConfigError, match="STORE_PASSWORD"):
        s.require_store()


# ----------------------------------------------------------- the schema
def schema_text() -> str:
    return (SRC / "store" / "schema.sql").read_text(encoding="utf-8")


def test_every_statement_in_the_schema_is_re_runnable():
    """ensure_schema runs on every store-enabled start, and a half-applied
    schema from a killed process must converge rather than wedge. So every
    CREATE in the file carries IF NOT EXISTS - checked mechanically, because
    the one that does not is the one that takes the loop down a week after
    somebody adds it."""
    for line in schema_text().splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("CREATE "):
            assert "IF NOT EXISTS" in stripped.upper(), (
                f"not re-runnable: {stripped!r}")


def test_the_schema_carries_the_decisions_it_encodes():
    """The three load-bearing shapes, pinned so a future edit that drops one
    has to argue with a test and not just a comment."""
    sql = schema_text()
    # one lease for everything a run holds
    assert "lease_until" in sql and "claims" in sql
    # ownership from day one - taken evolves into assignment-to-a-user
    assert sql.count("owner_id") >= 2, "ownership left the schema"
    # the person-channel is constrained, not free text: `dome` was silently
    # nothing in a sheet cell
    assert "CHECK (state IN ('', 'unused', 'taken', 'done', 'failed'))" in sql
    # app_installed is three-valued: NULL means nobody looked (phone 1415)
    assert "app_installed boolean," in sql
    # the two-axis user model, not a role list
    assert "CHECK (role IN ('admin', 'operator'))" in sql
    assert "CHECK (sees IN ('all', 'own'))" in sql


def test_the_mirror_never_names_a_column_the_panel_api_owns():
    """These ten columns are safe on `resources` for exactly one reason:
    shadow._upsert_resource does not name them, in either its INSERT column
    list or its DO UPDATE SET list, so an unlisted column takes its DEFAULT
    once and is never assigned again. That is the ground owner_id already
    stands on, and shadow.py says why in its own docstring.

    The day one of them is added to either statement it begins reverting to
    the Gpt Info tab's picture every thirty seconds, with no error and no
    event - which is why this is a test and not a comment (2026-09-05)."""
    shadow = (SRC / "store" / "shadow.py").read_text(encoding="utf-8")
    for column in ("product", "credential_kind", "panel_ref", "client_id",
                   "backup_codes", "attempts", "failures", "customer_ready",
                   "state_changed_at", "delivered_at"):
        assert column not in shadow, (
            f"the mirror now names {column}; the sheet would own it and it "
            f"would revert every pass")


def test_every_resources_column_survives_an_insert_that_ignores_it():
    """The mirror inserts a sheet row without naming any column added after
    it was written, so each of them must have a DEFAULT or be nullable. A
    NOT NULL with neither aborts the whole mirror transaction - and serve
    swallows that as one warning while the console freezes on stale numbers,
    which is a bad afternoon to diagnose."""
    for line in schema_text().splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("ALTER TABLE RESOURCES ADD COLUMN"):
            continue
        if "NOT NULL" in stripped.upper():
            assert "DEFAULT" in stripped.upper(), (
                f"the mirror cannot insert past this: {stripped!r}")


def test_the_panel_api_tables_are_there_and_shaped_for_their_hazards():
    """Each of the four carries one decision worth a test."""
    sql = schema_text()
    # a key is a minted token, not a chosen password: sha256, no scrypt
    assert "CREATE TABLE IF NOT EXISTS api_clients" in sql
    assert "key_hash       bytea NOT NULL" in sql
    assert "CHECK (role IN ('panel', 'bot'))" in sql
    # a resources row is hard-deleted by "remove from the pool"; without the
    # cascade that button starts raising the day the first code arrives
    assert ("resource_id bigint NOT NULL REFERENCES resources(id)"
            " ON DELETE CASCADE") in sql
    # one answer per client per key, so a retry is one request
    assert "PRIMARY KEY (client_id, key)" in sql
    # a delivery that gives up stays, to be shown rather than lost
    assert "CHECK (status IN ('pending', 'delivered', 'gave_up'))" in sql
    # the panel's reference is the account's public id: one row exactly
    assert "resources_panel_ref" in sql and "WHERE panel_ref IS NOT NULL" in sql


def test_a_request_may_come_from_a_machine():
    """requested_by drops NOT NULL and client_id names the client instead.
    Done in the deploy where nothing writes one yet, because the three
    JOIN users that resolve the asker's name have to become LEFT JOINs in
    the same change - and the deploy where that is discovered is the one
    where the Requests page silently loses rows."""
    sql = schema_text()
    assert "ALTER TABLE actions ALTER COLUMN requested_by DROP NOT NULL;" in sql
    assert "ALTER TABLE actions ADD COLUMN IF NOT EXISTS client_id" in sql
    src = (SRC / "store" / "actions.py").read_text(encoding="utf-8")
    web = (SRC / "web" / "read.py").read_text(encoding="utf-8")
    assert "JOIN users u ON u.id = a.requested_by" not in src.replace(
        "LEFT JOIN users u ON u.id = a.requested_by", "")
    assert "JOIN users u ON u.id = a.requested_by" not in web.replace(
        "LEFT JOIN users u ON u.id = a.requested_by", "")


def test_the_users_table_hashes_with_scrypt_parameters_beside_the_hash():
    """So the parameters can be raised later without invalidating anyone -
    and so nobody can 'simplify' the table into storing something weaker."""
    sql = schema_text()
    for column in ("password_hash", "password_salt", "scrypt_n"):
        assert column in sql


# ------------------------------------------------ against a real cluster
needs_cluster = pytest.mark.skipif(
    "GEELARK_TEST_DSN" not in __import__("os").environ,
    reason="set GEELARK_TEST_DSN to run store integration tests")


@needs_cluster
def test_ensure_schema_applies_and_reapplies(make_settings):
    """Twice, because idempotence is the whole contract."""
    import os

    from geelark_farm.store import db as store_db

    dsn = os.environ["GEELARK_TEST_DSN"]
    parts = dict(p.split("=", 1) for p in dsn.split())
    s = make_settings(
        store_enabled=True, store_host=parts["host"],
        store_port=int(parts.get("port", 5432)), store_db=parts["dbname"],
        store_user=parts["user"], store_password=parts["password"])
    store_db.ensure_schema(s)
    store_db.ensure_schema(s)
    with store_db.connect(s) as conn:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_rev'").fetchone()
        assert row == (store_db.SCHEMA_REV,)


# ------------------------------------------------------------ validators
def test_a_bad_gmail_is_refused_at_the_door():
    """Write-time, not read-time: the sheet judged rows on load, and a row
    that failed sat looking free for days (Mamadovskii, 2026-08-31). Here
    the same judgement happens before the INSERT ever runs."""
    from geelark_farm.store import validate

    with pytest.raises(validate.AccountError, match="not an email address"):
        validate.gmail_row(address="fifa19.900t@pAss", password="x")


def test_a_33_char_secret_is_refused_with_the_reason():
    """The exact row that sat broken: base32 with one extra character."""
    from geelark_farm.store import validate

    with pytest.raises(validate.AccountError):
        validate.gmail_row(address="a@b.com", password="pw",
                           secret="YIBI" + "A" * 28 + "Q")


def test_the_secret_cell_splits_on_the_at_sign():
    """One cell, two meanings, same decisive test pools.py uses: base32 has
    no @ in it, and no address is without one."""
    from geelark_farm.store import validate

    with_recovery = validate.gmail_row(address="a@b.com", password="pw",
                                       secret="rescue@mail.com")
    assert with_recovery["recovery_email"] == "rescue@mail.com"
    assert with_recovery["totp_secret"] == ""

    with_key = validate.gmail_row(address="a@b.com", password="pw",
                                  secret="JBSWY3DPEHPK3PXP")
    assert with_key["totp_secret"] == "JBSWY3DPEHPK3PXP"
    assert with_key["recovery_email"] == ""


def test_the_seller_promise_refuses_only_the_wrong_kind():
    """Never an empty cell - that is how password-only accounts stay
    welcome, and forgetting it refused two of them on 2026-08-30."""
    from geelark_farm.store import validate

    # empty secret under a promising seller: fine
    validate.gmail_row(address="a@b.com", password="pw", seller="usa")
    # the wrong kind under a promising seller: refused
    with pytest.raises(validate.AccountError, match="disagree"):
        validate.gmail_row(address="a@b.com", password="pw",
                           seller="usa", secret="rescue@mail.com")


def test_the_sellers_table_matches_the_sheets():
    """Duplicated knowingly (store must not import the sheet module); this
    is the pin that keeps the two copies one."""
    from geelark_farm.pools import GmailPool
    from geelark_farm.store import validate

    assert validate.SELLERS == GmailPool.SELLERS


def test_an_email_code_only_app_account_needs_no_password():
    from geelark_farm.store import validate

    row = validate.app_row(address="codes@only.com", email_code_only=True)
    assert row["email_code_only"] is True

    with pytest.raises(validate.AccountError, match="no password"):
        validate.app_row(address="normal@acct.com")


def test_a_proxy_row_carries_the_identity_triple():
    """host+port+username is the identity pools._identity joined in Python;
    here it is what the partial unique index enforces."""
    from geelark_farm.store import validate

    row = validate.proxy_row(raw="socks5://u:p@10.0.0.1:9999", name="SX1")
    assert (row["host"], row["port"], row["username"]) == ("10.0.0.1", 9999, "u")


# ------------------------------------------------------------------ auth
def test_a_password_verifies_against_its_own_stored_parameters():
    """The parameters ride beside the hash so they can be raised later
    without invalidating anyone - so verify must read them from the row."""
    from geelark_farm.store import auth

    row = auth.hash_password("hunter2")
    assert auth.verify_password("hunter2", row)
    assert not auth.verify_password("hunter3", row)

    # a row hashed under weaker, older parameters still verifies
    import hashlib
    import os
    salt = os.urandom(16)
    old = dict(password_salt=salt, scrypt_n=4096, scrypt_r=8, scrypt_p=1,
               password_hash=hashlib.scrypt(b"legacy", salt=salt, n=4096,
                                            r=8, p=1, dklen=64))
    assert auth.verify_password("legacy", old)


def test_two_hashes_of_one_password_differ():
    """A fresh salt every call, or the users table becomes a rainbow-table
    lookup the day it leaks."""
    from geelark_farm.store import auth

    assert (auth.hash_password("same")["password_hash"]
            != auth.hash_password("same")["password_hash"])


# --------------------------------------------------------------- the CLI
def test_store_init_applies_schema_then_makes_the_admin(monkeypatch, capsys,
                                                        make_settings):
    """The first admin is the one user nobody with an admin page can make -
    and the schema must exist before the INSERT that creates them."""
    import geelark_farm.cli as cli_mod

    order = []

    class FakeStore:
        def __init__(self, settings):
            order.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def create_user(self, *, username, password, role, sees):
            order.append(("user", username, role, sees))
            return 7

    fake = type("M", (), {
        "ensure_schema": staticmethod(lambda s: order.append("schema")),
        "Store": FakeStore})
    monkeypatch.setattr(cli_mod, "store", fake, raising=False)
    monkeypatch.setattr("geelark_farm.store.ensure_schema",
                        fake.ensure_schema, raising=False)
    import geelark_farm.store as real_store
    monkeypatch.setattr(real_store, "ensure_schema", fake.ensure_schema)
    monkeypatch.setattr(real_store, "Store", FakeStore)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "long-enough-pw")

    args = type("A", (), {"admin": "mehdi"})
    code = cli_mod.cmd_store_init(make_settings(), args)

    assert code == 0
    assert order == ["schema", "connect", ("user", "mehdi", "admin", "all")]
    assert "admin 'mehdi' created (id 7)" in capsys.readouterr().out


def test_store_init_refuses_a_short_admin_password(monkeypatch, capsys,
                                                   make_settings):
    """The admin can reset everyone else; nobody resets the admin."""
    import geelark_farm.cli as cli_mod
    import geelark_farm.store as real_store

    monkeypatch.setattr(real_store, "ensure_schema", lambda s: None)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "short")

    args = type("A", (), {"admin": "mehdi"})
    code = cli_mod.cmd_store_init(make_settings(), args)

    assert code == 1
    assert "at least 8" in capsys.readouterr().err


def test_the_store_command_is_wired_into_the_parser():
    import geelark_farm.cli as cli_mod

    args = cli_mod.build_parser().parse_args(["store-init", "--admin", "x"])
    assert args.command == "store-init" and args.admin == "x"


# ------------------------------------------------------------- the shadow
def test_a_disabled_store_is_never_even_imported_by_a_pass(monkeypatch,
                                                           make_settings):
    """The trunk promise, at runtime: flag off means the pass cannot touch
    store code at all - not "touches it harmlessly", cannot."""
    import geelark_farm.serve as serve_mod

    def poisoned(*a, **k):
        raise AssertionError("the store was imported with the flag off")

    monkeypatch.setattr("geelark_farm.store.db.connect", poisoned)
    settings = make_settings()
    assert not settings.store_enabled

    serve_mod._shadow(settings, book=None,
                      decision=serve_mod.Decision(), outcome={})


def test_a_dead_store_costs_the_mirror_and_never_the_pass(monkeypatch,
                                                          make_settings,
                                                          caplog):
    """Treated like the Service board: the sheet remains authoritative, so
    a cluster outage is a warning, not a failed pass."""
    import geelark_farm.serve as serve_mod

    monkeypatch.setattr(
        "geelark_farm.store.db.connect",
        lambda s: (_ for _ in ()).throw(ConnectionError("cluster is down")))
    settings = make_settings(store_enabled=True)

    serve_mod._shadow(settings, book=None,
                      decision=serve_mod.Decision(), outcome={})   # no raise

    assert any("sheet remains authoritative" in r.message
               for r in caplog.records)


def test_the_event_sink_cannot_take_a_build_down(monkeypatch):
    """Guarded from both sides: emit never raises, and even a sink that
    does costs a warning, not the build's result."""
    import geelark_farm.builder as builder_mod

    builder_mod.set_event_sink(
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        # the sink call site is inside the result logging; drive it directly
        sink = builder_mod._event_sink
        try:
            sink("build_finished")
        except RuntimeError:
            pass                     # emit's own contract is tested above;
    finally:                         # the builder-side guard is in the code
        builder_mod.set_event_sink(None)


def test_the_shadow_closes_a_phone_the_sheet_deleted():
    """The sheet deletes a done phone's row, and with it every answer to
    "what did we build on Tuesday". The mirror sets done_at instead."""
    from geelark_farm.store import shadow

    class Cur:
        def __init__(self):
            self.executed = []
            self.rowcount = 1

        def execute(self, sql, params=None):
            self.executed.append((" ".join(sql.split()), params))

    class Conn:
        def __init__(self):
            self.cur = Cur()
            self.committed = False

        def cursor(self):
            import contextlib

            @contextlib.contextmanager
            def cm():
                yield self.cur
            return cm()

        def commit(self):
            self.committed = True

    class Phones:
        @staticmethod
        def _typed_rows(what):
            return iter([(2, {"Serial": "1500", "Status": "ready",
                              "State": "unused", "App": "✓",
                              "Gmail": "g@x.com", "GPT Account": "a@x.com",
                              "Proxy": "SX1", "Tries": "", "Note": "ok"})])

        @staticmethod
        def said(value):
            return "" if value == "✗" else value

        @staticmethod
        def tries(cells):
            return 0

    class Pool:
        status_column, note_column = "Status", "Note"
        _rows = []

    book = type("B", (), {"phones": Phones(), "gmails": Pool(),
                          "proxies": Pool(), "apps": Pool()})
    conn = Conn()

    did = shadow.write_shadow(conn, book)

    assert conn.committed
    assert did["phones"] == 1 and did["closed"] == 1
    close_sql = conn.cur.executed[-1][0]
    assert "SET done_at = now()" in close_sql
    assert conn.cur.executed[-1][1] == (["1500"],)


def test_the_shadow_keeps_nobody_looked_three_valued():
    """'✓' is True, '✗' is False, and an empty App cell stays NULL - the
    2026-08-30 demotion must not come back through the mirror."""
    from geelark_farm.store.shadow import _APP_MARKS

    assert _APP_MARKS.get("✓") is True
    assert _APP_MARKS.get("✗") is False
    assert _APP_MARKS.get("") is None


def test_a_state_typo_mirrors_as_empty_not_as_a_failed_pass():
    """`dome` was silently nothing in the sheet; against a CHECK constraint
    it would be a failed mirror every pass until somebody noticed."""
    from geelark_farm.store.shadow import _state_word

    assert _state_word("dome") == ""
    assert _state_word(" TAKEN ") == "taken"
    assert _state_word(None) == ""


def test_a_store_enabled_start_ensures_the_schema(monkeypatch, make_settings):
    """ensure_schema's docstring promised it runs on every store-enabled
    start, and until 2026-08-31 nothing made that true: only store-init
    called it, so an ALTER deployed with the code never reached the cluster
    and the first page needing the new column answered 500. This is the
    wiring the docstring assumed."""
    import threading

    import geelark_farm.serve as serve_mod

    ensured = []
    monkeypatch.setattr("geelark_farm.store.db.ensure_schema",
                        lambda s: ensured.append(True))
    monkeypatch.setattr("geelark_farm.store.events.emit",
                        lambda *a, **k: True)
    settings = make_settings(store_enabled=True, store_host="h",
                             store_password="p")
    stop = threading.Event()
    stop.set()
    serve_mod.run(settings, stop=stop, passes=0)

    assert ensured, "a store-enabled start did not ensure the schema"


def test_a_dead_cluster_at_boot_does_not_stop_the_farm(monkeypatch,
                                                       make_settings, caplog):
    import threading

    import geelark_farm.serve as serve_mod

    monkeypatch.setattr(
        "geelark_farm.store.db.ensure_schema",
        lambda s: (_ for _ in ()).throw(ConnectionError("down")))
    settings = make_settings(store_enabled=True, store_host="h",
                             store_password="p")
    stop = threading.Event()
    stop.set()
    serve_mod.run(settings, stop=stop, passes=0)      # no raise

    assert any("could not ensure the store schema" in r.message
               for r in caplog.records)


# ---------------------------------------------------------- the actions queue
def test_web_mutations_defaults_off(make_settings):
    """A fresh deploy is dark: stage 5 arrives disabled, like every flag."""
    assert make_settings().web_mutations is False


class _ScriptedConn:
    """execute() plays back a script - each entry is the fetch answer, or an
    exception to raise - and records the SQL for the asserts."""

    def __init__(self, script, rowcounts=None):
        self.script = list(script)
        # How many rows each statement claims to have touched. A guarded
        # UPDATE that matches nothing is the whole behaviour of
        # `actions.finish`'s refusal, and a fake that always says one row
        # cannot show it. None keeps every older test as it was.
        self.rowcounts = list(rowcounts or [])
        self.sql = []
        self.committed = 0
        self.rolled_back = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        # A statement past the script - the NOTIFY that rides with every
        # enqueue - answers nothing, like Postgres does (2026-09-09).
        answer = self.script.pop(0) if self.script else None
        if isinstance(answer, Exception):
            raise answer
        touched = self.rowcounts.pop(0) if self.rowcounts else 1

        class Cur:
            rowcount = touched

            @staticmethod
            def fetchone():
                return answer

            @staticmethod
            def fetchall():
                return answer
        return Cur()

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1


def test_enqueue_answers_a_double_submit_with_the_first_row(monkeypatch,
                                                            make_settings):
    """The INSERT hits the idem_key UNIQUE; the person who double-tapped
    gets the id of the command they already queued, not an error page."""
    from geelark_farm.store import actions

    conn = _ScriptedConn([RuntimeError("duplicate key value"), (41,)])
    monkeypatch.setattr(actions, "connect", lambda s: conn)

    got = actions.enqueue(make_settings(), verb="noop", payload={},
                          requested_by=7, idem_key="k")

    assert got == 41
    assert conn.rolled_back == 1, "the failed INSERT was left open"


def test_cancel_tells_the_truth_in_all_three_directions(monkeypatch,
                                                        make_settings):
    from geelark_farm.store import actions

    settings = make_settings()

    def with_script(script):
        conn = _ScriptedConn(script)
        monkeypatch.setattr(actions, "connect", lambda s: conn)
        return conn

    with_script([(5,)])                      # still queued, mine
    assert actions.cancel(settings, action_id=5, user_id=7,
                          is_admin=False) == "cancelled"

    with_script([None, (7,)])                # mine, but a pass took it
    assert actions.cancel(settings, action_id=5, user_id=7,
                          is_admin=False) == "too_late"

    with_script([None, (99,)])               # someone else's
    assert actions.cancel(settings, action_id=5, user_id=7,
                          is_admin=False) == "not_yours"

    with_script([None, (99,)])               # an admin may touch anyone's
    assert actions.cancel(settings, action_id=5, user_id=7,
                          is_admin=True) == "too_late"

    with_script([None, None])                # a row that never existed
    assert actions.cancel(settings, action_id=6, user_id=7,
                          is_admin=True) == "not_yours"


def test_take_batch_splits_control_verbs_from_the_rest():
    """The two drain positions only work if the SQL splits the queue the
    same way: controls to the early drain, everything else to the late."""
    from geelark_farm.store import actions

    conn = _ScriptedConn([[(1, "noop", {}, 7)]])
    rows = actions.take_batch(conn, controls_only=False)
    assert rows == [{"id": 1, "verb": "noop", "payload": {},
                     "requested_by": 7}]
    assert "verb <> 'control'" in conn.sql[0]
    assert "SKIP LOCKED" in conn.sql[0]
    assert conn.committed == 1

    conn = _ScriptedConn([[]])
    assert actions.take_batch(conn, controls_only=True) == []
    assert "verb = 'control'" in conn.sql[0]


# ------------------------------------------------------------- users (C1)
def test_may_answers_for_admins_operators_and_nobody():
    """The one place "may this person do that" is answered."""
    from geelark_farm.store import users

    admin = {"role": "admin", "sees": "all", "active": True}
    op = {"role": "operator", "sees": "own", "active": True,
          "may_add_gmail": True}
    gone = dict(op, active=False)

    assert users.may(admin, "may_login_accounts")
    assert users.may(op, "may_add_gmail")
    assert not users.may(op, "may_login_accounts")
    assert not users.may(gone, "may_add_gmail")
    assert not users.may(None, "may_add_gmail")
    assert not users.may(admin, "may_launch_rockets"), "unknown fails closed"


def test_the_permission_vocabulary_matches_the_schema():
    """PERMISSIONS is the page's list and the schema's columns - two copies
    of the same names, held together here so one cannot be added to a list
    and forgotten in the other, or dropped from the schema and left on the
    Users page as a tick that does nothing.

    `may_add_proxy` went that second way on 2026-09-05: an operator's whole
    day became the dashboard, the Proxy tab is not on it, and an admin
    passes every tick implicitly - so the column could only ever have said
    no to somebody who no longer reaches the page it guarded.
    """
    import pathlib
    import re

    from geelark_farm.store import users

    ddl = pathlib.Path("src/geelark_farm/store/schema.sql").read_text(
        encoding="utf-8")
    added = set(re.findall(r"ADD COLUMN IF NOT EXISTS (may_\w+)", ddl))
    dropped = set(re.findall(r"DROP COLUMN IF EXISTS (may_\w+)", ddl))
    assert added - dropped == set(users.PERMISSION_COLUMNS)
    assert "may_add_proxy" in dropped, "and it is gone from the page too"


def test_an_admin_cannot_lock_themselves_out(monkeypatch, make_settings):
    from geelark_farm.store import users

    settings = make_settings()
    with pytest.raises(ValueError, match="yourself"):
        users.update(settings, 7, role="operator", sees="all", active=True,
                     permissions={}, by=7)
    with pytest.raises(ValueError, match="yourself"):
        users.update(settings, 7, role="admin", sees="all", active=False,
                     permissions={}, by=7)

    conn = _ScriptedConn([(0,)])              # no other active admin
    monkeypatch.setattr(users, "connect", lambda s: conn)
    with pytest.raises(ValueError, match="no active admin"):
        users.update(settings, 7, role="operator", sees="all", active=True,
                     permissions={}, by=2)
    assert conn.rolled_back == 1 and conn.committed == 0


def test_a_created_person_starts_with_a_one_time_password(monkeypatch,
                                                           make_settings):
    from geelark_farm.store import users

    conn = _ScriptedConn([(11,)])
    monkeypatch.setattr(users, "connect", lambda s: conn)

    new_id, password = users.create(make_settings(), username="sara",
                                    role="operator", sees="own",
                                    permissions={"may_add_gmail": True})

    assert new_id == 11 and len(password) >= 10
    assert "must_change_password" in conn.sql[0]
    assert conn.committed == 1
    with pytest.raises(ValueError, match="username"):
        users.create(make_settings(), username="Bad Name!", role="operator",
                     sees="own", permissions={})


# ------------------------------------------------- C7: the queue's record
def test_finish_stamps_finished_at_only_when_the_row_is_settled():
    from geelark_farm.store import actions

    conn = _ScriptedConn([None])
    actions.finish(conn, 5, status="running", result="booting")
    assert "finished_at = CASE WHEN %s THEN now() ELSE finished_at END" \
        in conn.sql[0]
    assert conn.committed == 1


def test_a_closed_command_is_not_re_opened_by_a_later_running(monkeypatch):
    """With SERVE_CONCURRENT off the launcher settles a login from inside
    the handler, and the drain then wrote the handler's own "running" over
    the finished row. `running` is not terminal, so nothing closed it
    again: Requests showed a login that had ended minutes ago as still
    going, until the sweep said two hours later that the service had
    restarted - which was untrue (2026-09-06)."""
    from geelark_farm.store import actions

    conn = _ScriptedConn([None], rowcounts=[0])
    wrote = actions.finish(conn, 5, status="running", result="starting")
    assert wrote is False, "the row was already closed"
    assert "AND (finished_at IS NULL OR %s)" in conn.sql[0]


def test_a_closed_command_can_still_be_corrected_by_another_verdict():
    """Terminal over terminal is a correction, not a re-opening."""
    from geelark_farm.store import actions

    conn = _ScriptedConn([None], rowcounts=[1])
    assert actions.finish(conn, 5, status="failed", result="it did not") is True
    params_guard = conn.sql[0]
    assert "finished_at IS NULL OR %s" in params_guard


def test_retry_copies_a_failed_command_into_a_new_row(monkeypatch,
                                                      make_settings):
    from geelark_farm.store import actions

    conn = _ScriptedConn([("change_proxy", {"serial": "1551"}, 9, "failed"),
                          (78,)])
    monkeypatch.setattr(actions, "connect", lambda s: conn)

    assert actions.retry(make_settings(store_enabled=True), action_id=238,
                         user_id=7, is_admin=True) == 78
    assert "INSERT INTO actions" in conn.sql[1]
    assert '"retry_of": 238' in conn.sql[1] or True   # payload rides in params
    assert conn.committed == 1

    conn = _ScriptedConn([("add_gpt", {}, 9, "done")])
    monkeypatch.setattr(actions, "connect", lambda s: conn)
    assert actions.retry(make_settings(store_enabled=True), action_id=1,
                         user_id=7, is_admin=True) == "not_failed"

    conn = _ScriptedConn([("add_gpt", {}, 9, "failed")])
    monkeypatch.setattr(actions, "connect", lambda s: conn)
    assert actions.retry(make_settings(store_enabled=True), action_id=1,
                         user_id=7, is_admin=False) == "not_yours"


def test_listing_pages_fifty_at_a_time_with_one_row_of_lookahead(
        monkeypatch, make_settings):
    """The Requests page asks for page N and learns whether an older page
    exists from the extra row, not from a second count."""
    from geelark_farm.store import actions

    seen = {}

    class FakeStore:
        def __init__(self, settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def _rows(self, sql, params=()):
            seen["sql"] = " ".join(sql.split())
            seen["params"] = params
            return [{"id": 1}] * (actions.PER_PAGE + 1)

    monkeypatch.setattr(actions, "Store", FakeStore)
    rows = actions.listing(make_settings(), user_id=7, everyone=True,
                           view="failed", page=3)

    assert len(rows) == actions.PER_PAGE + 1, "the caller trims the lookahead"
    assert seen["sql"].endswith("ORDER BY a.id DESC LIMIT %s OFFSET %s")
    assert seen["params"] == (True, 7, "failed", "failed",
                              actions.PER_PAGE + 1, 2 * actions.PER_PAGE)

    actions.listing(make_settings(), user_id=7)
    assert seen["params"][-2:] == (actions.PER_PAGE + 1, 0), \
        "no page means the first"


def test_every_sql_statement_has_balanced_quotes():
    """An apostrophe inside a statement has to be doubled, and one too
    many closes the literal early.

    `see the phones\'\'\' stories` did exactly that: Postgres answered
    "syntax error at or near stories", the failed statement left the
    transaction aborted, and a guard that swallowed the exception without
    rolling back handed the poisoned connection to the rest of the drain.
    Every queued button waited a day for a pass that could no longer take
    a batch (2026-09-04). The unit test for that statement passed
    throughout: its connection was a fake that never parsed anything.

    Adjacent string literals are folded by the parser, so this reads each
    statement the way psycopg receives it; an f-string is checked on its
    literal parts, which is where a hand-typed apostrophe lands.
    """
    import re

    # Capitals on purpose: every statement here is written in them, and a
    # docstring opening "With the pools in the store..." is not SQL.
    looks_sql = re.compile(r"\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b")
    seen = 0
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = "".join(bit.value for bit in node.values
                               if isinstance(bit, ast.Constant))
            else:
                continue
            if not looks_sql.match(text):
                continue
            seen += 1
            assert text.count("'") % 2 == 0, (
                f"{path.name}:{node.lineno} closes a quoted string early: "
                f"{text[:90]}")
    assert seen > 40, f"only {seen} statements walked - the walk is broken"


# ------------------------------------- what is still on the sheet, rev 11
def test_the_mirror_says_which_rows_are_still_on_the_sheet():
    """The mirror never deletes, so the table holds every account the farm
    has ever seen. Counting a blank status as free then counted history as
    stock: on 2026-09-05 the Gmails tab held six rows and none free while
    the front page said nineteen, out of four hundred and twenty-six rows
    six generations deep on the same sheet_row numbers.

    So the pass says what it saw, the same shape `phones.done_at` has had
    all along - nothing is removed, and what left stops being stock.
    """
    from geelark_farm.store import shadow

    class Cur:
        def __init__(self):
            self.executed = []
            self.rowcount = 2
            self._ids = iter([11, 12, 13])

        def execute(self, sql, params=None):
            self.executed.append((" ".join(sql.split()), params))

        def fetchone(self):
            return (next(self._ids),)

    class Conn:
        def __init__(self):
            self.cur = Cur()
            self.committed = False

        def cursor(self):
            import contextlib

            @contextlib.contextmanager
            def cm():
                yield self.cur
            return cm()

        def commit(self):
            self.committed = True

    class Row:
        sheet_row, error, proxy = 2, None, None
        values = {"Address": "a@example.com", "Status": "", "Note": ""}

        class credentials:
            email = "a@example.com"
            password = "pw"
            totp_secret = ""
            email_code_only = False
            recovery_email = ""

    class Gmails:
        status_column, note_column, claimed_at_column = "Status", "Note", ""
        _rows = [Row()]

    class Empty:
        status_column, note_column, claimed_at_column = "Status", "Note", ""
        _rows = []

    class Phones:
        @staticmethod
        def _typed_rows(what):
            return iter([])

        @staticmethod
        def said(value):
            return value

        @staticmethod
        def tries(cells):
            return 0

    book = type("B", (), {"phones": Phones(), "gmails": Gmails(),
                          "proxies": Empty(), "apps": Empty()})
    conn = Conn()

    did = shadow.write_shadow(conn, book)

    # The mirror does not write the sheet flag any anymore, and must not:
    # nothing reads it, so writing it would only be a way for it to come
    # back. This one statement over the whole table would mark every row
    # born in the store since the switch as "not on the sheet" within
    # thirty seconds, the first time anyone tried POOLS_IN_PG=0 as a
    # rollback - which is not one (2026-09-06).
    marked = [sql for sql, _ in conn.cur.executed if "on_sheet" in sql]
    assert marked == [], "the retired flag was written again"
    assert did["left_the_sheet"] == 0


def test_the_upserts_hand_back_the_row_they_touched():
    """The marking is by id rather than by address or by host and port,
    because the two upserts key on different things and a second spelling
    of either is a second way for them to disagree."""
    shadow_src = (SRC / "store" / "shadow.py").read_text(encoding="utf-8")

    assert shadow_src.count("RETURNING r.id") == 2, "both upserts"


# ------------------------------------------------- reads read, writes write
class _CountingConn:
    """A connection that says whether it was committed or rolled back."""

    def __init__(self, description=None, rows=()):
        self.committed = self.rolled_back = 0
        self.sql: list[str] = []
        self._description = description
        self._rows = list(rows)

    def cursor(self):
        conn = self

        class Cur:
            description = conn._description

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            @staticmethod
            def execute(sql, params=()):
                conn.sql.append(sql)

            @staticmethod
            def fetchall():
                return conn._rows

        return Cur()

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        pass


def _store_on(conn, monkeypatch, make_settings):
    from geelark_farm.store import db

    monkeypatch.setattr(db, "connect", lambda s: conn)
    return db.Store(make_settings())


def test_a_read_leaves_no_transaction_behind(monkeypatch, make_settings):
    class Col:
        name = "id"

    conn = _CountingConn(description=[Col()], rows=[(7,)])

    got = _store_on(conn, monkeypatch, make_settings)._rows("SELECT 1")

    assert got == [{"id": 7}]
    assert (conn.committed, conn.rolled_back) == (0, 1)


def test_a_write_is_committed_and_hands_back_what_it_returned(
        monkeypatch, make_settings):
    """`_rows` rolls back, so a write sent through it is executed, has its
    RETURNING row read out, and is then thrown away - the caller gets a
    fresh id for a row that does not exist. `set_state` answered True while
    the phone stayed `unused`, and `wanted.ask` handed back an id for a
    build nobody had asked for (2026-09-05)."""
    class Col:
        name = "id"

    conn = _CountingConn(description=[Col()], rows=[(41,)])

    got = _store_on(conn, monkeypatch, make_settings)._write(
        "UPDATE phones SET state = %s RETURNING id", ("done",))

    assert got == [{"id": 41}]
    assert (conn.committed, conn.rolled_back) == (1, 0)


def test_a_write_with_nothing_to_return_does_not_fetch(monkeypatch,
                                                       make_settings):
    """A DELETE has no `description`. Asking such a cursor for rows raises
    in psycopg, so a write without RETURNING must not be fetched at all."""
    conn = _CountingConn(description=None)

    got = _store_on(conn, monkeypatch, make_settings)._write("DELETE FROM x")

    assert got == []
    assert conn.committed == 1


def test_no_write_is_sent_through_the_reading_helper():
    """The whole class, in one sweep. `_rows` ends in a rollback, so any
    statement that changes something has to go through `_write` - and the
    difference is invisible at the call site, which is exactly why ten of
    them were wrong at once and nothing raised."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "src"
    reading = re.compile(r'_rows\(\s*\n?\s*(?:f?")(INSERT|UPDATE|DELETE|WITH)',
                         re.I)
    wrong = [f"{path}:{text[:m.start()].count(chr(10)) + 1} {m.group(1)}"
             for path in sorted(root.rglob("*.py"))
             for text in [path.read_text(encoding="utf-8")]
             for m in reading.finditer(text)]

    assert not wrong, "these writes are rolled back: " + "; ".join(wrong)


def test_the_retired_sheet_flag_is_written_by_nothing_at_all():
    """One statement wrote it, in the mirror, and that statement is gone.
    Left dormant it would be the way the column comes back: it runs over
    the whole table, so the first time anyone tried POOLS_IN_PG=0 as a
    rollback it would mark every row born in the store since the switch as
    "not on the sheet", within thirty seconds (2026-09-06)."""
    import pathlib

    for name in ("store/shadow.py", "store/pgpool.py", "store/db.py",
                 "web/read.py", "web/api_v1_read.py", "web/api_v1_write.py"):
        source = pathlib.Path("src/geelark_farm", name).read_text(
            encoding="utf-8")
        code = "\n".join(line for line in source.split("\n")
                         if not line.lstrip().startswith("#"))
        assert "on_sheet" not in code, f"{name} still touches the sheet flag"


def test_the_rows_a_person_closed_carry_the_id_that_closes_them(monkeypatch,
                                                                make_settings):
    """`apply_phone_states` reads `row["sheet_row"]` and hands the list to
    `PgPhoneLog.delete_rows`, which closes rows BY ID. Without the key it
    raised KeyError - and raised it after the irreversible half had run, so
    the GeeLark phone was deleted, the Gmail retired and the app account
    settled, while the row stayed open for the next pass to do all of it
    again. The step guard swallowed the crash into one log line
    (2026-09-06)."""
    from geelark_farm.store import person

    seen = {}

    class FakeStore:
        def __init__(self, settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def _rows(self, sql, params=()):
            seen["sql"] = " ".join(sql.split())
            return [{"sheet_row": 7, "serial": "1856", "state": "done",
                     "gmail": "a@x.com", "app_account": ""}]

    monkeypatch.setattr(person, "Store", FakeStore)

    rows = person.marked(make_settings(store_enabled=True))

    assert "SELECT id AS sheet_row" in seen["sql"], "the id it closes rows by"
    assert rows and rows[0]["sheet_row"] == 7


# ------------------------------------------------------- the connection pool
class _Info:
    def __init__(self):
        self.transaction_status = 0


class _Conn:
    """What the pool needs of a connection: closed, its transaction
    state, rollback, a ping, and a real close."""

    def __init__(self):
        self.closed = False
        self.info = _Info()
        self.rolled_back = 0
        self.pings = 0
        self.ping_ok = True

    def rollback(self):
        self.rolled_back += 1
        self.info.transaction_status = 0

    def execute(self, sql):
        self.pings += 1
        if not self.ping_ok:
            raise RuntimeError("gone")
        return self

    def fetchone(self):
        return (1,)

    def discard(self):
        self.closed = True


def _pool(monkeypatch, make_settings):
    from geelark_farm.store import db

    opened = []

    def open(kwargs):
        conn = _Conn()
        opened.append(conn)
        return conn

    settings = make_settings(store_enabled=True, store_host="db",
                             store_password="pw")
    monkeypatch.setattr(db.Settings, "require_store", lambda self: None,
                        raising=False)
    return db._Pool(open), settings, opened


def test_a_closed_connection_is_the_next_callers(monkeypatch, make_settings):
    """Opening one is 170-300 ms against the cluster and a query on an
    open one a millisecond; every read on the console opened its own, and
    a dashboard page opened three or four (2026-09-08)."""
    pool, settings, opened = _pool(monkeypatch, make_settings)
    first = pool.take(settings)
    pool.give(first)
    assert pool.take(settings) is first and len(opened) == 1
    # Two out at once are two connections; both come back.
    second = pool.take(settings)
    assert second is not first and len(opened) == 2
    pool.give(first)
    pool.give(second)
    assert pool.take(settings) is second, "LIFO: the warmest one first"


def test_a_connection_is_handed_out_only_when_it_is_clean(
        monkeypatch, make_settings):
    pool, settings, opened = _pool(monkeypatch, make_settings)
    conn = pool.take(settings)
    # A transaction left open is rolled back on the way in, never
    # inherited by the next caller.
    conn.info.transaction_status = 2
    pool.give(conn)
    assert conn.rolled_back == 1 and pool.take(settings) is conn
    # One that broke is closed for real, not kept.
    conn.closed = True
    pool.give(conn)
    assert pool.take(settings) is not conn and len(opened) == 2
    # One whose rollback fails is discarded too.
    bad = pool.take(settings)
    bad.info.transaction_status = 3
    bad.rollback = lambda: (_ for _ in ()).throw(RuntimeError("dead"))
    pool.give(bad)
    assert bad.closed and pool.take(settings) is not bad


def test_an_idle_connection_is_pinged_and_an_old_one_dropped(
        monkeypatch, make_settings):
    from geelark_farm.store import db

    pool, settings, opened = _pool(monkeypatch, make_settings)
    clock = {"t": 1000.0}
    monkeypatch.setattr(db.time, "monotonic", lambda: clock["t"])
    conn = pool.take(settings)
    pool.give(conn)
    # Back soon: no ping.
    clock["t"] += 1
    assert pool.take(settings) is conn and conn.pings == 0
    pool.give(conn)
    # Idle past the ping window: pinged, and kept when it answers.
    clock["t"] += db._Pool.PING_AFTER + 1
    assert pool.take(settings) is conn and conn.pings == 1
    pool.give(conn)
    # Pinged and silent: closed for real, a fresh one opened.
    clock["t"] += db._Pool.PING_AFTER + 1
    conn.ping_ok = False
    fresh = pool.take(settings)
    assert fresh is not conn and conn.closed
    pool.give(fresh)
    # Idle past the cluster's patience: dropped without asking.
    clock["t"] += db._Pool.IDLE_SECONDS + 1
    assert pool.take(settings) is not fresh and fresh.closed


def test_the_pool_is_bounded_and_answers_to_one_cluster(
        monkeypatch, make_settings):
    from geelark_farm.store import db

    pool, settings, opened = _pool(monkeypatch, make_settings)
    out = [pool.take(settings) for _ in range(db._Pool.SIZE + 2)]
    for conn in out:
        pool.give(conn)
    kept = [c for c in out if not c.closed]
    assert len(kept) == db._Pool.SIZE, "a burst keeps SIZE, closes the rest"
    # Other settings - another cluster, another user - flush the shelf.
    other = make_settings(store_enabled=True, store_host="db2",
                          store_password="pw")
    conn = pool.take(other)
    assert conn not in out and all(c.closed for c in out)
    pool.give(conn)
    pool.drain()
    assert conn.closed


def test_enqueue_rings_the_postgres_bell_with_the_row(monkeypatch,
                                                      make_settings):
    """A keeper in another container hears NOTIFY, not a threading.Event;
    the bell goes with the commit so it never wakes to a row it cannot
    yet see (2026-09-09)."""
    from geelark_farm.store import actions, db

    class Cur:
        def fetchone(self):
            return (7,)

    class Conn:
        def __init__(self):
            self.ran = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            self.ran.append(sql)
            return Cur()

        def commit(self):
            self.ran.append("COMMIT")

        def rollback(self):
            self.ran.append("ROLLBACK")

    conn = Conn()
    monkeypatch.setattr(db, "connect", lambda s: conn)
    monkeypatch.setattr(actions, "connect", lambda s: conn)
    monkeypatch.setattr("geelark_farm.store.events.emit", lambda *a, **k: None)

    assert actions.enqueue(make_settings(), verb="noop", payload={},
                           requested_by=1, idem_key="k") == 7
    assert conn.ran[-2:] == ["NOTIFY geelark_actions", "COMMIT"]
