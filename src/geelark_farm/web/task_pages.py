"""The Tasks pages: what there is to run, how it has been going, and
one run with the screens it saw.

The first version was read-only: what the playground had been doing
became visible to whoever was not running it from a terminal - the
numbers, the reasons, and the page a flow gave up on. The Run form came
second (2026-09-27), with its three decisions made by the operator: the
phone is one somebody names, one task runs at a time, and only an admin
runs one. The form is drawn from the task's own fields, so a new task
gets a form nobody wrote.

No viewer of its own: a run's screens are drawn by `journey.wireframe_svg`
through the phone page's own `/phones/<serial>/wire/...` route, which
already has the guards. Two drawings of one thing is how they come to
disagree.

In its own module for the same reason `task_read` is: `pages.py` is
seven thousand lines that somebody else is usually editing.
"""
from __future__ import annotations

from .pages import _csrf, _may, _said, _when, esc, page

#: The banner words a task page can be sent. Plain sentences, as every
#: other page's table is: `_said` escapes what it is given, and the
#: first version's (tone, words) pairs were never drawn, so nobody saw
#: that they would not have drawn (2026-09-27). A request's own sentence
#: - "phone 4435 is taken - ..." - replaces these when there is one.
_TASK_SAID = {
    "queued": "Queued - a builder takes it within seconds. Its row "
              "appears below; reload to see it end.",
    "already": "Already asked - that phone has a task on its way.",
    "twice": "That press already went through the first time.",
    "refused": "Only an admin runs a task.",
    "no": "It was not run.",
}

#: What a blame means, in the words the rest of the console uses. The
#: table is `failures.py`'s; these are only the colours.
_BLAME_TONE = {"credential": "bad", "exit": "warn", "device": "info",
               "challenged": "manual", "nobody": ""}


def _rate(tally: dict) -> str:
    """The one number, and what it is of. A rate with no count under it
    reads as a fact when it is two runs (the pools learned this)."""
    runs = int(tally.get("runs") or 0)
    if not runs:
        return '<span class="dim">no runs yet</span>'
    worked = int(tally.get("worked") or 0)
    tone = "ok" if worked == runs else "warn" if worked * 2 >= runs else "bad"
    middle = int(tally.get("median_seconds") or 0)
    said = f"of {runs} run{'' if runs == 1 else 's'}"
    if middle:
        said += f" · median {middle}s"
    return (f'<b class="mono" style="color:var(--{tone})">'
            f'{worked * 100 // runs}%</b> '
            f'<span class="dim">{esc(said)}</span>')


def _blames(tally: dict) -> str:
    """Whose fault the failures were - the reading a rate alone cannot
    give. Forty on the exit is a proxy problem; forty on the device is
    our bug."""
    blames = tally.get("blames") or {}
    if not blames:
        return ""
    return " ".join(
        f'<span class="badge {_BLAME_TONE.get(who, "")}">{esc(who)} {n}</span>'
        for who, n in blames.items())


def tasks_page(data: dict, user: dict, said: str = "",
               said_note: str = "") -> str:
    """What there is to run, and how each has been going."""
    rows = []
    for task in data["tasks"]:
        tally = task.get("tally") or {}
        asks = ", ".join(
            f.get("name", "") for f in task["inputs"] if f.get("required"))
        rows.append(
            f'<tr><td><a href="/tasks/{esc(task["key"])}">'
            f'{esc(task["title"])}</a> '
            f'<span class="dim mono">{esc(task["key"])}</span>'
            f'<br><span class="muted">{esc(task["summary"])}</span>'
            f'<br><span class="dim mono">needs {esc(asks) or "nothing"}'
            f'</span></td>'
            f'<td class="nowrap">{_rate(tally)}</td>'
            f'<td>{_blames(tally)}</td></tr>')
    body = ('<div class="narrow"><div class="top"><h2>Tasks</h2>'
            '<span class="sub" style="margin:0">the jobs a phone does '
            'that are not builds</span></div>'
            + _said(said, _TASK_SAID, user, said_note)
            + '<p class="sub">What somebody used to do to a phone by hand: '
              'read whether an account is still signed in, see what an '
              'app draws first. A task is run from the command line for '
              'now - <span class="mono">geelark task &lt;name&gt;</span> - '
              'and everything it did shows up here.</p>')
    if not data.get("counted"):
        body += ('<p class="hint">The store is off on this host, so the '
                 'numbers below are empty. What exists is still listed.</p>')
    body += (f'<div class="panel wrap"><table>'
             f'<tr><th>task</th>'
             f'<th>last {int(data.get("days") or 7)} days</th>'
             f'<th>whose fault</th></tr>{"".join(rows)}</table></div>')
    return page("Tasks", body + "</div>", user=user, here="/tasks")


