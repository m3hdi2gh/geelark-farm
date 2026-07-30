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
      -> Play Pass promo        -> Not now
      -> the download finally starts

  The chain is account-level, so once it has been cleared for an account,
  later phones on that same account install in about thirty seconds.
- success is `pm list packages <pkg>` returning the package. Nothing else
  counts, and nothing on screen is evidence.
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
INTERSTITIAL_LABELS = (
    "Continue", "Skip", "Not now", "No thanks", "Got it", "Dismiss",
)

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

    xml = screen.capture(client, phone_id)
    elements = screen.parse(xml) if xml else []
    archive("play-package-page", xml or "")
    blob = screen.texts(elements)

    reason = _fatal_reason(blob)
    if reason:
        return Outcome("fatal", reason,
                       "the Play Store cannot install for this account yet",
                       artifacts=saved)

    # An interstitial can also be sitting on top before Install is reachable.
    tapped = screen.tap_first_present(client, phone_id, elements,
                                      ("Complete account setup",) + INTERSTITIAL_LABELS)
    if tapped:
        log.info("cleared %r before looking for Install", tapped)
        time.sleep(4)
        elements = screen.read_screen(client, phone_id)

    if not screen.tap_label(client, phone_id, elements, "Install"):
        labels = [e.label for e in elements if e.label][:12]
        return Outcome("fatal", "no_install_button",
                       f"on screen: {labels}", artifacts=saved)
    log.info("tapped Install; waiting for the package to appear")

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
