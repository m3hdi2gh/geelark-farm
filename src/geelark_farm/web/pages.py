"""HTML, rendered by hand, escaped by rule.

Server-rendered strings and nothing else: no template engine (a dependency
plus an injection surface), no JavaScript (nothing here needs it yet), no
static files (no path handling, no traversal to get wrong). Every dynamic
value goes through `esc` - the mirror carries text typed into a spreadsheet
by people, and a Note cell is exactly where a `<script>` would sit.

The values (statuses, addresses, serials) stay exactly as the sheet holds
them, so the page and the tab never disagree about a word.
"""

from __future__ import annotations

from html import escape as esc

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — geelark</title><style>
body{{font-family:system-ui,sans-serif;margin:0;background:#f4f6f8;color:#182230}}
header{{display:flex;gap:1.2rem;align-items:baseline;padding:.7rem 1.2rem;
 background:#182230;color:#e8edf2}}
header b{{font-size:1.05rem}}
header a{{color:#9fb3c8;text-decoration:none}}
header a:hover{{color:#fff}}
header form{{margin-inline-start:auto}}
main{{max-width:64rem;margin:1.2rem auto;padding:0 1rem}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem}}
th,td{{text-align:left;padding:.45rem .6rem;border-bottom:1px solid #e3e8ee}}
th{{background:#eef2f6;font-weight:600}}
.tiles{{display:flex;gap:.8rem;flex-wrap:wrap;margin:0 0 1.2rem}}
.tile{{background:#fff;border:1px solid #e3e8ee;border-radius:6px;
 padding:.7rem 1.1rem;min-width:7rem}}
.tile b{{display:block;font-size:1.5rem}}
.err{{background:#fdecea;color:#8c3220;padding:.6rem .9rem;border-radius:4px;
 margin:0 0 1rem}}
.said{{background:#e7f2e8;color:#1e5b2a;padding:.6rem .9rem;border-radius:4px;
 margin:0 0 1rem}}
.muted{{color:#6b7b90}}
.badge{{display:inline-block;padding:.1rem .5rem;border-radius:9px;
 font-size:.8rem;background:#e3e8ee;color:#3c4a5c}}
.badge.queued{{background:#fff3d6;color:#7a5a12}}
.badge.running{{background:#dceafd;color:#1d4f91}}
.badge.done{{background:#e7f2e8;color:#1e5b2a}}
.badge.failed,.badge.refused{{background:#fdecea;color:#8c3220}}
button{{cursor:pointer}}
</style>{refresh}</head><body>{header}<main>{body}</main></body></html>"""


def page(title: str, body: str, *, user: dict | None = None,
         refresh: int = 0) -> str:
    """`refresh` seconds of meta-refresh, when a page shows pending state
    that the next serve pass will change; zero (the default) means none."""
    header = ""
    if user is not None:
        links = ['<b>geelark</b>', '<a href="/">Dashboard</a>',
                 '<a href="/phones">Phones</a>',
                 '<a href="/requests">Requests</a>']
        if user.get("sees") == "all":
            links += ['<a href="/needs">Needs attention</a>',
                      '<a href="/pools">Pools</a>',
                      '<a href="/events">Events</a>']
        if user.get("role") == "admin" and user.get("user_admin"):
            links += ['<a href="/users">Users</a>']
        links += [f'<form method="post" action="/logout">'
                  f'<span class="muted">{esc(user["username"])}</span> '
                  f'<input type="hidden" name="csrf" '
                  f'value="{esc(user.get("csrf", ""))}">'
                  f'<button>Log out</button></form>']
        header = "<header>" + "".join(links) + "</header>"
    tag = (f'<meta http-equiv="refresh" content="{int(refresh)}">'
           if refresh else "")
    return _PAGE.format(title=esc(title), header=header, body=body,
                        refresh=tag)


def login(error: str = "") -> str:
    body = f'<p class="err">{esc(error)}</p>' if error else ""
    body += ('<h2>Sign in</h2><form method="post" action="/login">'
             '<p><input name="username" placeholder="username" autofocus>'
             '</p><p><input name="password" type="password" '
             'placeholder="password"></p>'
             '<p><button>Sign in</button></p></form>')
    return page("Sign in", body)


def dashboard(snap: dict, user: dict) -> str:
    tiles = []
    labels = (("ready", "Ready to deliver"), ("app_only", "App only"),
              ("building", "In build"), ("incomplete", "Not finished"))
    for key, label in labels:
        tiles.append(f'<div class="tile">{label}'
                     f'<b>{snap["phones"].get(key, 0)}</b></div>')
    for kind, label in (("gmail", "Free gmails"), ("proxy", "Free proxies"),
                        ("app", "Free accounts")):
        stock = snap["stock"].get(kind, {})
        extra = (f' <span class="muted">({stock.get("unusable", 0)} '
                 f'unusable)</span>' if stock.get("unusable") else "")
        tiles.append(f'<div class="tile">{label}'
                     f'<b>{stock.get("free", 0)}</b>{extra}</div>')
    body = f'<div class="tiles">{"".join(tiles)}</div>'
    last = snap.get("last_event")
    if last:
        body += (f'<p class="muted">Last event: {esc(str(last["kind"]))} '
                 f'— {esc(str(last["at"]))}</p>')
    body += ('<p class="muted">This page reads the database mirror, which '
             'every pass (~30s) refreshes; the sheet is still the '
             'authority.</p>')
    return page("Dashboard", body, user=user)


_APP_MARK = {True: "✓", False: "✗", None: "?"}


def phones_page(rows: list[dict], user: dict) -> str:
    head = ("<tr><th>Serial</th><th>Status</th><th>State</th><th>App</th>"
            "<th>Gmail</th><th>Account</th><th>Proxy</th><th>Note</th></tr>")
    lines = []
    for r in rows:
        lines.append(
            "<tr>"
            f"<td>{esc(str(r['serial']))}</td>"
            f"<td>{esc(str(r['status']))}</td>"
            f"<td>{esc(str(r['state']))}</td>"
            f"<td>{_APP_MARK.get(r['app_installed'], '?')}</td>"
            f"<td>{esc(str(r['gmail'] or ''))}</td>"
            f"<td>{esc(str(r['app_account'] or ''))}</td>"
            f"<td>{esc(str(r['proxy_name'] or ''))}</td>"
            f"<td>{esc(str(r['note'] or ''))}</td></tr>")
    body = (f"<h2>Phones ({len(rows)})</h2>"
            f"<table>{head}{''.join(lines)}</table>")
    return page("Phones", body, user=user)


def pools_page(data: dict, user: dict) -> str:
    body = ("<h2>Pools</h2><table>"
            "<tr><th>Tab</th><th>Status</th><th>Count</th></tr>")
    for r in data["counts"]:
        body += (f"<tr><td>{esc(r['kind'])}</td>"
                 f"<td>{esc(str(r['status']))}</td><td>{r['c']}</td></tr>")
    body += "</table>"
    if data["broken"]:
        body += ("<h3>Unusable rows</h3>"
                 "<table><tr><th>Tab</th><th>Which</th><th>Why</th></tr>")
        for r in data["broken"]:
            body += (f"<tr><td>{esc(r['kind'])}</td>"
                     f"<td>{esc(str(r['who']))}</td>"
                     f"<td>{esc(str(r['error']))}</td></tr>")
        body += "</table>"
    return page("Pools", body, user=user)


def events_page(rows: list[dict], user: dict) -> str:
    head = ("<tr><th>When</th><th>What</th><th>Run</th><th>Serial</th>"
            "<th>Status</th><th>Detail</th></tr>")
    lines = []
    for r in rows:
        run = f"{r['run_id']}/{r['build']}" if r["build"] else r["run_id"]
        lines.append(
            "<tr>"
            f"<td class=\"muted\">{esc(str(r['at'])[:19])}</td>"
            f"<td>{esc(r['kind'])}</td><td>{esc(run)}</td>"
            f"<td>{esc(str(r['serial']))}</td>"
            f"<td>{esc(str(r['status']))}</td>"
            f"<td>{esc(str(r['detail']))}</td></tr>")
    body = f"<h2>Events</h2><table>{head}{''.join(lines)}</table>"
    return page("Events", body, user=user)


def forbidden(user: dict) -> str:
    return page("No access",
                "<h2>This page is outside your visibility</h2>", user=user)


def needs_page(data: dict, user: dict, advice) -> str:
    """`advice` is failures.verdict, passed in rather than imported here:
    pages render, read decides, and the one module that may know the verdict
    table is the one assembling the data."""
    total = sum(len(v) for v in data.values())
    body = f"<h2>Needs attention ({total})</h2>"
    if not total:
        body += "<p class=\"muted\">Nothing is waiting on anyone.</p>"

    if data["orphaned"]:
        body += ("<h3>Held by a phone that no longer exists</h3>"
                 "<p class=\"muted\">A spent credential on a phone that "
                 "left the panel - until someone decides, it stays out of "
                 "the pool forever. Delivered, or free again? That is "
                 "exactly the judgement the program refuses to make.</p>"
                 "<table><tr><th>Tab</th><th>Which</th><th>Phone</th></tr>")
        for r in data["orphaned"]:
            body += (f"<tr><td>{esc(r['kind'])}</td>"
                     f"<td>{esc(str(r['who']))}</td>"
                     f"<td>{esc(str(r['serial']))}</td></tr>")
        body += "</table>"

    if data["flagged"]:
        body += ("<h3>Flagged - a run judged it and set it aside</h3>"
                 "<table><tr><th>Tab</th><th>Which</th><th>Status</th>"
                 "<th>Meaning</th></tr>")
        for r in data["flagged"]:
            said = advice(r["status"])
            body += (f"<tr><td>{esc(r['kind'])}</td>"
                     f"<td>{esc(str(r['who']))}</td>"
                     f"<td>{esc(str(r['status']))}</td>"
                     f"<td class=\"muted\">{esc(said)}</td></tr>")
        body += "</table>"

    if data["broken"]:
        body += ("<h3>Unusable - validation refused it</h3>"
                 "<table><tr><th>Tab</th><th>Which</th><th>Why</th></tr>")
        for r in data["broken"]:
            body += (f"<tr><td>{esc(r['kind'])}</td>"
                     f"<td>{esc(str(r['who']))}</td>"
                     f"<td>{esc(str(r['error']))}</td></tr>")
        body += "</table>"

    if data["given_up"]:
        body += ("<h3>Given-up phones - three failures</h3>"
                 "<p class=\"muted\">Clearing the Tries cell in the sheet "
                 "puts them back in the queue.</p>"
                 "<table><tr><th>Serial</th><th>Status</th><th>Tries</th>"
                 "<th>Note</th></tr>")
        for r in data["given_up"]:
            body += (f"<tr><td>{esc(str(r['serial']))}</td>"
                     f"<td>{esc(str(r['status']))}</td>"
                     f"<td>{r['tries']}</td>"
                     f"<td>{esc(str(r['note']))}</td></tr>")
        body += "</table>"
    return page("Needs attention", body, user=user)


#: What a `?said=` token means, spelled out where the person reads it.
#: An unknown token renders as nothing - the address bar is user input.
_SAID = {
    "queued": "Queued - the next pass (within ~30s) will run it.",
    "cancelled": "Cancelled - it never ran.",
    "too_late": "Too late - a pass had already taken it; see its row below.",
    "not_yours": "That request is not yours to cancel.",
}


def requests_page(rows: list[dict], user: dict, said: str = "") -> str:
    """The queue, newest first: what was asked, by whom, what became of it.

    Refreshes itself only while something is pending - a settled list
    sitting still is the signal that nothing is owed."""
    body = ""
    note = _SAID.get(said, "")
    if note:
        body += f'<p class="said">{esc(note)}</p>'
    head = ("<tr><th>#</th><th>Verb</th><th>Status</th><th>Result</th>"
            "<th>By</th><th>Asked</th><th></th></tr>")
    lines = []
    pending = False
    for r in rows:
        status = str(r["status"])
        if status in ("queued", "awaiting_confirm", "running"):
            pending = True
        undo = ""
        if status == "queued":
            undo = (f'<form method="post" action="/requests/{r["id"]}/cancel">'
                    f'<input type="hidden" name="csrf" '
                    f'value="{esc(user.get("csrf", ""))}">'
                    f'<button>Cancel</button></form>')
        lines.append(
            "<tr>"
            f"<td class=\"muted\">{r['id']}</td>"
            f"<td>{esc(str(r['verb']))}</td>"
            f"<td><span class=\"badge {esc(status)}\">{esc(status)}"
            f"</span></td>"
            f"<td>{esc(str(r['result'] or ''))}</td>"
            f"<td class=\"muted\">{esc(str(r['requested_by']))}</td>"
            f"<td class=\"muted\">{esc(str(r['requested_at'])[:19])}</td>"
            f"<td>{undo}</td></tr>")
    body += f"<h2>Requests ({len(rows)})</h2>"
    if not rows:
        body += "<p class=\"muted\">Nothing has been asked yet.</p>"
    else:
        body += f"<table>{head}{''.join(lines)}</table>"
    return page("Requests", body, user=user, refresh=10 if pending else 0)


# ------------------------------------------------------------------ users
_USERS_SAID = {
    "saved": "Saved. That person's open sessions were ended - the new "
             "settings apply when they sign in again.",
    "no_change": "Nothing changed.",
}


def _tick(name: str, on: bool, label: str, hint: str = "") -> str:
    hint_html = f' <span class="muted">— {esc(hint)}</span>' if hint else ""
    return (f'<label><input type="checkbox" name="{esc(name)}" value="1"'
            f'{" checked" if on else ""}> {esc(label)}{hint_html}</label>')


def _choice(name: str, options: tuple, current: str) -> str:
    return "".join(
        f'<label><input type="radio" name="{esc(name)}" value="{esc(o)}"'
        f'{" checked" if o == current else ""}> {esc(o)}</label> '
        for o in options)


def users_page(users: list[dict], selected: dict | None, user: dict,
               permissions: tuple, said: str = "",
               error: str = "") -> str:
    """Everyone who can sign in, and an editor for one of them.

    The editor's form is the whole permission model made visible: role,
    sight, six ticks. Nothing here shows or accepts a password - creating
    or resetting mints a one-time one that the next page shows exactly
    once."""
    csrf = esc(user.get("csrf", ""))
    body = ""
    if error:
        body += f'<p class="err">{esc(error)}</p>'
    note = _USERS_SAID.get(said, "")
    if note:
        body += f'<p class="said">{esc(note)}</p>'
    body += f"<h2>Users ({len(users)})</h2>"
    head = ("<tr><th>User</th><th>Role</th><th>Sees</th><th>May</th>"
            "<th>Last seen</th><th></th></tr>")
    lines = []
    for u in users:
        may = ("everything" if u["role"] == "admin" else
               ", ".join(label for col, label, _ in permissions
                         if u.get(col)) or "nothing yet")
        state = "" if u["active"] else ' <span class="badge">deactivated</span>'
        seen = str(u.get("last_login_at") or "never")[:16]
        lines.append(
            "<tr>"
            f"<td>{esc(u['username'])}{state}</td>"
            f"<td>{esc(u['role'])}</td><td>{esc(u['sees'])}</td>"
            f"<td class=\"muted\">{esc(may)}</td>"
            f"<td class=\"muted\">{esc(seen)}</td>"
            f"<td><a href=\"/users?id={u['id']}\">edit</a></td></tr>")
    body += f"<table>{head}{''.join(lines)}</table>"

    if selected is not None:
        u = selected
        ticks = "".join(f"<p>{_tick(col, bool(u.get(col)), label, hint)}</p>"
                        for col, label, hint in permissions)
        body += (
            f"<h3>Edit {esc(u['username'])}</h3>"
            f'<form method="post" action="/users/{u["id"]}">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f"<p>Role: {_choice('role', ('admin', 'operator'), u['role'])}</p>"
            f"<p>Sees: {_choice('sees', ('all', 'own'), u['sees'])}</p>"
            f"<p>{_tick('active', bool(u['active']), 'active')}</p>"
            f"<p class=\"muted\">An admin may do everything below and drive "
            f"the service; an operator may do exactly what is ticked.</p>"
            f"{ticks}"
            f"<p><button>Save</button></p></form>"
            f'<form method="post" action="/users/{u["id"]}/reset">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f"<p><button>Reset password</button> "
            f"<span class=\"muted\">shows a one-time password once and "
            f"signs them out everywhere</span></p></form>")

    ticks = "".join(f"<p>{_tick(col, False, label, hint)}</p>"
                    for col, label, hint in permissions)
    body += (
        "<h3>New user</h3>"
        '<form method="post" action="/users/new">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        '<p><input name="username" placeholder="username" '
        'autocomplete="off"></p>'
        f"<p>Role: {_choice('role', ('admin', 'operator'), 'operator')}</p>"
        f"<p>Sees: {_choice('sees', ('all', 'own'), 'own')}</p>"
        f"{ticks}"
        "<p><button>Create</button> <span class=\"muted\">a one-time "
        "password is shown once on the next page</span></p></form>")
    return page("Users", body, user=user)


def one_time_page(username: str, password: str, user: dict,
                  *, created: bool) -> str:
    """The password, exactly once. Not in a URL, not in the log, not on
    any later page - the person types it at their first sign-in and is
    then made to choose their own."""
    what = "created" if created else "password reset"
    body = (f"<h2>{esc(username)} — {esc(what)}</h2>"
            f"<p>Their one-time password, shown only now:</p>"
            f"<p><code style=\"font-size:1.3rem\">{esc(password)}</code></p>"
            f"<p class=\"muted\">They will be asked to choose their own the "
            f"first time they sign in. Every open session of theirs has "
            f"been ended.</p>"
            f"<p><a href=\"/users\">Back to users</a></p>")
    return page("One-time password", body, user=user)


def password_page(user: dict, error: str = "") -> str:
    csrf = esc(user.get("csrf", ""))
    body = f'<p class="err">{esc(error)}</p>' if error else ""
    body += ("<h2>Choose your password</h2>"
             "<p class=\"muted\">The one you signed in with was for one "
             "use. Pick your own - at least 8 characters.</p>"
             '<form method="post" action="/password">'
             f'<input type="hidden" name="csrf" value="{csrf}">'
             '<p><input name="password" type="password" '
             'placeholder="new password" autofocus></p>'
             '<p><input name="again" type="password" '
             'placeholder="the same, again"></p>'
             "<p><button>Save</button></p></form>")
    return page("Choose your password", body, user=user)
