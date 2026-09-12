"""The keys the machines come in with.

A client is a name, a role and a hash. The token itself is minted once,
shown once and never stored - `web.api_v1.hash_key` is all that is kept -
so the only thing this module can ever do about a lost key is mint
another and let the old hash stop matching.

Written the way `store.users` is written, for the same reason: these are
rows a person administers from a page, not stock a pass claims, so each
verb is one short connection that commits, and the page calls them
directly rather than queueing a request nobody would read.

Three rules this keeps that the CLI's own mint does not:

* **A name that exists is refused, not rotated.** `geelark api-client`
  ends in `ON CONFLICT (name) DO UPDATE SET key_hash = ...`, which is
  right at a terminal where the person typing owns the decision, and
  wrong on a page where retyping a name would silently invalidate a live
  panel's key. Rotating is its own button, with a confirmation.
* **The secret of a webhook is written only when one was typed.** The
  form cannot show it back, so an empty field means "leave it alone".
* **The role list comes from the schema.** `api_clients` carries a CHECK
  that was widened once already (rev 26 added `sandbox`); a page offering
  a word outside it gets a constraint violation a person reads as "the
  store refused it".
"""

from __future__ import annotations

import logging

from ..config import Settings
from .db import connect

log = logging.getLogger(__name__)

#: The words `api_clients.role` accepts, in the order the page offers
#: them: what a panel is for, what the bot will be for, and the practice
#: room a client's author writes against first.
ROLES = ("panel", "bot", "sandbox")

#: Never `key_hash`, and never `webhook_secret`: one is a secret at rest
#: and the other is a secret a page must not echo. `has_secret` is what
#: the page shows instead.
_LISTING = (
    "SELECT id, name, role, key_prefix, active, webhook_url,"
    " webhook_secret <> '' AS has_secret, created_at, last_seen_at"
    " FROM api_clients")
LISTING_ORDER = " ORDER BY active DESC, name"


def listing(settings: Settings) -> list[dict]:
    with connect(settings) as conn:
        cur = conn.execute(_LISTING + LISTING_ORDER)
        names = [d.name for d in cur.description]
        rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        conn.rollback()
        return rows


def get(settings: Settings, client_id: int) -> dict | None:
    with connect(settings) as conn:
        cur = conn.execute(_LISTING + " WHERE id = %s", (int(client_id),))
        row = cur.fetchone()
        names = [d.name for d in cur.description]
        conn.rollback()
        return dict(zip(names, row, strict=True)) if row else None


def _fresh_key() -> tuple[str, bytes, str]:
    from ..web.api_v1 import mint_key

    return mint_key()


def create(settings: Settings, *, name: str, role: str) -> tuple[int, str]:
    """Mint a client and return (id, the token, shown this once).

    Raises ValueError for a name or a role the page should argue with,
    and lets the unique index on `name` raise as itself: a duplicate is
    the one case where rotating somebody's live key would be the wrong
    kindness.
    """
    wanted = str(name or "").strip()
    if not wanted or len(wanted) > 60:
        raise ValueError("a name of 1 to 60 characters")
    if role not in ROLES:
        raise ValueError("one of " + ", ".join(ROLES))
    token, digest, prefix = _fresh_key()
    with connect(settings) as conn:
        cur = conn.execute(
            "INSERT INTO api_clients (name, role, key_hash, key_prefix)"
            " VALUES (%s, %s, %s, %s) RETURNING id",
            (wanted, role, digest, prefix))
        new_id = int(cur.fetchone()[0])
        conn.commit()
    log.info("api client %r (%s) minted as id %s", wanted, role, new_id)
    return new_id, token


def rotate(settings: Settings, client_id: int) -> tuple[str, str] | None:
    """A new token for a client that has one. (name, token), or None.

    The old hash stops matching the moment this commits, which is the
    point: a key that got out is revoked by being replaced.
    """
    token, digest, prefix = _fresh_key()
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE api_clients SET key_hash = %s, key_prefix = %s,"
            " active = true WHERE id = %s RETURNING name",
            (digest, prefix, int(client_id)))
        row = cur.fetchone()
        conn.commit()
    if row is None:
        return None
    log.info("api client %r had its key rotated", row[0])
    return str(row[0]), token


def set_active(settings: Settings, client_id: int, active: bool) -> str | None:
    """Switch a key off or on. The client's name, or None."""
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE api_clients SET active = %s WHERE id = %s RETURNING name",
            (bool(active), int(client_id)))
        row = cur.fetchone()
        conn.commit()
    if row is None:
        return None
    log.info("api client %r is now %s", row[0],
             "active" if active else "switched off")
    return str(row[0])


def set_webhook(settings: Settings, client_id: int, *, url: str,
                secret: str = "") -> str | None:
    """Where this client's events will be posted once webhooks exist.

    The URL is written every time; the secret only when one was typed,
    because the form cannot show back what is already there and an empty
    box means "leave it alone", not "delete it".
    """
    wanted = str(url or "").strip()
    if wanted and not wanted.lower().startswith("https://"):
        raise ValueError("an https:// address - an event carries an account")
    sets = ["webhook_url = %s"]
    params: list = [wanted]
    if str(secret or "").strip():
        sets.append("webhook_secret = %s")
        params.append(str(secret).strip())
    params.append(int(client_id))
    with connect(settings) as conn:
        cur = conn.execute(
            f"UPDATE api_clients SET {', '.join(sets)}"
            f" WHERE id = %s RETURNING name", params)
        row = cur.fetchone()
        conn.commit()
    return str(row[0]) if row else None
