"""Pure, deterministic policy evaluator (Identity and Access context).

Combines RBAC permission verification with ABAC policy evaluations.
Zero infrastructure dependencies.
"""

from __future__ import annotations

from collections.abc import Sequence

from identity.domain.decision import (
    AuthorizationDecision,
    AuthorizationRequest,
    DecisionReason,
)
from identity.domain.permission import has_permission
from identity.domain.policy import AccessPolicy, PolicyEffect, evaluate_condition
from shared.application.context import ActorType


class PolicyEvaluator:
    """Evaluates an AuthorizationRequest against active policies and RBAC permissions."""

    @staticmethod
    def evaluate(
        request: AuthorizationRequest,
        policies: Sequence[AccessPolicy] = (),
    ) -> AuthorizationDecision:
        """Evaluate an authorization request deterministically.

        Semantics:
        1. SYSTEM actor: allowed immediately (internal operations).
        2. Authenticated principal check: must have principal_id.
        3. Cross-tenant isolation: resource tenant must match caller tenant.
        4. Service account scopes check: service account must explicitly have permission.
        5. RBAC check: caller must have required permission pattern.
        6. ABAC policies check:
           - Explicit DENY policies override ALLOW.
           - Policies evaluate safely against subject, resource, action, environment.
           - Evaluation failures fail-closed.
        7. Default: Deny.
        """
        # 1. System actor
        if request.principal_type == ActorType.SYSTEM:
            # Still check cross-tenant if explicitly mismatched
            if (
                request.resource_tenant_id is not None
                and request.tenant_id is not None
                and request.resource_tenant_id != request.tenant_id
            ):
                return AuthorizationDecision.deny(
                    request,
                    DecisionReason.DENIED_CROSS_TENANT,
                    details="resource belongs to a different tenant",
                )
            return AuthorizationDecision.allow(
                request,
                DecisionReason.ALLOWED_SYSTEM_ACTOR,
                details="trusted internal system execution",
            )

        # 2. Authenticated principal
        if not request.principal_id:
            return AuthorizationDecision.deny(
                request,
                DecisionReason.DENIED_UNAUTHENTICATED,
                details="unauthenticated request",
            )

        # 3. Cross-tenant isolation
        if (
            request.resource_tenant_id is not None
            and request.tenant_id is not None
            and request.resource_tenant_id != request.tenant_id
        ):
            return AuthorizationDecision.deny(
                request,
                DecisionReason.DENIED_CROSS_TENANT,
                details="resource belongs to a different tenant",
            )

        # 4. Service account scopes / RBAC check
        if not has_permission(request.permissions, request.action):
            if request.principal_type == ActorType.SERVICE_ACCOUNT:
                return AuthorizationDecision.deny(
                    request,
                    DecisionReason.DENIED_SERVICE_ACCOUNT_SCOPE,
                    details=f"service account lacks required scope '{request.action}'",
                )
            return AuthorizationDecision.deny(
                request,
                DecisionReason.DENIED_MISSING_PERMISSION,
                details=f"principal lacks required permission '{request.action}'",
            )

        # 5. Build context data for ABAC evaluation
        context_data = {
            "subject": {
                "id": request.principal_id,
                "type": str(request.principal_type),
                "roles": list(request.roles),
                **request.subject_attributes,
            },
            "resource": {
                "type": request.resource_type,
                "id": request.resource_id,
                "tenant_id": str(request.resource_tenant_id)
                if request.resource_tenant_id
                else None,
                **request.resource_attributes,
            },
            "action": request.action,
            "environment": request.environment,
        }

        # 6. Filter active policies matching action and resource
        applicable_policies = [
            p
            for p in policies
            if p.is_active
            and p.matches_action(request.action)
            and p.matches_resource(request.resource_type, request.resource_id)
            and (p.tenant_id is None or p.tenant_id == request.tenant_id)
        ]

        # Check explicit DENY first (Deny-Overrides)
        for policy in applicable_policies:
            if policy.effect == PolicyEffect.DENY:
                try:
                    matched = evaluate_condition(policy.conditions, context_data)
                except Exception as exc:
                    # Fail-closed on evaluation error
                    return AuthorizationDecision.deny(
                        request,
                        DecisionReason.DENIED_EVALUATION_ERROR,
                        policy_id=policy.id,
                        policy_version=policy.version,
                        details=f"policy evaluation failed: {exc}",
                    )

                if matched:
                    return AuthorizationDecision.deny(
                        request,
                        DecisionReason.DENIED_BY_ABAC_POLICY,
                        policy_id=policy.id,
                        policy_version=policy.version,
                        details=f"denied by policy '{policy.name}'",
                    )

        # Check explicit ALLOW policies
        for policy in applicable_policies:
            if policy.effect == PolicyEffect.ALLOW:
                try:
                    matched = evaluate_condition(policy.conditions, context_data)
                except Exception as exc:
                    return AuthorizationDecision.deny(
                        request,
                        DecisionReason.DENIED_EVALUATION_ERROR,
                        policy_id=policy.id,
                        policy_version=policy.version,
                        details=f"policy evaluation failed: {exc}",
                    )

                if matched:
                    return AuthorizationDecision.allow(
                        request,
                        DecisionReason.ALLOWED_BY_ABAC_POLICY,
                        policy_id=policy.id,
                        policy_version=policy.version,
                        details=f"authorized by policy '{policy.name}'",
                    )

        # Default allowed by RBAC since permission was satisfied and no ABAC deny matched
        return AuthorizationDecision.allow(
            request,
            DecisionReason.ALLOWED_BY_RBAC,
            details=f"authorized by role permissions for '{request.action}'",
        )


__all__ = ["PolicyEvaluator"]
