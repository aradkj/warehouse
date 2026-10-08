"""Real Elasticsearch integration tests: connection, index mapping, versioning,
tenant isolation, and idempotent deletion.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest

from search.adapters.elasticsearch import ElasticsearchIndexAdapter
from search.application.models import (
    PRODUCTS_INDEX_MAPPINGS,
    build_product_document_id,
)
from search.ports.indexer import SearchDocument

pytestmark = [pytest.mark.integration, pytest.mark.elasticsearch]

ES_URL = os.environ.get("ARAD_ELASTICSEARCH_URL", os.environ.get("ELASTICSEARCH_URL", "http://localhost:9200"))


@pytest.fixture
async def es_adapter() -> AsyncIterator[ElasticsearchIndexAdapter]:
    adapter = ElasticsearchIndexAdapter.from_url(url=ES_URL, request_timeout=3.0, max_retries=2)
    if not await adapter.ping():
        await adapter.close()
        pytest.skip(f"Elasticsearch is unreachable at {ES_URL}")
    yield adapter
    await adapter.close()


@pytest.fixture
async def test_index(es_adapter: ElasticsearchIndexAdapter) -> AsyncIterator[str]:
    unique_suffix = uuid.uuid4().hex[:8]
    index_name = f"test-products-{unique_suffix}"
    alias_name = f"test-alias-{unique_suffix}"
    await es_adapter.ensure_index(
        index_name=index_name,
        mappings=PRODUCTS_INDEX_MAPPINGS,
        aliases=(alias_name,),
    )
    yield alias_name
    try:
        if es_adapter._client is not None:
            await es_adapter._client.indices.delete(index=index_name, ignore_unavailable=True)
    except Exception:
        pass


async def test_es_connection_ping(es_adapter: ElasticsearchIndexAdapter) -> None:
    assert await es_adapter.ping() is True


async def test_es_index_and_get_document(
    es_adapter: ElasticsearchIndexAdapter,
    test_index: str,
) -> None:
    tenant_id = str(uuid.uuid4())
    product_id = str(uuid.uuid4())
    doc_id = build_product_document_id(tenant_id, product_id)

    doc = SearchDocument(
        index_alias=test_index,
        document_id=doc_id,
        tenant_id=tenant_id,
        version=1,
        body={
            "id": product_id,
            "tenant_id": tenant_id,
            "slug": "wireless-headphones",
            "name": "Wireless Headphones",
            "status": "DRAFT",
            "skus": [],
            "version": 1,
        },
    )

    # 1. Initially does not exist
    assert await es_adapter.get_document(index_alias=test_index, document_id=doc_id) is None

    # 2. Index document
    await es_adapter.index((doc,))

    # 3. Retrieve
    retrieved = await es_adapter.get_document(index_alias=test_index, document_id=doc_id)
    assert retrieved is not None
    assert retrieved["name"] == "Wireless Headphones"
    assert retrieved["tenant_id"] == tenant_id
    assert retrieved["status"] == "DRAFT"


async def test_es_external_versioning_and_concurrency(
    es_adapter: ElasticsearchIndexAdapter,
    test_index: str,
) -> None:
    tenant_id = str(uuid.uuid4())
    product_id = str(uuid.uuid4())
    doc_id = build_product_document_id(tenant_id, product_id)

    doc_v1 = SearchDocument(
        index_alias=test_index,
        document_id=doc_id,
        tenant_id=tenant_id,
        version=1,
        body={"name": "V1", "version": 1},
    )
    doc_v2 = SearchDocument(
        index_alias=test_index,
        document_id=doc_id,
        tenant_id=tenant_id,
        version=2,
        body={"name": "V2", "version": 2},
    )

    # Index v2 first
    await es_adapter.index((doc_v2,))
    current = await es_adapter.get_document(index_alias=test_index, document_id=doc_id)
    assert current is not None
    assert current["name"] == "V2"

    # Now attempt to index stale v1 - external_gte ensures conflict is caught and ignored
    await es_adapter.index((doc_v1,))

    # Content must still be V2
    after = await es_adapter.get_document(index_alias=test_index, document_id=doc_id)
    assert after is not None
    assert after["name"] == "V2"


async def test_es_idempotent_delete(
    es_adapter: ElasticsearchIndexAdapter,
    test_index: str,
) -> None:
    tenant_id = str(uuid.uuid4())
    product_id = str(uuid.uuid4())
    doc_id = build_product_document_id(tenant_id, product_id)

    doc = SearchDocument(
        index_alias=test_index,
        document_id=doc_id,
        tenant_id=tenant_id,
        version=1,
        body={"name": "To Delete"},
    )
    await es_adapter.index((doc,))
    assert await es_adapter.get_document(index_alias=test_index, document_id=doc_id) is not None

    # First delete
    await es_adapter.delete(index_alias=test_index, document_ids=(doc_id,))
    assert await es_adapter.get_document(index_alias=test_index, document_id=doc_id) is None

    # Second delete must succeed idempotently without error
    await es_adapter.delete(index_alias=test_index, document_ids=(doc_id,))
    assert await es_adapter.get_document(index_alias=test_index, document_id=doc_id) is None


async def test_es_multi_tenant_isolation(
    es_adapter: ElasticsearchIndexAdapter,
    test_index: str,
) -> None:
    t1 = str(uuid.uuid4())
    t2 = str(uuid.uuid4())
    shared_product_id = str(uuid.uuid4())

    doc_id1 = build_product_document_id(t1, shared_product_id)
    doc_id2 = build_product_document_id(t2, shared_product_id)

    doc1 = SearchDocument(
        index_alias=test_index,
        document_id=doc_id1,
        tenant_id=t1,
        version=1,
        body={"name": "Tenant 1 Exclusive", "tenant_id": t1},
    )
    doc2 = SearchDocument(
        index_alias=test_index,
        document_id=doc_id2,
        tenant_id=t2,
        version=1,
        body={"name": "Tenant 2 Exclusive", "tenant_id": t2},
    )

    await es_adapter.index((doc1,))
    await es_adapter.index((doc2,))

    res1 = await es_adapter.get_document(index_alias=test_index, document_id=doc_id1)
    res2 = await es_adapter.get_document(index_alias=test_index, document_id=doc_id2)

    assert res1 is not None and res2 is not None
    assert res1["name"] == "Tenant 1 Exclusive"
    assert res2["name"] == "Tenant 2 Exclusive"
    assert res1["tenant_id"] == t1
    assert res2["tenant_id"] == t2
