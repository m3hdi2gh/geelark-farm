# Runbook

What to do when a run misbehaves. Grown from real incidents — add an entry the
first time something surprises you, while the diagnosis is still fresh.

## First moves

```bash
geelark phones          # what exists, and what is running (i.e. billing)
geelark reap            # stop anything running that the ledger cannot explain
geelark dump --phone ID # what is actually on that phone's screen right now
```

`/phone/start` returns a live-view URL; opening it shows the real screen and
answers most questions faster than reading logs.

`/task/detail` (logs + a screenshot taken at completion) is the best source
when an RPA task is involved.

## Money

**A phone left running keeps billing per minute.** If a run is interrupted in a
way that skips its cleanup, `geelark reap` is the fix. Check after any crash,
any Ctrl+C, and any power loss.

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
Usually the proxy IP's reputation, not the automation. Check the outbound IP on
Scamalytics and spur.us. `/proxy/check` returning `country: None` means
geolocation databases do not recognise the address — a warning sign of a
freshly allocated or datacenter IP.

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
