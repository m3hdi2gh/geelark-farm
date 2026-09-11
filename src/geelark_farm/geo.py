"""Where an exit address is, so the phone can keep the same clock.

Every exit the farm has is a hosting address in the Netherlands, Turkey
or Poland, and every phone GeeLark makes for the US region keeps a US
clock - six or seven hours of dissonance between the address Google sees
and the timezone the device reports, which is one of the plainest
proxy signals there is (the sign-in research, 2026-09-10). One lookup
per address, remembered in the store; never fatal, never on the build's
critical path for more than a few seconds.

Two sources, in this order: `by_proxy` asks GeeLark's own proxy check,
which answers with the exit address and its country, city and timezone;
`lookup` asks ip-api.com by address. The second is the fallback and not
the first because this server resolves neither `ip-api.com` nor much
else - the DNS it has answers for some hosts and not others - and a
source that cannot be reached is not a source (2026-09-12).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request

from .config import Settings

log = logging.getLogger(__name__)

#: The free lookup, its fields, and how long an answer is kept.
LOOKUP_URL = "http://ip-api.com/json/{ip}?fields=status,countryCode,timezone,isp,hosting"
LOOKUP_SECONDS = 6.0
KEEP_SECONDS = 30 * 24 * 3600
STATE_KEY = "geo_ips"
_memory: dict[str, dict] = {}


def _fetch(ip: str) -> dict | None:
    with urllib.request.urlopen(LOOKUP_URL.format(ip=ip),
                                timeout=LOOKUP_SECONDS) as answer:
        data = json.loads(answer.read().decode("utf-8", "replace"))
    if data.get("status") != "success":
        return None
    return {"cc": str(data.get("countryCode") or ""),
            "tz": str(data.get("timezone") or ""),
            "isp": str(data.get("isp") or "")[:60],
            "hosting": bool(data.get("hosting")), "at": time.time()}


def _remembered(settings: Settings | None) -> dict:
    if settings is not None and getattr(settings, "store_enabled", False):
        try:
            from .store import state as store_state

            return dict(store_state.get(settings, STATE_KEY, {}) or {})
        except Exception as exc:                                  # noqa: BLE001
            log.debug("could not read the geo cache (%s)", exc)
    return dict(_memory)


def _remember(settings: Settings | None, known: dict) -> None:
    _memory.clear()
    _memory.update(known)
    if settings is not None and getattr(settings, "store_enabled", False):
        try:
            from .store import db
            from .store import state as store_state

            with db.connect(settings) as conn:
                store_state.put(conn, STATE_KEY, known)
                conn.commit()
        except Exception as exc:                                  # noqa: BLE001
            log.debug("could not write the geo cache (%s)", exc)


def remember_check(settings: Settings | None, seen: dict, *,
                   also: str = "") -> dict | None:
    """Keep what a proxy check already said about an exit.

    Every build checks its proxy before a phone goes behind it, and that
    answer carries the country, the city and the timezone. Reading it here
    means the clock costs no second call: the build's own check is the
    lookup (2026-09-12).
    """
    place = {"cc": str((seen or {}).get("countryCode") or ""),
             "tz": str((seen or {}).get("timezone") or ""),
             "isp": str((seen or {}).get("isp") or "")[:60],
             "city": str((seen or {}).get("city") or "")[:40],
             "at": time.time()}
    if not place["tz"] and not place["cc"]:
        return None
    ip = str((seen or {}).get("outboundIP") or "").strip()
    known = _remembered(settings)
    if ip:
        known[ip] = place
    if also and also != ip:
        known[also] = dict(place, via=f"geelark:{ip or 'the proxy'}")
    if ip or also:
        _remember(settings, known)
    return dict(place, ip=ip)


def known(settings: Settings | None, ip: str) -> dict | None:
    """What is already remembered about an address, asking nobody."""
    ip = (ip or "").strip()
    if not ip:
        return None
    hit = _remembered(settings).get(ip)
    if hit and time.time() - float(hit.get("at") or 0) < KEEP_SECONDS:
        return hit
    return None


def by_proxy(client, proxy, settings: Settings | None = None, *,
             also: str = "") -> dict | None:
    """Where this exit comes out, asked of GeeLark itself.

    `/v1/proxy/check` answers with the outbound address and the country,
    city and timezone it resolves to - the same lookup the panel's "match
    the IP" setting uses. It is the source now, and `lookup` below is the
    fallback, because this server cannot resolve `ip-api.com` at all: for
    a whole day every build logged "could not place exit ... name
    resolution" and not one phone's clock was ever set (2026-09-12).

    The answer is remembered under the address GeeLark measured and, when
    `also` is given, under the exit the proxy row already carried - the
    same gateway and the same credential, so the country is the country;
    `via` says the entry was not measured at that address.
    """
    from .proxy import check

    try:
        seen = check(client, proxy) or {}
    except Exception as exc:                                      # noqa: BLE001
        log.warning("GeeLark could not place %s (%s); the phone keeps its "
                    "clock", proxy, exc)
        return None
    return remember_check(settings, seen, also=also)


def lookup(settings: Settings | None, ip: str) -> dict | None:
    """{cc, tz, isp, hosting} for an address, or None when nothing could
    say. Remembered for a month."""
    ip = (ip or "").strip()
    if not ip:
        return None
    known = _remembered(settings)
    hit = known.get(ip)
    if hit and time.time() - float(hit.get("at") or 0) < KEEP_SECONDS:
        return hit
    try:
        found = _fetch(ip)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not place exit %s (%s); the phone keeps its clock",
                    ip, exc)
        return hit
    if found is None:
        return hit
    known[ip] = found
    _remember(settings, known)
    return found


def timezone_for(settings: Settings | None, ip: str) -> str:
    found = lookup(settings, ip)
    return str((found or {}).get("tz") or "")


def country_for(settings: Settings | None, ip: str) -> str:
    found = lookup(settings, ip)
    return str((found or {}).get("cc") or "")
