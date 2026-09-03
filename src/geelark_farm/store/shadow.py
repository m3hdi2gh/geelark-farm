"""The reverse mirror: each pass, the sheet's state lands in the store.

The sheet stays authoritative - this direction exists so the web's read
paths (stage 3) never touch the Sheets quota. Everything here reads the
Book already in memory; a shadow that cost API calls would be a second
consumer of the 60/min budget the loop itself lives on.

Two shapes, two treatments:

**Resources are upserted by identity** - kind + lowercased address, or the
proxy triple - through the same unique indexes that refuse duplicates at
the door. `owner_id` is never touched by the mirror: assignment is born in
the store (stage 5), has no sheet twin, and a mirror that reset it would
un-assign somebody's phone every thirty seconds.

**Phones are upserted by live serial, and closed when they vanish.** A row
leaving the Phones tab means `done`/`failed` was carried out and the sheet
deleted it - the exact deletion that made "what did we build on Tuesday"
unanswerable there. Here it sets `done_at`, and the question keeps its
answer.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: The sheet's App column marks, as PhoneLog writes them. NULL stays NULL:
#: "nobody looked" survived one demotion incident already (2026-08-30) and
#: the mirror must not flatten it back into False.
_APP_MARKS = {"✓": True, "✗": False}


def write_shadow(conn, book, *, resources: bool = True) -> dict:
    """One pass's mirror, in one transaction. Returns what it did, for the
    pass event. Raises to the caller, who treats the store like the board:
    never fatal to the pass.

    `resources=False` once the pools live in the store (C2): the table is
    the pool then, and mirroring the sheet's stale picture over it would
    un-claim every row a build is holding, thirty seconds at a time."""
    did = {"resources": 0, "phones": 0, "closed": 0}
    with conn.cursor() as cur:
        if resources:
            for pool, kind in ((book.gmails, "gmail"),
                               (book.proxies, "proxy"), (book.apps, "app")):
                for row in pool._rows:
                    _upsert_resource(cur, kind, pool, row)
                    did["resources"] += 1
        live = _upsert_phones(cur, book)
        did["phones"] = len(live)
        cur.execute(
            "UPDATE phones SET done_at = now(), updated_at = now()"
            " WHERE done_at IS NULL AND NOT (serial = ANY(%s))", (live,))
        did["closed"] = cur.rowcount
    conn.commit()
    return did


def _upsert_resource(cur, kind: str, pool, row) -> None:
    values = row.values
    status = (values.get(pool.status_column) or "").strip()
    note = (values.get(pool.note_column) or "").strip()
    error = str(row.error) if row.error else None
    # The columns the pools keep beside a row - claim stamp, use count, the
    # serial an exit carries, the dates - ride along too, so the last mirror
    # before the C2 switch leaves the table holding everything the tab did.
    claimed_at = _when(values.get(pool.claimed_at_column)
                       if pool.claimed_at_column else "")
    if kind == "proxy":
        proxy = row.proxy
        if proxy is None:
            return                     # an unparseable row has no identity
        try:
            times_used = int((values.get("Times Used") or "0").strip() or 0)
        except ValueError:
            # The same reading ProxyPool._uses makes: a note typed into the
            # count column is "never used", not a failed mirror.
            log.warning("Proxy row %s: Times Used %r is not a number; "
                        "mirrored as 0", row.sheet_row,
                        values.get("Times Used"))
            times_used = 0
        cur.execute(
            "INSERT INTO resources AS r (kind, sheet_row, status, host, port,"
            " username, proxy_pass, proxy_name, last_exit_ip, note, error,"
            " serial, times_used, claimed_at)"
            " VALUES ('proxy', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s)"
            " ON CONFLICT (host, port, username) WHERE kind = 'proxy'"
            " DO UPDATE SET sheet_row = EXCLUDED.sheet_row,"
            "  status = EXCLUDED.status, proxy_name = EXCLUDED.proxy_name,"
            "  last_exit_ip = EXCLUDED.last_exit_ip, note = EXCLUDED.note,"
            "  error = EXCLUDED.error, serial = EXCLUDED.serial,"
            "  times_used = EXCLUDED.times_used,"
            "  claimed_at = EXCLUDED.claimed_at,"
            # Stamped only when something moved: this runs every pass, and
            # a stamp that moves every pass answers no page's "since when".
            "  updated_at = CASE WHEN (r.status, r.proxy_name,"
            "   r.last_exit_ip, r.note, r.error, r.serial, r.times_used,"
            "   r.claimed_at) IS DISTINCT FROM (EXCLUDED.status,"
            "   EXCLUDED.proxy_name, EXCLUDED.last_exit_ip, EXCLUDED.note,"
            "   EXCLUDED.error, EXCLUDED.serial, EXCLUDED.times_used,"
            "   EXCLUDED.claimed_at) THEN now() ELSE r.updated_at END",
            (row.sheet_row, status, proxy.host, proxy.port,
             proxy.username or "", proxy.password or "",
             (values.get("Name") or "").strip(),
             (values.get("Last Exit IP") or "").strip(), note, error,
             (values.get("Used By") or "").strip(), times_used, claimed_at))
        return
    creds = row.credentials
    address = (creds.email if creds else values.get("Address", "")).strip()
    if not address:
        return
    cur.execute(
        "INSERT INTO resources AS r (kind, sheet_row, status, address, password,"
        " totp_secret, email_code_only, recovery_email, seller, serial,"
        " note, error, claimed_at, used_at, purchased_on)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (kind, lower(address))"
        " WHERE kind IN ('gmail', 'app') AND address IS NOT NULL"
        " DO UPDATE SET sheet_row = EXCLUDED.sheet_row,"
        "  status = EXCLUDED.status, password = EXCLUDED.password,"
        "  totp_secret = EXCLUDED.totp_secret,"
        "  email_code_only = EXCLUDED.email_code_only,"
        "  recovery_email = EXCLUDED.recovery_email,"
        "  seller = EXCLUDED.seller, serial = EXCLUDED.serial,"
        "  note = EXCLUDED.note,"
        "  error = EXCLUDED.error, claimed_at = EXCLUDED.claimed_at,"
        "  used_at = EXCLUDED.used_at,"
        "  purchased_on = EXCLUDED.purchased_on,"
        "  updated_at = CASE WHEN (r.status, r.serial, r.note, r.error,"
        "   r.claimed_at, r.used_at, r.seller) IS DISTINCT FROM"
        "   (EXCLUDED.status, EXCLUDED.serial, EXCLUDED.note, EXCLUDED.error,"
        "   EXCLUDED.claimed_at, EXCLUDED.used_at, EXCLUDED.seller)"
        "   THEN now() ELSE r.updated_at END",
        (kind, row.sheet_row, status, address,
         creds.password if creds else "",
         creds.totp_secret if creds else "",
         bool(creds and creds.email_code_only),
         (creds.recovery_email if creds else "") or "",
         (values.get("Seller") or "").strip(),
         (values.get("Phone Serial") or "").strip(), note, error,
         claimed_at, (values.get("Used Date") or "").strip(),
         (values.get("Purchase Date") or "").strip()))


def _when(stamp: str | None):
    """A sheet claim stamp as something the timestamptz column takes, or
    None. Both spellings the pools ever wrote - with and without the Z -
    and anything else is 'no time recorded', never an error."""
    import time

    text = (stamp or "").strip()
    if not text:
        return None
    try:
        time.strptime(text.rstrip("Zz"), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return text.rstrip("Zz") + "+00"


def _upsert_phones(cur, book) -> list[str]:
    live: list[str] = []
    for _offset, cells in book.phones._typed_rows("the Phones tab"):
        serial = (cells.get("Serial") or "").strip()
        if not serial:
            continue
        live.append(serial)
        app_installed = _APP_MARKS.get((cells.get("App") or "").strip())
        cur.execute(
            "INSERT INTO phones AS p (serial, status, state, app_installed,"
            " gmail, app_account, proxy_name, tries, note)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (serial) WHERE done_at IS NULL"
            " DO UPDATE SET status = EXCLUDED.status,"
            "  state = EXCLUDED.state,"
            "  app_installed = EXCLUDED.app_installed,"
            "  gmail = EXCLUDED.gmail, app_account = EXCLUDED.app_account,"
            "  proxy_name = EXCLUDED.proxy_name, tries = EXCLUDED.tries,"
            "  note = EXCLUDED.note,"
            "  updated_at = CASE WHEN (p.status, p.state, p.app_installed,"
            "   p.gmail, p.app_account, p.proxy_name, p.tries, p.note)"
            "   IS DISTINCT FROM (EXCLUDED.status, EXCLUDED.state,"
            "   EXCLUDED.app_installed, EXCLUDED.gmail, EXCLUDED.app_account,"
            "   EXCLUDED.proxy_name, EXCLUDED.tries, EXCLUDED.note)"
            "   THEN now() ELSE p.updated_at END",
            (serial, (cells.get("Status") or "").strip(),
             _state_word(cells.get("State")), app_installed,
             book.phones.said(cells.get("Gmail", "")),
             book.phones.said(cells.get("GPT Account", "")),
             (cells.get("Proxy") or "").strip(),
             book.phones.tries(cells),
             (cells.get("Note") or "").strip()))
    return live


def _state_word(raw: str | None) -> str:
    """The sheet's free-text State, fitted to the schema's CHECK.

    A word the schema does not know - `dome`, the typo that was silently
    nothing in the sheet - mirrors as '' rather than failing the whole
    pass, and the sheet remains the place such a word is visible and fixed.
    """
    word = (raw or "").strip().casefold()
    return word if word in ("", "unused", "taken", "done", "failed") else ""
