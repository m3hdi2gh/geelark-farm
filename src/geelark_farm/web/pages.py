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

# The only thing this module takes from the rest of the package, and it
# takes judgements rather than data: when a number is worth a colour is
# the same question whether it is being drawn at the foot of a page or
# raised as an alert, and two copies of an answer drift.
from . import assets
from .read import (PLAN_WARN_DAYS, READING_STALE_AFTER,
                   REFUSAL_SHOWN_FOR, SLOTS_LOW)

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
{assets}{refresh}</head><body><div class="shell">{header}
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
         refresh: int = 0, here: str = "", live: str = "",
         script: bool = True, bare: bool = False) -> str:
    """`refresh` seconds of meta-refresh, when a page shows pending state
    that the next serve pass will change; zero (the default) means none.
    `here` is the rail entry to light. Without a user there is no rail:
    the page stands alone, centred - the sign-in card.

    `live` is which stream the page listens on, and it is off unless the
    page asks: "farm" for what the dashboard draws, "logs" for a page
    that also draws the log lines. It used to default to "farm", so a
    page redrew itself under whoever was using it unless somebody had
    remembered to say otherwise - and three pages had, each after
    somebody hit the problem on that page alone. A page that moves on
    its own should be a page that says so (the operator, 2026-09-20:
    "the edit page popped out several times").

    `script` is the console's behaviour - forms by fetch, the manager,
    the editor, the toast - and is on for every signed-in page. It is
    not the same question as `live`: the two were one flag, so turning
    off a page's self-redraw also took its buttons' manners with it.
    `bare` is the viewer tab: no rail and no alert strip, the frame owns
    the window."""
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

    # The assets\' own hash when there is no commit to name - which on
    # the server is always, because the image has no `.git` in it. It
    # was the empty string there, so `mine.content !== theirs.content`
    # was `"" !== ""` and the reload-on-deploy this comment describes
    # has never once fired (2026-09-20).
    tag += f'<meta name="gf-rev" content="{esc(revision() or assets.REV)}">'
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
    if user is not None and script and _DASH_SCRIPT not in body:
        # `defer`, in the head, fetched once and cached under its own
        # hash: 76KB of unchanging script rode on every response, and
        # the browser re-parsed it every time the page was swapped.
        where = f'<script src="{assets.JS_PATH}" defer></script>'
    else:
        where = ""
    # And the stream, only where the page is worth moving on its own.
    if user is not None and live:
        tag += f'<meta name="gf-live" content="{esc(live)}">'
    tag += where
    # `.wide` is what widens the page. This class only says whether the
    # body is one card floating in the middle - the sign-in - or a page
    # that starts at the top and stays there.
    return _PAGE.format(title=esc(title), header=header, body=body,
                        favicon=_FAVICON, refresh=tag,
                        assets=f'<link rel="stylesheet" '
                               f'href="{assets.CSS_PATH}">',
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


#: What a queued command is called on the row it is about, while it
#: waits: the verb's own word, present tense, since the press has been
#: taken and not yet carried out.
_PENDING_WORDS = {
    "boot_phone": "Booting", "change_proxy": "Changing IP",
    "power_off_phone": "Switching off", "set_phone_state": "Closing",
    "login_accounts": "Signing in", "test_proxy": "Testing",
    "test_all_proxies": "Testing", "mark_proxy_free": "Freeing",
    "free_all_proxies": "Freeing", "remove_proxy": "Removing",
    "free_gmail": "Freeing", "edit_gmail": "Saving",
    "remove_gmail": "Removing", "remove_app": "Removing",
    "free_app": "Freeing", "offer_again": "Offering",
    "refund_gmail": "Marking", "stop_phone": "Stopping",
    "adopt_proxy": "Adopting", "ignore_proxy": "Ignoring",
    "add_proxies": "Adding", "add_gmails": "Adding",
}


def _pending_word(verb: str) -> str:
    return _PENDING_WORDS.get(verb, verb.replace("_", " ").capitalize())


def _pending_door(verb: str) -> str:
    """The doors of a row a command is already on its way to: one, shown
    pressed, saying which. The row used to offer the same door again
    over a press the lane had not yet carried out, and it was pressed
    again (2026-09-21). Not a form, so nothing here can be sent."""
    return (f'<button class="quiet" disabled title="{esc(verb)} is queued '
            f'for this row and the lane carries it out in a moment; the '
            f'row is redrawn when it has">{esc(_pending_word(verb))}'
            f'&hellip;</button>')


def _cancel_form(user: dict, serial: str, back: str = "/",
                 asked: bool = False) -> str:
    """"Cancel": the build on this phone gives up at its next step and puts
    back what it held - the Gmail, the exit. The same door "Stop this one"
    on Requests always was; here it sits on the row it is about.

    `asked` is a press that has landed and not yet been honoured: the
    door is shown pressed rather than offered again, so a second press
    is not what the wait tempts anybody into (2026-09-21)."""
    if not _may(user, "may_login_accounts"):
        return ""
    if asked:
        return ('<button class="quiet" disabled title="Cancel was pressed; '
                'the build gives up at its next step and puts back what it '
                'held">Stopping&hellip;</button>')
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


def _state_forms(user: dict, row: dict, back: str = "/", *,
                 release: bool = True) -> list[str]:
    """The phone-state buttons a taken row offers: Release, Done and
    Failed. Empty for a phone nobody holds - Boot is how one is taken,
    since the Live tab's closing became how it is released and Take was
    the one door left with no way back (the operator, 2026-09-16) - and
    while it is being built, and for someone who may not.

    A phone on its maker's shelf is theirs too, so it offers the same
    three. `release` is what the table leaves out: giving a hand-built
    phone back to the farm is rare and would be a fourth button on a row
    that already scrolls, so it lives on the phone's own page - the same
    place the rest of the rare doors do (2026-09-18).
    """
    if (row.get("status") or "") == "building" or \
            not _may(user, "may_take_phones") or not _holder(row):
        return []
    serial = str(row.get("serial") or "")
    forms = []
    if release:
        forms.append(_state_form(user, serial, "unused", back))
    return forms + [_state_form(user, serial, "done", back),
                    _state_form(user, serial, "failed", back)]


def _holder(row: dict) -> str:
    """Whose phone this is - the name of whoever it belongs to, or "" for
    one that belongs to the farm.

    Two ways a phone is somebody's, and the difference matters to the
    words on the row, not to who may act on it. It is in their hands -
    `taken`, which is what Boot does - or it is on their shelf: a phone
    asked for on the build card is put back the moment its build ends,
    off and bootable, and stays its maker's until they release it (the
    operator, 2026-09-18). `_kept_for` tells the two apart; everything
    that guards a door asks this one.
    """
    state = str(row.get("state") or "")
    if state in ("done", "failed"):
        return ""
    if state == "taken":
        return str(row.get("owner") or "") or "somebody"
    return str(row.get("owner") or "")


def _kept_for(row: dict) -> str:
    """The name of whoever a phone on the shelf is being kept for - "" for
    one in somebody's hands, and for one that is the farm's.

    A hand-built phone is not held: it is off, on the shelf, and its
    maker is the only person who may boot it. The row says so where a
    taken row says "with ali".
    """
    if str(row.get("state") or "") == "taken":
        return ""
    return _holder(row)


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

    Boot is not one of the three and never was: taking a phone over is
    nobody's, an admin's included, and `_boot_form` asks `_holder`
    rather than this (2026-09-15).
    """
    holder = _holder(row)
    if not holder or holder == str(user.get("username") or ""):
        return ""
    if user.get("role") == "admin":
        return ""
    return holder


def _whose_word(row: dict, name: str) -> str:
    """What the actions cell says on somebody else's phone: "with ali"
    for one in their hands, "built for ali" for one on their shelf."""
    return (f"built for {name}" if _kept_for(row) else f"with {name}")


def _row_actions(user: dict, row: dict, back: str = "/") -> str:
    """What one phone offers from the table.

    A phone on the shelf offers the one thing anybody does with it -
    Boot - and Change IP beside it. Once it is out with somebody the row
    turns into the three ways that ends: Release, Done, Failed. Closing
    a phone nobody took is rarer and lives on the phone's own page, so
    the table stays two buttons wide. A phone being built offers
    nothing: a run is holding it.

    Boot is one of the two ways a phone becomes taken - it starts it and
    takes it in one press - so it belongs to a phone nobody is holding,
    and goes as soon as somebody is. Offering it on a taken row is
    offering to take a phone that is already taken, which is the row it
    is already on. Its other half, opening the screen again, is on the
    phone's own page, one click away on the serial, and that page keeps
    Boot for as long as the phone is alive.

    A phone on its maker's shelf is the case that has both: nobody is
    holding it, so Boot is here and is theirs alone, and it is still
    theirs, so Done and Failed are here too (the operator, 2026-09-18).
    Release and Change IP stay off it - four buttons is one more than
    this column fits, and both are on the phone's own page.
    """
    building = (row.get("status") or "") == "building"
    serial = str(row.get("serial") or "")
    taken = (row.get("state") or "") == "taken"
    kept = bool(_kept_for(row))
    if (row.get("state") or "") in ("done", "failed"):
        # Decided: nothing more is done to a phone that is leaving.
        return '<span class="age">leaving</span>'
    held_by = _theirs(user, row)
    if held_by:
        # Somebody else's. The three ways a phone comes back belong to the
        # person holding it; offering them here is offering to act on a
        # phone that is not yours (the contract, 2026-09-05).
        return f'<span class="age">{esc(_whose_word(row, held_by))}</span>'
    actions = []
    if not building and _may(user, "may_take_phones"):
        # Not on a phone GeeLark already has on: Boot would start what
        # is started, and bill it again (2026-09-08). Such a phone - on
        # with nobody here holding it - is the keeper's to switch off
        # (forgotten.sweep), and the row offers nothing until it does.
        # An admin reading somebody else's shelved phone gets neither:
        # `_theirs` lets them end a hold, `_holder` says whose Boot it
        # is, and this row is not theirs to start.
        mine = _holder(row) in ("", str(user.get("username") or ""))
        if not taken and mine and not row.get("running"):
            actions.append(_boot_form(user, serial))
        actions += _state_forms(user, row, back, release=not kept)
    if _may(user, "may_change_proxy") and not building and not taken \
            and not kept:
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
        # person can take: nobody's, not still being built, and not a
        # phone that stopped halfway - that one is a row to look at, not
        # one to hand over. A phone kept on somebody's shelf is theirs,
        # not free: it is the one row where nobody holds it and it is
        # still not the farm's (2026-09-18).
        whose = _holder(r)
        view = ("mine" if whose and whose == me else
                "theirs" if whose else
                "free" if status in ("ready", "app_only") else status)
        if status == "building":
            # Two things to do to a build under way: watch it, and call
            # it off - the job gives up at its next step and puts back
            # what it held (the operator, 2026-09-05). The link is the
            # one GeeLark answered the start with, off the builder's own
            # log line (the operator, 2026-09-10).
            url = str((data.get("live") or {}).get(serial) or "")
            # A press that has landed says so on the row, in the place
            # the word Building was - not only in a toast that is gone
            # in four seconds.
            stopping = serial in (data.get("stops_asked") or ())
            if stopping:
                badge = ('<span class="badge manual" title="Cancel was '
                         'pressed; the build gives up at its next step and '
                         'puts back what it held">Stopping</span>')
            watch = (f'<a class="btn quiet live" target="_blank" '
                     f'rel="noopener" href="{esc(url)}" title="its '
                     f'screen, in a new tab">Watch live</a> '
                     if url else "")
            lines.append(
                f'<tr data-view="{view}"><td>{_serial_link(serial)}</td>'
                f'<td>{badge}</td>'
                f'<td colspan="4" class="progress">'
                f'{_progress(progress.get(serial))}</td>'
                f'<td class="act">{watch}'
                f'{_cancel_form(user, serial, asked=stopping)}</td>'
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
        # A command on its way to this phone: its doors become that one
        # word until the lane has carried it out.
        waiting = str((data.get("pending") or {}).get(serial) or "")
        maker = str(r.get("built_by") or "")
        kept = _kept_for(r)
        if kept:
            # Off, on the shelf, and still its maker's: the one line that
            # says both, since the status pill says only "Ready" and the
            # buttons are the same three a taken row shows (2026-09-18).
            badge += (f'<span class="dim maker" title="on the shelf and '
                      f'switched off - only {esc(kept)} can boot it">kept '
                      f'for {esc("you" if kept == me else kept)}</span>')
        elif maker:
            badge += (f'<span class="dim maker" title="asked for on the '
                      f'build card">built by {esc(maker)}</span>')
        lines.append(
            f'<tr data-view="{view}"><td>{_serial_link(serial)}</td>'
            f'<td>{badge}</td>'
            f'<td>{_addr_cell(r.get("gmail"), "no Gmail on it")}'
            f'{_seller_line(r)}</td>'
            f'<td>{_account_cell(r)}</td>'
            f'<td class="mono dim">{esc(str(r.get("proxy_name") or "-"))}</td>'
            f'<td class="mono dim nowrap">'
            f'{_ago(r.get("created_at") or r.get("updated_at")) or "-"}</td>'
            f'<td class="act">'
            f'{_pending_door(waiting) if waiting else _row_actions(user, r)}'
            f'</td></tr>')
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
    product = str(row.get("app_product") or "").strip().lower()
    if product != "spotify":
        if product == "claude":
            # It said GPT on every phone that was not a Spotify one, so
            # a Claude phone was labelled as the wrong product on the
            # one screen an operator hands a phone over from
            # (2026-09-19).
            return ('<span class="carries claude" title="a Claude '
                    'account">Claude</span>')
        if str(row.get("app_category") or "").strip().lower() == "eco":
            return ('<span class="carries eco" title="an eco ChatGPT '
                    'account - no password, a code is emailed to it">'
                    'GPT eco</span>')
        return '<span class="carries" title="a ChatGPT account">GPT</span>'
    word = str(row.get("app_category") or "").strip().lower()
    if word not in ("normal", "error"):
        return '<span class="carries" title="a Spotify account">Spotify</span>'
    rule = dict(SPOTIFY_CATEGORIES).get(word, "")
    return (f'<span class="carries {word}" title="a Spotify account of the '
            f'{word} kind - it {esc(rule)}">Spotify {word}</span>')


def _seller_line(row: dict) -> str:
    """The seller the phone's Gmail came from, in small under the
    address - the same line the maker's name takes under the status.
    Nothing when there is no Gmail, or the pool never knew whose it
    was (the operator, 2026-09-22)."""
    seller = str(row.get("gmail_seller") or "").strip()
    if not seller or _no_address(str(row.get("gmail") or "").strip()):
        return ""
    return (f'<span class="dim maker seller" title="the seller this Gmail '
            f'came from">{esc(seller)}</span>')


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
#: Kept as the whole tag, because everything that reads it reads it as
#: source: thirty-eight tests assert substrings of it. It is built from
#: the file now rather than written here - see `assets`.
_DASH_SCRIPT = f"""
<script>
{assets.JS}</script>"""

#: The script itself is `web/static/dash.js`; it was 1,400 lines of
#: JavaScript inside this file until 2026-09-20.


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
                "line, tabs or commas between. An eco paste is addresses "
                "and nothing else"),
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


#: The kinds each pool sifts by: the word on the chip, and the word the
#: row's category cell holds. They are the same for Spotify. A standard
#: GPT row has no kind written on it - it is what this pool has always
#: held - so its chip needs a word of its own: the empty string is
#: already taken by `both` (the operator, 2026-09-19).
_SIFT_KINDS = {"spotify": ("normal", "error"),
               "gpt": ("standard", "eco")}

#: The one chip whose word is not what its rows carry in the cell.
_SIFT_CELL = {"standard": ""}


def _sift_word(kind: str, row: dict) -> str:
    """Which of a pool's kind chips a row belongs under, or "" for a row
    whose cell holds a word this pool does not sift by - which shows
    under `both` and under neither chip, rather than being filed wrong.
    """
    cell = str(row.get("category") or "").strip().lower()
    for word in _SIFT_KINDS.get(kind, ()):
        if _SIFT_CELL.get(word, word) == cell:
            return word
    return ""


def _kind_chips(kind: str, rows: list[dict]) -> str:
    """A pool's second row of chips: which kind of account.

    The status chips answer "can this still be used". These answer the
    other question, which for a pool with kinds is the first one: which
    kind is it. Spotify's two are two stocks that go on two kinds of
    phone; the GPT pool's two sign in two different ways. Either way,
    one list with both in it is a list you read twice (the operator,
    2026-09-17, and again for the GPT pool on 2026-09-19). They sift
    together with the status chips, so `eco / current` is one press
    away from `eco / spent`.
    """
    words = list(_SIFT_KINDS.get(kind, ()))
    if not words:
        return ""
    counts = {word: sum(1 for r in rows if _sift_word(kind, r) == word)
              for word in words}
    chips = [("", "both", len(rows))]
    chips += [(word, word, counts[word]) for word in words]
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
    """The row's kind, for the chips above to sift on."""
    if kind not in _SIFT_KINDS:
        return ""
    return f' data-cat="{esc(_sift_word(kind, row))}"'


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


def _spotify_send_form(user: dict, row: dict, back: str = "/") -> str:
    """Send, for a Spotify row - which door depends on its kind.

    An `error` account wants a phone that has a Gmail and nothing signed
    in, which is the warm phone the chooser already offers: the GPT
    pool's own Send. A `normal` account wants a phone with no Google
    account, and none is kept warm - the press asks for one to be built,
    bare, with the account signed in as the build's last step
    (2026-09-17). A row of neither kind gets no door: nothing knows which
    phone it may go on.
    """
    address = str(row.get("address") or "")
    category = str(row.get("category") or "").strip().lower()
    if category == "error":
        return _send_form(user, address, back)
    if category != "normal":
        return ""
    # `+`, not `→`: this press makes a phone that does not exist yet, and
    # the difference from the other kind's door is the whole point. One
    # word, because the row has an address to show (2026-09-18).
    return (f'<form method="post" class="inline" '
            f'action="/accounts/spotify/build">{_csrf(user)}'
            f'<input type="hidden" name="address" value="{esc(address)}">'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<button class="quiet send" data-busy="Asking&hellip;" '
            f'title="build a bare phone - no Google account - and sign '
            f'this account into Spotify on it">+ phone</button>'
            f'</form>')


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
    send = kind in ("gpt", "spotify") and _may_send(user, manual_login)
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
        # The Spotify kind as its mark alone, and a short door beside it:
        # the pill and the words took the row and left the address in an
        # ellipsis (the operator, 2026-09-18).
        if kind == "spotify":
            aside = _category_dot(tag) + (
                _spotify_send_form(user, row) if send else "")
        else:
            aside = (_send_form(user, label) if send
                     else f'<span class="tag">{esc(tag)}</span>')
        items.append(f'<li><span class="t" title="{esc(label)}">'
                     f'{_split_address(label)}</span>{aside}</li>')
    return f'<ul class="queue">{"".join(items)}</ul>'


def _split_address(label: str) -> str:
    """An address as two pieces, so the half that identifies it survives
    a narrow card: the name whole, the domain clipped after it. Anything
    that is not an address - an exit is called `US25` - is itself."""
    name, at, domain = str(label).partition("@")
    if not at:
        return f"<b>{esc(label)}</b>"
    return f"<b>{esc(name)}</b><i>@{esc(domain)}</i>"


def _category_dot(category: str) -> str:
    """A Spotify kind with no word: the same circle and triangle the
    pills wear, in the same two colours, hovering its own rule."""
    word = str(category or "").strip().lower()
    if word not in ("normal", "error"):
        return ""
    rule = dict(SPOTIFY_CATEGORIES).get(word, "")
    return (f'<span class="cat dot {word}" '
            f'title="{esc(word)} - it {esc(rule)}"></span>')


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
    return (_cards(("gpt", "spotify"), data, user, manual_login, alerts)
            + _claude_card())


def _claude_card() -> str:
    """The Claude pool's card, shut.

    It stands under Spotify so the rail already reads as the three
    products it is going to hold, but it is dimmed and has no door: the
    Claude accounts will come in from the bot, not from a paste, and a
    Manage button that opened an empty sheet would promise a way in that
    does not exist yet (the operator, 2026-09-17). Not in `_POOL_KINDS`
    on purpose - a kind there gets a sheet, a paste box and a route.
    """
    return ('<section class="pool off" aria-disabled="true" '
            'title="not open yet - the Claude accounts will arrive from '
            'the bot">'
            '<header><b>&ndash;</b>'
            '<span class="t">Claude accounts<i>not open yet</i></span>'
            '<span class="lock">soon</span></header>'
            '<p class="railnote">The accounts will arrive from the bot; '
            'there is nothing to add here by hand.</p>'
            '</section>')


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
            # A browser puts back what a form held when the page is
            # reloaded, so a paste that had already gone into the
            # pool was still sitting in the box afterwards (the
            # operator, 2026-09-18). The box is the server's to fill.
            f'autocomplete="off" '
            f'spellcheck="false" placeholder="{esc(meta["how"])}"></textarea>'
            f'<div class="addrow">{_seller_field(kind, rows or [])}'
            f'<button class="go">Preview</button>'
            f'<span class="dim">{_add_note(kind)}</span></div></form>')


