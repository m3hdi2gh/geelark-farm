"""The Gmails page's writes against a real cluster (GEELARK_TEST_DSN): each
press moves only a Gmail that still stands where the page drew it, Undo
puts back only what nothing has moved since, and a build takes only its
lane's Gmails - in the SQL the farm will run, on the schema it will run
it on. Every row is tagged by an address domain of its own and removed
after the test."""

from __future__ import annotations

import datetime
import uuid

import pytest

from tests.test_station_store import DSN, needs_cluster

pytestmark = needs_cluster


@pytest.fixture
def desk(make_settings):
    from geelark_farm.store import db as store_db

    parts = dict(p.split("=", 1) for p in DSN.split())
    s = make_settings(
        store_enabled=True, store_host=parts["host"],
        store_port=int(parts.get("port", 5432)), store_db=parts["dbname"],
        store_user=parts["user"], store_password=parts["password"])
    store_db.ensure_schema(s)
    d = Desk(s, uuid.uuid4().hex[:8])
    try:
        yield d
    finally:
        d.cleanup()


class Desk:
    def __init__(self, settings, tag):
        self.s = settings
        self.domain = f"gp{tag}.test"

    def sql(self, text, params=()):
        from geelark_farm.store.db import connect

        with connect(self.s) as conn:
            cur = conn.execute(text, params)
            rows = ([dict(zip([d.name for d in cur.description], r, strict=True))
                     for r in cur.fetchall()] if cur.description else [])
            conn.commit()
        return rows

    def gmail(self, name, **cols):
        base = {"kind": "gmail", "address": f"{name}@{self.domain}", "password": "Pw-1",
                "totp_secret": "JBSWY3DPEHPK3PXP", "seller": "TEST 7OCT", "status": ""}
        base.update(cols)
        names = list(base)
        row = self.sql(f"INSERT INTO resources ({', '.join(names)}) VALUES"
                       f" ({', '.join(['%s'] * len(names))}) RETURNING id",
                       [base[n] for n in names])
        return int(row[0]["id"])

    def row(self, rid):
        got = self.sql("SELECT * FROM resources WHERE id = %s", (rid,))
        return got[0] if got else None

    def signin(self, name, ok, reason, at=None):
        self.sql("INSERT INTO signins (gmail, ok, reason, at, stage) VALUES"
                 " (%s, %s, %s, coalesce(%s, now()), 'p')",
                 (f"{name}@{self.domain}", ok, reason, at))

    def cleanup(self):
        like = f"%@{self.domain}"
        self.sql("DELETE FROM resources WHERE address LIKE %s", (like,))
        self.sql("DELETE FROM resources_archive WHERE address LIKE %s", (like,))
        self.sql("DELETE FROM signins WHERE gmail LIKE %s", (like,))


def test_the_switch_moves_only_what_stands_where_the_page_saw_it(desk):
    from geelark_farm.store import gmail_desk

    free = desk.gmail("free")
    waits = desk.gmail("waits", status="captcha_shown", last_reason="captcha_shown",
                       tries=1, retry_after=datetime.datetime.now(datetime.timezone.utc)
                       + datetime.timedelta(hours=2))
    phone = desk.gmail("phone", status="in_use", serial="900001")
    spent = desk.gmail("spent", status="used")
    stopped = desk.gmail("stopped", status="phone_verification_required",
                         refund_state="to_claim", tries=3)
    got = gmail_desk.aside(desk.s, [free, waits, phone, spent, stopped], by="t")
    assert sorted(c["id"] for c in got["changed"]) == [free, waits]
    assert got["left"] == {phone: "phone", spent: "spent", stopped: "same"}
    w = desk.row(waits)
    assert w["status"] == "set_aside" and w["retry_after"] is not None, \
        "set aside while waiting keeps its hour and its reason"
    from geelark_farm.store import ladder

    assert w["address"] not in ladder.revive_due(desk.s), "a person's shelf is not revived"

    got = gmail_desk.queue(desk.s, [free, waits, stopped, phone], by="t")
    assert sorted(c["id"] for c in got["changed"]) == [free, waits, stopped]
    assert desk.row(free)["status"] == ""
    assert desk.row(waits)["status"] == "captcha_shown", "waiting again, for the hour it had"
    s = desk.row(stopped)
    assert (s["status"], s["refund_state"], s["retry_after"], s["tries"]) == ("", "", None, 3), \
        "stopped goes free and off the seller's list, its tries as they were"
    assert desk.row(phone)["status"] == "in_use"


