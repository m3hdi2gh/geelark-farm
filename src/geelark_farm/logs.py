"""The log file, written so a machine can count it.

Text is what a person reads while a run is going, and that is what the console
handler is for. The file is a different job: it is the record somebody opens
weeks later, on a machine they are not sitting at, to find out what happened -
and increasingly it is what something else reads to decide whether to raise an
alarm. `grep -c` is a poor instrument for that, and a line that has been
rephrased breaks whatever was counting it.

So the file can be JSON, one object per line, and the console stays prose.
`LOG_FORMAT=json` turns it on; the default is the text format the file has
always had, because that file is read by hand on the laptop today and this
should not make that worse.

Every line carries which machine and which commit wrote it. That is two fields
rather than a header, because a log gets tailed, rotated, and concatenated
with another machine's, and each of those loses a header.

Anything a caller passes as `extra=` lands in the object beside the message,
which is the point: `log.info("...", extra={"warm": 4})` is a number something
can graph, and the same line still reads as a sentence on the console.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .config import machine, revision

#: Everything the logging module itself puts on a record. Derived from a real
#: record rather than written out, so a new attribute in some future Python
#: does not start turning up as though a caller had passed it.
RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime", "message", "taskName",
}

#: What the build-context filter stamps on a record when no build is running.
#: `[-]` reads correctly in the text format; in JSON it is a field on every
#: line that says nothing, so it is left out there. Named here rather than
#: written twice, so the two ends cannot drift apart.
NO_BUILD = "-"

#: What `LOG_FORMAT` may be.
FORMATS = ("text", "json")

#: The text the file has always used. Here so both formats live in one place.
TEXT_FORMAT = ("%(asctime)s %(levelname)s [%(run)s/%(row)s] "
               "%(name)s: %(message)s")


class JsonLines(logging.Formatter):
    """One JSON object per line."""

    def __init__(self) -> None:
        super().__init__()
        # Read once. Both are constant for the life of the process, and
        # `revision` shells out to git the first time it is asked.
        self.machine = machine()
        self.revision = revision()

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "t": datetime.fromtimestamp(
                record.created, tz=timezone.utc).isoformat(
                    timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "machine": self.machine,
        }
        if self.revision:
            payload["rev"] = self.revision

        for key, value in record.__dict__.items():
            # `row` is on every record whether or not a build is running.
            # No build is not a value worth a field on every line.
            if key in RESERVED or (key in ("row", "run", "build", "serial")
                                   and value in ("", NO_BUILD)):
                continue
            payload[key] = value

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # `default=str` so an extra that cannot be serialised costs its own
        # readability and not the whole line - losing a log line is how you
        # lose the record of the thing that went wrong.
        return json.dumps(payload, ensure_ascii=False, default=str)


def file_formatter(log_format: str) -> logging.Formatter:
    """The formatter the log file should use."""
    if log_format == "json":
        return JsonLines()
    return logging.Formatter(TEXT_FORMAT)
