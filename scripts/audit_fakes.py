"""Check every monkeypatched fake against the thing it stands in for.

    python scripts/audit_fakes.py

Coverage says a line ran. Mutation says an assertion holds it. Neither can see
that the fake a test hands the code is a different shape from what the code is
handed in production, and that is the gap ten builds fell through on
2026-08-24: the fake answered with `router.Outcome`, the real function answers
with its own `Outcome`, and the builder read `.trail` off whatever it got.

This wraps `MonkeyPatch.setattr` for the length of a run and asks three
questions at each of the suite's several hundred patch sites:

  SIG      would the fake refuse a call the real one accepts
  TYPE     a plain value replaced by one of a different type
  RETURN   did the fake hand back something the real one's annotation forbids

RETURN is the one worth the trouble, and within it the line that matters is
`fake HAS but real lacks`. A fake carrying an attribute the real class has not
got is code the tests will bless and production will not run. The other
direction - a fake with less on it than the real thing - fails loudly the
moment the code reaches for the missing part, which is a bad afternoon rather
than a bad build.

Nothing here is a gate. Most of what it prints is a test being economical, and
telling those apart from the two or three that matter is the reading you have
to do yourself.
"""
from __future__ import annotations

import functools
import inspect
import sys
import types
import typing
from pathlib import Path

from _pytest.monkeypatch import NOTSET, MonkeyPatch, derive_importpath

#: kind, target, detail -> the tests that reached it.
FINDINGS: dict[tuple, dict] = {}

_original_setattr = MonkeyPatch.setattr
_where = "?"

#: Types whose method list says nothing. A str standing in for a Panel is
#: worth reporting; the forty string methods it brings with it are not.
BUILTIN = "builtins"


def record(kind: str, target: str, detail: str) -> None:
    entry = FINDINGS.setdefault(
        (kind, target, detail),
        {"kind": kind, "target": target, "detail": detail, "tests": set()})
    entry["tests"].add(_where)


def surface(cls) -> set[str]:
    """Every public name an instance of `cls` answers to.

    `dir()` alone is not that. A dataclass field with no default is an
    annotation and nothing more until an instance exists, so `dir(Entry)` does
    not list `phone_id` - and a fake carrying `phone_id` read as having
    something the real class lacked, which is backwards.
    """
    names = {name for name in dir(cls) if not name.startswith("_")}
    for klass in getattr(cls, "__mro__", [cls]):
        names |= {name for name in getattr(klass, "__annotations__", {})
                  if not name.startswith("_")}
    return names


def accepts(real, fake) -> str | None:
    """Whether the smallest call the real signature accepts reaches the fake."""
    try:
        real_signature = inspect.signature(real)
        fake_signature = inspect.signature(fake)
    except (TypeError, ValueError):
        return None
    args, kwargs = [], {}
    for name, param in real_signature.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.default is not param.empty:
            continue                       # optional: a fake may leave it out
        if param.kind is param.KEYWORD_ONLY:
            kwargs[name] = None
        else:
            args.append(None)
    try:
        fake_signature.bind(*args, **kwargs)
    except TypeError as exc:
        return f"{real_signature} -> fake{fake_signature}: {exc}"
    return None


def returns_wrong(real, result) -> str | None:
    """What the real one's return annotation says about this answer."""
    try:
        hints = typing.get_type_hints(real)
    except Exception:                      # a forward reference that will not resolve
        return None
    annotation = hints.get("return", inspect.Parameter.empty)
    if annotation is inspect.Parameter.empty:
        return None

    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        allowed = tuple(a for a in typing.get_args(annotation) if isinstance(a, type))
        if not allowed or isinstance(result, allowed):
            return None
        return (f"annotated -> {'|'.join(a.__qualname__ for a in allowed)}, "
                f"returned {type(result).__qualname__}")
    if origin is not None:                 # list[str], dict[str, int]
        annotation = origin
    if annotation is None or annotation is type(None):
        return (None if result is None else
                f"annotated -> None, returned {type(result).__qualname__}")
    if not isinstance(annotation, type) or annotation in (typing.Any, object):
        return None
    if isinstance(result, annotation):
        return None

    got = type(result)
    extra = ([] if got.__module__ == BUILTIN
             else sorted(surface(got) - surface(annotation)))
    missing = sorted(surface(annotation) - surface(got))
    detail = f"annotated -> {annotation.__qualname__}, returned {got.__qualname__}"
    if extra:
        detail += f" | fake HAS but real lacks: {extra}"
    if missing:
        detail += f" | real has but fake lacks: {missing[:6]}"
    return detail


def watched(real, fake, target: str):
    """The fake, reporting on what it answers with."""
    if not (inspect.isfunction(fake) or inspect.ismethod(fake)
            or isinstance(fake, functools.partial)):
        return fake

    @functools.wraps(fake)
    def wrapper(*args, **kwargs):
        result = fake(*args, **kwargs)
        wrong = returns_wrong(real, result)
        if wrong:
            record("RETURN", target, wrong)
        return result
    return wrapper


def audited(self, target, name, value=NOTSET, raising=True):
    if isinstance(target, str):
        # The string form: `setattr("a.b.c", replacement)`, so the second
        # argument is the value. derive_importpath answers (attr, module).
        attribute, holder = derive_importpath(target, raising)
        replacement, dotted = name, target
    else:
        holder, attribute, replacement = target, name, value
        dotted = f"{getattr(target, '__name__', target)}.{name}"

    real = getattr(holder, attribute, None)
    if real is None or not raising:
        return _original_setattr(self, target, name, value, raising)

    if callable(real) and callable(replacement) and not inspect.isclass(real):
        problem = accepts(real, replacement)
        if problem:
            record("SIG", dotted, problem)
        return _original_setattr(self, holder, attribute,
                                 watched(real, replacement, dotted), raising)

    if (not callable(real) and not callable(replacement)
            and type(real) is not type(replacement)):
        record("TYPE", dotted,
               f"{type(real).__name__} -> {type(replacement).__name__}")

    return _original_setattr(self, target, name, value, raising)


# ------------------------------------------------------------ pytest plugin
def pytest_configure(config):
    MonkeyPatch.setattr = audited


def pytest_runtest_setup(item):
    global _where
    _where = item.nodeid


def pytest_terminal_summary(terminalreporter):
    MonkeyPatch.setattr = _original_setattr
    write = terminalreporter.write_line
    write("")
    write("=" * 78)
    write(f"FAKE AUDIT: {len(FINDINGS)} finding(s)")
    write("=" * 78)
    for entry in sorted(FINDINGS.values(), key=lambda e: (e["kind"], e["target"])):
        write(f"[{entry['kind']:6}] {entry['target']}")
        write(f"           {entry['detail']}")
        tests = sorted(entry["tests"])
        write(f"           {len(tests)} test(s), e.g. {tests[0]}")
    if not FINDINGS:
        write("Every fake matches the shape of the thing it replaces.")


if __name__ == "__main__":
    import pytest

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(pytest.main(["-q", "-p", "audit_fakes", *sys.argv[1:]]))