def _add_note(kind: str) -> str:
    """The line beside Preview. A pool with kinds says the one thing a
    paste can get wrong that the box cannot catch: a line does not say
    which kind it is, so the whole paste is one kind."""
    seen = "nothing is written until you have seen what it read"
    return (f"one kind per paste &mdash; {seen}" if kind in _KIND_TILES
            else seen)


#: What each Spotify category means, in the words beside the box. The
#: seller's words are kept - the operators know them - and the rule they
#: stand for is written out wherever they appear (the operator,
#: 2026-09-17).
SPOTIFY_CATEGORIES = (("normal", "goes on a phone with no Gmail"),
                      ("error", "goes on a phone that has a Gmail"))

#: The GPT pool's two kinds, in the same shape plus the word on the
#: tile. The ordinary kind has no word on the row - it is what this pool
#: has always held - so its value is empty and the tile says what it is
#: (the operator, 2026-09-19).
GPT_CATEGORIES = (("", "address, password and a 2fa key", "standard"),
                  ("eco", "an address only - a code is emailed to it", ""))

#: Which pools offer kinds, and the tiles each draws: value, rule,
#: label. An empty label means the value is the word on the tile, which
#: is every Spotify tile and the eco one.
_KIND_TILES = {
    "spotify": tuple((value, rule, "") for value, rule in SPOTIFY_CATEGORIES),
    "gpt": GPT_CATEGORIES,
}


