# geelark-farm

Provision GeeLark cloud phones from a spreadsheet.

You keep three tabs of stock — proxies, Gmail accounts, app accounts. A run
turns that stock into **stopped** cloud phones, each signed into a Gmail with
the target app installed and signed in, so the manual work that follows starts
from a ready device.

```
take a proxy ──► create the phone behind it ──► boot
             ──► first usable Gmail  ──► sign in  ──► verify on the device
             ──► install the app                 ──► verify on the device
             ──► first usable app account ──► sign in ──► verify in the app
             ──► stop the phone ──► write the row
```

About twelve minutes per phone, unattended — the median over 93 phones
that reached ready, four minutes at the fastest and half an hour at the
slowest. A phone that works through several credentials takes longer, which is
the point of it doing so.

Nothing is spent in advance. A credential the service rejects costs that
credential — the next one is tried on the same phone, which is already booted.
A refusal aimed at the network costs an exit address instead, and the
credential is kept. Which is which is decided in one place,
[`failures.py`](src/geelark_farm/failures.py).

---

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Configure](#configure)
- [The spreadsheet](#the-spreadsheet)
- [Check the setup](#check-the-setup)
- [Use](#use)
- [What a failure costs](#what-a-failure-costs)
- [Why it is built this way](#why-it-is-built-this-way)
- [Project layout](#project-layout)
- [Development](#development)

---

## Requirements

- Python 3.10 or newer
- A GeeLark account with API credentials and free profile slots
- A Google Cloud service account, and a spreadsheet shared with it as an
  **Editor**
- Proxies, Gmail accounts and app accounts to put in the tabs

No Android SDK, no `adb`, nothing native. Every device command goes through
GeeLark's API, so the tool runs the same on Windows, macOS and Linux.

## Install

```bash
git clone <this repo> && cd geelark-farm
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
```

**The editable install (`-e`) is required.** Settings, `logs/`, `artifacts/`
and `state/` are resolved relative to the repository root, which the package
finds from its own location. Installed non-editable it lands in
`site-packages`, `.env` is never found, and the working directories are
created in the wrong place. With `-e` you can run `geelark` from anywhere.

## Configure

`.env` holds everything. Every field is documented in
[`.env.example`](.env.example); these are the ones without a working default:

| setting | what it is |
|---|---|
| `GEELARK_APP_ID`, `GEELARK_API_KEY` | GeeLark dashboard → API settings → open API credentials |
| `GOOGLE_SHEET_ID` | the id in the spreadsheet's URL |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | path to the service account's JSON key (default `./secrets/service-account.json`) |

The rest have defaults worth knowing about:

| setting | default | effect |
|---|---|---|
| `MAX_CONCURRENT_PHONES` | `1` | how many phones are worked on at once |
| `BUILD_BUDGET_SECONDS` | `3600` | the outer bound on one phone; every step gets the smaller of its own budget and what is left |
| `API_REQUESTS_PER_MINUTE` | `120` | GeeLark allows 200/min and bans the key for two hours above it |
| `TARGET_PACKAGE` | `com.openai.chatgpt` | deep-linked by package id, so no Play Store search and no clones |
| `LOG_LEVEL` | `INFO` | the console only — the file log is always DEBUG |

Every invocation appends to `logs/<date>-<machine>.log` at DEBUG whatever
`LOG_LEVEL` says. The console is for watching a run; the file is for finding
out afterwards what happened.

## The spreadsheet

Six tabs. **Four you create**, two the tool creates for itself.

Column order does not matter anywhere — columns are found by header name — so
you can rearrange them, and adding your own is safe.

### Gmails — Google accounts to sign in

| you fill in | the tool writes |
|---|---|
| `Address`, `Password`, `2FA Secret` | `Used Date`, `Phone Serial`, `Status`, `Note` |

`Purchase Date` and `Seller` are ignored by the tool and yours to use.

### Proxy — the exits

| you fill in | the tool writes |
|---|---|
| `Name`, `Proxy String` | `Last Exit IP`, `Used By`, `Status`, `Note`, `Times Used`, `Last Used` |

`Proxy String` takes `socks5://user:pass@host:port` or
`host:port:user:pass`. `Name` is what the proxy is called in the vendor's
panel — `SX4` — and it is what every other tab and every log line calls it,
because "proxy SX4 is dead" is something you can act on where a host and port
sends you comparing strings across two windows.

Exits go out **least used first**, so one full round covers every proxy before
any of them is taken twice. `Times Used` and `Last Used` are added
automatically the first time the tool opens the tab.

### Gpt Info — accounts for the app itself

| you fill in | the tool writes |
|---|---|
| `Address`, `Password`, `2FA Secret` | `Phone Serial`, `Status`, `Note` |

### Phones — what the runs produced

Written by the tool, one row per phone, except for `State` which is yours.

`Created`, `Serial`, `State`, `Proxy`, `Gmail`, `GPT Account`, `Status`, `Note`

### Lists and History — created automatically

`Lists` holds the dropdown values for every `Status` and `State` column,
regenerated each session from `failures.py` so the dropdown can always offer
what a run can actually write. `History` is an append-only record of what
happened, one row per event, stamped with the machine that wrote it.

### Status: what a run concluded

A row is available while its `Status` is one the tool treats as free. The
words differ per tab, because what they describe differs.

| tab | available | taken | finished with |
|---|---|---|---|
| Gmails, Gpt Info | *(blank)* | `in_use` | `ready`, then `used` / `delivered` |
| Proxy | *(blank)*, `free`, `unused` | `claimed` | `on a phone` |

A proxy is never *spent* — it keeps working, and the column says where it is
rather than whether it is gone. Two proxy statuses take a row out of the pool
until you put it back:

- **`change ip`** — a service refused the connection through this exit. These
  rows carry no port id, so nothing here can ask for a new address; change it
  in the vendor's panel and set the status back to `free`.
- **`dead`** — GeeLark could not reach it. Retested every run, because these
  are rented monthly and one that stopped answering yesterday is often
  answering again today.

Anything else in a `Status` column is a verdict from
[`failures.py`](src/geelark_farm/failures.py) — `wrong_password`,
`captcha_shown`, `email_code_required` — and the `Note` beside it says in
plain words what was seen and what to do.

### State: an instruction back to the tool

`Status` is what a run concluded. `State`, in the Phones tab, is the other
direction — written by hand between runs and carried out at the start of the
next one:

| you write | what happens |
|---|---|
| `unused` | the default. Nothing. |
| `done` | finished with it. The phone is deleted, the row dropped, its app account marked `delivered`. |
| `failed` | something is wrong with it. The phone is deleted, the row dropped, its app account **freed** for another phone. |

Either way the Gmail is retired as `used`: it signed into that phone, and that
is the credit it had to spend. A running phone is never touched, only reported.

## Check the setup

```bash
geelark verify
```

One command for the whole chain, in the order the pieces depend on each other,
and each line says what to do rather than only what is wrong:

```
  ok    .env             /home/you/geelark-farm/.env
  ok    geelark api      appId YJI9O5..., 9 phone(s) visible
  ok    plan             30 slot(s), 21 free
  ok    service account  geelark-farm-bot@example.iam.gserviceaccount.com
  ok    spreadsheet      Cloud Phones Automation Sheet
  ok    write access     the key can write (tested without changing anything)
  ok    tabs             Gmails, Gpt Info, Proxy, Phones (+ Lists, History)
  ok    columns          every column the code writes exists
  warn  stock            18 gmails, 13 proxies, 0 app accounts
                         A run stops at whichever runs out first; app accounts
                         would stop it immediately.
```

The service-account address it prints is the one to share the spreadsheet
with. Write access is tested by writing a header cell back to the value it
already holds — a real write that changes nothing, because reading proves
nothing about the role the book was shared with. Warnings are a setup that
works and will not get far; only a failure exits non-zero.

## Use

```bash
geelark ui
```

The console is the whole tool on one screen. It opens on a dashboard — stock,
phones, free plan slots — then a menu split by what a choice costs: four
things that **do** something, four that only **look**. A batch draws one line
per phone, updated live, instead of several workers' logs interleaved. Ctrl+C
asks the run to stop and waits while each phone is stopped and its rows
released.

Every action is also a command, which is what cron and CI need:

```bash
geelark build --count 5          # end up with 5 ready phones
geelark build --dry-run          # ...show what that would take, spend nothing
geelark finish                   # only the phones one step short of ready
geelark pools                    # what the tabs hold, and what is stuck
geelark verify                   # check the setup
```

`build` finishes before it builds: a phone that already has its Gmail and the
app costs one app account, where a new one costs a phone, a Gmail and a proxy
to reach the same place.

One account at a time, with `--watch` to print a live-view link and wait so
you can follow along:

```bash
geelark login --row 1 --keep --watch
geelark install --watch
```

Phones and the account:

```bash
geelark ping                                   # credentials work, phones visible
geelark plan                                   # slots, free slots, parallel limit
geelark phones --ledger                        # what exists, and who owns it
geelark proxy "socks5://user:pass@host:port"   # test a proxy, spend nothing
geelark create --proxy "..." --label "row 4"
geelark start / geelark stop --all / geelark delete --phone ID
geelark reap                                   # stop phones nothing is accountable for
```

Device diagnostics, for when a flow does something unexpected. Each resolves
the phone from `--phone`, else the only running one, and starts it if needed:

```bash
geelark dump                    # every element on screen, with tap targets
geelark dump --save f.xml       # ...and keep it as a test fixture
geelark tap Install
geelark type "some text"
geelark shell "pm list packages"
geelark screenshot
```

### Naming

A phone is called `832 - NovaEclipse738465` in GeeLark's own list: the serial
first, because that is the key everything else is filed under — the Phones
tab is addressed by it, History records it, a failed build's artifacts are
named for it — then the address, because that is the half a person thinks in.
Phones that already exist are renamed to match on every run.

### Exits

When a service refuses the connection rather than the account, the build keeps
the credential and takes the next proxy. The refusal is per-session rather than
per-proxy — measured across twelve attempts, every gateway produced both
successes and rejections — so the proxy is not condemned; but its *address* has
just been turned down, so the row is held back rather than freed, and the cell
says to change the address in the vendor's panel before setting it to `free`.

There was a cheaper branch: sx.org can hand a proxy a new exit address while
keeping its host, port and credentials, so nothing on the phone changes. Only
the vendor's `port` product can do that. This account holds none — they are all
the Unlimited product, which does not appear in the vendor's port listing at
all — so the branch never once ran, and it has been removed (2026-08-25).

## What a failure costs

Every failure is classified in one table,
[`failures.py`](src/geelark_farm/failures.py), and nothing else classifies
one. Each verdict names who is to blame, which decides what the failure costs:

| blame | example | what it costs |
|---|---|---|
| `credential` | `wrong_password`, `captcha_shown`, `email_not_found` | that credential. The next one is tried on the same phone, which is already booted. |
| `exit` | `network_ssl_rejected`, `request_rejected` | an exit address. The credential is kept and tried again. |
| `device` | `download_stalled`, `app_would_not_start`, `unknown_screen` | the phone. Nothing about the credentials was learned. |
| `challenged` | `email_code_required` | nothing yet — the account is set aside with what it is waiting for, for a human to answer. |
| `nobody` | `no_usable_proxy`, `all_exits_refused`, `interrupted` | nothing. The build stopped without judging anything — the stock ran out, or you stopped it. |

The verdict is written to the credential's own tab, and the `Note` beside it
is a sentence, not a token: what was seen, and what to do about it.

Anything the flows do not recognise is archived as XML under `artifacts/` and
reported as `unknown_screen` — a task, not a mystery.

## Why it is built this way

GeeLark ships prebuilt RPA tasks for Google login and Play Store installs.
Both were tried first, and both **reported success while having done
nothing** — one left the device stranded on a verification screen, the other
never installed the app. Their selectors cannot be corrected from outside.

So this drives the device itself, on three rules every part of the code
follows.

**The device is the only truth.** Success is `dumpsys account` showing the
address, `pm list packages` returning the package, or the app's own settings
page naming the account that is signed in. No screen and no task status counts
as evidence.

**Screens are observed, not assumed.** Google does not present its login
screens in a fixed order, so the flows are loops over observed state, not
scripts:

```python
while not outcome:
    elements = screen.read_screen(...)      # what is on screen right now
    handler = registry.match(elements)      # the first known screen that fits
    outcome = handler.act(...)              # type, tap, or stop with a reason
```

Supporting a screen Google has just started showing means adding one entry to
that registry.

**Nothing retries a fixed number of times.** Caps were tried and each one cost
something real: three Gmails while eleven sat unused, five dead proxies while
four live ones waited. What bounds a phone is the stock and the budget, and
whichever runs out is what gets reported.

## Project layout

Dependencies point downward only, so the layer that knows what a Google
password screen looks like never also knows how to sign an HTTP request.

| | |
|---|---|
| `cli.py`, `ui.py` | what to ask for, and how it is shown |
| `verify.py` | whether the setup can work at all |
| `builder.py` | what to do with a phone, and what a failure costs |
| `failures.py` | whose fault each failure is — one table, nothing else decides |
| `flows/router.py` | the screen loop every flow runs on |
| `flows/google_login.py`, `flows/play_install.py`, `flows/chatgpt_login.py` | the multi-screen procedures |
| `screen.py`, `shell.py` | see the device / act on the device |
| `phones.py`, `pools.py`, `ledger.py` | phone lifecycle, the stock tabs, local record |
| `api.py`, `gsheet.py`, `config.py` | signed transport, sheet transport, settings |

`artifacts/` holds the pages every flow went through, which is what makes a
failure diagnosable. It is pruned on every run: a build that worked keeps its
pages while its phone exists, a build that failed keeps them for a week.

`state/ledger.json` is this machine's record of which phone belongs to which
run. It is what `geelark reap` consults to decide that nothing is accountable
for a running phone, so it is per-machine and not shared.

Further reading: [`docs/geelark-api.md`](docs/geelark-api.md) for the API's
undocumented corners, [`docs/runbook.md`](docs/runbook.md) for what to do when
a run goes wrong.

## Development

```bash
pytest
ruff check src tests
```

`tests/fixtures/` holds real view hierarchies captured from live runs. They
are the record of how each screen actually looks, and why each selector is
written the way it is — a new screen means a new fixture, taken from the
device with `geelark dump --save`.

Tests are written where a failure would be expensive to find any other way,
and each says in its docstring what went wrong and when. Several are sweeps
over the source rather than unit tests: every note the code can write must be
a sentence, every `geelark <command>` named in the code must exist, and every
step of the sheet sync must have something to say about itself.

### Secrets

`.env` and `secrets/` are gitignored, and must stay that way. `.env` holds
Google passwords, TOTP secrets and proxy credentials; the service-account JSON
grants write access to the spreadsheet. The GeeLark API key is the account —
anyone holding it can create and delete phones on it.
