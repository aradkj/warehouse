"""Unit tests for SearchProductsService and SearchIndexer port operations."""

from __future__ import annotations

import uuid

import pytest

from search.adapters.memory import InMemorySearchIndexer
from search.application.models import PRODUCTS_INDEX_ALIAS, build_product_document_id
from search.application.service import SearchProductsService
from search.ports.indexer import SearchDocument
from shared.application.errors import InvalidRequest

pytestmark = pytest.mark.unit


def _make_doc(
    tenant_id: str,
    product_id: str,
    name: str,
    slug: str,
    status: str = "ACTIVE",
    description: str | None = None,
    skus: list[dict[str, object]] | None = None,
    created_at: str = "2026-01-01T00:00:00Z",
    version: int = 1,
) -> SearchDocument:
    doc_id = build_product_document_id(tenant_id, product_id)
    return SearchDocument(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=doc_id,
        tenant_id=tenant_id,
        version=version,
        body={
            "id": product_id,
            "tenant_id": tenant_id,
            "slug": slug,
            "name": name,
            "description": description,
            "status": status,
            "attributes": {},
            "skus": skus or [],
            "created_at": created_at,
            "updated_at": created_at,
            "version": version,
        },
    )


@pytest.fixture
def indexer() -> InMemorySearchIndexer:
    return InMemorySearchIndexer()


@pytest.fixture
def service(indexer: InMemorySearchIndexer) -> SearchProductsService:
    return SearchProductsService(indexer=indexer, cache=None)


async def test_search_tenant_isolation(
    indexer: InMemorySearchIndexer, service: SearchProductsService
) -> None:
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    doc_a = _make_doc(tenant_a, str(uuid.uuid4()), "Widget A", "widget-a")
    doc_b = _make_doc(tenant_b, str(uuid.uuid4()), "Widget B", "widget-b")

    await indexer.index((doc_a, doc_b))

    # Tenant A searches: must only see doc_a
    result_a = await service.search(tenant_id=tenant_a)
    assert result_a.total == 1
    assert len(result_a.items) == 1
    assert result_a.items[0].name == "Widget A"
    assert result_a.items[0].tenant_id == tenant_a

    # Tenant B searches: must only see doc_b
    result_b = await service.search(tenant_id=tenant_b)
    assert result_b.total == 1
    assert len(result_b.items) == 1
    assert result_b.items[0].name == "Widget B"
    assert result_b.items[0].tenant_id == tenant_b


async def test_search_text_query(
    indexer: InMemorySearchIndexer, service: SearchProductsService
) -> None:
    tenant_id = str(uuid.uuid4())
    doc1 = _make_doc(
        tenant_id,
        str(uuid.uuid4()),
        "Ergonomic Office Chair",
        "office-chair",
        description="Very comfortable",
    )
    doc2 = _make_doc(
        tenant_id,
        str(uuid.uuid4()),
        "Standing Desk",
        "standing-desk",
        description="Adjustable height",
    )
    doc3 = _make_doc(
        tenant_id,
        str(uuid.uuid4()),
        "Wireless Mouse",
        "wireless-mouse",
        skus=[{"sku_id": "s1", "sku_code": "WM-CHAIR-MATCH", "status": "ACTIVE"}],
    )

    await indexer.index((doc1, doc2, doc3))

    # Search for "Chair" should match Chair by name and Wireless Mouse by sku_code
    res = await service.search(tenant_id=tenant_id, q="chair")
    assert res.total == 2
    matched_names = {it.name for it in res.items}
    assert matched_names == {"Ergonomic Office Chair", "Wireless Mouse"}

    # Search for "height" matches standing desk in description
    res_desc = await service.search(tenant_id=tenant_id, q="height")
    assert res_desc.total == 1
    assert res_desc.items[0].name == "Standing Desk"


