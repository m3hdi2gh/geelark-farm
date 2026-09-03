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

import datetime
import time
from html import escape as esc

#: The console's shell - the "Direction A" the owner chose on the design
#: canvas (2026-09-01): a dark ops console, a rail of links on the left with
#: live counts, panels on a deep blue ground, IBM Plex for both faces. One
#: string, inlined on every page: no static files, nothing to cache-bust,
#: nothing to path-handle. Every colour is a token so a page never picks
#: its own; every control has a visible focus state.
#: The tab icon - the mockup's phone, inlined so no file is served.
_FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='"
    "0 0 24 24' fill='none' stroke='%234f8ef7' stroke-width='2'%3E%3Crect x"
    "='6' y='2.5' width='12' height='19' rx='2.5'/%3E%3Cline x1='10' y1='18"
    "' x2='14' y2='18'/%3E%3C/svg%3E"
)

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — geelark</title>
<link rel="icon" href="{favicon}">
<link rel="stylesheet"
 href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{{--bg:#0f1522;--rail:#0b101b;--panel:#151d2d;--panel2:#101827;--line:#232c3f;
 --line2:#1d2636;--ink:#d7dee9;--bright:#f2f6fc;--muted:#8a97ab;--dim:#6b7a90;
 --green:#58d68d;--green-bg:#10331f;--amber:#f0c064;--amber-bg:#3a2d10;
 --red:#e0654f;--red-bg:#4d2323;--blue:#7fb4ff;--blue-bg:#16324f;--violet:#c9b8f0;
 --violet-bg:#2c1f3d;--accent:#2563c4;--accent-hi:#2f74e0;--focus:#7fb4ff;
 --sans:'IBM Plex Sans',system-ui,sans-serif;
 --mono:'IBM Plex Mono',ui-monospace,monospace}}
*{{box-sizing:border-box}}
html{{color-scheme:dark}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
 font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased}}
