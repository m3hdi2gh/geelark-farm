"""The jobs the farm can do on a phone that are not builds.

`products.py` says what the farm knows about an app it signs accounts
into. This says what it knows about a job: its name, what it asks for,
whether it needs a phone at all, and the module that does it. A build is
not one of these - it has a card, a pool and a queue of its own - and
neither is a sign-in, which is a product's flow. What is left is
everything else somebody does to a phone by hand today: read whether an
account is still signed in, check what an app's first screen says, walk
a setting.

A task is a folder's worth of knowledge in one entry plus one module
with `run(ctx)`. Nothing branches on a task's name anywhere else, which
is the property that lets a console page draw a form for one nobody
wrote console code for.

A leaf, like `products`: stdlib and dataclasses at import time, and the
module resolved when it is asked for. Importing this must not import
every task's dependencies.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType


@dataclass(frozen=True)
class Field:
    """One input a task asks for.

    `secret` is not decoration: a value marked secret is kept out of the
    run's row, out of the log, and out of any screen the run records
    (`capture.Redactor`). A password or a 2FA key is typed and forgotten.
    """

    name: str
    label: str
    required: bool = True
    secret: bool = False
    help: str = ""


@dataclass(frozen=True)
class TaskSpec:
    """One job. Only what today's need; a field is added when a real case
    wants it, not before."""

    #: What it is called on the command line, in a job's payload, and in
    #: the `task_runs` table. Lowercase, underscores.
    key: str
    #: The line a page puts at the top.
    title: str
    #: What it does and what it proves, for somebody who has not seen it.
    summary: str
    #: The module with `run(ctx) -> Outcome`, as a dotted path, imported
    #: when it is asked for.
    runs: str
    inputs: tuple[Field, ...] = ()
    #: Almost always true. A task that reads the store or an API and
    #: touches no device says False, and then nothing is created,
    #: started or stopped for it.
    needs_phone: bool = True
    #: A product key whose package must be on the phone before this runs
    #: (`products.PRODUCTS`). Empty when the task installs its own, or
    #: needs none.
    app: str = ""

    def module(self) -> ModuleType:
        return importlib.import_module(self.runs)

    def required(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.inputs if f.required)

    def secrets(self) -> frozenset[str]:
        return frozenset(f.name for f in self.inputs if f.secret)

    def check(self, given: dict) -> dict:
        """The inputs this was given, judged against what it asks for.

        Everything missing is named at once: a runner that reports them
        one at a time makes somebody start a phone three times to learn
        three things.
        """
        missing = [f.name for f in self.inputs
                   if f.required and not str(given.get(f.name) or "").strip()]
        if missing:
            raise ValueError(f"{self.key} needs {', '.join(sorted(missing))}")
        known = {f.name for f in self.inputs}
        extra = sorted(set(given) - known)
        if extra:
            raise ValueError(f"{self.key} takes no {', '.join(extra)}")
        return {k: str(v) for k, v in given.items() if k in known}

    def public(self, given: dict) -> dict:
        """What of the inputs may be written down."""
        secret = self.secrets()
        return {k: v for k, v in given.items() if k not in secret}

    def secret_values(self, given: dict) -> tuple[str, ...]:
        """The values that must appear in no capture and no log."""
        return tuple(str(given[k]) for k in self.secrets() if given.get(k))


#: Every task, in the order a page lists them.
TASKS: dict[str, TaskSpec] = {
    "app_probe": TaskSpec(
        key="app_probe",
        title="Open an app and read its first screen",
        summary="Launches a package and reports whether it shows a "
                "signed-in screen, a sign-in screen, or a page neither "
                "list knows. Answers 'is this phone still signed in?' "
                "for a delivered account, without a person opening it.",
        runs="geelark_farm.tasks.app_probe",
        inputs=(
            Field("package", "Package name",
                  help="com.openai.chatgpt, com.spotify.music, ..."),
            Field("signed_in_words", "Words that mean signed in",
                  required=False,
                  help="comma separated; the defaults suit a chat app"),
            Field("signed_out_words", "Words that mean signed out",
                  required=False, help="comma separated"),
        ),
    ),
}


def spec(key: str) -> TaskSpec | None:
    return TASKS.get(str(key or "").strip().lower())


def names() -> tuple[str, ...]:
    return tuple(TASKS)
