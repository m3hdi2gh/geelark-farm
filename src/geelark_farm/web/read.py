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

import datetime
import logging
import time

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
            "SELECT kind, count(*) FILTER (WHERE status = ''"
            "   AND error IS NULL) AS free,"
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
            # The GPT Pool badge: the Spotify rows share the table and
            # have no page of their own, so they are not this number.
            " count(*) FILTER (WHERE kind = 'app' AND status = ''"
            f"   AND error IS NULL AND {NOT_SPOTIFY}) AS app,"
            " (SELECT count(*) FROM actions"
            "   WHERE status IN ('queued', 'running')) AS pending,"
            " (SELECT count(*) FROM resources"
            "   WHERE error IS NOT NULL) AS broken,"
            " (SELECT count(*) FROM phones WHERE done_at IS NULL"
            "   AND tries >= 3) AS given_up,"
            " (SELECT value FROM service_state WHERE key = 'pass') AS pulse,"
            " (SELECT value FROM service_state"
            "   WHERE key = 'geelark_plan') AS geelark_plan,"
            " (SELECT value FROM service_state"
            "   WHERE key = 'geelark_refusal') AS geelark_refusal,"
            " (SELECT count(*) FROM phones"
            "   WHERE running AND done_at IS NULL) AS phones_running,"
            # What retires a refusal. Delivered phones count: the
            # question is whether GeeLark made one, not whether we
            # still hold it.
            " (SELECT max(created_at) FROM phones) AS phone_built_at"
            " FROM resources")
    counts = dict(rows[0]) if rows else {"gmail": 0, "proxy": 0, "app": 0,
                                        "pending": 0, "broken": 0,
                                        "given_up": 0, "pulse": None}
    counts["pulse"] = counts.get("pulse") or {}
    counts["needs"] = int(counts.get("broken") or 0) + int(
        counts.get("given_up") or 0)
    counts["alerts"] = (alerts(counts["pulse"], counts)
                        + geelark_alerts(counts))
    return counts


#: A pass older than this and every number on every page is stale.
STALE_AFTER = 180


def _why_it_tripped(pulse: dict) -> str:
    """The breaker's reasons as one sentence a person can read.

    It listed the raw tokens - `stuck_on_2fa_push_to_other_device,
    stuck_on_2fa_push_to_other_device, stuck_on_dismissable` - which is two
    lines of machine words on every page, including the operator's, who
    cannot clear it and has no use for the spelling. `failures` already
    holds the sentence for each, and the same reason three times is one
    fact, not three (the operator, 2026-09-07).
    """
    from ..failures import verdict

    said, order = {}, []
    for reason in pulse.get("breaker_reasons") or []:
        # No guard: `verdict` answers every string, and a reason it has
        # never seen gets the default verdict rather than an exception.
        seen = verdict(str(reason)).seen
        if seen not in said:
            order.append(seen)
        said[seen] = said.get(seen, 0) + 1
    if not order:
        return "No reason was recorded."
    # Commonest first, because the sentence opens with "Mostly" and a list
    # in the order the reasons happened to arrive does not keep that word's
    # promise. The count reads as a suffix: "... page (3 times)".
    first = {word: i for i, word in enumerate(order)}
    order.sort(key=lambda word: (-said[word], first[word]))
    parts = [f"{w} ({said[w]} times)" if said[w] > 1 else w for w in order]
    return "Mostly: " + "; ".join(parts) + "."


