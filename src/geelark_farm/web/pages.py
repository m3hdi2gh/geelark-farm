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
 --line2:#1d2636;--ink:#d7dee9;--bright:#f2f6fc;--muted:#8a97ab;--dim:#8391a8;
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
/* ---- the phones table, standing in its own panel */
.slab{{background:var(--panel);border:1px solid var(--line);border-radius:10px;
 overflow:hidden}}
.slab>.tscroll{{margin:0}}
.slab table{{margin:0}}
.slab thead th{{background:var(--panel2);border-bottom:1px solid var(--line);
 font-size:10.5px;letter-spacing:.9px;text-transform:uppercase;
 color:var(--dim);font-weight:500;padding:10px 15px;white-space:nowrap}}
.slab tbody td{{padding:11px 13px;border-bottom:1px solid var(--line2)}}
/* The three narrow ones shrink to their words so the two address columns
   keep the width. Without this the row runs past its box and the last
   button - the one a person came for - is the half that is cut off. */
.slab td:nth-child(5),.slab td:nth-child(6),.slab th:nth-child(5),
.slab th:nth-child(6){{width:1%;white-space:nowrap}}
.slab thead th:first-child,.slab tbody td:first-child{{width:1%}}
.slab tbody tr:last-child td{{border-bottom:0}}
.slab tbody tr:hover{{background:#141c2b}}
.slab td.act{{text-align:right;white-space:nowrap}}
.slab td.act form{{display:inline}}
.slab .addr{{font-family:var(--mono);font-size:12.5px;display:block;
 max-width:17ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
@media (min-width:1500px){{.slab .addr{{max-width:24ch}}}}
.nowrap{{white-space:nowrap}}
/* A dot before the word, so a state reads as a state from a distance
   and not as one more piece of text in a row made of text. */
.slab .badge::before{{content:"";display:inline-block;width:5px;height:5px;
 border-radius:50%;background:currentColor;margin-right:6px;
 vertical-align:middle}}
.slab .badge.running::before{{margin-right:6px}}
/* Every button in a row, one size and one rhythm. The exit button wore
   the amber of a warning and is not one: it is the ordinary thing you do
   to a phone whose way out is refused. */
.slab td.act button,.slab td.act .btn{{padding:5px 9px;font-size:12px;
 border-radius:7px;margin-left:4px}}
.slab thead th:last-child,.slab td.act{{padding-right:15px}}
/* ---- the pool cards on the rail, and the manager they open */
.pool{{background:var(--panel);border:1px solid var(--line);border-radius:10px;
 overflow:hidden}}
.pool>header{{display:flex;align-items:center;gap:11px;padding:13px 15px 11px}}
.pool>header b{{font-family:var(--mono);font-size:27px;line-height:1;
 font-weight:500;letter-spacing:-.5px;font-variant-numeric:tabular-nums;
 min-width:34px}}
.pool>header .t{{display:flex;flex-direction:column;font-size:13.5px;
 font-weight:600;color:#c6d1e0;line-height:1.25}}
.pool>header .t i{{font-style:normal;font-size:11.5px;font-weight:400;
 color:var(--dim)}}
.pool>header .go,.pool>header .lock{{margin-left:auto}}
.go.small{{padding:5px 11px;font-size:12.5px}}
.pool .queue{{list-style:none;margin:0;padding:0;border-top:1px solid var(--line2);
 max-height:176px;overflow-y:auto;overscroll-behavior:contain}}
.pool .queue li{{display:flex;align-items:center;gap:8px;padding:6px 15px;
 font-size:12px}}
.pool .queue li+li{{border-top:1px solid var(--line2)}}
.pool .queue .t{{font-family:var(--mono);color:var(--muted);overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap}}
.pool .queue .t{{flex:1;min-width:0}}
.pool .queue .tag{{margin-left:auto;font-size:11px;color:var(--dim);
 white-space:nowrap}}
.pool .queue form{{margin-left:auto;display:inline;flex-shrink:0}}
.pool .queue form button{{padding:2px 8px;font-size:11px;border-radius:5px;
 white-space:nowrap}}
.pool .railnote{{margin:0;padding:9px 15px;font-size:11.5px;color:var(--dim);
 border-top:1px solid var(--line2)}}
.pool .railnote.bad,.pool .railnote.warn{{font-weight:500}}
.pool .railnote.bad{{color:var(--red)}} .pool .railnote.warn{{color:var(--amber)}}
.pool .railnote.bad::before,.pool .railnote.warn::before{{content:"! ";
 font-family:var(--mono);font-weight:600}}
.pool .more{{display:block;width:100%;background:none;border:0;
 border-top:1px solid var(--line2);color:var(--blue);padding:9px;
 cursor:pointer;font:inherit;font-size:12px}}
.pool .more:hover{{background:#141c2b;color:#a8ccff}}
.ov{{position:fixed;inset:0;background:rgba(4,7,13,.72);display:flex;
 align-items:center;justify-content:center;padding:24px;z-index:40}}
[hidden]{{display:none!important}}
.ov .sheet{{background:var(--panel);border:1px solid var(--line);
 border-radius:12px;width:min(940px,100%);max-height:88vh;display:flex;
 flex-direction:column;box-shadow:0 24px 70px rgba(0,0,0,.55)}}
.ov .sheet>header{{display:flex;align-items:center;gap:12px;padding:15px 18px;
 border-bottom:1px solid var(--line)}}
.ov .sheet>header h3{{font-size:16px}}
.ov .sheet .x{{margin-left:auto;background:none;border:0;color:var(--muted);
 font-size:22px;line-height:1;cursor:pointer;padding:0 4px}}
.ov .sheet .x:hover{{color:var(--bright)}}
.sheetbody{{overflow:auto;padding:15px 18px}}
/* A sheet showing a page of its own - a preview, a confirm. It was
   `.sub`, which is also the subtitle class, so the whole body was capped
   at 78ch and coloured muted: the preview sat in two thirds of the sheet
   with its table cut off at the edge (the operator, 2026-09-08). */
.sheetbody.shown{{display:flex;flex-direction:column;gap:12px}}
.sheetbody.shown>*{{width:100%;max-width:none;margin:0}}
.sheetbody.shown .top,.sheetbody.shown .narrow>.top,
.sheetbody.shown .alerts{{display:none}}
/* The edit-and-preview-again box of the preview page is the paste box the
   sheet already has - Back returns to it with the paste still in it. */
.sheetbody.shown form[action$="/preview"],
.sheetbody.shown .panel:has(form[action$="/preview"]){{display:none}}
.sheetbody.shown .panel .row{{justify-content:flex-end;gap:8px}}
.sheetbody.shown .panel .row .right{{display:none}}
.sheetbody.shown .panel .row .dim{{margin-right:auto}}
/* The preview: one card, the table scrolling inside it rather than
   pushing the whole sheet sideways (2026-09-08). */
.preview .lede{{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}}
.preview .wrap{{overflow-x:auto}}
.preview th,.preview td{{white-space:nowrap}}
.preview td:first-child{{color:var(--ink)}}
form.busy{{cursor:progress}}
form.busy button{{opacity:.45;filter:grayscale(1);pointer-events:none}}
/* The rows a press is working on. Dimmed, not hidden: what is happening
   is happening to these, and a row that vanished mid-press would read as
   done (2026-09-14). */
tr.acting{{opacity:.45;transition:opacity .12s}}
tr.acting td{{cursor:progress}}
.addbox{{background:var(--panel2);border:1px solid var(--line);
 border-radius:9px;padding:12px;margin-bottom:14px}}
.addbox label{{display:block;font-size:11px;letter-spacing:.6px;
 text-transform:uppercase;color:var(--dim);margin-bottom:7px}}
.addbox textarea{{width:100%}}
/* Which kind of Spotify account a paste is: two tiles, not a dropdown.
   The two go on different kinds of phone, so the rule for each is
   written on the tile rather than hidden in an option list, and each
   wears the mark its rows wear in the table (2026-09-17). */
.kindpick{{display:flex;gap:10px}}
.kindpick input{{position:absolute;width:1px;height:1px;opacity:0}}
.addbox .kindpick label{{flex:1 1 0;min-width:0;display:block;margin:0;
 padding:9px 11px;border:1px solid var(--line);border-radius:9px;
 background:var(--bg);cursor:pointer;text-transform:none;letter-spacing:0;
 font-size:12px}}
.kindpick label b{{display:flex;align-items:center;gap:7px;
 font-family:var(--mono);font-size:12.5px;font-weight:600;color:var(--muted)}}
.kindpick label b::before{{content:"";width:8px;height:8px;
 background:currentColor}}
.kindpick label.normal b::before{{border-radius:50%}}
.kindpick label.error b::before{{clip-path:polygon(50% 0,100% 100%,0 100%)}}
.kindpick label i{{display:block;font-style:normal;font-size:11px;
 color:var(--dim);margin-top:4px}}
.kindpick label:hover{{border-color:#3d4f6e}}
.kindpick input:checked+label.normal{{border-color:#2a6b55;background:#0f2a22}}
.kindpick input:checked+label.normal b{{color:#4fd1a5}}
.kindpick input:checked+label.error{{border-color:#7a5320;background:#2b1f10}}
.kindpick input:checked+label.error b{{color:#f2a35c}}
.kindpick input:focus-visible+label{{outline:2px solid var(--blue);
 outline-offset:2px}}
.addbox .addrow{{display:flex;align-items:center;gap:10px;margin-top:9px;
 font-size:11.5px}}
.filters{{display:flex;align-items:center;gap:7px;flex-wrap:wrap;
 margin-bottom:10px}}
.filters .poolfind{{min-width:190px}}
.filters .tally{{margin-left:auto;font-size:11.5px}}
.filters .pill{{background:var(--panel2);border:1px solid var(--line);
 color:var(--muted);padding:4px 11px;border-radius:999px;cursor:pointer;
 font:inherit;font-size:12px}}
.filters .pill[aria-pressed=true]{{background:var(--blue-bg);
 border-color:#2c4d80;color:#a8ccff}}
.filters .pill b{{font-weight:600;margin-left:4px;font-variant-numeric:tabular-nums}}
.filters .chips{{gap:5px;margin-right:4px}}
/* The kind chips, Spotify only: their own two colours and their own two
   marks, so a chip and the rows it shows read as the same thing. Set
   off from the status chips by a rule - they are two questions, asked
   side by side (2026-09-17). */
.filters .chips.kinds{{margin-left:3px;padding-left:10px;
 border-left:1px solid var(--line2)}}
.filters .pill.kind::before{{content:"";display:inline-block;width:7px;
 height:7px;background:currentColor;margin-right:6px}}
.filters .pill.kind.normal{{color:#4fd1a5}}
.filters .pill.kind.normal::before{{border-radius:50%}}
.filters .pill.kind.error{{color:#f2a35c}}
.filters .pill.kind.error::before{{clip-path:polygon(50% 0,100% 100%,0 100%)}}
.filters .pill.kind.normal[aria-pressed=true]{{background:#0f3a2c;
 border-color:#2a6b55}}
.filters .pill.kind.error[aria-pressed=true]{{background:#3d2a12;
 border-color:#7a5320}}
table.pooltable td{{vertical-align:middle}}
table.pooltable .doors{{display:flex;gap:6px;justify-content:flex-end}}
table.pooltable .doors form{{display:inline}}
/* The row editor is a dialog of its own, in the top layer, one per
   sheet: it used to be a row of six boxes squeezed under the row, with
   the password and the key blanked - a form for retyping, not for
   correcting (the operator, 2026-09-08). */
dialog.editor{{background:var(--panel);color:var(--ink);border:1px solid
 var(--line2);border-radius:12px;padding:0;width:min(560px,calc(100vw - 32px));
 box-shadow:0 24px 70px rgba(0,0,0,.6)}}
dialog.editor::backdrop{{background:rgba(4,7,13,.6)}}
dialog.editor form{{display:flex;flex-direction:column;gap:12px;padding:16px 18px}}
dialog.editor header{{display:flex;align-items:baseline;gap:10px;
 padding-bottom:12px;border-bottom:1px solid var(--line2)}}
dialog.editor h4{{margin:0;font-size:14px;font-weight:600;color:var(--bright)}}
dialog.editor header .mono{{color:var(--muted);font-size:12.5px;overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap;min-width:0}}
dialog.editor .field input,dialog.editor .field select{{width:100%;height:36px;
 font-family:var(--mono);font-size:12.5px}}
dialog.editor .two{{display:grid;gap:10px;
 grid-template-columns:repeat(auto-fit,minmax(200px,1fr))}}
dialog.editor .tick{{display:flex;align-items:center;gap:8px;font-size:12.5px;
 color:var(--muted)}}
dialog.editor .tick input{{accent-color:var(--accent);width:15px;height:15px}}
dialog.editor .row{{justify-content:flex-end;padding-top:4px}}
dialog.editor .row button{{height:36px}}
/* ---- polish, from the contract (2026-09-05): nothing here changes what a
   thing does; each rule is what makes the page feel finished. */
::selection{{background:var(--blue-bg);color:#fff}}
*{{scrollbar-width:thin;scrollbar-color:var(--line2) transparent}}
*::-webkit-scrollbar{{width:8px;height:8px}}
*::-webkit-scrollbar-thumb{{background:var(--line2);border-radius:8px}}
.mono,.serial,.addr,.age,.pool>header b,.queue .t,.queue .tag,.status{{
 font-variant-numeric:tabular-nums}}
button:active,.btn:active{{transform:translateY(1px)}}
input,select,textarea{{color-scheme:dark}}
input:focus,select:focus,textarea:focus{{outline:none;border-color:var(--blue);
 box-shadow:0 0 0 3px var(--blue-bg)}}
/* Sticky needs a scrollport to stick inside. `.tscroll` was the nearest
   one and it only scrolls sideways - no height, so it never scrolled down
   and the headers never stuck, in a table of a hundred and ninety rows
   (2026-09-07). And a sticky header needs its own background, or the rows
   pass through the column names. */
.slab thead th,.pooltable thead th{{position:sticky;top:0;z-index:1}}
.pooltable thead th{{background:var(--panel2)}}
.slab>.tscroll{{max-height:calc(100vh - 260px);overflow:auto}}
.slab th.num,.slab td.num{{text-align:right}}
.slab tbody tr:hover .addr{{color:var(--ink)}}
.status .live{{animation:breathe 1.6s ease-in-out infinite;display:inline-block}}
@keyframes breathe{{50%{{opacity:.35}}}}
.slab .badge.info::before{{animation:blink 1.2s ease-in-out infinite}}
@keyframes blink{{50%{{opacity:.2}}}}
.slab td.progress{{position:relative;padding-bottom:14px}}
.slab td.progress::after{{content:"";position:absolute;left:15px;right:15px;
 bottom:8px;height:2px;background:linear-gradient(90deg,transparent,var(--blue),
 transparent);background-size:40% 100%;animation:slide 1.6s linear infinite}}
@keyframes slide{{from{{background-position:-40% 0}}to{{background-position:140% 0}}}}
.pool>header .go{{background:transparent;color:var(--blue);
 border:1px solid #2c4d80}}
.pool>header .go:hover{{background:var(--blue-bg);border-color:var(--blue)}}
.pool .queue li:hover{{background:#141c2b}}
.byhand .field input,.byhand button.go{{height:38px}}
.byhand.js details.newone[open]{{display:flex;flex-wrap:wrap;gap:10px;
 align-items:center;padding:10px 12px;background:var(--panel2);
 border:1px dashed #2c4d80;border-radius:8px}}
.byhand.js details.newone[open] summary{{display:none}}
.byhand details.newone .lead{{font-size:12px;color:var(--dim)}}
.byhand details.newone input{{margin:0;width:200px}}
.byhand input:disabled,.byhand select:disabled{{opacity:.45}}
.maker{{display:block;font-size:11px;margin-top:3px;white-space:nowrap}}
/* What is signed in on the phone, under the word for what the phone is:
   a square for GPT, a circle for a normal Spotify account, a triangle
   for an error one - the marks the pool wears (2026-09-17). */
.carries{{display:block;font-family:var(--mono);font-size:11px;margin-top:3px;
 white-space:nowrap;color:var(--dim)}}
.carries::before{{content:"";display:inline-block;width:6px;height:6px;
 background:currentColor;margin-right:5px}}
.carries.normal{{color:#4fd1a5}} .carries.normal::before{{border-radius:50%}}
.carries.error{{color:#f2a35c}}
.carries.error::before{{clip-path:polygon(50% 0,100% 100%,0 100%)}}
.byhand label.field select{{min-width:230px;height:38px}}
dialog.editor .or{{font-size:12.5px;color:var(--muted);padding-top:6px;
 border-top:1px solid var(--line2)}}
dialog.editor .pick{{max-height:170px;overflow:auto;border:1px solid var(--line2);
 border-radius:7px;background:var(--panel2)}}
dialog.editor .pick label{{display:flex;gap:10px;align-items:center;padding:7px 11px;
 font-family:var(--mono);font-size:12.5px;border-top:1px solid var(--line2);
 cursor:pointer}}
dialog.editor .pick label:first-child{{border-top:0}}
dialog.editor .pick label:hover{{background:#141d2f}}
dialog.editor .pick .none{{padding:10px 11px;font-size:12.5px;color:var(--dim)}}
dialog.editor .dlg{{display:flex;flex-direction:column;gap:12px;padding:16px 18px}}
dialog.editor .dlg .field input{{width:100%;height:36px;font-family:var(--mono);
 font-size:12.5px}}
.ov .sheet.narrow{{width:min(520px,100%)}}
.ov .sheet.drawer{{position:fixed;right:0;top:0;bottom:0;width:min(480px,100%);
 max-height:none;border-radius:0;border-right:0}}
.ov .sheet.drawer .narrow{{width:auto;max-width:none}}
.ov .sheet.drawer .top{{display:none}}
.ov .sheet.drawer .acts{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}}
/* A fold for what only a person debugging a flow wants: the archived
   screen files, the raw fields of a history line. Shut, it is one word. */
details.tech{{display:inline}}
details.tech>summary{{display:inline;cursor:pointer;color:var(--dim);
 font-size:12px;list-style:none}}
details.tech>summary::-webkit-details-marker{{display:none}}
details.tech>summary::after{{content:" \2304"}}
details.tech[open]>summary::after{{content:" \2303"}}
details.tech[open]{{display:block;margin-top:4px}}
/* A phone somebody asked for, before it is a phone. One line each,
   the same rhythm as the table under it. */
.wishes{{padding:12px 14px}}
.wishes h3{{margin:0 0 8px}}
.wish{{display:flex;align-items:center;gap:10px;padding:6px 0;
 border-top:1px solid var(--line2);font-size:13px}}
.wish:first-of-type{{border-top:0}}
.wish .who{{flex:0 0 auto;color:var(--ink)}}
.wish .why{{color:var(--dim);font-size:12.5px}}
.wish .age{{flex:0 0 46px;color:var(--dim);font-size:12px}}
/* The search and the chips live inside the one scrolling body, so they
   left the screen the moment somebody scrolled to the row they wanted -
   which is exactly when they want to search (2026-09-07). The paste box
   stuck too, and the two slid over each other (2026-09-08): only this
   row stays. The negative top cancels the padding on that body; the
   negative sides run its background to the edges, so nothing shows
   through beside it. */
.sheetbody .filters{{position:sticky;top:-15px;margin:0 -18px 0;
 padding:12px 18px 10px;background:var(--panel);z-index:2;
 border-bottom:1px solid var(--line2)}}
/* Under that row, not under the top edge it covers: the row is 59px
   tall in the same inset frame the -15px is measured in. And the table
   wrapper must not be a scrollport of its own here, or the column names
   stick to a box that never scrolls - which is why they never stuck in
   the sheet (2026-09-08). */
.sheetbody>.tscroll{{overflow:visible}}
.sheetbody .pooltable thead th{{top:44px}}
.pickrow{{display:flex;align-items:center;gap:10px;padding:9px 12px;
 border-bottom:1px solid var(--line2)}}
.pickrow:last-child{{border-bottom:0}}
.pickrow .serial{{min-width:56px;font-weight:500}}
.statepick{{display:flex;flex-direction:column;gap:4px;font-size:10.5px;
 letter-spacing:.07em;text-transform:uppercase;color:var(--dim)}}
.statepick select{{background:var(--panel2);border:1px solid var(--line);
 color:var(--ink);border-radius:7px;padding:7px 10px;font:12.5px var(--mono)}}
.statepick select:disabled{{opacity:.5}}
@media (prefers-reduced-motion:no-preference){{
 .ov .sheet.drawer{{animation:slidein .2s ease-out}}
 @keyframes slidein{{from{{transform:translateX(24px);opacity:0}}}}
}}
.filters .sellerpick{{background:var(--panel2);border:1px solid var(--line);
 color:var(--ink);border-radius:7px;padding:6px 10px;font:12.5px var(--mono)}}
.filters .poolfind{{flex:1}}
.addbox .addrow .seller{{min-width:230px}}
@media (prefers-reduced-motion:no-preference){{
 .ov{{animation:fade .16s ease-out}} .ov .sheet{{animation:rise .18s ease-out}}
 .said.toast.up{{animation:rise .18s ease-out}}
 @keyframes fade{{from{{opacity:0}}}}
 @keyframes rise{{from{{opacity:0;transform:translateY(8px)}}}}
}}
/* A small yes-or-no where the button was pressed, for the one destructive
   thing on the page. The full confirm page is what a browser without the
   script gets. */
/* Fixed, and clamped by the script. It used to be placed at the
   row position on the document, so a Remove near the foot of a long
   list put the question below the fold - the press looked like it had
   done nothing - and scrolling left the bubble over an unrelated row
   Remove was not the one that would fire (2026-09-07). */
.mini{{position:fixed;z-index:50;background:var(--panel);
 border:1px solid var(--line2);border-radius:10px;padding:12px 14px;
 box-shadow:0 14px 40px rgba(0,0,0,.55);width:min(320px,90vw);font-size:13px}}
.mini p{{margin:0 0 10px}} .mini .row{{display:flex;gap:8px;justify-content:flex-end}}
/* ---- the page */
main{{flex:1;min-width:0;padding:24px 32px 56px;display:flex;flex-direction:column;
 gap:16px}}
/* The sign-in card, and only it: `main` is a column flex box at least
   100vh tall, so centring it vertically centres the whole dashboard -
   and re-centres it after every live swap, which slides the page half a
   row under the cursor every time a row appears (2026-09-07). */
main.alone{{align-items:center;justify-content:center;padding:40px 20px}}
main.full{{padding:40px 20px}}
h2{{font-size:25px;font-weight:600;color:var(--bright);margin:0;letter-spacing:-.3px}}
h3{{font-size:13.5px;font-weight:600;color:#c6d1e0;margin:0;display:flex;align-items:center;
 gap:8px;flex-wrap:wrap}}
h3 .n{{font-family:var(--mono);font-size:12px;font-weight:400;color:var(--dim)}}
.top{{display:flex;align-items:center;gap:14px 18px;flex-wrap:wrap;min-height:42px}}
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
.pills{{display:flex;border:1px solid #2c3a52;border-radius:7px;
 overflow:hidden;align-items:stretch}}
/* Direct children only. `.pills span` also matched the count inside each
   pill, which then wore the background, the padding and the divider of
   the pill around it - four boxes inside four boxes (2026-09-04). */
.pills>a,.pills>span{{display:inline-flex;align-items:center;gap:9px;
 padding:8px 15px;font-size:13px;line-height:1.2;color:var(--muted);
 font-family:var(--sans);border-right:1px solid #2c3a52}}
.pills>a:last-child,.pills>span:last-child{{border-right:0}}
.pills>a:hover{{background:#141c2b;color:var(--bright)}}
.pills>span,.pills>a.here{{background:#1a2334;color:var(--bright)}}
.pills .n{{font-family:var(--mono);font-size:12px;color:var(--dim);
 font-variant-numeric:tabular-nums}}
.figure{{font-family:var(--mono);font-size:20px;font-weight:600;
 line-height:1;font-variant-numeric:tabular-nums;vertical-align:-1px}}
.addbox{{background:var(--panel2);border:1px solid var(--line2);
 border-radius:12px;padding:14px;display:flex;flex-direction:column;
 gap:11px}}
.addbox textarea{{background:var(--bg)}}
.secret{{display:flex;flex-direction:column;gap:3px}}
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
/* A Spotify account kind: its own two colours, and its own shape of
   dot - a round one for the account that wants a bare phone, a
   triangle for the one that wants a Gmail on it - so the two are told
   apart at a glance and not only by reading (2026-09-17). */
.cat{{display:inline-flex;align-items:center;gap:5px;padding:1px 8px;
 border-radius:9px;font-family:var(--mono);font-size:11px;font-weight:500;
 white-space:nowrap}}
.cat::before{{content:"";width:7px;height:7px;background:currentColor}}
.cat.normal{{background:#0f3a2c;color:#4fd1a5}}
.cat.normal::before{{border-radius:50%}}
.cat.error{{background:#3d2a12;color:#f2a35c}}
.cat.error::before{{clip-path:polygon(50% 0,100% 100%,0 100%)}}
/* ---- text helpers */
.muted{{color:var(--muted)}} .dim{{color:var(--dim);font-size:12px}}
.mono{{font-family:var(--mono)}}
.empty{{color:var(--muted);text-align:center;padding:22px 10px;font-size:13.5px}}
.err{{background:#2a1512;border:1px solid #57241c;color:#f0a094;padding:10px 14px;
 border-radius:8px;font-size:13px}}
.err::before,.said::before{{font-family:var(--mono);margin-right:8px;font-weight:600}}
.err::before{{content:"!"}}
.said{{background:#0f2b1a;border:1px solid #1e5b2a;color:#9be3b3;padding:10px 14px;
 border-radius:8px;font-size:13px;margin:0}}
.said::before{{content:"✓ "}}
/* An answer that is not a yes. Every press wore the green tick, so a
   refusal read as a success and the person walked away (2026-09-07). */
.said.no{{background:#2a1512;border-color:#57241c;color:#f0a094}}
.said.no::before{{content:"! "}}
.said.toast.up{{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);
 z-index:60;box-shadow:0 12px 34px rgba(0,0,0,.5);
 transition:opacity .4s,transform .4s}}
.said.toast.gone{{opacity:0;transform:translate(-50%,8px)}}
.said.toast form{{display:inline;margin-left:10px}}
.said.toast form button{{padding:3px 10px;font-size:12px;color:var(--blue);
 border-color:#22406e}}
.hint{{color:var(--dim);font-size:12px;line-height:1.55}}
/* ---- forms */
input,textarea,select{{background:var(--panel2);border:1px solid #2c3a52;
 border-radius:7px;color:var(--ink);padding:8px 12px;font-family:var(--mono);
 font-size:12.5px;min-height:36px}}
input:hover,textarea:hover{{border-color:#3d4f6e}}
input:focus,textarea:focus,select:focus{{outline:none;border-color:var(--focus);
 box-shadow:0 0 0 3px rgba(127,180,255,.15)}}
textarea{{width:100%;min-height:110px;line-height:1.7;resize:vertical}}
/* The palest thing on the page was the format of the paste box, at
   about 2.8:1 - and a placeholder is erased by the first keystroke, so
   the one text somebody needs while typing was the one that left
   (2026-09-07). */
input::placeholder,textarea::placeholder{{color:var(--dim)}}
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
button.quiet.warn,.btn.quiet.warn{{border-color:#57431c;color:var(--amber)}}
button.quiet.warn:hover,.btn.quiet.warn:hover{{background:var(--amber-bg);
 color:#ffd89a}}
button.quiet.bad,.btn.quiet.bad{{border-color:#4d2323;color:var(--red)}}
button.quiet.bad:hover,.btn.quiet.bad:hover{{background:var(--red-bg);
 color:#ffb3a6}}
button.quiet.ok,.btn.quiet.ok{{border-color:#1d4530;color:var(--green)}}
button.quiet.ok:hover,.btn.quiet.ok:hover{{background:var(--green-bg);
 color:#8ff0b5}}
button.quiet.go,.btn.quiet.go{{border-color:#22406e;color:var(--blue)}}
button.quiet.go:hover,.btn.quiet.go:hover{{background:var(--blue-bg);
 color:#c6dcff}}
button.quiet.live,.btn.quiet.live{{border-color:#3a2c55;color:var(--violet)}}
button.quiet.live:hover,.btn.quiet.live:hover{{background:var(--violet-bg);
 color:#e2d8ff}}
/* Boot: the one button on a free row, and the one that starts something
   - filled, where every other row button is an outline. */
button.boot,.btn.boot{{background:linear-gradient(135deg,#5b47c9,#8a6cf5);
 color:#fff;border:0;border-radius:999px;padding:5px 13px 5px 10px;
 font-size:12px;font-weight:600;letter-spacing:.02em;gap:5px;
 box-shadow:0 1px 2px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.16)}}
button.boot:hover,.btn.boot:hover{{background:linear-gradient(135deg,#6a56dc,#9d82ff);
 color:#fff;box-shadow:0 2px 8px rgba(124,92,230,.45),
 inset 0 1px 0 rgba(255,255,255,.2)}}
button.boot svg{{flex:none}}
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
.row .seg{{margin-left:auto;background:var(--panel2);padding:2px;gap:0}}
.seg button{{background:none;border:0;padding:4px 12px;border-radius:5px;
 cursor:pointer;font:inherit;font-size:12.5px;color:var(--muted)}}
.seg button:hover{{color:var(--bright);background:transparent}}
.seg button[aria-pressed=true]{{background:var(--panel);color:var(--bright);
 box-shadow:0 1px 0 rgba(0,0,0,.4)}}
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
/* The dashboard is two columns: what an operator watches on the left,
   what they reach for on the right. Everything else on the console keeps
   the single centred column. */
.wide{{width:100%;max-width:1340px;margin:0 auto;display:flex;
 flex-direction:column;gap:20px}}
/* Two rails, and what each is for. What the farm is BUILT FROM - the
   Gmails and the exits - stands across the top of the table as a short
   strip: stock is checked, not watched, and standing it there gives the
   table the width the left rail used to take. What is SIGNED IN ON
   PHONES - the accounts a person sends - keeps the right, where the
   presses are (the operator, 2026-09-17). */
.desk{{display:grid;grid-template-columns:minmax(0,1fr) 314px;
 gap:20px 30px;align-items:start;
 grid-template-areas:"supply side" "main side"}}
.deskmain{{grid-area:main;display:flex;flex-direction:column;gap:20px;
 min-width:0}}
.desk>.side{{grid-area:side}}
.rail{{grid-area:supply;display:flex;gap:16px;flex-wrap:wrap;
 align-items:flex-start}}
.rail>.pool{{flex:1 1 280px;min-width:0}}
.rail>.pool>header{{padding:11px 14px 9px}}
.rail>.pool>header b{{font-size:22px}}
/* Two across rather than one down. The strip is as wide as the table
   and a list one name wide was leaving half of it empty: eight names
   where two were, for fifty pixels of height (2026-09-17). */
.rail>.pool .queue{{max-height:122px;overflow:hidden auto;display:grid;
 grid-template-columns:minmax(0,1fr) minmax(0,1fr);border-top:0}}
/* `minmax(0,...)` and min-width:0, or a long address plus its seller
   widens the column past its track and the box scrolls sideways - the
   name is clipped with an ellipsis instead (the operator, 2026-09-17). */
.rail>.pool .queue li{{min-width:0;border-top:1px solid var(--line2)}}
.rail>.pool .queue li:nth-child(even){{border-left:1px solid var(--line2)}}
/* The Spotify card split: how many of each kind are free, because
   the two go on different phones (2026-09-17). */
.split{{display:flex;gap:8px;align-items:center;margin:0;
 padding:0 15px 10px;font-family:var(--mono);font-size:11.5px}}
.split b{{color:var(--ink);font-weight:600;margin-right:4px}}
.railcap{{width:100%;margin:0;font-family:var(--mono);font-size:10.5px;
 letter-spacing:.09em;text-transform:uppercase;color:var(--dim);padding:0 2px}}
@media (max-width:1100px){{
 .desk{{grid-template-columns:minmax(0,1fr);
  grid-template-areas:"supply" "main" "side"}}
}}
/* The table scrolls inside its own column rather than taking the page
   sideways with it - the rail has to stay where it was put. */
.tscroll{{overflow-x:auto}}
.whoout{{display:flex;align-items:center;gap:9px;margin-left:22px;
 padding-left:22px;border-left:1px solid var(--line2)}}
.whoout .av{{width:26px;height:26px;border-radius:50%;background:var(--panel);
 border:1px solid var(--line);display:flex;align-items:center;
 justify-content:center;font-size:12px;text-transform:uppercase}}
.whoout .who{{font-size:12.5px;color:var(--muted)}}
.addfold{{margin-left:auto}}
.addfold summary{{list-style:none;cursor:pointer}}
.addfold summary::-webkit-details-marker{{display:none}}
.addfold[open]{{margin-left:0;width:100%;padding-top:10px}}
.addfold[open] summary{{display:inline-block;margin-bottom:8px}}
.addfold form{{display:flex;flex-direction:column;gap:8px}}
.addfold textarea{{background:var(--panel2);border:1px solid var(--line);
 border-radius:6px;color:var(--ink);font:400 11.5px/1.6 var(--mono);
 padding:8px 9px;resize:vertical;width:100%}}
.addfold textarea:focus{{outline:none;border-color:#3a5c96}}
.byhand{{display:flex;flex-wrap:wrap;gap:12px 14px;align-items:flex-end;
 padding-top:4px}}
/* Three boxes, the tick and the button on one line, left to right and no
   wider than their words. Stretched to fill, the row read as four things
   scattered across a page; this reads as one sentence. */
.byhand label{{display:flex;flex-direction:column;align-items:flex-start;
 gap:5px;font-size:10.5px;letter-spacing:.7px;text-transform:uppercase;
 color:var(--dim);flex:0 0 auto;text-align:left}}
.byhand label input{{width:220px}}
.byhand label.tick{{flex-direction:row;align-items:center;gap:8px;
 font-size:12.5px;letter-spacing:0;text-transform:none;color:var(--ink);
 padding-bottom:9px}}
.byhand label.tick input{{accent-color:var(--accent);width:15px;height:15px}}
.byhand select,.byhand input[type=text],.byhand input:not([type]){{
 background:var(--panel2);border:1px solid var(--line);border-radius:7px;
 color:var(--ink);font:400 12.5px/1 var(--mono);padding:9px 10px;
 font-family:var(--mono);min-width:0}}
.byhand select:focus,.byhand input:focus{{outline:none;border-color:#3a5c96}}
.byhand button.go{{padding:9px 18px;align-self:flex-end;margin-bottom:1px}}
.byhand details.newone{{flex:1 1 100%;order:10;font-size:12px;
 color:var(--dim)}}
/* With the script running, the fold exists only while it is needed -
   an address the pool does not know has been typed. Without it, the
   fold is simply there, shut, as a fold. */
.byhand.js details.newone:not([open]){{display:none}}
.byhand details.newone summary{{cursor:pointer;padding:2px 0}}
.byhand details.newone input{{margin:8px 8px 0 0;width:220px}}
.byhand p{{flex:1 1 100%;order:11;font-size:11px;line-height:1.5;margin:0}}
.stopped h3 .ct{{margin-left:auto;font-family:var(--mono);color:var(--red);
 letter-spacing:0}}
.stopped details.fold{{padding:0 15px 12px}}
.stopped details.fold summary{{padding:10px 0 6px}}
.stoprow{{padding:9px 0;border-bottom:1px solid var(--line2);font-size:12px}}
.stoprow:last-child{{border-bottom:0}}
.stoprow .why{{display:block;color:var(--red);margin-top:3px;line-height:1.45}}
.stoprow .dim{{display:block;margin-top:2px;font-size:11px}}
/* Anything a person copies out of this console, they copy by hand today. */
.cp{{cursor:copy;border-radius:3px}}
.cp:hover{{background:var(--blue-bg);box-shadow:0 0 0 3px var(--blue-bg)}}
.cp.flash{{background:var(--green-bg);box-shadow:0 0 0 3px var(--green-bg)}}
.find{{margin-left:auto;position:relative}}
.find input{{background:var(--panel2);border:1px solid var(--line);
 border-radius:7px;color:var(--ink);font:400 12.5px/1 var(--sans);
 padding:8px 11px;width:210px;font-family:inherit}}
.find input:focus{{outline:none;border-color:#3a5c96;
 box-shadow:0 0 0 3px rgba(37,99,196,.16)}}
.railfind{{padding:9px 15px;border-bottom:1px solid var(--line2)}}
.railfind input{{width:100%;background:var(--panel2);border:1px solid var(--line);
 border-radius:6px;color:var(--ink);font:400 12px/1 var(--sans);padding:7px 9px}}
.railfind input:focus{{outline:none;border-color:#3a5c96}}
.side{{position:sticky;top:18px;display:flex;flex-direction:column;gap:14px}}
.side .panel{{padding:0;gap:0;overflow:hidden}}
.side h3{{font-size:10.5px;letter-spacing:.9px;text-transform:uppercase;
 color:var(--dim);padding:13px 15px;border-bottom:1px solid var(--line2);
 background:var(--panel2);display:flex;align-items:center;gap:8px;margin:0}}
.side h3 .ct{{margin-left:auto;font-family:var(--mono);color:var(--muted);
 letter-spacing:0}}
.stockrow{{display:flex;align-items:center;gap:11px;padding:12px 15px;
 border-bottom:1px solid var(--line2)}}
.stockrow b{{font-family:var(--mono);font-size:19px;font-weight:500;
 font-variant-numeric:tabular-nums;width:34px;flex:none;line-height:1.1}}
.stockrow .t{{font-size:12.5px;color:var(--ink);line-height:1.35}}
.stockrow .t i{{display:block;font-style:normal;font-size:11px;color:var(--dim)}}
.stockrow .plus{{margin-left:auto;border:1px solid var(--line);border-radius:6px;
 color:var(--muted);font-size:11px;padding:7px 9px}}
.stockrow .plus:hover{{color:var(--blue);border-color:#2c4a7a;
 background:var(--blue-bg)}}
.stockrow .lock{{margin-left:auto;font-size:10.5px;color:#54627a;
 letter-spacing:.5px;text-transform:uppercase}}
.failrow{{display:flex;align-items:center;gap:8px;padding:11px 15px;
 background:var(--red-bg);color:var(--red);font-size:11.5px;line-height:1.45}}
.failrow:hover{{filter:brightness(1.25)}}
.failrow b{{font-family:var(--mono)}}
.failrow .arrow{{margin-left:auto;opacity:.6}}
/* A phone that did not finish is not stock, so it does not stand in the
   shelf. It still costs a slot and an exit, so it does not vanish either. */
.didnot{{border:1px solid var(--line2);border-left:2px solid var(--red-bg);
 border-radius:9px;background:var(--panel2);overflow:hidden}}
.didnot h3{{font-size:10.5px;letter-spacing:.9px;text-transform:uppercase;
 color:#c07f6d;padding:12px 15px;border-bottom:1px solid var(--line2);
 display:flex;align-items:center;gap:8px;margin:0}}
.didnot h3 .why{{margin-left:auto;text-transform:none;letter-spacing:0;
 font-size:11.5px;color:var(--dim)}}
.didnot .r{{display:flex;align-items:center;gap:16px;padding:11px 15px;
 border-bottom:1px solid var(--line2)}}
.didnot .r:last-child{{border-bottom:0}}
.didnot .r .body{{flex:1;min-width:0;font-size:12.5px}}
.didnot .r .body .why{{color:var(--red)}}
.didnot .r .act{{margin-left:auto;display:flex;gap:5px}}
@media (max-width:1180px){{
 .desk{{display:flex;flex-direction:column}}
 .side{{position:static}}
}}
.headline{{display:flex;align-items:flex-start;gap:24px;flex-wrap:wrap}}
.strip{{display:flex;gap:2px;flex-wrap:wrap}}
.strip.pools{{margin-left:auto}}
.strip a,.strip span{{display:flex;flex-direction:column;align-items:center;
 gap:5px;min-width:88px;padding:10px 14px;border-radius:10px;
 color:var(--muted);border:1px solid transparent}}
.strip a:hover{{background:var(--panel2);border-color:var(--line);
 color:var(--bright)}}
.strip b{{font-family:var(--mono);font-size:27px;font-weight:500;
 line-height:1;font-variant-numeric:tabular-nums}}
.strip i{{font-style:normal;font-size:11.5px;letter-spacing:.3px}}
.svc{{display:flex;align-items:center;gap:14px;padding-top:12px;
 border-top:1px solid var(--line2);font-size:12px;color:#55627a}}
.svc button{{background:none;border:0;color:var(--dim);font-size:12px;
 font-family:inherit;cursor:pointer;padding:2px 0}}
.svc button:hover{{color:#fff;background:none}}
.svc .right{{margin-left:auto}}
details.fold{{display:inline}}
details.fold summary{{cursor:pointer;color:var(--dim);font-size:12px;
 list-style:none;padding:5px 0}}
details.fold summary::-webkit-details-marker{{display:none}}
details.fold summary:hover{{color:#fff}}
details.fold[open]{{display:block;width:100%}}
.alerts{{display:flex;flex-direction:column;gap:6px}}
.alert{{display:flex;align-items:center;gap:10px;padding:10px 14px;
 border-radius:8px;font-size:13px;border:1px solid;border-left-width:3px;
 color:var(--ink)}}
.alert a{{color:inherit;flex:1}} .alert a:hover{{color:#fff}}
.alert b{{color:#fff;font-weight:600}}
.alert .x{{background:none;border:0;color:var(--muted);font-size:18px;
 line-height:1;cursor:pointer;padding:0 2px}}
.alert .x:hover{{color:#fff;background:none}}
.alert::before{{font-family:var(--mono);font-weight:600}}
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
tr.now td{{background:rgba(127,180,255,.05)}}
tr.now td:first-child{{border-radius:8px 0 0 8px}}
tr.now td:last-child{{border-radius:0 8px 8px 0}}
.facts{{font-family:var(--mono);font-size:13px;color:var(--ink);
 margin-top:-10px}}
.facts .hand{{user-select:all}}
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
         ("/logins", "Login rate", ""),
         ("/events", "Events", ""), ("/api-clients", "API clients", ""),
         ("/users", "Users", ""))

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
    "/logins": '<polyline points="3 17 9 11 13 14 21 6"/>'
               '<polyline points="15 6 21 6 21 12"/>',
    "/events": '<line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" '
               'y2="12"/><line x1="4" y1="18" x2="14" y2="18"/>',
    "/api-clients": '<rect x="3" y="11" width="8" height="8" rx="2"/>'
                    '<path d="M7 11V8a5 5 0 0 1 10 0v3"/>'
                    '<path d="M14 15h7M18 15v4"/>',
    "/users": '<circle cx="9" cy="8" r="3.5"/><path d="M3.5 20c.7-3.2 2.9-5 '
              '5.5-5s4.8 1.8 5.5 5"/><circle cx="17" cy="9" r="2.5"/><path '
              'd="M15.5 15.3c2.6.2 4.3 1.8 5 4.7"/>',
}
_BRAND_ICON = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" '
               'stroke="#4f8ef7" stroke-width="2" aria-hidden="true"><rect x="6" '
               'y="2.5" width="12" height="19" rx="2.5"/><line x1="10" y1="18" '
               'x2="14" y2="18"/></svg>')


#: Who the whole console belongs to.
#:
#: An operator's day is the dashboard: the shelf, the stock, the accounts
#: with nowhere to go, and the one phone they are building by hand. The
#: pool tabs, the request log and the event feed are how somebody keeping
#: the farm reads it, and every one of them is a page an operator can be
#: given a wrong idea by and cannot act on.
#:
#: On the role rather than on `sees`, because `sees` answers a different
#: question - whose phones - and an operator with `sees = all` is a person
#: who may look at everybody's shelf, not a person who runs the farm.
def _keeps_the_console(user: dict) -> bool:
    return (user or {}).get("role") == "admin"


def _who_and_out(user: dict) -> str:
    """The person and the way out, for a page with no rail to carry them.

    An operator's console is one page, so the two things every console
    needs somewhere - who am I signed in as, and how do I leave - move up
    beside the title. An admin's rail already carries both.
    """
    if _keeps_the_console(user):
        return ""
    name = str(user.get("username") or "?")
    return (f'<form method="post" action="/logout" class="whoout">'
            f'<span class="av">{esc(name[:1])}</span>'
            f'<span class="who">{esc(name)}</span>'
            f'<input type="hidden" name="csrf" '
            f'value="{esc(user.get("csrf", ""))}">'
            f'<button class="quiet">Log out</button></form>')


def page(title: str, body: str, *, user: dict | None = None,
         refresh: int = 0, here: str = "", live: str = "farm",
         bare: bool = False) -> str:
    """`refresh` seconds of meta-refresh, when a page shows pending state
    that the next serve pass will change; zero (the default) means none.
    `here` is the rail entry to light. Without a user there is no rail:
    the page stands alone, centred - the sign-in card.

    `live` is which stream the page listens on: "farm" (the default) for
    everything the dashboard draws, "logs" for a page that also draws
    the log lines, "" for a page that must not listen or swap at all -
    the Boot tab, which reloads itself whole. `bare` is the viewer tab:
    no rail and no alert strip, the frame owns the window."""
    header = ""
    if bare:
        pass
    elif user is not None and not _keeps_the_console(user):
        # No rail at all, rather than a rail with one entry on it. An
        # operator has one page: a column down the side of it whose only
        # link is the page they are already on is furniture, and the name
        # and the way out read better beside the title than under it.
        header = ""
    elif user is not None:
        counts = user.get("nav") or {}
        links = [f'<nav><div class="brand">{_BRAND_ICON}geelark farm</div>']
        for path, label, key in _RAIL:
            # An operator has one page, so there is nowhere for a rail to
            # go. Everything they do is on it, and a rail of links that
            # all refuse them is worse than no rail: it spends the width
            # to advertise what they may not have.
            if not _keeps_the_console(user) and path != "/":
                continue
            if path in ("/events", "/needs", "/logins") and \
                    user.get("sees") != "all":
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
    # The refresh the browser does by itself lives inside <noscript>: once
    # parsed, a meta refresh fires whether or not the script later removes
    # the tag, and it fired under an open manager and wiped the paste in it
    # (the operator, 2026-09-05: "it crashes back to the main page"). With
    # the script running, the interval is read off a plain meta and the
    # refresh is a quiet swap that waits until nobody is mid-way through
    # something.
    tag = (f'<noscript><meta http-equiv="refresh" content="{int(refresh)}">'
           f'</noscript><meta name="gf-refresh" content="{int(refresh)}">'
           if refresh else "")
    # Which build of the console drew this page. The script compares it on
    # every swap and reloads outright when it changes, so a deploy reaches
    # an open tab by itself - a page that swaps its body forever never
    # fetches a new script otherwise (2026-09-06).
    from ..config import revision

    tag += f'<meta name="gf-rev" content="{esc(revision())}">'
    if user is not None and not bare:
        body = _alert_strip(user) + body
    # The one script, on every page a signed-in person sees - not only
    # the dashboard. The others carried the same "live" dot and the same
    # refresh meta, and the meta sits inside <noscript> on purpose, so in
    # a browser with scripts on they never refreshed at all: Requests,
    # Events and Logs sat still under a breathing green dot (2026-09-14,
    # found by audit). The pool pages' presses promised "the page shows
    # the answer" with nothing on the page to show it. One script, one
    # behaviour: forms go by fetch, <main> is swapped in place, and the
    # stream says when.
    if user is not None and live:
        tag += f'<meta name="gf-live" content="{esc(live)}">'
        if _DASH_SCRIPT not in body:
            body += _DASH_SCRIPT
    # `.wide` is what widens the page. This class only says whether the
    # body is one card floating in the middle - the sign-in - or a page
    # that starts at the top and stays there.
    return _PAGE.format(title=esc(title), header=header, body=body,
                        favicon=_FAVICON, refresh=tag,
                        alone=("" if header else
                               ' class="alone"' if user is None else
                               ' class="full"'))


#: `page` doubles as a parameter name on the paged views; the alias keeps
#: the shell reachable inside them.
page_ = page


def _alert_strip(user: dict) -> str:
    """What is wrong right now, on every page, one line each. Read off
    the pulse the pass leaves (read.alerts); nothing when all is well."""
    found = (user.get("nav") or {}).get("alerts") or []
    if not found:
        return ""
    import hashlib

    lines = []
    for a in found:
        text = str(a.get("text", ""))
        lead, dot, rest = text.partition(". ")
        said = (f"<b>{esc(lead)}.</b> {esc(rest)}" if dot else esc(text))
        # The key the dismissal is remembered under: a digest of the words,
        # so the same alert stays put away and a reworded one comes back -
        # and so the sentence is on the page once, not once more in an
        # attribute.
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        # An operator is redirected out of every page these link to, so for
        # them the whole banner was a link that flashed and put them back
        # where they started. It is a sentence instead; the admin keeps the
        # link, because for an admin those pages exist (2026-09-07).
        body = (f'<a href="{esc(a.get("href") or "/")}">{said}</a>'
                if user.get("role") == "admin"
                else f'<span class="say">{said}</span>')
        lines.append(
            f'<div class="alert {esc(a.get("level", "warn"))}" '
            f'data-alert="{key}">{body}'
            f'<button type="button" class="x" data-dismiss="1" '
            f'aria-label="Dismiss">&times;</button></div>')
    return f'<div class="alerts">{"".join(lines)}</div>'


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
    #: Stock lives in the store now, so a command
    #: that only touches it runs in the request that
    #: asked for it - there is nothing left for a
    #: pass to do.
    "done": "Done - it is already in.",
    "removed-gmail": "Removed - the row is out of the Gmail pool.",
    "removed-gpt": "Removed - the row is out of the GPT pool.",
    "removed-spotify": "Removed - the row is out of the Spotify pool.",
    "gone": "That cannot be undone any more - the request that removed it "
            "kept nothing to put back.",
    # It said "the next pass starts it within about thirty seconds",
    # which was true when only a pass could write the sheet. A command
    # rings a bell now and a lane takes it: measured over a day,
    # set_phone_state 0.2s, remove_proxy 0.1s, boot_phone 1.4s,
    # mark_proxy_free 4.6s - and the slowest, Test all, 27s because it
    # asks GeeLark about every exit. So: it starts at once, and how long
    # it takes is the work's own business (2026-09-14).
    "queued": "Queued - it starts within a second, and this page shows "
              "the answer as soon as it is done.",
    "refused": "You may not do that - ask an admin for the permission.",
    "off": "Actions are not switched on yet.",
    "auto": "Manual login is off: accounts log in on their own on the next "
            "pass, nothing to press.",
    "none": "Tick at least one account first.",
    "already": "Already asked - that request is still pending.",
    "twice": "That press already went through the first time; the page "
             "shows what it did.",
    # The general word, for the rare case the handler could not read the
    # row back. Normally the verb's own sentence replaces it, because only
    # that sentence can name the address and say why.
    "no": "That did not go through.",
    "asked": "Asked for - it is written down and starts within seconds. "
             "It is a row of the table until it becomes a phone.",
    # The phone buttons each said "Done - it is already in", a sentence
    # written for pasted stock, after a confirm page that talked about
    # deleting the phone (2026-09-07).
    "took": "It is yours - it stays on the list as With you.",
    "released": "Back on the shelf for anybody.",
    # "The next sync" was true when only a pass could carry a mark out.
    # The lane hears the bell now: measured over a day, the phone is gone
    # about two seconds after the press (2026-09-14).
    "closed": "Marked done - the phone is deleted in a moment and what was "
              "on it retired.",
    "written-off": "Marked failed - the phone is deleted in a moment and "
                   "the account that was on it freed.",
    "cancelled": "The build gives up at its next step and puts back what "
                 "it held.",
    "dismissed": "Taken off the list.",
}

#: The Phones tab's status words as the dashboard's badge colours, and the
#: one word the dashboard says differently: a phone with the app and no
#: account is "warm" stock, which is what the keeper calls it.
_PHONE_CLASS = {"ready": "ready", "app_only": "warn", "building": "info",
                "incomplete": "attn"}
#: What a status is called on the dashboard. The loop says `app_only`
#: and `warm`; a person handing phones out reads "App only" and knows what
#: is missing from it, which is the one thing the word has to carry.
_PHONE_WORD = {"app_only": "App only", "ready": "Ready",
               "incomplete": "Incomplete", "building": "Building"}


#: What a table cell shows for a row that names no phone.
_NO_SERIAL = "<span class=dim>&mdash;</span>"


def _phone_word(status: str) -> str:
    return _PHONE_WORD.get(status, status or "?")


def _phone_badge(row: dict, me: str | None = None) -> str:
    """What the phone is, and - when `me` is given - whose it is.

    With `me`, a taken phone keeps its status pill and gains a second one
    saying who holds it: "With you", or "With ali". Taken is not a state
    the phone is in, it is a fact about a person, and it used to replace
    the one word that said whether the phone actually works.
    """
    status = row.get("status") or ""
    state = row.get("state") or ""
    pill = (f'<span class="badge {_PHONE_CLASS.get(status, "")}">'
            f'{esc(_phone_word(status))}</span>')
    if state in ("done", "failed"):
        # Marked, and on its way out: the pass deletes it within seconds.
        # Said on the row, so the press is seen to have landed.
        return (f'<span class="badge manual" title="{esc(_phone_word(status))}">'
                f'marked {esc(state)} &middot; leaving</span>')
    on = " &middot; on" if row.get("running") else ""
    if state != "taken":
        # A phone being built is on because the build has it - that is
        # Building, not Running; the word is for a phone nobody here holds
        # and no run is working on (2026-09-08).
        if row.get("running") and status != "building":
            # GeeLark has it on and nobody here holds it: booted by hand
            # in GeeLark, or taken and released while still up. It is
            # billing, and it read as free (the operator, 2026-09-08).
            return (f'<span class="badge manual" '
                    f'title="{esc(_phone_word(status))} - on in GeeLark, '
                    f'nobody here holds it">Running</span>')
        return pill
    if status == "building":
        # A phone ordered from the card is reserved for whoever asked for
        # it from the moment it exists, so the taken pill took the place
        # of the only word that matters while a build is running on it:
        # four rows read "With you" while they were being made, which
        # reads as finished and handed over (the operator, 2026-09-11).
        # The word first, whose it is after it.
        owner = str(row.get("owner") or "")
        whose = ("yours" if me is not None and owner == me
                 else (esc(owner) if owner else ""))
        return (f'<span class="badge info" title="being built'
                f'{f" for {esc(owner)}" if owner else ""}">Building'
                f'{f" &middot; {whose}" if whose else ""}</span>')
    if me is None:
        return f'<span class="badge manual">taken{on}</span>'
    owner = str(row.get("owner") or "")
    who = "With you" if owner and owner == me else (
        f"With {owner}" if owner else "Taken")
    # The one pill, not two: the status word rode beside it and the row
    # grew a line when Take was pressed (the operator, 2026-09-08). What
    # the phone is stays on the hover; whether it is on rides along.
    return (f'<span class="badge manual" title="{esc(_phone_word(status))}">'
            f'{esc(who)}{on}</span>')



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
#: `said` is the word the toast looks up afterwards. Without it all five
#: presses answered "Done - it is already in", a sentence written for
#: pasted stock and shown after a confirm page about deleting the phone
#: (2026-09-07).
PHONE_STATES = {
    "taken": {"label": "Take", "klass": "quiet go", "sure": False,
              "text": "", "said": "took"},
    "unused": {"label": "Release", "klass": "quiet", "sure": False,
               "text": "", "said": "released"},
    "done": {"label": "Done", "klass": "quiet ok", "sure": True,
             "said": "closed",
             "text": "The phone is deleted in GeeLark within a few "
                     "seconds and the gmail and the account on it retired "
                     "as delivered. There is no undo: the phone is gone."},
    "failed": {"label": "Failed", "klass": "quiet bad", "sure": True,
               "said": "written-off",
               "text": "The phone is deleted in GeeLark within a few "
                       "seconds, its gmail marked used and the account "
                       "freed for another phone. There is no undo: the "
                       "phone is gone."},
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
    "web_api": {"name": "WEB_API",
                "on": "the panel and the bot may read through /api/v1",
                "off": "those paths are 404"},
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


def _controls(data: dict, user: dict) -> str:
    """The service buttons beside the status line, and who gets which.

    They sit next to the sentence that says building has stopped, because
    that sentence is where a person is already looking when they want to
    start it again. They were written for a `_service_row` at the foot of
    the page that nothing ever called, so for as long as they have existed
    nobody could press them - an operator who pasted a batch of Gmails had
    to go and find an admin, and the admin had no button either (the
    operator, 2026-09-07).

    Pause, Stop and Start are the admin's: they are about the service.
    Clear breaker is not - the breaker means "builds keep failing", and
    the answer to it is nearly always fresh stock, which is the one thing
    an operator is trusted to add. They could add a batch of Gmails and
    then had to find an admin before the farm would use them (the
    operator, 2026-09-07).
    """
    pulse = data.get("pulse") or {}
    if not pulse or not user.get("mutations"):
        return ""
    admin = user.get("role") == "admin"
    stopped = bool(pulse.get("stopped"))
    wanted = []
    if admin and stopped:
        wanted.append("start")
    else:
        if admin:
            wanted.append("resume" if pulse.get("paused") else "pause")
        if pulse.get("tripped") and (admin or _may(user, "may_add_gmail")):
            wanted.append("clear_breaker")
        if admin:
            wanted.append("stop")
    return "".join(
        f'<form method="post" class="inline" action="/service/{what}">'
        f'{_csrf(user)}<button class="{CONTROLS[what]["klass"]}">'
        f'{esc(CONTROLS[what]["label"])}</button></form>' for what in wanted)


def _state_form(user: dict, serial: str, state: str, back: str = "/") -> str:
    """One Take / Back / Done / Failed button; `back` is the page the
    press returns to (the dashboard, or the phone's own story)."""
    plan = PHONE_STATES[state]
    # The two that delete the phone carry their question on the form, so
    # the script asks it beside the button rather than on a page of its
    # own (the operator, 2026-09-08); without the script the server still
    # asks on that page.
    ask = (f' data-ask="Phone {esc(serial)} {state}? {esc(plan["text"])}"'
           f' data-yes="Yes, phone {esc(serial)} is {state}"'
           if plan["sure"] else "")
    return (f'<form method="post" class="inline" '
            f'action="/phones/{esc(serial)}/state"{ask}>{_csrf(user)}'
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


def _cancel_form(user: dict, serial: str, back: str = "/") -> str:
    """"Cancel": the build on this phone gives up at its next step and puts
    back what it held - the Gmail, the exit. The same door "Stop this one"
    on Requests always was; here it sits on the row it is about."""
    if not _may(user, "may_login_accounts"):
        return ""
    return (f'<form method="post" class="inline" '
            f'action="/phones/{esc(serial)}/stop">{_csrf(user)}'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<button class="quiet bad" title="the build gives up at its next '
            f'step and puts back what it held; a phone nothing was signed '
            f'into yet is deleted">Cancel</button></form>')


#: The power sign on the Boot button. Drawn here rather than typed as a
#: glyph, since the fonts on the operators' machines disagree about it.
_POWER_ICON = (
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">'
    '<path d="M8 1.6v6.2" stroke="currentColor" stroke-width="1.9" '
    'stroke-linecap="round" fill="none"/>'
    '<path d="M4.7 4.3a4.9 4.9 0 1 0 6.6 0" stroke="currentColor" '
    'stroke-width="1.9" stroke-linecap="round" fill="none"/></svg>')


def _boot_form(user: dict, serial: str) -> str:
    """"Boot": start the phone in GeeLark, take it, and watch the screen.

    The live-view URL is the answer to the start call and exists nowhere
    else, so the press cannot hand one over on the spot. It opens a new
    tab instead (`target`), and that tab waits for the pass and then goes
    to the screen itself - this one stays on the dashboard.

    Since 2026-09-16 Boot is the one way a phone is taken (Take went
    when the Live tab's closing became the way it is released), so it
    is the one button on a free row and is drawn as such: filled, with a
    power sign, and the only one that is (the operator: "make Boot a
    bit prettier").
    """
    return (f'<form method="post" class="inline" target="_blank" '
            f'action="/phones/{esc(serial)}/boot">{_csrf(user)}'
            f'<button class="boot" title="start it, take it, and '
            f'watch the screen in a new tab">{_POWER_ICON}Boot</button>'
            f'</form>')


def _state_forms(user: dict, row: dict, back: str = "/") -> list[str]:
    """The phone-state buttons a taken row offers: Release, Done and
    Failed. Empty for a phone nobody holds - Boot is how one is taken,
    since the Live tab's closing became how it is released and Take was
    the one door left with no way back (the operator, 2026-09-16) - and
    while it is being built, and for someone who may not."""
    if (row.get("status") or "") == "building" or \
            not _may(user, "may_take_phones") or \
            (row.get("state") or "") != "taken":
        return []
    serial = str(row.get("serial") or "")
    return [_state_form(user, serial, "unused", back),
            _state_form(user, serial, "done", back),
            _state_form(user, serial, "failed", back)]


def _holder(row: dict) -> str:
    """Who is holding this phone - its owner's name while it is taken,
    "somebody" for a taken phone with no name on it, "" otherwise."""
    if (row.get("state") or "") != "taken":
        return ""
    return str(row.get("owner") or "") or "somebody"


def _theirs(user: dict, row: dict) -> str:
    """The name of whoever else is holding this phone, when that keeps
    this person's hands off it - or "".

    The rule was written inside `_row_actions` and enforced only there, so
    the table refused a colleague's phone and the phone's own page offered
    Done and Failed on it - and Failed deletes the phone within seconds
    and frees the account on it. One contract, one function (2026-09-07).

    An admin is the exception, since 2026-09-15: operators forget phones
    (see forgotten.py), and the person running the farm needs to end a
    hold without waiting for whoever took it - Release, Done or Failed on
    anybody's phone. The row still says whose it is, and the request
    records that it was taken from under them. For everybody else the
    three ways a phone comes back belong to whoever is holding it.
    """
    holder = _holder(row)
    if not holder or holder == str(user.get("username") or ""):
        return ""
    if user.get("role") == "admin":
        return ""
    return holder


def _row_actions(user: dict, row: dict, back: str = "/") -> str:
    """What one phone offers from the table.

    A phone on the shelf offers the one thing anybody does with it -
    Boot - and Change IP beside it. Once it is out with somebody the row
    turns into the three ways that ends: Release, Done, Failed. Closing
    a phone nobody took is rarer and lives on the phone's own page, so
    the table stays two buttons wide. A phone being built offers
    nothing: a run is holding it.

    Boot is one of the two ways a phone becomes taken - it starts it and
    takes it in one press - so it belongs to a phone nobody holds, and
    goes as soon as one does. Offering it on a taken row is offering to
    take a phone that is already taken, which is the row it is already
    on. Its other half, opening the screen again, is on the phone's own
    page, one click away on the serial, and that page keeps Boot for as
    long as the phone is alive.
    """
    building = (row.get("status") or "") == "building"
    serial = str(row.get("serial") or "")
    taken = (row.get("state") or "") == "taken"
    if (row.get("state") or "") in ("done", "failed"):
        # Decided: nothing more is done to a phone that is leaving.
        return '<span class="age">leaving</span>'
    held_by = _theirs(user, row)
    if held_by:
        # Somebody else's. The three ways a phone comes back belong to the
        # person holding it; offering them here is offering to act on a
        # phone that is not yours (the contract, 2026-09-05).
        return f'<span class="age">with {esc(held_by)}</span>'
    actions = []
    if not building and _may(user, "may_take_phones"):
        # Not on a phone GeeLark already has on: Boot would start what
        # is started, and bill it again (2026-09-08). Such a phone - on
        # with nobody here holding it - is the keeper's to switch off
        # (forgotten.sweep), and the row offers nothing until it does.
        if not taken and not row.get("running"):
            actions.append(_boot_form(user, serial))
        actions += _state_forms(user, row, back)
    if _may(user, "may_change_proxy") and not building and not taken:
        actions.append(_change_ip_form(user, serial, back))
    return " ".join(actions)


def _phone_rows(data: dict, user: dict) -> str:
    """One line per phone: what it is, what is on it, and what you can do
    with it. The hand-over line and the story live on the phone's own
    page - this table is for seeing the shelf at a glance."""
    # What can go out first, and inside each kind what nobody has taken:
    # the top of this table is the shelf the headline number counts.
    # By status, then serial - and not by whether it is taken: that sent
    # the row somebody had just pressed Take on to the bottom of its
    # group, under their cursor (the operator, 2026-09-08).
    phones = sorted(data.get("phones") or [],
                    key=lambda r: (_PHONE_ORDER.get(r.get("status") or "", 9),
                                   str(r.get("serial"))))
    progress = data.get("progress") or {}
    me = str(user.get("username") or "")
    lines = []
    for r in phones:
        # Marked done or failed: decided, and gone from this table the
        # moment the press lands rather than when the pass gets to it -
        # the operator has nothing left to do with it (2026-09-08). The
        # phone's own page still tells its story until the sync closes it.
        if (r.get("state") or "") in ("done", "failed"):
            continue
        serial = str(r.get("serial") or "")
        status = r.get("status") or ""
        badge = _phone_badge(r, me)
        # Which of the three views this row belongs to. `free` is what a
        # person can take: not held, not still being built, and not a
        # phone that stopped halfway - that one is a row to look at, not
        # one to hand over.
        taken = (r.get("state") or "") == "taken"
        view = ("mine" if taken and str(r.get("owner") or "") == me else
                "theirs" if taken else
                "free" if status in ("ready", "app_only") else status)
        if status == "building":
            # Two things to do to a build under way: watch it, and call
            # it off - the job gives up at its next step and puts back
            # what it held (the operator, 2026-09-05). The link is the
            # one GeeLark answered the start with, off the builder's own
            # log line (the operator, 2026-09-10).
            url = str((data.get("live") or {}).get(serial) or "")
            watch = (f'<a class="btn quiet live" target="_blank" '
                     f'rel="noopener" href="{esc(url)}" title="its '
                     f'screen, in a new tab">Watch live</a> '
                     if url else "")
            lines.append(
                f'<tr data-view="{view}"><td>{_serial_link(serial)}</td>'
                f'<td>{badge}</td>'
                f'<td colspan="4" class="progress">'
                f'{_progress(progress.get(serial))}</td>'
                f'<td class="act">{watch}{_cancel_form(user, serial)}</td>'
                f'</tr>')
            continue
        # Asked for by hand: says so, and by whom, under the status - a
        # phone built for somebody is not the keeper's stock (2026-09-08).
        # What is signed into it, under the word for what it is. The
        # account column had grown three things in one cell - a product,
        # a kind and an address - and a table you have to read twice is
        # a crowded table (the operator, 2026-09-17). The column keeps
        # the address; which product it is belongs with the status.
        badge += _carries(r)
        maker = str(r.get("built_by") or "")
        if maker:
            badge += (f'<span class="dim maker" title="asked for on the '
                      f'build card">built by {esc(maker)}</span>')
        lines.append(
            f'<tr data-view="{view}"><td>{_serial_link(serial)}</td>'
            f'<td>{badge}</td>'
            f'<td>{_addr_cell(r.get("gmail"), "no Gmail on it")}</td>'
            f'<td>{_account_cell(r)}</td>'
            f'<td class="mono dim">{esc(str(r.get("proxy_name") or "-"))}</td>'
            f'<td class="mono dim nowrap">'
            f'{_ago(r.get("created_at") or r.get("updated_at")) or "-"}</td>'
            f'<td class="act">{_row_actions(user, r)}</td></tr>')
    return "".join(lines)


def _account_cell(row: dict) -> str:
    """The GPT account column: the address, or why there is none.

    Which app the phone carries stopped being the answer. It used to be
    one - "spotify" meant no account was ever coming, empty meant the
    phone was done the moment Google was in (2026-09-08) - and the
    column read that word out of `App name`. Every phone carries all
    three now, so that cell says "chatgpt+spotify+claude" on every row
    and both of those branches went quiet: a bare phone read "waiting
    for one", promising an account that nothing can send, because a
    finished phone is not on the warm list any door offers (the
    operator, 2026-09-12).

    What decides the word is whether the phone can still take one. Only
    an unfinished phone can; a `ready` one is done, and the honest word
    is why it has none.
    """
    address = row.get("app_account")
    if not _no_address(address):
        # The address alone. Which product it is, and for Spotify which
        # kind, is said under the status pill by `_carries` - one column
        # for what the phone is, one for the address on it.
        return _addr_cell(address, "")
    if (row.get("status") or "") != "ready":
        return '<span class="dim">waiting for one</span>'
    if _no_address(row.get("gmail")):
        return '<span class="dim">no Google account</span>'
    return '<span class="dim">none signed in</span>'


def _carries(row: dict) -> str:
    """Which product the phone carries, under its status pill.

    A phone is a GPT phone or a Spotify one, and for Spotify which kind
    of account it took - which is the rule about the phone it could go
    on in the first place. It rides with the status because those are
    one fact about the phone: what it is. The account column says which
    address, and nothing else (the operator, 2026-09-17).

    A shape as well as a colour, the same marks the pool wears: a square
    for GPT, a circle for a normal Spotify account, a triangle for an
    error one. Read at a glance, down a column, without reading a word.
    """
    if _no_address(row.get("app_account")):
        return ""
    if (row.get("app_product") or "").strip().lower() != "spotify":
        return '<span class="carries" title="a ChatGPT account">GPT</span>'
    word = str(row.get("app_category") or "").strip().lower()
    if word not in ("normal", "error"):
        return '<span class="carries" title="a Spotify account">Spotify</span>'
    rule = dict(SPOTIFY_CATEGORIES).get(word, "")
    return (f'<span class="carries {word}" title="a Spotify account of the '
            f'{word} kind - it {esc(rule)}">Spotify {word}</span>')


def _addr_cell(value, empty: str) -> str:
    """One address, on one line, clipped rather than wrapped.

    Gmail and the GPT account shared a cell, one above the other, which
    read as a single fact about the phone and is two. Side by side, a
    column is scannable: every phone missing an account is one empty
    column, down the page.
    """
    text = str(value or "").strip()
    if _no_address(text):
        return f'<span class="dim">{esc(empty)}</span>'
    return f'<span class="addr cp" title="{esc(text)}">{esc(text)}</span>'


def _no_address(value) -> bool:
    """Whether a phone's address cell says nothing is there. The tab's own
    marks for "none" - a cross, a tick - are not addresses, and the build
    writes a cross into an empty step column, so blank is not the only
    way a cell says none."""
    text = str(value or "").strip()
    return not text or text in ("✗", "✓", "-")


def _status_sentence(data: dict) -> str:
    """How the farm is, in one sentence. The numbers only when they are
    not what they should be - the alert strip carries the rest."""
    pulse = data.get("pulse") or {}
    if not pulse:
        return '<span class="dim">no pass has reported yet</span>'
    word, colour = _keeper_words(pulse)
    # How long ago the pass ran is not a thing to read on every page:
    # when it is late the alert strip says so in a sentence, and the pass
    # itself is on its way out.
    live = " live" if word.startswith("Building") else ""
    return (f'<span class="dot{live}" style="color:var(--{colour})">●</span> '
            f'{esc(word)}')


#: The shelf, in the order a phone travels: what is being made, what is
#: warm, what can go out, what went out, and what wants a look. Each
#: count wears the colour of its badge in the table below.
_SHELF = [("building", "blue", "being made right now"),
          ("warm", "amber", "built and waiting for an account"),
          ("ready", "green", "an account is signed in - hand it over"),
          ("taken", "violet", "out with somebody"),
          ("incomplete", "red", "something on it did not finish")]


def _shelf_strip(counts: dict) -> str:
    """The phones as one row of counts, the same size and shape as the
    pools facing them: the two things a person counts on this page are
    phones and stock, so they read as one pair.

    Ready and warm are always printed - they are the shelf, and a zero
    there is the news. The other three only when they are not zero, so a
    quiet farm is three cells wide.
    """
    return "".join(
        f'<span title="{esc(why)}">'
        f'<b style="color:var(--{colour})">{int(counts.get(word) or 0)}</b>'
        f'<i>{esc(word)}</i></span>'
        for word, colour, why in _SHELF
        if counts.get(word) or word in ("ready", "warm"))


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
        ("/pools/gpt", awaiting, "GPT",
         "amber" if awaiting > warm else "ink",
         f"{awaiting - warm} of them have no phone to go to"
         if awaiting > warm else "awaiting login"),
    ]
    return "".join(
        f'<a href="{href}" title="{esc(why)}">'
        f'<b style="color:var(--{colour})">{number}</b>'
        f'<i>{esc(word)}</i></a>'
        for href, number, word, colour, why in items)


#: What the keeper is doing, by the pulse it left: the word, its colour,
#: and whether the numbers belong in it. Read top to bottom - the first
#: that fits wins, so a stopped service never reads as "building".
#: The console's one page with script on it, and it is deliberately thin.
#:
#: Nothing here changes data. Searching, filtering and copying are the
#: three things a person does to a page rather than to the farm, and every
#: one of them costs a round trip to do on the server - on a page that
#: refreshes itself every thirty seconds while a phone is building, that
#: round trip lands on a page that has moved.
#:
#: So if it does not load, or a browser refuses it, the page is exactly
#: what it was before: the table is there, the rail is there, the fold
#: still folds, and every button still posts a form. That is the whole
#: contract, and it is why this is allowed to exist at all.
_DASH_SCRIPT = """
<script>
(function(){
  // The one script, and what it is allowed to do: arrange what is
  // already on the page, and send the page's own forms without leaving
  // it. Every request it makes is one a form on the page declared - the
  // same action, the same fields - and the answer is the same HTML the
  // browser would have shown; only <main> is swapped, so the manager
  // stays open and the scroll stays put. Without the script, every form
  // still posts and every page still reloads (2026-09-05). It was the
  // dashboard's alone until 2026-09-14; every page a signed-in person
  // sees carries it now, and everything dashboard-only in it checks for
  // its element first.
  var store = null;
  try { store = window.sessionStorage; } catch (err) {}

  function init(){
    // Not the "nothing matches" row: it lives in the same tbody and would
    // otherwise count itself as a phone.
    var rows = document.querySelectorAll('#phones tbody tr:not(#nohits)');
    var tally = document.getElementById('tally');
    var seg = document.getElementById('seg');
    var none = document.getElementById('nohits');
    var want = (store && store.getItem('gf.view')) || '';
    function sift(){
      var shown = 0;
      rows.forEach(function(tr){
        var hit = !want || tr.dataset.view === want;
        tr.hidden = !hit;
        if (hit) shown++;
      });
      if (none) {
        // What "nothing" means here. It was one fixed sentence about a
        // search, shown after pressing "With me" on a quiet morning -
        // and there is no search on this table (2026-09-07).
        var cell = none.firstElementChild;
        if (cell) cell.textContent =
          want === 'mine'
            ? 'You are not holding any phone - press Free to see what you '
              + 'can take.'
          : want === 'free'
            ? 'Nothing is free right now - the keeper is building.'
            : 'Nothing here matches that.';
        none.hidden = shown > 0;
      }
      if (tally) tally.textContent = want
        ? shown + ' of ' + rows.length + ' shown'
        : rows.length + (rows.length === 1 ? ' phone' : ' phones');
    }
    if (seg) {
      seg.hidden = false;
      seg.querySelectorAll('button').forEach(function(b){
        b.setAttribute('aria-pressed', String(b.dataset.show === want));
        b.addEventListener('click', function(){
          want = b.dataset.show;
          if (store) store.setItem('gf.view', want);
          seg.querySelectorAll('button').forEach(function(o){
            o.setAttribute('aria-pressed', String(o === b));
          });
          sift();
        });
      });
      sift();
    }

    // An alert, put away for this tab. It comes back in a new one: the
    // page is not deciding the problem is gone, the person is deciding
    // they have read it.
    document.querySelectorAll('.alert[data-alert]').forEach(function(el){
      var key = 'gf.alert.' + el.dataset.alert;
      if (store && store.getItem(key)) el.hidden = true;
      var x = el.querySelector('[data-dismiss]');
      if (x) x.addEventListener('click', function(){
        el.hidden = true;
        if (store) store.setItem(key, '1');
      });
    });

    // What a press said: a toast for a few seconds, and gone from the
    // address so a refresh does not say it again.
    var said = document.querySelector('.said.toast');
    if (said) {
      said.classList.add('up');
      if (window.history && history.replaceState && /[?&]said=/.test(location.search)) {
        var clean = location.search.replace(/([?&])said=[^&]*&?/, '$1')
          .replace(/[?&]$/, '');
        history.replaceState(null, '', location.pathname + clean + location.hash);
      }
      // One with a button in it - Undo - waits long enough to be pressed.
      var stay = said.querySelector('form') ? 9000 : 3800;
      setTimeout(function(){ said.classList.add('gone'); }, stay);
      setTimeout(function(){ said.remove(); }, stay + 600);
    }

    // Build one now: two choices. No Gmail means nothing is signed in,
    // so the account box goes off with it. Which app is not a choice any
    // more - every phone carries all three (2026-09-12). "choose..." on
    // the Gmail and the account opens a small dialog - type one, or pick
    // a free one - and what is typed rides in the card's hidden boxes
    // while the choice shows the address (2026-09-10).
    var byhand = document.querySelector('.byhand');
    if (byhand) {
      var gmailPick = byhand.querySelector('select[name="gmail"]');
      var acctPick = byhand.querySelector('select[name="app_account"]');
      var gate = function(){
        var bare = !!gmailPick && gmailPick.value === 'none';
        if (acctPick) {
          acctPick.disabled = bare;
          if (bare) acctPick.value = '';
        }
      };
      [gmailPick].forEach(function(p){
        if (p) p.addEventListener('change', gate);
      });
      gate();
      byhand.querySelectorAll('select[data-new]').forEach(function(pick){
        var was = pick.value === '__new__' ? '' : pick.value;
        pick.addEventListener('change', function(){
          if (pick.value !== '__new__') { was = pick.value; return; }
          openNew(pick, was);
        });
      });
      // A choice still on "type a new one" has nothing typed yet: open
      // the dialog rather than send the placeholder.
      byhand.addEventListener('submit', function(e){
        var stuck = Array.prototype.filter.call(
          byhand.querySelectorAll('select[data-new]'),
          function(p){ return !p.disabled && p.value === '__new__'; })[0];
        if (!stuck) return;
        e.preventDefault(); e.stopImmediatePropagation();
        openNew(stuck, '');
      }, true);
    }

    // Search, the seller, and the three chips - current, errored, spent
    // - one sift per sheet.
    document.querySelectorAll('#poolov .sheet').forEach(function(sheet){
      var find = sheet.querySelector('.poolfind');
      var seller = sheet.querySelector('.sellerpick');
      var chips = sheet.querySelectorAll('.filters .pill[data-group]');
      // The second question, where a pool has one: which kind of
      // account. Its own set of chips, sifting with the first.
      var cats = sheet.querySelectorAll('.filters .pill[data-cat]');
      // Read when the sift runs, not when it was bound: a kept sheet
      // (keepSheet) has its rows swapped under it while the listeners
      // stay, and a list taken here once would sift rows that are gone.
      var body = function(){ return sheet.querySelectorAll('tbody tr:not(.none)'); };
      var none = sheet.querySelector('tbody tr.none');
      var tally = sheet.querySelector('.tally');
      var sift = function(){
        var q = (find ? find.value : '').trim().toLowerCase(), shown = 0;
        var who = seller ? seller.value : '';
        var group = '', cat = '', elsewhere = {};
        chips.forEach(function(c){
          if (c.getAttribute('aria-pressed') === 'true') group = c.dataset.group;
        });
        cats.forEach(function(c){
          if (c.getAttribute('aria-pressed') === 'true') cat = c.dataset.cat;
        });
        var rows = body();
        rows.forEach(function(tr){
          // The row's own values, not its text: the text includes the
          // buttons, so "free" - the most natural word to type - kept
          // nearly every row, and "edit" or "remove" kept all of them
          // (the operator, 2026-09-07).
          var near = (!q || (tr.dataset.find || '').indexOf(q) >= 0)
                  && (!who || tr.dataset.seller === who)
                  && (!cat || tr.dataset.cat === cat);
          var hit = near && (!group || tr.dataset.group === group);
          tr.hidden = !hit;
          if (hit) shown++;
          // A match under another chip is counted, so "nothing" can say
          // where it went: a used address searched for under `current`
          // would otherwise answer "Nothing matches that" (2026-09-08).
          else if (near) elsewhere[tr.dataset.group]
            = (elsewhere[tr.dataset.group] || 0) + 1;
        });
        if (none) {
          none.hidden = shown > 0;
          var where = Object.keys(elsewhere).map(function(g){
            return elsewhere[g] + ' under ' + g;
          });
          none.firstElementChild.textContent = where.length
            ? 'Nothing here matches that - ' + where.join(', ') + '.'
            : 'Nothing matches that.';
        }
        if (tally) tally.textContent = shown === rows.length
          ? shown + (shown === 1 ? ' row' : ' rows')
          : shown + ' of ' + rows.length + ' shown';
        // A door that answers one group belongs under that group: Free
        // all acts on the whole set-aside list, and a press while you
        // are looking at the working exits would be a surprise.
        sheet.querySelectorAll('[data-for-group]').forEach(function(el){
          el.hidden = el.dataset.forGroup !== group;
        });
      };
      // Once per sheet node. `init` runs after every swap, and a sheet
      // the swap kept (keepSheet) would otherwise gain another set of
      // listeners each time - a sift per swap it had lived through.
      if (!sheet.dataset.live) {
        sheet.dataset.live = '1';
        if (find) find.addEventListener('input', sift);
        if (seller) seller.addEventListener('change', sift);
        [chips, cats].forEach(function(set){
          set.forEach(function(c){
            c.addEventListener('click', function(){
              set.forEach(function(o){
                o.setAttribute('aria-pressed', String(o === c));
              });
              sift();
            });
          });
        });
      }
      sift();
    });

    // The page refreshes itself while a phone builds. With the script
    // here, that is a quiet swap rather than a reload.
    var meta = document.querySelector('meta[name="gf-refresh"]');
    if (meta) {
      lookAgain((parseInt(meta.getAttribute('content'), 10) || 30) * 1000);
    }
    listen();
  }

  // Not while somebody is in the middle of something. A refresh that lands
  // while the manager is open, or while a box is being typed in, wipes
  // what they were doing - which read as the page crashing back to the
  // start (the operator, 2026-09-05). It waits, and tries again shortly.
  // How long after the last scroll a redraw waits. Long enough that a
  // flick of the wheel is one gesture, short enough that a page put down
  // is up to date by the time it is looked at again.
  var SCROLL_QUIET = 1200;
  // And the floor between two redraws. The live stream ticks whenever
  // anything the page draws has changed, which while the farm builds is
  // every second or two - honest, and far more often than a person can
  // read (2026-09-14).
  var SWAP_FLOOR = 4000;
  function scrolled(){ scrolled.at = Date.now(); }
  addEventListener('scroll', scrolled, {capture: true, passive: true});
  addEventListener('wheel', scrolled, {capture: true, passive: true});
  addEventListener('touchmove', scrolled, {capture: true, passive: true});

  function mayRedraw(){
    var o = ov();
    // Anything the keyboard is on inside the page, not just a box to type
    // in: the swap replaces every child of `main`, so a redraw threw the
    // caret back to the top while somebody was tabbing through it.
    // `:focus-visible` is the keyboard test - a button left focused by a
    // mouse click would otherwise stall the refresh for good
    // (2026-09-07).
    var live = document.activeElement;
    var main = document.querySelector('main');
    var typing = !!live
      && (['INPUT', 'TEXTAREA', 'SELECT'].indexOf(live.tagName) >= 0
          || (!!main && main.contains(live)
              && live.matches(':focus-visible')));
    // A question waiting for an answer. `askFirst` puts the bubble in
    // `main`, so a swap deletes it mid-read and the press is lost - and
    // with the live stream that is a few seconds, not thirty
    // (2026-09-14).
    if (document.querySelector('.mini')
        || document.querySelector('dialog[open]')) return false;
    // A hand on the wheel. The place is put back after a swap, but a
    // redraw in the middle of the gesture still stutters under it, and
    // nothing is so urgent that it cannot wait for the scroll to stop.
    if (Date.now() - (scrolled.at || 0) < SCROLL_QUIET) return false;
    // Reading something they chose: a swap drops the selection.
    var picked = window.getSelection && window.getSelection();
    if (picked && !picked.isCollapsed && String(picked).trim().length > 1)
      return false;
    return true;
  }
  // Every one of the tests above is a reason to wait, and a person can
  // leave any of them standing for ever - a selection is not a gesture,
  // it lasts until they click elsewhere. A page that waits for ever
  // still shows a breathing green dot, so it reads as live while it has
  // quietly stopped. The ceiling is what keeps the promise: held while
  // you are busy, never held silently (2026-09-14).
  var HELD_CEILING = 20000;
  // A sheet somebody opened is a working surface, and the news behind it
  // can wait. The rule was written once and lost: it sat below an
  // unconditional `return` in this function, so it never ran - and the
  // manager blinked once per ceiling under the operator's hand
  // (2026-09-17: "it still jumps, much less often"). The drawer is not
  // one of these: it holds no box to type in and a build being watched
  // should move.
  function heldOpen(){
    var o = ov();
    return !!(o && !o.hidden && openKind && openKind !== 'phone');
  }
  function settled(){
    // Held for as long as it is open, and one refresh the moment it
    // closes (`shut`). The ceiling below does not apply: it is for the
    // gestures a person leaves standing without meaning to, and an open
    // sheet is not one of those - it is where they are working.
    if (heldOpen()) { settled.since = 0; return false; }
    if (mayRedraw()) { settled.since = 0; return true; }
    settled.since = settled.since || Date.now();
    if (Date.now() - settled.since > HELD_CEILING) {
      settled.since = 0;
      return true;
    }
    return false;
  }
  // The farm's own tick. `/live` holds one connection open and sends a
  // number whenever anything a page draws has changed; the timer above
  // stays as the fallback for a browser or a proxy that will not carry
  // it. Opened once for the life of the tab - `init` runs on every swap,
  // and a stream per swap would be a stream per thirty seconds
  // (2026-09-14).
  function listen(){
    if (listen.on || typeof EventSource === 'undefined') return;
    // Which stream, said by the page: the farm's, or the one that also
    // moves for a log line. No meta, no listening - the Boot tab.
    var which = document.querySelector('meta[name="gf-live"]');
    if (!which || !which.content) return;
    listen.on = true;
    var feed = new EventSource(which.content === 'logs' ? '/live?logs=1'
                                                        : '/live');
    feed.onmessage = function(e){
      var now = parseInt(e.data, 10);
      if (!now || now === listen.seen) { listen.seen = now; return; }
      // The first number is where the farm is, not a change: note it and
      // wait for the next one.
      if (listen.seen === undefined) { listen.seen = now; return; }
      listen.seen = now;
      // Soon, but no sooner than the floor since the last redraw.
      var since = Date.now() - (swapMain.at || 0);
      lookAgain(Math.max(250, SWAP_FLOOR - since));
    };
    feed.onerror = function(){
      // EventSource retries on its own; the timer is untouched, so a
      // stream that never comes back costs nothing but the second.
    };
  }

  // Every re-check goes through here, and the soonest one stands. They
  // each used to call `setTimeout` over whatever was pending, so a press
  // that asked to look again in two and a half seconds had its answer
  // thrown away by the next `init` - which arms the thirty-second one -
  // and the row stayed as it was until the operator pressed a second
  // time (2026-09-14: "the first press does nothing").
  function lookAgain(ms){
    var due = Date.now() + ms;
    if (init.timer && init.due && init.due <= due) return;
    clearTimeout(init.timer);
    init.due = due;
    init.timer = setTimeout(function(){
      init.due = 0;
      reloadWhenSettled();
    }, ms);
  }
  function reloadWhenSettled(){
    if (settled()) { reload(); return; }
    lookAgain(5000);
  }

  // ------------------------------------------------------ the manager
  var openKind = null, opener = null;
  function ov(){ return document.getElementById('poolov'); }
  function shut(){
    var mini = document.querySelector('.mini'); if (mini) mini.remove();
    document.querySelectorAll('dialog.editor[open]').forEach(closeEditor);
    behind(false);
    var o = ov(); if (!o) return;
    // Every sheet that is showing a preview or a confirm hands its own
    // body back first. Closed mid-preview, the sheet stayed on it: reopen
    // Manage and you got the old preview with no paste box and no list -
    // and `showInSheet` had swapped away the drawer's mount, so the next
    // serial click left the dashboard altogether (2026-09-07).
    o.querySelectorAll('.sheet').forEach(restoreSheet);
    o.hidden = true; openKind = null; drawerHref = null;
    o.classList.remove('right');
    if (opener && document.contains(opener)) opener.focus();
    opener = null;
    o.querySelectorAll('.sheet').forEach(function(el){ el.hidden = true; });
    // Everything the page held back while the sheet was open, now.
    lookAgain(300);
  }
  function behind(off){
    // The dashboard is a sibling emitted before the overlay, so one flag
    // takes the whole of it out of the tab order. Without it, Tab walked
    // from the last row of the sheet onto the buttons under the dark
    // backdrop and Enter pressed whichever it landed on (2026-09-07).
    var page = document.querySelector('.wide');
    if (page) page.inert = !!off;
  }
  function show(kind, fresh){
    var o = ov(); if (!o) return;
    o.querySelectorAll('.sheet').forEach(function(el){
      el.hidden = el.dataset.sheet !== kind;
    });
    o.hidden = false; openKind = kind;
    behind(true);
    var open = o.querySelector('.sheet[data-sheet="' + kind + '"]');
    if (!open) return;
    // The paste box, which is what the manager is opened for. It focused
    // the search unless `focusAdd` was passed, and nothing passed it any
    // more - so a pasted line filtered the list instead of entering it.
    // The `/` shortcut still reaches the search (2026-09-07).
    // Only when a person opened it. A swap reopens the sheet behind
    // them, and focusing the paste box there scrolls the body back to
    // the top - undoing the very place `viewBack` is about to restore
    // (2026-09-14).
    if (fresh === false) return;
    var box = open.querySelector('.addbox textarea')
           || open.querySelector('.poolfind');
    if (box) box.focus();
  }

  document.addEventListener('click', function(e){
    var door = e.target.closest('[data-pool]');
    if (door && !door.dataset.edit) {
      opener = door;
      show(door.dataset.pool);
      return;
    }
    // -> phone: choose the phone rather than take the next one. The row's
    // own form still works without this (the next warm phone).
    var choose = e.target.closest('[data-choose]');
    if (choose) {
      var o2 = ov(), sheet2 = o2 && o2.querySelector('.sheet[data-sheet="send"]');
      if (sheet2) {
        e.preventDefault();
        opener = choose;
        sheet2.querySelectorAll('input[name=addresses]').forEach(function(i){
          i.value = choose.dataset.choose;
        });
        var hint = sheet2.querySelector('[data-hint]');
        if (hint) hint.textContent = choose.dataset.choose;
        o2.querySelectorAll('.sheet').forEach(function(el){
          el.hidden = el !== sheet2;
        });
        o2.hidden = false; openKind = 'send'; behind(true);
        return;
      }
    }
    // A serial opens the phone's page in a drawer, here, rather than
    // leaving the dashboard for it.
    var link = e.target.closest(
      '#phones a[href^="/phones/"], .slab a[href^="/phones/"]');
    if (link && !e.ctrlKey && !e.metaKey && link.target !== '_blank') {
      e.preventDefault();
      opener = link;
      openDrawer(link.href);
      return;
    }
    var o = ov();
    // The editor's own Cancel closes the editor. It used to carry the
    // overlay's `data-shut`, so pressing it threw the whole manager away
    // and put the person back on the dashboard - three clicks from where
    // they were (the operator, 2026-09-07).
    var row = e.target.closest('[data-close-edit]');
    if (row) { e.preventDefault(); closeEditor(row.closest('dialog')); return; }
    // A click on the editor's backdrop reaches the dialog itself.
    if (e.target.matches('dialog.editor')) { closeEditor(e.target); return; }
    if (e.target.closest('[data-shut]') || e.target === o) { shut(); return; }
    var edit = e.target.closest('[data-edit]');
    if (edit) { openEditor(edit); return; }
    // "Back" inside a sheet that is showing a preview or a confirm goes
    // back to the sheet, not to the page.
    var back = e.target.closest(
      '#poolov .sheetbody.shown a[href="/"], '
      + '#poolov .sheetbody.shown a[href^="/phones/"]');
    if (back) { e.preventDefault(); restoreSheet(back.closest('.sheet')); return; }
    var el = e.target.closest('.cp');
    if (!el) return;
    var text = el.textContent.trim();
    var flash = function(){
      el.classList.add('flash');
      setTimeout(function(){ el.classList.remove('flash'); }, 500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(flash, function(){});
      return;
    }
    var box = document.createElement('textarea');
    box.value = text; box.style.position = 'fixed'; box.style.opacity = '0';
    document.body.appendChild(box); box.select();
    try { document.execCommand('copy'); flash(); } catch (err) {}
    box.remove();
  });
  document.addEventListener('keydown', function(e){
    var o = ov();
    // Escape closes the editor first, and the dialog does that itself;
    // without this it closed the editor and the manager in one press.
    if (e.key === 'Escape' && document.querySelector('dialog.editor[open]'))
      return;
    if (e.key === 'Escape' && o && !o.hidden) { shut(); return; }
    var typing = ['INPUT', 'TEXTAREA', 'SELECT'].indexOf(
      (document.activeElement || {}).tagName) >= 0;
    if (e.key === '/' && o && !o.hidden && !typing) {
      var box = o.querySelector('.sheet:not([hidden]) .poolfind');
      if (box) { e.preventDefault(); box.focus(); }
    }
  });

  // The phone's own page, in a drawer. Its forms post like any other
  // and land back on the dashboard, which reopens the drawer refreshed.
  var drawerHref = null;
  // `again` is a swap reopening the drawer somebody is already reading,
  // not a serial they just clicked. Reading a phone's story, the drawer
  // vanished with its backdrop and slid back in every few seconds, at
  // the top, with its folds shut and the focus on the heading - because
  // a swap rebuilds `#poolov` and this was called afresh each time
  // (2026-09-14).
  function openDrawer(href, again){
    var o = ov(); if (!o) return;
    var sheet = o.querySelector('.sheet[data-sheet="phone"]');
    if (!sheet) { location.assign(href); return; }
    drawerHref = href;
    // Held open across the refetch, so there is no blink and no gap onto
    // the page underneath.
    if (again) {
      o.querySelectorAll('.sheet').forEach(function(el){ el.hidden = el !== sheet; });
      o.classList.add('right');
      o.hidden = false; openKind = 'phone'; behind(true);
    }
    var mark = ++openDrawer.turn;
    fetch(href, {credentials: 'same-origin'})
      .then(function(r){
        // A session that ended while the drawer was open goes to the
        // sign-in page, not into the drawer.
        if (r.redirected && /\\/login(\\?|$)/.test(r.url)) {
          location.assign(r.url); return null;
        }
        return r.ok ? r.text() : null;
      })
      .then(function(html){
        // A later click already asked for another phone: this answer is
        // last week's news and must not land on top of it.
        if (html === null || mark !== openDrawer.turn) return;
        var doc = parse(html), main = doc.querySelector('main');
        if (!main) { location.assign(href); return; }
        var h2 = main.querySelector('.top h2');
        var acts = main.querySelector('.top .status');
        sheet.querySelector('[data-title]').textContent = h2 ? h2.textContent : '';
        var head = main.querySelector('.top > span');
        var hint = sheet.querySelector('[data-hint]');
        hint.replaceChildren.apply(
          hint, head ? Array.prototype.slice.call(head.childNodes) : []);
        var body = sheet.querySelector('[data-drawer]');
        if (!body) { location.assign(href); return; }
        // Where they were in the story, and which folds they had open.
        var reading = sheet.querySelector('.sheetbody');
        var top = reading ? reading.scrollTop : 0;
        var open = [];
        body.querySelectorAll('details[open]').forEach(function(d){
          open.push((d.querySelector('summary') || {}).textContent || '');
        });
        var nodes = [];
        if (acts) { acts.className = 'acts'; nodes.push(acts); }
        Array.prototype.slice.call(main.children).forEach(function(n){
          // Not the page's alerts. `showInSheet` was taught this and the
          // drawer was not, so the same red banner arrived a second time
          // on top of the one behind it, an alert already dismissed came
          // back, its dismiss button was dead - no init() ran on the copy
          // - and clicking it navigated away and lost the drawer
          // (2026-09-07).
          if (n.matches('script, .alerts, .banner')) return;
          nodes.push(n);
        });
        body.replaceChildren.apply(body, nodes);
        o.querySelectorAll('.sheet').forEach(function(el){ el.hidden = el !== sheet; });
        o.classList.add('right');
        o.hidden = false; openKind = 'phone'; behind(true);
        if (again) {
          body.querySelectorAll('details').forEach(function(d){
            var word = (d.querySelector('summary') || {}).textContent || '';
            if (open.indexOf(word) >= 0) d.open = true;
          });
          if (reading && top) reading.scrollTop = top;
          return;               // and the focus stays where they put it
        }
        // Its own heading, not its first button: a stray Enter after the
        // drawer opened pressed whatever that button was (2026-09-07).
        var head = sheet.querySelector('[data-title]');
        if (head) { head.tabIndex = -1; head.focus(); }
      })
      .catch(function(){
        // A blip while reading is not a reason to throw them off the
        // dashboard: the drawer keeps what it has and the next tick
        // tries again.
        if (!again) location.assign(href);
      });
  }
  openDrawer.turn = 0;

  // A sheet can show a page of its own - the preview of a paste, the
  // "are you sure" of a remove - in place of its list, and come back.
  function restoreSheet(sheet){
    var body = sheet.querySelector('.sheetbody.shown');
    if (body && body._was) body.replaceWith(body._was);
  }
  function showInSheet(sheet, main){
    var was = sheet.querySelector('.sheetbody');
    var sub = document.createElement('div');
    sub.className = 'sheetbody shown';
    Array.prototype.slice.call(main.children).forEach(function(node){
      // Not the page's heading, and not the page's alerts: the breaker
      // banner is about the farm, not about the paste being previewed,
      // and it arrived a second time inside the sheet on top of the one
      // already on the page behind it (the operator, 2026-09-07).
      if (node.matches('.top, script, .alerts, .banner')) return;
      sub.appendChild(node);
    });
    sub._was = was.classList.contains('shown') ? was._was : was;
    was.replaceWith(sub);
    init();
  }

  // ---------------------------------------------------- the requests
  function parse(html){
    return new DOMParser().parseFromString(html, 'text/html');
  }
  // What the manager looks like right now, so a swap can put it back.
  // `swapMain` kept which sheet was open and nothing else, so every
  // press - and the timer, every thirty seconds, unasked - threw the
  // chip back to `all`, emptied the search and lost the scroll. The list
  // moved under the hand using it (the operator, 2026-09-14).
  // Where the page is scrolled, inside and out. A swap replaces every
  // child of `main`, so every scrollport in it is built again at zero -
  // and the dashboard's phone table is one: `.slab>.tscroll` has its own
  // max-height. Reading row ninety, the operator was thrown back to the
  // top about every thirteen seconds, which is how often the farm moved
  // while it was building (2026-09-14).
  //
  // Matched by position, not by id: the same page redrawn has the same
  // boxes in the same order, and giving each one a name would be a name
  // to keep in step with the markup. The manager's own sheets are left
  // to `viewNow`, which knows which sheet is open.
  function boxes(){
    var here = document.querySelector('main');
    if (!here) return [];
    return Array.prototype.filter.call(
      here.querySelectorAll('.tscroll, .queue'), function(el){
        return !el.closest('#poolov');
      });
  }
  function placeNow(){
    return {win: window.scrollY || 0,
            tops: boxes().map(function(el){ return el.scrollTop; })};
  }
  function placeBack(kept){
    if (!kept) return;
    boxes().forEach(function(el, i){
      if (kept.tops[i]) el.scrollTop = kept.tops[i];
    });
    if (kept.win) window.scrollTo(0, kept.win);
  }

  // Anything typed and not yet sent. A swap replaces every child of
  // `main`, and the guard only holds while the box still has the
  // keyboard - click away from the build card to glance at the table and
  // the next tick empties it. Worst of all silently: the Gmail select
  // goes back to "auto", the hidden password and secret boxes are blank,
  // and Build then spends a pool address instead of the one that was
  // typed (2026-09-14).
  //
  // Only what differs from what the server drew, so a page that has been
  // touched by nobody restores nothing.
  function typedNow(){
    var here = document.querySelector('main');
    if (!here) return [];
    var kept = [];
    here.querySelectorAll('input, textarea, select').forEach(function(el){
      if (!el.name || el.type === 'hidden' && !el.value) return;
      if (el.type === 'checkbox' || el.type === 'radio') {
        if (el.checked !== el.defaultChecked)
          kept.push({key: whichField(el), on: el.checked});
        return;
      }
      var was = el.tagName === 'SELECT'
        ? (Array.prototype.filter.call(el.options, function(o){
            return o.defaultSelected; })[0] || {}).value || ''
        : el.defaultValue;
      if (el.value !== was)
        kept.push({key: whichField(el), value: el.value,
                   word: el.tagName === 'SELECT' && el.selectedOptions[0]
                     ? el.selectedOptions[0].textContent : ''});
    });
    return kept;
  }
  function whichField(el){
    var form = el.form;
    // A radio group shares its name with the other radios in it, so the
    // value is part of which field this is - without it, "error picked"
    // was put back onto the `normal` button and the choice was lost on
    // the next tick (2026-09-17).
    var one = el.type === 'radio' || el.type === 'checkbox' ? '=' + el.value : '';
    return (form ? (form.getAttribute('action') || '') : '') + '|' + el.name + one;
  }
  function typedBack(kept){
    if (!kept || !kept.length) return;
    var here = document.querySelector('main');
    if (!here) return;
    var by = {};
    here.querySelectorAll('input, textarea, select').forEach(function(el){
      if (el.name && !(whichField(el) in by)) by[whichField(el)] = el;
    });
    kept.forEach(function(was){
      var el = by[was.key];
      if (!el) return;
      if ('on' in was) { el.checked = was.on; return; }
      // A value the dialog added to the list is not in the fresh copy of
      // it, so it is put back too - or the select would silently fall to
      // its first option, which is the whole trap.
      if (el.tagName === 'SELECT'
          && !Array.prototype.some.call(el.options, function(o){
               return o.value === was.value; })) {
        var made = document.createElement('option');
        made.value = was.value;
        made.textContent = was.word || was.value;
        el.insertBefore(made, el.firstChild);
      }
      el.value = was.value;
    });
  }

  function viewNow(){
    var seen = {};
    document.querySelectorAll('#poolov .sheet').forEach(function(sheet){
      var kind = sheet.dataset.sheet;
      var on = sheet.querySelector('.filters .pill[data-group][aria-pressed="true"]');
      var onCat = sheet.querySelector('.filters .pill[data-cat][aria-pressed="true"]');
      var find = sheet.querySelector('.poolfind');
      var seller = sheet.querySelector('.sellerpick');
      // `.sheetbody` is the scrollport, not `.tscroll`: the CSS gives
      // `.sheetbody>.tscroll` overflow:visible on purpose so the sticky
      // headers work, and an overflow:visible box always reports a
      // scrollTop of zero. So this saved nothing at all, every time
      // (2026-09-14).
      var scroll = sheet.querySelector('.sheetbody')
                || sheet.querySelector('.tscroll');
      seen[kind] = {group: on ? on.dataset.group : null,
                    cat: onCat ? onCat.dataset.cat : null,
                    find: find ? find.value : '',
                    seller: seller ? seller.value : '',
                    top: scroll ? scroll.scrollTop : 0};
    });
    return seen;
  }
  function viewBack(seen){
    if (!seen) return;
    document.querySelectorAll('#poolov .sheet').forEach(function(sheet){
      var was = seen[sheet.dataset.sheet];
      if (!was) return;
      var find = sheet.querySelector('.poolfind');
      var seller = sheet.querySelector('.sellerpick');
      if (find && was.find) find.value = was.find;
      if (seller && was.seller) {
        // A seller that is no longer in the list leaves the box alone.
        var there = Array.prototype.some.call(seller.options, function(o){
          return o.value === was.seller; });
        if (there) seller.value = was.seller;
      }
      // Pressing the chip is how the sift is told: it sets the others
      // false, this one true, and runs. Idempotent on the one already on.
      var chip = was.group === null ? null
        : pickData(sheet, '.filters .pill[data-group]', 'group', was.group);
      var kept = was.cat === null || was.cat === undefined ? null
        : pickData(sheet, '.filters .pill[data-cat]', 'cat', was.cat);
      if (kept) kept.click();
      if (chip) chip.click();
      else if (!kept && find) find.dispatchEvent(new Event('input'));
      var scroll = sheet.querySelector('.sheetbody')
                || sheet.querySelector('.tscroll');
      // After the chip's re-sift, which changes how tall the body is.
      if (scroll && was.top) scroll.scrollTop = was.top;
    });
  }
  // Finding a row or a chip by what it carries, rather than building a
  // selector out of it. A value with a quote or a backslash in it makes
  // a selector that does not parse - and one bad character in this file
  // is a dashboard with no working buttons at all, because the whole
  // script stops at it (the operator, 2026-09-14: "Manage does nothing").
  function pickData(root, within, name, value){
    var all = root.querySelectorAll(within);
    for (var i = 0; i < all.length; i++)
      if (all[i].dataset[name] === value) return all[i];
    return null;
  }

  // The rows a press is about: its own, or - for a door that answers a
  // whole group, like Free all - every row under that group.
  function actOn(form, sheet){
    var own = form.closest('tr');
    if (own) return [own];
    var group = form.dataset.forGroup;
    if (!sheet) return [];
    if (!group) return Array.prototype.slice.call(
      sheet.querySelectorAll('tbody tr:not(.none)'));
    return Array.prototype.filter.call(
      sheet.querySelectorAll('tbody tr:not(.none)'), function(tr){
        return tr.dataset.group === group;
      });
  }

  function swapMain(doc){
    var fresh = doc.querySelector('main'), here = document.querySelector('main');
    if (!fresh || !here) { location.reload(); return; }
    // A newer console than this page's: take it whole, script and all.
    var mine = document.querySelector('meta[name="gf-rev"]');
    var theirs = doc.querySelector('meta[name="gf-rev"]');
    if (mine && theirs && mine.content !== theirs.content) {
      location.reload(); return;
    }
    var kept = openKind, seen = viewNow(), place = placeNow();
    var typed = typedNow();
    // The manager somebody is reading is kept, not rebuilt. The overlay
    // is a child of `main`, so every swap replaced it with a fresh copy
    // and showed that again - and the sheet blinked out and back under
    // the operator's hand each time a builder wrote a row, every few
    // seconds while the farm was busy (the operator, 2026-09-17). Now
    // the nodes under the hand stay and only the rows that moved are
    // swapped in (keepSheet). The drawer fetches its own page and is
    // reopened as before.
    var held = (kept && kept !== 'phone') ? ov() : null;
    var nodes = Array.prototype.slice.call(fresh.childNodes).filter(function(n){
      return !(n.nodeType === 1 && n.matches('script'));
    });
    var mini = document.querySelector('.mini'); if (mini) mini.remove();
    if (held) {
      // The overlay never leaves the DOM. Taken out and put back - which
      // is what a whole-of-main replace does - its fade and the sheet's
      // rise play again from the start, and that is the very blink being
      // fixed. So everything around it is replaced, and it stays where
      // it is; the copy the swap brought is read and dropped.
      Array.prototype.slice.call(here.childNodes).forEach(function(n){
        if (n !== held) here.removeChild(n);
      });
      var brought = null;
      nodes.forEach(function(n){
        if (n.nodeType === 1 && n.id === 'poolov') { brought = n; return; }
        here.insertBefore(n, held);
      });
      keepSheet(held, kept, brought);
    } else {
      here.replaceChildren.apply(here, nodes);
    }
    init();
    if (kept === 'phone' && drawerHref) openDrawer(drawerHref, true);
    else if (held) behind(true);
    else if (kept && kept !== 'send') show(kept, false);
    typedBack(typed);
    viewBack(seen);
    placeBack(place);
    swapMain.at = Date.now();
  }

  // The overlay the swap found open, still in place, with the news from
  // the copy the swap brought carried over: the closed sheets
  // are taken whole (they open current later), and the open one has its
  // rows matched by key - changed ones replaced, gone ones removed, new
  // ones added before the "nothing matches" line - and the counts on its
  // chips copied. Its paste box, its editor, its scroll and the keyboard
  // are never touched. Rows are compared without the one-time `press`
  // token every render draws afresh, or every row would count as changed.
  function keepSheet(held, kind, fresh){
    if (!fresh) return;
    var mine = held.querySelector('.sheet[data-sheet="' + kind + '"]');
    var theirs = fresh.querySelector('.sheet[data-sheet="' + kind + '"]');
    Array.prototype.slice.call(fresh.querySelectorAll('.sheet')).forEach(function(s){
      if (s.dataset.sheet === kind) return;
      var old = held.querySelector('.sheet[data-sheet="' + s.dataset.sheet + '"]');
      if (old) old.replaceWith(s); else held.appendChild(s);
    });
    if (!mine || !theirs) return;
    var body = mine.querySelector('tbody'), fb = theirs.querySelector('tbody');
    if (body && fb) {
      var same = function(tr){
        return tr.outerHTML.replace(/name="press" value="[^"]*"/g, 'name="press"');
      };
      var have = {};
      body.querySelectorAll('tr[data-key]').forEach(function(tr){
        have[tr.dataset.key] = tr;
      });
      var none = body.querySelector('tr.none'), want = {};
      Array.prototype.slice.call(fb.querySelectorAll('tr[data-key]')).forEach(function(tr){
        want[tr.dataset.key] = true;
        var old = have[tr.dataset.key];
        if (!old) body.insertBefore(tr, none);
        else if (same(old) !== same(tr)) old.replaceWith(tr);
      });
      Object.keys(have).forEach(function(k){ if (!want[k]) have[k].remove(); });
    }
    // Both sets of chips, by position: the fresh sheet is the same
    // pool drawn by the same code, so the nth chip is the nth chip.
    var counts = theirs.querySelectorAll('.filters .pill b');
    mine.querySelectorAll('.filters .pill b').forEach(function(b, i){
      if (counts[i]) b.textContent = counts[i].textContent;
    });
  }

  // 3. One row changed, so one row is replaced - not the page under it.
  // A Test or a Free is about a single exit, and swapping the whole of
  // `main` for it is why the list jumped. The fresh document already
  // holds that row; take it and leave everything else alone.
  // The banner the answer came with, put where the page shows banners.
  // A one-row swap replaced the row and dropped everything else the
  // server had said about it.
  function sayIt(doc){
    var said = doc.querySelector('main .said');
    var here = document.querySelector('main');
    if (!said || !here) return;
    var old = here.querySelector('.said');
    if (old) old.replaceWith(said);
    else here.insertBefore(said, here.firstChild);
  }

  function swapRow(doc, key){
    var mine = pickData(document, '#poolov tr[data-key]', 'key', key);
    var theirs = pickData(doc, '#poolov tr[data-key]', 'key', key);
    if (!mine || !theirs) return false;
    var sheet = mine.closest('.sheet');
    mine.replaceWith(theirs);
    // The counts on the chips are now a row out. Counted off the table
    // rather than read from the answer, so they cannot drift.
    if (sheet) {
      var tally = {group: {}, cat: {}}, all = 0;
      sheet.querySelectorAll('tbody tr:not(.none)').forEach(function(tr){
        all++;
        ['group', 'cat'].forEach(function(k){
          var v = tr.dataset[k];
          if (v !== undefined) tally[k][v] = (tally[k][v] || 0) + 1;
        });
      });
      ['group', 'cat'].forEach(function(k){
        sheet.querySelectorAll('.filters .pill[data-' + k + ']').forEach(function(c){
          var n = c.querySelector('b'); if (!n) return;
          n.textContent = c.dataset[k] ? (tally[k][c.dataset[k]] || 0) : all;
        });
      });
    }
    init();
    return true;
  }
  function reload(){
    fetch(location.pathname + location.search, {credentials: 'same-origin'})
      .then(function(r){
        // A session that ended, or a store that is down while the farm
        // keeps building: neither is a reason to swap the dashboard for
        // a sign-in card or an error page nobody asked for. The page
        // keeps what it has and looks again shortly (2026-09-14).
        if (r.redirected && /\\/login(\\?|$)/.test(r.url)) {
          location.assign(r.url); return null;
        }
        if (!r.ok || (r.redirected && !isHere(r.url))) return null;
        return r.text();
      })
      .then(function(html){
        if (html === null) { lookAgain(5000); return; }
        // Asked again, because the answer took a moment to come back and
        // a hand may have arrived on the page meanwhile.
        if (!settled()) { lookAgain(5000); return; }
        swapMain(parse(html));
      })
      .catch(function(){ lookAgain(5000); });
  }
  // The page this is, not the dashboard: on Requests, a Retry answers
  // with Requests, and that is "here".
  function isHere(url){
    try { return new URL(url, location.href).pathname === location.pathname; }
    catch (err) { return false; }
  }

  // The row editor: the sheet's one dialog, filled from the row whose
  // Edit was pressed - password and key in clear, so a wrong one can be
  // seen to be wrong (the operator, 2026-09-08). Only `.value` is set;
  // nothing here is read as markup.
  function openEditor(button){
    var sheet = button.closest('.sheet');
    var dlg = sheet && sheet.querySelector('dialog.editor');
    var tr = button.closest('tr');
    if (!dlg || !tr) return;
    var form = dlg.querySelector('form'), f = form.elements;
    var address = button.dataset.edit;
    f.address.value = address;
    f.new_address.value = address;
    f.password.value = tr.dataset.password || '';
    // The Spotify editor has no key and no tick, and a category instead
    // - opened with the row's own kind, or Save would quietly make every
    // edited row `normal` (2026-09-17).
    if (f.secret) f.secret.value = tr.dataset.secret || '';
    if (f.clear_secret) f.clear_secret.checked = false;
    if (f.category) f.category.value = tr.dataset.cat || 'normal';
    if (f.seller) f.seller.value = tr.dataset.sellername || '';
    dlg.querySelector('[data-who]').textContent = address;
    // Status: free, set aside, or the word the row has now. A row a
    // phone is behind shows it greyed - the phone decides that one.
    var state = tr.dataset.state || '';
    var word = state === 'set_aside' ? 'set aside' : state;
    Array.prototype.slice.call(f.state.options).forEach(function(opt){
      if (opt.value !== 'free' && opt.value !== 'set aside') opt.remove();
    });
    if (word && word !== 'free' && word !== 'set aside')
      f.state.add(new Option(word, word));
    f.state.value = word || 'free';
    f.state.disabled = state === 'on a phone';
    f.state.title = f.state.disabled
      ? 'a phone is behind this row - the phone decides' : '';
    if (typeof dlg.showModal === 'function') dlg.showModal();
    else dlg.setAttribute('open', '');
  }
  function closeEditor(dlg){
    if (!dlg) return;
    if (dlg.open && typeof dlg.close === 'function') dlg.close();
    else dlg.removeAttribute('open');
  }

  // "type a new one" on the build card: the dialog `pick` names, filled
  // in, copied into the card's hidden boxes; the address becomes the
  // choice. Cancel puts the choice back where it was.
  function openNew(pick, was){
    var dlg = document.getElementById(pick.dataset.new);
    var form = pick.closest('form');
    if (!dlg || !form) return;
    var address = dlg.querySelector('[data-field$="_address"]');
    var picked = function(){
      return dlg.querySelector('input[type="radio"]:checked');
    };
    // Typing is choosing to type: the picked row lets go. Picking a row
    // is choosing the pool: the boxes empty, the pool has the rest.
    dlg.oninput = function(ev){
      if (ev.target.type === 'radio') {
        dlg.querySelectorAll('[data-field]').forEach(function(i){ i.value = ''; });
      } else {
        dlg.querySelectorAll('input[type="radio"]')
           .forEach(function(r){ r.checked = false; });
      }
    };
    var done = function(use){
      if (use) {
        var row = picked();
        var addr = row ? row.value : (address ? address.value : '').trim();
        if (!addr) { if (address) address.focus(); return; }
        dlg.querySelectorAll('[data-field]').forEach(function(i){
          if (i === address) return;
          var box = form.querySelector('input[name="' + i.dataset.field + '"]');
          if (box) box.value = row ? '' : i.value;
        });
        var old = pick.querySelector('option[data-typed]');
        if (old) old.remove();
        var opt = new Option(addr + (row ? ' (from the pool)' : ' (new)'),
                             addr, true, true);
        opt.setAttribute('data-typed', '1');
        pick.insertBefore(opt, pick.querySelector('option[value="__new__"]'));
        pick.value = addr;
      } else {
        pick.value = was;
      }
      // Setting .value fires nothing, so the card's own gate never heard
      // that the Gmail had gone back to "none" on Cancel and left the
      // account box live over a bare build (2026-09-12).
      pick.dispatchEvent(new Event('change'));
      closeEditor(dlg);
    };
    dlg.querySelector('[data-use]').onclick = function(){ done(true); };
    dlg.querySelector('[data-cancel]').onclick = function(){ done(false); };
    dlg.addEventListener('cancel', function(ev){
      ev.preventDefault(); done(false);
    }, {once: true});
    if (typeof dlg.showModal === 'function') dlg.showModal();
    else dlg.setAttribute('open', '');
    if (address) address.focus();
  }

  // Remove asks first - here, beside the button, not on a page of its own
  // (the operator, 2026-09-05). Saying yes sends the same form with the
  // server's own "sure" field, so the server needs nothing new.
  function askFirst(form, question, answer){
    var old = document.querySelector('.mini'); if (old) old.remove();
    var box = document.createElement('div');
    box.className = 'mini'; box.setAttribute('role', 'dialog');
    var p = document.createElement('p');
    p.textContent = question;
    var row = document.createElement('div'); row.className = 'row';
    var keep = document.createElement('button'); keep.type = 'button';
    keep.className = 'quiet'; keep.textContent = 'Keep it';
    var yes = document.createElement('button'); yes.type = 'button';
    yes.className = 'quiet bad'; yes.textContent = answer;
    row.append(keep, yes); box.append(p, row);
    // Placed in the window, not on the document, and kept inside it: it
    // used to sit at the row's own place on the page, so a Remove near
    // the foot of a long list asked its question below the fold - the
    // press looked like it had done nothing - and one scroll left the
    // bubble hovering over a different row (2026-09-07).
    document.body.appendChild(box);
    var at = form.getBoundingClientRect();
    var size = box.getBoundingClientRect();
    box.style.top = Math.max(
      8, Math.min(at.bottom + 6, window.innerHeight - size.height - 8)) + 'px';
    box.style.left = Math.max(
      8, Math.min(at.right - size.width, window.innerWidth - size.width - 8))
      + 'px';
    yes.focus();
    // And it lives only as long as what it is pointing at stays still.
    window.addEventListener('scroll', function(){ box.remove(); },
                            {capture: true, once: true});
    keep.addEventListener('click', function(){ box.remove(); });
    yes.addEventListener('click', function(){
      box.remove();
      var sure = document.createElement('input');
      sure.type = 'hidden'; sure.name = 'sure'; sure.value = '1';
      form.appendChild(sure);
      form.requestSubmit();
    });
    document.addEventListener('keydown', function esc(ev){
      if (ev.key !== 'Escape') return;
      box.remove(); document.removeEventListener('keydown', esc);
    });
  }

  // A word on this page, for a few seconds.
  function toast(text){
    var old = document.querySelector('.said.toast'); if (old) old.remove();
    var p = document.createElement('p');
    p.className = 'said toast up'; p.textContent = text;
    document.querySelector('main').appendChild(p);
    setTimeout(function(){ p.classList.add('gone'); }, 5200);
    setTimeout(function(){ p.remove(); }, 5800);
  }

  document.addEventListener('submit', function(e){
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if ((form.method || '').toLowerCase() !== 'post') return;
    if (form.target) {
      // Boot opens its own tab and that tab waits for the link. This
      // page says so, or the press looked like nothing (2026-09-08).
      if (/[/]boot$/.test(form.action)) {
        var which = (form.action.split('/phones/')[1] || '').split('/')[0];
        toast('Starting ' + (which || 'the phone') + ' - its screen opens '
          + 'in the new tab as soon as GeeLark hands the link back.');
      }
      return;
    }
    if (!document.querySelector('main').contains(form)) return;
    if (/[/]remove$/.test(form.action)
        && !form.querySelector('input[name=sure]')) {
      e.preventDefault();
      var who = (form.querySelector('input[name=address]') || {}).value
             || (form.querySelector('input[name=name]') || {}).value || 'this row';
      askFirst(form, 'Remove ' + who + ' from the pool? The request keeps '
        + 'the row so it can be put back.', 'Remove');
      return;
    }
    // Done and Failed, which delete the phone: asked beside the button,
    // with the words the server would have put on a page of its own.
    if (form.dataset.ask && !form.querySelector('input[name=sure]')) {
      e.preventDefault();
      askFirst(form, form.dataset.ask, form.dataset.yes || 'Yes');
      return;
    }
    e.preventDefault();
    var data = new FormData(form);
    var pressed = e.submitter;
    if (pressed && pressed.name) data.append(pressed.name, pressed.value);
    var sheet = form.closest('#poolov .sheet');
    form.classList.add('busy');
    // `pointer-events:none` does not stop Enter on a focused submit, so
    // the same press went twice (2026-09-07).
    if (pressed) pressed.disabled = true;
    // What it is doing, and to what. A Test all against sixteen exits is
    // sixteen calls to GeeLark and the button said nothing for all of
    // them, which reads as a hang (the operator, 2026-09-14). The rows
    // it is acting on go dim, so the wait has a shape.
    var wasLabel = null;
    if (pressed && pressed.dataset.busy) {
      wasLabel = pressed.textContent;
      pressed.textContent = pressed.dataset.busy;
    }
    var acting = actOn(form, sheet);
    acting.forEach(function(tr){ tr.classList.add('acting'); });
    var key = form.closest('tr') ? form.closest('tr').dataset.key : null;
    // As the browser would send it - urlencoded. FormData on its own goes
    // out multipart, which the server does not read, and every field
    // including the csrf token arrived as nothing: "Stale session"
    // inside the manager on the first real press (2026-09-05).
    fetch(form.action, {method: 'POST', body: new URLSearchParams(data),
                        credentials: 'same-origin', redirect: 'follow'})
      .then(function(r){
        if (r.redirected && /\\/login(\\?|$)/.test(r.url)) {
          location.assign(r.url); return null;
        }
        return r.text().then(function(html){ return {url: r.url, html: html}; });
      })
      .then(function(got){
        // The lock comes off however this ended. It only came off in the
        // `catch`, so a form that answered *successfully* stayed locked -
        // and `form.busy button` is `pointer-events:none`, so after a
        // preview and a Back the Preview button was dead to a real click
        // while looking perfectly ordinary (the operator, 2026-09-07).
        form.classList.remove('busy');
        if (pressed) pressed.disabled = false;
        if (pressed && wasLabel !== null) pressed.textContent = wasLabel;
        acting.forEach(function(tr){ tr.classList.remove('acting'); });
        if (!got) return;
        // The editor has said its piece: whatever the answer is, it
        // shows in the sheet, not under a dialog that is still up.
        closeEditor(form.closest('dialog.editor'));
        var doc = parse(got.html);
        // Queued is not done: the lane carries it out a moment later, so
        // look again shortly and the table shows what happened - a marked
        // phone gone, a taken one wearing its name (2026-09-08).
        // Queued is not done: the lane carries it out a moment later, so
        // what came back does not show it yet. Look again shortly - and
        // do NOT put that row back, because the row in this answer is the
        // row before the press (2026-09-14).
        var waiting = /[?&]said=queued/.test(got.url);
        if (waiting) lookAgain(2500);
        // One row's press, already carried out: put that row back and
        // leave the rest of the page alone.
        //
        // Only when it actually did something. The shortcut was taken
        // for anything that was not `queued`, so a refusal - `said=no`,
        // `refused`, `already`, a 409 from the verb - redrew the row
        // exactly as it was and threw away the sentence that said why.
        // The press looked like it had simply done nothing (2026-09-14).
        //
        // Named the other way round on purpose: the words that mean
        // nothing changed are a short closed list, and the ones that
        // mean something did are added to every time a verb is.
        // Followed by the request number, the next field or the end - a
        // word boundary. It was written as backslash-b, which Python's string rules
        // turned into a backspace character before the browser saw it,
        // so the test matched nothing and every refusal took the row
        // shortcut after all (2026-09-14).
        var nothing = /[?&]said=(queued|no|refused|already|twice|gone|off|none|bad|auto)(?:[:&]|$)/;
        var worked = !nothing.test(got.url);
        if (worked && isHere(got.url) && key && swapRow(doc, key)) {
          sayIt(doc);
          return;
        }
        if (isHere(got.url)) { swapMain(doc); return; }
        // Not the dashboard: a preview, a confirm, a refusal. Inside the
        // sheet it came from, if it came from one; else in place of the
        // page, which is what the browser would have done.
        var main = doc.querySelector('main');
        // A press inside the drawer answers with the phone's own page,
        // and `showInSheet` drops `.top` - which is where that page keeps
        // its buttons. So one press emptied the drawer of every control
        // it had, with no toast and no way back but the × (2026-09-07).
        if (sheet && sheet.dataset.sheet === 'phone'
            && got.url.indexOf('/phones/') >= 0) {
          openDrawer(got.url); return;
        }
        if (sheet && main) showInSheet(sheet, main);
        else swapMain(doc);
      })
      .catch(function(){
        form.classList.remove('busy');
        if (pressed) pressed.disabled = false;
        form.submit();
      });
  });

  init();
})();
</script>"""


#: Adding stock, from the page an operator actually has.
#:
#: The pool tabs went with the rail, so `+ add` cannot be a link to one any
#: more - it opens here instead, and posts to the same preview the tab
#: always posted to. Nothing about the flow changed: paste, see what each
#: line will do, confirm. Only the door moved, and `back` carries where it
#: was opened from so confirming returns here rather than to a page the
#: person who pressed it may not have.
#:
#: `<details>` rather than script, for the reason everything else on this
#: page is: if the one exception never loads, this still opens.
_ADD_WORDS = {
    "gmail": ("may_add_gmail", "/pools/gmail/preview",
              "address, password, then the 2fa secret or the recovery "
              "address - one account per line, tabs or commas between"),
    "gpt": ("may_add_gpt", "/pools/gpt/preview",
            "address, password, then the 2fa secret - one account per "
            "line, tabs or commas between"),
}


def _add_fold(user: dict, kind: str) -> str:
    permission, where, how = _ADD_WORDS[kind]
    if not _may(user, permission):
        return ""
    return (f'<details class="addfold"><summary class="plus">+ add</summary>'
            f'<form method="post" action="{where}">{_csrf(user)}'
            f'<input type="hidden" name="back" value="/">'
            f'<textarea name="pasted" rows="4" spellcheck="false" '
            f'placeholder="{esc(how)}"></textarea>'
            f'<button class="go">Preview</button></form></details>')


#: The three pools, described once. Everything the rail and the manager
#: draw comes from here, so a card and its manager can never disagree
#: about what a pool is called or which door adds to it.
#:
#: `edit` and `remove` are the endpoints that exist, not the ones that
#: ought to. Both account pools have both; proxies have neither here,
#: because that pool is the admin's - and a button that leads nowhere is
#: worse than no button.
_POOL_KINDS = {
    "gmail": {
        "name": "Gmail", "under": "free in the pool", "one": "address",
        "add": "may_add_gmail", "manage": "may_add_gmail",
        "preview": "/pools/gmail/preview", "free": "/pools/gmail/free",
        "edit": "/pools/gmail/edit", "remove": "/pools/gmail/remove",
        "how": ("address, password, then the 2fa secret or the recovery "
                "address - one account per line, tabs or commas between"),
        "columns": ("Address", "Status", "2FA", "Seller", "On phone"),
    },
    "gpt": {
        "name": "GPT accounts", "under": "waiting for a phone",
        "one": "account",
        "add": "may_add_gpt", "manage": "may_add_gpt",
        "preview": "/pools/gpt/preview", "free": "/pools/gpt/free",
        "edit": "/pools/gpt/edit", "remove": "/pools/gpt/remove",
        "how": ("address, password, then the 2fa secret - one account per "
                "line, tabs or commas between"),
        "columns": ("Address", "Status", "On phone"),
    },
    "spotify": {
        "name": "Spotify accounts", "under": "waiting for a phone",
        "one": "account",
        # The same tick as the GPT pool: it is the same table and the
        # same job - keeping the account stock - and a second tick to
        # hand out would only be one more thing to forget (2026-09-17).
        "add": "may_add_gpt", "manage": "may_add_gpt",
        "preview": "/pools/spotify/preview", "free": "/pools/spotify/free",
        "edit": "/pools/spotify/edit", "remove": "/pools/spotify/remove",
        "how": ("email, then the password - one account per line, tabs or "
                "commas between"),
        "columns": ("Address", "Status", "Category", "On phone"),
    },
    "proxy": {
        "name": "Proxies", "under": "free IPs", "one": "IP",
        # Whoever may change a phone's exit may keep the exits: it was the
        # admin's alone, and the operator had no way to put a dead exit
        # back or add one (2026-09-08).
        "add": "may_change_proxy", "manage": "may_change_proxy",
        "preview": "/pools/proxy/preview", "free": "/pools/proxy/free",
        "test": "/pools/proxy/test",
        "edit": "", "remove": "/pools/proxy/remove",
        "how": ("name, then the address - socks5://user:pass@host:port - "
                "one exit per line"),
        "test_all": "/pools/proxy/test-all",
        "free_all": "/pools/proxy/free-all",
        "columns": ("Name", "State", "Address", "Exit IP", "Used", "Phone"),
    },
}

#: The proxy sheet's chips, and `all` pressed when it opens - the exits
#: are one list a person reads whole, not a queue with a working end (the
#: operator, 2026-09-09). `all` is the empty group, which is how the sift
#: says "no chip".
#:
#: Two groups, not four: what a person does with the exits is one of two
#: things, and the split is exactly the one they act on. Either an exit
#: is working - free, or under a phone - or it is a job: dead, wanting a
#: new address, or set aside by the host gate. The second is the list
#: they work down with the vendor's panel open, and then free in one
#: press (the operator, 2026-09-14).
IN_PLAY = "free / on a phone"
SET_ASIDE = "dead / set aside"
_PROXY_GROUPS = ("", IN_PLAY, SET_ASIDE)


def _proxy_group(state: str) -> str:
    """Which proxy chip a row is under. A build that has just taken an
    exit (`starting`) is in play - that is where it is going; everything
    that is not in play is a job, whatever word it wears."""
    if state in ("free", "on a phone", "starting"):
        return IN_PLAY
    return SET_ASIDE


def _row_group(kind: str, state: str) -> str:
    return _proxy_group(state) if kind == "proxy" else _group_of(state)

#: The three views of a pool, in the order the chips read. `current` is
#: pressed when the sheet opens: it is the list the farm builds from.
_POOL_GROUPS = ("current", "errored", "spent")


def _group_of(state: str) -> str:
    """Which chip a row is under (the operator asked for three,
    2026-09-08). `current` is what the farm can still use - free, on a
    phone, or set aside by hand; `spent` is finished with; everything
    else is a word a run left on the row, which is the list the seller
    is asked about."""
    if state in ("used", "delivered"):
        return "spent"
    if state in ("free", "on a phone", "set aside", "set_aside"):
        return "current"
    return "errored"


def _group_chips(kind: str, rows: list[dict]) -> str:
    """The chips with their counts: current / errored / spent for the
    accounts, all / free / on a phone / dead for the exits."""
    if kind == "proxy":
        counts: dict[str, int] = {"": len(rows)}
        for row in rows:
            g = _proxy_group(str(row.get("state") or ""))
            counts[g] = counts.get(g, 0) + 1
        return ('<span class="chips" role="group" aria-label="Show">'
                + "".join(
                    f'<button type="button" class="pill" data-group="{g}" '
                    f'aria-pressed="{"true" if g == "" else "false"}">'
                    f'{g or "all"}<b>{counts.get(g, 0)}</b></button>'
                    for g in _PROXY_GROUPS)
                + "</span>")
    counts = {g: 0 for g in _POOL_GROUPS}
    for row in rows:
        counts[_group_of(str(row.get("state") or ""))] += 1
    return ('<span class="chips" role="group" aria-label="Show">'
            + "".join(
                f'<button type="button" class="pill" data-group="{g}" '
                f'aria-pressed="{"true" if g == "current" else "false"}">'
                f'{g}<b>{counts[g]}</b></button>' for g in _POOL_GROUPS)
            + "</span>")


def _kind_chips(kind: str, rows: list[dict]) -> str:
    """Spotify's second row of chips: which kind of account.

    The status chips answer "can this still be used". These answer the
    other question, which for this pool is the first one: which kind is
    it. The two categories are two stocks that go on two kinds of phone,
    and one list with both in it is a list you read twice (the operator,
    2026-09-17). They sift together, so `error / current` is one press
    away from `error / spent`.
    """
    if kind != "spotify":
        return ""
    counts = {word: sum(1 for r in rows
                        if str(r.get("category") or "") == word)
              for word in ("normal", "error")}
    chips = [("", "both", len(rows))]
    chips += [(word, word, counts[word]) for word in ("normal", "error")]
    return ('<span class="chips kinds" role="group" aria-label="Kind">'
            + "".join(
                f'<button type="button" class="pill{_kind_class(word)}" '
                f'data-cat="{word}" '
                f'aria-pressed="{"true" if not word else "false"}">'
                f'{said}<b>{count}</b></button>'
                for word, said, count in chips)
            + "</span>")


def _kind_class(word: str) -> str:
    return f" kind {word}" if word else ""


def _cat_attr(kind: str, row: dict) -> str:
    """The row's Spotify kind, for the chips above to sift on."""
    if kind != "spotify":
        return ""
    return f' data-cat="{esc(str(row.get("category") or ""))}"'


def _proxy_note(row: dict) -> str:
    """The one line the tab's Note column carried, cut to what the state
    makes useful: why a dead exit is dead, how long a phone has had one,
    how long ago a build took one that has no phone yet."""
    state = str(row.get("state") or "")
    note = str(row.get("note") or row.get("error") or "").strip()
    if state == "free":
        return ""
    if state == "on a phone":
        since = _ago(row.get("updated_at"))
        return f"since {since}" if since else ""
    if state == "starting":
        since = _ago(row.get("claimed_at") or row.get("updated_at"))
        return ("a build took it " + since + "; the phone is being created"
                if since else "a build took it; the phone is being created")
    # Dead, needs a new IP, broken: the reason, whole - it is a hover now,
    # not a cell (the operator, 2026-09-09).
    return note


def _pool_cells(kind: str, row: dict) -> list[str]:
    """One pool row, in the columns that pool's card names."""
    state = str(row.get("state") or "")
    if kind == "proxy":
        host = str(row.get("host") or "")
        port = row.get("port")
        where = f"{host}:{port}" if host and port else host
        return [str(row.get("address") or where or "?"), state, where or "-",
                str(row.get("exit_ip") or "-"),
                str(row.get("times_used") if row.get("times_used") is not None
                    else "-"),
                str(row.get("serial") or "-")]
    if kind == "spotify":
        return [str(row.get("address") or ""), state,
                str(row.get("category") or "-"),
                str(row.get("serial") or "-")]
    if kind == "gpt":
        # No Note column: what it held rides on the status pill's hover,
        # and the room goes to the buttons (the contract, 2026-09-05).
        return [str(row.get("address") or ""), state,
                str(row.get("serial") or "-")]
    return [str(row.get("address") or ""), state,
            str(row.get("second") or "-"),
            str(row.get("seller") or "-"), str(row.get("serial") or "-")]


def _cell_html(kind: str, index: int, cell: str, note: str) -> str:
    """One cell of a pool's table. The second column is always the row's
    state, and Spotify's third is its category - both are pills; the
    rest is text, escaped."""
    if index == 1:
        return _state_pill(cell, note)
    if kind == "spotify" and index == 2:
        return _category_pill(cell)
    return esc(cell)


def _category_pill(category: str) -> str:
    """A Spotify account's kind, in a colour of its own.

    Not one of the status badges: the category is not a state - it never
    changes on its own, and the two are read together on the same row.
    The words are the seller's, and what they mean is on the card and in
    the sheet beside them (2026-09-17).
    """
    word = str(category or "").strip().lower()
    if word not in ("normal", "error"):
        return '<span class="dim">-</span>'
    return f'<span class="cat {word}">{word}</span>'


def _state_pill(state: str, note: str = "") -> str:
    """A row's state in the badge the rest of the console already wears.

    `attn` for anything else on purpose: a word this reader has never
    seen - a new verdict, a status typed by hand - is a row somebody
    has to look at, and colouring it as ordinary would hide exactly
    the row that is not.
    """
    colour = ("free" if state == "free" else
              "bad" if state in ("broken", "dead") else
              "on_phone" if state in ("on a phone", "starting") else "attn")
    title = f' title="{esc(note)}"' if note else ""
    return f'<span class="badge {colour}"{title}>{esc(state or "-")}</span>'


def _send_form(user: dict, address: str, back: str = "/") -> str:
    """One waiting account, onto the next warm phone.

    The tick-and-send list this replaces stood in its own panel, which
    meant choosing accounts in one place and reading about them in
    another. The button belongs on the row: an account is sent one at a
    time, and one at a time is what a button is.

    Drawn only where the farm actually signs accounts in by hand
    (`MANUAL_LOGIN`) and only for somebody who may - otherwise it is a
    button that leads nowhere.
    """
    return (f'<form method="post" class="inline" action="/accounts/login">'
            f'{_csrf(user)}'
            f'<input type="hidden" name="addresses" value="{esc(address)}">'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<button class="quiet send" data-choose="{esc(address)}" '
            f'title="sign this account into a phone">&rarr; phone</button>'
            f'</form>')


def _may_send(user: dict, manual_login: bool) -> bool:
    return bool(manual_login) and _may(user, "may_login_accounts")


def _pool_queue(kind: str, rows: list[dict], user: dict,
                manual_login: bool, quiet: bool = False) -> str:
    """What is actually free, under the number that counts it.

    Every one of them, in a box that scrolls past the first few: the
    number above answers "how many", and this answers the other question
    a person has at a glance - which ones. A list that stopped at four
    made the fifth look like it did not exist.
    """
    free = [r for r in rows if (r.get("state") or "") == "free"]
    if not free:
        if quiet:
            return ""                # the card's alert has already said it
        held = len(rows)
        return (f'<p class="railnote">Nothing free. '
                f'{_plural(held, "row")} held or set aside.</p>' if held
                else '<p class="railnote">The pool is empty.</p>')
    send = kind == "gpt" and _may_send(user, manual_login)
    # Every free row, in a box that scrolls past the first few: the count
    # above says how many, and a list that stops at four made the fifth
    # look like it did not exist (the operator, 2026-09-05).
    items = []
    for row in free:
        label = str(row.get("address") or "?")
        tag = (str(row.get("seller") or "") if kind == "gmail" else
               str(row.get("category") or "") if kind == "spotify" else
               f'{row.get("host") or ""}:{row.get("port") or ""}'
               if kind == "proxy" and row.get("host") else "")
        # The Spotify kind in its own colour, the mark the split above
        # and the sheet below both wear.
        aside = (_send_form(user, label) if send
                 else _category_pill(tag) if kind == "spotify"
                 else f'<span class="tag">{esc(tag)}</span>')
        items.append(f'<li><span class="t" title="{esc(label)}">'
                     f'{esc(label)}</span>{aside}</li>')
    return f'<ul class="queue">{"".join(items)}</ul>'


def _pool_card(kind: str, count: int, rows: list[dict], colour: str,
               why: str, user: dict, manual_login: bool = False,
               alerts: list[dict] | None = None) -> str:
    meta = _POOL_KINDS[kind]
    # One door. `+ add` and `Manage all` opened the same manager, one
    # focused on the paste box and one on the search, and two buttons that
    # go to the same place read as two places (the operator, 2026-09-05).
    # The paste box is the first thing in the manager anyway.
    #
    # The proxy pool is the admin's: they get the door. An operator used to
    # get the word "admin" where the button goes - a label that answers a
    # question nobody asked and offers nothing, on the one card they cannot
    # open. The count is what they came for: it says whether there is an
    # exit to move a phone onto (the operator, 2026-09-07).
    # The proxy pool keeps the admin's door whatever the switches say -
    # it was theirs alone until the tick opened it (2026-09-08).
    opens = ((_may(user, meta["manage"])
              or (kind == "proxy" and user.get("role") == "admin"))
             if meta["manage"] else user.get("role") == "admin")
    # Short but not empty: the card says so in amber, under its number,
    # in the words the title used to keep for a hover.
    # Gmail only: "fewer than the phones the keeper keeps warm" is a fact
    # about Gmails, since each phone spends one. An exit is reused.
    short = (f'<p class="railnote warn">{esc(why[:1].upper() + why[1:])}</p>'
             if kind == "gmail" and colour == "amber" and count and not alerts
             else "")
    add = (f'<button type="button" class="go small" data-pool="{kind}">'
           f'Manage</button>' if opens else "")
    return (
        f'<section class="pool" title="{esc(why)}">'
        f'<header><b style="color:var(--{colour})">{count}</b>'
        f'<span class="t">{esc(meta["name"])}<i>{esc(meta["under"])}</i></span>'
        f'{add}</header>'
        f'{_pool_alerts(alerts or [])}{short}{_category_split(kind, rows)}'
        f'{_pool_queue(kind, rows, user, manual_login, quiet=bool(alerts))}'
        f'</section>')


def _category_split(kind: str, rows: list[dict]) -> str:
    """The Spotify card's two numbers. One total answers nothing here:
    the categories go on different phones, so a person reading the card
    wants to know how many of each are free (2026-09-17)."""
    if kind != "spotify":
        return ""
    free = [r for r in rows if (r.get("state") or "") == "free"]
    tally = {word: sum(1 for r in free
                       if (r.get("category") or "") == word)
             for word in ("normal", "error")}
    return ('<p class="split">'
            + "".join(f'<span class="cat {word}">{word}</span>'
                      f'<b>{tally[word]}</b>'
                      for word in ("normal", "error"))
            + '</p>')


def _pool_alerts(alerts: list[dict]) -> str:
    """What the pass said about this pool, in its own card: the lead
    sentence, in the colour of how bad it is. The whole sentence is one
    hover away, and the count above it is the rest of the story."""
    lines = []
    for a in alerts:
        text = str(a.get("text") or "")
        lead = text.partition(". ")[0].rstrip(".")
        # The whole sentence a hover away - only when there is more of it.
        more = f' title="{esc(text)}"' if lead != text.rstrip(".") else ""
        lines.append(f'<p class="railnote {esc(a.get("level", "warn"))}"'
                     f'{more}>{esc(lead)}</p>')
    return "".join(lines)


def _supply_card(data: dict, user: dict, manual_login: bool = False,
                 alerts: dict | None = None) -> str:
    """The three pools, stacked, each showing what is actually in it.

    Vertical rather than across the top, because stock is something a
    person checks and occasionally tops up - it is not what they are
    watching. Standing it on its side gives the table the whole width and
    costs the stock nothing: three numbers read as well in a column.

    Under each number, the rows the number counts, in a box that
    scrolls. The
    count answers "how many" and was the whole card; the list answers the
    question a person actually had next, which is "which ones", and it
    was two pages away. Everything else is behind Manage, because a rail
    that scrolls is a second page nobody reads.

    Proxies carry no `+`: keeping that pool alive is the admin's job, and
    an operator's power over an exit is Change IP on one phone. A button
    that leads nowhere is worse than no button, so the card says who owns
    it instead.
    """
    return _cards(("gmail", "proxy"), data, user, manual_login, alerts)


def _accounts_card(data: dict, user: dict, manual_login: bool = False,
                   alerts: dict | None = None) -> str:
    """The other rail: the accounts somebody signs in on a phone.

    GPT and Spotify sit together because that is what they are - stock
    waiting for a device - and the Claude pool joins them when its login
    exists. They keep the right, where the Send presses are, while the
    stock the keeper builds from stands above the table (the operator,
    2026-09-17).
    """
    return _cards(("gpt", "spotify"), data, user, manual_login, alerts)


def _cards(kinds: tuple, data: dict, user: dict, manual_login: bool,
           alerts: dict | None) -> str:
    """The cards of the named pools, in the order they are listed here."""
    stock = data.get("stock") or {}
    pulse = data.get("pulse") or {}
    target = int(pulse.get("target") or 0)
    warm = int(pulse.get("warm") or 0)
    gmail = int((stock.get("gmail") or {}).get("free") or 0)
    proxy = int((stock.get("proxy") or {}).get("free") or 0)
    awaiting = int((stock.get("app") or {}).get("awaiting") or 0)
    short = f"fewer than the {target} phones the keeper keeps warm"

    # Dicts rather than tuples: the pool's key and its label are the same
    # word in two spellings, and side by side in a tuple that is exactly
    # what the label sweep is written to catch - rightly, because the next
    # reader has to guess which position means which.
    rows = [
        {"kind": "gmail", "count": gmail,
         "colour": ("red" if not gmail else "amber" if gmail < target
                    else "bright"),
         "why": ("nothing can be built until rows are added" if not gmail
                 else short if gmail < target else "free to build with")},
        {"kind": "gpt", "count": awaiting,
         "colour": "amber" if awaiting > warm else "bright",
         "why": (f"{awaiting - warm} of them have no phone to go to"
                 if awaiting > warm else "awaiting login")},
    ]
    # Spotify's number is its free rows, and the line under it is the
    # split a person acts on: the two categories go on different phones,
    # so "twenty free" alone answers nothing (2026-09-17).
    spotify = data.get("spotify") or {}
    normal = int(spotify.get("normal") or 0)
    errored = int(spotify.get("error") or 0)
    both = normal + errored
    rows.append(
        {"kind": "spotify", "count": both,
         "colour": "bright" if both else "dim",
         "why": (f"{normal} normal, {errored} error" if both
                 else "no Spotify account is waiting")})
    rows.append(
        {"kind": "proxy", "count": proxy,
         "colour": ("red" if not proxy else "amber" if proxy < target
                    else "bright"),
         "why": ("no free exit - the next build has nowhere to go out from"
                 if not proxy else short if proxy < target
                 else "free to build with")})

    listed = data.get("pool_rows") or {}
    return "".join(
        _pool_card(row["kind"], row["count"], listed.get(row["kind"]) or [],
                   row["colour"], row["why"], user, manual_login,
                   (alerts or {}).get(row["kind"]) or [])
        for row in rows if row["kind"] in kinds)


def _pool_add_box(kind: str, user: dict, rows: list[dict] | None = None) -> str:
    """The paste box, inside the manager rather than folded into the card.

    Same door as before - `/pools/<kind>/preview`, which shows what it
    read and asks before writing. What changed is only where it is: a
    fold on a card is a form you open on top of the thing you were
    reading, and this is a place to stand while you work on one pool.
    """
    meta = _POOL_KINDS[kind]
    if not meta["add"] or not _may(user, meta["add"]):
        return ""
    return (f'<form class="addbox" method="post" action="{meta["preview"]}">'
            f'{_csrf(user)}<input type="hidden" name="back" value="/">'
            f'<label for="paste-{kind}">Add to the pool</label>'
            f'{_category_field(kind)}'
            f'<textarea id="paste-{kind}" name="pasted" rows="3" '
            f'spellcheck="false" placeholder="{esc(meta["how"])}"></textarea>'
            f'<div class="addrow">{_seller_field(kind, rows or [])}'
            f'<button class="go">Preview</button>'
            f'<span class="dim">{_add_note(kind)}</span></div></form>')


def _add_note(kind: str) -> str:
    """The line beside Preview. Spotify says the one thing a paste can
    get wrong that the box cannot catch: the kinds go on different
    phones, and a line does not say which kind it is."""
    seen = "nothing is written until you have seen what it read"
    return f"one kind per paste &mdash; {seen}" if kind == "spotify" else seen


#: What each Spotify category means, in the words beside the box. The
#: seller's words are kept - the operators know them - and the rule they
#: stand for is written out wherever they appear (the operator,
#: 2026-09-17).
SPOTIFY_CATEGORIES = (("normal", "goes on a phone with no Gmail"),
                      ("error", "goes on a phone that has a Gmail"))


def _category_field(kind: str) -> str:
    """Which kind of Spotify account this paste is. One category per
    paste: a line cannot say which it is, and a box that guesses would
    put an account on a phone it cannot work on.

    Two tiles, not a dropdown. The kind is the one thing about this pool
    a person has to get right, and an option list hid both the choice
    and the rule behind it until it was opened - beside a label, a hint
    and a box, which made this pool look nothing like the other three
    (the operator, 2026-09-17). Each tile wears the mark its rows wear
    in the table, and says out loud which phone that kind goes on.
    """
    if kind != "spotify":
        return ""
    tiles = []
    for index, (value, rule) in enumerate(SPOTIFY_CATEGORIES):
        tiles.append(
            f'<input type="radio" name="category" value="{value}" '
            f'id="cat-{value}"{" checked" if not index else ""}>'
            f'<label class="{value}" for="cat-{value}">'
            f'<b>{value}</b><i>{esc(rule)}</i></label>')
    return (f'<div class="kindpick" role="radiogroup" aria-label="Which kind '
            f'of account">{"".join(tiles)}</div>')


def _seller_field(kind: str, rows: list[dict]) -> str:
    """One field, pick or new: the sellers already in the pool drop down,
    and a name that is not there yet is simply typed."""
    if kind != "gmail":
        return ""
    # The names as they were written, which is what a person types.
    options = "".join(f'<option value="{esc(shown)}">'
                      for shown, _ in _sellers_of(rows).values())
    # No date box: stock is bought the day it is pasted, and the one
    # picker nobody used made the row look like a form (the operator,
    # 2026-09-08). `purchased_on` is stamped today by the add.
    return (f'<input name="seller" list="sellers-known" class="mono seller"'
            f' placeholder="seller - pick or type a new one"'
            f' autocomplete="off"><datalist id="sellers-known">{options}'
            f'</datalist>')


def _pool_row_doors(kind: str, row: dict, user: dict,
                    manual_login: bool = False) -> str:
    """Edit and Remove for one row, where those doors exist.

    Remove asks first - the confirm page the pool tabs already use - so
    the one destructive thing here cannot happen on a mis-click.
    """
    meta = _POOL_KINDS[kind]
    address = str(row.get("address") or "")
    if not address or not meta["manage"] or not _may(user, meta["manage"]):
        return ""
    doors = []
    state = str(row.get("state") or "")
    # The proxy routes name a row by `name`; the account routes by
    # `address`. The cell is the same one either way.
    field = "name" if kind == "proxy" else "address"
    if kind == "gpt" and state != "on a phone" \
            and _may_send(user, manual_login):
        doors.append(_send_form(user, address))
    if kind == "proxy":
        # The exits, by state - the doors the Proxy tab's blank-the-cell
        # and delete-the-row gave, named (the operator, 2026-09-09).
        #   free          Test, Remove
        #   dead          Test (answers -> free again), Remove
        #   needs new IP  Free (tested first), Test, Remove
        #   suspect       Free (tested first), Test, Remove - a host Google
        #                 kept challenging, set aside by the farm or by
        #                 hand (2026-09-09)
        #   starting      Free - only for one a dead run left behind; a
        #                 live build's is freed by nobody but that build,
        #                 and the pass frees a stale one on its own.
        #   on a phone    nothing: the phone decides.
        if state == "on a phone":
            return ""
        if state in ("needs new IP", "starting", "suspect"):
            doors.append(
                f'<form method="post" action="{meta["free"]}">{_csrf(user)}'
                f'<input type="hidden" name="{field}" value="{esc(address)}">'
                f'<input type="hidden" name="back" value="/">'
                f'<button class="quiet ok" data-busy="Testing…" title="'
                + ("tested, and back on the shelf if it answers"
                   if state in ("needs new IP", "suspect") else
                   "back on the shelf - only if the build that took it is "
                   "gone; a stale one is freed on its own within minutes")
                + '">Free</button></form>')
        if state != "starting":
            doors.append(
                f'<form method="post" action="{meta["test"]}">{_csrf(user)}'
                f'<input type="hidden" name="{field}" value="{esc(address)}">'
                f'<input type="hidden" name="back" value="/">'
                f'<button class="quiet" data-busy="Asking…" title="'
                + ("ask GeeLark whether it answers; one that does is free "
                   "again" if state != "free" else
                   "ask GeeLark whether it answers; one that does not is "
                   "marked dead")
                + '">Test</button></form>')
            doors.append(
                f'<form method="post" action="{meta["remove"]}">{_csrf(user)}'
                f'<input type="hidden" name="{field}" value="{esc(address)}">'
                f'<input type="hidden" name="back" value="/">'
                f'<button class="quiet bad">Remove</button></form>')
        return f'<div class="doors">{"".join(doors)}</div>'
    # A row a run set aside gets Free: one press, back on the shelf, and
    # nothing else on the row touched (the operator, 2026-09-06).
    if meta.get("free") and state not in ("free", "on a phone"):
        doors.append(
            f'<form method="post" action="{meta["free"]}">{_csrf(user)}'
            f'<input type="hidden" name="{field}" value="{esc(address)}">'
            f'<input type="hidden" name="back" value="/">'
            f'<button class="quiet ok" title="back on the shelf, as it is">'
            f'Free</button></form>')
    if meta["edit"]:
        doors.append(
            f'<button type="button" class="quiet" data-edit="{esc(address)}"'
            f' data-pool="{kind}">Edit</button>')
    if meta["remove"]:
        doors.append(
            f'<form method="post" action="{meta["remove"]}">{_csrf(user)}'
            f'<input type="hidden" name="{field}" value="{esc(address)}">'
            f'<input type="hidden" name="back" value="/">'
            f'<button class="quiet bad">Remove</button></form>')
    return f'<div class="doors">{"".join(doors)}</div>'


def _pool_editor(kind: str, user: dict, rows: list[dict]) -> str:
    """The editor: one dialog per sheet, filled from the row whose Edit
    was pressed, on top of the list.

    It was a row of six boxes squeezed under the row, with the password
    and the key blanked "for safety" - so a person checking whether a key
    had been pasted wrong had nothing to check it against, and the
    preview shows both to the same people anyway (the operator,
    2026-09-08). Now it opens showing what the row holds.

    Server-rendered, so the form is the pool tab's own form field for
    field, with the token in it; the script only copies the row's values
    in by `.value`, which nothing can read as markup. Status offers free
    (back on the shelf) and set aside (not to be handed out, by hand);
    the script adds the word the row has now, and greys the field for a
    row a phone is behind - the phone decides that one, or two things
    would be writing the same cell.
    """
    meta = _POOL_KINDS[kind]
    if not meta["edit"] or not _may(user, meta["manage"]):
        return ""
    gmail = kind == "gmail"
    sellers = "".join(f'<option value="{esc(shown)}">'
                      for shown, _ in _sellers_of(rows).values())
    return (
        f'<dialog class="editor" data-editor="{kind}" '
        f'aria-labelledby="edit-{kind}">'
        f'<form method="post" action="{meta["edit"]}">{_csrf(user)}'
        f'<input type="hidden" name="address" value="">'
        f'<input type="hidden" name="back" value="/">'
        f'<header><h4 id="edit-{kind}">Edit</h4>'
        f'<span class="mono" data-who></span></header>'
        f'<label class="field"><span>Address</span>'
        f'<input name="new_address" autocomplete="off" autofocus></label>'
        f'<label class="field"><span>Password</span>'
        f'<input name="password" autocomplete="off" spellcheck="false">'
        f'</label>'
        # A Spotify row has no second factor to show - email and
        # password are the whole credential - and has a category
        # instead, which is the one thing a paste can get wrong and the
        # only thing here worth changing afterwards (2026-09-17).
        + ("" if kind == "spotify" else
           '<label class="field"><span>'
           + ("2FA secret or recovery address" if gmail else "2FA secret")
           + '</span><input name="secret" autocomplete="off"'
             ' spellcheck="false" placeholder="none"></label>'
             # Blank leaves the secret as it was: the box shows it now,
             # so an emptied box is more likely a slip than a decision,
             # and a key somebody paid for was once deleted by a blank
             # that meant "clear" (2026-09-07). The tick is how you
             # mean it.
             '<label class="tick"><input type="checkbox" '
             'name="clear_secret" value="1"> no second factor &mdash; '
             'clear it</label>')
        + ('<label class="field"><span>Category</span>'
           '<select name="category">'
           + "".join(f'<option value="{v}">{esc(v)} - {esc(s)}</option>'
                     for v, s in SPOTIFY_CATEGORIES)
           + '</select></label>' if kind == "spotify" else "")
        + '<div class="two">'
        + (f'<label class="field"><span>Seller</span>'
           f'<input name="seller" list="sellers-edit" autocomplete="off">'
           f'<datalist id="sellers-edit">{sellers}</datalist></label>'
           if gmail else "")
        + '<label class="field"><span>Status</span>'
          '<select name="state"><option value="free">free</option>'
          '<option value="set aside">set aside</option></select></label>'
          '</div>'
          '<div class="row"><button type="button" class="quiet" '
          'data-close-edit="1">Cancel</button>'
          '<button class="go">Save</button></div>'
          '</form></dialog>')


def _pool_table(kind: str, rows: list[dict], user: dict,
                manual_login: bool = False) -> str:
    meta = _POOL_KINDS[kind]
    head = "".join(f"<th>{esc(c)}</th>" for c in meta["columns"])
    doors = bool(meta["manage"]) and _may(user, meta["manage"])
    span = len(meta["columns"]) + (1 if doors else 0)
    lines = []
    for row in rows:
        cells = _pool_cells(kind, row)
        note = str(row.get("note") or row.get("error") or "")
        if kind == "proxy":
            # No Note column (the operator, 2026-09-09): the why and the
            # since-when ride on the pill's hover instead.
            note = _proxy_note(row) or note
        drawn = "".join(
            f'<td>{_cell_html(kind, i, cell, note)}</td>'
            for i, cell in enumerate(cells))
        last = (f"<td>{_pool_row_doors(kind, row, user, manual_login)}</td>"
                if doors else "")
        # Lower-cased, and only the row's own words - the address, what
        # state it is in, whose it was, which phone has it. The state
        # words are worth keeping: "broken" and "set aside" are exactly
        # what somebody types when they want to see what wants them.
        findable = " ".join(str(c) for c in cells if c).lower()
        state = str(row.get("state") or "")
        # What the editor opens with, on the row itself - the one dialog
        # per sheet is filled from here. Never in `data-find`: a search
        # for a password would be a strange thing to answer.
        held = (f' data-password="{esc(str(row.get("password") or ""))}"'
                f' data-secret="{esc(str(row.get("secret") or ""))}"'
                f' data-sellername="{esc(str(row.get("seller") or "").strip())}"'
                if doors else "")
        # What a single-row answer is put back by, so a Test or a Free
        # replaces its own row instead of the page under it (2026-09-14).
        key = f'{kind}:{row.get("address") or row.get("id") or ""}'
        lines.append(f'<tr data-state="{esc(state)}"'
                     f' data-key="{esc(key)}"'
                     f' data-group="{_row_group(kind, state)}"'
                     f'{_cat_attr(kind, row)}'
                     f' data-find="{esc(findable)}"'
                     f' data-seller="{esc(_seller_key(row))}"{held}>'
                     f'{drawn}{last}</tr>')
    if not lines:
        return ('<p class="empty">Nothing in this pool that anybody still '
                'has a decision about.</p>')
    return (f'<table class="pooltable"><thead><tr>{head}'
            f'{"<th></th>" if doors else ""}</tr></thead>'
            f'<tbody>{"".join(lines)}'
            f'<tr class="none" hidden><td colspan="{span}">'
            f'Nothing matches that.</td></tr></tbody></table>')


def _capped(rows: list[dict], totals: dict | None) -> str:
    """Said out loud when the list is not the whole pool.

    The cap was silent and the search only looks at what was drawn, so an
    address that happened to be the 340th row answered "Nothing matches
    that" to a search that had never seen it (2026-09-07). Live and spent
    rows are capped apart, so each is said apart.
    """
    shown = {"live": 0, "spent": 0}
    for row in rows:
        part = ("spent" if _group_of(str(row.get("state") or "")) == "spent"
                else "live")
        shown[part] += 1
    said = []
    for part, name in (("live", "current and errored rows"),
                       ("spent", "spent rows")):
        total = int((totals or {}).get(part) or 0)
        if total > shown[part]:
            said.append(f"the newest {shown[part]} of {total} {name}")
    if not said:
        return ""
    return (f'<p class="dim capped">showing {" and ".join(said)} '
            f'&mdash; the search only looks at these.</p>')


def _seller_key(row: dict) -> str:
    """One seller, one key. The filter kept the cell as typed, so "Ali"
    and "ali" were two people in the list and a trailing space showed a
    count beside a name that then matched nothing (2026-09-07). The pool
    query already groups on `lower(seller)`."""
    return str(row.get("seller") or "").strip().lower()


def _sellers_of(rows: list[dict]) -> dict[str, tuple[str, int]]:
    """Every seller in these rows as `{key: (as they wrote it, how many)}`,
    keyed the way `_seller_key` keys a row."""
    found: dict[str, tuple[str, int]] = {}
    for row in rows:
        key = _seller_key(row)
        if not key:
            continue
        shown, count = found.get(key, (str(row.get("seller") or "").strip(), 0))
        found[key] = (shown, count + 1)
    return dict(sorted(found.items()))


def _seller_filter(kind: str, rows: list[dict]) -> str:
    """The seller picker beside the search - Gmail only, the one pool
    that has sellers. Each option says how many rows are that seller's."""
    if kind != "gmail":
        return ""
    sellers = _sellers_of(rows)
    if not sellers:
        return ""
    options = "".join(
        f'<option value="{esc(key)}">{esc(shown)} · {count}</option>'
        for key, (shown, count) in sellers.items())
    return (f'<select class="sellerpick" aria-label="Seller">'
            f'<option value="">every seller</option>{options}</select>')


def _set_aside_rows(rows: list[dict]) -> list[dict]:
    """The exits that are somebody's job: dead, wanting a new address, or
    set aside by the host gate."""
    return [r for r in rows
            if _proxy_group(str(r.get("state") or "")) == SET_ASIDE]


def _test_all_door(kind: str, rows: list[dict], user: dict) -> str:
    """One button that tests every exit no build is holding - the check
    the pass runs on its own schedule, on demand. Says how many are set
    aside, since those are the ones an answer can change."""
    meta = _POOL_KINDS[kind]
    if not meta.get("test_all") or not _may(user, meta["manage"]):
        return ""
    aside = len(_set_aside_rows(rows))
    return (f'<form method="post" action="{meta["test_all"]}" class="inline">'
            f'{_csrf(user)}<input type="hidden" name="back" value="/">'
            f'<button class="quiet" data-busy="Testing…" '
            f'title="ask GeeLark about every exit no build is holding; a '
            f'dead one that answers is free again">'
            f'Test all{f" · {aside} set aside" if aside else ""}</button>'
            f'</form>')


def _free_all_door(kind: str, rows: list[dict], user: dict) -> str:
    """The press that answers the whole set-aside list at once.

    A person changes the addresses at the vendor for the exits in that
    list and then wants them all back; one at a time was nine presses in
    an afternoon (the operator, 2026-09-14). Each is tested first, so
    this frees what answers and leaves what does not as dead - and it
    clears each host's judgement the way a single Free does.

    Shown only under its own chip: it is the answer to that list, and a
    button that acts on rows you are not looking at is a trap. `sift`
    does the showing.
    """
    meta = _POOL_KINDS[kind]
    if not meta.get("free_all") or not _may(user, meta["manage"]):
        return ""
    aside = len(_set_aside_rows(rows))
    return (f'<form method="post" action="{meta["free_all"]}" class="inline"'
            f' data-for-group="{esc(SET_ASIDE)}" hidden>'
            f'{_csrf(user)}<input type="hidden" name="back" value="/">'
            f'<button class="quiet ok" data-busy="Testing {aside}…" '
            f'title="test every exit in this list and free the ones that '
            f'answer - for after you have changed their addresses at the '
            f'vendor"{"" if aside else " disabled"}>'
            f'Free all{f" · {aside}" if aside else ""}</button></form>')


def _pool_manager(data: dict, user: dict,
                  manual_login: bool = False) -> str:
    """One pool, full size, without leaving the page.

    Everything a person does to a pool is here: what is in it, a search
    over all of it, the states as chips, the paste box, and the two doors
    on each row. The pool tabs still exist and still hold the archive -
    what left this page was the need to go there for the working list.

    Rendered shut, all three of them, rather than fetched: the rows are
    already read for the cards, the whole page is one response, and a
    manager that needs a second request is a manager that can fail to
    open.
    """
    listed = data.get("pool_rows") or {}
    if not listed:
        return ""
    sheets = []
    for kind, meta in _POOL_KINDS.items():
        rows = listed.get(kind) or []
        sheets.append(
            f'<section class="sheet" data-sheet="{kind}" hidden>'
            f'<header><h3>{esc(meta["name"])}</h3>'
            f'<button type="button" class="x" data-shut="1" '
            f'aria-label="Close">&times;</button></header>'
            f'<div class="sheetbody">'
            f'{_pool_add_box(kind, user, rows)}'
            f'<div class="filters">'
            f'{_group_chips(kind, rows)}{_kind_chips(kind, rows)}'
            f'<input type="search" class="poolfind" autocomplete="off"'
            f' placeholder="search {_plural(len(rows), "row")}">'
            f'{_seller_filter(kind, rows)}'
            f'{_test_all_door(kind, rows, user)}'
            f'{_free_all_door(kind, rows, user)}'
            # The script has always written "12 of 190 shown" into this,
            # and the CSS has always reserved the space for it, and it was
            # never rendered - so the count nobody could see is how you
            # confirm a paste of forty landed (2026-09-07).
            f'<span class="dim mono tally"></span>'
            f'</div>'
            f'{_capped(rows, (listed.get("totals") or {}).get(kind))}'
            f'<div class="tscroll">'
            f'{_pool_table(kind, rows, user, manual_login)}</div>'
            f'</div>{_pool_editor(kind, user, rows)}</section>')
    if _may_send(user, manual_login):
        sheets.append(_send_sheet(data, user))
    sheets.append(
        '<section class="sheet drawer" data-sheet="phone" hidden>'
        '<header><h3 class="mono" data-title></h3><span class="hint" data-hint>'
        '</span><button type="button" class="x" data-shut="1" '
        'aria-label="Close">&times;</button></header>'
        '<div class="sheetbody" data-drawer></div></section>')
    return f'<div class="ov" id="poolov" hidden>{"".join(sheets)}</div>'


def _send_sheet(data: dict, user: dict) -> str:
    """Which phone an account goes to: the phones that can take one, each
    with its status and exit and one Send. Only `app_only` phones nobody
    holds - a phone that already has an account is not a place to put a
    second one, and a phone somebody holds is theirs.

    The address is filled in by the script from the row that was pressed;
    without the script the row's own button sends to the next warm phone,
    which is what it always did.
    """
    # `app_account` holds a cross, not a blank, on a phone with no account
    # - the build writes one - so "no account" is the same test the cell
    # uses. Read as a plain truthy string, every warm phone failed it and
    # the sheet said none could take one (the operator, 2026-09-08).
    able = [p for p in (data.get("phones") or [])
            if (p.get("status") or "") == "app_only"
            and _no_address(p.get("app_account"))
            and not p.get("running")
            and (p.get("state") or "") not in ("taken", "done", "failed")]
    rows = "".join(
        f'<form method="post" class="pickrow" action="/accounts/login">'
        f'{_csrf(user)}<input type="hidden" name="addresses" value="">'
        f'<input type="hidden" name="serial" value="{esc(str(p["serial"]))}">'
        f'<input type="hidden" name="back" value="/">'
        f'<span class="serial mono">{esc(str(p["serial"]))}</span>'
        f'{_phone_badge(p)}<span class="age">{esc(str(p.get("proxy_name") or ""))}'
        f'</span><button class="go small" style="margin-left:auto">Send</button>'
        f'</form>' for p in able)
    body = (f'<div class="slab">{rows}</div>' if rows else
            '<p class="empty">No phone can take an account right now - '
            'every one of them already has one, or is still building.</p>')
    return (f'<section class="sheet narrow" data-sheet="send" hidden>'
            f'<header><h3>Send to a phone</h3><span class="hint mono" '
            f'data-hint></span><button type="button" class="x" data-shut="1" '
            f'aria-label="Close">&times;</button></header>'
            f'<div class="sheetbody">{body}'
            f'<p class="dim" style="margin:0;font-size:12px">The phone is '
            f'booted and the account signed into the app on it. It shows as '
            f'<b>Ready</b> when done.</p></div></section>')


#: What a blank picker means, said the same way in all three.
NEXT_FREE = "the next free one"


def _free_picker(name: str, rows, blank: str) -> str:
    """One field that is both the picker and the box.

    It was two: a `<select>` of what is free, and beneath it a second
    input for an address the pool has never heard of - which is the
    commonest reason to build one by hand at all, because an account
    bought this morning is in no pool yet. Two controls for one answer,
    and the rule about which one won lived in a sentence beside them.

    A `datalist` is one control that does both: the free rows drop down,
    and anything else is typed over them. Blank still means the pool -
    the form's default is the farm's own behaviour, and every field is a
    departure from it.
    """
    options = "".join(f'<option value="{esc(str(r.get("label") or ""))}">'
                      for r in rows or [] if r.get("label"))
    return (f'<input name="{name}" list="free-{name}" autocomplete="off"'
            f' spellcheck="false" placeholder="{esc(blank)}">'
            f'<datalist id="free-{name}">{options}</datalist>')


def _build_card(data: dict, user: dict) -> str:
    """Build one phone with credentials somebody chose.

    Open rather than folded: it is one of the two things this page is
    for, and a form nobody can see is a feature nobody has.

    One box per credential. Each is a picker of what is free and a place
    to type something else, in one control - which is what a person
    means either way. Whether the address is new is the server's business
    and not a second field to get right.
    """
    if not _may(user, "may_login_accounts"):
        return ""
    choose = data.get("choose") or {}
    stock = data.get("stock") or {}
    pulse = data.get("pulse") or {}
    exits = int((stock.get("proxy") or {}).get("free") or 0)
    if pulse.get("stopped"):
        # A stopped pass returns long before it takes the wishes, so a
        # press now is a press lost until somebody starts it again. Said
        # rather than offered, the way the no-exit branch below says it.
        return ('<div class="panel"><h3>Build one now</h3>'
                '<p class="dim">The service is stopped, so nothing will be '
                'built until an admin starts it again.</p></div>')
    if not exits:
        # No way out for a phone. Said rather than offered: a form that can
        # only be refused is worse than a sentence saying why.
        return ('<div class="panel"><h3>Build one now</h3>'
                '<p class="dim">There is no free exit to build with, so '
                'there is nothing to ask for yet.</p></div>')
    # An empty Gmail pool is not "nothing to build with": the box takes an
    # address the pool has never seen, and an account bought this morning
    # is exactly what this form is for. The hint says so instead of the
    # form hiding (2026-09-05).
    # The App box is gone: every phone carries all three apps now, so
    # which app was never a question about the phone, only about the
    # account - and the only accounts this farm holds are ChatGPT's (the
    # operator, 2026-09-12). Named here rather than read out of
    # APPS_ON_EVERY_PHONE, because the web layer does not import the
    # builder; whoever changes that setting changes this sentence in the
    # same breath.
    hint = ("Each box starts on what the farm would do by itself; pick "
            "something else, or choose your own. Every phone comes with "
            "ChatGPT, Spotify and Claude already on it.")
    if pulse.get("tripped") or pulse.get("paused"):
        hint += (" The keeper is held back right now, but a phone asked "
                 "for here is still built.")
    gmails = _label_list(choose.get("gmails"))
    apps = _label_list(choose.get("apps"))
    # Two boxes. The exit is not one of them: the build picks one and
    # swaps it whenever an install or a sign-in shows it is bad - a
    # choice made here was a choice the build had to undo (the operator,
    # 2026-09-10). Nor is the app: all three go on every phone (the
    # operator, 2026-09-12). A Gmail is the pool's next, none at all, or
    # one chosen in the dialog - typed, or picked from the free ones.
    if gmails:
        first = (f'<option value="">auto &mdash; the next free one '
                 f'({len(gmails)} free)</option>'
                 '<option value="none">none &mdash; no Google account</option>')
    else:
        first = ('<option value="" disabled>auto &mdash; the pool is empty'
                 '</option>'
                 '<option value="none" selected>none &mdash; no Google '
                 'account</option>')
    gmail_box = (f'<label class="field"><span>Gmail</span>'
                 f'<select name="gmail" data-new="gmail-new">{first}'
                 f'<option value="__new__">choose&hellip;</option>'
                 f'</select></label>')
    account_box = ('<label class="field"><span>GPT account</span>'
                   '<select name="app_account" data-new="account-new">'
                   '<option value="">none &mdash; sign in later</option>'
                   '<option value="__new__">choose&hellip;</option>'
                   '</select></label>')
    return (
        f'<div class="panel"><h3>Build one now</h3>'
        f'<p class="dim" style="margin:-6px 0 0">{hint}</p>'
        f'<form method="post" action="/phones/build" class="byhand">'
        f'{_csrf(user)}'
        + gmail_box + account_box
        + '<button class="go">Build</button>'
        # What the two dialogs typed rides here; the address itself is the
        # choice's value.
        + "".join(f'<input type="hidden" name="{name}" value="">'
                  for name in ("gmail_password", "gmail_secret",
                               "app_password", "app_secret"))
        + '</form>'
        + _new_dialog("gmail-new", "Choose a Gmail", [
            ("gmail_address", "Address", ""),
            ("gmail_password", "Password", ""),
            ("gmail_secret", "Authenticator key",
             "empty = the account has none")], rows=gmails)
        + _new_dialog("account-new", "Choose a GPT account", [
            ("app_address", "Address", ""),
            ("app_password", "Password", ""),
            ("app_secret", "2FA secret", "optional")], rows=apps)
        + '</div>')


def _label_list(rows) -> list[str]:
    return [str(r.get("label") or "") for r in rows or [] if r.get("label")]


def _new_dialog(ident: str, title: str,
                fields: list[tuple[str, str, str]],
                rows: list[str] | None = None) -> str:
    """The dialog "choose..." opens: the credential's boxes to type one,
    and under them the free rows of its pool to pick one instead, then
    Cancel and Use. Nothing here is a form field of the card - the
    script copies what was typed into the card's hidden boxes and shows
    the address as the choice; a picked row carries nothing but its
    address, since the pool has the rest."""
    boxes = "".join(
        f'<label class="field"><span>{esc(label)}</span>'
        f'<input data-field="{name}" autocomplete="off" spellcheck="false"'
        + (f' placeholder="{esc(hint)}"' if hint else "")
        + (' autofocus' if name.endswith("_address") else "")
        + '></label>' for name, label, hint in fields)
    pick = ""
    if rows is not None:
        if rows:
            pick = "".join(
                f'<label><input type="radio" name="pick-{ident}" '
                f'value="{esc(r)}"> {esc(r)}</label>' for r in rows)
        else:
            pick = ('<div class="none">The pool has nothing free - type '
                    'one above.</div>')
        pick = (f'<div class="or">or one of the free ones</div>'
                f'<div class="pick">{pick}</div>')
    return (f'<dialog class="editor" id="{ident}" aria-labelledby="{ident}-h">'
            f'<div class="dlg"><header><h4 id="{ident}-h">{esc(title)}</h4>'
            f'</header>{boxes}{pick}'
            f'<div class="row"><button type="button" class="quiet" '
            f'data-cancel="1">Cancel</button>'
            f'<button type="button" class="go" data-use="1">Use it</button>'
            f'</div></div></dialog>')


def _stopped_card(data: dict, user: dict, explain=None) -> str:
    """The credentials a run judged and set aside, in the words it used.

    This is the one thing on the page an operator has to act on rather
    than watch, and until now they could not see it at all: the count sat
    behind a link to Needs attention, which is an admin page. The list is
    small, it is theirs, and it belongs where they already are.

    Folded shut, because on a good day it is empty and on a bad one it is
    a work list rather than a headline. `<details>` rather than script:
    the fold has to work whether or not the page's one exception loaded.
    """
    stopped = data.get("stopped") or []
    if not stopped:
        return ""
    items = []
    for row in stopped:
        who = esc(str(row.get("who") or ""))
        status = str(row.get("status") or "")
        seen = ""
        if explain:
            got = explain(status)
            seen = got[0] if isinstance(got, tuple) else (got or "")
        words = esc(seen or status.replace("_", " ") or "it stopped")
        where = esc(str(row.get("serial") or ""))
        items.append(
            f'<div class="stoprow"><span class="mono cp">{who}</span>'
            f'<span class="why">{words}</span>'
            + (f'<span class="dim mono">on {where}</span>' if where else "")
            + '</div>')
    return (f'<div class="panel stopped"><h3>Needs a decision '
            f'<span class="ct">{len(stopped)}</span></h3>'
            f'<details class="fold"><summary>what stopped, and why'
            f'</summary><div class="stoplist">{"".join(items)}</div>'
            f'</details></div>')


def _did_not_finish(rows: list[dict], user: dict) -> str:
    """Phones that never warmed all the way, kept out of the shelf.

    They are not stock: something on them stopped, and offering one to a
    customer beside a ready phone is offering a phone that does not work.
    They are not nothing either - each holds a profile slot and an exit -
    so they sit under the table with what went wrong and the two things
    worth doing to one.
    """
    if not rows:
        return ""
    lines = []
    for r in rows:
        serial = str(r.get("serial") or "")
        why = (esc(str(r.get("note") or r.get("error") or ""))
               or "it stopped before it was ready")
        gmail = esc(str(r.get("gmail") or ""))
        acts = []
        if _may(user, "may_take_phones"):
            acts.append(_boot_form(user, serial))
        if _may(user, "may_change_proxy"):
            acts.append(_change_ip_form(user, serial))
        lines.append(
            f'<div class="r">{_serial_link(serial)}'
            f'<span class="body"><span class="why">{why}</span><br>'
            f'<span class="dim mono">{gmail}</span></span>'
            f'<span class="dim mono">{esc(str(r.get("proxy_name") or ""))}'
            f'</span><span class="act">{" ".join(acts)}</span></div>')
    return (f'<div class="didnot"><h3>Did not finish '
            f'<span class="ct mono">{len(rows)}</span>'
            f'<span class="why">not stock &mdash; it never warmed all the '
            f'way</span></h3>{"".join(lines)}</div>')


def _keeper_words(pulse: dict) -> tuple[str, str]:
    warm, target = int(pulse.get("warm") or 0), int(pulse.get("target") or 0)
    if pulse.get("stopped"):
        return ("Stopped — nothing is running until somebody starts it "
                "again", "red")
    if pulse.get("tripped"):
        return "Stopped by the breaker — nothing is being built", "red"
    if pulse.get("paused"):
        return "Paused — nothing new is being built", "amber"
    gate = pulse.get("gate") or {}
    if gate.get("closed"):
        # Google is refusing nearly everything; the keeper probes rather
        # than pours, and says so instead of "Building" (2026-09-14).
        wait = int(gate.get("next_probe_in") or 0)
        soon = ("a probe is going out now" if wait <= 0
                else f"next probe in {max(1, round(wait / 60))} min")
        return (f"Probing — Google let in {int(gate.get('ok') or 0)} of the "
                f"last {int(gate.get('of') or 0)} sign-ins; {soon}, and "
                f"building resumes once 2 of 4 get in", "amber")
    if warm < target and pulse.get("warning"):
        # Short, and the pass has said why nothing can be built. Calling
        # that "Building" is the page telling a story the loop is not.
        return "Idle — waiting for stock", "amber"
    if warm < target:
        return f"Building — {warm} of {target} phones warm", "amber"
    return f"Stocked — {warm} of {target} phones warm", "green"



def _wish_rows(data: dict, user: dict) -> str:
    """Phones asked for by hand, as rows of the phones table.

    One that failed says which address and why, in the builder's own
    sentence, until whoever asked presses Dismiss - theirs alone (an
    admin's too), since the row is the answer to something they asked
    for. One that has no phone yet is a dim row saying so, and leaves
    the moment its phone appears in the table as Building, built by
    them. First in the table either way: a wish is the one row that is
    somebody's own. The panel these lived in was one list too many
    (the operator, 2026-09-10).
    """
    me = str(user.get("username") or "")
    admin = user.get("role") == "admin"
    lines = []
    phones = data.get("phones") or []
    for w in data.get("wishes") or []:
        status = str(w.get("status") or "")
        who = str(w.get("asked_by") or "")
        mine = bool(who) and who == me
        if status in ("queued", "running"):
            # Its phone, once there is one, is the Building row built by
            # the same person after the wish was made; this row would
            # only say the same thing twice.
            asked_at = w.get("created_at")
            taken_over = any(
                (p.get("status") or "") == "building"
                and str(p.get("built_by") or "") == who
                and (asked_at is None or p.get("created_at") is None
                     or p["created_at"] >= asked_at)
                for p in phones)
            if taken_over:
                continue
            if w.get("no_gmail"):
                what = "a bare phone"
            else:
                what = str(w.get("gmail") or "") or "the next free Gmail"
                app = str(w.get("app") or "")
                what += {"": " &middot; no app", "spotify": " &middot; Spotify",
                         "claude": " &middot; Claude"}.get(app, "")
            word = ("a phone is being made for it" if status == "running"
                    else "waiting for a builder")
            lines.append(
                f'<tr data-view="{"mine" if mine else "theirs"}">'
                f'<td><span class="dim">&mdash;</span></td>'
                f'<td><span class="badge {"info" if status == "running" else ""}">'
                f'{"Building" if status == "running" else "Queued"}</span>'
                + (f'<span class="dim maker">asked by {esc(who)}</span>'
                   if who else "")
                + f'</td><td colspan="4" class="progress">{what} '
                f'<span class="dim">&middot; {word}</span></td>'
                f'<td class="act"></td></tr>')
            continue
        if status != "failed":
            continue
        serial = str(w.get("serial") or "")
        detail = str(w.get("detail") or "") or "it did not say why"
        if mine or admin:
            act = (f'<form method="post" class="inline" '
                   f'action="/wishes/{int(w["id"])}/dismiss">{_csrf(user)}'
                   f'<button class="quiet" title="take it off the list">'
                   f'Dismiss</button></form>')
        else:
            act = f'<span class="age">with {esc(who or "somebody")}</span>'
        lines.append(
            f'<tr data-view="{"mine" if mine else "theirs"}">'
            f'<td>{_serial_link(serial) if serial else _NO_SERIAL}</td>'
            f'<td><span class="badge failed">Failed</span>'
            + (f'<span class="dim maker">built by {esc(who)}</span>' if who
               else "")
            + f'</td><td colspan="4" class="progress">{esc(detail)}</td>'
            f'<td class="act">{act}</td></tr>')
    return "".join(lines)


def dashboard(data: dict, user: dict, said: str = "",
              manual_login: bool = False, explain=None,
              said_note: str = "") -> str:
    """The console's front page: what an operator watches, and what they
    reach for, side by side.

    The left column is the spine - the phones, and nothing competing with
    them. The right is the things a person reaches for: what stock is
    left, and the accounts with no phone yet. Standing those on their
    side rather than across the top is what gives the table its width.

    The shelf counts that used to sit above the table are gone: the table
    is already grouped, and a page should not say a number twice. So are
    the events - a developer's line on an operator's page. Everything
    that is only sometimes true still appears only when it is true.
    """
    phones = data.get("phones") or []
    building = [r for r in phones if (r.get("status") or "") == "building"]

    # A phone that did not finish is a row like the others, last, in the
    # amber of something that wants a look. It stood in its own box under
    # the table for a while; the prototype the operator chose puts it in
    # the table, and one list is easier to read than a list and a box.
    on_the_shelf = [p for p in phones
                    if (p.get("state") or "") not in ("done", "failed")]
    rows = (_wish_rows(data, user)
            + _phone_rows(dict(data, phones=on_the_shelf), user))
    table = (f'<table id="phones"><thead><tr><th>serial</th><th>status</th>'
             f'<th>gmail</th><th>account</th><th>ip</th>'
             f'<th>age</th><th></th></tr></thead>'
             f'<tbody>{rows}'
             f'<tr class="none" id="nohits" hidden><td colspan="7">'
             f'Nothing here matches that.</td></tr></tbody></table>'
             if rows else '<p class="empty">No phones yet - the keeper '
                          'builds the shortfall on its next pass.</p>')
    hint = _need(user, "may_take_phones",
                 "taking, returning and closing phones")

    # No second copy of the alert. The strip above the title already
    # carries every one of these in the same words, and a page that says
    # the same thing twice is a page where a reader learns to skip both
    # (the operator, 2026-09-05). What used to be this line's own value -
    # the link to the pool - is now the card in the rail, which is closer
    # to the hand than a link ever was.
    warning = ""

    # `hidden` until the script says otherwise: a search box that does
    # nothing is worse than none, and this page must still read without it.
    # Three views and no search box. A person here wants one of three
    # things - everything, what they can take, what they already hold -
    # and a box that filters on text answers none of those in one press.
    # `hidden` until the script says otherwise: three buttons that do
    # nothing are worse than none, and the page must still read without it.
    tools = (f'<div class="row"><h3>Phones</h3>'
             f'<span class="dim mono" id="tally">'
             f'{_plural(len(on_the_shelf), "phone")}</span>'
             f'<span class="seg" id="seg" role="group" aria-label="Show" hidden>'
             f'<button type="button" data-show="" aria-pressed="true">All'
             f'</button>'
             f'<button type="button" data-show="free" aria-pressed="false">'
             f'Free</button>'
             f'<button type="button" data-show="mine" aria-pressed="false">'
             f'With me</button></span></div>')
    # The form under the table, in the wide column, where three boxes and
    # a button fit on one line. In the rail they stacked five deep.
    main = (_said(said, _DASH_SAID, user, said_note) + warning + tools
            + f'<div class="slab"><div class="tscroll">{table}</div>'
              f'</div>{hint}'
            + _build_card(data, user))
    # The pools and the one form. Two panels went (2026-09-05, the
    # operator: "we still see attention here"), because the manager each
    # card opens is where both of them already lived:
    #
    # "Needs a decision" listed the credentials a run set aside. Every one
    # of those rows is in its pool's manager, wearing its own word, with a
    # chip that shows only them - and beside the rows it has to be judged
    # against, which a separate panel could never do.
    #
    # "Awaiting login" listed the accounts with no phone. That is the GPT
    # card's number and the list underneath it, which is where a person
    # looking for stock now looks.
    alerts = (user.get("nav") or {}).get("alerts") or []
    pooled = {kind: [a for a in alerts
                     if str(a.get("href") or "").startswith(f"/pools/{kind}")]
              for kind in _POOL_KINDS}
    rest = [a for a in alerts
            if not str(a.get("href") or "").startswith("/pools/")]
    quiet = dict(user, nav=dict(user.get("nav") or {}, alerts=[]))
    strip = _alert_strip(dict(user, nav=dict(user.get("nav") or {},
                                             alerts=rest)))
    supply = _supply_card(data, user, manual_login, pooled)
    side = _accounts_card(data, user, manual_login, pooled)

    # An alert about a pool is said in that pool's card, one line, where
    # the number it is about already is - a page-wide strip for "the Gmail
    # pool is empty" beside a card whose count is a red zero said it twice
    # (the operator, 2026-09-05). What is left for the strip is what has
    # no card: the breaker, a late pass, an error in the log. `page()` is
    # told nothing is left for it to add.
    body = (f'<div class="wide">{strip}'
            f'<div class="top"><h2>Instance manager</h2>'
            f'<span class="status">{_status_sentence(data)}'
            f'{_controls(data, user)}</span>'
            f'{_who_and_out(user)}</div>'
            f'<div class="desk">'
            f'<div class="rail"><p class="railcap">Built from</p>{supply}</div>'
            f'<div class="deskmain">{main}</div>'
            f'<aside class="side"><p class="railcap">Signed in on phones</p>'
            f'{side}</aside></div>'
            f'</div>' + _pool_manager(data, user, manual_login)
            + _DASH_SCRIPT)
    # The page keeps itself current: every ten seconds while something is
    # being built, every thirty otherwise - so a build that starts after
    # the page was opened still shows up without a hand on F5 (the
    # operator, 2026-09-05). With the script this is a quiet swap that
    # waits for a quiet moment; without it, the browser's own reload.
    busy = bool(building) or int(
        (data.get("queue") or {}).get("queued") or 0) > 0 or any(
            str(w.get("status") or "") in ("queued", "running")
            for w in (data.get("wishes") or []))
    return page("Instance manager", body, user=quiet, here="/",
                refresh=10 if busy else 15)


def live_page(serial: str, user: dict, said: str = "",
              creds: dict | None = None,
              row: dict | None = None) -> str:
    """The tab Boot opens.

    The live-view URL is the answer to a call only the pass makes, so
    this tab waits on the request it queued and goes to the screen the
    moment it lands. A refusal or a failure is said here in words - a
    tab that opens and stays blank is worse than no tab.
    """
    row = row or {}
    status = str(row.get("status") or "")
    result = str(row.get("result") or "")
    detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
    url = str(detail.get("url") or "")
    if status == "done" and url:
        return viewer_page(serial, user, url, creds=creds)
    # The Live tab's Change IP waits on a change_proxy request the same
    # way Boot's tab waits on its boot - and reads these titles to know
    # whether to keep waiting, swap screens, or offer Boot (2026-09-16).
    changing = str(row.get("verb") or "") == "change_proxy"
    wait = 0
    if said == "refused":
        title, note, colour = ("Not allowed",
                               "You may not do this to a phone - ask an "
                               "admin.", "red")
    elif said == "off":
        title, note, colour = ("Actions are not switched on yet",
                               "Nothing was queued.", "amber")
    elif status in ("failed", "refused", "cancelled") and changing:
        title, note, colour = (f"{serial} is off" if detail.get("off")
                               else f"{serial} kept its exit",
                               result or "the request did not go through",
                               "red")
    elif status in ("failed", "refused", "cancelled"):
        title, note, colour = (f"{serial} did not start",
                               result or "the request did not go through",
                               "red")
    elif status == "done":
        title, note, colour = (f"{serial} started",
                               result or "GeeLark gave no live-view link "
                               "back for it", "green")
    elif changing:
        title, note, colour, wait = (
            f"Changing the IP of {serial}",
            "The phone is stopped, moved to the next free exit and started "
            "again - usually under a minute. This tab goes back to the "
            "screen by itself; keep it open.",
            "amber", 3)
    else:
        title, note, colour, wait = (
            f"Starting {serial}",
            "GeeLark is starting it - usually ten to twenty seconds. This "
            "tab goes to the screen by itself; keep it open.",
            "amber", 3)
    # Its own refresh. The browser's went inside <noscript> for the
    # dashboard's sake, and this page has no script - so it never asked
    # again, and Boot looked wired to nothing while the link sat in the
    # request's row (the operator, 2026-09-08).
    again = (f'<script>setTimeout(function(){{ location.reload(); }}, '
             f'{int(wait) * 1000});</script>' if wait else "")
    body = (f'<div class="card" style="width:min(520px,100%);'
            f'text-align:center">'
            f'<div class="brand" style="justify-content:center">'
            f'{_BRAND_ICON}geelark farm</div>'
            f'<h2 style="color:var(--{colour})">{esc(title)}</h2>'
            f'<p class="muted">{esc(note)}</p>'
            f'<a class="btn quiet" href="/">Back to the dashboard</a></div>'
            f'{again}')
    # No listening and no swap: this tab reloads itself whole, above.
    return page(f"Boot {serial}", body, user=user, here="/", refresh=wait,
                live="")


#: How often the Live tab tells the farm it is open, in milliseconds. A
#: hidden tab's clock runs once a minute in Chrome whatever this says;
#: the keeper's grace (LIVE_TAB_GRACE_SECONDS, three minutes) allows for
#: that, and a real close is said by the pagehide beacon, not by silence.
LIVE_BEAT_MS = 15000
#: The width the viewer is asked for, and the box it draws itself in at
#: that width (measured: w + 56 wide, 2w + 32 tall). The box is then
#: scaled to the window, so these only fix the drawing's resolution.
VIEWER_WIDTH = 360
VIEWER_BOX = (VIEWER_WIDTH + 56, 2 * VIEWER_WIDTH + 32)


def viewer_page(serial: str, user: dict, url: str,
                creds: dict | None = None) -> str:
    """The Live tab once the phone is up: GeeLark's viewer inside this
    page, and a beat every fifteen seconds that says the tab is open.

    `creds` is the Gmail signed into the phone - address, password and
    authenticator key - drawn in the margin for whoever holds the phone,
    with the authenticator's current code computed in the page and
    counted down (the operator, 2026-09-16: "write the Gmail's details
    cleanly beside the screen"). None draws no margin.

    It used to send the tab to GeeLark's own page, whose closing nobody
    could see - so a phone booted from the console ran on after its tab
    was gone, until somebody found it under Running (the operator,
    2026-09-16). Framed here, the tab's life is the phone's: when the
    beat stops the keeper switches the phone off and puts it back
    (forgotten.sweep), which is what closing the tab means.

    The viewer draws itself at a size of its own - its `w` parameter,
    plus a title bar and a toolbar - and at GeeLark's default the phone's
    Back and Home ran off the bottom of an ordinary window, while a
    width picked from the window's height left a third of it empty on
    the operator's screen (2026-09-16). So the viewer is asked for one
    fixed width and drawn in a box of its natural size, and the box is
    scaled with CSS to whatever the window is - both ways, on any
    monitor. Measured on three screens: a phone of width w draws
    (w + 56) wide and (2w + 32) tall.
    """
    beat = (
        "(function(){"
        f"var serial={_js(serial)}, csrf={_js(str(user.get('csrf') or ''))},"
        f" base={_js(url)};"
        "var frame=document.getElementById('gf-view');"
        "var box=document.getElementById('gf-box');"
        "var stage=document.getElementById('gf-stage');"
        "var word=document.getElementById('gf-watch');"
        f"var W={VIEWER_WIDTH}, BOX_W={VIEWER_BOX[0]}, BOX_H={VIEWER_BOX[1]};"
        "var u=new URL(base); u.searchParams.set('w',String(W));"
        "function show(){ u=new URL(base); u.searchParams.set('w',String(W));"
        " frame.setAttribute('src',u.href); }"
        "show();"
        "function fit(){"
        " var h=stage.clientHeight||window.innerHeight,"
        "     w=stage.clientWidth||window.innerWidth;"
        " var k=Math.min(h/BOX_H,w/BOX_W);"
        " box.style.width=Math.floor(BOX_W*k)+'px';"
        " box.style.height=Math.floor(BOX_H*k)+'px';"
        " frame.style.transform='scale('+k+')';"
        "}"
        "fit(); window.addEventListener('resize',fit);"
        "document.getElementById('gf-reload').addEventListener('click',"
        " function(){ frame.setAttribute('src','about:blank');"
        "  setTimeout(show,300); });"
        "var form='csrf='+encodeURIComponent(csrf);"
        "var gone=false;"
        + _CHANGE_IP_SCRIPT +
        "function beat(){"
        " if(gone) return;"
        " fetch('/phones/'+serial+'/watching',{method:'POST',"
        "  credentials:'same-origin',keepalive:true,"
        "  headers:{'Content-Type':'application/x-www-form-urlencoded'},"
        "  body:form})"
        " .then(function(r){"
        "  if(r.status===410){gone=true; released();}"
        "  else if(r.ok){word.textContent='watching - close this tab to "
        "switch the phone off';}"
        " }).catch(function(){});"
        "}"
        "function released(){"
        " word.textContent='released - this phone is no longer yours';"
        " document.getElementById('gf-stage').innerHTML="
        "  '<div class=\"gf-gone\"><h2>This phone was put back</h2>"
        "<p>It is no longer yours - switched off after its tab closed, "
        "or released from the dashboard. Close this tab; to use it again, "
        "boot it from the dashboard.</p>"
        "<a class=\"btn\" href=\"/\">Dashboard</a></div>';"
        "}"
        "function closing(){"
        " if(gone) return;"
        " navigator.sendBeacon('/phones/'+serial+'/closing',"
        "  new Blob([form],{type:'application/x-www-form-urlencoded'}));"
        "}"
        f"beat(); setInterval(beat,{LIVE_BEAT_MS});"
        "window.addEventListener('pagehide',closing);"
        "document.addEventListener('visibilitychange',function(){"
        " if(document.visibilityState==='visible') beat();});"
        "})();"
    )
    rows = _gmail_margin(creds) if creds else ""
    ends = ""
    if _may(user, "may_take_phones"):
        # Done and Failed, as the dashboard's row offers them (the
        # operator, 2026-09-16: "beside the other buttons"). Both delete
        # the phone, so both ask first - here, in the page, by the same
        # data-ask the dashboard's script reads; answered yes, the form
        # carries the server's own `sure` and the tab goes home, since
        # the phone it was showing is gone.
        ends = (f'<div class="gf-acts">'
                f'{_state_form(user, serial, "done", "/")}'
                f'{_state_form(user, serial, "failed", "/")}</div>')
    change_ip = ""
    if _may(user, "may_change_proxy"):
        # Beside Reload: the phone is stopped, moved to the next free
        # exit and started again, and the screen swaps in place - the
        # tab never leaves this page, so its beat never stops and the
        # phone stays theirs (the operator, 2026-09-16). Boot is kept
        # hidden for the one outcome where the phone is off afterwards.
        change_ip = (
            f'<button type="button" class="quiet" id="gf-ip" title="Stops '
            f'the phone, moves it to the next free exit and starts it again '
            f'- about a minute, and the screen comes back here by itself">'
            f'Change IP</button>'
            f'<form method="post" class="inline" id="gf-boot" hidden '
            f'action="/phones/{esc(serial)}/boot">{_csrf(user)}'
            f'<button class="quiet">Boot again</button></form>')
    body = (
        '<style>html,body{overflow:hidden}'
        # The whole window, side by side: the stage takes every pixel of
        # height it can, the margin carries everything else - what used
        # to be a bar across the top (the operator, 2026-09-16: "move the
        # bar into the margin so the phone can be full size").
        '#gf-wrap{position:fixed;inset:0;display:flex;background:var(--bg)}'
        '#gf-stage{flex:1;min-width:0;display:flex;justify-content:center;'
        'align-items:flex-start;overflow:hidden}'
        '#gf-box{position:relative;overflow:hidden}'
        f'#gf-view{{border:0;width:{VIEWER_BOX[0]}px;height:{VIEWER_BOX[1]}px;'
        'background:#000;display:block;transform-origin:0 0}'
        '.gf-gone{max-width:420px;margin:80px auto;text-align:center}'
        '#gf-side{flex:none;width:300px;padding:16px;overflow:auto;'
        'border-left:1px solid var(--line);font-size:13px;display:flex;'
        'flex-direction:column;gap:2px}'
        '#gf-side h3{margin:18px 0 12px;font-size:12px;letter-spacing:.06em;'
        'text-transform:uppercase;color:var(--muted)}'
        '.gf-head{display:flex;align-items:center;gap:10px;margin-bottom:8px}'
        '.gf-head b{font-size:18px;color:var(--ink)}'
        '.gf-head .dim{margin-left:auto}'
        '#gf-watch{display:block;color:var(--muted);margin-bottom:10px;'
        'line-height:1.4}'
        '#gf-reload{align-self:flex-start;margin-bottom:6px}'
        '#gf-ip,#gf-boot{align-self:flex-start;margin-bottom:6px}'
        '.gf-acts{display:flex;gap:8px;margin:4px 0 6px}'
        '.gf-row{margin:0 0 14px}'
        '.gf-row .lbl{display:block;color:var(--muted);font-size:11px;'
        'margin-bottom:3px}'
        '.gf-row .val{display:flex;align-items:center;gap:8px}'
        '.gf-row .val code{flex:1;min-width:0;overflow-wrap:anywhere;'
        'font-size:13px}'
        '.gf-row button{flex:none;font-size:11px;padding:2px 8px}'
        '.gf-code{font-size:28px;letter-spacing:.14em;font-weight:600;'
        'font-variant-numeric:tabular-nums}'
        '.gf-bar{height:3px;background:var(--line);border-radius:2px;'
        'margin-top:6px;overflow:hidden}'
        '.gf-bar i{display:block;height:100%;background:var(--ok,#3c9)}'
        '@media (max-width:820px){#gf-wrap{flex-direction:column}'
        '#gf-side{width:auto;border-left:0;border-top:1px solid var(--line)}}'
        '</style>'
        f'<div id="gf-wrap"><div id="gf-stage"><div id="gf-box">'
        f'<iframe id="gf-view" data-src="{esc(url)}" '
        f'allow="clipboard-read; clipboard-write; fullscreen"></iframe>'
        f'</div></div>'
        f'<aside id="gf-side"><div class="gf-head"><b>{esc(serial)}</b>'
        f'<a class="dim" href="/">Dashboard</a></div>'
        f'<span id="gf-watch">connecting</span>'
        f'<button type="button" class="quiet" id="gf-reload" title="Loads '
        f'GeeLark&#39;s viewer again without closing the tab - for when it says '
        f'the connection timed out; that is the route from your network to '
        f'phone.geelark.com, not the phone">Reload viewer</button>'
        f'{change_ip}{ends}{rows}</aside></div>'
        f'<script>{beat}{_ASK_SCRIPT if ends else ""}</script>')
    return page(f"Phone {serial}", body, user=user, here="/", live="",
                bare=True)


#: The Live tab's Done and Failed: the question the form carries, asked
#: by the browser's own dialog, and `sure` added so the server does not
#: ask again on a page of its own.
_ASK_SCRIPT = (
    "document.querySelectorAll('form[data-ask]').forEach(function(f){"
    " f.onsubmit=function(){"
    "  if(!window.confirm(f.getAttribute('data-ask'))) return false;"
    "  var s=document.createElement('input'); s.type='hidden';"
    "  s.name='sure'; s.value='1'; f.appendChild(s); return true;"
    " };"
    "});"
)

#: The Live tab's Change IP, inside the beat's closure (it uses `serial`,
#: `form`, `frame`, `word`, `base` and `show`). The press is the same POST
#: the dashboard's button sends, plus `boot=1` and this page as `back`;
#: the answer is the page Boot's tab waits on, read here instead of
#: shown: while its title says "Changing", ask again in three seconds;
#: a viewer in it is the new screen; "is off" means Boot is the way on.
_CHANGE_IP_SCRIPT = (
    "var ipb=document.getElementById('gf-ip'),"
    " bootf=document.getElementById('gf-boot');"
    "if(ipb){"
    " var here='/phones/'+serial+'/live', asks=0;"
    " function landed(doc,at){"
    "  var view=doc.getElementById('gf-view');"
    "  var h=doc.querySelector('h2'), p=doc.querySelector('p.muted');"
    "  var title=h?h.textContent:'', note=p?p.textContent:'';"
    "  if(view&&view.getAttribute('data-src')){"
    "   base=view.getAttribute('data-src'); show();"
    "   word.textContent='watching - on a new IP now; close this tab to "
    "switch the phone off'; ipb.disabled=false; return; }"
    "  if(title.indexOf('Changing')===0&&asks<40){"
    "   asks++; setTimeout(function(){ ask(at); },3000); return; }"
    "  ipb.disabled=false;"
    "  if(title.indexOf(' is off')>0){"
    "   word.textContent=title+' - '+note; if(bootf) bootf.hidden=false;"
    "   return; }"
    "  show();"
    "  word.textContent=(title?title+' - ':'')+(note||'the IP was not "
    "changed');"
    " }"
    " function ask(at){"
    "  fetch(at,{credentials:'same-origin'})"
    "  .then(function(r){ return r.text(); })"
    "  .then(function(t){ landed(new DOMParser().parseFromString(t,"
    "'text/html'),at); })"
    "  .catch(function(){ show(); ipb.disabled=false;"
    "   word.textContent='could not reach the farm - press Change IP "
    "again'; });"
    " }"
    " ipb.addEventListener('click',function(){"
    "  if(gone||ipb.disabled) return;"
    "  ipb.disabled=true; asks=0;"
    "  word.textContent='changing IP - the phone stops, moves to the next "
    "free exit and starts again; about a minute';"
    "  frame.setAttribute('src','about:blank');"
    "  fetch('/phones/'+serial+'/proxy',{method:'POST',"
    "   credentials:'same-origin',"
    "   headers:{'Content-Type':'application/x-www-form-urlencoded'},"
    "   body:form+'&boot=1&back='+encodeURIComponent(here)})"
    "  .then(function(r){"
    "   var at=new URL(r.url,location.href);"
    "   if(at.pathname!==here){ show(); ipb.disabled=false;"
    "    word.textContent='not allowed - the phone is not yours, or you "
    "may not change IPs'; return null; }"
    "   return r.text().then(function(t){ landed(new DOMParser()"
    ".parseFromString(t,'text/html'),at.href); });"
    "  })"
    "  .catch(function(){ show(); ipb.disabled=false;"
    "   word.textContent='could not reach the farm - press Change IP "
    "again'; });"
    " });"
    "}"
)


def _js(value: str) -> str:
    """A string as a JavaScript literal, safe inside a <script>."""
    import json

    return json.dumps(str(value)).replace("</", "<\\/")


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
    listed = f'<div id="awaiting">{"".join(items)}</div>'
    head = (f'<div class="row"><h3>Awaiting login</h3>'
            f'<span class="dim mono">{_plural(len(awaiting), "account")}'
            f'</span></div>'
            f'<div class="railfind" hidden><input id="railfind" type="search"'
            f' placeholder="Filter accounts" autocomplete="off"></div>')
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
    #: Stock lives in the store now, so a command
    #: that only touches it runs in the request that
    #: asked for it - there is nothing left for a
    #: pass to do.
    "done": "Done - it is already in.",
    # See _POOL_SAID: a command rings a bell and a lane takes it, so the
    # thirty seconds was the old world's answer (2026-09-14).
    "queued": "Queued - it starts within a second.",
    "cancelled": "Cancelled - it never ran.",
    "too_late": "Too late - a pass had already taken it; see its row below.",
    "not_yours": "That request is not yours to touch.",
    "not_failed": "Only a failed request can be retried.",
    "refused": "You may not do that - ask an admin for the permission.",
    "already": "Already asked - that request is still pending.",
    "twice": "That press already went through the first time; the page "
             "shows what it did.",
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
    if verb == "add_panel_account":
        return f"Add {p.get('ref', '?')}", "from the customer panel"
    if verb == "withdraw_panel_account":
        return f"Withdraw {p.get('ref', '?')}", "the panel took it back"
    if verb == "edit_gmail":
        return f"Edit {p.get('address', '?')}", "in the Gmails tab"
    if verb == "remove_gmail":
        return f"Remove {p.get('address', '?')}", "from the Gmails tab"
    if verb == "boot_phone":
        return f"Boot phone {p.get('serial', '?')}", "start it and take it"
    if verb == "set_phone_state":
        # An admin ending somebody else's hold: the request says whose
        # phone it was, so the Requests page answers "who released my
        # phone" without a search.
        held = str(p.get("held_by") or "")
        return (f"Mark phone {p.get('serial', '?')} "
                f"{p.get('state') or 'unused'}"), (
            f"it was with {held}" if held else "")
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
            or not _keeps_the_console(user)):
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
    # Always, not only while something is pending: the page listens on
    # the stream whatever its rows say, and a request somebody else files
    # arrives on it by itself.
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


_CLIENTS_SAID = {
    "minted": "The key was minted. It is on the page you just left and "
              "nowhere else.",
    "rotated": "The key was replaced. The old one stopped working the "
               "moment it was.",
    "off": "That key is switched off - every request with it is answered "
           "401 until it is switched back on.",
    "on": "That key works again.",
    "webhook": "Saved. Nothing posts there yet: the sender is not built.",
}


def api_clients_page(data: dict, user: dict, said: str = "",
                     error: str = "") -> str:
    """The keys the machines come in with, and what each did today.

    A page rather than a terminal, because the key that matters most -
    the customer panel's - is handed over and revoked by whoever runs the
    farm, and until now that meant ssh and a CLI. Minting shows the token
    once, here, and never again: what is stored is its hash.
    """
    csrf = esc(user.get("csrf", ""))
    rows = data.get("rows") or []
    live = sum(1 for r in rows if r.get("active"))
    cap = int(data.get("cap") or 0)
    per_minute = int(data.get("per_minute") or 0)
    body = (f'<div class="top"><h2>API clients</h2><span class="status">'
            f'{live} active &middot; admin only</span></div>')
    if error:
        body += f'<p class="err">{esc(error)}</p>'
    note = _CLIENTS_SAID.get(said, "")
    if note:
        body += f'<p class="said">{esc(note)}</p>'
    body += (f'<p class="hint">Writing is '
             f'{"on" if data.get("writes") else "off"} for real accounts; '
             f'a sandbox key writes either way. Each key may ask '
             f'{per_minute or "any number of"} times a minute and hand over '
             f'{cap or "any number of"} accounts a day (UTC).</p>')
    head = ("<tr><th>client</th><th>role</th><th>key</th><th>last seen</th>"
            "<th>today</th><th>webhook</th><th></th></tr>")
    lines = []
    for r in rows:
        ident = int(r["id"])
        off = "" if r.get("active") else ' <span class="badge">off</span>'
        klass = "" if r.get("active") else ' class="off"'
        seen = _when(r["last_seen_at"]) if r.get("last_seen_at") else "never"
        # The cap counts a UTC day and the console counts the owner's,
        # and for three and a half hours every night they disagree: the
        # page showed the one the door does not enforce, beside a cap
        # labelled UTC (the review, 2026-09-12). Both are here now, and
        # the warning is on the one that refuses.
        made = int(r.get("accounts_today_utc") or 0)
        here = int(r.get("accounts_today") or 0)
        asked = int(r.get("requests_today") or 0)
        near = " warn" if cap and made >= cap else ""
        hook = ('<span class="badge green">set</span>'
                if r.get("webhook_url") else '<span class="dim">none</span>')
        if r.get("webhook_url") and not r.get("has_secret"):
            hook += ' <span class="badge warn">no secret</span>'
        lines.append(
            f'<tr{klass}><td><b style="font-weight:500;'
            f'color:var(--bright)">{esc(str(r["name"]))}</b>{off}</td>'
            f'<td><span class="badge {"info" if r["role"] == "panel" else ""}">'
            f'{esc(str(r["role"]))}</span></td>'
            f'<td class="muted">{esc(str(r.get("key_prefix") or ""))}&hellip;'
            f'</td><td class="muted">{seen}</td>'
            f'<td class="muted"><span class="badge{near}">{made}</span> '
            f'account{"" if made == 1 else "s"} (UTC), {asked} request'
            f'{"" if asked == 1 else "s"}'
            + (f'<span class="dim"> &middot; {here} today here</span>'
               if here != made else "")
            + f'</td>'
            f'<td>{hook}</td>'
            f'<td class="act">'
            f'<form method="post" class="inline" '
            f'action="/api-clients/{ident}/rotate">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<button class="quiet warn" title="mint a new key; the one it '
            f'has stops working at once">New key</button></form>'
            f'<form method="post" class="inline" '
            f'action="/api-clients/{ident}/active">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<input type="hidden" name="active" '
            f'value="{"0" if r.get("active") else "1"}">'
            f'<button class="quiet">'
            f'{"Switch off" if r.get("active") else "Switch on"}</button>'
            f'</form></td></tr>')
    listing = (f'<div class="panel wrap"><table>{head}{"".join(lines)}'
               f'</table><p class="dim">the token itself is never stored - '
               f'only its hash, and the first eight characters so two keys '
               f'can be told apart</p></div>')
    if not rows:
        listing = ('<div class="panel"><p class="muted">No keys yet. Mint '
                   'one below and hand it over privately.</p></div>')

    hooks = "".join(
        f'<form method="post" action="/api-clients/{int(r["id"])}/webhook" '
        f'class="row" style="gap:8px">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        f'<span class="muted" style="width:110px">{esc(str(r["name"]))}</span>'
        f'<input name="url" placeholder="https://..." '
        f'value="{esc(str(r.get("webhook_url") or ""))}" style="flex:1">'
        f'<input name="secret" type="password" placeholder="'
        f'{"secret set - blank keeps it" if r.get("has_secret") else "signing secret"}"'
        f' autocomplete="off" style="width:190px">'
        f'<button class="quiet">Save</button></form>' for r in rows)
    webhooks = (f'<div class="panel"><h3>Where events will be posted</h3>'
                f'<p class="hint">Kept now, used when the sender is built. '
                f'The secret signs every delivery so a receiver can tell '
                f'ours from anybody else&#x27;s; it is stored, never shown '
                f'back, and an empty box leaves the one already there.</p>'
                f'{hooks}</div>') if rows else ""

    creator = (
        '<div class="panel"><h3>New client</h3>'
        f'<form method="post" action="/api-clients/new" class="field" '
        f'style="gap:12px"><input type="hidden" name="csrf" value="{csrf}">'
        '<div class="row"><input name="name" placeholder="name" '
        'autocomplete="off" style="width:200px">'
        f'<span class="muted">Role</span>'
        f'{_choice("role", ("panel", "bot", "sandbox"), "sandbox")}</div>'
        '<p class="hint">A <b>panel</b> key hands the farm real accounts; a '
        '<b>bot</b> key only answers a code; a <b>sandbox</b> key writes into '
        'a practice room where no phone is ever built. A name cannot be '
        'reused - to replace a key, press New key on its row.</p>'
        '<div class="row"><button>Mint</button><span class="hint">the token '
        'is shown once, on the next page</span></div></form></div>')
    body += listing + f'<div class="grid2">{creator}{webhooks}</div>'
    return page("API clients", body, user=user, here="/api-clients")


def new_key_page(name: str, token: str, user: dict, *, minted: bool) -> str:
    """The token, exactly once. Not in a URL, not in the log, not on any
    later page - what the store keeps is its hash."""
    what = "minted" if minted else "key replaced"
    body = (f'<div class="top"><h2>{esc(name)} &mdash; {esc(what)}</h2></div>'
            f'<div class="panel ok" style="max-width:560px">'
            f'<h3>Its key, shown only now</h3>'
            f'<div class="code">{esc(token)}</div>'
            f'<p class="hint">Hand it over privately. It goes in one place '
            f'only - the Authorization header - and never in a URL. Any key '
            f'this client had before has stopped working. This page cannot '
            f'be opened again.</p>'
            f'<div class="row"><a class="btn quiet" href="/api-clients">Back '
            f'to API clients</a></div></div>')
    return page("A new key", body, user=user, here="/api-clients")


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
    #: Stock lives in the store now, so a command
    #: that only touches it runs in the request that
    #: asked for it - there is nothing left for a
    #: pass to do.
    "done": "Done - it is already in.",
    # It said "the next pass (within ~30s) carries it out", which was
    # true when only a pass could write the sheet. A command rings a bell
    # now and a lane takes it: measured over a day, set_phone_state 0.2s,
    # remove_proxy 0.1s, boot_phone 1.4s, mark_proxy_free 4.6s - and the
    # slowest, Test all, 27s, because it asks GeeLark about every exit.
    # So: it starts at once, and how long it takes is the work's own
    # business (2026-09-14).
    "queued": "Queued - it starts within a second; the page shows the "
              "answer as soon as it is done. Watch Requests.",
    "refused": "You may not do that - ask an admin for the permission.",
    "off": "Actions are not switched on yet.",
    "bad": "That account was refused at the form - check the address, the "
           "password and the secret.",
    "gone": "That exit is no longer in GeeLark's list - nothing to adopt.",
    "already": "Already asked - that request is still pending.",
    "twice": "That press already went through the first time; the page "
             "shows what it did.",
    "auto": "Manual login is off: accounts log in on their own on the next "
            "pass, nothing to press.",
    "none": "Tick at least one account first.",
}


def _may(user: dict, permission: str) -> bool:
    from ..store.users import may

    return bool(user.get("mutations")) and may(user, permission)


def _csrf(user: dict) -> str:
    """The token every form carries, and beside it the press: a stamp
    minted when the form is drawn, so the server can tell "the same
    button, pressed again after the page moved" from "the same drawing
    of it, sent twice". The request key was the wall-clock minute, so a
    second Test inside the same minute was folded into the first - which
    had already run - and answered "Queued" over nothing (2026-09-14).
    """
    import secrets

    return (f'<input type="hidden" name="csrf" '
            f'value="{esc(user.get("csrf", ""))}">'
            f'<input type="hidden" name="press" '
            f'value="{secrets.token_urlsafe(6)}">')


#: Tokens whose banner is an answer of "no", not of "done". Green with a
#: tick was worn by every one of these, so a refusal looked exactly like a
#: success and the person walked away believing it (2026-09-07).
_SAID_NO = frozenset({"no", "refused", "off", "auto", "none", "gone", "bad",
                      "too_late"})


def _links_out(user: dict | None) -> bool:
    """Whether this reader can open the pages the banners point at. An
    operator cannot: `_operator_may_get` sends them back to "/" from every
    one, so the link was a flash and a bounce (2026-09-07). `None` is the
    admin-only call sites, which pass no user at all."""
    return user is None or user.get("role") == "admin"


def _said(said: str, table: dict, user: dict | None = None,
          note: str = "") -> str:
    """The banner for a ?said= token. `queued:241` names the request the
    press became, and the banner links to it. `removed-gmail:241` names
    the remove, and the banner carries Undo - which puts the row back from
    what that request kept.

    `note` is the verb's own sentence, read off the settled row by the
    handler: "the Gmail x@y is not free", "16 gmails added, 1 already in
    the pool, 1 refused". It replaces the table's general word, because
    the general word cannot say which address or why."""
    word, _, req = (said or "").partition(":")
    note = note or table.get(word, "")
    if not note:
        return ""
    if word in _SAID_NO:
        return f'<p class="said no toast">{esc(note)}</p>'
    if word.startswith("removed-") and req.isdigit() and user is not None:
        kind = word[len("removed-"):]
        return (f'<p class="said toast undo">{esc(note)} '
                f'<form method="post" action="/pools/{esc(kind)}/undo" '
                f'class="inline">{_csrf(user)}'
                f'<input type="hidden" name="req" value="{req}">'
                f'<button class="quiet">Undo</button></form></p>')
    # `toast`: the script moves it to the corner and lets it go after a
    # few seconds, and takes `?said=` off the address so a refresh does
    # not say it again. Without the script it is the banner it always was.
    if word in ("queued", "already") and req.isdigit() and _links_out(user):
        return (f'<p class="said toast">{esc(note)} '
                f'<a href="/requests?hi={req}">#{req} on Requests</a></p>')
    return f'<p class="said toast">{esc(note)}</p>'


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


def _gmail_add(user: dict, known: list) -> str:
    """The one way in, at the top of the queued view: a box to paste the
    seller's sheet into, the seller beside it, and one button.

    One way, not two. Typing a single account by hand was a second form
    saying the same thing to the same preview, and a line pasted into the
    box is the same keystrokes without the second form.
    """
    if not _may(user, "may_add_gmail"):
        if not user.get("mutations"):
            return ""
        return ('<p class="hint">Adding gmails needs the add-gmails '
                'permission - ask an admin.</p>')
    return (
        f'<form method="post" action="/pools/gmail/preview" class="addbox">'
        f'{_csrf(user)}'
        f'<textarea name="pasted" placeholder="paste from the seller\'s '
        f'sheet — one account per line; tab or comma between the columns">'
        f'</textarea>'
        f'<div class="row">{_seller_pick(known)}'
        f'<span class="dim">address, password and the secret in any order — '
        f'nothing is added until you have seen the preview</span>'
        f'<span class="right"></span><button>Preview</button></div>'
        f'</form>')


def _on_phone_badge(r: dict) -> str:
    if r.get("status") == "in_use":
        return '<span class="badge in_use">signing in</span>'
    status = r.get("phone_status") or "ready"
    return (f'<span class="badge {_PHONE_CLASS.get(status, "")}">'
            f'{esc(_phone_word(status))}</span>')


#: The four views, in the order the pills read: the count each one shows
#: is the key itself, and the sentence goes under its table.
GMAIL_VIEWS = {
    "queued": {"label": "Queued", "tone": "green",
               "sub": "the keeper claims from the top of this list"},
    "on_phone": {"label": "On a phone", "tone": "blue",
                 "sub": "signed in right now - the phone's row says how it "
                        "is getting on"},
    "used": {"label": "Used",
             "sub": "retired with the phone they were delivered on"},
    "errored": {"label": "Errored", "tone": "red",
                "sub": "two piles: what Google refused for now and comes "
                       "back on its own, and what the seller owes for"},
}


def _view_pills(base: str, views: dict, view: str, counts: dict) -> str:
    """One pill per view with its count inside it; the view you are on is
    not a link. Shared by the pools, so the row of questions reads the
    same way on each of them."""
    out = []
    for name, words in views.items():
        n = int(counts.get(name) or 0)
        tone = words.get("tone") or ""
        paint = f' style="color:var(--{tone})"' if tone and n else ""
        inner = (f'{esc(words["label"])}'
                 f'<span class="n"{paint}>{n}</span>')
        out.append(f'<span>{inner}</span>' if name == view else
                   f'<a href="{base}?view={name}">{inner}</a>')
    return f'<div class="pills">{"".join(out)}</div>'


def _gmail_stock(counts: dict) -> str:
    """The sentence beside the title: how much stock there is and what it
    covers - the one number this page exists to keep above zero."""
    free = int(counts.get("queued") or 0)
    if not free:
        return ('<b class="figure" style="color:var(--red)">0</b> free '
                '<span class="dim">— no phone can be built until rows are '
                'added</span>')
    return (f'<b class="figure" style="color:var(--green)">{free}</b> free '
            f'<span class="dim">— enough for the next {free} '
            f'{"build" if free == 1 else "builds"}</span>')


def _kind_2fa_word(row: dict) -> str:
    """The same four answers as the badge, as coloured words: a table of
    a hundred rows is easier to read when only the odd one out is loud."""
    if row.get("has_totp"):
        return '<span class="dim">authenticator</span>'
    if row.get("has_recovery"):
        return ('<span style="color:var(--amber);font-size:12px">'
                'recovery address</span>')
    if row.get("email_code_only"):
        return ('<span style="color:var(--amber);font-size:12px">'
                'email code</span>')
    return '<span style="color:var(--red);font-size:12px">password only</span>'


def _secret_cell(r: dict) -> str:
    """What the account answers a challenge with, and which kind that is.

    One column, because the sheet keeps one: a key is base32 and an
    address has an @, so the value already says which it is. The word
    under it says the same thing in English, and is the loud half - a
    row with nothing to answer with is the one to notice.
    """
    if r.get("recovery_email"):
        value, word, colour = str(r["recovery_email"]), "recovery", "amber"
    elif r.get("totp_secret"):
        value, word, colour = str(r["totp_secret"]), "authenticator", "green"
    else:
        value, word, colour = "", "no second factor", "red"
    shown = (f'<span class="hand">{_clip(value, 26)}</span>' if value
             else '<span class="dim">—</span>')
    return (f'<div class="secret">{shown}<span style="color:var(--{colour});'
            f'font-size:11.5px">{esc(word)}</span></div>')


def _pass_cell(r: dict) -> str:
    """The password, selectable on its own so it can be copied without
    the rest of the row coming with it."""
    value = str(r.get("password") or "")
    return (f'<span class="hand">{esc(value)}</span>' if value
            else '<span class="dim">—</span>')


def _gmail_actions(user: dict, r: dict, view: str) -> str:
    """Edit and Remove on one row. Edit is a link, because the row it
    opens is this same page with one row drawn as a form - no state to
    keep, and a reload leaves it open where it was."""
    if not _may(user, "may_add_gmail"):
        return ""
    back = f"/pools/gmail?view={view}"
    address = str(r.get("address") or "")
    return (f'<a class="btn quiet go" href="{back}&edit={int(r["id"])}">'
            f'Edit</a> '
            f'<form method="post" action="/pools/gmail/remove" class="inline">'
            f'{_csrf(user)}<input type="hidden" name="address" '
            f'value="{esc(address)}">'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<button class="quiet bad">Remove</button></form>')


def _gmail_edit_row(user: dict, r: dict, view: str, columns: int) -> str:
    """One row, drawn as a form across the whole table: every cell the
    sheet keeps about this account, in the order it reads. The seller is
    a free box with the known names offered - a seller nobody has typed
    yet is a real thing, and a select cannot say one."""
    back = f"/pools/gmail?view={view}"
    address = str(r.get("address") or "")
    secret = str(r.get("recovery_email") or r.get("totp_secret") or "")
    return (f'<tr class="editrow"><td colspan="{columns}">'
            f'<form method="post" action="/pools/gmail/edit">{_csrf(user)}'
            f'<input type="hidden" name="address" value="{esc(address)}">'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<input name="new_address" value="{esc(address)}" size="26" '
            f'autocomplete="off" placeholder="address">'
            f'<input name="password" value="{esc(str(r.get("password") or ""))}"'
            f' size="14" autocomplete="off" placeholder="password">'
            f'<input name="secret" value="{esc(secret)}" size="26" '
            f'autocomplete="off" placeholder="key or recovery address">'
            f'<input name="seller" value="{esc(str(r.get("seller") or ""))}" '
            f'size="9" list="sellers" autocomplete="off" placeholder="seller">'
            f'<input name="purchased" '
            f'value="{esc(str(r.get("purchased_on") or ""))}" size="10" '
            f'autocomplete="off" placeholder="YYYY-MM-DD">'
            f'<button class="quiet ok">Save</button>'
            f'<a class="btn quiet" href="{back}">Cancel</a>'
            f'</form></td></tr>')


def _reason_words(status: str) -> str:
    colour = "red" if status in _BLAME_RED else "amber"
    return (f'<span style="color:var(--{colour});font-size:12.5px">'
            f'{esc(_reason_word(status))}</span>')


def _why(row: dict, advice) -> str:
    """What Google said, in a sentence - the row's own note when the
    verdict table has never heard of the reason."""
    said = advice(str(row.get("status") or "")) if advice else ""
    return esc(said or str(row.get("note") or ""))


def gmail_pool_page(data: dict, user: dict, said: str = "", *,
                    advice=None, editing: int = 0) -> str:
    """One question per view, one table each.

    Queued is the front door: how much stock there is, and the box that
    adds more. The other three are the same page with a different list -
    what is signed in now, what was spent, and what the seller owes back.

    `editing` is the id of the one row drawn as a form instead of cells -
    the console's row editor, which is this same page with one line
    swapped, so a reload leaves it open where it was.
    """
    counts = data.get("counts") or {}
    view = data.get("view") or "queued"
    sub = (GMAIL_VIEWS.get(view) or {}).get("sub", "")
    rows = data.get("rows") or []
    seller = str(data.get("seller") or "")

    right = ""
    owed = int((data.get("counts") or {}).get("owed") or 0)
    if view == "errored" and owed:
        where = f"?seller={_q(seller)}" if seller else ""
        right = (f'<a class="btn" href="/pools/gmail/refund.txt{where}">'
                 f'Copy {owed} to claim back</a>')
    body = (f'<div class="narrow">'
            f'<div class="top"><h2>Gmail Pool</h2>'
            f'<span class="sub" style="margin:0">{_gmail_stock(counts)}</span>'
            f'<span class="status">{right}</span></div>'
            + _said(said, _POOL_SAID))
    if view == "queued":
        body += _gmail_add(user, data.get("known_sellers") or [])
    body += _view_pills("/pools/gmail", GMAIL_VIEWS, view, counts)

    if view == "errored":
        body += _errored_filters(data, seller)
        head = ("<tr><th>address</th><th>reason</th><th>what happened</th>"
                "<th>failed</th><th>where it stands</th></tr>")
        lines = "".join(
            f'<tr><td>{esc(r["address"])}</td>'
            f'<td>{_reason_words(str(r["status"]))}</td>'
            f'<td class="muted">{_why(r, advice)}</td>'
            f'<td class="muted">{_when(r["updated_at"])}</td>'
            f'<td class="act">{_refund_cell(r, user, view, seller)}</td></tr>'
            for r in rows)
        empty = ("nothing has failed for this seller" if seller else
                 "nothing has been refused by Google")
    elif view == "used":
        head = ("<tr><th>address</th><th>phone</th><th>used</th>"
                "<th>seller</th></tr>")
        lines = "".join(
            f'<tr><td>{esc(r["address"])}</td>'
            f'<td>{_serial_link(r.get("serial"))}</td>'
            f'<td class="muted">{_when(r.get("used_at") or r["updated_at"])}'
            f'</td><td class="muted">{esc(r.get("seller") or "")}</td></tr>'
            for r in rows)
        empty = "nothing has been retired yet"
    elif view == "on_phone":
        head = ("<tr><th>address</th><th>phone</th><th>state</th>"
                "<th>since</th></tr>")
        lines = "".join(
            f'<tr><td>{esc(r["address"])}</td>'
            f'<td>{_serial_link(r.get("serial"))}</td>'
            f'<td>{_on_phone_badge(r)}</td>'
            f'<td class="muted">{_when(r["updated_at"])}</td></tr>'
            for r in rows)
        empty = "no address is signed in on a phone right now"
    else:
        acts = "<th></th>" if _may(user, "may_add_gmail") else ""
        head = (f"<tr><th>address</th><th>seller</th><th>secret</th>"
                f"<th>password</th><th>purchased</th>{acts}</tr>")
        columns = 5 + (1 if acts else 0)
        lines = "".join(
            _gmail_edit_row(user, r, view, columns)
            if editing and int(r.get("id") or 0) == editing else
            (f'<tr><td><span class="hand">{esc(r["address"])}</span></td>'
             f'<td class="muted">{esc(r.get("seller") or "")}</td>'
             f'<td>{_secret_cell(r)}</td>'
             f'<td class="muted">{_pass_cell(r)}</td>'
             f'<td class="muted">{esc(r.get("purchased_on") or "")}</td>'
             + (f'<td class="act">{_gmail_actions(user, r, view)}</td>'
                if acts else "") + "</tr>")
            for r in rows)
        empty = ("the pool is empty - paste a seller's sheet above and "
                 "nothing else has to happen")
    table = (f'<table>{head}{lines}</table>' if lines else
             f'<p class="empty">{esc(empty)}</p>')
    if editing:
        table += ('<datalist id="sellers">' + "".join(
            f'<option value="{esc(str(name))}">'
            for name in (data.get("known_sellers") or [])) + "</datalist>")
    base = f"/pools/gmail?view={view}&seller={_q(seller)}"
    pager = (_pager(base, int(data.get("page") or 1),
                    int(data.get("pages") or 1), bool(data.get("more")))
             if int(data.get("pages") or 1) > 1 or data.get("more") else "")
    body += (f'<div class="panel wrap">{table}'
             f'<p class="dim">{esc(sub)}</p>{pager}</div>')

    if view == "errored" and data.get("broken"):
        broken = "".join(
            f'<tr><td>{esc(r.get("address") or "")}</td>'
            f'<td class="muted">{esc(r.get("error") or "")}</td></tr>'
            for r in data["broken"])
        body += (f'<div class="panel bad"><h3>Refused before the pool '
                 f'<span class="n">{len(data["broken"])}</span></h3>'
                 f'<p class="hint">these rows were never stock: the sheet '
                 f'has them, validation would not take them. Fix the cell '
                 f'or ask for them back too.</p>'
                 f'<table>{broken}</table></div>')
    return page("Gmail Pool", body + "</div>", user=user,
                here="/pools/gmail")


#: The three words a refund row can wear, and how each reads on the page.
_REFUND_WORDS = {"to_claim": ("To claim back", "red"),
                 "claimed": ("Paid back", "green"),
                 "refused": ("Seller refused", "manual")}


def _refund_cell(row: dict, user: dict, view: str, seller: str) -> str:
    """Where one errored address stands: coming back on its own, or money.

    The two piles used to read the same, so a captcha - which signs in two
    times in three on its next try - sat in front of a seller beside a
    password that was never right (the operator, 2026-09-12).
    """
    state = str(row.get("refund_state") or "")
    if not state:
        tries = int(row.get("tries") or 0)
        back = row.get("retry_after")
        if back:
            return (f'<span class="dim">back in the queue {_when(back)} '
                    f'&middot; try {tries + 1} of 3</span>')
        return (f'<span class="dim">tried {tries} time'
                f'{"" if tries == 1 else "s"} &middot; needs a person</span>')
    word, tone = _REFUND_WORDS.get(state, (state, "manual"))
    pill = f'<span class="badge {tone}">{esc(word)}</span>'
    if state != "to_claim" or not _may(user, "may_add_gmail"):
        return pill
    back = "/pools/gmail?view=errored" + (f"&seller={_q(seller)}"
                                          if seller else "")
    buttons = "".join(
        f'<form method="post" class="inline" action="/pools/gmail/refund">'
        f'{_csrf(user)}<input type="hidden" name="address" '
        f'value="{esc(str(row.get("address") or ""))}">'
        f'<input type="hidden" name="state" value="{word}">'
        f'<input type="hidden" name="back" value="{esc(back)}">'
        f'<button class="quiet" title="{esc(hint)}">{label}</button></form>'
        for word, label, hint in (
            ("claimed", "Paid", "the seller paid this one back"),
            ("refused", "Not paid", "the seller would not pay it back")))
    return pill + buttons


def _errored_filters(data: dict, seller: str) -> str:
    """Two rows of chips: which seller sold them, and what Google said.
    Both narrow the same list, so the refund button always matches what
    is on screen."""
    counts = data.get("counts") or {}
    chips = [(f'<span>all sellers <span class="n">'
              f'{int(counts.get("errored") or 0)}</span></span>' if not seller
              else f'<a href="/pools/gmail?view=errored">all sellers '
                   f'<span class="n">{int(counts.get("errored") or 0)}</span>'
                   f'</a>')]
    for s in data.get("sellers") or []:
        name = s["seller"] or "(no seller)"
        inner = f'{esc(name)} <span class="n">{s["c"]}</span>'
        chips.append(f'<span>{inner}</span>'
                     if seller and seller.lower() == (s["seller"] or "")
                     else f'<a href="/pools/gmail?view=errored&seller='
                          f'{_q(s["seller"] or "")}">{inner}</a>')
    reasons = "".join(
        f'<span class="dim">{esc(_reason_word(str(r["status"])))} '
        f'<b class="mono" style="color:var('
        f'--{"red" if str(r["status"]) in _BLAME_RED else "amber"})">'
        f'{r["c"]}</b></span>' for r in (data.get("reasons") or []))
    return (f'<div class="row"><div class="chips">{"".join(chips)}</div>'
            f'<div class="row right" style="gap:14px">{reasons}</div></div>')


#: A note longer than this is clipped in the table; the rest rides in the
#: cell's title, so a hover reads it whole.
NOTE_CHARS = 60


def _last_test(tests: dict, name: str) -> str:
    """'42m ago · ok', '2h ago · dead', or 'never' - off the stamp the
    pass kept for this name when it last tested the exit."""
    stamp = (tests or {}).get(name or "")
    if not isinstance(stamp, dict) or not stamp.get("at"):
        return "never"
    ago = _ago(stamp.get("at")) or "?"
    return f"{ago} · {'ok' if stamp.get('ok') else 'dead'}"


def _test_words(tests: dict, name: str) -> str:
    """The same answer, coloured: a table of exits is read for the odd one
    out, and the odd one out is the exit that failed."""
    said = _last_test(tests, name)
    if said == "never":
        return '<span class="dim">never</span>'
    colour = "green" if said.endswith("ok") else "red"
    return (f'<span class="mono" style="color:var(--{colour});'
            f'font-size:12px">{esc(said)}</span>')


def _clip(text, limit: int = NOTE_CHARS) -> str:
    """Escaped, cut at `limit` with the whole text in a title attribute
    when it was longer."""
    text = str(text or "")
    if len(text) <= limit:
        return esc(text)
    return (f'<span title="{esc(text)}">{esc(text[:limit - 1].rstrip())}…'
            f'</span>')


#: What each bucket is called on the page, and the colour it wears. The
#: buckets themselves are read.proxy_bucket's answer, carried on the row.
_BUCKET_WORDS = {"free": ("free", "green"),
                 "on_phone": ("on a phone", "blue"),
                 "needs_new_ip": ("needs a new IP", "amber"),
                 "dead": ("dead", "red"),
                 "other": ("", "dim")}

#: The four views, in the order the pills read: the count each one shows
#: is the key itself, and the sentence goes under its table.
PROXY_VIEWS = {
    "free": {"label": "Free", "tone": "green",
             "sub": "a build takes the top one and gives it back when the "
                    "phone is done"},
    "on_phone": {"label": "On a phone", "tone": "blue",
                 "sub": "held by a build - an exit comes back here on its "
                        "own when the phone is finished or deleted"},
    "needs_hand": {"label": "Needs a hand", "tone": "amber",
                   "sub": "nothing here is thrown away on its own - a dead "
                          "exit is kept until you say otherwise"},
    "all": {"label": "All",
            "sub": "every exit the pool has ever been told about, newest "
                   "sheet row first"},
}


def _proxy_word(r: dict) -> str:
    """The state, in words and in colour - the row's own status when it is
    a word the pool never wrote."""
    bucket = str(r.get("bucket") or "other")
    word, colour = _BUCKET_WORDS.get(bucket, ("", "dim"))
    word = word or str(r.get("status") or "")
    if colour == "dim":
        return f'<span class="dim" style="white-space:nowrap">{esc(word)}</span>'
    return (f'<span style="color:var(--{colour});font-size:12.5px;'
            f'white-space:nowrap">{esc(word)}</span>')


def _proxy_add(user: dict) -> str:
    """The one way in, at the top of the free view: the vendor's list
    pasted, and the same thing typed field by field - folded into a
    `details` so it costs a line, not a second panel. Both go through the
    same preview, so both are judged by the same reader."""
    if not _keeps_the_console(user):
        return ""
    one = (
        f'<details class="fold"><summary>add one by hand</summary>'
        f'<form method="post" action="/pools/proxy/preview" class="row" '
        f'style="margin-top:10px">{_csrf(user)}'
        f'<input name="host" placeholder="host" autocomplete="off" '
        f'style="flex:1;min-width:160px">'
        f'<input name="port" placeholder="port" autocomplete="off" size="6">'
        f'<input name="username" placeholder="user" autocomplete="off">'
        f'<input name="password" placeholder="pass" autocomplete="off">'
        f'<input name="name" placeholder="name (optional)" '
        f'autocomplete="off" size="12">'
        f'<button class="quiet">Preview</button></form></details>')
    return (
        f'<form method="post" action="/pools/proxy/preview" class="field">'
        f'{_csrf(user)}'
        f'<textarea name="pasted" placeholder="paste from the vendor — '
        f'host:port:user:pass, one per line"></textarea>'
        f'<div class="row"><span class="dim">names are handed out in order '
        f'(SX43, SX44 …) unless a name column is pasted, and each one is '
        f'tested before it joins the pool</span>'
        f'<span class="right"></span><button>Preview</button></div>'
        f'</form>{one}')


def _back_field(back: str) -> str:
    """Which view the button was pressed on, so the banner comes back to
    it. The handler only honours a value it already knows."""
    return (f'<input type="hidden" name="back" value="{esc(back)}">'
            if back else "")


def _proxy_button(user: dict, action: str, name: str, label: str,
                  klass: str = "", back: str = "") -> str:
    """One quiet button posting a proxy's name, or nothing when this
    person may not press it."""
    if not _keeps_the_console(user):
        return ""
    return (f'<form method="post" action="{esc(action)}" class="inline">'
            f'{_csrf(user)}<input type="hidden" name="name" '
            f'value="{esc(name or "")}">{_back_field(back)}'
            f'<button class="quiet {klass}">{esc(label)}</button></form>')


def _stray_who(u: dict) -> str:
    return (f"{str(u.get('host', ''))}:{str(u.get('port', ''))} "
            f"({str(u.get('username', ''))})")


def _stray_buttons(user: dict, u: dict, back: str = "") -> str:
    """Add to pool / Ignore, both carrying the exit's three fields - the
    stray has no name to post, because the pool has never named it."""
    if not _keeps_the_console(user):
        return ""
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{esc(str(u.get(k, "")))}">'
        for k in ("host", "port", "username"))
    hidden += _back_field(back)
    return (f'<form method="post" action="/pools/proxy/adopt" class="inline">'
            f'{_csrf(user)}{hidden}<button class="quiet">Add to pool'
            f'</button></form> '
            f'<form method="post" action="/pools/proxy/ignore" '
            f'class="inline">{_csrf(user)}{hidden}<button class="quiet">'
            f'Ignore</button></form>')


def _trouble_row(user: dict, r: dict, tests: dict, back: str) -> str:
    """One line of the work list: what kind of trouble, where, what
    happened with the one sentence that answers it, and the button that
    is that answer."""
    bucket = str(r.get("bucket") or "")
    name = str(r.get("name") or "")
    where = f"{str(r.get('host') or '')}:{str(r.get('port') or '')}"
    if bucket == "dead":
        said = _last_test(tests, name)
        seen = ("never tested" if said == "never"
                else f"failed its last test {said}")
        seen += f", dead since {_when(r.get('updated_at'))}"
        advice = "revive it at the vendor and test again, or remove it"
        buttons = (_proxy_button(user, "/pools/proxy/test", name,
                                 "Test again", back=back)
                   + " " + _proxy_button(user, "/pools/proxy/remove", name,
                                         "Remove", "bad", back=back))
    else:
        seen = (str(r.get("note") or "") or
                "a build asked for a new IP on this exit")
        advice = ("change the IP in the vendor's panel, then free it here - "
                  "it is re-tested before any build takes it")
        buttons = _proxy_button(user, "/pools/proxy/free", name,
                                "IP changed — free it", "warn", back=back)
    phone = (f' <span class="dim">on</span> {_serial_link(r.get("serial"))}'
             if r.get("serial") else "")
    return (f'<tr><td>{esc(name)}<br>{_proxy_word(r)}</td>'
            f'<td class="muted">{esc(where)}{phone}</td>'
            f'<td>{_clip(seen, 90)}<br><span class="dim">{esc(advice)}</span>'
            f'</td><td class="right">{buttons}</td></tr>')


def _stray_row(user: dict, u: dict, back: str) -> str:
    return (f'<tr><td><span class="dim">not in the pool</span></td>'
            f'<td class="mono muted">{esc(_stray_who(u))}</td>'
            f'<td>GeeLark holds this exit and the pool has never heard of '
            f'it<br><span class="dim">yours to decide: add it, or ignore it '
            f'so it stops being reported</span></td>'
            f'<td class="right">{_stray_buttons(user, u, back)}</td></tr>')


def _proxy_sentence(view: str, counts: dict, data: dict, tested: str) -> str:
    """The line beside the title: the one number this view is about. On
    the free view it is the question the page exists for - are there
    working exits for the builds that come next."""
    if view == "on_phone":
        n = int(counts.get("on_phone") or 0)
        return (f'<span class="mono">{n}</span> on a phone right now'
                if n else "no exit is on a phone right now")
    if view == "needs_hand":
        n = int(counts.get("needs_hand") or 0)
        if not n:
            return "nothing needs a hand"
        exits = int(counts.get("needs_new_ip") or 0) + int(
            counts.get("dead") or 0)
        strays = int(counts.get("strays") or 0)
        parts = []
        if exits:
            parts.append(_plural(exits, "exit"))
        if strays:
            parts.append(_plural(strays, "stray"))
        return (f'<span class="mono" style="color:var(--amber)">{n}</span> '
                f'need a hand — {" and ".join(parts)}')
    if view == "all":
        whole = int(counts.get("all") or 0)
        q = str(data.get("q") or "")
        if q:
            return (f'{int(data.get("total") or 0)} of {whole} rows match '
                    f'"{esc(q)}"')
        return (f'<span class="mono">{whole}</span> rows — every exit the '
                f'pool has ever been told about')
    n = int(counts.get("free") or 0)
    if not n:
        return ('<span style="color:var(--red)">0 free</span> — no build can '
                'take an exit until one comes back or is added')
    return (f'<span class="mono" style="color:var(--green)">{n}</span> free — '
            f'{esc(tested)}')


def proxy_pool_page(data: dict, user: dict, said: str = "", *,
                    q: str = "", show_ignored: bool = False) -> str:
    """One question per view, one table each.

    Free is the front door: how many exits a build can take, when they
    were last tested, and the box that adds more. Needs a hand is the
    one that used to be three panels - an exit wanting a new IP, a dead
    one, and an exit GeeLark holds that the pool never heard of are all
    the same thing to a person: a job, with one button that answers it.

    `tests` and `ignored` come from what the pass keeps in service_state,
    merged into `data` by the caller.
    """
    counts = data.get("counts") or {}
    view = data.get("view") or "free"
    if view not in PROXY_VIEWS:
        view = "free"
    rows = data.get("rows") or []
    tests = data.get("tests") or {}
    ignored = list(data.get("ignored") or [])
    free_names = [r.get("name") for r in rows if r.get("bucket") == "free"]
    newest = max((float(tests[n]["at"]) for n in free_names
                  if isinstance(tests.get(n), dict) and tests[n].get("at")),
                 default=None)
    tested = (f"every one tested {_ago(newest)}" if newest
              else "not tested yet")
    here = ("/pools/proxy" if view == "free"
            else f"/pools/proxy?view={view}")

    right = ""
    if view == "free" and _keeps_the_console(user):
        right = (f'<form method="post" action="/pools/proxy/test-all" '
                 f'class="inline">{_csrf(user)}<button class="quiet">'
                 f'Test all now</button></form>')
    elif view == "all":
        right = (f'<form method="get" action="/pools/proxy" class="inline">'
                 f'<input type="hidden" name="view" value="all">'
                 f'<input name="q" value="{esc(q)}" placeholder="name, host '
                 f'or phone" size="20"></form>')
    body = (f'<div class="narrow">'
            f'<div class="top"><h2>Proxy Pool</h2>'
            f'<span class="sub" style="margin:0">'
            f'{_proxy_sentence(view, counts, data, tested)}</span>'
            f'<span class="status">{right}</span></div>'
            + _said(said, _POOL_SAID))
    if view == "free":
        body += _proxy_add(user)
    body += _view_pills("/pools/proxy", PROXY_VIEWS, view, counts)

    if view == "needs_hand" and show_ignored:
        lines = "".join(f'<tr><td class="mono">{esc(str(who))}</td></tr>'
                        for who in ignored)
        table = (f'<table>{lines}</table>' if lines else
                 '<p class="empty">nothing is ignored</p>')
        return page("Proxy Pool", body + (
            f'<div class="panel wrap"><h3>Ignored <span class="n">'
            f'{len(ignored)}</span></h3>{table}<p class="dim">held by '
            f'GeeLark and left there unreported (host:port:user); the list '
            f'lives in service_state under ignored_proxies. '
            f'<a href="/pools/proxy?view=needs_hand">Back to the work list'
            f'</a></p></div></div>'), user=user, here="/pools/proxy")

    if view == "needs_hand":
        head = ("<tr><th>what</th><th>host</th><th>what happened</th>"
                "<th></th></tr>")
        lines = ("".join(_trouble_row(user, r, tests, here) for r in rows)
                 + "".join(_stray_row(user, u, here)
                           for u in (data.get("strays") or [])))
        empty = "every exit is either free or on a phone - nothing to decide"
    elif view == "on_phone":
        head = ("<tr><th>name</th><th>host</th><th>phone</th><th>state</th>"
                "<th>since</th></tr>")
        lines = "".join(
            f'<tr><td>{esc(str(r.get("name") or ""))}</td>'
            f'<td class="muted">{esc(str(r.get("host") or ""))}:'
            f'{esc(str(r.get("port") or ""))}</td>'
            f'<td>{_serial_link(r.get("serial"))}</td>'
            f'<td>{_proxy_word(r)}</td>'
            f'<td class="muted">{_when(r.get("updated_at"))}</td></tr>'
            for r in rows)
        empty = "no exit is on a phone right now"
    elif view == "all":
        head = ("<tr><th>name</th><th>host</th><th>state</th><th>phone</th>"
                "<th>exit ip</th><th>last test</th></tr>")
        lines = "".join(
            f'<tr><td>{esc(str(r.get("name") or ""))}</td>'
            f'<td class="muted">{esc(str(r.get("host") or ""))}:'
            f'{esc(str(r.get("port") or ""))}</td>'
            f'<td>{_proxy_word(r)}</td>'
            f'<td>{_serial_link(r.get("serial"))}</td>'
            f'<td class="muted mono">{esc(str(r.get("last_exit_ip") or ""))}'
            f'</td><td>{_test_words(tests, str(r.get("name") or ""))}</td>'
            f'</tr>' for r in rows)
        empty = (f'nothing matches "{q}"' if q else "the pool is empty")
    else:
        head = ("<tr><th>name</th><th>host</th><th>exit ip</th><th>uses</th>"
                "<th>last test</th><th></th></tr>")
        lines = "".join(
            f'<tr><td>{esc(str(r.get("name") or ""))}</td>'
            f'<td class="muted">{esc(str(r.get("host") or ""))}:'
            f'{esc(str(r.get("port") or ""))}</td>'
            f'<td class="muted mono">{esc(str(r.get("last_exit_ip") or ""))}'
            f'</td><td class="muted num">{esc(str(r.get("times_used") or 0))}'
            f'</td><td>{_test_words(tests, str(r.get("name") or ""))}</td>'
            f'<td class="right">'
            + _proxy_button(user, "/pools/proxy/test",
                            str(r.get("name") or ""), "Test", back=here)
            + " "
            + _proxy_button(user, "/pools/proxy/remove",
                            str(r.get("name") or ""), "Remove", "bad",
                            back=here)
            + "</td></tr>" for r in rows)
        empty = ("no exit is free - every one is on a phone, or waiting for "
                 "you under Needs a hand")

    table = (f'<table>{head}{lines}</table>' if lines else
             f'<p class="empty">{esc(empty)}</p>')
    base = f"/pools/proxy?view={view}&q={_q(q)}"
    pager = (_pager(base, int(data.get("page") or 1),
                    int(data.get("pages") or 1), bool(data.get("more")))
             if int(data.get("pages") or 1) > 1 or data.get("more") else "")
    foot = f'<p class="dim">{esc(PROXY_VIEWS[view]["sub"])}</p>'
    if view == "needs_hand" and ignored:
        foot = (f'<p class="dim">{esc(PROXY_VIEWS[view]["sub"])} · '
                f'<a href="/pools/proxy?view=needs_hand&ignored=1">'
                f'{_plural(len(ignored), "ignored exit")}</a></p>')
    body += f'<div class="panel wrap">{table}{foot}{pager}</div>'
    return page("Proxy Pool", body + "</div>", user=user, here="/pools/proxy")


def _source_badge(r: dict) -> str:
    """Where an account came from: the customer panel pushed it in, or a
    person did. The name rides with the hand-added ones, so a question
    about a row has somebody to ask."""
    source = str(r.get("source") or "manual")
    if source == "panel":
        return '<span class="badge panel">panel</span>'
    who = r.get("added_by_name")
    return (f'<span class="badge manual">manual'
            f'{(" · " + esc(str(who))) if who else ""}</span>')


def _gpt_add(user: dict, form: dict | None, error: str) -> str:
    """The one way in, at the top of the waiting view: a box to paste the
    accounts you bought yourself into - the panel pushes its own straight
    to the pool - and the same thing spelled out, folded into a `details`
    so it costs a line, not a second panel. A by-hand account is judged
    on the spot; refused, it comes back in that fold, open, with the
    reason under it."""
    if not _may(user, "may_add_gpt"):
        return _need(user, "may_add_gpt", "adding accounts")
    form = form or {}
    said = f'<p class="err">{esc(error)}</p>' if error else ""
    one = (
        f'<details class="fold"{" open" if error or form else ""}>'
        f'<summary>add one by hand</summary>'
        f'<form method="post" action="/pools/gpt/add" class="row" '
        f'style="margin-top:10px">{_csrf(user)}'
        f'<input name="address" placeholder="email address" '
        f'autocomplete="off" value="{esc(str(form.get("address") or ""))}" '
        f'style="flex:1;min-width:220px">'
        f'<input name="password" placeholder="password" autocomplete="off" '
        f'value="{esc(str(form.get("password") or ""))}">'
        f'<input name="secret" placeholder="2FA secret (optional)" '
        f'autocomplete="off" value="{esc(str(form.get("secret") or ""))}" '
        f'style="flex:1;min-width:200px">'
        f'<label class="dim"><input type="checkbox" name="email_code" '
        f'value="1"{" checked" if form.get("email_code_only") else ""}> '
        f'email-code only</label>'
        f'<button class="quiet">Add</button>{said}</form></details>')
    return (
        f'<form method="post" action="/pools/gpt/preview" class="field">'
        f'{_csrf(user)}'
        f'<textarea name="pasted" placeholder="paste the accounts — one per '
        f'line: address, password, and the 2FA secret if it has one">'
        f'</textarea>'
        f'<div class="row"><span class="dim">the panel puts its own accounts '
        f'in by itself — this box is for the ones you buy — and nothing is '
        f'added until you have seen the preview</span>'
        f'<span class="right"></span><button>Preview</button></div>'
        f'</form>{one}')


#: The four views, in the order the pills read: the count each one shows
#: is the key itself, and the sentence goes under its table.
GPT_VIEWS = {
    "waiting": {"label": "Waiting", "tone": "green",
                "sub": "the keeper takes the top one first, wherever it "
                       "came from"},
    "on_phone": {"label": "On a phone", "tone": "blue",
                 "sub": "signing in now, or signed in and waiting to be "
                        "delivered"},
    "needs_human": {"label": "Needs a human", "tone": "red",
                    "sub": "offering one again blanks its status and puts it "
                           "back with the others waiting"},
    "delivered": {"label": "Delivered",
                  "sub": "the search matches the address, the phone or the "
                         "note; the export is everything that matches, not "
                         "this page"},
}


def _last_sentence(text) -> str:
    """The sentence a verdict ends on. Every one of them is written as an
    instruction - "Fix the payment ... then blank this status to offer it
    again" - and the paragraph in front of it is the reasoning."""
    parts = [p.strip() for p in str(text or "").split(". ") if p.strip()]
    return parts[-1] if parts else ""


def _gpt_happened(row: dict, explain) -> str:
    """What the run saw, and under it the one sentence saying what to do
    about it. The reasoning between the two rides in the title: a table
    is not where a person reads a paragraph. A status the verdict table
    has never heard of keeps the row\'s own note."""
    seen, advice = (explain(str(row.get("status") or "")) if explain
                    else ("", ""))
    if not seen:
        return esc(str(row.get("note") or ""))
    todo = _last_sentence(advice)
    return (f'<span title="{esc(str(advice))}">{esc(seen)}</span>'
            + (f'<br><span class="dim">{_clip(todo, 120)}</span>'
               if todo else ""))


def _gpt_sentence(view: str, counts: dict, data: dict, warm) -> str:
    """The line beside the title: the one number this view is about, and
    what it means right now. On the waiting view that is the question the
    whole page exists for - whether there is a phone for what is queued."""
    if view == "delivered":
        whole = int(counts.get("delivered") or 0)
        total = int(data["total"]) if data.get("total") is not None else whole
        q = str(data.get("q") or "")
        if q:
            return f'{total} of {whole} delivered accounts match "{esc(q)}"'
        return (f'<span class="mono">{whole}</span> delivered — the panel '
                f'pulls each one\'s fate from here')
    if view == "on_phone":
        n = int(counts.get("on_phone") or 0)
        if not n:
            return "no account is on a phone right now"
        return (f'<span class="mono">{n}</span> on a phone — signing in, or '
                f'signed in and waiting to go out')
    if view == "needs_human":
        n = int(counts.get("needs_human") or 0)
        if not n:
            return "nothing has been set aside"
        return (f'<span class="mono" style="color:var(--amber)">{n}</span> '
                f'set aside — each one waits for a decision')
    n = int(counts.get("waiting") or 0)
    if not n:
        return ("nothing is waiting — the next account the panel sends "
                "lands here")
    if warm is None:
        # No pass has left a pulse, so how many phones are warm is not
        # something this page knows. It says the number it does know.
        return f'<span class="mono">{n}</span> waiting for a phone'
    if warm >= n:
        colour, tail = "green", f"{_plural(warm, 'warm phone')} can take them"
    elif warm:
        colour, tail = "amber", f"only {_plural(warm, 'warm phone')} free"
    else:
        colour, tail = "red", "no warm phone is free for them"
    return (f'<span class="mono" style="color:var(--{colour})">{n}</span> '
            f'waiting — {tail}')


def gpt_pool_page(data: dict, user: dict, said: str = "", *,
                  explain=None, manual_login: bool = False,
                  form: dict | None = None, error: str = "") -> str:
    """One question per view, one table each.

    Waiting is the front door: how many accounts have no phone yet,
    whether a warm phone can take them, and the box that adds more.
    Panel and hand-added accounts share that one list with a column
    saying where each came from, because the keeper takes the next one
    either way. The other three views are the same page with a different
    list - what is on a phone now, what a run set aside, and the
    delivered archive the panel reads fates from.

    `explain(status)` turns a set-aside status into (what was seen, what
    to do) - app passes failures.verdict; pages never import it. `form`
    and `error` are the by-hand add coming back refused.
    """
    counts = data.get("counts") or {}
    view = data.get("view") or "waiting"
    if view not in GPT_VIEWS:
        view = "waiting"
    rows = data.get("rows") or []
    q = str(data.get("q") or "")
    pulse = (user.get("nav") or {}).get("pulse") or {}
    warm = None if pulse.get("warm") is None else int(pulse["warm"])
    can_login = manual_login and _may(user, "may_login_accounts")

    right = ""
    if view == "delivered":
        right = (f'<form method="get" action="/pools/gpt" class="inline">'
                 f'<input type="hidden" name="view" value="delivered">'
                 f'<input name="q" value="{esc(q)}" placeholder="address or '
                 f'phone" size="18"></form>'
                 f'<a class="btn quiet" href="/pools/gpt/delivered.csv'
                 f'?q={_q(q)}">Export CSV</a>')
    body = (f'<div class="narrow">'
            f'<div class="top"><h2>Gpt Pool</h2>'
            f'<span class="sub" style="margin:0">'
            f'{_gpt_sentence(view, counts, data, warm)}</span>'
            f'<span class="status">{right}</span></div>'
            + _said(said, _POOL_SAID))
    if view == "waiting":
        body += _gpt_add(user, form, error)
    body += _view_pills("/pools/gpt", GPT_VIEWS, view, counts)

    if view == "delivered":
        head = ("<tr><th>address</th><th>phone</th><th>delivered</th>"
                "<th>where from</th></tr>")
        lines = "".join(
            f'<tr><td>{esc(r["address"])}</td>'
            f'<td>{_serial_link(r.get("serial"))}</td>'
            f'<td class="muted">{_when(r["updated_at"])}</td>'
            f'<td>{_source_badge(r)}</td></tr>' for r in rows)
        empty = (f'nothing delivered matches "{q}"' if q else
                 "nothing has been delivered yet")
    elif view == "on_phone":
        head = ("<tr><th>address</th><th>phone</th><th>state</th>"
                "<th>since</th></tr>")
        lines = "".join(
            f'<tr><td>{esc(r["address"])}</td>'
            f'<td>{_serial_link(r.get("serial"))}</td>'
            f'<td>{_on_phone_badge(r)}</td>'
            f'<td class="muted">{_when(r["updated_at"])}</td></tr>'
            for r in rows)
        empty = "no account is signing in or waiting to go out"
    elif view == "needs_human":
        offer = _may(user, "may_add_gpt")
        head = ("<tr><th>address</th><th>reason</th><th>what happened</th>"
                "<th></th></tr>")
        lines = "".join(
            f'<tr><td>{esc(r["address"])} {_source_badge(r)}</td>'
            f'<td>{_reason_words(str(r["status"]))}</td>'
            f'<td class="muted">{_gpt_happened(r, explain)}</td>'
            f'<td class="right">' + (
                f'<form method="post" action="/pools/gpt/offer" '
                f'class="inline">{_csrf(user)}<input type="hidden" '
                f'name="address" value="{esc(r["address"])}">'
                f'<button class="quiet warn">Offer again</button></form>'
                if offer else "") + "</td></tr>" for r in rows)
        empty = "nothing has been set aside for a person"
    else:
        th = "<th></th>" if can_login else ""
        head = (f"<tr>{th}<th>address</th><th>where from</th><th>2fa</th>"
                f"<th>added</th></tr>")
        lines = "".join(
            "<tr>" + (f'<td><input type="checkbox" name="addresses" '
                      f'value="{esc(str(r["address"]))}"></td>'
                      if can_login else "")
            + f'<td>{esc(r["address"])}</td>'
              f'<td>{_source_badge(r)}</td>'
              f'<td>{_kind_2fa_word(r)}</td>'
              f'<td class="muted">{_when(r.get("created_at"))}</td></tr>'
            for r in rows)
        empty = ("nothing is waiting - paste the accounts you bought above, "
                 "or wait for the panel to send its next one")

    table = (f'<table>{head}{lines}</table>' if lines else
             f'<p class="empty">{esc(empty)}</p>')
    base = f"/pools/gpt?view={view}&q={_q(q)}"
    pager = (_pager(base, int(data.get("page") or 1),
                    int(data.get("pages") or 1), bool(data.get("more")))
             if int(data.get("pages") or 1) > 1 or data.get("more") else "")
    foot = f'<p class="dim">{esc(GPT_VIEWS[view]["sub"])}</p>'
    if view == "needs_human":
        foot = (foot if rows else "") + _need(user, "may_add_gpt",
                                              "offering accounts again")
    elif view == "waiting":
        if can_login:
            foot = ('<div class="row"><span class="dim">each ticked account '
                    'boots one warm phone and logs in there; the progress '
                    'of each one lands in <a href="/requests">Requests</a>'
                    '</span><button class="right">Log in selected</button>'
                    '</div>')
        elif manual_login:
            foot += _need(user, "may_login_accounts", "logging accounts in")
        else:
            foot = ('<p class="dim">accounts log in on their own on the next '
                    'pass — the keeper takes the top one as soon as a phone '
                    'is warm</p>')
    panel = f'<div class="panel wrap">{table}{foot}{pager}</div>'
    if view == "waiting" and can_login:
        panel = (f'<form method="post" action="/accounts/login">{_csrf(user)}'
                 f'<input type="hidden" name="back" value="/pools/gpt">'
                 f'{panel}</form>')
    body += panel

    if view == "needs_human" and data.get("broken"):
        broken = "".join(
            f'<tr><td>{esc(r.get("address") or "")}</td>'
            f'<td class="muted">{esc(r.get("error") or "")}</td></tr>'
            for r in data["broken"])
        body += (f'<div class="panel bad"><h3>Refused before the pool '
                 f'<span class="n">{len(data["broken"])}</span></h3>'
                 f'<p class="hint">these rows were never stock: the sheet '
                 f'has them, validation would not take them. Fix the cell, '
                 f'or ask whoever sold them for the money back.</p>'
                 f'<table>{broken}</table></div>')
    return page("Gpt Pool", body + "</div>", user=user, here="/pools/gpt")


# ----------------------------------------------------------- previews
# What a paste becomes before it is queued: one line per row with a
# verdict, and the good rows carried into the confirm form as the same
# tab-separated text - so the confirm re-reads exactly what was shown.

#: Refusals that come back in the words of the thing that raised them.
#: The person reading a preview bought these accounts; they did not write
#: the validator (the operator, 2026-09-07).
_PLAINER = (
    ("is not an email address",
     "no address on this line - check the first column"),
    ("cannot be typed",
     "this password has a character the phone cannot type - retype it with "
     "plain letters, digits and symbols"),
    ("not valid base32",
     "that does not look like an authenticator key - it is letters A-Z and "
     "digits 2-7, and Google shows it in groups of four"),
)


def _plainer(said: str) -> str:
    for needle, instead in _PLAINER:
        if needle in said:
            return instead
    return said


def _verdict_badge(row: dict) -> str:
    if row.get("twice"):
        return ('<span class="badge bad">the same address is on an earlier '
                'line</span>')
    if row.get("duplicate"):
        # And where it is. "Already in the pool" was said about rows the
        # manager deliberately does not list - a used Gmail, a delivered
        # account - so the operator went looking for a row that is not
        # there (2026-09-07).
        where = {"used": "this address was used up",
                 "delivered": "this account has been delivered",
                 "on a phone": "already in the pool - on a phone",
                 "set aside": "already in the pool - set aside",
                 "broken": "already in the pool - and unreadable",
                 "free": "already in the pool - free"}.get(
                     str(row.get("dup_state") or ""), "already in the pool")
        return f'<span class="badge bad">{esc(where)}</span>'
    if row.get("error"):
        # The raw words stay on the hover: they are what a person would
        # quote when asking somebody else about it.
        return (f'<span class="badge bad" title="{esc(row["error"])}">'
                f'{esc(_plainer(str(row["error"])))}</span>')
    if row.get("unread"):
        # Refused, not trimmed: a piece the reader could not place is a
        # line the person meant differently, and adding what was
        # understood would add it wrong without a word.
        return (f'<span class="badge bad">not understood: '
                f'{esc(", ".join(row["unread"]))}</span>')
    return '<span class="badge ok">ok</span>'


def _good(rows: list[dict]) -> list[dict]:
    return [r for r in rows if not r.get("error") and not r.get("duplicate")
            and not r.get("unread") and not r.get("twice")]


def _second_factor(row: dict) -> str:
    """The second factor, in full: a person checking a paste has to see
    the key they pasted, not a word for it (the operator, 2026-09-06)."""
    if row.get("recovery"):
        return f'recovery: <span class="mono">{esc(row["recovery"])}</span>'
    if row.get("secret"):
        return f'<span class="mono">{esc(row["secret"])}</span>'
    return "—"


def _shown_password(row: dict) -> str:
    """The password as pasted - it is theirs, and a preview that hides
    what it is about to write is not a preview."""
    return (f'<span class="mono">{esc(row["password"])}</span>'
            if row.get("password") else "—")


def _preview_card(action: str, rows: list[dict], good: list[dict],
                  user: dict, idem: str, back: str, lines: str,
                  carried: str, hidden: str = "", note: str = "") -> str:
    """The preview as one card: what it read at the top, the rows in a
    table that scrolls inside the card, the confirm at the foot.

    It was three panels - the table, then a form with the button, then
    the paste again - and inside the manager's sheet, cut to two thirds
    of its width, it read as a mess (the operator, 2026-09-08). Nothing
    is written until the button at the foot is pressed, and the heading
    says so in numbers.
    """
    skipped = len(rows) - len(good)
    lede = (f"{_plural(len(good), 'row')} to add"
            + (f", {skipped} to skip" if skipped else ""))
    return (f'<form method="post" action="{action}" class="panel preview">'
            f'{_csrf(user)}<input type="hidden" name="idem" value="{esc(idem)}">'
            f'<input type="hidden" name="back" value="{esc(back)}">{hidden}'
            f'<textarea name="rows" hidden>{esc(carried)}</textarea>'
            f'<div class="lede"><h3>{esc(lede)}</h3>'
            f'<span class="dim">nothing is written until you press Add'
            f'</span></div>'
            f'<div class="wrap"><table>{lines}</table></div>'
            f'<div class="row"><span class="dim">{note}</span>'
            f'<span class="right"></span>'
            f'<a class="btn quiet" href="{esc(back)}">Back</a>'
            + (f'<button>Add {len(good)} (skip {skipped})</button>' if good
               else '<span class="badge bad">nothing to add</span>')
            + '</div></form>')


def gmail_preview(rows: list[dict], seller: str, user: dict,
                  idem: str, *, pasted: str = "",
                  sellers: list | None = None,
                  back: str = "/pools/gmail") -> str:
    """The verdicts, the confirm, and the paste kept in an editable box
    underneath - a typo is fixed there and previewed again, not pasted
    from scratch."""
    good = _good(rows)
    lines = "<tr><th>address</th><th>password</th><th>2fa</th><th>verdict" \
            "</th></tr>" + "".join(
        f"<tr><td>{esc(r.get('address') or r.get('line', ''))}</td>"
        f"<td class=\"muted\">{_shown_password(r)}</td>"
        f"<td class=\"muted\">{_second_factor(r)}</td>"
        f"<td>{_verdict_badge(r)}</td></tr>" for r in rows)
    carried = "\n".join(
        f"{r['address']}\t{r['password']}\t{r.get('recovery') or r.get('secret') or ''}"
        for r in good)
    body = ('<div class="top"><h2>Gmail Pool</h2><span class="status">'
            'preview — nothing is added yet</span></div>'
            + _preview_card(
                "/pools/gmail/add", rows, good, user, idem, back, lines,
                carried,
                hidden=f'<input type="hidden" name="seller" value="{esc(seller)}">',
                note=f'seller: {esc(seller or "(none)")}')
            + f'<div class="panel"><h3>Edit and preview again</h3>'
            f'<form method="post" action="/pools/gmail/preview" class="field">'
            f'{_csrf(user)}'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<textarea name="pasted">{esc(pasted)}</textarea>'
            f'<div class="row">{_seller_pick(list(sellers or []), seller)}'
            f'<span class="right"></span>'
            f'<button class="quiet">Preview again</button></div>'
            f'</form></div>')
    return page("Gmail Pool — preview", body, user=user, here="/pools/gmail")


def spotify_preview(rows: list[dict], user: dict, idem: str, *,
                    category: str = "normal", pasted: str = "",
                    back: str = "/") -> str:
    """The Spotify paste, judged row by row, with the category it was
    pasted under carried into the confirm.

    One paste is one category - a line cannot say which kind it is - so
    the button at the foot says which, and nobody adds twenty rows as
    the wrong kind (2026-09-17).
    """
    good = _good(rows)
    lines = "<tr><th>address</th><th>password</th><th>verdict</th></tr>" + \
        "".join(
            f"<tr><td>{esc(r.get('address') or r.get('line', ''))}</td>"
            f'<td class="muted">{_shown_password(r)}</td>'
            f"<td>{_verdict_badge(r)}</td></tr>" for r in rows)
    carried = "\n".join(f"{r['address']}\t{r['password']}" for r in good)
    hidden = f'<input type="hidden" name="category" value="{esc(category)}">'
    body = ('<div class="top"><h2>Spotify pool</h2><span class="status">'
            'preview - nothing is added yet</span></div>'
            + _preview_card("/pools/spotify/add", rows, good, user, idem,
                            back, lines, carried, hidden=hidden,
                            note=f"they go in as {esc(category)} accounts")
            + '<div class="panel"><h3>Edit and preview again</h3>'
            + f'<form method="post" action="/pools/spotify/preview" '
              f'class="field">{_csrf(user)}{hidden}'
              f'<input type="hidden" name="back" value="{esc(back)}">'
              f'<textarea name="pasted">{esc(pasted)}</textarea>'
              f'<div class="row"><span class="right"></span>'
              f'<button class="quiet">Preview again</button></div>'
              f'</form></div>')
    return page("Spotify pool - preview", body, user=user, here="/")


def gpt_preview(rows: list[dict], user: dict, idem: str, *,
                pasted: str = "", back: str = "/pools/gpt") -> str:
    """The Gpt Pool's paste, judged row by row the way the by-hand form
    judges one; the good rows ride into the confirm as the same
    tab-separated text, and the paste stays in a box underneath."""
    good = _good(rows)
    lines = "<tr><th>address</th><th>password</th><th>2fa</th><th>verdict" \
            "</th></tr>" + "".join(
        f"<tr><td>{esc(r.get('address') or r.get('line', ''))}</td>"
        f"<td class=\"muted\">{_shown_password(r)}</td>"
        f"<td class=\"muted\">{_second_factor(r)}</td>"
        f"<td>{_verdict_badge(r)}</td></tr>" for r in rows)
    carried = "\n".join(
        f"{r['address']}\t{r['password']}\t{r.get('secret') or ''}"
        for r in good)
    body = ('<div class="top"><h2>Gpt Pool</h2><span class="status">'
            'preview — nothing is added yet</span></div>'
            + _preview_card("/pools/gpt/add", rows, good, user, idem, back,
                            lines, carried,
                            note="each waits for a phone to be sent to")
            + f'<div class="panel"><h3>Edit and preview again</h3>'
            f'<form method="post" action="/pools/gpt/preview" class="field">'
            f'{_csrf(user)}'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<textarea name="pasted">{esc(pasted)}</textarea>'
            f'<div class="row"><span class="right"></span>'
            f'<button class="quiet">Preview again</button></div>'
            f'</form></div>')
    return page("Gpt Pool — preview", body, user=user, here="/pools/gpt")


def proxy_preview(rows: list[dict], user: dict, idem: str, *,
                  back: str = "/pools/proxy") -> str:
    """The same one card the other two previews are; `back` is where the
    paste came from - the dashboard's sheet, or the pool page."""
    good = [r for r in rows if not r.get("error") and not r.get("duplicate")]
    lines = "<tr><th>name</th><th>proxy</th><th>verdict</th></tr>" + "".join(
        f"<tr><td>{esc(r.get('name') or 'next SX')}</td>"
        f"<td class=\"muted\">{esc(r.get('raw') or r.get('line', ''))}</td>"
        f"<td>{_verdict_badge(r)}</td></tr>" for r in rows)
    carried = "\n".join(f"{r['name']}\t{r['raw']}" if r.get("name")
                        else r["raw"] for r in good)
    body = ('<div class="top"><h2>Proxy Pool</h2><span class="status">'
            'preview — each is tested by the pass before it joins</span>'
            '</div>'
            + _preview_card("/pools/proxy/add", rows, good, user, idem, back,
                            lines, carried,
                            note="each is tested before it joins; one that "
                                 "does not answer goes in as dead"))
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


#: The dimensions of the Login rate page, in reading order, with the
#: word each key is called.
_LOGIN_TABLES = (("seller", "By seller", "sold by"),
                 ("model", "By phone model", "brand and model"),
                 ("host", "By exit host", "host address"),
                 ("position", "By position on the phone", "Gmail #"),
                 ("reason", "By how it ended", "outcome"),
                 ("day", "By day", "date"))


def logins_page(data: dict, user: dict) -> str:
    """Every Google sign-in of the last week, by what it depended on.

    Read to judge a purchase (a seller whose addresses never sign in), a
    phone model or an exit host (the gates set aside what this shows),
    and every builder change (the rate before and after). Each table is
    the same shape: the key, signed in / attempts, the rate as a bar.
    Rows under `min_sample` attempts are shown but greyed: too few to
    judge (the login-rate work, 2026-09-10).
    """
    days = int(data.get("days") or 7)
    totals = data.get("totals") or {}
    least = int(data.get("min_sample") or 5)
    by = data.get("by") or {}

    def pct(rate: float) -> str:
        return f"{round(float(rate or 0) * 100)}%"

    def table(key: str, title: str, word: str) -> str:
        rows = by.get(key) or []
        if not rows:
            return (f'<div class="panel"><h3>{esc(title)}</h3>'
                    f'<p class="dim">Nothing recorded yet.</p></div>')
        lines = []
        for r in rows:
            n, ok, rate = int(r.get("n") or 0), int(r.get("ok") or 0), \
                float(r.get("rate") or 0)
            thin = n < least
            colour = ("ready" if rate >= 0.6 else
                      "warn" if rate >= 0.35 else "failed")
            lines.append(
                f'<tr{" class=thin" if thin else ""}>'
                f'<td>{esc(str(r.get("key") or "") or "&mdash;")}</td>'
                f'<td class="num">{ok}/{n}</td>'
                f'<td><span class="bar"><i style="width:{round(rate * 100)}%"'
                f' class="{colour}"></i></span> '
                f'<span class="badge {colour}">{pct(rate)}</span>'
                + (' <span class="dim">too few to judge</span>' if thin else "")
                + '</td></tr>')
        return (f'<div class="panel"><h3>{esc(title)} '
                f'<span class="n">{len(rows)}</span></h3>'
                f'<table><thead><tr><th>{esc(word)}</th><th>signed in</th>'
                f'<th>rate</th></tr></thead><tbody>{"".join(lines)}</tbody>'
                f'</table></div>')

    head = (f'<div class="panel"><h3>Login rate <span class="n">last {days} '
            f'day{"s" if days != 1 else ""}</span></h3>'
            f'<div class="strip">'
            f'<div><b>{pct(totals.get("rate", 0))}</b><span>signed in</span></div>'
            f'<div><b>{int(totals.get("ok") or 0)}</b><span>of '
            f'{int(totals.get("n") or 0)} attempts</span></div>'
            f'<div><b>{int(totals.get("gmails") or 0)}</b><span>addresses '
            f'tried</span></div></div>'
            f'<p class="dim">One row per Google sign-in the builder made. '
            f'Rows with fewer than {least} attempts are too few to judge; '
            f'the host gate reads the exit-host table, the model gate the '
            f'phone-model one.</p>'
            f'<form method="get" action="/logins" class="row">'
            f'<label class="field"><span>Days</span>'
            f'<select name="days" onchange="this.form.submit()">'
            + "".join(f'<option value="{d}"{" selected" if d == days else ""}>'
                      f'{d}</option>' for d in (1, 3, 7, 14, 30))
            + '</select></label><noscript><button class="quiet">Show'
              '</button></noscript></form></div>')
    body = ('<style>.thin td{opacity:.55}.bar{display:inline-block;width:120px;'
            'height:8px;background:var(--panel2);border-radius:4px;'
            'vertical-align:middle;overflow:hidden}.bar i{display:block;height:100%;'
            'border-radius:4px;background:var(--red)}.bar i.warn{background:'
            'var(--amber)}.bar i.ready{background:var(--green)}'
            '.strip{display:flex;gap:28px;flex-wrap:wrap;margin:4px 0 10px}'
            '.strip b{display:block;font-family:var(--mono);font-size:26px;'
            'font-weight:500}.strip span{color:var(--dim);font-size:12px}'
            '</style><h2>Login rate</h2>' + head
            + "".join(table(*t) for t in _LOGIN_TABLES))
    return page("Login rate", body, user=user, here="/logins")


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
            '<span class="status">admin only · '
            '<span class="live">live</span></span></div>'
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
            f' · INFO and up · kept 30 days · <span class="live">live'
            f'</span></span></div>'
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
    # On the logs stream: the farm's own fingerprint does not move for a
    # log line, and this page is nothing but log lines.
    return page("Logs", body, user=user, here="/events", refresh=15,
                live="logs")


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
        # Folded away. Eighteen file names - `200117-play-package-page.xml` -
        # laid end to end was the longest thing on the page and the least
        # readable, and they are for whoever is debugging a flow, not for
        # the person deciding what to do with the phone (the operator,
        # 2026-09-07).
        links = " · ".join(
            f'<a href="/phones/{esc(serial)}/screens/{_q(folder)}/{_q(f)}">'
            f'{esc(f)}</a>' for f in files)
        if links:
            links = (f'<details class="tech"><summary>the screens</summary>'
                     f'{links}</details>')
        outcome = esc(status) if status else ""
        return head, " ".join(b for b in (outcome, links) if b)
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
    if kind == "history":
        return _history_lines(status, text)
    if kind == "breaker":
        return (f"Breaker {esc(status)}", esc(text))
    head = f"{esc(kind)} {esc(status)}".strip()
    return head, esc(text)


#: The fields a history line carries, in the order a person would ask
#: about them. `Steps` is the flow's own trail - a debugging aid, folded.
_HISTORY_SAID = ("Note", "Gmail", "GPT Account", "Proxy", "Event")


def _history_lines(status: str, text: str) -> tuple[str, str]:
    """A history entry as a sentence rather than the record it is stored as.

    It was printed whole: `Seconds=695; Proxy=SX23; Gmail=...; Note=Ready -
    signed into Google...; Steps=google: loading > dismissable > email_entry
    > captcha x17 | ...`. Every fact was in there and none of it was
    readable, and the one part an operator wants - what became of the phone,
    and how long it took - was in the middle of it (2026-09-07).
    """
    fields, order = {}, []
    for part in str(text or "").split(";"):
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if sep and key:
            if key not in fields:
                order.append(key)
            fields[key] = value
    if not fields:
        return (f"History: {esc(status)}".strip(), esc(text))
    took = fields.get("Seconds", "")
    head = f"Its story: {esc(status or fields.get('Event') or 'recorded')}"
    if took.replace(".", "", 1).isdigit():
        minutes, seconds = divmod(int(float(took)), 60)
        head += (f' <span class="dim">· took {minutes}m {seconds}s</span>'
                 if minutes else f' <span class="dim">· took {seconds}s</span>')
    said = [esc(fields[k]) for k in _HISTORY_SAID if fields.get(k)]
    rest = [f"{esc(k)}={esc(fields[k])}" for k in order
            if k not in _HISTORY_SAID and k != "Seconds" and fields[k]]
    if rest:
        said.append(f'<details class="tech"><summary>the rest</summary>'
                    f'{" · ".join(rest)}</details>')
    return head, " · ".join(said)


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
           klass: str = "", title: str = "") -> str:
    """One line of a phone's story, as a table row: when it happened,
    what kind of thing it was, and what happened with its explanation
    under it. `title` is what the when cell says on hover - the other
    times, when identical failures were folded into one row."""
    aside_html = f'<br><span class="dim">{aside}</span>' if aside else ""
    row = f' class="{klass}"' if klass else ""
    hint = f' title="{esc(title)}"' if title else ""
    return (f'<tr{row}><td class="muted mono"{hint}>{when}</td>'
            f'<td>{badge}</td><td>{head}{aside_html}</td></tr>')


def _phone_facts(serial: str, phone: dict) -> str:
    """What this phone is, in one line: the account it carries, the gmail
    under it and its exit - which is exactly what a customer is handed,
    so triple-clicking the line copies the lot. When it was built is
    dimmer and outside the selection: nobody is given a date."""
    bits = [serial, str(phone.get("app_account") or ""),
            str(phone.get("gmail") or ""),
            str(phone.get("proxy_name") or "")]
    line = " · ".join(b for b in bits if b)
    built = _when(phone.get("created_at"))
    return (f'<p class="facts"><span class="hand" title="click three times '
            f'to select it all - this is what the customer needs">'
            f'{esc(line)}</span>'
            + (f' <span class="dim">· built {esc(built)}</span>'
               if built else "") + "</p>")


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


def phone_story_page(story: dict, user: dict, *, explain=None,
                     said: str = "") -> str:
    """Everything one phone went through, two lines an entry: what
    happened, then why or what it means. Identical failures in a row
    fold into one entry with every time listed; the story closes with
    where the phone stands now. `explain` turns a status token into
    (seen, advice) - app passes failures.verdict; pages never import it."""
    phone = story.get("phone") or {}
    serial = str(story["serial"])
    back = f"/phones/{serial}"
    head = f'<span>{_phone_badge(phone)}</span>' if phone else ""
    if phone and phone.get("done_at"):
        head += f'<span class="badge">gone {_day(phone["done_at"])}</span>'
    actions = []
    # The same rule the table keeps. This page kept none, so clicking a
    # serial that read "with ali" offered Done and Failed on ali's phone
    # (2026-09-07).
    held_by = _theirs(user, phone) if phone else ""
    if held_by:
        actions = [f'<span class="age">with {esc(held_by)}</span>']
    elif phone and not phone.get("done_at"):
        building = (phone.get("status") or "") == "building"
        # Boot starts the phone and takes it. Its holder reopens the
        # screen with it; an admin on somebody else's phone may end the
        # hold (2026-09-15), not take it over.
        mine = _holder(phone) in ("", str(user.get("username") or ""))
        if not building and mine and _may(user, "may_take_phones"):
            actions.append(_boot_form(user, serial))
        actions += _state_forms(user, dict(phone, serial=serial), back)
        if _may(user, "may_change_proxy") and not building:
            actions.append(_change_ip_form(user, serial, back))
        if building:
            # The one thing there is to do about a phone being built, and
            # the table has always offered it. This page offered nothing
            # at all, so opening a build to watch it was a dead end
            # (2026-09-07).
            actions.append(_cancel_form(user, serial, back))
    actions = [a for a in actions if a]

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
        when, times = _when(first["at"]), ""
        if len(group) > 1:
            headline += f' <span class="dim">· {len(group)} times</span>'
            times = f"{_day(first['at'])}: " + ", ".join(
                _hhmm(t["at"]) for t in group)
        secs = (f' <span class="dim">· {int(first["seconds"])}s</span>'
                if first.get("seconds") and len(group) == 1 else "")
        items.append(_entry(when, f"{headline}{secs} {runs}".strip(), aside,
                            badge, title=times))
    if phone:
        items.append(_now_entry(phone))
    hint = _need(user, "may_take_phones", "taking, returning and closing "
                                          "this phone") if phone else ""
    table = (f'<table><tr><th>when</th><th>what</th><th>what happened</th>'
             f'</tr>{"".join(items)}</table>' if items else
             '<p class="empty">Nothing recorded about this phone.</p>')
    # Before `.top`, and wearing neither `alerts` nor `banner`: the
    # drawer hides `.top` and strips both of those, so a press inside it
    # said nothing at all - and neither did a press with the script off,
    # which is a plain navigation to this page (2026-09-07).
    body = (f'<div class="narrow">{_said(said, _DASH_SAID, user)}'
            f'<div class="top"><a href="/" class="dim">← Dashboard</a>'
            f'<h2>Phone {esc(serial)}</h2>{head}'
            f'<span class="status">{" ".join(actions)}</span></div>'
            + (_phone_facts(serial, phone) if phone else "")
            + f'<div class="panel wrap"><h3>Its story '
              f'<span class="n">{_plural(len(items), "entry", "entries")}'
              f'</span></h3>{table}{hint}'
              f'<p class="dim">everything this phone went through, in order '
              f'— events, requests and archived screens joined on its serial'
              f' · <a href="/logs?phone={esc(serial)}">open its log lines'
              f'</a></p></div></div>')
    return page(f"Phone {serial}", body, user=user, here="/")


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
            f'in 30 seconds.</p>{again}</div>'
            # Its own retry. The browser's meta refresh sits inside
            # <noscript>, and there is no user here and so no script - so
            # "retries in 30 seconds" was a sentence and nothing else
            # (2026-09-14).
            f'<script>setTimeout(function(){{ location.reload(); }}, '
            f'30000);</script>')
    return page("Store down", body, refresh=30)


#: The authenticator, in the page: base32 to bytes, HMAC-SHA1 through
#: WebCrypto, the RFC 6238 truncation - so the code is right to the
#: second and counts down, instead of riding on a beat that is fifteen
#: seconds old. The page is served over TLS, which WebCrypto requires.
_TOTP_SCRIPT = (
    "(function(){"
    "var el=document.getElementById('gf-totp'); if(!el) return;"
    "var secret=el.getAttribute('data-secret');"
    "var bar=document.getElementById('gf-totp-bar');"
    "function b32(s){s=s.replace(/[^A-Za-z2-7]/g,'').toUpperCase();"
    " var A='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits='',out=[];"
    " for(var i=0;i<s.length;i++){bits+=A.indexOf(s[i]).toString(2).padStart(5,'0');}"
    " for(var j=0;j+8<=bits.length;j+=8){out.push(parseInt(bits.slice(j,j+8),2));}"
    " return new Uint8Array(out);}"
    "var keyP=crypto.subtle.importKey('raw',b32(secret),{name:'HMAC',hash:'SHA-1'},false,['sign']);"
    "var last=-1;"
    "function tick(){"
    " var now=Math.floor(Date.now()/1000), step=Math.floor(now/30), left=30-(now%30);"
    " if(bar) bar.style.width=(left/30*100)+'%';"
    " if(step===last) return; last=step;"
    " keyP.then(function(key){"
    "  var msg=new Uint8Array(8), t=step;"
    "  for(var i=7;i>=0;i--){msg[i]=t&255; t=Math.floor(t/256);}"
    "  return crypto.subtle.sign('HMAC',key,msg);"
    " }).then(function(sig){"
    "  var h=new Uint8Array(sig), o=h[19]&15;"
    "  var c=((h[o]&127)<<24|h[o+1]<<16|h[o+2]<<8|h[o+3])%1000000;"
    "  el.textContent=String(c).padStart(6,'0');"
    " }).catch(function(){el.textContent='------';});"
    "}"
    "tick(); setInterval(tick,1000);"
    "document.querySelectorAll('[data-copy]').forEach(function(b){"
    " b.addEventListener('click',function(){"
    "  var t=document.getElementById(b.getAttribute('data-copy'));"
    "  var text=t.getAttribute('data-value')||t.textContent;"
    "  navigator.clipboard.writeText(text).then(function(){"
    "   b.textContent='copied'; setTimeout(function(){b.textContent='copy';},1500);});"
    " });});"
    "var pw=document.getElementById('gf-pw');"
    "var show=document.getElementById('gf-pw-show');"
    "if(pw&&show){show.addEventListener('click',function(){"
    " var on=pw.textContent==='\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022';"
    " pw.textContent=on?pw.getAttribute('data-value'):'\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022';"
    " show.textContent=on?'hide':'show';});}"
    "})();"
)


def _gmail_margin(creds: dict) -> str:
    """The Gmail's rows for the margin: address, password (hidden until
    shown, copied without showing) and the authenticator's code as it
    stands. Not the key it is made from: the code is what a person
    types, and the key beside it was one more thing to read past (the
    operator, 2026-09-16). It stays on the pool row."""
    address = str(creds.get("address") or "")
    password = str(creds.get("password") or "")
    secret = str(creds.get("totp_secret") or "")
    dots = "\u2022" * 8
    rows = [
        f'<div class="gf-row"><span class="lbl">Gmail</span>'
        f'<div class="val"><code id="gf-mail">{esc(address)}</code>'
        f'<button type="button" class="quiet" data-copy="gf-mail">copy'
        f'</button></div></div>']
    if password:
        rows.append(
            f'<div class="gf-row"><span class="lbl">Password</span>'
            f'<div class="val"><code id="gf-pw" data-value="{esc(password)}">'
            f'{dots}</code>'
            f'<button type="button" class="quiet" id="gf-pw-show">show</button>'
            f'<button type="button" class="quiet" data-copy="gf-pw">copy'
            f'</button></div></div>')
    else:
        rows.append('<div class="gf-row"><span class="lbl">Password</span>'
                    '<div class="val"><code class="dim">none on the row'
                    '</code></div></div>')
    if secret:
        rows.append(
            f'<div class="gf-row"><span class="lbl">Authenticator code</span>'
            f'<div class="val"><code id="gf-totp" class="gf-code" '
            f'data-secret="{esc(secret)}">------</code>'
            f'<button type="button" class="quiet" data-copy="gf-totp">copy'
            f'</button></div>'
            f'<div class="gf-bar"><i id="gf-totp-bar"></i></div></div>')
    else:
        rows.append('<div class="gf-row"><span class="lbl">Authenticator'
                    '</span><div class="val"><code class="dim">none on the '
                    'row</code></div></div>')
    return (f'<h3>On this phone</h3>{"".join(rows)}'
            f'<script>{_TOTP_SCRIPT}</script>')
