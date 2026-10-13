"""Access management router for RBAC and ABAC administration."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from bootstrap.dependencies import (
    UseCases,
    get_authenticated_tenant_context,
    get_authorization_service,
    get_use_cases,
    require_current_principal,
    require_permission,
    require_tenant_admin,
)
from bootstrap.routers.identity import apply_outcome
from bootstrap.schemas.access import (
    AssignRoleRequest,
    CreatePolicyRequest,
    CreateRoleRequest,
    DecisionEvaluationRequest,
    DecisionEvaluationResponse,
    PermissionListResponse,
    PermissionResponse,
    PolicyResponse,
    RoleResponse,
    UpdatePolicyStatusRequest,
)
from bootstrap.schemas.common import ErrorResponse
from bootstrap.schemas.identity import TenantMembershipResponse
from identity.application.authorization import ApplicationAuthorizationService
from identity.application.commands import (
    AssignRoleCommand,
    CreatePolicyCommand,
    CreateRoleCommand,
    GetPolicyQuery,
    GetRoleQuery,
    ListPoliciesQuery,
    ListRolesQuery,
    RevokeRoleCommand,
    UpdatePolicyStatusCommand,
)
from identity.domain.decision import AuthorizationRequest
from identity.domain.permission import ALL_PERMISSIONS
from identity.domain.principal import Principal
from shared.application.context import RequestContext
from shared.domain.identifiers import MembershipId, TenantId

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}

router = APIRouter(prefix="/access", tags=["access"])


# ===========================================================================
# Canonical Permissions Catalog
# ===========================================================================
@router.get(
    "/permissions",
    response_model=PermissionListResponse,
    summary="List canonical system permissions",
    responses=ERROR_RESPONSES,
)
async def list_permissions(
    _principal: Annotated[Principal, Depends(require_current_principal)],
) -> PermissionListResponse:
    """Return all documented platform permissions."""
    items = []
    for p in ALL_PERMISSIONS:
        parts = p.split(".")
        category = parts[0] if parts else "general"
        items.append(
            PermissionResponse(
                name=p,
                description=f"Permission granting action '{p}'",
                category=category,
            )
        )
    return PermissionListResponse(permissions=items, total=len(items))


# ===========================================================================
# Current Principal Identity & Permissions
# ===========================================================================
@router.get(
    "/me",
    summary="Inspect effective permissions and roles for current caller",
    responses=ERROR_RESPONSES,
)
async def get_my_access(
    principal: Annotated[Principal, Depends(require_current_principal)],
) -> dict[str, object]:
    """Return current caller's effective roles and permissions."""
    return {
        "id": principal.id,
        "type": principal.type.value if hasattr(principal.type, "value") else str(principal.type),
        "email": principal.email,
        "active_tenant_id": principal.active_tenant_id,
        "role": principal.role,
        "roles": list(principal.roles),
        "permissions": sorted(principal.permissions),
        "is_tenant_admin": principal.is_tenant_admin,
    }


# ===========================================================================
# Roles Management
# ===========================================================================
@router.post(
    "/roles",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a custom role for the tenant",
    responses=ERROR_RESPONSES,
)
async def create_role(
    payload: CreateRoleRequest,
    response: Response,
    admin: Annotated[Principal, Depends(require_tenant_admin)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> RoleResponse:
    assert context.tenant_id is not None
    outcome = await use_cases.create_role(
        CreateRoleCommand(
            tenant_id=context.tenant_id,
            context=context,
            name=payload.name,
            description=payload.description,
            permissions=tuple(payload.permissions),
        )
    )
    apply_outcome(response, outcome)
    return RoleResponse.from_view(outcome.view)


@router.get(
    "/roles",
    response_model=list[RoleResponse],
    summary="List roles accessible to the tenant",
    responses=ERROR_RESPONSES,
)
async def list_roles(
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("access.roles.read"))],
) -> list[RoleResponse]:
    roles = await use_cases.list_roles(ListRolesQuery(context=context, tenant_id=context.tenant_id))
    return [RoleResponse.from_view(view) for view in roles]


@router.get(
    "/roles/{role_id}",
    response_model=RoleResponse,
    summary="Get role by ID",
    responses=ERROR_RESPONSES,
)
async def get_role(
    role_id: UUID,
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("access.roles.read"))],
) -> RoleResponse:
    role = await use_cases.get_role(GetRoleQuery(context=context, role_id=str(role_id)))
    return RoleResponse.from_view(role)


