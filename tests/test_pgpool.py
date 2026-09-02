"""The pools over Postgres (C2): the same contract, a different floor.

Every behaviour a caller relies on - claim order, what each verb writes,
who is stuck or flagged or abandoned, the heartbeat - is exercised here
over an in-memory table of the same shape the SQL adapter speaks, so the
suite runs on a machine that has never seen the cluster. The adapter's
own statements are covered by the live tests at the bottom, which need
GEELARK_TEST_DSN and race two threads over one free row.
"""

from __future__ import annotations

import datetime as dt
import threading

import pytest

from geelark_farm.pools import AppPool, GmailPool, ProxyPool
from geelark_farm.store import importer, pgpool
from geelark_farm.store.pgpool import PgAppPool, PgGmailPool, PgProxyPool
from tests.test_pools import (
    APP_HEADERS,
    GMAIL_HEADERS,
    PROXY_HEADERS_ROTATION,
    SECRET,
    FakeWorksheet,
    gmail_row,
    proxy_row,
)


def _now():
    return dt.datetime.now(dt.timezone.utc)


class MemoryTable:
    """ResourceTable's contract over dicts: the same five calls, no SQL."""

    DEFAULTS = dict(status="", error=None, times_used=0, sheet_row=None,
                    claimed_at=None, serial="", note="", address=None,
                    password="", totp_secret="", recovery_email="",
                    email_code_only=False, seller="", host=None, port=None,
                    username=None, proxy_pass="", proxy_name="",
                    last_exit_ip="", used_at="", purchased_on="",
                    source="sheet", added_by=None, owner_id=None)

    def __init__(self):
        self._rows: dict[int, dict] = {}
        self._next = 1
        self.updates: list[tuple[int, dict]] = []

    def add(self, kind: str, **cols) -> int:
        row = dict(self.DEFAULTS, kind=kind, **cols)
        row["id"] = self._next
        self._rows[row["id"]] = row
        self._next += 1
        return row["id"]

    def row(self, row_id: int) -> dict:
        return self._rows[row_id]

    def _ordered(self, kind, count_use=False):
        rows = [r for r in self._rows.values() if r["kind"] == kind]
        return sorted(rows, key=lambda r: (
            r["times_used"] if count_use else 0,
            r["sheet_row"] is None, r["sheet_row"] or 0, r["id"]))

    # --- the contract --------------------------------------------------
    def rows(self, kind):
        return [dict(r) for r in self._ordered(kind)]

    def update(self, row_id, fields):
        self._rows[row_id].update(fields)
        self.updates.append((row_id, dict(fields)))

    def claim(self, kind, *, free, claimed, count_use, serial=""):
        for r in self._ordered(kind, count_use):
            if r["error"] is None and (r["status"] or "").lower() in free:
                r["status"] = claimed
                r["claimed_at"] = _now()
                if count_use:
                    r["times_used"] += 1
                if serial:
                    r["serial"] = serial
                return dict(r)
        return None

    def beat(self, ids):
        for i in ids:
            self._rows[i]["claimed_at"] = _now()
        return len(ids)

    def delete(self, row_id):
        self._rows.pop(row_id, None)

    def insert(self, row):
        for r in self._rows.values():
            if r["kind"] != row["kind"]:
                continue
            if row["kind"] == "proxy":
                same = (r["host"], r["port"], r["username"]) == (
                    row["host"], row["port"], row["username"])
            else:
                same = (r["address"] or "").lower() == row["address"].lower()
            if same:
                return None
        return self.add(row["kind"], **{k: v for k, v in row.items()
                                        if k != "kind"})


def gmails(table=None, n=2):
    table = table or MemoryTable()
    for i in range(n):
        table.add("gmail", address=f"g{i}@x.com", password="pw",
                  totp_secret=SECRET, sheet_row=i + 2, seller="usa")
    pool = PgGmailPool(table)
    pool.load()
    return table, pool


def proxies(table=None):
    table = table or MemoryTable()
    table.add("proxy", host="10.0.0.1", port=9999, username="u",
              proxy_pass="p", proxy_name="SX1", status="free", sheet_row=2,
              times_used=3)
    table.add("proxy", host="10.0.0.2", port=9999, username="u",
              proxy_pass="p", proxy_name="SX2", status="unused", sheet_row=3,
              times_used=1)
    pool = PgProxyPool(table)
    pool.load()
    return table, pool


