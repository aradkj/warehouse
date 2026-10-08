"""Dependency-boundary tests: pure tests stay pure.

A unit test that drags SQLAlchemy, FastAPI and a driver into the interpreter is not
a unit test: it cannot run without the persistence stack installed, it cannot be run
in isolation while debugging a domain rule, and it quietly turns "the domain has no
infrastructure dependency" from a fact into a claim.

These tests check the boundary by *importing* ``tests.conftest`` in a fresh
subprocess and inspecting ``sys.modules`` - an assertion about what really happens,
not about what a file looks like.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Top-level modules a pure test run must never pull in.
INFRASTRUCTURE_MODULES = (
    "sqlalchemy",
    "fastapi",
    "httpx",
    "asyncpg",
    "alembic",
    "starlette",
    "redis",
)

PROBE = """
import sys

import tests.conftest  # noqa: F401  (importing it is the whole point)

loaded = {{name.split(".")[0] for name in sys.modules}}
forbidden = sorted(set({forbidden!r}) & loaded)
if forbidden:
    raise SystemExit("conftest imported infrastructure: " + ", ".join(forbidden))
"""


def test_the_root_conftest_does_not_import_infrastructure() -> None:
    result = subprocess.run(
        [sys.executable, "-c", PROBE.format(forbidden=INFRASTRUCTURE_MODULES)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def imported_modules(path: Path) -> set[str]:
    """Top-level modules a file actually imports (AST, so prose never counts)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_unit_tests_do_not_import_http_or_database_libraries() -> None:
    """``tests/unit`` may only import the domain, the standard library and pytest."""
    offenders: list[str] = []
    for path in sorted((PROJECT_ROOT / "tests" / "unit").rglob("*.py")):
        for module in sorted(imported_modules(path) & set(INFRASTRUCTURE_MODULES)):
            offenders.append(f"{path.relative_to(PROJECT_ROOT)} -> {module}")
    assert not offenders, "domain tests must not import infrastructure:\n" + "\n".join(offenders)


def test_application_tests_stay_off_postgresql() -> None:
    """The application suite runs on fakes; a real session there is a design smell."""
    forbidden = {"sqlalchemy", "asyncpg", "fastapi", "httpx", "alembic"}
    offenders: list[str] = []
    for path in sorted((PROJECT_ROOT / "tests" / "application").rglob("*.py")):
        for module in sorted(imported_modules(path) & forbidden):
            offenders.append(f"{path.relative_to(PROJECT_ROOT)} -> {module}")
    assert not offenders, "application tests must not depend on infrastructure:\n" + "\n".join(
        offenders
    )
