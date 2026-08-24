"""`geelark verify` - the one command that says what a setup is missing.

Its whole value is in the failure paths, so those are what is tested: a check
that cannot run must say so rather than report a second, misleading failure,
and every fatal one must say what to do about it.
"""

from __future__ import annotations

import pytest

from geelark_farm import verify
from geelark_farm.verify import FATAL, OK, SKIP, WARN, Check


def states(checks):
    return {check.name: check.state for check in checks}


def detail(checks, name):
    return next(c.detail for c in checks if c.name == name)


# ------------------------------------------------------------------- columns
class FakeTab:
    def __init__(self, headers):
        self._headers = headers
        self.written = []

    def row_values(self, row):
        return list(self._headers)

    def acell(self, ref):
        return type("Cell", (), {"value": self._headers[0]})()

    def update_acell(self, ref, value):
        self.written.append((ref, value))


def full_tabs(**overrides):
    tabs = {name: FakeTab(list(columns))
            for name, columns in verify.REQUIRED_COLUMNS.items()}
    tabs.update(overrides)
    return tabs


def test_a_complete_set_of_tabs_and_columns_passes():
    checks = []
    verify._tabs_and_columns(full_tabs(), checks)

    assert states(checks) == {"tabs": OK, "columns": OK}


def test_a_missing_column_is_fatal_and_says_why_it_matters():
    """The quietest failure in the project: `_set` skips a column the tab does
    not have, on purpose, so a tab without Status claims rows and records
    nothing while the run looks fine."""
    tabs = full_tabs(Gmails=FakeTab(["Address", "Password", "Note",
                                     "Phone Serial"]))
    checks = []

    verify._tabs_and_columns(tabs, checks)

    assert states(checks)["columns"] == FATAL
    said = detail(checks, "columns")
    assert "Gmails.Status" in said
    assert "silently" in said            # why an absent column is not obvious


def test_missing_tabs_skip_the_column_check_rather_than_fail_it_too():
    """One cause, one failure. Reporting every column of a tab that is not
    there buries the thing that is actually wrong."""
    checks = []
    tabs = full_tabs()
    del tabs["Proxy"]

    verify._tabs_and_columns(tabs, checks)

    assert states(checks) == {"tabs": FATAL, "columns": SKIP}
    assert "Proxy" in detail(checks, "tabs")


def test_the_tabs_message_separates_what_it_makes_from_what_you_make():
    """Someone told four tabs are missing creates six by hand and wonders why
    two of them look wrong."""
    checks = []
    tabs = full_tabs()
    del tabs["Phones"]

    verify._tabs_and_columns(tabs, checks)

    assert "made automatically" in detail(checks, "tabs")


# -------------------------------------------------------------- write access
def test_write_access_is_tested_without_changing_anything():
    """Shared as a Viewer, every read works and the first write fails - in the
    middle of a build, after a phone has been paid for."""
    tabs = full_tabs()
    checks = []

    verify._writable(tabs, checks)

    phones = tabs["Phones"]
    assert states(checks)["write access"] == OK
    # The value written back is the one that was already there.
    assert phones.written == [("A1", phones.row_values(1)[0])]


def test_a_read_only_key_is_fatal_and_names_the_role_it_needs():
    tabs = full_tabs()

    def refuse(ref, value):
        raise PermissionError("caller does not have permission")
    tabs["Phones"].update_acell = refuse
    checks = []

    verify._writable(tabs, checks)

    assert states(checks)["write access"] == FATAL
    assert "Editor" in detail(checks, "write access")


# -------------------------------------------------------------------- stock
class FakePool:
    def __init__(self, n):
        self.available = list(range(n))


class FakeBook:
    def __init__(self, gmails, proxies, apps):
        self.gmails = FakePool(gmails)
        self.proxies = FakePool(proxies)
        self.apps = FakePool(apps)


def test_stock_that_would_stop_a_run_is_a_warning_not_a_failure(monkeypatch):
    """Nothing is broken - there is simply nothing to build with, and saying
    so is the difference between "fix your setup" and "top up the tabs"."""
    from geelark_farm import pools
    monkeypatch.setattr(pools.Book, "open",
                        classmethod(lambda cls, s: FakeBook(18, 13, 0)))
    checks = []

    verify._stock(object(), checks)

    assert states(checks)["stock"] == WARN
    assert "app accounts would stop it" in detail(checks, "stock")


