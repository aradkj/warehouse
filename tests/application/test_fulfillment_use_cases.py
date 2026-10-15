"""Application layer tests for Fulfillment use cases."""

from __future__ import annotations

import pytest
from tests.fakes.store import FakeStores, InMemoryIdempotencyStore

from fulfillment.adapters.in_memory import InMemoryFulfillmentRepository
from fulfillment.application.commands import (
    CompensateFulfillmentCommand,
    GetFulfillmentQuery,
    RequestFulfillmentCommand,
    RetryFulfillmentCommand,
    TaskRequirement,
)
from fulfillment.application.use_cases import (
    CompensateFulfillment,
    GetFulfillment,
    RequestFulfillment,
    RetryFulfillment,
)
from fulfillment.domain.value_objects import FulfillmentStatus
from shared.application.context import RequestContext
from shared.application.errors import NotFound
from shared.application.events import NullEventDispatcher
from shared.domain.clock import SystemClock
from shared.domain.identifiers import OrderId, SkuId, TenantId, WarehouseId, new_id
from shared.domain.value_objects import Quantity


class _SimpleFulfillmentUow:
    def __init__(self, repo: InMemoryFulfillmentRepository, idem: InMemoryIdempotencyStore) -> None:
        self.fulfillments = repo
        self.idempotency = idem
        self.committed = False

    async def __aenter__(self) -> _SimpleFulfillmentUow:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass


class TestFulfillmentUseCases:
    @pytest.mark.asyncio
    async def test_request_and_get_fulfillment(self) -> None:
        repo = InMemoryFulfillmentRepository()
        idem = InMemoryIdempotencyStore(FakeStores())
        clock = SystemClock()
        events = NullEventDispatcher()

        def uow_factory():
            return _SimpleFulfillmentUow(repo, idem)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        get_uc = GetFulfillment(uow_factory=uow_factory, clock=clock)

        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        sku_id = SkuId(new_id())
        context = RequestContext(tenant_id=tenant_id, correlation_id="req-123")

        cmd = RequestFulfillmentCommand(
            tenant_id=tenant_id,
            context=context,
            order_id=order_id,
            warehouse_id=warehouse_id,
            requirements=(TaskRequirement(sku_id=sku_id, quantity=Quantity(2)),),
            max_retries=3,
        )

        outcome = await request_uc(cmd)
        assert outcome.view.order_id == order_id
        assert outcome.view.status == FulfillmentStatus.ALLOCATED.value
        assert outcome.view.warehouse_id == warehouse_id

        # Query fulfillment
        query = GetFulfillmentQuery(
            tenant_id=tenant_id,
            context=context,
            fulfillment_id=outcome.view.fulfillment_id,
        )
        view = await get_uc(query)
        assert view.fulfillment_id == outcome.view.fulfillment_id
        assert view.status == FulfillmentStatus.ALLOCATED.value

    @pytest.mark.asyncio
    async def test_retry_and_compensate_fulfillment(self) -> None:
        repo = InMemoryFulfillmentRepository()
        idem = InMemoryIdempotencyStore(FakeStores())
        clock = SystemClock()
        events = NullEventDispatcher()

        def uow_factory():
            return _SimpleFulfillmentUow(repo, idem)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        retry_uc = RetryFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        compensate_uc = CompensateFulfillment(uow_factory=uow_factory, clock=clock, events=events)

        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        context = RequestContext(tenant_id=tenant_id, correlation_id="req-456")

        req_cmd = RequestFulfillmentCommand(
            tenant_id=tenant_id,
            context=context,
            order_id=order_id,
            warehouse_id=warehouse_id,
            requirements=(TaskRequirement(sku_id=SkuId(new_id()), quantity=Quantity(1)),),
        )
        req_outcome = await request_uc(req_cmd)
        f_id = req_outcome.view.fulfillment_id

        # Mark failure on domain aggregate
        agg = await repo.get(tenant_id=tenant_id, fulfillment_id=f_id)
        assert agg is not None
        v1 = agg.version
        agg.start_processing()
        agg.record_failure("Carrier failure", is_permanent=False)
        await repo.save(agg, expected_version=v1)

        # Retry
        retry_cmd = RetryFulfillmentCommand(
            tenant_id=tenant_id,
            context=context,
            fulfillment_id=f_id,
        )
        retry_outcome = await retry_uc(retry_cmd)
        assert retry_outcome.view.status == FulfillmentStatus.IN_PROGRESS.value

        # Mark permanent failure and compensate
        agg2 = await repo.get(tenant_id=tenant_id, fulfillment_id=f_id)
        assert agg2 is not None
        v2 = agg2.version
        agg2.record_failure("Carrier lost item", is_permanent=True)
        await repo.save(agg2, expected_version=v2)

        comp_cmd = CompensateFulfillmentCommand(
            tenant_id=tenant_id,
            context=context,
            fulfillment_id=f_id,
            reason="Order refunded after carrier loss",
        )
        comp_outcome = await compensate_uc(comp_cmd)
        assert comp_outcome.view.status == FulfillmentStatus.COMPENSATED.value
        assert comp_outcome.view.failure_reason == "Order refunded after carrier loss"

    @pytest.mark.asyncio
    async def test_fulfillment_cross_tenant_isolation(self) -> None:
        repo = InMemoryFulfillmentRepository()
        idem = InMemoryIdempotencyStore(FakeStores())
        clock = SystemClock()
        events = NullEventDispatcher()

        def uow_factory():
            return _SimpleFulfillmentUow(repo, idem)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        get_uc = GetFulfillment(uow_factory=uow_factory, clock=clock)

        tenant_1 = TenantId(new_id())
        tenant_2 = TenantId(new_id())
        context_1 = RequestContext(tenant_id=tenant_1, correlation_id="c1")
        context_2 = RequestContext(tenant_id=tenant_2, correlation_id="c2")

        cmd = RequestFulfillmentCommand(
            tenant_id=tenant_1,
            context=context_1,
            order_id=OrderId(new_id()),
            warehouse_id=WarehouseId(new_id()),
            requirements=(TaskRequirement(sku_id=SkuId(new_id()), quantity=Quantity(1)),),
        )
        outcome = await request_uc(cmd)

        # Tenant 2 attempting to read Tenant 1's fulfillment
        query = GetFulfillmentQuery(
            tenant_id=tenant_2,
            context=context_2,
            fulfillment_id=outcome.view.fulfillment_id,
        )
        with pytest.raises(NotFound):
            await get_uc(query)
