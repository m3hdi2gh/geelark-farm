"""The process-wide switches on how a phone is driven, set from Settings.

Three module globals the flows read - `shell.HUMAN_CADENCE`,
`shell.KERNEL_TOUCH`, `flows.google_login.SIGN_IN_VIA` - were set in two
copies inside `serve.run`, one per role, and nowhere else: a sign-in run
by hand from the CLI typed and tapped differently from the farm's
(the builder review, 2026-09-23). One place now, called by every process
that drives a phone.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def apply(settings, *, announce: bool = False) -> None:
    """Set the three from `settings`. `announce` logs the ones that are on,
    as the keeper always has at start."""
    from . import shell
    from .flows import google_login

    shell.HUMAN_CADENCE = bool(settings.human_cadence)
    shell.KERNEL_TOUCH = bool(getattr(settings, "kernel_touch", True))
    google_login.SIGN_IN_VIA = settings.sign_in_via
    if not announce:
        return
    # The hand's cadence for every login this process runs - see
    # shell.HUMAN_CADENCE for why (the operator, 2026-09-09).
    if shell.HUMAN_CADENCE:
        log.info("typing and tapping with a hand's cadence (HUMAN_CADENCE)")
    if shell.KERNEL_TOUCH:
        log.info("taps go into the phone's touch device in the viewer's "
                 "shape (KERNEL_TOUCH)")
    if google_login.SIGN_IN_VIA == "play":
        log.info("the Google sign-in starts from Google Play (SIGN_IN_VIA)")
