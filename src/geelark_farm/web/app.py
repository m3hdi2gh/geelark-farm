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
import hashlib
import hmac
import io
import json
import logging
import re
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
                if self._station_asked():
                    return self._station_error("signed-out")
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
                if self._station_asked():
                    return self._station_error("password")
                return self._redirect("/password")
            if path == "/password":
                return self._html(200, pages.password_page(user))
            if user.get("role") != "admin" and not (
                    _operator_may_get(path) or self._station_opened(path)):
                # Straight back to the one page they have. This used to be
                # a page of its own saying whose the page was, and every
                # link that landed an operator there - an alert, an old
                # bookmark - was one more page between them and the work
                # (the operator, 2026-09-05: "this page is superfluous").
                if self._station_asked():
                    return self._station_error("refused")
                return self._redirect("/")
            if path == "/live":
                return self._live(user)
            if path == "/users":
                return self._users_get(user)
            if path == "/api-clients":
                return self._api_clients_get(user)
            # The operator Station (2026-09-29): its page, its state, and
            # one phone's Live tab. An admin's during the trial; with
            # STATION_FOR_OPERATORS on, an operator's `/` is the Station.
            if path == "/station" or path.startswith("/station/"):
                return self._station_get(user, path)
            if path == "/" and user.get("role") != "admin" \
                    and self.settings.station_for_operators:
                return self._station_get(user, "/station")
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
                    progress=self._progress_of(rows),
                    stops_asked=self._stops_asked()))
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
            if path.startswith("/pools/") and path.endswith("/credentials"):
                return self._credentials(user, path)
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
            # The Gmails page (2026-10-07): the prototype the user approved,
            # its own document in the admin rail, in the Gmail Pool's
            # place. Its state, one Gmail's details for an admin's drawer,
            # and the archive are JSON for its script.
            if path == "/pools/gmail/state":
                return self._gmails_state(first)
            if path == "/pools/gmail/secret":
                return self._gmails_secret(user, first)
            if path == "/pools/gmail/archive":
                from . import gmails_read

                return self._json(200, dict(gmails_read.archive(
                    self.settings, q=first.get("q", "")), ok=True))
            if path == "/pools/gmail":
                from ..store import gmail_desk
                from . import gmail_pages, gmails_read

                gmail_desk.scrub_old(self.settings)
                return self._html(200, gmail_pages.gmails_page(
                    gmails_read.state(self.settings, fresh=True), user))
            # The Proxies page (2026-10-02): the prototype the user called
            # final, its own document in the admin rail - it took the place
            # of the Proxy Pool's four lists. Its state and its archive are
            # JSON for its script.
            if path == "/pools/proxy/state":
                return self._proxies_state(first)
            if path == "/pools/proxy/archive":
                from . import proxies_read

                return self._json(200, {"ok": True, "rows": proxies_read.archive(
                    self.settings)})
            if path == "/pools/proxy":
                from . import proxies_read, proxy_pages

                return self._html(200, proxy_pages.proxies_page(
                    proxies_read.state(self.settings, fresh=True), user))
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
            # ------------------------------------------- tasks (2026-09-26)
            # Read-only: what the jobs that are not builds have been
            # doing. The pages and their reader live in their own two
            # modules, so this is the whole of the farm's router that
            # knows about them.
            if path == "/tasks" or path.startswith("/tasks/"):
                from . import task_pages, task_read

                if not task_pages.may_see(user):
                    return self._html(403, pages.forbidden(user))
                rest = path[len("/tasks"):].strip("/")
                if not rest:
                    return self._html(200, task_pages.tasks_page(
                        task_read.listing(self.settings), user,
                        said=first.get("said", "")))
                name, _, run = rest.partition("/")
                asked, _, req = run.partition("/")
                if asked == "req" and req.isdigit():
                    # Where Run lands: the request, until a builder has
                    # opened the run, and the run itself after - one
                    # address that follows the press all the way.
                    got = task_read.request(self.settings, name, int(req))
                    if got is None:
                        return self._html(404, pages.page(
                            "404", "<h2>No such request</h2>", user=user))
                    watch = task_pages.may_run(user, self.settings)
                    if got["run"] is not None:
                        return self._html(200, task_pages.run_page(
                            got["run"], user, advice=_verdict, watch=watch))
                    return self._html(200, task_pages.waiting_page(
                        got["action"], name, user))
                said = first.get("said", "")
                word, _, req = said.partition(":")
                if word in ("queued", "already", "twice") and req.isdigit() \
                        and not run:
                    # The Run form is sent by the browser itself, so this
                    # redirect is followed and the address bar says where
                    # the run is - the page's live stream then reloads the
                    # run, not the task (2026-09-28).
                    return self._redirect(f"/tasks/{name}/req/{req}")
                if run.isdigit():
                    row = task_read.one(self.settings, int(run))
                    if row is None or str(row.get("task")) != name:
                        return self._html(404, pages.page(
                            "404", "<h2>No such run</h2>", user=user))
                    return self._html(200, task_pages.run_page(
                        row, user, advice=_verdict,
                        watch=task_pages.may_run(user, self.settings)))
                runner = task_pages.may_run(user, self.settings)
                return self._html(200, task_pages.task_page(
                    task_read.runs(self.settings, name,
                                   page=_page_number(first)),
                    user, said=said, said_note=self._said_note(said),
                    phones=task_read.phones(self.settings) if runner else (),
                    serial=first.get("serial", ""), may_run=runner))
            if path == "/stock":
                # The stock planner's page (2026-09-30): an admin's.
                if user.get("role") != "admin":
                    return self._html(403, pages.forbidden(user))
                from . import stock_pages, stock_read

                query = parse_qs(self.path.partition("?")[2])
                said = (query.get("said") or [""])[0]
                return self._html(200, stock_pages.stock_page(
                    stock_read.state(self.settings), user, said=said))
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
            if path.startswith("/phones/") and path.endswith("/watch"):
                # The dashboard's Watch live on a phone being built: the
                # screen the builder logged, framed under our own bar
                # (pages.viewer_page, watch=True). Nothing to hold and
                # nothing to do - the build owns the phone.
                serial = path[len("/phones/"):-len("/watch")]
                # The link is the phone's interactive viewer, and it
                # outlives the build on a phone nobody has restarted
                # since: somebody else's hold - a Station one included -
                # is not watched from here (2026-09-29, beside web-2).
                if self._held_by_somebody_else(user, serial):
                    log.info("watch %s: held by somebody else - not framed "
                             "for %s", serial, user.get("username"))
                    return self._html(200, pages.live_page(
                        serial, user, said="refused"))
                url = read.live_link(self.settings, serial)
                if not url:
                    return self._html(200, pages.live_page(
                        serial, user, said="", row={
                            "status": "failed",
                            "result": "no live screen for this phone right "
                                      "now - it is not being built, or its "
                                      "link is gone"}))
                return self._html(200, pages.viewer_page(
                    serial, user, url, watch=True))
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
                # Only the person who asked, or an admin: the row's
                # detail carries the phone's interactive viewer, and ids
                # are sequential - anybody could walk them (2026-09-29).
                # Said "not allowed", not left waiting: with no row the
                # tab read "Starting" and reloaded itself for ever.
                if row is not None and user.get("role") != "admin" and (
                        str(row.get("requested_by")) != str(user.get("id"))):
                    log.info("boot tab: request %s is not %s's - not framed",
                             req, user.get("username"))
                    row, said = None, "refused"
                # Once the link is there the page frames GeeLark's viewer
                # rather than sending the tab to it: the tab's closing is
                # the phone's off switch (pages.viewer_page, 2026-09-16).
                creds = account = None
                if row and row.get("status") == "done":
                    creds = self._gmail_for_the_holder(user, serial)
                    account = self._account_for_the_holder(user, serial)
                return self._html(200, pages.live_page(
                    serial, user, said=said, row=row, creds=creds,
                    account=account,
                    lane=read.lane_of(self.settings, serial)))
            if path.startswith("/phones/") and "/screens/" in path:
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                return self._screen(user, path)
            if path.startswith("/phones/") and ("/wire/" in path
                                                or "/shot/" in path):
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                return self._picture(user, path)
            if path.startswith("/phones/"):
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                serial = path[len("/phones/"):].strip("/")
                story = read.phone_story(self.settings, serial) \
                    if serial.isdigit() else None
                if story is None:
                    return self._html(404, pages.page(
                        "404", "<h2>No such phone</h2>", user=user))
                runs = [_run_words(run) for run in
                        read.phone_journey(self.settings, serial)]
                return self._html(200, pages.phone_story_page(
                    story, user, explain=_explain, journey=runs,
                    said=(parse_qs(self.path.partition("?")[2])
                          .get("said") or [""])[0]))
            self._html(404, pages.page("404", "<h2>Nothing here</h2>",
                                       user=user))
        except Exception as exc:                                  # noqa: BLE001
            # A handler that leaks a traceback leaks whatever was in it.
            if _store_down(exc):
                log.warning("web: %s - the store is not answering (%s)",
                            self.path, exc)
                if self._station_asked():
                    return self._station_error("down")
                return self._html(503, pages.store_down_page())
            log.exception("web: %s failed", self.path)
            if self._station_asked():
                return self._station_error("broke")
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
                if self._station_asked():
                    return self._station_error("signed-out")
                return self._redirect("/login")
            # CSRF, before any dispatch. The comparison runs constant-time
            # for the same reason password checks do, and an Origin header
            # that is present and foreign is refused as a second layer.
            if not hmac.compare_digest(field.get("csrf", ""),
                                       entry.get("csrf", "")):
                if self._station_asked():
                    return self._station_error("stale")
                return self._html(403, pages.page(
                    "403", "<h2>Stale session - reopen the page"
                           "</h2>", user=entry["user"]))
            origin = self.headers.get("Origin")
            host = self.headers.get("Host") or ""
            if origin and host and not origin.endswith("//" + host):
                if self._station_asked():
                    return self._station_error("origin")
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
                if self._station_asked():
                    return self._station_error("password")
                return self._redirect("/password")
            if (user.get("role") != "admin"
                    and not (_operator_may_post(self.path)
                             or (self.settings.station_for_operators
                                 and self.path.startswith("/station/")))):
                # A refusal, not a redirect: a form that quietly does
                # nothing is how somebody comes to believe they pressed it.
                if self._station_asked():
                    return self._station_error("refused")
                return self._html(403, pages.page(
                    "Not yours to do",
                    '<div class="narrow"><h2>That belongs to an admin</h2>'
                    '<p class="dim">Nothing was changed. '
                    '<a href="/">Back to the dashboard</a>.</p></div>',
                    user=user))
            # The Station's own doors, each answering JSON to the page and
            # a redirect to a plain form (2026-09-29).
            if self.path.startswith("/station/"):
                return self._station_post(user, field)
            if self.path == "/stock":
                return self._stock_post(user, field)
            if self.path.startswith("/pools/"):
                return self._pool_post(user, field)
            if self.path.startswith("/tasks/") and \
                    self.path.endswith("/run"):
                return self._task_post(user, field)
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
                    from ..verbs import SPOTIFY_ERROR_ON_A_BUILD
                    return self._refuse(
                        user, "build_by_hand",
                        {"gmail": "" if no_gmail else gmail,
                         "account_kind": kind},
                        SPOTIFY_ERROR_ON_A_BUILD
                        if kind == "spotify:error" else
                        "a phone with no Google account can only carry a "
                        "normal Spotify account"
                        if no_gmail else
                        "a normal Spotify account wants a phone with no "
                        "Google account on it")
                which, _, category = kind.partition(":")
                account = (field.get("app_account") or "").strip()
                if not which:
                    account = ""
                # The exit box (back 2026-09-28): a pool row's name when
                # picked, a proxy string when typed. A name never holds a
                # colon and a proxy string always does, which is a surer
                # tell than the pool's host:port list for a value that is
                # either a name or a whole URL.
                exit_ = (field.get("proxy_name") or "").strip()
                payload = {
                    "gmail": gmail,
                    "no_gmail": no_gmail,
                    "gmail_typed": bool(gmail) and self._is_new("gmail", gmail),
                    "gmail_password": field.get("gmail_password") or "",
                    "gmail_secret": field.get("gmail_secret") or "",
                    "proxy_name": exit_,
                    "proxy_typed": ":" in exit_,
                    # Which lane, for a phone no account decides for.
                    "purpose": (field.get("purpose") or "").strip().lower(),
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
                    "purpose": "spotify",
                    "app_account": address, "app_typed": False,
                    "app_password": "", "app_secret": "",
                }
                return self._act(
                    user, "may_login_accounts", "build_by_hand", payload,
                    idem=self._minute_key(user, "byhand", f"spotify:{address}"),
                    back="/", said_word="asked")
            if self.path == "/accounts/login":
                return self._login_accounts(
                    user, form.get("addresses") or [],
                    back=_login_back(field),
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
                from ..store import station as store_station

                serial = self.path[len("/phones/"):-len("/watching")]
                try:
                    # The Station's tab beats its holder's hold only; the
                    # old tab's beat never keeps somebody's Station hold
                    # alive (rev 42).
                    if str(field.get("station") or "") == "1":
                        still = store_station.watch(
                            self.settings, serial, user["id"],
                            self.settings.live_tab_grace_seconds)
                    else:
                        still = person.watch(self.settings, serial, user["id"])
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
                from ..store import station as store_station

                serial = self.path[len("/phones/"):-len("/closing")]
                try:
                    if str(field.get("station") or "") == "1":
                        store_station.tab_closed(self.settings, serial,
                                                 user["id"])
                    else:
                        person.tab_closed(self.settings, serial, user["id"])
                except Exception as exc:                          # noqa: BLE001
                    log.debug("closing of %s not written (%s)", serial, exc)
                # And the sweep is asked for the moment the twenty seconds
                # are up, rather than left to the next pass - which could
                # be minutes away with a build in it (the operator,
                # 2026-09-29). A reload's beat clears the stamp first, and
                # the sweep then finds nothing to do.
                self._sweep_soon(user, serial)
                return self._text(200, "noted")
            if self.path.startswith("/phones/") and \
                    self.path.endswith("/boot"):
                # One press: start the phone in GeeLark, take it, and hand
                # the live-view link to the tab that is waiting for it.
                serial = self.path[len("/phones/"):-len("/boot")]
                if str(field.get("station") or "") == "1":
                    return self._station_boot(user, serial)
                # Boot takes the phone: refused on anybody else's, an
                # admin's included - ending a hold is theirs (2026-09-15),
                # taking it over is not.
                held = self._holder_of(user, serial)[0]
                if held and held != user.get("username"):
                    # Boot's form opens its Live tab, so that is where
                    # the answer is read.
                    return self._refuse(
                        user, "boot_phone", {"serial": serial},
                        f"phone {serial} is with {held}",
                        back=f"/phones/{serial}/live")
                # One power press at a time on a phone, from every door
                # (rev 42): a Boot waits for a Change IP or a power-off.
                busy = self._power_busy(serial, ("change_proxy",
                                                 "power_off_phone"))
                if busy:
                    return self._refuse(user, "boot_phone", {"serial": serial},
                                        busy, back=f"/phones/{serial}/live")
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
                if str(field.get("station") or "") == "1":
                    return self._station_proxy(user, serial, field)
                held = self._held_by_somebody_else(user, serial)
                if held:
                    return self._refuse(
                        user, "change_proxy", {"serial": serial},
                        f"phone {serial} is with {held}",
                        back=_phone_back(field, serial))
                busy = self._power_busy(serial, ("boot_phone",
                                                 "power_off_phone"))
                if busy:
                    return self._refuse(user, "change_proxy",
                                        {"serial": serial}, busy,
                                        back=_phone_back(field, serial))
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
                if self._station_asked():
                    return self._station_error("down")
                # The retry form echoes every field it was sent, so a
                # door whose form carries a password or a typed secret
                # gets the page without it (pages.store_down_page drops
                # such fields as well).
                bare = self.path.split("?")[0]
                echo = not (bare == "/password" or bare == "/phones/build"
                            or bare.startswith("/station/"))
                return self._html(503, pages.store_down_page(
                    retry=(self.path, form) if echo else None))
            log.exception("web: POST %s failed", self.path)
            if self._station_asked():
                return self._station_error("broke")
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
        try:
            got = store_actions.retry(self.settings, action_id=action_id,
                                      user_id=user["id"],
                                      is_admin=user["role"] == "admin")
        except Exception as exc:                                  # noqa: BLE001
            # One pending power press per phone (rev 42): a failed Boot
            # retried while another Boot or Change IP is pending on that
            # phone is refused by the index, not a 500. A power-off is
            # not in the index - a give-back always gets its own.
            if type(exc).__name__ != "UniqueViolation":
                raise
            log.info("web: retry of request %s refused - a power press is "
                     "already pending on that phone", action_id)
            got = "already"
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
    #: The view the row was drawn under, when the press came from a
    #: dedicated pool page rather than the dashboard's sheet: the
    #: answer is then that page's own row (`pages.page_row_answer`).
    VIEW_ASKED = "X-GF-View"

    def _row_answer(self, kind: str, address: str, said: str,
                    user: dict, back: str = "/") -> bool:
        """Answer a one-row press with that row and the banner.

        False when this request did not ask for it, in which case the
        caller redirects as it always has.
        """
        if self.headers.get(self.ROW_ASKED) != kind or not address:
            return False
        view = (self.headers.get(self.VIEW_ASKED) or "").strip()
        try:
            if view:
                return self._page_row_answer(kind, view, address, said,
                                             user, back)
            found = read.pool_row(self.settings, kind, address)
        except Exception as exc:                              # noqa: BLE001
            # The work is done; only drawing the answer failed. The
            # redirect still tells the page where to look.
            log.warning("could not read %s back after the press (%s)",
                        address, exc)
            return False
        self._html(200, pages.row_answer(
            kind, found.get("row"), said, user, self._said_note(said),
            manual_login=self.settings.manual_login,
            pending=found.get("pending") or {}))
        return True

    def _page_row_answer(self, kind: str, view: str, address: str,
                         said: str, user: dict, back: str) -> bool:
        """The one-row answer for a dedicated pool page: the row as that
        page's view draws it, under fresh pills. `back` is where the
        press came from, rebuilt - its seller and search are what the
        view's list was cut by."""
        if kind not in pages.PAGE_ROW_KINDS:
            return False
        asked = {k: v[0] for k, v in
                 parse_qs(back.partition("?")[2]).items()}
        strays = 0
        tests: dict = {}
        if kind == "proxy":
            unlisted, _, tests = self._proxy_state()
            strays = len(unlisted)
        found = read.page_row(self.settings, kind, view, address,
                              seller=asked.get("seller", ""),
                              q=asked.get("q", ""), strays=strays)
        self._html(200, pages.page_row_answer(
            kind, found["view"], found.get("row"), said, user, back,
            found.get("counts") or {}, said_note=self._said_note(said),
            advice=_advice, explain=_explain, tests=tests,
            manual_login=self.settings.manual_login))
        return True

    def _act(self, user: dict, permission: str, verb: str, payload: dict,
             *, idem: str, back: str, said_word: str = "done",
             row_of: str = "", digest_skip: tuple = (),
             same_button: str = "") -> None:
        """Queue one command, or record that it was refused.

        The person's name rides in the payload so the pass can write it
        into the sheet's notes. A refusal is a row too - `refused`, with
        the permission named - so the Requests page says what was asked
        and why nothing happened, instead of a 403 nobody remembers.
        `permission` is one of the users' ticks, or "admin" for the
        service controls, which no tick grants.

        `digest_skip` names payload keys the idem key leaves out (the
        Station build's typed secrets: a retried press is one request,
        and no hash of a password is kept). `same_button` is a Station
        verdict's key: a pending twin pressed with another key is said
        to be so, rather than answered "already"."""
        if not self.settings.web_mutations:
            if self._station_asked():
                return self._station_error("off")
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
            return self._go(_said_url(back, "refused"))
        # The same button pressed twice for the same thing is one
        # request, not two the pass would refuse a minute apart.
        needle = str(payload.get("serial") or payload.get("name")
                     or payload.get("address") or "")
        try:
            if needle:
                twin = store_actions.pending_for(self.settings, verb=verb,
                                                 needle=needle)
            elif verb in _SWEEPS:
                # Test all and Free all name nothing, so the guard above
                # never found their twin: a second press was a second
                # ~27s sweep of GeeLark (2026-09-21, found by audit).
                twin = store_actions.pending_any(self.settings, verb=verb)
            else:
                twin = None
        except Exception as exc:                                  # noqa: BLE001
            log.debug("pending check skipped (%s)", exc)
            twin = None
        if twin is not None:
            other = self._other_button(twin, same_button, payload)
            if other:
                return self._go(_said_url(back, "no"), extra={"note": other})
            return self._go(_said_url(back, f"already:{twin}"))
        # One pending power press per phone (rev 42's unique index): a
        # second one is a unique violation, turned into words here - the
        # same press is "already", another is refused in its words, and an
        # orphan a restart left behind is closed so this one goes through.
        req = None
        for _attempt in range(2):
            try:
                req = store_actions.enqueue(
                    self.settings, verb=verb, payload=payload,
                    requested_by=user["id"],
                    idem_key=f"{idem}:{_digest(payload, digest_skip)}")
                break
            except Exception as exc:                              # noqa: BLE001
                if _attempt or not self._power_clash(verb, exc):
                    raise
                log.info("%s on %s: another power press is pending (%s)",
                         verb, payload.get("serial"), exc)
                # Only Boot and Change IP are in the index (a power-off
                # never clashes), so only they can be what refused it.
                clash = self._power_pending(str(payload.get("serial") or ""),
                                            _INDEXED_POWER)
                if clash is None:
                    continue
                if clash["verb"] == verb:
                    return self._go(_said_url(back, f"already:{clash['id']}"))
                return self._refuse(user, verb, payload, clash["words"],
                                    back=back)
        # The same drawing of the button, sent again: one row, and it was
        # carried out the first time. Said so, rather than "Queued" over a
        # row that is already finished (2026-09-14). `enqueued` has known
        # this all along - it is which branch it took - and the answer
        # used to be re-derived by comparing the database host's clock to
        # this container's with a one-second tolerance (2026-09-21).
        if not getattr(req, "fresh", True):
            other = self._other_button(int(req), same_button, payload)
            if other:
                return self._go(_said_url(back, "no"), extra={"note": other})
            return self._go(_said_url(back, f"twice:{req}"))
        # `said_word` is what the press was FOR, not what it did. The work
        # runs here now, so its own verdict is known before the redirect -
        # and it was thrown away: a refusal, a failure and a success all
        # left as one green tick reading "Done". The dashboard's own Build
        # button was refused every time somebody left Gmail on "auto", and
        # said "Done - it is already in" (the operator, 2026-09-07).
        ran = self._ran_it_now(verb, payload, req)
        if ran is not None and verb == "build_by_hand" and payload.get("station"):
            # A Station build keeps no typed secret on its request once it
            # has run (the state read scrubs one the lane runs later).
            from ..store import station as store_station

            store_station.scrub(self.settings, int(req))
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
        if row_of and self._row_answer(
                row_of, str(payload.get("address") or payload.get("name")
                            or ""), said, user, back):
            return None
        self._go(_said_url(back, said))

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

    def _task_post(self, user: dict, field: dict) -> None:
        """Run: one task on one phone, as a request like any other button's.

        Only the fields the task declares are passed on, and never one it
        marks secret - the verb refuses such a task anyway, and a secret
        must not reach the actions table even on its way to a refusal.
        Admin only: the gate above already sends an operator away, and
        `_act` asks again (2026-09-27, the agreed rule for phase 4).
        """
        from .. import tasks as registry

        name = self.path[len("/tasks/"):-len("/run")]
        spec = registry.spec(name)
        if spec is None or spec.key != name:
            return self._html(404, pages.page(
                "404", "<h2>No such task</h2>", user=user))
        inputs = {f.name: str(field.get(f"in_{f.name}") or "").strip()
                  for f in spec.inputs if not f.secret}
        return self._act(
            user, "admin", "run_task",
            {"task": spec.key,
             "serial": str(field.get("serial") or "").strip(),
             "inputs": {k: v for k, v in inputs.items() if v}},
            idem=self._minute_key(user, "task",
                                  f"{spec.key}:{field.get('serial', '')}"),
            back=f"/tasks/{spec.key}")

    def _said_note(self, said: str, user: dict | None = None) -> str:
        """The verb's own sentence for a press that did not go through.

        It is settled on the request's row a moment before the redirect -
        "the Gmail x@y is not free", "16 gmails added, 1 already in the
        pool, 1 refused" - and only that sentence can name the address and
        say why. Read here rather than carried in the address bar, because
        an address in a query string is the one thing that must not be
        (2026-09-07).

        With a `user`, only their own request's sentence is read (or any,
        for an admin): `?said=no:<id>` on a Live tab is a door anybody
        can type, and a sentence can name another person's phone.
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
        if user is not None and row is not None and user.get("role") != "admin" \
                and row.get("requested_by") != user.get("id"):
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
        # The add makes a free row, and a delivered account is not stock:
        # undone, it would go to the next phone. The archive keeps it
        # (2026-09-27).
        if str(kept.get("Status") or "").strip().lower() == "delivered":
            return self._redirect("/?said=kept_delivered")
        secret = str(kept.get("Secret") or kept.get("2FA Secret") or "").strip()
        if kind == "gmail" and str(kept.get("Id") or "").isdecimal():
            # The archive has the whole row: it comes back as it was, both
            # of its factors with it (2026-10-07).
            return self._act(user, permission, "restore_gmail",
                             {"id": int(kept["Id"])}, idem=f"undo-{req}",
                             back="/")
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

    def _remove_delivered(self, user: dict, kind: str, field: dict) -> None:
        """"Remove all delivered" on an account pool's spent list: asked
        once, then one command for every delivered row of that pool
        (the operator, 2026-09-27). The count on the question is the one
        the button was drawn with; the verb removes what is delivered
        when it runs, and says how many."""
        name = "Spotify" if kind == "spotify" else "GPT"
        back = "/"
        n = str(field.get("n") or "").strip()
        many = f"all {n}" if n.isdigit() else "all the"
        if field.get("sure") != "1":
            return self._html(200, pages.confirm_page(
                user, title=f"Remove {many} delivered {name} accounts?",
                text=("They leave the pool for the archive, which keeps "
                      "each row whole. A delivered account the panel "
                      "handed in stays. Undo does not bring them back as "
                      "stock."),
                action=f"/pools/{kind}/remove-delivered",
                fields={"n": n, "sure": "1", "back": back},
                button=f"Yes, remove {many} delivered", back=back))
        return self._act(user, "may_add_gpt", "remove_delivered_apps",
                         {"pool": kind},
                         idem=self._minute_key(user, "remove_delivered",
                                               kind),
                         back=back)

    def _sweep_soon(self, user: dict, serial: str) -> None:
        """Queue the forgotten-phones sweep once the closing grace is up.
        A timer in this process; lost on a restart, when the pass still
        catches the phone as it always did."""
        from .. import forgotten
        from ..store import actions as store_actions

        settings = self.settings
        who = int(user.get("id") or 0)
        key = f"sweep:{serial}:{int(time.time()) // 60}"

        def ask():
            try:
                store_actions.enqueue(settings, verb="sweep_forgotten",
                                      payload={"serial": serial},
                                      requested_by=who, idem_key=key)
            except Exception as exc:                          # noqa: BLE001
                log.debug("sweep for %s not queued (%s)", serial, exc)

        timer = threading.Timer(forgotten.TAB_CLOSED_SECONDS + 3, ask)
        timer.daemon = True
        timer.start()

    def _remove_gmail_group(self, user: dict, field: dict) -> None:
        """"Remove all" under the Gmail pool's spent or errored chip:
        asked once, then one command for every row under it (the
        operator, 2026-09-28). Any other group goes nowhere."""
        group = str(field.get("group") or "").strip().lower()
        back = "/"
        if group not in ("spent", "errored"):
            return self._redirect("/?said=none")
        n = str(field.get("n") or "").strip()
        many = f"all {n}" if n.isdigit() else "all the"
        if field.get("sure") != "1":
            return self._html(200, pages.confirm_page(
                user, title=f"Remove {many} {group} Gmails?",
                text=("They leave the pool for the archive, which keeps "
                      "each row whole. Free rows, rows set aside by hand "
                      "and rows on a phone are never touched."),
                action="/pools/gmail/remove-group",
                fields={"group": group, "n": n, "sure": "1", "back": back},
                button=f"Yes, remove {many} {group}", back=back))
        return self._act(user, "may_add_gmail", "remove_gmail_group",
                         {"group": group},
                         idem=self._minute_key(user, "remove_gmail_group",
                                               group),
                         back=back)

    def _login_accounts(self, user: dict, addresses: list,
                        back: str = "/", serial: str = "") -> None:
        """"Log in selected" (C6), off the dashboard or the Gpt Pool -
        `back` is whichever the ticks were on. Only meaningful with
        manual login on: off, the pass logs accounts in by itself and
        the button would race it for the same rows."""
        if not self.settings.manual_login:
            return self._redirect(_said_url(back, "auto"))
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

    def _stops_asked(self) -> list[str]:
        """The serials a Cancel has landed on and not yet been honoured
        for - so the Requests page's "Stop this one" is shown pressed,
        as the dashboard's row is. Never fatal: a store that will not
        say is a page with the doors it always had."""
        from ..store import stops as store_stops

        try:
            return sorted(store_stops.asked(self.settings))
        except Exception as exc:                                  # noqa: BLE001
            log.debug("the stop requests could not be read (%s)", exc)
            return []

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

    # ------------------------------------------------------ the Proxies page
    def _proxies_state(self, first: dict) -> None:
        """The Proxies page's state for its next draw, and how each request
        it waits on stands (`?req=1,2`). Unchanged and asked nothing else,
        it is a 304: an open page asks every twenty seconds."""
        from ..store import actions as store_actions
        from . import assets, proxies_read

        wanted = [int(x) for x in str(first.get("req") or "").split(",")
                  if x.strip().isdigit()][:80]
        if not wanted:
            answer = proxies_read.state(self.settings)
            tag = proxies_read.etag({"s": answer, "r": assets.REV})
            if (self.headers.get("If-None-Match") or "") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return None
            return self._json(200, {"ok": True, "state": answer,
                                    "rev": assets.REV}, etag=tag)
        # The requests first, then the pool: a request that ended is drawn
        # with the pool it left, never with one read before it ran.
        reqs, ended = {}, False
        for req in wanted:
            row = store_actions.one(self.settings, req)
            if row is None:
                continue
            # Names and counts only: a request's detail can hold a pasted
            # line, and a pasted line holds a password.
            detail = row["detail"] if isinstance(row["detail"], dict) else {}
            names = {k: [str(n) for n in detail.get(k) or []]
                     for k in ("added", "dead", "freed", "answered", "silent")}
            reqs[str(req)] = {
                "status": str(row["status"] or ""),
                "result": str(row["result"] or ""),
                "added": names["added"], "dead": names["dead"],
                # A request about several proxies says which went well.
                "good": names["freed"] + names["answered"],
                "bad": names["dead"] + names["silent"],
                "skipped": len(detail.get("skipped") or []),
                "refused": len(detail.get("refused") or [])}
            ended = ended or reqs[str(req)]["status"] not in ("queued", "running")
        answer = proxies_read.state(self.settings, fresh=ended)
        return self._json(200, {"ok": True, "state": answer,
                                "rev": assets.REV, "reqs": reqs})

    def _proxies_refusal(self, user: dict) -> str:
        """Why this person's press on the Proxies page cannot go, or ""."""
        from ..store.users import may

        if not self.settings.web_mutations:
            return "Actions are not switched on yet - nothing was changed."
        if not may(user, "may_change_proxy"):
            return "You may not change the proxies - nothing was changed."
        return ""

    def _proxies_do(self, user: dict, field: dict) -> None:
        """One press of the Proxies page on one proxy or many: its switch,
        a lane, a cap, a test or a remove. Each proxy is its own request,
        so Requests says what was asked of which and the queue's guards
        work per proxy; the answer is each one's outcome and the pool as
        it now stands."""
        from . import proxies_read

        refused = self._proxies_refusal(user)
        if refused:
            return self._json(200, {"ok": False, "note": refused})
        what = str(field.get("what") or "")
        if not _PROXY_PRESS.fullmatch(what):
            return self._json(200, {"ok": False,
                                    "note": "That is not a press this page knows."})
        ids = sorted({int(x) for x in str(field.get("ids") or "").split(",")
                      if x.strip().isdigit()})[:200]
        pool = {e["id"]: e for e in
                proxies_read.state(self.settings, fresh=True)["exits"]}
        items, many = [], {}
        for pid in ids:
            e = pool.get(pid)
            if e is None:
                items.append({"id": pid, "said": "gone",
                              "note": "it is no longer in the pool"})
                continue
            verb, payload = _proxy_verb(what, e)
            if verb is None:
                items.append({"id": pid, "name": e["n"], "said": "skip"})
            elif verb in _TESTED_TOGETHER:
                many.setdefault(verb, []).append(e)
            else:
                items.append(dict(self._proxy_press(
                    user, verb, dict(payload, name=e["n"])), id=pid,
                    name=e["n"]))
        # Proxies the cloud must test are tested together - one request,
        # every check at once - so ten silent ones cost the lane half a
        # minute, not five (the audit, 2026-10-02). One alone keeps its own
        # verb and its own sentence.
        for verb, group in many.items():
            if len(group) == 1:
                answer = self._proxy_press(user, verb, {"name": group[0]["n"]})
            else:
                answer = self._proxy_press(user, _TESTED_TOGETHER[verb], {
                    "names": [e["n"] for e in group]})
            items.extend(dict(answer, id=e["id"], name=e["n"]) for e in group)
        return self._json(200, {"ok": True, "items": items,
                                "state": proxies_read.state(self.settings,
                                                            fresh=True)})

    def _proxies_add(self, user: dict, field: dict) -> None:
        """A batch from the Proxies page: the lines its reader understood,
        the seller and type, the lane and the phones a day. The farm tests
        each and names them Seller-Type-DDMon-n when it runs the add."""
        import datetime

        from . import proxies_read

        refused = self._proxies_refusal(user)
        if refused:
            return self._json(200, {"ok": False, "note": refused})
        lines = [line.strip() for line in
                 str(field.get("lines") or "").splitlines() if line.strip()]
        if not lines or len(lines) > 500:
            return self._json(200, {"ok": False, "note": (
                "Nothing to add." if not lines else
                "Add at most 500 proxies at a time.")})
        if not re.sub(r"[^A-Za-z0-9]", "", str(field.get("seller") or "")):
            return self._json(200, {"ok": False,
                                    "note": "Name the seller to name them."})
        now = datetime.datetime.now(proxies_read.TEHRAN)
        lane = str(field.get("lane") or "").strip().lower()
        payload = {"rows": [{"raw": line, "name": ""} for line in lines],
                   "purpose": lane if lane in ("gpt", "spotify") else "",
                   "batch": {"seller": str(field.get("seller") or ""),
                             "type": str(field.get("type") or ""),
                             "tag": now.strftime("%d")
                             + proxies_read.MONTHS[now.month - 1]},
                   "cap": str(field.get("cap") or "0")}
        out = self._proxy_press(user, "add_proxies", payload)
        return self._json(200, dict(out, ok=out["said"] in (
            "queued", "pending", "done")))

    def _proxy_press(self, user: dict, verb: str, payload: dict) -> dict:
        """One request from the Proxies page: written down, and run here
        when it needs no word from the cloud - `_act` without the page it
        lands on. The answer says what became of it (done, failed,
        refused; queued or pending while the lane has it), its request,
        and the verb's own sentence."""
        from ..store import actions as store_actions

        payload = dict(payload, by=user["username"], by_id=user["id"])
        name = str(payload.get("name") or "")
        try:
            twin = (store_actions.pending_for(self.settings, verb=verb,
                                              needle=name) if name else None)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("pending check skipped (%s)", exc)
            twin = None
        if twin is not None:
            return {"said": "pending", "req": int(twin)}
        req = store_actions.enqueue(
            self.settings, verb=verb, payload=payload,
            requested_by=user["id"],
            idem_key=f"{self._minute_key(user, verb, name or '-')}:"
                     f"{_digest(payload)}")
        if getattr(req, "fresh", True):
            ran = self._ran_it_now(verb, payload, req)
            if ran is None:
                signals.ring(signals.queued)
                return {"said": "queued", "req": int(req)}
        row = store_actions.one(self.settings, int(req)) or {}
        word = str(row.get("status") or "")
        return {"said": "pending" if word in ("queued", "running") else word,
                "req": int(req), "note": str(row.get("result") or "")}

    # -------------------------------------------------------- the Gmails page
    def _gmails_state(self, first: dict) -> None:
        """The Gmails page's state for its next draw - a 304 when nothing
        moved, since an open page asks every twenty seconds - and how each
        request it waits on stands (`?req=1,2`)."""
        from ..store import actions as store_actions
        from . import assets, gmails_read

        wanted = [int(x) for x in str(first.get("req") or "").split(",")
                  if x.strip().isdecimal()][:40]
        if not wanted:
            answer = gmails_read.state(self.settings)
            tag = gmails_read.etag({"s": answer, "r": assets.REV})
            if (self.headers.get("If-None-Match") or "") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return None
            return self._json(200, {"ok": True, "state": answer,
                                    "rev": assets.REV}, etag=tag)
        # The requests first, then the pool: a request that ended is drawn
        # with the pool it left, never with one read before it ran.
        reqs, ended = {}, False
        for req in wanted:
            row = store_actions.one(self.settings, req)
            if row is None:
                continue
            reqs[str(req)] = _gmail_outcome(row)
            ended = ended or reqs[str(req)]["status"] not in ("queued", "running")
        answer = gmails_read.state(self.settings, fresh=ended)
        return self._json(200, {"ok": True, "state": answer,
                                "rev": assets.REV, "reqs": reqs})

    def _gmails_refusal(self, user: dict) -> str:
        """Why this person's press on the Gmails page cannot go, or ""."""
        from ..store.users import may

        if not self.settings.web_mutations:
            return "Actions are not switched on yet - nothing was changed."
        if not may(user, "may_add_gmail"):
            return "You may not change the Gmails - nothing was changed."
        return ""

    def _gmails_answer(self, out: dict) -> None:
        """A press's outcome and the pool as it now stands. The press is
        written by now: if the pool cannot be read this moment, the answer
        goes without it and the page's next poll brings it."""
        from ..store import gmail_desk
        from . import gmails_read

        gmails_read.forget()
        ok = out.get("status") in ("done", "queued", "running")
        answer = dict(out, ok=ok)
        try:
            answer["state"] = gmails_read.state(self.settings, fresh=True)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("the Gmails page's state did not read after a press"
                        " (%s); the page's next poll brings it", exc)
        # The typed details of presses whose Undo has passed leave the
        # requests that carried them.
        gmail_desk.scrub_old(self.settings)
        return self._json(200, answer)

    def _gmails_do(self, user: dict, field: dict) -> None:
        """One press of the Gmails page on one Gmail or many: in the queue
        or out of it, marked fixed, kept for a product, removed. One
        request for the whole press, which the farm runs at once; each
        Gmail is moved only if it still stands where the page drew it."""
        refused = self._gmails_refusal(user)
        if refused:
            return self._json(200, {"ok": False, "note": refused})
        what = str(field.get("what") or "")
        verb = _GMAIL_PRESS.get(what) or (
            "gmails_keep_for" if re.fullmatch(r"for:(gpt|spotify)?", what) else "")
        if not verb:
            return self._json(200, {"ok": False,
                                    "note": "That is not a press this page knows."})
        ids = sorted({int(x) for x in str(field.get("ids") or "").split(",")
                      if x.strip().isdecimal()})
        if not ids:
            return self._json(200, {"ok": False, "note": "No Gmail was named."})
        payload = {"ids": ids}
        if verb == "gmails_keep_for":
            payload["lane"] = what[4:]
        return self._gmails_answer(self._gmail_press(user, verb, payload,
                                                     what))

    def _gmails_save(self, user: dict, field: dict) -> None:
        """One Gmail's details from its form - and, asked to, marked fixed
        in the same go. The page sends only what the person changed, each
        with a `has_*` flag - an empty box arrives as no field at all, so
        the flag tells an emptied one from one left alone (None)."""
        refused = self._gmails_refusal(user)
        if refused:
            return self._json(200, {"ok": False, "note": refused})
        row_id = str(field.get("id") or "")
        if not row_id.isdecimal():
            return self._json(200, {"ok": False, "note": "No Gmail was named."})
        def given(name, flag):
            return (str(field.get(name) or "") if field.get(flag) == "1"
                    else None)

        payload = {"id": int(row_id),
                   "password": given("password", "has_password"),
                   "key": given("key", "has_key"),
                   "recovery": given("recovery", "has_rec"),
                   "note": given("note", "has_note"),
                   "fixed": field.get("fixed") == "1"}
        return self._gmails_answer(self._gmail_press(user, "gmail_save",
                                                     payload, row_id))

    def _gmails_add(self, user: dict, field: dict) -> None:
        """A paste from the Gmails page, as its reader understood each line:
        new Gmails into a batch, and refused ones back fixed."""
        refused = self._gmails_refusal(user)
        if refused:
            return self._json(200, {"ok": False, "note": refused})
        try:
            rows = json.loads(field.get("rows") or "[]")
            back = json.loads(field.get("back") or "[]")
        except ValueError:
            return self._json(200, {"ok": False,
                                    "note": "The page sent lines the farm cannot read."})
        keep = ("address", "password", "key", "recovery")

        def clean(items):
            return [{k: str(r.get(k) or "") for k in keep}
                    for r in items if isinstance(r, dict)]

        rows = rows if isinstance(rows, list) else []
        back = back if isinstance(back, list) else []
        if len(rows) + len(back) > 2000:
            return self._json(200, {"ok": False, "note": (
                "Paste at most 2000 Gmails at a time - nothing was added.")})
        rows, back = clean(rows), clean(back)
        if not rows and not back:
            return self._json(200, {"ok": False, "note": "Nothing to add."})
        lane = str(field.get("lane") or "").strip().lower()
        payload = {"rows": rows, "back": back,
                   "seller": str(field.get("seller") or "")[:60],
                   "lane": lane if lane in ("gpt", "spotify") else "",
                   "carry": sorted({int(x) for x in str(field.get("carry") or "")
                                    .split(",") if x.strip().isdecimal()})}
        return self._gmails_answer(self._gmail_press(user, "gmails_add",
                                                     payload, "batch"))

    def _gmails_revert(self, user: dict, field: dict) -> None:
        """The page's Undo: the presses named, taken back where nothing has
        moved the Gmails since."""
        refused = self._gmails_refusal(user)
        if refused:
            return self._json(200, {"ok": False, "note": refused})
        reqs = sorted({int(x) for x in str(field.get("reqs") or "").split(",")
                       if x.strip().isdecimal()})[:20]
        if not reqs:
            return self._json(200, {"ok": False, "note": "Nothing to take back."})
        return self._gmails_answer(self._gmail_press(
            user, "gmails_revert", {"reqs": reqs},
            ",".join(str(r) for r in reqs)))

    def _gmails_secret(self, user: dict, first: dict) -> None:
        """One Gmail's password, key and recovery address, for an admin's
        drawer - never in the page's state, never to an operator."""
        from ..store import gmail_desk

        # Asked by the page's script only (its header), never by a link
        # opened or followed: the answer is a password.
        if user.get("role") != "admin" or self._station_asked() != "page":
            return self._json(403, {"ok": False, "said": "refused"})
        row_id = str(first.get("id") or "")
        got = (gmail_desk.secrets(self.settings, int(row_id))
               if row_id.isdecimal() else None)
        if got is None:
            return self._json(200, {"ok": False,
                                    "note": "That Gmail is no longer in the pool."})
        log.info("%s read the details of %s", user.get("username"), got["address"])
        return self._json(200, dict(got, ok=True))

    def _gmail_press(self, user: dict, verb: str, payload: dict,
                     target: str) -> dict:
        """One request from the Gmails page: written down, and run here -
        every one of its verbs is the store's alone (verbs.runs_inline) -
        then answered with its outcome in the page's terms: never a value
        from the request's detail that a person typed."""
        from ..store import actions as store_actions

        payload = dict(payload, by=user["username"], by_id=user["id"])
        req = store_actions.enqueue(
            self.settings, verb=verb, payload=payload,
            requested_by=user["id"],
            idem_key=f"{self._minute_key(user, verb, target or '-')}:"
                     f"{_digest(payload)}")
        if getattr(req, "fresh", True):
            ran = self._ran_it_now(verb, payload, req)
            if ran is None:
                signals.ring(signals.queued)
                return {"status": "queued", "req": int(req), "said": ""}
        row = store_actions.one(self.settings, int(req)) or {}
        return dict(_gmail_outcome(row), req=int(req))

    def _stock_post(self, user: dict, field: dict) -> None:
        """The stock planner's settings, from its page (an admin's). Saved
        with an actions row that says who changed what."""
        if user.get("role") != "admin":
            return self._html(403, pages.forbidden(user))
        from ..store import stockplan as store_stockplan

        lanes = store_stockplan.LANES
        typed = {"mode": field.get("mode") or "watch",
                 "risk_pct": field.get("risk_pct"),
                 "min": {lane: field.get(f"min_{lane}") for lane in lanes},
                 "max": {lane: field.get(f"max_{lane}") for lane in lanes},
                 "stale_hours": field.get("stale_hours")}
        try:
            store_stockplan.set_knobs(self.settings, typed,
                                      by=str(user.get("username") or ""),
                                      by_id=int(user["id"]))
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not save the stock planner's settings (%s)", exc)
            return self._redirect("/stock?said=no")
        return self._redirect("/stock?said=saved")

    def _switches(self) -> dict:
        """The flags the admin's footer line lists, off Settings."""
        return {name: bool(getattr(self.settings, name, False))
                for name in ("web_mutations", "manual_login", "log_db",
                             "pools_in_pg", "web_user_admin",
                             "station_for_operators")}

    # ------------------------------------------------------------ station
    #: The header every press and poll of the Station carries: `page` for
    #: the Station document, `live` for a phone's Live tab. With it, every
    #: door answers JSON; without it, every door behaves as it always has.
    STATION_ASKED = "X-GF-Station"

    def _station_asked(self) -> str:
        headers = getattr(self, "headers", None)
        if headers is None:
            return ""
        value = str(headers.get(self.STATION_ASKED) or "").strip().lower()
        return value if value in ("page", "live") else ""

    def _station_open_to(self, user: dict | None) -> bool:
        """Whether the Station is this person's: an admin's always, an
        operator's with STATION_FOR_OPERATORS on."""
        return bool(user) and (user.get("role") == "admin"
                               or bool(self.settings.station_for_operators))

    def _station_opened(self, path: str) -> bool:
        """With STATION_FOR_OPERATORS on, the Station's pages join the
        operator's: `/station` and everything under it."""
        return bool(self.settings.station_for_operators) and (
            path == "/station" or path.startswith("/station/"))

    def _json(self, code: int, obj, *, etag: str = "",
              headers: tuple = ()) -> None:
        data = json.dumps(obj, default=str, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if etag:
            self.send_header("ETag", etag)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _station_error(self, key: str) -> None:
        """One of the Station's fixed answers (a gate, a crash, a lockout),
        as JSON - never an HTML page the script cannot read."""
        code, body = _STATION_ERRORS[key]
        return self._json(code, dict(body))

    def _go(self, where: str, *, extra: dict | None = None) -> None:
        """Where a press lands: the address, or - for the Station - the
        answer that address's `said` stands for, as JSON."""
        if self._station_asked():
            said = (parse_qs(where.partition("?")[2]).get("said") or [""])[0]
            return self._station_answer(said, extra=extra)
        return self._redirect(where)

    def _station_answer(self, said: str, *, extra: dict | None = None) -> None:
        """A press's answer for the Station: the word, the request, the
        sentence, and the fresh state it left behind."""
        word, _, req = str(said or "").partition(":")
        user = self._user()
        body = {"ok": word not in pages._SAID_NO, "said": word,
                "req": int(req) if req.isdigit() else None,
                "note": (self._said_note(said, user)
                         or pages._DASH_SAID.get(word, "")),
                "pending": word in ("queued", "already")}
        self._station_extras(body, user)
        if extra:
            body.update(extra)
        return self._json(200, body)

    def _station_extras(self, body: dict, user: dict | None) -> None:
        """The state a press answer carries: the Station's whole state for
        the page, the phone's for its Live tab. Each read is its own
        try - an answer without it is still the answer."""
        from . import station_read

        asked = self._station_asked()
        if user is None or not asked:
            return
        if asked == "page":
            # The page's state is the Station's - and its read writes
            # (scrub, stamp, serve the line). Not for an operator while
            # the Station is not theirs: the header alone must not open
            # it through the shared doors (2026-09-29). A Live tab's own
            # phone state below stays, so a tab already open when the
            # flag goes off keeps its footing.
            if not self._station_open_to(user):
                return
            try:
                got = station_read.state(self.settings, user)
                if not got.pop(station_read.PARTIAL, False):
                    body["state"] = got
            except Exception as exc:                              # noqa: BLE001
                log.warning("the station state after %s was not read (%s)",
                            self.path, exc)
            return
        hit = re.match(r"^/(?:station/)?phones/(\d+)/", self.path)
        if hit is None:
            return
        try:
            got = station_read.live(self.settings, user, hit.group(1))
            if not got.pop(station_read.PARTIAL, False):
                body["live"] = got
        except Exception as exc:                                  # noqa: BLE001
            log.warning("the live state after %s was not read (%s)",
                        self.path, exc)

    def _station_reply(self, body: dict, *, state: bool = True) -> None:
        """A Station door's own answer: JSON with the header, and back to
        the Station without it."""
        if not self._station_asked():
            return self._redirect("/station")
        if state:
            self._station_extras(body, self._user())
        return self._json(200, body)

    def _own_hold(self, serial: str, user: dict) -> bool:
        """Whether the phone is this person's own Station hold - an
        admin's too: nobody closes or drives somebody else's from the
        Station. Raises on a store error (the press answers 503)."""
        from ..store import station as store_station

        return store_station.holds(self.settings, serial, int(user["id"]))

    def _power_clash(self, verb: str, exc: BaseException) -> bool:
        from ..store import station as store_station

        return (verb in store_station.POWER_VERBS
                and type(exc).__name__ == "UniqueViolation")

    def _power_pending(self, serial: str,
                       verbs: tuple = ()) -> dict | None:
        """The pending power press on this phone - Boot, Change IP or
        Power off - as `{"id", "verb", "words"}`, or None. One older than
        three minutes is an orphan a restart left behind, and is closed
        first so it blocks nothing (decision 40). With `verbs`, only a
        pending press of those verbs is looked for. Raises."""
        from ..store import station as store_station

        def pending():
            if verbs:
                return store_station.power_pending_of(self.settings, serial,
                                                      tuple(verbs))
            return store_station.power_pending_of(self.settings, serial)

        pend = pending()
        if pend is not None and pend.get("stale"):
            closed = store_station.expire_power(self.settings, serial)
            log.info("phone %s: %d power press(es) nobody answered for three "
                     "minutes closed", serial, closed)
            pend = pending()
        if pend is None or pend.get("stale"):
            return None
        verb = str(pend.get("verb") or "")
        if verbs and verb not in verbs:
            return None
        words = _POWER_WORDS.get(verb, "phone {s} is busy - wait for it")
        return {"id": int(pend["id"]), "verb": verb,
                "words": words.format(s=serial)}

    def _power_busy(self, serial: str, verbs: tuple) -> str:
        """The sentence for a pending power press of `verbs` on the phone,
        or "". Never fatal: a store that cannot say is no refusal, and
        the unique index still holds the line."""
        try:
            pend = self._power_pending(serial, verbs)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("phone %s: the pending power press was not read (%s)",
                      serial, exc)
            return ""
        return pend["words"] if pend else ""

    def _other_button(self, req, button: str, payload: dict) -> str:
        """A Station verdict whose twin was pressed with another key: the
        sentence that says so, or "" when it is the same key (or not a
        Station verdict at all)."""
        if not button:
            return ""
        from ..store import station as store_station

        try:
            row = store_station.press_of(self.settings, int(req))
        except Exception as exc:                                  # noqa: BLE001
            log.debug("the twin of %s was not read (%s)", req, exc)
            return ""
        first = str((row or {}).get("button") or "")
        if not first or first == button:
            return ""
        label = (pages.PHONE_STATES.get(first) or {}).get("label", first)
        return (f"phone {payload.get('serial') or '?'} is already being "
                f"closed as {label}")

    def _station_get(self, user: dict, path: str) -> None:
        """The Station's reads: its page, its state, a phone's Live tab and
        that tab's state."""
        from . import station_pages, station_read

        write = self.command != "HEAD"
        if path == "/station":
            state = station_read.state(self.settings, user, write=write)
            # A store that did not answer is said as down, never drawn as
            # an empty Station ("No phone yet", a Build that looks open).
            if state.pop(station_read.PARTIAL, False):
                return self._html(503, pages.store_down_page())
            return self._html(200, station_pages.station_page(state, user))
        if path == "/station/state":
            state = station_read.state(self.settings, user, write=write)
            if state.pop(station_read.PARTIAL, False):
                return self._station_error("down")
            tag = _state_tag(state)
            if self.headers.get("If-None-Match") == tag:
                self.send_response(304)
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return None
            return self._json(200, state, etag=tag)
        if path.startswith("/station/phones/"):
            serial, _, tail = path[len("/station/phones/"):].partition("/")
            if serial.isdigit() and tail == "":
                query = parse_qs(self.path.partition("?")[2])
                said = str((query.get("said") or [""])[0])[:64]
                note = self._said_note(said, user) if said else ""
                got = station_read.live(self.settings, user, serial,
                                        said=said, note=note)
                if got.pop(station_read.PARTIAL, False):
                    return self._html(503, pages.store_down_page())
                return self._html(200, station_pages.live_page(got, user))
            if serial.isdigit() and tail == "state":
                if write and self._station_asked() == "live":
                    self._poll_is_a_beat(user, serial)
                got = station_read.live(self.settings, user, serial)
                if got.pop(station_read.PARTIAL, False):
                    return self._station_error("down")
                return self._json(200, got)
        if self._station_asked():
            return self._station_error("none")
        return self._html(404, pages.page("404", "<h2>Nothing here</h2>",
                                          user=user))

    def _poll_is_a_beat(self, user: dict, serial: str) -> None:
        """The Live tab asking for its phone is the tab alive, as much as
        its beat is. Beats from a tab in the background went missing while
        that same tab's polls still arrived - 86 of 135 gaps of a minute
        or more in the access log of 4-6 Oct - and the sweep then switched
        phones off under operators who had only looked away (2026-10-07).
        The holder's own hold only, as the beat; never fatal."""
        from ..store import station as store_station

        try:
            store_station.watch(self.settings, serial, user["id"],
                                self.settings.live_tab_grace_seconds)
        except Exception as exc:                                  # noqa: BLE001
            log.debug("the poll of %s was not counted as a beat (%s)",
                      serial, exc)

    def _station_post(self, user: dict, field: dict) -> None:
        """The Station's own doors. Its own try, so a store that does not
        answer is the JSON the script reads - and never the store-down
        page, which echoes what was posted (a password, here)."""
        try:
            path = self.path
            if path == "/station/take":
                return self._station_line_press(user, field, leave=False)
            if path == "/station/line/leave":
                return self._station_line_press(user, field, leave=True)
            if path == "/station/build":
                return self._station_build(user, field)
            if path == "/station/me/name":
                return self._profile_name(user, field)
            if path == "/station/me/username":
                return self._profile_username(user, field)
            if path == "/station/me/password":
                return self._profile_password(user, field)
            hit = re.fullmatch(r"/station/phones/([^/]+)/back", path)
            if hit is not None and hit.group(1).isdigit():
                return self._station_give_back(user, hit.group(1), field)
            hit = re.fullmatch(r"/station/builds/([^/]+)/off", path)
            if hit is not None and hit.group(1).isdigit():
                return self._station_call_off(user, hit.group(1))
            if self._station_asked():
                return self._station_error("none")
            return self._html(404, pages.page("404", "<h2>Nothing here</h2>",
                                              user=user))
        except Exception as exc:                                  # noqa: BLE001
            if _store_down(exc):
                log.warning("web: POST %s - the store is not answering (%s)",
                            self.path, exc)
                if self._station_asked():
                    return self._station_error("down")
                return self._html(503, pages.store_down_page())
            log.exception("web: POST %s failed", self.path)
            if self._station_asked():
                return self._station_error("broke")
            return self._html(500, pages.page("Error",
                                              "<h2>Something broke</h2>"))

    def _station_boot(self, user: dict, serial: str) -> None:
        """Boot from the Station: its own hold only, never while a Change
        IP or a power-off is pending, and the answer is the Live tab."""
        back = f"/station/phones/{serial}"
        if not self._own_hold(serial, user):
            return self._refuse(user, "boot_phone",
                                {"serial": serial, "station": True},
                                f"phone {serial} is not yours any more",
                                back=back)
        busy = self._power_busy(serial, ("change_proxy", "power_off_phone"))
        if busy:
            return self._refuse(user, "boot_phone",
                                {"serial": serial, "station": True}, busy,
                                back=back)
        return self._act(user, "may_take_phones", "boot_phone",
                         {"serial": serial, "station": True},
                         idem=self._minute_key(user, "boot", serial),
                         back=back)

    def _station_proxy(self, user: dict, serial: str, field: dict) -> None:
        """Change IP from the Station: its own hold only, never while a
        Boot or a power-off is pending, and the power stays as the page
        drew it."""
        if not self._own_hold(serial, user):
            return self._refuse(user, "change_proxy",
                                {"serial": serial, "station": True},
                                f"phone {serial} is not yours any more",
                                back="/station")
        busy = self._power_busy(serial, ("boot_phone", "power_off_phone"))
        if busy:
            return self._refuse(user, "change_proxy",
                                {"serial": serial, "station": True}, busy,
                                back="/station")
        was = "on" if str(field.get("was") or "") == "on" else "off"
        return self._act(user, "may_change_proxy", "change_proxy",
                         {"serial": serial, "boot": False, "keep_power": True,
                          "station": True, "was_on_page": was},
                         idem=self._minute_key(user, "proxy", serial),
                         back="/station")

    def _station_line_press(self, user: dict, field: dict, *,
                            leave: bool) -> None:
        """Take a phone from a lane's shelf (or a place in its line), or
        leave the line. One store transaction each, which writes its own
        request row - a Take must never run late from the queue."""
        from ..store import station as store_station
        from ..store.users import may

        lane = str(field.get("lane") or "").strip().lower()
        if lane not in store_station.LANES:
            return self._station_reply({"ok": False, "said": "no", "req": None,
                                        "note": "Pick GPT or Spotify.",
                                        "pending": False}, state=False)
        if not self.settings.web_mutations:
            if self._station_asked():
                return self._station_error("off")
            return self._html(403, pages.page(
                "Disabled", "<h2>Actions are not switched on yet</h2>",
                user=user))
        key = self._minute_key(user, "leave" if leave else "take", lane)
        # Leaving asks for no tick: a person whose tick went while they
        # waited must still be able to step out of the line.
        if not leave and not may(user, "may_take_phones"):
            why = (f"{user['username']} may not do this - permission "
                   f"may_take_phones is off")
            req = store_station.record(
                self.settings, verb="take_phone", payload={"lane": lane},
                requested_by=user["id"], status="refused", result=why,
                idem_key=key)
            return self._station_reply({"ok": False, "said": "refused",
                                        "req": req or None,
                                        "note": pages._DASH_SAID["refused"],
                                        "pending": False})
        if leave:
            got = store_station.leave_line(
                self.settings, lane=lane, user_id=int(user["id"]),
                by=user["username"], idem_key=key)
        else:
            # The line first: a person waiting always beats a fresh Take.
            store_station.serve_lines(self.settings, (lane,))
            got = store_station.take(
                self.settings, lane=lane, user_id=int(user["id"]),
                by=user["username"], idem_key=key)
        outcome = str(got.get("outcome") or "no")
        word = ("twice" if got.get("twice") else
                {"took": "took", "line": "in-line", "left": "left"}.get(outcome,
                                                                        "no"))
        return self._station_reply({
            "ok": outcome in ("took", "line", "left"), "said": word,
            "req": int(got.get("action_id") or 0) or None,
            "note": str(got.get("sentence") or ""), "pending": False})

    def _station_give_back(self, user: dict, serial: str, field: dict) -> None:
        where = str(field.get("where") or "")
        if where not in ("station", "station-live"):
            where = "station"
        payload = {"serial": serial, "where": where}
        idem = self._minute_key(user, "giveback", serial)
        if not self._own_hold(serial, user):
            first = self._pressed_before(idem, payload)
            if first:
                return self._go(_said_url("/station", f"twice:{first}"))
            return self._refuse(user, "give_back", {"serial": serial},
                                f"phone {serial} is not yours any more",
                                back="/station")
        return self._act(user, "may_take_phones", "give_back", payload,
                         idem=idem, back="/station", said_word="gave-back")

    def _station_call_off(self, user: dict, ident: str) -> None:
        return self._act(user, "may_login_accounts", "call_off_build",
                         {"wanted_id": int(ident), "name": f"wish {ident}",
                          "admin": user.get("role") == "admin"},
                         idem=self._minute_key(user, "calloff", ident),
                         back="/station", said_word="called-off")

    def _station_build(self, user: dict, field: dict) -> None:
        """The build dialog: its checks, in the dialog's own words, then
        the dashboard's own build path with a Station payload."""
        from ..store import station as store_station
        from ..store.users import may

        if not self.settings.web_mutations:
            if self._station_asked():
                return self._station_error("off")
            return self._html(403, pages.page(
                "Disabled", "<h2>Actions are not switched on yet</h2>",
                user=user))
        kind = str(field.get("kind") or "").strip().lower()
        gmail_mode = _mode(field.get("gmail_mode"), ("auto", "manual", "none"))
        acct_mode = _mode(field.get("acct_mode"), ("none", "manual"))
        ip_mode = _mode(field.get("ip_mode"), ("auto", "manual"))
        gmail_line = str(field.get("gmail_line") or "").strip()
        acct_line = str(field.get("acct_line") or "").strip()
        ip_line = str(field.get("ip_line") or "").strip()
        # What a refusal may record: the choices, never a typed line.
        safe = {"kind": kind, "gmail_mode": gmail_mode, "acct_mode": acct_mode,
                "ip_mode": ip_mode}
        # Asked here rather than by `_act`, whose refusal would record the
        # payload - and the payload carries what was typed.
        if not may(user, "may_login_accounts"):
            return self._refuse(
                user, "build_by_hand", safe,
                f"{user['username']} may not do this - permission "
                f"may_login_accounts is off", back="/station")
        form = (store_station.build_form(self.settings)
                if "auto" in (gmail_mode, ip_mode) else {})
        why, where = _build_refusal(kind, gmail_mode, gmail_line, acct_mode,
                                    acct_line, ip_mode, ip_line, form)
        if why:
            return self._refuse(user, "build_by_hand", safe, why,
                                back="/station", extra={"field": where})
        payload = {
            "gmail": "", "no_gmail": gmail_mode == "none", "gmail_typed": False,
            "gmail_password": "", "gmail_secret": "",
            "proxy_name": "", "proxy_typed": False,
            "purpose": kind, "app": "", "install_app": False,
            "app_account": "", "app_category": "", "app_typed": False,
            "app_password": "", "app_secret": "",
            "station": True, "carry_address": "", "carry_password": "",
        }
        if gmail_mode == "manual":
            address, _, rest = gmail_line.partition(":")
            password, _, secret = rest.partition(":")
            payload.update(gmail=address.lower(), gmail_password=password,
                           gmail_secret=secret.strip(),
                           gmail_typed=self._is_new("gmail", address))
        if acct_mode == "manual":
            address, _, password = acct_line.partition(":")
            if kind == "other":
                payload.update(carry_address=address.lower(),
                               carry_password=password)
            else:
                payload.update(
                    app="spotify" if kind == "spotify" else "chatgpt",
                    install_app=True, app_account=address.lower(),
                    app_category=("normal" if kind == "spotify"
                                  else "" if password else "eco"),
                    app_password=password,
                    app_typed=self._is_new("app", address))
        if ip_mode == "manual":
            payload.update(proxy_name=ip_line, proxy_typed=True)
        return self._act(user, "may_login_accounts", "build_by_hand", payload,
                         idem=self._minute_key(user, "byhand", kind),
                         back="/station", said_word="asked",
                         digest_skip=BUILD_SECRETS)

    # ------------------------------------------------------ the profile
    def _profile_reply(self, ok: bool, note: str, *, field: str = "",
                       me: dict | None = None, go: str = "",
                       cookie: str = "") -> None:
        """A profile answer: JSON with the header (status 200 for a
        refusal, so the form shows its hint), back to the Station without
        it."""
        if not self._station_asked():
            self.send_response(303)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Location", "/station")
            self.end_headers()
            return None
        body = ({"ok": True, "said": "saved", "note": note, "me": me or {}}
                if ok else
                {"ok": False, "said": "bad", "note": note, "field": field})
        if go:
            body["go"] = go
        return self._json(200, body,
                          headers=(("Set-Cookie", cookie),) if cookie else ())

    def _profile_locked(self, user: dict) -> bool:
        """The login lockout, by username and by id - so a rename never
        forgives the count of wrong current passwords."""
        return (_locked_out(str(user["username"]))
                or _locked_out(f"id:{user['id']}"))

    def _profile_record(self, user: dict, key: str, *, verb: str,
                        payload: dict, result: str) -> None:
        """The profile press's row on the actions list. Written after the
        change has committed, so it is never fatal: a store blip here
        used to answer "broke" for a name that was saved - and, for a
        password, without the new seat's cookie, so the person was
        signed out everywhere and told the change failed (2026-09-29)."""
        from ..store import station as store_station

        try:
            store_station.record(
                self.settings, verb=verb, payload=payload,
                requested_by=user["id"], status="done", result=result,
                idem_key=self._minute_key(user, key, str(user["id"])))
        except Exception as exc:                                  # noqa: BLE001
            log.warning("the %s press of %s was not recorded (%s)", verb,
                        user.get("username"), exc)

    def _profile_name(self, user: dict, field: dict) -> None:
        from ..store import users as store_users
        from . import station_read

        try:
            new = store_users.set_name(
                self.settings, int(user["id"]),
                store_users.clean_name(field.get("name") or ""))
        except ValueError as exc:
            return self._profile_reply(False, str(exc), field="name")
        self._profile_record(
            user, "name", verb="profile_name", payload={"name": new},
            result="Name saved.")
        return self._profile_reply(
            True, "Name saved.",
            me=station_read.me(dict(user, display_name=new)))

    def _profile_username(self, user: dict, field: dict) -> None:
        """A person renaming themselves. No current password (the
        prototype's one field, the lead's call); the lockout still holds,
        and nothing here forgives it."""
        from ..store import users as store_users
        from . import station_read

        if self._profile_locked(user):
            if self._station_asked():
                return self._station_error("locked")
            return self._redirect("/station")
        try:
            new = store_users.set_username(self.settings, int(user["id"]),
                                           field.get("username") or "")
        except ValueError as exc:
            return self._profile_reply(False, str(exc), field="user")
        said = "Username saved. Use it the next time you sign in."
        self._profile_record(
            user, "username", verb="profile_username",
            payload={"username": new, "was": user["username"]}, result=said)
        log.info("user %s is %s now", user["username"], new)
        return self._profile_reply(
            True, said, me=station_read.me(dict(user, username=new)))

    def _profile_password(self, user: dict, field: dict) -> None:
        """A person changing their own password: the current one first,
        then the new hash and this browser's new seat in one transaction,
        which signs every other browser out."""
        from ..store import users as store_users
        from . import station_read

        if self._profile_locked(user):
            if self._station_asked():
                return self._station_error("locked")
            return self._redirect("/station")
        current = str(field.get("current") or "")
        new = str(field.get("password") or "")
        again = str(field.get("again") or "")
        if not current:
            return self._profile_reply(False, "Type your current password first.",
                                       field="pw")
        if len(new) < store_users.PASSWORD_MIN:
            return self._profile_reply(
                False, "The new password needs at least 8 characters.",
                field="pw")
        if new != again:
            return self._profile_reply(
                False, "The two new passwords are not the same.", field="pw")
        if new == current:
            return self._profile_reply(
                False, "The new password is the same as the current one.",
                field="pw")
        try:
            token = store_users.change_password(
                self.settings, int(user["id"]), current, new,
                token=self._cookie(), hours=SESSION_HOURS)
        except store_users.WrongPassword:
            _note_failure(str(user["username"]))
            _note_failure(f"id:{user['id']}")
            log.info("user %s gave a wrong current password", user["username"])
            return self._profile_reply(
                False, "That is not your current password.", field="pw")
        except ValueError as exc:
            return self._profile_reply(False, str(exc), field="pw")
        with _lock:
            _failures.pop(str(user["username"]), None)
            _failures.pop(f"id:{user['id']}", None)
        said = "Password changed. Your other browsers are signed out."
        self._profile_record(user, "password", verb="profile_password",
                             payload={}, result=said)
        log.info("user %s changed their password", user["username"])
        me = station_read.me(user)
        me["pw"] = "Changed today"
        return self._profile_reply(
            True, said, me=me, go="" if token else "/login",
            cookie=self._seat_cookie(token) if token else "")

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
            phone = read.phone_holder(self.settings, serial) or {}
        except Exception as exc:                                  # noqa: BLE001
            log.debug("could not read phone %s back (%s)", serial, exc)
            return "", ""
        return pages._holder(phone), pages._theirs(user, phone)

    def _refuse(self, user: dict, verb: str, payload: dict,
                why: str, back: str = "/", *,
                extra: dict | None = None) -> None:
        """Say no, and leave the same record a refused permission leaves.

        The answer carries the request's id, so the banner reads `why`
        off the row: it used to answer the bare word, whose sentence is
        about a missing permission - "phone 3480 is with reza" was shown
        as "ask an admin for the tick", to a person who had the tick
        (2026-09-21, found by audit). And it goes back to the page the
        press was made on, not to the dashboard.
        """
        from ..store import actions as store_actions

        try:
            new_id = store_actions.record_refused(
                self.settings, verb=verb,
                payload=dict(payload, by=user["username"], by_id=user["id"]),
                requested_by=user["id"], reason=why)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not record the refusal (%s)", exc)
            return self._go(_said_url(back, "refused"), extra=extra)
        return self._go(_said_url(back, f"no:{new_id}"), extra=extra)

    def _phone_state(self, user: dict, serial: str, field: dict) -> None:
        """Take / Back / Done / Failed off the dashboard's table or the
        phone's own story (`back` says which). The two that delete the
        phone ask once, on a page that says so."""
        from ..store import verdicts

        state = (field.get("state") or "").strip().lower()
        plan = pages.PHONE_STATES.get(state)
        where = str(field.get("where") or "")
        # A verdict from the Station: one of its five keys, on the
        # presser's own Station hold - an admin's included (rev 42).
        station = where in ("station", "station-live")
        if plan is None or not serial.isdigit() or (
                station and state not in verdicts.KEYS):
            if self._station_asked():
                return self._station_error("none")
            return self._html(404, pages.page(
                "404", "<h2>Not a State word</h2>", user=user))
        # Decline and OR are failed to the farm; the button pressed
        # rides along as the operator's reason (store.verdicts).
        payload = {"serial": serial, "state": plan.get("state", state),
                   "button": state}
        if where in ("live", "dash", "story", "station", "station-live"):
            payload["where"] = where
        if station:
            payload["mine"] = True
        idem = self._minute_key(user, f"state-{state}", serial)
        if station and not self._own_hold(serial, user):
            first = self._pressed_before(idem, payload)
            if first:
                return self._go(_said_url("/station", f"twice:{first}"))
            return self._refuse(
                user, "set_phone_state", {"serial": serial, "state": state},
                f"phone {serial} is not yours any more", back="/station")
        held, theirs = self._holder_of(user, serial)
        if theirs:
            return self._refuse(
                user, "set_phone_state", {"serial": serial, "state": state},
                f"phone {serial} is with {theirs} - the three ways a phone "
                f"comes back belong to whoever is holding it",
                back=_phone_back(field, serial))
        word = plan.get("word", state)
        if held and held != user.get("username"):
            # An admin ending somebody else's hold (2026-09-15): said on
            # the request, so whoever comes back to find their phone
            # gone can read who did it and why.
            payload["held_by"] = held
        back = "/station" if station else _phone_back(field, serial)
        if plan["sure"] and field.get("sure") != "1":
            if self._station_asked():
                return self._station_error("ask")
            return self._html(200, pages.confirm_page(
                user, title=f"Mark phone {serial} {word}?",
                text=plan["text"], action=f"/phones/{serial}/state",
                fields={"state": state, "sure": "1", "back": back,
                        **({"where": where} if where else {})},
                button=f"Yes, phone {serial} is {word}", back=back))
        if state == "unused":
            # A power-off is not in the one-press index (rev 42), so a
            # Release beside a pending Boot or Change IP could run its
            # power-off first and the Boot would then start the phone
            # again and mark it taken. Wait for that press instead.
            busy = self._power_busy(serial, ("boot_phone", "change_proxy"))
            if busy:
                return self._refuse(
                    user, "set_phone_state",
                    {"serial": serial, "state": state}, busy, back=back)
            self._power_off(user, serial)
        return self._act(user, "may_take_phones", "set_phone_state", payload,
                         idem=idem, back=back, said_word=plan["said"],
                         same_button=state if station else "")

    def _pressed_before(self, idem: str, payload: dict) -> int | None:
        """The request this very press already wrote, or None.

        The Station keeps a press that got no answer and sends it again,
        token and all, once the farm answers (station.js, presses that
        wait). When the first did arrive, the phone it closed is nobody's
        hold any more, and the check that turns away a press on somebody
        else's phone would answer the farm's own press "not yours any
        more". Only a press with a token of its own is looked for: without
        one the key is the minute's, which any press in it shares."""
        if not getattr(self, "_press", ""):
            return None
        from ..store import actions as store_actions

        try:
            return store_actions.by_idem(self.settings,
                                         f"{idem}:{_digest(payload)}")
        except Exception as exc:                                  # noqa: BLE001
            log.debug("the first send of a press was not read (%s)", exc)
            return None

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

    def _picture(self, user: dict, path: str) -> None:
        """A picture of one archived screen for the phone's journey: `wire`
        draws the screen from its XML (web/journey), `shot` is the real
        screenshot a failing flow saved. The same guards as the XML's
        (read.screen_bytes); a screenshot is shown to those who may take
        phones, since it is the phone's screen as it stood."""
        from ..store.users import may
        from . import journey

        serial, _, rest = path[len("/phones/"):].partition("/")
        what, _, rest = rest.partition("/")
        folder, _, name = rest.partition("/")
        if what == "shot" and not may(user, "may_take_phones"):
            return self._html(403, pages.forbidden(user))
        found = (read.screen_bytes(self.settings, serial, folder, name,
                                   suffix=".png" if what == "shot" else ".xml")
                 if serial.isdigit() and what in ("wire", "shot") else None)
        if found is None:
            return self._html(404, pages.page(
                "404", "<h2>No such screen</h2>", user=user))
        if what == "wire":
            return self._bytes(200, journey.wireframe_svg(found).encode(),
                               "image/svg+xml")
        return self._bytes(200, found, "image/png")

    def _bytes(self, code: int, data: bytes, kind: str) -> None:
        """An archived picture. They never change once written, so the
        browser keeps them - privately: they are this farm's screens."""
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=86400")
        # A drawing is shown as an image; opened on its own it is a page,
        # and it may run nothing.
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

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
                idem=self._minute_key(user, "edit_app", address), back=back,
                # The Gmail Save answered with its row and these two
                # did not (2026-09-22): a Save in the drawer left the
                # row as it was, like the removes once did.
                row_of="spotify")
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
                             back=back, row_of="spotify")
        if path == "/pools/spotify/undo":
            return self._undo_remove(user, "spotify", field)
        if path == "/pools/spotify/remove-delivered":
            return self._remove_delivered(user, "spotify", field)
        return self._html(404, pages.page("404", "<h2>Nothing here</h2>",
                                          user=user))

    def _pool_post(self, user: dict, field: dict) -> None:
        from . import paste

        path = self.path
        if path == "/pools/proxy/do":
            return self._proxies_do(user, field)
        if path == "/pools/proxy/add-batch":
            return self._proxies_add(user, field)
        if path == "/pools/gmail/do":
            return self._gmails_do(user, field)
        if path == "/pools/gmail/save":
            return self._gmails_save(user, field)
        if path == "/pools/gmail/add-batch":
            return self._gmails_add(user, field)
        if path == "/pools/gmail/revert":
            return self._gmails_revert(user, field)
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
        if path == "/pools/gmail/remove-group":
            return self._remove_gmail_group(user, field)
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
                             back=_gmail_back(field), row_of="gmail")
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
                    # The triple read.known keys on, as the add verb
                    # matches: one host:port carries many exits.
                    here = (f"{checked['host']}:{checked['port']}:"
                            f"{checked['username']}")
                    row["duplicate"] = here in known
                    row["dup_state"] = known.get(here, "")
                except (validate.AccountError, validate.ProxyError) as exc:
                    log.debug("proxy paste row refused: %s", exc)
                    row["error"] = str(exc)
            self._mark_twice(rows, "raw")
            return self._html(200, pages.proxy_preview(
                rows, user, idem=secrets.token_urlsafe(12),
                back=_add_back(field, "/pools/proxy"),
                purpose=(field.get("purpose") or "").strip().lower()))
        if path == "/pools/proxy/add":
            rows = [{"raw": r["raw"], "name": r["name"]}
                    for r in paste.proxies(field.get("rows", ""))]
            return self._act(user, "may_change_proxy", "add_proxies",
                             {"rows": rows,
                              "purpose": (field.get("purpose") or "")
                              .strip().lower()},
                             idem=field.get("idem") or secrets.token_urlsafe(12),
                             back=_add_back(field, "/pools/proxy"))
        if path in ("/pools/proxy/free", "/pools/proxy/test",
                    "/pools/proxy/remove", "/pools/proxy/aside"):
            verb = {"free": "mark_proxy_free", "test": "test_proxy",
                    "remove": "remove_proxy",
                    "aside": "shelve_proxy"}[path.rsplit("/", 1)[1]]
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
                             back=back, row_of="proxy")
        if path == "/pools/proxy/for":
            # Which lane an exit is kept for (purposes.py, 2026-09-29).
            name = (field.get("name") or "").strip()
            purpose = (field.get("purpose") or "").strip().lower()
            return self._act(user, "may_change_proxy", "keep_proxy_for",
                             {"name": name, "purpose": purpose},
                             idem=self._minute_key(user, f"for-{purpose}", name),
                             back=_proxy_back(field), row_of="proxy")
        if path == "/pools/proxy/test-all":
            return self._act(user, "may_change_proxy", "test_all_proxies", {},
                             idem=self._minute_key(user, "test_all", "-"),
                             back=_proxy_back(field))
        if path == "/pools/proxy/free-shelved":
            # The shelf back in one press, tested first (2026-09-29).
            return self._act(user, "may_change_proxy", "free_shelved_proxies",
                             {}, idem=self._minute_key(user, "free_shelved", "-"),
                             back=_proxy_back(field))
        if path == "/pools/proxy/aside-all":
            # Every free exit off the shelf, kept: a person's choice, not a
            # verdict, and Free on a row undoes it (2026-09-28).
            return self._act(user, "may_change_proxy", "shelve_all_proxies",
                             {}, idem=self._minute_key(user, "aside_all", "-"),
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
                back=_add_back(field, "/pools/gpt"), row_of="gpt")
        if path == "/pools/gpt/remove-delivered":
            return self._remove_delivered(user, "gpt", field)
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
                             # The Gmail door answered with its row and
                             # these two did not, so a Remove in the
                             # drawer left the row on screen exactly as
                             # it was (2026-09-21, found by audit).
                             back=back, row_of="gpt")
        if path == "/pools/gpt/offer":
            address = (field.get("address") or "").strip()
            return self._act(user, "may_add_gpt", "offer_again",
                             {"address": address},
                             idem=self._minute_key(user, "offer", address),
                             back=_add_back(field, "/pools/gpt"),
                             row_of="gpt")
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
        self.send_header("Set-Cookie", self._seat_cookie(token))
        self.send_header("Location", "/")
        self.end_headers()

    def _seat_cookie(self, token: str) -> str:
        """The session cookie for this browser's seat. `Secure` when the
        request came over TLS - the reverse proxy in front of the console
        (Caddy, farm.iranspoty.store) says so in X-Forwarded-Proto. Left
        off over the ssh tunnel, which is plain http on loopback and would
        otherwise never see the cookie."""
        secure = ("; Secure" if (self.headers.get("X-Forwarded-Proto") or ""
                                 ).lower() == "https" else "")
        return f"gf={token}; HttpOnly; SameSite=Lax; Path=/{secure}"

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
        # A stamp of what the sheet is drawn from. An open drawer asks
        # again with it on every tick and is told 304 until something
        # moved, so the list a person works in is current without the
        # 726KB being sent again for nothing (2026-09-21). Weak, since
        # the bytes differ on every drawing - the press tokens.
        stamp = str(listed.get("stamp") or "")
        tag = ('W/"' + hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]
               + '"') if stamp else ""
        if tag and self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self._html(200, pages._pool_sheet(
            kind, listed.get(kind) or [], listed.get("totals") or {}, user,
            manual_login=self.settings.manual_login,
            pending=listed.get("pending") or {}), etag=tag)

    def _credentials(self, user: dict, path: str) -> None:
        """One row's password and second factor, for the editor the moment
        its Edit is pressed. They rode in every row of the sheet until
        2026-09-21; the same people, the same door, one row at a time.
        Answered as JSON, which the script fills the boxes from by
        `.value` - nothing here is read as markup."""
        import json as _json

        kind = path[len("/pools/"):-len("/credentials")]
        meta = pages._POOL_KINDS.get(kind)
        if not meta or not meta.get("edit") or kind == "proxy":
            return self._text(404, "no such editor\n")
        if not pages._may(user, meta["manage"]):
            return self._text(403, "not yours to open\n")
        asked = parse_qs(self.path.partition("?")[2])
        address = str((asked.get("address") or [""])[0]).strip()
        found = read.credentials(self.settings, kind, address)
        if found is None:
            return self._text(404, "no such row\n")
        self._text(200, _json.dumps({"password": str(found.get("password") or ""),
                                     "secret": str(found.get("secret") or "")}),
                   kind="application/json")

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

    def _html(self, code: int, body: str, *, etag: str = "") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        if etag:
            self.send_header("ETag", etag)
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
            return self._go(_said_url("/", "no"))
        try:
            gone = store_wanted.dismiss(
                self.settings, int(ident), user_id=user["id"],
                admin=user.get("role") == "admin")
        except Exception as exc:                                  # noqa: BLE001
            log.warning("could not dismiss wish %s (%s)", ident, exc)
            gone = False
        said = ("Taken off your station." if gone
                else "That build cannot be dismissed.")
        if self._station_asked() and self._station_open_to(user):
            # A press the Station made is recorded (brief B10); the
            # dashboard's Dismiss stays the direct write it always was.
            from ..store import station as store_station

            try:
                store_station.record(
                    self.settings, verb="dismiss_build",
                    payload={"wanted_id": int(ident)},
                    requested_by=user["id"],
                    status="done" if gone else "refused", result=said,
                    idem_key=self._minute_key(user, "dismiss", ident))
            except Exception as exc:                              # noqa: BLE001
                log.warning("the dismiss of wish %s was not recorded (%s)",
                            ident, exc)
        return self._go(_said_url("/", "dismissed" if gone else "no"),
                        extra={"note": said})

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
        bare = path.split("?")[0]
        if not path or bare == "/live":
            return
        # The Station's two polls, every few seconds per open page: a line
        # each would bury every other request (2026-09-29). Their good
        # answers only - a run of 401s, 403s or 503s is exactly what the
        # log is for.
        if bare.startswith("/station/") and bare.endswith("/state") and \
                getattr(self, "_code", "-") in (200, 304):
            return
        spent = (time.perf_counter() - getattr(self, "_began", 0.0)) * 1000
        log.info("web %s %s %s %.0fms %s bytes",
                 getattr(self, "command", "?"), path[:120],
                 getattr(self, "_code", "-"), spent,
                 getattr(self, "_sent", 0))



