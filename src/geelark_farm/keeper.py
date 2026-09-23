"""The keeper's reconciliation: bring the Phones, Gmails, Proxies and Gpt tabs
back into agreement with what GeeLark and the store say, carry out the
State words a person wrote, settle what a dead run left behind, and check
the exits.

Moved out of builder.py unchanged (the builder review, 2026-09-23). It is
a fifth of that file, the keeper container is the only one that runs it,
and the build path never called any of it. It logs under the builder's
logger name, as it always has, so the console's words for its lines stay
the same.

It never imports the builder: what it shares with a build - the device
helpers, the exit policy, the pools' words - lives in phones,
exit_health and pools.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import artifacts as archive
from . import failures, phones
from . import proxy as proxy_mod
from .api import ApiError, Client, TransportError
from .config import Settings
from .exit_health import HELD_BACK, gate_hosts
from .gsheet import SheetError
from .ledger import Ledger
from .phones import FARM_GROUP, _in_the_farms_group, _live_exits
from .pools import Book, PhoneLog, Pool, Resource

log = logging.getLogger("geelark_farm.builder")

#: The Phones tab's word for a phone with the app and no account.
APP_ONLY = PhoneLog.APP_ONLY


def _account_on(book: Book, serial: str, named: str) -> Resource | None:
    """The app account this phone is carrying, asking both records.

    The Phones row's `GPT Account` cell is the first answer and the usual
    one. When it is blank the Gpt Info tab is asked whether any row is still
    standing on this phone, because that tab keeps its own serial - and the
    two disagreed once. Phone 1542 was signed in at 23:13 and marked `done`
    three hours later with that cell empty, so the delivery went unrecorded:
    the account was neither delivered nor freed, and sat holding a phone that
    no longer existed while every pass warned about it (2026-09-01).

    Only a row the pool calls spent counts. A `delivered` row has been
    settled already, and a blank one is stock that happens to remember the
    serial it was last on - taking either would be this inventing a delivery
    rather than finding one.
    """
    if named:
        return book.apps.find(named)
    column = book.apps.serial_column
    if not column:
        return None
    wanted = str(serial).strip()
    for resource in book.apps._rows:
        if (resource.values.get(column) or "").strip() != wanted:
            continue
        if book.apps.status_of(resource) == book.apps.spent_status:
            return resource
    return None


def _settle_before_deleting(client: Client, phone_id: str, serial: str,
                            timeout: float = 90) -> bool:
    """Stop a phone and wait for it to say so. False if it will not.

    GeeLark will not delete a running phone, and `stop` only posts the request -
    the phone goes on reporting as running while it shuts down. Deleting into
    that window fails, so this waits for the state to settle rather than
    guessing at a sleep.

    A phone that will not stop keeps its row: the next sync finds it again, and
    a row still there is a better outcome than a delete that half worked.
    """
    log.info("phone %s is marked done and still running; stopping it so it "
             "can be deleted", serial)
    try:
        phones.stop(client, phone_id)
    except Exception as exc:                                      # noqa: BLE001
        log.error("could not stop phone %s (%s); its row is kept", serial, exc)
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if phones.status(client, phone_id) == phones.STOPPED:
                return True
        except Exception as exc:                                  # noqa: BLE001
            log.error("phone %s stopped answering (%s)", serial, exc)
            return False
        time.sleep(5)
    log.warning("phone %s has not reported stopped within %.0fs; its row is "
                "kept for the next run", serial, timeout)
    return False


def apply_phone_states(client: Client, book: Book, ledger: Ledger,
                       settings: Settings) -> dict[str, list[str]]:
    """Carry out what the operator wrote in the Phones tab's `State` column.

    `Status` is what a run concluded about a phone. `State` is the other
    direction - an instruction back to the tool, written by hand between runs:

        done     finished with it. Delete the phone and drop the row.
        failed   something is wrong with it. Free its app account so a new
                 phone can use it, then delete the phone and drop the row.
        unused   the default. Leave it alone.

    A running phone is never touched, only reported. Deleting one is not a
    documented way to end its billing, and stopping it to make deletion safe is
    not this function's business.

    What happens to the credentials the phone carried follows from which of the
    two it was:

        Gmail        retired as `used` either way. It signed into that phone,
                     and that is the credit it had to spend.
        app account  `delivered` after `done` - the phone was the product and
                     it went out on it. Freed after `failed` - it never got a
                     fair device, so the next build can put it on one.

    Neither is ever left pointing at the deleted phone. A stale serial is how
    thirteen proxies sat out of the pool for days without anyone noticing.
    """
    from .store import person

    marked = person.marked(settings)
    if not marked:
        return {}

    alive = {str(p.get("serialNo")): p for p in phones.listing(client)}
    outcome: dict[str, list[str]] = {"deleted": [], "freed": [],
                                     "delivered": [], "retired": [],
                                     "held": [], "running": []}
    finished_rows: list[int] = []

    for row in marked:
        serial = row["serial"]
        present = alive.get(str(serial))
        held = present and ledger.get(present["id"])
        if held is not None and held.is_claimed and not held.is_stale:
            # A run is working on it. That is the only reason to refuse: the
            # power state is not, because a phone left up by a browser tab is
            # nobody's, and `done` on it still means delete.
            outcome["held"].append(serial)
            continue
        if present and present.get("status") in (phones.RUNNING, phones.STARTING):
            # Stopped first, because a running phone cannot be deleted - and
            # this used to stop there and report it, which left `done` half
            # carried out and the row sitting in the tab until someone noticed,
            # closed the viewer and ran the sync again (2026-08-16, 749 and 751).
            if not _settle_before_deleting(client, present["id"], serial):
                outcome["running"].append(serial)
                continue

        failed = row["state"] == book.phones.FAILED

        # The Gmail is spent either way. It signed into that phone without
        # complaint, and whatever became of the phone afterwards, the address
        # has been on one - so it retires rather than going back on the shelf.
        if row["gmail"]:
            address = book.gmails.find(row["gmail"])
            if address is not None:
                book.gmails.retire(
                    address, note=f"Signed into phone {serial}, which was "
                                  f"marked {row['state']} and deleted. Kept "
                                  f"out of the pool from now on.")
                outcome["retired"].append(row["gmail"])

        account = _account_on(book, serial, row["app_account"])
        carried = account.label if account is not None else ""
        if account is not None:
            if failed:
                # It never got a fair phone. Back to the pool, so the next
                # build can put it on one that works.
                #
                # A Spotify row comes back as the `error` kind, whichever
                # kind it went out as. The kinds say which phone the
                # account wants - `normal` one with no Google account,
                # `error` one that has a Gmail - and a phone marked
                # failed is a person saying the phone this account had
                # did not work out, so the next build gives it the other
                # sort (the operator, 2026-09-19). A row that was already
                # `error` asks for the same sort again, which is the same
                # rule and needs no branch of its own.
                kind = book.apps.kind_of(account)
                wants = book.apps.kind_after_a_failed_phone(account)
                if not wants:
                    # No pair of kinds to move between - a GPT row, or a
                    # product whose kind says nothing about the phone.
                    kind = ""
                if not kind:
                    note = (f"Phone {serial} was marked failed and "
                            f"deleted before this account got a fair "
                            f"run. Free to try on another phone.")
                elif kind == "error":
                    note = (f"Phone {serial} was marked failed and "
                            f"deleted. Back in the pool as an `error` "
                            f"account, for another phone with a Gmail "
                            f"on it.")
                else:
                    note = (f"Phone {serial} was marked failed and "
                            f"deleted. It went out as a `{kind}` account "
                            f"and comes back as an `error` one, so the "
                            f"next build gives it a phone with a Gmail "
                            f"on it.")
                if kind:
                    # Before the release, not after: the row is still
                    # claimed here, so it is never on the shelf reading
                    # the kind it has just stopped being.
                    book.apps.set_kind(account, wants)
                book.apps.release(account, phone_failed=True, note=note)
                outcome["freed"].append(carried)
            else:
                # `done` means the phone was the product and it has been
                # handed over. The account went with it.
                book.apps.retire(
                    account, note=f"Delivered on phone {serial}, which was marked "
                                  f"done and handed over.")
                outcome["delivered"].append(carried)

        if present:
            try:
                phones.delete(client, [present["id"]], ledger=ledger)
                outcome["deleted"].append(serial)
            except Exception as exc:                              # noqa: BLE001
                log.error("could not delete phone %s (%s); its row is kept",
                          serial, exc)
                continue
        finished_rows.append(row["sheet_row"])
        # Three notes, not two. The pair branched on `failed` alone and said
        # "its app account was delivered with it" whichever way - including for
        # a phone that never had one, which is a whole product: the app is
        # installed and somebody signs a customer's own account in by hand.
        # History is the only durable record once the row is deleted, so it was
        # the one place that claimed a farm account went out with every such
        # hand-over (2026-08-29).
        if failed:
            note = ("Marked failed and deleted; its app account went back to "
                    "the pool for another phone.")
        elif carried:
            note = "Marked done and deleted; its app account was delivered with it."
        else:
            note = ("Marked done and deleted. No app account was ever on it - "
                    "the app was installed and whoever took it signs in "
                    "themselves.")
        book.record_history(
            Serial=serial, Event=row["state"], Gmail=row["gmail"], Note=note,
            **{"GPT Account": carried})

    # Only now, and bottom up: the row numbers were read before any moved.
    book.phones.delete_rows(finished_rows)
    for label, items in outcome.items():
        if items:
            log.info("%s: %s", label, ", ".join(items))
    return outcome


#: What each step is doing, for a caller that shows progress while it waits.
#: The whole sync takes half a minute or more and used to be one unchanging
#: line with every INFO record the steps emit scrolling through it.
STEP_NAMES = {
    "marks": "carrying out the State column",
    "abandoned": "settling phones a killed run left behind",
    "proxies": "matching the Proxy tab to the panel",
    "repointed": "checking which exit each phone is really on",
    "renamed": "naming the phones in GeeLark",
    "stranded": "looking for phones and accounts that lost each other",
    "unclaimed": "putting back what a dead run was holding",
    "pruned": "clearing out archived pages nothing needs",
    "checked": "testing every free proxy",
    "retried": "putting back the Gmails whose wait on the ladder is over",
    "hosts": "setting aside exits on hosts that sign in rarely",
}


def sync_sheet(client: Client, book: Book, ledger: Ledger, *,
               settings: Settings,
               apply_marks: bool = True,
               probe_proxies: bool = True,
               on_step: Callable[[str], None] | None = None,
               artifact_dir: Path | None = None,
               stale_claim_seconds: float | None = None,
               ) -> dict[str, list[str]]:
    """Bring all four tabs back into agreement with the world. Every run.

    The pieces existed and were called in a different combination from each of
    three places - `build` did three of them, `finish` did two, the console did
    one - so what a tab said depended on which door you came in by. This is the
    one door.

    The order is not arrangeable:

    1. **Gmails, then Gpt Info, then Phones.** Acting on the State column
       deletes phones, and the credentials a phone carried are named on its
       row - so they have to be settled while the row still exists. A Gmail is
       retired either way, since it signed into that phone whatever became of
       it. An app account is `delivered` if the phone was marked done and freed
       if it was marked failed, because a failed phone never gave it a fair
       device. That is `apply_phone_states`, and its internal order is this.
    2. **Reload.** The rows moved.
    3. **Proxy.** After the deletions, so the exits those phones held are seen
       to be free rather than freed a run later.
    4. **Test what is free.** Last, because steps 1-3 are what decide which
       proxies are free to test.

    Two switches, for the two halves that are not alike:

    `apply_marks` is the half that deletes phones. It is the only irreversible
    thing here, so a caller has to ask for it. `geelark pools` does not - it is
    a report, and a report that deletes six phones because a column said so is
    not one. A run does, and the console does after showing what it will do.

    `probe_proxies` is the part that costs real time - a live connection per
    free proxy - so a caller that only wants the tabs tidied can leave it out.
    Named for the switch rather than the function it turns on, because those
    were the same word for one commit: the parameter shadowed `check_proxies`
    inside this body, and calling it raised `'bool' object is not callable` on
    the first line of every console session (2026-08-14).
    """
    # The dropdowns come from the same table the build consults, so they cannot
    # be right by accident and cannot stay right on their own: a flow grew
    # `wrong_2fa_code` and the Gpt Info column went on refusing it - "Input
    # must fall within specified range" against a status a run had just
    # written (2026-08-16). Regenerated every session, and it writes only when
    # something actually moved.
    try:
        book.sync_lists()
    except SheetError as exc:
        log.warning("could not refresh the Status dropdowns: %s", exc)

    # Each step guarded on its own. A sheet error partway through - the write
    # quota exhausted by a big sync is the one seen in practice - must not
    # discard the steps that already ran: by the time the proxy check writes,
    # the phones are already deleted and the credentials already settled, and
    # unwinding out of the whole sync would leave the console unable to open
    # while reporting none of what it did (2026-08-17).
    def step(name: str, work) -> None:
        if on_step:
            on_step(STEP_NAMES.get(name, name))
        try:
            result = work()
            if isinstance(result, dict):
                outcome.update(result)
            elif result is not None:
                outcome[name] = result
        except Exception as exc:                                  # noqa: BLE001
            # Every step here also talks to GeeLark, and `ApiError`,
            # `TransportError` and `PhoneError` are none of them a
            # `SheetError`. Catching only that left a GeeLark hiccup partway
            # through unwinding the whole sync - the console unable to open,
            # and reporting none of the work that had already been done, which
            # is the exact outcome this guard was added to prevent.
            log.error("sync step %r stopped short: %s", name, exc)
            outcome.setdefault("incomplete", []).append(name)

    outcome: dict[str, list[str]] = {}
    if apply_marks:
        step("marks",
             lambda: apply_phone_states(client, book, ledger, settings))
    # Before the reload, because both read the Phones tab and this one is what
    # frees a row the last run died holding.
    step("abandoned", lambda: settle_abandoned(
        client, book, ledger, busy=_busy_serials(settings)))
    book.reload()
    step("proxies", lambda: sync_proxies(
        client, book, ledger,
        skip_groups=phones.reap_scope(settings).get("skip_groups", ())))
    step("repointed", lambda: sync_phone_proxies(client, book))
    step("renamed", lambda: sync_phone_names(client, book))
    step("stranded", lambda: strand_check(client, book))
    if getattr(settings, "store_enabled", False) and getattr(
            settings, "pools_in_pg", False):
        step("retried", lambda: _revive_ladder(settings))
        step("hosts", lambda: gate_hosts(book, settings))
    if stale_claim_seconds:
        step("unclaimed", lambda: free_abandoned_claims(book,
                                                        stale_claim_seconds))
    if artifact_dir is not None:
        step("pruned", lambda: archive.prune(
            artifact_dir,
            {str(item.get("serialNo") or "")
             for item in phones.listing(client)}))
    if probe_proxies:
        def check():
            gone, back = check_proxies(client, book)
            return {"dead": [r.label for r in gone],
                    "revived": [r.label for r in back]}
        step("checked", check)
    return {key: items for key, items in outcome.items() if items}


def _busy_serials(settings: Settings | None) -> frozenset[str]:
    """The phones a queued or running job is about: a login the lane
    queued a minute ago that no builder has taken yet has its row marked
    `building` and no ledger claim, and read as a dead run - three
    phones were written off as "stopped short" at 01:57 while their
    logins started at 01:57:46 (2026-09-10). Never raises: a queue that
    cannot be read protects nothing, and the ledger still answers."""
    if settings is None or not (getattr(settings, "build_queue", False)
                                and getattr(settings, "store_enabled", False)):
        return frozenset()
    try:
        from .store import jobs as store_jobs

        return frozenset(store_jobs.open_serials(settings))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not read which phones the queue is holding (%s)",
                    exc)
        return frozenset()


def settle_abandoned(client: Client, book: Book, ledger: Ledger,
                     busy: frozenset[str] | set[str] = frozenset()
                     ) -> dict[str, list[str]]:
    """Close out rows a run was holding when it died.

    `busy` is the serials a queued or running job is about, from
    `_busy_serials`: a job in the queue is a process that believes it
    owns the phone as surely as a ledger claim is, and the claim only
    comes once a builder takes the job.

    `building` means "a run has this right now", which is why every other
    reader skips it - and nothing ever un-set it. A run killed mid-build leaves
    the row saying `building` forever: `unfinished` will not offer it to a
    finish, `marked` only sees the State column, and the phone sits in the
    panel behind a row nobody acts on (2026-08-14, phone 750, left there when a
    stuck boot was interrupted).

    Two things protect a run that is still working, and one of them was not
    enough. The phone being up is the obvious signal - and a phone stuck in
    `starting` reports as `stopped`, so a build patiently waiting for one to
    boot looked exactly like a dead run. This deleted phone 750 out from under
    a live build, which then failed with `env not found` twenty minutes later
    (2026-08-14). The ledger is the other: `claim` is written the moment a
    build takes a phone and cleared when it lets go, so an unreleased claim
    means a process believes it owns this.

    The ledger is the one that answers the question. Being up was treated as
    an answer too, and it is not: a run that lost its network died without
    stopping its phones, so they stayed running with nothing accountable for
    them - and a running phone is settled by nothing, offered to `finish` by
    nothing and deleted by nothing, so the rows sat on `building` for good.
    One that nothing claims is stopped here and then settled like any other.

    What the row becomes follows the rule a build would have applied itself: a
    phone with a Gmail on it is `incomplete` and can be finished; one with
    nothing signed in is deleted, because that is not a phone.
    """
    live = {str(p.get("serialNo")): p for p in phones.listing(client)}
    outcome: dict[str, list[str]] = {"abandoned": [], "discarded": []}
    dropped: list[int] = []

    for row in book.phones.rows():
        if (row.get("Status") or "").strip() != book.phones.BUILDING:
            continue
        serial = row.get("Serial") or ""
        if str(serial) in busy:
            log.info("a job in the queue has phone %s; leaving it alone",
                     serial)
            continue
        present = live.get(str(serial))
        held = present and ledger.get(present["id"])
        if held is not None and held.is_claimed and not held.is_stale:
            log.info("a run still claims phone %s (%s); leaving it alone",
                     serial, held.label)
            continue

        if present and present.get("status") in (phones.RUNNING,
                                                 phones.STARTING):
            # Running, and nothing is accountable for it. That used to be an
            # unconditional skip, which read as "someone is working on it" -
            # but the ledger above is what answers that, and it has already
            # said no. A run that died without its network never got to stop
            # its phones, so they stayed up; and a phone that is up is settled
            # by nothing, offered by nothing, and deleted by nothing, so its
            # row sat on `building` for good (2026-08-17, phones 838 and 839).
            #
            # Stopping is not housekeeping about cost here - it is the only
            # way the row can be settled at all, since GeeLark will not delete
            # a phone that is still running.
            log.info("phone %s is still running with nothing accountable for "
                     "it; stopping it so its row can be settled", serial)
            try:
                phones.stop(client, present["id"])
                phones.wait_until_stopped(client, present["id"])
            except Exception as exc:                              # noqa: BLE001
                log.error("could not stop abandoned phone %s (%s); its row "
                          "stays `building` until it comes down", serial, exc)
                continue

        # What History is told, either way. Seconds is deliberately left out:
        # the run that did the work died without reporting, and this sync has
        # no duration to offer - a nought there would read as "took no time"
        # rather than "nobody knows". Everything else is on the row.
        recorded = {"Serial": str(serial),
                    "Proxy": (row.get("Proxy") or "").strip(),
                    "Gmail": (row.get("Gmail") or "").strip(),
                    "GPT Account": (row.get("GPT Account") or "").strip()}

        if row.get("Gmail"):
            note = ("Stopped short: the run holding this phone ended before "
                    "it could say why. Google is signed in, so finishing it "
                    "costs only an app account.")
            book.phones.finish(row["sheet_row"], Status=APP_ONLY, Note=note)
            outcome["abandoned"].append(str(serial))
            # This wrote nothing to History at all, so a phone rescued from a
            # killed run left no trace of having been rescued - the tab said
            # `incomplete` and the record of how it got there was missing
            # (2026-08-20).
            book.record_history(Event=APP_ONLY, Note=note, **recorded)
            continue

        # Nothing was ever signed into it - the same rule build_one applies.
        if present:
            try:
                phones.delete(client, [present["id"]], ledger=ledger)
            except Exception as exc:                              # noqa: BLE001
                log.error("phone %s was abandoned with nothing on it and "
                          "could not be deleted (%s)", serial, exc)
                continue
        dropped.append(row["sheet_row"])
        outcome["discarded"].append(str(serial))
        book.record_history(
            Event="discarded",
            Note="A killed run left it mid-build with nothing signed in; "
                 "deleted at the next sync.", **recorded)

    book.phones.delete_rows(dropped)
    for label, items in outcome.items():
        if items:
            log.info("%s: %s", label, ", ".join(items))
    return outcome


def sync_proxies(client: Client, book: Book,
                 ledger: Ledger | None = None, *,
                 skip_groups: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Make the Proxy tab say what is actually behind each exit.

    Three ways the tab drifts, and it only ever fixed one of them:

    - a phone is deleted from the panel and its proxy stays `on a phone`
      forever. Thirteen of twenty-two were locked to phones that had been gone
      for days, and a run failed with no_usable_proxy while they sat there
      (2026-08-11). This is what `reclaim` was written for.
    - a phone is moved onto another exit mid-run and the old row keeps its
      serial, so one phone holds two proxies (2026-08-13, SX5 and SX18).
    - a row says `free`, or `dead`, with a live phone behind it. Nothing ever
      corrected that direction at all, and the second one is a contradiction:
      the phone is the side of it that demonstrably works.

    `claimed` rows are left alone. That word means a run holds it right now,
    and a second run tidying it away is how two phones end up on one exit.
    """
    live = _live_exits(client, skip_groups)
    changed: dict[str, list[str]] = {"attached": [], "released": [],
                                     "unlisted": []}

    # What GeeLark has been given but the tab has never heard of. Reported, not
    # added: the last time this was asked, twelve of the twenty-three unknown
    # ones were expired sx.org proxies and ten were a second vendor's - so
    # adding them would have filled the pool with rows a build then has to
    # discover are dead. Which of them belong here is the operator's call, and
    # this is what tells them there is one to make.
    known = {f"{r.proxy.host}:{r.proxy.port}:{r.proxy.username}"
             for r in book.proxies._rows if r.proxy}
    try:
        # Every page. It asked for the first and stopped, so past a hundred
        # proxies the rest simply did not exist here and this report - which
        # is the only thing that says GeeLark holds an exit the tab has never
        # heard of - silently stopped mentioning them. The same cap that was
        # fixed in `phones.listing`, in the other place it was written.
        held = []
        for page in range(1, phones.MAX_PAGES + 1):
            batch = (client.data("/v1/proxy/list",
                                 {"page": page, "pageSize": 100})
                     or {}).get("list") or []
            held += batch
            if len(batch) < 100:
                break
    except (ApiError, TransportError) as exc:
        # Never worth failing a sync over - but `held = []` makes every proxy
        # look like one the tab already knows, so the report says there is
        # nothing unlisted when what happened is that it could not look.
        log.warning("could not list GeeLark's own proxies (%s), so nothing is "
                    "reported as unlisted this run", exc)
        held = []
    unlisted = [item for item in held
                if f"{item['server']}:{item['port']}:{item.get('username', '')}"
                not in known]
    changed["unlisted"] = [
        f"{item['server']}:{item['port']} ({item.get('username', '')})"
        for item in unlisted]
    # The raw items too, on the Book for the pass to keep in the store: the
    # web's Proxy Pool page offers "add it to the pool" off this list, and
    # that needs the credentials, not the report's one-line spelling.
    book.unlisted_proxies = [
        {"host": str(item.get("server") or ""),
         "port": str(item.get("port") or ""),
         "username": str(item.get("username") or ""),
         "password": str(item.get("password") or "")}
        for item in unlisted]

    for resource in book.proxies._rows:
        if resource.error or not resource.proxy:
            continue
        status = book.proxies.status_of(resource)
        behind = live.get(f"{resource.proxy.host}:{resource.proxy.port}") or []
        if status == book.proxies.claimed_status:
            # `claimed` means a run is holding it, and this used to stop there
            # because the power state cannot tell a live run from a dead one.
            # The ledger can. A phone sitting on this exit whose claim was
            # released is a build that finished and never wrote the row back,
            # and SX16 and SX17 sat like that through a whole run - claimed,
            # with the note from the release before it, and a ready phone on
            # each (2026-08-16).
            #
            # With nothing behind it there is no phone to ask about, and a run
            # between its claim and its create looks the same, so those are
            # left for `--release-stuck` rather than guessed at.
            if not behind or ledger is None:
                continue
            if any((held := ledger.get(p["id"])) is not None
                   and held.is_claimed and not held.is_stale for p in behind):
                continue
        if behind:
            serial = ", ".join(sorted(
                str(p.get("serialNo") or p.get("id") or "") for p in behind))
            already = (resource.values.get(book.proxies.serial_column) or "")
            if status != book.proxies.spent_status or already.strip() != serial:
                book.proxies.attach(resource, serial)
                changed["attached"].append(f"{resource.label} -> phone {serial}")
        elif status == book.proxies.spent_status:
            book.proxies.release(resource, note=(
                "Free again - no phone is behind this exit any more."))
            changed["released"].append(resource.label)

    for label, items in changed.items():
        if items:
            log.info("proxies %s: %d", label, len(items))
    return changed


