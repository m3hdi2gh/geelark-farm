# Working on geelark-farm

Two agents commit to this repository (Codex and Claude), and a person
decides what gets built. These rules exist so neither agent breaks the
other's work, and so the farm keeps building while the code changes.

## The branch and the server

- One branch: `main`. Never rebase, amend, squash or force-push anything
  that is already on `main` or on the server. Only add commits on top.
- Before you commit on the server, fast-forward first. Before you push from
  another machine, fetch and merge. Never `git reset --hard` over somebody
  else's commits. When a merge conflicts, keep both sides' intent. Do not
  delete code you did not write to make a conflict go away.
- Before a deploy, compare the server's `git rev-parse --short HEAD` with
  yours. If it moved, bring those commits back, merge them, and run the full
  suite before you deploy.

## Proof before every commit

- Run the full suite with pipefail. A piped `tail` must not hide a failure:

  ```bash
  set -o pipefail
  PYTHONDONTWRITEBYTECODE=1 python -m pytest tests -q --no-header -p no:randomly
  ```

  Also run `node --test tests/dash/` when you touch `web/static/dash.js`.
- `ruff check` the files you touched. Add no new findings. Older findings
  exist; do not add to them.
- A new guard test must fail with its fix taken out. Stash the source, run
  the test, see it fail, then restore the source.
- Guard tests are contracts, not obstacles. Examples: the substring pins on
  source, `tests/test_boundaries.py`, `tests/test_web_budget.py`, the verdict
  scans. Satisfy them, or change them in the same commit and say why. Never
  delete or weaken one to get green.

## Never

- Commit `.env`, `secrets/`, or any credential. Put a password or key on a
  command line. Read it from the environment, or ask for it at a hidden
  prompt.
- Touch billing, payments, cards or subscriptions. The farm never pays for
  anything. The customer upgrades after hand-over.
- Deploy what is not committed and tested.

## Deploying

- Build once with `sudo docker compose build -q geelark`. As a plain user,
  compose exits 0 and does nothing.
- The console: `sudo docker compose up -d --force-recreate web`.
- The keeper and the builders go through the gate script. It pauses
  building, waits for running builds to finish, restarts, and unpauses. Do
  not restart them any other way while builds are running.
- After touching `web/read.py` or `web/pages.py`, run the smoke scripts
  inside the web container against the live store.

## Where code belongs (the builder split, 2026-09-23 to 2026-09-24)

`builder.py` was taken apart leaf by leaf (commits `6b1cfec` to
`76f0e05`). It stays one file, about 2,600 lines: the Gmail→app build,
the finish and the job runner. Everything else lives with its owner:

