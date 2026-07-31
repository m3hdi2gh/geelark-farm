"""See the phone's screen, and act on what is actually there.

Elements are located by observation, never assumption. GeeLark's own flows fail
because they match only `content-desc`; the Play Store renders its Install
label as `text` on a non-clickable TextView, and Google's code fields have no
label at all. All three cases are handled here rather than in each flow.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .api import Client
from .shell import read, run, tap

log = logging.getLogger(__name__)

DUMP_PATH = "/sdcard/window_dump.xml"

# Editable classes, kept even with no text or content-desc: an empty input
# field is exactly what a login flow needs to find. Matched as substrings
# because Android reports many variants - TextInputEditText, SearchAutoComplete,
# MultiAutoCompleteTextView - and a Settings search box turned out to be an
# AutoCompleteTextView, not an EditText.
EDITABLE_CLASSES = ("EditText", "AutoComplete", "SearchView")

# Google's UI uses typographic punctuation, so "Couldn't sign you in" on screen
# contains U+2019, not an ASCII apostrophe - and a matcher written with ' does
# not match it. That silently turned a named failure into "unknown screen"
# (measured 2026-07-30). Non-breaking spaces appear too, in strings like
# "Google Play Pass". Normalising at parse time means every matcher
# downstream can be written in plain ASCII.
PUNCTUATION = str.maketrans({
    "‘": "'", "’": "'",        # single quotes
    "“": '"', "”": '"',        # double quotes
    "–": "-", "—": "-",        # en/em dash
    "…": "...",                     # ellipsis
    " ": " ", " ": " ",        # non-breaking spaces
})


def normalize(text: str) -> str:
    """Fold typographic punctuation to ASCII so selectors can be plain text."""
    return (text or "").translate(PUNCTUATION)


@dataclass(frozen=True)
class Element:
    """One node of the view hierarchy, flattened to what matching needs."""

    text: str
    desc: str
    cls: str
    resource_id: str
    bounds: str
    clickable: bool
    enabled: bool
    focused: bool
    password: bool

    @property
    def label(self) -> str:
        return self.text or self.desc

    @property
    def centre(self) -> tuple[int, int] | None:
        """'[left,top][right,bottom]' -> (x, y), or None if unparseable."""
        nums = [int(n) for n in re.findall(r"-?\d+", self.bounds)]
        if len(nums) != 4:
            return None
        x1, y1, x2, y2 = nums
        return (x1 + x2) // 2, (y1 + y2) // 2

    @property
    def is_input(self) -> bool:
        return any(c in self.cls for c in EDITABLE_CLASSES)

    def __str__(self) -> str:
        flags = "".join((
            "*" if self.clickable else " ",
            "!" if not self.enabled else " ",
            ">" if self.focused else " ",
            "#" if self.password else " ",
        ))
        shown = self.text or (f"(desc) {self.desc}" if self.desc else
                              f"(empty {self.cls})")
        extra = f"  desc={self.desc!r}" if self.text and self.desc else ""
        rid = f"  id={self.resource_id.rsplit('/', 1)[-1]}" if self.resource_id else ""
        return f"{flags} {shown!r:48} {self.cls:20} {self.bounds}{rid}{extra}"


# --------------------------------------------------------------- capture
def capture(client: Client, phone_id: str) -> str | None:
    """Dump the live view hierarchy and return its XML.

    uiautomator writes to a file rather than stdout, so this is two commands.
    Returns None when the dump produced nothing usable, which usually means the
    phone is still booting or the screen is off.
    """
    run(client, phone_id, f"uiautomator dump {DUMP_PATH}")
    raw = read(client, phone_id, f"cat {DUMP_PATH}")
    start = raw.find("<?xml")
    if start == -1:
        start = raw.find("<hierarchy")
    if start == -1:
        log.warning("no hierarchy in dump output: %r", raw[:200])
        return None
    return raw[start:]


def parse(xml: str) -> list[Element]:
    """Flatten the hierarchy into matchable elements.

    Keeps nodes that carry a label, plus unlabelled input fields - the latter
    is what makes a code-entry screen tractable.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        log.warning("could not parse hierarchy: %s", exc)
        return []

    elements: list[Element] = []
    for node in root.iter("node"):
        text = normalize(node.get("text") or "").strip()
        desc = normalize(node.get("content-desc") or "").strip()
        cls = (node.get("class") or "").rsplit(".", 1)[-1]
        keep = text or desc or any(c in cls for c in EDITABLE_CLASSES)
        if not keep:
            continue
        elements.append(Element(
            text=text,
            desc=desc,
            cls=cls,
            resource_id=node.get("resource-id") or "",
            bounds=node.get("bounds") or "",
            clickable=node.get("clickable") == "true",
            enabled=node.get("enabled") != "false",
            focused=node.get("focused") == "true",
            password=node.get("password") == "true",
        ))
    return elements


