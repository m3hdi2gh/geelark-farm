"""Run shell commands on a phone, and answer questions about its real state.

This module is the project's source of truth. GeeLark's RPA tasks report
success without having acted, so every flow confirms its result here instead of
trusting a task status.

Text entry deserves the attention it gets below: `input text` is the only way
to type, and it mangles spaces and shell metacharacters. Google passwords
contain both.
"""

from __future__ import annotations

import logging
import random
import re
import shlex
import time

from .api import Client

log = logging.getLogger(__name__)

# dumpsys prints one of these per real account. 'com.google' alone is NOT
# evidence of an account - it is the registered authenticator type, present on
# a device with none. Matching it produced a false "signed in" reading once.
ACCOUNT_RE = re.compile(r"name=([^\s,}]+@[^\s,}]+)")


class ShellError(Exception):
    """The device said the command did not run."""


def run(client: Client, phone_id: str, cmd: str, *, retry: bool = False,
        strict: bool = False) -> str:
    """Execute `cmd` on the phone and return stdout.

    retry=True is for commands the caller knows are read-only; /shell/execute
    is not retried by default because a repeated `pm uninstall` or `input tap`
    would act twice.

    strict=True raises instead of returning what a failed command produced,
    which is nothing. Without it "the command did not run" and "the command
    ran and found nothing" arrive as the same empty string - and for the two
    questions this module exists to answer, those are opposite answers with
    the same consequence. A failed `dumpsys account` reads as "nobody is
    signed in", which is the reading that condemns a Gmail that is fine.
    """
    data = client.data("/v1/shell/execute", {"id": phone_id, "cmd": cmd},
                       retry=retry) or {}
    if not data.get("status"):
        if strict:
            raise ShellError(f"the phone would not run {cmd!r}")
        log.warning("shell reported failure for %r", cmd)
    return data.get("output") or ""


def read(client: Client, phone_id: str, cmd: str, *,
         strict: bool = False) -> str:
    """run() for commands with no side effects, so they can be retried."""
    return run(client, phone_id, cmd, retry=True, strict=strict)


# ------------------------------------------------------------ verification
def device_accounts(client: Client, phone_id: str, *,
                    strict: bool = True) -> list[str]:
    """Google accounts actually present on the device, lowercased.

    Strict by default, because an empty answer here is usually a verdict:
    this is the check that says whether Google is signed in, and a command
    that did not run says "nobody is" just as convincingly as one that ran
    and found nobody. That reading condemns an account that is fine.

    `strict=False` is for the callers polling in a loop, where an empty
    answer means "not yet" and the next look is a few seconds away. Raising
    there ends a whole login over one bad poll, which is the opposite of what
    the strictness is for. The default stays strict so a new caller inherits
    the safe reading and has to say when it does not want it.
    """
    output = read(client, phone_id, "dumpsys account", strict=strict)
    return sorted({m.lower() for m in ACCOUNT_RE.findall(output)})


def foreground_package(client: Client, phone_id: str) -> str:
    """Which app is in front, or "" if the device will not say.

    Asking the device rather than reading the screen, because "this is not my
    app" is exactly the judgement a screen cannot be trusted to make - an
    unrecognised page looks the same whether the app is showing something new
    or was never brought to the front at all.

    Empty on any doubt: a caller that cannot find out should carry on rather
    than act on a guess.
    """
    try:
        out = read(client, phone_id,
                   "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'")
    except Exception:                                             # noqa: BLE001
        return ""
    found = re.search(r"([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)/", out)
    return found.group(1) if found else ""


def package_installed(client: Client, phone_id: str, package: str, *,
                      strict: bool = True) -> bool:
    """Whether `package` is really installed. The only acceptable proof.

    Strict and lenient for the same reasons as `device_accounts`: proof by
    default, `strict=False` where this is a poll waiting for a download.
    """
    output = read(client, phone_id, f"pm list packages {shlex.quote(package)}",
                  strict=strict)
    return f"package:{package}" in output


def third_party_packages(client: Client, phone_id: str) -> list[str]:
    output = read(client, phone_id, "pm list packages -3")
    return sorted(line.removeprefix("package:").strip()
                  for line in output.splitlines() if line.startswith("package:"))


