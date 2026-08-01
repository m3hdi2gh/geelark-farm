"""Signed HTTP transport for the GeeLark open API.

Everything above this module talks in endpoints and payloads; the signing,
rate limiting and retry policy live here.

Three rules this module exists to enforce:

1. Every request carries a fresh signature:
   `sign = SHA256(appId + traceId + ts + nonce + apiKey)` as uppercase hex.
2. The 200 req/min limit is a *process-wide* budget, and exceeding it bans the
   key for two hours. One limiter, shared by every caller and every worker.
3. A non-zero response code is an error, raised as ApiError. Callers never
   unwrap envelopes or check codes themselves.

See docs/geelark-api.md for the endpoint quirks behind these choices.
"""

from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
import uuid
from collections import deque
from typing import Any

import requests

from .config import Settings

log = logging.getLogger(__name__)

BASE_URL = "https://openapi.geelark.com/open"

# Failure codes worth explaining in the exception message. Only codes actually
# observed are listed - a guessed mapping is worse than none.
KNOWN_CODES = {
    20002: "the phone is already running another task "
           "(only one RPA task per phone at a time)",
    20008: "an element was not found, commonly because the phone's UI is not "
           "in English (mobileLanguage must be 'default')",
    40003: "signature rejected - check GEELARK_APP_ID and GEELARK_API_KEY",
    44002: "the GeeLark plan is full: no slots left for another phone. "
           "Delete phones you have finished with, or raise the plan. Rows "
           "already done are unaffected; re-run to pick up the rest.",
}

# Endpoints that only read. A timed-out write may have been applied server
# side, so only these are retried automatically; a caller who knows better can
# pass retry=True explicitly (e.g. a read-only shell command).
RETRY_SAFE_PATHS = (
    "/v1/phone/list",
    "/v1/phone/status",
    "/v1/phone/detail",
    "/v1/task/query",
    "/v1/task/detail",
    "/v1/proxy/check",
    "/v1/proxy/list",
)


class ApiError(Exception):
    """The API answered with a non-zero code."""

    def __init__(self, code: int | None, msg: str, *, path: str, trace_id: str,
                 data: Any = None):
        self.code = code
        self.msg = msg
        self.path = path
        self.trace_id = trace_id
        self.data = data
        detail = KNOWN_CODES.get(code)
        text = f"[{code}] {msg} ({path})"
        if detail:
            text += f"\n  -> {detail}"
        text += f"\n  traceId {trace_id}"
        super().__init__(text)


class TransportError(Exception):
    """The request never produced an answer, even after retries."""


class RateLimiter:
    """Sliding-window limiter shared by the whole process.

    GeeLark allows 200 requests/minute and bans the key for two hours when that
    is exceeded, so this blocks rather than rejecting: waiting a second is
    always better than losing two hours. Thread-safe, because concurrency
    across phones is the plan and they all draw from one budget.
    """

    def __init__(self, per_minute: int, window: float = 60.0):
        if per_minute < 1:
            raise ValueError("per_minute must be >= 1")
        self.capacity = per_minute
        self.window = window
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, *, now: float | None = None) -> float:
        """Reserve one slot, sleeping if the window is full.

        Returns how long it waited, which is worth logging: sustained waiting
        means the caller is polling too aggressively.
        """
        waited = 0.0
        while True:
            with self._lock:
                current = now if now is not None else time.monotonic()
                while self._hits and current - self._hits[0] >= self.window:
                    self._hits.popleft()
                if len(self._hits) < self.capacity:
                    self._hits.append(current)
                    return waited
                sleep_for = self.window - (current - self._hits[0])
            # Sleep outside the lock so other threads can still drain the
            # window while this one waits.
            sleep_for = max(sleep_for, 0.01)
            if now is not None:      # deterministic mode, for tests
                return waited + sleep_for
            log.debug("rate limit reached, waiting %.2fs", sleep_for)
            time.sleep(sleep_for)
            waited += sleep_for


