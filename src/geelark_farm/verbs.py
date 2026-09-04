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
    return _summary("gmail", added, skipped, refused, settings, _by(payload))


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
    return _summary("account", added, skipped, refused, settings,
                    _by(payload))


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
    return _summary("proxy", added, skipped, refused, settings, _by(payload))


def adopt_proxy(book, ledger, settings, payload, client):
    """An exit GeeLark holds that the tab never heard of, taken in."""
    raw = ":".join(p for p in (payload.get("host", ""), payload.get("port", ""),
                               payload.get("username", ""),
                               payload.get("password", "")) if p)
    return add_proxies(book, ledger, settings,
                       dict(payload, rows=[{"raw": raw, "name": ""}]), client)


_PLURAL = {"proxy": "proxies"}


def _summary(what: str, added, skipped, refused, settings=None, by=""):
    many = _PLURAL.get(what, what + "s")
    bits = [f"{len(added)} {what if len(added) == 1 else many} added"]
    if skipped:
        bits.append(f"{len(skipped)} already in the pool")
    if refused:
        bits.append(f"{len(refused)} refused")
    status = "done" if added or (not refused and skipped) else "failed"
    said = ", ".join(bits)
    if added and settings is not None and getattr(settings, "store_enabled",
                                                   False):
        # Stock arriving is an event (C8): the Events page's `stock` filter
        # and the gmail-burn forecast both read it.
        from .store import events as store_events

        store_events.emit(settings, "stock", status=what,
                          detail=f"{said} by {by or 'the web'}")
    return status, said, {"added": added, "skipped": skipped,
                          "refused": refused}


