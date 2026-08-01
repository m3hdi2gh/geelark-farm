# geelark-farm

Provision GeeLark cloud phones from a spreadsheet.

One row describes one account — a proxy, a Gmail address, a password, a TOTP
secret. One run turns every pending row into a **stopped** cloud phone that is
signed into that account with the target app installed, so the manual work that
follows starts from a ready device.

```
sheet row ──► create phone (behind the row's proxy) ──► boot
          ──► sign into Google ──► verify on device
          ──► install the app  ──► verify on device
          ──► stop the phone   ──► write the result back to the sheet
```

Re-running is safe: rows already marked `done` are skipped, so `geelark run` is
a habit rather than an operation.

```
$ geelark run --workers 3
=== row 1 (1/3): DesmondCarlisle590341@gmail.com ===
=== row 2 (2/3): NormanThatcher351780@gmail.com ===
=== row 3 (3/3): BruceColeridge807413@gmail.com ===
  row 2 OK: ready (330s)
  row 3 OK: ready (340s)
  row 1 OK: ready (302s)

 3/3 phones ready. All phones are stopped; nothing is billing.
```

About five minutes per account, unattended.

---

## Why it is built this way

GeeLark ships prebuilt RPA tasks for Google login and Play Store installs. Both
were tried first, and both **reported success while having done nothing** — one
left the device stranded on a verification screen, the other never installed the
app. Their selectors cannot be corrected from outside.

So this drives the device itself, on three rules that every part of the code
follows:

**The device is the only truth.** Success is `dumpsys account` showing the
address, or `pm list packages` returning the package. Nothing a screen says and
no task status counts as evidence.

**Screens are observed, not assumed.** Every flow reads the live view hierarchy
and acts on what is actually there. Google does not present its login screens in
a fixed order, so the flows are loops over observed state, not scripts:

```python
while not outcome:
    elements = screen.read_screen(...)      # what is on screen right now
    handler = registry.match(elements)      # the first known screen that fits
    outcome = handler.act(...)              # type, tap, or stop with a reason
```

Supporting a screen Google has just started showing means adding one entry to
that registry. Anything unrecognised is archived as XML under `artifacts/` and
reported as `unknown_screen` — a task, not a mystery.

**A running phone costs money every minute.** Every budget in `.env` is a spend
cap, every path that starts a phone stops it in a `finally`, and `geelark reap`
is the backstop for when that cannot run.

## Layout

Dependencies point downward only, so the layer that knows what a Google password
screen looks like never also knows how to sign an HTTP request.

| | |
|---|---|
| `cli.py`, `orchestrator.py` | which rows, in what order, with what budget |
| `flows/google_login.py`, `flows/play_install.py` | multi-screen procedures |
| `screen.py`, `shell.py` | see the device / act on the device |
| `phones.py`, `sheets.py`, `ledger.py` | phone lifecycle, work queue, local record |
| `api.py`, `config.py` | signed transport, settings |

`tests/fixtures/` holds real view hierarchies captured from live runs. They are
the record of how each screen actually looks, and why each selector is written
the way it is.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; source .venv/bin/activate elsewhere
pip install -e ".[dev]"
cp .env.example .env            # then fill it in
```

## Configure

`.env` holds the GeeLark credentials, the spreadsheet id, the path to a Google
service-account key, and the budgets. Every field is documented in
[.env.example](.env.example).

The spreadsheet is both the work queue and the result:

| proxy | email | password | totp_secret | status | phone_id | serial | note | updated_at |
|---|---|---|---|---|---|---|---|---|

Only the first four are yours to fill in; the rest are written back. Share the
sheet with the service account's email address as an **Editor**.

`status` drives everything: `pending` → `running` → `done` or `failed:<reason>`.
A blank status counts as pending, so pasting rows in is enough.

## Use

```bash
geelark rows                    # validate every row, spend nothing
geelark run --dry-run           # show the plan
geelark run                     # process the pending rows
geelark run --workers 3         # ...three at a time
geelark run --retry-failed      # also retry failed and stuck rows
geelark reap                    # stop anything left running
```

One account at a time, with `--watch` to print a live-view link and wait so you
can follow along:

```bash
geelark login --row 1 --keep --watch
geelark install --watch
geelark stop --all
```

Phone management:

```bash
geelark proxy "socks5://user:pass@host:port"   # test a proxy, spend nothing
geelark phones --ledger                        # what exists, and who owns it
geelark create --proxy "..." --label "row 4"
geelark start / geelark stop --all / geelark delete --phone ID
```

Device diagnostics, for when a flow does something unexpected. Each resolves the
phone from `--phone`, else the only running one, and starts it if needed:

```bash
geelark dump                    # every element on screen, with tap targets
geelark dump --save f.xml       # ...and keep it as a test fixture
geelark tap Install
geelark type "secret"
geelark shell "pm list packages -3"
geelark screenshot
```

## When something goes wrong

**[docs/runbook.md](docs/runbook.md)** — every failure mode seen so far, what it
means, and what to do. Start there.

**[docs/geelark-api.md](docs/geelark-api.md)** — the vendor API's sharp edges:
the signing scheme, the rate limit that bans a key for two hours, response
envelopes that differ per endpoint, and what `/proxy/check` does and does not
tell you.

Each failed row keeps its phone (stopped) and its archived screens, and the
sheet records the reason. `geelark run --retry-failed` reuses that phone rather
than paying for another.

## Cost

**Phones bill per running minute.** Check after any interrupted run:

```bash
geelark reap --dry-run
```

Running rows in parallel shortens the wall clock, not the bill: three phones for
five minutes costs the same as three phones one after another.

## Caveats

- Automating Google account sign-in is contrary to Google's terms of service,
  and accounts may be locked. This reports such outcomes accurately; it cannot
  prevent them.
- Proxy IP reputation sets the ceiling on success. A datacenter or freshly
  allocated exit address draws CAPTCHAs and phone-verification demands that no UI
  automation resolves — and with a backconnect proxy the exit is not the host you
  dialled. `geelark proxy <url>` prints both.
- An account whose only history is on someone else's device and IP is the
  hardest case there is.
- Secrets never belong in the repository. `.env`, the service-account JSON, and
  `state/` are gitignored; keep it that way.
