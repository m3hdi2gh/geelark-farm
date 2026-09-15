"""Phones somebody took or booted from the console and forgot.

Boot starts a phone in GeeLark and writes it `taken`; Take writes it
`taken` and the person opens it by hand. Either way the phone bills by
the minute until somebody presses Release or Power off - and some never
do (the operator, 2026-09-15: "some operators forget to switch the phone
off, and their phones just burn money"). So once a pass the keeper reads
the phones that have been taken, or on with no run holding them, for
longer than RELEASE_AFTER_MINUTES, switches them off in GeeLark and puts
them back on the shelf. An event says which, whose, and for how long.

What is left alone:

- a phone a build or a finish has: `status = building`, or a live claim
  in the ledger. The run switches its own phone off when it is done;
- a hand-built phone's reservation. It is switched off like any other
  phone, but stays with the person who asked for it - the build card
  promised them that phone, and an hour of not using it is not the same
  as forgetting it;
- everything, when GeeLark would not list its phones this pass. The
  store's picture of what is on may be stale, and stopping is an API
  call best made against the listing it was decided from.

The clock is two columns on the phone row (schema rev 29): `state_at`,
stamped by every write of `state`, and `running_since`, set by the pass
that first sees the phone on and cleared by the one that sees it off.
"""

from __future__ import annotations

import logging

from . import phones as phones_mod

log = logging.getLogger(__name__)

#: The two GeeLark states in which a phone is billing.
ON = (phones_mod.RUNNING, phones_mod.STARTING)


def overdue(settings, minutes: int) -> list[dict]:
    """The phones over the clock: taken by a person (not by a build card)
    for longer than `minutes`, or on for longer with nobody's run on them.
    A row still `building` belongs to its run and is never here."""
    from .store.db import Store

    with Store(settings) as store:
        return store._rows(
            "SELECT p.serial, p.status, p.state,"
            " p.built_by IS NOT NULL AS hand_built,"
            " coalesce(u.username, '') AS owner,"
            " extract(epoch FROM now() - p.state_at) AS taken_seconds,"
            " extract(epoch FROM now() - p.running_since) AS on_seconds"
            " FROM phones p LEFT JOIN users u ON u.id = p.owner_id"
            " WHERE p.done_at IS NULL AND p.status <> 'building'"
            "   AND ((p.state = 'taken' AND p.built_by IS NULL"
            "         AND p.state_at < now() - %s * interval '1 minute')"
            "     OR (p.running AND p.running_since IS NOT NULL"
            "         AND p.running_since < now() - %s * interval '1 minute'))"
            " ORDER BY p.serial", (int(minutes), int(minutes)))


def _release(settings, serial: str) -> bool:
    """Put the phone back: nobody's, and the clock restarted. True if the
    row was still taken to be put back."""
    from .store.db import Store

    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET state = '', owner_id = NULL,"
            " state_at = now(), updated_at = now()"
            " WHERE serial = %s AND done_at IS NULL AND state = 'taken'"
            " RETURNING id", (str(serial),))
    return bool(rows)


def _span(seconds) -> str:
    """"1 h 12 min", or "48 min"."""
    whole = max(0, int(float(seconds or 0)))
    hours, minutes = divmod(whole // 60, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


def _sentence(row: dict, *, off: bool, released: bool) -> str:
    owner = str(row.get("owner") or "")
    with_whom = f" with {owner}" if owner else ""
    if released:
        since = _span(row.get("taken_seconds"))
        if off:
            return (f"switched off and put back after {since}{with_whom} - "
                    f"nobody pressed Release")
        return (f"put back after {since}{with_whom} - nobody pressed "
                f"Release (it was already off)")
    since = _span(row.get("on_seconds"))
    if row.get("hand_built"):
        whose = owner or "its builder"
        return (f"switched off after {since} on - still {whose}'s, as the "
                f"build card promised")
    if row.get("state") == "taken":
        return f"switched off after {since} on{with_whom}"
    return (f"switched off after {since} on with nobody here holding it "
            f"- booted by hand in GeeLark, or released while still up")


def sweep(client, settings, ledger, listing: list[dict] | None) -> dict:
    """Switch off and put back what is over RELEASE_AFTER_MINUTES.

    `listing` is this pass's GeeLark listing - the same one the shadow
    marked `running` from a moment ago - so no extra call is spent, and
    None (GeeLark would not list) means nothing is touched. Never raises:
    a phone that would not stop is logged and tried again next pass, and
    is not put back while it is still billing.
    """
    # Inside, like every other store import outside the store package.
    from .store import events as store_events

    outcome: dict = {"off": [], "released": [], "held": []}
    minutes = int(getattr(settings, "release_after_minutes", 0) or 0)
    if (minutes <= 0 or not getattr(settings, "store_enabled", False)
            or listing is None):
        return outcome
    try:
        rows = overdue(settings, minutes)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not read the forgotten phones (%s)", exc)
        return outcome
    if not rows:
        return outcome
    live = {str(p.get("serialNo")): p for p in listing}
    for row in rows:
        serial = str(row["serial"])
        phone = live.get(serial)
        phone_id = str(phone.get("id") or "") if phone else ""
        held = (ledger.get(phone_id)
                if ledger is not None and phone_id else None)
        if held is not None and held.is_claimed and not held.is_stale:
            # A finish or a build has it: its run switches it off.
            outcome["held"].append(serial)
            continue
        on = phone is not None and phone.get("status") in ON
        if on:
            try:
                phones_mod.stop(client, phone_id)
            except Exception as exc:                              # noqa: BLE001
                log.warning("forgotten phone %s would not stop (%s); "
                            "tried again next pass", serial, exc)
                continue
            outcome["off"].append(serial)
        released = False
        if row.get("state") == "taken" and not row.get("hand_built"):
            try:
                released = _release(settings, serial)
            except Exception as exc:                              # noqa: BLE001
                log.warning("forgotten phone %s was not put back (%s)",
                            serial, exc)
        if released:
            outcome["released"].append(serial)
        if not (on or released):
            # The store said on, the listing says off: somebody got there
            # first, and the next shadow clears the clock. Nothing to say.
            continue
        detail = _sentence(row, off=on, released=released)
        log.info("phone %s: %s", serial, detail)
        store_events.emit(settings, "phone", serial=serial,
                          status="released" if released else "switched off",
                          detail=detail)
    return outcome
