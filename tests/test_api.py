"""The transport every GeeLark call goes through.

It covered the two pieces that are pure logic - the signature and the rate
limiter - and not the thing they exist to serve. `post` had no test at all:
not the retry ladder, not the envelope, not one successful call, and
`_backoff` was named nowhere in the repository (2026-08-25).

The limiter is worth testing because getting it wrong bans the API key for two
hours - a failure that cannot be discovered safely by trying it. The retry
ladder is worth testing for the mirror of that reason: a write repeated after
a timeout is a second phone nobody asked for and nobody is holding.

Three mutations survive here deliberately. `range(1, attempts + 2)` adds a
loop iteration nothing can enter, because every branch inside returns or
raises once `attempt == attempts`; and the two on `if waited > 1` move the
threshold of a log line. None of them changes what this module does.
"""

from __future__ import annotations

import pytest
import requests

from geelark_farm.api import (
    RETRY_SAFE_PATHS,
    ApiError,
    Client,
    RateLimiter,
    TransportError,
)


def test_signature_is_uppercase_sha256_of_the_documented_concatenation(make_settings):
    client = Client(make_settings(), limiter=RateLimiter(10))
    headers = client.auth_headers(trace_id="TRACE1", ts="1700000000000")

    import hashlib

    expected = hashlib.sha256(
        b"APPID" + b"TRACE1" + b"1700000000000" + b"TRACE1"[:6] + b"APIKEY"
    ).hexdigest().upper()

    assert headers["sign"] == expected
    assert headers["nonce"] == "TRACE1"[:6]
    assert headers["sign"].isupper()
    assert len(headers["sign"]) == 64


def test_signature_changes_every_call(make_settings):
    """A replayed signature would be indistinguishable from a stuck clock."""
    client = Client(make_settings(), limiter=RateLimiter(10))
    assert client.auth_headers()["sign"] != client.auth_headers()["sign"]


@pytest.fixture
def never_sleeps(monkeypatch):
    """Deterministic mode must not touch the clock at all.

    Asserted rather than assumed: `acquire` returns early only while it reads
    the `now` it was handed, and a change that lets it fall through to the
    real loop makes these tests sit out a whole sixty-second window instead of
    failing. That is a test suite that hangs rather than reports - and it hung
    a mutation run for exactly that reason (2026-08-25).
    """
    def caught(seconds):
        raise AssertionError(f"deterministic mode slept for {seconds}s")

    monkeypatch.setattr("geelark_farm.api.time.sleep", caught)


def test_limiter_allows_up_to_capacity_then_makes_the_caller_wait(never_sleeps):
    limiter = RateLimiter(per_minute=3, window=60.0)
    # Deterministic mode: passing `now` returns the wait instead of sleeping.
    assert limiter.acquire(now=100.0) == 0
    assert limiter.acquire(now=100.1) == 0
    assert limiter.acquire(now=100.2) == 0

    waited = limiter.acquire(now=100.3)
    assert waited == pytest.approx(59.7, abs=0.01)


def test_limiter_frees_slots_once_the_window_has_passed(never_sleeps):
    limiter = RateLimiter(per_minute=2, window=60.0)
    limiter.acquire(now=0.0)
    limiter.acquire(now=1.0)
    assert limiter.acquire(now=61.0) == 0     # the 0.0 hit has aged out


def test_only_read_only_endpoints_retry_by_default():
    """A timed-out write may already have been applied server side, so writes
    must never be repeated automatically."""
    assert "/v1/phone/list" in RETRY_SAFE_PATHS
    for mutating in ("/v1/phone/addNew", "/v1/phone/start", "/v1/phone/stop",
                     "/v1/shell/execute", "/v1/rpa/task/googleLogin"):
        assert mutating not in RETRY_SAFE_PATHS


