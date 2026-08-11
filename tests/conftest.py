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

from collections.abc import Callable

import pytest

from geelark_farm.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        app_id="APPID", api_key="APIKEY", sxorg_api_key="",
        sheet_id="",
        service_account_json="/nowhere", region="sgp", android="Android 15",
        phone_name_prefix="farm", target_package="com.example",
        max_concurrent_phones=1, build_budget_seconds=3600,
        login_budget_seconds=900, install_budget_seconds=600,
        app_login_budget_seconds=600,
        api_requests_per_minute=120, state_dir="/tmp", artifact_dir="/tmp",
        log_level="INFO",
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
