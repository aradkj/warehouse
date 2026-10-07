"""Fake unit-of-work implementations: one per bounded context.

Each class mirrors the repository attributes and cross-context helper methods of
its SQLAlchemy counterpart, so a use case wired to fakes behaves identically to
one wired to PostgreSQL for everything the application layer can observe.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from shared.domain.identifiers import SkuId, TenantId
from tests.fakes.ports import (
    FakeCatalogQuery,
    FakeInventoryItemRepository,
    FakeMovementRepository,
    FakeOrderRepository,
    FakeProductRepository,
    FakeReservationRepository,
    FakeSkuRepository,
    FakeTenantRepository,
    FakeWarehouseRepository,
)
from tests.fakes.store import (
    FakeStores,
    FakeTransactionScope,
    FakeUnitOfWork,
    InMemoryIdempotencyStore,
)


class FakeCatalogUnitOfWork(FakeUnitOfWork):
    """``CatalogUnitOfWork`` over in-memory stores."""

    def __init__(self, stores: FakeStores, scope: FakeTransactionScope) -> None:
        super().__init__(scope)
        self._stores = stores
        self.products = FakeProductRepository(stores)
        self.skus = FakeSkuRepository(stores)
        self.idempotency = InMemoryIdempotencyStore(stores)

    async def __aenter__(self) -> FakeCatalogUnitOfWork:
        await super().__aenter__()
        return self


class FakeIdentityUnitOfWork(FakeUnitOfWork):
    """``IdentityUnitOfWork`` over in-memory stores."""

    def __init__(self, stores: FakeStores, scope: FakeTransactionScope) -> None:
        super().__init__(scope)
        self._stores = stores
        self.tenants = FakeTenantRepository(stores)
        self.idempotency = InMemoryIdempotencyStore(stores)

    async def __aenter__(self) -> FakeIdentityUnitOfWork:
        await super().__aenter__()
        return self


class FakeInventoryUnitOfWork(FakeUnitOfWork):
    """``InventoryUnitOfWork`` over in-memory stores."""

    def __init__(self, stores: FakeStores, scope: FakeTransactionScope) -> None:
        super().__init__(scope)
        self._stores = stores
        self.warehouses = FakeWarehouseRepository(stores)
        self.inventory_items = FakeInventoryItemRepository(stores)
        self.movements = FakeMovementRepository(stores)
        self.reservations = FakeReservationRepository(stores)
        self.idempotency = InMemoryIdempotencyStore(stores)

    async def __aenter__(self) -> FakeInventoryUnitOfWork:
        await super().__aenter__()
        return self

    async def sku_exists(self, *, tenant_id: TenantId, sku_id: SkuId) -> bool:
        sku = self._stores.skus.get(sku_id)
        return sku is not None and sku.tenant_id == tenant_id


class FakeOrderingUnitOfWork(FakeUnitOfWork):
    """``OrderingUnitOfWork``: orders plus the two cross-context collaborators.

    The in-process inventory collaborator is composed exactly like the real one
    (``InProcessInventoryOperations`` over the same scope), from the fake
    inventory repository and the real inventory use cases wired to fakes. That is
    what makes the application-layer tests meaningful: order placement really does
    call the reserve/release flows, it is not stubbed out.
    """

    def __init__(
        self,
        stores: FakeStores,
        scope: FakeTransactionScope,
        *,
        inventory: Any,
        clock: Any,
        default_reservation_ttl_seconds: int = 1800,
        max_reservation_ttl_seconds: int = 86_400,
    ) -> None:
        super().__init__(scope)
        self._stores = stores
        self.orders = FakeOrderRepository(stores)
        self.catalog = FakeCatalogQuery(stores)
        self.inventory = inventory
        self.idempotency = InMemoryIdempotencyStore(stores)

    async def __aenter__(self) -> FakeOrderingUnitOfWork:
        await super().__aenter__()
        return self


def uuid_of(value: str) -> UUID:  # pragma: no cover - convenience for tests
    return UUID(value)


__all__ = [
    "FakeCatalogUnitOfWork",
    "FakeIdentityUnitOfWork",
    "FakeInventoryUnitOfWork",
    "FakeOrderingUnitOfWork",
]
