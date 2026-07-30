"""Sign a Google account into the device, handling every screen Google shows.
[phase 4 - the core of the project]

Written by hand rather than using GeeLark's googleLogin RPA task, because that
task cannot be extended: it fails to reach "Try another way", ships with
placeholder OCR credentials, and reports success while the device is stranded
on a verification screen.

Design - a screen router:

    while not terminal:
        rows = screen.capture()
        handler = registry.match(rows)      # first matching known screen
        handler.act(device, account)        # type, tap, or resolve

Each entry in the registry is one screen: a name, a match predicate, an
action, and a kind (continue / success / fatal). Adding support for a newly
observed Google screen means adding one entry - never rewriting the loop.

Terminal conditions:
- success: the expected address appears in `dumpsys account` (device truth,
  not screen text)
- fatal, named: captcha, account disabled, phone-number demand, wrong password
- budget exhausted: archive the XML and a screenshot, report "unknown_screen"
  so the screen can be catalogued and handled next time

Every unrecognised screen is saved to artifacts/ and belongs in
docs/google-login-screens.md.
"""