def test_stock_that_can_build_passes(monkeypatch):
    from geelark_farm import pools
    monkeypatch.setattr(pools.Book, "open",
                        classmethod(lambda cls, s: FakeBook(5, 5, 5)))
    checks = []

    verify._stock(object(), checks)

    assert states(checks)["stock"] == OK


# ------------------------------------------------------------ the whole run
def test_a_missing_geelark_key_stops_before_anything_else_is_tried(
        monkeypatch):
    """Each check needs the one before it. Reporting a spreadsheet failure to
    someone whose API key is wrong sends them to the wrong problem."""
    monkeypatch.setattr(verify, "_geelark",
                        lambda s, checks: checks.append(
                            Check("geelark api", FATAL, "bad key")) or False)
    settings = type("S", (), {"sheet_id": "x"})()

    checks = verify.run_checks(settings)

    assert [c.name for c in checks] == [".env", "geelark api"]
    assert verify.failed(checks)


def test_an_unset_sheet_id_is_reported_as_the_sheet_check(monkeypatch):
    monkeypatch.setattr(verify, "_geelark", lambda s, checks: True)
    settings = type("S", (), {"sheet_id": ""})()

    checks = verify.run_checks(settings)

    assert states(checks)["spreadsheet"] == FATAL
    assert "GOOGLE_SHEET_ID" in detail(checks, "spreadsheet")


def test_every_fatal_check_says_what_to_do_about_it():
    """A checklist that only says what is wrong leaves someone exactly where
    they were. Each of these is the message a first run actually meets."""
    import inspect

    source = inspect.getsource(verify)
    for fragment in ("come from the GeeLark panel",       # bad API key
                     "download its JSON key",             # no service account
                     "shared with",                       # sheet not shared
                     "needs Editor",                      # shared read-only
                     "made automatically",                # missing tabs
                     "record nothing"):                   # missing columns
        assert fragment in source, fragment


@pytest.mark.parametrize("said, expected", [
    ("<Response [404]>", "no spreadsheet with that id"),
    ("some other failure", "some other failure"),
])
def test_a_bare_404_is_translated(said, expected, monkeypatch):
    """Both a wrong id and an unshared book come back as `<Response [404]>`,
    which tells whoever is reading it nothing at all."""
    import gspread
    from google.oauth2 import service_account
    monkeypatch.setattr(service_account.Credentials,
                        "from_service_account_file",
                        classmethod(lambda cls, path, scopes=None: object()))
    monkeypatch.setattr(gspread, "authorize",
                        lambda creds: (_ for _ in ()).throw(RuntimeError(said)))
    settings = type("S", (), {"service_account_json": "k.json",
                              "sheet_id": "x"})()
    checks = []

    verify._spreadsheet(settings, "bot@x.iam.gserviceaccount.com", checks)

    assert expected in detail(checks, "spreadsheet")
    assert "Editor" in detail(checks, "spreadsheet")


# ------------------------------------------------------------------- sx.org
def sxorg_settings(key="k"):
    return type("S", (), {"sxorg_api_key": key})()


def test_a_key_without_the_column_it_needs_is_a_warning_not_a_pass():
    """Having one of the two is worse than having neither, because it reads
    like it is set up: sx.org is addressed by port id, and against a Proxy tab
    with no Port ID column `_new_exit` takes the expensive fallback every
    single time while verify reported it as working (2026-08-17)."""
    tabs = {"Proxy": FakeTab(["Name", "Proxy String", "Status"])}
    checks = []

    verify._sxorg(sxorg_settings(), tabs, checks)

    assert states(checks)["sx.org"] == WARN
    assert "Port ID" in detail(checks, "sx.org")


def test_both_halves_present_passes():
    tabs = {"Proxy": FakeTab(["Name", "Proxy String", "Port ID"])}
    checks = []

    verify._sxorg(sxorg_settings(), tabs, checks)

    assert states(checks)["sx.org"] == OK


def test_no_key_is_a_fact_not_a_problem():
    """The tool works without it; it just always takes the next proxy."""
    checks = []

    verify._sxorg(sxorg_settings(""), {}, checks)

    assert states(checks)["sx.org"] == verify.INFO


