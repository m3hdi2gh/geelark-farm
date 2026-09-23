"""The products registry: one entry per app the farm signs accounts into,
and the builder asking it rather than keeping its own ladders (the
builder review, 2026-09-23)."""
from __future__ import annotations

import inspect
import pathlib
from types import SimpleNamespace

from geelark_farm import builder, failures, products

#: What the builder hands every app flow's `sign_in` (builder._sign_into_app).
BUILDER_KEYWORDS = {"package", "budget_seconds", "artifact_dir", "codes",
                    "solver_key", "watch", "fresh"}


def test_every_product_names_a_flow_the_builder_can_call():
    """A registry entry is a promise: the flow imports, takes what the
    builder passes, and every reason it can hand back has a verdict."""
    settings = SimpleNamespace(target_package="com.openai.chatgpt")
    for key, spec in products.PRODUCTS.items():
        assert spec.key == key
        flow = spec.flow_module()
        params = set(inspect.signature(flow.sign_in).parameters)
        assert BUILDER_KEYWORDS <= params, (key, BUILDER_KEYWORDS - params)
        declared = getattr(flow, "PACKAGE", None)
        if declared:
            assert declared == spec.package_for(settings), key
        # Asked of the taxonomy, as test_failures does: the `stuck_on_`
        # family has a rule rather than entries.
        for reason in failures.reasons_reported_by(flow) - failures.SUCCESSES:
            assert failures.knows(reason), (key, reason)


def test_every_app_sign_in_flow_has_an_entry():
    """A flow written and never registered is a flow the builder cannot
    reach - the Google sign-in is the phone's, not an app's."""
    flows = pathlib.Path(builder.__file__).parent / "flows"
    written = {p.stem for p in flows.glob("*_login.py")} - {"google_login"}
    registered = {spec.flow.rsplit(".", 1)[-1]
                  for spec in products.PRODUCTS.values()}
    assert written == registered


def test_a_row_with_no_product_is_the_default_one():
    assert products.product_of({}) == products.DEFAULT == "chatgpt"
    assert products.product_of({"Product": " Claude "}) == "claude"
    assert products.spec_of({"Product": "nonesuch"}).key == "chatgpt"
    assert products.spec_of(None).key == "chatgpt"


def test_the_builder_asks_the_registry_instead_of_keeping_ladders():
    """Three `if product == ...` ladders and five tables were the builder's
    own copy of what each app is; a new app touched eleven places there."""
    source = inspect.getsource(builder)
    for ladder in ('product == "claude"', 'product == "spotify"',
                   'app == "spotify"', 'app == "claude"'):
        assert ladder not in source, ladder
    assert builder.APPS == {k: s.name for k, s in products.PRODUCTS.items()}
    assert builder.SERVICES == {k: s.service
                                for k, s in products.PRODUCTS.items()}
    row = SimpleNamespace(values={"Product": "claude"})
    settings = SimpleNamespace(target_package="com.openai.chatgpt")
    flow, package = builder._flow_for(settings, row)
    assert flow is products.PRODUCTS["claude"].flow_module()
    assert package == "com.anthropic.claude"
    assert builder._codes_for(settings, row, "given") == "given", (
        "a Claude code is the customer's")


def test_the_console_names_every_products_sign_in():
    """claude_login and spotify_login lines showed under their raw logger
    names on the dashboard."""
    from geelark_farm.web import pages

    for spec in products.PRODUCTS.values():
        logger = spec.flow.rsplit(".", 1)[-1]
        assert pages._FLOW_WORDS[logger] == f"{spec.key} sign-in"
    assert pages._FLOW_WORDS["chatgpt_login"] == "chatgpt sign-in", (
        "the word the dashboard has always said")


def test_the_lists_tab_offers_every_products_reasons():
    """The app pool holds all three products; its dropdown read only
    ChatGPT's flow, so a Claude or Spotify reason was flagged."""
    from geelark_farm import pools

    source = inspect.getsource(pools.Book.sync_lists)
    assert "products.PRODUCTS.values()" in source
    assert "credential_reasons(chatgpt_login)" not in source
