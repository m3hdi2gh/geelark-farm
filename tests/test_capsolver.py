"""The CapSolver door - classification only, answered from a fake transport."""
from __future__ import annotations

import pytest

from geelark_farm import capsolver


class FakePost:
    """One recorded POST and one scripted JSON reply (or an exception)."""

    def __init__(self, reply=None, raises=None):
        self.reply, self.raises = reply, raises
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        if self.raises is not None:
            raise self.raises

        class R:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return self.reply
        return R()


def test_a_word_off_the_screen_becomes_the_capsolver_id():
    assert capsolver.question_id("Select all images with traffic lights") \
        == "/m/015qff"
    assert capsolver.question_id("...with a bus") == "/m/01bjv"
    # The longest known phrase wins, so a stray word does not steal it.
    assert capsolver.question_id("with a school bus, not a car") \
        == "/m/02yvhj"
    assert capsolver.question_id("with airplanes") == ""


def test_solve_grid_sends_the_id_and_returns_the_tiles():
    post = FakePost({"errorId": 0, "solution": {"type": "multi",
                                                "objects": [0, 3, 7]}})
    tiles = capsolver.solve_grid("K", "BASE64", "traffic lights", session=post)
    assert tiles == [0, 3, 7]
    _url, body, _t = post.calls[0]
    assert body["clientKey"] == "K"
    assert body["task"]["type"] == "ReCaptchaV2Classification"
    assert body["task"]["question"] == "/m/015qff"
    assert body["task"]["image"] == "BASE64"


def test_a_single_object_answer_is_one_tile_or_none():
    yes = FakePost({"errorId": 0, "solution": {"type": "single",
                                               "hasObject": True}})
    assert capsolver.solve_grid("K", "b", "cars", session=yes) == [0]
    no = FakePost({"errorId": 0, "solution": {"type": "single",
                                              "hasObject": False}})
    assert capsolver.solve_grid("K", "b", "cars", session=no) == []


def test_an_unknown_category_never_spends_the_key():
    post = FakePost({"errorId": 0, "solution": {}})
    with pytest.raises(capsolver.CapError, match="no CapSolver category"):
        capsolver.solve_grid("K", "b", "select all airplanes", session=post)
    assert post.calls == [], "nothing was sent"


def test_a_refused_key_is_a_caperror_with_the_reason():
    post = FakePost({"errorId": 1, "errorCode": "ERROR_KEY_DENIED",
                     "errorDescription": "key is not authorised"})
    with pytest.raises(capsolver.CapError, match="not authorised"):
        capsolver.solve_grid("K", "b", "cars", session=post)


def test_a_network_that_does_not_answer_is_a_caperror():
    post = FakePost(raises=RuntimeError("no route to host"))
    with pytest.raises(capsolver.CapError, match="did not answer"):
        capsolver.balance("K", session=post)
