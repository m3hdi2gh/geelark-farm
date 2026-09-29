"""The admin's Stock page: what the stock planner reads, what it sets,
and the knobs that steer it (2026-09-30).

One panel per lane - the target and why, the shelf, the line, the day as
a chart - then the hour-of-day demand the plan is built on, how long the
operators waited, the Gmails left, the idle phones, and the settings.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .pages import _csrf, _said, esc, page

WORDS = {"gpt": "GPT", "spotify": "Spotify"}
COLOURS = {"gpt": "var(--blue)", "spotify": "var(--green)"}
SAID = {"saved": "Saved. The keeper plans with it from its next pass "
                 "(within a minute).",
        "no": "That was not saved - the store did not answer. Try again."}

_STYLE = (
    "<style>"
    ".stock-grid{display:grid;grid-template-columns:repeat(auto-fit,"
    "minmax(min(420px,100%),1fr));gap:16px;margin-bottom:16px}"
    ".stock-grid .panel{margin:0}"
    ".big{display:flex;gap:26px;flex-wrap:wrap;margin:6px 0 10px}"
    ".big b{display:block;font-family:var(--mono);font-size:28px;"
    "font-weight:500;line-height:1.1}"
    ".big span{color:var(--dim);font-size:12px}"
    ".big .hot b{color:var(--amber)}"
    ".why{color:var(--ink);font-size:13px;margin:4px 0 10px}"
    ".facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));"
    "gap:6px 18px;font-size:13px;margin:0 0 10px}"
    ".facts span{color:var(--dim)}"
    ".chart{width:100%;height:auto;display:block}"
    ".chart text{fill:var(--dim);font:11px var(--mono)}"
    ".chart .grid{stroke:var(--line2)}"
    ".legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;"
    "color:var(--dim);margin-top:4px}"
    ".legend i{display:inline-block;width:14px;height:3px;border-radius:2px;"
    "vertical-align:middle;margin-right:5px}"
    ".mode{display:inline-block;padding:2px 10px;border-radius:999px;"
    "font-size:12px;font-weight:600}"
    ".mode.auto{background:var(--green-bg);color:var(--green)}"
    ".mode.watch{background:var(--amber-bg);color:var(--amber)}"
    ".stockform{display:grid;grid-template-columns:repeat(auto-fit,"
    "minmax(190px,1fr));gap:12px 18px;align-items:end}"
    ".stockform label{display:flex;flex-direction:column;gap:4px;font-size:12px;"
    "color:var(--dim)}"
    ".stockform input,.stockform select{width:100%}"
    ".stockform .modes{display:flex;gap:14px;grid-column:1/-1;font-size:13px;"
    "color:var(--ink)}"
    ".stockform .modes label{flex-direction:row;align-items:center;gap:6px;"
    "color:var(--ink);font-size:13px}"
    ".stale td{color:var(--amber)}"
    "</style>")


def _zone(tz: str):
    from ..stockplan import _zone as zone

    return zone(tz)


def _clock(at, tz: str) -> str:
    if not isinstance(at, datetime):
        return ""
    return at.astimezone(_zone(tz)).strftime("%H:%M")


def _span(seconds) -> str:
    seconds = max(0, int(float(seconds or 0)))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    hours, rest = divmod(seconds, 3600)
    return f"{hours} h {rest // 60:02d} min"


def _num(value, digits: int = 1) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "&mdash;"
    text = f"{number:.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def stock_page(data: dict, user: dict, said: str = "") -> str:
    knobs = data.get("knobs") or {}
    mode = knobs.get("mode") or "watch"
    fixed = data.get("fixed") or {}
    tz = str(data.get("tz") or "Asia/Tehran")
    auto = mode == "auto"
    word = ("The planner steers the stock: each lane is built to the "
            "planner's target." if auto else
            "The planner is watching: the keeper builds to WARM_STOCK "
            f"(GPT {int(fixed.get('gpt', 0))}, Spotify "
            f"{int(fixed.get('spotify', 0))}) and the planner records what it "
            "would set. Switch it to steer the stock below.")
    head = (f'<h2>Stock <span class="mode {esc(mode)}">'
            f'{"steering" if auto else "watching"}</span></h2>'
            f'<p class="dim">{esc(word)}</p>')
    partial = data.get("partial") or []
    if partial:
        head += (f'<div class="panel warn"><h3>Some of this could not be read'
                 f'</h3><p class="dim">{esc(", ".join(partial))} - the rest is '
                 f'current.</p></div>')
    lanes = "".join(_lane_panel(lane, data, tz) for lane in ("gpt", "spotify"))
    body = (_STYLE + _said(said, SAID) + head
            + f'<div class="stock-grid">{lanes}</div>'
            + _profile_panel(data, tz) + _runway_panel(data)
            + _idle_panel(data, tz) + _knobs_panel(knobs, user))
    return page("Stock", body, user=user, here="/stock", live="farm")


def _lane_panel(lane: str, data: dict, tz: str) -> str:
    row = (data.get("latest") or {}).get(lane) or {}
    shelf = (data.get("shelves") or {}).get(lane) or {}
    waits = (data.get("waits") or {}).get(lane) or {}
    fixed = int((data.get("fixed") or {}).get(lane, 0))
    knobs = data.get("knobs") or {}
    stale_s = float(knobs.get("stale_hours") or 6) * 3600.0
    idle = [p for p in (data.get("idle") or []) if p.get("lane") == lane]
    stale = [p for p in idle if float(p.get("idle_s") or 0) >= stale_s]
    ready = int(shelf.get("ready") or 0)
    building = int(shelf.get("building") or 0)
    waiting = int(row.get("waiting") or 0)
    if row:
        planned = int(row.get("planned") or 0)
        used = int(row.get("used") or 0)
        at = _clock(row.get("at"), tz)
        why = str(row.get("why") or "")
    else:
        planned = used = fixed
        at = ""
        why = ("No plan yet: the keeper writes one a minute once it runs "
               "this version.")
    big = (f'<div class="big">'
           f'<div><b>{planned}</b><span>planner&#x27;s target</span></div>'
           f'<div><b>{used}</b><span>built toward</span></div>'
           f'<div><b>{ready}</b><span>on the shelf</span></div>'
           f'<div><b>{building}</b><span>being built</span></div>'
           f'<div class="{"hot" if waiting else ""}"><b>{waiting}</b>'
           f'<span>in the line</span></div></div>')
    asked = int(waits.get("asked") or 0)
    at_once = int(waits.get("at_once") or 0)
    share = f"{round(100 * at_once / asked)}%" if asked else "&mdash;"
    facts = (
        '<div class="facts">'
        f'<div><span>Takes an hour now</span><br>{_num(row.get("recent_h"))}</div>'
        f'<div><span>Usual for this hour</span><br>{_num(row.get("profile_h"))}'
        '</div>'
        f'<div><span>A phone lands in</span><br>{_num(row.get("lead_min"), 0)} min'
        f' ({_num(100 * float(row.get("success") or 0), 0)}% of builds work)</div>'
        f'<div><span>WARM_STOCK&#x27;s split</span><br>{fixed}</div>'
        f'<div><span>Asked for, 24 h</span><br>{asked} '
        f'({share} at once)</div>'
        f'<div><span>Waited, 24 h</span><br>median {_span(waits.get("p50_s"))}, '
        f'worst 5% {_span(waits.get("p95_s"))}</div>'
        f'<div><span>Longest wait</span><br>{_span(waits.get("max_s"))}</div>'
        f'<div><span>Refused (nothing coming)</span><br>'
        f'{int(waits.get("refused") or 0)}</div>'
        f'<div><span>Oldest on the shelf</span><br>'
        f'{_span(idle[0]["idle_s"]) if idle else "&mdash;"}'
        f'{f" ({len(stale)} stale)" if stale else ""}</div>'
        '</div>')
    stamp = f' <span class="n">as of {esc(at)}</span>' if at else ""
    return (f'<div class="panel"><h3>{WORDS[lane]}{stamp}</h3>{big}'
            f'<p class="why">{esc(why)}</p>{facts}'
            f'{_day_chart(lane, data, tz)}</div>')


def _day_chart(lane: str, data: dict, tz: str) -> str:
    """The last 24 hours: the takes of each hour as bars, the planner's
    target, WARM_STOCK's split and the shelf as lines."""
    takes = (data.get("takes_24h") or {}).get(lane) or [0] * 24
    points = [r for r in (data.get("history") or []) if r.get("lane") == lane]
    fixed = int((data.get("fixed") or {}).get(lane, 0))
    now = data.get("now")
    if not isinstance(now, datetime):
        return ""
    top = max([4, fixed] + list(takes)
              + [int(r.get("planned") or 0) for r in points]
              + [float(r.get("warm") or 0) for r in points])
    width, height, left, bottom = 600.0, 150.0, 28.0, 20.0
    plot_w, plot_h = width - left - 6, height - bottom - 8
    start = now - timedelta(hours=24)

    def x_of(at: datetime) -> float:
        seconds = (at - start).total_seconds()
        return left + max(0.0, min(1.0, seconds / 86400.0)) * plot_w

    def y_of(value: float) -> float:
        return 8 + plot_h - (float(value) / top) * plot_h

    parts = []
    for k in (0.5, 1.0):
        y = y_of(top * k)
        parts.append(f'<line class="grid" x1="{left}" x2="{width - 6}" '
                     f'y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text x="0" y="{y + 4:.1f}">{_num(top * k, 0)}</text>')
    hour0 = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23)
    colour = COLOURS[lane]
    for i, n in enumerate(takes):
        begin = hour0 + timedelta(hours=i)
        x0, x1 = x_of(begin), x_of(begin + timedelta(hours=1))
        if n:
            parts.append(f'<rect x="{x0 + 1:.1f}" y="{y_of(n):.1f}" '
                         f'width="{max(1.0, x1 - x0 - 2):.1f}" '
                         f'height="{y_of(0) - y_of(n):.1f}" rx="2" '
                         f'fill="{colour}" opacity=".35"/>')
        if begin.astimezone(_zone(tz)).hour % 3 == 0:
            parts.append(f'<text x="{x0:.1f}" y="{height - 4:.1f}">'
                         f'{begin.astimezone(_zone(tz)):%H}</text>')
    parts.append(f'<line x1="{left}" x2="{width - 6}" y1="{y_of(fixed):.1f}" '
                 f'y2="{y_of(fixed):.1f}" stroke="var(--dim)" '
                 f'stroke-dasharray="4 4"/>')
    for key, style in (("warm", 'stroke="var(--violet)" stroke-width="1.5"'),
                       ("planned", f'stroke="{colour}" stroke-width="2.2"')):
        line = " ".join(f'{x_of(r["slot"]):.1f},{y_of(float(r.get(key) or 0)):.1f}'
                        for r in points if isinstance(r.get("slot"), datetime))
        if line:
            parts.append(f'<polyline fill="none" {style} points="{line}"/>')
    legend = (f'<div class="legend"><span><i style="background:{colour};'
              f'opacity:.5"></i>takes an hour</span><span><i style="background:'
              f'{colour}"></i>planner&#x27;s target</span><span><i style='
              f'"background:var(--violet)"></i>on the shelf</span><span><i '
              f'style="background:var(--dim)"></i>WARM_STOCK</span></div>')
    return (f'<svg class="chart" viewBox="0 0 {width:.0f} {height:.0f}" '
            f'role="img" aria-label="{WORDS[lane]}: the last 24 hours">'
            + "".join(parts) + "</svg>" + legend)


