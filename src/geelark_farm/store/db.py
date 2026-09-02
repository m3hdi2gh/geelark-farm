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
SCHEMA_REV = "7"


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

    # ------------------------------------------------------------- stock
    def add_resource(self, row: dict) -> int:
        """Insert one validated row and return its id.

        `row` comes from store.validate - this method trusts its shape and
        lets the partial unique indexes speak for identity: a duplicate
        raises rather than flagging after the fact, which is the whole
        upgrade over _flag_duplicates.
        """
        columns = sorted(row)
        placeholders = ", ".join(["%s"] * len(columns))
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO resources ({', '.join(columns)}) "
                f"VALUES ({placeholders}) RETURNING id",
                [row[c] for c in columns])
            new_id = cur.fetchone()[0]
        self._conn.commit()
        return new_id

    def available(self, kind: str) -> list[dict]:
        """Free rows of one kind, in the claim's own order - so what a page
        shows is what the next claim will take."""
        return self._rows(
            "SELECT * FROM resources WHERE kind = %s AND error IS NULL "
            "AND status = '' ORDER BY "
            "CASE WHEN kind = 'proxy' THEN times_used ELSE 0 END, "
            "sheet_row NULLS LAST, id", (kind,))

    # ------------------------------------------------------------- claims
    def claim(self, kind: str, *, run_id: str, machine: str,
              lease_seconds: float,
              owner_id: int | None = None) -> dict | None:
        """Take the first free row of `kind`, atomically, or None.

        One transaction: pick with FOR UPDATE SKIP LOCKED, mark, and write
        the claims row carrying the ONE lease. Two processes racing here
        get two different rows, or one row and a None - the property the
        sheet needed a process-wide lock plus a re-read to approximate, and
        the engine simply has (2026-08-30 was exactly this gap).

        `owner_id` narrows the pick to that user's reserved rows plus the
        shared pool - assignment is a filter here, not a separate stock.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "WITH picked AS ("
                "  SELECT id FROM resources"
                "  WHERE kind = %s AND error IS NULL AND status = ''"
                "    AND (owner_id IS NULL OR owner_id = %s)"
                "  ORDER BY CASE WHEN kind = 'proxy' THEN times_used"
                "           ELSE 0 END, sheet_row NULLS LAST, id"
                "  FOR UPDATE SKIP LOCKED LIMIT 1)"
                "UPDATE resources r SET status = 'in_use',"
                "  times_used = times_used"
                "    + CASE WHEN r.kind = 'proxy' THEN 1 ELSE 0 END,"
                "  updated_at = now()"
                "  FROM picked WHERE r.id = picked.id RETURNING r.*",
                (kind, owner_id))
            row = _one_dict(cur)
            if row is None:
                self._conn.rollback()
                return None
            cur.execute(
                "INSERT INTO claims (run_id, machine, resource_id,"
                " lease_until) VALUES (%s, %s, %s,"
                " now() + make_interval(secs => %s)) RETURNING id",
                (run_id, machine, row["id"], lease_seconds))
            row["claim_id"] = cur.fetchone()[0]
        self._conn.commit()
        return row

    def beat(self, run_id: str, lease_seconds: float) -> int:
        """Push every live lease this run holds forward. Returns how many -
        the heartbeat logs that number, so a beat that stopped covering a
        claim is visible rather than silent."""
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE claims SET lease_until ="
                " now() + make_interval(secs => %s)"
                " WHERE run_id = %s AND released_at IS NULL",
                (lease_seconds, run_id))
            moved = cur.rowcount
        self._conn.commit()
        return moved

    def release(self, claim_id: int, *, outcome: str,
                resource_status: str = "") -> None:
        """Close one claim and put its resource where `outcome` says.

        `resource_status` empty means back to the pool - "nothing was
        learnt", the same meaning a blank sheet cell had.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE claims SET released_at = now(), outcome = %s"
                " WHERE id = %s AND released_at IS NULL"
                " RETURNING resource_id", (outcome, claim_id))
            got = cur.fetchone()
            if got is not None:
                cur.execute(
                    "UPDATE resources SET status = %s, updated_at = now()"
                    " WHERE id = %s", (resource_status, got[0]))
        self._conn.commit()

    def sweep_stale(self) -> list[dict]:
        """Free everything whose lease has passed - a dead run's holdings.

        A separate sweep rather than a predicate inside `claim`, so freeing
        is one visible, logged event per dead run instead of a side effect
        scattered across whoever claims next. Called once a pass, like the
        sheet's abandoned() sweep.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE claims SET released_at = now(),"
                " outcome = 'lease_expired'"
                " WHERE released_at IS NULL AND lease_until < now()"
                " RETURNING id, run_id, resource_id")
            dead = [dict(zip(("claim_id", "run_id", "resource_id"), r,
                          strict=True))
                    for r in cur.fetchall()]
            for item in dead:
                if item["resource_id"] is not None:
                    cur.execute(
                        "UPDATE resources SET status = '',"
                        " updated_at = now() WHERE id = %s",
                        (item["resource_id"],))
        self._conn.commit()
        return dead

    # -------------------------------------------------------------- users
    def create_user(self, *, username: str, password: str, role: str,
                    sees: str) -> int:
        from . import auth

        hashed = auth.hash_password(password)
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, password_salt,"
                " scrypt_n, scrypt_r, scrypt_p, role, sees)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (username, hashed["password_hash"],
                 hashed["password_salt"], hashed["scrypt_n"],
                 hashed["scrypt_r"], hashed["scrypt_p"], role, sees))
            new_id = cur.fetchone()[0]
        self._conn.commit()
        return new_id

    def check_login(self, username: str, password: str) -> dict | None:
        """The user row on success, None on any failure - one answer for
        wrong-name and wrong-password alike, so the login page cannot be
        used to enumerate usernames."""
        from . import auth

        rows = self._rows(
            "SELECT * FROM users WHERE username = %s AND active",
            (username,))
        if not rows:
            return None
        row = rows[0]
        if not auth.verify_password(password, row):
            return None
        row.pop("password_hash", None)
        row.pop("password_salt", None)
        # "last seen" on the Users page. Best effort: a login that verified
        # is a login, whether or not the stamp landed.
        try:
            with self._conn.cursor() as cur:
                cur.execute("UPDATE users SET last_login_at = now()"
                            " WHERE id = %s", (row["id"],))
            self._conn.commit()
        except Exception as exc:                                  # noqa: BLE001
            self._conn.rollback()
            log.warning("could not stamp last_login_at for %s (%s)",
                        username, exc)
        return row

    # ---------------------------------------------------------- plumbing
    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            names = [d.name for d in cur.description]
            out = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        self._conn.rollback()      # reads leave no transaction behind
        return out

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

def _one_dict(cur) -> dict | None:
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d.name for d in cur.description], row, strict=True))

