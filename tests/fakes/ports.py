"""In-memory implementations of every repository port.

Each fake mirrors the behaviour of its SQLAlchemy counterpart that the
application layer can observe: tenant scoping, ordering of list results,
optimistic-concurrency failures, uniqueness violations.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from catalog.domain.product import Product
from catalog.domain.sku import Sku
from catalog.domain.value_objects import ProductSlug, ProductStatus
from catalog.ports.publication import PurchasableSku
from identity.domain.tenant import Tenant
from inventory.domain.inventory_item import InventoryItem
from inventory.domain.movement import InventoryMovement
from inventory.domain.reservation import Reservation
from inventory.domain.value_objects import ReservationStatus
from inventory.domain.warehouse import Warehouse
from ordering.domain.order import Order
from ordering.domain.value_objects import OrderStatus
from shared.application.errors import ConcurrencyConflict, DuplicateResource
from shared.application.pagination import Page
from shared.domain.identifiers import (
    InventoryItemId,
    OrderId,
    ProductId,
    ReservationId,
    SkuId,
    TenantId,
    WarehouseId,
)
from tests.fakes.store import FakeStores


class _VersionedRepository:
    """Optimistic-concurrency behaviour shared by the fake repositories.

    The SQLAlchemy adapters compare ``expected_version`` with the version stored on
    the *database row*. The fakes keep the same bookkeeping in
    ``FakeStores.versions``: the dictionary may hold the very object a use case has
    already mutated, so comparing against ``aggregate.version`` would always match
    and the guard would silently never fire.
    """

    #: Attribute of ``FakeStores`` holding this repository's rows.
    _collection: str = ""
    #: Human-readable name used in conflict messages, and the identifier field.
    _noun: str = ""
    _id_field: str = "id"

    def _rows(self) -> dict[uuid.UUID, object]:
        return getattr(self._stores, self._collection)

    def _persist(self, aggregate: object) -> None:
        """Store ``aggregate`` and remember the version it was written with."""
        identity = aggregate.id  # type: ignore[attr-defined]
        self._rows()[identity] = aggregate
        self._stores.versions[identity] = aggregate.version  # type: ignore[attr-defined]
        self._on_persist(aggregate)

    def _on_persist(self, aggregate: object) -> None:
        """Hook for repositories with side tables (movements, for instance)."""

    async def save(self, aggregate: object, *, expected_version: int) -> None:
        identity = aggregate.id  # type: ignore[attr-defined]
        persisted = self._stores.versions.get(identity)
        if persisted != expected_version:
            raise ConcurrencyConflict(
                f"{self._noun} was modified",
                details={
                    self._id_field: str(identity),
                    "expected_version": expected_version,
                    "actual_version": persisted,
                },
            )
        self._persist(aggregate)


class FakeTenantRepository(_VersionedRepository):
    _collection = "tenants"
    _noun = "tenant"
    _id_field = "tenant_id"

    def __init__(self, stores: FakeStores) -> None:
        self._stores = stores

    async def add(self, tenant: Tenant) -> None:
        if any(existing.slug == tenant.slug for existing in self._stores.tenants.values()):
            raise DuplicateResource(resource_type="tenant", field="slug")
        self._persist(tenant)

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        return self._stores.tenants.get(tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        for tenant in self._stores.tenants.values():
            if tenant.slug == slug:
                return tenant
        return None


class FakeProductRepository(_VersionedRepository):
    _collection = "products"
    _noun = "product"
    _id_field = "product_id"

    def __init__(self, stores: FakeStores) -> None:
        self._stores = stores

    async def add(self, product: Product) -> None:
        for existing in self._stores.products.values():
            if existing.tenant_id == product.tenant_id and existing.slug == product.slug:
                raise DuplicateResource(resource_type="product", field="slug")
        self._persist(product)

    async def get(
        self, *, tenant_id: TenantId, product_id: ProductId, for_update: bool = False
    ) -> Product | None:
        product = self._stores.products.get(product_id)
        if product is None or product.tenant_id != tenant_id:
            return None
        return product

    async def get_by_slug(self, *, tenant_id: TenantId, slug: ProductSlug) -> Product | None:
        for product in self._stores.products.values():
            if product.tenant_id == tenant_id and product.slug == slug:
                return product
        return None

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        status: ProductStatus | None = None,
    ) -> Page[Product]:
        products = [
            product
            for product in self._stores.products.values()
            if product.tenant_id == tenant_id and (status is None or product.status is status)
        ]
        products.sort(key=lambda product: (product.created_at, product.id), reverse=True)
        return Page(items=tuple(products[:limit]), next_cursor=None)


class FakeSkuRepository(_VersionedRepository):
    _collection = "skus"
    _noun = "sku"
    _id_field = "sku_id"

    def __init__(self, stores: FakeStores) -> None:
        self._stores = stores

    async def add(self, sku: Sku) -> None:
        for existing in self._stores.skus.values():
            if existing.tenant_id == sku.tenant_id and existing.code == sku.code:
                raise DuplicateResource(resource_type="sku", field="code")
        self._persist(sku)

    async def get(
        self, *, tenant_id: TenantId, sku_id: SkuId, for_update: bool = False
    ) -> Sku | None:
        sku = self._stores.skus.get(sku_id)
        if sku is None or sku.tenant_id != tenant_id:
            return None
        return sku

    async def list_by_ids(
        self, *, tenant_id: TenantId, sku_ids: tuple[SkuId, ...]
    ) -> tuple[Sku, ...]:
        return tuple(
            sku
            for sku_id in sku_ids
            if (sku := self._stores.skus.get(sku_id)) is not None and sku.tenant_id == tenant_id
        )

    async def list_for_products(
        self, *, tenant_id: TenantId, product_ids: tuple[ProductId, ...]
    ) -> dict[ProductId, tuple[Sku, ...]]:
        grouped: dict[ProductId, list[Sku]] = {}
        for sku in self._stores.skus.values():
            if sku.tenant_id == tenant_id and sku.product_id in product_ids:
                grouped.setdefault(sku.product_id, []).append(sku)
        return {
            product_id: tuple(sorted(items, key=lambda sku: sku.code.value))
            for product_id, items in grouped.items()
        }


class FakeCatalogQuery:
    """Cross-context read model over the fake catalog stores."""

    def __init__(self, stores: FakeStores) -> None:
        self._stores = stores

    async def purchasable_skus(
        self, *, tenant_id: TenantId, sku_ids: tuple[SkuId, ...]
    ) -> dict[SkuId, PurchasableSku]:
        result: dict[SkuId, PurchasableSku] = {}
        for sku_id in sku_ids:
            sku = self._stores.skus.get(sku_id)
            if sku is None or sku.tenant_id != tenant_id:
                continue
            product = self._stores.products.get(sku.product_id)
            if product is None:
                continue
            result[sku_id] = PurchasableSku(
                sku_id=sku.id,
                product_id=product.id,
                sku_code=sku.code.value,
                sku_name=sku.name,
                product_name=product.name,
                product_slug=product.slug.value,
                product_status=product.status,
                sku_status=sku.status,
                unit_price=sku.unit_price,
            )
        return result


class FakeWarehouseRepository(_VersionedRepository):
    _collection = "warehouses"
    _noun = "warehouse"
    _id_field = "warehouse_id"

    def __init__(self, stores: FakeStores) -> None:
        self._stores = stores

    async def add(self, warehouse: Warehouse) -> None:
        for existing in self._stores.warehouses.values():
            if existing.tenant_id == warehouse.tenant_id and existing.code == warehouse.code:
                raise DuplicateResource(resource_type="warehouse", field="code")
        self._persist(warehouse)

    async def get(
        self, *, tenant_id: TenantId, warehouse_id: WarehouseId, for_update: bool = False
    ) -> Warehouse | None:
        warehouse = self._stores.warehouses.get(warehouse_id)
        if warehouse is None or warehouse.tenant_id != tenant_id:
            return None
        return warehouse

    async def get_by_code(self, *, tenant_id: TenantId, code: str) -> Warehouse | None:
        for warehouse in self._stores.warehouses.values():
            if warehouse.tenant_id == tenant_id and warehouse.code == code:
                return warehouse
        return None

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        include_inactive: bool = True,
    ) -> Page[Warehouse]:
        warehouses = [
            warehouse
            for warehouse in self._stores.warehouses.values()
            if warehouse.tenant_id == tenant_id and (include_inactive or warehouse.is_active)
        ]
        warehouses.sort(key=lambda warehouse: (warehouse.created_at, warehouse.id), reverse=True)
        return Page(items=tuple(warehouses[:limit]), next_cursor=None)


class FakeInventoryItemRepository(_VersionedRepository):
    _collection = "inventory_items"
    _noun = "inventory item"
    _id_field = "inventory_item_id"

    def __init__(self, stores: FakeStores) -> None:
        self._stores = stores

    def _on_persist(self, aggregate: object) -> None:
        """Movements are appended by the aggregate, never by the caller."""
        self._stores.movements.extend(aggregate.pull_movements())  # type: ignore[attr-defined]

    async def add(self, item: InventoryItem) -> None:
        for existing in self._stores.inventory_items.values():
            if (
                existing.tenant_id == item.tenant_id
                and existing.warehouse_id == item.warehouse_id
                and existing.sku_id == item.sku_id
            ):
                raise DuplicateResource(resource_type="inventory_item", field="sku_id")
        self._persist(item)

    async def get(
        self, *, tenant_id: TenantId, inventory_item_id: InventoryItemId
    ) -> InventoryItem | None:
        item = self._stores.inventory_items.get(inventory_item_id)
        if item is None or item.tenant_id != tenant_id:
            return None
        return item

    async def load_for_update(
        self, *, tenant_id: TenantId, inventory_item_ids: tuple[InventoryItemId, ...]
    ) -> tuple[InventoryItem, ...]:
        items = [
            item
            for item_id in inventory_item_ids
            if (item := self._stores.inventory_items.get(item_id)) is not None
            and item.tenant_id == tenant_id
        ]
        return tuple(sorted(items, key=lambda item: str(item.id)))

    async def lock_candidates_for_skus(
        self,
        *,
        tenant_id: TenantId,
        sku_ids: tuple[SkuId, ...],
        warehouse_ids: tuple[WarehouseId, ...] | None = None,
    ) -> tuple[InventoryItem, ...]:
        items = [
            item
            for item in self._stores.inventory_items.values()
            if item.tenant_id == tenant_id
            and item.sku_id in sku_ids
            and (warehouse_ids is None or item.warehouse_id in warehouse_ids)
        ]
        return tuple(sorted(items, key=lambda item: str(item.id)))

    async def get_or_register_for_update(
        self,
        *,
        tenant_id: TenantId,
        warehouse_id: WarehouseId,
        sku_id: SkuId,
        now: datetime,
    ) -> tuple[InventoryItem, bool]:
        for item in self._stores.inventory_items.values():
            if (
                item.tenant_id == tenant_id
                and item.warehouse_id == warehouse_id
                and item.sku_id == sku_id
            ):
                return item, False
        item = InventoryItem.register(
            tenant_id=tenant_id, warehouse_id=warehouse_id, sku_id=sku_id, now=now
        )
        self._persist(item)
        return item, True

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        sku_id: SkuId | None = None,
        warehouse_id: WarehouseId | None = None,
    ) -> Page[InventoryItem]:
        items = [
            item
            for item in self._stores.inventory_items.values()
            if item.tenant_id == tenant_id
            and (sku_id is None or item.sku_id == sku_id)
            and (warehouse_id is None or item.warehouse_id == warehouse_id)
        ]
        items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return Page(items=tuple(items[:limit]), next_cursor=None)


class FakeMovementRepository:
    def __init__(self, stores: FakeStores) -> None:
        self._stores = stores

    async def add(self, movement: InventoryMovement) -> None:
        self._stores.movements.append(movement)

    async def list_for_item(
        self,
        *,
        tenant_id: TenantId,
        inventory_item_id: InventoryItemId,
        limit: int = 100,
    ) -> tuple[InventoryMovement, ...]:
        movements = [
            movement
            for movement in self._stores.movements
            if movement.tenant_id == tenant_id and movement.inventory_item_id == inventory_item_id
        ]
        movements.sort(key=lambda movement: (movement.occurred_at, movement.id), reverse=True)
        return tuple(movements[:limit])


class FakeReservationRepository(_VersionedRepository):
    _collection = "reservations"
    _noun = "reservation"
    _id_field = "reservation_id"

    def __init__(self, stores: FakeStores) -> None:
        self._stores = stores

    async def add(self, reservation: Reservation) -> None:
        self._persist(reservation)

    async def get(
        self, *, tenant_id: TenantId, reservation_id: ReservationId
    ) -> Reservation | None:
        reservation = self._stores.reservations.get(reservation_id)
        if reservation is None or reservation.tenant_id != tenant_id:
            return None
        return reservation

    async def get_for_update(
        self, *, tenant_id: TenantId, reservation_id: ReservationId
    ) -> Reservation | None:
        return await self.get(tenant_id=tenant_id, reservation_id=reservation_id)

    async def get_active_for_order(
        self, *, tenant_id: TenantId, order_id: OrderId
    ) -> Reservation | None:
        for reservation in self._stores.reservations.values():
            if (
                reservation.tenant_id == tenant_id
                and reservation.order_id == order_id
                and reservation.status is ReservationStatus.ACTIVE
            ):
                return reservation
        return None

    async def get_active_for_order_for_update(
        self, *, tenant_id: TenantId, order_id: OrderId
    ) -> Reservation | None:
        return await self.get_active_for_order(tenant_id=tenant_id, order_id=order_id)

    async def list_for_order(
        self, *, tenant_id: TenantId, order_id: OrderId
    ) -> tuple[Reservation, ...]:
        reservations = [
            reservation
            for reservation in self._stores.reservations.values()
            if reservation.tenant_id == tenant_id and reservation.order_id == order_id
        ]
        reservations.sort(key=lambda reservation: (reservation.created_at, reservation.id))
        return tuple(reservations)


class FakeOrderRepository(_VersionedRepository):
    _collection = "orders"
    _noun = "order"
    _id_field = "order_id"

    def __init__(self, stores: FakeStores) -> None:
        self._stores = stores

    async def add(self, order: Order) -> None:
        self._persist(order)

    async def get(self, *, tenant_id: TenantId, order_id: OrderId) -> Order | None:
        order = self._stores.orders.get(order_id)
        if order is None or order.tenant_id != tenant_id:
            return None
        return order

    async def get_for_update(self, *, tenant_id: TenantId, order_id: OrderId) -> Order | None:
        return await self.get(tenant_id=tenant_id, order_id=order_id)

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        status: OrderStatus | None = None,
    ) -> Page[Order]:
        orders = [
            order
            for order in self._stores.orders.values()
            if order.tenant_id == tenant_id and (status is None or order.status is status)
        ]
        orders.sort(key=lambda order: (order.created_at, order.id), reverse=True)
        return Page(items=tuple(orders[:limit]), next_cursor=None)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


__all__ = [
    "FakeCatalogQuery",
    "FakeInventoryItemRepository",
    "FakeMovementRepository",
    "FakeOrderRepository",
    "FakeProductRepository",
    "FakeReservationRepository",
    "FakeSkuRepository",
    "FakeTenantRepository",
    "FakeWarehouseRepository",
    "new_uuid",
]
