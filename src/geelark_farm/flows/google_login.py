"""Sign a Google account into the device, handling every screen Google shows.

Written by hand rather than using GeeLark's googleLogin RPA task, because that
task cannot be extended: it never reaches "Try another way", ships with
placeholder OCR credentials, and reports success while the device is stranded on
a verification screen.

## Why a router and not a script

Google does not present its login screens in a fixed order. Consent pages
appear or do not, a second factor may be a code prompt or a push to another
device, and any step can be followed by an interstitial. A linear script breaks
on the first variation; this is a loop:

    while within budget:
        if the account is on the device      -> success
        read the screen
        find the first registry entry that matches it
        act, or stop with a named reason

Each SCREEN below is one page: a name, a predicate over the parsed elements, an
action, and whether it is terminal. Supporting a newly observed page means
adding one entry - the loop never changes.

## Outcomes

Success is only ever `dumpsys account` showing the expected address; screen text
claiming success is not evidence. Failures are named, so a run can act on them:
`captcha_shown` needs a better IP, `wrong_password` needs a corrected row, and
`unknown_screen` needs a new registry entry and ships its XML to prove it.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import accounts, phones, screen, shell
from ..accounts import Account
from ..api import Client
from . import router
from .router import Outcome, Screen, act_wait, fill, still_loading

log = logging.getLogger(__name__)

#: How often the loop asks the device whether the account has landed. Not
#: every pass: see `sign_in`. Short enough that the answer is never stale by
#: more than one screen capture.
ACCOUNT_CHECK_SECONDS = 8

# Consent and marketing pages: the label to press, in preference order. These
# are handled as one screen because they are interchangeable - each is "some
# page whose only job is to be dismissed".
#: Matched case-insensitively - `screen.find` casefolds both sides - so one
#: spelling of a word is every spelling of it. This carried `I AGREE`,
#: `ACCEPT` and `DON'T TURN ON` beside their lowercase twins, three entries
#: that could never be reached and an example for whoever adds the next one.
DISMISS_LABELS = (
    "I agree", "Agree", "Accept", "I understand",
    "Turn off", "Don't turn on", "No thanks", "Not now",
    "Skip", "Maybe later", "More", "Next", "Done", "Continue",
)

# Anything containing one of these is a dead end for an unattended run.
# Written in plain ASCII: screen.normalize() folds Google's typographic
# punctuation, so "couldn't" here matches "couldn’t" on screen.
FATAL_TEXTS = {
    "captcha_shown": (
        "confirm you're not a robot", "type the text you hear or see",
        "i'm not a robot",
    ),
    "wrong_password": ("wrong password", "incorrect password"),
    # Google accepts the address, then says the password on file is the old
    # one. Nothing on the device can fix that, and without this the flow simply
    # retyped the same password until the visit budget ran out and reported
    # stuck_on_password_entry - five minutes to not say "the password is stale"
    # (2026-08-06, rows 11 and 12).
    "password_changed": ("your password was changed",),
    # Google's risk check, not a broken account: it decided this device and
    # network are too unfamiliar to trust, and says so explicitly. Distinct
    # from a disabled account because the fix is different - the account needs
    # history on a device Google already trusts.
    "verification_blocked": (
        "didn't provide enough info",
        "use a device where you've signed in before",
    ),
    "account_disabled": (
        "account has been disabled", "account was disabled",
        "account has been locked",
    ),
    "sign_in_refused": ("couldn't sign you in",),
    "phone_verification_required": (
        "verify your phone number", "confirm your phone number",
        "enter a phone number", "get a verification code at",
    ),
    "email_not_found": (
        "couldn't find your google account", "enter a valid email",
    ),
    "too_many_attempts": ("too many failed attempts",),
    # Google rejecting the authenticator code. Without this the flow generated
    # a fresh one and sent it again, four times - four wrong codes against a
    # real account - before reporting stuck_on_2fa_code_entry, which names the
    # screen it was standing on (2026-08-10, row 15).
    "wrong_2fa_code": ("wrong code", "invalid code", "that code didn't work"),
}

# Detail lines for the reasons where the fix is not obvious from the name.
FATAL_ADVICE = {
    "captcha_shown":
        "Google is challenging this exit IP; a cleaner proxy is the fix",
    "password_changed":
        "the password in the sheet is the old one - Google says when it was "
        "changed on the archived screen",
    "verification_blocked":
        "Google refused a brand-new device on an unfamiliar network. The "
        "account needs prior history somewhere Google trusts - warming it up "
        "on this proxy first, or using accounts created on it - not a code fix",
    "sign_in_refused":
        "Google declined without saying why; check the screenshot",
    "phone_verification_required":
        "the account wants an SMS code, which this tool has no way to receive",
    "no_recovery_email":
        "Google asked this account to confirm the recovery address it already "
        "holds, and the row has none. The address is the whole answer - there "
        "is no code to fetch - so put it in the Recovery Email column of the "
        "Gmails tab. Nothing here can guess it",
    "no_authenticator":
        "this account has no 2FA secret in the sheet, and Google asked for a "
        "code anyway. Nothing here can produce one. Either the account does "
        "have 2FA and its secret is missing from the row, or Google decided "
        "this sign-in needed a second factor and the account cannot give one",
    "wrong_2fa_code":
        "Google rejected the authenticator code, so the totp_secret in the "
        "sheet is not this account's - a fresh code from a wrong secret is "
        "wrong every time, which is why this stops rather than retrying. "
        "Check the secret against the account's authenticator setup",
}


@dataclass
class Context(router.Context):
    """The generic context plus the account being signed in, and - when a
    key is set - the CapSolver door and the loop guard for the reCAPTCHA
    image grid Google sometimes throws."""
    account: Account = None                                     # type: ignore
    solver_key: str = ""
    captcha_max: int = 3
    #: Attempts actually made - a tick, or a grid sent to the solver.
    #: Waiting for reCAPTCHA to answer is not one of them.
    captcha_tries: int = 0
    #: The visit the tick box was last tapped on. Tapped again on the next
    #: visit it would untick what it just ticked; never tapped again at all,
    #: a challenge that closes itself is waited out to the limit.
    captcha_ticked_on: int | None = None
    #: Whether a grid this cannot place has already been saved once.
    captcha_unplaced: bool = False
    #: How many captchas this flow had met when the counters were last
    #: reset, so a fresh captcha starts with fresh rounds.
    captcha_met: int = 0
    #: Visits spent waiting for a grid to finish drawing itself.
    captcha_waited: int = 0


# --------------------------------------------------------------- primitives
def submit(ctx: Context) -> None:
    """Advance the form. Google labels the button Next, but the on-screen
    keyboard's enter key works when the button is scrolled out of view."""
    ctx.refresh()
    # One spelling each: `screen.find` casefolds, so `NEXT` after `Next` was
    # a second look for a label the first had already matched.
    for label in ("Next", "Continue", "Sign in", "Verify", "Done"):
        if ctx.tap(label):
            return
    shell.keyevent(ctx.client, ctx.phone_id, 66)      # ENTER


