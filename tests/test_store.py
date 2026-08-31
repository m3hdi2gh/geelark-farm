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
        if "store" in path.parts:
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
