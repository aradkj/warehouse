"""Unit tests for search indexer port, models, and in-memory adapter."""

from __future__ import annotations

import uuid

import pytest

from search.adapters.memory import InMemorySearchIndexer
from search.application.models import (
    PRODUCTS_INDEX_ALIAS,
    ProductSearchDocument,
    build_product_document_id,
)
from search.ports.indexer import SearchDocument

pytestmark = pytest.mark.unit


def test_build_product_document_id_isolation() -> None:
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()
    prod_id = uuid.uuid4()

    doc_id1 = build_product_document_id(t1, prod_id)
    doc_id2 = build_product_document_id(t2, prod_id)

    assert doc_id1 != doc_id2
    assert doc_id1 == f"{t1}:{prod_id}"
    assert doc_id2 == f"{t2}:{prod_id}"


def test_product_search_document_conversions() -> None:
    t_id = str(uuid.uuid4())
    p_id = str(uuid.uuid4())

    model = ProductSearchDocument(
        id=p_id,
        tenant_id=t_id,
        slug="cotton-tee",
        name="Cotton T-Shirt",
        description="Comfortable tee",
        status="ACTIVE",
        attributes={"color": "white", "size": "L"},
        skus=[
            {
                "sku_id": "sku-1",
                "sku_code": "CT-WHT-L",
                "unit_price_minor": 2000,
                "currency": "USD",
            }
        ],
        version=3,
    )

    search_doc = model.to_search_document()
    assert search_doc.index_alias == PRODUCTS_INDEX_ALIAS
    assert search_doc.document_id == f"{t_id}:{p_id}"
    assert search_doc.tenant_id == t_id
    assert search_doc.version == 3
    assert search_doc.body["name"] == "Cotton T-Shirt"
    assert search_doc.body["status"] == "ACTIVE"

    restored = ProductSearchDocument.from_dict(search_doc.body)
    assert restored.id == p_id
    assert restored.tenant_id == t_id
    assert restored.slug == "cotton-tee"
    assert restored.name == "Cotton T-Shirt"
    assert restored.status == "ACTIVE"
    assert restored.version == 3
    assert len(restored.skus) == 1


@pytest.mark.asyncio
async def test_in_memory_search_indexer_crud_and_versioning() -> None:
    indexer = InMemorySearchIndexer()
    assert await indexer.ping() is True

    doc_id = "tenant-1:prod-1"
    doc = SearchDocument(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=doc_id,
        tenant_id="tenant-1",
        version=1,
        body={"name": "Widget A", "status": "DRAFT"},
    )

    # 1. Index document
    await indexer.index((doc,))
    retrieved = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert retrieved is not None
    assert retrieved["name"] == "Widget A"

    # 2. Update with higher version
    doc_v2 = SearchDocument(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=doc_id,
        tenant_id="tenant-1",
        version=2,
        body={"name": "Widget A Premium", "status": "ACTIVE"},
    )
    await indexer.index((doc_v2,))
    retrieved_v2 = await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id)
    assert retrieved_v2 is not None
    assert retrieved_v2["name"] == "Widget A Premium"

    # 3. Out-of-order lower version is ignored (external_gte semantics)
    doc_v1_stale = SearchDocument(
        index_alias=PRODUCTS_INDEX_ALIAS,
        document_id=doc_id,
        tenant_id="tenant-1",
        version=1,
        body={"name": "Stale Widget", "status": "DRAFT"},
    )
    await indexer.index((doc_v1_stale,))
    retrieved_after_stale = await indexer.get_document(
        index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id
    )
    assert retrieved_after_stale is not None
    assert retrieved_after_stale["name"] == "Widget A Premium"

    # 4. Idempotent delete
    await indexer.delete(index_alias=PRODUCTS_INDEX_ALIAS, document_ids=(doc_id,))
    assert await indexer.get_document(index_alias=PRODUCTS_INDEX_ALIAS, document_id=doc_id) is None
    # Second delete does not error
    await indexer.delete(index_alias=PRODUCTS_INDEX_ALIAS, document_ids=(doc_id,))

    # 5. Close
    await indexer.close()
    assert await indexer.ping() is False
