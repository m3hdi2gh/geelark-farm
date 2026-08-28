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

### Prerequisites — **done, 2026-08-27**

- **A circuit breaker** — `breaker.py`. Five failed builds in a row and it
  stops, until a person clears it. Consecutive rather than a rate, because the
  question is whether it has stopped working and one success answers that.
  Written to `state/`, because `restart: always` means the process that trips
  it is not the one that has to still know. It counts three outcomes, not two:
  `no_usable_gpt` is the warm stock working and clears the count, an empty
  pool is nothing happening and leaves it alone, and everything else counts -
  including `network_unreachable`, whose blame is `nobody` but which means
  this machine cannot work right now.
- **`SIGTERM`** — `cli.stop_on_sigterm`, installed in `main`. It raises the
  `KeyboardInterrupt` the existing cleanup already listens for rather than
  inventing a second shutdown path.
- **`GEELARK_REVISION`** — the build stamps it; `--version` still prefers the
  checkout when there is one.

### The loop — **done, 2026-08-27**

`geelark serve`, in `serve.py`. `--once` does a single pass, which is what a
cron entry or a test wants.

The judgement is `decide`, a pure function of five numbers - whether the
breaker is open, how many phones are warm, how many should be, how many slots
are free, and how many accounts are waiting. It is kept apart from everything
that talks to GeeLark on purpose: what the service should do next is the part
worth being sure about, and this way it can be argued with in a test that has
no network, no sheet and no clock.

Three things it is careful about:

- **A tripped breaker stops building, not everything.** Finishing spends
  nothing new, and a customer waiting on an account is the one thing that
  should still happen while somebody works out why the last five builds
  failed.
- **Slots are read before building.** A run of undelivered phones is what runs
  the plan out of room, and the fix is a person marking rows done - so it says
  that in words rather than arriving as `[44002]`.
- **Finishing comes before topping up.** Both want the same pass; only one has
  somebody waiting at the end of it.

One phone built and one finished per pass. That is the pacing of the whole
service, and doubling it would double the rate the pools drain at without
anything saying so.

### What a pass costs — **measured, 2026-08-27**

|  | calls | time |
|---|---|---|
| before | 43 | 36.7s |
| after | **9** | **13.1s** |

34 of those 43 were `/v1/proxy/check` - one live connection per exit, every
thirty seconds, to answer a question whose answer changes on the scale of
days. The exits are now re-tested hourly, and on the first pass after a
restart, which is the one time they may have changed while nothing watched.

That takes the hourly cost from about 5,160 GeeLark calls to about 574,
against a limit of 24,000 an hour and 200 a minute.

The nine that remain are seven `/v1/phone/list`, one `/v1/proxy/list` and one
`/v1/pay/plan/info`.

**Deferred to after the `builder.py` split (decided 2026-08-27).** The seven
are ten separate call sites inside `sync_sheet`, and reading the panel once a
pass and handing it down means touching all ten - much cheaper once that
module is several modules. **Raise it again at the split.** The acceptance
number for a pass was under five calls and this is the whole of what stands
between us and it, so it is unfinished rather than rejected. It is not urgent:
at 2.4% of the rate limit it is tidiness, not a problem.

The interval is a gap between passes, not a period - `sleep` comes after the
work - so a pass that runs long delays the next one rather than stacking.

### The log a machine can read — **done, 2026-08-27**

`LOG_FORMAT=json` writes the log **file** as one JSON object per line. The
console stays prose, because those two handlers have always had different
jobs: the console is for watching a run, the file is for finding out what
happened afterwards - and increasingly for something else to read and decide
whether to raise an alarm. `grep -c` over prose breaks the day a sentence is
rephrased.

Every line carries which machine and which commit wrote it. Two fields rather
than a header, because a log gets tailed, rotated and concatenated with
another machine's, and each of those loses a header.

`serve`'s per-pass line now carries its numbers as fields as well as inside
the sentence - `warm`, `target`, `free_slots`, `accounts_waiting`, `will` -
which is what makes stage 4's "the stock has been short for an hour" an alarm
somebody can actually write.

The default stays text: that file is read by hand on the laptop today.

`GEELARK_MACHINE` is already an override for the hostname, which is what keeps
History rows and log filenames meaningful across container restarts. The build
path is already headless: neither `builder` nor `flows` reads stdin, and every
`input()` in the CLI is behind `--yes` or `isatty`.

### The service

Decided 2026-08-27: **Docker**, and the clock stays **UTC**. The case against
Docker on this box - 1 vCPU, 2 GB, one service, one machine - was heard and
overruled, so the image is built to suit it: dependencies in their own layer
so a code change does not rebuild them, and the interpreter pinned to a
version CI already tests rather than to the 3.12 the host happens to carry.

- `Dockerfile` and `compose.yml`, with `restart: always`
- secrets **mounted**, never `COPY`ed: `.env` and `secrets/service-account.json`
- volumes for `state/` (the ledger), `logs/` and `artifacts/`. The ledger is
  what stops orphan phones; losing it on a restart means phones nothing is
  accountable for
- `geelark serve`: the loop above

### Made fit to run unattended — **2026-08-28**

Found by auditing the deployed service rather than by it failing, which is the
only reason most of these were still cheap.

- **The warm stock could never grow past one.** `serve` called `builder.run`
  without `finish_first=False`, so a pass that had decided to *build* took back
  the phones it had just counted as warm and finished one instead — which then
  found no account, because a build pass only happens when none is waiting.
  Dormant at `WARM_STOCK=1`, and it would have woken the moment the stock was
  raised. Its `no_usable_gpt` cleared the breaker each time, so nothing counted.
- **The ledger and the pools disagreed about staleness.** The pools were
  shortened to five minutes and `ledger.STALE_CLAIM_SECONDS` was left at two
  hours, opening a window where a dead run's Gmail went back to the pool while
  its phone still read as held. One number now, pinned by a test.
- **The healthcheck could not see a loop that was failing.** The heartbeat is
  stamped *before* the pass, so a pass that throws stamps it like one that
  works. Consecutive failed passes are counted on disk and `healthy()` reads
  them.
- **Nothing the operator reads said anything.** The `Service` tab is rewritten
  each pass; every sheet timestamp now ends in `Z`; a finish marks its phone row
  `building` while it holds it.
- **The log had no rotation** — the filename carries the start date and is
  computed once, so a service writes one unbounded file for as long as it runs.

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
