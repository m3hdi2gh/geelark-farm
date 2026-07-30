"""Phone lifecycle.

Phase 2 delivers the read-and-run half - status, start, stop, screenshot -
because every device command needs a running phone. Creation, the ledger and
the reaper are phase 3.

Billing is per running minute, so `start` is the one call in this project that
begins spending. Anything that starts a phone owns stopping it.
"""

from __future__ import annotations

import logging
import time

from .api import Client

log = logging.getLogger(__name__)

RUNNING, STARTING, STOPPED, EXPIRED = 0, 1, 2, 3
STATUS_NAMES = {RUNNING: "running", STARTING: "starting",
                STOPPED: "stopped", EXPIRED: "expired"}

# Poll interval for boot waits. Never drop below 10s: the rate limit is a
# process-wide budget and a two-hour ban is the penalty for exhausting it.
POLL_SECONDS = 10


class PhoneError(Exception):
    """A phone is not in a usable state."""


def listing(client: Client, page_size: int = 100) -> list[dict]:
    data = client.data("/v1/phone/list", {"page": 1, "pageSize": page_size}) or {}
    return data.get("items") or []


def newest(client: Client) -> dict | None:
    """The most recently created phone that has not expired."""
    alive = [p for p in listing(client) if p.get("status") != EXPIRED]
    if not alive:
        return None
    return max(alive, key=lambda p: p.get("createTime") or 0)


def status(client: Client, phone_id: str) -> int | None:
    """0 running, 1 starting, 2 stopped, 3 expired.

    /phone/status answers under successDetails, unlike /phone/addNew which uses
    details - the envelope is not consistent across endpoints.
    """
    data = client.data("/v1/phone/status", {"ids": [phone_id]}) or {}
    for item in data.get("successDetails") or []:
        if item.get("id") == phone_id:
            return item.get("status")
    for item in data.get("failDetails") or []:
        raise PhoneError(f"status failed [{item.get('code')}] {item.get('msg')}")
    return None


def start(client: Client, phone_id: str) -> str | None:
    """Begin billing. Returns the live-view URL, which is the fastest way to
    see what a flow is actually doing."""
    data = client.data("/v1/phone/start", {"ids": [phone_id]}) or {}
    for item in data.get("failDetails") or []:
        raise PhoneError(f"start failed [{item.get('code')}] {item.get('msg')}")
    url = None
    for item in data.get("successDetails") or []:
        url = item.get("url") or url
        if item.get("chargingMethod"):
            log.info("billing: %s", item["chargingMethod"])
    return url


def stop(client: Client, phone_id: str) -> None:
    """End billing. Never strict: stopping an already-stopped phone is a
    success as far as the caller is concerned."""
    client.post("/v1/phone/stop", {"ids": [phone_id]}, strict=False)


def wait_until_running(client: Client, phone_id: str, *,
                       timeout: float = 600, settle: float = 30) -> None:
    """Block until the phone reports running, then let Play Services settle.

    The settle wait is not superstition: a dump taken immediately after boot
    returns a hierarchy that is still changing.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = status(client, phone_id)
        if state == RUNNING:
            log.info("phone running; settling for %.0fs", settle)
            time.sleep(settle)
            return
        if state == EXPIRED:
            raise PhoneError(f"phone {phone_id} has expired")
        log.info("phone %s (%s)", STATUS_NAMES.get(state, state), state)
        time.sleep(POLL_SECONDS)
    raise PhoneError(f"phone {phone_id} did not start within {timeout:.0f}s")


def ensure_running(client: Client, phone_id: str, *,
                   settle: float = 30) -> str | None:
    """Start the phone if needed. Returns the live-view URL when it started
    it, None when it was already up.

    Shell commands fail in confusing ways on a stopped phone, so every device
    command goes through here.
    """
    state = status(client, phone_id)
    if state == RUNNING:
        return None
    if state == EXPIRED:
        raise PhoneError(f"phone {phone_id} has expired")
    log.info("phone is %s - starting it (billing is per minute)",
             STATUS_NAMES.get(state, state))
    url = start(client, phone_id)
    if url:
        log.info("watch it live: %s", url)
    wait_until_running(client, phone_id, settle=settle)
    return url


def screenshot(client: Client, phone_id: str, *, timeout: float = 60) -> str | None:
    """Capture the screen and return a download link.

    Asynchronous: the request returns a taskId, then the result is polled.
    """
    started = client.data("/v1/phone/screenShot", {"id": phone_id}) or {}
    task_id = started.get("taskId")
    if not task_id:
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        result = client.data("/v1/phone/screenShot/result", {"taskId": task_id},
                             strict=False) or {}
        if result.get("status") == 2:
            return result.get("downloadLink")
        if result.get("status") in (0, 3):
            break
    log.warning("screenshot did not complete")
    return None
