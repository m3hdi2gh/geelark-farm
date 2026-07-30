"""Multi-screen procedures driven against the real UI.

A flow is a loop, not a script: look at the screen, decide what it is, act,
look again. Google does not present its login screens in a fixed order, and a
linear script breaks the first time an extra interstitial appears.

Each flow reports a typed outcome with a named reason, so a failure is
actionable ("captcha_shown", "account_disabled") rather than a timeout.
"""
