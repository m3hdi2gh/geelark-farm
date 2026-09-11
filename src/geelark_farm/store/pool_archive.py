"""Taking a pool row out of stock without destroying it.

The purge deletes: a spent or refused Gmail leaves nothing behind but a
line in `events`, and the address, the password and the authenticator key
are gone for good. For a pool the operator pays more for than for the
phones and the exits together that is the wrong default - "archive them"
(2026-09-11) - so this moves the row instead.

Every column of it goes into `resources_archive` as json and the row
leaves `resources`. To the pools, the console and the builder that is
exactly a purge: the row is not stock, cannot be claimed, and no reader
needs a new `WHERE` clause to keep it out. To a person it is still there
to read, count, and - `restore` - put back.

Both verbs are safe to run twice. `archive` copies before it deletes, in
one statement, and only deletes the ids that landed in the archive;
`restore` writes the row back under whatever columns `resources` has
today, and leaves the archived copy alone unless the row really went
back.
"""

from __future__ import annotations

import logging

from ..config import Settings
from .db import connect

log = logging.getLogger(__name__)

#: What `restore` refuses to carry back: the verdict a run left, the phone
#: it left it on, and the ladder's wait. A row put back is stock again -
#: if it is put back still saying `captcha_shown`, the pool reads it as
#: flagged and nothing will ever claim it. `tries` is deliberately not
#: here: a row that already spent the ladder comes back for one more try,
#: not for three, and the count is the only record that it did.
FRESH = {"status": "", "serial": "", "claimed_at": None, "error": None,
         "retry_after": None, "used_at": ""}


def archive(settings: Settings, ids, *, by: str = "") -> list[dict]:
    """Move these `resources` rows into the archive.

    Returns the rows that moved, as {id, address}. A row already archived
    (or already gone) is not in the answer and is not deleted.
    """
    wanted = sorted({int(i) for i in ids})
    if not wanted:
        return []
    with connect(settings) as conn:
        cur = conn.execute(
            "WITH copied AS ("
            "  INSERT INTO resources_archive"
            "    (id, kind, address, status, seller, payload, archived_by)"
            "  SELECT r.id, r.kind, coalesce(r.address, ''),"
            "         coalesce(r.status, ''), coalesce(r.seller, ''),"
            "         to_jsonb(r), %s"
            "    FROM resources r WHERE r.id = ANY(%s)"
            "  ON CONFLICT (id) DO NOTHING"
            "  RETURNING id)"
            " DELETE FROM resources"
            "  WHERE id IN (SELECT id FROM copied)"
            "  RETURNING id, coalesce(address, '') AS address",
            (str(by)[:80], wanted))
        moved = [{"id": int(r[0]), "address": str(r[1])}
                 for r in cur.fetchall()]
        conn.commit()
    if moved:
        log.info("archived %d pool row(s) of the %d asked for, by %s",
                 len(moved), len(wanted), by or "nobody named")
    return moved


def restore(settings: Settings, ids) -> list[dict]:
    """Put archived rows back in the pool as free stock.

    The payload is written back through the columns `resources` has now,
    so a row archived before a column existed still goes back; an address
    that is in the pool again under a new id stays archived and is
    reported, rather than raising on the unique index.
    """
    wanted = sorted({int(i) for i in ids})
    if not wanted:
        return []
    back: list[dict] = []
    with connect(settings) as conn:
        live = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema()"
            "   AND table_name = 'resources'").fetchall()}
        rows = conn.execute(
            "SELECT id, payload FROM resources_archive WHERE id = ANY(%s)"
            " ORDER BY id", (wanted,)).fetchall()
        for row_id, payload in rows:
            fields = {k: v for k, v in dict(payload or {}).items()
                      if k in live and k != "id"}
            fields.update({k: v for k, v in FRESH.items() if k in live})
            columns = sorted(fields)
            cur = conn.execute(
                f"INSERT INTO resources ({', '.join(columns)})"
                f" VALUES ({', '.join(['%s'] * len(columns))})"
                f" ON CONFLICT DO NOTHING RETURNING id, coalesce(address, '')",
                [fields[c] for c in columns])
            got = cur.fetchone()
            if got is None:
                log.info("archived row %s is in the pool again already; "
                         "left in the archive", row_id)
                continue
            conn.execute("DELETE FROM resources_archive WHERE id = %s",
                         (int(row_id),))
            back.append({"id": int(got[0]), "address": str(got[1])})
        conn.commit()
    return back


def counts(settings: Settings) -> list[dict]:
    """How much is archived, by kind and by the status it left with."""
    with connect(settings) as conn:
        cur = conn.execute(
            "SELECT kind, status, count(*) AS c FROM resources_archive"
            " GROUP BY kind, status ORDER BY kind, c DESC")
        out = [{"kind": r[0], "status": r[1], "count": int(r[2])}
               for r in cur.fetchall()]
        conn.rollback()
    return out


def listing(settings: Settings, kind: str = "", limit: int = 200) -> list[dict]:
    """The newest archived rows, for looking one up by hand."""
    with connect(settings) as conn:
        cur = conn.execute(
            "SELECT id, kind, address, status, seller, archived_at,"
            "       archived_by"
            "  FROM resources_archive"
            " WHERE (%s = '' OR kind = %s)"
            " ORDER BY archived_at DESC, id DESC LIMIT %s",
            (str(kind), str(kind), max(1, int(limit))))
        names = [d.name for d in cur.description]
        out = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        conn.rollback()
    return out