def task_page(data: dict, user: dict, said: str = "",
              said_note: str = "", *, phones=(), serial: str = "",
              may_run: bool = False) -> str:
    """One task: how it has been going, and every run of it."""
    spec = data.get("spec")
    if spec is None:
        return page("Tasks", '<div class="narrow"><div class="top">'
                             '<h2>No such task</h2></div>'
                             '<p class="sub">Nothing is registered under that '
                             'name. <a href="/tasks">Back to Tasks</a></p>'
                             '</div>', user=user, here="/tasks")
    tally = data.get("tally") or {}
    rows = "".join(
        f'<tr><td class="muted nowrap">{_when(r["started_at"])}</td>'
        f'<td>{_serial(r)}</td>'
        f'<td>{_outcome(r)}</td>'
        f'<td>{_blame_cell(r)}</td>'
        f'<td class="mono num">{float(r["seconds"] or 0):.0f}s</td>'
        f'<td class="act"><a class="btn quiet" '
        f'href="/tasks/{esc(spec.key)}/{int(r["id"])}">Open</a></td></tr>'
        for r in data["rows"])
    reasons = "".join(
        f'<tr><td class="mono">{esc(reason)}</td>'
        f'<td class="num">{n}</td></tr>'
        for reason, n in (tally.get("reasons") or {}).items())

    body = (f'<div class="narrow">'
            f'<p><a class="dim" href="/tasks">&larr; Tasks</a></p>'
            f'<div class="top"><h2>{esc(spec.title)}</h2>'
            f'<span class="sub" style="margin:0">{_rate(tally)}</span></div>'
            f'<p class="sub">{esc(spec.summary)}</p>'
            + _said(said, _TASK_SAID, user, said_note)
            + (_run_form(spec, user, phones, serial) if may_run
               else _how_to_run(spec)))
    if reasons:
        body += (f'<div class="panel"><h3>When it did not work</h3>'
                 f'<table>{reasons}</table>'
                 f'<p class="hint">{_blames(tally)}</p></div>')
    body += (f'<div class="panel wrap"><h3>Runs '
             f'<span class="n">{len(data["rows"])}</span></h3>'
             + (f'<table><tr><th>when</th><th>phone</th><th>came to</th>'
                f'<th>whose fault</th><th>took</th><th></th></tr>'
                f'{rows}</table>' if rows else
                '<p class="empty">nothing has been run yet</p>')
             + '</div></div>')
    return page(spec.title, body, user=user, here="/tasks")


