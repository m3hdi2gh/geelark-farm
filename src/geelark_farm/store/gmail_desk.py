"""The Gmails page's own writes: a person's hand on the Gmail pool.

Every change is one guarded statement per Gmail. The guard is the place
the Gmail stands in *now* - the same places the page draws - so a press
made on a page drawn a minute ago cannot move a Gmail a build has taken
since: a row that is no longer where the page saw it is left alone and
said so, never forced.

The places, in the page's words:

- on a phone: `in_use` (a build has it) or `ready` (signed in on one);
- spent: `used`;
- set aside by a person: `set_aside`;
- free: blank, off the refund list, readable;
- waiting: refused with a distrust reason and back in the queue at
  `retry_after` (store.ladder);
- stopped: everything else that is unused - its tries spent, a reason no
  retry mends, or on the seller's list.

Free and waiting are *in the queue*; stopped and set aside are *out of
it*. Whatever is not on a phone and not spent is *unused*, and only an
unused Gmail can be switched, kept for a product or marked fixed. Remove
takes a spent one too: only a Gmail a phone is behind stays where it is.

Each change answers what it changed, row by row, with the row's
`updated_at` after it. That stamp is how the page's Undo (`revert`) knows
nothing else has touched the row since: every writer of `resources`
moves it - the claim, the ladder, the console's editor - so a row whose
stamp is still the one this change left is a row Undo may put back, and
any other is left as it is now (the Gmails page, 2026-10-07).
"""

from __future__ import annotations

import datetime
import logging
import re
import time
from zoneinfo import ZoneInfo

from .. import failures
from ..config import Settings
from .db import connect

log = logging.getLogger(__name__)

TEHRAN = ZoneInfo("Asia/Tehran")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec")
#: The lanes a Gmail can be kept for; blank is any product (purposes.py).
LANES = ("gpt", "spotify")
LANE_WORD = {"gpt": "GPT", "spotify": "Spotify"}
#: How many Gmails one press may move.
MOST = 2000

_DISTRUST = sorted(failures.DISTRUST)
#: Not on a phone and not spent.
UNUSED = "lower(r.status) NOT IN ('in_use', 'ready', 'used')"
FREE = ("r.status = '' AND coalesce(r.refund_state, '') = ''"
        " AND r.error IS NULL")
WAITING = ("r.status = ANY(%(distrust)s) AND r.retry_after IS NOT NULL"
           " AND coalesce(r.refund_state, '') = '' AND r.error IS NULL")
IN_PLAY = f"(({FREE}) OR ({WAITING}))"
OUT = f"({UNUSED} AND NOT {IN_PLAY})"
#: Refused by Google since it was last marked fixed - or ever, if never.
REFUSED_SINCE = ("EXISTS (SELECT 1 FROM signins s"
                 " WHERE s.gmail = lower(r.address) AND NOT s.ok"
                 "   AND (r.fixed_at IS NULL OR s.at > r.fixed_at))")
#: What Mark as fixed takes: unused, readable, and out of the queue or
#: refused since its last fix. A row the farm cannot read is put right by
#: an edit (`save`), which judges its details again; turned on or marked
#: fixed as it is, nothing would take it.
MENDABLE = f"({UNUSED} AND r.error IS NULL AND ({OUT} OR {REFUSED_SINCE}))"
#: What the switch puts back in the queue: out of it, and readable.
QUEUEABLE = f"({OUT} AND r.error IS NULL)"
#: What Remove takes: any Gmail no phone is behind - unused, or spent (its
#: phone is gone), as the old manager's Remove all spent took them. The
#: archive keeps each whole, so Undo puts it back, and a paste of a spent
#: one is still refused.
REMOVABLE = "lower(r.status) NOT IN ('in_use', 'ready')"

#: The fields a change may write, and what each is in the table - so a
#: revert binds every value to its own type.
_TYPES = {"status": "text", "serial": "text", "retry_after": "timestamptz",
          "tries": "integer", "last_reason": "text", "last_host": "text",
          "refund_state": "text", "refund_at": "timestamptz",
          "fixed_at": "timestamptz", "fixed_by": "text", "note": "text",
          "purpose": "text", "password": "text", "totp_secret": "text",
          "recovery_email": "text", "error": "text"}
_MAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Refused(ValueError):
    """A change the farm will not make, in words for the person."""


def today() -> datetime.date:
    return datetime.datetime.now(TEHRAN).date()


def day_word(day: datetime.date | None = None) -> str:
    day = day or today()
    return f"{day.day} {MONTHS[day.month - 1]}"


def _ids(ids) -> list[int]:
    out = sorted({int(i) for i in ids or () if str(i).strip().isdecimal()})
    if len(out) > MOST:
        raise Refused(f"At most {MOST} Gmails at a time.")
    return out


def _stamp(value) -> str:
    """A value as the change remembers it: timestamps in full, so Undo
    compares the very instant."""
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return value


def _change(conn, ids: list[int], *, where: str, sets: dict,
            fields: tuple[str, ...], params: dict | None = None) -> list[dict]:
    """One UPDATE over `ids` that still meet `where`: each changed row as
    {id, address, before, after, ver}. `sets` maps a column to its SQL;
    `fields` are the columns remembered before and after."""
    if not ids:
        return []
    params = dict(params or {}, ids=ids, distrust=_DISTRUST)
    cols = ", ".join(f"old.{f} AS b_{f}" for f in fields)
    new = ", ".join(f"r.{f} AS a_{f}" for f in fields)
    assign = ", ".join(f"{c} = {v}" for c, v in sets.items())
    cur = conn.execute(
        f"WITH old AS (SELECT r.* FROM resources r"
        f"  WHERE r.kind = 'gmail' AND r.id = ANY(%(ids)s) AND {where}"
        f"  FOR UPDATE)"
        f" UPDATE resources r SET {assign}, updated_at = now()"
        f"  FROM old WHERE r.id = old.id"
        f" RETURNING r.id, coalesce(r.address, '') AS address, {cols}, {new},"
        f"  r.updated_at", params)
    names = [d.name for d in cur.description]
    out = []
    for row in cur.fetchall():
        got = dict(zip(names, row, strict=True))
        out.append({"id": int(got["id"]), "address": str(got["address"]),
                    "before": {f: _stamp(got[f"b_{f}"]) for f in fields},
                    "after": {f: _stamp(got[f"a_{f}"]) for f in fields},
                    "ver": _stamp(got["updated_at"])})
    return out


def _left(conn, ids: list[int], done: list[dict]) -> dict[int, str]:
    """Why each id that did not change did not: where it stands now."""
    moved = {c["id"] for c in done}
    rest = [i for i in ids if i not in moved]
    if not rest:
        return {}
    cur = conn.execute(
        "SELECT r.id, lower(r.status) AS status, r.retry_after IS NOT NULL AS waits,"
        " coalesce(r.refund_state, '') AS refund, r.error IS NOT NULL AS broken"
        " FROM resources r WHERE r.kind = 'gmail' AND r.id = ANY(%s)", (rest,))
    seen = {int(r[0]): r for r in cur.fetchall()}
    out = {}
    for i in rest:
        row = seen.get(i)
        if row is None:
            out[i] = "gone"
        elif row[1] in ("in_use", "ready"):
            out[i] = "phone"
        elif row[1] == "used":
            out[i] = "spent"
        elif row[4]:
            out[i] = "unreadable"
        else:
            out[i] = "same"
    return out


def _answer(conn, ids, done) -> dict:
    return {"changed": done, "left": _left(conn, ids, done)}


# --------------------------------------------------------------- the switch
def aside(settings: Settings, ids, *, by: str) -> dict:
    """Out of the queue by hand: a free or waiting Gmail is set aside, and
    nothing takes it until it is turned on. A waiting one keeps its hour
    and its reason, so turning it on puts it back where it was."""
    ids = _ids(ids)
    with connect(settings) as conn:
        # A free one keeps no hour: one left on it by an older Free would
        # bring it back waiting.
        done = _change(conn, ids, where=IN_PLAY,
                       sets={"status": "'set_aside'",
                             "retry_after": "CASE WHEN old.status = '' THEN NULL"
                                            " ELSE old.retry_after END"},
                       fields=("status", "retry_after"))
        answer = _answer(conn, ids, done)
        conn.commit()
    log.info("%s set %d Gmail(s) aside", by, len(done))
    return answer


