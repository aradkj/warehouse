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
    responses={503: {"description": "The system of record is unreachable."}},
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
    except Exception:
        logger.warning("readiness probe failed: the database is unreachable", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        checks["database"] = "unreachable"
        return HealthResponse(
            status="degraded", environment=settings.environment, checks=checks
        )

    # Cache is an accelerator: Redis outage degrades cache without failing readiness (Section 29)
    try:
        cache_ok = await cache.ping()
        checks["cache"] = "ok" if cache_ok else "unreachable"
    except Exception:
        checks["cache"] = "unreachable"

    return HealthResponse(
        status="ready", environment=settings.environment, checks=checks
    )


logger = logging.getLogger("arad.api.health")


__all__ = ["router"]
