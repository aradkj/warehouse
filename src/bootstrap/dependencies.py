"""Composition root: wiring, and the request-scoped context.

This is the only place in the codebase that knows about *all* the pieces. Every
dependency is constructed per request (or per process for immutable objects), so
there is no global mutable state and no service locator hiding in the domain.

The tenant context is resolved here from the request headers. This is explicitly
**not** authentication - Phase 4 replaces it with verified identities - but it is
the seam every later phase builds on, and it is the reason no use case ever has
to read an HTTP header itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from starlette.responses import Response

from bootstrap.config import Settings
from catalog.application.queries import GetProduct, GetSku, ListProducts
from catalog.application.use_cases import (
    ActivateProduct,
    ActivateSku,
    ArchiveProduct,
    ChangeSkuPrice,
    CreateProduct,
    CreateSku,
    DeactivateSku,
    ReviseProduct,
)
from fulfillment.application.use_cases import (
    CompensateFulfillment,
    GetFulfillment,
    RequestFulfillment,
    RetryFulfillment,
)
from identity.application.authorization import ApplicationAuthorizationService
from identity.application.use_cases import (
    AssignRole,
    AuthenticateServiceAccount,
    AuthenticateUser,
    ChangePassword,
    CreateInvitation,
    CreatePolicy,
    CreateRole,
    CreateServiceAccount,
    GetPolicy,
    GetRole,
    GetTenant,
    ListMemberships,
    ListPolicies,
    ListRoles,
    ProvisionMembership,
    RefreshToken,
    RegisterTenant,
    RegisterUser,
    RevokeRole,
    RevokeServiceAccount,
    RevokeSession,
    RotateServiceAccountSecret,
    SelectTenant,
    UpdatePolicyStatus,
)
from identity.domain.decision import AuthorizationRequest
from identity.domain.membership import MembershipRole, MembershipStatus
from identity.domain.principal import Principal
from identity.domain.service_account import ServiceAccountStatus
from identity.domain.tenant import TenantStatus
from identity.domain.user import UserStatus
from identity.infrastructure.security import PasswordHasher, TokenService
from inventory.application.queries import (
    GetAvailability,
    GetReservation,
    GetWarehouse,
    ListWarehouses,
)
from inventory.application.use_cases import (
    AdjustInventory,
    ConfirmReservation,
    ExpireReservations,
    RegisterWarehouse,
    ReleaseReservation,
    ReserveInventory,
)
from ordering.application.queries import GetOrder, ListOrders
from ordering.application.use_cases import CancelOrder, ConfirmOrder, CreateOrder
from ordering.domain.policies import UnavailableStockPolicy
from search.ports.indexer import SearchIndexer
from shared.application.context import ActorType, RequestContext
from shared.application.errors import (
    AccountDisabled,
    AuthenticationFailed,
    Forbidden,
    InvalidRequest,
    MembershipDisabled,
    TenantSuspended,
)
from shared.application.events import EventDispatcher
from shared.application.use_case import Outcome
from shared.domain.clock import Clock, SystemClock
from shared.domain.identifiers import ServiceAccountId, TenantId, UserId
from shared.infrastructure.db.session import Database, SqlAlchemyTransactionScope
from shared.ports.cache import CachePort

# ---------------------------------------------------------------------------
# process-wide singletons (immutable, therefore safe to share)
# ---------------------------------------------------------------------------
_clock: Clock = SystemClock()
_tenant_membership_cache: dict[tuple[str, TenantId], tuple[float, bool]] = {}


def invalidate_tenant_membership_cache(
    user_id: UserId | str | None = None,
    tenant_id: TenantId | None = None,
) -> None:
    """Clear cached tenant membership validation results.

    Membership checks are performed directly against PostgreSQL on every request
    to ensure immediate cross-worker revocation. Retained for backwards compatibility.
    """
    global _tenant_membership_cache
    if user_id is None and tenant_id is None:
        _tenant_membership_cache.clear()
        return
    uid_str = str(user_id) if user_id is not None else None
    keys_to_remove = [
        k
        for k in _tenant_membership_cache
        if (uid_str is None or k[0] == uid_str) and (tenant_id is None or k[1] == tenant_id)
    ]
    for k in keys_to_remove:
        _tenant_membership_cache.pop(k, None)


def get_application_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_clock() -> Clock:
    return _clock


def get_event_dispatcher(request: Request) -> EventDispatcher | None:
    return getattr(request.app.state, "event_dispatcher", None)


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_cache(request: Request) -> CachePort:
    cache = getattr(request.app.state, "cache", None)
    if cache is None:
        from shared.infrastructure.cache.memory import InMemoryCache

        return InMemoryCache()
    return cache


def get_search_indexer(request: Request) -> SearchIndexer:
    indexer = getattr(request.app.state, "search_indexer", None)
    if indexer is None:
        from search.adapters.memory import InMemorySearchIndexer

        return InMemorySearchIndexer()
    return indexer


def get_password_hasher(
    settings: Annotated[Settings, Depends(get_application_settings)] | None = None,
) -> PasswordHasher:
    is_prod = settings.is_production if settings is not None else False
    return PasswordHasher(allow_fallback=not is_prod)


def get_token_service(
    settings: Annotated[Settings, Depends(get_application_settings)],
) -> TokenService:
    return TokenService(
        secret=settings.auth_jwt_secret,
        algorithm=settings.auth_jwt_algorithm,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        access_token_ttl_seconds=settings.auth_access_token_ttl_seconds,
    )


# ---------------------------------------------------------------------------
# request-scoped
# ---------------------------------------------------------------------------
def get_transaction_scope(
    database: Annotated[Database, Depends(get_database)],
) -> SqlAlchemyTransactionScope:
    """One transaction scope per request.

    Starts without an unverified tenant context. Trusted tenant context is bound
    only after authentication and tenant membership validation.
    """
    return database.new_scope()


def get_request_context(
    request: Request,
    tenant_header: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    actor_header: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    actor_type_header: Annotated[str | None, Header(alias="X-Actor-Type")] = None,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RequestContext:
    """Build the immutable per-request context.

    Platform-scoped endpoints (tenant provisioning) call this too and simply get
    a context without a tenant.
    """
    tenant_id: TenantId | None = None
    if tenant_header:
        try:
            tenant_id = TenantId(uuid.UUID(tenant_header))
        except ValueError as exc:
            raise InvalidRequest(
                "the tenant header is not a valid identifier",
                details={"header": "X-Tenant-Id"},
            ) from exc

    actor_type = ActorType.SALES_CHANNEL
    if actor_type_header:
        try:
            actor_type = ActorType(actor_type_header.strip().lower())
        except ValueError as exc:
            allowed = [member.value for member in ActorType]
            raise InvalidRequest(
                "the actor type header is not recognised", details={"allowed": allowed}
            ) from exc

    correlation_id = (
        request.headers.get("X-Correlation-Id")
        or getattr(request.state, "correlation_id", None)
        or str(uuid.uuid4())
    )
    key = idempotency_header.strip() if idempotency_header else None
    if key is not None and not 1 <= len(key) <= 255:
        raise InvalidRequest("the idempotency key must be 1-255 characters")

    return RequestContext(
        tenant_id=tenant_id,
        actor_id=(actor_header or "unknown").strip(),
        actor_type=actor_type,
        correlation_id=correlation_id,
        idempotency_key=key,
        received_at=datetime.now(UTC),
    )


# get_tenant_context is defined below, delegating to get_authenticated_tenant_context


async def get_current_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_application_settings)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    database: Annotated[Database, Depends(get_database)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Principal | None:
    """Resolve and validate the current authenticated principal from Bearer token."""
    if authorization is None:
        if settings.auth_required:
            raise AuthenticationFailed("missing authorization header")
        return None

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationFailed("invalid authorization header format; expected 'Bearer <token>'")

    raw_token = parts[1]
    claims = token_service.verify_access_token(raw_token)

    sub = claims.get("sub")
    if not sub:
        raise AuthenticationFailed("missing subject claim in token")

    token_type = claims.get("type", "access")

    if token_type == "system":
        return Principal(
            id=str(sub),
            type=ActorType.SYSTEM,
            email=None,
            active_tenant_id=None,
            active_membership_id=None,
            account_status="ACTIVE",
            role="SYSTEM",
            roles=("SYSTEM", "ADMIN"),
            permissions=frozenset({"*"}),
        )

    raw_tid = claims.get("tenant_id")
    raw_uid = str(sub) if token_type != "service_account" else None
    raw_cid = claims.get("client_id") if token_type == "service_account" else None
    scope = database.new_scope(tenant_id=raw_tid, user_id=raw_uid, client_id=raw_cid)
    from identity.adapters.persistence.unit_of_work import SqlAlchemyIdentityUnitOfWork

    async with SqlAlchemyIdentityUnitOfWork(scope) as uow:
        if token_type == "service_account":
            try:
                sa_id = ServiceAccountId(uuid.UUID(sub))
            except ValueError as exc:
                raise AuthenticationFailed("malformed subject in token") from exc

            sa = await uow.service_accounts.get(sa_id)
            if sa is None or sa.status != ServiceAccountStatus.ACTIVE:
                raise AccountDisabled("service account is disabled or does not exist")

            token_version = claims.get("token_version")
            if token_version is None or token_version != sa.version:
                raise AuthenticationFailed(
                    "service account token has been invalidated by secret rotation"
                )

            tenant = await uow.tenants.get(sa.tenant_id)
            if tenant is None or tenant.status != TenantStatus.ACTIVE:
                raise TenantSuspended("tenant is suspended or does not exist")

            return Principal(
                id=str(sa.id),
                type=ActorType.SERVICE_ACCOUNT,
                email=None,
                active_tenant_id=sa.tenant_id,
                active_membership_id=None,
                account_status=sa.status.value,
                role="SERVICE_ACCOUNT",
                roles=(),
                permissions=sa.scopes,
            )
        else:
            try:
                user_id = UserId(uuid.UUID(sub))
            except ValueError as exc:
                raise AuthenticationFailed("malformed subject in token") from exc

            user = await uow.users.get(user_id)
            if user is None:
                raise AuthenticationFailed("user does not exist")
            if user.status != UserStatus.ACTIVE:
                raise AccountDisabled(f"user account is {user.status.value.lower()}")

            active_tenant_id = None
            active_membership_id = None
            membership = None

            raw_tenant_id = claims.get("tenant_id")
            if raw_tenant_id:
                try:
                    active_tenant_id = TenantId(uuid.UUID(raw_tenant_id))
                except ValueError as exc:
                    raise AuthenticationFailed("malformed tenant_id in token") from exc

                tenant = await uow.tenants.get(active_tenant_id)
                if tenant is None or tenant.status != TenantStatus.ACTIVE:
                    raise TenantSuspended("tenant is suspended or does not exist")

                membership = await uow.memberships.get_by_user_and_tenant(user.id, active_tenant_id)
                if membership is None:
                    raise Forbidden("user is not a member of the active tenant")
                if membership.status != MembershipStatus.ACTIVE:
                    raise MembershipDisabled("tenant membership is disabled")

                active_membership_id = membership.id

            effective_roles: set[str] = set()
            perms: set[str] = set()
            from identity.domain.role import SYSTEM_ROLES_PERMISSIONS

            if membership is not None:
                if membership.roles:
                    effective_roles.update(membership.roles)
                else:
                    effective_roles.add(membership.role.value)
                for r_name in effective_roles:
                    if r_name in SYSTEM_ROLES_PERMISSIONS:
                        perms.update(SYSTEM_ROLES_PERMISSIONS[r_name])
                    r_obj = await uow.roles.get_by_name(r_name, active_tenant_id)
                    if r_obj is not None:
                        perms.update(r_obj.permissions)

            return Principal(
                id=str(user.id),
                type=ActorType.USER,
                email=user.email,
                active_tenant_id=active_tenant_id,
                active_membership_id=active_membership_id,
                account_status=user.status.value,
                role=membership.role.value if membership is not None else "MEMBER",
                roles=tuple(sorted(effective_roles)),
                permissions=frozenset(perms),
            )


async def require_current_principal(
    principal: Annotated[Principal | None, Depends(get_current_principal)],
) -> Principal:
    """Strictly require an authenticated principal."""
    if principal is None:
        raise AuthenticationFailed("authentication required")
    return principal


async def get_optional_current_principal(
    request: Request,
    settings: Annotated[Settings, Depends(get_application_settings)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    database: Annotated[Database, Depends(get_database)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> Principal | None:
    """Resolve authenticated principal if Bearer token is provided, or None if absent/invalid."""
    if authorization is None:
        return None
    try:
        return await get_current_principal(
            request=request,
            settings=settings,
            token_service=token_service,
            database=database,
            authorization=authorization,
        )
    except Exception:
        return None


async def get_authenticated_tenant_context(
    request: Request,
    settings: Annotated[Settings, Depends(get_application_settings)],
    principal: Annotated[Principal | None, Depends(get_current_principal)],
    database: Annotated[Database, Depends(get_database)],
    scope: Annotated[SqlAlchemyTransactionScope | None, Depends(get_transaction_scope)] = None,
    tenant_header: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    actor_header: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
    actor_type_header: Annotated[str | None, Header(alias="X-Actor-Type")] = None,
    idempotency_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> RequestContext:
    """Resolve trusted tenant context for protected tenant-scoped operations."""
    if principal is not None:
        selected_tenant_id: TenantId | None = None
        if tenant_header:
            try:
                selected_tenant_id = TenantId(uuid.UUID(tenant_header))
            except ValueError as exc:
                raise InvalidRequest(
                    "the tenant header is not a valid identifier",
                    details={"header": "X-Tenant-Id"},
                ) from exc
        else:
            selected_tenant_id = principal.active_tenant_id

        if selected_tenant_id is None:
            raise InvalidRequest(
                "the X-Tenant-Id header is required or a tenant must be selected",
                details={"header": "X-Tenant-Id"},
            )

        if principal.is_user:
            async with database.new_scope(
                tenant_id=str(selected_tenant_id),
                user_id=principal.id,
            ) as verify_scope:
                from identity.adapters.persistence.unit_of_work import (
                    SqlAlchemyIdentityUnitOfWork,
                )

                async with SqlAlchemyIdentityUnitOfWork(verify_scope) as uow:
                    tenant = await uow.tenants.get(selected_tenant_id)
                    if tenant is None or tenant.status != TenantStatus.ACTIVE:
                        raise TenantSuspended("tenant is suspended or does not exist")

                    membership = await uow.memberships.get_by_user_and_tenant(
                        UserId(uuid.UUID(principal.id)), selected_tenant_id
                    )
                    if membership is None:
                        raise Forbidden("user is not a member of the requested tenant")
                    if membership.status != MembershipStatus.ACTIVE:
                        raise MembershipDisabled("tenant membership is disabled")
        elif principal.is_service_account and principal.active_tenant_id != selected_tenant_id:
            raise Forbidden("service account cannot access a different tenant")

        correlation_id = (
            request.headers.get("X-Correlation-Id")
            or getattr(request.state, "correlation_id", None)
            or str(uuid.uuid4())
        )
        key = idempotency_header.strip() if idempotency_header else None
        if key is not None and not 1 <= len(key) <= 255:
            raise InvalidRequest("the idempotency key must be 1-255 characters")

        if scope is not None and hasattr(scope, "set_tenant_context"):
            await scope.set_tenant_context(
                selected_tenant_id,
                user_id=principal.id,
            )

        return RequestContext(
            tenant_id=selected_tenant_id,
            actor_id=principal.id,
            actor_type=principal.type,
            correlation_id=correlation_id,
            idempotency_key=key,
            received_at=datetime.now(UTC),
            principal=principal,
        )

    dev_environments = {"local", "test", "dev", "development"}
    if (
        settings.auth_required
        or settings.is_production
        or settings.environment.lower() not in dev_environments
    ):
        raise AuthenticationFailed("authentication required")

    legacy_context = get_request_context(
        request=request,
        tenant_header=tenant_header,
        actor_header=actor_header,
        actor_type_header=actor_type_header,
        idempotency_header=idempotency_header,
    )
    if legacy_context.tenant_id is None:
        raise InvalidRequest(
            "the X-Tenant-Id header is required for this operation",
            details={"header": "X-Tenant-Id"},
        )
    if (
        scope is not None
        and hasattr(scope, "set_tenant_context")
        and legacy_context.tenant_id is not None
    ):
        await scope.set_tenant_context(legacy_context.tenant_id)
    legacy_principal = Principal(
        id=str(legacy_context.actor_id),
        type=legacy_context.actor_type,
        email=None,
        active_tenant_id=legacy_context.tenant_id,
        active_membership_id=None,
        account_status="ACTIVE",
        role="ADMIN",
        roles=("ADMIN",),
        permissions=frozenset({"*"}),
    )
    import dataclasses

    return dataclasses.replace(legacy_context, principal=legacy_principal)


async def get_tenant_context(
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
) -> RequestContext:
    """Context for tenant-scoped endpoints: requires validated tenant membership."""
    return context


async def require_tenant_admin(
    principal: Annotated[Principal, Depends(require_current_principal)],
    context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
    database: Annotated[Database, Depends(get_database)],
) -> Principal:
    """Enforce that caller is a human administrator of the targeted tenant."""
    if not principal.is_user:
        raise Forbidden("service accounts cannot administer tenant resources")

    assert context.tenant_id is not None
    if principal.is_tenant_admin or "ADMIN" in principal.roles or principal.role == "ADMIN":
        return principal

    async with database.new_scope() as scope:
        from identity.adapters.persistence.unit_of_work import SqlAlchemyIdentityUnitOfWork

        async with SqlAlchemyIdentityUnitOfWork(scope) as uow:
            membership = await uow.memberships.get_by_user_and_tenant(
                UserId(uuid.UUID(principal.id)), context.tenant_id
            )
            if membership is None or not (
                membership.role == MembershipRole.ADMIN
                or "ADMIN" in membership.roles
                or membership.has_role("ADMIN")
            ):
                raise Forbidden("operation requires tenant administrator privileges")
            if membership.status != MembershipStatus.ACTIVE:
                raise MembershipDisabled("tenant membership is disabled")
    return principal


def get_authorization_service(
    database: Annotated[Database, Depends(get_database)],
    scope: Annotated[SqlAlchemyTransactionScope, Depends(get_transaction_scope)],
) -> ApplicationAuthorizationService:
    from audit.adapters.persistence.repositories import SqlAlchemyAuditAdapter
    from identity.adapters.persistence.unit_of_work import SqlAlchemyIdentityUnitOfWork

    def _uow_factory(tid: TenantId | None = None) -> SqlAlchemyIdentityUnitOfWork:
        active_tid = str(tid) if tid is not None else getattr(scope, "_tenant_id", None)
        return SqlAlchemyIdentityUnitOfWork(database.new_scope(tenant_id=active_tid))

    return ApplicationAuthorizationService(
        uow_factory=_uow_factory,
        audit=SqlAlchemyAuditAdapter(scope),
    )


def require_permission(
    permission: str,
    resource_type: str | None = None,
    action: str | None = None,
) -> Any:
    """Enforce that caller holds the required permission or policy allows it."""

    async def _dependency(
        principal: Annotated[Principal | None, Depends(get_current_principal)],
        context: Annotated[RequestContext, Depends(get_authenticated_tenant_context)],
        auth_service: Annotated[
            ApplicationAuthorizationService, Depends(get_authorization_service)
        ],
        settings: Annotated[Settings, Depends(get_application_settings)],
    ) -> Principal:
        effective_principal = principal
        if effective_principal is None:
            dev_environments = {"local", "test", "dev", "development"}
            if (
                settings.auth_required
                or settings.is_production
                or settings.environment.lower() not in dev_environments
            ):
                raise AuthenticationFailed("authentication required")
            # Legacy non-authenticated request in test / local setup
            return Principal(
                id=str(context.actor_id),
                type=context.actor_type,
                email=None,
                active_tenant_id=context.tenant_id,
                active_membership_id=None,
                account_status="ACTIVE",
                role="ADMIN",
                roles=("ADMIN",),
                permissions=frozenset({"*"}),
            )

        req = AuthorizationRequest(
            principal_id=effective_principal.id,
            principal_type=effective_principal.type,
            tenant_id=effective_principal.active_tenant_id,
            roles=effective_principal.roles,
            permissions=effective_principal.permissions,
            action=action or permission,
            resource_type=resource_type or permission.split(".")[0],
            resource_id=None,
            resource_tenant_id=context.tenant_id,
        )
        decision = await auth_service.authorize(req)
        if not decision.allowed:
            raise Forbidden(f"operation requires permission '{permission}': {decision.reason}")
        return effective_principal

    return _dependency


# ---------------------------------------------------------------------------
# use cases
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class UseCases:
    """All Phase 1 use cases, wired to the request's transaction scope."""

    # catalog
    create_product: CreateProduct
    revise_product: ReviseProduct
    activate_product: ActivateProduct
    archive_product: ArchiveProduct
    create_sku: CreateSku
    change_sku_price: ChangeSkuPrice
    deactivate_sku: DeactivateSku
    activate_sku: ActivateSku
    get_product: GetProduct
    get_sku: GetSku
    list_products: ListProducts
    # inventory
    register_warehouse: RegisterWarehouse
    adjust_inventory: AdjustInventory
    reserve_inventory: ReserveInventory
    release_reservation: ReleaseReservation
    confirm_reservation: ConfirmReservation
    expire_reservations: ExpireReservations
    get_availability: GetAvailability
    get_reservation: GetReservation
    get_warehouse: GetWarehouse
    list_warehouses: ListWarehouses
    # ordering
    create_order: CreateOrder
    confirm_order: ConfirmOrder
    cancel_order: CancelOrder
    get_order: GetOrder
    list_orders: ListOrders
    # identity (Phase 1)
    register_tenant: RegisterTenant
    get_tenant: GetTenant
    # identity (Phase 4.1)
    register_user: RegisterUser
    authenticate_user: AuthenticateUser
    refresh_token: RefreshToken
    revoke_session: RevokeSession
    list_memberships: ListMemberships
    select_tenant: SelectTenant
    change_password: ChangePassword
    create_invitation: CreateInvitation
    provision_membership: ProvisionMembership
    create_service_account: CreateServiceAccount
    authenticate_service_account: AuthenticateServiceAccount
    rotate_service_account: RotateServiceAccountSecret
    revoke_service_account: RevokeServiceAccount
    # access (Phase 4.2)
    create_role: CreateRole
    get_role: GetRole
    list_roles: ListRoles
    assign_role: AssignRole
    revoke_role: RevokeRole
    create_policy: CreatePolicy
    get_policy: GetPolicy
    list_policies: ListPolicies
    update_policy_status: UpdatePolicyStatus
    # fulfillment (Phase 2)
    request_fulfillment: RequestFulfillment | None = None
    retry_fulfillment: RetryFulfillment | None = None
    compensate_fulfillment: CompensateFulfillment | None = None
    get_fulfillment: GetFulfillment | None = None


