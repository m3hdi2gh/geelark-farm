"""The code path's store half (section 7 of the panel contract): a flow
waits on a `code_requests` row, the API answers it, and the pool leaves
alone what the API calls blocked or waiting_customer (2026-09-16)."""

from __future__ import annotations

import inspect
import itertools

import pytest

from geelark_farm import accounts as domain
from geelark_farm.store import codes as store_codes


class _FakeStore:
    """`Store(settings)` over a script: each SQL call answered from `rows`
    in order (a list of row lists), every statement kept."""

    sql: list[tuple[str, tuple]] = []
    rows: list[list[dict]] = []

    def __init__(self, settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _rows(self, sql, params=()):
        type(self).sql.append((" ".join(sql.split()), params))
        return type(self).rows.pop(0) if type(self).rows else []

    _write = _rows


@pytest.fixture
def fake_store(monkeypatch):
    _FakeStore.sql, _FakeStore.rows = [], []
    monkeypatch.setattr(store_codes, "Store", _FakeStore)
    return _FakeStore


def _source(monkeypatch, make_settings, tmp_path, *, answers, wrong=0,
            customer=True, minutes=10):
    """A PgCodes whose store is scripted: `answers` is what each poll
    reads, in order; the last one repeats."""
    settings = make_settings(state_dir=tmp_path, store_enabled=True,
                             code_wait_minutes=minutes)
    source = store_codes.PgCodes(settings)
    seen = {"opened": [], "closed": [], "slept": []}
    polls = itertools.chain(answers, itertools.repeat(answers[-1]))
    monkeypatch.setattr(source, "_asks_a_person", lambda a: customer)
    monkeypatch.setattr(source, "_wrong_since", lambda a, s: wrong)
    monkeypatch.setattr(source, "_open",
                        lambda a, w, t: seen["opened"].append((a, w, t)) or 7)
    monkeypatch.setattr(source, "_answered", lambda rid: next(polls))
    monkeypatch.setattr(source, "_close",
                        lambda rid, o: seen["closed"].append((rid, o)))
    monkeypatch.setattr(store_codes.time, "sleep",
                        lambda s: seen["slept"].append(s))
    return source, seen


def test_a_flow_waits_on_a_request_and_gets_the_code_the_panel_wrote(
        monkeypatch, make_settings, tmp_path):
    source, seen = _source(monkeypatch, make_settings, tmp_path,
                           answers=[None, None, "482913"])

    got = source.code_for("c@example.com", since=1_000.0)

    assert got == "482913"
    assert seen["opened"] == [("c@example.com", 600.0, 3)], (
        "ten minutes by default, three tries")
    assert seen["closed"] == [(7, "typed")]
    assert seen["slept"] == [3.0, 3.0], "polled, not spun"


def test_silence_ends_the_wait_as_a_timeout(monkeypatch, make_settings,
                                            tmp_path):
    source, seen = _source(monkeypatch, make_settings, tmp_path,
                           answers=[None])
    clock = iter([0.0, 0.0, 0.5, 0.9, 1.1, 1.1, 1.1])
    monkeypatch.setattr(store_codes.time, "time", lambda: next(clock))

    assert source.code_for("c@example.com", since=0.0, timeout=1.0) is None
    assert seen["closed"] == [(7, "timeout")]
    assert all(s <= 3.0 for s in seen["slept"])


def test_the_fourth_ask_is_refused_without_a_request(monkeypatch,
                                                     make_settings, tmp_path):
    """Three wrong codes end the attempt (`wrong_code`): the flow is
    told no before a request the panel would answer for nothing."""
    source, seen = _source(monkeypatch, make_settings, tmp_path,
                           answers=["111111"], wrong=3)
    assert source.code_for("c@example.com", since=0.0) is None
    assert seen["opened"] == []
    # Two wrong: the third request says one try is left.
    source, seen = _source(monkeypatch, make_settings, tmp_path,
                           answers=["111111"], wrong=2)
    assert source.code_for("c@example.com", since=0.0) == "111111"
    assert seen["opened"][0][2] == 1


def test_an_account_no_customer_answers_for_is_not_waited_on(
        monkeypatch, make_settings, tmp_path):
    """A console or sheet account that turns out to want an emailed code
    has no panel behind it: ten idle minutes on a phone would change
    nothing, so the answer is no at once."""
    source, seen = _source(monkeypatch, make_settings, tmp_path,
                           answers=["111111"], customer=False)
    assert source.code_for("c@example.com", since=0.0) is None
    assert seen["opened"] == [] and seen["closed"] == []


def test_the_wait_comes_from_the_settings(make_settings, tmp_path):
    settings = make_settings(state_dir=tmp_path, code_wait_minutes=4)
    assert store_codes.PgCodes(settings).wait_seconds == 240.0
    assert store_codes.PgCodes(make_settings(state_dir=tmp_path)
                               ).wait_seconds == 600.0


def test_the_api_answers_only_a_request_that_is_open_and_says_which(
        fake_store, make_settings):
    settings = make_settings()
    # Not a code: nothing is written.
    assert store_codes.answer(settings, "c@example.com", "12") == "bad_code"
    assert store_codes.answer(settings, "c@example.com", "abc123") == "bad_code"
    assert fake_store.sql == []
    # A waiting request takes it.
    fake_store.rows = [[{"id": 7}]]
    assert store_codes.answer(settings, "c@example.com", " 482913 ") == "accepted"
    sql, params = fake_store.sql[-1]
    assert "SET code = %s, answered_at = now()" in sql
    assert "closed_at IS NULL AND code IS NULL AND until > now()" in sql
    assert params == ("482913", "c@example.com")
    # None open: the clock ran out, or nothing ever asked.
    fake_store.rows = [[]]
    assert store_codes.answer(settings, "c@example.com", "482913") == "not_waiting"


def test_the_open_request_is_the_one_the_panel_reads(fake_store,
                                                     make_settings):
    fake_store.rows = [[{"id": 7, "address": "c@example.com",
                         "tries_left": 3}]]
    got = store_codes.open_for(make_settings(), "C@example.com")
    assert got["id"] == 7
    sql, params = fake_store.sql[-1]
    assert "closed_at IS NULL AND code IS NULL AND until > now()" in sql
    assert params == ("C@example.com",)
    assert store_codes.open_for(make_settings(), "") is None


def test_opening_a_request_supersedes_the_last_one_for_the_address(
        fake_store, make_settings, tmp_path):
    fake_store.rows = [[], [{"id": 9}]]
    source = store_codes.PgCodes(make_settings(state_dir=tmp_path))
    assert source._open("c@example.com", 600.0, 3) == 9
    first, second = fake_store.sql[-2:]
    assert "outcome = 'superseded'" in first[0]
    assert "INSERT INTO code_requests" in second[0]
    assert "now() + %s * interval '1 second'" in second[0]
    assert second[1][2:] == (600, 3)


def test_a_wrong_code_is_counted_on_the_row_that_carried_it(fake_store,
                                                            make_settings,
                                                            tmp_path):
    source = store_codes.PgCodes(make_settings(state_dir=tmp_path))
    fake_store.rows = [[{"id": 7}]]
    source.wrong("c@example.com")
    assert "SET outcome = 'wrong'" in fake_store.sql[-1][0]
    assert "outcome = 'typed'" in fake_store.sql[-1][0]
    fake_store.rows = [[{"n": 2}]]
    assert source._wrong_since("c@example.com", 1_000.0) == 2
    assert "outcome = 'wrong'" in fake_store.sql[-1][0]
    assert "asked_at >= to_timestamp(%s)" in fake_store.sql[-1][0]


def test_the_flow_asks_the_store_which_kind_the_account_is(fake_store,
                                                           make_settings,
                                                           tmp_path):
    source = store_codes.PgCodes(make_settings(state_dir=tmp_path))
    fake_store.rows = [[{"credential_kind": "email_code_customer"}]]
    assert source._asks_a_person("c@example.com")
    fake_store.rows = [[{"credential_kind": ""}]]
    assert not source._asks_a_person("c@example.com")
    fake_store.rows = [[]]
    assert not source._asks_a_person("nobody@example.com")


def test_pgcodes_is_a_code_source():
    from geelark_farm import codes

    assert isinstance(store_codes.PgCodes.__dict__["code_for"], object)
    assert issubclass(store_codes.PgCodes, object)
    sig = inspect.signature(store_codes.PgCodes.code_for)
    assert list(sig.parameters) == ["self", "address", "since", "timeout"]
    assert isinstance(store_codes.PgCodes.__new__(store_codes.PgCodes),
                      codes.CodeSource)


def test_the_schema_carries_the_request_table():
    import pathlib

    sql = pathlib.Path("src/geelark_farm/store/schema.sql").read_text(
        encoding="utf-8")
    # The table is the "codes" one from before the panel existed; the
    # code path's shape is added to it column by column, because a second
    # CREATE TABLE IF NOT EXISTS was a no-op and the index on a column
    # the old table lacked failed at every start (2026-09-16).
    assert sql.count("CREATE TABLE IF NOT EXISTS code_requests") == 1
    for column in ("machine", "until", "tries_left", "closed_at", "outcome"):
        assert f"ALTER TABLE code_requests ADD COLUMN IF NOT EXISTS {column}" in sql
    assert ("CREATE INDEX IF NOT EXISTS code_requests_waiting\n"
            "    ON code_requests (lower(address)) WHERE closed_at IS NULL") in sql
    assert "code_requests_open" in sql, "the old index keeps its name"


# ------------------------------------------------------- what the pool holds
def test_held_back_is_blocked_or_waiting_customer_and_nothing_else():
    """The API's two pre-queue states, as the pool reads them. A row with
    no kind is the console's and is never held."""
    assert not domain.held_back("chatgpt", "", False)
    assert not domain.held_back("", "", False)
    assert not domain.held_back("chatgpt", "password_totp", False)
    assert domain.held_back("chatgpt", "google_backup_codes", True), "not served"
    assert domain.held_back("spotify", "password_totp", True), "not served"
    assert domain.held_back("claude", "password_totp", True), "not served"
    # Claude serves the customer-answered kind (2026-09-16): the customer
    # decides.
    assert domain.held_back("claude", "email_code_customer", False)
    assert not domain.held_back("claude", "email_code_customer", True)


def test_the_pool_leaves_held_back_accounts_where_they_are(monkeypatch):
    """The API said `blocked` while the claim did not know the word, so a
    Claude account posted today would have gone to a phone and failed
    there. The claim's SQL and `available` apply accounts.held_back."""
    from geelark_farm.store.pgpool import PgAppPool
    from tests.test_pgpool import MemoryTable

    table = MemoryTable()
    table.add("app", address="free@x.com", password="p", totp_secret="")
    table.add("app", address="blocked@x.com", password="", totp_secret="",
              product="spotify", credential_kind="password_totp",
              customer_ready=True)
    table.add("app", address="notyet@x.com", password="", totp_secret="",
              product="chatgpt", credential_kind="email_code_customer",
              customer_ready=False)
    pool = PgAppPool(table)
    pool.load()

    assert [r.values["Address"] for r in pool.available] == ["free@x.com"]
    sql, params = pool.held_back()
    assert ("(coalesce(product, 'chatgpt'), credential_kind) IN "
            "((%s, %s), (%s, %s))") in sql
    assert "coalesce(credential_kind, '') = ''" in sql
    assert "NOT (coalesce(credential_kind, '') = %s" in sql
    assert "AND NOT coalesce(customer_ready, false))" in sql
    assert params == ("chatgpt", "password_totp", "claude",
                      "email_code_customer", "email_code_customer")


def test_the_real_table_appends_the_pools_condition_to_claim_and_count():
    from geelark_farm.store import pgpool

    claim = inspect.getsource(pgpool.ResourceTable.claim)
    assert 'held_back: tuple[str, tuple] = ("", ())' in claim
    assert '{not_here}{held}{aside}' in claim
    assert "*withheld,\n                 *aside_params, claimed" in claim.replace(
        "\r\n", "\n")
    count = inspect.getsource(pgpool.ResourceTable.free_count)
    assert '{held}{aside}' in count and "*aside_params" in count


def test_the_builder_container_reads_codes_from_the_store(make_settings,
                                                          tmp_path):
    from geelark_farm import serve as serve_mod

    assert serve_mod._codes_source(
        make_settings(state_dir=tmp_path, store_enabled=False)) is None
    got = serve_mod._codes_source(
        make_settings(state_dir=tmp_path, store_enabled=True))
    assert isinstance(got, store_codes.PgCodes)
    src = inspect.getsource(serve_mod._carry_out)
    assert "codes_source=_codes_source(settings)" in src


def test_the_two_code_verdicts_are_words_the_farm_knows():
    from geelark_farm import failures

    for reason in ("code_timeout", "wrong_code"):
        assert failures.knows(reason), reason
        assert failures.VERDICTS[reason].blame == failures.CHALLENGED
        assert "POST /ready" in failures.VERDICTS[reason].advice
    assert set(store_codes.REASONS) == {"code_timeout", "wrong_code",
                                        "email_code_never_arrived"}


def test_the_code_wait_comes_from_the_environment(tmp_path, monkeypatch):
    from geelark_farm.config import Settings

    monkeypatch.setenv("GEELARK_APP_ID", "x")
    monkeypatch.setenv("GEELARK_API_KEY", "y")
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "a"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "l"))
    assert Settings.load().code_wait_minutes == 10
    monkeypatch.setenv("CODE_WAIT_MINUTES", "15")
    assert Settings.load().code_wait_minutes == 15
