"""Writing a build to the Phones tab: which of its words the row takes, the
note a person reads, a note in the middle of a run, a try counted against
the phone, a phone condemned, and the row and History at the end.

Moved out of builder.py unchanged (the builder review, 2026-09-23). Logs
under the builder's logger name, as before the move.
"""
from __future__ import annotations

import logging

from .build_result import Build, attempts_of, outcome_of
from .config import Settings
from .gsheet import SheetError
from .pools import Book, PhoneLog

log = logging.getLogger("geelark_farm.builder")

READY = PhoneLog.READY
APP_ONLY = PhoneLog.APP_ONLY


def _phone_status(build: Build) -> str | None:
    """Which of the three words this build ended on, or None for "cannot say".

    `READY if build.ok else APP_ONLY` said the app was on the device whenever
    a build stopped short - including when the install was the thing that
    failed. The `App` column beside it said `x` at the same time.

    Vague while `app_only` meant "not finished". Actively misleading once it
    named a product: the tab would offer a phone with no app to somebody whose
    whole use for it is opening that app (2026-08-29).

    That fix answered one half. The other half is a run that never looked:
    `else PhoneLog.INCOMPLETE` was reached by a `finish` whose phone would not
    start, and wrote `incomplete` over a row that had truthfully said
    `app_only` for two hours. `app_only` is one of the two things this farm
    sells, and the demoted phone was headed for its third strike and deletion
    (phone 1415, 2026-08-30).

    So None, and `_record` leaves the columns it would have written alone.
    """
    from .pools import PhoneLog

    if build.ok:
        return READY
    if build.app_installed is None:
        return None
    return APP_ONLY if build.app_installed else PhoneLog.INCOMPLETE


def _phone_note(build: Build) -> str:
    """What the Phones tab says about this build, in sentences.

    The Status column already carries the verdict - `ready` or `incomplete` -
    and the Gmail, GPT Account and Proxy columns already carry the what. This
    is the only cell with room to say how it went, so it is written as prose
    for someone reading the row rather than as a trace for someone debugging.

    It used to be neither: `no_usable_gpt. tried: a@b.com: email_code_required.
    the Gpt Info tab has no unused account left`, and for a phone that worked,
    the output of `pm list packages`. The reason tokens are still exact in the
    terminal summary and the logs, which is where you want to grep them.
    """
    opening = (f"Ready - {outcome_of(build)}." if build.ok
               else f"Stopped short: {outcome_of(build)}.")
    # `no_usable_gpt` is not a fault, it is a finished product of the other
    # kind: Google is signed in, the app is on it, and only an account is
    # missing. Read cold, "Stopped short" says the opposite - and this phone is
    # exactly the one somebody takes to sign a customer in by hand. The
    # taxonomy already computes the reassuring half and it was being thrown
    # away here (2026-08-29).
    if not build.ok and build.status == "no_usable_gpt":
        opening += (" The phone itself is finished - signed into Google with "
                    "the app installed - and is ready to take as it is if "
                    "somebody is signing in themselves.")
    if build.shared_exit:
        # Said on the phone's own row, because whoever reads it later is
        # deciding whether these accounts can be treated as unrelated.
        opening += (" The pool had nothing free when an exit refused this "
                    "phone, so it shares one with another - both accounts "
                    "reach the services from the same address.")
    if not build.tried:
        return opening
    # Everything it gave up on before getting here. On a ready phone these are
    # the false starts; on one that stopped short they are the whole story.
    attempts = "; ".join(line.replace(" - ", " (", 1) + ")"
                         for line in attempts_of(build))
    lead = "Also tried" if build.ok else "Tried"
    return f"{opening} {lead}: {attempts}."


def _note_on_row(book: Book, serial: str, **fields: str) -> None:
    """Say on a phone's row what has just become true of it.

    The row was written once, at the end, in a `finally`. Everything a build
    learned on the way - which Gmail signed in, above all - lived only in
    memory until then, so a run that died left a row saying nothing had
    happened.

    `settle_abandoned` reads that row to decide whether a phone a dead run
    left behind is worth finishing or is not a phone at all, and it reads the
    Gmail column to do it. Empty for the whole length of a build meant every
    interruption deleted a phone that was signed in and working: 1315 had
    signed into Google, installed the app and signed into ChatGPT, and was
    deleted by the next sync two minutes after a restart (2026-08-28).

    By serial, never by the row number `start` handed back ten minutes ago: a
    sibling discarding its phone deletes a row, and every row below it moves
    up, so that number can have come to mean a different phone.

    Never fatal. The build is what matters and this is only how it is
    remembered - a sheet that will not take the write costs a line in the log
    and the old behaviour, not the run.
    """
    what = ", ".join(fields)
    try:
        if not book.phones.write(serial, **fields):
            log.warning("phone %s has no row in the Phones tab to note %s on",
                        serial, what)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not note %s on phone %s's row (%s); a run "
                    "interrupted from here would leave the row saying less "
                    "than is true", what, serial, exc)


def _count_try(settings: Settings, book: Book, build: Build) -> None:
    """Tally one failed finish, and say so on the row when it is the last one.

    Never raises: it is called from a `finally`, where an exception replaces
    the value the function was about to return.
    """
    try:
        from .store import person

        made = person.count_try(settings, build.serial)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not count the attempt on %s (%s)", build.serial, exc)
        return
    limit = book.phones.GIVE_UP_AFTER
    if made >= limit:
        log.warning("phone %s has failed %d finishes; it will not be offered "
                    "again until the %s cell is cleared",
                    build.serial, made, book.phones.TRIES_COLUMN)
        build.detail = (f"{build.detail}. Tried {made} times and set aside - "
                        f"clear the {book.phones.TRIES_COLUMN} cell to offer "
                        f"it again").strip(". ")


