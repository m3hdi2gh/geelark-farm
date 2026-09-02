"""What the pages show, read from the mirror and nowhere else.

The budget rule, absolute: no function here may open the Book or call
GeeLark. A page load renders the last pass's mirror - an idle pass already
costs ~21 Sheets reads against a 60/min quota, and a status page that syncs
is a status page that takes the service down when somebody refreshes it.
Everything below is one short Postgres connection against tables `shadow`
refreshed within the last thirty seconds.

`owner_id` is the visibility scope: None means "sees all"; a user whose
`sees` is `own` passes their id and every query narrows itself. The
narrowing lives HERE, beside the SQL, so a page cannot forget it.
"""

from __future__ import annotations

from ..config import Settings
from ..store.db import Store


def snapshot(settings: Settings, owner_id: int | None = None) -> dict:
    """The dashboard's numbers, in one round trip's worth of queries."""
    with Store(settings) as store:
        phones = store._rows(
            "SELECT status, count(*) c FROM phones"
            " WHERE done_at IS NULL AND (%s::bigint IS NULL OR owner_id = %s)"
            " GROUP BY status", (owner_id, owner_id))
        stock = store._rows(
            "SELECT kind, count(*) FILTER (WHERE status = '' AND error IS NULL)"
            "   AS free,"
            " count(*) FILTER (WHERE error IS NOT NULL) AS unusable"
            " FROM resources GROUP BY kind")
        last = store._rows(
            "SELECT at, kind, status FROM events ORDER BY id DESC LIMIT 1")
    by_status = {r["status"]: r["c"] for r in phones}
    return {
        "phones": by_status,
        "stock": {r["kind"]: {"free": r["free"], "unusable": r["unusable"]}
                  for r in stock},
        "last_event": last[0] if last else None,
    }