# ------------------------------------------------------------ the Station
#: The verbs rev 42's unique index `actions_one_power_press` holds to one
#: pending press per phone. A power-off is not one of them: a give-back,
#: the hour's and a closed tab's switch-off always get their own row, and
#: a Boot queued behind one is refused by its holder check.
_INDEXED_POWER = ("boot_phone", "change_proxy")

#: The Station's fixed answers, by the word the script reads (§5.1): the
#: gates, the lockout, a crash. `go` is where the script sends the page.
_STATION_ERRORS = {
    "signed-out": (401, {"ok": False, "said": "signed-out",
                         "note": "You are signed out.", "go": "/login"}),
    "stale": (403, {"ok": False, "said": "stale",
                    "note": "Your session changed - reload the page.",
                    "go": None}),
    "origin": (403, {"ok": False, "said": "origin",
                     "note": "That came from another site. Nothing was "
                             "changed.", "go": None}),
    "password": (403, {"ok": False, "said": "password",
                       "note": "Choose your own password first.",
                       "go": "/password"}),
    "refused": (403, {"ok": False, "said": "refused",
                      "note": "That belongs to an admin. Nothing was changed.",
                      "go": "/"}),
    "off": (403, {"ok": False, "said": "off",
                  "note": "Actions are not switched on yet."}),
    "ask": (409, {"ok": False, "said": "ask",
                  "note": "Press the key a second time to close the phone."}),
    "locked": (429, {"ok": False, "said": "locked",
                     "note": "Too many wrong answers in a row - try again in "
                             "a few minutes."}),
    "down": (503, {"ok": False, "said": "down",
                   "note": "The farm's store is not answering - try again in "
                           "a moment."}),
    "broke": (500, {"ok": False, "said": "broke",
                    "note": "Something broke - it is in the server log."}),
    "none": (404, {"ok": False, "said": "none", "note": "Nothing here."}),
}

