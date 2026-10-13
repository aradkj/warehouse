"""Authorization port interface (Identity and Access context)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from identity.domain.decision import AuthorizationDecision, AuthorizationRequest
from identity.domain.principal import Principal
from shared.domain.identifiers import TenantId


@runtime_checkable
class AuthorizationPort(Protocol):
    """Port for resolving effective permissions and evaluating authorization decisions."""

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Evaluate an authorization request against roles and policies."""
        ...

    async def get_effective_permissions(
        self,
        principal: Principal,
        tenant_id: TenantId | None,
    ) -> frozenset[str]:
        """Compute the complete set of effective permissions for a principal."""
        ...


__all__ = [
    "AuthorizationDecision",
    "AuthorizationPort",
    "AuthorizationRequest",
    "Principal",
]
