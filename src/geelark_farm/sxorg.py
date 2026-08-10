"""The proxy vendor's API - one call, the one that matters.

sx.org can hand a proxy a new exit address while keeping its host, port and
credentials. That is worth more here than buying another proxy: nothing on the
phone changes, so a build that has been refused by an exit can try again
through the same connection details and a different address.

The allowance is three a day per proxy, so the count is spent deliberately and
recorded in the sheet - see ProxyPool.refreshes_today. Exceeding it is not an
error worth crashing a build over; it just means the next proxy.

Only the proxies in sx.org's *port* product can be refreshed this way, because
`portId` is what identifies them and it comes from `/v2/proxy/ports`. The
"Unlimited proxies" product does not appear in that listing at all (measured
2026-08-11: `countProxies 0` under every filter, while the panel showed them),
so a Proxy tab row with no `Port ID` simply cannot be refreshed. That is why
`refresh()` is asked for permission rather than assumed to work.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

BASE = "https://api.sx.org"

# What the vendor allows per proxy per day. Spent from the sheet's `Last
# Refresh` column, so the count survives a run ending.
REFRESHES_PER_DAY = 3

TIMEOUT = 30


class SxError(Exception):
    """The vendor refused, or could not be reached."""


def refresh(api_key: str, port_id: str | int) -> None:
    """Give one proxy a new exit address, keeping its host and credentials.

    Raises rather than returning a flag: every caller has a different fallback,
    and a silent False here would be indistinguishable from "refreshed, but the
    address did not change" - which is a different situation with a different
    answer.
    """
    if not api_key:
        raise SxError("no sx.org API key is configured (SXORG_API_KEY)")
    try:
        response = requests.get(f"{BASE}/v2/proxy/refresh/{port_id}",
                                params={"apiKey": api_key}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SxError(f"could not reach sx.org: {exc}") from exc

    try:
        data = response.json()
    except ValueError:
        raise SxError(f"sx.org answered {response.status_code} with "
                      f"{response.text[:120]!r}") from None

    if not data.get("success"):
        # "proxy not found" is the one worth recognising: it means this port id
        # is not on this account, which is what happens when the proxy came
        # from the Unlimited product rather than the port product.
        raise SxError(str(data.get("message") or data)[:200])
    log.info("sx.org refreshed the exit address of port %s", port_id)
