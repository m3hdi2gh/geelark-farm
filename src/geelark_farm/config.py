"""Settings, loaded once from the environment (and a .env file if present).

Everything configurable lives here so no other module reads os.environ. Values
are validated on access rather than at import, so `geelark --help` works
without a populated .env.
"""

from __future__ import annotations

import functools
import os
import subprocess
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


#: How long a claim may go unrefreshed before the sync puts the row back.
#:
#: Five missed heartbeats. A run restamps what it holds every 60 seconds, so a
#: stamp that has not moved in five minutes is not a slow run - it is a run
#: that is gone.
STALE_CLAIM_DEFAULT = 300

REPO_ROOT = _root()
ENV_FILE = REPO_ROOT / ".env"


@functools.lru_cache(maxsize=1)
def revision() -> str:
    """Which commit this is running out of, or "" if that cannot be known.

    Read when it is asked for rather than written down at install time. The
    install is editable and `git pull` moves the code underneath it, so
    anything stamped during setup would name the commit that was current the
    day somebody first configured the machine, not the one that is running.

    `--dirty` is the point of it on a server: a working tree edited in place
    is the difference between "this is commit abc1234" and "this is commit
    abc1234 and somebody has been in it", and only the second explains why the
    machine does not behave like the one next to it.

    Never fatal. A deployment without `.git` - a tarball, a container built
    from a copy - simply gets nothing, and every caller falls back to the
    version string alone.

    Cached: the answer cannot change while the process runs, and this is on
    the path of every single command through the log banner.
    """
    # An image built from a copy of the tree has no `.git`, so the one thing
    # that answers "which code is on this server" would go blind exactly where
    # it was added to help. The build stamps it instead.
    stamped = os.environ.get("GEELARK_REVISION", "").strip()
    if stamped:
        return stamped
    try:
        done = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "describe",
             "--always", "--dirty", "--tags"],
            capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


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