async def test_search_status_and_sku_filters(
    indexer: InMemorySearchIndexer, service: SearchProductsService
) -> None:
    tenant_id = str(uuid.uuid4())
    doc_active = _make_doc(
        tenant_id,
        str(uuid.uuid4()),
        "Active Lamp",
        "active-lamp",
        status="ACTIVE",
        skus=[{"sku_id": "s1", "sku_code": "LAMP-01", "status": "ACTIVE"}],
    )
    doc_draft = _make_doc(
        tenant_id,
        str(uuid.uuid4()),
        "Draft Lamp",
        "draft-lamp",
        status="DRAFT",
        skus=[{"sku_id": "s2", "sku_code": "LAMP-02", "status": "DRAFT"}],
    )

    await indexer.index((doc_active, doc_draft))

    # Filter by status
    res_active = await service.search(tenant_id=tenant_id, status="ACTIVE")
    assert res_active.total == 1
    assert res_active.items[0].name == "Active Lamp"

    res_draft = await service.search(tenant_id=tenant_id, status="DRAFT")
    assert res_draft.total == 1
    assert res_draft.items[0].name == "Draft Lamp"

    # Filter by SKU
    res_sku = await service.search(tenant_id=tenant_id, sku="LAMP-01")
    assert res_sku.total == 1
    assert res_sku.items[0].name == "Active Lamp"

    res_sku_none = await service.search(tenant_id=tenant_id, sku="NON-EXISTENT")
    assert res_sku_none.total == 0


async def test_search_pagination(
    indexer: InMemorySearchIndexer, service: SearchProductsService
) -> None:
    tenant_id = str(uuid.uuid4())
    docs = [
        _make_doc(
            tenant_id,
            str(uuid.uuid4()),
            f"Product {i:02d}",
            f"product-{i:02d}",
            created_at=f"2026-01-01T{i:02d}:00:00Z",
        )
        for i in range(15)
    ]
    await indexer.index(tuple(docs))

    # Page 1, page_size 5
    page1 = await service.search(
        tenant_id=tenant_id, page=1, page_size=5, sort_by="created_at", sort_order="asc"
    )
    assert page1.total == 15
    assert page1.page == 1
    assert page1.page_size == 5
    assert page1.total_pages == 3
    assert len(page1.items) == 5
    assert page1.items[0].name == "Product 00"

    # Page 2, page_size 5
    page2 = await service.search(
        tenant_id=tenant_id, page=2, page_size=5, sort_by="created_at", sort_order="asc"
    )
    assert page2.page == 2
    assert len(page2.items) == 5
    assert page2.items[0].name == "Product 05"

    # Page 4 out of bounds
    page4 = await service.search(tenant_id=tenant_id, page=4, page_size=5)
    assert page4.total == 15
    assert len(page4.items) == 0


async def test_search_validation_errors(service: SearchProductsService) -> None:
    tenant_id = str(uuid.uuid4())

    # Missing tenant_id
    with pytest.raises(InvalidRequest, match="tenant_id is required"):
        await service.search(tenant_id="")

    # Invalid page
    with pytest.raises(InvalidRequest, match="page must be greater than or equal to 1"):
        await service.search(tenant_id=tenant_id, page=0)

    # Invalid page_size
    with pytest.raises(InvalidRequest, match="page_size must be greater than or equal to 1"):
        await service.search(tenant_id=tenant_id, page_size=0)

    # Exceeding max page_size
    with pytest.raises(InvalidRequest, match="page_size cannot exceed maximum"):
        await service.search(tenant_id=tenant_id, page_size=101)

    # Disallowed sort field
    with pytest.raises(InvalidRequest, match="sorting by 'unindexed_field' is not permitted"):
        await service.search(tenant_id=tenant_id, sort_by="unindexed_field")

    # Invalid sort order
    with pytest.raises(InvalidRequest, match="sort_order must be either 'asc' or 'desc'"):
        await service.search(tenant_id=tenant_id, sort_order="sideways")