# ------------------------------------------------------- reading the table
def test_the_rows_come_back_in_the_sheets_own_words():
    """Nothing above the pool learns a new column name: `values` is keyed
    exactly as the tab was, and the special cells are rebuilt."""
    table = MemoryTable()
    table.add("gmail", address="a@x.com", password="pw", totp_secret="",
              recovery_email="rec@x.com", seller="egypt", sheet_row=5,
              status="", purchased_on="2026-08-30", used_at="")
    table.add("app", address="b@x.com", password="pw", totp_secret=SECRET,
              email_code_only=True, serial="1523", status="ready",
              claimed_at=dt.datetime(2026, 9, 1, 6, 30, 38,
                                     tzinfo=dt.timezone.utc))
    table.add("proxy", host="10.0.0.1", port=9999, username="u",
              proxy_pass="p", proxy_name="SX1", times_used=18,
              serial="1557", status="on a phone", last_exit_ip="1.2.3.4")

    g = PgGmailPool(table)
    g.load()
    a = PgAppPool(table)
    a.load()
    p = PgProxyPool(table)
    p.load()

    assert g._rows[0].values["Secret"] == "rec@x.com"
    assert g._rows[0].values["Purchase Date"] == "2026-08-30"
    assert g._rows[0].credentials.recovery_email == "rec@x.com"
    assert g._rows[0].sheet_row == 5 and g._rows[0].store_id == 1
    assert a._rows[0].values["Email code"] == "TRUE"
    assert a._rows[0].values["Claimed"] == "2026-09-01 06:30:38Z"
    assert a._rows[0].credentials.email_code_only
    assert p._rows[0].values["Proxy String"] == "10.0.0.1:9999:u:p"
    assert p._rows[0].values["Times Used"] == "18"
    assert p._rows[0].values["Used By"] == "1557"
    assert p._rows[0].proxy.host == "10.0.0.1"


def test_a_row_the_store_refused_is_broken_here_too():
    table = MemoryTable()
    table.add("gmail", address="bad@x.com", password="pw",
              error="the secret is not base32")
    pool = PgGmailPool(table)
    pool.load()
    assert pool.available == [] and len(pool.broken) == 1
    assert "base32" in pool.broken[0].error


# ------------------------------------------------------------ claiming
def test_claim_takes_the_top_row_marks_it_and_hands_back_the_same_object():
    table, pool = gmails()
    first = pool._rows[0]

    got = pool.claim(serial="1600")

    assert got is first, "identity is the contract - a run holds this"
    assert got.values["Status"] == "in_use"
    assert got.values["Phone Serial"] == "1600"
    assert got.values["Claimed"].endswith("Z")
    assert table.row(1)["status"] == "in_use"
    assert table.row(1)["serial"] == "1600"
    assert pool.available == [pool._rows[1]]
    assert pool.stuck == [got]


def test_two_claims_take_two_rows_and_a_third_takes_nothing():
    _, pool = gmails()
    a, b, c = pool.claim(), pool.claim(), pool.claim()
    assert a is not b and c is None


def test_a_proxy_is_claimed_least_used_first_and_counts_the_use():
    table, pool = proxies()
    got = pool.claim(serial="1600")
    assert got.values["Name"] == "SX2", "1 use beats 3"
    assert got.values["Status"] == "claimed"
    assert got.values["Times Used"] == "2"
    assert table.row(2)["times_used"] == 2
    assert pool.claim().values["Name"] == "SX1"


def test_a_row_added_after_the_book_opened_can_still_be_claimed():
    """The case the sheet pool could not take without orphaning a run's
    rows: here it is one more Resource, appended."""
    table, pool = gmails(n=0)
    assert pool.claim() is None
    table.add("gmail", address="late@x.com", password="pw",
              totp_secret=SECRET)
    got = pool.claim()
    assert got is not None and got.credentials.email == "late@x.com"
    assert got in pool._rows


# ------------------------------------------------------------ the verbs
def test_every_way_off_a_phone_clears_the_serial_and_writes_the_status():
    table, pool = gmails(n=4)
    rows = pool._rows
    for _ in rows:
        pool.claim(serial="1600")

    pool.release(rows[0], note="back")
    pool.retire(rows[1], note="gone")
    pool.fail(rows[2], "wrong_password", note="nope")
    pool.spend(rows[3], serial="1601", note="on it")

    assert (table.row(1)["status"], table.row(1)["serial"]) == ("", "")
    assert (table.row(2)["status"], table.row(2)["serial"]) == ("used", "")
    assert table.row(3)["status"] == "wrong_password"
    assert table.row(3)["serial"] == ""
    assert table.row(4)["status"] == "ready"
    assert table.row(4)["serial"] == "1601"
    assert table.row(4)["used_at"], "the Gmails tab records when"
    assert pool.stuck == [] and pool.flagged == [rows[2]]


