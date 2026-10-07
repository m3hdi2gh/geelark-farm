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

import contextlib
import logging
import re
import time

from . import pools, purposes
from . import proxy as proxy_mod
from .api import ApiError, TransportError

log = logging.getLogger(__name__)

_SX = re.compile(r"^SX(\d+)$", re.IGNORECASE)


def _by(payload: dict) -> str:
    return payload.get("by") or "the web"


def _stamp() -> str:
    return time.strftime("%Y-%m-%d")


def _uid(payload: dict) -> int:
    """The presser's user id, or 0 when the payload carries none."""
    raw = str(payload.get("by_id") or "").strip()
    return int(raw) if raw.isdigit() else 0


def _store_on(settings) -> bool:
    return settings is not None and bool(getattr(settings, "store_enabled",
                                                 False))


def _guarded(settings, default, call, *args, **kwargs):
    """A store call inside a verb: skipped when there is no store, and a
    failure logged and answered with `default`, never raised."""
    if not _store_on(settings):
        return default
    try:
        return call(settings, *args, **kwargs)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("%s did not answer (%s)",
                    getattr(call, "__name__", "the store"), exc)
        return default


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
        try:
            book.gmails.append(**{
                "Purchase Date": bought, "Seller": seller,
                "Address": checked["address"],
                "Password": checked["password"],
                "Secret": checked["recovery_email"] or checked["totp_secret"],
                "Status": "",
                "Note": f"Added from the web by {_by(payload)} on {_stamp()}."})
        except ValueError:
            # In the table under a row `find` cannot see - one whose
            # cells never parsed, or a spent one. The paste used to die
            # here mid-loop, rows after it never added and never
            # reported, under a banner that said "Queued" (2026-09-21,
            # found by audit).
            skipped.append(checked["address"])
            continue
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
    # The exit: blank means auto - the build picks one and swaps it
    # whenever an install or a sign-in shows it is bad. The card's dialog
    # (back 2026-09-28) and the panel API may name one, and a named free
    # one is honoured; a typed proxy string joins the pool first (below).
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
    # Which lane the phone is for. An account decides it by itself - a
    # Spotify account wants a Spotify phone whatever the card said -
    # and the card's box speaks for a phone with no account on it.
    purpose = (purposes.of_product(app) if app
               else purposes.normal(payload.get("purpose")) or purposes.GPT)
    # A phone for another app (the Station, 2026-09-29): no lane's stock,
    # behind any free IP, and nothing signed in. `normal` knows no such
    # lane - it would read GPT - so the word is asked for exactly.
    other = (str(payload.get("purpose") or "").strip().lower()
             == purposes.OTHER and app == "")
    if other:
        purpose = purposes.OTHER
    # The Station's words: an IP, never an exit.
    station = bool(payload.get("station"))
    ip_word = "IP" if station else "exit"
    # An account is signed into ChatGPT or Spotify; Claude's come from
    # the panel.
    app_account = named if app in ("chatgpt", "spotify") else ""
    # An Other phone's account is carried on the wish for its Live tab,
    # and never signed in or put in a pool.
    carry_address = carry_password = ""
    if other:
        app_account = ""
        carry_address = str(payload.get("carry_address") or "").strip().lower()
        carry_password = str(payload.get("carry_password") or "")
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
                which = ("with no Google account" if no_gmail
                         else "that has a Gmail")
                return ("refused",
                        f"a phone {which} takes a {wanted} Spotify account, "
                        f"and this one was typed as {given or 'unlabelled'}",
                        None)
        else:
            refused = _spotify_fits(book, app_account, no_gmail)
            if refused:
                return "refused", refused, None
        # What is left on a phone with a Gmail is an `error` account,
        # which a new build never signed in - its row's Send is the door
        # (2026-09-24). Asked after the kind checks, so a `normal` one
        # still hears its own reason.
        if not no_gmail:
            return "refused", SPOTIFY_ERROR_ON_A_BUILD, None

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

    # A typed exit (the card's dialog, 2026-09-28). One the pool already
    # holds by host:port is that row, free or not like any named one;
    # anything else is for this build only, and goes in - last, once
    # nothing else can refuse the wish - as a one-off.
    one_off = ""
    if proxy_name and payload.get("proxy_typed"):
        proxy_name, one_off, refused = _typed_exit(book, proxy_name)
        if refused:
            return ("refused", f"that {ip_word} was not usable - {refused}",
                    None)

    # A blank box is not a refusal, it is the word the box itself shows:
    # "auto". `builder.build_one` claims the next free row when the wish
    # names none, and the form says so out loud - so refusing it here made
    # the dashboard's own main button do nothing at all, under a green
    # tick (the operator, 2026-09-07).
    for what, name, pool in (("Gmail", gmail, book.gmails),
                             (ip_word, "" if one_off else proxy_name,
                              book.proxies)):
        if not name:
            continue
        # The same question `builder._pick` asks a pass later, asked now:
        # the row has to be free, not merely present. It said only "is not
        # in the Gmails tab", so a spent address was accepted here and
        # refused half an hour later where nobody was looking.
        match = next((r for r in pool.available if pools.is_called(r, name)),
                     None)
        if match is None:
            return ("refused",
                    f"the {what} {name} is not free - it is already on a "
                    f"phone, set aside, or not there at all", None)
        # A named exit of the other lane is refused now, in words, rather
        # than by the build a pass later. An Other phone takes any exit.
        if (pool is book.proxies and purpose != purposes.OTHER
                and not purposes.fits(match.values.get("Purpose"), purpose)):
            kept = purposes.word(match.values.get("Purpose")) or "another lane"
            return ("refused", f"the IP {name} is kept for {kept} - use Auto "
                               f"or another", None)
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
    # Free on the row is not free: a build asked for a minute ago names
    # it and claims it only when it gets there (2026-09-27).
    going = store_wanted.on_its_way(settings, app_account, gmail)
    if going:
        return ("refused", f"{going} is already on its way to a phone being "
                           f"built - see the Phones table", None)

    if one_off:
        try:
            _one_off_exit(book, payload, one_off, proxy_name)
        except ValueError:
            return ("refused", f"the {ip_word} {proxy_name} is already in "
                               f"the pool", None)
    # Never refused because the farm is stopped: a wish waits for it.
    asked = store_wanted.ask(settings, gmail=gmail, proxy_name=proxy_name,
                             install_app=install_app,
                             app_account=app_account,
                             requested_by=payload.get("by_id"), app=app,
                             no_gmail=no_gmail, purpose=purpose,
                             station=station, carry_address=carry_address,
                             carry_password=carry_password)
    if proxy_name:
        where = f" on {proxy_name}"
    elif other:
        where = f" behind any free {ip_word}"
    else:
        where = f" behind a {purposes.word(purpose)} {ip_word}"
    # Every phone carries all three apps, so what is worth saying back is
    # the account, not the apps (the operator, 2026-09-12).
    named = {"": "", "chatgpt": "ChatGPT", "spotify": "Spotify",
             "claude": "Claude"}[app]
    carrying = (f" and {app_account} signed into {named}" if app_account
                else " with no account signed into anything")
    if carry_address:
        carrying += f", carrying {carry_address} for you to sign in by hand"
    if no_gmail:
        # The comma is load-bearing: "no Google account with jack@..."
        # reads as "there is no Google account with that address", which
        # is the opposite of what a bare phone carrying a Spotify account
        # is. And the app is named from `named`, not typed: a bare phone
        # may carry any of the three (2026-09-18).
        carried = (f", with {app_account} to be signed into {named}"
                   if app_account else " and nothing signed in")
        if carry_address:
            carried += (f", carrying {carry_address} for you to sign in by "
                        f"hand")
        return "done", (f"asked for a bare phone{where} - no Google account"
                        f"{carried}, though it still carries the "
                        f"apps - request {asked}. It starts within seconds."
                        ), {"wanted_id": asked}
    who = gmail or "the next free Gmail"
    return "done", (f"asked for a phone{where} for {who}{carrying} - "
                    f"request {asked}. It starts within seconds."), {
                        "wanted_id": asked}


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
        try:
            book.apps.append(**{
                "Address": checked["address"],
                "Password": checked["password"],
                "2FA Secret": checked["totp_secret"], "Status": "",
                "Email code": "TRUE" if checked["email_code_only"] else "FALSE",
                "Note": (f"Added from the web by {_by(payload)} on {_stamp()}"
                         + (" as an eco account - a code is emailed to it."
                            if eco else ".")),
                **kinds})
        except ValueError:                 # see add_gmails
            skipped.append(checked["address"])
            continue
        added.append(checked["address"])
    return _summary("eco account" if eco else "account", added, skipped,
                    refused, settings, _by(payload))


#: The two kinds of Spotify account. They differ in one thing only -
#: which phone they may be signed in on - and the words are the ones the
#: operators already use (2026-09-17).
SPOTIFY_CATEGORIES = ("normal", "error")

#: Why an `error` Spotify account is refused on a new build. It wants a
#: phone that has a Gmail, and the build signed only ChatGPT accounts in
#: there: it installed Spotify and reported the phone ready, with the
#: account never signed in and nobody told (the builder review,
#: 2026-09-23). The warm phone its own Send picks is the door that works,
#: so the card sends people there instead (the operator, 2026-09-24).
SPOTIFY_ERROR_ON_A_BUILD = (
    "an error Spotify account goes on a warm phone, not on a new build - "
    "press Send on its row in the Spotify pool")


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
        try:
            book.apps.append(**{
                "Address": checked["address"],
                "Password": checked["password"],
                "2FA Secret": "", "Status": "", "Product": "spotify",
                "Category": category, "Credential kind": "password",
                "Note": (f"Added from the web by {_by(payload)} on {_stamp()} "
                         f"as a {category} account.")})
        except ValueError:                 # see add_gmails
            skipped.append(checked["address"])
            continue
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
        return ("its customer has not said they are at their keyboard "
                "yet - the panel presses /ready")
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


