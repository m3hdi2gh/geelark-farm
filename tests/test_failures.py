"""Every failure a flow can report has to mean something.

This is the test the taxonomy exists for. A reason added to a flow used to
reach a build and be handled by whatever the default happened to be - which is
how `email_code_required` arrived mid-run and was classified by accident. Now
the flow cannot grow a reason without someone deciding, here, whose fault it is.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from geelark_farm import failures

FLOWS = pathlib.Path(__file__).resolve().parents[1] / "src/geelark_farm/flows"


def reported_reasons() -> set[str]:
    """Every literal a flow passes to Outcome() as its reason.

    Read out of the source rather than listed by hand, because a list by hand
    is exactly the thing that goes stale - and going stale is the failure this
    test is here to catch.
    """
    found: set[str] = set()
    for path in FLOWS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Outcome"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                found.add(node.args[1].value)
    return found


def test_the_flows_actually_report_something():
    """If this finds nothing the scan has broken, and the test below would
    pass by knowing nothing."""
    assert len(reported_reasons()) > 10


def test_every_reported_reason_has_a_verdict():
    reasons = reported_reasons() - failures.SUCCESSES
    missing = sorted(r for r in reasons if r not in failures.VERDICTS)
    assert not missing, (
        f"these reasons reach a build with nothing said about them: {missing}. "
        f"Add each to VERDICTS in failures.py, deciding whether it is the "
        f"credential's fault, the exit's, or the device's.")


def test_the_fatal_reason_tables_are_covered_too():
    """google_login and chatgpt_login name reasons in dict literals as well as
    in Outcome() calls - FATAL_TEXTS and friends. Those reach a build the same
    way."""
    from geelark_farm.flows import chatgpt_login, google_login

    named: set[str] = set()
    for module in (google_login, chatgpt_login):
        for attribute in dir(module):
            value = getattr(module, attribute)
            if isinstance(value, dict) and attribute.isupper():
                named.update(k for k in value if isinstance(k, str))
    missing = sorted(r for r in named - failures.SUCCESSES
                     if r not in failures.VERDICTS)
    assert not missing, f"named in a flow's tables but not classified: {missing}"


@pytest.mark.parametrize("reason,blame", [
    ("captcha_shown", failures.CREDENTIAL),
    ("wrong_password", failures.CREDENTIAL),
    ("network_ssl_rejected", failures.EXIT),
    ("request_rejected", failures.EXIT),
    ("app_would_not_start", failures.DEVICE),
    ("unknown_screen", failures.DEVICE),
])
def test_the_decisions_that_cost_money(reason, blame):
    """The four that were wrong at some point this week, pinned by name.

    A CAPTCHA is the account's: filing it under the exit spent a proxy and
    then retried the Gmail that caused it. A network refusal is the exit's:
    filing it under the credential condemned an account OpenAI never examined.
    """
    assert failures.verdict(reason).blame == blame


def test_an_unclassified_reason_stops_the_phone_rather_than_the_pool():
    """The safe default in the only sense that matters: it spends nothing more
    while nobody knows what happened."""
    unknown = failures.verdict("something_nobody_has_seen_yet")

    assert unknown.stops_the_phone
    assert not unknown.costs_the_credential
    assert "failures.py" in unknown.advice
