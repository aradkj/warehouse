"""Fulfillment use cases."""

from __future__ import annotations

from fulfillment.application.commands import (
    CompensateFulfillmentCommand,
    GetFulfillmentQuery,
    RequestFulfillmentCommand,
    RetryFulfillmentCommand,
)
from fulfillment.application.views import FulfillmentView
from fulfillment.domain.fulfillment import Fulfillment
from fulfillment.domain.value_objects import FulfillmentStatus, FulfillmentTask
from fulfillment.ports.unit_of_work import FulfillmentUnitOfWork
from ordering.ports.repositories import OrderStatus
from shared.application.errors import DuplicateResource, InvalidRequest, NotFound
from shared.application.idempotency import IdempotencyRecord, completed_resource_id
from shared.application.use_case import Executed, QueryUseCase, UseCase, pull_events
from shared.domain.identifiers import FulfillmentId, FulfillmentTaskId, new_id


class RequestFulfillment(
    UseCase[RequestFulfillmentCommand, FulfillmentView, FulfillmentUnitOfWork]
):
    """Initiate warehouse fulfillment for an order."""

    operation = "fulfillment.request_fulfillment"
    permission = "fulfillments.create"
    resource_type = "fulfillments"
    success_status = 201

    async def _execute(
        self,
        uow: FulfillmentUnitOfWork,
        command: RequestFulfillmentCommand,
    ) -> Executed[FulfillmentView]:
        # 1. Validate order existence, tenant ownership, and status
        order = await uow.orders.get(
            tenant_id=command.tenant_id,
            order_id=command.order_id,
        )
        if order is None:
            raise NotFound("order", command.order_id)
        if order.status != OrderStatus.CONFIRMED:
            raise InvalidRequest(
                f"order {command.order_id} in status '{order.status.value}' "
                "cannot be fulfilled; status must be 'CONFIRMED'",
                details={"order_id": str(command.order_id), "status": order.status.value},
            )

        # 2. Validate warehouse existence, tenant ownership, and active status
        warehouse = await uow.warehouses.get(
            tenant_id=command.tenant_id,
            warehouse_id=command.warehouse_id,
        )
        if warehouse is None:
            raise NotFound("warehouse", command.warehouse_id)
        if not warehouse.is_active:
            raise InvalidRequest(
                f"warehouse {command.warehouse_id} is inactive and cannot accept "
                "fulfillment requests",
                details={"warehouse_id": str(command.warehouse_id)},
            )

        # 3. Check for existing active fulfillment for the order
        existing_fulfillments = await uow.fulfillments.list_by_order(
            tenant_id=command.tenant_id,
            order_id=command.order_id,
        )
        for existing in existing_fulfillments:
            if existing.status in (
                FulfillmentStatus.PENDING,
                FulfillmentStatus.ALLOCATED,
                FulfillmentStatus.IN_PROGRESS,
            ):
                raise DuplicateResource(resource_type="fulfillment", field="order_id")

        # 4. Validate that requested requirements are part of order lines
        if order.lines:
            order_sku_ids = {line.sku_id for line in order.lines}
            for req in command.requirements:
                if req.sku_id not in order_sku_ids:
                    raise InvalidRequest(
                        f"SKU {req.sku_id} is not part of order {command.order_id}",
                        details={
                            "sku_id": str(req.sku_id),
                            "order_id": str(command.order_id),
                        },
                    )

        tasks = [
            FulfillmentTask(
                task_id=FulfillmentTaskId(new_id()),
                warehouse_id=command.warehouse_id,
                sku_id=req.sku_id,
                quantity=req.quantity,
            )
            for req in command.requirements
        ]
        fulfillment = Fulfillment.create(
            tenant_id=command.tenant_id,
            order_id=command.order_id,
            warehouse_id=command.warehouse_id,
            tasks=tasks,
            max_retries=command.max_retries,
            now=self._clock.now(),
        )
        fulfillment.allocate(now=self._clock.now())
        await uow.fulfillments.add(fulfillment)
        return Executed(
            view=FulfillmentView.from_aggregate(fulfillment),
            resource_type="fulfillments",
            resource_id=fulfillment.id,
            events=pull_events(fulfillment),
        )

    async def _reload(
        self,
        uow: FulfillmentUnitOfWork,
        record: IdempotencyRecord,
        command: RequestFulfillmentCommand,
    ) -> FulfillmentView:
        if record.resource_id is not None:
            f_id = FulfillmentId(completed_resource_id(record))
            item = await uow.fulfillments.get(tenant_id=command.tenant_id, fulfillment_id=f_id)
            if item is not None:
                return FulfillmentView.from_aggregate(item)
        items = await uow.fulfillments.list_by_order(
            tenant_id=command.tenant_id,
            order_id=command.order_id,
        )
        if items:
            return FulfillmentView.from_aggregate(items[0])
        raise NotFound("fulfillment", record.resource_id or command.order_id)


