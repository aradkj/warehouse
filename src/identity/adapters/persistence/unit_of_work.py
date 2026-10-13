"""SQLAlchemy unit of work for the identity context.

The unit of work wraps the shared, participation-aware transaction scope and
exposes the repositories that must share its session.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from audit.adapters.persistence.repositories import SqlAlchemyAuditAdapter
from audit.ports.audit import AuditPort
from identity.adapters.persistence.repositories import (
    SqlAlchemyMembershipRepository,
    SqlAlchemyPolicyRepository,
    SqlAlchemyRoleRepository,
    SqlAlchemyServiceAccountRepository,
    SqlAlchemySessionRepository,
    SqlAlchemyTenantRepository,
    SqlAlchemyUserRepository,
)
from identity.ports.repositories import (
    MembershipRepository,
    PolicyRepository,
    RoleRepository,
    ServiceAccountRepository,
    SessionRepository,
    TenantRepository,
    UserRepository,
)
from shared.application.idempotency import IdempotencyStore
from shared.infrastructure.db.idempotency import SqlAlchemyIdempotencyStore
from shared.infrastructure.db.session import SqlAlchemyTransactionScope


class SqlAlchemyIdentityUnitOfWork:
    """Transaction-scoped access to identity repositories."""

    __slots__ = (
        "_scope",
        "audit",
        "idempotency",
        "memberships",
        "policies",
        "roles",
        "service_accounts",
        "sessions",
        "tenants",
        "users",
    )

    def __init__(self, scope: SqlAlchemyTransactionScope) -> None:
        self._scope = scope

    async def __aenter__(self) -> Self:
        await self._scope.__aenter__()
        session = self._scope.session
        self.tenants: TenantRepository = SqlAlchemyTenantRepository(session)
        self.users: UserRepository = SqlAlchemyUserRepository(session)
        self.memberships: MembershipRepository = SqlAlchemyMembershipRepository(session)
        self.sessions: SessionRepository = SqlAlchemySessionRepository(session)
        self.service_accounts: ServiceAccountRepository = SqlAlchemyServiceAccountRepository(
            session
        )
        self.roles: RoleRepository = SqlAlchemyRoleRepository(session)
        self.policies: PolicyRepository = SqlAlchemyPolicyRepository(session)
        self.audit: AuditPort = SqlAlchemyAuditAdapter(session)
        self.idempotency: IdempotencyStore = SqlAlchemyIdempotencyStore(session)
        return self

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


__all__ = ["SqlAlchemyIdentityUnitOfWork"]