#: What a pending power press on a phone is, in the words a refusal says.
_POWER_WORDS = {
    "boot_phone": "phone {s} is booting - wait for it",
    "change_proxy": "phone {s} is changing its IP - wait for it",
    "power_off_phone": "phone {s} is switching off - press Boot again in a "
                       "moment",
}

#: The build dialog's typed keys the idem key leaves out: a retried press
#: digests to the same key though `_is_new` answers differently the second
#: time, and no hash of a typed password is kept in `idem_key`.
BUILD_SECRETS = ("gmail_typed", "app_typed", "gmail_password", "gmail_secret",
                 "app_password", "app_secret", "carry_password", "proxy_name")

#: The dialog's three formats, the prototype's own.
GMAIL_LINE = re.compile(r"^[^\s:@]+@[^\s:@]+\.[^\s:@]+:[^\s:]+(:.+)?$")
ACCT_LINE = re.compile(r"^[^\s:@]+@[^\s:@]+\.[^\s:@]+(:\S+)?$")
IP_LINE = re.compile(r"^[A-Za-z0-9.-]+:\d{2,5}(:[^\s:]+:[^\s:]+)?$")

_LANE_WORDS = {"gpt": "GPT", "spotify": "Spotify"}


def _mode(value, allowed: tuple) -> str:
    """One of a dialog row's modes; anything else is the row's default."""
    word = str(value or "").strip().lower()
    return word if word in allowed else allowed[0]


