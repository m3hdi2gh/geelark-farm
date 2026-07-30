"""Run the pipeline for every pending row.  [phase 7]

Per account, in order:

    validate row -> check proxy -> create phone -> boot
    -> google_login -> verify against device
    -> play_install -> verify against device
    -> stop phone -> write status back to the sheet

Rules that shape the code:
- the proxy is checked before a phone is created, so a bad row costs nothing
- a phone is stopped in a finally block; a failure leaves it stopped too, with
  the artifacts (screen dump, screenshot) captured instead of a live device to
  poke at, because an unattended batch cannot leave phones billing
- one RPA task per phone at a time; concurrency is across phones, never within
- concurrency starts at 1 and is bounded by MAX_CONCURRENT_PHONES; the API
  rate limit is shared, so the limiter lives in api.py, not here
- each account gets its own log and artifact directory, and the run ends with
  a summary table: row, email, outcome, phone id, duration
"""