def test_the_column_a_missing_secret_hides_behind_is_required():
    """An account without a 2FA secret is usable; a tab without the column
    makes every row look like one, and each fails at the code page as
    `no_authenticator` - blaming the row for a missing heading (2026-08-23)."""
    from geelark_farm.verify import REQUIRED_COLUMNS

    for tab in ("Gmails", "Gpt Info"):
        assert "2FA Secret" in REQUIRED_COLUMNS[tab]


def test_every_column_the_pools_read_is_required_or_optional_on_purpose():
    """The list is maintained by hand, which is how it lost one. Anything the
    pools read and this does not demand has to be a column the code works
    without - the Proxy tab's split-out form, or one `ensure_columns` adds."""
    import pathlib
    import re

    from geelark_farm.verify import REQUIRED_COLUMNS

    src = pathlib.Path("src/geelark_farm/pools.py").read_text(encoding="utf-8")
    read = set(re.findall(r'values\.get\("([^"]+)"', src))
    required = {c for cols in REQUIRED_COLUMNS.values() for c in cols}

    # Known-optional, each for a reason written where it is used.
    optional = {"Host", "Port", "Username", "Port ID", "Name", "Last Exit IP",
                "Last Refresh", "Claimed", "Times Used", "Last Used",
                "Used Date", "App", "Email code", "Phone ID"}

    assert not (read - required - optional), (
        f"the pools read {sorted(read - required - optional)} and nothing "
        f"says whether the tab has to have them")


# ================= a diagnostic survives the thing it exists to diagnose
class Refusing:
    """A tab that answers a quota error to every read."""

    title = "Proxy"

    def row_values(self, row):
        from geelark_farm.gsheet import GSpreadError

        class Response:
            status_code = 429

            @staticmethod
            def json():
                return {"error": {"code": 429, "message": "quota"}}

            text = "quota"

        raise GSpreadError(Response())


def test_a_quota_on_a_column_read_is_reported_not_raised():
    """`run_checks` says it never raises, and three of its reads reached the
    network without a guard - so a sheet quota, the failure this project
    handles more carefully than any other, would have ended `geelark verify`
    in a traceback (2026-08-23)."""
    checks: list[verify.Check] = []
    tabs = dict.fromkeys(verify.REQUIRED_COLUMNS, Refusing())

    verify._tabs_and_columns(tabs, checks)

    columns = [c for c in checks if c.name == "columns"]
    assert columns and columns[0].state == verify.SKIP
    assert "could not be read" in columns[0].detail


def test_a_quota_on_the_sxorg_check_is_reported_not_raised():
    from geelark_farm.config import Settings

    settings = Settings.__new__(Settings)
    object.__setattr__(settings, "sxorg_api_key", "a-key")
    checks: list[verify.Check] = []

    verify._sxorg(settings, {"Proxy": Refusing()}, checks)

    assert checks[0].state == verify.SKIP


def test_the_tab_listing_that_fails_stops_with_a_check_not_a_traceback():
    """`book.worksheets()` is a network call and was the third unguarded one."""
    import inspect

    source = inspect.getsource(verify.run_checks)

    assert "book.worksheets()" in source
    assert "except Exception" in source


def test_every_read_in_this_module_is_guarded():
    """The promise is the module's own, and keeping it by inspection is how it
    stopped being kept."""
    import ast
    import inspect

    unguarded = []
    for node in ast.parse(inspect.getsource(verify)).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        touches = any(isinstance(n, ast.Attribute) and n.attr in
                      ("row_values", "worksheets", "acell", "update_acell",
                       "open_by_key", "data", "plan", "listing")
                      for n in ast.walk(node))
        if touches and not any(isinstance(n, ast.Try) for n in ast.walk(node)):
            unguarded.append(node.name)
    assert not unguarded, f"these reach the network and can raise: {unguarded}"


def test_the_phone_count_comes_from_the_one_place_that_knows_how_to_list():
    """Called raw, this was the third copy of the paging in the package."""
    import inspect

    source = inspect.getsource(verify._geelark)

    assert "phones.listing(client)" in source
    assert "pageSize" not in source
