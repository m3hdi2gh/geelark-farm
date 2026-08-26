"""The loop all three flows run on.

Its selectors are tested against captured pages in the per-flow files. What
had no test was the machinery underneath: the four ways the loop gives up,
each of which writes a reason into the sheet that somebody then acts on, and
`fill`, which exists because a field once grew "com" on every pass until it
read `...@gmail.comcomcom` (2026-08-25).
"""

from __future__ import annotations

import logging
import pathlib

import pytest

from geelark_farm import screen
from geelark_farm.flows import router
from geelark_farm.flows.router import Context, Outcome, Screen

QUIET = logging.getLogger("test.router")


class Device:
    """A phone that serves a queue of screens.

    Each answer is the XML for one `capture`. Running out means the flow asked
    for more screens than the test said it would, which is a fact about the
    flow worth failing on rather than papering over.
    """

    def __init__(self, *pages: str):
        self.pages = list(pages)
        self.captures = 0

    def next_page(self) -> str:
        self.captures += 1
        if not self.pages:
            raise AssertionError(f"asked for screen {self.captures}, "
                                 f"and only {self.captures - 1} were queued")
        return self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]


def page(*labels: str, progress: bool = False) -> str:
    """A hierarchy with these labels on it, as a dump would carry them."""
    nodes = "".join(
        f'<node text="{label}" resource-id="" class="android.widget.TextView" '
        f'bounds="[0,{i * 100}][200,{i * 100 + 80}]" clickable="true" />'
        for i, label in enumerate(labels))
    if progress:
        nodes += ('<node text="" class="android.widget.ProgressBar" '
                  'bounds="[0,0][10,10]" />')
    return f'<?xml version="1.0"?><hierarchy>{nodes}</hierarchy>'


EMPTY = '<?xml version="1.0"?><hierarchy></hierarchy>'


@pytest.fixture
def device(monkeypatch):
    """Screens come from the queue, and nothing sleeps for real."""
    made = Device()
    monkeypatch.setattr(screen, "capture",
                        lambda client, phone_id: made.next_page())
    monkeypatch.setattr(router.time, "sleep", lambda _s: None)
    return made


def context(tmp_path=None) -> Context:
    return Context(client=None, phone_id="P", artifact_dir=tmp_path)


def never_done():
    return None


def drive(ctx, screens, *, done=never_done, budget=60.0):
    return router.drive(ctx, screens, is_done=done, budget_seconds=budget,
                        logger=QUIET)


# ------------------------------------------------------ the device decides
def test_the_device_is_asked_before_the_screen_is_even_read(device):
    """The whole discipline of this project: success is something the device
    is asked, never something the page claims. Asked first, so a flow that is
    already finished does not act on a page it has no business touching."""
    device.pages = [page("anything")]
    answer = Outcome("success", "signed_in")

    out = drive(context(), [], done=lambda: answer)

    assert out is answer
    assert device.captures == 0, "it read the screen before asking the device"


# --------------------------------------------------- the four ways it stops
def test_an_empty_dump_is_waited_out_rather_than_called_unknown(device):
    """A dump with nothing in it is the device not answering yet, not a page
    nobody recognises - and reporting `unknown_screen` for it sends whoever
    reads the artifact looking for a screen that was never there."""
    device.pages = [EMPTY, EMPTY, page("Continue")]
    seen = []
    screens = [Screen("continue", lambda c: c.has("continue"),
                      lambda c: Outcome("success", "done"))]

    out = drive(context(), screens)

    assert out.ok
    assert seen == []


def test_a_page_nothing_matches_is_looked_at_again_before_it_is_given_up_on(
        device):
    """A dump taken mid-animation legitimately matches nothing. Giving up on
    the first one turns every slow transition into a failed build."""
    device.pages = [page("mid-animation"), page("mid-animation"),
                    page("Continue")]
    screens = [Screen("continue", lambda c: c.has("continue"),
                      lambda c: Outcome("success", "done"))]

    out = drive(context(), screens)

    assert out.ok
    assert device.captures == 3


