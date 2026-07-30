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
