"""The code emailed to an account, read from the mailbox its address
forwards into.

The operator's `eco` accounts have no password and no authenticator:
every sign-in is a fresh code, emailed to an AdGuard alias that
forwards into one Gmail the farm holds an app password for. This is
`codes.CodeSource` over that mailbox - the flows do not change, and
`codes.NoSource` remains what runs where no mailbox is configured.

Everything here was decided by reading 39 real forwarded messages
rather than by guessing, and the three that mattered are worth writing
down, because each one would have been got wrong:

- **the alias is in `To`, exactly.** `Delivered-To` is always the
  forwarding mailbox, so it says nothing about which account a message
  is for, and AdGuard *rewrites the sender* - the From address is
  `<alias>_<tag>@masked.me` and the real sender survives only in the
  display name. A rule about who sent it would have failed on every
  message.
- **the body is HTML and nothing else**, wrapped in AdGuard's relay
  template. Its markup carries three different six-digit runs - CDN
  urls, sizes - so reading the first six digits of the source returns
  a number that is not the code. The visible text is what is read.
- **exactly one six-digit run in the visible text is the code.** Across
  those 39 messages this separated the 32 code mails from the 7 "New
  sign-in to your OpenAI account" notifications perfectly, in four
  subject wordings and two languages. A rule that matched the subject
  would have missed the Persian ones; this one does not read words at
  all.

What this deliberately does not do is judge the account. A mailbox
that will not answer, or a code that never arrives, is not the
account's fault, and `MailboxError` is raised rather than returning
None so the caller can tell "we could not look" from "we looked and
nothing came".
"""

from __future__ import annotations

import datetime
import email
import html as html_mod
import imaplib
import logging
import re
import time
from dataclasses import dataclass
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime

log = logging.getLogger(__name__)

#: How often the mailbox is asked while a flow waits. Measured on the
#: live mailbox: a code was in the inbox three seconds after it was
#: asked for, so this is prompt without being a query storm from ten
#: builds at once.
POLL_SECONDS = 5.0

#: How much older than `since` a message may be and still count. Two
#: clocks are involved - this machine's and the sender's - and a code
#: that arrives stamped a minute before it was asked for is a clock
#: difference, not a stale code. Wider than this and a previous
#: attempt's code becomes reachable, which is the one mistake that
#: costs an account rather than a wait.
SKEW_SECONDS = 90

#: Six digits standing alone, the same shape `codes.code_in` looks for.
SIX_DIGITS = re.compile(r"\b(\d{6})\b")


class MailboxError(Exception):
    """The mailbox could not be reached or would not answer. Never a
    verdict on the account: the caller turns it into a reason that
    blames the farm's own plumbing."""


@dataclass(frozen=True)
class Mailbox:
    """Where the codes land, and the credential to read them with."""

    host: str
    user: str
    password: str
    #: Gmail's own search, which reaches Spam and Trash as well - a code
    #: filed as spam is still the code (the operator, 2026-09-19). An
    #: IMAP server without it falls back to a plain header search.
    gmail_search: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    def __str__(self) -> str:
        """Safe for logs: the mailbox, never the password."""
        return f"{self.user} at {self.host}"


def visible_text(markup: str) -> str:
    """What a person would see in an HTML message: script, style and
    head thrown away, tags dropped, entities undone, runs of whitespace
    collapsed. Crude on purpose - this is not a renderer, it is the
    difference between reading the code and reading a CDN url."""
    text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", markup or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return " ".join(html_mod.unescape(text).split())


def _decoded(part: Message) -> str:
    raw = part.get_payload(decode=True)
    if raw is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def text_of(message: Message) -> str:
    """The message as words. A plain-text part is taken as it is; a
    message that has only HTML - which is every one of these - is read
    through `visible_text`."""
    html_part = ""
    for part in message.walk():
        kind = part.get_content_type()
        if kind == "text/plain":
            return " ".join(_decoded(part).split())
        if kind == "text/html" and not html_part:
            html_part = _decoded(part)
    return visible_text(html_part)


def code_in_message(message: Message) -> str | None:
    """The code in one message, or None when it holds none - or holds
    more than one, which is a message this cannot read rather than a
    choice to make between them."""
    found = set(SIX_DIGITS.findall(text_of(message)))
    return found.pop() if len(found) == 1 else None


def addressed_to(message: Message, address: str) -> bool:
    """Whether this message was really sent to that alias.

    The whole `To` header, parsed rather than searched: `a@x.com` is a
    substring of `ba@x.com`, and two accounts signing in at once must
    not be able to take each other's code.
    """
    wanted = str(address or "").strip().casefold()
    if not wanted:
        return False
    return any(found.strip().casefold() == wanted
               for _name, found in getaddresses(message.get_all("To") or []))


def arrived_at(message: Message) -> float:
    """When the sender says the message was sent, as a unix time. 0 for
    a message with no readable Date, which is then never fresh enough
    to be used - a message this cannot date is one it cannot trust.

    A Date whose zone is `-0000` or missing means "the local zone is
    not known" (RFC 5322), and `parsedate_to_datetime` hands back a
    naive datetime for it - whose `.timestamp()` reads it as *this*
    machine's local time. On a farm whose clock is Tehran that put
    every such message three and a half hours out, which is far enough
    to make a code that had just arrived look like the last attempt's
    and be refused. Read as UTC, which is what every mail client does.
    """
    try:
        moment = parsedate_to_datetime(message.get("Date"))
    except Exception:                                             # noqa: BLE001
        return 0.0
    if moment is None:
        return 0.0
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