def _what_to_do(pulse: dict) -> str:
    """What to go and fix, decided by whose fault the failures were.

    It used to say "Add fresh stock" whatever had happened. On the night
    GeeLark's balance ran out that was six builds refused by somebody
    else's billing, and the console's one red line sent the operator to
    a pool that was perfectly full (2026-09-19).

    The taxonomy already knows: `failures.verdict` blames the
    credential, the exit or the device for every reason there is, and
    the stock is only the answer to the first.
    """
    from ..failures import CREDENTIAL, verdict

    reasons = [str(r) for r in (pulse.get("breaker_reasons") or [])]
    clear = "then press Clear breaker beside the status line."
    if not reasons:
        return f"Find out why, {clear}"
    blames = [verdict(r).blame for r in reasons]
    if all(b == CREDENTIAL for b in blames):
        return f"Add fresh stock, {clear}"
    if any(b == CREDENTIAL for b in blames):
        return (f"Some of that is the stock and some of it is not - read "
                f"the builds before adding rows, {clear}")
    # Nothing here is the pool's doing, so nothing in the pool fixes it.
    return (f"None of that is the stock, so adding rows will not help: "
            f"fix what the reasons name, {clear}")


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
                      "text": "STOPPED - nothing is synced, built or drained "
                              "until Start is pressed."})
    elif age is not None and age > STALE_AFTER:
        minutes = int(age // 60)
        found.append({"level": "warn", "href": "/events",
                      "text": f"The last pass was {minutes}m ago. Every "
                              f"number here is that old; queued requests "
                              f"wait for the next one."})
    if pulse.get("tripped"):
        n, limit = pulse.get("breaker_count", 0), pulse.get("breaker_limit", 5)
        found.append({"level": "bad", "href": "/events?kind=builds",
                      "text": f"Building has stopped - {n} builds in a row "
                              f"failed, and {limit} is the limit. "
                              f"{_why_it_tripped(pulse)} {_what_to_do(pulse)}"})
    if pulse.get("paused"):
        found.append({"level": "warn", "href": "/",
                      "text": "Building is paused (Pause building is ticked)."})
    if int(pulse.get("failing") or 0) > 0:
        found.append({"level": "bad", "href": "/logs?level=ERROR",
                      "text": f"{pulse['failing']} pass(es) in a row "
                              f"failed. Nothing new is being built until "
                              f"one of them gets through."})
    if int(counts.get("gmail") or 0) == 0:
        found.append({"level": "bad", "href": "/pools/gmail",
                      "text": "The Gmail pool is empty. No new phone can be "
                              "built until rows are added - building resumes "
                              "on its own once stock arrives."})
    if int(pulse.get("unknown_running") or 0) > 0:
        found.append({"level": "warn", "href": "/needs",
                      "text": f"{pulse['unknown_running']} phone(s) are "
                              f"running that nothing accounts for and are "
                              f"being billed. An admin has to stop them."})
    return found


def known(settings: Settings, kind: str) -> dict[str, str]:
    """Every identity the mirror holds for one kind, lowercased, against
    the state that row is in - what the add previews check a pasted row
    against so a duplicate is said before it is queued. Addresses for
    accounts, host:port for exits.

    A dict rather than a set, so the badge can say *where* the row is: it
    said "already in the pool" about addresses the manager deliberately
    does not list - a used Gmail, a delivered account - so the operator
    was sent to look for a row that is not there (2026-09-07). Every
    `address in known` test still reads, because `in` on a dict is key
    membership.
    """
    with Store(settings) as store:
        if kind == "proxy":
            rows = store._rows(
                "SELECT host || ':' || port AS who, status FROM resources"
                " WHERE kind = 'proxy' AND host IS NOT NULL")
        else:
            rows = store._rows(
                "SELECT lower(address) AS who, status FROM resources"
                " WHERE kind = %s AND address IS NOT NULL", (kind,))
    return {r["who"]: _pool_state(kind, r) for r in rows if r["who"]}


def phones(settings: Settings, owner_id: int | None = None) -> list[dict]:
    with Store(settings) as store:
        return store._rows(
            "SELECT serial, status, state, app_installed, gmail,"
            " app_account, proxy_name, tries, note, updated_at, running"
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
    from ..store import stops as store_stops

    """Everything the dashboard shows, in one connection: the phones (with
    who took them and, for one being built, its last captured log line),
    the three stock cards, the accounts awaiting login, the last pass's
    pulse, the queue, and the two latest requests and events for the
    ticker."""
    with Store(settings) as store:
        phone_rows = store._rows(
            "SELECT p.serial, p.status, p.state, p.app_installed, p.gmail,"
            " p.app_account, p.proxy_name, p.tries, p.note, p.updated_at,"
            " p.created_at, p.running, p.app, u.username AS owner,"
            " bu.username AS built_by,"
            # Which product the account on it belongs to, and for a
            # Spotify one which category - read off the pool row rather
            # than guessed from the app column, so the table can say
            # what a phone carries (2026-09-17).
            " coalesce(ra.product, '') AS app_product,"
            " coalesce(ra.category, '') AS app_category"
            " FROM phones p LEFT JOIN users u ON u.id = p.owner_id"
            " LEFT JOIN users bu ON bu.id = p.built_by"
            " LEFT JOIN resources ra ON ra.kind = 'app'"
            "   AND lower(ra.address) = lower(p.app_account)"
            " WHERE p.done_at IS NULL"
            " AND (%s::bigint IS NULL OR p.owner_id = %s)"
            " ORDER BY p.serial", (owner_id, owner_id))
        building = [str(r["serial"]) for r in phone_rows
                    if r["status"] == "building"]
        progress = _latest_lines(store, building)
        live = _live_links(store, building)
        stock = store._rows(
            "SELECT kind, lower(status) AS status, count(*) AS c"
            " FROM resources WHERE error IS NULL"
            " GROUP BY kind, status")
        awaiting = store._rows(
            "SELECT r.address, r.source, coalesce(u.username, '') AS added_by,"
            " r.created_at FROM resources r"
            " LEFT JOIN users u ON u.id = r.added_by"
            " WHERE r.kind = 'app' AND r.status = '' AND r.error IS NULL"
            f"   AND {NOT_SPOTIFY}"
            " ORDER BY r.created_at DESC, r.id DESC LIMIT 60")
        # The card's number, counted rather than measured off the list
        # above - which stops at sixty. Past that the card said sixty and
        # the list under it went on (2026-09-07).
        waiting = store._rows(
            "SELECT count(*) AS all_of_them,"
            " count(*) FILTER (WHERE source = 'panel') AS panel"
            " FROM resources"
            " WHERE kind = 'app' AND status = '' AND error IS NULL"
            f"   AND {NOT_SPOTIFY}")
        # The Spotify card's two numbers, by category.
        spotify_free = store._rows(
            "SELECT coalesce(category, '') AS category, count(*) AS c"
            " FROM resources WHERE kind = 'app' AND status = ''"
            f"   AND error IS NULL AND {IS_SPOTIFY}"
            " GROUP BY 1")
        # What a person can choose from when they build one by hand. Capped:
        # this is a picker, not the pool page, and a select with four hundred
        # options is a worse way to find an address than the search box on
        # the tab it came from.
        choose = {}
        choose["gmails"] = store._rows(
            "SELECT address AS label FROM resources"
            " WHERE kind = 'gmail' AND status = '' AND error IS NULL"
            "   AND address <> ''"
            " ORDER BY sheet_row NULLS LAST, id LIMIT 60")
        # The app rows carry what kind of account they are, because the
        # card asks that first now: a bare phone may take a `normal`
        # Spotify account and nothing else, and a phone with a Gmail may
        # take any of the rest. Spotify is no longer filtered out -
        # `NOT_SPOTIFY` was here while no flow could sign one in.
        #
        # Capped per kind rather than over the lot. One window of sixty
        # across four kinds is a window the oldest kind fills: the rows
        # are ordered by the sheet row they came in on, every Spotify
        # and `eco` row was added after every GPT one, and a pool with
        # sixty free GPT accounts in it showed "none free" for both of
        # the kinds this card was built for (2026-09-19).
        choose["apps"] = store._rows(
            "SELECT label, product, credential_kind, category FROM ("
            "  SELECT address AS label, coalesce(product, '') AS product,"
            "         coalesce(credential_kind, '') AS credential_kind,"
            "         coalesce(category, '') AS category,"
            "         row_number() OVER ("
            "           PARTITION BY coalesce(product, ''),"
            "                        coalesce(category, '')"
            "           ORDER BY sheet_row NULLS LAST, id) AS seat"
            "  FROM resources"
            "  WHERE kind = 'app' AND status = '' AND error IS NULL"
            "    AND address <> '') AS free"
            " WHERE seat <= 60 ORDER BY product, category, seat")
        choose["proxies"] = store._rows(
            "SELECT proxy_name AS label FROM resources"
            " WHERE kind = 'proxy' AND error IS NULL"
            "   AND lower(status) IN ('', 'free', 'unused')"
            "   AND proxy_name <> ''"
            " ORDER BY times_used, sheet_row NULLS LAST LIMIT 60")
        # Credentials a run judged and set aside, with the word it used.
        # Only what is still on a tab: a row that left is history, and
        # nobody has a decision to make about history.
        stopped = store._rows(
            "SELECT kind, address AS who, status, serial, note"
            " FROM resources"
            " WHERE kind IN ('gmail', 'app') AND error IS NULL"
            "   AND NOT (status = ANY(%s)) AND status <> %s"
            " ORDER BY updated_at DESC, id DESC LIMIT 12",
            (sorted(ROUTINE["gmail"] | ROUTINE["app"]), IMPORTED))
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
            " coalesce(u.username, c.name, '?') AS requested_by"
            " FROM actions a LEFT JOIN users u ON u.id = a.requested_by"
            " LEFT JOIN api_clients c ON c.id = a.client_id"
            " ORDER BY a.id DESC LIMIT 2")
        # A phone somebody asked for by hand, from the moment they ask.
        # `wanted_builds` was read by nothing in the whole web package, so
        # a press vanished: the toast is gone in four seconds, the table
        # does not change until a phone exists, and a wish that failed
        # before one did wrote its reason into a column nobody could see
        # (2026-09-07). Inlined rather than `wanted.recent`, because the
        # whole page is one connection.
        wishes = store._rows(
            "SELECT w.id, w.gmail, w.proxy_name, w.install_app, w.app,"
            " w.app_account, w.status, w.serial, w.detail, w.created_at,"
            " w.ended_at, w.no_gmail, w.requested_by,"
            " coalesce(u.username, '') AS asked_by"
            " FROM wanted_builds w LEFT JOIN users u"
            "   ON u.id = w.requested_by"
            " WHERE w.status IN ('queued', 'running')"
            # A wish that failed stays - as a row of the phones table
            # saying which address and why - until whoever asked
            # dismisses it (the build card, 2026-09-10). It used to leave
            # by itself after fifteen minutes, and a build that failed
            # while the operator was at lunch left no trace.
            # A day, not forever: the column arrived after weeks of
            # wishes, and every failure since June stood up at once
            # (the operator, 2026-09-10). Yesterday's is not news.
            "    OR (w.status = 'failed' AND w.dismissed_at IS NULL"
            "        AND coalesce(w.ended_at, w.created_at)"
            "            > now() - interval '24 hours'"
            # A failed wish whose phone is on the shelf is not a row of
            # its own: the phone's row says everything (2026-09-11).
            "        AND NOT EXISTS (SELECT 1 FROM phones p"
            "                        WHERE p.serial = w.serial"
            "                          AND p.done_at IS NULL))"
            " ORDER BY w.id DESC LIMIT 12")
        pulse = store._rows(
            "SELECT value FROM service_state WHERE key = 'pass'")
        # Which builds somebody has pressed Cancel on. The press was
        # written and the row went on saying Building until the build
        # actually died - minutes later, with nothing on the page to
        # show the press had landed, so it was pressed again (the
        # operator, 2026-09-21). Read on this connection: it is one row.
        asked_stops = store._rows(
            "SELECT value FROM service_state WHERE key = %s",
            (store_stops.KEY,))
        # The manager's lists, read on the same connection the rest of
        # this page uses: the cards need the free rows anyway, and the
        # whole page is one response.
        pools_listed = _card_rows(store)
    folded = {kind: dict.fromkeys(names, 0) for kind, names in _FOLD.items()}
    for row in stock:
        names = _FOLD.get(row["kind"])
        if not names:
            continue
        for name, words in names.items():
            if row["status"] in words:
                folded[row["kind"]][name] += row["c"]
    counted = waiting[0] if waiting else {"all_of_them": 0, "panel": 0}
    folded["app"] = {
        "awaiting": int(counted["all_of_them"] or 0),
        "panel": int(counted["panel"] or 0),
        "manual": int(counted["all_of_them"] or 0) - int(counted["panel"] or 0),
    }
    return {
        "phones": phone_rows,
        "progress": progress,
        "live": live,
        "stops_asked": sorted(store_stops.live(
            asked_stops[0]["value"] if asked_stops else {})),
        "stock": folded,
        "pool_rows": pools_listed,
        "spotify": {str(r["category"] or ""): int(r["c"] or 0)
                    for r in spotify_free},
        "awaiting": awaiting,
        "stopped": stopped,
        "choose": choose,
        "geelark": _geelark(store, (pulse[0]["value"] or {})
                            if pulse else {}),
        "queue": queue[0] if queue else {"running": 0, "queued": 0},
        "recent": recent,
        "asked": asked,
        "wishes": wishes,
        "pulse": (pulse[0]["value"] or {}) if pulse else {},
    }


#: Slots left below which building is about to stop.
SLOTS_LOW = 3

#: How long a refusal that is nobody's emergency stays worth a word.
#:
#: Only that kind is on a clock. Whether a refusal is still TRUE is not
#: a question about time at all: a refusal is the last word on creating
#: phones until a phone is created, and nothing else retires it. An
#: hour was both too short and too long on the same day - it dropped an
#: empty account that was still empty, and it went on saying `out of
#: credit` three minutes after forty-seven phones had been built
#: (2026-09-20).
REFUSAL_SHOWN_FOR = 3600.0

#: What GeeLark says when the account has run out of money.
#:
#: Everything else it turns a phone down for wears exactly the same
#: shape and is a different problem: [45004] is a proxy it could not
#: check, [44002] is the last profile slot. Reading them all as "no
#: money" is what put `out of credit` on the console, in red, while the
#: farm was building normally (the operator, 2026-09-20).
NO_MONEY_CODES = frozenset({41001})
NO_MONEY_WORDS = ("balance not enough", "not enough balance",
                  "insufficient balance", "insufficient funds")


def _is_about_money(refused: dict) -> bool:
    """Whether GeeLark's own words say the account has run out.

    The words as well as the code, because rows written before the code
    was kept apart have only the sentence.
    """
    if int(refused.get("code") or 0) in NO_MONEY_CODES:
        return True
    said = " ".join((f"{refused.get('said') or ''} "
                     f"{refused.get('msg') or ''} "
                     f"{refused.get('raw') or ''}").split()).casefold()
    return (any(word in said for word in NO_MONEY_WORDS)
            or any(f"[{code}]" in said for code in NO_MONEY_CODES))


def _refusal_words(refused: dict) -> str:
    """GeeLark's reason, as short as it comes: `[45004] check proxy
    failed`. The whole payload is in the log, and on rows written
    before it was cut up, in `said`."""
    code, msg = refused.get("code"), str(refused.get("msg") or "").strip()
    if code and msg:
        return f"[{int(code)}] {msg}"
    return " ".join(str(refused.get("said") or "").split())[:60]


def _nothing_built_since(at, built_at) -> bool:
    """Whether no phone has been created since that moment - the one
    thing that retires a refusal."""
    if built_at is None:
        return True
    try:
        when = float(at)
    except (TypeError, ValueError):
        return False
    if isinstance(built_at, (int, float)):
        return float(built_at) <= when
    if isinstance(built_at, datetime.datetime):
        made = built_at
        if made.tzinfo is None:
            made = made.replace(tzinfo=datetime.timezone.utc)
        return made.timestamp() <= when
    return True

#: When to start saying the subscription is nearly over.
PLAN_WARN_DAYS = 10

#: How old the keeper's reading of the plan may be before the foot of
#: the page says so. `serve.PLAN_EVERY_SECONDS` is five minutes; three
#: of those missed means the keeper is not running, and then every
#: number below it is a memory rather than a reading.
READING_STALE_AFTER = 900.0


def geelark_trouble(plan: dict, refused: dict, pulse: dict,
                    built_at=None) -> list[dict]:
    """Every way GeeLark itself is stopping the farm, judged once.

    One list, two renderings: `short` goes on the line at the foot of
    the page and `text` on the alert strip at the top. They come from
    here together so the two cannot disagree - and the disagreement
    that mattered was silence. The foot showed nothing but grey while
    the account had no money in it, and anybody glancing at it came
    away thinking GeeLark was fine (the operator, 2026-09-20).

    `pulse` is read as well as the refusal, because a refusal is only
    recorded when a build tries to start a phone - and once the breaker
    has tripped, nothing tries. The state would go quiet exactly when
    it had just become true.

    `built_at` is when a phone was last created, and it is what retires
    a refusal: GeeLark turning one down is the last word on creating
    phones until a phone is created. Only a refusal GeeLark's own words
    call a money one is `out of credit`; the rest are a problem of
    their own and say which.
    """
    found: list[dict] = []
    plan = plan or {}
    refused = refused or {}
    pulse = pulse or {}

    said = str(refused.get("said") or "")
    at = refused.get("at")
    # Live, not fresh: the refusal is still the last word about creating
    # phones because no phone has been created since it.
    live = bool(said and at and _nothing_built_since(at, built_at))
    fresh = bool(live and (time.time() - float(at)) < REFUSAL_SHOWN_FOR)
    money = live and _is_about_money(refused)
    # The breaker tripped on reasons that are nobody's stock is the same
    # news arrived a different way: while it is up no build tries, so no
    # fresher refusal can come.
    stalled = _breaker_blames_geelark(pulse)
    why = _refusal_words(refused) if live else ""

    if money:
        found.append({
            "kind": "refused", "level": "bad",
            "href": "/events?kind=builds",
            "short": "phones will not start",
            # GeeLark's own words, short enough to print under the
            # reading they explain rather than only in the paragraph.
            "detail": why,
            "text": (f"GeeLark will not start phones - {why}. Nothing can "
                     f"be built or signed in until the account is topped "
                     f"up; the API does not report the balance, so this "
                     f"refusal is the only warning there is.")})
    elif stalled or fresh:
        # A refusal that is not about money. On its own it is one build
        # among many and worth a word, not a colour that means stop -
        # unless the breaker is up, in which case nothing is being built
        # at all and it is the reason why.
        told = why or "no reason on file"
        found.append({
            "kind": "blocked", "level": "bad" if stalled else "warn",
            "href": "/events?kind=builds", "stalled": stalled,
            "short": ("building has stopped" if stalled
                      else "a phone was refused"),
            "detail": told, "msg": str(refused.get("msg") or ""),
            "code": refused.get("code"),
            "text": ((f"Building has stopped: the breaker is up on "
                      f"refusals no pool can fix, and the last thing "
                      f"GeeLark said was {told}. Deal with that, then "
                      f"clear the breaker.") if stalled else
                     (f"GeeLark turned a phone down - {told}. Nothing "
                      f"has been created since, so this is still the "
                      f"last word on it; the next phone that comes up "
                      f"clears it."))})

    free = plan.get("availableProfiles")
    if free is not None and int(free) <= SLOTS_LOW:
        total = int(plan.get("profiles") or 0)
        found.append({
            # The foot of the page has a reading of its own for this,
            # and colours it from the level here; `short` is the wording
            # it falls back on if that reading is ever dropped.
            "kind": "slots",
            "level": "bad" if int(free) == 0 else "warn", "href": "/phones",
            "short": ("no phone slots left" if int(free) == 0
                      else f"{free} phone slots left"),
            "text": (f"GeeLark has {free} of {total} phone "
                     f"{'slot' if int(free) == 1 else 'slots'} left. "
                     f"Creating another fails with [44002]; delete phones "
                     f"that are done, or raise the plan.")})

    ends = plan.get("expirationTime")
    if ends:
        days = (datetime.datetime.fromtimestamp(int(ends),
                                                datetime.timezone.utc)
                - datetime.datetime.now(datetime.timezone.utc)).days
        if days <= PLAN_WARN_DAYS:
            gone = "has expired" if days < 0 else f"ends in {days} day(s)"
            found.append({
                # As above: the foot prints the date and colours it
                # when it is near.
                "kind": "plan",
                "level": "bad" if days <= 2 else "warn", "href": "/",
                "short": f"the subscription {gone}",
                "text": (f"The GeeLark subscription {gone}. Every phone "
                         f"goes with it.")})
    return found


def _breaker_blames_geelark(pulse: dict) -> bool:
    """Whether building has stopped for reasons no pool can fix."""
    from ..failures import CREDENTIAL, verdict

    reasons = [str(r) for r in (pulse.get("breaker_reasons") or [])]
    if not pulse.get("tripped") or not reasons:
        return False
    return all(verdict(r).blame != CREDENTIAL for r in reasons)


def esc_text(value: str) -> str:
    """The little of `html.escape` this module needs - it renders no
    markup of its own, but `short` is dropped into some."""
    from html import escape

    return escape(str(value))


def geelark_alerts(counts: dict) -> list[dict]:
    """The trouble above, worded for the strip at the top of the page."""
    kept = counts.get("geelark_plan") or {}
    return [{"level": t["level"], "href": t["href"], "text": t["text"]}
            for t in geelark_trouble(kept.get("plan") or {},
                                     counts.get("geelark_refusal") or {},
                                     counts.get("pulse") or {},
                                     counts.get("phone_built_at"))]


def _geelark(store, pulse: dict | None = None) -> dict:
    """What GeeLark says about the account, as the keeper last heard it.

    Read from the store and never from the API: a page render that
    reaches somebody else's network is a page that hangs, and the plan
    endpoint allows one call a minute for the whole account. The keeper
    calls it every few minutes anyway and leaves the answer here.

    There is no balance in it. `/v1/pay/plan/info` carries the slots and
    the expiry and nothing about money, and no other endpoint answers
    (probed, 2026-09-20) - so the only thing that reports an empty
    account is a phone being refused, which is `refusal`.
    """
    rows = store._rows(
        "SELECT key, value FROM service_state"
        " WHERE key IN ('geelark_plan', 'geelark_refusal')")
    found = {r["key"]: r["value"] for r in rows}
    kept = found.get("geelark_plan") or {}
    refused = found.get("geelark_refusal") or {}
    # What the farm itself is using of the account, so the line can say
    # how much of the slot pool is ours and how much is somebody's
    # browser profiles - the question a full pool always raises.
    mine = store._rows(
        "SELECT count(*) AS total,"
        " count(*) FILTER (WHERE running) AS running,"
        # Over every phone, not just the ones still held: a refusal is
        # retired by GeeLark making a phone, whoever has it now.
        " (SELECT max(created_at) FROM phones) AS built_at"
        " FROM phones WHERE done_at IS NULL")
    counted = dict(mine[0]) if mine else {"total": 0, "running": 0}
    return {"plan": kept.get("plan") or {}, "at": kept.get("at"),
            "refusal": refused.get("said") or "",
            "refused_at": refused.get("at"),
            # Judged here, so the line at the foot says exactly what the
            # strip at the top does.
            "trouble": geelark_trouble(kept.get("plan") or {}, refused,
                                       pulse, counted.get("built_at")),
            "phones_total": int(counted.get("total") or 0),
            "phones_running": int(counted.get("running") or 0)}


def _latest_lines(store, serials: list[str]) -> dict[str, dict]:
    """The newest line captured for each of these phones, and when its
    run's first line landed - so a row can say what the phone is doing
    and for how long, without the sheet. Empty when nothing was asked."""
    if not serials:
        return {}
    # Bound to this phone's own life: lines since its row was opened. It
    # took the newest line for the serial with no time about it, so a
    # build ten seconds old showed yesterday's failure sentence and four
    # hours of elapsed time (2026-09-07). The fix then joined the
    # `claims` table - which nothing has ever written a row to, so every
    # building row read "starting" for three days (2026-09-10). A serial
    # is one phone, and a phone's row is opened once, when it is
    # created: that moment is the start, and nothing before it is this
    # build's.
    #
    # A serial whose build has said nothing yet returns nothing, and
    # `_progress` falls through to a dim "starting", which is the truth.
    lines = store._rows(
        "SELECT l.serial, l.logger, l.msg, l.at, l.run, p.created_at AS started"
        " FROM logs l"
        " JOIN phones p ON p.serial = l.serial AND p.done_at IS NULL"
        "   AND l.at >= p.created_at"
        " WHERE l.serial = ANY(%s)"
        "   AND l.id = (SELECT max(m.id) FROM logs m"
        "               WHERE m.serial = l.serial"
        "                 AND m.at >= p.created_at)", (list(serials),))
    return {str(r["serial"]): r for r in lines}


def _live_links(store, serials: list[str]) -> dict[str, str]:
    """The live-view link of each phone being built, by serial.

    The builder logs it the moment GeeLark answers the start call - once
    per start, so after an exit swap the newest line of the phone's
    life is the screen as it is now. Bound to the phone's row the way
    `_latest_lines` is, for the same reason: yesterday's link on
    today's build is a tab that opens on nothing."""
    if not serials:
        return {}
    lines = store._rows(
        "SELECT l.serial, l.msg FROM logs l"
        " JOIN phones p ON p.serial = l.serial AND p.done_at IS NULL"
        "   AND l.at >= p.created_at"
        " WHERE l.serial = ANY(%s) AND l.msg LIKE 'watch it live: %%'"
        "   AND l.id = (SELECT max(m.id) FROM logs m"
        "               WHERE m.serial = l.serial"
        "                 AND m.at >= p.created_at"
        "                 AND m.msg LIKE 'watch it live: %%')", (list(serials),))
    out = {}
    for r in lines:
        url = str(r["msg"] or "")[len("watch it live: "):].strip()
        if url.startswith("https://"):
            out[str(r["serial"])] = url
    return out


def latest_lines(settings: Settings, serials: list[str]) -> dict[str, dict]:
    """The dashboard's building-row feed, for any list of serials: the
    Requests page reads it for the phones a running login is working."""
    if not serials:
        return {}
    with Store(settings) as store:
        return _latest_lines(store, [str(s) for s in serials])


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
                  " r.password, r.totp_secret, r.recovery_email,"
                  " r.totp_secret <> '' AS has_totp,"
                  " r.recovery_email <> '' AS has_recovery, r.source,"
                  # What the errored list is really made of: rows on their
                  # way back into the queue, and rows that are money the
                  # seller owes. They used to read the same (2026-09-12).
                  " r.tries, r.retry_after, r.last_host, r.refund_state")


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


