"""geelark-farm: provision GeeLark cloud phones from a spreadsheet.

One row in, one ready phone out: create behind the row's proxy, sign into the
row's Google account, install the target app, verify both against the device,
then stop the phone to end billing.

Layering (dependencies point downward only):

    cli / orchestrator      what to do, in what order, for which rows
    flows/                  multi-screen procedures (login, install)
    screen / shell          see the device, act on the device
    api / config            signed transport, settings

See docs/architecture.md for the reasoning behind that split.
"""

__version__ = "0.1.0"
