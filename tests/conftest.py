"""Test-wide fixtures: application, database, HTTP client, deterministic clock.

Two rules shape this file.

**Pure tests stay pure.** ``unit`` and ``application`` tests must run without
PostgreSQL, without FastAPI and without a driver installed. This module is imported
for *every* test in the suite, so its top-level imports are limited to the standard
library, pytest and the framework-free ``shared.domain`` package; anything heavier
(SQLAlchemy, httpx, the application factory) is imported inside the fixture that
needs it. ``tests/unit/test_dependency_boundary.py`` enforces that.

**Isolation is explicit.** Database-backed tests start from empty tables
(``TRUNCATE ... CASCADE``) through a dedicated engine, so a test module may safely
define its own fixture called ``database`` without disturbing the cleanup.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import pytest

from shared.application.context import ActorType, RequestContext
from shared.domain.clock import FixedClock
from tests.support.events import RecordingEventDispatcher

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from sqlalchemy.ext.asyncio import AsyncEngine

    from bootstrap.config import Settings

#: Markers whose tests talk to PostgreSQL.
DATABASE_MARKERS = frozenset(
    {"repository", "api", "integration", "concurrency", "outbox", "messaging", "celery", "inbox"}
)
#: Fixtures whose tests need PostgreSQL even without a marker.
DATABASE_FIXTURES = frozenset({"client", "http_client", "app", "engine", "database"})

DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://arad:arad@127.0.0.1:5432/arad_test"


def test_database_url() -> str:
    """URL of the database the suite is allowed to truncate."""
    return os.environ.get("ARAD_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


# Ensure any component or worker querying get_settings() during tests uses the test database
os.environ.setdefault("ARAD_DATABASE_URL", test_database_url())
os.environ.setdefault("ARAD_ENVIRONMENT", "test")


# ---------------------------------------------------------------------------
# framework-free fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def clock() -> FixedClock:
    """Deterministic clock for tests that need to control time explicitly."""
    return FixedClock()


@pytest.fixture(scope="session")
def event_dispatcher() -> RecordingEventDispatcher:
    """Records the domain events a use case would have published (Phase 1: none)."""
    return RecordingEventDispatcher()


# ---------------------------------------------------------------------------
# application fixtures (imported lazily: FastAPI and SQLAlchemy live here)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def settings() -> Settings:
    """Settings used by the application under test.

    Production defaults everywhere except the database target and log noise: a
    failure must reflect application behaviour, not test-only wiring.
    """
    from bootstrap.config import Settings as _Settings

    return _Settings(
        database_url=test_database_url(),
        log_level="WARNING",
        log_json=False,
        environment="test",
    )


@pytest.fixture(scope="session")
async def app(settings: Settings, event_dispatcher: Any) -> AsyncIterator[Any]:
    """The application under test, with its lifespan executed.

    ``httpx`` does not run ASGI lifespans, so the lifespan context is entered
    explicitly: that is what creates the engine, and testing an application whose
    engine was never started would hide exactly the failures this suite exists to
    catch.
    """
    from bootstrap.app import create_app
    from search.adapters.memory import InMemorySearchIndexer
    from shared.infrastructure.cache.memory import InMemoryCache

    application = create_app(
        settings=settings,
        event_dispatcher=event_dispatcher,
        cache=InMemoryCache(),
        search_indexer=InMemorySearchIndexer(),
    )
    async with application.router.lifespan_context(application):  # type: ignore[attr-defined]
        yield application


@pytest.fixture
async def client(app: Any) -> AsyncIterator[Any]:
    """Async HTTP client bound to the application (no network, no server)."""
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture(scope="session")
async def database() -> AsyncIterator[AsyncEngine]:
    """Session-scoped engine for setup and read-only verification queries.

    Test modules may shadow this name with their own fixture; nothing in this file
    depends on it (see ``_truncation_engine``).
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(test_database_url())
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
async def _truncation_engine() -> AsyncIterator[AsyncEngine]:
    """Dedicated engine for the per-test cleanup, immune to fixture shadowing."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(test_database_url())
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
async def _isolate_database(
    request: pytest.FixtureRequest, _truncation_engine: AsyncEngine
) -> AsyncIterator[None]:
    """Start every database-backed test from empty tables.

    Pure ``unit`` and ``application`` tests are skipped: they never touch
    PostgreSQL, truncating for them would only slow the suite down, and skipping
    keeps the framework-free rule visible (a unit test that suddenly needed a
    database would fail loudly instead of quietly passing).
    """
    markers = {marker.name for marker in request.node.iter_markers()}
    uses_database = bool(markers & DATABASE_MARKERS) or bool(
        DATABASE_FIXTURES & set(request.fixturenames)
    )
    if not uses_database:
        yield
        return

    from sqlalchemy import text

    import shared.infrastructure.db.registry  # noqa: F401  (registers every table)
    from shared.infrastructure.db.base import metadata

    table_names = ", ".join(
        f'"{table.name}"'
        for table in metadata.sorted_tables
        if table.name not in ("roles", "role_permissions")
    )
    async with _truncation_engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {table_names} CASCADE"))
        await connection.execute(text("DELETE FROM roles WHERE is_system = false"))
        res = await connection.execute(text("SELECT count(*) FROM roles WHERE is_system = true"))
        if res.scalar() == 0:
            await connection.execute(
                text(
                    """
                    INSERT INTO roles (id, tenant_id, name, description, is_system, version) VALUES
                    ('00000000-0000-0000-0000-000000000001', NULL, 'ADMIN',
                     'Full tenant administrative access', true, 1),
                    ('00000000-0000-0000-0000-000000000002', NULL, 'MEMBER',
                     'Standard tenant member', true, 1),
                    ('00000000-0000-0000-0000-000000000003', NULL, 'SUPPORT_AGENT',
                     'Support agent with restricted financial views', true, 1),
                    ('00000000-0000-0000-0000-000000000004', NULL, 'AUDITOR',
                     'Read-only auditor with access to audit logs', true, 1)
                    ON CONFLICT (id) DO NOTHING;
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO role_permissions (role_id, permission) VALUES
                    ('00000000-0000-0000-0000-000000000001', '*'),
                    ('00000000-0000-0000-0000-000000000002', 'orders.read'),
                    ('00000000-0000-0000-0000-000000000002', 'orders.create'),
                    ('00000000-0000-0000-0000-000000000002', 'orders.cancel'),
                    ('00000000-0000-0000-0000-000000000002', 'orders.confirm'),
                    ('00000000-0000-0000-0000-000000000002', 'orders.financials.read'),
                    ('00000000-0000-0000-0000-000000000002', 'inventory.read'),
                    ('00000000-0000-0000-0000-000000000002', 'catalog.products.read'),
                    ('00000000-0000-0000-0000-000000000003', 'orders.read'),
                    ('00000000-0000-0000-0000-000000000003', 'inventory.read'),
                    ('00000000-0000-0000-0000-000000000003', 'catalog.products.read'),
                    ('00000000-0000-0000-0000-000000000004', 'orders.read'),
                    ('00000000-0000-0000-0000-000000000004', 'orders.financials.read'),
                    ('00000000-0000-0000-0000-000000000004', 'inventory.read'),
                    ('00000000-0000-0000-0000-000000000004', 'catalog.products.read'),
                    ('00000000-0000-0000-0000-000000000004', 'audit.events.read')
                    ON CONFLICT DO NOTHING;
                    """
                )
            )
    yield


@pytest.fixture
def actor_context() -> RequestContext:
    """A plain, tenant-less request context for tests that build their own."""
    return RequestContext(actor_id="test-harness", actor_type=ActorType.SERVICE_ACCOUNT)