#: The Gmail Pool's four views, and the count each pill shows.
GMAIL_VIEWS = {"queued": "queued", "on_phone": "on_phone", "used": "used",
               "errored": "errored"}


#: How many rows of one pool the dashboard's manager will hold. Well past
#: what a live pool ever carries, and a ceiling rather than a page: this
#: list is searched in the browser, and a search that quietly stops at a
#: boundary is worse than one that says it stopped.
POOL_LIMIT = 300

#: What the three cards in the rail draw: one line per FREE row, and the
#: count of everything else so the card can say "Nothing free. N rows
#: held or set aside." No credential, no spent half, no row nobody is
#: going to see - `_pool_queue` renders only the free ones, and the
#: dashboard was reading and shipping fourteen hundred rows with their
#: passwords to draw twenty-two list items (2026-09-20).
def _card_rows(store) -> dict:
    """The free rows of each pool, and how many are not free."""
    free = store._rows(
        "SELECT CASE WHEN kind = 'app' AND " + IS_SPOTIFY + " THEN 'spotify'"
        "   WHEN kind = 'app' THEN 'gpt' ELSE kind END AS pool,"
        " coalesce(nullif(address, ''), proxy_name, '') AS address,"
        " coalesce(seller, '') AS seller,"
        " coalesce(category, '') AS category,"
        " coalesce(host, '') AS host, port"
        " FROM resources"
        " WHERE kind IN ('gmail', 'app', 'proxy')"
        "   AND coalesce(status, '') IN ('', 'free', 'unused')"
        "   AND error IS NULL AND coalesce(address, proxy_name, '') <> ''"
        " ORDER BY id DESC LIMIT %s", (POOL_LIMIT * 2,))
    held = store._rows(
        "SELECT CASE WHEN kind = 'app' AND " + IS_SPOTIFY + " THEN 'spotify'"
        "   WHEN kind = 'app' THEN 'gpt' ELSE kind END AS pool, count(*) AS n"
        " FROM resources"
        " WHERE kind IN ('gmail', 'app', 'proxy')"
        "   AND NOT (kind = 'gmail' AND status = 'used')"
        "   AND NOT (kind = 'app' AND status = 'delivered')"
        "   AND NOT (coalesce(status, '') IN ('', 'free', 'unused')"
        "            AND error IS NULL)"
        " GROUP BY 1")
    out = {k: [] for k in ("gmail", "gpt", "spotify", "proxy")}
    for row in free:
        pool = str(row.pop("pool"))
        if pool in out:
            out[pool].append(dict(row, state="free"))
    # The held ones as placeholders, not as rows: `_pool_queue` only ever
    # counts them, and a card that says "Nothing free. 40 rows held" must
    # not be handed forty addresses to say it.
    for row in held:
        pool = str(row["pool"])
        if pool in out:
            out[pool] += [{"state": "on a phone"}] * int(row["n"] or 0)
    return out


