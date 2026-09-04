"""The machine-facing door: /api/v1, on the console's own listener.

Same process, same port, same store - a bearer key instead of a session
cookie, JSON instead of HTML, and its own error handling so that nothing
here can reach the console's pages. A second daemon for a handful of
routes would be a second thing to keep alive.

Stage A is reads. The verbs that hand the farm an account arrive behind
their own switch, the way WEB_MUTATIONS came after WEB_ENABLED: reads are
observable and reversible, writes touch stock and cost money.

Three rules this module keeps that the console's own path does not need:

* **Authenticate before touching the store.** Every route, `/health`
  included. The listener's thread pool is unbounded, so an unauthenticated
  flood that reached Postgres would be a way to open connections for free.
* **Say nothing when switched off.** A 404, not a 403 - the same reasoning
  the admin-only pages already use. A 403 tells an unknown caller that
  there is something here to come back for.
* **Never read a key from the query string.** Only `Authorization`. A key
  in a URL lands in access logs, in Referer headers and in browser history.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs

from . import api_v1_read as api_read

log = logging.getLogger(__name__)

#: Five wrong keys buys this many seconds of "try later", per key prefix.
#: The console's own lockout is per username and cannot see these.
LOCKOUT_AFTER = 5
LOCKOUT_SECONDS = 600

#: How many characters of a token are kept beside its hash, so two keys can
#: be told apart on a page that never holds one.
PREFIX_LEN = 8

_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def hash_key(token: str) -> bytes:
    """A key's hash. Plain SHA-256, not the scrypt the users table uses.

    A key is 32 random bytes this program minted, not eight characters a
    person chose, so there is no dictionary to run against it - and this
    hash is computed on every request where a password's is computed twice
    a day. Slowing it down would buy nothing and cost the panel's polling.
    """
    return hashlib.sha256(token.encode("utf-8")).digest()


def rfc3339(value) -> str | None:
    """A stamp the way the contract promises it: UTC, Z-suffixed.

    Local, not `pages._moment`, which converts to the owner's Tehran clock
    - right for a page a person reads, wrong for a field a machine parses.
    A naive datetime is read as UTC, which is what the store stores.
    """
    if not value:
        return None
    if isinstance(value, str):
        return value
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------- the answers
def _json(handler, code: int, obj: dict, *, headers=()) -> None:
    """The one way this module replies. Mirrors app._text: nosniff so a
    browser cannot be talked into rendering it, no-store because every
    answer is somebody's account, an explicit length, and the HEAD guard
    the stdlib does not apply for us."""
    data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Cache-Control", "no-store")
    for name, value in headers:
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(data)


def _error(handler, code: int, kind: str, message: str, **extra) -> None:
    """One shape for every failure, so a client writes one branch."""
    headers = ((("WWW-Authenticate", 'Bearer realm="geelark farm"'),)
               if code == 401 else ())
    _json(handler, code, {"error": {"code": kind, "message": message,
                                    **extra}}, headers=headers)


# --------------------------------------------------------------- the caller
def _bearer(handler) -> str:
    """The token out of the Authorization header, or ''. Header only."""
    said = handler.headers.get("Authorization") or ""
    scheme, _, token = said.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _locked(prefix: str) -> bool:
    """Whether this key prefix has been wrong too often lately."""
    with _lock:
        recent = [t for t in _failures.get(prefix, [])
                  if time.time() - t < LOCKOUT_SECONDS]
        _failures[prefix] = recent
        return len(recent) >= LOCKOUT_AFTER


def _wrong(prefix: str) -> None:
    with _lock:
        _failures.setdefault(prefix, []).append(time.time())


def client_for(settings, token: str) -> dict | None:
    """The client this key belongs to, or None.

    The hash is the lookup, so the database does the constant-time work in
    an index; `compare_digest` on the way out is belt and braces against a
    future where the lookup becomes a scan.
    """
    from ..store.db import Store

    digest = hash_key(token)
    with Store(settings) as store:
        rows = store._rows(
            "SELECT id, name, role, key_hash, webhook_url, active"
            " FROM api_clients WHERE key_hash = %s AND active LIMIT 1",
            (digest,))
    if not rows:
        return None
    row = rows[0]
    return row if hmac.compare_digest(bytes(row["key_hash"]), digest) else None


def _seen(settings, client_id: int) -> None:
    """When this key was last used, for the page that lists them. Never
    fatal: an answer already computed must not be lost to a bookkeeping
    write."""
    from ..store.db import connect

    try:
        with connect(settings) as conn:
            conn.execute("UPDATE api_clients SET last_seen_at = now()"
                         " WHERE id = %s", (client_id,))
            conn.commit()
    except Exception as exc:                                      # noqa: BLE001
        log.debug("api client %s: last_seen not stamped (%s)", client_id, exc)


# ------------------------------------------------------------ the shapes out
def account_json(row: dict) -> dict:
    """One account, the way the contract spells it. Credentials go in and
    never come out: `email` is the only identifying field echoed back."""
    state = api_read.state_of(row)
    return {
        "ref": api_read.ref_of(row),
        "product": str(row.get("product") or "") or None,
        "credential_kind": str(row.get("credential_kind") or "") or None,
        "state": state,
        "email": row.get("address"),
        "phone": str(row.get("serial") or "") or None,
        "attempts": int(row.get("attempts") or 0),
        "failures": int(row.get("failures") or 0),
        "reason": (str(row.get("status") or "")
                   if state == "needs_human" else None),
        "reason_text": _reason_text(row) if state == "needs_human" else None,
        "blocked": api_read.blocked_of(row),
        "invalid": row.get("error") if state == "invalid" else None,
        "source": str(row.get("source") or ""),
        "received_at": rfc3339(row.get("created_at")),
        "state_changed_at": rfc3339(row.get("state_changed_at")
                                    or row.get("updated_at")),
        "delivered_at": rfc3339(row.get("delivered_at")),
        "updated_at": rfc3339(row.get("updated_at")),
    }


def _reason_text(row: dict) -> str:
    """The verdict's own sentence for a set-aside account - the same words
    the console shows, so a customer is never told two stories."""
    from ..failures import knows, verdict

    status = str(row.get("status") or "")
    if not knows(status):
        return str(row.get("note") or "")
    return verdict(status).seen


# ----------------------------------------------------------------- the door
def dispatch(handler, path: str) -> None:
    """Every /api/ request, from the first line of do_GET.

    Owns its own failures: an exception here answers JSON and is logged,
    and never reaches the console's HTML handler.
    """
    settings = handler.settings
    if not (settings.store_enabled and getattr(settings, "web_api", False)):
        # Switched off says nothing about what exists.
        return _error(handler, 404, "not_found", "no such endpoint")
    try:
        return _serve(handler, settings, path)
    except Exception as exc:                                      # noqa: BLE001
        from .app import _store_down

        if _store_down(exc):
            log.warning("api: %s - the store is not answering (%s)",
                        path, exc)
            return _error(handler, 503, "unavailable",
                          "the store is not answering; nothing was read")
        log.exception("api: %s failed", path)
        return _error(handler, 500, "internal", "it is in the server log")


def _serve(handler, settings, path: str) -> None:
    if handler.command not in ("GET", "HEAD"):
        # Stage A is reads. A write arrives with its own switch.
        return _error(handler, 405, "not_allowed", "this door reads only")
    token = _bearer(handler)
    prefix = token[:PREFIX_LEN]
    if not token:
        return _error(handler, 401, "unauthorized", "a bearer key is needed")
    if _locked(prefix):
        return _error(handler, 429, "rate_limited",
                      "too many wrong keys; try later")
    client = client_for(settings, token)
    if client is None:
        _wrong(prefix)
        return _error(handler, 401, "unauthorized", "that key is not one of ours")
    _seen(settings, client["id"])

    query = parse_qs(handler.path.partition("?")[2])
    first = {k: v[0] for k, v in query.items()}
    rest = path[len("/api/v1"):] if path.startswith("/api/v1") else ""

    if rest == "/health":
        return _json(handler, 200, api_read.health(settings))
    if rest == "/accounts":
        page = api_read.accounts(
            settings, state=first.get("state", ""),
            cursor=first.get("cursor", ""), limit=_limit(first))
        return _json(handler, 200, {
            "accounts": [account_json(r) for r in page["rows"]],
            "next_cursor": page["next_cursor"]})
    if rest.startswith("/accounts/"):
        ref, _, tail = rest[len("/accounts/"):].partition("/")
        # The tail is checked first: a path nobody serves must not cost a
        # store read, or an unknown URL is a way to make the farm work.
        if tail not in ("", "events"):
            return _error(handler, 404, "not_found", "no such endpoint")
        row = api_read.account(settings, ref)
        if row is None:
            return _error(handler, 404, "not_found", "no account with that ref")
        if not tail:
            return _json(handler, 200, account_json(row))
        return _json(handler, 200, {"events": [
            {**e, "at": rfc3339(e.get("at"))}
            for e in api_read.events(settings, row)]})
    return _error(handler, 404, "not_found", "no such endpoint")


def _limit(first: dict) -> int:
    """A limit a client asked for, or the default. Anything that is not a
    number is the default, not an error - a page size is not worth a 422."""
    said = str(first.get("limit") or "").strip()
    return int(said) if said.isdigit() and said != "0" else 100


def mint_key() -> tuple[str, bytes, str]:
    """A new key: the token to hand over once, its hash to store, and the
    prefix to show. The token is never stored and cannot be recovered."""
    import secrets

    token = secrets.token_urlsafe(32)
    return token, hash_key(token), token[:PREFIX_LEN]
