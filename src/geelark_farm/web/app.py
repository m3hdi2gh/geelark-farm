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
from urllib.parse import parse_qs

from ..config import Settings
from . import pages, read

log = logging.getLogger(__name__)

SESSION_HOURS = 12
#: Five wrong answers buys this many seconds of "try later", per username.
LOCKOUT_AFTER = 5
LOCKOUT_SECONDS = 600

_sessions: dict[str, dict] = {}
_failures: dict[str, list[float]] = {}
_lock = threading.Lock()


def start(settings: Settings) -> ThreadingHTTPServer:
    """Bind, serve on a daemon thread, return the server (tests stop it)."""

    class Handler(_Handler):
        pass

    Handler.settings = settings
    pages.set_zone(settings.web_tz)
    server = ThreadingHTTPServer((settings.web_bind, settings.web_port),
                                 Handler)
    server.daemon_threads = True
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
            if path == "/login":
                return self._html(200, pages.login())
            user = self._user()
            if user is None:
                return self._redirect("/login")
            # A one-time password buys exactly one page: the one where the
            # person chooses their own. Everything else waits.
            if user.get("must_change_password") and path != "/password":
                return self._redirect("/password")
            if path == "/password":
                return self._html(200, pages.password_page(user))
            if path == "/users":
                return self._users_get(user)
            if path == "/":
                scope = None if user["sees"] == "all" else user["id"]
                query = parse_qs(self.path.partition("?")[2])
                return self._html(200, pages.dashboard(
                    read.dashboard(self.settings, scope), user,
                    said=(query.get("said") or [""])[0],
                    manual_login=self.settings.manual_login,
                    flags=self._switches()))
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
                return self._html(200, pages.needs_page(
                    read.needs(self.settings), user, _advice,
                    said=(query.get("said") or [""])[0]))
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
                return self._html(200, pages.gmail_pool_page(
                    read.gmail_pool(self.settings,
                                    view=first.get("view", "queued"),
                                    seller=first.get("seller", ""),
                                    page=_page_number(first)),
                    user, said=first.get("said", ""), advice=_advice))
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
                    show_ignored=first.get("ignored") == "1"))
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
                    manual_login=self.settings.manual_login))
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
                    story, user, explain=_explain))
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
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            field = {k: v[0] for k, v in form.items()}
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
            if self.path == "/logout":
                token = self._cookie()
                with _lock:
                    _sessions.pop(token, None)
                return self._redirect("/login")
            if self.path == "/password":
                return self._password_post(user, field, entry)
            if user.get("must_change_password"):
                return self._redirect("/password")
            if self.path.startswith("/pools/"):
                return self._pool_post(user, field)
            if self.path == "/accounts/login":
                back = field.get("back") or "/"
                return self._login_accounts(
                    user, form.get("addresses") or [],
                    back=back if back in LOGIN_BACKS else "/")
            if self.path.startswith("/requests/") and \
                    self.path.endswith("/retry"):
                return self._retry_action(user)
            if self.path.startswith("/phones/") and \
                    self.path.endswith("/stop"):
                serial = self.path[len("/phones/"):-len("/stop")]
                return self._act(user, "may_login_accounts", "stop_phone",
                                 {"serial": serial},
                                 idem=self._minute_key(user, "stop", serial),
                                 back="/requests")
            if self.path.startswith("/phones/") and \
                    self.path.endswith("/proxy"):
                serial = self.path[len("/phones/"):-len("/proxy")]
                return self._act(user, "may_change_proxy", "change_proxy",
                                 {"serial": serial},
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
    def _act(self, user: dict, permission: str, verb: str, payload: dict,
             *, idem: str, back: str) -> None:
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
        self._redirect(_said_url(back, f"queued:{req}"))

    def _login_accounts(self, user: dict, addresses: list,
                        back: str = "/") -> None:
        """"Log in selected" (C6), off the dashboard or the Gpt Pool -
        `back` is whichever the ticks were on. Only meaningful with
        manual login on: off, the pass logs accounts in by itself and
        the button would race it for the same rows."""
        if not self.settings.manual_login:
            return self._redirect(f"{back}?said=auto")
        chosen = [a.strip() for a in addresses if a and a.strip()]
        if not chosen:
            return self._redirect(_said_url(back, "none"))
        return self._act(user, "may_login_accounts", "login_accounts",
                         {"addresses": chosen},
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
        return f"{verb}:{target}:{user['id']}:{int(time.time()) // 60}"

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
    def _phone_state(self, user: dict, serial: str, field: dict) -> None:
        """Take / Back / Done / Failed off the dashboard's table or the
        phone's own story (`back` says which). The two that delete the
        phone ask once, on a page that says so."""
        state = (field.get("state") or "").strip().lower()
        plan = pages.PHONE_STATES.get(state)
        if plan is None or not serial.isdigit():
            return self._html(404, pages.page(
                "404", "<h2>Not a State word</h2>", user=user))
        back = _phone_back(field, serial)
        if plan["sure"] and field.get("sure") != "1":
            return self._html(200, pages.confirm_page(
                user, title=f"Mark phone {serial} {state}?",
                text=plan["text"], action=f"/phones/{serial}/state",
                fields={"state": state, "sure": "1", "back": back},
                button=f"Yes, phone {serial} is {state}", back=back))
        return self._act(user, "may_take_phones", "set_phone_state",
                         {"serial": serial, "state": state},
                         idem=self._minute_key(user, f"state-{state}", serial),
                         back=back)

    def _screen(self, user: dict, path: str) -> None:
        """One archived screen, as the plain text it is - and only one
        of this phone's, inside artifact_dir (read.screen_file guards
        the path). Anything else is a 404, never a listing."""
        serial, _, rest = path[len("/phones/"):].partition("/screens/")
        folder, _, name = rest.partition("/")
        found = (read.screen_file(self.settings, serial, folder, name)
                 if serial.isdigit() else None)
        if found is None:
            return self._html(404, pages.page(
                "404", "<h2>No such screen</h2>", user=user))
        return self._text(200, found.read_text(encoding="utf-8",
                                               errors="replace"))

    def _service(self, user: dict, what: str, field: dict) -> None:
        """Pause / Resume / Clear breaker / Stop / Start: admins only, and
        every one asks first - each changes what the next pass does to
        every phone at once."""
        if user.get("role") != "admin":
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
        return self._act(user, "admin", "control", {"what": what},
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
                row["duplicate"] = row["address"].lower() in known
            return self._html(200, pages.gmail_preview(
                rows, seller, user, idem=secrets.token_urlsafe(12),
                pasted=pasted, sellers=read.gmail_sellers(self.settings)))
        if path == "/pools/gmail/add":
            rows = [{"address": r["address"], "password": r["password"],
                     "secret": r["secret"], "recovery": r["recovery"]}
                    for r in paste.accounts(field.get("rows", ""))]
            return self._act(user, "may_add_gmail", "add_gmails",
                             {"rows": rows,
                              "seller": (field.get("seller") or "").strip()},
                             idem=field.get("idem") or secrets.token_urlsafe(12),
                             back="/pools/gmail")
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
                    row["duplicate"] = (
                        f"{checked['host']}:{checked['port']}" in known)
                except (validate.AccountError, validate.ProxyError) as exc:
                    log.debug("proxy paste row refused: %s", exc)
                    row["error"] = str(exc)
            return self._html(200, pages.proxy_preview(
                rows, user, idem=secrets.token_urlsafe(12)))
        if path == "/pools/proxy/add":
            rows = [{"raw": r["raw"], "name": r["name"]}
                    for r in paste.proxies(field.get("rows", ""))]
            return self._act(user, "may_add_proxy", "add_proxies",
                             {"rows": rows},
                             idem=field.get("idem") or secrets.token_urlsafe(12),
                             back="/pools/proxy")
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
            return self._act(user, "may_add_proxy", verb, {"name": name},
                             idem=self._minute_key(user, verb, name),
                             back=back)
        if path == "/pools/proxy/test-all":
            return self._act(user, "may_add_proxy", "test_all_proxies", {},
                             idem=self._minute_key(user, "test_all", "-"),
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
            return self._act(user, "may_add_proxy", "ignore_proxy", triple,
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
            return self._act(user, "may_add_proxy", "add_proxies",
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
            return self._act(user, "may_add_proxy", "adopt_proxy", held,
                             idem=self._minute_key(
                                 user, "adopt", f"{held['host']}:{held['port']}"),
                             back=_proxy_back(field))
        if path == "/pools/gpt/preview":
            from ..store import validate

            known = read.known(self.settings, "app")
            pasted = field.get("pasted") or ""
            rows = paste.accounts(pasted)
            for row in rows:
                try:
                    if row["recovery"]:
                        raise validate.AccountError(
                            f"{row['address']}: two addresses on one line - "
                            f"an app account has no recovery address")
                    validate.app_row(address=row["address"],
                                     password=row["password"],
                                     secret=row["secret"])
                except (validate.AccountError, validate.ProxyError) as exc:
                    log.debug("gpt paste row refused: %s", exc)
                    row["error"] = str(exc)
                row["duplicate"] = row["address"].lower() in known
            return self._html(200, pages.gpt_preview(
                rows, user, idem=secrets.token_urlsafe(12), pasted=pasted))
        if path == "/pools/gpt/add":
            from ..store import validate

            if "rows" in field:
                # The confirm off the preview: the good rows, as the
                # tab-separated text the preview showed.
                rows = [{"address": r["address"], "password": r["password"],
                         "secret": r["secret"], "email_code_only": False}
                        for r in paste.accounts(field.get("rows", ""))]
                return self._act(
                    user, "may_add_gpt", "add_gpt", {"rows": rows},
                    idem=field.get("idem") or secrets.token_urlsafe(12),
                    back="/pools/gpt")
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
            return self._act(user, "may_add_gpt", "add_gpt", {"rows": [row]},
                             idem=self._minute_key(user, "add_gpt",
                                                   row["address"].lower()),
                             back="/pools/gpt")
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
        _drop_sessions_of(target, keep=self._cookie())
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
        _drop_sessions_of(target, keep=self._cookie())
        log.info("password of user id %s reset by %s", target,
                 user["username"])
        self._html(200, pages.one_time_page(row["username"], password, user,
                                            created=False))

    def _password_post(self, user: dict, field: dict, entry: dict) -> None:
        from ..store import users as store_users

        new = field.get("password") or ""
        if new != (field.get("again") or ""):
            return self._html(200, pages.password_page(
                user, "The two do not match."))
        try:
            store_users.set_password(self.settings, user["id"], new)
        except ValueError as exc:
            return self._html(200, pages.password_page(user, str(exc)))
        with _lock:
            entry["user"]["must_change_password"] = False
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
        token = secrets.token_urlsafe(32)
        with _lock:
            _failures.pop(username, None)
            _sessions[token] = {"user": row,
                                "until": time.time() + SESSION_HOURS * 3600,
                                # One CSRF token per session, checked on
                                # every POST but /login. Dies with the
                                # process like the session - a re-login,
                                # nothing more.
                                "csrf": secrets.token_urlsafe(32)}
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

    def _entry(self) -> dict | None:
        token = self._cookie()
        with _lock:
            entry = _sessions.get(token)
            if entry is None or entry["until"] < time.time():
                _sessions.pop(token, None)
                return None
            return entry

    def _user(self) -> dict | None:
        entry = self._entry()
        if entry is None:
            return None
        # The csrf token rides in the user dict so every page's header
        # (the logout form) can carry it without a second parameter; so
        # do the flags the shell needs and the rail's counts.
        try:
            nav = read.nav_counts(self.settings)
        except Exception as exc:                                  # noqa: BLE001
            log.warning("the rail's counts did not load (%s)", exc)
            nav = {}
        return dict(entry["user"], csrf=entry.get("csrf", ""),
                    user_admin=self.settings.web_user_admin,
                    mutations=self.settings.web_mutations, nav=nav)

    def _cookie(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "gf":
                return value
        return ""

    # ----------------------------------------------------------- plumbing
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

    def _redirect(self, where: str) -> None:
        self.send_response(303)
        self.send_header("Location", where)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # Into the real log, at DEBUG: request lines are tracing, and the
        # file handler keeps DEBUG while the console shows INFO.
        log.debug("web: " + fmt, *args)


#: Where "Log in selected" may send the person back: the two pages that
#: carry the ticks. Anything else in the form's `back` goes to the front.
LOGIN_BACKS = ("/", "/pools/gpt")

#: Where a proxy button may send a person back to. Somebody who pressed
#: "Test again" on the work list wants the work list back, not the free
#: shelf; anything not named here is the shelf.
PROXY_BACKS = ("/pools/proxy", "/pools/proxy?view=needs_hand",
               "/pools/proxy?view=on_phone", "/pools/proxy?view=all")


def _proxy_back(field: dict) -> str:
    return (field.get("back") or "").strip() if (
        field.get("back") or "").strip() in PROXY_BACKS else "/pools/proxy"


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
        moment = pages._moment(r.get("updated_at"))
        writer.writerow([_csv_cell(v) for v in (
            r.get("address") or "", r.get("serial") or "",
            moment.isoformat(timespec="minutes") if moment
            else str(r.get("updated_at") or ""),
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
    """Where a phone button returns to: its story when the form said so,
    the dashboard otherwise - never an address the form made up."""
    back = str(field.get("back") or "")
    return back if back == f"/phones/{serial}" else "/"


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


def _drop_sessions_of(user_id: int, *, keep: str = "") -> int:
    """End every session this person holds, except the one token given
    (an admin editing themselves keeps their own seat). A permission
    change or a reset must not leave a stale session acting on old
    rights, and there is no second copy of the user row to refresh."""
    dropped = 0
    with _lock:
        for token, entry in list(_sessions.items()):
            if entry["user"].get("id") == user_id and token != keep:
                _sessions.pop(token, None)
                dropped += 1
    return dropped


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