def _typed_exit(book, raw: str) -> tuple[str, str, str]:
    """What a proxy string typed on the build card stands for, as
    (the name the wish carries, the string to add as a one-off or "",
    why it is no use or "").

    One the pool already holds by host and port is that row. Anything
    else is for this one build (the operator, 2026-09-28: "it is not
    meant to join the pool and stay"), named by its host and port."""
    from .store import validate

    try:
        checked = validate.proxy_row(raw=raw)
    except (validate.AccountError, validate.ProxyError) as exc:
        return "", "", str(exc)
    endpoint = f"{checked['host']}:{checked['port']}"
    have = book.proxies.find_exact(checked["host"], checked["port"],
                                   checked["username"])
    if have is not None:
        return (have.name or have.label), "", ""
    return endpoint, raw, ""


def _one_off_exit(book, payload, raw: str, endpoint: str) -> None:
    """Put a typed proxy in for the one build that names it.

    Under `one-off`, which `available` never offers, so no other build
    and no count of free exits sees it; it leaves the pool the moment it
    is let go (ProxyPool.one_off_status). Untested here, on purpose:
    `build_by_hand` answers inside the request that pressed Build, and a
    proxy test is seconds of somebody else's network - the build is the
    test, and a dead exit fails its phone the way any dead exit does."""
    pool = book.proxies
    row = pool.append(**{
        "Name": endpoint, "Proxy String": raw,
        "Status": pool.one_off_status, "Source": pool.ONE_OFF_SOURCE,
        "Note": (f"Typed on the build card by {_by(payload)} on {_stamp()} "
                 f"for one build; it leaves the pool when that build or "
                 f"its phone lets it go."),
        "Times Used": "0"})
    # A sheet pool has no Source column; the mark still has to hold for
    # the rest of this process.
    row.values["Source"] = pool.ONE_OFF_SOURCE


def add_proxies(book, ledger, settings, payload, client):
    """Each is tested before it joins: a proxy that does not answer goes
    in as `dead` rather than as free stock a build then discovers.

    All of them at once, the way Free all tests (`_test_many`): one at a
    time, ten exits on a slow gateway took minutes to land, and the
    operator read "three added" while the rest were still being asked
    (2026-09-29). The rows go in in the order they were pasted, whatever
    order the answers came back in."""
    from types import SimpleNamespace

    from .store import validate

    added, skipped, refused = [], [], []
    probes: list = []
    for row in payload.get("rows") or []:
        raw = (row.get("raw") or "").strip()
        try:
            checked = validate.proxy_row(raw=raw, name=row.get("name", ""))
        except (validate.AccountError, validate.ProxyError) as exc:
            refused.append(f"{raw or '?'}: {exc}")
            continue
        # All three of what makes a proxy, not the endpoint alone: a vendor
        # that multiplexes hands out ten on one host:port, told apart by
        # the username, and nine of them were called "already in the pool"
        # (2026-09-28).
        if book.proxies.find_exact(checked["host"], checked["port"],
                                   checked["username"]):
            skipped.append(f"{checked['host']}:{checked['port']}")
            continue
        probes.append(SimpleNamespace(raw=raw, checked=checked,
                                      proxy=proxy_mod.parse(raw)))
    # A batch from the Proxies page (2026-10-02): every proxy is named
    # Seller-Type-DDMon-n here, at run time, so two pastes of one batch
    # queued together still get numbers apart; and each carries the
    # daily cap the page chose.
    prefix = _batch_prefix(payload.get("batch"))
    if payload.get("batch") is not None and not prefix:
        return "refused", "a batch needs its seller, in letters or digits", None
    cap = _cap_of(payload) if prefix else 0
    dead: list[str] = []
    lane = purposes.normal(payload.get("purpose"))
    with contextlib.ExitStack() as held:
        names: list[str] = []
        if prefix:
            try:
                held.enter_context(_batch_lock(settings, prefix))
                names = _batch_names(book, settings, prefix, len(probes))
            except Exception as exc:                              # noqa: BLE001
                return ("failed", f"the batch could not be numbered ({exc}); "
                                  f"nothing was added", None)
        _join_proxies(book, payload, client, probes, names, lane, cap,
                      added, skipped, dead)
    status, said, detail = _summary("proxy", added, skipped, refused,
                                    settings, _by(payload))
    if prefix:
        if dead:
            said += (f"; {len(dead)} did not answer and joined as dead - "
                     f"each is tested again on its own")
        detail = dict(detail or {}, dead=dead)
    return status, said, detail


def _join_proxies(book, payload, client, probes, names, lane, cap,
                  added, skipped, dead) -> None:
    """Test the probes at once, then add them in the order pasted."""
    answers = _test_many(client, probes) if client is not None else {}
    for i, probe in enumerate(probes):
        checked = probe.checked
        name = names[i] if names else (checked["proxy_name"] or _next_name(book))
        status, note = "free", f"Added from the web by {_by(payload)} on " \
                               f"{_stamp()}."
        ok, exit_ip, why = answers.get(id(probe), (True, "", ""))
        if not ok:
            log.info("%s did not answer on arrival: %s", name, why)
            status = book.proxies.dead_status
            note = f"Added from the web, but it did not answer: {why}"
        try:
            book.proxies.append(**{
                "Name": name, "Proxy String": probe.raw, "Status": status,
                "Note": note, "Last Exit IP": exit_ip, "Times Used": "0",
                # The lane it is kept for, when the paste said one; a
                # pool that predates lanes has no column to put it in.
                **({"Purpose": lane} if lane else {}),
                **({"Uses per day": str(cap)} if cap else {})})
        except ValueError:                 # see add_gmails
            skipped.append(f"{checked['host']}:{checked['port']}")
            continue
        added.append(name)
        if not ok:
            dead.append(name)


_BATCH_PART = re.compile(r"[^A-Za-z0-9]")
_BATCH_DAY = re.compile(r"\d{2}[A-Z][a-z]{2}")


def _batch_prefix(batch) -> str:
    """`Seller-Type-DDMon` from what the Proxies page sent, each part
    cleaned to letters and digits; "" without a seller or a day."""
    if not isinstance(batch, dict):
        return ""
    seller = _BATCH_PART.sub("", str(batch.get("seller") or ""))[:24]
    kind = _BATCH_PART.sub("", str(batch.get("type") or ""))[:24]
    day = str(batch.get("tag") or "")
    if not seller or not _BATCH_DAY.fullmatch(day):
        return ""
    return "-".join(p for p in (seller, kind, day) if p)


def _batch_lock(settings, prefix: str):
    """The batch's lock where there is a store to hold it."""
    if not getattr(settings, "store_enabled", False):
        return contextlib.nullcontext()
    from .store import pool_archive

    return pool_archive.batch_lock(settings, prefix)


def _batch_names(book, settings, prefix: str, count: int) -> list[str]:
    """A batch's next `count` names. Its numbers go on from the highest
    any proxy of it ever carried, in the pool or in the archive - read from
    the tables, under the batch's lock - so a name is never given twice."""
    taken = [r.name or "" for r in book.proxies._rows]
    if getattr(settings, "store_enabled", False):
        from .store import pool_archive

        taken += pool_archive.batch_names(settings, prefix)
    number = re.compile(re.escape(prefix) + r"-(\d+)$", re.IGNORECASE)
    top = max([int(m.group(1)) for m in map(number.match, taken) if m] or [0])
    return [f"{prefix}-{top + i}" for i in range(1, count + 1)]


def _cap_of(payload: dict) -> int:
    """Phones a day from a press: a whole number 0-99, 0 for no cap."""
    try:
        return min(99, max(0, int(str(payload.get("cap") or 0).strip() or 0)))
    except ValueError:
        return 0


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
        return False, "", "no IranSpoty Cloud client on this pass"
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
        except (proxy_mod.ProxyError, ApiError, TransportError) as exc:
            return id(resource), (False, "", str(exc)[:200])

    if not rows:
        return {}
    with ThreadPoolExecutor(max_workers=min(_TEST_WIDTH, len(rows)),
                            thread_name_prefix="free-all") as pool:
        return dict(pool.map(one, rows))


def _forgive(settings, resource, by: str) -> None:
    from . import exit_health

    exit_health.forgive_host(
        settings, str(getattr(getattr(resource, "proxy", None), "host", "")
                      or ""), by=by, exit_key=exit_health.exit_key(resource))


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
    # The guard the button promises ("only if the build that took it is
    # gone"), the one `remove_proxy` has: a row a build claimed seconds
    # ago is drawn as `starting` with a Free door on it, and Free tested
    # the exit (it answers - it is in use, not broken) and put it back
    # on the shelf under the build, so the next build took the same
    # exit and two phones sat behind one address (2026-09-21, found by
    # audit).
    pool = book.proxies
    status = pool.status_of(resource)
    if status in (pool.spent_status, pool.claimed_status):
        return ("refused", f"{resource.name} is {status} - a phone is behind "
                           f"it; it can be freed once that build is gone",
                None)
    # Set aside, or waiting for a new address, under a phone that still
    # carries it: freeing it would hand the next build an address a phone
    # is on. Turning it back on keeps it on that phone (unshelve_proxy).
    phone = (resource.values.get(pool.serial_column) or "").strip()
    if phone:
        return ("refused", f"{resource.name} is {status} under phone {phone} "
                           f"- it can be freed once that phone is gone", None)
    ok, exit_ip, why = _test(book, client, resource, tries=3)
    if not ok:
        book.proxies.fail(resource, book.proxies.dead_status, note=(
            f"Marked free from the web by {_by(payload)} on {_stamp()}, but "
            f"it did not answer in three tries over half a minute: {why}"))
        return ("failed", f"{resource.name} did not answer in three tries: "
                          f"{why}", None)
    note = (f"IP changed - marked free from the web by {_by(payload)} on "
            f"{_stamp()}. Its host is judged afresh from here.")
    if status == pool.shelved_status:
        # Off a person's shelf: `release` keeps a shelf on purpose (a phone
        # going is not a change of mind), so it left a set-aside exit set
        # aside after a passing test (found porting the Proxies page,
        # 2026-10-02).
        pool.unshelve(resource, note=note)
    else:
        pool.release(resource, note=note)
    if exit_ip:
        book.proxies.record_exit(resource, exit_ip)
    _stamp_test(settings, resource.name, True, exit_ip)
    # The person says the address changed: the week's verdict on the host
    # is about the old address, and the day's captcha strikes too.
    _forgive(settings, resource, _by(payload))
    return ("done", f"{resource.name} is free again (exit {exit_ip}); its "
                    f"host is judged afresh from now", None)