def pool_rows(settings: Settings) -> dict:
    with Store(settings) as store:
        return _pool_rows(store)


#: The columns the manager's editor opens with. The password and the
#: second factor in clear: an editor that hides what it holds is a form
#: for retyping, not for correcting, and the operator asked to *see* the
#: key so they can tell a wrong one from a right one (2026-09-08). The
#: preview already shows both, to the same people.
_HELD = (" coalesce(password, '') AS password,"
         " coalesce(nullif(totp_secret, ''), recovery_email, '') AS secret,"
         # And which kind it is, as a word, for the column.
         " CASE WHEN coalesce(totp_secret, '') <> '' THEN 'authenticator'"
         "      WHEN coalesce(recovery_email, '') <> '' THEN 'recovery'"
         "      ELSE '' END AS second")

#: The status that means a row is finished with, per pool. Proxies have
#: none: an exit goes back on the shelf.
_SPENT = {"gmail": "used", "app": "delivered"}

#: The app pool holds two products in one table: the GPT accounts and,
#: since 2026-09-17, the Spotify ones. `product` is what tells them
#: apart - the column the panel API already used - so every count and
#: every list about one of them says which, or the GPT card counts the
#: Spotify rows and the build card offers one to ChatGPT.
IS_SPOTIFY = "coalesce(product, '') = 'spotify'"
NOT_SPOTIFY = "coalesce(product, '') <> 'spotify'"


def pool_sheet(settings: Settings, kind: str) -> dict:
    """One pool's rows, for the sheet the manager fetches when it opens.

    The manager used to be rendered shut inside every dashboard - 925,488
    of the page's 1,012,694 bytes, with ~500 passwords and TOTP secrets
    in its data attributes, on the 99 responses in 100 where nobody
    opened it (2026-09-20). It is a drawer; it is read when it is pulled.
    """
    with Store(settings) as store:
        return _pool_rows(store, kinds=(kind,))


