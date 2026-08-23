"""Where a one-time code emailed to an account comes from.

An app account with no authenticator is not refused by OpenAI - it is emailed
a six-digit code instead, and the login waits on a page saying "check your
inbox". Until now that page ended the attempt: nothing here could read an
inbox, so the account was set aside for a human and the phone moved on.

Reading the inbox is a decision that has not been made yet - which mailbox,
and with what credential - so it lives behind this one interface. The flow
asks for a code and gets one or does not; how it was obtained is not the
login's business, and swapping the answer later touches nothing but this
file.

`NoSource` is the default and does exactly what the tool did before: it never
produces a code, so the page is reported the same way it always was. That is
deliberate - this can be merged and changed nothing until a source is
configured.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

#: How long to wait for a code to arrive before giving up on it. Generous,
#: because the cost of waiting is a phone sitting idle and the cost of giving
#: up too early is the whole build - and mail providers are unhurried.
WAIT_SECONDS = 180

#: Six digits, standing alone. `\b` on both sides so a longer number - an
#: order id, a year in a footer - cannot be read as a code, which is the way
#: this kind of scraping usually goes wrong.
CODE = re.compile(r"\b(\d{6})\b")


def code_in(text: str) -> str | None:
    """The six-digit code in a message body, or None.

    Kept separate from any source so it can be tested against real message
    text without a mailbox anywhere in sight.
    """
    found = CODE.search(text or "")
    return found.group(1) if found else None


@runtime_checkable
class CodeSource(Protocol):
    """Something that can hand over the code emailed to an address.

    `since` is a unix time: only mail that arrived after it counts. Without
    it the first look would happily return the code from the previous
    attempt, which is expired and which the page will refuse - and the run
    would then blame the account.
    """

    def code_for(self, address: str, *, since: float,
                 timeout: float = WAIT_SECONDS) -> str | None:
        ...


class NoSource:
    """No inbox is reachable, so no code is ever produced.

    The default, and the whole of today's behaviour: the login reports the
    page it is standing on and the account is set aside for a human.
    """

    def code_for(self, address: str, *, since: float,
                 timeout: float = WAIT_SECONDS) -> str | None:
        log.info("no mailbox is configured, so the code emailed to %s "
                 "cannot be read", address)
        return None


@dataclass
class Request:
    """One build, stopped on the code page, waiting for someone to answer.

    Identified by the address it is signing in, which is what the batch table
    already shows per line - so the answerer can say which row is asking
    without this module knowing anything about batches.
    """

    address: str
    asked_at: float
    deadline: float
    answered: threading.Event = field(default_factory=threading.Event)
    code: str | None = None

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.deadline - time.time())


class Pending:
    """A source answered by a person rather than by a mailbox.

    The build stops on the code page and waits; whoever is running the console
    reads the code out of the inbox - or is handed it by the person signing up
    - and types it in. No mail credentials anywhere, and nothing to set up per
    account, which is what makes it the cheapest way to handle an account with
    no authenticator on it.

    Deliberately not tied to the console. This holds requests and hands back
    answers; who does the answering is the caller's business, so the same
    source serves a terminal prompt today and a web form later without the
    login flow knowing either exists.

    Every method is safe to call from any thread: the builds run in a pool and
    the answering happens on whichever thread is driving the display.
    """

    def __init__(self) -> None:
        self._waiting: list[Request] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------- the flow's side
    def code_for(self, address: str, *, since: float,
                 timeout: float = WAIT_SECONDS) -> str | None:
        """Block until someone answers, or the wait runs out."""
        request = Request(address=address, asked_at=since,
                          deadline=time.time() + timeout)
        with self._lock:
            self._waiting.append(request)
        log.info("waiting up to %.0fs for someone to supply the code sent "
                 "to %s", timeout, address)
        request.answered.wait(timeout)
        with self._lock:
            if request in self._waiting:
                self._waiting.remove(request)
        if request.code is None:
            log.warning("nobody supplied the code for %s", address)
        return request.code

    # ---------------------------------------------------- the answerer's side
    def waiting(self) -> list[Request]:
        """Requests still unanswered, oldest first. A copy, so the caller can
        walk it while a build adds another."""
        with self._lock:
            return [r for r in self._waiting if not r.answered.is_set()]

    def answer(self, request: Request, code: str) -> bool:
        """Give a waiting build its code. False if it is not six digits.

        Checked here rather than at the prompt so every answerer gets the same
        rule: a mistyped code costs the account an attempt, and OpenAI counts
        those.
        """
        digits = (code or "").strip()
        if not CODE.fullmatch(digits):
            return False
        request.code = digits
        request.answered.set()
        return True

    def give_up(self, request: Request) -> None:
        """Stop waiting - the code never came, or nobody is there to type it."""
        request.answered.set()
