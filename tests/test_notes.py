"""Every Note this code can write, checked without running it.

The dynamic tests in test_builder.py drive a build and read the cells it left,
which proves the paths they happen to take. This reads the source instead, so a
note is covered whether or not any test drives the branch that writes it - and
the ones nobody drives are exactly where the old machine-shaped notes survived.

What counts as standard, and why:

- **A sentence.** Opens with a capital, ends with a full stop. The column is
  read by a person, next to columns that are not prose.
- **No reason token.** `no_usable_gpt` names a thing exactly, and it belongs in
  the Status column - which is what you filter on - and in the terminal summary
  and the logs, which is what you grep. A note that repeats it is telling a
  person to go and learn the vocabulary first.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from geelark_farm import failures

SRC = pathlib.Path(__file__).resolve().parents[1] / "src/geelark_farm"

#: The pool methods that put a note in a cell. `ledger.release(note=...)` is
#: deliberately not here: that one goes to state/ledger.json, which is a
#: machine's record of which phone a run was holding, not a column anyone reads.
WRITERS = {"spend", "release", "fail", "retire"}

TOKEN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def templates(node: ast.AST) -> list[str]:
    """Every way this note can read, with `{}` where a value goes.

    Enough of an evaluator for the shapes that appear: a literal, an f-string, a
    truncation, a `x or "..."` default, a conditional. A conditional yields
    both branches - the first version of this returned only one, and the branch
    it dropped was the one a failed build takes.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        out = ""
        for part in node.values:
            out += str(part.value) if isinstance(part, ast.Constant) else "{}"
        return [out]
    if isinstance(node, ast.Subscript):            # note=(f"...")[:200]
        return templates(node.value)
    if isinstance(node, ast.BoolOp):               # note=note or "..."
        return templates(node.values[-1])
    if isinstance(node, ast.IfExp):                # note=a if cond else b
        return templates(node.body) + templates(node.orelse)
    return []                                      # a name or an attribute


def receiver(node: ast.Attribute) -> str:
    """`book.proxies.release` -> `proxies`; `ledger.release` -> `ledger`."""
    target = node.value
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return ""


