"""Inventory read use cases: availability, reservations and warehouses."""

from __future__ import annotations

from inventory.application.commands import (
    AvailabilityView,
    GetAvailabilityQuery,
    GetReservationQuery,
    GetWarehouseQuery,
    ListWarehousesQuery,
    ReservationView,
    WarehouseView,
)
from inventory.ports.unit_of_work import InventoryUnitOfWork
from shared.application.errors import NotFound
from shared.application.pagination import Page, normalise_limit
from shared.application.use_case import QueryUseCase


class GetAvailability(
    QueryUseCase[GetAvailabilityQuery, Page[AvailabilityView], InventoryUnitOfWork]
):
    """Report stock positions (on hand, reserved, available) per SKU/warehouse."""

    permission = "inventory.read"
    resource_type = "inventory"

    async def _execute(
        self, uow: InventoryUnitOfWork, command: GetAvailabilityQuery
    ) -> Page[AvailabilityView]:
        limit = normalise_limit(command.limit)
        page = await uow.inventory_items.page(
            tenant_id=command.tenant_id,
            limit=limit,
            cursor=command.cursor,
            sku_id=command.sku_id,
            warehouse_id=command.warehouse_id,
        )
        return Page(
            items=tuple(AvailabilityView.of(item) for item in page.items),
            next_cursor=page.next_cursor,
        )


class GetReservation(QueryUseCase[GetReservationQuery, ReservationView, InventoryUnitOfWork]):
    """Fetch one reservation with its allocations."""

    permission = "inventory.read"
    resource_type = "reservation"

    async def _execute(
        self, uow: InventoryUnitOfWork, command: GetReservationQuery
    ) -> ReservationView:
        reservation = await uow.reservations.get(
            tenant_id=command.tenant_id, reservation_id=command.reservation_id
        )
        if reservation is None:
            raise NotFound("reservation", command.reservation_id)
        return ReservationView.of(reservation)


class GetWarehouse(QueryUseCase[GetWarehouseQuery, WarehouseView, InventoryUnitOfWork]):
    """Fetch one warehouse."""

    permission = "inventory.read"
    resource_type = "warehouse"

    async def _execute(self, uow: InventoryUnitOfWork, command: GetWarehouseQuery) -> WarehouseView:
        warehouse = await uow.warehouses.get(
            tenant_id=command.tenant_id, warehouse_id=command.warehouse_id
        )
        if warehouse is None:
            raise NotFound("warehouse", command.warehouse_id)
        return WarehouseView.of(warehouse)


class ListWarehouses(QueryUseCase[ListWarehousesQuery, Page[WarehouseView], InventoryUnitOfWork]):
    """Keyset-paginated warehouse list."""

    permission = "inventory.read"
    resource_type = "warehouse"

    async def _execute(
        self, uow: InventoryUnitOfWork, command: ListWarehousesQuery
    ) -> Page[WarehouseView]:
        limit = normalise_limit(command.limit)
        page = await uow.warehouses.page(
            tenant_id=command.tenant_id,
            limit=limit,
            cursor=command.cursor,
            include_inactive=command.include_inactive,
        )
        return Page(
            items=tuple(WarehouseView.of(warehouse) for warehouse in page.items),
            next_cursor=page.next_cursor,
        )


__all__ = ["GetAvailability", "GetReservation", "GetWarehouse", "ListWarehouses"]
