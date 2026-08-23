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
import re
import time
from collections.abc import Callable
from urllib.parse import quote

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

#: How long a phone gets to come up before it is called stuck. A GeeLark phone
#: boots in well under a minute; ten is generous. It is a cap and not a budget:
#: every caller has a much larger deadline of its own and used to hand the whole
#: of it over, so a phone GeeLark kept reporting as `starting` was polled for
#: another thirty-eight minutes while a worker and a plan slot sat on it
#: (2026-08-14, phone 750).
BOOT_SECONDS = 600


class PhoneError(Exception):
    """A phone is not in a usable state."""


#: A page that comes back short is the last one. The cap is a guard against a
#: server that keeps answering full pages forever, not an expected limit: at
#: 100 a page it allows ten thousand phones, and a plan holds tens.
MAX_PAGES = 100


def listing(client: Client, page_size: int = 100) -> list[dict]:
    """Every phone on the account.

    Every page of them. It asked for page 1 and stopped, which is twenty
    callers' worth of "what exists" - including the sync that decides which
    rows have lost their phone. Past a hundred phones the rest would simply
    not exist as far as any of them could tell: their rows read as stranded
    and get settled, while the phones themselves stay up and billing.

    Out of reach today at thirty plan slots. Warm stock is what raises that
    number, and it raises it while nobody is watching.
    """
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = client.data("/v1/phone/list",
                           {"page": page, "pageSize": page_size}) or {}
        batch = data.get("items") or []
        items.extend(batch)
        if len(batch) < page_size:
            return items
    log.warning("stopped listing phones at %d pages (%d so far); if this is "
                "real, MAX_PAGES needs raising", MAX_PAGES, len(items))
    return items


def plan(client: Client) -> dict:
    """The subscription's limits and what is left of them.

    The only way to see why a creation was refused with [44002]. Note that
    `profiles` is a pool shared with browser profiles - the same error code is
    documented for both - so cloud phones alone need not add up to the total.

    GeeLark rate-limits this endpoint to one request per minute.
    """
    return client.data("/v1/pay/plan/info") or {}


# GeeLark's proxy type ids, from the vendor's own CLI reference:
# -1 direct, 1 socks5, 2 http, 3 https, 20+ are named providers.
PROXY_TYPE_IDS = {"socks5": 1, "http": 2, "https": 3}


def set_proxy(client: Client, phone_id: str, proxy: Proxy) -> None:
    """Point an existing phone at a different proxy.

    This is the call the whole retry story rests on. It was assumed for most of
    this project's life that a proxy was fixed at creation - which made a
    CAPTCHA a reason to delete the phone, since the verdict was on an exit
    address that could not be changed. It can be changed, and everything built
    on that assumption is wrong (see UNREUSABLE in orchestrator.py).

    Two constraints from the vendor's docs, both learned the expensive way if
    ignored:

    - "Do not call while starting a cloud phone." So callers stop the phone
      first, which is needed anyway: Android reads the proxy when the network
      comes up, and a phone that is already running keeps the old exit.
    - The proxy is checked by GeeLark as part of this call. [45004] means the
      new proxy did not answer, and the phone keeps the one it had - so a
      failure here is safe, it just has not achieved anything.
    """
    type_id = PROXY_TYPE_IDS.get(proxy.scheme)
    if type_id is None:                                          # pragma: no cover
        raise PhoneError(f"GeeLark has no proxy type for {proxy.scheme!r}")
    client.post("/v1/phone/detail/update", {
        "id": phone_id,
        "proxyConfig": {
            "typeId": type_id,
            "server": proxy.host,
            "port": proxy.port,
            "username": proxy.username,
            "password": proxy.password,
        },
    })
    log.info("phone %s now uses %s", phone_id, proxy)


#: How a phone is named in GeeLark's own list.
#:
#: The list used to read `farm-1786928959`, seven rows deep, differing in the
#: last three digits of a unix timestamp - which named the second a phone was
#: made and nothing anyone ever wants to know. The serial leads because it is
#: the key everything else is filed under: the Phones tab is addressed by it,
#: History records it, and the artifacts of a failed build are named for it.
#: The address follows because it is the part a person actually thinks in.
#:
#: ASCII only, deliberately. GeeLark stores a name with a non-ASCII separator
#: as mojibake - `832 - Rapid...` came back with the middle dot replaced by a
#: replacement character (measured 2026-08-17).
NAME_SEPARATOR = " - "


def display_name(serial: str | int = "", account: str = "") -> str:
    """`832 - RapidStorm162935`, from whichever halves are known.

    Empty when neither is, so the caller can fall back to something unique -
    a phone still has to be called something before it has a serial.
    """
    local = account.split("@")[0].strip()
    return NAME_SEPARATOR.join(p for p in (str(serial).strip(), local) if p)


def rename(client: Client, phone_id: str, name: str) -> None:
    """Change what the phone is called in GeeLark. Cosmetic, and never worth
    failing a build over - callers log and carry on."""
    client.post("/v1/phone/detail/update", {"id": phone_id, "name": name})
    log.info("phone %s is now called %r", phone_id, name)


def newest(client: Client) -> dict | None:
    """The most recently created phone that has not expired."""
    alive = [p for p in listing(client) if p.get("status") != EXPIRED]
    if not alive:
        return None
    return max(alive, key=lambda p: p.get("createTime") or 0)


