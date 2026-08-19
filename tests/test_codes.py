"""Answering the one-time code OpenAI emails an account with no authenticator.

The code can come from a mailbox or from a person; the login flow is not told
which. These are about the handshake between a build that is waiting and
whoever answers it, because that is where the threading is.
"""

from __future__ import annotations

import threading
import time

from geelark_farm import codes


def answered_from_another_thread(source, *, address="a@b.com", timeout=5):
    """Start a build waiting for a code, and hand back the request."""
    got: dict = {}
    worker = threading.Thread(
        target=lambda: got.update(
            code=source.code_for(address, since=time.time(), timeout=timeout)))
    worker.start()
    deadline = time.time() + 2
    while not source.waiting() and time.time() < deadline:
        time.sleep(0.01)
    return worker, got


# ------------------------------------------------------ reading the digits
def test_the_code_is_taken_out_of_a_real_message():
    assert codes.code_in("Your ChatGPT code is 481920. It expires in 10 "
                         "minutes.") == "481920"


def test_a_longer_number_is_not_read_as_a_code():
    """An order id or a year in a footer is how this kind of scraping usually
    goes wrong."""
    assert codes.code_in("Order 1234567 shipped in 2026") is None
    assert codes.code_in("no digits here at all") is None


# ------------------------------------------------------------ no mailbox
def test_the_default_source_never_produces_a_code():
    """The whole of today's behaviour, kept as the default so this can be
    merged and change nothing until a source is configured."""
    assert codes.NoSource().code_for("a@b.com", since=0) is None


# --------------------------------------------------- a person answering
def test_a_build_waits_until_someone_answers_it():
    source = codes.Pending()
    worker, got = answered_from_another_thread(source)

    waiting = source.waiting()
    assert [r.address for r in waiting] == ["a@b.com"]
    assert source.answer(waiting[0], "481920")

    worker.join(2)
    assert got["code"] == "481920"
    assert source.waiting() == []          # and it leaves the queue


def test_a_mistyped_code_is_refused_before_it_reaches_the_phone():
    """A wrong code costs the account an attempt, and OpenAI counts those - so
    the check lives here, where every answerer gets it, rather than at one
    prompt."""
    source = codes.Pending()
    worker, got = answered_from_another_thread(source)
    request = source.waiting()[0]

    assert not source.answer(request, "48192")      # five
    assert not source.answer(request, "4819201")    # seven
    assert not source.answer(request, "4819a0")     # not digits
    assert not source.answer(request, "")
    assert source.waiting()                          # still waiting

    source.answer(request, "481920")
    worker.join(2)
    assert got["code"] == "481920"


def test_giving_up_releases_the_build_with_nothing():
    """Walking away must not leave a phone blocked for its whole budget."""
    source = codes.Pending()
    worker, got = answered_from_another_thread(source)

    source.give_up(source.waiting()[0])

    worker.join(2)
    assert not worker.is_alive()
    assert got["code"] is None


def test_a_wait_that_lapses_reports_nothing_rather_than_hanging():
    source = codes.Pending()
    worker, got = answered_from_another_thread(source, timeout=0.3)

    worker.join(3)
    assert not worker.is_alive()
    assert got["code"] is None
    assert source.waiting() == []          # and it does not pile up


def test_two_builds_wait_separately():
    """A batch runs several phones at once, and each is asking about its own
    account - answering one must not release the other."""
    source = codes.Pending()
    first, got_first = answered_from_another_thread(source, address="one@x.com")
    second, got_second = answered_from_another_thread(source, address="two@x.com")

    waiting = {r.address: r for r in source.waiting()}
    assert set(waiting) == {"one@x.com", "two@x.com"}

    source.answer(waiting["one@x.com"], "111111")
    first.join(2)
    assert got_first["code"] == "111111"
    assert got_second == {}                # the other is still waiting

    source.answer(waiting["two@x.com"], "222222")
    second.join(2)
    assert got_second["code"] == "222222"


def test_the_request_says_how_long_is_left():
    """What the prompt shows, so whoever is typing knows whether to hurry."""
    source = codes.Pending()
    worker, _ = answered_from_another_thread(source, timeout=5)
    request = source.waiting()[0]

    assert 0 < request.seconds_left <= 5

    source.give_up(request)
    worker.join(2)
