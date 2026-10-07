"""Inventory use cases.

The reservation use case is the one that matters most: it is the only place in
the platform that decides whether stock can be promised. Its shape is deliberate:

1. lock every candidate stock row (``ORDER BY id`` - deterministic lock order),
2. run the pure allocation policy over the *locked* availability,
3. mutate the aggregates (which refuse to oversell),
4. persist stock rows, their ledger entries and the reservation in one transaction.

No Redis lock, no application-level mutex, no ``SELECT`` then ``UPDATE`` race:
the row lock held from step 1 until commit is the mutual exclusion.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import timedelta

from inventory.application.commands import (
    AdjustInventoryCommand,
    AdjustmentView,
    ConfirmReservationCommand,
    ExpireReservationsCommand,
    RegisterWarehouseCommand,
    ReleaseReservationCommand,
    ReservationResultView,
    ReservationView,
    ReserveInventoryCommand,
    ShortfallView,
    WarehouseView,
)
from inventory.domain.allocation import InventoryAllocationPolicy
from inventory.domain.errors import InsufficientInventory, WarehouseNotActive
from inventory.domain.inventory_item import InventoryItem
from inventory.domain.reservation import Reservation
from inventory.domain.value_objects import (
    AllocationCandidate,
    ReservationStatus,
)
from inventory.domain.warehouse import Warehouse
from inventory.ports.unit_of_work import InventoryUnitOfWork
from shared.application.errors import InvalidRequest, NotFound
from shared.application.events import EventDispatcher
from shared.application.idempotency import IdempotencyRecord, completed_resource_id
from shared.application.use_case import (
    DEFAULT_IDEMPOTENCY_RETENTION,
    Executed,
    UseCase,
    pull_events,
)
from shared.domain.clock import Clock
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import (
    InventoryItemId,
    ReservationId,
    TenantId,
    WarehouseId,
)


class RegisterWarehouse(UseCase[RegisterWarehouseCommand, WarehouseView, InventoryUnitOfWork]):
    """Create a stocking location."""

    operation = "inventory.register_warehouse"
    success_status = 201

    async def _execute(
        self, uow: InventoryUnitOfWork, command: RegisterWarehouseCommand
    ) -> Executed[WarehouseView]:
        warehouse = Warehouse.register(
            tenant_id=command.tenant_id,
            code=command.code,
            name=command.name,
            now=self._clock.now(),
        )
        await uow.warehouses.add(warehouse)
        return Executed(
            view=WarehouseView.of(warehouse),
            resource_type="warehouse",
            resource_id=warehouse.id,
            events=pull_events(warehouse),
        )

    async def _reload(
        self, uow: InventoryUnitOfWork, record: IdempotencyRecord, command: RegisterWarehouseCommand
    ) -> WarehouseView:
        warehouse = await uow.warehouses.get(
            tenant_id=command.tenant_id, warehouse_id=WarehouseId(completed_resource_id(record))
        )
        if warehouse is None:  # pragma: no cover - warehouses are never deleted
            raise NotFound("warehouse", record.resource_id)
        return WarehouseView.of(warehouse)


class AdjustInventory(UseCase[AdjustInventoryCommand, AdjustmentView, InventoryUnitOfWork]):
    """Apply a signed stock correction.

    The stock row is created on first use: receiving goods is what brings a
    SKU/warehouse pair into existence. The SKU must exist in the caller's tenant
    and the warehouse must exist and be active.
    """

    operation = "inventory.adjust_stock"
    success_status = 200

    async def _execute(
        self, uow: InventoryUnitOfWork, command: AdjustInventoryCommand
    ) -> Executed[AdjustmentView]:
        now = self._clock.now()
        if command.quantity_delta == 0:
            raise InvalidRequest("quantity_delta must not be zero")
        await _require_active_warehouse(uow, command.tenant_id, command.warehouse_id)
        if not await uow.sku_exists(tenant_id=command.tenant_id, sku_id=command.sku_id):
            raise NotFound("sku", command.sku_id)

        item, _created = await uow.inventory_items.get_or_register_for_update(
            tenant_id=command.tenant_id,
            warehouse_id=command.warehouse_id,
            sku_id=command.sku_id,
            now=now,
        )
        expected_version = item.version
        movement = item.adjust(
            delta=command.quantity_delta,
            reason=command.reason,
            note=command.note,
            now=now,
        )
        await uow.inventory_items.save(item, expected_version=expected_version)
        return Executed(
            view=AdjustmentView.of(item, movement),
            resource_type="inventory_item",
            resource_id=item.id,
            events=pull_events(item),
        )

    async def _reload(
        self,
        uow: InventoryUnitOfWork,
        record: IdempotencyRecord,
        command: AdjustInventoryCommand,
    ) -> AdjustmentView:
        item = await uow.inventory_items.get(
            tenant_id=command.tenant_id,
            inventory_item_id=InventoryItemId(completed_resource_id(record)),
        )
        if item is None:  # pragma: no cover - stock rows are never deleted
            raise NotFound("inventory_item", record.resource_id)
        movements = await uow.movements.list_for_item(
            tenant_id=command.tenant_id, inventory_item_id=item.id, limit=1
        )
        if not movements:  # pragma: no cover - an adjusted row always has a movement
            raise NotFound("inventory_movement", item.id)
        return AdjustmentView.of(item, movements[0])


class ReserveInventory(
    UseCase[ReserveInventoryCommand, ReservationResultView, InventoryUnitOfWork]
):
    """Hold stock for one or more SKUs, across warehouses when necessary."""

    operation = "inventory.reserve"
    success_status = 201

    def __init__(
        self,
        *,
        uow_factory: Callable[[], InventoryUnitOfWork],
        clock: Clock,
        events: EventDispatcher | None = None,
        idempotency_retention: timedelta = DEFAULT_IDEMPOTENCY_RETENTION,
        default_ttl_seconds: int = 1800,
        max_ttl_seconds: int = 86_400,
    ) -> None:
        super().__init__(
            uow_factory=uow_factory,
            clock=clock,
            events=events,
            idempotency_retention=idempotency_retention,
        )
        self._default_ttl_seconds = default_ttl_seconds
        self._max_ttl_seconds = max_ttl_seconds

    async def _execute(
        self, uow: InventoryUnitOfWork, command: ReserveInventoryCommand
    ) -> Executed[ReservationResultView]:
        now = self._clock.now()
        if not command.requirements:
            raise InvalidRequest("at least one stock requirement is required")
        if command.warehouse_ids:
            for warehouse_id in command.warehouse_ids:
                await _require_active_warehouse(uow, command.tenant_id, warehouse_id)

        sku_ids = tuple(dict.fromkeys(requirement.sku_id for requirement in command.requirements))
        items = await uow.inventory_items.lock_candidates_for_skus(
            tenant_id=command.tenant_id,
            sku_ids=sku_ids,
            warehouse_ids=command.warehouse_ids,
        )
        policy = InventoryAllocationPolicy(command.strategy)
        plan = policy.plan(
            requirements=command.requirements,
            candidates=[
                AllocationCandidate(
                    inventory_item_id=item.id,
                    warehouse_id=item.warehouse_id,
                    sku_id=item.sku_id,
                    available=item.available,
                )
                for item in items
            ],
            now=now,
        )

        if plan.is_empty:
            if command.strategy.requires_complete:
                raise InsufficientInventory(plan.shortfalls)
            # Nothing could be held: a legitimate PARTIAL outcome. The caller
            # (ordering) decides whether that becomes a back-order. No resource
            # is produced, so the idempotency claim is abandoned by the base class.
            return Executed(
                view=ReservationResultView(
                    reservation=None,
                    shortfalls=tuple(ShortfallView.of(shortfall) for shortfall in plan.shortfalls),
                    is_complete=False,
                ),
                resource_type="reservation",
                resource_id=None,
                events=(),
            )

        ttl = timedelta(seconds=self._resolve_ttl(command.ttl_seconds))
        reservation = Reservation.create(
            tenant_id=command.tenant_id,
            order_id=command.order_id,
            allocations=plan.allocations,
            expires_at=now + ttl,
            now=now,
        )

        expected_versions = {item.id: item.version for item in items}
        mutated: list[InventoryItem] = []
        for item in items:
            quantity = plan.quantity_for_item(item.id)
            if quantity.is_positive():
                item.reserve(quantity=quantity, reservation_id=reservation.id, now=now)
                mutated.append(item)

        await uow.reservations.add(reservation)
        for item in mutated:
            await uow.inventory_items.save(item, expected_version=expected_versions[item.id])

        return Executed(
            view=ReservationResultView(
                reservation=ReservationView.of(reservation),
                shortfalls=tuple(ShortfallView.of(shortfall) for shortfall in plan.shortfalls),
                is_complete=plan.is_complete,
            ),
            resource_type="reservation",
            resource_id=reservation.id,
            events=pull_events(reservation, *mutated),
        )

    async def _reload(
        self, uow: InventoryUnitOfWork, record: IdempotencyRecord, command: ReserveInventoryCommand
    ) -> ReservationResultView:
        reservation = await uow.reservations.get(
            tenant_id=command.tenant_id, reservation_id=ReservationId(completed_resource_id(record))
        )
        if reservation is None:  # pragma: no cover - reservations are never deleted
            raise NotFound("reservation", record.resource_id)
        return ReservationResultView(
            reservation=ReservationView.of(reservation), shortfalls=(), is_complete=True
        )

    def _resolve_ttl(self, requested: int | None) -> int:
        ttl = requested if requested is not None else self._default_ttl_seconds
        if ttl < 1 or ttl > self._max_ttl_seconds:
            raise InvalidRequest(
                f"reservation ttl must be between 1 and {self._max_ttl_seconds} seconds",
                details={"ttl_seconds": ttl, "max_ttl_seconds": self._max_ttl_seconds},
            )
        return ttl


class ReleaseReservation(UseCase[ReleaseReservationCommand, ReservationView, InventoryUnitOfWork]):
    """Give held stock back.

    Idempotent by contract: releasing an already-released reservation returns its
    current state instead of failing, while releasing a *confirmed* reservation is
    a genuine conflict (it would need a compensating return, which is Phase 2).
    """

    operation = "inventory.release_reservation"
    success_status = 200

    async def _execute(
        self, uow: InventoryUnitOfWork, command: ReleaseReservationCommand
    ) -> Executed[ReservationView]:
        now = self._clock.now()
        reservation = await _require_reservation_for_update(
            uow, command.tenant_id, command.reservation_id
        )
        if reservation.status is ReservationStatus.RELEASED:
            return Executed(
                view=ReservationView.of(reservation),
                resource_type="reservation",
                resource_id=reservation.id,
                events=(),
            )

        expected_version = reservation.version
        quantities = reservation.quantities_by_item()
        items = await _lock_reservation_items(uow, command.tenant_id, quantities.keys())
        expected_versions = {item.id: item.version for item in items}
        reservation.release(reason=command.reason, now=now)
        for item in items:
            quantity = quantities.get(item.id)
            if quantity is not None and quantity.is_positive():
                item.release(
                    quantity=quantity,
                    reservation_id=reservation.id,
                    reason=command.reason,
                    now=now,
                )
        await _persist(uow, reservation, expected_version, items, expected_versions)
        return Executed(
            view=ReservationView.of(reservation),
            resource_type="reservation",
            resource_id=reservation.id,
            events=pull_events(reservation, *items),
        )

    async def _reload(
        self,
        uow: InventoryUnitOfWork,
        record: IdempotencyRecord,
        command: ReleaseReservationCommand,
    ) -> ReservationView:
        reservation = await uow.reservations.get(
            tenant_id=command.tenant_id, reservation_id=ReservationId(completed_resource_id(record))
        )
        if reservation is None:  # pragma: no cover
            raise NotFound("reservation", record.resource_id)
        return ReservationView.of(reservation)


class ConfirmReservation(UseCase[ConfirmReservationCommand, ReservationView, InventoryUnitOfWork]):
    """Consume a hold: reserved stock becomes shipped stock.

    Refuses when the reservation is RELEASED or EXPIRED, and when it is ACTIVE but
    already past its expiry instant.
    """

    operation = "inventory.confirm_reservation"
    success_status = 200

    async def _execute(
        self, uow: InventoryUnitOfWork, command: ConfirmReservationCommand
    ) -> Executed[ReservationView]:
        now = self._clock.now()
        reservation = await _require_reservation_for_update(
            uow, command.tenant_id, command.reservation_id
        )
        expected_version = reservation.version
        quantities = reservation.quantities_by_item()
        items = await _lock_reservation_items(uow, command.tenant_id, quantities.keys())
        expected_versions = {item.id: item.version for item in items}
        reservation.confirm(now=now)
        for item in items:
            quantity = quantities.get(item.id)
            if quantity is not None and quantity.is_positive():
                item.commit_reservation(quantity=quantity, reservation_id=reservation.id, now=now)
        await _persist(uow, reservation, expected_version, items, expected_versions)
        return Executed(
            view=ReservationView.of(reservation),
            resource_type="reservation",
            resource_id=reservation.id,
            events=pull_events(reservation, *items),
        )

    async def _reload(
        self,
        uow: InventoryUnitOfWork,
        record: IdempotencyRecord,
        command: ConfirmReservationCommand,
    ) -> ReservationView:
        reservation = await uow.reservations.get(
            tenant_id=command.tenant_id, reservation_id=ReservationId(completed_resource_id(record))
        )
        if reservation is None:  # pragma: no cover
            raise NotFound("reservation", record.resource_id)
        return ReservationView.of(reservation)


class ExpireReservations(UseCase[ExpireReservationsCommand, int, InventoryUnitOfWork]):
    """System use case to sweep and expire active reservations past their TTL.

    Returns the count of expired reservations.
    """

    operation = "inventory.expire_reservations"
    success_status = 200

    async def _execute(
        self, uow: InventoryUnitOfWork, command: ExpireReservationsCommand
    ) -> Executed[int]:
        now = self._clock.now()
        tenant_id = command.context.tenant_id
        expired_batch = await uow.reservations.find_expired_active_for_update(
            now=now, limit=command.limit, tenant_id=tenant_id
        )

        all_events = []
        for reservation in expired_batch:
            expected_version = reservation.version
            quantities = reservation.quantities_by_item()
            items = await _lock_reservation_items(uow, reservation.tenant_id, quantities.keys())
            expected_versions = {item.id: item.version for item in items}
            reservation.expire(now=now)
            for item in items:
                quantity = quantities.get(item.id)
                if quantity is not None and quantity.is_positive():
                    item.release(
                        quantity=quantity,
                        reservation_id=reservation.id,
                        reason="reservation_expired",
                        now=now,
                    )
            await _persist(uow, reservation, expected_version, items, expected_versions)
            all_events.extend(pull_events(reservation, *items))

        return Executed(
            view=len(expired_batch),
            resource_type="reservation_batch",
            resource_id=None,
            events=tuple(all_events),
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
async def _require_active_warehouse(
    uow: InventoryUnitOfWork, tenant_id: TenantId, warehouse_id: WarehouseId
) -> Warehouse:
    warehouse = await uow.warehouses.get(tenant_id=tenant_id, warehouse_id=warehouse_id)
    if warehouse is None:
        raise NotFound("warehouse", warehouse_id)
    if not warehouse.is_active:
        raise WarehouseNotActive(
            "the warehouse is deactivated and cannot accept stock operations",
            details={"warehouse_id": str(warehouse_id)},
        )
    return warehouse


async def _require_reservation_for_update(
    uow: InventoryUnitOfWork, tenant_id: TenantId, reservation_id: ReservationId
) -> Reservation:
    reservation = await uow.reservations.get_for_update(
        tenant_id=tenant_id, reservation_id=reservation_id
    )
    if reservation is None:
        raise NotFound("reservation", reservation_id)
    return reservation


async def _lock_reservation_items(
    uow: InventoryUnitOfWork,
    tenant_id: TenantId,
    item_ids: Iterable[InventoryItemId],
) -> tuple[InventoryItem, ...]:
    ids = tuple(item_ids)
    if not ids:
        raise InvalidValue("the reservation holds no allocations")
    return await uow.inventory_items.load_for_update(tenant_id=tenant_id, inventory_item_ids=ids)


async def _persist(
    uow: InventoryUnitOfWork,
    reservation: Reservation,
    expected_version: int,
    items: tuple[InventoryItem, ...],
    expected_versions: dict[InventoryItemId, int],
) -> None:
    for item in items:
        await uow.inventory_items.save(item, expected_version=expected_versions[item.id])
    await uow.reservations.save(reservation, expected_version=expected_version)


__all__ = [
    "AdjustInventory",
    "ConfirmReservation",
    "ExpireReservations",
    "RegisterWarehouse",
    "ReleaseReservation",
    "ReserveInventory",
]
