"""Phase 0 acceptance: the package imports, and config behaves at the edges."""

from __future__ import annotations

import pytest

from geelark_farm.cli import build_parser
from geelark_farm.config import ConfigError, Settings, load_env


def test_cli_help_builds():
    """`geelark --help` must work with no .env at all - it is the phase 0
    acceptance test and the entry point for anyone new to the repo."""
    parser = build_parser()
    assert parser.prog == "geelark"


def test_settings_require_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr("geelark_farm.config.ENV_FILE", tmp_path / "absent.env")
    monkeypatch.delenv("GEELARK_APP_ID", raising=False)
    monkeypatch.delenv("GEELARK_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="GEELARK_APP_ID"):
        Settings.load()


def test_load_env_does_not_override_real_environment(monkeypatch, tmp_path):
    """Explicit environment beats the file, so a one-off override works."""
    env = tmp_path / ".env"
    env.write_text("GEELARK_APP_ID=from-file\n", encoding="utf-8")
    monkeypatch.setenv("GEELARK_APP_ID", "from-shell")
    load_env(env)
    import os

    assert os.environ["GEELARK_APP_ID"] == "from-shell"


def test_load_env_strips_quotes(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('GEELARK_API_KEY="quoted-value"\n', encoding="utf-8")
    monkeypatch.delenv("GEELARK_API_KEY", raising=False)
    load_env(env)
    import os

    assert os.environ["GEELARK_API_KEY"] == "quoted-value"