# ------------------------------------------------------------------ screens
# Reasons that describe one option among several rather than a verdict on the
# page. Google's 2-Step list puts "Get a verification code at •••••34" - which
# this tool cannot receive - directly beside "Get a verification code from the
# Google Authenticator app", which it can. Fatal is checked before every other
# entry, so matching the SMS row there killed a login that was one tap from
# working (2026-08-08, row 7).
#
# Everything else in FATAL_TEXTS is a statement about the whole page: a CAPTCHA,
# a changed password, a disabled account. Those stay fatal wherever they appear.
NOT_FATAL_BESIDE_AUTHENTICATOR = frozenset({"phone_verification_required"})

#: The row on Google's "Choose how you want to sign in" list that this tool can
#: answer from the sheet alone. Taken from a real screen (2026-08-29): the
#: options were "Get a verification code at eme•••@gmail.com", "Use another
#: phone or computer to finish signing in", "Confirm your recovery email" and
#: "Try another way", each a clickable View carrying the text as its
#: description.
#:
#: Only the third is answerable here. The first sends a code to that mailbox
#: and there is no inbox to read it from; the second wants a second device.
RECOVERY_OPTION_LABELS = ("Confirm your recovery email",)

#: The page that option opens: "Confirm the recovery email address you added to
#: your account:", a masked hint, and an empty box whose resource id is
#: `knowledge-preregistered-email-response`. Nothing is fetched - Google is
#: asking whether we know the address already on the account, and the row has
#: it.
RECOVERY_TEXTS = ("confirm the recovery email address",
                  "recovery email address you added")

#: The field on that page, by id. The text needles above are what a Google
#: rewording would break first; this is what it would break last.
RECOVERY_FIELD_ID = "knowledge-preregistered-email-response"

# Google's transient interstitial while it decides what to show next. No
# progress bar on it, so the generic check does not catch it, and there is
# nothing to act on - a row reported unknown_screen from this page having
# simply been read a second too early (2026-08-08, row 1).
CHECKING_TEXTS = ("checking info", "just a moment", "one moment")

# Where the add-account flow actually lives. Settings hosts it and Google Play
# services draws it, so either in front means the flow is still where it should
# be; anything else - a launcher, most likely - means it has been dropped out
# of and has to be started again.
SIGN_IN_PACKAGES = (
    "com.android.settings",
    "com.google.android.gms",
    "com.google.android.gsf",
)


def _fatal_reason(ctx: Context) -> str | None:
    for reason, needles in FATAL_TEXTS.items():
        if not ctx.has(*needles):
            continue
        if reason in NOT_FATAL_BESIDE_AUTHENTICATOR and answerable(ctx):
            continue
        # A captcha is not a dead end when a solver is configured: the
        # `captcha` screen tries it, and gives up - back to this same
        # reason - only after the attempt limit. Without a key it is
        # fatal exactly as before.
        if reason == "captcha_shown" and getattr(ctx, "solver_key", ""):
            continue
        return reason
    return None


#: The reCAPTCHA checkbox, and the grid it can open. The checkbox often
#: passes on a tap alone from a clean exit; the grid needs the solver.
_CAPTCHA_NEEDLES = ("confirm you're not a robot", "i'm not a robot",
                    "select all images", "select all squares")
_GRID_VERIFY = ("Verify", "Skip", "Next")


#: The lines that close the challenge's heading. Everything between the
#: "Select all …" line and one of these is the name of the thing to find.
#: Two wordings, one per grid shape: the 3x3 that refreshes says "click
#: verify once there are none left", the one-shot 4x4 says "if there are
#: none, click skip". Matching only the first read the whole rest of the
#: page into the question and left the heading's real foot unfound, which
#: is what put the grid rectangle out of reach (2026-09-05, phone 1793).
_GRID_TAIL = ("click verify", "click skip", "if there are none")


def _is_tail(text: str) -> bool:
    low = (text or "").lower()
    return any(needle in low for needle in _GRID_TAIL)


def _grid_instruction(ctx: Context) -> str:
    """The whole question - "Select all images with crosswalks" - assembled
    from the two nodes it is split across.

    Google renders the ask and the object as separate views: `Select all
    images with` on one line and `crosswalks` on the next. Reading only the
    first is reading the question without its subject, and CapSolver was
    handed "Select all images with" and no category (2026-09-06, build
    1781). Anything after the "Click verify once there are none left" line
    is not part of the question.
    """
    labels = [el.label or "" for el in ctx.elements]
    start = next((i for i, t in enumerate(labels)
                  if "select all" in t.lower()
                  and ("images" in t.lower() or "squares" in t.lower())), None)
    if start is None:
        return ""
    words = [labels[start]]
    for text in labels[start + 1:start + 4]:
        if (not text.strip() or _is_tail(text)
                or _TILE_LABEL in text.lower()):
            break
        words.append(text.strip())
    return " ".join(words)


#: Visits to leave the tick box alone after tapping it. reCAPTCHA takes a
#: few seconds to decide and reads unticked the whole time.
_TICK_AGAIN = 4


def _robot_checkbox(ctx: Context):
    """The reCAPTCHA tick box itself, not the words beside it.

    A CheckBox whose label says "not a robot" - the words on their own are
    a TextView the same page also carries, and tapping prose does nothing.
    """
    for el in ctx.elements:
        if ("checkbox" in (el.cls or "").lower()
                and "not a robot" in (el.label or "").lower()):
            return el
    return None


def _tile_points(rect: tuple[int, int, int, int], size: int,
                 indices: list[int]) -> list[tuple[int, int]]:
    """The device point at the centre of each named tile of a `size`x`size`
    grid filling `rect` (left, top, right, bottom). Pure, so the mapping
    can be checked without a phone."""
    left, top, right, bottom = rect
    if size < 1 or right <= left or bottom <= top:
        return []
    cw = (right - left) / size
    ch = (bottom - top) / size
    points = []
    for i in indices:
        if 0 <= i < size * size:
            col, row = i % size, i // size
            points.append((int(left + cw * (col + 0.5)),
                           int(top + ch * (row + 0.5))))
    return points


#: How far apart the lightest and darkest pixel of a line must be before
#: it counts as part of a photograph. A tile is a street scene and clears
#: this easily; the banner's flat blue and the card's white are 0.
_TILE_CONTRAST = 40

#: How big the picture has to be for CapSolver to read each grid, which is
#: the size Google serves that grid at. The solver reads the shape *from
#: the size*: the same 4x4 sent at 300 came back read as a 3x3, naming
#: three tiles that had no crosswalk in them, and sent at the phone's own
#: 631 it would not read a grid at all (2026-09-06).
_GRID_PIXELS = {3: 300, 4: 450}

#: Pixels of flat colour that may sit inside the tile block without ending
#: it: the white lines between tiles, and a stray row or two of the
#: heading's text bleeding down when a screenshot is not the hierarchy's
#: own size. The block itself is hundreds of pixels tall, so bridging a
#: handful cannot join two real things together.
_TILE_GAP = 8


#: What each tile of the grid calls itself once the WebView has finished
#: laying the challenge out. On the first visit they are not there at all,
#: which is why the picture can still be scanned instead.
_TILE_LABEL = "image challenge"


