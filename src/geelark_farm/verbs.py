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
    # When it was bought, if the person said. It used to be stamped today
    # whatever they meant, and `purchased_on` is the column the "how old is
    # this stock" question is answered from - so a batch bought last month
    # entering as bought today is an answer nobody can correct except by
    # editing every row. The sheet carried the real date; the console had
    # nowhere to type it (2026-09-06, found while closing the sheet).
    bought = (payload.get("purchased") or "").strip() or _stamp()
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
            "Purchase Date": bought, "Seller": seller,
            "Address": checked["address"], "Password": checked["password"],
            "Secret": checked["recovery_email"] or checked["totp_secret"],
            "Status": "",
            "Note": f"Added from the web by {_by(payload)} on {_stamp()}."})
        added.append(checked["address"])
    return _summary("gmail", added, skipped, refused, settings, _by(payload))


#: What a typed account's `Credential kind` is, by the kind the card
#: was on. The pool's own words: an eco account is `eco`, a Spotify one
#: is `password`, and a standard GPT one keeps the blank it has always
#: had, which means password-and-2fa.
def _account_is_free(book, address: str) -> str:
    """Why this app account cannot be claimed by name, or "".

    The row's own state, not the automatic claim's shortlist: a kind the
    keeper is not allowed to take by itself is still a kind a person may
    send by hand, which is the whole reason `_pick_named_app` exists.
    """
    resource = book.apps.find(address)
    if resource is None:
        return f"the account {address} is not in the accounts pool"
    if resource.error or (book.apps.status_of(resource)
                          not in book.apps.available_statuses):
        return (f"the account {address} is not free - it is already on a "
                f"phone, set aside, or not there at all")
    return ""


def _typed_kind(app: str, payload: dict) -> str:
    category = str(payload.get("app_category") or "").strip().lower()
    if category == "eco":
        return ECO_CREDENTIAL_KIND
    if app == "spotify":
        return "password"
    return ""


