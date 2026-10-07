"""Inventory request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bootstrap.schemas.common import PageSchema
from inventory.application.commands import (
    AdjustmentView,
    AvailabilityView,
    MovementView,
    ReservationView,
    WarehouseView,
)
from inventory.domain.allocation import AllocationStrategy
from inventory.domain.value_objects import AdjustmentReason, MovementType, ReservationStatus


class RegisterWarehouseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=160)


class AdjustInventoryRequest(BaseModel):
    """Receive, write off or correct stock."""

    model_config = ConfigDict(extra="forbid")

    warehouse_id: UUID
    sku_id: UUID
    quantity_delta: int = Field(
        description="Signed change: positive receives stock, negative writes it off. Never zero."
    )
    reason: AdjustmentReason
    note: str | None = Field(default=None, max_length=256)


class StockRequirementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: UUID
    quantity: int = Field(gt=0)
    order_reference: str | None = Field(
        default=None,
        max_length=128,
        description="Optional external line reference; stored with the hold for traceability.",
    )


class ReserveInventoryRequest(BaseModel):
    """Create a standalone hold (not attached to an order)."""

    model_config = ConfigDict(extra="forbid")

    requirements: list[StockRequirementRequest] = Field(min_length=1, max_length=100)
    warehouse_ids: list[UUID] | None = Field(
        default=None, description="Restrict allocation to these warehouses."
    )
    strategy: AllocationStrategy = Field(
        default=AllocationStrategy.ALL_OR_NOTHING,
        description=(
            "ALL_OR_NOTHING fails the request when the full quantity is unavailable; "
            "PARTIAL holds whatever is available."
        ),
    )
    ttl_seconds: int | None = Field(default=None, ge=1)


class WarehouseResponse(BaseModel):
    id: UUID
    code: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def from_view(cls, view: WarehouseView) -> WarehouseResponse:
        return cls(
            id=view.id,
            code=view.code,
            name=view.name,
            is_active=view.is_active,
            created_at=view.created_at,
            updated_at=view.updated_at,
            version=view.version,
        )


class WarehousePageResponse(PageSchema[WarehouseResponse]):
    pass


class AvailabilityResponse(BaseModel):
    """Stock position of one SKU in one warehouse."""

    inventory_item_id: UUID
    warehouse_id: UUID
    sku_id: UUID
    on_hand: int
    reserved: int
    available: int
    updated_at: datetime
    version: int

    @classmethod
    def from_view(cls, view: AvailabilityView) -> AvailabilityResponse:
        return cls(
            inventory_item_id=view.inventory_item_id,
            warehouse_id=view.warehouse_id,
            sku_id=view.sku_id,
            on_hand=view.on_hand,
            reserved=view.reserved,
            available=view.available,
            updated_at=view.updated_at,
            version=view.version,
        )


class AvailabilityPageResponse(PageSchema[AvailabilityResponse]):
    pass


class MovementResponse(BaseModel):
    """One immutable entry of the stock ledger."""

    id: UUID
    inventory_item_id: UUID
    warehouse_id: UUID
    sku_id: UUID
    movement_type: MovementType
    reason: AdjustmentReason | None
    quantity_delta: int
    on_hand_after: int
    reserved_after: int
    reference_type: str | None
    reference_id: UUID | None
    note: str | None
    occurred_at: datetime

    @classmethod
    def from_view(cls, view: MovementView) -> MovementResponse:
        return cls(
            id=view.id,
            inventory_item_id=view.inventory_item_id,
            warehouse_id=view.warehouse_id,
            sku_id=view.sku_id,
            movement_type=view.movement_type,
            reason=view.reason,
            quantity_delta=view.quantity_delta,
            on_hand_after=view.on_hand_after,
            reserved_after=view.reserved_after,
            reference_type=view.reference_type,
            reference_id=view.reference_id,
            note=view.note,
            occurred_at=view.occurred_at,
        )


class AdjustmentResponse(BaseModel):
    item: AvailabilityResponse
    movement: MovementResponse

    @classmethod
    def from_view(cls, view: AdjustmentView) -> AdjustmentResponse:
        return cls(
            item=AvailabilityResponse.from_view(view.item),
            movement=MovementResponse.from_view(view.movement),
        )


class ReservationAllocationResponse(BaseModel):
    id: UUID
    inventory_item_id: UUID
    warehouse_id: UUID
    sku_id: UUID
    order_line_id: UUID | None
    quantity: int

    @classmethod
    def from_view(cls, view: object) -> ReservationAllocationResponse:
        from inventory.application.commands import AllocationView

        assert isinstance(view, AllocationView)
        return cls(
            id=view.id,
            inventory_item_id=view.inventory_item_id,
            warehouse_id=view.warehouse_id,
            sku_id=view.sku_id,
            order_line_id=view.order_line_id,
            quantity=view.quantity,
        )


class ReservationResponse(BaseModel):
    id: UUID
    order_id: UUID | None
    status: ReservationStatus
    expires_at: datetime
    total_quantity: int
    allocations: list[ReservationAllocationResponse]
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    released_at: datetime | None
    release_reason: str | None
    version: int

    @classmethod
    def from_view(cls, view: ReservationView) -> ReservationResponse:
        return cls(
            id=view.id,
            order_id=view.order_id,
            status=view.status,
            expires_at=view.expires_at,
            total_quantity=view.total_quantity,
            allocations=[
                ReservationAllocationResponse.from_view(allocation)
                for allocation in view.allocations
            ],
            created_at=view.created_at,
            updated_at=view.updated_at,
            confirmed_at=view.confirmed_at,
            released_at=view.released_at,
            release_reason=view.release_reason,
            version=view.version,
        )


class ShortfallResponse(BaseModel):
    sku_id: UUID
    required: int
    allocated: int
    missing: int


class ReservationResultResponse(BaseModel):
    """Result of a reservation attempt.

    ``reservation`` is ``null`` only when nothing at all could be held and the
    request used the ``PARTIAL`` strategy.
    """

    reservation: ReservationResponse | None
    shortfalls: list[ShortfallResponse]
    is_complete: bool


__all__ = [
    "AdjustInventoryRequest",
    "AdjustmentResponse",
    "AvailabilityPageResponse",
    "AvailabilityResponse",
    "MovementResponse",
    "RegisterWarehouseRequest",
    "ReservationAllocationResponse",
    "ReservationResponse",
    "ReservationResultResponse",
    "ReserveInventoryRequest",
    "ShortfallResponse",
    "StockRequirementRequest",
    "WarehousePageResponse",
    "WarehouseResponse",
]
