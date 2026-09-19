"""Google's reCAPTCHA widget: the tick box, and the image grid behind it.

Lifted out of `google_login` whole when a second service turned out to
serve the same widget. Spotify's challenge page - `challenge.spotify.com`,
drawn by Chrome after the password on a phone it does not trust - is a
reCAPTCHA v2 tick box, and what opens when the tick will not do is the
same 3x3 "Select all images with crosswalks" over the same VERIFY, in the
same accessibility shape (3644, 2026-09-19). Two copies of the code below
would drift, and the copy that drifted would be the one nobody was
watching.

Nothing here knows which service is asking. Everything takes a
`router.Context` and reads only what is on the screen, which is why the
move was a move and not a rewrite: every line of it was already written
against the widget rather than against Google.

The comments are the ones the Google builds paid for and are kept
verbatim; the phone numbers in them are Google sign-ins.

What is *not* here yet: the tap loop that presses the tiles an answer
names. `google_login.act_captcha` still has its own, wound through its
own counters, and `tap_answer` below is the Spotify flow's. They do the
same thing in the same order and should become one the next time either
is touched.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from .. import screen, shell
from .router import Context

log = logging.getLogger(__name__)

#: The tick box's own words, and the grid's - what tells the page from
#: any other page a flow might be standing on.
CAPTCHA_NEEDLES = ("confirm you're not a robot", "i'm not a robot",
                   "select all images", "select all squares")
#: The words on the button that submits a grid. "Verify" on a 3x3 with a
#: tile taken, "Skip" on one with none, "Next" on a 4x4.
GRID_VERIFY = ("Verify", "Skip", "Next")

#: The lines that close the challenge's heading. Everything between the
#: "Select all …" line and one of these is the name of the thing to find.
#: Two wordings, one per grid shape: the 3x3 that refreshes says "click
#: verify once there are none left", the one-shot 4x4 says "if there are
#: none, click skip". Matching only the first read the whole rest of the
#: page into the question and left the heading's real foot unfound, which
#: is what put the grid rectangle out of reach (2026-09-05, phone 1793).
_GRID_TAIL = ("click verify", "click skip", "if there are none")

#: The controls that sit under the heading, which are not the question
#: either. The tail line above is not always in the tree - when it was
#: missing the next three labels were read as the subject and CapSolver
#: was asked for "traffic lights Get a new challenge Get an audio
#: challenge" (2026-09-11, phone 2333, four of that day's 77 calls). A
#: solver given the page's furniture answers for the furniture.
_GRID_NOISE = ("get a new challenge", "get an audio challenge",
               "get a liveness challenge", "image challenge", "recaptcha",
               "privacy", "terms", "help", "skip", "verify", "next",
               "try another way")


def _is_tail(text: str) -> bool:
    low = (text or "").lower()
    return any(needle in low for needle in _GRID_TAIL)


def _is_noise(text: str) -> bool:
    """A control or a legal line rather than a word of the question."""
    low = (text or "").strip().lower()
    return any(needle in low for needle in _GRID_NOISE)


def instruction_on(ctx: Context) -> str:
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
        if (not text.strip() or _is_tail(text) or _is_noise(text)
                or TILE_LABEL in text.lower()):
            break
        words.append(text.strip())
    return " ".join(words)


def robot_checkbox(ctx: Context):
    """The reCAPTCHA tick box itself, not the words beside it.

    A CheckBox whose label says "not a robot" - the words on their own are
    a TextView the same page also carries, and tapping prose does nothing.
    """
    for el in ctx.elements:
        if ("checkbox" in (el.cls or "").lower()
                and "not a robot" in (el.label or "").lower()):
            return el
    return None


def tile_points(rect: tuple[int, int, int, int], size: int,
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
GRID_PIXELS = {3: 300, 4: 450}

#: Pixels of flat colour that may sit inside the tile block without ending
#: it: the white lines between tiles, and a stray row or two of the
#: heading's text bleeding down when a screenshot is not the hierarchy's
#: own size. The block itself is hundreds of pixels tall, so bridging a
#: handful cannot join two real things together.
_TILE_GAP = 8


#: What each tile of the grid calls itself once the WebView has finished
#: laying the challenge out. On the first visit they are not there at all,
#: which is why the picture can still be scanned instead.
TILE_LABEL = "image challenge"


def tile_buttons(ctx: Context) -> list:
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
             if TILE_LABEL in (el.label or "").lower() and box_of(el)]
    if len(tiles) not in (9, 16):
        return []
    return sorted(tiles, key=lambda el: (box_of(el)[1], box_of(el)[0]))


def half_drawn(ctx: Context) -> bool:
    """Whether the page is showing some tiles but not a whole grid."""
    drawn = sum(1 for el in ctx.elements
                if TILE_LABEL in (el.label or "").lower() and box_of(el))
    return 0 < drawn < 16 and drawn != 9


def tiles_box(tiles: list) -> tuple[int, int, int, int]:
    """The rectangle the tiles fill, corner to corner."""
    boxes = [box_of(el) for el in tiles]
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


def challenge_button(ctx: Context, *, below: int):
    """The challenge's own VERIFY - the topmost button under the tiles.

    Not the page's NEXT, which is what `submit` finds: it matches on the
    word, and the sign-in page carries a NEXT of its own at the foot of
    the screen. Pressed there, the answer never goes anywhere - phone 1839
    tapped the same four tiles five rounds running and got the same grid
    back every time, because nothing had ever been submitted (2026-09-06).
    """
    below = max(below, 0)
    wanted = {word.casefold() for word in GRID_VERIFY}
    best, top = None, None
    for el in ctx.elements:
        box = box_of(el)
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
            and TILE_LABEL not in (el.label or "").lower()
            and (box := box_of(el)) and box[1] > below]
    return [0, min(tops)] if tops else []


def box_of(el) -> list[int]:
    """An element's bounds as [left, top, right, bottom], or []."""
    nums = [int(n) for n in re.findall(r"-?\d+", el.bounds if el else "")]
    return nums if len(nums) == 4 else []


