"""geelark-farm: provision GeeLark cloud phones from a spreadsheet.

A phone out, built from stock: take a free exit from the Proxy tab, create a
phone behind it, sign in the first usable Gmail, install the target app, sign
in an app account, verify each step against the device rather than against the
screen, then stop the phone to end billing.

Stock rather than rows, and the distinction is the whole design. A bad Gmail
costs itself and the next one is tried on the same phone; it does not cost the
phone that was created for it.

Layering (module-level imports point downward only):

    cli / ui                 what to do, in what order
    builder                  one phone, end to end, and the sheet sync
    pools / ledger           the sheet's stock, the local record of phones
    flows/                   multi-screen procedures (login, install)
    phones / screen / shell  make the device, see it, act on it
    api / gsheet / config    signed transport, sheet transport, settings

`failures.py` sits beside all of it: one table saying what each reason means
and who it blames, which is what decides whether a failure costs the
credential, the exit, or the phone.

One call goes upward - `pools.Book.sync_lists` reads the taxonomy through
`builder` - and it is imported inside the method so the cycle never exists at
import time.

The README explains why that split exists; docs/ holds the vendor API notes
and the runbook.
"""

__version__ = "0.1.0"
