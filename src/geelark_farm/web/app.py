"""The listener: stdlib, threads, loopback, read-only.

ThreadingHTTPServer rather than a framework, because every concurrency
invariant in this codebase is threads and the surface is a handful of
pages. It binds loopback only - compose publishes 127.0.0.1 on the host
too, so until the domain lands the one way in from outside the box is an
SSH tunnel.

The thread is a daemon and holds nothing that matters: sessions are
process memory, so the Watchdog's os._exit costs every viewer a login and
nothing else - by design, nothing that must survive may live only here.

The login answers wrong-name and wrong-password identically, and a name
that keeps failing is made to wait: this port is loopback today, but rate
limiting arrives with the door, not with the internet.
"""

from __future__ import annotations

import csv
import hmac
import io
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode

from .. import signals
from ..config import Settings
from . import api_v1, live, pages, read

log = logging.getLogger(__name__)

SESSION_HOURS = 12
#: Five wrong answers buys this many seconds of "try later", per username.
LOCKOUT_AFTER = 5
LOCKOUT_SECONDS = 600

#: Sessions live in the store (`store.sessions`), because this process
#: restarts on every deploy and a seat kept here does not survive one.
#: The lockout counter stays in memory on purpose: it is a few minutes of
#: "try later", and a restart forgiving it costs less than a table row per
#: wrong password.
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def start(settings: Settings) -> ThreadingHTTPServer:
    """Bind, serve on a daemon thread, return the server (tests stop it)."""

    class Handler(_Handler):
        pass

    Handler.settings = settings
    pages.set_zone(settings.web_tz)
    # Every stream held open asks it: a server being shut down is not a
    # place to wait twenty seconds for a tick that is not coming.
    server = ThreadingHTTPServer((settings.web_bind, settings.web_port),
                                 Handler)
    server.daemon_threads = True
    # Every stream held open on /live asks this: a server being shut down
    # is not a place to wait twenty seconds for a tick that is not coming,
    # and a test that stops the server must not hang on one.
    server.stopping = threading.Event()
    shut = server.shutdown

    def stop_streams():
        server.stopping.set()
        shut()

    server.shutdown = stop_streams
    live.start(settings, server.stopping)
    thread = threading.Thread(target=server.serve_forever,
                              name="web", daemon=True)
    thread.start()
    log.info("web ui listening on %s:%d (published to the host's loopback "
             "only; reach it through an ssh tunnel)",
             settings.web_bind, server.server_address[1])
    return server