def _build_refusal(kind: str, gmail_mode: str, gmail_line: str,
                   acct_mode: str, acct_line: str, ip_mode: str, ip_line: str,
                   form: dict) -> tuple[str, str]:
    """Why the build dialog's choice cannot be built, and which row to
    mark - ("", "") when it can. The first rule that fails wins (§5.6).
    A stopped farm is not a reason: the wish waits for it to start."""
    if kind not in ("gpt", "spotify", "other"):
        return "Pick what the phone is for.", ""
    if gmail_mode == "manual" and not GMAIL_LINE.match(gmail_line):
        return "Type it as email:password:2FA key.", "gmail"
    if acct_mode == "manual" and not ACCT_LINE.match(acct_line):
        return "Type it as email:password, or the email alone.", "acct"
    if ip_mode == "manual" and not IP_LINE.match(ip_line):
        return "Type it as host:port:user:password.", "ip"
    if kind == "gpt" and acct_mode == "manual" and gmail_mode == "none":
        return ("A GPT account needs a Gmail on the phone – set Gmail to Auto "
                "or Manual.", "acct")
    if kind == "spotify" and acct_mode == "manual" and gmail_mode != "none":
        return ("A Spotify account goes on a phone with no Gmail – set Gmail "
                "to None.", "acct")
    if kind == "spotify" and acct_mode == "manual" and ":" not in acct_line:
        return "A Spotify account needs its password: email:password.", "acct"
    if gmail_mode == "auto" and int(form.get("gmails_left") or 0) == 0:
        return ("No free Gmail in the pool – type one under Manual, or pick "
                "None.", "gmail")
    if ip_mode == "auto" and int((form.get("free_ips") or {}).get(kind) or 0) == 0:
        if kind == "other":
            return "No free IP – type one under Manual.", "ip"
        return f"No free {_LANE_WORDS[kind]} IP – type one under Manual.", "ip"
    return "", ""


