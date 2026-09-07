

def test_a_key_pasted_as_googles_groups_is_put_back_together():
    """Google prints the key as `fioi p2yx bzu7 gax3 ...`; a line split on
    plain spaces made eight tokens of four, and the reader threw them all
    away - eleven Gmails went in with no secret (2026-09-06)."""
    from geelark_farm.web import paste

    line = ("thay98355@gmail.com chaobuoisang "
            "xpui mde3 bpjl iulh qiuq 6uri bxe3 72wj")
    (row,) = paste.accounts(line)
    assert row["address"] == "thay98355@gmail.com"
    assert row["password"] == "chaobuoisang"
    assert row["secret"] == "XPUIMDE3BPJLIULHQIUQ6URIBXE372WJ"
    assert row["unread"] == []


def test_a_piece_the_reader_cannot_place_is_said_not_dropped():
    from geelark_farm.web import paste

    (row,) = paste.accounts("a@x.com pw extra-thing")
    assert row["password"] == "pw"
    assert row["unread"] == ["extra-thing"]


def test_a_password_of_letters_is_not_mistaken_for_key_groups():
    from geelark_farm.web import paste

    (row,) = paste.accounts("a@x.com abcd efgh")
    # Two groups of four is not a key (Google prints eight); it reads as a
    # password and one piece not understood.
    assert row["secret"] == ""
    assert row["password"] == "abcd" and row["unread"] == ["efgh"]


def test_an_exit_given_as_separate_columns_is_assembled():
    """The Proxy tab has always taken both shapes - its heading says
    "Proxy String (or Host/Port/User/Pass)" - and the sheet pool and the
    importer both join the four cells when the joined one is blank. The
    console's paste box could read only the joined string, so a vendor list
    in four columns pasted as nothing at all: every line refused, with no
    hint that the shape was the problem (2026-09-06)."""
    from geelark_farm.web import paste

    (row,) = paste.proxies("SX9\t1.2.3.4\t10627\tuser\tsecret")
    assert row["raw"] == "1.2.3.4:10627:user:secret"
    assert row["name"] == "SX9"

    (bare,) = paste.proxies("1.2.3.4\t10627\tuser\tsecret")
    assert bare["raw"] == "1.2.3.4:10627:user:secret" and bare["name"] == ""

    (open_exit,) = paste.proxies("SX9  1.2.3.4  10627")
    assert open_exit["raw"] == "1.2.3.4:10627", "no credentials is a shape too"


def test_a_joined_string_still_wins_over_the_columns():
    from geelark_farm.web import paste

    (row,) = paste.proxies("1.2.3.4:10627:user:secret  SX9")
    assert row["raw"] == "1.2.3.4:10627:user:secret"
    assert row["name"] == "SX9"


def test_a_line_with_no_port_in_it_is_not_invented_into_an_exit():
    """Nothing is assembled out of a line that has no port, because the
    port is the only token whose shape says where the host ends."""
    from geelark_farm.web import paste

    (row,) = paste.proxies("SX9 some words here")
    assert row["raw"] == ""


def test_a_two_column_line_keeps_its_password_instead_of_calling_it_a_key():
    """A seller's list reads address, password, key. The reader took the
    FIRST base32-shaped token, and a password of sixteen plain letters is
    base32-shaped - so it claimed the password as the key and the row read
    "no password" on a line that visibly had one (2026-09-07)."""
    from geelark_farm.web import paste

    (row,) = paste.accounts("buyer2@gmail.com\tSIXTEENLETTERPWD")
    assert row["password"] == "SIXTEENLETTERPWD"
    assert row["secret"] == ""
    assert "could not tell the password" in row["error"]


def test_the_key_is_taken_from_the_end_of_the_line_not_the_start():
    from geelark_farm.web import paste

    (row,) = paste.accounts("a@x.com\tPLAINLETTERSPASS\tABCDEFGHIJKLMNOP")
    assert row["password"] == "PLAINLETTERSPASS"
    assert row["secret"] == "ABCDEFGHIJKLMNOP"
    assert not row.get("error")


def test_a_line_with_both_a_key_and_a_recovery_address_is_refused():
    """A row keeps one, and it was the key that was dropped - under a
    green "ok" (2026-09-07)."""
    from geelark_farm.web import paste

    (row,) = paste.accounts("a@x.com\tpw1\tABCDEFGHIJKLMNOP\trec@x.com")
    assert row["secret"] and row["recovery"]
    assert "both an authenticator key and a recovery address" in row["error"]
