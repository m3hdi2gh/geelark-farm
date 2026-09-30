"""Icons on the phone's home screen (2026-09-30): the launcher's table as
`content query` prints it, where a free cell is, and the whole of `pin`
against a fake phone whose launcher behaves as the real one did."""

from __future__ import annotations

import logging
import re

import pytest

from geelark_farm import builder, shell
from geelark_farm.kit import homescreen as hs

CHATGPT = ("com.openai.chatgpt", "ChatGPT")
CLAUDE = ("com.anthropic.claude", "Claude")
SPOTIFY = ("com.spotify.music", "Spotify")
THREE = [CHATGPT, CLAUDE, SPOTIFY]
COMPONENTS = {"com.openai.chatgpt": "com.openai.chatgpt/.MainActivity",
              "com.anthropic.claude": "com.anthropic.claude/.mainactivity.MainActivity",
              "com.spotify.music": "com.spotify.music/.MainActivity"}

# The table of a real phone (5194, Android 15, Launcher3), as it printed.
_LAUNCH = ("#Intent;action=android.intent.action.MAIN;"
           "category=android.intent.category.LAUNCHER;launchFlags=0x10200000;")
REAL = "\n".join([
    f"Row: 0 container=-101, screen=0, cellX=0, cellY=0, spanX=1, spanY=1, "
    f"itemType=0, intent={_LAUNCH}package=com.android.dialer;"
    f"component=com.android.dialer/.main.impl.MainActivity;end",
    f"Row: 1 container=-101, screen=1, cellX=1, cellY=0, spanX=1, spanY=1, "
    f"itemType=0, intent={_LAUNCH}package=com.android.messaging;"
    f"component=com.android.messaging/.ui.conversationlist.ConversationListActivity;end",
    f"Row: 2 container=-101, screen=2, cellX=2, cellY=0, spanX=1, spanY=1, "
    f"itemType=0, intent={_LAUNCH}package=com.android.chrome;"
    f"component=com.android.chrome/com.google.android.apps.chrome.Main;end",
    f"Row: 3 container=-100, screen=0, cellX=1, cellY=3, spanX=1, spanY=1, "
    f"itemType=0, intent={_LAUNCH}package=com.android.gallery3d;"
    f"component=com.android.gallery3d/.app.GalleryActivity;end",
    f"Row: 4 container=-100, screen=0, cellX=3, cellY=3, spanX=1, spanY=1, "
    f"itemType=0, intent={_LAUNCH}package=com.android.vending;"
    f"component=com.android.vending/.AssetBrowserActivity;end",
])


# ================================================================== parse
def test_the_launchers_table_is_read_as_it_prints():
    items = hs.parse(REAL)
    assert len(items) == 5
    gallery = items[3]
    assert (gallery["container"], gallery["screen"], gallery["cellX"],
            gallery["cellY"], gallery["spanX"], gallery["spanY"],
            gallery["itemType"]) == (-100, 0, 1, 3, 1, 1, 0)
    assert "component=com.android.gallery3d/.app.GalleryActivity" in \
        gallery["intent"]
    assert items[0]["container"] == hs.HOTSEAT


def test_nothing_readable_is_no_table():
    assert hs.parse("") == []
    assert hs.parse("No result found.") == []
    assert hs.parse("Error while accessing provider: java.lang.Security"
                    "Exception: denied") == []
    # A row with no container is not a launcher row.
    assert hs.parse("Row: 0 title=x") == []


# ============================================================ free cells
def test_three_icons_go_in_one_row_below_the_search_bar():
    screen, cells = hs.free_cells(hs.parse(REAL), 3, grid=4)
    # Row 0 is the launcher's search bar, which is in no table, and it
    # deletes an icon placed there; row 3 has the gallery and the store.
    assert (screen, cells) == (0, [(0, 1), (1, 1), (2, 1)])


def _item(x, y, sx=1, sy=1, screen=0, container=hs.DESKTOP):
    return {"container": container, "screen": screen, "cellX": x, "cellY": y,
            "spanX": sx, "spanY": sy, "intent": "x"}