def _log_format() -> str:
    """`LOG_FORMAT`, refused rather than quietly ignored.

    `logs.file_formatter` returns the text formatter for anything that is not
    exactly "json", so `LOG_FORMAT=JSONL` or a stray space gave a prose log
    and said nothing - while everything downstream that counts those lines
    would have been reading sentences believing it had objects. The set of
    valid values existed as `logs.FORMATS` and nothing had ever consulted it
    (2026-08-30).
    """
    from .logs import FORMATS

    value = _str("LOG_FORMAT", "text").strip().lower()
    if value not in FORMATS:
        raise ConfigError(
            f"LOG_FORMAT={value!r} is not one of: {', '.join(FORMATS)}")
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
    #: How many phones one run works on at once. `0` means no ceiling of its
    #: own - under `serve` the pass then takes on whatever the real stock
    #: allows, which is already bounded: finishing by the accounts waiting and
    #: the warm phones there are, building by the shortfall, the free profile
    #: slots and the depth of the Gmail and Proxy tabs. The rate limiter is the
    #: absolute bound on API load either way; more at once does not exceed it,
    #: it only queues behind it - and a phone waiting its turn is a phone
    #: billing by the minute.
    max_concurrent_phones: int
    #: How many phones `serve` keeps built to one step short of ready, so a
    #: delivery is one login away rather than a whole build.
    warm_stock: int
    #: Whether a pass hands its work to a worker pool instead of waiting.
    #:
    #: Off by default, and deliberately. A pass that waits is what every
    #: invariant in this loop was written against - the reap that is safe
    #: because "nothing of ours is running", the breaker that counts passes,
    #: the watchdog that times one. Concurrency is the right shape and it
    #: changes all of those, so it is a thing somebody turns on and can turn
    #: off again in one line (2026-08-29).
    serve_concurrent: bool
    #: `text` or `json`. The file only; the console is always prose.
    log_format: str
    #: How long `serve` waits between passes.
    serve_interval_seconds: int
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
    #: That condition is now met (2026-08-28): the server is the only machine
    #: that builds against this sheet, and it beats. So the default is five
    #: minutes - five missed beats - rather than an hour.
    #:
    #: What an hour cost, the day it was shortened: a run was interrupted
    #: holding an app account, its phone was discarded, and the account sat
    #: `in_use` and unusable while the sheet waited out a window sized for a
    #: mechanism that no longer applies.
    #:
    #: It goes back up the moment something that does not beat touches this
    #: sheet again - an older checkout on somebody's laptop is enough. A
    #: window shorter than the holder's silence hands a live run's row to
    #: somebody else mid-build.
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

    #: Whether the Postgres store is wired in at all. Off, the `store`
    #: package is never imported - the flag is checked before the import, so
    #: a broken store module cannot take the loop down on a box where the
    #: store was never asked for. This is the trunk-based rule for every
    #: stage of the sheet retirement: merged inert, enabled deliberately.
    store_enabled: bool = False
    # The managed cluster. Split fields rather than one URL, because every
    # other credential here is a field and a URL invites the password into
    # a process list. `store_password` is deliberately excluded from repr.
    # All defaulted, like `store_enabled`: a Settings built by hand in a test
    # gets a store that is off, which is exactly what the flag rule promises.
    store_host: str = ""
    store_port: int = 5432
    store_db: str = "gfarm"
    store_user: str = "gfarm"
    store_password: str = field(repr=False, default="")

    #: The read-only web UI (stage 3). Same flag rule as the store: off by
    #: default, and `serve` imports the web package only inside the check.
    web_enabled: bool = False
    #: Loopback-published; the only way in from outside the box is an SSH
    #: tunnel until the domain lands at stage 7.
    web_port: int = 8787
    #: What the listener binds. Loopback by default - but inside the
    #: container it must be 0.0.0.0, because Docker's port proxy connects to
    #: the container's own address and a socket bound to the container's
    #: loopback answers a published port with nothing at all (found the hard
    #: way on first deploy, 2026-08-31). The host side of the publish is
    #: what keeps it private: compose pins it to the host's 127.0.0.1.
    web_bind: str = "127.0.0.1"
    #: Stage 5: the mutation verbs. Off means every action POST answers 403
    #: and the serve drain never runs - the read-only web of stage 3,
    #: exactly.
    web_mutations: bool = False
    #: C1: the Users page - an admin making people and ticking what each
    #: may do. Off means the page answers 404 and the user routes do not
    #: exist; the six permissions are still read by `users.may`, they just
    #: cannot be changed from the web.
    web_user_admin: bool = False
    #: C2: the three pools live in Postgres. Off, every pool is a sheet tab
    #: exactly as before. On, a Book opens its Gmails / Proxy / Gpt Info
    #: from the resources table, claims are one atomic statement, and the
    #: sheet's three tabs become an input funnel the Importer drains each
    #: pass. Needs the store settings; refused at Book.open otherwise.
    pools_in_pg: bool = False
    #: C6: app accounts are logged in on command, not on arrival. Off, a
    #: pass finishes a warm phone for every account waiting, as it always
    #: has. On, `decide` is told nobody is waiting - the Keeper only keeps
    #: the stock warm - and a person picks accounts on the dashboard and
    #: presses "Log in selected"; that command is what starts the finishes.
    manual_login: bool = False


    @classmethod
    def load(cls) -> Settings:
        """Read settings from the environment, requiring only what every
        command needs. Sheets settings are validated by require_sheets()."""
        load_env()
        return cls(
            app_id=_str("GEELARK_APP_ID", required=True),
            api_key=_str("GEELARK_API_KEY", required=True),
            sheet_id=_str("GOOGLE_SHEET_ID"),
            service_account_json=_path(
                "GOOGLE_SERVICE_ACCOUNT_JSON", "./secrets/service-account.json"
            ),
            region=_str("GEELARK_REGION", "sgp"),
            android=_str("GEELARK_ANDROID", "Android 15"),
            phone_name_prefix=_str("PHONE_NAME_PREFIX", "farm"),
            target_package=_str("TARGET_PACKAGE", "com.openai.chatgpt"),
            max_concurrent_phones=_int("MAX_CONCURRENT_PHONES", 1,
                                       minimum=0),
            warm_stock=_int("WARM_STOCK", 10),
            serve_concurrent=_str("SERVE_CONCURRENT", "0").strip()
                              in ("1", "true", "yes", "on"),
            log_format=_log_format(),
            serve_interval_seconds=_int("SERVE_INTERVAL_SECONDS", 30),
            build_budget_seconds=_int("BUILD_BUDGET_SECONDS", 3600),
            stale_claim_seconds=_int("STALE_CLAIM_SECONDS",
                                     STALE_CLAIM_DEFAULT),
            login_budget_seconds=_int("LOGIN_BUDGET_SECONDS", 900),
            install_budget_seconds=_int("INSTALL_BUDGET_SECONDS", 600),
            app_login_budget_seconds=_int("APP_LOGIN_BUDGET_SECONDS", 600),
            api_requests_per_minute=_int("API_REQUESTS_PER_MINUTE", 120),
            state_dir=_path("STATE_DIR", "./state"),
            artifact_dir=_path("ARTIFACT_DIR", "./artifacts"),
            log_dir=_path("LOG_DIR", "./logs"),
            log_level=_str("LOG_LEVEL", "INFO").upper(),
            store_enabled=_str("STORE_ENABLED", "0").strip()
                          in ("1", "true", "yes", "on"),
            web_enabled=_str("WEB_ENABLED", "0").strip()
                        in ("1", "true", "yes", "on"),
            web_port=_int("WEB_PORT", 8787),
            web_bind=_str("WEB_BIND", "127.0.0.1"),
            web_mutations=_str("WEB_MUTATIONS", "0").strip()
                          in ("1", "true", "yes", "on"),
            web_user_admin=_str("WEB_USER_ADMIN", "0").strip()
                           in ("1", "true", "yes", "on"),
            pools_in_pg=_str("POOLS_IN_PG", "0").strip()
                        in ("1", "true", "yes", "on"),
            manual_login=_str("MANUAL_LOGIN", "0").strip()
                         in ("1", "true", "yes", "on"),
            store_host=_str("STORE_HOST"),
            store_port=_int("STORE_PORT", 5432),
            store_db=_str("STORE_DB", "gfarm"),
            store_user=_str("STORE_USER", "gfarm"),
            store_password=_str("STORE_PASSWORD"),
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

    def require_store(self) -> None:
        """Fail early, with a fixable message, before anything is spent.

        Only called on the paths that are about to open a connection, so a
        box that never enables the store never needs these set - the same
        contract `require_sheets` gives the sheet settings.
        """
        if not self.store_host:
            raise ConfigError(
                "STORE_ENABLED is on but STORE_HOST is not set - the store "
                "has no cluster to talk to.")
        if not self.store_password:
            raise ConfigError(
                "STORE_ENABLED is on but STORE_PASSWORD is not set. It "
                "belongs in .env beside the other credentials, never in "
                "the repo.")

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
