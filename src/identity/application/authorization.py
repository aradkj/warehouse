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
    DecisionReason,
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


_policy_cache: dict[TenantId, tuple[float, Sequence[AccessPolicy]]] = {}
_POLICY_CACHE_TTL_SECONDS: float = 30.0


def invalidate_policy_cache(tenant_id: TenantId | None = None) -> None:
    """Clear cached policies for a tenant or globally across the local process.

    Note on multi-worker architectures:
    In multi-worker deployments (e.g. Uvicorn worker clusters or container replicas),
    in-process cache invalidation clears cached policies within the local worker process.
    Peer worker processes will reload policies upon expiry of the short-lived bounded
    TTL (_POLICY_CACHE_TTL_SECONDS = 30.0s). For instant cross-worker invalidation without
    TTL lag, distributed pub/sub or Redis cache invalidation is required.
    """
    if tenant_id is not None:
        _policy_cache.pop(tenant_id, None)
    else:
        _policy_cache.clear()


class ApplicationAuthorizationService(AuthorizationPort):
    """Central authorization service combining RBAC role resolution and ABAC policies."""

    def __init__(
        self,
        *,
        uow_factory: Callable[..., IdentityUnitOfWork],
        audit: AuditPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit

    def _uow(self, tenant_id: TenantId | None = None) -> IdentityUnitOfWork:
        import inspect

        sig = inspect.signature(self._uow_factory)
        if len(sig.parameters) > 0:
            return self._uow_factory(tenant_id)
        return self._uow_factory()

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
            async with self._uow(principal.active_tenant_id) as uow:
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
        if principal.role and principal.role in SYSTEM_ROLES_PERMISSIONS:
            effective.update(SYSTEM_ROLES_PERMISSIONS[principal.role])

        for role_name in principal.roles:
            if role_name in SYSTEM_ROLES_PERMISSIONS:
                effective.update(SYSTEM_ROLES_PERMISSIONS[role_name])

        target_tenant = tenant_id or principal.active_tenant_id
        if target_tenant is not None:
            async with self._uow(target_tenant) as uow:
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
                attributes=req.subject_attributes,
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

        # Fetch active policies for tenant (cached with short TTL for high throughput)
        policies: Sequence[AccessPolicy] = ()
        if req.tenant_id is not None:
            import time

            now_mono = time.monotonic()
            cached = _policy_cache.get(req.tenant_id)
            if cached is not None and (now_mono - cached[0]) < _POLICY_CACHE_TTL_SECONDS:
                policies = cached[1]
            else:
                async with self._uow(req.tenant_id) as uow:
                    try:
                        policies = tuple(
                            await uow.policies.list_for_tenant(req.tenant_id, active_only=True)
                        )
                        _policy_cache[req.tenant_id] = (now_mono, policies)
                    except Exception as exc:
                        logger.warning(
                            "Error loading policies for tenant %s: %s", req.tenant_id, exc
                        )
                        # Fail closed: Never allow access if policies cannot be loaded
                        error_decision = AuthorizationDecision.deny(
                            req,
                            DecisionReason.DENIED_EVALUATION_ERROR,
                            details=f"failed to load access policies: {exc}",
                        )
                        if self._audit is not None:
                            try:
                                corr_id = req.environment.get("correlation_id")
                                caus_id = req.environment.get("causation_id") or corr_id
                                await self._audit.record(
                                    AuditEntry(
                                        audit_id=str(uuid.uuid4()),
                                        tenant_id=str(req.tenant_id),
                                        actor_id=req.principal_id or "anonymous",
                                        action=req.action,
                                        resource_type=req.resource_type,
                                        resource_id=req.resource_id or "none",
                                        correlation_id=corr_id,
                                        causation_id=caus_id,
                                        occurred_at=datetime.now(UTC),
                                        before=None,
                                        after={
                                            "allowed": False,
                                            "reason": error_decision.reason.value,
                                            "details": error_decision.reason_details,
                                        },
                                    )
                                )
                            except Exception as audit_exc:
                                logger.warning(
                                    "Failed to record authorization error audit: %s", audit_exc
                                )
                        return error_decision

        decision = PolicyEvaluator.evaluate(req, policies)

        # Audit recording for sensitive administrative operations or any access denial
        is_denial = not decision.allowed
        is_sensitive = (
            req.action.startswith("access.")
            or req.action.startswith("identity.")
            or ".role." in req.action
            or ".policy." in req.action
            or ".service_account." in req.action
        )
        if self._audit is not None and (is_denial or is_sensitive):
            try:
                corr_id = req.environment.get("correlation_id")
                caus_id = req.environment.get("causation_id") or corr_id
                entry = AuditEntry(
                    audit_id=str(uuid.uuid4()),
                    tenant_id=str(req.tenant_id) if req.tenant_id else "system",
                    actor_id=req.principal_id or "anonymous",
                    action=req.action,
                    resource_type=req.resource_type,
                    resource_id=req.resource_id or "none",
                    correlation_id=corr_id,
                    causation_id=caus_id,
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


__all__ = ["ApplicationAuthorizationService", "invalidate_policy_cache"]
