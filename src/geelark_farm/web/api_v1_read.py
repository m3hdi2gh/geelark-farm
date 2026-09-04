"""What the panel API answers with, read from the mirror.

A sibling of `read.py`, not an extension of it. Both read the store and
neither touches the Book, but they answer different questions: read.py's
contract is "what the pages show", scoped by who is looking; this one's is
"what one client was told about an account", scoped by nothing - a bearer
key already said who is asking, and the panel may see every account it is
allowed to see whole.

The state a client is told is a VIEW over the pool's own status word, never
a second status column. While the sheet is authoritative the mirror rewrites
`resources.status` from the Gpt Info tab every thirty seconds, so a status
this side wrote would last half a minute; the columns this API owns are the
ones shadow._upsert_resource does not name. This module only reads them -
api_v1_write is the half that changes anything.

Timestamps come out of psycopg as aware datetimes and leave here that way.
RFC 3339 is the serializer's business, at the edge, because the console's
own clock (pages._moment) converts to Tehran and an API must not.
"""

from __future__ import annotations

import base64
import binascii
import logging

from ..config import Settings
from ..store.db import Store
from .read import IMPORTED, ROUTINE

log = logging.getLogger(__name__)

#: Every state a client can be told, in the order an account travels. The
#: vocabulary is fixed here even where nothing can reach a word yet:
#: `needs_code` arrives with the code path, and a client that learns it now
#: will not need a new release to understand it.
API_STATES = ("queued", "waiting_customer", "blocked", "signing_in",
              "needs_code", "ready", "delivered", "needs_human",
              "invalid", "withdrawn")

#: The pool's status words that map to one state each, whatever else the
#: row says. Everything not here is decided by the rules in `state_of`.
_DIRECT = {
    "delivered": "delivered",
    "ready": "ready",
    "in_use": "signing_in",
    # Written by the code path, which does not exist yet (stage D). Named
    # now so the word means the same thing on the day it is first written.
    "needs_code": "needs_code",
}

#: What the panel may send, and what the farm can actually carry out today.
#: A kind outside `SERVED` is accepted, stored and reported `blocked` - the
#: panel sees the truth rather than a queue that never moves.
CREDENTIAL_KINDS = ("password_totp", "google_backup_codes",
                    "email_code_auto", "email_code_customer")
PRODUCTS = ("chatgpt", "claude")
SERVED = {"chatgpt": ("password_totp",), "claude": ()}

#: The kind whose code comes from a person, so the account waits for the
#: panel to say that person is at their keyboard.
_ASKS_A_PERSON = "email_code_customer"

#: One row's worth of columns, r.-qualified because every query here joins
#: phones and both tables carry an id, a status and an updated_at - the
#: ambiguity that took the Gmail Pool down (2026-09-03).
_ACCOUNT_COLUMNS = (
    "r.id, r.address, r.status, r.error, r.serial, r.note, r.source,"
    " r.product, r.credential_kind, r.panel_ref, r.client_id,"
    " r.attempts, r.failures, r.customer_ready, r.withdrawn_at,"
    " r.state_changed_at, r.delivered_at, r.created_at, r.updated_at"
)


def ref_of(row: dict) -> str:
    """An account's public id: the panel's own reference when it gave one,
    and a farm-issued one otherwise.

    Every app account has a ref, including the several hundred that came
    from the sheet years before there was a panel - which is what lets a
    client read the real pool on the day the door opens, rather than an
    empty list until the first POST.
    """
    return str(row.get("panel_ref") or f"farm_{int(row['id'])}")


def state_of(row: dict) -> str:
    """The one word a client is told, from the pool's own vocabulary.

    Read top to bottom; the first rule that fits wins. A row validation
    refused is `invalid` whatever its status says, because it is not stock
    and nothing will ever claim it.
    """
    # Taken back by the panel, and its own column rather than a status
    # word: the mirror rewrites `status` from the sheet every pass, so a
    # word written there would last half a minute.
    if row.get("withdrawn_at"):
        return "withdrawn"
    if row.get("error"):
        return "invalid"
    status = str(row.get("status") or "").strip().lower()
    if status in _DIRECT:
        return _DIRECT[status]
    if status and status != IMPORTED and status not in ROUTINE["app"]:
        # A word the pool never settles on is a verdict a run wrote.
        return "needs_human"
    # Blank or `imported`: stock, unless something ahead of the pool holds
    # it back - a kind this farm cannot serve yet, or a customer who has
    # not said they are ready to answer.
    kind = str(row.get("credential_kind") or "")
    product = str(row.get("product") or "")
    if kind and kind not in SERVED.get(product or "chatgpt", ()):
        return "blocked"
    if kind == _ASKS_A_PERSON and not row.get("customer_ready"):
        return "waiting_customer"
    return "queued"


def blocked_of(row: dict) -> str | None:
    """Why a blocked row is blocked, in one token. Only ever set when
    `state_of` said `blocked`, so a client can branch on the state and read
    this for the detail."""
    return ("kind_not_served_yet" if state_of(row) == "blocked" else None)


