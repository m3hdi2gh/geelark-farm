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
    def clear():
        apps._versions.clear()
        apps._ours.clear()
        apps._ours_at = 0.0
    clear()
    yield
    clear()


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


# ------------------------------------------- the apps we uploaded ourselves
def _uploaded(monkeypatch, row, *, asked=None):
    """`store_state.get` answering with `row`, counting the reads."""
    from geelark_farm.store import state as store_state

    def get(settings, key, default=None):
        assert key == apps.STATE_KEY
        if asked is not None:
            asked.append(key)
        return row

    monkeypatch.setattr(store_state, "get", get)


def test_an_app_we_uploaded_is_found_in_the_store_not_in_the_listing(
        monkeypatch, make_settings):
    """`installable/list` is GeeLark's own catalogue and an upload never
    joins it - verified on phone 2369, where a search for Claude and one
    for ChatGPT both came back empty while both were installed. The id
    comes back once, from the upload, so it is written down (2026-09-12).
    """
    settings = make_settings(store_enabled=True)
    _uploaded(monkeypatch, {"com.openai.chatgpt": "2098579148801630209"})
    client = Center()

    assert apps.version_id(client, "P", "com.openai.chatgpt", name="ChatGPT",
                           settings=settings) == "2098579148801630209"
    assert apps.INSTALLABLE not in [p for p, _ in client.posts], (
        "the catalogue is not asked about an app it cannot carry")

    # And the catalogue still answers for what is GeeLark's own.
    assert apps.version_id(client, "P", "com.spotify.music", name="Spotify",
                           settings=settings) == "2067127873273200642"


def test_the_store_is_read_once_in_a_while_not_once_a_build(monkeypatch,
                                                            make_settings):
    settings = make_settings(store_enabled=True)
    asked = []
    _uploaded(monkeypatch, {"com.anthropic.claude": "42"}, asked=asked)

    assert apps.uploaded(settings, "com.anthropic.claude") == "42"
    assert apps.uploaded(settings, "com.openai.chatgpt") == ""
    assert len(asked) == 1, "one read covers every package"

    # A fresh upload is picked up without a restart: the row is believed
    # for STATE_SECONDS and then read again.
    monkeypatch.setattr(apps.time, "monotonic",
                        lambda: apps._ours_at + apps.STATE_SECONDS + 1)
    assert apps.uploaded(settings, "com.anthropic.claude") == "42"
    assert len(asked) == 2


def test_a_store_that_cannot_be_read_falls_back_to_what_it_last_said(
        monkeypatch, make_settings):
    """One unlucky query is not a reason to send an app through the Play
    Store, and the clock is left where it was so the next caller tries
    again."""
    settings = make_settings(store_enabled=True)
    _uploaded(monkeypatch, {"com.anthropic.claude": "42"})
    assert apps.uploaded(settings, "com.anthropic.claude") == "42"

    from geelark_farm.store import state as store_state
    monkeypatch.setattr(store_state, "get", lambda *a, **k: (_ for _ in ())
                        .throw(ConnectionError("no cluster")))
    apps._ours_at = 0.0
    assert apps.uploaded(settings, "com.anthropic.claude") == "42"
    assert apps._ours_at == 0.0, "not believed, so the next caller re-reads"

    # No store at all: nothing of ours, and the catalogue answers alone.
    assert apps.uploaded(make_settings(), "com.anthropic.claude") == ""
    assert apps.uploaded(None, "com.anthropic.claude") == ""


def test_a_version_the_center_dropped_is_forgotten_on_both_sides(
        monkeypatch, make_settings):
    """`forget` has to clear what the store said as well, or the stale id
    is handed straight back and every build repeats the refusal."""
    settings = make_settings(store_enabled=True)
    _uploaded(monkeypatch, {"com.anthropic.claude": "stale"})
    assert apps.uploaded(settings, "com.anthropic.claude") == "stale"

    apps.forget("com.anthropic.claude")
    assert apps._ours == {} and apps._ours_at == 0.0
