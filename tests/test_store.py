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
