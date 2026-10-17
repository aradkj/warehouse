"""Row <-> aggregate mapping for the fulfillment context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.engine import RowMapping

from fulfillment.domain.fulfillment import Fulfillment
from fulfillment.domain.value_objects import FulfillmentStatus, FulfillmentTask
from shared.domain.identifiers import (
    FulfillmentId,
    FulfillmentTaskId,
    OrderId,
    SkuId,
    TenantId,
    WarehouseId,
)
from shared.domain.value_objects import Quantity


def fulfillment_insert_values(fulfillment: Fulfillment) -> dict[str, Any]:
    return {
        "id": fulfillment.id,
        "tenant_id": fulfillment.tenant_id,
        "order_id": fulfillment.order_id,
        "warehouse_id": fulfillment.warehouse_id,
        "status": fulfillment.status.value,
        "tracking_number": fulfillment.tracking_number,
        "carrier": fulfillment.carrier,
        "failure_reason": fulfillment.failure_reason,
        "retry_count": fulfillment.retry_count,
        "max_retries": fulfillment.max_retries,
        "version": fulfillment.version,
        "created_at": fulfillment.created_at,
        "updated_at": fulfillment.updated_at,
    }


def fulfillment_update_values(fulfillment: Fulfillment) -> dict[str, Any]:
    return {
        "status": fulfillment.status.value,
        "tracking_number": fulfillment.tracking_number,
        "carrier": fulfillment.carrier,
        "failure_reason": fulfillment.failure_reason,
        "retry_count": fulfillment.retry_count,
        "updated_at": fulfillment.updated_at,
    }


def fulfillment_task_insert_values(
    tenant_id: TenantId, fulfillment_id: FulfillmentId, task: FulfillmentTask
) -> dict[str, Any]:
    return {
        "id": task.task_id,
        "tenant_id": tenant_id,
        "fulfillment_id": fulfillment_id,
        "warehouse_id": task.warehouse_id,
        "sku_id": task.sku_id,
        "quantity": int(task.quantity),
        "is_completed": task.is_completed,
    }


def fulfillment_task_to_domain(row: RowMapping) -> FulfillmentTask:
    return FulfillmentTask(
        task_id=FulfillmentTaskId(row["id"]),
        warehouse_id=WarehouseId(row["warehouse_id"]),
        sku_id=SkuId(row["sku_id"]),
        quantity=Quantity(row["quantity"]),
        is_completed=bool(row["is_completed"]),
    )


def fulfillment_to_domain(row: RowMapping, task_rows: Sequence[RowMapping]) -> Fulfillment:
    return Fulfillment(
        id=FulfillmentId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]),
        order_id=OrderId(row["order_id"]),
        warehouse_id=WarehouseId(row["warehouse_id"]),
        status=FulfillmentStatus(row["status"]),
        tasks=[fulfillment_task_to_domain(t) for t in task_rows],
        tracking_number=row["tracking_number"],
        carrier=row["carrier"],
        failure_reason=row["failure_reason"],
        retry_count=row["retry_count"],
        max_retries=row["max_retries"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


__all__ = [
    "fulfillment_insert_values",
    "fulfillment_task_insert_values",
    "fulfillment_task_to_domain",
    "fulfillment_to_domain",
    "fulfillment_update_values",
]
