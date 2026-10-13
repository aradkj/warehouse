"""Identity and authentication HTTP router."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status

from bootstrap.dependencies import (
    UseCases,
    apply_outcome,
    get_authenticated_tenant_context,
    get_request_context,
    get_use_cases,
    require_current_principal,
    require_tenant_admin,
)
from bootstrap.schemas.common import ErrorResponse
from bootstrap.schemas.identity import (
    AuthenticateServiceAccountRequest,
    AuthTokensResponse,
    ChangePasswordRequest,
    CreateInvitationRequest,
    CreateServiceAccountRequest,
    InvitationResponse,
    LoginRequest,
    PrincipalResponse,
    ProvisionMembershipRequest,
    RefreshTokenRequest,
    RegisterUserRequest,
    SelectTenantRequest,
    ServiceAccountCreatedResponse,
    ServiceAccountResponse,
    ServiceAccountTokensResponse,
    TenantMembershipResponse,
    UserResponse,
)
from identity.application.commands import (
    AuthenticateServiceAccountCommand,
    AuthenticateUserCommand,
    ChangePasswordCommand,
    CreateInvitationCommand,
    CreateServiceAccountCommand,
    ListMembershipsQuery,
    ProvisionMembershipCommand,
    RefreshTokenCommand,
    RegisterUserCommand,
    RevokeServiceAccountCommand,
    RevokeSessionCommand,
    RotateServiceAccountSecretCommand,
    SelectTenantCommand,
)
from identity.domain.principal import Principal
from shared.application.context import RequestContext
from shared.domain.identifiers import ServiceAccountId, TenantId, UserId

router = APIRouter(prefix="/auth", tags=["identity"])

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a user",
    responses=ERROR_RESPONSES,
)
async def register_user(
    payload: RegisterUserRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_request_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> UserResponse:
    outcome = await use_cases.register_user(
        RegisterUserCommand(
            context=context,
            email=payload.email,
            password=payload.password,
            tenant_id=TenantId(payload.tenant_id) if payload.tenant_id else None,
            invitation_token=payload.invitation_token,
        )
    )
    apply_outcome(response, outcome)
    return UserResponse.from_view(outcome.view)


@router.post(
    "/login",
    response_model=AuthTokensResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate a user",
    responses=ERROR_RESPONSES,
)
async def login(
    payload: LoginRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_request_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> AuthTokensResponse:
    outcome = await use_cases.authenticate_user(
        AuthenticateUserCommand(
            context=context,
            email=payload.email,
            password=payload.password,
            tenant_id=TenantId(payload.tenant_id) if payload.tenant_id else None,
        )
    )
    apply_outcome(response, outcome)
    return AuthTokensResponse.from_view(outcome.view)


@router.post(
    "/refresh",
    response_model=AuthTokensResponse,
    status_code=status.HTTP_200_OK,
    summary="Refresh access token",
    responses=ERROR_RESPONSES,
)
async def refresh_token(
    payload: RefreshTokenRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_request_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> AuthTokensResponse:
    outcome = await use_cases.refresh_token(
        RefreshTokenCommand(context=context, refresh_token=payload.refresh_token)
    )
    apply_outcome(response, outcome)
    return AuthTokensResponse.from_view(outcome.view)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke session (logout)",
    responses=ERROR_RESPONSES,
)
async def logout(
    payload: RefreshTokenRequest,
    context: Annotated[RequestContext, Depends(get_request_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> Response:
    await use_cases.revoke_session(
        RevokeSessionCommand(context=context, refresh_token=payload.refresh_token)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=PrincipalResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current principal profile",
    responses=ERROR_RESPONSES,
)
async def get_me(
    principal: Annotated[Principal, Depends(require_current_principal)],
) -> PrincipalResponse:
    return PrincipalResponse.from_principal(principal)


@router.get(
    "/memberships",
    response_model=list[TenantMembershipResponse],
    status_code=status.HTTP_200_OK,
    summary="List available tenant memberships",
    responses=ERROR_RESPONSES,
)
async def list_memberships(
    principal: Annotated[Principal, Depends(require_current_principal)],
    context: Annotated[RequestContext, Depends(get_request_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> list[TenantMembershipResponse]:
    views = await use_cases.list_memberships(
        ListMembershipsQuery(context=context, user_id=UserId(UUID(principal.id)))
    )
    return [TenantMembershipResponse.from_view(view) for view in views]


@router.post(
    "/select-tenant",
    response_model=AuthTokensResponse,
    status_code=status.HTTP_200_OK,
    summary="Select active tenant membership",
    responses=ERROR_RESPONSES,
)
async def select_tenant(
    payload: SelectTenantRequest,
    response: Response,
    principal: Annotated[Principal, Depends(require_current_principal)],
    context: Annotated[RequestContext, Depends(get_request_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> AuthTokensResponse:
    outcome = await use_cases.select_tenant(
        SelectTenantCommand(
            context=context,
            user_id=UserId(UUID(principal.id)),
            tenant_id=TenantId(payload.tenant_id),
        )
    )
    apply_outcome(response, outcome)
    return AuthTokensResponse.from_view(outcome.view)


@router.post(
    "/change-password",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Change user password and revoke other sessions",
    responses=ERROR_RESPONSES,
)
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    principal: Annotated[Principal, Depends(require_current_principal)],
    context: Annotated[RequestContext, Depends(get_request_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> UserResponse:
    outcome = await use_cases.change_password(
        ChangePasswordCommand(
            context=context,
            user_id=UserId(UUID(principal.id)),
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    )
    apply_outcome(response, outcome)
    return UserResponse.from_view(outcome.view)


# ===========================================================================
# Invitations & Memberships (Tenant Administration)
# ===========================================================================
@router.post(
    "/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an invitation token for a tenant",
    responses=ERROR_RESPONSES,
)
async def create_invitation(
    payload: CreateInvitationRequest,
    response: Response,
    admin: Annotated[Principal, Depends(require_tenant_admin)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> InvitationResponse:
    assert context.tenant_id is not None
    outcome = await use_cases.create_invitation(
        CreateInvitationCommand(
            context=context,
            tenant_id=context.tenant_id,
            email=payload.email,
            role=payload.role,
        )
    )
    apply_outcome(response, outcome)
    return InvitationResponse.from_view(outcome.view)


@router.post(
    "/memberships",
    response_model=TenantMembershipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision a membership for an existing user in a tenant",
    responses=ERROR_RESPONSES,
)
async def provision_membership(
    payload: ProvisionMembershipRequest,
    response: Response,
    admin: Annotated[Principal, Depends(require_tenant_admin)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> TenantMembershipResponse:
    assert context.tenant_id is not None
    outcome = await use_cases.provision_membership(
        ProvisionMembershipCommand(
            context=context,
            tenant_id=context.tenant_id,
            user_id=UserId(payload.user_id) if payload.user_id else None,
            email=payload.email,
            role=payload.role,
        )
    )
    apply_outcome(response, outcome)
    return TenantMembershipResponse.from_view(outcome.view)


# ===========================================================================
# Service Accounts (Tenant Administration)
# ===========================================================================
@router.post(
    "/service-accounts",
    response_model=ServiceAccountCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a service account for a tenant",
    responses=ERROR_RESPONSES,
)
async def create_service_account(
    payload: CreateServiceAccountRequest,
    response: Response,
    admin: Annotated[Principal, Depends(require_tenant_admin)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> ServiceAccountCreatedResponse:
    assert context.tenant_id is not None
    outcome = await use_cases.create_service_account(
        CreateServiceAccountCommand(
            tenant_id=context.tenant_id,
            context=context,
            name=payload.name,
            client_id=payload.client_id,
            scopes=tuple(payload.scopes),
        )
    )
    apply_outcome(response, outcome)
    return ServiceAccountCreatedResponse.from_view(outcome.view)


@router.post(
    "/service-accounts/token",
    response_model=ServiceAccountTokensResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate a service account",
    responses=ERROR_RESPONSES,
)
async def authenticate_service_account(
    payload: AuthenticateServiceAccountRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_request_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> ServiceAccountTokensResponse:
    outcome = await use_cases.authenticate_service_account(
        AuthenticateServiceAccountCommand(
            context=context,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
        )
    )
    apply_outcome(response, outcome)
    return ServiceAccountTokensResponse.from_view(outcome.view)


@router.post(
    "/service-accounts/{service_account_id}/rotate",
    response_model=ServiceAccountCreatedResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate credentials for a service account",
    responses=ERROR_RESPONSES,
)
async def rotate_service_account(
    service_account_id: UUID,
    response: Response,
    admin: Annotated[Principal, Depends(require_tenant_admin)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> ServiceAccountCreatedResponse:
    assert context.tenant_id is not None
    outcome = await use_cases.rotate_service_account(
        RotateServiceAccountSecretCommand(
            tenant_id=context.tenant_id,
            context=context,
            service_account_id=ServiceAccountId(service_account_id),
        )
    )
    apply_outcome(response, outcome)
    return ServiceAccountCreatedResponse.from_view(outcome.view)


@router.delete(
    "/service-accounts/{service_account_id}",
    response_model=ServiceAccountResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke/disable a service account",
    responses=ERROR_RESPONSES,
)
async def revoke_service_account(
    service_account_id: UUID,
    response: Response,
    admin: Annotated[Principal, Depends(require_tenant_admin)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> ServiceAccountResponse:
    assert context.tenant_id is not None
    outcome = await use_cases.revoke_service_account(
        RevokeServiceAccountCommand(
            tenant_id=context.tenant_id,
            context=context,
            service_account_id=ServiceAccountId(service_account_id),
        )
    )
    apply_outcome(response, outcome)
    return ServiceAccountResponse.from_view(outcome.view)


__all__ = ["router"]
