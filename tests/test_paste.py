

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
