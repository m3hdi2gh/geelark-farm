"""What the web's buttons do, once the serve pass picks them up.

Every entry is a handler for one verb in the actions queue, run by
`serve._drain_actions` with the pass's own Book, ledger, settings and
GeeLark client - which is the whole point of the queue: the sheet keeps
its one writer, and a button becomes a row that the writer carries out.
A handler returns (status, sentence, detail): the sentence is what the
Requests page shows, so it says what happened in the person's words.

Nothing here touches the store directly; the drain is inside the store
flag and imports `store.validate` lazily for the same reason everything
else does - a box that never opted in never runs a line of this.
"""

from __future__ import annotations

import logging
import re
import time

from . import proxy as proxy_mod
from .api import ApiError

log = logging.getLogger(__name__)

_SX = re.compile(r"^SX(\d+)$", re.IGNORECASE)


def _by(payload: dict) -> str:
    return payload.get("by") or "the web"


def _stamp() -> str:
    return time.strftime("%Y-%m-%d")


# ------------------------------------------------------------------ adds
def add_gmails(book, ledger, settings, payload, client):
    from .store import validate

    added, skipped, refused = [], [], []
    seller = (payload.get("seller") or "").strip()
    for row in payload.get("rows") or []:
        try:
            checked = validate.gmail_row(
                address=row.get("address", ""),
                password=row.get("password", ""),
                secret=row.get("recovery") or row.get("secret", ""),
                seller=seller)
        except (validate.AccountError, validate.ProxyError) as exc:
            refused.append(f"{row.get('address', '?')}: {exc}")
            continue
        if book.gmails.find(checked["address"]) is not None:
            skipped.append(checked["address"])
            continue
        book.gmails.append(**{
            "Purchase Date": _stamp(), "Seller": seller,
            "Address": checked["address"], "Password": checked["password"],
            "Secret": checked["recovery_email"] or checked["totp_secret"],
            "Status": "",
            "Note": f"Added from the web by {_by(payload)} on {_stamp()}."})
        added.append(checked["address"])
    return _summary("gmail", added, skipped, refused)


def add_gpt(book, ledger, settings, payload, client):
    from .store import validate

    added, skipped, refused = [], [], []
    for row in payload.get("rows") or []:
        try:
            checked = validate.app_row(
                address=row.get("address", ""),
                password=row.get("password", ""),
                secret=row.get("secret", ""),
                email_code_only=bool(row.get("email_code_only")))
        except (validate.AccountError, validate.ProxyError) as exc:
            refused.append(f"{row.get('address', '?')}: {exc}")
            continue
        if book.apps.find(checked["address"]) is not None:
            skipped.append(checked["address"])
            continue
        book.apps.append(**{
            "Address": checked["address"], "Password": checked["password"],
            "2FA Secret": checked["totp_secret"], "Status": "",
            "Email code": "TRUE" if checked["email_code_only"] else "FALSE",
            "Note": f"Added from the web by {_by(payload)} on {_stamp()}."})
        added.append(checked["address"])
    return _summary("account", added, skipped, refused)


def _next_name(book) -> str:
    highest = 0
    for r in book.proxies._rows:
        hit = _SX.match(r.name or "")
        if hit:
            highest = max(highest, int(hit.group(1)))
    return f"SX{highest + 1}"


def add_proxies(book, ledger, settings, payload, client):
    """Each is tested before it joins: a proxy that does not answer goes
    in as `dead` rather than as free stock a build then discovers."""
    from .store import validate

    added, skipped, refused = [], [], []
    for row in payload.get("rows") or []:
        raw = (row.get("raw") or "").strip()
        try:
            checked = validate.proxy_row(raw=raw, name=row.get("name", ""))
        except (validate.AccountError, validate.ProxyError) as exc:
            refused.append(f"{raw or '?'}: {exc}")
            continue
        if book.proxies.find_proxy(f"{checked['host']}:{checked['port']}"):
            skipped.append(f"{checked['host']}:{checked['port']}")
            continue
        name = checked["proxy_name"] or _next_name(book)
        status, note = "free", f"Added from the web by {_by(payload)} on " \
                               f"{_stamp()}."
        exit_ip = ""
        if client is not None:
            try:
                result = proxy_mod.check(client, proxy_mod.parse(raw))
                exit_ip = str(result.get("outboundIP") or "")
            except (proxy_mod.ProxyError, ApiError) as exc:
                log.info("%s did not answer on arrival: %s", name, exc)
                status = book.proxies.dead_status
                note = f"Added from the web, but it did not answer: {exc}"
        book.proxies.append(**{
            "Name": name, "Proxy String": raw, "Status": status,
            "Note": note, "Last Exit IP": exit_ip, "Times Used": "0"})
        added.append(name)
    return _summary("proxy", added, skipped, refused)


