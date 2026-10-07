"""In-process implementation of inventory's published contract.

This adapter is what makes the modular monolith *modular*: Ordering depends on
:class:`inventory.ports.publication.InventoryOperations`, and this class fulfils
that port by delegating to inventory's own use cases over the **caller's**
transaction scope. The reservation and the order therefore commit together.

It is a thin translator on purpose - no business logic lives here, and it depends
on inventory's *port* (``InventoryUnitOfWork``) rather than on a concrete
SQLAlchemy unit of work, so the very same translator is used by the application
test suite over in-memory fakes. When inventory becomes a service, this class is
replaced by an HTTP client that implements the same protocol, and nothing else
changes.
"""

from __future__ import annotations

from collections.abc import Callable

from inventory.application.commands import (
    ConfirmReservationCommand,
    ReleaseReservationCommand,
    ReservationView,
    ReserveInventoryCommand,
)
from inventory.application.use_cases import (
    ConfirmReservation,
    ReleaseReservation,
    ReserveInventory,
)
from inventory.ports.publication import (
    InventoryOperations,
    ReservationOutcome,
    ReservationRequest,
)
from inventory.ports.unit_of_work import InventoryUnitOfWork
from shared.application.context import RequestContext
from shared.domain.clock import Clock
from shared.domain.identifiers import OrderId, ReservationId, TenantId


class InProcessInventoryOperations(InventoryOperations):
    """``InventoryOperations`` over a shared transaction scope (no commit)."""

    __slots__ = ("_confirm", "_release", "_reserve", "_uow_factory")

    @staticmethod
    def _nested_context(context: RequestContext) -> RequestContext:
        """Derive the context for the nested inventory use case.

        The outer request's ``Idempotency-Key`` is claimed for the *outer*
        operation (``ordering.create_order``). Re-using it here would collide with
        that claim - the caller's key is exactly what makes the whole request
        idempotent, and a nested call is not a new client request. The nested call
        therefore runs without a key and records the outer correlation id as its
        cause instead.
        """
        return context.child(
            idempotency_key=None,
            causation_id=context.causation_id or context.correlation_id,
        )

    def __init__(
        self,
        *,
        uow_factory: Callable[[], InventoryUnitOfWork],
        clock: Clock,
        default_ttl_seconds: int = 1800,
        max_ttl_seconds: int = 86_400,
    ) -> None:
        self._uow_factory = uow_factory
        self._reserve = ReserveInventory(
            uow_factory=uow_factory,
            clock=clock,
            default_ttl_seconds=default_ttl_seconds,
            max_ttl_seconds=max_ttl_seconds,
        )
        self._confirm = ConfirmReservation(uow_factory=uow_factory, clock=clock)
        self._release = ReleaseReservation(uow_factory=uow_factory, clock=clock)

    async def reserve(
        self, request: ReservationRequest, context: RequestContext
    ) -> ReservationOutcome:
        outcome = await self._reserve(
            ReserveInventoryCommand(
                tenant_id=request.tenant_id,
                context=self._nested_context(context),
                requirements=request.requirements,
                order_id=request.order_id,
                warehouse_ids=request.warehouse_ids,
                strategy=request.strategy,
                ttl_seconds=request.ttl_seconds,
            )
        )
        view = outcome.view
        return ReservationOutcome(
            reservation=view.reservation,
            shortfalls=view.shortfalls,
            is_complete=view.is_complete,
        )

    async def confirm_reservation(
        self, *, tenant_id: TenantId, reservation_id: ReservationId, context: RequestContext
    ) -> ReservationView:
        outcome = await self._confirm(
            ConfirmReservationCommand(
                tenant_id=tenant_id,
                context=self._nested_context(context),
                reservation_id=reservation_id,
            )
        )
        return outcome.view

    async def release_reservation(
        self,
        *,
        tenant_id: TenantId,
        reservation_id: ReservationId,
        reason: str,
        context: RequestContext,
    ) -> ReservationView:
        outcome = await self._release(
            ReleaseReservationCommand(
                tenant_id=tenant_id,
                context=self._nested_context(context),
                reservation_id=reservation_id,
                reason=reason,
            )
        )
        return outcome.view

    async def reservation_for_order(
        self, *, tenant_id: TenantId, order_id: OrderId, context: RequestContext
    ) -> ReservationView | None:
        uow = self._uow_factory()
        async with uow:
            reservations = await uow.reservations.list_for_order(
                tenant_id=tenant_id, order_id=order_id
            )
        if not reservations:
            return None
        # ``list_for_order`` is ordered by creation time: the last one is the
        # current reservation for the order.
        return ReservationView.of(reservations[-1])


__all__ = ["InProcessInventoryOperations"]
