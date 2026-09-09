"""Build artifacts in the store: the screens a build archived, readable
by a console on any host.

A build writes its screens and its outcome into a directory under
`artifact_dir` on the host it ran on. With builders on hosts of their
own the console cannot reach that directory, so a builder mirrors the
directory here when the build ends (`put_dir`), the console lists and
serves from here first (`folders`, `get`) and from the disk it can see
second, and the keeper prunes what is older than `KEEP_DAYS` (scale-out
step 3, 2026-09-10). Files past `MAX_BYTES` stay on disk only: a
screenshot is worth keeping, a video is not what this table is for.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Settings
from .db import connect

log = logging.getLogger(__name__)

#: Kept in the store this long; the disk copy has its own prune.
KEEP_DAYS = 7
#: Nothing larger goes in. A screenshot is a few hundred kilobytes.
MAX_BYTES = 2_000_000


def put_dir(settings: Settings, directory: Path, serial: str) -> int:
    """Mirror one build's directory. Returns how many files went in;
    never raises - the build is done whatever becomes of its pictures."""
    try:
        directory = Path(directory)
        if not directory.is_dir() or not serial:
            return 0
        files = [f for f in directory.iterdir()
                 if f.is_file() and f.stat().st_size <= MAX_BYTES]
        if not files:
            return 0
        with connect(settings) as conn:
            for f in files:
                conn.execute(
                    "INSERT INTO artifacts (serial, folder, name, content)"
                    " VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (folder, name) DO UPDATE SET"
                    "   content = EXCLUDED.content",
                    (str(serial), directory.name, f.name, f.read_bytes()))
            conn.commit()
        return len(files)
    except Exception as exc:                                       # noqa: BLE001
        log.warning("could not mirror %s into the store (%s)", directory, exc)
        return 0


def folders(settings: Settings, serial: str) -> list[dict]:
    """The archived folders of one phone, newest last: name, when, the
    outcome line, and the .xml names - the shape `read._archived` builds
    from the disk, so the two lists merge."""
    with connect(settings) as conn:
        cur = conn.execute(
            "SELECT folder, min(created_at),"
            " array_agg(name ORDER BY name) FILTER (WHERE name LIKE '%%.xml'),"
            " max(convert_from(content, 'UTF8'))"
            "   FILTER (WHERE name = 'outcome.txt')"
            " FROM artifacts WHERE serial = %s GROUP BY folder"
            " ORDER BY min(created_at)", (str(serial),))
        rows = cur.fetchall()
        conn.rollback()
    out = []
    for folder, when, names, outcome in rows:
        out.append({"folder": folder, "at": when,
                    "files": list(names or []),
                    "outcome": (outcome or "").strip().splitlines()[0]
                    if (outcome or "").strip() else ""})
    return out


def get(settings: Settings, serial: str, folder: str, name: str
        ) -> bytes | None:
    with connect(settings) as conn:
        cur = conn.execute(
            "SELECT content FROM artifacts"
            " WHERE serial = %s AND folder = %s AND name = %s",
            (str(serial), folder, name))
        row = cur.fetchone()
        conn.rollback()
    return bytes(row[0]) if row else None


def prune(settings: Settings, days: int = KEEP_DAYS) -> int:
    with connect(settings) as conn:
        cur = conn.execute(
            "DELETE FROM artifacts"
            " WHERE created_at < now() - make_interval(days => %s)", (days,))
        gone = cur.rowcount
        conn.commit()
    if gone:
        log.info("pruned %d archived screen(s) older than %d days from the "
                 "store", gone, days)
    return gone