def adopt_proxy(book, ledger, settings, payload, client):
    """An exit GeeLark holds that the tab never heard of, taken in."""
    raw = ":".join(p for p in (payload.get("host", ""), payload.get("port", ""),
                               payload.get("username", ""),
                               payload.get("password", "")) if p)
    return add_proxies(book, ledger, settings,
                       dict(payload, rows=[{"raw": raw, "name": ""}]), client)


_PLURAL = {"proxy": "proxies"}


def _summary(what: str, added, skipped, refused):
    many = _PLURAL.get(what, what + "s")
    bits = [f"{len(added)} {what if len(added) == 1 else many} added"]
    if skipped:
        bits.append(f"{len(skipped)} already in the pool")
    if refused:
        bits.append(f"{len(refused)} refused")
    status = "done" if added or (not refused and skipped) else "failed"
    return status, ", ".join(bits), {"added": added, "skipped": skipped,
                                     "refused": refused}


# --------------------------------------------------------------- accounts
def offer_again(book, ledger, settings, payload, client):
    """Blank a set-aside account's status - the web's spelling of "clear
    the cell", with the person's name in the note."""
    address = (payload.get("address") or "").strip()
    resource = book.apps.find(address)
    if resource is None:
        return "failed", f"{address} is not in the Gpt Info tab", None
    status = book.apps.status_of(resource)
    settled = set(book.apps.available_statuses) | {
        book.apps.claimed_status, book.apps.spent_status,
        book.apps.retired_status}
    if status in settled:
        return ("refused", f"{address} is {status or 'free'}, not set "
                           f"aside - nothing to offer again", None)
    book.apps.release(resource, note=(
        f"Offered again from the web by {_by(payload)} on {_stamp()} "
        f"(was {status})."))
    return "done", f"{address} is back in the pool", {"was": status}


# ---------------------------------------------------------------- proxies
def _test(book, client, resource) -> tuple[bool, str, str]:
    if client is None:
        return False, "", "no GeeLark client on this pass"
    try:
        result = proxy_mod.check(client, resource.proxy)
        return True, str(result.get("outboundIP") or ""), ""
    except (proxy_mod.ProxyError, ApiError) as exc:
        return False, "", str(exc)[:200]


def _named(book, payload):
    name = (payload.get("name") or "").strip()
    resource = book.proxies.find_by_name(name)
    if resource is None or resource.proxy is None:
        return None, ("failed", f"{name or '?'} is not one row in the Proxy "
                                f"tab", None)
    return resource, None


def mark_proxy_free(book, ledger, settings, payload, client):
    """`change ip` -> free, after a test: the person says the address was
    changed at the vendor; the test says whether it answers."""
    resource, refused = _named(book, payload)
    if refused:
        return refused
    ok, exit_ip, why = _test(book, client, resource)
    if not ok:
        book.proxies.fail(resource, book.proxies.dead_status, note=(
            f"Marked free from the web by {_by(payload)} on {_stamp()}, but "
            f"it did not answer: {why}"))
        return "failed", f"{resource.name} did not answer: {why}", None
    book.proxies.release(resource, note=(
        f"IP changed - marked free from the web by {_by(payload)} on "
        f"{_stamp()}."))
    if exit_ip:
        book.proxies.record_exit(resource, exit_ip)
    return "done", f"{resource.name} is free again (exit {exit_ip})", None