def _pool_rows(store, kinds: tuple[str, ...] | None = None) -> dict:
    """Every row of the three pools, for the manager the dashboard opens.

    The live rows - free, on a phone, set aside, and everything a run
    refused - and then the spent ones: a `used` Gmail, a `delivered`
    account. Spent rows were left out as "the archive" until the operator
    asked for the pool in three views, spent among them (2026-09-08).
    They are read under their own cap, so a thousand used Gmails can
    never push the batch pasted a minute ago off the bottom of the list.

    Newest first in both: the cap cuts the tail, and the tail must not be
    what somebody just added (2026-09-07).
    """
    gmail = ("SELECT id, address, status, coalesce(seller, '') AS seller,"
             " coalesce(note, '') AS note, error, updated_at,"
             f" coalesce(serial, '') AS serial,{_HELD}"
             " FROM resources WHERE kind = 'gmail' AND status {op} 'used'"
             " ORDER BY id DESC LIMIT %s")
    # The kind rides with a GPT row too since 2026-09-19: `eco` accounts
    # sign in by a code emailed to an address the farm owns, and the
    # manager sifts them apart from the ones with a password.
    # What kind of account each row is, not just its address. Until
    # 2026-09-19 neither product nor credential_kind was selected, so a
    # Claude account, an eco one and a password one were four columns of
    # the same thing and the console could not tell them apart - on the
    # day the panel starts sending all six kinds through one door.
    _WHAT = (" coalesce(product, '') AS product,"
             " coalesce(credential_kind, '') AS credential_kind,"
             " coalesce(category, '') AS category,"
             " coalesce(panel_ref, '') AS panel_ref,"
             " coalesce(customer_ready, false) AS customer_ready,")
    gpt = ("SELECT id, address, status, coalesce(serial, '') AS serial,"
           f"{_WHAT}"
           f" coalesce(note, '') AS note, error, updated_at,{_HELD}"
           " FROM resources WHERE kind = 'app' AND status {op} 'delivered'"
           f"   AND {NOT_SPOTIFY}"
           " ORDER BY id DESC LIMIT %s")
    spotify = ("SELECT id, address, status, coalesce(serial, '') AS serial,"
               f"{_WHAT}"
               f" coalesce(note, '') AS note, error, updated_at,{_HELD}"
               " FROM resources WHERE kind = 'app' AND status {op} 'delivered'"
               f"   AND {IS_SPOTIFY}"
               " ORDER BY id DESC LIMIT %s")
    def asked(kind: str) -> bool:
        return kinds is None or kind in kinds

    rows = {
            "gmail": (store._rows(gmail.format(op="<>"), (POOL_LIMIT,))
                      + store._rows(gmail.format(op="="), (POOL_LIMIT,)))
                     if asked("gmail") else [],
            "gpt": (store._rows(gpt.format(op="<>"), (POOL_LIMIT,))
                    + store._rows(gpt.format(op="="), (POOL_LIMIT,)))
                   if asked("gpt") else [],
            "spotify": (store._rows(spotify.format(op="<>"), (POOL_LIMIT,))
                        + store._rows(spotify.format(op="="),
                                      (POOL_LIMIT,)))
                       if asked("spotify") else [],
            "proxy": store._rows(
                "SELECT id, coalesce(proxy_name, '') AS address, status,"
                " coalesce(host, '') AS host, port,"
                " coalesce(last_exit_ip, '') AS exit_ip, times_used,"
                " coalesce(serial, '') AS serial, coalesce(note, '') AS note,"
                " error, updated_at, claimed_at"
                " FROM resources WHERE kind = 'proxy'"
                # By the number in the name, which is the order the
                # vendor's own panel lists them in and the order a person
                # works down when they are changing addresses. It was by
                # `times_used`, the builder's order, which shuffled the
                # list under the hand using it (the operator, 2026-09-14).
                " ORDER BY nullif(regexp_replace(coalesce(proxy_name, ''),"
                "                 '[^0-9]', '', 'g'), '')::bigint NULLS LAST,"
                "          proxy_name, id LIMIT %s",
                (POOL_LIMIT,)) if asked("proxy") else [],
    }
    # How many there are, against how many are drawn, live and spent
    # apart since each has its own cap. The cap was silent, so an address
    # that happened to be the 340th row answered "Nothing matches that" to
    # a search that had never looked at it (2026-09-07).
    # The Spotify rows are counted as their own pool: they share the
    # table with the GPT accounts but have their own sheet and cap.
    totals = store._rows(
        f"SELECT CASE WHEN kind = 'app' AND {IS_SPOTIFY} THEN 'spotify'"
        "   ELSE kind END AS kind,"
        " count(*) FILTER (WHERE NOT (kind = 'gmail' AND status = 'used')"
        "   AND NOT (kind = 'app' AND status = 'delivered')) AS live,"
        " count(*) FILTER (WHERE (kind = 'gmail' AND status = 'used')"
        "   OR (kind = 'app' AND status = 'delivered')) AS spent"
        " FROM resources WHERE kind IN ('gmail', 'app', 'proxy')"
        " GROUP BY 1")
    counted = {str(r["kind"]): {"live": int(r["live"] or 0),
                                "spent": int(r["spent"] or 0)}
               for r in totals}
    rows["totals"] = {"gmail": counted.get("gmail", {"live": 0, "spent": 0}),
                      "gpt": counted.get("app", {"live": 0, "spent": 0}),
                      "spotify": counted.get("spotify",
                                             {"live": 0, "spent": 0}),
                      "proxy": counted.get("proxy", {"live": 0, "spent": 0})}
    for kind, listed in rows.items():
        if kind == "totals":
            continue
        for row in listed:
            # One word for what the row is, whatever column carried it.
            # An unreadable row is `broken` whatever its status says -
            # that is the thing about it a person has to act on.
            row["state"] = _pool_state(kind, row)
    return rows


#: What a pool row's status means, in the one word the manager sorts and
#: filters by. The pool pages spell the same four as views; this is the
#: same decision, taken once, for a list that shows every view at once.
def _pool_state(kind: str, row: dict) -> str:
    if row.get("error"):
        return "broken"
    status = (row.get("status") or "").strip().lower()
    if kind == "proxy":
        # The Proxy tab's words, said the way the sheet said them. `claimed`
        # is a build that took the exit seconds ago and has no phone yet -
        # it read as an error on the sheet and was nothing of the kind
        # (the operator, 2026-09-09); `change ip` is the vendor's word for
        # an exit that wants a new address before it is used again.
        if status in ("", "free", "unused"):
            return "free"
        if status in ("in_use", "on a phone"):
            return "on a phone"
        if status == "claimed":
            return "starting"
        if status == "change ip":
            return "needs new IP"
        return status
    if status == "":
        return "free"
    if status in ("in_use", "ready"):
        return "on a phone"
    return status


def gmail_pool(settings: Settings, view: str = "queued",
               seller: str = "", page: int = 1, per_page: int = 100) -> dict:
    """The Gmail Pool page, one list at a time.

    Four views, one table each: `queued` is the stock the keeper claims
    from and the page's front door, `on_phone` is what is signed in right
    now, `used` is history and `errored` is the list the seller is asked
    to refund. Every view pages; the refund list itself comes whole from
    `errored_addresses`, because a list cut at a page boundary is a
    refund never asked for.
    """
    view = GMAIL_VIEWS.get(view, "queued")
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or 1))
    with Store(settings) as store:
        counts = store._rows(
            "SELECT"
            " count(*) FILTER (WHERE status = ''"
            "   AND error IS NULL) AS queued,"
            " count(*) FILTER (WHERE status IN ('in_use', 'ready')) AS on_phone,"
            " count(*) FILTER (WHERE status = 'used') AS used,"
            " count(*) FILTER (WHERE error IS NULL"
            "   AND NOT (status = ANY(%s)) AND status <> %s) AS errored,"
            " count(*) FILTER (WHERE refund_state = 'to_claim') AS owed,"
            " count(*) FILTER (WHERE refund_state = 'claimed') AS refunded,"
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
        out = {"view": view, "counts": counts, "sellers": sellers,
               "known_sellers": known, "seller": seller, "page": page}
        skip = (page - 1) * per_page
        if view == "queued":
            rows = store._rows(
                f"SELECT {_GMAIL_COLUMNS} FROM resources r"
                " WHERE r.kind = 'gmail' AND r.status = ''"
                "   AND r.error IS NULL"
                " ORDER BY r.sheet_row NULLS LAST, r.id"
                " LIMIT %s OFFSET %s", (per_page + 1, skip))
            total = counts["queued"]
        elif view == "on_phone":
            rows = store._rows(
                f"SELECT {_GMAIL_COLUMNS}, p.status AS phone_status"
                " FROM resources r LEFT JOIN phones p"
                "   ON p.serial = r.serial AND p.done_at IS NULL"
                " WHERE r.kind = 'gmail' AND r.status IN ('in_use', 'ready')"
                " ORDER BY r.updated_at DESC LIMIT %s OFFSET %s",
                (per_page + 1, skip))
            total = counts["on_phone"]
        elif view == "used":
            rows = store._rows(
                f"SELECT {_GMAIL_COLUMNS} FROM resources r"
                " WHERE r.kind = 'gmail' AND r.status = 'used'"
                " ORDER BY r.updated_at DESC LIMIT %s OFFSET %s",
                (per_page + 1, skip))
            total = counts["used"]
        else:
            wanted = seller.lower()
            out["reasons"] = store._rows(
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
                 per_page + 1, skip))
            total = sum(int(r["c"]) for r in out["reasons"])
            # The rows validation refused live here too: they are not
            # stock, nobody can use them, and the seller hears about
            # them in the same breath as the ones Google refused.
            out["broken"] = store._rows(
                "SELECT id, address, error FROM resources"
                " WHERE kind = 'gmail' AND error IS NOT NULL ORDER BY id")
    out.update(rows=rows[:per_page], more=len(rows) > per_page, total=total,
               pages=_pages(total, per_page))
    return out


