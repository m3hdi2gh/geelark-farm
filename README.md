# geelark-farm

Provision GeeLark cloud phones from a spreadsheet.

You keep three tabs of stock — proxies, Gmail accounts, app accounts. A run
turns that stock into **stopped** cloud phones, each signed into a Gmail with
the target app installed and signed in, so the manual work that follows starts
from a ready device.

```
take a proxy ──► create phone behind it ──► boot
             ──► first usable Gmail ──► sign in  ──► verify on device
             ──► install the app                 ──► verify on device
             ──► first usable app account ──► sign in
             ──► stop the phone ──► record it in the Phones tab
```

Nothing is spent in advance. A credential the service rejects costs that
credential — the next one is tried on the same phone, which is already booted.
A refusal aimed at the network costs an exit address instead, and the
credential is kept. Which is which is decided in one place,
[failures.py](src/geelark_farm/failures.py).

```
$ geelark build --count 3 --workers 3
  build    account                             phone   state    time
      1    FrozenStorm658294@… + fuyironoxu…   650     ready    403s
      2    QuantumKnight472186@… + gijozuka…   651     ready    461s
      3    EchoWolf915320@… + dehilixihat83…   652     ready    426s

 3/3 phones ready. All phones are stopped; nothing is billing.
```

About six minutes per phone, unattended.

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

**A running phone costs money every minute.** `BUILD_BUDGET_SECONDS` bounds
what one phone can spend and every step is capped by what is left of it; every
path that starts a phone stops it in a `finally`; and `geelark reap` is the
backstop for when that cannot run.

**Nothing retries a fixed number of times.** Caps were tried and each one cost
something real: three Gmails while eleven sat unused, five dead proxies while
four live ones waited. What bounds a phone is the stock and the budget, and
whichever runs out is what gets reported.

## Layout

Dependencies point downward only, so the layer that knows what a Google password
screen looks like never also knows how to sign an HTTP request.

| | |
|---|---|
| `cli.py`, `ui.py` | what to ask for, and how it is shown |
| `builder.py` | what to do with a phone, and what a failure costs |
| `failures.py` | whose fault each failure is — one table, nothing else decides |
| `flows/router.py` | the screen loop every flow runs on |
| `flows/google_login.py`, `flows/play_install.py`, `flows/chatgpt_login.py` | multi-screen procedures |
| `screen.py`, `shell.py` | see the device / act on the device |
| `phones.py`, `pools.py`, `ledger.py` | phone lifecycle, the stock tabs, local record |
| `api.py`, `gsheet.py`, `config.py` | signed transport, sheet transport, settings |

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

### The tabs

One spreadsheet, four tabs. Three are stock you fill in; the fourth is what the
tool produced. Share it with the service account's email address as an
**Editor**.

| tab | you fill in | the tool writes back |
|---|---|---|
| `Gmails` | Address, Password, 2FA Secret | Used Date, Phone Serial, Status, Note |
| `Proxy` | Proxy String (or Host/Port/Username/Password) | Last Exit IP, Used By, Status, Note |
| `Gpt Info` | Address, Password, 2FA Secret | Phone Serial, Status, Note |
| `Phones` | — | one row per phone built, ready or not |

A resource is available while its `Status` is blank — or `unused` on the Proxy
tab, where that column doubles as the record of whether the proxy still works.
Claiming writes `in_use` before the row is handed out, so nothing takes it
twice; `geelark pools --release-stuck` frees what a dead run left behind.

What a failure costs is decided by [failures.py](src/geelark_farm/failures.py),
which is the only place that classifies one. A bad Gmail — wrong password, a
CAPTCHA, a locked account — is marked in its own tab and the **next one is
tried on the same phone**, which is already booted.

OpenAI's two network refusals are the exception: they arrive before any account
is examined, so they are a verdict on the exit address rather than on the
credential. Those get a **new exit on the phone that already exists**, and the
same credential is tried again. The cheapest form first — sx.org will hand a
proxy a different exit three times a day while keeping its host, port and
credentials, so nothing on the phone changes. Only when that allowance is spent
does the build take another proxy (`/phone/detail/update`). Fill the Proxy
tab's `Port ID` and set `SXORG_API_KEY` to enable the refresh; without either,
it goes straight to the next proxy.

A proxy left behind goes back to the pool as `unused`, not condemned: those
refusals were measured to be per-session, not per-proxy.

## Use

```bash
geelark ui                      # interactive console - everything, one screen
```

It opens on a dashboard - stock, phones, free slots - and a menu, and it
defaults the count to what the stock can actually produce. A batch drawn there
is one line per phone, updated live, instead of several workers' logs
interleaved. Ctrl+C asks the run to stop and waits while each phone is stopped
and its rows released.

Everything below is the same set of actions without prompts, which is what cron
and CI need:

```bash
geelark build --count 5         # end up with 5 ready phones
geelark build --dry-run         # ...show what that would take, spend nothing
geelark finish                  # only the phones one step short of ready
geelark pools                   # what the tabs hold; frees stranded proxies
geelark reap                    # stop anything left running
```

`build` finishes before it builds: a phone that already has its Gmail and the
app costs one app account, where a new one costs a phone, a Gmail and a proxy
to reach the same place.

One account at a time, with `--watch` to print a live-view link and wait so you
can follow along:

```bash
geelark login --row 1 --keep --watch
geelark install --watch
geelark stop --all
```

Phone management:

```bash
geelark plan                                   # slots, free slots, parallel limit
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

Every phone reaches the `Phones` tab whether it worked or not, with what it
tried and why it stopped, and keeps its archived screens under `artifacts/`.
Nothing is thrown away for failing: a phone that got as far as its Gmail and
the app is one `geelark finish` from ready once the app tab is topped up.

Each `Status` in the tabs is a reason from
[failures.py](src/geelark_farm/failures.py), and that file says what to do
about it in plain words — it is the first place to look, not the code.

## Cost

**Phones bill per running minute.** Check after any interrupted run:

```bash
geelark reap --dry-run
```

Running rows in parallel shortens the wall clock, not the bill: three phones for
five minutes costs the same as three phones one after another.

## Contributing

`ruff check .` and `pytest` run on every push. The whole suite is offline - no
credentials, no spreadsheet, no phone - so it can never spend money, and
`geelark --help` is checked to work on a machine with no `.env` at all.

Anything that touches a real device is verified by running the tool and reading
what came back, never by a test asserting that it should have worked. When a
screen surprises a flow, its hierarchy is archived under `artifacts/`; the fix
is to add a fixture from that capture and a selector that matches it.

**Anonymise a capture before committing it.** Archived screens come from real
runs and carry the account's address in plain text. `artifacts/` is gitignored,
but `tests/fixtures/` is not — replace addresses with `something@example.com` on
the way in. The fixture's value is the structure of the screen, never whose
account it was.

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
