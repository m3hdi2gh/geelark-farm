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
#: One key's request stamps inside the last `WINDOW` seconds. The rate
#: limit the contract promised and nothing enforced (2026-09-12).
_calls: dict[int, list[float]] = {}
WINDOW = 60.0
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


def _error(handler, code: int, kind: str, message: str, *,
           retry_after: int | None = None, **extra) -> None:
    """One shape for every failure, so a client writes one branch.

    `retry_after` is keyword-only and becomes a header, not a field: it is
    the one part of a refusal a client is expected to obey mechanically,
    and `**extra` lands inside the error object where a header cannot be
    seen (2026-09-12).
    """
    headers = ((("WWW-Authenticate", 'Bearer realm="geelark farm"'),)
               if code == 401 else ())
    if retry_after is not None:
        headers = (*headers, ("Retry-After", str(max(1, int(retry_after)))))
    _json(handler, code, {"error": {"code": kind, "message": message,
                                    **extra}}, headers=headers)


# --------------------------------------------------------------- the caller
def _bearer(handler) -> str:
    """The token out of the Authorization header, or ''. Header only."""
    said = handler.headers.get("Authorization") or ""
    scheme, _, token = said.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _locked(prefix: str) -> int:
    """Seconds this key prefix must wait for having been wrong too often,
    or 0. A number rather than a flag, so the 429 can say when to come
    back instead of leaving a client to guess (2026-09-12)."""
    now = time.time()
    with _lock:
        recent = [t for t in _failures.get(prefix, [])
                  if now - t < LOCKOUT_SECONDS]
        _failures[prefix] = recent
        if len(recent) < LOCKOUT_AFTER:
            return 0
    return max(1, int(LOCKOUT_SECONDS - (now - recent[0])) + 1)


def _wrong(prefix: str) -> None:
    with _lock:
        _failures.setdefault(prefix, []).append(time.time())