def build_by_hand(book, ledger, settings, payload, client):
    """One phone, with the credentials a person chose.

    Two halves, and the split is the whole design. This half runs inside
    the pass's action drain, which has to be quick: it settles what the
    words mean - adding a typed credential to its tab, checking a chosen
    one is really free - and writes the wish. The other half is the build
    phase of the same pass, which already runs seven-minute jobs in a
    pool.

    A typed credential is added to its tab rather than used and forgotten.
    Everything the farm signs in has a row: the mirror owns those columns
    and the pass reads them, so a credential that lives nowhere cannot be
    found again, cannot be marked when it fails, and cannot be counted as
    stock. Adding it is the honest spelling of "use this one".
    """
    from .store import validate
    from .store import wanted as store_wanted

    who = _by(payload)
    gmail = (payload.get("gmail") or "").strip()
    # The exit is not the card's to choose any more: the build picks one
    # and swaps it whenever an install or a sign-in shows it is bad. The
    # panel API may still name one, and a named one is honoured.
    proxy_name = (payload.get("proxy_name") or "").strip()
    # A bare phone: no Google account, and so no app and no account.
    no_gmail = bool(payload.get("no_gmail"))
    # Which app: '' for none, 'chatgpt', 'spotify', 'claude'. An older
    # payload says only `install_app`, which means ChatGPT or nothing.
    if "app" in payload:
        app = str(payload.get("app") or "").strip().lower()
    else:
        app = "chatgpt" if payload.get("install_app") else ""
    if app not in ("", "chatgpt", "spotify", "claude"):
        return "refused", f"{app!r} is not an app this farm installs", None
    # The one account a bare phone may carry is a `normal` Spotify one -
    # it wants exactly that phone (2026-09-17). Anything else named
    # alongside "no Gmail" is the card's off boxes, and ignored.
    named = (payload.get("app_account") or "").strip()
    if no_gmail:
        gmail = ""
        if not (app == "spotify" and named):
            app = ""
    install_app = bool(app)
    # An account is signed into ChatGPT or Spotify; Claude's come from
    # the panel.
    app_account = named if app in ("chatgpt", "spotify") else ""
    # Which phone this account may go on, judged before anything is
    # written. A typed row is not in the pool yet, so the rule is asked
    # of the category the card sent; a chosen row is asked of the pool.
    # Asked after the write instead, a refused Spotify account was added
    # to the pool and then turned down, leaving a row nobody asked to
    # create and a second press that could not succeed either
    # (2026-09-19).
    if app == "spotify" and app_account:
        if payload.get("app_typed"):
            wanted = "normal" if no_gmail else "error"
            given = str(payload.get("app_category") or "").strip().lower()
            if given != wanted:
                return ("refused",
                        f"a phone {'with no Google account' if no_gmail else 'that has a Gmail'} "
                        f"takes a {wanted} Spotify account, and this one "
                        f"was typed as {given or 'unlabelled'}", None)
        else:
            refused = _spotify_fits(book, app_account, no_gmail)
            if refused:
                return "refused", refused, None

    def add_typed(pool, kind, address, password, secret=""):
        """Put a typed credential in its tab, unless it is already there."""
        if pool.find(address) is not None:
            return address, ""
        try:
            if kind == "gmail":
                checked = validate.gmail_row(address=address,
                                             password=password,
                                             secret=secret, seller="")
                pool.append(**{
                    "Purchase Date": _stamp(), "Seller": "",
                    "Address": checked["address"],
                    "Password": checked["password"],
                    "Secret": (checked["recovery_email"]
                               or checked["totp_secret"]),
                    "Status": "",
                    "Note": f"Typed in by {who} on {_stamp()} to build a "
                            f"phone by hand."})
            else:
                # An eco account IS an address with no password: asking
                # `validate` for one refused the very kind the card had
                # just offered ("no password"), and a row stored without
                # the flag would be re-read as broken every pass.
                eco = (str(payload.get("app_category") or "").strip().lower()
                       == "eco")
                checked = validate.app_row(address=address,
                                           password=password, secret=secret,
                                           email_code_only=eco)
                # Filed as the kind it was chosen as. A row typed here
                # used to land with no product and no category, which
                # made it a standard ChatGPT account whatever the card
                # said - so a Spotify account typed onto a bare phone
                # was a GPT account with a Spotify password in it
                # (2026-09-19).
                pool.append(**{
                    "Address": checked["address"],
                    "Password": checked["password"],
                    "2FA Secret": checked["totp_secret"], "Status": "",
                    "Product": app or "chatgpt",
                    "Category": str(payload.get("app_category") or "").strip(),
                    "Credential kind": _typed_kind(app, payload),
                    "Email code": "TRUE" if eco else "",
                    "Note": f"Typed in by {who} on {_stamp()} to build a "
                            f"phone by hand."})
        except (validate.AccountError, validate.ProxyError) as exc:
            return "", str(exc)
        return checked["address"], ""

    if payload.get("gmail_typed"):
        gmail, refused = add_typed(book.gmails, "gmail", gmail,
                                   payload.get("gmail_password") or "",
                                   payload.get("gmail_secret") or "")
        if refused:
            return "refused", f"that Gmail was not usable - {refused}", None
        book.reload()
    if install_app and payload.get("app_typed"):
        app_account, refused = add_typed(
            book.apps, "app", app_account,
            payload.get("app_password") or "",
            payload.get("app_secret") or "")
        if refused:
            return ("refused",
                    f"that account was not usable - {refused}", None)
        book.reload()

    # A blank box is not a refusal, it is the word the box itself shows:
    # "auto". `builder.build_one` claims the next free row when the wish
    # names none, and the form says so out loud - so refusing it here made
    # the dashboard's own main button do nothing at all, under a green
    # tick (the operator, 2026-09-07).
    for what, name, pool in (("Gmail", gmail, book.gmails),
                             ("exit", proxy_name, book.proxies)):
        if not name:
            continue
        # The same question `builder._pick` asks a pass later, asked now:
        # the row has to be free, not merely present. It said only "is not
        # in the Gmails tab", so a spent address was accepted here and
        # refused half an hour later where nobody was looking.
        free = any((r.label or "").strip().lower() == name.strip().lower()
                   for r in pool.available)
        if not free:
            return ("refused",
                    f"the {what} {name} is not free - it is already on a "
                    f"phone, set aside, or not there at all", None)
    # The account is asked of the row itself, never of `available`.
    #
    # `available` subtracts the kinds the automatic claim holds back -
    # every Spotify row, and every `eco` one - and those are exactly the
    # kinds this card now offers. Spotify had an exemption here and eco
    # did not, so a free eco account the picker had just listed was
    # refused as "not free" and the kind could never be built at all
    # (2026-09-19). The wish is claimed by name a pass later
    # (`builder._pick_named_app`), which asks only whether the row is
    # free - so that is the question to ask here too.
    if install_app and app_account:
        refused = _account_is_free(book, app_account)
        if refused:
            return "refused", refused, None

    asked = store_wanted.ask(settings, gmail=gmail, proxy_name=proxy_name,
                             install_app=install_app,
                             app_account=app_account,
                             requested_by=payload.get("by_id"), app=app,
                             no_gmail=no_gmail)
    where = f" on {proxy_name}" if proxy_name else ""
    # Every phone carries all three apps, so what is worth saying back is
    # the account, not the apps (the operator, 2026-09-12).
    named = {"": "", "chatgpt": "ChatGPT", "spotify": "Spotify",
             "claude": "Claude"}[app]
    carrying = (f" and {app_account} signed into {named}" if app_account
                else " with no account signed into anything")
    if no_gmail:
        # The comma is load-bearing: "no Google account with jack@..."
        # reads as "there is no Google account with that address", which
        # is the opposite of what a bare phone carrying a Spotify account
        # is. And the app is named from `named`, not typed: a bare phone
        # may carry any of the three (2026-09-18).
        carried = (f", with {app_account} to be signed into {named}"
                   if app_account else " and nothing signed in")
        return "done", (f"asked for a bare phone{where} - no Google account"
                        f"{carried}, though it still carries the "
                        f"apps - request {asked}. It starts within seconds."
                        ), None
    who = gmail or "the next free Gmail"
    return "done", (f"asked for a phone{where} for {who}{carrying} - "
                    f"request {asked}. It starts within seconds."), None


#: The kinds of GPT account a paste can be. The unnamed one is what this
#: pool has always held - an address, a password and an authenticator
#: key. `eco` is the operator's word for an account with none of that:
#: only an address, and a fresh code emailed to it at every sign-in (the
#: operator, 2026-09-19).
GPT_CATEGORIES = ("", "eco")

#: What goes in `credential_kind` for an `eco` row - the same word the
#: console's tile and chip carry, and the same word the panel API will
#: take when these accounts start arriving through it (the operator,
#: 2026-09-19: one word, in both places). Its siblings -
#: `password_totp`, `email_code_customer` - say how an account signs
#: in; this one is the operator's name for the same thing, and one
#: word fewer to get wrong is worth more here than the symmetry.
#:
#: Not in `accounts.SERVED` yet and deliberately so: nothing can read
#: the mailbox these codes arrive in, so `accounts.held_back` keeps the
#: keeper off them until something can. Adding the word to SERVED is
#: what switches them on, and that is one line when the day comes.
ECO_CREDENTIAL_KIND = "eco"


