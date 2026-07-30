# geelark-farm

Provision GeeLark cloud phones from a spreadsheet.

One row describes one account — a proxy, a Gmail address, a password, a TOTP
secret. One run turns every pending row into a **stopped** cloud phone that is
signed into that account with the target app installed, so the manual work that
follows can start from a ready device.

```
sheet row ──► create phone (behind the row's proxy) ──► boot
          ──► sign into Google ──► verify on device
          ──► install the app  ──► verify on device
          ──► stop the phone   ──► write status back to the sheet
```

Re-running is safe: rows already marked `done` are skipped.

## Status

Phase 5 of 8 — **the whole device pipeline works.** Given an account, the tool
creates a phone on its proxy, signs into Google through six screens, installs the
target app from the Play Store, verifies both against the device, and stops the
phone. What remains is reading the accounts from a spreadsheet and running them
as a batch. See [docs/roadmap.md](docs/roadmap.md).

## Why this exists

GeeLark ships prebuilt RPA tasks for Google login and Play Store installs.
Both were tried first, and both **report success while having done nothing** —
one left the device stranded on a verification screen, the other never
installed the app. Their selectors cannot be corrected from outside.

So this project drives the device itself: read the real view hierarchy, act on
what is actually on screen, and confirm every result against the device
(`dumpsys account`, `pm list packages`) rather than a task status. The
reasoning is in [docs/architecture.md](docs/architecture.md); the API's sharp
edges are catalogued in [docs/geelark-api.md](docs/geelark-api.md).

## Install

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; source .venv/bin/activate elsewhere
pip install -e ".[dev]"
cp .env.example .env            # then fill it in
```

## Configure

`.env` holds the GeeLark API credentials, the spreadsheet id, the path to a
Google service-account key, and the budgets. Every field is documented in
[.env.example](.env.example).

The spreadsheet needs one row per account and is also where results are
written:

| proxy | email | password | totp_secret | status | phone_id | serial | note | updated_at |
|---|---|---|---|---|---|---|---|---|

Share the sheet with the service account's email address as an Editor.

`status` is the resume mechanism: `pending` → `running` → `done` or
`failed:<reason>`.

## Use

```bash
geelark --help                  # every command, and its phase
geelark ping                    # check credentials, list phones
geelark rows                    # validate the sheet without spending anything
geelark run --dry-run           # show the plan
geelark run                     # process pending rows
geelark reap                    # stop anything left running
```

Phone management:

```bash
geelark proxy "socks5://user:pass@host:port"    # test a proxy, spend nothing
geelark create --proxy "..." --label "row 4"    # create bound to that proxy
geelark phones --ledger                         # what exists, and who owns it
geelark start / geelark stop --all
geelark delete --phone ID
geelark reap --dry-run                          # what would be stopped, and why
```

One account, end to end. Each step creates or reuses a phone and stops it
afterwards; `--watch` prints a live-view link and waits so you can follow along:

```bash
geelark login --row 1 --keep --watch    # create a phone, sign in, leave it up
geelark install --watch                 # install the target app on it
geelark stop --all
```

Device diagnostics, for when a flow does something unexpected. Each resolves
the phone from `--phone`, else the only running one, and starts it if needed:

```bash
geelark dump                    # every element on screen, with tap targets
geelark dump --save f.xml       # ...and keep it as a test fixture
geelark tap Install             # tap by label (matches text or content-desc)
geelark type "secret"           # type into the focused field
geelark shell "pm list packages -3"
geelark screenshot
geelark stop --all              # end all billing
```

## Cost

**Phones bill per running minute.** Every budget in `.env` is therefore a spend
cap, every code path that starts a phone stops it, and `geelark reap` is the
backstop when something goes wrong. Check it after any interrupted run.

## Caveats

- Automating Google account sign-in is contrary to Google's terms of service,
  and accounts may be locked. This tooling reports such outcomes accurately; it
  cannot prevent them.
- Proxy IP reputation sets the ceiling on success. A datacenter or freshly
  allocated address draws CAPTCHAs and phone-verification demands that no UI
  automation resolves.
- Secrets never belong in the repository. `.env`, the service-account JSON and
  the `state/` directory are gitignored; keep it that way.