class _Handler(BaseHTTPRequestHandler):
    settings: Settings = None          # set by start()

    # ------------------------------------------------------------- routes
    def do_GET(self) -> None:
        try:
            path = self.path.split("?")[0]
            # The machine-facing door, before the session gate: an API
            # client has a bearer key and no cookie, and api_v1 owns its
            # own auth, its own errors and its own 404-when-switched-off.
            if path.startswith("/api/"):
                return api_v1.dispatch(self, path)
            # The stylesheet, before the session gate: the sign-in page
            # needs it and has no session yet. It is presentation, and
            # its name is its own hash, so it is cacheable for ever by
            # anything between here and the browser.
            if path.startswith("/s/") and path.endswith(".css"):
                return self._asset(path, private=False)
            if path == "/login":
                return self._html(200, pages.login())
            user = self._user()
            if user is None:
                return self._redirect("/login")
            # The script, behind it: this is the console's behaviour -
            # which endpoints exist, which fields they take, what each
            # press does - and there is no reason to hand it to anybody
            # who can reach the host. Cached `private`, which is all a
            # one-browser cache needs.
            if path.startswith("/s/") and path.endswith(".js"):
                return self._asset(path, private=True)
            # A one-time password buys exactly one page: the one where the
            # person chooses their own. Everything else waits.
            if user.get("must_change_password") and path != "/password":
                return self._redirect("/password")
            if path == "/password":
                return self._html(200, pages.password_page(user))
            if user.get("role") != "admin" and not _operator_may_get(path):
                # Straight back to the one page they have. This used to be
                # a page of its own saying whose the page was, and every
                # link that landed an operator there - an alert, an old
                # bookmark - was one more page between them and the work
                # (the operator, 2026-09-05: "this page is superfluous").
                return self._redirect("/")
            if path == "/live":
                return self._live(user)
            if path == "/users":
                return self._users_get(user)
            if path == "/api-clients":
                return self._api_clients_get(user)
            if path == "/":
                scope = None if user["sees"] == "all" else user["id"]
                query = parse_qs(self.path.partition("?")[2])
                said = (query.get("said") or [""])[0]
                return self._html(200, pages.dashboard(
                    read.dashboard(self.settings, scope), user,
                    said=said, said_note=self._said_note(said),
                    manual_login=self.settings.manual_login,
                    explain=_advice))
            if path == "/phones":
                scope = None if user["sees"] == "all" else user["id"]
                return self._html(200, pages.phones_page(
                    read.phones(self.settings, scope), user))
            if path == "/requests":
                from ..store import actions as store_actions

                query = parse_qs(self.path.partition("?")[2])
                first = {k: v[0] for k, v in query.items()}
                mine = first.get("mine") == "1"
                everyone = user["role"] == "admin" and not mine
                view = first.get("view", "")
                if view not in pages.REQUEST_VIEWS:
                    view = ""
                number = _page_number(first)
                per = store_actions.PER_PAGE
                rows = store_actions.listing(
                    self.settings, user_id=user["id"], everyone=everyone,
                    view=view, page=number)
                more = len(rows) > per
                rows = rows[:per]
                try:
                    tally = store_actions.counts(
                        self.settings, user_id=user["id"], everyone=everyone)
                except Exception as exc:                          # noqa: BLE001
                    log.debug("the request pills did not count (%s)", exc)
                    tally = {}
                total = (sum(tally.values()) if not view
                         else tally.get(view, 0))
                hi = first.get("hi", "")
                return self._html(200, pages.requests_page(
                    rows, user, said=first.get("said", ""), counts=tally,
                    view=view, mine=mine, page=number,
                    pages=max(1, -(-int(total or 0) // per)), more=more,
                    hi=int(hi) if hi.isdigit() else 0,
                    progress=self._progress_of(rows)))
            if path == "/needs":
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                query = parse_qs(self.path.partition("?")[2])
                said = (query.get("said") or [""])[0]
                return self._html(200, pages.needs_page(
                    read.needs(self.settings), user, _advice, said=said,
                    said_note=self._said_note(said)))
            # One pool's sheet, for the drawer. Not a page: a
            # fragment the script mounts inside the overlay it already
            # has - see pages._pool_manager for why it is not in the
            # dashboard any more.
            if path.startswith("/pools/") and path.endswith("/sheet"):
                return self._pool_sheet(user, path)
            if path == "/pools":
                return self._redirect("/pools/gmail")
            # The three pool pages (C5): shared stock, so everyone signed
            # in sees them; what they may DO on them is the buttons' job.
            query = parse_qs(self.path.partition("?")[2])
            first = {k: v[0] for k, v in query.items()}
            if path == "/pools/gmail/refund.txt":
                # The whole errored list for one seller, uncapped: the
                # page shows a hundred at a time, the refund asks for all.
                addresses = read.errored_addresses(
                    self.settings, seller=first.get("seller", ""))
                return self._text(200, "\n".join(addresses) + "\n")
            if path == "/pools/gmail":
                editing = first.get("edit", "")
                return self._html(200, pages.gmail_pool_page(
                    read.gmail_pool(self.settings,
                                    view=first.get("view", "queued"),
                                    seller=first.get("seller", ""),
                                    page=_page_number(first)),
                    user, said=first.get("said", ""), advice=_advice,
                    editing=int(editing) if editing.isdigit() else 0,
                    said_note=self._said_note(first.get("said", ""))))
            if path == "/pools/proxy":
                unlisted, ignored, tests = self._proxy_state()
                data = read.proxy_pool(self.settings,
                                       view=first.get("view", "free"),
                                       q=first.get("q", ""),
                                       page=_page_number(first),
                                       unlisted=unlisted)
                # What the pass keeps beside the rows (C5): the test
                # stamps and the ignore list, merged here so the reader
                # stays a reader of the resources table alone.
                data["tests"] = tests
                data["ignored"] = ignored
                return self._html(200, pages.proxy_pool_page(
                    data, user, said=first.get("said", ""),
                    q=first.get("q", ""),
                    show_ignored=first.get("ignored") == "1",
                    said_note=self._said_note(first.get("said", ""))))
            if path == "/pools/gpt/delivered.csv":
                # The delivered archive, whole, for whoever reconciles it
                # against the customer panel: the page shows fifty at a
                # time, the export matches the same search uncapped.
                rows = read.delivered_rows(self.settings,
                                           q=first.get("q", ""))
                return self._text(200, _delivered_csv(rows), kind="text/csv",
                                  filename="gpt-delivered.csv")
            if path == "/pools/gpt":
                return self._html(200, pages.gpt_pool_page(
                    read.gpt_pool(self.settings,
                                  view=first.get("view", "waiting"),
                                  q=first.get("q", ""),
                                  page=_page_number(first)),
                    user, said=first.get("said", ""), explain=_explain,
                    manual_login=self.settings.manual_login,
                    said_note=self._said_note(first.get("said", ""))))
            if path in ("/events", "/events.csv"):
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                kind, q = first.get("kind", ""), first.get("q", "").strip()
                # One day at a time, today unless asked; "all" is every
                # day. Anything that is not a date reads as today.
                day = first.get("day", "").strip() or pages.today()
                if day != "all" and read.day_bounds(self.settings,
                                                    day) is None:
                    day = pages.today()
                if path == "/events.csv":
                    rows = read.events_rows(self.settings, kind=kind, q=q,
                                            day=day)
                    return self._text(200, _events_csv(rows), kind="text/csv",
                                      filename=f"events-{day}.csv")
                return self._html(200, pages.events_page(
                    read.events_feed(self.settings, kind=kind, q=q, day=day,
                                     page=_page_number(first)),
                    user, signals=read.signals(self.settings), kind=kind,
                    q=q, day=day, explain=_explain))
            if path == "/logins":
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                try:
                    days = int(first.get("days", "7") or 7)
                except ValueError as exc:
                    log.debug("days is not a number (%s); a week", exc)
                    days = 7
                return self._html(200, pages.logins_page(
                    read.logins(self.settings, days=days), user))
            if path == "/logs":
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                filters = {k: first.get(k, "").strip()
                           for k in ("run", "phone", "q")}
                # The select and the free box both name a logger; a
                # typed name wins, since a select is often left as it was.
                filters["logger"] = (first.get("logger_text", "").strip()
                                     or first.get("logger", "").strip())
                level = first.get("level", "INFO").upper() or "INFO"
                before = first.get("before", "")
                before = int(before) if before.isdigit() else 0
                return self._html(200, pages.logs_page(
                    read.logs(self.settings, level=level, before=before,
                              **filters),
                    user, level=level, before=before, **filters,
                    capture=_capture_health(),
                    log_db=bool(self.settings.log_db)))
            if path.startswith("/phones/") and path.endswith("/live"):
                # Boot's new tab. The live-view URL is the answer to the
                # start call, so it can only exist once the pass has made
                # it; the ?said= the press redirected here with names the
                # request to wait on.
                from ..store import actions as store_actions

                serial = path[len("/phones/"):-len("/live")]
                said = first.get("said", "")
                _, _, req = said.partition(":")
                row = None
                if req.isdigit():
                    try:
                        row = store_actions.one(self.settings, int(req))
                    except Exception as exc:                      # noqa: BLE001
                        log.debug("boot %s: request not read (%s)", req, exc)
                # Once the link is there the page frames GeeLark's viewer
                # rather than sending the tab to it: the tab's closing is
                # the phone's off switch (pages.viewer_page, 2026-09-16).
                creds = account = None
                if row and row.get("status") == "done":
                    creds = self._gmail_for_the_holder(user, serial)
                    account = self._account_for_the_holder(user, serial)
                return self._html(200, pages.live_page(
                    serial, user, said=said, row=row, creds=creds,
                    account=account))
            if path.startswith("/phones/") and "/screens/" in path:
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                return self._screen(user, path)
            if path.startswith("/phones/"):
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                serial = path[len("/phones/"):].strip("/")
                story = read.phone_story(self.settings, serial) \
                    if serial.isdigit() else None
                if story is None:
                    return self._html(404, pages.page(
                        "404", "<h2>No such phone</h2>", user=user))
                return self._html(200, pages.phone_story_page(
                    story, user, explain=_explain,
                    said=(parse_qs(self.path.partition("?")[2])
                          .get("said") or [""])[0]))
            self._html(404, pages.page("404", "<h2>Nothing here</h2>",
                                       user=user))
        except Exception as exc:                                  # noqa: BLE001
            # A handler that leaks a traceback leaks whatever was in it.
            if _store_down(exc):
                log.warning("web: %s - the store is not answering (%s)",
                            self.path, exc)
                return self._html(503, pages.store_down_page())
            log.exception("web: %s failed", self.path)
            self._html(500, pages.page("Error", "<h2>Something broke - it "
                                                "is in the server log</h2>"))

    def do_POST(self) -> None:
        try:
            # Before the body is read and before the CSRF gate: an API
            # client sends JSON and no cookie, and the console's form
            # handling would answer it with a redirect.
            if self.path.split("?")[0].startswith("/api/"):
                return api_v1.dispatch(self, self.path.split("?")[0])
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            field = {k: v[0] for k, v in form.items()}
            # The stamp the page minted when it drew the form - see
            # pages._csrf and _minute_key.
            self._press = str(field.get("press") or "")[:32]
            if self.path == "/login":
                return self._login(field)
            entry = self._entry()
            if entry is None:
                return self._redirect("/login")
            # CSRF, before any dispatch. The comparison runs constant-time
            # for the same reason password checks do, and an Origin header
            # that is present and foreign is refused as a second layer.
            if not hmac.compare_digest(field.get("csrf", ""),
                                       entry.get("csrf", "")):
                return self._html(403, pages.page(
                    "403", "<h2>Stale session - reopen the page"
                           "</h2>", user=entry["user"]))
            origin = self.headers.get("Origin")
            host = self.headers.get("Host") or ""
            if origin and host and not origin.endswith("//" + host):
                return self._html(403, pages.page("403", "<h2>Bad origin</h2>"))
            user = self._user()
            # The console saying it broke. Answered with nothing at
            # all: the page has already lost its footing and is not
            # going to do anything with the reply.
            if self.path == "/clienterror":
                return self._client_error(entry["user"], field)
            if self.path == "/logout":
                from ..store import sessions as store_sessions

                store_sessions.end(self.settings, self._cookie())
                return self._redirect("/login")
            if self.path == "/password":
                return self._password_post(user, field)
            if user.get("must_change_password"):
                return self._redirect("/password")
            if (user.get("role") != "admin"
                    and not _operator_may_post(self.path)):
                # A refusal, not a redirect: a form that quietly does
                # nothing is how somebody comes to believe they pressed it.
                return self._html(403, pages.page(
                    "Not yours to do",
                    '<div class="narrow"><h2>That belongs to an admin</h2>'
                    '<p class="dim">Nothing was changed. '
                    '<a href="/">Back to the dashboard</a>.</p></div>',
                    user=user))
            if self.path.startswith("/pools/"):
                return self._pool_post(user, field)
            if self.path == "/phones/build":
                # Two choices: the Gmail and the account. The exit is not
                # one of them - the build picks one and swaps it when an
                # install or a sign-in shows it is bad (the operator,
                # 2026-09-10); a `proxy_name` the panel API still sends is
                # passed on, and honoured. Nor is the app, since every
                # phone carries all three (2026-09-12).
                #
                # A typed address wins over a picked one: somebody who
                # filled the box meant the box. Said here rather than in
                # the verb, so the verb takes one shape of payload however
                # it was asked - the API will ask differently.
                gmail = (field.get("gmail") or "").strip()
                # "none": a bare phone - nothing signed in, and so no app
                # and no account. The boxes below are off on the page and
                # ignored here, whatever they carried.
                no_gmail = gmail.lower() == "none"
                if no_gmail:
                    gmail = ""
                # Which app is not a question about the phone - every
                # one carries ChatGPT, Spotify and Claude - but it is a
                # question about the account, and the card now asks it:
                # `account_kind` is `product:category`, exactly as the
                # pool stores the two. Empty means no account at all.
                #
                # A bare phone is no longer "signs in nowhere": one kind
                # belongs on it, a `normal` Spotify account, which wants
                # a phone with no Google account (2026-09-17). The card
                # offers only that one when Gmail is none; this refuses
                # the rest again here, because a page is not a guard.
                kind = (field.get("account_kind") or "").strip().lower()
                # Checked against the card's own list, not merely split.
                # The half after the colon becomes a pool row's Category,
                # and a word nothing serves strands the account there.
                if kind not in pages.ACCOUNT_KIND_VALUES:
                    return self._refuse(
                        user, "build_by_hand", {"account_kind": kind},
                        f"{kind!r} is not a kind of account this card "
                        f"offers")
                allowed = {value for value, _w, bare, gmail
                           in pages.ACCOUNT_KINDS
                           if (bare if no_gmail else gmail)}
                if kind not in allowed:
                    return self._refuse(
                        user, "build_by_hand",
                        {"gmail": "" if no_gmail else gmail,
                         "account_kind": kind},
                        "a phone with no Google account can only carry a "
                        "normal Spotify account"
                        if no_gmail else
                        "a normal Spotify account wants a phone with no "
                        "Google account on it")
                which, _, category = kind.partition(":")
                account = (field.get("app_account") or "").strip()
                if not which:
                    account = ""
                payload = {
                    "gmail": gmail,
                    "no_gmail": no_gmail,
                    "gmail_typed": bool(gmail) and self._is_new("gmail", gmail),
                    "gmail_password": field.get("gmail_password") or "",
                    "gmail_secret": field.get("gmail_secret") or "",
                    "proxy_name": (field.get("proxy_name") or "").strip(),
                    "app": which,
                    "install_app": bool(which),
                    "app_account": account,
                    # Which kind the typed one is, so the row it becomes
                    # says so. A picked row already knows.
                    "app_category": category,
                    "app_typed": bool(account) and self._is_new("app", account),
                    "app_password": field.get("app_password") or "",
                    "app_secret": field.get("app_secret") or "",
                }
                return self._act(
                    user, "may_login_accounts", "build_by_hand", payload,
                    idem=self._minute_key(
                        user, "byhand",
                        payload["gmail"] or ("bare" if no_gmail else "next")),
                    back="/", said_word="asked")
            if self.path.startswith("/wishes/") and \
                    self.path.endswith("/dismiss"):
                return self._dismiss_wish(user)
            if self.path == "/accounts/spotify/build":
                # Send, for a `normal` Spotify account: the phone it wants
                # does not exist until it is built - no Google account on
                # it - so the press is a bare wish carrying the account,
                # and the build signs it in (2026-09-17).
                address = (field.get("address") or "").strip()
                payload = {
                    "gmail": "", "no_gmail": True, "gmail_typed": False,
                    "gmail_password": "", "gmail_secret": "",
                    "proxy_name": "", "app": "spotify", "install_app": True,
                    "app_account": address, "app_typed": False,
                    "app_password": "", "app_secret": "",
                }
                return self._act(
                    user, "may_login_accounts", "build_by_hand", payload,
                    idem=self._minute_key(user, "byhand", f"spotify:{address}"),
                    back="/", said_word="asked")
            if self.path == "/accounts/login":
                back = field.get("back") or "/"
                return self._login_accounts(
                    user, form.get("addresses") or [],
                    back=back if back in LOGIN_BACKS else "/",
                    serial=(field.get("serial") or "").strip())
            if self.path.startswith("/requests/") and \
                    self.path.endswith("/retry"):
                return self._retry_action(user)
            if self.path.startswith("/phones/") and \
                    self.path.endswith("/watching"):
                # The Live tab's beat: a plain stamp, no request and no
                # event - twenty seconds apart for as long as the tab is
                # open (pages.viewer_page). 410 once the phone is no
                # longer taken, so the tab can say so.
                from ..store import person

                serial = self.path[len("/phones/"):-len("/watching")]
                try:
                    still = person.watch(self.settings, serial)
                except Exception as exc:                          # noqa: BLE001
                    log.debug("beat for %s not written (%s)", serial, exc)
                    return self._text(503, "store down")
                return self._text(200 if still else 410,
                                  "watching" if still else "released")
            if self.path.startswith("/phones/") and \
                    self.path.endswith("/closing"):
                # The Live tab's pagehide beacon: the tab is closing (or
                # reloading - the next beat says which). The sweep acts
                # on it twenty seconds later (forgotten.TAB_CLOSED_SECONDS).
                from ..store import person

                serial = self.path[len("/phones/"):-len("/closing")]
                try:
                    person.tab_closed(self.settings, serial)
                except Exception as exc:                          # noqa: BLE001
                    log.debug("closing of %s not written (%s)", serial, exc)
                return self._text(200, "noted")
            if self.path.startswith("/phones/") and \
                    self.path.endswith("/boot"):
                # One press: start the phone in GeeLark, take it, and hand
                # the live-view link to the tab that is waiting for it.
                serial = self.path[len("/phones/"):-len("/boot")]
                # Boot takes the phone: refused on anybody else's, an
                # admin's included - ending a hold is theirs (2026-09-15),
                # taking it over is not.
                held = self._holder_of(user, serial)[0]
                if held and held != user.get("username"):
                    return self._refuse(
                        user, "boot_phone", {"serial": serial},
                        f"phone {serial} is with {held}")
                return self._act(user, "may_take_phones", "boot_phone",
                                 {"serial": serial},
                                 idem=self._minute_key(user, "boot", serial),
                                 back=f"/phones/{serial}/live")
            if self.path.startswith("/phones/") and \
                    self.path.endswith("/stop"):
                serial = self.path[len("/phones/"):-len("/stop")]
                return self._act(user, "may_login_accounts", "stop_phone",
                                 {"serial": serial},
                                 idem=self._minute_key(user, "stop", serial),
                                 back=_phone_back(field, serial),
                                 said_word="cancelled")
            if self.path.startswith("/phones/") and \
                    self.path.endswith("/proxy"):
                serial = self.path[len("/phones/"):-len("/proxy")]
                held = self._held_by_somebody_else(user, serial)
                if held:
                    return self._refuse(
                        user, "change_proxy", {"serial": serial},
                        f"phone {serial} is with {held}")
                # `boot` is the Live tab's press: the phone comes back up
                # on its new exit and the tab swaps to the new screen
                # without leaving the page (2026-09-16).
                return self._act(user, "may_change_proxy", "change_proxy",
                                 {"serial": serial,
                                  "boot": str(field.get("boot") or "") == "1"},
                                 idem=self._minute_key(user, "proxy", serial),
                                 back=_phone_back(field, serial))
            if self.path.startswith("/phones/") and \
                    self.path.endswith("/state"):
                serial = self.path[len("/phones/"):-len("/state")]
                return self._phone_state(user, serial, field)
            if self.path.startswith("/service/"):
                return self._service(user, self.path[len("/service/"):],
                                     field)
            if self.path in ("/needs/offer", "/needs/clear"):
                return self._needs_post(user, field)
            if self.path == "/api-clients/new":
                return self._api_clients_new(user, field)
            if self.path == "/api-clients/forget":
                return self._api_clients_forget(user)
            if self.path.startswith("/api-clients/"):
                # Suffix first, then the bare id: the same order the
                # /users routes below are written in.
                if self.path.endswith("/rotate"):
                    return self._api_clients_rotate(user, field)
                if self.path.endswith("/active"):
                    return self._api_clients_active(user, field)
                if self.path.endswith("/webhook"):
                    return self._api_clients_webhook(user, field)
            if self.path == "/users/new":
                return self._users_new(user, field)
            if self.path.startswith("/users/") and \
                    self.path.endswith("/reset"):
                return self._users_reset(user, field)
            if self.path.startswith("/users/"):
                return self._users_update(user, field)
            if self.path.startswith("/requests/") and \
                    self.path.endswith("/cancel"):
                return self._cancel_action(user)
            self._html(404, pages.page("404", "<h2>Nothing here</h2>",
                                       user=user))
        except Exception as exc:                                  # noqa: BLE001
            if _store_down(exc):
                log.warning("web: POST %s - the store is not answering (%s)",
                            self.path, exc)
                return self._html(503, pages.store_down_page(
                    retry=(self.path, form)))
            log.exception("web: POST %s failed", self.path)
            self._html(500, pages.page("Error", "<h2>Something broke</h2>"))

    def _cancel_action(self, user: dict) -> None:
        if not self.settings.web_mutations:
            return self._html(403, pages.page(
                "Disabled", "<h2>Actions are not switched on yet</h2>",
                user=user))
        from ..store import actions as store_actions

        action_id = int(self.path.split("/")[2])
        got = store_actions.cancel(self.settings, action_id=action_id,
                                   user_id=user["id"],
                                   is_admin=user["role"] == "admin")
        self._redirect(f"/requests?said={got}")

    def _retry_action(self, user: dict) -> None:
        """A failed command, queued again as a new row (C7)."""
        if not self.settings.web_mutations:
            return self._html(403, pages.page(
                "Disabled", "<h2>Actions are not switched on yet</h2>",
                user=user))
        from ..store import actions as store_actions

        action_id = int(self.path.split("/")[2])
        got = store_actions.retry(self.settings, action_id=action_id,
                                  user_id=user["id"],
                                  is_admin=user["role"] == "admin")
        self._redirect(f"/requests?said="
                       f"{'queued' if isinstance(got, int) else got}")

    # -------------------------------------------------------------- pools
    #: The header the script sets on a press it can answer with one row.
    #:
    #: Without it every press - a Free, a Save, a Remove on one address -
    #: answered 303 to the dashboard, and the page the script then
    #: fetched and DOMParsed to lift one `<tr>` out of was the whole
    #: dashboard. A scriptless browser sends no such header and gets the
    #: redirect it has always got, which is the contract the whole live
    #: layer rests on (pages, "the one script").
    ROW_ASKED = "X-GF-Row"

    def _row_answer(self, kind: str, address: str, said: str,
                    user: dict) -> bool:
        """Answer a one-row press with that row and the banner.

        False when this request did not ask for it, in which case the
        caller redirects as it always has.
        """
        if self.headers.get(self.ROW_ASKED) != kind or not address:
            return False
        try:
            listed = read.pool_sheet(self.settings, kind)
        except Exception as exc:                              # noqa: BLE001
            # The work is done; only drawing the answer failed. The
            # redirect still tells the page where to look.
            log.warning("could not read %s back after the press (%s)",
                        address, exc)
            return False
        want = str(address).strip().casefold()
        row = next((r for r in (listed.get(kind) or [])
                    if str(r.get("address") or "").strip().casefold() == want),
                   None)
        self._html(200, pages.row_answer(
            kind, row, said, user, self._said_note(said),
            manual_login=self.settings.manual_login))
        return True

    def _act(self, user: dict, permission: str, verb: str, payload: dict,
             *, idem: str, back: str, said_word: str = "done",
             row_of: str = "") -> None:
        """Queue one command, or record that it was refused.

        The person's name rides in the payload so the pass can write it
        into the sheet's notes. A refusal is a row too - `refused`, with
        the permission named - so the Requests page says what was asked
        and why nothing happened, instead of a 403 nobody remembers.
        `permission` is one of the users' ticks, or "admin" for the
        service controls, which no tick grants."""
        if not self.settings.web_mutations:
            return self._html(403, pages.page(
                "Disabled", "<h2>Actions are not switched on yet</h2>",
                user=user))
        from ..store import actions as store_actions
        from ..store.users import may

        payload = dict(payload, by=user["username"], by_id=user["id"])
        allowed = (user.get("role") == "admin" if permission == "admin"
                   else may(user, permission))
        if not allowed:
            store_actions.record_refused(
                self.settings, verb=verb, payload=payload,
                requested_by=user["id"],
                reason=f"{user['username']} may not do this - "
                       + ("only an admin drives the service"
                          if permission == "admin" else
                          f"permission {permission} is off"))
            return self._redirect(_said_url(back, "refused"))
        # The same button pressed twice for the same thing is one
        # request, not two the pass would refuse a minute apart.
        needle = str(payload.get("serial") or payload.get("name")
                     or payload.get("address") or "")
        try:
            twin = store_actions.pending_for(self.settings, verb=verb,
                                             needle=needle)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("pending check skipped (%s)", exc)
            twin = None
        if twin is not None:
            return self._redirect(_said_url(back, f"already:{twin}"))
        req = store_actions.enqueue(self.settings, verb=verb, payload=payload,
                                    requested_by=user["id"], idem_key=idem)
        # The same drawing of the button, sent again: one row, and it was
        # carried out the first time. Said so, rather than "Queued" over a
        # row that is already finished (2026-09-14). `enqueued` has known
        # this all along - it is which branch it took - and the answer
        # used to be re-derived by comparing the database host's clock to
        # this container's with a one-second tolerance (2026-09-21).
        if not getattr(req, "fresh", True):
            return self._redirect(_said_url(back, f"twice:{req}"))
        # `said_word` is what the press was FOR, not what it did. The work
        # runs here now, so its own verdict is known before the redirect -
        # and it was thrown away: a refusal, a failure and a success all
        # left as one green tick reading "Done". The dashboard's own Build
        # button was refused every time somebody left Gmail on "auto", and
        # said "Done - it is already in" (the operator, 2026-09-07).
        ran = self._ran_it_now(verb, payload, req)
        if ran is None:
            # Nothing ran here, so the lane has it. Ring only now: the
            # bell used to go before the inline attempt, which is an
            # invitation for the keeper to claim a row this request was
            # about to work (2026-09-21).
            signals.ring(signals.queued)
        if ran == "done":
            said = f"{said_word}:{req}"
        elif ran is not None:
            # Deliberately not `refused`, which both banner tables already
            # use for "you do not have that tick" and would read as a
            # missing permission rather than an answer.
            said = f"no:{req}"
        else:
            said = f"queued:{req}"
        # One row, when the press was about one row and the page asked
        # for it that way: ~1KB instead of the whole dashboard, and no
        # document to parse at the other end.
        if row_of and self._row_answer(row_of, str(payload.get("address")
                                                   or ""), said, user):
            return None
        self._redirect(_said_url(back, said))

    @staticmethod
    def _mark_twice(rows: list[dict], key: str = "address") -> None:
        """Flag a row whose address is on an earlier line of this paste.

        Two overlapping copies of a range both read "ok", the button said
        "Add 2 (skip 0)", and the verb quietly added one - so the count on
        the button was not the count that went in (2026-09-07).
        """
        seen: set[str] = set()
        for row in rows:
            here = str(row.get(key) or "").strip().lower()
            if not here:
                continue
            row["twice"] = here in seen
            seen.add(here)

    def _said_note(self, said: str) -> str:
        """The verb's own sentence for a press that did not go through.

        It is settled on the request's row a moment before the redirect -
        "the Gmail x@y is not free", "16 gmails added, 1 already in the
        pool, 1 refused" - and only that sentence can name the address and
        say why. Read here rather than carried in the address bar, because
        an address in a query string is the one thing that must not be
        (2026-09-07).
        """
        word, _, req = (said or "").partition(":")
        # Any token carrying a request id, not only `no`. The condition
        # was `word != "no"`, so a press that WORKED had its sentence
        # read off the row and thrown away: free_gmail settles
        # "x@y is back on the shelf" and the operator was shown
        # "Done - it is already in.", which is a sentence about pasting
        # stock (the operator, 2026-09-20). `_said` prefers the note and
        # falls back to the table, so the general word stays for the
        # tokens that carry no id.
        if not req.isdigit():
            return ""
        from ..store import actions as store_actions

        try:
            row = store_actions.one(self.settings, int(req))
        except Exception as exc:                                  # noqa: BLE001
            log.debug("could not read request %s back (%s)", req, exc)
            return ""
        return str((row or {}).get("result") or "")

    def _ran_it_now(self, verb: str, payload: dict, req: int) -> str | None:
        """Do the work here, in the request that asked for it, and answer
        with the verdict it reached - None when it did not run at all.

        The queue existed because only the pass could write the sheet.
        With the pools in the store that is no longer true of stock, and
        waiting up to thirty seconds to be told a pasted account was
        accepted is the difference between a tool and a form.

        Still a row in `actions`, written first and settled after: the
        Requests page is the record of what was asked and by whom, and a
        command that skipped it would be one nobody could audit. What
        changes is who runs it and when, not whether it is written down.

        The work itself is in `runner`, outside this package, because the
        web may not import the book - see that module's first paragraph.
        """
        from ..runner import run_now
        from ..store import actions as store_actions

        # Claimed before it is worked, which every other writer in the
        # system does and this one did not. `take_batch` claims any
        # queued row, five verbs are in both sets, and a duplicated
        # `build_by_hand` is two phones and two accounts spent.
        #
        # A row somebody else already has is not ours to run: answer
        # None and let the redirect say it is queued, which it is.
        try:
            if not store_actions.claim(self.settings, req):
                log.info("%s #%s was taken by the lane first", verb, req)
                return None
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not claim %s #%s (%s); leaving it to the "
                        "lane", verb, req, exc)
            return None

        outcome = run_now(self.settings, verb, payload)
        if outcome is None:
            # Claimed and not run: hand it straight back rather than
            # leaving it `running` for `expire_running` to close in an
            # hour with a sentence about a restart that never happened.
            try:
                store_actions.settle(self.settings, req, status="queued",
                                     result="", detail=None)
            except Exception as exc:                              # noqa: BLE001
                log.warning("could not put %s #%s back (%s)", verb, req, exc)
            return None
        status, said, detail = outcome
        try:
            store_actions.settle(self.settings, req, status=status,
                                 result=said, detail=detail)
        except Exception as exc:                                  # noqa: BLE001
            # The work is done; only the record of it failed. Saying it
            # was queued would be a lie in the other direction.
            log.warning("%s ran but could not be settled (%s)", verb, exc)
        return status

    def _undo_remove(self, user: dict, kind: str, field: dict) -> None:
        """Put back the row a remove took out, from what that request kept.

        A remove records the cells it removed in its detail - it always
        has, so Requests could put a row back by hand. This is the same
        thing pressed from the toast: the request is read, its cells
        become one row for the pool's own add verb, and the add runs the
        way a paste would, judged by the same reader. Nothing the remove
        did not keep can come back, and nothing that is not a remove of
        this person's - or an admin's view of one - is undone.
        """
        from ..store import actions as store_actions

        want = {"gmail": ("remove_gmail", "may_add_gmail", "add_gmails"),
                "gpt": ("remove_app", "may_add_gpt", "add_gpt"),
                "spotify": ("remove_app", "may_add_gpt", "add_spotify"),
                }.get(kind)
        req = str(field.get("req") or "").strip()
        if want is None or not req.isdigit():
            return self._redirect("/?said=none")
        verb, permission, add_verb = want
        try:
            row = store_actions.one(self.settings, int(req))
        except Exception as exc:                                  # noqa: BLE001
            log.warning("undo: could not read request %s (%s)", req, exc)
            row = None
        kept = ((row or {}).get("detail") or {}).get("removed") or {}
        if (row is None or row.get("verb") != verb
                or row.get("status") != "done" or not kept.get("Address")):
            return self._redirect("/?said=gone")
        if user.get("role") != "admin" and row.get("requested_by") != user["id"]:
            return self._redirect("/?said=refused")
        # A remove keeps the product, so a Spotify row cannot come back
        # through the GPT door as a GPT account, nor the other way round
        # (2026-09-17).
        product = str(kept.get("Product") or "").strip().lower()
        if (product == "spotify") != (kind == "spotify"):
            return self._redirect("/?said=gone")
        secret = str(kept.get("Secret") or kept.get("2FA Secret") or "").strip()
        if kind == "spotify":
            payload = {"rows": [{"address": kept["Address"],
                                 "password": kept.get("Password") or ""}],
                       "category": str(kept.get("Category") or "").strip()}
        elif kind == "gmail":
            payload = {"rows": [{"address": kept["Address"],
                                 "password": kept.get("Password") or "",
                                 "secret": "" if "@" in secret else secret,
                                 "recovery": secret if "@" in secret else ""}],
                       "seller": str(kept.get("Seller") or "").strip(),
                       # The remove kept the date and the add honours it;
                       # only this was dropping it, so Undo put a six-week
                       # -old batch back as bought today (2026-09-07).
                       "purchased": str(kept.get("Purchase Date")
                                        or "").strip()}
        else:
            payload = {"rows": [{"address": kept["Address"],
                                 "password": kept.get("Password") or "",
                                 "secret": secret,
                                 "email_code_only": str(
                                     kept.get("Email code") or ""
                                 ).strip().upper() == "TRUE"}]}
        return self._act(user, permission, add_verb, payload,
                         idem=f"undo-{req}", back="/")

    def _login_accounts(self, user: dict, addresses: list,
                        back: str = "/", serial: str = "") -> None:
        """"Log in selected" (C6), off the dashboard or the Gpt Pool -
        `back` is whichever the ticks were on. Only meaningful with
        manual login on: off, the pass logs accounts in by itself and
        the button would race it for the same rows."""
        if not self.settings.manual_login:
            return self._redirect(f"{back}?said=auto")
        chosen = [a.strip() for a in addresses if a and a.strip()]
        if not chosen:
            return self._redirect(_said_url(back, "none"))
        payload = {"addresses": chosen}
        if serial:
            payload["serial"] = serial            # the chooser named a phone
        return self._act(user, "may_login_accounts", "login_accounts",
                         payload,
                         idem=self._minute_key(
                             user, "login", ",".join(sorted(chosen))),
                         back=back)

    def _progress_of(self, rows: list[dict]) -> dict:
        """The latest captured log line per phone a running login is
        working, for the Requests sub-rows. Never fatal: a page without
        the step text still shows the phones."""
        serials = [str(ph.get("serial") or "")
                   for r in rows if r.get("status") == "running"
                   and isinstance(r.get("detail"), dict)
                   for ph in r["detail"].get("phones") or []
                   if ph.get("ok") is None and ph.get("serial")]
        if not serials:
            return {}
        try:
            return read.latest_lines(self.settings, serials)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("the phones' log lines did not load (%s)", exc)
            return {}

    def _minute_key(self, user: dict, verb: str, target: str) -> str:
        """One key per press.

        The page stamps every form when it draws it, so the same button
        pressed again after the page moved is a new request and the same
        drawing sent twice - a double-tap, a back-button re-POST - is
        one. A form without the stamp (an old tab, a POST made by hand)
        falls back to the wall-clock minute, which is what this always
        was: and what folded a second Test inside the same minute into
        the first, already finished, and answered "Queued" over nothing
        (the operator, 2026-09-14)."""
        press = getattr(self, "_press", "") or str(int(time.time()) // 60)
        return f"{verb}:{target}:{user['id']}:{press}"

    def _proxy_state(self) -> tuple[list, list, dict]:
        """What the pass keeps about exits outside the rows: the ones
        GeeLark holds that the tab never heard of (minus the ones a
        person said to ignore), the ignored triples themselves, and the
        test stamps by name. Each key is read defensively - the store
        hands back whatever was last written, and a page must not fall
        over a shape it did not expect."""
        from ..store import state as store_state

        held = store_state.get(self.settings, "unlisted_proxies", []) or []
        kept = store_state.get(self.settings, "ignored_proxies", []) or []
        ignored = [k for k in kept if isinstance(k, str)] \
            if isinstance(kept, list) else []
        stamps = store_state.get(self.settings, "proxy_tests", {}) or {}
        tests = stamps if isinstance(stamps, dict) else {}
        unlisted = [u for u in held if isinstance(u, dict)
                    and _proxy_key(u) not in set(ignored)]
        return unlisted, ignored, tests

    def _switches(self) -> dict:
        """The flags the admin's footer line lists, off Settings."""
        return {name: bool(getattr(self.settings, name, False))
                for name in ("web_mutations", "manual_login", "log_db",
                             "pools_in_pg", "web_user_admin")}

    # ------------------------------------------------ phones and service
    def _held_by_somebody_else(self, user: dict, serial: str) -> str:
        """Who is holding this phone, when it is not the person asking.

        The page drew the rule and nothing enforced it, so the press went
        through on a POST the page had not offered - and Failed deletes
        the phone within seconds and frees the account on it
        (2026-09-07). Read here, once, for every door that acts on a
        phone.
        """
        return self._holder_of(user, serial)[1]

    def _gmail_for_the_holder(self, user: dict, serial: str) -> dict | None:
        """The phone's Gmail, password and authenticator key, for the
        Live tab's margin (the operator, 2026-09-16) - to whoever holds
        the phone, or an admin, and nobody else. Never fatal: a store
        that will not answer costs the margin, not the screen."""
        try:
            held, theirs = self._holder_of(user, serial)
            if theirs:
                return None
            return read.gmail_on_phone(self.settings, serial)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("gmail for %s not read (%s)", serial, exc)
            return None

    def _account_for_the_holder(self, user: dict, serial: str) -> dict | None:
        """The app account signed into the phone, for the same margin and
        under the same rule as the Gmail above: the holder or an admin,
        nobody else, and never fatal (2026-09-19)."""
        try:
            held, theirs = self._holder_of(user, serial)
            if theirs:
                return None
            return read.account_on_phone(self.settings, serial)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("account for %s not read (%s)", serial, exc)
            return None

    def _holder_of(self, user: dict, serial: str) -> tuple[str, str]:
        """(who holds the phone, who holds it against this person). The
        second is "" for the holder and for an admin (2026-09-15); the
        first is the name an admin's request carries."""
        try:
            story = read.phone_story(self.settings, serial)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("could not read phone %s back (%s)", serial, exc)
            return "", ""
        phone = (story or {}).get("phone") or {}
        return pages._holder(phone), pages._theirs(user, phone)

    def _refuse(self, user: dict, verb: str, payload: dict,
                why: str) -> None:
        """Say no, and leave the same record a refused permission leaves."""
        from ..store import actions as store_actions

        try:
            store_actions.record_refused(
                self.settings, verb=verb,
                payload=dict(payload, by=user["username"], by_id=user["id"]),
                requested_by=user["id"], reason=why)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not record the refusal (%s)", exc)
        return self._redirect(_said_url("/", "refused"))

    def _phone_state(self, user: dict, serial: str, field: dict) -> None:
        """Take / Back / Done / Failed off the dashboard's table or the
        phone's own story (`back` says which). The two that delete the
        phone ask once, on a page that says so."""
        state = (field.get("state") or "").strip().lower()
        plan = pages.PHONE_STATES.get(state)
        if plan is None or not serial.isdigit():
            return self._html(404, pages.page(
                "404", "<h2>Not a State word</h2>", user=user))
        held, theirs = self._holder_of(user, serial)
        if theirs:
            return self._refuse(
                user, "set_phone_state", {"serial": serial, "state": state},
                f"phone {serial} is with {theirs} - the three ways a phone "
                f"comes back belong to whoever is holding it")
        payload = {"serial": serial, "state": state}
        if held and held != user.get("username"):
            # An admin ending somebody else's hold (2026-09-15): said on
            # the request, so whoever comes back to find their phone
            # gone can read who did it and why.
            payload["held_by"] = held
        back = _phone_back(field, serial)
        if plan["sure"] and field.get("sure") != "1":
            return self._html(200, pages.confirm_page(
                user, title=f"Mark phone {serial} {state}?",
                text=plan["text"], action=f"/phones/{serial}/state",
                fields={"state": state, "sure": "1", "back": back},
                button=f"Yes, phone {serial} is {state}", back=back))
        if state == "unused":
            self._power_off(user, serial)
        return self._act(user, "may_take_phones", "set_phone_state", payload,
                         idem=self._minute_key(user, f"state-{state}", serial),
                         back=back, said_word=plan["said"])

    def _power_off(self, user: dict, serial: str) -> None:
        """Release also stops the phone in GeeLark, so it stops billing
        (the operator, 2026-09-08). A second command beside the mark,
        for the lane: the mark itself runs here in the request, and
        stopping a phone is GeeLark's business. Never fatal, and only
        for somebody who may take phones - the mark is refused for
        anybody else, and so is this."""
        from ..store import actions as store_actions
        from ..store.users import may

        if not self.settings.web_mutations or not may(user, "may_take_phones"):
            return
        try:
            store_actions.enqueue(
                self.settings, verb="power_off_phone",
                payload={"serial": serial, "by": user["username"],
                         "by_id": user["id"]},
                requested_by=user["id"],
                idem_key=self._minute_key(user, "off", serial))
        except Exception as exc:                                  # noqa: BLE001
            log.warning("phone %s: power-off not queued (%s)", serial, exc)

    def _screen(self, user: dict, path: str) -> None:
        """One archived screen, as the plain text it is - and only one
        of this phone's, inside artifact_dir (read.screen_file guards
        the path). Anything else is a 404, never a listing."""
        serial, _, rest = path[len("/phones/"):].partition("/screens/")
        folder, _, name = rest.partition("/")
        found = (read.screen_bytes(self.settings, serial, folder, name)
                 if serial.isdigit() else None)
        if found is None:
            return self._html(404, pages.page(
                "404", "<h2>No such screen</h2>", user=user))
        return self._text(200, found.decode("utf-8", errors="replace"))

    def _service(self, user: dict, what: str, field: dict) -> None:
        """Pause / Resume / Clear breaker / Stop / Start. Every one asks
        first - each changes what the next pass does to every phone at
        once - and all but one are the admin's.

        The exception is Clear breaker. The breaker means "builds keep
        failing", and the answer to it is nearly always fresh stock, which
        is the one thing an operator is trusted to add: they could paste a
        batch of Gmails and then had to find an admin before the farm
        would use them (the operator, 2026-09-07).
        """
        from ..store.users import may

        need = "may_add_gmail" if what == "clear_breaker" else "admin"
        allowed = (user.get("role") == "admin" if need == "admin"
                   else may(user, need))
        if not allowed:
            return self._html(403, pages.page(
                "403", "<h2>Only an admin drives the service</h2>",
                user=user))
        plan = pages.CONTROLS.get(what)
        if plan is None:
            return self._html(404, pages.page(
                "404", "<h2>Not a service control</h2>", user=user))
        if field.get("sure") != "1":
            return self._html(200, pages.confirm_page(
                user, title=f"{plan['label']}?", text=plan["text"],
                action=f"/service/{what}", fields={"sure": "1"},
                button=f"Yes, {plan['label'].lower()}", back="/"))
        return self._act(user, need, "control", {"what": what},
                         idem=self._minute_key(user, "control", what),
                         back="/")

    def _needs_post(self, user: dict, field: dict) -> None:
        """Offer again (a set-aside gmail or account) and Clear tries (a
        given-up phone), off the Needs attention page."""
        if user["sees"] != "all":
            return self._html(403, pages.forbidden(user))
        if self.path == "/needs/clear":
            serial = (field.get("serial") or "").strip()
            return self._act(user, "may_take_phones", "clear_tries",
                             {"serial": serial},
                             idem=self._minute_key(user, "clear", serial),
                             back="/needs")
        kind = (field.get("kind") or "").strip()
        permission = pages.OFFER_PERMISSION.get(kind)
        if permission is None:
            return self._html(404, pages.page(
                "404", "<h2>Nothing to offer again</h2>", user=user))
        address = (field.get("address") or "").strip()
        return self._act(user, permission, "offer_again",
                         {"address": address, "kind": kind},
                         idem=self._minute_key(user, "offer", address),
                         back="/needs")

    def _spotify_post(self, user: dict, field: dict, path: str) -> None:
        """The Spotify pool's four doors.

        Its own handler because its rows carry one thing the GPT rows do
        not - the category, which says which phone the account may go on
        - and because it has no second factor to ask about. Everything
        else is the app pool's: the same table, the same verbs for
        editing, removing and freeing a row (2026-09-17).
        """
        from ..store import validate
        from ..verbs import SPOTIFY_CATEGORIES
        from . import paste

        category = (field.get("category") or "").strip().lower()
        # A kind the form did not name is not quietly `normal`: the add
        # verb refuses it and the edit leaves the row's own kind alone.
        # Only the preview, which writes nothing, shows the first.
        known_kind = category in SPOTIFY_CATEGORIES
        back = _add_back(field, "/")
        if path == "/pools/spotify/preview":
            known = read.known(self.settings, "app")
            pasted = field.get("pasted") or ""
            rows = paste.accounts(pasted)
            for row in rows:
                try:
                    if row["secret"] or row["recovery"]:
                        raise validate.AccountError(
                            f"{row['address']}: three things on one line - a "
                            f"Spotify account is an address and a password")
                    validate.app_row(address=row["address"],
                                     password=row["password"])
                except (validate.AccountError, validate.ProxyError) as exc:
                    log.debug("spotify paste row refused: %s", exc)
                    row["error"] = str(exc)
                here = row["address"].lower()
                row["duplicate"] = here in known
                row["dup_state"] = known.get(here, "")
            self._mark_twice(rows)
            return self._html(200, pages.spotify_preview(
                rows, user, idem=secrets.token_urlsafe(12), pasted=pasted,
                category=category if known_kind else SPOTIFY_CATEGORIES[0],
                back=back))
        if path == "/pools/spotify/add":
            rows = [{"address": r["address"], "password": r["password"]}
                    for r in paste.accounts(field.get("rows", ""))]
            return self._act(
                user, "may_add_gpt", "add_spotify",
                {"rows": rows, "category": category},
                idem=field.get("idem") or secrets.token_urlsafe(12),
                back=back)
        if path == "/pools/spotify/edit":
            address = (field.get("address") or "").strip()
            return self._act(
                user, "may_add_gpt", "edit_app",
                {"address": address,
                 "new_address": (field.get("new_address") or "").strip(),
                 "password": field.get("password") or "",
                 "category": category if known_kind else "",
                 "state": (field.get("state") or "").strip()},
                idem=self._minute_key(user, "edit_app", address), back=back)
        if path == "/pools/spotify/remove":
            address = (field.get("address") or "").strip()
            if field.get("sure") != "1":
                return self._html(200, pages.confirm_page(
                    user, title=f"Remove {address} from the pool?",
                    text=("The row leaves the Spotify pool. Nothing else is "
                          "touched, and the request keeps the row so it can "
                          "be put back."),
                    action="/pools/spotify/remove",
                    fields={"address": address, "sure": "1", "back": back},
                    button=f"Yes, remove {address}", back=back))
            return self._act(user, "may_add_gpt", "remove_app",
                             {"address": address},
                             # Its own word, so the toast's Undo comes
                             # back through the Spotify door and the row
                             # returns as what it was (2026-09-17).
                             said_word="removed-spotify",
                             idem=self._minute_key(user, "remove_app",
                                                   address),
                             back=back)
        if path == "/pools/spotify/undo":
            return self._undo_remove(user, "spotify", field)
        return self._html(404, pages.page("404", "<h2>Nothing here</h2>",
                                          user=user))

    def _pool_post(self, user: dict, field: dict) -> None:
        from . import paste

        path = self.path
        if path == "/pools/gmail/preview":
            from ..store import validate

            known = read.known(self.settings, "gmail")
            # The one-by-one form is the paste form with three boxes:
            # its fields become one pasted line so both are judged by
            # the same reader and confirmed on the same page.
            pasted = field.get("pasted") or "\t".join(
                (field.get(k) or "").strip()
                for k in ("address", "password", "second")
                if (field.get(k) or "").strip())
            rows = paste.accounts(pasted)
            seller = ((field.get("new_seller") or "").strip()
                      or (field.get("seller") or "").strip())
            for row in rows:
                try:
                    validate.gmail_row(address=row["address"],
                                       password=row["password"],
                                       secret=row["recovery"] or row["secret"],
                                       seller=seller)
                except (validate.AccountError, validate.ProxyError) as exc:
                    log.debug("gmail paste row refused: %s", exc)
                    row["error"] = str(exc)
                here = row["address"].lower()
                row["duplicate"] = here in known
                row["dup_state"] = known.get(here, "")
            self._mark_twice(rows)
            return self._html(200, pages.gmail_preview(
                rows, seller, user, idem=secrets.token_urlsafe(12),
                pasted=pasted, sellers=read.gmail_sellers(self.settings),
                back=_add_back(field, "/pools/gmail")))
        if path == "/pools/gmail/add":
            rows = [{"address": r["address"], "password": r["password"],
                     "secret": r["secret"], "recovery": r["recovery"]}
                    for r in paste.accounts(field.get("rows", ""))]
            return self._act(user, "may_add_gmail", "add_gmails",
                             {"rows": rows,
                              "seller": (field.get("seller") or "").strip(),
                              # Blank means today, which is what it always
                              # meant; the field only lets a person say
                              # otherwise for stock bought a while ago.
                              "purchased": (field.get("purchased")
                                            or "").strip()},
                             idem=field.get("idem") or secrets.token_urlsafe(12),
                             back=_add_back(field, "/pools/gmail"))
        if path == "/pools/gmail/edit":
            # The row editor: the address names the row, everything else
            # is what it should say now. Judged by the verb against the
            # same rule a pasted row is judged by.
            address = (field.get("address") or "").strip()
            return self._act(
                user, "may_add_gmail", "edit_gmail",
                {"address": address,
                 "new_address": (field.get("new_address") or "").strip(),
                 "password": field.get("password") or "",
                 "secret": (field.get("secret") or "").strip(),
                 "clear_secret": (field.get("clear_secret") or "").strip(),
                 "seller": (field.get("seller") or "").strip(),
                 "purchased": (field.get("purchased") or "").strip(),
                 "state": (field.get("state") or "").strip()},
                idem=self._minute_key(user, "edit_gmail", address),
                back=_gmail_back(field), row_of="gmail")
        if path == "/pools/gmail/remove":
            address = (field.get("address") or "").strip()
            back = _gmail_back(field)
            if field.get("sure") != "1":
                return self._html(200, pages.confirm_page(
                    user, title=f"Remove {address} from the pool?",
                    text=("The row leaves the Gmails tab. Nothing else is "
                          "touched - Google still has the account, and the "
                          "request keeps the row so it can be put back."),
                    action="/pools/gmail/remove",
                    fields={"address": address, "sure": "1", "back": back},
                    button=f"Yes, remove {address}", back=back))
            return self._act(user, "may_add_gmail", "remove_gmail",
                             {"address": address}, said_word="removed-gmail",
                             idem=self._minute_key(user, "remove_gmail",
                                                   address),
                             back=back, row_of="gmail")
        if path == "/pools/gmail/refund":
            address = (field.get("address") or "").strip()
            state = (field.get("state") or "").strip()
            return self._act(user, "may_add_gmail", "refund_gmail",
                             {"address": address, "state": state},
                             idem=self._minute_key(user, "refund_gmail",
                                                   f"{address}:{state}"),
                             back=_gmail_back(field))
        if path in ("/pools/gmail/undo", "/pools/gpt/undo"):
            return self._undo_remove(user, path.split("/")[2], field)
        if path in ("/pools/gmail/free", "/pools/gpt/free",
                    "/pools/spotify/free"):
            kind = path.split("/")[2]
            permission, verb = (("may_add_gmail", "free_gmail")
                                if kind == "gmail" else
                                ("may_add_gpt", "free_app"))
            address = (field.get("address") or "").strip()
            return self._act(user, permission, verb, {"address": address},
                             idem=self._minute_key(user, verb, address),
                             back=_add_back(field, f"/pools/{kind}"),
                             row_of=kind)
        if path == "/pools/proxy/preview":
            from ..store import validate

            known = read.known(self.settings, "proxy")
            # The one-by-one form is the paste form with five boxes: the
            # fields become one pasted line so both are judged by the
            # same reader and confirmed on the same page.
            pasted = field.get("pasted") or _one_proxy_line(field)
            rows = paste.proxies(pasted)
            for row in rows:
                try:
                    checked = validate.proxy_row(raw=row["raw"],
                                                 name=row["name"])
                    here = f"{checked['host']}:{checked['port']}"
                    row["duplicate"] = here in known
                    row["dup_state"] = known.get(here, "")
                except (validate.AccountError, validate.ProxyError) as exc:
                    log.debug("proxy paste row refused: %s", exc)
                    row["error"] = str(exc)
            self._mark_twice(rows, "raw")
            return self._html(200, pages.proxy_preview(
                rows, user, idem=secrets.token_urlsafe(12),
                back=_add_back(field, "/pools/proxy")))
        if path == "/pools/proxy/add":
            rows = [{"raw": r["raw"], "name": r["name"]}
                    for r in paste.proxies(field.get("rows", ""))]
            return self._act(user, "may_change_proxy", "add_proxies",
                             {"rows": rows},
                             idem=field.get("idem") or secrets.token_urlsafe(12),
                             back=_add_back(field, "/pools/proxy"))
        if path in ("/pools/proxy/free", "/pools/proxy/test",
                    "/pools/proxy/remove"):
            verb = {"free": "mark_proxy_free", "test": "test_proxy",
                    "remove": "remove_proxy"}[path.rsplit("/", 1)[1]]
            name = (field.get("name") or "").strip()
            back = _proxy_back(field)
            if verb == "remove_proxy" and field.get("sure") != "1":
                # The one button on the pools that takes something away:
                # a second page asks, with the name on it, before it queues.
                return self._html(200, pages.confirm_page(
                    user, title=f"Remove {name} from the pool?",
                    text=(f"{name} leaves the Proxy tab. GeeLark's own copy "
                          f"is not touched, so it shows up under 'held by "
                          f"GeeLark, not in the pool' until removed there "
                          f"by hand. A dead exit is better kept: revive it "
                          f"at the vendor and test again."),
                    action="/pools/proxy/remove",
                    fields={"name": name, "sure": "1", "back": back},
                    button=f"Yes, remove {name}", back=back))
            # Whoever may change a phone's exit may keep the exits: it
            # was the admin's alone (2026-09-08).
            return self._act(user, "may_change_proxy", verb, {"name": name},
                             idem=self._minute_key(user, verb, name),
                             back=back)
        if path == "/pools/proxy/test-all":
            return self._act(user, "may_change_proxy", "test_all_proxies", {},
                             idem=self._minute_key(user, "test_all", "-"),
                             back=_proxy_back(field))
        if path == "/pools/proxy/free-all":
            # The whole set-aside list, after their addresses were changed
            # at the vendor. Each is tested first, so this frees what
            # answers and leaves what does not (2026-09-14).
            return self._act(user, "may_change_proxy", "free_all_proxies", {},
                             idem=self._minute_key(user, "free_all", "-"),
                             back=_proxy_back(field))
        if path == "/pools/proxy/ignore":
            # "Ignore" on an exit GeeLark holds that the tab never heard
            # of: the triple goes on a list the pass keeps, and the page
            # stops reporting it. Undone by editing that list, not here.
            triple = {k: (field.get(k) or "").strip()
                      for k in ("host", "port", "username")}
            if not triple["host"]:
                return self._redirect(
                    _said_url(_proxy_back(field), "gone"))
            return self._act(user, "admin", "ignore_proxy", triple,
                             idem=self._minute_key(
                                 user, "ignore", _proxy_key(triple)),
                             back=_proxy_back(field))
        if path == "/pools/proxy/restore":
            # "Put it back" on a done remove (Requests): the row the verb
            # wrote into the request's detail, added again under the same
            # name. It is tested on arrival like any other add.
            name = (field.get("name") or "").strip()
            raw = (field.get("raw") or "").strip()
            if not raw:
                return self._redirect("/requests?said=gone")
            return self._act(user, "admin", "add_proxies",
                             {"rows": [{"raw": raw, "name": name}]},
                             idem=self._minute_key(user, "restore",
                                                   name or raw),
                             back="/requests")
        if path == "/pools/proxy/adopt":
            from ..store import state as store_state

            wanted = {k: (field.get(k) or "").strip()
                      for k in ("host", "port", "username")}
            # The password comes from what the pass kept, never the form.
            held = next((u for u in store_state.get(
                self.settings, "unlisted_proxies", []) or []
                if all(str(u.get(k, "")) == wanted[k] for k in wanted)), None)
            if held is None:
                return self._redirect(
                    _said_url(_proxy_back(field), "gone"))
            return self._act(user, "admin", "adopt_proxy", held,
                             idem=self._minute_key(
                                 user, "adopt", f"{held['host']}:{held['port']}"),
                             back=_proxy_back(field))
        if path.startswith("/pools/spotify/"):
            return self._spotify_post(user, field, path)
        if path == "/pools/gpt/preview":
            from ..store import validate
            from ..verbs import GPT_CATEGORIES

            known = read.known(self.settings, "app")
            pasted = field.get("pasted") or ""
            category = (field.get("category") or "").strip().lower()
            if category not in GPT_CATEGORIES:
                category = ""
            eco = category == "eco"
            rows = paste.accounts(pasted)
            for row in rows:
                try:
                    if row["recovery"]:
                        raise validate.AccountError(
                            f"{row['address']}: two addresses on one line - "
                            f"an app account has no recovery address")
                    if eco and (row["password"] or row["secret"]):
                        raise validate.AccountError(
                            f"{row['address']}: an eco account is an address "
                            f"and nothing else, and this line carries a "
                            f"password or a key")
                    validate.app_row(
                        address=row["address"],
                        password="" if eco else row["password"],
                        secret="" if eco else row["secret"],
                        email_code_only=eco)
                except (validate.AccountError, validate.ProxyError) as exc:
                    log.debug("gpt paste row refused: %s", exc)
                    row["error"] = str(exc)
                here = row["address"].lower()
                row["duplicate"] = here in known
                row["dup_state"] = known.get(here, "")
            self._mark_twice(rows)
            return self._html(200, pages.gpt_preview(
                rows, user, idem=secrets.token_urlsafe(12), pasted=pasted,
                back=_add_back(field, "/pools/gpt"), category=category))
        if path == "/pools/gpt/add":
            from ..store import validate

            from ..verbs import GPT_CATEGORIES

            category = (field.get("category") or "").strip().lower()
            if category not in GPT_CATEGORIES:
                category = ""
            if "rows" in field:
                # The confirm off the preview: the good rows, as the
                # tab-separated text the preview showed.
                rows = [{"address": r["address"], "password": r["password"],
                         "secret": r["secret"], "email_code_only": False}
                        for r in paste.accounts(field.get("rows", ""))]
                return self._act(
                    user, "may_add_gpt", "add_gpt",
                    {"rows": rows, "category": category},
                    idem=field.get("idem") or secrets.token_urlsafe(12),
                    back=_add_back(field, "/pools/gpt"))
            row = {"address": (field.get("address") or "").strip(),
                   "password": field.get("password") or "",
                   "secret": (field.get("secret") or "").strip(),
                   "email_code_only": field.get("email_code") == "1"}
            try:
                validate.app_row(**row)
            except (validate.AccountError, validate.ProxyError) as exc:
                # Back to the page with the boxes still filled and the
                # reason beside them - a redirect would empty the form
                # and say only "bad".
                log.info("gpt add refused at the form: %s", exc)
                return self._html(200, pages.gpt_pool_page(
                    read.gpt_pool(self.settings), user, explain=_explain,
                    manual_login=self.settings.manual_login,
                    form=row, error=str(exc)))
            return self._act(user, "may_add_gpt", "add_gpt",
                             {"rows": [row], "category": category},
                             idem=self._minute_key(user, "add_gpt",
                                                   row["address"].lower()),
                             back="/pools/gpt")
        if path == "/pools/gpt/edit":
            # The GPT twin of the Gmail row editor, judged by its own verb
            # against the rule a pasted row is judged by.
            address = (field.get("address") or "").strip()
            return self._act(
                user, "may_add_gpt", "edit_app",
                {"address": address,
                 "new_address": (field.get("new_address") or "").strip(),
                 "password": field.get("password") or "",
                 "secret": (field.get("secret") or "").strip(),
                 "clear_secret": (field.get("clear_secret") or "").strip(),
                 "state": (field.get("state") or "").strip()},
                idem=self._minute_key(user, "edit_app", address),
                back=_add_back(field, "/pools/gpt"))
        if path == "/pools/gpt/remove":
            address = (field.get("address") or "").strip()
            back = _add_back(field, "/pools/gpt")
            if field.get("sure") != "1":
                return self._html(200, pages.confirm_page(
                    user, title=f"Remove {address} from the pool?",
                    text=("The row leaves the Gpt tab. Nothing else is "
                          "touched, and the request keeps the row so it can "
                          "be put back."),
                    action="/pools/gpt/remove",
                    fields={"address": address, "sure": "1", "back": back},
                    button=f"Yes, remove {address}", back=back))
            return self._act(user, "may_add_gpt", "remove_app",
                             {"address": address}, said_word="removed-gpt",
                             idem=self._minute_key(user, "remove_app",
                                                   address),
                             back=back)
        if path == "/pools/gpt/offer":
            address = (field.get("address") or "").strip()
            return self._act(user, "may_add_gpt", "offer_again",
                             {"address": address},
                             idem=self._minute_key(user, "offer", address),
                             back="/pools/gpt")
        self._html(404, pages.page("404", "<h2>Nothing here</h2>", user=user))

    # -------------------------------------------------------------- users
    def _admin_page(self, user: dict) -> str | None:
        """None when this person may see the Users page; else the page
        that says why not. The flag hides the page entirely (404), the
        role refuses it (403) - a 404 to an operator says nothing about
        what exists."""
        if not self.settings.web_user_admin:
            return pages.page("404", "<h2>Nothing here</h2>", user=user)
        if user.get("role") != "admin":
            return pages.forbidden(user)
        return None

    # ------------------------------------------------- the API's own keys
    def _api_clients_page(self, user: dict, said: str = "",
                          error: str = "") -> None:
        """The page, with whatever the last press wants to say on it.

        Admin only, like Users: these are the credentials the machines
        come in with, and the one that matters most hands the farm real
        accounts that cost real phones.
        """
        if user.get("role") != "admin":
            return self._html(403, pages.forbidden(user))
        self._html(200, pages.api_clients_page(
            read.api_clients(self.settings), user, said=said, error=error))

    def _api_clients_get(self, user: dict) -> None:
        if user.get("role") != "admin":
            # A GET an operator followed goes home, the way every other
            # admin page here answers one; a POST refuses instead.
            return self._redirect("/")
        query = parse_qs(self.path.partition("?")[2])
        self._api_clients_page(user, said=(query.get("said") or [""])[0],
                               error=(query.get("error") or [""])[0])

    def _api_clients_new(self, user: dict, field: dict) -> None:
        if user.get("role") != "admin":
            return self._html(403, pages.forbidden(user))
        from ..store import api_clients as store_clients

        name = (field.get("name") or "").strip()
        try:
            new_id, token = store_clients.create(
                self.settings, name=name,
                role=(field.get("role") or "sandbox").strip())
        except ValueError as exc:
            return self._api_clients_page(user, error=str(exc))
        except Exception as exc:                                  # noqa: BLE001
            # The unique index on the name is the likely one, and the
            # answer is not to rotate the key that name already has.
            log.warning("could not mint api client %r: %s", name, exc)
            return self._api_clients_page(
                user, error="that name is taken, or the store refused it - "
                            "to replace a key, press New key on its row")
        log.info("api client %r (id %s) minted by %s", name, new_id,
                 user["username"])
        # Answered here rather than redirected: a token in an address is
        # a token in the history, the log and the ?said= banner.
        self._html(200, pages.new_key_page(name, token, user, minted=True))

    def _api_clients_rotate(self, user: dict, field: dict) -> None:
        if user.get("role") != "admin":
            return self._html(403, pages.forbidden(user))
        from ..store import api_clients as store_clients

        ident = int(self.path.split("/")[2])
        if field.get("sure") != "1":
            row = store_clients.get(self.settings, ident)
            name = str((row or {}).get("name") or ident)
            return self._html(200, pages.confirm_page(
                user, title=f"Mint a new key for {name}?",
                text=("The key it has now stops working the moment this is "
                      "pressed, and whoever holds it is answered 401 until "
                      "they are given the new one."),
                action=f"/api-clients/{ident}/rotate",
                fields={"sure": "1"}, button="Yes, mint a new key",
                back="/api-clients"))
        got = store_clients.rotate(self.settings, ident)
        if got is None:
            return self._api_clients_page(user, error="no client with that id")
        name, token = got
        log.info("api client %r had its key rotated by %s", name,
                 user["username"])
        self._html(200, pages.new_key_page(name, token, user, minted=False))

    def _api_clients_forget(self, user: dict) -> None:
        """Forget the wrong tries this process is holding.

        The lockout is a brake on a key nobody recognises, and during an
        integration the person being braked is usually the author of the
        panel with a stale key in his config. A correct key is never
        held by it (api_v1._right), so this is for clearing the noise
        off the page rather than for letting anybody in (2026-09-19).
        """
        if user.get("role") != "admin":
            return self._html(403, pages.forbidden(user))
        from . import api_v1

        gone = api_v1.clear_refusals()
        log.info("api: %d refused key prefix(es) forgotten by %s", gone,
                 user["username"])
        self._redirect("/api-clients?said=forgot")

    def _api_clients_active(self, user: dict, field: dict) -> None:
        if user.get("role") != "admin":
            return self._html(403, pages.forbidden(user))
        from ..store import api_clients as store_clients

        ident = int(self.path.split("/")[2])
        wanted = (field.get("active") or "").strip() == "1"
        name = store_clients.set_active(self.settings, ident, wanted)
        if name is None:
            return self._api_clients_page(user, error="no client with that id")
        log.info("api client %r switched %s by %s", name,
                 "on" if wanted else "off", user["username"])
        self._redirect("/api-clients?said=" + ("on" if wanted else "off"))

    def _api_clients_webhook(self, user: dict, field: dict) -> None:
        if user.get("role") != "admin":
            return self._html(403, pages.forbidden(user))
        from ..store import api_clients as store_clients

        ident = int(self.path.split("/")[2])
        try:
            name = store_clients.set_webhook(
                self.settings, ident, url=(field.get("url") or ""),
                secret=(field.get("secret") or ""))
        except ValueError as exc:
            return self._api_clients_page(user, error=str(exc))
        if name is None:
            return self._api_clients_page(user, error="no client with that id")
        self._redirect("/api-clients?said=webhook")

    def _users_get(self, user: dict) -> None:
        if (refused := self._admin_page(user)) is not None:
            code = 404 if not self.settings.web_user_admin else 403
            return self._html(code, refused)
        from ..store import users as store_users

        query = parse_qs(self.path.partition("?")[2])
        wanted = (query.get("id") or [""])[0]
        selected = None
        if wanted.isdigit():
            selected = store_users.get(self.settings, int(wanted))
        self._html(200, pages.users_page(
            store_users.listing(self.settings), selected, user,
            store_users.PERMISSIONS,
            said=(query.get("said") or [""])[0],
            error=(query.get("error") or [""])[0]))

    def _users_new(self, user: dict, field: dict) -> None:
        if (refused := self._admin_page(user)) is not None:
            code = 404 if not self.settings.web_user_admin else 403
            return self._html(code, refused)
        from ..store import users as store_users

        username = (field.get("username") or "").strip().lower()
        try:
            new_id, password = store_users.create(
                self.settings, username=username,
                role=field.get("role") or "operator",
                sees=field.get("sees") or "own",
                permissions=_ticks(field))
        except ValueError as exc:
            return self._html(200, pages.users_page(
                store_users.listing(self.settings), None, user,
                store_users.PERMISSIONS, error=str(exc)))
        except Exception as exc:                                  # noqa: BLE001
            # UNIQUE on username is the likely one; the page says so
            # without echoing the driver's sentence.
            log.warning("could not create user %r: %s", username, exc)
            return self._html(200, pages.users_page(
                store_users.listing(self.settings), None, user,
                store_users.PERMISSIONS,
                error="that username is taken, or the store refused it"))
        log.info("user %r (id %s) created by %s", username, new_id,
                 user["username"])
        self._html(200, pages.one_time_page(username, password, user,
                                            created=True))

    def _users_update(self, user: dict, field: dict) -> None:
        if (refused := self._admin_page(user)) is not None:
            code = 404 if not self.settings.web_user_admin else 403
            return self._html(code, refused)
        from ..store import users as store_users

        target = int(self.path.split("/")[2])
        try:
            store_users.update(
                self.settings, target,
                role=field.get("role") or "operator",
                sees=field.get("sees") or "own",
                active=field.get("active") == "1",
                permissions=_ticks(field), by=user["id"])
        except ValueError as exc:
            return self._redirect(f"/users?id={target}&error={_q(str(exc))}")
        _drop_sessions_of(self.settings, target, keep=self._cookie())
        log.info("user id %s updated by %s", target, user["username"])
        self._redirect(f"/users?id={target}&said=saved")

    def _users_reset(self, user: dict, field: dict) -> None:
        """Reset password asks first: the person's current password stops
        working the moment it is pressed, and every session of theirs
        ends."""
        if (refused := self._admin_page(user)) is not None:
            code = 404 if not self.settings.web_user_admin else 403
            return self._html(code, refused)
        from ..store import users as store_users

        target = int(self.path.split("/")[2])
        row = store_users.get(self.settings, target)
        if row is None:
            return self._redirect("/users")
        if field.get("sure") != "1":
            return self._html(200, pages.confirm_page(
                user, title=f"Reset {row['username']}'s password?",
                text=(f"{row['username']}'s current password stops working "
                      f"and every session of theirs ends. A one-time "
                      f"password is shown once on the next page; hand it "
                      f"over privately and they choose their own at their "
                      f"first sign-in."),
                action=f"/users/{target}/reset", fields={"sure": "1"},
                button="Yes, reset it", back=f"/users?id={target}"))
        password = store_users.reset_password(self.settings, target)
        _drop_sessions_of(self.settings, target, keep=self._cookie())
        log.info("password of user id %s reset by %s", target,
                 user["username"])
        self._html(200, pages.one_time_page(row["username"], password, user,
                                            created=False))

    def _password_post(self, user: dict, field: dict) -> None:
        from ..store import users as store_users

        new = field.get("password") or ""
        if new != (field.get("again") or ""):
            return self._html(200, pages.password_page(
                user, "The two do not match."))
        try:
            store_users.set_password(self.settings, user["id"], new)
        except ValueError as exc:
            return self._html(200, pages.password_page(user, str(exc)))
        # `set_password` clears must_change_password in the row itself,
        # and the row is read fresh on the next request - so there is no
        # second copy here to patch any more.
        log.info("user %s chose a password", user["username"])
        self._redirect("/")

    # -------------------------------------------------------------- login
    def _login(self, field: dict) -> None:
        username = (field.get("username") or "").strip()
        if _locked_out(username):
            return self._html(429, pages.login(
                "Too many wrong answers in a row - try again in a few "
                "minutes"))
        from ..store.db import Store

        with Store(self.settings) as store:
            row = store.check_login(username, field.get("password") or "")
        if row is None:
            _note_failure(username)
            return self._html(200, pages.login(
                "The username or password is not right"))
        from ..store import sessions as store_sessions

        with _lock:
            _failures.pop(username, None)
        # One CSRF token per session, checked on every POST but /login,
        # and stored beside the seat rather than in this process - so a
        # deploy no longer turns every open page into a stale form.
        token, _csrf = store_sessions.start(self.settings, row["id"],
                                            hours=SESSION_HOURS)
        self.send_response(303)
        # `Secure` when the request came over TLS - the reverse proxy in
        # front of the console (Caddy, farm.iranspoty.store) says so in
        # X-Forwarded-Proto. Left off over the ssh tunnel, which is plain
        # http on loopback and would otherwise never see the cookie.
        secure = ("; Secure" if (self.headers.get("X-Forwarded-Proto") or ""
                                 ).lower() == "https" else "")
        self.send_header("Set-Cookie",
                         f"gf={token}; HttpOnly; SameSite=Lax; Path=/{secure}")
        self.send_header("Location", "/")
        self.end_headers()

    def _is_new(self, kind: str, address: str) -> bool:
        """Whether the pool has never heard of this address.

        It used to be a second box the person filled instead of the
        picker, and typing one the pool already had meant "add it again",
        silently. Never fatal: a store that cannot answer is read as "we
        have it", because building on a row that exists is the ordinary
        case and a duplicate is the one that costs something.
        """
        if not address:
            return False
        try:
            return address.strip().lower() not in read.known(self.settings,
                                                             kind)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not check whether %r is new (%s)", address, exc)
            return False

    def _entry(self) -> dict | None:
        """The seat behind the cookie, or None.

        The user row comes back fresh on every request rather than frozen
        at login, so a permission taken away takes effect on the next
        click instead of waiting for the session to be ended by hand.

        Once per request, not once per caller: `do_POST` reads it to
        CSRF-check the press and `_user()` read it again from the same
        cookie a few lines later - two lookups for one press
        (2026-09-21). Held on the handler, which lives exactly as long
        as the request does, so "fresh on every request" still holds.
        """
        from ..store import sessions as store_sessions

        held = getattr(self, "_held_entry", _UNREAD)
        if held is not _UNREAD:
            return held
        self._held_entry = store_sessions.find(self.settings, self._cookie())
        return self._held_entry

    def _user(self) -> dict | None:
        entry = self._entry()
        if entry is None:
            return None
        # The csrf token rides in the user dict so every page's header
        # (the logout form) can carry it without a second parameter; so
        # do the flags the shell needs and the rail's counts.
        #
        # The counts are a nine-subquery statement and most presses end
        # in a 303, where nothing ever draws them. Read when a page asks
        # for them, which is the moment they are worth having.
        return dict(entry["user"], csrf=entry.get("csrf", ""),
                    user_admin=self.settings.web_user_admin,
                    mutations=self.settings.web_mutations,
                    nav=_RailCounts(lambda: read.nav_counts(self.settings)))

    def _cookie(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "gf":
                return value
        return ""

    # ----------------------------------------------------------- plumbing
    def _live(self, user: dict) -> None:
        """The stream a page listens on so it moves when the farm does.

        Server-Sent Events, which is one long GET and a line of text per
        change - no library on either side. The page still has its timer;
        this only means it usually does not have to wait for it.

        Held open by a thread of this server, so the number of them is
        capped: a browser refused here keeps the timer and loses nothing
        but the second.
        """
        from . import live

        if live.pulse.hold(1) > live.MAX_STREAMS:
            live.pulse.hold(-1)
            return self._text(503, "too many listeners; the page keeps its "
                                   "own timer")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            # Caddy buffers by default, which holds every tick until the
            # connection ends - which is never.
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            # `?logs=1`: the count that also moves for a log line, for the
            # one page that draws them. Everything else hears only the
            # farm's own moves, so a build's chatter does not redraw the
            # dashboard every few seconds (2026-09-14).
            logs = bool(parse_qs(self.path.partition("?")[2]).get("logs"))
            seen = live.pulse.count(logs=logs)
            self.wfile.write(f"retry: 5000\ndata: {seen}\n\n".encode())
            self.wfile.flush()
            while not self.server.stopping.is_set():
                now = live.pulse.wait(seen, live.KEEPALIVE, logs=logs)
                if now is None:
                    # A comment: it keeps the connection warm and tells a
                    # browser nothing, which is what nothing happening is.
                    self.wfile.write(b": still here\n\n")
                else:
                    seen = now
                    self.wfile.write(f"data: {seen}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            # The tab was closed, which is how every one of these ends.
            log.debug("a live stream closed (%s)", exc)
        finally:
            live.pulse.hold(-1)

    def _client_error(self, user: dict, field: dict) -> None:
        """A throw in the console's own script, into the log table.

        Until this there was no channel at all by which the console
        could report that it had broken for somebody: a throw leaves the
        page looking perfectly ordinary with half its buttons dead, and
        the only way anybody has ever found out is the operator saying
        so (2026-09-20). WARNING, so it is above the INFO the capture
        starts at and stands out in `/logs`.
        """
        log.warning(
            "console broke for %s on %s: %s [build %s]%s",
            user.get("username", "?"),
            str(field.get("where") or "")[:200],
            str(field.get("message") or "")[:500],
            str(field.get("rev") or "")[:40],
            ("\n" + str(field.get("stack") or "")[:2000]
             if field.get("stack") else ""))
        self._text(204, "")

    def _pool_sheet(self, user: dict, path: str) -> None:
        """The manager's drawer for one pool, read when it is pulled.

        All three used to be rendered shut inside every dashboard -
        925,488 of its 1,012,694 bytes, with ~500 passwords and TOTP
        secrets in their data attributes, on the 99 responses in 100
        where nobody opened them (2026-09-20).
        """
        kind = path[len("/pools/"):-len("/sheet")]
        if kind not in pages._POOL_KINDS:
            return self._text(404, "no such pool\n")
        listed = read.pool_sheet(self.settings, kind)
        self._html(200, pages._pool_sheet(
            kind, listed.get(kind) or [], listed.get("totals") or {}, user,
            manual_login=self.settings.manual_login))

    def _asset(self, path: str, *, private: bool) -> None:
        """One of the two files the page links to, under its own hash.

        A name this build does not answer to is a 404 and not a redirect
        to the current one: the only way to ask for a stale name is to
        be a stale page, and a stale page has a stale script to go with
        it - it should reload, which `gf-rev` makes it do.
        """
        from . import assets

        found = assets.served(path)
        if found is None:
            return self._text(404, "no such build\n")
        body, kind, _ = found
        raw = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header(
            "Cache-Control",
            ("private" if private else "public")
            + ", max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(raw)

    def _html(self, code: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Every page is signed in and some carry a secret once - a
        # one-time password, a form handed back with what was typed -
        # so no browser or proxy may keep a copy.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _text(self, code: int, body: str, *, kind: str = "text/plain",
              filename: str = "") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    # HEAD is GET without the body - what an uptime monitor sends. The
    # stdlib answers 501 unless told otherwise (2026-09-03, on the domain).
    do_HEAD = do_GET

    def do_DELETE(self) -> None:
        """Only the API has anything to delete. The console says what it
        means with a form and a confirm page, and the stdlib's own answer
        to an unknown method is a 501 in HTML - which a client parsing
        JSON cannot read (2026-09-05)."""
        return self._other_method()

    #: Nothing here serves these, and the door must still answer in its
    #: own shape: a PUT to /api/v1/health was the one request that came
    #: back as the stdlib's 501 in HTML, past every promise the contract
    #: makes about errors (the audit, 2026-09-12).
    def do_PUT(self) -> None:
        return self._other_method()

    def do_PATCH(self) -> None:
        return self._other_method()

    def do_OPTIONS(self) -> None:
        return self._other_method()

    def _other_method(self) -> None:
        """A method this program does not serve, answered the way the
        part of it that was addressed would answer: JSON under /api/,
        an empty 405 anywhere else."""
        path = self.path.split("?")[0]
        if path.startswith("/api/"):
            return api_v1.dispatch(self, path)
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dismiss_wish(self, user: dict) -> None:
        """Take a failed hand-built request off the dashboard.

        A direct write, like the users page: nothing runs and nothing is
        queued, so there is nothing for the keeper to do. The store says
        who may - whoever asked, or an admin - and a press on somebody
        else's row changes nothing and says so."""
        from ..store import wanted as store_wanted

        ident = self.path[len("/wishes/"):-len("/dismiss")]
        if not ident.isdigit():
            return self._redirect(_said_url("/", "no"))
        try:
            gone = store_wanted.dismiss(
                self.settings, int(ident), user_id=user["id"],
                admin=user.get("role") == "admin")
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not dismiss wish %s (%s)", ident, exc)
            gone = False
        return self._redirect(_said_url("/", "dismissed" if gone else "no"))

    def _redirect(self, where: str) -> None:
        self.send_response(303)
        self.send_header("Location", where)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # Into the real log, at DEBUG: request lines are tracing, and the
        # file handler keeps DEBUG while the console shows INFO.
        log.debug("web: " + fmt, *args)

    def handle_one_request(self) -> None:
        self._began = time.perf_counter()
        self._sent = 0
        self._code = "-"
        super().handle_one_request()
        self._say_request()

    def send_header(self, keyword: str, value: str) -> None:
        # What the answer weighs, for the line `log_request` writes. Not
        # a try/except: there is nothing here worth surviving in silence,
        # and a Content-Length that is not a number is not a thing this
        # server sends.
        if keyword.lower() == "content-length" and str(value).isdigit():
            self._sent = int(value)
        super().send_header(keyword, value)

    def log_request(self, code="-", size="-") -> None:
        """Kept, not written.

        `send_response` calls this before a single header has gone out,
        so at this moment the answer's size is not known - and the line
        said `0 bytes` for every request on the day it was added
        (2026-09-21). `_say_request` writes it when the answer is out.
        """
        self._code = code

    def _say_request(self) -> None:
        """One line per request, at INFO, with what it cost.

        The capture starts at INFO (`logdb`) and request lines went to
        DEBUG, so not one request had ever reached the log table - and
        "the console feels slow" had no number anywhere to check it
        against (2026-09-20). `/live` is left out: it is one connection
        held open for hours, and its line would say nothing true about
        how long anything took.
        """
        path = getattr(self, "path", "") or ""
        if not path or path.split("?")[0] == "/live":
            return
        spent = (time.perf_counter() - getattr(self, "_began", 0.0)) * 1000
        log.info("web %s %s %s %.0fms %s bytes",
                 getattr(self, "command", "?"), path[:120],
                 getattr(self, "_code", "-"), spent,
                 getattr(self, "_sent", 0))


#: Where "Log in selected" may send the person back: the two pages that
#: carry the ticks. Anything else in the form's `back` goes to the front.
LOGIN_BACKS = ("/", "/pools/gpt")

#: Where a gmail button may send a person back to - the view it was
#: pressed on, so the banner lands where the row is.
#: Where a pool button may send a person back to, and what it may carry.
#:
#: Whole-string allowlists were the first shape and they could only hold
#: the addresses somebody had thought to write down. Anything else was
#: silently replaced by the default - so pressing Paid on a list
#: filtered to one seller came back to the unfiltered queued view, and
#: an Edit on page three of a hundred-row list came back to page one
#: (2026-09-20). A rebuild is stricter than a match, not looser: the
#: path must be one of these, and only these parameters survive, each
#: checked for what it is allowed to be.
_BACK_PATHS = {
    "/": (),
    "/pools/gmail": ("view", "seller", "page", "edit"),
    "/pools/proxy": ("view", "q", "page", "ignored"),
    "/pools/gpt": ("view", "q", "page"),
    "/needs": (),
    "/requests": (),
}

#: Which words each `view` may be. A view this page does not have is not
#: an attack, it is a stale bookmark - and either way it is dropped.
_BACK_VIEWS = {
    "/pools/gmail": ("queued", "on_phone", "used", "errored"),
    "/pools/proxy": ("free", "needs_hand", "on_phone", "all", "dead"),
    "/pools/gpt": ("waiting", "on_phone", "delivered", "set_aside"),
}


def _back_to(field: dict, default: str) -> str:
    """The address the form asked to return to, rebuilt from its parts.

    Nothing of the request survives into the answer but a path this
    module names and a handful of parameters it names too, each within
    what it is allowed to be. What cannot be rebuilt is dropped, and
    what is left is the default.
    """
    asked = str(field.get("back") or "").strip()
    if not asked:
        return default
    path, _, query = asked.partition("?")
    if path not in _BACK_PATHS:
        return default
    kept = []
    seen = set()
    for name, values in parse_qs(query, keep_blank_values=False).items():
        if name not in _BACK_PATHS[path] or name in seen or not values:
            continue
        value = values[0].strip()
        if not value or len(value) > 120:
            continue
        if name == "view" and value not in _BACK_VIEWS.get(path, ()):
            continue
        if name in ("page", "edit") and not value.isdigit():
            continue
        if name == "ignored" and value != "1":
            continue
        seen.add(name)
        kept.append((name, value))
    # In the order this module lists them, so the same request always
    # rebuilds to the same address - which is what `_minute_key` and the
    # browser's history both want.
    kept.sort(key=lambda pair: _BACK_PATHS[path].index(pair[0]))
    return path + ("?" + urlencode(kept) if kept else "")


def _gmail_back(field: dict) -> str:
    return _back_to(field, "/pools/gmail")


def _proxy_back(field: dict) -> str:
    return _back_to(field, "/pools/proxy")


def _said_url(back: str, said: str) -> str:
    """The banner appended to wherever the button was pressed - with & when
    that place already carries a view."""
    return f"{back}{'&' if '?' in back else '?'}said={said}"


#: What a spreadsheet reads as the start of a formula. A cell beginning
#: with one is written with a quote in front, so an event's detail or a
#: note that happens to start with `=` opens as text, never as code.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_STARTS) else text


def _delivered_csv(rows: list[dict]) -> str:
    """address, serial, delivered_at, source - the stamp in the owner's
    zone, ISO, so a spreadsheet sorts it."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["address", "serial", "delivered_at", "source"])
    for r in rows:
        # The stamp the hand-over wrote, and the row's last change behind
        # it for everything delivered before that column was written.
        raw = r.get("delivered_at") or r.get("updated_at")
        moment = pages._moment(raw)
        writer.writerow([_csv_cell(v) for v in (
            r.get("address") or "", r.get("serial") or "",
            moment.isoformat(timespec="minutes") if moment else str(raw or ""),
            r.get("source") or "")])
    return out.getvalue()


def _events_csv(rows: list[dict]) -> str:
    """The feed as a spreadsheet reads it: the stamp in the owner's zone,
    ISO, then the columns the page shows."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["at", "kind", "run", "build", "serial", "status",
                     "seconds", "detail"])
    for r in rows:
        moment = pages._moment(r.get("at"))
        writer.writerow([_csv_cell(v) for v in (
            moment.isoformat(timespec="seconds") if moment
            else str(r.get("at") or ""),
            r.get("kind") or "", r.get("run_id") or "",
            r.get("build") or "", r.get("serial") or "",
            r.get("status") or "",
            "" if r.get("seconds") is None else r["seconds"],
            r.get("detail") or "")])
    return out.getvalue()


def _capture_health() -> dict | None:
    """What the log capture in this process says about itself, or None
    when none runs here; never fatal to the page."""
    try:
        from ..store import logdb

        return logdb.health()
    except Exception as exc:                                      # noqa: BLE001
        log.debug("the capture's health did not read (%s)", exc)
        return None


def _phone_back(field: dict, serial: str) -> str:
    """Where a phone button returns to: its story or its Live tab when
    the form said so, the dashboard otherwise - never an address the
    form made up."""
    back = str(field.get("back") or "")
    if back in (f"/phones/{serial}", f"/phones/{serial}/live"):
        return back
    return "/"


def _store_down(exc: BaseException) -> bool:
    """psycopg's OperationalError, without importing psycopg here: a
    connection refused or timed out is a page, not a traceback."""
    return type(exc).__name__ == "OperationalError" or any(
        type(c).__name__ == "OperationalError"
        for c in (exc.__cause__, exc.__context__) if c is not None)


def _ticks(field: dict) -> dict:
    """The permission checkboxes as they came off the form: present and
    "1" means ticked, absent means not - HTML sends nothing for a clear
    box, which is why every column is read rather than only the sent."""
    from ..store.users import PERMISSION_COLUMNS

    return {c: field.get(c) == "1" for c in PERMISSION_COLUMNS}


def _proxy_key(triple: dict) -> str:
    """host:port:username - the spelling verbs.ignore_proxy writes into
    service_state, so the page's filter and the verb's list agree."""
    return ":".join(str(triple.get(k) or "") for k in ("host", "port",
                                                       "username"))


def _one_proxy_line(field: dict) -> str:
    """The one-by-one boxes as the line a vendor would have pasted:
    `name<TAB>host:port:user:pass`, the user and password only when
    given. Empty when no host was typed, so a blank form previews as
    nothing rather than as a row with an error."""
    host = (field.get("host") or "").strip()
    if not host:
        return ""
    raw = ":".join(p for p in (host, (field.get("port") or "").strip(),
                               (field.get("username") or "").strip(),
                               (field.get("password") or "").strip()) if p)
    name = (field.get("name") or "").strip()
    return f"{name}\t{raw}" if name else raw


def _page_number(first: dict) -> int:
    """`?page=` as a number from 1; anything else is the first page."""
    try:
        return max(1, int(first.get("page", "1")))
    except ValueError:
        log.debug("page %r is not a number; showing the first",
                  first.get("page"))
        return 1


def _q(text: str) -> str:
    from urllib.parse import quote

    return quote(text, safe="")


def _drop_sessions_of(settings, user_id: int, *, keep: str = "") -> int:
    """End every seat this person holds, except the one token given (an
    admin editing themselves keeps their own chair).

    The rights half of this is now handled by reading the user row fresh
    on every request. What is left is the half that was always about
    seats rather than staleness: a password reset must put every other
    browser out, and a deactivated account must stop being able to click.
    Never fatal - a reset that could not reach the store is still a reset,
    and saying so beats failing the page.
    """
    from ..store import sessions as store_sessions

    try:
        return store_sessions.end_all_of(settings, user_id, keep=keep)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not end the other sessions of user %s (%s)",
                    user_id, exc)
        return 0


def _locked_out(username: str) -> bool:
    now = time.time()
    with _lock:
        recent = [t for t in _failures.get(username, ())
                  if now - t < LOCKOUT_SECONDS]
        _failures[username] = recent
        return len(recent) >= LOCKOUT_AFTER


def _note_failure(username: str) -> None:
    with _lock:
        _failures.setdefault(username, []).append(time.time())


def _explain(status: str) -> tuple[str, str]:
    """What a set-aside status means and what to do about it - the two
    sentences failures.verdict holds - or two empty strings for a word
    it never heard of, which renders as the row's own note."""
    from ..failures import knows, verdict

    if not knows(status):
        return "", ""
    found = verdict(status)
    return found.seen, found.advice


#: Where an add flow may return to. Two places, both real pages: the tab
#: it belongs to, and the dashboard - which is where an operator started,
#: because the tab is not theirs any more.
#:
#: A whitelist rather than the field: `back` rides through a preview and a
#: confirm in a hidden input, and an open redirect is what that shape is
#: for if nobody checks it.


def _add_back(field, default: str) -> str:
    return _back_to(field, default)


#: The pages an operator has, whatever they type in the bar.
#:
#: Hiding the rail is not access: a link that is not drawn is still a URL,
#: and an operator who once had these pages has them bookmarked. So the
#: rail and this list are the same decision written twice, and this is the
#: half that holds.
#:
#: The dashboard, the password page, the stream the dashboard listens on,
#: and one phone - its story, the tab Boot opens, and the screens in that
#: story. Clicking a serial is the one place the design sends an operator
#: off the dashboard.
_OPERATOR_PAGES = ("/", "/password", "/live")


#: Told apart from "the session is None", which is a cached answer too.
_UNREAD = object()


class _RailCounts(dict):
    """The rail's badge counts, read the first time a page reads them.

    `nav_counts` is nine subqueries and every request paid for it,
    including the presses that answer 303 and draw no rail at all
    (2026-09-21). A dict, because every reader already treats it as one
    and none of them should have to know.
    """

    def __init__(self, load):
        super().__init__()
        self._load = load
        self._read = False

    def _fill(self):
        if self._read:
            return
        self._read = True
        try:
            super().update(self._load())
        except Exception as exc:                                  # noqa: BLE001
            log.warning("the rail's counts did not load (%s)", exc)

    def __getitem__(self, key):
        self._fill()
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._fill()
        return super().get(key, default)

    def __contains__(self, key):
        self._fill()
        return super().__contains__(key)

    def __len__(self):
        self._fill()
        return super().__len__()

    def __iter__(self):
        self._fill()
        return super().__iter__()

    def items(self):
        self._fill()
        return super().items()

    def keys(self):
        self._fill()
        return super().keys()

    def values(self):
        self._fill()
        return super().values()


def _operator_may_get(path: str) -> bool:
    if path in _OPERATOR_PAGES:
        return True
    # The manager's drawer, which is part of the dashboard and always
    # has been - it just stopped riding inside the response on
    # 2026-09-21. An operator has never had the pool PAGES and has
    # always had the manager, and what they may DO in it is still each
    # button's own permission. Without this the drawer answers a
    # redirect to "/" and the sheet never opens for them.
    if path.startswith("/pools/") and path.endswith("/sheet"):
        return True
    return path.startswith("/phones/") and path != "/phones"


#: What an operator may post, which is not the same list.
#:
#: The pages went and the doors did not: adding stock is on the dashboard
#: now, and it posts to the same endpoints the Gmail and Gpt tabs always
#: did - a paste, a preview, a confirm. The permission on each still
#: decides, so this list says which doors exist for an operator, not who
#: may walk through them.
#:
#: Written as its own list rather than derived from the GET one, because
#: the two answer different questions and a POST that slips through is a
#: row written, not a page seen.
#:
#: Editing and removing a Gmail row are here now, because the manager the
#: dashboard opens is where a person works on that pool - the operator
#: asked for exactly that. They are still gated by `may_add_gmail`, which
#: is the permission that already decided who may put rows in; being able
#: to add a row and not fix a typo in it was the odd half.
_OPERATOR_POSTS = (
    "/logout", "/password", "/accounts/login", "/accounts/spotify/build",
    "/phones/build",
    "/pools/gmail/preview", "/pools/gmail/add",
    "/pools/gmail/edit", "/pools/gmail/remove", "/pools/gmail/undo",
    "/pools/gmail/free", "/pools/gmail/refund",
    "/pools/proxy/preview", "/pools/proxy/add", "/pools/proxy/free",
    "/pools/proxy/test", "/pools/proxy/remove", "/pools/proxy/test-all",
    "/pools/proxy/free-all",
    "/pools/gpt/preview", "/pools/gpt/add",
    "/pools/gpt/edit", "/pools/gpt/remove", "/pools/gpt/undo",
    "/pools/gpt/free",
    # The Spotify accounts are the same pool in the same table, kept by
    # whoever keeps the GPT ones (2026-09-17).
    "/pools/spotify/preview", "/pools/spotify/add",
    "/pools/spotify/edit", "/pools/spotify/remove", "/pools/spotify/free",
    "/pools/spotify/undo",
    # The one service control an operator is offered: the breaker means
    # builds keep failing, and fresh stock is the answer to it. The
    # permission on the other side is what decides; this only says the
    # door exists for them (2026-09-07).
    "/service/clear_breaker",
)


def _operator_may_post(path: str) -> bool:
    if path in _OPERATOR_POSTS:
        return True
    # One phone: boot it, take it, hand it back, change its exit.
    if path.startswith("/phones/") and path.rsplit("/", 1)[-1] in (
            "boot", "state", "proxy", "stop", "watching", "closing"):
        return True
    # One failed hand-built request: take it off the list.
    return path.startswith("/wishes/") and path.endswith("/dismiss")


def _advice(status: str) -> str:
    """One line of meaning for a flagged row's status token.

    failures.py is the one import allowed past the mirror rule: it is pure
    - zero package imports, no I/O - and it IS the meaning of these words.
    A word it does not know renders as nothing rather than a crash: rows
    written before a rename are data, not errors.
    """
    from ..failures import VERDICTS, verdict

    if status not in VERDICTS:
        return ""
    return verdict(status).seen