def errored_addresses(settings: Settings, seller: str = "") -> list[str]:
    """Every address the seller owes for, one seller's or everyone's, with
    no page cap: a list cut at a page boundary is a refund never asked
    for.

    The rows marked `to_claim` and no others. Until 2026-09-12 this was
    every errored address, which put rows that come back on their own -
    a captcha, a refusal - in front of a seller as though they were
    broken; the two piles read the same and only one of them is money.
    """
    wanted = seller.lower()
    with Store(settings) as store:
        rows = store._rows(
            "SELECT address FROM resources"
            " WHERE kind = 'gmail' AND error IS NULL AND address IS NOT NULL"
            " AND refund_state = 'to_claim'"
            " AND NOT (status = ANY(%s)) AND status <> %s"
            " AND (%s = '' OR lower(seller) = %s)"
            " ORDER BY updated_at DESC, id",
            (sorted(ROUTINE["gmail"]), IMPORTED, wanted, wanted))
    return [r["address"] for r in rows if r["address"]]


#: The Proxy Pool's four views, and the count each pill shows.
PROXY_VIEWS = {"free": "free", "on_phone": "on_phone",
               "needs_hand": "needs_hand", "all": "all"}


def proxy_bucket(status) -> str:
    """The one word a status files under. `claimed` sits with `on a phone`
    because both mean an exit a build is holding; anything the pool never
    wrote is `other`, which only the All view shows."""
    word = str(status or "").lower()
    if word in ("", "free", "unused"):
        return "free"
    if word in ("on a phone", "claimed"):
        return "on_phone"
    if word == "change ip":
        return "needs_new_ip"
    if word == "dead":
        return "dead"
    return "other"


def proxy_pool(settings: Settings, view: str = "free", q: str = "",
               page: int = 1, per_page: int = 50,
               unlisted: list | None = None) -> dict:
    """The Proxy Pool page, one list at a time.

    Four views, one table each: `free` is the stock a build takes and the
    page's front door, `on_phone` is what a build is holding, `needs_hand`
    is every kind of trouble in one list - an exit that wants a new IP, a
    dead one, and the exits GeeLark holds that the pool never heard of -
    and `all` is the escape hatch, searchable.

    `unlisted` is what the last pass found GeeLark holding; the pass keeps
    it in service_state and the caller passes it in, so this module stays
    a reader of the resources table alone. The pool is one row per phone,
    so every row comes back in one query and the views are cut from it -
    four queries would cost more than the whole table.
    """
    view = PROXY_VIEWS.get(view, "free")
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or 1))
    strays = list(unlisted or [])
    with Store(settings) as store:
        rows = store._rows(
            "SELECT r.id, r.proxy_name AS name, r.host, r.port, r.username,"
            " r.status, r.serial, r.last_exit_ip, r.times_used, r.note,"
            " r.updated_at, r.error"
            " FROM resources r WHERE r.kind = 'proxy'"
            " ORDER BY r.sheet_row NULLS LAST, r.id")
    buckets: dict[str, list] = {}
    for r in rows:
        r["bucket"] = proxy_bucket(r["status"])
        buckets.setdefault(r["bucket"], []).append(r)
    trouble = buckets.get("needs_new_ip", []) + buckets.get("dead", [])
    counts = {"free": len(buckets.get("free", [])),
              "on_phone": len(buckets.get("on_phone", [])),
              "needs_new_ip": len(buckets.get("needs_new_ip", [])),
              "dead": len(buckets.get("dead", [])),
              "strays": len(strays),
              "needs_hand": len(trouble) + len(strays),
              "all": len(rows)}
    out = {"view": view, "counts": counts, "q": q, "page": page,
           "strays": [], "more": False, "pages": 1}
    if view == "needs_hand":
        # Never paged: this is the work list, and a page boundary through
        # it is a job nobody sees.
        out.update(rows=trouble, strays=strays,
                   total=counts["needs_hand"])
        return out
    if view == "all":
        wanted = rows
        if q.strip():
            needle = q.strip().lower()
            wanted = [r for r in rows if needle in
                      f"{r['name'] or ''} {r['host'] or ''} "
                      f"{r['serial'] or ''}".lower()]
    else:
        wanted = buckets.get(view, [])
    skip = (page - 1) * per_page
    out.update(rows=wanted[skip:skip + per_page], total=len(wanted),
               more=len(wanted) > skip + per_page,
               pages=_pages(len(wanted), per_page))
    return out


_APP_COLUMNS = ("r.id, r.address, r.status, r.serial, r.source, r.added_by,"
                " r.note, r.updated_at, r.created_at, r.email_code_only,"
                " r.totp_secret <> '' AS has_totp")


#: The delivered archive's filter, shared by the page, its count and the
#: CSV: a search word matches the address, the phone's serial or the note.
#: Four parameters: the word itself (empty means everything), then the
#: ILIKE pattern three times.
_DELIVERED_MATCH = (f"r.kind = 'app' AND r.status = 'delivered' AND {NOT_SPOTIFY}"
                    " AND (%s = '' OR r.address ILIKE %s OR r.serial ILIKE %s"
                    "   OR r.note ILIKE %s)")


def delivered_rows(settings: Settings, q: str = "") -> list[dict]:
    """The whole delivered archive that matches `q`, uncapped, for the CSV
    export: address, serial, when it went out and where it came from.

    `delivered_at` is the stamp the hand-over writes (pgpool.PgAppPool),
    and `updated_at` behind it for every row delivered before that column
    was written - any later edit moves that one, so it answers "when did
    this row last change" and not the question the column is named for
    (2026-09-12).
    """
    like = f"%{q.strip()}%"
    with Store(settings) as store:
        return store._rows(
            "SELECT r.address, r.serial, r.updated_at, r.delivered_at,"
            " r.source"
            f" FROM resources r WHERE {_DELIVERED_MATCH}"
            " ORDER BY coalesce(r.delivered_at, r.updated_at) DESC, r.id DESC",
            (q.strip(), like, like, like))


#: The Gpt Pool's four views, and the count each pill shows.
GPT_VIEWS = {"waiting": "waiting", "on_phone": "on_phone",
             "needs_human": "needs_human", "delivered": "delivered"}


def gpt_pool(settings: Settings, view: str = "waiting", q: str = "",
             page: int = 1, per_page: int = 50) -> dict:
    """The Gpt Pool page, one list at a time.

    Four views, one table each: `waiting` is the front door - every
    account that has no phone yet, in the order the keeper claims them;
    `on_phone` is what is signing in now or signed in and waiting to go
    out; `needs_human` is what a run set aside, with the rows validation
    refused underneath; `delivered` is the archive the customer panel
    pulls each fate from, searchable by address, phone or note.

    Panel and hand-added accounts share the waiting list - `source` says
    which is which - because the keeper takes the next one either way.
    """
    view = GPT_VIEWS.get(view, "waiting")
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or 1))
    skip = (page - 1) * per_page
    with Store(settings) as store:
        counts = store._rows(
            "SELECT"
            " count(*) FILTER (WHERE status = ''"
            "   AND error IS NULL) AS waiting,"
            " count(*) FILTER (WHERE status IN ('in_use', 'ready'))"
            "   AS on_phone,"
            " count(*) FILTER (WHERE status = 'delivered') AS delivered,"
            " count(*) FILTER (WHERE error IS NULL AND NOT (status = ANY(%s))"
            "   AND status <> %s) AS needs_human,"
            " count(*) FILTER (WHERE error IS NOT NULL) AS broken"
            # The GPT Pool page is the GPT accounts: the Spotify rows in
            # the same table are the dashboard's Spotify sheet.
            f" FROM resources WHERE kind = 'app' AND {NOT_SPOTIFY}",
            (sorted(ROUTINE["app"]), IMPORTED))[0]
        out = {"view": view, "counts": counts, "q": q, "page": page}
        if view == "delivered":
            like = f"%{q.strip()}%"
            rows = store._rows(
                f"SELECT {_APP_COLUMNS}, u.username AS added_by_name"
                " FROM resources r LEFT JOIN users u ON u.id = r.added_by"
                f" WHERE {_DELIVERED_MATCH}"
                " ORDER BY r.updated_at DESC, r.id DESC LIMIT %s OFFSET %s",
                (q.strip(), like, like, like, per_page + 1, skip))
            found = store._rows(
                "SELECT count(*) AS n FROM resources r"
                f" WHERE {_DELIVERED_MATCH}", (q.strip(), like, like, like))
            total = int(found[0]["n"]) if found else 0
        elif view == "on_phone":
            rows = store._rows(
                f"SELECT {_APP_COLUMNS}, u.username AS added_by_name,"
                " p.status AS phone_status"
                " FROM resources r LEFT JOIN users u ON u.id = r.added_by"
                " LEFT JOIN phones p"
                "   ON p.serial = r.serial AND p.done_at IS NULL"
                " WHERE r.kind = 'app' AND r.status IN ('in_use', 'ready')"
                f"   AND {NOT_SPOTIFY}"
                " ORDER BY r.updated_at DESC LIMIT %s OFFSET %s",
                (per_page + 1, skip))
            total = int(counts["on_phone"])
        elif view == "needs_human":
            rows = store._rows(
                f"SELECT {_APP_COLUMNS}, u.username AS added_by_name"
                " FROM resources r LEFT JOIN users u ON u.id = r.added_by"
                " WHERE r.kind = 'app' AND r.error IS NULL"
                f"   AND {NOT_SPOTIFY}"
                " AND NOT (r.status = ANY(%s)) AND r.status <> %s"
                " ORDER BY r.updated_at DESC LIMIT %s OFFSET %s",
                (sorted(ROUTINE["app"]), IMPORTED, per_page + 1, skip))
            total = int(counts["needs_human"])
            # The rows validation refused ride with them: they are not
            # stock, nobody can use them, and they are the same kind of
            # thing to decide about.
            out["broken"] = store._rows(
                "SELECT id, address, error FROM resources"
                f" WHERE kind = 'app' AND error IS NOT NULL AND {NOT_SPOTIFY}"
                " ORDER BY id")
        else:
            rows = store._rows(
                f"SELECT {_APP_COLUMNS}, u.username AS added_by_name"
                " FROM resources r LEFT JOIN users u ON u.id = r.added_by"
                " WHERE r.kind = 'app' AND r.status = ''"
                f"   AND r.error IS NULL AND {NOT_SPOTIFY}"
                " ORDER BY r.sheet_row NULLS LAST, r.id LIMIT %s OFFSET %s",
                (per_page + 1, skip))
            total = int(counts["waiting"])
    out.update(rows=rows[:per_page], more=len(rows) > per_page, total=total,
               pages=_pages(total, per_page))
    return out


