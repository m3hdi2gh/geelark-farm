"""Whether an exit's host is worth building on: the day's Google
challenges counted against it, the week's sign-in rate that sets its exits
aside, and a person's Free that clears it.

Two halves of one policy that sat four thousand lines apart in builder.py
and shared a store key by its spelling (the builder review, 2026-09-23).
Moved unchanged; they log under the builder's logger name, as before. A
change to how the farm judges an exit is a change to this file.

`CAPTCHAS_PER_EXIT` - two challenges on one exit, change the exit - is the
Google phase's own rule and stays with it in builder.py.
"""
from __future__ import annotations

import logging
import time

from . import failures
from .config import Settings
from .pools import Book, ProxyPool

log = logging.getLogger("geelark_farm.builder")


#: Google challenges met on one exit *host* in one day before every free
#: exit on that host is set aside as `suspect`. The vendor sells several
#: ports on one address, and Google's opinion is of the address: on
#: 190.2.143.20 one phone ate forty-three captcha rounds while phones on
#: 212.8.248.20 met three or none (the operator, 2026-09-09). Set aside
#: rather than sent to the back of the queue, so a run never reaches for
#: one; Free on the row is how a person puts it back.
CAPTCHA_STRIKES_PER_HOST = 3
#: Captcha visits in one sign-in before it counts as a strike against the
#: host. Three to five is what a young account meets anywhere; the hosts
#: worth setting aside ran to thirteen and forty-three (2026-09-09).
HEAVY_CAPTCHA_ROUNDS = 6
#: The pool's own words - see ProxyPool.suspect_status/held_back_statuses.
SUSPECT = ProxyPool.suspect_status
HELD_BACK = ProxyPool.held_back_statuses
#: Where the day's tally lives with no store: one process, one dict.
_captcha_hosts_memory: dict = {}


def _captcha_hosts(settings: Settings) -> dict:
    if getattr(settings, "store_enabled", False):
        from .store import state as store_state
        return dict(store_state.get(settings, "captcha_hosts", {}) or {})
    # A copy, like the store's answer: `_remember_captcha_hosts` clears
    # the dict before it refills it, and refilling it from itself emptied
    # the tally on every strike.
    return dict(_captcha_hosts_memory)


def _bump_captcha_host(settings: Settings, host: str, today: str) -> int:
    """One more challenge against `host` today; returns the day's count.

    One edit under the store's row lock. It was a read, a change and a
    write on two connections, from two builder replicas and the console's
    forgive_host at once - so a strike landing between the console's read
    and its write resurrected the day's strikes for a host the operator
    had just cleared, which is the very thing forgive_host was written to
    end (2026-09-21, found by audit).
    """
    def bump(hosts: dict | None) -> dict:
        hosts = dict(hosts or {})
        seen = hosts.get(host) or {}
        count = (int(seen.get("count") or 0)
                 if seen.get("day") == today else 0) + 1
        hosts[host] = {"day": today, "count": count}
        return hosts

    if getattr(settings, "store_enabled", False):
        from .store import state as store_state
        hosts = store_state.update(settings, "captcha_hosts", bump, {})
    else:
        hosts = bump(_captcha_hosts_memory)
        _captcha_hosts_memory.clear()
        _captcha_hosts_memory.update(hosts)
    return int(hosts[host]["count"])


def _struck_hosts(settings: Settings) -> set[str]:
    """The hosts at `CAPTCHA_STRIKES_PER_HOST` today. An exit a build hands
    back onto one of these goes back as `suspect`, not as stock - or the
    two exits a build held would be the first two the next build takes,
    since the tally only sets aside what is free at the moment it fills."""
    try:
        today = failures.today()
        return {host for host, seen in _captcha_hosts(settings).items()
                if seen.get("day") == today
                and int(seen.get("count") or 0) >= CAPTCHA_STRIKES_PER_HOST}
    except Exception as exc:                                       # noqa: BLE001
        log.warning("could not read the day's captcha tally (%s)", exc)
        return set()


