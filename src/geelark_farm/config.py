"""Settings, loaded once from the environment (and a .env file if present).

Everything configurable lives here so no other module reads os.environ. Values
are validated on access rather than at import, so `geelark --help` works
without a populated .env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class ConfigError(Exception):
    """A required setting is missing or malformed."""


def load_env(path: Path | None = None) -> None:
    """Populate os.environ from a KEY=VALUE file.

    Existing environment variables win, so `GEELARK_APP_ID=x geelark ...` and
    CI secrets both override the file. Missing file is not an error - the
    settings may come from the real environment.
    """
    env_path = path or ENV_FILE
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _str(key: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(key, default)
    if required and not value:
        raise ConfigError(
            f"{key} is not set. Copy .env.example to .env and fill it in."
        )
    return value or ""


def _int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc


def _path(key: str, default: str) -> Path:
    raw = _str(key, default)
    path = Path(raw)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


@dataclass(frozen=True)
class Settings:
    """Resolved configuration for one process run."""

    # GeeLark API credentials.
    app_id: str
    api_key: str

    # The proxy vendor's key. Optional: without it a build simply takes the
    # next proxy instead of refreshing the one it has.
    sxorg_api_key: str

    # Google Sheets input.
    sheet_id: str
    sheet_tab: str
    service_account_json: Path

    # Phone specs. region 'us' only offers Android 15; sgp and cn are broader.
    region: str
    android: str
    phone_name_prefix: str

    # What to install, addressed by package id so the Play Store is never
    # searched by name (a text search can match a clone).
    target_package: str

    # Budgets. Billing is per running minute, so each of these caps spend as
    # well as time.
    max_concurrent_phones: int
    account_budget_seconds: int
    # What one `geelark build` phone may spend. Larger than the per-account
    # budget on purpose: a build may try several Gmails and several app
    # accounts on one phone, and the point of it is that a bad credential costs
    # a credential rather than the phone.
    build_budget_seconds: int
    login_budget_seconds: int
    install_budget_seconds: int
    app_login_budget_seconds: int

    # GeeLark bans a key for 2 hours past 200 req/min; stay well under.
    api_requests_per_minute: int

    # Local output.
    state_dir: Path
    artifact_dir: Path
    log_level: str

    _sheets_checked: bool = field(default=False, repr=False, compare=False)

    @classmethod
    def load(cls) -> Settings:
        """Read settings from the environment, requiring only what every
        command needs. Sheets settings are validated by require_sheets()."""
        load_env()
        return cls(
            app_id=_str("GEELARK_APP_ID", required=True),
            api_key=_str("GEELARK_API_KEY", required=True),
            sxorg_api_key=_str("SXORG_API_KEY"),
            sheet_id=_str("GOOGLE_SHEET_ID"),
            sheet_tab=_str("GOOGLE_SHEET_TAB", "accounts"),
            service_account_json=_path(
                "GOOGLE_SERVICE_ACCOUNT_JSON", "./secrets/service-account.json"
            ),
            region=_str("GEELARK_REGION", "sgp"),
            android=_str("GEELARK_ANDROID", "Android 15"),
            phone_name_prefix=_str("PHONE_NAME_PREFIX", "farm"),
            target_package=_str("TARGET_PACKAGE", "com.openai.chatgpt"),
            max_concurrent_phones=_int("MAX_CONCURRENT_PHONES", 1),
            account_budget_seconds=_int("ACCOUNT_BUDGET_SECONDS", 1800),
            build_budget_seconds=_int("BUILD_BUDGET_SECONDS", 3600),
            login_budget_seconds=_int("LOGIN_BUDGET_SECONDS", 900),
            install_budget_seconds=_int("INSTALL_BUDGET_SECONDS", 600),
            app_login_budget_seconds=_int("APP_LOGIN_BUDGET_SECONDS", 600),
            api_requests_per_minute=_int("API_REQUESTS_PER_MINUTE", 120),
            state_dir=_path("STATE_DIR", "./state"),
            artifact_dir=_path("ARTIFACT_DIR", "./artifacts"),
            log_level=_str("LOG_LEVEL", "INFO").upper(),
        )

    def require_sheets(self) -> None:
        """Fail early, with a fixable message, before anything is spent."""
        if not self.sheet_id:
            raise ConfigError(
                "GOOGLE_SHEET_ID is not set - the run has no input rows."
            )
        if not self.service_account_json.exists():
            raise ConfigError(
                f"service account file not found: {self.service_account_json}\n"
                "Create a service account, download its JSON key, and share "
                "the spreadsheet with that account's email as an Editor."
            )

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
