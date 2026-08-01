"""Shared test fixtures.

Everything here is offline: no network, no phone, no .env. The suite must run on
a machine that has never seen a GeeLark credential, which is what makes it
useful in CI.
"""

from __future__ import annotations

import pytest

from geelark_farm.config import Settings


def make_settings(**overrides) -> Settings:
    """A fully populated Settings that touches nothing real."""
    base = dict(
        app_id="APPID", api_key="APIKEY", sheet_id="", sheet_tab="accounts",
        service_account_json="/nowhere", region="sgp", android="Android 15",
        phone_name_prefix="farm", target_package="com.example",
        max_concurrent_phones=1, account_budget_seconds=1800,
        login_budget_seconds=900, install_budget_seconds=600,
        api_requests_per_minute=120, state_dir="/tmp", artifact_dir="/tmp",
        log_level="INFO",
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()