def test_mark_fixed_starts_its_tries_again_and_undo_puts_it_back(desk):
    from geelark_farm.store import gmail_desk

    broke = desk.gmail("broke", status="wrong_2fa_code", tries=3, last_reason="wrong_2fa_code",
                       last_host="h1", note="The farm's word.")
    free_refused = desk.gmail("refused")
    desk.signin("refused", False, "captcha_shown")
    fresh = desk.gmail("fresh")
    got = gmail_desk.mend(desk.s, [broke, free_refused, fresh], by="mehdi")
    assert sorted(c["id"] for c in got["changed"]) == [broke, free_refused]
    assert got["left"] == {fresh: "same"}, "never refused: nothing to fix"
    b = desk.row(broke)
    assert (b["status"], b["tries"], b["last_reason"], b["last_host"]) == ("", 0, "", "")
    assert b["fixed_by"] == "mehdi" and b["fixed_at"] is not None
    assert b["note"].startswith("Marked as fixed on ") and "by mehdi" in b["note"]
    # Refused only before the fix: not mendable again until refused again.
    assert gmail_desk.mend(desk.s, [free_refused], by="mehdi")["changed"] == []

    back = gmail_desk.revert(desk.s, got["changed"], by="mehdi")
    assert back == {"back": sorted([broke, free_refused]), "moved": []}
    b = desk.row(broke)
    assert (b["status"], b["tries"], b["last_reason"], b["note"], b["fixed_at"]) == (
        "wrong_2fa_code", 3, "wrong_2fa_code", "The farm's word.", None)


def test_an_unreadable_gmail_is_put_right_by_an_edit_only(desk):
    from geelark_farm.store import gmail_desk

    rid = desk.gmail("torn", error="bad key", status="")
    assert gmail_desk.queue(desk.s, [rid], by="t")["left"] == {rid: "unreadable"}
    assert gmail_desk.mend(desk.s, [rid], by="t")["left"] == {rid: "unreadable"}
    got = gmail_desk.save(desk.s, rid, password=None, key=None, recovery=None,
                          note="checked", fixed=False, by="t")
    assert got["said"] == "note edited" and desk.row(rid)["error"] is None


def test_a_save_leaves_what_the_person_did_not_touch(desk):
    from geelark_farm.store import gmail_desk

    rid = desk.gmail("keep2", recovery_email="Back@Example.org")
    desk.sql("UPDATE resources SET password = 'Changed-Elsewhere' WHERE id = %s", (rid,))
    got = gmail_desk.save(desk.s, rid, password=None, key=None, recovery=None,
                          note="only the note", fixed=False, by="t")
    r = desk.row(rid)
    assert got["said"] == "note edited", "an untouched field is not reported changed"
    assert (r["password"], r["recovery_email"]) == ("Changed-Elsewhere", "Back@Example.org")


def test_a_free_gmail_set_aside_comes_back_free(desk):
    """A free Gmail carries no hour into the aside: an old one would bring
    it back waiting. Undo puts the hour back with the rest."""
    from geelark_farm.store import gmail_desk

    soon = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)
    rid = desk.gmail("hour", last_reason="captcha_shown", retry_after=soon)
    got = gmail_desk.aside(desk.s, [rid], by="t")
    assert (desk.row(rid)["status"], desk.row(rid)["retry_after"]) == ("set_aside", None)
    gmail_desk.queue(desk.s, [rid], by="t")
    assert desk.row(rid)["status"] == "", "free again, not waiting"
    gmail_desk.aside(desk.s, [rid], by="t")
    rid2 = desk.gmail("hour2", last_reason="captcha_shown", retry_after=soon)
    got = gmail_desk.aside(desk.s, [rid2], by="t")
    assert gmail_desk.revert(desk.s, got["changed"], by="t")["back"] == [rid2]
    assert desk.row(rid2)["retry_after"] == soon


