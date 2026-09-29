"""What an operator pressed on a phone, one row a press.

Done, Decline and OR on the Live tab and the dashboard end a phone (the
Station adds Auth and Failed); Done delivers it, the others write it off,
and which of them was pressed is the operator's own reason, kept as
`button`. The row carries what the phone was at that moment - its Gmail,
the app account, the exit it was built behind by name, by
host:port:username and by outbound address - so the verdicts can be
queried later by any of them without joining back to rows that are gone
by then (2026-09-29).

`record` never raises: a row that is not written costs a warning, never
the press. `close` is the Station's verdict and raises: it closes the
phone and writes the row in one statement, so there is no press without
its row and no row without its press (rev 42).
"""

from __future__ import annotations

import logging

from ..config import Settings, machine
from .db import Store, connect
from .station import POWER_STALE_SECONDS

log = logging.getLogger(__name__)

#: The five keys of the Station, in the order they are drawn and counted.
KEYS = ("done", "decline", "or", "auth", "failed")
#: How old a pending Boot or Change IP may be before it no longer holds a
#: phone (an orphan a restart left behind), as SQL.
_STALE = f"interval '{POWER_STALE_SECONDS} seconds'"

#: The buttons a verdict can come from, and the state each means.
BUTTONS = {"done": "done", "failed": "failed", "decline": "failed",
           "or": "failed", "auth": "failed"}


def record(settings: Settings, *, serial: str, button: str, state: str,
           by: str = "", by_id=None, where: str = "") -> bool:
    """One row for a press on `serial`, read off the phone as it stands
    now and the proxy row its exit name points at."""
    try:
        with connect(settings) as conn:
            phone = conn.execute(
                "SELECT p.phone_id, p.gmail, p.app_account, p.proxy_name,"
                " p.exit_ip, r.host, r.port, r.username"
                " FROM phones p LEFT JOIN resources r"
                "   ON r.kind = 'proxy' AND r.proxy_name = p.proxy_name"
                " WHERE p.serial = %s AND p.done_at IS NULL"
                " ORDER BY p.id DESC LIMIT 1", (str(serial or ""),)).fetchone()
            (phone_id, gmail, app_account, proxy_name, exit_ip,
             host, port, username) = phone or (None,) * 8
            conn.execute(
                "INSERT INTO verdicts (machine, by_name, by_id, button, state,"
                " serial, phone_id, gmail, app_account, proxy_name,"
                " proxy_host, proxy_port, proxy_username, exit_ip, pressed_on)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s, %s)",
                (machine(), str(by or "")[:80],
                 int(by_id) if str(by_id or "").isdigit() else None,
                 str(button or "")[:16], str(state or "")[:16],
                 str(serial or ""), str(phone_id or ""),
                 str(gmail or "").lower(), str(app_account or ""),
                 str(proxy_name or "")[:80], str(host or "")[:120],
                 int(port) if port else None, str(username or "")[:120],
                 str(exit_ip or "")[:45], str(where or "")[:16]))
            conn.commit()
        return True
    except Exception as exc:                                      # noqa: BLE001
        log.warning("the %s on phone %s was not recorded (%s); the press "
                    "is unaffected", button, serial, exc)
        return False


