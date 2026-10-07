"""Catalog HTTP adapter.

The router is intentionally thin: validate the transport shape, build a command,
invoke a use case, map the returned view onto a response schema. No business rule
is expressed here (an architecture test enforces that there is no other kind of
statement in these files).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response, status

from bootstrap.dependencies import (
    UseCases,
    apply_outcome,
    get_tenant_context,
    get_use_cases,
    parse_if_match,
)
from bootstrap.schemas.catalog import (
    ChangeSkuPriceRequest,
    CreateProductRequest,
    CreateSkuRequest,
    ProductPageResponse,
    ProductResponse,
    ReviseProductRequest,
    SkuResponse,
)
from bootstrap.schemas.common import ErrorResponse
from catalog.application.commands import (
    ChangeProductStateCommand,
    ChangeSkuPriceCommand,
    ChangeSkuStateCommand,
    CreateProductCommand,
    CreateSkuCommand,
    GetProductQuery,
    GetSkuQuery,
    ListProductsQuery,
    ReviseProductCommand,
)
from catalog.domain.value_objects import ProductStatus
from shared.application.context import RequestContext
from shared.domain.identifiers import ProductId, SkuId

router = APIRouter(tags=["catalog"])

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.post(
    "/products",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a product",
    responses=ERROR_RESPONSES,
)
async def create_product(
    payload: CreateProductRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
) -> ProductResponse:
    """Create a product in ``DRAFT``; activate it when it is ready to sell.

    Sending an ``Idempotency-Key`` makes retries safe: the second identical
    request replays the first result instead of creating a second product.
    """
    assert context.tenant_id is not None  # guaranteed by get_tenant_context
    outcome = await use_cases.create_product(
        CreateProductCommand(
            tenant_id=context.tenant_id,
            context=context,
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            attributes=payload.attributes,
        )
    )
    apply_outcome(response, outcome)
    return ProductResponse.from_view(outcome.view)


@router.get(
    "/products",
    response_model=ProductPageResponse,
    summary="List products",
    responses=ERROR_RESPONSES,
)
async def list_products(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(description="Cursor from the previous page.")] = None,
    product_status: Annotated[
        ProductStatus | None, Query(alias="status", description="Filter by lifecycle state.")
    ] = None,
) -> ProductPageResponse:
    """Newest-first product list with cursor pagination."""
    assert context.tenant_id is not None
    page = await use_cases.list_products(
        ListProductsQuery(
            tenant_id=context.tenant_id,
            context=context,
            limit=limit,
            cursor=cursor,
            status=product_status,
        )
    )
    return ProductPageResponse(
        items=[ProductResponse.from_view(view) for view in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/products/{product_id}",
    response_model=ProductResponse,
    summary="Get a product",
    responses=ERROR_RESPONSES,
)
async def get_product(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    product_id: Annotated[UUID, Path()],
) -> ProductResponse:
    """Fetch a product with its SKUs."""
    assert context.tenant_id is not None
    view = await use_cases.get_product(
        GetProductQuery(
            tenant_id=context.tenant_id, context=context, product_id=ProductId(product_id)
        )
    )
    return ProductResponse.from_view(view)


@router.put(
    "/products/{product_id}",
    response_model=ProductResponse,
    summary="Revise a product",
    responses=ERROR_RESPONSES,
)
async def revise_product(
    payload: ReviseProductRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    product_id: Annotated[UUID, Path()],
    expected_version: Annotated[int | None, Depends(parse_if_match)] = None,
) -> ProductResponse:
    """Change descriptive metadata.

    Send ``If-Match: <version>`` to fail with ``409 concurrency_conflict`` instead
    of overwriting a change made by somebody else.
    """
    assert context.tenant_id is not None
    outcome = await use_cases.revise_product(
        ReviseProductCommand(
            tenant_id=context.tenant_id,
            context=context,
            product_id=ProductId(product_id),
            name=payload.name,
            description=payload.description,
            attributes=payload.attributes,
            expected_version=expected_version,
        )
    )
    apply_outcome(response, outcome)
    return ProductResponse.from_view(outcome.view)


@router.post(
    "/products/{product_id}/activate",
    response_model=ProductResponse,
    summary="Activate a product",
    responses=ERROR_RESPONSES,
)
async def activate_product(
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    product_id: Annotated[UUID, Path()],
) -> ProductResponse:
    """Move a product from ``DRAFT`` to ``ACTIVE`` so it can be sold."""
    assert context.tenant_id is not None
    outcome = await use_cases.activate_product(
        ChangeProductStateCommand(
            tenant_id=context.tenant_id, context=context, product_id=ProductId(product_id)
        )
    )
    apply_outcome(response, outcome)
    return ProductResponse.from_view(outcome.view)


@router.post(
    "/products/{product_id}/archive",
    response_model=ProductResponse,
    summary="Archive a product",
    responses=ERROR_RESPONSES,
)
async def archive_product(
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    product_id: Annotated[UUID, Path()],
) -> ProductResponse:
    """Retire a product permanently (``ARCHIVED`` is terminal)."""
    assert context.tenant_id is not None
    outcome = await use_cases.archive_product(
        ChangeProductStateCommand(
            tenant_id=context.tenant_id, context=context, product_id=ProductId(product_id)
        )
    )
    apply_outcome(response, outcome)
    return ProductResponse.from_view(outcome.view)


@router.post(
    "/products/{product_id}/skus",
    response_model=SkuResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a SKU to a product",
    responses=ERROR_RESPONSES,
)
async def create_sku(
    payload: CreateSkuRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    product_id: Annotated[UUID, Path()],
) -> SkuResponse:
    """Create the sellable unit (code + list price) of a product."""
    assert context.tenant_id is not None
    outcome = await use_cases.create_sku(
        CreateSkuCommand(
            tenant_id=context.tenant_id,
            context=context,
            product_id=ProductId(product_id),
            code=payload.code,
            name=payload.name,
            unit_price_minor=payload.unit_price_minor,
            currency=payload.currency.upper(),
            attributes=payload.attributes,
        )
    )
    apply_outcome(response, outcome)
    return SkuResponse.from_view(outcome.view)


@router.get(
    "/skus/{sku_id}",
    response_model=SkuResponse,
    summary="Get a SKU",
    responses=ERROR_RESPONSES,
)
async def get_sku(
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    sku_id: Annotated[UUID, Path()],
) -> SkuResponse:
    """Fetch one SKU."""
    assert context.tenant_id is not None
    view = await use_cases.get_sku(
        GetSkuQuery(tenant_id=context.tenant_id, context=context, sku_id=SkuId(sku_id))
    )
    return SkuResponse.from_view(view)


@router.put(
    "/skus/{sku_id}/price",
    response_model=SkuResponse,
    summary="Change a SKU price",
    responses=ERROR_RESPONSES,
)
async def change_sku_price(
    payload: ChangeSkuPriceRequest,
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    sku_id: Annotated[UUID, Path()],
) -> SkuResponse:
    """Set a new list price. Existing orders keep their pricing snapshot."""
    assert context.tenant_id is not None
    outcome = await use_cases.change_sku_price(
        ChangeSkuPriceCommand(
            tenant_id=context.tenant_id,
            context=context,
            sku_id=SkuId(sku_id),
            unit_price_minor=payload.unit_price_minor,
            currency=payload.currency.upper(),
        )
    )
    apply_outcome(response, outcome)
    return SkuResponse.from_view(outcome.view)


@router.post(
    "/skus/{sku_id}/deactivate",
    response_model=SkuResponse,
    summary="Withdraw a SKU from sale",
    responses=ERROR_RESPONSES,
)
async def deactivate_sku(
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    sku_id: Annotated[UUID, Path()],
) -> SkuResponse:
    """Make the SKU unpurchasable for new orders."""
    assert context.tenant_id is not None
    outcome = await use_cases.deactivate_sku(
        ChangeSkuStateCommand(tenant_id=context.tenant_id, context=context, sku_id=SkuId(sku_id))
    )
    apply_outcome(response, outcome)
    return SkuResponse.from_view(outcome.view)


@router.post(
    "/skus/{sku_id}/activate",
    response_model=SkuResponse,
    summary="Return a SKU to sale",
    responses=ERROR_RESPONSES,
)
async def activate_sku(
    response: Response,
    context: Annotated[RequestContext, Depends(get_tenant_context)],
    use_cases: Annotated[UseCases, Depends(get_use_cases)],
    sku_id: Annotated[UUID, Path()],
) -> SkuResponse:
    """Re-activate a previously withdrawn SKU (an explicit, allowed transition)."""
    assert context.tenant_id is not None
    outcome = await use_cases.activate_sku(
        ChangeSkuStateCommand(tenant_id=context.tenant_id, context=context, sku_id=SkuId(sku_id))
    )
    apply_outcome(response, outcome)
    return SkuResponse.from_view(outcome.view)


__all__ = ["router"]
