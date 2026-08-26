"""The README, checked against the thing it describes.

It had drifted far enough to mislead someone setting the tool up: four tabs
where there are six, columns that no longer exist, `in_use` given as what
every tab writes when the Proxy tab writes `claimed`, and a command that had
never existed at all (2026-08-17). Prose does not move when code is renamed,
so these are the claims worth pinning down.
"""

from __future__ import annotations

import re
from pathlib import Path

README = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")

#: Where a command is actually written: a ```bash fence, or inline code. Prose
#: and pasted output are neither - the sample of `geelark verify`'s own report
#: has a row labelled "geelark api", which is a heading and not a command.
SHELL = "\n".join(re.findall(r"```bash\n(.*?)```", README, re.S)
                  + re.findall(r"`([^`\n]+)`", README))


def test_every_command_it_shows_is_a_real_command():
    from geelark_farm.cli import build_parser

    actions = next(a for a in build_parser()._actions
                   if getattr(a, "choices", None) and a.dest == "command")
    named = set(re.findall(r"geelark ([a-z][a-z-]*)", SHELL))

    assert named, "no commands found - the sweep is looking in the wrong place"
    assert named <= set(actions.choices), named - set(actions.choices)


def test_every_flag_it_shows_is_a_real_flag():
    """`--release-stuck` and `--dry-run` are the kind of thing that gets
    renamed once and documented forever."""
    from geelark_farm.cli import build_parser

    subcommands = next(a for a in build_parser()._actions
                       if getattr(a, "choices", None) and a.dest == "command")

    for command, flag in re.findall(r"geelark ([a-z-]+)((?: --[a-z-]+)+)",
                                    README):
        parser = subcommands.choices.get(command)
        if parser is None:                    # covered by the test above
            continue
        known = {option for action in parser._actions
                 for option in action.option_strings}
        for one in flag.split():
            assert one in known, f"geelark {command} has no {one}"


def test_every_path_it_links_to_exists():
    root = Path(__file__).parent.parent
    for link in re.findall(r"\]\(([^)#][^)]*)\)", README):
        assert (root / link).exists(), link


def test_the_status_words_it_documents_are_the_ones_the_pools_write():
    """The words are the whole contract with the operator - they read them in
    the tab and type them back into it."""
    from geelark_farm.pools import AppPool, GmailPool, ProxyPool

    for pool in (GmailPool, ProxyPool, AppPool):
        for word in (pool.claimed_status, pool.spent_status,
                     pool.retired_status):
            assert f"`{word}`" in README, f"{pool.__name__}: {word!r}"

    for word in (ProxyPool.needs_new_ip, ProxyPool.dead_status):
        assert f"`{word}`" in README, word


def section(heading: str) -> str:
    """One section of the README, up to the next heading of any level."""
    start = README.index(heading) + len(heading)
    rest = README[start:]
    end = re.search(r"^#{2,3} ", rest, re.M)
    return rest[:end.start()] if end else rest


def first_column(text: str) -> set[str]:
    return set(re.findall(r"^\| `([a-z_ ]+)` \|", text, re.M))


def test_the_state_column_it_documents_is_the_one_the_tool_reads():
    """`State` is the operator's instruction back to the tool, so a value the
    README invents is one they would write and nothing would carry out - and
    one it leaves out is a thing the tool does that nobody knows about."""
    from geelark_farm.pools import PhoneLog

    documented = first_column(section("### State: an instruction back"))

    assert documented == {PhoneLog.DONE, PhoneLog.FAILED, PhoneLog.UNUSED}


def test_the_blames_it_documents_are_the_ones_the_table_hands_out():
    """What a failure costs follows from its blame, so this table is the one
    place the README explains the tool's central decision."""
    from geelark_farm import failures

    documented = first_column(section("## What a failure costs"))

    assert documented == {v.blame for v in failures.VERDICTS.values()}


def test_the_failures_it_names_as_examples_are_real_reasons():
    """An example that no longer exists sends someone searching the sheet for
    a status nothing writes."""
    from geelark_farm import failures
    from geelark_farm.pools import AppPool, GmailPool, ProxyPool

    # A snake_case word in backticks is one of two things, and both come from
    # the code: a failure reason or a pool's status word.
    statuses = {word for pool in (GmailPool, ProxyPool, AppPool)
                for word in (pool.claimed_status, pool.spent_status,
                             pool.retired_status)}
    known = set(failures.VERDICTS) | set(failures.SITUATIONS) | statuses
    named = set(re.findall(r"`([a-z]+(?:_[a-z]+)+)`", README))
    named -= {"service_account", "geelark_farm"}    # paths, not reasons

    assert named, "no reasons found - the sweep is looking in the wrong place"
    assert named <= known, f"named but not in the code: {named - known}"


def test_it_names_every_tab_the_tool_uses():
    from geelark_farm import pools

    for tab in (pools.GMAILS_TAB, pools.PROXY_TAB, pools.APPS_TAB,
                pools.PHONES_TAB, pools.LISTS_TAB, pools.HISTORY_TAB):
        assert tab in README, tab


def test_it_names_every_column_the_tool_writes():
    """A column left out of the README is one nobody creates, and `_set` skips
    a column that is not there without saying so."""
    from geelark_farm.verify import REQUIRED_COLUMNS

    for columns in REQUIRED_COLUMNS.values():
        for column in columns:
            assert f"`{column}`" in README, column


def test_it_says_the_install_has_to_be_editable():
    """Everything is resolved from the repository root, which the package
    finds from its own location - installed non-editable, .env is never found
    and the working directories land in site-packages."""
    assert "-e" in README
    assert "editable" in README.lower()


def test_it_names_the_optional_columns_that_change_what_happens():
    """`REQUIRED_COLUMNS` is what the code cannot run without, and the test
    above pins those. It does not cover the two that are optional and matter
    anyway - and both were missing from the tables here until somebody went
    looking (2026-08-26).

    `Claimed` is written by the tool and decides when a stuck row comes back;
    `Email code` is one a person has to tick, and an undocumented checkbox is
    a cell in the sheet that nothing explains.
    """
    from geelark_farm.pools import AppPool, GmailPool

    assert f"`{GmailPool.claimed_at_column}`" in README
    for column in AppPool.checkbox_columns:
        assert f"`{column}`" in README, column


def test_every_setting_the_code_reads_is_in_the_example_file():
    """The README points at `.env.example` as the complete reference - "every
    field is documented in" it - so a setting missing from it makes that
    sentence false. Two were (2026-08-26), one of them added the same day."""
    root = Path(__file__).parent.parent
    config = (root / "src" / "geelark_farm" / "config.py").read_text(
        encoding="utf-8")
    example = (root / ".env.example").read_text(encoding="utf-8")

    reads = set(re.findall(r'_(?:str|int|path)\("([A-Z_]+)"', config))
    assert reads, "the pattern stopped matching how settings are read"

    for name in sorted(reads):
        assert name in example, f"{name} is read but not in .env.example"