def _strike_captcha_host(settings: Settings, book: Book,
                         proxy_row) -> list[str]:
    """One Google challenge, counted against the exit's host for today.

    At `CAPTCHA_STRIKES_PER_HOST` every free exit on that host is set
    aside as `suspect`, with the count in its note. Returns the names set
    aside. The exit the challenge was met on is not among them - it is
    held by this build, and the build's own rule (two captchas, change the
    exit) is what moves the phone off it.
    """
    host = str(getattr(getattr(proxy_row, "proxy", None), "host", "") or "")
    if not host:
        return []
    today = failures.today()
    count = _bump_captcha_host(settings, host, today)
    if count < CAPTCHA_STRIKES_PER_HOST:
        return []
    aside = []
    for resource in list(book.proxies.available):
        if str(getattr(getattr(resource, "proxy", None), "host", "")) != host:
            continue
        book.proxies.fail(resource, SUSPECT, note=(
            f"Suspect - {count} Google challenges on {host} today ({today}); "
            f"set aside on its own. Press Free to use it again."))
        aside.append(str(getattr(resource, "name", "") or resource.label))
    if aside:
        log.warning("%s: %d Google challenge(s) today; %d free exit(s) on it "
                    "set aside as suspect: %s", host, count, len(aside),
                    ", ".join(aside))
    return aside


#: The note every exit the host gate sets aside begins with, so the gate
#: can tell its own suspects from the captcha tally's and free them when
#: the host recovers.
HOST_GATE_NOTE = "Login rate"
#: Where a person's Free is written down, by host: {host: unix time}.
#: The gate judges a host from its last clear onwards, and the captcha
#: tally forgets the host's strikes for the day.
HOST_CLEARS = "host_clears"


def host_clears(settings: Settings) -> dict[str, float]:
    """When each host was last cleared by hand, or {} without a store."""
    if not getattr(settings, "store_enabled", False):
        return {}
    try:
        from .store import state as store_state

        got = store_state.get(settings, HOST_CLEARS, {}) or {}
        return {str(k): float(v) for k, v in dict(got).items() if v}
    except Exception as exc:                                      # noqa: BLE001
        log.debug("could not read the host clears (%s)", exc)
        return {}


def forgive_host(settings: Settings, host: str, *, by: str = "") -> None:
    """A person freed an exit on `host` on the console: judge the host
    from now on, and forget the day's captcha strikes against it.

    Free was a status write and nothing more, so the gate - which
    re-derives `suspect` from the week's sign-ins on every pass - set the
    exit straight back aside within a minute, and the captcha tally did
    the same at the next challenge. The operator changed the exit's
    address at the vendor, pressed Free, and read the same row in the
    same list again nine times in an afternoon (2026-09-13). Never fatal.
    """
    host = str(host or "").strip()
    if not host or not getattr(settings, "store_enabled", False):
        return
    try:
        from .store import state as store_state

        now = time.time()
        # Each under its row's lock - see _bump_captcha_host for the race
        # this closes.
        store_state.update(settings, HOST_CLEARS,
                           lambda cleared: {**(cleared or {}), host: now}, {})
        store_state.update(settings, "captcha_hosts",
                           lambda strikes: {k: v
                                            for k, v in (strikes or {}).items()
                                            if k != host}, {})
        log.info("host %s cleared by %s: judged from now on", host,
                 by or "hand")
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not clear host %s (%s); the gate may set its "
                    "exits aside again", host, exc)


def gate_threshold(settings: Settings, farm_rate: float) -> float:
    """The rate under which an exit is suspect: the absolute floor
    (`host_gate_rate`) or `host_gate_relative` of what the farm as a
    whole signs in over the same week, whichever is lower. When Google
    refuses everyone the farm's rate falls and the threshold with it, so
    no exit is blamed for the hour (2026-09-15)."""
    floor = float(getattr(settings, "host_gate_rate", 0.5))
    relative = float(getattr(settings, "host_gate_relative", 0.5))
    return min(floor, relative * max(0.0, float(farm_rate or 0.0)))