def _profile_panel(data: dict, tz: str) -> str:
    """The demand the plan is built on: takes an hour, by hour of the day,
    each lane's own, over the days the history holds."""
    shape = data.get("profile") or {}
    raw = shape.get("raw") or {}
    if not raw:
        return ""
    now = data.get("now")
    hour_now = (now.astimezone(_zone(tz)).hour if isinstance(now, datetime)
                else -1)
    totals = [sum(float((raw.get(lane) or [0] * 24)[h]) for lane in raw)
              for h in range(24)]
    top = max([1.0] + totals)
    width, height, left, bottom = 600.0, 130.0, 28.0, 20.0
    plot_w, plot_h = width - left - 6, height - bottom - 8
    slot = plot_w / 24.0
    parts = []
    for k in (0.5, 1.0):
        y = 8 + plot_h - k * plot_h
        parts.append(f'<line class="grid" x1="{left}" x2="{width - 6}" '
                     f'y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text x="0" y="{y + 4:.1f}">{_num(top * k, 1)}</text>')
    for h in range(24):
        x = left + h * slot
        base = 8 + plot_h
        for lane in ("gpt", "spotify"):
            value = float((raw.get(lane) or [0] * 24)[h])
            if value <= 0:
                continue
            tall = value / top * plot_h
            base -= tall
            parts.append(f'<rect x="{x + 1:.1f}" y="{base:.1f}" '
                         f'width="{slot - 2:.1f}" height="{tall:.1f}" '
                         f'fill="{COLOURS[lane]}" '
                         f'opacity="{".95" if h == hour_now else ".55"}"/>')
        if h % 3 == 0:
            parts.append(f'<text x="{x:.1f}" y="{height - 4:.1f}">{h:02d}</text>')
    share = shape.get("share") or {}
    note = (f'Over the last {esc(str(shape.get("days") or ""))} days, in '
            f'{esc(tz)} hours. The plan reads the farm&#x27;s total for this '
            f'hour and the next, split by each lane&#x27;s share of the last '
            f'two days (GPT {round(100 * float(share.get("gpt", 0)))}%, Spotify '
            f'{round(100 * float(share.get("spotify", 0)))}%).')
    legend = ('<div class="legend">' + "".join(
        f'<span><i style="background:{COLOURS[lane]}"></i>{WORDS[lane]}</span>'
        for lane in ("gpt", "spotify")) + '<span>the hour now is brighter</span>'
        '</div>')
    return (f'<div class="panel"><h3>Takes an hour, by hour of the day</h3>'
            f'<p class="dim">{note}</p><svg class="chart" viewBox="0 0 '
            f'{width:.0f} {height:.0f}" role="img" aria-label="Takes an hour '
            f'by hour of the day">' + "".join(parts) + "</svg>" + legend
            + "</div>")


