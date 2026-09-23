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
#: What the helpers moved here from builder.py write to - its logger, as
#: before the move (the builder review, 2026-09-23).
_build_log = logging.getLogger("geelark_farm.builder")

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


class PhoneCapacityError(PhoneError):
    """GeeLark has no machine free of this Android version right now.

    Its own subclass because it is the one refusal that says nothing about
    this phone, this account or this code. Nothing was created and nothing
    was spent; the answer is to come back shortly, which is what GeeLark's
    own message advises.
    """


class WaitInterrupted(PhoneError):
    """A wait on the phone was stopped because the run is shutting down.

    Its own subclass so a build can file it as the stop it is. As a plain
    PhoneError it read as `phone_would_not_start` - a verdict on the phone -
    and the empty phone was deleted, which the run's own shutdown is the one
    stop that must not do (the builder review, 2026-09-23). Still a
    PhoneError, so every other caller is answered as before.
    """


#: What GeeLark answers when it is out of machines of the requested Android
#: version. Transient - it clears in minutes - and it arrives at the phone
#: that happened to ask, not at anything that is wrong (2026-08-28).
CAPACITY_REFUSED = 43043

#: How many times to ask before giving the refusal back to the caller, and how
#: long to leave between. Four over half a minute: long enough to ride out the
#: usual dip, short enough that a build budget does not notice.
CAPACITY_ATTEMPTS = 4
CAPACITY_WAIT_SECONDS = 10


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
    on that assumption is wrong.

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


#: GeeLark refuses a phone in two shapes. `/phone/addNew` answers 200
#: with the failure inside `details`, so `PhoneError` carries the whole
#: JSON body; everything else raises `ApiError`, whose text starts
#: `[41001] balance not enough (/v1/phone/start)`. Both are one code and
#: one sentence.
_REFUSAL_PAIR = re.compile(r'"code"\s*:\s*(\d+)\s*,\s*"msg"\s*:\s*"([^"]*)"')
_REFUSAL_CODE = re.compile(r'"code"\s*:\s*(\d+)')
_REFUSAL_MSG = re.compile(r'"msg"\s*:\s*"([^"]*)"')
_REFUSAL_BRACKET = re.compile(r"\[(\d+)\]\s*([^(\n]*)")


def read_refusal(said: str) -> dict:
    """GeeLark's reason for turning a phone down, in two fields.

    Two hundred characters of braces were going straight onto the
    console, where somebody looks to find out what is wrong: the alert
    strip read `creation failed: { "totalAmount": 1, "successAmount":
    0, ...` and the reader had to hunt for `"code": 45004` inside it
    (the operator, 2026-09-20). One code and one sentence is the whole
    of what it said.

    Returns `code` (None when there is none to find), `msg`, and
    `said` - a line short enough to print, keeping whatever the caller
    put in front of the payload: `creation failed [45004] check proxy
    failed`.
    """
    text = str(said or "")
    head = " ".join(text.split("{", 1)[0].split("[", 1)[0]
                    .replace(":", " ").split())
    code, msg = None, ""
    # A pair first, so a body that lists several details cannot take the
    # code from one and the sentence from another. Zero is success: the
    # envelope around a failed item says `"code": 0` of itself.
    for found, spoken in _REFUSAL_PAIR.findall(text):
        if int(found):
            code, msg = int(found), spoken
            break
    if code is None:
        numbers = [int(c) for c in _REFUSAL_CODE.findall(text) if int(c)]
        spoken = [m for m in _REFUSAL_MSG.findall(text) if m.strip()]
        if numbers:
            code, msg = numbers[0], (spoken[0] if spoken else "")
        else:
            hit = _REFUSAL_BRACKET.search(text)
            if hit:
                code, msg = int(hit.group(1)), hit.group(2).strip()
    said_short = " ".join(
        x for x in (head, f"[{code}] {msg}".strip() if code else "") if x)
    return {"code": code, "msg": " ".join(msg.split()),
            "said": said_short or " ".join(text.split())[:120]}


