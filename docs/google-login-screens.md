# Google login screens

The catalogue behind the screen router in `flows/google_login.py`. Every page
the flow can meet gets an entry here: how it is recognised, what is done about
it, and whether it is terminal.

A page earns an entry only once it has actually been observed, with a captured
hierarchy to prove it. Guessed selectors are how GeeLark's own flow failed.

## How the router uses this

`SCREENS` is an ordered list; the first entry whose predicate matches the parsed
elements wins. Order therefore encodes priority:

1. **fatal** — checked first, so a dead end is never mistaken for a page to
   dismiss.
2. **specific pages** — 2FA push, method list, code entry, password, email,
   account picker.
3. **dismissable** — the catch-all for consent and marketing pages, last so it
   never swallows a page with real work to do.

Three outcome kinds:

| Kind | Meaning |
|---|---|
| `success` | the expected address is in `dumpsys account` — device truth only |
| `fatal` | terminal and named; the row cannot proceed unattended |
| `unknown` | nothing matched, or a screen repeated without progress; XML archived |
| `budget` | no result in time; screens seen are reported |

## Recognition

Predicates match against a casefolded blob of every `text` and `content-desc`
on screen, **after** `screen.normalize()` folds typographic punctuation to
ASCII. This is not cosmetic: Google writes "Couldn't sign you in" with U+2019,
so a selector typed with an ASCII apostrophe does not match it. That bug turned
a correctly-named failure into `unknown_screen` on the first real run (fixed
2026-07-30). Non-breaking spaces appear too, in strings like "Google Play Pass".
Selectors are therefore written in plain ASCII everywhere.

Beyond text, predicates use structural facts that text cannot express:

- a **password field** is `password="true"` in the hierarchy, which is far more
  reliable than looking for the word "password";
- an **empty input** has no text or content-desc at all, so it is found by class
  (`EDITABLE_CLASSES`) — and not every editable is an `EditText`, as a Settings
  search box proved by being an `AutoCompleteTextView`.

## Entries

### fatal — terminal, named
Matched by substring against `FATAL_TEXTS`. Grouped by the reason reported:

| Reason | Recognised by | Why it is terminal |
|---|---|---|
| `captcha_shown` | "confirm you're not a robot", "type the text you hear or see" | needs a human; the real fix is a cleaner exit IP |
| `wrong_password` | "wrong password", "incorrect password" | the row is wrong; retrying burns reputation |
| `verification_blocked` | "didn't provide enough info", "use a device where you've signed in before" | Google's risk check — see below |
| `account_disabled` | "account has been disabled/locked" | nothing to automate |
| `sign_in_refused` | "couldn't sign you in" | declined without a stated reason |
| `phone_verification_required` | "verify your phone number", "get a verification code at" | needs a number the tool does not have |
| `email_not_found` | "couldn't find your google account" | the row is wrong |
| `too_many_attempts` | "too many failed attempts" | stop, do not retry |

#### verification_blocked — observed 2026-07-30, run 1

The wall the first real run hit, after the router had correctly handled four
screens in a row. Fixture: `tests/fixtures/google-verification-blocked.xml`.

> Couldn't sign you in — You didn't provide enough info for Google to be sure
> this account is really yours. … Use a device where you've signed in before ·
> Use a familiar Wi-Fi network

Reached immediately after tapping "Try another way": Google had pushed the second
factor to a device the account already trusted, and when offered the choice it
declined to accept an authenticator code from a brand-new device on an
unfamiliar network.

This is not a defect in the flow, and there is no selector that fixes it. It is
account provenance: an account whose only history is on someone else's device
and IP is exactly what Google's risk engine is built to stop. The page also
offers "TRY AGAIN", which is why `fatal` is checked before `dismissable` — a
router that treated this as a page to dismiss would loop until its budget ran
out and learn nothing.

### 2fa_push_to_other_device
- **Recognised by**: "try another way", with "check your \<device>" / "tap yes" /
  "2-step verification".
- **Action**: tap "Try another way".
- **Observed 2026-07-29** in the prototype. Google pushes a prompt to a device
  the account already trusts instead of asking for a code — and the trusted
  device is usually a cloud phone from an earlier run, so this gets *more*
  likely as the project is used. GeeLark's RPA cannot reach this button, which
  is the single clearest reason the flow is hand-written.

### 2fa_method_list
- **Recognised by**: "choose how you want to sign in" / "other ways to verify".
- **Action**: choose the authenticator option.
- **Fatal if absent** (`no_authenticator_option`): every other factor needs a
  human or a phone number.

### 2fa_code_entry
- **Recognised by**: "authenticator" / "verification code" / "2-step", plus an
  input field present.
- **Action**: generate a TOTP code with at least 8 seconds of life left, type
  it, submit. The life check matters — a code that expires between typing and
  submitting reads to Google as a wrong code.

### password_entry
- **Recognised by**: an input field with `password="true"`.
- **Action**: type the password, submit.

### email_entry
- **Recognised by**: "sign in" / "email or phone", plus a non-password input.
- **Action**: type the address, submit.

### add_account_picker
- **Recognised by**: "add an account", with a "Google" entry.
- **Action**: tap Google. Reached by
  `am start -a android.settings.ADD_ACCOUNT_SETTINGS --esa account_types
  com.google`, which lands on the right page without navigating menus whose
  layout varies by Android skin.

### dismissable — the catch-all
- **Recognised by**: any of `DISMISS_LABELS` being present and clickable.
- **Action**: tap the highest-priority one.
- Covers consent and marketing pages, which are interchangeable: each exists
  only to be dismissed. Labels seen in RPA traces and prototype runs include
  "I agree", "ACCEPT", "I understand", "DON'T TURN ON" (backup prompt), "Not
  now", "Skip", "More".
- Allowed more visits than other screens (8), because a chain of them in a row
  is normal rather than a sign of being stuck.

## Loop protection

Every screen has a `max_visits`. Handling the same page more times than that
means the action is not having the effect it assumes, so the flow stops with
`stuck_on_<screen>` and archives the hierarchy. Without this a router will
happily tap the same button until its budget runs out and report nothing useful.

An unmatched screen is retried twice before being declared unknown, because a
dump taken mid-animation legitimately matches nothing.
