"""GeeLark's own installer - the door that takes Play out of a build for
the apps the app center carries (2026-09-08)."""

from __future__ import annotations

import pytest

from geelark_farm import apps


class Center:
    """A client whose app center carries Spotify and nothing else, and
    which answers `/app/install` with whatever code it is told to."""

    def __init__(self, install_code=0):
        self.install_code = install_code
        self.posts = []

    def post(self, path, payload=None, **kw):
        self.posts.append((path, payload))
        if path == apps.INSTALLABLE:
            items = [] if payload["name"] == "ChatGPT" else [
                {"appName": "Snapchat", "packageName": "com.snapchat.android",
                 "appVersionInfoList": [{"id": "1", "versionName": "1"}]},
                {"appName": "Spotify", "packageName": "com.spotify.music",
                 "appVersionInfoList": [
                     {"id": "2067127873273200642", "versionName": "9.1.56"},
                     {"id": "2051029237240209410", "versionName": "9.1.46"}]},
            ]
            return {"code": 0, "data": {"total": len(items), "items": items}}
        if path == apps.INSTALL:
            return {"code": self.install_code, "msg": "as told"}
        raise AssertionError(path)


@pytest.fixture(autouse=True)
def fresh_cache():
    apps._versions.clear()
    yield
    apps._versions.clear()


def test_the_newest_version_of_the_package_asked_for_is_the_one_ordered():
    """Matched on the package, never on the search word: a search for
    "Chat" finds Snapchat, and the first version listed is the newest."""
    client = Center()
    assert apps.version_id(client, "P", "com.spotify.music",
                           name="Spotify") == "2067127873273200642"
    assert apps.version_id(client, "P", "com.openai.chatgpt",
                           name="ChatGPT") == ""
    # Cached for the process once known: the id is the center's, not the
    # phone's, and the listing is a paged call nobody wants once a build.
    apps.version_id(client, "Q", "com.spotify.music", name="Spotify")
    assert [p for p, _ in client.posts].count(apps.INSTALLABLE) == 2


def test_begin_orders_the_install_and_says_whether_geelark_took_it():
    client = Center()
    assert apps.begin(client, "P", "com.spotify.music", name="Spotify")
    assert client.posts[-1] == (apps.INSTALL, {
        "envId": "P", "appVersionId": "2067127873273200642"})

    # An install already under way is the order already given.
    assert apps.begin(Center(install_code=apps.ALREADY_INSTALLING), "P",
                      "com.spotify.music", name="Spotify")


def test_every_refusal_is_a_no_and_never_a_failed_build():
    # Not in the center: Play's turn.
    assert not apps.begin(Center(), "P", "com.openai.chatgpt", name="ChatGPT")
    # Refused outright.
    assert not apps.begin(Center(install_code=42002), "P",
                          "com.spotify.music", name="Spotify")
    # A version the center dropped is forgotten, so the next build asks
    # the listing again rather than repeating the refusal.
    apps._versions["com.spotify.music"] = "stale"
    assert not apps.begin(Center(install_code=apps.NO_SUCH_VERSION), "P",
                          "com.spotify.music", name="Spotify")
    assert "com.spotify.music" not in apps._versions

    class Down:
        def post(self, *a, **k):
            raise ConnectionError("no route")
    assert not apps.begin(Down(), "P", "com.spotify.music", name="Spotify")


def test_the_proof_is_the_package_list_polled_within_the_budget(monkeypatch):
    seen = {"asks": 0}

    def installed(client, phone_id, package, *, strict=True):
        seen["asks"] += 1
        assert strict is False, "a poll is lenient"
        return seen["asks"] >= 3

    monkeypatch.setattr(apps.shell, "package_installed", installed)
    slept = []
    monkeypatch.setattr(apps.time, "sleep", lambda s: slept.append(s))
    assert apps.wait_installed(object(), "P", "com.spotify.music",
                               budget_seconds=120)
    assert seen["asks"] == 3 and slept == [10, 10]

    # Out of budget: a no, not an exception - and the sleeps never overrun
    # the budget.
    clock = {"now": 0.0}
    monkeypatch.setattr(apps.time, "monotonic", lambda: clock["now"])

    def tick(s):
        slept.append(s)
        clock["now"] += s

    monkeypatch.setattr(apps.time, "sleep", tick)
    monkeypatch.setattr(apps.shell, "package_installed",
                        lambda *a, **k: False)
    slept.clear()
    assert not apps.wait_installed(object(), "P", "com.spotify.music",
                                   budget_seconds=25)
    assert slept == [10, 10, 5]

    # Cancelled: out at once.
    assert not apps.wait_installed(object(), "P", "com.spotify.music",
                                   budget_seconds=25, cancelled=lambda: True)
