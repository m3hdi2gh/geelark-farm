"""Turn a recorded walk into the first draft of a flow.

The second of the three tools. Writing a flow by hand means reading
thirteen kilobytes of XML per page and guessing which words on it will
still be there next time. This does the guessing part, and it does it
by a method rather than by taste:

**A screen's matcher is what only that screen says.** Given the eight
pages of a walk, the text on page four that is on no other page is
exactly what tells page four from the rest. It is a set difference, it
costs nothing, and it is better than a person's first guess - a person
reaches for the heading, and the heading is often on three pages.

What this cannot know, and says so in the draft rather than inventing:

- **What to do on a page.** It lists the controls it found, in order of
  what a flow usually wants (a button called Next, an empty field), and
  leaves the act for a person to choose.
- **Two pages that read the same.** When a walk passes the same page
  twice - a loading spinner, a list come back to - there is nothing
  unique to either. The draft names them and says they need telling
  apart by what came before, which is a real thing about the walk and
  not a failure of this.

Nothing here touches a phone. It reads a folder and writes text.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import capture, screen

#: A matcher this short is an accident ("OK", "1"); this long is a
#: paragraph, and a paragraph is re-worded between releases.
SHORTEST, LONGEST = 4, 44
#: Labels that say nothing about which page this is. The clock and the
#: battery are on every Android screen; the rest are chrome.
EVERYWHERE = frozenset({
    "back", "navigate up", "more options", "close", "menu", "search",
    "phone", "messages", "chrome", "settings", "home", "overview",
    "recent apps", "notification", "wifi", "battery", "signal",
})

#: What a flow reaches for first on a page it has just recognised.
WANTED_BUTTONS = ("next", "continue", "sign in", "log in", "accept",
                  "agree", "allow", "ok", "done", "got it", "no thanks",
                  "not now", "skip", "use without an account")


def _useful(label: str) -> bool:
    """Whether a label is worth matching on at all."""
    text = label.strip()
    if not (SHORTEST <= len(text) <= LONGEST):
        return False
    if text.casefold() in EVERYWHERE:
        return False
    # A code, a clock, a count: different every run.
    letters = sum(c.isalpha() for c in text)
    return letters >= max(3, len(text) // 3)


#: A label that begins like an instruction is the app's own words, and
#: the app says them to everybody. "Welcome back, Ali" is seventeen
#: characters and beat "Enter your password" on length alone - and it
#: holds a person's name, so it is different for every account this
#: flow will ever meet (found by the draft's own test, 2026-09-25).
#:
#: This is a guess from one walk, and the honest method needs two: record
#: the same job with two different accounts, and the labels that differ
#: between the walks are the ones that belong to the account. Worth
#: building when a flow is written for something that greets people.
ASKS = ("enter", "choose", "select", "confirm", "verify", "sign in",
        "sign up", "log in", "add", "create", "type", "scan", "allow",
        "check your", "get a", "use ", "try ", "set ")


def _score(label: str) -> tuple:
    """Lower sorts first: an instruction, then no digits, then shorter."""
    words = label.casefold()
    return (not words.startswith(ASKS), any(c.isdigit() for c in label),
            len(label), words)


def slug(label: str) -> str:
    """A screen name from its best label: `enter_your_password`.

    The apostrophe goes, inside a word as well as at its ends: Google
    writes "Verify it's you", and `is_verify_it's_you` is not a name
    Python will take (found by running this on that page, 2026-09-25).
    """
    words = re.findall(r"[A-Za-z']+", label.casefold())[:4]
    kept = [w.replace("'", "") for w in words]
    return "_".join(w for w in kept if w) or "page"


def only_here(pages: list[list[screen.Element]]) -> list[list[str]]:
    """For each page, the labels no other page carries."""
    sets = [{e.label for e in page if e.label} for page in pages]
    out = []
    for i, mine in enumerate(sets):
        others: set[str] = set()
        for j, theirs in enumerate(sets):
            if j != i:
                others |= theirs
        out.append(sorted(mine - others))
    return out


def controls(page: list[screen.Element]) -> list[screen.Element]:
    """The things on a page a flow could act on, best first: a button
    whose word a flow usually wants, then any other clickable, then the
    input fields."""
    empty, filled, wanted, clickable, labels = [], [], [], [], []
    for element in page:
        if element.is_input:
            (empty if not element.text else filled).append(element)
        elif element.label and element.label.casefold() in WANTED_BUTTONS:
            (wanted if element.clickable else labels).append(element)
        elif element.clickable and element.label:
            clickable.append(element)
    # A form is filled before it is submitted, so an empty field leads.
    # Then the buttons a flow reaches for, and only then a label that
    # merely says one of their words - 'Sign in' is the heading of the
    # page whose button is 'NEXT' (2026-09-25).
    return empty + wanted + clickable + filled + labels


def name_pages(pages: list[list[screen.Element]],
               unique: list[list[str]]) -> list[str]:
    """One name per page. A page with nothing of its own is named for
    where it sits, because it has to be called something.

    What names a page is not what matches it. A matcher wants the
    shortest stable string; a name wants to say what the page IS, and
    the page's own words say that while its buttons do not - Chrome's
    ad-privacy page was called `got_it` after the button that dismisses
    it (found on the first real walk, 2026-09-26). So a label that is
    not a control is preferred, and the button is only the fallback.
    """
    names, used = [], {}
    for i, labels in enumerate(unique):
        usable = [w for w in labels if _useful(w)]
        pressable = {e.label for e in pages[i] if e.clickable and e.label}
        says = sorted([w for w in usable if w not in pressable], key=_score)
        best = says or sorted(usable, key=_score)
        base = slug(best[0]) if best else f"page_{i + 1}"
        used[base] = used.get(base, 0) + 1
        names.append(base if used[base] == 1 else f"{base}_{used[base]}")
    return names


def draft(directory: Path) -> str:
    """The draft, as Python source ready to be pasted into a flow."""
    pages = capture.pages(directory)
    if not pages:
        return "# nothing to draft: the walk has no screens\n"
    unique = only_here(pages)
    names = name_pages(pages, unique)

    out = [
        '"""A draft, written by `geelark draft` from a recorded walk.',
        "",
        f"    {len(pages)} screen(s) from {Path(directory).name}",
        "",
        "Every matcher below is the text that was on that screen and on no",
        "other screen of this walk. Read each one and ask whether it will",
        "still be there next time; a heading usually will, a count will not.",
        "The acts are left for you: this knows what could be pressed, never",
        "what should be.",
        '"""',
        "from geelark_farm.flows.router import Outcome, Screen, act_wait",
        "",
        "",
    ]

    ambiguous = []
    for i, page in enumerate(pages):
        labels = [w for w in unique[i] if _useful(w)]
        best = sorted(labels, key=_score)[:2]
        out.append(f"# ---------------------------------------- screen {i + 1}"
                   f": {names[i]}")
        if not best:
            ambiguous.append(i + 1)
            every = sorted({e.label for e in page
                            if e.label and _useful(e.label)},
                           key=_score)[:2]
            out.append("# Nothing on this screen is only on this screen. It is "
                       "the same page")
            out.append("# as another in this walk, so it cannot be told apart "
                       "by its words:")
            out.append("# match on what came before it (`ctx.trail`), or let "
                       "one Screen")
            out.append("# handle both. Its own words, for reference:")
            out.append("#   " + (", ".join(repr(w) for w in every) or "(none)"))
            best = every[:1]
        said = ", ".join(repr(w.casefold()) for w in best) or "'change me'"
        out.append(f"def is_{names[i]}(ctx):")
        out.append(f"    return ctx.has({said})")
        out.append("")
        out.append(f"def act_{names[i]}(ctx):")
        found = controls(page)
        if found:
            out.append("    # what is on this screen, in the order a flow "
                       "usually wants it:")
            for element in found[:6]:
                what = ("an empty field" if element.is_input and not element.text
                        else f"a field holding {element.text!r}" if element.is_input
                        else "a button" if element.clickable else "a label")
                out.append(f"    #   {element.label!r:44} {what}")
            first = found[0]
            if first.is_input:
                out.append("    # field = screen.find_input(ctx.elements)")
                out.append("    # return fill(ctx, field, ...) and None")
            else:
                out.append(f"    # ctx.tap({first.label!r})")
        else:
            out.append("    # nothing here can be pressed - a page that draws "
                       "itself, or a spinner.")
            out.append("    # return act_wait(ctx)")
        out.append("    raise NotImplementedError('say what to do here')")
        out.append("")
        out.append("")

    out.append("SCREENS = [")
    for page_name in names:
        out.append(f"    Screen({page_name!r}, is_{page_name}, act_{page_name}),")
    out.append("]")
    out.append("")
    if ambiguous:
        out.append(f"# {len(ambiguous)} screen(s) had no words of their own: "
                   + ", ".join(str(n) for n in ambiguous))
    out.append(f"# Written from {len(pages)} screen(s). Correct it, then:")
    out.append(f"#   geelark replay {Path(directory).as_posix()} "
               f"--flow <your module>")
    return "\n".join(out) + "\n"