def keep_proxy_for(book, ledger, settings, payload, client):
    """Label one exit with the lane it is kept for - gpt, spotify, or
    blank for either (purposes.py). A person's choice; nothing is
    tested. The row keeps its status and its phone."""
    resource, refused = _named(book, payload)
    if refused:
        return refused
    said = str(payload.get("purpose") or "").strip()
    lane = purposes.normal(said)
    if said and not lane:
        return "refused", f"{said!r} is not a lane - GPT or Spotify", None
    book.proxies.keep_for(resource, lane)
    return ("done", f"{resource.name} is kept for "
                    f"{purposes.word(lane) if lane else 'either lane'}",
            {"purpose": lane})


def cap_proxy(book, ledger, settings, payload, client):
    """An exit's daily cap: at most N phones a Tehran day, 0 for none
    (rev 44; set from the Proxies page, 2026-10-02). A person's choice:
    nothing is tested, and a phone already on the exit keeps it."""
    resource, refused = _named(book, payload)
    if refused:
        return refused
    cap = _cap_of(payload)
    book.proxies.set_daily_cap(resource, cap)
    if not cap:
        return "done", f"{resource.name} has no daily cap", {"cap": 0}
    return ("done", f"{resource.name} takes at most {cap} "
                    f"phone{'' if cap == 1 else 's'} a day", {"cap": cap})


def unshelve_proxy(book, ledger, settings, payload, client):
    """Back in play while a phone still carries it: the set-aside on an
    exit under a phone, undone (the Proxies page's switch, 2026-10-02).
    It is `on a phone` again, so it returns to the shelf when that phone
    goes. One no phone is on goes back by `mark_proxy_free`, which tests
    it first."""
    resource, refused = _named(book, payload)
    if refused:
        return refused
    pool = book.proxies
    phone = (resource.values.get(pool.serial_column) or "").strip()
    if pool.status_of(resource) not in (pool.shelved_status,
                                        pool.needs_new_ip) or not phone:
        return ("refused", f"{resource.name} is not set aside under a phone "
                           f"- turning it on tests it and frees it", None)
    pool.keep_on_phone(resource, note=(
        f"Kept in play from the web by {_by(payload)} on {_stamp()}: the "
        f"phone on it keeps it, and it goes back on the shelf once that "
        f"phone is gone."))
    return ("done", f"{resource.name} stays in play - back on the shelf "
                    f"once phone {phone} goes", None)


def _named_many(book, payload) -> tuple[list, list[str]]:
    """The rows `names` points at, and a sentence for each that is not one."""
    rows, missing = [], []
    for name in payload.get("names") or []:
        resource = book.proxies.find_by_name(str(name or "").strip())
        if resource is None or resource.proxy is None:
            missing.append(f"{name or '?'} is not one row in the Proxy tab")
        else:
            rows.append(resource)
    return rows, missing


def free_proxies(book, ledger, settings, payload, client):
    """Free several exits at once - the Proxies page's batch and bulk
    switch (the audit, 2026-10-02). Each is tested as Free tests one, three
    tries fifteen seconds apart, but together: ten silent exits cost the
    lane half a minute, where one Free after another cost five. One that
    answers is off the shelf, or freed, with its host judged afresh; one
    that does not is dead and is retested with the dead ones."""
    if client is None:
        return "failed", "no IranSpoty Cloud client on this pass", None
    pool = book.proxies
    named, refused = _named_many(book, payload)
    rows = []
    for resource in named:
        status = pool.status_of(resource)
        phone = (resource.values.get(pool.serial_column) or "").strip()
        if status in (pool.spent_status, pool.claimed_status) or phone:
            refused.append(f"{resource.name} has a phone behind it")
        else:
            rows.append(resource)
    answers, waiting = {}, list(rows)
    for attempt in range(3):
        if attempt:
            time.sleep(15.0)
        got = _test_many(client, waiting)
        answers.update(got)
        waiting = [r for r in waiting if not got[id(r)][0]]
        if not waiting:
            break
    freed, dead = [], []
    for resource in rows:
        ok, exit_ip, why = answers[id(resource)]
        name = str(resource.name or resource.label)
        if not ok:
            pool.fail(resource, pool.dead_status, note=(
                f"Turned on from the web by {_by(payload)} on {_stamp()}, but "
                f"it did not answer in three tries over half a minute: {why}"))
            _stamp_test(settings, name, False, "")
            dead.append(name)
            continue
        note = (f"Turned on from the web by {_by(payload)} on {_stamp()}: it "
                f"answers, and its host is judged afresh from here.")
        if pool.status_of(resource) == pool.shelved_status:
            pool.unshelve(resource, note=note)
        else:
            pool.release(resource, note=note)
        if exit_ip:
            pool.record_exit(resource, exit_ip)
        _stamp_test(settings, name, True, exit_ip)
        _forgive(settings, resource, _by(payload))
        freed.append(name)
    bits = [f"{len(freed)} back in play"]
    if dead:
        bits.append(f"{len(dead)} did not answer and {'is' if len(dead) == 1 else 'are'} dead")
    if refused:
        bits.append(f"{len(refused)} not freed")
    return ("done" if freed or not dead else "failed", ", ".join(bits),
            {"freed": freed, "dead": dead, "refused": refused})


def test_proxies(book, ledger, settings, payload, client):
    """Test several exits at once - the Proxies page's bulk and batch Test
    (the audit, 2026-10-02) - each as Test does one: a dead one that answers
    is back, a free one that does not is dead, and the address each comes
    out at is recorded."""
    if client is None:
        return "failed", "no IranSpoty Cloud client on this pass", None
    pool = book.proxies
    rows, refused = _named_many(book, payload)
    answers = _test_many(client, rows)
    answered, silent = [], []
    for resource in rows:
        ok, exit_ip, why = answers[id(resource)]
        name = str(resource.name or resource.label)
        was = pool.status_of(resource)
        _stamp_test(settings, name, ok, exit_ip)
        if ok:
            if was == pool.dead_status:
                pool.release(resource, note=(
                    f"Answered again on {_stamp()} - tested from the web by "
                    f"{_by(payload)}."))
                _forgive(settings, resource, _by(payload))
            if exit_ip:
                pool.record_exit(resource, exit_ip)
            answered.append(name)
            continue
        if was in pool.available_statuses:
            pool.fail(resource, pool.dead_status, note=(
                f"Did not answer on {_stamp()} - tested from the web by "
                f"{_by(payload)}: {why}"))
        silent.append(name)
    bits = [f"{len(answered)} answered"]
    if silent:
        bits.append(f"{len(silent)} did not")
    if refused:
        bits.append(f"{len(refused)} not found")
    return ("done" if answered or not silent else "failed", ", ".join(bits),
            {"answered": answered, "silent": silent, "refused": refused})


def _shelve(book, resource, payload) -> None:
    pool = book.proxies
    on_phone = pool.status_of(resource) == pool.spent_status
    pool.shelve(resource, note=(
        f"Set aside from the web by {_by(payload)} on {_stamp()}: "
        + ("the phone on it stays; once that phone is gone the exit is "
           "never handed to another build."
           if on_phone else
           "kept out of the builds until freed by hand.")
        + " The cloud still holds it."))


def _shelvable(pool, resource) -> str:
    """Why this row cannot be set aside, or "" when it can. A build that
    has just taken one is the only holder that says no: the exit is on
    its way onto a phone that does not exist yet, and a one-off is that
    build's own. A phone already on one is fine - the phone keeps it and
    nothing else ever gets it."""
    status = pool.status_of(resource)
    if status == pool.shelved_status:
        return "is already set aside"
    if status == pool.claimed_status:
        return "was just taken by a build - set it aside once its phone is up"
    if status == pool.one_off_status:
        return "is a one-off typed for a build that is still on its way"
    return ""


def shelve_proxy(book, ledger, settings, payload, client):
    """Set aside: one exit off the shelf, kept, until Free puts it back
    (the operator, 2026-09-28). Free, dead, wanting an address, suspect -
    or under a phone, which keeps it and never passes it on. Nothing is
    tested and nothing is judged: a person's choice, not a verdict."""
    resource, refused = _named(book, payload)
    if refused:
        return refused
    why = _shelvable(book.proxies, resource)
    if why:
        return "refused", f"{resource.name} {why}", None
    _shelve(book, resource, payload)
    return ("done", f"{resource.name} is set aside - Free on its row puts "
                    f"it back", None)


def shelve_all_proxies(book, ledger, settings, payload, client):
    """Set aside all: every exit the builds could ever reach again off
    the shelf in one press - free, dead, wanting an address, suspect, and
    the ones under a phone, which keep their phone and are never handed
    on - so a fresh batch can be poured in and be the only stock (the
    operator, 2026-09-28). Only what a build has just taken, a one-off,
    and what is already set aside stay as they are."""
    pool = book.proxies
    named, kept = [], 0
    for resource in list(pool._rows):
        if _shelvable(pool, resource):
            continue
        if pool.status_of(resource) == pool.spent_status:
            kept += 1
        _shelve(book, resource, payload)
        named.append(resource.name or resource.label)
    if not named:
        return "done", "no exit to set aside", {"shelved": []}
    said = (f"{len(named)} exit{'' if len(named) == 1 else 's'} set aside "
            f"by {_by(payload)} - Free on a row puts one back")
    if kept:
        said += (f"; {kept} of them {'stays' if kept == 1 else 'stay'} under "
                 f"{'its' if kept == 1 else 'their'} phone until it goes")
    return "done", said, {"shelved": named}


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
    from . import keeper

    if client is None:
        return "failed", "no IranSpoty Cloud client on this pass", None
    dead, revived = keeper.check_proxies(client, book)
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
        return "failed", "no IranSpoty Cloud client on this pass", None
    from .pools import ProxyPool

    held = (ProxyPool.held_back_statuses + (book.proxies.dead_status,))
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


