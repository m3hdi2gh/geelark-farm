"""The code that is typed at a 2FA prompt, and when it is worth typing.

`totp_now` had no test in any file. Its guard had one until the row flow was
deleted and took tests/test_sheets.py with it - that file was mixed, and the
half of it about accounts.py was still describing live code (1a97d7e,
2026-08-12). The `min_life` rule below never had one at all.
"""

from __future__ import annotations

import time

import pytest

from geelark_farm.accounts import AccountError, Credentials

# A key pyotp accepts, from its own documentation.
SECRET = "JBSWY3DPEHPK3PXP"

#: A moment on a window boundary, so "seconds into the window" can be written
#: by adding to it. TOTP counts from the epoch in steps of `interval`, and this
#: is divisible by 30.
WINDOW_START = 1_800_000_000.0


def creds(secret: str = SECRET) -> Credentials:
    return Credentials(email="a@example.com", password="pw", totp_secret=secret)


class Clock:
    """A clock that only moves when something sleeps on it.

    Real time would make every assertion below a race: the whole subject is
    what happens in the last seconds of a thirty-second window.
    """

    def __init__(self, now: float):
        self.now = now
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    @property
    def life_left(self) -> float:
        """How long the code generated at this instant would stay valid."""
        return 30 - (self.now % 30)


@pytest.fixture
def clock(monkeypatch):
    """Installs a fake clock and hands it back. `at` sets the moment."""
    made = Clock(WINDOW_START)
    monkeypatch.setattr(time, "time", made.time)
    monkeypatch.setattr(time, "sleep", made.sleep)
    return made


# ------------------------------------------ a code that would expire mid-type
def test_a_code_about_to_expire_is_never_the_one_typed(clock):
    """The rule this function exists for, and nothing has ever exercised it.

    A code typed at second 27 of a 30-second window arrives after it has
    expired. Google does not answer "too late" - it answers "wrong code", and
    it counts that against the account. So a run that was going to succeed
    instead spends an attempt, and the account collects a strike that looks
    like an attacker guessing.
    """
    clock.now = WINDOW_START + 27          # three seconds of life left

    creds().totp_now()

    assert clock.slept, "a code with three seconds left was typed as it stood"
    assert clock.life_left >= 8.0, (
        "it waited, but not far enough to be worth waiting at all")


def test_the_wait_lands_inside_the_next_window_not_on_its_edge(clock):
    """Sleeping exactly `remaining` lands on the boundary itself, where the
    code that comes back is a coin toss between the two windows. The extra
    half second is the whole point of the `+ 0.5`."""
    clock.now = WINDOW_START + 29.9

    creds().totp_now()

    assert clock.now % 30 > 0, "it woke up on the boundary"
    assert clock.now % 30 < 1, "it slept past a whole window for no reason"


# ------------------------------------------------- a code with life left in it
def test_a_code_with_life_left_is_typed_without_waiting(clock):
    """The ordinary case. Waiting here would add up to half a minute to every
    sign-in that reaches a code prompt, on both accounts, on every phone."""
    clock.now = WINDOW_START + 2           # twenty-eight seconds left

    creds().totp_now()

    assert clock.slept == []


def test_the_minimum_life_is_enough_on_its_own(clock):
    """Exactly `min_life` left is enough - the comparison is `<`, not `<=`.
    Off by one here costs a wait on one code in every thirty for no gain."""
    clock.now = WINDOW_START + 22          # exactly eight seconds left

    creds().totp_now()

    assert clock.slept == []


def test_a_caller_that_needs_longer_can_ask_for_it(clock):
    """`min_life` is a parameter because a slower screen needs more of the
    window than a faster one, and the default is only what suits today's."""
    clock.now = WINDOW_START + 12          # eighteen seconds left

    creds().totp_now()
    assert clock.slept == [], "eighteen seconds is plenty by default"

    creds().totp_now(min_life=20)
    assert clock.slept, "a caller that asked for twenty was given eighteen"


# ------------------------------------------------------ what comes back at all
def test_the_code_is_the_one_the_authenticator_would_show():
    """Six digits, and the same six pyotp produces - because pyotp is what
    Google is checking the typed code against.

    On the real clock deliberately: the fake one above cannot be used here,
    since pyotp reads `datetime.datetime.now()` rather than `time.time()` and
    would not see it. Safe from a window roll between the two calls, because
    `totp_now` only returns a code with at least eight seconds left.
    """
    import pyotp

    code = creds().totp_now()

    assert len(code) == 6
    assert code.isdigit()
    assert code == pyotp.TOTP(SECRET).now()


# --------------------------------------------------- an account with no secret
def test_no_code_can_be_produced_without_a_secret():
    """Some Gmails are sold without 2FA, so a blank secret is a fact about the
    account rather than a broken row - but asking one for a code is a caller's
    mistake, and it says so here instead of failing inside pyotp on an empty
    base32 string, ten minutes into a phone.

    This had a test until 1a97d7e deleted tests/test_sheets.py along with the
    row flow it mostly covered.
    """
    with pytest.raises(AccountError) as caught:
        creds(secret="").totp_now()

    message = str(caught.value)
    assert "no authenticator secret" in message
    assert "a@example.com" in message, "which account is the useful half"
