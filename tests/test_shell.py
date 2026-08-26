"""What the device is asked, and what an unanswered question is worth.

This module is where the project decides what is true - whether Google is
signed in, whether the app is really installed. Both answers are read out of
a command's output, so a command that never ran produces the same empty
string as one that ran and found nothing. Those are opposite answers, and one
of them condemns a perfectly good account.
"""

from __future__ import annotations

import pytest

from geelark_farm import shell


class Device:
    """A phone that either runs a command or reports that it did not."""

    def __init__(self, *, output="", ok=True, explode=None):
        self.output, self.ok = output, ok
        self.explode = explode
        self.commands: list[str] = []
        #: Whether each call asked for a retry - which is the whole of what
        #: separates a command that reads from one that acts.
        self.retries: list[bool | None] = []

    def data(self, path, payload=None, **kwargs):
        self.commands.append(payload["cmd"])
        self.retries.append(kwargs.get("retry"))
        if self.explode:
            raise self.explode
        return {"status": self.ok, "output": self.output}


def test_a_refused_command_is_not_read_as_an_answer():
    """`dumpsys account` failing used to read as "nobody is signed in", which
    sends the build to `no_google_account` and tells an operator to rebuild a
    phone that was fine (2026-08-23)."""
    with pytest.raises(shell.ShellError, match="dumpsys account"):
        shell.device_accounts(Device(ok=False), "P1")


def test_the_app_cannot_be_disproved_by_a_command_that_never_ran():
    with pytest.raises(shell.ShellError):
        shell.package_installed(Device(ok=False), "P1", "com.openai.chatgpt")


def test_a_command_that_ran_and_found_nothing_still_answers_nothing():
    """The other half. An empty answer is only a verdict when it was asked."""
    assert shell.device_accounts(Device(output=""), "P1") == []
    assert not shell.package_installed(Device(output=""), "P1", "com.x")


def test_the_accounts_on_the_device_are_read_from_dumpsys():
    device = Device(output="Account {name=A@Gmail.com, type=com.google}")

    assert shell.device_accounts(device, "P1") == ["a@gmail.com"]


def test_the_authenticator_type_alone_is_not_an_account():
    """`com.google` is present on a device with no accounts at all. Matching
    it produced a false "signed in" reading once."""
    assert shell.device_accounts(Device(output="type=com.google"), "P1") == []


def test_an_ordinary_command_still_returns_what_it_produced():
    """Everything that is not proof stays as it was: a warning, not a raise,
    because a failed tap is not worth ending a flow over."""
    device = Device(output="", ok=False)

    assert shell.run(device, "P1", "input tap 1 2") == ""


def test_a_poll_may_ask_without_being_ended_by_one_bad_answer():
    """Both of these are also called in a loop - the router asking whether
    Google has landed, the installer asking whether the download finished.

    Strict there ends a whole login over one refused `dumpsys`, which is the
    opposite of what the strictness is for: an empty answer in a loop means
    "not yet", and the next look is a few seconds away (2026-08-23).
    """
    assert shell.device_accounts(Device(ok=False), "P1", strict=False) == []
    assert not shell.package_installed(Device(ok=False), "P1", "com.x",
                                       strict=False)


def test_the_safe_reading_is_what_a_new_caller_inherits():
    """Strict stays the default, so opting out has to be written down."""
    import inspect

    for fn in (shell.device_accounts, shell.package_installed):
        assert inspect.signature(fn).parameters["strict"].default is True


# =====================================================================
# What mutation found (2026-08-26). This module answers two questions
# the whole pipeline rests on - who is signed in, and what is
# installed - and the difference between "the command did not run" and
# "it ran and found nothing" is what `strict` exists for.
# =====================================================================

