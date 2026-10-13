"""Inventory HTTP adapter: warehouses, adjustments, availability and reservations."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status

from bootstrap.dependencies import (
    UseCases,
    apply_outcome,
    get_tenant_context,
    get_use_cases,
    require_permission,
)
from bootstrap.schemas.common import ErrorResponse
from bootstrap.schemas.inventory import (
    AdjustInventoryRequest,
    AdjustmentResponse,
    AvailabilityPageResponse,
    AvailabilityResponse,
    RegisterWarehouseRequest,
    ReservationResponse,
    ReservationResultResponse,
    ReserveInventoryRequest,
    ShortfallResponse,
    WarehousePageResponse,
    WarehouseResponse,
)
from identity.domain.principal import Principal
from inventory.application.commands import (
    AdjustInventoryCommand,
    GetAvailabilityQuery,
    GetReservationQuery,
    GetWarehouseQuery,
    ListWarehousesQuery,
    RegisterWarehouseCommand,
    ReleaseReservationCommand,
    ReserveInventoryCommand,
)
from inventory.domain.value_objects import StockRequirement
from shared.application.context import RequestContext
from shared.domain.identifiers import ReservationId, SkuId, WarehouseId
from shared.domain.value_objects import Quantity

router = APIRouter(tags=["inventory"])

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.post(
    "/warehouses",
    response_model=WarehouseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a warehouse",
    responses=ERROR_RESPONSES,
)
async def register_warehouse(
    payload: RegisterWarehouseRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("inventory.adjust"))],
) -> WarehouseResponse:
    """Create a stocking location. Codes are unique per tenant."""
    assert context.tenant_id is not None
    outcome = await use_cases.register_warehouse(
        RegisterWarehouseCommand(
            tenant_id=context.tenant_id,
            context=context,
            code=payload.code,
            name=payload.name,
        )
    )
    apply_outcome(response, outcome)
    return WarehouseResponse.from_view(outcome.view)


@router.get(
    "/warehouses",
    response_model=WarehousePageResponse,
    summary="List warehouses",
    responses=ERROR_RESPONSES,
)
async def list_warehouses(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("inventory.read"))],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = True,
) -> WarehousePageResponse:
    """Newest-first warehouse list."""
    assert context.tenant_id is not None
    page = await use_cases.list_warehouses(
        ListWarehousesQuery(
            tenant_id=context.tenant_id,
            context=context,
            limit=limit,
            cursor=cursor,
            include_inactive=include_inactive,
        )
    )
    return WarehousePageResponse(
        items=[WarehouseResponse.from_view(view) for view in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/warehouses/{warehouse_id}",
    response_model=WarehouseResponse,
    summary="Get a warehouse",
    responses=ERROR_RESPONSES,
)
async def get_warehouse(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    warehouse_id: Annotated[UUID, Path()],
    _principal: Annotated[Principal, Depends(require_permission("inventory.read"))],
) -> WarehouseResponse:
    assert context.tenant_id is not None
    view = await use_cases.get_warehouse(
        GetWarehouseQuery(
            tenant_id=context.tenant_id, context=context, warehouse_id=WarehouseId(warehouse_id)
        )
    )
    return WarehouseResponse.from_view(view)


@router.post(
    "/inventory/adjustments",
    response_model=AdjustmentResponse,
    summary="Adjust stock",
    responses=ERROR_RESPONSES,
)
async def adjust_inventory(
    payload: AdjustInventoryRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("inventory.adjust"))],
) -> AdjustmentResponse:
    """Receive, write off or correct stock for one SKU in one warehouse.

    The stock row is created on first receipt. The adjustment is refused (409) if
    it would take on-hand stock below what is already reserved: released stock is
    a promise, and promises are not broken by a cycle count.
    """
    assert context.tenant_id is not None
    outcome = await use_cases.adjust_inventory(
        AdjustInventoryCommand(
            tenant_id=context.tenant_id,
            context=context,
            sku_id=SkuId(payload.sku_id),
            warehouse_id=WarehouseId(payload.warehouse_id),
            quantity_delta=payload.quantity_delta,
            reason=payload.reason,
            note=payload.note,
        )
    )
    apply_outcome(response, outcome)
    return AdjustmentResponse.from_view(outcome.view)


@router.get(
    "/inventory/availability",
    response_model=AvailabilityPageResponse,
    summary="Report availability",
    responses=ERROR_RESPONSES,
)
async def get_availability(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("inventory.read"))],
    sku_id: Annotated[UUID | None, Query()] = None,
    warehouse_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> AvailabilityPageResponse:
    """``available = on_hand - reserved`` per SKU/warehouse pair.

    This is a *read*: correctness of reservations never depends on it.
    """
    assert context.tenant_id is not None
    page = await use_cases.get_availability(
        GetAvailabilityQuery(
            tenant_id=context.tenant_id,
            context=context,
            limit=limit,
            cursor=cursor,
            sku_id=SkuId(sku_id) if sku_id is not None else None,
            warehouse_id=WarehouseId(warehouse_id) if warehouse_id is not None else None,
        )
    )
    return AvailabilityPageResponse(
        items=[AvailabilityResponse.from_view(view) for view in page.items],
        next_cursor=page.next_cursor,
    )


@router.post(
    "/reservations",
    response_model=ReservationResultResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reserve stock",
    responses=ERROR_RESPONSES,
)
async def reserve_inventory(
    payload: ReserveInventoryRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("reservations.create"))],
) -> ReservationResultResponse:
    """Hold stock without an order (a channel-side hold, a quotation, ...).

    With the default ``ALL_OR_NOTHING`` strategy the request fails with
    ``409 insufficient_inventory`` unless every requirement is met in full.
    ``PARTIAL`` holds whatever is available and reports the shortfall.
    """
    assert context.tenant_id is not None
    outcome = await use_cases.reserve_inventory(
        ReserveInventoryCommand(
            tenant_id=context.tenant_id,
            context=context,
            requirements=tuple(
                StockRequirement(sku_id=SkuId(item.sku_id), quantity=Quantity(item.quantity))
                for item in payload.requirements
            ),
            warehouse_ids=(
                tuple(WarehouseId(value) for value in payload.warehouse_ids)
                if payload.warehouse_ids
                else None
            ),
            strategy=payload.strategy,
            ttl_seconds=payload.ttl_seconds,
        )
    )
    apply_outcome(response, outcome)
    view = outcome.view
    return ReservationResultResponse(
        reservation=(ReservationResponse.from_view(view.reservation) if view.reservation else None),
        shortfalls=[
            ShortfallResponse(
                sku_id=shortfall.sku_id,
                required=shortfall.required,
                allocated=shortfall.allocated,
                missing=shortfall.missing,
            )
            for shortfall in view.shortfalls
        ],
        is_complete=view.is_complete,
    )


@router.get(
    "/reservations/{reservation_id}",
    response_model=ReservationResponse,
    summary="Get a reservation",
    responses=ERROR_RESPONSES,
)
async def get_reservation(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    reservation_id: Annotated[UUID, Path()],
    _principal: Annotated[Principal, Depends(require_permission("inventory.read"))],
) -> ReservationResponse:
    assert context.tenant_id is not None
    view = await use_cases.get_reservation(
        GetReservationQuery(
            tenant_id=context.tenant_id,
            context=context,
            reservation_id=ReservationId(reservation_id),
        )
    )
    return ReservationResponse.from_view(view)


@router.delete(
    "/reservations/{reservation_id}",
    response_model=ReservationResponse,
    summary="Release a reservation",
    responses=ERROR_RESPONSES,
)
async def release_reservation(
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    reservation_id: Annotated[UUID, Path()],
    _principal: Annotated[Principal, Depends(require_permission("reservations.release"))],
) -> ReservationResponse:
    """Give held stock back.

    Idempotent: releasing an already released reservation returns its current
    state. Releasing a *confirmed* reservation is a conflict - the stock has
    already left the warehouse and undoing that needs a compensating return.
    """
    assert context.tenant_id is not None
    outcome = await use_cases.release_reservation(
        ReleaseReservationCommand(
            tenant_id=context.tenant_id,
            context=context,
            reservation_id=ReservationId(reservation_id),
        )
    )
    apply_outcome(response, outcome)
    return ReservationResponse.from_view(outcome.view)


__all__ = ["router"]
