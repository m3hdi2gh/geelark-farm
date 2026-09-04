"""What a client may change, and the judging it passes on the way in.

Three writes, and each one is careful about a different thing.

**Handing the farm an account** writes a `resources` row here AND queues a
request that carries it into the sheet. Both, on purpose: the sheet is
what the keeper reads, and only the pass may write it - but a panel that
POSTs and immediately GETs must not be told its account does not exist.
So the row is born in the store, and the pass adopts it into the tab a
moment later; the mirror's own ON CONFLICT (kind, lower(address)) then
recognises it and fills in the sheet_row, keeping the id and every column
this API owns.

**Saying a customer is ready** touches nothing the sheet knows, so it is a
plain store write with no request at all.

**Taking one back** has to reach the sheet, so it queues; the row is
stamped withdrawn here so a client sees the answer at once.

Credentials never ride in a request's payload. The console's own add does
that and it is why a password can be read off the Requests page; the
panel's path writes them to the row and hands the verb a reference.
"""

from __future__ import annotations

import json
import logging

from ..config import Settings
from ..store.db import connect
from . import api_v1_read as api_read

log = logging.getLogger(__name__)


class Refused(Exception):
    """A payload this door will not take. Carries the field, so the answer
    can say which one and the client can point at it."""

    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.field = field


#: What each kind must bring, and what it may. Judged before anything is
#: written, so a payload that fails leaves nothing behind for anyone to
#: poll - which is the difference between "refused" and "stuck".
_NEEDS = {
    "password_totp": ("email", "password"),
    "google_backup_codes": ("email", "password", "backup_codes"),
    "email_code_auto": ("email",),
    "email_code_customer": ("email",),
}


def judge(body: dict) -> dict:
    """The row a valid payload becomes, or Refused saying which field.

    Judged the way the sheet's own rows are - through validate.app_row,
    the same reader the paste box and the importer go through - so a
    secret that is not base32 or an address that is not one is refused
    here rather than discovered on a phone.
    """
    from ..store import validate

    ref = str(body.get("ref") or "").strip()
    if not ref or len(ref) > 64:
        raise Refused("a ref of 1 to 64 characters", "ref")
    product = str(body.get("product") or "").strip()
    if product not in api_read.PRODUCTS:
        raise Refused(f"one of {', '.join(api_read.PRODUCTS)}", "product")
    kind = str(body.get("credential_kind") or "").strip()
    if kind not in api_read.CREDENTIAL_KINDS:
        raise Refused(f"one of {', '.join(api_read.CREDENTIAL_KINDS)}",
                      "credential_kind")
    creds = body.get("credentials")
    if not isinstance(creds, dict):
        raise Refused("an object", "credentials")
    for field in _NEEDS[kind]:
        if not creds.get(field):
            raise Refused(f"{kind} needs it", f"credentials.{field}")
    codes = creds.get("backup_codes") or []
    if codes and not (isinstance(codes, list)
                      and all(isinstance(c, str) for c in codes)):
        raise Refused("a list of strings", "credentials.backup_codes")

    # The address and the secret go through the sheet's own reader, so
    # this door and the paste box refuse exactly the same rows.
    try:
        checked = validate.app_row(
            address=str(creds.get("email") or ""),
            password=str(creds.get("password") or ""),
            secret=str(creds.get("totp_secret") or ""),
            email_code_only=kind in ("email_code_auto",
                                     "email_code_customer"))
    except Exception as exc:                                      # noqa: BLE001
        raise Refused(str(exc), "credentials") from exc

    return {"panel_ref": ref, "product": product, "credential_kind": kind,
            "address": checked["address"], "password": checked["password"],
            "totp_secret": checked["totp_secret"],
            "email_code_only": checked["email_code_only"],
            "backup_codes": list(codes)}


