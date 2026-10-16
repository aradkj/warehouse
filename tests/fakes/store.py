"""In-memory state shared by the fake repositories.

The fakes are not mocks: they implement the *same ports* as the SQLAlchemy
adapters and reproduce the transaction semantics the application layer relies on
(commit persists, rollback restores, nested scopes participate in the outer
transaction). That is what allows the application-layer test suite to run without
infrastructure while still failing when a use case forgets to commit, or when a
failure leaves partial state behind.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from catalog.domain.product import Product
from catalog.domain.sku import Sku
from identity.domain.membership import TenantMembership
from identity.domain.policy import AccessPolicy
from identity.domain.role import Role
from identity.domain.service_account import ServiceAccount
from identity.domain.session import UserSession
from identity.domain.tenant import Tenant
from identity.domain.user import User
from inventory.domain.inventory_item import InventoryItem
from inventory.domain.movement import InventoryMovement
from inventory.domain.reservation import Reservation
from inventory.domain.warehouse import Warehouse
from ordering.domain.order import Order
from shared.application.errors import (
    DuplicateIdempotencyKey,
    IdempotencyOperationInProgress,
)
from shared.application.idempotency import (
    IdempotencyClaim,
    IdempotencyOutcome,
    IdempotencyRecord,
    IdempotencyState,
)
from shared.domain.identifiers import (
    InventoryItemId,
    MembershipId,
    OrderId,
    PolicyId,
    ProductId,
    ReservationId,
    RoleId,
    ServiceAccountId,
    SkuId,
    TenantId,
    UserId,
    WarehouseId,
)


@dataclass
class FakeStores:
    """All in-memory tables, kept together so one snapshot covers a transaction."""

    tenants: dict[TenantId, Tenant] = field(default_factory=dict)
    products: dict[ProductId, Product] = field(default_factory=dict)
    skus: dict[SkuId, Sku] = field(default_factory=dict)
    warehouses: dict[WarehouseId, Warehouse] = field(default_factory=dict)
    inventory_items: dict[InventoryItemId, InventoryItem] = field(default_factory=dict)
    movements: list[InventoryMovement] = field(default_factory=list)
    reservations: dict[ReservationId, Reservation] = field(default_factory=dict)
    orders: dict[OrderId, Order] = field(default_factory=dict)
    idempotency: dict[tuple[uuid.UUID, str], IdempotencyRecord] = field(default_factory=dict)
    users: dict[UserId, User] = field(default_factory=dict)
    memberships: dict[MembershipId, TenantMembership] = field(default_factory=dict)
    sessions: dict[uuid.UUID, UserSession] = field(default_factory=dict)
    service_accounts: dict[ServiceAccountId, ServiceAccount] = field(default_factory=dict)
    roles: dict[RoleId, Role] = field(default_factory=dict)
    policies: dict[PolicyId, AccessPolicy] = field(default_factory=dict)
    audit_logs: list[Any] = field(default_factory=list)
    #: Version each aggregate had when it was last written. Optimistic
    #: concurrency is checked against this, not against the object in the
    #: dictionary: that object *is* the one the use case just mutated, so its
    #: version would always match and the check would never fire.
    versions: dict[uuid.UUID, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.roles:
            from datetime import UTC, datetime

            from identity.domain.role import (
                SYSTEM_ROLES_PERMISSIONS,
                Role,
            )

            now = datetime.now(UTC)
            for name, perms in SYSTEM_ROLES_PERMISSIONS.items():
                r = Role.create(
                    name=name,
                    description=f"System {name} role",
                    permissions=perms,
                    tenant_id=None,
                    is_system=True,
                    now=now,
                )
                self.roles[r.id] = r

    def snapshot(self) -> FakeStores:
        return copy.deepcopy(self)

    def restore(self, snapshot: FakeStores) -> None:
        self.tenants = snapshot.tenants
        self.products = snapshot.products
        self.skus = snapshot.skus
        self.warehouses = snapshot.warehouses
        self.inventory_items = snapshot.inventory_items
        self.movements = snapshot.movements
        self.reservations = snapshot.reservations
        self.orders = snapshot.orders
        self.idempotency = snapshot.idempotency
        self.users = snapshot.users
        self.memberships = snapshot.memberships
        self.sessions = snapshot.sessions
        self.service_accounts = snapshot.service_accounts
        self.roles = snapshot.roles
        self.policies = snapshot.policies
        self.versions = snapshot.versions


class FakeTransactionScope:
    """Participation-aware transaction scope, mirroring the SQLAlchemy one.

    * the outermost ``__aenter__`` snapshots the stores,
    * nested entries participate (depth counting),
    * ``commit()`` at depth 1 keeps the snapshot,
    * ``__aexit__`` with an exception (or without a commit) restores it.
    """

    def __init__(self, stores: FakeStores) -> None:
        self.stores = stores
        self._depth = 0
        self._snapshot: FakeStores | None = None
        self._committed = False
        self._rollback_only = False

    async def __aenter__(self) -> FakeTransactionScope:
        if self._depth == 0:
            self._snapshot = self.stores.snapshot()
            self._committed = False
            self._rollback_only = False
        self._depth += 1
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool | None:
        self._depth -= 1
        if self._depth == 0:
            failed = exc is not None or not self._committed or self._rollback_only
            if failed and self._snapshot is not None:
                self.stores.restore(self._snapshot)
            self._snapshot = None
            self._committed = False
        return None

    async def commit(self) -> None:
        if self._rollback_only:
            raise RuntimeError("cannot commit: this unit of work was rolled back")
        if self._depth > 1:
            return  # participation: the owner commits
        self._committed = True
        self._snapshot = self.stores.snapshot()

    async def rollback(self) -> None:
        self._rollback_only = True
        if self._snapshot is not None:
            self.stores.restore(self._snapshot)

    @property
    def is_participating(self) -> bool:
        return self._depth > 1


class FakeUnitOfWork:
    """Base class for the per-context fake unit of works."""

    def __init__(self, scope: FakeTransactionScope) -> None:
        self._scope = scope

    async def __aenter__(self) -> FakeUnitOfWork:
        await self._scope.__aenter__()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool | None:
        return await self._scope.__aexit__(exc_type, exc, tb)

    async def commit(self) -> None:
        await self._scope.commit()

    async def rollback(self) -> None:
        await self._scope.rollback()


class InMemoryIdempotencyStore:
    """Port implementation with exactly the SQL implementation's semantics."""

    def __init__(self, stores: FakeStores) -> None:
        self._stores = stores

    async def claim(
        self,
        *,
        tenant_id: uuid.UUID,
        key: str,
        operation: str,
        request_hash: str,
        now: datetime,
        expires_at: datetime,
    ) -> IdempotencyClaim:
        existing = self._stores.idempotency.get((tenant_id, key))
        if existing is None:
            record = IdempotencyRecord(
                tenant_id=tenant_id,
                idempotency_key=key,
                operation=operation,
                request_hash=request_hash,
                state=IdempotencyState.IN_PROGRESS,
                resource_type=None,
                resource_id=None,
                response_status=None,
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )
            self._stores.idempotency[(tenant_id, key)] = record
            return IdempotencyClaim(outcome=IdempotencyOutcome.STARTED, record=record)

        if existing.request_hash != request_hash:
            raise DuplicateIdempotencyKey(
                "this idempotency key was already used for a different request",
                details={"idempotency_key": key},
            )
        if existing.state is IdempotencyState.IN_PROGRESS:
            raise IdempotencyOperationInProgress(
                "another request with this idempotency key is still in progress",
                details={"idempotency_key": key},
            )
        return IdempotencyClaim(outcome=IdempotencyOutcome.REPLAY, record=existing)

    async def complete(
        self,
        *,
        tenant_id: uuid.UUID,
        key: str,
        resource_type: str,
        resource_id: uuid.UUID,
        response_status: int,
        now: datetime,
    ) -> None:
        record = self._stores.idempotency[(tenant_id, key)]
        self._stores.idempotency[(tenant_id, key)] = _replace(
            record,
            state=IdempotencyState.COMPLETED,
            resource_type=resource_type,
            resource_id=resource_id,
            response_status=response_status,
            updated_at=now,
        )

    async def abandon(self, *, tenant_id: uuid.UUID, key: str, now: datetime) -> None:
        self._stores.idempotency.pop((tenant_id, key), None)


def _replace(record: IdempotencyRecord, **changes: object) -> IdempotencyRecord:
    import dataclasses

    return dataclasses.replace(record, **changes)  # type: ignore[arg-type]


__all__ = [
    "FakeStores",
    "FakeTransactionScope",
    "FakeUnitOfWork",
    "InMemoryIdempotencyStore",
]
