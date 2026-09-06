

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
