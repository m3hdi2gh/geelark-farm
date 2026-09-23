"""An exit for a phone: a fresh one from the pool, one borrowed when
nothing is free, a swap to another when Google or Play refused this one,
bringing the phone back up behind it, and its clock set to the exit's
zone.

Moved out of builder.py unchanged, under their old names (the builder
review, 2026-09-23). Logs under the builder's logger name, as before.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable

from .. import phones, shell
from .. import proxy as proxy_mod
from ..api import ApiError, Client
from ..build_result import Build
from ..cancel import Aborted
from ..config import Settings
from ..pools import Book, Resource

log = logging.getLogger("geelark_farm.builder")


def _any_exit_free(book: Book) -> bool:
    """Whether the exit pool could hand anything out at all right now.

    Asked of the store when it can answer, because the Book's own rows
    are a snapshot taken when the pass began and the question decides
    which of two words a person reads in the log.
    """
    counted = getattr(book.proxies, "free_now", None)
    if counted is not None:
        try:
            return counted() > 0
        except Exception as exc:                                  # noqa: BLE001
            log.debug("could not count the free exits (%s)", exc)
    return bool(book.proxies.available)


def _fresh_proxy(client: Client, book: Book, *,
                 settings: Settings | None = None,
                 avoid_host: str = "") -> Resource:
    """Claim a proxy GeeLark can actually reach.

    Checked before it is used, because an unreachable proxy is the one failure
    that is genuinely the proxy's: GeeLark either carried the request or it did
    not. Those are marked `dead` and the next one is tried.

    The proxy being replaced is released only after this returns, so `claim()`
    cannot hand back the very proxy that was just judged.

    There is no cap on how many dead ones it will skip. Each is marked `dead`
    before the next is claimed, so the pool strictly shrinks and this cannot
    spin - and a cap costs working phones: when a whole purchase batch died,
    one build hit five dead proxies in a row and gave up while four live ones
    sat in the tab (2026-08-11).

    The two ways to run out are told apart, because they need different things
    doing. `no_usable_proxy` means the tab has nothing left to hand out.
    `no_working_proxy` means it had rows and every one of them was unreachable,
    which is a fact about the stock rather than about this run.
    """
    skipped = 0
    wanted_elsewhere = avoid_host
    while True:
        resource = book.proxies.claim(avoid_host=wanted_elsewhere)
        if resource is None and wanted_elsewhere:
            # Nothing came back, and two different things look like this:
            # every free exit is on the host this address was refused on,
            # or there are no free exits at all. Saying the first when it
            # is the second sends the reader looking at hosts over a pool
            # that is simply empty (2026-09-13).
            if not _any_exit_free(book):
                raise Aborted("no_working_proxy" if skipped
                              else "no_usable_proxy")
            # Every one of them is where this address has already been
            # refused. The caller looks past it to the next address.
            raise Aborted("no_other_exit")
        if resource is None:
            raise Aborted("no_working_proxy" if skipped else "no_usable_proxy")
        try:
            result = proxy_mod.check(client, resource.proxy)
        except (proxy_mod.ProxyError, ApiError) as exc:
            # The name, not the label: the label carries the whole address,
            # and so does the error after it, so the line printed one
            # credential-bearing URL twice and wrapped over two rows to do it.
            # What the name is for is finding the exit in the vendor's panel;
            # what failed and why is the error's job.
            log.warning("proxy %s is dead: %s", resource.name or resource.label,
                        exc)
            book.proxies.fail(resource, "dead", note=(
                f"GeeLark could not reach it when a phone was put behind it: "
                f"{exc}"))
            skipped += 1
            continue
        book.proxies.record_exit(resource, str(result.get("outboundIP") or ""))
        # The check already said where the exit is; keeping it here is what
        # lets the clock be set without a second call to anybody.
        try:
            from .. import geo

            geo.remember_check(settings, result)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("the exit's place was not kept (%s)", exc)
        return resource


def _exit_ip(proxy_row) -> str:
    """The address Google sees through this exit: the last outbound IP
    the check recorded, else the proxy's own host."""
    values = getattr(proxy_row, "values", None) or {}
    ip = str(values.get("Last Exit IP") or "").strip()
    if ip:
        return ip
    return str(getattr(getattr(proxy_row, "proxy", None), "host", "") or "")


