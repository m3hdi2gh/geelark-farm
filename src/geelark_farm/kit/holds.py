"""What a build was holding, and what becomes of each piece of it at the
end: spent, released as stock, or set aside - and the exits whose host
went bad today go back as suspect, not as stock.

Moved out of builder.py unchanged, under their old names (the builder
review, 2026-09-23). Logs under the builder's logger name, as before.
"""
from __future__ import annotations

import logging

from .. import failures
from ..build_result import Build
from ..exit_health import CAPTCHA_STRIKES_PER_HOST, SUSPECT
from ..pools import Book
from ..runctx import _record_event, _run

log = logging.getLogger("geelark_farm.builder")


# What becomes of a resource a build was holding. It was a boolean - spent or
# not - and a challenged app account is neither: it was not used, and putting
# it back blank is what made every run pick the same one again.
SPEND = "spend"


RELEASE = "release"


SET_ASIDE = "set aside"


def _refused_holds(book: Book, refused: list[tuple]) -> list[tuple]:
    """Exits a service refused this phone through, and what each becomes:
    held back rather than freed. The proxy is not condemned - a refusal is
    per-session, which is measured - but its *address* has just been
    turned down, and nothing here can change one: the address is the
    vendor's to rotate, not ours. Freeing it hands the next build the
    same address to be refused through again."""
    today = failures.today()
    return [(book.proxies, resource, SET_ASIDE,
             f"On {today} {failures.verdict(why).seen}. The proxy is fine; "
             f"the exit address is the thing that was turned down. Change it "
             f"in the vendor's panel, then set this cell to `free`.", why)
            for resource, why in refused]


def _release(book: Book, build: Build, held: list[tuple], *,
             suspect_hosts: frozenset | set = frozenset()) -> None:
    """Hand every still-claimed resource its outcome.

    `suspect_hosts` are the exit hosts Google challenged enough today (see
    `_struck_hosts`): an exit going back as stock onto one of them goes
    back as `suspect` instead, with Free on the row as the way back.

    `spent` is what the resource ended up on a device as, not whether the build
    as a whole succeeded. A Gmail that signed in is on that phone whatever
    happens afterwards, so a build that then fails its app login must still
    mark it used - releasing it would hand a signed-in account to the next
    phone, which is the one mistake in this file that costs an account rather
    than a minute. The same goes for a proxy the phone was created behind.

    Runs in a finally, so it must not raise: a sheet error here would replace
    the build's real result with a network complaint, and the resources would
    stay claimed either way.
    """
    for pool, resource, action, note, reason in held:
        if resource is None:
            continue
        try:
            if action == SET_ASIDE:
                pool.set_aside(resource, reason=reason, note=note)
                if pool is book.apps:
                    # An account leaving the pool is an event (C8): it is
                    # what the Gpt Pool's "set aside" list is made of.
                    _record_event("account", "set_aside", run_id=_run.get(),
                                  build=str(build.index),
                                  serial=str(build.serial or ""),
                                  detail=f"{resource.label}: {reason}")
            elif action == SPEND:
                # A rename's note - "Sold as x; signs in as y" - survives
                # the spend: it is the one place the sold address is kept.
                sold = str((resource.values or {}).get("Note") or "")
                sold = f" {sold}" if sold.startswith("Sold as ") else ""
                pool.spend(resource, serial=build.serial, note=(
                    f"On phone {build.serial}.{sold}"
                    if build.ok else
                    f"On phone {build.serial}, which stopped short of ready - "
                    f"see that row in the Phones tab.{sold}"))
            elif (pool is book.proxies and suspect_hosts
                  and str(getattr(getattr(resource, "proxy", None), "host", ""))
                  in suspect_hosts):
                host = resource.proxy.host
                pool.fail(resource, SUSPECT, note=(
                    f"Suspect - Google challenged {CAPTCHA_STRIKES_PER_HOST} "
                    f"or more sign-ins on {host} today ({failures.today()}); "
                    f"set aside on its own as this build let go of it. Press "
                    f"Free to use it again."))
                log.warning("%s goes back as suspect, not stock: %s is a host "
                            "Google kept challenging today", resource.label,
                            host)
            else:
                # Claimed but never put on a device - the Gmail fetched just as
                # the budget ran out, the app account nothing was tried with,
                # the exit that was swapped away from. It is stock, and it goes
                # back as stock.
                pool.release(resource, note=note or (
                    "Free again - a build claimed it but never got as far as "
                    "using it."))
        except Exception as exc:                                  # noqa: BLE001
            # Broad, and the docstring above says why: this runs in a finally,
            # where an exception does not fail the call - it replaces the value
            # the call was about to return. `SheetError` covered the quota and
            # the network, and `batch_write` re-raises every other APIError
            # untouched: a revoked key or a bad range escaped, took the Build
            # with it, and left the resources after this one in the list still
            # claimed with nothing coming back to free them.
            log.error("%s: could not release %s (%s) - it stays in_use until "
                      "'geelark pools --release-stuck'",
                      pool.tab, resource.label, exc)