def read_screen(client: Client, phone_id: str) -> list[Element]:
    """capture() + parse() - the pair every flow actually wants."""
    xml = capture(client, phone_id)
    return parse(xml) if xml else []


def save_fixture(xml: str, path: str | Path) -> Path:
    """Persist a capture so screen matching can be tested without a phone."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(xml, encoding="utf-8")
    return target


# ---------------------------------------------------------------- matching
# How much longer than the query a partial match may be. A control's label is
# the word plus perhaps a qualifier ("Install (30 MB)"); a paragraph that merely
# contains the word is prose, and tapping its centre is never what was meant.
#
# Measured 2026-07-31: searching for "Install" on Play's Terms of Service dialog
# matched "Google Play gives you access to millions of apps to use or install.
# Links to instant apps will open without requiring installation..." - 150
# characters of body text. The flow tapped the middle of that paragraph,
# reported "tapped Install", and then waited ten minutes for a download that was
# never going to start.
MAX_PARTIAL_EXTRA = 30


def _partial_matcher(wanted: str):
    """A predicate for 'this label contains the query as a word'.

    Word boundaries alone are not enough - "use or install." matches "install"
    legitimately - so the length cap above does the rest of the work.
    """
    if wanted and wanted[0].isalnum() and wanted[-1].isalnum():
        pattern = re.compile(rf"\b{re.escape(wanted)}\b")
        return lambda value: pattern.search(value) is not None
    return lambda value: wanted in value


def find(elements: list[Element], label: str, *,
         clickable_only: bool = False) -> Element | None:
    """Find an element by label, matching text OR content-desc.

    Three rules, each of which exists because breaking it caused a real failure:

    - **text OR content-desc**, because the same label moves between the two:
      Play's Install button is a `text` TextView on one rendering and a
      `content-desc` View on another. Matching one alone is how GeeLark's own
      flow fails.
    - **A non-clickable match still counts**, because that Install label reports
      `clickable=false` and tapping its centre works anyway.
    - **A partial match must be label-shaped**: the query as a whole word, in a
      string not much longer than the query itself. Otherwise a paragraph
      mentioning the word wins over the button.

    Exact matches beat partial ones, and among partial matches the shortest wins,
    so "Install" is preferred to "Install (30 MB)".
    """
    wanted = label.casefold()
    matches_partially = _partial_matcher(wanted)
    limit = len(wanted) + MAX_PARTIAL_EXTRA

    exact: list[Element] = []
    partial: list[Element] = []
    for element in elements:
        values = [v for v in (element.text.casefold(), element.desc.casefold()) if v]
        if any(v == wanted for v in values):
            exact.append(element)
        elif any(matches_partially(v) and len(v) <= limit for v in values):
            partial.append(element)
    partial.sort(key=lambda e: len(e.label))

    for pool in (exact, partial):
        usable = [e for e in pool if e.enabled]
        clickable = [e for e in usable if e.clickable]
        if clickable:
            return clickable[0]
        if clickable_only:
            continue
        if usable:
            return usable[0]
    return None


def find_first(elements: list[Element], labels: tuple[str, ...] | list[str],
               **kwargs) -> Element | None:
    """The first of several labels that is present. Order is priority."""
    for label in labels:
        found = find(elements, label, **kwargs)
        if found:
            return found
    return None


def find_input(elements: list[Element], *,
               password: bool | None = None) -> Element | None:
    """Find a text field. Google's code and password boxes carry no label, so
    they are matched by class; `password=True` picks the masked one."""
    fields = [e for e in elements if e.is_input and e.enabled]
    if password is not None:
        fields = [e for e in fields if e.password is password]
    # A focused field is the one the app is asking about.
    return next((f for f in fields if f.focused), fields[0] if fields else None)


def texts(elements: list[Element]) -> str:
    """Everything on screen as one casefolded blob, for cheap screen
    recognition ("does this look like the 2FA page at all?")."""
    return " ".join(f"{e.text} {e.desc}" for e in elements).casefold()


# ------------------------------------------------------------------ acting
def tap_element(client: Client, phone_id: str, element: Element) -> bool:
    point = element.centre
    if not point:
        log.warning("element %r has unparseable bounds %r",
                    element.label, element.bounds)
        return False
    log.info("tapping %r at %s (clickable=%s)",
             element.label or element.cls, point, element.clickable)
    tap(client, phone_id, *point)
    return True


def tap_label(client: Client, phone_id: str, elements: list[Element],
              label: str) -> bool:
    element = find(elements, label)
    if not element:
        return False
    return tap_element(client, phone_id, element)


def tap_first_present(client: Client, phone_id: str, elements: list[Element],
                      labels: tuple[str, ...] | list[str]) -> str | None:
    """Tap the first label present and return which one - the primitive for
    clearing a chain of interstitials whose order is not known."""
    element = find_first(elements, labels, clickable_only=True)
    if not element:
        return None
    if not tap_element(client, phone_id, element):
        return None
    return element.label