| Module | Owns |
|---|---|
| `products.py` | Everything the farm knows about an app: name, service, sign-in flow, package, where a code comes from, and `signs_in_on_gmail_build` (ChatGPT only). A new app is an entry here plus a `flows/<app>_login.py`. Nothing else branches on a product name. |
| `wishes.py` | `Wanted`, and the strict reader of a job's payload. |
| `switches.py` | The process-wide device switches (`HUMAN_CADENCE`, `KERNEL_TOUCH`, `SIGN_IN_VIA`). |
| `runctx.py` | The run, job and phone stamped on every log line; run ids; the sink build events go to. |
| `cancel.py` | How a job hears it must stop: `Aborted`, the console's Stop (`_stop_asked`, `_stop_honoured`), the wired `cancelled` every wait takes (`_hand_stop_wired`), a State word written mid-run (`_given_up_on`). |
| `exit_health.py` | Whether an exit's host is worth building on: the day's captcha strikes, the week's sign-in gate, a person's Free. |
| `keeper.py` | Reconciliation: tabs vs GeeLark vs the store, State words, what a dead run left behind, stranded credentials, proxy checks. The build path never calls it. |
| `build_result.py` | `Build`, `Capacity`, the reporter, `outcome_of` and the summaries. |
| `rows.py` | Writing a build to the Phones table: status, note, tries, condemning, the row and History at the end. |
| `kit/exits.py` | An exit for a phone: fresh, borrowed, swapped, brought back up, clock set. `ExitLease` is the one owner of a job's exits: the one it owns (`current`), the ones it moved away from (`refused`), the ones it borrowed from another phone (`borrowed`, never spent or freed), one `swaps` count. Every swap is `lease.swap(...)`. |
| `kit/holds.py` | What a job held and what becomes of it at the end: spent, released, set aside, or back as suspect. |
| `kit/install.py` | An app onto a phone: GeeLark's app centre, then Play, then the operator's Play recipe. |
| `kit/phone.py` | The end of any phone job: `PhoneRun` (one `finish` with seconds and API calls), `_ended_by` (the one exception ladder), `_let_the_phone_go` (stop, release, answer the stop request), `_discard`. The module docstring shows the pattern a new kind of job follows. |
| `pools.PhoneLog` | The Phones tab's Status words and `possible_statuses()`. |
| `pools.ProxyPool` | The proxy words, including `suspect_status` and `held_back_statuses`. |
| `failures.py` | Every verdict word and its sentence. A new reason gets a verdict here. |
| `flows/*` | Screens and sign-ins. They import no builder, pools or store. |
| `builder.py` | The Gmail→app build, the finish of a warm phone, and the job runner. |

### The build path

- `build_one` is a skeleton over `_BuildState` and the phases in
  `_BUILD_PHASES`, in order: `_acquire`, `_bring_up`, `_google_phase`,
  `_install_phase`, `_app_phase`. Each phase returns the `Build` that ends
  the job, or `None` to hand on. An exception goes to `_ended_by`; the
  `finally` is `_let_the_build_go(st)`.
- `finish_one` ends through the same `PhoneRun`, `_ended_by` and
  `_let_the_phone_go`.
- State the phases share is a field of `_BuildState`, written as
  `st.<name>`. Never a bare local with a field's name: two tuple targets
  once kept theirs and a build claimed a second exit. A test refuses it.
- Tests that pin the build's source read it through
  `tests/build_sources.build_one_source()` (the skeleton, the phases, the
  ladder and the teardown joined, `st.` taken off), never
  `inspect.getsource(builder.build_one)` alone.
- In the `phones` table, `drop` closes a row (`done_at`), it never deletes
  one. Read open rows with `done_at IS NULL`; a closed row can still say
  `building`.

- Add code to the module that owns it. Do not add a second copy of a word
  or a table that already has an owner.
- Nothing below the builder imports it. That includes pools, products,
  wishes, failures, breaker, phones, shell, flows and the rest.
  `tests/test_boundaries.py` checks this.
- **Patch where a name lives.** When a name moves out of `builder.py`,
  `builder` keeps a re-export so imports keep working. A test must patch the
  owner, not `builder`. `tests/conftest.py` refuses a patch on a name
  builder no longer owns. When you split another module out of the builder,
  add it to `MIGRATED_FROM_BUILDER` in `tests/conftest.py` and to
  `BUILDER_MODULES` in `tests/build_sources.py`. The two lists must match.
- A new kind of phone job (not a build) uses `kit/` and ends through
  `kit/phone.py`; it does not add a third copy of the ending to
  `builder.py`.
- A move is only a move. Never combine moving code with renaming it, fixing
  it or rewording its comments. The dated "why" comments move with the
  lines they explain.

## Writing

- The interface and the code are English.
- Comments explain why, with the date and how it was found. Keep that
  style.
- A commit message says what changed in one sentence, then why in a
  paragraph. Say so in the message when you change a contract: the panel
  API, the schema, a flow's outcome words, or a job's payload.
- Say what you are working on before you start on a shared file
  (`builder.py`, `serve.py`, `web/pages.py`), and keep each change small
  enough that the other agent can merge around it.
