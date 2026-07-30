# Architecture

## The shape of the problem

One spreadsheet row describes one account: a proxy, a Gmail address, a
password, a TOTP secret. One run turns each pending row into a stopped cloud
phone that is signed into that account with the target app installed.

Two properties dominate every design choice:

1. **The device is the only source of truth.** GeeLark's RPA tasks report
   success without acting, so no step may conclude from a task status.
2. **Running phones cost money by the minute.** Budgets are spend caps, and a
   phone left running is a bug, not an inconvenience.

## Layering

Dependencies point downward only.

```
cli.py, orchestrator.py        which rows, in what order, with what budget
        │
flows/  google_login.py        multi-screen procedures
        play_install.py
        │
screen.py, shell.py            see the device / act on the device
        │
api.py, config.py              signed transport, settings
```

`sheets.py` and `phones.py` sit beside the device layer: both are state stores
that the orchestrator reads and writes, but neither knows about screens.

Why this split matters: the layer that knows *what a Google password screen
looks like* must not also know *how to sign an HTTP request*. Keeping them
apart is what makes the login flow testable against saved screen captures with
no phone and no network.

## The screen router

A linear script ("type the email, then the password, then the code") breaks on
first contact with Google, because the order of screens is not fixed and extra
interstitials appear without warning.

Flows are therefore loops over observed state:

```python
while not outcome:
    rows = screen.capture(phone)
    handler = registry.match(rows)     # first matching known screen
    outcome = handler.act(device, account)
```

A registry entry is one screen: a name, a match predicate over the parsed
elements, an action, and a kind.

| Kind | Meaning |
|---|---|
| `continue` | handled; loop again |
| `success` | terminal, confirmed against the device |
| `fatal` | terminal and named, e.g. `captcha_shown`, `account_disabled` |

Adding support for a screen Google has just started showing means adding one
entry. The loop never changes. Anything unmatched is archived (XML +
screenshot) and reported as `unknown_screen`, which is a task, not a mystery.

## Element matching

Elements are located by observation, not assumption:

- capture the live hierarchy with `uiautomator dump`, parse it, and match on
  **both** `text` and `content-desc` — GeeLark's own flows fail precisely
  because they match only `content-desc`;
- tap the centre of an element's `bounds`, and do not require
  `clickable=true`: on the Play Store page the Install label is a
  non-clickable `TextView`, and tapping its centre works;
- an empty input field has neither text nor content-desc, so it has to be
  found in the raw XML by class (`EditText`) rather than by label.

## State and resumability

The spreadsheet is both the input and the state store. `status` drives resume:

```
pending -> running -> done
                   -> failed:<reason>
```

A run skips `done` rows, so re-running is safe and idempotent — the property
that makes this tool usable as a daily habit rather than a careful operation.

A local ledger under `state/` records each created phone immediately, before
anything else can fail, so a crash between "phone created" and "sheet updated"
is recoverable and `reap` can find orphans.

## Concurrency

- Across phones only: one RPA task per phone is a hard API constraint.
- The API rate limit (200 req/min, two-hour ban when exceeded) is a
  process-wide budget, so the limiter lives in `api.py` and every worker draws
  from it.
- Concurrency starts at 1. The orchestrator is written as a bounded worker pool
  from the start so raising `MAX_CONCURRENT_PHONES` is a config change, but the
  first correct version is the sequential one.

## What this design cannot fix

A dirty proxy IP. Google responds to low-reputation addresses with CAPTCHAs and
phone-number demands, which no amount of UI automation resolves. The flow will
name the failure accurately; the fix is a better IP.
