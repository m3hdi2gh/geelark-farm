"""What a person asked a build for, as it crosses the jobs table.

The keeper writes a wish's fields into a job's payload and a builder
replica reads them back - two processes that are deployed one after the
other, so for a minute or two they run different versions of this class.
`Wanted(**want)` made that minute dangerous: a field the keeper had and
the replica did not raised TypeError inside the job, which was filed as
`builder_crashed` - a DEVICE blame, counted by the breaker - over a
deploy-order mistake (the builder review, 2026-09-23). `from_payload`
reads it strictly and says so by name.

A leaf: stdlib only.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass


class WishNotUnderstood(ValueError):
    """A job's payload named fields this builder does not know. Ends the
    job as `wish_not_understood`, before anything is claimed."""


@dataclass(frozen=True)
class Wanted:
    """The credentials a person chose for one build, as they typed them.

    Text rather than rows, because the wish is written a pass before the
    build and `build_one` claims under the lock that stops one Gmail
    reaching two phones. A row taken in between is a named failure here,
    not a race there.

    Anything blank means "the pool decides", which is what the keeper's own
    builds do - so a wish naming only a Gmail is a normal build with one
    thing pinned.
    """

    gmail: str = ""
    proxy_name: str = ""
    install_app: bool = True
    app_account: str = ""
    wanted_id: int | None = None
    #: Which app: '' for none, 'chatgpt' (the farm's own), 'spotify'. An
    #: account is only ever signed into ChatGPT (2026-09-08).
    app: str = "chatgpt"
    #: Who asked, by user id. The phone is theirs from the moment it
    #: exists: taken, owned, marked built by them - and not the keeper's
    #: stock while they hold it (2026-09-08).
    requested_by: int | None = None
    #: A bare phone: no Google account at all, and so no app account
    #: either. Nothing is claimed and nothing is signed in. It still
    #: carries the apps every phone carries - bare is about the accounts
    #: (the operator, 2026-09-12) - and it is ready the moment they are
    #: on it.
    no_gmail: bool = False

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> Wanted:
        """The wish a job carries, or WishNotUnderstood naming the fields
        this version does not know. Strict on purpose: a tolerant reader
        that dropped them would build something other than what was
        asked, and say nothing."""
        known = {f.name for f in dataclasses.fields(cls)}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise WishNotUnderstood(
                f"the wish names {', '.join(unknown)}, which this builder "
                f"does not know - it is older than the keeper that queued it")
        return cls(**dict(payload))
