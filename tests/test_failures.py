"""Every failure a flow can report has to mean something.

This is the test the taxonomy exists for. A reason added to a flow used to
reach a build and be handled by whatever the default happened to be - which is
how `email_code_required` arrived mid-run and was classified by accident. Now
the flow cannot grow a reason without someone deciding, here, whose fault it is.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

from geelark_farm import failures

FLOWS = pathlib.Path(__file__).resolve().parents[1] / "src/geelark_farm/flows"


def reported_reasons() -> set[str]:
    """Every reason any flow can hand back.

    Uses the same function the sheet sync uses, so what the tests check and
    what the dropdowns offer cannot come apart - and so a flow module added
    later is covered without anyone remembering to list it here. An earlier
    version of this file named two of the three flows by hand and missed three
    reasons in the one it forgot (2026-08-12).
    """
    found: set[str] = set()
    for path in FLOWS.glob("*.py"):
        if path.stem.startswith("_"):
            continue
        module = importlib.import_module(f"geelark_farm.flows.{path.stem}")
        found |= failures.reasons_reported_by(module)
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


def test_a_flow_reports_more_than_its_literal_outcomes():
    """A flow names reasons two ways - literally in an Outcome() call, and as
    the keys of its own fatal tables, which reach Outcome through a variable.
    Reading only the first missed captcha_shown, a reason written to the sheet
    thirty times."""
    from geelark_farm.flows import google_login

    reported = failures.reasons_reported_by(google_login)

    assert "captcha_shown" in reported          # only ever named in a table
    assert "no_authenticator" in reported       # only ever an Outcome literal
    assert failures.SUCCESSES.isdisjoint(reported)


def test_the_dropdowns_would_offer_exactly_what_a_build_writes():
    """The lists were maintained by hand and drifted both ways at once: the
    Gmail column offered three device failures no build writes to an address,
    and omitted two credential reasons it does."""
    from geelark_farm.flows import google_login

    offered = sorted(r for r in failures.reasons_reported_by(google_login)
                     if failures.verdict(r).costs_the_credential)

    assert "captcha_shown" in offered
    assert "no_authenticator" in offered
    # device failures stop the phone; they never mark the address
    assert "too_many_attempts" not in offered
    assert "unknown_screen" not in offered


def test_every_reason_can_be_said_out_loud():
    """`seen` is what the sheet's Note columns are written from, so a reason
    without one would put its token back in front of a person.

    The shape matters as much as the presence: these get dropped into a larger
    sentence - `a@b.com (Google showed a CAPTCHA)` - so a trailing full stop or
    a leading capital reads as a seam.
    """
    for reason, verdict in failures.VERDICTS.items():
        assert verdict.seen, f"{reason} has no plain-language description"
        assert not verdict.seen.endswith("."), reason
        assert "_" not in verdict.seen, (
            f"{reason}'s description names a token: {verdict.seen!r}")
        # Lowercase, unless it opens on the name of whoever refused.
        first = re.match(r"[A-Za-z]+", verdict.seen).group()
        assert first[0].islower() or first in ("Google", "OpenAI", "Cloudflare"), (
            f"{reason}'s description is not a clause: {verdict.seen!r}")


def test_the_reasons_a_build_raises_itself_can_also_be_said():
    """`all_exits_refused` and its neighbours never reach VERDICTS - there is no
    credential to blame - but they do reach the Phones tab."""
    from geelark_farm import builder

    raised = set(re.findall(r'Aborted\("([a-z_]+)"', builder.__file__ and
                            pathlib.Path(builder.__file__).read_text("utf-8")))

    assert raised, "the scan found no Aborted() reasons"
    missing = sorted(r for r in raised if r not in failures.SITUATIONS)
    assert not missing, (
        f"these stop a build and land in the Phones tab with no way to say "
        f"them in words: {missing}. Add each to SITUATIONS in failures.py.")


def test_the_date_a_note_carries_needs_no_platform_branch():
    """Dropping a leading zero is a strftime flag, and it is not the same flag
    everywhere: `%-d` on BSD and glibc, `%#d` on Windows, each raising
    ValueError on the other. This was the only line in the package that
    branched on the platform - a poor thing to find out from a traceback
    inside a sheet write on a machine you are not sitting at."""
    import time

    for stamp, expected in (("2026-08-03", "3 Aug 2026"),
                            ("2026-08-13", "13 Aug 2026"),
                            ("2026-12-01", "1 Dec 2026")):
        parsed = time.strptime(stamp, "%Y-%m-%d")
        assert time.strftime("%d %b %Y", parsed).lstrip("0") == expected

    assert failures.today()          # and it runs here, whatever here is
    assert not failures.today().startswith("0")


def test_nothing_in_the_package_branches_on_the_operating_system():
    import pathlib

    src = pathlib.Path(failures.__file__).parent
    offenders = [f"{p.name}:{n}" for p in src.rglob("*.py")
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                 if ("os.name" in line or "sys.platform" in line
                     or "platform.system" in line)]

    assert not offenders, (
        f"{offenders} behave differently per platform. This runs on Windows "
        f"and on a Mac, and the difference should be in the standard library "
        f"rather than here.")