def _align_clock(client: Client, settings: Settings, phone_id: str,
                 proxy_row) -> str:
    """Set the phone's timezone to its exit's (GEO_ALIGN). Returns the
    zone set, or "". Never fatal: a phone that keeps its clock is what
    every phone had until today."""
    if not getattr(settings, "geo_align", True) or proxy_row is None:
        return ""
    from .. import geo

    ip = _exit_ip(proxy_row)
    # What the build's own proxy check already said, then GeeLark asked
    # again, and only then the address lookup - which on this server
    # resolves nothing at all, and for a whole day left every clock unset
    # (2026-09-12). `also` files GeeLark's answer under the exit the row
    # carried, so the sign-in record finds a country there too.
    place = dict(geo.known(settings, ip) or {})
    exit_proxy = getattr(proxy_row, "proxy", None)
    if not place.get("tz") and exit_proxy is not None:
        place = dict(geo.by_proxy(client, exit_proxy, settings, also=ip) or {})
    zone = str(place.get("tz") or "")
    ip = str(place.get("ip") or "") or ip
    if not zone and ip:
        zone = geo.timezone_for(settings, ip)
    if not zone or not re.fullmatch(r"[A-Za-z_]+(?:/[A-Za-z_+\-0-9]+){1,2}",
                                    zone):
        return ""
    try:
        shell.run(client, phone_id,
                  f"settings put global auto_time_zone 0; "
                  f"setprop persist.sys.timezone {zone}; "
                  f"date +%Z")
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not set the clock of %s to %s (%s)", phone_id,
                    zone, exc)
        return ""
    log.info("clock set to %s, where exit %s is", zone, ip)
    return zone


def _borrow_exit(book: Book, avoid: set[str]) -> Resource | None:
    """An exit already behind another phone, when nothing is free.

    This breaks the rule the rest of the module keeps: one phone per exit. It
    is deliberate and it is a last resort, reached only once the pool has
    nothing free at all, because the alternative is what happened to phone 762
    - everything done right, one ordinary refusal, and no second exit to answer
    it with.

    What it costs is worth stating plainly. Two phones behind one address means
    Google and OpenAI can see the two accounts arriving from the same place, so
    a run that shares exits is linking the accounts it builds. That is the
    operator's trade to make and they have made it; the sharing is written into
    both the phone's note and the proxy's, so it is never a surprise later.

    `avoid` is every exit this build has already been through. Without it the
    loop has no bound: a phone refused twice would take back the exit that
    refused it first and go round for as long as its budget lasted, which is
    exactly what holding refused proxies claimed was written to stop
    (2026-08-11, phone 658, forty-nine minutes).
    """
    for resource in book.proxies._rows:
        if resource.error or not resource.proxy:
            continue
        if book.proxies.status_of(resource) != book.proxies.spent_status:
            continue                      # free, dead or claimed - not shared
        if f"{resource.proxy.host}:{resource.proxy.port}" in avoid:
            continue
        return resource
    return None


