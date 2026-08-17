"""Play Store install recognition, against pages captured from a real install.

The Install button is the reason this flow exists: GeeLark's own task matches it
only by content-description, and on this page it has none.
"""

from __future__ import annotations

from pathlib import Path

from geelark_farm import screen
from geelark_farm.flows import play_install

FIXTURES = Path(__file__).parent / "fixtures"


def elements(fixture: str) -> list[screen.Element]:
    return screen.parse((FIXTURES / fixture).read_text(encoding="utf-8"))


def test_install_is_found_even_though_it_is_not_clickable():
    """The Install label moves between attributes depending on how Play renders
    the page: a `text` TextView in the prototype's run, a `content-desc` View in
    this one. Either way it reports clickable=false, and tapping the centre of
    its bounds works regardless.

    Hence two rules in screen.find: match text OR content-desc, and do not
    require clickable. A matcher that assumed either would miss this button on
    half the renderings - which is how GeeLark's own flow fails."""
    button = screen.find(elements("play-package-page.xml"), "Install")

    assert button is not None
    assert "Install" in (button.text or button.desc)
    assert not button.clickable          # a clickable filter would drop it
    assert button.centre == (360, 468)   # yet the centre is the right target


def test_the_package_page_is_the_real_app_not_a_search_result():
    """The deep link addresses the package directly, so there is no list of
    similarly named apps to pick the wrong one from."""
    labels = [e.label for e in elements("play-package-page.xml")]
    assert "ChatGPT" in labels
    assert "OpenAI" in labels


def test_the_play_protect_prompt_is_cleared_while_the_download_runs():
    """Mid-download Play offers to enable Protect scanning. It must be dismissed
    rather than answered, and the download must not be mistaken for finished."""
    rows = elements("play-downloading-protect-prompt.xml")
    blob = screen.texts(rows)

    assert "verified by play protect" in blob      # a download is in progress
    assert play_install._fatal_reason(blob) is None
    assert screen.find_first(rows, play_install.INTERSTITIAL_LABELS,
                             clickable_only=True) is not None


# ---------------------------------------------- Play's Terms of Service dialog
def tos() -> list[screen.Element]:
    return elements("play-terms-of-service.xml")


def test_a_paragraph_mentioning_install_is_not_mistaken_for_the_button():
    """The bug that cost a whole run (2026-07-31). This dialog has no Install
    button at all, but its body text says "...apps to use or install. Links to
    instant apps will open without requiring installation...". A substring match
    picked that 150-character paragraph, tapped its centre, logged "tapped
    Install", and waited ten minutes for a download that could never start.

    Finding nothing here is the correct answer."""
    assert screen.find(tos(), "Install") is None


def test_the_terms_dialog_is_cleared_by_accepting_it():
    """Only a brand-new account meets this screen; the first three accounts
    tested had accepted it elsewhere, which is why its absence from the
    interstitial list went unnoticed."""
    chosen = screen.find_first(tos(), play_install.INTERSTITIAL_LABELS,
                               clickable_only=True)
    assert chosen is not None
    assert chosen.label == "Accept"


def test_decline_is_never_a_label_the_flow_will_press():
    """The dialog offers Decline right next to Accept. The interstitial list is
    an allowlist precisely so that 'press any button' can never happen."""
    assert screen.find(tos(), "Decline") is not None      # it is on screen
    assert "Decline" not in play_install.INTERSTITIAL_LABELS


def test_the_real_install_button_still_matches_after_the_tightening():
    """The fix must not break the normal case it protects."""
    button = screen.find(elements("play-package-page.xml"), "Install")
    assert button is not None
    assert (button.text or button.desc) == "Install"


# ------------------------------------------------- a page that has not rendered
def test_a_still_loading_page_is_waited_for_not_given_up_on():
    """Captured 2026-08-01 from a parallel run. Six seconds after the deep link
    the Play Store had drawn nothing but a spinner, so there was no Install
    button and no dialog to clear - and the flow treated that as a dead end,
    failing a row whose page was about to appear.

    "Nothing on screen" and "nothing I recognise" are different answers."""
    xml = (FIXTURES / "play-page-still-loading.xml").read_text(encoding="utf-8")
    rows = screen.parse(xml)

    assert rows == []                     # not one labelled element
    assert "ProgressBar" in xml
    assert play_install.still_loading(rows)


