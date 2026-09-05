"""Run a command in the request that asked for it.

The queue exists because only the pass could write the sheet. With the
pools in the store that stopped being true of stock, and waiting up to
thirty seconds to be told a pasted account was accepted is the difference
between a tool and a form.

**Why this is not in `web/`.** The web package may not import `pools`,
`builder`, `gsheet` or the GeeLark client - a test walks its imports and
fails if it does. That wall is what keeps the pages reading from the
mirror instead of opening a spreadsheet on somebody's click, and it is
worth more than the one import it would save here. So the web calls this,
the same way it calls the store, and the knowledge of what a verb needs
stays on this side of the line.

Nothing here decides *whether* a verb may run inline - `verbs.runs_inline`
does, from the verb's own source. This only runs it.
"""

from __future__ import annotations

import logging

from .config import Settings

log = logging.getLogger(__name__)


def run_now(settings: Settings, verb: str, payload: dict) -> tuple | None:
    """Carry out one store-only command here. `None` if it did not run.

    Never raises: every caller's fallback is the queue, which is where the
    command would have been a minute ago - so the worst this can do is be
    no faster than before, and the row is already written either way.
    """
    from . import verbs as verb_table

    if not (settings.pools_in_pg and verb_table.runs_inline(verb)):
        return None
    try:
        from .ledger import Ledger
        from .pools import Book

        book = Book.pools_only(settings)
        # `client` is None on purpose: a verb that reaches for GeeLark is
        # one `runs_inline` should have refused, and the AttributeError
        # that follows is a great deal louder than a phone being driven
        # from a web worker.
        return verb_table.VERBS[verb](
            book, Ledger.load(settings.state_dir), settings, payload, None)
    except Exception as exc:                                      # noqa: BLE001
        log.warning("%s did not run in the request (%s); it stays queued",
                    verb, exc)
        return None
