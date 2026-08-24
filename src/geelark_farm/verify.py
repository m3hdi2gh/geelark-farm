"""Check a whole setup in one command, and say what is missing.

Every piece of this - the GeeLark key, the service-account file, whether the
spreadsheet was shared as an Editor rather than a Viewer, whether the tabs
have the columns the code writes into - fails in its own place, at its own
time, with its own message. Someone setting the tool up for the first time
meets them one at a time, each after doing more work, and a couple of them
only surface in the middle of a build that has already spent a phone.

So the checks are ordered the way the dependencies are, each says what to do
rather than only what is wrong, and one that cannot run because the thing
before it failed says so instead of reporting a second, misleading failure.

A detail may carry newlines; everything after the first line is advice, and
the printer indents it under the line it belongs to.
"""

from __future__ import annotations

import json
import logging

from .config import ENV_FILE, Settings
from .gsheet import SCOPES
from .sxorg import SxError, port_count

log = logging.getLogger(__name__)

#: Marks the whole setup unusable. A warning is a setup that works and will
#: not get far - no stock, no free slots - which is worth saying and is not a
#: reason to exit non-zero. INFO is a fact, SKIP is a check that could not run
#: because the one before it failed.
FATAL, WARN, INFO, SKIP, OK = "fatal", "warn", "info", "skip", "ok"


class Check:
    """One line of the report."""

    __slots__ = ("name", "state", "detail")

    def __init__(self, name: str, state: str, detail: str) -> None:
        self.name, self.state, self.detail = name, state, detail

    def __repr__(self) -> str:                                # pragma: no cover
        return f"Check({self.name!r}, {self.state!r}, {self.detail!r})"


#: What each tab has to have for the code that writes it to work.
#:
#: A missing column is the quietest failure in the project: `_set` skips a
#: column the tab does not have, on purpose, so the optional ones can be
#: absent - which means a tab without `Status` claims rows and never records
#: it, and the run looks fine while every row is handed out twice.
#: `2FA Secret` is here although an account without one is perfectly usable:
#: what has to exist is the column, not a value in every row. Without it every
#: row reads as an account with no authenticator, is handed to a phone, and
#: fails at the code page as `no_authenticator` - which blames the row for a
#: tab that is missing a heading (2026-08-23).
REQUIRED_COLUMNS = {
    "Gmails": ["Address", "Password", "2FA Secret", "Status", "Note",
               "Phone Serial"],
    "Gpt Info": ["Address", "Password", "2FA Secret", "Status", "Note",
                 "Phone Serial"],
    "Proxy": ["Proxy String", "Status", "Note", "Used By"],
    "Phones": ["Created", "Serial", "State", "Proxy", "Gmail", "GPT Account",
               "Status", "Note"],
}


def _one_line(exc: object) -> str:
    """An error's first line. The rest is its own advice and a traceId, which
    belong in the log rather than in a column of a checklist."""
    return str(exc).splitlines()[0].strip()


def _geelark(settings: Settings, checks: list[Check]) -> bool:
    from . import phones
    from .api import Client
    try:
        client = Client(settings)
        # Through `listing`, which is the one place that knows the endpoint
        # and how it pages. Called raw here, this was a third copy of that
        # knowledge - and listing a phone exercises the same signing, limiter
        # and envelope a bare call does, which is all this check is for.
        items = phones.listing(client)
    except Exception as exc:                                  # noqa: BLE001
        checks.append(Check("geelark api", FATAL, "\n".join([
            _one_line(exc),
            "GEELARK_APP_ID and GEELARK_API_KEY come from the GeeLark panel, "
            "under API."])))
        return False

    checks.append(Check("geelark api", OK,
                        f"appId {settings.app_id[:6]}..., "
                        f"{len(items)} phone(s) visible"))

    try:
        info = phones.plan(client)
    except Exception as exc:                                  # noqa: BLE001
        # GeeLark allows one call a minute to this endpoint, separately from
        # the account limit, so running verify twice in a row rate-limits this
        # one check. A diagnostic that cries wolf about itself is worse than
        # one that says plainly it did not look.
        if "too many requests" in str(exc).lower():
            checks.append(Check("plan", INFO,
                                "not checked - GeeLark allows one call a "
                                "minute here, so try again in a minute"))
        else:
            checks.append(Check("plan", WARN,
                                f"could not be read ({_one_line(exc)})"))
        return True

    free = info.get("availableProfiles") or 0
    slots = info.get("profiles") or 0
    checks.append(Check("plan", OK if free else WARN,
                        f"{slots} slot(s), {free} free" if free else
                        f"{slots} slot(s), none free - no phone can be "
                        f"created until one is deleted"))
    return True