def test_a_rendered_page_is_never_mistaken_for_a_loading_one():
    xml = (FIXTURES / "play-package-page.xml").read_text(encoding="utf-8")
    assert not play_install.still_loading(screen.parse(xml))


def test_a_dialog_is_not_mistaken_for_a_loading_page():
    """The Terms dialog has content, so it must be cleared rather than waited
    out - otherwise the flow would sit there until its budget expired."""
    xml = (FIXTURES / "play-terms-of-service.xml").read_text(encoding="utf-8")
    assert not play_install.still_loading(screen.parse(xml))


def test_a_parked_download_is_restarted_rather_than_waited_out():
    """Play parks a download it cannot start - "Waiting for connection...
    Download will begin once restored" - and then waits indefinitely. Row 5 sat
    on that page for its entire budget and installed nothing (2026-08-07).

    The page keeps its Cancel button, so the state is recoverable; it just
    never recovers on its own.
    """
    from geelark_farm import screen
    from geelark_farm.flows import play_install

    xml = (FIXTURES / "play-download-stalled.xml").read_text(encoding="utf-8")
    elements = screen.parse(xml)
    blob = screen.texts(elements)

    assert play_install._download_stalled(blob)
    assert screen.find(elements, "Cancel") is not None, "the way out is on the page"


def test_a_healthy_install_page_is_not_read_as_stalled():
    """The counterweight: restarting a download that is simply running would
    reset it every ten seconds and never finish."""
    from geelark_farm import screen
    from geelark_farm.flows import play_install

    for fixture in ("play-package-page.xml", "play-terms-of-service.xml"):
        blob = screen.texts(screen.parse(
            (FIXTURES / fixture).read_text(encoding="utf-8")))
        assert not play_install._download_stalled(blob), fixture


def test_restarts_are_bounded():
    """A page that stays parked however often it is asked has something else
    wrong with it, and the budget should report that rather than be spent
    cancelling."""
    assert 1 <= play_install.MAX_DOWNLOAD_RESTARTS <= 5


def stalled_install(monkeypatch, *, restart_takes: bool):
    """Drive `install` against a page that shows the package, then parks the
    download and never moves again.

    A failed restart leaves the phone back on the package page - `open_package
    _page` put it there - which is neither downloading nor stalled. That is
    the state phone 823 was left in, and the state that clears the stall
    clock, so the harness reproduces it rather than holding the parked page.

    Returns (outcome, elapsed, restarts).
    """
    from geelark_farm import shell

    page = (FIXTURES / "play-package-page.xml").read_text(encoding="utf-8")
    parked = (FIXTURES / "play-download-stalled.xml").read_text(encoding="utf-8")

    clock = {"now": 0.0}
    monkeypatch.setattr(play_install.time, "time", lambda: clock["now"])
    monkeypatch.setattr(play_install.time, "sleep",
                        lambda s: clock.__setitem__("now", clock["now"] + s))
    monkeypatch.setattr(shell, "package_installed", lambda *a, **k: False)
    monkeypatch.setattr(play_install, "open_package_page", lambda *a, **k: None)

    state = {"first": True, "settled": False}

    def capture(*a, **k):
        if state["first"]:                 # the package page, to tap Install
            state["first"] = False
            return page
        return page if state["settled"] else parked

    monkeypatch.setattr(play_install.screen, "capture", capture)
    # What the restart finds when it goes looking for Install again.
    monkeypatch.setattr(play_install.screen, "read_screen",
                        lambda *a, **k: screen.parse(page if restart_takes
                                                     else parked))

    restarts = []
    real_restart = play_install._restart_download

    def counted(*args, **kwargs):
        restarts.append(1)
        asked = real_restart(*args, **kwargs)
        state["settled"] = not asked      # left on a page that looks idle
        return asked
    monkeypatch.setattr(play_install, "_restart_download", counted)
    monkeypatch.setattr(play_install.screen, "tap_element", lambda *a, **k: True)
    monkeypatch.setattr(play_install.screen, "tap_label", lambda *a, **k: True)
    monkeypatch.setattr(play_install.screen, "tap_first_present",
                        lambda *a, **k: None)

    outcome = play_install.install(object(), "1", "com.openai.chatgpt",
                                   budget_seconds=3600)
    return outcome, clock["now"], len(restarts)


