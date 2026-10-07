"""SQLAlchemy implementations of the inventory repository ports.

The locking behaviour documented in the ports is implemented here:

* ``with_for_update()`` for single-row read-modify-write,
* ``ORDER BY id ... FOR UPDATE`` for multi-row contention (deterministic lock
  order),
* ``INSERT ... ON CONFLICT DO NOTHING`` + locking select for get-or-register.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Select, insert, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from inventory.adapters.persistence.mappers import (
    allocation_insert_values,
    inventory_item_insert_values,
    inventory_item_to_domain,
    inventory_item_update_values,
    movement_insert_values,
    movement_to_domain,
    reservation_insert_values,
    reservation_to_domain,
    reservation_update_values,
    warehouse_insert_values,
    warehouse_to_domain,
    warehouse_update_values,
)
from inventory.adapters.persistence.tables import (
    inventory_items,
    inventory_movements,
    reservation_allocations,
    reservations,
    warehouses,
)
from inventory.domain.inventory_item import InventoryItem
from inventory.domain.movement import InventoryMovement
from inventory.domain.reservation import Reservation
from inventory.domain.value_objects import ReservationStatus
from inventory.domain.warehouse import Warehouse
from shared.application.pagination import (
    Page,
    build_page,
    cursor_datetime,
    cursor_uuid,
    decode_cursor,
)
from shared.domain.identifiers import (
    InventoryItemId,
    OrderId,
    ReservationId,
    SkuId,
    TenantId,
    WarehouseId,
)
from shared.infrastructure.db.execution import execute, execute_guarded_update

_WAREHOUSE_DUPLICATES: Mapping[str, tuple[str, str]] = {
    "uq_warehouses_tenant_id_code": ("warehouse", "code"),
}


class SqlAlchemyWarehouseRepository:
    """Warehouse persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, warehouse: Warehouse) -> None:
        await execute(
            self._session,
            insert(warehouses).values(**warehouse_insert_values(warehouse)),
            duplicates=_WAREHOUSE_DUPLICATES,
        )

    async def get(
        self, *, tenant_id: TenantId, warehouse_id: WarehouseId, for_update: bool = False
    ) -> Warehouse | None:
        statement = select(warehouses).where(
            warehouses.c.tenant_id == tenant_id, warehouses.c.id == warehouse_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).mappings().first()
        return warehouse_to_domain(row) if row is not None else None

    async def get_by_code(self, *, tenant_id: TenantId, code: str) -> Warehouse | None:
        statement = select(warehouses).where(
            warehouses.c.tenant_id == tenant_id, warehouses.c.code == code
        )
        row = (await self._session.execute(statement)).mappings().first()
        return warehouse_to_domain(row) if row is not None else None

    async def save(self, warehouse: Warehouse, *, expected_version: int) -> None:
        statement = (
            update(warehouses)
            .where(
                warehouses.c.id == warehouse.id,
                warehouses.c.tenant_id == warehouse.tenant_id,
                warehouses.c.version == expected_version,
            )
            .values(**warehouse_update_values(warehouse), version=warehouse.version)
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="warehouse",
            resource_id=warehouse.id,
            expected_version=expected_version,
        )

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        include_inactive: bool = True,
    ) -> Page[Warehouse]:
        statement: Select[Any] = select(warehouses).where(warehouses.c.tenant_id == tenant_id)
        if not include_inactive:
            statement = statement.where(warehouses.c.is_active.is_(True))
        if cursor is not None:
            values = decode_cursor(cursor)
            statement = statement.where(
                tuple_(warehouses.c.created_at, warehouses.c.id)
                < tuple_(cursor_datetime(values, "created_at"), cursor_uuid(values, "id"))
            )
        statement = statement.order_by(warehouses.c.created_at.desc(), warehouses.c.id.desc())
        statement = statement.limit(limit + 1)
        rows = (await self._session.execute(statement)).mappings().all()
        return build_page(
            [warehouse_to_domain(row) for row in rows],
            limit=limit,
            cursor_of=lambda warehouse: {
                "created_at": warehouse.created_at,
                "id": warehouse.id,
            },
        )


