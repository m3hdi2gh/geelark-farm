"""The 2Captcha door and the solvers that choose between the doors."""
from __future__ import annotations

import pytest

from geelark_farm import capsolver, solvers, twocaptcha


class ScriptedPost:
    """Records every POST and answers each with the next scripted reply;
    a reply that is an exception is raised instead."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], json, timeout))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply

        class R:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return reply
        return R()


def _no_sleep(_seconds):
    return None


# ------------------------------------------------------------ the 2Captcha door
def test_a_grid_goes_out_as_a_grid_task_and_the_clicks_come_back_zero_based():
    post = ScriptedPost({"errorId": 0, "taskId": 77},
                        {"errorId": 0, "status": "processing"},
                        {"errorId": 0, "status": "ready",
                         "solution": {"click": [1, 5, 9]}, "cost": "0.001"})
    tiles, size = twocaptcha.solve_grid(
        "K", "BASE64", "Select all images with crosswalks. Click verify "
        "once there are none left.", size=3, session=post, sleep=_no_sleep)
    assert (tiles, size) == ([0, 4, 8], 3), "1 is the top-left tile at 2Captcha"
    create, body, _t = post.calls[0]
    assert create == "createTask" and body["clientKey"] == "K"
    task = body["task"]
    assert task["type"] == "GridTask" and task["body"] == "BASE64"
    assert task["rows"] == task["columns"] == 3
    assert task["comment"] == "Select all images with crosswalks", (
        "the worker reads the ask, not Google's whole paragraph")
    assert task["canNoAnswer"] == 1
    assert [c[0] for c in post.calls[1:]] == ["getTaskResult"] * 2
    assert post.calls[1][1] == {"clientKey": "K", "taskId": 77}


def test_none_of_them_is_an_empty_answer_not_an_error():
    post = ScriptedPost({"errorId": 0, "taskId": 1},
                        {"errorId": 0, "status": "ready", "solution": {}})
    assert twocaptcha.solve_grid("K", "b", "buses", size=4, session=post,
                                 sleep=_no_sleep) == ([], 4)


def test_a_click_off_the_grid_is_dropped_rather_than_tapped_somewhere():
    post = ScriptedPost({"errorId": 0, "taskId": 1},
                        {"errorId": 0, "status": "ready",
                         "solution": {"click": [0, 3, 10, 16]}})
    tiles, _ = twocaptcha.solve_grid("K", "b", "cars", size=3, session=post,
                                     sleep=_no_sleep)
    assert tiles == [2]


def test_a_refusal_is_raised_with_the_workers_words():
    post = ScriptedPost({"errorId": 12, "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
                         "errorDescription": "Workers could not solve the Captcha"})
    with pytest.raises(twocaptcha.TwoCapError, match="Workers could not"):
        twocaptcha.solve_grid("K", "b", "cars", size=3, session=post,
                              sleep=_no_sleep)


def test_a_grid_nobody_answers_in_time_is_given_up_not_polled_forever(monkeypatch):
    clock = iter([0.0, 0.0, 200.0, 200.0])
    monkeypatch.setattr(twocaptcha.time, "monotonic", lambda: next(clock))
    post = ScriptedPost({"errorId": 0, "taskId": 5},
                        {"errorId": 0, "status": "processing"},
                        {"errorId": 0, "status": "processing"})
    with pytest.raises(twocaptcha.TwoCapError, match="not answered within"):
        twocaptcha.solve_grid("K", "b", "cars", size=3, session=post,
                              sleep=_no_sleep, wait_seconds=120)


def test_cancel_is_felt_between_polls():
    class Stop(Exception):
        pass

    def watch():
        raise Stop

    post = ScriptedPost({"errorId": 0, "taskId": 5})
    with pytest.raises(Stop):
        twocaptcha.solve_grid("K", "b", "cars", size=3, session=post,
                              sleep=_no_sleep, watch=watch)
    assert len(post.calls) == 1, "the task was created, then Cancel came first"


def test_a_transport_hiccup_is_retried_and_a_grid_of_the_wrong_width_refused():
    post = ScriptedPost(ConnectionError("reset"), {"errorId": 0, "taskId": 2},
                        {"errorId": 0, "status": "ready",
                         "solution": {"click": [2]}})
    assert twocaptcha.solve_grid("K", "b", "cars", size=3, session=post,
                                 sleep=_no_sleep) == ([1], 3)
    with pytest.raises(twocaptcha.TwoCapError, match="3 or 4 wide"):
        twocaptcha.solve_grid("K", "b", "cars", size=5, session=post)


def test_the_comment_is_the_first_sentence_folded():
    assert twocaptcha.comment_of("Select all squares with\n  traffic lights") \
        == "Select all squares with traffic lights"
    assert twocaptcha.comment_of("") == "Select the tiles that match"


def test_balance_reads_the_dollars():
    post = ScriptedPost({"errorId": 0, "balance": 9.5})
    assert twocaptcha.balance("K", session=post) == 9.5
    assert post.calls[0][0] == "getBalance"


# ---------------------------------------------------------- choosing the door
class _Settings:
    def __init__(self, cap="", two="", first=""):
        self.capsolver_key, self.twocaptcha_key = cap, two
        self.captcha_solver = first


def test_the_doors_with_a_key_are_tried_2captcha_first_unless_told_otherwise():
    assert solvers.of(_Settings()).order == ()
    assert not solvers.of(_Settings()), "no key reads as no solver"
    assert solvers.of(_Settings(cap="C")).order == ("capsolver",)
    both = solvers.of(_Settings(cap="C", two="T"))
    assert both.order == ("2captcha", "capsolver") and both
    assert solvers.of(_Settings(cap="C", two="T", first="capsolver")).order \
        == ("capsolver", "2captcha")
    assert solvers.of(_Settings(cap="C", first="2captcha")).order \
        == ("capsolver",), "a preference for a door with no key is ignored"


def test_a_bare_key_still_goes_to_capsolver_as_every_old_caller_expects(monkeypatch):
    seen = {}

    def fake(key, image, question, **kw):
        seen.update(key=key, kw=sorted(kw))
        return [1], 3

    monkeypatch.setattr(capsolver, "solve_grid", fake)
    assert solvers.solve_grid("CAP-k", "b", "cars", size=3) == ([1], 3)
    assert seen["key"] == "CAP-k"
    assert "size" not in seen["kw"], "CapSolver reads the width itself"


def test_the_next_door_is_tried_when_the_first_does_not_answer(monkeypatch):
    calls = []

    def two(key, image, question, *, size, session=None, watch=None):
        calls.append(("2captcha", key, size))
        raise twocaptcha.TwoCapError("ERROR_NO_SLOT_AVAILABLE")

    def cap(key, image, question, **kw):
        calls.append(("capsolver", key))
        return [0, 2], 3

    monkeypatch.setattr(twocaptcha, "solve_grid", two)
    monkeypatch.setattr(capsolver, "solve_grid", cap)
    doors = solvers.of(_Settings(cap="C", two="T"))
    assert solvers.solve_grid(doors, "b", "cars", size=3) == ([0, 2], 3)
    assert calls == [("2captcha", "T", 3), ("capsolver", "C")]


def test_when_every_door_fails_the_last_error_is_the_one_raised(monkeypatch):
    def two(key, image, question, **kw):
        raise twocaptcha.TwoCapError("no slot")

    def cap(key, image, question, **kw):
        raise capsolver.CapError("zero balance")

    monkeypatch.setattr(twocaptcha, "solve_grid", two)
    monkeypatch.setattr(capsolver, "solve_grid", cap)
    with pytest.raises(capsolver.CapError, match="zero balance"):
        solvers.solve_grid(solvers.of(_Settings(cap="C", two="T")), "b",
                           "cars", size=3)
    with pytest.raises(RuntimeError, match="no captcha solver"):
        solvers.solve_grid(solvers.of(_Settings()), "b", "cars", size=3)


def test_the_settings_read_both_keys_and_the_preference(monkeypatch):
    from geelark_farm.config import Settings

    monkeypatch.setenv("GEELARK_APP_ID", "id")
    monkeypatch.setenv("GEELARK_API_KEY", "key")
    monkeypatch.setenv("TWOCAPTCHA_KEY", "T")
    monkeypatch.setenv("CAPTCHA_SOLVER", " CapSolver ")
    monkeypatch.delenv("CAPSOLVER_KEY", raising=False)
    s = Settings.load()
    assert (s.twocaptcha_key, s.captcha_solver) == ("T", "capsolver")
    assert solvers.of(s).order == ("2captcha",)