def test_the_first_screens_top_row_is_never_used_even_when_empty():
    items = [_item(0, 3)]
    _, cells = hs.free_cells(items, 3, grid=4)
    assert all(y >= 1 for _, y in cells)
    # On a screen that is not the first, row 0 is the same reserved row of
    # the page the launcher opens on - the first screen is the lowest id.
    assert hs.free_cells([_item(0, 3, screen=2)], 1, grid=4)[0] == 2


def test_a_widget_blocks_every_cell_it_spans():
    items = [_item(0, 1, sx=4, sy=2), _item(1, 3)]
    # Row 0 is the search bar's, rows 1-2 are the widget's: only three
    # cells of row 3 are left, and no three of them are side by side.
    assert hs.free_cells(items, 3, grid=4) == (0, [(0, 3), (2, 3), (3, 3)])
    assert hs.free_cells(items, 4, grid=4)[1] == []


def test_no_row_with_room_takes_reading_order():
    # Every row has one cell taken, so no row has three side by side.
    items = [_item(1, 1), _item(1, 2), _item(1, 3)]
    _, cells = hs.free_cells(items, 3, grid=4)
    assert cells == [(0, 1), (2, 1), (3, 1)]


def test_a_full_screen_has_no_room():
    items = [_item(x, y) for x in range(4) for y in range(1, 4)]
    assert hs.free_cells(items, 1, grid=4)[1] == []
    assert hs.free_cells([], 3, grid=4) == (0, [])


def test_the_grid_is_at_least_what_its_items_reach():
    # Without the database's name (grid 0) the box is what the items reach.
    items = [_item(3, 3)]
    _, cells = hs.free_cells(items, 3, grid=0)
    assert cells == [(0, 1), (1, 1), (2, 1)]
    _, cells = hs.free_cells([_item(0, 0)], 3, grid=0)
    assert cells == []                      # a 1x1 box with its top row spent


# =============================================================== commands
def test_the_insert_names_every_column_and_quotes_what_it_writes():
    cmd = hs._insert("ChatGPT", "com.openai.chatgpt/.MainActivity", 0, 2, 1)
    assert cmd.startswith(f"content insert --uri {hs.FAVORITES} ")
    for bind in ("title:s:'ChatGPT'", "container:i:-100", "screen:i:0",
                 "cellX:i:2", "cellY:i:1", "spanX:i:1", "spanY:i:1",
                 "itemType:i:0", "profileId:i:0"):
        assert f"--bind {bind}" in cmd
    assert ("--bind intent:s:'#Intent;action=android.intent.action.MAIN;"
            "category=android.intent.category.LAUNCHER;launchFlags=0x10200000;"
            "component=com.openai.chatgpt/.MainActivity;end'") in cmd


def test_the_component_is_what_the_phone_resolves_and_nothing_a_shell_could_run(
        monkeypatch):
    def answer(text):
        monkeypatch.setattr(shell, "read", lambda c, p, cmd: text)
        return hs._component(None, "1", "com.openai.chatgpt")

    resolved = ("priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 "
                "isDefault=false\ncom.openai.chatgpt/.MainActivity\n")
    assert answer(resolved) == "com.openai.chatgpt/.MainActivity"
    assert answer("No activity found") == ""
    assert answer("") == ""
    assert answer("x/y; rm -rf /data") == ""
    assert answer("com.a/.B'; reboot; '") == ""
    # And a package that is not one never reaches the phone at all.
    sent = []
    monkeypatch.setattr(shell, "read", lambda c, p, cmd: sent.append(cmd) or "")
    assert hs._component(None, "1", "a; reboot") == ""
    assert sent == []