def _too_fast(client_id: int, limit: int) -> int:
    """Seconds to wait, or 0. One key's requests in the last minute.

    Kept in this process's memory, like the lockout above, because one
    process serves this door: `geelark-web` has a fixed container name and
    cannot be scaled, and neither the keeper nor a builder starts a
    listener. A second web would each enforce the limit on its own share,
    which is the honest failure mode for a brake (2026-09-12).
    """
    if limit <= 0:
        return 0
    now = time.time()
    with _lock:
        recent = [t for t in _calls.get(client_id, []) if now - t < WINDOW]
        if len(recent) >= limit:
            _calls[client_id] = recent
            return max(1, int(WINDOW - (now - recent[0])) + 1)
        recent.append(now)
        _calls[client_id] = recent
    return 0


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
def account_json(row: dict, *, sandbox: bool = False) -> dict:
    """One account, the way the contract spells it. Credentials go in and
    never come out: `email` is the only identifying field echoed back.

    `sandbox` is in every object rather than only in `/health`, so a
    client that is pointed at the wrong key can see it in the first
    answer it reads instead of wondering why no phone ever arrives.
    """
    state = api_read.state_of(row)
    return {
        "sandbox": bool(sandbox),
        "ref": api_read.ref_of(row),
        "product": str(row.get("product") or "") or None,
        "credential_kind": str(row.get("credential_kind") or "") or None,
        "state": state,
        "email": row.get("address"),
        "phone": str(row.get("serial") or "") or None,
        # Real numbers since 2026-09-12: the pool counts an attempt in
        # the statement that puts the account on a phone and a failure
        # when a run judged the account itself. Until then both were
        # published null - "not counting" rather than "none" - and
        # /health still names whatever is left in that state.
        "attempts": _measured(row, "attempts"),
        "failures": _measured(row, "failures"),
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


#: Columns the schema has and nothing yet writes. Published as null
#: rather than as their zero, and named in /health so a client can tell
#: "not counted" from "counted, and it is none".
#:
#: Empty since 2026-09-12, and kept as the mechanism rather than deleted:
#: the next field the contract promises before the farm writes it goes
#: here, and both `_measured` and /health follow it without another edit.
NOT_MEASURED: tuple[str, ...] = ()


def _measured(row: dict, field: str):
    """The number, or None while nothing is counting it."""
    return None if field in NOT_MEASURED else int(row.get(field) or 0)


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
    locked = _locked(prefix)
    if locked:
        return _error(handler, 429, "rate_limited",
                      "too many wrong keys; try later", retry_after=locked)
    client = client_for(settings, token)
    if client is None:
        _wrong(prefix)
        return _error(handler, 401, "unauthorized", "that key is not one of ours")
    wait = _too_fast(client["id"],
                     int(getattr(settings, "web_api_rate_per_minute", 600)))
    if wait:
        # Before `_seen`, so a client hammering the door does not also
        # write a row a second: the point of the brake is that a refusal
        # is cheap for us.
        return _error(handler, 429, "rate_limited",
                      "too many requests for this key; try later",
                      retry_after=wait)
    _seen(settings, client["id"])

    query = parse_qs(handler.path.partition("?")[2])
    first = {k: v[0] for k, v in query.items()}
    rest = path[len("/api/v1"):] if path.startswith("/api/v1") else ""

    if handler.command in ("POST", "DELETE"):
        if client["role"] not in ("panel", "sandbox"):
            return _error(handler, 403, "forbidden",
                          "this key may not change accounts")
        # The switch guards what a write costs, and a practice write costs
        # nothing: it builds no phone, spends no Gmail and no exit, and
        # touches a table no pool can see. Holding the author of the panel
        # behind it would leave him unable to write his client at all -
        # which is why the door has stayed read-only since it was built
        # (2026-09-11).
        if not is_sandbox(client) and not getattr(settings, "web_api_writes",
                                                  False):
            return _error(handler, 405, "not_allowed",
                          "this door reads only")
        return _write(handler, settings, client, rest)
    if handler.command not in ("GET", "HEAD"):
        return _error(handler, 405, "not_allowed", "no such method here")

    box = is_sandbox(client)
    if rest == "/health":
        return _json(handler, 200, api_read.health(settings, sandbox=box))
    if rest == "/accounts":
        page = api_read.accounts(
            settings, state=first.get("state", ""),
            cursor=first.get("cursor", ""), limit=_limit(first), sandbox=box)
        return _json(handler, 200, {
            "accounts": [account_json(r, sandbox=box) for r in page["rows"]],
            "next_cursor": page["next_cursor"]})
    if rest.startswith("/accounts/"):
        ref, _, tail = rest[len("/accounts/"):].partition("/")
        # The tail is checked first: a path nobody serves must not cost a
        # store read, or an unknown URL is a way to make the farm work.
        if tail not in ("", "events"):
            return _error(handler, 404, "not_found", "no such endpoint")
        row = api_read.account(settings, ref, sandbox=box)
        if row is None:
            return _error(handler, 404, "not_found", "no account with that ref")
        if not tail:
            return _json(handler, 200, account_json(row, sandbox=box))
        if box:
            # Nothing happens to a practice row that anybody recorded: no
            # request took it, no pass judged it. An empty list is the
            # truth, and it keeps a client's paging code honest.
            return _json(handler, 200, {"events": []})
        return _json(handler, 200, {"events": [
            {**e, "at": rfc3339(e.get("at"))}
            for e in api_read.events(settings, row)]})
    return _error(handler, 404, "not_found", "no such endpoint")


def is_sandbox(client: dict) -> bool:
    """Whether this key lives in the practice room. One reading of the
    role, so no route can forget which world it is answering about."""
    return str(client.get("role") or "") == "sandbox"


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
        try:
            seen = api_write.replay(settings, client_id=client["id"], key=key,
                                    method=handler.command, path=rest)
        except api_write.Reused as exc:
            return _error(handler, 409, "already_exists", str(exc))
        if seen is not None:
            return _json(handler, seen["status"], seen["body"],
                         headers=(("Idempotent-Replayed", "true"),))
    if rest == "/accounts" and handler.command == "POST":
        # The money brake, and the last place it can be applied without
        # having written anything: an account past here becomes a phone,
        # a Gmail and an exit within a pass. A sandbox key is exempt -
        # its accounts build nothing - and a refusal is never remembered
        # as an idempotent answer, so the same key works tomorrow.
        over = _over_the_day(settings, client)
        if over:
            return _error(handler, 429, "rate_limited", over[0],
                          retry_after=over[1])
    try:
        code, body = _do_write(handler, settings, client, rest)
    except api_write.Refused as exc:
        return _error(handler, 422, "invalid", str(exc), field=exc.field)
    if key and code < 500:
        api_write.remember(settings, client_id=client["id"], key=key,
                           method=handler.command, path=rest,
                           status=code, body=body)
    return _json(handler, code, body)


def _over_the_day(settings, client: dict) -> tuple[str, int] | None:
    """The sentence and the wait when this key has had its day's worth.

    Counted in the store rather than in memory, because the console is
    deployed by restarting this very process and a day's tally kept here
    would start again every time. The day is UTC, which is the day every
    stamp this API publishes is in.
    """
    cap = int(getattr(settings, "web_api_accounts_per_day", 100))
    if cap <= 0 or is_sandbox(client):
        return None
    try:
        from ..store.db import Store

        with Store(settings) as store:
            # The requests this key made, not the rows that survived them:
            # `DELETE /accounts/{ref}` archives the row it withdraws, and
            # so does the console's Remove, so a count of `resources`
            # gives the allowance back - POST, DELETE, repeat, and the
            # cap never trips (the review, 2026-09-12).
            rows = store._rows(
                "SELECT count(*) AS c FROM actions"
                " WHERE verb = 'add_panel_account' AND client_id = %s"
                "   AND requested_at >= date_trunc('day', now() AT TIME ZONE"
                "                                   'UTC')",
                (int(client["id"]),))
        made = int((rows[0] or {}).get("c") or 0) if rows else 0
    except Exception as exc:                                      # noqa: BLE001
        # A store that cannot answer is not a reason to refuse work: the
        # cap is a guard against a loop, not an authorisation check.
        log.warning("the day's tally for client %s could not be read (%s); "
                    "the account is taken", client.get("id"), exc)
        return None
    if made < cap:
        return None
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    midnight = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return (f"this key has handed the farm {made} accounts today, which is "
            f"its limit; the count starts again at midnight UTC",
            int((midnight - now).total_seconds()))


def _do_write(handler, settings, client: dict, rest: str):
    """One write, as (status, body). Raises Refused for a bad payload."""
    from . import api_sandbox
    from . import api_v1_write as api_write

    box = is_sandbox(client)
    if rest == "/accounts" and handler.command == "POST":
        row = api_write.judge(_body(handler))
        made = api_write.create(settings, row, client_id=client["id"],
                                sandbox=box)
        if isinstance(made, str):
            which = "ref" if made == "already_ref" else "address"
            return 409, {"error": {
                "code": "already_exists",
                "message": f"an account with that {which} is already here"}}
        if not box:
            # The half only a pass may do. A practice account never has
            # it done: no request, no tab, no phone - which is the whole
            # difference between the two worlds.
            api_write.enqueue(
                settings, verb="add_panel_account",
                payload={"ref": row["panel_ref"]},
                client_id=client["id"],
                idem=f"add:{row['panel_ref']}")
        return 201, account_json(made, sandbox=box)

    if rest.startswith("/accounts/"):
        ref, _, tail = rest[len("/accounts/"):].partition("/")
        row = api_read.account(settings, ref, sandbox=box)
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
            api_write.mark_ready(settings, panel_ref, sandbox=box)
            return 202, account_json(
                api_read.account(settings, panel_ref, sandbox=box), sandbox=box)
        if tail == "simulate" and handler.command == "POST":
            # The practice room's own verb, and the only route in this
            # door that a panel key may not reach: a state nobody worked
            # for is a lie everywhere else.
            if not box:
                return 404, {"error": {"code": "not_found",
                                       "message": "no such endpoint"}}
            body = _body(handler)
            try:
                api_sandbox.simulate(
                    settings, client_id=client["id"], ref=panel_ref,
                    state=str(body.get("state") or ""),
                    reason=str(body.get("reason") or ""))
            except api_sandbox.Refused as exc:
                raise api_write.Refused(str(exc), exc.field) from exc
            return 200, account_json(
                api_read.account(settings, panel_ref, sandbox=box), sandbox=box)
        if not tail and handler.command == "DELETE":
            state = api_read.state_of(row)
            if state in ("signing_in", "ready", "delivered", "withdrawn"):
                return 409, {"error": {"code": "invalid_state",
                                       "message": "too late to take it back",
                                       "state": state}}
            api_write.mark_withdrawn(settings, panel_ref, sandbox=box)
            if not box:
                api_write.enqueue(settings, verb="withdraw_panel_account",
                                  payload={"ref": panel_ref},
                                  client_id=client["id"],
                                  idem=f"withdraw:{panel_ref}")
            return 200, account_json(
                api_read.account(settings, panel_ref, sandbox=box), sandbox=box)
    return 404, {"error": {"code": "not_found", "message": "no such endpoint"}}