def build_use_cases(
    *,
    settings: Settings,
    clock: Clock,
    scope: SqlAlchemyTransactionScope,
    events: EventDispatcher | None = None,
    authorizer: Any | None = None,
) -> UseCases:
    """Wire every use case onto one transaction scope.

    Imported by the HTTP dependencies and by the test fixtures, so tests exercise
    exactly the object graph production uses.
    """
    from catalog.adapters.persistence.unit_of_work import SqlAlchemyCatalogUnitOfWork
    from identity.adapters.persistence.unit_of_work import SqlAlchemyIdentityUnitOfWork
    from inventory.adapters.persistence.unit_of_work import SqlAlchemyInventoryUnitOfWork
    from ordering.adapters.persistence.unit_of_work import SqlAlchemyOrderingUnitOfWork
    from shared.infrastructure.db.outbox import SqlAlchemyOutboxEventDispatcher

    retention = timedelta(seconds=settings.idempotency_retention_seconds)
    dispatcher = events if events is not None else SqlAlchemyOutboxEventDispatcher(scope)

    catalog_uow = lambda: SqlAlchemyCatalogUnitOfWork(scope)  # noqa: E731
    identity_uow = lambda: SqlAlchemyIdentityUnitOfWork(scope)  # noqa: E731
    inventory_uow = lambda: SqlAlchemyInventoryUnitOfWork(scope)  # noqa: E731
    ordering_uow = lambda: SqlAlchemyOrderingUnitOfWork(  # noqa: E731
        scope,
        clock=clock,
        default_reservation_ttl_seconds=settings.reservation_ttl_seconds,
        max_reservation_ttl_seconds=settings.max_reservation_ttl_seconds,
    )
    from fulfillment.adapters.unit_of_work import SqlAlchemyFulfillmentUnitOfWork

    fulfillment_uow = lambda: SqlAlchemyFulfillmentUnitOfWork(scope)  # noqa: E731

    if authorizer is None and settings.auth_required:
        from audit.adapters.persistence.repositories import SqlAlchemyAuditAdapter
        from identity.application.authorization import ApplicationAuthorizationService

        authorizer = ApplicationAuthorizationService(
            uow_factory=identity_uow,
            audit=SqlAlchemyAuditAdapter(scope),
        )

    common: dict[str, Any] = {
        "clock": clock,
        "events": dispatcher,
        "idempotency_retention": retention,
        "authorizer": authorizer,
    }

    hasher = get_password_hasher(settings)
    token_service = TokenService(
        secret=settings.auth_jwt_secret,
        algorithm=settings.auth_jwt_algorithm,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        access_token_ttl_seconds=settings.auth_access_token_ttl_seconds,
    )
    refresh_ttl = timedelta(seconds=settings.auth_refresh_token_ttl_seconds)

    return UseCases(
        create_product=CreateProduct(uow_factory=catalog_uow, **common),
        revise_product=ReviseProduct(uow_factory=catalog_uow, **common),
        activate_product=ActivateProduct(uow_factory=catalog_uow, **common),
        archive_product=ArchiveProduct(uow_factory=catalog_uow, **common),
        create_sku=CreateSku(uow_factory=catalog_uow, **common),
        change_sku_price=ChangeSkuPrice(uow_factory=catalog_uow, **common),
        deactivate_sku=DeactivateSku(uow_factory=catalog_uow, **common),
        activate_sku=ActivateSku(uow_factory=catalog_uow, **common),
        get_product=GetProduct(uow_factory=catalog_uow, clock=clock, authorizer=authorizer),
        get_sku=GetSku(uow_factory=catalog_uow, clock=clock, authorizer=authorizer),
        list_products=ListProducts(uow_factory=catalog_uow, clock=clock, authorizer=authorizer),
        register_warehouse=RegisterWarehouse(uow_factory=inventory_uow, **common),
        adjust_inventory=AdjustInventory(uow_factory=inventory_uow, **common),
        reserve_inventory=ReserveInventory(
            uow_factory=inventory_uow,
            default_ttl_seconds=settings.reservation_ttl_seconds,
            max_ttl_seconds=settings.max_reservation_ttl_seconds,
            **common,
        ),
        release_reservation=ReleaseReservation(uow_factory=inventory_uow, **common),
        confirm_reservation=ConfirmReservation(uow_factory=inventory_uow, **common),
        expire_reservations=ExpireReservations(uow_factory=inventory_uow, **common),
        get_availability=GetAvailability(
            uow_factory=inventory_uow, clock=clock, authorizer=authorizer
        ),
        get_reservation=GetReservation(
            uow_factory=inventory_uow, clock=clock, authorizer=authorizer
        ),
        get_warehouse=GetWarehouse(uow_factory=inventory_uow, clock=clock, authorizer=authorizer),
        list_warehouses=ListWarehouses(
            uow_factory=inventory_uow, clock=clock, authorizer=authorizer
        ),
        create_order=CreateOrder(
            uow_factory=ordering_uow,
            unavailable_policy=UnavailableStockPolicy(settings.unavailable_stock_policy),
            **common,
        ),
        confirm_order=ConfirmOrder(uow_factory=ordering_uow, **common),
        cancel_order=CancelOrder(uow_factory=ordering_uow, **common),
        get_order=GetOrder(uow_factory=ordering_uow, clock=clock, authorizer=authorizer),
        list_orders=ListOrders(uow_factory=ordering_uow, clock=clock, authorizer=authorizer),
        register_tenant=RegisterTenant(uow_factory=identity_uow, **common),
        get_tenant=GetTenant(uow_factory=identity_uow, clock=clock, authorizer=authorizer),
        register_user=RegisterUser(
            uow_factory=identity_uow,
            clock=clock,
            hasher=hasher,
            token_service=token_service,
            events=dispatcher,
        ),
        authenticate_user=AuthenticateUser(
            uow_factory=identity_uow,
            clock=clock,
            hasher=hasher,
            token_service=token_service,
            refresh_ttl=refresh_ttl,
            events=dispatcher,
        ),
        refresh_token=RefreshToken(
            uow_factory=identity_uow,
            clock=clock,
            token_service=token_service,
            refresh_ttl=refresh_ttl,
            events=dispatcher,
        ),
        revoke_session=RevokeSession(uow_factory=identity_uow, clock=clock, events=dispatcher),
        list_memberships=ListMemberships(uow_factory=identity_uow, clock=clock),
        select_tenant=SelectTenant(
            uow_factory=identity_uow,
            clock=clock,
            token_service=token_service,
            refresh_ttl=refresh_ttl,
            events=dispatcher,
        ),
        change_password=ChangePassword(
            uow_factory=identity_uow, clock=clock, hasher=hasher, events=dispatcher
        ),
        create_invitation=CreateInvitation(
            uow_factory=identity_uow,
            clock=clock,
            token_service=token_service,
            events=dispatcher,
            authorizer=authorizer,
        ),
        provision_membership=ProvisionMembership(
            uow_factory=identity_uow, clock=clock, events=dispatcher, authorizer=authorizer
        ),
        create_service_account=CreateServiceAccount(
            uow_factory=identity_uow,
            clock=clock,
            hasher=hasher,
            events=dispatcher,
            authorizer=authorizer,
        ),
        authenticate_service_account=AuthenticateServiceAccount(
            uow_factory=identity_uow,
            clock=clock,
            hasher=hasher,
            token_service=token_service,
            events=dispatcher,
        ),
        rotate_service_account=RotateServiceAccountSecret(
            uow_factory=identity_uow,
            clock=clock,
            hasher=hasher,
            events=dispatcher,
            authorizer=authorizer,
        ),
        revoke_service_account=RevokeServiceAccount(
            uow_factory=identity_uow, clock=clock, events=dispatcher, authorizer=authorizer
        ),
        create_role=CreateRole(
            uow_factory=identity_uow, clock=clock, events=dispatcher, authorizer=authorizer
        ),
        get_role=GetRole(uow_factory=identity_uow, clock=clock, authorizer=authorizer),
        list_roles=ListRoles(uow_factory=identity_uow, clock=clock, authorizer=authorizer),
        assign_role=AssignRole(
            uow_factory=identity_uow, clock=clock, events=dispatcher, authorizer=authorizer
        ),
        revoke_role=RevokeRole(
            uow_factory=identity_uow, clock=clock, events=dispatcher, authorizer=authorizer
        ),
        create_policy=CreatePolicy(
            uow_factory=identity_uow, clock=clock, events=dispatcher, authorizer=authorizer
        ),
        get_policy=GetPolicy(uow_factory=identity_uow, clock=clock, authorizer=authorizer),
        list_policies=ListPolicies(uow_factory=identity_uow, clock=clock, authorizer=authorizer),
        update_policy_status=UpdatePolicyStatus(
            uow_factory=identity_uow, clock=clock, events=dispatcher, authorizer=authorizer
        ),
        request_fulfillment=RequestFulfillment(uow_factory=fulfillment_uow, **common),
        retry_fulfillment=RetryFulfillment(uow_factory=fulfillment_uow, **common),
        compensate_fulfillment=CompensateFulfillment(uow_factory=fulfillment_uow, **common),
        get_fulfillment=GetFulfillment(
            uow_factory=fulfillment_uow, clock=clock, authorizer=authorizer
        ),
    )