def logins(settings: Settings, days: int = 7) -> dict:
    """The Login rate page: every Google sign-in of the last `days`, by
    seller, phone model, exit host, day, reason and position on the
    phone - what a purchase and a builder change are judged by."""
    from ..store import signins as store_signins

    days = max(1, min(int(days or 7), 90))
    by = {name: store_signins.rates(settings, name, days)
          for name in ("seller", "model", "host", "day", "reason", "position")}
    by["day"] = sorted(by["day"], key=lambda r: r["key"])
    by["position"] = sorted(by["position"],
                            key=lambda r: int(r["key"] or 0))
    return {"days": days, "totals": store_signins.totals(settings, days),
            "by": by, "min_sample": store_signins.MIN_SAMPLE}


def api_clients(settings: Settings) -> dict:
    """The keys the machines come in with, and what each did today.

    Two numbers beside every row, because a key is administered by what
    it is doing and not by when it was made: the requests it has asked
    the farm to carry out today, and the accounts it has handed over
    today - which is the number the daily cap is about.

    The day is the console's own (the owner's zone), the same day the
    Events page chips are cut on. The API's cap counts a UTC day: they
    disagree for three and a half hours, and the page says which is
    which rather than pretending one number answers both.
    """
    import datetime

    from ..store import api_clients as store_clients

    rows = store_clients.listing(settings)
    day = datetime.datetime.now(_zone(settings)).date().isoformat()
    # Built before the early return: with no keys minted yet the page was
    # told there were no limits and that writing was off, on the one page
    # whose job is to say what the limits are (the review, 2026-09-12).
    # What the door itself is doing, not only what the keys are: on the
    # morning of an integration the first question is "is it open", and
    # the page could not answer it (the review, 2026-09-19).
    from ..web import api_v1

    out = {"rows": rows, "day": day,
           "cap": int(getattr(settings, "web_api_accounts_per_day", 100)),
           "per_minute": int(getattr(settings, "web_api_rate_per_minute",
                                     600)),
           "open": bool(getattr(settings, "web_api", False)),
           "writes": bool(getattr(settings, "web_api_writes", False)),
           # Refused keys are this process's own memory, so they are read
           # here rather than from the store: a prefix, how many tries are
           # on it and when the last one was. Never a token.
           "refused": api_v1.refusals(),
           "locked_after": api_v1.LOCKOUT_AFTER,
           "locked_for": api_v1.LOCKOUT_SECONDS}
    if not rows:
        return out
    ids = [int(r["id"]) for r in rows]
    bounds = day_bounds(settings, day)
    since = bounds[0] if bounds else None
    with Store(settings) as store:
        asked = store._rows(
            "SELECT a.client_id AS id, count(*) AS c FROM actions a"
            " WHERE a.client_id = ANY(%s) AND a.requested_at >= %s"
            " GROUP BY a.client_id", (ids, since))
        made = store._rows(
            "SELECT r.client_id AS id, count(*) AS c FROM resources r"
            " WHERE r.kind = 'app' AND r.client_id = ANY(%s)"
            "   AND r.created_at >= %s GROUP BY r.client_id", (ids, since))
        today_utc = store._rows(
            "SELECT r.client_id AS id, count(*) AS c FROM resources r"
            " WHERE r.kind = 'app' AND r.client_id = ANY(%s)"
            "   AND r.created_at >= date_trunc('day', now() AT TIME ZONE"
            "                                  'UTC')"
            " GROUP BY r.client_id", (ids,))
    counted = {name: {int(r["id"]): int(r["c"] or 0) for r in source}
               for name, source in (("asked", asked), ("made", made),
                                    ("utc", today_utc))}
    # Which prefixes are refused right now, so a row can say "this key is
    # locked out" instead of the admin wondering why a correct-looking
    # panel gets 429.
    locked = {r["prefix"]: r for r in out["refused"]}
    for row in rows:
        row["refused_tries"] = int(
            (locked.get(str(row.get("key_prefix") or "")) or {}).get(
                "tries") or 0)
    for row in rows:
        ident = int(row["id"])
        row["requests_today"] = counted["asked"].get(ident, 0)
        row["accounts_today"] = counted["made"].get(ident, 0)
        row["accounts_today_utc"] = counted["utc"].get(ident, 0)
    return out


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


def _zone(settings: Settings):
    """The owner's zone, for a day's boundaries. A machine without the
    zone database keeps the fixed Tehran offset, like the pages do."""
    import datetime

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(settings.web_tz)
    except Exception as exc:                                      # noqa: BLE001
        log.debug("zone %r is not available (%s); days are Tehran +03:30",
                  settings.web_tz, exc)
        return datetime.timezone(datetime.timedelta(hours=3, minutes=30))


def day_bounds(settings: Settings, day: str) -> tuple | None:
    """Midnight to midnight of `day` (YYYY-MM-DD) in the owner's zone, as
    two aware stamps; None for anything that is not a date - "all", a
    typo, an empty string."""
    import datetime

    try:
        date = datetime.date.fromisoformat(str(day or ""))
    except ValueError:
        log.debug("%r is not a day; no bounds for it", day)
        return None
    start = datetime.datetime(date.year, date.month, date.day,
                              tzinfo=_zone(settings))
    return start, start + datetime.timedelta(days=1)


def _events_where(settings: Settings, kind: str, q: str,
                  day: str) -> tuple[str, list]:
    """The feed's filter as SQL: the pill's kinds, one search word (a
    serial, a run id, or text in the detail) and one day in the owner's
    zone. Empty `day` means every day."""
    where, params = [], []
    if kind in KINDS:
        where.append("kind = ANY(%s)")
        params.append(list(KINDS[kind]))
    if q:
        where.append("(serial = %s OR run_id = %s OR detail ILIKE %s)")
        params += [q, q, f"%{q}%"]
    bounds = day_bounds(settings, day)
    if bounds is not None:
        where.append("at >= %s AND at < %s")
        params += list(bounds)
    return ((" WHERE " + " AND ".join(where)) if where else ""), params