def queue(settings: Settings, ids, *, by: str) -> dict:
    """Back in the queue: a Gmail out of it goes in again. Set aside while
    waiting, it waits again for the hour it had, if that is still to come;
    anything else is free - off the seller's list, its tries as they were,
    so the queue reaches it after the fresh ones (store.ladder)."""
    ids = _ids(ids)
    waits = (f"lower(r.status) = 'set_aside' AND r.retry_after > now()"
             f" AND r.last_reason = ANY(%(distrust)s)"
             f" AND coalesce(r.refund_state, '') = ''")
    with connect(settings) as conn:
        done = _change(
            conn, ids, where=QUEUEABLE,
            sets={"status": f"CASE WHEN {waits.replace('r.', 'old.')}"
                            f" THEN old.last_reason ELSE '' END",
                  "retry_after": f"CASE WHEN {waits.replace('r.', 'old.')}"
                                 f" THEN old.retry_after ELSE NULL END",
                  "serial": "''", "refund_state": "''", "refund_at": "NULL"},
            fields=("status", "retry_after", "serial", "refund_state",
                    "refund_at"))
        answer = _answer(conn, ids, done)
        conn.commit()
    log.info("%s put %d Gmail(s) back in the queue", by, len(done))
    return answer


# ---------------------------------------------------------------- the fix
def fixed_note(by: str, day: datetime.date | None = None) -> str:
    return f"Marked as fixed on {day_word(day)} by {by}; its tries start again."


def mend(settings: Settings, ids, *, by: str, note: str | None = None) -> dict:
    """Marked fixed: refused by Google and mended by the seller or by
    hand, it goes back to the pool as fresh stock - free, its tries from
    nought, off the seller's list, no exit to keep away from - and the
    page counts its sign-ins from now. The farm's note about why it broke
    gives way to one about the fix, unless the person wrote their own."""
    ids = _ids(ids)
    with connect(settings) as conn:
        done = _mend(conn, ids, by=by, note=note)
        answer = _answer(conn, ids, done)
        conn.commit()
    log.info("%s marked %d Gmail(s) fixed", by, len(done))
    return answer


_MEND_FIELDS = ("status", "serial", "retry_after", "tries", "last_reason",
                "last_host", "refund_state", "refund_at", "fixed_at",
                "fixed_by", "note")


def _mend(conn, ids: list[int], *, by: str, note: str | None) -> list[dict]:
    return _change(
        conn, ids, where=MENDABLE,
        sets={"status": "''", "serial": "''", "retry_after": "NULL",
              "tries": "0", "last_reason": "''", "last_host": "''",
              "refund_state": "''", "refund_at": "NULL",
              "fixed_at": "now()", "fixed_by": "%(by)s",
              "note": "%(note)s"},
        fields=_MEND_FIELDS,
        params={"by": str(by)[:80],
                "note": (note if note is not None else fixed_note(by))[:500]})


# ------------------------------------------------------------ the product
def keep_for(settings: Settings, ids, lane: str, *, by: str) -> dict:
    """Kept for one product - builds for the other pass these Gmails by
    (PgGmailPool.claim) - or, with a blank lane, for any product again."""
    lane = str(lane or "").strip().lower()
    if lane not in ("", *LANES):
        raise Refused(f"{lane!r} is not a product a Gmail can be kept for.")
    ids = _ids(ids)
    with connect(settings) as conn:
        done = _change(conn, ids,
                       where=f"{UNUSED} AND coalesce(r.purpose, '') <> %(lane)s",
                       sets={"purpose": "%(lane)s"}, fields=("purpose",),
                       params={"lane": lane})
        answer = _answer(conn, ids, done)
        conn.commit()
    log.info("%s kept %d Gmail(s) for %s", by, len(done), lane or "any product")
    return answer


