# Roadmap

Each phase ends in something runnable. "Done when" is the acceptance test.

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Repo skeleton, config, CLI surface, ported API notes | **done** |
| 1 | Signed API client: rate limiter, retries, `ApiError` | next |
| 2 | Device layer: shell, screen capture/parse/tap, fixtures | |
| 3 | Phone lifecycle + ledger + reaper | |
| 4 | Google login screen router | |
| 5 | Play Store install flow | |
| 6 | Google Sheets input and status write-back | |
| 7 | Orchestrator: per-row pipeline, budgets, summary | |
| 8 | Hardening, runbook, release | |

## Phase 0 — skeleton and documentation

Repo, `.gitignore` covering every secret, `.env.example`, packaging, module
skeleton with each module's responsibility written down, and the API field
notes carried over from the prototype.

**Done when** `pip install -e .` succeeds and `geelark --help` works.

## Phase 1 — API core

Signing, a process-wide rate limiter well under 200 req/min, backoff on
transport errors, `ApiError` on non-zero response codes.

**Done when** `geelark ping` authenticates and lists the account's phones, and
the signing unit test matches the worked example from the docs.

## Phase 2 — device layer

`shell.run`, plus screen capture, parse, find and tap. Text entry that survives
passwords containing spaces and shell metacharacters — `input text` mangles
them, so this needs escaping or an IME, and it is a blocker for phase 4 rather
than a detail.

`geelark dump --save` writes real screens into `tests/fixtures/`, which is what
makes phase 4 testable without a phone.

**Done when** dump and tap work against a live phone and the parser tests pass
on fixtures.

## Phase 3 — phone lifecycle

Create bound to a proxy, boot with a wait, stop, delete, list. Ledger written
at creation time. `geelark reap` stops anything running that the ledger does
not account for.

**Done when** no error path can leave a phone running.

## Phase 4 — Google login screen router

The core of the project. Screen registry, handlers, named terminal states,
budget, and artifact capture on anything unrecognised.

Method: run against real accounts; every unknown screen becomes a fixture, a
registry entry, and a row in `google-login-screens.md`.

**Done when** several consecutive accounts sign in, and every failure carries a
named reason instead of a timeout.

## Phase 5 — Play install

Port the known-good sequence from the prototype and capture its fixtures.

**Done when** an install is verified by `pm list packages` on a fresh phone.

## Phase 6 — Sheets

Service-account access, row validation before any spend, status write-back,
`--dry-run` that reports the plan for free.

**Done when** status round-trips and a second run does nothing.

## Phase 7 — Orchestrator

The per-row pipeline, budgets, per-account logs and artifacts, end-of-run
summary table. Sequential first; `MAX_CONCURRENT_PHONES` raises it later.

**Done when** a three-row sheet yields three ready phones unattended, and
re-running is a no-op.

## Phase 8 — hardening

Retry and quarantine policy, `runbook.md` mapping every observed failure to its
cause and fix, README with a real walkthrough, tagged release.

Optional beyond that: webhooks (`/callback/set`, event 6) instead of polling,
and a `uiautomator2` backend over real ADB (`/adb/getData`) if direct tapping
proves fragile at scale.
