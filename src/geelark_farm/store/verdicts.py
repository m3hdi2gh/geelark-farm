"""What an operator pressed on a phone, one row a press.

Done, Decline and OR on the Live tab and the dashboard end a phone; Done
delivers it, the other two write it off, and which of the two was pressed
is the operator's own reason, kept as `button`. The row carries what the
phone was at that moment - its Gmail, the app account, the exit it was
built behind by name, by host:port:username and by outbound address - so
the verdicts can be queried later by any of them without joining back to
rows that are gone by then (2026-09-29).

Recording never raises: a row that is not written costs a warning, never
the press.
"""

from __future__ import annotations

import logging

from ..config import Settings, machine
from .db import connect

log = logging.getLogger(__name__)

#: The buttons a verdict can come from, and the state each means.
BUTTONS = {"done": "done", "failed": "failed",
           "decline": "failed", "or": "failed"}


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
