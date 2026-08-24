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
"Unlimited proxies" product does not appear in that listing at all, so a Proxy
tab row with no `Port ID` simply cannot be refreshed. That is why `refresh()`
is asked for permission rather than assumed to work.

## The whole API, so nobody derives it twice

Nineteen endpoints, read off docs.sx.org and each one called against the live
key (2026-08-14). All are GET, all take the key as `?apiKey=`:

    /v2/user/balance              /v2/proxy/ports          /v2/dir/countries
    /v2/plan/info                 /v2/proxy/port-info      /v2/dir/states
    /v2/proxy/search              /v2/proxy/create-port    /v2/dir/cities
    /v2/proxy/refresh/{portId}    /v2/proxy/delete-port    /v2/dir/asns
    /v2/proxy/total-spent-traffic /v2/proxy/archive-port
    /v2/proxy-template            /v2/proxy/change-name
    /v2/proxy-template/create-template, /update-template, /delete-template

There is no `/v1` of any of them, and no endpoint anywhere returns the
Unlimited proxies - not `ports` under any filter including `archived`, not
`search`, not `plan/info`. That is now known by exhausting the surface rather
than by inference.

What `plan/info` does return is `urls.all`, `urls.residential` and
`urls.mobile`: download links, on an `/api/v1/proxy-list/<token>.txt` path,
holding the rotating pool this account can reach - 460,669 entries, one
gateway, a UUID per line as the username. Those work (checked through GeeLark:
three at random, three different exit addresses) and they are billed by
traffic, which is a different bargain from the flat-fee proxies in the tab.
Note the shape: one host:port, many usernames - which is why `Pool._identity`
counts the username.
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


def _scrubbed(text: str, api_key: str) -> str:
    """Take the key back out of anything about to be reported.

    This vendor wants its key in the query string - every one of its nineteen
    endpoints does - and `requests` puts the whole URL, query and all, into the
    text of a connection error:

        Max retries exceeded with url: /v2/proxy/refresh/100?apiKey=<the key>

    `_refreshed` logs that text, so one unreachable-host moment would write
    SXORG_API_KEY in clear into the day's log file - the file that gets pasted
    into a chat window when something needs looking at. Nothing had ever hit
    it, so the key had not leaked yet (checked across every log, 2026-08-25).
    """
    return text.replace(api_key, "***") if api_key else text


def _get(path: str, api_key: str) -> dict:
    """One GET, and the four ways it can fail to be an answer.

    Shared, because every endpoint here is a GET with the key as `?apiKey=`
    and they all fail the same four ways - and the scrubbing above has to
    happen on every one of them, not on whichever was written first.
    """
    if not api_key:
        raise SxError("no sx.org API key is configured (SXORG_API_KEY)")
    try:
        response = requests.get(f"{BASE}{path}",
                                params={"apiKey": api_key}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        # Not `from exc`: the cause carries the same unscrubbed URL, and a
        # traceback anywhere would print it under "the direct cause of".
        raise SxError(
            f"could not reach sx.org: {_scrubbed(str(exc), api_key)}") from None

    try:
        data = response.json()
    except ValueError:
        raise SxError(f"sx.org answered {response.status_code} with "
                      f"{_scrubbed(response.text[:120], api_key)!r}") from None

    if not data.get("success"):
        # "proxy not found" is the one worth recognising: it means this port id
        # is not on this account, which is what happens when the proxy came
        # from the Unlimited product rather than the port product.
        raise SxError(_scrubbed(str(data.get("message") or data)[:200], api_key))
    return data


def refresh(api_key: str, port_id: str | int) -> None:
    """Give one proxy a new exit address, keeping its host and credentials.

    Raises rather than returning a flag: every caller has a different fallback,
    and a silent False here would be indistinguishable from "refreshed, but the
    address did not change" - which is a different situation with a different
    answer.
    """
    _get(f"/v2/proxy/refresh/{port_id}", api_key)
    log.info("sx.org refreshed the exit address of port %s", port_id)


def port_count(api_key: str) -> int:
    """How many proxies on this account can be refreshed at all.

    Zero is the answer that matters, and it is not a failure: the Unlimited
    product does not appear in this listing, so an account holding only those
    reports none. That is the difference between "the tab is missing a column"
    - which someone can fix - and "these proxies are not the refreshable kind"
    - which no column will change (2026-08-25).

    On success this vendor puts the payload under `message`, which is a string
    on failure and a dict here. Both counts it offers read zero on an empty
    account, so there is no way to tell which is the page and which the total;
    the larger is taken, because the only question asked of this is whether
    there are any.
    """
    payload = _get("/v2/proxy/ports", api_key).get("message")
    if not isinstance(payload, dict):
        raise SxError(f"sx.org answered the port listing with "
                      f"{type(payload).__name__}, not a listing")
    pagination = payload.get("pagination")
    total = (pagination or {}).get("totalCount") if \
        isinstance(pagination, dict) else 0
    try:
        return max(int(payload.get("countProxies") or 0), int(total or 0))
    except (TypeError, ValueError) as exc:
        raise SxError(f"sx.org answered the port listing with a count "
                      f"nothing can read ({exc})") from None