def _state_tag(state: dict) -> str:
    """The Station state's ETag: the body without `now`, which is the one
    value that moves on every read."""
    stable = {k: v for k, v in state.items() if k != "now"}
    text = json.dumps(stable, default=str, sort_keys=True,
                      separators=(",", ":"))
    return 'W/"' + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] + '"'


#: Where "Log in selected" may send the person back: the two pages that
#: carry the ticks. Anything else in the form's `back` goes to the front.
LOGIN_BACKS = ("/", "/pools/gpt")


def _login_back(field: dict) -> str:
    """Where "Log in selected" comes back to: one of the two pages with
    ticks on it, rebuilt with its view, search and page (`_back_to`).
    It was matched whole, so ticks on page two of a search came back to
    page one of the plain list (2026-09-21, found by audit)."""
    back = _back_to(field, "/")
    return back if back.partition("?")[0] in LOGIN_BACKS else "/"

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
    # The page's own views (pages.GPT_VIEWS): this list once carried a
    # `set_aside` the page never had and lacked the `needs_human` it
    # has, so no door could come back to the set-aside list
    # (2026-09-21, found by audit).
    "/pools/gpt": ("waiting", "on_phone", "needs_human", "delivered"),
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


def _digest(payload: dict, skip: tuple = ()) -> str:
    """The press's words, in ten characters, for the idem key.

    The key was the press token alone - one per drawing of the form - so
    a corrected Save of the same row from the same drawing of the sheet
    was "the same press, sent twice" and thrown away with a green
    "already went through" over it: the operator retyped a password and
    the row kept the old one (2026-09-21, found by audit). The same press
    with the same words is still one request; with different words it is
    a new one. `by`/`by_id` are left out: they are who pressed, not what,
    and so is whatever `skip` names (a Station build's typed secrets).
    """
    said = {k: v for k, v in (payload or {}).items()
            if k not in ("by", "by_id") + tuple(skip)}
    return hashlib.sha1(json.dumps(said, sort_keys=True, default=str)
                        .encode("utf-8")).hexdigest()[:10]