def add_gpt(book, ledger, settings, payload, client):
    """Paste GPT accounts into the pool, one kind at a time.

    `eco` rows are address-only. A pasted password or key on one is
    refused rather than dropped: it means the paste was labelled with
    the wrong kind, and a credential thrown away without a word is the
    bug this pool's reader was written to stop making.
    """
    from .store import validate

    category = str(payload.get("category") or "").strip().lower()
    if category not in GPT_CATEGORIES:
        return ("refused",
                f"{category} is not a kind of GPT account - it is eco (an "
                f"address a code is emailed to) or the ordinary kind (an "
                f"address, a password and a 2fa key)", None)
    eco = category == "eco"
    added, skipped, refused = [], [], []
    for row in payload.get("rows") or []:
        address = str(row.get("address", "") or "")
        if eco and (str(row.get("password", "") or "").strip()
                    or str(row.get("secret", "") or "").strip()):
            refused.append(f"{address or '?'}: an eco account is an address "
                           f"and nothing else, and this line carries a "
                           f"password or a key")
            continue
        try:
            checked = validate.app_row(
                address=address,
                password="" if eco else row.get("password", ""),
                secret="" if eco else row.get("secret", ""),
                email_code_only=eco or bool(row.get("email_code_only")))
        except (validate.AccountError, validate.ProxyError) as exc:
            refused.append(f"{address or '?'}: {exc}")
            continue
        if book.apps.find(checked["address"]) is not None:
            skipped.append(checked["address"])
            continue
        # The kind, and the contract word that keeps the keeper off an
        # eco row until a flow can read its inbox. An ordinary row is
        # written exactly as it always was: neither cell is touched.
        kinds = ({"Category": "eco",
                  "Credential kind": ECO_CREDENTIAL_KIND} if eco else {})
        book.apps.append(**{
            "Address": checked["address"], "Password": checked["password"],
            "2FA Secret": checked["totp_secret"], "Status": "",
            "Email code": "TRUE" if checked["email_code_only"] else "FALSE",
            "Note": (f"Added from the web by {_by(payload)} on {_stamp()}"
                     + (" as an eco account - a code is emailed to it."
                        if eco else ".")),
            **kinds})
        added.append(checked["address"])
    return _summary("eco account" if eco else "account", added, skipped,
                    refused, settings, _by(payload))


#: The two kinds of Spotify account. They differ in one thing only -
#: which phone they may be signed in on - and the words are the ones the
#: operators already use (2026-09-17).
SPOTIFY_CATEGORIES = ("normal", "error")


def add_spotify(book, ledger, settings, payload, client):
    """Paste Spotify accounts into the pool, one category at a time.

    The same table and the same pool as the GPT accounts: what tells
    them apart is `product`, which the panel API already knew about.
    `credential_kind` is set so `accounts.held_back` leaves them where
    they are - no flow signs Spotify in yet, and a row the keeper could
    claim would go onto a phone and fail there.
    """
    from .store import validate

    category = str(payload.get("category") or "").strip().lower()
    if category not in SPOTIFY_CATEGORIES:
        return ("refused",
                f"{category or 'no category'} is not a Spotify category - "
                f"it is normal (a phone with no Gmail) or error (a phone "
                f"that has one)", None)
    added, skipped, refused = [], [], []
    for row in payload.get("rows") or []:
        try:
            checked = validate.app_row(address=row.get("address", ""),
                                       password=row.get("password", ""))
        except (validate.AccountError, validate.ProxyError) as exc:
            refused.append(f"{row.get('address', '?')}: {exc}")
            continue
        if book.apps.find(checked["address"]) is not None:
            skipped.append(checked["address"])
            continue
        book.apps.append(**{
            "Address": checked["address"], "Password": checked["password"],
            "2FA Secret": "", "Status": "", "Product": "spotify",
            "Category": category, "Credential kind": "password",
            "Note": (f"Added from the web by {_by(payload)} on {_stamp()} "
                     f"as a {category} account.")})
        added.append(checked["address"])
    return _summary("spotify account", added, skipped, refused, settings,
                    _by(payload))


#: The kinds a person may send by hand although the pool holds them back
#: from the automatic claim. Being held back means two different things
#: and only one of them is "the farm cannot do this": an `eco` ChatGPT
#: account and a Spotify one are held back so the keeper does not take
#: them off the shelf by itself, and a person pressing Send is exactly
#: how they are meant to go out (2026-09-19).
BY_HAND_KINDS = ("eco", "email_code_auto", "password")


def _cannot_sign_in(resource) -> str:
    """Why this account may not be sent to a phone at all, or "".

    The pool's automatic claim reads `accounts.held_back`; this door did
    not, so an account of a kind no flow exists for - a Google-login one,
    or anything the panel sends before its flow is written - could be
    pressed onto a warm phone, which would spend the phone and stop on a
    screen nobody has ever captured.
    """
    from . import accounts as domain

    values = getattr(resource, "values", None) or {}
    product = str(values.get("Product") or "chatgpt").strip().lower()
    kind = str(values.get("Credential kind") or "").strip()
    ready = str(values.get("Customer ready") or "").strip().upper() == "TRUE"
    if not kind or not domain.held_back(product, kind, ready):
        return ""
    if kind in BY_HAND_KINDS:
        return ""
    if kind == domain.ASKS_A_PERSON and not ready:
        return (f"its customer has not said they are at their keyboard "
                f"yet - the panel presses /ready")
    return (f"no sign-in flow exists for a {product} account of kind "
            f"`{kind}` yet, so a phone would be spent on it for nothing")


def _spotify_category(resource) -> str:
    values = getattr(resource, "values", None) or {}
    if str(values.get("Product") or "").strip().lower() != "spotify":
        return ""
    return str(values.get("Category") or "").strip().lower() or "unlabelled"


def _spotify_fits(book, address: str, no_gmail: bool) -> str:
    """Why this Spotify account may not go on the phone being asked for,
    or "" when it may. The category is the rule: `normal` wants a phone
    with no Google account, `error` one that has a Gmail (2026-09-17)."""
    resource = book.apps.find(address)
    if resource is None:
        return f"{address} is not in the Spotify pool"
    category = _spotify_category(resource)
    if not category:
        return f"{address} is not a Spotify account"
    # Free, asked of the row itself: a Spotify row is never in the pool's
    # `available` - nothing serves the product, so the automatic claim
    # holds every one back - and that list is what the loop below asks
    # for the other pools. The builder claims it by name the same way
    # (builder._pick_named_app).
    if resource.error or (book.apps.status_of(resource)
                          not in book.apps.available_statuses):
        return (f"the account {address} is not free - it is already on a "
                f"phone, set aside, or not there at all")
    if no_gmail and category != "normal":
        return (f"{address} is an {category} account - it wants a phone "
                f"that has a Gmail, not a bare one")
    if not no_gmail and category != "error":
        return (f"{address} is a {category} account - it wants a phone with "
                f"no Google account; Send on its row builds one")
    return ""


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
def _test(book, client, resource, *, tries: int = 1,
          pause: float = 15.0) -> tuple[bool, str, str]:
    """Ask GeeLark whether the exit answers. `tries` above one is patience
    for an address just changed at the vendor: SX29 was freed at 16:33
    and refused, and answered at 16:37 - the gateway takes a minute or
    two to carry the new address (2026-09-13)."""
    if client is None:
        return False, "", "no GeeLark client on this pass"
    why = ""
    for n in range(max(1, int(tries))):
        if n:
            time.sleep(pause)
        try:
            result = proxy_mod.check(client, resource.proxy)
            return True, str(result.get("outboundIP") or ""), ""
        except (proxy_mod.ProxyError, ApiError) as exc:
            why = str(exc)[:200]
            log.info("%s did not answer on try %d of %d: %s",
                     resource.name or resource.label, n + 1, tries, why)
    return False, "", why


