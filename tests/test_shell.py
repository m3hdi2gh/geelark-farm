"""What the device is asked, and what an unanswered question is worth.

This module is where the project decides what is true - whether Google is
signed in, whether the app is really installed. Both answers are read out of
a command's output, so a command that never ran produces the same empty
string as one that ran and found nothing. Those are opposite answers, and one
of them condemns a perfectly good account.
"""

from __future__ import annotations

import re
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


# ------------------------------------------------------- a hand's cadence
def test_with_the_cadence_off_typing_and_tapping_are_what_they_were(
        monkeypatch):
    monkeypatch.setattr(shell, "HUMAN_CADENCE", False)
    assert shell.bursts("hello%sworld") == ["hello%sworld"]
    assert shell.human_point((50, 50), (0, 0, 100, 100)) == (50, 50)
    naps = []
    monkeypatch.setattr(shell.time, "sleep", lambda s: naps.append(s))
    shell.pause(0.5, 1.0)
    assert naps == []


def test_with_the_cadence_on_text_goes_in_bursts_that_keep_a_space_whole(
        monkeypatch):
    """The operator signed the same accounts into fresh phones through the
    same exits by hand and met no captcha; the builder, which pastes a
    field in one call and taps the same pixel every time, met one on
    thirteen of sixteen phones (2026-09-09). `%s` is one key - a space -
    and split in two it types the two characters instead."""
    import random

    monkeypatch.setattr(shell, "HUMAN_CADENCE", True)
    monkeypatch.setattr(shell, "_rng", random.Random(7))
    naps = []
    monkeypatch.setattr(shell.time, "sleep", lambda s: naps.append(s))

    made = shell.bursts("ab%scd%sefghij")
    assert "".join(made) == "ab%scd%sefghij"
    assert all(1 <= len(re.findall(r"%s|.", b)) <= 4 for b in made)
    assert all("%" not in b or "%s" in b for b in made), "a space stays whole"
    assert len(made) >= 3

    class Device:
        def __init__(self):
            self.typed = []

        def post(self, path, payload=None, **k):
            self.typed.append(payload["cmd"] if payload else path)
            return {"code": 0, "data": {"output": ""}}

    device = Device()
    monkeypatch.setattr(shell, "run",
                        lambda c, p, cmd, **k: device.typed.append(cmd) or "")
    shell.type_text(device, "P", "someone@example.com")
    assert len(device.typed) >= 5, "several `input text` calls, not one"
    assert "".join(c.removeprefix("input text ").strip("'")
                   for c in device.typed) == "someone@example.com"
    assert naps and all(0.12 <= n <= 0.4 for n in naps)


def test_with_the_cadence_on_a_tap_lands_inside_the_control_not_on_its_centre(
        monkeypatch):
    import random

    monkeypatch.setattr(shell, "HUMAN_CADENCE", True)
    monkeypatch.setattr(shell, "_rng", random.Random(3))
    points = {shell.human_point((360, 518), (200, 490, 520, 546))
              for _ in range(40)}
    assert len(points) > 5, "not the same pixel every time"
    for x, y in points:
        assert 200 < x < 520 and 490 < y < 546, "always inside the button"
        assert abs(x - 360) <= 48 and abs(y - 518) <= 9, "near the middle"
    # A tiny target - the reCAPTCHA tick box - is still hit.
    for _ in range(40):
        x, y = shell.human_point((82, 564), (62, 544, 102, 584))
        assert 62 < x < 102 and 544 < y < 584


# ------------------------------------------- kernel touches (2026-09-10)
def test_a_kernel_tap_has_the_viewers_exact_frame_order():
    """Recorded with getevent on phones 2293 and 2294: DOWN with a tracking
    id and a position, one repeated position frame, UP after the dwell,
    and sometimes three spare release frames later. No pressure, no
    size, no jitter - the viewer sends none."""
    script = shell.tap_events(360, 700, dwell_ms=135, repeat_ms=40,
                              tracking_id=154, spare_after_ms=None)
    steps = script.split("; ")
    d = f"sendevent {shell.TOUCH_DEVICE}"
    assert steps[:6] == [f"{d} 3 57 154", f"{d} 3 53 360", f"{d} 3 54 700",
                         f"{d} 1 330 1", f"{d} 0 2 0", f"{d} 0 0 0"]
    assert steps[6] == "sleep 0.040"
    assert steps[7:11] == [f"{d} 3 53 360", f"{d} 3 54 700", f"{d} 0 2 0",
                           f"{d} 0 0 0"]
    assert steps[11] == "sleep 0.095", "the rest of the dwell"
    assert steps[12:] == [f"{d} 3 57 -1", f"{d} 0 2 0", f"{d} 1 330 0",
                          f"{d} 0 0 0"]
    assert "pressure" not in script and " 58 " not in script and " 48 " not in script
    spare = shell.tap_events(1, 1, dwell_ms=100, repeat_ms=30, tracking_id=1,
                             spare_after_ms=420).split("; ")
    assert spare[16] == "sleep 0.420"
    assert spare[17:20] == [f"{d} 3 57 -1", f"{d} 0 2 0", f"{d} 0 0 0"]
    assert spare.count(f"{d} 3 57 -1") == 4, "the UP's and three spare ones"