def nav_counts(settings: Settings) -> dict:
    """The four numbers the rail shows beside its links, one query."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT"
            " count(*) FILTER (WHERE kind = 'gmail' AND status = ''"
            "   AND error IS NULL) AS gmail,"
            " count(*) FILTER (WHERE kind = 'proxy'"
            "   AND lower(status) IN ('', 'free', 'unused')"
            "   AND error IS NULL) AS proxy,"
            " count(*) FILTER (WHERE kind = 'app' AND status = ''"
            "   AND error IS NULL) AS app,"
            " (SELECT count(*) FROM actions"
            "   WHERE status IN ('queued', 'running')) AS pending"
            " FROM resources")
    return rows[0] if rows else {"gmail": 0, "proxy": 0, "app": 0,
                                 "pending": 0}


def known(settings: Settings, kind: str) -> set[str]:
    """Every identity the mirror holds for one kind, lowercased - what the
    add previews check a pasted row against so a duplicate is said before
    it is queued. Addresses for accounts, host:port for exits."""
    with Store(settings) as store:
        if kind == "proxy":
            rows = store._rows(
                "SELECT host || ':' || port AS who FROM resources"
                " WHERE kind = 'proxy' AND host IS NOT NULL")
        else:
            rows = store._rows(
                "SELECT lower(address) AS who FROM resources"
                " WHERE kind = %s AND address IS NOT NULL", (kind,))
    return {r["who"] for r in rows if r["who"]}


def phones(settings: Settings, owner_id: int | None = None) -> list[dict]:
    with Store(settings) as store:
        return store._rows(
            "SELECT serial, status, state, app_installed, gmail,"
            " app_account, proxy_name, tries, note, updated_at"
            " FROM phones WHERE done_at IS NULL"
            " AND (%s::bigint IS NULL OR owner_id = %s)"
            " ORDER BY serial", (owner_id, owner_id))


def pools(settings: Settings) -> dict:
    """The three stock tabs as the operator reads them: counts by status,
    plus every row validation refused - the rows that looked free in the
    sheet while being nothing (Mamadovskii, 2026-08-31)."""
    with Store(settings) as store:
        counts = store._rows(
            "SELECT kind, coalesce(nullif(status, ''), '(free)') AS status,"
            " count(*) c FROM resources WHERE error IS NULL"
            " GROUP BY kind, status ORDER BY kind, c DESC")
        broken = store._rows(
            "SELECT kind, coalesce(address, proxy_name,"
            " host || ':' || port) AS who, error"
            " FROM resources WHERE error IS NOT NULL ORDER BY kind")
    return {"counts": counts, "broken": broken}


# --------------------------------------------------------------- the pools
#: Every status word that means "settled, nothing for a person to do", per
#: kind - the complement of pools.Pool.flagged, restated here because the
#: web may not import the sheet module. A pin test derives the same sets
#: from the Pool classes and holds the two copies together.
ROUTINE = {
    "gmail": frozenset({"", "in_use", "ready", "used"}),
    "app": frozenset({"", "in_use", "ready", "delivered"}),
    "proxy": frozenset({"", "free", "unused", "claimed",
                        "on a phone", "used"}),
}

#: The one status the Importer writes into a sheet row and the mirror then
#: carries: not a verdict, so never "flagged".
IMPORTED = "imported"

_GMAIL_COLUMNS = ("id, address, status, serial, seller, purchased_on,"
                  " used_at, note, updated_at, totp_secret <> '' AS has_totp,"
                  " recovery_email <> '' AS has_recovery, source")


def gmail_pool(settings: Settings, view: str = "active",
               seller: str = "") -> dict:
    """The Gmail Pool page, one view at a time.

    `active` is the two things a person wants to know - which addresses are
    on a phone right now, and how many are queued behind them - split so
    the count of queued is the count of builds the stock covers. `used`
    and `errored` are the archives: `used` is history, `errored` is the
    list the seller gets asked to refund, so it carries seller, purchase
    date and the date it failed.
    """
    with Store(settings) as store:
        counts = store._rows(
            "SELECT"
            " count(*) FILTER (WHERE status = '') AS queued,"
            " count(*) FILTER (WHERE status IN ('in_use', 'ready')) AS on_phone,"
            " count(*) FILTER (WHERE status = 'used') AS used,"
            " count(*) FILTER (WHERE NOT (status = ANY(%s))"
            "   AND status <> %s) AS errored,"
            " count(*) FILTER (WHERE error IS NOT NULL) AS broken"
            " FROM resources WHERE kind = 'gmail'",
            (sorted(ROUTINE["gmail"]), IMPORTED))[0]
        sellers = store._rows(
            "SELECT lower(seller) AS seller, count(*) c FROM resources"
            " WHERE kind = 'gmail' AND error IS NULL"
            " AND NOT (status = ANY(%s)) AND status <> %s"
            " GROUP BY lower(seller) ORDER BY c DESC",
            (sorted(ROUTINE["gmail"]), IMPORTED))
        if view == "used":
            rows = store._rows(
                f"SELECT {_GMAIL_COLUMNS} FROM resources"
                " WHERE kind = 'gmail' AND status = 'used'"
                " ORDER BY updated_at DESC LIMIT 200")
            return {"view": view, "counts": counts, "rows": rows,
                    "sellers": sellers}
        if view == "errored":
            rows = store._rows(
                f"SELECT {_GMAIL_COLUMNS} FROM resources"
                " WHERE kind = 'gmail' AND error IS NULL"
                " AND NOT (status = ANY(%s)) AND status <> %s"
                " AND (%s = '' OR lower(seller) = %s)"
                " ORDER BY updated_at DESC LIMIT 500",
                (sorted(ROUTINE["gmail"]), IMPORTED, seller.lower(),
                 seller.lower()))
            return {"view": view, "counts": counts, "rows": rows,
                    "sellers": sellers, "seller": seller}
        on_phone = store._rows(
            f"SELECT {_GMAIL_COLUMNS}, p.status AS phone_status"
            " FROM resources r LEFT JOIN phones p"
            "   ON p.serial = r.serial AND p.done_at IS NULL"
            " WHERE r.kind = 'gmail' AND r.status IN ('in_use', 'ready')"
            " ORDER BY r.updated_at DESC")
        queued = store._rows(
            f"SELECT {_GMAIL_COLUMNS} FROM resources"
            " WHERE kind = 'gmail' AND status = '' AND error IS NULL"
            " ORDER BY sheet_row NULLS LAST, id")
        broken = store._rows(
            "SELECT id, address, error FROM resources"
            " WHERE kind = 'gmail' AND error IS NOT NULL ORDER BY id")
    return {"view": "active", "counts": counts, "on_phone": on_phone,
            "queued": queued, "broken": broken, "sellers": sellers}


def proxy_pool(settings: Settings, unlisted: list | None = None) -> dict:
    """The Proxy Pool page: the rows that need a hand first, then all of
    them. `unlisted` is what the last pass found GeeLark holding that the
    tab never heard of - kept by the pass in service_state, passed in by
    the caller so this module stays a reader of two tables."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT r.id, r.proxy_name AS name, r.host, r.port, r.username,"
            " r.status, r.serial, r.last_exit_ip, r.times_used, r.note,"
            " r.updated_at, r.error"
            " FROM resources r WHERE r.kind = 'proxy'"
            " ORDER BY r.sheet_row NULLS LAST, r.id")
    by_status: dict[str, list] = {}
    for r in rows:
        word = (r["status"] or "").lower()
        key = ("free" if word in ("", "free", "unused") else
               "on_phone" if word == "on a phone" else
               "claimed" if word == "claimed" else
               "needs_new_ip" if word == "change ip" else
               "dead" if word == "dead" else "other")
        by_status.setdefault(key, []).append(r)
    counts = {k: len(v) for k, v in by_status.items()}
    counts["all"] = len(rows)
    return {"rows": rows, "counts": counts,
            "needs_new_ip": by_status.get("needs_new_ip", []),
            "dead": by_status.get("dead", []),
            "unlisted": unlisted or []}


_APP_COLUMNS = ("id, address, status, serial, source, added_by, note,"
                " updated_at, created_at, email_code_only,"
                " totp_secret <> '' AS has_totp")