def test_a_page_that_keeps_not_matching_is_reported_with_what_was_on_it(
        device, tmp_path):
    """The reason lands in the sheet and the artifact is what makes it
    actionable - so the labels go in the message, and the page goes on disk."""
    device.pages = [page("Something Else Entirely")]

    out = drive(context(tmp_path), [])

    assert out.kind == "unknown"
    assert out.reason == "unknown_screen"
    assert "Something Else Entirely" in out.detail
    assert out.artifacts, "the page nobody recognised was not kept"


def test_a_screen_handled_too_often_is_stuck_and_says_which_one(device,
                                                                tmp_path):
    """The action is not having the effect it assumes. Row 13 retyped an
    address and tapped NEXT four times without ever leaving the first
    screen."""
    device.pages = [page("Email")]
    screens = [Screen("email_entry", lambda c: c.has("email"),
                      lambda c: None, max_visits=3)]

    out = drive(context(tmp_path), screens)

    assert out.reason == "stuck_on_email_entry"
    assert "4 times" in out.detail
    assert out.artifacts


def test_a_flow_that_runs_out_of_budget_says_what_it_saw(device, tmp_path):
    """`budget_exhausted` on its own is unactionable. The screens it walked
    are what say whether it was looping or simply slow."""
    device.pages = [page("Email")]
    screens = [Screen("email_entry", lambda c: c.has("email"),
                      lambda c: None, max_visits=99)]

    out = drive(context(tmp_path), screens, budget=0)

    assert out.kind == "budget"
    assert out.reason == "budget_exhausted"
    assert device.captures == 0, "it worked past a budget already spent"


def test_tapping_by_label_goes_to_the_screen_the_context_is_holding(
        monkeypatch):
    """A one-line convenience, and the one place a flow taps something it
    named rather than something it found - so it has to pass the elements it
    is actually looking at, not read the device again."""
    asked = {}

    def tap_label(client, phone_id, elements, label):
        asked.update(phone_id=phone_id, labels=[e.label for e in elements],
                     label=label)
        return True

    monkeypatch.setattr(screen, "tap_label", tap_label)
    ctx = context()
    ctx.elements = screen.parse(page("Continue", "Cancel"))

    assert ctx.tap("Continue") is True
    assert asked["label"] == "Continue"
    assert asked["phone_id"] == "P"
    assert asked["labels"] == ["Continue", "Cancel"]


# --------------------------------------------------------------- the trail
def test_every_outcome_carries_the_path_the_flow_walked(device):
    """Stamped by `drive` on whatever comes back, so a flow cannot forget to.
    A success is the shape a healthy run has, which is what makes a failure's
    shape readable."""
    device.pages = [page("Email"), page("Password"), page("Password")]
    screens = [
        Screen("email", lambda c: c.has("email"), lambda c: None),
        Screen("password", lambda c: c.has("password"),
               lambda c: Outcome("success", "signed_in")),
    ]

    out = drive(context(), screens)

    assert out.trail == ["email", "password"]


def test_the_trail_records_repeats_because_a_loop_is_the_thing_to_see(device):
    """`seen` counts visits per name, so `A B A B` and `A A B B` are the same
    dictionary - and telling a loop from a straight run is most of what
    reading one of these is for."""
    device.pages = [page("Email")]
    screens = [Screen("email", lambda c: c.has("email"), lambda c: None,
                      max_visits=3)]

    out = drive(context(), screens)

    # Three, not four: the fourth visit is the one that trips `max_visits`,
    # and it returns before the trail is appended to.
    assert out.trail == ["email"] * 3
    assert out.reason == "stuck_on_email"


def test_an_outcome_decided_before_the_loop_has_an_empty_trail(device):
    """`app_not_installed` never saw a screen, and says so by having none."""
    out = drive(context(), [], done=lambda: Outcome("fatal", "app_not_installed"))

    assert out.trail == []