def test_a_kept_key_nobody_touched_never_stops_a_note(desk):
    from geelark_farm.store import gmail_desk

    rid = desk.gmail("short", totp_secret="JBSWY3DPEHPK")
    got = gmail_desk.save(desk.s, rid, password=None, key=None, recovery=None,
                          note="checked by hand", fixed=False, by="t")
    assert got["said"] == "note edited" and desk.row(rid)["totp_secret"] == "JBSWY3DPEHPK"
    with pytest.raises(gmail_desk.Refused, match="16 or more"):
        gmail_desk.save(desk.s, rid, password=None, key="JBSWY3DPEHPK", recovery=None,
                        note=None, fixed=False, by="t")


def test_undo_of_a_change_and_the_remove_after_it_brings_both_back(desk):
    from geelark_farm.store import gmail_desk

    rid = desk.gmail("twice")
    first = gmail_desk.aside(desk.s, [rid], by="t")["changed"]
    second = gmail_desk.remove(desk.s, [rid], by="t")["changed"]
    back = gmail_desk.revert(desk.s, first + second, by="t")
    assert back == {"back": [rid], "moved": []}, back
    assert desk.row(rid)["status"] == ""


def test_restore_brings_a_removed_gmail_back_under_its_own_id(desk):
    from geelark_farm.store import gmail_desk

    rid = desk.gmail("rest", status="captcha_shown", tries=2)
    gmail_desk.remove(desk.s, [rid], by="t")
    got = gmail_desk.restore(desk.s, rid, by="t")
    assert got["id"] == rid and got["ver"], got
    assert (desk.row(rid)["status"], desk.row(rid)["tries"]) == ("captcha_shown", 2)
    assert gmail_desk.restore(desk.s, rid, by="t") is None, "not in the archive any more"


def test_the_scrub_empties_a_settled_save_past_its_undo(desk):
    import json as _json

    from geelark_farm.store import gmail_desk

    payload = {"id": 1, "password": "NEW-PW", "key": "", "recovery": None, "by": "t"}
    detail = {"said": "password changed", "changes": [
        {"id": 1, "before": {"password": "OLD", "note": "n"},
         "after": {"password": "NEW-PW", "note": "n"}}]}
    made = desk.sql("INSERT INTO actions (verb, payload, status, detail, requested_at)"
                    " VALUES ('gmail_save', %s::jsonb, 'done', %s::jsonb,"
                    "         now() - interval '1 hour'),"
                    "        ('gmail_save', %s::jsonb, 'done', %s::jsonb, now())"
                    " RETURNING id", (_json.dumps(payload), _json.dumps(detail),
                                      _json.dumps(payload), _json.dumps(detail)))
    old, fresh = (r["id"] for r in made)
    try:
        assert gmail_desk.scrub_old(desk.s, every=0) >= 1
        rows = {r["id"]: r for r in desk.sql(
            "SELECT id, payload, detail FROM actions WHERE id = ANY(%s)", ([old, fresh],))}
        assert "password" not in rows[old]["payload"] and rows[old]["detail"]["scrubbed"] == "1"
        assert rows[old]["detail"]["changes"][0]["before"] == {"note": "n"}
        assert rows[fresh]["payload"]["password"] == "NEW-PW", "its Undo still needs it"
    finally:
        desk.sql("DELETE FROM actions WHERE id = ANY(%s)", ([old, fresh],))


def test_undo_leaves_a_gmail_something_else_moved_since(desk):
    from geelark_farm.store import gmail_desk

    a, b = desk.gmail("a"), desk.gmail("b")
    got = gmail_desk.aside(desk.s, [a, b], by="t")
    desk.sql("UPDATE resources SET status = '', updated_at = now() WHERE id = %s", (b,))
    back = gmail_desk.revert(desk.s, got["changed"], by="t")
    assert back == {"back": [a], "moved": [b]}
    assert desk.row(a)["status"] == "" and desk.row(b)["status"] == ""


