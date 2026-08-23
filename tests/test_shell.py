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

    def __init__(self, *, output="", ok=True):
        self.output, self.ok = output, ok
        self.commands: list[str] = []

    def data(self, path, payload=None, **kwargs):
        self.commands.append(payload["cmd"])
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
