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
    server = ThreadingHTTPServer(("127.0.0.1", settings.web_port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever,
                              name="web", daemon=True)
    thread.start()
    log.info("web ui listening on 127.0.0.1:%d (loopback only; reach it "
             "through an ssh tunnel)", server.server_address[1])
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
            if path == "/":
                scope = None if user["sees"] == "all" else user["id"]
                return self._html(200, pages.dashboard(
                    read.snapshot(self.settings, scope), user))
            if path == "/phones":
                scope = None if user["sees"] == "all" else user["id"]
                return self._html(200, pages.phones_page(
                    read.phones(self.settings, scope), user))
            if path == "/pools":
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                return self._html(200, pages.pools_page(
                    read.pools(self.settings), user))
            if path == "/events":
                if user["sees"] != "all":
                    return self._html(403, pages.forbidden(user))
                return self._html(200, pages.events_page(
                    read.events(self.settings), user))
            self._html(404, pages.page("۴۰۴", "<h2>این‌جا چیزی نیست</h2>",
                                       user=user))
        except Exception:                                         # noqa: BLE001
            # A handler that leaks a traceback leaks whatever was in it.
            log.exception("web: %s failed", self.path)
            self._html(500, pages.page("خطا", "<h2>چیزی خراب شد - در لاگ "
                                              "سرور ثبت شد</h2>"))

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            field = {k: v[0] for k, v in form.items()}
            if self.path == "/login":
                return self._login(field)
            if self.path == "/logout":
                token = self._cookie()
                with _lock:
                    _sessions.pop(token, None)
                return self._redirect("/login")
            self._html(404, pages.page("۴۰۴", "<h2>این‌جا چیزی نیست</h2>"))
        except Exception:                                         # noqa: BLE001
            log.exception("web: POST %s failed", self.path)
            self._html(500, pages.page("خطا", "<h2>چیزی خراب شد</h2>"))

    # -------------------------------------------------------------- login
    def _login(self, field: dict) -> None:
        username = (field.get("username") or "").strip()
        if _locked_out(username):
            return self._html(429, pages.login(
                "چند بار پشت‌سرهم اشتباه شد - چند دقیقه بعد دوباره امتحان کن"))
        from ..store.db import Store

        with Store(self.settings) as store:
            row = store.check_login(username, field.get("password") or "")
        if row is None:
            _note_failure(username)
            return self._html(200, pages.login(
                "نام کاربری یا رمز عبور درست نیست"))
        token = secrets.token_urlsafe(32)
        with _lock:
            _failures.pop(username, None)
            _sessions[token] = {"user": row,
                                "until": time.time() + SESSION_HOURS * 3600}
        self.send_response(303)
        self.send_header("Set-Cookie",
                         f"gf={token}; HttpOnly; SameSite=Lax; Path=/")
        self.send_header("Location", "/")
        self.end_headers()

    def _user(self) -> dict | None:
        token = self._cookie()
        with _lock:
            entry = _sessions.get(token)
            if entry is None or entry["until"] < time.time():
                _sessions.pop(token, None)
                return None
            return entry["user"]

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
