"""Row <-> aggregate mapping for the ordering context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.engine import RowMapping

from ordering.domain.order import Order
from ordering.domain.value_objects import OrderLine, OrderStatus
from shared.domain.identifiers import (
    OrderId,
    OrderLineId,
    ProductId,
    SkuId,
    TenantId,
)
from shared.domain.value_objects import Money, Quantity


def order_insert_values(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "tenant_id": order.tenant_id,
        "customer_reference": order.customer_reference,
        "currency": order.currency,
        "status": order.status.value,
        "confirmed_at": order.confirmed_at,
        "cancelled_at": order.cancelled_at,
        "cancellation_reason": order.cancellation_reason,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "version": order.version,
    }


def order_update_values(order: Order) -> dict[str, Any]:
    return {
        "status": order.status.value,
        "confirmed_at": order.confirmed_at,
        "cancelled_at": order.cancelled_at,
        "cancellation_reason": order.cancellation_reason,
        "updated_at": order.updated_at,
    }


def order_line_insert_values(
    tenant_id: TenantId, order_id: OrderId, line: OrderLine
) -> dict[str, Any]:
    return {
        "id": line.id,
        "tenant_id": tenant_id,
        "order_id": order_id,
        "line_number": line.line_number,
        "sku_id": line.sku_id,
        "product_id": line.product_id,
        "sku_code": line.sku_code,
        "product_name": line.product_name,
        "quantity": int(line.quantity),
        "unit_price_minor": line.unit_price.amount_minor,
        "currency": line.unit_price.currency,
        "created_at": line.created_at,
    }


def order_line_to_domain(row: RowMapping) -> OrderLine:
    return OrderLine(
        id=OrderLineId(row["id"]),
        line_number=row["line_number"],
        sku_id=SkuId(row["sku_id"]),
        product_id=ProductId(row["product_id"]),
        sku_code=row["sku_code"],
        product_name=row["product_name"],
        quantity=Quantity(row["quantity"]),
        unit_price=Money(row["unit_price_minor"], row["currency"]),
        created_at=row["created_at"],
    )


def order_to_domain(row: RowMapping, line_rows: Sequence[RowMapping]) -> Order:
    return Order(
        id=OrderId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]),
        customer_id=None,
        customer_reference=row["customer_reference"],
        currency=row["currency"],
        status=OrderStatus(row["status"]),
        lines=[order_line_to_domain(line_row) for line_row in line_rows],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        confirmed_at=row["confirmed_at"],
        cancelled_at=row["cancelled_at"],
        cancellation_reason=row["cancellation_reason"],
        version=row["version"],
    )


__all__ = [
    "order_insert_values",
    "order_line_insert_values",
    "order_line_to_domain",
    "order_to_domain",
    "order_update_values",
]