# ------------------------------------------------------------- the archive
def remove(settings: Settings, ids, *, by: str) -> dict:
    """Out of the pool and into the archive - the whole row kept, so it can
    be read, counted and put back (store.pool_archive). An unused Gmail or
    a spent one; one on a phone stays where it is."""
    ids = _ids(ids)
    with connect(settings) as conn:
        cur = conn.execute(
            f"WITH gone AS ("
            f"  SELECT r.* FROM resources r"
            f"   WHERE r.kind = 'gmail' AND r.id = ANY(%(ids)s) AND {REMOVABLE}"
            f"   FOR UPDATE),"
            f" copied AS ("
            f"  INSERT INTO resources_archive"
            f"    (id, kind, address, status, seller, payload, archived_by)"
            f"  SELECT g.id, g.kind, coalesce(g.address, ''),"
            f"         coalesce(g.status, ''), coalesce(g.seller, ''),"
            f"         to_jsonb(g), %(by)s FROM gone g"
            f"  ON CONFLICT (id) DO NOTHING RETURNING id)"
            f" DELETE FROM resources WHERE id IN (SELECT id FROM copied)"
            f" RETURNING id, coalesce(address, '')",
            {"ids": ids, "by": str(by)[:80]})
        done = [{"id": int(r[0]), "address": str(r[1]), "archived": True}
                for r in cur.fetchall()]
        answer = _answer(conn, ids, done)
        conn.commit()
    log.info("%s removed %d Gmail(s) to the archive", by, len(done))
    return answer


def _live_columns(conn) -> set[str]:
    """The columns of the `resources` this connection writes to, by its own
    name resolution - so the table the INSERT lands in is the one asked."""
    return {r[0] for r in conn.execute(
        "SELECT attname FROM pg_attribute"
        " WHERE attrelid = 'resources'::regclass AND attnum > 0"
        "   AND NOT attisdropped AND attgenerated = ''").fetchall()}


def _unarchive(conn, row_id: int, live: set[str] | None = None) -> dict | None:
    """One archived Gmail back in the pool exactly as it was - its own id,
    every column the table still has (`live`, read once by a caller that
    puts back many) - unless its address is in the pool again meanwhile.
    None when it could not go back."""
    got = conn.execute(
        "SELECT payload FROM resources_archive WHERE id = %s AND kind = 'gmail'"
        " FOR UPDATE", (int(row_id),)).fetchone()
    if got is None:
        return None
    payload = dict(got[0] or {})
    address = str(payload.get("address") or "")
    if conn.execute("SELECT 1 FROM resources WHERE kind = 'gmail'"
                    " AND lower(address) = lower(%s)", (address,)).fetchone():
        return None
    if live is None:
        live = _live_columns(conn)
    from psycopg.types.json import Jsonb

    # A json column comes out of the payload as a dict or a list, and
    # either would be bound as an array, not as json.
    fields = {k: Jsonb(v) if isinstance(v, (dict, list)) else v
              for k, v in payload.items() if k in live}
    fields["id"] = int(row_id)
    fields.pop("updated_at", None)
    columns = sorted(fields)
    cur = conn.execute(
        f"INSERT INTO resources ({', '.join(columns)}) OVERRIDING SYSTEM VALUE"
        f" VALUES ({', '.join(['%s'] * len(columns))})"
        f" ON CONFLICT DO NOTHING RETURNING id, updated_at",
        [fields[c] for c in columns])
    got = cur.fetchone()
    if got is None:
        return None
    conn.execute("DELETE FROM resources_archive WHERE id = %s", (int(row_id),))
    return {"id": int(row_id), "address": address, "ver": _stamp(got[1])}


def restore(settings: Settings, row_id: int, *, by: str) -> dict | None:
    """Undo of the dashboard's Remove: the archived Gmail back as it left,
    under its own id - None when it is not in the archive, or its address
    is in the pool again."""
    with connect(settings) as conn:
        got = _unarchive(conn, int(row_id))
        conn.commit()
    if got:
        log.info("%s put %s back from the archive", by, got["address"])
    return got


