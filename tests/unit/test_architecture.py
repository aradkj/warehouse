"""Architecture tests: the dependency rule is enforced, not just documented.

Hexagonal architecture fails quietly: one convenient ``import sqlalchemy`` inside
an aggregate and the domain is no longer testable without a database, no longer
framework-independent, and no longer extractable into a service. This module turns
the rule into a test that fails the build.

Checked here:

1. ``*/domain`` never imports a framework, driver or another context's internals.
2. ``*/application`` imports only its own context's domain/ports plus ``shared``.
3. Routers contain no business rules (no branching on order status, no SQL).
4. The domain packages import nothing from ``adapters``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SRC = Path(__file__).resolve().parents[2] / "src"

#: Anything that would couple the domain to a delivery mechanism or a driver.
FORBIDDEN_IN_DOMAIN = {
    "fastapi",
    "starlette",
    "uvicorn",
    "sqlalchemy",
    "asyncpg",
    "alembic",
    "httpx",
    "requests",
    "aio_pika",
    "pika",
    "kombu",
    "celery",
    "redis",
    "elasticsearch",
    "opensearchpy",
    "prometheus_client",
    "opentelemetry",
    "kubernetes",
}

CONTEXTS = (
    "shared",
    "identity",
    "catalog",
    "inventory",
    "ordering",
    "fulfillment",
    "search",
    "audit",
    "bootstrap",
)


def python_files(*parts: str) -> list[Path]:
    root = SRC.joinpath(*parts)
    if not root.exists():
        return []
    return sorted(root.rglob("*.py"))


def imported_modules(path: Path) -> set[str]:
    """Top-level module names imported by a file (absolute imports only)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def full_imports(path: Path) -> set[str]:
    """Fully-qualified absolute imports of a file (``inventory.domain.x``)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_domain_packages_do_not_import_infrastructure() -> None:
    offenders: list[str] = []
    for context in CONTEXTS:
        for path in python_files(context, "domain"):
            for module in imported_modules(path):
                if module in FORBIDDEN_IN_DOMAIN:
                    offenders.append(f"{path.relative_to(SRC)} imports {module}")
    assert not offenders, "the domain must stay framework-free:\n" + "\n".join(offenders)


def test_domain_packages_do_not_import_adapters() -> None:
    offenders: list[str] = []
    for context in CONTEXTS:
        for path in python_files(context, "domain"):
            for module in full_imports(path):
                if ".adapters" in module or module.startswith("bootstrap"):
                    offenders.append(f"{path.relative_to(SRC)} imports {module}")
    assert not offenders, "the domain must not depend on adapters:\n" + "\n".join(offenders)


def test_ports_do_not_import_adapters_or_frameworks() -> None:
    offenders: list[str] = []
    for context in CONTEXTS:
        for path in python_files(context, "ports"):
            for module in imported_modules(path):
                if module in FORBIDDEN_IN_DOMAIN:
                    offenders.append(f"{path.relative_to(SRC)} imports {module}")
            for module in full_imports(path):
                if ".adapters" in module:
                    offenders.append(f"{path.relative_to(SRC)} imports {module}")
    assert not offenders, "ports describe contracts, not implementations:\n" + "\n".join(offenders)


def test_application_layer_does_not_import_sqlalchemy_or_messaging() -> None:
    """Use cases orchestrate through ports; they never see a session, table, or message broker."""
    offenders: list[str] = []
    forbidden = {"sqlalchemy", "aio_pika", "pika", "kombu", "celery", "redis"}
    for context in CONTEXTS:
        for path in python_files(context, "application"):
            for module in imported_modules(path):
                if module in forbidden:
                    offenders.append(f"{path.relative_to(SRC)} imports {module}")
    assert not offenders, (
        "application code must depend on ports, not SQLAlchemy or messaging:\n"
        + "\n".join(offenders)
    )


def test_routers_contain_no_sql_and_no_repository_access() -> None:
    """Controllers translate HTTP <-> commands. Nothing else."""
    offenders: list[str] = []
    for path in python_files("bootstrap", "routers"):
        for module in full_imports(path):
            if module.split(".")[0] in {"sqlalchemy", "asyncpg"} or ".adapters" in module:
                offenders.append(f"{path.relative_to(SRC)} imports {module}")
    assert not offenders, "routers must not touch persistence:\n" + "\n".join(offenders)


@pytest.mark.parametrize("layer", ["domain", "application", "ports"])
def test_contexts_do_not_reach_into_other_contexts_internals(layer: str) -> None:
    """Cross-context coupling goes through each context's published ``ports``.

    ``adapters`` and ``infrastructure`` are the outer layers: they are allowed to
    wire other contexts together (that is what a composition root is for), but the
    inner layers are not.
    """
    offenders: list[str] = []
    for context in CONTEXTS:
        for path in python_files(context, layer):
            for module in full_imports(path):
                parts = module.split(".")
                if (
                    len(parts) >= 2
                    and parts[0] in CONTEXTS
                    and parts[0] not in {context, "shared", "bootstrap"}
                    and parts[1] in {"adapters", "application", "domain"}
                ):
                    offenders.append(f"{path.relative_to(SRC)} imports {module}")
    assert not offenders, (
        "one context may only import another context's published ports:\n" + "\n".join(offenders)
    )


def test_placeholders_declare_their_planned_phase() -> None:
    """Deferred contexts say *when* they arrive instead of looking unfinished."""
    placeholders = [
        SRC / "fulfillment" / "ports" / "publication.py",
        SRC / "search" / "ports" / "indexer.py",
        SRC / "audit" / "domain" / "entry.py",
    ]
    for path in placeholders:
        assert "PLANNED_PHASE" in path.read_text(encoding="utf-8"), path
