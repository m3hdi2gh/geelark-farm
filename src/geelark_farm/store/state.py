"""Small facts a pass learns that belong to no row.

The proxies GeeLark holds that the Proxy tab never heard of, for one: the
sync computes the list every pass, the board shows a count, and until now
nothing kept the list itself - so a page could not offer "add it" without
a GeeLark call of its own, which the budget rule forbids. One jsonb row
per key, replaced whole each pass, read by the pages.
"""

from __future__ import annotations

import json

from ..config import Settings
from .db import connect


def put(conn, key: str, value) -> None:
    """Replace one key, on the caller's connection and transaction."""
    conn.execute(
        "INSERT INTO service_state (key, value, updated_at)"
        " VALUES (%s, %s::jsonb, now())"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,"
        " updated_at = now()", (key, json.dumps(value)))


def get(settings: Settings, key: str, default=None):
    """One key's value, or `default` when nothing wrote it yet."""
    with connect(settings) as conn:
        cur = conn.execute("SELECT value FROM service_state WHERE key = %s",
                           (key,))
        row = cur.fetchone()
        conn.rollback()
    return row[0] if row else default
