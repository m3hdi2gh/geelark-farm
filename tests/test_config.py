"""Phase 0 acceptance: the package imports, and config behaves at the edges."""

from __future__ import annotations

import pytest

from geelark_farm.cli import build_parser
from geelark_farm.config import ConfigError, Settings, load_env, machine


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


# ------------------------------ a key padded with characters nobody can see
def test_a_secret_padded_with_invisible_characters_is_still_a_key():
    """A secret pasted out of a browser or a chat window arrives padded with
    fillers that render as nothing, so the row looks perfectly ordinary and is
    refused for holding a character nobody can see. Two Gpt Info rows sat
    unusable that way, each with a valid key behind two U+3164 (2026-08-22)."""
    from geelark_farm.accounts import check_totp_secret, normalize_totp_secret

    real = "ㅤㅤ BI4ZAPC7QRFZDYDULON5KWNGP7F33WWO"

    cleaned = normalize_totp_secret(real)

    assert cleaned == "BI4ZAPC7QRFZDYDULON5KWNGP7F33WWO"
    check_totp_secret(cleaned)             # and it produces codes


def test_dropping_any_named_character_cannot_change_a_key():
    """The list is a promise, and this is the half of it a machine can check:
    none of these is part of a base32 key, so removing one can never turn a
    valid secret into a different valid secret.

    That they are also invisible is a human judgement - Unicode files them
    under four different categories, and the braille blank is a symbol - so
    the reason each earns its place is written beside the list, not asserted
    here."""
    from geelark_farm.accounts import BASE32_ALPHABET, INVISIBLE

    for char in INVISIBLE:
        assert char not in BASE32_ALPHABET, repr(char)
    assert len(set(INVISIBLE)) == len(INVISIBLE)      # and none listed twice

    # Not `isalnum`, which is true of the character that prompted all this:
    # Unicode files the Hangul filler as a letter. Being outside the alphabet
    # is the whole of the guarantee.
    assert "ㅤ".isalnum()


def test_something_pasted_into_the_wrong_column_is_still_refused():
    """The reason this strips only the invisible ones. A cell holding
    `fifa19.900t@pAss` - a password that had drifted a column - came back as a
    padding complaint and sent the reader to the wrong thing entirely
    (2026-08-09). Stripping anything outside base32 would make it decode."""
    import pytest

    from geelark_farm.accounts import (
        AccountError,
        check_totp_secret,
        normalize_totp_secret,
    )

    with pytest.raises(AccountError) as caught:
        check_totp_secret(normalize_totp_secret("fifa19.900t@pAss"))

    assert "." in str(caught.value) or "@" in str(caught.value)


# ------------------------------------------ who a machine says it is
def test_a_container_can_declare_a_stable_name(monkeypatch):
    """Docker hands a container a fresh random hex id for a hostname on every
    `run`. Without an override the History tab would fill with names that mean
    nothing and the log would start a new file each time the service came
    back - and which machine wrote a row is the whole reason both exist."""
    monkeypatch.setenv("GEELARK_MACHINE", "geelark-server")
    assert machine() == "geelark-server"


def test_the_hostname_is_still_the_answer_without_one(monkeypatch):
    """Nothing on a laptop needs to set this."""
    monkeypatch.delenv("GEELARK_MACHINE", raising=False)
    assert machine()                 # whatever the host is called, not blank


def test_a_declared_name_is_sanitised_like_any_other(monkeypatch):
    """It lands in filenames, the same as a hostname does."""
    monkeypatch.setenv("GEELARK_MACHINE", "geelark server.01")
    assert machine() == "geelark-server-01"


# ------------------------------------------- where the tool's own files live
def test_a_source_checkout_is_found_by_the_file_that_marks_one(monkeypatch):
    """Two levels above the package, when `pyproject.toml` is actually there."""
    from geelark_farm import config

    monkeypatch.delenv("GEELARK_ROOT", raising=False)
    root = config._root()

    assert (root / "pyproject.toml").is_file()
    assert (root / "src" / "geelark_farm" / "config.py").is_file()


def test_an_installed_package_falls_back_to_the_working_directory(monkeypatch,
                                                                  tmp_path):
    """`pip install .` puts the package in site-packages, where two levels up
    is the interpreter's library directory - not a project at all.

    Trusting the arithmetic there resolves `.env` somewhere it cannot be and
    writes `state/`, `logs/` and `artifacts/` into the installed library. The
    ledger goes with them, and one that dies with the container cannot account
    for the phones a restart interrupted (2026-08-23).
    """
    from geelark_farm import config

    monkeypatch.delenv("GEELARK_ROOT", raising=False)
    installed = tmp_path / "lib" / "python3.12" / "site-packages" / "geelark_farm"
    installed.mkdir(parents=True)
    monkeypatch.setattr(config, "__file__", str(installed / "config.py"))
    monkeypatch.chdir(tmp_path)

    assert config._root() == tmp_path.resolve()


def test_the_root_can_be_stated_outright(monkeypatch, tmp_path):
    """What a container should set, rather than depending on either guess."""
    from geelark_farm import config

    monkeypatch.setenv("GEELARK_ROOT", str(tmp_path))

    assert config._root() == tmp_path.resolve()