# ------------------------------------------------- the page that was loading
def test_a_spinner_is_not_a_screen_to_act_on():
    """Row 13 tapped NEXT, Google began loading, and the flow read the email
    page still showing behind the spinner - so it retyped the address and
    tapped NEXT again, four times (2026-08-06). Watching it live, it was
    simply slow.

    Against the captured page, because that is the only thing that says what
    Google's spinner actually looks like.
    """
    from pathlib import Path
    fixtures = Path(__file__).parent / "fixtures"

    ctx = context()
    ctx.elements = screen.parse(
        (fixtures / "google-email-entry-loading.xml").read_text(encoding="utf-8"))

    assert router.still_loading(ctx) is True

    ctx.elements = screen.parse(page("Email"))

    assert router.still_loading(ctx) is False


def test_a_spinner_carrying_no_text_is_invisible_to_this_check():
    """A limitation, pinned so it is not rediscovered.

    `screen.parse` keeps a node only if it has text, a content-desc, or is an
    input - so a bare `<ProgressBar/>` never reaches the element list this
    reads. Google's carries `text="indeterminate, Loading"` and is seen; the
    Play Store's carries neither and is not, which is why play_install has its
    own detector that asks whether the PAGE arrived rather than whether a
    spinner was drawn (rewritten 2026-08-08, after twelve layout nodes with no
    text and no spinner were reported as no_install_button).

    Nothing says which shape ChatGPT's is: no captured page of that flow has a
    ProgressBar on it at all.
    """
    bare = ('<?xml version="1.0"?><hierarchy>'
            '<node text="Email" class="android.widget.TextView" '
            'bounds="[0,0][200,80]" />'
            '<node text="" content-desc="" class="android.widget.ProgressBar" '
            'bounds="[0,0][10,10]" />'
            '</hierarchy>')
    ctx = context()
    ctx.elements = screen.parse(bare)

    assert not any("ProgressBar" in e.cls for e in ctx.elements)
    assert router.still_loading(ctx) is False


def test_waiting_is_an_action_that_does_nothing_on_purpose(device):
    ctx = context()

    assert router.act_wait(ctx) is None


# ------------------------------------------------------------ what it looks like
def test_an_outcome_reads_as_kind_reason_and_what_happened():
    """Logged and put in a cell, so it has to be legible on one line."""
    assert str(Outcome("fatal", "wrong_password")) == "fatal:wrong_password"
    assert str(Outcome("unknown", "unknown_screen", "on screen: [A, B]")) == (
        "unknown:unknown_screen - on screen: [A, B]")


# =====================================================================
# fill(): typing into a box and checking it took. It exists because a
# field grew "com" on every pass until it read `...@gmail.comcomcom`,
# four submissions later - each one an attempt against a real account
# (2026-08-08, row 7). None of that checking had a test.
# =====================================================================

def field(text="", *, password=False, bounds="[0,0][200,80]"):
    return screen.Element(text=text, desc="", cls="EditText", resource_id="",
                          bounds=bounds, clickable=True, enabled=True,
                          focused=False, password=password)


class Keyboard:
    """What was done to the device, in order.

    `holds` is what the field reads back as after each type - a list, so a box
    that keeps the old contents can be written down as the two different
    answers it gives.
    """

    def __init__(self, *holds: str, tap_fails: bool = False):
        self.holds = list(holds)
        self.tap_fails = tap_fails
        self.typed: list[str] = []
        self.cleared: list[int] = []
        self.taps = 0

    def install(self, monkeypatch):
        monkeypatch.setattr(screen, "tap_element",
                            lambda c, p, e: self._tap())
        monkeypatch.setattr(router.shell, "type_text",
                            lambda c, p, text: self.typed.append(text))
        monkeypatch.setattr(router.shell, "clear_field",
                            lambda c, p, max_chars=64:
                            self.cleared.append(max_chars))
        monkeypatch.setattr(router.time, "sleep", lambda _s: None)

    def _tap(self):
        self.taps += 1
        return not self.tap_fails

    def reading(self, box):
        """Install a refresh that answers with the next `holds` value."""
        def refresh(ctx):
            value = self.holds.pop(0) if self.holds else ""
            ctx.elements = [field(value, bounds=box.bounds)]
        return refresh