# ------------------------------------------------------------- the details
def check_details(address: str, password: str, key: str, recovery: str,
                  *, touched=("password", "key", "recovery")) -> dict:
    """The details a Gmail is given, judged by the rule a phone will judge
    them by (Credentials.validate): {password, key, recovery} as they will
    be written, or Refused saying what is wrong and how to put it right.
    The page's own stricter words apply to what the person `touched`; a
    value the farm already holds answers only to the farm's rule, so an
    older key nobody is changing never stops a note being saved."""
    from ..accounts import AccountError, Credentials, normalize_totp_secret

    password = str(password or "").strip()
    key = normalize_totp_secret(str(key or ""))
    recovery = str(recovery or "").strip()
    if "recovery" in touched:
        recovery = recovery.lower()
    if not password:
        raise Refused("A Gmail needs its password.")
    if "key" in touched and key and (len(key) < 16
                                     or not re.fullmatch(r"[A-Z2-7]+", key)):
        raise Refused("A key is 16 or more letters A to Z and digits 2 to 7;"
                      " spaces between its groups are fine.")
    if "recovery" in touched and recovery and not _MAIL.match(recovery):
        raise Refused("A recovery address looks like name@outlook.com.")
    if ("recovery" in touched and recovery
            and recovery == str(address or "").strip().lower()):
        raise Refused("The recovery address has to be another mailbox,"
                      " not this Gmail.")
    try:
        Credentials(email=str(address or "").strip(), password=password,
                    totp_secret=key, recovery_email=recovery
                    ).validate(what="gmail")
    except AccountError as exc:
        said = str(exc)
        raise Refused(said[0].upper() + said[1:] if said else
                      "The farm cannot use these details.") from exc
    return {"password": password, "key": key, "recovery": recovery}


def _said(old: dict, new: dict) -> str:
    """What a save changed, in words - never the values."""
    done = []
    if new["password"] != (old.get("password") or ""):
        done.append("password changed")
    was_key, was_rec = old.get("totp_secret") or "", old.get("recovery_email") or ""
    if new["key"] != was_key:
        done.append("key removed" if not new["key"] else
                    "key changed" if was_key else "key added")
    if new["recovery"] != was_rec:
        done.append("recovery address removed" if not new["recovery"] else
                    "recovery address changed" if was_rec else
                    "recovery address added")
    return done


def words(done: list[str]) -> str:
    if len(done) < 2:
        return "".join(done)
    return ", ".join(done[:-1]) + " and " + done[-1]


_SAVE_FIELDS = ("password", "totp_secret", "recovery_email", "note", "error")


