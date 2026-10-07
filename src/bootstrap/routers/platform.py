"""Platform HTTP adapter: tenant provisioning.

Tenant provisioning is the platform's onboarding entry point. It sits behind the
same ``/v1`` prefix as everything else, but it is the only write that is not
tenant-scoped - there is no tenant yet.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Response, status

from bootstrap.dependencies import (
    UseCases,
    apply_outcome,
    get_request_context,
    get_use_cases,
)
from bootstrap.schemas.common import ErrorResponse
from bootstrap.schemas.platform import RegisterTenantRequest, TenantResponse
from identity.application.commands import GetTenantQuery, RegisterTenantCommand
from shared.application.context import RequestContext
from shared.domain.identifiers import TenantId

router = APIRouter(tags=["platform"])

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.post(
    "/tenants",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a tenant",
    responses=ERROR_RESPONSES,
)
async def register_tenant(
    payload: RegisterTenantRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_request_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> TenantResponse:
    """Create a tenant (the onboarding entry point of the platform).

    This is the only Phase 1 write that is not tenant-scoped. Its idempotency
    comes from the unique slug: registering the same slug twice yields
    ``409 duplicate_resource``, and the first tenant id is returned by a lookup.
    Authentication and tenant administration belong to Phase 4.
    """
    outcome = await use_cases.register_tenant(
        RegisterTenantCommand(context=context, slug=payload.slug, name=payload.name)
    )
    apply_outcome(response, outcome)
    return TenantResponse.from_view(outcome.view)


@router.get(
    "/tenants/{tenant_id}",
    response_model=TenantResponse,
    summary="Get a tenant",
    responses=ERROR_RESPONSES,
)
async def get_tenant(
    context: Annotated[RequestContext, Depends(get_request_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    tenant_id: Annotated[UUID, Path(description="Identifier returned by provisioning.")],
) -> TenantResponse:
    """Fetch a tenant by id."""
    view = await use_cases.get_tenant(
        GetTenantQuery(context=context, tenant_id=TenantId(tenant_id))
    )
    return TenantResponse.from_view(view)


__all__ = ["router"]