def _category_field(kind: str) -> str:
    """Which kind of account this paste is. One kind per paste: a line
    cannot say which it is, and a box that guesses would put an account
    on a phone it cannot work on - or throw a password away.

    Tiles, not a dropdown. The kind is the one thing about these pools a
    person has to get right, and an option list hid both the choice and
    the rule behind it until it was opened - beside a label, a hint and
    a box, which made the pool look nothing like the other three (the
    operator, 2026-09-17). Each tile wears the mark its rows wear in the
    table, and says out loud what that kind is.
    """
    tiles = []
    for index, (value, rule, label) in enumerate(_KIND_TILES.get(kind, ())):
        tag, label = value or "plain", label or value
        tiles.append(
            f'<input type="radio" name="category" value="{value}" '
            f'id="cat-{kind}-{tag}"{" checked" if not index else ""}>'
            f'<label class="{tag}" for="cat-{kind}-{tag}">'
            f'<b>{esc(label)}</b><i>{esc(rule)}</i></label>')
    if not tiles:
        return ""
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
    # Only a row that is actually free. It was every row but one on a
    # phone, so a delivered account and one a run had set aside both
    # offered Send - and the verb refuses both, because neither is
    # claimable. A door that leads nowhere is worse than no door
    # (2026-09-18); Free is what a set-aside row is offered, below.
    if kind in ("gpt", "spotify") and state == "free" \
            and _may_send(user, manual_login):
        doors.append(_send_form(user, address) if kind == "gpt"
                     else _spotify_send_form(user, row))
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
                f'<button class="quiet bad" data-busy="Removing&hellip;">'
                f'Remove</button></form>')
        return f'<div class="doors">{"".join(doors)}</div>'
    # A row a run set aside gets Free: one press, back on the shelf, and
    # nothing else on the row touched (the operator, 2026-09-06).
    if meta.get("free") and state not in ("free", "on a phone"):
        doors.append(
            f'<form method="post" action="{meta["free"]}">{_csrf(user)}'
            f'<input type="hidden" name="{field}" value="{esc(address)}">'
            f'<input type="hidden" name="back" value="/">'
            f'<button class="quiet ok" data-busy="Freeing&hellip;" '
            f'title="back on the shelf, as it is">Free</button></form>')
    if meta["edit"]:
        doors.append(
            f'<button type="button" class="quiet" data-edit="{esc(address)}"'
            f' data-pool="{kind}">Edit</button>')
    if meta["remove"]:
        doors.append(
            f'<form method="post" action="{meta["remove"]}">{_csrf(user)}'
            f'<input type="hidden" name="{field}" value="{esc(address)}">'
            f'<input type="hidden" name="back" value="/">'
            f'<button class="quiet bad" data-busy="Removing&hellip;">'
            f'Remove</button></form>')
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
          # Where a refusal lands. It went to the page behind the
          # dialog, which with the manager open is under a backdrop, so
          # a Save the verb turned down closed the editor and said why
          # somewhere nobody could see (the operator, 2026-09-20).
          '<p class="editsay" hidden></p>'
          '<div class="row"><button type="button" class="quiet" '
          'data-close-edit="1">Cancel</button>'
          '<button class="go" data-busy="Saving&hellip;">Save</button></div>'
          '</form></dialog>')


