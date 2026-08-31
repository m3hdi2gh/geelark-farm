"""HTML, rendered by hand, escaped by rule.

Server-rendered strings and nothing else: no template engine (a dependency
plus an injection surface), no JavaScript (nothing here needs it yet), no
static files (no path handling, no traversal to get wrong). Every dynamic
value goes through `esc` - the mirror carries text typed into a spreadsheet
by people, and a Note cell is exactly where a `<script>` would sit.

The chrome is Persian and RTL because that is the language the operators
read; the values (statuses, addresses, serials) stay as the sheet holds
them, so the page and the tab never disagree about a word.
"""

from __future__ import annotations

from html import escape as esc

_PAGE = """<!doctype html>
<html dir="rtl" lang="fa"><head><meta charset="utf-8">
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
th,td{{text-align:right;padding:.45rem .6rem;border-bottom:1px solid #e3e8ee}}
th{{background:#eef2f6;font-weight:600}}
.tiles{{display:flex;gap:.8rem;flex-wrap:wrap;margin:0 0 1.2rem}}
.tile{{background:#fff;border:1px solid #e3e8ee;border-radius:6px;
 padding:.7rem 1.1rem;min-width:7rem}}
.tile b{{display:block;font-size:1.5rem}}
.err{{background:#fdecea;color:#8c3220;padding:.6rem .9rem;border-radius:4px;
 margin:0 0 1rem}}
.muted{{color:#6b7b90}}
button{{cursor:pointer}}
</style></head><body>{header}<main>{body}</main></body></html>"""


def page(title: str, body: str, *, user: dict | None = None) -> str:
    header = ""
    if user is not None:
        links = ['<b>geelark</b>', '<a href="/">داشبورد</a>',
                 '<a href="/phones">گوشی‌ها</a>']
        if user.get("sees") == "all":
            links += ['<a href="/pools">استخرها</a>',
                      '<a href="/events">رویدادها</a>']
        links += [f'<form method="post" action="/logout">'
                  f'<span class="muted">{esc(user["username"])}</span> '
                  f'<button>خروج</button></form>']
        header = "<header>" + "".join(links) + "</header>"
    return _PAGE.format(title=esc(title), header=header, body=body)


def login(error: str = "") -> str:
    body = f'<p class="err">{esc(error)}</p>' if error else ""
    body += ('<h2>ورود</h2><form method="post" action="/login">'
             '<p><input name="username" placeholder="نام کاربری" autofocus>'
             '</p><p><input name="password" type="password" '
             'placeholder="رمز عبور"></p>'
             '<p><button>ورود</button></p></form>')
    return page("ورود", body)


def dashboard(snap: dict, user: dict) -> str:
    tiles = []
    labels = (("ready", "آمادهٔ تحویل"), ("app_only", "فقط اپ"),
              ("building", "در حال ساخت"), ("incomplete", "ناقص"))
    for key, label in labels:
        tiles.append(f'<div class="tile">{label}'
                     f'<b>{snap["phones"].get(key, 0)}</b></div>')
    for kind, label in (("gmail", "جیمیل آزاد"), ("proxy", "پروکسی آزاد"),
                        ("app", "اکانت آزاد")):
        stock = snap["stock"].get(kind, {})
        extra = (f' <span class="muted">({stock.get("unusable", 0)} '
                 f'خراب)</span>' if stock.get("unusable") else "")
        tiles.append(f'<div class="tile">{label}'
                     f'<b>{stock.get("free", 0)}</b>{extra}</div>')
    body = f'<div class="tiles">{"".join(tiles)}</div>'
    last = snap.get("last_event")
    if last:
        body += (f'<p class="muted">آخرین رویداد: {esc(str(last["kind"]))} '
                 f'— {esc(str(last["at"]))}</p>')
    body += ('<p class="muted">این صفحه از آینهٔ دیتابیس می‌خواند که هر '
             'پاس (~۳۰ ثانیه) تازه می‌شود؛ شیت همچنان مرجع است.</p>')
    return page("داشبورد", body, user=user)


_APP_MARK = {True: "✓", False: "✗", None: "؟"}


def phones_page(rows: list[dict], user: dict) -> str:
    head = ("<tr><th>سریال</th><th>وضعیت</th><th>State</th><th>اپ</th>"
            "<th>جیمیل</th><th>اکانت</th><th>پروکسی</th><th>یادداشت</th></tr>")
    lines = []
    for r in rows:
        lines.append(
            "<tr>"
            f"<td>{esc(str(r['serial']))}</td>"
            f"<td>{esc(str(r['status']))}</td>"
            f"<td>{esc(str(r['state']))}</td>"
            f"<td>{_APP_MARK.get(r['app_installed'], '؟')}</td>"
            f"<td>{esc(str(r['gmail'] or ''))}</td>"
            f"<td>{esc(str(r['app_account'] or ''))}</td>"
            f"<td>{esc(str(r['proxy_name'] or ''))}</td>"
            f"<td>{esc(str(r['note'] or ''))}</td></tr>")
    body = (f"<h2>گوشی‌ها ({len(rows)})</h2>"
            f"<table>{head}{''.join(lines)}</table>")
    return page("گوشی‌ها", body, user=user)


def pools_page(data: dict, user: dict) -> str:
    body = "<h2>استخرها</h2><table><tr><th>تب</th><th>وضعیت</th><th>تعداد</th></tr>"
    for r in data["counts"]:
        body += (f"<tr><td>{esc(r['kind'])}</td>"
                 f"<td>{esc(str(r['status']))}</td><td>{r['c']}</td></tr>")
    body += "</table>"
    if data["broken"]:
        body += ("<h3>ردیف‌های غیرقابل‌استفاده</h3>"
                 "<table><tr><th>تب</th><th>کدام</th><th>چرا</th></tr>")
        for r in data["broken"]:
            body += (f"<tr><td>{esc(r['kind'])}</td>"
                     f"<td>{esc(str(r['who']))}</td>"
                     f"<td>{esc(str(r['error']))}</td></tr>")
        body += "</table>"
    return page("استخرها", body, user=user)


def events_page(rows: list[dict], user: dict) -> str:
    head = ("<tr><th>کی</th><th>چه</th><th>اجرا</th><th>سریال</th>"
            "<th>وضعیت</th><th>جزئیات</th></tr>")
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
    body = f"<h2>رویدادها</h2><table>{head}{''.join(lines)}</table>"
    return page("رویدادها", body, user=user)


def forbidden(user: dict) -> str:
    return page("دسترسی نیست",
                "<h2>این صفحه در دامنهٔ دید شما نیست</h2>", user=user)