class MailboxSource:
    """A `codes.CodeSource` over one IMAP mailbox.

    One connection per waiting flow, opened when the wait starts and
    closed when it ends: the builds that wait at once are counted in
    tens, and a connection held open for the life of the process would
    have to survive every disconnection the server decides on.
    """

    def __init__(self, mailbox: Mailbox, *, poll_seconds: float = POLL_SECONDS,
                 skew_seconds: float = SKEW_SECONDS) -> None:
        self.mailbox = mailbox
        self.poll_seconds = poll_seconds
        self.skew_seconds = skew_seconds

    # ------------------------------------------------------------ the flow
    def code_for(self, address: str, *, since: float,
                 timeout: float = 180.0) -> str | None:
        """Wait for the code emailed to `address`, up to `timeout`.

        None means the wait ran out with nothing to read, which is a
        forwarding problem or a service that never sent one - not a
        judgement on the account. A mailbox that cannot be reached
        raises instead, so the two are never reported as one thing.
        """
        if not self.mailbox.configured:
            raise MailboxError("no mailbox is configured")
        started = time.time()
        deadline = started + max(0.0, float(timeout))
        floor = float(since) - self.skew_seconds
        box = self._open()
        try:
            while True:
                code = self._look(box, address, floor)
                if code is not None:
                    log.info("the code emailed to %s was in the mailbox "
                             "after %.0fs", address, time.time() - started)
                    return code
                if time.time() + self.poll_seconds >= deadline:
                    log.warning("nothing arrived for %s in %.0fs - the "
                                "forwarding for that address is the thing "
                                "to check", address, time.time() - started)
                    return None
                time.sleep(self.poll_seconds)
        finally:
            self._close(box)

    # ------------------------------------------------------------- the imap
    def _open(self):
        try:
            box = imaplib.IMAP4_SSL(self.mailbox.host)
            box.login(self.mailbox.user, self.mailbox.password)
            return box
        except Exception as exc:                                  # noqa: BLE001
            raise MailboxError(f"{self.mailbox} would not let us in "
                               f"({exc})") from exc

    @staticmethod
    def _close(box) -> None:
        try:
            box.logout()
        except Exception as exc:                                  # noqa: BLE001
            log.debug("the mailbox did not close cleanly (%s)", exc)

    def _folder(self) -> str:
        return '"[Gmail]/All Mail"' if self.mailbox.gmail_search else "INBOX"

    def _search(self, box, address: str) -> list[bytes]:
        """The messages worth opening, newest last as IMAP returns them."""
        if self.mailbox.gmail_search:
            query = ("X-GM-RAW", f'"in:anywhere to:{address}"')
        else:
            query = ("TO", f'"{address}"')
        ok, found = box.search(None, *query)
        if ok != "OK":
            raise MailboxError(f"the mailbox refused a search ({ok})")
        return (found[0] or b"").split()

    def _look(self, box, address: str, floor: float) -> str | None:
        """One pass: the newest message to this alias, fresh enough, that
        holds one code."""
        try:
            ok, _info = box.select(self._folder(), readonly=True)
            if ok != "OK":
                raise MailboxError(f"cannot open {self._folder()}")
            numbers = self._search(box, address)
        except MailboxError:
            raise
        except Exception as exc:                                  # noqa: BLE001
            raise MailboxError(f"{self.mailbox} stopped answering "
                               f"({exc})") from exc
        # Newest first, and never more than a handful: a flow waits on
        # the code it just asked for, and everything older than `floor`
        # is another attempt's.
        for number in reversed(numbers[-_LOOK_AT:]):
            message = self._fetch(box, number)
            if message is None:
                continue
            if not addressed_to(message, address):
                continue
            when = arrived_at(message)
            if when < floor:
                # Older than this attempt: every message before it is
                # older still.
                return None
            code = code_in_message(message)
            if code is None:
                log.debug("a message to %s carries no single code; skipped",
                          address)
                continue
            return code
        return None

    @staticmethod
    def _fetch(box, number: bytes) -> Message | None:
        try:
            ok, data = box.fetch(number, "(BODY.PEEK[])")
        except Exception as exc:                                  # noqa: BLE001
            raise MailboxError(f"a message would not come back ({exc})") \
                from exc
        if ok != "OK" or not data or not isinstance(data[0], tuple):
            return None
        return email.message_from_bytes(data[0][1])


#: How many of an alias's messages one pass opens. A code arrives within
#: seconds and is read on the pass after it; anything further back is
#: another attempt's and is refused by the clock anyway. Bounded so a
#: mailbox with a year of an alias's mail in it is not downloaded to
#: answer one wait.
_LOOK_AT = 6


def from_settings(settings) -> MailboxSource | None:
    """The source this farm is configured for, or None - which is the
    caller's cue to go on using `codes.NoSource` exactly as before."""
    box = Mailbox(host=str(getattr(settings, "mail_imap_host", "") or ""),
                  user=str(getattr(settings, "mail_imap_user", "") or ""),
                  password=str(getattr(settings, "mail_imap_password", "")
                               or ""))
    if not box.configured:
        return None
    log.info("codes emailed to eco accounts are read from %s", box)
    return MailboxSource(box)