def _pool_table_rows(kind: str, rows: list[dict], user: dict,
                     manual_login: bool = False,
                     pending: dict | None = None) -> str:
    """The `<tr>`s of a pool table, without the table around them.

    Out here so a press about one address can be answered with that one
    row - drawn by the code that draws it in the sheet, so the two
    cannot come to differ (2026-09-21).
    """
    meta = _POOL_KINDS[kind]
    doors = bool(meta["manage"]) and _may(user, meta["manage"])
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
        waiting = str((pending or {}).get(str(row.get("address") or "")) or "")
        last = (f"<td>{_pending_door(waiting) if waiting else _pool_row_doors(kind, row, user, manual_login)}</td>"
                if doors else "")
        # Lower-cased, and only the row's own words - the address, what
        # state it is in, whose it was, which phone has it. The state
        # words are worth keeping: "broken" and "set aside" are exactly
        # what somebody types when they want to see what wants them.
        findable = " ".join(str(c) for c in cells if c).lower()
        state = str(row.get("state") or "")
        # What the editor opens with, on the row itself - the seller. The
        # password and the key used to ride here too, in every row of
        # every sheet fetched, for the one row in a hundred anybody
        # opened; the editor reads those for its one row when Edit is
        # pressed (2026-09-21).
        held = (f' data-sellername="{esc(str(row.get("seller") or "").strip())}"'
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
    return "".join(lines)


def _pool_table(kind: str, rows: list[dict], user: dict,
                manual_login: bool = False,
                pending: dict | None = None) -> str:
    meta = _POOL_KINDS[kind]
    head = "".join(f"<th>{esc(c)}</th>" for c in meta["columns"])
    doors = bool(meta["manage"]) and _may(user, meta["manage"])
    span = len(meta["columns"]) + (1 if doors else 0)
    lines = _pool_table_rows(kind, rows, user, manual_login, pending)
    if not lines:
        return ('<p class="empty">Nothing in this pool that anybody still '
                'has a decision about.</p>')
    return (f'<table class="pooltable"><thead><tr>{head}'
            f'{"<th></th>" if doors else ""}</tr></thead>'
            f'<tbody>{lines}'
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


def row_answer(kind: str, row: dict | None, said: str, user: dict,
               said_note: str = "", manual_login: bool = False,
               pending: dict | None = None) -> str:
    """One row and the banner about it - the whole of what a press on one
    address changes.

    The press used to answer 303 to the dashboard, and the script then
    fetched and DOMParsed a megabyte to lift this `<tr>` out of it. A
    row gone from the pool answers with no row at all, which is what the
    script removes.
    """
    drawn = (_pool_table_rows(kind, [row], user, manual_login, pending)
             if row else "")
    return (f'<div class="rowanswer" data-row-kind="{esc(kind)}">'
            f'{_said(said, _DASH_SAID, user, said_note)}'
            f'<table>{drawn}</table></div>')


#: The dedicated pool pages that answer a press with one of their own
#: rows, and the base their pills link to.
PAGE_ROW_KINDS = {"gmail": "/pools/gmail", "gpt": "/pools/gpt",
                  "proxy": "/pools/proxy"}


def page_row_answer(kind: str, view: str, row: dict | None, said: str,
                    user: dict, here: str, counts: dict, *,
                    said_note: str = "", advice=None, explain=None,
                    tests: dict | None = None,
                    manual_login: bool = False) -> str:
    """One row of a dedicated pool page and the banner about it - and
    the pills, because the press that moved the row moved a count.

    The dashboard's `row_answer` draws the sheet's columns; these pages
    draw their own per view, so a press on them was answered with a
    redirect and a whole page read (2026-09-22, found by audit). A row
    that has left the view - Free on the errored list - answers with no
    row, which the script removes.
    """
    views = {"gmail": GMAIL_VIEWS, "gpt": GPT_VIEWS,
             "proxy": PROXY_VIEWS}[kind]
    if row is None:
        drawn = ""
    elif kind == "gmail":
        drawn = _gmail_view_row(view, row, user, here, advice=advice)
    elif kind == "gpt":
        can_login = manual_login and _may(user, "may_login_accounts")
        drawn = _gpt_view_row(view, row, user, here, explain=explain,
                              can_login=can_login)
    else:
        drawn = _proxy_view_row(view, row, user, here, tests or {})
    return (f'<div class="rowanswer" data-row-kind="{esc(kind)}"'
            f' data-row-view="{esc(view)}">'
            f'{_said(said, _POOL_SAID, user, said_note)}'
            f'{_view_pills(PAGE_ROW_KINDS[kind], views, view, counts)}'
            f'<table>{drawn}</table></div>')


def _pool_sheet(kind: str, rows: list[dict], totals: dict, user: dict,
                manual_login: bool = False,
                pending: dict | None = None) -> str:
    """One pool, as the manager shows it: the paste box, the chips, the
    search, and the table under them.

    Fetched when the drawer is pulled rather than rendered shut inside
    every response - see `_pool_manager`.
    """
    meta = _POOL_KINDS[kind]
    return (
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
        f'{_capped(rows, (totals or {}).get(kind))}'
        f'<div class="tscroll">'
        f'{_pool_table(kind, rows, user, manual_login, pending)}</div>'
        f'</div>{_pool_editor(kind, user, rows)}</section>')


def _pool_manager(data: dict, user: dict,
                  manual_login: bool = False) -> str:
    """The drawer the pool doors open into: a mount, and the two sheets
    that are about phones rather than stock.

    The three pool sheets used to be rendered here, shut, on every
    response - 925,488 of the dashboard's 1,012,694 bytes, 651 rows and
    ~500 passwords and TOTP secrets in `data-*` attributes, for the 99
    responses in 100 where nobody opened them (2026-09-20). Each one is
    now fetched from `/pools/<kind>/sheet` the first time its door is
    pressed, and kept in the DOM after that, so everything the swap does
    with an open sheet (`keepSheet`) is untouched.

    Without the script the doors are still forms that go to the pool's
    own page, which is what they were before the manager existed.
    """
    sheets = []
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


#: What the build card's Account box offers: the value it sends, the
#: word on the option, and whether a bare phone may carry it.
#:
#: The value is `product:category`, which is exactly what the pools
#: already store - `spotify`/`normal`, `chatgpt`/`eco` - so nothing has
#: to translate it on the way in or out. The empty value is "none".
#:
#: One kind is allowed on a bare phone: a `normal` Spotify account
#: wants a phone with *no* Google account on it, which is the only
#: reason to build one and put an account on it at all (2026-09-17).
#: Everything else needs a Gmail beside it - an `error` Spotify account
#: by its own rule, and every GPT account because the app signs in
#: through Google's own machinery.
#: Each row is the value, the word on the option, and the two phones it
#: may go on: a bare one, and one with a Gmail.
#:
#: A Spotify account says which phone it wants and the two answers do
#: not overlap - `normal` wants a phone with *no* Google account on it,
#: `error` wants one that has a Gmail (2026-09-17). So each is offered
#: on exactly one of them. Offered on both, `normal` sat in the list on
#: every ordinary build and the server refused every press.
ACCOUNT_KINDS: tuple[tuple[str, str, bool, bool], ...] = (
    #  value             word                                   bare   gmail
    ("", "none &mdash; sign in later", True, True),
    ("spotify:normal", "Spotify &mdash; normal", True, False),
    ("spotify:error", "Spotify &mdash; error", False, True),
    ("chatgpt:", "ChatGPT &mdash; password and a 2fa key", False, True),
    ("chatgpt:eco", "ChatGPT &mdash; eco, a code is emailed to it",
     False, True),
)

#: The values the card may send, for the route to check what it is given
#: against. A word off the wire becomes a pool row's Category, and an
#: unchecked one strands the account under a category nothing serves.
ACCOUNT_KIND_VALUES = frozenset(value for value, _w, _b, _g in ACCOUNT_KINDS)


def _account_kind_of(row) -> str:
    """The `product:category` a free pool row answers to.

    A row with no product is ChatGPT's: that is what this pool held
    before it held anything else, and the column was added around those
    rows rather than under them.
    """
    product = str(row.get("product") or "").strip().lower() or "chatgpt"
    category = str(row.get("category") or "").strip().lower()
    return f"{product}:{category}"


def _account_kinds(rows) -> dict[str, list[str]]:
    """The free addresses of each kind, for the card's second box."""
    by_kind: dict[str, list[str]] = {}
    for row in rows or []:
        label = str(row.get("label") or "").strip()
        if label:
            by_kind.setdefault(_account_kind_of(row), []).append(label)
    return by_kind


def _account_rows_json(rows) -> str:
    """Those lists as JSON, for the attribute the script reads."""
    import json

    return json.dumps(_account_kinds(rows), separators=(",", ":"))


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
    account_kinds = _account_kinds(choose.get("apps"))
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
    # "Account", not "GPT account": one box for every product, because a
    # field named for one would have to be renamed the day the second
    # arrives (the operator, 2026-09-17). Two boxes now, and the first
    # decides the second: which kind of account, then which account.
    #
    # The kinds on offer follow the Gmail box. A bare phone can carry
    # exactly one kind - a `normal` Spotify account, which wants a phone
    # with no Google account on it - and everything else needs a Gmail
    # to sit beside. The script hides the rest when "none" is chosen
    # rather than this rendering two lists, so the one page serves both
    # answers and the rule is written once, in ACCOUNT_KINDS.
    kind_box = ('<label class="field"><span>Account</span>'
                '<select name="account_kind" id="acctkind">'
                + "".join(
                    f'<option value="{esc(value)}"'
                    + (' data-bare="1"' if bare else '')
                    + (' data-gmail="1"' if gmail else '')
                    + f'>{word}</option>'
                    for value, word, bare, gmail in ACCOUNT_KINDS)
                + '</select></label>')
    # Which account of that kind. Filled by the script from the free
    # rows, which carry their own product and category - so the list is
    # always the rows this kind can actually use.
    account_box = ('<label class="field" id="acctwrap" hidden>'
                   '<span>Which one</span>'
                   '<select name="app_account" data-new="account-new"'
                   f' data-rows="{esc(_account_rows_json(choose.get("apps")))}">'
                   '<option value="__new__">choose&hellip;</option>'
                   '</select></label>')
    return (
        f'<div class="panel"><h3>Build one now</h3>'
        f'<p class="dim" style="margin:-6px 0 0">{hint}</p>'
        f'<form method="post" action="/phones/build" class="byhand">'
        f'{_csrf(user)}'
        + gmail_box + kind_box + account_box
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
        + _new_dialog("account-new", "Choose an account", [
            ("app_address", "Address", ""),
            ("app_password", "Password", ""),
            ("app_secret", "2FA secret", "optional")], rows=apps)
        # The dialog's rows are the free ones of the chosen kind, swapped
        # in by the script; `rows=apps` above is what it starts with.
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


def _geelark_line(data: dict, user: dict) -> str:
    """What GeeLark says about the account, along the foot of the page.

    Outside any card and at the very bottom, because none of it is
    anybody's work: it is the ground the farm stands on, worth a glance
    when something is odd and worth no room at all the rest of the time
    (the operator, 2026-09-20).

    A small grid of captioned readings rather than a run of words.
    Three goes at a sentence failed the same way: what is worth knowing
    does not fit on one line, so the line either wrapped into a ragged
    second row - "ugly, sloppy and cluttered" - or was trimmed until
    the numbers somebody wanted had gone into title attributes nobody
    hovers over. A grid carries more and reads as less, and nothing has
    to be hidden to keep it quiet.

    Wallet values come from a separate cached /v1/pay/wallet reading.
    Cash, gifted money and remaining time are kept distinct; zero cash
    alone does not mean phones cannot run.

    Colour comes from `read.geelark_trouble`, the one judgement the
    alert strip at the top is drawn from as well, so the foot can never
    read calm while the top reads red.
    """
    if not _may(user, "is_admin") and not _may(user, "may_login_accounts"):
        return ""
    found = data.get("geelark") or {}
    plan = found.get("plan") or {}
    if not plan and not found.get("refusal") and not found.get("wallet_reading"):
        return ""
    trouble = found.get("trouble") or []

    cells = [c for c in (_gl_balance(found, trouble),
                         _gl_time_addon(found),
                         # Only there when GeeLark has turned something
                         # down for a reason that is not the money.
                         _gl_blocked(found, trouble),
                         _gl_slots(plan, found, trouble),
                         _gl_running(plan, found),
                         _gl_subscription(plan, trouble)) if c]
    # Anything `read.geelark_trouble` raised that no reading above
    # speaks for gets a cell of its own. The promise this whole
    # arrangement rests on is that the foot says whatever the top says;
    # a kind added there and forgotten here would break it in silence.
    spoken = {k for c in cells for k in c.get("kinds", ())}
    for item in trouble:
        if item.get("kind") in spoken or not item.get("short"):
            continue
        cells.append({"cap": "Attention", "value": str(item["short"]),
                      "kinds": (item.get("kind"),),
                      "tone": str(item.get("level") or ""),
                      "title": str(item.get("text") or "")})

    age, stale = _gl_age(found)
    tones = {c.get("tone") for c in cells} | ({"warn"} if stale else set())
    worst = "bad" if "bad" in tones else "warn" if "warn" in tones else ""

    return (f'<footer class="glfoot{" " + worst if worst else ""}">'
            f'<p class="glhead"><span class="gldot"></span>'
            f'<span class="gltag" title="{esc(_gl_source(plan))}">'
            f'GeeLark</span>'
            f'<span class="glage{" warn" if stale else ""}"'
            f' title="{esc(_gl_freshness(stale))}">{esc(age)}</span></p>'
            f'<dl class="glstats">'
            + "".join(_gl_cell(c) for c in cells) + '</dl></footer>')


def _gl_cell(cell: dict) -> str:
    """One reading: its caption, the reading, and the line under it that
    would otherwise have had to be hovered for."""
    tone = (str(cell.get("tone") or "")
            or ("quiet" if cell.get("quiet") else ""))
    hint = f' title="{esc(cell["title"])}"' if cell.get("title") else ""
    note = (f'<span class="glnote">{esc(cell["note"])}</span>'
            if cell.get("note") else "")
    return (f'<div class="glcell{" " + tone if tone else ""}"{hint}>'
            f'<dt>{esc(cell["cap"])}</dt>'
            f'<dd>{esc(cell["value"])}{note}</dd></div>')


def _gl_balance(found: dict, trouble: list) -> dict:
    """Cash reported by the wallet, never inferred from phone activity."""
    cached = found.get("wallet_reading") or {}
    money = cached.get("wallet") or {}
    if money.get("balance") is not None:
        note, stale = _gl_wallet_age(cached)
        gift = money.get("giftMoney")
        if gift is not None:
            note = f"${float(gift):,.2f} gift credit · {note}"
        return {"cap": "Balance", "value": f"${float(money['balance']):,.2f}",
                "note": note, "tone": "warn" if stale else "",
                "title": "Cash balance. Gift credit and time add-on are separate."}
    for item in trouble:
        if item.get("kind") != "refused":
            continue
        return {"cap": "Balance", "value": "out of credit",
                "tone": str(item.get("level") or "bad"),
                "kinds": ("refused",),
                "note": _gl_brief(item.get("detail")) or "a phone was refused",
                "title": str(item.get("text") or "")}
    return {"cap": "Balance", "value": "unavailable", "quiet": True,
            "note": ("refresh failed; retrying" if cached.get("failed")
                     else "waiting for wallet reading"),
            "tone": "warn" if cached.get("failed") else "",
            "title": "No successful wallet reading yet; this does not mean zero."}


def _gl_wallet_age(cached: dict) -> tuple[str, bool]:
    when = cached.get("at")
    stale = not when or time.time() - float(when) > READING_STALE_AFTER
    age = f"read {_ago(when)}" if when else "reading time unknown"
    if cached.get("failed"):
        return f"last known · {age} · refresh failed", True
    return (f"last known · {age}" if stale else age), stale


def _gl_time_addon(found: dict) -> dict:
    cached = found.get("wallet_reading") or {}
    minutes = (cached.get("wallet") or {}).get("availableTimeAddOn")
    if minutes is None:
        return {}
    note, stale = _gl_wallet_age(cached)
    return {"cap": "Time add-on", "value": f"{int(minutes):,} min",
            "note": note, "tone": "warn" if stale else "",
            "title": "Remaining prepaid cloud-phone time; separate from cash."}


def _gl_blocked(found: dict, trouble: list) -> dict:
    """GeeLark turning a phone down for something that is not the money.

    Every refusal used to be read as an empty account, so the console
    said `out of credit`, in red, on a day forty-seven phones were
    built and the one thing GeeLark had refused was a proxy it could
    not check (the operator, 2026-09-20). It is a reading of its own
    now, and amber rather than red: one build turned down among many is
    worth seeing and is not a stop. It turns red only when the breaker
    is up, which is when nothing is being built at all.

    Absent while nothing has been refused, which is most of the time -
    so the foot is four readings wide unless there is a fifth thing to
    say.
    """
    for item in trouble:
        if item.get("kind") != "blocked":
            continue
        told = str(item.get("detail") or "")
        if item.get("stalled"):
            return {"cap": "Building", "value": "stopped", "tone": "bad",
                    "kinds": ("blocked",), "note": _gl_brief(told),
                    "title": str(item.get("text") or "")}
        when = found.get("refused_at")
        code = item.get("code")
        aside = [f"[{int(code)}]" if code else "", _ago(when) if when else ""]
        return {"cap": "Last refusal", "kinds": ("blocked",),
                "tone": str(item.get("level") or "warn"),
                "value": _gl_brief(item.get("msg") or told, 28)
                         or "turned down",
                "note": " \u00b7 ".join(x for x in aside if x),
                "title": str(item.get("text") or "")}
    return {}


def _gl_slots(plan: dict, found: dict, trouble: list) -> dict:
    """How much of the profile pool is spoken for.

    The pool is shared with browser profiles this API will not list, so
    the note splits it: that is the answer to "why did a create fail
    while the tab looks half empty".
    """
    total = int(plan.get("profiles") or 0)
    if not total:
        return {}
    free = int(plan.get("availableProfiles") or 0)
    used = max(0, total - free)
    ours = int(found.get("phones_total") or 0)
    elsewhere = max(0, used - ours)
    if not used:
        note = "none taken"
    elif elsewhere:
        note = f"{min(ours, used)} ours · {elsewhere} elsewhere"
    else:
        note = "all of them ours"
    return {"cap": "Phone slots", "value": f"{used} / {total}",
            "tone": _gl_level(trouble, "slots"), "kinds": ("slots",),
            "note": note,
            "title": (f"{free} of {total} free. Browser profiles share "
                      f"this pool and the API will not list them; a "
                      f"create past the last slot fails with [44002].")}


def _gl_running(plan: dict, found: dict) -> dict:
    """The phones that are switched on - the only number here that is
    costing money as it is read."""
    running = int(found.get("phones_running") or 0)
    included = int(plan.get("parallels") or 0)
    if not included:
        note = "billed by the minute"
    elif running > included:
        note = f"{included} included · {running - included} billed"
    else:
        note = f"{included} included in the plan"
    return {"cap": "Running now", "quiet": not running,
            "value": str(running) if running else "none",
            "note": note,
            "title": ("Phones switched on right now. Anything past the "
                      "parallels the plan includes is billed by the "
                      "minute, so a phone left on spends whether or not "
                      "anybody is looking at it.")}


def _gl_subscription(plan: dict, trouble: list) -> dict:
    """The day the plan runs out, and what it costs to keep."""
    ends = plan.get("expirationTime")
    if not ends:
        return {}
    when = datetime.datetime.fromtimestamp(int(ends), datetime.timezone.utc)
    days = (when - datetime.datetime.now(datetime.timezone.utc)).days
    left = ("expired" if days < -1 else "expired today" if days < 0
            else "ends today" if days == 0
            else "1 day left" if days == 1 else f"{days} days left")
    fee = int(plan.get("monthlyFee") or 0)
    note = " · ".join(x for x in (left, f"${fee}/mo" if fee else "")
                           if x)
    # Not strftime's `%-d`: that flag is glibc's and the suite runs on
    # Windows too.
    return {"cap": "Subscription", "kinds": ("plan",),
            "value": f"{when.day} {when.strftime('%b %Y')}",
            "tone": _gl_level(trouble, "plan"), "note": note,
            "title": ("When the GeeLark subscription runs out. Every "
                      "phone on the account goes with it.")}


def _gl_age(found: dict) -> tuple[str, bool]:
    """How old the keeper's reading is, and whether that is old enough
    to say so. A stale foot is worse than no foot: the numbers look
    like readings and are memories."""
    when = found.get("at")
    if not when:
        return "not read yet", True
    try:
        old = time.time() - float(when)
    except (TypeError, ValueError):
        return "", False
    return f"read {_ago(when)}", old > READING_STALE_AFTER


def _gl_source(plan: dict) -> str:
    """Where every number above came from, and the caveat under all of
    them - carried on the one word that is always there."""
    return (f"The GeeLark account as the keeper last read it. Plan "
            f"{int(plan.get('plan') or 0)}: "
            f"{int(plan.get('profiles') or 0)} profile slots, "
            f"{int(plan.get('parallels') or 0)} parallel phone(s) "
            f"included, ${int(plan.get('monthlyFee') or 0)} a month. "
            f"The plan comes from /v1/pay/plan/info; the wallet is read "
            f"separately from /v1/pay/wallet, every five minutes.")


def _gl_freshness(stale: bool) -> str:
    return ("The keeper reads the plan every few minutes and this one is "
            "older than that, so it may have stopped - take the readings "
            "above as the last thing known rather than as now."
            if stale else
            "The keeper reads the plan every few minutes and leaves the "
            "answer here. Wallet readings have their own timestamp.")


def _gl_brief(said, limit: int = 46) -> str:
    """GeeLark's own sentence, cut to something that sits under a
    reading. The whole of it is on the alert strip at the top."""
    said = " ".join(str(said or "").split())
    return said if len(said) <= limit else said[:limit - 1].rstrip() + "…"


def _gl_level(trouble: list, kind: str) -> str:
    """The level `read.geelark_trouble` gave this kind of trouble, or ""
    when it raised none about it. The word is the class name, so the
    foot cannot grade something differently from the strip at the top.
    """
    for item in trouble:
        if item.get("kind") == kind:
            return str(item.get("level") or "")
    return ""


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
    # `data-live`: a region the server owns, which a swap replaces on
    # its own instead of replacing every child of <main> around it.
    #
    # The update model was "refetch the whole page, replaceChildren on
    # <main>, then put the operator\'s state back by hand" - six
    # routines doing the putting back, and the caret covered by none of
    # them. What is inside a region is the server\'s; what is outside it
    # is left exactly as it stands, so there is nothing to put back
    # (2026-09-21).
    #
    # Every block the server draws is one. The table was the first and
    # for a day the only region - and the swap, finding it, returned
    # before touching anything else, so the status line, the alert
    # strip, the stock counts, the accounts card, the build card and the
    # GeeLark foot stood still from the moment the tab was opened until
    # it was reloaded (2026-09-21, found by audit the same evening). A
    # block outside every region is a block that never moves; the test
    # that names these regions is what stops the next block being
    # forgotten. The pool manager's mount is the one thing left out on
    # purpose: the sheets under it are fetched, and replacing its
    # children would throw them away every tick.
    table = (f'<div data-live="phones">'
             f'<table id="phones"><thead><tr><th>serial</th><th>status</th>'
             f'<th>gmail</th><th>account</th><th>ip</th>'
             f'<th>age</th><th></th></tr></thead>'
             f'<tbody>{rows}'
             f'<tr class="none" id="nohits" hidden><td colspan="7">'
             f'Nothing here matches that.</td></tr></tbody></table></div>'
             if rows else '<div data-live="phones"><p class="empty">'
                          'No phones yet - the keeper builds the '
                          'shortfall on its next pass.</p></div>')
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
             f'<span class="dim mono" id="tally" data-live="tally">'
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
            + f'<div data-live="build">{_build_card(data, user)}</div>')
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
    # The wrappers around the strip and the foot are there even when
    # either is empty: a region has to exist on both sides of a swap to
    # be swapped, and a block that comes and goes changes the page's
    # shape, which is the whole-of-main path.
    body = (f'<div class="wide"><div data-live="alerts">{strip}</div>'
            f'<div class="top" data-live="top"><h2>Instance manager</h2>'
            f'<span class="status">{_status_sentence(data)}'
            f'{_controls(data, user)}</span>'
            f'{_who_and_out(user)}</div>'
            f'<div class="desk">'
            f'<div class="rail" data-live="rail"><p class="railcap">Built '
            f'from</p>{supply}</div>'
            f'<div class="deskmain">{main}</div>'
            f'<aside class="side" data-live="side"><p class="railcap">Signed '
            f'in on phones</p>{side}</aside></div>'
            f'</div>' + _pool_manager(data, user, manual_login)
            # The ground the farm stands on, at the bottom, where it is
            # scrolled to rather than looked at.
            + f'<div data-live="foot">{_geelark_line(data, user)}</div>'
            )
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
                live="farm", refresh=10 if busy else 15)


def live_page(serial: str, user: dict, said: str = "",
              creds: dict | None = None,
              row: dict | None = None,
              account: dict | None = None) -> str:
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
        return viewer_page(serial, user, url, creds=creds, account=account)
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
                script=False)


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
                creds: dict | None = None,
                account: dict | None = None) -> str:
    """The Live tab once the phone is up: GeeLark's viewer inside this
    page, and a beat every fifteen seconds that says the tab is open.

    `creds` is the Gmail signed into the phone - address, password and
    authenticator key - drawn in the margin for whoever holds the phone,
    with the authenticator's current code computed in the page and
    counted down (the operator, 2026-09-16: "write the Gmail's details
    cleanly beside the screen"). None draws no Gmail box.

    `account` is the app account signed into it, drawn under the Gmail
    with the product it belongs to and, for Spotify, which kind: the
    phone being handed over is the account on it as much as the Google
    one, and half the phones the farm builds have no Gmail at all (the
    operator, 2026-09-19). Neither one draws no margin at all.

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
    rows = (_gmail_margin(creds) if creds else "") + (
        _account_margin(account) if account else "")
    if rows:
        rows += f'<script>{_TOTP_SCRIPT}</script>'
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
        '.gf-chip{margin:0 0 12px}'
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
    return page(f"Phone {serial}", body, user=user, here="/",
                script=False, bare=True)


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


def needs_page(data: dict, user: dict, advice, said: str = "",
               said_note: str = "") -> str:
    """`advice` is failures.verdict, passed in rather than imported here:
    pages render, read decides, and the one module that may know the verdict
    table is the one assembling the data."""
    total = sum(len(v) for v in data.values())
    body = (f'<div class="top"><h2>Needs attention</h2><span class="status">'
            f'{total} waiting on a person</span></div>'
            f'<p class="sub">What the program refuses to decide on its own. '
            f'Each block says what it is and where the fix lives.</p>'
            + _said(said, _POOL_SAID, user, said_note))
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
                  progress: dict | None = None,
                  stops_asked: tuple[str, ...] | list[str] = ()) -> str:
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
                # Pressed already: shown so, not offered again - the same
                # rule the dashboard's row keeps (2026-09-21).
                stop = (_cancel_form(user, serial, "/requests", asked=True)
                        if serial in (stops_asked or ()) else
                        f'<form method="post" class="inline" '
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
    return page_("Requests", body, user=user, here="/requests", live="farm",
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
    "forgot": "The refused keys are forgotten. A key that is ours was "
              "never held by them anyway.",
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
    # The door's own state, in the two words that decide whether a
    # panel can do anything at all this morning. The page used to say
    # only whether writing was on, and nothing at all about whether the
    # door answered (the review, 2026-09-19).
    shut = not data.get("open", True)
    body += (
        '<div class="panel apidoor"><div class="row">'
        + (f'<span class="badge {"bad" if shut else "green"}">'
           f'{"the API door is SHUT" if shut else "the API door is open"}'
           f'</span>')
        + (f'<span class="badge {"green" if data.get("writes") else "warn"}">'
           f'writes {"on" if data.get("writes") else "off"}</span>')
        + f'<span class="dim">{per_minute or "any number of"} requests a '
          f'minute per key &middot; {cap or "any number of"} accounts a day '
          f'per key (UTC)</span></div>'
        + ('<p class="hint">With writes off, a <b>panel</b> key is read-only: '
           'every POST and DELETE answers <code>405 not_allowed</code>. A '
           '<b>sandbox</b> key writes either way, into a practice room where '
           'no phone is ever built. Turning writes on is WEB_API_WRITES in '
           'the server environment - one line and a restart of the web '
           'container, seconds, not a release.</p>' if not data.get("writes")
           else '<p class="hint">Writes are <b>on</b>: a panel key POSTing an '
                'account is buying a phone, a Gmail and an exit. A sandbox '
                'key still writes only into the practice room.</p>')
        + '</div>')
    refused = data.get("refused") or []
    if refused:
        after = int(data.get("locked_after") or 5)
        mins = int(data.get("locked_for") or 600) // 60
        rows_r = "".join(
            f'<tr><td class="muted">{esc(str(r["prefix"]))}&hellip;</td>'
            f'<td>{int(r["tries"])} wrong '
            f'{"try" if int(r["tries"]) == 1 else "tries"}</td>'
            f'<td class="muted">'
            f'{_ago(r["last"]) if r.get("last") else "just now"}</td>'
            f'<td>{"<span class=\"badge bad\">locked out</span>" if int(r["tries"]) >= after else ""}</td>'
            f'</tr>' for r in refused[:8])
        body += (
            f'<div class="panel"><h3>Keys that were refused</h3>'
            f'<p class="hint">This process&#x27;s own memory, since it '
            f'started: a key we do not know, or one that was switched off '
            f'when it called. {after} wrong tries lock a prefix out for '
            f'{mins} minutes - a key that IS ours is never held by that, '
            f'and using it clears the count. The token itself is never '
            f'kept.</p><table>{rows_r}</table>'
            f'<form method="post" action="/api-clients/forget" class="row">'
            f'<input type="hidden" name="csrf" value="{csrf}">'
            f'<button class="quiet">Forget them</button></form></div>')
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
        if int(r.get("refused_tries") or 0):
            tries = int(r["refused_tries"])
            seen += (f' <span class="badge bad" title="wrong tries on this '
                     f'prefix in the last ten minutes">{tries} refused'
                     f'</span>')
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
    # `script=False`: this page must not swap, and since 2026-09-20 the
    # swap and the script are separate switches - a page that does not
    # listen is no longer a page without buttons. This one wants
    # neither: the live layer would replace the one screen in the
    # console that shows something it cannot show again - a token,
    # mid-copy (the review, 2026-09-19).
    return page("A new key", body, user=user, here="/api-clients",
                script=False)


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
    # `no` is what a verb that ran and refused answers with. It had no
    # entry here at all, so `_said` returned "" and a refusal on a pool
    # page drew no banner whatever - the press looked like it had done
    # nothing. The sentence is a fallback: the verb's own words, read off
    # the settled row, are what is normally shown (2026-09-20).
    "no": "That did not go through - open Requests for the reason.",
    "removed-gmail": "Removed - the row is out of the Gmail pool.",
    "removed-gpt": "Removed - the row is out of the GPT pool.",
    "removed-spotify": "Removed - the row is out of the Spotify pool.",
    "removed-proxy": "Removed - the exit is out of the pool.",
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
    key = str(r.get("totp_secret") or "")
    address = str(r.get("recovery_email") or "")
    if key and address:
        # A row can hold both, and the word says so rather than naming
        # whichever the reader happened to prefer - which is how a key
        # went unseen for a fortnight (sgiving962@gmail.com, 2026-09-19).
        value, word, colour = key, "authenticator + recovery", "green"
    elif key:
        value, word, colour = key, "authenticator", "green"
    elif address:
        value, word, colour = address, "recovery", "amber"
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


def _gmail_actions(user: dict, r: dict, view: str, back: str = "") -> str:
    """Edit and Remove on one row. Edit is a link, because the row it
    opens is this same page with one row drawn as a form - no state to
    keep, and a reload leaves it open where it was.

    `back` is where the person actually is, seller and page and all. It
    was rebuilt here from the view alone, so an Edit on page three came
    back to page one and a press on a list filtered to one seller came
    back to the unfiltered one (2026-09-20)."""
    if not _may(user, "may_add_gmail"):
        return ""
    back = back or f"/pools/gmail?view={view}"
    address = str(r.get("address") or "")
    return (f'<a class="btn quiet go" href="{back}&edit={int(r["id"])}">'
            f'Edit</a> '
            f'<form method="post" action="/pools/gmail/remove" class="inline">'
            f'{_csrf(user)}<input type="hidden" name="address" '
            f'value="{esc(address)}">'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<button class="quiet bad">Remove</button></form>')


def _errored_actions(user: dict, r: dict, back: str) -> str:
    """Edit and Free on a row Google refused.

    Free rather than Remove: a refused row is usually a row with one
    cell wrong in it, and what it needs is correcting and putting back,
    not throwing away. Remove stays where it always was, on the queued
    list.
    """
    if not _may(user, "may_add_gmail"):
        return ""
    address = str(r.get("address") or "")
    return (f'<a class="btn quiet go" href="{esc(back)}'
            f'{"&" if "?" in back else "?"}edit={int(r["id"])}">Edit</a> '
            f'<form method="post" action="/pools/gmail/free" class="inline">'
            f'{_csrf(user)}<input type="hidden" name="address" '
            f'value="{esc(address)}">'
            f'<input type="hidden" name="back" value="{esc(back)}">'
            f'<button class="quiet ok" data-busy="Freeing&hellip;" '
            f'title="back on the shelf, to be handed out again">'
            f'Free</button></form>')


def _gmail_edit_row(user: dict, r: dict, view: str, columns: int,
                    back: str = "") -> str:
    """One row, drawn as a form across the whole table: every cell the
    sheet keeps about this account, in the order it reads. The seller is
    a free box with the known names offered - a seller nobody has typed
    yet is a real thing, and a select cannot say one."""
    back = back or f"/pools/gmail?view={view}"
    address = str(r.get("address") or "")
    # The key first, to match the cell the pool shows. Filled the other
    # way round, saving a row that had both wrote the address over the
    # key - the editor rewrites every cell it shows.
    secret = str(r.get("totp_secret") or r.get("recovery_email") or "")
    return (f'<tr class="editrow"{_row_key("gmail", address, view)}>'
            f'<td colspan="{columns}">'
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


def _row_key(kind: str, name: str, view: str) -> str:
    """The attributes that make one row of a dedicated pool page
    addressable: which row, and which view drew it. The script answers
    a press on it with that row alone (`page_row_answer`), the way the
    dashboard's sheet has since step 8 - a page whose rows carried no
    key paid a full redirect and a whole page re-read for every Free,
    Edit and Remove (2026-09-22, found by audit)."""
    # `data-view` is the phone table's chip word (dash.js `sift`), so
    # the pool pages' view is its own attribute.
    return (f' data-key="{esc(kind)}:{esc(str(name or ""))}"'
            f' data-page-view="{esc(view)}"')


def _gmail_view_row(view: str, r: dict, user: dict, here: str, *,
                    advice=None, editing: int = 0) -> str:
    """One row of the Gmail page, as `view` draws it."""
    acts = _may(user, "may_add_gmail")
    key = _row_key("gmail", r.get("address"), view)
    if view == "errored":
        if editing and int(r.get("id") or 0) == editing:
            return _gmail_edit_row(user, r, view, 5 + (1 if acts else 0), here)
        return (f'<tr{key}><td>{esc(r["address"])}</td>'
                f'<td>{_reason_words(str(r["status"]))}</td>'
                f'<td class="muted">{_why(r, advice)}</td>'
                f'<td class="muted">{_when(r["updated_at"])}</td>'
                f'<td class="act">{_refund_cell(r, user, here)}</td>'
                + (f'<td class="act">{_errored_actions(user, r, here)}</td>'
                   if acts else "") + "</tr>")
    if view == "used":
        return (f'<tr{key}><td>{esc(r["address"])}</td>'
                f'<td>{_serial_link(r.get("serial"))}</td>'
                f'<td class="muted">{_when(r.get("used_at") or r["updated_at"])}'
                f'</td><td class="muted">{esc(r.get("seller") or "")}</td></tr>')
    if view == "on_phone":
        return (f'<tr{key}><td>{esc(r["address"])}</td>'
                f'<td>{_serial_link(r.get("serial"))}</td>'
                f'<td>{_on_phone_badge(r)}</td>'
                f'<td class="muted">{_when(r["updated_at"])}</td></tr>')
    if editing and int(r.get("id") or 0) == editing:
        return _gmail_edit_row(user, r, view, 5 + (1 if acts else 0), here)
    return (f'<tr{key}><td><span class="hand">{esc(r["address"])}</span></td>'
            f'<td class="muted">{esc(r.get("seller") or "")}</td>'
            f'<td>{_secret_cell(r)}</td>'
            f'<td class="muted">{_pass_cell(r)}</td>'
            f'<td class="muted">{esc(r.get("purchased_on") or "")}</td>'
            + (f'<td class="act">{_gmail_actions(user, r, view, here)}</td>'
               if acts else "") + "</tr>")


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
                    advice=None, editing: int = 0,
                    said_note: str = "") -> str:
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
            + _said(said, _POOL_SAID, user, said_note))
    if view == "queued":
        body += _gmail_add(user, data.get("known_sellers") or [])
    body += _view_pills("/pools/gmail", GMAIL_VIEWS, view, counts)

    # Where the person actually is, to the parameter: the view, the
    # seller they filtered to, the page they are on. Every Edit link and
    # every button's `back` carries it, so a press comes back to the
    # list as they had it. The allowlist on the other side used to match
    # whole strings and knew only the bare views, so everything else was
    # silently replaced - press Paid on a seller-filtered list and you
    # landed on the unfiltered queued one (2026-09-20).
    page_no = int(data.get("page") or 1)
    here = f"/pools/gmail?view={view}"
    if seller:
        here += f"&seller={_q(seller)}"
    if page_no > 1:
        here += f"&page={page_no}"

    if view == "errored":
        body += _errored_filters(data, seller)
        # Edit and Free, which this view has never had. A row Google
        # refused is exactly the row somebody wants to correct - a key
        # read as a recovery address, a password pasted with a space -
        # and until now the only place to do it was the dashboard's
        # overlay, where a click on the dark area threw the correction
        # away (the operator, 2026-09-20). Both verbs and both routes
        # already existed; this is the markup that reaches them.
        acts = "<th></th>" if _may(user, "may_add_gmail") else ""
        head = ("<tr><th>address</th><th>reason</th><th>what happened</th>"
                f"<th>failed</th><th>where it stands</th>{acts}</tr>")
        lines = "".join(_gmail_view_row(view, r, user, here, advice=advice,
                                        editing=editing) for r in rows)
        empty = ("nothing has failed for this seller" if seller else
                 "nothing has been refused by Google")
    elif view == "used":
        head = ("<tr><th>address</th><th>phone</th><th>used</th>"
                "<th>seller</th></tr>")
        lines = "".join(_gmail_view_row(view, r, user, here) for r in rows)
        empty = "nothing has been retired yet"
    elif view == "on_phone":
        head = ("<tr><th>address</th><th>phone</th><th>state</th>"
                "<th>since</th></tr>")
        lines = "".join(_gmail_view_row(view, r, user, here) for r in rows)
        empty = "no address is signed in on a phone right now"
    else:
        acts = "<th></th>" if _may(user, "may_add_gmail") else ""
        head = (f"<tr><th>address</th><th>seller</th><th>secret</th>"
                f"<th>password</th><th>purchased</th>{acts}</tr>")
        lines = "".join(_gmail_view_row(view, r, user, here,
                                        editing=editing) for r in rows)
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


def _refund_cell(row: dict, user: dict, back: str) -> str:
    """Where one errored address stands: coming back on its own, or money.

    The two piles used to read the same, so a captcha - which signs in two
    times in three on its next try - sat in front of a seller beside a
    password that was never right (the operator, 2026-09-12).

    `back` is where the person is, page and all. It was rebuilt here
    from the view and the seller, so Paid on page three of a seller's
    list - the two presses made over and over while working a refund
    list - came back to page one (2026-09-21, found by audit).
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
    return (f'<tr{_row_key("proxy", name, "needs_hand")}>'
            f'<td>{esc(name)}<br>{_proxy_word(r)}</td>'
            f'<td class="muted">{esc(where)}{phone}</td>'
            f'<td>{_clip(seen, 90)}<br><span class="dim">{esc(advice)}</span>'
            f'</td><td class="right">{buttons}</td></tr>')