@router.post(
    "/memberships/{membership_id}/roles",
    response_model=TenantMembershipResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign role to a tenant membership",
    responses=ERROR_RESPONSES,
)
async def assign_role(
    membership_id: UUID,
    payload: AssignRoleRequest,
    response: Response,
    admin: Annotated[Principal, Depends(require_tenant_admin)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> TenantMembershipResponse:
    assert context.tenant_id is not None
    outcome = await use_cases.assign_role(
        AssignRoleCommand(
            tenant_id=context.tenant_id,
            membership_id=MembershipId(membership_id),
            role_name=payload.role_name,
            context=context,
        )
    )
    apply_outcome(response, outcome)
    return TenantMembershipResponse.from_view(outcome.view)


@router.delete(
    "/memberships/{membership_id}/roles/{role_name}",
    response_model=TenantMembershipResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke role from a tenant membership",
    responses=ERROR_RESPONSES,
)
async def revoke_role(
    membership_id: UUID,
    role_name: str,
    response: Response,
    admin: Annotated[Principal, Depends(require_tenant_admin)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> TenantMembershipResponse:
    assert context.tenant_id is not None
    outcome = await use_cases.revoke_role(
        RevokeRoleCommand(
            tenant_id=context.tenant_id,
            membership_id=MembershipId(membership_id),
            role_name=role_name,
            context=context,
        )
    )
    apply_outcome(response, outcome)
    return TenantMembershipResponse.from_view(outcome.view)


# ===========================================================================
# Policies Management (ABAC)
# ===========================================================================
@router.post(
    "/policies",
    response_model=PolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an access policy",
    responses=ERROR_RESPONSES,
)
async def create_policy(
    payload: CreatePolicyRequest,
    response: Response,
    admin: Annotated[Principal, Depends(require_tenant_admin)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> PolicyResponse:
    assert context.tenant_id is not None
    resources = payload.resources or payload.resource_types
    conditions = payload.conditions or payload.condition
    outcome = await use_cases.create_policy(
        CreatePolicyCommand(
            tenant_id=context.tenant_id,
            context=context,
            name=payload.name,
            description=payload.description,
            effect=payload.effect,
            actions=tuple(payload.actions),
            resources=tuple(resources),
            conditions=conditions,
        )
    )
    apply_outcome(response, outcome)
    return PolicyResponse.from_view(outcome.view)


@router.get(
    "/policies",
    response_model=list[PolicyResponse],
    summary="List policies for tenant",
    responses=ERROR_RESPONSES,
)
async def list_policies(
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("access.policies.read"))],
) -> list[PolicyResponse]:
    policies = await use_cases.list_policies(
        ListPoliciesQuery(context=context, tenant_id=context.tenant_id, active_only=False)
    )
    return [PolicyResponse.from_view(view) for view in policies]


@router.get(
    "/policies/{policy_id}",
    response_model=PolicyResponse,
    summary="Get policy by ID",
    responses=ERROR_RESPONSES,
)
async def get_policy(
    policy_id: UUID,
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    _principal: Annotated[Principal, Depends(require_permission("access.policies.read"))],
) -> PolicyResponse:
    policy = await use_cases.get_policy(GetPolicyQuery(context=context, policy_id=str(policy_id)))
    return PolicyResponse.from_view(policy)


@router.patch(
    "/policies/{policy_id}/status",
    response_model=PolicyResponse,
    summary="Update policy status (ACTIVE / INACTIVE)",
    responses=ERROR_RESPONSES,
)
@router.post(
    "/policies/{policy_id}/status",
    response_model=PolicyResponse,
    summary="Update policy status (ACTIVE / INACTIVE)",
    responses=ERROR_RESPONSES,
)
async def update_policy_status(
    policy_id: UUID,
    payload: UpdatePolicyStatusRequest,
    response: Response,
    admin: Annotated[Principal, Depends(require_tenant_admin)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> PolicyResponse:
    assert context.tenant_id is not None
    outcome = await use_cases.update_policy_status(
        UpdatePolicyStatusCommand(
            policy_id=str(policy_id),
            tenant_id=context.tenant_id,
            status=payload.status,
            context=context,
        )
    )
    apply_outcome(response, outcome)
    return PolicyResponse.from_view(outcome.view)


# ===========================================================================
# Policy Evaluation Engine
# ===========================================================================
@router.post(
    "/evaluate",
    response_model=DecisionEvaluationResponse,
    summary="Evaluate an authorization decision against RBAC and ABAC policies",
    responses=ERROR_RESPONSES,
)
async def evaluate_access(
    payload: DecisionEvaluationRequest,
    principal: Annotated[Principal, Depends(require_current_principal)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    auth_service: Annotated[ApplicationAuthorizationService, Depends(get_authorization_service)],
) -> DecisionEvaluationResponse:
    req = AuthorizationRequest(
        principal_id=principal.id,
        principal_type=principal.type,
        tenant_id=principal.active_tenant_id,
        roles=principal.roles,
        permissions=principal.permissions,
        action=payload.action,
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
        resource_tenant_id=TenantId(payload.resource_tenant_id)
        if payload.resource_tenant_id
        else context.tenant_id,
        resource_attributes=payload.resource_attributes,
        environment=payload.environment,
    )
    decision = await auth_service.authorize(req)

    # Sanitize internals: non-admins only receive coarse reason codes and no internal policy ID
    if principal.is_tenant_admin or principal.is_system:
        reason = decision.reason_details or decision.reason.value
        policy_id = UUID(str(decision.policy_id)) if decision.policy_id else None
    else:
        reason = decision.reason.value
        policy_id = None

    return DecisionEvaluationResponse(
        allowed=decision.allowed,
        reason=reason,
        reason_code=decision.reason.value,
        action=decision.action,
        resource_type=decision.resource_type,
        policy_id=policy_id,
        missing_permissions=[],
    )
