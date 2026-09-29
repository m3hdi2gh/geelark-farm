"""The 2Captcha door: a grid and its question in, the tiles to tap out.

Only the `GridTask`, and on purpose. 2Captcha's other shapes hand back a
token for a browser's `grecaptcha` callback, which the add-account flow on
a phone has no way to use (see capsolver.py for the same argument). The
grid task is answered by people: the picture and the words off the screen
go to a worker, who sends back the numbers of the tiles that hold the
thing asked for. A person is slower than CapSolver's model - ten to forty
seconds against one to three - and costs more per picture, which is the
point of having it: it is the door for a grid the model gets wrong.

The task is asynchronous on their side. `createTask` gives a task id, and
`getTaskResult` says `processing` until a worker has answered. The docs ask
for five seconds before the first poll and five to ten between polls.

Never a hard dependency of a build: the caller (solvers.py) treats a
missing key, a refused key, an empty balance or no worker free as "this
door did not answer" and tries the next one, or leaves the captcha standing.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

_BASE = "https://api.2captcha.com"

#: Seconds before the first poll, and between polls - what the docs ask.
POLL_SECONDS = 5.0
#: How long one grid may wait for a worker before the door gives up. A
#: 3x3 refreshes its tiles as they are answered, so one captcha is several
#: grids in a row; two minutes each is the outer bound, not the norm.
WAIT_SECONDS = 120.0


class TwoCapError(Exception):
    """The door could not answer, with the reason a person would want: a
    refused key, an empty balance, no worker free, the workers gave up, a
    network that did not answer. The caller decides what to do next."""


def _post(key: str, path: str, payload: dict, *, session=None,
          timeout: float = 30, tries: int = 3) -> dict:
    """One call, retried on transport trouble; a refusal is raised once.

    The same shape as CapSolver's door: a fresh connection each try, a
    modest timeout, and a hang counted as one failed try rather than
    waited out."""
    import requests

    body = {"clientKey": key, **payload}
    last: Exception | None = None
    for attempt in range(max(1, tries)):
        try:
            get = session or requests
            resp = get.post(f"{_BASE}{path}", json=body, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:                                   # noqa: BLE001
            last = exc
            if attempt + 1 < max(1, tries):
                log.warning("2Captcha %s did not answer (%s); trying again",
                            path, exc)
    else:
        raise TwoCapError(f"2Captcha {path} did not answer ({last})") from last
    if data.get("errorId"):
        raise TwoCapError(data.get("errorDescription")
                          or data.get("errorCode") or "2Captcha refused")
    return data


def balance(key: str, *, session=None) -> float:
    """Dollars left on the key."""
    data = _post(key, "/getBalance", {}, session=session, timeout=15)
    return float(data.get("balance") or 0.0)


def comment_of(instruction: str) -> str:
    """The words a worker sees, off the screen and trimmed to the ask.

    Google's instruction runs on past the question ("Select all images
    with crosswalks. Click verify once there are none left."); the worker
    wants the first sentence, whitespace folded, and not a paragraph."""
    text = " ".join((instruction or "").split())
    for stop in (". ", "\n"):
        if stop in text:
            text = text.split(stop, 1)[0].strip()
    return text.rstrip(".")[:140] or "Select the tiles that match"


def solve_grid(key: str, image_b64: str, question: str, *, size: int,
               session=None, watch=None, sleep=time.sleep,
               wait_seconds: float = WAIT_SECONDS) -> tuple[list[int], int]:
    """Which tiles of a `size` x `size` grid hold the thing asked for.

    The answer is 0-based, left to right, top to bottom - 2Captcha numbers
    its tiles from 1 and this takes one off, so the caller reads it exactly
    as CapSolver's. The width comes back as it went in: a worker is told
    the shape, not asked for it. An empty list is a real answer ("none of
    them"), which `canNoAnswer` lets the worker say.

    `watch` is called between polls, so a Cancel pressed while a worker
    is looking at the picture is felt within one poll."""
    if size not in (3, 4):
        raise TwoCapError(f"a grid is 3 or 4 wide, not {size}")
    data = _post(key, "/createTask", {"task": {
        "type": "GridTask",
        "body": image_b64,
        "comment": comment_of(question),
        "rows": size,
        "columns": size,
        "canNoAnswer": 1,
    }}, session=session)
    task_id = data.get("taskId")
    if not task_id:
        raise TwoCapError("2Captcha took the grid but gave no task id")
    log.info("2Captcha took grid task %s for %r", task_id, comment_of(question))
    deadline = time.monotonic() + wait_seconds
    while True:
        sleep(POLL_SECONDS)
        if watch is not None:
            watch()
        data = _post(key, "/getTaskResult", {"taskId": task_id},
                     session=session)
        if data.get("status") == "ready":
            break
        if time.monotonic() > deadline:
            raise TwoCapError(f"2Captcha task {task_id} was not answered "
                              f"within {wait_seconds:.0f}s")
    clicks = (data.get("solution") or {}).get("click") or []
    tiles = sorted({int(c) - 1 for c in clicks if 1 <= int(c) <= size * size})
    log.info("2Captcha answered tiles %s of a %dx%d grid (cost %s)",
             tiles, size, size, data.get("cost"))
    return tiles, size