# -------------------------------------------------------------- interaction
#: Touches written into the phone's own touch device (KERNEL_TOUCH), in
#: the shape the GeeLark viewer's touches have, instead of `input tap`.
#:
#: `input tap` never reaches the kernel: it is a MotionEvent injected
#: straight into the input pipeline, DOWN and UP in the same instant, with
#: no movement and a pressure and size of exactly 1.0 (AOSP's constants),
#: from a device that is not the touchscreen. A person's tap on the
#: GeeLark viewer, recorded with `getevent` on phones 2293 and 2294
#: (2026-09-10, 117 touches): DOWN with a tracking id and a position, UP
#: after 55-270 ms (median 135), no pressure and no size at all, no
#: jitter - and, after a quarter of the UPs, three spare "released"
#: frames some 400 ms later. A swipe is 20-60 position frames over
#: 450-900 ms. The operator's sign-ins pass without a challenge and the
#: builder's do not, and this is the first of the three differences the
#: recordings left standing.
#:
#: Each frame is one binary write of `struct input_event`s with `printf`
#: - one process per frame. `sendevent` is one process per *event*, and
#: on these phones a process costs about 65 ms: a four-event UP frame
#: alone took a quarter of a second, the dwell passed the long-press
#: threshold, and the first build tapped "I agree" eight times to no
#: effect (2026-09-11, phone 2297). With one write per frame the dwell
#: is `sleep` plus the spawn of `sleep` and the UP's own write - about
#: 75 ms - and lands where it is aimed.
#:
#: Off in this module so the suite never shells out; `serve` turns it on
#: from `KERNEL_TOUCH` (default on). A phone whose device cannot be
#: written to falls back to `input tap` once and for all - the probe is
#: one shell call per phone.
KERNEL_TOUCH = False
TOUCH_DEVICE = "/dev/input/event0"
#: Linux input event codes the recordings used - nothing else.
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT, SYN_MT_REPORT = 0, 2
BTN_TOUCH = 330
ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 53, 54, 57
#: The measured tap: dwell in ms (log-normal, median 135, p10 79, p90
#: 221), and the odds and timing of the spare release frames.
TAP_DWELL_MEDIAN_MS = 135.0
TAP_DWELL_SIGMA = 0.40
TAP_DWELL_MIN_MS, TAP_DWELL_MAX_MS = 55.0, 270.0
TAP_SPARE_ODDS = 0.25
TAP_SPARE_AFTER_MS = (300.0, 500.0)
#: What a process costs on the phone, measured with getevent: a `sleep`
#: of 80 ms put 153 ms between DOWN and UP, 200 ms put 286 (2026-09-11).
#: Subtracted from the dwell asked for; the floor is what one write
#: after another can do.
SPAWN_MS = 75.0
#: The measured swipe, in frames this can afford: one write per frame is
#: one spawn, so a 450-900 ms swipe is 7-14 frames.
SWIPE_MS = (450.0, 900.0)
_touch_ready: dict[str, tuple[float, float] | None] = {}


def _tracking_id() -> int:
    return int(time.time() * 10) % 60000 + 100


def _event(kind: int, code: int, value: int) -> bytes:
    """One `struct input_event` as the 64-bit kernel reads it: a zero
    timestamp (the kernel stamps writes itself), then type, code, value,
    little-endian."""
    import struct

    return struct.pack("<qqHHi", 0, 0, kind, code, value)


def _frame(*events: tuple[int, int, int]) -> str:
    """Events plus the two SYNs that close a frame, as one `printf` into
    the device. Octal escapes: `printf` decodes them and nothing is
    quoted by mistake."""
    raw = b"".join(_event(*e) for e in events)
    raw += _event(EV_SYN, SYN_MT_REPORT, 0) + _event(EV_SYN, SYN_REPORT, 0)
    return ("printf '" + "".join("\\%03o" % b for b in raw) + "' > "
            + TOUCH_DEVICE)