def sync_phone_proxies(client: Client, book: Book) -> list[str]:
    """Correct the Phones tab's Proxy column from the phone itself.

    A phone that swapped exits mid-run has its row rewritten by the build that
    moved it - but only if that build got as far as recording. One that was
    repointed by hand in the panel, or by a run that died, keeps the string it
    was created with, and that cell is what someone reads to answer "which
    exit is this phone on".
    """
    live = {str(phone.get("serialNo")): phone
            for phone in phones.listing(client)}
    # host:port -> what the Proxy tab calls it, so the correction is written in
    # the same words a build would have written.
    named = {f"{r.proxy.host}:{r.proxy.port}": (r.name or str(r.proxy))
             for r in book.proxies._rows if r.proxy}
    corrected = []
    for row in book.phones.rows():
        phone = live.get(str(row.get("Serial")))
        if phone is None:
            continue
        config = phone.get("proxy") or {}
        if not config.get("server"):
            continue
        actual = named.get(f"{config['server']}:{config.get('port')}",
                           f"{config['server']}:{config.get('port')}")
        if (row.get("Proxy") or "").strip() != actual:
            book.phones.finish(row["sheet_row"], Proxy=actual)
            corrected.append(f"phone {row.get('Serial')}")
    if corrected:
        log.info("corrected the exit recorded for %d phone(s)", len(corrected))
    return corrected


