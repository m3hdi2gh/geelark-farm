"""`geelark verify` - the one command that says what a setup is missing.

Its whole value is in the failure paths, so those are what is tested: a check
that cannot run must say so rather than report a second, misleading failure,
and every fatal one must say what to do about it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

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
    settings = type("S", (), {"sheet_id": "x", **DIRS})()

    checks = verify.run_checks(settings)

    names = [c.name for c in checks]
    assert names[-1] == "geelark api"       # it stopped there
    assert "spreadsheet" not in names       # and never reached the sheet
    assert verify.failed(checks)


def test_an_unset_sheet_id_is_reported_as_the_sheet_check(monkeypatch):
    monkeypatch.setattr(verify, "_geelark", lambda s, checks: True)
    settings = type("S", (), {"sheet_id": "", **DIRS})()

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
    settings = type("S", (), {**DIRS, "service_account_json": "k.json",
                              "sheet_id": "x"})()
    checks = []

    verify._spreadsheet(settings, "bot@x.iam.gserviceaccount.com", checks)

    assert expected in detail(checks, "spreadsheet")
    assert "Editor" in detail(checks, "spreadsheet")


def test_the_column_a_missing_secret_hides_behind_is_required():
    """An account without a 2FA secret is usable; a tab without the column
    makes every row look like one, and each fails at the code page as
    `no_authenticator` - blaming the row for a missing heading (2026-08-23)."""
    from geelark_farm.verify import REQUIRED_COLUMNS

    # One column each, and they are named differently: the Gmails tab's
    # `Secret` holds either an authenticator key or a recovery address, and
    # `Gpt Info` has only ever held the first.
    assert "Secret" in REQUIRED_COLUMNS["Gmails"]
    assert "2FA Secret" in REQUIRED_COLUMNS["Gpt Info"]


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
    # `Seller` is read but never demanded on purpose: only the two names in
    # `GmailPool.SELLERS` promise anything about a row, and a tab without the
    # column simply makes no promises - which is what every batch did before
    # the promise existed.
    optional = {"Host", "Port", "Username", "Name", "Last Exit IP",
                "Claimed", "Times Used", "Last Used",
                "Used Date", "App", "Email code", "Phone ID", "Seller"}

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


# =====================================================================
# The checks that reach the network, and the order they stop in
# (2026-08-26). `run_checks` never raises - a promise, not a hope: a
# sheet quota, the failure this project handles more carefully than any
# other, would otherwise have ended `geelark verify` in a traceback.
# =====================================================================

def geelark(monkeypatch, *, listing=None, plan=None, boom=None,
            plan_boom=None):
    """GeeLark answering, or refusing, the two calls this check makes."""
    from geelark_farm import phones

    def listing_(client):
        if boom:
            raise boom
        return listing or []

    def plan_(client):
        if plan_boom:
            raise plan_boom
        return plan or {}

    monkeypatch.setattr(phones, "listing", listing_)
    monkeypatch.setattr(phones, "plan", plan_)
    monkeypatch.setattr("geelark_farm.api.Client.__init__",
                        lambda self, settings, **kw: None)


#: The three run_checks now writes a probe into. Real and writable, because
#: the check exists to find out whether they are.
DIRS = {"state_dir": Path(tempfile.gettempdir()),
        "log_dir": Path(tempfile.gettempdir()),
        "artifact_dir": Path(tempfile.gettempdir())}


def api_settings(**over):
    base = {"app_id": "APPID123456", "api_key": "k",
            "api_requests_per_minute": 120, "sheet_id": "",
            "sxorg_api_key": "", **DIRS}
    base.update(over)
    return type("S", (), base)()


# ------------------------------------------------------ the key, first of all
def test_a_key_that_works_says_what_it_can_see(monkeypatch):
    """The count is the useful half: a key that authenticates against an empty
    account looks identical to one pointed at the wrong account."""
    geelark(monkeypatch, listing=[{"id": "P1"}, {"id": "P2"}],
            plan={"profiles": 30, "availableProfiles": 5})
    checks = []

    assert verify._geelark(api_settings(), checks) is True
    assert states(checks)["geelark api"] == OK
    assert "2 phone(s)" in detail(checks, "geelark api")


def test_a_key_that_does_not_work_stops_everything_after_it(monkeypatch):
    """Fatal, and it returns False so the sheet checks below never run. A
    second failure caused by the first is a second thing to read and nothing
    to act on."""
    geelark(monkeypatch, boom=RuntimeError("[40003] signature rejected"))
    checks = []

    assert verify._geelark(api_settings(), checks) is False
    assert states(checks)["geelark api"] == FATAL
    assert "GEELARK_APP_ID" in detail(checks, "geelark api")


def test_a_plan_this_check_rate_limited_itself_is_not_a_warning(monkeypatch):
    """GeeLark allows one call a minute here, separately from the account
    limit, so running verify twice in a row rate-limits this one check. A
    diagnostic that cries wolf about itself is worse than one that says
    plainly it did not look."""
    geelark(monkeypatch, plan_boom=RuntimeError("Too Many Requests"))
    checks = []

    assert verify._geelark(api_settings(), checks) is True
    assert states(checks)["plan"] == verify.INFO
    assert "try again in a minute" in detail(checks, "plan")


def test_a_plan_that_fails_for_a_real_reason_is_a_warning(monkeypatch):
    """Not fatal - the key works and phones can still be listed - but not
    silence either."""
    geelark(monkeypatch, plan_boom=RuntimeError("connection reset"))
    checks = []

    verify._geelark(api_settings(), checks)

    assert states(checks)["plan"] == WARN


def test_a_plan_with_no_free_slots_is_worth_saying_before_a_build(monkeypatch):
    """Every phone would fail to create, one after another, each spending a
    Gmail and a proxy on the way."""
    geelark(monkeypatch, plan={"profiles": 30, "availableProfiles": 0})
    checks = []

    verify._geelark(api_settings(), checks)

    assert states(checks)["plan"] == WARN
    assert "none free" in detail(checks, "plan")


# ---------------------------------------------------------- the key file
def test_a_missing_service_account_says_where_it_looked(tmp_path):
    """The path is the whole of the fix, and it is configurable - so printing
    the default would send someone to the wrong place."""
    settings = api_settings(service_account_json=tmp_path / "nowhere.json")
    checks = []

    assert verify._key_file(settings, checks) == ""
    assert states(checks)["service account"] == FATAL
    assert str(tmp_path) in detail(checks, "service account")


def test_a_key_file_that_is_not_a_key_says_so_rather_than_crashing(tmp_path):
    """A downloaded HTML error page saved as .json is the ordinary way this
    goes wrong."""
    path = tmp_path / "key.json"
    path.write_text("<html>Sign in</html>", encoding="utf-8")
    checks = []

    assert verify._key_file(api_settings(service_account_json=path),
                            checks) == ""
    assert states(checks)["service account"] == FATAL


def test_a_key_file_missing_its_address_is_not_usable_either(tmp_path):
    """The address is what the spreadsheet has to be shared with, so a key
    without one cannot be acted on even though it parses."""
    path = tmp_path / "key.json"
    path.write_text('{"type": "service_account"}', encoding="utf-8")
    checks = []

    assert verify._key_file(api_settings(service_account_json=path),
                            checks) == ""


def test_a_usable_key_hands_back_the_address_to_share_with(tmp_path):
    path = tmp_path / "key.json"
    path.write_text('{"client_email": "bot@project.iam.gserviceaccount.com"}',
                    encoding="utf-8")
    checks = []

    found = verify._key_file(api_settings(service_account_json=path), checks)

    assert found == "bot@project.iam.gserviceaccount.com"
    assert states(checks)["service account"] == OK


# ------------------------------------------------------- and the order
def test_a_run_with_no_sheet_configured_stops_before_the_sheet_checks(
        monkeypatch, tmp_path):
    """Every check below reads the book. Running them without an id produces
    four failures that all say the same thing."""
    geelark(monkeypatch, listing=[], plan={"profiles": 30,
                                           "availableProfiles": 1})
    settings = api_settings(sheet_id="",
                            service_account_json=tmp_path / "key.json")

    checks = verify.run_checks(settings)

    names = [c.name for c in checks]
    assert "spreadsheet" in names
    assert "columns" not in names, "it went on reading a book it cannot open"
    assert states(checks)["spreadsheet"] == FATAL


def test_a_key_that_fails_stops_before_the_sheet_is_even_opened(monkeypatch,
                                                                tmp_path):
    geelark(monkeypatch, boom=RuntimeError("[40003] signature rejected"))
    settings = api_settings(sheet_id="abc",
                            service_account_json=tmp_path / "key.json")

    checks = verify.run_checks(settings)

    assert [c.name for c in checks][-1] == "geelark api"


def test_the_checks_never_raise_whatever_the_network_does(monkeypatch,
                                                           tmp_path):
    """The promise this function makes. A diagnostic that cannot survive the
    thing it exists to diagnose is worth less than nothing (2026-08-23)."""
    geelark(monkeypatch, boom=OSError("no route to host"))
    settings = api_settings(sheet_id="abc",
                            service_account_json=tmp_path / "key.json")

    checks = verify.run_checks(settings)          # no raise

    assert any(c.state == FATAL for c in checks)


def test_a_key_it_cannot_even_look_at_is_reported_not_raised(monkeypatch,
                                                              tmp_path):
    """`exists()` raises rather than answering when the directory above the
    file cannot be searched - which is what a container meets when the key is
    readable only by the host user. This module promises never to raise, and
    that was the one call in it that could (2026-08-27)."""
    class Unreachable:
        name = "service-account.json"

        def exists(self):
            raise PermissionError(13, "Permission denied")

        def __str__(self):
            return "/app/secrets/service-account.json"

    checks = []
    settings = type("S", (), {"service_account_json": Unreachable()})()

    assert verify._key_file(settings, checks) == ""
    assert states(checks)["service account"] == FATAL
    assert "readable by the uid" in detail(checks, "service account")
