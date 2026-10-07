"""SQLAlchemy unit of work for the inventory context."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from catalog.adapters.persistence.repositories import SqlAlchemySkuRepository
from inventory.adapters.persistence.repositories import (
    SqlAlchemyInventoryItemRepository,
    SqlAlchemyInventoryMovementRepository,
    SqlAlchemyReservationRepository,
    SqlAlchemyWarehouseRepository,
)
from inventory.ports.repositories import (
    InventoryItemRepository,
    InventoryMovementRepository,
    ReservationRepository,
    WarehouseRepository,
)
from shared.application.idempotency import IdempotencyStore
from shared.domain.identifiers import SkuId, TenantId
from shared.infrastructure.db.idempotency import SqlAlchemyIdempotencyStore
from shared.infrastructure.db.session import SqlAlchemyTransactionScope


class SqlAlchemyInventoryUnitOfWork:
    """Transaction-scoped access to inventory repositories.

    ``sku_exists`` is answered through the catalog's own repository adapter: the
    inventory context does not read catalog tables directly, and it does not
    import catalog's domain either.
    """

    __slots__ = (
        "_scope",
        "_skus",
        "idempotency",
        "inventory_items",
        "movements",
        "reservations",
        "warehouses",
    )

    def __init__(self, scope: SqlAlchemyTransactionScope) -> None:
        self._scope = scope

    async def __aenter__(self) -> Self:
        await self._scope.__aenter__()
        session = self._scope.session
        self.warehouses: WarehouseRepository = SqlAlchemyWarehouseRepository(session)
        self.inventory_items: InventoryItemRepository = SqlAlchemyInventoryItemRepository(session)
        self.movements: InventoryMovementRepository = SqlAlchemyInventoryMovementRepository(session)
        self.reservations: ReservationRepository = SqlAlchemyReservationRepository(session)
        self.idempotency: IdempotencyStore = SqlAlchemyIdempotencyStore(session)
        self._skus = SqlAlchemySkuRepository(session)
        return self

    async def sku_exists(self, *, tenant_id: TenantId, sku_id: SkuId) -> bool:
        sku = await self._skus.get(tenant_id=tenant_id, sku_id=sku_id)
        return sku is not None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        return await self._scope.__aexit__(exc_type, exc, tb)

    async def commit(self) -> None:
        await self._scope.commit()

    async def rollback(self) -> None:
        await self._scope.rollback()


__all__ = ["SqlAlchemyInventoryUnitOfWork"]