def _proxy_view_row(view: str, r: dict, user: dict, here: str,
                    tests: dict) -> str:
    """One row of the Proxy page, as `view` draws it."""
    if view == "needs_hand":
        return _trouble_row(user, r, tests, here)
    name = str(r.get("name") or "")
    key = _row_key("proxy", name, view)
    where = (f'<td class="muted">{esc(str(r.get("host") or ""))}:'
             f'{esc(str(r.get("port") or ""))}</td>')
    if view == "on_phone":
        return (f'<tr{key}><td>{esc(name)}</td>{where}'
                f'<td>{_serial_link(r.get("serial"))}</td>'
                f'<td>{_proxy_word(r)}</td>'
                f'<td class="muted">{_when(r.get("updated_at"))}</td></tr>')
    if view == "all":
        return (f'<tr{key}><td>{esc(name)}</td>{where}'
                f'<td>{_proxy_word(r)}</td>'
                f'<td>{_serial_link(r.get("serial"))}</td>'
                f'<td class="muted mono">{esc(str(r.get("last_exit_ip") or ""))}'
                f'</td><td>{_test_words(tests, name)}</td></tr>')
    return (f'<tr{key}><td>{esc(name)}</td>{where}'
            f'<td class="muted mono">{esc(str(r.get("last_exit_ip") or ""))}'
            f'</td><td class="muted num">{esc(str(r.get("times_used") or 0))}'
            f'</td><td>{_test_words(tests, name)}</td>'
            f'<td class="right">'
            + _proxy_button(user, "/pools/proxy/test", name, "Test",
                            back=here)
            + " "
            + _proxy_button(user, "/pools/proxy/remove", name, "Remove",
                            "bad", back=here)
            + "</td></tr>")


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
                    q: str = "", show_ignored: bool = False,
                    said_note: str = "") -> str:
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
            + _said(said, _POOL_SAID, user, said_note))
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
        lines = "".join(_proxy_view_row(view, r, user, here, tests)
                        for r in rows)
        empty = "no exit is on a phone right now"
    elif view == "all":
        head = ("<tr><th>name</th><th>host</th><th>state</th><th>phone</th>"
                "<th>exit ip</th><th>last test</th></tr>")
        lines = "".join(_proxy_view_row(view, r, user, here, tests)
                        for r in rows)
        empty = (f'nothing matches "{q}"' if q else "the pool is empty")
    else:
        head = ("<tr><th>name</th><th>host</th><th>exit ip</th><th>uses</th>"
                "<th>last test</th><th></th></tr>")
        lines = "".join(_proxy_view_row(view, r, user, here, tests)
                        for r in rows)
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