def test_a_restart_that_did_not_take_still_spends_a_restart(monkeypatch):
    """Phone 823 stalled, the restart could not find Install afterwards, and
    the page it was left on looked neither downloading nor stalled - so the
    clock never fired again, two of its three restarts were never spent, and
    it polled for seventeen minutes before the budget ran out (2026-08-17).

    All three restarts must be spent, and the answer must be the stall.
    """
    outcome, elapsed, restarts = stalled_install(monkeypatch,
                                                 restart_takes=False)

    assert outcome.reason == "download_stalled"
    assert restarts == play_install.MAX_DOWNLOAD_RESTARTS
    assert elapsed < 3600           # reported, not waited out


def test_a_restart_that_took_gets_the_clock_back(monkeypatch):
    """The other half of the same rule: a restart that did find Install has
    genuinely asked again, so the phone deserves a fresh stall window rather
    than being charged for the seconds it was parked."""
    outcome, _, restarts = stalled_install(monkeypatch, restart_takes=True)

    assert outcome.reason == "download_stalled"      # it never does install
    assert restarts == play_install.MAX_DOWNLOAD_RESTARTS


def test_a_parked_download_is_not_reported_as_a_missing_button():
    """On its retry, row 5's page showed Cancel and Open where Install would
    be, because a download from the previous attempt was still parked.
    "no_install_button" is true of that page and useless: the button is absent
    because the work is half done (2026-08-07)."""
    from geelark_farm import screen
    from geelark_farm.flows import play_install

    elements = screen.parse(
        (FIXTURES / "play-download-stalled.xml").read_text(encoding="utf-8"))

    assert screen.find(elements, "Install") is None, "the premise of the bug"
    assert play_install._download_stalled(screen.texts(elements))


def test_a_page_that_never_painted_is_not_a_page_without_a_button():
    """Row 1 spent its pre-install budget on a blank page and was reported as
    though Play had refused it, which sends whoever reads it looking for a
    button that was never missing."""
    from geelark_farm import screen
    from geelark_farm.flows import play_install

    xml = (FIXTURES / "play-page-never-loaded.xml").read_text(encoding="utf-8")
    elements = screen.parse(xml)

    assert elements == []
    assert play_install.still_loading(elements)


def test_plays_server_error_is_retried_by_name():
    """Play replaces the whole package page with an error and a Try again
    button, so there is no Install to find and nothing in the interstitial list
    to press - a row reported no_install_button for two minutes of it
    (2026-08-08).

    By name, because the same page offers a mini-game to pass the time, and
    its button is called Play. "Press the clickable button" would start that.
    """
    from geelark_farm import screen
    from geelark_farm.flows import play_install

    elements = screen.parse(
        (FIXTURES / "play-server-error.xml").read_text(encoding="utf-8"))

    assert play_install._server_error(screen.texts(elements))
    assert screen.find(elements, "Install") is None
    assert screen.find(elements, "Try again") is not None
    # The trap on this very page.
    assert screen.find(elements, "Play") is not None
    assert "Play" not in play_install.INTERSTITIAL_LABELS


def test_a_normal_package_page_is_not_read_as_an_error():
    from geelark_farm import screen
    from geelark_farm.flows import play_install

    blob = screen.texts(screen.parse(
        (FIXTURES / "play-package-page.xml").read_text(encoding="utf-8")))
    assert not play_install._server_error(blob)


def test_a_page_of_bare_layout_is_still_loading():
    """The check used to require a ProgressBar in the raw XML, which made it a
    question about whether Google had drawn a spinner rather than about whether
    the page had arrived. A hierarchy of twelve layout nodes with no text and no
    spinner was reported as no_install_button - true, and about a page that was
    not there (2026-08-08, row 2)."""
    from geelark_farm import screen
    from geelark_farm.flows import play_install

    xml = (FIXTURES / "play-page-layout-only.xml").read_text(encoding="utf-8")
    assert "ProgressBar" not in xml, "the premise: no spinner to go by"
    assert xml.count("<node") > 0, "and it is not an empty dump either"

    assert play_install.still_loading(screen.parse(xml))