def save(settings: Settings, row_id: int, *, password: str | None,
         key: str | None, recovery: str | None, note: str | None,
         fixed: bool, by: str) -> dict:
    """A Gmail's details as the person left them in its form: the password,
    the key and the recovery address as typed - an empty key or recovery
    address takes that one off - and the note. A field given as None is
    one the person did not touch, and keeps exactly what the farm holds
    now: a form opened a while ago must not write back a value somebody
    else has changed since. Not while a phone has it: the build signs in
    with what it read. With `fixed`, a Gmail Google refused is marked
    fixed in the same statement (`mend`), its note kept when the person
    wrote one - one change, so one Undo takes it all back.

    The answer says what changed in words, never the values."""
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT id, coalesce(address, '') AS address, lower(status),"
            " coalesce(password, ''), coalesce(totp_secret, ''),"
            " coalesce(recovery_email, ''), coalesce(note, '')"
            " FROM resources WHERE kind = 'gmail' AND id = %s FOR UPDATE",
            (int(row_id),)).fetchone()
        if row is None:
            raise Refused("That Gmail is no longer in the pool.")
        _, address, status, *_rest = row
        if status in ("in_use", "ready"):
            raise Refused(f"{address} is on a phone now; its details stay as"
                          f" the build read them until the phone is done.")
        old = {"password": row[3], "totp_secret": row[4],
               "recovery_email": row[5], "note": row[6]}
        new = check_details(address,
                            old["password"] if password is None else password,
                            old["totp_secret"] if key is None else key,
                            old["recovery_email"] if recovery is None else recovery,
                            touched=[n for n, v in (("password", password), ("key", key),
                                                    ("recovery", recovery))
                                     if v is not None])
        # Untouched, a field keeps the farm's own value to the letter.
        if password is None:
            new["password"] = old["password"]
        if key is None:
            new["key"] = old["totp_secret"]
        if recovery is None:
            new["recovery"] = old["recovery_email"]
        noted = note is not None and str(note).strip() != old["note"].strip()
        done = _said(old, new)
        if noted:
            done.append("note edited")
        mend_it = bool(fixed) and conn.execute(
            f"SELECT 1 FROM resources r WHERE r.id = %(id)s AND {MENDABLE}",
            {"id": int(row_id), "distrust": _DISTRUST}).fetchone() is not None
        sets = {"password": "%(pw)s", "totp_secret": "%(key)s",
                "recovery_email": "%(rec)s", "error": "NULL",
                "note": "%(note)s" if noted else "r.note"}
        fields = _SAVE_FIELDS
        params = {"pw": new["password"], "key": new["key"],
                  "rec": new["recovery"], "note": str(note or "").strip()[:500]}
        if mend_it:
            sets.update({"status": "''", "serial": "''", "retry_after": "NULL",
                         "tries": "0", "last_reason": "''", "last_host": "''",
                         "refund_state": "''", "refund_at": "NULL",
                         "fixed_at": "now()", "fixed_by": "%(by)s",
                         "note": "%(note)s"})
            fields = _SAVE_FIELDS + tuple(f for f in _MEND_FIELDS
                                          if f not in _SAVE_FIELDS)
            params.update(by=str(by)[:80],
                          note=(str(note).strip() if noted
                                else fixed_note(by))[:500])
        changes = []
        if done or mend_it:
            changes = _change(conn, [int(row_id)], where="TRUE", sets=sets,
                              fields=fields, params=params)
        conn.commit()
    log.info("%s saved the details of %s (%s)%s", by, address,
             words(done) or "nothing different",
             " and marked it fixed" if mend_it else "")
    return {"id": int(row_id), "address": address, "said": words(done),
            "changed": changes, "mended": mend_it,
            "unfixable": bool(fixed) and not mend_it}


def secrets(settings: Settings, row_id: int) -> dict | None:
    """One Gmail's password, key and recovery address, for an admin's
    drawer - None when it is not in the pool."""
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT coalesce(address, ''), coalesce(password, ''),"
            " coalesce(totp_secret, ''), coalesce(recovery_email, '')"
            " FROM resources WHERE kind = 'gmail' AND id = %s",
            (int(row_id),)).fetchone()
        conn.rollback()
    if row is None:
        return None
    return {"id": int(row_id), "address": row[0], "pw": row[1], "key": row[2],
            "rec": row[3]}