# ------------------------------------------------- did it run, or find nothing
def test_a_refused_command_is_told_apart_from_one_that_found_nothing():
    """The reason `strict` is here. Without it the two arrive as the same
    empty string, and for the questions this module answers those are opposite
    answers with the same consequence: a failed `dumpsys account` reads as
    "nobody is signed in", which is the reading that condemns a Gmail that is
    fine."""
    refused = Device(ok=False)

    with pytest.raises(shell.ShellError, match="would not run"):
        shell.run(refused, "P", "dumpsys account", strict=True)

    found_nothing = Device(output="")

    assert shell.run(found_nothing, "P", "dumpsys account", strict=True) == ""


def test_a_refused_command_without_strict_answers_empty_and_says_so(caplog):
    """The default, because most callers are doing something rather than
    asking something - and a tap that did not land is not worth ending a build
    over. It is still logged."""
    refused = Device(ok=False)

    with caplog.at_level("WARNING"):
        assert shell.run(refused, "P", "input tap 1 1") == ""

    assert any("shell reported failure" in r.message for r in caplog.records)


def test_a_command_that_answers_nothing_at_all_is_an_empty_string():
    """`.get("output")` on an answer with no output member, and on no answer
    at all. Either would be a None arriving in a caller that expects text."""
    assert shell.run(Device(output=None), "P", "echo") == ""


# ------------------------------------------------------- what may be repeated
def test_only_reads_are_retried():
    """`/shell/execute` is not retried by default, because a repeated
    `pm uninstall` or `input tap` acts twice."""
    doing = Device()
    shell.run(doing, "P", "input tap 1 1")
    assert doing.retries == [False]

    asking = Device()
    shell.read(asking, "P", "dumpsys account")
    assert asking.retries == [True]


def test_a_read_carries_the_caller_s_strictness_through():
    """`read` is `run` with retry on - it must not quietly drop the other
    half, or a strict read stops being strict."""
    refused = Device(ok=False)

    with pytest.raises(shell.ShellError):
        shell.read(refused, "P", "dumpsys account", strict=True)


# --------------------------------------------------------- what is in front
def test_the_app_in_front_is_read_out_of_the_window_dump():
    device = Device(output=(
        "  mCurrentFocus=Window{a1b2 u0 com.openai.chatgpt/"
        "com.openai.chatgpt.MainActivity}"))

    assert shell.foreground_package(device, "P") == "com.openai.chatgpt"


def test_a_dump_that_names_nothing_answers_nothing():
    """Empty is "the device would not say", and every caller treats that as a
    diagnostic that is unavailable rather than as evidence."""
    assert shell.foreground_package(Device(output=""), "P") == ""


def test_a_device_that_will_not_answer_does_not_take_the_caller_with_it():
    """This is asked to decide whether an app came up, on paths that are
    already handling a failure. Raising here turns a diagnostic into the
    error."""
    broken = Device(explode=RuntimeError("no connection"))

    assert shell.foreground_package(broken, "P") == ""


# ------------------------------------------------------------- what can be typed
def test_text_that_cannot_be_typed_is_refused_before_a_phone_exists():
    """`input text` is ASCII only. A row that cannot be typed should fail in
    validation, not halfway through a login - a password that types wrong
    looks identical to a wrong password, and costs an attempt against the
    account's reputation to discover."""
    with pytest.raises(shell.TypingError) as caught:
        shell.check_typeable("passw\u00f6rd")

    said = str(caught.value)
    assert "\u00f6" in said, "which character is the whole of the fix"
    assert "ADBKeyboard" in said, "and what to do about it"


def test_ordinary_ascii_passes():
    shell.check_typeable("Hunter2!#$%^&*()_+-=[]{}|;:',.<>?/~`")
    shell.check_typeable("")


def test_nothing_is_typed_for_an_empty_string():
    """A `input text ""` is a shell call spent saying nothing, and every one
    of them is drawn from a process-wide budget."""
    device = Device()

    shell.type_text(device, "P", "")

    assert device.commands == []


def test_text_is_checked_before_any_of_it_is_sent():
    """Half a password typed and then refused leaves the field holding
    something the account has never had."""
    device = Device()

    with pytest.raises(shell.TypingError):
        shell.type_text(device, "P", "pass\u00f6rd")

    assert device.commands == [], "it sent part of it before checking"
