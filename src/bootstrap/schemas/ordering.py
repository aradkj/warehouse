"""Ordering request/response schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bootstrap.schemas.common import MoneySchema, PageSchema
from ordering.application.commands import OrderLineView, OrderView, ReservationSummaryView
from ordering.domain.value_objects import OrderStatus


class OrderLineRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: UUID
    quantity: int = Field(gt=0, le=1_000_000)


class CreateOrderRequest(BaseModel):
    """Submit an order.

    Prices come from the catalog: the channel states *what* it wants, never what
    it pays.
    """

    model_config = ConfigDict(extra="forbid")

    lines: list[OrderLineRequestModel] = Field(min_length=1, max_length=100)
    customer_reference: str | None = Field(
        default=None,
        max_length=128,
        description="The channel's own identifier for this order or customer.",
    )


class CancelOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=256)


class OrderLineResponse(BaseModel):
    id: UUID
    line_number: int
    sku_id: UUID
    product_id: UUID
    sku_code: str
    product_name: str
    quantity: int
    unit_price: MoneySchema | None = None
    line_total: MoneySchema | None = None

    @classmethod
    def from_view(
        cls, view: OrderLineView, *, redact_financials: bool = False
    ) -> OrderLineResponse:
        return cls(
            id=view.id,
            line_number=view.line_number,
            sku_id=view.sku_id,
            product_id=view.product_id,
            sku_code=view.sku_code,
            product_name=view.product_name,
            quantity=view.quantity,
            unit_price=(
                None
                if redact_financials
                else MoneySchema(amount_minor=view.unit_price_minor, currency=view.currency)
            ),
            line_total=(
                None
                if redact_financials
                else MoneySchema(amount_minor=view.line_total_minor, currency=view.currency)
            ),
        )


class ReservationSummaryResponse(BaseModel):
    id: UUID
    status: str
    expires_at: datetime
    allocated_quantity: int
    warehouse_ids: list[UUID]

    @classmethod
    def from_view(cls, view: ReservationSummaryView) -> ReservationSummaryResponse:
        return cls(
            id=UUID(view.id),
            status=view.status,
            expires_at=view.expires_at,
            allocated_quantity=view.allocated_quantity,
            warehouse_ids=[UUID(value) for value in view.warehouse_ids],
        )


class OrderResponse(BaseModel):
    id: UUID
    status: OrderStatus
    currency: str
    customer_reference: str | None
    total: MoneySchema | None = None
    total_quantity: int
    lines: list[OrderLineResponse]
    reservation: ReservationSummaryResponse | None
    placed_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    version: int

    @classmethod
    def from_view(cls, view: OrderView, *, redact_financials: bool = False) -> OrderResponse:
        return cls(
            id=view.id,
            status=view.status,
            currency=view.currency,
            customer_reference=view.customer_reference,
            total=(
                None
                if redact_financials
                else MoneySchema(amount_minor=view.total_minor, currency=view.currency)
            ),
            total_quantity=view.total_quantity,
            lines=[
                OrderLineResponse.from_view(line, redact_financials=redact_financials)
                for line in view.lines
            ],
            reservation=(
                ReservationSummaryResponse.from_view(view.reservation)
                if view.reservation is not None
                else None
            ),
            placed_at=view.placed_at,
            updated_at=view.updated_at,
            confirmed_at=view.confirmed_at,
            cancelled_at=view.cancelled_at,
            cancellation_reason=view.cancellation_reason,
            version=view.version,
        )


class OrderPageResponse(PageSchema[OrderResponse]):
    pass


__all__ = [
    "CancelOrderRequest",
    "CreateOrderRequest",
    "OrderLineRequestModel",
    "OrderLineResponse",
    "OrderPageResponse",
    "OrderResponse",
    "ReservationSummaryResponse",
]