def _runway_panel(data: dict) -> str:
    runway = data.get("runway") or {}
    if not runway:
        return ""
    free = int(runway.get("free") or 0)
    hours = runway.get("hours")
    per_hour = runway.get("per_hour")
    if free == 0:
        cls, said = "bad", ("No free Gmail: no phone of any lane can be built "
                            "until Gmails are added to the pool.")
    elif hours is not None and float(hours) < 3:
        cls, said = "warn", (f"The free Gmails last about {_num(hours)} h at "
                             f"the pace of the last six hours.")
    else:
        cls, said = "", ("Enough Gmails for now." if hours is None else
                         f"The free Gmails last about {_num(hours)} h at the "
                         f"pace of the last six hours.")
    return (f'<div class="panel {cls}"><h3>Gmails <span class="n">{free} free'
            f'</span></h3><p class="dim">{esc(said)} Builds spent about '
            f'{_num(per_hour)} an hour over the last six hours; every lane '
            f'builds from the one pool.</p></div>')


def _idle_panel(data: dict, tz: str) -> str:
    rows = data.get("idle") or []
    knobs = data.get("knobs") or {}
    stale_s = float(knobs.get("stale_hours") or 6) * 3600.0
    if not rows:
        return ('<div class="panel"><h3>On the shelves</h3>'
                '<p class="dim">Nothing is waiting on a shelf.</p></div>')
    lines = "".join(
        f'<tr class="{"stale" if float(r.get("idle_s") or 0) >= stale_s else ""}">'
        f'<td>{esc(str(r.get("serial") or ""))}</td>'
        f'<td>{WORDS.get(str(r.get("lane") or ""), "")}</td>'
        f'<td>{_span(r.get("idle_s"))}</td></tr>' for r in rows)
    return (f'<div class="panel"><h3>On the shelves <span class="n">{len(rows)}'
            f'</span></h3><p class="dim">The longest waiting first. Amber: '
            f'on the shelf longer than {int(knobs.get("stale_hours") or 6)} h.'
            f'</p><table><thead><tr><th>Phone</th><th>Lane</th><th>Waiting'
            f'</th></tr></thead><tbody>{lines}</tbody></table></div>')