def gate_hosts(book: Book, settings: Settings) -> dict[str, list[str]]:
    """Set aside the free exits whose sign-ins over the last week fall
    under `gate_threshold` (with at least `host_gate_min` of them), and
    free the ones it set aside once their host recovers.

    Judged per exit first: Google sees the exit's own address, and a
    vendor host carries seven of them, so one bad address must not take
    six good ones with it (the operator, 2026-09-15: "my proxies are
    limited"). The host as a whole is judged only when it is plainly
    dead - twice the sample, half the threshold - which is what catches
    a host before every one of its exits has burned eight sign-ins.
    Measured on 2026-09-10: 185.100.235.x 20 in 100, 82.38.66.x 30
    against 82.27.118.x 75 among addresses with a key.

    Relative, not absolute: an exit is suspect when it does worse than
    half the farm's own rate over the same week. The absolute floor set
    fourteen exits aside in one afternoon when Google was refusing 95%
    of everything, and none of them was the reason (2026-09-14).

    A host a person cleared is judged from the clear onwards - its
    exits need `host_gate_min` fresh sign-ins before they can be set
    aside again - so that Free means something (2026-09-13). An exit set
    aside on its own numbers gets no new sign-ins, so it is freed when
    its HOST is back over the threshold - the host's other exits keep
    the sample moving - or by a hand."""
    from .store import signins as store_signins

    least = max(1, int(getattr(settings, "host_gate_min", 8)))
    cleared = host_clears(settings) or None
    farm = store_signins.totals(settings)
    threshold = gate_threshold(settings, farm.get("rate", 0.0))
    hosts = {r["key"]: r for r in store_signins.host_rates(
                 settings, since=cleared) if r["n"] >= least}
    exits = {r["key"]: r for r in store_signins.exit_rates(
                 settings, since=cleared) if r["n"] >= least}
    aside, freed = [], []
    for resource in list(book.proxies._rows):
        host = str(getattr(getattr(resource, "proxy", None), "host", "") or "")
        name = str(getattr(resource, "name", "") or resource.label)
        status = str(resource.values.get("Status") or "").strip().lower()
        note = str(resource.values.get("Note") or "")
        own = exits.get(name)
        whole = hosts.get(host)
        if own is not None:
            bad = own["rate"] < threshold
            seen = (f"{own['ok']}/{own['n']} on {name} in the last 7 days, "
                    f"against {farm['ok']}/{farm['n']} on the farm")
        elif whole is not None and whole["n"] >= 2 * least:
            bad = whole["rate"] < threshold / 2
            seen = (f"{whole['ok']}/{whole['n']} on host {host} in the last "
                    f"7 days, against {farm['ok']}/{farm['n']} on the farm")
        else:
            continue
        if bad and status in ("", "free", "unused"):
            book.proxies.fail(resource, SUSPECT, note=(
                f"Login rate {seen}; set aside on its own. Press Free once "
                f"the address is changed at the vendor - the host is then "
                f"judged afresh - or wait for the host to recover."))
            aside.append(name)
        elif (status == SUSPECT and note.startswith(HOST_GATE_NOTE)
              and whole is not None and whole["rate"] >= threshold):
            # Its host is back over the line. Its own numbers cannot say
            # so - a suspect gets no sign-ins - so the host speaks for it.
            book.proxies.release(resource, note=(
                f"Free again - {host} signs in {whole['ok']}/{whole['n']} "
                f"over the last 7 days."))
            freed.append(name)
    if aside:
        log.warning("host gate: %d exit(s) set aside under %.0f%% (the farm "
                    "signs in %.0f%%): %s", len(aside), threshold * 100,
                    farm.get("rate", 0.0) * 100, ", ".join(aside))
    if freed:
        log.info("host gate: %d exit(s) back on recovered hosts: %s",
                 len(freed), ", ".join(freed))
    return {"gated": aside, "ungated": freed}