# ----------------------------------------------------------------- the add
def add(settings: Settings, rows: list[dict], *, seller: str, lane: str,
        by: str, by_id: int | None = None, back: list[dict] | None = None,
        carry: list | None = None) -> dict:
    """A paste from the page. `rows` are new Gmails - {address, password,
    key, recovery} as the page read each line - filed under `seller`, the
    batch's key ("LEO 7OCT"), bought today, free in the queue and kept for
    `lane` if one is chosen. `back` are Gmails of the pool Google refused,
    whose lines bring new details: saved, and marked fixed. `carry` are the
    unused Gmails of the batch they join, when the person chose another
    product for the whole batch.

    Every row is judged by itself; one that is refused stays out and says
    why. An address already in the pool is not added twice, and one that
    was spent on a phone before - it is in the archive as used - is not
    sold to the farm twice either."""
    lane = str(lane or "").strip().lower()
    if lane not in ("", *LANES):
        raise Refused(f"{lane!r} is not a product a Gmail can be kept for.")
    seller = re.sub(r"\s+", " ", str(seller or "")).strip()[:60]
    rows, back = list(rows or []), list(back or [])
    if len(rows) + len(back) > MOST:
        raise Refused(f"Paste at most {MOST} Gmails at a time - nothing was added.")
    if rows and not re.sub(r"[^A-Za-z0-9]", "", seller):
        raise Refused("Name the seller to file them.")
    # Judged before anything is written, so a refusal here writes nothing.
    carry = _ids(carry)
    bought = today().isoformat()
    note = f"Added from the web by {by} on {day_word()}."
    added, refused = [], []
    with connect(settings) as conn:
        spent = {str(r[0]).lower() for r in conn.execute(
            "SELECT lower(address) FROM resources_archive"
            " WHERE kind = 'gmail' AND status = 'used'"
            "   AND lower(address) = ANY(%s)",
            ([str(r.get("address") or "").strip().lower() for r in rows],)
        ).fetchall()}
        for row in rows:
            address = str(row.get("address") or "").strip().lower()
            try:
                ok = check_details(address, row.get("password"), row.get("key"),
                                   row.get("recovery"))
            except Refused as exc:
                refused.append({"address": address, "why": str(exc)})
                continue
            if address in spent:
                refused.append({"address": address,
                                "why": "was spent on a phone before; it is in"
                                       " the archive"})
                continue
            got = conn.execute(
                "INSERT INTO resources (kind, address, password, totp_secret,"
                " recovery_email, seller, purchased_on, status, note, purpose,"
                " source, added_by)"
                " VALUES ('gmail', %s, %s, %s, %s, %s, %s, '', %s, %s, 'web', %s)"
                " ON CONFLICT DO NOTHING RETURNING id",
                (address, ok["password"], ok["key"], ok["recovery"], seller,
                 bought, note, lane, by_id)).fetchone()
            if got is None:
                refused.append({"address": address, "why": "is in the pool already"})
                continue
            added.append({"id": int(got[0]), "address": address})
        conn.commit()
    # The new rows are written. Every later step is its own, and one that
    # fails is told as that line left out - never a raise, which would put
    # the whole paste back in the queue to be run again over rows it added.
    returned = []
    for row in back:
        address = str(row.get("address") or "").strip().lower()
        try:
            with connect(settings) as conn:
                hit = conn.execute(
                    f"SELECT r.id FROM resources r WHERE r.kind = 'gmail'"
                    f" AND lower(r.address) = %(a)s AND {MENDABLE}",
                    {"a": address, "distrust": _DISTRUST}).fetchone()
                conn.rollback()
            if hit is None:
                refused.append({"address": address,
                                "why": "is not a refused Gmail of the pool any more"})
                continue
            # A line brings a password, and a key or a recovery address only
            # if it has one: the one it does not mention stays as it is.
            got = save(settings, int(hit[0]), password=row.get("password") or "",
                       key=row.get("key") or None,
                       recovery=row.get("recovery") or None,
                       note=None, fixed=True, by=by)
        except Refused as exc:
            refused.append({"address": address, "why": str(exc)})
            continue
        except Exception as exc:                                  # noqa: BLE001
            log.warning("the pasted line of %s did not bring it back (%s)",
                        address, exc)
            refused.append({"address": address,
                            "why": "could not be saved just now; paste its line again"})
            continue
        returned.append({"id": got["id"], "address": address,
                         "said": got["said"], "changed": got["changed"],
                         "mended": got["mended"]})
    # The batch's other unused Gmails follow its new product only when
    # this paste really joined it.
    carried = []
    if carry and added:
        try:
            carried = keep_for(settings, carry, lane, by=by)["changed"]
        except Exception as exc:                                  # noqa: BLE001
            log.warning("the batch's other Gmails did not follow its product"
                        " (%s)", exc)
    log.info("%s added %d Gmail(s) to %s and brought %d back fixed; %d left out",
             by, len(added), seller or "no batch", len(returned), len(refused))
    return {"added": added, "returned": returned, "refused": refused,
            "carried": carried, "seller": seller, "lane": lane}


# ---------------------------------------------------------------- scrub
#: The requests of this page that carry typed details: a save's old and
#: new password, key and recovery address (its Undo needs them), and a
#: paste's lines.
_SCRUB_VERBS = ("gmail_save", "gmails_add")
_SECRET_FIELDS = ("password", "totp_secret", "recovery_email")
#: Past the Undo window (verbs.UNDO_MINUTES) and a margin.
SCRUB_AFTER_MINUTES = 20
#: When this process last looked: every press and page load asks, and a
#: look a minute is plenty.
_scrubbed_at = [0.0]


