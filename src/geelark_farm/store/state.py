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


def update(settings: Settings, key: str, fn, default=None):
    """Change one key under its own row lock: read, `fn(value)`, write,
    in one transaction. Returns what was written.

    `put` replaces a value whole, and six subsystems used it as the
    second half of a read-modify-write across two connections - the stop
    requests, the breaker's count, the day's captcha strikes and the host
    clears, the uploaded apps, the geo cache - so two processes editing
    the same blob within a moment lost one edit: a strike from a builder
    landing between the console's read and its write resurrected the
    strikes the operator had just cleared, and two replicas settling
    builds under one breaker count could each overwrite the other's
    (2026-09-21, found by audit). `fn` sees the value the row holds at
    the moment of the lock, and nobody else writes it until this commits.
    """
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO service_state (key, value, updated_at)"
            " VALUES (%s, %s::jsonb, now()) ON CONFLICT (key) DO NOTHING",
            (key, json.dumps(default)))
        cur = conn.execute(
            "SELECT value FROM service_state WHERE key = %s FOR UPDATE",
            (key,))
        row = cur.fetchone()
        value = fn(row[0] if row else default)
        conn.execute(
            "UPDATE service_state SET value = %s::jsonb, updated_at = now()"
            " WHERE key = %s", (json.dumps(value), key))
        conn.commit()
    return value