class Client:
    """A signed, rate-limited client for one GeeLark account.

    Each thread gets its own `requests.Session`. A Session is not thread-safe -
    its connection pool is shared mutable state - and running three rows at once
    against one Session produced
    `ConnectionResetError(10054, 'An existing connection was forcibly closed')`
    mid-run (measured 2026-08-01). Sessions are kept per thread rather than
    dropped entirely so connection reuse still works within a row.
    """

    def __init__(self, settings: Settings, *, limiter: RateLimiter | None = None,
                 session: requests.Session | None = None):
        self.settings = settings
        self.limiter = limiter or RateLimiter(settings.api_requests_per_minute)
        self._shared_session = session      # tests may inject one
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        if self._shared_session is not None:
            return self._shared_session
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            self._local.session = session
        return session

    # ------------------------------------------------------------- signing
    def auth_headers(self, *, trace_id: str | None = None,
                     ts: str | None = None) -> dict[str, str]:
        """Build the per-request auth headers.

        traceId and ts are injectable so the signature can be tested against a
        fixed vector; in normal use both are generated here.
        """
        trace_id = trace_id or str(uuid.uuid4()).upper()
        ts = ts or str(int(time.time() * 1000))
        nonce = trace_id[:6]
        sign = hashlib.sha256(
            f"{self.settings.app_id}{trace_id}{ts}{nonce}"
            f"{self.settings.api_key}".encode()
        ).hexdigest().upper()
        return {
            "Content-Type": "application/json",
            "appId": self.settings.app_id,
            "traceId": trace_id,
            "ts": ts,
            "nonce": nonce,
            "sign": sign,
        }

    # ------------------------------------------------------------ requests
    def post(self, path: str, payload: dict | None = None, *,
             strict: bool = True, retry: bool | None = None,
             timeout: float = 90.0, attempts: int = 3) -> dict:
        """POST to `path` and return the parsed envelope.

        strict=False returns the envelope even on a non-zero code, for calls
        whose failure is acceptable (stopping a phone that is already stopped).
        retry defaults to True only for read-only endpoints.
        """
        if retry is None:
            retry = path in RETRY_SAFE_PATHS
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            headers = self.auth_headers()
            trace_id = headers["traceId"]
            waited = self.limiter.acquire()
            if waited > 1:
                log.info("waited %.1fs for rate limit before %s", waited, path)

            try:
                response = self.session.post(
                    f"{BASE_URL}{path}", headers=headers,
                    json=payload or {}, timeout=timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                if not retry or attempt == attempts:
                    raise TransportError(
                        f"{path} failed after {attempt} attempt(s): {exc}"
                    ) from exc
                self._backoff(attempt, path, exc)
                continue

            # 5xx is the server's problem and safe to repeat; 4xx is ours.
            if response.status_code >= 500:
                last_error = requests.HTTPError(
                    f"HTTP {response.status_code}", response=response
                )
                if not retry or attempt == attempts:
                    raise TransportError(f"{path}: HTTP {response.status_code}")
                self._backoff(attempt, path, last_error)
                continue

            # Everything from here on is still requests' territory, so it is
            # wrapped too: a raw RequestException escaping this method would
            # bypass every caller's error handling, and in a batch that means a
            # row dying without its reason ever reaching the sheet.
            try:
                response.raise_for_status()
                body = response.json()
            except requests.RequestException as exc:
                raise TransportError(f"{path}: {exc}") from exc
            except ValueError as exc:
                raise TransportError(
                    f"{path}: response was not JSON ({response.text[:200]!r})"
                ) from exc
            code = body.get("code")
            if code != 0 and strict:
                raise ApiError(code, body.get("msg", ""), path=path,
                               trace_id=trace_id, data=body.get("data"))
            return body

        raise TransportError(f"{path}: exhausted retries ({last_error})")

    @staticmethod
    def _backoff(attempt: int, path: str, error: Exception) -> None:
        """Exponential backoff with jitter, so parallel workers recovering from
        the same outage do not resynchronise into a thundering herd."""
        delay = min(2 ** attempt, 8) + random.uniform(0, 0.5)
        log.warning("%s failed (%s); retrying in %.1fs", path, error, delay)
        time.sleep(delay)

    # ------------------------------------------------------------ shortcuts
    def data(self, path: str, payload: dict | None = None, **kwargs) -> Any:
        """post() but returning the 'data' member, which is what callers want
        in almost every case."""
        return self.post(path, payload, **kwargs).get("data")


def build_client(settings: Settings | None = None) -> Client:
    return Client(settings or Settings.load())