#: The verbs about the whole pool rather than one row, which the
#: double-press guard dedupes on the verb alone. `build_by_hand` is
#: deliberately not here: two presses may well mean two phones.
#: The verbs that ask the cloud about one proxy, and the ones that ask
#: about several at once (`_proxies_do`).
_TESTED_TOGETHER = {"mark_proxy_free": "free_proxies",
                    "test_proxy": "test_proxies"}

#: What a press of the Proxies page may ask (`_proxy_verb`).
_PROXY_PRESS = re.compile(r"free|aside|test|remove|lane:(?:gpt|spotify)?|cap:\d{1,2}")


def _proxy_verb(what: str, e: dict) -> tuple[str | None, dict]:
    """Which request a press of the Proxies page is for one proxy, by its
    state as the page draws it - None when it is already that way. On is
    "in play": free, or on a phone it keeps; off is set aside, at once or
    once its phone goes."""
    s, after = e["s"], bool(e.get("after"))
    if what == "free":
        if s == "phone" and after:
            return "unshelve_proxy", {}
        return ("mark_proxy_free", {}) if s in ("aside", "dead") else (None, {})
    if what == "aside":
        # Off is a person's shelf: an exit resting after a refusal, or dead,
        # would come back into the builds on its own (the rest rule, the
        # retests), so it is shelved as well. One already on the shelf, under
        # its phone or not, is already that way.
        return (None, {}) if e.get("shelf") else ("shelve_proxy", {})
    if what.startswith("lane:"):
        lane = what[5:]
        return (("keep_proxy_for", {"purpose": lane}) if e["lane"] != lane
                else (None, {}))
    if what.startswith("cap:"):
        cap = min(99, int(what[4:]))
        return ("cap_proxy", {"cap": cap}) if e["cap"] != cap else (None, {})
    if what == "test":
        return "test_proxy", {}
    if what == "remove":
        return "remove_proxy", {}
    return None, {}