def free_shelved_proxies(book, ledger, settings, payload, client):
    """Free all set aside: every exit a person put on the shelf, back in
    one press - tested first, like Free all; one that answers is stock
    again with its host judged afresh, one that does not is dead and is
    retested with the dead ones (the operator, 2026-09-29: the old batch
    parked, then wanted back)."""
    if client is None:
        return "failed", "no IranSpoty Cloud client on this pass", None
    pool = book.proxies
    rows = [r for r in pool._rows
            if not r.error and r.proxy
            and pool.status_of(r) == pool.shelved_status]
    if not rows:
        return "done", "nothing is set aside by hand", None
    answers = _test_many(client, rows)
    freed, silent = [], []
    for resource in rows:
        name = str(getattr(resource, "name", "") or resource.label)
        ok, exit_ip, why = answers[id(resource)]
        if not ok:
            pool.fail(resource, pool.dead_status, note=(
                f"Taken off the shelf by {_by(payload)} on {_stamp()}, but "
                f"it did not answer: {why}. Retested with the dead ones."))
            silent.append(name)
            _stamp_test(settings, name, False, "")
            continue
        pool.unshelve(resource, note=(
            f"Taken off the shelf by {_by(payload)} on {_stamp()} - it "
            f"answers, and its host is judged afresh from here."))
        if exit_ip:
            pool.record_exit(resource, exit_ip)
        _stamp_test(settings, name, True, exit_ip)
        _forgive(settings, resource, _by(payload))
        freed.append(name)
    said = f"{len(freed)} exit(s) are back in the pool"
    if silent:
        said += (f"; {len(silent)} did not answer and are dead until they do "
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
    phone = (resource.values.get(book.proxies.serial_column) or "").strip()
    if phone:
        return ("refused", f"{resource.name} is {status} under phone {phone} "
                           f"- it can be removed once that phone is gone", None)
    kept = {"name": resource.name,
            "raw": (resource.values.get("Proxy String") or str(resource.proxy)),
            "status": status, "note": resource.values.get("Note", "")}
    book.proxies.delete_row(resource, by=_by(payload))
    return ("done", f"{resource.name} removed from the pool (IranSpoty Cloud "
                    f"still holds it - remove it there by hand)",
            {"removed": kept})


def sweep_forgotten(book, ledger, settings, payload, client):
    """The forgotten-phones sweep, now: a Live tab said it was closing,
    and the pass that would have switched its phone off could be minutes
    away (the operator, 2026-09-29). The same sweep, on a fresh listing,
    so a reload's beat in the meantime still spares the phone."""
    if client is None:
        return "failed", "no IranSpoty Cloud client on this pass", None
    from . import forgotten
    from . import phones as phones_mod

    outcome = forgotten.sweep(client, settings, ledger,
                              listing=phones_mod.listing(client))
    off, back = outcome.get("off") or [], outcome.get("released") or []
    if not off and not back:
        return "done", "nothing to put back yet", outcome
    return ("done", f"{len(back)} phone(s) put back"
                    + (f", {len(off)} switched off" if off else ""), outcome)


# ------------------------------------------------------- the phones (C6)
def login_accounts(book, ledger, settings, payload, client, launch=None):
    """"Log in selected": N chosen accounts onto N warm phones, at once.

    The person chose the accounts, so each is claimed by name - `claim_this`
    - and paired with the next warm phone; the pairs become finish jobs the
    pass launches together. An account with no warm phone left is said so
    and left free: the Keeper builds the shortfall, and the person presses
    the button again. Nothing here waits: the sentence says what started.
    """
    from . import keeper

    addresses = [a.strip() for a in payload.get("addresses") or [] if a.strip()]
    if not addresses:
        return "refused", "no account was chosen", None
    if client is None or launch is None:
        return "failed", "this pass cannot start phone work", None
    # Asked as the person pressing the button, so a phone they are
    # keeping is theirs to send to. Without this, holding a phone and
    # using it were the same door and could not both be open (3644,
    # 2026-09-19).
    warm, _gone = keeper._unfinished(
        client, book, for_owner=str(payload.get("by_id") or ""),
        busy=keeper._busy_serials(settings))
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
        # Only a phone of the account's lane: a Spotify account never
        # spends a GPT phone, nor the other way round. A phone with no
        # lane on it - built before there were lanes - takes either.
        lane = purposes.of_product(resource.values.get("Product"))
        phone = before = None
        lost_chosen = False
        while phone is None:
            fit = next((i for i, p in enumerate(warm)
                        if purposes.fits(p.get("purpose"), lane)), None)
            if fit is None:
                break
            phone = warm.pop(fit)
            # Reserved before it is paired: a Take between the warm list
            # and the job would hand somebody a phone a finish then signs
            # into. One that cannot be reserved is gone - the next one.
            reserved, before = _reserve(settings, payload, phone)
            if not reserved:
                lost_chosen = bool(chosen)
                phone = None
        if phone is None:
            if lost_chosen:
                refused.append(f"{address}: phone {chosen} cannot take an "
                               f"account right now - somebody holds it")
                continue
            if chosen and warm:
                other = purposes.word(warm[0].get("purpose"))
                refused.append(f"{address}: phone {chosen} is a {other} "
                               f"phone, and this is a {purposes.word(lane)} "
                               f"account")
                continue
            unpaired.append(address)
            continue
        if not book.apps.claim_this(resource, str(phone["serial"])):
            refused.append(f"{address}: taken by another run meanwhile")
            if before is not None:
                _guarded(settings, False, _store_station().unreserve_warm,
                         str(phone["serial"]), before)
            warm.insert(0, phone)
            continue
        paired = {**phone, "account": resource}
        if before is not None:
            paired["status_before"] = before
        jobs.append({"kind": "finish", "phone": paired})
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


def _store_station():
    """The Station's store module, imported where it is used."""
    from .store import station as store_station

    return store_station


def _reserve(settings, payload: dict,
             phone: dict) -> tuple[bool, str | None]:
    """Reserve a warm phone for a pairing: (whether it may be paired, the
    status the store had it at or None when the store reserved nothing).
    With no store there is nothing to ask, and a store that will not
    answer reads as reserved, which is the old way."""
    if not _store_on(settings):
        return True, None
    try:
        before = _store_station().reserve_warm(
            settings, str(phone["serial"]), owner_id=_uid(payload) or None)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("phone %s was not reserved before its pairing (%s)",
                    phone.get("serial"), exc)
        return True, None
    return before is not None, before


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
    # Its id in the archive, so Undo puts the very row back - both of its
    # factors, its place and its tries - rather than a fresh one
    # (restore_gmail, 2026-10-07).
    kept["Id"] = resource.store_id
    book.gmails.delete_row(resource, by=_by(payload))
    return ("done", f"{address} removed from the pool by {_by(payload)} - "
                    f"archived, not deleted", {"removed": kept})


# ------------------------------------------------------- the Gmails page
#: The Gmails page's presses (2026-10-07): store writes of milliseconds,
#: each guarded by where a Gmail stands now (store.gmail_desk), so a press
#: made on a page drawn a minute ago never moves a Gmail a build took
#: since. Each answers which Gmails it moved and how they were before -
#: the detail its Undo (`gmails_revert`) takes back - and why the rest
#: stayed where they are.
DESK_VERBS = ("gmails_aside", "gmails_queue", "gmails_mend",
              "gmails_keep_for", "gmails_remove", "gmail_save")
#: How long after a press its Undo is still offered and taken.
UNDO_MINUTES = 15
_LEFT = {"phone": "on a phone", "spent": "spent", "gone": "no longer in the pool",
         "same": "already that way",
         "unreadable": "with details the farm cannot read"}


def _plural_gmails(n: int) -> str:
    return f"{n} Gmail" + ("" if n == 1 else "s")


def _desk_answer(got: dict, did: str, by: str) -> tuple:
    """(status, sentence, detail) for one press of the page: the sentence
    for Requests, the detail for the page and its Undo."""
    changed, left = got.get("changed") or [], got.get("left") or {}
    stayed = {}
    for why in left.values():
        stayed[why] = stayed.get(why, 0) + 1
    rest = "; ".join(f"{n} {_LEFT.get(w, w)}" for w, n in sorted(stayed.items()))
    said = (f"{_plural_gmails(len(changed))} {did} by {by}"
            + (f" ({rest})" if rest else ""))
    detail = {"ids": [c["id"] for c in changed],
              "left": {str(k): v for k, v in left.items()},
              "changes": changed}
    return "done", said, detail


def _desk_refused(exc) -> tuple:
    return "refused", str(exc), None


def gmails_aside(book, ledger, settings, payload, client):
    """Set aside by hand: the free and waiting Gmails named go out of the
    queue until a person turns them on again."""
    from .store import gmail_desk

    try:
        got = gmail_desk.aside(settings, payload.get("ids"), by=_by(payload))
    except gmail_desk.Refused as exc:
        return _desk_refused(exc)
    return _desk_answer(got, "set aside", _by(payload))


def gmails_queue(book, ledger, settings, payload, client):
    """Back in the queue: the Gmails named that are out of it go in again -
    a waiting one set aside waits again for the hour it had."""
    from .store import gmail_desk

    try:
        got = gmail_desk.queue(settings, payload.get("ids"), by=_by(payload))
    except gmail_desk.Refused as exc:
        return _desk_refused(exc)
    return _desk_answer(got, "back in the queue", _by(payload))


def gmails_mend(book, ledger, settings, payload, client):
    """Marked fixed: refused Gmails mended by the seller or by hand go back
    to the pool as fresh stock, their tries started again."""
    from .store import gmail_desk

    try:
        got = gmail_desk.mend(settings, payload.get("ids"), by=_by(payload))
    except gmail_desk.Refused as exc:
        return _desk_refused(exc)
    return _desk_answer(got, "marked fixed", _by(payload))


def gmails_keep_for(book, ledger, settings, payload, client):
    """Kept for one product, or for any again: builds for the other product
    pass these Gmails by."""
    from .store import gmail_desk

    lane = str(payload.get("lane") or "").strip().lower()
    try:
        got = gmail_desk.keep_for(settings, payload.get("ids"), lane,
                                  by=_by(payload))
    except gmail_desk.Refused as exc:
        return _desk_refused(exc)
    word = (f"kept for {gmail_desk.LANE_WORD[lane]}" if lane
            else "open to any product")
    status, said, detail = _desk_answer(got, word, _by(payload))
    detail["lane"] = lane
    return status, said, detail


def gmails_remove(book, ledger, settings, payload, client):
    """Out of the pool and into the archive, whole: unused Gmails and
    spent ones - never one a phone is behind."""
    from .store import gmail_desk

    try:
        got = gmail_desk.remove(settings, payload.get("ids"), by=_by(payload))
    except gmail_desk.Refused as exc:
        return _desk_refused(exc)
    return _desk_answer(got, "removed to the archive", _by(payload))


def gmail_save(book, ledger, settings, payload, client):
    """One Gmail's details as its form left them - and, asked to, marked
    fixed in the same go. The sentence names what changed, never a value."""
    from .store import gmail_desk

    try:
        # None is a field the person did not touch (gmail_desk.save).
        got = gmail_desk.save(
            settings, int(payload.get("id") or 0),
            password=payload.get("password"), key=payload.get("key"),
            recovery=payload.get("recovery"), note=payload.get("note"),
            fixed=bool(payload.get("fixed")), by=_by(payload))
    except (gmail_desk.Refused, ValueError) as exc:
        return _desk_refused(exc)
    words = got["said"] or "nothing different"
    said = (f"{got['address']} saved by {_by(payload)}: {words}"
            + ("; marked fixed" if got["mended"] else "")
            + ("; it was not refused, so it is not marked fixed"
               if got["unfixable"] else ""))
    return "done", said, {"ids": [got["id"]], "said": got["said"],
                          "mended": got["mended"],
                          "unfixable": got["unfixable"],
                          "changes": got["changed"]}


def gmails_add(book, ledger, settings, payload, client):
    """A paste from the Gmails page: new Gmails into a batch, kept for a
    product if one was chosen, and refused ones of the pool back fixed
    with the details on their lines. Every line is judged on its own; the
    ones refused say why."""
    from .store import gmail_desk

    try:
        got = gmail_desk.add(
            settings, list(payload.get("rows") or []),
            seller=str(payload.get("seller") or ""),
            lane=str(payload.get("lane") or ""), by=_by(payload),
            by_id=_uid(payload) or None, back=list(payload.get("back") or []),
            carry=list(payload.get("carry") or []))
    except gmail_desk.Refused as exc:
        return _desk_refused(exc)
    added, returned, refused = got["added"], got["returned"], got["refused"]
    bits = []
    if added:
        bits.append(f"{_plural_gmails(len(added))} added to "
                    f"{got['seller'] or 'no batch'}")
    if returned:
        bits.append(f"{_plural_gmails(len(returned))} back fixed")
    if refused:
        bits.append(f"{len(refused)} refused")
    said = (", ".join(bits) or "nothing added") + f" by {_by(payload)}"
    return ("done" if added or returned else "failed"), said, {
        "added": [a["id"] for a in added],
        "addresses": [a["address"] for a in added],
        "returned": [{"id": r["id"], "said": r["said"], "mended": bool(r["mended"])}
                     for r in returned],
        # Not `refused`: Requests draws that key as one line of text each.
        "left_out": refused,
        "ids": [r["id"] for r in returned],
        "carried": [c["id"] for c in got["carried"]],
        "lane": got["lane"],
        "changes": [c for r in returned for c in r["changed"]]}


def restore_gmail(book, ledger, settings, payload, client):
    """Undo of a Remove: the archived Gmail back in the pool exactly as it
    left, under its own id - unless its address is in the pool again."""
    from .store import gmail_desk

    raw = str(payload.get("id") or "")
    if not raw.isdecimal():
        return "refused", "No Gmail was named.", None
    got = gmail_desk.restore(settings, int(raw), by=_by(payload))
    if got is None:
        return ("refused", "It cannot come back: it is no longer in the archive,"
                           " or its address is in the pool again.", None)
    return ("done", f"{got['address']} put back as it was by {_by(payload)}",
            {"ids": [got["id"]]})


def gmails_revert(book, ledger, settings, payload, client):
    """The page's Undo: the presses named, taken back - by the person who
    made them, within UNDO_MINUTES, and each Gmail only if nothing has
    moved it since. The rest stay as they are now, and the sentence says
    how many."""
    import datetime

    from .store import actions as store_actions
    from .store import gmail_desk

    who = _uid(payload)
    reqs = sorted({int(r) for r in payload.get("reqs") or []
                   if str(r).strip().isdecimal()})
    changes, took = [], []
    soon = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        minutes=UNDO_MINUTES)
    for req in reqs:
        row = store_actions.one(settings, req)
        if (row is None or row.get("verb") not in DESK_VERBS
                or row.get("status") != "done"
                or int(row.get("requested_by") or 0) != who
                or not row.get("requested_at") or row["requested_at"] < soon):
            continue
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        changes.extend(detail.get("changes") or [])
        took.append(req)
    if not took:
        return ("refused", "That change can no longer be taken back here - "
                           "it is older, someone else's, or not a change "
                           "of this page.", None)
    got = gmail_desk.revert(settings, changes, by=_by(payload))
    back, moved = got["back"], got["moved"]
    said = (f"{_plural_gmails(len(back))} put back as they were by "
            f"{_by(payload)}" + (f"; {len(moved)} had moved on since and "
                                 f"stay as they are" if moved else ""))
    return "done", said, {"reqs": took, "back": back, "moved": moved}