class SqlAlchemyInventoryItemRepository:
    """Inventory item (stock row) persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, item: InventoryItem) -> None:
        await execute(
            self._session, insert(inventory_items).values(**inventory_item_insert_values(item))
        )
        await self._append_pending_movements(item)

    async def get(
        self, *, tenant_id: TenantId, inventory_item_id: InventoryItemId
    ) -> InventoryItem | None:
        statement = select(inventory_items).where(
            inventory_items.c.tenant_id == tenant_id, inventory_items.c.id == inventory_item_id
        )
        row = (await self._session.execute(statement)).mappings().first()
        return inventory_item_to_domain(row) if row is not None else None

    async def load_for_update(
        self, *, tenant_id: TenantId, inventory_item_ids: tuple[InventoryItemId, ...]
    ) -> tuple[InventoryItem, ...]:
        if not inventory_item_ids:
            return ()
        statement = (
            select(inventory_items)
            .where(
                inventory_items.c.tenant_id == tenant_id,
                inventory_items.c.id.in_(tuple(inventory_item_ids)),
            )
            # Deterministic lock order: the Sort node sits *below* LockRows, so
            # PostgreSQL locks rows in id order. Every writer uses the same order,
            # which removes the deadlock cycle between concurrent operations.
            .order_by(inventory_items.c.id.asc())
            .with_for_update()
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(inventory_item_to_domain(row) for row in rows)

    async def lock_candidates_for_skus(
        self,
        *,
        tenant_id: TenantId,
        sku_ids: tuple[SkuId, ...],
        warehouse_ids: tuple[WarehouseId, ...] | None = None,
    ) -> tuple[InventoryItem, ...]:
        if not sku_ids:
            return ()
        statement = select(inventory_items).where(
            inventory_items.c.tenant_id == tenant_id,
            inventory_items.c.sku_id.in_(tuple(sku_ids)),
        )
        if warehouse_ids:
            statement = statement.where(inventory_items.c.warehouse_id.in_(tuple(warehouse_ids)))
        statement = statement.order_by(inventory_items.c.id.asc()).with_for_update()
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(inventory_item_to_domain(row) for row in rows)

    async def get_or_register_for_update(
        self,
        *,
        tenant_id: TenantId,
        warehouse_id: WarehouseId,
        sku_id: SkuId,
        now: datetime,
    ) -> tuple[InventoryItem, bool]:
        # 1) Fast path: the row exists - lock it.
        existing = await self._locked_item(
            tenant_id=tenant_id, warehouse_id=warehouse_id, sku_id=sku_id
        )
        if existing is not None:
            return existing, False

        # 2) First receipt for this SKU/warehouse pair: create the row. ON CONFLICT
        #    makes the race between two concurrent first receipts a no-op for the
        #    loser instead of a unique-violation error.
        item = InventoryItem.register(
            tenant_id=tenant_id, warehouse_id=warehouse_id, sku_id=sku_id, now=now
        )
        statement = (
            pg_insert(inventory_items)
            .values(**inventory_item_insert_values(item))
            .on_conflict_do_nothing(index_elements=["tenant_id", "warehouse_id", "sku_id"])
            .returning(*inventory_items.c)
        )
        row = (await execute(self._session, statement)).mappings().first()
        if row is not None:
            return inventory_item_to_domain(row), True

        # 3) Somebody else won the race; lock and read their row.
        racily_created = await self._locked_item(
            tenant_id=tenant_id, warehouse_id=warehouse_id, sku_id=sku_id
        )
        if racily_created is None:  # pragma: no cover - the competing writer rolled back
            raise RuntimeError("inventory item could not be registered or locked")
        return racily_created, False

    async def save(self, item: InventoryItem, *, expected_version: int) -> None:
        statement = (
            update(inventory_items)
            .where(
                inventory_items.c.id == item.id,
                inventory_items.c.tenant_id == item.tenant_id,
                inventory_items.c.version == expected_version,
            )
            .values(**inventory_item_update_values(item), version=item.version)
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="inventory_item",
            resource_id=item.id,
            expected_version=expected_version,
        )
        await self._append_pending_movements(item)

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        sku_id: SkuId | None = None,
        warehouse_id: WarehouseId | None = None,
    ) -> Page[InventoryItem]:
        statement: Select[Any] = select(inventory_items).where(
            inventory_items.c.tenant_id == tenant_id
        )
        if sku_id is not None:
            statement = statement.where(inventory_items.c.sku_id == sku_id)
        if warehouse_id is not None:
            statement = statement.where(inventory_items.c.warehouse_id == warehouse_id)
        if cursor is not None:
            values = decode_cursor(cursor)
            statement = statement.where(
                tuple_(inventory_items.c.created_at, inventory_items.c.id)
                < tuple_(cursor_datetime(values, "created_at"), cursor_uuid(values, "id"))
            )
        statement = statement.order_by(
            inventory_items.c.created_at.desc(), inventory_items.c.id.desc()
        )
        statement = statement.limit(limit + 1)
        rows = (await self._session.execute(statement)).mappings().all()
        return build_page(
            [inventory_item_to_domain(row) for row in rows],
            limit=limit,
            cursor_of=lambda item: {"created_at": item.created_at, "id": item.id},
        )

    # -- internals ---------------------------------------------------------
    async def _locked_item(
        self, *, tenant_id: TenantId, warehouse_id: WarehouseId, sku_id: SkuId
    ) -> InventoryItem | None:
        statement = (
            select(inventory_items)
            .where(
                inventory_items.c.tenant_id == tenant_id,
                inventory_items.c.warehouse_id == warehouse_id,
                inventory_items.c.sku_id == sku_id,
            )
            .with_for_update()
        )
        row = (await self._session.execute(statement)).mappings().first()
        return inventory_item_to_domain(row) if row is not None else None

    async def _append_pending_movements(self, item: InventoryItem) -> None:
        """Write the ledger entries the aggregate produced.

        The movements live on the aggregate until the repository persists them,
        which makes "every stock change has a ledger entry" impossible to forget.
        """
        for movement in item.pull_movements():
            await execute(
                self._session,
                insert(inventory_movements).values(**movement_insert_values(movement)),
            )


class SqlAlchemyInventoryMovementRepository:
    """Append-only stock ledger adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, movement: InventoryMovement) -> None:
        await execute(
            self._session, insert(inventory_movements).values(**movement_insert_values(movement))
        )

    async def list_for_item(
        self,
        *,
        tenant_id: TenantId,
        inventory_item_id: InventoryItemId,
        limit: int = 100,
    ) -> tuple[InventoryMovement, ...]:
        statement = (
            select(inventory_movements)
            .where(
                inventory_movements.c.tenant_id == tenant_id,
                inventory_movements.c.inventory_item_id == inventory_item_id,
            )
            .order_by(inventory_movements.c.occurred_at.desc(), inventory_movements.c.id.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(movement_to_domain(row) for row in rows)


class SqlAlchemyReservationRepository:
    """Reservation persistence adapter (reservation + its allocations)."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, reservation: Reservation) -> None:
        await execute(
            self._session,
            insert(reservations).values(**reservation_insert_values(reservation)),
        )
        for allocation in reservation.allocations:
            await execute(
                self._session,
                insert(reservation_allocations).values(
                    **allocation_insert_values(reservation.tenant_id, reservation.id, allocation)
                ),
            )

    async def get(
        self, *, tenant_id: TenantId, reservation_id: ReservationId
    ) -> Reservation | None:
        return await self._load(tenant_id=tenant_id, reservation_id=reservation_id, lock=False)

    async def get_for_update(
        self, *, tenant_id: TenantId, reservation_id: ReservationId
    ) -> Reservation | None:
        return await self._load(tenant_id=tenant_id, reservation_id=reservation_id, lock=True)

    async def get_active_for_order(
        self, *, tenant_id: TenantId, order_id: OrderId
    ) -> Reservation | None:
        return await self._load_for_order(tenant_id=tenant_id, order_id=order_id, lock=False)

    async def get_active_for_order_for_update(
        self, *, tenant_id: TenantId, order_id: OrderId
    ) -> Reservation | None:
        return await self._load_for_order(tenant_id=tenant_id, order_id=order_id, lock=True)

    async def list_for_order(
        self, *, tenant_id: TenantId, order_id: OrderId
    ) -> tuple[Reservation, ...]:
        statement = (
            select(reservations.c.id)
            .where(reservations.c.tenant_id == tenant_id, reservations.c.order_id == order_id)
            .order_by(reservations.c.created_at.asc())
        )
        ids = [row[0] for row in (await self._session.execute(statement)).all()]
        loaded: list[Reservation] = []
        for reservation_id in ids:
            reservation = await self._load(
                tenant_id=tenant_id, reservation_id=ReservationId(reservation_id), lock=False
            )
            if reservation is not None:
                loaded.append(reservation)
        return tuple(loaded)

    async def save(self, reservation: Reservation, *, expected_version: int) -> None:
        statement = (
            update(reservations)
            .where(
                reservations.c.id == reservation.id,
                reservations.c.tenant_id == reservation.tenant_id,
                reservations.c.version == expected_version,
            )
            .values(**reservation_update_values(reservation), version=reservation.version)
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="reservation",
            resource_id=reservation.id,
            expected_version=expected_version,
        )

    async def find_expired_active_for_update(
        self, *, now: datetime, limit: int = 50, tenant_id: TenantId | None = None
    ) -> tuple[Reservation, ...]:
        statement = select(reservations.c.id).where(
            reservations.c.status == ReservationStatus.ACTIVE.value,
            reservations.c.expires_at <= now,
        )
        if tenant_id is not None:
            statement = statement.where(reservations.c.tenant_id == tenant_id)
        statement = (
            statement.order_by(reservations.c.expires_at.asc(), reservations.c.id.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(statement)
        ids = [row[0] for row in result.all()]
        loaded: list[Reservation] = []
        for reservation_id in ids:
            res_stmt = select(reservations).where(reservations.c.id == reservation_id)
            row = (await self._session.execute(res_stmt)).mappings().first()
            if row is not None:
                alloc_stmt = (
                    select(reservation_allocations)
                    .where(reservation_allocations.c.reservation_id == reservation_id)
                    .order_by(reservation_allocations.c.created_at.asc(), reservation_allocations.c.id.asc())
                )
                allocs = list((await self._session.execute(alloc_stmt)).mappings().all())
                res = reservation_to_domain(row, allocs)
                if res.status is ReservationStatus.ACTIVE and res.expires_at <= now:
                    loaded.append(res)
        return tuple(loaded)

    # -- internals ---------------------------------------------------------
    async def _load(
        self, *, tenant_id: TenantId, reservation_id: ReservationId, lock: bool
    ) -> Reservation | None:
        statement = select(reservations).where(
            reservations.c.tenant_id == tenant_id, reservations.c.id == reservation_id
        )
        if lock:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).mappings().first()
        if row is None:
            return None
        allocations = await self._allocations(tenant_id=tenant_id, reservation_id=reservation_id)
        return reservation_to_domain(row, allocations)

    async def _load_for_order(
        self, *, tenant_id: TenantId, order_id: OrderId, lock: bool
    ) -> Reservation | None:
        statement = select(reservations).where(
            reservations.c.tenant_id == tenant_id,
            reservations.c.order_id == order_id,
            reservations.c.status == ReservationStatus.ACTIVE.value,
        )
        if lock:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).mappings().first()
        if row is None:
            return None
        allocations = await self._allocations(
            tenant_id=tenant_id, reservation_id=ReservationId(row["id"])
        )
        return reservation_to_domain(row, allocations)

    async def _allocations(
        self, *, tenant_id: TenantId, reservation_id: ReservationId
    ) -> list[RowMapping]:
        statement = (
            select(reservation_allocations)
            .where(
                reservation_allocations.c.tenant_id == tenant_id,
                reservation_allocations.c.reservation_id == reservation_id,
            )
            .order_by(
                reservation_allocations.c.created_at.asc(),
                reservation_allocations.c.id.asc(),
            )
        )
        return list((await self._session.execute(statement)).mappings().all())


__all__ = [
    "SqlAlchemyInventoryItemRepository",
    "SqlAlchemyInventoryMovementRepository",
    "SqlAlchemyReservationRepository",
    "SqlAlchemyWarehouseRepository",
]
