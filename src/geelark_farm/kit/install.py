"""An app onto a phone: GeeLark's app centre first, then the Play Store,
and the operator's recipe around Play when it will not give the app -
another exit, a cleared Play Store, the page again.

Moved out of builder.py unchanged, under their old names (the builder
review, 2026-09-23). Logs under the builder's logger name, as before.
"""
from __future__ import annotations

import logging

from .. import apps, phones
from ..api import Client
from ..build_result import Build
from ..cancel import STOPPED_BY_A_PERSON, Aborted
from ..config import Settings
from ..flows import play_install
from ..pools import Book
from .exits import ExitLease, _exit_up

log = logging.getLogger("geelark_farm.builder")


#: How long an install GeeLark was asked for at boot gets to land after the
#: sign-in, before Play is walked for it instead. Spotify is on well inside
#: the sign-in's two minutes when the order was taken; three more is
#: patience, not a budget.
API_INSTALL_WAIT_SECONDS = 180


#: The Play Store outcomes the operator's recipe answers, and how. A page
#: with no Install (`no_install_button`, `play_page_never_loaded`,
#: `app_unavailable`, a server error) is the exit: stop the phone, put it
#: behind another exit, start it, force-stop and clear the Play Store,
#: open the page again - "usually the third exit does it". A download
#: parked pending or on one percentage (`download_stalled`) is Play's own
#: state: force-stop and clear it, and the download goes through; a second
#: stall is treated like a page with no Install (the operator, 2026-09-10).
PLAY_RETRY_REASONS = frozenset({"no_install_button", "play_page_never_loaded",
                                "app_unavailable", "play_server_error",
                                "download_stalled"})


PLAY_RECIPE_EXITS = 3


#: Not worth starting another round with less than this left.
PLAY_RETRY_FLOOR_SECONDS = 150.0


def _install_by_recipe(client: Client, settings: Settings, book: Book,
                       build: Build, phone_id: str, package: str, *,
                       name: str, ordered: bool, remaining, artifacts,
                       cancelled, lease: ExitLease, proxy_row=None
                       ) -> tuple[play_install.Outcome, object]:
    """`_install`, and the operator's recipe around it when Play will not
    give the app - see PLAY_RETRY_REASONS. Returns the outcome and the
    exit the phone ends up on.

    Every swap goes through the build's `lease`, which knows the exit the
    build owns before the wait that can raise - the `hold` list the caller
    used to read back on a raise is gone with it (2026-09-23). `proxy_row`
    is the exit the phone is on now, when that is not the one it owns."""
    if proxy_row is None:
        proxy_row = lease.current
    installed = _install(client, phone_id, package, name=name,
                         ordered=ordered,
                         budget=min(settings.install_budget_seconds,
                                    remaining()),
                         artifacts=artifacts, cancelled=cancelled)
    cleared = False
    swaps = 0
    while (not installed.ok and installed.reason in PLAY_RETRY_REASONS
           and remaining() > PLAY_RETRY_FLOOR_SECONDS):
        if cancelled is not None and cancelled():
            break
        if installed.reason == "download_stalled" and not cleared:
            log.warning("%s: the download is parked (%s); clearing the Play "
                        "Store and asking again", name, installed.reason)
            play_install._reset_play(client, phone_id)
            cleared = True
        else:
            if swaps >= PLAY_RECIPE_EXITS or proxy_row is None:
                break
            try:
                proxy_row = lease.swap(
                    client, settings, book, build, phone_id,
                    f"{name}: the Play Store offered no install "
                    f"({installed.reason}) - exit {swaps + 1} of "
                    f"{PLAY_RECIPE_EXITS}",
                    installed.reason, remaining())
            except Aborted as exc:
                # A person's stop is not a verdict on the exit pool, and
                # this handler read it as one: logged it, booted the
                # phone the person had just asked to stop, and carried
                # on (2026-09-21, found by audit).
                if str(exc) in STOPPED_BY_A_PERSON:
                    raise
                log.warning("no other exit to try the Play Store from (%s)",
                            exc)
                phones.ensure_running(
                    client, phone_id,
                    timeout=min(phones.BOOT_SECONDS, remaining()),
                    cancelled=cancelled)
                break
            swaps += 1
            _exit_up(client, settings, phone_id, proxy_row, remaining(),
                     cancelled)
            cleared = False
            play_install._reset_play(client, phone_id)
        installed = _install(client, phone_id, package, name=name,
                             ordered=False,
                             budget=min(settings.install_budget_seconds,
                                        remaining()),
                             artifacts=artifacts, cancelled=cancelled)
    return installed, proxy_row


def _install(client: Client, phone_id: str, package: str, *, name: str,
             ordered: bool, budget: float, artifacts,
             cancelled=None, play: bool = True) -> play_install.Outcome:
    """The app onto the phone: by the order GeeLark's installer already
    took at boot when there was one, and by the Play Store otherwise -
    or as well, if the order never landed. The Play outcome is the type
    either way, since the builder reads `.trail` and `.reason` off it.

    `play` is false where there is no Play Store to walk: the Store asks
    to be signed in, and a bare phone has no Google account at all, so
    the walk could only end in screens nobody can answer (2026-09-12).
    """
    if ordered:
        wait = min(API_INSTALL_WAIT_SECONDS, budget)
        if apps.wait_installed(client, phone_id, package, budget_seconds=wait,
                               cancelled=cancelled):
            log.info("%s is on, from GeeLark's installer", name)
            return play_install.Outcome("success", "installed",
                                        f"{name} installed by GeeLark")
        log.warning("%s has not landed from GeeLark's installer in %.0fs",
                    name, wait)
        budget = max(0.0, budget - wait)
    if not play:
        return play_install.Outcome(
            "fatal", "install_failed",
            f"{name} did not come from GeeLark's installer, and there is no "
            f"Google account on this phone to walk the Play Store with")
    log.info("the Play Store is walked for %s", name)
    got = play_install.install(client, phone_id, package,
                               budget_seconds=budget, artifact_dir=artifacts,
                               cancelled=cancelled)
    # The service's own stop, answered by the walk as a word. Raised here
    # so it is filed as the stop it is: returned, the recipe would have
    # read it as an install that failed (2026-09-21).
    if got.reason == "interrupted":
        raise Aborted("interrupted")
    return got
