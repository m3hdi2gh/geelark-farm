"""One-time codes a person supplies, kept where every builder can read them.

`codes.Pending` holds a waiting build in memory and is answered on the
same process - a terminal prompt. The builds run in builder containers
now, and the person answering is a customer behind the panel's API, so
the wait and the answer meet here, in a table: the sign-in flow opens a
request row and polls it; the API writes the code into it.

One request is one time the app asked. `tries_left` is the contract's
three: the third wrong code ends the attempt (`wrong_code`), and
CODE_WAIT_MINUTES of silence ends it too (`code_timeout`). Rows are
never deleted - "when did the customer answer, and how long did it
take" is a question worth keeping.

Nothing here knows which app asked. The Claude flow and any other that
waits on an emailed code go through `PgCodes.code_for`, which is the
`codes.CodeSource` protocol, so the flows do not change when the source
does.
"""

from __future__ import annotations

import logging
import re
import time

from ..config import Settings
from .db import Store

log = logging.getLogger(__name__)

#: How many codes one attempt may be given. The contract's number.
TRIES = 3

#: How often a waiting flow looks for its answer. A phone is idle while
#: it waits, and a person takes tens of seconds to read an inbox; three
#: seconds is prompt without being a query storm from four builders.
POLL_SECONDS = 3.0

#: What a code looks like. Claude sends six digits; the range leaves room
#: for a service that sends four or eight without a deploy, and refuses
#: what is plainly not a code - a word, an address, an empty box.
CODE = re.compile(r"\d{4,8}")

#: The reasons a code attempt ends with, as the panel reads them. `/ready`
#: puts an account back in the queue from any of these.
REASONS = ("code_timeout", "wrong_code", "email_code_never_arrived")


