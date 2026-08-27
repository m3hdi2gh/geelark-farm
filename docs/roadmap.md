# Roadmap

Where this is going, in two phases. Written down here because the last version
of it lived only in a chat transcript, and the first time anyone needed it
again they had to go looking for it (2026-08-27).

Today the tool is run by hand, by two people on two machines, and every build
is a command somebody types. The destination is one service on a server that
does the same work continuously, with a person adding accounts and confirming
deliveries and nothing else.

## The loop

```
every ~30s:
    sync the sheet          # carries out the State column, frees stale claims
    if incomplete < 10:     # keep the warm stock topped up
        build one
    if the Gpt Info tab has an unused account:
        finish a waiting phone
```

That is the whole of phase 1. It is worth being clear that **none of the three
steps is new work** - each is a command that exists and does exactly this:

| what the loop needs | what already does it |
|---|---|
| a phone built up to just before the ChatGPT login | `build` with an empty `Gpt Info` tab. It stops at `no_usable_gpt`, which is not a failure: the phone is signed into Google with the app installed, one account short |
| completing one the moment an account arrives | `finish`, which reuses the phone's own exit and spends no new phone, Gmail or proxy |
| carrying out a delivery | `sync_sheet(apply_marks=True)`, the default. A phone marked `done` is deleted, its app account is retired as `delivered`, its Gmail as `used`, and a History row records it |

What is missing is the loop around them, and the things a process needs before
it can be left alone.

## Phase 1 — the service runs itself

**Done when:** the server is up, nobody touches the sheet except to add
accounts and mark deliveries, and the sheet stays correct.

This phase has value on its own. Even if the bot never arrives, it takes the
self-healing and the sheet-keeping off both of you.

### Prerequisites

Small, and everything after them is built on sand without them.

- **A circuit breaker.** After N consecutive failed builds, stop and stay
  stopped until a person clears it. Unattended, this is the only thing standing
  between one bad deploy and a burnt Gmail pool.
- **`SIGTERM`.** `docker stop` sends it, and nothing here catches it - every
  shutdown path is wired to `KeyboardInterrupt`, which Python raises only for
  Ctrl+C. The cleanup itself already exists and is right (`shutting_down`,
  cancel the futures, stop every phone this run started); it simply is not
  reachable from the signal a container is stopped with. Without it a
  `docker stop` mid-build leaves phones running and billing.
- **`GEELARK_REVISION`.** `--version` reads the commit out of `.git`, and an
  image built from a copy has none - so the one thing that answers "which code
  is on this server" goes blind exactly where it was added to help. A build
  argument fills it in.

`GEELARK_MACHINE` is already an override for the hostname, which is what keeps
History rows and log filenames meaningful across container restarts. The build
path is already headless: neither `builder` nor `flows` reads stdin, and every
`input()` in the CLI is behind `--yes` or `isatty`.

### The service

- `Dockerfile` and `compose.yml`, with `restart: always`
- secrets **mounted**, never `COPY`ed: `.env` and `secrets/service-account.json`
- volumes for `state/` (the ledger), `logs/` and `artifacts/`. The ledger is
  what stops orphan phones; losing it on a restart means phones nothing is
  accountable for
- `geelark serve`: the loop above

### The one number that bounds it

The plan has 30 profile slots. Ten are permanently warm, which leaves twenty
for phones that are finished and waiting to be delivered, plus whatever is in
flight.

A finished phone holds its slot until a person marks it `done`. So if
deliveries fall behind by about twenty, the loop runs out of slots - and it
should **say so**, loudly, rather than discovering `[44002]` at phone creation
and spinning. Read the free slots before building, not after.

A warm phone is stopped, so it costs a slot and not a billed minute.

## Phase 2 — the emailed code comes from the bot

**Done when:** an app account with no authenticator can be signed in without
anyone sitting at a console.

An account with no password and no authenticator is emailed a six-digit code.
The machinery for that is built and merged: `codes.CodeSource` is the
interface, `codes.Pending` is a source answered by a person - thread-safe,
per-request deadlines, `waiting()` / `answer()` / `give_up()` - and the console
already answers it. `NoSource` is the default, so an unattended run today sets
those accounts aside exactly as it always did.

What phase 2 adds is a second implementation of the same interface, answered
over the network instead of at a terminal, and enough of an HTTP surface for
the bot to reach it: something like `GET /codes/pending` and
`POST /codes/{id}`, behind a shared token.

Nothing about the login flow changes. That is the point of the interface.

## Not in either phase

The earlier version of this roadmap had a phase that inverted the build's
input - credentials arriving in a request rather than being claimed from the
`Gpt Info` tab. That is not what is being built: the account is still added to
the pool, and the service reacts to the sheet. It was the only phase that
changed the architecture, and it is gone.

## Open

**Who marks a phone `done`** is a person, in the State column, in both phases.
The service carries it out at the next sync and never decides it.
