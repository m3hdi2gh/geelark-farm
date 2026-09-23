"""Shared test fixtures.

Everything here is offline: no network, no phone, no .env. The suite must run on
a machine that has never seen a GeeLark credential, which is what makes it
useful in CI.

These are fixtures rather than importable helpers on purpose. A test module that
does `from tests.conftest import ...` needs `tests` to be an importable package,
which it is on a developer machine and is not on a clean CI runner - the first
CI run failed on exactly that. pytest injects fixtures by name and never needs
the import at all.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from geelark_farm.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        app_id="APPID", api_key="APIKEY",
        sheet_id="",
        # Paths, not strings: `Settings` declares these as Path and the code
        # calls Path methods on them. A fake that hands over strings passes
        # until the day something calls `.mkdir()`, and then fails in a test
        # that has nothing to do with the change (2026-08-23).
        service_account_json=Path("/nowhere"), region="sgp",
        android="Android 15",
        phone_name_prefix="farm", target_package="com.example",
        max_concurrent_phones=1, build_budget_seconds=3600,
        # What `serve` keeps in stock and how often it looks. Small here, so a
        # test that drives the loop does not have to override them to say
        # anything about it.
        warm_stock=2, serve_interval_seconds=1,
        # Its own number now: a live run refreshes what it holds, so how long
        # a claim may go unrefreshed no longer has to be the build budget.
        stale_claim_seconds=3600,
        login_budget_seconds=900, install_budget_seconds=600,
        app_login_budget_seconds=600,
        api_requests_per_minute=120,
        state_dir=Path(tempfile.gettempdir()),
        artifact_dir=Path(tempfile.gettempdir()),
        log_dir=Path(tempfile.gettempdir()), log_level="INFO",
        # Prose, which is what the file has always been and what a test that
        # reads a log line expects. The JSON path has its own tests.
        log_format="text",
        # Off, as it is on a server that has not been told otherwise: a pass
        # that waits is what every test of the loop is written against.
        serve_concurrent=False,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Build a fully populated Settings that touches nothing real.

    A factory rather than a value, because most tests need to override a field
    - usually state_dir, to point it at tmp_path.
    """
    return _settings


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.fixture(autouse=True)
def _one_ledger_per_test():
    """`Ledger.shared` keeps one object per file for the process; a test
    must not inherit the last test's claims through it."""
    from geelark_farm import ledger as ledger_mod

    ledger_mod._SHARED.clear()
    yield
    ledger_mod._SHARED.clear()


# ------------------------------------------ patches that no longer land
#: The modules builder.py has handed names to, by the builder review's
#: split (2026-09-23). Add the module each step creates.
MIGRATED_FROM_BUILDER = ("products", "wishes", "runctx", "cancel",
                         "exit_health")


def _stale_patch(target, name) -> str:
    """Why patching `target.name` would not reach the code it is meant
    for, or "".

    `monkeypatch.setattr(builder, "X", fake)` replaces what builder's own
    globals say `X` is. Once `X` lives in another module, its callers
    there look it up in that module and never see the fake - the test
    patches nothing and passes, or quietly reaches the real thing. So a
    patch on builder of a function or class a migrated module owns is
    refused, naming where to patch instead.
    """
    import inspect
    import sys

    builder = sys.modules.get("geelark_farm.builder")
    if builder is None or target is not builder or not isinstance(name, str):
        return ""
    if not hasattr(builder, name):
        return ""
    current = getattr(builder, name)
    if inspect.ismodule(current):
        return ""                      # builder's code looks these up here
    owner = ""
    if inspect.isfunction(current) or inspect.isclass(current):
        owner = getattr(current, "__module__", "") or ""
        if owner not in {f"geelark_farm.{m}" for m in MIGRATED_FROM_BUILDER}:
            owner = ""
    if not owner:
        # State and constants: a set, a dict, a number re-exported from
        # the module that owns it now. Replacing builder's name for it
        # leaves the owner's - the one its code reads - as it was.
        for m in MIGRATED_FROM_BUILDER:
            module = sys.modules.get(f"geelark_farm.{m}")
            if module is not None and getattr(module, name, None) is current:
                owner = module.__name__
                break
    if owner:
        return (f"builder.{name} is {owner}.{name} now - patch it there "
                f"(or where its caller looks it up); a patch on builder "
                f"reaches nothing")
    return ""


def _install_stale_patch_guard() -> None:
    """Wrap whatever `MonkeyPatch.setattr` is current - scripts/
    audit_fakes.py wraps it too, and the two must chain."""
    from _pytest.monkeypatch import NOTSET, MonkeyPatch

    current = MonkeyPatch.setattr
    if getattr(current, "_refuses_stale_patches", False):
        return

    def setattr(self, target, name=NOTSET, value=NOTSET, raising=True):
        if not isinstance(target, str):
            why = _stale_patch(target, name)
            if why:
                raise AssertionError(why)
        return current(self, target, name, value, raising)

    setattr._refuses_stale_patches = True
    MonkeyPatch.setattr = setattr


_install_stale_patch_guard()