def _key_file(settings: Settings, checks: list[Check]) -> str:
    """The service-account file, and the address the spreadsheet has to be
    shared with. Returns that address, or "" if the file is unusable."""
    path = settings.service_account_json
    if not path.exists():
        checks.append(Check("service account", FATAL, "\n".join([
            f"no file at {path}",
            "Create a service account in Google Cloud, download its JSON key, "
            "and put it there (or point GOOGLE_SERVICE_ACCOUNT_JSON at it)."])))
        return ""
    try:
        email = json.loads(path.read_text(encoding="utf-8"))["client_email"]
    except Exception as exc:                                  # noqa: BLE001
        checks.append(Check("service account", FATAL,
                            f"{path} is not a usable key ({_one_line(exc)})"))
        return ""
    checks.append(Check("service account", OK, email))
    return email


def _spreadsheet(settings: Settings, email: str, checks: list[Check]):
    """Open the book. Returns the gspread Spreadsheet, or None."""
    import gspread
    from google.oauth2.service_account import Credentials as Key
    try:
        client = gspread.authorize(Key.from_service_account_file(
            str(settings.service_account_json), scopes=SCOPES))
        book = client.open_by_key(settings.sheet_id)
    except Exception as exc:                                  # noqa: BLE001
        # By far the most common cause, and the one the error does not say:
        # the spreadsheet exists and is simply not shared with the key. Both
        # that and a wrong id come back as a bare `<Response [404]>`, which
        # tells whoever is reading it nothing at all.
        said = _one_line(exc)
        if "404" in said:
            said = "no spreadsheet with that id is visible to this key"
        checks.append(Check("spreadsheet", FATAL, "\n".join([
            said,
            f"Either GOOGLE_SHEET_ID is wrong, or the book has not been "
            f"shared with {email or 'the service account'} as an Editor."])))
        return None
    checks.append(Check("spreadsheet", OK, book.title))
    return book


def _writable(tabs: dict, checks: list[Check]) -> None:
    """Prove the key can write, not just read.

    Shared as a Viewer, every read works and the first write fails - partway
    through a build, after a phone has been paid for. The cheapest honest test
    is a real write that changes nothing: a header cell set to what it holds.
    """
    sheet = tabs.get("Phones") or next(iter(tabs.values()), None)
    if sheet is None:
        checks.append(Check("write access", SKIP, "no tab to test against"))
        return
    try:
        sheet.update_acell("A1", sheet.acell("A1").value or "")
    except Exception as exc:                                  # noqa: BLE001
        checks.append(Check("write access", FATAL, "\n".join([
            _one_line(exc),
            "The key can read this book but not write to it - it is shared "
            "as a Viewer, and a run needs Editor."])))
        return
    checks.append(Check("write access", OK,
                        "the key can write (tested without changing anything)"))


def _tabs_and_columns(tabs: dict, checks: list[Check]) -> None:
    from .pools import HISTORY_TAB, LISTS_TAB

    missing = [name for name in REQUIRED_COLUMNS if name not in tabs]
    if missing:
        checks.append(Check("tabs", FATAL, "\n".join([
            f"missing: {', '.join(missing)}",
            f"found: {', '.join(sorted(tabs))}",
            f"{LISTS_TAB} and {HISTORY_TAB} are made automatically; "
            f"these four are not."])))
        checks.append(Check("columns", SKIP, "not checked - tabs are missing"))
        return

    made = [name for name in (LISTS_TAB, HISTORY_TAB) if name in tabs]
    checks.append(Check("tabs", OK, ", ".join(REQUIRED_COLUMNS)
                        + (f" (+ {', '.join(made)})" if made else "")))

    absent = []
    for name, wanted in REQUIRED_COLUMNS.items():
        try:
            headers = {h.strip() for h in tabs[name].row_values(1)}
        except Exception as exc:                              # noqa: BLE001
            # A read that did not happen is not a column that is missing.
            # `run_checks` says it never raises and this was one of three
            # places that could - in the command whose whole job is to
            # explain a problem rather than be one (2026-08-23).
            checks.append(Check("columns", SKIP,
                                f"not checked - {name} could not be read "
                                f"({_one_line(exc)})"))
            return
        absent += [f"{name}.{column}" for column in wanted
                   if column not in headers]
    if absent:
        # Fatal rather than a warning: a claim that cannot record itself hands
        # the same row out again, which spends credentials on nothing.
        checks.append(Check("columns", FATAL, "\n".join([
            f"missing: {', '.join(absent)}",
            "A column the tab does not have is skipped silently, so a run "
            "would look fine and record nothing."])))
        return
    checks.append(Check("columns", OK, "every column the code writes exists"))


