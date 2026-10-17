"""Application layer tests for Fulfillment use cases."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

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
from ordering.domain.value_objects import OrderStatus
from shared.application.context import RequestContext
from shared.application.errors import DuplicateResource, InvalidRequest, NotFound
from shared.application.events import NullEventDispatcher
from shared.domain.clock import SystemClock
from shared.domain.identifiers import OrderId, SkuId, TenantId, WarehouseId, new_id
from shared.domain.value_objects import Quantity


class _FakeOrderRepo:
    def __init__(self, orders: dict[tuple[TenantId, OrderId], Any] | None = None) -> None:
        self._orders = orders or {}

    async def get(self, *, tenant_id: TenantId, order_id: OrderId) -> Any | None:
        return self._orders.get((tenant_id, order_id))


class _FakeWarehouseRepo:
    def __init__(self, warehouses: dict[tuple[TenantId, WarehouseId], Any] | None = None) -> None:
        self._warehouses = warehouses or {}

    async def get(
        self, *, tenant_id: TenantId, warehouse_id: WarehouseId, for_update: bool = False
    ) -> Any | None:
        return self._warehouses.get((tenant_id, warehouse_id))


class _SimpleFulfillmentUow:
    def __init__(
        self,
        repo: InMemoryFulfillmentRepository,
        idem: InMemoryIdempotencyStore,
        orders: _FakeOrderRepo | None = None,
        warehouses: _FakeWarehouseRepo | None = None,
    ) -> None:
        self.fulfillments = repo
        self.idempotency = idem
        self.orders = orders or _FakeOrderRepo()
        self.warehouses = warehouses or _FakeWarehouseRepo()
        self.committed = False

    async def __aenter__(self) -> _SimpleFulfillmentUow:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass


def _make_confirmed_order(
    tenant_id: TenantId,
    order_id: OrderId,
    sku_ids: list[SkuId],
    status: OrderStatus = OrderStatus.CONFIRMED,
) -> Any:
    return SimpleNamespace(
        id=order_id,
        tenant_id=tenant_id,
        status=status,
        lines=[SimpleNamespace(sku_id=s, quantity=10) for s in sku_ids],
    )


def _make_warehouse(
    tenant_id: TenantId,
    warehouse_id: WarehouseId,
    is_active: bool = True,
) -> Any:
    return SimpleNamespace(
        id=warehouse_id,
        tenant_id=tenant_id,
        is_active=is_active,
    )


class TestFulfillmentUseCases:
    @pytest.mark.asyncio
    async def test_request_and_get_fulfillment(self) -> None:
        repo = InMemoryFulfillmentRepository()
        idem = InMemoryIdempotencyStore(FakeStores())
        clock = SystemClock()
        events = NullEventDispatcher()

        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        sku_id = SkuId(new_id())
        context = RequestContext(tenant_id=tenant_id, correlation_id="req-123")

        order_repo = _FakeOrderRepo(
            {(tenant_id, order_id): _make_confirmed_order(tenant_id, order_id, [sku_id])}
        )
        wh_repo = _FakeWarehouseRepo(
            {(tenant_id, warehouse_id): _make_warehouse(tenant_id, warehouse_id, is_active=True)}
        )

        def uow_factory() -> _SimpleFulfillmentUow:
            return _SimpleFulfillmentUow(repo, idem, order_repo, wh_repo)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        get_uc = GetFulfillment(uow_factory=uow_factory, clock=clock)

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

        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        sku_id = SkuId(new_id())
        context = RequestContext(tenant_id=tenant_id, correlation_id="req-456")

        order_repo = _FakeOrderRepo(
            {(tenant_id, order_id): _make_confirmed_order(tenant_id, order_id, [sku_id])}
        )
        wh_repo = _FakeWarehouseRepo(
            {(tenant_id, warehouse_id): _make_warehouse(tenant_id, warehouse_id, is_active=True)}
        )

        def uow_factory() -> _SimpleFulfillmentUow:
            return _SimpleFulfillmentUow(repo, idem, order_repo, wh_repo)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        retry_uc = RetryFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        compensate_uc = CompensateFulfillment(uow_factory=uow_factory, clock=clock, events=events)

        req_cmd = RequestFulfillmentCommand(
            tenant_id=tenant_id,
            context=context,
            order_id=order_id,
            warehouse_id=warehouse_id,
            requirements=(TaskRequirement(sku_id=sku_id, quantity=Quantity(1)),),
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

        tenant_1 = TenantId(new_id())
        tenant_2 = TenantId(new_id())
        context_1 = RequestContext(tenant_id=tenant_1, correlation_id="c1")
        context_2 = RequestContext(tenant_id=tenant_2, correlation_id="c2")

        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        sku_id = SkuId(new_id())

        order_repo = _FakeOrderRepo(
            {(tenant_1, order_id): _make_confirmed_order(tenant_1, order_id, [sku_id])}
        )
        wh_repo = _FakeWarehouseRepo(
            {(tenant_1, warehouse_id): _make_warehouse(tenant_1, warehouse_id, is_active=True)}
        )

        def uow_factory() -> _SimpleFulfillmentUow:
            return _SimpleFulfillmentUow(repo, idem, order_repo, wh_repo)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        get_uc = GetFulfillment(uow_factory=uow_factory, clock=clock)

        cmd = RequestFulfillmentCommand(
            tenant_id=tenant_1,
            context=context_1,
            order_id=order_id,
            warehouse_id=warehouse_id,
            requirements=(TaskRequirement(sku_id=sku_id, quantity=Quantity(1)),),
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

    @pytest.mark.asyncio
    async def test_request_fulfillment_missing_order_raises_not_found(self) -> None:
        repo = InMemoryFulfillmentRepository()
        idem = InMemoryIdempotencyStore(FakeStores())
        clock = SystemClock()
        events = NullEventDispatcher()

        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        sku_id = SkuId(new_id())

        wh_repo = _FakeWarehouseRepo(
            {(tenant_id, warehouse_id): _make_warehouse(tenant_id, warehouse_id, is_active=True)}
        )
        order_repo = _FakeOrderRepo()  # empty

        def uow_factory() -> _SimpleFulfillmentUow:
            return _SimpleFulfillmentUow(repo, idem, order_repo, wh_repo)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        cmd = RequestFulfillmentCommand(
            tenant_id=tenant_id,
            context=RequestContext(tenant_id=tenant_id),
            order_id=order_id,
            warehouse_id=warehouse_id,
            requirements=(TaskRequirement(sku_id=sku_id, quantity=Quantity(1)),),
        )
        with pytest.raises(NotFound) as exc_info:
            await request_uc(cmd)
        assert exc_info.value.code == "order_not_found"

    @pytest.mark.asyncio
    async def test_request_fulfillment_invalid_order_status_raises_invalid_request(self) -> None:
        repo = InMemoryFulfillmentRepository()
        idem = InMemoryIdempotencyStore(FakeStores())
        clock = SystemClock()
        events = NullEventDispatcher()

        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        sku_id = SkuId(new_id())

        order_repo = _FakeOrderRepo(
            {
                (tenant_id, order_id): _make_confirmed_order(
                    tenant_id, order_id, [sku_id], status=OrderStatus.PENDING
                )
            }
        )
        wh_repo = _FakeWarehouseRepo(
            {(tenant_id, warehouse_id): _make_warehouse(tenant_id, warehouse_id, is_active=True)}
        )

        def uow_factory() -> _SimpleFulfillmentUow:
            return _SimpleFulfillmentUow(repo, idem, order_repo, wh_repo)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        cmd = RequestFulfillmentCommand(
            tenant_id=tenant_id,
            context=RequestContext(tenant_id=tenant_id),
            order_id=order_id,
            warehouse_id=warehouse_id,
            requirements=(TaskRequirement(sku_id=sku_id, quantity=Quantity(1)),),
        )
        with pytest.raises(InvalidRequest, match="status must be 'CONFIRMED'"):
            await request_uc(cmd)

    @pytest.mark.asyncio
    async def test_request_fulfillment_missing_warehouse_raises_not_found(self) -> None:
        repo = InMemoryFulfillmentRepository()
        idem = InMemoryIdempotencyStore(FakeStores())
        clock = SystemClock()
        events = NullEventDispatcher()

        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        sku_id = SkuId(new_id())

        order_repo = _FakeOrderRepo(
            {(tenant_id, order_id): _make_confirmed_order(tenant_id, order_id, [sku_id])}
        )
        wh_repo = _FakeWarehouseRepo()  # empty

        def uow_factory() -> _SimpleFulfillmentUow:
            return _SimpleFulfillmentUow(repo, idem, order_repo, wh_repo)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        cmd = RequestFulfillmentCommand(
            tenant_id=tenant_id,
            context=RequestContext(tenant_id=tenant_id),
            order_id=order_id,
            warehouse_id=warehouse_id,
            requirements=(TaskRequirement(sku_id=sku_id, quantity=Quantity(1)),),
        )
        with pytest.raises(NotFound) as exc_info:
            await request_uc(cmd)
        assert exc_info.value.code == "warehouse_not_found"

    @pytest.mark.asyncio
    async def test_request_fulfillment_inactive_warehouse_raises_invalid_request(self) -> None:
        repo = InMemoryFulfillmentRepository()
        idem = InMemoryIdempotencyStore(FakeStores())
        clock = SystemClock()
        events = NullEventDispatcher()

        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        sku_id = SkuId(new_id())

        order_repo = _FakeOrderRepo(
            {(tenant_id, order_id): _make_confirmed_order(tenant_id, order_id, [sku_id])}
        )
        wh_repo = _FakeWarehouseRepo(
            {(tenant_id, warehouse_id): _make_warehouse(tenant_id, warehouse_id, is_active=False)}
        )

        def uow_factory() -> _SimpleFulfillmentUow:
            return _SimpleFulfillmentUow(repo, idem, order_repo, wh_repo)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        cmd = RequestFulfillmentCommand(
            tenant_id=tenant_id,
            context=RequestContext(tenant_id=tenant_id),
            order_id=order_id,
            warehouse_id=warehouse_id,
            requirements=(TaskRequirement(sku_id=sku_id, quantity=Quantity(1)),),
        )
        with pytest.raises(InvalidRequest, match="is inactive"):
            await request_uc(cmd)

    @pytest.mark.asyncio
    async def test_request_fulfillment_duplicate_active_fulfillment_rejected(self) -> None:
        repo = InMemoryFulfillmentRepository()
        idem = InMemoryIdempotencyStore(FakeStores())
        clock = SystemClock()
        events = NullEventDispatcher()

        tenant_id = TenantId(new_id())
        order_id = OrderId(new_id())
        warehouse_id = WarehouseId(new_id())
        sku_id = SkuId(new_id())

        order_repo = _FakeOrderRepo(
            {(tenant_id, order_id): _make_confirmed_order(tenant_id, order_id, [sku_id])}
        )
        wh_repo = _FakeWarehouseRepo(
            {(tenant_id, warehouse_id): _make_warehouse(tenant_id, warehouse_id, is_active=True)}
        )

        def uow_factory() -> _SimpleFulfillmentUow:
            return _SimpleFulfillmentUow(repo, idem, order_repo, wh_repo)

        request_uc = RequestFulfillment(uow_factory=uow_factory, clock=clock, events=events)
        cmd = RequestFulfillmentCommand(
            tenant_id=tenant_id,
            context=RequestContext(tenant_id=tenant_id),
            order_id=order_id,
            warehouse_id=warehouse_id,
            requirements=(TaskRequirement(sku_id=sku_id, quantity=Quantity(1)),),
        )

        # First request succeeds
        outcome = await request_uc(cmd)
        assert outcome.view.status == FulfillmentStatus.ALLOCATED.value

        # Second request with a different idempotency key or command rejects duplicate active fulfillment
        with pytest.raises(DuplicateResource) as exc_info:
            await request_uc(cmd)
        assert exc_info.value.resource_type == "fulfillment"
        assert exc_info.value.field == "order_id"