def tap_events(x: int, y: int, *, dwell_ms: float, tracking_id: int,
               spare_after_ms: float | None,
               scale: tuple[float, float] = (1.0, 1.0)) -> str:
    """One tap as a shell script: the DOWN frame, a sleep, the UP frame,
    and sometimes the viewer's spare release frames. Coordinates are
    screen pixels; `scale` maps them onto the device's axis ranges."""
    dx, dy = int(round(x * scale[0])), int(round(y * scale[1]))
    lines = [_frame((EV_ABS, ABS_MT_TRACKING_ID, tracking_id),
                    (EV_ABS, ABS_MT_POSITION_X, dx),
                    (EV_ABS, ABS_MT_POSITION_Y, dy),
                    (EV_KEY, BTN_TOUCH, 1))]
    nap = max(0.0, dwell_ms - SPAWN_MS) / 1000
    if nap >= 0.005:
        lines.append(f"sleep {nap:.3f}")
    lines.append(_frame((EV_ABS, ABS_MT_TRACKING_ID, -1),
                        (EV_KEY, BTN_TOUCH, 0)))
    if spare_after_ms is not None:
        lines.append(f"sleep {spare_after_ms / 1000:.3f}")
        lines += [_frame((EV_ABS, ABS_MT_TRACKING_ID, -1))] * 3
    return "; ".join(lines)


def swipe_events(x1: int, y1: int, x2: int, y2: int, *, frames: int,
                 tracking_id: int,
                 scale: tuple[float, float] = (1.0, 1.0)) -> str:
    """One swipe: DOWN at the start, `frames` eased position frames, UP
    at the end - a thumb that starts slowly, moves, and slows down. No
    sleeps: each frame's own write is the pacing."""
    frames = max(2, int(frames))
    sx, sy = scale

    def at(k: int) -> tuple[int, int]:
        t = k / frames
        ease = t * t * (3 - 2 * t)                 # smoothstep
        return (int(round((x1 + (x2 - x1) * ease) * sx)),
                int(round((y1 + (y2 - y1) * ease) * sy)))

    x0, y0 = at(0)
    lines = [_frame((EV_ABS, ABS_MT_TRACKING_ID, tracking_id),
                    (EV_ABS, ABS_MT_POSITION_X, x0),
                    (EV_ABS, ABS_MT_POSITION_Y, y0),
                    (EV_KEY, BTN_TOUCH, 1))]
    for k in range(1, frames + 1):
        px, py = at(k)
        lines.append(_frame((EV_ABS, ABS_MT_POSITION_X, px),
                            (EV_ABS, ABS_MT_POSITION_Y, py)))
    lines.append(_frame((EV_ABS, ABS_MT_TRACKING_ID, -1),
                        (EV_KEY, BTN_TOUCH, 0)))
    return "; ".join(lines)


def touch_scale(probe: str) -> tuple[float, float] | None:
    """The screen-to-device scale from one probe's output, or None when
    the device cannot be used. The probe prints `wm size`, then the
    touch device's X and Y axis lines from `getevent -p`, then OK."""
    if "OK" not in probe:
        return None
    m = re.search(r"size:\s*(\d+)x(\d+)", probe)
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    axes = {}
    for code, mx in re.findall(r"^\s*(0035|0036)\s*:.*?max\s+(\d+)", probe,
                               flags=re.M):
        axes[code] = int(mx)
    if not w or not h:
        return None
    sx = axes.get("0035", w) / w if axes.get("0035") else 1.0
    sy = axes.get("0036", h) / h if axes.get("0036") else 1.0
    return (sx or 1.0, sy or 1.0)


def _kernel_touch(client: Client, phone_id: str) -> tuple[float, float] | None:
    """Whether this phone's touch device takes our events, probed once."""
    if phone_id in _touch_ready:
        return _touch_ready[phone_id]
    try:
        probe = read(client, phone_id,
                     f"test -w {TOUCH_DEVICE} && which printf >/dev/null && "
                     f"wm size && getevent -p {TOUCH_DEVICE} | grep -E "
                     f"'^ *003[56]' && echo OK")
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not probe the touch device of %s (%s); taps go "
                    "through `input tap`", phone_id, exc)
        probe = ""
    scale = touch_scale(probe or "")
    if scale is None:
        log.warning("phone %s: the touch device cannot be written to; taps "
                    "go through `input tap` (probe: %r)", phone_id,
                    (probe or "")[:120])
    else:
        log.info("phone %s: taps go into %s (scale %.2fx%.2f)", phone_id,
                 TOUCH_DEVICE, *scale)
    _touch_ready[phone_id] = scale
    return scale


