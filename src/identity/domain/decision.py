"""Authorization decision model and request context."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from shared.application.context import ActorType
from shared.domain.identifiers import PolicyId, TenantId


class DecisionReason(StrEnum):
    """Reason code indicating how the authorization decision was reached."""

    # Allowed
    ALLOWED_BY_RBAC = "ALLOWED_BY_RBAC"
    ALLOWED_BY_ABAC_POLICY = "ALLOWED_BY_ABAC_POLICY"
    ALLOWED_SYSTEM_ACTOR = "ALLOWED_SYSTEM_ACTOR"

    # Denied
    DENIED_UNAUTHENTICATED = "DENIED_UNAUTHENTICATED"
    DENIED_INACTIVE_MEMBERSHIP = "DENIED_INACTIVE_MEMBERSHIP"
    DENIED_CROSS_TENANT = "DENIED_CROSS_TENANT"
    DENIED_MISSING_PERMISSION = "DENIED_MISSING_PERMISSION"
    DENIED_SERVICE_ACCOUNT_SCOPE = "DENIED_SERVICE_ACCOUNT_SCOPE"
    DENIED_BY_ABAC_POLICY = "DENIED_BY_ABAC_POLICY"
    DENIED_ATTRIBUTE_MISMATCH = "DENIED_ATTRIBUTE_MISMATCH"
    DENIED_EVALUATION_ERROR = "DENIED_EVALUATION_ERROR"
    DENIED_DEFAULT = "DENIED_DEFAULT"


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """Complete authorization context evaluated by the policy engine."""

    action: str
    resource_type: str
    principal_id: str | None = None
    principal_type: ActorType = ActorType.USER
    tenant_id: TenantId | None = None
    roles: tuple[str, ...] = ()
    permissions: frozenset[str] = field(default_factory=frozenset)
    subject_attributes: dict[str, Any] = field(default_factory=dict)
    resource_id: str | None = None
    resource_tenant_id: TenantId | None = None
    resource_attributes: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Structured decision returned by the authorization engine."""

    allowed: bool
    reason: DecisionReason
    action: str
    resource_type: str
    resource_id: str | None = None
    actor_id: str | None = None
    tenant_id: str | None = None
    reason_details: str | None = None
    policy_id: PolicyId | None = None
    policy_version: int | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def allow(
        cls,
        request: AuthorizationRequest,
        reason: DecisionReason = DecisionReason.ALLOWED_BY_RBAC,
        *,
        policy_id: PolicyId | None = None,
        policy_version: int | None = None,
        details: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AuthorizationDecision:
        env = request.environment or {}
        corr = correlation_id or env.get("correlation_id")
        caus = causation_id or env.get("causation_id")
        return cls(
            allowed=True,
            reason=reason,
            action=request.action,
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            actor_id=request.principal_id,
            tenant_id=str(request.tenant_id) if request.tenant_id else None,
            reason_details=details,
            policy_id=policy_id,
            policy_version=policy_version,
            correlation_id=corr,
            causation_id=caus,
        )

    @classmethod
    def deny(
        cls,
        request: AuthorizationRequest,
        reason: DecisionReason,
        *,
        policy_id: PolicyId | None = None,
        policy_version: int | None = None,
        details: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> AuthorizationDecision:
        env = request.environment or {}
        corr = correlation_id or env.get("correlation_id")
        caus = causation_id or env.get("causation_id")
        return cls(
            allowed=False,
            reason=reason,
            action=request.action,
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            actor_id=request.principal_id,
            tenant_id=str(request.tenant_id) if request.tenant_id else None,
            reason_details=details,
            policy_id=policy_id,
            policy_version=policy_version,
            correlation_id=corr,
            causation_id=caus,
        )


__all__ = [
    "AuthorizationDecision",
    "AuthorizationRequest",
    "DecisionReason",
]