# ===================================================================== pin
class Phone:
    """A launcher that answers `content query` and takes `content insert`
    the way the measured one did: an icon at row 0 of the first screen is
    thrown out when the launcher loads, the rest stay."""

    def __init__(self, monkeypatch, *, rows=REAL, installed=None, grid="4_by_4",
                 takes=True, breaks=False):
        self.rows = hs.parse(rows) if isinstance(rows, str) else list(rows)
        self.installed = (COMPONENTS if installed is None else installed)
        self.grid = grid
        self.takes = takes
        self.breaks = breaks
        self.commands: list[str] = []
        monkeypatch.setattr(shell, "read", self.read)
        monkeypatch.setattr(shell, "run", self.run)

    def _print(self) -> str:
        lines = []
        for i, r in enumerate(self.rows):
            lines.append(
                f"Row: {i} container={r['container']}, screen={r['screen']}, "
                f"cellX={r['cellX']}, cellY={r['cellY']}, spanX={r['spanX']}, "
                f"spanY={r['spanY']}, itemType=0, intent={r['intent']}")
        return "\n".join(lines)

    def read(self, client, phone_id, cmd):
        self.commands.append(cmd)
        if self.breaks:
            raise RuntimeError("the phone dropped")
        if cmd.startswith("content query"):
            return self._print()
        if "resolve-activity" in cmd:
            package = cmd.rsplit(" ", 1)[-1]
            found = self.installed.get(package)
            return f"priority=0\n{found}\n" if found else "No activity found\n"
        if cmd.startswith("ls /data/user/0/com.android.launcher3/databases"):
            return f"app_icons.db\nlauncher_{self.grid}.db\n"
        return ""

    def run(self, client, phone_id, cmd, **kw):
        self.commands.append(cmd)
        assert cmd.startswith("content insert")
        if not self.takes:
            return "EXIT=0"
        grab = lambda name: int(re.search(rf"--bind {name}:i:(-?\d+)", cmd).group(1))  # noqa: E731
        intent = re.search(r"--bind intent:s:'([^']*)'", cmd).group(1)
        item = {"container": grab("container"), "screen": grab("screen"),
                "cellX": grab("cellX"), "cellY": grab("cellY"),
                "spanX": grab("spanX"), "spanY": grab("spanY"),
                "intent": intent}
        first = min(r["screen"] for r in self.rows if r["container"] == -100)
        if item["screen"] == first and item["cellY"] == 0:
            return ""                       # the search bar's row: thrown out
        self.rows.append(item)
        return ""

    def inserts(self) -> list[str]:
        return [c for c in self.commands if c.startswith("content insert")]


def test_all_three_land_in_a_row_and_a_second_run_adds_nothing(monkeypatch):
    phone = Phone(monkeypatch)
    assert hs.pin(None, "1", THREE, serial="5194") == [
        "com.openai.chatgpt", "com.anthropic.claude", "com.spotify.music"]
    assert len(phone.inserts()) == 3
    cells = [(int(re.search(r"cellX:i:(\d+)", c).group(1)),
              int(re.search(r"cellY:i:(\d+)", c).group(1)))
             for c in phone.inserts()]
    assert cells == [(0, 1), (1, 1), (2, 1)]
    titles = [re.search(r"title:s:'([^']*)'", c).group(1) for c in phone.inserts()]
    assert titles == ["ChatGPT", "Claude", "Spotify"]
    before = len(phone.inserts())
    assert hs.pin(None, "1", THREE, serial="5194") == []
    assert len(phone.inserts()) == before


def test_an_app_already_on_a_home_screen_or_the_dock_is_left_alone(monkeypatch):
    dock = _item(0, 0, container=hs.HOTSEAT)
    dock["intent"] = f"{_LAUNCH}component=com.openai.chatgpt/.MainActivity;end"
    phone = Phone(monkeypatch, rows=hs.parse(REAL) + [dock])
    assert hs.pin(None, "1", THREE) == ["com.anthropic.claude",
                                        "com.spotify.music"]
    assert len(phone.inserts()) == 2


def test_an_app_that_is_not_installed_gets_no_icon_and_the_rest_do(monkeypatch):
    phone = Phone(monkeypatch, installed={
        "com.openai.chatgpt": "com.openai.chatgpt/.MainActivity"})
    assert hs.pin(None, "1", THREE) == ["com.openai.chatgpt"]
    assert len(phone.inserts()) == 1
    assert hs.pin(None, "1", [CLAUDE, SPOTIFY]) == []
    assert len(phone.inserts()) == 1


