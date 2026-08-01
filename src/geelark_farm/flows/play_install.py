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


def still_loading(elements: list[screen.Element], xml: str) -> bool:
    """Whether the Play Store has not finished drawing the page yet.

    A rendered package page always has text on it, so no labelled elements at
    all means nothing has arrived - usually with a spinner sitting in the
    middle. Distinguishing this from "a page I do not recognise" matters: the
    first should be waited for, the second is a genuine dead end.

    Measured 2026-08-01: with three rows running at once everything is slower,
    and one row reached this check six seconds after the deep link with a bare
    ProgressBar on screen. Treating that as "no Install button" failed a row
    whose page was about to appear.
    """
    if elements:
        return False
    return "ProgressBar" in xml or not xml.strip()


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

        if still_loading(elements, xml):
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
