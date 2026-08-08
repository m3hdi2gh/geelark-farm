"""Phone lifecycle: create, start, stop, delete, and the reaper.

Billing is per running minute, so `start` is the call that begins spending and
anything that starts a phone owns stopping it. `create` is the call that makes
a phone exist at all, and it records the phone in the ledger before returning -
the window between "created" and "recorded" is exactly how the prototype
produced orphans.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable

from .api import Client
from .config import Settings
from .ledger import Entry, Ledger
from .proxy import Proxy

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


def plan(client: Client) -> dict:
    """The subscription's limits and what is left of them.

    The only way to see why a creation was refused with [44002]. Note that
    `profiles` is a pool shared with browser profiles - the same error code is
    documented for both - so cloud phones alone need not add up to the total.

    GeeLark rate-limits this endpoint to one request per minute.
    """
    return client.data("/v1/pay/plan/info") or {}


def serial_of(client: Client, phone_id: str) -> str:
    """The human-facing serial for a phone that already exists.

    Creation returns it, but a run that reuses a phone never sees it - and the
    serial is how a phone is identified in the GeeLark panel, so a row without
    one is harder to act on than it needs to be.
    """
    for item in listing(client):
        if item.get("id") == phone_id:
            return str(item.get("serialNo") or "")
    return ""


def newest(client: Client) -> dict | None:
    """The most recently created phone that has not expired."""
    alive = [p for p in listing(client) if p.get("status") != EXPIRED]
    if not alive:
        return None
    return max(alive, key=lambda p: p.get("createTime") or 0)


def create(client: Client, settings: Settings, proxy: Proxy, *,
           ledger: Ledger, name: str | None = None, label: str = "") -> Entry:
    """Create one phone bound to `proxy`, and record it in the ledger.

    The proxy is set at creation so the device never touches the network
    unproxied. Constraints that are not obvious (see docs/geelark-api.md):
    region 'us' only offers Android 15, netType applies only on Android
    12/13/15, and mobileLanguage MUST be 'default' - a non-English UI makes
    every English text selector fail.

    /phone/addNew answers per item under 'details', not 'successDetails'.
    """
    data = client.data("/v1/phone/addNew", {
        "mobileType": settings.android,
        "chargeMode": 0,
        "region": settings.region,
        "data": [{
            "profileName": name or f"{settings.phone_name_prefix}-{int(time.time())}",
            "proxyInformation": proxy.url,
            "proxyQueryChannel": 2,
            "mobileLanguage": "default",
            "netType": 1,
            "profileGroup": "automation",
        }],
    }) or {}

    created = [d for d in (data.get("details") or [])
               if d.get("code") == 0 and d.get("id")]
    if not created:
        raise PhoneError("creation failed:\n" + json.dumps(data, indent=2))

    row = created[0]
    phone_id = row["id"]
    # Record before anything else can fail. A phone that exists but is not in
    # the ledger is invisible to reap and bills silently.
    entry = ledger.record(phone_id, serial=row.get("envSerialNo"), label=label,
                          proxy=f"{proxy.host}:{proxy.port}")

    info = row.get("equipmentInfo") or {}
    log.info("created %s (serial %s): %s %s / %s, %s / %s",
             phone_id, row.get("envSerialNo"), info.get("deviceBrand"),
             info.get("deviceModel"), info.get("osVersion"),
             info.get("countryName"), info.get("timeZone"))
    if info.get("netType") == 0:
        log.info("netType came back 0 (Wi-Fi) despite requesting mobile data")
    return entry


def delete(client: Client, phone_ids: list[str], *,
           ledger: Ledger | None = None) -> None:
    """Delete phones permanently. Stop them first - deleting a running phone
    is not a documented way to end billing."""
    client.post("/v1/phone/delete", {"ids": phone_ids})
    for phone_id in phone_ids:
        if ledger:
            ledger.forget(phone_id)
        log.info("deleted %s", phone_id)


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
                       timeout: float = 600, settle: float = 30,
                       cancelled: Callable[[], bool] | None = None) -> None:
    """Block until the phone reports running, then let Play Services settle.

    The settle wait is not superstition: a dump taken immediately after boot
    returns a hierarchy that is still changing.

    `cancelled` is how an interrupt reaches this loop. Without it, Ctrl+C
    stopped the phones and then left every worker polling the phone it had just
    had stopped underneath it - for the full ten minutes, printing "phone
    stopped" the whole way. The process could not exit either: a
    ThreadPoolExecutor's threads are not daemons and Python joins them on the
    way out, so the terminal sat there ignoring further Ctrl+C (2026-08-08).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancelled and cancelled():
            raise PhoneError(f"stopped waiting for phone {phone_id}: "
                             f"the run is shutting down")
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


