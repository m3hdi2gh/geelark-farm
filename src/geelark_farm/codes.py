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
from typing import Protocol, runtime_checkable

log = logging.getLogger(__name__)

#: How long to wait for a code to arrive before giving up on it. Generous,
#: because the cost of waiting is a phone sitting idle and the cost of giving
#: up too early is the whole build - and mail providers are unhurried.
WAIT_SECONDS = 180

#: How often to look. Not lower: every check is a request to somebody's mail
#: server, and a code that took a minute to arrive is not made faster by
#: asking twice a second.
POLL_SECONDS = 10

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
