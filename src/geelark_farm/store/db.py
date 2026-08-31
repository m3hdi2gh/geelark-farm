"""Connections, and the schema they expect to find.

`psycopg` is imported lazily, inside the functions, for the same reason
gspread is in pools.py: the dependency exists only on the paths that use
it, and `import geelark_farm.store.db` on a box without psycopg installed
must not raise until something actually asks for a connection.
"""

from __future__ import annotations

import logging
from importlib import resources as importlib_resources

from ..config import Settings

log = logging.getLogger(__name__)

#: How long to wait for the cluster before calling the attempt failed.
#: Ten seconds against a same-network endpoint that answers in 25ms: a
#: connect that needs longer than this is down, not slow, and the caller's
#: job is to refuse the work rather than stall a pass on it - the same
#: asymmetry `Pool._still_free` records: a claim not made costs one pass
#: of waiting, a claim made against a dead store costs the truth.
CONNECT_TIMEOUT = 10


def dsn_kwargs(settings: Settings) -> dict:
    """Connection keywords from settings, in one place.

    `sslmode=prefer`, not `require`: the cluster does not offer TLS
    (measured 2026-08-31, "server does not support SSL"), and requiring it
    would turn every connection into an instant failure. The endpoint is
    unreachable from the public internet, which is the security this
    deployment actually has.
    """
    settings.require_store()
    return dict(
        host=settings.store_host,
        port=settings.store_port,
        dbname=settings.store_db,
        user=settings.store_user,
        password=settings.store_password,
        connect_timeout=CONNECT_TIMEOUT,
        sslmode="prefer",
        # The farm appears in pg_stat_activity by name, so whoever runs
        # other databases on this shared cluster can tell whose load is
        # whose without asking.
        application_name="geelark-farm",
    )


def connect(settings: Settings):
    """One new connection. The caller owns closing it."""
    import psycopg

    return psycopg.connect(**dsn_kwargs(settings))


def schema_sql() -> str:
    """The DDL, read from the file beside this module."""
    return (importlib_resources.files(__package__) / "schema.sql").read_text(
        encoding="utf-8")


def ensure_schema(settings: Settings) -> None:
    """Bring the cluster to this version's schema. Safe to run every start.

    Idempotent by construction - every statement in schema.sql is
    `IF NOT EXISTS` - because this runs on every store-enabled start and a
    half-applied schema from a killed process must converge on the next
    one rather than wedge it. The applied marker is written last, so its
    presence means the whole file ran.
    """
    with connect(settings) as conn:
        conn.execute(schema_sql())
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_rev', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (SCHEMA_REV,))
        conn.commit()
    log.info("store schema ensured (rev %s) on %s/%s",
             SCHEMA_REV, settings.store_host, settings.store_db)


#: Bumped when schema.sql changes shape. Not a migration system - the file
#: is additive-only while the sheet is still authoritative, and a real
#: migration story is stage 7's problem, not stage 1's. What this buys now
#: is one queryable fact: which code last touched the schema.
SCHEMA_REV = "1"


class Store:
    """The farm's data, spoken in ids.

    One connection, owned by the Store, used from the loop's threads under
    psycopg's own thread-safety rules; the claim methods that arrive with
    the write paths will each be one statement, so the serialisation the
    sheet needed a process-wide lock for is the engine's job here.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._conn = connect(settings)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ health
    def ping(self) -> bool:
        """Whether the cluster answers, without raising. The store's callers
        follow the sheet's rule: a read that fails refuses the work."""
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception as exc:                                  # noqa: BLE001
            log.warning("the store did not answer (%s)", exc)
            return False