def events_feed(settings: Settings, *, kind: str = "", q: str = "",
                day: str = "", page: int = 1, per_page: int = 100) -> dict:
    """The event table, filtered by pill, by one search word - a serial,
    an address, a run id - and by one day, newest first, with the pills'
    counts scoped to the same day (or to everything when `day` is not a
    date)."""
    clause, params = _events_where(settings, kind, q, day)
    day_clause, day_params = _events_where(settings, "", "", day)
    offset = max(0, page - 1) * per_page
    with Store(settings) as store:
        total = store._rows(f"SELECT count(*) AS n FROM events{clause}",
                            tuple(params))
        rows = store._rows(
            f"SELECT id, at, kind, run_id, build, serial, status, seconds,"
            f" detail FROM events{clause} ORDER BY id DESC"
            f" LIMIT %s OFFSET %s", tuple(params) + (per_page, offset))
        tally = store._rows(
            f"SELECT kind, count(*) AS n FROM events{day_clause}"
            f" GROUP BY kind", tuple(day_params))
    by_kind = {r["kind"]: int(r["n"]) for r in tally}
    counts = {name: sum(by_kind.get(k, 0) for k in kinds)
              for name, kinds in KINDS.items()}
    counts["all"] = sum(by_kind.values())
    n = int(total[0]["n"]) if total else 0
    return {"rows": rows, "counts": counts, "page": page, "day": day,
            "pages": max(1, -(-n // per_page)), "total": n}


def events_rows(settings: Settings, *, kind: str = "", q: str = "",
                day: str = "") -> list[dict]:
    """The same feed, whole, for the CSV export: the page shows a hundred
    at a time, the export carries everything the filter matches."""
    clause, params = _events_where(settings, kind, q, day)
    with Store(settings) as store:
        return store._rows(
            f"SELECT id, at, kind, run_id, build, serial, status, seconds,"
            f" detail FROM events{clause} ORDER BY id DESC", tuple(params))


def logs(settings: Settings, *, level: str = "INFO", logger: str = "",
         run: str = "", phone: str = "", q: str = "", before: int = 0,
         limit: int = 200) -> dict:
    """The captured log lines, newest first, narrowed by whatever the
    person typed. Empty filters mean "everything at INFO and up";
    `before` is the id the previous page ended on, for reading older.
    `loggers` is every name the table has seen, for the filter's select;
    `more` says whether an older page exists."""
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
    if before:
        where.append("id < %s")
        params.append(int(before))
    with Store(settings) as store:
        rows = store._rows(
            f"SELECT id, at, level, logger, run, build, serial, msg FROM logs"
            f" WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT %s",
            tuple(params) + (limit + 1,))
        today = store._rows(
            "SELECT count(*) AS n FROM logs"
            " WHERE at > date_trunc('day', now())")
        names = store._rows("SELECT DISTINCT logger FROM logs ORDER BY 1")
    return {"rows": rows[:limit], "more": len(rows) > limit,
            "today": int(today[0]["n"]) if today else 0,
            "loggers": [r["logger"] for r in names if r["logger"]]}


def _stamp_key(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def phone_story(settings: Settings, serial: str) -> dict | None:
    """Everything one phone went through, in order: its events, the
    requests that named it, and the archived screens on disk - joined on
    the serial, which is the one name all three sources use."""
    with Store(settings) as store:
        phone = store._rows(
            "SELECT p.serial, p.status, p.state, p.gmail, p.app_account,"
            " p.proxy_name, p.tries, p.note, p.created_at, p.updated_at,"
            " p.done_at, u.username AS owner"
            " FROM phones p LEFT JOIN users u ON u.id = p.owner_id"
            " WHERE p.serial = %s ORDER BY p.id DESC LIMIT 1", (serial,))
        events = store._rows(
            "SELECT at, kind, run_id, build, status, seconds, detail"
            " FROM events WHERE serial = %s ORDER BY id", (serial,))
        requests = store._rows(
            "SELECT a.id, a.verb, a.payload, a.status, a.result,"
            " a.requested_at,"
            " coalesce(u.username, c.name, '?') AS requested_by"
            " FROM actions a LEFT JOIN users u ON u.id = a.requested_by"
            " LEFT JOIN api_clients c ON c.id = a.client_id"
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
                         "run": f"#{r['id']}", "id": r["id"],
                         "verb": r["verb"], "payload": r["payload"] or {},
                         "requested_by": r["requested_by"],
                         "result": r["result"],
                         "text": f"{r['requested_by']} asked: {r['verb']}"
                                 f" -> {r['status']}: {r['result']}",
                         "seconds": None})
    seen_folders = set()
    if settings.artifacts_in_pg:
        # The store first: screens a builder on another host archived.
        for folder in _stored(settings, serial):
            seen_folders.add(folder["folder"])
            timeline.append(folder)
    for folder in _archived(settings.artifact_dir, serial):
        if folder["folder"] not in seen_folders:
            timeline.append(folder)
    timeline.sort(key=lambda t: _stamp_key(t["at"]))
    return {"phone": phone[0] if phone else None, "serial": serial,
            "timeline": timeline}


def _stored(settings: Settings, serial: str) -> list[dict]:
    """The archived screens the store holds for one phone, as timeline
    entries - the same shape `_archived` builds from the disk."""
    from ..store import artifacts as store_artifacts

    try:
        found = store_artifacts.folders(settings, serial)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("could not list the store's screens for %s (%s)", serial,
                  exc)
        return []
    return [{"at": f["at"], "source": "artifact", "kind": "screens",
             "status": f["outcome"], "run": f["folder"],
             "folder": f["folder"], "files": f["files"],
             "text": f"{len(f['files'])} screen(s) archived in {f['folder']}"
                     + (f" - {f['outcome']}" if f["outcome"] else ""),
             "seconds": None} for f in found]


def screen_bytes(settings: Settings, serial: str, folder: str, name: str
                 ) -> bytes | None:
    """One archived screen's text, from the store when it is on and has
    it, from the disk this host can see otherwise. The same three guards
    as `screen_file`: nothing here reads a name it was not told."""
    if not (serial and folder and name):
        return None
    if any(sep in folder + name for sep in ("/", "\\")) or ".." in (folder,
                                                                     name):
        return None
    if not name.endswith(".xml"):
        return None
    if settings.artifacts_in_pg:
        from ..store import artifacts as store_artifacts

        try:
            found = store_artifacts.get(settings, serial, folder, name)
        except Exception as exc:                                   # noqa: BLE001
            log.debug("could not read %s/%s from the store (%s)", folder,
                      name, exc)
            found = None
        if found is not None:
            return found
    path = screen_file(settings, serial, folder, name)
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


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
            files = sorted(f.name for f in folder.iterdir()
                           if f.suffix == ".xml" and f.is_file())
            when = datetime.fromtimestamp(folder.stat().st_mtime,
                                          tz=timezone.utc)
        except (OSError, IndexError) as exc:
            log.debug("skipping %s (%s)", folder, exc)
            continue
        found.append({"at": when, "source": "artifact", "kind": "screens",
                      "status": outcome, "run": folder.name,
                      "folder": folder.name, "files": files,
                      "text": f"{len(files)} screen(s) archived in "
                              f"{folder.name}"
                              + (f" - {outcome}" if outcome else ""),
                      "seconds": None})
    return found


def screen_file(settings: Settings, serial: str, folder: str, name: str):
    """The path of one archived screen, or None. Guarded three ways: the
    folder must be one of this phone's (artifacts.serial_of), the file
    must be one plain .xml name inside it, and the resolved path must
    still sit under artifact_dir - so a crafted name walks nowhere."""
    from pathlib import Path

    from ..artifacts import serial_of

    if not (serial and folder and name):
        return None
    if any(sep in folder + name for sep in ("/", "\\")) or ".." in (folder,
                                                                     name):
        return None
    if not name.endswith(".xml") or serial_of(Path(folder)) != serial:
        return None
    root = settings.artifact_dir
    try:
        root = root.resolve()
        path = (root / folder / name).resolve()
        if root not in path.parents or not path.is_file():
            return None
    except OSError as exc:
        log.debug("screen %s/%s not served (%s)", folder, name, exc)
        return None
    return path


def gmail_on_phone(settings: Settings, serial: str) -> dict | None:
    """The Gmail signed into this phone, with what the operator needs
    beside the screen: the address, its password and its authenticator
    key. Read for the Live tab (pages.viewer_page), and only handed to
    the person holding the phone - see app.py. None when the phone has
    no Gmail, or the pool no longer has the row."""
    with Store(settings) as store:
        rows = store._rows(
            "SELECT r.address, coalesce(r.password, '') AS password,"
            " coalesce(r.totp_secret, '') AS totp_secret"
            " FROM phones p JOIN resources r"
            "   ON r.kind = 'gmail' AND lower(r.address) = lower(p.gmail)"
            " WHERE p.serial = %s AND p.done_at IS NULL"
            "   AND coalesce(p.gmail, '') <> ''"
            " ORDER BY r.id DESC LIMIT 1", (str(serial),))
    return rows[0] if rows else None


def account_on_phone(settings: Settings, serial: str) -> dict | None:
    """The app account signed into this phone, with what the operator
    needs beside the screen: which product it is for, which kind of
    Spotify account, the address, its password and its authenticator
    key.

    The Gmail was the only thing in the Live tab's margin, and half the
    phones the farm builds carry no Gmail at all - a bare Spotify phone
    is an account and nothing else, so the margin was empty on exactly
    the phone whose account somebody is about to hand over (the
    operator, 2026-09-19).

    Same rule as the Gmail: read for the Live tab, handed only to the
    person holding the phone (app.py). None when nothing is signed in,
    or the pool no longer has the row - which is the case for a phone
    whose account was delivered and archived.

    The `app_account` cell holds a cross for a phone with no account;
    no row has a cross for an address, so the join answers None without
    this having to know the mark.
    """
    with Store(settings) as store:
        rows = store._rows(
            "SELECT r.address, coalesce(r.password, '') AS password,"
            " coalesce(r.totp_secret, '') AS totp_secret,"
            " coalesce(r.product, '') AS product,"
            " coalesce(r.category, '') AS category,"
            " coalesce(r.email_code_only, false) AS email_code_only"
            " FROM phones p JOIN resources r"
            "   ON r.kind = 'app'"
            "   AND lower(r.address) = lower(p.app_account)"
            " WHERE p.serial = %s AND p.done_at IS NULL"
            "   AND coalesce(p.app_account, '') <> ''"
            " ORDER BY r.id DESC LIMIT 1", (str(serial),))
    return rows[0] if rows else None