@pytest.fixture
def keys(monkeypatch):
    def make(*holds, **kw):
        board = Keyboard(*holds, **kw)
        board.install(monkeypatch)
        return board
    return make


def fill_into(box, text, board, monkeypatch):
    ctx = context()
    monkeypatch.setattr(Context, "refresh", board.reading(box))
    return router.fill(ctx, box, text), ctx


# ------------------------------------------------------------ the ordinary case
def test_typing_into_an_empty_box_does_not_clear_it_first(keys, monkeypatch):
    """Backspacing an empty field is harmless and it is also a dozen shell
    calls, each one a request against the rate limit."""
    board = keys("a@b.com")
    box = field("")

    done, _ = fill_into(box, "a@b.com", board, monkeypatch)

    assert done is True
    assert board.typed == ["a@b.com"]
    assert board.cleared == [], "it backspaced an empty field"


def test_a_box_with_something_in_it_is_emptied_before_typing(keys, monkeypatch):
    """Replacing, not appending - which is the whole of the `comcomcom` bug."""
    board = keys("new@b.com")
    box = field("old@b.com")

    fill_into(box, "new@b.com", board, monkeypatch)

    assert board.cleared, "it typed on top of what was there"
    assert board.cleared[0] >= len("old@b.com")
    assert board.typed == ["new@b.com"]


def test_a_field_that_cannot_be_tapped_is_not_typed_into(keys, monkeypatch):
    """Typing goes to whatever is focused. Without the tap that is some other
    box, and the text lands somewhere nobody is looking."""
    board = keys(tap_fails=True)
    box = field("")

    done, _ = fill_into(box, "a@b.com", board, monkeypatch)

    assert done is False
    assert board.typed == []


# ---------------------------------------------------------- reading it back
def test_a_field_that_did_not_take_the_text_is_corrected_once(keys, monkeypatch):
    """The worst kind of quiet failure here: the form submits, the service
    rejects it, the flow sees the same page and tries again - and each attempt
    is an attempt against a real account."""
    board = keys("a@b.comcom", "a@b.com")
    box = field("a@b.com")

    done, _ = fill_into(box, "a@b.com", board, monkeypatch)

    assert done is True
    assert board.typed == ["a@b.com", "a@b.com"], "it never tried again"
    assert len(board.cleared) == 2
    assert board.cleared[1] > board.cleared[0], (
        "the second clear was not the generous one")


def test_a_field_that_took_the_text_is_left_alone(keys, monkeypatch):
    """A second pass on a field that is already right is another chance to
    make it wrong."""
    board = keys("a@b.com")
    box = field("")

    fill_into(box, "a@b.com", board, monkeypatch)

    assert board.typed == ["a@b.com"]


def test_a_password_box_is_never_read_back(keys, monkeypatch):
    """They report dots, so there is nothing to compare against - and a
    comparison against dots fails every time, which would retype every
    password twice."""
    board = keys("\u2022\u2022\u2022\u2022")
    box = field("", password=True)

    done, _ = fill_into(box, "hunter2", board, monkeypatch)

    assert done is True
    assert board.typed == ["hunter2"]


