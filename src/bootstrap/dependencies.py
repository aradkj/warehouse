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
from identity.application.use_cases import GetTenant, RegisterTenant
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
from shared.application.errors import InvalidRequest
from shared.application.events import EventDispatcher, NullEventDispatcher
from shared.application.use_case import Outcome
from shared.domain.clock import Clock, SystemClock
from shared.domain.identifiers import TenantId
from shared.infrastructure.db.session import Database, SqlAlchemyTransactionScope
from shared.ports.cache import CachePort

# ---------------------------------------------------------------------------
# process-wide singletons (immutable, therefore safe to share)
# ---------------------------------------------------------------------------
_clock: Clock = SystemClock()


def get_application_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_clock() -> Clock:
    return _clock


def get_event_dispatcher(request: Request) -> EventDispatcher:
    dispatcher = getattr(request.app.state, "event_dispatcher", None)
    if dispatcher is None:
        return NullEventDispatcher()
    return dispatcher


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


# ---------------------------------------------------------------------------
# request-scoped
# ---------------------------------------------------------------------------
def get_transaction_scope(
    database: Annotated[Database, Depends(get_database)],
) -> SqlAlchemyTransactionScope:
    """One transaction scope per request.

    Every use case invoked while handling the request participates in the same
    transaction; commit happens inside the use case that owns the operation.
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


def get_tenant_context(
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> RequestContext:
    """Context for tenant-scoped endpoints: the tenant header is mandatory."""
    if context.tenant_id is None:
        raise InvalidRequest(
            "the X-Tenant-Id header is required for this operation",
            details={"header": "X-Tenant-Id"},
        )
    return context


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
    # identity
    register_tenant: RegisterTenant
    get_tenant: GetTenant


def build_use_cases(
    *,
    settings: Settings,
    clock: Clock,
    scope: SqlAlchemyTransactionScope,
    events: EventDispatcher | None = None,
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

    common: dict[str, Any] = {
        "clock": clock,
        "events": dispatcher,
        "idempotency_retention": retention,
    }

    return UseCases(
        create_product=CreateProduct(uow_factory=catalog_uow, **common),
        revise_product=ReviseProduct(uow_factory=catalog_uow, **common),
        activate_product=ActivateProduct(uow_factory=catalog_uow, **common),
        archive_product=ArchiveProduct(uow_factory=catalog_uow, **common),
        create_sku=CreateSku(uow_factory=catalog_uow, **common),
        change_sku_price=ChangeSkuPrice(uow_factory=catalog_uow, **common),
        deactivate_sku=DeactivateSku(uow_factory=catalog_uow, **common),
        activate_sku=ActivateSku(uow_factory=catalog_uow, **common),
        get_product=GetProduct(uow_factory=catalog_uow, clock=clock),
        get_sku=GetSku(uow_factory=catalog_uow, clock=clock),
        list_products=ListProducts(uow_factory=catalog_uow, clock=clock),
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
        get_availability=GetAvailability(uow_factory=inventory_uow, clock=clock),
        get_reservation=GetReservation(uow_factory=inventory_uow, clock=clock),
        get_warehouse=GetWarehouse(uow_factory=inventory_uow, clock=clock),
        list_warehouses=ListWarehouses(uow_factory=inventory_uow, clock=clock),
        create_order=CreateOrder(
            uow_factory=ordering_uow,
            unavailable_policy=UnavailableStockPolicy(settings.unavailable_stock_policy),
            **common,
        ),
        confirm_order=ConfirmOrder(uow_factory=ordering_uow, **common),
        cancel_order=CancelOrder(uow_factory=ordering_uow, **common),
        get_order=GetOrder(uow_factory=ordering_uow, clock=clock),
        list_orders=ListOrders(uow_factory=ordering_uow, clock=clock),
        register_tenant=RegisterTenant(uow_factory=identity_uow, **common),
        get_tenant=GetTenant(uow_factory=identity_uow, clock=clock),
    )


def get_use_cases(
    settings: Annotated[Settings, Depends(get_application_settings)],
    scope: Annotated[SqlAlchemyTransactionScope, Depends(get_transaction_scope)],
    clock: Annotated[Clock, Depends(get_clock)],
    events: Annotated[EventDispatcher, Depends(get_event_dispatcher)],
) -> UseCases:
    return build_use_cases(settings=settings, clock=clock, scope=scope, events=events)


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


def get_admin_context(
    context: Annotated[RequestContext, Depends(get_request_context)],
) -> RequestContext:
    """Context for platform-wide administrative endpoints."""
    if context.actor_type not in (ActorType.SYSTEM, ActorType.SERVICE_ACCOUNT):
        raise InvalidRequest(
            "administrative operations require system or service_account privileges",
            details={"actor_type": str(context.actor_type)},
        )
    return context


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


__all__ = [
    "UseCases",
    "apply_outcome",
    "build_use_cases",
    "get_admin_context",
    "get_application_settings",
    "get_cache",
    "get_catalog_search_reader",
    "get_clock",
    "get_database",
    "get_event_dispatcher",
    "get_reconciliation_service",
    "get_reindexing_service",
    "get_request_context",
    "get_search_indexer",
    "get_search_service",
    "get_tenant_context",
    "get_transaction_scope",
    "get_use_cases",
    "parse_if_match",
]
