# Roadmap

Each phase ends in something runnable. "Done when" is the acceptance test.

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Repo skeleton, config, CLI surface, ported API notes | **done** |
| 1 | Signed API client: rate limiter, retries, `ApiError` | **done** |
| 2 | Device layer: shell, screen capture/parse/tap, fixtures | **done** |
| 3 | Phone creation + ledger + reaper | **done** |
| 4 | Google login screen router | **done** |
| 5 | Play Store install flow | **done** |
| 6 | Google Sheets input and status write-back | next |
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

**Done when** `geelark ping` authenticates and lists the account's phones.

Delivered: `api.Client` (signing, `data()` shortcut), `RateLimiter` (sliding
window, thread-safe, blocks rather than rejecting — waiting a second beats a
two-hour ban), backoff with jitter, and a retry policy that only repeats
read-only endpoints by default, since a timed-out write may already have been
applied. `ApiError` names known failure codes (20002 concurrent task, 20008
non-English UI) and always reports the `traceId` for support.

## Phase 2 — device layer

`shell.run`, plus screen capture, parse, find and tap. Text entry that survives
passwords containing spaces and shell metacharacters — `input text` mangles
them, so this needs escaping or an IME, and it is a blocker for phase 4 rather
than a detail.

`geelark dump --save` writes real screens into `tests/fixtures/`, which is what
makes phase 4 testable without a phone.

**Done when** dump and tap work against a live phone and the parser tests pass
on fixtures.

Delivered: `shell.run/read` plus the verification primitives
(`device_accounts`, `package_installed`), `screen.capture/parse/find/tap`, and
the CLI diagnostics `dump --save`, `tap`, `shell`, `type`, `screenshot`,
`phones`, `stop`.

Phone status/start/stop moved here from phase 3, because every device command
needs a running phone and the layer is unusable without them. Creation, the
ledger and the reaper remain in phase 3.

Two findings worth keeping:

- **Text entry is solved.** `input text` has two independent hazards: the shell
  interprets `$ ` \ " '` before `input` sees them (fixed by `shlex.quote`), and
  `input text` itself splits on spaces and decodes `%s` as a space (fixed by
  encoding spaces, and refusing a literal `%` outright). Verified on a device:
  nine password-shaped strings covering every shell metacharacter typed
  **exactly**, and both unrepresentable cases (`%`, non-ASCII) raise
  `TypingError` rather than typing something subtly different. That distinction
  matters because a mistyped password is indistinguishable from a wrong one and
  costs an attempt against the account's reputation to discover.
- **Editable fields are not all `EditText`.** The Settings search box is an
  `AutoCompleteTextView`; matching only `EditText` would have made a login code
  field invisible to the router. `EDITABLE_CLASSES` matches substrings now, and
  `tests/test_screen.py` pins the behaviour to a captured hierarchy.

## Phase 3 — phone creation, ledger, reaper

Create bound to a proxy, delete, and a ledger written at creation time so a
crash between "phone created" and "row updated" is recoverable.
`geelark reap` stops anything running that the ledger does not account for.

**Done when** no error path can leave a phone running unaccounted for.

Delivered: `proxy.parse/check`, `ledger.Ledger`, `phones.create/delete/reap`,
and the CLI commands `create`, `delete`, `start`, `reap --dry-run`, `proxy`,
plus `phones --ledger`.

The reaper answers one question — *does anything legitimately need this running
phone right now?* — and stops it in the three cases where the answer is no:
it is absent from the ledger, it was already released by its run, or its claim
is older than two hours, which means the process that owned it is gone. A fresh
claim is left alone. `--dry-run` reports without acting.

Verified end to end on a real phone, then deleted: create recorded serial 435 in
the ledger with the proxy endpoint (never its password) → `start` began billing
→ `reap --dry-run` reported "created but never claimed" → `reap` stopped it and
wrote the reason into the ledger → `delete` removed it from both the account and
the ledger. That run also confirmed `/v1/phone/delete`, the one endpoint here
that had never been exercised.

One correction came out of this phase. `/proxy/check` returning an empty
`country` was inherited from the prototype's notes as a warning sign of a dirty
IP. It is not: four proxies from two vendors all returned no country from
GeeLark while resolving to real US ISPs with `hosting: false` in a public
database. `proxy.check` no longer treats it as a signal, and reports the exit
address instead — with a backconnect gateway that is the address Google judges.

## Phase 4 — Google login screen router

The core of the project. Screen registry, handlers, named terminal states,
budget, and artifact capture on anything unrecognised.

Method: run against real accounts; every unknown screen becomes a fixture, a
registry entry, and a row in `google-login-screens.md`.

**Done when** several consecutive accounts sign in, and every failure carries a
named reason instead of a timeout.

Delivered: `accounts.Account` (validated offline before a phone is created for
it), and `flows/google_login.py` - an ordered screen registry plus a loop that
reads, matches, acts and repeats. `geelark login --row N` creates a phone on the
row's proxy, signs in, and stops it.

A real account signed in on the third run, unattended, through six screens:
leftover setup page, email, password, authenticator choice, TOTP code, consent.
Confirmed by `dumpsys account`, not by anything the screen said.

Three findings, in order of what they cost:

1. **The authenticator must outrank "Try another way".** Google shows both on
   one page. Pressing the latter while the former is visible gets the sign-in
   refused outright with "You didn't provide enough info" - a message that reads
   exactly like an account-provenance problem, and was diagnosed as one for two
   runs. It was self-inflicted; reordering two registry entries fixed it.
2. **Typographic punctuation broke every selector containing an apostrophe.**
   Google writes "Couldn't" with U+2019, so ASCII selectors silently never
   matched and a named failure was reported as `unknown_screen`.
   `screen.normalize` folds punctuation at parse time.
3. **Archiving one screenshot is not enough.** The end-state capture supported
   the wrong diagnosis; the full chain of screens exposed the real cause
   immediately. Every screen is now archived on first visit.

Also worth knowing: the live-view URL from `/phone/start` expires within
seconds, so it has to be handed over the moment it is issued - hence the
`on_url` callback in `ensure_running`.

## Phase 5 — Play install

Port the known-good sequence from the prototype and capture its fixtures.

**Done when** an install is verified by `pm list packages` on a fresh phone.

Delivered: `flows/play_install.py` and `geelark install [--watch]`. Verified on a
freshly created, freshly signed-in phone: deep link to the package page, tap
Install, clear the Play Protect prompt mid-download, and confirm with
`pm list packages`.

Three things this run settled:

- **The Install label moves between attributes.** Here it was a `content-desc`
  on a non-clickable `View`; in the prototype's run it was `text`. Matching both,
  and not requiring `clickable`, is what makes the tap reliable — and matching
  only one is exactly how GeeLark's task fails. Pinned by
  `test_install_is_found_even_though_it_is_not_clickable`.
- **The "Complete account setup" chain is account-specific, not universal.** It
  never appeared for these accounts; the only interstitial was Play Protect's
  "Turn on scanning", cleared with Dismiss. The chain handling stays, since the
  prototype hit it, but it is not on the normal path.
- **`install` refuses a phone another run holds.** The ledger already tracked
  claims; nothing consulted them. Running `install` during a login would have
  had two flows calling `uiautomator dump` on one device, which cannot run twice
  and corrupts both reads — the victim being the long login, failing for a reason
  unrelated to Google. It now exits with an explanation instead, and a claim
  older than two hours is ignored so a dead process cannot lock a phone forever.

Also added: `phones.prune_ledger`, because phones get deleted from the GeeLark
panel directly and the ledger otherwise accumulates entries for devices that no
longer exist.

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