def sync_phone_names(client: Client, book: Book) -> list[str]:
    """Give every phone in GeeLark the name its serial and address say.

    The panel used to list `farm-1786928959` nine rows deep, differing in the
    last digits of a unix timestamp - the second the phone was made, which is
    the one fact nobody ever needs. Phones are named properly at creation now;
    this is for the ones made before that, for any renamed by hand, and for a
    phone whose Gmail was still blank in the tab when it was created.

    A running phone is left alone. GeeLark's own note about `/phone/detail/
    update` is that it must not be called against a phone that is coming up,
    and a tidier list is not worth reaching into a build that is under way -
    the next sync catches it once it has stopped.

    **A phone with no row is left alone too.** The account is shared, and
    most of what `/v1/phone/list` returns was made by somebody else. This
    renamed one of the operator's own phones to `1743` on 2026-09-05: it
    had no row, so `named` had nothing for it, and `display_name` happily
    made a name out of the serial alone. `strand_check` says of exactly
    this set that "a phone with no row is touched by nothing: not the
    State column, not the abandoned sweep, not the renaming" - which was
    true of the other two and never of this one. Whose phones those are is
    the operator's business, and their names are not ours to write.
    """
    named = {str(row.get("Serial") or "").strip(): (row.get("Gmail") or "").strip()
             for row in book.phones.rows()}
    renamed = []
    for phone in phones.listing(client):
        if phone.get("status") in (phones.RUNNING, phones.STARTING):
            continue
        serial = str(phone.get("serialNo") or "").strip()
        if serial not in named:
            continue                     # not ours; see the note above
        wanted = phones.display_name(serial, named.get(serial, ""))
        if not wanted or wanted == (phone.get("serialName") or "").strip():
            continue
        try:
            phones.rename(client, phone["id"], wanted)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not rename phone %s (%s)", serial, exc)
            continue
        renamed.append(wanted)
    if renamed:
        log.info("renamed %d phone(s) in GeeLark", len(renamed))
    return renamed