def test_proxy(book, ledger, settings, payload, client):
    resource, refused = _named(book, payload)
    if refused:
        return refused
    ok, exit_ip, why = _test(book, client, resource)
    was = book.proxies.status_of(resource)
    if ok:
        if was == book.proxies.dead_status:
            book.proxies.release(resource, note=(
                f"Answered again on {_stamp()} - tested from the web by "
                f"{_by(payload)}."))
        if exit_ip:
            book.proxies.record_exit(resource, exit_ip)
        return "done", f"{resource.name} answers (exit {exit_ip})", None
    if was in book.proxies.available_statuses:
        book.proxies.fail(resource, book.proxies.dead_status, note=(
            f"Did not answer on {_stamp()} - tested from the web by "
            f"{_by(payload)}: {why}"))
    return "failed", f"{resource.name} did not answer: {why}", None


def test_all_proxies(book, ledger, settings, payload, client):
    from . import builder

    if client is None:
        return "failed", "no GeeLark client on this pass", None
    dead, revived = builder.check_proxies(client, book)
    return ("done", f"tested every free and dead exit: {len(dead)} newly "
                    f"dead, {len(revived)} revived", None)


def remove_proxy(book, ledger, settings, payload, client):
    """Out of the pool. GeeLark's own copy is not touched - the delete
    endpoint is not in this program's contract yet - so a removed row
    reappears under "held by GeeLark, not in the pool" until it is
    removed there by hand; the sentence says so."""
    resource, refused = _named(book, payload)
    if refused:
        return refused
    status = book.proxies.status_of(resource)
    if status in (book.proxies.spent_status, book.proxies.claimed_status):
        return ("refused", f"{resource.name} is {status} - a phone is behind "
                           f"it", None)
    book.proxies.delete_row(resource)
    return ("done", f"{resource.name} removed from the pool (GeeLark still "
                    f"holds it - remove it there by hand)", None)


# ------------------------------------------------------- the phones (C6)
def login_accounts(book, ledger, settings, payload, client, launch=None):
    """"Log in selected": N chosen accounts onto N warm phones, at once.

    The person chose the accounts, so each is claimed by name - `claim_this`
    - and paired with the next warm phone; the pairs become finish jobs the
    pass launches together. An account with no warm phone left is said so
    and left free: the Keeper builds the shortfall, and the person presses
    the button again. Nothing here waits: the sentence says what started.
    """
    from . import builder

    addresses = [a.strip() for a in payload.get("addresses") or [] if a.strip()]
    if not addresses:
        return "refused", "no account was chosen", None
    if client is None or launch is None:
        return "failed", "this pass cannot start phone work", None
    warm, _gone = builder._unfinished(client, book)
    jobs, started, unpaired, refused = [], [], [], []
    for address in addresses:
        resource = book.apps.find(address)
        if resource is None:
            refused.append(f"{address}: not in the Gpt Info tab")
            continue
        status = book.apps.status_of(resource)
        if resource.error or status not in book.apps.available_statuses:
            refused.append(f"{address}: {resource.error or status}")
            continue
        if not warm:
            unpaired.append(address)
            continue
        phone = warm.pop(0)
        if not book.apps.claim_this(resource, str(phone["serial"])):
            refused.append(f"{address}: taken by another run meanwhile")
            warm.insert(0, phone)
            continue
        jobs.append({"kind": "finish",
                     "phone": {**phone, "account": resource}})
        started.append(f"{address} -> {phone['serial']}")
    if jobs:
        launch(jobs)
    bits = []
    if started:
        bits.append(f"logging in {len(started)} account(s) in parallel: "
                    + ", ".join(started))
    if unpaired:
        bits.append(f"{len(unpaired)} left free - no warm phone for them "
                    f"yet; the keeper is building, press again later")
    if refused:
        bits.append(f"{len(refused)} refused")
    # `running`, not `done`: the phones are booting. The launcher settles
    # the row with what became of each when they end (serve._settle_action).
    status = "running" if started else "failed"
    return status, "; ".join(bits), {
        "phones": [{"serial": str(j["phone"]["serial"]),
                    "account": j["phone"]["account"].label,
                    "status": "booting", "ok": None} for j in jobs],
        "unpaired": unpaired, "refused": refused}