def written_notes() -> list[tuple[str, int, str]]:
    """Every note literal in the package, as (file, line, text)."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            named = (isinstance(node.func, ast.Attribute)
                     and node.func.attr in WRITERS
                     and receiver(node.func) != "ledger")
            for keyword in node.keywords:
                if keyword.arg != "note" or not named:
                    continue
                found += [(path.name, node.lineno, text)
                          for text in templates(keyword.value) if text]
    return found


def test_the_scan_finds_the_notes_it_is_meant_to_check():
    """If this finds nothing, everything below passes by knowing nothing."""
    found = written_notes()
    assert len(found) >= 8, found
    assert {name for name, _, _ in found} >= {"builder.py", "pools.py"}


@pytest.mark.parametrize("where,line,note",
                         [(w, ln, n) for w, ln, n in written_notes()])
def test_every_note_the_code_can_write_is_a_sentence(where, line, note):
    assert note[0].isupper(), f"{where}:{line} does not open a sentence: {note!r}"
    assert note.rstrip().endswith((".", "{}")), (
        f"{where}:{line} has no full stop: {note!r}")


@pytest.mark.parametrize("where,line,note",
                         [(w, ln, n) for w, ln, n in written_notes()])
def test_no_note_the_code_can_write_names_a_reason_token(where, line, note):
    found = TOKEN.findall(note)
    assert not found, (
        f"{where}:{line} puts {found} in front of a person. The token belongs "
        f"in the Status column and the logs; use failures.verdict(...).seen "
        f"for the words.")


def test_the_advice_a_failure_carries_is_also_a_sentence():
    """`advice` is written straight into a credential's Note when a build
    condemns it, so it is held to the same standard as the notes above."""
    for reason in failures.VERDICTS:
        # Rendered, because three of these open on `{service}` - the name of
        # whoever refused, which is not known until a row is being written.
        verdict = failures.verdict(reason, "OpenAI")
        assert verdict.advice[0].isupper(), reason
        assert verdict.advice.rstrip().endswith("."), reason
        assert not TOKEN.findall(verdict.advice), (
            f"{reason}'s advice names a token: {verdict.advice!r}")
        assert "{" not in verdict.advice, (
            f"{reason} left a placeholder unfilled: {verdict.advice!r}")


def test_the_phone_note_never_carries_a_flow_s_own_words():
    """A flow writes its detail for whoever is debugging it - play_install says
    `on screen: [Install, Uninstall]`, which is the right thing to log and the
    wrong thing to put in a tab. Every build-level detail that ends up in the
    Phones tab is either written here or taken from the taxonomy.
    """
    source = (SRC / "builder.py").read_text(encoding="utf-8")

    assert "installed.detail" not in source
    assert "outcome.detail" not in source


def test_a_reason_with_no_entry_still_reads_as_a_sentence():
    """The fallback is the one note nobody writes by hand, so it is the one
    most likely to be machine-shaped. It names the token deliberately - an
    unclassified reason is a bug report, and the token is the bug."""
    unknown = failures.verdict("something_nobody_has_seen_yet")

    assert unknown.seen.startswith("something happened")
    assert "something_nobody_has_seen_yet" in unknown.seen


# ---------------------------------------------- the Phones tab's own note
# `_phone_note` does not take a `note=` keyword, so the scan above cannot see
# it. These read the (status, detail) pairs out of every place a build can end
# and put each through the real function.
def build_endings() -> list[tuple[int, str, str]]:
    """Every `finish(status, detail)` in builder.py, as (line, status, detail)."""
    tree = ast.parse((SRC / "builder.py").read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        called = (isinstance(node, ast.Call)
                  and ((isinstance(node.func, ast.Name)
                        and node.func.id == "finish")
                       or (isinstance(node.func, ast.Attribute)
                           and node.func.attr == "finish")))
        if not called or len(node.args) < 2:
            continue
        status = templates(node.args[0])
        detail = templates(node.args[1])
        if not detail:
            continue
        # Two endings name their status from a variable - the flow's own reason,
        # and whatever Aborted carried. Neither can be `ready`, so a stand-in is
        # enough to exercise the branch their detail goes down. Leaving them out
        # was leaving out the two details built from a flow's words.
        found.append((node.lineno, status[0] if status else "stopped", detail[0]))
    return found


def test_the_scan_finds_every_way_a_build_can_end():
    endings = build_endings()
    assert len(endings) >= 8, endings
    assert {status for _, status, _ in endings} >= {"ready", "no_usable_gpt"}


@pytest.mark.parametrize("line,status,detail",
                         [(ln, s, d) for ln, s, d in build_endings()])
def test_every_way_a_build_can_end_reads_as_a_sentence(line, status, detail):
    from geelark_farm import builder

    note = builder._phone_note(builder.Build(
        index=0, ok=status == builder.READY, status=status, detail=detail,
        serial="700",
        tried=[("someone@example.com", "captcha_shown", "Google")]))

    assert note[0].isupper(), f"builder.py:{line}: {note!r}"
    assert note.rstrip().endswith("."), f"builder.py:{line}: {note!r}"
    assert not TOKEN.findall(note), (
        f"builder.py:{line} ends a build in a way that reaches the Phones tab "
        f"as a token: {note!r}")


def test_a_stop_the_builder_raises_itself_reads_as_a_sentence():
    """These never reach VERDICTS - there is no credential to blame - so they
    used to arrive in the tab as the bare word raised by Aborted()."""
    from geelark_farm import builder

    for reason in failures.SITUATIONS:
        note = builder._phone_note(builder.Build(
            index=0, ok=False, status=reason,
            detail=failures.situation(reason)))
        assert not TOKEN.findall(note), note
        assert note.startswith("Stopped short: "), note


# ============================ a failure that is survived is still announced
#: How a handler can tell somebody. A log line, a printed line, a `Check`
#: appended to the report, a row added to a table - or a `raise`/`return`,
#: which tells the caller rather than a person.
#:
#: Kept as a list rather than a rule about "does it do anything", because a
#: handler that does plenty and says nothing is exactly the case this is for.
SPEAKS = ("attr='error'", "attr='warning'", "attr='info'", "attr='exception'",
          "attr='debug'", "id='print'", "attr='print'", "attr='append'",
          "attr='add_row'", "id='Check'")


def test_no_failure_is_survived_in_silence():
    """Twenty-one places in this package log an error and carry on, and each
    is the same decision: this failed, it must not stop the run, so say so.
    Four handlers made the first two halves of that decision and not the
    third (2026-08-23).

    The one that cost something: a row claimed with an unreadable date was
    skipped without a word, so it was never old enough to be abandoned, never
    freed, and nothing anywhere said why.
    """
    import ast
    import pathlib

    silent = []
    for path in sorted(pathlib.Path("src/geelark_farm").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            if "Raise(" in body or "Return(" in body:
                continue                       # it tells the caller
            if any(word in body for word in SPEAKS):
                continue                       # it tells a person
            silent.append(f"{path.name}:{node.lineno}")

    assert not silent, (
        f"these catch a failure, carry on, and tell nobody: {silent}. "
        f"Either say something or let it out.")