def _revive_ladder(settings: Settings) -> list[str]:
    from .store import ladder

    return ladder.revive_due(settings)


def free_abandoned_claims(book: Book, older_than: float) -> list[str]:
    """Put back every credential a dead run left claimed.

    The manual release exists because the tool could not tell a run that died
    holding a row from one using it right now - and handing the same Gmail to
    two phones is worse than leaving one out of the pool. So it reported them
    and waited for a hand on the console, which meant three Gmails and three
    exits sat out for a day, twice in three days.

    A claim time settles it, the same way the ledger's does for phones - and
    a live run keeps its own stamps moving, so a stamp that has stopped is
    proof the run that wrote it is gone. `older_than` is how long a claim may
    go unrefreshed; anything newer is left alone, and the console still offers
    to release those by hand.
    """
    freed = []
    for pool in (book.gmails, book.proxies, book.apps):
        for resource in pool.abandoned(older_than):
            pool.release(resource, note=(
                f"Claimed and never released. A run refreshes what it is "
                f"holding every {Pool.HEARTBEAT_SECONDS}s, and nothing "
                f"refreshed this for {older_than / 60:.0f} minutes, so the "
                f"run that took it is gone. Freed automatically on "
                f"{failures.today()}."))
            freed.append(f"{pool.tab}: {resource.label}")
    if freed:
        log.info("freed %d row(s) a dead run left claimed", len(freed))
    return freed