#: One statement closes the phone and writes its verdict row. A second
#: verdict, a phone gone, a phone building, a phone whose Boot or Change IP
#: is still running, or (with an owner) a phone that is not theirs writes
#: nothing. An Other phone's carried password goes in the same statement.
_CLOSE = (
    "WITH phone AS ("
    "  UPDATE phones SET state = %(state)s, state_at = now(), updated_at = now(),"
    "         owner_id = NULL, taken_at = NULL, watched_at = NULL,"
    "         tab_closed_at = NULL, live_url = ''"
    "   WHERE serial = %(serial)s AND done_at IS NULL"
    "     AND state IN ('', 'unused', 'taken') AND status <> 'building'"
    "     AND (%(owner)s::bigint IS NULL OR owner_id = %(owner)s::bigint)"
    "     AND NOT EXISTS (SELECT 1 FROM actions x"
    "                      WHERE x.status IN ('queued', 'running')"
    "                        AND x.verb IN ('boot_phone', 'change_proxy')"
    "                        AND x.payload->>'serial' = phones.serial"
    "                        AND coalesce(x.executed_at, x.requested_at)"
    "                            > now() - " + _STALE + ")"
    "  RETURNING id, phone_id, gmail, app_account, proxy_name, exit_ip, purpose),"
    " carried AS ("
    "  UPDATE wanted_builds w SET carry_password = '', updated_at = now()"
    "    FROM phone WHERE lower(phone.purpose) = 'other' AND w.serial = %(serial)s"
    "     AND w.carry_password <> ''"
    "  RETURNING w.id)"
    " INSERT INTO verdicts (machine, by_name, by_id, button, state, serial,"
    "                       phone_id, gmail, app_account, proxy_name, proxy_host,"
    "                       proxy_port, proxy_username, exit_ip, pressed_on,"
    "                       lane, phone_row)"
    " SELECT %(machine)s, %(by)s, %(by_id)s, %(button)s, %(state)s, %(serial)s,"
    "        coalesce(phone.phone_id, ''), lower(coalesce(phone.gmail, '')),"
    "        coalesce(phone.app_account, ''), coalesce(phone.proxy_name, ''),"
    "        coalesce(r.host, ''), r.port, coalesce(r.username, ''),"
    "        coalesce(phone.exit_ip, ''), %(where)s,"
    "        CASE WHEN lower(phone.purpose) = 'spotify' THEN 'spotify'"
    "             WHEN lower(phone.purpose) = 'other' THEN 'other' ELSE 'gpt' END,"
    "        phone.id"
    "   FROM phone"
    "   LEFT JOIN LATERAL (SELECT host, port, username FROM resources"
    "                       WHERE kind = 'proxy' AND proxy_name = phone.proxy_name"
    "                       ORDER BY id DESC LIMIT 1) r ON true"
    " RETURNING id, at, button, serial, gmail, proxy_name, exit_ip, lane")


def close(settings: Settings, *, serial: str, button: str, state: str,
          by: str = "", by_id=None, where: str = "",
          owner_id: int | None = None) -> dict | None:
    """Close the phone and write its verdict, in one statement. None when
    nothing was closed (`standing` says why). Raises."""
    if state not in ("done", "failed"):
        raise ValueError(f"a verdict closes a phone as done or failed, "
                         f"not {state!r}")
    params = {
        "state": state, "serial": str(serial or ""),
        "owner": int(owner_id) if owner_id is not None else None,
        "machine": machine(), "by": str(by or "")[:80],
        "by_id": int(by_id) if str(by_id or "").isdigit() else None,
        "button": str(button or "")[:16], "where": str(where or "")[:16]}
    with connect(settings) as conn:
        cur = conn.execute(_CLOSE, params)
        row = cur.fetchone()
        names = [d.name for d in cur.description]
        conn.commit()
    return dict(zip(names, row, strict=True)) if row is not None else None


def standing(settings: Settings, serial: str) -> dict | None:
    """Why a close did nothing: `{"state", "owner_id", "status", "busy"}`,
    busy being the verb of a fresh pending Boot or Change IP, else ""."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT p.state, p.owner_id, p.status,"
            " coalesce((SELECT x.verb FROM actions x"
            "            WHERE x.status IN ('queued', 'running')"
            "              AND x.verb IN ('boot_phone', 'change_proxy')"
            "              AND x.payload->>'serial' = p.serial"
            "              AND coalesce(x.executed_at, x.requested_at)"
            "                  > now() - " + _STALE +
            "            ORDER BY x.id LIMIT 1), '') AS busy"
            " FROM phones p WHERE p.serial = %s AND p.done_at IS NULL"
            " ORDER BY p.id DESC LIMIT 1", (str(serial or ""),))
    return rows[0] if rows else None


def of_person(settings: Settings, by_id: int, since, until) -> list[dict]:
    """This person's results between `since` and `until`, newest first -
    the bar's counts and the profile's list are these same rows. The blank
    phantom rows the old `record` could write are left out."""
    with Store(settings) as store:
        return store._rows(
            "SELECT v.id, v.at, v.button, v.serial, v.gmail, v.proxy_name,"
            " v.exit_ip, CASE WHEN v.lane = '' THEN 'gpt' ELSE v.lane END AS lane"
            " FROM verdicts v"
            " WHERE v.by_id = %s AND v.at >= %s AND v.at < %s"
            "   AND v.button = ANY(%s)"
            "   AND (v.phone_row IS NOT NULL OR v.phone_id <> '')"
            " ORDER BY v.at DESC, v.id DESC",
            (int(by_id), since, until, list(KEYS)))
