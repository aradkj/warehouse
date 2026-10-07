"""Ordering value objects: lifecycle states and order lines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from shared.domain.aggregate import Entity
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import OrderLineId, ProductId, SkuId, new_id
from shared.domain.value_objects import Money, Quantity

MAX_ORDER_LINES = 100


class OrderStatus(StrEnum):
    """Order lifecycle state.

    ``PENDING``            accepted, stock not yet secured.
    ``BACKORDERED``        accepted, stock partially or not yet available - the
                           configurable back-order outcome of the submission flow.
    ``INVENTORY_RESERVED`` stock is held for every line, awaiting confirmation.
    ``CONFIRMED``          stock has been consumed; a Phase 2 fulfilment exists.
    ``SHIPPED``            handed to a carrier (Phase 2 drives this transition).
    ``DELIVERED``          terminal.
    ``CANCELLED``          terminal; reached from a non-confirmed state.

    ``CONFIRMED``/``SHIPPED``/``DELIVERED`` are terminal *for the forward path*:
    the aggregate refuses to move backwards out of them (see ADR-0004).
    """

    PENDING = "PENDING"
    BACKORDERED = "BACKORDERED"
    INVENTORY_RESERVED = "INVENTORY_RESERVED"
    CONFIRMED = "CONFIRMED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.DELIVERED, OrderStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class OrderLine(Entity):
    """One ordered item, with the pricing snapshot taken when the order was placed.

    The snapshot is deliberate: a later catalogue price change must never alter
    what an existing order says the customer owes. ``product_name`` and
    ``sku_code`` are snapshotted for the same reason - an order is a historical
    document, and it must stay readable even after the catalogue changes.
    """

    id: OrderLineId
    line_number: int
    sku_id: SkuId
    product_id: ProductId
    sku_code: str
    product_name: str
    quantity: Quantity
    unit_price: Money
    created_at: datetime

    @classmethod
    def create(
        cls,
        *,
        line_number: int,
        sku_id: SkuId,
        product_id: ProductId,
        sku_code: str,
        product_name: str,
        quantity: Quantity,
        unit_price: Money,
        now: datetime,
    ) -> OrderLine:
        if not quantity.is_positive():
            raise InvalidValue(
                "an order line must order a positive quantity",
                details={"sku_id": str(sku_id), "quantity": int(quantity)},
            )
        if unit_price.is_negative():
            raise InvalidValue(
                "an order line cannot carry a negative unit price",
                details={"sku_id": str(sku_id)},
            )
        return cls(
            id=OrderLineId(new_id()),
            line_number=line_number,
            sku_id=sku_id,
            product_id=product_id,
            sku_code=sku_code,
            product_name=product_name,
            quantity=quantity,
            unit_price=unit_price,
            created_at=now,
        )

    @property
    def line_total(self) -> Money:
        return self.unit_price * self.quantity.value


__all__ = ["MAX_ORDER_LINES", "OrderLine", "OrderStatus"]