def _gpt_view_row(view: str, r: dict, user: dict, here: str, *,
                  explain=None, can_login: bool = False) -> str:
    """One row of the Gpt page, as `view` draws it."""
    key = _row_key("gpt", r.get("address"), view)
    if view == "delivered":
        return (f'<tr{key}><td>{esc(r["address"])}</td>'
                f'<td>{_serial_link(r.get("serial"))}</td>'
                f'<td class="muted">{_when(r["updated_at"])}</td>'
                f'<td>{_source_badge(r)}</td></tr>')
    if view == "on_phone":
        return (f'<tr{key}><td>{esc(r["address"])}</td>'
                f'<td>{_serial_link(r.get("serial"))}</td>'
                f'<td>{_on_phone_badge(r)}</td>'
                f'<td class="muted">{_when(r["updated_at"])}</td></tr>')
    if view == "needs_human":
        offer = _may(user, "may_add_gpt")
        return (f'<tr{key}><td>{esc(r["address"])} {_source_badge(r)}</td>'
                f'<td>{_reason_words(str(r["status"]))}</td>'
                f'<td class="muted">{_gpt_happened(r, explain)}</td>'
                f'<td class="right">' + (
                    f'<form method="post" action="/pools/gpt/offer" '
                    f'class="inline">{_csrf(user)}<input type="hidden" '
                    f'name="address" value="{esc(r["address"])}">'
                    f'<input type="hidden" name="back" value="{esc(here)}">'
                    f'<button class="quiet warn" data-busy="Offering&hellip;">'
                    f'Offer again</button></form>'
                    if offer else "") + "</td></tr>")
    return (f"<tr{key}>" + (f'<td><input type="checkbox" name="addresses" '
                            f'value="{esc(str(r["address"]))}"></td>'
                            if can_login else "")
            + f'<td>{esc(r["address"])}</td>'
              f'<td>{_source_badge(r)}</td>'
              f'<td>{_kind_2fa_word(r)}</td>'
              f'<td class="muted">{_when(r.get("created_at"))}</td></tr>')


