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
    assert play_install.still_loading(rows, xml)


def test_a_rendered_page_is_never_mistaken_for_a_loading_one():
    xml = (FIXTURES / "play-package-page.xml").read_text(encoding="utf-8")
    assert not play_install.still_loading(screen.parse(xml), xml)


def test_a_dialog_is_not_mistaken_for_a_loading_page():
    """The Terms dialog has content, so it must be cleared rather than waited
    out - otherwise the flow would sit there until its budget expired."""
    xml = (FIXTURES / "play-terms-of-service.xml").read_text(encoding="utf-8")
    assert not play_install.still_loading(screen.parse(xml), xml)


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
    from geelark_farm.flows import play_install

    assert 1 <= play_install.MAX_DOWNLOAD_RESTARTS <= 5


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
    assert play_install.still_loading(elements, xml)