def create(settings: Settings, row: dict, *, client_id: int) -> dict | str:
    """The row, born in the store. Returns it, or a token saying why not.

    Two identities can already be taken: the panel's own ref, and the
    address - the partial unique index the pool has always had, which is
    what stops the same account being bought twice.
    """
    with connect(settings) as conn:
        for column in ("panel_ref", "address"):
            taken = conn.execute(
                "SELECT 1 FROM resources WHERE kind = 'app'"
                f" AND lower({column}) = lower(%s) LIMIT 1", (row[column],)
            ).fetchall()
            if taken:
                return "already_ref" if column == "panel_ref" else "already_address"
        conn.execute(
            "INSERT INTO resources (kind, source, status, address, password,"
            " totp_secret, email_code_only, product, credential_kind,"
            " panel_ref, client_id, backup_codes)"
            " VALUES ('app', 'panel', '', %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (row["address"], row["password"], row["totp_secret"],
             row["email_code_only"], row["product"], row["credential_kind"],
             row["panel_ref"], client_id,
             json.dumps(row["backup_codes"]) if row["backup_codes"] else None))
        conn.commit()
    return api_read.account(settings, row["panel_ref"])


def mark_ready(settings: Settings, ref: str) -> None:
    """The customer is at their keyboard. A column this API owns, so no
    request and no pass: nothing about the sheet changes."""
    with connect(settings) as conn:
        conn.execute("UPDATE resources SET customer_ready = true,"
                     " state_changed_at = now(), updated_at = now()"
                     " WHERE kind = 'app' AND panel_ref = %s", (ref,))
        conn.commit()


def mark_withdrawn(settings: Settings, ref: str) -> None:
    """Stamped here so the client sees the answer at once; the request
    that follows takes the row out of the sheet."""
    with connect(settings) as conn:
        conn.execute("UPDATE resources SET withdrawn_at = now(),"
                     " state_changed_at = now(), updated_at = now()"
                     " WHERE kind = 'app' AND panel_ref = %s", (ref,))
        conn.commit()


# ------------------------------------------------------------- the queue
def enqueue(settings: Settings, *, verb: str, payload: dict,
            client_id: int, idem: str) -> int | None:
    """One request, asked by a machine.

    `requested_by` is NULL and `client_id` names the client instead - the
    queue learned that in stage A, in the deploy where nothing wrote one.
    The idempotency key is namespaced, because actions.idem_key is UNIQUE
    across the whole table and the console's own keys live there too.
    """
    with connect(settings) as conn:
        cur = conn.execute(
            "INSERT INTO actions (verb, payload, client_id, source, idem_key)"
            " VALUES (%s, %s, %s, 'panel', %s)"
            " ON CONFLICT (idem_key) DO NOTHING RETURNING id",
            (verb, json.dumps(payload), client_id, f"api:{client_id}:{idem}"))
        got = cur.fetchall()
        conn.commit()
    return int(got[0][0]) if got else None


# ------------------------------------------------------- idempotency
def replay(settings: Settings, *, client_id: int, key: str) -> dict | None:
    """What this client was told the first time it sent this key."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT status, body FROM api_idempotency"
            " WHERE client_id = %s AND key = %s", (client_id, key)).fetchall()
    return {"status": rows[0][0], "body": rows[0][1]} if rows else None


def remember(settings: Settings, *, client_id: int, key: str, method: str,
             path: str, status: int, body: dict) -> None:
    """The answer, kept so a retry is one request rather than two accounts.

    Never fatal: an answer already computed must not be lost because the
    bookkeeping failed. The cost of forgetting is a second POST that the
    address index refuses anyway.
    """
    try:
        with connect(settings) as conn:
            conn.execute(
                "INSERT INTO api_idempotency"
                " (client_id, key, method, path, status, body)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (client_id, key) DO NOTHING",
                (client_id, key, method, path, status, json.dumps(body)))
            conn.commit()
    except Exception as exc:                                      # noqa: BLE001
        log.warning("api: the answer to %s was not remembered (%s)", key, exc)
