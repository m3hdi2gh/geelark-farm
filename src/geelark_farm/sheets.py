"""The spreadsheet: input rows in, status out.  [phase 6]

Responsibility:
- read the account rows (proxy, email, password, totp_secret) via a service
  account, so no interactive OAuth is needed for an unattended run
- validate every row BEFORE anything is spent: parseable proxy, well-formed
  base32 secret, no duplicate address
- write results back: status, phone_id, serial, note, updated_at

The sheet is also the state store. `status` is the resume mechanism:

    pending          not attempted
    running          claimed by a run (stale ones are recoverable)
    done             phone ready - skipped on re-runs
    failed:<reason>  named failure, e.g. failed:captcha_shown

Re-running the tool must therefore be safe and idempotent: rows marked done
are never touched again.
"""