def test_a_sentence_is_not_the_try_again_button():
    """Row 2's page said "Something went wrong. Please go back and try again."
    and had no button at all. "try again" is a whole word inside that sentence,
    so the label search matched the subtitle - and tapping a line of text
    reports success, so the retry never re-opened the page and the row failed
    three identical times (2026-08-08).

    Requiring the match to be clickable is what separates the button from the
    sentence describing it.
    """
    from geelark_farm import screen
    from geelark_farm.flows import play_install

    els = screen.parse(
        (FIXTURES / "play-something-went-wrong.xml").read_text(encoding="utf-8"))

    assert play_install._server_error(screen.texts(els))
    assert screen.find(els, "Install") is None

    # The trap, still there: the sentence matches by label.
    loose = screen.find(els, "Try again")
    assert loose is not None and not loose.clickable
    # And the guard that steps around it.
    assert screen.find(els, "Try again", clickable_only=True) is None


def test_a_real_try_again_button_is_still_found():
    """The counterweight: Play's other error page has a genuine Button, and
    pressing it is what recovered that row."""
    from geelark_farm import screen

    els = screen.parse(
        (FIXTURES / "play-server-error.xml").read_text(encoding="utf-8"))

    button = screen.find(els, "Try again", clickable_only=True)
    assert button is not None and button.clickable


def test_the_retries_are_spaced_and_still_fit_the_budget():
    """Three attempts twelve seconds apart got the same page three times. These
    failures clear by waiting, so the spacing is the fix - and it has to stay
    inside the pre-install budget or it turns a named failure into a timeout."""
    from geelark_farm.flows import play_install

    total = play_install.MAX_PLAY_RETRIES * play_install.PLAY_RETRY_PAUSE
    assert play_install.PLAY_RETRY_PAUSE >= 20
    assert total < play_install.PRE_INSTALL_SECONDS


def test_a_pending_download_is_recognised_at_all():
    """Play parks a queued download as "Pending..." with a Cancel button. The
    stalled list had "download pending" and not that, so the poll loop logged
    "still installing..." for the whole budget while nothing moved
    (2026-08-09, row 13)."""
    from geelark_farm import screen
    from geelark_farm.flows import play_install

    els = screen.parse(
        (FIXTURES / "play-download-pending.xml").read_text(encoding="utf-8"))
    blob = screen.texts(els)

    assert "pending" in blob
    assert play_install._download_stalled(blob)
    assert screen.find(els, "Cancel") is not None, "the way out is on the page"
    assert screen.find(els, "Install") is None


def test_pending_is_only_acted_on_once_it_has_not_moved():
    """"Pending..." is what a healthy queued download says for its first
    seconds too - and what a download that has just been restarted says by
    definition. Acting on the word alone, the pre-install phase cancelled and
    restarted three times inside thirty seconds, spending every attempt it had
    on the state it had itself created (2026-08-09, row 13).
    """
    from geelark_farm.flows import play_install

    stall = play_install.Stall()

    # Not stalled yet: the clock does not run.
    assert stall.held_for(False) == 0.0
    # It starts on the first sighting, and zero elapsed is not enough to act.
    assert stall.held_for(True) < play_install.STALLED_SECONDS
    # A page that recovers clears it, so the next sighting starts over.
    assert stall.held_for(False) == 0.0
    assert stall.held_for(True) < play_install.STALLED_SECONDS

    assert play_install.STALLED_SECONDS >= 30


def test_the_pre_install_phase_has_room_for_the_waits_it_must_do():
    """Three restarts, each preceded by a wait for the page to stop looking
    pending, need several times what clearing a dialog does. If they do not
    fit, the phase runs out and reports a missing button instead."""
    from geelark_farm.flows import play_install

    needed = play_install.MAX_DOWNLOAD_RESTARTS * play_install.STALLED_SECONDS
    assert play_install.PRE_INSTALL_SECONDS > needed


def test_a_page_without_install_is_asked_for_again_before_giving_up():
    """Play wanders. After its Terms dialog was cleared, one row was left on
    the "About this app" description page - which has no Install button at all
    - and it was reported as though Play had refused the install (2026-08-10,
    row 10). The deep link puts it back on the package page, so it is worth
    asking again."""
    from geelark_farm.flows import play_install

    assert play_install.MAX_PAGE_REOPENS >= 1
    # Bounded, or a Play Store that never shows the page loops until the budget.
    assert play_install.MAX_PAGE_REOPENS <= 3


def test_no_install_button_keeps_the_page_it_is_about():
    """The reason is only ever diagnosed from what was on screen, and the
    labels in its message are truncated - the last one had to be read out of
    the sheet's note column."""
    import inspect

    from geelark_farm.flows import play_install

    source = inspect.getsource(play_install.install)
    where = source.index('"no_install_button"')
    assert 'archive("no-install-button"' in source[:where]