def test_a_kernel_tap_is_scaled_onto_the_devices_axes():
    script = shell.tap_events(360, 700, dwell_ms=100, repeat_ms=30,
                              tracking_id=1, spare_after_ms=None,
                              scale=(1.5, 2.0))
    assert f"sendevent {shell.TOUCH_DEVICE} 3 53 540" in script
    assert f"sendevent {shell.TOUCH_DEVICE} 3 54 1400" in script


def test_a_kernel_swipe_eases_through_its_frames():
    script = shell.swipe_events(100, 1000, 100, 400, frames=10, total_ms=500,
                                tracking_id=7)
    steps = script.split("; ")
    ys = [int(s.rsplit(" ", 1)[1]) for s in steps
          if s.startswith(f"sendevent {shell.TOUCH_DEVICE} 3 54 ")]
    assert ys[0] == 1000 and ys[-1] == 400 and len(ys) == 11
    assert ys == sorted(ys, reverse=True), "monotonic"
    gaps = [a - b for a, b in zip(ys, ys[1:])]
    assert gaps[0] < gaps[4] and gaps[-1] < gaps[4], "slow, fast, slow"
    assert steps.count("sleep 0.050") == 10
    assert steps[-4:] == [f"sendevent {shell.TOUCH_DEVICE} 3 57 -1",
                          f"sendevent {shell.TOUCH_DEVICE} 0 2 0",
                          f"sendevent {shell.TOUCH_DEVICE} 1 330 0",
                          f"sendevent {shell.TOUCH_DEVICE} 0 0 0"]


def test_the_touch_device_is_probed_once_and_read_for_its_scale():
    probe = ("Physical size: 720x1440\n"
             "    0035  : value 0, min 0, max 720, fuzz 0, flat 0, resolution 0\n"
             "    0036  : value 0, min 0, max 1440, fuzz 0, flat 0, resolution 0\n"
             "OK\n")
    assert shell.touch_scale(probe) == (1.0, 1.0)
    wide = probe.replace("max 720", "max 1080").replace("max 1440", "max 2160")
    assert shell.touch_scale(wide) == (1.5, 1.5)
    assert shell.touch_scale(probe.replace("OK", "")) is None, "no device"
    assert shell.touch_scale("") is None
    assert shell.touch_scale("Physical size: 720x1440\nOK\n") == (1.0, 1.0), (
        "axes unreadable: assume the screen's pixels")


def test_taps_go_into_the_device_when_it_takes_them_and_fall_back_otherwise(
        monkeypatch):
    sent = []
    monkeypatch.setattr(shell, "run",
                        lambda client, phone_id, cmd, **k: sent.append(cmd) or "")
    monkeypatch.setattr(shell, "read",
                        lambda client, phone_id, cmd, **k: (
                            "Physical size: 720x1440\nOK\n"
                            if phone_id == "good" else "sh: not found"))
    monkeypatch.setattr(shell, "_touch_ready", {})
    monkeypatch.setattr(shell, "HUMAN_CADENCE", False)

    monkeypatch.setattr(shell, "KERNEL_TOUCH", False)
    shell.tap(None, "good", 10, 20)
    assert sent == ["input tap 10 20"], "off: what it always was"

    sent.clear()
    monkeypatch.setattr(shell, "KERNEL_TOUCH", True)
    shell.tap(None, "good", 10, 20)
    shell.tap(None, "good", 11, 21)
    assert all(c.startswith(f"sendevent {shell.TOUCH_DEVICE} 3 57 ")
               for c in sent), sent
    assert "sleep 0.048" in sent[0] and "sleep 0.087" in sent[0], (
        "the cadence off: the median dwell of 135 ms, the repeat at 48")
    assert shell._touch_ready["good"] == (1.0, 1.0)

    sent.clear()
    shell.tap(None, "bad", 10, 20)
    shell.tap(None, "bad", 10, 20)
    assert sent == ["input tap 10 20", "input tap 10 20"]
    assert shell._touch_ready["bad"] is None, "probed once, remembered"

    sent.clear()
    shell.swipe(None, "good", 100, 900, 100, 300)
    assert sent[0].count(f"sendevent {shell.TOUCH_DEVICE} 3 54 ") == 40
    shell.swipe(None, "bad", 100, 900, 100, 300, seconds=0.5)
    assert sent[1] == "input swipe 100 900 100 300 500"


def test_with_the_cadence_on_the_dwell_is_the_recorded_distribution(monkeypatch):
    import random

    monkeypatch.setattr(shell, "HUMAN_CADENCE", True)
    monkeypatch.setattr(shell, "_rng", random.Random(3))
    dwells = sorted(shell._dwell_ms() for _ in range(2000))
    assert shell.TAP_DWELL_MIN_MS <= dwells[0] and dwells[-1] <= shell.TAP_DWELL_MAX_MS
    assert 115 <= dwells[1000] <= 155, "median about 135 ms"
    assert 65 <= dwells[200] <= 100 and 190 <= dwells[1800] <= 260