def _run_form(spec, user: dict, phones, serial: str = "") -> str:
    """Run it: the phone, then the task's own fields, then the button.

    The phone list is a suggestion the browser filters as a serial is
    typed - four hundred phones do not fit a select, and a person who
    came from a phone's page already knows the number (`?serial=`).
    A task that asks for a secret gets no form at all: its secret is
    typed on the command line and forgotten, never sent to a queue.
    """
    if spec.secrets():
        return (_how_to_run(spec, why="It asks for a secret, which is typed "
                                      "on the command line and never sent "
                                      "through the console."))
    known = "".join(
        f'<option value="{esc(str(p.get("serial") or ""))}">'
        f'{esc(_phone_line(p))}</option>'
        for p in phones if str(p.get("serial") or "").isdigit())
    wanted = serial if str(serial or "").isdigit() else ""
    fields = "".join(
        f'<label class="field"><span>{esc(f.label)}'
        f'{"" if f.required else " (optional)"}</span>'
        f'<input name="in_{esc(f.name)}" autocomplete="off" '
        f'spellcheck="false" class="mono"'
        f'{" required" if f.required else ""}'
        f' placeholder="{esc(f.help)}"></label>'
        for f in spec.inputs)
    return (f'<div class="panel"><h3>Run it</h3>'
            f'<form method="post" action="/tasks/{esc(spec.key)}/run">'
            f'{_csrf(user)}'
            f'<label class="field"><span>Phone</span>'
            f'<input name="serial" list="task-phones" required '
            f'inputmode="numeric" pattern="[0-9]+" autocomplete="off" '
            f'class="mono" placeholder="serial, e.g. 4435" '
            f'value="{esc(wanted)}"></label>'
            f'<datalist id="task-phones">{known}</datalist>'
            f'{fields}'
            f'<div class="row" style="margin-top:10px">'
            f'<button class="go">Run</button>'
            f'<span class="hint">One at a time. A phone that is off is '
            f'started and switched off again; one somebody has taken is '
            f'refused.</span></div></form>'
            f'<details><summary class="dim">From the command line</summary>'
            f'<pre class="mono" style="margin:6px 0 0;overflow-x:auto">'
            f'{_command(spec)}</pre></details></div>')


def _phone_line(phone: dict) -> str:
    """What the list says beside a serial: enough to know the phone."""
    return " · ".join(str(phone.get(k)) for k in ("status", "state", "gmail")
                      if phone.get(k))


def _command(spec) -> str:
    asks = "".join(f" {f.name}=..." for f in spec.inputs if f.required)
    return (f'geelark task {esc(spec.key)}{esc(asks)} '
            f'--phone &lt;ID&gt;')


def _how_to_run(spec, why: str = "Only an admin runs one from here.") -> str:
    """The line to type, until the Run button exists. Its required
    fields in the order the spec lists them, because that is the order
    somebody reading the page above has just seen them in."""
    return (f'<div class="panel"><h3>How to run it</h3>'
            f'<p class="hint">{esc(why)}</p>'
            f'<pre class="mono" style="margin:0;overflow-x:auto">'
            f'{_command(spec)}</pre></div>')


def _serial(row: dict) -> str:
    serial = str(row.get("serial") or "")
    if not serial.isdigit():
        return '<span class="dim">-</span>'
    return f'<a href="/phones/{esc(serial)}">{esc(serial)}</a>'


def _outcome(row: dict) -> str:
    reason = str(row.get("reason") or "")
    if str(row.get("status")) == "running":
        return '<span class="badge info">running</span>'
    tone = "ok" if row.get("ok") else "bad"
    return f'<span class="badge {tone}">{esc(reason or "?")}</span>'


def _blame_cell(row: dict) -> str:
    if row.get("ok"):
        return '<span class="dim">-</span>'
    who = str(row.get("blame") or "")
    if not who:
        return '<span class="dim">-</span>'
    return f'<span class="badge {_BLAME_TONE.get(who, "")}">{esc(who)}</span>'


