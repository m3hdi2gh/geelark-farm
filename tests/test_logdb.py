"""Log capture (C8): the process's own lines, batched into the store.

What these pin is the part that would fail quietly or expensively: a
line that never reaches the table, a capture that stalls a build, and a
dead cluster that costs a warning per second instead of one.
"""

from __future__ import annotations

import logging

import pytest

from geelark_farm.store import logdb


class FakeConn:
    """Records executemany rows; raises when told the cluster is down."""

    def __init__(self, sink, *, down=False):
        self.sink, self.down = sink, down
        self.committed = 0

    def __enter__(self):
        if self.down:
            raise ConnectionError("cluster unreachable")
        return self

    def __exit__(self, *exc):
        return None

    def cursor(self):
        return self

    def executemany(self, sql, rows):
        assert "INSERT INTO logs" in sql
        self.sink.extend(rows)

    def execute(self, sql, params=None):
        self.sink.append(("sql", " ".join(sql.split()), params))

    def commit(self):
        self.committed += 1


@pytest.fixture(autouse=True)
def root_at_debug():
    """serve sets the root to DEBUG; pytest leaves it at WARNING, which
    would stop every INFO line before any handler saw it."""
    root = logging.getLogger()
    was = root.level
    root.setLevel(logging.DEBUG)
    try:
        yield
    finally:
        root.setLevel(was)


@pytest.fixture
def capture(make_settings):
    """A Capture with no thread: tests flush by hand."""
    rows = []
    settings = make_settings(store_enabled=True, log_db=True)
    made = logdb.Capture(settings, connect=lambda: FakeConn(rows))
    logging.getLogger().addHandler(made.handler)
    try:
        yield made, rows
    finally:
        logging.getLogger().removeHandler(made.handler)


def test_a_logged_line_becomes_one_row_with_its_context(capture):
    from geelark_farm import builder

    made, rows = capture
    log = logging.getLogger("geelark_farm.builder")
    # The run and the build come off the worker's context, exactly as
    # they reach the file - not off `extra`, which the filter overwrites.
    run_token, build_token = builder._run.set("r8"), builder._build.set(1)
    try:
        log.info("signing into the app as %s", "a@x.com",
                 extra={"serial": "1533", "warm": 4})
        log.debug("not captured: DEBUG")
    finally:
        builder._run.reset(run_token)
        builder._build.reset(build_token)

    made.flush_now()

    assert len(rows) == 1
    at, level, logger, run, build, serial, machine, msg, extra = rows[0]
    assert level == "INFO" and logger == "geelark_farm.builder"
    assert msg == "signing into the app as a@x.com"
    assert (run, build, serial) == ("r8", "1", "1533")
    assert '"warm": 4' in extra
    assert made.written == 1


def test_no_build_context_is_an_empty_column_not_a_dash(capture):
    made, rows = capture
    logging.getLogger("geelark_farm.serve").info("5 warm of 5")
    made.flush_now()
    assert rows[0][3] == "" and rows[0][4] == ""


def test_the_capture_never_records_its_own_lines(capture):
    made, rows = capture
    logging.getLogger("geelark_farm.store.logdb").warning("about me")
    made.flush_now()
    assert rows == []


def test_a_dead_cluster_switches_the_capture_off_with_one_warning(
        make_settings, caplog):
    settings = make_settings(store_enabled=True, log_db=True)
    made = logdb.Capture(settings, connect=lambda: FakeConn([], down=True))
    root = logging.getLogger()
    root.addHandler(made.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="geelark_farm.store"):
            for i in range(10):
                logging.getLogger("geelark_farm.serve").info("line %d", i)
                made.flush_now()
    finally:
        root.removeHandler(made.handler)

    assert made.disabled is True
    assert made.handler not in root.handlers, "took itself off the root"
    off = [r for r in caplog.records if "switched itself off" in r.getMessage()]
    assert len(off) == 1
    assert made.failures == logdb.FAILURES_BEFORE_OFF


def test_a_full_queue_drops_and_counts_rather_than_blocking(make_settings,
                                                            monkeypatch):
    monkeypatch.setattr(logdb, "QUEUE_SIZE", 2)
    settings = make_settings(store_enabled=True, log_db=True)
    made = logdb.Capture(settings, connect=lambda: FakeConn([]))
    root = logging.getLogger()
    root.addHandler(made.handler)
    try:
        for i in range(5):
            logging.getLogger("geelark_farm.serve").info("line %d", i)
    finally:
        root.removeHandler(made.handler)

    assert made.queue.qsize() == 2
    assert made.dropped >= 2, "the rest were dropped, not waited on"


def test_pruning_keeps_thirty_days(capture):
    made, rows = capture
    made._prune()
    sql = [r for r in rows if r[0] == "sql"]
    assert sql and "DELETE FROM logs" in sql[0][1]
    assert sql[0][2] == (logdb.KEEP_DAYS,)


def test_install_is_a_no_op_unless_both_flags_are_on(make_settings):
    assert logdb.install(make_settings(store_enabled=True)) is None
    assert logdb.install(make_settings(log_db=True)) is None


def test_install_keeps_the_capture_where_the_logs_page_can_read_it(
        make_settings, monkeypatch):
    """The page says 'capture on · N written · N dropped' off the Capture
    install started - so install must keep it, and health must read it."""
    monkeypatch.setattr(logdb, "CURRENT", None)
    assert logdb.health() is None, "nothing installed, nothing to say"
    monkeypatch.setattr(logdb.Capture, "start", lambda self: self)
    made = logdb.install(make_settings(store_enabled=True, log_db=True))
    assert made is not None and logdb.CURRENT is made
    made.written, made.dropped = 31204, 2
    assert logdb.health() == {"on": True, "written": 31204, "dropped": 2,
                              "off_at": None, "off_why": ""}
    made._switch_off("3 failed flushes in a row: cluster unreachable")
    said = logdb.health()
    assert said["on"] is False and said["off_at"] is not None
    assert said["off_why"].startswith("3 failed flushes")