def get_use_cases(
    settings: Annotated[Settings, Depends(get_application_settings)],
    scope: Annotated[SqlAlchemyTransactionScope, Depends(get_transaction_scope)],
    clock: Annotated[Clock, Depends(get_clock)],
    events: Annotated[EventDispatcher | None, Depends(get_event_dispatcher)],
    auth_service: Annotated[ApplicationAuthorizationService, Depends(get_authorization_service)],
) -> UseCases:
    return build_use_cases(
        settings=settings, clock=clock, scope=scope, events=events, authorizer=auth_service
    )


# ---------------------------------------------------------------------------
# small HTTP helpers
# ---------------------------------------------------------------------------
def apply_outcome(response: Response, outcome: Outcome[Any]) -> None:
    """Reflect an idempotent outcome's status code and replay flag on the response."""
    response.status_code = outcome.status_code
    if outcome.replayed:
        response.headers["Idempotent-Replay"] = "true"
    if outcome.replayed and outcome.status_code == 200:
        response.headers["Cache-Control"] = "no-store"


def parse_if_match(
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> int | None:
    """Read an optional expected version from ``If-Match``.

    Only two forms are accepted: a bare version number, or a quoted one
    (``"3"``, the HTTP entity-tag form). Anything else is a client error rather
    than a silently ignored header.
    """
    if if_match is None:
        return None
    raw = if_match.strip().strip('"')
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise InvalidRequest(
            "If-Match must contain the expected version, e.g. If-Match: 3",
            details={"if_match": if_match},
        ) from exc


async def get_admin_context(
    request: Request,
    principal: Annotated[Principal, Depends(require_current_principal)],
    scope: Annotated[SqlAlchemyTransactionScope | None, Depends(get_transaction_scope)] = None,
) -> RequestContext:
    """Context for platform-wide administrative endpoints.

    Requires an authenticated principal with administrative privileges.
    Untrusted headers such as X-Actor-Type are never used as an authority source.
    """
    is_admin = (
        principal.type == ActorType.SYSTEM
        or principal.is_tenant_admin
        or "ADMIN" in principal.roles
        or principal.role == "ADMIN"
        or principal.has_permission("*")
        or principal.has_permission("access.roles.manage")
    )
    if not is_admin:
        raise Forbidden("administrative operations require system or administrator privileges")

    if (
        scope is not None
        and principal.active_tenant_id is not None
        and hasattr(scope, "set_tenant_context")
    ):
        await scope.set_tenant_context(principal.active_tenant_id)

    correlation_id = (
        request.headers.get("X-Correlation-Id")
        or getattr(request.state, "correlation_id", None)
        or str(uuid.uuid4())
    )
    idempotency_header = request.headers.get("Idempotency-Key")
    key = idempotency_header.strip() if idempotency_header else None

    return RequestContext(
        tenant_id=principal.active_tenant_id,
        actor_id=principal.id,
        actor_type=principal.type,
        correlation_id=correlation_id,
        idempotency_key=key,
        received_at=datetime.now(UTC),
        principal=principal,
    )


def get_catalog_search_reader(
    database: Annotated[Database, Depends(get_database)],
) -> Any:
    from search.adapters.postgres import SqlAlchemyCatalogSearchReader

    return SqlAlchemyCatalogSearchReader(database)


def get_search_service(
    settings: Annotated[Settings, Depends(get_application_settings)],
    indexer: Annotated[SearchIndexer, Depends(get_search_indexer)],
    cache: Annotated[CachePort, Depends(get_cache)],
) -> Any:
    from search.application.service import SearchProductsService

    return SearchProductsService(
        indexer=indexer,
        cache=cache,
        cache_ttl_seconds=settings.search_cache_ttl_seconds,
        max_page_size=settings.search_max_page_size,
    )


def get_order_search_service(
    settings: Annotated[Settings, Depends(get_application_settings)],
    indexer: Annotated[SearchIndexer, Depends(get_search_indexer)],
    cache: Annotated[CachePort, Depends(get_cache)],
) -> Any:
    from search.application.order_service import SearchOrdersService

    return SearchOrdersService(
        indexer=indexer,
        cache=cache,
        cache_ttl_seconds=settings.search_cache_ttl_seconds,
        max_page_size=settings.search_max_page_size,
    )


def get_audit_search_service(
    settings: Annotated[Settings, Depends(get_application_settings)],
    indexer: Annotated[SearchIndexer, Depends(get_search_indexer)],
    cache: Annotated[CachePort, Depends(get_cache)],
) -> Any:
    from search.application.audit_service import SearchAuditService

    return SearchAuditService(
        indexer=indexer,
        cache=cache,
        cache_ttl_seconds=settings.search_cache_ttl_seconds,
        max_page_size=settings.search_max_page_size,
    )


def get_reindexing_service(
    settings: Annotated[Settings, Depends(get_application_settings)],
    indexer: Annotated[SearchIndexer, Depends(get_search_indexer)],
    reader: Annotated[Any, Depends(get_catalog_search_reader)],
    cache: Annotated[CachePort, Depends(get_cache)],
) -> Any:
    from search.application.reindex import ReindexingService

    return ReindexingService(
        indexer=indexer,
        reader=reader,
        cache=cache,
        batch_size=settings.reindex_batch_size,
    )


def get_reconciliation_service(
    indexer: Annotated[SearchIndexer, Depends(get_search_indexer)],
    reader: Annotated[Any, Depends(get_catalog_search_reader)],
) -> Any:
    from search.application.reconciliation import ReconciliationService

    return ReconciliationService(
        indexer=indexer,
        reader=reader,
    )


def get_audit_port(
    scope: Annotated[SqlAlchemyTransactionScope, Depends(get_transaction_scope)],
) -> Any:
    from audit.adapters.persistence.repositories import SqlAlchemyAuditAdapter

    return SqlAlchemyAuditAdapter(scope)


__all__ = [
    "UseCases",
    "apply_outcome",
    "build_use_cases",
    "get_admin_context",
    "get_application_settings",
    "get_audit_port",
    "get_audit_search_service",
    "get_authenticated_tenant_context",
    "get_authorization_service",
    "get_cache",
    "get_catalog_search_reader",
    "get_clock",
    "get_current_principal",
    "get_optional_current_principal",
    "get_database",
    "get_event_dispatcher",
    "get_order_search_service",
    "get_password_hasher",
    "get_reconciliation_service",
    "get_reindexing_service",
    "get_request_context",
    "get_search_indexer",
    "get_search_service",
    "get_tenant_context",
    "get_token_service",
    "get_transaction_scope",
    "get_use_cases",
    "invalidate_tenant_membership_cache",
    "parse_if_match",
    "require_current_principal",
    "require_permission",
    "require_tenant_admin",
]
