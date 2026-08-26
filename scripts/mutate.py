"""Change one thing in a source file and see whether the tests notice.

The only question worth asking about a test: if this line were wrong, would
anything say so? Coverage answers a weaker one - whether the line ran - and
the gap between them is where this project's quiet bugs have lived. `failures.py`
sat at 100% coverage with a rule nothing held.

    python scripts/mutate.py src/geelark_farm/pools.py tests/test_pools.py
    python scripts/mutate.py src/geelark_farm/proxy.py tests/

A survivor is a change to the source that no test objected to. Some are
harmless - a log threshold, a jitter bound, a loop iteration nothing can enter
- and some are a hole. The point is to be shown the list and to judge it.

Two rules learned the hard way:

**Run it against the whole suite before believing a number.** A module's own
test file is not the only thing that covers it: `gsheet` showed nine survivors
against `tests/test_gsheet.py` and five against `tests/`.

**Mutation below about 80% coverage is misleading.** An unexecuted line's
mutation always survives, so the list mixes "nothing asserts this" with
"nothing runs this" and stops being readable. Raise coverage first.

mutmut will not run on Windows and mutatest does not build here, which is why
this exists rather than a dependency.

Three hazards this has been bitten by, all handled below:

- **Stale bytecode.** CPython invalidates a .pyc on the source's size and its
  mtime truncated to whole seconds. This rewrites one file dozens of times a
  second, and `ast.unparse` output for opposite mutations of the same operator
  is very often the same length - so a run can execute bytecode compiled for
  the mutation before it. Two runs over one file disagreed, 23 survivors then
  21, and the quiet error is the dangerous one: a mutation reported as killed
  because stale, detectable bytecode ran in its place is a real hole the report
  says is covered.
- **No timeout.** A mutation that breaks a poll loop makes the tests wait out
  a real deadline. Bounded here, and reported as HUNG - which names a test that
  cannot report rather than a survivor. Those are worth fixing too: a suite
  that hangs tells CI nothing.
- **Restoring only in `finally`.** SIGTERM does not reach it, and what a kill
  left on disk was not the original with one thing changed - it was
  `ast.unparse` output, which has dropped every comment in the module.
"""
from __future__ import annotations

import ast
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

#: The child must not read or write .pyc files. See the note above.
CHILD_ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

#: Long enough for the slowest suite here, short enough that a mutation which
#: breaks a poll loop is reported rather than waited out.
RUN_TIMEOUT = 90

FLIP_CMP = {ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE, ast.LtE: ast.Gt,
            ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
            ast.In: ast.NotIn, ast.NotIn: ast.In,
            ast.Is: ast.IsNot, ast.IsNot: ast.Is}
FLIP_BOOL = {ast.And: ast.Or, ast.Or: ast.And}


def pytest_path() -> str:
    """The virtualenv's pytest, on either platform.

    Not the bare name: `pytest` off PATH may belong to another environment, and
    a mutation run against the wrong interpreter reports on code nobody is
    changing.
    """
    for candidate in (Path(".venv/Scripts/pytest.exe"),   # Windows
                      Path(".venv/bin/pytest")):          # macOS, Linux
        if candidate.exists():
            return str(candidate)
    return "pytest"


class Mutator(ast.NodeTransformer):
    """Applies exactly one change, chosen by index."""

    def __init__(self, wanted: int) -> None:
        self.wanted, self.seen, self.did = wanted, 0, ""

    def _take(self, what: str, line: int) -> bool:
        hit = self.seen == self.wanted
        if hit:
            self.did = f"line {line}: {what}"
        self.seen += 1
        return hit

    def visit_Compare(self, node):
        self.generic_visit(node)
        for i, op in enumerate(node.ops):
            other = FLIP_CMP.get(type(op))
            if other and self._take(f"{type(op).__name__} -> {other.__name__}",
                                    node.lineno):
                node.ops[i] = other()
        return node

    def visit_BoolOp(self, node):
        self.generic_visit(node)
        other = FLIP_BOOL.get(type(node.op))
        if other and self._take(f"{type(node.op).__name__} -> {other.__name__}",
                                node.lineno):
            node.op = other()
        return node

    def visit_UnaryOp(self, node):
        self.generic_visit(node)
        if isinstance(node.op, ast.Not) and self._take("dropped `not`",
                                                       node.lineno):
            return node.operand
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, bool):
            if self._take(f"{node.value} -> {not node.value}", node.lineno):
                return ast.copy_location(ast.Constant(value=not node.value),
                                         node)
        elif isinstance(node.value, int) and node.value in (0, 1):
            if self._take(f"{node.value} -> {node.value + 1}", node.lineno):
                return ast.copy_location(
                    ast.Constant(value=node.value + 1), node)
        return node


def count(source: str) -> int:
    """How many changes this knows how to make to `source`."""
    counter = Mutator(-1)
    counter.visit(ast.parse(source))
    return counter.seen


def mutate(source: str, index: int) -> tuple[str, str]:
    """`source` with change number `index` applied, and a name for it.

    Two mutations can share a name - `range(1, attempts + 1)` has two ones on
    the same line - so a name is a label, not an identity. Walk the indices
    when it matters which of them survived.
    """
    mutator = Mutator(index)
    tree = mutator.visit(ast.parse(source))
    ast.fix_missing_locations(tree)
    return ast.unparse(tree), mutator.did


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    target, *tests = sys.argv[1:]
    path = Path(target)
    original = path.read_text(encoding="utf-8")
    total = count(original)
    pytest = pytest_path()
    print(f"{total} mutations in {path.name}, tests: {' '.join(tests)}\n",
          flush=True)

    # Every cache that exists now was compiled from some other version of this
    # file. The env var stops new ones being written; these have to go, or they
    # will be read.
    for cache in Path("src").rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)

    def restore(*_) -> None:
        path.write_text(original, encoding="utf-8")

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: (restore(), sys.exit(130)))

    survivors: list[str] = []
    hangs: list[str] = []
    started = time.time()
    try:
        for index in range(total):
            changed, what = mutate(original, index)
            if changed == ast.unparse(ast.parse(original)):
                continue                       # nothing actually moved
            path.write_text(changed, encoding="utf-8")
            try:
                done = subprocess.run([pytest, "-qx", "--no-header", "-p",
                                       "no:cacheprovider", *tests],
                                      capture_output=True, text=True,
                                      timeout=RUN_TIMEOUT, env=CHILD_ENV)
            except subprocess.TimeoutExpired:
                # Not a survivor: the change was noticed, by a test that now
                # waits rather than failing. Worth its own line - it names a
                # test that would hang CI instead of reporting.
                hangs.append(what)
                print(f"  HUNG      {what}", flush=True)
                continue
            if done.returncode == 0:
                survivors.append(what)
                print(f"  SURVIVED  {what}", flush=True)
    finally:
        restore()

    took = time.time() - started
    print(f"\n{len(survivors)} of {total} survived, in {took:.0f}s", flush=True)
    if hangs:
        print(f"{len(hangs)} hung the suite rather than failing it", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
