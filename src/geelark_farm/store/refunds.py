"""The addresses the seller owes for.

Two refusals say nothing about the phone or the exit and everything about
the account somebody sold us: Google asking for a phone number for the
account itself, and a password that was never right. `store.ladder`
takes those rows out of the pool as `to_claim`; this is the other half -
what a person does about one once the seller has been asked.

Three words and no more: `to_claim` is owed, `claimed` is paid back,
`refused` is the seller saying no. A row that is settled either way stops
being counted, and none of the three ever puts the address back in the
pool: nothing here is about stock, it is about money.
"""

from __future__ import annotations

import logging

from ..config import Settings
from .db import connect

log = logging.getLogger(__name__)

#: What a refund row can be, and the sentence each one writes.
STATES = {
    "to_claim": "On the list to claim back from the seller",
    "claimed": "The seller paid this one back",
    "refused": "The seller would not pay this one back",
}


def mark(settings: Settings, *, address: str, state: str,
         by: str = "") -> dict | None:
    """Move one address between the three words. The row, or None when no
    Gmail has that address or the word is not one of ours."""
    wanted = str(state or "").strip().lower()
    if wanted not in STATES or not str(address or "").strip():
        return None
    said = f"{STATES[wanted]} ({by or 'somebody'})."
    with connect(settings) as conn:
        cur = conn.execute(
            "UPDATE resources SET refund_state = %s, refund_at = now(),"
            " note = left(coalesce(note, '') || ' ' || %s, 400),"
            " updated_at = now()"
            " WHERE kind = 'gmail' AND lower(address) = lower(%s)"
            "   AND coalesce(refund_state, '') <> ''"
            " RETURNING id, address, refund_state",
            (wanted, said, str(address).strip()))
        row = cur.fetchone()
        conn.commit()
    if row is None:
        return None
    log.info("%s is now %s on the refund list (%s)", row[1], row[2],
             by or "somebody")
    return {"id": int(row[0]), "address": str(row[1]),
            "refund_state": str(row[2])}
