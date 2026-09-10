"""Every Google sign-in attempt, one row - and the week's rates read
off them.

The builder writes a row the moment a sign-in ends, ok or not: which
address, on which phone model, behind which exit host, in which position
on the phone. The host gate and the Login rate page read the rates back.
Recording never raises: a row that is not written costs a warning, never
the build (the login-rate work, 2026-09-10).
"""

from __future__ import annotations

import logging

from ..config import Settings, machine
from .db import connect

log = logging.getLogger(__name__)

#: Attempts before a host, a model or a seller is judged at all.
MIN_SAMPLE = 5


def record(settings: Settings, *, serial: str, gmail: str, seller: str = "",
           host: str = "", model: str = "", position: int = 1,
           reason: str = "", ok: bool = False, seconds: float | None = None,
           captcha_rounds: int = 0) -> bool:
    try:
        with connect(settings) as conn:
            conn.execute(
                "INSERT INTO signins (machine, serial, gmail, seller, host,"
                " model, position, reason, ok, seconds, captcha_rounds)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (machine(), str(serial or ""), str(gmail or "").lower(),
                 str(seller or "")[:80], str(host or "")[:80],
                 str(model or "")[:80], int(position or 1),
                 str(reason or "")[:80], bool(ok), seconds,
                 int(captcha_rounds or 0)))
            conn.commit()
        return True
    except Exception as exc:                                      # noqa: BLE001
        log.warning("the sign-in of %s was not recorded (%s); the build is "
                    "unaffected", gmail, exc)
        return False


_BY = {"host": "host", "model": "model", "seller": "seller",
       "reason": "reason", "position": "position::text",
       "day": "to_char(at, 'YYYY-MM-DD')"}


def rates(settings: Settings, by: str, days: int = 7) -> list[dict]:
    """[{key, ok, n, rate}] for one dimension over the last `days`,
    most attempts first."""
    column = _BY[by]
    with connect(settings) as conn:
        cur = conn.execute(
            f"SELECT {column} AS key, count(*) FILTER (WHERE ok) AS ok,"
            f" count(*) AS n FROM signins"
            f" WHERE at > now() - make_interval(days => %s)"
            f" GROUP BY 1 ORDER BY n DESC, 1", (int(days),))
        rows = cur.fetchall()
        conn.rollback()
    return [{"key": str(key or ""), "ok": int(ok or 0), "n": int(n or 0),
             "rate": (int(ok or 0) / int(n)) if n else 0.0}
            for key, ok, n in rows]


def host_rates(settings: Settings, days: int = 7) -> list[dict]:
    """The hosts with enough attempts to be judged."""
    return [r for r in rates(settings, "host", days)
            if r["key"] and r["n"] >= MIN_SAMPLE]


def totals(settings: Settings, days: int = 7) -> dict:
    with connect(settings) as conn:
        cur = conn.execute(
            "SELECT count(*) FILTER (WHERE ok), count(*),"
            " count(DISTINCT gmail) FROM signins"
            " WHERE at > now() - make_interval(days => %s)", (int(days),))
        ok, n, gmails = cur.fetchone()
        conn.rollback()
    return {"ok": int(ok or 0), "n": int(n or 0), "gmails": int(gmails or 0),
            "rate": (int(ok or 0) / int(n)) if n else 0.0}
