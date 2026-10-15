"""Fulfillment application read models / views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from fulfillment.domain.fulfillment import Fulfillment


@dataclass(frozen=True, slots=True)
class FulfillmentTaskView:
    """Read view of an individual fulfillment task."""

    task_id: UUID
    warehouse_id: UUID
    sku_id: UUID
    quantity: int
    is_completed: bool


@dataclass(frozen=True, slots=True)
class FulfillmentView:
    """Read view of a Fulfillment aggregate."""

    id: UUID
    tenant_id: UUID
    order_id: UUID
    warehouse_id: UUID
    status: str
    tasks: list[FulfillmentTaskView]
    tracking_number: str | None
    carrier: str | None
    failure_reason: str | None
    retry_count: int
    max_retries: int
    created_at: datetime
    updated_at: datetime
    version: int

    @property
    def fulfillment_id(self) -> UUID:
        return self.id

    @classmethod
    def from_aggregate(cls, fulfillment: Fulfillment) -> FulfillmentView:
        return cls(
            id=fulfillment.id,
            tenant_id=fulfillment.tenant_id,
            order_id=fulfillment.order_id,
            warehouse_id=fulfillment.warehouse_id,
            status=fulfillment.status.value,
            tasks=[
                FulfillmentTaskView(
                    task_id=task.task_id,
                    warehouse_id=task.warehouse_id,
                    sku_id=task.sku_id,
                    quantity=task.quantity.value,
                    is_completed=task.is_completed,
                )
                for task in fulfillment.tasks
            ],
            tracking_number=fulfillment.tracking_number,
            carrier=fulfillment.carrier,
            failure_reason=fulfillment.failure_reason,
            retry_count=fulfillment.retry_count,
            max_retries=fulfillment.max_retries,
            created_at=fulfillment.created_at,
            updated_at=fulfillment.updated_at,
            version=fulfillment.version,
        )


__all__ = [
    "FulfillmentTaskView",
    "FulfillmentView",
]