def _tile_buttons(ctx: Context) -> list:
    """The grid's own tiles, in reading order, when the page offers them.

    reCAPTCHA's tiles arrive in the accessibility tree a moment after the
    heading does - sixteen buttons all called `Image challenge`, each with
    its own bounds. When they are there they are better than anything a
    picture can be scanned for: the rectangle is exact, the count says
    whether it is a nine or a sixteen, and a tap goes to a tile rather than
    to a point that ought to be one.

    Nine or sixteen or nothing. Any other number is a page mid-render, and
    a half-drawn grid is not one to answer (2026-09-06, phone 1812).
    """
    tiles = [el for el in ctx.elements
             if _TILE_LABEL in (el.label or "").lower() and _box(el)]
    if len(tiles) not in (9, 16):
        return []
    return sorted(tiles, key=lambda el: (_box(el)[1], _box(el)[0]))


def _half_drawn(ctx: Context) -> bool:
    """Whether the page is showing some tiles but not a whole grid."""
    drawn = sum(1 for el in ctx.elements
                if _TILE_LABEL in (el.label or "").lower() and _box(el))
    return 0 < drawn < 16 and drawn != 9


def _tiles_box(tiles: list) -> tuple[int, int, int, int]:
    """The rectangle the tiles fill, corner to corner."""
    boxes = [_box(el) for el in tiles]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _tiles_in(image, box: tuple[int, int, int, int]):
    """The tile block inside `box`, as a rectangle in the same pixels.

    Found by looking rather than by measuring the page: a tile is a
    photograph and a photograph has contrast down every line of it, while
    the banner above and the card around are flat colour. The columns are
    trimmed first and the rows only within them - scanned the other way
    round, the white page either side of a blue banner is contrast enough
    to keep the banner, which is how the crop came to start 50 pixels into
    it.

    None when nothing in the box has any contrast at all, which is a screen
    with no tiles on it.
    """
    grey = image.crop(box).convert("L")
    wide, high = grey.size
    px = grey.load()
    # Scanned across the middle of the card, never its edges. The card's
    # own border is white page against blue banner, and a screenshot that
    # is not the hierarchy's own size blurs that border across the first
    # column - which put contrast on every row of the banner and swallowed
    # the whole window.
    inset = max(1, wide // 20)
    down = _run([_spread(px, [(x, y) for x in range(inset, wide - inset, 3)])
                 > _TILE_CONTRAST for y in range(high)])
    if down is None:
        return None
    top, bottom = box[1] + down[0], box[1] + down[1]
    # The width follows from the height, because a reCAPTCHA grid is square
    # and centred in its card. Trimmed the same way as the rows it does
    # not: one flat column - a plain wall, a stretch of sky - takes the
    # edge with it and every tap after that lands a column over.
    side = bottom - top
    middle = (box[0] + box[2]) // 2
    return (middle - side // 2, top, middle - side // 2 + side, bottom)


def _spread(px, points) -> int:
    values = [px[x, y] for x, y in points]
    return max(values) - min(values) if values else 0


def _run(flags: list[bool]) -> tuple[int, int] | None:
    """The longest stretch of `True`, bridging gaps of `_TILE_GAP`.

    The longest and not the first-to-last: the window's top edge sits
    right under the heading's last line, and on a phone whose screenshot
    is not its hierarchy's own size a pixel of that text bleeds into it.
    Taken end to end, those two pixels stretched the block up over the
    whole banner; the tiles are a stretch six hundred deep and win on
    length every time.
    """
    best: tuple[int, int] | None = None
    start = gap = None
    for i, on in enumerate([*flags, *([False] * (_TILE_GAP + 1))]):
        if on:
            start = i if start is None else start
            gap = 0
        elif start is not None:
            gap += 1
            if gap <= _TILE_GAP:
                continue
            end = i - gap + 1                 # one past the last true pixel
            if best is None or end - start > best[1] - best[0]:
                best = (start, end)
            start = gap = None
    return best


def _challenge_button(ctx: Context, *, below: int):
    """The challenge's own VERIFY - the topmost button under the tiles.

    Not the page's NEXT, which is what `submit` finds: it matches on the
    word, and the sign-in page carries a NEXT of its own at the foot of
    the screen. Pressed there, the answer never goes anywhere - phone 1839
    tapped the same four tiles five rounds running and got the same grid
    back every time, because nothing had ever been submitted (2026-09-06).
    """
    below = max(below, 0)
    wanted = {word.casefold() for word in _GRID_VERIFY}
    best, top = None, None
    for el in ctx.elements:
        box = _box(el)
        # By the word and not by position alone: the same row carries four
        # icon buttons - another challenge, audio, liveness, help - and the
        # leftmost of those is the one a topmost-button rule picks.
        if (not box or not el.clickable
                or (el.label or "").strip().casefold() not in wanted
                or box[1] < below):
            continue
        if top is None or box[1] < top:
            best, top = el, box[1]
    return best


def _button_row(ctx: Context, *, below: int) -> list[int]:
    """The challenge's own button row - the first real button under the
    heading - which is where the tiles stop.

    Found by class and position rather than by wording. Reading it off a
    label matched `Verify` against the page's `Verify it's you` heading,
    which sits *above* the grid: the floor came out higher than the
    ceiling, every grid was called unplaceable, and seven of them went by
    untouched before the phone gave up (2026-09-05, phone 1793).
    """
    tops = [box[1] for el in ctx.elements
            if el.clickable and "button" in (el.cls or "").lower()
            and _TILE_LABEL not in (el.label or "").lower()
            and (box := _box(el)) and box[1] > below]
    return [0, min(tops)] if tops else []


def _box(el) -> list[int]:
    """An element's bounds as [left, top, right, bottom], or []."""
    nums = [int(n) for n in re.findall(r"-?\d+", el.bounds if el else "")]
    return nums if len(nums) == 4 else []


def _grid_rect(ctx: Context) -> tuple[int, int, int, int] | None:
    """The band of screen the tiles are somewhere inside.

    Not the tiles themselves. They are pictures in a WebView and are in no
    accessibility tree, so nothing here can say where they start - an
    earlier version read the edges off the heading's text and the blue
    banner's own padding put the top 50 pixels high and the bottom a whole
    row short (2026-09-05, phone 1807). What the tree *can* say is what the
    tiles are between: below the heading's last line, above the challenge's
    button row, and no wider than the screen. `_tiles_in` finds the block
    itself, in the picture, where it is actually visible.

    None when either edge is missing, and a grid nothing can bound is never
    tapped.
    """
    labels = [(el, (el.label or "").lower()) for el in ctx.elements]
    ask = next((el for el, t in labels
                if "select all" in t and ("images" in t or "squares" in t)),
               None)
    if ask is None:
        return None
    after = labels[[el for el, _ in labels].index(ask) + 1:]
    subject = next((el for el, t in after if t.strip()), None)
    tail = next((el for el, t in after if _is_tail(t)), None)
    head = _box(tail) or _box(subject)
    card = _card_around(ctx, _box(ask))
    if not head or not card:
        return None
    floor = _button_row(ctx, below=head[3])
    if not floor or floor[1] - head[3] < 100:
        return None
    return (card[0], head[3], card[2], floor[1])


def _card_around(ctx: Context, ask: list[int]) -> list[int]:
    """The challenge's own frame - the smallest thing on the page that the
    heading sits inside, and wide enough to hold a grid.

    Searched for rather than assumed to be the screen. Scanned across the
    whole width instead, a grid came out starting at x=134 and running to
    the screen's own edge at 720, because there was contrast outside the
    card to find (2026-09-06, phone 1831). The frame is `reCAPTCHA`, a
    View, and the page carries two of them - the collapsed tick-box widget
    is the other - so it is told apart by containing the heading.
    """
    best: list[int] = []
    for el in ctx.elements:
        box = _box(el)
        if not box or box[2] - box[0] < 200:
            continue
        if not (box[0] <= ask[0] and box[1] <= ask[1]
                and box[2] >= ask[2] and box[3] >= ask[3]):
            continue
        if box == ask:
            continue
        if not best or (box[2] - box[0]) * (box[3] - box[1]) < (
                best[2] - best[0]) * (best[3] - best[1]):
            best = box
    return best


def _grab_grid_b64(ctx: Context, window: tuple[int, int, int, int],
                   size: int, *, scan: bool = True):
    """The tiles as base64 JPEG, and where they sit on the phone.

    Three things, none of which can be skipped:

    Cropped, not the whole screen - a classification task counts squares of
    the picture it is handed, so a phone screen makes it count squares of a
    phone screen. Found by looking, not by measuring the page, because the
    tiles are in no accessibility tree. And *resized*: CapSolver reads the
    grid's shape from the picture's size, and at the phone's own 631 pixels
    it declined to read a grid at all - `{"hasObject": false, "size": 0,
    "type": ""}`, four builds running (2026-09-05). At 450 the same picture
    came back `[13, 14, 15]`, which is exactly where the crosswalks were.

    Returns (base64, rectangle) with the rectangle in the view hierarchy's
    own numbers, because that is where the taps go. None on any hitch,
    which the caller reads as "the captcha stands".
    """
    import base64
    import io

    from .. import phones
    # Before each wait, not only between screens: the screenshot poll is a
    # minute (phones.py), the download is another, and the solver is three
    # tries at ninety seconds. Six and a half minutes in one act, and the
    # router does not come back round until it ends - so a phone somebody
    # stopped kept answering a captcha through all of it (2026-09-06).
    ctx.check()
    link = phones.screenshot(ctx.client, ctx.phone_id)
    if not link:
        return None
    ctx.check()
    try:
        import requests
        data = requests.get(link, timeout=60).content
    except Exception as exc:                                       # noqa: BLE001
        log.warning("could not fetch the captcha screenshot (%s)", exc)
        return None
    try:
        from PIL import Image

        shot = Image.open(io.BytesIO(data)).convert("RGB")
        # The window comes off the view hierarchy, which has its own width,
        # and the screenshot is the device's own picture. Assuming the two
        # agree would search a band of the wrong part of the screen.
        scale = shot.width / max(1, _screen_width(ctx))
        band = tuple(int(round(n * scale)) for n in window)
        box = _tiles_in(shot, band) if scan else band
        if box is None:
            log.warning("nothing with any contrast in %s - no tiles there",
                        band)
            return None
        square = min(box[2] - box[0], box[3] - box[1])
        if square < 100 or abs((box[2] - box[0]) - (box[3] - box[1])) > square:
            log.warning("the block at %s is not a grid's shape", box)
            _keep(ctx, "captcha-refused.png", shot)
            return None
        grid = shot.crop(box)
        side = _GRID_PIXELS[size]
        buf = io.BytesIO()
        grid.resize((side, side)).save(buf, format="JPEG", quality=92)
        _keep(ctx, "captcha-screen.png", shot)
        _keep(ctx, "captcha-grid.png", grid)
        log.info("captcha grid at %s of the %sx%s screenshot, sent at %dx%d",
                 box, shot.width, shot.height, side, side)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("could not cut the captcha grid out (%s)", exc)
        return None
    at = tuple(int(round(n / scale)) for n in box)
    return base64.b64encode(buf.getvalue()).decode("ascii"), at


def _keep(ctx: Context, name: str, image) -> None:
    """Put a picture beside the build's other artifacts, once per name.

    The view hierarchy says where the tiles are; only the picture says what
    was actually sent. Four builds handed CapSolver a grid and got back an
    answer about a single image, and there was no way to tell a bad crop
    from a bad answer without seeing the crop (2026-09-05).
    """
    if not ctx.artifact_dir:
        return
    path = ctx.artifact_dir / name
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG")
        ctx.saved.append(str(path))
    except OSError as exc:
        log.warning("could not keep %s (%s)", name, exc)


def _screen_width(ctx: Context) -> int:
    """How wide the view hierarchy says the screen is - the widest bounds
    on the page, which is the frame every other rectangle sits inside."""
    widest = 0
    for el in ctx.elements:
        box = _box(el)
        if box and box[2] - box[0] > widest:
            widest = box[2] - box[0]
    return widest


#: How many visits the captcha screen gets across a whole flow. Generous,
#: because most of them are spent waiting and because one flow may meet
#: several captchas; `act_captcha` gives up one short of it, so the build
#: ends on `captcha_shown` and never on the router's own phrase.
CAPTCHA_VISITS = 44

#: Rounds one captcha gets - a tick of its box, or one grid sent to the
#: solver. Google's 3x3 takes tiles away as they are answered and draws
#: fresh ones in their place, so passing a single captcha takes three to
#: six rounds; ten is past that and still stops a page that will not
#: settle.
_ROUNDS_PER_CAPTCHA = 10

#: Visits to give a grid to finish drawing before answering it from the
#: picture instead. Waited on without a bound, a grid that never settles
#: took twenty-four visits and the phone with it (2026-09-06, phone 1839).
_DRAW_WAIT = 3

#: Screens that can appear in the middle of one captcha without the next
#: captcha screen being a *new* captcha. A challenge that redraws goes
#: through `loading` on the way, and counting that as a second captcha
#: would spend the operator's limit on one page.
_WITHIN_A_CAPTCHA = {"captcha", "loading"}


def _captchas_met(ctx: Context) -> int:
    """How many separate captchas this flow has been shown.

    Not rounds and not visits: the operator's limit is three captchas in
    one flow, and one captcha is one arrival at the screen however many
    times it redraws while it is being answered. So this counts runs in
    the trail - `captcha captcha captcha` is one, and a captcha after the
    password page is a second.
    """
    met, inside = 0, False
    for name in ctx.trail:
        if name == "captcha":
            met, inside = (met + (0 if inside else 1)), True
        elif name not in _WITHIN_A_CAPTCHA:
            inside = False
    return met


def _captcha_gave_up(ctx: Context, said: str) -> Outcome:
    path = ctx.save("captcha_shown")
    return Outcome("fatal", "captcha_shown",
                   f"{said}; {FATAL_ADVICE['captcha_shown']}",
                   artifacts=[path] if path else [])


def _captcha_present(ctx: Context) -> bool:
    return bool(getattr(ctx, "solver_key", "")) and ctx.has(*_CAPTCHA_NEEDLES)


def act_captcha(ctx: Context) -> Outcome | None:
    """Try the reCAPTCHA rather than give up on it - within a limit.

    The checkbox alone often passes on a tap from a clean exit. A grid is
    sent to CapSolver as an image, and the tiles it names are tapped. Every
    way this can fail - no key balance, an object we have no id for, a grid
    we cannot place on the screen, the limit reached - ends the same way it
    did before there was a solver: the `captcha_shown` fatal, so a build
    still moves on and never loops on a challenge it cannot pass.
    """
    # Counted here rather than taken from the router's visit tally: most
    # visits are waiting for reCAPTCHA to make up its mind, and a wait is
    # not an attempt. Three attempts is what the operator asked for, and
    # three attempts is what this is - not three glances at the page.
    met = _captchas_met(ctx)
    if met != ctx.captcha_met:
        # A new captcha: its rounds start again, and so does the tick box.
        ctx.captcha_met, ctx.captcha_tries, ctx.captcha_waited = met, 0, 0
        ctx.captcha_ticked_on, ctx.captcha_unplaced = None, False
    if met > ctx.captcha_max:
        return _captcha_gave_up(
            ctx, f"a {met}th captcha in one sign-in, past the {ctx.captcha_max}"
                 f" this is allowed to answer")
    if ctx.captcha_tries >= _ROUNDS_PER_CAPTCHA:
        return _captcha_gave_up(
            ctx, f"this captcha did not settle in {_ROUNDS_PER_CAPTCHA} rounds")
    # The other way out. Most visits here are waiting - for reCAPTCHA to
    # decide, or on a grid this cannot place - and waiting was unbounded:
    # the screen simply ran out of visits and the router reported
    # `stuck_on_captcha`, a phrase about the tool rather than the answer
    # the operator asked for (2026-09-05, phones 1793 and 1795). One visit
    # short of the budget, this says the same thing the limit says.
    if ctx.seen.get("captcha", 0) >= CAPTCHA_VISITS - 1:
        return _captcha_gave_up(ctx, "the captcha never cleared")
    instruction = _grid_instruction(ctx)
    if not instruction:
        # The tick box. Tapped once and once only: a second tap unticks
        # what the first ticked, which is what two live builds did
        # (2026-09-06, phones 1787 and 1788) - and reCAPTCHA leaves the
        # box reading unticked for a few seconds while it decides, so the
        # flow must wait rather than help.
        #
        # Nothing is submitted either. Google advances by itself once it
        # is satisfied - phone 1786 went straight from the tick to the
        # password page - and pressing NEXT under an unanswered captcha is
        # a poke at a form that is not ready.
        box = _robot_checkbox(ctx)
        visit = ctx.seen.get("captcha", 0)
        if box is None or box.checked:
            return None                      # let it think; the visit ends
        if (ctx.captcha_ticked_on is not None
                and visit - ctx.captcha_ticked_on < _TICK_AGAIN):
            # Still deciding. reCAPTCHA leaves the box reading unticked for
            # a few seconds after a tap, and a flow that helps here unticks
            # what it just ticked - which two builds did until the limit
            # (2026-09-06, phones 1787 and 1788).
            return None
        # Long enough. Either it never took, or the challenge opened and
        # closed itself again while nothing was answering it, and a phone
        # that waits out its whole budget in front of a closed challenge
        # has spent none of its three tries (2026-09-06, phone 1815).
        screen.tap_element(ctx.client, ctx.phone_id, box)
        ctx.captcha_ticked_on = visit
        ctx.captcha_tries += 1
        return None
    # The tiles first, if the page is offering them: they are exact, they
    # say how many there are, and they can be tapped as tiles rather than
    # as points. The picture is what is left when they have not arrived.
    tiles = _tile_buttons(ctx)
    if not tiles and _half_drawn(ctx) and ctx.captcha_waited < _DRAW_WAIT:
        # Some tiles, not all of them. The grid is still being laid out, and
        # a picture taken now holds whichever corner has arrived - one came
        # out 206 pixels across and was answered as though it were the
        # whole thing (2026-09-06, phone 1836). Waited out instead; the
        # visit budget is what ends this if they never all arrive.
        ctx.captcha_waited += 1
        return None
    ctx.captcha_waited = 0
    window = _tiles_box(tiles) if tiles else _grid_rect(ctx)
    if window is None:
        # A grid we cannot place. Never a tap on coordinates guessed from
        # nothing - but saved once, not once a visit: seven copies of one
        # screen is the same evidence seven times (2026-09-05, phone 1793).
        if not ctx.captcha_unplaced:
            ctx.captcha_unplaced = True
            ctx.save("captcha_grid_unplaced")
        return None
    # How many tiles there are, said by the tiles when they are there.
    # Otherwise the wording, which is the only other thing that tells the
    # two shapes apart: "Select all squares with X" over a one-shot 4x4,
    # "Select all images with X" over a 3x3 that refreshes as tiles are
    # taken. It has to be decided before the picture goes out, because the
    # size it goes out at is what the solver reads the shape from.
    size = (3 if len(tiles) == 9 else 4) if tiles else (
        4 if "squares" in instruction.lower() else 3)
    got = _grab_grid_b64(ctx, window, size, scan=not tiles)
    if got is None:
        return None
    image, rect = got
    ctx.captcha_tries += 1
    try:
        from .. import capsolver
        answer, read = capsolver.solve_grid(ctx.solver_key, image,
                                            instruction, watch=ctx.check)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("captcha not solved (%s)", exc)
        return None
    if read and read != size:
        # The solver read a different grid than the one that was sent to
        # it, so its indices are about a picture nobody has. Left alone.
        log.warning("sent a %dx%d grid and the answer is about a %dx%d one",
                    size, size, read, read)
        return None
    if tiles:
        chosen = [tiles[i] for i in answer if 0 <= i < len(tiles)]
        log.info("captcha: %r -> tiles %s of %d, tapped where they are",
                 instruction, answer, len(tiles))
        for tile in chosen:
            screen.tap_element(ctx.client, ctx.phone_id, tile)
    else:
        points = _tile_points(rect, size, answer)
        log.info("captcha: %r -> tiles %s of a %dx%d grid at %s",
                 instruction, answer, size, size, points)
        for x, y in points:
            shell.tap(ctx.client, ctx.phone_id, x, y)
    _answer(ctx, rect)
    return None


def _answer(ctx: Context, rect: tuple[int, int, int, int]) -> None:
    """Hand the challenge its own answer, on its own button.

    Read from the screen as it is now, not as it was before the tiles were
    tapped: the button row sits under the tiles and moves with them - the
    same challenge showed it at y=937 and at y=988 on two rounds - and the
    button's own word changes from SKIP to VERIFY as soon as one tile is
    taken. A stale tree presses where the button used to be.
    """
    ctx.refresh()
    button = _challenge_button(ctx, below=rect[3])
    if button is None:
        log.warning("the challenge has no button of its own to press")
        return
    screen.tap_element(ctx.client, ctx.phone_id, button)


def recovery_offered(ctx: Context) -> bool:
    return screen.find_first(ctx.elements, RECOVERY_OPTION_LABELS) is not None


def answerable(ctx: Context) -> bool:
    """Whether this page offers something the tool can actually do.

    A chooser is not a verdict. Google's "Choose how you want to sign in" list
    carries one row reading `Get a verification code at eme•••@gmail.com` -
    which `phone_verification_required` matches on `get a verification code
    at`, a needle that says nothing about a phone. So a page offering three
    ways in, one of them answerable from the sheet, was reported as "the
    account wants an SMS code, which this tool has no way to receive", and the
    Gmail was marked and set aside with nothing wrong with it (2026-08-29).

    The needle is kept, because a real standalone SMS page says the same words
    and is genuinely fatal. What changed is that it no longer speaks for a page
    that also offers a way through.
    """
    return authenticator_offered(ctx) or recovery_offered(ctx)


def is_loading(ctx: Context) -> bool:
    """The generic progress bar, or Google saying it is still thinking."""
    return still_loading(ctx) or ctx.has(*CHECKING_TEXTS)


def act_fatal(ctx: Context) -> Outcome:
    reason = _fatal_reason(ctx) or "unknown_fatal"
    path = ctx.save(reason)
    detail = FATAL_ADVICE.get(reason,
                              "the screen says this cannot proceed unattended")
    return Outcome("fatal", reason, detail, artifacts=[path] if path else [])


def act_recaptcha_unreachable(ctx: Context) -> Outcome | None:
    """Close the "Cannot contact reCAPTCHA" dialog and let the widget try
    again; bounded by the entry's visit allowance."""
    log.info("the phone could not reach reCAPTCHA; closing the notice to "
             "let it try again")
    screen.tap_first_present(ctx.client, ctx.phone_id, ctx.elements, ("OK",))
    time.sleep(5)
    return None


def act_go_back(ctx: Context) -> Outcome | None:
    """Take the page at its word and go back.

    "Something went wrong. Please go back and try again." is Google's generic
    stumble, and it says what to do about it. Row 1 met it 143 seconds in and
    reported unknown_screen, which reads like a gap in this registry rather
    than what it was: a transient failure with printed instructions.

    Bounded by the entry's visit allowance, so a page that keeps returning
    becomes stuck_on_transient_error - a named, diagnosable outcome instead of
    a loop.
    """
    log.info("Google reported a transient error; going back to retry")
    shell.keyevent(ctx.client, ctx.phone_id, 4)          # BACK
    time.sleep(5)

    # Back does not always return to the previous step - once it closed the
    # sign-in outright and left the phone on its home screen, where nothing
    # matched and the row was reported as unknown_screen with a launcher full
    # of app icons in its archive (2026-08-09, row 1).
    #
    # Asked of the device, because a screen this flow does not recognise looks
    # the same whether Google has shown something new or the flow is no longer
    # in Google at all.
    front = shell.foreground_package(ctx.client, ctx.phone_id)
    if front and not any(front.startswith(p) for p in SIGN_IN_PACKAGES):
        log.warning("back left the sign-in (%s is in front); reopening it", front)
        open_add_account(ctx.client, ctx.phone_id)
    return None


def sign_in_closed(ctx: Context) -> bool:
    """Whether the add-account UI is no longer the app in front.

    Asked of the device, never of the screen: a page this flow does not
    recognise looks exactly the same whether Google has drawn something new or
    Google is not on screen at all. An empty answer is "cannot tell", and this
    must not claim a page on a guess - so it says no.
    """
    front = shell.foreground_package(ctx.client, ctx.phone_id)
    return bool(front) and not any(front.startswith(p)
                                   for p in SIGN_IN_PACKAGES)


def act_wait_for_the_account(ctx: Context) -> Outcome | None:
    """Do nothing but let Google finish. The account arrives, or it does not.

    Deliberately not `open_add_account`: reopening the flow here would abort
    the very thing being waited for. The only other action available is to
    wait, and `is_done` is polling the device the whole time.
    """
    log.info("the sign-in has closed; waiting for the account to land")
    time.sleep(5)
    return None


def act_account_picker(ctx: Context) -> Outcome | None:
    """The "Add an account" type list - choose Google."""
    if ctx.tap("Google"):
        time.sleep(4)
    return None


def act_email(ctx: Context) -> Outcome | None:
    field = screen.find_input(ctx.elements, password=False)
    if not field:
        return None
    log.info("entering the email address")
    fill(ctx, field, ctx.account.email)
    submit(ctx)
    time.sleep(4)
    return None


#: The password page's own words. Its box is masked - `password="true"`
#: in the dump - on every visit but one: straight after a reCAPTCHA the
#: same box, at the same bounds, comes back `password="false"`. The page
#: then fell past this entry to `dismissable`, which pressed NEXT over an
#: empty box eight times and failed the build as `stuck_on_dismissable` -
#: a verdict on the device, so the Gmail stayed free, the next phone took
#: it, met the same captcha and the same page, and four phones went on one
#: address in an hour (2026-09-08, phones 1981, 1985, 1987 and 1988).
#:
#: So the page is known by its words as well as by its box: these, and a
#: box of either kind. The masked one is still taken first when both are
#: on screen.
PASSWORD_TEXTS = ("enter your password", "enter a password", "show password",
                  "forgot password")


def _password_box(ctx: Context):
    box = screen.find_input(ctx.elements, password=True)
    if box is not None or not ctx.has(*PASSWORD_TEXTS):
        return box
    return screen.find_input(ctx.elements, password=False)


def password_page(ctx: Context) -> bool:
    return _password_box(ctx) is not None


def act_password(ctx: Context) -> Outcome | None:
    field = _password_box(ctx)
    if not field:
        return None
    log.info("entering the password")
    fill(ctx, field, ctx.account.password)
    submit(ctx)
    time.sleep(5)
    return None


def act_totp(ctx: Context) -> Outcome | None:
    """Type an authenticator code with enough life left to survive submission."""
    field = screen.find_input(ctx.elements)
    if not field:
        return None
    if not ctx.account.has_authenticator:
        # Accounts sold without 2FA normally never reach this screen. When one
        # does, Google is asking for something the row cannot produce, and
        # saying so beats an AccountError escaping into the catch-all and
        # arriving in the sheet as "error".
        path = ctx.save("no_authenticator")
        return Outcome("fatal", "no_authenticator",
                       FATAL_ADVICE["no_authenticator"],
                       artifacts=[path] if path else [])
    code = ctx.account.totp_now()
    log.info("entering a fresh authenticator code")
    fill(ctx, field, code)
    submit(ctx)
    time.sleep(5)
    return None


# The authenticator row, most specific phrasing first. The full sentence is the
# tappable list item; "Google Authenticator" alone is an inner span whose centre
# may miss the row.
AUTHENTICATOR_LABELS = (
    "Get a verification code from the Google Authenticator app",
    "verification code from the Google Authenticator",
    "Google Authenticator",
)


def authenticator_offered(ctx: Context) -> bool:
    return screen.find_first(ctx.elements, AUTHENTICATOR_LABELS) is not None


def act_choose_authenticator(ctx: Context) -> Outcome | None:
    """Take the one option on the list this tool can satisfy on its own.

    The authenticator first, because it needs nothing but the row and answers
    in six digits. Then the recovery address, which also needs nothing but the
    row - Google asks us to confirm the address it already holds, so the cell
    is the answer - but only when the row carries one.

    Never the other two. `Get a verification code at eme•••@gmail.com` sends a
    code to a mailbox nothing here can read, and `Use another phone or computer`
    wants a second device.
    """
    for label in AUTHENTICATOR_LABELS:
        if ctx.tap(label):
            log.info("chose the authenticator option")
            time.sleep(5)
            return None

    if ctx.account.recovery_email:
        for label in RECOVERY_OPTION_LABELS:
            if ctx.tap(label):
                log.info("chose the recovery email option")
                time.sleep(5)
                return None
    elif recovery_offered(ctx):
        # The one way through is on screen and the row cannot take it. Said as
        # its own reason rather than `no_authenticator_option`, because the
        # fix is a cell somebody can fill rather than an account to replace.
        path = ctx.save("no-recovery-email")
        return Outcome("fatal", "no_recovery_email",
                       FATAL_ADVICE["no_recovery_email"],
                       artifacts=[path] if path else [])

    path = ctx.save("no-authenticator-option")
    return Outcome("fatal", "no_authenticator_option",
                   "the account's 2FA choices do not include an authenticator app",
                   artifacts=[path] if path else [])


def act_recovery_email(ctx: Context) -> Outcome | None:
    """Type the address Google is asking us to confirm.

    Unlike every other challenge in this file there is nothing to fetch and no
    inbox to read: Google shows a masked hint and asks for the whole address,
    and the sheet has it.
    """
    field = screen.find_input(ctx.elements, password=False)
    if field is None:
        return None
    if not ctx.account.recovery_email:
        path = ctx.save("no-recovery-email")
        return Outcome("fatal", "no_recovery_email",
                       FATAL_ADVICE["no_recovery_email"],
                       artifacts=[path] if path else [])
    log.info("confirming the recovery email address")
    fill(ctx, field, ctx.account.recovery_email)
    submit(ctx)
    time.sleep(5)
    return None


def act_try_another_way(ctx: Context) -> Outcome | None:
    """Only reached when the authenticator is NOT among the visible options.

    "Try another way" asks Google to widen the list, and it is a last resort:
    if the authenticator is already on screen and this is pressed instead,
    Google reads it as "I have nothing else" and refuses the sign-in outright
    with "You didn't provide enough info" (measured 2026-07-30, twice). Hence
    the guard, and hence this screen ranking below the method list.
    """
    log.info("no authenticator option visible; asking for another way")
    if ctx.tap("Try another way"):
        time.sleep(4)
    return None


def act_close_passkey_dialog(ctx: Context) -> Outcome | None:
    """Close Google's passkey dead end and let the method list be read again.

    "OK" deliberately, never "Use a different device": there is no other
    device, and the only way on from here is back to the list of ways to
    verify. "OK" is also not a dismiss label and must not become one - it sits
    on dialogs where pressing it agrees to something, and this flow taps by
    label wherever it finds one.
    """
    if ctx.tap("OK"):
        log.info("closed the passkey dialog; back to the ways to verify")
        time.sleep(3)
    return None


def act_dismiss(ctx: Context) -> Outcome | None:
    tapped = screen.tap_first_present(ctx.client, ctx.phone_id, ctx.elements,
                                      DISMISS_LABELS)
    if tapped:
        log.info("dismissed %r", tapped)
        time.sleep(3)
    return None


# Order matters: the first match wins, so fatal checks come first and the
# catch-all dismissal comes last.
SCREENS: list[Screen] = [
    Screen("fatal", lambda c: _fatal_reason(c) is not None, act_fatal, max_visits=1),

    # Above every acting screen: a captcha is over whatever drew it, and
    # only reached at all when a solver key is set (else `fatal` above has
    # already claimed it). Its own visit budget is one past the attempt
    # limit, so the act's own guard is what ends it, with the right word.
    # The widget itself could not load: "Cannot contact reCAPTCHA. Check
    # your connection and try again." over an OK button, with nothing else
    # on the page - so `captcha` did not match it and it was archived as an
    # unknown screen and the phone thrown away (2026-09-08, phone 1994).
    # OK closes it and the page reloads the widget; three of those and it
    # is `stuck_on_recaptcha_unreachable`, which names the exit's fault.
    Screen("recaptcha_unreachable",
           lambda c: c.has("cannot contact recaptcha"),
           act_recaptcha_unreachable, max_visits=3),

    Screen("captcha", _captcha_present, act_captcha,
           max_visits=CAPTCHA_VISITS),

    # Ranked above every screen that acts, and below fatal only because a page
    # that says the sign-in cannot proceed says so whether or not it is still
    # painting. Waiting is the cheapest thing this loop can do and the login
    # budget bounds it either way, so the visit allowance is generous: the cost
    # of waiting a little too long is seconds, and the cost of acting too early
    # is a whole login.
    Screen("loading", is_loading, act_wait, max_visits=20),

    # A modal over whatever it was opened from, so it outranks the pages
    # underneath - whose text is still in the blob and would otherwise claim
    # the screen.
    #
    # "Try another way" now offers a passkey among the ways to verify, and
    # choosing it on a phone that has none ends here: "There aren't any
    # passkeys for google.com on this device", with OK and "Use a different
    # device". Neither is a dismiss label, so the dialog matched nothing at all
    # and the run reported `unknown_screen` while sitting on a dialog one tap
    # would have closed (2026-09-04, build 1695 - seen by hand first, which is
    # how it was found).
    Screen("passkey_unavailable",
           lambda c: c.has("no passkeys available", "aren't any passkeys"),
           act_close_passkey_dialog, max_visits=3),

    # Google's generic stumble, which prints its own remedy. Above the acting
    # screens because the page underneath it is not the page it claims to be.
    Screen("transient_error",
           lambda c: c.has("something went wrong",
                           "please go back and try again"),
           act_go_back, max_visits=3),

    # Above 2fa_code_entry, which would otherwise claim this page: its
    # predicate is four loose tokens plus any input, and "verification code"
    # sits in the heading of half of Google's challenge pages. `act_totp` on
    # this one kills the row as `no_authenticator` for a box that wanted an
    # address. Above `email_entry` and `dismissable` too - the first would type
    # the login address into it, the second would tap NEXT on it empty.
    #
    # max_visits=2, not the default. A recovery address that Google refuses is
    # refused every time, and each retry is another wrong answer against a live
    # account - the argument that made wrong_2fa_code fatal.
    Screen("recovery_email_confirm",
           lambda c: (c.has(*RECOVERY_TEXTS)
                      and screen.find_input(c.elements,
                                            password=False) is not None),
           act_recovery_email, max_visits=2),

    # Code entry outranks the method list: once a code box is on screen the
    # choice has already been made, and re-choosing would leave it.
    Screen("2fa_code_entry",
           lambda c: (c.has("authenticator", "verification code", "2-step",
                            "enter code")
                      and screen.find_input(c.elements) is not None),
           act_totp),

    # The authenticator, whenever it is visible. This must outrank "try another
    # way": Google presents both on the same page, and pressing the latter while
    # the former is available gets the sign-in refused outright.
    Screen("2fa_authenticator_offered", authenticator_offered,
           act_choose_authenticator),

    # Only when no authenticator row is present.
    Screen("2fa_push_to_other_device",
           lambda c: (c.has("try another way")
                      and c.has("check your", "tap yes", "2-step verification")
                      and not authenticator_offered(c)),
           act_try_another_way),

    Screen("2fa_method_list",
           lambda c: (c.has("choose how you want to sign in",
                            "other ways to verify")
                      and not authenticator_offered(c)),
           act_choose_authenticator, max_visits=1),

    # Google's g.co/sc challenge: "To get your security code, go to g.co/sc in
    # a new browser window". Unattended, that is a dead end - there is no other
    # browser to go to - but the page also offers "Try another way", and on an
    # account with an authenticator that leads to the method list and straight
    # back into the normal path. Confirmed by hand on 2026-08-06: one tap, then
    # the flow finished the sign-in unaided.
    #
    # The last three phrasings are the same challenge reworded: Google stopped
    # sending people to g.co/sc and now walks them through its own app -
    # "Verify it's you", then Google app > Settings > Security > Choose an
    # account to get your code. None of the older three appears on it, so it
    # fell past every entry here to `dismissable`, which found the page's NEXT
    # button, pressed it against an empty code box eight times and gave up as
    # `stuck_on_dismissable`. That reason blames the device, so nothing was
    # written to the Gmail row, the row stayed free, the next pass took it
    # again - and five of those in a row opened the breaker (2026-09-04).
    #
    # Matched on the instruction lines rather than on "verify it's you", which
    # is the heading of half of Google's challenge pages and would swallow
    # screens that belong to other entries.
    #
    # Ranked below the authenticator entry for the usual reason: if a screen
    # ever offers both, taking the authenticator is always right.
    Screen("2fa_security_code_prompt",
           lambda c: (c.has("get a code to sign in", "g.co/sc",
                            "get your security code",
                            "choose an account to get your code",
                            "select the security tab",
                            "choose your account, if it is not already")
                      and not authenticator_offered(c)),
           act_try_another_way, max_visits=2),

    Screen("password_entry", password_page, act_password),

    # Matched on text unique to the email page, never on "sign in". That
    # phrase appears on half of Google's verification screens - including
    # "Get a code to sign in", whose security-code box is also a non-password
    # input, so this entry claimed it and typed the address into it (2026-08-05,
    # row 1). Google answered "This code is invalid", the loop repeated until
    # the visit budget ran out, and the row failed as "stuck_on_email_entry"
    # while never having been on the email screen at all.
    #
    # A screen this no longer matches is archived as unknown_screen, which is a
    # task. Typing an address into whatever box is on offer is a wrong answer
    # wearing the wrong name.
    Screen("email_entry",
           lambda c: (c.has("email or phone", "enter your email",
                            "forgot email", "use your google account")
                      and screen.find_input(c.elements, password=False) is not None),
           act_email),

    Screen("add_account_picker",
           lambda c: c.has("add an account", "add account") and c.find("Google")
           is not None,
           act_account_picker),

    Screen("dismissable",
           lambda c: screen.find_first(c.elements, DISMISS_LABELS,
                                       clickable_only=True) is not None,
           act_dismiss, max_visits=8),

    # Last, so the device is only asked about a page nothing else claimed.
    #
    # Google's consent is the end of the sign-in, not the end of the work:
    # `am start ADD_ACCOUNT_SETTINGS` runs as its own task, so when the consent
    # is accepted the activity finishes and Android drops to the launcher -
    # while Google is still adding the account. Phone 1675's own account log
    # puts thirty-five seconds between `action_called_account_add` and
    # `action_account_add`; the router declares an unknown screen after three
    # unmatched looks, which is about fifteen. So a sign-in that had done
    # everything right was failed as `unknown_screen` with a screenful of app
    # icons in its archive, and the builder deleted the phone underneath it
    # saying nothing had ever been signed into it (2026-09-04, builds 1678-81).
    #
    # Waiting is the whole action. `is_done` polls the device every eight
    # seconds throughout, so the account landing ends this at once; only an
    # account that never lands runs the visits out, and then it fails as
    # `stuck_on_sign_in_closed` - which names what happened.
    # Generous on purpose. Eight visits is about ninety seconds, and ninety
    # seconds is not how long Google takes: every phone that outlived one of
    # these failures was later found holding its account. Waiting costs the
    # per-minute billing of a phone that is already running; giving up early
    # costs the phone, the Gmail and the proxy together. The login budget
    # bounds it either way.
    Screen("sign_in_closed", sign_in_closed, act_wait_for_the_account,
           max_visits=25),
]


# ------------------------------------------------------------------- driver
def open_add_account(client: Client, phone_id: str) -> None:
    """Start the flow at Android's own add-Google-account entry point.

    Driving Settings ourselves means no dependence on GeeLark's RPA, and the
    intent goes straight there rather than navigating menus whose layout varies
    by Android skin.
    """
    shell.run(client, phone_id, "am force-stop com.android.settings")
    time.sleep(2)
    shell.run(
        client, phone_id,
        "am start -a android.settings.ADD_ACCOUNT_SETTINGS "
        "--esa account_types com.google",
    )
    time.sleep(6)


def sign_in(client: Client, phone_id: str, account: Account, *,
            budget_seconds: float = 900, artifact_dir: Path | None = None,
            already_open: bool = False, solver_key: str = "",
            captcha_max: int = 3,
            watch: Callable[[], None] | None = None) -> Outcome:
    """Drive the login to a named outcome.

    Returns rather than raises: a batch needs to record why a row failed and
    move on, not unwind.
    """
    if artifact_dir:
        artifact_dir.mkdir(parents=True, exist_ok=True)

    present = shell.device_accounts(client, phone_id)
    if any(accounts.same_google_account(account.email, a) for a in present):
        return Outcome("success", "already_signed_in", f"accounts: {present}")
    if present:
        # A different account on a device we are about to sign into is a
        # mismatch worth naming: Play can refuse installs for the wrong one.
        log.warning("device already has %s", present)

    if not already_open:
        open_add_account(client, phone_id)

    ctx = Context(client=client, phone_id=phone_id, account=account,
                  artifact_dir=artifact_dir, solver_key=solver_key,
                  captcha_max=captcha_max)

    # The device is the only truth for this step - the account is either in
    # `dumpsys account` or it is not - so unlike the app login this cannot be
    # read off elements already fetched. It costs a request each time it is
    # asked, out of a budget that is process-wide and bans the key for two
    # hours when it runs out, and four builds at once ask four times.
    #
    # So it is asked on a clock rather than every pass. Google puts the
    # account on the device at the end of a consent it takes several seconds
    # to render; noticing that a few seconds late costs nothing, and the loop
    # is doing a screen capture of its own either way.
    last_asked = [0.0]

    def signed_in() -> Outcome | None:
        now = time.monotonic()
        if now - last_asked[0] < ACCOUNT_CHECK_SECONDS:
            return None
        last_asked[0] = now
        # Not strict: this is a poll, and an empty answer here means "not
        # yet". Raising over one refused `dumpsys` would end the login.
        present = shell.device_accounts(client, phone_id, strict=False)
        # Compared as Google compares them, not as strings. The sheet holds
        # `ethicishant123@gmail.com` and the device answers
        # `ethic.ishant123@gmail.com`; a literal `in` says no to both, and did,
        # for every build of 2026-09-04.
        if any(accounts.same_google_account(account.email, a) for a in present):
            return Outcome("success", "signed_in",
                           f"{account.email} is on the device")
        return None

    return router.drive(ctx, SCREENS, is_done=signed_in, watch=watch,
                        budget_seconds=budget_seconds, logger=log)


def sign_in_on_phone(client: Client, phone_id: str, account: Account,
                     **kwargs) -> Outcome:
    """sign_in(), but ensure the phone is up first."""
    phones.ensure_running(client, phone_id)
    return sign_in(client, phone_id, account, **kwargs)
