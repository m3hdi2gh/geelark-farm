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

## Where code belongs (the builder split, 2026-09-23)

`builder.py` is being taken apart one leaf at a time. The plan is in the
commit history from `6b1cfec` onward. These rules hold now:

| Module | Owns |
|---|---|
| `products.py` | Everything the farm knows about an app: name, service, sign-in flow, package, where a code comes from. A new app is an entry here plus a `flows/<app>_login.py`. Nothing else branches on a product name. |
| `wishes.py` | `Wanted`, and the strict reader of a job's payload. |
| `switches.py` | The process-wide device switches (`HUMAN_CADENCE`, `KERNEL_TOUCH`, `SIGN_IN_VIA`). |
| `pools.PhoneLog` | The Phones tab's Status words and `possible_statuses()`. |
| `pools.ProxyPool` | The proxy words, including `suspect_status` and `held_back_statuses`. |
| `failures.py` | Every verdict word and its sentence. A new reason gets a verdict here. |
| `flows/*` | Screens and sign-ins. They import no builder, pools or store. |
| `builder.py` | The Gmail→app build, finish and job runner, for now. |

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
