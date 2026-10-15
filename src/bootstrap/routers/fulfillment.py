"""Fulfillment HTTP router."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Response, status

from bootstrap.dependencies import (
    UseCases,
    apply_outcome,
    get_authenticated_tenant_context,
    get_use_cases,
    require_permission,
)
from bootstrap.schemas.common import ErrorResponse
from bootstrap.schemas.fulfillment import (
    FulfillmentResponse,
    RequestFulfillmentRequest,
)
from fulfillment.application.commands import (
    GetFulfillmentQuery,
    RequestFulfillmentCommand,
    RetryFulfillmentCommand,
    TaskRequirement,
)
from identity.domain.principal import Principal
from shared.application.context import RequestContext
from shared.domain.identifiers import FulfillmentId, OrderId, SkuId, WarehouseId
from shared.domain.value_objects import Quantity

router = APIRouter(prefix="/fulfillments", tags=["fulfillment"])

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.post(
    "",
    response_model=FulfillmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request fulfillment for an order",
    responses=ERROR_RESPONSES,
)
async def request_fulfillment(
    payload: RequestFulfillmentRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("fulfillments.create"))],
    _idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="Tenant-scoped idempotency key."),
    ] = None,
) -> FulfillmentResponse:
    """Create fulfillment tasks and allocate stock in a designated warehouse."""
    assert context.tenant_id is not None
    assert use_cases.request_fulfillment is not None
    outcome = await use_cases.request_fulfillment(
        RequestFulfillmentCommand(
            tenant_id=context.tenant_id,
            context=context,
            order_id=OrderId(payload.order_id),
            warehouse_id=WarehouseId(payload.warehouse_id),
            requirements=tuple(
                TaskRequirement(
                    sku_id=SkuId(req.sku_id),
                    quantity=Quantity(req.quantity),
                )
                for req in payload.requirements
            ),
            max_retries=payload.max_retries,
        )
    )
    apply_outcome(response, outcome)
    return FulfillmentResponse.from_view(outcome.view)


@router.get(
    "/{fulfillment_id}",
    response_model=FulfillmentResponse,
    summary="Get fulfillment status and task details",
    responses=ERROR_RESPONSES,
)
async def get_fulfillment(
    fulfillment_id: Annotated[UUID, Path(description="The unique fulfillment identifier.")],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("fulfillments.read"))],
) -> FulfillmentResponse:
    """Fetch fulfillment aggregate state."""
    assert context.tenant_id is not None
    assert use_cases.get_fulfillment is not None
    view = await use_cases.get_fulfillment(
        GetFulfillmentQuery(
            tenant_id=context.tenant_id,
            context=context,
            fulfillment_id=FulfillmentId(fulfillment_id),
        )
    )
    return FulfillmentResponse.from_view(view)


@router.post(
    "/{fulfillment_id}/retry",
    response_model=FulfillmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Retry a failed fulfillment",
    responses=ERROR_RESPONSES,
)
async def retry_fulfillment(
    fulfillment_id: Annotated[UUID, Path(description="The unique fulfillment identifier.")],
    response: Response,
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("fulfillments.modify"))],
    _idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", description="Tenant-scoped idempotency key."),
    ] = None,
) -> FulfillmentResponse:
    """Retry external carrier dispatch for a failed fulfillment."""
    assert context.tenant_id is not None
    assert use_cases.retry_fulfillment is not None
    outcome = await use_cases.retry_fulfillment(
        RetryFulfillmentCommand(
            tenant_id=context.tenant_id,
            context=context,
            fulfillment_id=FulfillmentId(fulfillment_id),
        )
    )
    apply_outcome(response, outcome)
    return FulfillmentResponse.from_view(outcome.view)


__all__ = ["router"]
