"""Ordering HTTP adapter: submit, read, confirm and cancel orders."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Response, status

from bootstrap.dependencies import (
    UseCases,
    apply_outcome,
    get_order_search_service,
    get_tenant_context,
    get_use_cases,
    parse_if_match,
    require_permission,
)
from bootstrap.schemas.common import ErrorResponse
from bootstrap.schemas.ordering import (
    CancelOrderRequest,
    CreateOrderRequest,
    OrderPageResponse,
    OrderResponse,
)
from bootstrap.schemas.search import OrderSearchPageResponse
from identity.domain.principal import Principal
from ordering.application.commands import (
    CancelOrderCommand,
    ConfirmOrderCommand,
    CreateOrderCommand,
    GetOrderQuery,
    ListOrdersQuery,
    OrderLineRequest,
)
from ordering.domain.value_objects import OrderStatus
from search.application.order_service import SearchOrdersService
from shared.application.context import RequestContext
from shared.domain.identifiers import OrderId, SkuId

router = APIRouter(tags=["ordering"])

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.post(
    "/orders",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit an order",
    responses=ERROR_RESPONSES,
)
async def create_order(
    payload: CreateOrderRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    principal: Annotated[Principal, Depends(require_permission("orders.create"))],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description=(
                "Required. Tenant-scoped: retrying with the same key and the same "
                "payload replays the original order instead of creating a second one."
            ),
        ),
    ] = None,
) -> OrderResponse:
    """Submit an order and reserve its stock in the same transaction.

    Outcomes:

    * ``201`` with ``CONFIRMED``         - every line was reserved and consumed;
    * ``201`` with ``BACKORDERED``       - the order was accepted short of stock
      (configurable back-order policy);
    * ``409 insufficient_inventory``     - rejection policy, no order created.

    A retry with the same ``Idempotency-Key`` returns the original order
    (``Idempotent-Replay: true``) and reserves nothing twice.
    """
    assert context.tenant_id is not None
    outcome = await use_cases.create_order(
        CreateOrderCommand(
            tenant_id=context.tenant_id,
            context=context,
            lines=tuple(
                OrderLineRequest(sku_id=SkuId(line.sku_id), quantity=line.quantity)
                for line in payload.lines
            ),
            customer_reference=payload.customer_reference,
        )
    )
    apply_outcome(response, outcome)
    redact = not principal.has_permission("orders.financials.read")
    return OrderResponse.from_view(outcome.view, redact_financials=redact)


@router.get(
    "/orders",
    response_model=OrderPageResponse,
    summary="List orders",
    responses=ERROR_RESPONSES,
)
async def list_orders(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    principal: Annotated[Principal, Depends(require_permission("orders.read"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
) -> OrderPageResponse:
    """Newest-first order list (Elasticsearch-backed search arrives in Phase 3)."""
    assert context.tenant_id is not None
    page = await use_cases.list_orders(
        ListOrdersQuery(
            tenant_id=context.tenant_id,
            context=context,
            limit=limit,
            cursor=cursor,
            status=order_status,
        )
    )
    redact = not principal.has_permission("orders.financials.read")
    return OrderPageResponse(
        items=[OrderResponse.from_view(view, redact_financials=redact) for view in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/orders/search",
    response_model=OrderSearchPageResponse,
    summary="Search orders",
    responses=ERROR_RESPONSES,
)
async def search_orders(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    search_service: Annotated[SearchOrdersService, Depends(get_order_search_service)],
    _principal: Annotated[Principal, Depends(require_permission("orders.read"))],
    q: Annotated[
        str | None,
        Query(description="Free-text query across customer reference, order ID, or SKU."),
    ] = None,
    order_status: Annotated[
        str | None,
        Query(alias="status", description="Filter by order status."),
    ] = None,
    sku: Annotated[
        str | None,
        Query(description="Filter by SKU code or identifier."),
    ] = None,
    customer: Annotated[
        str | None,
        Query(description="Filter by customer identifier or reference."),
    ] = None,
    warehouse: Annotated[
        str | None,
        Query(description="Filter by warehouse identifier."),
    ] = None,
    date_from: Annotated[
        str | None,
        Query(description="Filter orders created on or after ISO timestamp."),
    ] = None,
    date_to: Annotated[
        str | None,
        Query(description="Filter orders created on or before ISO timestamp."),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 20,
    cursor: Annotated[
        str | None,
        Query(description="Opaque search_after pagination cursor."),
    ] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=100, description="Page size with cursor pagination."),
    ] = None,
    sort_by: Annotated[
        str,
        Query(description="Sort field: created_at, updated_at, status, total_minor."),
    ] = "created_at",
    sort_order: Annotated[
        str,
        Query(description="Sort direction: asc or desc."),
    ] = "desc",
) -> OrderSearchPageResponse:
    """Execute tenant-isolated Elasticsearch-backed order search."""
    assert context.tenant_id is not None
    result = await search_service.search(
        tenant_id=str(context.tenant_id),
        q=q,
        status=order_status,
        sku=sku,
        customer=customer,
        warehouse=warehouse,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
        limit=limit,
        cursor=cursor,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return OrderSearchPageResponse.from_page_result(result)


@router.get(
    "/orders/{order_id}",
    response_model=OrderResponse,
    summary="Get an order",
    responses=ERROR_RESPONSES,
)
async def get_order(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    principal: Annotated[Principal, Depends(require_permission("orders.read"))],
    order_id: Annotated[UUID, Path()],
) -> OrderResponse:
    """Fetch an order with its lines, totals and inventory hold."""
    assert context.tenant_id is not None
    view = await use_cases.get_order(
        GetOrderQuery(tenant_id=context.tenant_id, context=context, order_id=OrderId(order_id))
    )
    redact = not principal.has_permission("orders.financials.read")
    return OrderResponse.from_view(view, redact_financials=redact)


@router.post(
    "/orders/{order_id}/confirm",
    response_model=OrderResponse,
    summary="Confirm an order",
    responses=ERROR_RESPONSES,
)
async def confirm_order(
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    order_id: Annotated[UUID, Path()],
    principal: Annotated[Principal, Depends(require_permission("orders.confirm"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OrderResponse:
    """Confirm once every line is genuinely held by inventory.

    A back-ordered order first tries to secure *all* of its stock (all or
    nothing); a reservation that cannot cover every line is refused with
    ``409 order_lines_not_reserved``. Confirming consumes the hold: stock leaves
    the warehouse.
    """
    assert context.tenant_id is not None
    outcome = await use_cases.confirm_order(
        ConfirmOrderCommand(
            tenant_id=context.tenant_id, context=context, order_id=OrderId(order_id)
        )
    )
    apply_outcome(response, outcome)
    redact = not principal.has_permission("orders.financials.read")
    return OrderResponse.from_view(outcome.view, redact_financials=redact)


@router.post(
    "/orders/{order_id}/cancel",
    response_model=OrderResponse,
    summary="Cancel an order",
    responses=ERROR_RESPONSES,
)
async def cancel_order(
    payload: CancelOrderRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    order_id: Annotated[UUID, Path()],
    principal: Annotated[Principal, Depends(require_permission("orders.cancel"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    expected_version: Annotated[int | None, Depends(parse_if_match)] = None,
) -> OrderResponse:
    """Cancel a not-yet-confirmed order and release its stock.

    Cancelling an already cancelled order returns its current state. Cancelling a
    *confirmed* order is refused (``409 invalid_order_transition``): the stock has
    been consumed and undoing it requires a compensating workflow (Phase 2).
    """
    assert context.tenant_id is not None
    outcome = await use_cases.cancel_order(
        CancelOrderCommand(
            tenant_id=context.tenant_id,
            context=context,
            order_id=OrderId(order_id),
            reason=payload.reason,
            expected_version=expected_version,
        )
    )
    apply_outcome(response, outcome)
    redact = not principal.has_permission("orders.financials.read")
    return OrderResponse.from_view(outcome.view, redact_financials=redact)


__all__ = ["router"]
