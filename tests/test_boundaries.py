"""The lines the builder split is drawn along, held by tests (the builder
review, 2026-09-23).

builder.py is being taken apart one leaf at a time, and every step is a
chance for a boundary to erode without anybody noticing: a module that
should not know about the builder importing it, a test patching a name
that no caller looks up any more, a vocabulary word copied instead of
imported. These are the checks that make each of those loud.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "geelark_farm"


def _imports(path: pathlib.Path) -> set[str]:
    """Every geelark_farm module this file imports, anywhere in it, as a
    dotted name relative to the package ("builder", "store.jobs")."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = path.relative_to(SRC).with_suffix("").parts[:-1]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(package[:len(package) - (node.level - 1)])
                mod = ".".join([*base, *(node.module or "").split(".")]).strip(".")
                if node.module is None or not node.module:
                    for alias in node.names:
                        found.add(".".join([*base, alias.name]).strip("."))
                else:
                    found.add(mod)
                    for alias in node.names:
                        found.add(f"{mod}.{alias.name}")
            elif (node.module or "").startswith("geelark_farm"):
                mod = node.module.removeprefix("geelark_farm").strip(".")
                found.add(mod)
                for alias in node.names:
                    found.add(f"{mod}.{alias.name}".strip("."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("geelark_farm."):
                    found.add(alias.name.removeprefix("geelark_farm."))
    return found


def _module(name: str) -> pathlib.Path:
    return SRC / (name.replace(".", "/") + ".py")


# ------------------------------------------------------------- layers
#: Modules that must never import builder.py, anywhere in them - not at
#: the top, not inside a function. Each is below the builder or beside
#: it; an import of it from here is the start of a cycle. `pools` was in
#: one until 2026-09-23 (`sync_lists` reached back for possible_statuses).
BELOW_THE_BUILDER = ("pools", "products", "wishes", "failures", "breaker",
                     "phones",
                     "shell", "accounts", "api", "proxy", "ledger",
                     "config", "codes", "mailbox", "apps",
                     "flows.router", "flows.google_login",
                     "flows.chatgpt_login", "flows.claude_login",
                     "flows.spotify_login", "flows.play_install",
                     "flows.recaptcha")


@pytest.mark.parametrize("name", BELOW_THE_BUILDER)
def test_nothing_below_the_builder_imports_it(name):
    imported = _imports(_module(name))
    assert "builder" not in imported and not any(
        i.startswith("builder.") for i in imported), (
        f"{name} imports builder - that is a cycle; the name it wants "
        f"belongs in {name} or below it")


def test_the_registry_is_a_leaf():
    """products.py resolves flows by name at call time: importing it must
    not import a sign-in flow, the builder or the pools."""
    imported = _imports(_module("products"))
    assert not {i for i in imported if i.split(".")[0] in
                {"builder", "pools", "flows", "phones", "shell", "store"}}


# ------------------------------------------------------ one owner a word
def test_the_builders_words_are_their_owners_objects():
    """Copies drift: `"change ip"` in HELD_BACK was a second spelling of
    ProxyPool.needs_new_ip, and READY/APP_ONLY lived in two classes."""
    from geelark_farm import breaker, builder, failures
    from geelark_farm.pools import PhoneLog, ProxyPool

    assert builder.READY is PhoneLog.READY
    assert builder.APP_ONLY is PhoneLog.APP_ONLY
    assert builder.SUSPECT is ProxyPool.suspect_status
    assert builder.HELD_BACK is ProxyPool.held_back_statuses
    assert ProxyPool.needs_new_ip in ProxyPool.held_back_statuses
    assert builder.WARM_FOR_OPERATOR is failures.WARM_FOR_OPERATOR
    assert failures.WARM_FOR_OPERATOR in breaker.WORKED
    assert failures.WARM_FOR_OPERATOR in failures.VERDICTS
    assert builder.possible_statuses() == PhoneLog.possible_statuses()


def test_dead_code_stays_gone():
    from geelark_farm import builder

    for name in ("_this_module", "reclaim_proxies"):
        assert not hasattr(builder, name), name
