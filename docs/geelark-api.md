# GeeLark open API — field notes

Hard-won knowledge from building against this API. The official reference is at
<https://open.geelark.com/api/> (also distributed as a zip of ~198 markdown
files); this document records only what the docs get wrong, omit, or bury.

## Transport and auth

- Base URL `https://openapi.geelark.com/open/v1/...`. Every endpoint is
  `POST` + JSON, including pure reads.
- Auth headers on every request: `appId`, `traceId` (uppercase uuid4), `ts`
  (milliseconds), `nonce` (first 6 chars of `traceId`), `sign`.
- `sign = SHA256(appId + traceId + ts + nonce + apiKey)` as **uppercase hex**.
  Verified against the worked example in the docs.

## Rate limit

200 requests/minute, 24,000/hour. **Exceeding it bans the key for two hours.**

Consequences for design:
- keep polling intervals at 10 s or more;
- the limiter must be central to the process, because every concurrent worker
  spends from the same budget;
- prefer webhooks (`/callback/set`, event type 6 = RPA task finished) over
  polling once more than a handful of phones run at once.

Read timeouts do occur on healthy calls. Retry transport failures, but never
retry a call that may already have changed state.

## Status codes

| Domain | Values |
|---|---|
| Task | `1` waiting, `2` in progress, `3` completed, `4` failed, `7` cancelled |
| Phone | `0` running, `1` starting, `2` shut down, `3` expired |

## Response-shape trap

The envelope is not consistent across endpoints:

- `/phone/addNew` returns per-item results under **`details`** — each item
  carries its own `code`, `msg`, `id`, `envSerialNo`, `equipmentInfo`.
- `/phone/status`, `/phone/start`, `/phone/stop` return **`successDetails`**
  and **`failDetails`**.

Do not assume; check the doc for each endpoint. This cost a debugging cycle.

## Concurrency

**One RPA task per phone at a time.** A second concurrent task on the same
phone fails with `failCode 20002` ("machine is performing other tasks").
Parallelism must therefore be across phones, never within one.

## Endpoints that are easy to overlook

- `/phone/start` returns a `url` in `successDetails`: open it in a browser to
  watch the phone's screen live. The single most useful debugging aid.
- `/task/detail` returns `logs` (a step-by-step RPA trace) and `resultImages`
  (a screenshot taken at completion or failure).
- `/shell/execute` runs arbitrary shell on the device. This is what makes
  hand-written flows possible at all.
- `/adb/getData` returns ip/port/password for a real ADB connection, so
  `uiautomator2`, Appium or scrcpy can be used instead of GeeLark's RPA.
- `/phone/keyboxUpload` matters if an installed app fails Play Integrity
  attestation.

## Phone creation constraints

- `region: "us"` only offers Android 15. `sgp` and `cn` are more flexible.
- `netType` (0 = Wi-Fi, 1 = mobile) applies only on Android 12/13/15.
  Requesting `1` has been observed to come back as `0` with `enableSim: 1`;
  unexplained, harmless so far.
- `mobileLanguage` must be `default` (English). A non-English UI makes every
  English text selector — ours and GeeLark's — fail (`20008`).
- Bind the proxy at creation via `proxyInformation` (a URL string) or
  `proxyNumber` (a serial from `/proxy/list`), so the device never reaches the
  network unproxied. `/phone/detail/update` can change it later.
- `mobileRegion` follows the proxy automatically when left unset.

## The most important lesson: RPA tasks report false success

Both prebuilt RPA tasks have returned `status: 3` ("Run successfully") having
accomplished nothing:

1. `googleLogin` — reported success while the device was not signed in; the
   screen was stranded on 2-Step Verification.
2. `googleAppDownload` — reported success; the app was not installed.

`/task/detail` showed why for the download task:

```
Selector: desc:${AppName}      -> Invalid taskStep format
Selector: desc:Install         -> No element found
Selector: text:Continue        -> No element found
OCR: ApiKey: 123  Secret: 123  -> No element found
```

Three separate defects: it matches the install button only by
content-description (Play renders it as `text`), its OCR fallback ships with
placeholder credentials, and the flow definition itself emits
`Invalid taskStep format`. It also takes `appName` as a **text search string**,
so it can install a clone — "ChatGPT" has many impostors on the Play Store.

**Never trust a task status. Verify against the device:**

| Claim | Verification |
|---|---|
| account signed in | `dumpsys account` contains `name=<email>` |
| app installed | `pm list packages <pkg>` returns the package |

### Verification trap

`'com.google' in dumpsys account` is **true even with zero accounts** — it is
the registered authenticator type, not evidence of an account. Only
`name=...@...` entries count. This produced a false "signed in" reading and
sent the pipeline on to a Play Store that was not logged in.

## `code2fa` on `/rpa/task/googleLogin`

The docs describe it only as "2fa code", with no example, while the Browser API
calls the same concept `accountTOTPSecret` and shows a base32 value. Since the
task is schedulable, a live 6-digit code could not survive the delay — so the
secret was the likely intent, and that is **confirmed empirically: the raw
base32 TOTP secret works.**

The secret must be normalised first: Google displays it lowercase in groups of
four, base32 needs uppercase with no spaces and no padding.

## `/proxy/check` reports less than it appears to

`detectStatus` is trustworthy: the proxy either carried the request or it did
not, and a failure here should stop a row before a phone is created.

**`country` is not trustworthy.** Measured 2026-07-30 across four proxies from
two vendors: GeeLark returned no country for all four, while a public
geolocation service resolved every one of them to a real US ISP with
`hosting: false`. An empty `country` is a gap in GeeLark's lookup, not evidence
of a datacenter or freshly allocated address.

This correction matters because the prototype's notes drew the opposite
conclusion and treated an empty country as a warning sign of a dirty IP. It is
not a signal at all.

`outboundIP` is worth reading: when it differs from the host you dialled, the
proxy is a backconnect gateway and the exit address is what Google judges.

## Cost discipline

Billing is **per minute while a phone is running**, so:

1. every code path that starts a phone must stop it, including on crash and
   Ctrl+C;
2. never let a missing state file mean "create a new phone" — three orphan
   phones were created before that lesson landed;
3. keep a reaper that stops running phones nothing is accountable for.
