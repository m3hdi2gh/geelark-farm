"""The exit a phone goes out through, and the check that it works at all.

`parse` had tests scattered through test_ledger.py; `check` had none. It is
called before every phone is created, and its whole job is that a dead proxy
costs nothing - so a check that cannot tell a working exit from a dead one is
worse than no check, because a build trusts it (2026-08-26).
"""

from __future__ import annotations

import dataclasses

import pytest

from geelark_farm.proxy import Proxy, ProxyError, check, parse

GOOD = "socks5://user:pass@1.2.3.4:1080"


class Panel:
    """GeeLark answering a proxy check."""

    def __init__(self, answer):
        self.answer = answer
        self.asked: list[tuple[str, dict]] = []

    def data(self, path, payload=None, **kwargs):
        self.asked.append((path, payload or {}))
        return self.answer


# ------------------------------------------------------------- the verdict
def test_a_proxy_the_vendor_says_works_is_accepted():
    """`detectStatus` is the trustworthy field: the proxy either carried the
    request or it did not."""
    panel = Panel({"detectStatus": True, "outboundIP": "5.6.7.8"})

    result = check(panel, parse(GOOD))

    assert result["outboundIP"] == "5.6.7.8"


def test_a_proxy_the_vendor_refuses_is_named_as_unusable():
    """The counterweight, and the half that matters: this runs before a phone
    is created so that a dead exit costs nothing. Reading the verdict the
    wrong way round spends a phone on a proxy that cannot carry a request -
    or throws away one that can."""
    panel = Panel({"detectStatus": False, "message": "Proxy connection failed"})

    with pytest.raises(ProxyError) as caught:
        check(panel, parse(GOOD))

    said = str(caught.value)
    assert "unusable" in said
    assert "Proxy connection failed" in said, "the vendor's own reason is the"\
                                              " only actionable part"
    assert "1.2.3.4" in said, "which proxy is the other half"


def test_an_answer_with_no_verdict_in_it_is_not_a_pass():
    """A missing field is not a yes. GeeLark answering something this does not
    recognise has to be refused, not assumed."""
    with pytest.raises(ProxyError):
        check(Panel({}), parse(GOOD))

    with pytest.raises(ProxyError):
        check(Panel(None), parse(GOOD))


def test_the_check_asks_about_the_proxy_it_was_given():
    """Every field goes to the vendor, and the scheme with them - a socks5
    proxy tested as http answers about a service that is not there."""
    panel = Panel({"detectStatus": True})

    check(panel, parse(GOOD))

    path, payload = panel.asked[0]
    assert path.endswith("/proxy/check")
    assert payload["server"] == "1.2.3.4"
    assert payload["port"] == 1080
    assert payload["username"] == "user"
    assert payload["password"] == "pass"
    assert payload["proxyType"] == "socks5"


def test_an_exit_that_differs_from_the_gateway_is_still_usable():
    """A backconnect pool comes out somewhere else, which is normal and worth
    logging - not a refusal. The exit IP is the one Google judges."""
    panel = Panel({"detectStatus": True, "outboundIP": "9.9.9.9"})

    assert check(panel, parse(GOOD))["outboundIP"] == "9.9.9.9"


# --------------------------------------------------------- reading the string
def test_a_port_outside_the_range_is_refused_at_both_ends():
    """1 and 65535 are real ports. Refusing either rejects a proxy that
    works, and accepting 0 or 65536 sends GeeLark a configuration it cannot
    use."""
    assert parse("socks5://u:p@1.2.3.4:1").port == 1
    assert parse("socks5://u:p@1.2.3.4:65535").port == 65535

    for bad in ("0", "65536", "99999"):
        with pytest.raises(ProxyError, match="out of range"):
            parse(f"socks5://u:p@1.2.3.4:{bad}")


# ------------------------------------------------------------ what it is
def test_a_proxy_cannot_be_changed_once_it_is_read():
    """One Proxy object is passed from the sheet to the build, to the ledger
    and into GeeLark's own configuration. Editing it anywhere edits it for
    everyone holding it - the same reason `failures.py`'s verdicts and
    `Settings` are frozen, and the third place the gap turned up (2026-08-26).
    """
    proxy = parse(GOOD)

    with pytest.raises(dataclasses.FrozenInstanceError):
        proxy.host = "9.9.9.9"


def test_a_proxy_never_prints_its_own_password():
    """It goes in log lines, in sheet notes and in the console. The address is
    the useful half; the password is the half that must not travel."""
    said = str(parse(GOOD))

    assert "1.2.3.4" in said
    assert "pass" not in said


def test_two_proxies_with_the_same_details_are_the_same_proxy():
    """Frozen and comparable, which is what lets a set of them answer "is this
    exit already behind a phone"."""
    assert parse(GOOD) == parse(GOOD)
    assert len({parse(GOOD), parse(GOOD)}) == 1
    assert Proxy("socks5", "1.2.3.4", 1080, "u", "p") != \
        Proxy("socks5", "1.2.3.4", 1081, "u", "p")
