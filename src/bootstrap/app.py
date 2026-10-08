"""FastAPI application factory.

The factory takes its dependencies as parameters (settings, event dispatcher),
which is what makes the test suite able to run the *same* application object
graph against a real PostgreSQL database instead of a mock.

Startup order matters and is explicit: configuration -> logging -> database engine
-> routes. Shutdown disposes the connection pool so that a container can stop
without leaving connections open (graceful shutdown, Phase 5 hardens it further).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from bootstrap.config import Settings, get_settings
from bootstrap.errors import register_exception_handlers
from bootstrap.logging import configure_logging
from bootstrap.middleware import (
    AccessLogMiddleware,
    CorrelationIdMiddleware,
    RequestSizeLimitMiddleware,
)
from bootstrap.routers import catalog, health, inventory, ordering, platform, search
from search.adapters.elasticsearch import ElasticsearchIndexAdapter
from search.ports.indexer import SearchIndexer
from shared.application.events import EventDispatcher, NullEventDispatcher
from shared.infrastructure.cache.redis import RedisCacheAdapter
from shared.infrastructure.db.session import Database
from shared.ports.cache import CachePort

API_VERSION = "0.1.0"

DESCRIPTION = """
Phase 1 of the Distributed Order and Inventory Orchestration Platform: a modular
monolith (hexagonal architecture) that coordinates catalog, inventory and orders
with PostgreSQL as the system of record.

Guarantees implemented in this phase:

* **no overselling** - stock is reserved under row-level locking inside
  PostgreSQL transactions, never in Redis or in application memory;
* **idempotent mutations** - `Idempotency-Key` is scoped to `(tenant, key)` and
  enforced by a unique database constraint;
* **explicit lifecycles** - orders, reservations, products and SKUs have
  enforced state machines that refuse backwards transitions;
* **tenant isolation** - every repository call is tenant-scoped and every
  tenant-owned row carries its owner.

Read `docs/architecture/system-overview.md` in the repository for the full
picture, including what is deliberately deferred to later phases.
"""


def create_app(
    *,
    settings: Settings | None = None,
    event_dispatcher: EventDispatcher | None = None,
    cache: CachePort | None = None,
    search_indexer: SearchIndexer | None = None,
) -> FastAPI:
    """Build the ASGI application."""
    app_settings = settings or get_settings()
    configure_logging(level=app_settings.log_level, json_output=app_settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(
            app_settings.database_url,
            pool_size=app_settings.db_pool_size,
            max_overflow=app_settings.db_max_overflow,
            pool_timeout=app_settings.db_pool_timeout_seconds,
            pool_recycle=app_settings.db_pool_recycle_seconds,
            statement_timeout_ms=app_settings.db_statement_timeout_ms,
            echo=app_settings.db_echo,
        )
        app.state.database = database

        cache_instance: CachePort
        owns_cache = False
        if cache is not None:
            cache_instance = cache
        else:
            cache_instance = RedisCacheAdapter.from_settings(app_settings)
            owns_cache = True
        app.state.cache = cache_instance

        indexer_instance: SearchIndexer
        owns_indexer = False
        if search_indexer is not None:
            indexer_instance = search_indexer
        elif app_settings.elasticsearch_url:
            indexer_instance = ElasticsearchIndexAdapter.from_settings(app_settings)
            owns_indexer = True
        else:
            from search.adapters.memory import InMemorySearchIndexer

            indexer_instance = InMemorySearchIndexer()
        app.state.search_indexer = indexer_instance

        try:
            yield
        finally:
            if owns_indexer:
                await indexer_instance.close()
            if owns_cache:
                await cache_instance.close()
            await database.dispose()

    app = FastAPI(
        title=app_settings.api_title,
        version=API_VERSION,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        contact={"name": "Arad Platform Team"},
        openapi_tags=[
            {"name": "platform", "description": "Tenant provisioning."},
            {"name": "catalog", "description": "Products and SKUs."},
            {"name": "inventory", "description": "Warehouses, stock, reservations."},
            {"name": "ordering", "description": "Order submission and lifecycle."},
            {"name": "search", "description": "Search queries, reindexing, and reconciliation."},
            {"name": "health", "description": "Liveness and readiness probes."},
        ],
    )
    app.state.settings = app_settings
    app.state.event_dispatcher = event_dispatcher or NullEventDispatcher()

    # Middleware runs outermost-first: correlation id first so that even a
    # rejected request is logged with an id, then the size guard, then access logs.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=app_settings.max_request_bytes)
    app.add_middleware(CorrelationIdMiddleware)

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(platform.router, prefix=app_settings.api_prefix)
    app.include_router(catalog.router, prefix=app_settings.api_prefix)
    app.include_router(inventory.router, prefix=app_settings.api_prefix)
    app.include_router(ordering.router, prefix=app_settings.api_prefix)
    app.include_router(search.router, prefix=app_settings.api_prefix)

    return app


__all__ = ["API_VERSION", "create_app"]
