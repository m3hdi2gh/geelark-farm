"""The events a web request may ring so the service looks sooner.

Nothing here holds state that matters: an event is a nudge, and every
nudge's meaning is already written down in Postgres. If one is missed the
next pass finds the row anyway, thirty seconds later, exactly as it did
before this module existed. That is the whole design - it can only make
the farm quicker, never wrong.

It lives outside `web/` so the web may import it: the import wall forbids
the web from reaching `pools`, `builder`, `gsheet`, `api`, `phones` and
`shell`, and this is none of those. It reaches nothing itself - three
`threading.Event`s and no imports past the standard library - so it cannot
become a way around that wall.

One process only, deliberately. The web server is a thread of the service
(`serve.run` starts it), so a `threading.Event` is enough and a durable
channel would be a second source of truth for something the queue already
records.
"""

from __future__ import annotations

import threading

#: Somebody queued a command. The service's sleep waits on this, so a press
#: is looked at in about a second instead of at the top of the next pass.
queued = threading.Event()

#: Somebody queued a job for the builders (phase 4). A builder's wait
#: sits on this; the keeper never touches it.
jobs = threading.Event()


def ring(event: threading.Event) -> None:
    """Ring a bell.

    A function rather than a bare `event.set()` at each caller, so the
    reason lives in one place: a nudge is never load-bearing, and a missed
    one costs a wait and nothing else.

    No try/except around it, deliberately. `Event.set()` takes a lock and
    sets a flag; there is no failure to swallow, and a guard here would be
    a silent handler standing over an exception that cannot happen - which
    is worse than nothing, because the next reader would believe in it.
    """
    event.set()