def strand_check(client: Client, book: Book) -> dict[str, list[str]]:
    """Two ways the sheet and the panel come apart, both of which cost stock.

    **A phone GeeLark has that the tab has never heard of.** Every settling
    path here reads the Phones tab and acts on rows, so a phone with no row is
    touched by nothing: not the State column, not the abandoned sweep, not the
    renaming. Phone 964 sat running for a day that way after an older version
    recorded it as discarded when the delete had actually been refused - the
    row went, the phone did not (2026-08-20). Reported rather than deleted,
    for the same reason an unlisted proxy is: which of them belong here is the
    operator's call, and a report that deletes phones is not a report.

    **A credential still held against a phone that is gone.** `sync_proxies`
    has done this for exits since a stale serial held thirteen of them out of
    the pool for days; nothing did it for credentials, so two app accounts and
    a Gmail sat `ready` against phones deleted days earlier, out of the pool
    and waiting for nobody.

    A Gmail is retired outright, because the rule about it is not in doubt: it
    signed into a phone, and that is the credit it had to spend, whatever
    became of the phone. An app account is only reported - `delivered` and
    `freed` are a judgement about whether it ever got a fair device, and
    guessing wrong either retires an account that was never used or frees one
    that is with a customer.

    **The account is shared**, so most of what `/v1/phone/list` returns was
    made by somebody else. Every phone this farm creates is created with
    `profileGroup: automation`; theirs carry no group at all, and on the
    seventeen orphans of 2026-09-04 that signal was right about all of them.
    A phone outside the group is not reported, because a warning nobody can
    ever clear is the same as no warning (2026-08-29) - and these three were
    the operator's own.

    The signal is checked before it is trusted, in the one way that costs
    nothing: a phone we *do* hold a row for must be in the group, because
    we put it there at creation. If any of ours is not, the group means
    something different from what this reads into it, and everything is
    reported the way it was before. It degrades to the old behaviour rather
    than to silence - the failure that matters here is a phone of ours
    running unseen and billing by the minute.
    """
    listing = phones.listing(client)
    alive = {str(p.get("serialNo") or "") for p in listing}
    known = {str(row.get("Serial") or "").strip() for row in book.phones.rows()}
    outcome: dict[str, list[str]] = {}

    ours = [p for p in listing if str(p.get("serialNo") or "") in known]
    trusted = all(_in_the_farms_group(p) for p in ours)
    if not trusted:
        log.warning("%d phone(s) with a row are not in the %r group, so the "
                    "group says nothing about whose a phone is; reporting "
                    "every unaccounted phone",
                    sum(1 for p in ours if not _in_the_farms_group(p)),
                    FARM_GROUP)
    theirs = {str(p.get("serialNo") or "") for p in listing
              if trusted and not _in_the_farms_group(p)}
    if theirs:
        log.debug("%d phone(s) on the account are outside the %r group and "
                  "are not ours: %s", len(theirs), FARM_GROUP,
                  ", ".join(sorted(theirs)))

    unknown = sorted(s for s in alive if s and s not in known | theirs)
    if unknown:
        outcome["unknown_phones"] = unknown
        # Which of them are running, separately, because the two are different
        # problems with different answers. A running one bills by the minute
        # and `Stop unaccounted phones` deals with it. A stopped one costs
        # nothing per minute but still holds a profile slot - and slots are
        # what bound the warm stock - and the only answer to that is a person
        # deleting it in the panel. Reported as one thing, the urgent half was
        # unanswerable and the whole line became a warning nobody could ever
        # clear (2026-08-29).
        running = {str(p.get("serialNo") or "") for p in listing
                   if p.get("status") in (phones.RUNNING, phones.STARTING)}
        billing = sorted(s for s in unknown if s in running)
        if billing:
            outcome["unknown_running"] = billing
        log.warning("%d phone(s) exist that the Phones tab has never heard "
                    "of: %s%s", len(unknown), ", ".join(unknown),
                    f" ({len(billing)} running)" if billing else "")

    retired, waiting = [], []
    for pool, held in ((book.gmails, retired), (book.apps, waiting)):
        for resource in pool._rows:
            serial = (resource.values.get(pool.serial_column) or "").strip()
            if not serial or serial in alive:
                continue
            if pool.status_of(resource) != pool.spent_status:
                continue                  # already settled, or never handed out
            held.append(f"{resource.label} (was on phone {serial})")
            if pool is book.gmails:
                pool.retire(resource, note=(
                    f"Phone {serial} no longer exists. An address that has "
                    f"signed into a phone is spent whatever became of it, so "
                    f"this retires rather than going back on the shelf."))
    if retired:
        outcome["stranded_retired"] = retired
    if waiting:
        outcome["stranded_waiting"] = waiting
        log.warning("%d app account(s) are held against a phone that is gone: "
                    "%s", len(waiting), ", ".join(waiting))
    return outcome