def scrub_old(settings: Settings, minutes: int = SCRUB_AFTER_MINUTES, *,
              every: float = 60.0) -> int:
    """Take the typed details out of this page's settled requests once their
    Undo has passed - as the Station does with a build's typed secrets.
    What each did stays: which Gmails, and in words. Looks at most once
    `every` seconds; returns how many were scrubbed; never raises."""
    from psycopg.types.json import Jsonb

    if time.monotonic() - _scrubbed_at[0] < every:
        return 0
    _scrubbed_at[0] = time.monotonic()
    try:
        with connect(settings) as conn:
            rows = conn.execute(
                "SELECT id, payload, detail FROM actions"
                " WHERE verb = ANY(%s)"
                "   AND status IN ('done', 'failed', 'refused', 'cancelled')"
                "   AND requested_at < now() - %s"
                "   AND coalesce(detail->>'scrubbed', '') = ''"
                " ORDER BY id LIMIT 500",
                (list(_SCRUB_VERBS),
                 datetime.timedelta(minutes=int(minutes)))).fetchall()
            for rid, payload, detail in rows:
                payload = dict(payload) if isinstance(payload, dict) else {}
                for name in ("password", "key", "recovery"):
                    payload.pop(name, None)
                for name in ("rows", "back"):
                    if isinstance(payload.get(name), list):
                        payload[name] = [{"address": str(r.get("address") or "")}
                                         for r in payload[name] if isinstance(r, dict)]
                detail = dict(detail) if isinstance(detail, dict) else {}
                for change in detail.get("changes") or []:
                    for side in ("before", "after"):
                        part = change.get(side) if isinstance(change, dict) else None
                        for name in _SECRET_FIELDS if isinstance(part, dict) else ():
                            part.pop(name, None)
                detail["scrubbed"] = "1"
                conn.execute("UPDATE actions SET payload = %s, detail = %s"
                             " WHERE id = %s", (Jsonb(payload), Jsonb(detail), rid))
            conn.commit()
    except Exception as exc:                                      # noqa: BLE001
        log.warning("could not scrub the Gmails page's settled requests (%s)",
                    exc)
        return 0
    return len(rows)


# ----------------------------------------------------------------- undo
def revert(settings: Settings, changes: list[dict], *, by: str) -> dict:
    """Put rows back as a change found them - each only if nothing has
    touched it since (its `updated_at` is still the one the change left).
    An archived row comes back with its own id, unless its address is in
    the pool again. Answers which went back and which had moved on."""
    back, moved = [], []
    # Newest first, and a row taken back once is expected next at the
    # stamp that taking-back left: one press can have changed a row twice.
    now_ver: dict[int, str] = {}
    # Read once: an Undo of a Remove can put hundreds back.
    live: set[str] | None = None
    with connect(settings) as conn:
        for change in reversed(list(changes or [])):
            row_id = int(change.get("id") or 0)
            if change.get("archived"):
                if live is None:
                    live = _live_columns(conn)
                got = _unarchive(conn, row_id, live)
                if got:
                    now_ver[row_id] = got["ver"]
                (back if got else moved).append(row_id)
                continue
            before = {k: v for k, v in (change.get("before") or {}).items()
                      if k in _TYPES}
            ver = now_ver.get(row_id) or change.get("ver")
            if not before or not ver:
                moved.append(row_id)
                continue
            sets = ", ".join(f"{k} = %({k})s::{_TYPES[k]}" for k in before)
            cur = conn.execute(
                f"UPDATE resources SET {sets}, updated_at = now()"
                f" WHERE kind = 'gmail' AND id = %(id)s"
                f"   AND updated_at = %(ver)s::timestamptz"
                f" RETURNING updated_at",
                dict(before, id=row_id, ver=ver))
            got = cur.fetchone()
            if got is None:
                moved.append(row_id)
                continue
            now_ver[row_id] = _stamp(got[0])
            back.append(row_id)
        conn.commit()
    back = sorted(set(back))
    moved = sorted(set(moved) - set(back))
    log.info("%s took back a change: %d row(s) back, %d moved on since",
             by, len(back), len(moved))
    return {"back": back, "moved": moved}