def create(client: Client, settings: Settings, proxy: Proxy, *,
           ledger: Ledger, name: str | None = None, label: str = "",
           account: str = "") -> Entry:
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
            # Named twice: once here with what is known before the phone
            # exists, once below with the serial GeeLark answers with. The
            # first name is what survives if the second call fails, so it is
            # the address rather than a timestamp wherever there is one.
            "profileName": (name or display_name(account=account)
                            or f"{settings.phone_name_prefix}-{int(time.time())}"),
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

    if name is None:
        wanted = display_name(row.get("envSerialNo") or "", account)
        if wanted:
            try:
                rename(client, phone_id, wanted)
            except Exception as exc:                              # noqa: BLE001
                # The phone is made, recorded and usable. A list that reads a
                # little worse is not a reason to throw that away.
                log.warning("could not rename %s to %r (%s)",
                            phone_id, wanted, exc)

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
    is not a documented way to end billing.

    The per-item answer is read, and this is the whole point of the function
    doing anything beyond one POST. `/phone/delete` returns `code: 0` at the
    envelope whatever happens to the phones inside it, and puts the refusals
    under `failDetails` - the same shape `/phone/start` has always been read
    for. Not reading it here meant every refusal was reported as a deletion:
    two running phones were recorded as discarded, had their rows dropped and
    their exits freed, and are still in the panel with nothing in the sheet
    that knows about them (2026-08-17, phones 840 and 841).

    Raises PhoneError naming what would not go. Whatever did go is forgotten
    from the ledger first, so a partial delete leaves no ghosts either way.
    """
    answer = client.post("/v1/phone/delete", {"ids": phone_ids}) or {}
    data = answer.get("data") or {}
    refused = {str(item.get("id")): item for item in
               (data.get("failDetails") or [])}
    for phone_id in phone_ids:
        if str(phone_id) in refused:
            continue
        if ledger:
            ledger.forget(phone_id)
        log.info("deleted %s", phone_id)
    if refused:
        said = "; ".join(f"{item.get('id')} [{item.get('code')}] "
                         f"{item.get('msg')}" for item in refused.values())
        raise PhoneError(f"delete refused: {said}")


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


def tidy_url(url: str) -> str:
    """Percent-encode the profile name GeeLark pastes into the live-view link.

    GeeLark builds this URL by dropping the profile name straight into
    `envName=` without encoding it. That was harmless while every name was
    `farm-1786928959`; a name with spaces in it - which is now every name this
    tool writes - ends the URL as far as a terminal is concerned, so the link
    printed above a batch could only be selected as far as the first space
    (2026-08-17).

    Only that one value is touched. The token further along the query is
    signed, and re-encoding the whole URL risks changing it.
    """
    return re.sub(r"(?<=envName=)[^&]*",
                  lambda found: quote(found.group(0), safe=""), url)


def start(client: Client, phone_id: str) -> str | None:
    """Begin billing. Returns the live-view URL, which is the fastest way to
    see what a flow is actually doing."""
    data = client.data("/v1/phone/start", {"ids": [phone_id]}) or {}
    for item in data.get("failDetails") or []:
        raise PhoneError(f"start failed [{item.get('code')}] {item.get('msg')}")
    url = None
    for item in data.get("successDetails") or []:
        url = tidy_url(item["url"]) if item.get("url") else url
        if item.get("chargingMethod"):
            log.info("billing: %s", item["chargingMethod"])
    return url


def stop(client: Client, phone_id: str) -> None:
    """End billing. Never strict: stopping an already-stopped phone is a
    success as far as the caller is concerned."""
    client.post("/v1/phone/stop", {"ids": [phone_id]}, strict=False)


#: How long to wait for a phone to come down before deleting it anyway.
#: Short: this is only ever on the way to a delete, and a phone that will not
#: stop is reported by the delete itself rather than waited out.
STOP_SECONDS = 60


def wait_until_stopped(client: Client, phone_id: str, *,
                       timeout: float = STOP_SECONDS) -> bool:
    """Block until the phone is down, or the wait runs out. True if it is down.

    `stop` posts the request and returns; GeeLark goes on reporting the phone
    as running while it shuts down, and it refuses to delete one that is still
    up. Asking straight after stopping is therefore refused nearly every time.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = status(client, phone_id)
        if state in (STOPPED, EXPIRED) or state is None:
            return True
        time.sleep(POLL_SECONDS)
    log.warning("phone %s was still not stopped after %.0fs", phone_id, timeout)
    return False


def wait_until_running(client: Client, phone_id: str, *,
                       timeout: float = BOOT_SECONDS, settle: float = 30,
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancelled and cancelled():
            raise PhoneError(f"stopped waiting for phone {phone_id}: "
                             f"the run is shutting down")
        state = status(client, phone_id)
        if state == RUNNING:
            log.info("phone running; settling for %.0fs", settle)
            # In pieces, so the settle answers an interrupt too. The loop
            # around it checks `cancelled` and this did not, so a run being
            # shut down still owed every worker up to half a minute in a
            # sleep nothing could reach.
            waited = 0.0
            while waited < settle:
                if cancelled and cancelled():
                    raise PhoneError(f"stopped waiting for phone {phone_id}: "
                                     f"the run is shutting down")
                nap = min(2.0, settle - waited)
                time.sleep(nap)
                waited += nap
            return
        if state == EXPIRED:
            raise PhoneError(f"phone {phone_id} has expired")
        log.info("phone %s (%s)", STATUS_NAMES.get(state, state), state)
        time.sleep(POLL_SECONDS)
    raise PhoneError(f"phone {phone_id} did not start within {timeout:.0f}s")


def ensure_running(client: Client, phone_id: str, *, settle: float = 30,
                   timeout: float = BOOT_SECONDS,
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(3)
        result = client.data("/v1/phone/screenShot/result", {"taskId": task_id},
                             strict=False) or {}
        if result.get("status") == 2:
            return result.get("downloadLink")
        if result.get("status") in (0, 3):
            break
    log.warning("screenshot did not complete")
    return None