def run_page(row: dict, user: dict, advice=None) -> str:
    """One run: what it came to, and every screen it kept.

    The screens are drawn by the phone page's own route, so this page
    adds no viewer and no second opinion about what a screen looks like.
    """
    task = esc(str(row.get("task") or ""))
    said = advice(str(row.get("reason") or "")) if advice else None
    # `.said` is green with a tick and `.said.no` is red with a bang -
    # the console's two, and the only two. A guessed `.said.bad` drew a
    # failed run as a success (seen in the devserver, 2026-09-26).
    tone = "" if row.get("ok") else "no"
    body = (f'<div class="narrow">'
            f'<p><a class="dim" href="/tasks/{task}">&larr; {task}</a></p>'
            f'<div class="top"><h2>Run {int(row["id"])}</h2>'
            f'{_outcome(row)}{_blame_cell(row)}</div>')
    if said is not None:
        body += (f'<p class="said {tone}">{esc(said.seen)}'
                 f'<br><span class="dim">{esc(said.advice)}</span></p>')
    elif row.get("detail"):
        body += f'<p class="said {tone}">{esc(str(row["detail"]))}</p>'

    facts = [("phone", _serial(row)),
             ("started", _when(row.get("started_at")) or
              '<span class="dim">-</span>'),
             ("took", f'{float(row.get("seconds") or 0):.0f}s'),
             ("api calls", str(row.get("api_calls") or 0)),
             ("screens", str(len(row.get("screens") or [])))]
    body += ('<div class="panel"><table>'
             + "".join(f'<tr><td class="dim">{esc(k)}</td><td>{v}</td></tr>'
                       for k, v in facts) + "</table></div>")
    # `trails` carries the phase's name, and a task's phase is its own
    # key - which says the same word twice. What is wanted is the
    # screens, and a run that recognised none says nothing rather than
    # drawing a panel holding a colon (the devserver, 2026-09-26).
    walked = str(row.get("trail") or "")
    walked = (walked.partition(":")[2] if ":" in walked else walked).strip()
    if walked:
        body += (f'<div class="panel"><h3>What it walked</h3>'
                 f'<p class="mono">{esc(walked)}</p></div>')
    if row.get("inputs"):
        body += ('<div class="panel"><h3>What it was given</h3><table>'
                 + "".join(f'<tr><td class="mono">{esc(str(k))}</td>'
                           f'<td class="mono muted">{esc(str(v))}</td></tr>'
                           for k, v in dict(row["inputs"]).items())
                 + '</table><p class="hint">A field the task marks secret is '
                   'never written here.</p></div>')

    screens = row.get("screens") or []
    if screens:
        # `jcard` and `jcards` are the phone journey's own (console.css,
        # 2026-09-26). A task's screens are the same thing seen from
        # another page, so they wear the same clothes and the stylesheet
        # grows by nothing.
        cards = "".join(
            f'<figure class="jcard">'
            f'<img src="{esc(s["wire"])}" loading="lazy" width="120" '
            f'height="240" alt="{esc(s["screen"])}, as the phone reported it">'
            f'<figcaption><b>{esc(s["screen"])}</b>'
            f'<span class="mono dim">{esc(s["at"])}</span>'
            + (f'<a href="{esc(s["shot"])}" target="_blank" '
               f'rel="noopener">photo</a>' if s["shot"] else "")
            + '</figcaption></figure>' for s in screens)
        body += (f'<div class="panel wrap journey"><h3>The screens it saw '
                 f'<span class="n">{len(screens)}</span></h3>'
                 f'<div class="jcards">{cards}</div>'
                 f'<p class="dim">Drawn from each page\'s own elements, the '
                 f'way a phone\'s journey draws them. An address in one is a '
                 f'stand-in: a run never writes a real one down.</p></div>')
    else:
        body += ('<div class="panel"><p class="empty">No screen of this run '
                 'reached the store.</p></div>')
    return page(f"Run {int(row['id'])}", body + "</div>", user=user,
                here="/tasks")


def may_run(user: dict, settings) -> bool:
    """Who gets the Run form: an admin, on a console whose buttons are
    switched on. The verb and `_act` ask again; this only decides what
    is drawn."""
    return (user.get("role") == "admin"
            and bool(getattr(settings, "web_mutations", False)))


def may_see(user: dict) -> bool:
    """Who the Tasks pages are for. The same tick that opens the pools:
    a task's rows say what happened to the farm's own stock."""
    return _may(user, "may_add_gmail") or _may(user, "may_change_proxy")
