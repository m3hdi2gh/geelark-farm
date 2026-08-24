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
    """Asked of the taxonomy, not of the table. The table stopped being the
    only source of a verdict when the `stuck_on_` family got a rule, and
    `reason in VERDICTS` is what this asked while twenty-one of them went
    unclassified and one reached a build (2026-08-16, phone 778)."""
    reasons = reported_reasons() - failures.SUCCESSES
    missing = sorted(r for r in reasons if not failures.knows(r))
    assert not missing, (
        f"these reasons reach a build with nothing said about them: {missing}. "
        f"Add each to VERDICTS in failures.py, deciding whether it is the "
        f"credential's fault, the exit's, or the device's.")


def test_the_scan_sees_the_reasons_the_router_builds_from_a_screen_name():
    """`Outcome("unknown", f"stuck_on_{matched.name}")` is a JoinedStr, so it
    is never a literal to be found - which is how a whole family stayed
    invisible to the check written to catch exactly this."""
    reported = reported_reasons()

    assert "stuck_on_totp_entry" in reported          # the one that reached 778
    assert "stuck_on_email_entry" in reported
    # Eighteen distinct across the flows, not twenty-two: the two login flows
    # both register `fatal`, `loading`, `email_entry` and `password_entry`, and
    # a screen of that name is the same reason wherever it is handled.
    # The eighteenth is `email_code_entry`, which was a fatal page until it
    # became a screen that answers the code OpenAI emails.
    assert "stuck_on_email_code_entry" in reported
    assert len({r for r in reported if r.startswith("stuck_on_")}) == 18


