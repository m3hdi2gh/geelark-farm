"""Parse and check proxies.

A proxy is bound to a phone at creation time, so the device never reaches the
network unproxied. That makes parsing a spend gate: a malformed row must fail
here, before a phone exists.

Vendors hand out four shapes and none of them is canonical, so all four are
accepted and normalised.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .api import Client

log = logging.getLogger(__name__)

# GeeLark accepts these three; anything else is a typo, not a protocol.
SCHEMES = ("socks5", "http", "https")


class ProxyError(ValueError):
    """The proxy string cannot be understood, or the proxy does not work."""


@dataclass(frozen=True)
class Proxy:
    scheme: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    @property
    def url(self) -> str:
        """The canonical form GeeLark's proxyInformation field expects."""
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    def __str__(self) -> str:
        """Safe for logs: shows the endpoint, never the password."""
        who = f"{self.username}:***@" if self.username else ""
        return f"{self.scheme}://{who}{self.host}:{self.port}"


def parse(raw: str) -> Proxy:
    """Accept the formats proxy vendors actually hand out:

        socks5://user:pass@host:port      full URL
        user:pass@host:port               no scheme (socks5 assumed)
        host:port:user:pass               vendor-style colon list
        host:port                         no auth
    """
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise ProxyError("proxy is empty")

    scheme = "socks5"
    if "://" in text:
        scheme, _, text = text.partition("://")
        scheme = scheme.lower()
        if scheme not in SCHEMES:
            raise ProxyError(
                f"unsupported scheme {scheme!r} (GeeLark takes "
                f"{', '.join(SCHEMES)})"
            )

    if "@" in text:
        # rpartition so an '@' inside the password stays with the credentials.
        credentials, _, endpoint = text.rpartition("@")
        username, _, password = credentials.partition(":")
        host, _, port = endpoint.rpartition(":")
    else:
        parts = text.split(":")
        if len(parts) == 4:
            host, port, username, password = parts
        elif len(parts) == 2:
            host, port = parts
            username = password = ""
        else:
            raise ProxyError(
                f"cannot parse {raw!r}. Use one of:\n"
                "  socks5://user:pass@host:port\n"
                "  host:port:user:pass\n"
                "  host:port"
            )

    if not host:
        raise ProxyError(f"no host in {raw!r}")
    if not port.isdigit():
        raise ProxyError(f"port {port!r} is not a number in {raw!r}")
    port_number = int(port)
    if not 1 <= port_number <= 65535:
        raise ProxyError(f"port {port_number} is out of range")

    return Proxy(scheme, host, port_number, username, password)


def check(client: Client, proxy: Proxy) -> dict:
    """Ask GeeLark to test the proxy and report the outbound address.

    Called before creating a phone, so a dead proxy costs nothing. A `country`
    of None means geolocation databases do not recognise the address - a
    datacenter or freshly allocated IP, which is what draws CAPTCHAs and
    phone-verification demands from Google later.
    """
    result = client.data("/v1/proxy/check", {
        "proxyQueryChannel": "IP2Location",
        "proxyType": proxy.scheme,
        "server": proxy.host,
        "port": proxy.port,
        "username": proxy.username,
        "password": proxy.password,
    }) or {}
    if not result.get("detectStatus"):
        raise ProxyError(f"{proxy} is unusable: {result.get('message')}")
    if not result.get("country"):
        log.warning(
            "%s resolves to %s but no country - geolocation databases do not "
            "know this IP, which correlates with Google challenges",
            proxy, result.get("outboundIP"),
        )
    return result