login_accounts.needs_launch = True


def stop_phone(book, ledger, settings, payload, client):
    """"Stop this one": the job on one phone gives up at its next step,
    the way an interrupt would, and puts back what it held."""
    from . import builder

    serial = str(payload.get("serial") or "").strip()
    if not serial:
        return "refused", "no phone named", None
    builder.STOP_BY_HAND.add(serial)
    return ("done", f"phone {serial} stops at its next step; whatever it "
                    f"held goes back to its pool", None)


def change_proxy(book, ledger, settings, payload, client):
    """Put a phone on a different exit: the next free one from the pool.

    The phone is stopped first - Android reads the proxy when the network
    comes up, and GeeLark refuses the update on a phone that is starting -
    then GeeLark is told, and only after it agreed are the two rows moved:
    the old exit back to free, the new one spent on this serial. A phone a
    run holds right now is refused; a run swapping exits underneath a
    build is the one thing worse than a bad exit.
    """
    from . import phones as phones_mod
    from .phones import PhoneError

    serial = str(payload.get("serial") or "").strip()
    row = next((r for r in book.phones.rows()
                if str(r.get("Serial") or "").strip() == serial), None)
    if row is None:
        return "failed", f"phone {serial or '?'} is not in the Phones tab", None
    if row.get("Status") == book.phones.BUILDING:
        return "refused", f"phone {serial} is being worked on right now", None
    if client is None:
        return "failed", "no GeeLark client on this pass", None
    live = next((p for p in phones_mod.listing(client)
                 if str(p.get("serialNo")) == serial), None)
    if live is None:
        return "failed", f"phone {serial} is not in GeeLark's list", None
    held = ledger.get(live["id"]) if ledger is not None else None
    if held is not None and held.is_claimed and not held.is_stale:
        return "refused", f"phone {serial} is held by a run ({held.label})", None
    fresh = book.proxies.claim(serial)
    if fresh is None or fresh.proxy is None:
        return "failed", "the Proxy tab has no free exit left", None
    try:
        if live.get("status") in (phones_mod.RUNNING, phones_mod.STARTING):
            phones_mod.stop(client, live["id"])
            phones_mod.wait_until_stopped(client, live["id"])
        phones_mod.set_proxy(client, live["id"], fresh.proxy)
    except (PhoneError, ApiError) as exc:
        log.warning("phone %s kept its exit: %s", serial, exc)
        book.proxies.release(fresh, note=(
            f"Phone {serial} would not take it on {_stamp()}: "
            f"{str(exc)[:120]}"))
        return "failed", f"GeeLark refused the change: {str(exc)[:160]}", None
    old = book.proxies.find_by_name((row.get("Proxy") or "").strip())
    if old is not None and old is not fresh:
        book.proxies.release(old, note=(
            f"Left phone {serial} on {_stamp()} - proxy changed from the "
            f"web by {_by(payload)}."))
    book.proxies.spend(fresh, serial=serial, note=(
        f"On phone {serial} since {_stamp()} - changed from the web by "
        f"{_by(payload)}."))
    name = fresh.name or str(fresh.proxy)
    book.phones.write(serial, Proxy=name)
    return ("done", f"phone {serial} is on {name} now (it is stopped; it "
                    f"reads the new exit when it next starts)",
            {"was": (row.get("Proxy") or "").strip(), "now": name})


VERBS = {
    "login_accounts": login_accounts,
    "change_proxy": change_proxy,
    "stop_phone": stop_phone,
    "add_gmails": add_gmails,
    "add_gpt": add_gpt,
    "add_proxies": add_proxies,
    "adopt_proxy": adopt_proxy,
    "offer_again": offer_again,
    "mark_proxy_free": mark_proxy_free,
    "test_proxy": test_proxy,
    "test_all_proxies": test_all_proxies,
    "remove_proxy": remove_proxy,
}
