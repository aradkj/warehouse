"""Unit tests for the Fulfillment domain aggregate and invariants."""

from __future__ import annotations

import pytest

from fulfillment.domain.errors import InvalidFulfillment, InvalidFulfillmentTransition
from fulfillment.domain.events import (
    FulfillmentAllocated,
    FulfillmentCompensated,
    FulfillmentCreated,
    FulfillmentFailed,
    FulfillmentShipped,
)
from fulfillment.domain.fulfillment import Fulfillment
from fulfillment.domain.value_objects import Carrier, FulfillmentStatus, FulfillmentTask
from shared.domain.identifiers import (
    FulfillmentTaskId,
    OrderId,
    SkuId,
    TenantId,
    WarehouseId,
    new_id,
)
from shared.domain.value_objects import Quantity


def _make_task(warehouse_id: WarehouseId, sku_code: str = "SKU-1", qty: int = 5) -> FulfillmentTask:
    return FulfillmentTask(
        task_id=FulfillmentTaskId(new_id()),
        warehouse_id=warehouse_id,
        sku_id=SkuId(new_id()),
        quantity=Quantity(qty),
    )


class TestFulfillmentDomain:
    def test_create_fulfillment_success(self) -> None:
        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        task = _make_task(warehouse_id)

        fulfillment = Fulfillment.create(
            tenant_id=tenant_id,
            order_id=order_id,
            warehouse_id=warehouse_id,
            tasks=[task],
            max_retries=3,
        )

        assert fulfillment.tenant_id == tenant_id
        assert fulfillment.order_id == order_id
        assert fulfillment.warehouse_id == warehouse_id
        assert fulfillment.status == FulfillmentStatus.PENDING
        assert len(fulfillment.tasks) == 1
        assert fulfillment.retry_count == 0

        events = fulfillment.pull_events()
        assert len(events) == 1
        assert isinstance(events[0], FulfillmentCreated)
        assert events[0].tenant_id == tenant_id
        assert events[0].fulfillment_id == fulfillment.id

    def test_create_fulfillment_empty_tasks_rejected(self) -> None:
        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())

        with pytest.raises(InvalidFulfillment, match="at least one task is required"):
            Fulfillment.create(
                tenant_id=tenant_id,
                order_id=order_id,
                warehouse_id=warehouse_id,
                tasks=[],
            )

    def test_create_fulfillment_mismatched_warehouse_rejected(self) -> None:
        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id_1 = WarehouseId(new_id())
        warehouse_id_2 = WarehouseId(new_id())
        task = _make_task(warehouse_id_2)

        with pytest.raises(InvalidFulfillment, match="does not match fulfillment warehouse"):
            Fulfillment.create(
                tenant_id=tenant_id,
                order_id=order_id,
                warehouse_id=warehouse_id_1,
                tasks=[task],
            )

    def test_lifecycle_allocate_and_ship(self) -> None:
        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        task = _make_task(warehouse_id)

        fulfillment = Fulfillment.create(
            tenant_id=tenant_id,
            order_id=order_id,
            warehouse_id=warehouse_id,
            tasks=[task],
        )
        fulfillment.pull_events()

        fulfillment.allocate()
        assert fulfillment.status == FulfillmentStatus.ALLOCATED
        events = fulfillment.pull_events()
        assert len(events) == 1
        assert isinstance(events[0], FulfillmentAllocated)

        fulfillment.start_processing()
        assert fulfillment.status == FulfillmentStatus.IN_PROGRESS

        fulfillment.mark_shipped(tracking_number="TRACK-12345", carrier=Carrier.FEDEX.value)
        assert fulfillment.status == FulfillmentStatus.SHIPPED
        assert fulfillment.tracking_number == "TRACK-12345"
        assert fulfillment.carrier == Carrier.FEDEX.value
        assert all(t.is_completed for t in fulfillment.tasks)

        events = fulfillment.pull_events()
        assert len(events) == 1
        assert isinstance(events[0], FulfillmentShipped)
        assert events[0].tracking_number == "TRACK-12345"

    def test_retry_and_failure_flow(self) -> None:
        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        task = _make_task(warehouse_id)

        fulfillment = Fulfillment.create(
            tenant_id=tenant_id,
            order_id=order_id,
            warehouse_id=warehouse_id,
            tasks=[task],
            max_retries=2,
        )
        fulfillment.allocate()
        fulfillment.start_processing()
        fulfillment.pull_events()

        # Transient failure 1
        fulfillment.record_failure("Carrier API timeout", is_permanent=False)
        assert fulfillment.status == FulfillmentStatus.FAILED
        assert fulfillment.retry_count == 1
        assert fulfillment.can_retry() is True
        events = fulfillment.pull_events()
        assert len(events) == 1
        assert isinstance(events[0], FulfillmentFailed)
        assert events[0].is_permanent is False

        # Retry transition
        fulfillment.retry()
        assert fulfillment.status == FulfillmentStatus.IN_PROGRESS

        # Transient failure 2
        fulfillment.record_failure("Label printer offline", is_permanent=False)
        assert fulfillment.status == FulfillmentStatus.FAILED
        assert fulfillment.retry_count == 2
        assert fulfillment.can_retry() is False  # max retries 2 reached

        with pytest.raises(InvalidFulfillmentTransition):
            fulfillment.retry()

        # Permanent compensation
        fulfillment.compensate(reason="Carrier dispatch failed after max retries")
        assert fulfillment.status == FulfillmentStatus.COMPENSATED
        events = fulfillment.pull_events()
        assert any(isinstance(e, FulfillmentCompensated) for e in events)

    def test_direct_permanent_failure(self) -> None:
        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        task = _make_task(warehouse_id)

        fulfillment = Fulfillment.create(
            tenant_id=tenant_id,
            order_id=order_id,
            warehouse_id=warehouse_id,
            tasks=[task],
        )
        fulfillment.allocate()
        fulfillment.start_processing()
        fulfillment.record_failure("Undeliverable address destination", is_permanent=True)

        assert fulfillment.status == FulfillmentStatus.FAILED
        assert fulfillment.can_retry() is False
