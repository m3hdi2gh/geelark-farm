"""GeeLark's own installer: an app from its app center put on a phone by
one API call, with no Play Store screen to walk.

Play was the only way for a year, and it is the slow half of a build:
ChatGPT and Spotify took seven of a warm phone's eleven minutes, one
stalled download at a time (2026-09-08, phone 1983). The center has
Spotify. It does not have ChatGPT, which still goes in through Play until
an APK of it is uploaded (`/v1/app/upload` takes a URL).

`begin` fires the install and returns at once - GeeLark installs in the
background, so the Google sign-in walks on while Spotify lands - and
`wait_installed` is the proof, the same `pm list packages` the Play flow
asks for. Every refusal is a reason the caller can fall back on: Play is
still there, and a build never fails on this door alone.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from . import shell
from .api import Client

log = logging.getLogger(__name__)

INSTALLABLE = "/v1/app/installable/list"
INSTALL = "/v1/app/install"
#: GeeLark's own codes on `/app/install`: another install is under way on
#: this phone (which is what an order already given looks like), and the
#: version id names nothing (the center dropped it, so the cache is wrong).
ALREADY_INSTALLING = 42003
NO_SUCH_VERSION = 42006
#: The proof poll. `pm list packages` is one shell call; ten seconds is
#: the pace every other wait on a phone keeps, and the rate limit is a
#: process-wide budget.
POLL_SECONDS = 10

#: The center's version id by package, for the process. The ids are the
#: center's, not a phone's - the same Spotify id answers every phone - and
#: the listing is a paged call nobody wants once a build.
_versions: dict[str, str] = {}
_lock = threading.Lock()


def forget(package: str) -> None:
    with _lock:
        _versions.pop(package, None)


def version_id(client: Client, phone_id: str, package: str, *,
               name: str) -> str:
    """The center's id for the newest version of `package`, or "" when the
    center has no such app. `name` is the search word the listing takes;
    the package is what is matched, since a search for "Chat" finds
    Snapchat."""
    with _lock:
        cached = _versions.get(package, "")
    if cached:
        return cached
    data = client.post(INSTALLABLE, {"envId": phone_id, "page": 1,
                                     "pageSize": 50, "name": name},
                       retry=True).get("data") or {}
    for item in data.get("items") or []:
        if item.get("packageName") != package:
            continue
        versions = item.get("appVersionInfoList") or []
        found = str((versions[0] or {}).get("id") or "") if versions else ""
        if found:
            with _lock:
                _versions[package] = found
            return found
    return ""


def begin(client: Client, phone_id: str, package: str, *, name: str) -> bool:
    """Ask GeeLark to install `package` on the phone, and say whether it
    took the order. Never raises: a door that does not open is Play's turn,
    and the phone is already up and billing."""
    try:
        version = version_id(client, phone_id, package, name=name)
        if not version:
            log.info("GeeLark's app center has no %s; Play installs it", name)
            return False
        reply = client.post(INSTALL, {"envId": phone_id,
                                      "appVersionId": version}, strict=False)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("GeeLark's installer could not be asked for %s (%s); "
                    "Play installs it", name, exc)
        return False
    code = reply.get("code")
    if code in (0, ALREADY_INSTALLING):
        log.info("GeeLark is installing %s (%s) in the background", name,
                 version)
        return True
    if code == NO_SUCH_VERSION:
        forget(package)
    log.warning("GeeLark refused to install %s: [%s] %s; Play installs it",
                name, code, reply.get("msg"))
    return False


def wait_installed(client: Client, phone_id: str, package: str, *,
                   budget_seconds: float,
                   cancelled: Callable[[], bool] | None = None) -> bool:
    """Block until `package` is on the phone, or the budget is spent."""
    deadline = time.monotonic() + budget_seconds
    while True:
        if cancelled and cancelled():
            return False
        try:
            if shell.package_installed(client, phone_id, package, strict=False):
                return True
        except Exception as exc:                                   # noqa: BLE001
            log.warning("could not ask whether %s is on yet (%s)", package, exc)
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(POLL_SECONDS, left))