#: How long the vendor's gateway may take to carry an address changed a
#: moment ago. SX29 was freed at 16:33 and refused, and answered at 16:37
#: (2026-09-13).
_GATEWAY_SECONDS = 15.0
#: How many exits are asked about at once. The same width `check_proxies`
#: uses, and the API limiter keeps the burst honest either way.
_TEST_WIDTH = 8


def _test_many(client, rows) -> dict:
    """Ask about all of them at once: {id(row): (ok, exit_ip, why)}."""
    from concurrent.futures import ThreadPoolExecutor

    def one(resource):
        try:
            got = proxy_mod.check(client, resource.proxy)
            return id(resource), (True, str(got.get("outboundIP") or ""), "")
        except (proxy_mod.ProxyError, ApiError) as exc:
            return id(resource), (False, "", str(exc)[:200])

    if not rows:
        return {}
    with ThreadPoolExecutor(max_workers=min(_TEST_WIDTH, len(rows)),
                            thread_name_prefix="free-all") as pool:
        return dict(pool.map(one, rows))


def _forgive(settings, resource, by: str) -> None:
    from . import builder

    builder.forgive_host(
        settings, str(getattr(getattr(resource, "proxy", None), "host", "")
                      or ""), by=by)


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
    ok, exit_ip, why = _test(book, client, resource, tries=3)
    if not ok:
        book.proxies.fail(resource, book.proxies.dead_status, note=(
            f"Marked free from the web by {_by(payload)} on {_stamp()}, but "
            f"it did not answer in three tries over half a minute: {why}"))
        return ("failed", f"{resource.name} did not answer in three tries: "
                          f"{why}", None)
    book.proxies.release(resource, note=(
        f"IP changed - marked free from the web by {_by(payload)} on "
        f"{_stamp()}. Its host is judged afresh from here."))
    if exit_ip:
        book.proxies.record_exit(resource, exit_ip)
    _stamp_test(settings, resource.name, True, exit_ip)
    # The person says the address changed: the week's verdict on the host
    # is about the old address, and the day's captcha strikes too.
    _forgive(settings, resource, _by(payload))
    return ("done", f"{resource.name} is free again (exit {exit_ip}); its "
                    f"host is judged afresh from now", None)


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
            _forgive(settings, resource, _by(payload))
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
    return ("done", f"tested every exit no build is holding: {len(dead)} "
                    f"newly dead, {len(revived)} revived", None)


