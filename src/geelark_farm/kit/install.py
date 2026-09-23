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
from .exits import _exit_up, _new_exit

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
                       cancelled, proxy_row, refused_exits: list,
                       hold: list | None = None
                       ) -> tuple[play_install.Outcome, object]:
    """`_install`, and the operator's recipe around it when Play will not
    give the app - see PLAY_RETRY_REASONS. Returns the outcome and the
    exit the phone ends up on.

    `hold` is the caller's way of learning that exit when this raises
    instead of returning: every swap appends the row the phone is now on,
    and the caller takes the last one before it releases anything."""
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
            previous = proxy_row
            seen = {f"{r.proxy.host}:{r.proxy.port}"
                    for r, _ in refused_exits if getattr(r, "proxy", None)}
            if getattr(proxy_row, "proxy", None):
                seen.add(f"{proxy_row.proxy.host}:{proxy_row.proxy.port}")
            try:
                proxy_row = _new_exit(
                    client, settings, book, build, phone_id, proxy_row,
                    f"{name}: the Play Store offered no install "
                    f"({installed.reason}) - exit {swaps + 1} of "
                    f"{PLAY_RECIPE_EXITS}",
                    remaining(), swaps=swaps, avoid=seen)
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
            if previous is not None and previous is not proxy_row:
                refused_exits.append((previous, installed.reason))
            swaps += 1
            # The caller reads this back on a raise - see build_one.
            if hold is not None:
                hold.append(proxy_row)
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
