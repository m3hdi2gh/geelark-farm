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