@pytest.mark.parametrize("screen", ["totp_entry", "2fa_method_list", "welcome"])
def test_a_stuck_screen_is_the_devices_problem_and_names_the_page(screen):
    """One rule rather than one entry per screen: they all mean the page kept
    coming back and the action was not moving it, whichever page it was."""
    found = failures.verdict(f"stuck_on_{screen}")

    assert found.stops_the_phone
    assert not found.costs_the_credential
    assert screen.replace("_", " ") in found.seen
    assert "artifacts/" in found.advice
    assert "no name for" not in found.seen


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
    for reason in failures.VERDICTS:
        # Rendered, not the raw table: three of these carry `{service}`, and
        # what has to read as a sentence is what the operator is shown.
        verdict = failures.verdict(reason, "OpenAI")
        assert verdict.seen, f"{reason} has no plain-language description"
        assert not verdict.seen.endswith("."), reason
        assert "_" not in verdict.seen, (
            f"{reason}'s description names a token: {verdict.seen!r}")
        assert "{" not in verdict.seen and "}" not in verdict.seen, (
            f"{reason} left a placeholder unfilled: {verdict.seen!r}")
        # Lowercase, unless it opens on the name of whoever refused.
        first = re.match(r"[A-Za-z]+", verdict.seen).group()
        assert first[0].islower() or first in ("Google", "OpenAI", "Cloudflare",
                                           "GeeLark"), (
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


# ------------------------------------------------ one table, not two
def test_a_build_that_ran_out_of_stock_has_advice_like_everything_else():
    """The reasons the builder raises itself lived in a second dictionary
    holding only a clause, so a row reading `no_usable_proxy` got a sentence
    where a row reading `wrong_password` got a sentence and something to do
    about it - and the console had to know which table a status came from
    before it could say anything (2026-08-17)."""
    for reason in failures.SITUATIONS:
        found = failures.verdict(reason)

        assert found.blame == failures.NOBODY, reason
        assert found.advice, reason
        assert "failures.py" not in found.advice, reason   # not the fallback


def test_nothing_is_spent_when_nobody_is_to_blame():
    """The whole point of the fourth blame: these stop a build without judging
    anything, so nothing may be marked, retired or swapped for them."""
    for reason in failures.SITUATIONS:
        found = failures.verdict(reason)

        assert not found.costs_the_credential, reason
        assert not found.needs_a_new_exit, reason
        assert not found.sets_aside, reason


def test_the_situations_are_the_table_rather_than_a_copy_of_it():
    """Two dictionaries drifted apart in shape once. Derived now, so the only
    way to add one is to add a verdict."""
    assert failures.SITUATIONS == {
        reason: found.seen for reason, found in failures.VERDICTS.items()
        if found.blame == failures.NOBODY}
    assert failures.SITUATIONS                     # and it is not empty


def test_a_situation_reads_as_the_clause_it_is_dropped_into():
    """`situation` completes "the build stopped ...", so the words have to
    survive being merged into the one table."""
    said = failures.situation("no_usable_proxy")

    assert said == "the Proxy tab had no free proxy to give it"
    assert said[0].islower() and not said.endswith(".")


def test_a_reason_nobody_has_classified_still_names_itself():
    assert failures.situation("something_new") == "it stopped with something_new"


# --------------------------------- a verdict that two flows share names neither
SERVICES = ("Google", "OpenAI", "Cloudflare", "Play Store")


def test_a_reason_two_flows_share_does_not_hardcode_one_of_them():
    """Three reasons - a CAPTCHA, a refused password, a refused 2FA code - are
    reported by both login flows, and all three were worded as though only
    Google could produce them. So an app account OpenAI turned down was
    written into the Gpt Info tab as "Google turned down the 2FA code", which
    sends the reader to the wrong service and the wrong tab. Five rows in
    History said exactly that (2026-08-20, phones 926, 932, 938, 939, 941).
    """
    from geelark_farm.flows import chatgpt_login, google_login

    shared = (failures.reasons_reported_by(google_login)
              & failures.reasons_reported_by(chatgpt_login))
    assert shared, "the scan found no shared reasons - it is looking wrong"

    for reason in sorted(shared & set(failures.VERDICTS)):
        raw = failures.VERDICTS[reason]
        for word in SERVICES:
            assert word not in raw.seen, (
                f"{reason} is reported by both flows but its description says "
                f"{word!r} - use {{service}}")
            assert word not in raw.advice, (
                f"{reason} is reported by both flows but its advice says "
                f"{word!r} - use {{service}}")


def test_the_shared_reasons_name_whoever_actually_refused():
    for reason in ("captcha_shown", "wrong_password", "wrong_2fa_code"):
        assert "OpenAI" in failures.verdict(reason, "OpenAI").seen, reason
        assert "Google" in failures.verdict(reason, "Google").seen, reason


def test_a_reason_only_one_flow_reports_may_name_it():
    """`no_authenticator_option` is Google's page and nothing else's, so
    spelling that out is the clearer thing to do, not a bug."""
    assert "Google" in failures.VERDICTS["no_authenticator_option"].seen


def test_the_tab_a_row_is_in_is_what_names_the_service():
    """Nothing has to be threaded to the console: a row's own pool knows who
    judges its credentials."""
    from geelark_farm.pools import AppPool, GmailPool

    assert GmailPool.service == "Google"
    assert AppPool.service == "OpenAI"


# ------------------------------------------- what the guard itself can see
def test_a_reason_named_through_a_variable_is_still_checked():
    """Both login flows end their fatal path with

        reason = _fatal_reason(ctx) or "unknown_fatal"

    and a literal in that position was invisible to the guard - so
    `unknown_fatal` was never actually checked against the table, and any
    reason introduced the same way would not have been either (2026-08-23).
    """
    from geelark_farm.flows import chatgpt_login, google_login

    for flow in (chatgpt_login, google_login):
        assert "unknown_fatal" in failures.reasons_reported_by(flow)


def test_the_guard_reads_a_reason_out_of_every_shape_it_is_written_in():
    """The four ways a flow names one, against a module written to use all
    four - so the guard is tested rather than the flows that happen to exist.
    """
    import ast
    import types

    module = types.ModuleType("fake_flow")
    module.FATAL_ADVICE = {"from_a_table": "..."}
    source = (
        "def a(ctx):\n"
        "    return Outcome('fatal', 'from_a_literal')\n"
        "\n"
        "def b(ctx):\n"
        "    reason = look(ctx) or 'from_a_variable'\n"
        "    return Outcome('fatal', reason)\n"
        "\n"
        "SCREENS = [Screen('a_page', match, act)]\n"
    )
    module.__source__ = source

    import inspect
    original = inspect.getsource
    inspect.getsource = lambda m: source if m is module else original(m)
    try:
        found = failures.reasons_reported_by(module)
    finally:
        inspect.getsource = original

    assert ast.parse(source)                       # the sample really parses
    assert found == {"from_a_literal", "from_a_table", "from_a_variable",
                     "stuck_on_a_page"}


def test_an_unclassified_reason_falls_back_without_looking_twice():
    """The fallback used to be `VERDICTS.get(reason, default)` three lines
    after establishing there was no entry, which can only return its default.
    """
    found = failures.verdict("something_nobody_named")

    assert found.blame == failures.DEVICE
    assert "something_nobody_named" in found.seen
    assert "failures.py" in found.advice


def test_the_group_nothing_is_to_blame_for_is_derived_not_declared():
    """It stays derived so that naming it can never become a second opinion
    about which reasons are in it."""
    assert failures.SITUATIONS == {
        reason: found.seen for reason, found in failures.VERDICTS.items()
        if found.blame == failures.NOBODY}
    assert failures.SITUATIONS


# ------------------------------------- the reasons the build decides itself
def test_every_status_the_builder_settles_a_phone_with_has_a_verdict():
    """Not read off a screen: the build decides these - the pool was empty,
    the install did not take, an exception reached the top. They end up on a
    Build and are read back through `verdict()` exactly like a flow's, and
    nothing checked them. Nine had none (2026-08-23)."""
    from geelark_farm import builder

    decided = failures.reasons_decided_by_the_builder(builder)
    missing = sorted(r for r in decided if not failures.knows(r))

    assert decided, "the scan found nothing, which cannot be right"
    assert not missing, (
        f"the builder can settle a phone with {missing} and failures.py says "
        f"nothing about them. Add an entry for each.")


def test_a_status_written_as_a_choice_of_two_is_still_seen():
    """`finish("phone_is_gone" if vanished else "phone_would_not_start", ...)`
    is neither half a bare Constant, and a scanner wanting one sees neither."""
    from geelark_farm import builder

    decided = failures.reasons_decided_by_the_builder(builder)

    assert {"phone_is_gone", "phone_would_not_start"} <= decided


def test_the_other_finish_does_not_contribute_a_row_number():
    """`book.phones.finish(row["sheet_row"], ...)` is a different `finish`,
    taking a row rather than a reason."""
    from geelark_farm import builder

    assert "sheet_row" not in failures.reasons_decided_by_the_builder(builder)


# ------------------------------------------------ the app phase's own prefix
def test_an_app_prefixed_reason_is_answered_by_the_one_underneath_it():
    """The prefix adds one fact to a reason that already has a verdict: it
    happened during the app login rather than during Google's."""
    inner = failures.verdict("rate_limited")
    outer = failures.verdict("app_rate_limited")

    assert failures.knows("app_rate_limited")
    assert outer.blame == inner.blame
    assert inner.seen in outer.seen
    assert outer.seen.startswith("the app login could not go on")


def test_the_table_answers_a_reason_that_begins_with_app_of_its_own():
    """`app_not_installed` and `app_would_not_start` are the table's, and the
    builder does not double the prefix on them."""
    for reason in ("app_not_installed", "app_would_not_start"):
        assert failures.verdict(reason) is not failures.verdict("nonsense")
        assert "could not go on" not in failures.verdict(reason).seen


def test_a_prefix_over_something_unclassified_is_still_unclassified():
    """The rule delegates; it does not invent."""
    assert not failures.knows("app_nonsense")


def test_a_state_the_phones_tab_holds_is_not_a_reason():
    """`ready` is not something that went wrong and wants no verdict."""
    from geelark_farm import builder

    assert not (failures.BUILD_STATES
                & failures.reasons_decided_by_the_builder(builder))
