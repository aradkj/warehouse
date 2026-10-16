"""Health and readiness probes.

They live outside the ``/v1`` namespace on purpose: container orchestrators and
load balancers call them, they are not part of the business API contract, and
they must stay stable across API versions.

* ``/healthz`` - *liveness*. The process is running. Deliberately does not touch
  PostgreSQL: a database outage must not cause the orchestrator to kill and
  restart healthy API containers.
* ``/readyz``  - *readiness*. The process can serve traffic, which requires the
  system of record to be reachable.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from bootstrap.config import Settings
from bootstrap.dependencies import get_application_settings, get_cache, get_database
from bootstrap.metrics import metrics
from bootstrap.schemas.common import HealthResponse
from shared.infrastructure.db.session import Database
from shared.ports.cache import CachePort

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
async def healthz(
    settings: Annotated[Settings, Depends(get_application_settings)],
) -> HealthResponse:
    return HealthResponse(status="ok", environment=settings.environment, checks={})


@router.get(
    "/readyz",
    response_model=HealthResponse,
    summary="Readiness probe",
    responses={503: {"description": "The system of record is unreachable or unmigrated."}},
)
async def readyz(
    response: Response,
    database: Annotated[Database, Depends(get_database)],
    cache: Annotated[CachePort, Depends(get_cache)],
    settings: Annotated[Settings, Depends(get_application_settings)],
) -> HealthResponse:
    checks: dict[str, str] = {}
    try:
        await database.ping()
        checks["database"] = "ok"

        # Verify database migrations are applied via Database abstraction
        version_num = await database.check_migration_version()
        if version_num:
            checks["migrations"] = f"ok:{version_num}"
        else:
            logger.error("readiness probe failed: alembic_version table is empty")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            checks["migrations"] = "unmigrated"
            return HealthResponse(
                status="degraded", environment=settings.environment, checks=checks
            )

        # Update DB pool metrics
        pool_size, conns_in_use = database.get_pool_stats()
        metrics.set_gauge("database_pool_size", pool_size)
        metrics.set_gauge("database_connections_in_use", conns_in_use)
    except Exception:
        logger.warning(
            "readiness probe failed: the database is unreachable or unmigrated", exc_info=True
        )
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        checks["database"] = "unreachable"
        return HealthResponse(
            status="degraded", environment=settings.environment, checks=checks
        )

    # Cache is an accelerator: Redis outage degrades cache without failing readiness
    try:
        cache_ok = await cache.ping()
        checks["cache"] = "ok" if cache_ok else "unreachable"
    except Exception:
        checks["cache"] = "unreachable"

    return HealthResponse(status="ready", environment=settings.environment, checks=checks)


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_class=Response,
    responses={200: {"content": {"text/plain": {}}}},
)
async def prometheus_metrics() -> Response:
    """Expose Prometheus formatted metrics for scrapers."""
    content = metrics.generate_metrics_text()
    return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")


logger = logging.getLogger("arad.api.health")


__all__ = ["router"]
