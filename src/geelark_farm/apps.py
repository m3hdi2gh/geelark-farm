"""GeeLark's own installer: an app from its app center put on a phone by
one API call, with no Play Store screen to walk.

Play was the only way for a year, and it is the slow half of a build:
ChatGPT and Spotify took seven of a warm phone's eleven minutes, one
stalled download at a time (2026-09-08, phone 1983). The center carries
all three of ours now - Spotify is GeeLark's own, and ChatGPT and Claude
are copies pulled off a Play-signed phone and uploaded through
`/v1/app/upload` (2026-09-12).

Those two are findable nowhere. `installable/list` is GeeLark's own
catalogue of three hundred and twenty-five apps and an upload never joins
it; `/v1/app/list` answers for one phone, so a phone with nothing on it
cannot be asked either. The id comes back once, from the upload, and is
written down in the store - see `uploaded` and `remember`.

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

#: Where the ids of the apps we uploaded ourselves are written down.
#: There is no call that finds them again, so the upload records them and
#: every builder reads them from here (2026-09-12).
STATE_KEY = "app_versions"
#: How long that row is believed. Uploading a fresher copy of an app
#: replaces it, and a builder that has been up for days should carry the
#: new one without a restart; one small read every five minutes is
#: nothing against a build.
STATE_SECONDS = 300

#: The center's version id by package, for the process. The ids are the
#: center's, not a phone's - the same Spotify id answers every phone - and
#: the listing is a paged call nobody wants once a build.
_versions: dict[str, str] = {}
#: What the state row last said, and when it was read.
_ours: dict[str, str] = {}
_ours_at = 0.0
_lock = threading.Lock()


def forget(package: str) -> None:
    global _ours_at
    with _lock:
        _versions.pop(package, None)
        _ours.pop(package, None)
        _ours_at = 0.0


def _entry(value) -> dict:
    """One package's record, whichever shape it was written in.

    The row began as {package: id} and grew the version name that id
    carries, because "is the copy in the center still the one Play
    serves?" cannot be answered by an id alone - and Play's own installs
    are not in `/v1/app/list` to compare against, so the answer has to
    have been written down (2026-09-12). Both shapes read.
    """
    if isinstance(value, dict):
        return {"id": str(value.get("id") or ""),
                "version": str(value.get("version") or ""),
                "at": str(value.get("at") or "")}
    return {"id": str(value or ""), "version": "", "at": ""}


def recorded(settings) -> dict[str, dict]:
    """Every app we uploaded, by package: the center's id, the version
    name that id carries, and the day it was written down. Raises if the
    store cannot be read - this is the recorder's own view, not a
    build's."""
    from .store import state as store_state

    row = store_state.get(settings, STATE_KEY, {}) or {}
    out = {}
    for package, value in dict(row).items():
        entry = _entry(value)
        if entry["id"]:
            out[str(package)] = entry
    return out


def uploaded(settings, package: str) -> str:
    """The center's id for the copy of `package` we put there ourselves,
    or "" when we put none there - which is every app GeeLark's own
    catalogue carries, Spotify among them."""
    global _ours_at
    if settings is None or not getattr(settings, "store_enabled", False):
        return ""
    with _lock:
        known = dict(_ours)
        fresh = _ours_at > 0 and time.monotonic() - _ours_at < STATE_SECONDS
    if fresh:
        return known.get(package, "")
    try:
        from .store import state as store_state

        row = store_state.get(settings, STATE_KEY, {}) or {}
        known = {str(k): _entry(v)["id"] for k, v in dict(row).items()}
        known = {k: v for k, v in known.items() if v}
    except Exception as exc:                                       # noqa: BLE001
        # The last read stands rather than the app falling back to Play
        # on one unlucky query, and the clock is left where it was so the
        # next caller tries again.
        log.warning("could not read the app center's own ids (%s); going by "
                    "what was read last", exc)
        return known.get(package, "")
    with _lock:
        _ours.clear()
        _ours.update(known)
        _ours_at = time.monotonic()
    return known.get(package, "")


def remember(settings, package: str, version_id: str, *,
             version_name: str = "", at: str = "") -> None:
    """Write down the center's id for an app we uploaded, so every builder
    installs it. A blank id forgets the package.

    `version_name` is what the app calls that build - "1.2026.244" - and
    it is what a check against Play compares. Raises rather than
    reporting trouble: a recorder that quietly did nothing would be found
    out one slow build at a time.
    """
    from .store import db
    from .store import state as store_state

    known = {str(k): _entry(v) for k, v in
             dict(store_state.get(settings, STATE_KEY, {}) or {}).items()}
    known = {k: v for k, v in known.items() if v["id"]}
    if version_id:
        known[package] = {"id": str(version_id),
                          "version": str(version_name),
                          "at": at or time.strftime("%Y-%m-%d")}
    else:
        known.pop(package, None)
    with db.connect(settings) as conn:
        store_state.put(conn, STATE_KEY, known)
        conn.commit()
    forget(package)


def version_id(client: Client, phone_id: str, package: str, *,
               name: str, settings=None) -> str:
    """The center's id for the newest version of `package`, or "" when the
    center has no such app. `name` is the search word the listing takes;
    the package is what is matched, since a search for "Chat" finds
    Snapchat.

    What we uploaded ourselves comes first and never reaches the listing,
    because the listing is GeeLark's catalogue and an upload is not in it.
    """
    with _lock:
        cached = _versions.get(package, "")
    if cached:
        return cached
    mine = uploaded(settings, package)
    if mine:
        return mine
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


def begin(client: Client, phone_id: str, package: str, *, name: str,
          settings=None) -> bool:
    """Ask GeeLark to install `package` on the phone, and say whether it
    took the order. Never raises: a door that does not open is Play's turn,
    and the phone is already up and billing.

    Several may be given at once: three orders inside two seconds were all
    taken on phone 2184, and the missing app was on the phone fourteen
    seconds later (2026-09-12).
    """
    try:
        version = version_id(client, phone_id, package, name=name,
                             settings=settings)
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
