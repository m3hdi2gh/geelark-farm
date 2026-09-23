"""The files that make up the builder, for the tests that scan its source.

builder.py is being split one leaf at a time (the builder review,
2026-09-23). A test that reads `builder.py` for every `finish("...")`,
every `Aborted("...")` or the absence of some pattern stops checking the
moment the code it was about moves to another file - and passes, which is
worse than failing. So the scans read these, file by file, and each
asserts it found something.

Add the module each step of the split creates. `test_boundaries` checks
the reverse: a verdict literal written outside these files and outside
flows/ is a builder module nobody added here.
"""
from __future__ import annotations

import importlib
import pathlib
from types import ModuleType

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "geelark_farm"

#: The builder and every module split out of it, as dotted names under
#: geelark_farm.
BUILDER_MODULES = ("builder", "products", "wishes", "runctx", "cancel",
                   "exit_health")


def builder_modules() -> list[ModuleType]:
    return [importlib.import_module(f"geelark_farm.{name}")
            for name in BUILDER_MODULES]


def builder_sources() -> list[pathlib.Path]:
    paths = [SRC / (name.replace(".", "/") + ".py") for name in BUILDER_MODULES]
    missing = [p for p in paths if not p.exists()]
    assert not missing, f"BUILDER_MODULES names files that are not there: {missing}"
    return paths


def builder_texts() -> list[tuple[pathlib.Path, str]]:
    """Each file and its text - scanned apart, never joined, so a pattern
    cannot match across the seam between two files."""
    return [(p, p.read_text(encoding="utf-8")) for p in builder_sources()]