#: What a press of the Gmails page may ask, and the request each is.
#: `for:<lane>` is `gmails_keep_for` (`_gmails_do`).
_GMAIL_PRESS = {"aside": "gmails_aside", "free": "gmails_queue",
                "mend": "gmails_mend", "remove": "gmails_remove"}


def _gmail_outcome(row: dict) -> dict:
    """A Gmails page request as the page may be told it: its status and
    sentence, which Gmails moved and why the rest did not - never a value
    from its detail that a person typed (a password rides in a save's)."""
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}

    def ints(key):
        return [int(i) for i in detail.get(key) or [] if str(i).isdecimal()]

    left = detail.get("left") if isinstance(detail.get("left"), dict) else {}
    return {"status": str(row.get("status") or ""),
            "said": str(row.get("result") or ""),
            "verb": str(row.get("verb") or ""),
            "ids": ints("ids"),
            "left": {str(k): str(v) for k, v in left.items()},
            "mended": bool(detail.get("mended")),
            "unfixable": bool(detail.get("unfixable")),
            "added": ints("added"),
            "returned": [int(r.get("id") or 0) for r in detail.get("returned") or []
                         if isinstance(r, dict)],
            "refused": [{"address": str(r.get("address") or ""),
                         "why": str(r.get("why") or "")}
                        for r in detail.get("left_out") or [] if isinstance(r, dict)],
            "carried": ints("carried"),
            "back": ints("back"), "moved": ints("moved")}