def _app_row(book, payload, *, delivered_goes=False):
    """The GPT row this command names, or the refusal that says why not.

    Same rule as `_gmail_row`, for the same reason: a row a phone is
    behind is not stock to edit or tidy away. A delivered one is not
    either - Free or Edit would put it back where a build could take it
    - but no phone is behind it (handing it over blanked the serial), so
    Remove may take it out of the pool: `delivered_goes`. The one
    exception is a row the panel handed in, which its API reads the
    order back from (the operator, 2026-09-27).
    """
    address = (payload.get("address") or "").strip()
    resource = book.apps.find(address)
    if resource is None:
        return None, ("failed", f"{address or '?'} is not in the "
                                f"{book.apps.tab} tab", None)
    status = book.apps.status_of(resource)
    if status == book.apps.retired_status:
        source = str((resource.values or {}).get("Source") or "")
        if source.strip().lower() == "panel":
            return None, ("refused", f"{address} was delivered to a panel "
                                     f"customer - the panel still reads "
                                     f"this row, so it stays", None)
        if delivered_goes:
            return resource, None
        return None, ("refused", f"{address} is delivered - it went out on "
                                 f"a phone; Remove takes it out of the "
                                 f"pool", None)
    if status in (book.apps.claimed_status, book.apps.spent_status):
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
    resource, refused = _app_row(book, payload, delivered_goes=True)
    if refused:
        return refused
    address = str(resource.values.get("Address") or "")
    # Product and category too, or Undo puts a Spotify row back as a
    # GPT account (2026-09-17).
    kept = {name: str(resource.values.get(name) or "")
            for name in ("Address", "Password", "2FA Secret",
                         book.apps.EMAIL_CODE_COLUMN, "Product", "Category")}
    # So Undo can tell a delivered row, which must not come back as stock.
    kept["Status"] = book.apps.status_of(resource)
    book.apps.delete_row(resource, by=_by(payload))
    return ("done", f"{address} removed from the pool by {_by(payload)} - "
                    f"archived, not deleted", {"removed": kept})


def remove_delivered_apps(book, ledger, settings, payload, client):
    """Every delivered account of one pool - `spotify` or `gpt` - out of
    it and into the archive, in one press: 25 delivered Spotify accounts
    were 25 presses and 25 confirms (the operator, 2026-09-27).

    What `remove_app` does to one delivered row, row by row, with its
    one exception kept: a row the panel handed in stays, since its API
    reads the order back from it. The addresses ride in the detail and
    nothing else - no password, because no Undo puts a delivered account
    back as stock; the archive holds each row whole.
    """
    pool = str(payload.get("pool") or "").strip().lower()
    if pool not in ("spotify", "gpt"):
        return "refused", f"{pool or '?'} is not an account pool", None
    name = "Spotify" if pool == "spotify" else "GPT"
    removed, panel = [], 0
    for resource in list(book.apps._rows):
        if book.apps.status_of(resource) != book.apps.retired_status:
            continue
        values = resource.values or {}
        product = str(values.get("Product") or "").strip().lower()
        if (product == "spotify") != (pool == "spotify"):
            continue
        if str(values.get("Source") or "").strip().lower() == "panel":
            panel += 1
            continue
        book.apps.delete_row(resource, by=_by(payload))
        removed.append(str(values.get("Address") or ""))
    kept = f"; {panel} kept for the panel" if panel else ""
    if not removed:
        return ("done", f"no delivered {name} account to remove{kept}",
                {"removed": []})
    what = f"delivered {name} account" + ("" if len(removed) == 1 else "s")
    return ("done", f"{len(removed)} {what} removed from the pool by "
                    f"{_by(payload)} - archived, not deleted{kept}",
            {"removed": removed})


#: The Gmail rows no group verb may touch: free, set aside by hand, or on
#: a phone - the manager's `current` chip (pages._group_of).
_GMAIL_CURRENT = frozenset({"", "free", "in_use", "ready", "set_aside",
                            "set aside"})


def _gmail_group(book, resource) -> str:
    """Which of the manager's chips a Gmail row is under, read the way
    the manager reads it (web.read._pool_state, pages._group_of): an
    unreadable row is errored whatever its status says."""
    if resource.error:
        return "errored"
    status = book.gmails.status_of(resource)
    if status == book.gmails.retired_status:
        return "spent"
    return "current" if status in _GMAIL_CURRENT else "errored"


