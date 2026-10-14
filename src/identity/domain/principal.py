"""Framework-independent authenticated principal representation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from identity.domain.permission import has_permission
from shared.application.context import ActorType, RequestContext
from shared.domain.identifiers import MembershipId, TenantId


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated caller identity (human user or service account)."""

    id: str
    type: ActorType
    email: str | None = None
    active_tenant_id: TenantId | None = None
    active_membership_id: MembershipId | None = None
    account_status: str = "ACTIVE"
    session_id: str | None = None
    role: str = "MEMBER"
    roles: tuple[str, ...] = ("MEMBER",)
    permissions: frozenset[str] = field(default_factory=frozenset)
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def tenant_id(self) -> TenantId | None:
        return self.active_tenant_id

    @property
    def is_user(self) -> bool:
        return self.type == ActorType.USER

    @property
    def is_service_account(self) -> bool:
        return self.type == ActorType.SERVICE_ACCOUNT

    @property
    def is_system(self) -> bool:
        return self.type == ActorType.SYSTEM

    @property
    def is_tenant_admin(self) -> bool:
        return self.is_user and (self.role == "ADMIN" or "ADMIN" in self.roles)

    def has_permission(self, permission: str) -> bool:
        """Check if principal possesses the specified permission pattern."""
        return has_permission(self.permissions, permission)

    def to_request_context(
        self,
        *,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> RequestContext:
        """Derive a tenant-aware RequestContext from this authenticated principal."""
        return RequestContext(
            tenant_id=self.active_tenant_id,
            actor_id=self.id,
            actor_type=self.type,
            principal=self,
            correlation_id=correlation_id or str(uuid.uuid4()),
            idempotency_key=idempotency_key,
        )


__all__ = ["Principal"]
