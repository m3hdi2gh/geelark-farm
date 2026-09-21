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
from typing import Callable, Protocol, runtime_checkable

log = logging.getLogger(__name__)

#: How long to wait for a code to arrive before giving up on it. Generous,
#: because the cost of waiting is a phone sitting idle and the cost of giving
#: up too early is the whole build - and mail providers are unhurried.
WAIT_SECONDS = 180

#: How long a source waits between looks at whether it has been told to
#: stop. The wait itself is the source's; this is only how often the
#: caller's `watch` gets a word in.
WATCH_EVERY = 2.0

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

    `watch` is the caller's own stop, the same callable the screen router
    is driven with: called between looks, and whatever it raises goes up.
    It is part of the protocol rather than of one source because every
    source is a wait of minutes, and a wait that takes no stop is deaf by
    construction - all three were, so a Cancel pressed while a build sat
    on the code page was unheard for up to ten minutes (2026-09-21, found
    by audit).
    """

    def code_for(self, address: str, *, since: float,
                 timeout: float = WAIT_SECONDS,
                 watch: Callable[[], None] | None = None) -> str | None:
        ...


class NoSource:
    """No inbox is reachable, so no code is ever produced.

    The default, and the whole of today's behaviour: the login reports the
    page it is standing on and the account is set aside for a human.
    """

    def code_for(self, address: str, *, since: float,
                 timeout: float = WAIT_SECONDS,
                 watch: Callable[[], None] | None = None) -> str | None:
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
                 timeout: float = WAIT_SECONDS,
                 watch: Callable[[], None] | None = None) -> str | None:
        """Block until someone answers, the wait runs out, or `watch` says
        stop - in slices, so the stop is heard within WATCH_EVERY."""
        request = Request(address=address, asked_at=since,
                          deadline=time.time() + timeout)
        with self._lock:
            self._waiting.append(request)
        log.info("waiting up to %.0fs for someone to supply the code sent "
                 "to %s", timeout, address)
        try:
            while not request.answered.is_set():
                if watch is not None:
                    watch()
                left = request.deadline - time.time()
                if left <= 0:
                    break
                request.answered.wait(min(WATCH_EVERY, left))
        finally:
            # Whatever ended the wait, the request is not waiting any more
            # - a stop must not leave a line on the console for a build
            # that is gone.
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
        """Give a waiting build its code. False if nobody is still waiting for
        it, or if it is not six digits.

        Checked here rather than at the prompt so every answerer gets the same
        rule: a mistyped code costs the account an attempt, and OpenAI counts
        those.

        The deadline is checked for the same reason. A build whose wait has
        run out has already reported that no code arrived and moved on, so
        taking one afterwards changes nothing and tells whoever typed it that
        it did - and this is the answer they would act on, because it is the
        only thing on screen that says whether the account went through.
        """
        digits = (code or "").strip()
        if not CODE.fullmatch(digits) or request.seconds_left <= 0:
            return False
        request.code = digits
        request.answered.set()
        return True

    def give_up(self, request: Request) -> None:
        """Stop waiting - the code never came, or nobody is there to type it."""
        request.answered.set()