def _cursor(row: dict) -> str:
    """An opaque handle on (updated_at, id) - the keyset this API pages by.

    Not a version column: nothing in this program would bump one. The
    mirror stamps `updated_at` only when its own guard tuple moved, and that
    tuple is (status, serial, note, error, claimed_at, used_at, seller) -
    which is every sheet-driven field this API publishes. So a stamp that
    moved means something a client can see moved, and one that did not
    means a credential was rotated, which no client is told anyway.
    """
    stamp = row.get("updated_at")
    raw = f"{stamp.isoformat() if stamp else ''}|{int(row['id'])}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode(cursor: str) -> tuple[str, int] | None:
    """The pair back, or None for anything that is not one of ours. A
    client that invents a cursor gets the first page, not a traceback."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        stamp, _, ident = raw.rpartition("|")
        return stamp, int(ident)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        log.debug("a cursor was not one of ours (%s)", exc)
        return None


def account(settings: Settings, ref: str) -> dict | None:
    """One account by its ref - the panel's own, or the farm-issued one."""
    wanted = str(ref or "").strip()
    if not wanted:
        return None
    ident = 0
    if wanted.startswith("farm_") and wanted[5:].isdigit():
        ident = int(wanted[5:])
    with Store(settings) as store:
        rows = store._rows(
            f"SELECT {_ACCOUNT_COLUMNS} FROM resources r"
            " WHERE r.kind = 'app' AND (r.panel_ref = %s OR r.id = %s)"
            " LIMIT 1", (wanted, ident))
    return rows[0] if rows else None


def accounts(settings: Settings, *, state: str = "", cursor: str = "",
             limit: int = 100) -> dict:
    """A page of accounts, newest change first.

    Keyset, not OFFSET: a client walking the list while the farm works
    would see rows twice or not at all under an offset, and the cursor is
    exactly the pair the index is on.
    """
    limit = max(1, min(int(limit or 100), 500))
    after = _decode(cursor) if cursor else None
    where = ["r.kind = 'app'"]
    params: list = []
    if after:
        where.append("(r.updated_at, r.id) < (%s, %s)")
        params += [after[0] or None, after[1]]
    with Store(settings) as store:
        rows = store._rows(
            f"SELECT {_ACCOUNT_COLUMNS} FROM resources r"
            f" WHERE {' AND '.join(where)}"
            " ORDER BY r.updated_at DESC, r.id DESC LIMIT %s",
            (*params, limit + 1))
    # `state` is a view over several columns, so it cannot be a WHERE; the
    # filter happens here, and the page is still bounded by the same limit.
    if state:
        rows = [r for r in rows if state_of(r) == state]
    more = len(rows) > limit
    rows = rows[:limit]
    return {"rows": rows, "more": more,
            "next_cursor": _cursor(rows[-1]) if rows and more else None}


def events(settings: Settings, row: dict) -> list[dict]:
    """Everything recorded about one account, oldest first.

    Two sources, joined on the address the way the phone story joins on a
    serial: the requests somebody made about it, and the events a pass
    recorded against it. Sparse today - nothing emits an `account` event
    yet - and it fills in on its own as the writers arrive.
    """
    address = str(row.get("address") or "")
    if not address:
        return []
    like = f"%{address}%"
    with Store(settings) as store:
        asked = store._rows(
            "SELECT a.id, a.verb, a.status, a.result, a.requested_at AS at,"
            " coalesce(u.username, c.name, '?') AS who"
            " FROM actions a LEFT JOIN users u ON u.id = a.requested_by"
            " LEFT JOIN api_clients c ON c.id = a.client_id"
            " WHERE a.payload::text ILIKE %s ORDER BY a.id", (like,))
        seen = store._rows(
            "SELECT id, at, kind, status, serial, detail FROM events"
            " WHERE kind = 'account' AND detail ILIKE %s ORDER BY id",
            (like,))
    out = [{"at": r["at"], "type": "request", "verb": r["verb"],
            "status": r["status"], "result": r["result"], "by": r["who"]}
           for r in asked]
    out += [{"at": r["at"], "type": "account", "status": r["status"],
             "phone": r["serial"], "detail": r["detail"], "by": "farm"}
            for r in seen]
    out.sort(key=lambda e: (e["at"] is not None, e["at"]))
    return out


def health(settings: Settings) -> dict:
    """Liveness, which kinds are actually served, and how much warm stock
    there is - one round trip, the way nav_counts does it."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT count(*) FILTER (WHERE kind = 'app') AS accounts,"
            " (SELECT value FROM service_state WHERE key = 'pass') AS pulse"
            " FROM resources")
    counts = dict(rows[0]) if rows else {"accounts": 0, "pulse": None}
    pulse = counts.get("pulse") or {}
    from .api_v1 import NOT_MEASURED

    return {"ok": True,
            "served": {name: list(kinds) for name, kinds in SERVED.items()},
            "not_measured": list(NOT_MEASURED),
            "accounts": int(counts.get("accounts") or 0),
            "warm_phones": int(pulse.get("warm") or 0)}
