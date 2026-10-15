"""Fulfillment value objects and entities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from shared.domain.identifiers import FulfillmentTaskId, SkuId, WarehouseId
from shared.domain.value_objects import Quantity


class FulfillmentStatus(StrEnum):
    """Lifecycle state of a warehouse fulfillment order."""

    PENDING = "PENDING"
    ALLOCATED = "ALLOCATED"
    IN_PROGRESS = "IN_PROGRESS"
    SHIPPED = "SHIPPED"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"


class Carrier(StrEnum):
    """Supported external carrier providers."""

    FEDEX = "FEDEX"
    UPS = "UPS"
    DHL = "DHL"
    INTERNAL = "INTERNAL"


@dataclass(frozen=True, slots=True)
class FulfillmentTask:
    """An individual picking/packing task within a fulfillment."""

    task_id: FulfillmentTaskId
    warehouse_id: WarehouseId
    sku_id: SkuId
    quantity: Quantity
    is_completed: bool = False

    def complete(self) -> FulfillmentTask:
        """Return a copy of the task marked as completed."""
        return FulfillmentTask(
            task_id=self.task_id,
            warehouse_id=self.warehouse_id,
            sku_id=self.sku_id,
            quantity=self.quantity,
            is_completed=True,
        )


__all__ = [
    "Carrier",
    "FulfillmentStatus",
    "FulfillmentTask",
]
