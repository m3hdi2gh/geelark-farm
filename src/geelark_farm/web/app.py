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

import hmac
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
                    manual_login=self.settings.manual_login))
            if path == "/phones":
                scope = None if user["sees"] == "all" else user["id"]
                return self._html(200, pages.phones_page(
                    read.phones(self.settings, scope), user))
            if path == "/requests":
                from ..store import actions as store_actions

                rows = store_actions.listing(
                    self.settings, user_id=user["id"],
                    everyone=user["role"] == "admin")
                query = parse_qs(self.path.partition("?")[2])
                said = (query.get("said") or [""])[0]
                return self._html(200, pages.requests_page(
                    rows, user, said=said))
            if path == "/needs":
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                return self._html(200, pages.needs_page(
                    read.needs(self.settings), user, _advice))
            if path == "/pools":
                return self._redirect("/pools/gmail")
            # The three pool pages (C5): shared stock, so everyone signed
            # in sees them; what they may DO on them is the buttons' job.
            query = parse_qs(self.path.partition("?")[2])
            first = {k: v[0] for k, v in query.items()}
            if path == "/pools/gmail":
                return self._html(200, pages.gmail_pool_page(
                    read.gmail_pool(self.settings,
                                    view=first.get("view", "active"),
                                    seller=first.get("seller", "")),
                    user, said=first.get("said", "")))
            if path == "/pools/proxy":
                from ..store import state as store_state

                return self._html(200, pages.proxy_pool_page(
                    read.proxy_pool(self.settings, unlisted=store_state.get(
                        self.settings, "unlisted_proxies", [])),
                    user, said=first.get("said", ""),
                    state=first.get("state", ""), q=first.get("q", "")))
            if path == "/pools/gpt":
                try:
                    number = max(1, int(first.get("page", "1")))
                except ValueError:
                    log.debug("page %r is not a number; showing the first",
                              first.get("page"))
                    number = 1
                return self._html(200, pages.gpt_pool_page(
                    read.gpt_pool(self.settings,
                                  view=first.get("view", "active"),
                                  q=first.get("q", ""), page=number),
                    user, said=first.get("said", "")))
            if path == "/events":
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                return self._html(200, pages.events_page(
                    read.events(self.settings), user))
            self._html(404, pages.page("404", "<h2>Nothing here</h2>",
                                       user=user))
        except Exception:                                         # noqa: BLE001
            # A handler that leaks a traceback leaks whatever was in it.
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
                return self._login_accounts(user, form.get("addresses") or [])
            if self.path.startswith("/phones/") and \
                    self.path.endswith("/proxy"):
                serial = self.path[len("/phones/"):-len("/proxy")]
                return self._act(user, "may_change_proxy", "change_proxy",
                                 {"serial": serial},
                                 idem=self._minute_key(user, "proxy", serial),
                                 back="/")
            if self.path == "/users/new":
                return self._users_new(user, field)
            if self.path.startswith("/users/") and \
                    self.path.endswith("/reset"):
                return self._users_reset(user)
            if self.path.startswith("/users/"):
                return self._users_update(user, field)
            if self.path.startswith("/requests/") and \
                    self.path.endswith("/cancel"):
                return self._cancel_action(user)
            self._html(404, pages.page("404", "<h2>Nothing here</h2>",
                                       user=user))
        except Exception:                                         # noqa: BLE001
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

    # -------------------------------------------------------------- pools
    def _act(self, user: dict, permission: str, verb: str, payload: dict,
             *, idem: str, back: str) -> None:
        """Queue one command, or record that it was refused.

        The person's name rides in the payload so the pass can write it
        into the sheet's notes. A refusal is a row too - `refused`, with
        the permission named - so the Requests page says what was asked
        and why nothing happened, instead of a 403 nobody remembers."""
        if not self.settings.web_mutations:
            return self._html(403, pages.page(
                "Disabled", "<h2>Actions are not switched on yet</h2>",
                user=user))
        from ..store import actions as store_actions
        from ..store.users import may

        payload = dict(payload, by=user["username"])
        if not may(user, permission):
            store_actions.record_refused(
                self.settings, verb=verb, payload=payload,
                requested_by=user["id"],
                reason=f"{user['username']} may not do this - permission "
                       f"{permission} is off")
            return self._redirect(f"{back}?said=refused")
        store_actions.enqueue(self.settings, verb=verb, payload=payload,
                              requested_by=user["id"], idem_key=idem)
        self._redirect(f"{back}?said=queued")

    def _login_accounts(self, user: dict, addresses: list) -> None:
        """"Log in selected" (C6). Only meaningful with manual login on:
        off, the pass logs accounts in by itself and the button would
        race it for the same rows."""
        if not self.settings.manual_login:
            return self._redirect("/?said=auto")
        chosen = [a.strip() for a in addresses if a and a.strip()]
        if not chosen:
            return self._redirect("/?said=none")
        return self._act(user, "may_login_accounts", "login_accounts",
                         {"addresses": chosen},
                         idem=self._minute_key(
                             user, "login", ",".join(sorted(chosen))),
                         back="/")

    def _minute_key(self, user: dict, verb: str, target: str) -> str:
        return f"{verb}:{target}:{user['id']}:{int(time.time()) // 60}"

    def _pool_post(self, user: dict, field: dict) -> None:
        from . import paste

        path = self.path
        if path == "/pools/gmail/preview":
            from ..store import validate

            known = read.known(self.settings, "gmail")
            rows = paste.accounts(field.get("pasted", ""))
            seller = (field.get("seller") or "").strip()
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
                rows, seller, user, idem=secrets.token_urlsafe(12)))
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
            rows = paste.proxies(field.get("pasted", ""))
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
            return self._act(user, "may_add_proxy", verb, {"name": name},
                             idem=self._minute_key(user, verb, name),
                             back="/pools/proxy")
        if path == "/pools/proxy/test-all":
            return self._act(user, "may_add_proxy", "test_all_proxies", {},
                             idem=self._minute_key(user, "test_all", "-"),
                             back="/pools/proxy")
        if path == "/pools/proxy/adopt":
            from ..store import state as store_state

            wanted = {k: (field.get(k) or "").strip()
                      for k in ("host", "port", "username")}
            # The password comes from what the pass kept, never the form.
            held = next((u for u in store_state.get(
                self.settings, "unlisted_proxies", []) or []
                if all(str(u.get(k, "")) == wanted[k] for k in wanted)), None)
            if held is None:
                return self._redirect("/pools/proxy?said=gone")
            return self._act(user, "may_add_proxy", "adopt_proxy", held,
                             idem=self._minute_key(
                                 user, "adopt", f"{held['host']}:{held['port']}"),
                             back="/pools/proxy")
        if path == "/pools/gpt/add":
            from ..store import validate

            row = {"address": (field.get("address") or "").strip(),
                   "password": field.get("password") or "",
                   "secret": (field.get("secret") or "").strip(),
                   "email_code_only": field.get("email_code") == "1"}
            try:
                validate.app_row(**row)
            except (validate.AccountError, validate.ProxyError) as exc:
                log.info("gpt add refused at the form: %s", exc)
                return self._redirect("/pools/gpt?said=bad")
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

    def _users_reset(self, user: dict) -> None:
        if (refused := self._admin_page(user)) is not None:
            code = 404 if not self.settings.web_user_admin else 403
            return self._html(code, refused)
        from ..store import users as store_users

        target = int(self.path.split("/")[2])
        row = store_users.get(self.settings, target)
        if row is None:
            return self._redirect("/users")
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
        self.send_header("Set-Cookie",
                         f"gf={token}; HttpOnly; SameSite=Lax; Path=/")
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
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, where: str) -> None:
        self.send_response(303)
        self.send_header("Location", where)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # Into the real log, at DEBUG: request lines are tracing, and the
        # file handler keeps DEBUG while the console shows INFO.
        log.debug("web: " + fmt, *args)


def _ticks(field: dict) -> dict:
    """The permission checkboxes as they came off the form: present and
    "1" means ticked, absent means not - HTML sends nothing for a clear
    box, which is why every column is read rather than only the sent."""
    from ..store.users import PERMISSION_COLUMNS

    return {c: field.get(c) == "1" for c in PERMISSION_COLUMNS}


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