def remove_gmail_group(book, ledger, settings, payload, client):
    """Every Gmail under one of the manager's chips - `spent` or
    `errored` - out of the pool and into the archive, in one press (the
    operator, 2026-09-28). What `remove_gmail` does to one row, row by
    row; `current` is never a choice, so a free row, one set aside by
    hand, or one on a phone cannot be reached from here."""
    group = str(payload.get("group") or "").strip().lower()
    if group not in ("spent", "errored"):
        return ("refused", f"{group or '?'} is not a group Remove all takes",
                None)
    removed = []
    for resource in list(book.gmails._rows):
        if _gmail_group(book, resource) != group:
            continue
        book.gmails.delete_row(resource, by=_by(payload))
        removed.append(str((resource.values or {}).get("Address") or ""))
    if not removed:
        return "done", f"no {group} Gmail to remove", {"removed": []}
    what = f"{group} Gmail" + ("" if len(removed) == 1 else "s")
    return ("done", f"{len(removed)} {what} removed from the pool by "
                    f"{_by(payload)} - archived, not deleted",
            {"removed": removed})


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
    from . import cancel

    serial = str(payload.get("serial") or "").strip()
    if not serial:
        return "refused", "no phone named", None
    cancel.STOP_BY_HAND.add(serial)
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

    The Station queues it too: a give-back, the hour, and a Live tab
    that closed (`why` = closed). That last one is asked again under the
    phone's power lock - a tab that is beating once more keeps its phone
    on. Every stop, and every "already off", is written to the store, so
    the card and the shelf read the phone off at once.
    """
    from . import phones as phones_mod
    from .phones import PhoneError

    serial = str(payload.get("serial") or "").strip()
    if not serial:
        return "refused", "no phone named", None
    if client is None:
        return "failed", "no IranSpoty Cloud client on this pass", None
    why = str(payload.get("why") or "")
    station = _store_station()
    with phones_mod.power_lock(serial):
        if why in ("given back", "alone") and _guarded(
                settings, None, station.station_holder, serial) is not None:
            # The give-back's own power-off, run after somebody took the
            # phone from the shelf again: it is theirs now, and so is its
            # power.
            return ("done", f"phone {serial} was taken again - left on", None)
        # Listed inside the lock: a Boot or a stop that held the lock a
        # moment ago has changed what the cloud says.
        live = next((p for p in phones_mod.listing(client)
                     if str(p.get("serialNo")) == serial), None)
        if live is None:
            return ("failed", f"phone {serial} is not in the cloud's phone list",
                    None)
        held = ledger.get(live["id"]) if ledger is not None else None
        if held is not None and held.is_claimed and not held.is_stale:
            return ("refused", f"phone {serial} is held by a run ({held.label})",
                    None)
        if why == "closed" and _guarded(settings, False, station.is_watched,
                                        serial, _grace(settings)):
            # Given back meanwhile, it is no Station hold any more and
            # reads unwatched: it is stopped.
            return ("done", f"phone {serial} is being watched again - left on",
                    None)
        if live.get("status") not in (phones_mod.RUNNING, phones_mod.STARTING):
            _guarded(settings, False, station.powered_off, serial)
            return "done", f"phone {serial} was already off", None
        try:
            phones_mod.stop(client, live["id"])
        except (PhoneError, ApiError, TransportError) as exc:
            log.warning("phone %s would not stop: %s", serial, exc)
            return ("failed", f"phone {serial} would not stop - it is tried "
                              f"again", _code(exc))
        _guarded(settings, False, station.powered_off, serial)
    return "done", f"phone {serial} is off - it stops billing", {"off": True}


def _code(exc) -> dict | None:
    """The vendor's error code for a page that needs it, never its words."""
    code = getattr(exc, "code", None)
    return {"code": code} if code is not None else None


def _grace(settings) -> int:
    """How long a Live tab's beat may be silent before it counts as closed."""
    return int(getattr(settings, "live_tab_grace_seconds", 45) or 45)


def _stop_quietly(phones_mod, client, phone_id: str, serial: str) -> None:
    """A best-effort stop of a phone this press started and must not keep
    running. Logged when it fails; the forgotten sweep is the backstop."""
    try:
        phones_mod.stop(client, phone_id)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("phone %s was left running after its hold ended (%s)",
                    serial, exc)


def _refused_by_holder(settings, serial: str, uid: int) -> str:
    """The refusal a dashboard press gets on somebody's Station hold, or ""."""
    holder = _guarded(settings, None, _store_station().station_holder, serial)
    if holder is not None and holder != uid:
        return f"phone {serial} is on somebody's station"
    return ""


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

    The new exit is the phone's own lane's, checked before it is used
    (a dead one is marked and the next one tried), and the address it
    came out of is written on the phone. The Station's press keeps the
    power the page showed (`keep_power`, `was_on_page`): off stays off,
    on comes back on. The old exit goes back free only when no other
    phone is still on it.
    """
    from . import phones as phones_mod
    from .cancel import Aborted
    from .kit import exits as kit_exits
    from .phones import PhoneError

    serial = str(payload.get("serial") or "").strip()
    station = bool(payload.get("station"))
    uid = _uid(payload)
    row = next((r for r in book.phones.rows()
                if str(r.get("Serial") or "").strip() == serial), None)
    if row is None:
        return "failed", f"phone {serial or '?'} is not in the Phones tab", None
    if row.get("Status") == book.phones.BUILDING:
        return "refused", f"phone {serial} is being worked on right now", None
    if client is None:
        return "failed", "no IranSpoty Cloud client on this pass", None
    lane = purposes.phone_lane(row.get("Purpose"))
    was = (row.get("Proxy") or "").strip()
    store_station = _store_station()
    marked = {"station": True} if station else {}
    with phones_mod.power_lock(serial):
        if station:
            if _store_on(settings) and not store_station.holds(settings, serial,
                                                               uid):
                return "refused", f"phone {serial} is not yours any more", None
        else:
            refused = _refused_by_holder(settings, serial, uid)
            if refused:
                return "refused", refused, None
        # Listed inside the lock: whether the phone is on is read after
        # any other power press on it has finished.
        live = next((p for p in phones_mod.listing(client)
                     if str(p.get("serialNo")) == serial), None)
        if live is None:
            return ("failed", f"phone {serial} is not in the cloud's phone list",
                    None)
        held = ledger.get(live["id"]) if ledger is not None else None
        if held is not None and held.is_claimed and not held.is_stale:
            return ("refused", f"phone {serial} is held by a run ({held.label})",
                    None)
        was_on = live.get("status") in (phones_mod.RUNNING, phones_mod.STARTING)
        # Off stays off as the operator saw it: a card whose tab just
        # closed reads Ready while the cloud still runs the phone, and a
        # Change IP pressed on it does not start it again.
        boot = bool(payload.get("boot")) or (
            bool(payload.get("keep_power")) and was_on
            and payload.get("was_on_page") == "on")
        kept = was or "its IP"
        try:
            fresh = kit_exits._fresh_proxy(client, book, settings=settings,
                                           purpose=lane)
        except Aborted:
            word = "" if lane == purposes.OTHER else purposes.WORDS[lane] + " "
            return ("failed", f"there is no free {word}IP left - phone "
                              f"{serial} kept {kept}",
                    {"off": not was_on, **marked} if (boot or station)
                    else None)
        except (PhoneError, ApiError, TransportError) as exc:
            # The check of the new exit did not come back (the cloud's
            # hangs): `_fresh_proxy` put the exit it claimed back, and
            # nothing on the phone was touched.
            log.warning("phone %s kept its exit: the new one was not checked "
                        "(%s)", serial, exc)
            return ("failed", f"the IP could not be changed right now - phone "
                              f"{serial} kept {kept}",
                    {"off": not was_on, **marked} if (boot or station)
                    else None)
        # Whether the phone is off by the time this returns: it was, or the
        # stop below went through. Only the Live tab asks.
        off = not was_on
        try:
            if not off:
                phones_mod.stop(client, live["id"])
                # Asked to stop is as good as off for what the store says:
                # a wait that does not come back must not leave it On.
                off = True
                phones_mod.wait_until_stopped(client, live["id"])
            phones_mod.set_proxy(client, live["id"], fresh.proxy)
        except (PhoneError, ApiError, TransportError) as exc:
            log.warning("phone %s kept its exit: %s", serial, exc)
            code = getattr(exc, "code", None)
            book.proxies.release(fresh, note=(
                f"Phone {serial} would not take it on {_stamp()}"
                + (f" (code {code})." if code is not None else ".")))
            if off and was_on:
                _guarded(settings, False, store_station.powered_off, serial)
            said = f"the new IP was refused - phone {serial} kept {kept}"
            if station:
                return ("failed", said,
                        {"off": off, **marked, **(_code(exc) or {})})
            return ("failed", said,
                    dict({"off": off}, **(_code(exc) or {})) if boot
                    else _code(exc))
        old = book.proxies.find_by_name(was)
        if old is not None and old is not fresh:
            if _exit_still_shared(settings, was, serial):
                # A borrowed exit carries two phones on purpose; freeing
                # it here would hand out one a phone is still behind.
                note = (old.values.get("Note") or "").strip()
                book.proxies._set(old, {"Note": (
                    f"{note} Phone {serial} left it on {_stamp()}; another "
                    f"phone is still on it.").strip()})
            else:
                book.proxies.release(old, note=(
                    f"Left phone {serial} on {_stamp()} - proxy changed from "
                    f"the web by {_by(payload)}."))
        book.proxies.spend(fresh, serial=serial, note=(
            f"On phone {serial} since {_stamp()} - changed from the web by "
            f"{_by(payload)}."))
        name = fresh.name or str(fresh.proxy)
        book.phones.write(serial, Proxy=name, **{
            "Exit IP": str(fresh.values.get("Last Exit IP") or "").strip()})
        moved = {"was": was, "now": name}
        if not boot:
            _guarded(settings, False, store_station.powered_off, serial)
            if station:
                return ("done", f"phone {serial} is on {name} now (it is off; "
                                f"it reads the new IP when it next starts)",
                        dict(moved, started=False, station=True))
            return ("done", f"phone {serial} is on {name} now (it is stopped; "
                            f"it reads the new exit when it next starts)", moved)
        try:
            # One attempt, as Boot's: the person is watching a tab that can
            # offer Boot again in a minute.
            url = phones_mod.start(client, live["id"], attempts=1)
        except phones_mod.PhoneCapacityError:
            _guarded(settings, False, store_station.powered_off, serial)
            return ("failed", f"phone {serial} is on {name} now but IranSpoty "
                              f"Cloud has no machine free to start it - press "
                              f"Boot again in a minute",
                    dict(moved, off=True, **marked))
        except (PhoneError, ApiError, TransportError) as exc:
            log.warning("phone %s would not start on its new IP: %s", serial,
                        exc)
            _guarded(settings, False, store_station.powered_off, serial)
            return ("failed", f"phone {serial} is on {name} now but would not "
                              f"start - press Boot again in a minute",
                    dict(moved, off=True, **marked, **(_code(exc) or {})))
        if station:
            if not _guarded(settings, True, store_station.booted, serial,
                            url or "", owner_id=uid, started=True):
                # Given back while its IP changed: it is not theirs to run.
                _stop_quietly(phones_mod, client, live["id"], serial)
                _guarded(settings, False, store_station.powered_off, serial)
                return ("failed", f"phone {serial} went back to the shelf while "
                                  f"its IP changed",
                        dict(moved, off=True, station=True))
        else:
            _guarded(settings, False, store_station.booted, serial, url or "",
                     started=True)
    started = {"started": True, "station": True} if station else {}
    if not url:
        return ("done", f"phone {serial} is on {name} now and started again "
                        f"- IranSpoty Cloud gave no live-view link back",
                dict(moved, **started))
    return ("done", f"phone {serial} is on {name} now and started again",
            dict(moved, url=url, **started))


def _exit_still_shared(settings, name: str, serial: str) -> bool:
    """Whether another live phone is on the exit `name`. No store: not
    shared, as before. A store that will not answer: shared, the safe
    side - an exit left spent costs less than one handed out twice."""
    if not name or not _store_on(settings):
        return False
    try:
        return bool(_store_station().exit_shared(settings, name, serial))
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not ask whether exit %s is shared (%s); it stays "
                    "spent", name, exc)
        return True


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
    only the name beside it.

    It never takes over somebody's Station hold; a legacy hold of
    somebody else is taken over as it always was (an admin ending a
    hold), and a Station hold of the same person keeps its clock."""
    if settings is None or not getattr(settings, "store_enabled", False):
        return
    uid = int(by_id) if str(by_id or "").strip().isdigit() else None
    try:
        from .store import db as store_db

        with store_db.connect(settings) as conn:
            conn.execute(
                "UPDATE phones SET owner_id = %(uid)s,"
                " taken_at = CASE WHEN owner_id IS NOT DISTINCT FROM"
                "                      %(uid)s::bigint THEN taken_at END,"
                " updated_at = now()"
                " WHERE serial = %(s)s AND done_at IS NULL"
                "   AND (owner_id IS NULL OR owner_id = %(uid)s::bigint"
                "        OR taken_at IS NULL)", {"uid": uid, "s": str(serial)})
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

    The link is kept on the phone row, so a phone that is already on is
    never started again: its stored link is handed back instead. The
    Station's press (`station`) boots only the presser's own Station hold,
    asked again here under the phone's power lock, and never writes the
    State or the owner - its hour and its place in line are untouched.
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
        return "failed", "no IranSpoty Cloud client on this pass", None
    station = bool(payload.get("station"))
    uid = _uid(payload)
    store_station = _store_station()
    with phones_mod.power_lock(serial):
        if station:
            # Not guarded: a store that will not answer leaves the row for
            # the lane to try again, rather than booting somebody's phone.
            if _store_on(settings) and not store_station.holds(settings, serial,
                                                               uid):
                return ("refused", f"phone {serial} is not yours any more - it "
                                   f"went back to the shelf", None)
        else:
            refused = _refused_by_holder(settings, serial, uid)
            if refused:
                return "refused", refused, None
        # Listed inside the lock: a stop that held the lock a moment ago
        # has changed what the cloud says, and a phone read on from before
        # it would be handed back with a dead link and never started.
        live = next((p for p in phones_mod.listing(client)
                     if str(p.get("serialNo")) == serial), None)
        if live is None:
            return ("failed", f"phone {serial} is not in the cloud's phone list",
                    None)
        held = ledger.get(live["id"]) if ledger is not None else None
        if held is not None and held.is_claimed and not held.is_stale:
            return ("refused", f"phone {serial} is held by a run ({held.label})",
                    None)
        was_on = live.get("status") in (phones_mod.RUNNING, phones_mod.STARTING)
        url = (_guarded(settings, "", store_station.stored_link, serial)
               if was_on else "")
        started = not url
        if started:
            try:
                # One attempt, not the builder's four: a capacity refusal
                # here would sleep half a minute inside the drain, and the
                # person is watching a tab that can simply be pressed again.
                url = phones_mod.start(client, live["id"], attempts=1)
            except phones_mod.PhoneCapacityError:
                return ("failed", f"IranSpoty Cloud has no machine free for "
                                  f"{serial} right now - press Boot again in a "
                                  f"minute", None)
            except TransportError as exc:
                log.warning("phone %s: the start did not come back (%s)",
                            serial, exc)
                return ("failed", f"phone {serial} did not answer - press Boot "
                                  f"again in a minute", None)
            except (PhoneError, ApiError) as exc:
                log.warning("phone %s would not start: %s", serial, exc)
                return ("failed", f"phone {serial} would not start - press Boot "
                                  f"again in a minute", _code(exc))
        if station:
            if not _guarded(settings, True, store_station.booted, serial,
                            url or "", owner_id=uid, started=started):
                # Given back while it was starting: not theirs to run.
                if started:
                    _stop_quietly(phones_mod, client, live["id"], serial)
                    _guarded(settings, False, store_station.powered_off, serial)
                return ("failed", f"phone {serial} went back to the shelf while "
                                  f"it was booting", None)
            if not url:
                return ("done", f"phone {serial} is on - IranSpoty Cloud gave no "
                                f"live-view link back",
                        {"state": "taken", "station": True})
            return ("done", f"phone {serial} is on",
                    {"state": "taken", "url": url, "station": True})
        book.phones.write(serial, State="taken")
        _stamp_owner(settings, serial, payload.get("by_id"))
        _guarded(settings, False, store_station.booted, serial, url or "",
                 started=started)
    if not url:
        return ("done", f"phone {serial} started and taken by "
                        f"{_by(payload)} - IranSpoty Cloud gave no live-view link "
                        f"back", {"state": "taken"})
    return ("done", f"phone {serial} started and taken by {_by(payload)}",
            {"state": "taken", "url": url})


