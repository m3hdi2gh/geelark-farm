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

## The run will not stop
Ctrl+C once is enough, and the summary tells you whether anything is still
billing. If a second Ctrl+C does nothing and the phone log keeps scrolling,
the main thread has already died and Python is waiting on the worker threads
(a ThreadPoolExecutor's are not daemons). Kill it and check:

```powershell
Stop-Process -Name geelark -Force
```

```bash
geelark reap
```

Killing it is safe — `reap` is the backstop, and it is what tells you the
truth about billing rather than the terminal you just closed.

That hang was a bug, fixed on 2026-08-08: an interrupt stopped the phones but
never told the workers, so each carried on polling the phone that had just been
stopped underneath it for the rest of its boot timeout.

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

**The fix is a different proxy, and only that.** `captcha_shown` is the one
failure whose phone the run deletes instead of keeping: the proxy is bound to a
phone when it is created and cannot be changed afterwards, so a retry on that
phone meets the same exit address and the same challenge. Measured on
2026-08-04 — a row's exit answered three consecutive checks with the same IP,
so these addresses are sticky rather than rotating. The row is left with its
reason and no phone; put a new proxy in the sheet and re-run it.

Do not conclude anything about the account from a CAPTCHA raised at the email
step: Google had not yet seen the password, so it is a judgement on the network
and the device, not on the credentials.

### `password_changed`
Google accepted the address and rejected the password as the old one - the
archived screen says when it was changed. Nothing on the device fixes this: put
the current password in the sheet.

The phone is deleted rather than kept, so the slot goes back. That is a choice,
not a necessity - the phone would work with a corrected sheet. If these
passwords do turn up in practice, take `password_changed` out of `UNREUSABLE`
in `orchestrator.py` and the phone will be waiting for the retry.

### `stuck_on_<screen>`
The flow saw the same screen more times than its allowance and gave up. Read it
as "the screen did not change when I acted on it", never as a description of
what is wrong - twice now it has named the wrong screen entirely:

- **row 1, 2026-08-05** reported `stuck_on_email_entry` while sitting on
  Google's g.co/sc security-code page, because `email_entry` matched on the
  word "sign in" and typed the address into the code box.
- **row 13, 2026-08-06** reported the same thing while the email page was
  merely still loading; the flow read the page behind the spinner and retyped
  into it four times.

Both are fixed, and the lesson holds for the next one: open the last archived
XML before believing the name. `artifacts/<run>/` keeps every screen the flow
visited, in order.

### `app_unknown_screen` showing the Play Store
The app was installed but never came to the front, so the flow drove against
Play's own package page — "Uninstall", "Open" — matched nothing, and named the
screen rather than the cause. Fixed on 2026-08-08: the launch now asks the
device which app is in front and retries.

If it recurs, the archived XML says which app was showing. `geelark shell
"dumpsys window | grep mCurrentFocus"` answers the same question live.

### The live table leaves copies of itself behind
Cosmetic. Live erases its previous frame by moving the cursor up over the lines
it drew; its region sits at the bottom of the window, so when the table grows
and the terminal scrolls, the part that scrolled off is in the scrollback where
no cursor can reach it. The leftovers are always a header and one row.

Restarting the display on a resize, and around printing the live-view links,
covers the cases that caused it. **The summary at the end is authoritative** —
it is printed once, after the table is finished with.

### `app_request_rejected` — change the exit IP
OpenAI answered the sign-in with:

    There is a problem with your request. (a27a1dff7e6fe572-EWR)

The identifier is a **Cloudflare Ray ID** — this is their edge refusing the
request, not their login rejecting the credentials. It is a judgement about
where the request came from. The account and the password were never examined.

**Retry first.** These proxies hand out a different exit each session, and that
alone has fixed it:

```bash
geelark run --retry-failed
```

**If it recurs on the same row, change the proxy — and delete the phone.** A
phone keeps the proxy it was created with, so a new proxy in the sheet does
nothing until the row gets a new phone:

```bash
geelark delete --phone <id>
```

Then clear that row's `phone_id` and `serial` and re-run it.

The flow submits the address twice and then stops. Not more, deliberately:
rapid repetition is what a bot-protection layer exists to punish, so hammering
it makes the score worse rather than better, and every attempt counts against a
real account. Spamming it by hand does eventually work — that is winning a
lottery on which requests happen to share an exit, not a strategy to automate.

Note the toast fades within seconds, so an archived capture of this usually
shows the address sitting in the box with no error anywhere. That is why the
flow identifies it by having already submitted rather than by the message.

### A field ends up holding more than was typed
Backspace only deletes to the left of the cursor, and a field is focused by
tapping it — on a filled field that puts the cursor in the middle of the text,
so the right-hand side survives. An email box grew "com" on every retry until
it read `...@gmail.comcomcom`, four submissions later (2026-08-08).

`clear_field` now sends the cursor to the end first and follows the backspaces
with forward deletes, and `fill` reads the field back and corrects it once.
If this ever recurs, the warning names both values:

    the field holds '...' after typing '...'; clearing it properly and trying
    once more

### `app_<reason>` — the app login failed, not the phone
Everything before it worked. The phone is signed into Google and has the app
installed; only the last step is missing, and the note in the sheet says so.
The usual fix is the app account rather than anything about the phone, and a
retry reuses it:

```bash
geelark run --retry-failed
```

`app_email_code_required` is the common one: the app account has no
authenticator, so OpenAI emails a one-time code instead. Nothing on the device
can read that. Set up 2FA on that account, or put one that has it in the sheet.

Do not confuse it with a login failure. Before this reason existed the page
saying "Enter the verification code we just sent to..." was matched as the
authenticator prompt, and the flow typed TOTP codes into it until they ran out
— each one answered "Incorrect code" (2026-08-07).

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

### `play_server_error`
Play replaced the package page with "Server error" and a Try again button. The
flow presses it up to three times; three failures means Play, not the flow.

That page also offers a mini-game while you wait, whose button is labelled
**Play**. Anything added here must target a label by name — "press the
clickable button" starts the game.

### `app_network_ssl_rejected`
"For your security, ChatGPT can't connect while this network is presenting an
unexpected SSL certificate."

Nothing about the account is involved, and the phone is fine — Google signed in
and the app installed over that same proxy, so only the app login is missing.

**It is the exit address, not the proxy.** Measured across twelve attempts on
five gateways: every gateway produced both successes and this refusal, and all
four refusals cleared on a later attempt. What every one of those later
attempts had in common was a phone restart, which opens a new session through
the proxy and comes out somewhere else.

So the run now restarts the phone once by itself and tries again, which is why
you should rarely see this reason at all. Seeing it means the second exit was
refused too, or the row had under seven minutes of budget left. Re-run it:

```bash
geelark run --retry-failed
```

Replacing the proxy is the answer only if one row keeps producing it while
others on the same provider do not.

### The download never starts
"Waiting for connection… Download will begin once restored" — Play has parked
the download rather than failed it, and left it parked. One row spent its whole
budget on that page (2026-08-07). The flow now cancels and asks again, up to
three times, since the page keeps its Cancel button and the state is
recoverable. Three failures in a row means something other than the download.

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

### The summary says a phone could not be stopped
Believe it, and act on it now:

```bash
geelark reap
```

It happens when the network drops during cleanup - the run knows it failed to
stop the phone but cannot do anything more about it. Seen 2026-08-01, when DNS
went away mid-batch.

Until that run, the summary ended with "All phones are stopped; nothing is
billing" unconditionally, and the failure was only an ERROR line hundreds of
lines above. The phone billed until someone noticed by hand. The summary now
refuses to make that claim unless it is true.

### `[44002] Maximum number of package environments reached`
No profile slots left. Ask the plan itself rather than guessing:

```bash
geelark plan
```

It reports the total, how many are free, and how many are cloud phones. **The
pool is shared with browser profiles**, which is not obvious and cost an
investigation on 2026-08-01: 20 slots, 19 phones, and a single browser profile
holding the twentieth. Browser profiles cannot be listed through the cloud API -
they live behind the local agent - so look in the GeeLark desktop app.

Free a slot, or raise the plan, then:

```bash
geelark run --retry-failed
```

`geelark plan` also reports the parallel limit, which is what the account may run
concurrently without extra charge.

### The network drops mid-batch
Rows in flight fail with `ConnectionResetError` or `Failed to resolve
openapi.geelark.com`; the rest of the batch carries on. Read-only calls retry
automatically, which is why some rows survive a blip and only the unlucky ones
do not.

Afterwards: `geelark reap` first, then `geelark run --retry-failed`, which
reuses the phones those rows already created rather than paying again.

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

Archived screens under `artifacts/` contain the account's email address in plain
text. That directory is gitignored, but `tests/fixtures/` is not: anonymise a
capture before promoting it to a fixture. Passwords and TOTP codes do not appear
— Android marks those fields `password="true"` and does not expose their
contents — but the address does.
