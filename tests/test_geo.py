"""Where an exit is, so the phone keeps the same clock (2026-09-11)."""
from __future__ import annotations

import io
import json

import pytest

from geelark_farm import geo


class _Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_lookup_is_made_once_and_remembered(monkeypatch, make_settings,
                                              tmp_path):
    asked = []

    def urlopen(url, timeout):
        asked.append(url)
        return _Answer(json.dumps({"status": "success", "countryCode": "NL",
                                   "timezone": "Europe/Amsterdam",
                                   "isp": "WorldStream", "hosting": True}
                                  ).encode())

    monkeypatch.setattr(geo.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(geo, "_memory", {})
    settings = make_settings(state_dir=tmp_path, store_enabled=False)

    assert geo.timezone_for(settings, "212.8.252.6") == "Europe/Amsterdam"
    assert geo.country_for(settings, "212.8.252.6") == "NL"
    assert len(asked) == 1 and "212.8.252.6" in asked[0]
    assert geo.lookup(settings, "212.8.252.6")["hosting"] is True
    assert geo.lookup(settings, "") is None and asked[-1].count("json/") == 1


def test_a_failed_lookup_keeps_the_clock_and_never_raises(monkeypatch,
                                                         make_settings,
                                                         tmp_path):
    def down(url, timeout):
        raise OSError("no route")

    monkeypatch.setattr(geo.urllib.request, "urlopen", down)
    monkeypatch.setattr(geo, "_memory", {})
    settings = make_settings(state_dir=tmp_path, store_enabled=False)
    assert geo.timezone_for(settings, "1.2.3.4") == ""

    def refused(url, timeout):
        return _Answer(b'{"status": "fail", "message": "private range"}')

    monkeypatch.setattr(geo.urllib.request, "urlopen", refused)
    assert geo.lookup(settings, "10.0.0.1") is None


def test_the_store_remembers_what_was_looked_up(monkeypatch, make_settings,
                                                tmp_path):
    from geelark_farm.store import state as store_state

    kept = {}
    monkeypatch.setattr(store_state, "get",
                        lambda s, key, default=None: kept.get(key, default))
    monkeypatch.setattr(store_state, "put",
                        lambda conn, key, value: kept.__setitem__(key, value))

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def commit(self):
            pass

    from geelark_farm.store import db as store_db

    monkeypatch.setattr(store_db, "connect", lambda s: _Conn())
    asked = []
    monkeypatch.setattr(geo.urllib.request, "urlopen", lambda url, timeout: (
        asked.append(url) or _Answer(json.dumps(
            {"status": "success", "countryCode": "TR",
             "timezone": "Europe/Istanbul"}).encode())))
    monkeypatch.setattr(geo, "_memory", {})
    settings = make_settings(state_dir=tmp_path, store_enabled=True)

    assert geo.timezone_for(settings, "82.27.118.182") == "Europe/Istanbul"
    assert kept["geo_ips"]["82.27.118.182"]["cc"] == "TR"
    monkeypatch.setattr(geo, "_memory", {})
    assert geo.timezone_for(settings, "82.27.118.182") == "Europe/Istanbul"
    assert len(asked) == 1, "the second ask came from the store"


# ------------------------------------ GeeLark as the source (2026-09-12)
def test_geelarks_own_check_places_the_exit_and_is_remembered(
        monkeypatch, make_settings, tmp_path):
    """The address lookup answered for none of our exits - this server
    cannot resolve ip-api.com - while GeeLark answers for every one of
    them with a country, a city and a timezone. So it is the source."""
    from geelark_farm import proxy as proxy_mod

    monkeypatch.setattr(proxy_mod, "check", lambda client, proxy: {
        "detectStatus": True, "outboundIP": "185.68.81.45",
        "countryCode": "US", "countryName": "United States of America",
        "city": "Washington", "timezone": "America/New_York",
        "isp": "Clouvider"})
    monkeypatch.setattr(geo, "_memory", {})
    settings = make_settings(state_dir=tmp_path, store_enabled=False)

    place = geo.by_proxy(object(), object(), settings, also="1.2.3.4")

    assert place["tz"] == "America/New_York" and place["cc"] == "US"
    assert place["ip"] == "185.68.81.45" and place["city"] == "Washington"
    # Filed under what GeeLark measured, and under the exit the row had -
    # the same gateway, so the sign-in record finds a country there too.
    assert geo.timezone_for(settings, "185.68.81.45") == "America/New_York"
    assert geo.country_for(settings, "1.2.3.4") == "US"
    assert geo.lookup(settings, "1.2.3.4")["via"].startswith("geelark:")


def test_a_proxy_geelark_cannot_place_leaves_the_clock_alone(
        monkeypatch, make_settings, tmp_path):
    from geelark_farm import proxy as proxy_mod

    monkeypatch.setattr(geo, "_memory", {})
    settings = make_settings(state_dir=tmp_path, store_enabled=False)

    def dead(client, proxy):
        raise proxy_mod.ProxyError("unusable")

    monkeypatch.setattr(proxy_mod, "check", dead)
    assert geo.by_proxy(object(), object(), settings) is None

    monkeypatch.setattr(proxy_mod, "check",
                        lambda client, proxy: {"detectStatus": True,
                                               "outboundIP": "9.9.9.9"})
    assert geo.by_proxy(object(), object(), settings) is None, (
        "no country and no zone is not a place")
    assert geo._memory == {}, "nothing empty was ever remembered"