def ensure_running(client: Client, phone_id: str, *, settle: float = 30,
                   timeout: float = 600,
                   on_url: Callable[[str], None] | None = None,
                   cancelled: Callable[[], bool] | None = None) -> str | None:
    """Start the phone if needed. Returns the live-view URL when it started
    it, None when it was already up.

    `on_url` fires the moment the URL is known, before the boot wait. Without
    it the link only surfaces a minute and a half later, by which time whatever
    you wanted to watch has already happened.

    `timeout` lets a caller with its own deadline - a batch row, for instance -
    cap the boot wait rather than letting it spend ten minutes of a budget that
    has to cover the whole row.

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
        if on_url:
            on_url(url)
    wait_until_running(client, phone_id, settle=settle, timeout=timeout,
                       cancelled=cancelled)
    return url


def prune_ledger(client: Client, ledger: Ledger) -> list[str]:
    """Forget phones that no longer exist upstream.

    Phones get deleted from the GeeLark panel directly, and without this the
    ledger grows forever with entries for devices that are gone - which makes
    `phones --ledger` misleading and hides the entries that still matter.
    """
    live = {p.get("id") for p in listing(client)}
    gone = [phone_id for phone_id in ledger.entries if phone_id not in live]
    for phone_id in gone:
        ledger.forget(phone_id)
        log.info("ledger: forgot %s (no longer on the account)", phone_id)
    return gone


def reapable(client: Client, ledger: Ledger) -> list[tuple[str, str]]:
    """Which running phones should be stopped, and why.

    A phone that is running is spending money, so the question is only ever
    "does something legitimately need this right now?". Three cases say no:

    - not in the ledger at all: nothing created it through this tool, or the
      ledger was lost. Either way nothing here is accountable for it.
    - released: a run finished with it and it should already be off.
    - stale claim: a run claimed it hours ago and never came back, so the
      process that owned it is gone.

    A fresh claim is left alone - that is a run in progress.
    """
    verdicts = []
    for item in listing(client):
        if item.get("status") not in (RUNNING, STARTING):
            continue
        phone_id = item.get("id")
        entry = ledger.get(phone_id)
        if entry is None:
            verdicts.append((phone_id, "not in the ledger"))
        elif entry.released_at is not None:
            verdicts.append((phone_id, "already released by its run"))
        elif entry.is_stale:
            hours = (time.time() - entry.claimed_at) / 3600
            verdicts.append((phone_id, f"claimed {hours:.1f}h ago, owner gone"))
        elif not entry.is_claimed:
            verdicts.append((phone_id, "created but never claimed"))
    return verdicts


def reap(client: Client, ledger: Ledger, *, dry_run: bool = False,
         verdicts: list[tuple[str, str]] | None = None) -> int:
    """Stop every phone nothing is accountable for. The backstop for when a
    run dies before its own cleanup.

    A caller that has already shown the user what will be stopped passes those
    verdicts back in, so the list acted on is the list that was displayed - a
    second lookup could disagree with the first, and the user would have
    approved something other than what happened.
    """
    if verdicts is None:
        verdicts = reapable(client, ledger)
    for phone_id, reason in verdicts:
        if dry_run:
            log.info("would stop %s (%s)", phone_id, reason)
            continue
        stop(client, phone_id)
        ledger.release(phone_id, note=f"reaped: {reason}")
        log.info("stopped %s (%s)", phone_id, reason)
    return len(verdicts)


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