#: How many tasks the farm runs at once. One: the GeeLark account's two
#: hundred calls a minute are the farm's builds' first, and a task is a
#: person asking a question, which can wait for the one before it.
TASKS_AT_ONCE = 1


def run_task(book, ledger, settings, payload, client, action_id=None):
    """"Run": one of `tasks.TASKS` on one phone, onto the builders' queue.

    Everything that can be answered without the phone is answered here,
    in words, before anything is queued: the task, its inputs, whether
    the phone exists, whether something else has it. What is left - the
    phone being up with nobody's claim on it - is the builder's to ask,
    at the moment it takes the job (`serve._carry_task`).

    A task ends by switching its phone off, as every phone job does. So
    a phone somebody has taken is refused rather than switched off under
    them; a warm or a delivered phone was off already and is off again.

    A task that asks for a secret is refused outright. A password or a
    2FA key is typed at the command line and forgotten; a queue is a
    table, and a table keeps what it is given.
    """
    from . import phones as phones_mod
    from . import tasks as registry
    from .store import jobs as store_jobs

    spec = registry.spec(str(payload.get("task") or ""))
    if spec is None:
        return "refused", f"there is no task called {payload.get('task')!r}", None
    if spec.secrets():
        return ("refused", f"{spec.key} asks for a secret, which is typed on "
                           f"the command line and never queued", None)
    try:
        inputs = spec.check(dict(payload.get("inputs") or {}))
    except ValueError as exc:
        return "refused", str(exc), None
    serial = str(payload.get("serial") or "").strip()
    if not serial.isdigit():
        return "refused", "a task needs a phone - give its serial", None
    if not (getattr(settings, "build_queue", False)
            and getattr(settings, "store_enabled", False)):
        return ("failed", "tasks run on the builders, and this farm has no "
                          "build queue", None)
    row = next((r for r in book.phones.rows()
                if str(r.get("Serial") or "").strip() == serial), None)
    if row is None:
        return "failed", f"phone {serial} is not in the Phones tab", None
    if row.get("Status") == book.phones.BUILDING:
        return "refused", f"phone {serial} is being worked on right now", None
    if str(row.get("State") or "").strip() == "taken":
        return ("refused", f"phone {serial} is taken - a task switches its "
                           f"phone off when it ends, so release it first",
                None)
    if serial in store_jobs.open_serials(settings):
        return "refused", f"a job for phone {serial} is already queued", None
    if store_jobs.open_count(settings, "task") >= TASKS_AT_ONCE:
        return ("refused", "another task is running - one at a time, so the "
                           "builds keep their share of the cloud", None)
    if client is None:
        return "failed", "no IranSpoty Cloud client on this pass", None
    live = next((p for p in phones_mod.listing(client)
                 if str(p.get("serialNo")) == serial), None)
    if live is None:
        return "failed", f"phone {serial} is not in the cloud's phone list", None
    held = ledger.get(live["id"]) if ledger is not None else None
    if held is not None and held.is_claimed and not held.is_stale:
        return "refused", f"phone {serial} is held by a run ({held.label})", None
    job = store_jobs.queue(
        settings, "task",
        {"task": spec.key, "inputs": inputs, "by_id": payload.get("by_id"),
         "phone": {"serial": serial, "phone_id": str(live["id"])}},
        action_id=action_id)
    # `running`, not `done`: the builder settles the row with what the
    # task came to (serve._settle_task).
    return ("running", f"{spec.key} on phone {serial} is queued - a builder "
                       f"takes it within seconds",
            {"task": spec.key, "serial": serial, "job": job})