def test_a_phone_with_no_table_or_no_room_is_left_as_it_was(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    phone = Phone(monkeypatch, rows=[])
    assert hs.pin(None, "1", THREE, serial="5194") == []
    assert phone.inserts() == [] and "no home screen" in caplog.text
    full = [_item(x, y) for x in range(4) for y in range(1, 4)]
    for row in full:
        row["intent"] = "#Intent;component=a.b/.C;end"
    phone = Phone(monkeypatch, rows=full)
    assert hs.pin(None, "1", THREE) == [] and phone.inserts() == []
    assert "no room" in caplog.text


def test_a_launcher_that_throws_the_icons_out_is_said_not_believed(
        monkeypatch, caplog):
    phone = Phone(monkeypatch, takes=False)
    assert hs.pin(None, "1", THREE, serial="5194") == []
    assert len(phone.inserts()) == 3
    assert "did not take an icon" in caplog.text


def test_a_phone_that_drops_mid_way_is_never_a_failed_build(monkeypatch, caplog):
    Phone(monkeypatch, breaks=True)
    assert hs.pin(None, "1", THREE, serial="5194") == []
    assert "could not add the icons" in caplog.text


def test_the_grid_is_read_from_the_launchers_database_name(monkeypatch):
    Phone(monkeypatch, grid="5_by_6")
    assert hs._grid(None, "1") == 5
    Phone(monkeypatch, grid="x")
    assert hs._grid(None, "1") == 0


# ================================================================ builder
class Recorder:
    def __init__(self, monkeypatch):
        self.calls: list = []
        monkeypatch.setattr(
            builder.kit_homescreen, "pin",
            lambda client, phone_id, apps, serial="": self.calls.append(
                (phone_id, apps, serial)) or [])


def _build(serial="5194"):
    return type("B", (), {"serial": serial})()


def test_the_build_pins_what_landed_in_the_farms_order(monkeypatch, make_settings):
    rec = Recorder(monkeypatch)
    settings = make_settings(target_package="com.openai.chatgpt")
    # ChatGPT was the app the wish named, so it heads `on`; the order the
    # icons take is the farm's own.
    builder._pin_icons(None, settings, _build(), "PH1",
                       ["chatgpt", "claude", "spotify"], remaining=lambda: 300,
                       cancelled=None)
    assert rec.calls == [("PH1", [("com.openai.chatgpt", "ChatGPT"),
                                  ("com.spotify.music", "Spotify"),
                                  ("com.anthropic.claude", "Claude")],
                          "5194")]


def test_an_app_that_did_not_install_gets_no_icon_call(monkeypatch,
                                                       make_settings):
    rec = Recorder(monkeypatch)
    builder._pin_icons(None, make_settings(target_package="com.openai.chatgpt"),
                       _build(), "PH1", ["chatgpt"], remaining=lambda: 300,
                       cancelled=None)
    assert [a for _, a, _ in rec.calls] == [[("com.openai.chatgpt", "ChatGPT")]]


def test_the_icons_wait_for_nothing_and_stop_for_a_cancelled_or_spent_build(
        monkeypatch, make_settings):
    rec = Recorder(monkeypatch)
    on = ["chatgpt", "claude"]
    builder._pin_icons(None, make_settings(home_screen_icons=False), _build(),
                       "PH1", on, remaining=lambda: 300, cancelled=None)
    builder._pin_icons(None, make_settings(), _build(), "PH1", on,
                       remaining=lambda: 300, cancelled=lambda: True)
    builder._pin_icons(None, make_settings(), _build(), "PH1", on,
                       remaining=lambda: builder.PIN_ICONS_SECONDS - 1,
                       cancelled=None)
    assert rec.calls == []


def test_the_icons_are_the_last_thing_the_install_does():
    import inspect

    source = inspect.getsource(builder._install_the_rest)
    assert source.index('build.app = "+".join(on)') < source.index("_pin_icons(")
    # and the setting is on by default, with a switch.
    from geelark_farm import config

    assert config.Settings.__dataclass_fields__["home_screen_icons"].default is True


def test_the_setting_reads_its_switch(make_settings, monkeypatch):
    from geelark_farm import config

    monkeypatch.setenv("HOME_SCREEN_ICONS", "0")
    assert config.Settings.load().home_screen_icons is False
    monkeypatch.setenv("HOME_SCREEN_ICONS", "1")
    assert config.Settings.load().home_screen_icons is True
    monkeypatch.delenv("HOME_SCREEN_ICONS")
    assert config.Settings.load().home_screen_icons is True


@pytest.mark.parametrize("word", ["geelark", "GeeLark"])
def test_nothing_the_module_says_names_the_vendor(word):
    import pathlib

    src = pathlib.Path(hs.__file__).read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]            # the docstring may say who
    assert word not in body
