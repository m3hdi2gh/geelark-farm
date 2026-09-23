"""The end of a phone's job: whether anything is signed into it after
all, and deleting one that holds nothing worth keeping.

Moved out of builder.py unchanged, under their old names (the builder
review, 2026-09-23). Logs under the builder's logger name, as before.
"""
from __future__ import annotations

import logging

from .. import phones, shell
from ..api import Client
from ..build_result import Build, outcome_of
from ..ledger import Ledger
from ..pools import Book

log = logging.getLogger("geelark_farm.builder")


# What a build needs left to be worth starting another attempt: a stop, a boot
# and a login. Below this the honest thing is to report what it has.
ATTEMPT_SECONDS = 420


def _signed_in_after_all(client: Client, build: Build) -> bool:
    """Ask the device itself, once, before throwing the phone away.

    The flow's verdict is what the run saw while it was watching. Google adds
    the account after its own consent closes, and it takes as long as it takes:
    every phone that survived a `stuck_on_sign_in_closed` failure on 2026-09-04
    was found later holding the account it was supposed to have, with nothing
    in the device's own account history but the add. The ones that did not
    survive were deleted by this path, signed in, on the strength of a verdict
    that was already out of date when it was written.

    Deleting is the one thing here that cannot be undone: a phone kept in error
    is a row somebody closes, and a phone deleted in error is a Gmail, a proxy
    and the minutes spent on both. So this is asked strictly, and anything that
    is not a clear "there is nothing on it" keeps the phone.
    """
    try:
        present = shell.device_accounts(client, build.phone_id, strict=True)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("phone %s could not say whether it is signed in (%s), so "
                    "it is kept rather than deleted",
                    build.serial or build.phone_id, exc)
        return True
    if not present:
        return False
    log.warning("phone %s is signed in as %s after all - the run gave up "
                "before Google finished. Keeping it.",
                build.serial or build.phone_id, ", ".join(present))
    return True


def _discard(client: Client, book: Book, ledger: Ledger,
             build: Build) -> bool:
    """Delete a phone nothing was ever signed into, and free its exit.

    Returns whether it went. A delete that fails leaves the phone to be stopped
    and recorded the ordinary way - half-deleting it, with its row dropped and
    the device still there, is the one outcome worse than keeping it.
    """
    try:
        # Stopped first, and not as a courtesy: GeeLark refuses to delete a
        # running phone, and this runs before the stop that the ordinary path
        # does at the end. Both phones this discarded on 2026-08-17 were still
        # running when it asked, so both refusals came back under failDetails
        # while the tool went on to drop their rows.
        phones.stop(client, build.phone_id)
        phones.wait_until_stopped(client, build.phone_id)
        phones.delete(client, [build.phone_id], ledger=ledger)
    except Exception as exc:                                      # noqa: BLE001
        log.error("phone %s has no Google account on it and could not be "
                  "deleted (%s); it is recorded and left alone",
                  build.serial or build.phone_id, exc)
        return False
    log.info("deleted phone %s - nothing was ever signed into it (%s)",
             build.serial or build.phone_id, outcome_of(build))
    book.record_history(
        Serial=build.serial, Event="discarded",
        Seconds=f"{build.seconds:.0f}", Proxy=build.proxy_name or build.proxy,
        Steps=build.steps,
        Note=(f"Deleted rather than kept - nothing was ever signed into it. "
              f"{outcome_of(build).capitalize()}."))
    resource = book.proxies.find_proxy(build.proxy) if build.proxy else None
    if resource is not None:
        book.proxies.release(resource, note=(
            "Free again - the phone taken on it had nothing signed in and was "
            "deleted."))
    build.phone_id = ""
    return True
