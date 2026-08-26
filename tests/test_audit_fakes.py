"""The fake auditor itself.

A dev tool, so this is not exhaustive. It holds the three pure functions - the
reading it does - and the two things that were wrong before any of it said
anything true: `dir()` missing a dataclass field with no default, so a fake
carrying that field read as having something the real class lacked, and
`X | None` compared against `types.UnionType` rather than against its arms,
which made every optional return a finding.

What it does not hold is the plugin end: `audited`, the terminal summary and
the runner. Those need a live pytest session to reach, and the way they are
checked is by running the tool - `python scripts/audit_fakes.py` - and reading
what comes back.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.audit_fakes import accepts, returns_wrong, surface  # noqa: E402


@dataclass
class Entry:
    """Shaped like `ledger.Entry`: a field with no default, and one with."""

    phone_id: str
    note: str = ""

    @property
    def is_claimed(self) -> bool:
        return False


class Lookalike:
    """A fake with the same two fields and one the real class has not got."""

    phone_id = "P1"
    note = ""
    trail: list[str] = []


# ------------------------------------------------------------------ surface
def test_a_field_with_no_default_is_part_of_the_surface():
    """It is an annotation and nothing else until an instance exists, so
    `dir(Entry)` does not list it - and reading the surface off `dir` alone
    reported `phone_id` as something the fake had and the real class did
    not, which is backwards."""
    assert "phone_id" not in dir(Entry)
    assert {"phone_id", "note", "is_claimed"} <= surface(Entry)


# ------------------------------------------------------------ returns_wrong
def real_list() -> list[str]:
    return []


def real_optional() -> str | None:
    return None


def real_nothing() -> None:
    return None


def real_entry() -> Entry:
    return Entry("P1")


def real_unannotated():
    return None


def test_an_answer_the_annotation_allows_is_not_reported():
    assert returns_wrong(real_list, ["a"]) is None
    assert returns_wrong(real_nothing, None) is None
    assert returns_wrong(real_entry, Entry("P1")) is None


def test_both_arms_of_an_optional_are_allowed():
    """`X | None` is a union, and comparing the answer against the union
    itself rather than against its arms made every one of these a finding."""
    assert returns_wrong(real_optional, "text") is None
    assert returns_wrong(real_optional, None) is None


def test_an_answer_neither_arm_of_an_optional_allows_is_reported():
    """The other half of the union: allowing both arms is only useful if
    something outside them is still caught."""
    found = returns_wrong(real_optional, 7)

    assert found == "annotated -> str|NoneType, returned int"


def test_a_dict_where_a_list_was_promised_is_reported():
    """The shape `step` reads: it merges a dict into the sync report and files
    a list under the step's own name, so the two are not interchangeable."""
    found = returns_wrong(real_list, {})

    assert found and "annotated -> list" in found and "returned dict" in found


def test_something_where_nothing_was_promised_is_reported():
    found = returns_wrong(real_nothing, ["P1"])

    assert found == "annotated -> None, returned list"


def test_an_unannotated_function_says_nothing_either_way():
    """No annotation is not a promise, so there is nothing to break."""
    assert returns_wrong(real_unannotated, object()) is None


def test_an_annotation_that_forbids_nothing_is_not_a_finding():
    """`Any` is not a promise, and `Literal` is not a class - reading either
    as one would report every answer they allow."""
    import typing

    def anything() -> typing.Any:
        return None

    def literal() -> typing.Literal["a"]:
        return "a"

    assert returns_wrong(anything, object()) is None
    assert returns_wrong(literal, "a") is None


def test_the_dangerous_direction_is_named():
    """A fake carrying what the real class has not got is the ten-build
    outage: code written against it is blessed by every test and finds
    nothing there in production."""
    found = returns_wrong(real_entry, Lookalike())

    assert found and "fake HAS but real lacks: ['trail']" in found


def test_the_safe_direction_is_reported_apart_from_it():
    """A fake with less on it fails loudly the first time the code reaches
    for the missing part, so it is worth saying and not worth alarm."""
    class Thin:
        phone_id = "P1"

    found = returns_wrong(real_entry, Thin())

    assert found and "fake HAS but real lacks" not in found
    assert "real has but fake lacks" in found


def test_a_builtin_standing_in_for_a_class_does_not_list_its_methods():
    """A str where a Panel was promised is worth one line. The forty string
    methods it brings with it are not the finding."""
    found = returns_wrong(real_entry, "just text")

    assert found and "fake HAS but real lacks" not in found


# ------------------------------------------------------------------ accepts
def test_a_fake_that_would_refuse_the_real_call_is_reported():
    def real(client, phone_id, *, strict=False) -> str:
        return ""

    assert accepts(real, lambda client: "") is not None


def test_a_fake_taking_nothing_at_all_is_reported():
    """The required arguments are what the call is built from. Built from the
    optional ones instead, this fake looks fine."""
    def real(client) -> None:
        return None

    assert accepts(real, lambda: None) is not None


def test_a_fake_shaped_exactly_like_the_real_one_is_not_reported():
    """Keyword-only arguments have to be passed as keywords. Passed
    positionally, a fake that matches perfectly is reported as broken."""
    def real(client, *, token) -> None:
        return None

    assert accepts(real, lambda client, *, token: None) is None


def test_a_fake_that_swallows_anything_is_not():
    def real(client, phone_id, *, strict=False) -> str:
        return ""

    assert accepts(real, lambda *a, **k: "") is None


def test_an_optional_argument_the_fake_leaves_out_is_not_a_finding():
    """Production may never pass it, and a fake that ignores it is the
    ordinary way to write one."""
    def real(client, ledger=None) -> None:
        return None

    assert accepts(real, lambda client: None) is None


# ------------------------------------------------------------------ watched
def test_only_a_function_is_wrapped_to_watch_what_it_answers():
    """A class or a plain value put in a module's place is not a call, and
    wrapping it would change what the test under audit is holding."""
    from scripts.audit_fakes import watched

    class FakeModule:
        pass

    assert watched(real_entry, FakeModule, "x") is FakeModule


def test_a_wrapped_fake_still_answers_what_it_answered():
    """The audit watches; it must not alter the run it is watching."""
    from scripts.audit_fakes import watched

    def fake() -> Entry:
        return Entry("P9")

    assert watched(real_entry, fake, "x")().phone_id == "P9"


def test_a_fake_answering_the_wrong_shape_is_recorded_when_it_is_called():
    """The wrapping is the whole mechanism: the shape is not knowable at the
    moment of patching, only at the moment the fake answers."""
    from scripts.audit_fakes import FINDINGS, watched

    def fake() -> Entry:
        return "not an Entry at all"

    wrapped = watched(real_entry, fake, "a target no other test uses")

    assert wrapped is not fake
    wrapped()
    assert any(entry["target"] == "a target no other test uses"
               for entry in FINDINGS.values())
