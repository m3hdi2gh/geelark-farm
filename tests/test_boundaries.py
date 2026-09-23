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
BELOW_THE_BUILDER = ("pools", "products", "wishes", "runctx", "failures",
                     "breaker",
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


# ------------------------------------------ patches that no longer land
def test_a_patch_on_a_name_builder_no_longer_owns_is_refused(monkeypatch):
    """The split's main hazard: a caller moves out of builder.py, and the
    tests that patch `builder.X` keep passing while patching nothing."""
    from geelark_farm import builder, wishes

    assert builder.Wanted is wishes.Wanted
    with pytest.raises(AssertionError, match="wishes.Wanted now"):
        monkeypatch.setattr(builder, "Wanted", object)
    # What builder still owns - and the modules its code looks up through
    # it - may be patched as before.
    monkeypatch.setattr(builder, "_flow_for", lambda *a: None)
    monkeypatch.setattr(builder, "phones", builder.phones)


def test_every_migrated_module_is_scanned_as_part_of_the_builder():
    """The two lists are the same list: a module split out of builder.py
    is both a place a patch no longer lands and a file the verdict scans
    must read."""
    import conftest

    from tests.build_sources import BUILDER_MODULES

    assert set(conftest.MIGRATED_FROM_BUILDER) == set(BUILDER_MODULES) - {
        "builder"}


def test_no_verdict_is_written_outside_the_builders_files():
    """The reverse of `builder_sources`: a `finish("...")` or
    `Aborted("...")` with a literal reason in a file the verdict scans do
    not read is a builder module nobody added to the list - its reasons
    would reach a phone with nothing checking they have a verdict."""
    from tests.build_sources import builder_sources

    scanned = set(builder_sources())
    strays = []
    for path in SRC.rglob("*.py"):
        if path in scanned or "flows" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                continue
            func = node.func
            if (isinstance(func, ast.Name) and func.id in ("finish", "Aborted")) \
                    or (isinstance(func, ast.Attribute)
                        and func.attr == "Aborted"):
                strays.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not strays, strays


# ------------------------------------------------ one place for a switch
def test_the_device_switches_are_set_in_one_place(monkeypatch):
    """They were set in two copies inside serve.run, one per role, and a
    sign-in run by hand from the CLI set them nowhere (2026-09-23)."""
    from types import SimpleNamespace

    from geelark_farm import serve, shell, switches
    from geelark_farm.flows import google_login

    monkeypatch.setattr(shell, "HUMAN_CADENCE", False)
    monkeypatch.setattr(shell, "KERNEL_TOUCH", False)
    monkeypatch.setattr(google_login, "SIGN_IN_VIA", "settings")
    switches.apply(SimpleNamespace(human_cadence=True, kernel_touch=True,
                                   sign_in_via="play"))
    assert shell.HUMAN_CADENCE and shell.KERNEL_TOUCH
    assert google_login.SIGN_IN_VIA == "play"
    for path in SRC.rglob("*.py"):
        if path.name in ("switches.py", "shell.py", "google_login.py"):
            continue
        text = path.read_text(encoding="utf-8")
        for flag in (".HUMAN_CADENCE =", ".KERNEL_TOUCH =", ".SIGN_IN_VIA ="):
            assert flag not in text, f"{path.relative_to(SRC)} sets {flag}"
    assert "switches.apply(settings" in __import__("inspect").getsource(serve.run)


# --------------------------------------------------------- import smoke
def test_every_module_imports_in_a_fresh_process():
    """A module a deploy broke was found by the first job that imported
    it, on a pool thread, where the error went nowhere (2026-09-23). Here
    it is found before the commit: every module, in a process that has
    imported nothing else first."""
    import subprocess
    import sys

    names = sorted(
        "geelark_farm." + ".".join(p.relative_to(SRC).with_suffix("").parts)
        for p in SRC.rglob("*.py") if p.name != "__init__.py")
    script = ("import importlib, sys\n"
              "bad = []\n"
              f"for name in {names!r}:\n"
              "    try:\n"
              "        importlib.import_module(name)\n"
              "    except Exception as exc:\n"
              "        bad.append(f'{name}: {exc!r}')\n"
              "print(chr(10).join(bad)); sys.exit(1 if bad else 0)" + chr(10))
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, timeout=180)
    assert done.returncode == 0, done.stdout + done.stderr
