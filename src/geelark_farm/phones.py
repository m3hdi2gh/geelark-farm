"""Phone lifecycle, and the ledger that keeps it accountable.  [phase 3]

Responsibility:
- create a phone bound to a given proxy at creation time, so the device never
  touches the network unproxied
- boot and wait for status 0 (running), stop, delete, list
- record every phone in a local ledger the moment it is created - before
  anything else can fail - so a crash can never orphan a phone silently
- reap: find phones that are running but not accounted for, and stop them

Billing is per running minute. Any code path that starts a phone owns the
obligation to stop it; `reap` is the backstop for when that fails.
"""