_SWEEPS = frozenset({"test_all_proxies", "free_all_proxies",
                     "shelve_all_proxies", "free_shelved_proxies",
                     "remove_delivered_apps", "remove_gmail_group"})


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
    connection refused or timed out is a page, not a traceback.

    Its subclasses too, by the class's ancestry rather than its own name:
    a connect that times out raises `ConnectionTimeout` and a cluster
    restart `AdminShutdown`, and matched by name alone both read as a
    crash - 500 `broke`, not the 503 `down` the Station waits out
    (2026-09-29)."""
    def down(err: BaseException) -> bool:
        return any(k.__name__ == "OperationalError"
                   for k in type(err).__mro__)

    return down(exc) or any(
        down(c) for c in (exc.__cause__, exc.__context__) if c is not None)


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


#: Who a verdict blames, in the words the phone's journey shows.
BLAME_WORDS = {"credential": "the account", "exit": "the exit",
               "device": "the phone", "challenged": "a code or a check",
               "nobody": "nobody"}


def _run_words(run: dict) -> dict:
    """A run of the phone's journey with what ended it in words: the
    specific reason where the failing flow named its last screen for one
    (`180610-captcha_shown.xml`), the run's own status otherwise - and
    who that blames."""
    from ..failures import knows, verdict

    reason = ""
    dump = str(run.get("last_dump") or "")
    if "-" in dump:
        reason = dump.split("-", 1)[1].rsplit(".", 1)[0]
    for word in (reason, str(run.get("status") or "")):
        if word and knows(word):
            found = verdict(word)
            return dict(run, why=found.seen, advice=found.advice,
                        blame=BLAME_WORDS.get(found.blame, ""), reason=word)
    return dict(run, why="", advice="", blame="", reason="")


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
    # And the editor's one-row read, which is the sheet's Edit door.
    if path.startswith("/pools/") and path.endswith("/credentials"):
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
    "/pools/gmail/remove-group",
    "/pools/gmail/free", "/pools/gmail/refund",
    "/pools/proxy/preview", "/pools/proxy/add", "/pools/proxy/free",
    "/pools/proxy/test", "/pools/proxy/remove", "/pools/proxy/test-all",
    "/pools/proxy/free-all", "/pools/proxy/aside", "/pools/proxy/aside-all",
    "/pools/proxy/free-shelved", "/pools/proxy/for",
    "/pools/gpt/preview", "/pools/gpt/add",
    "/pools/gpt/edit", "/pools/gpt/remove", "/pools/gpt/undo",
    "/pools/gpt/free",
    # The Spotify accounts are the same pool in the same table, kept by
    # whoever keeps the GPT ones (2026-09-17).
    "/pools/spotify/preview", "/pools/spotify/add",
    "/pools/spotify/edit", "/pools/spotify/remove", "/pools/spotify/free",
    "/pools/spotify/undo", "/pools/spotify/remove-delivered",
    "/pools/gpt/remove-delivered",
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


def _verdict(status: str):
    """The whole verdict for a task run's reason - both sentences, not
    the one `_advice` gives. A word the table never heard of answers
    None, and the page falls back to the run's own detail rather than
    printing the safe default at somebody as though it were a finding.
    """
    from ..failures import knows, verdict

    return verdict(status) if knows(status) else None


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