def _dwell_ms() -> float:
    if not HUMAN_CADENCE:
        return TAP_DWELL_MEDIAN_MS
    import math

    value = math.exp(math.log(TAP_DWELL_MEDIAN_MS)
                     + _rng.gauss(0.0, TAP_DWELL_SIGMA))
    return min(TAP_DWELL_MAX_MS, max(TAP_DWELL_MIN_MS, value))


def tap(client: Client, phone_id: str, x: int, y: int) -> None:
    scale = _kernel_touch(client, phone_id) if KERNEL_TOUCH else None
    if scale is None:
        run(client, phone_id, f"input tap {x} {y}")
        return
    spare = None
    if HUMAN_CADENCE and _rng.random() < TAP_SPARE_ODDS:
        spare = _rng.uniform(*TAP_SPARE_AFTER_MS)
    run(client, phone_id, tap_events(
        x, y, dwell_ms=_dwell_ms(), tracking_id=_tracking_id(),
        spare_after_ms=spare, scale=scale))


def swipe(client: Client, phone_id: str, x1: int, y1: int, x2: int, y2: int,
          *, seconds: float | None = None) -> None:
    """A swipe: eased kernel frames when the device takes them, else
    `input swipe`."""
    scale = _kernel_touch(client, phone_id) if KERNEL_TOUCH else None
    if HUMAN_CADENCE:
        total = (seconds * 1000) if seconds else _rng.uniform(*SWIPE_MS)
    else:
        total = (seconds * 1000) if seconds else sum(SWIPE_MS) / 2
    if scale is None:
        run(client, phone_id,
            f"input swipe {x1} {y1} {x2} {y2} {int(total)}")
        return
    frames = max(4, int(round(total / SPAWN_MS)))
    run(client, phone_id, swipe_events(x1, y1, x2, y2, frames=frames,
                                       tracking_id=_tracking_id(),
                                       scale=scale))


def keyevent(client: Client, phone_id: str, code: int) -> None:
    """Send a key. 66 = ENTER, 61 = TAB, 4 = BACK, 67 = DEL."""
    run(client, phone_id, f"input keyevent {code}")


def launch_url(client: Client, phone_id: str, url: str) -> str:
    """Open a URL or deep link with the default handler."""
    return run(client, phone_id,
               f"am start -a android.intent.action.VIEW -d {shlex.quote(url)}")


def force_stop(client: Client, phone_id: str, package: str) -> None:
    run(client, phone_id, f"am force-stop {shlex.quote(package)}")


# --------------------------------------------------------------- typing
# `input text` has two independent hazards, and both must be handled or a
# password silently types as something else:
#
#   1. The shell. /shell/execute runs the string through a shell, so $ ` \ " '
#      and friends are interpreted before `input` ever sees them. Fixed by
#      single-quoting the whole argument (shlex.quote).
#   2. `input text` itself. It splits its argument on spaces, and decodes %s as
#      a space. So a literal space must be sent as %s, and a literal % must be
#      avoided entirely - there is no escape for it.
#
# Anything outside printable ASCII cannot be typed this way at all.
_TYPEABLE = re.compile(r"^[\x20-\x7e]*$")


class TypingError(Exception):
    """The text cannot be typed reliably on this device."""


def check_typeable(text: str) -> None:
    """Raise TypingError if `text` cannot be typed exactly as given.

    Separate from type_text so a password can be validated offline, before a
    phone is created for it - a row that cannot be typed should fail in
    validation, not halfway through a login.
    """
    if not _TYPEABLE.match(text):
        bad = sorted({c for c in text if not _TYPEABLE.match(c)})
        raise TypingError(
            f"cannot type non-ASCII characters {bad} with `input text`; "
            f"an IME such as ADBKeyboard would be required"
        )


def type_segments(text: str) -> list[str]:
    """Split `text` into payloads that `input text` will type verbatim.

    Two facts about `input text` drive this (AOSP `Input.java`):

    - it turns the two-character sequence `%s` into a space, and deletes the
      `%`;
    - a `%` followed by anything else - including the end of the argument - is
      left alone.

    So a space is sent as `%s`, and a literal `%` is safe unless an `s` happens
    to follow it. That one case is handled by ending the call right after the
    `%` and starting the next one with the `s`, which types both literally.

    This replaces a blanket refusal of `%`, which had blocked an otherwise
    perfectly good account whose password contained one.
    """
    segments: list[str] = []
    current = ""
    for char in text:
        piece = "%s" if char == " " else char
        if current.endswith("%") and piece.startswith("s"):
            segments.append(current)
            current = ""
        current += piece
    if current:
        segments.append(current)
    return segments


