"""The CapSolver door: an image challenge in, the tiles to tap out.

Only the recognition task, and on purpose. CapSolver has two shapes:

- a **token** task returns a `gRecaptchaResponse` string, which is the
  answer a *browser* posts back into the page's callback. The add-account
  flow is not a browser we can reach into - there is no `grecaptcha`
  object to hand a token to - so a token is useless here.
- a **classification** task takes the grid as an image and a word for what
  to find, and returns which tiles hold it. That is answerable by tapping,
  which is the only thing this tool can do to a phone.

So this module speaks classification (`ReCaptchaV2Classification`) and
nothing else. It answers synchronously - the recognition endpoints return
the solution on the create call - so there is no polling and no task id to
carry.

Never a hard dependency of a build: the caller checks `settings.capsolver_key`
first and treats a missing key, a refused key or an empty balance as "the
captcha stands", which is exactly what happened before this existed.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_BASE = "https://api.capsolver.com"

#: The object each of Google's grid questions asks for, by the id CapSolver
#: wants. Google shows the words ("Select all images with traffic lights");
#: the flow reads the words and this turns them into the id. Taken from
#: CapSolver's own table (docs.capsolver.com, ReCaptchaV2Classification).
QUESTION_IDS = {
    "taxis": "/m/0pg52",
    "bus": "/m/01bjv", "buses": "/m/01bjv",
    "school bus": "/m/02yvhj", "school buses": "/m/02yvhj",
    "motorcycles": "/m/04_sv", "motorcycle": "/m/04_sv",
    "tractors": "/m/013xlm",
    "chimneys": "/m/01jk_4",
    "crosswalks": "/m/014xcs", "crosswalk": "/m/014xcs",
    "traffic lights": "/m/015qff", "traffic light": "/m/015qff",
    "bicycles": "/m/0199g", "bicycle": "/m/0199g",
    "parking meters": "/m/015qbp", "parking meter": "/m/015qbp",
    "cars": "/m/0k4j", "car": "/m/0k4j",
    "bridges": "/m/015kr", "bridge": "/m/015kr",
    "boats": "/m/019jd", "boat": "/m/019jd",
    "palm trees": "/m/0cdl1", "palm tree": "/m/0cdl1",
    "mountains or hills": "/m/09d_r", "mountains": "/m/09d_r",
    "hills": "/m/09d_r", "mountain": "/m/09d_r",
    "fire hydrants": "/m/01pns0", "fire hydrant": "/m/01pns0",
    "stairs": "/m/01lynh",
}


class CapError(Exception):
    """The solver could not answer, with the reason a person would want:
    a refused key, an empty balance, an object we have no id for, a network
    that did not answer. The caller turns any of these back into
    `captcha_shown` - the captcha simply stands, as it did before."""


def question_id(text: str) -> str:
    """The CapSolver id for what a grid asks for, or "" if we have none.

    The instruction reads "Select all images with a **bus**" or "...with
    **traffic lights**"; the noun is what matters and this finds the
    longest known phrase inside it, so "traffic lights" wins over a stray
    "light" elsewhere on the screen.
    """
    low = " ".join((text or "").lower().split())
    best = ""
    for phrase in QUESTION_IDS:
        if phrase in low and len(phrase) > len(best):
            best = phrase
    return QUESTION_IDS.get(best, "")


def _post(key: str, path: str, payload: dict, *, session=None,
          timeout: float = 90, tries: int = 3, watch=None) -> dict:
    """One call, retried twice. The farm reaches api.capsolver.com over a
    link that is neither quick nor reliable: a 40-second read timed out
    with the grid already in hand (phone 1788), a createTask came back 520
    (phone 1811), and one attempt was reset by the peer and then timed out
    at 90 seconds (phone 1815). A captcha is worth waiting for - the
    alternative is a phone thrown away - and each of those failures cost a
    whole build.
    """
    import requests

    body = {"clientKey": key, **payload}
    last: Exception | None = None
    for attempt in range(max(1, tries)):
        if watch is not None:
            # Between the tries, which is where the waiting is: three at
            # ninety seconds is four and a half minutes in one call, and
            # somebody who pressed Cancel is watching all of it.
            watch()
        try:
            get = session or requests
            resp = get.post(f"{_BASE}{path}", json=body, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:                                   # noqa: BLE001
            last = exc
            if attempt + 1 < max(1, tries):
                log.warning("CapSolver %s did not answer (%s); trying again",
                            path, exc)
    else:
        raise CapError(f"CapSolver {path} did not answer ({last})") from last
    if data.get("errorId"):
        raise CapError(data.get("errorDescription")
                       or data.get("errorCode") or "CapSolver refused")
    return data


def balance(key: str, *, session=None) -> float:
    """Dollars left on the key. For the console to show, and for a caller
    that wants to say "the solver is out" rather than fail each build."""
    data = _post(key, "/getBalance", {}, session=session, timeout=15)
    return float(data.get("balance") or 0.0)


def solve_grid(key: str, image_b64: str, question: str, *,
               website_url: str = "", session=None,
               watch=None) -> tuple[list[int], int]:
    """Which tiles of a grid hold the thing asked for, and how wide the
    solver read the grid to be.

    `question` is the word off the screen ("traffic lights"), not the id -
    this maps it. A word we have no id for is a `CapError`, because sending
    the grid with no question wastes the key and answers nothing.

    The tiles are returned in no promised order; the caller taps each.
    An empty list is a real answer - "none of them" is a page with a
    Verify/Skip button and no tiles to press - and is not an error.

    The width comes back in the answer (`size`), and it is the answer's own
    account of the grid it just read: the indices only mean anything against
    it. Guessing it from the wording instead - "squares" for sixteen,
    "images" for nine - is a guess about a picture we are holding, and a
    wrong one puts every tap in the wrong place. `0` means the solver did
    not say, and the caller falls back to the wording.
    """
    qid = question_id(question)
    if not qid:
        raise CapError(f"no CapSolver category matches {question!r}")
    data = _post(key, "/createTask", {"task": {
        "type": "ReCaptchaV2Classification",
        "image": image_b64,
        "question": qid,
        **({"websiteURL": website_url} if website_url else {}),
    }}, session=session, watch=watch)
    solution = data.get("solution") or {}
    log.info("CapSolver answered %s for %r", solution, question)
    if solution.get("type") == "single":
        return ([0] if solution.get("hasObject") else []), 1
    objects = solution.get("objects") or []
    size = solution.get("size") or 0
    return [int(i) for i in objects], (int(size) if size in (3, 4) else 0)