def display_name(serial: str | int = "", account: str = "") -> str:
    """`832 - MerylQuinn162935`, from whichever halves are known.

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
            # The farm's group, or a playground's (settings.phone_group):
            # what the reap and the proxy sync tell the two apart by.
            "profileGroup": getattr(settings, "phone_group", "") or FARM_GROUP,
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
    try:
        entry.model = " ".join(str(info.get(k) or "").strip()
                               for k in ("deviceBrand", "deviceModel")).strip()
    except AttributeError as exc:
        log.debug("the ledger entry takes no model (%s)", exc)
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


def start(client: Client, phone_id: str, *,
          attempts: int = CAPACITY_ATTEMPTS) -> str | None:
    """Begin billing. Returns the live-view URL, which is the fastest way to
    see what a flow is actually doing.

    A capacity refusal is asked again rather than raised at once. GeeLark runs
    out of machines of a given Android version for minutes at a time, says so,
    and advises trying again - and it is safe to: the refusal means no phone
    was started, so asking twice cannot start two.

    Every other refusal is raised on the first answer. Retrying a phone that
    has expired or been deleted only takes longer to say the same thing.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    for attempt in range(1, attempts + 1):
        data = client.data("/v1/phone/start", {"ids": [phone_id]}) or {}
        refused = next(iter(data.get("failDetails") or []), None)
        if refused is None:
            break
        said = f"start failed [{refused.get('code')}] {refused.get('msg')}"
        # Compared as text, because the envelope is not consistent about it.
        # `43043 != "43043"` sent a capacity refusal down the branch for
        # everything else, so it was raised as a bare PhoneError on the first
        # answer instead of being retried - and arrived in the sheet as
        # `error`, which counts against the breaker, rather than `no_capacity`,
        # which is in `NOTHING_HAPPENED` and does not. Five busy afternoons in
        # a row would have stopped the service over a shortage of machines at
        # somebody else's datacentre (2026-08-29).
        if str(refused.get("code")) != str(CAPACITY_REFUSED):
            raise PhoneError(said)
        if attempt == attempts:
            raise PhoneCapacityError(said)
        log.warning("%s - asking again (%d of %d)", said, attempt, attempts)
        time.sleep(CAPACITY_WAIT_SECONDS)

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
                       cancelled: Callable[[], bool] | None = None,
                       on_running: Callable[[], None] | None = None) -> None:
    """Block until the phone reports running, then let Play Services settle.

    The settle wait is not superstition: a dump taken immediately after boot
    returns a hierarchy that is still changing.

    `on_running` fires once, the moment the phone reports running and
    before the settle - for work that needs a live phone and no screen,
    such as asking GeeLark to install an app, which then lands while the
    settle and the sign-in go on (2026-09-08).

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
            raise WaitInterrupted(f"stopped waiting for phone {phone_id}: "
                                  f"the run is shutting down")
        state = status(client, phone_id)
        if state == RUNNING:
            if on_running:
                on_running()
            log.info("phone running; settling for %.0fs", settle)
            # In pieces, so the settle answers an interrupt too. The loop
            # around it checks `cancelled` and this did not, so a run being
            # shut down still owed every worker up to half a minute in a
            # sleep nothing could reach.
            waited = 0.0
            while waited < settle:
                if cancelled and cancelled():
                    raise WaitInterrupted(
                        f"stopped waiting for phone {phone_id}: the run is "
                        f"shutting down")
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
                   cancelled: Callable[[], bool] | None = None,
                   on_running: Callable[[], None] | None = None) -> str | None:
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
        if on_running:
            on_running()
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
                       cancelled=cancelled, on_running=on_running)
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


#: The GeeLark profile group every phone this farm creates is put in, at
#: creation, by `phones.create`. The account is shared with other people,
#: and this is what tells a phone of ours from one of theirs.
FARM_GROUP = "automation"


def group_of(item: dict) -> str:
    """The GeeLark group a listed phone is in, casefolded - "" for none.
    The listing answers `group` as an object, and a phone with no group
    answers it with every field empty rather than answering nothing."""
    group = item.get("group") or {}
    if not isinstance(group, dict):
        return ""
    return str(group.get("name") or "").strip().casefold()


def reap_scope(settings) -> dict:
    """What this process's reap may look at. The farm spares the
    playground's group; a playground (a `phone_group` of its own) reaps
    only its own. Without the second half a playground's reap stopped
    every farm build, because the farm's claims are in Postgres and a
    playground's ledger has never heard of them (the builder review,
    2026-09-23)."""
    group = str(getattr(settings, "phone_group", "") or FARM_GROUP)
    if group.casefold() != FARM_GROUP:
        return {"only_group": group.casefold()}
    return {"skip_groups": tuple(getattr(settings, "spared_groups", ()) or ())}


def reapable(client: Client, ledger: Ledger, *, only_group: str | None = None,
             skip_groups: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Which running phones should be stopped, and why.

    A phone that is running is spending money, so the question is only ever
    "does something legitimately need this right now?". Four cases say no:

    - not in the ledger at all: nothing created it through this tool, or the
      ledger was lost. Either way nothing here is accountable for it.
    - released: a run finished with it and it should already be off.
    - stale claim: a run claimed it hours ago and never came back, so the
      process that owned it is gone.
    - recorded but never claimed: something made the phone and then nothing
      took responsibility for it. `geelark create --start` is how that
      happens, and it is why that command does not claim.

    A fresh claim is left alone - that is a run in progress.

    `only_group`/`skip_groups` scope it by GeeLark group (`reap_scope`):
    a phone outside what this process may touch is not its business,
    accounted for or not.
    """
    verdicts = []
    skip = {g.casefold() for g in skip_groups}
    for item in listing(client):
        if item.get("status") not in (RUNNING, STARTING):
            continue
        group = group_of(item)
        if group in skip or (only_group is not None and group != only_group):
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
         verdicts: list[tuple[str, str]] | None = None,
         only_group: str | None = None,
         skip_groups: tuple[str, ...] = ()) -> int:
    """Stop every phone nothing is accountable for. The backstop for when a
    run dies before its own cleanup.

    A caller that has already shown the user what will be stopped passes those
    verdicts back in, so the list acted on is the list that was displayed - a
    second lookup could disagree with the first, and the user would have
    approved something other than what happened.
    """
    if verdicts is None:
        verdicts = reapable(client, ledger, only_group=only_group,
                            skip_groups=skip_groups)
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


