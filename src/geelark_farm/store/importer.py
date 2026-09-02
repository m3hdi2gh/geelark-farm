"""The sheet as an input funnel: new rows flow into the store, once.

With the pools living in Postgres, the sheet's three stock tabs keep one
job - the place the friend pastes what he bought - and lose the other:
nothing reads status from them any more. Each pass this takes every row
that is still blank there, validates it the way the web form would, puts
it in the store, and writes `imported` into the sheet row so the person
who pasted it sees it was taken in and nothing takes it in twice.

Idempotent by construction: the store's unique indexes refuse a second
copy, and a row the store already knows is marked `imported` all the same,
so a run that died between the insert and the mark converges next pass.
"""

from __future__ import annotations

import logging
import time

from . import validate

log = logging.getLogger(__name__)

IMPORTED = "imported"
#: How a refused row's note begins - and the guard that keeps a broken row
#: from being re-noted every pass, which would be one write a pass per
#: bad row against the sheet's sixty a minute.
REFUSED = "Not imported:"


def pull(sheet_pools, table) -> dict[str, int]:
    """Drain the sheet's free rows into `table`. Returns counts per kind."""
    counts = {"gmail": 0, "proxy": 0, "app": 0, "refused": 0}
    today = time.strftime("%Y-%m-%d")
    for pool in sheet_pools:
        kind = _kind_of(pool)
        # A row the sheet pool could not read is not `available`, so it
        # would never be seen here - and the person who pasted it would
        # see a blank status and nothing else. Said once, in the row: the
        # web form would have refused it on the spot, and the sheet cannot.
        for resource in pool.broken:
            note = (resource.values.get(pool.note_column) or "")
            if not note.startswith(REFUSED):
                pool._set(resource, {
                    pool.note_column: f"{REFUSED} {resource.error}"})
                counts["refused"] += 1
        for resource in list(pool.available):
            try:
                row = _row_for(kind, resource)
            except (validate.AccountError, validate.ProxyError) as exc:
                log.warning("%s row %d was not imported: %s", pool.tab,
                            resource.sheet_row, exc)
                pool._set(resource, {pool.note_column: f"{REFUSED} {exc}"})
                counts["refused"] += 1
                continue
            new_id = table.insert(row)
            pool._set(resource, {
                pool.status_column: IMPORTED,
                pool.note_column: (
                    f"Imported into the store on {today}."
                    if new_id is not None else
                    f"Already in the store - marked on {today}.")})
            if new_id is not None:
                counts[kind] += 1
    took = {k: v for k, v in counts.items() if v}
    if took:
        log.info("imported from the sheet: %s", took)
    return counts


def _kind_of(pool) -> str:
    return {"Gmails": "gmail", "Proxy": "proxy", "Gpt Info": "app"}[pool.tab]


def _row_for(kind: str, resource) -> dict:
    values = resource.values
    if kind == "gmail":
        row = validate.gmail_row(
            address=values.get("Address", ""),
            password=values.get("Password", ""),
            secret=values.get("Secret", ""),
            seller=values.get("Seller", ""))
        row["purchased_on"] = (values.get("Purchase Date") or "").strip()
    elif kind == "app":
        row = validate.app_row(
            address=values.get("Address", ""),
            password=values.get("Password", ""),
            secret=values.get("2FA Secret", ""),
            email_code_only=(values.get("Email code") or "")
            .strip().upper() == "TRUE")
    else:
        raw = values.get("Proxy String", "") or ":".join(
            p for p in (values.get("Host", ""), values.get("Port", ""),
                        values.get("Username", ""),
                        values.get("Password", "")) if p)
        row = validate.proxy_row(raw=raw, name=values.get("Name", ""))
    row["source"] = "sheet"
    row["sheet_row"] = resource.sheet_row
    return row
