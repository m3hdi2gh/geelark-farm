"""Open an app and say what its first screen is.

The plain task, and the first one on purpose: it shares nothing with a
sign-in but the seam. No account, no code, no solver. If the only task
the farm could run were a sign-in, `tasks` would quietly become
"whatever a sign-in needs"; this is the thing that says otherwise.

It answers a question somebody asks by hand today - "is this phone still
signed in?" - and it answers it the way this project answers everything:
by what the device drew, with the page kept either way.

A page neither word list knows is not a failure. It is the thing this
exists to find, and its capture is what a new word is written from.
"""
from __future__ import annotations

import logging

from .. import shell
from ..flows import router
from ..flows.router import Outcome, Screen, act_wait, still_loading

log = logging.getLogger(__name__)

#: What a chat app's two pages usually say. Overridden per run, because
#: the point of this task is to be pointed at anything.
SIGNED_IN = ("new chat", "message", "ask anything", "what can i help",
             "your library", "for you", "home")
SIGNED_OUT = ("log in", "sign in", "continue with google", "create account",
              "get started", "sign up", "welcome to")
#: How long the app gets to draw its first screen.
OPEN_SECONDS = 45.0
#: After the launcher intent, before the first look.
DREW = 3.0


def words(given: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    listed = tuple(w.strip().casefold() for w in (given or "").split(",")
                   if w.strip())
    return listed or fallback


def run(ctx) -> Outcome:
    """`ctx` is a `tasks.drive.Doing`: the client, the phone, the inputs
    already checked, and where the screens go."""
    package = ctx.input("package")
    wanted_in = words(ctx.input("signed_in_words"), SIGNED_IN)
    wanted_out = words(ctx.input("signed_out_words"), SIGNED_OUT)

    # The device's own answer first. An app that is not there cannot be
    # probed, and saying so costs one shell call - much less than a
    # budget spent watching a screen that will never be the app's.
    if not shell.package_installed(ctx.client, ctx.phone_id, package):
        return Outcome("fatal", "app_not_installed",
                       f"{package} is not on the phone")
    shell.force_stop(ctx.client, ctx.phone_id, package)
    shell.run(ctx.client, ctx.phone_id,
              f"monkey -p {package} -c android.intent.category.LAUNCHER 1")

    screens = [
        Screen("loading", still_loading, act_wait, max_visits=15),
        Screen("signed_in", lambda c: c.has(*wanted_in),
               lambda c: Outcome("success", "signed_in",
                                 f"{package} drew a signed-in screen"),
               max_visits=1),
        Screen("signed_out", lambda c: c.has(*wanted_out),
               lambda c: Outcome("success", "signed_out",
                                 f"{package} drew its sign-in screen"),
               max_visits=1),
    ]

    flow = router.Context(client=ctx.client, phone_id=ctx.phone_id,
                          artifact_dir=ctx.artifact_dir, watch=ctx.watch)
    flow.package = package
    outcome = router.drive(flow, screens, is_done=lambda: None,
                           budget_seconds=min(ctx.budget_seconds, OPEN_SECONDS),
                           logger=log, watch=ctx.watch)

    if outcome.reason in ("unknown_screen", "left_the_app"):
        # Not a failure of the phone or the account: a page neither list
        # knows is the answer this task exists to bring back, and it has
        # been kept. Named so it does not read as the device's fault.
        labels = [e.label for e in flow.elements if e.label][:12]
        return Outcome("fatal", "unrecognised_screen",
                       f"{package} drew a page neither list knows: {labels}",
                       artifacts=outcome.artifacts, trail=outcome.trail,
                       dumps=outcome.dumps)
    if outcome.reason == "screen_unreadable":
        return Outcome("fatal", "app_would_not_start", outcome.detail,
                       artifacts=outcome.artifacts, trail=outcome.trail,
                       dumps=outcome.dumps)
    return outcome