def free_all_proxies(book, ledger, settings, payload, client):
    """Test every exit that is out of the pool, and free the ones that
    answer - the whole set-aside list in one press.

    What a person does before pressing it is change the addresses at the
    vendor for the exits in that list; doing them one at a time was nine
    presses in an afternoon (the operator, 2026-09-14). Each host is
    cleared the way a single Free clears it, so the gate judges it afresh
    rather than setting the exit straight back aside.
    """
    if client is None:
        return "failed", "no GeeLark client on this pass", None
    from . import builder

    held = (builder.HELD_BACK + (book.proxies.dead_status,))
    rows = [r for r in book.proxies._rows
            if not r.error and r.proxy
            and book.proxies.status_of(r) in held]
    if not rows:
        return "done", "nothing is set aside - every exit is in play", None
    # Two passes, in parallel, rather than patience per row: a serial walk
    # with a fifteen-second retry on each silent one is three minutes of a
    # browser waiting on fourteen exits, and the gateway needs that minute
    # once, not fourteen times (2026-09-14).
    answers = _test_many(client, rows)
    late = [r for r in rows if not answers[id(r)][0]]
    if late:
        time.sleep(_GATEWAY_SECONDS)
        answers.update(_test_many(client, late))
    freed, silent = [], []
    for resource in rows:
        name = str(getattr(resource, "name", "") or resource.label)
        ok, exit_ip, why = answers[id(resource)]
        if not ok:
            if book.proxies.status_of(resource) != book.proxies.dead_status:
                book.proxies.fail(resource, book.proxies.dead_status, note=(
                    f"Freed with the rest by {_by(payload)} on {_stamp()}, "
                    f"but it did not answer: {why}"))
            silent.append(name)
            _stamp_test(settings, name, False, "")
            continue
        book.proxies.release(resource, note=(
            f"Freed with the rest by {_by(payload)} on {_stamp()} - it "
            f"answers, and its host is judged afresh from here."))
        if exit_ip:
            book.proxies.record_exit(resource, exit_ip)
        _stamp_test(settings, name, True, exit_ip)
        _forgive(settings, resource, _by(payload))
        freed.append(name)
    said = f"{len(freed)} exit(s) are free again"
    if silent:
        said += (f"; {len(silent)} still did not answer and stay dead "
                 f"({', '.join(silent[:6])}"
                 f"{' and more' if len(silent) > 6 else ''})")
    return "done", said, None


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
    book.proxies.delete_row(resource, by=_by(payload))
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
    # Asked as the person pressing the button, so a phone they are
    # keeping is theirs to send to. Without this, holding a phone and
    # using it were the same door and could not both be open (3644,
    # 2026-09-19).
    warm, _gone = builder._unfinished(
        client, book, for_owner=str(payload.get("by_id") or ""))
    # The console's chooser names the phone; the old tick-and-send did not.
    # Named, that phone is the only one offered - and a name that is not a
    # warm phone is a refusal in words, not the next phone in line.
    chosen = str(payload.get("serial") or "").strip()
    if chosen:
        warm = [p for p in warm if str(p.get("serial")) == chosen]
        if not warm:
            return ("refused", f"phone {chosen} cannot take an account right "
                               f"now - it is not warm, or somebody holds it",
                    None)
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
        # A warm phone has a Gmail on it, which is the one phone a
        # `normal` Spotify account may not go on (2026-09-17).
        if _spotify_category(resource) == "normal":
            refused.append(f"{address}: a normal Spotify account wants a "
                           f"phone with no Google account - Send on its row "
                           f"builds one")
            continue
        # An account whose kind no flow signs in yet. The pool's
        # automatic claim has always left these alone; this door did
        # not, so a panel account of a kind the farm cannot do was one
        # press away from a phone that would spend seven minutes and
        # stop on a screen nobody wrote (the review, 2026-09-19). A
        # person may still send a kind that is held back only because
        # the keeper must not take it by itself - eco and Spotify are
        # exactly that, and `_by_hand_kinds` names them.
        why = _cannot_sign_in(resource)
        if why:
            refused.append(f"{address}: {why}")
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
        # Marked `building` here, before the job has started, rather than
        # by the job a few seconds in: the pass that runs this counts the
        # warm phones right after, and a phone with an account on the way
        # is not warm - counted as one, the replacement was not built until
        # the next pass (2026-09-08). Never fatal: the finish writes the
        # same word itself when it takes the phone.
        try:
            book.phones.write(str(phone["serial"]),
                              Status=book.phones.BUILDING)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("could not mark %s building ahead of its finish (%s)",
                      phone["serial"], exc)
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
    is not one, is refused here rather than discovered on a phone.

    A blank box leaves the cell as it was. It used to mean blank, on the
    reasoning that a paste which leaves the secret out means "no second
    factor" - true of a paste, and false of an editor, which never shows
    the secret it holds. So somebody correcting a seller's name saved the
    row and silently deleted the authenticator key they had paid for, and
    a blank password was not "leave it" at all but a refusal reading "no
    password" (the operator, 2026-09-07). Blank leaves it; the tick
    clears it.
    """
    from .accounts import AccountError, Credentials, normalize_totp_secret

    resource, refused = _gmail_row(book, payload)
    if refused:
        return refused
    was = dict(resource.values)
    clear = str(payload.get("clear_secret") or "").strip() in ("1", "on",
                                                               "true", "yes")
    typed = str(payload.get("secret") or "").strip()
    secret = "" if clear else (typed or str(was.get(book.gmails.SECRET_COLUMN)
                                            or "").strip())
    password = (str(payload.get("password") or "")
                or str(was.get("Password") or ""))
    recovery = secret if "@" in secret else ""
    address = str(payload.get("new_address") or "").strip() or str(
        payload.get("address") or "").strip()
    try:
        Credentials(
            email=address,
            password=password,
            totp_secret="" if recovery else normalize_totp_secret(secret),
            recovery_email=recovery,
        ).validate(what="gmail:")
    except AccountError as exc:
        return "refused", str(exc), None
    cells = {"Address": address,
             "Password": password,
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
    changed += _restate(book.gmails, resource, payload)
    return ("done", f"{address} edited by {_by(payload)}"
                    + (f" ({', '.join(changed)})" if changed
                       else " - nothing was different"),
            {"changed": changed})


#: What the editor may set a row's status to, and the word the pool writes.
#: `free` is `release` - back on the shelf, serial cleared. `set aside` is
#: a row a person does not want handed out, said in the pool's own column
#: so every reader sees it the way it sees a run's verdict.
_RESTATE = {"free": "", "set aside": "set_aside"}


def _restate(pool, resource, payload) -> list[str]:
    """Apply the editor's status choice, if it made one. Returns the list
    of what changed - empty when the choice was the row's current word.

    A row a phone is behind never reaches here: `_gmail_row`/`_app_row`
    refuse it first, and the editor greys the field for the same reason.
    """
    want = str(payload.get("state") or "").strip().lower()
    if want not in _RESTATE:
        return []
    now = pool.status_of(resource)
    if now == _RESTATE[want]:
        return []
    note = f"{want} by {_by(payload)}"
    if want == "free":
        pool.release(resource, note=note)
    else:
        pool.edit_cells(resource, **{pool.status_column: _RESTATE[want],
                                     pool.note_column: note})
    return [pool.status_column]


def free_gmail(book, ledger, settings, payload, client):
    """"Free": a row a run set aside goes back on the shelf, as it is.

    One press where the editor was three - open, choose free, save - and
    nothing else on the row is touched, which the editor could not promise
    (it rewrites every cell it shows). A row a phone is behind is refused
    the way it is everywhere else (the operator, 2026-09-06).
    """
    resource, refused = _gmail_row(book, payload)
    if refused:
        return refused
    if book.gmails.status_of(resource) == "":
        return "done", f"{payload.get('address')} is already free", None
    book.gmails.release(resource, note=f"Put back on the shelf by {_by(payload)}.")
    return "done", f"{payload.get('address')} is back on the shelf", None


def free_app(book, ledger, settings, payload, client):
    """`free_gmail`, for the GPT pool."""
    resource, refused = _app_row(book, payload)
    if refused:
        return refused
    if book.apps.status_of(resource) == "":
        return "done", f"{payload.get('address')} is already free", None
    book.apps.release(resource, note=f"Put back on the shelf by {_by(payload)}.")
    return "done", f"{payload.get('address')} is back on the shelf", None


def refund_gmail(book, ledger, settings, payload, client):
    """Move one address along the refund list: claimed, refused, or back
    to `to_claim` if it was marked by mistake.

    Nothing about stock: these rows left the pool the moment Google said
    the account itself was the problem, and none of the three words puts
    one back. What it changes is whether the address is still money
    somebody is owed (2026-09-12).
    """
    from .store import refunds

    address = (payload.get("address") or "").strip()
    state = (payload.get("state") or "").strip().lower()
    if state not in refunds.STATES:
        return "failed", f"{state or '?'} is not one of the refund words", None
    row = refunds.mark(settings, address=address, state=state,
                       by=_by(payload))
    if row is None:
        return ("failed", f"{address or '?'} is not a Gmail on the refund "
                          f"list", None)
    said = {"claimed": "was paid back", "refused": "was refused by the seller",
            "to_claim": "is back on the list to claim"}[state]
    return "done", f"{row['address']} {said} ({_by(payload)})", {"state": state}


def remove_gmail(book, ledger, settings, payload, client):
    """Out of the pool and into the archive. The row it removed rides in
    the detail, so Requests can put it back the way a removed proxy can -
    and the archive keeps the whole of it either way (2026-09-11)."""
    resource, refused = _gmail_row(book, payload)
    if refused:
        return refused
    address = str(resource.values.get("Address") or "")
    kept = {name: str(resource.values.get(name) or "")
            for name in ("Address", "Password", book.gmails.SECRET_COLUMN,
                         "Seller", "Purchase Date")}
    book.gmails.delete_row(resource, by=_by(payload))
    return ("done", f"{address} removed from the pool by {_by(payload)} - "
                    f"archived, not deleted", {"removed": kept})


def _app_row(book, payload):
    """The GPT row this command names, or the refusal that says why not.

    Same rule as `_gmail_row`, for the same reason: a row a phone is
    behind, or one already delivered, is not stock to edit or tidy away.
    """
    address = (payload.get("address") or "").strip()
    resource = book.apps.find(address)
    if resource is None:
        return None, ("failed", f"{address or '?'} is not in the "
                                f"{book.apps.tab} tab", None)
    status = book.apps.status_of(resource)
    if status in (book.apps.claimed_status, book.apps.spent_status,
                  book.apps.retired_status):
        return None, ("refused", f"{address} is {status} - a phone is behind "
                                 f"it", None)
    return resource, None


def edit_app(book, ledger, settings, payload, client):
    """The GPT row editor - `edit_gmail`, for the other account pool.

    Judged before anything is written, the way a pasted row is: an address
    that is not one, or a secret that is not base32, is refused here rather
    than discovered on a phone.

    A blank box leaves the cell as it was - see `edit_gmail` for why that
    is not what a blank means in a paste. The tick clears the secret.

    And an emailed-code account keeps its tick: `Credentials.validate`
    reads `email_code_only` to know a blank password is allowed, so
    leaving it out refused every edit of such a row outright (2026-09-07).
    """
    from .accounts import AccountError, Credentials, normalize_totp_secret

    resource, refused = _app_row(book, payload)
    if refused:
        return refused
    was = dict(resource.values)
    clear = str(payload.get("clear_secret") or "").strip() in ("1", "on",
                                                               "true", "yes")
    typed = str(payload.get("secret") or "").strip()
    secret = "" if clear else (typed
                               or str(was.get("2FA Secret") or "").strip())
    password = (str(payload.get("password") or "")
                or str(was.get("Password") or ""))
    address = str(payload.get("new_address") or "").strip() or str(
        payload.get("address") or "").strip()
    try:
        Credentials(
            email=address,
            password=password,
            totp_secret=normalize_totp_secret(secret),
            email_code_only=bool(getattr(resource.credentials,
                                         "email_code_only", False)),
        ).validate(what="app account:")
    except AccountError as exc:
        return "refused", str(exc), None
    cells = {"Address": address,
             "Password": password,
             "2FA Secret": secret}
    # The one field a Spotify row has that a GPT row does not, and the
    # one a paste can get wrong: which phone the account may go on
    # (2026-09-17). Only on its own rows - a GPT account has no category
    # and a stray one would be a word nothing reads.
    category = str(payload.get("category") or "").strip().lower()
    if (category in SPOTIFY_CATEGORIES
            and str(was.get("Product") or "") == "spotify"):
        cells["Category"] = category
    problem = book.apps.edit_cells(resource, **cells)
    if problem:
        book.apps.edit_cells(resource, **{name: str(was.get(name, ""))
                                          for name in cells})
        return "refused", problem, None
    changed = [name for name, value in cells.items()
               if str(was.get(name, "")) != value]
    changed += _restate(book.apps, resource, payload)
    return ("done", f"{address} edited by {_by(payload)}"
                    + (f" ({', '.join(changed)})" if changed
                       else " - nothing was different"),
            {"changed": changed})


def remove_app(book, ledger, settings, payload, client):
    """Out of the pool and into the archive. The row rides in the detail
    so Requests can put it back, the way a removed Gmail or proxy can."""
    resource, refused = _app_row(book, payload)
    if refused:
        return refused
    address = str(resource.values.get("Address") or "")
    # Product and category too, or Undo puts a Spotify row back as a
    # GPT account (2026-09-17).
    kept = {name: str(resource.values.get(name) or "")
            for name in ("Address", "Password", "2FA Secret",
                         book.apps.EMAIL_CODE_COLUMN, "Product", "Category")}
    book.apps.delete_row(resource, by=_by(payload))
    return ("done", f"{address} removed from the pool by {_by(payload)} - "
                    f"archived, not deleted", {"removed": kept})


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
    try:
        book.apps.append(**{
            "Address": row["address"], "Password": row["password"],
            "2FA Secret": row["secret"], "Status": "",
            "Email code": "TRUE" if row["email_code_only"] else "FALSE",
            "Note": f"From the customer panel ({ref}) on {_stamp()}."})
    except Exception as exc:                                      # noqa: BLE001
        # The request will say failed, but the panel reads accounts, not
        # requests - so the row itself has to say it too, or it reads
        # `queued` forever while nothing is ever going to claim it.
        _panel_broke(settings, ref, f"could not be added to the tab: {exc}")
        return "failed", f"{row['address']} was not added: {exc}", None
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
    book.apps.delete_row(resource, by=_by(payload))
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
    if getattr(settings, "store_enabled", False):
        # The build is in a builder container, not this process: the set
        # above is heard by nobody there. The store's copy is (the
        # operator, 2026-09-10: "the Cancel button does nothing").
        from .store import stops as store_stops

        try:
            store_stops.ask(settings, serial)
        except Exception as exc:                                  # noqa: BLE001
            return ("failed", f"the stop for phone {serial} could not be "
                              f"written where the builders read it ({exc})",
                    None)
    return ("done", f"phone {serial} stops at its next step; whatever it "
                    f"held goes back to its pool, and if nothing was signed "
                    f"into it yet the phone is deleted", None)


def power_off_phone(book, ledger, settings, payload, client):
    """Stop the phone in GeeLark, so it stops billing.

    Release said "back on the shelf" and left the phone running - a
    phone booted from the console and released kept billing until
    somebody noticed it under Running (the operator, 2026-09-08). Queued
    beside Release by the web; the same door Boot goes through, the
    other way. A phone a run holds is left to the run.
    """
    from . import phones as phones_mod
    from .phones import PhoneError

    serial = str(payload.get("serial") or "").strip()
    if not serial:
        return "refused", "no phone named", None
    if client is None:
        return "failed", "no GeeLark client on this pass", None
    live = next((p for p in phones_mod.listing(client)
                 if str(p.get("serialNo")) == serial), None)
    if live is None:
        return "failed", f"phone {serial} is not in GeeLark's list", None
    held = ledger.get(live["id"]) if ledger is not None else None
    if held is not None and held.is_claimed and not held.is_stale:
        return "refused", f"phone {serial} is held by a run ({held.label})", None
    if live.get("status") not in (phones_mod.RUNNING, phones_mod.STARTING):
        return "done", f"phone {serial} was already off", None
    try:
        phones_mod.stop(client, live["id"])
    except (PhoneError, ApiError) as exc:
        return "failed", f"phone {serial} would not stop: {exc}", None
    return "done", f"phone {serial} is off - it stops billing", {"off": True}


def change_proxy(book, ledger, settings, payload, client):
    """Put a phone on a different exit: the next free one from the pool.

    The phone is stopped first - Android reads the proxy when the network
    comes up, and GeeLark refuses the update on a phone that is starting -
    then GeeLark is told, and only after it agreed are the two rows moved:
    the old exit back to free, the new one spent on this serial. A phone a
    run holds right now is refused; a run swapping exits underneath a
    build is the one thing worse than a bad exit.

    `boot` in the payload is the Live tab's press (the operator,
    2026-09-16: "change the IP without leaving this page"): the phone is
    started again on its new exit and the live-view link rides in the
    detail, exactly as Boot's does, for the tab that is waiting on it. A
    phone that then would not start is `failed` with `off` in the
    detail, so the tab knows to offer Boot rather than the old screen.
    """
    from . import phones as phones_mod
    from .phones import PhoneError

    serial = str(payload.get("serial") or "").strip()
    boot = bool(payload.get("boot"))
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
    # Whether the phone is off by the time this returns: it was, or the
    # stop below went through. Only the Live tab asks.
    off = live.get("status") not in (phones_mod.RUNNING, phones_mod.STARTING)
    try:
        if not off:
            phones_mod.stop(client, live["id"])
            phones_mod.wait_until_stopped(client, live["id"])
            off = True
        phones_mod.set_proxy(client, live["id"], fresh.proxy)
    except (PhoneError, ApiError) as exc:
        log.warning("phone %s kept its exit: %s", serial, exc)
        book.proxies.release(fresh, note=(
            f"Phone {serial} would not take it on {_stamp()}: "
            f"{str(exc)[:120]}"))
        return ("failed", f"GeeLark refused the change: {str(exc)[:160]}",
                {"off": off} if boot else None)
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
    moved = {"was": (row.get("Proxy") or "").strip(), "now": name}
    if not boot:
        return ("done", f"phone {serial} is on {name} now (it is stopped; it "
                        f"reads the new exit when it next starts)", moved)
    try:
        # One attempt, as Boot's: the person is watching a tab that can
        # offer Boot again in a minute.
        url = phones_mod.start(client, live["id"], attempts=1)
    except phones_mod.PhoneCapacityError:
        return ("failed", f"phone {serial} is on {name} now but GeeLark has "
                          f"no machine free to start it - press Boot again "
                          f"in a minute", dict(moved, off=True))
    except (PhoneError, ApiError) as exc:
        return ("failed", f"phone {serial} is on {name} now but would not "
                          f"start: {exc}", dict(moved, off=True))
    if not url:
        return ("done", f"phone {serial} is on {name} now and started again "
                        f"- GeeLark gave no live-view link back", moved)
    return ("done", f"phone {serial} is on {name} now and started again",
            dict(moved, url=url))


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


def _is_building(settings, serial: str) -> bool:
    """Whether a run is holding this phone right now.

    Asked of the store rather than of the tab, now that the tab is not
    where the answer lives. Closing a phone mid-build is refused for the
    same reason it always was: the run would go on spending minutes on a
    phone somebody has already written off.
    """
    from .store.db import Store

    try:
        with Store(settings) as store:
            rows = store._rows(
                "SELECT status FROM phones"
                " WHERE serial = %s AND done_at IS NULL", (str(serial),))
    except Exception:                                             # noqa: BLE001
        # Unknown is not "no": refusing to close a phone because the store
        # blinked is the safe way round, and the person can press again.
        return True
    return bool(rows) and (rows[0]["status"] or "") == "building"


def set_phone_state(book, ledger, settings, payload, client):
    """Write the State cell - taken / done / failed / (blank) - the way a
    hand does in the sheet; the keeper's lane carries it out the moment
    it hears the bell (measured over a day: about two seconds from the
    press to the phone being gone). A phone somebody takes is stamped
    with who took it in the mirror."""
    serial = str(payload.get("serial") or "").strip()
    state = str(payload.get("state") or "").strip().lower()
    if state not in ("taken", "done", "failed", "", "unused"):
        return "refused", f"{state!r} is not a State word", None
    from .store import person

    word = "" if state == "unused" else state
    if word in ("done", "failed") and _is_building(settings, serial):
        return "refused", f"phone {serial} is being worked on right now", None
    if not person.set_state(settings, serial, word):
        return "failed", f"phone {serial or '?'} is not on the farm", None
    _stamp_owner(settings, serial,
                 payload.get("by_id") if word == "taken" else None)
    meaning = {"taken": "out with somebody - the farm leaves it alone",
               "done": "the phone is deleted in a moment and what was on it "
                       "retired",
               "failed": "the phone is deleted in a moment and its account "
                         "freed",
               "": "back on the shelf"}[word]
    return ("done", f"phone {serial} marked {word or 'unused'} by "
                    f"{_by(payload)}: {meaning}", {"state": word})


def clear_tries(book, ledger, settings, payload, client):
    """A given-up phone back in the queue: the Tries cell blanked, the
    way the runbook says to do it by hand."""
    from .store import person

    serial = str(payload.get("serial") or "").strip()
    if not person.clear_tries(settings, serial):
        return "failed", f"phone {serial or '?'} is not on the farm", None
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


#: The only halves of the book a verb may touch and still answer inside
#: the request that asked for it. Stock lives in Postgres now, and reaching
#: it costs milliseconds; everything else in the book is the workbook, and
#: opening that is about six seconds against Google - fine once a pass,
#: absurd on every click.
_POOLS = ("gmails", "apps", "proxies")

#: Book methods that are not a tab. `reload` re-reads the three pools,
#: which with the pools in the store is three Postgres queries.
_BOOK_METHODS = ("reload", "beat")

#: The workbook halves, named however a verb spells them. `control`
#: reaches the Service board with `getattr(book, "service")`, which no
#: scan of `book.<name>` would ever see - and that is the same blind spot
#: a blacklist of attribute names has.
_WORKBOOK = ("phones", "history", "service")


def runs_inline(verb: str) -> bool:
    """Whether this verb can answer in the request that asked for it.

    Closed by default and derived from the verb's own source, rather than
    listed. A list is a second place to remember, and the verb that gets
    forgotten is the one that then blocks a web request for six seconds or
    drives a phone from it.

    Two rules, both of which have to hold:

    - every `book.<something>` it names is one of the three pools. The
      Phones tab, the History tab and the Service board are the workbook.
    - it does not name `client` at all. That is GeeLark: booting a phone,
      testing an exit, deleting a profile - seconds to minutes of somebody
      else's network, which a person waiting on a form should not hold.

    Written as "may touch only these" rather than "must not touch those"
    on purpose. The first draft was the second shape and it called four
    verbs instant that were not - the Service board, the account logins
    and both proxy tests - because each reached the world by a spelling
    the list had not thought of. A closed rule is wrong in the safe
    direction: the worst it does is leave something on the queue, which is
    where everything was yesterday.
    """
    import inspect
    import re

    found = VERBS.get(verb)
    if found is None:
        return False
    try:
        source = inspect.getsource(found)
    except (OSError, TypeError):                                  # pragma: no cover
        return False
    # Past the signature, so the parameter names in it are not evidence.
    body = source.partition(chr(10))[2]
    if re.search(r"\bclient\b", body):
        return False
    if any(re.search(rf'"{half}"|\bbook\.{half}\b', body)
           for half in _WORKBOOK):
        return False
    return all(name in _POOLS + _BOOK_METHODS
               for name in re.findall(r"\bbook\.(\w+)", body))


VERBS = {
    "login_accounts": login_accounts,
    "control": control,
    "set_phone_state": set_phone_state,
    "boot_phone": boot_phone,
    "clear_tries": clear_tries,
    "ignore_proxy": ignore_proxy,
    "change_proxy": change_proxy,
    "stop_phone": stop_phone,
    "power_off_phone": power_off_phone,
    "add_gmails": add_gmails,
    "build_by_hand": build_by_hand,
    "edit_gmail": edit_gmail,
    "remove_gmail": remove_gmail,
    "edit_app": edit_app,
    "remove_app": remove_app,
    "free_gmail": free_gmail,
    "refund_gmail": refund_gmail,
    "free_app": free_app,
    "add_gpt": add_gpt,
    "add_spotify": add_spotify,
    "add_panel_account": add_panel_account,
    "withdraw_panel_account": withdraw_panel_account,
    "add_proxies": add_proxies,
    "adopt_proxy": adopt_proxy,
    "offer_again": offer_again,
    "mark_proxy_free": mark_proxy_free,
    "test_proxy": test_proxy,
    "test_all_proxies": test_all_proxies,
    "free_all_proxies": free_all_proxies,
    "remove_proxy": remove_proxy,
}


#: The verbs a second drainer may run off the pass's thread.
#:
#: Marked by hand and not derived, because the rule is not a property of the
#: source: it is "this finishes in seconds and holds nothing a build needs".
#: `runs_inline` is derived and refuses anything naming `client`, which is
#: exactly the set that matters here - a boot, an exit test, a proxy swap -
#: so a second rule would have had to be its opposite and would have said
#: yes to `login_accounts`, which is a ten-minute job and belongs on the
#: pass with the pass's fuse, flight and launcher.
#:
#: Everything here was checked one at a time against what a build holds at
#: the same moment: the pools claim with FOR UPDATE SKIP LOCKED, the phone
#: boards write a row at a time, the ledger is only read, and the GeeLark
#: client keeps a session per thread behind one locked limiter. Add nothing
#: here without doing that, and nothing that can take minutes.
for _lane in (control, boot_phone, test_proxy, test_all_proxies,
              free_all_proxies,
              change_proxy, mark_proxy_free, adopt_proxy, add_proxies,
              ignore_proxy, remove_proxy, set_phone_state, stop_phone,
              power_off_phone,
              # Three seconds of pairing; the minutes of login go to the
              # lane's own pool through `launch`, so the lane's thread is
              # free again at once (A-1, 2026-09-08). The pass still drains
              # it too, as the backstop it is for every lane verb.
              login_accounts,
              # One UPDATE against the store and nothing else: a person
              # ticking off a refund should not wait for a pass.
              refund_gmail):
    _lane.lane_safe = True
del _lane