def gpt_pool(settings: Settings, view: str = "active", q: str = "",
             page: int = 1, per_page: int = 50) -> dict:
    """The Gpt Pool page. Active = the accounts waiting for a phone, in
    two sections by where they came from, plus the ones a run set aside
    for a person. Delivered = the archive the customer panel pulls fates
    from, searchable by address or phone."""
    with Store(settings) as store:
        counts = store._rows(
            "SELECT"
            " count(*) FILTER (WHERE status = '' AND error IS NULL)"
            "   AS awaiting,"
            " count(*) FILTER (WHERE status = 'in_use') AS logging_in,"
            " count(*) FILTER (WHERE status = 'ready') AS on_phone,"
            " count(*) FILTER (WHERE status = 'delivered') AS delivered,"
            " count(*) FILTER (WHERE error IS NULL AND NOT (status = ANY(%s))"
            "   AND status <> %s) AS needs_human"
            " FROM resources WHERE kind = 'app'",
            (sorted(ROUTINE["app"]), IMPORTED))[0]
        if view == "delivered":
            like = f"%{q.strip()}%"
            rows = store._rows(
                f"SELECT {_APP_COLUMNS}, u.username AS added_by_name"
                " FROM resources r LEFT JOIN users u ON u.id = r.added_by"
                " WHERE r.kind = 'app' AND r.status = 'delivered'"
                " AND (%s = '' OR r.address ILIKE %s OR r.serial ILIKE %s"
                "   OR r.note ILIKE %s)"
                " ORDER BY r.updated_at DESC LIMIT %s OFFSET %s",
                (q.strip(), like, like, like, per_page + 1,
                 (page - 1) * per_page))
            more = len(rows) > per_page
            return {"view": view, "counts": counts, "rows": rows[:per_page],
                    "q": q, "page": page, "more": more}
        waiting = store._rows(
            f"SELECT {_APP_COLUMNS}, u.username AS added_by_name"
            " FROM resources r LEFT JOIN users u ON u.id = r.added_by"
            " WHERE r.kind = 'app' AND r.error IS NULL"
            " AND r.status IN ('', 'in_use')"
            " ORDER BY r.status DESC, r.sheet_row NULLS LAST, r.id")
        needs_human = store._rows(
            f"SELECT {_APP_COLUMNS} FROM resources r"
            " WHERE r.kind = 'app' AND r.error IS NULL"
            " AND NOT (r.status = ANY(%s)) AND r.status <> %s"
            " ORDER BY r.updated_at DESC",
            (sorted(ROUTINE["app"]), IMPORTED))
        broken = store._rows(
            "SELECT id, address, error FROM resources"
            " WHERE kind = 'app' AND error IS NOT NULL ORDER BY id")
    panel = [r for r in waiting if r["source"] == "panel"]
    manual = [r for r in waiting if r["source"] != "panel"]
    return {"view": "active", "counts": counts, "panel": panel,
            "manual": manual, "needs_human": needs_human, "broken": broken}


def events(settings: Settings, limit: int = 200) -> list[dict]:
    with Store(settings) as store:
        return store._rows(
            "SELECT at, kind, run_id, build, serial, status, seconds,"
            " detail FROM events ORDER BY id DESC LIMIT %s", (limit,))


def needs(settings: Settings) -> dict:
    """Everything waiting on a person, in one read of the mirror.

    This is the two needs_you implementations unified - serve's pure
    function over the sync outcome, and the console's list with its
    proxies-waiting item - rebuilt over the mirror so a page load costs
    the sheet nothing. Panel-vs-sheet strays (phones GeeLark has that the
    tab does not) are the one item that cannot be derived from the mirror;
    they live on the Service board's own row, written by the pass that
    counted them.

    Four sections, ordered by what they cost while they wait:
    - orphaned: a spent credential naming a phone that no longer exists -
      stock held by nothing, forever, until a person decides
    - flagged: rows a run judged and set aside, with the verdict's advice
    - broken: rows validation refused, invisible in the sheet by design
    - given_up: phones at the tries limit, off the shelf until cleared
    """
    with Store(settings) as store:
        flagged = []
        for kind, routine in ROUTINE.items():
            flagged += store._rows(
                "SELECT kind, coalesce(nullif(address, ''), proxy_name) who,"
                " status, serial, note FROM resources"
                " WHERE kind = %s AND error IS NULL"
                " AND NOT (status = ANY(%s)) AND status <> %s"
                " ORDER BY sheet_row", (kind, sorted(routine), IMPORTED))
        orphaned = store._rows(
            "SELECT kind, address who, status, serial FROM resources"
            " WHERE kind IN ('gmail', 'app') AND status = 'ready'"
            " AND serial <> '' AND serial NOT IN"
            " (SELECT serial FROM phones WHERE done_at IS NULL)"
            " ORDER BY kind, serial")
        broken = store._rows(
            "SELECT kind, coalesce(address, proxy_name,"
            " host || ':' || port) who, error FROM resources"
            " WHERE error IS NOT NULL ORDER BY kind")
        given_up = store._rows(
            "SELECT serial, status, tries, note FROM phones"
            " WHERE done_at IS NULL AND tries >= 3"
            " AND state IN ('', 'unused') ORDER BY serial")
    return {"orphaned": orphaned, "flagged": flagged, "broken": broken,
            "given_up": given_up}
