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
import re
import time
from html import escape as esc
from urllib.parse import quote

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
td.num{{text-align:right}}
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
.pick{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;
 align-items:center;padding:9px 10px;border-radius:8px;background:var(--panel2);
 border:1px solid var(--line2)}}
.pick.tick{{grid-template-columns:auto minmax(0,1fr) auto;cursor:pointer}}
.pick:has(input:checked){{border-color:#2c4a7a;background:#111b2e}}
.hand{{width:100%;font-size:12px;user-select:all;min-height:30px;padding:4px 10px;
 color:var(--muted)}}
.switches{{display:flex;gap:6px 18px;flex-wrap:wrap}}
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
.narrow{{width:100%;max-width:1060px;margin:0 auto;display:flex;
 flex-direction:column;gap:26px}}
.headline{{display:flex;align-items:flex-end;gap:40px;flex-wrap:wrap}}
.headline .n{{font-family:var(--mono);font-size:56px;line-height:1;
 font-weight:500;font-variant-numeric:tabular-nums}}
.headline .l{{font-size:12.5px;color:var(--muted)}}
.strip{{margin-left:auto;display:flex;gap:22px;flex-wrap:wrap;
 font-size:13px;color:var(--muted);padding-bottom:6px}}
.strip a{{color:var(--muted)}} .strip a:hover{{color:#fff}}
.strip b{{font-family:var(--mono);font-weight:500;margin-right:5px}}
.svc{{display:flex;align-items:center;gap:14px;padding-top:12px;
 border-top:1px solid var(--line2);font-size:12px;color:#55627a}}
.svc button{{background:none;border:0;color:var(--dim);font-size:12px;
 font-family:inherit;cursor:pointer;padding:2px 0}}
.svc button:hover{{color:#fff;background:none}}
.svc .right{{margin-left:auto}}
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
tr.warn td{{background:rgba(240,192,100,.06)}} tr.warn td.msg{{color:var(--amber)}}
tr.bad td{{background:rgba(224,101,79,.08)}} tr.bad td.msg{{color:var(--red)}}
tr.off td{{opacity:.55}}
.avatar{{display:inline-flex;width:26px;height:26px;border-radius:13px;
 background:#24314a;align-items:center;justify-content:center;font-size:12px;
 font-weight:600;color:#b9cae6;text-transform:uppercase;margin-right:8px;
 vertical-align:middle;flex-shrink:0}}
.entry{{display:flex;gap:12px;align-items:flex-start;padding:9px 0;
 border-bottom:1px solid var(--line2)}}
.entry:last-child{{border-bottom:0}}
.entry .when{{min-width:150px;font-family:var(--mono);color:var(--dim);
 font-size:12px;padding-top:2px}}
.entry .lines{{display:flex;flex-direction:column;gap:2px;min-width:0}}
.entry .head{{color:var(--bright)}}
.entry.now{{background:rgba(127,180,255,.05);border-radius:8px;padding:9px 10px;
 margin-top:6px}}
.live{{display:inline-flex;align-items:center;gap:6px;font-family:var(--mono);
 font-size:12px;color:var(--green)}}
.live::before{{content:"";width:8px;height:8px;border-radius:50%;
 background:var(--green);box-shadow:0 0 6px var(--green)}}
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
         ("/needs", "Needs attention", "needs"),
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
                hot = " hot" if key in ("pending", "app", "needs") \
                    and counts[key] else ""
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


#: `page` doubles as a parameter name on the paged views; the alias keeps
#: the shell reachable inside them.
page_ = page


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


def today() -> str:
    """Today's date in the owner's zone, the way a ?day= carries it."""
    return datetime.datetime.now(_ZONE).date().isoformat()


#: The service controls an admin may press from the actor bar, keyed by
#: the word the `control` verb takes. Which ones show depends on the pulse:
#: a paused keeper offers Resume, an open breaker offers Clear, a stopped
#: service offers only Start.
CONTROLS = {
    "pause": {"label": "Pause building", "klass": "quiet",
              "text": "The keeper stops starting builds at its next pass. "
                      "Phones already being built finish; accounts still "
                      "log in; nothing is deleted. Untick it here or on "
                      "the Service tab to resume."},
    "resume": {"label": "Resume building", "klass": "quiet",
               "text": "Pause building is unticked at the next pass and "
                       "the keeper builds the shortfall again."},
    "clear_breaker": {"label": "Clear breaker", "klass": "quiet warn",
                      "text": "The breaker opened because builds failed "
                              "several times in a row. Clearing it lets "
                              "the keeper build again at the next pass - "
                              "if the cause is still there it trips again "
                              "and spends stock on the way."},
    "stop": {"label": "Stop everything", "klass": "quiet bad",
             "text": "The service stops at its next pass: nothing is "
                     "synced, built, finished or drained until Start is "
                     "pressed (or the tick removed on the Service tab). "
                     "Phones GeeLark is running keep running and keep "
                     "being billed."},
    "start": {"label": "Start", "klass": "quiet",
              "text": "Stop everything is unticked at the next pass and "
                      "the service carries on where it left off."},
}

#: The sheet's State words a person can give a phone from the table, and
#: what each costs - the two that delete something ask first.
PHONE_STATES = {
    "taken": {"label": "Take", "klass": "quiet", "sure": False,
              "text": ""},
    "unused": {"label": "Release", "klass": "quiet", "sure": False,
               "text": ""},
    "done": {"label": "Done", "klass": "quiet bad", "sure": True,
             "text": "The next sync deletes the phone in GeeLark and "
                     "retires the gmail and the account on it as "
                     "delivered. There is no undo: the phone is gone."},
    "failed": {"label": "Failed", "klass": "quiet bad", "sure": True,
               "text": "The next sync deletes the phone in GeeLark, marks "
                       "its gmail used and frees the account for another "
                       "phone. There is no undo: the phone is gone."},
}

#: Order of the phones table: what can be handed over first, then what is
#: waiting for an account, then what needs a look, then what is still
#: being made.
_PHONE_ORDER = {"ready": 0, "app_only": 1, "incomplete": 2, "building": 3}

#: The flow a captured log line came from, in the words the row shows.
_FLOW_WORDS = {"google_login": "google sign-in",
               "chatgpt_login": "chatgpt sign-in",
               "play_install": "play install", "router": "screen",
               "verify": "verifying", "builder": "build"}

#: Every switch the admin's footer line lists, with one clause for each
#: side of it. The keys are the Settings attributes.
_SWITCHES = {
    "web_mutations": {"name": "WEB_MUTATIONS",
                      "on": "buttons queue commands for the pass",
                      "off": "the console is read-only"},
    "manual_login": {"name": "MANUAL_LOGIN",
                     "on": "accounts wait for a person to pick them",
                     "off": "accounts log in on their own"},
    "log_db": {"name": "LOG_DB",
               "on": "log lines are captured into the store",
               "off": "the Logs page stays empty"},
    "pools_in_pg": {"name": "POOLS_IN_PG",
                    "on": "the store is the pool",
                    "off": "the sheet is the pool; the store mirrors it"},
    "web_user_admin": {"name": "WEB_USER_ADMIN",
                       "on": "admins can manage users here",
                       "off": "the Users page does not exist"},
}

#: The colour a request's outcome is shown in on the ticker.
_OUTCOME_COLOUR = {"done": "green", "failed": "red", "refused": "red",
                   "running": "blue", "queued": "muted",
                   "cancelled": "dim", "awaiting_confirm": "amber"}


def _need(user: dict, permission: str, doing: str) -> str:
    """The dim line under a panel whose buttons this person cannot see:
    the flag is on, the permission is not. Nothing when the flag is off
    (there is nothing to ask for) or the buttons are there."""
    if not user.get("mutations") or _may(user, permission):
        return ""
    return (f'<p class="dim">{esc(doing)} needs the {esc(permission)} '
            f'permission - ask an admin</p>')


def _warning_link(pulse: dict) -> tuple[str, str]:
    """Where the keeper's sentence points: the pool that is short, or the
    events for the breaker."""
    low = str(pulse.get("warning") or "").lower()
    if pulse.get("tripped") or "breaker" in low:
        return "/events?kind=breaker", "see the breaker's events"
    if "gmail" in low:
        return "/pools/gmail", "open the Gmail pool"
    if "prox" in low or "exit" in low:
        return "/pools/proxy", "open the Proxy pool"
    if "account" in low or "gpt" in low:
        return "/pools/gpt", "open the Gpt pool"
    return "/events", "see the events"


def _progress(line: dict | None) -> str:
    """What a phone being built is doing right now, off its last captured
    log line: 'google sign-in: <message> · 96s'."""
    if not line:
        return '<span class="dim">starting</span>'
    logger = str(line.get("logger") or "").rsplit(".", 1)[-1]
    flow = _FLOW_WORDS.get(logger, logger or "build")
    msg = str(line.get("msg") or "").splitlines() or [""]
    text = f'{esc(flow)}: {esc(msg[0][:120])}'
    started = _moment(line.get("started"))
    if started is not None:
        seconds = (datetime.datetime.now(_ZONE) - started).total_seconds()
        text += f' <span class="dim">· {max(0, int(seconds))}s</span>'
    return text


def _hhmm(value) -> str:
    return _clock(value)[:5]


def _event_sentence(e: dict) -> tuple[str, str]:
    """One event as a sentence with its actor, and the colour it earns."""
    kind = str(e.get("kind") or "")
    status = str(e.get("status") or "")
    detail = str(e.get("detail") or "")
    serial = str(e.get("serial") or "")
    phone = f"phone {_serial_link(serial)}" if serial else "a phone"
    if kind == "build_finished":
        if detail.startswith("ok=True"):
            return f"{phone} became ready", "green"
        return f"{phone} failed its build — {esc(status)}", "red"
    if kind == "breaker":
        if status == "tripped":
            return f"the breaker tripped — {esc(detail)}", "red"
        return f"the breaker was cleared — {esc(detail)}", "green"
    if kind == "phone":
        return f"{phone} was {esc(status)}", "amber"
    if kind == "account":
        return f"an account was set aside on {phone} — {esc(detail)}", "amber"
    if kind == "stock":
        return f"stock: {esc(detail)}", "green"
    if kind == "pass":
        return f"pass: {esc(status or detail)}", "muted"
    return (f"{esc(kind)} {esc(status)}"
            + (f" on {phone}" if serial else "")), "muted"


def _request_sentence(a: dict) -> tuple[str, str]:
    """'mehdi asked: Log in 2 accounts → running', serial linked."""
    payload = a.get("payload") or {}
    head, _aside = describe(str(a.get("verb") or ""), payload)
    text = esc(head)
    serial = str(payload.get("serial") or "")
    if serial and esc(serial) in text:
        text = text.replace(esc(serial), _serial_link(serial), 1)
    status = str(a.get("status") or "")
    colour = _OUTCOME_COLOUR.get(status, "muted")
    return (f'<b>{esc(str(a.get("requested_by") or "?"))}</b> asked: {text} '
            f'→ <span style="color:var(--{colour})">{esc(status)}</span>',
            colour)


def _ticker(data: dict) -> str:
    items = []
    for e in data.get("recent") or []:
        text, colour = _event_sentence(e)
        items.append((e.get("at"), text, colour))
    for a in data.get("asked") or []:
        text, colour = _request_sentence(a)
        items.append((a.get("at"), text, colour))
    items.sort(key=lambda t: (_as_dt(t[0]) or datetime.datetime.min).replace(
        tzinfo=None), reverse=True)
    return " ".join(
        f'<span><span style="color:var(--{colour})">{_hhmm(at)}</span> '
        f'{text}</span>' for at, text, colour in items)


def _controls(data: dict, user: dict) -> str:
    """The service buttons in the actor bar - admins only, and only the
    ones that make sense for the pulse."""
    pulse = data.get("pulse") or {}
    if not pulse or not (user.get("mutations") and
                         user.get("role") == "admin"):
        return ""
    wanted = []
    if pulse.get("stopped"):
        wanted.append("start")
    else:
        wanted.append("resume" if pulse.get("paused") else "pause")
        if pulse.get("tripped"):
            wanted.append("clear_breaker")
        wanted.append("stop")
    return "".join(
        f'<form method="post" class="inline" action="/service/{what}">'
        f'{_csrf(user)}<button class="{CONTROLS[what]["klass"]}">'
        f'{esc(CONTROLS[what]["label"])}</button></form>' for what in wanted)


def _state_form(user: dict, serial: str, state: str, back: str = "/") -> str:
    """One Take / Back / Done / Failed button; `back` is the page the
    press returns to (the dashboard, or the phone's own story)."""
    plan = PHONE_STATES[state]
    return (f'<form method="post" class="inline" '
            f'action="/phones/{esc(serial)}/state">{_csrf(user)}'
            f'<input type="hidden" name="state" value="{state}">'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<button class="{plan["klass"]}">{esc(plan["label"])}'
            f'</button></form>')


def _change_ip_form(user: dict, serial: str, back: str = "/") -> str:
    """"Change IP": the phone is stopped, given the next free exit and
    reads it when it next starts. Offered in every state a person can
    act on - a taken phone whose exit is refused needs it most."""
    return (f'<form method="post" class="inline" '
            f'action="/phones/{esc(serial)}/proxy">{_csrf(user)}'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<button class="quiet">Change IP</button></form>')


def _state_forms(user: dict, row: dict, back: str = "/") -> list[str]:
    """The phone-state buttons a row offers: Take or Back, then Done and
    Failed. Empty while it is being built or for someone who may not."""
    if (row.get("status") or "") == "building" or \
            not _may(user, "may_take_phones"):
        return []
    serial = str(row.get("serial") or "")
    taken = (row.get("state") or "") == "taken"
    return [_state_form(user, serial, "unused" if taken else "taken", back),
            _state_form(user, serial, "done", back),
            _state_form(user, serial, "failed", back)]


def _row_actions(user: dict, row: dict, back: str = "/") -> str:
    """What one phone offers from the table.

    A phone on the shelf offers the one thing anybody does with it -
    Take - and Change IP beside it. Once it is out with somebody the row
    turns into the three ways that ends: Release, Done, Failed. Closing
    a phone nobody took is rarer and lives on the phone's own page, so
    the table stays two buttons wide. A phone being built offers
    nothing: a run is holding it.
    """
    building = (row.get("status") or "") == "building"
    if building or not _may(user, "may_take_phones"):
        actions = []
    elif (row.get("state") or "") == "taken":
        actions = _state_forms(user, row, back)
    else:
        actions = _state_forms(user, row, back)[:1]
    if _may(user, "may_change_proxy") and not building:
        actions.append(_change_ip_form(user, str(row.get("serial") or ""),
                                       back))
    return " ".join(actions)


def _phone_rows(data: dict, user: dict) -> str:
    """One line per phone: what it is, what is on it, and what you can do
    with it. The hand-over line and the story live on the phone's own
    page - this table is for seeing the shelf at a glance."""
    # What can go out first, and inside each kind what nobody has taken:
    # the top of this table is the shelf the headline number counts.
    phones = sorted(data.get("phones") or [],
                    key=lambda r: (_PHONE_ORDER.get(r.get("status") or "", 9),
                                   (r.get("state") or "") == "taken",
                                   str(r.get("serial"))))
    progress = data.get("progress") or {}
    lines = []
    for r in phones:
        serial = str(r.get("serial") or "")
        status = r.get("status") or ""
        badge = _phone_badge(r)
        if (r.get("state") or "") == "taken":
            who = esc(str(r.get("owner") or "somebody"))
            when = _when(r.get("updated_at")) if r.get("updated_at") else ""
            badge += (f'<br><span class="dim">{who}'
                      f'{" · " + when if when else ""}</span>')
        if status == "building":
            lines.append(
                f'<tr><td>{_serial_link(serial)}</td><td>{badge}</td>'
                f'<td colspan="3">{_progress(progress.get(serial))}</td>'
                f'</tr>')
            continue
        account = esc(str(r.get("app_account") or "")) or \
            '<span class="dim">waiting for an account</span>'
        lines.append(
            f'<tr><td>{_serial_link(serial)}</td><td>{badge}</td>'
            f'<td>{account}<br><span class="dim">'
            f'{esc(str(r.get("gmail") or ""))}</span></td>'
            f'<td>{esc(str(r.get("proxy_name") or ""))}</td>'
            f'<td class="act">{_row_actions(user, r)}</td></tr>')
    return "".join(lines)


def _status_sentence(data: dict) -> str:
    """How the farm is, in one sentence. The numbers only when they are
    not what they should be - the alert strip carries the rest."""
    pulse = data.get("pulse") or {}
    if not pulse:
        return '<span class="dim">no pass has reported yet</span>'
    warm, target = int(pulse.get("warm") or 0), int(pulse.get("target") or 0)
    if pulse.get("stopped"):
        word, colour = "Stopped from the sheet", "red"
    elif pulse.get("tripped"):
        word, colour = "Building stopped", "red"
    elif pulse.get("paused"):
        word, colour = "Building paused", "amber"
    elif warm < target:
        word, colour = f"Building — {warm} of {target} warm", "amber"
    else:
        word, colour = "Everything running", "green"
    when = _ago(pulse["at"]) if pulse.get("at") else "unknown"
    return (f'<span style="color:var(--{colour})">●</span> {esc(word)} '
            f'<span class="dim">·</span> last pass {esc(when)}')


def _stock_strip(data: dict) -> str:
    """The three pools as one line: the number, the word, and a colour
    when there is not enough of it to keep building."""
    stock = data.get("stock") or {}
    pulse = data.get("pulse") or {}
    target = int(pulse.get("target") or 0)
    warm = int(pulse.get("warm") or 0)
    gmail = int((stock.get("gmail") or {}).get("free") or 0)
    proxy = int((stock.get("proxy") or {}).get("free") or 0)
    awaiting = int((stock.get("app") or {}).get("awaiting") or 0)
    short = f"fewer than the {target} phones the keeper keeps warm"
    items = [
        ("/pools/gmail", gmail, "gmail",
         "red" if not gmail else "amber" if gmail < target else "ink",
         "nothing can be built until rows are added" if not gmail else
         short if gmail < target else "free to build with"),
        ("/pools/proxy", proxy, "proxies",
         "red" if not proxy else "amber" if proxy < target else "ink",
         "no free exit - the next build has nowhere to go out from"
         if not proxy else short if proxy < target else "free to build with"),
        ("/pools/gpt", awaiting, "GPT accounts",
         "amber" if awaiting > warm else "ink",
         f"{awaiting - warm} of them have no phone to go to"
         if awaiting > warm else "awaiting login"),
    ]
    return "".join(
        f'<a href="{href}" title="{esc(why)}">'
        f'<b style="color:var(--{colour})">{number}</b>{esc(word)}</a>'
        for href, number, word, colour, why in items)


def _service_row(data: dict, user: dict, flags: dict | None) -> str:
    """The quiet line at the foot: the service's own controls and which
    switches this server runs with. Admins only, and never shouted."""
    if user.get("role") != "admin":
        return ""
    controls = _controls(data, user)
    switches = ""
    if flags:
        bits = []
        for key, words in _SWITCHES.items():
            on = bool(flags.get(key))
            bits.append(f'{words["name"]} '
                        f'<b style="color:var(--{"green" if on else "dim"})">'
                        f'{"on" if on else "off"}</b>')
        switches = (f'<span class="right mono">{" · ".join(bits)}</span>')
    if not controls and not switches:
        return ""
    return (f'<div class="svc"><span>Service</span>{controls}{switches}</div>')


def dashboard(data: dict, user: dict, said: str = "",
              manual_login: bool = False,
              flags: dict | None = None) -> str:
    """The console's front page, kept to two questions: is the farm well,
    and what can be handed over now. One sentence of health, one number,
    a line of stock, the phones with their own buttons. Everything that
    is only sometimes true - the keeper's complaint, accounts waiting -
    appears only when it is true; the rest lives on its own page."""
    pulse = data.get("pulse") or {}
    phones = data.get("phones") or []
    taken = [r for r in phones if (r.get("state") or "") == "taken"]
    ready = [r for r in phones if (r.get("status") or "") == "ready"
             and (r.get("state") or "") != "taken"]
    warm = [r for r in phones if (r.get("status") or "") == "app_only"]
    building = [r for r in phones if (r.get("status") or "") == "building"]

    note = []
    if ready:
        note.append("Take one to hand it over")
    note.append(f"{_plural(len(warm), 'warm phone')} behind them")
    if building:
        note.append(f"{len(building)} building")
    if taken:
        note.append(f"{len(taken)} out with somebody")
    headline = (f'<div class="headline"><div>'
                f'<div class="l">Ready to deliver</div>'
                f'<div class="n" style="color:var('
                f'--{"green" if ready else "dim"})">{len(ready)}</div>'
                f'<div class="l">{esc(" · ".join(note))}</div></div>'
                f'<div class="strip">{_stock_strip(data)}</div></div>')

    rows = _phone_rows(data, user)
    table = (f'<table><tr><th>serial</th><th>state</th><th>account</th>'
             f'<th>proxy</th><th></th></tr>{rows}</table>'
             if rows else '<p class="empty">No phones yet - the keeper '
                          'builds the shortfall on its next pass.</p>')
    hint = _need(user, "may_take_phones",
                 "taking, returning and closing phones")

    warning = ""
    if pulse.get("warning"):
        href, label = _warning_link(pulse)
        warning = (f'<a class="alert warn" href="{href}">'
                   f'{esc(str(pulse["warning"]))} — {esc(label)}</a>')

    login_panel = _awaiting_panel(data, user, manual_login, pulse)

    footer = ""
    if user.get("sees") == "all":
        footer = (f'<div class="row dim mono" style="border-top:1px solid '
                  f'var(--line2);padding-top:12px">{_ticker(data)}'
                  f'<a href="/events" style="margin-left:auto">all events</a>'
                  f'</div>')
    footer += _service_row(data, user, flags)

    body = (f'<div class="narrow">'
            f'<div class="top"><h2>Dashboard</h2>'
            f'<span class="status">{_status_sentence(data)}</span></div>'
            + _said(said, _DASH_SAID) + warning + headline
            + f'<div>{table}{hint}</div>' + login_panel + footer
            + '</div>')
    busy = bool(building) or int(
        (data.get("queue") or {}).get("queued") or 0) > 0
    return page("Dashboard", body, user=user, here="/",
                refresh=30 if busy else 0)


def _awaiting_panel(data: dict, user: dict, manual_login: bool,
                    pulse: dict) -> str:
    """The accounts with nowhere to go yet - shown only when there are
    some, or when this person could act on them. An empty panel saying
    "nothing waiting" is a line of noise on a page that is about the
    phones."""
    awaiting = data.get("awaiting") or []
    can_login = manual_login and _may(user, "may_login_accounts")
    if not awaiting:
        return ""
    warm = int(pulse.get("warm") or 0)
    items = []
    for a in awaiting:
        source = a.get("source") or "manual"
        who = ("panel" if source == "panel" else
               f'manual · {esc(a.get("added_by") or "sheet")}')
        tick = (f'<input type="checkbox" name="addresses" '
                f'value="{esc(a["address"])}">' if can_login else "")
        ago = _ago(a.get("created_at"))
        items.append(
            f'<label class="pick{" tick" if tick else ""}">{tick}'
            f'<span class="mono" style="min-width:0;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap">{esc(a["address"])}'
            f'</span>'
            f'<span class="badge {"panel" if source == "panel" else "manual"}">'
            f'{who}</span>'
            f'<span class="dim" style="grid-column:{2 if tick else 1}/-1">'
            f'{"added " + ago if ago else "added: no stamp"}</span></label>')
    listed = "".join(items)
    head = (f'<div class="row"><h3>Awaiting login</h3>'
            f'<span class="dim mono">{_plural(len(awaiting), "account")}'
            f'</span></div>')
    if not can_login:
        why = ("accounts log in on their own on the next pass"
               if not manual_login else
               "you may not log accounts in - it needs the "
               "may_login_accounts permission; ask an admin")
        return (f'<div class="panel">{head}{listed}'
                f'<p class="dim">{why}</p></div>')
    if warm:
        many = _plural(warm, "warm phone")
        foot = (f'<div class="row"><span class="dim">{many} can take them; '
                f'each ticked account boots one</span>'
                f'<button class="right">Log in selected</button></div>')
    else:
        foot = ('<p class="dim">no warm phone is free - the keeper is '
                'building; there is nothing to press until one is</p>')
    return (f'<form method="post" action="/accounts/login" class="panel">'
            f'{_csrf(user)}{head}{listed}{foot}</form>')


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


#: Which permission offers a set-aside row of each kind again. Proxies
#: have no "offer again" - a `change ip` exit is marked free from the
#: Proxy Pool once the vendor changed it.
OFFER_PERMISSION = {"gmail": "may_add_gmail", "app": "may_add_gpt"}


def needs_page(data: dict, user: dict, advice, said: str = "") -> str:
    """`advice` is failures.verdict, passed in rather than imported here:
    pages render, read decides, and the one module that may know the verdict
    table is the one assembling the data."""
    total = sum(len(v) for v in data.values())
    body = (f'<div class="top"><h2>Needs attention</h2><span class="status">'
            f'{total} waiting on a person</span></div>'
            f'<p class="sub">What the program refuses to decide on its own. '
            f'Each block says what it is and where the fix lives.</p>'
            + _said(said, _POOL_SAID))
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
                 f'yours: set the row\'s status in the sheet - "Free again" '
                 f'and "Mark used" buttons are not built here yet.</p>'
                 f'<table><tr><th>tab</th><th>which</th><th>phone</th></tr>'
                 f'{rows}</table></div>')

    if data["flagged"]:
        lines = []
        kinds = set()
        for r in data["flagged"]:
            kind = str(r["kind"])
            kinds.add(kind)
            permission = OFFER_PERMISSION.get(kind)
            action = ""
            if permission and _may(user, permission):
                action = (f'<form method="post" action="/needs/offer" '
                          f'class="inline">{_csrf(user)}'
                          f'<input type="hidden" name="kind" value="{esc(kind)}">'
                          f'<input type="hidden" name="address" '
                          f'value="{esc(str(r["who"]))}">'
                          f'<button class="quiet warn">Offer again</button>'
                          f'</form>')
            lines.append(
                f"<tr><td>{esc(kind)}</td><td>{esc(str(r['who']))}</td>"
                f"<td><span class=\"badge attn\">{esc(str(r['status']))}</span>"
                f"</td><td class=\"muted\">{esc(advice(r['status']))}</td>"
                f"<td>{action}</td></tr>")
        hints = "".join(
            _need(user, OFFER_PERMISSION[kind], f"offering {word} again")
            for kind, word in (("gmail", "gmails"), ("app", "accounts"))
            if kind in kinds)
        body += (f'<div class="panel warn"><h3>Set aside by a run '
                 f'<span class="n">{len(data["flagged"])}</span></h3>'
                 f'<p class="hint">A run judged these and put them out of '
                 f'the pool. Fix the cause, then Offer again - the row goes '
                 f'back in the pool with your name in its note.</p>'
                 f'<table><tr><th>tab</th><th>which</th>'
                 f'<th>status</th><th>meaning</th><th></th></tr>'
                 f'{"".join(lines)}</table>{hints}</div>')

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
        can_clear = _may(user, "may_take_phones")
        rows = "".join(
            f"<tr><td>{_serial_link(r['serial'])}</td>"
            f"<td><span class=\"badge attn\">{esc(str(r['status']))}</span>"
            f"</td><td>{r['tries']}</td>"
            f"<td class=\"muted\">{esc(str(r['note']))}</td><td>" + (
                f'<form method="post" action="/needs/clear" class="inline">'
                f'{_csrf(user)}<input type="hidden" name="serial" '
                f'value="{esc(str(r["serial"]))}">'
                f'<button class="quiet warn">Clear tries</button></form>'
                if can_clear else "") + "</td></tr>"
            for r in data["given_up"])
        body += (f'<div class="panel warn"><h3>Given-up phones '
                 f'<span class="n">{len(data["given_up"])}</span></h3>'
                 f'<p class="hint">Three failed logins each. Clearing the '
                 f'Tries cell puts a phone back in the queue - the keeper '
                 f'offers it an account again on its next pass.</p>'
                 f'<table><tr><th>serial</th><th>status</th><th>tries</th>'
                 f'<th>note</th><th></th></tr>{rows}</table>'
                 f'{_need(user, "may_take_phones", "clearing tries")}</div>')
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


#: The one control whose record reads differently from its button: the
#: button says "Start" beside a stopped service, the record says what
#: was asked of a service that had been stopped.
_CONTROL_SAID = {"start": "Start again"}

#: A running row older than this shows a "stuck?" hint: no build takes
#: twenty minutes, and the drain closes such rows after two budgets.
STUCK_AFTER = 20 * 60


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
        return f"Change IP on {p.get('serial', '?')}", ""
    if verb == "stop_phone":
        return f"Stop phone {p.get('serial', '?')}", ""
    if verb == "add_gmails":
        seller = p.get("seller") or ""
        return (f"Add {_plural(len(rows), 'gmail')}",
                f"seller {seller}" if seller else "")
    if verb == "add_proxies":
        return f"Add {_plural(len(rows), 'proxy', 'proxies')}", ""
    if verb == "add_gpt":
        who = [str(r.get("address") or "") for r in rows]
        return (f"Add {_plural(len(who), 'GPT account')}",
                ", ".join(who[:6]) + (" …" if len(who) > 6 else ""))
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
        what = str(p.get("what") or "")
        return (_CONTROL_SAID.get(what)
                or (CONTROLS.get(what) or {}).get("label")
                or what or "Control"), ""
    if verb == "set_phone_state":
        return (f"Mark phone {p.get('serial', '?')} "
                f"{p.get('state') or 'unused'}"), ""
    if verb == "clear_tries":
        return f"Clear tries on {p.get('serial', '?')}", ""
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


def _sub(text: str, tail: str = "") -> str:
    """One dim line under a request: what a command did to one thing."""
    return (f'<tr class="subrow"><td></td><td colspan="5" class="dim">'
            f'{text}</td><td>{tail}</td></tr>')


def _stuck(row: dict) -> bool:
    started = _as_dt(row.get("executed_at"))
    if started is None or row.get("status") != "running":
        return False
    now = datetime.datetime.now(started.tzinfo)
    return (now - started).total_seconds() > STUCK_AFTER


def _put_back(user: dict, row: dict) -> str:
    """"Put it back" on a done remove: the row the verb kept in the
    request's detail, offered again as an add."""
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    kept = detail.get("removed")
    if (row.get("verb") != "remove_proxy" or row.get("status") != "done"
            or not isinstance(kept, dict) or not kept.get("raw")
            or not _may(user, "may_add_proxy")):
        return ""
    return (f'<form method="post" class="inline" action="/pools/proxy/restore">'
            f'{_csrf(user)}<input type="hidden" name="name" '
            f'value="{esc(str(kept.get("name") or ""))}">'
            f'<input type="hidden" name="raw" value="{esc(str(kept["raw"]))}">'
            f'<button class="quiet">Put it back</button></form>')


def requests_page(rows: list[dict], user: dict, said: str = "", *,
                  counts: dict | None = None, view: str = "",
                  mine: bool = False, page: int = 1, pages: int = 1,
                  more: bool = False, hi: int = 0,
                  progress: dict | None = None) -> str:
    """The queue, newest first: what was asked, in words, by whom, what
    became of it - and under a command that works several phones, one
    line per phone, with the phone's latest captured log line while it
    runs (`progress`, by serial). `hi` is the row a banner pointed at.
    Refreshes itself only while something is pending."""
    counts = counts or {}
    progress = progress or {}
    body = _said(said, _SAID)
    can_stop = _may(user, "may_login_accounts")
    is_admin = user.get("role") == "admin"
    keep = "&mine=1" if mine else ""
    pending = any(str(r["status"]) in _PENDING for r in rows)
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
    if pending:
        top += '<span class="live">live</span>'
    head = ("<tr><th>#</th><th>what</th><th>by</th><th>asked</th>"
            "<th>state</th><th>result / progress</th><th></th></tr>")
    lines = []
    for r in rows:
        status = str(r["status"])
        head_text, aside = describe(str(r["verb"]), r.get("payload") or {})
        what = esc(head_text) + (f' <span class="dim">— {esc(aside)}</span>'
                                 if aside else "")
        action = _put_back(user, r)
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
        if _stuck(r):
            result += ('<br><span class="dim">stuck? the pass closes it '
                       'after two build budgets</span>')
        lit = ' class="hi"' if hi and int(r["id"]) == hi else ""
        lines.append(
            f'<tr{lit}><td class="muted">{r["id"]}</td><td>{what}</td>'
            f'<td class="muted">{esc(str(r["requested_by"]))}</td>'
            f'<td class="muted">{_clock(r["requested_at"])}</td>'
            f'<td><span class="badge {esc(status)}">{esc(status)}</span></td>'
            f'<td>{result}</td><td>{action}</td></tr>')
        detail = r.get("detail") if isinstance(r.get("detail"), dict) else {}
        for ph in detail.get("phones") or []:
            serial = str(ph.get("serial") or "")
            stop = ""
            if status == "running" and can_stop and ph.get("ok") is None:
                stop = (f'<form method="post" class="inline" '
                        f'action="/phones/{esc(serial)}/stop">'
                        f'{_csrf(user)}<button class="quiet warn">Stop this '
                        f'one</button></form>')
            line = progress.get(serial) if ph.get("ok") is None else None
            if line and status == "running":
                step = _progress(line)
            else:
                step = esc("is ready" if ph.get("ok") else
                           f"failed: {ph.get('status')}"
                           if ph.get("ok") is False
                           else str(ph.get("status") or "working"))
            lines.append(
                f'<tr class="subrow"><td></td><td colspan="4" class="mono dim">'
                f'↳ {_serial_link(serial)} — '
                f'{esc(str(ph.get("account") or ""))}</td>'
                f'<td class="dim">{step}'
                + (f' — {_span(ph.get("seconds"))}' if ph.get("seconds")
                   else "")
                + f'</td><td>{stop}</td></tr>')
        # What an add or a login could not do, one line each: the
        # sentence counts them, these say which.
        for text in detail.get("refused") or []:
            lines.append(_sub(f"↳ {esc(str(text))}"))
        for who in detail.get("skipped") or []:
            lines.append(_sub(f"↳ {esc(str(who))}: already in the pool"))
        for who in detail.get("unpaired") or []:
            lines.append(_sub(f"↳ {esc(str(who))}: no warm phone for it "
                              f"yet - press again once one is"))
    body = (f'<div class="top"><h2>Requests</h2>{top}</div>' + body)
    if not rows:
        body += ('<p class="muted">Nothing has been asked yet.</p>'
                 if not view and page == 1 else
                 f'<p class="muted">No {esc(view + " ") if view else ""}'
                 f'requests{" on this page" if page > 1 else ""}.</p>')
    else:
        body += (f'<div class="panel"><table>{head}{"".join(lines)}</table>'
                 f'<p class="dim">every command anyone gives lands here - '
                 f'including the instant ones - and stays as the record</p>'
                 + _pager(f"/requests?view={view}{keep}", page, pages, more)
                 + '</div>')
    return page_("Requests", body, user=user, here="/requests",
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


#: Each permission as the one or two words its chip says in the listing,
#: keyed by column. PERMISSIONS itself keeps its (column, label, hint)
#: shape - the editor's ticks read that; this is only the short form.
_PERMISSION_SHORT = {
    "may_add_gmail": "add gmail",
    "may_add_gpt": "add gpt",
    "may_add_proxy": "add proxies",
    "may_login_accounts": "log in",
    "may_change_proxy": "change proxy",
    "may_take_phones": "take phones",
}

#: The listing's colour for each role: admins violet, operators blue.
_ROLE_BADGE = {"admin": "manual", "operator": "info"}


def _avatar(name: str) -> str:
    return f'<span class="avatar">{esc(str(name or "?")[:1])}</span>'


def _may_cell(u: dict, permissions: tuple) -> str:
    """What one person may do, as the listing says it: a sentence for an
    admin or a deactivated person, chips for an operator."""
    if not u.get("active"):
        return ('<span class="dim">kept for the record - their requests '
                'still carry the name</span>')
    if u.get("role") == "admin":
        return "everything, including the service controls"
    chips = [f'<span class="badge">'
             f'{esc(_PERMISSION_SHORT.get(col, label))}</span>'
             for col, label, _ in permissions if u.get(col)]
    return " ".join(chips) or '<span class="dim">nothing yet</span>'


def users_page(users: list[dict], selected: dict | None, user: dict,
               permissions: tuple, said: str = "",
               error: str = "") -> str:
    """Everyone who can sign in, and an editor for one of them.

    The editor's form is the whole permission model made visible: role,
    sight, six ticks. Nothing here shows or accepts a password - creating
    or resetting mints a one-time one that the next page shows exactly
    once."""
    csrf = esc(user.get("csrf", ""))
    active = sum(1 for u in users if u.get("active"))
    body = (f'<div class="top"><h2>Users</h2><span class="status">'
            f'{active} can sign in · admin only</span></div>')
    if error:
        body += f'<p class="err">{esc(error)}</p>'
    note = _USERS_SAID.get(said, "")
    if note:
        body += f'<p class="said">{esc(note)}</p>'
    head = ("<tr><th>user</th><th>role</th><th>sees</th><th>may</th>"
            "<th>last seen</th><th></th></tr>")
    lines = []
    for u in users:
        state = "" if u["active"] else ' <span class="badge">deactivated</span>'
        seen = _when(u["last_login_at"]) if u.get("last_login_at") else "never"
        chosen = selected is not None and selected["id"] == u["id"]
        klass = "" if u["active"] else ' class="off"'
        lines.append(
            f"<tr{klass}>"
            f"<td>{_avatar(u['username'])}"
            f"<b style=\"font-weight:500;color:var(--bright)\">"
            f"{esc(u['username'])}</b>{state}</td>"
            f"<td><span class=\"badge {_ROLE_BADGE.get(u['role'], '')}\">"
            f"{esc(u['role'])}</span></td><td>{esc(u['sees'])}</td>"
            f"<td class=\"muted\">{_may_cell(u, permissions)}</td>"
            f"<td class=\"muted\">{seen}</td>"
            f"<td class=\"act\"><a class=\"btn quiet\" href=\"/users?id={u['id']}\">"
            f"{'editing' if chosen else 'edit'}</a></td></tr>")
    listing = (f'<div class="panel wrap"><table>{head}{"".join(lines)}'
               f'</table><p class="dim">users are deactivated, never deleted '
               f'- History and Requests keep naming them</p></div>')

    def tick_grid(current: dict) -> str:
        return '<div class="ticks">' + "".join(
            _tick(col, bool(current.get(col)), label, hint)
            for col, label, hint in permissions) + "</div>"

    editor = ""
    if selected is not None:
        u = selected
        editor = (
            f'<div class="panel"><h3>{_avatar(u["username"])}'
            f'{esc(u["username"])}'
            f'<span class="badge {_ROLE_BADGE.get(u["role"], "")}">'
            f'{esc(u["role"])}</span>'
            + ("" if u["active"] else '<span class="badge">deactivated</span>')
            + f'</h3>'
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
            f'<span class="hint">asks first, then shows a one-time password '
            f'once and signs them out everywhere</span></form></div>')

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
    "auto": "Manual login is off: accounts log in on their own on the next "
            "pass, nothing to press.",
    "none": "Tick at least one account first.",
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


#: A failure token the way a person says it in a tally: "22 captcha".
#: Anything not listed falls back to the token with its underscores
#: turned to spaces, so a new reason still reads as words.
_REASON_WORDS = {
    "captcha_shown": "captcha",
    "phone_verification_required": "phone verification",
    "wrong_2fa_code": "wrong 2fa",
    "no_authenticator": "no authenticator",
    "no_authenticator_option": "no authenticator option",
    "wrong_password": "wrong password",
    "no_recovery_email": "no recovery address",
    "email_code_required": "email code needed",
    "verification_blocked": "verification blocked",
    "account_disabled": "account disabled",
    "sign_in_refused": "sign-in refused",
    "email_not_found": "address unknown",
    "password_changed": "password changed",
}

#: How many queued rows the active view shows before it folds the rest
#: behind "+ N more".
QUEUED_SHOWN = 12
#: A refused 2fa secret is the seller's fault and is coloured as one; the
#: other reasons are Google's mood and are amber.
_BLAME_RED = {"wrong_2fa_code"}


def _reason_word(status: str) -> str:
    return _REASON_WORDS.get(status, (status or "?").replace("_", " "))


def _reason_badge(status: str) -> str:
    klass = "bad" if status in _BLAME_RED else "attn"
    return f'<span class="badge {klass}">{esc(status)}</span>'


def _seller_pick(known: list, current: str = "") -> str:
    """A select of the sellers the tab knows, plus a free box for a new
    one. The box wins when both are filled - a new name is typed on
    purpose, a select is often left where it was."""
    listed = [str(k) for k in (known or []) if k]
    chosen = current if current in listed else ""
    options = ['<option value="">— seller —</option>'] + [
        f'<option value="{esc(k)}"{" selected" if k == chosen else ""}>'
        f'{esc(k)}</option>' for k in listed]
    typed = "" if chosen else current
    return (f'<select name="seller">{"".join(options)}</select>'
            f'<input name="new_seller" placeholder="or a new seller" '
            f'size="16" value="{esc(typed)}">')


def _pager(base: str, page: int, pages: int, more: bool) -> str:
    """'page N of M' with newer/older links; `base` already carries the
    view and its filters, so only page= is appended."""
    nav = []
    if page > 1:
        nav.append(f'<a href="{base}&page={page - 1}">← newer</a>')
    nav.append(f'<span class="dim">page {page} of {max(pages, page)}</span>')
    if more:
        nav.append(f'<a href="{base}&page={page + 1}">older →</a>')
    return (f'<div class="row"><span class="right"></span>'
            f'{" ".join(nav)}</div>')


def _gmail_add_panel(user: dict, known: list) -> str:
    """Two ways in: a paste of the seller's sheet, or one account typed
    field by field. Both post to the same preview, so both are judged
    by the same validation and confirmed on the same page."""
    if not _may(user, "may_add_gmail"):
        if not user.get("mutations"):
            return ""
        return ('<div class="panel"><h3>Add gmails</h3>'
                '<p class="dim">Adding gmails needs the add-gmails '
                'permission - ask an admin</p></div>')
    paste = (
        '<div class="panel"><h3>Add gmails <span class="n">paste from the '
        'seller\'s sheet</span></h3>'
        '<form method="post" action="/pools/gmail/preview" class="field">'
        f'{_csrf(user)}'
        '<textarea name="pasted" placeholder="paste straight from the '
        'seller\'s sheet - one account per line; tab or comma between '
        'the columns"></textarea>'
        '<p class="dim">the address is found by its @, the secret by its '
        'shape, the password is what remains; nothing is added until '
        'you confirm the preview</p>'
        f'<div class="row">{_seller_pick(known)}'
        '<span class="dim">purchase date stamps automatically</span>'
        '<span class="right"></span><button>Preview</button></div>'
        '</form></div>')
    one = (
        '<div class="panel"><h3>One by one</h3>'
        '<form method="post" action="/pools/gmail/preview" class="field">'
        f'{_csrf(user)}'
        '<input name="address" placeholder="address" autocomplete="off">'
        '<input name="password" placeholder="password" autocomplete="off">'
        '<input name="second" placeholder="authenticator secret or '
        'recovery address" autocomplete="off">'
        f'<div class="row">{_seller_pick(known)}'
        '<span class="right"></span><button>Preview</button></div>'
        '<p class="dim">the same preview judges it - a typo is caught '
        'before anything is queued</p>'
        '</form></div>')
    return f'<div class="grid2">{paste}{one}</div>'


def _gmail_phone_badge(r: dict) -> str:
    if r.get("status") == "in_use":
        return '<span class="badge in_use">signing in</span>'
    status = r.get("phone_status") or "ready"
    return (f'<span class="badge {_PHONE_CLASS.get(status, "")}">'
            f'{esc(_phone_word(status))}</span>')


def gmail_pool_page(data: dict, user: dict, said: str = "", *,
                    advice=None, show_all: bool = False) -> str:
    """Active = on a phone / queued; Used and Errored are the archives.
    Errored carries seller, purchase and failure dates and a link to the
    plain-text list of addresses - the one the seller is asked to
    refund. `advice` turns a status token into the sentence a person
    would say (app passes failures.verdict; pages never import it)."""
    c = data["counts"]
    view = data["view"]
    known = list(data.get("known_sellers") or [])
    pills = "".join(
        (f'<span>{v.capitalize()} · {n}</span>' if view == v else
         f'<a href="/pools/gmail?view={v}">{v.capitalize()} · {n}</a>')
        for v, n in {"active": c["queued"] + c["on_phone"],
                     "used": c["used"], "errored": c["errored"]}.items())
    body = (f'<div class="top"><h2>Gmail Pool</h2><div class="pills">{pills}'
            f'</div><span class="status">{c["queued"]} queued covers the '
            f'next {c["queued"]} builds <span class="badge">'
            f'{_plural(len(known), "seller")}</span></span></div>')
    body += _said(said, _POOL_SAID)

    if view == "used":
        rows = "".join(
            f"<tr><td>{esc(r['address'])}</td><td>{_serial_link(r['serial'])}"
            f"</td><td class=\"muted\">{_when(r['used_at'])}</td>"
            f"<td class=\"muted\">{esc(r['seller'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['note'] or '')}</td></tr>"
            for r in data["rows"])
        body += (f'<div class="panel"><h3>Used — {c["used"]}</h3><table>'
                 f'<tr><th>address</th><th>phone</th><th>used</th>'
                 f'<th>seller</th><th>note</th></tr>{rows}</table>'
                 + _pager("/pools/gmail?view=used", int(data.get("page") or 1),
                          int(data.get("pages") or 1), bool(data.get("more")))
                 + '</div>')
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
                             f'{_q(s["seller"] or "")}">{esc(name)} · '
                             f'{s["c"]}</a>')
        tally = " · ".join(
            f'{t["c"]} {esc(_reason_word(str(t["status"])))}'
            for t in data.get("reasons") or [])
        total = int(data.get("total") or len(data["rows"]))

        def happened(r: dict) -> str:
            said_ = advice(str(r["status"])) if advice else ""
            return said_ or str(r.get("note") or "")

        rows = "".join(
            f"<tr><td>{esc(r['address'])}</td>"
            f"<td class=\"muted\">{esc(r['seller'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['purchased_on'] or '')}</td>"
            f"<td class=\"muted\">{_when(r['updated_at'])}</td>"
            f"<td>{_reason_badge(str(r['status']))}</td>"
            f"<td class=\"muted\">{esc(happened(r))}</td></tr>"
            for r in data["rows"])
        refund = (f'/pools/gmail/refund.txt?seller={_q(chosen)}' if chosen
                  else '/pools/gmail/refund.txt')
        base = f"/pools/gmail?view=errored&seller={_q(chosen)}"
        body += (f'<div class="row"><div class="chips">{"".join(chips)}'
                 f'</div><span class="dim right">{tally}</span></div>'
                 f'<div class="panel"><table><tr><th>address</th>'
                 f'<th>seller</th><th>purchased</th><th>failed on</th>'
                 f'<th>reason</th><th>what happened</th></tr>{rows}</table>'
                 f'<div class="row"><p class="dim">an errored address never '
                 f're-enters the pool - this list exists so the seller pays '
                 f'it back</p><a class="btn quiet" href="{refund}">'
                 f'Addresses for refund ({total}) →</a></div>'
                 + _pager(base, int(data.get("page") or 1),
                          int(data.get("pages") or 1), bool(data.get("more")))
                 + '</div>')
        return page("Gmail Pool", body, user=user, here="/pools/gmail")

    body += _gmail_add_panel(user, known)

    on_phone = "".join(
        f"<tr><td>{esc(r['address'])}</td><td>{_serial_link(r['serial'])}</td>"
        f"<td>{_gmail_phone_badge(r)}</td>"
        f"<td class=\"muted\">{_when(r['updated_at'])}</td>"
        f"<td class=\"muted\">{esc(r['seller'] or '')}</td></tr>"
        for r in data["on_phone"])
    waiting = list(data["queued"])
    shown = waiting if show_all else waiting[:QUEUED_SHOWN]
    queued = "".join(
        f"<tr><td>{esc(r['address'])}</td>"
        f"<td class=\"muted\">{esc(r['seller'] or '')}</td>"
        f"<td class=\"muted\">{esc(r['purchased_on'] or '')}</td>"
        f"<td>{_kind_2fa(r)}</td></tr>"
        for r in shown)
    more = ""
    if len(waiting) > len(shown):
        more = (f'<p class="dim"><a href="/pools/gmail?all=1">'
                f'+ {len(waiting) - len(shown)} more</a></p>')
    body += (f'<div class="panel"><h3>On a phone — {c["on_phone"]}</h3>'
             f'<table><tr><th>address</th><th>phone</th><th>state</th>'
             f'<th>since</th><th>seller</th></tr>{on_phone}</table></div>'
             f'<div class="panel"><h3>Queued — {c["queued"]}'
             f' <span class="dim">the keeper claims from the top</span></h3>'
             f'<table><tr><th>address</th><th>seller</th><th>purchased</th>'
             f'<th>2fa</th></tr>{queued}</table>{more}</div>')
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

#: How many rows each of the Proxy Pool's side lists shows before it folds
#: the rest behind "+ N more" (?all=1 unfolds every list at once).
PROXY_SHOWN = 8

#: A note longer than this is clipped in the table; the rest rides in the
#: cell's title, so a hover reads it whole.
NOTE_CHARS = 60


def _proxy_bucket(status) -> str:
    """The chip a status word files under: free / on_phone / claimed /
    needs_new_ip / dead, or `other` for a word the pool never wrote."""
    key = _PROXY_STATE.get((status or "").lower(), "info")
    return {"free": "free", "on_phone": "on_phone", "claimed": "claimed",
            "attn": "needs_new_ip", "dead": "dead"}.get(key, "other")


def _last_test(tests: dict, name: str) -> str:
    """'42m ago · ok', '2h ago · dead', or 'never' - off the stamp the
    pass kept for this name when it last tested the exit."""
    stamp = (tests or {}).get(name or "")
    if not isinstance(stamp, dict) or not stamp.get("at"):
        return "never"
    ago = _ago(stamp.get("at")) or "?"
    return f"{ago} · {'ok' if stamp.get('ok') else 'dead'}"


def _clip(text, limit: int = NOTE_CHARS) -> str:
    """Escaped, cut at `limit` with the whole text in a title attribute
    when it was longer."""
    text = str(text or "")
    if len(text) <= limit:
        return esc(text)
    return (f'<span title="{esc(text)}">{esc(text[:limit - 1].rstrip())}…'
            f'</span>')


def _fold(rows: list, show_all: bool) -> tuple[list, str]:
    """The rows to show and the '+ N more' line (empty when all fit)."""
    shown = rows if show_all else rows[:PROXY_SHOWN]
    if len(rows) <= len(shown):
        return shown, ""
    return shown, (f'<p class="dim"><a href="/pools/proxy?all=1">'
                   f'+ {len(rows) - len(shown)} more</a></p>')


def _proxy_add_panel(user: dict) -> str:
    """Two ways in: the vendor's list pasted, or one exit typed field by
    field. Both post to the same preview, so both are judged by the same
    reader and confirmed on the same page."""
    if not _may(user, "may_add_proxy"):
        if not user.get("mutations"):
            return ""
        return (f'<div class="panel"><h3>Add proxies</h3>'
                f'{_need(user, "may_add_proxy", "Adding proxies")}</div>')
    paste = (
        '<div class="panel"><h3>Add proxies <span class="n">paste from the '
        'vendor</span></h3>'
        '<form method="post" action="/pools/proxy/preview" class="field">'
        f'{_csrf(user)}'
        '<textarea name="pasted" placeholder="host:port:user:pass, one '
        'per line - or a name first, then the string"></textarea>'
        '<p class="dim">names are handed out in order (SX43, SX44 …) '
        'unless a name column is pasted; each is tested by the pass '
        'before it joins the pool</p>'
        '<div class="row"><span class="right"></span><button>Preview'
        '</button></div></form></div>')
    one = (
        '<div class="panel"><h3>One by one</h3>'
        '<form method="post" action="/pools/proxy/preview" class="field">'
        f'{_csrf(user)}'
        '<input name="host" placeholder="host" autocomplete="off">'
        '<input name="port" placeholder="port" autocomplete="off" size="6">'
        '<input name="username" placeholder="user" autocomplete="off">'
        '<input name="password" placeholder="pass" autocomplete="off">'
        '<input name="name" placeholder="name (optional - SX43)" '
        'autocomplete="off">'
        '<div class="row"><span class="right"></span><button>Preview'
        '</button></div>'
        '<p class="dim">the same preview judges it - a typo is caught '
        'before anything is queued</p>'
        '</form></div>')
    return f'<div class="grid2">{paste}{one}</div>'


def _proxy_button(user: dict, action: str, name: str, label: str,
                  klass: str = "") -> str:
    """One quiet button posting a proxy's name, or nothing when this
    person may not press it."""
    if not _may(user, "may_add_proxy"):
        return ""
    return (f'<form method="post" action="{esc(action)}" class="inline">'
            f'{_csrf(user)}<input type="hidden" name="name" '
            f'value="{esc(name or "")}"><button class="quiet {klass}">'
            f'{esc(label)}</button></form>')


def _held_row(user: dict, u: dict) -> str:
    who = (f"{esc(str(u.get('host', '')))}:{esc(str(u.get('port', '')))} "
           f"({esc(str(u.get('username', '')))})")
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{esc(str(u.get(k, "")))}">'
        for k in ("host", "port", "username"))
    buttons = ""
    if _may(user, "may_add_proxy"):
        buttons = (
            f'<form method="post" action="/pools/proxy/adopt" class="inline">'
            f'{_csrf(user)}{hidden}<button class="quiet">Add to pool'
            f'</button></form> '
            f'<form method="post" action="/pools/proxy/ignore" '
            f'class="inline">{_csrf(user)}{hidden}<button class="quiet">'
            f'Ignore</button></form>')
    return f'<tr><td class="mono">{who}</td><td>{buttons}</td></tr>'


def proxy_pool_page(data: dict, user: dict, said: str = "",
                    state: str = "", q: str = "", *, show_all: bool = False,
                    show_ignored: bool = False) -> str:
    """The Proxy Pool: the rows that need a hand first (a new IP, held by
    GeeLark, dead), then every row. `tests` and `ignored` come from what
    the pass keeps in service_state, merged in by the caller; the header
    says when the free ones were last tested off the newest stamp."""
    c = data["counts"]
    tests = data.get("tests") or {}
    ignored = list(data.get("ignored") or [])
    free_names = [r["name"] for r in data["rows"]
                  if _proxy_bucket(r["status"]) == "free" and r["name"]]
    newest = max((float(tests[n]["at"]) for n in free_names
                  if isinstance(tests.get(n), dict) and tests[n].get("at")),
                 default=None)
    tested = (f"free ones tested {_ago(newest)}" if newest
              else "free ones not tested yet")
    body = (f'<div class="top"><h2>Proxy Pool</h2>'
            f'<span class="mono muted">{c["all"]} rows — '
            f'{c.get("free", 0)} free · {c.get("on_phone", 0)} on phones · '
            f'{c.get("needs_new_ip", 0)} need a new IP · '
            f'{c.get("dead", 0)} dead</span>'
            f'<span class="dim">{esc(tested)}</span>')
    if _may(user, "may_add_proxy"):
        body += (f'<form method="post" action="/pools/proxy/test-all" '
                 f'class="inline" style="margin-left:auto">{_csrf(user)}'
                 f'<button class="quiet">Test all now</button></form>')
    body += "</div>" + _said(said, _POOL_SAID)
    body += _proxy_add_panel(user)

    panels = []
    if data["needs_new_ip"]:
        shown, more = _fold(list(data["needs_new_ip"]), show_all)
        rows = "".join(
            f"<tr><td>{esc(r['name'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['host'] or '')} — "
            f"{_clip(r['note'])}</td><td>"
            + _proxy_button(user, "/pools/proxy/free", r["name"],
                            "IP changed — mark free", "warn")
            + "</td></tr>" for r in shown)
        panels.append(f'<div class="panel warn"><h3>Needs a new IP — '
                      f'{len(data["needs_new_ip"])}</h3><table>{rows}'
                      f'</table>{more}<p class="dim">change the IP in the '
                      f'vendor\'s panel first; marking free re-tests it '
                      f'before any build takes it</p>'
                      f'{_need(user, "may_add_proxy", "Marking free")}'
                      f'</div>')
    if show_ignored:
        rows = "".join(f'<tr><td class="mono">{esc(who)}</td></tr>'
                       for who in ignored)
        panels.append(f'<div class="panel"><h3>Ignored — {len(ignored)}'
                      f'</h3><table>{rows}</table><p class="dim">held by '
                      f'GeeLark and left there unreported (host:port:user); '
                      f'the list lives in service_state under '
                      f'ignored_proxies. <a href="/pools/proxy">Back to the '
                      f'held list</a></p></div>')
    elif data["unlisted"] or ignored:
        shown, more = _fold(list(data["unlisted"]), show_all)
        rows = "".join(_held_row(user, u) for u in shown)
        seen = (f'<p class="dim">Ignored ({len(ignored)}) · '
                f'<a href="/pools/proxy?ignored=1">see them</a></p>'
                if ignored else "")
        panels.append(f'<div class="panel"><h3>Held by GeeLark, not in the '
                      f'pool — {len(data["unlisted"])}</h3><table>{rows}'
                      f'</table>{more}<p class="dim">reported, never added on '
                      f'its own - which of them belong here is your call; '
                      f'Ignore stops one being reported</p>{seen}'
                      f'{_need(user, "may_add_proxy", "Adding or ignoring")}'
                      f'</div>')
    if data["dead"]:
        shown, more = _fold(list(data["dead"]), show_all)
        rows = "".join(
            f"<tr><td>{esc(r['name'] or '')}</td>"
            f"<td class=\"muted\">{esc(r['host'] or '')}:"
            f"{esc(str(r['port'] or ''))} — since {_when(r['updated_at'])}"
            f" · last test {esc(_last_test(tests, r['name']))}</td><td>"
            + _proxy_button(user, "/pools/proxy/test", r["name"],
                            "Test again")
            + "</td></tr>" for r in shown)
        panels.append(f'<div class="panel bad"><h3>Dead — '
                      f'{len(data["dead"])}</h3><table>{rows}</table>{more}'
                      f'<p class="dim">kept, never removed on its own - '
                      f'revive it at the vendor and test again</p>'
                      f'{_need(user, "may_add_proxy", "Testing")}</div>')
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
        bucket = _proxy_bucket(r["status"])
        if state and bucket != state:
            continue
        hay = f"{r['name']} {r['host']} {r['serial']}".lower()
        if q and q.lower() not in hay:
            continue
        shown.append(r)

    def phone_cell(r: dict) -> str:
        link = _serial_link(r["serial"])
        if r["serial"] and _proxy_bucket(r["status"]) in ("on_phone",
                                                          "claimed"):
            return f'<span class="dim">on phone</span> {link}'
        return link

    def buttons(r: dict) -> str:
        bucket = _proxy_bucket(r["status"])
        if bucket == "free":
            return (_proxy_button(user, "/pools/proxy/test", r["name"], "Test")
                    + " " + _proxy_button(user, "/pools/proxy/remove",
                                          r["name"], "Remove", "bad"))
        if bucket in ("dead", "needs_new_ip"):
            return _proxy_button(user, "/pools/proxy/remove", r["name"],
                                 "Remove", "bad")
        return ""

    rows = "".join(
        f"<tr><td>{esc(r['name'] or '')}</td>"
        f"<td class=\"muted\">{esc(r['host'] or '')}:"
        f"{esc(str(r['port'] or ''))}</td>"
        f"<td><span class=\"badge "
        f"{_PROXY_STATE.get((r['status'] or '').lower(), 'info')}\">"
        f"{esc(r['status'] or 'free')}</span></td>"
        f"<td class=\"muted mono\">{esc(r['last_exit_ip'] or '')}</td>"
        f"<td>{phone_cell(r)}</td>"
        f"<td class=\"muted num\">{esc(str(r['times_used'] or 0))}</td>"
        f"<td class=\"muted\">{esc(_last_test(tests, r['name']))}</td>"
        f"<td class=\"muted\">{_clip(r['note'])}</td>"
        f"<td>{buttons(r)}</td></tr>" for r in shown)
    body += (f'<div class="panel"><div class="row"><h3>All proxies</h3>'
             f'<div class="chips">{"".join(chips)}</div>'
             f'<form method="get" action="/pools/proxy" class="inline" '
             f'style="margin-left:auto">'
             f'<input type="hidden" name="state" value="{esc(state)}">'
             f'<input name="q" value="{esc(q)}" placeholder="name, host or '
             f'phone" size="22"></form></div>'
             f'<table><tr><th>name</th><th>host</th><th>state</th><th>exit'
             f'</th><th>phone</th><th>uses</th><th>last test</th><th>note'
             f'</th><th></th></tr>{rows}</table>'
             f'{_need(user, "may_add_proxy", "Testing or removing")}</div>')
    return page("Proxy Pool", body, user=user, here="/pools/proxy")


def _gpt_add_panel(user: dict, form: dict | None, error: str) -> str:
    """Two ways in, like the Gmail Pool: a paste of several accounts that
    goes through a preview, and one typed by hand that is judged on the
    spot - refused, it comes back filled in with the reason beside it."""
    if not _may(user, "may_add_gpt"):
        hint = _need(user, "may_add_gpt", "adding accounts")
        return (f'<div class="panel"><h3>Add accounts</h3>{hint}</div>'
                if hint else "")
    form = form or {}
    paste = (
        '<div class="panel"><h3>Paste several <span class="n">address, '
        'password, secret per line</span></h3>'
        '<form method="post" action="/pools/gpt/preview" class="field">'
        f'{_csrf(user)}'
        '<textarea name="pasted" placeholder="one account per line - '
        'address, password and the 2FA secret if it has one; tab, comma '
        'or a space between them"></textarea>'
        '<p class="dim">the address is found by its @, the secret by its '
        'shape, the password is what remains; nothing is added until '
        'you confirm the preview</p>'
        '<div class="row"><span class="right"></span>'
        '<button>Preview</button></div></form></div>')
    said = (f'<p class="err">{esc(error)}</p>' if error else "")
    one = (
        '<div class="panel"><h3>Add an account by hand</h3>'
        '<form method="post" action="/pools/gpt/add" class="field">'
        f'{_csrf(user)}{said}'
        f'<input name="address" placeholder="email address" '
        f'autocomplete="off" value="{esc(str(form.get("address") or ""))}">'
        f'<input name="password" placeholder="password" autocomplete="off" '
        f'value="{esc(str(form.get("password") or ""))}">'
        f'<input name="secret" placeholder="2FA secret (optional)" '
        f'autocomplete="off" value="{esc(str(form.get("secret") or ""))}">'
        '<div class="row"><label><input type="checkbox" name="email_code" '
        f'value="1"{" checked" if form.get("email_code_only") else ""}> '
        'email-code only</label><span class="right"></span>'
        '<button>Add</button></div>'
        '<p class="dim">validated the way the sheet rows are - a bad '
        '2FA secret or a malformed address is refused here, not '
        'discovered on a phone</p></form></div>')
    return f'<div class="grid2">{paste}{one}</div>'


def _source_badge(r: dict) -> str:
    source = str(r.get("source") or "manual")
    if source == "panel":
        return '<span class="badge panel">panel</span>'
    who = r.get("added_by_name")
    return (f'<span class="badge manual">manual'
            f'{(" · " + esc(str(who))) if who else ""}</span>')


def gpt_pool_page(data: dict, user: dict, said: str = "", *,
                  explain=None, manual_login: bool = False,
                  form: dict | None = None, error: str = "") -> str:
    """Active = the accounts waiting for a phone, by where they came
    from, then the ones a run set aside for a person; Delivered = the
    archive. `explain(status)` turns a set-aside status into (what was
    seen, what to do) - app passes failures.verdict; pages never import
    it. `form` and `error` are the by-hand add coming back refused."""
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
        q = str(data.get("q") or "")
        rows = "".join(
            f"<tr><td>{esc(r['address'])}</td><td>{_source_badge(r)}</td>"
            f"<td>{_serial_link(r['serial'])}</td>"
            f"<td class=\"muted\">{_when(r['updated_at'])}</td>"
            f"<td class=\"muted\">{esc(r['note'] or '')}</td></tr>"
            for r in data["rows"])
        total = int(data.get("total") if data.get("total") is not None
                    else c["delivered"])
        count = (f'{total} of {c["delivered"]} delivered accounts match '
                 f'"{esc(q)}"' if q else
                 f'{c["delivered"]} delivered accounts - the panel pulls '
                 f'each one\'s fate from here')
        body += (f'<div class="panel"><table><tr><th>address</th><th>source'
                 f'</th><th>phone</th><th>delivered</th><th>note</th></tr>'
                 f'{rows}</table><div class="row"><span class="dim">{count}'
                 f'</span><a class="btn quiet right" '
                 f'href="/pools/gpt/delivered.csv?q={_q(q)}">Export CSV</a>'
                 f'</div>'
                 + _pager(f"/pools/gpt?view=delivered&q={_q(q)}",
                          int(data.get("page") or 1),
                          int(data.get("pages") or 1), bool(data.get("more")))
                 + '</div>')
        return page("Gpt Pool", body, user=user, here="/pools/gpt")

    body += _gpt_add_panel(user, form, error)

    can_login = manual_login and _may(user, "may_login_accounts")

    def account_rows(rows: list, by: bool) -> str:
        out = []
        for r in rows:
            logging_in = r["status"] == "in_use"
            state = ('<span class="badge info">logging in — '
                     f'{_serial_link(r["serial"])}</span>'
                     if logging_in else
                     '<span class="badge warn">awaiting login</span>')
            who = (f"{esc(r.get('added_by_name') or 'sheet')} · "
                   f"{_when(r['created_at'])}" if by else _when(r["created_at"]))
            tick = ""
            if can_login:
                tick = ("<td></td>" if logging_in else
                        f'<td><input type="checkbox" name="addresses" '
                        f'value="{esc(r["address"])}"></td>')
            klass = ' class="muted"' if logging_in else ""
            out.append(f"<tr{klass}>{tick}<td>{esc(r['address'])}</td>"
                       f"<td class=\"muted\">{who}</td>"
                       f"<td>{_kind_2fa(r)}</td><td>{state}</td></tr>")
        return "".join(out)

    def tally(rows: list) -> str:
        waiting = sum(1 for r in rows if r["status"] != "in_use")
        busy = len(rows) - waiting
        return (f"{waiting} awaiting" + (f" · {busy} logging in" if busy
                                          else ""))

    th = "<th></th>" if can_login else ""
    tables = (f'<div class="panel"><h3><span class="badge panel">panel</span> '
              f'From the customer panel <span class="n">'
              f'{tally(data["panel"])}</span></h3>'
              f'<table><tr>{th}<th>address</th><th>received</th><th>2fa</th>'
              f'<th>state</th></tr>{account_rows(data["panel"], False)}'
              f'</table></div>'
              f'<div class="panel"><h3><span class="badge manual">manual'
              f'</span> Added by hand or from the sheet <span class="n">'
              f'{tally(data["manual"])}</span></h3>'
              f'<table><tr>{th}<th>address</th><th>added by</th><th>2fa</th>'
              f'<th>state</th></tr>{account_rows(data["manual"], True)}'
              f'</table>')
    if can_login:
        tables = (f'<form method="post" action="/accounts/login">{_csrf(user)}'
                  f'<input type="hidden" name="back" value="/pools/gpt">'
                  f'{tables}<div class="row"><span class="dim">each ticked '
                  f'account boots one warm phone and logs in there; '
                  f'progress lands in <a href="/requests">Requests</a></span>'
                  f'<button class="right">Log in selected</button></div>'
                  f'</div></form>')
    else:
        hint = _need(user, "may_login_accounts", "logging accounts in")
        if not manual_login:
            hint = ('<p class="dim">accounts log in on their own on the next '
                    'pass</p>')
        tables += f'{hint}</div>'
    body += tables

    if data["needs_human"]:
        n = len(data["needs_human"])

        def happened(r: dict) -> str:
            seen, advice = explain(str(r["status"])) if explain else ("", "")
            if not seen:
                return esc(str(r.get("note") or ""))
            return (esc(seen)
                    + (f' <span class="dim">— {esc(advice)}</span>'
                       if advice else ""))

        offer = _may(user, "may_add_gpt")
        rows = "".join(
            f"<tr><td>{esc(r['address'])}</td>"
            f"<td><span class=\"badge attn\">{esc(r['status'])}</span></td>"
            f"<td>{_source_badge(r)}</td>"
            f"<td>{happened(r)}</td><td>" + (
                f'<form method="post" action="/pools/gpt/offer" '
                f'class="inline">{_csrf(user)}<input type="hidden" '
                f'name="address" value="{esc(r["address"])}">'
                f'<button class="quiet warn">Offer again</button></form>'
                if offer else "") + "</td></tr>"
            for r in data["needs_human"])
        body += (f'<div class="panel warn"><h3>Needs a human <span class="n">'
                 f'{_plural(n, "account")}</span></h3><table><tr>'
                 f'<th>address</th><th>status</th><th>source</th>'
                 f'<th>what happened</th><th></th></tr>{rows}</table>'
                 f'{_need(user, "may_add_gpt", "offering accounts again")}'
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
        return '<span class="badge bad">already in the pool</span>'
    if row.get("error"):
        return f'<span class="badge bad">{esc(row["error"])}</span>'
    return '<span class="badge ok">ok</span>'


def _second_factor(row: dict) -> str:
    if row.get("recovery"):
        return "recovery"
    return "authenticator" if row.get("secret") else "—"


def gmail_preview(rows: list[dict], seller: str, user: dict,
                  idem: str, *, pasted: str = "",
                  sellers: list | None = None) -> str:
    """The verdicts, the confirm, and the paste kept in an editable box
    underneath - a typo is fixed there and previewed again, not pasted
    from scratch."""
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
            f'</span><span class="right"></span>'
            f'<a class="btn quiet" href="/pools/gmail">Back</a>'
            + (f'<button>Add {len(good)} (skip {len(rows) - len(good)})'
               f'</button>' if good else
               '<span class="badge bad">nothing to add</span>')
            + '</div></form>'
            f'<div class="panel"><h3>Edit and preview again</h3>'
            f'<form method="post" action="/pools/gmail/preview" class="field">'
            f'{_csrf(user)}<textarea name="pasted">{esc(pasted)}</textarea>'
            f'<div class="row">{_seller_pick(list(sellers or []), seller)}'
            f'<span class="right"></span>'
            f'<button class="quiet">Preview again</button></div>'
            f'</form></div>')
    return page("Gmail Pool — preview", body, user=user, here="/pools/gmail")


def gpt_preview(rows: list[dict], user: dict, idem: str, *,
                pasted: str = "") -> str:
    """The Gpt Pool's paste, judged row by row the way the by-hand form
    judges one; the good rows ride into the confirm as the same
    tab-separated text, and the paste stays in a box underneath."""
    good = [r for r in rows if not r.get("error") and not r.get("duplicate")]
    lines = "".join(
        f"<tr><td>{esc(r.get('address') or r.get('line', ''))}</td>"
        f"<td class=\"muted\">{'········' if r.get('password') else '—'}</td>"
        f"<td class=\"muted\">{'authenticator' if r.get('secret') else '—'}"
        f"</td><td>{_verdict_badge(r)}</td></tr>" for r in rows)
    carried = "\n".join(
        f"{r['address']}\t{r['password']}\t{r.get('secret') or ''}"
        for r in good)
    body = (f'<div class="top"><h2>Gpt Pool</h2><span class="status">'
            f'preview — nothing is added yet</span></div>'
            f'<div class="panel"><table><tr><th>address</th><th>password'
            f'</th><th>2fa</th><th>verdict</th></tr>{lines}</table></div>'
            f'<form method="post" action="/pools/gpt/add" class="panel">'
            f'{_csrf(user)}<input type="hidden" name="idem" value="{esc(idem)}">'
            f'<textarea name="rows" hidden>{esc(carried)}</textarea>'
            f'<div class="row"><span class="dim">each lands in the Gpt Info '
            f'tab as awaiting login</span><span class="right"></span>'
            f'<a class="btn quiet" href="/pools/gpt">Back</a>'
            + (f'<button>Add {len(good)} (skip {len(rows) - len(good)})'
               f'</button>' if good else
               '<span class="badge bad">nothing to add</span>')
            + '</div></form>'
            f'<div class="panel"><h3>Edit and preview again</h3>'
            f'<form method="post" action="/pools/gpt/preview" class="field">'
            f'{_csrf(user)}<textarea name="pasted">{esc(pasted)}</textarea>'
            f'<div class="row"><span class="right"></span>'
            f'<button class="quiet">Preview again</button></div>'
            f'</form></div>')
    return page("Gpt Pool — preview", body, user=user, here="/pools/gpt")


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


def _q(text) -> str:
    """A value the way it goes into a query string."""
    return quote(str(text or ""), safe="")


def _serial_link(serial) -> str:
    text = str(serial or "").strip()
    if not text:
        return '<span class="dim">—</span>'
    return f'<a href="/phones/{esc(text)}">{esc(text)}</a>'


def _breaker_words(pulse: dict) -> tuple[str, str]:
    """The breaker tile: 'N of 5 in a row' off the pulse's streak, 'open'
    in red once it tripped, 'armed' when the pass never counted."""
    count = pulse.get("breaker_count")
    limit = int(pulse.get("breaker_limit") or 5)
    if pulse.get("tripped"):
        streak = f" — {int(count)} of {limit}" if count is not None else ""
        return f"open{streak}", "bad"
    if count is None:
        return "armed", ""
    return (f'{int(count)} of {limit} <span class="dim">in a row</span>',
            "warn" if int(count) else "")


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
    tiles.append(("breaker", *_breaker_words(pulse)))
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


def _build_fields(detail: str) -> dict:
    """`ok=True gmail=x proxy=SX3 app=y` as a dict, the way build_finished
    writes its detail."""
    found = {}
    for part in str(detail or "").split():
        key, sep, value = part.partition("=")
        if sep:
            found[key] = value
    return found


_REQUEST_ID = re.compile(r"^#(\d+)\b")


def _link_request(text: str) -> str:
    """'#241 login_accounts: ...' with the number linked to its row."""
    hit = _REQUEST_ID.match(text)
    if hit is None:
        return esc(text)
    return (f'<a href="/requests?hi={hit.group(1)}">#{hit.group(1)}</a>'
            f'{esc(text[hit.end():])}')


def _event_what(r: dict, explain) -> str:
    """The 'what' cell as prose: a build that ended says who it signed
    in as, or what its status means; a request links its number; every
    other kind says its detail."""
    kind = str(r.get("kind") or "")
    detail = str(r.get("detail") or "")
    status = str(r.get("status") or "")
    if kind == "build_finished":
        fields = _build_fields(detail)
        if fields.get("ok") == "True":
            who = fields.get("app") or fields.get("gmail") or ""
            text = ("ready — signed in as " + esc(who) if who
                    else "ready")
        else:
            seen = (explain(status)[0] if explain and status else "") or ""
            text = esc(status) + (f" — {esc(seen)}" if seen else "")
        if r.get("seconds"):
            text += f' <span class="dim">· {int(r["seconds"])}s</span>'
        return text
    if kind == "request":
        return _link_request(detail)
    return esc(detail)


def _day_chips(day: str, kind: str, q: str) -> str:
    """'today' and 'all days' chips beside a date box; whichever the
    page is on is lit."""
    now = today()
    keep = f"kind={_q(kind)}&q={_q(q)}"
    chips = []
    for value, label in ((now, "today"), ("all", "all days")):
        lit = ' class="here"' if day == value else ""
        chips.append(f'<a href="/events?{keep}&day={_q(value)}"{lit}>'
                     f'{label}</a>')
    if day not in (now, "all"):
        chips.append(f'<span>{esc(day)}</span>')
    return "".join(chips)


def events_page(data: dict, user: dict, *, signals: dict | None = None,
                kind: str = "", q: str = "", day: str = "",
                explain=None) -> str:
    """The feed for one day (today unless asked otherwise), the pills'
    counts scoped to it, and the 'what' column in words. `explain` turns
    a build's status into what was seen (app passes failures.verdict)."""
    counts = data.get("counts") or {}
    day = day or str(data.get("day") or "") or today()
    keep = f"kind={_q(kind)}&q={_q(q)}&day={_q(day)}"
    pills = [("", "all")] + [(name, name) for name in
                             ("builds", "phones", "accounts", "breaker",
                              "requests", "stock", "passes")]
    chips = []
    for value, label in pills:
        n = counts.get(label if value else "all", 0)
        lit = ' class="here"' if value == kind else ""
        href = f"/events?kind={_q(value)}&q={_q(q)}&day={_q(day)}"
        chips.append(f'<a href="{href}"{lit}>{esc(label)} · {n}</a>')
    lines = []
    for r in data.get("rows") or []:
        run_id = str(r.get("run_id") or "")
        run = (f"{run_id}/{r['build']}" if r.get("build") else run_id)
        run_cell = (f'<a href="/logs?run={_q(run_id)}">{esc(run)}</a>'
                    if run_id else "—")
        lines.append(
            f'<tr><td class="muted">{_day(r.get("at"))} '
            f'{_clock(r.get("at"))}</td><td>{_event_badge(r)}</td>'
            f'<td class="muted">{run_cell}</td>'
            f'<td>{_serial_link(r.get("serial"))}</td>'
            f'<td>{_event_what(r, explain)}</td></tr>')
    page_n, pages = int(data.get("page") or 1), int(data.get("pages") or 1)
    nav = ""
    if pages > 1:
        prev = (f'<a href="/events?{keep}&page={page_n - 1}">← newer</a>'
                if page_n > 1 else "")
        nxt = (f'<a href="/events?{keep}&page={page_n + 1}">older →</a>'
               if page_n < pages else "")
        nav = (f'<div class="row dim">{prev}<span style="margin-left:auto">'
               f'page {page_n} of {pages}</span>{nxt}</div>')
    when = "today" if day == today() else ("any day" if day == "all"
                                           else esc(day))
    under = f"under {esc(kind)} " if kind else ""
    matching = f' matching "{esc(q)}"' if q else ""
    empty = (f'<tr><td colspan="5" class="muted">Nothing recorded {under}'
             f'{when}{matching} - <a href="/events?day=all">see every day'
             f'</a>.</td></tr>')
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
              f'value="{esc(kind)}">'
              f'<div class="chips">{_day_chips(day, kind, q)}</div>'
              f'<input type="date" name="day" '
              f'value="{esc(day) if day != "all" else ""}" '
              f'title="one day, in your zone">'
              f'<input name="q" value="{esc(q)}" '
              f'placeholder="serial, address, run id"><button class="quiet">'
              f'Search</button></form></div>'
            + '<div class="panel"><table><tr><th>time</th><th>kind</th>'
              '<th>run</th><th>phone</th><th>what</th></tr>'
            + ("".join(lines) or empty)
            + f'</table>{nav}<div class="row"><p class="dim">alerts fire '
              f'on these kinds — never on log prose · a serial anywhere '
              f'opens that phone\'s story · the run opens its log lines · '
              f'{int(data.get("total") or 0)} matching</p>'
              f'<a class="btn quiet right" href="/events.csv?{keep}">'
              f'Export CSV</a></div></div>')
    return page("Events", body, user=user, here="/events", refresh=30)


_LEVEL_BADGE = {"INFO": "", "WARNING": "warn", "ERROR": "bad",
                "CRITICAL": "bad", "DEBUG": ""}


def _capture_line(capture: dict | None, log_db: bool) -> str:
    """How the capture is doing, in one line under the header."""
    if not log_db:
        return ('<span style="color:var(--dim)">capture off - LOG_DB is '
                'not set</span>')
    if capture is None:
        return ('<span style="color:var(--amber)">capture not started in '
                'this process</span>')
    if not capture.get("on"):
        when = ""
        if capture.get("off_at"):
            moment = datetime.datetime.fromtimestamp(
                float(capture["off_at"]), datetime.timezone.utc)
            when = f" at {_clock(moment)}"
        why = str(capture.get("off_why") or "")
        return (f'<span style="color:var(--red)">capture switched itself OFF'
                f'{when}</span>'
                + (f' <span class="dim">— {esc(why)}; a restart brings it '
                   f'back</span>' if why else ""))
    return (f'<span style="color:var(--green)">capture on</span> · '
            f'{int(capture.get("written") or 0):,} written · '
            f'{int(capture.get("dropped") or 0):,} dropped')


def _logs_empty(log_db: bool, level: str, logger: str, run: str,
                phone: str, q: str, before: int) -> str:
    """An empty table that says which nothing this is."""
    if not log_db:
        return "nothing captured yet - LOG_DB is off"
    bits = [f"nothing at {esc(level)}"]
    if run:
        bits.append(f"for run {esc(run)}")
    if phone:
        bits.append(f"on phone {esc(phone)}")
    if logger:
        bits.append(f"from {esc(logger)}")
    if q:
        bits.append(f'matching "{esc(q)}"')
    if before:
        bits.append(f"older than #{int(before)}")
    if len(bits) == 1:
        bits.append("yet - the capture writes within a second of the first "
                    "line")
    return " ".join(bits)


def logs_page(data: dict, user: dict, *, level: str = "INFO",
              logger: str = "", run: str = "", phone: str = "",
              q: str = "", before: int = 0, capture: dict | None = None,
              log_db: bool = True) -> str:
    """The captured lines, newest first, with the capture's own health
    on the header line. `capture` is what logdb.health() says in this
    process; `log_db` is the flag, so an empty table can say why."""
    keep = (f"logger={_q(logger)}&run={_q(run)}&phone={_q(phone)}"
            f"&q={_q(q)}")

    def pill(name: str) -> str:
        lit = ' class="here"' if name == level else ""
        return f'<a href="/logs?level={name}&{keep}"{lit}>{name}</a>'

    lines = []
    smallest = None
    for r in data.get("rows") or []:
        ctx = (f"[{r.get('run') or '-'}/{r.get('build') or '-'}]")
        name = str(r.get("logger") or "").replace("geelark_farm.", "")
        lvl = str(r.get("level") or "")
        tint = {"WARNING": "warn", "ERROR": "bad", "CRITICAL": "bad"}.get(lvl)
        row_class = f' class="{tint}"' if tint else ""
        if r.get("id") is not None:
            smallest = (int(r["id"]) if smallest is None
                        else min(smallest, int(r["id"])))
        lines.append(
            f'<tr{row_class}><td class="muted">{_clock(r.get("at"))}</td>'
            f'<td><span class="badge {_LEVEL_BADGE.get(lvl, "")}">'
            f'{esc(lvl)}</span></td>'
            f'<td class="muted">{esc(ctx)}</td>'
            f'<td class="muted">{esc(name)}</td>'
            f'<td class="msg" style="white-space:pre-wrap">'
            f'{esc(str(r.get("msg") or ""))}</td></tr>')
    known = [str(n) for n in (data.get("loggers") or []) if n]
    chosen = logger if logger in known else ""
    options = ['<option value="">logger: any</option>'] + [
        f'<option value="{esc(n)}"{" selected" if n == chosen else ""}>'
        f'{esc(n.replace("geelark_farm.", ""))}</option>' for n in known]
    older = ""
    if data.get("more") and smallest is not None:
        older = (f'<div class="row"><span class="right"></span>'
                 f'<a href="/logs?level={_q(level)}&{keep}&before={smallest}">'
                 f'older →</a></div>')
    newest = (f'<a class="dim" href="/logs?level={_q(level)}&{keep}">'
              f'← newest</a>' if before else "")
    empty = _logs_empty(log_db, level, logger, run, phone, q, before)
    body = (f'<div class="top"><h2>Events</h2>'
            f'<div class="pills"><a href="/events">Events</a><span>Logs'
            f'</span></div><span class="status">{_capture_line(capture, log_db)}'
            f' · INFO and up · kept 30 days</span></div>'
            f'<form method="get" action="/logs" class="row">'
            f'<input type="hidden" name="level" value="{esc(level)}">'
            f'<div class="chips">{pill("INFO")}{pill("WARNING")}'
            f'{pill("ERROR")}</div>'
            f'<select name="logger">{"".join(options)}</select>'
            f'<input name="logger_text" value="{esc("" if chosen else logger)}" '
            f'placeholder="or part of a logger name" size="16">'
            f'<input name="run" value="{esc(run)}" placeholder="run: r8" '
            f'size="10"><input name="phone" value="{esc(phone)}" '
            f'placeholder="phone: 1533" size="12">'
            f'<input name="q" value="{esc(q)}" placeholder="text in the '
            f'message"><button class="quiet">Filter</button>{newest}</form>'
            f'<div class="panel"><table><tr><th>time</th><th>level</th>'
            f'<th>run</th><th>logger</th><th>message</th></tr>'
            + ("".join(lines) or f'<tr><td colspan="5" class="muted">{empty}'
                                  f'</td></tr>')
            + f'</table>{older}<p class="dim">captured in-process, batched '
              f'into the database; if the database stalls the capture '
              f'disables itself with one warning — it can never slow a build '
              f'· the JSON file on disk stays the complete record · '
              f'{int(data.get("today") or 0):,} lines today</p></div>')
    return page("Logs", body, user=user, here="/events", refresh=15)


# ----------------------------------------------------------- the story
#: The word the closing entry uses for each phone status.
_NOW_WORDS = {"ready": "ready to hand over", "app_only": "waiting for an "
              "account", "building": "being built right now",
              "incomplete": "stopped short - needs a look"}


def _story_lines(t: dict, explain) -> tuple[str, str]:
    """One timeline entry as (headline, explanation), both HTML. The
    headline is what happened; the explanation is why or what it means,
    dimmer underneath."""
    kind = str(t.get("kind") or "")
    status = str(t.get("status") or "")
    text = str(t.get("text") or "")
    source = t.get("source")
    if source == "request":
        if t.get("verb"):
            head_text, aside = describe(str(t["verb"]), t.get("payload") or {})
            head = (f'<b>{esc(str(t.get("requested_by") or "?"))}</b> asked: '
                    f'{esc(head_text)}'
                    + (f' <span class="dim">— {esc(aside)}</span>'
                       if aside else ""))
            number = t.get("id")
            colour = _OUTCOME_COLOUR.get(status, "muted")
            outcome = (f'<span style="color:var(--{colour})">{esc(status)}'
                       f'</span>'
                       + (f': {esc(str(t.get("result") or ""))}'
                          if t.get("result") else "")
                       + (f' · <a href="/requests?hi={int(number)}">'
                          f'#{int(number)}</a>' if number else ""))
            return head, outcome
        return esc(text), ""
    if source == "artifact":
        files = [str(f) for f in (t.get("files") or [])]
        folder = str(t.get("folder") or t.get("run") or "")
        serial = str(t.get("serial") or "")
        head = (f"{_plural(len(files), 'screen')} archived" if files
                else esc(text))
        links = " · ".join(
            f'<a href="/phones/{esc(serial)}/screens/{_q(folder)}/{_q(f)}">'
            f'{esc(f)}</a>' for f in files)
        outcome = esc(status) if status else ""
        return head, " — ".join(b for b in (outcome, links) if b)
    if kind == "build_finished":
        fields = _build_fields(text)
        ok = fields.get("ok") == "True" or (not fields and status == "ready")
        if ok:
            who = fields.get("app") or fields.get("gmail") or ""
            return ("Build ended: ready",
                    f"signed in as {esc(who)}" if who else "")
        seen, advice = (explain(status) if explain and status else ("", ""))
        head = esc(seen) if seen else f"Build ended: {esc(status or '?')}"
        return head, esc(advice) if advice else esc(status if seen else "")
    if kind == "phone":
        if status == "created":
            return esc(text[:1].upper() + text[1:]), ""
        return esc(status.capitalize() or "Phone"), esc(text)
    if kind == "account":
        who, sep, reason = text.partition(":")
        reason = reason.strip()
        seen, advice = (explain(reason) if explain and reason else ("", ""))
        head = (f"{esc(who.strip())} set aside: {esc(seen or reason)}"
                if sep else f"{esc(text)}")
        return head, esc(advice)
    if kind == "breaker":
        return (f"Breaker {esc(status)}", esc(text))
    head = f"{esc(kind)} {esc(status)}".strip()
    return head, esc(text)


def _fold_story(timeline: list) -> list[list]:
    """Consecutive events of one kind and status - three app logins that
    failed the same way - as one group, so the story says it once with
    every time it happened."""
    groups: list[list] = []
    for t in timeline:
        last = groups[-1] if groups else None
        same = (last is not None and t.get("source") == "event"
                and last[0].get("source") == "event"
                and last[0].get("kind") == t.get("kind")
                and str(last[0].get("status")) == str(t.get("status"))
                and t.get("kind") != "phone")
        if same:
            last.append(t)
        else:
            groups.append([t])
    return groups


def _entry(when: str, head: str, aside: str, badge: str = "",
           klass: str = "") -> str:
    aside_html = f'<span class="dim">{aside}</span>' if aside else ""
    return (f'<div class="entry{" " + klass if klass else ""}">'
            f'<span class="when">{when}</span>{badge}'
            f'<div class="lines"><span class="head">{head}</span>'
            f'{aside_html}</div></div>')


def _now_entry(phone: dict) -> str:
    """The closing line: where the phone stands right now, off its row."""
    status = str(phone.get("status") or "")
    if phone.get("done_at"):
        head = f"gone — deleted {_day(phone['done_at'])}"
    elif (phone.get("state") or "") == "taken":
        head = f"out with {esc(str(phone.get('owner') or 'somebody'))}"
    else:
        head = _NOW_WORDS.get(status, _phone_word(status))
    bits = []
    tries = int(phone.get("tries") or 0)
    if tries:
        bits.append(f"Tries {tries} of 3"
                    + (" — given up until cleared" if tries >= 3 else ""))
    if phone.get("note"):
        bits.append(esc(str(phone["note"])))
    if phone.get("updated_at"):
        bits.append(f"last change {_when(phone['updated_at'])}")
    return _entry("now", f"Now: {head}", " · ".join(bits),
                  _phone_badge(phone), "now")


def phone_story_page(story: dict, user: dict, *, explain=None) -> str:
    """Everything one phone went through, two lines an entry: what
    happened, then why or what it means. Identical failures in a row
    fold into one entry with every time listed; the story closes with
    where the phone stands now. `explain` turns a status token into
    (seen, advice) - app passes failures.verdict; pages never import it."""
    phone = story.get("phone") or {}
    serial = str(story["serial"])
    back = f"/phones/{serial}"
    head = ""
    if phone:
        bits = [esc(str(phone.get("gmail") or "no gmail")),
                esc(str(phone.get("proxy_name") or "no proxy")),
                f"created {_when(phone.get('created_at'))}"]
        if phone.get("app_account"):
            bits.insert(1, esc(str(phone["app_account"])))
        head = (f'<span>{_phone_badge(phone)}</span>'
                f'<span class="dim mono">{" · ".join(bits)}</span>')
        if phone.get("done_at"):
            head += (f'<span class="badge">gone {_day(phone["done_at"])}'
                     f'</span>')
    actions = []
    if phone and not phone.get("done_at"):
        actions = _state_forms(user, dict(phone, serial=serial), back)
        if _may(user, "may_change_proxy") and \
                (phone.get("status") or "") != "building":
            actions.append(_change_ip_form(user, serial, back))
    hand = ""
    if phone and (phone.get("status") or "") == "ready" \
            and not phone.get("done_at"):
        line = " · ".join(str(phone.get(k) or "") for k in
                          ("serial", "app_account", "gmail", "proxy_name"))
        hand = (f'<div class="panel"><h3>Hand over</h3>'
                f'<input class="hand" readonly value="{esc(line)}" '
                f'title="one line for the customer - click, copy">'
                f'<p class="dim">click the line to select it, then copy - '
                f'this is what the customer needs</p></div>')

    items = []
    for group in _fold_story(story.get("timeline") or []):
        first = group[0]
        headline, aside = _story_lines(dict(first, serial=serial), explain)
        badge = _event_badge({"kind": first.get("kind"),
                              "status": first.get("status"),
                              "detail": ("ok=True" if first.get("kind") ==
                                         "build_finished" and
                                         str(first.get("status")) == "ready"
                                         else "")})
        if first.get("source") == "artifact":
            badge = '<span class="badge">screens</span>'
        runs = " ".join(f'<span class="dim">[{esc(str(t["run"]))}]</span>'
                        for t in group if t.get("run")
                        and t.get("source") == "event")
        if len(group) > 1:
            when = (f"{_day(first['at'])} "
                    + " · ".join(_hhmm(t["at"]) for t in group))
            headline += (f' <span class="dim">· {len(group)} times</span>')
        else:
            when = f"{_day(first['at'])} {_clock(first['at'])}"
        secs = (f' <span class="dim">· {int(first["seconds"])}s</span>'
                if first.get("seconds") and len(group) == 1 else "")
        items.append(_entry(when, f"{headline}{secs} {runs}".strip(), aside,
                            badge))
    if phone:
        items.append(_now_entry(phone))
    hint = _need(user, "may_take_phones", "taking, returning and closing "
                                          "this phone") if phone else ""
    body = (f'<div class="top"><a href="/events" class="dim">← Events</a>'
            f'<h2>Phone {esc(serial)}</h2>{head}'
            f'<span class="status">{" ".join(actions)}</span></div>'
            + hand
            + '<div class="panel">'
            + ("".join(items) or '<p class="muted">Nothing recorded about '
                                 'this phone.</p>')
            + hint
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