def test_a_save_and_its_fix_are_one_change(desk):
    from geelark_farm.store import gmail_desk

    rid = desk.gmail("save", status="password_changed", refund_state="to_claim", tries=3,
                     note="Old note.")
    got = gmail_desk.save(desk.s, rid, password="New-Pw-2", key="jbsw y3dp ehpk 3pxq",
                          recovery="back@example.org", note=None, fixed=True, by="mehdi")
    assert got["mended"] and got["said"] == (
        "password changed, key changed and recovery address added")
    assert len(got["changed"]) == 1
    r = desk.row(rid)
    assert (r["password"], r["totp_secret"], r["recovery_email"]) == (
        "New-Pw-2", "JBSWY3DPEHPK3PXQ", "back@example.org")
    assert (r["status"], r["refund_state"], r["tries"]) == ("", "", 0)
    assert r["note"].startswith("Marked as fixed"), "the farm's note gives way to the fix's"
    back = gmail_desk.revert(desk.s, got["changed"], by="mehdi")
    assert back["back"] == [rid]
    r = desk.row(rid)
    assert (r["password"], r["totp_secret"], r["recovery_email"], r["status"],
            r["refund_state"], r["note"]) == ("Pw-1", "JBSWY3DPEHPK3PXP", None,
                                              "password_changed", "to_claim", "Old note."), \
        "exactly as it was - a recovery address never set stays NULL"
    with pytest.raises(gmail_desk.Refused, match="on a phone"):
        gmail_desk.save(desk.s, desk.gmail("busy", status="ready"), password="p", key="",
                        recovery="", note=None, fixed=False, by="t")
    edited = gmail_desk.save(desk.s, rid, password="Pw-1", key="JBSWY3DPEHPK3PXP",
                             recovery="", note="", fixed=False, by="t")
    assert edited["said"] == "note edited" and desk.row(rid)["note"] == ""


def test_remove_archives_the_whole_row_and_undo_brings_it_back_as_it_was(desk):
    from geelark_farm.store import gmail_desk

    rid = desk.gmail("gone", status="captcha_shown", tries=2, last_reason="captcha_shown",
                     purpose="spotify", backup_codes=None)
    before = desk.row(rid)
    got = gmail_desk.remove(desk.s, [rid, desk.gmail("busy", status="in_use")], by="mehdi")
    assert [c["id"] for c in got["changed"]] == [rid]
    assert desk.row(rid) is None
    arch = desk.sql("SELECT archived_by, status FROM resources_archive WHERE id = %s", (rid,))
    assert arch == [{"archived_by": "mehdi", "status": "captcha_shown"}]
    back = gmail_desk.revert(desk.s, got["changed"], by="mehdi")
    assert back["back"] == [rid]
    again = desk.row(rid)
    for k in ("address", "password", "totp_secret", "status", "tries", "last_reason",
              "purpose", "seller", "created_at"):
        assert again[k] == before[k], k
    assert not desk.sql("SELECT 1 FROM resources_archive WHERE id = %s", (rid,))


def test_keep_for_and_the_lanes_a_build_claims_from(desk):
    from geelark_farm.store import gmail_desk
    from geelark_farm.store.pgpool import PgGmailPool, ResourceTable

    # Only these rows are free in this test's world: park every other free
    # Gmail of the test cluster for the length of the test is not ours to
    # do, so the claims are pinned to this test's rows through a seller.
    g, s_, any_ = desk.gmail("g"), desk.gmail("s"), desk.gmail("any")
    got = gmail_desk.keep_for(desk.s, [g, desk.gmail("busy", status="ready")], "gpt", by="t")
    assert [c["id"] for c in got["changed"]] == [g]
    gmail_desk.keep_for(desk.s, [s_], "spotify", by="t")
    with pytest.raises(gmail_desk.Refused):
        gmail_desk.keep_for(desk.s, [g], "tiktok", by="t")
    table = ResourceTable(desk.s)
    pool = PgGmailPool(table)
    seen = set()
    for _ in range(3):
        row = pool._claim("", "", (" AND address LIKE %s AND lower(coalesce(purpose, ''))"
                                   " IN (%s, '')", (f"%@{desk.domain}", "spotify")))
        if row is None:
            break
        seen.add(row.store_id)
    assert seen == {s_, any_}, "a Spotify build never takes a Gmail kept for GPT"