class RetryFulfillment(UseCase[RetryFulfillmentCommand, FulfillmentView, FulfillmentUnitOfWork]):
    """Retry a failed fulfillment."""

    operation = "fulfillment.retry_fulfillment"
    permission = "fulfillments.modify"
    resource_type = "fulfillments"
    success_status = 200

    async def _execute(
        self,
        uow: FulfillmentUnitOfWork,
        command: RetryFulfillmentCommand,
    ) -> Executed[FulfillmentView]:
        fulfillment = await uow.fulfillments.get_for_update(
            tenant_id=command.tenant_id,
            fulfillment_id=command.fulfillment_id,
        )
        if fulfillment is None:
            raise NotFound("fulfillment", command.fulfillment_id)

        # ABAC warehouse scoping check
        if self._authorizer is not None:
            await self._authorize(
                command.context,
                action=self.permission or "fulfillments.modify",
                resource_type=self.resource_type,
                resource_id=str(fulfillment.id),
                resource_tenant_id=fulfillment.tenant_id,
                resource_attributes={"warehouse_id": str(fulfillment.warehouse_id)},
            )

        expected_version = fulfillment.version
        fulfillment.retry(now=self._clock.now())
        await uow.fulfillments.save(fulfillment, expected_version=expected_version)
        return Executed(
            view=FulfillmentView.from_aggregate(fulfillment),
            resource_type="fulfillments",
            resource_id=fulfillment.id,
            events=pull_events(fulfillment),
        )

    async def _reload(
        self,
        uow: FulfillmentUnitOfWork,
        record: IdempotencyRecord,
        command: RetryFulfillmentCommand,
    ) -> FulfillmentView:
        item = await uow.fulfillments.get(
            tenant_id=command.tenant_id,
            fulfillment_id=command.fulfillment_id,
        )
        if item is None:
            raise NotFound("fulfillment", command.fulfillment_id)
        return FulfillmentView.from_aggregate(item)


class CompensateFulfillment(
    UseCase[CompensateFulfillmentCommand, FulfillmentView, FulfillmentUnitOfWork]
):
    """Trigger compensation workflow for permanently failed fulfillment."""

    operation = "fulfillment.compensate_fulfillment"
    permission = "fulfillments.modify"
    resource_type = "fulfillments"
    success_status = 200

    async def _execute(
        self,
        uow: FulfillmentUnitOfWork,
        command: CompensateFulfillmentCommand,
    ) -> Executed[FulfillmentView]:
        fulfillment = await uow.fulfillments.get_for_update(
            tenant_id=command.tenant_id,
            fulfillment_id=command.fulfillment_id,
        )
        if fulfillment is None:
            raise NotFound("fulfillment", command.fulfillment_id)

        expected_version = fulfillment.version
        fulfillment.compensate(command.reason, now=self._clock.now())
        await uow.fulfillments.save(fulfillment, expected_version=expected_version)
        return Executed(
            view=FulfillmentView.from_aggregate(fulfillment),
            resource_type="fulfillments",
            resource_id=fulfillment.id,
            events=pull_events(fulfillment),
        )

    async def _reload(
        self,
        uow: FulfillmentUnitOfWork,
        record: IdempotencyRecord,
        command: CompensateFulfillmentCommand,
    ) -> FulfillmentView:
        item = await uow.fulfillments.get(
            tenant_id=command.tenant_id,
            fulfillment_id=command.fulfillment_id,
        )
        if item is None:
            raise NotFound("fulfillment", command.fulfillment_id)
        return FulfillmentView.from_aggregate(item)


class GetFulfillment(QueryUseCase[GetFulfillmentQuery, FulfillmentView, FulfillmentUnitOfWork]):
    """Fetch fulfillment by ID."""

    permission = "fulfillments.read"
    resource_type = "fulfillments"

    async def _execute(
        self,
        uow: FulfillmentUnitOfWork,
        command: GetFulfillmentQuery,
    ) -> FulfillmentView:
        assert command.tenant_id is not None
        fulfillment = await uow.fulfillments.get(
            tenant_id=command.tenant_id,
            fulfillment_id=command.fulfillment_id,
        )
        if fulfillment is None:
            raise NotFound("fulfillment", command.fulfillment_id)

        if self._authorizer is not None:
            await self._authorize(
                command.context,
                action=self.permission or "fulfillments.read",
                resource_type=self.resource_type,
                resource_id=str(fulfillment.id),
                resource_tenant_id=fulfillment.tenant_id,
                resource_attributes={"warehouse_id": str(fulfillment.warehouse_id)},
            )

        return FulfillmentView.from_aggregate(fulfillment)


__all__ = [
    "CompensateFulfillment",
    "GetFulfillment",
    "RequestFulfillment",
    "RetryFulfillment",
]