def _knobs_panel(knobs: dict, user: dict) -> str:
    mode = knobs.get("mode") or "watch"
    risk = int(knobs.get("risk_pct") or 5)
    low = knobs.get("min") or {}
    high = knobs.get("max") or {}

    def number(name: str, value, lo: int, hi: int) -> str:
        return (f'<input type="number" name="{name}" value="{int(value)}" '
                f'min="{lo}" max="{hi}" step="1" required>')

    risks = "".join(f'<option value="{r}"{" selected" if r == risk else ""}>'
                    f'{r}%</option>' for r in (1, 2, 5, 10, 20))
    lanes = "".join(
        f'<label>{WORDS[lane]} at least{number("min_" + lane, low.get(lane, 1), 0, 20)}'
        f'</label><label>{WORDS[lane]} at most'
        f'{number("max_" + lane, high.get(lane, 8), 1, 30)}</label>'
        for lane in ("gpt", "spotify"))
    changed = ""
    if knobs.get("updated_by"):
        changed = (f'<p class="dim">Last changed by '
                   f'{esc(str(knobs.get("updated_by")))} at '
                   f'{esc(str(knobs.get("updated_at") or ""))}.</p>')
    return (
        '<div class="panel"><h3>Settings</h3>'
        '<p class="dim">The chance of a wait is how often an operator may find '
        'the shelf empty: lower keeps more phones ready, higher keeps fewer. '
        'The minimum is kept even when nobody is at work; the maximum caps a '
        'lane however busy it is.</p>'
        '<form method="post" action="/stock" class="stockform">'
        + _csrf(user)
        + '<div class="modes"><label><input type="radio" name="mode" '
          f'value="watch"{" checked" if mode != "auto" else ""}> Watch only '
          '(build to WARM_STOCK)</label><label><input type="radio" name="mode" '
          f'value="auto"{" checked" if mode == "auto" else ""}> Steer the stock'
          '</label></div>'
        + f'<label>Chance of a wait<select name="risk_pct">{risks}</select></label>'
        + lanes
        + '<label>Stale after (hours)'
        + number("stale_hours", knobs.get("stale_hours") or 6, 1, 72)
        + '</label><div><button class="go">Save</button></div></form>'
        + changed + '</div>')