def test_an_app_account_set_aside_wears_the_reason_as_its_status():
    table = MemoryTable()
    table.add("app", address="a@x.com", password="pw", totp_secret=SECRET)
    pool = PgAppPool(table)
    pool.load()
    row = pool.claim()
    pool.set_aside(row, reason="payment_problem", note="fix it")
    assert table.row(1)["status"] == "payment_problem"
    assert table.row(1)["serial"] == "" and table.row(1)["note"] == "fix it"


def test_a_proxy_keeps_every_serial_it_carried_and_records_its_exit():
    table, pool = proxies()
    row = pool._rows[0]
    pool.spend(row, serial="1600", note="a")
    pool.spend(row, serial="1601", note="b")
    pool.record_exit(row, "5.6.7.8")
    pool.record_exit(row, "5.6.7.8")             # unchanged: no write
    assert table.row(1)["serial"] == "1600, 1601"
    assert table.row(1)["last_exit_ip"] == "5.6.7.8"
    assert sum(1 for _, f in table.updates if "last_exit_ip" in f) == 1


def test_a_note_is_clipped_and_an_unknown_column_is_skipped():
    table, pool = gmails(n=1)
    row = pool._rows[0]
    pool._set(row, {"Note": "x" * 900, "No Such Column": "y"})
    assert len(table.row(1)["note"]) == pool.NOTE_LIMIT
    assert "No Such Column" not in table.row(1)


def test_find_answers_by_address_whatever_the_state():
    _, pool = gmails()
    pool.claim()
    assert pool.find("G0@X.COM") is pool._rows[0]
    assert pool.find("nobody@x.com") is None


# ----------------------------------------------------- staleness, beats
def test_abandoned_reads_the_stamp_the_table_keeps():
    table, pool = gmails()
    old = _now() - dt.timedelta(minutes=20)
    table.row(1).update(status="in_use", claimed_at=old)
    table.row(2).update(status="in_use", claimed_at=_now())
    pool.load()
    assert [r.store_id for r in pool.abandoned(600)] == [1]


def test_beat_restamps_only_what_this_process_holds():
    table, pool = gmails()
    held = pool.claim()
    before = table.row(held.store_id)["claimed_at"]
    table.row(held.store_id)["claimed_at"] = before - dt.timedelta(minutes=9)

    assert pool.beat() == 1
    assert table.row(held.store_id)["claimed_at"] > before - dt.timedelta(
        minutes=9)
    pool.release(held)
    assert pool.beat() == 0


# ------------------------------------------------------------ the funnel
def _sheet_pools(gmail_rows=(), app_rows=(), proxy_rows=()):
    lock = threading.Lock()
    g = GmailPool(FakeWorksheet(GMAIL_HEADERS, list(gmail_rows)),
                  GMAIL_HEADERS, lock)
    a = AppPool(FakeWorksheet(APP_HEADERS, list(app_rows)), APP_HEADERS, lock)
    p = ProxyPool(FakeWorksheet(PROXY_HEADERS_ROTATION, list(proxy_rows)),
                  PROXY_HEADERS_ROTATION, lock)
    for pool in (g, a, p):
        pool.load()
    return g, a, p


def test_the_importer_takes_fresh_rows_once_and_marks_them_in_the_sheet():
    g, a, p = _sheet_pools(
        gmail_rows=[gmail_row("new@x.com"), gmail_row("used@x.com", "used")],
        app_rows=[["app@x.com", "pw", SECRET, "", "", ""]],
        proxy_rows=[proxy_row("10.0.0.9:9999:u:p")])
    table = MemoryTable()

    took = importer.pull((g, a, p), table)

    assert (took["gmail"], took["app"], took["proxy"]) == (1, 1, 1)
    assert g._rows[0].values["Status"] == "imported"
    assert "Imported into the store" in g._rows[0].values["Note"]
    assert g._rows[1].values["Status"] == "used", "not free: untouched"
    assert table.rows("gmail")[0]["source"] == "sheet"
    assert table.rows("gmail")[0]["sheet_row"] == 2
    # a second pass finds nothing free and inserts nothing
    assert importer.pull((g, a, p), table)["gmail"] == 0


def test_the_importer_marks_a_row_the_store_already_knows():
    g, a, p = _sheet_pools(gmail_rows=[gmail_row("known@x.com")])
    table = MemoryTable()
    table.add("gmail", address="KNOWN@x.com", password="pw")

    took = importer.pull((g, a, p), table)

    assert took["gmail"] == 0
    assert g._rows[0].values["Status"] == "imported"
    assert "Already in the store" in g._rows[0].values["Note"]


def test_the_importer_says_why_a_bad_row_stays_behind():
    g, a, p = _sheet_pools(app_rows=[["nope", "pw", "", "", "", ""]])
    table = MemoryTable()

    took = importer.pull((g, a, p), table)

    assert took["refused"] == 1 and table.rows("app") == []
    assert a._rows[0].values["Status"] == "", "left for a person to fix"
    assert a._rows[0].values["Note"].startswith("Not imported:")
    # said once - a second pass does not spend a write repeating it
    writes = len(a._ws.writes)
    assert importer.pull((g, a, p), table)["refused"] == 0
    assert len(a._ws.writes) == writes


