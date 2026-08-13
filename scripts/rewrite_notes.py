"""Rewrite the notes already in the workbook into the voice the code now uses.

One-off. Every note in there was written by an older build, in one of five
shapes, and each maps onto exactly what `builder` would write for it today.
Run with --apply to write; without, it prints the diff and touches nothing.
"""
from __future__ import annotations

import re
import sys

from geelark_farm import builder, failures
from geelark_farm.config import Settings
from geelark_farm.gsheet import a1_column, batch_write
from geelark_farm.pools import Book

DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def spoken(iso: str) -> str:
    y, m, d = DATE.match(iso).groups()
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


def credential(note: str) -> str | None:
    """`phone 685: ready` - what a spent Gmail or app account used to say."""
    match = re.fullmatch(r"phone (\d+): (\w+)", note)
    if not match:
        return None
    serial, status = match.groups()
    if status == builder.READY:
        return f"On phone {serial}."
    return (f"On phone {serial}, which stopped short of ready - see that row "
            f"in the Phones tab.")


def proxy(note: str) -> str | None:
    """The two shapes the Proxy tab used: a refusal seen through it, and a
    proxy freed because nothing was behind it any more."""
    match = re.fullmatch(r"([a-z_]+) seen through it on (\d{4}-\d{2}-\d{2})", note)
    if match:
        reason, when = match.groups()
        return (f"On {spoken(when)} {failures.verdict(reason).seen}, so the "
                f"phone moved to another exit. Free again - that refusal was "
                f"about the attempt, not about this proxy.")
    if note.startswith("freed: the phone that had it was moved onto another"):
        return ("Free again - the phone that had it was moved onto another "
                "exit, so nothing is behind it.")
    if note.startswith("freed: the phone using it is gone"):
        return "Free again - the phone that was behind it no longer exists."
    return None


def challenged(note: str) -> str | None:
    match = re.fullmatch(
        r"challenged on (\d{4}-\d{2}-\d{2}); not judged, free to try again", note)
    if not match:
        return None
    return (f"Challenged on {spoken(match.group(1))} rather than judged, so "
            f"nothing is known against it. Free to try again.")


def phone(note: str, status: str) -> str | None:
    """Rebuild the Build the note describes and let the code write it again,
    so a migrated row and a fresh one cannot read differently."""
    # Already migrated. Worth guarding rather than trusting nobody runs this
    # twice: the new note contains "Also tried:", so a second pass would parse
    # its own output back in and nest it (verified, and it reads like nonsense).
    if note.startswith(("Ready - ", "Stopped short: ")):
        return None
    if not note or not re.search(r"\b(apps:|tried:)|^[a-z_]+\.", note):
        return None
    tried: list[tuple[str, str]] = []
    match = re.search(r"tried: (.+?)(?=\. apps:|\. the |\. it |$)", note)
    if match:
        for item in match.group(1).split("; "):
            email, _, reason = item.rpartition(": ")
            tried.append((email.strip(), reason.strip()))
    ok = status == builder.READY
    detail = ""
    if not ok:
        rest = note[match.end():] if match else note
        rest = re.sub(r"^[a-z_]+\.\s*", "", rest.lstrip(". "))
        detail = rest.strip().rstrip(".")
    return builder._phone_note(builder.Build(
        index=0, ok=ok, status=status, detail=detail, tried=tried))


def main(apply: bool) -> None:
    book = Book.open(Settings.load())
    plans: list[tuple[str, object, list[dict]]] = []

    for label, pool, rules in (
        ("Gmails", book.gmails, (credential, challenged)),
        ("Gpt Info", book.apps, (credential, challenged)),
        ("Proxy", book.proxies, (credential, proxy)),
    ):
        rows = pool._ws.get_all_values()
        at = rows[0].index("Note")
        column = a1_column(at + 1)
        payload = []
        for n, row in enumerate(rows[1:], 2):
            note = row[at] if len(row) > at else ""
            new = next((r(note) for r in rules if r(note)), None)
            if new and new != note:
                print(f"{label} {n}\n  - {note}\n  + {new}")
                payload.append({"range": f"{column}{n}", "values": [[new]]})
        if payload:
            plans.append((label, pool._ws, payload))

    rows = book.phones._ws.get_all_values()
    head = rows[0]
    column = a1_column(head.index("Note") + 1)
    payload = []
    for n, row in enumerate(rows[1:], 2):
        note = row[head.index("Note")] if len(row) > head.index("Note") else ""
        new = phone(note, row[head.index("Status")])
        if new and new != note:
            print(f"Phones {n}\n  - {note}\n  + {new}")
            payload.append({"range": f"{column}{n}", "values": [[new]]})
    if payload:
        plans.append(("Phones", book.phones._ws, payload))

    total = sum(len(p) for _, _, p in plans)
    if not apply:
        print(f"\n{total} note(s) would be rewritten. Nothing was changed.")
        return
    for label, worksheet, payload in plans:
        batch_write(worksheet, book._lock, payload, what=f"{label} notes")
        print(f"{label}: rewrote {len(payload)} note(s)")


if __name__ == "__main__":
    main("--apply" in sys.argv)
