"""Search HTTP router.

Provides tenant-isolated product search, alongside administrative reindexing
and reconciliation endpoints.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from bootstrap.dependencies import (
    get_admin_context,
    get_audit_search_service,
    get_order_search_service,
    get_reconciliation_service,
    get_reindexing_service,
    get_search_service,
    get_tenant_context,
    require_permission,
)
from bootstrap.schemas.common import ErrorResponse
from bootstrap.schemas.search import (
    AuditSearchPageResponse,
    OrderSearchPageResponse,
    ProductSearchPageResponse,
    ReconcileRequest,
    ReconcileResponse,
    ReindexRequest,
    ReindexResponse,
)
from identity.domain.principal import Principal
from search.application.audit_service import SearchAuditService
from search.application.order_service import SearchOrdersService
from search.application.reconciliation import ReconciliationService
from search.application.reindex import ReindexingService
from search.application.service import SearchProductsService
from shared.application.context import RequestContext

router = APIRouter(prefix="/search", tags=["search"])

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


@router.get(
    "/products",
    response_model=ProductSearchPageResponse,
    summary="Search catalog products",
    responses=ERROR_RESPONSES,
)
async def search_products(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    search_service: Annotated[SearchProductsService, Depends(get_search_service)],
    _principal: Annotated[Principal, Depends(require_permission("catalog.products.read"))],
    q: Annotated[
        str | None,
        Query(description="Free-text search query across name, slug, description, and SKU codes."),
    ] = None,
    status_filter: Annotated[
        str | None, Query(alias="status", description="Filter by product status.")
    ] = None,
    sku: Annotated[str | None, Query(description="Filter by specific SKU code.")] = None,
    page: Annotated[int, Query(ge=1, description="Page number (1-based, offset pagination).")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, description="Page size (offset pagination).")
    ] = 20,
    cursor: Annotated[
        str | None,
        Query(
            description="Opaque pagination cursor for deterministic keyset search_after pagination."
        ),
    ] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=100, description="Max items per page when using cursor pagination."),
    ] = None,
    sort_by: Annotated[
        str, Query(description="Sort field: created_at, updated_at, name, status, slug.")
    ] = "created_at",
    sort_order: Annotated[str, Query(description="Sort direction: asc or desc.")] = "desc",
) -> ProductSearchPageResponse:
    """Execute a tenant-safe search query.

    Supports both backward-compatible offset pagination (page/page_size) and
    high-performance, tamper-safe cursor pagination (cursor/limit).
    """
    effective_tenant_id = str(context.require_tenant())
    result = await search_service.search(
        tenant_id=effective_tenant_id,
        q=q,
        status=status_filter,
        sku=sku,
        page=page,
        page_size=page_size,
        cursor=cursor,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return ProductSearchPageResponse.from_page_result(result)


@router.get(
    "/orders",
    response_model=OrderSearchPageResponse,
    summary="Search orders across customer, SKU, status, warehouse, and date",
    responses=ERROR_RESPONSES,
)
async def search_orders(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    search_service: Annotated[SearchOrdersService, Depends(get_order_search_service)],
    _principal: Annotated[Principal, Depends(require_permission("orders.read"))],
    q: Annotated[
        str | None,
        Query(description="Free-text query across customer reference, order ID, or SKU."),
    ] = None,
    status_filter: Annotated[
        str | None,
        Query(alias="status", description="Filter by order status."),
    ] = None,
    sku: Annotated[
        str | None,
        Query(description="Filter by SKU code or identifier."),
    ] = None,
    customer: Annotated[
        str | None,
        Query(description="Filter by customer identifier or reference."),
    ] = None,
    warehouse: Annotated[
        str | None,
        Query(description="Filter by warehouse identifier."),
    ] = None,
    date_from: Annotated[
        str | None,
        Query(description="Filter orders created on or after ISO timestamp."),
    ] = None,
    date_to: Annotated[
        str | None,
        Query(description="Filter orders created on or before ISO timestamp."),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 20,
    cursor: Annotated[
        str | None,
        Query(description="Opaque search_after pagination cursor."),
    ] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=100, description="Page size with cursor pagination."),
    ] = None,
    sort_by: Annotated[
        str,
        Query(description="Sort field: created_at, updated_at, status, total_minor."),
    ] = "created_at",
    sort_order: Annotated[
        str,
        Query(description="Sort direction: asc or desc."),
    ] = "desc",
) -> OrderSearchPageResponse:
    """Execute tenant-safe order search."""
    effective_tenant_id = str(context.require_tenant())
    result = await search_service.search(
        tenant_id=effective_tenant_id,
        q=q,
        status=status_filter,
        sku=sku,
        customer=customer,
        warehouse=warehouse,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
        cursor=cursor,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return OrderSearchPageResponse.from_page_result(result)


@router.get(
    "/audit",
    response_model=AuditSearchPageResponse,
    summary="Search audit records across actor, action, resource, decision, and dates",
    responses=ERROR_RESPONSES,
)
async def search_audit_logs(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    search_service: Annotated[SearchAuditService, Depends(get_audit_search_service)],
    _principal: Annotated[Principal, Depends(require_permission("audit.events.read"))],
    q: Annotated[
        str | None,
        Query(description="Free-text query across action, actor ID, resource, or decision reason."),
    ] = None,
    action: Annotated[
        str | None,
        Query(description="Filter by audit action name (e.g. order.create, product.archive)."),
    ] = None,
    actor_id: Annotated[
        str | None,
        Query(description="Filter by actor ID or user UUID."),
    ] = None,
    resource_type: Annotated[
        str | None,
        Query(description="Filter by resource type (e.g. order, product, inventory)."),
    ] = None,
    resource_id: Annotated[
        str | None,
        Query(description="Filter by resource identifier."),
    ] = None,
    date_from: Annotated[
        str | None,
        Query(description="Filter audit logs occurring on or after ISO timestamp."),
    ] = None,
    date_to: Annotated[
        str | None,
        Query(description="Filter audit logs occurring on or before ISO timestamp."),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number.")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 20,
    cursor: Annotated[
        str | None,
        Query(description="Opaque search_after pagination cursor."),
    ] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=100, description="Page size with cursor pagination."),
    ] = None,
    sort_by: Annotated[
        str,
        Query(description="Sort field: occurred_at, action, resource_type, policy_decision."),
    ] = "occurred_at",
    sort_order: Annotated[
        str,
        Query(description="Sort direction: asc or desc."),
    ] = "desc",
) -> AuditSearchPageResponse:
    """Execute tenant-safe audit log search with ABAC enforcement."""
    effective_tenant_id = str(context.require_tenant())
    result = await search_service.search(
        tenant_id=effective_tenant_id,
        q=q,
        action=action,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
        cursor=cursor,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return AuditSearchPageResponse.from_page_result(result)


@router.post(
    "/reindex",
    response_model=ReindexResponse,
    status_code=status.HTTP_200_OK,
    summary="Rebuild search index from PostgreSQL (Admin only)",
    responses={
        **ERROR_RESPONSES,
        202: {"model": ReindexResponse, "description": "Accepted asynchronous reindex job"},
    },
)
async def reindex_products(
    response: Response,
    _context: Annotated[RequestContext, Depends(get_admin_context)],
    reindex_service: Annotated[ReindexingService, Depends(get_reindexing_service)],
    payload: ReindexRequest = ReindexRequest(),
) -> ReindexResponse:
    """Administrative workflow to rebuild the search index from PostgreSQL.

    Executes zero-downtime rebuild using a versioned index and an atomic alias swap.
    If async_mode is True, dispatches a Celery task and immediately returns 202 Accepted.
    """
    if _context.tenant_id is not None:
        if payload.tenant_id is None:
            from shared.application.errors import Forbidden

            raise Forbidden("tenant administrators must specify their own tenant_id")
        if str(payload.tenant_id) != str(_context.tenant_id):
            from shared.application.errors import Forbidden

            raise Forbidden("cannot perform administrative search operations on another tenant")

    tenant_str: str | None = (
        str(payload.tenant_id)
        if payload.tenant_id is not None
        else (str(_context.tenant_id) if _context.tenant_id is not None else None)
    )
    if payload.async_mode:
        from bootstrap.workers import reindex_products_task

        task = reindex_products_task.delay(tenant_str, payload.retain_old_indices)
        response.status_code = status.HTTP_202_ACCEPTED
        return ReindexResponse(
            status="ACCEPTED",
            task_id=str(task.id),
        )

    result = await reindex_service.reindex(
        tenant_id=tenant_str,
        retain_old_indices=payload.retain_old_indices,
    )
    return ReindexResponse.from_result(result)


@router.post(
    "/reconcile",
    response_model=ReconcileResponse,
    status_code=status.HTTP_200_OK,
    summary="Reconcile PostgreSQL and Elasticsearch read models (Admin only)",
    responses={
        **ERROR_RESPONSES,
        202: {
            "model": ReconcileResponse,
            "description": "Accepted asynchronous reconciliation job",
        },
    },
)
async def reconcile_products(
    response: Response,
    _context: Annotated[RequestContext, Depends(get_admin_context)],
    reconcile_service: Annotated[ReconciliationService, Depends(get_reconciliation_service)],
    payload: ReconcileRequest = ReconcileRequest(),
) -> ReconcileResponse:
    """Administrative workflow to detect and repair discrepancies.

    Compares PostgreSQL (source of truth) with Elasticsearch read models in bounded chunks.
    If async_mode is True, dispatches a Celery task and immediately returns 202 Accepted.
    """
    if _context.tenant_id is not None:
        if payload.tenant_id is None:
            from shared.application.errors import Forbidden

            raise Forbidden("tenant administrators must specify their own tenant_id")
        if str(payload.tenant_id) != str(_context.tenant_id):
            from shared.application.errors import Forbidden

            raise Forbidden("cannot perform administrative search operations on another tenant")

    tenant_str: str | None = (
        str(payload.tenant_id)
        if payload.tenant_id is not None
        else (str(_context.tenant_id) if _context.tenant_id is not None else None)
    )
    if payload.async_mode:
        from bootstrap.workers import reconcile_products_task

        task = reconcile_products_task.delay(tenant_str, payload.auto_repair)
        response.status_code = status.HTTP_202_ACCEPTED
        return ReconcileResponse(
            status="ACCEPTED",
            task_id=str(task.id),
        )

    result = await reconcile_service.reconcile(
        tenant_id=tenant_str,
        auto_repair=payload.auto_repair,
    )
    return ReconcileResponse.from_result(result)


__all__ = ["router"]