# ------------------------------------------------------------ the switch
def test_the_pools_reach_the_store_only_inside_the_flag():
    """The trunk rule, for this module: pools.py names the store package
    nowhere at module level, and the one import it makes sits inside the
    `pools_in_pg` branch of Book.open."""
    import ast
    import pathlib

    src = pathlib.Path("src/geelark_farm/pools.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:                      # module level only
        if isinstance(node, ast.ImportFrom) and node.module and \
                "store" in node.module:
            raise AssertionError("pools.py imports the store at module level")
    assert "if settings.pools_in_pg:" in src
    assert src.index("if settings.pools_in_pg:") < src.index(
        "from .store.pgpool import")


def test_the_mirror_leaves_the_resources_alone_once_they_are_the_pool():
    from geelark_farm.store import shadow

    executed = []

    class Cur:
        rowcount = 0

        def execute(self, sql, params=None):
            executed.append(" ".join(sql.split()))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

    class Conn:
        def cursor(self):
            return Cur()

        def commit(self):
            pass

    class Phones:
        @staticmethod
        def _typed_rows(what):
            return iter([])

    book = type("B", (), {"phones": Phones(), "gmails": None,
                          "proxies": None, "apps": None})
    shadow.write_shadow(Conn(), book, resources=False)
    assert not any("INSERT INTO resources" in s for s in executed)
    assert any("UPDATE phones" in s for s in executed)


def test_serve_imports_from_the_sheet_only_with_the_flag_on(monkeypatch,
                                                            make_settings):
    import geelark_farm.serve as serve_mod

    pulled = []
    monkeypatch.setattr(pgpool, "ResourceTable", lambda s: "table")
    monkeypatch.setattr(importer, "pull",
                        lambda pools, table: pulled.append((pools, table)))
    reloaded = []
    book = type("B", (), {"sheet_pools": ("g", "a", "p"),
                          "reload": lambda self: reloaded.append(1)})()

    serve_mod._import_from_sheet(make_settings(), book)
    assert pulled == [], "flag off: the store is never touched"

    on = make_settings(store_enabled=True, store_host="h",
                       store_password="p", pools_in_pg=True)
    serve_mod._import_from_sheet(on, book)
    assert pulled == [(("g", "a", "p"), "table")] and reloaded == [1]


def test_the_shadow_stamp_reads_both_spellings_and_refuses_the_rest():
    from geelark_farm.store.shadow import _when

    assert _when("2026-09-01 06:30:38Z") == "2026-09-01 06:30:38+00"
    assert _when("2026-09-01 06:30:38") == "2026-09-01 06:30:38+00"
    assert _when("") is None and _when("yesterday") is None


# ------------------------------------------------ against a real cluster
needs_cluster = pytest.mark.skipif(
    "GEELARK_TEST_DSN" not in __import__("os").environ,
    reason="set GEELARK_TEST_DSN to run store integration tests")


@needs_cluster
def test_two_threads_claiming_one_free_row_get_one_row_and_one_none(
        make_settings):
    """The property the sheet needed a lock plus a re-read to approximate,
    now the engine's: FOR UPDATE SKIP LOCKED hands two racers two rows, or
    one row and a None - never the same row twice."""
    import os
    import uuid

    from geelark_farm.store.pgpool import ResourceTable

    dsn = os.environ["GEELARK_TEST_DSN"]
    parts = dict(p.split("=", 1) for p in dsn.split())
    settings = make_settings(
        store_enabled=True, store_host=parts["host"],
        store_port=int(parts.get("port", 5432)), store_db=parts["dbname"],
        store_user=parts["user"], store_password=parts["password"],
        pools_in_pg=True)
    table = ResourceTable(settings)
    tag = uuid.uuid4().hex[:10]
    row_id = table.insert(dict(kind="proxy", host=f"race-{tag}.test",
                               port=1, username="u", proxy_pass="p",
                               proxy_name=f"RACE-{tag}", status="free",
                               source="sheet"))
    assert row_id is not None
    try:
        results = []

        def racer():
            results.append(table.claim(
                "proxy", free=("", "free", "unused"), claimed="claimed",
                count_use=True, serial="race"))

        threads = [threading.Thread(target=racer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        won = [r for r in results if r is not None
               and r["id"] == row_id]
        assert len(won) == 1
    finally:
        with table._connect() as conn:
            conn.execute("DELETE FROM resources WHERE id = %s", (row_id,))
            conn.commit()