def _new_exit(client: Client, settings: Settings, book: Book, build: Build,
              phone_id: str, current: Resource | None, why: str, budget: float,
              swaps: int = 0, avoid: set[str] | None = None) -> Resource:
    """Get the phone onto a different exit address: another proxy.

    Claimed and set, and handed back - the phone is left stopped, and
    `_exit_up` brings it up once the caller holds the row. The boot used
    to be this function's last act, so a raise out of its wait - a Cancel
    pressed on the row, a phone that would not start - meant the caller
    never got the row: its `proxy_row` still named the exit before, the
    phone stood on the new one, and the end-of-build release spent the
    wrong row while the one the phone was on stayed `in_use` for good
    (2026-09-21, found by audit). Nothing below can now raise once the
    exit is ours and on the phone.

    There used to be a cheaper branch first - sx.org can hand a proxy a new
    address while keeping its host, port and credentials, so nothing on the
    phone changes. It is gone: only the vendor's `port` product can do that,
    this account holds none, and buying them is not the plan (2026-08-25).

    The phone is stopped before anything: GeeLark's documentation says not to
    call the update while a phone is starting, and Android reads the proxy
    when the network comes up - a phone left running would keep the exit just
    judged.
    """
    log.warning("%s - getting a different exit address", why)
    phones.stop(client, phone_id)

    # Whether the exit below is one this build took or one it is standing on
    # beside another phone. They are settled in opposite ways and were told
    # apart nowhere: see the refusal handler.
    borrowed = False
    try:
        replacement = _fresh_proxy(client, book, settings=settings)
    except Aborted as exc:
        if str(exc) != "no_usable_proxy":
            # The stock was unreachable, not refusing. Reported as it is:
            # "every exit refused" would send the reader looking at OpenAI when
            # the answer is that their proxies are down (2026-08-11, phone 671,
            # which met three dead proxies from an expired batch and was
            # recorded as though the service had judged it).
            raise
        # An empty pool means two different things, and saying the wrong one
        # sends the reader to the wrong place. A build that has already worked
        # through several exits emptied the pool itself - it holds each refused
        # one claimed - and that is a fact about the pool or the service. A
        # build on its first swap emptied nothing: there was simply no free
        # proxy to move to, which is what happens when a run is given as many
        # phones as it has proxies. Phone 762 was told "every exit in the pool
        # was refused in turn" after being refused exactly once (2026-08-16).
        # Nothing free. Rather than stop here, take one that another phone is
        # already on - see _borrow_exit for what that costs.
        replacement = _borrow_exit(book, avoid or set())
        if replacement is None:
            raise Aborted("all_exits_refused" if swaps
                          else "no_exit_to_move_to") from None
        borrowed = True
        build.shared_exit = True
        log.warning("no free proxy left; sharing %s, which %s is already on",
                    replacement.label,
                    (replacement.values.get("Used By") or "another phone"))
    try:
        phones.set_proxy(client, phone_id, replacement.proxy)
    except ApiError as exc:
        # The phone keeps the proxy it had, so nothing is broken - but this
        # build cannot do what it came here to do, and saying "the login
        # failed" would hide that.
        #
        # Only if it was ours. A borrowed exit is one another phone is running
        # on right now - `_borrow_exit` returns it without claiming it - and
        # releasing that blanks its status and wipes the `Used By` naming its
        # real owner. The next build then claims it, putting a third phone on
        # the address and leaving nothing that says whose it was. The path
        # into this is not exotic: the pool empties, a proxy is borrowed, and
        # GeeLark refuses to move onto it - which is what [45004] is, and a
        # borrowed exit is exactly the kind that draws one.
        if not borrowed:
            book.proxies.release(replacement, note=(
                f"Free again - GeeLark would not move a phone onto it: {exc}"))
        raise Aborted("proxy_change_refused") from exc
    # `current` is deliberately NOT released here. Releasing it put it straight
    # back on the shelf as `unused`, where the very next swap could claim it
    # again - so a phone that kept being refused went round the pool instead of
    # through it, for as long as its budget lasted: phone 658 spent 49 minutes
    # alternating request_rejected and network_ssl_rejected across the same
    # proxies (2026-08-11). Holding it claimed for the rest of the build is
    # what makes the loop terminate: each swap costs one proxy, so the pool is
    # the bound. The caller collects these and releases them all at the end.
    build.proxy = str(replacement.proxy)
    build.proxy_name = replacement.name
    return replacement


def _exit_up(client: Client, settings: Settings, phone_id: str, exit_row,
             budget: float, cancelled: Callable[[], bool] | None) -> None:
    """Bring the phone back up on the exit `_new_exit` just put it on.

    Called after the caller has written the row down - into `proxy_row`,
    and the one before it into `refused_exits` - so that whatever this
    wait raises, the release at the end of the build sees the exit the
    phone is actually standing on. See `_new_exit` for what happened
    when the two were one function.
    """
    time.sleep(5)
    phones.ensure_running(client, phone_id,
                          timeout=min(phones.BOOT_SECONDS, budget),
                          cancelled=cancelled)
    _align_clock(client, settings, phone_id, exit_row)