def _gpt_here(view: str, q: str, page_no: int) -> str:
    """The Gpt pool address a door comes back to: the bare path for the
    Waiting view's first page, the parts that differ from it otherwise -
    the same order `app._back_to` rebuilds them in."""
    parts = []
    if view != "waiting":
        parts.append(f"view={view}")
    if q:
        parts.append(f"q={_q(q)}")
    if page_no > 1:
        parts.append(f"page={page_no}")
    return "/pools/gpt" + ("?" + "&".join(parts) if parts else "")


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
                  form: dict | None = None, error: str = "",
                  said_note: str = "") -> str:
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
    # Where the person is - view, search and page - for every door on
    # the page to come back to. Offer again carried nothing and its
    # route named the bare path, so a press on page two of the set-aside
    # list drew the Waiting list under a bar that still said otherwise
    # (2026-09-21, found by audit). The bare path is the Waiting view.
    here = _gpt_here(view, q, int(data.get("page") or 1))

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
            + _said(said, _POOL_SAID, user, said_note))
    if view == "waiting":
        body += _gpt_add(user, form, error)
    body += _view_pills("/pools/gpt", GPT_VIEWS, view, counts)

    lines = "".join(_gpt_view_row(view, r, user, here, explain=explain,
                                  can_login=can_login) for r in rows)
    if view == "delivered":
        head = ("<tr><th>address</th><th>phone</th><th>delivered</th>"
                "<th>where from</th></tr>")
        empty = (f'nothing delivered matches "{q}"' if q else
                 "nothing has been delivered yet")
    elif view == "on_phone":
        head = ("<tr><th>address</th><th>phone</th><th>state</th>"
                "<th>since</th></tr>")
        empty = "no account is signing in or waiting to go out"
    elif view == "needs_human":
        head = ("<tr><th>address</th><th>reason</th><th>what happened</th>"
                "<th></th></tr>")
        empty = "nothing has been set aside for a person"
    else:
        th = "<th></th>" if can_login else ""
        head = (f"<tr>{th}<th>address</th><th>where from</th><th>2fa</th>"
                f"<th>added</th></tr>")
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
                 f'<input type="hidden" name="back" value="{esc(here)}">'
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
                pasted: str = "", back: str = "/pools/gpt",
                category: str = "") -> str:
    """The Gpt Pool's paste, judged row by row the way the by-hand form
    judges one; the good rows ride into the confirm as the same
    tab-separated text, and the paste stays in a box underneath.

    `category` is the kind the paste was labelled with, carried into the
    confirm beside the rows. An eco paste is addresses and nothing else,
    so its table has no password or 2fa column to show (2026-09-19).
    """
    good = _good(rows)
    eco = category == "eco"
    heads = ("<tr><th>address</th><th>verdict</th></tr>" if eco else
             "<tr><th>address</th><th>password</th><th>2fa</th>"
             "<th>verdict</th></tr>")
    lines = heads + "".join(
        f"<tr><td>{esc(r.get('address') or r.get('line', ''))}</td>"
        + ("" if eco else
           f"<td class=\"muted\">{_shown_password(r)}</td>"
           f"<td class=\"muted\">{_second_factor(r)}</td>")
        + f"<td>{_verdict_badge(r)}</td></tr>" for r in rows)
    carried = "\n".join(
        r["address"] if eco else
        f"{r['address']}\t{r['password']}\t{r.get('secret') or ''}"
        for r in good)
    hidden = (f'<input type="hidden" name="category" value="{esc(category)}">'
              if category else "")
    body = ('<div class="top"><h2>Gpt Pool</h2><span class="status">'
            'preview — nothing is added yet</span></div>'
            + _preview_card("/pools/gpt/add", rows, good, user, idem, back,
                            lines, carried, hidden=hidden,
                            note=("they go in as eco accounts - a code is "
                                  "emailed to each" if eco else
                                  "each waits for a phone to be sent to"))
            + f'<div class="panel"><h3>Edit and preview again</h3>'
            f'<form method="post" action="/pools/gpt/preview" class="field">'
            f'{_csrf(user)}{hidden}'
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
    return page("Events", body, user=user, here="/events", live="farm",
                refresh=30)


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
    elif _kept_for(phone):
        head = (f"{_NOW_WORDS.get(status, _phone_word(status))} — on the "
                f"shelf, kept for {esc(_kept_for(phone))}")
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
    # The dashboard's row said Stopping the moment Cancel was pressed;
    # this page - where a build is watched - went on offering Cancel
    # over the press it had already taken (2026-09-21, found by audit).
    stopping = bool(story.get("stop_asked"))
    waiting = str(story.get("pending") or "")
    if stopping:
        head += ('<span class="badge manual" title="Cancel was pressed; '
                 'the build gives up at its next step and puts back what '
                 'it held">Stopping</span>')
    actions = []
    # The same rule the table keeps. This page kept none, so clicking a
    # serial that read "with ali" offered Done and Failed on ali's phone
    # (2026-09-07).
    held_by = _theirs(user, phone) if phone else ""
    if held_by:
        actions = [f'<span class="age">'
                   f'{esc(_whose_word(phone, held_by))}</span>']
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
            actions.append(_cancel_form(user, serial, back, asked=stopping))
    actions = [a for a in actions if a]
    if waiting and not stopping:
        # One door, pressed, until the lane has carried the command out.
        actions = [_pending_door(waiting)]

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
#:
#: Written for one box and now serving every one on the page: the margin
#: shows the Gmail and the app account side by side, and either can
#: carry an authenticator (2026-09-19). Each code element names its own
#: countdown bar and each show button names its own password, so nothing
#: here knows how many boxes there are.
#:
#: The old shape read one id and returned early when it was missing -
#: which took the copy buttons and the show button with it, on every
#: phone whose Gmail row had no key. Wiring each kind of control on its
#: own is what fixes that.
_TOTP_SCRIPT = (
    "(function(){"
    "function b32(s){s=s.replace(/[^A-Za-z2-7]/g,'').toUpperCase();"
    " var A='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567',bits='',out=[];"
    " for(var i=0;i<s.length;i++){bits+=A.indexOf(s[i]).toString(2).padStart(5,'0');}"
    " for(var j=0;j+8<=bits.length;j+=8){out.push(parseInt(bits.slice(j,j+8),2));}"
    " return new Uint8Array(out);}"
    "function code(el){"
    " var bar=document.getElementById(el.getAttribute('data-bar')||'');"
    " var keyP=crypto.subtle.importKey('raw',"
    "  b32(el.getAttribute('data-secret')),{name:'HMAC',hash:'SHA-1'},"
    "  false,['sign']);"
    " var last=-1;"
    " function tick(){"
    "  var now=Math.floor(Date.now()/1000), step=Math.floor(now/30),"
    "      left=30-(now%30);"
    "  if(bar) bar.style.width=(left/30*100)+'%';"
    "  if(step===last) return; last=step;"
    "  keyP.then(function(key){"
    "   var msg=new Uint8Array(8), t=step;"
    "   for(var i=7;i>=0;i--){msg[i]=t&255; t=Math.floor(t/256);}"
    "   return crypto.subtle.sign('HMAC',key,msg);"
    "  }).then(function(sig){"
    "   var h=new Uint8Array(sig), o=h[19]&15;"
    "   var c=((h[o]&127)<<24|h[o+1]<<16|h[o+2]<<8|h[o+3])%1000000;"
    "   el.textContent=String(c).padStart(6,'0');"
    "  }).catch(function(){el.textContent='------';});"
    " }"
    " tick(); setInterval(tick,1000);"
    "}"
    "document.querySelectorAll('[data-secret]').forEach(code);"
    "document.querySelectorAll('[data-copy]').forEach(function(b){"
    " b.addEventListener('click',function(){"
    "  var t=document.getElementById(b.getAttribute('data-copy'));"
    "  if(!t) return;"
    "  var text=t.getAttribute('data-value')||t.textContent;"
    "  navigator.clipboard.writeText(text).then(function(){"
    "   b.textContent='copied'; setTimeout(function(){b.textContent='copy';},1500);});"
    " });});"
    "document.querySelectorAll('[data-show]').forEach(function(b){"
    " var pw=document.getElementById(b.getAttribute('data-show'));"
    " if(!pw) return;"
    " var dots='\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022';"
    " b.addEventListener('click',function(){"
    "  var on=pw.textContent===dots;"
    "  pw.textContent=on?pw.getAttribute('data-value'):dots;"
    "  b.textContent=on?'hide':'show';});});"
    "})();"
)


def _margin_rows(prefix: str, label: str, creds: dict, *,
                 say_no_key: bool = True) -> str:
    """One credential's rows for the margin: address, password (hidden
    until shown, copied without showing) and the authenticator's code as
    it stands. Not the key it is made from: the code is what a person
    types, and the key beside it was one more thing to read past (the
    operator, 2026-09-16). It stays on the pool row.

    `prefix` keys every id on this box, so the Gmail's and the app
    account's can sit on one page without either one's show button
    reaching into the other (2026-09-19).

    `say_no_key` draws the authenticator row even when there is no key.
    True for the Gmail, where a missing key is news - every Gmail the
    farm buys has one. False for an app account, where most have none
    and a row saying so is a line of margin spent on nothing.
    """
    address = str(creds.get("address") or "")
    password = str(creds.get("password") or "")
    secret = str(creds.get("totp_secret") or "")
    dots = "\u2022" * 8
    rows = [
        f'<div class="gf-row"><span class="lbl">{esc(label)}</span>'
        f'<div class="val"><code id="{prefix}-addr">{esc(address)}</code>'
        f'<button type="button" class="quiet" data-copy="{prefix}-addr">copy'
        f'</button></div></div>']
    if password:
        rows.append(
            f'<div class="gf-row"><span class="lbl">Password</span>'
            f'<div class="val">'
            f'<code id="{prefix}-pw" data-value="{esc(password)}">'
            f'{dots}</code>'
            f'<button type="button" class="quiet" data-show="{prefix}-pw">'
            f'show</button>'
            f'<button type="button" class="quiet" data-copy="{prefix}-pw">copy'
            f'</button></div></div>')
    else:
        rows.append('<div class="gf-row"><span class="lbl">Password</span>'
                    '<div class="val"><code class="dim">none on the row'
                    '</code></div></div>')
    if secret:
        rows.append(
            f'<div class="gf-row"><span class="lbl">Authenticator code</span>'
            f'<div class="val"><code id="{prefix}-totp" class="gf-code" '
            f'data-secret="{esc(secret)}" data-bar="{prefix}-bar">------</code>'
            f'<button type="button" class="quiet" data-copy="{prefix}-totp">'
            f'copy</button></div>'
            f'<div class="gf-bar"><i id="{prefix}-bar"></i></div></div>')
    elif say_no_key:
        rows.append('<div class="gf-row"><span class="lbl">Authenticator'
                    '</span><div class="val"><code class="dim">none on the '
                    'row</code></div></div>')
    return "".join(rows)


def _gmail_margin(creds: dict) -> str:
    """The Gmail box: what is signed into Google on this phone."""
    return f'<h3>On this phone</h3>{_margin_rows("gf", "Gmail", creds)}'


def _account_margin(account: dict) -> str:
    """The app account's box, under the Gmail's.

    Which product it is for and - for Spotify - which kind, because that
    is what the person handing the phone over is about to say out loud,
    and then the same three rows the Gmail gets. A bare Spotify phone
    has no Gmail at all, so before this the margin on it was empty
    beside the one account the phone exists for (the operator,
    2026-09-19).
    """
    product = str(account.get("product") or "").strip().lower()
    kind = str(account.get("category") or "").strip().lower()
    named = {"spotify": "Spotify", "claude": "Claude"}.get(product, "ChatGPT")
    chip = _carries({"app_account": account.get("address"),
                     "app_product": product, "app_category": kind})
    code_only = bool(account.get("email_code_only"))
    note = ('<div class="gf-row"><span class="lbl">Signing in</span>'
            '<div class="val"><code class="dim">by a code emailed to it - '
            'no password</code></div></div>' if code_only else "")
    return (f'<h3>{esc(named)} account</h3>'
            f'{f"<p class=gf-chip>{chip}</p>" if chip else ""}'
            f'{_margin_rows("gf-acct", "Address", account, say_no_key=False)}'
            f'{note}')