def test_the_box_read_back_is_the_one_that_was_typed_into(keys, monkeypatch):
    """Matched on bounds, which is what tells one box from another on a page
    with two. Taking whatever `find_input` returns first compares one field's
    contents against another's and corrects the one it can see - worse than
    useless on the check that caught `comcomcom`."""
    board = Keyboard()
    board.install(monkeypatch)
    box = field("", bounds="[0,300][200,380]")

    def refresh(ctx):
        ctx.elements = [field("someone.else@b.com", bounds="[0,0][200,80]"),
                        field("a@b.com", bounds="[0,300][200,380]")]

    monkeypatch.setattr(Context, "refresh", refresh)
    ctx = context()

    assert router.fill(ctx, box, "a@b.com") is True
    assert board.typed == ["a@b.com"], "it corrected against the wrong box"


def test_a_box_that_moved_falls_back_to_the_focused_field(keys, monkeypatch):
    """The page re-rendered it somewhere else. The focused field is a better
    guess than giving up, and is what this always used."""
    board = Keyboard()
    board.install(monkeypatch)
    box = field("", bounds="[0,300][200,380]")

    def refresh(ctx):
        ctx.elements = [field("a@b.com", bounds="[0,900][200,980]")]

    monkeypatch.setattr(Context, "refresh", refresh)
    ctx = context()

    assert router.fill(ctx, box, "a@b.com") is True
    assert board.typed == ["a@b.com"], "a moved box was treated as a wrong one"


def test_a_field_that_cannot_be_read_at_all_is_taken_on_trust(keys,
                                                              monkeypatch):
    """No input on the page after typing means the read-back has nothing to
    say. Retyping on that basis is a guess that costs an attempt."""
    board = Keyboard()
    board.install(monkeypatch)
    box = field("")

    monkeypatch.setattr(Context, "refresh",
                        lambda ctx: setattr(ctx, "elements", []))
    ctx = context()

    assert router.fill(ctx, box, "a@b.com") is True
    assert board.typed == ["a@b.com"]


# --------------------------------------- what mutation found (2026-08-25)
def test_the_page_that_gets_archived_is_the_page_that_was_on_screen(
        device, tmp_path):
    """An artifact is the whole reason an unrecognised screen is fixable, and
    an empty one is worse than none: it says the page was captured and looked
    like nothing."""
    device.pages = [page("Something Nobody Recognises")]

    out = drive(context(tmp_path), [])

    kept = pathlib.Path(out.artifacts[0]).read_text(encoding="utf-8")
    assert "Something Nobody Recognises" in kept


def test_a_screen_is_archived_the_first_time_and_not_after(device, tmp_path):
    """Once per page, so a run leaves a record of the path it took. Every
    visit instead turns a stuck screen into twenty copies of one page, and the
    pruning that keeps the artifact directory usable then has to work through
    them."""
    device.pages = [page("Email")]
    screens = [Screen("email", lambda c: c.has("email"), lambda c: None,
                      max_visits=3)]

    drive(context(tmp_path), screens)

    kept = sorted(p.name for p in tmp_path.iterdir())
    # Not the `stuck-email` one: that is the giving-up artifact and is
    # supposed to be there beside the first sighting.
    named = [n for n in kept
             if n.endswith("-email.xml") and "stuck" not in n]
    assert len(named) == 1, f"archived the same screen {len(named)} times"
    assert any("stuck-email" in n for n in kept), "the giving-up page is gone"


def test_a_screen_seen_only_once_is_still_archived(device, tmp_path):
    """"The first time it is seen" has to mean the first, not the second: a
    flow that walks straight through leaves no record at all if the archive
    waits for a repeat, and a healthy run's shape is what makes a failure's
    shape readable."""
    device.pages = [page("Email")]
    screens = [Screen("email", lambda c: c.has("email"),
                      lambda c: Outcome("success", "done"))]

    drive(context(tmp_path), screens)

    assert any(p.name.endswith("-email.xml") for p in tmp_path.iterdir()), (
        "a screen the flow passed through once was never kept")