def _stock(settings: Settings, checks: list[Check]) -> None:
    from .pools import Book
    try:
        book = Book.open(settings)
    except Exception as exc:                                  # noqa: BLE001
        checks.append(Check("stock", SKIP, _one_line(exc)))
        return

    counts = {"gmails": len(book.gmails.available),
              "proxies": len(book.proxies.available),
              "app accounts": len(book.apps.available)}
    said = ", ".join(f"{n} {name}" for name, n in counts.items())
    empty = [name for name, n in counts.items() if not n]
    if empty:
        checks.append(Check("stock", WARN, "\n".join([
            said,
            f"A run stops at whichever runs out first; "
            f"{' and '.join(empty)} would stop it immediately."])))
        return
    checks.append(Check("stock", OK, said))


#: The Proxy column the cheap retry needs. Not in REQUIRED_COLUMNS, because a
#: tab without it works - it just always takes the expensive path.
PORT_ID_COLUMN = "Port ID"


def _sxorg(settings: Settings, tabs: dict, checks: list[Check]) -> None:
    """Whether the cheap way out of a refused exit can actually happen.

    It needs three things, and having some of them is worse than having none,
    because it reads like it is set up. The key alone was reported as working
    against a Proxy tab with no Port ID column, where `_new_exit` takes the
    fallback every single time (2026-08-17).

    The third is the one this used to infer and now asks about. A missing
    Port ID column was reported as something to go and add - and on an account
    holding only Unlimited proxies there is nothing to put in it, because they
    do not appear in the vendor's port listing at all. That advice sent the
    operator to do work that cannot succeed, so the count is read from sx.org
    before the column is mentioned (2026-08-25).
    """
    if not settings.sxorg_api_key:
        checks.append(Check("sx.org", INFO,
                            "SXORG_API_KEY not set - a refused exit takes "
                            "another proxy instead, which is the more "
                            "expensive of the two"))
        return

    try:
        ports = port_count(settings.sxorg_api_key)
    except SxError as exc:
        checks.append(Check("sx.org", WARN,
                            f"the key is set but sx.org would not answer "
                            f"({_one_line(exc)})"))
        return
    if not ports:
        checks.append(Check("sx.org", WARN, "\n".join([
            "the key works, but this account has no refreshable proxies",
            "Only the 'port' product can be given a new exit address; the "
            "Unlimited proxies cannot, and do not appear in the vendor's "
            "listing at all. A refused exit will always take another proxy.",
            "Nothing to fix in the sheet - adding a Port ID column would "
            "leave you with nothing to put in it."])))
        return

    proxy = tabs.get("Proxy")
    try:
        headers = {h.strip() for h in proxy.row_values(1)} if proxy else set()
    except Exception as exc:                                  # noqa: BLE001
        checks.append(Check("sx.org", SKIP,
                            f"not checked - the Proxy tab could not be read "
                            f"({_one_line(exc)})"))
        return
    if PORT_ID_COLUMN not in headers:
        checks.append(Check("sx.org", WARN, "\n".join([
            f"{ports} refreshable proxy(s) on the account, but the Proxy tab "
            f"has no {PORT_ID_COLUMN} column",
            "sx.org is addressed by port id, so without it a refused exit "
            "still takes another proxy - the key achieves nothing."])))
        return
    checks.append(Check("sx.org", OK,
                        f"a refused exit gets a new address on the same proxy "
                        f"({ports} refreshable)"))


def run_checks(settings: Settings) -> list[Check]:
    """Every check, in dependency order. Never raises.

    Which is a promise, not a hope: three of the reads below reached the
    network without a guard, and a sheet quota - the failure this project
    handles more carefully than any other - would have ended `geelark verify`
    in a traceback. A diagnostic that cannot survive the thing it exists to
    diagnose is worth less than nothing (2026-08-23).
    """
    checks: list[Check] = []

    checks.append(Check(
        ".env", OK if ENV_FILE.exists() else INFO,
        str(ENV_FILE) if ENV_FILE.exists() else
        f"no file at {ENV_FILE} - settings are coming from the environment "
        f"itself, which works, or are missing"))

    if not _geelark(settings, checks):
        return checks

    if not settings.sheet_id:
        checks.append(Check("spreadsheet", FATAL,
                            "GOOGLE_SHEET_ID is not set - a run has no input"))
        return checks

    email = _key_file(settings, checks)
    if not email:
        return checks

    book = _spreadsheet(settings, email, checks)
    if book is None:
        return checks

    try:
        tabs = {ws.title: ws for ws in book.worksheets()}
    except Exception as exc:                                  # noqa: BLE001
        checks.append(Check("tabs", FATAL, "\n".join([
            _one_line(exc),
            "The book opened but its tabs could not be listed, so nothing "
            "below this could be checked. A quota is the usual cause and "
            "clears in a minute."])))
        return checks
    _writable(tabs, checks)
    _tabs_and_columns(tabs, checks)
    _stock(settings, checks)

    _sxorg(settings, tabs, checks)
    return checks


def failed(checks: list[Check]) -> list[Check]:
    return [check for check in checks if check.state == FATAL]