def _bad_model(settings: Settings, model: str) -> bool:
    """Whether GeeLark handed out a model the numbers condemned."""
    said = (model or "").casefold()
    return bool(said) and any(bad.casefold() in said
                              for bad in getattr(settings, "bad_models", ()))


def _create_kept(client: Client, settings: Settings, ledger: Ledger, proxy,
                 *, label: str, account: str):
    """`phones.create`, and again when the model is one of the bad ones -
    deleted before anything is spent on it, up to `model_retries` times.
    GeeLark's API takes no model; the only choice is after the fact, and
    a phone a few seconds old costs nothing but those seconds (the model
    gate, 2026-09-10). The last one is kept whatever it is."""
    tries = max(0, int(getattr(settings, "model_retries", 0)))
    entry = create(client, settings, proxy, ledger=ledger,
                          label=label, account=account)
    for n in range(tries):
        model = str(getattr(entry, "model", "") or "")
        if not _bad_model(settings, model):
            return entry
        _build_log.warning("phone %s is a %s, which signs in rarely; deleting it "
                    "and creating another (%d of %d)",
                    entry.serial or entry.phone_id, model, n + 1, tries)
        try:
            delete(client, [entry.phone_id], ledger=ledger)
        except Exception as exc:                                  # noqa: BLE001
            _build_log.warning("could not delete phone %s (%s); keeping it",
                        entry.serial or entry.phone_id, exc)
            return entry
        entry = create(client, settings, proxy, ledger=ledger,
                              label=label, account=account)
    return entry


def _remember_refusal(settings: Settings, said: str) -> None:
    """Keep GeeLark's own words about a phone that would not start.

    The open API has no balance in it - `/v1/pay/plan/info` gives the
    slots and the expiry and nothing about money, and there is no other
    endpoint (probed, 2026-09-20). So the only thing that says the
    account has run out is a refusal, and until this it said it only in
    a log line: nineteen builds were turned down for
    `[41001] balance not enough` in six hours and the console showed a
    tripped breaker with no hint why (2026-09-19).

    The code and the sentence are kept apart from each other, because
    only GeeLark can say whether a refusal is about money and only the
    code says it: reading every refusal as an empty account put `out of
    credit` on the console while forty-seven phones were being built,
    on a day whose one refusal was a proxy it could not check
    (2026-09-20). A line of the original goes with them for a shape
    `read_refusal` has never seen.

    Never fatal. It is a note for a page, written on a path that is
    already reporting a failure.
    """
    if not getattr(settings, "store_enabled", False):
        return
    try:
        from .store import db
        from .store import state as store_state

        note = read_refusal(said)
        with db.connect(settings) as conn:
            store_state.put(conn, "geelark_refusal",
                            {"said": note["said"], "code": note["code"],
                             "msg": note["msg"], "at": time.time(),
                             "raw": " ".join(str(said).split())[:200]})
            conn.commit()
    except Exception as exc:                                      # noqa: BLE001
        _build_log.debug("could not keep why a phone would not start (%s)", exc)


def _live_exits(client: Client, skip_groups: tuple[str, ...] = ()
                ) -> dict[str, list[dict]]:
    """What GeeLark says is behind each exit: `host:port` -> the phones on it.

    The only authority on this. The Proxy tab records what a run believed when
    it wrote the row, and the two come apart every time a phone is deleted from
    the panel or moved onto another exit mid-run.

    A list rather than one phone, because an exit can carry more than one since
    a build ran dry and borrowed - and keeping the last one seen would have the
    sync quietly rewrite the tab to name whichever came back second.
    """
    found: dict[str, list[dict]] = {}
    skip = {g.casefold() for g in skip_groups}
    for phone in listing(client):
        # A playground's phone is not the farm's exit user: its proxy row,
        # if it has one, is not the farm's to attach or release (the
        # builder review, 2026-09-23).
        if skip and group_of(phone) in skip:
            continue
        config = phone.get("proxy") or {}
        if config.get("server"):
            found.setdefault(f"{config['server']}:{config.get('port')}",
                             []).append(phone)
    return found


def _in_the_farms_group(phone: dict) -> bool:
    """Whether GeeLark says this phone is in the farm's own group.

    The listing answers `group` as an object - `{"id", "name", "remark"}` -
    and a phone with no group answers the object with every field empty
    rather than answering nothing, so this reads the name and compares it.
    """
    group = phone.get("group") or {}
    if not isinstance(group, dict):
        return False
    return str(group.get("name") or "").strip().casefold() == FARM_GROUP