def test_the_signature_is_made_after_the_wait_not_before(monkeypatch,
                                                        make_settings):
    """A signature carries the millisecond it was made in, and the limiter
    blocks - for up to a full window when the budget is spent. Signing first
    sends a timestamp as stale as the wait was long, which is what [40003]
    rejects.

    Invisible today: a local cap of 120 against a real 200 means the limiter
    almost never blocks. A service that never stops is what makes it block.
    """
    from geelark_farm import api

    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(api.time, "time", lambda: clock["now"])

    class SlowLimiter:
        def acquire(self):
            clock["now"] += 45          # a wait long enough to matter
            return 45.0

    sent = {}

    class Response:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"code": 0, "data": {}}

    class Session:
        def post(self, url, headers=None, json=None, timeout=None):
            sent["ts"] = int(headers["ts"])
            return Response()

    client = api.Client(make_settings(), limiter=SlowLimiter(),
                        session=Session())
    client.post("/v1/phone/list")

    # The timestamp is the moment the request actually left, not the moment
    # the caller asked for it 45 seconds earlier.
    assert sent["ts"] == int(clock["now"] * 1000)


# ==================================================================
# post() itself (2026-08-25). The signature and the limiter had
# tests; the thing they exist to serve had none - not the retry
# ladder, not the envelope, not one successful call. `_backoff` was
# named in no test in the repository.
# ==================================================================


class Reply:
    """As much of a requests Response as post() reads."""

    def __init__(self, body=None, *, status=200, text="", not_json=False):
        self.body = {"code": 0, "msg": "success"} if body is None else body
        self.status_code = status
        self.text = text
        self.not_json = not_json

    def raise_for_status(self):
        if 400 <= self.status_code < 500:
            raise requests.HTTPError(f"{self.status_code} Client Error",
                                     response=self)

    def json(self):
        if self.not_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self.body


