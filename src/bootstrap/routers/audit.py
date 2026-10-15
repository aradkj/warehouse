"""Audit HTTP router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from audit.ports.audit import AuditPort
from bootstrap.dependencies import (
    get_audit_port,
    get_authenticated_tenant_context,
    require_permission,
)
from bootstrap.schemas.audit import AuditEntryResponse, AuditPageResponse
from bootstrap.schemas.common import ErrorResponse
from identity.domain.principal import Principal
from shared.application.context import RequestContext

router = APIRouter(prefix="/audit", tags=["audit"])

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.get(
    "/events",
    response_model=AuditPageResponse,
    summary="List audit events for tenant",
    responses=ERROR_RESPONSES,
)
async def list_audit_events(
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    audit_port: Annotated[AuditPort, Depends(get_audit_port)],
    _principal: Annotated[Principal, Depends(require_permission("audit.events.read"))],
    limit: Annotated[int, Query(ge=1, le=200, description="Max audit entries to fetch.")] = 50,
) -> AuditPageResponse:
    """Retrieve immutable audit records for the caller's tenant."""
    assert context.tenant_id is not None
    rows = await audit_port.list_for_tenant(context.tenant_id, limit=limit)
    items = [AuditEntryResponse.from_row(row) for row in rows]
    return AuditPageResponse(items=items, total=len(items))


__all__ = ["router"]
