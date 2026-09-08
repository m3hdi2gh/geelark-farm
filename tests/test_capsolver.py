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
    tiles, size = capsolver.solve_grid("K", "BASE64", "traffic lights",
                                       session=post)
    assert tiles == [0, 3, 7]
    assert size == 0, "an answer that did not say leaves the caller to guess"
    _url, body, _t = post.calls[0]
    assert body["clientKey"] == "K"
    assert body["task"]["type"] == "ReCaptchaV2Classification"
    assert body["task"]["question"] == "/m/015qff"
    assert body["task"]["image"] == "BASE64"


def test_a_single_object_answer_is_one_tile_or_none():
    yes = FakePost({"errorId": 0, "solution": {"type": "single",
                                               "hasObject": True}})
    assert capsolver.solve_grid("K", "b", "cars", session=yes) == ([0], 1)
    no = FakePost({"errorId": 0, "solution": {"type": "single",
                                              "hasObject": False}})
    assert capsolver.solve_grid("K", "b", "cars", session=no) == ([], 1)


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


def test_the_grid_width_comes_back_with_the_tiles():
    """The indices only mean anything against the grid the solver read, and
    it says which that was. Guessed from the wording instead, a 4x4 read as
    a 3x3 puts every tap in the wrong place."""
    post = FakePost({"errorId": 0, "solution": {"type": "multi",
                                                "objects": [0, 15],
                                                "size": 4}})
    assert capsolver.solve_grid("K", "b", "stairs", session=post) == ([0, 15], 4)


def test_a_width_the_answer_invents_is_not_believed():
    """Only the two grids Google draws are accepted; anything else falls
    back to the wording rather than taking a tap into open space."""
    post = FakePost({"errorId": 0, "solution": {"type": "multi",
                                                "objects": [1], "size": 7}})
    assert capsolver.solve_grid("K", "b", "stairs", session=post) == ([1], 0)


def test_a_hang_is_given_up_on_at_thirty_seconds_and_tried_five_times():
    """A call that answers does so in seconds; one that does not never
    does. Ninety seconds three times was four and a half minutes a grid,
    and one phone spent three Gmails that way in an hour (2026-09-08)."""
    post = FakePost(raises=RuntimeError("read timed out"))
    with pytest.raises(capsolver.CapError, match="did not answer"):
        capsolver.solve_grid("K", "aGk=", "cars", session=post)
    assert len(post.calls) == 5
    assert {timeout for _, _, timeout in post.calls} == {30}