def _condemn(book: Book, build: Build) -> None:
    """An empty phone GeeLark would not delete is marked `failed` on its
    row, so the sync deletes it on a later pass - the same door the
    console's Failed goes through.

    The delete fails when the network does: phone 3237 was created,
    GeeLark answered 502 to the sign-in's shell command and then to the
    stop, and the phone was "recorded and left alone" - an empty
    `incomplete` row on the shelf that an operator booted two hours
    later and forgot (2026-09-16). Nothing ever tried the delete again.
    Never fatal, like every row write in this `finally`.
    """
    note = (f"{_phone_note(build)} Nothing was signed into it and GeeLark "
            f"would not delete it when asked; marked failed so the sync "
            f"deletes it once GeeLark answers.")
    try:
        if not book.phones.write(build.serial, State="failed", Note=note):
            log.warning("phone %s has no row left to mark failed", build.serial)
            return
    except Exception as exc:                                      # noqa: BLE001
        log.error("could not mark phone %s failed for the sync (%s) - it "
                  "stays on the shelf as incomplete", build.serial, exc)
        return
    log.info("phone %s: empty and not deleted; marked failed for the sync",
             build.serial)


def _write_row(book: Book, build: Build, *, drop: bool = False) -> None:
    """Put this build in the Phones tab, and never raise doing it.

    Both callers are in a `finally`, where an exception does not merely fail -
    it replaces the value the function was about to return. A sheet that went
    unreachable mid-run therefore threw away three finished Builds and left the
    summary reporting the same urllib3 error three times, in place of what each
    phone had actually reached (2026-08-17).

    The row can be rebuilt from the log and from History. The outcome, once the
    Build carrying it is gone, cannot.
    """
    try:
        if drop:
            book.phones.drop(build.serial)
        else:
            _record(book, build)
    except Exception as exc:                                      # noqa: BLE001
        log.error("could not write %s to the Phones tab (%s) - the run's own "
                  "summary and the log still have it", build.name, exc)


def _record(book: Book, build: Build) -> None:
    """Write the finished phone to the Phones tab. Also in a finally.

    The three step columns read left to right in the order the steps happen:
    Google, then the app, then the app account. Each says the address that
    signed in where there is one to show, and a cross where the step did not
    happen - so `incomplete` beside three crosses and `incomplete` beside two
    addresses are told apart without reading the note.
    """
    note = _phone_note(build)
    cross = book.phones.NO
    status = _phone_status(build)

    def said(value: str) -> str:
        return value or cross

    # Status and App are claims about the device. A run that never reached it
    # makes neither, and the cells keep what the last run that did look put
    # there. Everything else is about the run itself - which exit it used,
    # what happened - and is true whether or not the phone ever came up.
    device: dict[str, str] = {}
    if status is not None:
        device = {"Status": status,
                  "App": book.phones.YES if build.app_installed else cross}
        # Which one, for the table: "Spotify" beside a phone that has
        # it, rather than "waiting for one". Written only by a run that
        # knows - the same rule as the two above, applied per column
        # rather than to the group. `finish_one` sets `app_installed`
        # and never `app`, so every finish used to blank this: the row
        # then said nothing was on a phone with all three apps on it,
        # and the console's table said so too (3644, 2026-09-19).
        if build.app:
            device["App name"] = build.app

    # A phone asked for on the build card is `taken` by whoever asked from
    # the moment it exists - that is what keeps the keeper off it and what
    # "Building - yours" reads - and the hold ends with the build. It goes
    # back on their shelf: State blank, Owner still theirs, so the row
    # offers Boot in one press and nobody else can press it (the operator,
    # 2026-09-18). Written before `_condemn`, which is the one thing that
    # may put `failed` here afterwards, and never on the keeper's own.
    shelf = {"State": ""} if build.built_for else {}
    try:
        wrote = book.phones.write(
            build.serial,
            Proxy=build.proxy_name or build.proxy,
            Gmail=said(build.gmail), Note=note,
            **{"GPT Account": said(build.app_account)}, **device, **shelf,
        )
        if not wrote:
            log.error("phone %s has no row in the Phones tab to record on; "
                      "its result is in the summary above and nowhere else",
                      build.serial)
    except SheetError as exc:
        log.error("could not record phone %s (%s)", build.serial, exc)
    # The Phones tab is current state - a row marked done is deleted, and with
    # it every answer to "what did we build on Tuesday". History keeps the
    # outcome, appended, whichever machine produced it.
    # History is appended whatever happened, and `Event` is the one word it
    # gets. Where the run cannot name a phone status it names its own outcome
    # instead - `phone_would_not_start` says more about that row than a
    # guessed `incomplete` ever did, and it is already a `failures` token.
    book.record_history(
        Serial=build.serial, Event=status or build.status,
        Seconds=f"{build.seconds:.0f}", Proxy=build.proxy_name or build.proxy,
        Gmail=build.gmail, Note=note, Steps=build.steps,
        **{"GPT Account": build.app_account,
           "App": book.phones.INSTALLED if build.app_installed else ""})
