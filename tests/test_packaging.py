"""What the package declares, checked against what it imports.

A dependency list drifts in both directions and neither shows up in a test
run. An import nobody declared works on every machine that already has the
package and fails on the first fresh clone - and the suite cannot catch it,
because the suite runs on a machine that has it. A declaration nothing imports
is quieter still: `mutmut` sat in the dev extras for months, was installed on
every CI run on both Python versions, dragged libcst and textual along, and
exits 1 on Windows - the only platform this is developed on - so it had never
once been run (2026-08-27).
"""

from __future__ import annotations

import ast
import re
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def normalised(name: str) -> str:
    """PEP 503: `google-auth`, `Google_Auth` and `google.auth` are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirements(table: str) -> set[str]:
    """One dependency table's package names, without version specifiers.

    Read from the table rather than from the file, so a name that appears in
    a comment explaining why it is *not* a dependency does not count as one.
    """
    block = re.search(rf"^{table} = \[(.*?)^\]", PYPROJECT, re.M | re.S)
    assert block, f"the {table} table stopped looking like this"
    return {normalised(re.match(r"[A-Za-z0-9._-]+", line).group())
            for line in re.findall(r'"([^"]+)"', block.group(1))}


def declared() -> set[str]:
    return requirements("dependencies")


def imported() -> set[str]:
    """Every top-level name `src/` imports that the standard library does not
    provide. Local modules are relative imports, so they never appear here."""
    import sys

    names: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names.add((node.module or "").split(".")[0])
    return {name for name in names
            if name and name not in sys.stdlib_module_names
            and name != "geelark_farm"}


def distributions_for(module: str) -> set[str]:
    return {normalised(dist)
            for dist in packages_distributions().get(module, [])}


def test_every_package_the_code_imports_is_declared():
    """Otherwise it installs on this machine and not on the next one."""
    missing = [module for module in sorted(imported())
               if not distributions_for(module) & declared()]

    assert not missing, f"imported by src/ but not in dependencies: {missing}"


def test_every_declared_dependency_is_actually_imported():
    """The other direction, which nothing else would ever notice."""
    used = {dist for module in imported() for dist in distributions_for(module)}
    unused = sorted(declared() - used)

    assert not unused, f"declared but imported nowhere in src/: {unused}"


def test_mutation_testing_is_a_script_here_and_not_a_dependency():
    """The harness is `scripts/mutate.py` precisely because the packaged tools
    do not run on this platform, so a dependency claiming to do that job is
    one nobody can use."""
    assert (ROOT / "scripts" / "mutate.py").exists()
    assert "mutmut" not in requirements("dev")