def test_add_files_new_ones_and_brings_refused_ones_back_fixed(desk):
    from geelark_farm.store import gmail_desk

    refused = desk.gmail("old", status="captcha_shown", tries=3, last_reason="captcha_shown")
    keep = desk.gmail("keep")
    desk.sql("INSERT INTO resources_archive (id, kind, address, status, payload)"
             " VALUES (%s, 'gmail', %s, 'used', '{}'::jsonb)",
             (900000000 + keep, f"spent@{desk.domain}"))
    got = gmail_desk.add(desk.s, [
        {"address": f"New1@{desk.domain}", "password": "p1", "key": "JBSWY3DPEHPK3PXP",
         "recovery": ""},
        {"address": f"new2@{desk.domain}", "password": "p2", "key": "",
         "recovery": f"r@{desk.domain}"},
        {"address": f"keep@{desk.domain}", "password": "p3", "key": "", "recovery": ""},
        {"address": f"spent@{desk.domain}", "password": "p4", "key": "", "recovery": ""},
        {"address": f"bad@{desk.domain}", "password": "p5", "key": "abc", "recovery": ""}],
        seller="TEST 7OCT", lane="spotify", by="mehdi", by_id=None,
        back=[{"address": f"old@{desk.domain}", "password": "fixed", "key": "", "recovery": ""}],
        carry=[keep])
    assert [a["address"] for a in got["added"]] == [f"new1@{desk.domain}",
                                                    f"new2@{desk.domain}"]
    assert {r["address"]: r["why"] for r in got["refused"]} == {
        f"keep@{desk.domain}": "is in the pool already",
        f"spent@{desk.domain}": "was spent on a phone before; it is in the archive",
        f"bad@{desk.domain}": "A key is 16 or more letters A to Z and digits 2 to 7;"
                              " spaces between its groups are fine."}
    new = desk.row(got["added"][0]["id"])
    assert (new["status"], new["purpose"], new["seller"], new["source"]) == (
        "", "spotify", "TEST 7OCT", "web")
    assert [r["id"] for r in got["returned"]] == [refused]
    o = desk.row(refused)
    assert (o["status"], o["tries"], o["password"]) == ("", 0, "fixed")
    assert [c["id"] for c in got["carried"]] == [keep] and desk.row(keep)["purpose"] == "spotify"
    desk.sql("DELETE FROM resources_archive WHERE id = %s", (900000000 + keep,))


def test_the_reader_reads_the_live_shape(desk):
    from geelark_farm.web import gmails_read

    rid = desk.gmail("read", status="captcha_shown", last_reason="captcha_shown",
                     retry_after=datetime.datetime.now(datetime.timezone.utc))
    desk.signin("read", False, "captcha_shown")
    got = gmails_read.state(desk.s, fresh=True)
    row = next(e for e in got["rows"] if e["id"] == rid)
    assert row["st"] == "captcha_shown" and row["next"] and row["t"][0][2] == "captcha_shown"
    assert row["key"] is True and "password" not in row
    found = gmails_read.archive(desk.s, q=desk.domain)
    assert found["matched"] == 0 and found["rows"] == []


def test_a_sign_ins_stage_is_written(desk):
    from geelark_farm.store import signins

    assert signins.record(desk.s, serial="1", gmail=f"st@{desk.domain}", ok=True,
                          reason="", stage="c")
    assert signins.record(desk.s, serial="1", gmail=f"st2@{desk.domain}", stage="zz")
    got = desk.sql("SELECT gmail, stage FROM signins WHERE gmail LIKE %s ORDER BY gmail",
                   (f"%@{desk.domain}",))
    assert got == [{"gmail": f"st2@{desk.domain}", "stage": ""},
                   {"gmail": f"st@{desk.domain}", "stage": "c"}]
