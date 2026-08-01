# Runbook

What to do when a run misbehaves. Grown from real incidents — add an entry the
first time something surprises you, while the diagnosis is still fresh.

## First moves

```bash
geelark phones --ledger   # what exists, what is billing, and who owns it
geelark reap --dry-run    # what would be stopped, and why
geelark reap              # stop anything the ledger cannot account for
geelark dump --phone ID   # what is actually on that phone's screen right now
```

`/phone/start` returns a live-view URL; opening it shows the real screen and
answers most questions faster than reading logs.

`/task/detail` (logs + a screenshot taken at completion) is the best source
when an RPA task is involved.

## Money

**A phone left running keeps billing per minute.** If a run is interrupted in a
way that skips its cleanup, `geelark reap` is the fix. Check after any crash,
any Ctrl+C, and any power loss.

`reap` decides from the ledger, so understand what it will and will not touch:

| Ledger state | reap |
|---|---|
| absent | stops it — nothing here is accountable for it |
| released by its run | stops it — it should already be off |
| claimed over 2h ago | stops it — the owning process is gone |
| claimed recently | leaves it — a run is using it |

If the ledger is lost or corrupt, every phone looks like an orphan, so `reap`
would stop a run in progress. It logs an error in that case; check
`geelark phones --ledger` before reaping.

## Known failure modes

### A step reports success but nothing happened
Expected, not surprising: GeeLark's RPA tasks do this. Every step must be
confirmed against the device (`dumpsys account`, `pm list packages`). If a new
step trusts a task status, that is the bug.

### "signed in" but the Play Store asks to sign in
The account check matched `com.google` rather than a real `name=...@...` entry.
`com.google` is present in `dumpsys account` even with zero accounts. See the
verification trap in `geelark-api.md`.

### `fatal:verification_blocked`
Google refused the sign-in with "You didn't provide enough info for Google to be
sure this account is really yours", suggesting a device or network it already
knows.

**Check the archived screens before concluding anything about the account.** This
exact message was produced twice by the router's own wrong turn: it pressed "Try
another way" on a page that was already offering the authenticator, and Google
took that to mean no other factor existed. The fix was two lines of ordering, not
a better account. Look at the `2fa_*` XML in the run's artifact directory — if an
authenticator row was on screen and something else was tapped, that is the bug.

If the authenticator genuinely was not offered, then it is the account, and
nothing in this tool can resolve it. What can:

- **Give the account more to verify with.** The message is literal: a recovery
  email and phone number on the account give Google an alternative to a device
  it does not recognise.
- **Warm the account up on the same exit IP first.** Sign in manually through a
  browser on that proxy, or on the cloud phone itself, so the address and device
  are not both new at the moment automation runs.
- **Prefer accounts created on the infrastructure that will use them.** An
  account whose entire history is on a seller's device and IP is the hardest
  case there is.

Retrying immediately makes it worse: repeated refusals from a new device raise
the account's risk score.

### Login stalls on 2-Step Verification
Google pushed a prompt to a device the account already trusts — often a cloud
phone from an earlier run. The router handles it by switching to the
authenticator code; if it recurs, confirm the TOTP secret is normalised
(uppercase, unspaced, unpadded).

### `failCode 20002`
Two RPA tasks on one phone. Concurrency is across phones only.

### Rate-limited / API calls suddenly all fail
200 req/min exceeded bans the key for two hours. Nothing to do but wait, then
lower `API_REQUESTS_PER_MINUTE`.

### CAPTCHA, or a demand for a phone number
Usually the proxy IP's reputation, not the automation.

Check the **outbound** IP, not the host you dialled — with a backconnect proxy
they differ, and Google judges the exit address. `geelark proxy <url>` prints
both.

Do not read anything into GeeLark's `country` field; it comes back empty for
addresses that resolve fine elsewhere (see `geelark-api.md`). To actually assess
an IP:

```bash
curl "http://ip-api.com/json/<outbound-ip>?fields=country,isp,proxy,hosting,mobile"
```

`hosting: true` means a datacenter address, which is the strongest predictor of
challenges. Scamalytics and spur.us give a fuller reputation picture. A
residential or mobile ISP with `hosting: false` is what you want.

### "still installing..." repeats until the budget runs out
The Install tap did not land on the Install button. Look at the run's
`*-play-package-page.xml`: if there is no Install element on it, something was
covering the page - Play's Terms of Service on a brand-new account, most likely.

Two defects produced exactly this in one run (2026-07-31), and both are fixed:
`Accept` was missing from the install flow's interstitial list, and `screen.find`
matched the word "install" inside the dialog's body text, so it tapped a
paragraph and reported success. Partial matches are now required to be
label-shaped - the query as a whole word, in a string not much longer than the
query - so a paragraph can never win over a button.

If it recurs, the archived XML names the screen that needs a new entry.

### `fatal:no_install_button` with nothing on the archived screen
The Play Store had not finished drawing the page. The archived
`*-play-package-page.xml` will parse to zero labelled elements, usually with a
`ProgressBar` in the middle of it.

Fixed 2026-08-01: the flow now distinguishes "nothing on screen" from "nothing I
recognise", waiting for the former and only failing on the latter. It surfaced
under `--workers 3`, where everything is slower and the six-second wait after the
deep link was not enough - the sequential runs had simply been lucky.

If it recurs, the page took longer than `PRE_INSTALL_SECONDS` to render; check
whether that proxy's exit is unusually slow.

### A row stuck on `running`
Its run died without writing an outcome, so the row is neither done nor
retryable and no later run will select it - the work is lost, and so is the
phone it names.

```bash
geelark phones --ledger    # confirm no run is actually holding it
geelark run --retry-failed # reclaims running rows as well as failed ones
```

Since 2026-08-01 the orchestrator catches every exception per row, so this
should only be reachable after a hard crash or power loss. It happened once
because a raw `requests` exception escaped the handler; both that and the
reclaim path are fixed.

### `ConnectionResetError(10054)` during a parallel run
A `requests.Session` shared across threads. Fixed - each thread now gets its
own. If something similar appears, check that no new code holds a `Session`,
`gspread` client, or other connection-pooled object across workers.

### A password containing '%'
Handled. `input text` turns `%s` into a space, so a password containing that
exact pair is typed in two calls, ending one after the `%` so it stays literal.
Every other `%` needs nothing. Only non-ASCII characters are still refused, and
those need an IME such as ADBKeyboard.

### Selectors stop matching
Confirm the phone's UI is English: `mobileLanguage` must be `default`, or every
English text selector fails (`20008`).

### App installs but will not run
Play Integrity attestation. See `/phone/keyboxUpload`. ChatGPT has been
observed launching normally, so this has not been hit yet.

## Credential hygiene

The sheet holds Google passwords and TOTP secrets, and `.env` holds the API
key. Neither belongs in git — `.gitignore` covers both, and the service-account
JSON as well. Rotate anything that has been pasted into a chat, a ticket, or a
screenshot.
