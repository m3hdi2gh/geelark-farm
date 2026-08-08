"""Install a package from the Play Store and prove it landed.

Not GeeLark's googleAppDownload task, for three reasons found in its own
`/task/detail` logs: it matches the install button only by content-description
(Play renders the label as `text`), its OCR fallback ships with placeholder
credentials, and it takes the app name as a **text search string** - so it can
install a clone. "ChatGPT" has many impostors. It also reported success while
having installed nothing.

This drives the UI instead, addressing the app by package id.

## The sequence

- deep link straight to the package page:
  `am start -a android.intent.action.VIEW -d "market://details?id=<pkg>"`
  No search, so no clone can be selected.
- tap Install, matching text OR content-desc, and without requiring
  `clickable=true`: on this page the label is a non-clickable TextView whose
  centre is nonetheless the right place to tap.
- keep clearing interstitials WHILE polling. On a fresh account the chain
  appears *after* the Install tap, not before:

      "Complete account setup" -> Continue
      -> "Add a payment option" -> Skip
      -> Play Protect scanning  -> Dismiss
      -> the download finally starts

  The chain is account-level, so once it has been cleared for an account,
  later phones on that same account install in about thirty seconds.
- success is `pm list packages <pkg>` returning the package. Nothing else
  counts, and nothing on screen is evidence.

## Two things a brand-new account taught this flow (2026-07-31)

**The Terms of Service dialog can need more than one tap.** Two runs tapped
Accept, waited five seconds, re-read the screen and found the identical
hierarchy still there - same dialog, same coordinates - and only the second tap
took. That is why clearing pre-install dialogs is a loop that re-reads each
pass, not a single attempt: with one attempt this flow would have reported
`no_install_button` on a screen whose Accept button it had just pressed.

**The setup chain is normal for a fresh account, not exotic.** The first
accounts tested skipped it entirely because they had been used elsewhere
already; accounts that have genuinely never opened the Play Store walk the whole
chain. Its absence means the account has history, not that a step was missed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import screen, shell
from ..api import Client

log = logging.getLogger(__name__)

PLAY_PACKAGE = "com.android.vending"

# Interstitials, in the order Play tends to present them. Tapping the wrong one
# first is harmless - the loop simply sees the next screen on its next pass.
#
# "Accept" is Play's Terms of Service, which only a brand-new account meets: the
# first three accounts tested had already accepted it elsewhere, so its absence
# from this list went unnoticed until an account that had never opened the Play
# Store hit it (2026-07-31). Note that the same dialog offers "Decline", which
# must never be tapped - so the list is an allowlist, never a "press any button".
INTERSTITIAL_LABELS = (
    "Accept", "I agree", "Agree",
    "Continue", "Skip", "Not now", "No thanks", "Got it", "Dismiss", "OK",
    # Last, so a real dialog is always preferred: Play's transient server
    # error can also land after the Install tap, and this is the way out of it.
    "Try again",
)

# How many dialogs may stand between the deep link and the Install button before
# something is clearly wrong.
MAX_PRE_INSTALL_DIALOGS = 4

# How long to keep waiting for the package page to render and its dialogs to be
# cleared. Generous, because it is bounded by finding the button rather than by
# elapsed time in the normal case.
PRE_INSTALL_SECONDS = 120

# Text that means the Play Store is not usable for this account yet, rather
# than a page to click through.
FATAL_TEXTS = {
    "play_not_signed_in": ("sign in to find the latest",),
    "play_needs_payment": ("add a payment method to continue",),
    "app_unavailable": (
        "this app isn't available", "not available in your country",
        "item not found",
    ),
}

# How long to keep clearing interstitials and polling before giving up.
POLL_SECONDS = 10


@dataclass
class Outcome:
    kind: str                 # success | fatal | budget
    reason: str
    detail: str = ""
    artifacts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind == "success"

    def __str__(self) -> str:
        text = f"{self.kind}:{self.reason}"
        return f"{text} - {self.detail}" if self.detail else text


def open_package_page(client: Client, phone_id: str, package: str) -> None:
    """Jump straight to one package's page, from a freshly launched Play Store.

    force-stop first: after a login the Play Store is often sitting on some
    other screen, and a deep link into a warm process does not always land.
    """
    shell.force_stop(client, phone_id, PLAY_PACKAGE)
    time.sleep(2)
    shell.launch_url(client, phone_id, f"market://details?id={package}")
    time.sleep(6)


def _fatal_reason(blob: str) -> str | None:
    for reason, needles in FATAL_TEXTS.items():
        if any(n in blob for n in needles):
            return reason
    return None


# Play parks a download it cannot start and says so, then waits indefinitely.
# The page keeps its Cancel button, so the parked state is recoverable - it
# just never recovers on its own within a budget.
STALLED_TEXTS = (
    "waiting for connection", "download will begin once restored",
    "waiting for wi-fi", "download pending",
)
MAX_DOWNLOAD_RESTARTS = 3


def _download_stalled(blob: str) -> bool:
    return any(n in blob for n in STALLED_TEXTS)


# Play's own transient failure. It replaces the whole package page with an
# error and a Try again button, so there is no Install to find and nothing in
# the interstitial list to press - one row reported no_install_button for two
# minutes of it (2026-08-08).
SERVER_ERROR_TEXTS = ("server error", "something went wrong", "no connection")
MAX_PLAY_RETRIES = 3


def _server_error(blob: str) -> bool:
    return any(n in blob for n in SERVER_ERROR_TEXTS)


def _restart_download(client: Client, phone_id: str, package: str,
                      elements: list[screen.Element]) -> None:
    """Cancel a parked download and ask for it again.

    Best effort by design: if any step of it does not land, the polling loop
    simply carries on and may try again, which is better than giving up on a
    download that is one tap from starting.
    """
    if screen.tap_label(client, phone_id, elements, "Cancel"):
        time.sleep(5)
    open_package_page(client, phone_id, package)
    fresh = screen.read_screen(client, phone_id)
    button = screen.find(fresh, "Install")
    if button and screen.tap_element(client, phone_id, button):
        log.info("asked for the download again")
    else:
        log.warning("could not find Install after cancelling; will keep polling")


def still_loading(elements: list[screen.Element]) -> bool:
    """Whether the Play Store has not finished drawing the page yet.

    A rendered package page always has text on it, so no usable elements at all
    means nothing has arrived. Distinguishing this from "a page I do not
    recognise" matters: the first should be waited for, the second is a genuine
    dead end.

    Measured 2026-08-01: with three rows running at once everything is slower,
    and one row reached this check six seconds after the deep link with a bare
    ProgressBar on screen. Treating that as "no Install button" failed a row
    whose page was about to appear.

    It used to require a ProgressBar in the raw XML as well, which made it a
    question about whether Google had drawn a spinner rather than about whether
    the page was there. On 2026-08-08 a row got a hierarchy of twelve layout
    nodes, no text and no spinner, and was reported as no_install_button - true,
    and about a page that had not arrived.
    """
    return not elements


def install(client: Client, phone_id: str, package: str, *,
            budget_seconds: float = 600,
            artifact_dir: Path | None = None) -> Outcome:
    """Install `package`, and return a named outcome.

    Returns rather than raises, so a batch can record why a row failed and move
    on to the next one.
    """
    saved: list[str] = []

    def archive(name: str, xml: str) -> None:
        if artifact_dir and xml:
            artifact_dir.mkdir(parents=True, exist_ok=True)
            path = artifact_dir / f"{time.strftime('%H%M%S')}-{name}.xml"
            screen.save_fixture(xml, path)
            saved.append(str(path))

    if shell.package_installed(client, phone_id, package):
        return Outcome("success", "already_installed", package)

    open_package_page(client, phone_id, package)

    # Clear whatever stands between the deep link and the Install button. A
    # brand-new account meets Play's Terms of Service here; an established one
    # meets nothing. So this loops on what is actually on screen rather than
    # assuming a fixed number of dialogs.
    elements: list[screen.Element] = []
    deadline = time.time() + PRE_INSTALL_SECONDS
    dialogs = 0
    restarts = 0
    retries = 0
    first = True
    while time.time() < deadline:
        xml = screen.capture(client, phone_id) or ""
        elements = screen.parse(xml)
        if first:
            archive("play-package-page", xml)
            first = False

        reason = _fatal_reason(screen.texts(elements))
        if reason:
            archive(reason, xml)
            return Outcome("fatal", reason,
                           "the Play Store cannot install for this account yet",
                           artifacts=saved)

        if screen.find(elements, "Install"):
            break

        if _download_stalled(screen.texts(elements)):
            # A download is already parked from an earlier attempt, so the page
            # shows Cancel and Open where Install would be. Reporting
            # no_install_button here is true and useless: the button is absent
            # because the work is half done, not because the page is wrong
            # (2026-08-07, row 5, on its own retry).
            if restarts >= MAX_DOWNLOAD_RESTARTS:
                archive("download-stalled-final", xml)
                return Outcome("fatal", "download_stalled",
                               "Play parked the download and would not restart "
                               "it", artifacts=saved)
            restarts += 1
            archive(f"download-stalled-{restarts}", xml)
            log.warning("a parked download is holding the page (%d/%d); "
                        "cancelling and starting again", restarts,
                        MAX_DOWNLOAD_RESTARTS)
            _restart_download(client, phone_id, package, elements)
            continue

        if _server_error(screen.texts(elements)):
            if retries >= MAX_PLAY_RETRIES:
                archive("play-server-error-final", xml)
                return Outcome("fatal", "play_server_error",
                               f"Play answered with an error {retries} times",
                               artifacts=saved)
            retries += 1
            archive(f"play-server-error-{retries}", xml)
            log.warning("Play returned an error (%d/%d); pressing Try again",
                        retries, MAX_PLAY_RETRIES)
            # By name, not by "the clickable button": this page also offers a
            # mini-game to pass the time, whose button is called Play.
            if not screen.tap_label(client, phone_id, elements, "Try again"):
                open_package_page(client, phone_id, package)
            time.sleep(6)
            continue

        if still_loading(elements):
            log.info("the package page is still loading")
            time.sleep(4)
            continue

        if dialogs >= MAX_PRE_INSTALL_DIALOGS:
            break
        tapped = screen.tap_first_present(
            client, phone_id, elements,
            ("Complete account setup",) + INTERSTITIAL_LABELS)
        if not tapped:
            break            # real content, but nothing this flow recognises
        dialogs += 1
        log.info("cleared %r before looking for Install", tapped)
        archive(f"pre-install-{tapped.replace(' ', '-')}", xml)
        time.sleep(5)

    button = screen.find(elements, "Install")
    if not button:
        if still_loading(elements):
            # The page never painted at all. Saying "no Install button" of a
            # blank screen sends whoever reads it looking for a button that was
            # never missing - row 1 spent its pre-install budget waiting and
            # was then reported as though Play had refused it (2026-08-07).
            archive("play-page-never-loaded", xml)
            return Outcome("fatal", "play_page_never_loaded",
                           f"the package page was still blank after "
                           f"{PRE_INSTALL_SECONDS}s", artifacts=saved)
        labels = [e.label for e in elements if e.label][:12]
        return Outcome("fatal", "no_install_button",
                       f"on screen: {labels}", artifacts=saved)
    if not screen.tap_element(client, phone_id, button):
        return Outcome("fatal", "no_install_button",
                       "the Install element has no usable bounds",
                       artifacts=saved)
    log.info("tapped Install (%r); waiting for the package to appear",
             button.label)

    deadline = time.time() + budget_seconds
    seen: set[str] = set()
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        if shell.package_installed(client, phone_id, package):
            return Outcome("success", "installed", package, artifacts=saved)

        xml = screen.capture(client, phone_id)
        elements = screen.parse(xml) if xml else []
        blob = screen.texts(elements)

        reason = _fatal_reason(blob)
        if reason:
            archive(reason, xml or "")
            return Outcome("fatal", reason,
                           "the Play Store stopped being able to install",
                           artifacts=saved)

        if _download_stalled(blob) and restarts < MAX_DOWNLOAD_RESTARTS:
            # Play has parked the download rather than failed it, and left it
            # parked: row 5 sat on "Waiting for connection..." for its whole
            # budget and installed nothing (2026-08-07). Cancelling and asking
            # again is what shifts it, and Play offers Cancel on that same page.
            restarts += 1
            archive(f"download-stalled-{restarts}", xml or "")
            log.warning("the download is stalled (%d/%d); cancelling and "
                        "starting it again", restarts, MAX_DOWNLOAD_RESTARTS)
            _restart_download(client, phone_id, package, elements)
            continue

        tapped = screen.tap_first_present(client, phone_id, elements,
                                          INTERSTITIAL_LABELS)
        if tapped:
            if tapped not in seen:
                archive(f"interstitial-{tapped.replace(' ', '-')}", xml or "")
                seen.add(tapped)
            log.info("interstitial: tapped %r", tapped)
        else:
            log.info("still installing...")

    archive("install-budget-exhausted", xml or "")
    return Outcome("budget", "budget_exhausted",
                   f"{package} did not appear within {budget_seconds:.0f}s",
                   artifacts=saved)
