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
        "withdrawn_at": rfc3339(row.get("withdrawn_at")),
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
    # Read whatever was sent before answering anything. A body left in the
    # socket while the reply goes out aborts the connection on Windows and
    # confuses keep-alive everywhere else (2026-09-05).
    handler.api_body = _drain(handler)
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

    if handler.command in ("POST", "DELETE"):
        if not getattr(settings, "web_api_writes", False):
            return _error(handler, 405, "not_allowed",
                          "this door reads only")
        if client["role"] != "panel":
            return _error(handler, 403, "forbidden",
                          "this key may not change accounts")
        return _write(handler, settings, client, rest)
    if handler.command not in ("GET", "HEAD"):
        return _error(handler, 405, "not_allowed", "no such method here")

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


# ------------------------------------------------------------- the writes
#: How much of a body this door will read. A client with something larger
#: to say is saying it wrong, and an unbounded read on a thread per
#: connection is a way to spend this box's memory.
MAX_BODY = 64 * 1024


def _drain(handler) -> bytes:
    """Everything the client sent, read off the socket at once and kept.

    Read even when the answer will not need it - a request body left
    unread is a connection the client cannot reuse and, on Windows, one
    the reply never reaches. Bounded: what is over the limit is read and
    thrown away, so the socket is clean and the answer is still a 422.
    """
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return b""
    raw = handler.rfile.read(min(length, MAX_BODY + 1))
    left = length - len(raw)
    while left > 0:                          # over the limit: drain it away
        chunk = handler.rfile.read(min(left, 64 * 1024))
        if not chunk:
            break
        left -= len(chunk)
    return raw


def _body(handler) -> dict:
    """The JSON a client sent, or Refused. Bounded, and an object at the
    top level - a bare list or a number is not a request this door knows."""
    from .api_v1_write import Refused

    raw = getattr(handler, "api_body", b"")
    if len(raw) > MAX_BODY:
        raise Refused(f"at most {MAX_BODY} bytes", "body")
    try:
        got = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError) as exc:
        raise Refused(f"JSON: {exc}", "body") from exc
    if not isinstance(got, dict):
        raise Refused("an object", "body")
    return got


def _write(handler, settings, client: dict, rest: str) -> None:
    """POST and DELETE, with the idempotency wrapper around all of them.

    The key is read first and answered from the store when it has been
    seen: a retry is one request, byte for byte, which is the whole point
    of the header. Without one the write still runs - a client that does
    not send a key gets no protection, and saying so with a 400 would
    stop a panel that simply has not added it yet.
    """
    from . import api_v1_write as api_write

    key = (handler.headers.get("Idempotency-Key") or "").strip()[:200]
    if key:
        seen = api_write.replay(settings, client_id=client["id"], key=key)
        if seen is not None:
            return _json(handler, seen["status"], seen["body"],
                         headers=(("Idempotent-Replayed", "true"),))
    try:
        code, body = _do_write(handler, settings, client, rest)
    except api_write.Refused as exc:
        return _error(handler, 422, "invalid", str(exc), field=exc.field)
    if key and code < 500:
        api_write.remember(settings, client_id=client["id"], key=key,
                           method=handler.command, path=rest,
                           status=code, body=body)
    return _json(handler, code, body)


def _do_write(handler, settings, client: dict, rest: str):
    """One write, as (status, body). Raises Refused for a bad payload."""
    from . import api_v1_write as api_write

    if rest == "/accounts" and handler.command == "POST":
        row = api_write.judge(_body(handler))
        made = api_write.create(settings, row, client_id=client["id"])
        if isinstance(made, str):
            which = "ref" if made == "already_ref" else "address"
            return 409, {"error": {
                "code": "already_exists",
                "message": f"an account with that {which} is already here"}}
        api_write.enqueue(
            settings, verb="add_panel_account", payload={"ref": row["panel_ref"]},
            client_id=client["id"],
            idem=f"add:{row['panel_ref']}")
        return 201, account_json(made)

    if rest.startswith("/accounts/"):
        ref, _, tail = rest[len("/accounts/"):].partition("/")
        row = api_read.account(settings, ref)
        if row is None or not row.get("panel_ref"):
            # A farm-issued ref names a row the sheet owns; the panel may
            # read those and may not change them.
            return 404, {"error": {"code": "not_found",
                                   "message": "no account of yours with that ref"}}
        panel_ref = str(row["panel_ref"])
        if tail == "ready" and handler.command == "POST":
            state = api_read.state_of(row)
            if state not in ("waiting_customer", "needs_human"):
                return 409, {"error": {"code": "invalid_state",
                                       "message": "it is not waiting for anybody",
                                       "state": state}}
            api_write.mark_ready(settings, panel_ref)
            return 202, account_json(api_read.account(settings, panel_ref))
        if not tail and handler.command == "DELETE":
            state = api_read.state_of(row)
            if state in ("signing_in", "ready", "delivered", "withdrawn"):
                return 409, {"error": {"code": "invalid_state",
                                       "message": "too late to take it back",
                                       "state": state}}
            api_write.mark_withdrawn(settings, panel_ref)
            api_write.enqueue(settings, verb="withdraw_panel_account",
                              payload={"ref": panel_ref},
                              client_id=client["id"],
                              idem=f"withdraw:{panel_ref}")
            return 200, account_json(api_read.account(settings, panel_ref))
    return 404, {"error": {"code": "not_found", "message": "no such endpoint"}}
