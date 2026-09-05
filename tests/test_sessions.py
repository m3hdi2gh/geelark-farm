"""Seats in the store, not in this process's memory.

The dict version worked exactly until the process restarted - and it
restarts on every deploy, so every change to the code signed everybody
out (2026-09-05). These are about the table that replaced it.
"""

from __future__ import annotations

import hashlib

import pytest

from geelark_farm.store import sessions


class FakeStore:
    """Records what was written; answers reads from a script."""

    def __init__(self, rows=None, raises=None):
        self.rows = rows if rows is not None else []
        self.raises = raises
        self.writes: list[tuple[str, tuple]] = []
        self.reads: list[tuple[str, tuple]] = []

    def __call__(self, settings):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def _write(self, sql, params=()):
        self.writes.append((sql, params))
        return list(self.rows)

    def _rows(self, sql, params=()):
        if self.raises is not None:
            raise self.raises
        self.reads.append((sql, params))
        return list(self.rows)


@pytest.fixture
def store(monkeypatch):
    def use(rows=None, raises=None):
        fake = FakeStore(rows, raises)
        monkeypatch.setattr(sessions, "Store", fake)
        return fake
    return use


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_cookie_is_never_what_is_stored(store, make_settings):
    """A copy of the database must not be a drawer full of live seats.
    The raw token exists in the browser's cookie and nowhere else - the
    shape the password columns already have."""
    fake = store()

    token, csrf = sessions.start(make_settings(), 7, hours=12)

    sql, params = fake.writes[0]
    assert token not in sql and token not in params
    assert csrf not in sql
    assert _sha(token) in params
    assert params[1] == 7


def test_two_seats_do_not_share_a_token_or_a_csrf(store, make_settings):
    store()
    one = sessions.start(make_settings(), 7, hours=12)
    two = sessions.start(make_settings(), 7, hours=12)

    assert one[0] != two[0] and one[1] != two[1]


def test_the_user_row_comes_back_fresh_and_without_its_secrets(store,
                                                               make_settings):
    """Read on every request rather than frozen at login, so a permission
    taken away takes effect on the next click. The two password columns
    go the same way `check_login` drops them."""
    store([{"csrf": "c1", "id": 7, "username": "mehdi", "role": "admin",
            "password_hash": "x", "password_salt": "y"}])

    seat = sessions.find(make_settings(), "tok")

    assert seat["csrf"] == "c1"
    assert seat["user"]["username"] == "mehdi"
    assert "csrf" not in seat["user"]
    assert "password_hash" not in seat["user"]
    assert "password_salt" not in seat["user"]


def test_a_seat_is_looked_up_by_the_digest_of_the_cookie(store,
                                                         make_settings):
    fake = store([{"csrf": "c1", "id": 7}])

    sessions.find(make_settings(), "tok")

    _sql, params = fake.reads[0]
    assert params == (_sha("tok"),)


def test_an_empty_cookie_asks_the_store_nothing(store, make_settings):
    fake = store([{"csrf": "c1", "id": 7}])

    assert sessions.find(make_settings(), "") is None
    assert fake.reads == []


def test_an_unknown_cookie_is_simply_nobody(store, make_settings):
    store([])

    assert sessions.find(make_settings(), "tok") is None


def test_a_store_that_cannot_be_reached_does_not_forge_a_logout(
        store, make_settings, caplog):
    """Refusing the request is right; saying "you are logged out" is a
    second, wrong story about what went wrong."""
    store(raises=RuntimeError("no route to host"))

    assert sessions.find(make_settings(), "tok") is None
    assert "could not read the session" in caplog.text


def test_the_expired_and_the_deactivated_are_refused_in_sql(store,
                                                            make_settings):
    """One None for three different reasons, so the caller has nothing
    left to decide - and all three are the database's job, not a second
    round trip's."""
    fake = store([{"csrf": "c1", "id": 7}])

    sessions.find(make_settings(), "tok")

    sql = fake.reads[0][0]
    assert "s.until > now()" in sql
    assert "u.active" in sql


def test_logging_out_deletes_by_digest(store, make_settings):
    fake = store()

    sessions.end(make_settings(), "tok")

    sql, params = fake.writes[0]
    assert sql.startswith("DELETE FROM sessions")
    assert params == (_sha("tok"),)


def test_logging_out_with_no_cookie_writes_nothing(store, make_settings):
    fake = store()

    sessions.end(make_settings(), "")

    assert fake.writes == []


def test_ending_a_persons_seats_spares_the_chair_they_are_sitting_in(
        store, make_settings):
    """An admin resetting their own password keeps their own session;
    everybody else holding that account is put out."""
    fake = store([{"token_hash": "a"}, {"token_hash": "b"}])

    gone = sessions.end_all_of(make_settings(), 9, keep="mine")

    assert gone == 2
    sql, params = fake.writes[0]
    assert "token_hash <> %s" in sql
    assert params == (9, _sha("mine"))


def test_the_sweep_is_housekeeping_and_never_fatal(monkeypatch,
                                                   make_settings, caplog):
    """`find` already refuses an expired row, so nothing depends on this."""
    class Angry(FakeStore):
        def _write(self, sql, params=()):
            raise RuntimeError("gone")

    monkeypatch.setattr(sessions, "Store", Angry())

    assert sessions.sweep(make_settings()) == 0
    assert "could not sweep" in caplog.text


def test_the_sweep_says_how_many_it_dropped(store, make_settings):
    fake = store([{"token_hash": "a"}, {"token_hash": "b"}])

    assert sessions.sweep(make_settings()) == 2
    assert "until <= now()" in fake.writes[0][0]


def test_no_session_is_kept_in_the_web_process():
    """The whole point. A seat in a module-level dict does not survive a
    `docker compose up`, and the process restarts on every deploy."""
    import inspect
    import re

    from geelark_farm.web import app

    # The bare name only - `store_sessions` and `_drop_sessions_of` are
    # the store-backed spellings, and neither has a word boundary here.
    source = inspect.getsource(app)
    assert not re.search(r"\b_sessions\b", source), (
        "a session dict is back in the web process; it will be emptied by "
        "the next deploy")