def test_the_artifact_directory_is_made_along_with_its_parents(device,
                                                               tmp_path):
    """`artifacts/<stamp>-build3` is two levels deep, and a flow driven
    directly - `geelark login --watch` - has not been through the settings
    call that makes the top one."""
    nested = tmp_path / "artifacts" / "20260825-build3"
    device.pages = [page("Nothing")]

    out = drive(context(nested), [])          # no raise

    assert out.artifacts
    assert nested.is_dir()


def test_a_directory_that_already_exists_is_not_an_error(device, tmp_path):
    """Two flows run against one phone inside the same second - a Google
    sign-in and then the app one - and the second must not fail on the
    directory the first made."""
    device.pages = [page("Nothing")]
    drive(context(tmp_path), [])

    device.pages = [page("Nothing")]
    out = drive(context(tmp_path), [])          # no raise

    assert out.reason == "unknown_screen"


def test_one_recognised_screen_buys_the_full_allowance_again(device):
    """The streak is about consecutive unknowns. Resetting it to anything but
    zero means a flow that has been working gets fewer looks at the next
    transition than a flow that has not."""
    device.pages = [page("Email"),                  # matched, resets the streak
                    page("mid-animation"), page("mid-animation"),
                    page("Password")]
    screens = [
        Screen("email", lambda c: c.has("email"), lambda c: None),
        Screen("password", lambda c: c.has("password"),
               lambda c: Outcome("success", "done")),
    ]

    out = drive(context(), screens)

    assert out.ok, "two unknowns after a match were treated as three"


def test_the_flow_logs_where_its_caller_asked_it_to(device):
    """A build installs its own logger so every line is tagged with the phone
    it belongs to. Falling back to this module's logger puts fifteen parallel
    builds' screens into one untagged stream."""
    device.pages = [page("Email")]
    records = []

    class Collecting(logging.Logger):
        def info(self, msg, *args, **kwargs):
            records.append(msg % args if args else msg)

    screens = [Screen("email", lambda c: c.has("email"),
                      lambda c: Outcome("success", "done"))]

    router.drive(context(), screens, is_done=never_done, budget_seconds=60,
                 logger=Collecting("test.collecting"))

    assert any("screen: email" in line for line in records)


def test_a_box_that_is_read_back_but_cannot_be_tapped_again_gives_up(keys,
                                                                     monkeypatch):
    """The correction needs the field twice: once to read it, once to retype
    into it. A field that reports its contents and then refuses the tap - one
    disabled mid-submit does exactly that - has to end as False, not as a
    silent success on text that was never corrected."""
    board = Keyboard("wrong@b.com")
    board.install(monkeypatch)
    box = field("", bounds="[0,0][200,80]")

    def refresh(ctx):
        # Readable by bounds, invisible to find_input, which skips disabled
        # fields - so the read-back sees it and the retype cannot have it.
        stuck = screen.Element(text="wrong@b.com", desc="", cls="EditText",
                               resource_id="", bounds="[0,0][200,80]",
                               clickable=True, enabled=False, focused=False,
                               password=False)
        ctx.elements = [stuck]

    monkeypatch.setattr(Context, "refresh", refresh)

    assert router.fill(context(), box, "a@b.com") is False


def test_the_fallback_reads_the_typed_box_and_not_the_password_one(keys,
                                                                   monkeypatch):
    """When the box has moved, the focused field is the fallback - and it has
    to be the one text was typed into. Reading the password box instead
    compares dots against an address, decides it went wrong, and retypes."""
    board = Keyboard()
    board.install(monkeypatch)
    box = field("", bounds="[0,300][200,380]")

    def refresh(ctx):
        ctx.elements = [
            screen.Element(text="\u2022\u2022\u2022", desc="", cls="EditText",
                           resource_id="", bounds="[0,600][200,680]",
                           clickable=True, enabled=True, focused=False,
                           password=True),
            field("a@b.com", bounds="[0,900][200,980]"),
        ]

    monkeypatch.setattr(Context, "refresh", refresh)

    assert router.fill(context(), box, "a@b.com") is True
    assert board.typed == ["a@b.com"], "it compared against the password box"