#: Hand this verb the id of its own row: the job carries it, and the
#: builder settles the row when the task ends.
run_task.wants_action = True


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
    with who took it in the mirror.

    A verdict - done or failed - is one statement in the store: the phone
    is closed and its verdicts row written together, so a second verdict
    on the same phone is refused and writes nothing. With `mine` (the
    Station's keys) it closes only the presser's own Station hold."""
    serial = str(payload.get("serial") or "").strip()
    state = str(payload.get("state") or "").strip().lower()
    if state not in ("taken", "done", "failed", "", "unused"):
        return "refused", f"{state!r} is not a State word", None
    from .store import person, verdicts

    word = "" if state == "unused" else state
    # Which button ended it - done, decline, or, auth or failed - is the
    # operator's own reason; a form that only says the state pressed that
    # word.
    button = str(payload.get("button") or word).strip().lower()
    if word in ("done", "failed") and verdicts.BUTTONS.get(button) != word:
        return "refused", f"{button!r} is not a button that means {word}", None
    if word in ("done", "failed") and _is_building(settings, serial):
        return "refused", f"phone {serial} is being worked on right now", None
    if word in ("done", "failed"):
        # Not guarded: a store that will not answer raises, and the row
        # stays queued for the lane, as `person.set_state` always did.
        owner = _uid(payload) if payload.get("mine") else None
        closed = verdicts.close(settings, serial=serial, button=button,
                                state=word, by=str(payload.get("by") or ""),
                                by_id=payload.get("by_id"),
                                where=str(payload.get("where") or ""),
                                owner_id=owner)
        if closed is None:
            return _why_not_closed(verdicts.standing(settings, serial), serial,
                                   owner)
    else:
        if word == "taken" and _store_on(settings):
            holder = _store_station().station_holder(settings, serial)
            if holder is not None and holder != _uid(payload):
                return ("refused", f"phone {serial} is on somebody's station",
                        None)
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
    pressed = f" ({button})" if button != word else ""
    return ("done", f"phone {serial} marked {word or 'unused'}{pressed} by "
                    f"{_by(payload)}: {meaning}",
            {"state": word, **({"button": button} if pressed else {})})


def _why_not_closed(st, serial: str, owner) -> tuple:
    """A verdict that closed nothing, in words, from `verdicts.standing`."""
    if st is None:
        return "failed", f"phone {serial or '?'} is not on the farm", None
    if st.get("state") in ("done", "failed"):
        return ("refused", f"phone {serial} is already closed as "
                           f"{st.get('state')}", None)
    if st.get("busy") == "change_proxy":
        return ("refused", f"phone {serial} is changing its IP - wait for it",
                None)
    if st.get("busy") == "boot_phone":
        return "refused", f"phone {serial} is booting - wait for it", None
    if owner is not None:
        return "refused", f"phone {serial} is not yours any more", None
    return "refused", f"phone {serial} is being worked on right now", None


def give_back(book, ledger, settings, payload, client):
    """Give back: a Station phone goes to the back of its shelf, switched
    off, keeping everything it was built with - its Gmail, its account and
    its IP. An Other phone goes back to the farm, unowned, for an admin.

    The give-back and its power-off are one statement in the store, so
    nothing can take the phone in between: the pending power-off keeps it
    off every shelf until it has run."""
    serial = str(payload.get("serial") or "").strip()
    if not serial:
        return "refused", "no phone named", None
    if not _store_on(settings):
        return "failed", "no store to give it back to", None
    store_station = _store_station()
    # A power press a restart orphaned (older than the Station's three
    # minutes) is closed first, so it never runs after the phone is back.
    _guarded(settings, 0, store_station.expire_power, serial)
    # Not guarded: a store that will not answer leaves the row queued.
    row = store_station.give_back(settings, serial=serial,
                                  owner_id=_uid(payload), by=_by(payload))
    if row is None:
        st = store_station.hold_state(settings, serial)
        if st is None or st.get("state") in ("done", "failed"):
            return ("refused", f"phone {serial} is not on the farm any more",
                    None)
        if st.get("busy") == "change_proxy":
            return ("refused", f"phone {serial} is changing its IP - wait for "
                               f"it", None)
        if st.get("busy") == "boot_phone":
            return "refused", f"phone {serial} is booting - wait for it", None
        return "refused", f"phone {serial} is not yours any more", None
    if row.get("off_id") is None and _guarded(
            settings, {}, store_station.power_pending_of, serial,
            ("power_off_phone",)) is None:
        # The statement queues its power-off with the give-back; a phone
        # back on the shelf with no power-off behind it bills until the
        # legacy hour catches it.
        log.error("phone %s was given back but no power-off was queued",
                  serial)
    return ("done", f"Phone {serial} is back on "
                    f"{store_station.home(row['lane'])}.",
            {"serial": serial, "lane": row["lane"], "off": row.get("off_id")})


def call_off_build(book, ledger, settings, payload, client):
    """Call a Station build off. One still waiting ends now, and the
    one-off IP typed for it goes to the archive; one on its way stops at
    its next step, and its Gmail and IP go back to the pool unless the
    Gmail is already signed in - then the phone is kept and lands on the
    asker's Station. One that has landed is not called off."""
    from .store import wanted as store_wanted

    raw = str(payload.get("wanted_id") or "").strip()
    if not raw.isdigit():
        return "refused", "no build named", None
    if not _store_on(settings):
        return "failed", "no store to call it off in", None
    wid = int(raw)
    w = store_wanted.call_off(settings, wid, by_id=_uid(payload),
                              by=_by(payload), admin=bool(payload.get("admin")))
    if w is None:
        return "refused", "That build is not yours.", None
    stage = str(w.get("stage") or "")
    if stage == "ended":
        return "refused", "That build has already ended.", None
    if stage == "landed":
        return "refused", "That build has already landed on your station.", None
    if stage in ("queued", "job_cancelled"):
        _archive_one_off(book, settings, str(w.get("proxy_name") or ""), wid)
        return ("done", "The build was called off.",
                {"wanted_id": wid, "stage": stage})
    serial = str(w.get("serial") or "")
    if serial:
        # Also on a second press ('already'): the first one wrote the
        # call-off and may have stumbled before its stop was asked, and
        # past its phone a build hears only the stop.
        stopped = _stop_the_build(settings, serial)
        if stopped == "landed" and stage != "already":
            return ("refused", "That build has already landed on your station.",
                    None)
        if stopped not in ("asked", "landed"):
            return "failed", stopped, None
    if stage == "already":
        return ("done", "The build is already being called off.",
                {"wanted_id": wid, "stage": "already"})
    return ("done", "The build was called off. It stops at its next step; its "
                    "Gmail and IP go back to the pool unless the Gmail is "
                    "already signed in - then the phone is kept and lands on "
                    "your station.", {"wanted_id": wid, "stage": "running"})


def _stop_the_build(settings, serial: str) -> str:
    """Ask the build on `serial` to stop, only while its phone is still
    building: a stop request lives for two hours and would end the next
    job on a landed phone. 'asked', 'landed', or the sentence of what
    could not be reached. Asking twice is harmless."""
    from .store import stops as store_stops

    try:
        st = _store_station().hold_state(settings, serial)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("call-off of phone %s: the phone was not read (%s)",
                    serial, exc)
        return "could not reach the builders - press Call off again"
    if st is None or st.get("status") != "building":
        return "landed"
    try:
        store_stops.ask(settings, serial)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("call-off of phone %s: the stop was not written (%s)",
                    serial, exc)
        return "could not reach the builders - press Call off again"
    return "asked"


def _archive_one_off(book, settings, name: str, wanted_id: int) -> None:
    """The one-off IP a called-off wish named goes to the archive, unless
    another open wish or a live phone still names it. Never fatal: one
    left behind waits under its own status, which no build ever claims."""
    from .store import wanted as store_wanted

    if not name or book is None:
        return
    try:
        named = store_wanted.others_name_exit(settings, name, wanted_id)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("the one-off IP %s was kept: could not ask who names it "
                    "(%s)", name, exc)
        return
    if named:
        return
    pool = book.proxies
    for r in list(pool._rows):
        if (pool.is_one_off(r) and r.name == name
                and pool.status_of(r) == pool.one_off_status):
            pool.delete_row(r, by="one-off IP, its build was called off")
            break


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
    return "done", f"{who} is ignored - it stays in IranSpoty Cloud, unreported", None


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
    "run_task": run_task,
    "clear_tries": clear_tries,
    "ignore_proxy": ignore_proxy,
    "change_proxy": change_proxy,
    "stop_phone": stop_phone,
    "sweep_forgotten": sweep_forgotten,
    "power_off_phone": power_off_phone,
    "add_gmails": add_gmails,
    "build_by_hand": build_by_hand,
    "edit_gmail": edit_gmail,
    "remove_gmail": remove_gmail,
    "edit_app": edit_app,
    "remove_app": remove_app,
    "remove_delivered_apps": remove_delivered_apps,
    "remove_gmail_group": remove_gmail_group,
    "free_gmail": free_gmail,
    "refund_gmail": refund_gmail,
    "gmails_aside": gmails_aside,
    "gmails_queue": gmails_queue,
    "gmails_mend": gmails_mend,
    "gmails_keep_for": gmails_keep_for,
    "gmails_remove": gmails_remove,
    "gmail_save": gmail_save,
    "gmails_add": gmails_add,
    "gmails_revert": gmails_revert,
    "restore_gmail": restore_gmail,
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
    "shelve_proxy": shelve_proxy,
    "keep_proxy_for": keep_proxy_for,
    "cap_proxy": cap_proxy,
    "unshelve_proxy": unshelve_proxy,
    "free_proxies": free_proxies,
    "test_proxies": test_proxies,
    "shelve_all_proxies": shelve_all_proxies,
    "free_shelved_proxies": free_shelved_proxies,
    "remove_proxy": remove_proxy,
    "give_back": give_back,
    "call_off_build": call_off_build,
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
              # The Proxies page's several-at-once Free and Test: checks in
              # parallel, half a minute at most (2026-10-02).
              free_proxies, test_proxies,
              change_proxy, mark_proxy_free, adopt_proxy, add_proxies,
              ignore_proxy, remove_proxy, set_phone_state, stop_phone,
              power_off_phone,
              # Three seconds of pairing; the minutes of login go to the
              # lane's own pool through `launch`, so the lane's thread is
              # free again at once (A-1, 2026-09-08). The pass still drains
              # it too, as the backstop it is for every lane verb.
              login_accounts,
              # A handful of checks and one row on the jobs table; the
              # minutes of the task are a builder's (2026-09-27).
              run_task,
              # One UPDATE against the store and nothing else: a person
              # ticking off a refund should not wait for a pass.
              refund_gmail,
              # The Station's give-back and call-off: store writes of
              # milliseconds, the same shape as a refund (2026-09-29).
              give_back, call_off_build,
              # The Gmails page's presses: guarded store writes of
              # milliseconds that never touch a Gmail a phone has
              # (store.gmail_desk, 2026-10-07).
              gmails_aside, gmails_queue, gmails_mend, gmails_keep_for,
              gmails_remove, gmail_save, gmails_add, gmails_revert,
              restore_gmail):
    _lane.lane_safe = True
del _lane