# --------------------------------------------------------------- accounts
def offer_again(book, ledger, settings, payload, client):
    """Blank a set-aside account's status - the web's spelling of "clear
    the cell", with the person's name in the note."""
    address = (payload.get("address") or "").strip()
    pool = book.gmails if payload.get("kind") == "gmail" else book.apps
    resource = pool.find(address)
    if resource is None:
        return "failed", f"{address} is not in the {pool.tab} tab", None
    status = pool.status_of(resource)
    settled = set(pool.available_statuses) | {
        pool.claimed_status, pool.spent_status, pool.retired_status}
    if status in settled:
        return ("refused", f"{address} is {status or 'free'}, not set "
                           f"aside - nothing to offer again", None)
    pool.release(resource, note=(
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
    _stamp_test(settings, resource.name, True, exit_ip)
    return "done", f"{resource.name} is free again (exit {exit_ip})", None


def test_proxy(book, ledger, settings, payload, client):
    resource, refused = _named(book, payload)
    if refused:
        return refused
    ok, exit_ip, why = _test(book, client, resource)
    was = book.proxies.status_of(resource)
    _stamp_test(settings, resource.name, ok, exit_ip)
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
    kept = {"name": resource.name,
            "raw": (resource.values.get("Proxy String") or str(resource.proxy)),
            "status": status, "note": resource.values.get("Note", "")}
    book.proxies.delete_row(resource)
    return ("done", f"{resource.name} removed from the pool (GeeLark still "
                    f"holds it - remove it there by hand)", {"removed": kept})


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


def _gmail_row(book, payload):
    """The row this command names, or the refusal that says why not.

    A row a phone is behind is not stock to edit or tidy away: the build
    signed into it minutes ago and the sheet is what it will be read from
    again.
    """
    address = (payload.get("address") or "").strip()
    resource = book.gmails.find(address)
    if resource is None:
        return None, ("failed", f"{address or '?'} is not in the "
                                f"{book.gmails.tab} tab", None)
    status = book.gmails.status_of(resource)
    if status in (book.gmails.claimed_status, book.gmails.spent_status):
        return None, ("refused", f"{address} is {status} - a phone is behind "
                                 f"it", None)
    return resource, None


def edit_gmail(book, ledger, settings, payload, client):
    """The row editor: whatever cells the person changed, written back.

    Judged the way a pasted row is judged, before anything is written - a
    secret that is neither a base32 key nor an address, or an address that
    is not one, is refused here rather than discovered on a phone. Blank
    means blank on purpose: an account with no second factor is a real
    thing this pool has always carried.
    """
    from .accounts import AccountError, Credentials, normalize_totp_secret

    resource, refused = _gmail_row(book, payload)
    if refused:
        return refused
    was = dict(resource.values)
    secret = str(payload.get("secret") or "").strip()
    recovery = secret if "@" in secret else ""
    address = str(payload.get("new_address") or "").strip() or str(
        payload.get("address") or "").strip()
    try:
        Credentials(
            email=address,
            password=str(payload.get("password") or ""),
            totp_secret="" if recovery else normalize_totp_secret(secret),
            recovery_email=recovery,
        ).validate(what="gmail:")
    except AccountError as exc:
        return "refused", str(exc), None
    cells = {"Address": address,
             "Password": str(payload.get("password") or ""),
             book.gmails.SECRET_COLUMN: secret,
             "Seller": str(payload.get("seller") or "").strip()}
    purchased = str(payload.get("purchased") or "").strip()
    if purchased:
        cells["Purchase Date"] = purchased
    # Written, then read back by the tab's own rule - which knows things
    # `Credentials` does not, like a Seller that promises a recovery
    # address. A row that will not read back is put straight back the way
    # it was: half an edit is a row nothing can claim.
    problem = book.gmails.edit_cells(resource, **cells)
    if problem:
        book.gmails.edit_cells(resource, **{name: str(was.get(name, ""))
                                            for name in cells})
        return "refused", problem, None
    changed = [name for name, value in cells.items()
               if str(was.get(name, "")) != value]
    return ("done", f"{address} edited by {_by(payload)}"
                    + (f" ({', '.join(changed)})" if changed
                       else " - nothing was different"),
            {"changed": changed})


def remove_gmail(book, ledger, settings, payload, client):
    """Out of the pool. The row it removed rides in the detail, so
    Requests can put it back the way a removed proxy can."""
    resource, refused = _gmail_row(book, payload)
    if refused:
        return refused
    address = str(resource.values.get("Address") or "")
    kept = {name: str(resource.values.get(name) or "")
            for name in ("Address", "Password", book.gmails.SECRET_COLUMN,
                         "Seller", "Purchase Date")}
    book.gmails.delete_row(resource)
    return ("done", f"{address} removed from the pool by {_by(payload)}",
            {"removed": kept})


def _panel_row(settings, ref: str):
    """The row the panel named, read from the store.

    The credentials are fetched here rather than carried in the request:
    a request is rendered on a page, and a password in a payload is a
    password on a page - which is true of the console's own add today and
    is not a thing to copy.
    """
    if settings is None or not getattr(settings, "store_enabled", False):
        return None
    from .store import db as store_db

    with store_db.connect(settings) as conn:
        rows = conn.execute(
            "SELECT address, password, totp_secret, email_code_only"
            " FROM resources WHERE kind = 'app' AND panel_ref = %s",
            (ref,)).fetchall()
    if not rows:
        return None
    address, password, secret, code_only = rows[0]
    return {"address": address or "", "password": password or "",
            "secret": secret or "", "email_code_only": bool(code_only)}


def _panel_broke(settings, ref: str, why: str) -> None:
    """Say on the row itself that it never reached the tab, so the panel
    reads `invalid` with the reason rather than a queue that never moves.

    Safe to write `error` here precisely because this row has no sheet
    twin - the append is what would have made one. Once it does have one
    the mirror owns that column again, which is correct: the sheet's
    verdict is the one that matters then.
    """
    from .store import db as store_db

    try:
        with store_db.connect(settings) as conn:
            conn.execute("UPDATE resources SET error = %s, updated_at = now()"
                         " WHERE kind = 'app' AND panel_ref = %s", (why, ref))
            conn.commit()
    except Exception as exc:                                      # noqa: BLE001
        log.warning("panel account %s: the refusal was not written (%s)",
                    ref, exc)


def add_panel_account(book, ledger, settings, payload, client):
    """The panel's account, from the store into the tab the keeper reads.

    The API already wrote the row - it had to, so that a GET straight
    after the POST finds it - and this is the half only a pass may do.
    The next mirror pass then recognises the row by its address and fills
    in the sheet_row, keeping the id and every column the API owns.
    """
    ref = str(payload.get("ref") or "").strip()
    row = _panel_row(settings, ref)
    if row is None:
        return "failed", f"{ref or '?'} is not a row in the store", None
    if book.apps.find(row["address"]) is not None:
        # Already in the tab: the mirror will adopt the store row on its
        # next pass, so this is done, not failed.
        return ("done", f"{row['address']} was already in the "
                        f"{book.apps.tab} tab", {"ref": ref})
    book.apps.append(**{
        "Address": row["address"], "Password": row["password"],
        "2FA Secret": row["secret"], "Status": "",
        "Email code": "TRUE" if row["email_code_only"] else "FALSE",
        "Note": f"From the customer panel ({ref}) on {_stamp()}."})
    return ("done", f"{row['address']} added to the {book.apps.tab} tab "
                    f"for {ref}", {"ref": ref, "address": row["address"]})


def withdraw_panel_account(book, ledger, settings, payload, client):
    """Taken back. Out of the tab if it reached it, and left in the store
    stamped withdrawn - the panel keeps its history of what it sent."""
    ref = str(payload.get("ref") or "").strip()
    row = _panel_row(settings, ref)
    if row is None:
        return "failed", f"{ref or '?'} is not a row in the store", None
    resource = book.apps.find(row["address"])
    if resource is None:
        return "done", f"{ref} was not in the tab; nothing to take out", None
    status = book.apps.status_of(resource)
    if status in (book.apps.claimed_status, book.apps.spent_status):
        return ("refused", f"{row['address']} is {status} - a phone is "
                           f"behind it", None)
    book.apps.delete_row(resource)
    return ("done", f"{row['address']} taken out of the {book.apps.tab} tab",
            {"ref": ref})


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


# ------------------------------------------------ the service (controls)
_CONTROL = {
    "pause": ("tick", "Pause building", "building pauses at the next pass"),
    "resume": ("untick", "Pause building", "building resumes at the next pass"),
    "clear_breaker": ("tick", "Clear breaker",
                      "the breaker is cleared at the next pass"),
    "stop": ("tick", "Stop everything",
             "the service stops at the next pass - nothing synced, built or "
             "finished until it is started again"),
    "start": ("untick", "Stop everything", "the service starts again"),
    "stop_unaccounted": ("tick", "Stop unaccounted phones",
                         "phones nothing accounts for are stopped at the "
                         "next quiet pass"),
}


def control(book, ledger, settings, payload, client):
    """The Service tab's checkboxes, pressed from the web. Ticking is
    all this does: the pass reads the tick at the top of its next turn
    exactly as it reads a hand's, so the sheet and the web cannot
    disagree about what was asked. Drained above the Stop check, so
    "start" works while the service is stopped."""
    what = str(payload.get("what") or "").strip()
    plan = _CONTROL.get(what)
    if plan is None:
        return "refused", f"{what or '?'} is not a service control", None
    board = getattr(book, "service", None)
    if board is None:
        return "failed", "the sheet has no Service tab to tick", None
    move, name, said = plan
    if move == "tick":
        if not board.tick(name):
            return "failed", f"could not tick {name} on the Service tab", None
    else:
        board.taken(name)
    return ("done", f"{name} {'ticked' if move == 'tick' else 'unticked'} "
                    f"by {_by(payload)}: {said}",
            {"control": name, "move": move})


# --------------------------------------------------------- phones by hand
def _stamp_owner(settings, serial: str, by_id) -> None:
    """Who is holding the phone, written into the mirror - the sheet has
    no column for it. Never fatal: the State cell is the record, this is
    only the name beside it."""
    if settings is None or not getattr(settings, "store_enabled", False):
        return
    try:
        from .store import db as store_db

        with store_db.connect(settings) as conn:
            conn.execute(
                "UPDATE phones SET owner_id = %s, updated_at = now()"
                " WHERE serial = %s AND done_at IS NULL", (by_id, serial))
            conn.commit()
    except Exception as exc:                                      # noqa: BLE001
        log.warning("phone %s: owner not stamped (%s)", serial, exc)


def boot_phone(book, ledger, settings, payload, client):
    """"Boot": start the phone in GeeLark and take it, in one press.

    The live-view URL exists only as the answer to /phone/start - GeeLark
    has no endpoint that hands one out for a phone already running - so
    starting it is what produces the link, and the link rides in this
    action's detail for the page waiting on it.

    Starting a phone bills it, and somebody watching a screen is holding
    that phone, so this writes State=taken in the same breath: the sync
    then leaves it alone until they say Done, Failed or Release.
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
    try:
        # One attempt, not the builder's four: a capacity refusal here
        # would sleep half a minute inside the drain, and the person is
        # watching a tab that can simply be pressed again.
        url = phones_mod.start(client, live["id"], attempts=1)
    except phones_mod.PhoneCapacityError:
        return ("failed", f"GeeLark has no machine free for {serial} right "
                          f"now - press Boot again in a minute", None)
    except (PhoneError, ApiError) as exc:
        return "failed", f"phone {serial} would not start: {exc}", None
    book.phones.write(serial, State="taken")
    _stamp_owner(settings, serial, payload.get("by_id"))
    if not url:
        return ("done", f"phone {serial} started and taken by "
                        f"{_by(payload)} - GeeLark gave no live-view link "
                        f"back", {"state": "taken"})
    return ("done", f"phone {serial} started and taken by {_by(payload)}",
            {"state": "taken", "url": url})


def set_phone_state(book, ledger, settings, payload, client):
    """Write the State cell - taken / done / failed / (blank) - the way a
    hand does in the sheet; the sync carries it out on the next pass.
    A phone somebody takes is stamped with who took it in the mirror."""
    serial = str(payload.get("serial") or "").strip()
    state = str(payload.get("state") or "").strip().lower()
    if state not in ("taken", "done", "failed", "", "unused"):
        return "refused", f"{state!r} is not a State word", None
    row = next((r for r in book.phones.rows()
                if str(r.get("Serial") or "").strip() == serial), None)
    if row is None:
        return "failed", f"phone {serial or '?'} is not in the Phones tab", None
    if row.get("Status") == book.phones.BUILDING and state in ("done", "failed"):
        return "refused", f"phone {serial} is being worked on right now", None
    word = "" if state == "unused" else state
    book.phones.write(serial, State=word)
    _stamp_owner(settings, serial,
                 payload.get("by_id") if word == "taken" else None)
    meaning = {"taken": "out with somebody - the sync leaves it alone",
               "done": "the sync deletes the phone and retires what was on it",
               "failed": "the sync deletes the phone and frees its account",
               "": "back on the shelf"}[word]
    return ("done", f"phone {serial} marked {word or 'unused'} by "
                    f"{_by(payload)}: {meaning}", {"state": word})


def clear_tries(book, ledger, settings, payload, client):
    """A given-up phone back in the queue: the Tries cell blanked, the
    way the runbook says to do it by hand."""
    serial = str(payload.get("serial") or "").strip()
    if not book.phones.write(serial, **{book.phones.TRIES_COLUMN: ""}):
        return "failed", f"phone {serial or '?'} is not in the Phones tab", None
    return ("done", f"phone {serial}: tries cleared by {_by(payload)} - it is "
                    f"offered to the keeper again", None)


def ignore_proxy(book, ledger, settings, payload, client):
    """Stop reporting one exit GeeLark holds that the tab never heard of.
    Kept in service_state, so it is a list a person can read and undo."""
    who = ":".join(str(payload.get(k) or "") for k in ("host", "port", "username"))
    if settings is None or not getattr(settings, "store_enabled", False):
        return "failed", "no store to remember it in", None
    from .store import db as store_db
    from .store import state as store_state

    kept = list(store_state.get(settings, "ignored_proxies", []) or [])
    if who not in kept:
        kept.append(who)
    with store_db.connect(settings) as conn:
        store_state.put(conn, "ignored_proxies", kept)
        conn.commit()
    return "done", f"{who} is ignored - it stays in GeeLark, unreported", None


def _stamp_test(settings, name: str, ok: bool, exit_ip: str) -> None:
    """Remember when an exit was last tested and how it answered, for the
    Proxy Pool's "last test" column. Never fatal."""
    if settings is None or not getattr(settings, "store_enabled", False):
        return
    try:
        from .store import db as store_db
        from .store import state as store_state

        tests = dict(store_state.get(settings, "proxy_tests", {}) or {})
        tests[name] = {"at": time.time(), "ok": ok, "exit": exit_ip}
        with store_db.connect(settings) as conn:
            store_state.put(conn, "proxy_tests", tests)
            conn.commit()
    except Exception as exc:                                      # noqa: BLE001
        log.debug("proxy test stamp for %s not kept (%s)", name, exc)


VERBS = {
    "login_accounts": login_accounts,
    "control": control,
    "set_phone_state": set_phone_state,
    "boot_phone": boot_phone,
    "clear_tries": clear_tries,
    "ignore_proxy": ignore_proxy,
    "change_proxy": change_proxy,
    "stop_phone": stop_phone,
    "add_gmails": add_gmails,
    "edit_gmail": edit_gmail,
    "remove_gmail": remove_gmail,
    "add_gpt": add_gpt,
    "add_panel_account": add_panel_account,
    "withdraw_panel_account": withdraw_panel_account,
    "add_proxies": add_proxies,
    "adopt_proxy": adopt_proxy,
    "offer_again": offer_again,
    "mark_proxy_free": mark_proxy_free,
    "test_proxy": test_proxy,
    "test_all_proxies": test_all_proxies,
    "remove_proxy": remove_proxy,
}
