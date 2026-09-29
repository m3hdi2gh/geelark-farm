"""The Station's store: schema rev 42 and every statement it adds.

The unit tests at the top run anywhere. Everything below `needs_cluster`
runs each statement against a real Postgres (`GEELARK_TEST_DSN`), because
a fake connection never parsed a single one of them: a statement that
Postgres refuses, or that binds a dict, is found here and not on the farm.

Rows are made with a unique tag - users `st_<tag>_a`, serials `9<digits>`
- and deleted in a `finally`, in foreign-key order.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from geelark_farm.store import station as st
from geelark_farm.store import users as store_users

SRC = pathlib.Path(__file__).parent.parent / "src" / "geelark_farm"


# ================================================================ units
def test_the_exit_word_never_shows_a_password_or_port():
    assert st.exit_word("socks5://u:pw@h.x:1080") == "h.x"
    assert st.exit_word("PC5") == "PC5"
    assert st.exit_word("10.0.0.9:1080") == "10.0.0.9"
    assert st.exit_word("1.2.3.4:1080:u:pw") == "1.2.3.4"
    assert st.exit_word("h.x:1080:u:p") == "h.x"
    assert st.exit_word("") == ""
    assert st.exit_word(None) == ""
    for secret in ("pw", "1080", "u:"):
        assert secret not in st.exit_word("socks5://u:pw@h.x:1080")


def test_the_lane_and_home_words():
    assert st.lane_word("gpt") == "GPT"
    assert st.lane_word("spotify") == "Spotify"
    assert st.lane_word("other") == "Other"
    assert st.lane_word("") == "GPT" and st.lane_word("weird") == "GPT"
    assert st.home("gpt") == "the GPT shelf"
    assert st.home("spotify") == "the Spotify shelf"
    assert st.home("other") == "the farm"
    assert st.home("") == "the GPT shelf"


def test_a_shown_name_falls_back_to_the_username():
    assert store_users.shown_name({"display_name": "Sara", "username": "s"}) == "Sara"
    assert store_users.shown_name({"display_name": "  ", "username": "sara"}) == "sara"
    assert store_users.shown_name({"username": "sara"}) == "sara"
    assert store_users.shown_name({}) == "?"


def test_a_name_is_cleaned_or_refused_in_its_words():
    assert store_users.clean_name("  Sara  ") == "Sara"
    persian = "مه‌سا"          # a ZWNJ inside
    assert store_users.clean_name(persian) == persian
    with pytest.raises(ValueError, match=r"^Your name cannot be empty\.$"):
        store_users.clean_name("   ")
    assert store_users.clean_name("x" * 24) == "x" * 24
    with pytest.raises(ValueError, match=r"^Your name is at most 24 characters\.$"):
        store_users.clean_name("x" * 25)
    for bad in ("Sa‮ra", "Sa\u0007ra", "a⁦b", "a‪b"):
        with pytest.raises(ValueError, match="^Your name has a character that "
                                             r"cannot be shown\.$"):
            store_users.clean_name(bad)


def test_the_station_sql_is_read_through_select_and_written_through_write():
    """`_rows` rolls back: a write sent through it is silently undone."""
    text = (SRC / "store" / "station.py").read_text(encoding="utf-8")
    assert not re.search(r'_rows\(\s*f?"\s*(WITH|UPDATE|INSERT|DELETE)', text)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_rows"):
            continue
        first = node.args[0]
        while isinstance(first, ast.BinOp):
            first = first.left
        if isinstance(first, ast.Name):
            sql = getattr(st, first.id)
        elif isinstance(first, ast.Constant):
            sql = first.value
        else:
            sql = "".join(b.value for b in first.values
                          if isinstance(b, ast.Constant))
        assert sql.lstrip().startswith("SELECT"), (node.lineno, sql[:60])


def test_no_dict_or_tuple_is_bound_raw():
    """psycopg 3 cannot adapt a dict (jsonb goes as json.dumps) nor a
    tuple (an array goes as a list)."""
    for name in ("station.py", "verdicts.py", "wanted.py"):
        text = (SRC / "store" / name).read_text(encoding="utf-8")
        assert "tuple(" not in text, name
        for node in ast.walk(ast.parse(text)):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("execute", "_write", "_rows")
                    and len(node.args) > 1):
                continue
            params = node.args[1]
            values = (params.elts if isinstance(params, (ast.Tuple, ast.List))
                      else params.values if isinstance(params, ast.Dict) else [])
            for value in values:
                assert not isinstance(value, ast.Dict), (name, node.lineno)
                assert not (isinstance(value, ast.Name)
                            and value.id in ("payload", "detail")), (
                    name, node.lineno)
    station = (SRC / "store" / "station.py").read_text(encoding="utf-8")
    assert "json.dumps(payload)" in station and "json.dumps(detail)" in station


def test_rev_42_names_every_column_the_station_reads():
    from geelark_farm.store import db

    sql = (SRC / "store" / "schema.sql").read_text(encoding="utf-8")
    assert db.SCHEMA_REV == "42"
    block = sql[sql.index("-- rev 42:"):]
    for piece in (
            "phones ADD COLUMN IF NOT EXISTS taken_at timestamptz",
            "phones ADD COLUMN IF NOT EXISTS live_url text NOT NULL DEFAULT ''",
            "phones ADD COLUMN IF NOT EXISTS last_owner_id bigint",
            "phones_station_hold_shape",
            "CHECK (taken_at IS NULL OR (state = 'taken' AND owner_id IS NOT NULL))",
            "phones_station_holds",
            "CREATE TABLE IF NOT EXISTS station_line",
            "CHECK (lane IN ('gpt', 'spotify'))",
            "CHECK (ended_why IN ('', 'served', 'left', 'gone'))",
            "station_line_open", "station_line_head",
            "verdicts ADD COLUMN IF NOT EXISTS lane",
            "verdicts ADD COLUMN IF NOT EXISTS phone_row",
            "verdicts_by_at ON verdicts (by_id, at)",
            "users ADD COLUMN IF NOT EXISTS display_name",
            "users ADD COLUMN IF NOT EXISTS password_changed_at",
            "UPDATE users SET password_changed_at = created_at",
            "wanted_builds ADD COLUMN IF NOT EXISTS station",
            "wanted_builds ADD COLUMN IF NOT EXISTS called_off_at",
            "wanted_builds ADD COLUMN IF NOT EXISTS called_off_by",
            "wanted_builds ADD COLUMN IF NOT EXISTS carry_address",
            "wanted_builds ADD COLUMN IF NOT EXISTS carry_password",
            "wanted_builds ADD COLUMN IF NOT EXISTS updated_at",
            "wanted_station", "jobs_wish", "actions_phone_power",
            "actions_one_power_press"):
        assert piece in block, piece
    # The backfill comes before the default, or every user reads "today".
    assert (block.index("UPDATE users SET password_changed_at")
            < block.index("ALTER COLUMN password_changed_at SET DEFAULT now()"))
    # The heal before the constraint, the orphan sweep before the index.
    assert block.index("UPDATE phones SET taken_at = NULL") < block.index(
        "ADD CONSTRAINT phones_station_hold_shape")
    assert block.index("closed by rev 42") < block.index(
        "CREATE UNIQUE INDEX IF NOT EXISTS actions_one_power_press")


class _Dead:
    """A store that is down."""

    def __init__(self, *a, **k):
        raise RuntimeError("no store")


def test_what_never_raises_answers_empty_when_the_store_is_down(monkeypatch,
                                                                make_settings):
    from geelark_farm.store import wanted

    monkeypatch.setattr(st, "Store", _Dead)
    monkeypatch.setattr(st, "connect", _Dead)
    monkeypatch.setattr(wanted, "Store", _Dead)
    s = make_settings()
    empty = st.shelves(s)
    assert empty["gpt"] == {"ready": 0, "building": 0, "etas": [], "late": 0,
                            "typical_s": 360.0}
    assert set(empty) == {"gpt", "spotify"}
    assert st.typical(s) == {"gpt": 360.0, "spotify": 360.0, "other": 360.0}
    assert st.serve_lines(s) == []
    assert st.stamp_line(s, 1) is None
    assert st.line_of(s, 1) == {"gpt": None, "spotify": None}
    assert st.notes(s, 1) == []
    assert st.build_form(s) == {"gmails_left": 0,
                                "free_ips": {"gpt": 0, "spotify": 0, "other": 0},
                                "stopped": False}
    assert st.scrub(s, 1) is None
    assert st.scrub_mine(s, 1) == 0
    assert wanted.attach(s, 1, "9") is False
    assert wanted.called_off(s, 1) is False
    with pytest.raises(ValueError, match="not a lane"):
        st.take(s, lane="other", user_id=1, by="x", idem_key="k")
    with pytest.raises(ValueError, match="not a lane"):
        st.leave_line(s, lane="", user_id=1, by="x", idem_key="k")
    with pytest.raises(RuntimeError):
        st.holds(s, "9", 1)


# ======================================================= against a cluster
DSN = os.environ.get("GEELARK_TEST_DSN", "")
needs_cluster = pytest.mark.skipif(
    not DSN, reason="set GEELARK_TEST_DSN to run store integration tests")

_SCHEMA_READY: list = []


class Raw(str):
    """A SQL expression written into an insert as it is, not bound."""


class Farm:
    """Rows of one test, tagged, and a way to read them back."""

    def __init__(self, settings, tag: str):
        self.s = settings
        self.tag = tag
        self.users: list[int] = []
        self.serials: list[str] = []
        self._base = int(tag, 16) % 1_000_000
        self._n = 0

    # ---------------------------------------------------------- plumbing
    def sql(self, text: str, params=()) -> list[dict]:
        from geelark_farm.store.db import connect

        with connect(self.s) as conn:
            cur = conn.execute(text, params)
            rows = ([dict(zip([d.name for d in cur.description], r, strict=True))
                     for r in cur.fetchall()] if cur.description else [])
            conn.commit()
        return rows

    def one(self, text: str, params=()) -> dict | None:
        rows = self.sql(text, params)
        return rows[0] if rows else None

    def insert(self, table: str, **cols) -> int:
        names, holes, values = [], [], []
        for key, value in cols.items():
            names.append(key)
            if isinstance(value, Raw):
                holes.append(str(value))
            else:
                holes.append("%s")
                values.append(json.dumps(value) if isinstance(value, dict)
                              else value)
        row = self.one(f"INSERT INTO {table} ({', '.join(names)})"
                       f" VALUES ({', '.join(holes)}) RETURNING id", values)
        return int(row["id"])

    def now(self) -> datetime:
        return self.one("SELECT now() AS t")["t"]

    # ------------------------------------------------------------- makers
    def user(self, letter: str, *, role: str = "operator", may_take: bool = True,
             active: bool = True, password: str = "", name: str = "") -> int:
        from geelark_farm.store import auth

        hashed = (auth.hash_password(password) if password else
                  {"password_hash": b"x", "password_salt": b"y",
                   "scrypt_n": 16384, "scrypt_r": 8, "scrypt_p": 1})
        uid = self.insert("users", username=name or f"st_{self.tag}_{letter}",
                          role=role, sees="all", active=active,
                          may_take_phones=may_take, **hashed)
        self.users.append(uid)
        return uid

    def serial(self) -> str:
        self._n += 1
        serial = f"9{self._base:06d}{self._n:02d}"
        self.serials.append(serial)
        return serial

    def phone(self, **cols) -> str:
        serial = self.serial()
        base = {"serial": serial, "status": "ready", "state": "",
                "purpose": "gpt", "phone_id": "PH" + serial}
        base.update(cols)
        self.insert("phones", **base)
        return serial

    def hold(self, owner: int, **cols) -> str:
        base = {"state": "taken", "owner_id": owner, "taken_at": Raw("now()")}
        base.update(cols)
        return self.phone(**base)

    def job(self, **cols) -> int:
        payload = dict(cols.pop("payload", {}), tag=self.tag)
        base = {"kind": "build", "status": "queued", "payload": payload}
        base.update(cols)
        return self.insert("jobs", **base)

    def action(self, verb: str, serial: str, by: int, **cols) -> int:
        payload = dict(cols.pop("payload", {}), serial=serial)
        base = {"verb": verb, "payload": payload, "requested_by": by,
                "status": "queued"}
        base.update(cols)
        return self.insert("actions", **base)

    def wish(self, by: int, **cols) -> int:
        base = {"requested_by": by, "status": "queued", "station": True}
        base.update(cols)
        return self.insert("wanted_builds", **base)

    def wait(self, uid: int, lane: str = "gpt", **cols) -> int:
        return self.insert("station_line", user_id=uid, lane=lane, **cols)

    def resource(self, kind: str, **cols) -> int:
        return self.insert("resources", kind=kind, note=self.tag, **cols)

    def row(self, serial: str) -> dict:
        return self.one("SELECT * FROM phones WHERE serial = %s"
                        " ORDER BY id DESC LIMIT 1", (serial,))

    def cleanup(self) -> None:
        uids = self.users or [0]
        serials = self.serials or ["-"]
        like = f"%{self.tag}%"
        for text, params in (
                ("DELETE FROM verdicts WHERE serial = ANY(%s) OR by_id = ANY(%s)",
                 (serials, uids)),
                ("DELETE FROM actions WHERE payload->>'serial' = ANY(%s)"
                 " OR requested_by = ANY(%s) OR idem_key LIKE %s",
                 (serials, uids, like)),
                ("DELETE FROM station_line WHERE user_id = ANY(%s)", (uids,)),
                ("DELETE FROM wanted_builds WHERE requested_by = ANY(%s)"
                 " OR called_off_by = ANY(%s) OR serial = ANY(%s)",
                 (uids, uids, serials)),
                ("DELETE FROM jobs WHERE payload->>'tag' = %s"
                 " OR payload->'phone'->>'serial' = ANY(%s)", (self.tag, serials)),
                ("DELETE FROM phones WHERE serial = ANY(%s) OR owner_id = ANY(%s)"
                 " OR last_owner_id = ANY(%s)", (serials, uids, uids)),
                ("DELETE FROM resources WHERE note = %s OR owner_id = ANY(%s)",
                 (self.tag, uids)),
                ("DELETE FROM events WHERE serial = ANY(%s) OR user_id = ANY(%s)",
                 (serials, uids)),
                ("DELETE FROM sessions WHERE user_id = ANY(%s)", (uids,)),
                ("DELETE FROM users WHERE id = ANY(%s)", (uids,))):
            self.sql(text, params)


@pytest.fixture
def farm(make_settings):
    from geelark_farm.store import db as store_db

    parts = dict(p.split("=", 1) for p in DSN.split())
    s = make_settings(
        store_enabled=True, store_host=parts["host"],
        store_port=int(parts.get("port", 5432)), store_db=parts["dbname"],
        store_user=parts["user"], store_password=parts["password"])
    if not _SCHEMA_READY:
        store_db.ensure_schema(s)
        _SCHEMA_READY.append(True)
    f = Farm(s, uuid.uuid4().hex[:8])
    try:
        yield f
    finally:
        f.cleanup()


def _key(f: Farm, word: str) -> str:
    f._n += 1
    return f"{word}:{f.tag}:{f._n}"


# ---------------------------------------------------------------- schema
@needs_cluster
def test_rev_42_is_one_block_and_re_runs(farm):
    from geelark_farm.store import db as store_db

    sql = store_db.schema_sql()
    assert sql.count("-- rev 42:") == 1
    revs = [int(n) for n in re.findall(r"^-- rev (\d+):", sql, re.M)]
    assert max(revs) == 42 and revs[-1] == 42
    a = farm.user("a")
    serial = farm.hold(a, watched_at=Raw("now() - interval '5 minutes'"))
    vid = farm.insert("verdicts", button="done", state="done", serial=serial,
                      phone_id="PH" + serial, lane="spotify", by_id=a)
    before_user = farm.one("SELECT password_changed_at FROM users WHERE id = %s",
                           (a,))
    before_phone = farm.row(serial)
    store_db.ensure_schema(farm.s)
    store_db.ensure_schema(farm.s)
    assert farm.one("SELECT value FROM schema_meta"
                    " WHERE key = 'schema_rev'")["value"] == "42"
    assert farm.one("SELECT password_changed_at FROM users WHERE id = %s",
                    (a,)) == before_user
    assert before_user["password_changed_at"] is not None
    assert farm.row(serial) == before_phone
    assert farm.one("SELECT lane FROM verdicts WHERE id = %s",
                    (vid,))["lane"] == "spotify"


@needs_cluster
def test_rev_42_backfills_a_verdicts_lane_and_phone_row(farm):
    """The backfill joins by phone_id and runs only once per row."""
    from geelark_farm.store import db as store_db

    serial = farm.phone(purpose="spotify")
    pid = farm.row(serial)["id"]
    vid = farm.insert("verdicts", button="done", state="done", serial=serial,
                      phone_id="PH" + serial)
    blank = farm.insert("verdicts", button="done", state="done", serial=serial,
                        phone_id="")
    store_db.ensure_schema(farm.s)
    row = farm.one("SELECT lane, phone_row FROM verdicts WHERE id = %s", (vid,))
    assert row == {"lane": "spotify", "phone_row": pid}
    assert farm.one("SELECT lane, phone_row FROM verdicts WHERE id = %s",
                    (blank,)) == {"lane": "", "phone_row": None}


@needs_cluster
def test_rev_42_heals_a_stale_hold_and_refuses_a_bad_one(farm):
    import psycopg

    from geelark_farm.store import db as store_db

    a = farm.user("a")
    good = farm.hold(a)
    stale = farm.phone()
    try:
        # What rev-41 code leaves behind after a rollback: a taken_at on a
        # phone its writers put back without knowing the column.
        farm.sql("ALTER TABLE phones DROP CONSTRAINT phones_station_hold_shape")
        farm.sql("UPDATE phones SET taken_at = now() WHERE serial = %s", (stale,))
    finally:
        store_db.ensure_schema(farm.s)
    assert farm.row(stale)["taken_at"] is None
    assert farm.row(good)["taken_at"] is not None
    with pytest.raises(psycopg.errors.CheckViolation):
        farm.sql("UPDATE phones SET state = '' WHERE serial = %s", (good,))
    with pytest.raises(psycopg.errors.CheckViolation):
        farm.sql("UPDATE phones SET owner_id = NULL WHERE serial = %s", (good,))


@needs_cluster
def test_one_phone_has_one_pending_power_press(farm):
    import psycopg

    a = farm.user("a")
    one, two = farm.phone(), farm.phone()
    first = farm.action("boot_phone", one, a)
    with pytest.raises(psycopg.errors.UniqueViolation):
        farm.action("boot_phone", one, a)
    with pytest.raises(psycopg.errors.UniqueViolation):
        farm.action("power_off_phone", one, a)
    farm.action("change_proxy", two, a)
    farm.action("login_accounts", one, a)           # not a power press
    assert st.power_pending_of(farm.s, one) == {"id": first, "verb": "boot_phone",
                                                "stale": False}
    assert st.power_pending_of(farm.s, one, ("change_proxy",)) is None
    assert st.expire_power(farm.s, one) == 0        # fresh: left alone
    farm.sql("UPDATE actions SET requested_at = now() - interval '4 minutes'"
             " WHERE id = %s", (first,))
    assert st.power_pending_of(farm.s, one)["stale"] is True
    assert st.expire_power(farm.s, one) == 1
    closed = farm.one("SELECT status, result, finished_at FROM actions"
                      " WHERE id = %s", (first,))
    assert closed["status"] == "failed" and closed["finished_at"] is not None
    assert "three minutes" in closed["result"]
    assert st.power_pending_of(farm.s, one) is None
    farm.action("boot_phone", one, a)                # a new press goes in
    press = st.press_of(farm.s, first)
    assert press["verb"] == "boot_phone" and press["requested_by"] == a
    assert st.press_of(farm.s, 0) is None


# ---------------------------------------------------------- take and line
@needs_cluster
def test_one_take_hands_out_one_phone_oldest_first_and_a_double_press_is_one(farm):
    a = farm.user("a")
    newer = farm.phone(state_at=Raw("now() - interval '1 hour'"))
    older = farm.phone(state_at=Raw("now() - interval '2 hours'"),
                       status="app_only")
    key = _key(farm, "take")
    got = st.take(farm.s, lane="gpt", user_id=a, by="sara", idem_key=key)
    assert got["outcome"] == "took" and got["serial"] == older
    assert got["sentence"] == f"Phone {older} is yours. Press Boot to switch it on."
    assert got["twice"] is False and got["lane"] == "gpt"
    row = farm.row(older)
    assert row["owner_id"] == a and row["state"] == "taken"
    assert row["taken_at"] is not None and row["live_url"] == ""
    again = st.take(farm.s, lane="gpt", user_id=a, by="sara", idem_key=key)
    assert again["twice"] is True and again["serial"] == older
    assert again["outcome"] == "took" and again["action_id"] == got["action_id"]
    assert again["sentence"] == got["sentence"]
    assert farm.row(newer)["owner_id"] is None
    press = farm.one("SELECT verb, status, result, detail, payload, finished_at"
                     " FROM actions WHERE id = %s", (got["action_id"],))
    assert press["verb"] == "take_phone" and press["status"] == "done"
    assert press["detail"] == {"outcome": "took", "lane": "gpt", "serial": older}
    assert press["payload"] == {"lane": "gpt", "by": "sara", "by_id": a}
    assert press["finished_at"] is not None
    assert st.holds(farm.s, older, a) and not st.holds(farm.s, newer, a)
    assert st.station_holder(farm.s, older) == a
    assert st.station_holder(farm.s, newer) is None


@needs_cluster
def test_two_takers_at_once_get_two_phones_or_one_and_a_line(farm):
    a, b = farm.user("a"), farm.user("b")
    phones = {farm.phone(), farm.phone()}

    def race(n_users):
        barrier = threading.Barrier(len(n_users))
        out: dict = {}

        def one(uid):
            barrier.wait()
            out[uid] = st.take(farm.s, lane="gpt", user_id=uid, by=str(uid),
                               idem_key=_key(farm, f"race{uid}"))
        threads = [threading.Thread(target=one, args=(u,)) for u in n_users]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return out

    got = race([a, b])
    assert {g["outcome"] for g in got.values()} == {"took"}
    assert {g["serial"] for g in got.values()} == phones

    c, d = farm.user("c"), farm.user("d")
    last = farm.phone()
    farm.job(payload={"purpose": "gpt"}, status="running",
             claimed_at=Raw("now()"))
    got = race([c, d])
    outcomes = sorted(g["outcome"] for g in got.values())
    assert outcomes == ["line", "took"]
    took = next(g for g in got.values() if g["outcome"] == "took")
    assert took["serial"] == last
    waited = next(g for g in got.values() if g["outcome"] == "line")
    assert waited["position"] == 1


@needs_cluster
def test_a_waiter_who_presses_take_gets_one_phone_and_leaves_the_line(farm):
    a = farm.user("a")
    farm.wait(a)
    first = farm.phone(state_at=Raw("now() - interval '2 hours'"))
    served = st.serve_lines(farm.s, ("gpt",))
    assert served == [{"user_id": a, "serial": first, "lane": "gpt"}]
    assert st.served_recently(farm.s, a, "gpt") == first
    second = farm.phone()
    got = st.take(farm.s, lane="gpt", user_id=a, by="a", idem_key=_key(farm, "t"))
    assert got["outcome"] == "took" and got["serial"] == first
    assert got["sentence"] == (f"Your GPT phone is here: {first}."
                               f" Press Boot to switch it on.")
    assert farm.row(second)["owner_id"] is None, "no second phone"
    note = farm.one("SELECT verb, status, result FROM actions"
                    " WHERE verb = 'serve_line' AND requested_by = %s", (a,))
    assert note["status"] == "done"
    assert note["result"] == (f"Your GPT phone is here: {first}."
                              f" Press Boot to switch it on.")
    farm.sql("UPDATE phones SET status = 'building' WHERE serial = %s", (second,))

    # A waiter the guard skips (they gave this phone back a minute ago) is
    # not served it, but a Take hands it to them and ends their wait.
    b = farm.user("b")
    wait = farm.wait(b)
    back = farm.phone(last_owner_id=b, state_at=Raw("now() - interval '1 minute'"))
    assert st.serve_lines(farm.s, ("gpt",)) == []
    got = st.take(farm.s, lane="gpt", user_id=b, by="b", idem_key=_key(farm, "t"))
    assert got["outcome"] == "took" and got["serial"] == back
    ended = farm.one("SELECT ended_at, ended_why, serial FROM station_line"
                     " WHERE id = %s", (wait,))
    assert ended["ended_why"] == "served" and ended["serial"] == back
    assert st.line_of(farm.s, b) == {"gpt": None, "spotify": None}


@needs_cluster
def test_the_shelf_leaves_out_a_running_a_held_a_busy_and_an_other_phone(farm):
    a, b = farm.user("a"), farm.user("b")
    fine = farm.phone(state_at=Raw("now() - interval '3 hours'"))
    farm.phone(running=True)
    farm.phone(owner_id=b)                              # a legacy reserve
    farm.hold(b)
    farm.phone(state="done")
    farm.phone(status="building")
    farm.phone(status="incomplete")
    farm.phone(purpose="other")
    with_job = farm.phone()
    farm.job(kind="finish", payload={"phone": {"serial": with_job}})
    with_action = farm.phone()
    farm.action("login_accounts", with_action, a)
    booting = farm.phone()
    farm.action("boot_phone", booting, a)
    powering_off = farm.phone()
    farm.action("power_off_phone", powering_off, a)
    orphan = farm.phone(state_at=Raw("now() - interval '1 hour'"))
    farm.action("change_proxy", orphan, a, status="running",
                executed_at=Raw("now() - interval '181 seconds'"))
    spotify = farm.phone(purpose="spotify")
    blank = farm.phone(purpose="", state="unused",
                       state_at=Raw("now() - interval '30 minutes'"))
    shelves = st.shelves(farm.s)
    assert shelves["gpt"]["ready"] == 3                 # fine, orphan, blank
    assert shelves["spotify"]["ready"] == 1
    taken = [st.take(farm.s, lane="gpt", user_id=a, by="a",
                     idem_key=_key(farm, "t"))["serial"] for _ in range(3)]
    assert taken == [fine, orphan, blank]
    last = st.take(farm.s, lane="gpt", user_id=a, by="a", idem_key=_key(farm, "t"))
    assert last["outcome"] == "no"
    assert st.take(farm.s, lane="spotify", user_id=a, by="a",
                   idem_key=_key(farm, "t"))["serial"] == spotify


@needs_cluster
def test_an_empty_shelf_with_a_build_coming_joins_the_line_once(farm):
    a, b = farm.user("a"), farm.user("b")
    farm.job(payload={"purpose": "spotify"})
    farm.job(payload={"want": {"wanted_id": 1}, "purpose": "gpt"})  # a wish
    assert st.take(farm.s, lane="gpt", user_id=a, by="a",
                   idem_key=_key(farm, "t"))["outcome"] == "no"
    got = st.take(farm.s, lane="spotify", user_id=a, by="a",
                  idem_key=_key(farm, "t"))
    assert got["outcome"] == "line" and got["position"] == 1
    assert got["sentence"] == "You are first in line for the next Spotify phone."
    again = st.take(farm.s, lane="spotify", user_id=a, by="a",
                    idem_key=_key(farm, "t"))
    assert again["outcome"] == "line" and again["position"] == 1
    assert again["sentence"] == "You are already in line for the next Spotify phone."
    second = st.take(farm.s, lane="spotify", user_id=b, by="b",
                     idem_key=_key(farm, "t"))
    assert second["position"] == 2
    assert second["sentence"] == ("You are number 2 in line for the next"
                                  " Spotify phone.")
    rows = farm.sql("SELECT id FROM station_line WHERE user_id = %s"
                    " AND ended_at IS NULL", (a,))
    assert len(rows) == 1
    line = st.line_of(farm.s, a)
    assert line["gpt"] is None
    assert line["spotify"]["position"] == 1 and line["spotify"]["id"] == rows[0]["id"]
    assert st.line_of(farm.s, b)["spotify"]["position"] == 2


@needs_cluster
def test_an_empty_shelf_with_nothing_coming_says_so(farm):
    a = farm.user("a")
    got = st.take(farm.s, lane="gpt", user_id=a, by="a", idem_key=_key(farm, "t"))
    assert got["outcome"] == "no" and got["serial"] == "" and got["position"] == 0
    assert got["sentence"] == "Nothing on the GPT shelf, and nothing is being built."
    row = farm.one("SELECT status, result, detail FROM actions WHERE id = %s",
                   (got["action_id"],))
    assert row["status"] == "refused" and row["result"] == got["sentence"]
    assert row["detail"] == {"outcome": "no", "lane": "gpt"}


@needs_cluster
def test_the_line_is_served_to_its_head_and_never_its_own_give_back(farm):
    stale, a, b = farm.user("c"), farm.user("a"), farm.user("b")
    farm.wait(stale, joined_at=Raw("now() - interval '30 minutes'"),
              seen_at=Raw("now() - interval '2 minutes'"))
    farm.wait(a, joined_at=Raw("now() - interval '20 minutes'"))
    farm.wait(b, joined_at=Raw("now() - interval '10 minutes'"))
    mine = farm.phone(last_owner_id=a, state_at=Raw("now() - interval '1 minute'"))
    assert st.serve_lines(farm.s) == [{"user_id": b, "serial": mine, "lane": "gpt"}]
    assert farm.one("SELECT verb FROM actions WHERE requested_by = %s"
                    " AND verb = 'serve_line'", (b,))
    assert farm.row(mine)["owner_id"] == b
    older = farm.phone(last_owner_id=a, state_at=Raw("now() - interval '11 minutes'"))
    assert st.serve_lines(farm.s) == [{"user_id": a, "serial": older, "lane": "gpt"}]
    assert st.line_of(farm.s, stale)["gpt"]["position"] == 1
    assert farm.row(older)["taken_at"] is not None


@needs_cluster
def test_the_line_serves_only_those_who_may_take_phones(farm):
    untick = farm.user("a", may_take=False)
    gone = farm.user("b", active=False)
    admin = farm.user("c", role="admin", may_take=False)
    farm.wait(untick, joined_at=Raw("now() - interval '3 minutes'"))
    farm.wait(gone, joined_at=Raw("now() - interval '2 minutes'"))
    farm.wait(admin, joined_at=Raw("now() - interval '1 minute'"))
    one = farm.phone()
    assert st.serve_lines(farm.s, ("gpt",)) == [
        {"user_id": admin, "serial": one, "lane": "gpt"}]
    farm.phone()
    assert st.serve_lines(farm.s, ("gpt",)) == []


@needs_cluster
def test_a_wait_nobody_polls_ends_as_gone(farm):
    a, b = farm.user("a"), farm.user("b")
    old = farm.wait(a, seen_at=Raw("now() - interval '11 minutes'"),
                    updated_at=Raw("now() - interval '11 minutes'"))
    fresh = farm.wait(b, seen_at=Raw("now() - interval '9 minutes'"),
                      updated_at=Raw("now() - interval '1 hour'"))
    before = farm.one("SELECT seen_at, updated_at FROM station_line WHERE id = %s",
                      (fresh,))
    st.stamp_line(farm.s, b)
    after = farm.one("SELECT seen_at, updated_at FROM station_line WHERE id = %s",
                     (fresh,))
    assert after["seen_at"] > before["seen_at"]
    assert after["updated_at"] == before["updated_at"], "a poll is not news"
    assert st.serve_lines(farm.s) == []
    ended = farm.one("SELECT ended_why, ended_at, updated_at FROM station_line"
                     " WHERE id = %s", (old,))
    assert ended["ended_why"] == "gone" and ended["ended_at"] is not None
    assert farm.one("SELECT ended_at FROM station_line WHERE id = %s",
                    (fresh,))["ended_at"] is None


@needs_cluster
def test_leaving_the_line_is_recorded_and_too_late_says_so(farm):
    a = farm.user("a")
    farm.wait(a)
    key = _key(farm, "leave")
    got = st.leave_line(farm.s, lane="gpt", user_id=a, by="a", idem_key=key)
    assert got["outcome"] == "left" and got["twice"] is False
    assert got["sentence"] == "You left the line for a GPT phone."
    row = farm.one("SELECT verb, status, result, detail FROM actions WHERE id = %s",
                   (got["action_id"],))
    assert row["verb"] == "leave_line" and row["status"] == "done"
    assert row["detail"] == {"lane": "gpt", "outcome": "left"}
    twin = st.leave_line(farm.s, lane="gpt", user_id=a, by="a", idem_key=key)
    assert twin["twice"] is True and twin["outcome"] == "left"
    assert twin["action_id"] == got["action_id"]
    not_in = st.leave_line(farm.s, lane="gpt", user_id=a, by="a",
                           idem_key=_key(farm, "leave"))
    assert not_in["outcome"] == "no"
    assert not_in["sentence"] == "You are not in line for a GPT phone."
    assert farm.one("SELECT status FROM actions WHERE id = %s",
                    (not_in["action_id"],))["status"] == "refused"
    farm.wait(a, lane="spotify")
    phone = farm.phone(purpose="spotify")
    st.serve_lines(farm.s)
    late = st.leave_line(farm.s, lane="spotify", user_id=a, by="a",
                         idem_key=_key(farm, "leave"))
    assert late["outcome"] == "no"
    assert late["sentence"] == f"Too late - phone {phone} was already yours."


# ------------------------------------------------------------- the hold
@needs_cluster
def test_give_back_goes_to_the_back_of_the_shelf_and_queues_its_power_off(farm):
    a, b = farm.user("a"), farm.user("b")
    serial = farm.hold(a, gmail="g@x.com", proxy_name="PC2", running=True,
                       state_at=Raw("now() - interval '30 minutes'"),
                       live_url="https://view/1",
                       watched_at=Raw("now() - interval '1 minute'"))
    before = farm.row(serial)
    assert st.give_back(farm.s, serial=serial, owner_id=b, by="b") is None
    got = st.give_back(farm.s, serial=serial, owner_id=a, by="sara")
    assert got["lane"] == "gpt" and got["running"] is True
    assert got["id"] == before["id"] and isinstance(got["off_id"], int)
    row = farm.row(serial)
    assert row["state"] == "" and row["owner_id"] is None
    assert row["last_owner_id"] == a and row["taken_at"] is None
    assert row["state_at"] > before["state_at"]
    assert row["gmail"] == "g@x.com" and row["proxy_name"] == "PC2"
    assert row["live_url"] == "" and row["watched_at"] is None
    off = farm.sql("SELECT id, payload, requested_by, status FROM actions"
                   " WHERE verb = 'power_off_phone' AND payload->>'serial' = %s",
                   (serial,))
    assert len(off) == 1 and off[0]["id"] == got["off_id"]
    assert off[0]["payload"] == {"serial": serial, "by": "sara", "by_id": a,
                                 "why": "given back"}
    assert off[0]["requested_by"] == a and off[0]["status"] == "queued"
    farm.sql("UPDATE phones SET running = false WHERE serial = %s", (serial,))
    assert st.shelves(farm.s)["gpt"]["ready"] == 0, "off every shelf until off"
    farm.sql("UPDATE actions SET status = 'done' WHERE id = %s", (got["off_id"],))
    assert st.shelves(farm.s)["gpt"]["ready"] == 1, "then back of the shelf"
    assert st.give_back(farm.s, serial=serial, owner_id=a, by="sara") is None
    assert st.hold_state(farm.s, serial)["station"] is False

    busy = farm.hold(a)
    farm.action("boot_phone", busy, a)
    assert st.give_back(farm.s, serial=busy, owner_id=a, by="a") is None
    state = st.hold_state(farm.s, busy)
    assert state == {"state": "taken", "owner_id": a, "station": True,
                     "status": "ready", "busy": "boot_phone"}
    assert st.hold_state(farm.s, "0") is None

    closing = farm.hold(a, running=True)
    farm.action("power_off_phone", closing, a, payload={"why": "closed"})
    got = st.give_back(farm.s, serial=closing, owner_id=a, by="a")
    assert got is not None and got["off_id"] is None, "the pending one runs"

    other = farm.hold(a, purpose="other")
    wid = farm.wish(a, serial=other, purpose="other", carry_address="m@p.me",
                    carry_password="secret-pw", status="done")
    got = st.give_back(farm.s, serial=other, owner_id=a, by="a")
    assert got["lane"] == "other" and st.home(got["lane"]) == "the farm"
    carried = farm.one("SELECT carry_address, carry_password FROM wanted_builds"
                       " WHERE id = %s", (wid,))
    assert carried == {"carry_address": "m@p.me", "carry_password": ""}
    farm.sql("UPDATE phones SET running = false WHERE serial = %s", (other,))
    farm.sql("UPDATE actions SET status = 'done' WHERE payload->>'serial' = %s",
             (other,))
    assert st.shelves(farm.s)["gpt"]["ready"] == 1, "an Other phone is on none"


@needs_cluster
def test_the_station_beat_and_close_are_the_holders_only(farm):
    a, b = farm.user("a"), farm.user("b")
    serial = farm.hold(a, updated_at=Raw("now() - interval '1 hour'"))

    def stamp():
        return farm.row(serial)["updated_at"]

    old = stamp()
    assert st.watch(farm.s, serial, b, 180) is False
    assert stamp() == old
    assert st.watch(farm.s, serial, a, 180) is True
    moved = stamp()
    assert moved > old, "the first beat is a transition"
    farm.sql("UPDATE phones SET updated_at = now() - interval '1 hour'"
             " WHERE serial = %s", (serial,))
    old = stamp()
    assert st.watch(farm.s, serial, a, 180) is True
    assert stamp() == old, "a steady beat costs the stream nothing"
    assert st.is_watched(farm.s, serial, 180) is True
    assert st.tab_closed(farm.s, serial, b) is False
    assert st.tab_closed(farm.s, serial, a) is True
    assert stamp() > old
    assert st.is_watched(farm.s, serial, 180) is False
    farm.sql("UPDATE phones SET updated_at = now() - interval '1 hour'"
             " WHERE serial = %s", (serial,))
    old = stamp()
    assert st.watch(farm.s, serial, a, 180) is True
    assert stamp() > old, "a reopened tab is heard at once"
    assert farm.row(serial)["tab_closed_at"] is None
    # silent past the grace is a transition too
    farm.sql("UPDATE phones SET updated_at = now() - interval '1 hour',"
             " watched_at = now() - interval '200 seconds' WHERE serial = %s",
             (serial,))
    old = stamp()
    assert st.is_watched(farm.s, serial, 180) is False
    assert st.watch(farm.s, serial, a, 180) is True
    assert stamp() > old


@needs_cluster
def test_the_old_beat_and_close_never_reach_a_station_hold_of_somebody_else(farm):
    from geelark_farm.store import person

    a, b = farm.user("a"), farm.user("b")
    station = farm.hold(a)
    legacy = farm.phone(state="taken", owner_id=b)
    assert person.watch(farm.s, station, b) is False
    assert person.watch(farm.s, station) is False
    assert person.tab_closed(farm.s, station, b) is False
    assert farm.row(station)["watched_at"] is None
    assert farm.row(station)["tab_closed_at"] is None
    assert person.watch(farm.s, station, a) is True
    assert person.tab_closed(farm.s, station, a) is True
    assert person.watch(farm.s, legacy, a) is True
    assert person.watch(farm.s, legacy) is True
    assert person.tab_closed(farm.s, legacy) is True
    assert person.watch(farm.s, "") is False


@needs_cluster
def test_booted_and_powered_off_write_the_link_and_the_power(farm):
    a, b = farm.user("a"), farm.user("b")
    serial = farm.hold(a, running_since=Raw("now() - interval '1 hour'"),
                       tab_closed_at=Raw("now() - interval '1 minute'"))
    assert st.booted(farm.s, serial, "https://v/1", owner_id=b) is False
    assert farm.row(serial)["running"] is False
    assert st.booted(farm.s, serial, "https://v/1", owner_id=a) is True
    row = farm.row(serial)
    now = farm.now()
    assert row["live_url"] == "https://v/1" and row["running"] is True
    assert abs((row["running_since"] - now).total_seconds()) < 5
    assert row["watched_at"] is not None and row["tab_closed_at"] is None
    assert st.stored_link(farm.s, serial) == "https://v/1"
    farm.sql("UPDATE phones SET running_since = now() - interval '1 hour'"
             " WHERE serial = %s", (serial,))
    kept = farm.row(serial)["running_since"]
    assert st.booted(farm.s, serial, "http://plain/2", owner_id=a,
                     started=False) is True
    row = farm.row(serial)
    assert row["live_url"] == "" and row["running_since"] == kept
    legacy = farm.phone()
    assert st.booted(farm.s, legacy, "https://v/3") is True
    assert st.powered_off(farm.s, serial) is True
    row = farm.row(serial)
    assert row["running"] is False and row["running_since"] is None
    assert row["live_url"] == ""
    assert st.stored_link(farm.s, serial) == ""
    assert st.stored_link(farm.s, "0") == ""
    assert st.powered_off(farm.s, "0") is False


@needs_cluster
def test_land_turns_a_station_wish_into_a_station_hold_and_leaves_a_dashboard_wish_kept(
        farm):
    a = farm.user("a")
    serial = farm.phone(owner_id=a, status="ready")
    wid = farm.wish(a, serial=serial, status="done")
    assert st.land(farm.s, serial=serial, wanted_id=wid) is True
    row = farm.row(serial)
    assert row["state"] == "taken" and row["taken_at"] is not None
    assert st.holds(farm.s, serial, a)
    kept = farm.phone(owner_id=a)
    dash = farm.wish(a, serial=kept, station=False, status="done")
    assert st.land(farm.s, serial=kept, wanted_id=dash) is False
    assert farm.row(kept)["state"] == "" and farm.row(kept)["taken_at"] is None
    building = farm.phone(owner_id=a, status="building")
    wid = farm.wish(a, serial=building, status="running")
    assert st.land(farm.s, serial=building, wanted_id=wid) is False
    stranger = farm.phone()
    wid = farm.wish(a, serial=stranger)
    assert st.land(farm.s, serial=stranger, wanted_id=wid) is False


@needs_cluster
def test_the_sweep_rows_are_alone_after_an_hour_unwatched_and_closed_after_twenty_seconds(  # noqa: E501
        farm):
    a = farm.user("a")
    alone = farm.hold(a, taken_at=Raw("now() - interval '2 hours'"),
                      watched_at=Raw("now() - interval '90 minutes'"))
    closed = farm.hold(a, taken_at=Raw("now() - interval '10 minutes'"),
                       running=True,
                       watched_at=Raw("now() - interval '2 minutes'"),
                       tab_closed_at=Raw("now() - interval '30 seconds'"))
    young = farm.hold(a, running=True,
                      tab_closed_at=Raw("now() - interval '5 seconds'"))
    watching = farm.hold(a, taken_at=Raw("now() - interval '2 hours'"),
                         running=True, watched_at=Raw("now() - interval '10 seconds'"))
    booting = farm.hold(a, taken_at=Raw("now() - interval '2 hours'"))
    farm.action("boot_phone", booting, a)
    never = farm.hold(a, running=True, taken_at=Raw("now() - interval '5 minutes'"))
    farm.phone(state="taken", owner_id=a, running=True,
               state_at=Raw("now() - interval '3 hours'"))     # legacy
    rows = {r["serial"]: r for r in st.overdue(farm.s, 60, 180, closed=20)
            if r["serial"] in farm.serials}
    assert set(rows) == {alone, closed, never}
    assert rows[alone]["why"] == "alone" and rows[closed]["why"] == "closed"
    assert rows[never]["why"] == "closed"
    assert rows[alone]["owner"] == f"st_{farm.tag}_a"
    assert isinstance(rows[alone]["idle_seconds"], float)
    assert rows[alone]["idle_seconds"] > 3500
    assert rows[closed]["closed_seconds"] > 25
    assert rows[never]["closed_seconds"] is None
    assert st.still_over(farm.s, alone, 60, 180, 20) == "alone"
    assert st.still_over(farm.s, closed, 60, 180, 20) == "closed"
    assert st.still_over(farm.s, watching, 60, 180, 20) == ""
    assert st.still_over(farm.s, young, 60, 180, 20) == ""
    assert st.still_over(farm.s, booting, 60, 180, 20) == ""

    assert st.give_back_idle(farm.s, closed, 60, 180) is None
    assert st.give_back_idle(farm.s, watching, 60, 180) is None
    assert st.give_back_idle(farm.s, booting, 60, 180) is None
    back = st.give_back_idle(farm.s, alone, 60, 180)
    assert back["owner_id"] == a and back["lane"] == "gpt"
    assert isinstance(back["off_id"], int)
    why = farm.one("SELECT payload FROM actions WHERE id = %s",
                   (back["off_id"],))["payload"]
    assert why == {"serial": alone, "by": "the hour", "by_id": a, "why": "alone"}
    assert farm.row(alone)["last_owner_id"] == a

    first = st.queue_off_closed(farm.s, closed, 180, 20)
    assert isinstance(first, int)
    assert farm.one("SELECT payload FROM actions WHERE id = %s",
                    (first,))["payload"] == {"serial": closed, "by": "the station",
                                             "by_id": a, "why": "closed"}
    assert st.queue_off_closed(farm.s, closed, 180, 20) is None
    assert st.queue_off_closed(farm.s, watching, 180, 20) is None
    assert st.queue_off_closed(farm.s, young, 180, 20) is None
    assert st.is_watched(farm.s, watching, 180) is True
    assert st.is_watched(farm.s, closed, 180) is False


@needs_cluster
def test_a_warm_phone_is_reserved_once_and_never_after_a_take(farm):
    a, b = farm.user("a"), farm.user("b")
    warm = farm.phone(status="app_only")
    assert st.reserve_warm(farm.s, warm) == "app_only"
    assert farm.row(warm)["status"] == "building"
    assert st.reserve_warm(farm.s, warm) is None
    assert st.unreserve_warm(farm.s, warm, "app_only") is True
    assert farm.row(warm)["status"] == "app_only"
    assert st.take(farm.s, lane="gpt", user_id=a, by="a",
                   idem_key=_key(farm, "t"))["serial"] == warm
    assert st.reserve_warm(farm.s, warm) is None
    assert st.reserve_warm(farm.s, warm, owner_id=a) is None, "a hold is no reserve"
    reserved = farm.phone(owner_id=a, status="ready")
    assert st.reserve_warm(farm.s, reserved, owner_id=b) is None
    assert st.reserve_warm(farm.s, reserved) is None
    assert st.reserve_warm(farm.s, reserved, owner_id=a) == "ready"
    farm.job(kind="finish", payload={"phone": {"serial": reserved}})
    assert st.unreserve_warm(farm.s, reserved, "ready") is False
    assert farm.row(reserved)["status"] == "building"


# ------------------------------------------------------ what a page reads
@needs_cluster
def test_mine_reads_power_secrets_and_the_last_press(farm):
    a, b = farm.user("a"), farm.user("b")
    gmail = f"g{farm.tag}@example.com"
    farm.resource("gmail", address=gmail.upper(), password="gpw",
                  totp_secret="TOTP")
    booting = farm.hold(a, gmail=gmail, watched_at=Raw("now()"),
                        taken_at=Raw("now() - interval '3 minutes'"),
                        proxy_name="PC2")
    farm.action("boot_phone", booting, a)
    orphan = farm.hold(a, gmail=st.CROSS, running=True, live_url="https://v/9",
                       taken_at=Raw("now() - interval '2 minutes'"))
    farm.action("boot_phone", orphan, a, status="running",
                executed_at=Raw("now() - interval '181 seconds'"))
    moved = farm.hold(a, taken_at=Raw("now() - interval '1 minute'"),
                      tab_closed_at=Raw("now()"))
    last = farm.action("change_proxy", moved, a, status="done",
                       result="phone moved", finished_at=Raw("now()"),
                       detail={"was": "PC1", "now": "PC8", "started": False})
    farm.action("change_proxy", moved, b, status="done", finished_at=Raw("now()"),
                payload={"other": 1})                          # not the holder's
    wid = farm.wish(a, serial=moved, status="done",
                    called_off_at=Raw("now()"))
    farm.hold(b)
    farm.hold(a, status="building")
    rows = st.mine(farm.s, a, 180)
    assert [r["serial"] for r in rows] == [booting, orphan, moved]
    one, two, three = rows
    assert one["busy"] == "boot_phone" and one["lane"] == "gpt"
    assert one["gmail_password"] == "gpw" and one["totp"] == "TOTP"
    assert one["watching"] is True and one["tab_seen"] is True
    assert one["idle_since"] is not None and one["wish"] is None
    assert two["busy"] is None, "an orphan boot is not busy"
    assert two["gmail_password"] == "" and two["totp"] == ""
    assert two["tab_seen"] is False and two["live_url"] == "https://v/9"
    assert two["running"] is True and two["tab_closed"] is False
    assert three["tab_closed"] is True and three["tab_seen"] is True
    assert three["last_id"] == last and three["last_verb"] == "change_proxy"
    assert three["last_status"] == "done" and three["last_result"] == "phone moved"
    assert three["last_detail"] == {"was": "PC1", "now": "PC8", "started": False}
    assert three["wish"] == wid and three["called_off"] is True
    assert three["from_line"] is None
    # a phone the line served is marked as such
    c = farm.user("c")
    farm.wait(c)
    served = farm.phone()
    st.serve_lines(farm.s, ("gpt",))
    assert st.mine(farm.s, c, 180)[0]["serial"] == served
    assert st.mine(farm.s, c, 180)[0]["from_line"] is not None


@needs_cluster
def test_the_live_phone_shows_a_carried_account_only_to_who_asked_for_it(farm):
    a, b = farm.user("a"), farm.user("b")
    other = farm.hold(a, purpose="other")
    farm.wish(a, serial=other, purpose="other", carry_address="m@p.me",
              carry_password="pw1", status="done")
    row = st.live_phone(farm.s, other, 180)
    assert row["lane"] == "other" and row["owner_id"] == a
    assert row["carry_address"] == "m@p.me" and row["carry_password"] == "pw1"
    assert row["acct_address"] is None and row["state"] == "taken"
    farm.sql("UPDATE phones SET owner_id = %s WHERE serial = %s", (b, other))
    row = st.live_phone(farm.s, other, 180)
    assert row["carry_address"] == "" and row["carry_password"] == ""

    app = f"app{farm.tag}@example.com"
    farm.resource("app", address=app, password="apw", product="spotify",
                  category="normal")
    gpt = farm.hold(a, app_account=app.upper(), purpose="spotify")
    row = st.live_phone(farm.s, gpt, 180)
    assert row["acct_address"] == app and row["acct_password"] == "apw"
    assert row["acct_product"] == "spotify" and row["acct_category"] == "normal"
    assert row["acct_email_code"] is False
    bare = farm.hold(a, app_account=st.CROSS)
    assert st.live_phone(farm.s, bare, 180)["acct_address"] is None
    farm.sql("UPDATE phones SET done_at = now(), state = 'done', taken_at = NULL"
             " WHERE serial = %s", (gpt,))
    closed = st.live_phone(farm.s, gpt, 180)
    assert closed["done_at"] is not None and closed["state"] == "done"
    assert st.live_phone(farm.s, "0", 180) is None


@needs_cluster
def test_builds_of_lists_open_and_failed_wishes_and_hides_one_that_landed(farm):
    a, b = farm.user("a"), farm.user("b")
    queued = farm.wish(a, purpose="Spotify", gmail="x@y.z")
    running = farm.wish(a, status="running", proxy_name="1.2.3.4:80:u:p")
    farm.job(status="running", claimed_at=Raw("now() - interval '1 minute'"),
             payload={"want": {"wanted_id": running}})
    failed = farm.wish(a, status="failed", detail="no Gmail left",
                       ended_at=Raw("now() - interval '1 hour'"))
    farm.wish(a, status="failed", ended_at=Raw("now() - interval '1 hour'"),
              dismissed_at=Raw("now()"))
    farm.wish(a, status="failed", ended_at=Raw("now() - interval '25 hours'"))
    farm.wish(a, status="queued", station=False)
    farm.wish(b)
    landed_on = farm.hold(a, proxy_name="PC4", gmail="l@x.com")
    farm.wish(a, status="running", serial=landed_on)
    on_its_way = farm.phone(owner_id=a, status="building", proxy_name="PC5",
                            gmail="w@x.com")
    coming = farm.wish(a, status="running", serial=on_its_way)
    rows = st.builds_of(farm.s, a)
    assert [r["id"] for r in rows] == [queued, running, failed, coming]
    assert rows[0]["purpose"] == "spotify" and rows[0]["job_status"] is None
    assert rows[1]["job_status"] == "running" and rows[1]["claimed_at"] is not None
    assert rows[2]["detail"] == "no Gmail left"
    assert rows[3]["phone_exit"] == "PC5" and rows[3]["phone_gmail"] == "w@x.com"
    assert rows[0]["phone_exit"] == ""


@needs_cluster
def test_typical_is_the_median_with_a_six_minute_fallback(farm):
    for seconds in (300, 400, 900):
        farm.job(status="done", payload={"purpose": "gpt"},
                 claimed_at=Raw(f"now() - interval '{seconds + 60} seconds'"),
                 done_at=Raw("now() - interval '60 seconds'"),
                 result={"worked": True})
    for seconds in (100, 200):
        farm.job(status="done", payload={"purpose": "spotify"},
                 claimed_at=Raw(f"now() - interval '{seconds} seconds'"),
                 done_at=Raw("now()"), result={"worked": True})
    for seconds in (50, 60, 70):
        farm.job(status="done", payload={"want": {"purpose": "other"}},
                 claimed_at=Raw(f"now() - interval '{seconds} seconds'"),
                 done_at=Raw("now()"), result={"worked": True})
    farm.job(status="done", payload={"purpose": "gpt"},
             claimed_at=Raw("now() - interval '5000 seconds'"),
             done_at=Raw("now()"), result={"worked": False})
    usual = st.typical(farm.s)
    assert usual["gpt"] == pytest.approx(400.0)
    assert usual["spotify"] == 360.0
    assert usual["other"] == pytest.approx(60.0)
    assert all(isinstance(v, float) for v in usual.values())


@needs_cluster
def test_the_shelves_answer_is_stable_between_reads(farm):
    farm.phone()
    farm.phone(purpose="spotify")
    farm.job(status="running", payload={"purpose": "gpt"},
             claimed_at=Raw("now() - interval '60 seconds'"))
    farm.job(status="running", payload={"purpose": "gpt"},
             claimed_at=Raw("now() - interval '2 hours'"))
    farm.job(payload={"purpose": "spotify"})
    first = st.shelves(farm.s)
    time.sleep(1)
    second = st.shelves(farm.s)
    assert first == second
    assert first["gpt"]["ready"] == 1 and first["gpt"]["building"] == 2
    assert first["gpt"]["late"] == 1 and len(first["gpt"]["etas"]) == 1
    assert first["gpt"]["etas"][0] > datetime.now(timezone.utc)
    assert first["spotify"] == {"ready": 1, "building": 1, "etas": [], "late": 0,
                                "typical_s": 360.0}


# --------------------------------------------------------------- verdicts
@needs_cluster
def test_a_verdict_closes_once_and_writes_one_row_with_lane_and_phone_row(farm):
    from geelark_farm.store import verdicts

    a, b = farm.user("a"), farm.user("b")
    name = f"PX{farm.tag}"
    farm.resource("proxy", host="h1.x", port=1080, username=f"u1{farm.tag}",
                  proxy_name=name)
    farm.resource("proxy", host="h2.x", port=1081, username=f"u2{farm.tag}",
                  proxy_name=name)
    serial = farm.hold(a, gmail="G@X.com", proxy_name=name, exit_ip="5.6.7.8",
                       app_account="acc", purpose="spotify", live_url="https://v")
    pid = farm.row(serial)["id"]
    with pytest.raises(ValueError):
        verdicts.close(farm.s, serial=serial, button="auth", state="taken")
    row = verdicts.close(farm.s, serial=serial, button="auth", state="failed",
                         by="sara" * 30, by_id=str(a), where="station-live-tab",
                         owner_id=a)
    assert row["button"] == "auth" and row["lane"] == "spotify"
    assert row["serial"] == serial and row["gmail"] == "g@x.com"
    assert row["proxy_name"] == name and row["exit_ip"] == "5.6.7.8"
    kept = farm.sql("SELECT * FROM verdicts WHERE serial = %s", (serial,))
    assert len(kept) == 1, "one row, whatever shares the exit name"
    v = kept[0]
    assert v["phone_row"] == pid and v["by_id"] == a and len(v["by_name"]) == 80
    assert v["pressed_on"] == "station-live-tab"[:16] and v["state"] == "failed"
    assert v["proxy_host"] == "h2.x" and v["phone_id"] == "PH" + serial
    phone = farm.row(serial)
    assert phone["state"] == "failed" and phone["owner_id"] is None
    assert phone["taken_at"] is None and phone["live_url"] == ""
    assert verdicts.close(farm.s, serial=serial, button="done",
                          state="done") is None
    assert len(farm.sql("SELECT id FROM verdicts WHERE serial = %s",
                        (serial,))) == 1
    assert verdicts.standing(farm.s, serial)["state"] == "failed"

    theirs = farm.hold(a)
    assert verdicts.close(farm.s, serial=theirs, button="done", state="done",
                          owner_id=b) is None
    assert verdicts.standing(farm.s, theirs) == {"state": "taken", "owner_id": a,
                                                 "status": "ready", "busy": ""}
    building = farm.phone(status="building")
    assert verdicts.close(farm.s, serial=building, button="done",
                          state="done") is None
    moving = farm.hold(a)
    farm.action("change_proxy", moving, a)
    assert verdicts.close(farm.s, serial=moving, button="or", state="failed",
                          owner_id=a) is None
    assert verdicts.standing(farm.s, moving)["busy"] == "change_proxy"
    assert verdicts.standing(farm.s, "0") is None

    other = farm.hold(a, purpose="other")
    wid = farm.wish(a, serial=other, purpose="other", carry_address="m@p",
                    carry_password="pw", status="done")
    done = verdicts.close(farm.s, serial=other, button="done", state="done",
                          by_id="x", owner_id=a)
    assert done["lane"] == "other"
    assert farm.one("SELECT carry_password FROM wanted_builds WHERE id = %s",
                    (wid,))["carry_password"] == ""
    assert farm.one("SELECT by_id FROM verdicts WHERE id = %s",
                    (done["id"],))["by_id"] is None
    legacy = farm.phone(state="unused")
    assert verdicts.close(farm.s, serial=legacy, button="decline",
                          state="failed")["lane"] == "gpt"


@needs_cluster
def test_today_counts_the_same_rows_it_lists_and_skips_phantoms(farm):
    from geelark_farm.store import verdicts

    a, b = farm.user("a"), farm.user("b")
    one, two = farm.hold(a), farm.hold(a, purpose="spotify")
    first = verdicts.close(farm.s, serial=one, button="done", state="done",
                           by_id=a, owner_id=a)
    second = verdicts.close(farm.s, serial=two, button="decline", state="failed",
                            by_id=a, owner_id=a)
    farm.insert("verdicts", button="done", state="done", by_id=a, phone_id="")
    farm.insert("verdicts", button="bogus", state="failed", by_id=a,
                phone_id="PH1")
    legacy = farm.insert("verdicts", button="or", state="failed", by_id=a,
                         phone_id="PH2", serial="1500")
    farm.insert("verdicts", button="done", state="done", by_id=a, phone_id="PH3",
                at=Raw("now() - interval '2 days'"))
    farm.insert("verdicts", button="done", state="done", by_id=b, phone_id="PH4")
    now = datetime.now(timezone.utc)
    rows = verdicts.of_person(farm.s, a, now - timedelta(hours=1),
                              now + timedelta(hours=1))
    assert [r["id"] for r in rows] == [legacy, second["id"], first["id"]]
    assert [r["lane"] for r in rows] == ["gpt", "spotify", "gpt"]
    assert {r["button"] for r in rows} <= set(verdicts.KEYS)
    assert verdicts.KEYS == ("done", "decline", "or", "auth", "failed")
    assert verdicts.BUTTONS["auth"] == "failed"


# ------------------------------------------------------------------ users
@needs_cluster
def test_a_username_is_unique_whatever_its_case_and_follows_the_rule(farm):
    a, b = farm.user("a"), farm.user("b")
    farm.user("c", name=f"ST_{farm.tag}_C")
    new = f"sa.{farm.tag}_1"
    assert store_users.set_username(farm.s, a, "  " + new.upper() + " ") == new
    assert farm.one("SELECT username FROM users WHERE id = %s",
                    (a,))["username"] == new
    assert store_users.set_username(farm.s, a, new) == new
    with pytest.raises(ValueError, match=r"^That username is taken\.$"):
        store_users.set_username(farm.s, b, new.upper())
    with pytest.raises(ValueError, match=r"^That username is taken\.$"):
        store_users.set_username(farm.s, b, f"st_{farm.tag}_c")
    for bad in ("ab", "a" * 21, "a b c", "abc-d", "café"):
        with pytest.raises(ValueError, match="^3 to 20 small letters, digits, "
                                             r"dots or underscores\.$"):
            store_users.set_username(farm.s, b, bad)
    for bad in ("_abc", ".abc"):
        with pytest.raises(ValueError, match=r"^Start it with a letter or a digit\.$"):
            store_users.set_username(farm.s, b, bad)
    assert store_users.set_name(farm.s, a, "Sara") == "Sara"
    assert farm.one("SELECT display_name FROM users WHERE id = %s",
                    (a,))["display_name"] == "Sara"
    gone = farm.user("d", active=False)
    with pytest.raises(ValueError, match=r"^Your account is not active\.$"):
        store_users.set_name(farm.s, gone, "X")
    with pytest.raises(ValueError, match=r"^That username is taken\.$"):
        store_users.set_username(farm.s, gone, f"zz{farm.tag}")


@needs_cluster
def test_a_password_change_checks_the_current_one_and_rotates_the_seats_in_one_go(
        farm, monkeypatch):
    from geelark_farm.store import auth, sessions

    a = farm.user("a", password="old-password-1")
    b = farm.user("b")
    farm.sql("UPDATE users SET must_change_password = true,"
             " password_changed_at = now() - interval '9 days' WHERE id = %s", (a,))
    mine, csrf = sessions.start(farm.s, a, hours=1)
    other, _ = sessions.start(farm.s, a, hours=1)
    theirs, _ = sessions.start(farm.s, b, hours=1)

    def hash_of():
        return bytes(farm.one("SELECT password_hash FROM users WHERE id = %s",
                              (a,))["password_hash"])

    old_hash = hash_of()
    with pytest.raises(store_users.WrongPassword,
                       match=r"^That is not your current password\.$"):
        store_users.change_password(farm.s, a, "wrong", "new-password-2",
                                    token=mine, hours=1)
    with pytest.raises(ValueError, match="at least 8 characters"):
        store_users.change_password(farm.s, a, "old-password-1", "short",
                                    token=mine, hours=1)
    with pytest.raises(ValueError, match="the same as the current one"):
        store_users.change_password(farm.s, a, "old-password-1", "old-password-1",
                                    token=mine, hours=1)
    with pytest.raises(ValueError, match="at most 256"):
        store_users.change_password(farm.s, a, "old-password-1", "x" * 257,
                                    token=mine, hours=1)

    # The optimistic guard: the hash changed between the check and the write.
    real = store_users._verified
    monkeypatch.setattr(store_users, "_verified",
                        lambda s, uid, cur: dict(real(s, uid, cur),
                                                 password_hash=b"other"))
    with pytest.raises(ValueError, match="changed somewhere else"):
        store_users.change_password(farm.s, a, "old-password-1", "new-password-2",
                                    token=mine, hours=1)
    monkeypatch.setattr(store_users, "_verified", real)
    assert hash_of() == old_hash

    # A failure inside the transaction leaves the old hash and every seat.
    def boom(*a, **k):
        raise RuntimeError("seat table down")
    monkeypatch.setattr(sessions, "rotate", boom)
    with pytest.raises(RuntimeError):
        store_users.change_password(farm.s, a, "old-password-1", "new-password-2",
                                    token=mine, hours=1)
    monkeypatch.undo()
    assert hash_of() == old_hash
    assert sessions.find(farm.s, other) is not None

    fresh = store_users.change_password(farm.s, a, "old-password-1",
                                        "new-password-2", token=mine, hours=1)
    assert fresh and fresh != mine
    seat = sessions.find(farm.s, fresh)
    assert seat["user"]["id"] == a and seat["csrf"] == csrf, "the csrf is kept"
    assert sessions.find(farm.s, mine) is None
    assert sessions.find(farm.s, other) is None
    assert sessions.find(farm.s, theirs) is not None
    row = farm.one("SELECT * FROM users WHERE id = %s", (a,))
    assert row["must_change_password"] is False
    assert abs((row["password_changed_at"] - farm.now()).total_seconds()) < 5
    assert auth.verify_password("new-password-2", row)
    assert fresh not in json.dumps(farm.sql(
        "SELECT token_hash FROM sessions WHERE user_id = %s", (a,)))

    # A browser whose seat is already gone: the password still changes and
    # every seat of the person ends.
    assert store_users.change_password(farm.s, a, "new-password-2",
                                       "new-password-3", token="gone",
                                       hours=1) is None
    assert farm.sql("SELECT 1 FROM sessions WHERE user_id = %s", (a,)) == []
    assert auth.verify_password(
        "new-password-3", farm.one("SELECT * FROM users WHERE id = %s", (a,)))

    farm.sql("UPDATE users SET password_changed_at = now() - interval '9 days'"
             " WHERE id = %s", (a,))
    store_users.set_password(farm.s, a, "new-password-4")
    assert farm.one("SELECT password_changed_at > now() - interval '1 minute'"
                    " AS fresh FROM users WHERE id = %s", (a,))["fresh"]
    farm.sql("UPDATE users SET password_changed_at = now() - interval '9 days'"
             " WHERE id = %s", (a,))
    store_users.reset_password(farm.s, a)
    assert farm.one("SELECT password_changed_at > now() - interval '1 minute'"
                    " AS fresh FROM users WHERE id = %s", (a,))["fresh"]


@needs_cluster
def test_set_state_off_taken_clears_the_station_clock(farm):
    from geelark_farm.store import person

    a = farm.user("a")
    serial = farm.hold(a, watched_at=Raw("now()"), tab_closed_at=Raw("now()"),
                       live_url="https://v/1")
    assert person.set_state(farm.s, serial, "taken") is True
    row = farm.row(serial)
    assert row["taken_at"] is not None and row["watched_at"] is not None
    assert row["tab_closed_at"] is not None and row["live_url"] == "https://v/1"
    assert person.set_state(farm.s, serial, "") is True
    row = farm.row(serial)
    assert row["state"] == "" and row["taken_at"] is None
    assert row["watched_at"] is None and row["tab_closed_at"] is None
    assert row["live_url"] == ""
    done = farm.hold(a)
    assert person.set_state(farm.s, done, "done") is True
    assert farm.row(done)["taken_at"] is None
    assert person.set_state(farm.s, "0", "") is False


@needs_cluster
def test_stamp_owner_never_takes_over_a_station_hold(farm):
    """The §4.9 statement `verbs._stamp_owner` runs, against the cluster."""
    stamp = ("UPDATE phones SET owner_id = %(uid)s,"
             " taken_at = CASE WHEN owner_id IS NOT DISTINCT FROM %(uid)s::bigint"
             "                 THEN taken_at END,"
             " updated_at = now()"
             " WHERE serial = %(s)s AND done_at IS NULL"
             "   AND (owner_id IS NULL OR owner_id = %(uid)s::bigint"
             "        OR taken_at IS NULL)")
    a, b = farm.user("a"), farm.user("b")
    station = farm.hold(a)
    farm.sql(stamp, {"uid": b, "s": station})
    assert farm.row(station)["owner_id"] == a
    assert farm.row(station)["taken_at"] is not None
    farm.sql(stamp, {"uid": a, "s": station})
    assert farm.row(station)["taken_at"] is not None, "its own clock is kept"
    legacy = farm.phone(state="taken", owner_id=a)
    farm.sql(stamp, {"uid": b, "s": legacy})
    assert farm.row(legacy)["owner_id"] == b
    free = farm.phone()
    farm.sql(stamp, {"uid": b, "s": free})
    assert farm.row(free)["owner_id"] == b and farm.row(free)["taken_at"] is None


# ----------------------------------------------------------------- wishes
@needs_cluster
def test_a_wish_carries_station_and_what_it_carries_and_every_write_moves_updated_at(
        farm):
    from geelark_farm.store import wanted

    a = farm.user("a")
    wid = wanted.ask(farm.s, requested_by=a, app="", purpose="Other",
                     station=True, carry_address=" m@p.me ",
                     carry_password=" pw ")
    row = farm.one("SELECT * FROM wanted_builds WHERE id = %s", (wid,))
    assert row["station"] is True and row["purpose"] == "other"
    assert row["carry_address"] == "m@p.me" and row["carry_password"] == " pw "
    assert row["app"] == "" and row["install_app"] is False
    dash = wanted.ask(farm.s, requested_by=a, gmail="g@x")
    row = farm.one("SELECT * FROM wanted_builds WHERE id = %s", (dash,))
    assert row["station"] is False and row["carry_address"] == ""

    def age(ident):
        farm.sql("UPDATE wanted_builds SET updated_at = now() - interval '1 hour'"
                 " WHERE id = %s", (ident,))
        return farm.one("SELECT updated_at FROM wanted_builds WHERE id = %s",
                        (ident,))["updated_at"]

    def moved(ident, before):
        return farm.one("SELECT updated_at FROM wanted_builds WHERE id = %s",
                        (ident,))["updated_at"] > before

    before = age(wid)
    taken = {r["id"] for r in wanted.take(farm.s, limit=5)}
    assert wid in taken and moved(wid, before)
    before = age(wid)
    farm.sql("UPDATE wanted_builds SET created_at = now() - interval '2 hours'"
             " WHERE id = %s", (wid,))
    assert wanted.release_stale(farm.s, 45) >= 1
    assert moved(wid, before)
    before = age(wid)
    wanted.settle(farm.s, wid, ok=False, detail="no")
    assert moved(wid, before)
    before = age(wid)
    assert wanted.dismiss(farm.s, wid, user_id=a) is True
    assert moved(wid, before)
    before = age(wid)
    assert wanted.attach(farm.s, wid, "9") is True, "not running: stop"
    assert moved(wid, before)
    assert wanted.attach(farm.s, 0, "9") is False


@needs_cluster
def test_calling_off_a_queued_wish_a_queued_job_and_a_running_build(farm):
    from geelark_farm.store import wanted

    a, b = farm.user("a"), farm.user("b")
    queued = farm.wish(a, proxy_name="1.2.3.4:80")
    assert wanted.call_off(farm.s, queued, by_id=b, by="b", admin=False) is None
    assert wanted.call_off(farm.s, 0, by_id=a, by="a", admin=True) is None
    got = wanted.call_off(farm.s, queued, by_id=a, by="sara", admin=False)
    assert got == {"stage": "queued", "serial": "", "proxy_name": "1.2.3.4:80",
                   "status": "queued", "id": queued}
    row = farm.one("SELECT * FROM wanted_builds WHERE id = %s", (queued,))
    assert row["status"] == "failed" and row["detail"] == "called off by sara"
    assert row["called_off_by"] == a and row["dismissed_at"] is not None
    assert row["ended_at"] is not None and row["called_off_at"] is not None
    assert wanted.called_off(farm.s, queued) is True
    assert wanted.call_off(farm.s, queued, by_id=a, by="a",
                           admin=False)["stage"] == "ended"

    with_job = farm.wish(a, status="running")
    job = farm.job(payload={"want": {"wanted_id": with_job}})
    got = wanted.call_off(farm.s, with_job, by_id=b, by="admin", admin=True)
    assert got["stage"] == "job_cancelled"
    jrow = farm.one("SELECT status, seen, result FROM jobs WHERE id = %s", (job,))
    assert jrow["status"] == "failed" and jrow["seen"] is True
    assert jrow["result"]["status"] == "stopped_by_hand"
    assert jrow["result"]["wanted_id"] == with_job
    assert farm.one("SELECT status FROM wanted_builds WHERE id = %s",
                    (with_job,))["status"] == "failed"

    running = farm.wish(a, status="running",
                        created_at=Raw("now() - interval '2 hours'"))
    farm.job(status="running", payload={"want": {"wanted_id": running}})
    assert wanted.attach(farm.s, running, "") is False, "still running"
    got = wanted.call_off(farm.s, running, by_id=a, by="a", admin=False)
    assert got["stage"] == "running" and got["status"] == "running"
    row = farm.one("SELECT * FROM wanted_builds WHERE id = %s", (running,))
    assert row["status"] == "running" and row["called_off_at"] is not None
    assert wanted.call_off(farm.s, running, by_id=a, by="a",
                           admin=False)["stage"] == "already"
    phone = farm.phone(owner_id=a, status="building")
    assert wanted.attach(farm.s, running, phone) is True, "called off: stop"

    # release_stale leaves a called-off wish and one with an open job alone.
    open_job = farm.wish(a, status="running",
                         created_at=Raw("now() - interval '2 hours'"))
    farm.job(status="queued", payload={"want": {"wanted_id": open_job}})
    dead = farm.wish(a, status="running",
                     created_at=Raw("now() - interval '2 hours'"))
    wanted.release_stale(farm.s, 45)
    status = {r["id"]: r["status"] for r in farm.sql(
        "SELECT id, status FROM wanted_builds WHERE id = ANY(%s)",
        ([running, open_job, dead],))}
    assert status == {running: "running", open_job: "running", dead: "queued"}

    landed_phone = farm.phone(owner_id=a, status="ready")
    landed = farm.wish(a, status="running", serial=landed_phone)
    farm.job(status="running", payload={"want": {"wanted_id": landed}})
    got = wanted.call_off(farm.s, landed, by_id=a, by="a", admin=False)
    assert got["stage"] == "landed" and got["serial"] == landed_phone
    assert farm.one("SELECT called_off_at FROM wanted_builds WHERE id = %s",
                    (landed,))["called_off_at"] is None

    building_phone = farm.phone(owner_id=a, status="building")
    building = farm.wish(a, status="running", serial=building_phone)
    farm.job(status="running", payload={"want": {"wanted_id": building}})
    assert wanted.call_off(farm.s, building, by_id=a, by="a",
                           admin=False)["stage"] == "running"

    name = f"one-off-{farm.tag}"
    alone = farm.wish(a, proxy_name=name)
    assert wanted.others_name_exit(farm.s, name, alone) is False
    second = farm.wish(a, proxy_name=name)
    assert wanted.others_name_exit(farm.s, name, alone) is True
    farm.sql("UPDATE wanted_builds SET status = 'done' WHERE id = %s", (second,))
    assert wanted.others_name_exit(farm.s, name, alone) is False
    farm.phone(proxy_name=name)
    assert wanted.others_name_exit(farm.s, name, alone) is True


# --------------------------------------------------- records and hygiene
@needs_cluster
def test_record_and_scrub_leave_no_secret_behind(farm):
    a, b = farm.user("a"), farm.user("b")
    key = _key(farm, "name")
    first = st.record(farm.s, verb="profile_name", payload={"name": "Sara"},
                      requested_by=a, status="done", result="Name saved.",
                      detail={"lane": "gpt"}, idem_key=key)
    again = st.record(farm.s, verb="profile_name", payload={"name": "Other"},
                      requested_by=a, status="done", result="Name saved.",
                      idem_key=key)
    assert first == again
    row = farm.one("SELECT * FROM actions WHERE id = %s", (first,))
    assert row["payload"] == {"name": "Sara"} and row["detail"] == {"lane": "gpt"}
    assert row["finished_at"] is not None and row["executed_at"] is not None
    bare = st.record(farm.s, verb="dismiss_build", payload={"wanted_id": 1},
                     requested_by=a, status="refused", result="no")
    assert farm.one("SELECT detail FROM actions WHERE id = %s",
                    (bare,))["detail"] is None

    secrets = {"station": True, "gmail_password": "gp", "gmail_secret": "gs",
               "app_password": "ap", "app_secret": "as", "carry_password": "cp",
               "proxy_typed": True, "proxy_name": "h.x:1080:user:pass",
               "kind": "gpt"}

    def build(by, status, **extra):
        return farm.insert("actions", verb="build_by_hand", requested_by=by,
                           status=status, payload=dict(secrets, **extra),
                           idem_key=_key(farm, "build"))

    now = build(a, "done")
    st.scrub(farm.s, now)
    left = farm.one("SELECT payload FROM actions WHERE id = %s", (now,))["payload"]
    assert left == {"station": True, "proxy_typed": True,
                    "proxy_name": "h.x:1080", "kind": "gpt"}
    live = build(a, "running")
    st.scrub(farm.s, live)
    assert farm.one("SELECT payload FROM actions WHERE id = %s",
                    (live,))["payload"]["gmail_password"] == "gp"
    later = [build(a, "refused"), build(a, "cancelled"), build(a, "failed")]
    dash = build(a, "done", station=False)
    theirs = build(b, "done")
    named = build(a, "done", proxy_typed=False, proxy_name="PC1")
    assert st.scrub_mine(farm.s, a) == 4
    for ident in later:
        payload = farm.one("SELECT payload FROM actions WHERE id = %s",
                           (ident,))["payload"]
        assert not set(payload) & {"gmail_password", "gmail_secret",
                                   "app_password", "app_secret", "carry_password"}
        assert payload["proxy_name"] == "h.x:1080"
    assert farm.one("SELECT payload FROM actions WHERE id = %s",
                    (named,))["payload"]["proxy_name"] == "PC1"
    for ident in (live, dash, theirs):
        assert "gmail_password" in farm.one(
            "SELECT payload FROM actions WHERE id = %s", (ident,))["payload"]
    assert st.scrub_mine(farm.s, a) == 0, "nothing left to scrub"


@needs_cluster
def test_notes_the_build_form_and_the_small_reads(farm):
    a, b = farm.user("a"), farm.user("b")
    gone_back = farm.phone(purpose="spotify")
    farm.insert("events", kind="phone", serial=gone_back, status="given back",
                user_id=a, at=Raw("now() - interval '2 minutes'"))
    farm.insert("events", kind="phone", serial=gone_back, status="given back",
                user_id=a, at=Raw("now() - interval '20 minutes'"))
    farm.insert("events", kind="phone", serial=gone_back, status="deleted",
                user_id=a)
    farm.insert("events", kind="phone", serial=gone_back, status="switched off",
                user_id=b)
    off = farm.phone(purpose="other")
    event = farm.insert("events", kind="phone", serial=off, status="switched off",
                        user_id=a, at=Raw("now() - interval '1 minute'"))
    returned = farm.insert(
        "actions", verb="give_back", requested_by=a, status="done",
        payload={"serial": "5073", "where": "station-live"},
        detail={"lane": "spotify"}, finished_at=Raw("now()"))
    farm.insert("actions", verb="give_back", requested_by=a, status="done",
                payload={"serial": "5074", "where": "station"},
                finished_at=Raw("now()"))
    notes = st.notes(farm.s, a)
    assert [n["key"] for n in notes][:2] == [f"a{returned}", f"e{event}"]
    assert notes[0] == {"key": f"a{returned}", "at": notes[0]["at"],
                        "serial": "5073", "kind": "returned", "lane": "spotify"}
    assert notes[1]["kind"] == "switched off" and notes[1]["lane"] == "other"
    assert notes[2]["kind"] == "given back" and notes[2]["lane"] == "spotify"
    assert len(notes) == 3

    before = st.build_form(farm.s)
    farm.resource("gmail", address=f"f{farm.tag}@x.com")
    farm.resource("gmail", address=f"r{farm.tag}@x.com", refund_state="asked")
    farm.resource("proxy", host="p1", port=1, username=farm.tag, purpose="gpt")
    farm.resource("proxy", host="p2", port=2, username=farm.tag, purpose="")
    farm.resource("proxy", host="p3", port=3, username=farm.tag,
                  purpose="spotify", status="free")
    farm.resource("proxy", host="p4", port=4, username=farm.tag, status="spent")
    after = st.build_form(farm.s)
    assert after["gmails_left"] - before["gmails_left"] == 1
    grew = {k: after["free_ips"][k] - before["free_ips"][k]
            for k in ("gpt", "spotify", "other")}
    assert grew == {"gpt": 2, "spotify": 2, "other": 3}
    had = farm.one("SELECT value FROM service_state WHERE key = 'pass'")
    try:
        farm.sql("INSERT INTO service_state (key, value) VALUES ('pass', %s)"
                 " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                 (json.dumps({"stopped": True}),))
        assert st.build_form(farm.s)["stopped"] is True
    finally:
        if had is None:
            farm.sql("DELETE FROM service_state WHERE key = 'pass'")
        else:
            farm.sql("UPDATE service_state SET value = %s WHERE key = 'pass'",
                     (json.dumps(had["value"]),))

    one, two = farm.phone(proxy_name="PS1"), farm.phone(proxy_name="PS1")
    assert st.exit_shared(farm.s, "PS1", one) is True
    farm.sql("UPDATE phones SET done_at = now() WHERE serial = %s", (two,))
    assert st.exit_shared(farm.s, "PS1", one) is False