a{{color:var(--blue);text-decoration:none}} a:hover{{color:#a8ccff}}
:focus-visible{{outline:2px solid var(--focus);outline-offset:2px;border-radius:4px}}
@media (prefers-reduced-motion:no-preference){{
 a,button,.btn,input,textarea,select,tr{{transition:background-color .12s,
  border-color .12s,color .12s}}}}
.shell{{display:flex;min-height:100vh}}
/* ---- the rail */
nav{{width:224px;flex-shrink:0;background:var(--rail);border-right:1px solid
 var(--line2);
 padding:22px 12px 16px;display:flex;flex-direction:column;gap:3px;position:sticky;
 top:0;height:100vh}}
nav .brand{{display:flex;align-items:center;gap:10px;font-weight:600;font-size:15px;
 letter-spacing:.4px;color:#eef3fa;padding:2px 12px 22px}}
nav .brand svg{{flex-shrink:0}}
nav a{{display:flex;align-items:center;gap:11px;height:40px;padding:0 12px;
 border-radius:7px;color:#9aa7ba;font-size:14px;position:relative}}
nav a svg{{width:17px;height:17px;flex-shrink:0;opacity:.85}}
nav a:hover{{color:#fff;background:#141c2b}}
nav a.here{{background:#1a2334;color:#fff;font-weight:500}}
nav
 a.here::before{{content:"";position:absolute;left:-12px;top:10px;bottom:10px;width:3px;
 border-radius:0 3px 3px 0;background:#4f8ef7}}
nav a .n{{margin-left:auto;font-family:var(--mono);font-size:12px;color:var(--dim)}}
nav a .n.hot{{min-width:20px;height:20px;display:flex;align-items:center;
 justify-content:center;border-radius:10px;background:var(--amber-bg);
 color:var(--amber);font-weight:500}}
nav form{{margin-top:auto;display:flex;align-items:center;gap:10px;padding:14px 8px 0;
 border-top:1px solid var(--line2)}}
nav form .av{{width:30px;height:30px;border-radius:15px;background:#24314a;display:flex;
 align-items:center;justify-content:center;font-size:13px;font-weight:600;color:#b9cae6;
 flex-shrink:0;text-transform:uppercase}}
nav form span.who{{color:#b9c4d4;font-size:13px;overflow:hidden;text-overflow:ellipsis;
 white-space:nowrap}}
nav form button{{margin-left:auto;background:none;border:0;color:var(--dim);
 font-size:12px;cursor:pointer;font-family:inherit;padding:6px 8px;border-radius:5px}}
nav form button:hover{{color:#fff;background:#141c2b}}
/* ---- the page */
main{{flex:1;min-width:0;padding:24px 32px 56px;display:flex;flex-direction:column;
 gap:16px}}
main.alone{{align-items:center;justify-content:center;padding:40px 20px}}
h2{{font-size:20px;font-weight:600;color:var(--bright);margin:0;letter-spacing:-.1px}}
h3{{font-size:13.5px;font-weight:600;color:#c6d1e0;margin:0;display:flex;align-items:center;
 gap:8px;flex-wrap:wrap}}
h3 .n{{font-family:var(--mono);font-size:12px;font-weight:400;color:var(--dim)}}
.top{{display:flex;align-items:center;gap:14px 18px;flex-wrap:wrap;min-height:38px}}
.top
 .status{{margin-left:auto;font-family:var(--mono);font-size:12.5px;color:var(--muted);
 display:flex;gap:8px;align-items:center;flex-wrap:wrap}}
.sub{{color:var(--muted);font-size:13.5px;max-width:78ch;margin-top:-8px}}
.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;
 padding:16px 18px;display:flex;flex-direction:column;gap:12px}}
.panel.warn{{background:#1c1a15;border-color:#57431c}} .panel.warn
 h3{{color:var(--amber)}}
.panel.bad{{background:#201414;border-color:var(--red-bg)}} .panel.bad
 h3{{color:var(--red)}}
.panel.ok{{border-color:#1e5b2a}} .panel.ok h3{{color:var(--green)}}
.grid2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}
.grid3{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}
.tiles{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}}
.tile{{background:var(--panel);border:1px solid var(--line);border-radius:10px;
 padding:14px 18px;display:flex;flex-direction:column;gap:4px}}
.tile
 .l{{font-size:12.5px;color:var(--muted);display:flex;align-items:baseline;gap:8px}}
.tile .l a{{margin-left:auto;font-size:12px}}
.tile b{{display:block;font-family:var(--mono);font-size:30px;font-weight:500;
 line-height:1.15;font-variant-numeric:tabular-nums}}
.tile .s{{font-size:12.5px;color:var(--muted)}}
.tile.warn{{border-color:#57431c}} .tile.bad{{border-color:var(--red-bg)}}
/* ---- tables */
.wrap{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-family:var(--mono);font-size:12.5px;
 font-variant-numeric:tabular-nums}}
th{{text-align:left;padding:0 10px 8px 0;font-weight:500;font-size:11.5px;
 letter-spacing:.4px;text-transform:uppercase;color:var(--dim);
 border-bottom:1px solid var(--line);white-space:nowrap}}
td{{padding:8px 10px 8px 0;border-bottom:1px solid var(--line2);color:#b9c4d4;
 vertical-align:top}}
tr:last-child td{{border-bottom:0}}
tbody tr:hover td,table tr:hover td{{background:rgba(127,180,255,.035)}}
td.act,td:has(> form.inline){{white-space:nowrap;text-align:right;padding-right:0}}
td .badge{{vertical-align:middle}}
/* ---- pills, chips, badges */
.pills{{display:flex;border:1px solid #2c3a52;border-radius:7px;overflow:hidden}}
.pills a,.pills span{{padding:7px
 14px;font-size:13px;color:#9aa7ba;font-family:var(--sans);
 border-right:1px solid #2c3a52}}
.pills a:last-child,.pills span:last-child{{border-right:0}}
.pills a:hover{{background:#141c2b;color:#fff}}
.pills span,.pills a.here{{background:#1a2334;color:#fff}}
.chips{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.chips a,.chips span{{padding:4px 11px;border-radius:12px;font-size:12px;
 border:1px solid #2c3a52;color:#9aa7ba}}
.chips a:hover{{color:#fff;border-color:#3d4f6e}}
.chips span,.chips a.here{{background:#1a2334;color:#fff;border-color:transparent}}
.badge{{display:inline-block;padding:1px 9px;border-radius:9px;font-size:11.5px;
 background:#1d2636;color:#9aa7ba;white-space:nowrap;font-family:var(--sans);
 font-weight:500;line-height:1.6}}
.badge.ok,.badge.done,.badge.free,.badge.ready{{background:var(--green-bg);
 color:var(--green)}}
.badge.warn,.badge.queued,.badge.on_phone,.badge.in_use,.badge.claimed{{
 background:var(--amber-bg);color:var(--amber)}}
.badge.bad,.badge.failed,.badge.refused,.badge.dead{{background:var(--red-bg);
 color:var(--red)}}
.badge.info,.badge.running,.badge.panel{{background:var(--blue-bg);color:var(--blue)}}
.badge.running::before{{content:"";display:inline-block;width:6px;height:6px;
 border-radius:3px;background:var(--blue);margin-right:6px;vertical-align:1px}}
.badge.manual{{background:var(--violet-bg);color:var(--violet)}}
.badge.attn{{background:#4b2a12;color:#f0a24a}}
.badge.cancelled{{text-decoration:line-through;opacity:.8}}
/* ---- text helpers */
.muted{{color:var(--muted)}} .dim{{color:var(--dim);font-size:12px}}
.mono{{font-family:var(--mono)}}
.empty{{color:var(--muted);text-align:center;padding:22px 10px;font-size:13.5px}}
.err{{background:#2a1512;border:1px solid #57241c;color:#f0a094;padding:10px 14px;
 border-radius:8px;font-size:13px}}
.err::before,.said::before{{font-family:var(--mono);margin-right:8px;font-weight:600}}
.err::before{{content:"!"}}
.said{{background:#0f2b1a;border:1px solid #1e5b2a;color:#9be3b3;padding:10px 14px;
 border-radius:8px;font-size:13px}}
.said::before{{content:"✓"}}
.hint{{color:var(--dim);font-size:12px;line-height:1.55}}
/* ---- forms */
input,textarea,select{{background:var(--panel2);border:1px solid #2c3a52;
 border-radius:7px;color:var(--ink);padding:8px 12px;font-family:var(--mono);
 font-size:12.5px;min-height:36px}}
input:hover,textarea:hover{{border-color:#3d4f6e}}
input:focus,textarea:focus,select:focus{{outline:none;border-color:var(--focus);
 box-shadow:0 0 0 3px rgba(127,180,255,.15)}}
textarea{{width:100%;min-height:110px;line-height:1.7;resize:vertical}}
input::placeholder,textarea::placeholder{{color:#55627a}}
input[type=checkbox],input[type=radio]{{min-height:0;width:16px;height:16px;
 accent-color:var(--accent-hi);margin:0}}
button,.btn{{cursor:pointer;background:var(--accent);color:#fff;border:0;
 border-radius:7px;padding:9px 18px;font-family:var(--sans);font-size:13.5px;
 font-weight:600;line-height:1.3;white-space:nowrap;display:inline-flex;
 align-items:center;gap:6px}}
button:hover,.btn:hover{{background:var(--accent-hi);color:#fff}}
button:active{{transform:translateY(1px)}}
button.quiet,.btn.quiet{{background:none;border:1px solid #2c3a52;color:#9db4d4;
 font-weight:500;padding:5px 11px;font-size:12px}}
button.quiet:hover,.btn.quiet:hover{{background:#141c2b;border-color:#3d4f6e;color:#fff}}
button.quiet.warn{{border-color:#57431c;color:var(--amber)}}
button.quiet.warn:hover{{background:var(--amber-bg)}}
button.quiet.bad{{border-color:#3a2626;color:#b98b85}}
button.quiet.bad:hover{{background:var(--red-bg);color:#ffb3a6}}
button.big{{height:44px;justify-content:center;width:100%;font-size:14px}}
form.inline{{display:inline}}
.row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.row .right{{margin-left:auto}}
label{{display:flex;gap:8px;align-items:center;font-size:13px}}
.seg{{display:inline-flex;border:1px solid #2c3a52;border-radius:7px;overflow:hidden}}
.seg label{{padding:6px 12px;gap:7px;cursor:pointer;border-right:1px solid #2c3a52;
 font-size:13px}}
.seg label:last-child{{border-right:0}}
.seg label:has(input:checked){{background:#1a2334;color:#fff}}
.ticks{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
.ticks label{{align-items:flex-start;padding:9px 12px;border:1px solid var(--line2);
 border-radius:8px;background:var(--panel2);line-height:1.4;cursor:pointer}}
.ticks label:has(input:checked){{border-color:#2c4a7a;background:#111b2e}}
.ticks label input{{margin-top:3px}}
.ticks label .muted{{display:block;font-size:12px}}
.card{{width:min(420px,100%);background:var(--panel);border:1px solid var(--line);
 border-radius:12px;padding:28px 28px 24px;display:flex;flex-direction:column;gap:14px}}
.card .brand{{display:flex;align-items:center;gap:10px;font-weight:600;font-size:15px;
 color:#eef3fa;letter-spacing:.4px;margin-bottom:6px}}
.card input{{width:100%}}
.card button{{width:100%;justify-content:center;height:42px;font-size:14px}}
.field{{display:flex;flex-direction:column;gap:5px;align-items:stretch}}
label.field{{align-items:stretch}}
.field span{{font-size:12.5px;color:var(--muted)}}
.code{{font-family:var(--mono);font-size:22px;letter-spacing:1px;background:var(--panel2);
 border:1px dashed #3d4f6e;border-radius:8px;padding:14px 18px;color:var(--bright);
 user-select:all;text-align:center}}
.story{{display:flex;flex-direction:column}}
.story .item{{display:grid;grid-template-columns:150px auto 1fr;gap:12px;
 align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--line2)}}
.story .item:last-child{{border-bottom:0}}
.subrow td{{color:var(--dim);background:rgba(255,255,255,.015)}}
.subrow td:first-child{{border-left:2px solid var(--line)}}
p{{margin:0}}
.alerts{{display:flex;flex-direction:column;gap:6px}}
.alert{{display:block;padding:9px 14px;border-radius:8px;font-size:13px;
 border:1px solid;color:var(--ink)}}
.alert::before{{font-family:var(--mono);font-weight:600;margin-right:8px}}
.alert.warn{{background:#1c1a15;border-color:#57431c}}
.alert.warn::before{{content:"!";color:var(--amber)}}
.alert.bad{{background:#201414;border-color:var(--red-bg)}}
.alert.bad::before{{content:"!";color:var(--red)}}
.alert:hover{{color:#fff}}
tr.hi td{{background:rgba(127,180,255,.10)}}
@media (max-width:900px){{
 .shell{{flex-direction:column}}
 nav{{width:auto;height:auto;position:static;flex-direction:row;flex-wrap:wrap;
  align-items:center;padding:10px 12px;gap:2px;border-right:0;
  border-bottom:1px solid var(--line2)}}
 nav .brand{{padding:4px 10px;width:100%}}
 nav a{{height:34px;padding:0 10px;font-size:13px}}
 nav a.here::before{{display:none}}
 nav form{{margin-top:0;margin-left:auto;padding:0;border:0}}
 main{{padding:18px 16px 40px}}
 .grid2,.grid3,.tiles,.ticks{{grid-template-columns:1fr}}
 .story .item{{grid-template-columns:1fr}}
}}
</style>{refresh}</head><body><div class="shell">{header}
<main{alone}>{body}</main></div></body></html>"""

#: The rail, in the order the canvas fixed. (path, label, count-key). A
#: count-key names a number in `user["nav"]`; the Requests one is "hot"
#: (amber) when anything is pending.
_RAIL = (("/", "Dashboard", ""), ("/pools/gmail", "Gmail Pool", "gmail"),
         ("/pools/proxy", "Proxy Pool", "proxy"),
         ("/pools/gpt", "Gpt Pool", "app"), ("/requests", "Requests", "pending"),
         ("/needs", "Needs attention", ""),
         ("/events", "Events", ""), ("/users", "Users", ""))

#: One line icon per rail entry - the mockup's, inlined so no file is
#: served. Stroke uses currentColor, so the active colour applies.
_ICON_TAG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
             'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
             'aria-hidden="true">{}</svg>')
_ICONS = {
    "/": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" '
         'width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" '
         'rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "/pools/gmail": '<path d="M4 6l8 6 8-6"/><rect x="3" y="5" width="18" '
                    'height="14" rx="2"/>',
    "/pools/proxy": '<circle cx="12" cy="12" r="9"/><path d="M3.5 9h17M3.5 15h17"/>'
                    '<path d="M12 3c-2.5 2.4-4 5.4-4 9s1.5 6.6 4 9c2.5-2.4 4-5.4 '
                    '4-9s-1.5-6.6-4-9z"/>',
    "/pools/gpt": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M9 '
                  '9.5c.4-1 1.5-1.7 3-1.7 1.8 0 3 .9 3 2.2 0 2.4-3 2-3 4"/>'
                  '<circle cx="12" cy="16.6" r="0.6" fill="currentColor"/>',
    "/requests": '<path d="M21 12H16l-2 4h-4l-2-4H3"/><path d="M5 5h14l2 7v6a1 1 '
                 '0 0 1-1 1H4a1 1 0 0 1-1-1v-6l2-7z"/>',
    "/needs": '<path d="M12 3l10 18H2z"/><path d="M12 10v5"/><circle cx="12" '
              'cy="18" r="0.6" fill="currentColor"/>',
    "/events": '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" '
               'y2="12"/><line x1="4" y1="18" x2="14" y2="18"/>',
    "/users": '<circle cx="9" cy="8" r="3.5"/><path d="M3.5 20c.7-3.2 2.9-5 '
              '5.5-5s4.8 1.8 5.5 5"/><circle cx="17" cy="9" r="2.5"/><path '
              'd="M15.5 15.3c2.6.2 4.3 1.8 5 4.7"/>',
}
_BRAND_ICON = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" '
               'stroke="#4f8ef7" stroke-width="2" aria-hidden="true"><rect x="6" '
               'y="2.5" width="12" height="19" rx="2.5"/><line x1="10" y1="18" '
               'x2="14" y2="18"/></svg>')


def page(title: str, body: str, *, user: dict | None = None,
         refresh: int = 0, here: str = "") -> str:
    """`refresh` seconds of meta-refresh, when a page shows pending state
    that the next serve pass will change; zero (the default) means none.
    `here` is the rail entry to light. Without a user there is no rail:
    the page stands alone, centred - the sign-in card."""
    header = ""
    if user is not None:
        counts = user.get("nav") or {}
        links = [f'<nav><div class="brand">{_BRAND_ICON}geelark farm</div>']
        for path, label, key in _RAIL:
            if path in ("/events", "/needs") and user.get("sees") != "all":
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
            icon = _ICON_TAG.format(_ICONS.get(path, ""))
            links.append(f'<a href="{path}"{lit}>{icon}{esc(label)}{n}</a>')
        name = str(user.get("username") or "?")
        links.append(f'<form method="post" action="/logout">'
                     f'<span class="av">{esc(name[:1])}</span>'
                     f'<span class="who">{esc(name)}</span>'
                     f'<input type="hidden" name="csrf" '
                     f'value="{esc(user.get("csrf", ""))}">'
                     f'<button>Log out</button></form></nav>')
        header = "".join(links)
    tag = (f'<meta http-equiv="refresh" content="{int(refresh)}">'
           if refresh else "")
    if user is not None:
        body = _alert_strip(user) + body
    return _PAGE.format(title=esc(title), header=header, body=body,
                        favicon=_FAVICON, refresh=tag, alone="" if user is not None
                        else ' class="alone"')


def _alert_strip(user: dict) -> str:
    """What is wrong right now, on every page, one line each. Read off
    the pulse the pass leaves (read.alerts); nothing when all is well."""
    found = (user.get("nav") or {}).get("alerts") or []
    if not found:
        return ""
    lines = "".join(
        f'<a class="alert {esc(a.get("level", "warn"))}" '
        f'href="{esc(a.get("href") or "/")}">{esc(a.get("text", ""))}</a>'
        for a in found)
    return f'<div class="alerts">{lines}</div>'


def login(error: str = "") -> str:
    body = f'<p class="err">{esc(error)}</p>' if error else ""
    body = (f'<div class="card"><div class="brand">{_BRAND_ICON}geelark farm'
            f'</div><h2>Sign in</h2>{body}'
            '<form method="post" action="/login" class="field" '
            'style="gap:12px">'
            '<label class="field"><span>Username</span>'
            '<input name="username" autofocus autocomplete="username"></label>'
            '<label class="field"><span>Password</span>'
            '<input name="password" type="password" '
            'autocomplete="current-password"></label>'
            '<button>Sign in</button></form>'
            '<p class="hint">Five wrong tries lock the name for a while. '
            'Your first sign-in with a one-time password asks you to choose '
            'your own.</p></div>')
    return page("Sign in", body)


_DASH_SAID = {
    "queued": "Queued - the next pass (within ~30s) carries it out; watch "
              "Requests.",
    "refused": "You may not do that - ask an admin for the permission.",
    "off": "Actions are not switched on yet.",
    "auto": "Manual login is off: accounts log in on their own on the next "
            "pass, nothing to press.",
    "none": "Tick at least one account first.",
    "already": "Already asked - that request is still pending:",
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


def _ago(stamp) -> str:
    """"14m ago", off a unix stamp or any timestamp the store hands back."""
    if not isinstance(stamp, (int, float)):
        moment = _as_dt(stamp)
        if moment is None:
            return ""
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        stamp = moment.timestamp()
    seconds = max(0, int(time.time() - float(stamp)))
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{seconds // 60}m ago"
    if seconds < 172800:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


#: The zone the clocks are shown in; `set_zone` is called once by app.start.
_ZONE = datetime.timezone(datetime.timedelta(hours=3, minutes=30), "Tehran")


def set_zone(name: str) -> None:
    """Use an IANA zone for every clock on every page. A machine without
    the zone database keeps the fixed Tehran offset rather than failing."""
    global _ZONE
    try:
        from zoneinfo import ZoneInfo

        _ZONE = ZoneInfo(name)
    except Exception as exc:                                      # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(
            "zone %r is not available (%s); clocks show Tehran +03:30", name, exc)


def _moment(value) -> datetime.datetime | None:
    moment = _as_dt(value)
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(_ZONE)


def _when(value) -> str:
    """A stamp the way a person says it: 'today 17:50', 'yesterday 02:40',
    'Sep 1 09:12', and the year only when it is not this one."""
    moment = _moment(value)
    if moment is None:
        return esc(str(value or "")[:16])
    today = datetime.datetime.now(_ZONE).date()
    day = moment.date()
    if day == today:
        return f"today {moment:%H:%M}"
    if (today - day).days == 1:
        return f"yesterday {moment:%H:%M}"
    if day.year == today.year:
        return f"{moment:%b} {moment.day} {moment:%H:%M}"
    return f"{moment:%Y-%m-%d %H:%M}"


def _clock(value) -> str:
    """HH:MM:SS in the owner's zone (a bare string passes through)."""
    moment = _moment(value)
    if moment is None:
        text = str(value or "")
        return esc(text[11:19] if len(text) >= 19 else text[:19])
    return f"{moment:%H:%M:%S}"


def _day(value) -> str:
    moment = _moment(value)
    if moment is None:
        return esc(str(value or "")[:10])
    today = datetime.datetime.now(_ZONE).date()
    if moment.date() == today:
        return "today"
    if (today - moment.date()).days == 1:
        return "yesterday"
    return f"{moment:%Y-%m-%d}"


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
            f'<label style="display:grid;grid-template-columns:'
            f'{"auto " if tick else ""}minmax(0,1fr) auto;gap:4px 10px;'
            f'align-items:center;padding:9px 10px;border-radius:8px;'
            f'background:var(--panel2);border:1px solid var(--line2);'
            f'cursor:{"pointer" if tick else "default"}">{tick}'
            f'<span class="mono" style="min-width:0;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap">{esc(a["address"])}'
            f'</span>'
            f'<span class="badge {"panel" if source == "panel" else "manual"}">'
            f'{who}</span>'
            f'<span class="dim" style="grid-column:{2 if tick else 1}/-1">'
            f'added {_when(a.get("created_at"))}</span></label>')
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
    body = (f'<div class="top"><h2>Phones</h2><span class="status">'
            f'{len(rows)} in the tab</span></div>'
            f'<div class="panel wrap"><table>{head}{"".join(lines)}</table>'
            + ('' if rows else '<p class="empty">No phones yet.</p>')
            + '</div>')
    return page("Phones", body, user=user, here="/")


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


def forbidden(user: dict) -> str:
    return page("No access",
                '<div class="top"><h2>This page is outside your visibility'
                '</h2></div><p class="sub">Your account sees only its own '
                'phones and requests. An admin can widen that on the Users '
                'page.</p><p><a class="btn quiet" href="/">Back to the '
                'dashboard</a></p>', user=user)


def needs_page(data: dict, user: dict, advice) -> str:
    """`advice` is failures.verdict, passed in rather than imported here:
    pages render, read decides, and the one module that may know the verdict
    table is the one assembling the data."""
    total = sum(len(v) for v in data.values())
    body = (f'<div class="top"><h2>Needs attention</h2><span class="status">'
            f'{total} waiting on a person</span></div>'
            f'<p class="sub">What the program refuses to decide on its own. '
            f'Each block says what it is and where the fix lives.</p>')
    if not total:
        body += ('<div class="panel ok"><p class="empty">Nothing is waiting '
                 'on anyone.</p></div>')

    if data["orphaned"]:
        rows = "".join(
            f"<tr><td>{esc(r['kind'])}</td><td>{esc(str(r['who']))}</td>"
            f"<td>{_serial_link(r['serial'])}</td></tr>"
            for r in data["orphaned"])
        body += (f'<div class="panel warn"><h3>Held by a phone that no longer '
                 f'exists <span class="n">{len(data["orphaned"])}</span></h3>'
                 f'<p class="hint">A spent credential on a phone that left '
                 f'the panel. Delivered, or free again? That judgement is '
                 f'yours: set the row\'s status in the sheet.</p>'
                 f'<table><tr><th>tab</th><th>which</th><th>phone</th></tr>'
                 f'{rows}</table></div>')

    if data["flagged"]:
        rows = "".join(
            f"<tr><td>{esc(r['kind'])}</td><td>{esc(str(r['who']))}</td>"
            f"<td><span class=\"badge attn\">{esc(str(r['status']))}</span>"
            f"</td><td class=\"muted\">{esc(advice(r['status']))}</td></tr>"
            for r in data["flagged"])
        body += (f'<div class="panel warn"><h3>Set aside by a run '
                 f'<span class="n">{len(data["flagged"])}</span></h3>'
                 f'<p class="hint">A run judged these and put them out of '
                 f'the pool. Fix the cause, then Offer again from the pool '
                 f'page.</p><table><tr><th>tab</th><th>which</th>'
                 f'<th>status</th><th>meaning</th></tr>{rows}</table></div>')

    if data["broken"]:
        rows = "".join(
            f"<tr><td>{esc(r['kind'])}</td><td>{esc(str(r['who']))}</td>"
            f"<td class=\"muted\">{esc(str(r['error']))}</td></tr>"
            for r in data["broken"])
        body += (f'<div class="panel bad"><h3>Refused by validation '
                 f'<span class="n">{len(data["broken"])}</span></h3>'
                 f'<p class="hint">These look free in the sheet and are '
                 f'nothing: fix the cell or delete the row.</p>'
                 f'<table><tr><th>tab</th><th>which</th><th>why</th></tr>'
                 f'{rows}</table></div>')

    if data["given_up"]:
        rows = "".join(
            f"<tr><td>{_serial_link(r['serial'])}</td>"
            f"<td><span class=\"badge attn\">{esc(str(r['status']))}</span>"
            f"</td><td>{r['tries']}</td>"
            f"<td class=\"muted\">{esc(str(r['note']))}</td></tr>"
            for r in data["given_up"])
        body += (f'<div class="panel warn"><h3>Given-up phones '
                 f'<span class="n">{len(data["given_up"])}</span></h3>'
                 f'<p class="hint">Three failed logins each. Clearing the '
                 f'Tries cell in the sheet puts a phone back in the queue.</p>'
                 f'<table><tr><th>serial</th><th>status</th><th>tries</th>'
                 f'<th>note</th></tr>{rows}</table></div>')
    return page("Needs attention", body, user=user, here="/needs")


#: What a `?said=` token means, spelled out where the person reads it.
#: An unknown token renders as nothing - the address bar is user input.
_SAID = {
    "queued": "Queued - the next pass (within ~30s) will run it.",
    "cancelled": "Cancelled - it never ran.",
    "too_late": "Too late - a pass had already taken it; see its row below.",
    "not_yours": "That request is not yours to touch.",
    "not_failed": "Only a failed request can be retried.",
    "refused": "You may not do that - ask an admin for the permission.",
    "already": "Already asked - that request is still pending:",
}

#: The pills above the list, in order. "" is everything.
REQUEST_VIEWS = ("", "running", "queued", "failed")

_PENDING = ("queued", "awaiting_confirm", "running")


def _local(address: str) -> str:
    return str(address or "").split("@")[0]


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def describe(verb: str, payload: dict) -> tuple[str, str]:
    """A command in the words a person would say it: (head, aside).

    The head is what was asked; the aside is who or what it was about,
    shown dimmer. Every verb the buttons queue has a line here; anything
    else reads as its verb, which is still better than a JSON blob."""
    p = payload or {}
    rows = p.get("rows") or []
    if verb == "login_accounts":
        who = [_local(a) for a in (p.get("addresses") or [])]
        return _plural(len(who), "account").replace(
            str(len(who)), f"Log in {len(who)}", 1), ", ".join(who)
    if verb == "change_proxy":
        return f"Change proxy on {p.get('serial', '?')}", ""
    if verb == "stop_phone":
        return f"Stop phone {p.get('serial', '?')}", ""
    if verb == "add_gmails":
        seller = p.get("seller") or ""
        return (f"Add {_plural(len(rows), 'gmail')}",
                f"seller {seller}" if seller else "")
    if verb == "add_proxies":
        return f"Add {_plural(len(rows), 'proxy', 'proxies')}", ""
    if verb == "add_gpt":
        return "Add GPT account", ", ".join(r.get("address", "")
                                           for r in rows)
    if verb == "adopt_proxy":
        return "Adopt proxy", f"{p.get('host', '')}:{p.get('port', '')}"
    if verb == "offer_again":
        return "Offer again", p.get("address", "")
    if verb == "mark_proxy_free":
        return f"Mark {p.get('name', '?')} free", "IP changed at the vendor"
    if verb == "test_proxy":
        return f"Test {p.get('name', '?')}", ""
    if verb == "test_all_proxies":
        return "Test all proxies", ""
    if verb == "remove_proxy":
        return f"Remove {p.get('name', '?')}", "from the pool"
    if verb == "control":
        return str(p.get("what") or "Control"), ""
    return verb.replace("_", " ").capitalize(), ""


def _targets(verb: str, payload: dict, detail: dict | None = None) -> dict:
    """What a command holds while it runs: {thing: kind}. Two commands
    that share one are serialised by the pass, and the queued one says
    which row it waits for. The phones a login was paired with live in
    the detail, not the payload - so both are read."""
    p = dict(payload or {})
    if isinstance(detail, dict) and detail.get("phones"):
        p["phones"] = detail["phones"]
    held = {}
    if p.get("serial"):
        held[str(p["serial"])] = "phone"
    for a in p.get("addresses") or []:
        held[str(a).lower()] = "account"
    if p.get("address"):
        held[str(p["address"]).lower()] = "account"
    if p.get("name"):
        held[str(p["name"])] = "exit"
    for ph in (p.get("phones") or []):
        held[str(ph.get("serial"))] = "phone"
    return held


def _waits_for(row: dict, rows: list[dict]) -> str:
    """For a queued row: the earlier pending row holding the same thing."""
    mine = _targets(row["verb"], row.get("payload") or {}, row.get("detail"))
    if not mine:
        return ""
    for other in rows:
        if other is row or other["status"] not in _PENDING:
            continue
        # A running row holds its things whatever its number; among the
        # queued, the earlier number goes first.
        if other["status"] != "running" and other["id"] >= row["id"]:
            continue
        theirs = _targets(other["verb"], other.get("payload") or {},
                          other.get("detail"))
        shared = [t for t in mine if t in theirs]
        if shared:
            kind = mine[shared[0]]
            return (f"waits for #{other['id']} to release the {kind} - "
                    f"same {kind}, one at a time")
    return ""


def _as_dt(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _span(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def _took(row: dict) -> str:
    """How long it took, or has been running."""
    started = _as_dt(row.get("executed_at"))
    if started is None:
        return ""
    ended = _as_dt(row.get("finished_at"))
    if ended is None and row.get("status") == "running":
        now = datetime.datetime.now(started.tzinfo)
        return _span((now - started).total_seconds())
    if ended is None:
        return ""
    return _span((ended - started).total_seconds())


def requests_page(rows: list[dict], user: dict, said: str = "", *,
                  counts: dict | None = None, view: str = "",
                  mine: bool = False) -> str:
    """The queue, newest first: what was asked, in words, by whom, what
    became of it - and under a command that works several phones, one
    line per phone. Refreshes itself only while something is pending."""
    counts = counts or {}
    body = _said(said, _SAID)
    can_stop = _may(user, "may_login_accounts")
    is_admin = user.get("role") == "admin"
    keep = "&mine=1" if mine else ""
    pills = []
    for name in REQUEST_VIEWS:
        label = name or "all"
        n = (sum(counts.values()) if not name else counts.get(name, 0))
        href = f"/requests?view={name}{keep}" if name else f"/requests?{keep[1:]}"
        pills.append(f'<span>{label} · {n}</span>' if name == view else
                     f'<a href="{href}">{label} · {n}</a>')
    top = f'<div class="pills">{"".join(pills)}</div>'
    if is_admin:
        flip = f"/requests?view={view}" + ("" if mine else "&mine=1")
        top += (f'<a class="btn quiet" href="{flip}">'
                f'{"everyone" if mine else "mine only"}</a>')
    head = ("<tr><th>#</th><th>what</th><th>by</th><th>asked</th>"
            "<th>state</th><th>result / progress</th><th></th></tr>")
    lines = []
    pending = False
    for r in rows:
        status = str(r["status"])
        if status in _PENDING:
            pending = True
        head_text, aside = describe(str(r["verb"]), r.get("payload") or {})
        what = esc(head_text) + (f' <span class="dim">— {esc(aside)}</span>'
                                 if aside else "")
        action = ""
        if status == "queued":
            action = (f'<form method="post" class="inline" '
                      f'action="/requests/{r["id"]}/cancel">{_csrf(user)}'
                      f'<button class="quiet">Cancel</button></form>')
        elif status == "failed":
            action = (f'<form method="post" class="inline" '
                      f'action="/requests/{r["id"]}/retry">{_csrf(user)}'
                      f'<button class="quiet">Retry</button></form>')
        said_what = str(r.get("result") or "")
        if status == "queued" and not said_what:
            said_what = _waits_for(r, rows) or "waiting for the next pass"
        took = _took(r)
        result = esc(said_what) + (f' <span class="dim">— {took}</span>'
                                   if took else "")
        lines.append(
            f'<tr><td class="muted">{r["id"]}</td><td>{what}</td>'
            f'<td class="muted">{esc(str(r["requested_by"]))}</td>'
            f'<td class="muted">{_clock(r["requested_at"])}</td>'
            f'<td><span class="badge {esc(status)}">{esc(status)}</span></td>'
            f'<td>{result}</td><td>{action}</td></tr>')
        detail = r.get("detail") or {}
        for ph in (detail.get("phones") or []) if isinstance(detail, dict) \
                else []:
            stop = ""
            if status == "running" and can_stop and ph.get("ok") is None:
                stop = (f'<form method="post" class="inline" '
                        f'action="/phones/{esc(str(ph.get("serial")))}/stop">'
                        f'{_csrf(user)}<button class="quiet warn">Stop this '
                        f'one</button></form>')
            word = ("is ready" if ph.get("ok") else
                    f"failed: {ph.get('status')}" if ph.get("ok") is False
                    else str(ph.get("status") or "working"))
            lines.append(
                f'<tr><td></td><td colspan="4" class="mono dim">↳ '
                f'{esc(str(ph.get("serial")))} — '
                f'{esc(str(ph.get("account") or ""))}</td>'
                f'<td class="dim">{esc(word)}'
                + (f' — {_span(ph.get("seconds"))}' if ph.get("seconds")
                   else "")
                + f'</td><td>{stop}</td></tr>')
    body = (f'<div class="top"><h2>Requests</h2>{top}</div>' + body)
    if not rows:
        body += '<p class="muted">Nothing has been asked yet.</p>'
    else:
        body += (f'<div class="panel"><table>{head}{"".join(lines)}</table>'
                 f'<p class="dim">every command anyone gives lands here - '
                 f'including the instant ones - and stays as the record</p>'
                 f'</div>')
    return page("Requests", body, user=user, here="/requests",
                refresh=10 if pending else 0)


# ------------------------------------------------------------------ users
_USERS_SAID = {
    "saved": "Saved. That person's open sessions were ended - the new "
             "settings apply when they sign in again.",
    "no_change": "Nothing changed.",
}


def _tick(name: str, on: bool, label: str, hint: str = "") -> str:
    hint_html = f'<span class="muted">{esc(hint)}</span>' if hint else ""
    return (f'<label><input type="checkbox" name="{esc(name)}" value="1"'
            f'{" checked" if on else ""}><span>{esc(label)}{hint_html}</span>'
            f'</label>')


def _choice(name: str, options: tuple, current: str) -> str:
    return '<span class="seg">' + "".join(
        f'<label><input type="radio" name="{esc(name)}" value="{esc(o)}"'
        f'{" checked" if o == current else ""}> {esc(o)}</label>'
        for o in options) + "</span>"


def users_page(users: list[dict], selected: dict | None, user: dict,
               permissions: tuple, said: str = "",
               error: str = "") -> str:
    """Everyone who can sign in, and an editor for one of them.

    The editor's form is the whole permission model made visible: role,
    sight, six ticks. Nothing here shows or accepts a password - creating
    or resetting mints a one-time one that the next page shows exactly
    once."""
    csrf = esc(user.get("csrf", ""))
    body = (f'<div class="top"><h2>Users</h2><span class="status">'
            f'{len(users)} can sign in</span></div>')
    if error:
        body += f'<p class="err">{esc(error)}</p>'
    note = _USERS_SAID.get(said, "")
    if note:
        body += f'<p class="said">{esc(note)}</p>'
    head = ("<tr><th>user</th><th>role</th><th>sees</th><th>may</th>"
            "<th>last seen</th><th></th></tr>")
    lines = []
    for u in users:
        may = ("everything" if u["role"] == "admin" else
               ", ".join(label for col, label, _ in permissions
                         if u.get(col)) or "nothing yet")
        state = "" if u["active"] else ' <span class="badge">deactivated</span>'
        seen = str(u.get("last_login_at") or "never")[:16]
        chosen = selected is not None and selected["id"] == u["id"]
        lines.append(
            "<tr>"
            f"<td><b style=\"font-weight:500;color:var(--bright)\">"
            f"{esc(u['username'])}</b>{state}</td>"
            f"<td><span class=\"badge {'info' if u['role'] == 'admin' else ''}\">"
            f"{esc(u['role'])}</span></td><td>{esc(u['sees'])}</td>"
            f"<td class=\"muted\">{esc(may)}</td>"
            f"<td class=\"muted\">{esc(seen)}</td>"
            f"<td class=\"act\"><a class=\"btn quiet\" href=\"/users?id={u['id']}\">"
            f"{'editing' if chosen else 'edit'}</a></td></tr>")
    listing = (f'<div class="panel wrap"><table>{head}{"".join(lines)}'
               f'</table></div>')

    def tick_grid(current: dict) -> str:
        return '<div class="ticks">' + "".join(
            _tick(col, bool(current.get(col)), label, hint)
            for col, label, hint in permissions) + "</div>"

    editor = ""
    if selected is not None:
        u = selected
        editor = (
            f'<div class="panel"><h3>Edit {esc(u["username"])}</h3>'
            f'<form method="post" action="/users/{u["id"]}" class="field" '
            f'style="gap:12px"><input type="hidden" name="csrf" '
            f'value="{csrf}">'
            f'<div class="row"><span class="muted">Role</span>'
            f'{_choice("role", ("admin", "operator"), u["role"])}'
            f'<span class="muted" style="margin-left:8px">Sees</span>'
            f'{_choice("sees", ("all", "own"), u["sees"])}'
            f'<span style="margin-left:auto">'
            f'{_tick("active", bool(u["active"]), "active")}</span></div>'
            f'<p class="hint">An admin may do everything below and drive '
            f'the service; an operator may do exactly what is ticked.</p>'
            f'{tick_grid(u)}'
            f'<div class="row"><button>Save</button></div></form>'
            f'<form method="post" action="/users/{u["id"]}/reset" '
            f'class="row"><input type="hidden" name="csrf" value="{csrf}">'
            f'<button class="quiet warn">Reset password</button>'
            f'<span class="hint">shows a one-time password once and signs '
            f'them out everywhere</span></form></div>')

    creator = (
        '<div class="panel"><h3>New user</h3>'
        '<form method="post" action="/users/new" class="field" '
        'style="gap:12px">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        '<div class="row"><input name="username" placeholder="username" '
        'autocomplete="off" style="width:200px">'
        f'<span class="muted">Role</span>'
        f'{_choice("role", ("admin", "operator"), "operator")}'
        f'<span class="muted" style="margin-left:8px">Sees</span>'
        f'{_choice("sees", ("all", "own"), "own")}</div>'
        f'{tick_grid({})}'
        '<div class="row"><button>Create</button><span class="hint">a '
        'one-time password is shown once on the next page</span></div>'
        '</form></div>')
    body += listing + (f'<div class="grid2">{editor}{creator}</div>'
                       if editor else creator)
    return page("Users", body, user=user, here="/users")


def one_time_page(username: str, password: str, user: dict,
                  *, created: bool) -> str:
    """The password, exactly once. Not in a URL, not in the log, not on
    any later page - the person types it at their first sign-in and is
    then made to choose their own."""
    what = "created" if created else "password reset"
    body = (f'<div class="top"><h2>{esc(username)} — {esc(what)}</h2></div>'
            f'<div class="panel ok" style="max-width:520px">'
            f'<h3>Their one-time password, shown only now</h3>'
            f'<div class="code">{esc(password)}</div>'
            f'<p class="hint">Hand it over privately. They will be asked to '
            f'choose their own the first time they sign in, and every open '
            f'session of theirs has been ended. This page cannot be opened '
            f'again.</p>'
            f'<div class="row"><a class="btn quiet" href="/users">Back to '
            f'users</a></div></div>')
    return page("One-time password", body, user=user, here="/users")


def password_page(user: dict, error: str = "") -> str:
    csrf = esc(user.get("csrf", ""))
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    body = (f'<div class="card"><h2>Choose your password</h2>'
            f'<p class="muted">The one you signed in with was for one use. '
            f'Pick your own - at least 8 characters.</p>{err}'
            f'<form method="post" action="/password" class="field" '
            f'style="gap:12px"><input type="hidden" name="csrf" '
            f'value="{csrf}">'
            f'<label class="field"><span>New password</span>'
            f'<input name="password" type="password" autofocus '
            f'autocomplete="new-password"></label>'
            f'<label class="field"><span>The same, again</span>'
            f'<input name="again" type="password" '
            f'autocomplete="new-password"></label>'
            f'<button>Save</button></form></div>')
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
    "already": "Already asked - that request is still pending:",
}


def _may(user: dict, permission: str) -> bool:
    from ..store.users import may

    return bool(user.get("mutations")) and may(user, permission)


def _csrf(user: dict) -> str:
    return (f'<input type="hidden" name="csrf" '
            f'value="{esc(user.get("csrf", ""))}">')


def _said(said: str, table: dict) -> str:
    """The banner for a ?said= token. `queued:241` names the request the
    press became, and the banner links to it."""
    word, _, req = (said or "").partition(":")
    note = table.get(word, "")
    if not note:
        return ""
    if word in ("queued", "already") and req.isdigit():
        return (f'<p class="said">{esc(note)} <a href="/requests?hi={req}">'
                f'#{req} on Requests</a></p>')
    return f'<p class="said">{esc(note)}</p>'


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
            f"<tr><td class=\"mono\">{esc(str(u.get('host', '')))}:"
            f"{esc(str(u.get('port', '')))} "
            f"({esc(str(u.get('username', '')))})</td>"
            f"<td>" + (
                f'<form method="post" action="/pools/proxy/adopt" '
                f'class="inline">{_csrf(user)}'
                f'<input type="hidden" name="host" '
                f'value="{esc(str(u.get("host", "")))}">'
                f'<input type="hidden" name="port" '
                f'value="{esc(str(u.get("port", "")))}">'
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


# ------------------------------------------------- events, logs, story (C8)
def _event_badge(row: dict) -> str:
    kind, status = row.get("kind") or "", str(row.get("status") or "")
    if kind == "build_finished":
        ok = str(row.get("detail") or "").startswith("ok=True")
        return (f'<span class="badge {"ok" if ok else "bad"}">'
                f'build {"ok" if ok else "failed"}</span>')
    klass = {"phone": "info", "account": "manual", "stock": "ok",
             "request": "", "pass": "", "breaker": ""}.get(kind, "")
    if kind == "breaker":
        klass = "bad" if status == "tripped" else "ok"
    if kind == "request" and status in ("failed", "refused"):
        klass = "bad"
    return f'<span class="badge {klass}">{esc(kind)}</span>'


def _serial_link(serial) -> str:
    text = str(serial or "").strip()
    if not text:
        return '<span class="dim">—</span>'
    return f'<a href="/phones/{esc(text)}">{esc(text)}</a>'


def _signal_tiles(signals: dict) -> str:
    pulse = signals.get("pulse") or {}
    builds = signals.get("builds") or {}
    tiles = []
    tiles.append(("last pass", _ago(pulse["at"]) if pulse.get("at")
                  else "none yet", ""))
    tiles.append(("builds, last hour",
                  f'{int(builds.get("ok") or 0)} <span class="dim">ok</span> '
                  f'· {int(builds.get("failed") or 0)} '
                  f'<span class="dim">failed</span>', ""))
    tiles.append(("breaker", "open" if pulse.get("tripped") else "armed",
                  "bad" if pulse.get("tripped") else ""))
    days = signals.get("gmail_days")
    if days is None:
        burn = ('no builds this week <span class="dim">'
                f'· {signals.get("gmail_free", 0)} free</span>')
    else:
        burn = (f'~{days:.0f} days <span class="dim">of stock at '
                f'{signals.get("gmail_per_day", 0):.1f}/day</span>')
    tiles.append(("gmail burn", burn, "warn" if days is not None and
                  days < 3 else ""))
    last = signals.get("last_stock")
    tiles.append(("stock", f"added {_day(last)}" if last else "no adds yet",
                  ""))
    return "".join(
        f'<div class="tile {klass}"><div class="l">{esc(label)}</div>'
        f'<div class="mono" style="font-size:16px">{value}</div></div>'
        for label, value, klass in tiles)


def events_page(data: dict, user: dict, *, signals: dict | None = None,
                kind: str = "", q: str = "") -> str:
    counts = data.get("counts") or {}
    pills = [("", "all")] + [(name, name) for name in
                             ("builds", "phones", "accounts", "breaker",
                              "requests", "stock", "passes")]
    chips = []
    for value, label in pills:
        n = counts.get(label if value else "all", 0)
        lit = ' class="here"' if value == kind else ""
        href = f"/events?kind={esc(value)}&q={esc(q)}"
        chips.append(f'<a href="{href}"{lit}>{esc(label)} · {n}</a>')
    lines = []
    for r in data.get("rows") or []:
        run = (f"{r['run_id']}/{r['build']}" if r.get("build")
               else (r.get("run_id") or ""))
        text = esc(str(r.get("detail") or ""))
        if r.get("kind") == "build_finished":
            text = (f'{esc(str(r.get("status") or ""))} · {text}'
                    + (f' · {int(r["seconds"])}s' if r.get("seconds")
                       else ""))
        lines.append(
            f'<tr><td class="muted">{_day(r.get("at"))} '
            f'{_clock(r.get("at"))}</td><td>{_event_badge(r)}</td>'
            f'<td class="muted">{esc(run) or "—"}</td>'
            f'<td>{_serial_link(r.get("serial"))}</td><td>{text}</td></tr>')
    page_n, pages = int(data.get("page") or 1), int(data.get("pages") or 1)
    nav = ""
    if pages > 1:
        prev = (f'<a href="/events?kind={esc(kind)}&q={esc(q)}&page='
                f'{page_n - 1}">← newer</a>' if page_n > 1 else "")
        nxt = (f'<a href="/events?kind={esc(kind)}&q={esc(q)}&page='
               f'{page_n + 1}">older →</a>' if page_n < pages else "")
        nav = (f'<div class="row dim">{prev}<span style="margin-left:auto">'
               f'page {page_n} of {pages}</span>{nxt}</div>')
    body = ('<div class="top"><h2>Events</h2>'
            '<div class="pills"><span>Events</span>'
            '<a href="/logs">Logs</a></div>'
            '<span class="status">admin only · refreshes every 30s</span>'
            '</div>'
            + (f'<div class="tiles" style="grid-template-columns:repeat(5,'
               f'minmax(0,1fr))">{_signal_tiles(signals)}</div>'
               if signals is not None else "")
            + f'<div class="row"><div class="chips">{"".join(chips)}</div>'
              f'<form method="get" action="/events" class="row" '
              f'style="margin-left:auto"><input type="hidden" name="kind" '
              f'value="{esc(kind)}"><input name="q" value="{esc(q)}" '
              f'placeholder="serial, address, run id"><button class="quiet">'
              f'Search</button></form></div>'
            + '<div class="panel"><table><tr><th>time</th><th>kind</th>'
              '<th>run</th><th>phone</th><th>what</th></tr>'
            + ("".join(lines) or '<tr><td colspan="5" class="muted">'
                                  'Nothing recorded yet.</td></tr>')
            + f'</table>{nav}<p class="dim">alerts (stage 6) fire on these '
              f'kinds — never on log prose · a serial anywhere opens that '
              f'phone\'s story · {int(data.get("total") or 0)} matching'
              f'</p></div>')
    return page("Events", body, user=user, here="/events", refresh=30)


_LEVEL_BADGE = {"INFO": "", "WARNING": "warn", "ERROR": "bad",
                "CRITICAL": "bad", "DEBUG": ""}


def logs_page(data: dict, user: dict, *, level: str = "INFO",
              logger: str = "", run: str = "", phone: str = "",
              q: str = "") -> str:
    def pill(name: str) -> str:
        lit = ' class="here"' if name == level else ""
        href = (f"/logs?level={name}&logger={esc(logger)}&run={esc(run)}"
                f"&phone={esc(phone)}&q={esc(q)}")
        return f'<a href="{href}"{lit}>{name}</a>'

    lines = []
    for r in data.get("rows") or []:
        ctx = (f"[{r.get('run') or '-'}/{r.get('build') or '-'}]")
        name = str(r.get("logger") or "").replace("geelark_farm.", "")
        lines.append(
            f'<tr><td class="muted">{_clock(r.get("at"))}</td>'
            f'<td><span class="badge '
            f'{_LEVEL_BADGE.get(str(r.get("level")), "")}">'
            f'{esc(str(r.get("level")))}</span></td>'
            f'<td class="muted">{esc(ctx)}</td>'
            f'<td class="muted">{esc(name)}</td>'
            f'<td style="white-space:pre-wrap">{esc(str(r.get("msg") or ""))}'
            f'</td></tr>')
    body = (f'<div class="top"><h2>Events</h2>'
            f'<div class="pills"><a href="/events">Events</a><span>Logs'
            f'</span></div><span class="status">INFO and up · kept 30 days '
            f'· the JSON file on disk stays the complete record</span></div>'
            f'<form method="get" action="/logs" class="row">'
            f'<input type="hidden" name="level" value="{esc(level)}">'
            f'<div class="chips">{pill("INFO")}{pill("WARNING")}'
            f'{pill("ERROR")}</div>'
            f'<input name="logger" value="{esc(logger)}" placeholder="logger">'
            f'<input name="run" value="{esc(run)}" placeholder="run: r8" '
            f'size="10"><input name="phone" value="{esc(phone)}" '
            f'placeholder="phone: 1533" size="12">'
            f'<input name="q" value="{esc(q)}" placeholder="text in the '
            f'message"><button class="quiet">Filter</button></form>'
            f'<div class="panel"><table><tr><th>time</th><th>level</th>'
            f'<th>run</th><th>logger</th><th>message</th></tr>'
            + ("".join(lines) or '<tr><td colspan="5" class="muted">'
                                  'Nothing captured yet - LOG_DB may be off.'
                                  '</td></tr>')
            + f'</table><p class="dim">captured in-process, batched into '
              f'the database; if the database stalls the capture disables '
              f'itself with one warning — it can never slow a build · '
              f'{int(data.get("today") or 0):,} lines today</p></div>')
    return page("Logs", body, user=user, here="/events", refresh=15)


def phone_story_page(story: dict, user: dict) -> str:
    phone = story.get("phone") or {}
    serial = story["serial"]
    head = ""
    if phone:
        bits = [esc(str(phone.get("gmail") or "no gmail")),
                esc(str(phone.get("proxy_name") or "no proxy")),
                f"created {_day(phone.get('created_at'))} "
                f"{_clock(phone.get('created_at'))}"]
        if phone.get("app_account"):
            bits.insert(1, esc(str(phone["app_account"])))
        head = (f'<span>{_phone_badge(phone)}</span>'
                f'<span class="dim mono">{" · ".join(bits)}</span>')
        if phone.get("done_at"):
            head += (f'<span class="badge">gone {_day(phone["done_at"])}'
                     f'</span>')
    action = ""
    if _may(user, "may_change_proxy") and phone and not phone.get("done_at"):
        action = (f'<form method="post" class="inline" '
                  f'action="/phones/{esc(serial)}/proxy">{_csrf(user)}'
                  f'<button class="quiet">Change proxy</button></form>')
    items = []
    for t in story.get("timeline") or []:
        when = f"{_day(t['at'])} {_clock(t['at'])}"
        badge = _event_badge({"kind": t["kind"], "status": t["status"],
                              "detail": ("ok=True" if t["kind"] ==
                                         "build_finished" and
                                         str(t["status"]) == "ready"
                                         else "")})
        if t["source"] == "artifact":
            badge = '<span class="badge">screens</span>'
        run = f' <span class="dim">[{esc(str(t.get("run") or ""))}]</span>' \
            if t.get("run") else ""
        secs = (f' <span class="dim">· {int(t["seconds"])}s</span>'
                if t.get("seconds") else "")
        items.append(
            f'<div class="row" style="align-items:flex-start;padding:8px 0;'
            f'border-bottom:1px solid var(--line2)">'
            f'<span class="mono dim" style="min-width:150px">{when}</span>'
            f'{badge}<span>{esc(str(t.get("text") or ""))}{run}{secs}'
            f'</span></div>')
    body = (f'<div class="top"><a href="/events" class="dim">← Events</a>'
            f'<h2>Phone {esc(serial)}</h2>{head}'
            f'<span class="status">{action}</span></div>'
            f'<div class="panel">'
            + ("".join(items) or '<p class="muted">Nothing recorded about '
                                 'this phone.</p>')
            + f'<p class="dim">everything this phone went through, in order '
              f'— events, requests and archived screens joined on its serial'
              f' · <a href="/logs?phone={esc(serial)}">open its log lines'
              f'</a></p></div>')
    return page(f"Phone {serial}", body, user=user, here="/events")


# ------------------------------------------------------------ confirming
def confirm_page(user: dict, *, title: str, text: str, action: str,
                 fields: dict, button: str, back: str) -> str:
    """One question before something is taken away. The hidden fields
    carry exactly what the first form sent, plus `sure`."""
    hidden = "".join(
        f'<input type="hidden" name="{esc(k)}" value="{esc(str(v))}">'
        for k, v in fields.items())
    body = (f'<div class="card" style="width:min(560px,100%)">'
            f'<h2>{esc(title)}</h2><p class="muted">{esc(text)}</p>'
            f'<form method="post" action="{esc(action)}" class="row">'
            f'{_csrf(user)}{hidden}'
            f'<button class="quiet bad" style="padding:9px 16px;'
            f'font-size:13.5px">{esc(button)}</button>'
            f'<a class="btn quiet" href="{esc(back)}" style="padding:9px 16px;'
            f'font-size:13.5px">Keep it</a></form></div>')
    return page(title, body, user=user, here=back)


# ---------------------------------------------------------- store is down
def store_down_page(retry: tuple | None = None) -> str:
    """The cluster did not answer. Nothing was read or queued; say so,
    keep what the person typed, and try again in half a minute."""
    again = ""
    if retry:
        path, form = retry
        hidden = "".join(
            f'<input type="hidden" name="{esc(k)}" value="{esc(str(v))}">'
            for k, vs in (form or {}).items() for v in (vs or [])
            if k != "csrf")
        again = (f'<form method="post" action="{esc(path)}">{hidden}'
                 f'<p class="hint">Your form is kept here - press to send '
                 f'it again once the store is back.</p>'
                 f'<button class="quiet">Try again</button></form>')
    body = (f'<div class="card" style="width:min(560px,100%)">'
            f'<h2>The store is not answering</h2>'
            f'<p class="muted">Nothing was read or queued. The service on '
            f'the server keeps building from the sheet; this page retries '
            f'in 30 seconds.</p>{again}</div>')
    return page("Store down", body, refresh=30)
