"""What one page of the console is allowed to weigh.

The dashboard reached 1,012,694 bytes and 15,273 elements with nothing
in the suite turning red, because nothing in the suite had ever looked.
Of that, 925,488 bytes were the pool-manager overlay, rendered `hidden`
on every single response whether or not anybody had opened it - and with
~500 passwords and TOTP secrets in its `data-*` attributes.

These are budgets, not measurements: they are set above where the page
sits today and below where it hurt, and they are asserted at the volume
the farm actually runs at, not at the four rows a unit test uses. A page
that grows past one of them is a page somebody has to think about.
"""
from __future__ import annotations

import re

from geelark_farm.web import pages

USER = {"id": 1, "role": "admin", "csrf": "c", "mutations": True,
        "is_admin": True, "may_login_accounts": True, "may_add_gmail": True,
        "may_add_gpt": True, "sees": "all", "username": "mehdi",
        "user_admin": True, "nav": {}}

#: What the farm holds, near enough. 512 Gmails is what was in the pool
#: on the day this was written; the rest are that day's counts too.
LIVE = {"gmail": 512, "gpt": 42, "spotify": 6, "proxy": 91}


def big_farm(scale: int = 1) -> dict:
    """A dashboard's read at production volume.

    `scale` multiplies it, for asking what the page does as the pool
    grows - the question nobody had asked when it reached a megabyte.
    """
    rows: dict[str, list[dict]] = {}
    for kind, many in LIVE.items():
        made = []
        for i in range(many * scale):
            state = "free" if i % 3 else "on a phone"
            row = {"id": i + 1, "address": f"row{i:05d}@gmail.com",
                   "status": "" if state == "free" else "in_use",
                   "state": state, "serial": "" if state == "free" else "3800",
                   "note": "", "error": None, "seller": "LEO 18SEP",
                   "updated_at": "2026-09-20 10:00:00+00",
                   "password": "Hienluong102@",
                   "secret": "A66OUCIDONRH2WL2EYN3P24MA3J47OKU",
                   "second": "authenticator", "category": "normal",
                   "product": "spotify" if kind == "spotify" else "",
                   "credential_kind": "", "panel_ref": "",
                   "customer_ready": False,
                   "host": f"38.154.{i % 250}.11", "port": 6540 + (i % 99),
                   "exit_ip": f"38.154.{i % 250}.11", "times_used": i % 7}
            made.append(row)
        rows[kind] = made
    phones = [{"serial": str(3800 + i), "status": "ready", "state": "",
               "gmail": f"row{i:05d}@gmail.com", "app_account": "",
               "app_product": "", "app_category": "", "proxy_name": f"US{i}",
               "updated_at": "2026-09-20 10:00:00+00"}
              for i in range(12 * scale)]
    free = {k: sum(1 for r in v if r["state"] == "free")
            for k, v in rows.items()}
    return {
        "pool_rows": rows, "phones": phones,
        "stock": {"gmail": {"free": free["gmail"], "on_phones": 5, "used": 9},
                  "proxy": {"free": free["proxy"], "on_phones": 4, "dead": 2},
                  "app": {"awaiting": free["gpt"], "panel": 2, "manual": 3}},
        "spotify": {"normal": free["spotify"], "error": 0},
        "wishes": [], "stopped": [], "queue": {}, "pulse": {},
        "geelark": {}, "signins": {},
        "choose": {"gmails": [], "proxies": [], "apps": []},
    }


def drawn(scale: int = 1) -> str:
    return pages.dashboard(big_farm(scale), USER)


def test_the_dashboard_fits_in_its_budget():
    """1,012,694 bytes on 2026-09-20, of which 91% was a hidden drawer."""
    body = drawn()
    assert len(body) < 300_000, (
        f"the dashboard is {len(body):,} bytes at production volume")
    assert body.count("<tr") < 900, (
        f"{body.count('<tr')} rows drawn on a page showing twelve phones")
    assert body.count("<") < 6_000, f"{body.count('<')} elements"


def test_no_credential_is_on_the_dashboard():
    """~500 passwords and TOTP secrets crossed the wire on every render,
    in the overlay's data attributes, whether or not anybody had opened
    it (2026-09-20)."""
    body = drawn()
    for leak in ("data-password", "data-secret", "Hienluong102@",
                 "A66OUCIDONRH2WL2EYN3P24MA3J47OKU"):
        assert leak not in body, f"{leak} is on the dashboard"


def test_the_manager_is_not_rendered_until_it_is_opened():
    """It is a drawer. Drawing it shut on every response is drawing it
    for the 99 responses in 100 where nobody opens it."""
    body = drawn()
    assert 'id="poolov"' in body, "the overlay's mount is gone"
    for kind in ("gmail", "gpt", "spotify", "proxy"):
        assert f'data-sheet="{kind}"' not in body, (
            f"the {kind} sheet is drawn shut on every response again")
    # What stays: the two sheets that are about phones, not about stock,
    # and both are small and already on the page's own data.
    assert 'data-sheet="phone"' in body


def test_the_page_holds_at_twice_the_pool():
    """The question nobody asked as the pool went from 45 rows to 512.

    The page does still grow, and on purpose: each rail card lists every
    FREE row under its count, because "a list that stopped at four made
    the fifth look like it did not exist" (the operator, 2026-09-05). At
    512 Gmails that is ~340 list items in a box 122px tall. It is a
    deliberate cost and it is the only one left - everything held, spent
    or credentialled has gone - so what this holds is the ceiling, not
    the slope. If the pool reaches four thousand, this is the test that
    says so and the decision is the operator's.
    """
    one, two = len(drawn(1)), len(drawn(2))
    assert two < 300_000, (
        f"{two:,} bytes at twice the pool - past the budget")
    # And what grows is the free list and nothing else: every extra byte
    # should be a <li>, near enough.
    extra_rows = drawn(2).count("<li") - drawn(1).count("<li")
    assert (two - one) < extra_rows * 220, (
        f"{two - one:,} more bytes for {extra_rows} more free rows - "
        f"something other than the list is growing with the pool")
