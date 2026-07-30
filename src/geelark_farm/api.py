"""Signed HTTP transport for the GeeLark open API.  [phase 1]

Responsibility:
- build the auth headers (appId / traceId / ts / nonce / sign) for every call,
  where sign = SHA256(appId + traceId + ts + nonce + apiKey) as uppercase hex
- enforce one central rate limit for the whole process (GeeLark bans a key for
  2 hours past 200 req/min, and every concurrent worker shares that budget)
- retry transient transport failures (read timeouts have been observed) while
  never retrying a call that already changed state
- raise ApiError on a non-zero response code, so callers never inspect
  envelopes themselves

Deliberately knows nothing about phones, screens or accounts.
See docs/geelark-api.md for the endpoint quirks this must accommodate.
"""