def check_proxies(client: Client, book: Book) -> tuple[list[Resource],
                                                       list[Resource]]:
    """Test the proxies a run could take, and correct the tab both ways.

    A dead proxy used to be discovered by claiming it: the build spent an
    attempt, marked it, and took the next one. That is fine for one, and it was
    a whole purchase batch that expired overnight - eight of them - so a run
    began against a pool a third of which no longer answered, and the count the
    operator had just been shown was fiction (2026-08-11).

    `dead` is tested too, and that is the half this was missing. These proxies
    are rented and renewed on the same address, so one that stopped answering
    yesterday is often answering again today - and nothing ever looked, so a
    renewed proxy stayed out of the pool until someone noticed and blanked the
    cell by hand. The check costs one call either way; the only difference is
    whether the answer can put a row back.

    Every exit no build is holding is tested - `suspect` and `change ip` as
    well, which "Test all" said it covered and did not: it tested the free
    ones and the dead ones, so half the work list answered nothing at all
    (the operator, 2026-09-14). What their answer may do is narrower: a
    `dead` one that answers is free again, while one the host gate set
    aside keeps its word and only has its exit address and test stamp
    written down. Freeing that one is the operator's press, not a test's.

    A proxy already behind a phone is not tested: it is not a candidate for
    this run, and the call would learn something that changes nothing. Checked
    in parallel because they are independent and each takes a few seconds; the
    rate limiter in api.py keeps the burst honest.

    Returns (newly dead, revived).
    """
    buried = [r for r in book.proxies._rows if not r.error and r.proxy
              and book.proxies.status_of(r) == book.proxies.dead_status]
    aside = [r for r in book.proxies._rows if not r.error and r.proxy
             and book.proxies.status_of(r) in HELD_BACK]
    free = book.proxies.available + buried + aside
    if not free:
        return [], []

    def test(resource: Resource) -> tuple[Resource, str | None, str]:
        try:
            result = proxy_mod.check(client, resource.proxy)
        except (proxy_mod.ProxyError, ApiError) as exc:
            return resource, None, str(exc)[:200]
        return resource, str(result.get("outboundIP") or ""), ""

    dead, revived = [], []
    was_dead = {id(r) for r in buried}
    with ThreadPoolExecutor(max_workers=min(8, len(free)),
                            thread_name_prefix="proxy-check") as pool:
        for resource, exit_ip, error in pool.map(test, free):
            if exit_ip is None:
                if (id(resource) in was_dead
                        or book.proxies.status_of(resource) in HELD_BACK):
                    continue          # already out of the pool, and says why
                # The name, not the label, for the reason given where the
                # build reports the same thing: the error already carries the
                # address, and the label carries it again.
                log.warning("proxy %s is dead: %s",
                            resource.name or resource.label, error)
                book.proxies.fail(resource, book.proxies.dead_status, note=(
                    f"Did not answer when the pool was checked: {error}"))
                dead.append(resource)
            elif id(resource) in was_dead:
                log.info("proxy %s answers again; back in the pool",
                         resource.label)
                book.proxies.release(resource, note=(
                    f"Answering again as of {failures.today()}, so it is back "
                    f"in the pool. It had been marked dead."))
                book.proxies.record_exit(resource, exit_ip)
                revived.append(resource)
            else:
                book.proxies.record_exit(resource, exit_ip)
    if dead:
        log.info("%d proxy(s) had died since the last run", len(dead))
    if revived:
        log.info("%d proxy(s) marked dead are answering again", len(revived))
    return dead, revived


def _unfinished(client: Client, book: Book,
                listing: list[dict] | None = None,
                held_too: bool = False,
                for_owner: str = "") -> tuple[list[dict], list[dict]]:
    """Phones one step short, split into those that still exist and those that
    do not. GeeLark's own listing is what says which - handed in when the
    caller has already asked for it this pass, fetched otherwise. `held_too`
    is the keeper's count, which keeps a taken phone (see PhoneLog.unfinished).
    `for_owner` lets the person asking reach the phones on their own shelf,
    which nobody else may have."""
    pending = book.phones.unfinished(held_too=held_too, for_owner=for_owner)
    # Resolved here rather than stored in the tab. The id is a machine's
    # handle - twenty digits nobody reads - and the serial is what the panel,
    # the notes and the operator all call the phone by, so the sheet keeps the
    # serial and this turns it into an id at the one moment anything needs one.
    by_serial = {str(p.get("serialNo")): p.get("id")
                 for p in (listing if listing is not None
                           else phones.listing(client))}
    waiting, gone = [], []
    for row in pending:
        phone_id = by_serial.get(str(row["serial"]))
        (gone if phone_id is None else waiting).append({**row,
                                                       "phone_id": phone_id})
    return waiting, gone
