"""Ordering use cases.

The submission flow implemented here is exactly the one in the specification:

1. validate the request and read prices from the catalog,
2. create the order (``PENDING``) with a pricing snapshot,
3. ask inventory for an atomic reservation across warehouses,
4. confirm when *all* lines are held, or record a back-order / reject according
   to configuration,
5. commit everything - order, hold, ledger and idempotency record - in one
   transaction.

Everything happens inside a single unit of work: if the reservation fails, the
order does not exist either.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any

# Ordering talks to the other contexts through their published ports only.
from catalog.ports.publication import ProductNotPurchasable
from inventory.ports.publication import (
    AllocationStrategy,
    ReservationRequest,
    ShortfallView,
    StockRequirement,
)
from ordering.application.commands import (
    CancelOrderCommand,
    ConfirmOrderCommand,
    CreateOrderCommand,
    OrderView,
)
from ordering.domain.order import Order
from ordering.domain.policies import UnavailableStockPolicy, coverage_by_line
from ordering.domain.value_objects import OrderLine, OrderStatus
from ordering.ports.unit_of_work import OrderingUnitOfWork
from shared.application.errors import ConcurrencyConflict, InvalidRequest, NotFound
from shared.application.events import EventDispatcher
from shared.application.idempotency import IdempotencyRecord, completed_resource_id
from shared.application.use_case import (
    DEFAULT_IDEMPOTENCY_RETENTION,
    Executed,
    UseCase,
    pull_events,
)
from shared.domain.clock import Clock
from shared.domain.identifiers import OrderId, OrderLineId, TenantId
from shared.domain.value_objects import Quantity


class CreateOrder(UseCase[CreateOrderCommand, OrderView, OrderingUnitOfWork]):
    """Submit an order and try to secure its stock immediately."""

    operation = "ordering.create_order"
    permission = "orders.create"
    resource_type = "orders"
    success_status = 201
    #: The specification's flow says a channel submits an order *with* a
    #: tenant-scoped idempotency key; the endpoint enforces it.
    idempotency_required = True

    def __init__(
        self,
        *,
        uow_factory: Callable[[], OrderingUnitOfWork],
        clock: Clock,
        events: EventDispatcher | None = None,
        idempotency_retention: timedelta = DEFAULT_IDEMPOTENCY_RETENTION,
        unavailable_policy: UnavailableStockPolicy = UnavailableStockPolicy.BACKORDER,
        authorizer: Any | None = None,
    ) -> None:
        super().__init__(
            uow_factory=uow_factory,
            clock=clock,
            events=events,
            idempotency_retention=idempotency_retention,
            authorizer=authorizer,
        )
        self._unavailable_policy = unavailable_policy

    async def _execute(
        self, uow: OrderingUnitOfWork, command: CreateOrderCommand
    ) -> Executed[OrderView]:
        now = self._clock.now()
        if not command.lines:
            raise InvalidRequest("an order must contain at least one line")
        for line in command.lines:
            if line.quantity < 1:
                raise InvalidRequest(
                    "order line quantities must be positive",
                    details={"sku_id": str(line.sku_id), "quantity": line.quantity},
                )

        sku_ids = tuple(dict.fromkeys(line.sku_id for line in command.lines))
        catalog = await uow.catalog.purchasable_skus(tenant_id=command.tenant_id, sku_ids=sku_ids)
        missing = [sku_id for sku_id in sku_ids if sku_id not in catalog]
        if missing:
            # A SKU that is unknown *in this tenant* is reported as not found:
            # the API must not reveal whether it exists elsewhere.
            raise NotFound(
                "sku",
                missing[0],
                details={"missing_sku_ids": [str(sku_id) for sku_id in missing]},
            )
        for sku_id in sku_ids:
            entry = catalog[sku_id]
            if not entry.is_purchasable:
                raise ProductNotPurchasable(
                    sku_id, reason=entry.not_purchasable_reason or "unavailable"
                )

        total_minor = sum(
            catalog[line.sku_id].unit_price.amount_minor * line.quantity for line in command.lines
        )
        currency = catalog[sku_ids[0]].unit_price.currency

        await self._authorize(
            command.context,
            action="orders.create",
            resource_type="orders",
            resource_tenant_id=command.tenant_id,
            resource_attributes={
                "line_count": len(command.lines),
                "customer_reference": command.customer_reference,
                "total_minor": total_minor,
                "currency": currency,
            },
        )
        lines = tuple(
            OrderLine.create(
                line_number=index,
                sku_id=request.sku_id,
                product_id=catalog[request.sku_id].product_id,
                sku_code=catalog[request.sku_id].sku_code,
                product_name=catalog[request.sku_id].product_name,
                quantity=Quantity(request.quantity),
                unit_price=catalog[request.sku_id].unit_price,
                now=now,
            )
            for index, request in enumerate(command.lines, start=1)
        )

        order = Order.place(
            tenant_id=command.tenant_id,
            customer_id=None,
            customer_reference=command.customer_reference,
            currency=currency,
            lines=lines,
            now=now,
        )

        # The order row is written *before* stock is reserved because the
        # reservation carries a real foreign key to it (``reservations.order_id``):
        # PostgreSQL enforces that reference immediately. Both writes share one
        # transaction, so a failure anywhere below rolls the order back as well -
        # there is no window in which an order exists without its reservation
        # attempt, and no orphan reservation can survive a failed submission.
        created_version = order.version
        await uow.orders.add(order)

        strategy = (
            AllocationStrategy.PARTIAL
            if self._unavailable_policy is UnavailableStockPolicy.BACKORDER
            else AllocationStrategy.ALL_OR_NOTHING
        )
        outcome = await uow.inventory.reserve(
            ReservationRequest(
                tenant_id=command.tenant_id,
                requirements=tuple(
                    StockRequirement(
                        sku_id=line.sku_id,
                        quantity=line.quantity,
                        order_line_id=line.id,
                    )
                    for line in order.lines
                ),
                order_id=order.id,
                strategy=strategy,
            ),
            command.context,
        )

        reservation = outcome.reservation
        if outcome.is_complete and reservation is not None:
            order.mark_inventory_reserved(reservation_id=reservation.id, now=now)
            order.confirm(coverage=coverage_by_line(reservation.covered_lines()), now=now)
        else:
            # Only reachable with the BACKORDER policy: with ALL_OR_NOTHING the
            # inventory context raises InsufficientInventory instead.
            order.mark_backordered(shortfalls=_shortfall_payload(outcome.shortfalls), now=now)

        # Persist the state transitions performed above, guarded by the version
        # the aggregate had when it was inserted.
        await uow.orders.save(order, expected_version=created_version)
        return Executed(
            view=OrderView.of(order, reservation=reservation),
            resource_type="order",
            resource_id=order.id,
            events=pull_events(order),
        )

    async def _reload(
        self, uow: OrderingUnitOfWork, record: IdempotencyRecord, command: CreateOrderCommand
    ) -> OrderView:
        order = await uow.orders.get(
            tenant_id=command.tenant_id, order_id=OrderId(completed_resource_id(record))
        )
        if order is None:  # pragma: no cover - orders are never deleted
            raise NotFound("order", record.resource_id)
        reservation = await uow.inventory.reservation_for_order(
            tenant_id=command.tenant_id, order_id=order.id, context=command.context
        )
        return OrderView.of(order, reservation=reservation)


class ConfirmOrder(UseCase[ConfirmOrderCommand, OrderView, OrderingUnitOfWork]):
    """Confirm an order once every line is genuinely held.

    * ``PENDING``/``BACKORDERED`` - try to secure the stock now (all or nothing,
      replacing any partial hold with a complete one).
    * ``INVENTORY_RESERVED``    - validate coverage and confirm.

    Confirming an order does **not** consume its reservation: the units are still
    physically in the warehouse, and ``on_hand`` is a count of physical stock. The
    hold stays ``ACTIVE`` (which is what makes the invariant "a confirmed order has
    valid reservations for all its lines" true) and is consumed when fulfilment
    ships the order in Phase 2 - see ``docs/decisions/0004-order-lifecycle.md``.
    """

    operation = "ordering.confirm_order"
    permission = "orders.confirm"
    resource_type = "orders"
    success_status = 200

    async def _execute(
        self, uow: OrderingUnitOfWork, command: ConfirmOrderCommand
    ) -> Executed[OrderView]:
        now = self._clock.now()
        order = await _require_order_for_update(uow, command.tenant_id, command.order_id)
        expected_version = order.version

        await self._authorize(
            command.context,
            action="orders.confirm",
            resource_type="orders",
            resource_id=str(order.id),
            resource_tenant_id=order.tenant_id,
            resource_attributes={
                "status": order.status.value,
                "total_minor": order.total.amount_minor,
                "currency": order.total.currency,
                "line_count": len(order.lines),
            },
        )

        reservation = await uow.inventory.reservation_for_order(
            tenant_id=command.tenant_id, order_id=order.id, context=command.context
        )

        if order.status in (OrderStatus.PENDING, OrderStatus.BACKORDERED):
            # A partial hold must go before a complete one can be taken: the
            # database allows a single ACTIVE reservation per order.
            if reservation is not None and reservation.is_active and now < reservation.expires_at:
                await uow.inventory.release_reservation(
                    tenant_id=command.tenant_id,
                    reservation_id=reservation.id,
                    reason="superseded_by_confirmation",
                    context=command.context,
                )
                reservation = None
            outcome = await uow.inventory.reserve(
                ReservationRequest(
                    tenant_id=command.tenant_id,
                    requirements=tuple(
                        StockRequirement(
                            sku_id=line.sku_id,
                            quantity=line.quantity,
                            order_line_id=line.id,
                        )
                        for line in order.lines
                    ),
                    order_id=order.id,
                    strategy=AllocationStrategy.ALL_OR_NOTHING,
                ),
                command.context,
            )
            reservation = outcome.reservation
            order.mark_inventory_reserved(
                reservation_id=reservation.id if reservation is not None else None, now=now
            )

        coverage: Mapping[OrderLineId, int] = (
            coverage_by_line(reservation.covered_lines()) if reservation is not None else {}
        )
        # Raises OrderLinesNotReserved when a line is not fully covered, and
        # InvalidOrderTransition when the order is not in a confirmable state.
        order.confirm(coverage=coverage, now=now)

        await uow.orders.save(order, expected_version=expected_version)
        return Executed(
            view=OrderView.of(order, reservation=reservation),
            resource_type="order",
            resource_id=order.id,
            events=pull_events(order),
        )

    async def _reload(
        self, uow: OrderingUnitOfWork, record: IdempotencyRecord, command: ConfirmOrderCommand
    ) -> OrderView:
        order = await uow.orders.get(
            tenant_id=command.tenant_id, order_id=OrderId(completed_resource_id(record))
        )
        if order is None:  # pragma: no cover
            raise NotFound("order", record.resource_id)
        reservation = await uow.inventory.reservation_for_order(
            tenant_id=command.tenant_id, order_id=order.id, context=command.context
        )
        return OrderView.of(order, reservation=reservation)


class CancelOrder(UseCase[CancelOrderCommand, OrderView, OrderingUnitOfWork]):
    """Cancel an order and release whatever stock it still holds.

    Cancelling an already cancelled order returns its current state (the
    operation is idempotent); cancelling a *confirmed* order is refused - undoing
    a confirmed order is a compensating workflow, not a state flip.
    """

    operation = "ordering.cancel_order"
    permission = "orders.cancel"
    resource_type = "orders"
    success_status = 200

    async def _execute(
        self, uow: OrderingUnitOfWork, command: CancelOrderCommand
    ) -> Executed[OrderView]:
        now = self._clock.now()
        order = await _require_order_for_update(uow, command.tenant_id, command.order_id)
        if command.expected_version is not None and command.expected_version != order.version:
            raise ConcurrencyConflict(
                "the order was modified by another request",
                details={
                    "expected_version": command.expected_version,
                    "actual_version": order.version,
                },
            )

        await self._authorize(
            command.context,
            action="orders.cancel",
            resource_type="orders",
            resource_id=str(order.id),
            resource_tenant_id=order.tenant_id,
            resource_attributes={
                "status": order.status.value,
                "total_minor": order.total.amount_minor,
                "currency": order.total.currency,
            },
        )

        reservation = await uow.inventory.reservation_for_order(
            tenant_id=command.tenant_id, order_id=order.id, context=command.context
        )
        if order.status.is_terminal and order.cancelled_at is not None:
            return Executed(
                view=OrderView.of(order, reservation=reservation),
                resource_type="order",
                resource_id=order.id,
                events=(),
            )

        expected_version = order.version
        order.cancel(reason=command.reason, now=now)
        if reservation is not None and reservation.is_active and now < reservation.expires_at:
            await uow.inventory.release_reservation(
                tenant_id=command.tenant_id,
                reservation_id=reservation.id,
                reason=f"order_cancelled:{order.id}",
                context=command.context,
            )
            reservation = await uow.inventory.reservation_for_order(
                tenant_id=command.tenant_id, order_id=order.id, context=command.context
            )
        await uow.orders.save(order, expected_version=expected_version)
        return Executed(
            view=OrderView.of(order, reservation=reservation),
            resource_type="order",
            resource_id=order.id,
            events=pull_events(order),
        )

    async def _reload(
        self, uow: OrderingUnitOfWork, record: IdempotencyRecord, command: CancelOrderCommand
    ) -> OrderView:
        order = await uow.orders.get(
            tenant_id=command.tenant_id, order_id=OrderId(completed_resource_id(record))
        )
        if order is None:  # pragma: no cover
            raise NotFound("order", record.resource_id)
        reservation = await uow.inventory.reservation_for_order(
            tenant_id=command.tenant_id, order_id=order.id, context=command.context
        )
        return OrderView.of(order, reservation=reservation)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _require_order_for_update(
    uow: OrderingUnitOfWork, tenant_id: TenantId, order_id: OrderId
) -> Order:
    order = await uow.orders.get_for_update(tenant_id=tenant_id, order_id=order_id)
    if order is None:
        raise NotFound("order", order_id)
    return order


def _shortfall_payload(shortfalls: Sequence[ShortfallView]) -> list[dict[str, object]]:
    """Render inventory shortfalls as plain data for the domain event."""
    payload: list[dict[str, object]] = []
    for shortfall in shortfalls:
        payload.append(
            {
                "sku_id": str(shortfall.sku_id),
                "required": shortfall.required,
                "allocated": shortfall.allocated,
                "missing": shortfall.missing,
            }
        )
    return payload


__all__ = ["CancelOrder", "ConfirmOrder", "CreateOrder"]
