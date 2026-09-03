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

import logging

from ..config import Settings
from ..store.db import Store

log = logging.getLogger(__name__)


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
    """The numbers the rail shows beside its links, plus the last pass's
    pulse and the alerts it implies - one round trip, on every page."""
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
            "   WHERE status IN ('queued', 'running')) AS pending,"
            " (SELECT count(*) FROM resources"
            "   WHERE error IS NOT NULL) AS broken,"
            " (SELECT count(*) FROM phones WHERE done_at IS NULL"
            "   AND tries >= 3) AS given_up,"
            " (SELECT value FROM service_state WHERE key = 'pass') AS pulse"
            " FROM resources")
    counts = dict(rows[0]) if rows else {"gmail": 0, "proxy": 0, "app": 0,
                                        "pending": 0, "broken": 0,
                                        "given_up": 0, "pulse": None}
    counts["pulse"] = counts.get("pulse") or {}
    counts["needs"] = int(counts.get("broken") or 0) + int(
        counts.get("given_up") or 0)
    counts["alerts"] = alerts(counts["pulse"], counts)
    return counts


#: A pass older than this and every number on every page is stale.
STALE_AFTER = 180


def alerts(pulse: dict, counts: dict) -> list[dict]:
    """What is wrong right now, as sentences with the page that fixes it.
    Read off the last pass's pulse, never recomputed; empty when the
    farm is simply running."""
    import time as _time

    found = []
    pulse = pulse or {}
    age = _time.time() - float(pulse.get("at") or 0) if pulse.get("at") else None
    if pulse.get("stopped"):
        found.append({"level": "bad", "href": "/",
                      "text": "STOPPED from the sheet - nothing is synced, "
                              "built or drained until Stop everything is "
                              "unticked (or Start from the dashboard)."})
    elif age is not None and age > STALE_AFTER:
        minutes = int(age // 60)
        found.append({"level": "warn", "href": "/events",
                      "text": f"The last pass was {minutes}m ago. Every "
                              f"number here is that old; queued requests "
                              f"wait for the next one."})
    if pulse.get("tripped"):
        n, limit = pulse.get("breaker_count", 0), pulse.get("breaker_limit", 5)
        why = ", ".join(pulse.get("breaker_reasons") or []) or "no reason recorded"
        found.append({"level": "bad", "href": "/events?kind=builds",
                      "text": f"The breaker is open ({n} of {limit} in a row: "
                              f"{why}). Nothing is built until it is cleared."})
    if pulse.get("paused"):
        found.append({"level": "warn", "href": "/",
                      "text": "Building is paused (Pause building is ticked)."})
    if int(pulse.get("failing") or 0) > 0:
        found.append({"level": "bad", "href": "/logs?level=ERROR",
                      "text": f"{pulse['failing']} pass(es) in a row failed - "
                              f"the log says why."})
    if int(counts.get("gmail") or 0) == 0:
        found.append({"level": "bad", "href": "/pools/gmail",
                      "text": "The Gmail pool is empty. No new phone can be "
                              "built until rows are added - building resumes "
                              "on its own once stock arrives."})
    if int(pulse.get("unknown_running") or 0) > 0:
        found.append({"level": "warn", "href": "/needs",
                      "text": f"{pulse['unknown_running']} phone(s) are running "
                              f"that nothing accounts for - they are being "
                              f"billed."})
    return found


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


#: How each pool's status words fold into the dashboard's three numbers.
_FOLD = {
    "gmail": {"free": ("",), "on_phones": ("in_use", "ready"),
              "used": ("used",)},
    "proxy": {"free": ("", "free", "unused"),
              "on_phones": ("on a phone", "claimed"), "dead": ("dead",)},
}


def dashboard(settings: Settings, owner_id: int | None = None) -> dict:
    """Everything the dashboard shows, in one connection: the phones (with
    who took them and, for one being built, its last captured log line),
    the three stock cards, the accounts awaiting login, the last pass's
    pulse, the queue, and the two latest requests and events for the
    ticker."""
    with Store(settings) as store:
        phone_rows = store._rows(
            "SELECT p.serial, p.status, p.state, p.app_installed, p.gmail,"
            " p.app_account, p.proxy_name, p.tries, p.note, p.updated_at,"
            " u.username AS owner"
            " FROM phones p LEFT JOIN users u ON u.id = p.owner_id"
            " WHERE p.done_at IS NULL"
            " AND (%s::bigint IS NULL OR p.owner_id = %s)"
            " ORDER BY p.serial", (owner_id, owner_id))
        building = [str(r["serial"]) for r in phone_rows
                    if r["status"] == "building"]
        progress = {}
        if building:
            # The newest line captured for each phone under construction,
            # and when its run's first line landed - so the row can say
            # what it is doing and for how long, without the sheet.
            lines = store._rows(
                "SELECT l.serial, l.logger, l.msg, l.at, l.run,"
                " (SELECT min(f.at) FROM logs f"
                "   WHERE f.serial = l.serial AND f.run = l.run) AS started"
                " FROM logs l WHERE l.id IN"
                " (SELECT max(id) FROM logs WHERE serial = ANY(%s)"
                "   GROUP BY serial)", (building,))
            progress = {str(r["serial"]): r for r in lines}
        stock = store._rows(
            "SELECT kind, lower(status) AS status, count(*) AS c"
            " FROM resources WHERE error IS NULL GROUP BY kind, status")
        awaiting = store._rows(
            "SELECT r.address, r.source, coalesce(u.username, '') AS added_by,"
            " r.created_at FROM resources r"
            " LEFT JOIN users u ON u.id = r.added_by"
            " WHERE r.kind = 'app' AND r.status = '' AND r.error IS NULL"
            " ORDER BY r.created_at DESC, r.id DESC LIMIT 60")
        queue = store._rows(
            "SELECT count(*) FILTER (WHERE status = 'running') AS running,"
            " count(*) FILTER (WHERE status = 'queued') AS queued"
            " FROM actions")
        # Requests are shown from their own rows (verb, payload, who), so
        # the `request` events that mirror them would only say it twice.
        recent = store._rows(
            "SELECT at, kind, serial, status, detail FROM events"
            " WHERE kind <> 'request' ORDER BY id DESC LIMIT 2")
        asked = store._rows(
            "SELECT a.id, a.verb, a.payload, a.status, a.requested_at AS at,"
            " u.username AS requested_by"
            " FROM actions a JOIN users u ON u.id = a.requested_by"
            " ORDER BY a.id DESC LIMIT 2")
        pulse = store._rows(
            "SELECT value FROM service_state WHERE key = 'pass'")
    folded = {kind: dict.fromkeys(names, 0) for kind, names in _FOLD.items()}
    for row in stock:
        names = _FOLD.get(row["kind"])
        if not names:
            continue
        for name, words in names.items():
            if row["status"] in words:
                folded[row["kind"]][name] += row["c"]
    folded["app"] = {
        "awaiting": len(awaiting),
        "panel": sum(1 for a in awaiting if a["source"] == "panel"),
        "manual": sum(1 for a in awaiting if a["source"] != "panel"),
    }
    return {
        "phones": phone_rows,
        "progress": progress,
        "stock": folded,
        "awaiting": awaiting,
        "queue": queue[0] if queue else {"running": 0, "queued": 0},
        "recent": recent,
        "asked": asked,
        "pulse": (pulse[0]["value"] or {}) if pulse else {},
    }


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

#: Qualified with the alias `r`, because two of the queries join phones or
#: users - both of which have an `id`, a `status`, an `updated_at` - and
#: "column reference is ambiguous" took the Gmail Pool down (2026-09-03).
#: Every query that uses these therefore reads `FROM resources r`.
_GMAIL_COLUMNS = ("r.id, r.address, r.status, r.serial, r.seller,"
                  " r.purchased_on, r.used_at, r.note, r.updated_at,"
                  " r.totp_secret <> '' AS has_totp,"
                  " r.recovery_email <> '' AS has_recovery, r.source")


def _gmail_sellers(store) -> list[str]:
    rows = store._rows(
        "SELECT DISTINCT seller FROM resources"
        " WHERE kind = 'gmail' AND seller <> '' ORDER BY 1")
    return [r["seller"] for r in rows if r["seller"]]


def gmail_sellers(settings: Settings) -> list[str]:
    """Every seller the Gmail tab has ever named, for the add form's
    select - a typed seller that differs by a letter is a seller the
    promise check never matches, so the known ones are offered first."""
    with Store(settings) as store:
        return _gmail_sellers(store)


def _pages(total: int, per_page: int) -> int:
    return max(1, -(-int(total or 0) // max(1, per_page)))


def gmail_pool(settings: Settings, view: str = "active",
               seller: str = "", page: int = 1, per_page: int = 100) -> dict:
    """The Gmail Pool page, one view at a time.

    `active` is the two things a person wants to know - which addresses are
    on a phone right now, and how many are queued behind them - split so
    the count of queued is the count of builds the stock covers. `used`
    and `errored` are the archives: `used` is history, `errored` is the
    list the seller gets asked to refund, so it carries seller, purchase
    date and the date it failed. Both archives page, `per_page` rows at a
    time; the refund list itself comes whole from `errored_addresses`.
    """
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or 1))
    with Store(settings) as store:
        counts = store._rows(
            "SELECT"
            " count(*) FILTER (WHERE status = '') AS queued,"
            " count(*) FILTER (WHERE status IN ('in_use', 'ready')) AS on_phone,"
            " count(*) FILTER (WHERE status = 'used') AS used,"
            " count(*) FILTER (WHERE error IS NULL"
            "   AND NOT (status = ANY(%s)) AND status <> %s) AS errored,"
            " count(*) FILTER (WHERE error IS NOT NULL) AS broken"
            " FROM resources WHERE kind = 'gmail'",
            (sorted(ROUTINE["gmail"]), IMPORTED))[0]
        sellers = store._rows(
            "SELECT lower(seller) AS seller, count(*) c FROM resources"
            " WHERE kind = 'gmail' AND error IS NULL"
            " AND NOT (status = ANY(%s)) AND status <> %s"
            " GROUP BY lower(seller) ORDER BY c DESC",
            (sorted(ROUTINE["gmail"]), IMPORTED))
        known = _gmail_sellers(store)
        if view == "used":
            rows = store._rows(
                f"SELECT {_GMAIL_COLUMNS} FROM resources r"
                " WHERE r.kind = 'gmail' AND r.status = 'used'"
                " ORDER BY r.updated_at DESC LIMIT %s OFFSET %s",
                (per_page + 1, (page - 1) * per_page))
            return {"view": view, "counts": counts, "rows": rows[:per_page],
                    "sellers": sellers, "known_sellers": known,
                    "page": page, "more": len(rows) > per_page,
                    "pages": _pages(counts["used"], per_page)}
        if view == "errored":
            wanted = seller.lower()
            reasons = store._rows(
                "SELECT status, count(*) c FROM resources"
                " WHERE kind = 'gmail' AND error IS NULL"
                " AND NOT (status = ANY(%s)) AND status <> %s"
                " AND (%s = '' OR lower(seller) = %s)"
                " GROUP BY status ORDER BY c DESC, status",
                (sorted(ROUTINE["gmail"]), IMPORTED, wanted, wanted))
            rows = store._rows(
                f"SELECT {_GMAIL_COLUMNS} FROM resources r"
                " WHERE r.kind = 'gmail' AND r.error IS NULL"
                " AND NOT (r.status = ANY(%s)) AND r.status <> %s"
                " AND (%s = '' OR lower(r.seller) = %s)"
                " ORDER BY r.updated_at DESC LIMIT %s OFFSET %s",
                (sorted(ROUTINE["gmail"]), IMPORTED, wanted, wanted,
                 per_page + 1, (page - 1) * per_page))
            total = sum(int(r["c"]) for r in reasons)
            return {"view": view, "counts": counts, "rows": rows[:per_page],
                    "sellers": sellers, "seller": seller, "reasons": reasons,
                    "total": total, "known_sellers": known,
                    "page": page, "more": len(rows) > per_page,
                    "pages": _pages(total, per_page)}
        on_phone = store._rows(
            f"SELECT {_GMAIL_COLUMNS}, p.status AS phone_status"
            " FROM resources r LEFT JOIN phones p"
            "   ON p.serial = r.serial AND p.done_at IS NULL"
            " WHERE r.kind = 'gmail' AND r.status IN ('in_use', 'ready')"
            " ORDER BY r.updated_at DESC")
        queued = store._rows(
            f"SELECT {_GMAIL_COLUMNS} FROM resources r"
            " WHERE r.kind = 'gmail' AND r.status = '' AND r.error IS NULL"
            " ORDER BY r.sheet_row NULLS LAST, r.id")
        broken = store._rows(
            "SELECT id, address, error FROM resources"
            " WHERE kind = 'gmail' AND error IS NOT NULL ORDER BY id")
    return {"view": "active", "counts": counts, "on_phone": on_phone,
            "queued": queued, "broken": broken, "sellers": sellers,
            "known_sellers": known}


def errored_addresses(settings: Settings, seller: str = "") -> list[str]:
    """Every errored gmail address, one seller's or everyone's, with no
    page cap: this is the list the seller is asked to refund, and a list
    cut at a page boundary is a refund never asked for."""
    wanted = seller.lower()
    with Store(settings) as store:
        rows = store._rows(
            "SELECT address FROM resources"
            " WHERE kind = 'gmail' AND error IS NULL AND address IS NOT NULL"
            " AND NOT (status = ANY(%s)) AND status <> %s"
            " AND (%s = '' OR lower(seller) = %s)"
            " ORDER BY updated_at DESC, id",
            (sorted(ROUTINE["gmail"]), IMPORTED, wanted, wanted))
    return [r["address"] for r in rows if r["address"]]


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


_APP_COLUMNS = ("r.id, r.address, r.status, r.serial, r.source, r.added_by,"
                " r.note, r.updated_at, r.created_at, r.email_code_only,"
                " r.totp_secret <> '' AS has_totp")


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


# ------------------------------------------------- events, logs, story (C8)
#: The Events page's filter pills: a name a person picks, and the closed
#: vocabulary of `kind` it stands for. Alerts (stage 6) will key on the
#: same words - never on the prose in `detail`.
KINDS = {
    "builds": ("build_finished",),
    "phones": ("phone",),
    "accounts": ("account",),
    "breaker": ("breaker",),
    "requests": ("request",),
    "stock": ("stock",),
    "passes": ("pass",),
}

_LEVELS = {"INFO": ("INFO", "WARNING", "ERROR", "CRITICAL"),
           "WARNING": ("WARNING", "ERROR", "CRITICAL"),
           "ERROR": ("ERROR", "CRITICAL")}


def signals(settings: Settings) -> dict:
    """The Events page's signal bar: the last pass, the hour's builds, the
    breaker, and how long the free gmails last at this week's burn."""
    with Store(settings) as store:
        builds = store._rows(
            "SELECT count(*) FILTER (WHERE detail LIKE 'ok=True%%') AS ok,"
            " count(*) FILTER (WHERE detail NOT LIKE 'ok=True%%') AS failed"
            " FROM events WHERE kind = 'build_finished'"
            " AND at > now() - interval '1 hour'")
        week = store._rows(
            "SELECT count(*) AS spent FROM events"
            " WHERE kind = 'build_finished' AND detail LIKE 'ok=True%%'"
            " AND at > now() - interval '7 days'")
        free = store._rows(
            "SELECT count(*) AS free FROM resources WHERE kind = 'gmail'"
            " AND status = '' AND error IS NULL")
        pulse = store._rows(
            "SELECT value FROM service_state WHERE key = 'pass'")
        stock = store._rows(
            "SELECT at FROM events WHERE kind = 'stock'"
            " ORDER BY id DESC LIMIT 1")
    spent = int(week[0]["spent"]) if week else 0
    per_day = spent / 7.0
    gmail_free = int(free[0]["free"]) if free else 0
    return {
        "builds": dict(builds[0]) if builds else {"ok": 0, "failed": 0},
        "gmail_free": gmail_free,
        "gmail_per_day": per_day,
        "gmail_days": (gmail_free / per_day) if per_day else None,
        "pulse": (pulse[0]["value"] or {}) if pulse else {},
        "last_stock": stock[0]["at"] if stock else None,
    }


def events_feed(settings: Settings, *, kind: str = "", q: str = "",
                page: int = 1, per_page: int = 100) -> dict:
    """The event table, filtered by pill and by one search word - a serial,
    an address, a run id - newest first, with the pills' counts for today."""
    where, params = [], []
    if kind in KINDS:
        where.append("kind = ANY(%s)")
        params.append(list(KINDS[kind]))
    if q:
        where.append("(serial = %s OR run_id = %s OR detail ILIKE %s)")
        params += [q, q, f"%{q}%"]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    offset = max(0, page - 1) * per_page
    with Store(settings) as store:
        total = store._rows(f"SELECT count(*) AS n FROM events{clause}",
                            tuple(params))
        rows = store._rows(
            f"SELECT id, at, kind, run_id, build, serial, status, seconds,"
            f" detail FROM events{clause} ORDER BY id DESC"
            f" LIMIT %s OFFSET %s", tuple(params) + (per_page, offset))
        today = store._rows(
            "SELECT kind, count(*) AS n FROM events"
            " WHERE at > date_trunc('day', now()) GROUP BY kind")
    by_kind = {r["kind"]: int(r["n"]) for r in today}
    counts = {name: sum(by_kind.get(k, 0) for k in kinds)
              for name, kinds in KINDS.items()}
    counts["all"] = sum(by_kind.values())
    n = int(total[0]["n"]) if total else 0
    return {"rows": rows, "counts": counts, "page": page,
            "pages": max(1, -(-n // per_page)), "total": n}


def logs(settings: Settings, *, level: str = "INFO", logger: str = "",
         run: str = "", phone: str = "", q: str = "",
         limit: int = 200) -> dict:
    """The captured log lines, newest first, narrowed by whatever the
    person typed. Empty filters mean "everything at INFO and up"."""
    where = ["level = ANY(%s)"]
    params: list = [list(_LEVELS.get(level.upper(), _LEVELS["INFO"]))]
    if logger:
        where.append("logger ILIKE %s")
        params.append(f"%{logger}%")
    if run:
        where.append("run = %s")
        params.append(run)
    if phone:
        where.append("(serial = %s OR msg ILIKE %s)")
        params += [phone, f"%{phone}%"]
    if q:
        where.append("msg ILIKE %s")
        params.append(f"%{q}%")
    with Store(settings) as store:
        rows = store._rows(
            f"SELECT at, level, logger, run, build, serial, msg FROM logs"
            f" WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s",
            tuple(params) + (limit,))
        today = store._rows(
            "SELECT count(*) AS n FROM logs"
            " WHERE at > date_trunc('day', now())")
    return {"rows": rows, "today": int(today[0]["n"]) if today else 0}


def _stamp_key(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def phone_story(settings: Settings, serial: str) -> dict | None:
    """Everything one phone went through, in order: its events, the
    requests that named it, and the archived screens on disk - joined on
    the serial, which is the one name all three sources use."""
    with Store(settings) as store:
        phone = store._rows(
            "SELECT serial, status, state, gmail, app_account, proxy_name,"
            " tries, note, created_at, updated_at, done_at FROM phones"
            " WHERE serial = %s ORDER BY id DESC LIMIT 1", (serial,))
        events = store._rows(
            "SELECT at, kind, run_id, build, status, seconds, detail"
            " FROM events WHERE serial = %s ORDER BY id", (serial,))
        requests = store._rows(
            "SELECT a.id, a.verb, a.status, a.result, a.requested_at,"
            " u.username AS requested_by FROM actions a"
            " JOIN users u ON u.id = a.requested_by"
            " WHERE a.payload::text ILIKE %s ORDER BY a.id",
            (f"%{serial}%",))
    if not phone and not events:
        return None
    timeline = []
    for e in events:
        timeline.append({"at": e["at"], "source": "event",
                         "kind": e["kind"], "status": e["status"],
                         "run": (f"{e['run_id']}/{e['build']}"
                                 if e["build"] else e["run_id"]),
                         "text": e["detail"], "seconds": e["seconds"]})
    for r in requests:
        timeline.append({"at": r["requested_at"], "source": "request",
                         "kind": "request", "status": r["status"],
                         "run": f"#{r['id']}",
                         "text": f"{r['requested_by']} asked: {r['verb']}"
                                 f" -> {r['status']}: {r['result']}",
                         "seconds": None})
    for folder in _archived(settings.artifact_dir, serial):
        timeline.append(folder)
    timeline.sort(key=lambda t: _stamp_key(t["at"]))
    return {"phone": phone[0] if phone else None, "serial": serial,
            "timeline": timeline}


def _archived(root, serial: str) -> list[dict]:
    """The archived screens of one phone, as timeline entries. Never
    raises: a folder that cannot be read is one line in the log and one
    entry fewer, not a broken page."""
    from datetime import datetime, timezone

    from ..artifacts import OUTCOME_FILE, serial_of

    found = []
    try:
        folders = ([d for d in root.iterdir() if d.is_dir()]
                   if root.is_dir() else [])
    except OSError as exc:
        log.debug("could not list %s (%s)", root, exc)
        return found
    for folder in folders:
        if serial_of(folder) != serial:
            continue
        try:
            outcome_file = folder / OUTCOME_FILE
            outcome = (outcome_file.read_text(encoding="utf-8").strip()
                       .splitlines()[0] if outcome_file.is_file() else "")
            pages = sum(1 for f in folder.iterdir() if f.suffix == ".xml")
            when = datetime.fromtimestamp(folder.stat().st_mtime,
                                          tz=timezone.utc)
        except (OSError, IndexError) as exc:
            log.debug("skipping %s (%s)", folder, exc)
            continue
        found.append({"at": when, "source": "artifact", "kind": "screens",
                      "status": outcome, "run": folder.name,
                      "text": f"{pages} screen(s) archived in {folder.name}"
                              + (f" - {outcome}" if outcome else ""),
                      "seconds": None})
    return found