def grid_rect(ctx: Context) -> tuple[int, int, int, int] | None:
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
    head = box_of(tail) or box_of(subject)
    card = _card_around(ctx, box_of(ask))
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
        box = box_of(el)
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


def grab_grid_b64(ctx: Context, window: tuple[int, int, int, int],
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
        side = GRID_PIXELS[size]
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
        box = box_of(el)
        if box and box[2] - box[0] > widest:
            widest = box[2] - box[0]
    return widest

#: Between two tiles of a grid: the look at the picture before the
#: press. With the tap's own pause that is 0.4-1.6 s a tile, never the
#: whole grid in one second (2026-09-11).
TILE_LOOK_SECONDS = (0.2, 0.9)


def grid_size(instruction: str, tiles: list) -> int:
    """How many tiles a side, said by the tiles when they are there.

    Otherwise the wording, which is the only other thing that tells the
    two shapes apart: "Select all squares with X" over a one-shot 4x4,
    "Select all images with X" over a 3x3 that refreshes as tiles are
    taken. It has to be decided before the picture goes out, because the
    size it goes out at is what the solver reads the shape from.
    """
    if tiles:
        return 3 if len(tiles) == 9 else 4
    return 4 if "squares" in (instruction or "").lower() else 3


def tap_answer(ctx: Context, answer: list[int], *, tiles: list,
               rect: tuple[int, int, int, int], size: int,
               instruction: str = "") -> None:
    """Press the tiles the solver named, then the challenge's own button.

    The tiles when the page offers them - a tap goes to a tile rather
    than to a point that ought to be one - and the arithmetic when it
    does not.
    """
    if tiles:
        chosen = [tiles[i] for i in answer if 0 <= i < len(tiles)]
        log.info("captcha: %r -> tiles %s of %d, tapped where they are",
                 instruction, answer, len(tiles))
        for tile in chosen:
            # A person looks at each picture before pressing it.
            shell.pause(*TILE_LOOK_SECONDS)
            screen.tap_element(ctx.client, ctx.phone_id, tile)
    else:
        points = tile_points(rect, size, answer)
        log.info("captcha: %r -> tiles %s of a %dx%d grid at %s",
                 instruction, answer, size, size, points)
        for x, y in points:
            shell.pause(*TILE_LOOK_SECONDS)
            shell.tap(ctx.client, ctx.phone_id, x, y)
    press_verify(ctx, below=rect[3])


def press_verify(ctx: Context, *, below: int) -> None:
    """Hand the challenge its own answer, on its own button.

    Read from the screen as it is now, not as it was before the tiles
    were tapped: the button row sits under the tiles and moves with them
    - the same challenge showed it at y=937 and at y=988 on two rounds -
    and the button's own word changes from SKIP to VERIFY as soon as one
    tile is taken. A stale tree presses where the button used to be.
    """
    ctx.refresh()
    button = challenge_button(ctx, below=below)
    if button is None:
        log.warning("the challenge has no button of its own to press")
        return
    screen.tap_element(ctx.client, ctx.phone_id, button)
