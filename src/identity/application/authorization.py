"""Application authorization service implementing AuthorizationPort."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from audit.ports import AuditEntry, AuditPort
from identity.domain.decision import (
    AuthorizationDecision,
    AuthorizationRequest,
)
from identity.domain.evaluator import PolicyEvaluator
from identity.domain.permission import ALL_PERMISSIONS
from identity.domain.policy import AccessPolicy
from identity.domain.principal import Principal
from identity.domain.role import SYSTEM_ROLES_PERMISSIONS
from identity.ports.authorization import AuthorizationPort
from identity.ports.unit_of_work import IdentityUnitOfWork
from shared.application.context import ActorType
from shared.domain.identifiers import TenantId, UserId

logger = logging.getLogger(__name__)


class ApplicationAuthorizationService(AuthorizationPort):
    """Central authorization service combining RBAC role resolution and ABAC policies."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        audit: AuditPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit

    async def get_effective_permissions(
        self,
        principal: Principal,
        tenant_id: TenantId | None,
    ) -> frozenset[str]:
        """Compute the combined effective permissions for a principal within a tenant context."""
        if principal.type == ActorType.SYSTEM:
            return ALL_PERMISSIONS

        if principal.type == ActorType.SERVICE_ACCOUNT:
            # If principal already carries permissions/scopes, use them
            if principal.permissions:
                return principal.permissions

            # Otherwise, look up service account in database
            async with self._uow_factory() as uow:
                from shared.domain.identifiers import ServiceAccountId

                try:
                    sa_id = ServiceAccountId(uuid.UUID(principal.id))
                    sa = await uow.service_accounts.get(sa_id)
                    if sa is not None:
                        return sa.scopes
                except (ValueError, TypeError):
                    return frozenset()
            return frozenset()

        # Human user: look up membership and roles
        effective: set[str] = set()

        # Check legacy/direct role attribute on principal
        if principal.role in SYSTEM_ROLES_PERMISSIONS:
            effective.update(SYSTEM_ROLES_PERMISSIONS[principal.role])

        for role_name in principal.roles:
            if role_name in SYSTEM_ROLES_PERMISSIONS:
                effective.update(SYSTEM_ROLES_PERMISSIONS[role_name])

        target_tenant = tenant_id or principal.active_tenant_id
        if target_tenant is not None:
            async with self._uow_factory() as uow:
                try:
                    user_id = UserId(uuid.UUID(principal.id))
                    membership = await uow.memberships.get_by_user_and_tenant(
                        user_id, target_tenant
                    )
                    if membership is not None and membership.is_active:
                        # Include permissions from membership role
                        if membership.role.value in SYSTEM_ROLES_PERMISSIONS:
                            effective.update(SYSTEM_ROLES_PERMISSIONS[membership.role.value])

                        for r_name in membership.roles:
                            if r_name in SYSTEM_ROLES_PERMISSIONS:
                                effective.update(SYSTEM_ROLES_PERMISSIONS[r_name])
                            else:
                                # Look up custom tenant role
                                custom_role = await uow.roles.get_by_name(
                                    r_name, tenant_id=target_tenant
                                )
                                if custom_role is not None:
                                    effective.update(custom_role.permissions)
                except Exception as exc:
                    logger.warning(
                        "Error resolving permissions for principal %s: %s", principal.id, exc
                    )

        return frozenset(effective)

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Evaluate an authorization request against permissions and active ABAC policies."""
        # If permissions are empty on request, populate effective permissions
        req = request
        if not req.permissions and req.principal_id:
            principal = Principal(
                id=req.principal_id,
                type=req.principal_type,
                active_tenant_id=req.tenant_id,
                role=req.roles[0] if req.roles else "MEMBER",
                roles=req.roles,
            )
            perms = await self.get_effective_permissions(principal, req.tenant_id)
            req = AuthorizationRequest(
                action=request.action,
                resource_type=request.resource_type,
                principal_id=request.principal_id,
                principal_type=request.principal_type,
                tenant_id=request.tenant_id,
                roles=request.roles,
                permissions=perms,
                subject_attributes=request.subject_attributes,
                resource_id=request.resource_id,
                resource_tenant_id=request.resource_tenant_id,
                resource_attributes=request.resource_attributes,
                environment=request.environment,
            )

        # Fetch active policies for tenant
        policies: Sequence[AccessPolicy] = ()
        if req.tenant_id is not None:
            async with self._uow_factory() as uow:
                try:
                    policies = await uow.policies.list_for_tenant(req.tenant_id, active_only=True)
                except Exception as exc:
                    logger.warning("Error loading policies for tenant %s: %s", req.tenant_id, exc)

        decision = PolicyEvaluator.evaluate(req, policies)

        # Audit recording for sensitive operations or denials
        if self._audit is not None:
            try:
                entry = AuditEntry(
                    audit_id=str(uuid.uuid4()),
                    tenant_id=str(req.tenant_id) if req.tenant_id else "system",
                    actor_id=req.principal_id or "anonymous",
                    action=req.action,
                    resource_type=req.resource_type,
                    resource_id=req.resource_id or "none",
                    correlation_id=req.environment.get("correlation_id"),
                    causation_id=None,
                    occurred_at=datetime.now(UTC),
                    before=None,
                    after={
                        "decision": "ALLOW" if decision.allowed else "DENY",
                        "reason": decision.reason.value,
                        "reason_details": decision.reason_details,
                        "policy_id": str(decision.policy_id) if decision.policy_id else None,
                        "policy_version": decision.policy_version,
                    },
                )
                await self._audit.record(entry)
            except Exception as exc:
                logger.error("Failed to record audit entry: %s", exc)

        return decision


__all__ = ["ApplicationAuthorizationService"]
