"""The person channel: what somebody said about a phone, and how often
this tool has tried it.

`State` and `Tries` were the two cells on the Phones tab a human wrote
into, and the only two the pass read back to be told something. They move
here together, because half of them here and half on the tab is a phone
that is Done in one place and not the other.

**Why the mirror must not carry these any more.** It rewrites the Phones
tab over this table every pass, so a state written here and mirrored from
there would be undone within thirty seconds - silently, with no error and
no event. That is the trap `owner_id` has always stood clear of, and
`_upsert_phones` now leaves these two columns alone for the same reason:
a mirror that resets them un-presses somebody's button every half minute.

The sheet's own State column stays where it is and stops meaning
anything. Nothing reads it after this.
"""

from __future__ import annotations

import logging

from ..config import Settings
from .db import Store

log = logging.getLogger(__name__)

#: The words a person may write, and the two the pass acts on. The same
#: vocabulary the sheet had, and the schema's CHECK holds it here - which
#: is more than the tab ever did: `dome` was silently nothing in a cell.
TAKEN, DONE, FAILED, UNUSED = "taken", "done", "failed", "unused"
ACTED_ON = (DONE, FAILED)


def state_of(settings: Settings, serial: str) -> str:
    """What somebody has said about this phone, right now.

    Read live rather than from anything the run remembers: the point is to
    notice a word written *since* the build started, so a run does not
    spend ten more minutes on a phone that is about to be deleted.

    Never raises. A build must not die because this read failed - the
    worst case is that it carries on, which is what it did before any of
    this existed.
    """
    wanted = str(serial or "").strip()
    if not wanted:
        return ""
    try:
        with Store(settings) as store:
            rows = store._rows(
                "SELECT state FROM phones"
                " WHERE serial = %s AND done_at IS NULL", (wanted,))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not read what was said about %s (%s)", wanted, exc)
        return ""
    return (rows[0]["state"] or "") if rows else ""


def set_state(settings: Settings, serial: str, state: str) -> bool:
    """Write what a person said. False if there is no live phone by that
    serial, which is what the caller says back rather than guessing.

    Any word but `taken` ends a Station hold, so its clocks and its viewer
    link go in the same statement (rev 42): the hold's shape constraint
    refuses a `taken_at` on a phone that is not taken."""
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET state = %s, state_at = now(), updated_at = now(),"
            " taken_at = CASE WHEN %s = 'taken' THEN taken_at END,"
            " watched_at = CASE WHEN %s = 'taken' THEN watched_at END,"
            " tab_closed_at = CASE WHEN %s = 'taken' THEN tab_closed_at END,"
            " live_url = CASE WHEN %s = 'taken' THEN live_url ELSE '' END"
            " WHERE serial = %s AND done_at IS NULL RETURNING id",
            (state, state, state, state, state, str(serial).strip()))
    return bool(rows)


def count_try(settings: Settings, serial: str) -> int:
    """Record one more attempt on this phone and say how many that makes.

    One statement, so two runs counting at once cannot both read three and
    both write four.
    """
    wanted = str(serial or "").strip()
    if not wanted:
        return 0
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET tries = tries + 1, updated_at = now()"
            " WHERE serial = %s AND done_at IS NULL RETURNING tries",
            (wanted,))
    return int(rows[0]["tries"]) if rows else 0


def clear_tries(settings: Settings, serial: str) -> bool:
    """Put a given-up phone back in the queue."""
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET tries = 0, updated_at = now()"
            " WHERE serial = %s AND done_at IS NULL RETURNING id",
            (str(serial).strip(),))
    return bool(rows)


def marked(settings: Settings) -> list[dict]:
    """Phones somebody has closed as done or failed, with what is on them.

    The shape the sheet handed back, `sheet_row` included - it is this
    table's own `id`, which is what closes the row.

    It was left out, on the reasoning that "there are no sheet rows here,
    and the one caller that used it was deleting the row it named". That
    caller still names it: `apply_phone_states` collects `row["sheet_row"]`
    (builder.py) and hands the list to `PgPhoneLog.delete_rows`, which
    closes rows BY ID. Without the key it raised KeyError - and raised it
    *after* the irreversible half had run, so the GeeLark phone was
    deleted, the Gmail retired and the app account delivered or freed,
    while the row stayed open for the next pass to do all of it again. The
    step guard swallowed the crash into one log line. Nobody had pressed
    Done or Failed since POOLS_IN_PG went on, so it never fired
    (2026-09-06, found by audit).
    """
    with Store(settings) as store:
        return store._rows(
            "SELECT id AS sheet_row, serial, state,"
            " coalesce(gmail, '') AS gmail,"
            " coalesce(app_account, '') AS app_account"
            " FROM phones WHERE done_at IS NULL AND state = ANY(%s)"
            " ORDER BY serial", (list(ACTED_ON),))


def watch(settings: Settings, serial: str, user_id: int | None = None) -> bool:
    """A console Live tab is open on this phone right now. True while the
    phone is taken; False once it has been put back or has gone, so the
    tab can say so instead of beating for nobody.

    A Station hold is beaten only by its holder's tab: an old Live tab left
    open in the background never keeps somebody's hold alive (rev 42)."""
    wanted = str(serial or "").strip()
    if not wanted:
        return False
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET watched_at = now(), tab_closed_at = NULL"
            " WHERE serial = %s AND done_at IS NULL AND state = 'taken'"
            " AND (taken_at IS NULL OR owner_id = %s)"
            " RETURNING id", (wanted, _uid(user_id)))
    return bool(rows)


def tab_closed(settings: Settings, serial: str,
               user_id: int | None = None) -> bool:
    """A console Live tab on this phone is closing - its pagehide beacon.
    A reload fires it too, and the reloaded page's first beat clears it
    within seconds; the sweep waits longer than that before acting.

    Somebody else's old tab closing never switches a Station hold off."""
    wanted = str(serial or "").strip()
    if not wanted:
        return False
    with Store(settings) as store:
        rows = store._write(
            "UPDATE phones SET tab_closed_at = now()"
            " WHERE serial = %s AND done_at IS NULL AND state = 'taken'"
            " AND (taken_at IS NULL OR owner_id = %s)"
            " RETURNING id", (wanted, _uid(user_id)))
    return bool(rows)


def _uid(user_id) -> int | None:
    return int(user_id) if user_id is not None else None
