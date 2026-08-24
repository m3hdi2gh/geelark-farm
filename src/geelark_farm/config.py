"""Settings, loaded once from the environment (and a .env file if present).

Everything configurable lives here so no other module reads os.environ. Values
are validated on access rather than at import, so `geelark --help` works
without a populated .env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _root() -> Path:
    """Where this tool's own files live: `.env`, `state/`, `logs/`, `secrets/`.

    In a source checkout that is the repo, two levels above the package
    (`<root>/src/geelark_farm/`). That was taken as a fact, and it holds only
    while the package is installed with `pip install -e .`.

    A plain `pip install .` - which is what an image does - puts the package in
    `site-packages/geelark_farm/`, so the same arithmetic answers
    `/usr/local/lib/python3.12`. Every path here would then be resolved inside
    the installed library: `.env` looked for where it cannot be, and `state/`,
    `logs/` and `artifacts/` written into the image itself. The ledger would go
    with them, and a ledger that dies with the container is a ledger that
    cannot account for the phones a restart interrupted.

    So the arithmetic is checked rather than trusted. `pyproject.toml` next to
    the candidate means a checkout; anything else means the package is
    installed and the working directory is what the paths are relative to.
    `GEELARK_ROOT` settles it outright, which is what a container should set.
    """
    explicit = os.environ.get("GEELARK_ROOT")
    if explicit:
        return Path(explicit).resolve()
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path.cwd().resolve()


REPO_ROOT = _root()
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


def _int(key: str, default: int, *, minimum: int = 1) -> int:
    """An integer setting, refused here if it cannot mean anything.

    Parsing was the only check, so a value that was a number but not a
    quantity was carried into the run and did something different in each
    place it landed:

    - `API_REQUESTS_PER_MINUTE=0` built a Settings happily and then raised a
      bare ValueError out of RateLimiter - the wrong layer, with a message
      that does not mention the file to go and fix.
    - `MAX_CONCURRENT_PHONES=-3` was silently rounded up to 1 by the builder,
      so whoever set it never learned it had been ignored.
    - `BUILD_BUDGET_SECONDS=-1` put every deadline in the past. Every build
      ends at once on budget_exhausted, and the same number is the staleness
      window for a claim - so the sync would call every claim abandoned and
      start freeing rows a live run was holding.

    `minimum` defaults to 1 because every setting here is a count or a
    duration and none of them means anything at zero. A future one that does
    can say `minimum=0` and be read as deliberate.
    """
    raw = os.environ.get(key)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(
            f"{key} must be at least {minimum}, got {value}. "
            f"A smaller one does not mean 'no limit' - it means the run "
            f"behaves in a way nothing here is written for."
        )
    return value


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

    # Google Sheets input: the workbook holding the resource tabs.
    sheet_id: str
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
    # The outer bound on what one phone may spend, and so on what it can cost.
    # It wins: each step below gets whichever is smaller, its own budget or the
    # time left. A build may work through several Gmails and several app
    # accounts on one phone, which is the point of it, so this has to cover
    # more than one pass of the steps.
    build_budget_seconds: int
    #: How long a claim on a Gmail, an exit or an app account may go without
    #: being refreshed before the sync puts it back.
    #:
    #: It used to BE `build_budget_seconds`, because without a heartbeat the
    #: only safe answer was "longer than any run could legitimately hold one".
    #: A live run now restamps what it holds every `HEARTBEAT_SECONDS`, so a
    #: stamp that has stopped moving means the holder is gone whatever the
    #: budget is - and the wait can be minutes instead of an hour.
    #:
    #: The default is still the build budget, deliberately. Shortening it is
    #: only safe once EVERY machine that touches this sheet runs a version
    #: that beats; an older one holds a row without refreshing it, and a
    #: shorter window would hand that row to somebody else mid-build.
    stale_claim_seconds: int
    login_budget_seconds: int
    install_budget_seconds: int
    app_login_budget_seconds: int

    # GeeLark bans a key for 2 hours past 200 req/min; stay well under.
    api_requests_per_minute: int

    # Local output.
    state_dir: Path
    artifact_dir: Path
    log_dir: Path
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
            service_account_json=_path(
                "GOOGLE_SERVICE_ACCOUNT_JSON", "./secrets/service-account.json"
            ),
            region=_str("GEELARK_REGION", "sgp"),
            android=_str("GEELARK_ANDROID", "Android 15"),
            phone_name_prefix=_str("PHONE_NAME_PREFIX", "farm"),
            target_package=_str("TARGET_PACKAGE", "com.openai.chatgpt"),
            max_concurrent_phones=_int("MAX_CONCURRENT_PHONES", 1),
            build_budget_seconds=(budget := _int("BUILD_BUDGET_SECONDS", 3600)),
            stale_claim_seconds=_int("STALE_CLAIM_SECONDS", budget),
            login_budget_seconds=_int("LOGIN_BUDGET_SECONDS", 900),
            install_budget_seconds=_int("INSTALL_BUDGET_SECONDS", 600),
            app_login_budget_seconds=_int("APP_LOGIN_BUDGET_SECONDS", 600),
            api_requests_per_minute=_int("API_REQUESTS_PER_MINUTE", 120),
            state_dir=_path("STATE_DIR", "./state"),
            artifact_dir=_path("ARTIFACT_DIR", "./artifacts"),
            log_dir=_path("LOG_DIR", "./logs"),
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
        """Make every directory this object names.

        `log_dir` was not one of them, and only `_configure_logging` making
        its own kept that from showing - so a caller who asked for the
        directories got two of the three and no way to know which.
        """
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def machine() -> str:
    """This device's name, the way logs and History rows say it.

    Machines share one spreadsheet and nothing else, so every durable record
    has to say which of them wrote it - "the Mac hit this at 04:20" is the
    whole reason the log file and the History tab exist. Sanitised because
    hostnames arrive with dots and spaces, and this ends up in filenames.

    `GEELARK_MACHINE` overrides the hostname, and a container is why. Docker
    gives one a fresh random hex id for a hostname on every `run`, so the
    identity that ties a History row to the thing that wrote it would change
    each restart: the tab would fill with names that mean nothing and the log
    would start a new file under a new name every time the service came back.

    Nothing else needs it. On a laptop the hostname is already the answer.
    """
    import platform
    import re

    name = _str("GEELARK_MACHINE") or platform.node() or "unknown"
    return re.sub(r"[^A-Za-z0-9-]+", "-", name).strip("-").lower() or "unknown"