class Line:
    """The network, as a queue of answers. An Exception is raised, anything
    else is returned - which is how a flaky connection is written down."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.sent.append({"url": url, "headers": headers or {},
                          "json": json, "timeout": timeout})
        if not self.answers:
            # Never a default. A fake that answers anyway turns "it tried one
            # more time than it should have" into a passing test, which is
            # the whole thing these tests are here to notice.
            raise AssertionError(
                f"asked for answer {len(self.sent)}, and only "
                f"{len(self.sent) - 1} were queued")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def attempts(self) -> int:
        return len(self.sent)


@pytest.fixture
def naps(monkeypatch):
    """Backoff without the waiting. Recorded, because how long it waits is
    the whole of what `_backoff` does."""
    from geelark_farm import api

    slept: list[float] = []
    monkeypatch.setattr(api.time, "sleep", slept.append)
    return slept


def client_for(make_settings, line, **kw):
    return Client(make_settings(), limiter=RateLimiter(1000), session=line, **kw)


READ = "/v1/phone/list"          # retried by default
WRITE = "/v1/phone/start"        # never retried by default


# ------------------------------------------------------------ the ordinary case
def test_a_successful_call_returns_the_whole_envelope(make_settings):
    line = Line(Reply({"code": 0, "msg": "success", "data": {"items": []}}))

    body = client_for(make_settings, line).post(READ, {"page": 1})

    assert body["code"] == 0
    assert body["data"] == {"items": []}
    assert line.attempts == 1


def test_the_payload_and_the_signature_both_go_with_it(make_settings):
    line = Line(Reply())

    client_for(make_settings, line).post(READ, {"page": 2})

    sent = line.sent[0]
    assert sent["json"] == {"page": 2}
    assert sent["url"].endswith(READ)
    assert set(sent["headers"]) >= {"appId", "traceId", "ts", "nonce", "sign"}


def test_a_call_with_no_payload_still_sends_an_object(make_settings):
    """`json=None` would send no body at all, and GeeLark answers a bare POST
    with a signature complaint that points at the wrong thing."""
    line = Line(Reply())

    client_for(make_settings, line).post(READ)

    assert line.sent[0]["json"] == {}


def test_data_hands_back_the_member_callers_actually_want(make_settings):
    line = Line(Reply({"code": 0, "data": {"items": [1, 2]}}))

    assert client_for(make_settings, line).data(READ) == {"items": [1, 2]}


def test_data_on_an_envelope_with_none_is_not_an_error(make_settings):
    """A successful call that carries nothing is a real answer - `/phone/stop`
    is one - and `.get` is what makes it None rather than a KeyError."""
    line = Line(Reply({"code": 0, "msg": "success"}))

    assert client_for(make_settings, line).data(WRITE) is None


# --------------------------------------------------------------- the envelope
def test_a_non_zero_code_is_an_error_callers_never_have_to_check(make_settings):
    """Rule three of this module: callers never unwrap envelopes themselves.
    A code that only some callers remembered to look at is a phone created and
    then reported as a success."""
    line = Line(Reply({"code": 44002, "msg": "no free slots",
                       "data": {"x": 1}}))

    with pytest.raises(ApiError) as caught:
        client_for(make_settings, line).post(WRITE)

    said = caught.value
    assert said.code == 44002
    assert said.path == WRITE
    assert said.data == {"x": 1}
    assert said.trace_id == line.sent[0]["headers"]["traceId"]


def test_a_code_this_module_knows_is_explained(make_settings):
    """The number alone sends whoever reads it to the vendor's docs. 44002 in
    particular reads like a bug and is a full plan."""
    line = Line(Reply({"code": 44002, "msg": "fail"}))

    with pytest.raises(ApiError) as caught:
        client_for(make_settings, line).post(WRITE)

    assert "plan is full" in str(caught.value)
    assert "traceId" in str(caught.value)


def test_a_call_that_may_fail_gets_its_envelope_back_instead(make_settings):
    """`strict=False` is for calls whose failure is acceptable - stopping a
    phone that is already stopped is the one this exists for."""
    line = Line(Reply({"code": 45001, "msg": "phone is not running"}))

    body = client_for(make_settings, line).post(WRITE, strict=False)

    assert body["code"] == 45001


def test_strict_governs_the_envelope_and_not_the_transport(make_settings, naps):
    """A code is the server answering; a dead connection is no answer at all,
    and no `strict=False` makes one acceptable."""
    line = Line(requests.ConnectionError("connection aborted"))

    with pytest.raises(TransportError):
        client_for(make_settings, line).post(WRITE, strict=False)


# ------------------------------------------------- a connection that came back
def test_a_read_that_drops_is_tried_again(make_settings, naps):
    """The failure this ladder exists for: GeeLark resets connections under
    load, and a read is safe to repeat."""
    line = Line(requests.ConnectionError("reset by peer"),
                Reply({"code": 0, "data": "second time"}))

    body = client_for(make_settings, line).post(READ)

    assert body["data"] == "second time"
    assert line.attempts == 2


def test_a_write_that_drops_is_never_repeated(make_settings, naps):
    """A timed-out write may already have been applied server side. Repeating
    `/phone/addNew` is a second phone nobody asked for and nobody is holding."""
    line = Line(requests.ConnectionError("reset by peer"), Reply())

    with pytest.raises(TransportError):
        client_for(make_settings, line).post(WRITE)

    assert line.attempts == 1, "a write was sent twice"


def test_a_caller_that_knows_better_can_ask_for_a_retry(make_settings, naps):
    """A read-only shell command is a POST to a writing endpoint."""
    line = Line(requests.ConnectionError("reset"), Reply({"code": 0}))

    client_for(make_settings, line).post(WRITE, retry=True)

    assert line.attempts == 2


def test_a_connection_that_never_comes_back_says_how_often_it_tried(
        make_settings, naps):
    line = Line(*[requests.ConnectionError("reset")] * 3)

    with pytest.raises(TransportError, match="3 attempt"):
        client_for(make_settings, line).post(READ)

    assert line.attempts == 3


# ----------------------------------------------------- whose problem the code is
def test_a_server_error_is_repeated_because_it_is_theirs(make_settings, naps):
    line = Line(Reply(status=503), Reply({"code": 0, "data": "ok"}))

    body = client_for(make_settings, line).post(READ)

    assert body["data"] == "ok"
    assert line.attempts == 2


def test_a_client_error_is_not_repeated_because_it_is_ours(make_settings, naps):
    """4xx means the request was wrong, and sending it again makes it wrong
    again - twice as fast towards the rate limit."""
    line = Line(Reply(status=404), Reply())

    with pytest.raises(TransportError):
        client_for(make_settings, line).post(READ)

    assert line.attempts == 1


def test_a_server_that_stays_broken_names_the_status(make_settings, naps):
    line = Line(Reply(status=502), Reply(status=502), Reply(status=502))

    with pytest.raises(TransportError, match="502"):
        client_for(make_settings, line).post(READ)

    assert line.attempts == 3


# -------------------------------------------------------- an answer that is not
def test_a_body_that_is_not_json_quotes_what_came_instead(make_settings):
    """A gateway's HTML error page. `.json()` raises something that says
    nothing about what arrived, and the first two hundred characters are what
    tells you it was a proxy and not GeeLark."""
    line = Line(Reply(not_json=True, text="<html>504 Gateway Timeout</html>"))

    with pytest.raises(TransportError) as caught:
        client_for(make_settings, line).post(READ)

    assert "not JSON" in str(caught.value)
    assert "Gateway Timeout" in str(caught.value)


def test_nothing_from_requests_escapes_this_method(make_settings, naps):
    """A raw RequestException would bypass every caller's error handling, and
    in a batch that is a row dying without its reason reaching the sheet."""
    for thrown in (requests.Timeout("timed out"),
                   requests.TooManyRedirects("too many"),
                   requests.ConnectionError("reset")):
        line = Line(thrown)
        with pytest.raises(TransportError):
            client_for(make_settings, line).post(WRITE)


# --------------------------------------------------------------- the backing off
def test_it_waits_between_attempts_and_the_wait_grows(make_settings, naps):
    """Hammering a service that is already failing is how a blip becomes a
    ban."""
    line = Line(*[requests.ConnectionError("reset")] * 3)

    with pytest.raises(TransportError):
        client_for(make_settings, line).post(READ)

    assert len(naps) == 2, "it retried without waiting"
    assert naps[1] > naps[0]


def test_the_wait_is_capped_so_a_retry_is_not_a_hang(make_settings, naps):
    """Doubling without a cap puts the fourth wait past a minute, inside a
    build whose whole budget the server knows nothing about."""
    line = Line(*[requests.ConnectionError("reset")] * 6)

    with pytest.raises(TransportError):
        client_for(make_settings, line).post(READ, attempts=6)

    assert max(naps) < 9
    assert all(nap > 0 for nap in naps)


def test_no_two_workers_wait_exactly_the_same_time(make_settings, naps):
    """Jitter, so parallel workers recovering from one outage do not
    resynchronise into a thundering herd against a service already in
    trouble."""
    for _ in range(6):
        line = Line(requests.ConnectionError("reset"), Reply({"code": 0}))
        client_for(make_settings, line).post(READ)

    assert len(set(naps)) > 1, "every retry waited an identical time"


# ------------------------------------------------------------- and the budget
def test_every_attempt_draws_on_the_shared_budget(make_settings, naps):
    """The 200/min is process-wide and exceeding it bans the key for two
    hours. A retry that skipped the limiter would spend budget nothing was
    counting."""
    limiter = RateLimiter(1000)
    line = Line(*[requests.ConnectionError("reset")] * 3)
    client = Client(make_settings(), limiter=limiter, session=line)

    with pytest.raises(TransportError):
        client.post(READ)

    assert len(limiter._hits) == 3


# ------------------------------------------- what mutation found (2026-08-25)
def test_a_server_error_on_a_write_is_not_repeated_either(make_settings, naps):
    """The 5xx branch has its own copy of the retry rule, and reading it the
    other way round repeats a write GeeLark may already have applied - the
    thing `RETRY_SAFE_PATHS` exists to prevent. The read version of this is
    two tests up; nothing exercised the write version at all."""
    line = Line(Reply(status=503))

    with pytest.raises(TransportError, match="503"):
        client_for(make_settings, line).post(WRITE)

    assert line.attempts == 1, "a write was repeated after a server error"


def test_each_thread_gets_its_own_connection_pool(make_settings):
    """A requests Session is not thread-safe - its connection pool is shared
    mutable state - and three rows at once against one Session produced
    `ConnectionResetError(10054)` mid-run (measured 2026-08-01).

    Every other test here injects a session, so nothing reached the branch
    that makes them.
    """
    import threading

    client = Client(make_settings(), limiter=RateLimiter(10))
    seen = []

    def look():
        seen.append(client.session)

    threads = [threading.Thread(target=look) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(seen) == 3
    assert len({id(s) for s in seen}) == 3, "two threads shared one Session"
    assert client.session is client.session, "one thread got two"


def test_an_injected_session_is_the_one_used(make_settings):
    """What every other test in this file rests on."""
    line = Line(Reply())
    client = Client(make_settings(), limiter=RateLimiter(10), session=line)

    assert client.session is line


def test_the_jitter_is_a_nudge_and_not_another_wait(make_settings, naps):
    """It exists to break up synchronised retries, not to add a wait of its
    own - so it has to stay small beside the delay it is spread over."""
    line = Line(*[requests.ConnectionError("reset")] * 3)

    with pytest.raises(TransportError):
        client_for(make_settings, line).post(READ)

    # attempt 1 waits 2s, attempt 2 waits 4s, plus a jitter under half a second
    assert 2 <= naps[0] < 2.5
    assert 4 <= naps[1] < 4.5


def test_a_limiter_has_to_allow_at_least_one_request(make_settings):
    """`API_REQUESTS_PER_MINUTE=0` built a Settings happily and then raised a
    bare ValueError out of here - the wrong layer, with a message that does
    not name the file to go and fix. Config refuses it now; this is the
    backstop, and one a minute is a legitimate setting."""
    with pytest.raises(ValueError, match=">= 1"):
        RateLimiter(0)

    assert RateLimiter(1).capacity == 1


def test_a_full_window_makes_the_caller_actually_wait(monkeypatch):
    """The limiter's whole job, and the only path that had no test: the other
    two pass `now=`, which returns instead of sleeping.

    That branch is what stands between a busy run and a two-hour ban, and it
    is the one that blocks - so it is exercised on a clock that moves when
    something sleeps on it, rather than on the wall.
    """
    from geelark_farm import api

    now = 1_000.0
    slept: list[float] = []

    def monotonic():
        return now

    def sleep(seconds):
        nonlocal now
        slept.append(seconds)
        now += seconds

    monkeypatch.setattr(api.time, "monotonic", monotonic)
    monkeypatch.setattr(api.time, "sleep", sleep)

    limiter = RateLimiter(2, window=60.0)
    assert limiter.acquire() == 0.0
    assert limiter.acquire() == 0.0
    assert slept == [], "it waited while the window still had room"

    waited = limiter.acquire()

    assert slept, "the third request went straight through a full window"
    assert waited == pytest.approx(60.0, abs=0.1)
    assert waited == pytest.approx(sum(slept), abs=0.001), (
        "it reported a wait that is not the one it took")


def test_a_call_has_to_be_allowed_at_least_one_attempt(make_settings):
    """`attempts=0` ran the loop zero times and came out the far side with
    `exhausted retries (None)` - a sentence saying the retries ran out when
    not one request had been made. The same shape as the ledger read that
    answered "no phones exist" from a loop that never ran (2026-08-25)."""
    line = Line(Reply())

    with pytest.raises(ValueError, match=">= 1"):
        client_for(make_settings, line).post(READ, attempts=0)

    assert line.attempts == 0, "it went to the network anyway"

    # And one is a real answer, not the edge of the refusal: it means "send
    # this once and do not repeat it", which is what a caller asks for when
    # it would rather fail than risk a second write.
    once = Line(Reply({"code": 0, "data": "sent"}))
    assert client_for(make_settings, once).post(READ, attempts=1)["data"] == "sent"
    assert once.attempts == 1


def test_build_client_loads_the_settings_when_it_is_given_none(monkeypatch,
                                                               make_settings):
    """The one-line convenience every command starts from."""
    from geelark_farm import api

    made = make_settings()
    monkeypatch.setattr(api.Settings, "load", staticmethod(lambda: made))

    assert api.build_client().settings is made
    assert api.build_client(made).settings is made