def open_for(settings: Settings, address: str) -> dict | None:
    """The request an app is waiting on for this address right now - open,
    unanswered and not yet expired - or None."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT id, address, asked_at, until, tries_left"
            " FROM code_requests"
            " WHERE lower(address) = lower(%s) AND closed_at IS NULL"
            "   AND code IS NULL AND until > now()"
            " ORDER BY id DESC LIMIT 1", (str(address or "").strip(),))
    return rows[0] if rows else None


def answer(settings: Settings, address: str, code: str) -> str:
    """Give a waiting flow its code. One of:

    - `accepted`: a flow was waiting and now has it;
    - `bad_code`: not something a code looks like - nothing written;
    - `not_waiting`: no open request for this address, or it expired
      between the panel's read and this write.
    """
    digits = str(code or "").strip()
    if not CODE.fullmatch(digits):
        return "bad_code"
    with Store(settings) as store:
        rows = store._write(
            "UPDATE code_requests SET code = %s, answered_at = now()"
            " WHERE id = (SELECT id FROM code_requests"
            "             WHERE lower(address) = lower(%s)"
            "               AND closed_at IS NULL AND code IS NULL"
            "               AND until > now()"
            "             ORDER BY id DESC LIMIT 1)"
            " RETURNING id", (digits, str(address or "").strip()))
    if rows:
        log.info("a code for %s was supplied", address)
        return "accepted"
    return "not_waiting"


class PgCodes:
    """The `codes.CodeSource` a builder container uses: the request goes
    in the table, the answer comes out of it."""

    def __init__(self, settings: Settings):
        self._settings = settings
        minutes = int(getattr(settings, "code_wait_minutes", 10) or 10)
        self.wait_seconds = float(minutes * 60)

    # ------------------------------------------------------- the flow's side
    def code_for(self, address: str, *, since: float,
                 timeout: float | None = None) -> str | None:
        """Block until the panel supplies a code, or the wait runs out.

        `since` is when this sign-in began: wrong codes typed since then
        count against the contract's three, so the fourth ask is refused
        here without opening a request the panel would answer for
        nothing. `timeout` defaults to CODE_WAIT_MINUTES rather than the
        protocol's three minutes: a customer reads an inbox, a mailbox
        does not.
        """
        wait = self.wait_seconds if timeout is None else float(timeout)
        if not self._asks_a_person(address):
            # A sheet-era or console account that turned out to want an
            # emailed code: no panel will ever answer for it, and ten
            # idle minutes on a phone would not change that.
            log.info("no code asked for %s: not a customer-answered "
                     "account", address)
            return None
        tries_left = TRIES - self._wrong_since(address, since)
        if tries_left <= 0:
            log.warning("no code asked for %s: %d wrong already", address,
                        TRIES)
            return None
        request_id = self._open(address, wait, tries_left)
        log.info("waiting up to %.0fs for the panel to supply the code sent "
                 "to %s (%d tr%s left)", wait, address, tries_left,
                 "y" if tries_left == 1 else "ies")
        deadline = time.time() + wait
        while True:
            code = self._answered(request_id)
            if code:
                self._close(request_id, "typed")
                return code
            left = deadline - time.time()
            if left <= 0:
                break
            time.sleep(min(POLL_SECONDS, left))
        self._close(request_id, "timeout")
        log.warning("nobody supplied the code for %s within %.0fs", address,
                    wait)
        return None

    def wrong(self, address: str) -> None:
        """The app refused the last code typed for this address. Counted
        against the three, and said on the row that carried it."""
        with Store(self._settings) as store:
            store._write(
                "UPDATE code_requests SET outcome = 'wrong'"
                " WHERE id = (SELECT id FROM code_requests"
                "             WHERE lower(address) = lower(%s)"
                "               AND outcome = 'typed'"
                "             ORDER BY id DESC LIMIT 1) RETURNING id",
                (str(address or "").strip(),))

    # ------------------------------------------------------------- the rows
    def _open(self, address: str, wait: float, tries_left: int) -> int:
        from .. import config as _config

        with Store(self._settings) as store:
            # Whatever this address was still waiting on is over: one
            # open request per address, or the panel could answer the
            # wrong one.
            store._write(
                "UPDATE code_requests SET closed_at = now(),"
                " outcome = 'superseded'"
                " WHERE lower(address) = lower(%s) AND closed_at IS NULL"
                " RETURNING id", (address,))
            # `deadline` is the column `until` replaced in rev 30, and it
            # was left NOT NULL with no default - so an INSERT that names
            # only the new one raises NotNullViolation on the very first
            # real request, out of a flow standing on a phone with no
            # try/except around it. The table was still empty when this
            # was found (2026-09-19), which is how it survived: nothing
            # had ever opened a request against a real cluster. Written
            # with the same value as `until`, which needs no migration
            # and leaves an old reader of the column telling the truth.
            rows = store._write(
                "INSERT INTO code_requests (address, machine, until,"
                " deadline, tries_left)"
                " VALUES (%s, %s, now() + %s * interval '1 second',"
                "         now() + %s * interval '1 second', %s)"
                " RETURNING id",
                (str(address).strip(), _config.machine(), int(wait),
                 int(wait), int(tries_left)))
        return int(rows[0]["id"])

    def _answered(self, request_id: int) -> str | None:
        with Store(self._settings) as store:
            rows = store._rows(
                "SELECT code FROM code_requests WHERE id = %s", (request_id,))
        return (rows[0]["code"] or None) if rows else None

    def _close(self, request_id: int, outcome: str) -> None:
        with Store(self._settings) as store:
            store._write(
                "UPDATE code_requests SET closed_at = now(), outcome = %s"
                " WHERE id = %s AND closed_at IS NULL RETURNING id",
                (outcome, request_id))

    def _asks_a_person(self, address: str) -> bool:
        from .. import accounts as domain

        with Store(self._settings) as store:
            rows = store._rows(
                "SELECT credential_kind FROM resources"
                " WHERE kind = 'app' AND lower(address) = lower(%s)"
                " ORDER BY id DESC LIMIT 1", (str(address).strip(),))
        return bool(rows) and (
            str(rows[0]["credential_kind"] or "") == domain.ASKS_A_PERSON)

    def _wrong_since(self, address: str, since: float) -> int:
        with Store(self._settings) as store:
            rows = store._rows(
                "SELECT count(*) AS n FROM code_requests"
                " WHERE lower(address) = lower(%s) AND outcome = 'wrong'"
                "   AND asked_at >= to_timestamp(%s)",
                (str(address).strip(), float(since or 0)))
        return int(rows[0]["n"]) if rows else 0
