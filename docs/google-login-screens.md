# Google login screens

The catalogue behind the screen router in `flows/google_login.py`. Every screen
the flow can meet is listed here with how it is recognised and what is done
about it. Filled in during phase 4, from screens actually observed — a screen
gets an entry only once it has been seen, with a fixture to prove it.

Each entry should carry: the recognising text, the action, the outcome kind,
and the fixture file under `tests/fixtures/`.

## Observed so far (prototype, 2026-07-29)

### 2-Step Verification — push to another device
- **Recognised by**: "2-Step Verification" plus "Check your \<device>", with
  "Try another way" present.
- **Action**: tap "Try another way", choose the authenticator option, focus the
  `EditText` (it has no label, so find it by class in the raw XML), type a
  fresh TOTP code, submit.
- **Kind**: continue.
- **Note**: the "other device" Google names is usually a cloud phone from an
  earlier run — the account remembers it. This is the failure mode that made a
  hand-written flow necessary: GeeLark's RPA cannot reach "Try another way".

### Play Store setup chain (post-install-tap)
- "Complete account setup" / "Review your account to continue installing apps"
  → **Continue**
- "Add a payment option to complete your account" → **Skip**
- "Try Google Play Pass free for 1 month" → **Not now**
- **Kind**: continue. Account-level: once cleared it does not reappear, so
  later phones on the same account skip straight to the download.

### Consent screens seen in RPA traces
Selectors the prebuilt flow attempted, i.e. screens Google is known to show:
"I understand", "I agree", "ACCEPT", "Skip", "DON'T TURN ON" (backup prompt).
Each needs its own verified entry before it can be trusted.

## To be catalogued in phase 4

Expected but not yet captured. Listed so the fatal ones are recognised rather
than waited out:

| Screen | Expected kind |
|---|---|
| Email entry | continue |
| Password entry | continue |
| TOTP code entry (direct, no push) | continue |
| "Verify it's you" — recovery email or phone | fatal or needs a sheet column |
| SMS / phone-number verification demand | fatal: `phone_verification_required` |
| CAPTCHA / "Confirm you're not a robot" | fatal: `captcha_shown` |
| "Couldn't sign you in" | fatal: `sign_in_refused` |
| "Wrong password" | fatal: `wrong_password` |
| Account disabled or locked | fatal: `account_disabled` |
| Play Protect prompt | continue |
| Google services / backup prompts | continue |

A fatal outcome is not a defect in the flow. `captcha_shown` on a fresh
datacenter IP is the IP's fault, and the correct behaviour is to name it and
move to the next row.
