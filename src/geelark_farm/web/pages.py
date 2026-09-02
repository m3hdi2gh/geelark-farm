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

import time
from html import escape as esc

#: The console's shell - the "Direction A" the owner chose on the design
#: canvas (2026-09-01): a dark ops console, a rail of links on the left with
#: the stock counts beside them, monospace where digits line up. One
#: stylesheet for every page, no JavaScript, no static files.
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — geelark</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{{--bg:#0f1522;--rail:#0b101b;--panel:#151d2d;--panel2:#101827;--line:#232c3f;
 --line2:#1d2636;--ink:#d7dee9;--bright:#f2f6fc;--muted:#8a97ab;--dim:#6b7a90;
 --green:#58d68d;--green-bg:#10331f;--amber:#f0c064;--amber-bg:#3a2d10;
 --red:#e0654f;--red-bg:#4d2323;--blue:#7fb4ff;--blue-bg:#16324f;--violet:#c9b8f0;
 --violet-bg:#2c1f3d;--accent:#2563c4}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
 font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:14px}}
a{{color:var(--blue);text-decoration:none}} a:hover{{color:#a8ccff}}
.shell{{display:flex;min-height:100vh}}
nav{{width:216px;flex-shrink:0;background:var(--rail);
 border-right:1px solid var(--line2);padding:20px 12px;display:flex;
 flex-direction:column;gap:4px}}
nav .brand{{font-weight:600;font-size:15px;letter-spacing:.4px;color:#eef3fa;
 padding:4px 12px 20px}}
nav a{{display:flex;align-items:center;gap:10px;height:40px;padding:0 12px;
 border-radius:6px;color:#9aa7ba;font-size:14px}}
nav a:hover{{color:#fff;background:#141c2b}}
nav a.here{{background:#1a2334;color:#fff;font-weight:500}}
nav a .n{{margin-left:auto;font-family:'IBM Plex Mono',monospace;font-size:12px;
 color:var(--dim)}}
nav a .n.hot{{min-width:20px;height:20px;display:flex;align-items:center;
 justify-content:center;border-radius:10px;background:var(--amber-bg);
 color:var(--amber)}}
nav form{{margin-top:auto;display:flex;align-items:center;gap:10px;padding:12px;
 border-top:1px solid var(--line2)}}
nav form span{{color:#b9c4d4;font-size:13px}}
nav form button{{margin-left:auto;background:none;border:0;color:var(--dim);
 font-size:12px;cursor:pointer;font-family:inherit}}
main{{flex:1;min-width:0;padding:22px 28px;display:flex;flex-direction:column;gap:16px}}
h2{{font-size:19px;font-weight:600;color:var(--bright);margin:0}}
h3{{font-size:13px;font-weight:600;color:#c6d1e0;margin:0}}
.top{{display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
.top .status{{margin-left:auto;font-family:'IBM Plex Mono',monospace;
 font-size:12.5px;color:var(--muted)}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:8px;
 padding:16px 18px;display:flex;flex-direction:column;gap:10px}}
.panel.warn{{background:#1c1a15;
 border-color:#57431c}} .panel.warn h3{{color:var(--amber)}}
.panel.bad{{background:#201414;
 border-color:var(--red-bg)}} .panel.bad h3{{color:var(--red)}}
.grid2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}
.grid3{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}
.tiles{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}
.tile{{background:var(--panel);border:1px solid var(--line);border-radius:8px;
 padding:14px 18px}}
.tile .l{{font-size:12.5px;color:var(--muted);margin-bottom:6px}}
.tile b{{display:block;font-family:'IBM Plex Mono',monospace;font-size:30px;
 font-weight:500}}
table{{border-collapse:collapse;width:100%;font-family:'IBM Plex Mono',monospace;
 font-size:12.5px}}
th{{text-align:left;padding:0 8px 6px 0;font-weight:400;font-size:12px;
 color:var(--dim);border-bottom:1px solid var(--line)}}
td{{padding:6px 8px 6px 0;border-bottom:1px solid var(--line2);color:#b9c4d4;
 vertical-align:top}}
tr:last-child td{{border-bottom:0}}
.pills{{display:flex;border:1px solid #2c3a52;border-radius:6px;overflow:hidden}}
.pills a,.pills span{{padding:7px 14px;font-size:13px;color:#9aa7ba;
 font-family:'IBM Plex Sans',system-ui,sans-serif}}
.pills span,.pills a.here{{background:#1a2334;color:#fff}}
.chips{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.chips a,.chips span{{padding:4px 10px;border-radius:12px;font-size:12px;
 border:1px solid #2c3a52;color:#9aa7ba}}
.chips span,.chips a.here{{background:#1a2334;color:#fff;border-color:transparent}}
.badge{{display:inline-block;padding:1px 8px;border-radius:9px;font-size:11.5px;
 background:#1d2636;color:#9aa7ba;white-space:nowrap}}
.badge.ok,.badge.done,.badge.free,.badge.ready{{background:var(--green-bg);
 color:var(--green)}}
.badge.warn,.badge.queued,.badge.on_phone,.badge.in_use,.badge.claimed{{background:var(--amber-bg);
 color:var(--amber)}}
.badge.bad,.badge.failed,.badge.refused,.badge.dead{{background:var(--red-bg);
 color:var(--red)}}
.badge.info,.badge.running,.badge.panel{{background:var(--blue-bg);color:var(--blue)}}
.badge.manual{{background:var(--violet-bg);color:var(--violet)}}
.badge.attn{{background:#4b2a12;color:#f0a24a}}
.muted{{color:var(--muted)}} .dim{{color:var(--dim);font-size:12px}}
.mono{{font-family:'IBM Plex Mono',monospace}}
.err{{background:#2a1512;border:1px solid #57241c;color:#f0a094;padding:10px 14px;
 border-radius:6px;font-size:13px}}
.said{{background:#0f2b1a;border:1px solid #1e5b2a;color:#9be3b3;padding:10px 14px;
 border-radius:6px;font-size:13px}}
input,textarea,select{{background:var(--panel2);border:1px solid #2c3a52;
 border-radius:6px;color:var(--ink);padding:9px 12px;
 font-family:'IBM Plex Mono',monospace;font-size:12.5px}}
textarea{{width:100%;min-height:110px;line-height:1.7}}
input::placeholder,textarea::placeholder{{color:#55627a}}
button,.btn{{cursor:pointer;background:var(--accent);color:#fff;border:0;
 border-radius:6px;padding:9px 18px;
 font-family:'IBM Plex Sans',system-ui,sans-serif;font-size:13.5px;font-weight:600}}
button.quiet,.btn.quiet{{background:none;border:1px solid #2c3a52;color:#9db4d4;
 font-weight:400;padding:5px 10px;font-size:12px}}
button.quiet.warn{{border-color:#57431c;color:var(--amber)}}
button.quiet.bad{{border-color:var(--red-bg);color:var(--red)}}
form.inline{{display:inline}}
.row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
label{{display:flex;gap:8px;align-items:center;font-size:13px}}
p{{margin:0}}
</style>{refresh}</head><body><div class="shell">{header}
<main>{body}</main></div></body></html>"""

#: The rail, in the order the canvas fixed. (path, label, count-key). A
#: count-key names a number in `user["nav"]`; the Requests one is "hot"
#: (amber) when anything is pending.
_RAIL = (("/", "Dashboard", ""), ("/pools/gmail", "Gmail Pool", "gmail"),
         ("/pools/proxy", "Proxy Pool", "proxy"),
         ("/pools/gpt", "Gpt Pool", "app"), ("/requests", "Requests", "pending"),
         ("/events", "Events", ""), ("/users", "Users", ""))


def page(title: str, body: str, *, user: dict | None = None,
         refresh: int = 0, here: str = "") -> str:
    """`refresh` seconds of meta-refresh, when a page shows pending state
    that the next serve pass will change; zero (the default) means none.
    `here` is the rail entry to light."""
    header = ""
    if user is not None:
        counts = user.get("nav") or {}
        links = ['<nav><div class="brand">geelark farm</div>']
        for path, label, key in _RAIL:
            if path == "/events" and user.get("sees") != "all":
                continue
            if path == "/users" and not (user.get("role") == "admin"
                                         and user.get("user_admin")):
                continue
            n = ""
            if key and counts.get(key) is not None:
                hot = " hot" if key in ("pending", "app") and counts[key] \
                    else ""
                n = f'<span class="n{hot}">{int(counts[key])}</span>'
            lit = ' class="here"' if path == here else ""
            links.append(f'<a href="{path}"{lit}>{esc(label)}{n}</a>')
        links.append(f'<form method="post" action="/logout">'
                     f'<span>{esc(user["username"])}</span>'
                     f'<input type="hidden" name="csrf" '
                     f'value="{esc(user.get("csrf", ""))}">'
                     f'<button>Log out</button></form></nav>')
        header = "".join(links)
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


_DASH_SAID = {
    "queued": "Queued - the next pass (within ~30s) carries it out; watch "
              "Requests.",
    "refused": "You may not do that - ask an admin for the permission.",
    "off": "Actions are not switched on yet.",
    "auto": "Manual login is off: accounts log in on their own on the next "
            "pass, nothing to press.",
    "none": "Tick at least one account first.",
}

#: The Phones tab's status words as the dashboard's badge colours, and the
#: one word the dashboard says differently: a phone with the app and no
#: account is "warm" stock, which is what the keeper calls it.
_PHONE_CLASS = {"ready": "ready", "app_only": "warn", "building": "info",
                "incomplete": "attn"}
_PHONE_WORD = {"app_only": "warm"}


def _phone_word(status: str) -> str:
    return _PHONE_WORD.get(status, status or "?")


def _phone_badge(row: dict) -> str:
    if (row.get("state") or "") == "taken":
        return '<span class="badge manual">taken</span>'
    status = row.get("status") or ""
    return (f'<span class="badge {_PHONE_CLASS.get(status, "")}">'
            f'{esc(_phone_word(status))}</span>')


def _actor_bar(data: dict) -> str:
    pulse = data.get("pulse") or {}
    queue = data.get("queue") or {}
    bits = []
    if pulse:
        warm, target = pulse.get("warm", 0), pulse.get("target", 0)
        colour = "green" if warm >= target else "amber"
        bits.append(f'<span style="color:var(--{colour})">●</span> keeper '
                    f'{warm}/{target} warm')
    else:
        bits.append("keeper: no pass yet")
    bits.append(f'queue {int(queue.get("running") or 0)} running · '
                f'{int(queue.get("queued") or 0)} queued')
    if pulse.get("tripped"):
        bits.append('<span style="color:var(--red)">breaker open</span>')
    elif pulse:
        bits.append("breaker armed")
    if pulse.get("paused"):
        bits.append('<span style="color:var(--amber)">building paused</span>')
    if pulse.get("manual_login"):
        bits.append("manual login")
    if pulse.get("at"):
        bits.append(f"last pass {_ago(pulse['at'])}")
    return ' <span class="dim">·</span> '.join(bits)


def _ago(stamp: float) -> str:
    seconds = max(0, int(time.time() - float(stamp)))
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def dashboard(data: dict, user: dict, said: str = "",
              manual_login: bool = False) -> str:
    stock = data.get("stock") or {}
    gmail, proxy = stock.get("gmail", {}), stock.get("proxy", {})
    app = stock.get("app", {})
    cards = (
        f'<div class="tile"><div class="l">Gmail <a href="/pools/gmail" '
        f'style="float:right">open pool</a></div>'
        f'<b style="color:var(--green)">{gmail.get("free", 0)}</b>'
        f'<span class="muted">free — {gmail.get("on_phones", 0)} on phones · '
        f'{gmail.get("used", 0)} used</span></div>'
        f'<div class="tile"><div class="l">GPT accounts <a href="/pools/gpt" '
        f'style="float:right">open pool</a></div>'
        f'<b style="color:var(--amber)">{app.get("awaiting", 0)}</b>'
        f'<span class="muted">awaiting login — {app.get("panel", 0)} from '
        f'panel · {app.get("manual", 0)} manual</span></div>'
        f'<div class="tile"><div class="l">Proxies <a href="/pools/proxy" '
        f'style="float:right">open pool</a></div>'
        f'<b style="color:var(--green)">{proxy.get("free", 0)}</b>'
        f'<span class="muted">free — {proxy.get("on_phones", 0)} on phones · '
        f'{proxy.get("dead", 0)} dead</span></div>')

    phones = data.get("phones") or []
    counts = {}
    for r in phones:
        counts[r.get("status") or "?"] = counts.get(r.get("status") or "?", 0) + 1
    summary = " · ".join(f"{n} {_phone_word(k)}" for k, n in counts.items())
    can_change = _may(user, "may_change_proxy")
    lines = []
    for r in phones:
        action = ""
        if can_change and (r.get("status") or "") != "building":
            action = (f'<form method="post" class="inline" '
                      f'action="/phones/{esc(str(r["serial"]))}/proxy">'
                      f'{_csrf(user)}<button class="quiet">Change proxy'
                      f'</button></form>')
        account = esc(str(r.get("app_account") or "")) or \
            '<span class="dim">—</span>'
        lines.append(
            f"<tr><td>{esc(str(r['serial']))}</td><td>{_phone_badge(r)}</td>"
            f"<td>{esc(str(r.get('gmail') or ''))}</td><td>{account}</td>"
            f"<td>{esc(str(r.get('proxy_name') or ''))}</td>"
            f"<td>{action}</td></tr>")
    table = (f'<table><tr><th>serial</th><th>state</th><th>gmail</th>'
             f'<th>gpt account</th><th>proxy</th><th></th></tr>'
             f'{"".join(lines)}</table>' if lines else
             '<p class="muted">No phones yet.</p>')
    phones_panel = (
        f'<div class="panel"><div class="row"><h3>Phones</h3>'
        f'<span class="dim mono">{esc(summary)}</span></div>{table}'
        f'<p class="dim">keeper builds the shortfall in parallel — 3 taken '
        f'at once means 3 builds start together</p></div>')

    awaiting = data.get("awaiting") or []
    can_login = manual_login and _may(user, "may_login_accounts")
    items = []
    for a in awaiting:
        source = a.get("source") or "manual"
        who = ("panel" if source == "panel" else
               f'manual · {esc(a.get("added_by") or "sheet")}')
        tick = (f'<input type="checkbox" name="addresses" '
                f'value="{esc(a["address"])}">' if can_login else "")
        items.append(
            f'<label class="row" style="padding:8px 10px;border-radius:6px;'
            f'background:var(--panel2);border:1px solid var(--line2)">{tick}'
            f'<span class="mono" style="min-width:0;overflow:hidden;'
            f'text-overflow:ellipsis">{esc(a["address"])}</span>'
            f'<span class="dim" style="margin-left:auto">added '
            f'{_when(a.get("created_at"))}</span>'
            f'<span class="badge {"panel" if source == "panel" else "manual"}">'
            f'{who}</span></label>')
    if can_login:
        foot = ('<p class="dim">each ticked account boots one warm phone; '
                'they log in in parallel</p>'
                '<button>Log in selected</button>')
        login_panel = (f'<form method="post" action="/accounts/login" '
                       f'class="panel">{_csrf(user)}<div class="row">'
                       f'<h3>Awaiting login</h3><span class="dim mono">'
                       f'{len(awaiting)} accounts</span></div>'
                       f'{"".join(items) or "<p class=muted>Nothing waiting.</p>"}'
                       f'{foot}</form>')
    else:
        why = ("accounts log in on their own on the next pass"
               if not manual_login else
               "you may not log accounts in - ask an admin")
        login_panel = (f'<div class="panel"><div class="row"><h3>Awaiting '
                       f'login</h3><span class="dim mono">{len(awaiting)} '
                       f'accounts</span></div>'
                       f'{"".join(items) or "<p class=muted>Nothing waiting.</p>"}'
                       f'<p class="dim">{why}</p></div>')

    recent = data.get("recent") or []
    tail = " ".join(
        f'<span style="color:var(--blue)">{_when(e.get("at"))}</span> '
        f'<span>{esc(str(e.get("kind") or ""))} {esc(str(e.get("status") or ""))}'
        f'</span>' for e in recent)
    footer = (f'<div class="row dim mono" style="border-top:1px solid '
              f'var(--line2);padding-top:12px">{tail}'
              f'<a href="/events" style="margin-left:auto">all events</a>'
              f'</div>' if user.get("sees") == "all" else "")

    body = (f'<div class="top"><h2>Dashboard</h2><span class="status">'
            f'{_actor_bar(data)}</span></div>'
            + _said(said, _DASH_SAID)
            + f'<div class="grid3">{cards}</div>'
            + f'<div class="grid2" style="grid-template-columns:minmax(0,58fr) '
              f'minmax(0,42fr)">{phones_panel}{login_panel}</div>'
            + footer)
    busy = any((r.get("status") or "") == "building" for r in phones) or \
        int((data.get("queue") or {}).get("queued") or 0) > 0
    return page("Dashboard", body, user=user, here="/",
                refresh=30 if busy else 0)


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


# ------------------------------------------------------------- the pools
# The three pool pages of the console (C5). Reads come off the mirror;
# every button is a POST that lands in the actions queue and is carried
# out by the next serve pass against the sheet - the interim model, until
# the pools move into the store for good. Buttons render only when the
# mutation flag is on AND the person may do the thing; the pages
# themselves are shared stock and everyone signed in sees them.

_POOL_SAID = {
    "queued": "Queued - the next pass (within ~30s) carries it out; watch "
              "Requests.",
    "refused": "You may not do that - ask an admin for the permission.",
    "off": "Actions are not switched on yet.",
    "bad": "That account was refused at the form - check the address, the "
           "password and the secret.",
    "gone": "That exit is no longer in GeeLark's list - nothing to adopt.",
}


def _may(user: dict, permission: str) -> bool:
    from ..store.users import may

    return bool(user.get("mutations")) and may(user, permission)


def _csrf(user: dict) -> str:
    return (f'<input type="hidden" name="csrf" '
            f'value="{esc(user.get("csrf", ""))}">')


def _when(value) -> str:
    return esc(str(value or "")[:16])


def _said(said: str, table: dict) -> str:
    note = table.get(said, "")
    return f'<p class="said">{esc(note)}</p>' if note else ""


def _kind_2fa(row: dict) -> str:
    if row.get("has_totp"):
        return '<span class="badge ok">authenticator</span>'
    if row.get("has_recovery"):
        return '<span class="badge warn">recovery address</span>'
    if row.get("email_code_only"):
        return '<span class="badge warn">email code</span>'
    return '<span class="badge">password only</span>'


def gmail_pool_page(data: dict, user: dict, said: str = "") -> str:
    """Active = on a phone / queued; Used and Errored are the archives.
    Errored carries seller, purchase and failure dates and a plain box of
    addresses - the list the seller is asked to refund."""
    c = data["counts"]
    view = data["view"]
    pills = "".join(
        (f'<span>{v.capitalize()} · {n}</span>' if view == v else
         f'<a href="/pools/gmail?view={v}">{v.capitalize()} · {n}</a>')
        for v, n in {"active": c["queued"] + c["on_phone"],
                     "used": c["used"], "errored": c["errored"]}.items())
    body = (f'<div class="top"><h2>Gmail Pool</h2><div class="pills">{pills}'
            f'</div><span class="status">{c["queued"]} queued covers the '
            f'next {c["queued"]} builds</span></div>')
    body += _said(said, _POOL_SAID)

    if view == "used":
        rows = "".join(
            f"<tr><td>{esc(r['address'])}</td><td>{esc(r['serial'] or '')}"
            f"</td><td class=\"muted\">{esc(r['used_at'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['seller'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['note'] or '')}</td></tr>"
            for r in data["rows"])
        body += (f'<div class="panel"><h3>Used — {c["used"]}</h3><table>'
                 f'<tr><th>address</th><th>phone</th><th>used</th>'
                 f'<th>seller</th><th>note</th></tr>{rows}</table></div>')
        return page("Gmail Pool", body, user=user, here="/pools/gmail")

    if view == "errored":
        chosen = data.get("seller", "")
        chips = [(f'<span>all sellers · {c["errored"]}</span>' if not chosen
                  else f'<a href="/pools/gmail?view=errored">all sellers · '
                       f'{c["errored"]}</a>')]
        for s in data["sellers"]:
            name = s["seller"] or "(no seller)"
            if chosen and chosen.lower() == (s["seller"] or ""):
                chips.append(f'<span>{esc(name)} · {s["c"]}</span>')
            else:
                chips.append(f'<a href="/pools/gmail?view=errored&seller='
                             f'{esc(s["seller"] or "")}">{esc(name)} · '
                             f'{s["c"]}</a>')
        rows = "".join(
            f"<tr><td>{esc(r['address'])}</td>"
            f"<td class=\"muted\">{esc(r['seller'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['purchased_on'] or '')}</td>"
            f"<td class=\"muted\">{_when(r['updated_at'])}</td>"
            f"<td><span class=\"badge attn\">{esc(r['status'])}</span></td>"
            f"<td class=\"muted\">{esc(r['note'] or '')}</td></tr>"
            for r in data["rows"])
        addresses = "\n".join(r["address"] for r in data["rows"])
        body += (f'<div class="chips">{"".join(chips)}</div>'
                 f'<div class="panel"><table><tr><th>address</th>'
                 f'<th>seller</th><th>purchased</th><th>failed on</th>'
                 f'<th>reason</th><th>what happened</th></tr>{rows}</table>'
                 f'<p class="dim">an errored address never re-enters the '
                 f'pool - this list exists so the seller pays it back</p>'
                 f'</div>'
                 f'<div class="panel"><h3>Addresses for refund '
                 f'({len(data["rows"])})</h3>'
                 f'<textarea readonly>{esc(addresses)}</textarea>'
                 f'<p class="dim">select all, copy, paste to the seller</p>'
                 f'</div>')
        return page("Gmail Pool", body, user=user, here="/pools/gmail")

    if _may(user, "may_add_gmail"):
        body += (
            '<div class="panel"><h3>Add gmails</h3>'
            '<form method="post" action="/pools/gmail/preview">'
            f'{_csrf(user)}'
            '<textarea name="pasted" placeholder="paste straight from the '
            'seller\'s sheet - one account per line; tab, colon or comma '
            'all work"></textarea>'
            '<p class="dim">the address is found by its @, the secret by its '
            'shape, the password is what remains; nothing is added until '
            'you confirm the preview</p>'
            '<div class="row"><input name="seller" placeholder="seller '
            '(e.g. egypt)" size="18">'
            '<span class="dim">purchase date stamps automatically</span>'
            '<button>Preview</button></div></form></div>')

    def phone_state(r: dict) -> str:
        if r["status"] == "in_use":
            return '<span class="badge in_use">signing in</span>'
        return (f'<span class="badge ready">'
                f'{esc(r.get("phone_status") or "ready")}</span>')

    on_phone = "".join(
        f"<tr><td>{esc(r['address'])}</td><td>{esc(r['serial'] or '')}</td>"
        f"<td>{phone_state(r)}</td>"
        f"<td class=\"muted\">{_when(r['updated_at'])}</td>"
        f"<td class=\"muted\">{esc(r['seller'] or '')}</td></tr>"
        for r in data["on_phone"])
    queued = "".join(
        f"<tr><td>{esc(r['address'])}</td>"
        f"<td class=\"muted\">{esc(r['seller'] or '')}</td>"
        f"<td class=\"muted\">{esc(r['purchased_on'] or '')}</td>"
        f"<td>{_kind_2fa(r)}</td></tr>"
        for r in data["queued"])
    body += (f'<div class="panel"><h3>On a phone — {c["on_phone"]}</h3>'
             f'<table><tr><th>address</th><th>phone</th><th>state</th>'
             f'<th>since</th><th>seller</th></tr>{on_phone}</table></div>'
             f'<div class="panel"><h3>Queued — {c["queued"]}'
             f' <span class="dim">the keeper claims from the top</span></h3>'
             f'<table><tr><th>address</th><th>seller</th><th>purchased</th>'
             f'<th>2fa</th></tr>{queued}</table></div>')
    if data["broken"]:
        broken = "".join(
            f"<tr><td>{esc(r['address'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['error'])}</td></tr>"
            for r in data["broken"])
        body += (f'<div class="panel bad"><h3>Refused by validation — '
                 f'{len(data["broken"])}</h3><table>{broken}</table></div>')
    return page("Gmail Pool", body, user=user, here="/pools/gmail")


_PROXY_STATE = {"free": "free", "on a phone": "on_phone", "claimed": "claimed",
                "change ip": "attn", "dead": "dead", "": "free",
                "unused": "free", "imported": "info"}


def proxy_pool_page(data: dict, user: dict, said: str = "",
                    state: str = "", q: str = "") -> str:
    c = data["counts"]
    body = (f'<div class="top"><h2>Proxy Pool</h2>'
            f'<span class="mono muted">{c["all"]} rows — '
            f'{c.get("free", 0)} free · {c.get("on_phone", 0)} on phones · '
            f'{c.get("needs_new_ip", 0)} need a new IP · '
            f'{c.get("dead", 0)} dead</span>')
    if _may(user, "may_add_proxy"):
        body += (f'<form method="post" action="/pools/proxy/test-all" '
                 f'class="inline" style="margin-left:auto">{_csrf(user)}'
                 f'<button class="quiet">Test all now</button></form>')
    body += "</div>" + _said(said, _POOL_SAID)

    if _may(user, "may_add_proxy"):
        body += (
            '<div class="panel"><h3>Add proxies</h3>'
            '<form method="post" action="/pools/proxy/preview">'
            f'{_csrf(user)}'
            '<textarea name="pasted" placeholder="host:port:user:pass, one '
            'per line - or a name first, then the string"></textarea>'
            '<p class="dim">names are handed out in order (SX43, SX44 …) '
            'unless pasted; each is tested by the pass before it joins</p>'
            '<div class="row"><button>Preview</button></div></form></div>')

    panels = []
    if data["needs_new_ip"]:
        rows = "".join(
            f"<tr><td>{esc(r['name'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['host'] or '')} — {esc(r['note'] or '')}"
            f"</td><td>" + (
                f'<form method="post" action="/pools/proxy/free" '
                f'class="inline">{_csrf(user)}<input type="hidden" '
                f'name="name" value="{esc(r["name"] or "")}">'
                f'<button class="quiet warn">IP changed — mark free</button>'
                f'</form>' if _may(user, "may_add_proxy") else "") +
            "</td></tr>" for r in data["needs_new_ip"])
        panels.append(f'<div class="panel warn"><h3>Needs a new IP — '
                      f'{len(data["needs_new_ip"])}</h3><table>{rows}'
                      f'</table><p class="dim">change the IP in the '
                      f'vendor\'s panel first; marking free re-tests it '
                      f'before any build takes it</p></div>')
    if data["unlisted"]:
        rows = "".join(
            f"<tr><td class=\"mono\">{esc(u.get('host', ''))}:"
            f"{esc(u.get('port', ''))} ({esc(u.get('username', ''))})</td>"
            f"<td>" + (
                f'<form method="post" action="/pools/proxy/adopt" '
                f'class="inline">{_csrf(user)}'
                f'<input type="hidden" name="host" value="{esc(u.get("host", ""))}">'
                f'<input type="hidden" name="port" value="{esc(u.get("port", ""))}">'
                f'<input type="hidden" name="username" '
                f'value="{esc(u.get("username", ""))}">'
                f'<button class="quiet">Add to pool</button></form>'
                if _may(user, "may_add_proxy") else "") + "</td></tr>"
            for u in data["unlisted"])
        panels.append(f'<div class="panel"><h3>Held by GeeLark, not in the '
                      f'pool — {len(data["unlisted"])}</h3><table>{rows}'
                      f'</table><p class="dim">reported, never added on its '
                      f'own - which of them belong here is your call</p>'
                      f'</div>')
    if data["dead"]:
        rows = "".join(
            f"<tr><td>{esc(r['name'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['host'] or '')}:{esc(str(r['port'] or ''))}"
            f" — since {_when(r['updated_at'])}</td><td>" + (
                f'<form method="post" action="/pools/proxy/test" '
                f'class="inline">{_csrf(user)}<input type="hidden" '
                f'name="name" value="{esc(r["name"] or "")}">'
                f'<button class="quiet">Test again</button></form>'
                if _may(user, "may_add_proxy") else "") + "</td></tr>"
            for r in data["dead"])
        panels.append(f'<div class="panel bad"><h3>Dead — '
                      f'{len(data["dead"])}</h3><table>{rows}</table>'
                      f'<p class="dim">kept, never removed on its own - '
                      f'revive it at the vendor and test again</p></div>')
    if panels:
        body += "".join(panels)

    chips = [(f'<span>all · {c["all"]}</span>' if not state else
              f'<a href="/pools/proxy">all · {c["all"]}</a>')]
    for key, label in {"free": "free", "on_phone": "on a phone",
                       "claimed": "claimed", "needs_new_ip": "needs new IP",
                       "dead": "dead"}.items():
        n = c.get(key, 0)
        chips.append(f'<span>{label} · {n}</span>' if state == key else
                     f'<a href="/pools/proxy?state={key}">{label} · {n}</a>')
    shown = []
    for r in data["rows"]:
        word = (r["status"] or "").lower()
        key = _PROXY_STATE.get(word, "info")
        bucket = ("free" if key == "free" else "on_phone" if key == "on_phone"
                  else "claimed" if key == "claimed" else
                  "needs_new_ip" if key == "attn" else
                  "dead" if key == "dead" else "other")
        if state and bucket != state:
            continue
        hay = f"{r['name']} {r['host']} {r['serial']}".lower()
        if q and q.lower() not in hay:
            continue
        shown.append(r)
    rows = "".join(
        f"<tr><td>{esc(r['name'] or '')}</td>"
        f"<td class=\"muted\">{esc(r['host'] or '')}:"
        f"{esc(str(r['port'] or ''))}</td>"
        f"<td><span class=\"badge "
        f"{_PROXY_STATE.get((r['status'] or '').lower(), 'info')}\">"
        f"{esc(r['status'] or 'free')}</span></td>"
        f"<td class=\"muted\">{esc(r['last_exit_ip'] or '')}</td>"
        f"<td>{esc(r['serial'] or '')}</td>"
        f"<td class=\"muted\">{esc(str(r['times_used'] or 0))}</td>"
        f"<td class=\"muted\">{_when(r['updated_at'])}</td>"
        f"<td class=\"muted\">{esc(r['note'] or '')}</td>" + (
            f"<td><form method=\"post\" action=\"/pools/proxy/remove\" "
            f"class=\"inline\">{_csrf(user)}<input type=\"hidden\" "
            f"name=\"name\" value=\"{esc(r['name'] or '')}\">"
            f"<button class=\"quiet bad\">Remove</button></form></td>"
            if _may(user, "may_add_proxy") and
            (r["status"] or "").lower() in ("", "free", "unused", "dead",
                                            "change ip") else "<td></td>")
        + "</tr>" for r in shown)
    body += (f'<div class="panel"><div class="row"><h3>All proxies</h3>'
             f'<div class="chips">{"".join(chips)}</div>'
             f'<form method="get" action="/pools/proxy" class="inline" '
             f'style="margin-left:auto">'
             f'<input type="hidden" name="state" value="{esc(state)}">'
             f'<input name="q" value="{esc(q)}" placeholder="name, host or '
             f'phone" size="22"></form></div>'
             f'<table><tr><th>name</th><th>host</th><th>state</th><th>exit'
             f'</th><th>phone</th><th>uses</th><th>updated</th><th>note</th>'
             f'<th></th></tr>{rows}</table></div>')
    return page("Proxy Pool", body, user=user, here="/pools/proxy")


def gpt_pool_page(data: dict, user: dict, said: str = "") -> str:
    c = data["counts"]
    view = data["view"]
    active_n = c["awaiting"] + c["logging_in"] + c["needs_human"]
    pills = (f'<span>Active · {active_n}</span>'
             f'<a href="/pools/gpt?view=delivered">Delivered · '
             f'{c["delivered"]}</a>' if view == "active" else
             f'<a href="/pools/gpt">Active · {active_n}</a>'
             f'<span>Delivered · {c["delivered"]}</span>')
    body = f'<div class="top"><h2>Gpt Pool</h2><div class="pills">{pills}</div>'
    if view == "delivered":
        body += (f'<form method="get" action="/pools/gpt" class="inline" '
                 f'style="margin-left:auto"><input type="hidden" name="view" '
                 f'value="delivered"><input name="q" value="{esc(data["q"])}" '
                 f'placeholder="search an address or a phone" size="28">'
                 f'</form>')
    body += "</div>" + _said(said, _POOL_SAID)

    if view == "delivered":
        rows = "".join(
            f"<tr><td>{esc(r['address'])}</td>"
            f"<td><span class=\"badge {esc(r['source'])}\">{esc(r['source'])}"
            f"{(' · ' + esc(r['added_by_name'])) if r.get('added_by_name') else ''}"
            f"</span></td><td>{esc(r['serial'] or '')}</td>"
            f"<td class=\"muted\">{_when(r['updated_at'])}</td>"
            f"<td class=\"muted\">{esc(r['note'] or '')}</td></tr>"
            for r in data["rows"])
        nav = ""
        if data["page"] > 1:
            nav += (f'<a href="/pools/gpt?view=delivered&q={esc(data["q"])}'
                    f'&page={data["page"] - 1}">← newer</a> ')
        if data["more"]:
            nav += (f'<a href="/pools/gpt?view=delivered&q={esc(data["q"])}'
                    f'&page={data["page"] + 1}">older →</a>')
        body += (f'<div class="panel"><table><tr><th>address</th><th>source'
                 f'</th><th>phone</th><th>delivered</th><th>note</th></tr>'
                 f'{rows}</table><div class="row"><span class="dim">'
                 f'{c["delivered"]} delivered accounts - the panel pulls '
                 f'each one\'s fate from here</span>'
                 f'<span class="mono dim" style="margin-left:auto">{nav}'
                 f'</span></div></div>')
        return page("Gpt Pool", body, user=user, here="/pools/gpt")

    if _may(user, "may_add_gpt"):
        body += (
            '<div class="panel"><h3>Add an account by hand</h3>'
            '<form method="post" action="/pools/gpt/add">'
            f'{_csrf(user)}<div class="row">'
            '<input name="address" placeholder="email address" size="30">'
            '<input name="password" placeholder="password" size="16">'
            '<input name="secret" placeholder="2FA secret (optional)" '
            'size="30"><label><input type="checkbox" name="email_code" '
            'value="1"> email-code only</label><button>Add</button></div>'
            '<p class="dim">validated the way the sheet rows are - a bad '
            '2FA secret or a malformed address is refused here, not '
            'discovered on a phone</p></form></div>')

    def account_rows(rows: list, by: bool) -> str:
        out = []
        for r in rows:
            state = ('<span class="badge info">logging in — '
                     f'{esc(r["serial"] or "")}</span>'
                     if r["status"] == "in_use" else
                     '<span class="badge warn">awaiting login</span>')
            who = (f"{esc(r.get('added_by_name') or 'sheet')} · "
                   f"{_when(r['created_at'])}" if by else _when(r["created_at"]))
            out.append(f"<tr><td>{esc(r['address'])}</td>"
                       f"<td class=\"muted\">{who}</td>"
                       f"<td>{_kind_2fa(r)}</td><td>{state}</td></tr>")
        return "".join(out)

    body += (f'<div class="panel"><h3><span class="badge panel">panel</span> '
             f'From the customer panel — {len(data["panel"])}</h3>'
             f'<table><tr><th>address</th><th>received</th><th>2fa</th>'
             f'<th>state</th></tr>{account_rows(data["panel"], False)}'
             f'</table></div>'
             f'<div class="panel"><h3><span class="badge manual">manual'
             f'</span> Added by hand or from the sheet — '
             f'{len(data["manual"])}</h3>'
             f'<table><tr><th>address</th><th>added by</th><th>2fa</th>'
             f'<th>state</th></tr>{account_rows(data["manual"], True)}'
             f'</table></div>')
    if data["needs_human"]:
        rows = "".join(
            f"<tr><td>{esc(r['address'])}</td>"
            f"<td><span class=\"badge attn\">{esc(r['status'])}</span></td>"
            f"<td class=\"muted\">{esc(r['note'] or '')}</td><td>" + (
                f'<form method="post" action="/pools/gpt/offer" '
                f'class="inline">{_csrf(user)}<input type="hidden" '
                f'name="address" value="{esc(r["address"])}">'
                f'<button class="quiet warn">Offer again</button></form>'
                if _may(user, "may_add_gpt") else "") + "</td></tr>"
            for r in data["needs_human"])
        body += (f'<div class="panel warn"><h3>Needs a human — '
                 f'{len(data["needs_human"])}</h3><table>{rows}</table>'
                 f'</div>')
    if data["broken"]:
        broken = "".join(
            f"<tr><td>{esc(r['address'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['error'])}</td></tr>"
            for r in data["broken"])
        body += (f'<div class="panel bad"><h3>Refused by validation — '
                 f'{len(data["broken"])}</h3><table>{broken}</table></div>')
    return page("Gpt Pool", body, user=user, here="/pools/gpt")


# ----------------------------------------------------------- previews
# What a paste becomes before it is queued: one line per row with a
# verdict, and the good rows carried into the confirm form as the same
# tab-separated text - so the confirm re-reads exactly what was shown.

def _verdict_badge(row: dict) -> str:
    if row.get("duplicate"):
        return '<span class="badge bad">already in the pool — skipped</span>'
    if row.get("error"):
        return f'<span class="badge bad">{esc(row["error"])}</span>'
    return '<span class="badge ok">ok</span>'


def _second_factor(row: dict) -> str:
    if row.get("recovery"):
        return "recovery"
    return "authenticator" if row.get("secret") else "—"


def gmail_preview(rows: list[dict], seller: str, user: dict,
                  idem: str) -> str:
    good = [r for r in rows if not r.get("error") and not r.get("duplicate")]
    lines = "".join(
        f"<tr><td>{esc(r.get('address') or r.get('line', ''))}</td>"
        f"<td class=\"muted\">{'········' if r.get('password') else '—'}</td>"
        f"<td class=\"muted\">{_second_factor(r)}</td>"
        f"<td>{_verdict_badge(r)}</td></tr>" for r in rows)
    carried = "\n".join(
        f"{r['address']}\t{r['password']}\t{r.get('recovery') or r.get('secret') or ''}"
        for r in good)
    body = (f'<div class="top"><h2>Gmail Pool</h2><span class="status">'
            f'preview — nothing is added yet</span></div>'
            f'<div class="panel"><table><tr><th>address</th><th>password'
            f'</th><th>2fa</th><th>verdict</th></tr>{lines}</table></div>'
            f'<form method="post" action="/pools/gmail/add" class="panel">'
            f'{_csrf(user)}<input type="hidden" name="idem" value="{esc(idem)}">'
            f'<input type="hidden" name="seller" value="{esc(seller)}">'
            f'<textarea name="rows" hidden>{esc(carried)}</textarea>'
            f'<div class="row"><span class="dim">seller: '
            f'{esc(seller or "(none)")} · purchase date stamps automatically'
            f'</span><span style="margin-left:auto"></span>'
            f'<a class="btn quiet" href="/pools/gmail">Back</a>'
            + (f'<button>Add {len(good)} (skip {len(rows) - len(good)})'
               f'</button>' if good else
               '<span class="badge bad">nothing to add</span>')
            + '</div></form>')
    return page("Gmail Pool — preview", body, user=user, here="/pools/gmail")


def proxy_preview(rows: list[dict], user: dict, idem: str) -> str:
    good = [r for r in rows if not r.get("error") and not r.get("duplicate")]
    lines = "".join(
        f"<tr><td>{esc(r.get('name') or 'next SX')}</td>"
        f"<td class=\"muted\">{esc(r.get('raw') or r.get('line', ''))}</td>"
        f"<td>{_verdict_badge(r)}</td></tr>" for r in rows)
    carried = "\n".join(f"{r['name']}\t{r['raw']}" if r.get("name")
                        else r["raw"] for r in good)
    body = (f'<div class="top"><h2>Proxy Pool</h2><span class="status">'
            f'preview — each is tested by the pass before it joins</span>'
            f'</div>'
            f'<div class="panel"><table><tr><th>name</th><th>proxy</th>'
            f'<th>verdict</th></tr>{lines}</table></div>'
            f'<form method="post" action="/pools/proxy/add" class="panel">'
            f'{_csrf(user)}<input type="hidden" name="idem" value="{esc(idem)}">'
            f'<textarea name="rows" hidden>{esc(carried)}</textarea>'
            f'<div class="row"><span style="margin-left:auto"></span>'
            f'<a class="btn quiet" href="/pools/proxy">Back</a>'
            + (f'<button>Add {len(good)} (skip {len(rows) - len(good)})'
               f'</button>' if good else
               '<span class="badge bad">nothing to add</span>')
            + '</div></form>')
    return page("Proxy Pool — preview", body, user=user, here="/pools/proxy")