#: Type and tap the way a hand does, or the way a script does.
#:
#: Off, a field is filled in one `input text` and every tap lands on the
#: exact centre of its button, the same pixel every time. Google's sign-in
#: page scores exactly that: the operator signed the same accounts into
#: fresh GeeLark phones through the same exits by hand and met no captcha
#: and no "Verify your phone number", while the builder met both on
#: thirteen of sixteen phones (2026-09-09). On, text goes in bursts of two
#: to four characters with a short pause between, taps land at a point
#: inside the control rather than on its centre, and there is a moment
#: before a form is submitted. Off in this module so the test suite does
#: not sleep; `serve` turns it on from `HUMAN_CADENCE` (default on).
HUMAN_CADENCE = False
_rng = random.Random()


def pause(low: float, high: float) -> None:
    """A hand's hesitation, when the cadence is on; nothing when it is off."""
    if HUMAN_CADENCE:
        time.sleep(_rng.uniform(low, high))


def bursts(payload: str) -> list[str]:
    """One `input text` payload cut the way a hand types it: two to four
    keys at a time. `%s` is one key - a space - and never split, because
    `%` and `s` typed in two calls are the two characters, not the space
    (see `type_segments`)."""
    if not HUMAN_CADENCE:
        return [payload]
    keys = re.findall(r"%s|.", payload, flags=re.DOTALL)
    out: list[str] = []
    at = 0
    while at < len(keys):
        take = _rng.randint(2, 4)
        out.append("".join(keys[at:at + take]))
        at += take
    return out


def human_point(centre: tuple[int, int],
                bounds: tuple[int, int, int, int] | None) -> tuple[int, int]:
    """Where a thumb lands on a control: near the middle, not on it.

    Inside the middle three fifths of the control on each axis, so a small
    target - the reCAPTCHA tick box is forty pixels across - is still hit,
    and a wide button is not always pressed at the same pixel.
    """
    if not HUMAN_CADENCE or bounds is None:
        return centre
    left, top, right, bottom = bounds
    x = centre[0] + int(round(_rng.uniform(-0.3, 0.3) * max(0, right - left) / 2))
    y = centre[1] + int(round(_rng.uniform(-0.3, 0.3) * max(0, bottom - top) / 2))
    return (min(max(x, left + 1), right - 1) if right > left + 2 else centre[0],
            min(max(y, top + 1), bottom - 1) if bottom > top + 2 else centre[1])


def type_text(client: Client, phone_id: str, text: str) -> None:
    """Type `text` into the focused field, exactly as given.

    Raises TypingError rather than typing something subtly different - a
    password that types wrong looks identical to a wrong password, and costs a
    login attempt against an account's reputation to discover.

    In bursts, with the cadence on - see `HUMAN_CADENCE`.
    """
    if not text:
        return
    check_typeable(text)
    for payload in type_segments(text):
        for burst in bursts(payload):
            run(client, phone_id, f"input text {shlex.quote(burst)}")
            pause(0.12, 0.4)


MOVE_END = 123          # KEYCODE_MOVE_END
BACKSPACE = 67          # KEYCODE_DEL, deletes to the LEFT of the cursor
FORWARD_DELETE = 112    # KEYCODE_FORWARD_DEL, deletes to the right


def clear_field(client: Client, phone_id: str, max_chars: int = 64) -> None:
    """Empty the focused field.

    select-all + delete is unreliable across keyboards, so this deletes
    character by character - but backspace only removes what is to the LEFT of
    the cursor, and a field is focused by tapping it, which puts the cursor
    wherever the tap landed. On a filled field that is the middle of the text,
    so everything to the right survived: an email box was retyped four times
    and grew "com" on each pass, until the address in it was
    `...@gmail.comcomcom` (2026-08-08, row 7).

    So the cursor is sent to the end first, and forward deletes follow the
    backspaces. Either alone would do if the other always worked - together
    they hold whether or not a web view honours MOVE_END.
    """
    keys = ([MOVE_END] + [BACKSPACE] * max_chars + [FORWARD_DELETE] * max_chars)
    run(client, phone_id,
        f"input keyevent {' '.join(str(k) for k in keys)}")
