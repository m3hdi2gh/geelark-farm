"""The two lanes of stock: the words, the products behind them, the targets."""
from __future__ import annotations

from types import SimpleNamespace

from geelark_farm import purposes


def test_products_fall_into_two_lanes_and_words_are_read_loosely():
    assert purposes.of_product("chatgpt") == "gpt"
    assert purposes.of_product("claude") == "gpt", "Claude rides in the GPT lane"
    assert purposes.of_product("spotify") == "spotify"
    assert purposes.of_product("") == "gpt", "a blank Product always meant ChatGPT"
    assert purposes.normal(" GPT ") == "gpt" and purposes.normal("Spotify") == "spotify"
    assert purposes.normal("chatgpt") == "gpt" and purposes.normal("nope") == ""
    assert purposes.word("gpt") == "GPT" and purposes.word("spotify") == "Spotify"
    assert purposes.word("") == ""


def test_an_unlabelled_row_fits_either_lane_and_a_labelled_one_its_own():
    assert purposes.fits("", "gpt") and purposes.fits("", "spotify")
    assert purposes.fits("gpt", "gpt") and not purposes.fits("gpt", "spotify")
    assert purposes.fits("Spotify", "spotify")
    assert purposes.fits("spotify", ""), "no lane asked: anything fits"


def test_targets_split_warm_stock_with_spotify_never_past_the_total():
    assert purposes.targets(SimpleNamespace(warm_stock=8, warm_stock_spotify=4)) \
        == {"gpt": 4, "spotify": 4}
    assert purposes.targets(SimpleNamespace(warm_stock=8, warm_stock_spotify=0)) \
        == {"gpt": 8, "spotify": 0}, "no Spotify share: as the farm always ran"
    assert purposes.targets(SimpleNamespace(warm_stock=3, warm_stock_spotify=9)) \
        == {"gpt": 0, "spotify": 3}
